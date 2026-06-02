# ROADMAP

The big design docs ([RULES](RULES.md), [SCORING](SCORING.md), [INTERFACE](INTERFACE.md),
[PROCEDURAL](PROCEDURAL.md), [MOVEMENT](MOVEMENT.md)) describe the **end state**. This roadmap
is the **lean build order**: prove the core idea (latency costs points) is fun and
discriminative *first*, then add benchmark apparatus only when there's a concrete need.

Guiding rule: don't build leaderboard/anti-cheat/cross-hardware machinery before the game
itself is worth standardizing.

---

## Phase 1 — Playable, deterministic engine (✅ in progress)

The irreducible core. Stdlib-only (no third-party runtime deps).

- `config.py` — the §16 constant table, recipe/ingredient catalog, difficulty tiers.
- `scoring.py` — base value, time-decay, combo, penalties (RULES §9).
- `engine.py` — deterministic discrete-event engine: grid + movement, station-gated
  actions, cooking + burn windows, orders + deadlines, chained tool calls, the
  latency→clock mechanic, scoring, event sweep with the fixed tie-break.
- `tools.py` — the native function-calling tool schemas + a `ToolCall` type.
- `procgen.py` — seeded generation of a solvable kitchen + order stream (simple but
  correct; full oracle/feasibility proof deferred).
- `runner.py` + `baselines/` — run a full episode with an in-process policy (null / random),
  injecting a latency trace. No network needed.
- `cli.py` — `kitchenrush run --baseline random --seed 0 --tier easy`.
- `tests/` — determinism, scoring, engine mechanics, procgen validity.

**Exit criteria:** `kitchenrush run` plays a full game; tests green; same seed + same
actions + same latency → bit-identical result.

## Phase 2 — Real models (single adapter)

- `adapter.py` — **one LiteLLM-based** `ModelClient` (covers OpenAI / Anthropic / Gemini /
  vLLM / Nemotron via native function calling) that measures wall-clock latency.
- Wire the adapter into `runner.py`; default reference agent prompt.
- `report.py` — JSONL trajectory + run-summary writer.
- Run a few real models; confirm scores **spread apart** (the benchmark discriminates).

**Exit criteria:** `kitchenrush run --model openai:gpt-4.1 --seeds test` produces a scored
trajectory; ≥3 models rank differently in a way that tracks speed *and* accuracy.

## Phase 3 — Make it a benchmark (only after Phase 2 is convincing)

- Multi-trial runs + reliability (Pass^k / score variance).
- Aggregate metrics + a headline score (RTTC), normalized against baselines.
- **Reproducible (RP) latency track** (token-proxy) — add when cross-hardware comparison
  actually matters. Resolves open questions #2/#3.
- Hand-written per-provider adapters only if LiteLLM hides needed behavior.

## Phase 4 — Public leaderboard & anti-overfitting

- Manifest + validation + PR submission flow; `leaderboard/` subsystem.
- Hidden `challenge` seed band + canary GUID (resolves open question #4).
- Version hashes (`RULESET_VERSION`, …) and contamination policy enforcement.

## Phase 5 — UI & future

- `ui/` replay dashboard (watch food burn while the model thinks).
- Future: speech-to-speech evaluation track.

---

## Open questions (most are deferred until they matter)

Only **#1** and **#5** need an answer to start; both have safe defaults already adopted.

1. **`LATENCY_SCALE`** (how harshly thinking-time hurts) — adopted **1.0** (1 real-sec =
   1 game-sec); revisit after Phase 2 calibration. *(active)*
5. **Pass^k temperature** — adopted **0.2**; revisit in Phase 3. *(active)*
2. RP token-proxy coefficients (β) — **deferred to Phase 3** (no RP track yet).
3. Pinned tokenizer for RP counting — **deferred to Phase 3**.
4. Headline split (hidden `challenge` vs public `test`) — **deferred to Phase 4**.
