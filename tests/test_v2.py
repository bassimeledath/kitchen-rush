"""Protocol invariants: test failures that could invalidate a paid benchmark release."""

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from kitchenrush import config
from kitchenrush.tools import ToolCall
from kitchenrush.v2.environment import (
    Engine,
    candidates,
    decision,
    digest,
    generate,
    public_state,
    quality,
    recipe_pool,
    recipe_signature,
    scripted_episode,
    verify_replay,
)
from kitchenrush.v2.evaluation import run_episode
from kitchenrush.v2.ledger import BudgetExceeded, Ledger
from kitchenrush.v2.providers import Client, Model


def test_structural_holdouts_and_no_global_mutation():
    original = copy.deepcopy(config.RECIPES)
    for size in (2, 3, 4):
        pools = [
            {recipe_signature(c) for c in recipe_pool(size, split)}
            for split in ("development", "pilot", "main", "confirmation")
        ]
        for i, pool in enumerate(pools):
            assert pool
            assert all(not pool & other for other in pools[i + 1 :])
    a, b = Engine(generate(42)), Engine(generate(43))
    a.recipes["R1"].clear()
    assert b.recipes["R1"]
    assert config.RECIPES == original


@pytest.mark.parametrize("size", [2, 3, 4])
@pytest.mark.parametrize("split", ["development", "pilot", "main", "confirmation"])
def test_solvability_and_replay(size, split):
    for seed in range(3):
        ep = scripted_episode(generate(seed, split, size))
        assert ep["quality"] == 100, (seed, split, size, ep["quality"])
        assert ep["invalid"] == 0
        assert verify_replay(ep)
        broken = copy.deepcopy(ep)
        broken["quality"] -= 1
        with pytest.raises(ValueError):
            verify_replay(broken)


def test_no_hidden_future_direct_or_derived_leaks():
    spec = generate(42)
    future = spec.orders[2]
    cards = copy.deepcopy(spec.recipe_cards)
    cards[future.dish] = {"bun": "RAW"}
    modified = replace(
        spec,
        recipe_cards=cards,
        horizon_gs=99999,
        orders=[
            *spec.orders[:2],
            replace(future, deadline_gs=9999, arrival_gs=300, base_value=10000),
        ],
    )
    a, b = public_state(Engine(spec)), public_state(Engine(modified))
    assert digest(a) == digest(b)
    assert digest(decision(a, {})[0]) == digest(decision(b, {})[0])
    assert future.dish not in a["recipe_cards"]
    assert "remaining_gs" not in a and "ready_actions" not in a


def test_candidates_are_not_legality_filtered():
    engine = Engine(generate(42))
    options = candidates(public_state(engine))
    assert "chop(bun)" in options
    assert "move_to(0,0)" in options
    assert "plate(R1)" in options or "plate(R2)" in options
    engine.step([options["chop(bun)"]])
    assert engine.counters["invalid_actions"] == 1
    assert len(options) < 255


def test_clock_wait_burn_stall_and_one_action():
    engine = Engine(generate(2))
    with pytest.raises(ValueError):
        engine.step([ToolCall("wait", {"seconds": 1})], 20)
    with pytest.raises(ValueError):
        engine.step([ToolCall("wait", {"seconds": 1})] * 2)
    before = engine.clock_gs
    for _ in range(15):
        engine.step([ToolCall("wait", {"seconds": 1})])
    assert engine.clock_gs == before + 15
    assert not engine.terminated
    engine.active_ingredients.add("patty")
    engine.step([ToolCall("collect", {"ingredient": "patty"})])
    engine.step([ToolCall("cook", {"ingredient": "patty"})])
    engine.step([ToolCall("wait", {"seconds": 20})])
    assert engine.counters["burns"] == 1


def test_scoring_truncation_and_invalid_penalty():
    engine = Engine(generate(4))
    assert quality(engine) == 0
    engine.counters["orders_served"] = 2
    engine.counters["invalid_actions"] = 1
    assert quality(engine) == pytest.approx(100 * 1.9 / 3)
    engine.final_report()
    assert quality(engine) == pytest.approx(100 * 1.9 / 3)


def test_atomic_reservation_crash_and_idempotence(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite", cap=0.1)

    def reserve(i):
        try:
            ledger.reserve(str(i), "episode", "canary", 0.06, 0.1)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(reserve, [1, 2])) == 1
    assert Ledger(tmp_path / "ledger.sqlite", cap=0.1).used() == 0.06
    with ledger.connect() as con:
        rid = con.execute("SELECT id FROM requests").fetchone()[0]
    ledger.uncertain(rid)
    assert ledger.used() == 0.06
    ledger.settle(rid, 0.01)
    ledger.settle(rid, 0.01)
    assert ledger.used() == 0.01
    with pytest.raises(ValueError):
        ledger.settle(rid, 0.02)
    with pytest.raises(ValueError):
        ledger.reserve(rid, "episode", "canary", 0.01, 0.1)


