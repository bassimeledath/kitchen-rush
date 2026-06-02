# Kitchen Rush v2 — SCORING

This is the mathematical core. It owns the score *formulas*; RULES.md owns the state machine and lists the identical numeric constants (§16 there ↔ §7 here). Every constant cited here is the canonical value from `config/constants.py`.

## 1. Notation & clock

### 1.1 Primitives
| Symbol | Meaning | Default |
|---|---|---|
| `H` | horizon (gs) | 300 (per-tier) |
| `τ` | game clock (float gs) | — |
| `O` | order set (seeded) | — |
| `a_o, d_o` | arrival, deadline of order o (one deadline) | procgen |
| `L_o = d_o − a_o` | order lifetime | procgen |
| `V_o` | base value | `V0+V1·n+V2·n²` |
| `t_o` | serve time | — |
| `r_c, b_c` | cook ready/burn time (absolute gs) | from spec |

### 1.2 Clock-update rule (THE mechanic)
Per turn: `τ += think_gs` first (world moves while thinking), then each chained action's intrinsic duration `δ_act`. One latency charge per response; N action durations.

The single conversion (identical to RULES §3.2.2, defined in `engine/clock.py`):
```
think_gs = LATENCY_SCALE · latency_seconds          # LATENCY_SCALE = 1.0; continuous float
```
**Two tracks** differ only in `latency_seconds`:

- **RT (Real-Latency, diagnostic):** `latency_seconds = wall_clock_total_ms / 1000`, measured around the single successful API call (`attempts=1`, `concurrency=1` enforced by the validator). Hardware/region-dependent; disclosed, never the sole ranking number.
- **RP (Reproducible, canonical/ranked):** deterministic token proxy
```
latency_seconds = β0 + β_in · n_in + β_out · n_out
β0 = 0.30 s,  β_in = 0.0002 s/tok,  β_out = 0.006 s/tok
```
`n_out` **includes reasoning/thinking tokens** (`n_out = completion_tokens + reasoning_tokens`) — closing the thinking-model loophole; a model that emits 5000 hidden reasoning tokens pays for them exactly as a real realtime app would. `n_in, n_out` are counted by the **pinned tokenizer** (`adapters/tokenizer.py`, version-stamped in `version.py`) applied to the canonical transcript — **not** provider `usage` fields — so RP is provider-independent and recomputable from a trajectory log. Cached prompt tokens are counted at full `β_in` (no cache discount) so caching cannot game the score. The validator recomputes RP from logged token counts and rejects mismatches, and rejects submissions where a known thinking-model logs `reasoning_tokens = null`.

`δ_act` durations are the RULES §3.3 constants (move per step 1.0, collect 2.0, chop/prep 4.0, cook-start 2.0, cook-pickup 1.0, plate 5.0, serve 3.0, discard 1.0, observe 1.0, invalid 3.0).

## 2. The score
```
S = Σ_{o∈Delivered} earned_o  −  Σ_{o∈Lost} 0.5·V_o  −  Π_burn  −  Π_invalid  −  Π_drop
earned_o = floor( V_o · f(o) · m_o · q_o + 0.5 )        # q_o ∈ {0,1}, always 1 for a valid plate
```
No win/lose; `S` may be negative (raw for ranking, `max(0,S)` for display). The only rounding is `floor(x+0.5)` per serve, left-to-right float64 (RULES §11.6).

### 2.1 Time-decay `f(o)` (linear, NO grace plateau)
```
f(o) = clamp( 1 − DECAY_RATE · (t_o − a_o)/L_o , FLOOR_FACTOR , 1 )
DECAY_RATE = 0.6,  FLOOR_FACTOR = 0.4
```
`f(a_o)=1`, `f(d_o)=0.4`, slope `f' = −DECAY_RATE/L_o < 0` on the **entire** `[a_o,d_o]` interval. There is no free window — latency costs points in every speed regime. Tight orders (small `L_o`) decay faster, so latency hurts more on them.

