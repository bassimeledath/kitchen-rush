"""Command-line interface.

    kitchenrush run   --baseline random --seed 0 --tier easy
    kitchenrush run   --model openai:gpt-4.1 --seed 0 --tier easy --track rp   # needs providers extra + keys
    kitchenrush bench --baseline random --tier easy --seeds 20 --trials 4 --track rp
    kitchenrush seeds --tier medium
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config
from .metrics import aggregate
from .procgen import generate
from .report import EpisodeResult, write_jsonl
from .runner import run_episode, run_suite
from .version import __version__


_PROFILES = {"voice": 1.0, "chat": 5.0, "quality": 20.0}   # latency budget B (s/decision)


def _resolve_b(args: argparse.Namespace) -> float:
    """Latency budget B from --latency-budget (wins), else --profile preset, else default."""
    if getattr(args, "latency_budget", None) is not None:
        return float(args.latency_budget)
    if getattr(args, "profile", None):
        return _PROFILES[args.profile]
    return config.B_SECONDS


def _policy_factory(args: argparse.Namespace):
    """Return a callable (seed, trial) -> policy from the run/bench args."""
    if args.model:
        from .adapter import resolve_model
        from .agent import ModelAgent

        def factory(seed: int, trial: int):
            return ModelAgent(resolve_model(args.model), track=args.track,
                              temperature=args.temperature)
        return factory

    from .baselines import NullAgent, RandomAgent
    if args.baseline == "null":
        return lambda seed, trial: NullAgent(latency=args.latency)
    if args.baseline == "random":
        return lambda seed, trial: RandomAgent(seed=seed * 1000 + trial, latency=args.latency)
    raise SystemExit(f"unknown baseline {args.baseline!r}")


def _print_report(rep: dict, label: str, track: str) -> None:
    c = rep["counters"]
    print(f"Kitchen Rush — tier={rep['tier']} seed={rep['seed']} {label} track={track}")
    print(f"  score (raw/display): {rep['score_raw']} / {rep['score_display']}")
    print(f"  game time: {rep['clock_gs']}/{rep['horizon_gs']} gs over {rep['turns']} turns")
    print(f"  orders: {c['orders_served']} served, {c['orders_expired']} expired of {c['orders_total']}")
    print(f"  burns={c['burns']} invalid={c['invalid_actions']} drops={c['drops']} max_combo={c['max_combo']}")


def _cmd_run(args: argparse.Namespace) -> int:
    config.B_SECONDS = _resolve_b(args)
    spec = generate(args.seed, args.tier)
    policy = _policy_factory(args)(args.seed, 0)
    result = run_episode(spec, policy, max_turns=args.max_turns)
    from .runner import anchors_for
    result.s_null, result.s_ref = anchors_for(spec)
    base = f"model={args.model}" if args.model else f"baseline={args.baseline}"
    label = f"{base}  B={config.B_SECONDS:g}s"
    if args.out:
        write_jsonl(result, args.out)
    if args.json:
        print(json.dumps(result.report, indent=2))
    else:
        _print_report(result.report, label, args.track)
        if args.out:
            print(f"  wrote trajectory -> {args.out}")
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    config.B_SECONDS = _resolve_b(args)
    seeds = range(args.start, args.start + args.seeds)
    episodes = run_suite(seeds, args.tier, _policy_factory(args),
                         trials=args.trials, max_turns=args.max_turns)
    agg = aggregate(episodes, k=args.trials)
    base = f"model={args.model}" if args.model else f"baseline={args.baseline}"
    label = f"{base}  B={config.B_SECONDS:g}s"
    if args.json:
        print(json.dumps(agg, indent=2))
    else:
        print(f"Kitchen Rush bench — tier={args.tier} {label} track={args.track} "
              f"({agg['seeds']} seeds x {args.trials} trials = {agg['episodes']} episodes)")
        keys = dict.fromkeys((  # dict.fromkeys dedupes (pass_1 == pass_{trials} when trials=1)
            "KR", "kr_std", "mean_score_raw", "completion_rate", "expiry_rate",
            "invalid_rate", "pass_1", f"pass_{args.trials}", "think_gs_p50",
            "think_gs_p95", "degenerate_instances",
        ))
        for key in keys:
            if key in agg:
                print(f"  {key:20} {agg[key]}")
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    """Sweep the greedy-EDF reference at injected latencies and print KR(EDF@l) — the
    calibration shape used to choose final parameter values (METHODOLOGY §5)."""
    from .oracle import OracleAgent, null_score, reference_score
    from .runner import run_episode

    config.B_SECONDS = _resolve_b(args)
    seeds = range(args.start, args.start + args.seeds)
    specs = [generate(s, args.tier) for s in seeds]
    completed = served = orders = 0
    for spec in specs:
        rep = run_episode(spec, OracleAgent(0.0)).report
        served += rep["counters"]["orders_served"]
        orders += rep["counters"]["orders_total"]
        completed += rep["counters"]["orders_served"] == rep["counters"]["orders_total"]
    print(f"Kitchen Rush calibrate — tier={args.tier} B={config.B_SECONDS:g}s ({len(specs)} seeds)")
    print(f"  oracle@0 order completion: {served}/{orders}  "
          f"(fully-completed instances: {completed}/{len(specs)})")
    for latency in [0.0, 0.5, 1.0, 2.0, 4.0]:
        krs = []
        for spec in specs:
            s_null = null_score(spec)
            s_ref = reference_score(spec, 0.0)
            if s_ref <= s_null:
                continue
            s = reference_score(spec, latency)
            krs.append(100.0 * max(0.0, min(1.0, (s - s_null) / (s_ref - s_null))))
        val = sum(krs) / len(krs) if krs else float("nan")
        print(f"  KR(EDF@{latency:>3}s) = {val:5.1f}   (over {len(krs)} non-degenerate seeds)")
    return 0


def _cmd_seeds(args: argparse.Namespace) -> int:
    for seed in range(args.start, args.start + args.count):
        spec = generate(seed, args.tier)
        print(f"seed={seed} tier={args.tier} grid={spec.grid_n} "
              f"stations={len(spec.stations)} orders={len(spec.orders)}")
    return 0


def _add_policy_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--baseline", choices=["null", "random"], default="random")
    p.add_argument("--model", default=None, help="provider:model, e.g. openai:gpt-4.1 (needs providers extra)")
    p.add_argument("--tier", choices=sorted(config.TIERS), default="easy")
    p.add_argument("--track", choices=["rt", "rp"], default="rp")
    p.add_argument("--temperature", type=float, default=config.DEFAULT_TEMPERATURE)
    p.add_argument("--latency", type=float, default=0.5, help="baseline seconds/response (-> game-time)")
    p.add_argument("--max-turns", type=int, default=None)
    p.add_argument("--latency-budget", type=float, default=None,
                   help="B: seconds/decision the deadlines are priced at (default 1.0)")
    p.add_argument("--profile", choices=sorted(_PROFILES), default=None,
                   help="latency preset: voice=1s, chat=5s, quality=20s (sets --latency-budget)")
    p.add_argument("--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kitchenrush", description="Kitchen Rush benchmark CLI")
    parser.add_argument("--version", action="version", version=f"kitchenrush {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="play one episode")
    _add_policy_args(run)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--out", type=str, default=None, help="write trajectory JSONL here")
    run.set_defaults(func=_cmd_run)

    bench = sub.add_parser("bench", help="multi-seed x trial run with aggregate metrics + RTTC")
    _add_policy_args(bench)
    bench.add_argument("--seeds", type=int, default=20, help="number of seeds")
    bench.add_argument("--start", type=int, default=0, help="first seed")
    bench.add_argument("--trials", type=int, default=config.PASS_K)
    bench.set_defaults(func=_cmd_bench)

    seeds = sub.add_parser("seeds", help="preview generated instances")
    seeds.add_argument("--tier", choices=sorted(config.TIERS), default="easy")
    seeds.add_argument("--start", type=int, default=0)
    seeds.add_argument("--count", type=int, default=5)
    seeds.set_defaults(func=_cmd_seeds)

    cal = sub.add_parser("calibrate", help="sweep the EDF reference at injected latencies (KR shape)")
    cal.add_argument("--tier", choices=sorted(config.TIERS), default="easy")
    cal.add_argument("--start", type=int, default=0)
    cal.add_argument("--seeds", type=int, default=12)
    cal.add_argument("--latency-budget", type=float, default=None,
                     help="B: seconds/decision the deadlines are priced at (default 1.0)")
    cal.add_argument("--profile", choices=sorted(_PROFILES), default=None,
                     help="latency preset: voice=1s, chat=5s, quality=20s")
    cal.set_defaults(func=_cmd_calibrate)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:               # e.g. missing providers extra / API keys
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
