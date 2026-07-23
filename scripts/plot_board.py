"""Render the README leaderboard charts from leaderboard/results/<name>.json.

One horizontal bar chart per latency budget B (tiers pooled), with 95% CIs.
Needs matplotlib (not a package dependency — maintainer tool only):

    .venv/bin/pip install matplotlib
    .venv/bin/python scripts/plot_board.py            # writes docs/assets/leaderboard_b*.png
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "leaderboard" / "results" / (sys.argv[1] if len(sys.argv) > 1 else "starter")
OUT = ROOT / "docs" / "assets"

SUBTITLE = {
    1.0: "~65 output tokens per decision · terse, single-shot tool dispatch",
    5.0: "~730 output tokens per decision · room for a short reasoning burst",
}
ACCENT, MUTED = "#2f6fb2", "#9db4c8"


def pooled(cells: list[dict]) -> tuple[float, float]:
    """Mean KR across tier cells + 95% CI (normal approx over pooled episodes)."""
    mean = sum(c["KR"] for c in cells) / len(cells)
    var_of_mean = sum(c["kr_std"] ** 2 / c["episodes"] for c in cells) / len(cells) ** 2
    return mean, 1.96 * math.sqrt(var_of_mean)


def main() -> int:
    board = json.loads(RESULTS.with_suffix(".json").read_text())["board"]
    by_model_b: dict[tuple[str, float], list[dict]] = defaultdict(list)
    for cell in board:
        by_model_b[(cell["model"], cell["B"])].append(cell)

    OUT.mkdir(parents=True, exist_ok=True)
    budgets = sorted({b for _, b in by_model_b})
    for b in budgets:
        rows = sorted(
            ((m, *pooled(cells)) for (m, bb), cells in by_model_b.items() if bb == b),
            key=lambda r: r[1],
        )
        names = [r[0] for r in rows]
        krs = [r[1] for r in rows]
        cis = [r[2] for r in rows]

        fig, ax = plt.subplots(figsize=(6.4, 0.42 * len(rows) + 1.6), dpi=200)
        # the lead is shared: accent every bar statistically tied with the top one (its 95% CI
        # reaches the top mean), so a coin-flip ordering never reads as a decided winner
        top = max(krs)
        bars = ax.barh(names, krs, xerr=cis, height=0.62,
                       color=[ACCENT if k + c >= top else MUTED for k, c in zip(krs, cis)],
                       error_kw={"ecolor": "#444", "capsize": 2.5, "lw": 1})
        for bar, k, ci in zip(bars, krs, cis):
            ax.text(bar.get_width() + ci + 1.2, bar.get_y() + bar.get_height() / 2,
                    f"{k:.0f}", va="center", fontsize=8, color="#333")
        ax.set_xlim(0, 100)
        ax.set_xlabel("KR (Kitchen Rush score, 0–100)", fontsize=9)
        ax.set_title(f"Latency budget B = {b:g}s\n{SUBTITLE.get(b, '')}",
                     fontsize=9.5, loc="left")
        ax.tick_params(labelsize=8.5)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="x", color="#e6e6e6", lw=0.7)
        ax.set_axisbelow(True)
        fig.text(0.99, 0.01, "medium+hard pooled · 24 eps/model (Gemini 3.6: 48) · 95% CI · blue = tied for the lead within CI · ·think/·low = reasoning on",
                 ha="right", fontsize=6.5, color="#777")
        fig.tight_layout()
        path = OUT / f"leaderboard_b{b:g}.png"
        fig.savefig(path, facecolor="white")
        print("wrote", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
