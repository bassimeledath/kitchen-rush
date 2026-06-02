# Kitchen Rush — RULES.md (v2, authoritative)

> **Status:** Normative game specification. Kitchen Rush is a deterministic discrete-event simulation. All numeric values are the canonical defaults from §16 (mirrored in `config/constants.py`); SCORING.md owns the scoring *formulas* and cites these same constants. Language is MUST / MUST NOT / SHALL. Time is in **game-seconds (gs)**, a **float** quantity (see §3.1).

---

## 1. Scope & invariants

1.1. Single-agent, text-to-text. One chef agent (the model) issues native tool calls; the engine returns observations. No human, no voice.

1.2. Discrete-event simulation on a **continuous float game-clock**. State changes only at events.

1.3. **Latency is load-bearing.** Per-response thinking time converts to game-seconds and advances the clock *before* the action resolves (§3.2). A slow model loses world-time, burning food and expiring orders.

1.4. **Score accrual, not win/lose.** A run yields a scalar `score` (may be negative pre-clamp; reported raw for ranking, clamped ≥0 only for display) plus diagnostic counters (§10).

1.5. **Total determinism.** Given `(seed, tier, action_sequence, latency_sequence)` the trajectory, score, and event log are bit-reproducible across machines/Python versions (§11).

1.6. Every entity, action, timer, and transition maps to an explicit field/rule here. There MUST be no unspecified behavior.

---

## 2. Entities

### 2.1 Grid & coordinates
- Kitchen is an `N×N` grid; `N = GRID_N` (default 7; procgen may vary per tier, §PROCEDURAL).
- Cell `(row, col)`, zero-indexed, `row` increases **south**, `col` increases **east**; `(0,0)` is north-west.
- Directions (canonical tokens, full words): `north`(-1,0), `south`(+1,0), `east`(0,+1), `west`(0,-1).
- A cell is **FLOOR** (walkable) or **STATION** (occupied, not walkable; chef stands adjacent). Border is implicit WALL.
- The chef occupies exactly one FLOOR cell (`chef_pos`).
- A station at cell `s` is **operable** from `p` iff `p` is 4-adjacent (Manhattan distance 1) to `s`. The chef MUST NOT stand on a station cell. The FLOOR cells 4-adjacent to a station are its **access cells**.

### 2.2 Station types
| Station | Symbol | Purpose | Capacity |
|---|---|---|---|
| Dispenser | `ING:<ingredient>` | source of one raw ingredient | ∞ |
| Cutting board | `BOARD` | `chop` | 1 in progress |
| Stove/burner | `STOVE` | `cook` (timed, burns) | 1 job per STOVE cell |
| Plating counter | `PLATE` | `plate` | 1 in progress |
| Pass / serving | `PASS` | `serve` | ∞ |
| Bin | `BIN` | `discard` | ∞ |

There MUST be ≥1 of each of `BOARD, PLATE, PASS, BIN`, one `ING` per required ingredient, and `BURNER_COUNT` STOVE cells (= number of placed STOVE cells; default 2). `BURNER_COUNT` is the cooking-concurrency cap (this replaces the legacy `MAX_ACTIVE_COOKS`).

### 2.3 Ingredients & states
Catalog: `patty, bun, lettuce, tomato, onion, cheese, broth_base, mushroom, noodles, egg`.
Ingredient state within a held item: `RAW → CHOPPED → COOKED` (plus terminal `BURNED`). Not all states apply to all ingredients; the recipe defines the required terminal state.

### 2.4 Recipes (concrete catalog)
A recipe is a partially-ordered bill of steps producing a named **dish**. Step verbs: `collect` (ING), `chop`/`prep` (BOARD), `cook` (STOVE, timed), `plate` (PLATE). The **active recipe set** is seeded (default all five).

**R1 burger** — collect patty; collect bun; cook patty; plate{bun, cooked_patty}.
**R2 soup** — collect broth_base; cook broth_base; plate{cooked_broth}.
**R3 salad** — collect lettuce; collect tomato; chop lettuce; chop tomato; plate{chopped_lettuce, chopped_tomato}.
**R4 mushroom_cheeseburger** — collect patty, bun, mushroom, cheese; chop mushroom; cook patty; cook mushroom_chopped; plate{bun, cooked_patty, cooked_mushroom, cheese}.
**R5 veggie_ramen** — collect noodles, broth_base, egg, onion; chop onion; cook broth_base; cook noodles; cook egg; plate{cooked_broth, cooked_noodles, cooked_egg, chopped_onion}.

2.4.1 **Ordering** is enforced by preconditions (§5): `chop`/`cook` require the named ingredient held in the correct prior state; `plate` requires all named components present in terminal state. `collect` for distinct ingredients may occur in any order. Cooking a non-cookable or chopping a non-choppable ingredient is invalid (§5.9).

