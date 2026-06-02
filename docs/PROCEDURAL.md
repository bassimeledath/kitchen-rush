# Kitchen Rush v2 — PROCEDURAL GENERATION

A single `(seed, tier)` deterministically produces one `KitchenSpec`: grid, station placement, the full order stream (dishes, arrivals, deadlines), and all per-instance jittered timers. Re-running `generate_spec(seed, tier)` reproduces it byte-for-byte. The spec — not the generator — is the engine's source of truth; it carries `generator_version` so locked test specs pin `(seed, tier, generator_version)`.

## 0. RNG discipline (mandatory)
One `numpy.random.SeedSequence(seed).spawn(4)` → four named sub-streams so changing one stage never perturbs another:
```python
ss = numpy.random.SeedSequence(seed)
rng_grid, rng_orders, rng_timers, rng_jitter = (
    numpy.random.Generator(numpy.random.PCG64(c)) for c in ss.spawn(4))
```
Rules: never iterate a `set` or unordered `dict` during generation (sort keys first); never use the global `random`. **Each generation attempt draws a FIXED, documented number of values up front, then validates** — so sub-stream position after attempt k is implementation-independent (resolves the rejection-sampling determinism hole). This multi-substream design is authoritative; the single-`Generator` model is not used.

## (a) Grid generation
Grid `n×n`; cells `FLOOR`/`WALL`/`STATION` (stations non-walkable; operated from a 4-adjacent floor cell — Overcooked convention). Border is WALL.

**Size by tier:** TUTORIAL 5, EASY 6, MEDIUM 7, HARD 8, NIGHTMARE 9.

