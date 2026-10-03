#!/usr/bin/env python3
"""Show a combined model matrix while keeping original and continuation provenance."""

from __future__ import annotations

import json
import random
from collections import defaultdict

import report_new_frontier_four_20260929 as display
import run_new_frontier_four_20260929 as prior
import run_new_frontier_four_recovery_20260930 as rec


def render(audit, intervals, with_ci):
    by_cell = defaultdict(list)
    by_id = {}
    for ep in audit["episodes"]:
        by_cell[ep["level"], ep["key"]].append(ep)
        by_id[ep["level"], ep["seed"], ep["key"]] = ep
    lines = ["# Kitchen Rush: GPT-6.1 Sol and Claude Sonnet 5.5",
             "", "Combined scored results from the frozen original campaign and its separately declared continuation. Every kitchen starts from the beginning. Exactly 12 fixed seeds were assigned per level and model setting; failed provider attempts and their charges remain visible in their own cohort. Efficiency charges every scored attempt but credits orders only from fully cleared kitchens.",
             "", "| Level | Model setting | Scored / 12 | Full clears | Orders served | Generation tokens / finished order | Frozen USD / finished order | Invalid decisions |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for level in prior.LEVELS:
        for key in prior.MODELS:
            items = by_cell[level, key]
            point = display.ratios(items)
            ci = intervals[f"{level}|{key}"] if with_ci else {}
            full = sum(ep["clear"] for ep in items)
            served = sum(ep["served"] for ep in items)
            requested = sum(ep["orders"] for ep in items)
            invalid = sum(ep["invalid"] for ep in items)
            decisions = sum(ep["decisions"] for ep in items)
            token_cell = display.fmt("tokens", point["tokens"], ci.get("tokens")) if items else "pending"
            dollar_cell = display.fmt("usd", point["usd"], ci.get("usd")) if items else "pending"
            completion = display.fmt("completion", point["completion"], ci.get("completion")) if items else "pending"
            invalid_rate = display.fmt("invalid", point["invalid"], ci.get("invalid")) if items else "pending"
            lines.append(f"| {level.title()} | {key} | {len(items)}/12 | {full}/{len(items)} | "
                         f"{served}/{requested} ({completion}) | {token_cell} | {dollar_cell} | "
                         f"{invalid}/{decisions} ({invalid_rate}) |")
    lines += ["", "## Matched low-versus-high kitchens", "",
              "Only seeds with scored endings for both effort settings are compared.", "",
              "| Level | Model | Matched pairs | High wins / Low wins / Ties in orders served | High clears / Low clears | High minus low frozen USD on matched seeds |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    for level in prior.LEVELS:
        for family, low, high in (("GPT-6.1 Sol", "gpt-6-1-sol-low", "gpt-6-1-sol-high"),
                                  ("Claude Sonnet 5.5", "claude-sonnet-5-5-low", "claude-sonnet-5-5-high")):
            seeds = [row["seed"] for row in prior.rows(level)]
            pairs = [(by_id[level, seed, low], by_id[level, seed, high])
                     for seed in seeds if (level, seed, low) in by_id and (level, seed, high) in by_id]
            wins = sum(h["served"] > l["served"] for l, h in pairs)
            losses = sum(h["served"] < l["served"] for l, h in pairs)
            ties = len(pairs) - wins - losses
            high_clears = sum(h["clear"] for _, h in pairs)
            low_clears = sum(l["clear"] for l, _ in pairs)
            dollar_diff = sum(h["frozen_usd"] - l["frozen_usd"] for l, h in pairs)
            lines.append(f"| {level.title()} | {family} | {len(pairs)}/12 | {wins} / {losses} / {ties} | "
                         f"{high_clears} / {low_clears} | ${dollar_diff:+.4f} |")
    orig = audit["original_ledger"]
    cont = audit["continuation_ledger"]
    lines += ["", "## Coverage and accounting", "",
              f"Original campaign: {audit['original_attempts']} attempted, {audit['original_scored']} scored; "
              f"${orig['settled_usd']:.6f} settled plus ${orig['unresolved_reserve_usd']:.6f} "
              "retained maximum failed-request liability.", "",
              f"Continuation: {len(audit['continuation_attempts'])}/70 attempted, "
              f"{sum(e['scored'] for e in audit['continuation_attempts'])} scored; "
              f"${cont['settled_usd']:.6f} settled plus ${cont['unresolved_reserve_usd']:.6f} "
              "reserved or retained maximum liability.", "",
              f"Combined local accounted maximum: **${audit['combined_accounted_usd']:.6f} / $189.00 inference cap**. "
              "This is not an external billing statement. Frozen list-price dollars in the table differ from actual provider charges.", ""]
    missing = [(level, row["seed"], key)
               for level in prior.LEVELS for row in prior.rows(level) for key in prior.MODELS
               if (level, row["seed"], key) not in by_id]
    lines.append(f"Scored assignments: {len(audit['episodes'])}/144. Missing scored results: {len(missing)}.")
    if missing:
        lines += ["", "Missing assignments:", ""]
        lines += [f"- {level} {seed} {key}" for level, seed, key in missing]
    lines.append("")
    if with_ci:
        lines += ["Intervals are exploratory 95% whole-kitchen bootstrap intervals over scored episodes. They are omitted with fewer than two scored kitchens or when more than 5% of bootstrap draws have no full clear. They do not account for provider missingness.", ""]
    return "\n".join(lines)


def main():
    audit = json.loads((rec.RUNS / "audit.json").read_text())
    by_cell = defaultdict(list)
    for ep in audit["episodes"]:
        by_cell[ep["level"], ep["key"]].append(ep)
    rng = random.Random(20260930)
    intervals = {f"{level}|{key}": display.bootstrap(by_cell[level, key], rng)
                 for level in prior.LEVELS for key in prior.MODELS}
    (rec.RUNS / "intervals.json").write_text(json.dumps(intervals, indent=2) + "\n")
    (rec.RUNS / "COMBINED_RESULTS.md").write_text(render(audit, intervals, False))
    (rec.RUNS / "COMBINED_RESULTS_WITH_CI.md").write_text(render(audit, intervals, True))
    html = display.render_html(audit, intervals)
    html = html.replace("Provider failures are unscored and are not included in intervals.",
                        "Original and continuation cohorts are reported separately; provider failures are unscored and excluded from intervals.")
    (rec.RUNS / "MATRIX.html").write_text(html)
    print(rec.RUNS / "COMBINED_RESULTS.md")
    print(rec.RUNS / "COMBINED_RESULTS_WITH_CI.md")
    print(rec.RUNS / "MATRIX.html")


if __name__ == "__main__":
    main()
