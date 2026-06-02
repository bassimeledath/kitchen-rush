"""Determinism contract (RULES §11): same (seed, tier) -> identical spec; same
(spec, policy, latency trace) -> identical trajectory, score, and counters."""

from kitchenrush import procgen
from kitchenrush.baselines import NullAgent, RandomAgent
from kitchenrush.runner import run_episode


def test_spec_is_deterministic():
    assert procgen.generate(7, "medium") == procgen.generate(7, "medium")
    assert procgen.generate(0, "easy") != procgen.generate(1, "easy")


def test_run_deterministic_null():
    r1 = run_episode(procgen.generate(3, "easy"), NullAgent(latency=0.8))
    r2 = run_episode(procgen.generate(3, "easy"), NullAgent(latency=0.8))
    assert r1.report == r2.report


def test_run_deterministic_random():
    r1 = run_episode(procgen.generate(5, "easy"), RandomAgent(seed=11, latency=0.4))
    r2 = run_episode(procgen.generate(5, "easy"), RandomAgent(seed=11, latency=0.4))
    assert r1.report == r2.report
    assert [s["last_turn"] for s in r1.steps] == [s["last_turn"] for s in r2.steps]
