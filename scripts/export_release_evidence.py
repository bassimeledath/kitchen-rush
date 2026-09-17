#!/usr/bin/env python3
"""Package audited synthetic traces and accounting for offline independent verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from audit_release_v2 import audit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def export(batches, output):
    gates = {batch: audit(batch, write_gate=False) for batch in batches}
    paths = {
        ROOT / "data/v2/release-r2-roster.json",
        ROOT / "data/v2/roster.json",
        ROOT / "data/v2/release-r2/campaign.json",
    }
    for batch, gate in gates.items():
        paths.update((ROOT / "runs/v2/release-r2" / batch).rglob("*.json"))
        paths.update(
            ROOT / f"data/v2/release-r2/{batch}-{suffix}.json" for suffix in ("manifest", "gate")
        )
        paths.add(ROOT / gate["source_archive"])
        if gate.get("operational_amendment"):
            paths.add(ROOT / gate["operational_amendment"]["path"])
            paths.add(ROOT / gate["operational_amendment"]["source_archive"])
        paths.update((ROOT / f"data/v2/release-r2/{batch}-recoveries").glob("*.json"))
    payloads = {str(p.relative_to(ROOT)): p.read_bytes() for p in sorted(paths)}
    with tempfile.TemporaryDirectory() as directory:
        for name in (
            "runs/v2/spend.sqlite",
            "runs/v2/release/spend.sqlite",
            "runs/v2/release-r2/spend.sqlite",
        ):
            target = Path(directory) / Path(name).name
            # Use distinct targets and SQLite backup, not a possibly torn filesystem copy.
            target = target.with_name(name.replace("/", "-"))
            with sqlite3.connect(f"file:{ROOT / name}?mode=ro", uri=True) as source:
                with sqlite3.connect(target) as dest:
                    source.backup(dest)
            payloads[name] = target.read_bytes()
    # Credentials are read only to reject an accidental inclusion, never exported or logged.
    secrets = []
    for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            if not re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.IGNORECASE):
                continue
            value = value.strip().strip('"').strip("'")
            if len(value) >= 12:
                secrets.append(value.encode())
    if any(secret in content for secret in secrets for content in payloads.values()):
        raise ValueError("credential scan rejected evidence export")
    restore_sources = list(dict.fromkeys(g["source_archive"] for g in gates.values()))
    restore_sources.extend(
        path
        for path in dict.fromkeys(
            g["operational_amendment"]["source_archive"]
            for g in gates.values()
            if g.get("operational_amendment")
        )
        if path not in restore_sources
    )
    evidence = dict(
        batches=batches,
        protocol_hashes=sorted({g["protocol_hash"] for g in gates.values()}),
        restore="Extract this evidence archive into a clean directory, then extract each "
        "archive in restore_sources into that same directory in the listed order, allowing "
        "the operational amendment to replace driver files. Run python "
        "scripts/audit_release_v2.py --batch <batch>. No API call is needed.",
        restore_sources=restore_sources,
        credential_scan_passed=True,
        sha256={name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()},
    )
    payloads["EVIDENCE.json"] = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(payloads.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    with zipfile.ZipFile(output) as archive:
        for name, expected in evidence["sha256"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("export verification mismatch")
    return dict(
        path=str(output),
        bytes=output.stat().st_size,
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        batches=batches,
        credential_scan_passed=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.batch, args.output), indent=2))
