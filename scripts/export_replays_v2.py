#!/usr/bin/env python3
"""Export public replay data from an independently audited release cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from audit_release_v2 import audit, require

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_replay(episode, *, label, source):
    fields = (
        "protocol",
        "protocol_hash",
        "status",
        "dollar_cap",
        "token_cap",
        "reason",
        "quality",
        "completed",
        "invalid",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "accepted_cost_usd",
        "boundary_cost_usd",
    )
    result = {key: episode[key] for key in fields}
    spec = episode["spec"]
    result.update(
        replay_export_schema="sanitized-choice-episode-1",
        model_label=label,
        source=source,
        spec={
            key: spec[key]
            for key in (
                "seed",
                "grid_n",
                "blocked",
                "stations",
                "recipe_cards",
                "ingredient_rules",
            )
        },
        frames=[],
        requests=[],
    )
    result["spec"]["orders"] = [{"order_id": order["order_id"]} for order in spec["orders"]]
    for frame in episode["frames"]:
        if frame["kind"] not in ("start", "action"):
            continue
        clean = {key: frame[key] for key in ("kind", "clock_gs", "chef_pos", "hands", "burners")}
        clean["orders"] = [
            {"status": "PENDING"} if order["status"] == "PENDING" else order
            for order in frame["orders"]
        ]
        if "action" in frame:
            clean["action"] = frame["action"]
        result["frames"].append(clean)
    for request in episode["requests"]:
        result["requests"].append(
            {
                key: request[key]
                for key in (
                    "admitted",
                    "cost_usd",
                    "input_tokens",
                    "output_tokens",
                )
            }
        )
    require(
        sum(frame["kind"] == "action" for frame in result["frames"])
        == sum(request["admitted"] for request in result["requests"]),
        "public replay action/accounting mismatch",
    )
    return result


def export(batch, seed, size, dollars, tokens):
    gate_path = ROOT / f"data/v2/release-r2/{batch}-gate.json"
    stored = json.loads(gate_path.read_text())
    gate = audit(batch, write_gate=False)
    for field in ("manifest_sha256", "protocol_hash", "evaluated_artifacts", "failed_attempts"):
        require(stored[field] == gate[field], "stale stored replay audit")
    manifest_path = ROOT / f"data/v2/release-r2/{batch}-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    roster = {
        row["key"]: row
        for row in json.loads((ROOT / "data/v2/release-r2-roster.json").read_text())["models"]
    }
    jobs = [
        job
        for job in manifest["jobs"]
        if (
            job["seed"] == seed
            and job["size"] == size
            and job["dollar_cap"] == dollars
            and job["token_cap"] == tokens
        )
    ]
    require(bool(jobs), "no matching audited replay jobs")
    output = ROOT / "ui/v2"
    examples = [
        {
            "file": "demo.json",
            "label": "Scripted feasibility example · current v2",
            "kind": "scripted",
            "sha256": sha(output / "demo.json"),
        }
    ]
    diagnostic = "CANARY ONLY" if "canary" in batch else batch.upper()
    for job in jobs:
        raw = ROOT / gate["evaluated_artifacts"][job["id"]]
        episode = json.loads(raw.read_text())
        label = roster[job["model"]]["label"]
        failed = next((f for f in gate["failed_attempts"] if f["job"] == job["id"]), None)
        source = dict(
            episode_id=episode["id"],
            episode_sha256=sha(raw),
            audit_gate=gate_path.relative_to(ROOT).as_posix(),
            audit_gate_sha256=sha(gate_path),
            manifest_sha256=sha(manifest_path),
            source_archive_sha256=gate["source_archive_sha256"],
            purpose="Audited selected replay; one kitchen is not a model ranking.",
            prior_failed_attempt=failed,
        )
        exported = public_replay(episode, label=label, source=source)
        filename = job["id"] + "-public.json"
        target = output / filename
        target.write_text(json.dumps(exported, indent=2, sort_keys=True) + "\n")
        allowance = f"{tokens:,} tokens" if tokens is not None else f"${dollars:g}"
        examples.append(
            dict(
                file=filename,
                label=f"{label} · {diagnostic} · {allowance}",
                kind="model",
                sha256=sha(target),
                audit=dict(
                    status="passed",
                    protocol_hash=gate["protocol_hash"],
                    source=f"{batch} audit · {gate['requests_verified']} requests verified",
                    gate_sha256=sha(gate_path),
                    manifest_sha256=sha(manifest_path),
                ),
            )
        )
    (output / "examples.json").write_text(json.dumps(examples, indent=2) + "\n")
    return dict(batch=batch, model_replays=len(jobs), seed=seed, size=size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--size", type=int, choices=(2, 3, 4), required=True)
    parser.add_argument("--dollars", type=float, required=True)
    parser.add_argument("--tokens", type=int)
    args = parser.parse_args()
    print(json.dumps(export(args.batch, args.seed, args.size, args.dollars, args.tokens)))
