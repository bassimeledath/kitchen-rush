"""Portable offline fixtures; no private run directories or live provider calls."""

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def phone_checkout(tmp_path_factory):
    """Current code plus hash-checked prior evidence from the public tracked export."""
    target = tmp_path_factory.mktemp("phone-evidence-checkout")
    sources = list((ROOT / "src").rglob("*.py")) + [
        ROOT / "scripts" / name
        for name in (
            "probe_phonellm_sandbox.py",
            "phonellm_episode_diagnostic.py",
            "phonellm_durable_recovery.py",
            "phonellm_cpu_transport_check.py",
        )
    ]
    for source in sources:
        dest = target / source.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    prefix = "runs/v2/phonellm-episodes-20260917/execution-attempt2/"
    archive_path = ROOT / "data/v2/release-r2/evidence-diagnostics-20260917.zip"
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("EVIDENCE.json"))
        names = [name for name in archive.namelist() if name.startswith(prefix)]
        assert names, "tracked export is missing prior PhoneLLM diagnostic evidence"
        for name in names:
            content = archive.read(name)
            assert hashlib.sha256(content).hexdigest() == manifest["sha256"][name]
            assert not Path(name).is_absolute() and ".." not in Path(name).parts
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return target


@pytest.fixture(scope="session")
def modal_sdk_python():
    """Optional pinned-SDK integration tests; imports only, never authenticates."""
    candidates = [Path.home() / ".local/share/uv/tools/modal/bin/python", Path(sys.executable)]
    code = (
        "import json,sys,modal; print(json.dumps([list(sys.version_info[:2]),modal.__version__]))"
    )
    for python in dict.fromkeys(candidates):
        if not python.is_file():
            continue
        try:
            result = subprocess.run(
                [str(python), "-c", code], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and json.loads(result.stdout) == [[3, 10], "1.5.2"]:
                return python
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
    pytest.skip("Optional Python 3.10 / Modal 1.5.2 SDK runtime is unavailable")
