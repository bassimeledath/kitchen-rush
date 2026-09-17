# Checkpoint operations

Worktree: `/Users/bassime/Desktop/fullstack/kitchen-rush-v2`, branch `codex/remodel-v2`.
The original checkout retains its unrelated work. All commands below run from this worktree.
Install with `python -m pip install -e '.[dev,v2]'`; this local run uses the
original checkout's `.venv/bin/python`. Local credentials live only in ignored `.env` (0600).

## Offline checks

```sh
PYTHONPATH=src python -m pytest -q
python scripts/v2.py offline
ruff check src/kitchenrush/v2 scripts/v2.py scripts/audit_v2.py scripts/report_v2.py scripts/v2_roster.py tests/test_v2.py
git diff --check
```

The offline command makes no API calls. It verifies 240 generated episodes and emits a
scripted replay plus the recipe-reader/fixed-lookup diagnostic.

## Paid stages and audits

The authorized runner exposes only `canary`, `smoke`, and `pilot`; there is no main sweep.
It loads `.env`, freezes the manifest, reserves money, and persists every request. Completed
matching episodes can be skipped on restart. A noncomplete artifact or outstanding billing
requires inspection before dispatch; do not delete it to force a retry. Do not edit hashed
source during a run or mix results across hashes.

Current run generation is r3. Canary r1 was a development attempt; r2 passed after switching
generative responses to a strict schema. Smoke and pilot use r3, which also removes legacy
reward wording from action feedback. Earlier source ZIPs preserve the development versions.
The abandoned canary timeout retains its entire reservation, with actual billing unknown.

```sh
python scripts/audit_v2.py smoke
python scripts/audit_v2.py pilot
python scripts/report_v2.py pilot
```

Audit after all active requests finish. It checks the exact manifest, public information,
native payloads, provider identities, per-request ledger entries, budget enforcement,
termination, and every replay frame. It writes a gate only if all checks pass.
Audited data, charts and traces establish pilot correctness; they do not establish a rank
for configurations that have not run.

## Evidence locations

- `data/v2/`: public endpoint snapshots, roster, frozen manifests, source archives and audits.
- `runs/v2/`: local raw episodes and `spend.sqlite`; ignored by Git, retained for local audit.
- `docs/v2/`: protocol, short checkpoint review, detailed results and release specifications.
- `ui/v2/`: scripted and audited pilot replay prototype. Serve **only `ui`**, not the repository root:
  `python -m http.server 8768 --bind 127.0.0.1 --directory ui`, then open `/v2/`.

Before a public release, export a reviewed reproduction bundle and pin source, pricing and
configuration versions. Do not publish `.env`, authorization headers or unrelated local data.
Checkpoint 3 must implement its own authorized ledger ceilings and full-roster manifest;
the current checkpoint runner must not be repurposed by relabeling pilot results.
