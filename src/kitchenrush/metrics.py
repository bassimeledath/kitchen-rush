"""Aggregate metrics, Pass^k reliability, and the RTTC headline (SCORING §6).

Normalization uses an instant-serve upper bound ``S_ref`` (sum of base values) as a
documented placeholder for the deferred greedy-EDF oracle ``S*``; ``eta = clamp(S_raw/S_ref,
0, 1)``. RTTC = 100 · eta · sqrt(completion) · sqrt(1 − invalid_rate) · sqrt(Pass^k).
"""

from __future__ import annotations

import math
from typing import Any

from . import config
from .report import EpisodeResult


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def episode_metrics(ep: EpisodeResult) -> dict[str, Any]:
    rep = ep.report
    c = rep["counters"]
    total = max(1, c["orders_total"])
    calls = max(1, c["total_tool_calls"])
    s_ref = ep.s_ref or 0.0
    score = rep["score_raw"]
    return {
        "seed": ep.seed,
        "trial": ep.trial,
        "score_raw": score,
        "eta": _clamp01(score / s_ref) if s_ref > 0 else 0.0,
        "passed": (score >= config.THETA_PASS * s_ref) if s_ref > 0 else False,
        "completion_rate": c["orders_served"] / total,
        "expiry_rate": c["orders_expired"] / total,
        "invalid_rate": c["invalid_actions"] / calls,
        "burns": c["burns"],
        "think_gs": [s["think_gs"] for s in ep.steps],
    }


def aggregate(episodes: list[EpisodeResult], *, k: int = config.PASS_K) -> dict[str, Any]:
    if not episodes:
        return {"episodes": 0}
    ems = [episode_metrics(e) for e in episodes]
    n = len(ems)

    def mean(key: str) -> float:
        return sum(m[key] for m in ems) / n

    scores = [m["score_raw"] for m in ems]
    s_mean = sum(scores) / n
    s_std = (sum((x - s_mean) ** 2 for x in scores) / n) ** 0.5
    cv = s_std / abs(s_mean) if s_mean != 0 else 0.0

    completion = mean("completion_rate")
    invalid = mean("invalid_rate")
    eta = mean("eta")
    pass_1 = sum(1 for m in ems if m["passed"]) / n

    by_seed: dict[int, list[bool]] = {}
    for m in ems:
        by_seed.setdefault(m["seed"], []).append(m["passed"])
    pass_k = (sum(1 for v in by_seed.values() if all(v)) / len(by_seed)) if by_seed else 0.0

    thinks = sorted(t for m in ems for t in m["think_gs"])
    rttc = 100.0 * _clamp01(eta) * math.sqrt(_clamp01(completion)) \
        * math.sqrt(_clamp01(1 - invalid)) * math.sqrt(_clamp01(pass_k))

    return {
        "episodes": n,
        "seeds": len(by_seed),
        "trials_per_seed": k,
        "mean_score": round(s_mean, 3),
        "score_std": round(s_std, 3),
        "cv": round(cv, 4),
        "eta_mean": round(eta, 4),
        "completion_rate": round(completion, 4),
        "expiry_rate": round(mean("expiry_rate"), 4),
        "invalid_rate": round(invalid, 4),
        "pass_1": round(pass_1, 4),
        f"pass_{k}": round(pass_k, 4),
        "think_gs_p50": round(percentile(thinks, 0.50), 3),
        "think_gs_p95": round(percentile(thinks, 0.95), 3),
        "RTTC": round(rttc, 2),
    }
