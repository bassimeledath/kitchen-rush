# Kitchen Rush v2 — MIGRATION

How to evolve the hackathon repo (voice "Kitchen Rush" under `server/`) into the v2 benchmark (new top-level `src/kitchenrush/` package).

## Verdict: port the engine *architecture*, rewrite the engine *module*; new package, archive `server/`
Keep the proven patterns (frozen-state dataclass, `_advance()` time pump, per-item step dict, event log, `ready_actions` oracle, deterministic `_result`/`_record`). Rewrite into `engine/{state,engine,grid,recipes,cooking,scoring,events,clock}.py`. A new top-level `src/kitchenrush/` package (interface layout is authoritative); `server/` is archived/deleted (voice is out of scope). ~25% of `engine.py` lines port near-verbatim; ~60% of its structure survives as guidance.

## KEEP (port near-verbatim or light edits)
| Asset | File:line | Becomes |
|---|---|---|
| `now_ms()` perf-counter | `core/audio.py:25` | `engine/clock.py` (latency primitive) |
| `(text, latency_ms)` contract | `core/llm.py:88,113` | `adapters/base.py` `ModelResponse.latency` (extended to tool_calls) |
| Nemotron OpenAI-compatible client | `core/llm.py:27-37` | `adapters/openai_compatible.py` (native FC; keep base-url/`enable_thinking`) |
| `_advance` ordered tick pattern | `engine.py:362-367` | `engine/clock.py` advance + `engine/events.py` sweep (+ burn transition, §11.5 tie-break) |
| Recipe/step dict + topo prereq check | `engine.py:11-22,92-105,152-158` | `engine/recipes.py` (+ station gating) |
| `ready_actions()` affordance oracle | `engine.py:271-287` | `engine/engine.py` (+ movement-aware; difficulty aid + scoring oracle) |
| `KitchenEvent` + `_record()` | `engine.py:39-46,432-448` | `engine/events.py` (+ `latency`/`position` detail) |
| Arrival scheduler | `engine.py:369-379` | `procgen/orders.py` arrivals + engine sweep (seeded source) |
| `_result()` / `summary()` envelope | `engine.py:450-457,225-265` | `engine.observe()` (+ grid/burn/pos) |
| Mistake/unnecessary tracking | `engine.py:417-430` | `engine/scoring.py` counters (accrue penalty, never end game) |
| Run/trajectory layout | `kitchen_rush_simulation.py:306-338` | `report/writer.py` (JSONL + result/summary) |
| Replay dashboard | `ui/kitchen-rush.*` | `ui/` placeholder (evolve for grid+chef token; Phase 4) |

## EVOLVE
| Concept | Old | New |
|---|---|---|
| State machine | `KitchenRushGame` `engine.py:73` | `engine/state.py` + `engine.py` (add grid, positions, burn timers, station gating, spec-driven config) |
| Time pump | `_advance(seconds:int)` | float clock; charged by movement steps AND `think_gs`; per-call in a chain |
| Cook state | `active_cooks: dict[item,ready_at]` | `CookJob{stage,start,ready_gs,burn_gs}` + burn transition |
| Scoring | win/lose `final_report()` `engine.py:297-339` | score-accrual `engine/scoring.py` (SCORING formula; `max(0,·)` display only) |
| Tool dispatch | `execute_tool()` `sim.py:124-138` | `tools/executor.py` (native FC, chained, fail-fast-commit) |
| Turn loop | `run_one()` `sim.py:162-233` | `harness/runner.py` (charge `think_gs`, chained calls, no manager) |
| Config loader | `core/game_config.py` | `adapters/registry.py` + `config/defaults.py` + `procgen` |
| LLM adapters | `core/llm.py` | `adapters/{base,openai_compatible,anthropic,gemini,litellm,registry,tokenizer}.py` |

## DROP (voice/hackathon-specific)
All `server/bot-*.py`, `arena_*`, `ARENA.md`; STT/TTS/audio (`core/audio.py` except `now_ms`/`clean_text`, `nvidia_stt.py`, voice fns); `VoicePass` + voice fields of `AgentConfig`/`BridgeOptions`; voice metrics (`record_voice`, `note_manager_question`, `voice_updates`, `manager_question*`); `scenarios.py` + `manager_line_for_turn` (→ procgen, no user-sim); `yc_interview/`; all `cekura_*`, `CEKURA.md`; `core/judge.py`, `rejudge_batch.py`, `transcript_batch.py`, `improvement_loop.py` (no LLM judge — scoring is deterministic); `core/bridge.py` (keep only `remaining_timeout` math → `harness`), `synthetic_bridge.py`, `mock_backend.py`, `startups.py`; voice UI `ui/index.html`, `ui/scene.*`. Regenerate `README.md`/`AGENTS.md` (voice-framed).

## Determinism fixes to apply during the port (from the audit)
- Replace `announced_tickets` (a `set`) and any insertion-`dict` iteration in `_update_cooks` with ordered structures; emit simultaneous cook completions in ascending `burner_index` (RULES §11.5). Event-log equality is part of the determinism contract.
- Remove the early-end behavior (`_maybe_deadline_miss` setting `ended=True`): v2 never failure-terminates (RULES §13.4).

## Module migration map (old → new)
```
engine.py                  → engine/{state,engine,grid,recipes,cooking,scoring,events,clock}.py
scenarios.py               → DELETED → procgen/{generator,orders,layout,oracle,spec,splits}.py
kitchen_rush_simulation.py → harness/runner.py + report/{writer,aggregate,schema}.py + cli.py + tools/executor.py
core/llm.py                → adapters/{base,openai_compatible,anthropic,gemini,litellm,registry,tokenizer}.py
core/audio.py              → engine/clock.py (now_ms only); rest DELETED
core/game_config.py        → adapters/registry.py + config/defaults.py
core/types.py              → AgentConfig slimmed (no voice) near harness; BridgeOptions → RunConfig subset
core/{bridge,text_bridge,defaults}.py → harness/runner.py (timeout math only)
everything voice/cekura/yc/judge → DELETED
ui/kitchen-rush.*          → ui/ placeholder (evolve Phase 4)
runs/                      → runs/ (keep trajectory convention)
```
