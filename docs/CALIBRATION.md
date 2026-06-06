# Ruleset calibration & freeze — generation 1.0

**Frozen:** 2026-06-06 · `ruleset_hash = 33034952fa7f` · `RULESET_VERSION = 1.0`

This records the evidence behind freezing the starter generation. The official starter leaderboard
is computed against this hash; any change to a load-bearing constant (see
`version._RULESET_CONSTANTS`) starts a new generation.

## Reference (EDF oracle) discrimination
`kitchenrush calibrate` sweeps the deterministic greedy-EDF reference at injected per-decision
latency. The reference completes **100% of orders at zero latency** on all tiers (the ceiling is
achievable, so KR normalisation is well-posed), and KR decays smoothly:

| tier | EDF@0s | @0.5s | @1s | @2s | @4s |
|---|---|---|---|---|---|
| easy   | 100 | 96.0 | 92.2 | 83.1 | 9.5 |
| medium | 100 | 94.4 | 89.9 | 75.4 | 0.7 |
| hard   | 100 | 93.6 | 88.0 | 21.6 | 0.0 |

There is a hard collapse between ~2s and ~4s of per-decision latency that **persists at every
latency budget B** (B=1/2/5/10 all collapse by 4s). Cause: the EDF oracle takes ~one decision per
atomic action (~80–100/game), so at ~4s each, cumulative latency exceeds the fixed `horizon_gs`
regardless of how loose the deadlines (B) are. **The horizon, not the deadlines, binds at high
latency.** Consequence — and this is the intended mechanic — **chaining multiple actions per turn
is the load-bearing skill**: a model that emits 5–6 actions per response makes far fewer decisions,
accumulates far less latency, and survives; a model that emits one call per turn collapses.

## Real-model spread (conformance panel, medium, B=1s, 1 seed)
13 OpenRouter configs, all returned parseable native tool calls (adapter conformance passed). KR
spread 0–70 with no saturation at the top — the panel discriminates:

| KR | model (·think = reasoning on) | served | turns |
|---|---|---|---|
| 69.9 | gemini-3.1-flash-lite | 6/6 | 24 |
| 53.6 | claude-sonnet-4.6 | 6/6 | 51 |
| 23.5 | gemini-3.5-flash·think | 4/6 | 10 |
| 18.7 | gpt-oss-120b·think | 4/6 | 43 |
| 0 | (8 others, served 0–3) | — | — |

Single-seed, so noisy; the floor pile-up at B=1 is expected to lift under B=5 (looser deadlines).
Reasoning-on configs mostly collapse at B=1 (slow → miss deadlines) but not universally — gpt-oss
and gemini-flash survive by chaining. This is the latency-tax story the two-budget sweep measures.

## Why freeze now
- Ceiling achievable (oracle 100%), floor well-defined (S_null), normalisation well-posed.
- Smooth, monotonic latency response; clear discriminating band for the realtime-relevant regime.
- Anti-loop stall guard (`STALL_TURNS=12`) bounds runaway tool-call loops without an artificial,
  speed-penalising turn cap.
- Real models spread without saturation.

## Provisional / known
- **RP β-coefficients are provisional** and part of the hash → a future β-calibration bumps the
  generation (RP stays labelled *experimental* until then). RT (wall-clock) is the realism check.
- trials=1 in the starter sweep (12 seeds chosen over more trials, since noise is seed-dominated);
  pass^k at trials≥4 is the upgrade path.
