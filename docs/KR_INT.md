# KR-INT — the time-agnostic intelligence track

A second track that measures **planning correctness with speed removed**, complementary to the timed
KR headline. Motivation and design discussion: see [LIMITATIONS.md](LIMITATIONS.md) /
[OBJECTIONS.md](OBJECTIONS.md).

## What it is
- **Latency is free** (`LATENCY_SCALE=0`): thinking costs no game-time. A model can deliberate
  arbitrarily long with no penalty.
- **Deadlines never bind** (`relax_deadlines`): orders wait indefinitely; nothing expires from the
  clock.
- **Intrinsic dynamics stay on**: cook durations and burn timers remain, so the model must still
  *sequence* actions correctly (don't burn food, plate exact matches) — but measured in **action
  order**, not wall-clock. With latency=0, game-time is a deterministic function of the action
  sequence, so this is a pure *scheduling/planning* test, not a speed test.

Because deadlines don't bind, the **sequential greedy-EDF oracle solves every rung** (it isn't racing
a clock) — validated at **100% completion, 8 seeds/rung** — so each rung is feasible by construction
and the score needs no S_ref/S_null normalization.

## The complexity ladder K0..K5
Difficulty scales in action/state units (not time), burners held at 2 so contention is what breaks a
planner as volume/concurrency rise. Recipe depth enters via the menu (soup 3 → veggie_ramen 9 steps).
Lives in `kitchenrush/kr_int.py`, **outside `config.TIERS`**, so the frozen gen-1.0 timed-track hash
is untouched.

| K | menu (recipes) | max orders | arrival pace | hints | grid |
|---|---|---|---|---|---|
| K0 | soup | 2 | slow | yes | 7 |
| K1 | soup, burger | 3 | | yes | 7 |
| K2 | burger, salad | 4 | | yes | 7 |
| K3 | burger, salad, soup | 6 | | no | 7 |
| K4 | burger, salad, mushroom_cheeseburger | 8 | | no | 7 |
| K5 | all 5 (incl. veggie_ramen) | 10 | dense | no | 9 |

## Scoring
- **Per instance:** completion fraction = orders served correctly / total (0..1).
- **Per rung:** mean completion over seeds (+ full-pass rate = fraction of seeds with completion 1.0).
- **Headline:** **K50** = highest rung still cleared at ≥50% mean completion, linearly interpolated
  (the complexity ceiling); **AUC** = area under the completion-vs-K curve (0..1).

## Note on the ruleset hash
KR-INT runs at `LATENCY_SCALE=0`, which is a load-bearing (hashed) constant — so KR-INT outputs carry
a **distinct ruleset hash** (`frozen: false` vs the timed track). This is correct: zero-latency is a
different scoring regime, a deliberately separate generation. The frozen timed-track hash
(`33034952fa7f` at `LATENCY_SCALE=1`) is unaffected.

## First baseline — GPT-5.5 (reasoning=low), 3 seeds
| rung | K0 | K1 | K2 | K3 | K4 | K5 |
|---|---|---|---|---|---|---|
| completion | 0.83 | 1.00 | 1.00 | 0.94 | 1.00 | 0.97 |

**K50 = 5.0 (maxed), AUC = 0.957, cost $9.95.** Full result:
`leaderboard/results/krint_gpt55-low.json`.

**Finding: the current ladder is too easy to find a frontier model's ceiling.** GPT-5.5 clears all six
rungs at ≥83% (the K0 0.83 dip is small-sample noise — easiest rung below K4 is an artifact of n=3).
To discriminate at the top, the ladder needs to **extend harder (K6+)**: more orders, denser
concurrency, tighter burn windows, possibly fewer burners or a larger grid. The action-unit knobs are
already in place; only new presets are needed. A wider model panel will also place weaker models lower
on the existing rungs.
