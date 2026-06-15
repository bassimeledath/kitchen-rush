"""KR-INT time-agnostic track: deadlines don't bind, rungs are oracle-feasible, scoring works,
and adding the K-ladder did NOT disturb the frozen gen-1.0 ruleset hash."""

from kitchenrush import config, kr_int
from kitchenrush.oracle import OracleAgent
from kitchenrush.runner import run_episode
from kitchenrush.version import FROZEN_RULESET_HASH, ruleset_hash


def test_kr_ladder_does_not_change_frozen_ruleset():
    # The K-ladder lives outside config.TIERS, so the frozen gen-1.0 hash must be unchanged.
    assert ruleset_hash() == FROZEN_RULESET_HASH


def test_relaxed_deadlines_never_bind():
    for k in range(kr_int.N_RUNGS):
        spec = kr_int.generate(0, k)
        assert spec.orders, f"K{k} produced no orders"
        # deadlines are pushed far beyond any real play horizon
        assert all(o.deadline_gs >= 1_000_000.0 for o in spec.orders)


def test_named_tier_path_unaffected_by_relaxation():
    # The frozen named-tier generator must still produce binding deadlines (regression guard).
    from kitchenrush import procgen
    spec = procgen.generate(0, "easy")
    assert all(o.deadline_gs < 1_000_000.0 for o in spec.orders)


def test_kr_int_oracle_solves_low_and_high_rung():
    old = config.LATENCY_SCALE
    config.LATENCY_SCALE = 0.0
    try:
        for k in (0, 3, 5):
            comps = [kr_int.completion(run_episode(kr_int.generate(s, k), OracleAgent(0.5)).report)
                     for s in range(4)]
            assert all(c >= 0.999 for c in comps), f"K{k} not fully oracle-solvable: {comps}"
    finally:
        config.LATENCY_SCALE = old


def test_kr_int_deterministic():
    assert kr_int.generate(3, 4) == kr_int.generate(3, 4)
    assert kr_int.generate(0, 5) != kr_int.generate(1, 5)


def test_summarize_k50_interpolates_and_auc():
    # mean completion: K0=1, K1=1, K2=0.8, K3=0.2, K4=0, K5=0 -> crosses 0.5 between K2 and K3.
    per_k = {0: [1.0], 1: [1.0], 2: [0.8], 3: [0.2], 4: [0.0], 5: [0.0]}
    s = kr_int.summarize(per_k, theta=0.5)
    # crossing: 2 + (0.8-0.5)/(0.8-0.2) = 2 + 0.5 = 2.5
    assert abs(s["k50"] - 2.5) < 1e-6
    assert abs(s["auc"] - (1 + 1 + 0.8 + 0.2) / 6) < 1e-6


def test_summarize_floor_and_ceiling():
    assert kr_int.summarize({0: [0.0], 1: [0.0]})["k50"] == -1.0      # fails from the start
    assert kr_int.summarize({0: [1.0], 1: [1.0]})["k50"] == 1.0       # clears every rung
