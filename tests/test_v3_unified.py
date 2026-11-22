"""Offline unit tests for Kitchen Rush v3 unified engine, batching, interrupts, and adapters."""

from __future__ import annotations

from pathlib import Path

from kitchenrush.tools import ToolCall
from kitchenrush.v3.adapters import DecisionModelAdapter, TurnDecision
from kitchenrush.v3.env import (
    UnifiedEngine,
    dynamic_state,
    generate_kitchen,
    max_actions_for_level,
    static_context,
    verify_replay,
)
from kitchenrush.v3.metrics import cell_ratios, summarize_campaign
from kitchenrush.v3.replay import build_v3_replay
from kitchenrush.v3.runner import SpendLedger, run_episode
from kitchenrush.v3.solvers import best_parallel_witness


def test_generate_and_replay_all_levels():
    for level in (1, 2, 3, 4, 5):
        # Find first clean seed for this level
        spec = None
        batches = None
        for seed in range(2000, 2020):
            try:
                spec, batches = generate_kitchen(seed, level)
                break
            except RuntimeError:
                continue
        assert spec is not None and batches is not None
        res = verify_replay(
            spec, batches, expected_served=len(spec.orders), expected_invalid=0, expected_burns=0
        )
        assert res["verified"] is True
        assert res["served"] == len(spec.orders)


def test_procedural_prep_rules_vary_beyond_mushroom():
    seen_chop_reqs = set()
    for seed in range(2000, 2030):
        spec, _ = generate_kitchen(seed, 1)
        for name, rule in spec.ingredient_rules.items():
            if rule["cookable_from"] == "CHOPPED":
                seen_chop_reqs.add(name)
    # All cookable ingredients should be eligible for CHOPPED prerequisite, not just mushroom
    assert len(seen_chop_reqs) >= 4


def test_batch_interrupts_on_invalid_and_order_arrival():
    spec, _ = generate_kitchen(2000, 2)
    # 1. Invalid action mid-batch aborts remaining calls
    eng = UnifiedEngine(spec, record_trace=True, early_stop=False)
    res = eng.step_batch([
        ToolCall("collect", {"ingredient": "patty"}),
        ToolCall("serve", {"order_id": "O1"}),  # invalid: not holding plate!
        ToolCall("wait", {"seconds": 5}),
    ])
    assert res["interrupted_reason"] == "invalid_action"
    assert len(res["calls"]) == 2
    assert res["aborted_calls"] == ["wait"]

    # 2. Hidden order arrival mid-batch interrupts subsequent queued calls
    spec3, _ = generate_kitchen(2000, 3)
    pending_arrivals = [o.arrival_gs for o in spec3.orders if o.arrival_gs > 0]
    assert pending_arrivals, "D=3 must have staggered hidden arrivals"
    first_arr = int(min(pending_arrivals))
    eng3 = UnifiedEngine(spec3, record_trace=True, early_stop=False)
    res3 = eng3.step_batch([
        ToolCall("wait", {"seconds": first_arr + 1}),
        ToolCall("wait", {"seconds": 5}),
    ])
    assert res3["interrupted_reason"] == "order_arrived"
    assert res3["aborted_calls"] == ["wait"]


def test_early_stop_on_impossible_deadlines():
    spec, _ = generate_kitchen(2000, 1)
    eng = UnifiedEngine(spec, record_trace=True, early_stop=True)
    # Wait past nearly all deadlines so remaining work is impossible
    max_dl = max(o.deadline_gs for o in spec.orders)
    eng.step_batch([ToolCall("wait", {"seconds": min(120, int(max_dl))})])
    assert eng.terminated is True
    assert eng.termination_reason in ("mathematically_impossible", "orders_exhausted", "horizon")


