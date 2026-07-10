"""Build the calibrated real-speed board: two per-budget tables + Pareto plots + appendix.

    python scripts/build_calibrated_board.py --run calibrated-<date> \
        --calibration calibration/<id>/calibration.json

Reads runs/<run>/episodes.jsonl (scored) + the calibration artifact, writes:
  leaderboard/results/calibrated_board.{json,md}  and  docs/assets/calibrated_b{1,5}.png
Rank bands: adjacent models stay in one band unless mean-KR gap >=10 AND the paired seed-bootstrap
CI lower bound > 0. Efficiency is shown as the KR-vs-$ Pareto plot, not a column.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_NOTE = ("Dated calibrated real-speed deployment snapshot — each model's game clock is its "
                 "own measured serving speed (frozen). NOT a fully reproducible or provider-neutral "
                 "benchmark; re-measuring on another day/endpoint/region can change results.")


def boot_mean_ci(vals, draws=2000, seed=0):
    rng = np.random.default_rng(seed)
    v = np.array(vals, float)
    if len(v) < 2:
        return (float(v.mean()) if len(v) else 0.0, None)
    means = v[rng.integers(0, len(v), size=(draws, len(v)))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(v.mean()), float(max(hi - v.mean(), v.mean() - lo))


def paired_ci_lower(a_by_seed, b_by_seed, draws=2000, seed=0):
    """Lower bound of the 95% CI on mean(KR_a - KR_b) over common seeds."""
    common = sorted(set(a_by_seed) & set(b_by_seed))
    if len(common) < 2:
        return -1e9
    d = np.array([a_by_seed[s] - b_by_seed[s] for s in common], float)
    rng = np.random.default_rng(seed)
    means = d[rng.integers(0, len(d), size=(draws, len(d)))].mean(axis=1)
    return float(np.percentile(means, 2.5))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--calibration", required=True)
    ap.add_argument("--date", required=True, help="snapshot date YYYY-MM-DD (pass in; no clock in-script)")
    args = ap.parse_args()

    eps = [json.loads(l) for l in (ROOT / "runs" / args.run / "episodes.jsonl").open() if l.strip()]
    cal = json.loads(Path(args.calibration).read_text())

    # group per (model, B)
    cells: dict[tuple, list] = defaultdict(list)
    for e in eps:
        if e.get("kr") is not None:
            cells[(e["model"], e["B"])].append(e)

    budgets = sorted({b for _, b in cells})
    board = {"snapshot_date": args.date, "note": SNAPSHOT_NOTE, "run": args.run,
             "calibration_id": cal.get("calibration_id"), "ruleset": cal.get("ruleset"),
             "budgets": budgets, "tables": {}, "appendix": {}}

    for B in budgets:
        rows = []
        for (m, b), es in cells.items():
            if b != B:
                continue
            kr_by_seed = {e["seed"]: e["kr"] for e in es}
            mean, ci = boot_mean_ci(list(kr_by_seed.values()))
            served, total = sum(e["served"] for e in es), sum(e["total"] for e in es)
            tin = sum(e["ep_tokens_in"] for e in es); tout = sum(e["ep_tokens_out"] for e in es)
            cost = sum(e["ep_cost"] for e in es)
            drifts = [e["drift_ratio"] for e in es if e.get("drift_ratio") is not None]
            lvl = (cal["models"].get(m, {}).get("selected_level") or {}).get(str(int(B)))
            rows.append({
                "model": m, "B": B, "n": len(es), "KR": round(mean, 1),
                "ci": round(ci, 1) if ci is not None else None,
                "serve_pct": round(100 * served / max(1, total)),
                "usd_per_mtok": round(1e6 * cost / max(1, tin + tout), 3),
                "drift": round(float(np.median(drifts)), 3) if drifts else None,
                "level": lvl, "kr_by_seed": kr_by_seed,
            })
        rows.sort(key=lambda r: -r["KR"])
        # Rank bands, measured from each band's TOP member (not the neighbour): a new band starts
        # when a model is >=10 KR below the current band's top AND that gap is significant (paired
        # seed-bootstrap CI lower bound > 0). Comparing to the band top (rather than the adjacent
        # row) prevents a gradual decline from collapsing into one giant band.
        band = 1
        band_top = rows[0]
        rows[0]["band"] = 1
        for r in rows[1:]:
            gap = band_top["KR"] - r["KR"]
            lo = paired_ci_lower(band_top["kr_by_seed"], r["kr_by_seed"])
            if gap >= 10 and lo > 0:
                band += 1
                band_top = r
            r["band"] = band
        for r in rows:
            r.pop("kr_by_seed", None)
        board["tables"][str(int(B))] = rows

    # calibration appendix (one row per model)
    for m, rec in cal["models"].items():
        c = rec.get("clock", {})
        board["appendix"][m] = {
            "provider": rec.get("provider"), "decode_tps": c.get("decode_tps"),
            "beta0": c.get("beta0"), "beta_out": c.get("beta_out"), "n": c.get("valid_samples"),
            "r2": c.get("r2"), "selected_level": rec.get("selected_level"),
            "cal_flags": c.get("flags", []),
        }

    out = ROOT / "leaderboard" / "results" / "calibrated_board.json"
    out.write_text(json.dumps(board, indent=2))

    # markdown
    lines = ["# Kitchen Rush — calibrated real-speed board", "",
             f"_{SNAPSHOT_NOTE}_", "",
             f"Snapshot **{args.date}** · calibration `{cal.get('calibration_id')}` · ruleset "
             f"`{cal.get('ruleset')}` · single kitchen (the benchmark) · serial · one provider pinned "
             f"per model · blended $/token from provider-billed cost. Rank = band (rows in a band are "
             f"statistically tied). `$/Mtok` = billed $ per 1M (prompt+completion) tokens.", ""]
    for B in budgets:
        lines += [f"## B = {int(B)}s", "",
                  "| rank | model | KR | ±95%CI | serve% | $/Mtok | level |",
                  "|---|---|--:|--:|--:|--:|--|"]
        rowsB = board["tables"][str(int(B))]
        # render band as ordinal range
        from itertools import groupby
        band_ranks = {}
        idx = 1
        for bnd, grp in groupby(rowsB, key=lambda r: r["band"]):
            g = list(grp); n = len(g)
            label = f"{idx}" if n == 1 else f"{idx}–{idx+n-1}"
            for r in g:
                band_ranks[id(r)] = label
            idx += n
        for r in rowsB:
            ci = "—" if r["ci"] is None else f"±{r['ci']}"
            lines.append(f"| {band_ranks[id(r)]} | {r['model']} | {r['KR']:.0f} | {ci} | "
                         f"{r['serve_pct']}% | {r['usd_per_mtok']:g} | {r['level'] or '—'} |")
        lines.append("")
    lines += ["## Calibration appendix", "",
              "| model | provider | decode tok/s | β0 | selected level (B1/B5) | flags |",
              "|---|---|--:|--:|--|--|"]
    for m, a in board["appendix"].items():
        sl = a.get("selected_level") or {}
        lines.append(f"| {m} | {a.get('provider') or 'default'} | {a.get('decode_tps') or '—'} | "
                     f"{a.get('beta0') or '—'} | {sl.get('1') or '—'}/{sl.get('5') or '—'} | "
                     f"{','.join(a.get('cal_flags') or []) or '—'} |")
    (ROOT / "leaderboard" / "results" / "calibrated_board.md").write_text("\n".join(lines) + "\n")

    # Pareto plots
    try:
        import matplotlib.pyplot as plt
        for B in budgets:
            rowsB = board["tables"][str(int(B))]
            xs = [r["usd_per_mtok"] for r in rowsB]; ys = [r["KR"] for r in rowsB]
            cis = [r["ci"] or 0 for r in rowsB]
            fig, ax = plt.subplots(figsize=(7, 5), dpi=200)
            ax.errorbar(xs, ys, yerr=cis, fmt="o", color="#2f6fb2", ecolor="#bbb", capsize=2)
            for r in rowsB:
                ax.annotate(f"{r['model']}" + (f"·{r['level']}" if r['level'] else ""),
                            (r["usd_per_mtok"], r["KR"]), fontsize=6, xytext=(4, 2),
                            textcoords="offset points")
            # non-dominated frontier
            front = sorted(rowsB, key=lambda r: (r["usd_per_mtok"], -r["KR"]))
            fx, fy, best = [], [], -1e9
            for r in front:
                if r["KR"] > best:
                    fx.append(r["usd_per_mtok"]); fy.append(r["KR"]); best = r["KR"]
            ax.plot(fx, fy, "--", color="#e07b39", lw=1, label="frontier")
            ax.set_xscale("log")
            ax.set_xlabel("$ per 1M tokens (billed, log)"); ax.set_ylabel("KR (0–100)")
            ax.set_title(f"Kitchen Rush — calibrated real-speed · B={int(B)}s · {args.date}", fontsize=9)
            ax.grid(True, color="#eee"); ax.set_axisbelow(True); ax.legend(fontsize=7)
            fig.tight_layout()
            p = ROOT / "docs" / "assets" / f"calibrated_b{int(B)}.png"
            fig.savefig(p, facecolor="white"); print("wrote", p.relative_to(ROOT))
    except Exception as exc:  # noqa: BLE001
        print("plot skipped:", exc)

    print(f"wrote leaderboard/results/calibrated_board.{{json,md}}  ({len(eps)} episodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
