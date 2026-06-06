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
- [ ] Reconcile or archive the **drifted docs** (`SCORING.md`, `MOVEMENT.md`, `PROCEDURAL.md`,
  `INTERFACE.md`, `DESIGN.md`): they still describe RTTC gates, RP-primary, manual directional
  `move`, model-facing `observe`, timer jitter, S/S\* normalization, multi-module splits, and CLI
  commands that don't exist. Banner added pointing to RULES.md; full rewrite/split still pending
  (`METHODOLOGY` / `SUBMISSIONS` / `ROADMAP`).
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
- [ ] Run a cheap calibration panel (≈6 models × 12 seeds × 2 trials × 3 tiers × 3 B), confirm
  discrimination/saturation, then **freeze the provisional constants** (movement cost + penalties)
  and stamp `RULESET_VERSION`.

**Code health**
- [x] Remove dead counters (`overshoot`, `timeouts`). (`observe_calls` is live via the oracle.)
- [ ] Decide legacy `MAX_STEPS_PER_MOVE` / `SCHEMA_MAX_STEPS` (unused) — keep documented or remove.
- [ ] Typed report schema (dataclass/Pydantic) instead of loose dicts, so malformed runs can't look
  valid.
- [ ] Adapter conformance tests (OpenAI-compatible / Anthropic / Gemini / vLLM tool-call shapes);
  current parsing assumes OpenAI-style `tool_calls`.
- [ ] Move the global `litellm.drop_params = True` side-effect out of the per-call path.
- [ ] Tests for the new mechanics (auto-burn, walled layout, exact-match plate, `--no-reasoning`),
  RP determinism, and procgen feasibility.
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
- [ ] **Official model panel** (≈12–15): frontier closed (OpenAI / Anthropic / Google / xAI),
  cost-efficient + open-weight (DeepSeek / Qwen / Llama / Mistral via API or local vLLM), reasoning
  vs no-reasoning variants. Run `medium`+`hard` × all B at ~50 seeds × 4 trials for ranking; `easy`
  as an unranked smoke row. Budget ≈ **$2–5k minimal / $5–15k full**.
- [ ] `prices.yaml` (per-model, date-stamped) → report `$ /episode`, `$ /successful order`,
  `$ /100 KR`; tokens per episode/order. Cost as **Pareto metadata, never folded into KR**.
- [ ] Statistical reporting: **seed-block bootstrap CIs** (trials within a seed are correlated),
  Pass^1/2/4.

---

## P2 — the differentiators

- [~] **KR-INT (time-agnostic intelligence) track** — separate from the realtime board. The
  zero-latency mode is implemented (`--no-latency` → KR-0: thinking costs no game-time; gives the
  "latency tax" = KR-0 − KR-RP). Still to build: the complexity ladder K (below). Zero
  latency (`latency_s=0`), keep intrinsic durations/burn timers, relax deadlines so they never bind.
  A complexity vector `K` (recipe-DAG depth, menu size + shared ingredients, concurrency, decoys,
  memory/horizon, precision); presets K0…K5; per-model **staircase** to report `K50` (highest K with
  ≥50% Pass^4) + area-under-K-curve. (Needs a richer generator; bounded by the reference scheduler.)
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
- [ ] README hero: KR-vs-B results table + an embedded replay GIF.
- [ ] Independent external reproduction of one standard submission before launch (treat every
  failure as a docs/CLI bug).
- [ ] Optional: human/expert baseline rows; Docker for a pinned environment.
- [ ] Multiplayer (2+ chefs) — future extension (see MULTI_AGENT.md).

---

## Bottom line
Don't publish public rankings until the contract + validator + RP credibility are real; but the
first (private) model sweep can start as soon as the ruleset is frozen. The differentiator —
**latency made load-bearing inside a deterministic, reproducible, verifiable tool-world, reported
as a cost/latency/competence trade-off rather than one number** — is genuine; the launch just has
to make it obvious, reproducible, and hard to game.