**Archetypes** (uniform from tier's allowed list): `OPEN` (perimeter ring, interior floor — TUTORIAL/EASY default), `CENTRAL_ISLAND` (+ central counter block — MEDIUM), `CORRIDOR` (+ interior wall segment(s) forming a chokepoint, never partitioning floor — HARD/NIGHTMARE). Interior wall segments ≤ `n-2`, never partitioning.

**Stations:** 1 `PASS` (perimeter), 1 `PLATE`, 1 `BIN`, `num_burners` `STOVE` (= `BURNER_COUNT`, tier 2–3), 1–2 `BOARD`, 1 `ING` per active ingredient. Placement: only on WALL/counter cells with ≥1 adjacent FLOOR; seeded farthest-point sampler with min separation `d_min = max(2, floor(n/3))` (0.7 spread / 0.3 uniform, both from `rng_grid`). Two stations never share an access cell.

**Hard constraints (all must pass, else resample from the same sub-stream):**
1. Floor connectivity (BFS from spawn reaches all floor).
2. Every station has ≥1 reachable access cell.
3. Pairwise station access reachability.
4. Travel-cost balance: Σ pairwise Manhattan among access cells ∈ `[1.5n, 6n]` (rejects pancake-flat and pathological sprawl).
5. **Spawn cell has ≥1 walkable 4-neighbor** (chef can always move — resolves the boxed-in softlock; walked-to floor cells always retain a walkable entry neighbor, so a pocket is unreachable mid-game).

Hard cap 200 attempts → fall back to `OPEN` (constructively valid). `generate_spec` always returns a valid grid.

## (b) Order stream
**Dish catalog & complexity:** salad/soup/burger (simple), mushroom_cheeseburger/veggie_ramen (hard) — the RULES §2.4 catalog. Per-tier complexity mix governs sampling.

**Inter-arrival = non-homogeneous Poisson** with shape `λ(t)=λ_base·shape(t)`, `shape`: 0–20% ramp 0.5→1.0, 20–70% peak 1.0, 70–100% decay 1.0→0.3. Gaps `~Exp(1/λ(t))` from `rng_orders`, accumulated until `arrival > horizon`. Minimum inter-arrival floor `g_min=4 gs`. **Backlog cap B** (tier 2–5): if a new arrival would exceed B live (arrived, unserved, pre-deadline) orders, push it later — guarantees a perfect agent is never pile-up-doomed while still pressuring a slow one.

**Per-instance timers:** each dish's `COOK_TIME`/`BURN_WINDOW` = RULES §3.5 base × tier `step_time_multiplier`, jittered ±`step_time_jitter` via `rng_timers`, baked into the spec. The engine reads cook/burn from the spec. `BURN_WINDOW`≡`burn_grace`.

**Value:** `base_value = V0 + V1·n_steps + V2·n_steps²` (V0=6,V1=2,V2=0.5; SCORING §2/§4.1).

## (c) Deadlines (feasibility-anchored — the speed-accuracy crux)
Per order, the reference scheduler (§e) computes the **critical-path completion time** `C_o` given THIS grid (travel = path length × `MOVE_GS_PER_STEP`, action durations, **parallel walk-away cooking** up to `num_burners`, single-agent serialization of non-cook steps). Then:
```
L_o = ceil( C_o · slack_factor );  deadline_gs = arrival_gs + L_o
```
**This is the only slack model** (resolves the three-way slack conflict). `slack_factor` per tier:
| Tier | TUTORIAL | EASY | MEDIUM | HARD | NIGHTMARE |
|---|---|---|---|---|---|
| slack_factor | 2.5 | 2.0 | **1.6** | 1.35 | 1.2 |
| slack_jitter | 0 | 0.1 | 0.1 | 0.1 | 0.1 |

Seeded jitter ±slack_jitter on each order (via `rng_jitter`); deadline-relaxation during feasibility retries recomputes as `L_o = ceil(C_o · slack_factor · 1.05^n)` (single integer ceil each retry — deterministic, no compounding-float ambiguity). **All deadlines are clamped ≤ horizon** (RULES §13.1) so no order is cut off mid-life; arrivals whose `arrival + L_o` would exceed horizon are dropped from the stream.

## (d) Splits (AppWorld-style hold-out, residue-class banding)
```
band(seed) = seed mod 1000
  TRAIN     [0,599]    60%  public — tuning
  DEV       [600,799]  20%  public — iteration
  TEST      [800,949]  15%  public seeds; official run = a locked, hashed manifest (TEST_v1)
  CHALLENGE [950,999]   5%  HELD OUT (seeds undisclosed, harder θ) — maintainer-run headline + tie-break
```
Residue-class banding keeps every band an i.i.d. sample of the whole seed space (TRAIN/TEST share distribution; the split tests generalization, not shift). `split_of(seed)` and `assert_eval_legal(seed)` are one-liners anyone can audit. The locked TEST manifest is `{tier × 50 seeds}`, hash published; rotated yearly (`TEST_v1, TEST_v2, …`) from fresh TEST sub-ranges so memorizing a manifest decays. **Headline RTTC is computed on the hidden CHALLENGE band** (run by maintainers) plus a canary GUID, so public-seed determinism cannot be gamed by regenerating test specs. Each `(seed,tier)` is run `k=4` trials (Pass^k, SCORING §6.2). `GenerationError` (extremely rare) deterministically excludes a seed from a split (logged), keeping splits reproducible.

## (e) Reference scheduler / oracle (feasibility + normalizer)
A deterministic, **zero-latency**, version-pinned **greedy-EDF** planner — the best a perfect *instantaneous* agent could do (the latency-free ceiling). It is a strong reference upper bound, NOT provably optimal (job-shop with travel+deadlines is NP-hard); `η` is therefore **clamped at 1.0** (SCORING §5) and called the oracle-relative score — we never claim 100 is information-theoretically unreachable.

**Policy** at each decision point, among legal actions (station-gated, burner-aware, recipe-ordered): (1) EDF on the owning order; (2) tie-break: unblocks the most downstream steps; (3) tie-break: nearest station by Manhattan path; (4) never lets food burn (collects within `[ready, burn)`). Cook timers advance in parallel on the global clock.

```text
generate_spec(seed, tier) -> KitchenSpec:
    ss=SeedSequence(seed); rng_grid,rng_orders,rng_timers,rng_jitter = spawn(4); θ=TIERS[tier]
    grid = build_grid(rng_grid, θ)  with reachability+balance+spawn-mobility (200 attempts → OPEN)
    orders = sample_orders(rng_orders, rng_timers, rng_jitter, θ, grid)
    assign_deadlines(orders, grid, θ)                  # via reference C_o, clamp ≤ horizon
    oracle = reference_play(spec)                       # zero-latency greedy-EDF
    assert hand_feasible(orders, HAND_SLOTS=4)         # every recipe completable hands-only
    if oracle.served_fraction < θ.feasible_fraction:   # 1.0 TUT/EASY, 0.95 MED, 0.9 HARD/NIGHT
        relax deadlines (×1.05^n, bounded) and re-check; else GenerationError(seed,tier)
    # per-tier latency-sensitivity guard:
    s0 = reference_play(spec, latency=0).score
    sref = reference_play(spec, latency=ref_median).score
    assert (s0 - sref) >= θ.min_latency_sensitivity  OR  tier in {TUTORIAL, EASY}   # warmups exempt
    return KitchenSpec(..., oracle_score=oracle.score, oracle_served=…, null_score=do-nothing-no-penalty=0.0)
```
`null_score` is the do-nothing-but-no-penalty baseline (0.0), used only for context; normalization divides by `oracle_score` alone (no floor subtraction — SCORING §5).

## (f) KitchenSpec (canonical artifact)
```jsonc
{"generator_version":"krgen-1.0.0","seed":824173,"tier":"HARD","split":"test",
 "grid":{"n":8,"archetype":"CORRIDOR","cells":"<row-major FLOOR/WALL>",
         "stations":[{"id":"stove_0","type":"STOVE","cell":[0,3],"access":[[1,3]],"burner_index":0}, "..."],
         "spawn":[4,4]},
 "orders":[{"id":"O1","arrival_gs":0,"deadline_gs":54,"dish":"veggie_ramen","base_value":64.5,
            "recipe_timers":{"broth_base":{"cook_time":12.4,"burn_window":8.1},"noodles":{"cook_time":6.2,"burn_window":5.0}, "..."},
            "critical_path_gs":40}, "..."],
 "theta":{/* DifficultyVector */},
 "oracle_score":312.0,"oracle_served_on_time":11,"null_score":0.0,"horizon_gs":210.0}
```

## (g) Difficulty vector θ (per-tier presets)
| Param | TUT | EASY | MED | HARD | NIGHT |
|---|---|---|---|---|---|
| grid_size n | 5 | 6 | 7 | 8 | 9 |
| archetypes | OPEN | OPEN | OPEN,ISLAND | ISLAND,CORRIDOR | CORRIDOR |
| num_burners | 2 | 2 | 2 | 3 | 3 |
| num_boards | 1 | 1 | 1 | 2 | 2 |
| horizon_gs | 120 | 150 | 180 | 210 | 240 |
| λ_base (orders/min) | 1.0 | 1.5 | 2.5 | 3.5 | 4.5 |
| complexity_mix {simple,hard} | {1,0} | {.85,.15} | {.65,.35} | {.5,.5} | {.4,.6} |
| backlog_cap B | 2 | 2 | 3 | 4 | 5 |
| slack_factor | 2.5 | 2.0 | 1.6 | 1.35 | 1.2 |
| step_time_multiplier | 1.0 | 1.0 | 1.0 | 1.0 | 1.1 |
| step_time_jitter | 0 | 0.1 | 0.15 | 0.15 | 0.15 |
| feasible_fraction | 1.0 | 1.0 | 0.95 | 0.9 | 0.9 |
| show_ready_actions | true | true | true | false | false |
| latency-graded? | no (warmup) | partial | yes | yes | yes |

Headline analysis sweeps `slack_factor` per model to plot each model's speed-accuracy frontier; the MEDIUM preset (slack 1.6) is the canonical default.