2.4.2 **Hand-capacity feasibility (normative guarantee).** With `HAND_SLOTS=4` and cook items occupying burners (not hands), every recipe R1–R5 is completable hands-only. Procgen MUST assert this per instance via the oracle (§PROCEDURAL): the maximum simultaneous in-hand component count for any recipe is ≤4. (R5 worst case: at plate time the chef collects/cooks ingredients incrementally — cooked items return from the burner one at a time; the engine verifies `|hands| ≤ 4` at every step. R5 is completable because the three cooked items can be collected and immediately... see note.) **Engine rule:** because all components must be in hands at `plate` time, R5 (4 components) and R4 (4 components) require exactly 4 slots; the chef MUST NOT hold a plate concurrently while assembling. This is feasible: plate consumes the 4 components and produces 1 plate in their place. Procgen's oracle MUST fail-generate any recipe whose terminal component count exceeds `HAND_SLOTS`.

### 2.5 Dishes & plates
- An in-progress dish is just the multiset of components in hands. A `plate` action consumes the exact required components and produces one **finished plate** (`PlatedDish(recipe)`), occupying 1 hand slot.
- **Binding is at serve time, not plate time** (a finished plate is generic for its recipe). This eliminates stranded-plate/re-bind edge cases. `plate` takes only `recipe`; `serve` takes only `order_id`.

### 2.6 Orders (tickets)
Fields: `order_id` (`O1…`), `dish` (one recipe per order), `arrival_gs`, `deadline_gs` (single deadline; see §3.4.4), `base_value` (§9.1), `status ∈ {PENDING → ACTIVE → SERVED | EXPIRED}`. Orders are procedurally generated (§PROCEDURAL); the full schedule is fixed at reset.

### 2.7 Chef
The chef is the sole actor: `chef_pos`, `hands`, burner references. It perceives the world only through tool-call return values (§8).

### 2.8 Hands / inventory
- `HAND_SLOTS = 4`. Each slot holds one component `(ingredient, state)` or one finished plate.
- Exceeding capacity makes the action invalid (§5.9, E08).
- `chop`/`plate` operate on held items. `cook` moves a held item onto a burner (occupies no hand slot); `collect_cooked` returns it to hands.

### 2.9 Engine state (authoritative)
```
S = (seed, tier, spec, grid,            # immutable after reset
     clock_gs: float,                    # monotonic non-decreasing
     chef_pos, hands,                    # |hands| ≤ HAND_SLOTS
     burners,                            # list[None | CookJob(item, start, ready_gs, burn_gs)]
     orders, combo_count, score: float,
     counters, events, terminated)
```
There MUST be no hidden state outside `S`.

---

## 3. Time model

### 3.1 Units
- **Game-second (gs):** `float` simulation clock; `clock_gs` starts at 0.0, monotonic non-decreasing.
- **Real-millisecond (ms):** wall-clock latency measured by the harness per response.

### 3.2 Latency → game-time (THE core mechanic; single definition)
3.2.1 Per turn the harness produces `latency_seconds` (RT: measured wall-clock of the successful attempt; RP: token-proxy `β₀ + β_in·n_in + β_out·n_out`, §SCORING §1.2).
3.2.2 The engine converts via the single canonical function (`engine/clock.py`):
```
think_gs = LATENCY_SCALE * latency_seconds      # LATENCY_SCALE = 1.0; float; NO ceil/round
```
There is exactly one constant (`LATENCY_SCALE`) and one function. `MS_PER_GS`, `α`-as-converter, and `time_scale` are NOT used.

3.2.3 **Within-turn order (normative):**
1. Advance clock by `think_gs`, running the event sweep (§3.4, §11.5) over the crossed interval.
2. For each chained call in order: validate against current state; if valid, advance clock by the action's intrinsic duration (running the sweep), then apply the effect; if invalid, charge `INVALID_GS` + `INVALID_PENALTY`, halt the chain (fail-fast-commit, §4.6).
3. Return the observation reflecting post-turn state.

3.2.4 For chained calls, `think_gs` is charged **once** for the whole turn, independent of chain length. Each call then charges its own intrinsic duration.

### 3.3 Intrinsic action durations (gs)
| Action | Constant | Default |
|---|---|---|
| `move` per step | `MOVE_GS_PER_STEP` | 1.0 |
| `collect` | `COLLECT_GS` | 2.0 |
| `chop`/`prep` | `CHOP_GS`/`PREP_GS` | 4.0 |
| start `cook` | `COOK_START_GS` | 2.0 |
| `collect_cooked` | `COOK_PICKUP_GS` | 1.0 |
| `plate` | `PLATE_GS` | 5.0 |
| `serve` | `SERVE_GS` | 3.0 |
| `discard` | `DISCARD_GS` | 1.0 |
| `observe` | `OBSERVE_GS` | 1.0 |
| invalid action | `INVALID_GS` | 3.0 |

