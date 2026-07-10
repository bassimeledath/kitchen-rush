"""Calibration for the calibrated real-speed board (docs/CALIBRATED_SPEED.md).

Two stages, run in order (share one durable spend ledger under calibration/<name>/spend.jsonl):

    python scripts/calibrate.py speed  --name cal-<date> --cap 5
    python scripts/calibrate.py levels --name cal-<date> --cap 35

Stage `speed` runs a couple of throwaway RT episodes per model on fixed calibration seeds, collects
per-turn (n_out, live_latency) points, and fits a constrained 2-parameter clock
`latency = max(0.05, beta0 + beta_out*n_out)` (beta_in pinned 0). Stage `levels` runs the
level-selectable models at each supported level on the FROZEN clock over disjoint tuning seeds and
freezes the best-KR level per budget. Provider-pinned where it matters, serial, warmup off, no
retries (a real infra error is quarantined, never a scored stall). Keys via env.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))                    # sweep helpers
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np                                                          # noqa: E402

from sweep import PRICES, TallyClient, client_extra, kr_of, ledger_total   # noqa: E402
from kitchenrush import procgen                                            # noqa: E402
from kitchenrush.agent import ModelAgent                                   # noqa: E402
from kitchenrush.runner import anchors_for, run_episode                    # noqa: E402
from kitchenrush.tokenizer import TOKENIZER_ID                             # noqa: E402
from kitchenrush.version import ruleset_hash, versions                     # noqa: E402

CAL_DIR = Path("calibration")
CAL_SEEDS = [900, 901]                       # fixed throwaway seeds for speed calibration
TUNE_SEEDS = [1000, 1001, 1002, 1003]        # disjoint from scored seeds 0..11; never in the board
TIER = "medium"                              # THE benchmark kitchen
EP_CAP = 2.0                                 # abort any single episode whose own spend exceeds this
EXPECT_TOKENIZER = "tiktoken-cl100k_base-v1"

# One row per base model. provider=None -> OpenRouter default routing (fine for single-provider or
# KR~0 models; observed provider is still captured). `probe` = reasoning level for speed
# calibration (level-independent decode). `levels` = candidate modes for the level sweep.
ROSTER = [
    {"row": "gpt-5.6-luna",          "id": "openai/gpt-5.6-luna",               "provider": None,   "probe": "minimal", "levels": ["off", "minimal", "low", "medium"]},
    {"row": "claude-sonnet-4.6",     "id": "anthropic/claude-sonnet-4.6",       "provider": None,   "probe": "off",     "levels": []},
    {"row": "claude-haiku-4.5",      "id": "anthropic/claude-haiku-4.5",        "provider": None,   "probe": "default", "levels": []},
    {"row": "gpt-5.4",               "id": "openai/gpt-5.4",                    "provider": None,   "probe": "none",    "levels": ["none", "low", "medium"]},
    {"row": "gpt-5.4-mini",          "id": "openai/gpt-5.4-mini",               "provider": None,   "probe": "none",    "levels": ["none", "minimal", "low", "medium"]},
    {"row": "gpt-oss-120b",          "id": "openai/gpt-oss-120b",               "provider": "Groq", "probe": "low",     "levels": ["low", "medium"]},
    {"row": "glm-5.2",               "id": "z-ai/glm-5.2",                      "provider": None,   "probe": "off",     "levels": ["off", "low"]},
    {"row": "gemini-3.1-flash-lite", "id": "google/gemini-3.1-flash-lite",      "provider": None,   "probe": "off",     "levels": []},
    {"row": "gemini-3.5-flash",      "id": "google/gemini-3.5-flash",           "provider": None,   "probe": "default", "levels": []},
    {"row": "grok-build-0.1",        "id": "x-ai/grok-build-0.1",               "provider": None,   "probe": "default", "levels": []},
    {"row": "qwen3.7-plus",          "id": "qwen/qwen3.7-plus",                 "provider": None,   "probe": "off",     "levels": []},
    {"row": "deepseek-v4-pro",       "id": "deepseek/deepseek-v4-pro",          "provider": None,   "probe": "off",     "levels": []},
    {"row": "deepseek-v4-flash",     "id": "deepseek/deepseek-v4-flash",        "provider": None,   "probe": "off",     "levels": ["off", "low"]},
    {"row": "nemotron-3-super",      "id": "nvidia/nemotron-3-super-120b-a12b",  "provider": None,  "probe": "off",     "levels": []},
    {"row": "nemotron-3-nano",       "id": "nvidia/nemotron-3-nano-30b-a3b",    "provider": None,   "probe": "off",     "levels": []},
    {"row": "mistral-small",         "id": "mistralai/mistral-small-2603",      "provider": None,   "probe": "off",     "levels": []},
    {"row": "llama-4-scout",         "id": "meta-llama/llama-4-scout",          "provider": None,   "probe": "off",     "levels": []},
]


def assert_tokenizer():
    if TOKENIZER_ID != EXPECT_TOKENIZER:
        raise SystemExit(f"FATAL: tokenizer is {TOKENIZER_ID!r}, need {EXPECT_TOKENIZER!r} "
                         f"(install tiktoken). Refusing to spend on a mis-tokenized clock.")


def build_client(mid, mode, provider, tracker, *, ep_cap=EP_CAP, ledger=None):
    spec = mid if ":" in mid else f"openrouter:{mid}"
    extra = client_extra(mode)
    if provider:
        eb = dict(extra.get("extra_body") or {})
        eb["provider"] = {"order": [provider], "allow_fallbacks": False}
        extra = {**extra, "extra_body": eb}
    return TallyClient(spec, tracker, PRICES.get(mid, (0.0, 0.0)), ep_cap=ep_cap, ledger=ledger, **extra)


def collect_points(mid, mode, provider, seeds, *, ledger):
    """RT episodes on `seeds`; per-turn (n_out, live_latency) points + spend + observed providers.
    Per-seed try/except so one transient failure doesn't discard the other seed's data."""
    pts, spend, provs = [], 0.0, set()
    for seed in seeds:
        spec = procgen.generate(seed, TIER, b=1.0)
        mt = {"in": 0, "out": 0, "reason": 0, "cost": 0.0, "calls": 0, "lat": 0.0}
        try:
            client = build_client(mid, mode, provider, mt, ledger=ledger)
            # retries survive transient 429s (fit drops the backoff-inflated points); fail_fast still
            # quarantines a genuine outage (all retries exhausted) rather than recording a stall.
            agent = ModelAgent(client, track="rt", temperature=0.2, num_retries=3, fail_fast=True)
            res = run_episode(spec, agent, record_steps=True, warmup=False)
            for st in res.steps:
                if st.get("n_out") is not None and st.get("live_latency_s") is not None:
                    pts.append((int(st["n_out"]), float(st["live_latency_s"])))
        except Exception as exc:  # noqa: BLE001 - infra error on this seed; keep the other seed
            print(f"    seed {seed} error: {type(exc).__name__}: {str(exc)[:80]}", flush=True)
        spend += mt["cost"]
        if mt.get("provider_served"):
            provs.add(mt["provider_served"])
        time.sleep(3)          # pace to avoid provider rate limits during calibration
    return pts, spend, provs


def fit_clock(pts):
    xs = np.array([p[0] for p in pts], float)
    ys = np.array([p[1] for p in pts], float)
    flags = []
    # Drop retry/backoff-inflated latency outliers before the real fit: initial fit, then keep only
    # points whose actual/predicted ratio is within [0.33, 3]. A rate-limited call that litellm
    # retried carries seconds of backoff in its measured latency and would bias the clock upward.
    if len(xs) >= 8:
        ib0, ibout = np.linalg.lstsq(np.vstack([np.ones_like(xs), xs]).T, ys, rcond=None)[0]
        ipred = np.maximum(ib0 + ibout * xs, 1e-6)
        keep = (ys / ipred >= 0.33) & (ys / ipred <= 3.0)
        if keep.sum() >= max(8, int(0.5 * len(xs))):
            dropped = int(len(xs) - keep.sum())
            xs, ys = xs[keep], ys[keep]
            if dropped:
                flags.append(f"DROPPED_{dropped}_OUTLIERS")
    b0, bout = np.linalg.lstsq(np.vstack([np.ones_like(xs), xs]).T, ys, rcond=None)[0]
    if bout <= 0 or b0 < 0.05:
        b0 = 0.05
        den = float(np.sum(xs * xs))
        bout = float(np.sum((ys - b0) * xs) / den) if den > 0 else 0.006
        if bout <= 0:
            bout = float(np.sum(np.maximum(ys - b0, 0.0)) / np.sum(xs)) if np.sum(xs) > 0 else 0.006
            flags.append("CLOCK_FIT_WEAK")
        if abs(b0 - 0.05) < 1e-9:
            flags.append("CLOCK_INTERCEPT_FLOOR")
    bout = max(bout, 1e-6)
    pred = b0 + bout * xs
    ss_res, ss_tot = float(np.sum((ys - pred) ** 2)), float(np.sum((ys - ys.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    span = float(xs.max() / max(xs.min(), 1))
    ratios = ys / np.maximum(pred, 1e-6)
    ratio_cv = float(ratios.std() / ratios.mean()) if ratios.mean() > 0 else 0.0
    if len(pts) < 12:
        flags.append("CAL_TOO_FEW")
    if span < 3 or r2 < 0.5:
        flags.append("CLOCK_FIT_WEAK")
    if ratio_cv > 0.5:            # real network jitter runs ~0.3-0.4; only flag genuine instability
        flags.append("CAL_UNSTABLE")
    return {"beta0": round(float(b0), 6), "beta_in": 0.0, "beta_out": round(float(bout), 8),
            "decode_tps": round(1.0 / bout, 1), "valid_samples": len(pts), "r2": round(r2, 3),
            "residual_ratio_cv": round(ratio_cv, 3), "token_span_ratio": round(span, 2),
            "latency_p50": round(float(np.percentile(ys, 50)), 3),
            "latency_p95": round(float(np.percentile(ys, 95)), 3), "flags": sorted(set(flags))}


def cmd_speed(args) -> int:
    assert_tokenizer()
    out = CAL_DIR / args.name
    out.mkdir(parents=True, exist_ok=True)
    ledger = str(out / "spend.jsonl")
    rows = [r for r in ROSTER if not args.only or r["row"] in args.only.split(",")]
    art = {"calibration_id": args.name, "stage": "speed", "ruleset": ruleset_hash(),
           "versions": versions(), "tokenizer": TOKENIZER_ID, "tier": TIER,
           "cal_seeds": CAL_SEEDS, "tune_seeds": TUNE_SEEDS, "models": {}}
    if (out / "calibration.json").exists():                 # RESUME: keep already-calibrated models
        prev = json.loads((out / "calibration.json").read_text())
        art["models"] = prev.get("models", {})
    for r in rows:
        if art["models"].get(r["row"], {}).get("clock", {}).get("beta_out", 0) > 0:
            print(f"  {r['row']:24} — already calibrated, skip"); continue
        if ledger_total(ledger) >= args.cap:
            print(f"CAP ${args.cap} reached (spent ${ledger_total(ledger):.2f}) — stopping speed cal")
            break
        t0 = time.time()
        pts, spend, provs = collect_points(r["id"], r["probe"], r["provider"], CAL_SEEDS, ledger=ledger)
        clock = fit_clock(pts) if len(pts) >= 4 else {"flags": ["CAL_TOO_FEW"], "valid_samples": len(pts)}
        art["models"][r["row"]] = {
            "model_id": r["id"], "provider_pinned": r["provider"], "provider_observed": sorted(provs),
            "probe_level": r["probe"], "levels": r["levels"], "clock": clock,
            "selected_level": {"1": None, "5": None}}
        art["total_cost_usd"] = round(ledger_total(ledger), 3)
        (out / "calibration.json").write_text(json.dumps(art, indent=2))   # durable checkpoint per model
        print(f"  {r['row']:24} n={clock.get('valid_samples')} tps={clock.get('decode_tps')} "
              f"beta0={clock.get('beta0')} r2={clock.get('r2')} prov={sorted(provs)} "
              f"flags={clock.get('flags')} ${spend:.3f} {time.time()-t0:.0f}s", flush=True)
    print(f"WROTE {out/'calibration.json'}  total=${ledger_total(ledger):.2f}")
    return 0


def _tune_episode(r, level, B, seed, clock, ledger, cap):
    """One frozen-clock tuning episode -> (level, "B", kr, cost) or None if capped/errored."""
    if ledger_total(ledger) + EP_CAP > cap:
        return None
    spec = procgen.generate(seed, TIER, b=B)
    s_null, s_ref = anchors_for(spec)
    mt = {"in": 0, "out": 0, "reason": 0, "cost": 0.0, "calls": 0, "lat": 0.0}
    try:
        client = build_client(r["id"], level, r["provider"], mt, ledger=ledger)
        agent = ModelAgent(client, track="calibrated", temperature=0.2, clock=clock,
                           num_retries=3, fail_fast=True)
        res = run_episode(spec, agent, warmup=False)
        return (level, str(int(B)), kr_of(res.report["score_raw"], s_null, s_ref) or 0.0, mt["cost"])
    except Exception as exc:  # noqa: BLE001
        print(f"    {r['row']} {level} B={B:.0f} s{seed}: {str(exc)[:70]}", flush=True)
        return None


def cmd_levels(args) -> int:
    assert_tokenizer()
    out = CAL_DIR / args.name
    art = json.loads((out / "calibration.json").read_text())
    ledger = str(out / "spend.jsonl")
    for r in ROSTER:
        rec = art["models"].get(r["row"])
        if not rec or not r["levels"] or "beta_out" not in rec.get("clock", {}):
            continue
        sl = rec.get("selected_level") or {}
        if sl.get("1") is not None and sl.get("5") is not None:   # RESUME / preset: already selected
            print(f"  {r['row']:24} — levels already selected {sl}, skip"); continue
        c = rec["clock"]
        clock = (c["beta0"], c["beta_in"], c["beta_out"])
        tasks = [(level, B, seed) for level in r["levels"] for B in (1.0, 5.0) for seed in TUNE_SEEDS]
        agg = {}   # (level, "B") -> {"krs":[], "costs":[]}
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(_tune_episode, r, lv, B, s, clock, ledger, args.cap) for lv, B, s in tasks]
            for fut in as_completed(futs):
                res = fut.result()
                if res is None:
                    continue
                level, Bk, kr, cost = res
                a = agg.setdefault((level, Bk), {"krs": [], "costs": []})
                a["krs"].append(kr); a["costs"].append(cost)
        results = {}
        for (level, Bk), a in agg.items():
            results.setdefault(level, {})[Bk] = {
                "kr": sum(a["krs"]) / len(a["krs"]), "cost": sum(a["costs"]) / len(a["costs"]),
                "n": len(a["krs"])}
        sel = {}   # best-KR level per budget; require >=2 valid seeds; tie-break by lower mean cost
        for Bk in ("1", "5"):
            cands = [(lv, d[Bk]["kr"], d[Bk]["cost"]) for lv, d in results.items()
                     if d.get(Bk) and d[Bk]["n"] >= 2]
            cands.sort(key=lambda t: (-t[1], t[2] if t[2] is not None else 1e9))
            sel[Bk] = cands[0][0] if cands else None
        rec["selected_level"] = sel
        rec["level_results"] = results
        art["total_cost_usd"] = round(ledger_total(ledger), 3)
        (out / "calibration.json").write_text(json.dumps(art, indent=2))   # durable checkpoint per model
        print(f"  {r['row']:24} -> selected {sel}  ({ {lv:{b:round(d[b]['kr']) for b in d} for lv,d in results.items()} })", flush=True)
    art["stage"] = "levels"
    (out / "calibration.json").write_text(json.dumps(art, indent=2))
    print(f"WROTE {out/'calibration.json'}  total=${ledger_total(ledger):.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("speed"); sp.add_argument("--name", required=True)
    sp.add_argument("--only", default=""); sp.add_argument("--cap", type=float, default=5.0)
    lv = sub.add_parser("levels"); lv.add_argument("--name", required=True)
    lv.add_argument("--cap", type=float, default=35.0)
    lv.add_argument("--workers", type=int, default=6, help="concurrent tuning episodes (frozen clock)")
    args = ap.parse_args()
    return cmd_speed(args) if args.cmd == "speed" else cmd_levels(args)


if __name__ == "__main__":
    raise SystemExit(main())
