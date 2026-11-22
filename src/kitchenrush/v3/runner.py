"""Resumable, spend-reserved, seed-interleaved runner with deterministic replay verification."""

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
    static_context,
    verify_replay,
)
from .metrics import format_markdown_table, summarize_campaign

VALID_HISTORY_MODES = ("last_turn", "on_demand", "full_chat")


class SpendLedger:
    """Thread-safe append-only JSONL spend ledger with atomic worst-case reservations.

    Enforces a single campaign-level safety ceiling without silent per-episode cutoffs.
    """

    def __init__(self, path: Path, campaign_cap_usd: float):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.campaign_cap_usd = campaign_cap_usd
        self._lock = threading.Lock()
        self.total_usd = 0.0
        self.reserved_usd = 0.0
        self._reservations: dict[int, float] = {}
        self._next_res_id = 1
        self.by_model_usd: dict[str, float] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                c = float(rec.get("cost_usd", 0.0))
                m = rec.get("model", "")
                self.total_usd += c
                self.by_model_usd[m] = self.by_model_usd.get(m, 0.0) + c

    def reserve(self, amount_usd: float) -> tuple[bool, int | None, str]:
        """Atomically reserve worst-case spend for an upcoming API call."""
        amt = max(0.0, float(amount_usd))
        with self._lock:
            projected = self.total_usd + self.reserved_usd + amt
            if projected > self.campaign_cap_usd:
                return (
                    False,
                    None,
                    f"campaign cap reached (spent=${self.total_usd:.4f} + reserved=${self.reserved_usd:.4f} "
                    f"+ req=${amt:.4f} > cap=${self.campaign_cap_usd:.4f})",
                )
            res_id = self._next_res_id
            self._next_res_id += 1
            self._reservations[res_id] = amt
            self.reserved_usd += amt
            return True, res_id, "ok"

    def settle(self, res_id: int | None, entry: dict[str, Any]) -> None:
        """Release reservation and record settled actual cost atomically."""
        with self._lock:
            if res_id is not None and res_id in self._reservations:
                self.reserved_usd = max(0.0, self.reserved_usd - self._reservations.pop(res_id))
            c = float(entry.get("cost_usd", 0.0))
            m = entry.get("model", "")
            self.total_usd += c
            self.by_model_usd[m] = self.by_model_usd.get(m, 0.0) + c
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def release(
        self,
        res_id: int | None,
        *,
        retain_liability: bool = False,
        note_entry: dict[str, Any] | None = None,
    ) -> None:
        """Release a reservation after a failed call (or retain worst-case liability if billing is uncertain)."""
        with self._lock:
            held = 0.0
            if res_id is not None and res_id in self._reservations:
                held = self._reservations.pop(res_id)
                self.reserved_usd = max(0.0, self.reserved_usd - held)
            if retain_liability and held > 0:
                self.total_usd += held
                if note_entry is not None:
                    rec = {**note_entry, "cost_usd": held, "cost_source": "uncertain_liability"}
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            elif note_entry is not None:
                rec = {**note_entry, "cost_usd": 0.0, "cost_source": "unbilled_error"}
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def can_spend(self, buffer_usd: float = 0.0) -> tuple[bool, str]:
        with self._lock:
            if self.total_usd + self.reserved_usd + max(0.0, buffer_usd) > self.campaign_cap_usd:
                return (
                    False,
                    f"campaign cap reached (${self.total_usd + self.reserved_usd:.4f}/${self.campaign_cap_usd:.4f})",
                )
            return True, "ok"


