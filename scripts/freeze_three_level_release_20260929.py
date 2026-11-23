#!/usr/bin/env python3
"""Freeze concrete Easy/Medium/Hard banks and a complete release source manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import extreme_sol_kitchens_20260926 as hard_family  # noqa: E402
import overlap_gap_v2 as medium_family  # noqa: E402
import run_severe_replication_20260928 as original  # noqa: E402
from kitchenrush.v2 import environment as easy_family  # noqa: E402
from kitchenrush.tools import ToolCall  # noqa: E402

OUT = ROOT / "data/v2/three-level-release-20260929"
HARD_SOURCE = ROOT / "data/v2/severe-replication-20260928/kitchens.json"
EASY_SEEDS = tuple(range(92000, 92012))
MEDIUM_CANDIDATES = tuple(range(92000, 92120))
COUNT = 12
NATIVE_WAITS = (1, 2, 5, 10, 20)


def write_new(path: Path, value):
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def build_easy():
    easy_family.WAITS = NATIVE_WAITS
    rows = []
    for seed in EASY_SEEDS:
        spec = easy_family.generate(seed, "main", 3)
        witness = easy_family.scripted_episode(spec)
        if witness["completed"] != 3 or witness["invalid"] != 0:
            raise ValueError(f"easy seed {seed} witness failed")
        rows.append({"level": "easy", "seed": seed, "split": "main", "size": 3,
                     "spec": witness["spec"], "witness_actions": witness["actions"],
                     "witness_hash": easy_family.digest(witness["actions"]),
                     "wait_choices": list(easy_family.WAITS)})
    return rows


def build_medium():
    easy_family.WAITS = NATIVE_WAITS
    rows, exclusions = [], []
    for seed in MEDIUM_CANDIDATES:
        diagnostic = {}
        row = medium_family.candidate(seed, "medium", 20.0,
                                      medium_family.WIDTH, diagnostic)
        if row is None:
            exclusions.append({"seed": seed, **diagnostic})
        else:
            row["level"] = "medium"
            row["wait_choices"] = list(easy_family.WAITS)
            rows.append(row)
            print(json.dumps({"medium_accepted": seed, "count": len(rows)}), flush=True)
        if len(rows) == COUNT:
            break
    if len(rows) != COUNT:
        raise RuntimeError(f"only {len(rows)} medium kitchens found")
    medium_family.verify_dataset(rows)
    return rows, exclusions


def verify_banks():
    easy_family.WAITS = NATIVE_WAITS
    easy = json.loads((OUT / "easy.json").read_text())
    medium = json.loads((OUT / "medium.json").read_text())
    hard = json.loads((OUT / "hard.json").read_text())
    assert len(easy) == len(medium) == len(hard) == COUNT
    assert [r["seed"] for r in easy] == list(EASY_SEEDS)
    assert hard == json.loads(HARD_SOURCE.read_text())
    for row in easy:
        spec = easy_family.generate(row["seed"], "main", 3)
        assert row["spec"] == easy_family.serialize_spec(spec)
        assert row["wait_choices"] == list(easy_family.WAITS)
        assert row["witness_hash"] == easy_family.digest(row["witness_actions"])
        engine = easy_family.Engine(spec)
        for action in row["witness_actions"]:
            assert not engine.terminated
            engine.step([ToolCall(**action)])
        assert engine.counters["orders_served"] == 3
        assert engine.counters["invalid_actions"] == 0
    assert all(row["arm"] == "gap-medium" and row["wait_choices"] == list(easy_family.WAITS)
               for row in medium)
    medium_family.verify_dataset(medium)
    for row in hard:
        hard_family.verify(row)
    return {"easy": [r["seed"] for r in easy],
            "medium": [r["seed"] for r in medium],
            "hard": [r["seed"] for r in hard]}


def source_manifest():
    files = original.source_files()
    paths = [Path(__file__), ROOT / "scripts/overlap_gap_v2.py",
             ROOT / "scripts/extreme_sol_kitchens_20260926.py",
             ROOT / "scripts/run_severe_recovery_20260929.py",
             ROOT / "scripts/run_severe_recovery_r2_20260929.py",
             ROOT / "scripts/severe_recovery_transport_20260929.py",
             ROOT / "scripts/difficulty_sweep_v2.py",
             ROOT / "scripts/concurrency_probe_v2.py",
             ROOT / "scripts/complexity_ladder_v2.py",
             ROOT / "docs/v2/FROZEN_THREE_LEVEL_BENCHMARK_20260928.md",
             ROOT / "docs/v2/THREE_LEVEL_BANKS_20260929.md",
             ROOT / "docs/v2/SEVERE_PAIRED_RECOVERY_20260929.md",
             ROOT / "docs/v2/SEVERE_PAIRED_RECOVERY_R2_20260929.md",
             ROOT / "data/v2/severe-recovery-20260929/campaign.json",
             ROOT / "data/v2/severe-recovery-r2-20260929/campaign.json",
             original.RUNS / "source-manifest.json", HARD_SOURCE,
             ROOT / "runs/v2/severe-recovery-20260929/source-manifest.json",
             ROOT / "runs/v2/severe-recovery-r2-20260929/source-manifest.json",
             OUT / "easy.json", OUT / "medium.json",
             OUT / "medium-exclusions.json", OUT / "hard.json"]
    files.update({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in paths})
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("build", "freeze-manifest", "verify"))
    args = parser.parse_args()
    if args.mode == "build":
        write_new(OUT / "easy.json", build_easy())
        medium, exclusions = build_medium()
        write_new(OUT / "medium.json", medium)
        write_new(OUT / "medium-exclusions.json", exclusions)
        write_new(OUT / "hard.json", json.loads(HARD_SOURCE.read_text()))
    banks = verify_banks()
    if args.mode == "freeze-manifest":
        files = source_manifest()
        write_new(OUT / "source-manifest.json", {
            "family": "single-chef-easy-medium-hard-20260929",
            "banks": banks, "files": files,
            "wait_choices": {"easy": [1, 2, 5, 10, 20],
                             "medium": [1, 2, 5, 10, 20],
                             "hard": list(hard_family.WAITS)}})
    elif args.mode == "verify":
        manifest = json.loads((OUT / "source-manifest.json").read_text())
        assert manifest["banks"] == banks
        assert manifest["files"] == source_manifest()
    print(json.dumps({"mode": args.mode, "banks": banks}), flush=True)


if __name__ == "__main__":
    main()
