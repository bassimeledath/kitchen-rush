#!/usr/bin/env python3
"""Render the four frozen metrics, paired effort contrasts and seed intervals."""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs/v2/new-frontier-four-20260929"
LEVELS = ("easy", "medium", "hard")
KEYS = ("gpt-6-1-sol-low", "gpt-6-1-sol-high",
        "claude-sonnet-5-5-low", "claude-sonnet-5-5-high")


def wilson(k, n):
    if not n:
        return None
    z = 1.959963984540054
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return mid - half, mid + half


def ratios(items):
    finished = sum(e["orders"] for e in items if e["clear"])
    ordered = sum(e["orders"] for e in items)
    decisions = sum(e["decisions"] for e in items)
    return {
        "tokens": sum(e["generation_tokens"] for e in items) / finished if finished else None,
        "usd": sum(e["frozen_usd"] for e in items) / finished if finished else None,
        "completion": sum(e["served"] for e in items) / ordered if ordered else None,
        "invalid": sum(e["invalid"] for e in items) / decisions if decisions else None,
    }


def percentile(values, q):
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo = math.floor(pos)
    return values[lo] + (values[math.ceil(pos)] - values[lo]) * (pos - lo)


def bootstrap(items, rng, reps=4000):
    if len(items) < 2:
        return {name: None for name in ratios(items)}
    draws = {name: [] for name in ratios(items)}
    for _ in range(reps):
        sample = rng.choices(items, k=len(items))
        for name, value in ratios(sample).items():
            if value is not None:
                draws[name].append(value)
    return {name: {"low": percentile(vals, .025), "high": percentile(vals, .975),
                   "finite_draw_fraction": len(vals) / reps}
            if len(vals) >= .95 * reps else None for name, vals in draws.items()}


def fmt(name, value, ci=None):
    if value is None:
        return "no kitchen completed" if name in ("tokens", "usd") else "—"
    if name == "tokens":
        format_one = lambda x: f"{x:,.0f}"
    elif name == "usd":
        format_one = lambda x: f"${x:.4f}"
    else:
        format_one = lambda x: f"{x:.1%}"
    result = format_one(value)
    if ci:
        result += f" [{format_one(ci['low'])}, {format_one(ci['high'])}]"
    return result