def _append_full_chat_turn(
    chat_messages: list[dict[str, Any]],
    user_text: str,
    decision_calls: list[Any],
    raw_content: str | None,
    step_res: dict[str, Any],
) -> None:
    chat_messages.append({"role": "user", "content": user_text})
    if not decision_calls:
        chat_messages.append({"role": "assistant", "content": raw_content or "(no tool calls)"})
        return

    tool_calls_payload = []
    call_ids: list[str] = []
    for i, c in enumerate(decision_calls):
        cid = c.id or f"call_{len(chat_messages)}_{i}"
        call_ids.append(cid)
        tool_calls_payload.append({
            "id": cid,
            "type": "function",
            "function": {
                "name": c.name,
                "arguments": json.dumps(c.arguments or {}, separators=(",", ":")),
            },
        })
    chat_messages.append({
        "role": "assistant",
        "content": raw_content or "",
        "tool_calls": tool_calls_payload,
    })

    executed = step_res.get("calls") or []
    interrupted = step_res.get("interrupted_reason")
    for i, cid in enumerate(call_ids):
        if i < len(executed):
            ex = executed[i]
            payload = {"ok": ex["ok"], "note": ex.get("note", "")}
            if i == len(executed) - 1 and step_res.get("events"):
                payload["events"] = step_res["events"]
        else:
            payload = {"ok": False, "aborted": True, "reason": interrupted or "batch_aborted"}
        chat_messages.append({
            "role": "tool",
            "tool_call_id": cid,
            "content": json.dumps(payload, separators=(",", ":")),
        })


