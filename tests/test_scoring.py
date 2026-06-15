"""Scoring math (RULES §9): superlinear base value, linear decay with no free window,
complexity-gated combo, round-half-up, value-scaled expiry."""

from kitchenrush import config, scoring


def test_base_value_examples():
    assert scoring.base_value("salad") == 28.5
    assert scoring.base_value("burger") == 22.0
    assert scoring.base_value("soup") == 16.5
    assert scoring.base_value("mushroom_cheeseburger") == 54.0
    assert scoring.base_value("veggie_ramen") == 64.5


def test_time_factor_bounds_and_strictly_decreasing():
    a, d = 0.0, 100.0
    assert scoring.time_factor(a, a, d) == 1.0
    assert abs(scoring.time_factor(d, a, d) - config.FLOOR_FACTOR) < 1e-9
    assert config.FLOOR_FACTOR < scoring.time_factor(50, a, d) < 1.0
    assert scoring.time_factor(10, a, d) > scoring.time_factor(20, a, d)  # no free window
    assert scoring.time_factor(500, a, d) == config.FLOOR_FACTOR          # clamp past deadline


def test_combo_multiplier_caps_at_streak_5():
    assert scoring.combo_multiplier(0) == 1.0
    assert scoring.combo_multiplier(1) == 1.0
    assert scoring.combo_multiplier(2) == 1.25
    assert scoring.combo_multiplier(5) == 2.0
    assert scoring.combo_multiplier(10) == 2.0


def test_round_half_up_and_rewards():
    assert scoring.round_half_up(8.5) == 9
    assert scoring.round_half_up(8.4) == 8
    assert scoring.delivery_reward(22, 1.0, 1.0) == 22
    assert scoring.delivery_reward(22, 0.4, 1.0) == 9     # 8.8 -> 9
    assert scoring.expiry_penalty(22) == -11


def test_base_value_superlinear_per_recipe_step():
    # Base value strictly increases with recipe complexity (discourages farming the cheapest
    # 1-3 step dishes), and the hardest dish yields strictly more points PER RECIPE-STEP than
    # the simplest. NOTE: this is points-per-STEP, NOT points-per-game-second. Per-game-second
    # is NOT monotone in difficulty (salad beats veggie_ramen once cook/travel time is priced
    # in) — see RULES.md §9.7.4. Do not read this test as proving the per-gs property.
    order = ["soup", "burger", "salad", "mushroom_cheeseburger", "veggie_ramen"]
    by_steps = sorted(order, key=config.recipe_n_steps)
    values = [scoring.base_value(r) for r in by_steps]
    assert values == sorted(values) and len(set(values)) == len(values)  # strictly increasing
    ppr = lambda r: scoring.base_value(r) / config.recipe_n_steps(r)     # points per recipe-step
    assert ppr("veggie_ramen") > ppr("soup")
