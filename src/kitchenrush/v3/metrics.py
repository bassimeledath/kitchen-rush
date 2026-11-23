"""Aligned metrics and kitchen-level bootstrap confidence intervals for Kitchen Rush v3.

Primary metrics per (model, level) cell over scored episodes:
1. Full Clears & Order Completion:
   - Full Clear Rate:            clears / scored             (primary pass/fail)
   - Order Completion:           sum(S) / sum(N)             (partial credit)
2. Output Tokens / Cleared Ord:  sum(T_out all scored) / sum(N in cleared kitchens)  ("no clear" if clears == 0)
3. Actual USD / Cleared Ord:     sum(USD all scored)   / sum(N in cleared kitchens)  ("no clear" if clears == 0)
   (Secondary per-individually-served-order ratios are preserved alongside.)
4. Reliability:
   - Inv+Burn / 100 Acts:        100 * sum(I + B) / sum(A)
   - Empty Turns & Truncated Turns reported explicitly.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any


def _episode_empty_turns(ep: dict[str, Any]) -> int:
    if "empty_turns" in ep:
        return int(ep["empty_turns"])
    return sum(1 for t in ep.get("turn_logs", []) if not t.get("calls"))


def _episode_truncated_turns(ep: dict[str, Any]) -> int:
    if "truncated_turns" in ep:
        return int(ep["truncated_turns"])
    return sum(1 for t in ep.get("turn_logs", []) if t.get("finish_reason") == "length")


def cell_ratios(episodes: list[dict[str, Any]]) -> dict[str, float | None]:
    served = sum(e["served"] for e in episodes)
    requested = sum(e["requested"] for e in episodes)
    clears = sum(1 for e in episodes if e["clear"])
    cleared_orders = sum(e["requested"] for e in episodes if e["clear"])
    out_tok = sum(e["output_tokens"] for e in episodes)
    in_tok = sum(e["input_tokens"] for e in episodes)
    cached_tok = sum(e["cached_tokens"] for e in episodes)
    usd = sum(e["cost_usd"] for e in episodes)
    invalid = sum(e["invalid"] for e in episodes)
    burns = sum(e["burns"] for e in episodes)
    actions = sum(e["actions"] for e in episodes)
    turns = sum(e["turns"] for e in episodes)
    empty_turns = sum(_episode_empty_turns(e) for e in episodes)
    trunc_turns = sum(_episode_truncated_turns(e) for e in episodes)

    return {
        "completion": served / requested if requested else None,
        "full_clear_rate": clears / len(episodes) if episodes else None,
        "out_tok_per_cleared_order": out_tok / cleared_orders if cleared_orders else None,
        "usd_per_cleared_order": usd / cleared_orders if cleared_orders else None,
        "cleared_orders_per_dollar": cleared_orders / usd if (usd > 0 and cleared_orders > 0) else 0.0 if usd > 0 else None,
        "out_tok_per_order": out_tok / served if served else None,
        "total_tok_per_order": (in_tok + out_tok) / served if served else None,
        "usd_per_order": usd / served if served else None,
        "orders_per_dollar": served / usd if usd > 0 else None,
        "failure_rate": (invalid + burns) / actions if actions else None,
        "invalid_rate": invalid / actions if actions else None,
        "burn_rate": burns / actions if actions else None,
        "empty_turn_rate": empty_turns / turns if turns else 0.0,
        "truncated_turn_rate": trunc_turns / turns if turns else 0.0,
        "avg_batch_size": actions / turns if turns else None,
        "cache_hit_rate": cached_tok / in_tok if in_tok else 0.0,
    }


def _percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def wilson_ci95(k: int, n: int, z: float = 1.959963984540054) -> list[float] | None:
    """Two-sided 95% Wilson score interval for a binomial proportion k/n (valid at k=0 and k=n)."""
    if n <= 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return [max(0.0, center - half), min(1.0, center + half)]


def bootstrap_cell(episodes: list[dict[str, Any]], reps: int = 4000, seed: int = 20261122) -> dict[str, list[float] | None]:
    if not episodes:
        return {}
    n = len(episodes)
    clears = sum(1 for e in episodes if e["clear"])
    out: dict[str, list[float] | None] = {
        "full_clear_rate": wilson_ci95(clears, n),
    }
    if n < 2:
        return out
    rng = random.Random(seed)
    keys = (
        "completion",
        "out_tok_per_cleared_order",
        "usd_per_cleared_order",
        "out_tok_per_order",
        "usd_per_order",
        "failure_rate",
    )
    draws: dict[str, list[float]] = {k: [] for k in keys}
    for _ in range(reps):
        sample = rng.choices(episodes, k=n)
        r = cell_ratios(sample)
        for k in keys:
            val = r[k]
            if val is not None and math.isfinite(val):
                draws[k].append(val)
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
        for k in ("full_clear_rate", "completion", "usd_per_cleared_order", "out_tok_per_cleared_order", "usd_per_order"):
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
        "a_cleared_more": sum(int(a["clear"]) > int(b["clear"]) for a, b in pairs),
        "b_cleared_more": sum(int(b["clear"]) > int(a["clear"]) for a, b in pairs),
        "a_served_more": sum(a["served"] > b["served"] for a, b in pairs),
        "b_served_more": sum(b["served"] > a["served"] for a, b in pairs),
        "ties": sum(a["served"] == b["served"] for a, b in pairs),
        "point_delta": point,
        "ci95": cis,
        "a_ratios": ra,
        "b_ratios": rb,
    }


def _episode_cost_sources(ep: dict[str, Any]) -> tuple[int, int]:
    if "provider_cost_turns" in ep and "estimated_cost_turns" in ep:
        return int(ep["provider_cost_turns"]), int(ep["estimated_cost_turns"])
    prov = sum(1 for t in ep.get("turn_logs", []) if t.get("cost_source", "provider") == "provider")
    est = sum(1 for t in ep.get("turn_logs", []) if t.get("cost_source") == "estimated")
    return prov, est


def summarize_campaign(
    episodes: list[dict[str, Any]],
    *,
    planned_by_cell: dict[tuple[str, int], int] | None = None,
) -> dict[str, Any]:
    by_cell: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for ep in episodes:
        by_cell[(ep["model"], ep["level"])].append(ep)
    if planned_by_cell:
        for cell_key in planned_by_cell:
            by_cell.setdefault(cell_key, [])

    rows = []
    for (model, level), group in sorted(by_cell.items(), key=lambda x: (x[0][1], x[0][0])):
        scored = [e for e in group if e.get("scored")]
        unscored = len(group) - len(scored)
        planned = planned_by_cell.get((model, level), len(group)) if planned_by_cell else len(group)
        missing = max(0, planned - len(group))
        r = cell_ratios(scored)
        ci = bootstrap_cell(scored) if scored else {}
        rows.append({
            "model": model,
            "level": level,
            "planned": planned,
            "scored": len(scored),
            "unscored": unscored,
            "missing": missing,
            "clears": sum(1 for e in scored if e["clear"]),
            "cleared_orders": sum(e["requested"] for e in scored if e["clear"]),
            "served": sum(e["served"] for e in scored),
            "requested": sum(e["requested"] for e in scored),
            "invalid": sum(e["invalid"] for e in scored),
            "burns": sum(e["burns"] for e in scored),
            "empty_turns": sum(_episode_empty_turns(e) for e in scored),
            "truncated_turns": sum(_episode_truncated_turns(e) for e in scored),
            "turns": sum(e["turns"] for e in scored),
            "total_usd": round(sum(e["cost_usd"] for e in group), 6),
            "ratios": r,
            "ci95": ci,
        })

    prov_turns = sum(_episode_cost_sources(e)[0] for e in episodes)
    est_turns = sum(_episode_cost_sources(e)[1] for e in episodes)
    total_planned = sum(planned_by_cell.values()) if planned_by_cell else len(episodes)
    total_missing = max(0, total_planned - len(episodes))
    cap_diag = {
        "truncated_turns": sum(_episode_truncated_turns(e) for e in episodes),
        "empty_turns": sum(_episode_empty_turns(e) for e in episodes),
        "estimated_cost_turns": est_turns,
        "max_decisions_eps": sum(1 for e in episodes if e.get("termination_reason") == "max_decisions"),
        "max_turns_eps": sum(1 for e in episodes if e.get("termination_reason") == "max_turns"),
        "stalled_eps": sum(1 for e in episodes if e.get("termination_reason") == "stalled"),
        "stopped_cap_eps": sum(1 for e in episodes if e.get("status") == "stopped_cap"),
        "provider_error_eps": sum(1 for e in episodes if e.get("status") == "provider_error"),
        "missing_planned_eps": total_missing,
    }
    return {
        "rows": rows,
        "total_planned": total_planned,
        "total_episodes": len(episodes),
        "total_scored": sum(1 for e in episodes if e.get("scored")),
        "total_missing": total_missing,
        "provider_cost_turns": prov_turns,
        "estimated_cost_turns": est_turns,
        "total_usd": round(sum(e["cost_usd"] for e in episodes), 6),
        "cap_diagnostics": cap_diag,
    }


def format_markdown_table(summary: dict[str, Any]) -> str:
    def pct(v: float | None) -> str:
        return f"{100 * v:.1f}%" if v is not None else "—"

    def tok(v: float | None, empty: str = "no clear") -> str:
        return f"{v:,.0f}" if v is not None else empty

    def usd(v: float | None, empty: str = "no clear") -> str:
        return f"${v:.4f}" if v is not None else empty

    lines = [
        "| D | Model | Clears | Order Completion | Out Tok / Cleared Ord | USD / Cleared Ord | USD / Served Ord | Inv+Burn / 100 Acts | Empty / Trunc Turns | Avg Batch | Cache % | Spend |",
        "| :-: | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["rows"]:
        r = row["ratios"]
        batch_str = f"{r['avg_batch_size']:.2f}" if r["avg_batch_size"] is not None else "—"
        ib100 = f"{100 * r['failure_rate']:.1f}" if r["failure_rate"] is not None else "—"
        lines.append(
            f"| D={row['level']} | {row['model']} | "
            f"{row['clears']}/{row['scored']} | "
            f"{row['served']}/{row['requested']} ({pct(r['completion'])}) | "
            f"{tok(r['out_tok_per_cleared_order'])} | "
            f"{usd(r['usd_per_cleared_order'])} | "
            f"{usd(r['usd_per_order'], '—')} | "
            f"{ib100} ({row['invalid']}i+{row['burns']}b) | "
            f"{row['empty_turns']}/{row['truncated_turns']} | "
            f"{batch_str} | "
            f"{pct(r['cache_hit_rate'])} | "
            f"${row['total_usd']:.4f} |"
        )
    lines.append("")
    planned_tot = summary.get("total_planned", summary["total_episodes"])
    missing_tot = summary.get("total_missing", 0)
    prov_t = summary.get("provider_cost_turns", 0)
    est_t = summary.get("estimated_cost_turns", 0)
    lines.append(
        f"**Episodes:** `{summary['total_scored']}` scored / `{planned_tot}` planned (`{missing_tot}` missing) | "
        f"**Episode spend:** `${summary['total_usd']:.4f}` (provider-billed turns: `{prov_t}`, estimated fallback: `{est_t}`)"
    )
    cd = summary.get("cap_diagnostics") or {}
    hits = [f"{k}={v}" for k, v in cd.items() if v]
    if hits:
        lines.append(f"**Cap & Reliability Alerts:** `{', '.join(hits)}`")
    else:
        lines.append("**Cap & Reliability Alerts:** `NONE (0 truncations, 0 empty turns, 0 action/turn/spend cap hits)`")
    return "\n".join(lines)
