"""Offline supplemental evidence/privacy regressions; no provider calls."""

import copy
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import export_release_evidence as export  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "export_phone_fixture", ROOT / "scripts/phonellm_episode_diagnostic.py"
)
phone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_funding_interruption_preserves_unranked_traces_without_account_data(tmp_path):
    manifest = tmp_path / "data/v2/release-r2/roster_canary-manifest.json"
    write(manifest, {"jobs": []})
    observations = []
    for model in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-4-6-think"):
        relative = (
            "runs/v2/release-r2/roster_canary/"
            f"roster_canary-{model}-60100-3-0-d0.4-tNone.json"
        )
        path = tmp_path / relative
        write(path, {"status": "incomplete", "requests": []})
        observations.append(dict(
            path=relative, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            rank_eligible=False,
        ))
    write(tmp_path / "data/v2/release-r2/roster_canary-interruption.json", dict(
        kind="funding_interruption", http_status=402,
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        observations=observations,
    ))
    write(tmp_path / "runs/v2/release-r2/funding-check-20260917.json",
          {"balance": "PRIVATE_ACCOUNT_CHECK"})
    payloads = export.funding_interruption_payloads(tmp_path)
    assert len(payloads) == 5
    assert all(b"PRIVATE_ACCOUNT_CHECK" not in raw for raw in payloads.values())
    path.write_text('{"status":"complete"}')
    with pytest.raises(ValueError, match="trace binding"):
        export.funding_interruption_payloads(tmp_path)


def fixture(root, attempt="execution", terminal=True):
    folder = root / "runs/v2/phonellm-episodes-20260917"
    out = folder / attempt
    out.mkdir(parents=True)
    # Simulate the previous launcher's immutable archived revision.
    source = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(phone.bundle())) as old, zipfile.ZipFile(source, "w") as new:
        for name in old.namelist():
            content = old.read(name)
            if name == "scripts/phonellm_episode_diagnostic.py":
                content += b"\n# previous archived diagnostic revision\n"
                script = content
            new.writestr(name, content)
    plan = phone.plan()
    plan["source_archive_sha256"] = hashlib.sha256(source.getvalue()).hexdigest()
    (out / "source.zip").write_bytes(source.getvalue())
    write(out / "plan.json", plan)
    suffix = "1" if attempt == "execution" else "2"
    result = dict(
        plan_sha256=export.sha_json(plan),
        reservation_id="phonellm-full-episodes-20260917-" + suffix,
        sandbox_id="sb-public",
        events=[],
        episodes={},
    )
    if terminal:
        result.update(local_after_terminate_unix=1, termination_confirmed=True)
    write(out / "result.json", result)
    review = "preflight-review.json" if suffix == "1" else "retry-preflight-review.json"
    write(
        folder / review,
        dict(
            plan_sha256=export.sha_json(plan),
            script_sha256=hashlib.sha256(script).hexdigest(),
            source_archive_sha256=plan["source_archive_sha256"],
        ),
    )
    write(
        folder / "receipt.json",
        {"workspace": "DO_NOT_EXPORT_PRIVATE_ACCOUNT", "api_key": "PRIVATE_SECRET"},
    )
    write(folder / "retry-receipt.json", {"workspace": "DO_NOT_EXPORT_PRIVATE_ACCOUNT"})
    write(folder / "account-funding-check.json", {"balance": 200})
    sidecar = root / (
        "runs/v2/release-r2/external/phonellm-full-episodes-20260917-" + suffix + ".json"
    )
    write(
        sidecar, dict(id=result["reservation_id"], actual_cost_usd=None, retained_liability_usd=5)
    )
    return out, plan, result


def test_archive_own_plan_and_private_exclusions(tmp_path):
    fixture(tmp_path)
    fixture(tmp_path, "execution-attempt2")
    payloads, pending = export.diagnostic_payloads(tmp_path)
    assert not pending
    assert sum(name.endswith("source.zip") for name in payloads) == 2
    assert sum("/external/" in name for name in payloads) == 2
    assert not any("receipt" in name or "funding-check" in name for name in payloads)
    assert not any(b"DO_NOT_EXPORT_PRIVATE_ACCOUNT" in content for content in payloads.values())


