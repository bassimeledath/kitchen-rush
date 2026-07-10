"""Render a cost-efficiency view of the board: KR per dollar of API spend.

A companion to plot_board.py — a different lens that prices each model's KR̄
against what it cost to run. Models scoring too low to deploy (KR̄ < FLOOR) are
greyed: their high KR/$ is illusory (cheap × near-useless). Needs matplotlib.

    .venv/bin/python scripts/plot_efficiency.py board   # writes docs/assets/leaderboard_efficiency.png
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "leaderboard" / "results" / (sys.argv[1] if len(sys.argv) > 1 else "board")
OUT = ROOT / "docs" / "assets"

ACCENT, GREY = "#2f6fb2", "#cdd5dd"
FLOOR = 10.0  # KR̄ below this: too weak to deploy, so KR/$ is misleading


def main() -> int:
    b = json.loads(RESULTS.with_suffix(".json").read_text())
    krbar: dict[str, list[float]] = defaultdict(list)
    for c in b["board"]:
        krbar[c["model"]].append(c["KR"])
    stats = b["model_stats"]

    rows = []
    for m, ks in krbar.items():
        kbar = sum(ks) / len(ks)
        cost = stats[m]["cost"]
        rows.append((m, kbar, cost, kbar / cost if cost else 0.0))
    rows.sort(key=lambda r: r[3])  # ascending -> largest KR/$ at top in barh

    names = [r[0] for r in rows]
    ratios = [r[3] for r in rows]
    colors = [ACCENT if r[1] >= FLOOR else GREY for r in rows]

    fig, ax = plt.subplots(figsize=(7.0, 0.42 * len(rows) + 1.6), dpi=200)
    bars = ax.barh(names, ratios, height=0.62, color=colors)
    for bar, (m, kbar, cost, ratio) in zip(bars, rows):
        ax.text(bar.get_width() + max(ratios) * 0.012, bar.get_y() + bar.get_height() / 2,
                f"KR {kbar:.0f} · ${cost:.2f}", va="center", fontsize=7.2,
                color="#333" if kbar >= FLOOR else "#aaa")
    ax.set_xlim(0, max(ratios) * 1.2)
    ax.set_xlabel("KR per dollar  (KR̄ ÷ total API spend)", fontsize=9)
    ax.set_title("A different lens: cost efficiency\nhow much Kitchen Rush score each dollar of API spend buys",
                 fontsize=9.5, loc="left")
    ax.tick_params(labelsize=8.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color="#e6e6e6", lw=0.7)
    ax.set_axisbelow(True)
    fig.text(0.99, 0.01,
             f"grey = KR̄ < {FLOOR:g}, too weak to deploy (high KR/$ is illusory) · "
             "cost = total run spend, both budgets pooled · list prices 2026-06 · ·think = reasoning on",
             ha="right", fontsize=6, color="#777")
    fig.tight_layout()
    path = OUT / "leaderboard_efficiency.png"
    fig.savefig(path, facecolor="white")
    print("wrote", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
