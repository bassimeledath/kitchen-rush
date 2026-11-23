#!/usr/bin/env python3
"""Checkpoint runner. Stage 3 is intentionally not implemented/authorized here."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kitchenrush.v2.environment import (  # noqa: E402
    PROTOCOL,
    generate,
    protocol_hash,
    recipe_pool,
    scripted_episode,
    verify_replay,
)
from kitchenrush.v2.evaluation import run_episode, write_json  # noqa: E402
from kitchenrush.v2.ledger import Ledger  # noqa: E402
from kitchenrush.v2.providers import Client, Model, load_env  # noqa: E402

RUN_GENERATION = "r3"

MODELS = {
    "jev": Model("jev", "jev-1.13.0", "TypeSafe", 0.042 / 1e6, 0),
    "mini": Model(
        "mini", "openai/gpt-5.4-mini", "OpenAI", 0.75 / 1e6, 4.5 / 1e6, {"effort": "low"}, 2048
    ),
    "sonnet": Model(
        "sonnet",
        "anthropic/claude-sonnet-4.6",
        "Anthropic",
        3 / 1e6,
        15 / 1e6,
        {"enabled": False},
        512,
    ),
}


def offline():
    rows = []
    for split in ("development", "pilot", "main", "confirmation"):
        for size in (2, 3, 4):
            for seed in range(20):
                ep = scripted_episode(generate(seed, split, size))
                assert ep["quality"] == 100 and ep["invalid"] == 0
                verify_replay(ep)
                rows.append({"seed": seed, "split": split, "size": size, "quality": ep["quality"]})
    memorized = generate(4, "development", 3).recipe_cards
    transfer = []
    for seed in range(12):
        spec = generate(40000 + seed, "pilot", 2 + seed % 3)
        reader = scripted_episode(spec)
        lookup = scripted_episode(spec, memorize=memorized)
        transfer.append(
            {
                "seed": spec.seed,
                "recipe_reader_quality": reader["quality"],
                "fixed_lookup_quality": lookup["quality"],
            }
        )
    demo = scripted_episode(generate(41000, "development", 3))
    write_json(ROOT / "runs/v2/offline-demo.json", demo)
    report = {
        "protocol": PROTOCOL,
        "protocol_hash": protocol_hash(),
        "status": "passed",
        "solvable_replayed_instances": len(rows),
        "instances": rows,
        "grammar_counts": {
            split: {size: len(recipe_pool(size, split)) for size in (2, 3, 4)}
            for split in ("development", "pilot", "main", "confirmation")
        },
        "transfer_baselines": transfer,
        "limitations": "Lookup failure is a shortcut audit, "
        "not proof against training on the public generator distribution.",
    }
    write_json(ROOT / "data/v2/checkpoint1.json", report)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("instances", "transfer_baselines")}
        )
    )


def plan(stage):
    jobs = []
    if stage == "canary":
        for key in MODELS:
            jobs.append(
                dict(model=key, seed=42000, size=3, trial=0, dollar_cap=0.4, token_cap=None)
            )
    elif stage == "smoke":
        for size in (2, 3, 4):
            for key in MODELS:
                for cap in (0.05, 0.4):
                    jobs.append(
                        dict(
                            model=key,
                            seed=43000 + size,
                            size=size,
                            trial=0,
                            dollar_cap=cap,
                            token_cap=None,
                        )
                    )
    else:
        for trial in range(2):
            for offset in range(3):
                for cap in (0.05, 0.15, 0.4):
                    for key in MODELS:
                        jobs.append(
                            dict(
                                model=key,
                                seed=44000 + offset,
                                size=2 + offset % 3,
                                trial=trial,
                                dollar_cap=cap,
                                token_cap=None,
                            )
                        )
        for size in (2, 3, 4):
            for cap in (512, 2048, 8192):
                for key in ("mini", "sonnet"):
                    jobs.append(
                        dict(
                            model=key,
                            seed=45000 + size,
                            size=size,
                            trial=0,
                            dollar_cap=1.0,
                            token_cap=cap,
                        )
                    )
    for j in jobs:
        j["id"] = (
            f"{stage}-{RUN_GENERATION}-{j['model']}-{j['seed']}-{j['trial']}-d{j['dollar_cap']}-t{j['token_cap']}"
        )
    return jobs


def paid(stage, workers):
    gate = ROOT / "data/v2/checkpoint1.json"
    if not gate.exists() or json.loads(gate.read_text())["status"] != "passed":
        raise SystemExit("offline gate missing")
    if json.loads(gate.read_text())["protocol_hash"] != protocol_hash():
        raise SystemExit("protocol changed: repeat offline gate before dispatch")
    if stage != "canary":
        previous = "canary" if stage == "smoke" else "smoke"
        previous_gate = ROOT / f"data/v2/{previous}-gate.json"
        if (
            not previous_gate.exists()
            or json.loads(previous_gate.read_text())["status"] != "passed"
            or (
                previous == "smoke"
                and json.loads(previous_gate.read_text())["protocol_hash"] != protocol_hash()
            )
        ):
            raise SystemExit("previous stage has not passed audit")
    ledger = Ledger(ROOT / "runs/v2/spend.sqlite")
    snapshot = ledger.snapshot()
    if snapshot["unresolved_requests"] or snapshot["overruns"]:
        raise SystemExit("unresolved billing or overrun: audit before any dispatch")
    load_env(ROOT / ".env")
    jobs = plan(stage)
    manifest = {
        "stage": stage,
        "protocol_hash": protocol_hash(),
        "models": {k: asdict(v) for k, v in MODELS.items()},
        "jobs": jobs,
    }
    manifest_path = ROOT / f"data/v2/{stage}-{RUN_GENERATION}-manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise SystemExit("manifest changed; do not mix protocols/configurations")
    write_json(manifest_path, manifest)
    pending = []
    for job in jobs:
        path = ROOT / "runs/v2" / f"{stage}-{RUN_GENERATION}" / (job["id"] + ".json")
        if path.exists():
            old = json.loads(path.read_text())
            if old["status"] != "complete" or old["protocol_hash"] != protocol_hash():
                raise SystemExit(f"existing noncomplete/stale episode: {job['id']}")
        else:
            pending.append(job)

    def execute(j):
        ep = run_episode(
            generate(j["seed"], "development" if stage != "pilot" else "pilot", j["size"]),
            Client(MODELS[j["model"]]),
            episode_id=j["id"],
            stage=stage,
            dollar_cap=j["dollar_cap"],
            token_cap=j["token_cap"],
            ledger=ledger,
            output=ROOT / "runs/v2" / f"{stage}-{RUN_GENERATION}" / (j["id"] + ".json"),
        )
        return {
            k: ep[k]
            for k in ("id", "status", "quality", "completed", "invalid", "cost_usd", "reason")
        }

    # Only submit one balanced block at a time; a failure stops future dispatches.
    for start in range(0, len(pending), workers):
        with ThreadPoolExecutor(workers) as pool:
            results = [
                f.result()
                for f in as_completed(
                    [pool.submit(execute, j) for j in pending[start : start + workers]]
                )
            ]
        for result in results:
            print(json.dumps(result), flush=True)
        write_json(
            ROOT / f"data/v2/{stage}-status.json",
            {"ledger": ledger.snapshot(), "last_block": results},
        )
        if any(r["status"] != "complete" or r["reason"] == "spending_gate" for r in results):
            raise SystemExit("stage stopped: inspect failed block before continuing")
    print(json.dumps({"stage": stage, "finished": True, "ledger": ledger.snapshot()}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["offline", "canary", "smoke", "pilot"])
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.workers <= 3:
        raise SystemExit("workers must be 1..3")
    if args.command == "offline":
        offline()
    else:
        paid(args.command, args.workers)


if __name__ == "__main__":
    main()