def test_decision_model_adapter_compatibility(tmp_path: Path):
    spec, _ = generate_kitchen(2000, 1)

    class DummyDecisionAdapter(DecisionModelAdapter):
        @property
        def name(self) -> str:
            return "dummy-decision"

        def choose_one(self, static_ctx, dyn_state) -> TurnDecision:
            # Deliberately return 2 calls to verify DecisionModelAdapter enforces batch-size 1
            return TurnDecision(
                calls=[ToolCall("wait", {"seconds": 10}), ToolCall("wait", {"seconds": 10})],
                prompt_tokens=100,
                completion_tokens=10,
                cost_usd=0.0001,
            )

    ledger = SpendLedger(tmp_path / "spend.jsonl", campaign_cap_usd=1.0)
    ep = run_episode(spec, DummyDecisionAdapter(), ledger, max_turns=15)
    assert ep["scored"] is True
    assert ep["replay_verified"] is True
    assert all(len(b) == 1 for b in ep["batches"])
    summary = summarize_campaign([ep])
    assert summary["total_scored"] == 1


def test_no_hidden_orders_leaked_in_dynamic_state():
    spec, _ = generate_kitchen(2000, 3)
    eng = UnifiedEngine(spec, early_stop=False)
    s_ctx = static_context(eng)
    d_st = dynamic_state(eng)
    pending_ids = {o.order_id for o in spec.orders if o.arrival_gs > 0}
    pending_dishes = {o.dish for o in spec.orders if o.arrival_gs > 0}
    visible_ids = {o["order_id"] for o in d_st["active_orders"]}
    assert pending_ids.isdisjoint(visible_ids)
    assert pending_dishes.isdisjoint(set(d_st["recipe_cards"].keys()))
    assert d_st["progress"]["pending_hidden"] == len(pending_ids)
    assert "orders" not in s_ctx
    assert "recent_turns" not in d_st
    assert "last_turn" not in d_st


def test_atomic_spend_reservation_prevents_overspend(tmp_path: Path):
    ledger = SpendLedger(tmp_path / "spend.jsonl", campaign_cap_usd=1.00)
    ok0, r0, _ = ledger.reserve(0.95)
    assert ok0 and r0 is not None
    ledger.settle(r0, {"model": "m", "cost_usd": 0.95})
    # With $0.95 spent against a $1.00 cap, at most zero $0.10 calls can reserve concurrently.
    ok1, r1, _ = ledger.reserve(0.10)
    assert ok1 is False and r1 is None
    # Two $0.03 reservations: first succeeds ($0.98), second must be rejected ($1.01 > $1.00).
    ok2, r2, _ = ledger.reserve(0.03)
    ok3, r3, _ = ledger.reserve(0.03)
    assert ok2 is True and r2 is not None
    assert ok3 is False and r3 is None
    ledger.release(r2)
    # After release, reservation is freed.
    ok4, r4, _ = ledger.reserve(0.03)
    assert ok4 is True and r4 is not None


def test_full_clear_gated_efficiency_metrics():
    ep_clear = {
        "model": "m", "level": 3, "seed": 1, "scored": True, "clear": True,
        "served": 4, "requested": 4, "output_tokens": 1000, "input_tokens": 2000,
        "cached_tokens": 1000, "cost_usd": 0.04, "invalid": 0, "burns": 0,
        "empty_turns": 0, "truncated_turns": 0, "actions": 20, "turns": 5,
    }
    ep_partial = {
        "model": "m", "level": 3, "seed": 2, "scored": True, "clear": False,
        "served": 2, "requested": 4, "output_tokens": 1000, "input_tokens": 2000,
        "cached_tokens": 1000, "cost_usd": 0.04, "invalid": 1, "burns": 0,
        "empty_turns": 1, "truncated_turns": 1, "actions": 20, "turns": 5,
    }
    # Zero clears -> cleared-order efficiency is None ("no clear"), per-served-order still finite.
    r_none = cell_ratios([ep_partial])
    assert r_none["usd_per_cleared_order"] is None
    assert r_none["out_tok_per_cleared_order"] is None
    assert r_none["usd_per_order"] == 0.02

    # One clear + one partial -> numerator pools BOTH episodes ($0.08), denominator = 4 cleared orders -> $0.02/cleared ord.
    r_mix = cell_ratios([ep_clear, ep_partial])
    assert abs((r_mix["usd_per_cleared_order"] or 0.0) - 0.02) < 1e-9
    assert abs((r_mix["out_tok_per_cleared_order"] or 0.0) - 500.0) < 1e-9


