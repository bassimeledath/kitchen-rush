#!/usr/bin/env python3
"""Staged v2 release runner. No batch advances without its independently audited gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kitchenrush.v2.environment import (  # noqa: E402
    PROTOCOL,
    digest,
    generate,
    protocol_hash,
    scripted_episode,
    verify_replay,
)
from kitchenrush.v2.evaluation import run_episode, write_json  # noqa: E402
from kitchenrush.v2.ledger import SCALE, STAGES, Ledger, units  # noqa: E402
from kitchenrush.v2.providers import Client, Model, load_env  # noqa: E402

DATA = ROOT / "data/v2/release-r2"
RUNS = ROOT / "runs/v2/release-r2"
CONTROLS = (
    "deepseek-v4-flash-think",
    "claude-sonnet-4-6-think",
    "gpt-5-4-mini-think",
    "gemini-3-5-flash",
    "grok-build-think",
    "jev",
)
CAMPAIGN_FIELDS = (
    "protocol_hash",
    "configuration_hash",
    "roster_sha256",
    "opening_balance",
    "orchestration",
)


def prior_accounting():
    """Add request liabilities once; the second ledger's opening repeats the first."""
    total = 0
    original = None
    for name in ("runs/v2/spend.sqlite", "runs/v2/release/spend.sqlite"):
        path = ROOT / name
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as con:
            rows = con.execute("SELECT reserved,actual,status FROM requests").fetchall()
            if any(r[2] not in ("settled", "retained_failure") for r in rows):
                raise ValueError("prior accounting is live, unresolved, or overrun")
            amount = sum(r[1] if r[1] is not None else r[0] for r in rows)
            tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            opening = (
                con.execute("SELECT amount FROM opening WHERE id=1").fetchone()[0]
                if "opening" in tables
                else 0
            )
            if original is None:
                if opening != 0:
                    raise ValueError("original ledger opening must be zero")
                original = amount
            elif opening != original:
                raise ValueError("prior release opening does not reconcile")
            total += amount
    return total / SCALE


def orchestration_descriptor():
    members = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "scripts/release_v2.py",
            "scripts/audit_release_v2.py",
            "scripts/audit_roster_v2.py",
        )
    }
    identity = digest(dict(protocol_hash=protocol_hash(), scripts=members))
    return dict(version=1, scripts=members, archive=f"data/v2/release-r2/source-{identity}.zip")


def models():
    roster = json.loads((ROOT / "data/v2/release-r2-roster.json").read_text())
    result = {}
    for row in roster["models"]:
        if row["status"].startswith("blocked"):
            continue
        jev = row["provider"] == "TypeSafe"
        result[row["key"]] = Model(
            key=row["key"],
            model=row["model"],
            provider=row["provider"],
            input_price=row["input_price"],
            output_price=row["output_price"],
            reasoning=row["reasoning"],
            max_output=row["max_output"],
            route_slug=row["route_slug"],
            prompt_limit=1_000_000 if jev else row["context_length"],
            reservation_input_price=row.get("operational_max_input_price", row["input_price"]),
            reservation_output_price=row.get("operational_max_output_price", row["output_price"]),
            request_fee=row["request_fee"],
            reservation_basis=(
                "estimated_1M_input_tokens_no_documented_provider_limit"
                if jev
                else "frozen_endpoint_context_and_max_input_tariff"
            ),
        )
    return result


