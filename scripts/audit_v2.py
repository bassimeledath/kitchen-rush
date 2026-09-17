#!/usr/bin/env python3
"""Independent gate audit against exact manifests, request accounting and fresh replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import zipfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from v2 import RUN_GENERATION

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2.analysis import summarize  # noqa: E402
from kitchenrush.v2.environment import (  # noqa: E402
    MAX_DECISIONS,
    Engine,
    decision,
    deserialize_spec,
    digest,
    generate,
    protocol_hash,
    public_state,
    serialize_spec,
    verify_replay,
)
from kitchenrush.v2.evaluation import write_json  # noqa: E402
from kitchenrush.v2.ledger import SCALE, Ledger, units  # noqa: E402
from kitchenrush.v2.providers import Client, Model  # noqa: E402


def audit(stage, generation=RUN_GENERATION):
    manifest = json.loads((ROOT / f"data/v2/{stage}-{generation}-manifest.json").read_text())
    archive = ROOT / f"data/v2/source-{manifest['protocol_hash'][:12]}.zip"
    if manifest["protocol_hash"] == protocol_hash():
        with zipfile.ZipFile(archive) as z:
            package_paths = {p.relative_to(ROOT).as_posix()
                             for p in (ROOT / "src/kitchenrush").rglob("*.py")}
            assert package_paths == {n for n in z.namelist() if n.startswith("src/")}
            assert all((ROOT / n).read_bytes() == z.read(n) for n in package_paths), (
                "package changed since source archive, including supporting legacy modules"
            )
    if manifest["protocol_hash"] != protocol_hash():
        with zipfile.ZipFile(archive) as z:
            names = ["engine.py", "config.py", "procgen.py", "oracle.py", "tools.py"]
            names += sorted(
                n.removeprefix("src/kitchenrush/")
                for n in z.namelist()
                if n.startswith("src/kitchenrush/v2/") and n.endswith(".py")
            )
            archived_hash = digest(
                {n: hashlib.sha256(z.read("src/kitchenrush/" + n)).hexdigest() for n in names}
            )
            assert archived_hash == manifest["protocol_hash"], "missing/mismatched source archive"
    ledger = Ledger(ROOT / "runs/v2/spend.sqlite")
    with ledger.connect() as con:
        ledger_rows = {
            row[0]: row[1:]
            for row in con.execute(
                "SELECT id,episode,stage,reserved,actual,status FROM requests"
            )
        }
    expected_ids = {job["id"] for job in manifest["jobs"]}
    assert len(expected_ids) == len(manifest["jobs"]), "duplicate manifest episode"
    assert {
        p.stem for p in (ROOT / "runs/v2" / f"{stage}-{generation}").glob("*.json")
    } == expected_ids, "unexpected or missing episode artifacts"
    episodes = []
    usage_requests = set()
    for job in manifest["jobs"]:
        ep = json.loads(
            (ROOT / "runs/v2" / f"{stage}-{generation}" / (job["id"] + ".json")).read_text()
        )
        assert ep["status"] == "complete", (ep["id"], ep["status"])
        assert ep["protocol_hash"] == manifest["protocol_hash"]
        assert ep["model"] == manifest["models"][job["model"]]
        assert ep["dollar_cap"] == job["dollar_cap"] and ep["token_cap"] == job["token_cap"]
        assert ep["spec"] == serialize_spec(
            generate(job["seed"], "development" if stage != "pilot" else "pilot", job["size"])
        )
        assert digest(ep["spec"]) == ep["spec_hash"]
        assert ep["reason"] != "spending_gate", "stage/global spend prevented completion"
        assert ep["cost_usd"] <= ep["dollar_cap"] + 1e-9
        assert len(ep["actions"]) == len(ep["requests"]), "unexecuted request in scored episode"
        assert math.isclose(
            sum(r["cost_usd"] for r in ep["requests"]), ledger.used(ep["id"]), abs_tol=2e-7
        )
        assert ep["input_tokens"] == sum(r["input_tokens"] for r in ep["requests"])
        assert ep["output_tokens"] == sum(r["output_tokens"] for r in ep["requests"])
        if ep["token_cap"] is not None:
            assert ep["output_tokens"] <= ep["token_cap"]
        engine = Engine(deserialize_spec(ep["spec"]))
        client = Client(Model(**ep["model"]))
        spent_units = 0
        generated = 0
        for index, (record, action) in enumerate(zip(ep["requests"], ep["actions"], strict=True)):
            assert record["id"] not in usage_requests
            usage_requests.add(record["id"])
            state = public_state(engine)
            # Budget is the only variable state item outside the replayed public kitchen.
            resource = record["canonical"]["state"]["resource"]
            dollars_left = ep["dollar_cap"] - spent_units / SCALE
            tokens_left = None if ep["token_cap"] is None else ep["token_cap"] - generated
            assert resource == {
                "kind": "USD" if tokens_left is None else "generation_tokens",
                "allowance": ep["dollar_cap"] if tokens_left is None else ep["token_cap"],
                "remaining": dollars_left if tokens_left is None else tokens_left,
                "decisions_remaining": MAX_DECISIONS - index,
            }
            expected, options = decision(state, resource)
            assert expected == record["canonical"], "hidden or altered model information"
            prepared = client.prepare(expected, dollars_left, tokens_left)
            assert prepared is not None
            normalized = json.loads(json.dumps(prepared[0]))
            observed = json.loads(json.dumps(record["payload"]))
            if "messages" in normalized:
                for payload in (normalized, observed):
                    payload["messages"][1]["content"] = json.loads(
                        payload["messages"][1]["content"]
                    )
            assert normalized == observed
            assert math.isclose(prepared[1], record["reserved_usd"], abs_tol=1e-12)
            if ep["model"]["provider"] == "TypeSafe":
                assert record["payload"] == {"model": ep["model"]["model"], **expected}
            else:
                assert json.loads(record["payload"]["messages"][1]["content"]) == expected
            ni, no, cost = client.usage(record["response"], record["payload"])
            assert ledger_rows[record["id"]] == (
                ep["id"], stage, units(record["reserved_usd"]), units(cost), "settled"
            ), "request-level ledger mismatch"
            assert ni <= client.bound(record["payload"])[0]
            if ep["model"]["provider"] != "TypeSafe":
                assert no <= record["payload"]["max_tokens"]
            assert (ni, no) == (record["input_tokens"], record["output_tokens"])
            assert math.isclose(cost, record["cost_usd"], abs_tol=1e-12)
            client.validate_identity(record["response"])
            spent_units += units(cost)
            generated += no
            choice = client.select(record["response"])
            selected = options.get(choice) if isinstance(choice, str) else None
            assert action == (
                asdict(selected)
                if selected
                else {"name": "invalid_model_selection", "arguments": {}, "id": None}
            )
            engine.step([ToolCall(**action)])
            assert record["post_state_hash"] == digest(public_state(engine))
        if ep["reason"] == "game_terminal":
            assert engine.terminated
        elif ep["reason"] == "decision_limit":
            assert not engine.terminated and len(ep["actions"]) == MAX_DECISIONS
        elif ep["reason"] == "token_budget_exhausted":
            assert not engine.terminated and ep["token_cap"] == generated
        elif ep["reason"] == "cannot_reserve_next_request":
            assert not engine.terminated and len(ep["actions"]) < MAX_DECISIONS
            tokens_left = None if ep["token_cap"] is None else ep["token_cap"] - generated
            dollars_left = ep["dollar_cap"] - spent_units / SCALE
            canonical, _ = decision(public_state(engine), {
                "kind": "USD" if tokens_left is None else "generation_tokens",
                "allowance": ep["dollar_cap"] if tokens_left is None else ep["token_cap"],
                "remaining": dollars_left if tokens_left is None else tokens_left,
                "decisions_remaining": MAX_DECISIONS - len(ep["actions"]),
            })
            assert client.prepare(canonical, dollars_left, tokens_left) is None
        else:
            raise AssertionError(f"unknown scored termination: {ep['reason']}")
        verify_replay(ep)
        episodes.append(ep)
    # Each model in the main economic pilot must have the same task/budget/trial grid.
    grids = {}
    for model in manifest["models"]:
        grids[model] = Counter(
            (e["spec"]["seed"], e["dollar_cap"])
            for e in episodes
            if e["model"]["key"] == model and e["token_cap"] is None
        )
    assert all(g == next(iter(grids.values())) for g in grids.values())
    token_models = {e["model"]["key"] for e in episodes if e["token_cap"] is not None}
    token_grids = [
        Counter((e["spec"]["seed"], e["token_cap"]) for e in episodes
                if e["model"]["key"] == model and e["token_cap"] is not None)
        for model in token_models
    ]
    assert not token_grids or all(g == token_grids[0] for g in token_grids)
    assert {
        rid for rid, row in ledger_rows.items() if row[0] in expected_ids
    } == usage_requests, "unaccounted request for scored episode"
    snapshot = ledger.snapshot()
    assert snapshot["unresolved_requests"] == 0 and snapshot["overruns"] == 0
    result = {
        "status": "passed",
        "stage": stage,
        "episodes": len(episodes),
        "requests_verified": len(usage_requests),
        "protocol_hash": manifest["protocol_hash"],
        "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "ledger": snapshot,
        "summary": summarize(episodes),
    }
    write_json(ROOT / f"data/v2/{stage}-gate.json", result)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["canary", "smoke", "pilot"])
    p.add_argument("--generation", default=RUN_GENERATION)
    args = p.parse_args()
    audit(args.stage, args.generation)
