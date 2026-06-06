"""Render a Kitchen Rush sweep into a readable leaderboard + latency-tax view.

    python scripts/render_board.py --name starter

Computes everything from runs/<name>/episodes.jsonl — the per-episode durable log (flushed as the
sweep runs and resume-safe), so the board is correct even after a crash/resume and never depends
on the end-of-run summary file. Prints a markdown board and writes runs/<name>/BOARD.md.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from kitchenrush.version import versions  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    args = ap.parse_args()
    base = Path('runs') / args.name
    eps = [json.loads(l) for l in (base / 'episodes.jsonl').open() if l.strip()]
    if not eps:
        print('no episodes yet'); return 1

    # per-model rollups
    cost = defaultdict(float); reason = defaultdict(int)
    served = defaultdict(int); total = defaultdict(int); ne = defaultdict(int)
    # per (model, B, tier) KR samples + completion
    krs = defaultdict(list); compl = defaultdict(list)
    for e in eps:
        m = e['model']
        cost[m] += e['ep_cost']; reason[m] += e['ep_reason']; ne[m] += 1
        served[m] += e['served']; total[m] += e['total']
        key = (m, e['B'], e['tier'])
        if e['kr'] is not None:                       # skip degenerate seeds (s_ref<=s_null)
            krs[key].append(e['kr'])
        compl[key].append(1.0 if e['served'] == e['total'] else 0.0)

    def mean(xs):
        return sum(xs) / len(xs) if xs else None

    kr = {k: mean(v) for k, v in krs.items()}
    tiers = sorted({e['tier'] for e in eps})
    budgets = sorted({e['B'] for e in eps})
    cols = [(t, b) for t in tiers for b in budgets]
    models = sorted({e['model'] for e in eps})

    def overall(m):
        vals = [kr.get((m, b, t)) for t, b in cols if kr.get((m, b, t)) is not None]
        return mean(vals) if vals else -1.0

    def lat_tax(m):
        hi = [kr.get((m, max(budgets), t)) for t in tiers if kr.get((m, max(budgets), t)) is not None]
        lo = [kr.get((m, min(budgets), t)) for t in tiers if kr.get((m, min(budgets), t)) is not None]
        return (mean(hi) - mean(lo)) if hi and lo else None

    models.sort(key=overall, reverse=True)
    meta_path = base / 'run_meta.json'           # accurate tokenizer/ruleset from the sweep's env
    v = json.loads(meta_path.read_text())['versions'] if meta_path.exists() else versions()

    hdr = "| # | model | " + " | ".join(f"{t[:3]} B{int(b)}" for t, b in cols) + \
          " | KR̄ | Δlat | serve% | reason/ep | $ |"
    sep = "|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|"
    lines = [
        f"# Kitchen Rush — starter leaderboard ({args.name})",
        "",
        f"Ruleset `{v['ruleset']}` (gen {v.get('ruleset_version')}, frozen={v.get('frozen')}) · "
        f"tokenizer `{v.get('tokenizer')}` · track RP (experimental β) · "
        f"{len(eps)} episodes · total ${sum(cost.values()):.2f}",
        "",
        "KR = 100·clip((S−S_null)/(S_ref−S_null)), mean over seeds. `KR̄` = mean over tier×budget. "
        "`Δlat` = mean KR at the loosest budget − tightest (latency head-room). `·think` = reasoning on.",
        "",
        hdr, sep,
    ]
    for i, m in enumerate(models, 1):
        cells = []
        for t, b in cols:
            val = kr.get((m, b, t))
            cells.append("—" if val is None else f"{val:.0f}")
        tax = lat_tax(m)
        spct = 100.0 * served[m] / total[m] if total[m] else 0.0
        lines.append(
            f"| {i} | {m} | " + " | ".join(cells) +
            f" | **{overall(m):.1f}** | {'—' if tax is None else f'{tax:+.0f}'} | "
            f"{spct:.0f}% | {reason[m] // max(ne[m], 1)} | {cost[m]:.2f} |")

    md = "\n".join(lines) + "\n"
    (base / 'BOARD.md').write_text(md)
    print(md)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