def plan(batch, *, orchestration=None):
    catalog = models()
    jobs = []

    def add(key, seed, size, dollars, tokens=None, split="development", trial=0):
        jobs.append(
            dict(
                id=f"{batch}-{key}-{seed}-{size}-{trial}-d{dollars}-t{tokens}",
                model=key,
                seed=seed,
                split=split,
                size=size,
                trial=trial,
                dollar_cap=dollars,
                token_cap=tokens,
            )
        )

    if batch == "revised_canary":
        stage, previous = "corrected_validation", "offline"
        for key in CONTROLS:
            add(key, 60000, 3, 0.15)
    elif batch == "roster_canary":
        stage, previous = "roster_canary", "revised_canary"
        for key in catalog:
            add(key, 60100, 3, 0.4)
    elif batch in ("main_1", "main_2", "confirmation", "repeat_1", "tokens_1"):
        stage = {
            "main_1": "main",
            "main_2": "main",
            "confirmation": "confirmation",
            "repeat_1": "repeat",
            "tokens_1": "tokens",
        }[batch]
        previous = {
            "main_1": "roster_canary",
            "tokens_1": "main_1",
            "repeat_1": "tokens_1",
            "confirmation": "repeat_1",
            "main_2": "confirmation",
        }[batch]
        seed_base = {
            "main_1": 61000,
            "main_2": 62000,
            "confirmation": 63000,
            "repeat_1": 61000,
            "tokens_1": 61000,
        }[batch]
        for size in (2, 3, 4):
            budgets = (
                (512, 2048, 8192)
                if stage == "tokens"
                else (
                    (0.15,)
                    if stage == "repeat"
                    else (0.4,)
                    if stage == "confirmation"
                    else (0.05, 0.15, 0.4)
                )
            )
            for budget in budgets:
                for key, model in catalog.items():
                    if stage == "tokens" and model.provider == "TypeSafe":
                        continue
                    add(
                        key,
                        seed_base + size,
                        size,
                        1.0 if stage == "tokens" else budget,
                        budget if stage == "tokens" else None,
                        split="confirmation" if stage == "confirmation" else "main",
                        trial=1 if stage == "repeat" else 0,
                    )
    else:
        raise ValueError("unknown batch")
    return dict(
        batch=batch,
        epoch="r2",
        orchestration=orchestration or orchestration_descriptor(),
        per_call_output_ceiling=16384,
        stage=stage,
        previous=previous,
        protocol=PROTOCOL,
        protocol_hash=protocol_hash(),
        configuration_hash=digest({k: asdict(v) for k, v in catalog.items()}),
        roster_sha256=hashlib.sha256(
            (ROOT / "data/v2/release-r2-roster.json").read_bytes()
        ).hexdigest(),
        opening_balance=prior_accounting(),
        models={k: asdict(catalog[k]) for k in sorted({j["model"] for j in jobs})},
        jobs=jobs,
        max_decisions=160,
        recovery_policy="manual-transport-replacement-once-1",
        cost_policy="Actual paid cost includes the first non-admitted boundary response. "
        "Operational reservations constrain stage/global spending, not scored admission. "
        "TypeSafe reservation is an explicit estimate, not a guaranteed provider maximum.",
    )


def archive_source(manifest):
    descriptor = manifest["orchestration"]
    if descriptor != orchestration_descriptor():
        raise ValueError("current orchestration differs from frozen campaign")
    archive = ROOT / descriptor["archive"]
    files = sorted((ROOT / "src").rglob("*.py"))
    files += [ROOT / name for name in descriptor["scripts"]]
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        with zipfile.ZipFile(archive) as bundle:
            if set(bundle.namelist()) != {str(p.relative_to(ROOT)) for p in files} or any(
                bundle.read(str(p.relative_to(ROOT))) != p.read_bytes() for p in files
            ):
                raise ValueError("source archive differs; never overwrite evidence")
        return
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(ROOT))


