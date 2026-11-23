"""Bounded single-action evaluation with durable per-request evidence and replay."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from ..tools import ToolCall
from .environment import (
    MAX_DECISIONS,
    PROTOCOL,
    Engine,
    decision,
    digest,
    protocol_hash,
    public_state,
    quality,
    scripted_episode,
    serialize_spec,
    verify_replay,
)
from .ledger import SCALE, BudgetExceeded, Ledger, units
from .providers import Client, ProviderError


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_episode(
    spec,
    client: Client,
    *,
    episode_id,
    stage,
    dollar_cap,
    ledger: Ledger,
    output,
    token_cap=None,
    max_decisions=MAX_DECISIONS,
):
    output = Path(output)
    if output.exists():
        raise ValueError("episode output already exists; do not overwrite or rerun")
    if token_cap is not None and client.model.provider == "TypeSafe":
        raise ValueError("Jev generation-token eligibility unresolved")
    if ledger.used(episode_id):
        raise ValueError("episode already incurred charges; audit instead of restarting")
    witness = scripted_episode(spec)
    if witness["quality"] != 100 or witness["invalid"] != 0:
        raise ValueError("instance is not verified solvable")
    engine = Engine(spec, record_trace=True)
    artifact = {
        "id": episode_id,
        "protocol": PROTOCOL,
        "protocol_hash": protocol_hash(),
        "model": asdict(client.model),
        "spec": serialize_spec(spec),
        "stage": stage,
        "dollar_cap": dollar_cap,
        "token_cap": token_cap,
        "actions": [],
        "requests": [],
        "status": "running",
        "input_tokens": 0,
        "output_tokens": 0,
        "accepted_cost_usd": 0.0,
        "boundary_cost_usd": 0.0,
        "accounting_contract": "actual-cost-action-admission-1",
    }
    artifact["spec_hash"] = digest(artifact["spec"])
    artifact["feasibility_witness_hash"] = digest(witness["actions"])
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents two local runners from overwriting one another's
    # evidence before the ledger rejects a duplicate request ID.
    with output.open("x") as initial:
        json.dump(artifact, initial, indent=2, sort_keys=True)
        initial.write("\n")
    reason = None
    settled_units = 0
    while not engine.terminated and len(artifact["actions"]) < max_decisions:
        remaining_usd = max(0, dollar_cap - ledger.used(episode_id))
        remaining_tokens = None if token_cap is None else token_cap - artifact["output_tokens"]
        if remaining_tokens is not None and remaining_tokens <= 0:
            reason = "token_budget_exhausted"
            break
        if units(dollar_cap) <= settled_units:
            reason = "dollar_budget_exhausted"
            break
        budget = {
            "kind": "generation_tokens" if token_cap is not None else "USD",
            "allowance": token_cap if token_cap is not None else dollar_cap,
            "remaining": remaining_tokens if token_cap is not None else remaining_usd,
            "decisions_remaining": max_decisions - len(artifact["actions"]),
        }
        canonical, options = decision(public_state(engine), budget)
        prepared = client.prepare(canonical, remaining_usd, remaining_tokens)
        if prepared is None:
            reason = "token_budget_exhausted"
            break
        payload, bound = prepared
        rid = f"{episode_id}:{len(artifact['requests'])}"
        try:
            ledger.reserve(rid, episode_id, stage, bound)
        except BudgetExceeded:
            reason = "operational_spending_gate"
            artifact["status"] = "incomplete"
            break
        record = {
            "id": rid,
            "canonical": canonical,
            "payload": payload,
            "reserved_usd": bound,
            "reservation_basis": client.model.reservation_basis,
            "admitted": False,
        }
        artifact["requests"].append(record)
        write_json(output, artifact)
        started = time.monotonic()
        try:
            response = client.evaluate(payload)
            record["response"] = response
            record["debug_api_seconds"] = time.monotonic() - started
            write_json(output, artifact)
            n_in, n_out, cost = client.usage(response, payload)
            record.update(input_tokens=n_in, output_tokens=n_out, cost_usd=cost)
            artifact["input_tokens"] += n_in
            artifact["output_tokens"] += n_out
            write_json(output, artifact)
            ledger.settle(rid, cost)
            settled_units += units(cost)
            client.validate_identity(response)
            prompt_bound, _ = client.bound(payload)
            if n_in > prompt_bound:
                raise ProviderError("input-token reservation bound violated")
            if client.model.provider != "TypeSafe" and n_out > payload["max_tokens"]:
                raise ProviderError("output-token cap violated")
            # The first unaffordable response is paid and archived, but never executed,
            # interpreted as an invalid action, or used to select a later attempt.
            if settled_units > units(dollar_cap):
                artifact["boundary_cost_usd"] = units(cost) / SCALE
                reason = "dollar_budget_exhausted"
                break
            selected = client.select(response)
            call = options.get(selected) if isinstance(selected, str) else None
            if call is None:
                call = ToolCall("invalid_model_selection", {})
            record["selection"] = selected
            engine.step([call])
            if sum(f["kind"] == "action" for f in engine.trace) != len(artifact["actions"]) + 1:
                raise RuntimeError("admitted action did not produce exactly one action frame")
            artifact["actions"].append(asdict(call))
            record["admitted"] = True
            artifact["accepted_cost_usd"] = settled_units / SCALE
            record["post_quality"] = quality(engine)
            record["post_state_hash"] = digest(public_state(engine))
            write_json(output, artifact)
        except (ProviderError, RuntimeError, ValueError, KeyError) as exc:
            ledger.uncertain(rid)
            artifact["status"] = "quarantined"
            artifact["error"] = f"{type(exc).__name__}: {exc}"
            reason = "provider_or_accounting_failure"
            break
    if reason is None:
        reason = "game_terminal" if engine.terminated else "decision_limit"
    artifact.update(
        reason=reason,
        quality=quality(engine),
        frames=engine.trace,
        completed=engine.counters["orders_served"],
        invalid=engine.counters["invalid_actions"],
        invalid_by_reason=engine.counters["invalid_by_reason"],
        cost_usd=ledger.used(episode_id),
    )
    if artifact["status"] == "running":
        artifact["status"] = "complete"
    verify_replay(artifact)
    artifact["replay_verified"] = True
    write_json(output, artifact)
    return artifact