### 2.2 Combo / tip multiplier `m_o` (strict, complexity-gated)
Streak `s` = consecutive **on-time** (`t_o ≤ d_o`) **clean** serves (no burned component — guaranteed by the all-or-nothing plate). Only serves of dishes with `n_steps ≥ COMBO_MIN_STEPS = 4` advance `s` (anti-farming). Reset to 0 on any expiry, burn, OR invalid action.
```
m_o = min( COMBO_CAP , 1 + COMBO_STEP · max(0, s−1) )
COMBO_STEP = 0.25,  COMBO_CAP = 2.0      # cap at s=5
```
Multiplicative on decayed value: rewards being fast AND clean AND tackling hard dishes — the three interact super-additively, sharpening the interior optimum.

### 2.3 Quality `q_o ∈ {0,1}` (no per-step partial credit)
A delivered plate is always `q_o = 1` (the plate precondition rejects missing/extra/wrong-state/burned components; an invalid plate is never created). There is no graded partial plate; "mostly correct" is expressed at the *order/throughput* level (shipping more correct dishes faster), not within a dish. This removes the partial-credit-farming exploit and the scoring↔rules contradiction.

### 2.4 Expiry penalty (value-scaled)
```
Lost order o:  S += −floor(EXPIRY_FRACTION · V_o + 0.5),  EXPIRY_FRACTION = 0.5
```
Invariant (holds for every value tier): a serve before deadline pays ≥`0.4·V_o`; expiry costs `−0.5·V_o`; finishing always beats expiring by ≥`0.9·V_o`. Combined with the combo gate (§2.2: hard dishes are *required* to keep a streak), strategic abandonment of hard orders is dominated by honest play (stress-tested by an adversarial abandon-bot in `test_scoring.py`).

### 2.5 Penalty sums
`Π_burn = 8·(#burns)`; `Π_invalid = 5·(#invalid actions)`; `Π_drop = 6·(#bad discards)`. Every invalid action also costs `INVALID_GS=3` clock time and resets combo. Burns and expiries reset combo. (Stalls — timeouts/empty turns — cost only the `think_gs` clock advance, no point penalty, no combo reset.)

## 3. Interior-optimum & latency sensitivity

### 3.1 Single-order functional
Parameterize a policy by latency `ℓ` and care `κ(ℓ)` (probability of correct calls; `κ'(ℓ)≥0`, saturating). Delivery time since arrival `Δ_o(ℓ) ≈ Δ_intrinsic + N·think_gs(ℓ)`; more thinking ⇒ later delivery ⇒ more decay.

### 3.2 Expected points
```
E[P_o](ℓ) = p_succeed(ℓ)·V_o·f(Δ_o(ℓ))·m̄ − (1−p_deliver(ℓ))·0.5·V_o − E[#burn](ℓ)·8 − E[#invalid](ℓ)·5
```
- `f(Δ_o(ℓ))` **decreasing** in ℓ (speed-rewarding).
- `p_succeed(ℓ)`, `m̄` (via fewer combo-breaks) **increasing** in ℓ.
- `E[#burn](ℓ)` is **U-shaped**: high at low ℓ (careless neglect of READY cooks) and high at high ℓ (deliberation overruns `b_c`), minimized interior.

