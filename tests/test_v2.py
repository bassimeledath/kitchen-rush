"""Protocol invariants: test failures that could invalidate a paid benchmark release."""

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from kitchenrush import config
from kitchenrush.engine import CookJob
from kitchenrush.tools import ToolCall
from kitchenrush.v2.environment import (
    Engine,
    candidates,
    decision,
    digest,
    generate,
    generate_profiles,
    public_state,
    quality,
    recipe_pool,
    recipe_signature,
    scripted_episode,
    verify_replay,
)
from kitchenrush.v2.evaluation import run_episode
from kitchenrush.v2.ledger import BudgetExceeded, Ledger
from kitchenrush.v2.providers import Client, Model, reservation_tariffs


def test_reservation_tariffs_include_long_context_and_long_cache_write():
    assert reservation_tariffs(
        {
            "prompt": "0.000003",
            "completion": "0.000015",
            "input_cache_write": "0.00000375",
            "input_cache_write_1h": "0.000006",
            "web_search": "0.01",
            "overrides": [{"prompt": "0.000005", "completion": "0.0000225"}],
        }
    ) == (6e-6, 22.5e-6, 0.0)
    with pytest.raises(ValueError, match="invalid reservation tariff"):
        reservation_tariffs({"prompt": "nan", "completion": "1"})


