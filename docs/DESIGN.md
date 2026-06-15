> ⚠️ **Design history — not the current spec.** Parts of this document describe an earlier or
> aspirational design and may not match the implementation. The authoritative, code-verified spec
> is **[RULES.md](RULES.md)**; release tracking is in **[LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md)**.
# Kitchen Rush v2 — DESIGN

This document ties the subsystems together. The authoritative spec is [RULES.md](RULES.md) (deterministic state machine **and scoring formulas, §9**); the headline-metric rationale is in [METHODOLOGY.md](METHODOLOGY.md). (`SCORING.md` is archived design history.) This doc is the architectural glue.

## 1. Design principles

1. **One source of truth per concern.** Constants live once in `config/constants.py` (mirrored in RULES §16). The tool schema lives once in `tools/schemas.py`. The latency→clock conversion lives once in `engine/clock.py`. Every other module *references* these, never restates values.
2. **Total determinism.** Given `(seed, tier, ordered tool calls, latency trace)`, the engine produces a bit-identical trajectory, score, and event log on any platform. RP-track scores are recomputable from a logged trajectory without re-running inference.
3. **Float clock, integer-free determinism worries handled by a single rounding rule.** The world clock is `float` game-seconds; all boundary comparisons are half-open intervals; the *only* rounding in the score path is `floor(x+0.5)` applied once per serve.
4. **Hands-only world model.** Components live in the chef's hands (capacity 4); there is no free-standing counter storage. This keeps the inventory model small and deterministic. (The cook subsystem is the one exception: a cooking item sits on a burner, occupying no hand slot.)
5. **Engine is the only authority on game state.** Adapters translate one request; the harness owns the conversation; the engine owns time, scoring, and validation.

## 2. Component map

| Component | Package | Responsibility |
|---|---|---|
| **Engine** | `engine/` | `GameState`, `step(calls)→StepOutcome`, `observe()`, `tool_specs()`; clock, grid, recipes, cooking, scoring, events |
| **Procgen** | `procgen/` | Seeded `KitchenSpec` (grid + order stream + per-instance timers), oracle `S*`, splits |
| **Tools** | `tools/` | Canonical native-FC schemas; `ToolCall`→engine dispatch with fail-fast-commit |
| **Adapters** | `adapters/` | `ModelClient` per provider: messages+tools → tool_calls+text+latency+usage; pinned tokenizer for RP |
| **Harness** | `harness/` | Turn loop, reference `Agent`, provider-neutral transcript |
| **Report** | `report/` | Pydantic schemas, JSONL writer, aggregation (Pass^k, latency, RTTC) |
| **Leaderboard** | `leaderboard/` | Manifest, validator (recomputes RP, checks hashes), board builder |
| **CLI** | `cli.py` | `run / validate / submit / leaderboard / adapters / seeds / aggregate` |
| **Baselines** | `baselines/` | null / random / oracle agents (anchor thresholds) |

## 3. The three engine seams (the only API the harness touches)

```python
class KitchenRushEngine:
    def __init__(self, spec: KitchenSpec, config: GameConfig): ...
    def tool_specs(self) -> list[ToolSpec]:      # native-FC schemas for this instance
    def observe(self) -> dict:                    # full structured + ASCII observation
    def step(self, calls: list[ToolCall], *, think_gs: float) -> StepOutcome: ...
    @property
    def state(self) -> GameState: ...
```

`step()` is called once per model response. The harness measures latency, converts it to `think_gs` via the single canonical function, and passes it in. The engine: (1) advances the clock by `think_gs` with the event sweep, then (2) executes the chained calls sequentially (each: validate against current state → advance by action duration with sweep → apply), fail-fast-commit on the first invalid call. `StepOutcome` carries `tool_results`, `events`, `score_delta`, `game_time_sec`, `done`, and `info.sim_seconds_charged`.

## 4. Data flow

```
                 seed, tier
                     │
                     ▼
            ┌─────────────────┐     KitchenSpec (grid, order stream,
            │   procgen        │────▶ per-instance jittered timers,
            │  generator+oracle│      oracle_score S*, null_score)
            └─────────────────┘
                     │ spec
                     ▼
   ┌───────────────────────────────────────────────────────────┐
   │                        HARNESS LOOP                          │
   │                                                              │
   │  engine.observe() ──▶ Agent.build(system, transcript,        │
   │                                   engine.tool_specs())        │
   │            ▲                         │                        │
   │            │                         ▼                        │
   │            │              client.generate(...)  ── measures ──┐│
   │            │                         │            latency      ││
   │            │              ModelResponse(tool_calls,            ││
   │            │                 text, latency, usage)             ││
   │            │                         │                         ││
   │            │      think_gs = clock.latency_to_gs(              ││
   │            │          RP: tokenizer counts | RT: wall_ms)  ◀───┘│
   │            │                         │                         │
   │            │           engine.step(tool_calls, think_gs)       │
   │            │                         │                         │
   │            │              StepOutcome(results, events,         │
   │            │                 score_delta, done)                │
   │            │                         │                         │
   │            └──── transcript += tool results ◀──────────────────┤
   │                                      │                         │
   │              report.writer ◀── StepRecord (JSONL)              │
   │                                      │                         │
   │                              done?  ─┴─ no ─▶ loop             │
   └───────────────────────────────── yes ───────────────────────┘
                                       │
                                       ▼
                          EpisodeResult ──▶ RunSummary ──▶ aggregate
                                              │
                                              ▼
                                   leaderboard manifest + validate
```

## 5. Latency → game-clock (the single mechanism)

Defined once in `engine/clock.py`, cited by RULES §3.2 and SCORING §1.2:

```python
LATENCY_SCALE = 1.0   # game-seconds per real second
def latency_to_gs(latency_seconds: float) -> float:
    return LATENCY_SCALE * latency_seconds      # continuous float; NO ceil, NO round
```

- **RT track:** `latency_seconds = wall_clock_total_ms / 1000` (successful attempt only).
- **RP track:** `latency_seconds = β₀ + β_in·n_in + β_out·n_out`, where `n_out` *includes reasoning/thinking tokens*, counted by the pinned tokenizer on the canonical transcript (not provider usage). β₀=0.30, β_in=0.0002, β_out=0.006.

Within a turn: `think_gs` advances the clock first (world moves while thinking), then each action's intrinsic duration advances it. The event sweep (§ RULES 11.5) fires at every advance.

## 6. Determinism contract (cross-cutting)

- Procgen uses `numpy.random.SeedSequence(seed).spawn(4)` → four named sub-streams (`rng_grid, rng_orders, rng_timers, rng_jitter`). Each generation attempt draws a *fixed* number of values up front, then validates, so sub-stream position is implementation-independent.
- The live engine never iterates a `set` or insertion-ordered `dict` for normative behavior; simultaneous events resolve by the fixed tie-break (RULES §11.5) with explicit sort keys.
- `RULESET_VERSION` is a content hash of `constants.py` + `recipes.py` + `scoring.py` + tier presets; `SCHEMA_VERSION` for JSON shapes; `GENERATOR_VERSION` for procgen. All three stamp every output file and are checked by the validator.

## 7. Versioning & compatibility

A submission is valid on the headline board only if its `ruleset_version`, `config_hash`, and `seeds_hash` match the active official split. Bumping any scoring constant or recipe bumps `RULESET_VERSION` and starts a new board generation.
