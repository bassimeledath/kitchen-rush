#!/usr/bin/env python3
"""Freeze twelve fresh native severe kitchens without inspecting model results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import extreme_sol_kitchens_20260926 as severe

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/v2/severe-replication-20260928/kitchens.json"
# Consecutive fresh seeds; retain the first twelve with a clean replayed witness.
# 90610 is recorded in exclusions.json because the existing witness burns an item.
CANDIDATE_SEEDS = tuple(range(90603, 90616))
TARGET = 12


def build():
    rows, excluded = [], []
    for seed in CANDIDATE_SEEDS:
        try:
            row = severe.build(seed, "severe")
        except ValueError as error:
            excluded.append({"seed": seed, "reason": str(error)})
            continue
        row["split"] = "fixed-fresh-replication"
        rows.append(row)
        if len(rows) == TARGET:
            break
    if len(rows) != TARGET:
        raise RuntimeError(f"only {len(rows)} certified kitchens")
    return rows, excluded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    rows, excluded = build()
    if args.verify:
        assert json.loads(OUT.read_text()) == rows
        assert json.loads(OUT.with_name("exclusions.json").read_text()) == excluded
    else:
        if OUT.exists():
            raise FileExistsError(OUT)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
        OUT.with_name("exclusions.json").write_text(json.dumps(excluded, indent=2) + "\n")
    for row in rows:
        severe.verify(row)
    print(json.dumps({"seeds": [r["seed"] for r in rows],
                      "excluded": excluded,
                      "witness_decisions": [r["witness_decisions"] for r in rows]}))


if __name__ == "__main__":
    main()