### 3.4 Cooking, burning, expiry timers
3.4.1 On `cook` at clock `t`: `ready_gs = t + COOK_TIME[ingredient]`, `burn_gs = ready_gs + BURN_WINDOW[ingredient]`. (Per-instance jittered values come from the `KitchenSpec`; §3.5 lists base values.)
3.4.2 Cook-job state: **COOKING** (`clock < ready_gs`), **READY** (`ready_gs ≤ clock < burn_gs`, collectible as COOKED), **BURNED** (`clock ≥ burn_gs`, ruined).
3.4.3 The READY window `[ready_gs, burn_gs)` is half-open and is the speed-accuracy crux.
3.4.4 **One deadline per order.** An ACTIVE order with `clock_gs > deadline_gs` transitions to EXPIRED. Serving is allowed up to and including `deadline_gs` (inclusive); value at the deadline is `FLOOR_FACTOR` (§9.3). There is no separate soft/hard deadline.
3.4.5 **Boundary conventions:** order fulfillable while `clock_gs ≤ deadline_gs`; cook ready at `clock == ready_gs` (inclusive); burned at `clock == burn_gs` (inclusive). All comparisons are evaluated on the float clock.

### 3.5 Base timer constants (jittered per-instance by procgen)
| Ingredient | `COOK_TIME` (gs) | `BURN_WINDOW` (gs) |
|---|---|---|
| patty | 8 | 6 |
| broth_base | 12 | 8 |
| mushroom_chopped | 5 | 5 |
| noodles | 6 | 5 |
| egg | 4 | 3 |

Procgen scales these by the tier `step_time_multiplier` and jitters ±`step_time_jitter` (deterministic, seeded), then bakes the resulting `cook_time`/`burn_window` into each order's recipe in the `KitchenSpec`. The **engine reads cook/burn from the spec, not from module constants** (the constants above are the base inputs to generation). `BURN_WINDOW` and procgen's `burn_grace` are the same named quantity.

| Timer | Constant | Default |
|---|---|---|
| Episode horizon | `HORIZON_GS` | 300.0 (procgen may set per tier) |
| Safety turn cap | `MAX_TURNS` | 300 |

---

## 4. Action set (overview)
| Action | Signature | Gated? | Duration |
|---|---|---|---|
| `move` | `move(direction, steps)` | no | `cells_moved × MOVE_GS_PER_STEP` |
| `observe` | `observe()` | no | `OBSERVE_GS` |
| `collect` | `collect(ingredient)` | ING:ingredient | `COLLECT_GS` |
| `chop` | `chop(ingredient)` | BOARD | `CHOP_GS` |
| `prep` | `prep(ingredient)` | BOARD | `PREP_GS` |
| `cook` | `cook(ingredient)` | STOVE | `COOK_START_GS` |
| `collect_cooked` | `collect_cooked(ingredient, burner_index?)` | STOVE | `COOK_PICKUP_GS` |
| `plate` | `plate(recipe)` | PLATE | `PLATE_GS` |
| `serve` | `serve(order_id)` | PASS | `SERVE_GS` |
| `discard` | `discard(item)` | BIN | `DISCARD_GS` |

4.1 **Station-gating (universal).** A gated action is invalid (§5.9) unless `chef_pos` is an access cell of a matching station (typed `ING:<ingredient>` must match). `move`/`observe` are never gated.

4.2 **`move(direction, steps)`** — `direction ∈ {north,south,east,west}`, `steps ∈ [1, MAX_STEPS_PER_MOVE]` (default max 8; schema hard-cap 12). The chef walks cell-by-cell in a straight line, **stopping at the first WALL/STATION** (no auto-turn).

4.3 **Overshoot/wall rule.** If `steps` exceeds available floor, the chef stops at the last legal FLOOR cell — a **partial move**, NOT invalid, `counters.overshoot += 1`. Clock charges `cells_actually_moved × MOVE_GS_PER_STEP`. Moving 0 net cells (immediately blocked) IS invalid (E02). (Schema-out-of-range `steps` are coerced: `<1→1`, `>12→12`; over-`MAX_STEPS_PER_MOVE` clamped with `clamped:true`, not a mistake.)

4.4 **`observe()`** returns the full observation (§8) and costs `OBSERVE_GS` (always; no free quota). Since a full observation is returned after every turn, `observe` is rarely needed. Over-use is tracked (`counters.observe_calls`) but not separately penalized.

4.5 **`discard(item)`** removes one held component or plate into BIN. Required to clear BURNED items (free) or full hands. Discarding a non-burned recipe-needed item costs `DROP_PENALTY` (§9.6).

4.6 **Chained tool calls.** A turn MAY contain an ordered list of ≥1 calls, capped at `MAX_CALLS_PER_RESPONSE` (default 6); overflow calls are dropped silently (no time, no penalty). Execution: `think_gs` charged once before call 1; each call resolves fully (validate → advance duration + event sweep → effect) before the next. **The inter-call sweep CAN self-invalidate a later call** (e.g., a soup burns between `move` and `plate`) — this is intended difficulty. **Partial failure = fail-fast-with-commit:** on the first invalid call `k`, calls `1..k-1` persist (no rollback), call `k` charges `INVALID_GS`+`INVALID_PENALTY` and breaks combo (§9.7), calls `k+1..` are **aborted** (not executed, cost zero time, not counted as mistakes). The observation reports `failed_at_index`.