def test_no_oracle_witness_and_replay_export():
    spec, batches = generate_kitchen(2000, 3)
    b_no_oracle, _ = best_parallel_witness(spec, allow_arrival_peek=False)
    res = verify_replay(spec, b_no_oracle, expected_served=len(spec.orders), expected_invalid=0, expected_burns=0)
    assert res["verified"] is True

    fake_ep = {
        "model": "witness", "level": 3, "seed": 2000,
        "served": len(spec.orders), "requested": len(spec.orders), "clear": True,
        "output_tokens": 0, "cost_usd": 0.0, "turns": len(batches), "actions": sum(len(b) for b in batches),
        "batches": batches,
    }
    doc = build_v3_replay(fake_ep, spec)
    assert doc["meta"]["protocol"] == "kitchen-rush-v3"
    assert len(doc["frames"]) >= len(batches) + 2
    assert doc["frames"][0]["kind"] == "start"
    assert doc["frames"][-1]["kind"] == "end"
    # Procedural ingredient rules must be reflected in the exported replay catalog.
    for ing, prof in spec.ingredient_rules.items():
        assert doc["catalog"]["ingredients"][ing]["cook_time"] == prof["cook_time"]
        assert doc["catalog"]["ingredients"][ing]["cookable_from"] == prof["cookable_from"]


def test_history_modes_full_chat_and_last_turn(tmp_path: Path):
    from kitchenrush.v3.adapters import AgentAdapter

    spec, _ = generate_kitchen(2000, 1)

    # 1. full_chat (default): grows append-only assistant/tool message history and replays cleanly.
    observed_msg_counts: list[int] = []

    class FullChatDummy(AgentAdapter):
        @property
        def name(self) -> str:
            return "full-chat-dummy"

        def act(self, static_ctx, dyn_state, *, chat_messages=None) -> TurnDecision:
            observed_msg_counts.append(len(chat_messages or []))
            assert "last_turn" not in dyn_state
            return TurnDecision(calls=[ToolCall("wait", {"seconds": 30}, id=f"tc_{len(observed_msg_counts)}")])

    ledger1 = SpendLedger(tmp_path / "spend1.jsonl", campaign_cap_usd=1.0)
    ep1 = run_episode(spec, FullChatDummy(), ledger1, history_mode="full_chat", max_turns=10)
    assert ep1["scored"] is True and ep1["replay_verified"] is True
    assert observed_msg_counts[0] == 0
    assert observed_msg_counts[-1] >= 3

    # 2. last_turn (for 1-step decision models): passes only last_turn feedback with no chat_messages.
    seen_last_turns: list[bool] = []

    class LastTurnDummy(AgentAdapter):
        @property
        def name(self) -> str:
            return "last-turn-dummy"

        def act(self, static_ctx, dyn_state, *, chat_messages=None) -> TurnDecision:
            assert chat_messages is None
            seen_last_turns.append("last_turn" in dyn_state)
            return TurnDecision(calls=[ToolCall("wait", {"seconds": 30})])

    ledger2 = SpendLedger(tmp_path / "spend2.jsonl", campaign_cap_usd=1.0)
    ep2 = run_episode(spec, LastTurnDummy(), ledger2, history_mode="last_turn", max_turns=10)
    assert ep2["scored"] is True and ep2["replay_verified"] is True
    assert seen_last_turns[0] is False
    assert any(seen_last_turns[1:])


def test_scaled_action_budget_and_d6_full_burner_witness():
    assert max_actions_for_level(5) == 160
    assert max_actions_for_level(6) == 168
    assert max_actions_for_level(10) == 264
    spec = None
    batches = None
    for seed in range(6000, 6030):
        try:
            spec, batches = generate_kitchen(seed, 6)
            break
        except RuntimeError:
            continue
    assert spec is not None and batches is not None
    eng = UnifiedEngine(spec)
    assert eng.max_actions == 168
    res = verify_replay(spec, batches, expected_served=len(spec.orders), expected_invalid=0, expected_burns=0)
    assert res["verified"] is True


