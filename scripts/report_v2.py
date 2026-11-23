#!/usr/bin/env python3
"""Generate pilot-only figures and checkpoint review from audited, balanced results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kitchenrush.v2.evaluation import write_json  # noqa: E402


def build(stage):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, NullFormatter

    gate = json.loads((ROOT / f"data/v2/{stage}-gate.json").read_text())
    assert gate["status"] == "passed"
    rows = gate["summary"]["rows"]
    assets = ROOT / "docs/v2/assets"
    assets.mkdir(parents=True, exist_ok=True)
    colors = {"jev": "#41834a", "mini": "#3265c5", "sonnet": "#bc6327"}
    labels = {
        "jev": "Jev 1.13.0",
        "mini": "GPT-5.4 Mini · low",
        "sonnet": "Claude Sonnet 4.6 · thinking off",
    }
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    views = ["dollar-budget", "cost-quality"]
    if any(r["budget_kind"] == "tokens" for r in rows):
        views.append("token-budget")
    for view in views:
        fig, ax = plt.subplots(figsize=(8, 4.8), layout="constrained")
        kind = "tokens" if view == "token-budget" else "USD"
        for model in colors:
            group = sorted(
                [r for r in rows if r["model"] == model and r["budget_kind"] == kind],
                key=lambda r: r["budget"],
            )
            if not group:
                continue
            xs = [r["mean_cost_usd"] if view == "cost-quality" else r["budget"] for r in group]
            ys = [r["quality"] for r in group]
            # Zero-charge cells have no paid decision and cannot lie on a logarithmic cost axis.
            valid = [i for i, x in enumerate(xs) if x > 0]
            xs = [xs[i] for i in valid]
            ys = [ys[i] for i in valid]
            group = [group[i] for i in valid]
            ax.plot(xs, ys, "o-", color=colors[model], label=labels[model], lw=1.8)
            for x, y, r in zip(xs, ys, group, strict=True):
                if r["ci95"] is not None:
                    ax.errorbar(
                        x,
                        y,
                        yerr=[[max(0, y - r["ci95"][0])], [max(0, r["ci95"][1] - y)]],
                        color=colors[model],
                        alpha=0.5,
                        capsize=3,
                    )
                if view == "cost-quality":
                    left_label = model == "jev" and r["budget"] == 0.05
                    ax.annotate(
                        f"${r['budget']:g}",
                        (x, y),
                        xytext=(-8, 10) if left_label else (5, 6),
                        ha="right" if left_label else "left",
                        textcoords="offset points",
                        fontsize=8,
                    )
                if kind == "tokens" and gate["summary"]["B80"][model]["mean"] == r["budget"]:
                    ax.annotate(f"B80: {int(r['budget']):,}", (x, y), xytext=(-85, 12),
                                textcoords="offset points", color=colors[model], fontsize=9)
                if kind == "tokens" and r["dollar_censored"]:
                    ax.annotate(
                        "cost-censored",
                        (x, y),
                        xytext=(4, -13),
                        textcoords="offset points",
                        fontsize=7,
                    )
        if view == "cost-quality":
            points = [r for r in rows if r["budget_kind"] == "USD" and r["mean_cost_usd"] > 0]
            frontier = [
                r
                for r in points
                if not any(
                    s["mean_cost_usd"] <= r["mean_cost_usd"]
                    and s["quality"] >= r["quality"]
                    and (s["mean_cost_usd"] < r["mean_cost_usd"] or s["quality"] > r["quality"])
                    for s in points
                )
            ]
            frontier.sort(key=lambda r: r["mean_cost_usd"])
            ax.plot(
                [r["mean_cost_usd"] for r in frontier],
                [r["quality"] for r in frontier],
                "--",
                color="#555",
                alpha=0.7,
                label="Empirical Pareto frontier",
            )
        if kind == "tokens":
            ax.axhline(80, color="#888", ls=":", lw=1)
        ax.set_xscale("log")
        if view != "cost-quality":
            ticks = sorted({r["budget"] for r in rows if r["budget_kind"] == kind})
            ax.set_xticks(ticks)
        ax.xaxis.set_major_formatter(
            FuncFormatter(
                (lambda x, _: f"{x:,.0f}") if kind == "tokens" else (lambda x, _: f"${x:g}")
            )
        )
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_ylim(-3, 105)
        ax.set_ylabel("Mean quality (0–100)")
        ax.set_xlabel(
            {
                "dollar-budget": "Episode dollar allowance (USD, log scale)",
                "cost-quality": "Actual mean episode API cost (USD, log scale)",
                "token-budget": "Generation-token allowance (log scale)",
            }[view]
        )
        ax.set_title(
            f"Kitchen Rush v2 · {stage.upper()} ONLY · " + view.replace("-", " "),
            loc="left",
            fontweight="bold",
        )
        ax.grid(axis="y", alpha=0.15)
        ax.legend(fontsize=8, loc="best")
        fig.savefig(assets / f"{stage}-{view}.png", dpi=160)
        svg_path = assets / f"{stage}-{view}.svg"
        fig.savefig(svg_path)
        svg_path.write_text(
            "\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n"
        )
        plt.close(fig)
    lines = [
        f"# Checkpoint 2 — {stage} results",
        "",
        f"**{gate['episodes']} audited episodes; "
        f"{gate['requests_verified']} verified API requests.** "
        "These are pilot controls, not the full model leaderboard.",
        "",
        "| Configuration | View | Allowance | Quality | 95% CI | "
        "Mean cost | Complete orders | Invalid calls |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for r in rows:
        ci = "Unavailable" if r["ci95"] is None else f"{r['ci95'][0]:.1f}–{r['ci95'][1]:.1f}"
        lines.append(
            f"| {labels[r['model']]} | {r['budget_kind']} | {r['budget']:g} | "
            f"{r['quality']:.1f} | {ci} | ${r['mean_cost_usd']:.4f} | "
            f"{r['mean_completion']:.0%} | {r['mean_invalid']:.1f} |"
        )
    lines += [
        "",
        "Repeated trials are averaged within seed; intervals resample seeds. Small pilot samples "
        "cannot establish stable ranks. USD and token experiments are separate. Token cells "
        "stopped by the operational dollar backstop cannot establish B80.",
        "",
        "The reservation policy can leave dollars unspent: it must cover a conservative "
        "input-token bound before sending another request. Especially at small allowances, "
        "these curves measure the configured model plus this spending policy. Review this "
        "effect before a release; do not interpret early stopping as the model choosing to stop. "
        "The zero floor also makes some episodes with completed orders score zero after "
        "invalid-action penalties; completion and invalid counts remain essential.",
        "",
        "## B80",
        "",
    ]
    for model in ("jev", "mini", "sonnet"):
        token_rows = [r for r in rows if r["model"] == model and r["budget_kind"] == "tokens"]
        if model == "jev":
            status = "N/A — generation-token comparability/enforcement not established."
        elif not token_rows:
            status = "Not tested in this stage."
        elif all(r["dollar_censored"] for r in token_rows):
            status = "Not estimable: all tested token cells are cost-censored."
        else:
            value = gate["summary"]["B80"][model]
            status = (
                f"{value['mean']:,} tokens."
                if value["mean"]
                else "Not reached within uncensored tested budgets."
            )
            status += f" Conservative B80: {value['conservative'] or 'not reached'}."
        lines.append(f"- {labels[model]}: {status}")
    ledger = gate["ledger"]
    lines += [
        "",
        "## Spending",
        "",
        f"This stage: ${ledger['stages'][stage]:.4f}. "
        f"Total accounted across stages: **${ledger['accounted_usd']:.4f} / $50**. "
        f"Recorded usage charges: ${ledger['settled_usd']:.4f}; permanently retained "
        f"liability for abandoned attempts: ${ledger['unresolved_reserve_usd']:.4f}. "
        "No reserve is counted as a known invoice.",
        "",
        "Jev cost uses its published input-token tariff; "
        "OpenRouter cost comes from response usage. "
        "Output tokens are recorded for both, including Jev probability output. "
        "No network timing enters the charts.",
        "",
        f"Protocol source hash: `{gate['protocol_hash']}`.",
        "",
    ]
    for view in views:
        lines.append(f"![{view}](assets/{stage}-{view}.png)")
    (ROOT / f"docs/v2/{stage.upper()}_RESULTS.md").write_text("\n".join(lines) + "\n")
    write_json(
        ROOT / f"data/v2/{stage}-plot-data.json",
        {"protocol_hash": gate["protocol_hash"], "rows": rows},
    )
    if stage == "pilot":
        # Fixed first dollar instance, trial 0, highest allowance: the same kitchen for all.
        # Export frames and costs only; model responses and internal reasoning stay local.
        manifest = json.loads((ROOT / "data/v2/pilot-r3-manifest.json").read_text())
        dollar_jobs = [j for j in manifest["jobs"] if j["token_cap"] is None]
        seed = min(j["seed"] for j in dollar_jobs)
        cap = max(j["dollar_cap"] for j in dollar_jobs)
        examples = [{"label": "Scripted feasibility example", "file": "demo.json"}]
        for model in colors:
            job = next(j for j in dollar_jobs if j["model"] == model
                       and j["seed"] == seed and j["trial"] == 0 and j["dollar_cap"] == cap)
            ep = json.loads((ROOT / "runs/v2/pilot-r3" / (job["id"] + ".json")).read_text())
            write_json(ROOT / f"ui/v2/pilot-{model}.json", {
                "spec": ep["spec"], "frames": ep["frames"], "model_label": labels[model],
                "dollar_cap": ep["dollar_cap"], "quality": ep["quality"],
                "cost_usd": ep["cost_usd"], "reason": ep["reason"],
                "action_costs": [r["cost_usd"] for r in ep["requests"]],
                "episode_id": ep["id"], "protocol_hash": ep["protocol_hash"],
            })
            examples.append({"label": f"{labels[model]} · pilot · ${cap:g}",
                             "file": f"pilot-{model}.json"})
        write_json(ROOT / "ui/v2/examples.json", examples)
    print(f"Wrote docs/v2/{stage.upper()}_RESULTS.md and {len(views)} figures")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["smoke", "pilot"])
    build(p.parse_args().stage)
