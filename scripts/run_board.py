"""Scored run for the calibrated real-speed board (docs/CALIBRATED_SPEED.md).

Reads a frozen `levels`-stage calibration artifact (per-model clock + selected level per budget) and
runs the scored seeds on each model's calibrated clock. Serial, warmup off, no retries. Spend is
tracked in the shared durable ledger (calibration/<id>/spend.jsonl) and hard-capped: an episode is
refused if the ledger + a per-episode reserve would exceed --cap, and each episode is itself capped.

    python scripts/run_board.py --calibration calibration/<id>/calibration.json \
        --name calibrated-<date> --seed-list 0,1,2,3 --budgets 1,5 --cap 100     # pilot (seeds 0-3)
    python scripts/run_board.py --calibration ... --name calibrated-<date> \
        --seed-list 4,5,6,7,8,9,10,11 --budgets 1,5 --cap 100                    # extend (resumes)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np                                                # noqa: E402

from calibrate import ROSTER, TIER, EP_CAP, EXPECT_TOKENIZER, build_client   # noqa: E402
from kitchenrush import procgen                                  # noqa: E402
from kitchenrush.agent import ModelAgent                         # noqa: E402
from kitchenrush.runner import anchors_for, run_episode          # noqa: E402
from kitchenrush.tokenizer import TOKENIZER_ID                   # noqa: E402
from kitchenrush.version import ruleset_hash, versions           # noqa: E402
from sweep import kr_of, ledger_total                            # noqa: E402

ROW_BY_NAME = {r["row"]: r for r in ROSTER}


def run_one(row, model_id, provider, level, B, seed, clock, ledger):
    spec = procgen.generate(seed, TIER, b=float(B))
    s_null, s_ref = anchors_for(spec)
    mt = {"in": 0, "out": 0, "reason": 0, "cost": 0.0, "calls": 0, "lat": 0.0}
    client = build_client(model_id, level, provider, mt, ep_cap=EP_CAP, ledger=ledger)
    # fail_fast=False: transient provider blips degrade to a brief stall so the episode still
    # completes (QA's HIGH_NOOP flag catches a genuinely-down endpoint) rather than quarantining
    # an otherwise-good episode. max_turns bounds any pathological long episode.
    agent = ModelAgent(client, track="calibrated", temperature=0.2,
                       clock=(clock["beta0"], clock["beta_in"], clock["beta_out"]),
                       num_retries=3, fail_fast=False)
    t0 = time.time()
    res = run_episode(spec, agent, record_steps=True, warmup=False, max_turns=250)
    rep = res.report
    drifts = [st["live_latency_s"] / st["latency_s"] for st in res.steps
              if st.get("live_latency_s") and st.get("latency_s")]
    return {
        "model": row, "model_id": model_id, "provider_pinned": provider,
        "provider_served": mt.get("provider_served"), "level": level, "B": float(B),
        "tier": TIER, "seed": seed, "kr": kr_of(rep["score_raw"], s_null, s_ref),
        "score_raw": rep["score_raw"], "s_null": round(s_null, 2), "s_ref": round(s_ref, 2),
        "served": rep["counters"]["orders_served"], "total": rep["counters"]["orders_total"],
        "turns": rep["turns"], "invalid": rep["counters"]["invalid_actions"],
        "noop_turns": sum(1 for st in res.steps if not st["calls"]),
        "ep_tokens_in": mt["in"], "ep_tokens_out": mt["out"], "ep_reason": mt["reason"],
        "ep_cost": mt["cost"], "ep_lat_s": round(mt["lat"], 2),
        "drift_ratio": round(float(np.median(drifts)), 3) if drifts else None,
        "wall_s": round(time.time() - t0, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibration", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--seed-list", required=True)
    ap.add_argument("--budgets", default="1,5")
    ap.add_argument("--cap", type=float, default=100.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=6, help="concurrent scored episodes (frozen clock)")
    args = ap.parse_args()

    # ---- fail-closed preflight (never spend on a mis-set run) ----
    if TOKENIZER_ID != EXPECT_TOKENIZER:
        raise SystemExit(f"FATAL: tokenizer {TOKENIZER_ID!r} != {EXPECT_TOKENIZER!r}")
    art = json.loads(Path(args.calibration).read_text())
    if art.get("stage") != "levels":
        raise SystemExit(f"FATAL: calibration stage is {art.get('stage')!r}, need 'levels' (run level cal first)")
    if art.get("tokenizer") != EXPECT_TOKENIZER:
        raise SystemExit(f"FATAL: calibration tokenizer {art.get('tokenizer')!r} != {EXPECT_TOKENIZER!r}")
    seeds = sorted(dict.fromkeys(int(s) for s in args.seed_list.split(",")))   # dedup, keep order-safe
    budgets = sorted(dict.fromkeys(float(b) for b in args.budgets.split(",")))
    overlap = set(seeds) & (set(art.get("cal_seeds", [])) | set(art.get("tune_seeds", [])))
    if overlap:
        raise SystemExit(f"FATAL: scored seeds overlap calibration/tuning seeds: {sorted(overlap)}")
    models = art["models"]

    ledger = str(Path(args.calibration).parent / "spend.jsonl")
    out = Path("runs") / args.name
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "run_meta.json"
    meta = {"name": args.name, "calibration": args.calibration, "calibration_id": art.get("calibration_id"),
            "ruleset": ruleset_hash(), "tokenizer": TOKENIZER_ID, "versions": versions(),
            "seeds": seeds, "budgets": budgets, "cap": args.cap}
    if meta_path.exists():   # resume must be the same contract
        old = json.loads(meta_path.read_text())
        for k in ("calibration_id", "ruleset", "tokenizer"):
            if old.get(k) != meta[k]:
                raise SystemExit(f"FATAL: resume mismatch on {k}: {old.get(k)!r} != {meta[k]!r}")
    else:
        meta_path.write_text(json.dumps(meta, indent=2))

    eppath = out / "episodes.jsonl"
    logf = (out / "progress.log").open("a")

    def log(m):
        line = f"[{time.strftime('%H:%M:%S')}] {m}"
        print(line, flush=True); logf.write(line + "\n"); logf.flush()

    done = set()
    if eppath.exists():
        for ln in eppath.read_text().splitlines():
            if ln.strip():
                try:
                    r = json.loads(ln)
                    done.add((r["model"], r["B"], r["seed"]))
                except Exception:  # noqa: BLE001 - tolerate a torn final line
                    pass
        if done:
            log(f"RESUME: {len(done)} episodes already scored")

    rows = [r for r in ROSTER if not args.only or r["row"] in args.only.split(",")]
    # Build the task list (skip invalid clocks / missing selected level / already-done).
    tasks = []
    for r in rows:
        rec = models.get(r["row"])
        clock = (rec or {}).get("clock", {})
        if not rec or clock.get("beta_out", 0) <= 0:
            log(f"SKIP {r['row']} — no valid clock"); continue
        model_id = rec.get("model_id", r["id"])
        provider = rec.get("provider_pinned", r["provider"])
        for B in budgets:
            selected = (rec.get("selected_level") or {}).get(str(int(B)))
            level = selected if selected is not None else (rec.get("probe_level") if not r["levels"] else None)
            if level is None:
                log(f"SKIP {r['row']} B={B:.0f} — level-selectable but no selected level"); continue
            for seed in seeds:
                if (r["row"], float(B), seed) not in done:
                    tasks.append((r, model_id, provider, level, B, seed, clock))
    # Interleave by model (round-robin) so concurrent workers hit DIFFERENT providers instead of
    # hammering one endpoint (which caused a 429 storm when tasks were model-grouped).
    from collections import defaultdict, deque
    bym = defaultdict(deque)
    for t in tasks:
        bym[t[0]["row"]].append(t)
    queues, tasks = list(bym.values()), []
    while queues:
        for q in queues:
            if q:
                tasks.append(q.popleft())
        queues = [q for q in queues if q]
    epf = eppath.open("a")
    lock = threading.Lock()
    log(f"scored run '{args.name}' cap=${args.cap} (ledger ${ledger_total(ledger):.2f}) tasks={len(tasks)} "
        f"workers={args.workers} ruleset={ruleset_hash()}")

    def do(t):
        r, model_id, provider, level, B, seed, clock = t
        if ledger_total(ledger) + EP_CAP > args.cap:      # hard cap gate (bounded overshoot = workers*EP_CAP)
            return ("cap",)
        try:
            ep = run_one(r["row"], model_id, provider, level, B, seed, clock, ledger)
        except Exception as exc:  # noqa: BLE001 - infra-invalid; quarantine (spend already durable in ledger)
            with lock:
                with (out / "quarantine.jsonl").open("a") as qf:
                    qf.write(json.dumps({"model": r["row"], "B": B, "seed": seed, "level": level,
                                         "error": f"{type(exc).__name__}: {str(exc)[:200]}"}) + "\n")
            return ("err", r["row"], B, seed, f"{type(exc).__name__}: {str(exc)[:80]}")
        return ("ok", ep)

    capped = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(do, t) for t in tasks]):
            res = fut.result()
            if res[0] == "ok":
                ep = res[1]
                with lock:
                    epf.write(json.dumps(ep) + "\n"); epf.flush()
                    done.add((ep["model"], ep["B"], ep["seed"]))
                pin, served = ep["provider_pinned"], ep["provider_served"]
                pmis = "" if (not pin or served in (None, pin)) else f" PROV!={served}"
                log(f"  {ep['model']:22} B={ep['B']:.0f} s{ep['seed']} {ep['level']:7} KR={ep['kr']} "
                    f"srv={ep['served']}/{ep['total']} drift={ep['drift_ratio']} ${ep['ep_cost']:.3f} "
                    f"cum=${ledger_total(ledger):.2f}{pmis}")
            elif res[0] == "err":
                log(f"  ERR {res[1]} B={res[2]:.0f} s{res[3]}: {res[4]}")
            else:
                capped += 1
    if capped:
        log(f"CAP: {capped} episodes skipped — ledger+reserve would exceed ${args.cap}")
    epf.close()
    log(f"DONE '{args.name}' — {len(done)} scored total; {capped} capped; cumulative spend ${ledger_total(ledger):.2f}")
    logf.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
