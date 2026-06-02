# Kitchen Rush v2 — ROADMAP

Phased build. Each phase is independently testable; determinism and the single-source-of-truth constants are established in Phase 1 and never violated thereafter.

## Phase 1 — Engine + Procgen + Scoring (the deterministic core)
**Goal:** a complete, deterministic, replayable game with seeded generation and scoring — no LLM yet.
Deliverables:
- `config/constants.py` (THE constant table, RULES §16) + `config/defaults.py` (`GameConfig`, tier presets) + `tiers.json`.
- `engine/`: `clock.py` (float clock, single `latency_to_gs`), `state.py`, `grid.py` (tiles, adjacency, straight-line move), `recipes.py` (R1–R5, step DAG), `cooking.py` (CookJob, burn), `events.py` (ordered log, §11.5 tie-break sweep), `scoring.py` (decay, combo, penalties, `floor(x+0.5)`), `engine.py` (`step`, `observe`, `tool_specs`).
- `tools/`: `schemas.py` (canonical native-FC schemas), `executor.py` (dispatch, fail-fast-commit).
- `procgen/`: `layout.py`, `orders.py`, `oracle.py` (greedy-EDF), `generator.py`, `spec.py`, `splits.py`.
- `baselines/`: `null_agent.py`, `random_agent.py`, `oracle_agent.py`.
- Tests: `test_determinism` (same seed → identical spec + trajectory + event log across processes), `test_clock_latency` (no ceil/round; self-invalidating chains), `test_scoring` (decay monotone, combo gate, superlinear value, adversarial abandon-bot dominated, no-grace-window), `test_recipe_feasibility` (R1–R5 completable at HAND_SLOTS=4), `test_grid_movement`, `test_chained_calls`, `test_procgen` (splits disjoint, fixed-draw determinism, spawn mobility), `test_oracle` (feasibility gate, η clamp).
Exit: `oracle_agent` reproduces `spec.oracle_score`; `null_agent` ≈ 0; all determinism tests green.

## Phase 2 — Adapters + CLI + Trajectory
**Goal:** plug in real models via native FC and produce trajectory logs + summaries.
Deliverables:
- `adapters/`: `base.py` (protocol + types), `openai_compatible.py` (OpenAI/vLLM/Nemotron), `anthropic.py`, `gemini.py`, `litellm.py`, `tokenizer.py` (pinned RP counting incl. reasoning tokens), `registry.py`.
- `harness/`: `runner.py` (`run_episode`/`run_suite`, measure latency → `think_gs` → `engine.step`), `agent.py` (version-pinned reference Agent: system prompt + transcript threading), `conversation.py`.
- `report/`: `schema.py` (pydantic), `writer.py` (JSONL), `aggregate.py` (Pass^k, latency percentiles, η, RTTC).
- `cli.py`: `run / adapters / seeds / aggregate`.
- Tests: `test_adapters_conformance` (per-provider replay, id synthesis, parallel-FC fallback), `test_replay` (RP recomputable from logged tokens; RT replays from wall_ms).
Exit: `kitchenrush run --model openai:gpt-4.1 --seeds dev --trials 1 --track rp` produces valid JSONL + summary; RP score recomputes from the log.

## Phase 3 — Leaderboard + Baselines + Calibration
**Goal:** submission flow, anti-overfitting, and a real-model baseline table.
Deliverables:
- `leaderboard/`: `manifest.py`, `validate.py` (schema + hashes + RP recompute + attempts/concurrency checks + reasoning-token check), `build.py` (board JSON/CSV).
- `cli.py`: `validate / submit / leaderboard build`.
- `docs/CONTAMINATION.md`, canary GUID, hidden CHALLENGE-band wiring, locked `TEST_v1` manifest (hashed).
- Calibration: run frontier models (GPT, Claude, Gemini, Nemotron, a vLLM open-weight) on dev/test; publish a baseline table on **both** tracks; verify Pass^k neither saturates nor floors (tune `θ_pass`/`COMBO_*`/`slack` if needed, bump `RULESET_VERSION`); verify per-tier latency-sensitivity guard.
- Tests: `test_leaderboard_validate`.
Exit: end-to-end submit → validate → board build works; baseline table committed; thresholds grounded in real models.

## Phase 4 — UI (replay dashboard)
**Goal:** evolve the hackathon replay dashboard for grid + movement (lower priority).
Deliverables: `ui/` renders the `n×n` grid, a moving chef token, station tiles, order cards with countdowns, burner timers, the tool-call feed (with chained-turn grouping + aborted/failed markers), and the live score/combo/latency panel from `trajectory.jsonl` + `summary.json`. Reuse the existing `ui/kitchen-rush.*` data contract; extend the renderer for grid coordinates.
Exit: load any run directory and scrub the trajectory visually.

## Future — Speech-to-speech extension
Out of scope now, anticipated by the two-track design: swap the `Λ` definition so `latency_seconds` maps to *audio* real-time (TTFB + audio duration) instead of text token-proxy/wall-clock. The engine, scoring, procgen, and leaderboard are unchanged; only a new audio adapter + an `Λ_audio` track is added. The grid/recipe/scoring core is modality-agnostic by construction.
