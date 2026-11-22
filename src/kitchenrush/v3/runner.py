"""Resumable, spend-capped, seed-interleaved runner with deterministic replay verification."""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path
from typing import Any

from .adapters import AgentAdapter, ProviderError
from .env import (
    MAX_DECISIONS,
    UnifiedEngine,
    UnifiedKitchen,
    deserialize_spec,
    digest,
    dynamic_state,
    serialize_spec,
    static_context,
    verify_replay,
)
from .metrics import format_markdown_table, summarize_campaign


class SpendLedger:
    """Thread-safe durable append-only JSONL spend ledger enforcing per-model and campaign caps."""

    def __init__(self, path: Path, campaign_cap_usd: float, model_caps_usd: dict[str, float] | None = None):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.campaign_cap_usd = campaign_cap_usd
        self.model_caps_usd = dict(model_caps_usd or {})
        self._lock = threading.Lock()
        self.total_usd = 0.0
        self.by_model_usd: dict[str, float] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                c = float(rec.get("cost_usd", 0.0))
                m = rec.get("model", "")
                self.total_usd += c
                self.by_model_usd[m] = self.by_model_usd.get(m, 0.0) + c

    def can_spend(self, model: str, buffer_usd: float = 0.0) -> tuple[bool, str]:
        with self._lock:
            if self.total_usd + buffer_usd >= self.campaign_cap_usd:
                return False, f"campaign cap reached (${self.total_usd:.4f}/${self.campaign_cap_usd:.4f})"
            m_cap = self.model_caps_usd.get(model)
            if m_cap is not None and self.by_model_usd.get(model, 0.0) + buffer_usd >= m_cap:
                return False, f"model cap reached for {model} (${self.by_model_usd.get(model, 0.0):.4f}/${m_cap:.4f})"
            return True, "ok"

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            c = float(entry.get("cost_usd", 0.0))
            m = entry.get("model", "")
            self.total_usd += c
            self.by_model_usd[m] = self.by_model_usd.get(m, 0.0) + c
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def run_episode(
    spec: UnifiedKitchen,
    adapter: AgentAdapter,
    ledger: SpendLedger,
    *,
    episode_cap_usd: float = 0.50,
    max_turns: int = 80,
) -> dict[str, Any]:
    """Run one complete episode with batched tool calls, spend guards, and replay verification."""
    engine = UnifiedEngine(spec, record_trace=True, early_stop=True)
    s_ctx = static_context(engine)
    recent_turns: list[dict[str, Any]] = []
    batches: list[list[dict[str, Any]]] = []
    turn_logs: list[dict[str, Any]] = []

    ep_usd = 0.0
    in_tok = 0
    out_tok = 0
    reason_tok = 0
    cached_tok = 0
    status = "complete"
    error_note: str | None = None
    t_start = time.monotonic()

    while not engine.terminated and engine.turn_count < max_turns and engine._action_count < MAX_DECISIONS:
        ok_cap, cap_msg = ledger.can_spend(adapter.name)
        if not ok_cap:
            status = "stopped_cap"
            error_note = cap_msg
            break
        if ep_usd >= episode_cap_usd:
            status = "stopped_episode_cap"
            error_note = f"episode spend ${ep_usd:.4f} >= ${episode_cap_usd:.4f}"
            break

        dyn = dynamic_state(engine, recent_turns)
        try:
            decision = adapter.act(s_ctx, dyn)
        except ProviderError as exc:
            status = "provider_error"
            error_note = str(exc)
            break

        ep_usd += decision.cost_usd
        in_tok += decision.prompt_tokens
        out_tok += decision.completion_tokens
        reason_tok += decision.reasoning_tokens
        cached_tok += decision.cached_tokens

        ledger.record({
            "ts": round(time.time(), 2),
            "model": adapter.name,
            "level": spec.difficulty,
            "seed": spec.seed,
            "turn": engine.turn_count + 1,
            "prompt_tokens": decision.prompt_tokens,
            "completion_tokens": decision.completion_tokens,
            "reasoning_tokens": decision.reasoning_tokens,
            "cached_tokens": decision.cached_tokens,
            "cost_usd": decision.cost_usd,
            "cost_source": decision.cost_source,
        })

        batch_dicts = [{"name": c.name, "arguments": dict(c.arguments)} for c in decision.calls]
        batches.append(batch_dicts)
        step_res = engine.step_batch(decision.calls)

        compact_turn = {
            "turn": step_res["turn"],
            "clock_gs": step_res["clock_gs"],
            "calls": [
                {"call": c["call"], "args": c["arguments"], "ok": c["ok"], "note": c["note"]}
                for c in step_res["calls"]
            ],
            "aborted": step_res["aborted_calls"],
            "interrupted": step_res["interrupted_reason"],
            "events": step_res["events"],
        }
        recent_turns.append(compact_turn)
        turn_logs.append({
            **compact_turn,
            "prompt_tokens": decision.prompt_tokens,
            "completion_tokens": decision.completion_tokens,
            "reasoning_tokens": decision.reasoning_tokens,
            "cached_tokens": decision.cached_tokens,
            "cost_usd": decision.cost_usd,
            "latency_s": decision.latency_s,
            "parse_errors": decision.parse_errors,
        })

    if status == "complete" and not engine.terminated and engine.turn_count >= max_turns:
        engine.termination_reason = "max_turns"

    trace_hash = digest(engine.trace)
    scored = status == "complete"
    replay_ok = False
    if scored:
        verify_replay(
            spec,
            batches,
            expected_served=engine.counters["orders_served"],
            expected_invalid=engine.counters["invalid_actions"],
            expected_burns=engine.counters["burns"],
            expected_trace_hash=trace_hash,
        )
        replay_ok = True

    return {
        "model": adapter.name,
        "level": spec.difficulty,
        "seed": spec.seed,
        "status": status,
        "scored": scored,
        "replay_verified": replay_ok,
        "error": error_note,
        "termination_reason": engine.termination_reason,
        "served": engine.counters["orders_served"],
        "requested": len(spec.orders),
        "clear": engine.counters["orders_served"] == len(spec.orders),
        "invalid": engine.counters["invalid_actions"],
        "burns": engine.counters["burns"],
        "expired": engine.counters["orders_expired"],
        "actions": engine._action_count,
        "turns": engine.turn_count,
        "chained_turns": engine.counters["chained_turns"],
        "chain_partial_failures": engine.counters["chain_partial_failures"],
        "clock_gs": round(engine.clock_gs, 2),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "reasoning_tokens": reason_tok,
        "cached_tokens": cached_tok,
        "cost_usd": round(ep_usd, 6),
        "wall_s": round(time.monotonic() - t_start, 2),
        "trace_hash": trace_hash,
        "batches": batches,
        "turn_logs": turn_logs,
    }


