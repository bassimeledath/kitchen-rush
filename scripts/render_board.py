"""Render a Kitchen Rush sweep into a readable leaderboard + latency-tax view.

    python scripts/render_board.py --name starter

Reads runs/<name>/leaderboard.json (per model×B×tier aggregates) and runs/<name>/episodes.jsonl
(per-episode cost/tokens), prints a markdown board, and writes runs/<name>/BOARD.md.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    args = ap.parse_args()
    base = Path('runs') / args.name
    lb = json.loads((base / 'leaderboard.json').read_text())
    eps = [json.loads(l) for l in (base / 'episodes.jsonl').open()]

    # per-model rollups from episodes
    cost = defaultdict(float); reason = defaultdict(int); turns = defaultdict(list)
    served = defaultdict(int); total = defaultdict(int); ne = defaultdict(int)
    for e in eps:
        m = e['model']
        cost[m] += e['ep_cost']; reason[m] += e['ep_reason']; turns[m].append(e['turns'])
        served[m] += e['served']; total[m] += e['total']; ne[m] += 1

    # KR pivot: model -> (tier,B) -> KR
    kr = defaultdict(dict)
    for r in lb['board']:
        kr[r['model']][(r['tier'], r['B'])] = r['KR']

    tiers = sorted({r['tier'] for r in lb['board']})
    budgets = sorted({r['B'] for r in lb['board']})
    cols = [(t, b) for t in tiers for b in budgets]

    def overall(m):
        vals = [kr[m].get(c) for c in cols if kr[m].get(c) is not None]
        return sum(vals) / len(vals) if vals else -1.0

    def lat_tax(m):
        hi = [kr[m].get((t, max(budgets))) for t in tiers if kr[m].get((t, max(budgets))) is not None]
        lo = [kr[m].get((t, min(budgets))) for t in tiers if kr[m].get((t, min(budgets))) is not None]
        if not hi or not lo:
            return None
        return sum(hi) / len(hi) - sum(lo) / len(lo)

    models = sorted(kr, key=overall, reverse=True)

    hdr = "| # | model | " + " | ".join(f"{t[:3]} B{int(b)}" for t, b in cols) + \
          " | KR̄ | Δlat | serve% | reason/ep | $ |"
    sep = "|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|"
    lines = [
        f"# Kitchen Rush — starter leaderboard ({args.name})",
        "",
        f"Ruleset `{lb['ruleset']}` (gen {lb['versions'].get('ruleset_version','?')}, "
        f"frozen={lb['versions'].get('frozen')}) · tokenizer `{lb['versions'].get('tokenizer')}` · "
        f"track RP (experimental β) · {sum(ne.values())} episodes · total ${lb['total_cost_usd']}"
        + ("  · **BUDGET-CAPPED (partial)**" if lb.get('halted_on_budget') else ""),
        "",
        "KR = 100·clip((S−S_null)/(S_ref−S_null)). `KR̄` = mean over tier×budget. "
        "`Δlat` = mean KR at the loosest budget − tightest (latency head-room). "
        "`·think` = reasoning on.",
        "",
        hdr, sep,
    ]
    for i, m in enumerate(models, 1):
        cells = []
        for c in cols:
            v = kr[m].get(c)
            cells.append("—" if v is None else f"{v:.0f}")
        tax = lat_tax(m)
        spct = 100.0 * served[m] / total[m] if total[m] else 0.0
        lines.append(
            f"| {i} | {m} | " + " | ".join(cells) +
            f" | **{overall(m):.1f}** | {'—' if tax is None else f'{tax:+.0f}'} | "
            f"{spct:.0f}% | {reason[m]//max(ne[m],1)} | {cost[m]:.2f} |")

    md = "\n".join(lines) + "\n"
    (base / 'BOARD.md').write_text(md)
    print(md)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