def test_live_attempt_excluded_and_changed_archive_rejected(tmp_path):
    out, plan, result = fixture(tmp_path, terminal=False)
    payloads, pending = export.diagnostic_payloads(tmp_path)
    assert not payloads and pending == [out.relative_to(tmp_path).as_posix()]
    result.update(local_after_terminate_unix=1, termination_confirmed=True)
    write(out / "result.json", result)
    (out / "source.zip").write_bytes((out / "source.zip").read_bytes() + b"changed")
    with pytest.raises(ValueError, match="source or plan binding"):
        export.diagnostic_payloads(tmp_path)


def test_private_field_rejected_in_allowed_public_file(tmp_path):
    out, _, _ = fixture(tmp_path)
    write(out.parent / "accounting-disposition.json", {"workspace": "private"})
    with pytest.raises(ValueError, match="private account field"):
        export.diagnostic_payloads(tmp_path)


def test_local_rejection_requires_exact_diagnosis_binding(tmp_path):
    out, _, result = fixture(tmp_path)
    result.pop("sandbox_id")
    result.pop("termination_confirmed")
    result["error_type"] = "InvalidError"
    write(out / "result.json", result)
    payloads, pending = export.diagnostic_payloads(tmp_path)
    assert not payloads and pending
    diagnosis = dict(
        validation_before_sandbox_creation_rpc=True,
        original_artifact_sha256={
            name: hashlib.sha256((out / name).read_bytes()).hexdigest()
            for name in ("result.json", "plan.json", "source.zip")
        },
    )
    write(out.parent / "failure-diagnosis.json", diagnosis)
    payloads, pending = export.diagnostic_payloads(tmp_path)
    assert payloads and not pending
    changed = copy.deepcopy(result)
    changed["error_type"] = "DifferentError"
    write(out / "result.json", changed)
    payloads, pending = export.diagnostic_payloads(tmp_path)
    assert not payloads and pending


def test_credential_scan_reaches_compressed_sources():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("src/example.py", b"credential = 'PRIVATE_FIXTURE_TOKEN'")
    with pytest.raises(ValueError, match="credential scan rejected"):
        export.reject_credentials({"source.zip": output.getvalue()}, [b"PRIVATE_FIXTURE_TOKEN"])


def test_billing_export_waits_for_terminal_and_binds_both_helpers(tmp_path):
    out, _, _ = fixture(tmp_path, "execution-attempt2")
    (tmp_path / "scripts").mkdir()
    helpers = {
        "fetch_script_sha256": "reconcile_phonellm_episodes.py",
        "shared_helper_sha256": "reconcile_phonellm_probe.py",
    }
    billing = {"status": "fetch_started"}
    path = out.parent / "gross-billing-attempt2.json"
    for field, name in helpers.items():
        helper = tmp_path / "scripts" / name
        helper.write_text("# fixture billing helper\n")
        billing[field] = hashlib.sha256(helper.read_bytes()).hexdigest()
    billing["result_sha256"] = hashlib.sha256((out / "result.json").read_bytes()).hexdigest()
    write(path, billing)
    payloads, _ = export.diagnostic_payloads(tmp_path)
    assert path.relative_to(tmp_path).as_posix() not in payloads
    billing.update(status="gross_rows_available", finished_at_utc="2026-09-17T09:03:01Z")
    write(path, billing)
    payloads, _ = export.diagnostic_payloads(tmp_path)
    assert path.relative_to(tmp_path).as_posix() in payloads
    assert all("scripts/" + name in payloads for name in helpers.values())
    (tmp_path / "scripts/reconcile_phonellm_probe.py").write_text("# tampered helper\n")
    with pytest.raises(ValueError, match="billing helper binding"):
        export.diagnostic_payloads(tmp_path)
