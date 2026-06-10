# METHODOLOGY — how Kitchen Rush scores models, and why the parameters are defensible

This document is the *justification layer*. [RULES.md](RULES.md) defines the game and the raw
point formulas (§9 there, mirrored in `scoring.py`); this explains the **headline metric**, how
the **latency budget B** is encoded, and how every tunable parameter is either
**derived**, **calibrated**, or **acknowledged-arbitrary-but-robust**.

It incorporates an independent design review (gpt-5.5) that converged on the scheme below.

## 1. One headline number (no tradeoff plot)

Kitchen Rush is a **realtime** benchmark: speed and accuracy are fused *by construction*
(the world clock advances while the model thinks). We therefore refuse to present a
latency-vs-accuracy Pareto plot as the primary view — that would imply you trade one for the
other, which is the opposite of the point. The headline is a single 0–100 score:

```
KR = 100 · mean_{seed,trial}  clip( (S_model − S_null) / (S_ref − S_null), 0, 1 )
```

- **`S_model`** — raw game score with the model's latency charged into the world clock.
- **`S_null(seed)`** — a "do nothing, let every order expire" policy: no deliveries, no
  invalid/burn/drop behavior. A *meaningful* floor: `KR = 0` means "no better than letting
  the kitchen fail." Computed analytically: `S_null = −Σ_o round(EXPIRY_FRACTION · V_o)`.
- **`S_ref(seed)`** — the greedy-EDF reference scheduler playing the same instance at **zero
  latency**. A strong *reference ceiling*, not a proven optimum; `KR` clips at 100.

**Why null→reference** (not `clip(S / S_ref, 0, 1)`): the bare ratio collapses every
weak-but-nonzero model to ~0; anchoring the floor at the null policy keeps the low end
interpretable and discriminative while still not pretending failure is success. Raw signed
`S_model` is always reported alongside (for audit), but it is **not** the headline.

Everything else — RT latency distribution, RP score, completion / on-time / invalid / burn
rates, Pass^k — is a **diagnostic**, never multiplied into the headline. (This drops the
`√OnTime · √(1−Invalid) · √Pass^k` gates from the older RTTC: they double-count behavior the
game already prices and add arbitrary exponents.) Reliability enters only as a tie-breaker:

```
sort: mean KR  →  lower 95% CI of KR  →  Pass^4  →  RT p95 latency
```

## 2. Encoding the ~1-second preference (in the world, not as a separate axis)

We want to favor models that are **accurate at ≤ ~1 s per decision**. Rather than report
"accuracy at a latency cap" (which re-introduces a tradeoff framing), we bake the target
`B = 1.0 s` into **deadline generation**:

```
C_o(B) = A_o + K_o · B          # reference critical path priced at B s/decision
deadline_o = arrival_o + ceil( σ_tier · C_o(B) )
```

where `A_o` is the intrinsic action+travel+cook time for order `o` and `K_o` is the number of
decisions a competent plan needs. For a model running the same plan at per-decision latency
`ℓ`:

```
margin_o(ℓ) = (σ_tier − 1)·C_o(B) + K_o · LATENCY_SCALE · (B − ℓ)
```

When the slack `σ_tier ≈ 1`, the **sign of the margin flips at ℓ = B = 1 s** — that *is* the
mathematical statement of "favor sub-1s." Faster-than-1s buys slack and higher time-decay
value; slower-than-1s spends it and eventually misses orders. The per-order marginal cost of
latency on the decay interval is `∂P_o/∂ℓ = −ρ · V_o · m_o · K_o · LATENCY_SCALE / L_o` —
strictly negative, larger for valuable/long/combo'd/tight orders.

Accuracy stays necessary because it is **outcome-graded**: only valid deliveries earn points;
invalids, burns, expiries, and broken combos cost them. This yields the intended interior
optimum (reckless-fast and careful-slow both lose).

### 2.1 B is a user-selectable axis (latency profiles)

