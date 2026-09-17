#!/usr/bin/env python3
"""Create cohort-separated figures and full tables from independently re-audited batches."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from audit_release_v2 import audit, money, require  # noqa: E402

from kitchenrush.v2.analysis import summarize  # noqa: E402

COHORTS = {
    "revised_canary": ("revised-canary", "CANARY ONLY", "development"),
    "roster_canary": ("roster-canary", "ROSTER CANARY ONLY", "development"),
    "main_1": ("main", "MAIN EVALUATION", "main"),
    "main_2": ("main", "MAIN EVALUATION", "main"),
    "tokens_1": ("main", "MAIN EVALUATION", "main"),
    "repeat_1": ("main", "MAIN EVALUATION", "main"),
    "confirmation": ("confirmation", "CONFIRMATION ONLY", "confirmation"),
}
LAMBDAS = (0.0, 0.05, 0.1, 0.2)


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def weighted_mean(episodes, value):
    seeds = defaultdict(list)
    for ep in episodes:
        family = ep["spec"]["tier"].rsplit("-", 1)[-1]
        seeds[(family, ep["spec"]["seed"])].append(value(ep))
    families = defaultdict(list)
    for (family, _), values in seeds.items():
        families[family].append(statistics.mean(values))
    return statistics.mean(statistics.mean(values) for values in families.values())


def pareto(rows):
    rows = [row for row in rows if not row.get("unresolved_cost", False)]
    return [
        row
        for row in rows
        if not (row["mean_cost_usd"] > 0 and row["quality"] <= 0)
        and not any(
            other["mean_cost_usd"] <= row["mean_cost_usd"]
            and other["quality"] >= row["quality"]
            and (other["mean_cost_usd"] < row["mean_cost_usd"] or other["quality"] > row["quality"])
            for other in rows
        )
    ]


def cell_episodes(episodes, row):
    return [
        ep
        for ep in episodes
        if ep["model"]["key"] == row["model"]
        and ("USD" if ep["token_cap"] is None else "tokens") == row["budget_kind"]
        and (ep["dollar_cap"] if ep["token_cap"] is None else ep["token_cap"]) == row["budget"]
    ]


def aggregate(episodes):
    # Independent auditing already replayed these artifacts; raw stored flags are not authority.
    safe = copy.deepcopy(episodes)
    for ep in safe:
        ep["replay_verified"] = True
    result = summarize(safe)
    for row in result["rows"]:
        cell = cell_episodes(safe, row)
        row["mean_completed_attempt_cost_usd"] = weighted_mean(cell, lambda e: e["cost_usd"])
        row["mean_cost_usd"] = weighted_mean(
            cell, lambda e: e["cost_usd"] + e.get("_prior_attempt", {}).get("known_cost_usd", 0)
        )
        row["mean_retained_liability_usd"] = weighted_mean(
            cell, lambda e: e.get("_prior_attempt", {}).get("retained_liability_usd", 0)
        )
        row["mean_accounted_cost_usd"] = row["mean_cost_usd"] + row["mean_retained_liability_usd"]
        row["unresolved_cost"] = row["mean_retained_liability_usd"] > 0
        row["replacement_affected"] = sum(bool(e.get("_prior_attempt")) for e in cell)
        row["scheduled_jobs"] = len(cell)
        row["completed_jobs"] = len(cell)
        row["first_attempt_completed_jobs"] = len(cell) - row["replacement_affected"]
        row["attempts"] = len(cell) + row["replacement_affected"]
        row["provider_failures"] = sum(
            e.get("_prior_attempt", {}).get("failure_kind") == "provider_failure" for e in cell
        )
        row["peer_accounting_pauses"] = sum(
            e.get("_prior_attempt", {}).get("failure_kind") == "peer_accounting_pause" for e in cell
        )
        row["mean_completion"] = weighted_mean(
            cell, lambda e: e["completed"] / len(e["spec"]["orders"])
        )
        row["mean_invalid"] = weighted_mean(cell, lambda e: e["invalid"])
        requests = [request for e in cell for request in e["requests"] if "response" in request]
        generative = requests if cell[0]["model"]["provider"] != "TypeSafe" else []
        row["truncated_requests"] = sum(
            isinstance(r["response"].get("choices"), list)
            and bool(r["response"]["choices"])
            and isinstance(r["response"]["choices"][0], dict)
            and r["response"]["choices"][0].get("finish_reason") == "length"
            for r in generative
        )
        row["generative_requests"] = len(generative)
        row["request_truncation_rate"] = (
            row["truncated_requests"] / len(generative) if generative else None
        )
        row["mean_boundary_cost_usd"] = weighted_mean(cell, lambda e: e["boundary_cost_usd"])
        row["mean_accepted_cost_usd"] = weighted_mean(cell, lambda e: e["accepted_cost_usd"])
        row["sensitivity"] = {
            str(weight): weighted_mean(
                cell,
                lambda e, w=weight: (
                    100 * max(0, (e["completed"] - w * e["invalid"]) / len(e["spec"]["orders"]))
                ),
            )
            for weight in LAMBDAS
        }
        require(
            abs(row["quality"] - row["sensitivity"]["0.1"]) < 1e-10,
            "headline quality and raw penalty reconstruction disagree",
        )
    # A replacement resets the wallet: its successful score cannot establish that the
    # scheduled job (including the interrupted prefix) fit the nominal token allowance.
    for model, value in result["B80"].items():
        token_rows = [
            r for r in result["rows"] if r["model"] == model and r["budget_kind"] == "tokens"
        ]
        if not token_rows:
            continue
        usable = sorted(
            (r for r in token_rows if not r["dollar_censored"] and not r["replacement_affected"]),
            key=lambda r: r["budget"],
        )
        value["mean"] = next((r["budget"] for r in usable if r["quality"] >= 80), None)
        value["conservative"] = next(
            (r["budget"] for r in usable if r["ci95"] is not None and r["ci95"][0] >= 80), None
        )
        value["status"] = (
            "reached" if value["mean"] is not None else "not reached in eligible cells"
        )
    economic = [r for r in result["rows"] if r["budget_kind"] == "USD"]
    frontier = pareto(economic)
    for row in result["rows"]:
        row["empirical_cost_quality_frontier"] = any(row is point for point in frontier)
    return result


def b80_text(model, summary, roster):
    if roster["status"].startswith("blocked"):
        return "Blocked — " + roster["block_reason"], "N/A"
    if roster["provider"] == "TypeSafe":
        return "N/A — generation-token accounting/enforcement not comparable", "N/A"
    rows = [r for r in summary["rows"] if r["model"] == model and r["budget_kind"] == "tokens"]
    if not rows:
        return "Not tested in this cohort", "Not tested"
    usable = [r for r in rows if not r["dollar_censored"] and not r["replacement_affected"]]
    if not usable:
        return "Not estimable — cells censored or replacement-affected", "Not estimable"
    values = summary["B80"][model]
    mean = (
        f"{values['mean']:,}" if values["mean"] is not None else "Not reached within tested budgets"
    )
    conservative = (
        f"{values['conservative']:,}"
        if values["conservative"] is not None
        else "Unavailable — insufficient seeds"
        if all(r["ci95"] is None for r in usable)
        else "Not reached within tested budgets"
    )
    return mean, conservative


def plot_cohort(name, title, summary, roster, codes, assets):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, NullFormatter

    rows = summary["rows"]
    dollars = [r for r in rows if r["budget_kind"] == "USD"]
    frontier = pareto(dollars)
    highlight = {r["model"] for r in frontier}
    models = sorted({r["model"] for r in rows})
    if len(models) <= 5:
        highlight = set(models)
    elif len(highlight) > 8:
        ranked = sorted(
            highlight, key=lambda m: min(r["mean_cost_usd"] for r in frontier if r["model"] == m)
        )
        highlight = {ranked[round(i * (len(ranked) - 1) / 7)] for i in range(8)}
    colors = {key: plt.get_cmap("tab10")(i % 10) for i, key in enumerate(sorted(highlight))}
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.hashsalt": "kitchen-rush-v2",
        }
    )
    views = ["dollar-budget", "cost-quality"] if dollars else []
    if any(r["budget_kind"] == "tokens" for r in rows):
        views.append("token-budget")
    for view in views:
        fig, ax = plt.subplots(figsize=(9.6, 5.8))
        fig.subplots_adjust(left=0.10, right=0.96, top=0.84, bottom=0.28)
        token = view == "token-budget"
        data = [r for r in rows if r["budget_kind"] == ("tokens" if token else "USD")]
        xlabel = (
            "Generation-token allowance"
            if token
            else "Known total attempt cost / scheduled job (USD)"
            if view == "cost-quality"
            else "Accepted-action dollar allowance (USD)"
        )
        if view == "cost-quality":
            positive = [r for r in data if r["mean_cost_usd"] > 0]
            ax.scatter(
                [r["mean_cost_usd"] for r in positive if not r["unresolved_cost"]],
                [r["quality"] for r in positive if not r["unresolved_cost"]],
                s=25,
                c="#acb4c0",
                alpha=0.75,
                label="Resolved all-attempt cost",
            )
            for index, row in enumerate(r for r in positive if r["unresolved_cost"]):
                ax.errorbar(
                    row["mean_cost_usd"],
                    row["quality"],
                    xerr=[[0], [row["mean_retained_liability_usd"]]],
                    fmt="o",
                    markerfacecolor="white",
                    color="#a36622",
                    capsize=3,
                    label="Unknown charge: retained-liability extent" if index == 0 else None,
                )
            ordered = sorted(
                [r for r in frontier if r["mean_cost_usd"] > 0],
                key=lambda r: (r["mean_cost_usd"], r["quality"]),
            )
            ax.plot(
                [r["mean_cost_usd"] for r in ordered],
                [r["quality"] for r in ordered],
                "o--",
                color="#194f8d",
                lw=1.5,
                ms=5,
                label="Empirical frontier · resolved billing only",
            )
            annotated = (
                sorted(positive, key=lambda r: (r["mean_cost_usd"], r["quality"]))
                if len(models) <= 6
                else ordered
            )
            annotate_indices = (
                set(range(len(annotated)))
                if len(models) <= 6 or len(annotated) <= 8
                else {round(i * (len(annotated) - 1) / 7) for i in range(8)}
            )
            for i, row in enumerate(annotated):
                if i not in annotate_indices:
                    continue
                label = f"{codes[row['model']]} · ${row['budget']:g}"
                if len(models) <= 6:
                    label = f"{roster[row['model']]['label']} · ${row['budget']:g}"
                ax.annotate(
                    label,
                    (row["mean_cost_usd"], row["quality"]),
                    xytext=(5, 8 + 11 * (i % 2)),
                    textcoords="offset points",
                    fontsize=8,
                )
        else:
            budgets = sorted({r["budget"] for r in data})
            token_highlight = (
                set(
                    sorted(
                        {r["model"] for r in data},
                        key=lambda m: (-max(r["quality"] for r in data if r["model"] == m), m),
                    )[:8]
                )
                if token
                else set()
            )
            token_colors = {
                m: plt.get_cmap("tab10")(i) for i, m in enumerate(sorted(token_highlight))
            }
            for model in models:
                group = sorted([r for r in data if r["model"] == model], key=lambda r: r["budget"])
                if not group:
                    continue
                selected = (model in token_highlight if token else model in highlight) or len(
                    models
                ) <= 5
                color = (
                    (token_colors if token else colors).get(model, "#8795a9")
                    if selected
                    else "#b7bec8"
                )
                label = f"{codes[model]} {roster[model]['label']}" if selected else None
                ax.plot(
                    [r["budget"] for r in group],
                    [r["quality"] for r in group],
                    "o-",
                    color=color,
                    alpha=0.95 if selected else 0.45,
                    lw=1.8 if selected else 0.8,
                    ms=4 if selected else 2,
                    label=label,
                )
                for row in group:
                    if selected and row["ci95"] is not None:
                        ax.errorbar(
                            row["budget"],
                            row["quality"],
                            yerr=[
                                [max(0, row["quality"] - row["ci95"][0])],
                                [max(0, row["ci95"][1] - row["quality"])],
                            ],
                            color=color,
                            alpha=0.45,
                            capsize=2,
                        )
                    if token and (row["dollar_censored"] or row["replacement_affected"]):
                        ax.scatter(
                            [row["budget"]], [row["quality"]], marker="x", color="#bc3434", s=55
                        )
            ax.set_xticks(budgets)
            if token:
                ax.axhline(80, color="#626e7b", ls=":", lw=1, label="B80 target")
        xs = [r["mean_cost_usd"] if view == "cost-quality" else r["budget"] for r in data]
        if view == "cost-quality":
            xs.extend(r["mean_accounted_cost_usd"] for r in data)
        positive = [x for x in xs if x > 0]
        if positive:
            ax.set_xscale("log")
            if view != "cost-quality":
                ax.set_xticks(sorted({r["budget"] for r in data}))
            lo, hi = min(positive), max(positive)
            ax.set_xlim(lo / (1.6 if lo == hi else 1.3), hi * (1.6 if lo == hi else 1.5))
        ax.xaxis.set_major_formatter(
            FuncFormatter((lambda x, _: f"{x:,.0f}") if token else (lambda x, _: f"${x:g}"))
        )
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_ylim(-3, 115)
        ax.set_ylabel("Mean quality (0–100)")
        ax.set_xlabel(xlabel + " · log scale")
        ax.set_title(
            f"Kitchen Rush v2 · {title}\n"
            + {
                "cost-quality": "Cost and quality",
                "dollar-budget": "Quality at dollar allowances",
                "token-budget": "Quality at token allowances",
            }[view],
            loc="left",
            fontweight="bold",
        )
        ax.grid(axis="y", alpha=0.15)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(
                handles,
                labels,
                loc="upper left",
                bbox_to_anchor=(0, -0.23),
                frameon=False,
                fontsize=8,
                ncol=2 if len(handles) <= 8 else 3,
            )
        footer = (
            "No-call ($0, Q0) is off log axis. "
            "All attempts included. Quality from completed attempt.\n"
            "Liability extent is an accounting envelope, not a CI or guaranteed bill ceiling."
            if view == "cost-quality"
            else "Intervals exploratory; unavailable below two independent seeds per family."
        )
        if len(models) > 5 and view == "dollar-budget":
            footer += " Gray lines: other configurations; full values in table."
        if token:
            footer += " × = censored or replacement-affected."
        fig.text(0.02, 0.02, footer, fontsize=8, color="#4d5865")
        fig.savefig(assets / f"{name}-{view}.png", dpi=170, bbox_inches="tight", pad_inches=0.15)
        svg = assets / f"{name}-{view}.svg"
        fig.savefig(svg, bbox_inches="tight", pad_inches=0.15, metadata={"Date": None})
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
        plt.close(fig)
    return views


def spending(root):
    result = {
        "prior_accounted_usd": 0,
        "current_accounted_usd": 0,
        "current_failed_or_incomplete_usd": 0,
        "current_unresolved_or_unknown_usd": 0,
        "current_missing_artifact_usd": 0,
        "failed_attempts": [],
        "historical_failed_attempts": [],
    }
    for prefix, path in (
        ("current", root / "runs/v2/release-r2/spend.sqlite"),
        ("historical_r1", root / "runs/v2/release/spend.sqlite"),
        ("historical", root / "runs/v2/spend.sqlite"),
    ):
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as con:
            con.execute("BEGIN")
            rows = con.execute("SELECT episode,reserved,actual,status FROM requests").fetchall()
            tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            opening = (
                con.execute("SELECT amount FROM opening WHERE id=1").fetchone()[0]
                if "opening" in tables
                else 0
            )
        by_episode = defaultdict(list)
        for row in rows:
            by_episode[row[0]].append(row)
        if prefix == "current":
            result["prior_accounted_usd"] = opening / 1e9
            result["current_accounted_usd"] = (
                sum(r[2] if r[2] is not None else r[1] for r in rows) / 1e9
            )
        for eid, group in sorted(by_episode.items()):
            base = (
                root
                / {
                    "current": "runs/v2/release-r2",
                    "historical_r1": "runs/v2/release",
                    "historical": "runs/v2",
                }[prefix]
            )
            paths = [p for p in base.rglob("*.json") if p.stem == eid]
            episode = load(paths[0]) if len(paths) == 1 else None
            status = episode.get("status") if episode else "artifact_unavailable"
            liability = sum(r[2] if r[2] is not None else r[1] for r in group)
            unknown = sum(r[1] for r in group if r[2] is None)
            if status != "complete":
                item = {
                    "episode": eid,
                    "status": status,
                    "accounted_usd": liability / 1e9,
                    "known_charges_usd": sum(r[2] or 0 for r in group) / 1e9,
                    "unknown_liability_usd": unknown / 1e9,
                }
                result[
                    "failed_attempts" if prefix == "current" else "historical_failed_attempts"
                ].append(item)
                if prefix == "current":
                    result["current_failed_or_incomplete_usd"] += liability / 1e9
                    if episode is None:
                        result["current_missing_artifact_usd"] += liability / 1e9
            if prefix == "current":
                result["current_unresolved_or_unknown_usd"] += unknown / 1e9
    result["total_accounted_usd"] = result["prior_accounted_usd"] + result["current_accounted_usd"]
    return result


def build(batches, *, root=ROOT):
    require(batches and len(batches) == len(set(batches)), "duplicate or missing batch names")
    require(all(b in COHORTS for b in batches), "unknown reporting cohort")
    roster_data = load(root / "data/v2/release-r2-roster.json")
    roster = {r["key"]: r for r in roster_data["models"]}
    codes = {key: f"M{i + 1:02d}" for i, key in enumerate(sorted(roster))}
    cohorts = {}
    identities = defaultdict(set)
    provenance = []
    for batch in batches:
        gate_path = root / f"data/v2/release-r2/{batch}-gate.json"
        require(gate_path.is_file(), f"missing independent gate: {batch}")
        stored = load(gate_path)
        fresh = audit(batch, root=root, write_gate=False)
        for field in (
            "status",
            "protocol_hash",
            "configuration_hash",
            "roster_sha256",
            "manifest_sha256",
            "source_archive_sha256",
            "episodes",
            "requests_verified",
            "evaluated_artifacts",
            "failed_attempts",
            "job_resources",
            "operational_amendment",
        ):
            require(stored[field] == fresh[field], f"stale independent gate field {batch}/{field}")
        manifest_path = root / f"data/v2/release-r2/{batch}-manifest.json"
        manifest = load(manifest_path)
        name, title, split = COHORTS[batch]
        require(all(j["split"] == split for j in manifest["jobs"]), "mixed recipe splits in cohort")
        cohort = cohorts.setdefault(
            name, {"title": title, "split": split, "batches": [], "episodes": []}
        )
        cohort["batches"].append(batch)
        for job in manifest["jobs"]:
            identity = tuple(
                job[k]
                for k in ("model", "seed", "split", "size", "trial", "dollar_cap", "token_cap")
            )
            require(identity not in identities[name], "duplicate experiment across pooled batches")
            identities[name].add(identity)
            artifact_path = root / fresh["evaluated_artifacts"][job["id"]]
            episode = load(artifact_path)
            prior = next((f for f in fresh["failed_attempts"] if f["job"] == job["id"]), None)
            if prior:
                episode["_prior_attempt"] = prior
            resources = fresh["job_resources"][job["id"]]
            require(
                money(episode["cost_usd"]) + money((prior or {}).get("known_cost_usd", 0))
                == money(resources["known_cost_usd"])
                and money((prior or {}).get("retained_liability_usd", 0))
                == money(resources["retained_liability_usd"]),
                "report job economics disagree with independently reconciled ledger",
            )
            require(
                episode["output_tokens"] + (prior or {}).get("known_output_tokens", 0)
                == resources["known_output_tokens"],
                "report job token usage disagrees with independent accounting",
            )
            cohort["episodes"].append(episode)
        provenance.append(
            {
                "batch": batch,
                "gate_sha256": sha(gate_path),
                "manifest_sha256": sha(manifest_path),
                "source_archive_sha256": fresh["source_archive_sha256"],
                "protocol_hash": fresh["protocol_hash"],
                "configuration_hash": fresh["configuration_hash"],
                "roster_sha256": fresh["roster_sha256"],
                "episodes": fresh["episodes"],
                "requests_verified": fresh["requests_verified"],
                "failed_attempt_count": fresh["failed_attempt_count"],
                "attempt_count": fresh["attempt_count"],
                "failed_attempts": fresh["failed_attempts"],
                "operational_amendment": fresh["operational_amendment"],
            }
        )
    require(len({p["protocol_hash"] for p in provenance}) == 1, "mixed protocol generations")
    assets = root / "docs/v2/release-r2-assets"
    assets.mkdir(parents=True, exist_ok=True)
    totals = spending(root)
    artifact = {
        "provenance": provenance,
        "spending": totals,
        "cohorts": {},
        "roster": roster_data,
        "pareto_reference": {"label": "No call", "cost_usd": 0, "quality": 0},
    }
    labels = ", ".join(c["title"] for c in cohorts.values())
    lines = [
        "# Kitchen Rush v2 — audited results",
        "",
        f"**{labels}.**",
        "",
        "Useful work. Limited budgets. Unfamiliar kitchens.",
        "",
        "Canaries and pilots are diagnostic controls. Main and confirmation results remain "
        "separate. Main batches pool matched dollar/token cells and repeated trials, "
        "with trials averaged within kitchens. "
        "No old pilot or v1 score enters these results.",
        "",
        "Quality is 100 × max(0, (completed orders − 0.1 × invalid actions) / requested orders). "
        "Trials average within kitchen, kitchens within recipe-size family, then families equally. "
        "Error bars are exploratory pointwise 95% within-family seed bootstrap intervals; "
        "fewer than two "
        "independent seeds in any represented family means unavailable. "
        "Few seeds cannot establish stable ranks.",
        "",
        "Dollar allowances fund the accepted action prefix. "
        "The first response crossing the allowance "
        "is paid but not executed. **Graphs include known charges from every attempt of each "
        "scheduled job, including boundary responses and interrupted prefixes.** "
        "Quality comes from the audited completed attempt. Unknown charges are shown as "
        "retained-liability extents; these are accounting envelopes, not confidence intervals "
        "or guaranteed bill ceilings. Unresolved-cost points cannot enter the exact frontier. "
        "A documented transport failure permits one replacement of the same job; "
        "the original trace and all liability remain in the campaign accounting. "
        "Scores never qualify for a retry. "
        "No latency enters scoring or figures.",
        "",
        "## Full roster coverage",
        "",
        "| ID | Configuration | " + " | ".join(c["title"] for c in cohorts.values()) + " |",
        "|---|---|" + "---|" * len(cohorts),
    ]
    for key in sorted(roster):
        row = roster[key]
        statuses = []
        for cohort in cohorts.values():
            count = sum(e["model"]["key"] == key for e in cohort["episodes"])
            statuses.append(
                f"{count} audited episodes"
                if count
                else "Blocked: " + row["block_reason"]
                if row["status"].startswith("blocked")
                else "Not tested in this cohort"
            )
        lines.append(f"| {codes[key]} | {row['label']} | " + " | ".join(statuses) + " |")
    for name, cohort in cohorts.items():
        episodes = cohort["episodes"]
        require(len({e["id"] for e in episodes}) == len(episodes), "duplicate pooled episodes")
        summary = aggregate(episodes)
        cohort_data = {k: v for k, v in cohort.items() if k != "episodes"}
        cohort_data.update(
            summary=summary, episode_count=len(episodes), episode_ids=[e["id"] for e in episodes]
        )
        artifact["cohorts"][name] = cohort_data
        views = plot_cohort(name, cohort["title"], summary, roster, codes, assets)
        lines += [
            "",
            f"## {cohort['title']}",
            "",
            f"{len(episodes)} audited episodes from "
            + ", ".join(cohort["batches"])
            + ". Recipe split: "
            + cohort["split"]
            + ".",
            "",
        ]
        for view in views:
            lines += [f"![{cohort['title']} {view}](release-r2-assets/{name}-{view}.png)", ""]
        lines += [
            "The frontier uses observed means and a no-call ($0, quality 0) reference. "
            "This reference is off the log axis. Positive-cost zero-quality points are dominated. "
            "It is not a claim of statistical dominance. "
            "Its comparison set contains only resolved-cost points. "
            "Gray points/lines remain in the complete table. "
            "Truncation counts include paid boundary replies; 0/0 means no generative replies. "
            "Zero-cost cells remain in the table "
            "but cannot appear on the logarithmic cost axis.",
            "",
            "| ID / configuration | View | Allowance | Quality | 95% interval | Seeds / family | "
            "Known all-attempt cost | Boundary cost | Orders completed | Invalid actions | "
            "Truncated replies | Frontier |",
            "|---|---|---:|---:|---|---|---:|---:|---:|---:|---|---|",
        ]
        for row in summary["rows"]:
            ci = (
                "Unavailable"
                if row["ci95"] is None
                else f"{row['ci95'][0]:.1f}–{row['ci95'][1]:.1f}"
            )
            allowance = (
                f"${row['budget']:g}" if row["budget_kind"] == "USD" else f"{row['budget']:,}"
            )
            family = ", ".join(f"{k}: {v}" for k, v in sorted(row["seeds_by_family"].items()))
            view = row["budget_kind"] + (" (dollar-censored)" if row["dollar_censored"] else "")
            if row["replacement_affected"]:
                view += " (replacement-affected)"
            frontier_status = (
                "Unresolved billing"
                if row["unresolved_cost"]
                else "Yes"
                if row["empirical_cost_quality_frontier"]
                else "—"
            )
            lines.append(
                f"| {codes[row['model']]} {roster[row['model']]['label']} | {view} | {allowance} | "
                f"{row['quality']:.1f} | {ci} | {family} | ${row['mean_cost_usd']:.6f} | "
                f"${row['mean_boundary_cost_usd']:.6f} | {row['mean_completion']:.1%} | "
                f"{row['mean_invalid']:.2f} | "
                f"{row['truncated_requests']}/{row['generative_requests']} | "
                f"{frontier_status} |"
            )
        lines += [
            "",
            "### Attempts and cost accounting",
            "",
            "Costs sum attempts within each scheduled job, then use the same kitchen/family "
            "weighting as quality. Replacements do not add independent statistical samples. "
            "A fully audited batch requires every scheduled job to finish; partial batches "
            "are not silently reduced to successful jobs. Peer accounting pauses are harness "
            "interruptions, not provider failures.",
            "",
            "| Configuration / allowance | Completed / scheduled jobs | "
            "First-attempt completions | "
            "Attempts | Provider failures | Peer pauses | Completed-attempt cost | "
            "Known all-attempt cost | Unknown retained liability |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in summary["rows"]:
            lines.append(
                f"| {codes[row['model']]} / {row['budget']:g} {row['budget_kind']} | "
                f"{row['completed_jobs']}/{row['scheduled_jobs']} | "
                f"{row['first_attempt_completed_jobs']} | {row['attempts']} | "
                f"{row['provider_failures']} | {row['peer_accounting_pauses']} | "
                f"${row['mean_completed_attempt_cost_usd']:.6f} | "
                f"${row['mean_cost_usd']:.6f} | ${row['mean_retained_liability_usd']:.6f} |"
            )
        lines += [
            "",
            "### B80",
            "",
            "Smallest tested generation-token allowance with mean quality ≥80. "
            "The conservative variant requires the lower pointwise 95% bound ≥80. "
            "It is exploratory, not a simultaneous 95% guarantee across the budget search. "
            "No interpolation; "
            "dollar-censored and replacement-affected cells are excluded in full. "
            "A replacement resets the wallet, so its final score cannot prove that the "
            "scheduled job including its failed prefix fit the stated token budget. "
            "Results apply only to this cohort's represented families.",
            "",
            "| ID / configuration | B80 (tokens) | Conservative B80 |",
            "|---|---|---|",
        ]
        for key in sorted(roster):
            mean, conservative = b80_text(key, summary, roster[key])
            lines.append(f"| {codes[key]} {roster[key]['label']} | {mean} | {conservative} |")
        lines += [
            "",
            "### Penalty sensitivity",
            "",
            "Recomputed from raw completed/invalid counts. "
            "The headline remains λ=0.1; clipping is applied per episode before averaging.",
            "",
            "| ID / configuration | View | Allowance | λ=0 | λ=0.05 | λ=0.1 | λ=0.2 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for row in summary["rows"]:
            values = " | ".join(f"{row['sensitivity'][str(w)]:.1f}" for w in LAMBDAS)
            lines.append(
                f"| {codes[row['model']]} {roster[row['model']]['label']} | "
                f"{row['budget_kind']} | {row['budget']:g} | {values} |"
            )
    lines += [
        "",
        "## Spending and failed attempts",
        "",
        f"Total accounted: **${totals['total_accounted_usd']:.6f} / $200**. "
        f"Brought forward from earlier protocols: ${totals['prior_accounted_usd']:.6f}; "
        f"current protocol: ${totals['current_accounted_usd']:.6f}. "
        f"Current failed/incomplete attempts: ${totals['current_failed_or_incomplete_usd']:.6f}. "
        f"Current unknown liability: ${totals['current_unresolved_or_unknown_usd']:.6f}. "
        "These are campaign totals, including batches not selected for this report.",
        "",
        "Known charges and retained unknown liabilities are separate. Jev dollar charges use "
        "reported input usage × frozen published tariff; OpenRouter uses reported usage.cost. "
        "TypeSafe's operational reserve is an estimate, not a guaranteed provider spending cap.",
        "",
        "| Protocol | Attempt | Status | Known charges | Unknown liability |",
        "|---|---|---|---:|---:|",
    ]
    failures = [("Current", e) for e in totals["failed_attempts"]]
    failures += [("Historical", e) for e in totals["historical_failed_attempts"]]
    if not failures:
        lines.append("| Both | — | No failed or incomplete attempts found | $0 | $0 |")
    for prefix, entry in failures:
        lines.append(
            f"| {prefix} | {entry['episode']} | {entry['status']} | "
            f"${entry['known_charges_usd']:.6f} | ${entry['unknown_liability_usd']:.6f} |"
        )
    lines += [
        "",
        "## Provenance",
        "",
        "[Machine-readable plot data and hashes](release-r2-assets/plot-data.json). "
        "Each requested batch was independently re-audited before report generation.",
        "",
        "| Batch | Manifest SHA-256 | Independent gate SHA-256 |",
        "|---|---|---|",
    ]
    for item in provenance:
        lines.append(f"| {item['batch']} | `{item['manifest_sha256']}` | `{item['gate_sha256']}` |")
    (assets / "plot-data.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    document = root / "docs/v2/RELEASE_RESULTS.md"
    document.write_text("\n".join(lines) + "\n")
    return {
        "document": str(document),
        "cohorts": list(cohorts),
        "episodes": sum(len(c["episodes"]) for c in cohorts.values()),
        "spending": totals,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch",
        action="append",
        required=True,
        help="Repeat for additional batches; recipe splits remain separate",
    )
    print(json.dumps(build(parser.parse_args().batch), indent=2))