def run_episode(
    spec: UnifiedKitchen,
    adapter: AgentAdapter,
    ledger: SpendLedger,
    *,
    max_turns: int = MAX_DECISIONS,
    history_mode: str = "last_turn",
) -> dict[str, Any]:
    """Run one complete episode with batched tool calls, atomic spend reservation, and replay verification."""
    if history_mode not in VALID_HISTORY_MODES:
        raise ValueError(f"Unknown history_mode={history_mode!r}; expected one of {VALID_HISTORY_MODES}")

    engine = UnifiedEngine(spec, record_trace=True, early_stop=True)
    s_ctx = static_context(engine)
    all_compact_turns: list[dict[str, Any]] = []
    last_turn: dict[str, Any] | None = None
    pending_requested_history: list[dict[str, Any]] | None = None
    chat_messages: list[dict[str, Any]] = []
    batches: list[list[dict[str, Any]]] = []
    turn_logs: list[dict[str, Any]] = []

    ep_usd = 0.0
    in_tok = 0
    out_tok = 0
    reason_tok = 0
    cached_tok = 0
    empty_turns = 0
    truncated_turns = 0
    history_lookups = 0
    history_lookup_depths: list[int] = []
    consecutive_history_only = 0
    api_turns = 0
    routes_seen: set[str] = set()
    status = "complete"
    error_note: str | None = None
    t_start = time.monotonic()
    reserve_amt = adapter.max_turn_reserve_usd()

    while not engine.terminated and api_turns < max_turns and engine._action_count < MAX_DECISIONS:
        ok_cap, res_id, cap_msg = ledger.reserve(reserve_amt)
        if not ok_cap:
            status = "stopped_cap"
            error_note = cap_msg
            break

        dyn = dynamic_state(engine, None if history_mode == "full_chat" else last_turn)
        if history_mode == "on_demand" and pending_requested_history is not None:
            dyn["requested_history"] = pending_requested_history
            pending_requested_history = None

        user_text = f"STATE_JSON:\n{json.dumps(dyn, separators=(',', ':'))}"
        try:
            decision = adapter.act(
                s_ctx,
                dyn,
                chat_messages=chat_messages if history_mode == "full_chat" else None,
                allow_inspect_history=(history_mode == "on_demand"),
            )
        except ProviderError as exc:
            ledger.release(
                res_id,
                retain_liability=exc.uncertain_billing,
                note_entry={
                    "ts": round(time.time(), 2),
                    "kind": "provider_error",
                    "model": adapter.name,
                    "level": spec.difficulty,
                    "seed": spec.seed,
                    "turn": api_turns + 1,
                    "error": str(exc)[:200],
                },
            )
            status = "provider_error"
            error_note = str(exc)
            break
        except Exception:
            ledger.release(res_id)
            raise

        api_turns += 1
        ep_usd += decision.cost_usd
        in_tok += decision.prompt_tokens
        out_tok += decision.completion_tokens
        reason_tok += decision.reasoning_tokens
        cached_tok += decision.cached_tokens
        if not decision.calls:
            empty_turns += 1
        if decision.finish_reason == "length":
            truncated_turns += 1
        if decision.provider_route:
            routes_seen.add(decision.provider_route)

        ledger.settle(
            res_id,
            {
                "ts": round(time.time(), 2),
                "kind": "turn",
                "model": adapter.name,
                "history_mode": history_mode,
                "level": spec.difficulty,
                "seed": spec.seed,
                "turn": api_turns,
                "prompt_tokens": decision.prompt_tokens,
                "completion_tokens": decision.completion_tokens,
                "reasoning_tokens": decision.reasoning_tokens,
                "cached_tokens": decision.cached_tokens,
                "cost_usd": decision.cost_usd,
                "cost_source": decision.cost_source,
                "finish_reason": decision.finish_reason,
                "provider_route": decision.provider_route,
            },
        )

        # Handle optional zero-game-time `inspect_history` tool cleanly outside UnifiedEngine.
        kitchen_calls = []
        requested_depth: int | None = None
        for c in decision.calls:
            if history_mode == "on_demand" and c.name == "inspect_history":
                try:
                    depth = max(1, min(50, int((c.arguments or {}).get("turns", 5))))
                except (TypeError, ValueError):
                    depth = 5
                requested_depth = depth
                history_lookups += 1
                history_lookup_depths.append(depth)
            else:
                kitchen_calls.append(c)

        if requested_depth is not None:
            pending_requested_history = all_compact_turns[-requested_depth:] if all_compact_turns else []

        if requested_depth is not None and not kitchen_calls:
            consecutive_history_only += 1
            compact_turn = {
                "turn": api_turns,
                "clock_gs": round(engine.clock_gs, 2),
                "calls": [{"call": "inspect_history", "args": {"turns": requested_depth}, "ok": True, "note": f"returned {len(pending_requested_history or [])} prior turns"}],
                "aborted": [],
                "interrupted": None,
                "events": [],
            }
            last_turn = compact_turn
            turn_logs.append({
                **compact_turn,
                "prompt_tokens": decision.prompt_tokens,
                "completion_tokens": decision.completion_tokens,
                "reasoning_tokens": decision.reasoning_tokens,
                "cached_tokens": decision.cached_tokens,
                "cost_usd": decision.cost_usd,
                "cost_source": decision.cost_source,
                "latency_s": decision.latency_s,
                "finish_reason": decision.finish_reason,
                "provider_route": decision.provider_route,
                "requested_effort": decision.requested_effort,
                "max_tokens": decision.max_tokens,
                "parse_errors": decision.parse_errors,
            })
            if consecutive_history_only >= 4:
                engine.terminated = True
                engine.termination_reason = "stalled"
                break
            continue

        consecutive_history_only = 0
        batch_dicts = [{"name": c.name, "arguments": dict(c.arguments)} for c in kitchen_calls]
        batches.append(batch_dicts)
        step_res = engine.step_batch(kitchen_calls)

        if history_mode == "full_chat":
            _append_full_chat_turn(chat_messages, user_text, kitchen_calls, decision.raw_content, step_res)

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
        all_compact_turns.append(compact_turn)
        last_turn = compact_turn
        turn_logs.append({
            **compact_turn,
            "prompt_tokens": decision.prompt_tokens,
            "completion_tokens": decision.completion_tokens,
            "reasoning_tokens": decision.reasoning_tokens,
            "cached_tokens": decision.cached_tokens,
            "cost_usd": decision.cost_usd,
            "cost_source": decision.cost_source,
            "latency_s": decision.latency_s,
            "finish_reason": decision.finish_reason,
            "provider_route": decision.provider_route,
            "requested_effort": decision.requested_effort,
            "max_tokens": decision.max_tokens,
            "parse_errors": decision.parse_errors,
        })

    if status == "complete" and not engine.terminated and api_turns >= max_turns:
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

    cap_hits: list[str] = []
    if truncated_turns > 0:
        cap_hits.append(f"output_length_cap({truncated_turns})")
    if empty_turns > 0:
        cap_hits.append(f"empty_turns({empty_turns})")
    if engine.termination_reason in ("max_decisions", "max_turns", "stalled"):
        cap_hits.append(str(engine.termination_reason))
    if status != "complete":
        cap_hits.append(status)

    meta = adapter.metadata
    return {
        "model": adapter.name,
        "model_id": meta.get("model_id"),
        "requested_effort": meta.get("requested_effort"),
        "max_tokens": meta.get("max_tokens"),
        "history_mode": history_mode,
        "provider_routes": sorted(routes_seen),
        "level": spec.difficulty,
        "seed": spec.seed,
        "status": status,
        "scored": scored,
        "replay_verified": replay_ok,
        "error": error_note,
        "termination_reason": engine.termination_reason,
        "cap_hits": cap_hits,
        "served": engine.counters["orders_served"],
        "requested": len(spec.orders),
        "clear": engine.counters["orders_served"] == len(spec.orders),
        "invalid": engine.counters["invalid_actions"],
        "burns": engine.counters["burns"],
        "expired": engine.counters["orders_expired"],
        "empty_turns": empty_turns,
        "truncated_turns": truncated_turns,
        "history_lookups": history_lookups,
        "history_lookup_depths": history_lookup_depths,
        "actions": engine._action_count,
        "turns": api_turns,
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
    campaign_cap_usd: float = 12.0,
    history_mode: str = "last_turn",
    workers: int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run seed-wave interleaved evaluation across levels and adapters with resumable disk state."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ep_dir = out_dir / "episodes"
    ep_dir.mkdir(exist_ok=True)
    ledger = SpendLedger(out_dir / "spend.jsonl", campaign_cap_usd)
    print_lock = threading.Lock()

    n_seeds = min(len(bank[lvl]) for lvl in levels)
    if max_seeds is not None:
        n_seeds = min(n_seeds, max_seeds)

    suffix = "" if history_mode == "last_turn" else f"__{history_mode}"
    jobs: list[tuple[int, UnifiedKitchen, AgentAdapter, Path]] = []
    for seed_idx in range(n_seeds):
        for level in levels:
            spec = bank[level][seed_idx]
            for adapter in adapters:
                ep_path = ep_dir / f"D{level}_seed{spec.seed}_{adapter.name}{suffix}.json"
                jobs.append((level, spec, adapter, ep_path))

    def execute_one(job: tuple[int, UnifiedKitchen, AgentAdapter, Path]) -> dict[str, Any] | None:
        level, spec, adapter, ep_path = job
        if ep_path.exists():
            return json.loads(ep_path.read_text(encoding="utf-8"))
        ok_cap, cap_msg = ledger.can_spend(adapter.max_turn_reserve_usd())
        if not ok_cap:
            if verbose:
                with print_lock:
                    print(f"[CAP_HIT SKIP] D={level} seed={spec.seed} {adapter.name}: {cap_msg}", flush=True)
            return None
        res = run_episode(spec, adapter, ledger, history_mode=history_mode)
        if suffix:
            res["model"] = f"{adapter.name}{suffix}"
        ep_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
        if verbose:
            cap_banner = f" [CAP_HIT: {', '.join(res['cap_hits'])}]" if res["cap_hits"] else ""
            hl_str = f" hlook={res['history_lookups']}" if history_mode == "on_demand" else ""
            with print_lock:
                print(
                    f"D={level} seed={spec.seed} {res['model']:28s} | "
                    f"status={res['status']:14s} served={res['served']}/{res['requested']} "
                    f"inv={res['invalid']} burn={res['burns']} emp={res['empty_turns']} trunc={res['truncated_turns']}{hl_str} "
                    f"acts={res['actions']:2d} turns={res['turns']:2d} "
                    f"out={res['output_tokens']:5d} cost=${res['cost_usd']:.4f} "
                    f"(cum=${ledger.total_usd:.4f}) reason={res['termination_reason']}{cap_banner}",
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
