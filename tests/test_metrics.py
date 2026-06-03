"""KR headline (null->reference), Pass^k, and aggregate diagnostics (METHODOLOGY §1)."""

from kitchenrush import procgen
from kitchenrush.baselines import NullAgent, RandomAgent
from kitchenrush.metrics import aggregate, episode_metrics, percentile
from kitchenrush.report import EpisodeResult
from kitchenrush.runner import run_suite


def test_percentile():
    vals = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert percentile(vals, 0.0) == 0.0
    assert percentile(vals, 1.0) == 4.0
    assert percentile(vals, 0.5) == 2.0
    assert percentile([], 0.5) == 0.0


def _fake_episode(seed, trial, score, *, s_ref=100.0, s_null=0.0, served=5, total=5, invalid=0, calls=10):
    report = {
        "seed": seed, "tier": "easy", "score_raw": score, "score_display": max(0, score),
        "counters": {
            "orders_total": total, "orders_served": served, "orders_expired": total - served,
            "invalid_actions": invalid, "total_tool_calls": calls, "burns": 0,
        },
    }
    steps = [{"think_gs": 0.5}, {"think_gs": 1.5}]
    return EpisodeResult(seed=seed, tier="easy", report=report, steps=steps,
                         s_ref=s_ref, s_null=s_null, trial=trial)


def test_episode_kr_and_pass_threshold():
    good = episode_metrics(_fake_episode(0, 0, 80.0, s_ref=100.0, s_null=0.0))   # kr=0.8 >= 0.6
    bad = episode_metrics(_fake_episode(0, 0, 40.0, s_ref=100.0, s_null=0.0))    # kr=0.4 < 0.6
    assert good["kr"] == 0.8 and good["passed"]
    assert bad["kr"] == 0.4 and not bad["passed"]


def test_null_to_reference_normalization_uses_floor():
    # score equal to the null floor -> kr 0; score at the reference -> kr 1
    assert episode_metrics(_fake_episode(0, 0, -20.0, s_ref=80.0, s_null=-20.0))["kr"] == 0.0
    assert episode_metrics(_fake_episode(0, 0, 80.0, s_ref=80.0, s_null=-20.0))["kr"] == 1.0


def test_degenerate_instance_excluded():
    # S_ref <= S_null -> degenerate, no kr, excluded from headline
    m = episode_metrics(_fake_episode(0, 0, 10.0, s_ref=-5.0, s_null=-5.0))
    assert m["degenerate"] and m["kr"] is None
    agg = aggregate([_fake_episode(0, 0, 10.0, s_ref=-5.0, s_null=-5.0)], k=1)
    assert agg["degenerate_instances"] == 1 and agg["KR"] == 0.0


def test_aggregate_bounds_and_passk():
    eps = [
        _fake_episode(0, 0, 80.0), _fake_episode(0, 1, 90.0),
        _fake_episode(1, 0, 80.0), _fake_episode(1, 1, 30.0),
    ]
    agg = aggregate(eps, k=2)
    assert agg["episodes"] == 4 and agg["seeds"] == 2
    assert agg["pass_1"] == 0.75          # 3 of 4 episodes pass
    assert agg["pass_2"] == 0.5           # only seed 0 passes all trials
    assert agg["KR"] == 70.0              # 100*mean(0.8,0.9,0.8,0.3)
    assert 0.0 <= agg["KR"] <= 100.0


def test_suite_runs_and_is_deterministic():
    factory = lambda seed, trial: RandomAgent(seed=seed * 1000 + trial, latency=0.5)
    a = aggregate(run_suite(range(0, 3), "easy", factory, trials=2), k=2)
    b = aggregate(run_suite(range(0, 3), "easy", factory, trials=2), k=2)
    assert a == b
    assert 0.0 <= a["KR"] <= 100.0


def test_null_baseline_scores_zero_kr():
    # a do-nothing agent that lets time pass sits at the floor -> KR 0
    agg = aggregate(run_suite(range(0, 3), "easy", lambda s, t: NullAgent(latency=20.0), trials=1), k=1)
    assert agg["KR"] == 0.0
