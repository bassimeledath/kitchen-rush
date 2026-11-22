"""Four aligned metrics and kitchen-level bootstrap confidence intervals for Kitchen Rush v3.

Four primary metrics per (model, level) cell over scored episodes:
1. Order Completion:         sum(S) / sum(N)           (higher is better; Full-Clear % shown alongside)
2. Output Tokens / Order:    sum(T_out) / sum(S)       (lower is better; Total Tokens also tracked)
3. Actual USD / Order:       sum(USD) / sum(S)         (lower is better; finite Orders/$1 also shown)
4. Action Failure Rate:      sum(I + B) / sum(A)       (lower is better; invalid + food burns)
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any


def cell_ratios(episodes: list[dict[str, Any]]) -> dict[str, float | None]:
    served = sum(e["served"] for e in episodes)
    requested = sum(e["requested"] for e in episodes)
    clears = sum(1 for e in episodes if e["clear"])
    out_tok = sum(e["output_tokens"] for e in episodes)
    in_tok = sum(e["input_tokens"] for e in episodes)
    cached_tok = sum(e["cached_tokens"] for e in episodes)
    usd = sum(e["cost_usd"] for e in episodes)
    invalid = sum(e["invalid"] for e in episodes)
    burns = sum(e["burns"] for e in episodes)
    actions = sum(e["actions"] for e in episodes)
    turns = sum(e["turns"] for e in episodes)

    return {
        "completion": served / requested if requested else None,
        "full_clear_rate": clears / len(episodes) if episodes else None,
        "out_tok_per_order": out_tok / served if served else None,
        "total_tok_per_order": (in_tok + out_tok) / served if served else None,
        "usd_per_order": usd / served if served else None,
        "orders_per_dollar": served / usd if usd > 0 else None,
        "failure_rate": (invalid + burns) / actions if actions else None,
        "invalid_rate": invalid / actions if actions else None,
        "burn_rate": burns / actions if actions else None,
        "avg_batch_size": actions / turns if turns else None,
        "cache_hit_rate": cached_tok / in_tok if in_tok else 0.0,
    }


def _percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def bootstrap_cell(episodes: list[dict[str, Any]], reps: int = 4000, seed: int = 20261122) -> dict[str, list[float] | None]:
    if len(episodes) < 2:
        return {}
    rng = random.Random(seed)
    keys = ("completion", "out_tok_per_order", "usd_per_order", "orders_per_dollar", "failure_rate")
    draws: dict[str, list[float]] = {k: [] for k in keys}
    n = len(episodes)
    for _ in range(reps):
        sample = rng.choices(episodes, k=n)
        r = cell_ratios(sample)
        for k in keys:
            val = r[k]
            if val is not None and math.isfinite(val):
                draws[k].append(val)
    out: dict[str, list[float] | None] = {}
    for k, vals in draws.items():
        if len(vals) >= 0.95 * reps:
            out[k] = [_percentile(vals, 0.025), _percentile(vals, 0.975)]
        else:
            out[k] = None
    return out


def paired_delta_bootstrap(
    eps_a: list[dict[str, Any]],
    eps_b: list[dict[str, Any]],
    reps: int = 4000,
    seed: int = 20261122,
) -> dict[str, Any]:
    """Paired seed-cluster bootstrap comparing model A minus model B on shared seeds."""
    by_a = {e["seed"]: e for e in eps_a if e.get("scored")}
    by_b = {e["seed"]: e for e in eps_b if e.get("scored")}
    shared = sorted(set(by_a) & set(by_b))
    if not shared:
        return {"paired_seeds": 0}
    pairs = [(by_a[s], by_b[s]) for s in shared]
    ra = cell_ratios([p[0] for p in pairs])
    rb = cell_ratios([p[1] for p in pairs])

    def delta_of(pair_list: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, float | None]:
        r1 = cell_ratios([p[0] for p in pair_list])
        r2 = cell_ratios([p[1] for p in pair_list])
        res = {}
        for k in ("completion", "usd_per_order", "out_tok_per_order", "orders_per_dollar"):
            if r1[k] is not None and r2[k] is not None:
                res[k] = r1[k] - r2[k]
            else:
                res[k] = None
        return res

    point = delta_of(pairs)
    cis: dict[str, list[float] | None] = {}
    if len(pairs) >= 2:
        rng = random.Random(seed)
        draws: dict[str, list[float]] = {k: [] for k in point}
        for _ in range(reps):
            sample = rng.choices(pairs, k=len(pairs))
            d = delta_of(sample)
            for k, v in d.items():
                if v is not None and math.isfinite(v):
                    draws[k].append(v)
        for k, vals in draws.items():
            cis[k] = [_percentile(vals, 0.025), _percentile(vals, 0.975)] if len(vals) >= 0.95 * reps else None

    return {
        "paired_seeds": len(shared),
        "a_served_more": sum(a["served"] > b["served"] for a, b in pairs),
        "b_served_more": sum(b["served"] > a["served"] for a, b in pairs),
        "ties": sum(a["served"] == b["served"] for a, b in pairs),
        "point_delta": point,
        "ci95": cis,
        "a_ratios": ra,
        "b_ratios": rb,
    }


def summarize_campaign(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    by_cell: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for ep in episodes:
        by_cell[(ep["model"], ep["level"])].append(ep)

    rows = []
    for (model, level), group in sorted(by_cell.items(), key=lambda x: (x[0][1], x[0][0])):
        scored = [e for e in group if e.get("scored")]
        unscored = len(group) - len(scored)
        r = cell_ratios(scored)
        ci = bootstrap_cell(scored) if scored else {}
        rows.append({
            "model": model,
            "level": level,
            "scored": len(scored),
            "unscored": unscored,
            "clears": sum(1 for e in scored if e["clear"]),
            "served": sum(e["served"] for e in scored),
            "requested": sum(e["requested"] for e in scored),
            "total_usd": round(sum(e["cost_usd"] for e in group), 6),
            "ratios": r,
            "ci95": ci,
        })
    return {
        "rows": rows,
        "total_episodes": len(episodes),
        "total_scored": sum(1 for e in episodes if e.get("scored")),
        "total_usd": round(sum(e["cost_usd"] for e in episodes), 6),
    }


def format_markdown_table(summary: dict[str, Any]) -> str:
    def pct(v: float | None) -> str:
        return f"{100 * v:.1f}%" if v is not None else "—"

    def tok(v: float | None) -> str:
        return f"{v:,.0f}" if v is not None else "—"

    def usd(v: float | None) -> str:
        return f"${v:.4f}" if v is not None else "—"

    def opd(v: float | None) -> str:
        return f"{v:,.1f}" if v is not None else "—"

    lines = [
        "| D | Model | Scored | Clears | Order Completion | Out Tok / Order | Actual USD / Order | Orders / $1 | Fail Rate (Inv+Burn) | Avg Batch | Cache % | Cell Spend |",
        "| :-: | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["rows"]:
        r = row["ratios"]
        batch_str = f"{r['avg_batch_size']:.2f}" if r["avg_batch_size"] is not None else "—"
        lines.append(
            f"| D={row['level']} | {row['model']} | {row['scored']}/{row['scored'] + row['unscored']} | "
            f"{row['clears']}/{row['scored']} | "
            f"{row['served']}/{row['requested']} ({pct(r['completion'])}) | "
            f"{tok(r['out_tok_per_order'])} | "
            f"{usd(r['usd_per_order'])} | "
            f"{opd(r['orders_per_dollar'])} | "
            f"{pct(r['failure_rate'])} | "
            f"{batch_str} | "
            f"{pct(r['cache_hit_rate'])} | "
            f"${row['total_usd']:.4f} |"
        )
    lines.append("")
    lines.append(f"**Total campaign spend:** `${summary['total_usd']:.4f}` across `{summary['total_episodes']}` episodes (`{summary['total_scored']}` scored).")
    return "\n".join(lines)
