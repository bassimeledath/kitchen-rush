# Kitchen Rush

**A benchmark for FAST *and* ACCURATE native tool calling.**

> **Status — beta / first results.** The **ruleset is frozen at generation 1.0**
> (`33034952fa7f`, see [docs/CALIBRATION.md](docs/CALIBRATION.md)) and the **first 12-model sweep**
> is in ([results below](#results--starter-board-gen-10)). Implemented & tested (55 tests): the
> deterministic engine, seeded procedural generation, baselines, a native-FC multi-provider adapter
> (via LiteLLM), the reference agent, both latency tracks (RT/RP), the KR headline metric, a
> greedy-EDF reference oracle, a selectable latency budget, a pinned cl100k tokenizer, version/ruleset
> hashing, a no-progress anti-loop guard, and a pixel-art **replay viewer**. Still before a public
> launch: the leaderboard UI + `submit`/`validate` flow, a time-pressure-free "intelligence" track,
> and **β-coefficient calibration** (so RP is still labelled *experimental*) — see
> [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md).

Kitchen Rush is a text-to-text, Overcooked-inspired benchmark where a model plays a chef on a
seeded grid kitchen, issuing **native function calls** (collect, chop, cook, plate, serve…) to
fulfil arriving orders before they expire and before food burns. Its defining feature: **latency
costs points by construction** — the model's per-response thinking time is converted to
game-seconds that advance one shared world clock *before* each action resolves, so while it
deliberates, food burns and deadlines pass.

## The gap it fills

Tool-calling benchmarks (BFCL, τ-bench/τ², ToolSandbox, AppWorld) measure accuracy and treat
latency at best as a turn-count proxy; **none grade latency as a scored axis.** Realtime agents
(voice, robotics, live ops) need *both*. Kitchen Rush makes the speed–accuracy tradeoff the object
of measurement: the scoring is constructed so the point-maximizing policy is *interior* — neither
reckless-fast nor careful-slow.

## Inspired by Overcooked, but different

Same cooking / deadline pressure, but it strips what *isn't* about tool use: there is **no manual
pathfinding or dexterity** — station actions auto-navigate, so the measured skill is **choosing
the right action sequence under latency**, not steering a sprite. The kitchen layout is fixed per
tier (only the order stream is randomized), because with auto-navigation the model never reasons
about coordinates.

## What it measures

- **Speed** — per-response latency → game-time → decays order value and risks burns/expiry.
- **Accuracy** — correct, well-sequenced tool calls; mistakes, burns, and expiries cost points.
- **Planning under contention** — concurrent orders, shared burners, chained calls (one latency
  charge, N action durations).
- **Reliability** — Pass^k over repeated trials of the same seed.

## Headline metric (KR)

```
KR = 100 · mean over (seeds × trials) of  clip( (S_model − S_null) / (S_ref − S_null), 0, 1 )
```

where `S_null` is the do-nothing floor (serve nothing → everything expires) and `S_ref` is a
deterministic greedy-EDF reference run at **zero latency**. **Reported per latency budget B**
(e.g. B=1s, B=5s) — never averaged into one number. Tokens, $ cost, Pass^k, and a
failure-type breakdown are reported alongside.

## Two latency tracks

| Track | Source | Use |
|---|---|---|
| **RP** (reproducible) | token proxy `β₀ + β_in·n_in + β_out·n_out` (incl. reasoning tokens) | intended ranking track — provider-independent, recomputable. *Tokenizer pinned (cl100k via tiktoken, char/4 fallback); the β-coefficients are still provisional pending the calibration study, so RP is **experimental** until they're frozen.* |
| **RT** (real-latency) | measured wall-clock | realism diagnostic; disclose hardware/region |

> **Important:** RP standardizes speed — it rewards *token economy + decision quality at a fixed
> speed*, and does **not** credit a model for genuinely running faster (that's a deployment
> property). See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for why, and how this compares to
> Artificial Analysis.

## Results — starter board (gen 1.0)

First sweep: 12 models (via OpenRouter) × 12 seeds × {medium, hard} × {B=1s, B=5s}, RP track,
576 episodes. `KR̄` is the mean over tier×budget; `±` is a 95% seed-bootstrap CI. Full board with
per-cell KR and per-budget splits: [leaderboard/results/starter.md](leaderboard/results/starter.md).

| # | model | reasoning | KR̄ | ± | KR @B1 | KR @B5 | $ |
|---|---|---|---|---|---|---|---|
| 1 | claude-sonnet-4.6 | off | **40.6** | ±5.8 | 36.7 | 44.4 | 29.45 |
| 2 | gemini-3.1-flash-lite | off | **26.3** | ±9.8 | 31.6 | 21.0 | 0.79 |
| 3 | qwen3.7-plus | off | 8.3 | ±4.3 | 9.9 | 6.7 | 2.32 |
| 4 | deepseek-v4-pro | off | 7.8 | ±5.4 | 4.1 | 11.5 | 2.04 |
| 5 | gpt-oss-120b | on (low) | 7.1 | ±3.4 | 3.3 | 10.9 | 0.42 |

Most of the panel ran **reasoning off** — this is a *fast* tool-calling benchmark, so no-reasoning is
the honest default; a reasoning cohort (`·think`) ran `reasoning_effort=low`. (Two models —
`grok-build`, `gemini-3.5-flash` — can't disable reasoning on OpenRouter, so they only have an
on row.) The full board labels each model's reasoning state.

The two-budget split is the point: `gemini-3.1-flash-lite` nearly ties for #1 under tight real-time
pressure (B=1s) but **falls** when deliberation is cheap (B=5s), while deeper models
(`deepseek-v4-pro`, `gpt-oss-120b` with reasoning) roughly **triple** with the extra slack — the
latency tax, made visible. RP standardizes speed (see [Limitations](docs/LIMITATIONS.md)); the
experimental β means
absolute KR will shift after calibration, but the ordering is informative now.

## Quickstart

```bash
pip install -e .                          # core is stdlib-only
kitchenrush bench --baseline random --tier easy --seeds 12 --trials 2
kitchenrush calibrate --tier easy --latency-budget 1    # B = seconds/decision the deadlines are priced at

pip install -e '.[providers]'             # real models (needs provider API keys)
kitchenrush bench --model gemini:gemini-3.5-flash --no-reasoning --tier easy --latency-budget 5 --track rt

# watch a game in the browser:
kitchenrush replay --oracle --tier easy --seed 0     # writes ui/replays/easy_seed0_oracle.json
cd ui && python3 -m http.server 8000                 # then open http://localhost:8000
```

CLI today: `run`, `bench`, `replay`, `seeds`, `calibrate`. (`submit` / `validate` + the
leaderboard are coming — see the checklist.)

## Add your own model

Native function calling via LiteLLM — just pass `provider:model`:

```bash
kitchenrush bench --model anthropic:claude-sonnet-4-6 --tier easy --latency-budget 5
kitchenrush bench --model vllm:Qwen/Qwen3-32B         --tier easy --latency-budget 5
```

Or register a custom client (only needs `name` + `generate(system, messages, tools) -> ModelResponse`):

```python
from kitchenrush import register_adapter, ModelResponse
class MyClient:
    name = "mycorp:my-7b"
    def generate(self, *, system, messages, tools, **kw) -> ModelResponse: ...
register_adapter("mycorp", lambda model, **kw: MyClient())
```

## Docs

- [docs/RULES.md](docs/RULES.md) — the **authoritative, code-verified** ruleset
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md) — scoring rationale, B profiles, statistical protocol
- [docs/CALIBRATION.md](docs/CALIBRATION.md) — evidence behind the gen-1.0 ruleset freeze
- [docs/KR_INT.md](docs/KR_INT.md) — the time-agnostic "intelligence" track (complexity ladder K0..K5)
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — what KR does/doesn't measure (incl. the speed caveat + AA comparison)
- [docs/OBJECTIONS.md](docs/OBJECTIONS.md) — anticipated critiques & responses (e.g. "why did a model score worse with more time?")
- [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md) — what's left before a public release
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased build plan
- `SCORING.md` / `MOVEMENT.md` / `PROCEDURAL.md` / `INTERFACE.md` / `DESIGN.md` are **design
  history** pending rewrite — defer to `RULES.md` where they differ.

## Related work

BFCL (Berkeley) · τ-bench / τ² (Sierra) · ToolSandbox · AppWorld · BALROG · overcooked_ai.
Kitchen Rush adds the missing **graded latency axis** inside a deterministic tool-world.

## License

Apache-2.0. See [LICENSE](LICENSE).
