# Kitchen Rush

**A tool-calling benchmark where thinking time costs points.**

> **Heads-up before quoting numbers:** Kitchen Rush is in beta. The game rules are frozen
> (generation 1.0, hash `33034952fa7f` — see [docs/CALIBRATION.md](docs/CALIBRATION.md)), but the
> token→seconds coefficients behind the reproducible clock are still being calibrated, so
> absolute scores may shift a little. The model *ordering* is already informative.

## Why this exists

Most tool-calling benchmarks (BFCL, τ-bench, ToolSandbox, AppWorld) check *whether* a model
makes the right calls — and the world politely waits while it thinks. That's fine for offline
tasks. But if you're building a voice assistant, a live-ops agent, or anything realtime, you
care about two things at once: **does the model do the right thing, and does it do it fast
enough?** A model that finds the perfect answer after thirty seconds of reasoning is, for you,
the wrong model.

Kitchen Rush measures both at once, by construction: the time a model spends thinking is
converted into game time that passes *before* its actions land. While the model deliberates,
food keeps cooking, food burns, and order deadlines slip away. Speed and accuracy aren't two
charts you squint at — they're one score, experienced the way a deployment would experience
them.

## How it works

The model plays a chef in an [Overcooked](https://github.com/HumanCompatibleAI/overcooked_ai)-style
kitchen. Orders stream in (burgers, soups, ramen…), and the model fulfils them with ordinary
**native function calls** — `collect`, `chop`, `cook`, `plate`, `serve` — racing deadlines,
burn timers, and a combo bonus for consecutive successful dishes. Three deliberate changes from
Overcooked:

1. **Latency is the game.** Every model response first charges its thinking time to the shared
   world clock, then its actions execute. (You can chain several calls in one response and pay
   the latency once — decisiveness is rewarded.)
2. **No joystick skills.** The chef walks itself to the right station automatically; travel
   time is charged inside the action. What's being tested is *choosing the right action
   sequence under time pressure*, not steering a sprite.
3. **Fully deterministic.** Same seed, same actions, same latencies → exactly the same episode,
   every time, on any machine. Every run can be replayed in a browser viewer and audited.

Every episode produces a score called **KR**, from 0 to 100. It's graded on a curve between two
fixed anchors: KR 0 means "no better than doing nothing and letting every order expire," and
KR 100 means "matched a scripted reference chef that plays the same kitchen with zero latency."

## The latency budget (B)

Here's the knob that makes Kitchen Rush flexible: every kitchen is generated **at a latency
budget `B`** (`--latency-budget`, in seconds per decision). Think of B as **the pace the
kitchen is priced for**: order deadlines are set so that a chef spending exactly B seconds on
each decision can finish every order, with roughly 1.4–1.6× headroom to spare. Each B gets its
own leaderboard — results at different budgets are never averaged together.

For the mathematically inclined, the pricing is exact:

```
deadline = arrival + ⌈σ · C(B)⌉,   where C(B) = A + K·B
```

`A` is the order's intrinsic cooking/walking time, `K` is how many decisions a competent plan
needs, and σ is the headroom (1.4–1.6 by tier). So a model that actually decides in ℓ seconds
gains or loses `K·(B − ℓ)` seconds of breathing room per order. Faster than B? You bank slack
and serve while orders are still worth full value. Slower? You eat through the headroom, and
orders start becoming unfinishable at around `ℓ ≈ B + (σ−1)·C(B)/K` — about 3–4 s/decision at
B=1 on the current tiers, which is exactly where our calibration sweep shows the reference
chef collapsing ([docs/METHODOLOGY.md §2](docs/METHODOLOGY.md),
[docs/CALIBRATION.md](docs/CALIBRATION.md)).

And in plain deployment terms: **the model that wins at B=1s is the best pick when every
decision has to land in about a second** — on the benchmark's reproducible clock that's a
budget of roughly 65 output tokens per decision, i.e. terse, single-shot tool dispatch, the
voice-agent regime. **B=5s** buys about 730 tokens per decision — enough for a short burst of
reasoning — the interactive-assistant regime. The same model can rank very differently on the
two boards, and that reordering is precisely what the benchmark is for.

## Results — starter board (gen 1.0)

First sweep: 12 models × 12 seeds × {medium, hard} × {B=1s, B=5s} — 576 episodes. Top 5 shown
here; the full board with per-tier cells is at
[leaderboard/results/starter.md](leaderboard/results/starter.md).

| # | model | reasoning | **KR @B=1s** | **KR @B=5s** | KR̄ ± CI | $ |
|---|---|---|---|---|---|---|
| 1 | claude-sonnet-4.6 | off | **36.7** | **44.4** | 40.6 ±5.8 | 29.45 |
| 2 | gemini-3.1-flash-lite | off | **31.6** | 21.0 | 26.3 ±9.8 | 0.79 |
| 3 | qwen3.7-plus | off | 9.9 | 6.7 | 8.3 ±4.3 | 2.32 |
| 4 | deepseek-v4-pro | off | 4.1 | **11.5** | 7.8 ±5.4 | 2.04 |
| 5 | gpt-oss-120b | low | 3.3 | 10.9 | 7.1 ±3.4 | 0.42 |

Read the two budget columns side by side — that contrast is the product.
`gemini-3.1-flash-lite` nearly ties for first under tight realtime pressure (B=1s) but *drops*
when deliberation gets cheap, while deeper models like `deepseek-v4-pro` and `gpt-oss-120b`
roughly triple with the extra slack. That's the latency tax, made visible. (Most of the panel
ran with reasoning off — this is a benchmark for *fast* tool calling, so no-reasoning is the
honest default; the full board labels every row.)

## Try it

Two minutes, no API key needed:

```bash
pip install -e .                          # the core has zero dependencies
kitchenrush bench --baseline random --tier easy --seeds 12 --trials 2
kitchenrush calibrate --tier easy --latency-budget 1   # see how the reference chef degrades with latency

# watch a game in the browser (scripted chef, no key needed):
kitchenrush replay --oracle --tier easy --seed 0       # writes ui/replays/easy_seed0.json
cd ui && python3 -m http.server 8000                   # then open http://localhost:8000
# ...or race up to 4 models side-by-side on one clock: ?replays=a.json,b.json (see ui/README.md)
```

To benchmark a real model, add provider support and your API key:

```bash
pip install -e '.[providers]'
kitchenrush bench --model anthropic:claude-sonnet-4-6 --tier medium --latency-budget 1
```

Any LiteLLM-routable model works via `provider:model`. You can also plug in a fully custom
client — it only needs a `name` and a `generate(system, messages, tools) -> ModelResponse`
method, registered with `register_adapter`. CLI commands: `run`, `bench`, `replay`, `seeds`,
`calibrate`.

## Learn more

- [docs/RULES.md](docs/RULES.md) — the authoritative, code-verified ruleset
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md) — the KR metric, the math of B, statistical protocol
- [docs/CALIBRATION.md](docs/CALIBRATION.md) — the evidence behind the gen-1.0 freeze
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — what KR does and doesn't measure (worth reading
  before citing results)
- [docs/OBJECTIONS.md](docs/OBJECTIONS.md) — anticipated critiques, answered with data
- [docs/SUBMISSIONS.md](docs/SUBMISSIONS.md) · [docs/CONTAMINATION.md](docs/CONTAMINATION.md) —
  leaderboard contract & data hygiene

## License

Apache-2.0. See [LICENSE](LICENSE).
