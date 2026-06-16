# Submitting to the Kitchen Rush leaderboard

> **Draft.** The `submit`/`validate` CLI and CI auto-validation are not built yet (see
> [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md)); this documents the intended contract so it can be
> reviewed. Field definitions are authoritative in [`leaderboard/submission.schema.json`](../leaderboard/submission.schema.json).

Kitchen Rush accepts two kinds of submissions, mirroring τ²-bench's standard-vs-custom split:

- **`standard`** — an off-the-shelf model run with the **official harness** (the default system
  prompt, the official tool schemas, the pinned ruleset, the frozen seed split). This is the
  apples-to-apples board.
- **`custom`** — anything that changes the scaffold: a custom prompt, planner, router, retry/ensemble
  policy, or a non-LiteLLM adapter. Ranked on a separate board with a methodology note.

**Public-accessibility rule (like BFCL):** to appear on the public board the model must be reachable
by others — open-weights or a publicly available API (auth/billing/registration are fine). Private
endpoints can be submitted but are labelled unverified.

## Flow

1. **Run** the official split (all seeds, ≥4 trials, one configuration), per latency budget:
   ```bash
   kitchenrush bench --model <provider:model> --tier medium --latency-budget 5 \
       --seeds 50 --trials 4 --track rp --json > runs/<id>.json
   ```
   Repeat for the budgets/tiers the board ranks (e.g. `--latency-budget 1|5|20` × `medium|hard`).
2. **Prepare** a submission file (one JSON, schema-validated):
   ```bash
   kitchenrush submit prepare --run runs/<id> --meta meta.toml \
       --out leaderboard/submissions/<org>_<model>_<date>.json     # (CLI: coming)
   ```
3. **Validate** locally (the same checks CI runs):
   ```bash
   kitchenrush validate --submission leaderboard/submissions/<...>.json   # (CLI: coming)
   ```
4. **Open a PR** adding your `submissions/<...>.json` and a line in `leaderboard/manifest.json`,
   with a link to your trajectory artifacts (hosted externally; hashes recorded in the submission).
   CI re-validates and — because RP + seeded procgen are deterministic — **recomputes your aggregates
   from the trajectories** to confirm the numbers. A maintainer approves `standard` classification
   and the board rebuilds.

## What CI checks (intended)

- Version/hash fields present and matching the current generation (`ruleset`, `schema`,
  `generator`, `tokenizer`); wrong/old hashes are rejected.
- The frozen seed split was used in full (no `--seeds`/`--trials` filtering down), one row per cell,
  no duplicate seed/trial rows.
- `standard` runs used the default prompt / tool schemas / harness settings.
- RT rows disclose region/hardware and used `attempts=1`, `concurrency=1`.
- For RP rows: the proxy latency recomputes from the logged token counts; reasoning models must
  report `reasoning_tokens` (not null).
- Recomputed KR / Pass^k / rates match the submitted summary.

## Reporting

Every submission carries, **per (tier, B, track)**: `KR` (+ CI) and `Pass^1…Pass^k` as the
competence/reliability headline, with **latency (p50/p95), tokens/episode, and $/episode adjacent**
(never folded into KR). Cost uses the pinned `prices.yaml`; submitters may add measured cost.

See [`leaderboard/examples/`](../leaderboard/examples/) for a filled `standard` and `custom` example.