Different users have different latency needs, so **B is a parameter**, not a fixed constant:
`--latency-budget <s>` or presets `--profile voice (B=1s) | chat (B=5s) | quality (B=20s)`.
Each B is its own leaderboard slice (a single fused realtime ranking at that operating point —
*not* a latency-vs-accuracy tradeoff plot). The horizon scales with B so a loose budget isn't
clipped, and the oracle is re-normalized at the chosen B.

**Evidence it's meaningful (synthetic sweep, clean EDF oracle at injected latency ℓ):**

| budget B | KR(ℓ=1s) | KR(ℓ=2s) | KR(ℓ=4s) |
|---|---|---|---|
| 1 (voice) | 66 | 50 | 15 |
| 5 (chat)  | 88 | 68 | 14 |
| 20 (quality) | 97 | 94 | 19 |

A 2-second agent goes 50 → 68 → 94 as the budget loosens — the ranking genuinely reorders by
latency need. Two robustness facts from the same sweep:
- **No global saturation.** Agents far slower than the budget (ℓ≥4s) stay near 0 at *every* B —
  loosening the budget can't rescue an agent whose cumulative think dominates. So "everyone
  passes past some t" does not happen for slow models.
- **The saturation that does occur is narrow:** at large B, agents *already* faster than the
  budget cluster near 100 and tie *on a clean agent*. For a latency-relaxed user that's
  correct (they're equivalent on latency), and what should separate them is **quality**
  (invalids/burns) — which needs the always-on throughput/quality pressure (dense order stream
  + parallel reference oracle) to have headroom. That is the documented fix for the high-B end
  (tracked in [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md)), not a patch to the metric.

### 2.2 Reading a B-board in deployment terms

"Model X is best at B" means: X is the most reliable kitchen operator *when the world is priced
for B seconds per decision* — the per-decision latency regime its deployment imposes. Two
mathematically grounded translations:

- **Where slow breaks.** For per-decision latency ℓ > B, each order loses `K_o·(ℓ − B)` seconds
  of margin against a slack reserve of `(σ−1)·C_o(B)`; hard misses begin near
  `ℓ* = B + (σ−1)·C_o(B)/K_o`. On the current tiers at B=1 that is ≈3–4 s/decision (e.g. a
  medium burger: C_o(1) ≈ 32 gs, K_o = 6, σ = 1.5 → ℓ* ≈ 3.7 s) — matching the measured EDF
  collapse between ℓ=2 s and ℓ=4 s in [CALIBRATION.md](CALIBRATION.md). Value decay starts
  charging well before ℓ*, so the score gradient is smooth, not a cliff.
- **What B buys in tokens (RP clock).** With `ℓ = 0.30 + 0.0002·n_in + 0.006·n_out`, at a
  representative ~1.5k-token observation the fixed+input cost is ≈0.6 s, so **B=1 s affords
  ≈65 output tokens per decision** (terse single-shot tool dispatch — the voice-agent regime)
  and **B=5 s affords ≈730** (a short reasoning burst — the interactive-assistant regime).
  This is why reasoning-heavy models reorder upward between the B=1 and B=5 boards.

## 3. Latency tracks

| Track | `latency_seconds` | Role |
|---|---|---|
| **RP (ranked)** | `0.30 + 0.0002·n_in + 0.006·n_out` (reasoning tokens incl.) | the reproducible headline — provider-independent, recomputable from logs; *experimental* until the β-calibration study freezes the coefficients |
| **RT (diagnostic)** | measured wall-clock | the realism check; requires disclosed hardware/region, concurrency=1, fixed warmup |

The leaderboard ranks by **RP**; RT is published adjacent as the realism diagnostic. RP
standardizes speed — what that does and doesn't credit is spelled out in
[LIMITATIONS.md](LIMITATIONS.md) §1.

## 4. Parameter taxonomy

**(a) Derived from the 1 s target — formulaic, not hand-tuned:**
`B = 1.0`; `LATENCY_SCALE = 1.0` (so B is literally one second); `deadline_o` via `C_o(B)`;
`floor = 1 − DECAY_RATE` (one knob — guarantees no pre-deadline grace plateau).

**(b) Empirically calibrated by sensitivity analysis (§5):** `σ_tier` (deadline slack —
primary difficulty knob), arrival rate / backlog cap, grid size & layout archetype,
`DECAY_RATE`, `EXPIRY_FRACTION`, `INVALID_PENALTY`/`INVALID_GS`, `BURN_PENALTY` & burn-window
multiplier, combo `STEP`/`CAP`/`MIN_STEPS`, recipe-complexity mix.

**(c) Acknowledged arbitrary, then robustness-tested:** `B = 1 s` itself (a realtime-UX
norm, not first-principles); the recipe/ingredient catalog (flavor); the kitchen aesthetic;
absolute action-duration units (ratios matter once `LATENCY_SCALE=1`); tier labels; hidden-
split governance. For each, we publish a robustness check showing model *rankings* are stable
across reasonable alternatives.

## 5. Sensitivity analysis → choosing final values

Goal: pick parameters that are **stable**, not the ones that maximize separation against
today's models (which overfits to the current zoo).

- **Agents:** null; random; the greedy-EDF reference at injected fixed latencies
  `ℓ ∈ {0, 0.5, 1, 1.5, 2, 4}` s (synthetic, no API needed); plus a panel of real models.
- **Sweep:** one-at-a-time around the center to find sensitive knobs → Latin-hypercube over
  the top 6–8 → confirmation grid on the best plateau.
- **Ranking-stability metric:** Kendall τ_b of model rankings vs the center, plus top-3
  Jaccard and CI-aware pairwise agreement; **seed-block bootstrap** (trials within a seed are
  correlated).
- **Calibration constraints** a setting must satisfy (revisable, but written down *before*
  hunting for the prettiest board):
  - shape: `KR(EDF@0.5s) > KR(EDF@1s) > KR(EDF@2s) > KR(EDF@4s)`, with `KR(EDF@1s) − KR(EDF@2s) ≥ 15`;
  - feasibility: `KR(EDF@1s) ∈ [65,85]`, `KR(EDF@0s) ∈ [90,100]`, `KR(null) ≤ 2`, `KR(random) ≤ 10`;
  - discrimination: real-model IQR ≥ 15 KR points;
  - non-collapse: ≤ 25% of real runs clip to 0, ≤ 10% to 100;
  - stability: median local Kendall τ_b ≥ 0.85.
- **Selection rule:** filter by the constraints → take the largest high-stability plateau →
  choose the simplest point nearest the semantic defaults (`B=1`, `ρ=0.6`, …), mid-plateau →
  freeze as a versioned ruleset and publish the sweep, rank-stability heatmaps, score-vs-
  injected-latency curves, and raw-score distributions.

## 6. Partial credit (deliberately *not* in the headline)

A model that does valid work but completes no order scores ≈ `S_null` ≈ 0 — by design. We do
**not** add per-move or per-milestone credit to the headline: it invites farming safe
subgoals and turns an outcome benchmark into trace-matching. The *first* lever for low-end
discrimination is **calibration** (make instances solvable so capable models actually
complete orders). If, after calibration, the low end is still flat and we want
progress-sensitivity, the only farming-proof option is **potential-based reward shaping**
(`F = Φ(s') − Φ(s)`, Φ = progress toward active orders): it telescopes over an episode
(cycling nets zero) and provably preserves the optimal policy. Reserved as a future option,
gated on evidence.

## 7. The empirical baseline (panel) vs the metric's normalization

These are different and must not be conflated:
- **KR's normalization** (null floor, EDF ceiling) is **model-independent**, so the 0–100
  scale is **stable as models improve** — a new SOTA model simply scores higher; it does not
  rescale everyone. Never normalize to the model panel itself.
- **The model panel** is the empirical "current bar": run today's frontier models, publish
  their KR; future models aim to beat it. The panel also *calibrates difficulty* (§5). It is
  a baseline + calibration input, **not** the normalizer.

## 8. Versioning

Any change to a scoring constant, recipe, or the deadline formula bumps `RULESET_VERSION` and
starts a new leaderboard generation; results carry the version they were produced under.