---

## 5. Preconditions & effects (normative)
Notation: `near(T)` = chef on an access cell of station type T; `hold(x)` = x in hands.

5.1 `move(d,k)` — MUST: `d∈{north,south,east,west}`, `1≤k≤MAX_STEPS_PER_MOVE` (post-coercion). Effect: advance up to k legal cells, stop at obstacle; clock += `cells_moved × MOVE_GS_PER_STEP`. `cells_moved==0` → invalid (E02).

5.2 `observe()` — no precondition. Effect: clock += `OBSERVE_GS`; `counters.observe_calls += 1`; return §8.

5.3 `collect(i)` — MUST: `near(ING:i)`, `|hands|<HAND_SLOTS`, `i ∈ active set`. Effect: hands ⊕ `(i, RAW)`; clock += `COLLECT_GS`.

5.4 `chop(i)`/`prep(i)` — MUST: `near(BOARD)`, `hold((i, RAW))`, `i` choppable/prep-able. Effect: `(i,RAW)→(i,CHOPPED)`; clock += `CHOP_GS`/`PREP_GS`.

5.5 `cook(i)` — MUST: `near(STOVE)`, a free burner exists, `hold((i, pre_state))` (recipe-required RAW or CHOPPED), `i` cookable. Effect: hands ⊖ item; occupy free burner with `CookJob`; clock += `COOK_START_GS`.

5.6 `collect_cooked(i, burner_index?)` — MUST: `near(STOVE)`, a burner here holds a READY|BURNED job for `i` (if two burners hold the same `i`, `burner_index` disambiguates; omitted → lowest-index matching), `|hands|<HAND_SLOTS`. Effect: free burner; if READY → hands ⊕ `(i, COOKED)`; if BURNED → hands ⊕ `(i, BURNED)` (ruined; burn penalty already charged at `burn_gs`, §9.4). Collecting a COOKING burner is invalid (E05). clock += `COOK_PICKUP_GS`.

5.7 `plate(recipe)` — MUST: `near(PLATE)`, `hands` contain **exactly** the recipe's required terminal components (no missing/extra/wrong-state/BURNED), and slot arithmetic holds (components consumed, 1 plate added). Effect: hands ⊖ components; hands ⊕ `PlatedDish(recipe)`; clock += `PLATE_GS`. Any deviation → invalid (E06). **Plating is all-or-nothing; there is no partial plate** (quality `q ∈ {0,1}`, §SCORING; a plate is always q=1 because BURNED components are rejected here).

5.8 `serve(order_id)` — MUST: `near(PASS)`, `hold(PlatedDish(recipe))` with `recipe == orders[order_id].dish`, `orders[order_id].status == ACTIVE`. Effect: hands ⊖ plate; order → SERVED; score += earned (§9.2); combo update (§9.7); clock += `SERVE_GS`. Wrong dish or non-ACTIVE order → invalid, **plate retained** (E09, E10).

5.9 **Invalid action (universal).** Any unmet precondition: no state effect except clock += `INVALID_GS`, `counters.invalid_actions += 1`, score += `INVALID_PENALTY` (§9.6), combo → 0 (§9.7), and an `invalid` event. Position/hands/burners/orders unchanged. Invalid actions MUST NOT terminate the episode.

---

## 6. Cooking subsystem
6.1 Burners = STOVE cells reachable from `chef_pos`; each STOVE cell is an independent burner. `BURNER_COUNT` = number of STOVE cells.
6.2 `cook` requires a free burner; all busy → invalid (E08-cook).
6.3 **Walk-away/parallel cooking (frozen).** Cook timers advance only on the global clock; the chef is free to leave while an item cooks. Readiness is observable only through observations (never inferred from turn count). Procgen's oracle uses this same parallel model for deadline calibration.
6.4 Item flow: `cook` moves hands→burner; `collect_cooked` moves burner→hands. On a burner an item occupies no hand slot.
6.5 **Auto-burn** is passive: at `clock ≥ burn_gs` the item becomes BURNED with no action, charging `BURN_PENALTY` and resetting combo (§9.4, §9.7). Simultaneous cook completions on the same tick are emitted in ascending `burner_index` order (§11.5).

---

## 7. Serving & order matching
7.1 `serve(order_id)` succeeds iff held plate's recipe == order's dish and order ACTIVE.
7.2 Wrong-dish serve → invalid (E09); plate retained.
7.3 Serve to EXPIRED/SERVED order → invalid (E10); plate retained.
7.4 **Bind-at-serve:** a finished plate is generic for its recipe; it may be served to any ACTIVE order of matching dish. Two ACTIVE orders for the same dish (E15) each need a separate plate; the model chooses which `order_id` to credit at serve time.

