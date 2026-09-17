"""A paid CPU transport test is an expense, never a model capability result."""

import hashlib
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_release_evidence import (  # noqa: E402
    billing_v2_payloads,
    cpu_transport_payloads,
    durable_recovery_payloads,
)
from report_release_v2 import aggregate, spending  # noqa: E402

RELATIVE = Path("runs/v2/phonellm-cpu-transport-20260917")
ID = "phonellm-cpu-transport-20260917-1"
ARCHIVE = ROOT / "data/v2/release-r2/evidence-diagnostics-20260917.zip"


def copy_archived_evidence(root, relative):
    """Fixtures come only from the versioned public archive, never ignored live runs."""
    with zipfile.ZipFile(ARCHIVE) as archive:
        for name in archive.namelist():
            if (
                name.startswith(relative.as_posix() + "/execution/")
                or name == (relative / "preflight-review.json").as_posix()
            ):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))


@pytest.fixture
def evidence(tmp_path):
    folder = tmp_path / RELATIVE
    folder.mkdir(parents=True)
    copy_archived_evidence(tmp_path, RELATIVE)
    # Private receipt must never enter export, even if present beside public evidence.
    (folder / "receipt.json").write_text('{"workspace":"PRIVATE_FIXTURE"}')
    return tmp_path


def mutate(path, change):
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record))


def test_cpu_export_uses_immutable_source_and_excludes_private_receipt(evidence):
    payloads, pending = cpu_transport_payloads(evidence)
    assert len(payloads) == 6 and pending == []
    assert any(name.endswith("source.zip") for name in payloads)
    assert any(name.endswith("artifacts.zip") for name in payloads)
    assert not any(name.endswith("receipt.json") for name in payloads)
    assert all(b"PRIVATE_FIXTURE" not in value for value in payloads.values())


def test_cpu_pending_is_excluded(evidence):
    mutate(
        evidence / RELATIVE / "execution/result.json",
        lambda r: r.update(termination_confirmed=False),
    )
    payloads, pending = cpu_transport_payloads(evidence)
    assert payloads == {} and pending == [(RELATIVE / "execution").as_posix()]


@pytest.mark.parametrize("tamper", ["flags", "source", "artifacts"])
def test_cpu_export_rejects_binding_tamper(evidence, tamper):
    out = evidence / RELATIVE / "execution"
    if tamper == "flags":
        mutate(out / "result.json", lambda r: r.update(rank_eligible=True))
    else:
        path = out / ("source.zip" if tamper == "source" else "artifacts.zip")
        path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="CPU transport"):
        cpu_transport_payloads(evidence)


def test_synthetic_fixture_cannot_be_aggregated_even_without_top_level_flags():
    with pytest.raises(ValueError, match="synthetic transport fixtures"):
        aggregate([{"requests": [{"response": {"synthetic_fixture": True}}]}])


def test_transport_reserve_counts_without_synthetic_quality(tmp_path):
    for relative in ("runs/v2", "runs/v2/release", "runs/v2/release-r2"):
        folder = tmp_path / relative
        folder.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(folder / "spend.sqlite") as con:
            con.execute(
                "CREATE TABLE requests (episode TEXT, reserved INT, actual INT, status TEXT)"
            )
            if relative.endswith("release-r2"):
                con.execute("INSERT INTO requests VALUES (?,250000000,NULL,'retained')", (ID,))
    sidecar = tmp_path / "runs/v2/release-r2/external" / (ID + ".json")
    sidecar.parent.mkdir()
    # Even an accidental quality field on a sidecar must not be displayed as model quality.
    sidecar.write_text(
        json.dumps(
            {
                "status": "billing_pending",
                "execution_status": "verified_transport_only",
                "quality": 100,
                "kind": "transport_selftest",
            }
        )
    )
    result = spending(tmp_path)
    assert result["total_accounted_usd"] == 0.25
    assert result["current_unresolved_or_unknown_usd"] == 0.25
    assert result["failed_attempts"] == []
    assert result["current_failed_or_incomplete_usd"] == 0
    assert result["current_completed_diagnostic_usd"] == 0.25
    row = result["diagnostic_attempts"][0]
    assert row["status"] == "verified_transport_only"
    assert row["failure_kind"] == "synthetic_transport_diagnostic"
    assert row["known_charges_usd"] == 0 and row["unknown_liability_usd"] == 0.25
    assert row["partial_quality_unranked"] is None and row["rank_eligible"] is False
    for execution_status, completed in (
        ("complete", True),
        ("verified_unranked_recovery", True),
        ("incomplete_trace", False),
    ):
        # Distinguish execution from billing state for actual hosting diagnostics too.
        with sqlite3.connect(tmp_path / "runs/v2/release-r2/spend.sqlite") as con:
            con.execute("UPDATE requests SET episode='hosting-fixture'")
        sidecar.unlink()
        sidecar = sidecar.with_name("hosting-fixture.json")
        sidecar.write_text(
            json.dumps(
                dict(
                    status="billing_pending",
                    execution_status=execution_status,
                    kind="unranked_hosting_diagnostic",
                    quality=100,
                    observed_gross_cost_usd=0.1,
                )
            )
        )
        observed = spending(tmp_path)
        assert observed["total_accounted_usd"] == 0.25  # Gross observation is not added again.
        assert observed["current_failed_or_incomplete_usd"] == 0
        assert observed["current_completed_diagnostic_usd"] == (0.25 if completed else 0)
        assert observed["current_interrupted_diagnostic_usd"] == (0 if completed else 0.25)
        assert observed["current_diagnostic_retained_liability_usd"] == 0.25
        diagnostic = observed["diagnostic_attempts"][0]
        assert diagnostic["status"] == execution_status
        assert diagnostic["partial_quality_unranked"] is None
        assert diagnostic["known_charges_usd"] == 0


