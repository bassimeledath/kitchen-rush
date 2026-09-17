"""Balanced episode aggregation; uncertainty resamples kitchens, not individual trials."""

from __future__ import annotations

import random
import statistics
from collections import defaultdict


def summarize(episodes):
    groups = defaultdict(list)
    for ep in episodes:
        if ep["status"] != "complete" or not ep.get("replay_verified"):
            raise ValueError("quarantined/unverified episodes cannot enter results")
        key = (
            ep["model"]["key"],
            "tokens" if ep["token_cap"] is not None else "USD",
            ep["token_cap"] if ep["token_cap"] is not None else ep["dollar_cap"],
        )
        groups[key].append(ep)
    rows = []
    for (model, kind, budget), group in sorted(groups.items()):
        by_seed = defaultdict(list)
        for ep in group:
            by_seed[ep["spec"]["seed"]].append(ep["quality"])
        # Repeated trials are nested within seed; all configured families have equal seed counts.
        means = [statistics.mean(v) for v in by_seed.values()]
        ci = None
        if len(means) >= 3:
            rng = random.Random(20260916)
            draws = sorted(statistics.mean(rng.choices(means, k=len(means))) for _ in range(4000))
            ci = [draws[99], draws[3899]]
        rows.append(
            {
                "model": model,
                "budget_kind": kind,
                "budget": budget,
                "quality": statistics.mean(means),
                "ci95": ci,
                "seeds": len(means),
                "episodes": len(group),
                "mean_cost_usd": statistics.mean(ep["cost_usd"] for ep in group),
                "mean_completion": statistics.mean(
                    ep["completed"] / len(ep["spec"]["orders"]) for ep in group
                ),
                "mean_invalid": statistics.mean(ep["invalid"] for ep in group),
                "mean_output_tokens": statistics.mean(ep["output_tokens"] for ep in group),
                "dollar_censored": sum(
                    ep["reason"] == "cannot_reserve_next_request" for ep in group
                )
                if kind == "tokens"
                else 0,
            }
        )
    b80 = {}
    for model in {ep["model"]["key"] for ep in episodes}:
        rows_m = [r for r in rows if r["model"] == model and r["budget_kind"] == "tokens"]
        if not rows_m:
            b80[model] = {"status": "not tested / ineligible", "mean": None, "conservative": None}
            continue
        usable = [r for r in rows_m if r["dollar_censored"] == 0]
        mean = next(
            (r["budget"] for r in sorted(usable, key=lambda r: r["budget"]) if r["quality"] >= 80),
            None,
        )
        conservative = next(
            (
                r["budget"]
                for r in sorted(usable, key=lambda r: r["budget"])
                if r["ci95"] is not None and r["ci95"][0] >= 80
            ),
            None,
        )
        b80[model] = {
            "status": "reached" if mean else "not reached within uncensored tested budgets",
            "mean": mean,
            "conservative": conservative,
        }
    return {
        "rows": rows,
        "B80": b80,
        "uncertainty": "95% seed-cluster percentile bootstrap, 4000 draws. "
        "Pilot intervals are exploratory; no stable full-roster rank is claimed.",
    }
