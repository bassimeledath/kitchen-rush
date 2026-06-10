# Kitchen Rush

**The realtime tool-calling benchmark: thinking time costs points.**

> **Status — beta.** Ruleset frozen at **generation 1.0** (`33034952fa7f`,
> [docs/CALIBRATION.md](docs/CALIBRATION.md)). The starter board below ran the reproducible (RP)
> latency track, whose β-coefficients are still experimental — absolute KR will shift after β
> calibration, but the ordering is informative now.

## Why

Tool-calling benchmarks (BFCL, τ-bench/τ², ToolSandbox, AppWorld) grade *what* a model calls,
never *how long it took to decide* — the world politely waits while the model thinks. Realtime
agents (voice, live ops, games, robotics) don't get that luxury, and a model that needs 30
seconds of reasoning to make the right call is the wrong model for them no matter how right it
is. Kitchen Rush makes latency a scored axis **by construction**: the model's per-response
thinking time is converted to game-seconds that advance one shared world clock *before* its
actions resolve. While the model deliberates, food burns and orders expire — so speed and
accuracy are measured fused, as one number, the way deployment actually experiences them.

## What it is

An [Overcooked](https://github.com/HumanCompatibleAI/overcooked_ai)-inspired kitchen: the model
is a chef on a seeded grid, fulfilling a randomized order stream (burgers, soups, ramen…) via
**native function calls** — `collect`, `chop`, `cook`, `plate`, `serve` — against order
deadlines, burn timers, and a combo multiplier. Three deliberate rule changes from Overcooked:

1. **Latency is the mechanic.** Every response charges its latency to the world clock first
   (measured wall-clock on the RT track; a deterministic token-price on the reproducible RP
   track). Chaining several calls in one response pays the latency once — decisiveness is a
   skill.
2. **No pathfinding, no dexterity.** Station actions auto-navigate and charge travel time
   inside the action. The measured skill is *choosing the right action sequence under time
   pressure*, not steering a sprite.
3. **Deterministic and replayable.** Same seed, actions, and latencies → bit-identical episode,
   with a browser replay viewer for auditing any run.

Score is normalized per instance: `KR = 100 · (S_model − S_null) / (S_ref − S_null)`, where
`S_null` is doing nothing and `S_ref` is a scripted greedy-EDF reference at zero latency.
KR 0 = no better than letting the kitchen fail; KR 100 = matched the reference.

## The latency budget B

Every instance is generated **at a latency budget `B`** (`--latency-budget`, in seconds per
decision), and each B is its own leaderboard — never averaged together. B has an exact meaning:
**deadlines are priced so that a chef who spends exactly B seconds deciding each action finishes
every order with σ ≈ 1.4–1.6× headroom**:

```
deadline = arrival + ⌈σ · C(B)⌉,   C(B) = A + K·B
```

where `A` is the order's intrinsic cook/travel/action time and `K` the number of decisions a
competent plan needs. A model deciding in ℓ seconds therefore gains or loses `K·(B − ℓ)` seconds
of margin per order: **faster than B banks slack and serves at higher value (order value decays
linearly toward the deadline); slower than B burns through the σ-headroom and starts missing
orders outright near ℓ ≈ B + (σ−1)·C(B)/K** — about 3–4 s/decision at B=1 on the current tiers,
which is exactly where the calibration sweep shows the reference scheduler collapsing
([docs/METHODOLOGY.md §2](docs/METHODOLOGY.md), [docs/CALIBRATION.md](docs/CALIBRATION.md)).

In applied terms: **winning at B=1s means being the most reliable model when every decision must
land in about a second** — on the RP clock (`0.30 + 0.0002·n_in + 0.006·n_out` s), that's a
budget of roughly **65 output tokens per decision** at a typical observation, i.e. terse
single-shot tool dispatch, the voice-agent regime. **B=5s** affords ~730 tokens — a short
reasoning burst per decision — the interactive-assistant regime. A model can top one board and
not the other; that reordering is the point.

## Results — starter board (gen 1.0)

12 models × 12 seeds × {medium, hard} × {B=1s, B=5s}, RP track, 576 episodes. Top 5 of 12 by
overall mean (±95% seed-bootstrap CI); full board:
[leaderboard/results/starter.md](leaderboard/results/starter.md).

| # | model | reasoning | **KR @B=1s** | **KR @B=5s** | KR̄ ± CI | $ |
|---|---|---|---|---|---|---|
| 1 | claude-sonnet-4.6 | off | **36.7** | **44.4** | 40.6 ±5.8 | 29.45 |
| 2 | gemini-3.1-flash-lite | off | **31.6** | 21.0 | 26.3 ±9.8 | 0.79 |
| 3 | qwen3.7-plus | off | 9.9 | 6.7 | 8.3 ±4.3 | 2.32 |
| 4 | deepseek-v4-pro | off | 4.1 | **11.5** | 7.8 ±5.4 | 2.04 |
| 5 | gpt-oss-120b | low | 3.3 | 10.9 | 7.1 ±3.4 | 0.42 |

The per-budget split is the product: `gemini-3.1-flash-lite` nearly ties for #1 under tight
realtime pressure (B=1s) but **falls** when deliberation is cheap (B=5s), while deeper models
(`deepseek-v4-pro`, `gpt-oss-120b`) roughly **triple** with the slack — the latency tax, made
visible. Most of the panel ran reasoning **off**: this is a fast tool-calling benchmark, so
no-reasoning is the honest default (the full board labels each model's reasoning state).

## Quickstart

```bash
pip install -e .                          # core is stdlib-only
kitchenrush bench --baseline random --tier easy --seeds 12 --trials 2
kitchenrush calibrate --tier easy --latency-budget 1   # KR of the reference at injected latencies

pip install -e '.[providers]'             # real models (needs provider API keys)
kitchenrush bench --model anthropic:claude-sonnet-4-6 --tier medium --latency-budget 1

# watch a game in the browser:
kitchenrush replay --oracle --tier easy --seed 0     # writes ui/replays/easy_seed0_oracle.json
cd ui && python3 -m http.server 8000                 # then open http://localhost:8000
```

CLI: `run`, `bench`, `replay`, `seeds`, `calibrate`. Any LiteLLM-routable model works via
`provider:model`; or register a custom client (`name` +
`generate(system, messages, tools) -> ModelResponse`) with `register_adapter`.

## Docs

- [docs/RULES.md](docs/RULES.md) — the authoritative, code-verified ruleset
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md) — the KR metric, the math of B, statistical protocol
- [docs/CALIBRATION.md](docs/CALIBRATION.md) — evidence behind the gen-1.0 freeze
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — what KR does/doesn't measure (read before citing)
- [docs/OBJECTIONS.md](docs/OBJECTIONS.md) — anticipated critiques & responses
- [docs/SUBMISSIONS.md](docs/SUBMISSIONS.md) · [docs/CONTAMINATION.md](docs/CONTAMINATION.md) —
  leaderboard contract & data hygiene

## License

Apache-2.0. See [LICENSE](LICENSE).
