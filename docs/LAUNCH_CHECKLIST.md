# Kitchen Rush — launch readiness checklist

What's left between the current alpha and a public benchmark people take seriously. Synthesized
from two independent reviews (Opus + GPT-5.5-extra-high). The **engine/scoring core is strong**;
the gaps are the public *contract* (docs, versioning, validation, RP credibility, cost) and a real
model panel — not the game.

Legend: `[ ]` todo · `[~]` partial · `[x]` done.

---

## P0 — must do before any public result or PRs

**Truthful public contract**
- [~] `RULES.md` synced to code (done) — keep it authoritative.
- [~] README rewritten to be accurate + short (done) — no non-existent commands, correct status.
- [x] Drifted design-history docs (`SCORING.md`, `MOVEMENT.md`, `PROCEDURAL.md`, `INTERFACE.md`,
  `DESIGN.md`, plus `MIGRATION.md`/`MULTI_AGENT.md`/`ROADMAP.md`) **deleted** — RULES.md is the
  single spec, METHODOLOGY.md the justification layer; git history preserves the originals.
- [ ] Fix CLI overclaim: docs reference `run-suite`/`aggregate`/`submit`/`validate` that don't
  exist yet — either build them (P1) or stop documenting them.

**Reproducibility & versioning**
- [x] Implement version/hash stamping: `ruleset_hash()` (over constants+catalog+recipes+tiers) +
  `versions()` {package, ruleset, schema, generator, tokenizer}, stamped into report/replay/aggregate.
- [ ] Freeze official **seed manifests / splits** (train/dev/test + maintainer-only challenge),
  with a manifest hash and a legality/feasibility check.
- [x] **RP credibility:** pinned tokenizer (`cl100k` via tiktoken, char/4 fallback for the
  stdlib-only core), versioned `TOKENIZER_ID` stamped in outputs; RP now counts the tool schemas
  (`n_in`) + each tool call's name (`n_out`) + reasoning tokens. *(β-coefficients still need the
  calibration study; ranked vs experimental is decided: headline RP, RT diagnostic — pending lock.)*
- [ ] Fatten the trajectory log so RP can be **recomputed from it** (full model-facing prompt, tool
  schemas, raw tool calls, usage incl. reasoning tokens, latency samples). Current `steps` are too
  thin for audit/recompute — which is what makes "verify-in-CI" possible.
- [ ] RT measurement standard: official RT = `attempts=1`, `concurrency=1`, disclosed region/hardware,
  fixed warmup. (Adapter currently defaults `num_retries=2`.)

**Ruleset lock**
- [x] Ran the calibration panel (EDF discrimination sweep + 13-config conformance), confirmed
  discrimination/no-saturation, **froze the constants as generation 1.0** (`RULESET_VERSION=1.0`,
  `FROZEN_RULESET_HASH=33034952fa7f`). Evidence in `docs/CALIBRATION.md`. *NB: β-coefficients are
  frozen into the hash but still provisional — a future β-calibration will bump the generation.*
- [x] First real model sweep done: 12 models × 12 seeds × {medium,hard} × {B=1s,B=5s} = 576
  episodes via OpenRouter, RP track, seed-bootstrap CIs, budget-capped at $80 (spent $67.68).
  Board in `leaderboard/results/starter.{md,json}`; runner `scripts/sweep.py` (resume-safe) +
  `scripts/render_board.py`. *Still pending: β-calibration before these are publishable as ranked.*

**Code health**
- [x] Remove dead counters (`overshoot`, `timeouts`). (`observe_calls` is live via the oracle.)
- [ ] Decide legacy `MAX_STEPS_PER_MOVE` / `SCHEMA_MAX_STEPS` (unused) — keep documented or remove.
- [ ] Typed report schema (dataclass/Pydantic) instead of loose dicts, so malformed runs can't look
  valid.
- [ ] Adapter conformance tests (OpenAI-compatible / Anthropic / Gemini / vLLM tool-call shapes);
  current parsing assumes OpenAI-style `tool_calls`.
- [ ] Move the global `litellm.drop_params = True` side-effect out of the per-call path.
- [~] Tests for the new mechanics: auto-burn, exact-match plate, the latency→clock charge, and the
  **no-progress stall guard** (+ counter reset) are covered (55 tests). Still want: walled-layout
  floor model, RP determinism, procgen feasibility.
- [~] CI: pytest on every PR (`.github/workflows/ci.yml`, done); ruff + mypy pending.

---

## P1 — the leaderboard product

- [ ] Submission CLI: `kitchenrush submit prepare` + `validate` (mirror τ²-bench), emitting a
  schema-validated `submission.json` + manifest entry.