---

## 8. Observations (return schema)
Every turn returns the **full** observation (no partial/withheld-map mode in v1 — full-state-every-turn is the policy, eliminating book-keeping noise that would confound the tool-calling signal):
```json
{
  "ok": true, "clock_gs": 142.4, "horizon_gs": 300.0, "remaining_gs": 157.6,
  "chef_pos": [3,4],
  "grid_ascii": "....(BALROG-style render, see MOVEMENT.md)....",
  "hands": [{"ingredient":"patty","state":"COOKED"}], "hand_slots_free": 3,
  "stations": [{"station_id":"stove_0","type":"STOVE","cell":[2,5]}, "...all stations..."],
  "burners": [{"burner_index":0,"cell":[2,5],"status":"READY","ingredient":"patty","ready_gs":138.0,"burn_gs":144.0},
              {"burner_index":1,"cell":[2,6],"status":"FREE"}],
  "burner_summary": {"active":1,"max":2},
  "orders": [{"order_id":"O4","dish":"soup","status":"ACTIVE","deadline_gs":160.0,"gs_remaining":17.6,"base_value":22}],
  "last_turn": {"think_gs":0.74,"calls":[{"i":0,"call":"serve(O3)","ok":true,"note":"served O3 (burger) +31"}],
                "aborted_calls":[], "failed_at_index": null},
  "events_since_last": [{"type":"cook_ready","clock_gs":138.0,"detail":{"burner_index":0}}],
  "score": 214.0, "combo_count": 3,
  "ready_actions": ["plate(soup)","serve(O4)"],
  "last_invalid_reason": null, "terminated": false
}
```
8.1 The observation reflects post-turn state. Arrivals/burns/expiries that fired appear in `events_since_last`.
8.2 The full station map (positions) is provided every turn. `ready_actions` is a tunable difficulty aid (`SHOW_READY_ACTIONS`, default true; false on the hardest tier).

---

## 9. Scoring (events & values; SCORING.md owns the formulas — values are identical)
Score is a `float` accumulator; the single rounding rule (`floor(x+0.5)`) is applied once per serve (§11.6). Final reported score is raw (ranking) and `max(0, score)` (display).

### 9.1 Base value (superlinear in steps, prevents cheap-dish farming)
```
base_value(recipe) = V0 + V1·n_steps + V2·n_steps²    # V0=6, V1=2, V2=0.5
```
Yields: salad (5 steps) → 6+10+12.5 = 28.5→ rounded display 29; burger (4) → 6+8+8 = 22; soup (3) → 6+6+4.5 = 16.5→17; mushroom_cheeseburger (8) → 6+16+32 = 54; veggie_ramen (9) → 6+18+40.5 = 64.5→65. (Stored as float `base_value`; superlinearity guarantees hard dishes have higher points-per-time potential even at the combo cap — verified in `test_scoring.py`.)

