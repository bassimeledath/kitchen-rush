# ROADMAP

The big design docs ([RULES](RULES.md), [SCORING](SCORING.md), [INTERFACE](INTERFACE.md),
[PROCEDURAL](PROCEDURAL.md), [MOVEMENT](MOVEMENT.md)) describe the **end state**. This roadmap
is the **lean build order**: prove the core idea (latency costs points) is fun and
discriminative *first*, then add benchmark apparatus only when there's a concrete need.

Guiding rule: don't build leaderboard/anti-cheat/cross-hardware machinery before the game
itself is worth standardizing.

---

## Phase 1 — Playable, deterministic engine (✅ done)

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

## Phase 2 — Real models (single adapter) (✅ done)

- `adapter.py` — **one LiteLLM-based** `ModelClient` (OpenAI / Anthropic / Gemini / vLLM /
  Nemotron via native function calling) that measures wall-clock latency, with timeout +
  retries and graceful stall-on-error.
- `agent.py` — stateless reference agent fed **only human-visible info** (readable grid,
  station positions, tickets, hands, outcomes — no nav hints, no valid-action list).
- `report.py` — JSONL trajectory writer.
- Validated against real Gemini 2.5/3.5-flash: the latency mechanic cleanly separated
  "too slow" from "can't play."

## Phase 3 — Make it a benchmark (✅ core done)

Implemented (see [docs/METHODOLOGY.md](METHODOLOGY.md)):
- **KR headline** — `100·mean clip((S−S_null)/(S_ref−S_null),0,1)`; a single realtime score,
  **no √-gates** (raw score, rates, latency percentiles, Pass^k are diagnostics); degenerate
  instances (`S_ref ≤ S_null`) excluded + counted.
- **Greedy-EDF reference + null floor** (`oracle.py`): the `S_ref` ceiling, the `S_null`
  floor, and the injected-latency calibration sweep (`kitchenrush calibrate`).
- **User-selectable latency budget B** — `--latency-budget` / `--profile voice|chat|quality`
  (B = 1 / 5 / 20 s). Deadlines are priced at B (`procgen.critical_path`); the horizon scales
  with B; each B is its own leaderboard slice. A synthetic sweep confirms the ranking reorders
  by latency need (a 2s agent: KR 50→68→94 across voice/chat/quality).
- Multi-trial `run_suite` + Pass^k; **RT primary / RP shadow** tracks.

Remaining in Phase 3:
- **Parallel reference scheduler + dense/throughput-bound stream** — the current *sequential*
  oracle can't fully complete dense/overlapping orders, so **easy is calibrated** (KR(EDF@1s)≈70,
  monotone) while **medium/hard are provisional**. This also fixes the only saturation case
  (at large B, already-fast clean agents tie near 100; an always-on throughput/quality pressure
  gives quality headroom to separate them).
- **Model panel** → first real KR baseline (in progress).
- Full sensitivity sweep, then freeze parameters on the stability plateau (METHODOLOGY §5).

## Phase 4 — Public leaderboard & anti-overfitting

- Manifest + validation + PR submission flow; `leaderboard/` subsystem.
- Hidden `challenge` seed band + canary GUID (resolves open question #4).
- Version hashes (`RULESET_VERSION`, …) and contamination policy enforcement.

## Phase 5 — UI & future

- `ui/` replay dashboard (watch food burn while the model thinks).
- Future: speech-to-speech evaluation track.

## Phase 6 — Multi-player (2+ chefs) — deferred, simple

One LLM controls 2+ chefs in the same kitchen; each turn it says what each chef does
(single-chef is the C=1 case). Small change: per-chef position + hands, a `chef` index on
each tool call; the existing chained-call loop and single latency charge already handle the
rest. One "think" driving multiple bodies amortizes latency — a good, cheap test. Full note
in [docs/MULTI_AGENT.md](MULTI_AGENT.md).

---

## Open questions (most are deferred until they matter)

Only **#1** and **#5** need an answer to start; both have safe defaults already adopted.

1. **`LATENCY_SCALE`** (how harshly thinking-time hurts) — adopted **1.0** (1 real-sec =
   1 game-sec); revisit after Phase 2 calibration. *(active)*
5. **Pass^k temperature** — adopted **0.2**; revisit in Phase 3. *(active)*
2. RP token-proxy coefficients (β) — **implemented** with defaults (0.30 / 0.0002 / 0.006);
   calibrate in the sensitivity sweep. *(RT is primary, so this is lower-stakes.)*
3. Pinned tokenizer for RP counting — **placeholder** (deterministic char/4); swap for a
   real BPE before any RP-ranked release.
4. Headline split (hidden `challenge` vs public `test`) — **deferred to Phase 4**.
6. **Parallel reference scheduler** — needed to calibrate medium/hard (new, active).