class FakeClient(Client):
    def __init__(self, outputs):
        super().__init__(Model("fake", "fake", "Test", 1e-9, 1e-9, max_output=32))
        self.outputs = iter(outputs)
        self.requests = 0

    def evaluate(self, payload):
        self.requests += 1
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return {
            "model": "fake",
            "provider": "Test",
            "usage": {"prompt_tokens": 20, "completion_tokens": 16, "cost": 36e-9},
            "choices": [{"finish_reason": "stop", "message": {"content": value}}],
        }


def test_token_cap_invalid_output_and_replay(tmp_path):
    client = FakeClient(["not JSON", '{"action":"wait(1)"}'])
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ep = run_episode(
        generate(42),
        client,
        episode_id="fake",
        stage="canary",
        dollar_cap=0.1,
        token_cap=32,
        ledger=ledger,
        output=tmp_path / "ep.json",
    )
    assert ep["output_tokens"] == 32 and ep["invalid"] == 1
    assert ep["reason"] == "token_budget_exhausted"
    assert client.requests == 2 and ep["replay_verified"]
    assert ledger.used() == pytest.approx(72e-9)


def test_no_money_means_no_dispatch(tmp_path):
    client = FakeClient([])
    ep = run_episode(
        generate(42),
        client,
        episode_id="empty",
        stage="canary",
        dollar_cap=0,
        ledger=Ledger(tmp_path / "ledger.sqlite"),
        output=tmp_path / "ep.json",
    )
    assert client.requests == 0 and ep["quality"] == 0


def test_transport_error_is_quarantined_and_reserved(tmp_path):
    from kitchenrush.v2.providers import ProviderError

    client = FakeClient([ProviderError("test disconnect")])
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ep = run_episode(
        generate(42),
        client,
        episode_id="fail",
        stage="canary",
        dollar_cap=0.1,
        ledger=ledger,
        output=tmp_path / "ep.json",
    )
    assert ep["status"] == "quarantined" and ep["invalid"] == 0
    assert ledger.snapshot()["unresolved_requests"] == 1


def test_b80_does_not_interpolate_or_use_cost_censored_cells():
    from kitchenrush.v2.analysis import summarize

    episodes = []
    for budget, q in [(512, 70), (2048, 85), (8192, 100)]:
        for seed in (1, 2, 3):
            episodes.append(
                {
                    "status": "complete",
                    "replay_verified": True,
                    "model": {"key": "test"},
                    "token_cap": budget,
                    "dollar_cap": 1,
                    "spec": {"seed": seed, "orders": [{}, {}, {}]},
                    "quality": q,
                    "cost_usd": 0.1,
                    "completed": 2,
                    "invalid": 0,
                    "output_tokens": budget,
                    "reason": "cannot_reserve_next_request" if budget == 2048 else "game_terminal",
                }
            )
    result = summarize(episodes)
    assert result["B80"]["test"]["mean"] == 8192
    assert result["B80"]["test"]["conservative"] == 8192


def test_changed_requirement_pair_changes_needed_action():
    from kitchenrush.engine import Component
    from kitchenrush.v2.environment import RecipeReader

    spec = generate(77)
    first = spec.orders[0]
    cards = copy.deepcopy(spec.recipe_cards)
    cards[first.dish] = {"lettuce": "RAW", "bun": "RAW"}
    raw = Engine(replace(spec, recipe_cards=cards))
    chopped_cards = copy.deepcopy(cards)
    chopped_cards[first.dish]["lettuce"] = "CHOPPED"
    chopped = Engine(replace(spec, recipe_cards=chopped_cards))
    for e in (raw, chopped):
        e.hands = [Component("lettuce", "RAW"), Component("bun", "RAW")]
    a = RecipeReader().choose(public_state(raw))
    b = RecipeReader().choose(public_state(chopped))
    assert a.name == "plate" and b.name == "chop"
    # A memorized old action really fails under the changed requirement.
    chopped.step([a])
    assert chopped.counters["invalid_actions"] == 1


def test_stage_cap_and_provider_overage_stop(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    with pytest.raises(BudgetExceeded):
        ledger.reserve("x", "e", "canary", 5.01, 10)
    ledger.reserve("y", "e", "canary", 0.01, 1)
    with pytest.raises(RuntimeError):
        ledger.settle("y", 0.02)
    assert ledger.snapshot()["overruns"] == 1 and ledger.used() == 0.02


def test_abandoned_transport_keeps_entire_liability(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite", cap=0.1)
    ledger.reserve("old", "abandoned", "canary", 0.06, 0.1)
    ledger.uncertain("old")
    ledger.retain_failed_liability("old")
    assert ledger.used() == 0.06
    assert ledger.snapshot()["settled_usd"] == 0
    assert ledger.snapshot()["retained_failed_requests"] == 1
    with pytest.raises(BudgetExceeded):
        ledger.reserve("new", "fresh", "canary", 0.05, 0.1)