@pytest.fixture
def durable_evidence(tmp_path):
    relative = Path("runs/v2/phonellm-durable-recovery-20260917")
    folder = tmp_path / relative
    folder.mkdir(parents=True)
    copy_archived_evidence(tmp_path, relative)
    planned = json.loads((folder / "execution/plan.json").read_text())
    with zipfile.ZipFile(ARCHIVE) as archive:
        for name in planned["prior_artifacts"]:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    return tmp_path, folder


def test_durable_terminal_source_prefixes_exported_without_live_dependency(durable_evidence):
    root, folder = durable_evidence
    payloads, pending = durable_recovery_payloads(root)
    assert len(payloads) == 11 and pending == []
    planned = json.loads((folder / "execution/plan.json").read_text())
    assert set(planned["prior_artifacts"]) <= payloads.keys()
    assert not (root / "scripts").exists()  # Each source.zip carries its own exact implementation.


def test_durable_live_result_excluded(durable_evidence):
    root, folder = durable_evidence
    mutate(folder / "execution/result.json", lambda r: r.update(termination_confirmed=False))
    payloads, pending = durable_recovery_payloads(root)
    assert payloads == {} and len(pending) == 1


@pytest.mark.parametrize("tamper", ["prior", "usage", "archive"])
def test_durable_rejects_lost_prior_cost_usage_or_changed_archive(durable_evidence, tamper):
    root, folder = durable_evidence
    if tamper == "prior":
        planned = json.loads((folder / "execution/plan.json").read_text())
        prior = root / next(iter(planned["prior_artifacts"]))
        prior.write_bytes(prior.read_bytes() + b" ")
    elif tamper == "usage":

        def remove_prior(result):
            next(iter(result["all_attempt_known_usage"].values()))["input_tokens"] = 0

        mutate(folder / "execution/result.json", remove_prior)
    else:
        path = folder / "execution/artifacts.zip"
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="durable recovery"):
        durable_recovery_payloads(root)


@pytest.fixture
def versioned_billing(tmp_path):
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    folder = tmp_path / "runs/v2/phonellm-probe-20260917"
    helpers = {}
    for name in (
        "reconcile_phonellm_probe.py",
        "reconcile_phonellm_episodes.py",
        "reconcile_phonellm_billing_v2.py",
    ):
        path = tmp_path / "scripts" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("# frozen fixture helper\n")
        helpers[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result_hash = write(
        folder / "execution/result.json",
        dict(termination_confirmed=True, local_after_terminate_unix=1),
    )
    old_hash = write(folder / "gross-billing-fetch.json", dict(status="fetch_started"))
    interruption_hash = write(
        folder / "billing-fetch-interruption.json",
        dict(terminal_process_observed=True, original_fetch_sha256=old_hash),
    )
    output = folder / "gross-billing-fetch-v2.json"
    write(
        output,
        dict(
            status="gross_usage_reported_requires_review",
            finished_at_utc="2026-09-17T10:00:00Z",
            result_sha256=result_hash,
            prior_interrupted_fetch_sha256=old_hash,
            interruption_sha256=interruption_hash,
            fetch_script_sha256=helpers.pop("reconcile_phonellm_billing_v2.py"),
            helper_sha256=helpers,
        ),
    )
    return tmp_path, output


def test_finished_billing_exports_helpers_and_interruption(versioned_billing):
    root, _ = versioned_billing
    payloads = billing_v2_payloads(root)
    assert len(payloads) == 6
    assert any(n.endswith("billing-fetch-interruption.json") for n in payloads)
    assert any(n.endswith("gross-billing-fetch.json") for n in payloads)


def test_started_billing_excluded(versioned_billing):
    root, output = versioned_billing
    mutate(output, lambda r: r.update(status="fetch_started"))
    assert billing_v2_payloads(root) == {}


def test_billing_rejects_changed_helper_and_interruption(versioned_billing):
    root, output = versioned_billing
    mutate(output, lambda r: r.update(interruption_sha256="0" * 64))
    with pytest.raises(ValueError, match="interruption binding"):
        billing_v2_payloads(root)
    (root / "scripts/reconcile_phonellm_probe.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="helper hash"):
        billing_v2_payloads(root)
