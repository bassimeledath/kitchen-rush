#!/usr/bin/env python3
"""Kitchen Rush v3 bank builder, offline calibrator, OpenRouter runner, and UI replay exporter."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kitchenrush.v3.adapters import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_REQUEST_TIMEOUT_S,
    ModelSpec,
    OpenRouterToolAdapter,
)
from kitchenrush.v3.env import generate_kitchen, serialize_spec  # noqa: E402
from kitchenrush.v3.metrics import format_markdown_table, paired_delta_bootstrap, summarize_campaign  # noqa: E402
from kitchenrush.v3.replay import export_v3_replay  # noqa: E402
from kitchenrush.v3.runner import load_bank, run_campaign, save_bank  # noqa: E402
from kitchenrush.v3.solvers import run_greedy_overlap_solver, run_sequential_solver  # noqa: E402

DEFAULT_BANK = ROOT / "data/v3/calibration_bank_v2.json"
DEFAULT_RUN_DIR = ROOT / "runs/v3/bank_v2_run"


def _spec(key: str, model_id: str, tier: str, inp: float, out: float, effort: str) -> ModelSpec:
    return ModelSpec(
        key=key,
        model_id=model_id,
        tier=tier,
        input_price_per_m=inp,
        output_price_per_m=out,
        max_tokens=DEFAULT_MAX_TOKENS,
        reasoning={"effort": effort},
        temperature=None,
    )


MODELS: dict[str, ModelSpec] = {
    "gpt-6-luna-low": _spec("gpt-6-luna-low", "openai/gpt-6-luna", "small", 0.10, 0.50, "low"),
    "claude-haiku-5.5-low": _spec("claude-haiku-5.5-low", "anthropic/claude-haiku-5.5", "small", 0.10, 0.50, "low"),
    "gemini-3.8-flash-low": _spec("gemini-3.8-flash-low", "google/gemini-3.8-flash", "medium", 0.825, 4.125, "low"),
    "gemini-3.8-flash-medium": _spec("gemini-3.8-flash-medium", "google/gemini-3.8-flash", "medium", 0.825, 4.125, "medium"),
    "gpt-6.1-terra-low": _spec("gpt-6.1-terra-low", "openai/gpt-6.1-terra", "medium", 1.00, 5.00, "low"),
    "claude-sonnet-5.5-low": _spec("claude-sonnet-5.5-low", "anthropic/claude-sonnet-5.5", "frontier", 2.00, 10.00, "low"),
    "claude-sonnet-5.5-medium": _spec("claude-sonnet-5.5-medium", "anthropic/claude-sonnet-5.5", "frontier", 2.00, 10.00, "medium"),
    "claude-sonnet-5.5-high": _spec("claude-sonnet-5.5-high", "anthropic/claude-sonnet-5.5", "frontier", 2.00, 10.00, "high"),
    "gpt-6.1-sol-low": _spec("gpt-6.1-sol-low", "openai/gpt-6.1-sol", "frontier", 2.00, 10.00, "low"),
    "gpt-6.1-sol-medium": _spec("gpt-6.1-sol-medium", "openai/gpt-6.1-sol", "frontier", 2.00, 10.00, "medium"),
    "gpt-6.1-sol-high": _spec("gpt-6.1-sol-high", "openai/gpt-6.1-sol", "frontier", 2.00, 10.00, "high"),
    "gpt-6.2-sol-low": _spec("gpt-6.2-sol-low", "openai/gpt-6.2-sol", "frontier", 2.00, 10.00, "low"),
    "gpt-6.2-sol-medium": _spec("gpt-6.2-sol-medium", "openai/gpt-6.2-sol", "frontier", 2.00, 10.00, "medium"),
    "gpt-6.2-sol-high": _spec("gpt-6.2-sol-high", "openai/gpt-6.2-sol", "frontier", 2.00, 10.00, "high"),
    "gemini-4-neon-low": _spec("gemini-4-neon-low", "google/gemini-4-neon", "frontier", 3.00, 15.00, "low"),
    "gemini-4-neon-medium": _spec("gemini-4-neon-medium", "google/gemini-4-neon", "frontier", 3.00, 15.00, "medium"),
    "gemini-4-neon-high": _spec("gemini-4-neon-high", "google/gemini-4-neon", "frontier", 3.00, 15.00, "high"),
}


def cmd_build_bank(args: argparse.Namespace) -> None:
    out = Path(args.bank)
    if out.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing bank {out}; pass --force or specify a new --bank path.")
    levels = tuple(int(x) for x in args.levels.split(","))
    bank: dict[int, list[dict]] = {}
    exclusions: dict[str, list[dict]] = {}
    print(f"Building frozen v3 bank ({args.seeds_per_level} seeds/level, levels={levels})...")
    for level in levels:
        retained = []
        excl = []
        seed = args.start_seed
        attempts = 0
        seq_s = seq_n = seq_c = 0
        grd_s = grd_n = grd_c = 0
        w_acts = []
        while len(retained) < args.seeds_per_level and attempts < args.max_attempts:
            attempts += 1
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
        if len(retained) < args.seeds_per_level:
            raise SystemExit(
                f"Only retained {len(retained)}/{args.seeds_per_level} kitchens at D={level} after {attempts} attempts"
            )
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
    if args.max_tokens <= 0:
        raise SystemExit("--max-tokens must be a positive integer")
    for k in model_keys:
        if k not in MODELS:
            raise SystemExit(f"Unknown model key {k!r}; available: {sorted(MODELS)}")
        spec = replace(MODELS[k], max_tokens=args.max_tokens)
        adapters.append(OpenRouterToolAdapter(spec, timeout=args.timeout))

    levels = tuple(int(x) for x in args.levels.split(","))
    summary = run_campaign(
        Path(args.out_dir),
        bank,
        adapters,
        levels=levels,
        max_seeds=args.seeds,
        campaign_cap_usd=args.campaign_cap,
        history_mode=args.history_mode,
        workers=args.workers,
        verbose=True,
    )
    print("\n=== SUMMARY ===\n")
    print(format_markdown_table(summary))


def cmd_report(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    ep_dir = out_dir / "episodes"
    manifest_path = out_dir / "manifest.json"
    planned_by_cell: dict[tuple[str, int], int] | None = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        planned_by_cell = {}
        episodes = []
        for entry in manifest.get("planned_episodes", []):
            key = (entry["model"], int(entry["level"]))
            planned_by_cell[key] = planned_by_cell.get(key, 0) + 1
            p = ep_dir / entry["filename"]
            if p.exists():
                episodes.append(json.loads(p.read_text(encoding="utf-8")))
    else:
        episodes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ep_dir.glob("*.json"))]
    summary = summarize_campaign(episodes, planned_by_cell=planned_by_cell)
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
                        d_usd_c = p["point_delta"].get("usd_per_cleared_order")
                        ci_comp = p["ci95"].get("completion")
                        comp_str = f"{100 * d_comp:+.1f}%" if d_comp is not None else "—"
                        ci_str = f"[{100 * ci_comp[0]:+.1f}%, {100 * ci_comp[1]:+.1f}%]" if ci_comp else "—"
                        usd_str = f"{d_usd_c:+.4f}" if d_usd_c is not None else "no clear"
                        print(
                            f"  D={level} {ma} vs {mb} (n={p['paired_seeds']}): "
                            f"ClearW/L={p['a_cleared_more']}/{p['b_cleared_more']} "
                            f"ServedW/L/T={p['a_served_more']}/{p['b_served_more']}/{p['ties']} | "
                            f"Δcomp={comp_str} (CI95={ci_str}) | ΔUSD/cleared={usd_str}"
                        )


def cmd_export_replay(args: argparse.Namespace) -> None:
    bank = load_bank(Path(args.bank))
    spec_by_key = {(lvl, s.seed): s for lvl, specs in bank.items() for s in specs}
    ep_dir = Path(args.run_dir) / "episodes"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exported: list[tuple[str, Path]] = []
    for ep_path in sorted(ep_dir.glob(f"D{args.level}_seed{args.seed}_*.json")):
        ep = json.loads(ep_path.read_text(encoding="utf-8"))
        spec = spec_by_key[(ep["level"], ep["seed"])]
        out_path = out_dir / f"v3_D{ep['level']}_seed{ep['seed']}_{ep['model']}.json"
        export_v3_replay(ep_path, spec, out_path)
        exported.append((ep["model"], out_path))
    if not exported:
        raise SystemExit(f"No episodes found matching D{args.level}_seed{args.seed}_*.json in {ep_dir}")
    for model, path in exported:
        print(f"Exported {model}: {path}")
    if len(exported) <= 4:
        rels = ",".join(f"replays/{p.name}" for _, p in exported)
        labels = ",".join(m for m, _ in exported)
        print(f"\nRace Mode URL (when serving ui/ on :8000):\nhttp://localhost:8000/?replays={rels}&labels={labels}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kitchen Rush v3 calibration CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_b = sub.add_parser("build-bank")
    p_b.add_argument("--bank", default=str(DEFAULT_BANK))
    p_b.add_argument("--levels", default="1,2,3,4,5")
    p_b.add_argument("--seeds-per-level", type=int, default=10)
    p_b.add_argument("--start-seed", type=int, default=6000)
    p_b.add_argument("--max-attempts", type=int, default=250)
    p_b.add_argument("--force", action="store_true", help="Allow overwriting an existing bank file.")
    p_b.set_defaults(func=cmd_build_bank)

    p_r = sub.add_parser("run")
    p_r.add_argument("--bank", default=str(DEFAULT_BANK))
    p_r.add_argument("--out-dir", default=str(DEFAULT_RUN_DIR))
    p_r.add_argument("--models", required=True)
    p_r.add_argument("--levels", default="1,2,3,4,5")
    p_r.add_argument("--seeds", type=int, default=6)
    p_r.add_argument("--campaign-cap", type=float, default=12.0)
    p_r.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="Per-turn output+reasoning token ceiling applied uniformly across models.")
    p_r.add_argument("--timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT_S, help="HTTP request timeout in seconds per turn.")
    p_r.add_argument("--history-mode", choices=("full_chat", "last_turn"), default="full_chat")
    p_r.add_argument("--workers", type=int, default=4)
    p_r.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report")
    p_rep.add_argument("--out-dir", default=str(DEFAULT_RUN_DIR))
    p_rep.set_defaults(func=cmd_report)

    p_exp = sub.add_parser("export-replay")
    p_exp.add_argument("--bank", default=str(DEFAULT_BANK))
    p_exp.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    p_exp.add_argument("--out-dir", default=str(ROOT / "ui/replays"))
    p_exp.add_argument("--level", type=int, required=True)
    p_exp.add_argument("--seed", type=int, required=True)
    p_exp.set_defaults(func=cmd_export_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
