#!/usr/bin/env python3
"""Kitchen Rush v3 bank builder, offline calibrator, and OpenRouter experiment runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kitchenrush.v3.adapters import ModelSpec, OpenRouterToolAdapter  # noqa: E402
from kitchenrush.v3.env import generate_kitchen, serialize_spec  # noqa: E402
from kitchenrush.v3.metrics import format_markdown_table, paired_delta_bootstrap, summarize_campaign  # noqa: E402
from kitchenrush.v3.runner import load_bank, run_campaign, save_bank  # noqa: E402
from kitchenrush.v3.solvers import run_greedy_overlap_solver, run_sequential_solver  # noqa: E402

DEFAULT_BANK = ROOT / "data/v3/calibration_bank_v1.json"
DEFAULT_RUN_DIR = ROOT / "runs/v3/calibration_v1"

MODELS: dict[str, ModelSpec] = {
    "gpt-6-luna-low": ModelSpec(
        key="gpt-6-luna-low",
        model_id="openai/gpt-6-luna",
        tier="small",
        input_price_per_m=0.10,
        output_price_per_m=0.50,
        max_tokens=2048,
        reasoning={"effort": "low"},
        temperature=None,
    ),
    "claude-haiku-5.5-low": ModelSpec(
        key="claude-haiku-5.5-low",
        model_id="anthropic/claude-haiku-5.5",
        tier="small",
        input_price_per_m=0.10,
        output_price_per_m=0.50,
        max_tokens=2048,
        reasoning={"effort": "low"},
        temperature=None,
    ),
    "gemini-3.8-flash-low": ModelSpec(
        key="gemini-3.8-flash-low",
        model_id="google/gemini-3.8-flash",
        tier="medium",
        input_price_per_m=0.825,
        output_price_per_m=4.125,
        max_tokens=2048,
        reasoning={"effort": "low"},
        temperature=None,
    ),
    "gpt-6.1-terra-low": ModelSpec(
        key="gpt-6.1-terra-low",
        model_id="openai/gpt-6.1-terra",
        tier="medium",
        input_price_per_m=1.00,
        output_price_per_m=5.00,
        max_tokens=2500,
        reasoning={"effort": "low"},
        temperature=None,
    ),
    "gpt-6.1-sol-low": ModelSpec(
        key="gpt-6.1-sol-low",
        model_id="openai/gpt-6.1-sol",
        tier="frontier",
        input_price_per_m=2.00,
        output_price_per_m=10.00,
        max_tokens=3072,
        reasoning={"effort": "low"},
        temperature=None,
    ),
    "gpt-6.1-sol-medium": ModelSpec(
        key="gpt-6.1-sol-medium",
        model_id="openai/gpt-6.1-sol",
        tier="frontier",
        input_price_per_m=2.00,
        output_price_per_m=10.00,
        max_tokens=4096,
        reasoning={"effort": "medium"},
        temperature=None,
    ),
    "claude-sonnet-5.5-low": ModelSpec(
        key="claude-sonnet-5.5-low",
        model_id="anthropic/claude-sonnet-5.5",
        tier="frontier",
        input_price_per_m=2.00,
        output_price_per_m=10.00,
        max_tokens=3072,
        reasoning={"effort": "low"},
        temperature=None,
    ),
}


def cmd_build_bank(args: argparse.Namespace) -> None:
    bank: dict[int, list[dict]] = {}
    exclusions: dict[str, list[dict]] = {}
    print(f"Building frozen v3 bank ({args.seeds_per_level} seeds/level, D=1..5)...")
    for level in (1, 2, 3, 4, 5):
        retained = []
        excl = []
        seed = args.start_seed
        seq_s = seq_n = seq_c = 0
        grd_s = grd_n = grd_c = 0
        w_acts = []
        while len(retained) < args.seeds_per_level:
            try:
                spec, batches = generate_kitchen(seed, level)
            except Exception as exc:
                excl.append({"seed": seed, "reason": str(exc)})
                seed += 1
                continue
            r_seq = run_sequential_solver(spec)
            r_grd = run_greedy_overlap_solver(spec)
            seq_s += r_seq["served"]
            seq_n += r_seq["total"]
            seq_c += int(r_seq["clear"])
            grd_s += r_grd["served"]
            grd_n += r_grd["total"]
            grd_c += int(r_grd["clear"])
            w_acts.append(sum(len(b) for b in batches))
            retained.append({
                "seed": seed,
                "level": level,
                "spec": serialize_spec(spec),
                "witness_actions": sum(len(b) for b in batches),
                "baselines": {"sequential": r_seq, "greedy_overlap": r_grd},
            })
            seed += 1
        bank[level] = retained
        exclusions[f"D{level}"] = excl
        n = len(retained)
        print(
            f"  D={level}: retained={n} (excluded {len(excl)}) | witness_acts={sum(w_acts)/n:4.1f} | "
            f"Sequential={100*seq_s/seq_n:5.1f}% (clear {seq_c:2d}/{n}) | "
            f"GreedyOverlap={100*grd_s/grd_n:5.1f}% (clear {grd_c:2d}/{n})"
        )
    out = Path(args.bank)
    save_bank(out, bank)
    excl_path = out.with_name(out.stem + "_exclusions.json")
    excl_path.write_text(json.dumps(exclusions, indent=2), encoding="utf-8")
    print(f"Saved frozen bank to {out} (exclusions: {excl_path})")


def cmd_run(args: argparse.Namespace) -> None:
    bank = load_bank(Path(args.bank))
    model_keys = [k.strip() for k in args.models.split(",") if k.strip()]
    adapters = []
    ep_caps = {}
    model_caps = {}
    for k in model_keys:
        if k not in MODELS:
            raise SystemExit(f"Unknown model key {k!r}; available: {sorted(MODELS)}")
        spec = MODELS[k]
        adapters.append(OpenRouterToolAdapter(spec))
        if spec.tier == "small":
            ep_caps[k] = args.small_ep_cap
            model_caps[k] = args.small_model_cap
        elif spec.tier == "medium":
            ep_caps[k] = args.medium_ep_cap
            model_caps[k] = args.medium_model_cap
        else:
            ep_caps[k] = args.frontier_ep_cap
            model_caps[k] = args.frontier_model_cap

    levels = tuple(int(x) for x in args.levels.split(","))
    summary = run_campaign(
        Path(args.out_dir),
        bank,
        adapters,
        levels=levels,
        max_seeds=args.seeds,
        campaign_cap_usd=args.campaign_cap,
        model_caps_usd=model_caps,
        episode_caps_usd=ep_caps,
        workers=args.workers,
        verbose=True,
    )
    print("\n=== SUMMARY ===\n")
    print(format_markdown_table(summary))


def cmd_report(args: argparse.Namespace) -> None:
    ep_dir = Path(args.out_dir) / "episodes"
    episodes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ep_dir.glob("*.json"))]
    summary = summarize_campaign(episodes)
    print(format_markdown_table(summary))
    models = sorted({e["model"] for e in episodes})
    if len(models) >= 2:
        print("\n=== PAIRED SEED COMPARISONS (BY LEVEL) ===")
        for level in sorted({e["level"] for e in episodes}):
            for i in range(len(models)):
                for j in range(i + 1, len(models)):
                    ma, mb = models[i], models[j]
                    ea = [e for e in episodes if e["model"] == ma and e["level"] == level]
                    eb = [e for e in episodes if e["model"] == mb and e["level"] == level]
                    p = paired_delta_bootstrap(ea, eb)
                    if p.get("paired_seeds", 0) > 0:
                        d_comp = p["point_delta"].get("completion")
                        d_usd = p["point_delta"].get("usd_per_order")
                        ci_comp = p["ci95"].get("completion")
                        print(
                            f"  D={level} {ma} vs {mb} (n={p['paired_seeds']}): "
                            f"W/L/T={p['a_served_more']}/{p['b_served_more']}/{p['ties']} | "
                            f"Δcomp={f'{100*d_comp:+.1f}%' if d_comp is not None else '—'} "
                            f"(CI95={f'[{100*ci_comp[0]:+.1f}%, {100*ci_comp[1]:+.1f}%]' if ci_comp else '—'}) | "
                            f"ΔUSD/ord={f'{d_usd:+.4f}' if d_usd is not None else '—'}"
                        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Kitchen Rush v3 calibration CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_b = sub.add_parser("build-bank")
    p_b.add_argument("--bank", default=str(DEFAULT_BANK))
    p_b.add_argument("--seeds-per-level", type=int, default=10)
    p_b.add_argument("--start-seed", type=int, default=3000)
    p_b.set_defaults(func=cmd_build_bank)

    p_r = sub.add_parser("run")
    p_r.add_argument("--bank", default=str(DEFAULT_BANK))
    p_r.add_argument("--out-dir", default=str(DEFAULT_RUN_DIR))
    p_r.add_argument("--models", required=True)
    p_r.add_argument("--levels", default="1,2,3,4,5")
    p_r.add_argument("--seeds", type=int, default=4)
    p_r.add_argument("--campaign-cap", type=float, default=16.0)
    p_r.add_argument("--small-ep-cap", type=float, default=0.15)
    p_r.add_argument("--small-model-cap", type=float, default=3.0)
    p_r.add_argument("--medium-ep-cap", type=float, default=0.40)
    p_r.add_argument("--medium-model-cap", type=float, default=6.0)
    p_r.add_argument("--frontier-ep-cap", type=float, default=0.80)
    p_r.add_argument("--frontier-model-cap", type=float, default=8.0)
    p_r.add_argument("--workers", type=int, default=4)
    p_r.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report")
    p_rep.add_argument("--out-dir", default=str(DEFAULT_RUN_DIR))
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