- [~] Submission JSON **schema** drafted (`leaderboard/submission.schema.json`) (versions/hashes, `submission_type` standard|custom, track,
  profile/B, tier, seeds_hash, trials, model+harness+env, results [KR + bootstrap CI, Pass^k,
  completion/expiry/burn/invalid/overflow rates, latency p50/p95, tokens, cost], artifact hashes,
  methodology/contamination declaration).
- [ ] CI submission flow: validate → verify artifact hashes → **recompute aggregates from
  trajectories** (and recompute RP) → rebuild leaderboard JSON → comment a summary table on the PR;
  maintainer approval gates "standard" classification.
- [~] `docs/SUBMISSIONS.md` + PR template + `leaderboard/examples/example_standard.json` + manifest
  added (draft); `CONTRIBUTING.md` and a `custom` example still pending.
- [ ] Standard-vs-Custom rules: fixed prompt/tool-schemas/temperature for "standard"; custom for
  modified scaffold/planner/retries/ensembles. Public-accessibility requirement (BFCL-style).
- [ ] Minimal leaderboard (static `data.json` + simple page, or reuse the replay viewer).
- [~] **Official model panel.** 2026-06-11 patch added gpt-5.4 / gpt-5.4-mini (direct OpenAI,
  reasoning none+low), claude-haiku-4.5 (direct Anthropic), nemotron-3 nano/super (OpenRouter)
  — combined board in `leaderboard/results/board.{md,json}` (`scripts/build_board.py`).
  Pending: gpt-5.4·think rerun (OpenAI quota died mid-config; episodes quarantined in
  `runs/openai_patch/`). Starter board done (12 models via OpenRouter, medium+hard ×
  {B=1s,B=5s}, 12 seeds × 1 trial, RP). To upgrade to ranked-official: add trials≥4 (pass^k),
  more seeds, β-calibration, and a frontier-reasoning tier. Budget for the cheap starter was
  ~$68; a full ranked run is the larger spend.
- [~] Cost reporting: per-model **$/episode** and total spend are tallied live from provider usage
  (`scripts/sweep.py`, prices embedded, date-stamped 2026-06-06) and shown on the board. Still
  want a standalone `prices.yaml` + `$/successful order` and `$/100 KR`. Cost stays **Pareto
  metadata, never folded into KR**.
- [~] Statistical reporting: **seed-block bootstrap CIs** done (`render_board.py`, 95% CI on KR̄);
  Pass^k still needs trials≥2 (starter ran trials=1, seeds-over-trials by design).

---

## P2 — the differentiators

- ~~Time-agnostic "intelligence" track~~ — **out of scope.** Kitchen Rush is the realtime
  benchmark; latency-free variants were explored on a side branch and deliberately cut from the
  launch product to keep the focus single (2026-06-10 decision).
- [ ] Failure-taxonomy view on the board (`invalid_by_reason` already exists) — a near-unique
  diagnostic.
- [ ] Contamination defense: canary GUID embedded in generated specs + a maintainer-only hidden
  **challenge** split as the contamination-resistant headline. (Policy exists in CONTAMINATION.md;
  enforcement does not.)
- [ ] Parallel/throughput-aware reference scheduler (unlocks denser streams + higher K; the current
  oracle is sequential greedy-EDF).
- [ ] Replay links for top submissions (the viewer is a strong, almost-unique audit artifact).

---

## P3 — polish & credibility

- [ ] Short tech report / methodology writeup for citeability (METHODOLOGY exists).
- [~] README hero: per-budget leaderboard charts embedded (done); replay GIF rendered
  (`runs/clips/`, offline renderer `scripts/render_clip.mjs`) — embed once the top-2 duel is
  re-cut with a sonnet replay (needs an Anthropic/OpenRouter key).
- [ ] Independent external reproduction of one standard submission before launch (treat every
  failure as a docs/CLI bug).
- [ ] Optional: human/expert baseline rows; Docker for a pinned environment.
- [ ] Multiplayer (2+ chefs) — future extension.

---

## Bottom line
Ruleset is frozen (gen 1.0) and the first private sweep is in (`leaderboard/results/starter`) — the
benchmark discriminates and the per-budget reordering shows the latency tax working. **Before public
rankings**, the two real gates remain: (1) **β-calibration** so RP is a grounded clock rather than an
experimental guess, and (2) the **submission contract + validator** (recompute-from-trajectories) so
results are verifiable and hard to game. The differentiator — **latency made load-bearing inside a
deterministic, reproducible, verifiable tool-world, reported as a cost/latency/competence trade-off
rather than one number** — is genuine and now demonstrated; the launch just has to make it grounded,
verifiable, and hard to game.
