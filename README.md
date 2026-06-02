# Kitchen Rush

**A public benchmark for FAST *and* ACCURATE tool calling.**

> **Status — alpha (Phases 1–3 done).** The deterministic engine, seeded procedural
> generation, baselines, a native-FC model adapter (via LiteLLM), the reference agent,
> both latency tracks (RT/RP), and aggregate metrics (Pass^k, RTTC) are implemented and
> tested. The public leaderboard, multi-agent, and UI (Phases 4–6) are upcoming
> (see [docs/ROADMAP.md](docs/ROADMAP.md)). Some of this README still describes the
> **end state**; what runs today:
> ```bash
> pip install -e .                      # core is stdlib-only
> kitchenrush run   --baseline random --seed 0 --tier easy
> kitchenrush bench --baseline random --tier easy --seeds 12 --trials 2 --track rp
> pip install -e '.[providers]'         # for real models (needs API keys)
> kitchenrush run   --model openai:gpt-4.1 --seed 0 --tier easy --track rp
> pytest                                # 38 passing
> ```

Kitchen Rush is a text-to-text, Overcooked-inspired benchmark in which a model plays a chef on a seeded grid kitchen, issuing **native function calls** (move, collect, chop, cook, plate, serve) to fulfill arriving orders before they expire and before food burns. Its defining feature: **latency costs points by construction.** The model's measured per-response thinking time is converted to game-seconds that advance a single shared world clock *before* each action resolves — so while the model deliberates, the world keeps moving, cooks burn, and deadlines pass.

## The gap we fill

Tool-calling benchmarks (BFCL, tau-bench/τ², ToolSandbox, AppWorld) measure accuracy and treat latency at best as a turn-count proxy; grid-agent benchmarks (BALROG) measure progression but not wall-clock speed. **None grade latency as a scored axis.** Realtime agents (voice, robotics, live ops) need *both* speed and accuracy. Kitchen Rush makes the speed–accuracy tradeoff the object of measurement: the scoring math is constructed so the unique point-maximizing policy is *interior* — neither reckless-fast nor careful-slow.

## What it measures

- **Speed:** per-response latency, converted to game time, decays order value and risks burns/expiry.
- **Accuracy:** correct, station-gated, well-sequenced tool calls; mistakes and burned/expired dishes cost points.
- **Spatial reasoning + chaining:** the kitchen is an `n×n` grid; the model decides *where* to move and *by how many steps*, then acts. Multiple tool calls can be chained in one response (one latency charge, N action durations) — chaining is rewarded.
- **Reliability:** Pass^k over repeated trials of the same seed.

## Two tracks

| Track | Latency source | Use |
|---|---|---|
| **RP (Reproducible)** — *canonical, ranked* | Deterministic token-proxy from a pinned tokenizer (`think_gs = β₀ + β_in·n_in + β_out·n_out`, reasoning tokens included) | Provider-independent, recomputable from a trajectory log; this is the leaderboard ranking track |
| **RT (Real-Latency)** — *diagnostic* | Measured wall-clock (`concurrency=1`, attempts=1) | Realism check, disclosed with hardware/region; maintainer-verified for top entries; never the sole ranking number |

## Quickstart

```bash
# install (uv-managed; provider SDKs are optional extras)
uv sync --extra all
cp .env.example .env          # add your API keys / endpoints

# run a model on the official test split, 4 trials per seed
uv run kitchenrush run --model openai:gpt-4.1 --seeds test --trials 4 --track rp

# see results
uv run kitchenrush aggregate --run runs/<run_id>
```

## Add your own model (native FC, multi-provider)

Built-in providers: `openai` (also vLLM, Nemotron via base URL), `anthropic`, `gemini`, `litellm`.

```bash
uv run kitchenrush run --model vllm:Qwen/Qwen3-32B    --seeds test --trials 4
uv run kitchenrush run --model anthropic:claude-sonnet-4 --seeds test --trials 4
uv run kitchenrush run --model gemini:gemini-flash-latest --seeds test --trials 4
```

Custom adapter in ~15 lines (structurally satisfies `ModelClient`):

```python
from kitchenrush import register_adapter

class MyCoolClient:
    provider = "mycorp"; supports_parallel_tool_calls = True
    def __init__(self, model, **kw): self.model = model; self.name = f"mycorp:{model}"
    def generate(self, *, system, messages, tools, **kw): ...   # -> ModelResponse

register_adapter("mycorp", MyCoolClient)
# now: kitchenrush run --model mycorp:my-7b --plugin mypkg.adapters
```

The adapter does only "messages + tool schemas in → tool_calls + text + latency + usage out." All game logic lives in the engine. See [docs/INTERFACE.md](docs/INTERFACE.md).

## Leaderboard submission

1. `kitchenrush run --model … --seeds test --trials 4 --track rp`
2. `kitchenrush submit --run <DIR> --meta meta.toml`  (builds a `SubmissionManifest` + `RunSummary` + trajectory hashes)
3. `kitchenrush validate --submission <FILE>`  (same check CI runs: schema, hashes match the frozen split, RP recomputed from logs matches summary)
4. Open a PR adding the submission. CI re-validates; a maintainer merges; the board rebuilds.

**Headline ranking** is RP-track **RTTC** (Realtime Tool-Calling Score, 0–100), segmented by submission type (Standard vs Custom). Anti-overfitting: public train/dev/test seeds plus a maintainer-run hidden `challenge` split and a canary GUID. See [docs/CONTAMINATION.md](docs/CONTAMINATION.md).

## Docs

- [RULES.md](docs/RULES.md) — airtight deterministic ruleset (crown jewel)
- [SCORING.md](docs/SCORING.md) — the math, two tracks, interior-optimum proof
- [INTERFACE.md](docs/INTERFACE.md) — adapters, CLI, JSON schemas
- [PROCEDURAL.md](docs/PROCEDURAL.md) — seeded generation, tiers, splits, oracle
- [MOVEMENT.md](docs/MOVEMENT.md) — grid, movement, chained tool calls
- [DESIGN.md](docs/DESIGN.md) — architecture + data flow
- [MIGRATION.md](docs/MIGRATION.md) — from the hackathon repo
- [ROADMAP.md](docs/ROADMAP.md) — phased build plan

## Related work

BFCL (Berkeley) · tau-bench / τ² / τ³ (Sierra) · ToolSandbox (Apple) · ToolBench · AppWorld · BALROG · overcooked_ai. Kitchen Rush borrows their handler-registry, Pass^k, milestone, split, and grid-as-text-action patterns, and adds the missing **graded latency axis**.

## License

Apache-2.0. See [LICENSE](LICENSE).