def test_operational_reserve_uses_output_tier_without_repricing_actual_usage():
    client = Client(
        Model(
            "tiered",
            "tiered",
            "Test",
            2.5e-6,
            15e-6,
            max_output=16384,
            prompt_limit=1050000,
            reservation_input_price=5e-6,
            reservation_output_price=22.5e-6,
        )
    )
    _, reserve = client.bound({"max_tokens": 16384})
    assert reserve == pytest.approx(5.61864)
    response = {"usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.0004}}
    assert client.usage(response, {}) == (100, 10, 0.0004)


def test_token_wallet_controls_request_limit_without_old_2048_ceiling():
    client = Client(Model("fake", "fake", "Test", 1e-6, 1e-6, max_output=16384, prompt_limit=10000))
    canonical, _ = decision(public_state(Engine(generate(42))), {})
    for remaining in (512, 2048, 8192):
        payload, _ = client.prepare(canonical, remaining_usd=1, remaining_tokens=remaining)
        assert payload["max_tokens"] == remaining
    payload, _ = client.prepare(canonical, remaining_usd=1)
    assert payload["max_tokens"] == 16384


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


def test_hidden_future_cannot_change_public_action_transitions():
    spec = generate(42)
    future = spec.orders[2]
    visible_cards = {o.dish: {"bun": "RAW"} for o in spec.orders[:2]}
    a = replace(spec, recipe_cards={**visible_cards, future.dish: {"patty": "COOKED"}})
    b = replace(spec, recipe_cards={**visible_cards, future.dish: {"egg": "COOKED"}})
    for action in candidates(public_state(Engine(a))).values():
        first, second = Engine(a), Engine(b)
        assert public_state(first) == public_state(second)
        first.step([action])
        second.step([action])
        assert public_state(first) == public_state(second), action


def test_pickup_removes_heat_before_handling_but_not_before_travel():
    engine = Engine(generate(42))
    burner = engine.burners[0]
    engine.chef_pos = (burner.cell[0] - 1, burner.cell[1])
    burner.job = CookJob("patty", -10, -1, 0.5)
    engine.step([ToolCall("collect_cooked", {"ingredient": "patty", "burner_index": 0})])
    assert [(c.ingredient, c.state) for c in engine.hands] == [("patty", "COOKED")]
    assert engine.counters["burns"] == 0
    assert engine.clock_gs == config.COOK_PICKUP_GS

    engine = Engine(generate(42))
    engine.burners[0].job = CookJob("patty", -10, -1, 0.01)
    engine.step([ToolCall("collect_cooked", {"ingredient": "patty", "burner_index": 0})])
    assert not engine.hands
    assert engine.counters["burns"] == 1
    assert engine.counters["invalid_actions"] == 1


def test_invalid_action_terminates_when_last_order_expires():
    engine = Engine(generate(42), record_trace=True)
    engine.advance(max(o.deadline_gs for o in engine._order_list) - 1)
    assert not engine.terminated
    engine.step([ToolCall("chop", {"ingredient": "bun"})])
    assert engine.terminated
    assert all(o.status == "EXPIRED" for o in engine._order_list)
    assert sum(f["kind"] == "action" for f in engine.trace) == 1


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
        super().__init__(
            Model("fake", "fake", "Test", 1e-9, 1e-9, max_output=32, prompt_limit=1024)
        )
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


@pytest.mark.parametrize("allowance,admitted", [(72e-9, 2), (71e-9, 1)])
def test_actual_cost_admission_boundary_and_exact_equality(tmp_path, allowance, admitted):
    client = FakeClient(['{"action":"wait(1)"}'] * 3)
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ep = run_episode(
        generate(42),
        client,
        episode_id="boundary",
        stage="canary",
        dollar_cap=allowance,
        ledger=ledger,
        output=tmp_path / "ep.json",
    )
    assert client.requests == 2
    assert len(ep["actions"]) == admitted
    assert sum(r["admitted"] for r in ep["requests"]) == admitted
    assert ep["invalid"] == 0
    assert ep["cost_usd"] == pytest.approx(72e-9)
    assert ep["accepted_cost_usd"] == pytest.approx(admitted * 36e-9)
    assert ep["boundary_cost_usd"] == pytest.approx((2 - admitted) * 36e-9)
    assert ep["reason"] == "dollar_budget_exhausted"
    assert ep["status"] == "complete"
    # Reservation is far larger than the scored allowance; it must not stop valid work.
    assert ep["requests"][0]["reserved_usd"] > allowance


def test_operational_stop_is_unscored_and_prior_liability_immutable(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite", cap=1, opening_balance=0.999999999)
    client = FakeClient([])
    ep = run_episode(
        generate(42),
        client,
        episode_id="stop",
        stage="canary",
        dollar_cap=1,
        ledger=ledger,
        output=tmp_path / "ep.json",
    )
    assert ep["status"] == "incomplete" and client.requests == 0
    assert ep["reason"] == "operational_spending_gate"
    assert ledger.used() == 0.999999999 and ledger.used("stop") == 0
    with pytest.raises(ValueError, match="prior spending"):
        Ledger(tmp_path / "ledger.sqlite", cap=1, opening_balance=0)
    from kitchenrush.v2.analysis import summarize

    with pytest.raises(ValueError):
        summarize([ep])


def test_uncertainty_requires_multiple_seeds_within_each_family():
    from kitchenrush.v2.analysis import summarize

    episodes = [
        dict(
            status="complete",
            replay_verified=True,
            model={"key": "fake"},
            token_cap=None,
            dollar_cap=1,
            quality=100 * (size - 2) / 2,
            spec={"seed": size, "tier": f"v2-main-{size}", "orders": [{}] * 3},
            completed=1,
            invalid=0,
            output_tokens=100,
            cost_usd=0.1,
            reason="game_terminal",
        )
        for size in (2, 3, 4)
    ]
    row = summarize(episodes)["rows"][0]
    assert row["quality"] == 50 and row["ci95"] is None


def test_exact_token_and_dollar_exhaustion_is_not_censored(tmp_path):
    ep = run_episode(
        generate(42),
        FakeClient(['{"action":"wait(1)"}']),
        episode_id="equal",
        stage="canary",
        dollar_cap=36e-9,
        token_cap=16,
        ledger=Ledger(tmp_path / "ledger.sqlite"),
        output=tmp_path / "ep.json",
    )
    assert ep["reason"] == "token_budget_exhausted"
    assert len(ep["actions"]) == 1


def test_known_usage_survives_reservation_overrun(tmp_path):
    client = FakeClient(['{"action":"wait(1)"}'])
    client.model = replace(client.model, prompt_limit=1)
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ep = run_episode(
        generate(42),
        client,
        episode_id="overrun",
        stage="canary",
        dollar_cap=1,
        ledger=ledger,
        output=tmp_path / "ep.json",
    )
    assert ep["status"] == "quarantined" and ep["actions"] == []
    assert ep["output_tokens"] == 16 and ep["input_tokens"] == 20
    assert ep["requests"][0]["cost_usd"] == 36e-9
    assert ep["cost_usd"] == 36e-9 and ledger.snapshot()["overruns"] == 1


def test_profiles_are_split_isolated_and_public():
    seen = {}
    for split in ("development", "pilot", "main", "confirmation"):
        seen[split] = {digest(generate_profiles(seed, split)) for seed in range(20)}
    for split, signatures in seen.items():
        assert all(not signatures & other for name, other in seen.items() if name != split)
    a, b = generate(42, size=2), generate(42, size=4)
    assert a.ingredient_rules == b.ingredient_rules
    assert public_state(Engine(a))["ingredient_rules"] == a.ingredient_rules


def test_same_card_requires_different_preparation_under_public_profiles():
    from kitchenrush.engine import Component
    from kitchenrush.v2.environment import RecipeReader

    original = copy.deepcopy(config.INGREDIENTS)
    spec = generate(42)
    cards = {r: {"mushroom": "COOKED"} for r in spec.recipe_cards}
    engines = []
    for prerequisite in ("RAW", "CHOPPED"):
        profiles = copy.deepcopy(spec.ingredient_rules)
        profiles["mushroom"]["cookable_from"] = prerequisite
        engine = Engine(replace(spec, recipe_cards=cards, ingredient_rules=profiles))
        engine.hands = [Component("mushroom", "RAW")]
        engines.append(engine)
    raw, chopped = engines
    assert RecipeReader().choose(public_state(raw)).name == "cook"
    assert RecipeReader().choose(public_state(chopped)).name == "chop"
    for engine in engines:
        engine.step([ToolCall("cook", {"ingredient": "mushroom"})])
    assert raw.counters["invalid_actions"] == 0 and any(b.job for b in raw.burners)
    assert chopped.counters["invalid_actions"] == 1 and not any(b.job for b in chopped.burners)
    assert config.INGREDIENTS == original


@pytest.mark.parametrize("cook_time,burn_window", [(4, 3), (12, 8), (12, 3), (4, 8)])
@pytest.mark.parametrize("prerequisite", ["RAW", "CHOPPED"])
def test_profile_extremes_remain_solvable_and_replayable(cook_time, burn_window, prerequisite):
    for seed in range(4):
        spec = generate(seed, size=4)
        profiles = copy.deepcopy(spec.ingredient_rules)
        for profile in profiles.values():
            if profile["cookable_from"]:
                profile.update(cook_time=cook_time, burn_window=burn_window)
        profiles["mushroom"]["cookable_from"] = prerequisite
        episode = scripted_episode(replace(spec, ingredient_rules=profiles))
        assert episode["quality"] == 100 and episode["invalid"] == 0
        assert verify_replay(episode)


def test_profile_timers_control_actual_ready_and_burn_events():
    from kitchenrush.engine import Component

    spec = generate(42)
    engines = []
    for cook_time in (4, 12):
        profiles = copy.deepcopy(spec.ingredient_rules)
        profiles["patty"].update(cook_time=cook_time, burn_window=3)
        engine = Engine(replace(spec, ingredient_rules=profiles))
        engine.hands = [Component("patty", "RAW")]
        engine.step([ToolCall("cook", {"ingredient": "patty"})])
        job = next(b.job for b in engine.burners if b.job)
        assert job.ready_gs == engine.clock_gs + cook_time
        assert job.burn_gs == job.ready_gs + 3
        engine.step([ToolCall("wait", {"seconds": 10})])
        engines.append(engine)
    assert engines[0].counters["burns"] == 1
    assert engines[1].counters["burns"] == 0
    assert next(b.job for b in engines[1].burners if b.job).status(engines[1].clock_gs) == "COOKING"
