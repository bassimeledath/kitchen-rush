# Kitchen Rush — known limitations

Honest accounting of what the benchmark does and doesn't measure. The biggest one first, because
it's the question everyone asks.

## 1. RP standardizes speed — it does NOT credit a genuinely faster model

Kitchen Rush is real-time: deliberation costs game-time, so we **must** convert a model's tokens
into seconds to run the world clock. The reproducible track (RP, the ranked headline) does this
with a **single shared latency model** for every model:

```
seconds_per_turn = β0 + β_in·(input tokens) + β_out·(output tokens)
```

`β_out = 0.006 s/token` is **~167 tokens/sec, assumed identical for all models**. So RP prices a
model's *output volume* at one universal speed. Consequence:

- RP measures **decision quality + token economy** ("who solves the kitchen in the fewest/shortest
  decisions"), **not real-world wall-clock competitiveness.**
- A model that genuinely runs at 600 tok/s (e.g. on Groq/Cerebras-class silicon) gets **no credit**
  for that speed; a slow model is **not penalised** for being slow. Only token *count* matters.

This is deliberate — speed is a property of the *deployment* (model × provider × hardware × load ×
date), not the model. The same weights run at 30 tok/s on one host and 600 on another, and
OpenRouter silently reroutes between backends. Folding that into the score would make results
non-reproducible (re-run next month → different number) and would conflate intelligence with
whichever GPU the request landed on. So RP holds speed constant on purpose; the cost is that it
**cannot tell you which model will actually keep up in production.**

### How to recover real-world speed
- **RT track (diagnostic):** measured wall-clock latency. Captures real speed, but must be run
  sequentially with disclosed hardware/region/date (concurrent load corrupts it), and isn't
  reproducible across environments. It's our analogue to a live speed measurement.
- **Speed as a separate axis (recommended):** report real per-model/provider output tok/s + TTFT
  *alongside* KR and $ (sourced from a measurement service such as Artificial Analysis, pinned to a
  date), never blended into KR. A buyer then reads "smarter per token" vs "faster in practice" and
  picks by their constraint.
- **Per-model-β "realtime-adjusted" board (optional):** a secondary board that prices each model's
  tokens at its real measured speed (pinned snapshot). More realistic, explicitly non-reproducible,
  never the headline.

## 2. How this compares to Artificial Analysis

AA faces the same underlying fact (speed varies by endpoint) and resolves it differently — and the
difference is instructive:

| | Artificial Analysis | Kitchen Rush |
|---|---|---|
| Is latency *inside* the task? | **No** — quality tasks are untimed | **Yes** — deliberation costs game-time (the whole point) |
| Speed measurement | **Real, per provider/endpoint**, measured continuously | RP: **standardized** single β (reproducible); RT: measured (diagnostic) |
| Speed vs quality | **Separate axes**, never combined | Coupled by design in KR; we re-separate via "report speed alongside" |
| Token *unit* | Standardized (`tiktoken o200k_base`) | Standardized (`tiktoken cl100k_base`) — **aligned in spirit** |

**They keep speed and intelligence on separate axes, which lets them measure real speed without
hurting reproducibility** — they never convert tokens→time. We intentionally coupled the two (that's
the contribution: latency made load-bearing in a deterministic tool-world), which *forces* a speed
assumption, so we standardize it for the reproducible headline and expose real speed separately. In
short: AA avoids the conversion; we embrace it and pay for it with the limitation in §1.

(Minor: AA standardizes on `o200k_base`, we use `cl100k_base`. Switching to `o200k` would make our
token unit directly comparable to AA's — worth doing.)

## 3. Other current limitations
- **RP is provider-trusted on reasoning tokens (not fully recomputable for hidden-reasoning
  models).** RP's `n_out` adds the provider's *self-reported* reasoning-token count, which is over
  **hidden** text and therefore **cannot be recomputed from the canonical transcript** with the
  pinned tokenizer (the visible `n_in`/`n_out` terms can). For a thinking model the dominant latency
  term is thus provider-trusted: a provider that under-reports or returns null/0 reasoning tokens
  pays less game-time. The turn log records `reasoning_reported` (whether the provider actually
  returned a count) so the gap is auditable, but there is **no submission validator** yet to reject
  unreported reasoning — that is a P1 launch item. Until it ships, treat RP for thinking models as
  provider-trusted, not provider-independent. (See RULES §3.2.1, METHODOLOGY §3.1.)
  *Concrete case (`claude-sonnet-5`):* its *adaptive* thinking API returns the reasoning
  **encrypted** and reports `reasoning_tokens: 0` while actually spending ~1000 hidden thinking
  tokens per decision (they surface only inside `completion_tokens`). This is subtler than a
  missing field — the provider *reports* 0, so `reasoning_reported` is even True — so on the
  provider-trusted clock a `tool_choice:auto` thinking run thinks essentially for free and posts an
  inflated ~KR 44. Charging the hidden tokens (`n_out = completion_tokens`) instead drops it to
  ~KR 7.6 at B=5, *below* its reasoning-off row, because ~1000 tokens/decision overruns the budget.
  We therefore publish only the reasoning-off `claude-sonnet-5` row and omit a thinking-on number
  until reasoning-token accounting is enforced. Data for both is preserved under `runs/`.
- **Provisional β-coefficients.** β0/β_in/β_out are not yet calibrated to real measured throughput —
  RP is labelled *experimental* until they are. They're part of the ruleset hash, so calibrating
  them starts a new generation.
- **trials=1 in the starter sweep.** 12 seeds give solid *across-seed* CIs (instance variance), but
  there's no *within-seed* (pass^k) reliability number yet — that needs trials≥2.
- **OpenRouter routing is opaque.** Which backend served a request (and thus its real speed) isn't
  pinned; fine for RP (speed-independent), but it means RT numbers from this harness aren't
  controlled-environment.
- **Single-agent, text-only.** No multi-chef coordination; voice/realtime models are exercised over
  their text path, not their speech pipeline (see RULES §1.1).
- **No prompt caching in the starter sweep.** Each turn re-sends the full prompt (system + tool
  schemas + observation), and the static system+schema block (~1,435 tokens, identical every turn)
  was billed at full input price on every call — there was no provider-side prompt caching. Input
  dominates cost (~92% for the priciest model), so reported `$` figures are an **upper bound**;
  enabling prompt caching would cut input spend roughly 40–50%. Cost is metadata only — it does not
  affect KR or any score.
