"""Phase 3: aggregate metrics, Pass^k reliability, and the RTTC headline (SCORING §6)."""

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


def _fake_episode(seed, trial, score, *, s_ref=100.0, served=5, total=5, invalid=0, calls=10):
    report = {
        "seed": seed, "tier": "easy", "score_raw": score, "score_display": max(0, score),
        "counters": {
            "orders_total": total, "orders_served": served, "orders_expired": total - served,
            "invalid_actions": invalid, "total_tool_calls": calls, "burns": 0,
        },
    }
    steps = [{"think_gs": 0.5}, {"think_gs": 1.5}]
    return EpisodeResult(seed=seed, tier="easy", report=report, steps=steps, s_ref=s_ref, trial=trial)


def test_episode_metrics_pass_threshold():
    good = episode_metrics(_fake_episode(0, 0, 80.0, s_ref=100.0))   # 0.8 >= 0.6
    bad = episode_metrics(_fake_episode(0, 0, 40.0, s_ref=100.0))    # 0.4 < 0.6
    assert good["passed"] and good["eta"] == 0.8
    assert not bad["passed"] and bad["eta"] == 0.4


def test_aggregate_bounds_and_passk():
    # seed 0: both trials pass; seed 1: one trial fails -> seed fails Pass^2
    eps = [
        _fake_episode(0, 0, 80.0), _fake_episode(0, 1, 90.0),
        _fake_episode(1, 0, 80.0), _fake_episode(1, 1, 30.0),
    ]
    agg = aggregate(eps, k=2)
    assert agg["episodes"] == 4 and agg["seeds"] == 2
    assert agg["pass_1"] == 0.75          # 3 of 4 episodes pass
    assert agg["pass_2"] == 0.5           # only seed 0 passes all trials
    assert 0.0 <= agg["RTTC"] <= 100.0
    assert 0.0 <= agg["eta_mean"] <= 1.0


def test_suite_runs_and_is_deterministic():
    factory = lambda seed, trial: RandomAgent(seed=seed * 1000 + trial, latency=0.5)
    a = aggregate(run_suite(range(0, 4), "easy", factory, trials=2), k=2)
    b = aggregate(run_suite(range(0, 4), "easy", factory, trials=2), k=2)
    assert a == b
    assert 0.0 <= a["RTTC"] <= 100.0


def test_null_baseline_has_low_rttc():
    factory = lambda seed, trial: NullAgent(latency=0.8)
    agg = aggregate(run_suite(range(0, 3), "easy", factory, trials=1), k=1)
    assert agg["RTTC"] == 0.0           # never serves anything -> eta 0, completion 0
