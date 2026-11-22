"""Offline unit tests for Kitchen Rush v3 unified engine, batching, interrupts, and adapters."""

from __future__ import annotations

from pathlib import Path

from kitchenrush.tools import ToolCall
from kitchenrush.v3.adapters import DecisionModelAdapter, TurnDecision
from kitchenrush.v3.env import (
    UnifiedEngine,
    dynamic_state,
    generate_kitchen,
    static_context,
    verify_replay,
)
from kitchenrush.v3.metrics import summarize_campaign
from kitchenrush.v3.runner import SpendLedger, run_episode
from kitchenrush.v3.solvers import run_greedy_overlap_solver, run_sequential_solver


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