### 3.3 Marginal value of latency (graded, by construction)
On the decay interval (`f' = −0.6/L_o` everywhere — no plateau to zero it):
```
∂P_o/∂ℓ = V_o·m̄ · f' · ∂Δ_o/∂ℓ = −0.6 · (V_o·m̄ / L_o) · N·LATENCY_SCALE   [points / gs]
```
With defaults (`N≈1`, `LATENCY_SCALE=1`): `∂P_o/∂ℓ = −0.6·V_o·m̄/L_o`. Numerics: `V_o=22, m̄=1, L_o=60` → −0.22 pt/s; with combo `m̄=2` → −0.44 pt/s; tight `L_o=20` → −0.66 pt/s. Strictly negative and nonzero for every finite-lifetime order. Episode-level `dS/dℓ ∝ Σ_o V_o·m̄/L_o`, so a model 200 ms/turn faster over a ~30-order, 300 s game gains a measurable, reproducible delta. **Per-tier latency-sensitivity is verified at generation time** (PROCEDURAL §e): the oracle's score delta between ℓ=0 and ℓ=ref-median must exceed a threshold; tutorial/easy tiers are documented as accuracy-focused warmups, not latency-graded.

### 3.4 Both corners lose
- **Pure-speed (ℓ→0, low κ):** `f≈1` but `κ` small ⇒ many invalids (each −5, breaks combo ⇒ `m̄→1`), burns from neglect (−8 + re-cook decay). Score capped below the combo-amplified optimum.
- **Pure-accuracy-slow (ℓ→∞, κ→1):** `f→0.4` (decay haircut), orders expire (`+0.4·V_o → −0.5·V_o`, swing 0.9·V_o), arrival backlog grows, deliberation burns. Dominated by floor-value + expiry.
- **Interior dominates** because `E[#burn]` is U-shaped and `f↓, κ↑`; `dE[S]/dℓ>0` at ℓ→0 and `<0` at ℓ→∞ ⇒ an interior maximizer exists (IVT on the continuous derivative). The first-order condition balances marginal care-benefit against marginal latency-cost — the benchmark *is* that equation.

## 4. The tunable vector θ
| Group | Param | Default | ↑ effect |
|---|---|---|---|
| Latency | `LATENCY_SCALE` | 1.0 | ↑ harder (master latency knob) |
| RP proxy | β0,β_in,β_out | .30,.0002,.006 | ↑ punishes verbose/CoT output |
| Decay | `DECAY_RATE` | 0.6 | ↑ steeper, speed matters more |
| | `FLOOR_FACTOR` | 0.4 | ↓ harder (late ≈ worthless) |
| Deadline | slack_factor (→`L_o`) | per-tier 1.2–2.5 | ↓ harder (PRIMARY realtime knob) |
| Cook | `BURN_WINDOW` | per-ingredient | ↓ harder (sharpens interior) |
| | `BURNER_COUNT` | 2 | ↓ harder |
| Combo | `COMBO_STEP`/`COMBO_CAP` | .25/2.0 | ↑ widens fast+clean advantage |
| | `COMBO_MIN_STEPS` | 4 | gate against farming |
| Value | V0,V1,V2 | 6,2,0.5 | superlinear ⇒ hard dishes worth more/gs |
| Penalties | `EXPIRY_FRACTION` | 0.5 | ↑ punishes slow |
| | `BURN_PENALTY`/`INVALID_PENALTY`/`DROP_PENALTY` | −8/−5/−6 | ↑ harder |
| Grid | `GRID_N` | 7 | ↑ longer routes ⇒ more latency exposure |
| Procgen | arrival rate, complexity mix | per-tier | ↑ throughput pressure |

