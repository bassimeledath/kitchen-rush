"""QA pass over a scored calibrated-board run (docs/CALIBRATED_SPEED.md §E).

    python scripts/qa_board.py --run calibrated-<date>

Reads runs/<run>/episodes.jsonl (+ quarantine.jsonl) and emits runs/<run>/qa.{json,md} with
per-(model,B) and per-episode flags. Flags surface what to rerun; nothing is auto-rerun here.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def robust_outliers(vals_by_seed):
    """Return seeds whose KR is a robust-z (MAD) outlier > 3.5 (or differ when MAD==0)."""
    seeds = list(vals_by_seed)
    v = np.array([vals_by_seed[s] for s in seeds], float)
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    out = []
    for s, x in zip(seeds, v):
        if mad == 0:
            if x != med:
                out.append(s)
        elif abs(x - med) / (1.4826 * mad) > 3.5:
            out.append(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    run = ROOT / "runs" / args.run
    eps = [json.loads(l) for l in (run / "episodes.jsonl").open() if l.strip()]
    quced = []
    qpath = run / "quarantine.jsonl"
    if qpath.exists():
        quced = [json.loads(l) for l in qpath.open() if l.strip()]

    cells = defaultdict(list)
    for e in eps:
        cells[(e["model"], e["B"])].append(e)

    findings = []          # {level, model, B, flag, detail}
    for (m, B), es in cells.items():
        drifts = [e["drift_ratio"] for e in es if e.get("drift_ratio") is not None]
        if drifts:
            md = float(np.median(drifts))
            if not (0.67 <= md <= 1.50):
                findings.append({"sev": "BLOCKER", "model": m, "B": B, "flag": "SEVERE_SPEED_DRIFT",
                                 "detail": f"median live/clock={md:.2f} (outside 0.67-1.50) — recalibrate+rerun"})
            elif not (0.80 <= md <= 1.25):
                findings.append({"sev": "WARN", "model": m, "B": B, "flag": "SPEED_DRIFT",
                                 "detail": f"median live/clock={md:.2f} (outside 0.80-1.25)"})
        # provider mismatch
        for e in es:
            pin, served = e.get("provider_pinned"), e.get("provider_served")
            if pin and served and served != pin:
                findings.append({"sev": "BLOCKER", "model": m, "B": B, "flag": "PROVIDER_MISMATCH",
                                 "detail": f"seed {e['seed']}: served {served!r} != pinned {pin!r}"})
        # KR outliers (advisory — inspect, do not auto-rerun)
        krs = {e["seed"]: e["kr"] for e in es if e.get("kr") is not None}
        for s in robust_outliers(krs) if len(krs) >= 4 else []:
            findings.append({"sev": "WARN", "model": m, "B": B, "flag": "KR_SEED_OUTLIER",
                             "detail": f"seed {s}: KR={krs[s]} vs median {np.median(list(krs.values())):.0f} — inspect transcript"})
        # reasoning enabled but zero reasoning tokens reported (clock may undercount)
        lvl = es[0].get("level")
        if lvl in ("minimal", "low", "medium", "high", "on") and all(e.get("ep_reason", 0) == 0 for e in es):
            findings.append({"sev": "WARN", "model": m, "B": B, "flag": "REASONING_USAGE_MISSING",
                             "detail": f"level={lvl} but 0 reasoning tokens across all seeds"})
        # high stall/noop rate (possible silent infra trouble that slipped past fail_fast)
        noopy = [e for e in es if e["turns"] and e["noop_turns"] / e["turns"] > 0.5]
        if noopy:
            findings.append({"sev": "WARN", "model": m, "B": B, "flag": "HIGH_NOOP",
                             "detail": f"{len(noopy)}/{len(es)} episodes >50% no-op turns"})

    sev_rank = {"BLOCKER": 0, "WARN": 1}
    findings.sort(key=lambda f: (sev_rank.get(f["sev"], 9), f["model"], f["B"]))
    qa = {"run": args.run, "n_episodes": len(eps), "n_quarantined": len(quced),
          "n_cells": len(cells), "findings": findings}
    (run / "qa.json").write_text(json.dumps(qa, indent=2))

    lines = [f"# QA — {args.run}", "",
             f"{len(eps)} episodes · {len(cells)} cells · {len(quced)} quarantined · "
             f"{len(findings)} findings", ""]
    if quced:
        lines += ["## Quarantined (infra-invalid — rerun same model×B×seed)", ""]
        for q in quced:
            lines.append(f"- {q['model']} B={q['B']:.0f} s{q['seed']} ({q.get('level')}): {q.get('error')}")
        lines.append("")
    lines += ["## Flags", ""]
    if not findings:
        lines.append("_No flags — board is clean._")
    else:
        lines += ["| sev | model | B | flag | detail |", "|---|---|--|---|---|"]
        for f in findings:
            lines.append(f"| {f['sev']} | {f['model']} | {f['B']:.0f} | {f['flag']} | {f['detail']} |")
    (run / "qa.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {run/'qa.md'} — {len(findings)} findings, {len(quced)} quarantined")
    # exit nonzero if any BLOCKER (so an orchestrator can gate on it)
    return 1 if any(f["sev"] == "BLOCKER" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