def offline():
    rows = []
    for split in ("development", "pilot", "main", "confirmation"):
        for size in (2, 3, 4):
            for seed in range(20):
                ep = scripted_episode(generate(seed, split, size))
                if ep["quality"] != 100 or ep["invalid"] or not verify_replay(ep):
                    raise ValueError(f"infeasible kitchen: {split}/{size}/{seed}")
                rows.append(dict(split=split, size=size, seed=seed, actions=len(ep["actions"])))
    report = dict(
        status="passed",
        protocol=PROTOCOL,
        protocol_hash=protocol_hash(),
        solved_replayed_instances=len(rows),
        instances=rows,
    )
    write_json(DATA / "offline-gate.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "instances"}), flush=True)


def dispatch_width(jobs, manifest, ledger, workers):
    """Fit whole initial request reservations before creating any episode artifact."""
    with ledger.connect() as con:
        global_room = ledger.cap - ledger.opening_balance - Ledger._used(con)
        stage_room = units(STAGES[manifest["stage"]]) - Ledger._used(
            con, "WHERE stage=?", (manifest["stage"],)
        )
    room = min(global_room, stage_room)
    width = 0
    for job in jobs[:workers]:
        model = Model(**manifest["models"][job["model"]])
        maximum = min(model.max_output, job["token_cap"]) if job["token_cap"] else model.max_output
        _, amount = Client(model).bound({"max_tokens": maximum})
        needed = units(amount)
        if needed > room:
            break
        room -= needed
        width += 1
    return width


def authorize_recovery(batch, job_id, note):
    """Manual disposition only: validate evidence and retain liability, never dispatch."""
    from audit_release_v2 import recovery_paths, verify_archive, verify_campaign, verify_episode

    manifest_path = DATA / f"{batch}-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    verify_campaign(ROOT, manifest)
    verify_archive(ROOT, manifest)
    matches = [job for job in manifest["jobs"] if job["id"] == job_id]
    if len(matches) != 1 or not note.strip():
        raise ValueError("recovery requires one canonical manifest job and review note")
    job = matches[0]
    disposition, replacement = recovery_paths(ROOT, batch, job_id)
    if disposition.exists() or replacement.exists():
        raise ValueError("one replacement already authorized; no further retries")
    original_path = RUNS / batch / f"{job_id}.json"
    ep = json.loads(original_path.read_text())
    ledger = Ledger(RUNS / "spend.sqlite", cap=200, opening_balance=manifest["opening_balance"])
    with ledger.connect() as con:
        rows = con.execute(
            "SELECT id,episode,stage,reserved,actual,status FROM requests"
        ).fetchall()
    if any(row[5] in ("reserved", "overrun") for row in rows):
        raise ValueError("live request or overrun must be resolved before recovery")
    entries = {row[0]: tuple(row[1:]) for row in rows}
    used = set()
    verify_episode(ep, job, manifest, entries, used, failed=True, allow_uncertain=True)
    if used != {rid for rid, row in entries.items() if row[0] == job_id}:
        raise ValueError("original attempt has unaccounted requests")
    failed_rid = ep["requests"][-1]["id"]
    if entries[failed_rid][4] == "uncertain":
        ledger.retain_failed_liability(failed_rid)
    record = dict(
        policy=manifest["recovery_policy"],
        job=job_id,
        original_sha256=hashlib.sha256(original_path.read_bytes()).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        replacement_id=job_id + "--attempt2",
        replacement_path=replacement.relative_to(ROOT).as_posix(),
        review_note=note,
    )
    disposition.parent.mkdir(parents=True, exist_ok=True)
    with disposition.open("x") as file:
        json.dump(record, file, indent=2, sort_keys=True)
        file.write("\n")
    (DATA / f"{batch}-gate.json").unlink(missing_ok=True)
    print(json.dumps(dict(status="replacement_authorized_not_dispatched", **record)), flush=True)


def paid(batch, workers):
    manifest = plan(batch)
    campaign = {k: manifest[k] for k in CAMPAIGN_FIELDS}
    campaign_path = DATA / "campaign.json"
    if campaign_path.exists():
        if json.loads(campaign_path.read_text()) != campaign:
            raise ValueError("campaign configuration changed; do not reuse prior gates")
    elif batch != "revised_canary":
        raise ValueError("campaign must start with the revised canary")
    gate_path = DATA / f"{manifest['previous']}-gate.json"
    if not gate_path.exists():
        raise ValueError("previous checkpoint audit missing")
    gate = json.loads(gate_path.read_text())
    if gate["status"] != "passed" or gate["protocol_hash"] != protocol_hash():
        raise ValueError("previous checkpoint did not pass for this protocol")
    if manifest["previous"] != "offline":
        previous_path = DATA / f"{manifest['previous']}-manifest.json"
        if gate.get("manifest_sha256") != hashlib.sha256(previous_path.read_bytes()).hexdigest():
            raise ValueError("predecessor manifest no longer matches its audited gate")
        previous = json.loads(previous_path.read_text())
        if previous["configuration_hash"] != manifest["configuration_hash"]:
            raise ValueError("predecessor gate used different configurations")
    offline_gate = json.loads((DATA / "offline-gate.json").read_text())
    if offline_gate["protocol_hash"] != protocol_hash() or offline_gate["status"] != "passed":
        raise ValueError("offline gate stale")
    manifest_path = DATA / f"{batch}-manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("manifest changed: do not mix or overwrite experiments")
    write_json(manifest_path, manifest)
    archive_source(manifest)
    if not campaign_path.exists():
        with campaign_path.open("x") as file:
            json.dump(campaign, file, indent=2, sort_keys=True)
            file.write("\n")
    ledger = Ledger(RUNS / "spend.sqlite", cap=200, opening_balance=manifest["opening_balance"])
    snapshot = ledger.snapshot()
    if snapshot["unresolved_requests"] or snapshot["overruns"]:
        raise ValueError("accounting audit required before dispatch")
    load_env(ROOT / ".env")

    from audit_release_v2 import recovery_record

    def destination(job):
        disposition = recovery_record(ROOT, manifest, job)
        if disposition:
            return ROOT / disposition["replacement_path"], disposition["replacement_id"]
        return RUNS / batch / f"{job['id']}.json", job["id"]

    def run(job):
        output, episode_id = destination(job)
        if output.exists():
            prior = json.loads(output.read_text())
            if prior["status"] != "complete":
                raise ValueError(
                    "unfinished attempt requires audit; never automatically redispatch"
                )
            return prior
        client = Client(Model(**manifest["models"][job["model"]]))
        return run_episode(
            generate(job["seed"], job["split"], job["size"]),
            client,
            episode_id=episode_id,
            stage=manifest["stage"],
            dollar_cap=job["dollar_cap"],
            token_cap=job["token_cap"],
            ledger=ledger,
            output=output,
        )

    # Completed jobs require no new reserve. An incomplete attempt must be audited;
    # neither resume nor failure disposition can silently alter the declared job grid.
    pending = []
    for job in manifest["jobs"]:
        output, _ = destination(job)
        if not output.exists():
            pending.append(job)
        elif json.loads(output.read_text())["status"] != "complete":
            raise ValueError("unfinished attempt requires audit before new dispatch")
    # Recompute headroom between groups. Narrow concurrency when full initial reserves
    # cannot coexist; if even one cannot fit, leave unattempted jobs without artifacts.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        start = 0
        while start < len(pending):
            width = dispatch_width(pending[start:], manifest, ledger, workers)
            if not width:
                print(
                    json.dumps(
                        dict(
                            batch=batch,
                            status="operational_budget_stop",
                            next_job=pending[start]["id"],
                            unattempted_jobs=len(pending) - start,
                            accounting=ledger.snapshot(),
                        )
                    ),
                    flush=True,
                )
                return
            results = list(pool.map(run, pending[start : start + width]))
            start += width
            for ep in results:
                print(
                    json.dumps(
                        dict(
                            id=ep["id"],
                            status=ep["status"],
                            reason=ep["reason"],
                            quality=ep["quality"],
                            cost_usd=ep["cost_usd"],
                            actions=len(ep["actions"]),
                        )
                    ),
                    flush=True,
                )
            if any(ep["status"] != "complete" for ep in results):
                raise RuntimeError("batch stopped: review incomplete/quarantined attempts")
    print(json.dumps(dict(batch=batch, accounting=ledger.snapshot())), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("offline", "plan", "run", "recover"))
    parser.add_argument("--batch", default="revised_canary")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--job")
    parser.add_argument("--note")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    if args.command == "offline":
        offline()
    elif args.command == "plan":
        print(json.dumps(plan(args.batch), indent=2))
    elif args.command == "recover":
        if not args.job or not args.note:
            parser.error("recover requires --job and --note")
        authorize_recovery(args.batch, args.job, args.note)
    else:
        paid(args.batch, args.workers)


if __name__ == "__main__":
    main()