### 4.1 How procgen sets V_o, L_o
`V_o = V0 + V1·n_steps + V2·n_steps²`. `L_o = d_o − a_o = ceil(C_o · slack_factor)` where `C_o` is the reference-scheduler critical-path time (parallel cooking, grid travel, single-agent serialization). `slack_factor` is the per-tier tightness knob (PROCEDURAL); the MEDIUM-tier value (1.6) is the canonical headline default. There is exactly one slack model (procgen's tier-based factor on the oracle critical path); rules' DEADLINE_SLACK_GS-per-recipe and a fixed global σ are NOT used.

## 5. Oracle & normalization
`S*(seed)` = score of the **greedy-EDF reference scheduler** (PROCEDURAL §e) playing the instance at **zero latency**, baked into the `KitchenSpec` at generation time. It is a strong *reference* upper bound (job-shop with travel+deadlines is NP-hard; greedy-EDF is not provably optimal). Therefore:
```
η = clamp( S_raw / S* , 0 , 1 )      # CLAMPED at 1.0 by definition
```
`η` is named the **oracle-relative score**; beating the reference is impossible-by-clamp, so `η=1`/`RTTC=100` is a well-defined (clamped) anchor — we do NOT claim it is information-theoretically unreachable. We do NOT use floor-subtraction normalization (the null-agent floor is hugely negative and would compress weak models toward 1); the denominator is `S*` alone, and the raw `S` is always reported alongside `η`.

## 6. Aggregate metrics & headline
Per (model, track) over ≥50 seeds × `k` trials. **Ranking is RP-track only.**

Rate metrics: mean points/game `S̄` (raw, not clamped); points-per-gs `S̄/τ̄_end`; on-time rate (`t_o≤d_o`); completion rate; expiry rate; burn rate; invalid-action rate; combo efficiency (realized Σm / max Σm); throughput.
Latency metrics (distribution, never just mean): mean, p50, p90, p95, p99 of per-response `think_gs` (RP) and `wall_ms` (RT); timeouts.

### 6.1 Headline: RTTC (Realtime Tool-Calling Score, 0–100)
```
RTTC = 100 · η · (OnTimeRate)^0.5 · (1 − InvalidRate)^0.5 · R_k^0.5
```
Soft gates (γ=0.5) prevent topping the board by farming points while spamming invalids or finishing late. Reported per track; **canonical leaderboard sorts by RP-track RTTC**, with RT-track RTTC + hardware disclosure adjacent.

### 6.2 Reliability (Pass^k) — single definition
- **Pass** (oracle-relative): an episode passes a seed iff `S_raw ≥ θ_pass · S*(seed)`, `θ_pass = 0.6`. (Computable because `S*` is baked into the spec.) The absolute `pass_score=100` is NOT used.
- **Pass^k** (tau-bench style): run `k = 4` trials per seed; the seed passes-at-k iff all k pass. Report `k ∈ {1,2,4}`. `R_k = (Pass^4)^0.5` feeds RTTC.
- Trials use a **small nonzero temperature `T=0.2`** so Pass^k measures genuine model+sampling reliability, not just provider nondeterminism at `T=0`. (Determinism of *the engine* is unaffected; the latency trace and tool calls of each trial are logged and replayable.)
- Report `S̄` with bootstrap 95% CI, per-seed std, CV, and latency tail.

## 7. Default constant block (identical to RULES §16)
```
LATENCY_SCALE=1.0  (RP: β0=0.30, β_in=0.0002, β_out=0.006, reasoning tokens INCLUDED, pinned tokenizer)
Decay: DECAY_RATE=0.6, FLOOR_FACTOR=0.4 (linear, no grace)
Deadline: L_o = ceil(C_o · slack_factor); slack per-tier (MEDIUM=1.6 canonical)
Value: V_o = 6 + 2·n + 0.5·n²
Cook/burn: per-ingredient (RULES §3.5), procgen-jittered per instance
Burners: BURNER_COUNT=2
Combo: COMBO_STEP=0.25, COMBO_CAP=2.0 (cap s=5), COMBO_MIN_STEPS=4, strict reset
Quality: q∈{0,1}
Penalties: EXPIRY_FRACTION=0.5, BURN=−8, INVALID=−5, DROP=−6
Action gs: move/step 1, collect 2, chop/prep 4, cook-start 2, cook-pickup 1, plate 5, serve 3, discard 1, observe 1, invalid 3
Grid: GRID_N=7
Normalization: η = clamp(S_raw/S*, 0, 1)
RTTC gates: γ=0.5 each
Reliability: k=4, ≥50 seeds, θ_pass=0.6, T=0.2
```
