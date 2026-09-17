#!/usr/bin/env python3
"""Independent, read-only verification of a complete v2 release batch before gating it."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import types
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2.analysis import summarize  # noqa: E402
from kitchenrush.v2.environment import (  # noqa: E402
    MAX_DECISIONS,
    PROTOCOL,
    Engine,
    decision,
    digest,
    generate,
    protocol_hash,
    public_state,
    quality,
    scripted_episode,
    serialize_spec,
)
from kitchenrush.v2.ledger import STAGES  # noqa: E402
from kitchenrush.v2.providers import Client, Model  # noqa: E402

SCALE = 1_000_000_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def money(value):
    require(
        type(value) in (int, float) and math.isfinite(value) and value >= 0,
        "invalid monetary value",
    )
    return math.ceil(value * SCALE - 1e-9)


def read_json(path):
    return json.loads(path.read_text())


def normalized_payload(payload):
    result = json.loads(json.dumps(payload))
    if "messages" in result:
        result["messages"][1]["content"] = json.loads(result["messages"][1]["content"])
    return result


def response_usage(response, model):
    usage = response["usage"]
    jev = model.provider == "TypeSafe"
    ni = usage["input_tokens" if jev else "prompt_tokens"]
    no = usage["output_tokens" if jev else "completion_tokens"]
    require(all(type(n) is int and n >= 0 for n in (ni, no)), "invalid response token counts")
    cost = ni * model.input_price if jev else float(usage["cost"])
    money(cost)
    require(response.get("model") == model.model, "response model identity mismatch")
    if not jev:
        require(
            response.get("provider", "").casefold() == model.provider.casefold(),
            "response provider identity mismatch",
        )
    return ni, no, cost


def response_selection(response, model):
    # Independently decode native replies, including invalid/refused/truncated generations.
    try:
        if model.provider == "TypeSafe":
            answer = response["answers"]["next_action"]
            return answer["choice"] if answer["type"] == "choice" else None
        choice = response["choices"][0]
        if choice["finish_reason"] != "stop":
            return None
        answer = json.loads(choice["message"]["content"])
        return answer["action"] if isinstance(answer, dict) and set(answer) == {"action"} else None
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def ledger_snapshot(path, opening):
    require(path.is_file(), "missing release ledger")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("BEGIN")
        require(
            con.execute("SELECT cap FROM meta WHERE id=1").fetchone() == (200 * SCALE,),
            "release ledger does not have immutable $200 ceiling",
        )
        require(
            con.execute("SELECT amount FROM opening WHERE id=1").fetchone() == (money(opening),),
            "opening balance mismatch",
        )
        rows = con.execute(
            "SELECT id,episode,stage,reserved,actual,status FROM requests ORDER BY rowid"
        ).fetchall()
    require(
        all(r[5] in ("settled", "retained_failure") for r in rows),
        "ledger has unresolved requests, overrun, or unknown status",
    )
    require(
        all(
            (r[5] == "settled" and type(r[4]) is int and 0 <= r[4] <= r[3])
            or (r[5] == "retained_failure" and r[4] is None)
            for r in rows
        ),
        "invalid settled/retained ledger row",
    )
    stage_totals = Counter()
    for row in rows:
        require(row[2] in STAGES, "unknown ledger stage")
        stage_totals[row[2]] += row[4] if row[4] is not None else row[3]
    require(
        all(total <= money(STAGES[stage]) for stage, total in stage_totals.items()),
        "stage spending exceeds its ceiling",
    )
    accounted = money(opening) + sum(r[4] if r[4] is not None else r[3] for r in rows)
    require(accounted <= 200 * SCALE, "global spending exceeds $200")
    return {r[0]: r[1:] for r in rows}, {
        "cap_usd": 200.0,
        "prior_accounted_usd": opening,
        "accounted_usd": accounted / SCALE,
        "settled_usd": sum(r[4] or 0 for r in rows) / SCALE,
        "retained_liability_usd": sum(r[3] for r in rows if r[4] is None) / SCALE,
        "requests": len(rows),
        "unresolved_requests": 0,
        "overruns": 0,
    }


def verify_archive(root, manifest):
    require(manifest["protocol_hash"] == protocol_hash(), "current protocol differs from batch")
    descriptor = manifest["orchestration"]
    require(descriptor.get("version") == 1, "unknown orchestration contract")
    scripts = {"scripts/release_v2.py", "scripts/audit_release_v2.py", "scripts/audit_roster_v2.py"}
    require(set(descriptor["scripts"]) == scripts, "orchestration scripts differ")
    identity = digest(dict(protocol_hash=manifest["protocol_hash"], scripts=descriptor["scripts"]))
    require(
        descriptor["archive"] == f"data/v2/release-r2/source-{identity}.zip", "archive path differs"
    )
    campaign = read_json(root / "data/v2/release-r2/campaign.json")
    require(campaign["orchestration"] == descriptor, "source not bound by immutable campaign")
    archive = root / descriptor["archive"]
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        package = {p.relative_to(root).as_posix() for p in (root / "src").rglob("*.py")}
        require(len(names) == len(set(names)), "duplicate archive members")
        require(set(names) == package | scripts, "archive source membership differs")
        for name in package:
            require((root / name).read_bytes() == z.read(name), f"archived core differs: {name}")
        for name in scripts:
            require(
                hashlib.sha256(z.read(name)).hexdigest() == descriptor["scripts"][name],
                f"archived orchestration differs: {name}",
            )
        source = z.read("scripts/release_v2.py")
    # Only execute a runner after its bytes and frozen core have been independently verified.
    runner = types.ModuleType("release_audit_archived_plan")
    runner.__file__ = str(root / "scripts/release_v2.py")
    exec(compile(source, runner.__file__, "exec"), runner.__dict__)
    runner.ROOT = root
    expected = runner.plan(manifest["batch"], orchestration=descriptor)
    require(manifest == expected, "manifest differs from complete frozen batch plan")
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def verify_amendment(root, manifest, *, current=False):
    path = root / "data/v2/release-r2/peer-pause-amendment.json"
    require(path.is_file(), "explicit peer-pause amendment missing")
    amendment = read_json(path)
    campaign = read_json(root / "data/v2/release-r2/campaign.json")
    require(
        amendment.get("policy") == "manual-peer-pause-replacement-once-1"
        and amendment.get("baseline_campaign") == campaign
        and bool(amendment.get("review_note", "").strip()),
        "amendment baseline differs",
    )
    require(manifest["orchestration"] == campaign["orchestration"], "amendment campaign mismatch")
    descriptor = amendment["amended_orchestration"]
    scripts = set(campaign["orchestration"]["scripts"])
    require(
        descriptor.get("version") == 1 and set(descriptor["scripts"]) == scripts,
        "amended script membership differs",
    )
    identity = digest(dict(protocol_hash=manifest["protocol_hash"], scripts=descriptor["scripts"]))
    require(
        descriptor["archive"] == f"data/v2/release-r2/source-{identity}.zip", "amended path differs"
    )
    archive = root / descriptor["archive"]
    require(
        hashlib.sha256(archive.read_bytes()).hexdigest() == amendment["archive_sha256"],
        "amended archive hash differs",
    )
    package = {p.relative_to(root).as_posix() for p in (root / "src").rglob("*.py")}
    with zipfile.ZipFile(archive) as bundle:
        require(
            len(bundle.namelist()) == len(set(bundle.namelist()))
            and set(bundle.namelist()) == package | scripts,
            "amended archive membership differs",
        )
        for name in package:
            require(
                bundle.read(name) == (root / name).read_bytes(), "amendment changes frozen core"
            )
        for name in scripts:
            require(
                hashlib.sha256(bundle.read(name)).hexdigest() == descriptor["scripts"][name],
                "amended script hash differs",
            )
            if current:
                require(
                    bundle.read(name) == (root / name).read_bytes(),
                    "unlogged current driver change",
                )
    return dict(
        path=path.relative_to(root).as_posix(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_archive=descriptor["archive"],
        source_archive_sha256=amendment["archive_sha256"],
    )


def verify_current_orchestration(root, manifest):
    from funding_recovery_v2 import AMENDMENT
    from funding_recovery_v2 import verify_amendment as verify_funding

    if (root / AMENDMENT).exists():
        return verify_funding(root, manifest, current=True)
    return verify_amendment(root, manifest, current=True)


def verify_campaign(root, manifest):
    roster_path = root / "data/v2/release-r2-roster.json"
    roster = read_json(roster_path)
    original = read_json(root / "data/v2/roster.json")
    historical = Counter(row["label"] for row in original["models"])
    retained = Counter(label for row in roster["models"] for label in row["historical_labels"])
    require(retained == historical, "release roster drops or duplicates historical labels")
    rows = roster["models"]
    require(
        len({r["key"] for r in rows}) == len(rows) == roster["configuration_count"],
        "release roster configuration count/key mismatch",
    )
    require(len(rows) == original["canonical_entry_count"], "canonical roster coverage changed")
    require(
        sum(retained.values()) == roster["historical_label_count"],
        "historical roster count mismatch",
    )
    blocked = []
    for row in rows:
        if row["status"].startswith("blocked"):
            require(bool(row.get("block_reason")), "blocked model has no explicit reason")
            blocked.append({"key": row["key"], "reason": row["block_reason"]})
        else:
            require(
                row["status"]
                in ("pending_canary", "passed_canary", "pending_corrected_protocol_canary"),
                "unrecognized release roster eligibility status",
            )
    require(
        hashlib.sha256(roster_path.read_bytes()).hexdigest() == manifest["roster_sha256"],
        "manifest does not bind current frozen roster",
    )
    campaign = {
        key: manifest[key]
        for key in (
            "protocol_hash",
            "configuration_hash",
            "roster_sha256",
            "opening_balance",
            "orchestration",
        )
    }
    require(
        read_json(root / "data/v2/release-r2/campaign.json") == campaign,
        "batch differs from immutable campaign configuration",
    )
    return {
        "total_configurations": len(rows),
        "eligible_configurations": len(rows) - len(blocked),
        "blocked_configurations": blocked,
        "historical_labels": sum(retained.values()),
    }


def verify_grid(manifest):
    jobs = manifest["jobs"]
    require(jobs and manifest["models"], "empty manifest")
    require(len({j["id"] for j in jobs}) == len(jobs), "duplicate episode ids")
    require(
        {j["model"] for j in jobs} == set(manifest["models"]),
        "manifest models and evaluated models differ",
    )
    semantic = set()
    by_view = defaultdict(lambda: defaultdict(Counter))
    by_budget = defaultdict(lambda: defaultdict(Counter))
    for job in jobs:
        require(type(job["trial"]) is int and job["trial"] >= 0, "invalid trial")
        require(type(job["seed"]) is int and job["size"] in (2, 3, 4), "invalid seed/family")
        money(job["dollar_cap"])
        require(
            job["token_cap"] is None or (type(job["token_cap"]) is int and job["token_cap"] > 0),
            "invalid token cap",
        )
        require(Path(job["id"]).name == job["id"], "episode id is not a filename")
        view = "USD" if job["token_cap"] is None else "tokens"
        task = (job["split"], job["size"], job["seed"], job["trial"])
        budget = (job["dollar_cap"], job["token_cap"])
        key = (job["model"], *task, *budget)
        require(key not in semantic, "duplicate semantic experiment")
        semantic.add(key)
        by_view[view][job["model"]][(*task, *budget)] += 1
        by_budget[(view, job["model"])][budget][task] += 1
        if view == "tokens":
            require(
                manifest["models"][job["model"]]["provider"] != "TypeSafe",
                "Jev cannot enter generation-token track",
            )
    for view, models in by_view.items():
        grids = list(models.values())
        require(all(g == grids[0] for g in grids), f"unmatched model grid in {view}")
    for key, budgets in by_budget.items():
        grids = list(budgets.values())
        require(all(g == grids[0] for g in grids), f"different kitchens across budgets: {key}")


def eligible_transport_failure(ep):
    error = ep.get("error", "")
    allowed = bool(
        re.fullmatch(r"ProviderError: HTTP (408|429|5[0-9]{2}); no automatic retry", error)
        or re.fullmatch(r"ProviderError: transport failure [A-Za-z]+; no automatic retry", error)
    )
    require(
        ep.get("status") == "quarantined"
        and ep.get("reason") == "provider_or_accounting_failure"
        and allowed
        and ep.get("requests")
        and "response" not in ep["requests"][-1],
        "only terminal transport/408/429/5xx failures without responses may be replaced",
    )


def verify_episode(
    ep,
    job,
    manifest,
    ledger,
    used_requests,
    *,
    failed=False,
    allow_uncertain=False,
    paused=False,
    funding=False,
):
    if funding:
        from funding_recovery_v2 import eligible_funding_failure

        eligible_funding_failure(ep, job)
        failed = True
    elif failed:
        eligible_transport_failure(ep)
    label = job["id"]
    require(
        ep["id"] == label
        and ep["status"] == ("incomplete" if paused else "quarantined" if failed else "complete"),
        f"incomplete episode {label}",
    )
    require(
        ep["protocol"] == PROTOCOL and ep["protocol_hash"] == manifest["protocol_hash"],
        f"protocol mismatch {label}",
    )
    require(ep["stage"] == manifest["stage"], f"stage mismatch {label}")
    require(
        ep["accounting_contract"] == "actual-cost-action-admission-1",
        "wrong accounting contract",
    )
    model = Model(**manifest["models"][job["model"]])
    require(ep["model"] == asdict(model) and model.key == job["model"], "model config mismatch")
    require(
        ep["dollar_cap"] == job["dollar_cap"] and ep["token_cap"] == job["token_cap"],
        "resource allowance mismatch",
    )
    spec = generate(job["seed"], job["split"], job["size"])
    require(
        ep["spec"] == serialize_spec(spec) and ep["spec_hash"] == digest(ep["spec"]),
        "instance generation/hash mismatch",
    )
    witness = scripted_episode(spec)
    require(
        witness["quality"] == 100
        and witness["invalid"] == 0
        and ep["feasibility_witness_hash"] == digest(witness["actions"]),
        "feasibility witness mismatch",
    )
    engine, client = Engine(spec, record_trace=True), Client(model)
    spent = ni_total = no_total = admitted = accepted = boundary = 0
    for index, record in enumerate(ep["requests"]):
        require(not engine.terminated and admitted < MAX_DECISIONS, "request after game end")
        require(spent < money(ep["dollar_cap"]), "request after dollar allowance exhausted")
        remaining_tokens = None if ep["token_cap"] is None else ep["token_cap"] - no_total
        require(remaining_tokens is None or remaining_tokens > 0, "request after token exhaustion")
        remaining_usd = max(0, ep["dollar_cap"] - spent / SCALE)
        resource = {
            "kind": "USD" if remaining_tokens is None else "generation_tokens",
            "allowance": ep["dollar_cap"] if remaining_tokens is None else ep["token_cap"],
            "remaining": remaining_usd if remaining_tokens is None else remaining_tokens,
            "decisions_remaining": MAX_DECISIONS - admitted,
        }
        canonical, options = decision(public_state(engine), resource)
        require(record["canonical"] == canonical, "altered/hidden information in request")
        maximum = (
            model.max_output
            if remaining_tokens is None
            else min(model.max_output, remaining_tokens)
        )
        payload = client.payload(canonical, maximum)
        require(
            normalized_payload(record["payload"]) == normalized_payload(payload),
            "altered provider payload/assistance",
        )
        require(
            model.prompt_limit is not None and model.prompt_limit > 0, "missing operational cap"
        )
        reserve = (
            model.prompt_limit * (model.reservation_input_price or model.input_price)
            + (
                0
                if model.provider == "TypeSafe"
                else maximum
                * (
                    model.reservation_output_price
                    if model.reservation_output_price is not None
                    else model.output_price
                )
            )
            + model.request_fee
        )
        require(
            record["reserved_usd"] == reserve
            and record["reservation_basis"] == model.reservation_basis,
            "reservation mismatch",
        )
        rid = f"{label}:{index}"
        require(record["id"] == rid and rid not in used_requests, "request identity reused/altered")
        used_requests.add(rid)
        if "response" not in record:
            require(failed and index == len(ep["requests"]) - 1, "missing nonterminal response")
            require(
                record["admitted"] is False
                and not any(
                    k in record
                    for k in (
                        "selection",
                        "post_quality",
                        "post_state_hash",
                        "cost_usd",
                        "input_tokens",
                        "output_tokens",
                    )
                ),
                "failed request has interpreted response",
            )
            row = ledger.get(rid)
            statuses = (
                ("retained_failure", "uncertain") if allow_uncertain else ("retained_failure",)
            )
            require(
                row is not None
                and row[:4] == (label, manifest["stage"], money(reserve), None)
                and row[4] in statuses,
                "failed request liability not fully retained",
            )
            spent += money(reserve)
            continue
        ni, no, cost = response_usage(record["response"], model)
        require(
            ni <= model.prompt_limit and (model.provider == "TypeSafe" or no <= maximum),
            "provider token bound exceeded",
        )
        require(
            (ni, no) == (record["input_tokens"], record["output_tokens"])
            and cost == record["cost_usd"],
            "usage-derived accounting mismatch",
        )
        require(
            ledger.get(rid) == (label, manifest["stage"], money(reserve), money(cost), "settled"),
            "request does not reconcile to settled ledger row",
        )
        ni_total += ni
        no_total += no
        spent += money(cost)
        if spent > money(ep["dollar_cap"]):
            require(
                index == len(ep["requests"]) - 1 and record["admitted"] is False,
                "boundary request was admitted or followed by another request",
            )
            require(
                not any(k in record for k in ("selection", "post_quality", "post_state_hash")),
                "boundary response interpreted as an action",
            )
            boundary = money(cost)
            continue
        require(record["admitted"] is True, "affordable action not admitted")
        selection = response_selection(record["response"], model)
        require(record["selection"] == selection, "stored selection differs from response")
        selected = options.get(selection) if isinstance(selection, str) else None
        call = selected or ToolCall("invalid_model_selection", {})
        require(
            admitted < len(ep["actions"]) and ep["actions"][admitted] == asdict(call),
            "executed action differs from selected candidate",
        )
        engine.step([call])
        admitted += 1
        accepted = spent
        require(
            sum(f["kind"] == "action" for f in engine.trace) == admitted,
            "selection/action-frame count mismatch",
        )
        require(
            record["post_state_hash"] == digest(public_state(engine))
            and record["post_quality"] == quality(engine),
            "post-action state/quality mismatch",
        )
    require(admitted == len(ep["actions"]), "unaccounted actions")
    require(
        ep["input_tokens"] == ni_total and ep["output_tokens"] == no_total,
        "token total mismatch",
    )
    require(
        ep["token_cap"] is None or no_total <= ep["token_cap"],
        "episode generation cap exceeded",
    )
    require(
        ep["cost_usd"] == spent / SCALE
        and ep["accepted_cost_usd"] == accepted / SCALE
        and ep["boundary_cost_usd"] == boundary / SCALE,
        "episode cost totals mismatch",
    )
    require(accepted <= money(ep["dollar_cap"]), "accepted action cost exceeds allowance")
    if paused:
        require(
            not engine.terminated
            and admitted < MAX_DECISIONS
            and not boundary
            and spent < money(ep["dollar_cap"])
            and (ep["token_cap"] is None or no_total < ep["token_cap"]),
            "operational pause occurred after task/resource exhaustion",
        )
        reason = "operational_spending_gate"
    elif failed:
        reason = "provider_or_accounting_failure"
    elif boundary:
        reason = "dollar_budget_exhausted"
    elif engine.terminated:
        reason = "game_terminal"
    elif admitted == MAX_DECISIONS:
        reason = "decision_limit"
    elif ep["token_cap"] is not None and no_total == ep["token_cap"]:
        reason = "token_budget_exhausted"
    elif spent == money(ep["dollar_cap"]):
        reason = "dollar_budget_exhausted"
    else:
        raise ValueError(f"unjustified stopping in {label}")
    require(ep["reason"] == reason, "incorrect termination reason")
    require(
        ep["completed"] == engine.counters["orders_served"]
        and ep["invalid"] == engine.counters["invalid_actions"]
        and ep["invalid_by_reason"] == engine.counters["invalid_by_reason"]
        and ep["quality"] == quality(engine),
        "final score/counter mismatch",
    )
    require(digest(ep["frames"]) == digest(engine.trace), "fresh frame replay mismatch")
    # Set this only after independent reconstruction; never trust the saved flag.
    ep["replay_verified"] = True
    return ep, spent, boundary


def recovery_paths(root, batch, job_id):
    return (
        root / f"data/v2/release-r2/{batch}-recoveries/{job_id}.json",
        root / f"runs/v2/release-r2/{batch}/attempts/{job_id}--attempt2.json",
    )


def verify_peer_pause(root, manifest, job, ep, ledger, snapshot, peer_job, *, funding=False):
    from funding_recovery_v2 import FUNDING_JOB, ORIGINAL_HASHES, POLICY

    if funding:
        require(
            job["id"] in ORIGINAL_HASHES and job["id"] != FUNDING_JOB and peer_job == FUNDING_JOB,
            "funding peer outside incident scope",
        )
    verify_episode(ep, job, manifest, ledger, set(), paused=True)
    require(peer_job != job["id"], "pause cannot cite itself")
    peers = [j for j in manifest["jobs"] if j["id"] == peer_job]
    require(len(peers) == 1, "peer must be a canonical job in this batch")
    peer_disposition, _ = recovery_paths(root, manifest["batch"], peer_job)
    require(
        peer_disposition.is_file()
        and read_json(peer_disposition).get("policy")
        == (POLICY if funding else "manual-transport-replacement-once-1"),
        "peer transport failure needs its own retained-liability disposition",
    )
    recovery_record(root, manifest, peers[0])
    peer_path = root / f"runs/v2/release-r2/{manifest['batch']}/{peer_job}.json"
    verify_episode(
        read_json(peer_path), peers[0], manifest, ledger, set(), failed=not funding, funding=funding
    )
    count = snapshot["row_count"]
    require(type(count) is int and 0 < count <= len(ledger), "invalid accounting snapshot count")
    rows = list(ledger.items())[:count]
    require(digest(rows) == snapshot["rows_sha256"], "authorization accounting snapshot changed")
    require(
        all(row[4] in ("settled", "retained_failure") for _, row in rows),
        "authorization snapshot has live/unresolved accounting",
    )
    ids = {rid for rid, _ in rows}
    require(all(r["id"] in ids for r in ep["requests"]), "snapshot predates paused prefix")
    peer_failed = read_json(peer_path)["requests"][-1]["id"]
    require(peer_failed in ids, "snapshot predates retained peer liability")
    global_used = money(manifest["opening_balance"]) + sum(
        r[3] if r[3] is not None else r[2] for _, r in rows
    )
    stage_used = sum(
        r[3] if r[3] is not None else r[2] for _, r in rows if r[1] == manifest["stage"]
    )
    model = Model(**manifest["models"][job["model"]])
    maximum = (
        model.max_output
        if ep["token_cap"] is None
        else min(model.max_output, ep["token_cap"] - ep["output_tokens"])
    )
    _, reserve = Client(model).bound({"max_tokens": maximum})
    require(
        global_used + money(reserve) <= 200 * SCALE
        and stage_used + money(reserve) <= money(STAGES[manifest["stage"]]),
        "next paused request could not fit after peer accounting resolution",
    )


def recovery_record(root, manifest, job):
    from funding_recovery_v2 import PEER_POLICY, POLICY, verify_funded
    from funding_recovery_v2 import verify_amendment as verify_funding

    disposition, replacement = recovery_paths(root, manifest["batch"], job["id"])
    if not disposition.exists():
        require(not replacement.exists(), "replacement has no manual disposition")
        return None
    record = read_json(disposition)
    original_path = root / f"runs/v2/release-r2/{manifest['batch']}/{job['id']}.json"
    manifest_path = root / f"data/v2/release-r2/{manifest['batch']}-manifest.json"
    require(
        record.get("policy")
        in (
            "manual-transport-replacement-once-1",
            "manual-peer-pause-replacement-once-1",
            POLICY,
            PEER_POLICY,
        )
        and record.get("job") == job["id"]
        and record.get("replacement_id") == job["id"] + "--attempt2"
        and record.get("replacement_path") == replacement.relative_to(root).as_posix()
        and record.get("original_sha256") == hashlib.sha256(original_path.read_bytes()).hexdigest()
        and record.get("manifest_sha256") == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        and bool(record.get("review_note", "").strip()),
        "invalid recovery disposition",
    )
    funding = record["policy"] in (POLICY, PEER_POLICY)
    if funding:
        amendment = verify_funding(root, manifest)
        require(
            record.get("amendment_sha256") == amendment["sha256"],
            "funding amendment binding differs",
        )
        verify_funded(root, record["funded_evidence"], 6.24576)
    if record["policy"] == POLICY:
        from funding_recovery_v2 import eligible_funding_failure

        eligible_funding_failure(read_json(original_path), job)
    elif record["policy"] == "manual-transport-replacement-once-1":
        eligible_transport_failure(read_json(original_path))
    else:
        amendment = verify_funding(root, manifest) if funding else verify_amendment(root, manifest)
        require(
            record.get("amendment_sha256") == amendment["sha256"], "pause amendment binding differs"
        )
        ledger, _ = ledger_snapshot(
            root / "runs/v2/release-r2/spend.sqlite", manifest["opening_balance"]
        )
        verify_peer_pause(
            root,
            manifest,
            job,
            read_json(original_path),
            ledger,
            record["accounting_snapshot"],
            record["peer_failure_job"],
            funding=funding,
        )
    if funding and replacement.exists():
        from funding_recovery_v2 import verify_dispatch

        verify_dispatch(root, manifest, job, record)
    return record


def release_summary(episodes, failed_attempts):
    result = summarize(episodes)
    affected = {(f["model"], f["token_cap"]) for f in failed_attempts if f["token_cap"] is not None}
    for row in result["rows"]:
        row["replacement_affected"] = (
            row["budget_kind"] == "tokens" and (row["model"], row["budget"]) in affected
        )
        row["b80_eligible"] = (
            row["budget_kind"] == "tokens"
            and not row["replacement_affected"]
            and not row["dollar_censored"]
        )
    for model, old in result["B80"].items():
        if not any(key[0] == model for key in affected):
            continue
        usable = sorted(
            (r for r in result["rows"] if r["model"] == model and r["b80_eligible"]),
            key=lambda r: r["budget"],
        )
        mean = next((r["budget"] for r in usable if r["quality"] >= 80), None)
        conservative = next((r["budget"] for r in usable if r["ci95"] and r["ci95"][0] >= 80), None)
        old.update(
            mean=mean,
            conservative=conservative,
            status="reached on unaffected budgets"
            if mean
            else "not reached within unaffected uncensored budgets",
            replacement_affected_budgets=sorted(b for m, b in affected if m == model),
        )
    result["recovery_interpretation"] = (
        "Replacement quality is conditional on a completed attempt. Entire replacement-affected "
        "token cells are ineligible for B80; known tokens and costs remain in job_resources. "
        "Unknown response usage is not treated as zero."
    )
    return result


def audit(batch, *, root=ROOT, write_gate=True):
    require(Path(batch).name == batch and batch not in (".", ".."), "invalid batch name")
    from funding_recovery_v2 import AMENDMENT, PEER_POLICY, POLICY, gate_path
    from funding_recovery_v2 import verify_amendment as verify_funding

    target = gate_path(root, batch)
    if write_gate:
        # A failed re-audit must not leave an earlier successful gate looking current.
        target.unlink(missing_ok=True)
    manifest_path = root / f"data/v2/release-r2/{batch}-manifest.json"
    manifest = read_json(manifest_path)
    require(manifest["batch"] == batch, "batch label mismatch")
    verify_grid(manifest)
    coverage = verify_campaign(root, manifest)
    archive_hash = verify_archive(root, manifest)
    amendment = (
        verify_amendment(root, manifest)
        if (root / "data/v2/release-r2/peer-pause-amendment.json").exists()
        else None
    )
    funding_amendment = (
        verify_funding(root, manifest)
        if batch == "roster_canary" and (root / AMENDMENT).exists()
        else None
    )
    opening = manifest["opening_balance"]
    # Reconcile both historical ledgers without double-counting their brought-forward balance.
    prior = 0
    original = None
    for name in ("runs/v2/spend.sqlite", "runs/v2/release/spend.sqlite"):
        old = root / name
        require(old.is_file(), "missing prior spending ledger")
        with sqlite3.connect(old.resolve().as_uri() + "?mode=ro", uri=True) as con:
            rows = con.execute("SELECT reserved,actual,status FROM requests").fetchall()
            require(
                all(r[2] in ("settled", "retained_failure") for r in rows),
                "historical ledger has live, unresolved, or overrun requests",
            )
            amount = sum(r[1] if r[1] is not None else r[0] for r in rows)
            tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            carried = (
                con.execute("SELECT amount FROM opening WHERE id=1").fetchone()[0]
                if "opening" in tables
                else 0
            )
            require(carried == (0 if original is None else original), "historical opening mismatch")
            if original is None:
                original = amount
            prior += amount
    require(prior == money(opening), "opening balance does not reconcile to both prior ledgers")
    ledger, snapshot = ledger_snapshot(root / "runs/v2/release-r2/spend.sqlite", opening)
    folder = root / f"runs/v2/release-r2/{batch}"
    expected_ids = {j["id"] for j in manifest["jobs"]}
    require(
        {p.stem for p in folder.glob("*.json")} == expected_ids,
        "missing or unexpected episode artifacts",
    )
    episodes, used_requests = [], set()
    batch_units = boundary_units = 0
    failed_attempts, evaluated_artifacts, job_resources = [], {}, {}
    recovery_jobs = set()
    for job in manifest["jobs"]:
        original_path = folder / (job["id"] + ".json")
        ep = read_json(original_path)
        disposition = recovery_record(root, manifest, job)
        effective_job = job
        if disposition:
            ep, failure_units, _ = verify_episode(
                ep,
                job,
                manifest,
                ledger,
                used_requests,
                failed=disposition["policy"] == "manual-transport-replacement-once-1",
                paused=disposition["policy"]
                in ("manual-peer-pause-replacement-once-1", PEER_POLICY),
                funding=disposition["policy"] == POLICY,
            )
            known = sum(money(r["cost_usd"]) for r in ep["requests"] if "cost_usd" in r)
            failed_attempts.append(
                dict(
                    job=job["id"],
                    model=job["model"],
                    token_cap=job["token_cap"],
                    known_input_tokens=ep["input_tokens"],
                    known_output_tokens=ep["output_tokens"],
                    unknown_response_usage=disposition["policy"]
                    in ("manual-transport-replacement-once-1", POLICY),
                    original_path=original_path.relative_to(root).as_posix(),
                    original_sha256=disposition["original_sha256"],
                    error=ep.get("error", ep["reason"]),
                    failure_kind="peer_accounting_pause"
                    if disposition["policy"]
                    in ("manual-peer-pause-replacement-once-1", PEER_POLICY)
                    else "funding_pause"
                    if disposition["policy"] == POLICY
                    else "provider_failure",
                    accounted_usd=failure_units / SCALE,
                    known_cost_usd=known / SCALE,
                    retained_liability_usd=(failure_units - known) / SCALE,
                )
            )
            replacement_path = root / disposition["replacement_path"]
            ep = read_json(replacement_path)
            effective_job = dict(job, id=disposition["replacement_id"])
            expected_ids.add(effective_job["id"])
            recovery_jobs.add(job["id"])
            evaluated_artifacts[job["id"]] = disposition["replacement_path"]
        else:
            evaluated_artifacts[job["id"]] = original_path.relative_to(root).as_posix()
        ep, spent, boundary = verify_episode(ep, effective_job, manifest, ledger, used_requests)
        prior_attempt = next((f for f in failed_attempts if f["job"] == job["id"]), {})
        job_resources[job["id"]] = dict(
            known_input_tokens=ep["input_tokens"] + prior_attempt.get("known_input_tokens", 0),
            known_output_tokens=ep["output_tokens"] + prior_attempt.get("known_output_tokens", 0),
            unknown_response_usage=prior_attempt.get("unknown_response_usage", False),
            known_cost_usd=(spent + money(prior_attempt.get("known_cost_usd", 0))) / SCALE,
            retained_liability_usd=prior_attempt.get("retained_liability_usd", 0),
            replacement_affected=bool(prior_attempt),
        )
        episodes.append(ep)
        batch_units += spent
        boundary_units += boundary
    require(
        {p.stem for p in (root / f"data/v2/release-r2/{batch}-recoveries").glob("*.json")}
        == recovery_jobs,
        "unexpected recovery disposition",
    )
    require(
        {p.stem for p in (folder / "attempts").glob("*.json")}
        == {j + "--attempt2" for j in recovery_jobs},
        "unexpected replacement attempt",
    )
    require(
        {rid for rid, row in ledger.items() if row[0] in expected_ids} == used_requests,
        "missing or unaccounted paid request for batch episodes",
    )
    result = {
        "status": "passed",
        "batch": batch,
        "stage": manifest["stage"],
        "episodes": len(episodes),
        "requests_verified": len(used_requests),
        "protocol_hash": manifest["protocol_hash"],
        "source_archive_sha256": archive_hash,
        "operational_amendment": amendment,
        "source_archive": manifest["orchestration"]["archive"],
        "orchestration": manifest["orchestration"],
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "configuration_hash": manifest["configuration_hash"],
        "roster_sha256": manifest["roster_sha256"],
        "roster_coverage": coverage,
        "batch_cost_usd": batch_units / SCALE,
        "failed_attempts": failed_attempts,
        "failed_attempt_count": len(failed_attempts),
        "attempt_count": len(episodes) + len(failed_attempts),
        "failed_attempt_accounted_usd": sum(money(f["accounted_usd"]) for f in failed_attempts)
        / SCALE,
        "all_attempts_accounted_usd": (
            batch_units + sum(money(f["accounted_usd"]) for f in failed_attempts)
        )
        / SCALE,
        "evaluated_artifacts": evaluated_artifacts,
        "job_resources": job_resources,
        "boundary_cost_usd": boundary_units / SCALE,
        "ledger": snapshot,
        "summary": release_summary(episodes, failed_attempts),
    }
    if funding_amendment:
        # Every additional authorization proof remains explicit exportable audit evidence.
        evidence_files = set(funding_amendment["evidence_files"])
        for job in manifest["jobs"]:
            record = recovery_record(root, manifest, job)
            if record and record["policy"] in (POLICY, PEER_POLICY):
                from funding_recovery_v2 import dispatch_path, verify_dispatch

                evidence_files.add(record["funded_evidence"]["path"])
                evidence_files.add(dispatch_path(root, job).relative_to(root).as_posix())
                evidence_files.add(
                    verify_dispatch(root, manifest, job, record)["funded_evidence"]["path"]
                )
        funding_amendment["evidence_files"] = sorted(evidence_files)
        result["funding_operational_amendment"] = funding_amendment
    if write_gate:
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        temporary.replace(target)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    result = audit(args.batch)
    print(json.dumps({k: v for k, v in result.items() if k != "summary"}, indent=2))