def save_bank(path: Path, bank: dict[int, list[dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {str(k): v for k, v in sorted(bank.items())}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_bank(path: Path) -> dict[int, list[UnifiedKitchen]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, list[UnifiedKitchen]] = {}
    for k, items in raw.items():
        out[int(k)] = [deserialize_spec(item["spec"]) for item in items]
    return out


def run_campaign(
    out_dir: Path,
    bank: dict[int, list[UnifiedKitchen]],
    adapters: list[AgentAdapter],
    *,
    levels: tuple[int, ...] = (1, 2, 3, 4, 5),
    max_seeds: int | None = None,
    campaign_cap_usd: float = 5.0,
    model_caps_usd: dict[str, float] | None = None,
    episode_caps_usd: dict[str, float] | None = None,
    default_episode_cap_usd: float = 0.50,
    workers: int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run seed-wave interleaved evaluation across levels and adapters with resumable disk state."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ep_dir = out_dir / "episodes"
    ep_dir.mkdir(exist_ok=True)
    ledger = SpendLedger(out_dir / "spend.jsonl", campaign_cap_usd, model_caps_usd)
    print_lock = threading.Lock()

    n_seeds = min(len(bank[lvl]) for lvl in levels)
    if max_seeds is not None:
        n_seeds = min(n_seeds, max_seeds)

    jobs: list[tuple[int, UnifiedKitchen, AgentAdapter, Path]] = []
    for seed_idx in range(n_seeds):
        for level in levels:
            spec = bank[level][seed_idx]
            for adapter in adapters:
                ep_path = ep_dir / f"D{level}_seed{spec.seed}_{adapter.name}.json"
                jobs.append((level, spec, adapter, ep_path))

    def execute_one(job: tuple[int, UnifiedKitchen, AgentAdapter, Path]) -> dict[str, Any] | None:
        level, spec, adapter, ep_path = job
        if ep_path.exists():
            return json.loads(ep_path.read_text(encoding="utf-8"))
        ok_cap, cap_msg = ledger.can_spend(adapter.name)
        if not ok_cap:
            if verbose:
                with print_lock:
                    print(f"[SKIP] D={level} seed={spec.seed} {adapter.name}: {cap_msg}", flush=True)
            return None
        ep_cap = (episode_caps_usd or {}).get(adapter.name, default_episode_cap_usd)
        res = run_episode(spec, adapter, ledger, episode_cap_usd=ep_cap)
        ep_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
        if verbose:
            with print_lock:
                print(
                    f"D={level} seed={spec.seed} {adapter.name:24s} | "
                    f"status={res['status']:14s} served={res['served']}/{res['requested']} "
                    f"inv={res['invalid']} burn={res['burns']} "
                    f"acts={res['actions']:2d} turns={res['turns']:2d} "
                    f"out={res['output_tokens']:5d} cost=${res['cost_usd']:.4f} "
                    f"(cum=${ledger.total_usd:.4f}) reason={res['termination_reason']}",
                    flush=True,
                )
        return res

    if workers <= 1:
        for job in jobs:
            execute_one(job)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(execute_one, job) for job in jobs]
            for f in concurrent.futures.as_completed(futs):
                f.result()

    all_episodes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ep_dir.glob("*.json"))]
    summary = summarize_campaign(all_episodes)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md = format_markdown_table(summary)
    (out_dir / "REPORT.md").write_text(md + "\n", encoding="utf-8")
    return summary