### 9.2 Delivery reward
On `serve` at `clock_gs = t`:
```
earned = floor( base_value · time_factor(t) · combo_multiplier · q + 0.5 )    # q ∈ {0,1}, always 1 for a valid plate
score += earned
```
Multiplication is left-to-right in float64; `floor(x+0.5)` (round-half-up; NOT Python `round`/banker's) applied once.

### 9.3 Time-decay `time_factor` (linear, NO grace plateau)
Let `a=arrival_gs`, `d=deadline_gs`, `L=d-a>0`:
```
time_factor(t) = clamp( 1.0 - DECAY_RATE · (t - a)/L , FLOOR_FACTOR , 1.0 )
```
`DECAY_RATE = 0.6`, `FLOOR_FACTOR = 0.4`. Serving at arrival → 1.0; at deadline → 0.4. **The derivative dP/dt is strictly negative on the entire `[a,d]` interval** (no free window), so latency always costs points — the benchmark thesis holds by construction for every speed regime (SCORING §3).

### 9.4 Burn penalty
On a cook auto-transitioning to BURNED: `score += BURN_PENALTY` (−8); `counters.burns += 1`; combo → 0. Later discard of the burned item is free (E07).

### 9.5 Expiry penalty (value-scaled, NOT flat)
On an ACTIVE order → EXPIRED: `score += -(EXPIRY_FRACTION · base_value)` with `EXPIRY_FRACTION = 0.5`; `counters.expiries += 1`; combo → 0. (Value-scaled so cheap orders aren't over-punished and the invariant "finishing before deadline always beats expiry" holds for every value tier: a near-deadline serve pays ≥`FLOOR_FACTOR·base_value = 0.4·base_value` vs `−0.5·base_value` for expiry — a swing ≥0.9·base_value.)

### 9.6 Invalid & drop penalties
| Event | Constant | Default |
|---|---|---|
| Invalid action (any §5.9) | `INVALID_PENALTY` | −5 |
| Discard of needed non-burned item | `DROP_PENALTY` | −6 |
| Discard of burned item | — | 0 |

### 9.7 Combo / tip multiplier (complexity-gated; strict)
9.7.1 The streak `s` counts **consecutive on-time, clean serves**. A serve advances the streak iff it is **on-time** (`t ≤ deadline_gs`) AND uses no BURNED component (guaranteed by §5.7). **Combo resets to 0** on: an expiry, a burn, OR any invalid action (probing is expensive — adopting the strict rule).
9.7.2 **Anti-farming gate:** only serves of dishes with `n_steps ≥ COMBO_MIN_STEPS` (default 4 → burger/R4/R5; salad and soup do NOT advance the streak past 1) increment `s`. Cheap-dish spamming cannot build the multiplier.
9.7.3
```
combo_multiplier = min( COMBO_CAP , 1.0 + COMBO_STEP · max(0, s-1) )
```
`COMBO_STEP = 0.25`, `COMBO_CAP = 2.0` (cap reached at s=5, matching Overcooked's 2× tip). Combined with §9.1 superlinearity and §9.7.2 gating, `points_per_gs(hard) > points_per_gs(easy)` at the cap (verified in `test_scoring.py`).

### 9.8 No win/loss
No win flag. Reporting presents raw `score`, clamped display score, all counters, and the latency-normalized RTTC (SCORING §6).

---

## 10. Diagnostic counters
`serves_ok, invalid_actions, burns, expiries, drops, overshoot, observe_calls, total_tool_calls, chained_turns, chain_partial_failures, total_think_gs, total_action_gs, idle_gs, timeouts, empty_turns, max_combo, orders_total, orders_served, orders_expired`.

---

## 11. Determinism guarantees
11.1 **Contract.** Given `(seed, tier, sequence_of(action|chain), sequence_of(latency_seconds))` the engine produces identical `S`, `score`, event log, and per-turn observations everywhere. No wall-clock, no unseeded RNG, no reliance on `set`/insertion-`dict` iteration for normative behavior.
11.2 **Seeded generation** uses `numpy.random.SeedSequence(seed).spawn(4)` → `(rng_grid, rng_orders, rng_timers, rng_jitter)` (see PROCEDURAL). Each generation attempt draws a fixed, documented number of values up front then validates, so sub-stream position is implementation-independent. The single-`Generator` model is NOT used.
11.3 **Seeded order stream** is computed at reset and fixed for the episode; only fulfillment depends on agent choices.
11.4 **Latency replay.** `think_gs` is a pure function of `latency_seconds` (§3.2.2). RT replays from logged `wall_ms`; RP replays from recomputed token counts (pinned tokenizer, §SCORING). Both are pure and fully specified. Tests MAY inject a fixed latency trace (e.g., all-zero) to isolate decision quality.
11.5 **Tie-break (fixed priority).** When multiple passive events fall in the same advance, resolve in order: **(1) order expiries, (2) cook burns, (3) order arrivals, (4) the action effect.** Within a category, sort by ascending id (`order_id` lexicographic for orders, `burner_index` for cooks). This applies at every clock advance, including each intra-chain advance.
11.6 **Score arithmetic.** Clock is float64. The ONLY rounding in the score path: `earned = floor(base_value·time_factor·combo·q + 0.5)`, computed left-to-right in float64, once per serve. Python `round()`/banker's rounding is FORBIDDEN. Penalties (`BURN_PENALTY`, `EXPIRY_FRACTION·base_value`, `INVALID_PENALTY`, `DROP_PENALTY`) are exact; expiry is `floor(EXPIRY_FRACTION·base_value + 0.5)` as a magnitude. Normalized/RTTC display metrics are reported to 4 decimal places; leaderboard ties break on the next reported metric, never on float noise.
11.7 **Versions** `RULESET_VERSION` (hash of constants+recipes+scoring+tiers), `SCHEMA_VERSION`, `GENERATOR_VERSION` stamp every output and are validator-checked.

---

## 12. State-transition table
`clock += think_gs` + the §11.5 sweep precede every turn (once); each chained call then runs validate→advance(duration)+sweep→effect. Invalid rows apply §5.9 uniformly.

| Action | Guard | clock += | Mutation |
|---|---|---|---|
| `move(d,k)` | `d∈{4 dirs}`,`1≤k≤MAX_STEPS_PER_MOVE`,`cells_moved>0` | `cells_moved·MOVE_GS_PER_STEP` | `chef_pos ← last legal cell` |
| `observe()` | — | `OBSERVE_GS` | `observe_calls += 1` |
| `collect(i)` | `near(ING:i)`,`|hands|<4`,`i∈active` | `COLLECT_GS` | `hands ⊕ (i,RAW)` |
| `chop/prep(i)` | `near(BOARD)`,`hold(i,RAW)`,valid verb | `CHOP_GS`/`PREP_GS` | `(i,RAW)→(i,CHOPPED)` |
| `cook(i)` | `near(STOVE)`,free burner,`hold(i,pre)` ,cookable | `COOK_START_GS` | hands⊖item; burner←CookJob |
| `collect_cooked(i,b?)` | `near(STOVE)`,burner READY|BURNED,`|hands|<4` | `COOK_PICKUP_GS` | burner→FREE; hands ⊕ (i,COOKED|BURNED) |
| `plate(r)` | `near(PLATE)`,hands==exact terminal comps,no BURNED | `PLATE_GS` | hands ⊖ comps; hands ⊕ PlatedDish(r) |
| `serve(o)` | `near(PASS)`,`hold(PlatedDish(o.dish))`,`o` ACTIVE | `SERVE_GS` | hands⊖plate; o→SERVED; score+=earned; combo update |
| `discard(x)` | `near(BIN)`,`hold(x)` | `DISCARD_GS` | hands⊖x; if needed&¬burned: score+=DROP_PENALTY |
| *invalid* | precondition unmet | `INVALID_GS` | score+=INVALID_PENALTY; invalid_actions+=1; combo←0 |

Passive (clock sweep, §11.5 priority):
| Trigger | Condition | Effect |
|---|---|---|
| Expiry | ACTIVE ∧ `clock>deadline_gs` | EXPIRED; score+=−floor(0.5·base_value+0.5); combo←0; expiries+=1 |
| Burn | job `clock≥burn_gs ∧ status≠BURNED` | BURNED; score+=BURN_PENALTY; combo←0; burns+=1 |
| Arrival | PENDING ∧ `clock≥arrival_gs` | ACTIVE; arrival event |
| Termination | §13 | terminated←True |

---

## 13. Termination
13.1 **Horizon always wins:** `clock_gs ≥ HORIZON_GS` (evaluated after the current action) terminates. Procgen guarantees every order's `deadline_gs ≤ HORIZON_GS`, so no order is ever cut off mid-life.
13.2 Early natural end: all scheduled orders are SERVED or EXPIRED and the stream is exhausted (only reachable before horizon).
13.3 `MAX_TURNS` safety cap (diagnostic; should rarely fire).
13.4 **No failure-termination.** Invalid actions, burns, expiries never end the episode early.
13.5 Orders still ACTIVE at horizon (arrived, unserved, not yet past deadline) are scored **neutral** (no expiry penalty, no reward) — the chef ran out of game. (Because deadlines ≤ horizon, an ACTIVE order at horizon means deadline == horizon exactly.)
13.6 On termination, emit `final_report` with raw + clamped score, all counters, full event log, latency trace.
13.7 **Timeout / empty turn.** If the model fails to respond within `timeout_s`: charge `think_gs = LATENCY_SCALE·timeout_s`, apply NO action, `counters.timeouts += 1`, combo unaffected (no action was taken), run the event sweep. A response with zero tool calls (pure text): charge `think_gs` only, `counters.empty_turns += 1`, no action, combo unaffected. Both are **stalls**, not invalid actions. A malformed tool call (bad JSON/args) IS invalid (§5.9, E18).

---

## 14. Edge-case table (normative)
| # | Situation | Behavior |
|---|---|---|
| E01 | Move overshoots into wall | stop at last legal floor; partial; charge cells_moved; overshoot+1; not invalid |
| E02 | Move blocked immediately (0 cells) | invalid: INVALID_PENALTY+INVALID_GS, no move |
| E03 | Move overshoots a target's access cell | as E01; later station action invalid until repositioned |
| E04 | Act at wrong station | invalid (gate fails) |
| E05 | `collect_cooked` while COOKING | invalid; burner unchanged |
| E06 | `plate` missing/extra/wrong-state/burned comps | invalid; hands unchanged |
| E07 | BURNED item collected | enters hands as BURNED; cannot plate; discard free; burn already charged |
| E08 | Hands full on collect / all burners busy on cook | invalid (capacity) |
| E09 | Serve wrong dish | invalid; plate retained |
| E10 | Serve to SERVED/EXPIRED order | invalid; plate retained |
| E11 | (removed — bind-at-serve eliminates plate-binding) | n/a |
| E12 | Serve exactly at `clock==deadline_gs` | valid; pays `time_factor(deadline)=FLOOR_FACTOR` |
| E13 | Order expires mid-cook of its ingredient | order EXPIRED (penalty, combo reset); cook continues independently, may burn (separate penalty); item should be discarded |
| E14 | Chained partial failure | prefix persists; failing call charges invalid; suffix aborted (0 time); `failed_at_index` reported; no rollback |
| E15 | Two ACTIVE orders, same dish | independent; each needs its own plate; serve credits chosen `order_id` |
| E16 | Cook-READY and order-deadline same tick | §11.5: at `clock==deadline` order still ACTIVE (E12), ready item collectible — both succeed |
| E17 | Burn and expiry same tick | §11.5: expiry before burn; both penalties; combo reset once (idempotent) |
| E18 | Malformed action (bad arg/unknown ingredient/steps out of range/unknown tool) | invalid (§5.9); `last_invalid_reason` set |
| E19 | Action after terminated | `{"ok":false,"terminated":true}`; no clock/score change; not invalid |
| E20 | Huge latency skips multiple events | all crossed events fire in clock order with §11.5 tie-break before the action validates |
| E21 | Discard needed non-burned item | allowed; DROP_PENALTY |
| E22 | `plate` while holding an unrelated finished plate | allowed if slots suffice and comps exact (chef may hold multiple plates up to HAND_SLOTS) |
| E23 | Timeout (no response) | stall (§13.7): charge timeout, no action, timeouts+1, combo unaffected |
| E24 | Empty response (text, 0 calls) | stall: charge think_gs, no action, empty_turns+1 |
| E25 | Chef boxed in (all moves 0-cell) | each move invalid; procgen guarantees spawn has ≥1 walkable neighbor and walked-to floor cells always have a walkable entry neighbor, so this cannot occur in valid instances |

---

## 15. Native tool-call schemas
Authoritative schemas live in `tools/schemas.py` and are reproduced in MOVEMENT.md §1. RULES and MOVEMENT cite the same file; there is exactly one schema. Direction enum is `["north","south","east","west"]`. `serve` takes only `order_id`; `plate` takes only `recipe`.

---

## 16. Constant table (SINGLE SOURCE OF TRUTH; mirrors `config/constants.py`)
| Constant | Default | Role |
|---|---|---|
| `GRID_N` | 7 | grid size (procgen may vary per tier) |
| `BURNER_COUNT` | 2 | STOVE cells = cook concurrency |
| `HAND_SLOTS` | 4 | inventory capacity |
| `LATENCY_SCALE` | 1.0 | latency_seconds → gs (the one knob) |
| `MOVE_GS_PER_STEP` | 1.0 | move cost |
| `COLLECT_GS` | 2.0 | collect |
| `CHOP_GS`/`PREP_GS` | 4.0 | knife |
| `COOK_START_GS` | 2.0 | place on burner |
| `COOK_PICKUP_GS` | 1.0 | take off burner |
| `PLATE_GS` | 5.0 | plate |
| `SERVE_GS` | 3.0 | serve |
| `DISCARD_GS` | 1.0 | discard |
| `OBSERVE_GS` | 1.0 | observe |
| `INVALID_GS` | 3.0 | invalid time |
| `HORIZON_GS` | 300.0 | episode length (per-tier in procgen) |
| `MAX_TURNS` | 300 | safety cap |
| `MAX_STEPS_PER_MOVE` | 8 | single-leg cap (schema hard-cap 12) |
| `MAX_CALLS_PER_RESPONSE` | 6 | chain cap |
| `DECAY_RATE` | 0.6 | time-decay slope |
| `FLOOR_FACTOR` | 0.4 | min time factor at deadline |
| `V0`/`V1`/`V2` | 6/2/0.5 | base value `V0+V1·n+V2·n²` |
| `EXPIRY_FRACTION` | 0.5 | expiry penalty = 0.5·base_value |
| `BURN_PENALTY` | −8 | per burn |
| `INVALID_PENALTY` | −5 | per invalid |
| `DROP_PENALTY` | −6 | per bad discard |
| `COMBO_STEP` | 0.25 | combo growth |
| `COMBO_CAP` | 2.0 | combo ceiling (cap at s=5) |
| `COMBO_MIN_STEPS` | 4 | min recipe steps to advance combo |
| `SHOW_READY_ACTIONS` | true | difficulty aid (false on hardest tier) |
| `COOK_TIME[...]`/`BURN_WINDOW[...]` | §3.5 | base timers (procgen jitters per-instance) |

---

## 17. Resolved cross-section decisions (formerly open)
1. **Clock = float**, single rounding rule §11.6. (Resolves int/float contradiction.)
2. **Latency conversion** = single continuous function §3.2.2. (Resolves ceil/round/raw.)
3. **World model = hands-only, ingredient/order-keyed verbs**, `HAND_SLOTS=4`. (Resolves tool-set fork; movement's pick_up/place vocabulary is dropped.)
4. **One deadline**, value-scaled expiry. (Resolves one-vs-two deadlines.)
5. **No grace plateau**; linear decay. (Resolves the latency-thesis hole.)
6. **Combo:** strict (on-time clean only; invalid breaks it), complexity-gated, superlinear value. (Resolves combo-farming + reset-trigger conflicts.)
7. **No per-step partial credit**; `q∈{0,1}`. (Resolves partial-credit vs strict-plate.)
8. **Bind-at-serve.** (Resolves plate-binding/re-bind edge cases.)
9. **Cooking = parallel/walk-away.** (Frozen across engine/scoring/oracle.)
10. **Observe = fixed `OBSERVE_GS=1`, no free quota; full observation every turn.**
