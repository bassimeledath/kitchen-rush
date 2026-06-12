"""Build the combined current board from the frozen starter run + patch runs.

    .venv/bin/python scripts/build_board.py

Merges board cells from leaderboard/results/starter.json with each patch run's
runs/<name>/leaderboard.json, writes leaderboard/results/board.{json,md}, and leaves
starter.* untouched as the frozen first-sweep artifact. Per-model CI/serve%/$ for patch
models are recomputed from their episodes.jsonl (same seed-block bootstrap as
render_board.py); starter rows reuse the CIs already published in starter.md.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "leaderboard" / "results"

PATCH_RUNS = ["openai_patch", "anthropic_patch", "openrouter_patch"]
# rows that must not appear on the published board, and why (kept in the json for audit)
EXCLUDED = {
    "gpt-5.4·think": "OpenAI quota exhausted mid-config — episodes quarantined, pending rerun",
    "claude-sonnet-4.6·think†": "Anthropic credit exhausted mid-config — B=5 + 6 hard-B1 episodes "
                                "quarantined, pending rerun (row incomplete)",
    "nemotron-3-ultra": "no OpenRouter endpoint supports tool_choice:required (harness contract)",
}
# starter rows superseded by a cleaner direct-API rerun with the same label
SUPERSEDED_STARTER = {"gpt-5.4-mini"}


def bootstrap_ci(eps: list[dict], n_boot: int = 2000) -> float | None:
    """95% CI half-width on KR̄ via seed-block bootstrap (mirrors render_board.py)."""
    import random
    seedkr: dict = defaultdict(dict)
    for e in eps:
        if e["kr"] is not None:
            seedkr[e["seed"]][(e["B"], e["tier"])] = e["kr"]
    seeds = list(seedkr)
    if len(seeds) < 3:
        return None
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    cells_all = defaultdict(list)
    for s in seeds:
        for c, v in seedkr[s].items():
            cells_all[c].append(v)
    kbar = mean([mean(v) for v in cells_all.values()])
    rng = random.Random(0)
    boots = []
    for _ in range(n_boot):
        pick = [seeds[rng.randrange(len(seeds))] for _ in seeds]
        cells = defaultdict(list)
        for s in pick:
            for c, v in seedkr[s].items():
                cells[c].append(v)
        boots.append(mean([mean(v) for v in cells.values()]))
    boots.sort()
    lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
    return max(hi - kbar, kbar - lo)


def main() -> int:
    starter = json.loads((RESULTS / "starter.json").read_text())
    cells = [c for c in starter["board"] if c["model"] not in SUPERSEDED_STARTER]
    total_cost = starter["total_cost_usd"]

    # per-model stats: starter rows from starter.md, patch rows from episodes
    stats: dict[str, dict] = {}
    for m in re.finditer(r"^\| \d+ \| (\S+) \| .* \| \*\*[\d.]+\*\* \| ±([\d.]+) \| [+-−]?\d+ \| (\d+)% \| (\d+) \| ([\d.]+) \|",
                         (RESULTS / "starter.md").read_text(), re.M):
        stats[m.group(1)] = {"ci": float(m.group(2)), "serve": int(m.group(3)),
                             "reason_ep": int(m.group(4)), "cost": float(m.group(5)),
                             "source": "starter (OpenRouter)"}

    for name in PATCH_RUNS:
        run = ROOT / "runs" / name
        lb = json.loads((run / "leaderboard.json").read_text())
        total_cost += lb["total_cost_usd"]
        eps = [json.loads(l) for l in (run / "episodes.jsonl").open() if l.strip()]
        by_model = defaultdict(list)
        for e in eps:
            by_model[e["model"]].append(e)
        for c in lb["board"]:
            if c["model"] not in EXCLUDED:
                cells.append(c)
        for m, mes in by_model.items():
            if m in EXCLUDED:
                continue
            stats[m] = {
                "ci": bootstrap_ci(mes),
                "serve": round(100 * sum(e["served"] for e in mes) / max(1, sum(e["total"] for e in mes))),
                "reason_ep": sum(e["ep_reason"] for e in mes) // max(1, len(mes)),
                "cost": round(sum(e["ep_cost"] for e in mes), 2),
                "source": name,
            }

    models = sorted({c["model"] for c in cells})
    kr = {(c["model"], c["B"], c["tier"]): c["KR"] for c in cells}
    tiers = sorted({c["tier"] for c in cells})
    budgets = sorted({c["B"] for c in cells})
    cols = [(t, b) for t in tiers for b in budgets]
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731

    def overall(m):
        vals = [kr.get((m, b, t)) for t, b in cols if kr.get((m, b, t)) is not None]
        return mean(vals) if vals else -1.0

    models.sort(key=overall, reverse=True)
    n_eps = sum(c["episodes"] for c in cells)

    out = {
        "name": "board",
        "note": "current combined board = frozen starter run + 2026-06-11 patch runs "
                "(direct OpenAI/Anthropic keys + OpenRouter top-up)",
        "ruleset": starter["ruleset"],
        "versions": starter["versions"],
        "total_cost_usd": round(total_cost, 2),
        "excluded": EXCLUDED,
        "superseded": sorted(SUPERSEDED_STARTER),
        "board": cells,
        "model_stats": stats,
    }
    (RESULTS / "board.json").write_text(json.dumps(out, indent=2))

    v = starter["versions"]
    hdr = "| # | model | " + " | ".join(f"{t[:3]} B{int(b)}" for t, b in cols) + \
          " | KR̄ | ±95%CI | serve% | reason/ep | $ |"
    lines = [
        "# Kitchen Rush — current leaderboard (gen 1.0)",
        "",
        f"Ruleset `{v['ruleset']}` (gen {v['ruleset_version']}, frozen) · tokenizer `{v['tokenizer']}` · "
        f"track RP (experimental β) · {n_eps} episodes · total ${total_cost:.2f} · "
        "= [starter run](starter.md) + 2026-06-11 patch (gpt-5.4 family & haiku via direct keys, nemotron via OpenRouter)",
        "",
        "KR = 100·clip((S−S_null)/(S_ref−S_null)), mean over seeds. `·think` = reasoning on "
        "(low effort). Not on the board: `gpt-5.4·think` (provider quota died mid-run — pending), "
        "`nemotron-3-ultra` (no tool_choice:required endpoint on OpenRouter), `gpt-oss-120b` "
        "reasoning-off (provider: reasoning is mandatory), `claude-sonnet-4.6·think†` (runs as a "
        "flagged deviation under tool_choice:auto since Anthropic forbids thinking with forced "
        "tool use — partially run, Anthropic credit died mid-config, pending completion).",
        "",
        hdr,
        "|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|",
    ]
    for i, m in enumerate(models, 1):
        s = stats.get(m, {})
        row = [f"{kr.get((m, b, t)):.0f}" if kr.get((m, b, t)) is not None else "—" for t, b in cols]
        ci = s.get("ci")
        lines.append(f"| {i} | {m} | " + " | ".join(row) +
                     f" | **{overall(m):.1f}** | {'—' if ci is None else f'±{ci:.1f}'} | "
                     f"{s.get('serve', '—')}% | {s.get('reason_ep', '—')} | {s.get('cost', '—')} |")
    (RESULTS / "board.md").write_text("\n".join(lines) + "\n")
    print(f"wrote leaderboard/results/board.json + board.md  ({len(models)} models, {n_eps} episodes, ${total_cost:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
