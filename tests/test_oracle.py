"""Greedy-EDF reference + null floor (METHODOLOGY §1).

On the calibrated 'easy' tier the reference should mostly complete instances, give a real
positive ceiling above the null floor, and degrade monotonically as latency is injected.
"""

from kitchenrush import generate
from kitchenrush.oracle import OracleAgent, null_score, reference_score
from kitchenrush.runner import run_episode


def test_null_floor_is_analytic_and_negative():
    spec = generate(0, "easy")
    # null = every order expires; analytic = sum of expiry penalties (all negative)
    assert null_score(spec) < 0


def test_reference_completes_easy_and_beats_null():
    served = total = 0
    for seed in range(6):
        spec = generate(seed, "easy")
        rep = run_episode(spec, OracleAgent(0.0)).report
        served += rep["counters"]["orders_served"]
        total += rep["counters"]["orders_total"]
        assert reference_score(spec, 0.0) > null_score(spec)   # real positive headroom
    assert served / total >= 0.75                              # reference is competent


def test_reference_degrades_with_injected_latency():
    spec = generate(3, "easy")
    fast = reference_score(spec, 0.0)
    slow = reference_score(spec, 4.0)
    assert fast > slow      # more thinking time -> fewer points (the core mechanic)
