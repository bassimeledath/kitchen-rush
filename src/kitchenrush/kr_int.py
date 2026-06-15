"""KR-INT — the time-agnostic intelligence track (docs: LIMITATIONS / OBJECTIONS discussion).

Speed is removed two ways: latency costs no game-time (set ``config.LATENCY_SCALE = 0``) and order
deadlines never bind (``relax_deadlines=True``). What remains is **planning correctness in a dynamic
kitchen**: intrinsic cook/burn timers stay on, so a model must still *sequence* its actions so food
doesn't burn and plates match — but it is never punished for deliberating. Because deadlines don't
bind, the *sequential* greedy-EDF oracle solves every rung (it isn't racing a clock), so each rung is
oracle-feasible by construction and the score reduces to a clean completion fraction (no S_ref/S_null
normalization needed).

Difficulty is scaled by a complexity ladder K0..K5 expressed in action/state units (menu size +
recipe depth, order volume, concurrency/working-set via arrival pacing, burner contention held at 2,
and — at high K — no ready-action hints). The ladder lives here, NOT in ``config.TIERS``, so the
frozen gen-1.0 ruleset hash is untouched.
"""
from __future__ import annotations

from . import config, procgen

# K0..K5. Difficulty rises across several axes at once; burners held at 2 so contention is the
# thing that breaks a planner as volume/concurrency climb. Recipe depth (3..9 steps) enters via the
# menu: soup(3) < burger(4) < salad(5) < mushroom_cheeseburger(8) < veggie_ramen(9). horizon_gs here
# only paces arrivals (it's generous); the real episode horizon is set by the relaxed deadlines.
K_LADDER: list[config.Tier] = [
    config.Tier("K0", grid_n=7, burner_count=2, horizon_gs=2000.0,
                recipes=("soup",), arrival_rate=1/60, slack=1.0,
                show_ready_actions=True, max_orders=2),
    config.Tier("K1", grid_n=7, burner_count=2, horizon_gs=2000.0,
                recipes=("soup", "burger"), arrival_rate=1/45, slack=1.0,
                show_ready_actions=True, max_orders=3),
    config.Tier("K2", grid_n=7, burner_count=2, horizon_gs=2000.0,
                recipes=("burger", "salad"), arrival_rate=1/30, slack=1.0,
                show_ready_actions=True, max_orders=4),
    config.Tier("K3", grid_n=7, burner_count=2, horizon_gs=2000.0,
                recipes=("burger", "salad", "soup"), arrival_rate=1/22, slack=1.0,
                show_ready_actions=False, max_orders=6),
    config.Tier("K4", grid_n=7, burner_count=2, horizon_gs=2000.0,
                recipes=("burger", "salad", "mushroom_cheeseburger"), arrival_rate=1/15, slack=1.0,
                show_ready_actions=False, max_orders=8),
    config.Tier("K5", grid_n=9, burner_count=2, horizon_gs=2000.0,
                recipes=tuple(config.RECIPES.keys()), arrival_rate=1/10, slack=1.0,
                show_ready_actions=False, max_orders=10),
]
N_RUNGS = len(K_LADDER)


def generate(seed: int, k: int):
    """Generate a time-agnostic instance at complexity rung ``k`` (0..5)."""
    if not 0 <= k < N_RUNGS:
        raise ValueError(f"k must be in 0..{N_RUNGS - 1}")
    return procgen.generate_from_tier(seed, K_LADDER[k], b=1.0, relax_deadlines=True)


def completion(report: dict) -> float:
    """Per-instance KR-INT score: fraction of orders served correctly (0..1)."""
    c = report["counters"]
    tot = c["orders_total"]
    return c["orders_served"] / tot if tot else 0.0


def summarize(per_k: dict[int, list[float]], theta: float = 0.5) -> dict:
    """Roll per-rung completion samples into the KR-INT headline.

    Returns mean completion per rung, full-pass rate per rung, the area under the completion-vs-K
    curve (AUC, normalized 0..1), and K50 — the highest rung still solved at >= ``theta`` mean
    completion, linearly interpolated between rungs for a continuous ceiling.
    """
    rungs = sorted(per_k)
    mean = {k: (sum(v) / len(v) if v else 0.0) for k, v in per_k.items()}
    full = {k: (sum(1 for x in v if x >= 0.999) / len(v) if v else 0.0) for k, v in per_k.items()}
    auc = sum(mean[k] for k in rungs) / len(rungs) if rungs else 0.0

    # K50: walk up; find the crossing of theta and interpolate.
    k50 = -1.0
    for i, k in enumerate(rungs):
        if mean[k] >= theta:
            k50 = float(k)
        else:
            if i > 0 and mean[rungs[i - 1]] >= theta:
                prev = rungs[i - 1]
                frac = (mean[prev] - theta) / (mean[prev] - mean[k]) if mean[prev] != mean[k] else 0.0
                k50 = prev + frac * (k - prev)
            break
    return {"k50": round(k50, 2), "auc": round(auc, 4),
            "mean_completion": {k: round(mean[k], 3) for k in rungs},
            "full_pass_rate": {k: round(full[k], 3) for k in rungs}}
