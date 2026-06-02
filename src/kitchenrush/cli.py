"""Command-line interface (Phase 1: stdlib argparse, baselines only).

    kitchenrush run --baseline random --seed 0 --tier easy --latency 0.5
    kitchenrush seeds --tier easy

Phase 2 adds `--model provider:model` once the LiteLLM adapter lands (docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config
from .procgen import generate
from .report import write_json
from .runner import run_episode
from .version import __version__


def _build_policy(baseline: str, seed: int, latency: float):
    from .baselines import NullAgent, RandomAgent

    if baseline == "null":
        return NullAgent(latency=latency)
    if baseline == "random":
        return RandomAgent(seed=seed, latency=latency)
    raise SystemExit(f"unknown baseline {baseline!r} (choose: null, random)")


def _cmd_run(args: argparse.Namespace) -> int:
    spec = generate(args.seed, args.tier)
    policy = _build_policy(args.baseline, args.seed, args.latency)
    result = run_episode(spec, policy, max_turns=args.max_turns)
    rep = result.report
    if args.out:
        write_json(result, args.out)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        c = rep["counters"]
        print(f"Kitchen Rush — tier={rep['tier']} seed={rep['seed']} baseline={args.baseline}")
        print(f"  score (raw/display): {rep['score_raw']} / {rep['score_display']}")
        print(f"  game time: {rep['clock_gs']}/{rep['horizon_gs']} gs over {rep['turns']} turns")
        print(f"  orders: {c['orders_served']} served, {c['orders_expired']} expired "
              f"of {c['orders_total']}")
        print(f"  burns={c['burns']} invalid={c['invalid_actions']} "
              f"drops={c['drops']} max_combo={c['max_combo']}")
        if args.out:
            print(f"  wrote trajectory -> {args.out}")
    return 0


def _cmd_seeds(args: argparse.Namespace) -> int:
    for seed in range(args.start, args.start + args.count):
        spec = generate(seed, args.tier)
        print(f"seed={seed} tier={args.tier} grid={spec.grid_n} "
              f"stations={len(spec.stations)} orders={len(spec.orders)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kitchenrush", description="Kitchen Rush benchmark CLI")
    parser.add_argument("--version", action="version", version=f"kitchenrush {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="play one episode with a baseline policy")
    run.add_argument("--baseline", choices=["null", "random"], default="random")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--tier", choices=sorted(config.TIERS), default="easy")
    run.add_argument("--latency", type=float, default=0.5, help="seconds per response (-> game-time)")
    run.add_argument("--max-turns", type=int, default=None)
    run.add_argument("--out", type=str, default=None, help="write full trajectory JSON here")
    run.add_argument("--json", action="store_true", help="print the full report as JSON")
    run.set_defaults(func=_cmd_run)

    seeds = sub.add_parser("seeds", help="preview generated instances")
    seeds.add_argument("--tier", choices=sorted(config.TIERS), default="easy")
    seeds.add_argument("--start", type=int, default=0)
    seeds.add_argument("--count", type=int, default=5)
    seeds.set_defaults(func=_cmd_seeds)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
