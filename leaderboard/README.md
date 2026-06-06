# leaderboard/

DRAFT submission infrastructure (the CLI + CI that consume it are pending — see
[../docs/LAUNCH_CHECKLIST.md](../docs/LAUNCH_CHECKLIST.md)).

- `submission.schema.json` — the JSON Schema every submission validates against.
- `manifest.json` — registry of accepted submissions (one line each).
- `submissions/` — one `<org>_<model>_<date>.json` per submission (added via PR).
- `examples/` — filled examples (`standard`, soon `custom`).

How to contribute a result: see [../docs/SUBMISSIONS.md](../docs/SUBMISSIONS.md).