def render(audit, intervals, with_ci):
    episodes = audit["episodes"]
    by_cell = defaultdict(list)
    by_id = {}
    for ep in episodes:
        by_cell[ep["level"], ep["key"]].append(ep)
        by_id[ep["level"], ep["seed"], ep["key"]] = ep
    lines = ["# Kitchen Rush: GPT-6.1 Sol and Claude Sonnet 5.5", "",
             "Frozen Easy → Medium → Hard single-chef kitchens; 12 assigned kitchens per level and model setting. All episodes start from the beginning. Model settings run independently on the same seeds. The first two metrics charge every **scored** attempt but credit only fully cleared kitchens. Provider and operational failures remain unscored. Intervals, when shown, are exploratory 95% kitchen bootstrap intervals over scored episodes; they do not account for missing provider outcomes.", "",
             "| Level | Model setting | Scored / 12 | Full clears | Orders served | Generation tokens / finished order | Frozen USD / finished order | Invalid decisions |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for level in LEVELS:
        for key in KEYS:
            items = [e for e in by_cell[level, key] if e["scored"]]
            r = ratios(items)
            ci = intervals[f"{level}|{key}"] if with_ci else {}
            summary = audit["summary"][f"{level}|{key}"]
            token_cell = fmt('tokens', r['tokens'], ci.get('tokens')) if items else 'pending'
            usd_cell = fmt('usd', r['usd'], ci.get('usd')) if items else 'pending'
            completion_cell = fmt('completion', r['completion'], ci.get('completion')) if items else 'pending'
            lines.append(f"| {level.title()} | {key} | {len(items)}/12 | "
                         f"{summary['clear_kitchens']}/{len(items)} | "
                         f"{summary['served_orders']}/{summary['requested_orders']} ({completion_cell}) | "
                         f"{token_cell} | "
                         f"{usd_cell} | "
                         f"{summary['invalid']}/{summary['decisions']} "
                         f"({fmt('invalid', r['invalid'], ci.get('invalid'))}) |")
    lines += ["", "## Matched low-versus-high results", "",
              "Each pair below uses the same kitchen seed. A pair counts only when both effort settings have a scored game ending.", "",
              "| Level | Model | Scored pairs | High served more / Low served more / Ties | High full clears / Low full clears | High minus low total frozen USD |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    for level in LEVELS:
        for family, low, high in (("GPT-6.1 Sol", KEYS[0], KEYS[1]),
                                  ("Claude Sonnet 5.5", KEYS[2], KEYS[3])):
            seeds = sorted({ep["seed"] for ep in by_cell[level, low]})
            pairs = [(by_id[level, seed, low], by_id[level, seed, high])
                     for seed in seeds if (level, seed, high) in by_id
                     and by_id[level, seed, low]["scored"] and by_id[level, seed, high]["scored"]]
            wins = sum(h["served"] > l["served"] for l, h in pairs)
            losses = sum(h["served"] < l["served"] for l, h in pairs)
            ties = len(pairs) - wins - losses
            high_clears = sum(h["clear"] for _, h in pairs)
            low_clears = sum(l["clear"] for l, _ in pairs)
            dollar_diff = sum(h["frozen_usd"] - l["frozen_usd"] for l, h in pairs)
            lines.append(f"| {level.title()} | {family} | {len(pairs)}/12 | "
                         f"{wins} / {losses} / {ties} | {high_clears} / {low_clears} | "
                         f"${dollar_diff:+.4f} |")
    ledger = audit["ledger"]
    lines += ["", "## Coverage and accounting", "",
              f"{len(episodes)}/144 episodes attempted; {sum(e['scored'] for e in episodes)} scored, "
              f"{sum(e['status'] == 'running' for e in episodes)} still running, "
              f"{sum(e['status'] not in ('running', 'complete') for e in episodes)} unscored. "
              f"The campaign ledger accounts for ${ledger['accounted_usd']:.6f} of its "
              f"${ledger['cap_usd']:.2f} inference cap, including "
              f"${ledger['unresolved_reserve_usd']:.6f} currently reserved or retained liability. "
              "This is local accounting, not a provider invoice.", ""]
    unscored = [e for e in episodes if not e["scored"] and e["status"] != "running"]
    if unscored:
        lines += ["Unscored attempted episodes:", ""]
        lines += [f"- {e['level']} {e['seed']} {e['key']}: {e['status']} ({e['reason']})"
                  for e in unscored]
        lines.append("")
    if with_ci:
        lines += ["Intervals are omitted where fewer than two kitchens scored or where more than 5% of bootstrap draws have no fully cleared kitchen. A scored-only interval may be optimistic when failures are missing nonrandomly.", ""]
    return "\n".join(lines)


def render_html(audit, intervals):
    data = json.dumps({"summary": audit["summary"], "intervals": intervals}, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    return '''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kitchen Rush frontier pilot</title>
<style>
body{font:15px/1.5 system-ui,-apple-system,sans-serif;max-width:1150px;margin:45px auto;padding:0 22px;color:#202329;background:#faf9f6}
h1{font-size:28px;line-height:1.15}p{color:#4e535b;max-width:850px}label{display:inline-flex;gap:8px;align-items:center;margin:12px 25px 18px 0}
select{font:inherit;padding:8px 10px;background:white;border:1px solid #b9bdc4;border-radius:6px}
table{width:100%;border-collapse:collapse;background:white;border:1px solid #dadddf}th,td{padding:16px;text-align:left;border-bottom:1px solid #e7e8e8;vertical-align:top}th{background:#f1f2f0;font-weight:650}td:first-child{font-weight:600;width:230px}
.value{font-size:19px;font-variant-numeric:tabular-nums}.meta,.interval{font-size:12px;color:#626873;margin-top:5px}.interval{color:#3b617c}
.note{margin-top:18px;font-size:13px}
</style>
<h1>Kitchen Rush: new frontier pilot</h1>
<p>Four model settings on the same 12 Easy, 12 Medium, and 12 Hard kitchens. Every simulation is independent. Thinking uses tokens and dollars but does not advance the game clock.</p>
<label>Metric <select id="metric"><option value="usd">Frozen dollars per finished order</option><option value="tokens">Generation tokens per finished order</option><option value="completion">Orders served</option><option value="invalid">Invalid decisions</option></select></label>
<label><input type="checkbox" id="ci"> Show 95% intervals</label>
<table><thead><tr><th>Model setting</th><th>Easy</th><th>Medium</th><th>Hard →</th></tr></thead><tbody id="rows"></tbody></table>
<p class="note">Efficiency counts tokens and frozen list-price dollars from all scored attempts, but credits orders only when the entire kitchen is cleared. Each cell shows scored simulations and full clears. Intervals resample whole kitchens and are unavailable when fewer than two kitchens scored or too many resamples had no full clear. Provider failures are unscored and are not included in intervals. Different tokenizers limit cross-provider comparisons of token counts.</p>
<script>
const data=__DATA__;
const levels=['easy','medium','hard'];
const keys=['gpt-6-1-sol-low','gpt-6-1-sol-high','claude-sonnet-5-5-low','claude-sonnet-5-5-high'];
const names=['GPT-6.1 Sol · low','GPT-6.1 Sol · high','Claude Sonnet 5.5 · low','Claude Sonnet 5.5 · high'];
const fields={usd:'usd_per_finished_order',tokens:'tokens_per_finished_order',completion:'order_completion_pct',invalid:'invalid_action_pct'};
function format(metric,value){if(value===null||value===undefined)return metric==='usd'||metric==='tokens'?'No kitchen completed':'—';if(metric==='usd')return '$'+value.toFixed(4);if(metric==='tokens')return Math.round(value).toLocaleString();return value.toFixed(1)+'%'}
function render(){let metric=document.querySelector('#metric').value,show=document.querySelector('#ci').checked;
document.querySelector('#rows').innerHTML=keys.map((key,i)=>'<tr><td>'+names[i]+'</td>'+levels.map(level=>{let id=level+'|'+key,s=data.summary[id],c=data.intervals[id][metric],point=s[fields[metric]];
let shown=s.scored?format(metric,point):'Pending';let interval='';if(show&&c){let lo=c.low,hi=c.high;if(metric==='completion'||metric==='invalid'){lo*=100;hi*=100}interval='<div class="interval">95%: '+format(metric,lo)+' – '+format(metric,hi)+'</div>'}
return '<td><div class="value">'+shown+'</div><div class="meta">'+s.scored+'/12 scored · '+s.clear_kitchens+' full clears'+(s.unscored?' · '+s.unscored+' unscored':'')+'</div>'+interval+'</td>'}).join('')+'</tr>').join('')}
document.querySelector('#metric').addEventListener('change',render);document.querySelector('#ci').addEventListener('change',render);render();
</script></html>'''.replace("__DATA__", data)


def main():
    audit = json.loads((RUNS / "audit.json").read_text())
    by_cell = defaultdict(list)
    for ep in audit["episodes"]:
        if ep["scored"]:
            by_cell[ep["level"], ep["key"]].append(ep)
    rng = random.Random(20260929)
    intervals = {f"{level}|{key}": bootstrap(by_cell[level, key], rng)
                 for level in LEVELS for key in KEYS}
    (RUNS / "intervals.json").write_text(json.dumps(intervals, indent=2) + "\n")
    (RUNS / "RESULTS.md").write_text(render(audit, intervals, False))
    (RUNS / "RESULTS_WITH_CI.md").write_text(render(audit, intervals, True))
    (RUNS / "MATRIX.html").write_text(render_html(audit, intervals))
    print(RUNS / "RESULTS.md")
    print(RUNS / "RESULTS_WITH_CI.md")
    print(RUNS / "MATRIX.html")


if __name__ == "__main__":
    main()
