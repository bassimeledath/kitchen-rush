"""Resumable, manifest-validated, crash-safe spend-reserved runner with deterministic replay verification."""

from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .adapters import AgentAdapter, CapExceededError, OpenRouterToolAdapter, ProviderError
from .env import (
    PROTOCOL,
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

VALID_HISTORY_MODES = ("full_chat", "last_turn")


class SpendLedger:
    """Thread-safe crash-safe append-only JSONL spend ledger with persistent per-attempt reservations.

    Every HTTP attempt writes a durable `reserve` record before submission and a matching `turn` or
    `release` record afterward. Any `reserve` left unsettled across a process crash remains counted
    as conservative liability on reload.
    """

    def __init__(self, path: Path, campaign_cap_usd: float):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.campaign_cap_usd = campaign_cap_usd
        self._lock = threading.Lock()
        self.total_usd = 0.0
        self.reserved_usd = 0.0
        self._reservations: dict[str, float] = {}
        self.by_model_usd: dict[str, float] = {}
        self.unreconciled_crash_usd = 0.0

        if self.path.exists():
            pending_from_disk: dict[str, float] = {}
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                kind = rec.get("kind", "turn")
                rid = rec.get("res_id")
                if kind == "reserve" and rid:
                    pending_from_disk[str(rid)] = float(rec.get("amount_usd", 0.0))
                else:
                    if rid:
                        pending_from_disk.pop(str(rid), None)
                    c = float(rec.get("cost_usd", 0.0))
                    m = rec.get("model", "")
                    self.total_usd += c
                    if m:
                        self.by_model_usd[m] = self.by_model_usd.get(m, 0.0) + c
            # Any reservation that never settled or released before a prior crash counts conservatively.
            if pending_from_disk:
                self.unreconciled_crash_usd = sum(pending_from_disk.values())
                self.total_usd += self.unreconciled_crash_usd

    def _append(self, rec: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def reserve(self, amount_usd: float, meta: dict[str, Any] | None = None) -> tuple[bool, str | None, str]:
        """Atomically reserve worst-case spend and persist the reservation before an HTTP attempt."""
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
            res_id = uuid.uuid4().hex[:16]
            self._reservations[res_id] = amt
            self.reserved_usd += amt
            if amt > 0:
                self._append({
                    "ts": round(time.time(), 2),
                    "kind": "reserve",
                    "res_id": res_id,
                    "amount_usd": round(amt, 6),
                    **(meta or {}),
                })
            return True, res_id, "ok"

    def settle(self, res_id: str | None, entry: dict[str, Any]) -> None:
        """Release reservation and record settled actual cost atomically."""
        with self._lock:
            if res_id is not None and res_id in self._reservations:
                self.reserved_usd = max(0.0, self.reserved_usd - self._reservations.pop(res_id))
            c = float(entry.get("cost_usd", 0.0))
            m = entry.get("model", "")
            self.total_usd += c
            if m:
                self.by_model_usd[m] = self.by_model_usd.get(m, 0.0) + c
            rec = {**entry, "kind": entry.get("kind", "turn")}
            if res_id is not None:
                rec["res_id"] = res_id
            self._append(rec)

    def release(
        self,
        res_id: str | None,
        *,
        retain_liability: bool = False,
        note_entry: dict[str, Any] | None = None,
    ) -> None:
        """Release a reservation after a failed attempt (retaining worst-case liability if billing is uncertain)."""
        with self._lock:
            held = 0.0
            if res_id is not None and res_id in self._reservations:
                held = self._reservations.pop(res_id)
                self.reserved_usd = max(0.0, self.reserved_usd - held)
            cost_recorded = held if retain_liability else 0.0
            if cost_recorded > 0:
                self.total_usd += cost_recorded
                m = (note_entry or {}).get("model", "")
                if m:
                    self.by_model_usd[m] = self.by_model_usd.get(m, 0.0) + cost_recorded
            if res_id is not None or note_entry is not None:
                rec = {
                    "ts": round(time.time(), 2),
                    **(note_entry or {}),
                    "kind": "release",
                    "res_id": res_id,
                    "cost_usd": round(cost_recorded, 6),
                    "cost_source": "uncertain_liability" if retain_liability and held > 0 else "unbilled_error",
                }
                self._append(rec)

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
    *,
    reasoning_details: list[dict[str, Any]] | None = None,
    raw_tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    chat_messages.append({"role": "user", "content": user_text})
    if not decision_calls:
        asst_msg: dict[str, Any] = {"role": "assistant", "content": raw_content or "(no tool calls)"}
        if reasoning_details:
            asst_msg["reasoning_details"] = reasoning_details
        chat_messages.append(asst_msg)
        return

    tool_calls_payload: list[dict[str, Any]] = []
    call_ids: list[str] = []
    use_raw_tc = raw_tool_calls is not None and len(raw_tool_calls) == len(decision_calls)
    for i, c in enumerate(decision_calls):
        raw_tc = raw_tool_calls[i] if use_raw_tc else None
        cid = (raw_tc.get("id") if isinstance(raw_tc, dict) else None) or c.id or f"call_{len(chat_messages)}_{i}"
        call_ids.append(cid)
        if use_raw_tc and isinstance(raw_tc, dict):
            tc_copy = dict(raw_tc)
            tc_copy["id"] = cid
            tc_copy.setdefault("type", "function")
            tool_calls_payload.append(tc_copy)
        else:
            tool_calls_payload.append({
                "id": cid,
                "type": "function",
                "function": {
                    "name": c.name,
                    "arguments": json.dumps(c.arguments or {}, separators=(",", ":")),
                },
            })

    asst_msg = {
        "role": "assistant",
        "content": raw_content if raw_content is not None else "",
        "tool_calls": tool_calls_payload,
    }
    if reasoning_details:
        call_id_set = set(call_ids)
        filtered_rd = [
            item
            for item in reasoning_details
            if not isinstance(item, dict) or item.get("id") is None or item.get("id") in call_id_set
        ]
        if filtered_rd:
            asst_msg["reasoning_details"] = filtered_rd
    chat_messages.append(asst_msg)

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
    max_turns: int | None = None,
    history_mode: str = "full_chat",
) -> dict[str, Any]:
    """Run one complete episode with batched tool calls, per-attempt spend reservation, and replay verification."""
    if history_mode not in VALID_HISTORY_MODES:
        raise ValueError(f"Unknown history_mode={history_mode!r}; expected one of {VALID_HISTORY_MODES}")

    engine = UnifiedEngine(spec, record_trace=True, early_stop=True)
    effective_max_turns = max_turns if max_turns is not None else engine.max_actions
    s_ctx = static_context(engine)
    spec_sha256 = digest(serialize_spec(spec))
    last_turn: dict[str, Any] | None = None
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
    provider_cost_turns = 0
    estimated_cost_turns = 0
    api_turns = 0
    routes_seen: set[str] = set()
    status = "complete"
    error_note: str | None = None
    t_start = time.monotonic()
    uses_http_hooks = isinstance(adapter, OpenRouterToolAdapter)

    while not engine.terminated and api_turns < effective_max_turns and engine._action_count < engine.max_actions:
        dyn = dynamic_state(engine, None if history_mode == "full_chat" else last_turn)
        user_text = f"STATE_JSON:\n{json.dumps(dyn, separators=(',', ':'))}"
        next_turn_num = api_turns + 1

        res_id: str | None = None
        if uses_http_hooks:
            def _reserve_attempt(amt: float, attempt_num: int) -> tuple[bool, str | None, str]:
                return ledger.reserve(
                    amt,
                    meta={
                        "model": adapter.name,
                        "level": spec.difficulty,
                        "seed": spec.seed,
                        "turn": next_turn_num,
                        "attempt": attempt_num,
                    },
                )

            def _release_attempt(rid: str | None, uncertain: bool, err_msg: str) -> None:
                ledger.release(
                    rid,
                    retain_liability=uncertain,
                    note_entry={
                        "model": adapter.name,
                        "level": spec.difficulty,
                        "seed": spec.seed,
                        "turn": next_turn_num,
                        "error": err_msg[:200],
                    },
                )

            try:
                decision = adapter.act(
                    s_ctx,
                    dyn,
                    chat_messages=chat_messages if history_mode == "full_chat" else None,
                    reserve_attempt=_reserve_attempt,
                    release_attempt=_release_attempt,
                )
                res_id = decision.reservation_id
            except CapExceededError as exc:
                status = "stopped_cap"
                error_note = str(exc)
                break
            except ProviderError as exc:
                status = "provider_error"
                error_note = str(exc)
                break
        else:
            reserve_amt = adapter.estimate_turn_reserve_usd(
                s_ctx, dyn, chat_messages=chat_messages if history_mode == "full_chat" else None
            )
            ok_cap, res_id, cap_msg = ledger.reserve(
                reserve_amt,
                meta={"model": adapter.name, "level": spec.difficulty, "seed": spec.seed, "turn": next_turn_num},
            )
            if not ok_cap:
                status = "stopped_cap"
                error_note = cap_msg
                break
            try:
                decision = adapter.act(
                    s_ctx,
                    dyn,
                    chat_messages=chat_messages if history_mode == "full_chat" else None,
                )
            except ProviderError as exc:
                ledger.release(
                    res_id,
                    retain_liability=exc.uncertain_billing,
                    note_entry={
                        "model": adapter.name,
                        "level": spec.difficulty,
                        "seed": spec.seed,
                        "turn": next_turn_num,
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
        if decision.cost_source == "provider":
            provider_cost_turns += 1
        else:
            estimated_cost_turns += 1
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

        batch_dicts = [{"name": c.name, "arguments": dict(c.arguments)} for c in decision.calls]
        batches.append(batch_dicts)
        step_res = engine.step_batch(decision.calls)

        if history_mode == "full_chat":
            _append_full_chat_turn(
                chat_messages,
                user_text,
                decision.calls,
                decision.raw_content,
                step_res,
                reasoning_details=decision.reasoning_details if decision.finish_reason != "length" else None,
                raw_tool_calls=decision.raw_tool_calls,
            )

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

    if status == "complete" and not engine.terminated and api_turns >= effective_max_turns:
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
        "protocol": PROTOCOL,
        "spec_sha256": spec_sha256,
        "tool_choice": getattr(adapter, "tool_choice", "required") if uses_http_hooks else "adapter",
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
        "provider_cost_turns": provider_cost_turns,
        "estimated_cost_turns": estimated_cost_turns,
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


def _episode_matches_plan(ep: dict[str, Any], plan_entry: dict[str, Any], model_meta: dict[str, Any]) -> bool:
    """Strictly validate that a cached episode matches the planned spec, protocol, history, and model config."""
    return (
        ep.get("spec_sha256") == plan_entry["spec_sha256"]
        and ep.get("protocol") == plan_entry["protocol"]
        and ep.get("history_mode") == plan_entry["history_mode"]
        and ep.get("tool_choice") == plan_entry["tool_choice"]
        and ep.get("model_id") == model_meta.get("model_id")
        and ep.get("requested_effort") == model_meta.get("requested_effort")
        and ep.get("max_tokens") == model_meta.get("max_tokens")
    )


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically via same-directory temp file + os.replace so crashes never corrupt JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _load_or_create_manifest(
    manifest_path: Path,
    *,
    history_mode: str,
    planned_entries: list[dict[str, Any]],
    models_meta: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Create or strictly validate the immutable campaign manifest before running or resuming."""
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("protocol") != PROTOCOL or existing.get("history_mode") != history_mode:
            raise ValueError(
                f"Manifest mismatch in {manifest_path}: existing protocol/history="
                f"({existing.get('protocol')}, {existing.get('history_mode')}) vs requested ({PROTOCOL}, {history_mode}). "
                "Use a new --out-dir for a different protocol/history configuration."
            )
        existing_by_file = {e["filename"]: e for e in existing.get("planned_episodes", [])}
        existing_models = dict(existing.get("models", {}))
        for mkey, mmeta in models_meta.items():
            if mkey in existing_models and existing_models[mkey] != mmeta:
                raise ValueError(
                    f"Manifest model config mismatch for {mkey!r} in {manifest_path}: "
                    f"existing={existing_models[mkey]} vs requested={mmeta}. Use a new --out-dir."
                )
            existing_models[mkey] = mmeta
        for entry in planned_entries:
            fn = entry["filename"]
            if fn in existing_by_file and existing_by_file[fn] != entry:
                raise ValueError(
                    f"Manifest episode spec mismatch for {fn} in {manifest_path}: "
                    "bank spec or tool_choice changed. Use a new --out-dir."
                )
            existing_by_file[fn] = entry
        merged = {
            "protocol": PROTOCOL,
            "history_mode": history_mode,
            "models": existing_models,
            "planned_episodes": list(existing_by_file.values()),
        }
        _atomic_write_text(manifest_path, json.dumps(merged, indent=2))
        return merged

    manifest = {
        "protocol": PROTOCOL,
        "history_mode": history_mode,
        "models": models_meta,
        "planned_episodes": planned_entries,
    }
    _atomic_write_text(manifest_path, json.dumps(manifest, indent=2))
    return manifest


def run_campaign(
    out_dir: Path,
    bank: dict[int, list[UnifiedKitchen]],
    adapters: list[AgentAdapter],
    *,
    levels: tuple[int, ...] = (1, 2, 3, 4, 5),
    max_seeds: int | None = None,
    campaign_cap_usd: float = 12.0,
    history_mode: str = "full_chat",
    workers: int = 1,
    retry_incomplete: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run manifest-validated, seed-interleaved evaluation across levels and adapters."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ep_dir = out_dir / "episodes"
    ep_dir.mkdir(exist_ok=True)
    incomplete_dir = out_dir / "episodes_incomplete"
    ledger = SpendLedger(out_dir / "spend.jsonl", campaign_cap_usd)
    print_lock = threading.Lock()

    n_seeds = min(len(bank[lvl]) for lvl in levels)
    if max_seeds is not None:
        n_seeds = min(n_seeds, max_seeds)

    suffix = "" if history_mode == "full_chat" else f"__{history_mode}"
    models_meta = {f"{a.name}{suffix}": dict(a.metadata) for a in adapters}
    planned_entries: list[dict[str, Any]] = []
    jobs: list[tuple[int, UnifiedKitchen, AgentAdapter, Path, dict[str, Any]]] = []
    for seed_idx in range(n_seeds):
        for level in levels:
            spec = bank[level][seed_idx]
            spec_hash = digest(serialize_spec(spec))
            for adapter in adapters:
                model_label = f"{adapter.name}{suffix}"
                filename = f"D{level}_seed{spec.seed}_{model_label}.json"
                plan_entry = {
                    "filename": filename,
                    "level": level,
                    "seed": spec.seed,
                    "model": model_label,
                    "spec_sha256": spec_hash,
                    "protocol": PROTOCOL,
                    "history_mode": history_mode,
                    "tool_choice": adapter.tool_choice if isinstance(adapter, OpenRouterToolAdapter) else "adapter",
                }
                planned_entries.append(plan_entry)
                jobs.append((level, spec, adapter, ep_dir / filename, plan_entry))

    manifest = _load_or_create_manifest(
        out_dir / "manifest.json",
        history_mode=history_mode,
        planned_entries=planned_entries,
        models_meta=models_meta,
    )

    def execute_one(job: tuple[int, UnifiedKitchen, AgentAdapter, Path, dict[str, Any]]) -> dict[str, Any] | None:
        level, spec, adapter, ep_path, plan_entry = job
        model_label = plan_entry["model"]
        if ep_path.exists():
            cached = json.loads(ep_path.read_text(encoding="utf-8"))
            if not _episode_matches_plan(cached, plan_entry, models_meta[model_label]):
                raise ValueError(
                    f"Refusing to reuse stale/mismatched episode file {ep_path}: "
                    f"cached (spec={cached.get('spec_sha256')}, history={cached.get('history_mode')}, "
                    f"tool_choice={cached.get('tool_choice')}) != planned "
                    f"(spec={plan_entry['spec_sha256']}, history={plan_entry['history_mode']}, "
                    f"tool_choice={plan_entry['tool_choice']}). Use a fresh --out-dir."
                )
            if cached.get("status") == "complete" and cached.get("scored") is True:
                return cached
            if not retry_incomplete:
                return cached
            ok_cap_retry, cap_msg_retry = ledger.can_spend(adapter.max_turn_reserve_usd())
            if not ok_cap_retry:
                if verbose:
                    with print_lock:
                        print(f"[CAP_HIT SKIP RETRY] D={level} seed={spec.seed} {model_label}: {cap_msg_retry}", flush=True)
                return cached
            incomplete_dir.mkdir(exist_ok=True)
            archived = incomplete_dir / f"{ep_path.stem}.{int(time.time())}_{uuid.uuid4().hex[:6]}.json"
            os.replace(ep_path, archived)
            if verbose:
                with print_lock:
                    print(
                        f"[RETRY INCOMPLETE] D={level} seed={spec.seed} {model_label}: "
                        f"prior status={cached.get('status')} archived to {archived.name}",
                        flush=True,
                    )

        ok_cap, cap_msg = ledger.can_spend(adapter.max_turn_reserve_usd())
        if not ok_cap:
            if verbose:
                with print_lock:
                    print(f"[CAP_HIT SKIP] D={level} seed={spec.seed} {model_label}: {cap_msg}", flush=True)
            return None
        res = run_episode(spec, adapter, ledger, history_mode=history_mode)
        if suffix:
            res["model"] = model_label
        _atomic_write_text(ep_path, json.dumps(res, indent=2))
        if verbose:
            cap_banner = f" [CAP_HIT: {', '.join(res['cap_hits'])}]" if res["cap_hits"] else ""
            with print_lock:
                print(
                    f"D={level} seed={spec.seed} {res['model']:28s} | "
                    f"status={res['status']:14s} served={res['served']}/{res['requested']} "
                    f"inv={res['invalid']} burn={res['burns']} emp={res['empty_turns']} trunc={res['truncated_turns']} "
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

    # Aggregate STRICTLY the episodes declared in this campaign's manifest (never stray files).
    planned_by_cell: dict[tuple[str, int], int] = {}
    manifest_episodes: list[dict[str, Any]] = []
    for entry in manifest["planned_episodes"]:
        key = (entry["model"], int(entry["level"]))
        planned_by_cell[key] = planned_by_cell.get(key, 0) + 1
        p = ep_dir / entry["filename"]
        if p.exists():
            manifest_episodes.append(json.loads(p.read_text(encoding="utf-8")))

    summary = summarize_campaign(manifest_episodes, planned_by_cell=planned_by_cell)
    _atomic_write_text(out_dir / "summary.json", json.dumps(summary, indent=2))
    md = format_markdown_table(summary)
    _atomic_write_text(out_dir / "REPORT.md", md + "\n")
    return summary