def test_manifest_blocks_stale_episode_reuse_and_stray_files(tmp_path: Path):
    import dataclasses
    import json
    import pytest
    from kitchenrush.v3.adapters import AgentAdapter
    from kitchenrush.v3.runner import run_campaign

    spec, _ = generate_kitchen(2000, 1)

    class WaitAdapter(AgentAdapter):
        @property
        def name(self) -> str:
            return "wait-adapter"

        def act(self, static_ctx, dyn_state, *, chat_messages=None, reserve_attempt=None, release_attempt=None) -> TurnDecision:
            return TurnDecision(calls=[ToolCall("wait", {"seconds": 60})])

    out_dir = tmp_path / "camp"
    s1 = run_campaign(out_dir, {1: [spec]}, [WaitAdapter()], levels=(1,), max_seeds=1, history_mode="full_chat", verbose=False)
    assert s1["total_planned"] == 1 and s1["total_scored"] == 1

    # Drop a stray episode JSON outside the manifest — resume must NOT aggregate it!
    (out_dir / "episodes" / "D1_seed9999_stray.json").write_text(
        json.dumps({"model": "stray", "level": 1, "seed": 9999, "scored": True, "clear": False, "served": 0, "requested": 2,
                    "output_tokens": 0, "input_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0, "invalid": 0, "burns": 0,
                    "actions": 1, "turns": 1}),
        encoding="utf-8",
    )
    s2 = run_campaign(out_dir, {1: [spec]}, [WaitAdapter()], levels=(1,), max_seeds=1, history_mode="full_chat", verbose=False)
    assert len(s2["rows"]) == 1 and s2["rows"][0]["model"] == "wait-adapter"

    # Mutate first order deadline -> spec_sha256 changes -> runner MUST refuse to reuse cached episode!
    mutated = dataclasses.replace(
        spec,
        orders=[dataclasses.replace(spec.orders[0], deadline_gs=1.0)] + list(spec.orders[1:]),
    )
    with pytest.raises(ValueError, match="Manifest episode spec mismatch"):
        run_campaign(out_dir, {1: [mutated]}, [WaitAdapter()], levels=(1,), max_seeds=1, history_mode="full_chat", verbose=False)


def test_persistent_reservation_survives_crash_and_scales_with_payload(tmp_path: Path):
    from kitchenrush.v3.adapters import ModelSpec, OpenRouterToolAdapter

    # 1. Unsettled reservation survives process restart as conservative liability.
    p = tmp_path / "spend.jsonl"
    l1 = SpendLedger(p, campaign_cap_usd=1.00)
    ok, rid, _ = l1.reserve(0.60, meta={"model": "m"})
    assert ok and rid is not None
    # Simulate process crash (no settle / no release) and reload from disk:
    l2 = SpendLedger(p, campaign_cap_usd=1.00)
    assert abs(l2.unreconciled_crash_usd - 0.60) < 1e-6
    ok2, _, _ = l2.reserve(0.50)
    assert ok2 is False

    # 2. Large full-chat serialized body reserves more than baseline 6k-token estimate.
    spec = ModelSpec(key="sol", model_id="openai/gpt-6.2-sol", tier="frontier", input_price_per_m=2.0, output_price_per_m=10.0)
    adapter = OpenRouterToolAdapter(spec, api_key="test-key")
    small_res = adapter._reserve_for_bytes(18_000)
    large_res = adapter._reserve_for_bytes(150_000)
    assert large_res > small_res + 0.08


def test_wilson_ci_nonzero_width_at_zero_and_full_clears():
    from kitchenrush.v3.metrics import bootstrap_cell

    base = {
        "model": "m", "level": 5, "scored": True, "served": 3, "requested": 6,
        "output_tokens": 500, "input_tokens": 1000, "cached_tokens": 500,
        "cost_usd": 0.05, "invalid": 0, "burns": 0, "actions": 20, "turns": 5,
    }
    ci_zero = bootstrap_cell([{**base, "seed": i, "clear": False} for i in range(6)])["full_clear_rate"]
    ci_full = bootstrap_cell([{**base, "seed": i, "clear": True, "served": 6} for i in range(6)])["full_clear_rate"]
    assert ci_zero is not None and ci_zero[0] == 0.0 and 0.35 < ci_zero[1] < 0.45
    assert ci_full is not None and 0.55 < ci_full[0] < 0.65 and ci_full[1] == 1.0
