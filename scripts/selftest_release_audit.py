import copy
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from kitchenrush.v2.environment import generate
from kitchenrush.v2.evaluation import run_episode
from kitchenrush.v2.ledger import Ledger
from kitchenrush.v2.providers import Client, Model

real = Path.cwd()


def mod(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


a = mod("auditfixture", real / "scripts/audit_release_v2.py")


class Fake(Client):
    def evaluate(self, p):
        if self.model.provider == "TypeSafe":
            return dict(
                model=self.model.model,
                usage=dict(input_tokens=1000000, output_tokens=16),
                answers=dict(next_action=dict(type="choice", choice="wait(1)")),
            )
        return dict(
            model=self.model.model,
            provider=self.model.provider,
            usage=dict(prompt_tokens=20, completion_tokens=16, cost=0.09),
            choices=[dict(finish_reason="stop", message=dict(content='{"action":"wait(1)"}'))],
        )


with TemporaryDirectory() as td:
    r = Path(td)
    for p in list((real / "src").rglob("*.py")) + [
        real / "scripts/release_v2.py",
        real / "scripts/audit_release_v2.py",
        real / "scripts/audit_roster_v2.py",
        real / "scripts/export_release_evidence.py",
        real / "data/v2/release-r2-roster.json",
        real / "data/v2/roster.json",
    ]:
        q = r / p.relative_to(real)
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, q)
    d = r / "data/v2/release-r2"
    d.mkdir()
    old = Ledger(r / "runs/v2/spend.sqlite")
    old.reserve("old", "old", "canary", 1)
    old.settle("old", 1)
    previous = Ledger(r / "runs/v2/release/spend.sqlite", cap=200, opening_balance=1)
    previous.reserve("prior", "prior", "corrected_validation", 2)
    previous.settle("prior", 2)
    previous.reserve("failed", "prior", "corrected_validation", 0.25)
    previous.uncertain("failed")
    previous.retain_failed_liability("failed")
    ledger = Ledger(r / "runs/v2/release-r2/spend.sqlite", cap=200, opening_balance=3.25)
    runner = mod("runnerfixture", r / "scripts/release_v2.py")
    runner.ROOT = r
    m = runner.plan("revised_canary")
    b = m["batch"]
    mp = d / f"{b}-manifest.json"
    mp.write_text(json.dumps(m))
    (d / "campaign.json").write_text(json.dumps({k: m[k] for k in runner.CAMPAIGN_FIELDS}))
    assert m["opening_balance"] == 3.25  # 1 + 2 + retained .25; never add opening twice.
    runner.archive_source(m)
    for j in m["jobs"]:
        p = r / f"runs/v2/release-r2/{b}/{j['id']}.json"
        e = run_episode(
            generate(j["seed"], j["split"], j["size"]),
            Fake(Model(**m["models"][j["model"]])),
            episode_id=j["id"],
            stage=m["stage"],
            dollar_cap=j["dollar_cap"],
            ledger=ledger,
            output=p,
        )
        e["replay_verified"] = False
        p.write_text(json.dumps(e))
    x = a.audit(b, root=r, write_gate=False)
    print(
        "full frozen-plan synthetic audit",
        x["status"],
        x["episodes"],
        x["requests_verified"],
        "roster",
        x["roster_coverage"]["total_configurations"],
    )
    p = r / f"runs/v2/release-r2/{b}/{m['jobs'][0]['id']}.json"
    original = json.loads(p.read_text())
    changes = {
        "boundary admitted": lambda e: e["requests"][-1].update(admitted=True),
        "boundary parsed": lambda e: e["requests"][-1].update(selection="wait(1)"),
        "hidden input": lambda e: e["requests"][0]["canonical"]["state"].update(secret="x"),
        "usage total": lambda e: e.update(output_tokens=0),
        "action changed": lambda e: e["actions"][0]["arguments"].update(seconds=2),
        "frame changed": lambda e: e["frames"][0].update(clock_gs=99),
        "wrong stop": lambda e: e.update(reason="token_budget_exhausted"),
        "missing request": lambda e: e["requests"].pop(),
    }
    for label, f in changes.items():
        e = copy.deepcopy(original)
        f(e)
        p.write_text(json.dumps(e))
        try:
            a.audit(b, root=r, write_gate=False)
        except (ValueError, KeyError, IndexError):
            print("reject", label)
        else:
            raise AssertionError(label)
        finally:
            p.write_text(json.dumps(original))
    broken = copy.deepcopy(m)
    removed = broken["jobs"].pop()["model"]
    broken["models"].pop(removed)
    mp.write_text(json.dumps(broken))
    try:
        a.audit(b, root=r, write_gate=False)
    except ValueError as ex:
        print("reject internally balanced model subset:", ex)
    else:
        raise AssertionError("subset passed")
    mp.write_text(json.dumps(m))
    a.audit(b, root=r)
    gate = d / f"{b}-gate.json"
    assert gate.exists()
    e = copy.deepcopy(original)
    e["quality"] = 100
    p.write_text(json.dumps(e))
    try:
        a.audit(b, root=r)
    except ValueError:
        pass
    assert not gate.exists()
    print("stale gate invalidation passed")

    p.write_text(json.dumps(original))
    assert a.audit(b, root=r)["status"] == "passed"
    print("re-audit restored evidence after stale gate removal passed")
    runner_path = r / "scripts/release_v2.py"
    old_runner = runner_path.read_bytes()
    runner_path.write_bytes(old_runner + b"\n# Future orchestration-only edit.\n")
    assert a.audit(b, root=r, write_gate=False)["status"] == "passed"
    print("archived plan survives current runner-only edit")
    runner_path.write_bytes(old_runner)
    source_path = r / m["orchestration"]["archive"]
    original_source = source_path.read_bytes()
    with zipfile.ZipFile(source_path) as z:
        members = {name: z.read(name) for name in z.namelist()}
    members["scripts/release_v2.py"] += b"\n# Tampered archived runner.\n"
    with zipfile.ZipFile(source_path, "w") as z:
        for name, content in members.items():
            z.writestr(name, content)
    try:
        a.audit(b, root=r, write_gate=False)
    except ValueError:
        print("reject archived runner tamper")
    else:
        raise AssertionError("archived runner tamper passed")
    source_path.write_bytes(original_source)
    core_path = r / "src/kitchenrush/v2/environment.py"
    original_core = core_path.read_bytes()
    core_path.write_bytes(original_core + b"\n# Tampered core.\n")
    try:
        a.audit(b, root=r, write_gate=False)
    except ValueError:
        print("reject current core tamper")
    else:
        raise AssertionError("core tamper passed")
    core_path.write_bytes(original_core)
    previous.reserve("live", "future", "corrected_validation", 0.1)
    try:
        a.audit(b, root=r, write_gate=False)
    except ValueError:
        print("reject live prior ledger")
    else:
        raise AssertionError("live ledger passed")
    previous.settle("live", 0)
    assert a.audit(b, root=r)["status"] == "passed"
    for batch in (
        "revised_canary",
        "roster_canary",
        "main_1",
        "tokens_1",
        "repeat_1",
        "confirmation",
        "main_2",
    ):
        manifest = runner.plan(batch)
        a.verify_grid(manifest)
        counts = {
            "revised_canary": 6,
            "roster_canary": 29,
            "main_1": 261,
            "tokens_1": 252,
            "repeat_1": 87,
            "confirmation": 87,
            "main_2": 261,
        }
        assert len(manifest["jobs"]) == counts[batch]
        if batch == "repeat_1":
            assert all(j["trial"] == 1 and j["seed"] == 61000 + j["size"] for j in manifest["jobs"])
        print("grid", batch, len(manifest["jobs"]))
    import sys

    sys.modules["audit_release_v2"] = a
    ex = mod("exportfixture", r / "scripts/export_release_evidence.py")
    ex.ROOT = r
    ex.audit = lambda batch, **kwargs: a.audit(batch, root=r, **kwargs)
    out = r / "evidence.zip"
    ex.export([b], out)
    with zipfile.ZipFile(out) as z:
        assert m["orchestration"]["archive"] in z.namelist()
        assert all(
            name in z.namelist()
            for name in (
                "runs/v2/spend.sqlite",
                "runs/v2/release/spend.sqlite",
                "runs/v2/release-r2/spend.sqlite",
            )
        )
    print("self-contained r2 source and three-ledger evidence export passed")

    dispatch = Ledger(r / "dispatch.sqlite", cap=200, opening_balance=180)
    dispatch_model = Model(
        key="dispatch",
        model="synthetic",
        provider="synthetic",
        input_price=1,
        output_price=0,
        prompt_limit=12,
        max_output=16384,
    )
    dispatch_manifest = dict(
        stage="corrected_validation", models={"dispatch": dispatch_model.__dict__}
    )
    dispatch_jobs = [dict(model="dispatch", token_cap=None) for _ in range(3)]
    assert runner.dispatch_width(dispatch_jobs, dispatch_manifest, dispatch, 3) == 1
    dispatch.reserve("active", "active", "corrected_validation", 12)
    assert runner.dispatch_width(dispatch_jobs, dispatch_manifest, dispatch, 3) == 0
    dispatch.settle("active", 1)
    assert runner.dispatch_width(dispatch_jobs, dispatch_manifest, dispatch, 3) == 1
    print("adaptive concurrency narrows and refuses unaffordable initial dispatch")

    # A separate synthetic campaign exercises manual recovery without touching paid evidence.
    with TemporaryDirectory() as recovery_directory:
        rr = Path(recovery_directory)
        shutil.copytree(r, rr, dirs_exist_ok=True)
        recovery_runner = mod("recoveryrunner", rr / "scripts/release_v2.py")
        recovery_ledger = Ledger(
            rr / "runs/v2/release-r2/spend.sqlite", cap=200, opening_balance=3.25
        )
        job = m["jobs"][0]
        original_path = rr / f"runs/v2/release-r2/{b}/{job['id']}.json"
        original_path.unlink()
        with recovery_ledger.connect() as con:
            con.execute("DELETE FROM requests WHERE episode=?", (job["id"],))

        class FakeFailure(Fake):
            calls = 0

            def evaluate(self, payload):
                from kitchenrush.v2.providers import ProviderError

                self.calls += 1
                if self.calls == 2:
                    raise ProviderError("HTTP 429; no automatic retry")
                return super().evaluate(payload)

        failed = run_episode(
            generate(job["seed"], job["split"], job["size"]),
            FakeFailure(Model(**m["models"][job["model"]])),
            episode_id=job["id"],
            stage=m["stage"],
            dollar_cap=job["dollar_cap"],
            ledger=recovery_ledger,
            output=original_path,
        )
        assert failed["status"] == "quarantined"
        failed_bytes = original_path.read_bytes()
        recovery_runner.authorize_recovery(
            b, job["id"], "Synthetic HTTP429 independently verified."
        )
        assert original_path.read_bytes() == failed_bytes
        disposition, replacement = a.recovery_paths(rr, b, job["id"])
        assert disposition.exists() and not replacement.exists()
        run_episode(
            generate(job["seed"], job["split"], job["size"]),
            Fake(Model(**m["models"][job["model"]])),
            episode_id=job["id"] + "--attempt2",
            stage=m["stage"],
            dollar_cap=job["dollar_cap"],
            ledger=recovery_ledger,
            output=replacement,
        )
        recovered = a.audit(b, root=rr)
        assert recovered["episodes"] == 6 and recovered["attempt_count"] == 7
        assert recovered["failed_attempt_count"] == 1
        assert recovered["failed_attempt_accounted_usd"] == failed["cost_usd"]
        assert recovered["evaluated_artifacts"][job["id"]] == replacement.relative_to(rr).as_posix()
        assert recovered["all_attempts_accounted_usd"] > recovered["batch_cost_usd"]
        for retry_id in (job["id"], job["id"] + "--attempt2", m["jobs"][1]["id"]):
            try:
                recovery_runner.authorize_recovery(b, retry_id, "Must reject")
            except ValueError:
                pass
            else:
                raise AssertionError("recovery allowed repeated/successful attempt")
        changed = json.loads(failed_bytes)
        changed["requests"][0]["canonical"]["state"]["secret"] = "tampered"
        original_path.write_text(json.dumps(changed))
        try:
            a.audit(b, root=rr, write_gate=False)
        except ValueError:
            pass
        else:
            raise AssertionError("recovered original tamper passed")
        original_path.write_bytes(failed_bytes)
        print("manual429 recovery and prohibited-retry tests passed")

        ex.ROOT = rr
        ex.audit = lambda batch, **kwargs: a.audit(batch, root=rr, **kwargs)
        recovered_zip = rr / "recovered-evidence.zip"
        ex.export([b], recovered_zip)
        with zipfile.ZipFile(recovered_zip) as bundle:
            assert replacement.relative_to(rr).as_posix() in bundle.namelist()
            assert disposition.relative_to(rr).as_posix() in bundle.namelist()
        print("recovery export includes original, replacement, disposition, and all liabilities")

    with TemporaryDirectory() as peer_directory:
        pr = Path(peer_directory)
        shutil.copytree(r, pr, dirs_exist_ok=True)
        peer_ledger = Ledger(pr / "runs/v2/release-r2/spend.sqlite", cap=200, opening_balance=3.25)
        failed_job, paused_job = m["jobs"][0], m["jobs"][2]
        for chosen in (failed_job, paused_job):
            (pr / f"runs/v2/release-r2/{b}/{chosen['id']}.json").unlink()
            with peer_ledger.connect() as con:
                con.execute("DELETE FROM requests WHERE episode=?", (chosen["id"],))

        class ConcurrentPeer(Fake):
            def evaluate(self, payload):
                run_episode(
                    generate(failed_job["seed"], failed_job["split"], failed_job["size"]),
                    FakeFailure(Model(**m["models"][failed_job["model"]])),
                    episode_id=failed_job["id"],
                    stage=m["stage"],
                    dollar_cap=failed_job["dollar_cap"],
                    ledger=peer_ledger,
                    output=pr / f"runs/v2/release-r2/{b}/{failed_job['id']}.json",
                )
                return super().evaluate(payload)

        paused_path = pr / f"runs/v2/release-r2/{b}/{paused_job['id']}.json"
        pause = run_episode(
            generate(paused_job["seed"], paused_job["split"], paused_job["size"]),
            ConcurrentPeer(Model(**m["models"][paused_job["model"]])),
            episode_id=paused_job["id"],
            stage=m["stage"],
            dollar_cap=paused_job["dollar_cap"],
            ledger=peer_ledger,
            output=paused_path,
        )
        assert pause["status"] == "incomplete" and pause["reason"] == "operational_spending_gate"
        assert len(pause["requests"]) == 1
        preserved = {
            path: path.read_bytes()
            for path in (
                paused_path,
                pr / f"runs/v2/release-r2/{b}/{failed_job['id']}.json",
                pr / f"data/v2/release-r2/{b}-manifest.json",
                pr / "data/v2/release-r2/campaign.json",
                pr / m["orchestration"]["archive"],
            )
        }
        driver = pr / "scripts/release_v2.py"
        driver.write_bytes(driver.read_bytes() + b"\n# Explicit operational driver amendment.\n")
        amended = mod("amendedfixture", driver)
        assert amended.plan(b) == m
        amended.authorize_peer_amendment(
            b, "Synthetic simultaneous provider failure and peer pause."
        )
        amended.authorize_recovery(b, failed_job["id"], "Synthetic provider failure verified.")
        amended.authorize_recovery(
            b, paused_job["id"], "Synthetic peer pause verified.", failed_job["id"]
        )
        for chosen in (failed_job, paused_job):
            _, dest = a.recovery_paths(pr, b, chosen["id"])
            run_episode(
                generate(chosen["seed"], chosen["split"], chosen["size"]),
                Fake(Model(**m["models"][chosen["model"]])),
                episode_id=chosen["id"] + "--attempt2",
                stage=m["stage"],
                dollar_cap=chosen["dollar_cap"],
                ledger=peer_ledger,
                output=dest,
            )
        gate = a.audit(b, root=pr)
        assert gate["episodes"] == 6 and gate["attempt_count"] == 8
        assert {f["failure_kind"] for f in gate["failed_attempts"]} == {
            "provider_failure",
            "peer_accounting_pause",
        }
        assert all(path.read_bytes() == content for path, content in preserved.items())
        assert gate["operational_amendment"]["source_archive"] != m["orchestration"]["archive"]
        print(
            "concurrent429 -> peer pause -> immutable amendment -> both replacements audit passed"
        )
        amendment_path = pr / "data/v2/release-r2/peer-pause-amendment.json"
        prior_amendment = amendment_path.read_bytes()
        bad = json.loads(prior_amendment)
        bad["archive_sha256"] = "0" * 64
        amendment_path.write_text(json.dumps(bad))
        try:
            a.audit(b, root=pr, write_gate=False)
        except ValueError:
            pass
        else:
            raise AssertionError("amendment tamper passed")
        amendment_path.write_bytes(prior_amendment)
        ex.ROOT = pr
        ex.audit = lambda batch, **kw: a.audit(batch, root=pr, **kw)
        ex.export([b], pr / "amended-export.zip")
        with zipfile.ZipFile(pr / "amended-export.zip") as z:
            assert m["orchestration"]["archive"] in z.namelist()
            assert gate["operational_amendment"]["source_archive"] in z.namelist()
            assert amendment_path.relative_to(pr).as_posix() in z.namelist()
        # Entire affected token cell excluded; no dropping its failed seed from a mean.
        ep = json.loads((pr / gate["evaluated_artifacts"][paused_job["id"]]).read_text())
        ep.update(token_cap=512, quality=100, replay_verified=True, reason="token_budget_exhausted")
        summary = a.release_summary([ep], [dict(model=ep["model"]["key"], token_cap=512)])
        assert summary["rows"][0]["replacement_affected"] and not summary["rows"][0]["b80_eligible"]
        assert summary["B80"][ep["model"]["key"]]["mean"] is None
        print("amendment tamper/export and whole-cell B80 exclusion passed")
