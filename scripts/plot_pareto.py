"""Render a per-decision-cost vs KR scatter.

A lens companion to plot_board.py: each model placed by its AVERAGE COST PER
DECISION (total spend ÷ total turns, ×1000 → $/1k decisions) against KR̄.
Normalising by turns isolates how much a model spends per decision, so a
verbose reasoning model shows up as expensive instead of hiding inside a total.
Colour = reasoning on/off; marker size = serve%. Needs matplotlib.

Per-turn cost needs episode-level turn counts (runs/*/episodes.jsonl); models
whose episode data is gone (direct-API patch runs) are skipped and listed.

    .venv/bin/python scripts/plot_pareto.py board   # writes docs/assets/leaderboard_pareto.png
"""
from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "leaderboard" / "results" / (sys.argv[1] if len(sys.argv) > 1 else "board")
SCALE = sys.argv[2] if len(sys.argv) > 2 else "linear"   # linear | log | sqrt
METRIC = sys.argv[3] if len(sys.argv) > 3 else "turn"    # turn (13 models) | episode (all 18)
OUT = ROOT / "docs" / "assets"

OFF, THINK = "#2f6fb2", "#e08a3c"


def per_turn_cost() -> dict[str, float]:
    """Pooled $ per ACTIVE turn (turn that made a tool call) from every episodes.jsonl on disk.

    Active turns = turns − noop_turns. Under forced tool calls a no-op turn is a stall
    (rate-limit/error), which costs nothing, so excluding them makes $/turn robust to the
    429s hit during the OpenRouter backfill. For clean runs noop≈0, so this equals $/turn.
    """
    agg: dict[str, dict] = defaultdict(lambda: {"turns": 0, "cost": 0.0})
    for f in glob.glob(str(ROOT / "runs" / "*" / "episodes.jsonl")):
        for line in open(f):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "model" not in r or "turns" not in r:
                continue
            a = agg[r["model"]]
            a["turns"] += r["turns"] - r.get("noop_turns", 0)
            a["cost"] += r.get("ep_cost", 0.0)
    return {m: a["cost"] / a["turns"] for m, a in agg.items() if a["turns"]}


def main() -> int:
    b = json.loads(RESULTS.with_suffix(".json").read_text())
    krbar: dict[str, list[float]] = defaultdict(list)
    eps_n: dict[str, int] = defaultdict(int)
    for c in b["board"]:
        krbar[c["model"]].append(c["KR"])
        eps_n[c["model"]] += c["episodes"]
    stats = b["model_stats"]
    cpt = per_turn_cost()

    roster = set(krbar)
    pts, missing = [], []
    for m in roster:
        if METRIC == "turn":
            if m not in cpt:
                missing.append(m)
                continue
            x = cpt[m] * 1000                          # $ per 1,000 decisions
        else:                                          # per-episode: all 18 from board cost
            x = stats[m]["cost"] / eps_n[m]            # $ per game
        pts.append({"m": m, "kr": sum(krbar[m]) / len(krbar[m]), "cost1k": x,
                    "serve": stats[m].get("serve") or 0, "think": "·think" in m})

    fig, ax = plt.subplots(figsize=(7.6, 5.6), dpi=200)
    for p in pts:
        ax.scatter(p["cost1k"], p["kr"], s=30 + 1.6 * p["serve"],
                   color=THINK if p["think"] else OFF, alpha=0.85,
                   edgecolor="white", lw=0.8, zorder=3)
        ax.annotate(p["m"], (p["cost1k"], p["kr"]),
                    xytext=(5, 3), textcoords="offset points",
                    fontsize=6.4, color="#333")

    unit = "1,000 decisions" if METRIC == "turn" else "game"
    hi = max(p["cost1k"] for p in pts)
    ticks = ([0.1, 0.5, 1, 2, 5, 10, 17] if METRIC == "turn"
             else [0.01, 0.05, 0.1, 0.2, 0.4, 0.6])
    if SCALE == "log":
        ax.set_xscale("log")
        ax.set_xlim(min(p["cost1k"] for p in pts) * 0.7, hi * 1.4)
    elif SCALE == "sqrt":
        ax.set_xscale("function", functions=(np.sqrt, np.square))
        ax.set_xlim(0, hi * 1.08)
        ax.set_xticks(ticks)
    else:
        ax.set_xlim(left=0)
    ax.set_ylim(-2, 46)
    ax.set_xlabel(f"cost per {unit} ($, {SCALE} scale)  —  cheaper →", fontsize=9)
    ax.set_ylabel("KR̄  (overall Kitchen Rush score)", fontsize=9)
    subtitle = ("spend normalised by turns — verbose reasoning models drift right"
                if METRIC == "turn"
                else "total spend per game — all models, but per-decision verbosity is hidden")
    ax.set_title(f"A different lens: cost per {'decision' if METRIC=='turn' else 'game'} vs. capability\n{subtitle}",
                 fontsize=9.5, loc="left")
    ax.tick_params(labelsize=8.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(color="#ededed", lw=0.7)
    ax.set_axisbelow(True)
    h = [plt.Line2D([], [], marker="o", ls="", color=OFF, label="reasoning off", ms=7),
         plt.Line2D([], [], marker="o", ls="", color=THINK, label="reasoning on (·think)", ms=7)]
    ax.legend(handles=h, fontsize=7.5, loc="upper right", frameon=False)
    basis = "$/turn pooled over both budgets" if METRIC == "turn" else "$/game = total run cost ÷ games"
    note = f"marker size ∝ serve% · {basis} · OpenRouter list prices 2026-06"
    if missing:
        note += f" · omitted (per-turn data deleted): {', '.join(sorted(missing))}"
    fig.text(0.99, 0.005, note, ha="right", fontsize=5.6, color="#777")
    fig.tight_layout()
    path = OUT / f"leaderboard_pareto_{METRIC}_{SCALE}.png"
    fig.savefig(path, facecolor="white")
    print("wrote", path.relative_to(ROOT), "·", len(pts), "models ·", len(missing), "omitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
