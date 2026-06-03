"""Aggregate metrics + the KR headline (METHODOLOGY §1).

KR = 100 * mean clip( (S_model - S_null) / (S_ref - S_null), 0, 1 ) over seeds x trials.
Single realtime headline; raw score, rates, latency percentiles, and Pass^k are diagnostics
(NOT multiplied into the headline). Instances where S_ref <= S_null are degenerate (the
reference can't beat doing nothing) and are excluded from KR + counted, since that signals
mis-calibration rather than model quality.
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
    score = rep["score_raw"]
    s_null = ep.s_null if ep.s_null is not None else 0.0
    s_ref = ep.s_ref if ep.s_ref is not None else 0.0
    degenerate = not (s_ref > s_null)
    kr = None if degenerate else _clamp01((score - s_null) / (s_ref - s_null))
    return {
        "seed": ep.seed,
        "trial": ep.trial,
        "score_raw": score,
        "s_null": s_null,
        "s_ref": s_ref,
        "degenerate": degenerate,
        "kr": kr,                                   # 0..1 normalized; None if degenerate
        "passed": (kr is not None and kr >= config.THETA_PASS),
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
    scored = [m for m in ems if m["kr"] is not None]
    degenerate = n - len(scored)

    def mean(key: str) -> float:
        return sum(m[key] for m in ems) / n

    kr_vals = [m["kr"] for m in scored]
    kr_headline = 100.0 * (sum(kr_vals) / len(kr_vals)) if kr_vals else 0.0
    kr_std = (sum((x - (sum(kr_vals) / len(kr_vals))) ** 2 for x in kr_vals) / len(kr_vals)) ** 0.5 \
        if kr_vals else 0.0

    # Pass^1 over scored episodes; Pass^k = fraction of seeds whose all-k scored trials pass
    pass_1 = (sum(1 for m in scored if m["passed"]) / len(scored)) if scored else 0.0
    by_seed: dict[int, list[bool]] = {}
    for m in scored:
        by_seed.setdefault(m["seed"], []).append(m["passed"])
    pass_k = (sum(1 for v in by_seed.values() if all(v)) / len(by_seed)) if by_seed else 0.0

    thinks = sorted(t for m in ems for t in m["think_gs"])
    return {
        "episodes": n,
        "seeds": len({m["seed"] for m in ems}),
        "degenerate_instances": degenerate,
        "KR": round(kr_headline, 2),               # <-- headline (0-100)
        "kr_std": round(100.0 * kr_std, 2),
        "mean_score_raw": round(mean("score_raw"), 2),
        "completion_rate": round(mean("completion_rate"), 4),
        "expiry_rate": round(mean("expiry_rate"), 4),
        "invalid_rate": round(mean("invalid_rate"), 4),
        "pass_1": round(pass_1, 4),
        f"pass_{k}": round(pass_k, 4),
        "think_gs_p50": round(percentile(thinks, 0.50), 3),
        "think_gs_p95": round(percentile(thinks, 0.95), 3),
    }
