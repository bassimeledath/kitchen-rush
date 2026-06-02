"""Engine mechanics (RULES §4–§9): full recipe completion + scoring, movement/overshoot,
station gating, chained calls, and the latency-burns-food core mechanic.

These tests teleport the chef (set ``engine.chef_pos``) between stations to exercise the
action logic independently of pathfinding, on a small hand-built kitchen."""

from kitchenrush import config, scoring
from kitchenrush.engine import KitchenRushEngine
from kitchenrush.procgen import KitchenSpec, OrderSpec, StationSpec
from kitchenrush.tools import ToolCall


def make_spec() -> KitchenSpec:
    stations = [
        StationSpec((0, 1), config.ING, "bun"),
        StationSpec((0, 3), config.ING, "patty"),
        StationSpec((2, 1), config.STOVE),
        StationSpec((2, 3), config.PLATE),
        StationSpec((4, 1), config.PASS),
        StationSpec((4, 3), config.BOARD),
        StationSpec((0, 0), config.BIN),
    ]
    orders = [OrderSpec("O1", "burger", 0.0, 200.0, scoring.base_value("burger"))]
    return KitchenSpec(
        seed=0, tier="test", grid_n=5, burner_count=1, horizon_gs=300.0,
        show_ready_actions=True, active_recipes=("burger",),
        stations=stations, chef_start=(1, 0), orders=orders,
    )


def _do(eng, name, **args):
    obs = eng.step([ToolCall(name, args)], 0.0)
    return obs["last_turn"]["calls"][0]


def test_full_burger_serves_and_scores():
    eng = KitchenRushEngine(make_spec())
    eng.chef_pos = (1, 1)
    assert _do(eng, "collect", ingredient="bun")["ok"]
    eng.chef_pos = (1, 3)
    assert _do(eng, "collect", ingredient="patty")["ok"]
    eng.chef_pos = (2, 0)
    assert _do(eng, "cook", ingredient="patty")["ok"]
    eng.step([ToolCall("observe", {})], 8.0)          # think long enough for the patty to cook
    eng.chef_pos = (2, 0)
    assert _do(eng, "collect_cooked", ingredient="patty")["ok"]
    eng.chef_pos = (2, 2)
    assert _do(eng, "plate", recipe="burger")["ok"]
    eng.chef_pos = (4, 0)
    assert _do(eng, "serve", order_id="O1")["ok"]

    rep = eng.final_report()
    assert rep["counters"]["orders_served"] == 1
    assert rep["counters"]["burns"] == 0
    assert rep["score_raw"] > 0


def test_move_overshoot_then_blocked_is_invalid():
    eng = KitchenRushEngine(make_spec())
    eng.chef_pos = (1, 0)
    eng.step([ToolCall("move", {"direction": "south", "steps": 5})], 0.0)
    assert eng.chef_pos == (4, 0)            # stopped at the bottom wall
    assert eng.counters["overshoot"] == 1
    obs = eng.step([ToolCall("move", {"direction": "south", "steps": 2})], 0.0)
    assert obs["last_turn"]["calls"][0]["ok"] is False   # 0-cell move is invalid (E02)
    assert eng.counters["invalid_actions"] == 1


def test_station_gating_rejects_far_collect():
    eng = KitchenRushEngine(make_spec())
    eng.chef_pos = (2, 2)                     # not adjacent to any dispenser
    assert _do(eng, "collect", ingredient="bun")["ok"] is False


def test_chained_move_then_collect():
    eng = KitchenRushEngine(make_spec())
    eng.chef_pos = (1, 0)
    obs = eng.step(
        [ToolCall("move", {"direction": "east", "steps": 1}),
         ToolCall("collect", {"ingredient": "bun"})],
        0.0,
    )
    calls = obs["last_turn"]["calls"]
    assert len(calls) == 2 and all(c["ok"] for c in calls)
    assert len(obs["hands"]) == 1


def test_latency_burns_food_and_breaks_combo():
    eng = KitchenRushEngine(make_spec())
    eng.chef_pos = (1, 3)
    _do(eng, "collect", ingredient="patty")
    eng.chef_pos = (2, 0)
    _do(eng, "cook", ingredient="patty")
    eng.step([ToolCall("observe", {})], 30.0)   # the world moves while "thinking" -> burn
    assert eng.counters["burns"] == 1
    assert eng.combo_count == 0
    assert eng.score <= config.BURN_PENALTY
