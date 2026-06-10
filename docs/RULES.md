# Kitchen Rush — RULES.md (v2, authoritative)

> **Status:** Normative game specification. Kitchen Rush is a deterministic discrete-event simulation. All numeric values are the canonical defaults from §16 (mirrored in `src/kitchenrush/config.py`); the scoring formulas are normative in §9 (mirrored in `src/kitchenrush/scoring.py`). Language is MUST / MUST NOT / SHALL. Time is in **game-seconds (gs)**, a **float** quantity (see §3.1).
>
> **Ruleset frozen — generation 1.0** (`ruleset_hash = 33034952fa7f`, frozen 2026-06-06 after the calibration panel; see docs/CALIBRATION.md). §16 mirrors the frozen `config.py` values. Note: the RP β-coefficients are part of the hash but remain **provisional** — a future β-calibration will bump the generation, and RP stays labelled *experimental* until then.
>
> **Known limitations** (read before citing results): RP standardizes speed and does **not** credit a genuinely faster model; see docs/LIMITATIONS.md (incl. how this compares to Artificial Analysis).
>
> **Design stance — the benchmark tests action sequencing, not pathfinding.** Navigation is automatic: every station action walks the chef to the nearest appropriate station and charges the travel game-time inside the action (§4). The model never reasons about coordinates; it chooses the right ACTION SEQUENCE under latency. Consequently the kitchen layout is **deterministic** per tier (only the order stream is randomized; see `src/kitchenrush/procgen.py`).

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

### 2.1 Grid, coordinates & the walled room
- Kitchen is an `N×N` grid; `N = GRID_N` (default 7; per tier: easy/medium 7, hard 9 — §16).
- Cell `(row, col)`, zero-indexed, `row` increases **south**, `col` increases **east**; `(0,0)` is north-west.
- Directions (canonical tokens, full words): `north`(-1,0), `south`(+1,0), `east`(0,+1), `west`(0,-1). Used internally for adjacency/BFS; the model never issues directional moves (§4).
- **The kitchen is a walled room.** The grid has a non-walkable perimeter band of counters/walls with the stations embedded in it, an open interior floor, and one doorway cell. The `KitchenSpec` carries `blocked` (a `frozenset` of non-walkable counter/wall cells that are not stations) and `door` (a single decorative doorway cell in the wall).
- A cell is **FLOOR** (walkable) or non-walkable. `engine._is_floor(cell)` is true iff the cell is **in-bounds AND not a station AND not in `blocked`**. Equivalently, walkable floor = the interior cells minus any station; the entire perimeter band (empty counters, corners, the doorway) and every station cell are non-walkable.
- The chef occupies exactly one FLOOR cell (`chef_pos`).
- A station at cell `s` is **operable** from `p` iff `p` is 4-adjacent (Manhattan distance 1) to `s`. The chef MUST NOT stand on a station cell. The FLOOR cells 4-adjacent to a station are its **access cells**.
- **Travel.** Moving between FLOOR cells costs `MOVE_GS_PER_STEP` game-seconds per cell. The chef does not navigate manually; station actions auto-walk (§4) along a BFS shortest path over FLOOR cells.

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

2.4.2 **Hand-capacity feasibility (design guarantee).** With `HAND_SLOTS=4` and cook items occupying burners (not hands), every recipe R1–R5 is completable hands-only: each recipe's terminal component count is ≤4 (R4 and R5 are exactly 4). Because all components must be in hands at `plate` time, the chef MUST NOT hold a finished plate concurrently while assembling a 4-component dish; `plate` then consumes the 4 components and produces 1 plate in their place, so capacity holds. *(This is a static property of the fixed recipe catalog — verified by inspection, not enforced at generation time: the current procgen does **not** run a feasibility oracle or fail-generate, so a generation-time feasibility oracle remains aspirational, not implemented.)*

### 2.5 Dishes & plates
- An in-progress dish is just the multiset of components in hands. A `plate` action consumes the exact required components and produces one **finished plate** (`PlatedDish(recipe)`), occupying 1 hand slot.
- **Binding is at serve time, not plate time** (a finished plate is generic for its recipe). This eliminates stranded-plate/re-bind edge cases. `plate` takes only `recipe`; `serve` takes only `order_id`.

### 2.6 Orders (tickets)
Fields: `order_id` (`O1…`), `dish` (one recipe per order), `arrival_gs`, `deadline_gs` (single deadline; see §3.4.4), `base_value` (§9.1), `status ∈ {PENDING → ACTIVE → SERVED | EXPIRED}`. Orders are procedurally generated from the seeded order stream (`procgen.py`); the full schedule is fixed at reset.

2.6.1 **Progressive release (`procgen._build_orders`).** The **first 2 orders are ACTIVE at `t=0`**; each remaining order arrives later, with inter-arrival gaps drawn `Poisson` (`rng.expovariate(tier.arrival_rate)`), up to `tier.max_orders` (or the horizon). Orders are PENDING before their `arrival_gs` and become ACTIVE when the clock reaches it.

2.6.2 **Deadline pricing (single-server queue).** Deadlines are priced on a **one-chef (single-server) queue** at `B` seconds/decision (`config.B_SECONDS`, default 1.0), using each dish's critical path `C_o` (`procgen.critical_path`): for orders in arrival order, `finish = max(arrival, busy) + C_o`; `busy ← finish`; then `deadline = arrival + ceil(tier.slack · (finish − arrival))`. So an order's headroom covers its queue wait **plus** its own critical path. The horizon is extended if needed so every `deadline_gs ≤ horizon_gs` (§13.1).

### 2.7 Chef
The chef is the sole actor: `chef_pos`, `hands`, burner references. It perceives the world only through tool-call return values (§8).

### 2.8 Hands / inventory
- `HAND_SLOTS = 4`. Each slot holds one component `(ingredient, state)` or one finished plate.
- Exceeding capacity makes the action invalid (§5.9, E08).
- `chop`/`plate` operate on held items. `cook` moves a held item onto a burner (occupies no hand slot); `collect_cooked` returns it to hands.

### 2.9 Engine state (authoritative)
```
S = (spec,                               # immutable: seed, tier, grid_n, horizon_gs,
                                         #   stations, blocked, door, chef_start, orders
     stations, blocked, door,            # derived grid maps (walled room, §2.1)
     clock_gs: float,                    # monotonic non-decreasing, starts 0.0
     chef_pos, hands,                    # |hands| ≤ HAND_SLOTS
     burners,                            # list[Burner(cell, job: None | CookJob(ingredient,
                                         #   start_gs, ready_gs, burn_gs, burned))]
     orders, combo_count, score: float,
     turn_count, last_invalid_reason,
     counters, events, terminated)
```
There MUST be no hidden state outside `S`.

---

## 3. Time model

### 3.1 Units
- **Game-second (gs):** `float` simulation clock; `clock_gs` starts at 0.0, monotonic non-decreasing.
- **Real-millisecond (ms):** wall-clock latency measured by the harness per response.

### 3.2 Latency → game-time (THE core mechanic; single definition)
3.2.1 Per response the harness produces `latency_seconds` via one of two tracks:
- **RP** (reproducible; the intended ranked headline) = a token proxy `β₀ + β_in·n_in + β_out·n_out` (`RP_BETA0/RP_BETA_IN/RP_BETA_OUT`). `n_in` counts ALL model-visible request content (system prompt + observation + **tool schemas**); `n_out` counts the canonical assistant output (each tool call's **name** + arguments + any text) **plus provider-reported reasoning tokens**. Tokens use ONE pinned tokenizer applied to every model — `cl100k_base` via tiktoken (stamped `TOKENIZER_ID`), with a char/4 fallback when tiktoken is absent (stdlib-only core); the active id is recorded in every output so RP is never silently compared across tokenizers. RP is provider-independent and recomputable from a trajectory log. *(β coefficients are provisional pending a published calibration.)*
- **RT** (real; diagnostic) = measured wall-clock of the response — ecologically real but provider/region/load-dependent, so it is reported alongside, **not** the cross-model rank.

The in-process runner passes a policy-supplied `latency_s` (baselines inject a fixed value); `think_gs = LATENCY_SCALE · latency_s`.
3.2.2 The conversion is a single multiply (`runner.run_episode`):
```
think_gs = LATENCY_SCALE * latency_seconds      # LATENCY_SCALE = 1.0; float; NO ceil/round
```
There is exactly one constant (`LATENCY_SCALE`) and one conversion. `MS_PER_GS`, `α`-as-converter, and `time_scale` are NOT used.

3.2.3 **Within-turn order (normative; `engine.step`):**
1. Advance clock by `think_gs` FIRST, running the event sweep (§3.4, §11.5) over the crossed interval — so food can burn and orders expire while the model deliberates, **before** any action resolves.
2. For each chained call in order: validate against current state; if valid, auto-walk to the station then advance the clock by the action's intrinsic duration (running the sweep at each advance) and apply the effect; if invalid, charge `INVALID_GS` + `INVALID_PENALTY` and halt the chain (fail-fast-commit, §4.6).
3. Return the observation reflecting post-turn state.

3.2.4 For chained calls, `think_gs` is charged **once** for the whole turn, independent of chain length. Each call then charges its own intrinsic duration.

### 3.3 Intrinsic action durations (gs)
A station action's total game-time = its **travel cost** (`cells_walked × MOVE_GS_PER_STEP`, charged by the auto-navigation step, §4) **plus** the intrinsic duration below.

| Action | Constant | Default |
|---|---|---|
| travel per cell | `MOVE_GS_PER_STEP` | 0.15 *(provisional)* |
| `collect` | `COLLECT_GS` | 2.0 |
| `chop`/`prep` | `CHOP_GS`/`PREP_GS` | 4.0 |
| start `cook` | `COOK_START_GS` | 2.0 |
| `collect_cooked` | `COOK_PICKUP_GS` | 1.0 |
| `plate` | `PLATE_GS` | 5.0 |
| `serve` | `SERVE_GS` | 3.0 |
| `discard` | `DISCARD_GS` | 1.0 |
| `observe` (internal only) | `OBSERVE_GS` | 1.0 |
| invalid action | `INVALID_GS` | 3.0 |

### 3.4 Cooking, burning, expiry timers
3.4.1 On `cook` at clock `t`: `ready_gs = t + cook_time[ingredient]`, `burn_gs = ready_gs + burn_window[ingredient]`. The engine reads `cook_time`/`burn_window` **directly from the `config.INGREDIENTS` catalog** (`_a_cook`); §3.5 lists those values. *(There is currently no per-instance timer jitter — see §3.5.)*
3.4.2 Cook-job state: **COOKING** (`clock < ready_gs`), **READY** (`ready_gs ≤ clock < burn_gs`, collectible as COOKED), **BURNED** (`clock ≥ burn_gs`, ruined).
3.4.3 The READY window `[ready_gs, burn_gs)` is half-open and is the speed-accuracy crux.
3.4.4 **One deadline per order.** The event sweep fires an order's expiry when the clock advance reaches `deadline_gs` (`start < deadline_gs ≤ target`), transitioning ACTIVE → EXPIRED. There is no separate soft/hard deadline. During a `serve`'s own clock advance the **target order is exempted** (`advance(..., exempt_order=order_id)`), so a serve that lands exactly on the deadline can still complete at `time_factor = FLOOR_FACTOR` (§9.3); but an order whose deadline is crossed **while walking to the pass** (the walk is not exempt) expires and the post-walk re-check fails (§5.8).
3.4.5 **Boundary conventions:** cook READY at `clock ≥ ready_gs`; BURNED at `clock ≥ burn_gs` (then auto-freed, §6.5); order expiry fires at `clock = deadline_gs`. All comparisons are on the float clock.

### 3.5 Timer constants (from the `config.INGREDIENTS` catalog)
| Ingredient | `cook_time` (gs) | `burn_window` (gs) | cooks from |
|---|---|---|---|
| patty | 8 | 6 | RAW |
| broth_base | 12 | 8 | RAW |
| mushroom | 5 | 5 | CHOPPED |
| noodles | 6 | 5 | RAW |
| egg | 4 | 3 | RAW |

These are read **directly from `config.INGREDIENTS`** by the engine at `cook` time. There is **currently no per-instance jitter and no tier time-multiplier** applied to cooking timers (procgen does not modify them). The cook timers above are identical across seeds and tiers.

| Timer | Constant | Default |
|---|---|---|
| Episode horizon | `HORIZON_GS` | 300.0 (procgen sets per tier and may extend it to fit the schedule, §13.1) |
| Stall turn limit | `STALL_TURNS` | 50 (consecutive unproductive turns ends the episode, §13) |
| Safety turn cap | `MAX_TURNS` | 500 (anti-runaway ceiling; should not bind, §13) |
| Reference turn budget | `REFERENCE_MAX_TURNS` | 20000 (the scripted oracle's turns are free; game-time bounds it) |

---

## 4. Action set (overview)
**Navigation is automatic.** There is no required "move to a station" step. Each station action — `collect`, `chop`/`prep`, `cook`, `collect_cooked`, `plate`, `serve`, `discard` — first walks the chef to the nearest reachable access cell of the appropriate station (engine `_walk_to`/`_walk_to_cells`/`_nearest_access_cell`), **charging the travel game-time inside the action**, then performs the action. `move_to(row, col)` still exists as **optional** pre-positioning only (it never performs a station action). The `observe` tool is **not offered to the model** (it has been removed from `TOOL_SCHEMAS`); an `observe` action still exists internally in the engine dispatcher but is never surfaced.

| Action | Signature | Auto-walks to | Total duration |
|---|---|---|---|
| `move_to` | `move_to(row, col)` | the target cell (or the floor cell beside a station) | `dist × MOVE_GS_PER_STEP` |
| `collect` | `collect(ingredient)` | nearest `ING:<ingredient>` | travel + `COLLECT_GS` |
| `chop` | `chop(ingredient)` | nearest BOARD | travel + `CHOP_GS` |
| `prep` | `prep(ingredient)` | nearest BOARD (alias of `chop`) | travel + `PREP_GS` |
| `cook` | `cook(ingredient)` | nearest STOVE with a free burner | travel + `COOK_START_GS` |
| `collect_cooked` | `collect_cooked(ingredient, burner_index?)` | nearest STOVE holding the item | travel + `COOK_PICKUP_GS` |
| `plate` | `plate(recipe)` | nearest PLATE | travel + `PLATE_GS` |
| `serve` | `serve(order_id)` | nearest PASS | travel + `SERVE_GS` |
| `discard` | `discard(item)` | nearest BIN | travel + `DISCARD_GS` |

4.1 **Auto-navigation (universal).** Each station action selects the nearest reachable access cell over the matching station type (`ING:<ingredient>` must match the named ingredient; `cook` targets only burners that are currently free) and walks the chef there before acting. If **no** matching station is reachable, the action is invalid with category `unreachable` (§5.9, §10). Movement still costs game-time, so the layout still matters — auto-navigation only removes the brittle move→act split, not any strategic choice (which action, which order, when).

4.2 **`move_to(row, col)`** — optional pre-positioning. Walks the chef along a BFS shortest path to `(row, col)`; if the target is a station cell, it stops on the nearest reachable floor cell beside it. Cost = `dist × MOVE_GS_PER_STEP`. Off-grid target → invalid (`bad_target`); unreachable target / no reachable floor beside a station → invalid (`unreachable`); already at the target → succeeds as a no-op ("already there"). `move_to` performs no station work.

4.3 *(reserved — the legacy directional `move(direction, steps)` and its overshoot/wall rule have been removed; navigation is automatic, §4.1–4.2.)*

4.4 **`observe` is not a model tool.** Because the full observation (§8) is returned after every turn, no explicit look-around is needed. The engine retains an internal `observe` action (cost `OBSERVE_GS`, `counters.observe_calls += 1`) for completeness, but it is absent from `TOOL_SCHEMAS` and cannot be called by a model.

4.5 **`discard(item)`** removes one held component or plate into BIN (auto-walks to the bin). Discarding a non-burned recipe-needed item costs `DROP_PENALTY` (§9.6); discarding a BURNED item is free. (Note: under auto-burn, §6.5, burned cook-jobs are auto-binned at the burner, so a BURNED item rarely reaches the hands; this branch is the residual handler.)

4.6 **Chained tool calls.** A response MAY contain an ordered list of ≥1 calls, capped at `MAX_CALLS_PER_RESPONSE` (default 6); calls **beyond** the cap are **counted as overflow** (`counters.overflow_calls += len(overflow)`), not executed — they are dropped from execution but recorded, not silently ignored. Execution: `think_gs` charged once before call 1 (§3.2.4); each call resolves fully (validate → auto-walk + advance duration + event sweep → effect) before the next. **The inter-call sweep CAN self-invalidate a later call** (e.g., a soup burns between two calls) — this is intended difficulty. **Partial failure = fail-fast-with-commit:** on the first invalid call `k`, calls `1..k-1` persist (no rollback), call `k` charges `INVALID_GS`+`INVALID_PENALTY` and breaks combo (§9.7), calls `k+1..` are **aborted** (not executed, cost zero time, not counted as mistakes). The observation reports `failed_at_index`; aborted call names appear in `last_turn.aborted_calls` and overflow names in `last_turn.overflow_dropped`.

---

## 5. Preconditions & effects (normative)
Notation: `reach(T)` = a station of type T (matching the named ingredient / a free burner where required) is reachable from `chef_pos`; `hold(x)` = x in hands. Each station action **auto-walks** to its station first (§4.1): the travel game-time is charged before the intrinsic duration. "clock += action_gs" below is shorthand for "clock += travel + intrinsic".

5.1 `move_to(r,c)` — MUST: `(r,c)` in-bounds and (a target cell or a station with a reachable adjacent floor cell) reachable. Effect: walk BFS-shortest to the target (or the floor cell beside a station); clock += `dist × MOVE_GS_PER_STEP`. Off-grid → invalid (`bad_target`); unreachable → invalid (`unreachable`); already there → no-op success. Performs no station work.

5.2 `observe()` *(internal only; not a model tool, §4.4)* — no precondition. Effect: clock += `OBSERVE_GS`; `counters.observe_calls += 1`; return §8.

5.3 `collect(i)` — MUST: `i ∈ active set`, `|hands|<HAND_SLOTS`, `reach(ING:i)`. Effect: auto-walk to the dispenser; hands ⊕ `(i, RAW)`; clock += `COLLECT_GS`. (Order checked in code: bad ingredient → `bad_target`; hands full → `wrong_inventory`; no dispenser reachable → `unreachable`.)

5.4 `chop(i)`/`prep(i)` — MUST: `i` choppable, `hold((i, RAW))`, `reach(BOARD)`. Effect: auto-walk to a board; `(i,RAW)→(i,CHOPPED)`; clock += `CHOP_GS`/`PREP_GS`. (Not choppable → `bad_target`; not holding raw `i` → `wrong_inventory`; no board → `unreachable`.)

5.5 `cook(i)` — MUST: `i` cookable, `hold((i, pre_state))` (recipe-required RAW or CHOPPED, i.e. `i`'s `cookable_from`), a free burner exists, `reach(STOVE)` with a free burner. Effect: auto-walk to a free stove; hands ⊖ item; occupy the lowest-index free burner here with a `CookJob` (`ready_gs = clock + cook_time`, `burn_gs = ready_gs + burn_window`); clock += `COOK_START_GS`. (Not cookable → `bad_target`; wrong held state → `wrong_inventory`; all burners busy → `burner_full`; unreachable stove → `unreachable`.)

5.6 `collect_cooked(i, burner_index?)` — MUST: `|hands|<HAND_SLOTS`, a reachable burner holds a READY (or transiently BURNED) job for `i` (if two burners hold the same `i`, `burner_index` disambiguates; omitted → lowest-index matching), `reach(STOVE)`. Effect: auto-walk to the stove; free the burner; if READY → hands ⊕ `(i, COOKED)`; if BURNED → hands ⊕ `(i, BURNED)`. Collecting a COOKING burner is invalid (`early_pickup`, E05); no matching READY/BURNED item → `bad_target`. clock += `COOK_PICKUP_GS`. **Note:** under auto-burn (§6.5) a burned job is auto-binned and its burner auto-freed the instant `clock ≥ burn_gs`, so in normal play the BURNED branch here is unreachable — a job is collected only while READY.

5.7 `plate(recipe)` — MUST: `recipe ∈ active set`, the held components (ignoring any finished plates) **EXACTLY** match the recipe by ingredient→state **and by count** (no extras, no missing, no duplicates, no wrong-state), `reach(PLATE)`. Recipes are ingredient→state maps, so they cannot request duplicates; you therefore cannot plate two of anything. Effect: auto-walk to a board; hands ⊖ the matched components; hands ⊕ `PlatedDish(recipe)`; clock += `PLATE_GS`. Any deviation → invalid (`wrong_inventory`, E06); unknown/off-menu recipe → `bad_target`; no plating counter → `unreachable`. **All-or-nothing; no partial plate** (quality `q ∈ {0,1}`, §SCORING; always q=1).

5.8 `serve(order_id)` — MUST: `order_id` exists, `orders[order_id].status == ACTIVE`, `hold(PlatedDish(recipe))` with `recipe == orders[order_id].dish`, `reach(PASS)`. Effect: auto-walk to the pass; hands ⊖ plate; order → SERVED; score += earned (§9.2); combo update (§9.7); clock += `SERVE_GS`. Unknown order → `bad_target`; non-ACTIVE order → `expired_serve`; wrong/absent plate → `wrong_inventory` — in all cases **plate retained** (E09, E10). **Re-check at the pass:** the order's status is re-validated after the walk, so an order that **expires while the chef is walking to the pass** makes the serve invalid (`expired_serve`); the serve action itself is exempted from triggering its own target's expiry during the `SERVE_GS` advance (`advance(..., exempt_order=order_id)`).

5.9 **Invalid action (universal).** Any unmet precondition: no state effect except clock += `INVALID_GS`, `counters.invalid_actions += 1`, `counters.invalid_by_reason[category] += 1` (§10), score += `INVALID_PENALTY` (§9.6), combo → 0 (§9.7), `last_invalid_reason` set, and an `invalid` event. Position/hands/burners/orders unchanged. Invalid actions MUST NOT terminate the episode.

---

## 6. Cooking subsystem
6.1 Burners = STOVE cells; each STOVE cell is an independent burner. `BURNER_COUNT` = number of STOVE cells = the cooking-concurrency cap. Burners are indexed in ascending cell order.
6.2 `cook` requires a free burner; if all are busy → invalid (`burner_full`). `cook` auto-walks to the nearest stove that has a free burner and uses the lowest-index free burner there.
6.3 **Walk-away/parallel cooking (frozen).** Cook timers advance only on the global clock; the chef is free to leave while an item cooks. Readiness is observable only through observations (never inferred from turn count). The oracle uses this same parallel model.
6.4 Item flow: `cook` moves hands→burner; `collect_cooked` moves burner→hands. On a burner an item occupies no hand slot.
6.5 **Auto-burn = auto-discard + auto-free (passive).** At `clock ≥ burn_gs` the cook job is destroyed with no action: the engine charges `BURN_PENALTY`, resets combo to 0, increments `counters.burns`, and **sets the burner's job to `None`** — the burnt item is binned automatically and the burner reopens. There is **no manual clearing** of a burned item. Effective burner status is therefore **FREE / COOKING / READY** (BURNED is a transient that the sweep clears before any action resolves). Simultaneous burns/ready events on the same advance are resolved in §11.5 order (burns by ascending `burner_index`).

---

## 7. Serving & order matching
7.1 `serve(order_id)` succeeds iff the order is ACTIVE and a held plate's recipe == the order's dish (and the order is still ACTIVE after the walk to the pass, §5.8).
7.2 Wrong-dish / missing plate → invalid (`wrong_inventory`, E09); plate retained.
7.3 Serve to EXPIRED/SERVED/PENDING (non-ACTIVE) order → invalid (`expired_serve`, E10); plate retained. Unknown order → invalid (`bad_target`).
7.4 **Bind-at-serve:** a finished plate is generic for its recipe; it may be served to any ACTIVE order of matching dish. Two ACTIVE orders for the same dish (E15) each need a separate plate; the model chooses which `order_id` to credit at serve time.

---

## 8. Observations (return schema)
Every turn returns the **full** observation (no partial/withheld-map mode — full-state-every-turn is the policy, eliminating book-keeping noise that would confound the tool-calling signal). Actual shape (from `engine.observe()`):
```json
{
  "ok": true, "clock_gs": 142.4, "horizon_gs": 260.0, "remaining_gs": 117.6,
  "chef_pos": [3,4],
  "grid_ascii": "#II#I##\n#.....I\n....(walled-room render)....",
  "grid_legend": "@=you I=dispenser B=board S=stove P=plate R=pass X=bin #=wall/counter D=door .=floor",
  "hands": [{"ingredient":"patty","state":"COOKED"}], "hand_slots_free": 3,
  "stations": [{"type":"STOVE","ingredient":null,"cell":[6,5]}, "...all stations..."],
  "burners": [{"burner_index":0,"cell":[6,3],"status":"READY","ingredient":"patty","ready_gs":138.0,"burn_gs":144.0},
              {"burner_index":1,"cell":[6,5],"status":"FREE","ingredient":null,"ready_gs":null,"burn_gs":null}],
  "burner_summary": {"active":1,"max":2},
  "orders": [{"order_id":"O4","dish":"soup","status":"ACTIVE","arrival_gs":31.6,"deadline_gs":160.0,"gs_remaining":17.6,"base_value":16.5}],
  "last_turn": {"think_gs":0.74,"calls":[{"ok":true,"action":"serve","note":"served O4 (soup) +9","call":"serve"}],
                "aborted_calls":[], "failed_at_index": null, "overflow_dropped": []},
  "events_since_last": [{"type":"cook_ready","clock_gs":138.0,"detail":{"burner_index":0,"ingredient":"patty"}}],
  "score": 214.0, "combo_count": 3,
  "ready_actions": ["plate(<recipe>)","serve(O4)"],
  "last_invalid_reason": null, "terminated": false
}
```
8.1 The observation reflects post-turn state. Arrivals/ready/burns/expiries that fired appear in `events_since_last`.
8.2 `stations` lists every station (`type`, `ingredient`, `cell`) every turn; `orders` lists only ACTIVE/PENDING orders (resolved ones drop out). Burner `status ∈ {FREE, COOKING, READY}` (§6.5). `ready_actions` is a tunable difficulty aid (`SHOW_READY_ACTIONS`, default true; **false** on the hard tier — it returns `[]`); it lists adjacency-based `collect`/`plate(<recipe>)`/`serve` hints for stations next to `chef_pos`.

---

## 9. Scoring (events, values & formulas — mirrored in `src/kitchenrush/scoring.py`)
Score is a `float` accumulator; the single rounding rule (`floor(x+0.5)`) is applied once per serve (§11.6). Final reported score is raw (ranking) and `max(0, score)` (display).

### 9.1 Base value (superlinear in steps, prevents cheap-dish farming)
```
base_value(recipe) = V0 + V1·n_steps + V2·n_steps²    # V0=6, V1=2, V2=0.5
```
Where `n_steps` = `config.recipe_n_steps` (collect/chop/cook pipeline per component + 1 plate step). Yields (`base_value`, kept as **float** — never rounded here; rounding happens only once per serve, §9.2/§11.6): soup (3 steps) → 16.5; burger (4) → 22.0; salad (5) → 28.5; mushroom_cheeseburger (8) → 54.0; veggie_ramen (9) → 64.5. Superlinearity guarantees hard dishes have higher points-per-time potential even at the combo cap.

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
On a cook auto-transitioning to BURNED: `score += BURN_PENALTY` (−5); `counters.burns += 1`; combo → 0; **the burner is auto-freed and the item auto-binned** (§6.5) — there is nothing left to discard.

### 9.5 Expiry penalty (value-scaled, NOT flat)
On an ACTIVE order → EXPIRED: `score += -(EXPIRY_FRACTION · base_value)` with `EXPIRY_FRACTION = 0.5`; `counters.expiries += 1`; combo → 0. (Value-scaled so cheap orders aren't over-punished and the invariant "finishing before deadline always beats expiry" holds for every value tier: a near-deadline serve pays ≥`FLOOR_FACTOR·base_value = 0.4·base_value` vs `−0.5·base_value` for expiry — a swing ≥0.9·base_value.)

### 9.6 Invalid & drop penalties
| Event | Constant | Default |
|---|---|---|
| Invalid action (any §5.9) | `INVALID_PENALTY` | −3 |
| Discard of needed non-burned item | `DROP_PENALTY` | −4 |
| Discard of burned item | — | 0 |

### 9.7 Combo / tip multiplier (complexity-gated; strict)
9.7.1 The streak `s` counts **consecutive on-time, clean serves**. A serve advances the streak iff it is **on-time** (`t ≤ deadline_gs`) AND uses no BURNED component (guaranteed by §5.7). **Combo resets to 0** on: an expiry, a burn, OR any invalid action (probing is expensive — adopting the strict rule).
9.7.2 **Anti-farming gate:** only serves of dishes with `n_steps ≥ COMBO_MIN_STEPS` (default 4 → burger/R4/R5; salad and soup do NOT advance the streak past 1) increment `s`. Cheap-dish spamming cannot build the multiplier.
9.7.3
```
combo_multiplier = min( COMBO_CAP , 1.0 + COMBO_STEP · max(0, s-1) )
```
`COMBO_STEP = 0.25`, `COMBO_CAP = 2.0` (cap reached at s=5, matching Overcooked's 2× tip). Combined with §9.1 superlinearity and §9.7.2 gating, `points_per_gs(hard) > points_per_gs(easy)` at the cap (verified in `test_scoring.py`).

### 9.8 No win/loss; the KR headline
No win flag. `final_report` presents `score_raw`, `score_display` (= `max(0, raw)`), and all counters. The leaderboard **headline** is the normalized **Kitchen Rush score** (`metrics.py`, METHODOLOGY §1):
```
KR = 100 · mean_over(seeds×trials) clip( (S_model − S_null) / (S_ref − S_null), 0, 1 )
```
where for each instance `S_model = score_raw`, `S_null` = the analytic do-nothing floor (serve nothing → every order expires, `oracle.null_score`), and `S_ref` = the **greedy-EDF oracle** (`oracle.py`) run at **zero latency** — deterministic and *complete* (it finishes every instance) but **not claimed optimal**. Instances with `S_ref ≤ S_null` are degenerate (excluded from KR, flagged as mis-calibration). Raw score, completion/expiry/invalid rates, latency percentiles, and Pass^k are diagnostics, not multiplied into the headline.

---

## 10. Diagnostic counters
From `engine._new_counters()`:
`serves_ok, invalid_actions, burns, expiries, drops, overshoot, observe_calls, total_tool_calls, chained_turns, chain_partial_failures, total_think_gs, total_action_gs, timeouts, empty_turns, max_combo, orders_total, orders_served, orders_expired, overflow_calls` (calls past `MAX_CALLS_PER_RESPONSE`), and `invalid_by_reason` — a per-category breakdown over the **invalid-action taxonomy** (`engine.INV_*` constants): `early_pickup, wrong_inventory, burner_full, expired_serve, bad_target, unreachable, malformed`. Each invalid action costs `INVALID_PENALTY` (§9.6) regardless of category.

*Vestigial under the current model interface:* `overshoot` (tied to the removed directional move) and `timeouts` (adapter-side) are allocated but **never incremented** by the engine; `observe_calls` only increments via the internal `observe` action, which models cannot invoke (§4.4).

---

## 11. Determinism guarantees
11.1 **Contract.** Given `(seed, tier, sequence_of(action|chain), sequence_of(latency_seconds))` the engine produces identical `S`, `score`, event log, and per-turn observations everywhere. No wall-clock, no unseeded RNG, no reliance on `set`/insertion-`dict` iteration for normative behavior.
11.2 **Seeded generation** uses stdlib `random.Random` with two named sub-streams derived from the seed (`procgen._substreams(seed)` → `(rng_grid, rng_orders)`). The **layout is deterministic per tier** (`rng_grid` is currently unused — see the design stance above), so only `rng_orders` drives variation. The single-`Generator` model is NOT used.
11.3 **Seeded order stream** (arrivals + dishes) is computed at reset from `rng_orders` and fixed for the episode; only fulfillment depends on agent choices.
11.4 **Latency replay.** `think_gs` is a pure function of `latency_seconds` (§3.2.2). RT replays from logged `wall_ms`; RP replays from recomputed token counts (pinned tokenizer, §3.2.1). Both are pure and fully specified.
11.5 **Tie-break (fixed priority).** Events in one advance are sorted by `(time, category, id_key)` with category numbers **(1) order expiries, (2) cook burns, (3) order arrivals, (4) cook ready**; the **action effect** is applied last, after the advance returns. Within a category, sort by ascending id (`order_id` lexicographic for orders, zero-padded `burner_index` for cooks). This applies at every clock advance, including each intra-chain advance.
11.6 **Score arithmetic.** Clock is float64. The ONLY rounding in the score path: `earned = floor(base_value·time_factor·combo·q + 0.5)`, computed left-to-right in float64, once per serve. Python `round()`/banker's rounding is FORBIDDEN. Penalties (`BURN_PENALTY`, `EXPIRY_FRACTION·base_value`, `INVALID_PENALTY`, `DROP_PENALTY`) are exact; expiry is `floor(EXPIRY_FRACTION·base_value + 0.5)` as a magnitude. Normalized display metrics (KR and rates, §9.8) are reported to 4 decimal places; leaderboard ties break on the next reported metric, never on float noise.
11.7 **Versions** `RULESET_VERSION` (hash of constants+recipes+scoring+tiers), `SCHEMA_VERSION`, `GENERATOR_VERSION` stamp every output and are validator-checked.

---

## 12. State-transition table
`clock += think_gs` + the §11.5 sweep precede every turn (once); each chained call then runs validate→advance(duration)+sweep→effect. Invalid rows apply §5.9 uniformly.

Station actions auto-walk first (§4.1); "clock +=" below is `travel + intrinsic`.

| Action | Guard | clock += | Mutation |
|---|---|---|---|
| `move_to(r,c)` | in-bounds; target/station-adjacent reachable | `dist·MOVE_GS_PER_STEP` | `chef_pos ← target (or floor beside station)` |
| `collect(i)` | `i∈active`,`|hands|<4`,reach(ING:i) | travel + `COLLECT_GS` | `hands ⊕ (i,RAW)` |
| `chop/prep(i)` | `i` choppable,`hold(i,RAW)`,reach(BOARD) | travel + `CHOP_GS`/`PREP_GS` | `(i,RAW)→(i,CHOPPED)` |
| `cook(i)` | cookable,`hold(i,pre)`,free burner,reach(STOVE) | travel + `COOK_START_GS` | hands⊖item; burner←CookJob |
| `collect_cooked(i,b?)` | reach(STOVE),burner READY (BURNED transient),`|hands|<4` | travel + `COOK_PICKUP_GS` | burner→FREE; hands ⊕ (i,COOKED) |
| `plate(r)` | `r∈active`,hands exactly == terminal comps (count-exact),reach(PLATE) | travel + `PLATE_GS` | hands ⊖ comps; hands ⊕ PlatedDish(r) |
| `serve(o)` | reach(PASS),`hold(PlatedDish(o.dish))`,`o` ACTIVE (re-checked post-walk) | travel + `SERVE_GS` | hands⊖plate; o→SERVED; score+=earned; combo update |
| `discard(x)` | reach(BIN),`hold(x)` | travel + `DISCARD_GS` | hands⊖x; if needed & ¬burned: score+=DROP_PENALTY |
| *invalid* | precondition unmet | `INVALID_GS` | score+=INVALID_PENALTY; invalid_actions+=1; invalid_by_reason[cat]+=1; combo←0 |

Passive (clock sweep, §11.5 priority):
| Trigger | Condition | Effect |
|---|---|---|
| Expiry | (PENDING∨ACTIVE) ∧ deadline crossed (≤ target), not the serve-exempt order | if ACTIVE: EXPIRED; score+=−floor(0.5·base_value+0.5); combo←0; expiries+=1; orders_expired+=1 |
| Burn | job ∧ ¬burned ∧ `burn_gs` crossed | BURN_PENALTY; combo←0; burns+=1; **burner job ← None (auto-free)** |
| Arrival | PENDING ∧ `arrival_gs ≤ target` | ACTIVE; arrival event |
| Ready | job ∧ ¬burned ∧ `ready_gs` crossed | `cook_ready` event (status becomes READY; no score effect) |
| Termination | §13 | terminated←True (`horizon` / `orders_exhausted` / `stalled`) |

---

## 13. Termination
13.1 **Horizon is the real limit (it always wins):** `clock_gs ≥ spec.horizon_gs` (checked in `_check_terminate`) terminates with reason `horizon`. Procgen sets the per-tier horizon and **extends it if needed** so that `max(deadline) + 1.0 ≤ horizon_gs` — the invariant "every order's `deadline_gs ≤ horizon_gs" holds, so no order is cut off mid-life.
13.2 **Early natural end (`orders_exhausted`):** all scheduled orders are SERVED or EXPIRED (the whole stream is fixed at reset, so this is the full set). Only reachable before horizon.
13.3 **Stall guard (`stalled`):** an episode that does **no productive work** for `STALL_TURNS` (50) consecutive turns terminates. Any successful (productive) action resets the counter; repeated invalids, no-op `move_to`, and empty responses do not. This is the true safety net that keeps **game-time**, not a small turn cap, the binding limit.
13.4 **`MAX_TURNS` (500)** is a paranoid anti-runaway ceiling enforced by the runner loop (not the engine); for a model making progress it should never bind. The scripted reference runs under `REFERENCE_MAX_TURNS` (20000) instead, since its turns are free and game-time bounds it.
13.5 **No failure-termination.** Invalid actions, burns, expiries never end the episode early.
13.6 **Truncation-invariance (any unresolved order → EXPIRED).** At episode end `final_report` force-resolves every order still PENDING or ACTIVE to EXPIRED, applying the expiry penalty (§9.5), resetting combo, and counting it as an expiry/`force_expired_end`. There is **no neutral scoring** for ACTIVE-at-horizon orders. This keeps scoring consistent with `S_null` (which assumes all unserved orders expire) so a fast agent cannot dodge expiry penalties by running out of turns. Idempotent.
13.7 On termination, emit `final_report` with `score_raw` + `score_display` (= `max(0, raw)`), all counters, the per-order final statuses, and (if tracing) the replay trace.
13.8 **Empty turn (stall).** A response with zero tool calls (pure text): charge `think_gs` only, `counters.empty_turns += 1`, no action, combo unaffected; counts toward the stall guard (§13.3). A malformed tool call (bad JSON/args/unknown tool) is NOT a stall — it IS an invalid action (`malformed`, §5.9, E18). *(Note: an explicit response-timeout path with a `timeouts` counter exists in `_new_counters()` but is exercised by the model adapter, not the in-process runner; no timeout handling is implemented in `engine.step`.)*

---

## 14. Edge-case table (normative)
| # | Situation | Behavior |
|---|---|---|
| E01 | Station has no reachable access cell of its type | invalid (`unreachable`); no move, no action |
| E02 | `move_to` to an unreachable / off-grid cell | invalid (`unreachable` / `bad_target`); no move |
| E03 | `move_to` to a station cell | walk to the nearest reachable floor cell beside it (no station work) |
| E04 | Station action with no reachable matching station | invalid (`unreachable`) — auto-nav cannot place the chef |
| E05 | `collect_cooked` while COOKING | invalid (`early_pickup`); burner unchanged |
| E06 | `plate` with missing/extra/wrong-state/duplicate comps | invalid (`wrong_inventory`); hands unchanged |
| E07 | Cook reaches `burn_gs` | auto-burn: BURN_PENALTY, combo reset, burner auto-freed, item auto-binned (§6.5) — no manual collect/discard needed |
| E08 | Hands full on collect / all burners busy on cook | invalid (`wrong_inventory` / `burner_full`) |
| E09 | Serve wrong dish / no matching plate | invalid (`wrong_inventory`); plate retained |
| E10 | Serve to SERVED/EXPIRED/PENDING order | invalid (`expired_serve`); plate retained |
| E11 | (removed — bind-at-serve eliminates plate-binding) | n/a |
| E12 | Serve exactly at `clock==deadline_gs` | valid; pays `time_factor(deadline)=FLOOR_FACTOR` |
| E13 | Order expires mid-cook of its ingredient | order EXPIRED (penalty, combo reset); the cook continues independently and, if not collected, auto-burns (separate penalty + auto-free, §6.5) |
| E14 | Chained partial failure | prefix persists; failing call charges invalid; suffix aborted (0 time); `failed_at_index` reported; no rollback |
| E15 | Two ACTIVE orders, same dish | independent; each needs its own plate; serve credits chosen `order_id` |
| E16 | Cook-READY and order-deadline same tick | §11.5: at `clock==deadline` order still ACTIVE (E12), ready item collectible — both succeed |
| E17 | Burn and expiry same tick | §11.5: expiry before burn; both penalties; combo reset once (idempotent) |
| E18 | Malformed action (bad arg/unknown ingredient/recipe/tool) | invalid (`malformed` or `bad_target`, §5.9); `last_invalid_reason` set |
| E19 | Action after terminated | `step` returns the observation with `"ok": false`; no clock/score change; not counted as invalid |
| E20 | Huge latency skips multiple events | all crossed events fire in clock order with §11.5 tie-break before the action validates |
| E21 | Discard needed non-burned item | allowed (auto-walk to bin); DROP_PENALTY |
| E22 | `plate` while holding an unrelated finished plate | allowed: finished plates are ignored when matching components, so the held comps can still exactly match (chef may hold multiple plates up to HAND_SLOTS) |
| E23 | *(timeout path is adapter-side, not implemented in `engine.step`; see §13.8)* | n/a in-process |
| E24 | Empty response (text, 0 calls) | stall: charge think_gs, no action, empty_turns+1; counts toward STALL_TURNS |
| E25 | Chef has no reachable station of a needed type | the action is invalid (`unreachable`); procgen's deterministic walled-room layout keeps the interior floor 4-connected with access to every station, so this cannot occur in valid instances |

---

## 15. Native tool-call schemas
Authoritative schemas live in `src/kitchenrush/tools.py` (`TOOL_SCHEMAS`, exposed to the model as `tools`). The model-facing tool set is: `move_to, collect, chop, prep, cook, collect_cooked, plate, serve, discard` — **`observe` is intentionally NOT included** (§4.4). `serve` takes only `order_id`; `plate` takes only `recipe`; `move_to` takes `row, col`. There is no directional `move` tool. (The internal `DIRECTIONS` map `north/south/east/west` is used only for adjacency/BFS inside the engine.)

---

## 16. Constant table (SINGLE SOURCE OF TRUTH; mirrors `src/kitchenrush/config.py`)
> Values are current as of this revision. **Provisional / under active recalibration** (ruleset not yet locked at 1.0.0): `MOVE_GS_PER_STEP`, `INVALID_PENALTY`, `BURN_PENALTY`, `DROP_PENALTY` — marked *‡* below.

| Constant | Default | Role |
|---|---|---|
| `GRID_N` | 7 | base grid size (tiers: easy/medium 7, hard 9) |
| `BURNER_COUNT` | 2 | STOVE cells = cook concurrency |
| `HAND_SLOTS` | 4 | inventory capacity |
| `LATENCY_SCALE` | 1.0 | latency_seconds → gs (the one knob) |
| `MOVE_GS_PER_STEP` *‡* | 0.15 | travel cost per cell (provisional) |
| `COLLECT_GS` | 2.0 | collect |
| `CHOP_GS`/`PREP_GS` | 4.0 | knife |
| `COOK_START_GS` | 2.0 | place on burner |
| `COOK_PICKUP_GS` | 1.0 | take off burner |
| `PLATE_GS` | 5.0 | plate |
| `SERVE_GS` | 3.0 | serve |
| `DISCARD_GS` | 1.0 | discard |
| `OBSERVE_GS` | 1.0 | internal observe (not a model tool) |
| `INVALID_GS` | 3.0 | invalid time |
| `HORIZON_GS` | 300.0 | base episode length (per-tier 260/340/420, extended to fit schedule) |
| `STALL_TURNS` | 50 | consecutive unproductive turns → terminate |
| `MAX_TURNS` | 500 | runner anti-runaway ceiling |
| `REFERENCE_MAX_TURNS` | 20000 | turn budget for the scripted oracle |
| `MAX_STEPS_PER_MOVE` | 8 | legacy single-leg cap (no directional `move` tool exists; unused by current actions) |
| `SCHEMA_MAX_STEPS` | 12 | legacy schema cap (unused by current actions) |
| `MAX_CALLS_PER_RESPONSE` | 6 | chain cap (overflow counted, §4.6) |
| `DECAY_RATE` | 0.6 | time-decay slope |
| `FLOOR_FACTOR` | 0.4 | min time factor at deadline |
| `V0`/`V1`/`V2` | 6.0/2.0/0.5 | base value `V0+V1·n+V2·n²` |
| `EXPIRY_FRACTION` | 0.5 | expiry penalty = 0.5·base_value |
| `BURN_PENALTY` *‡* | −5.0 | per burn (provisional) |
| `INVALID_PENALTY` *‡* | −3.0 | per invalid (provisional) |
| `DROP_PENALTY` *‡* | −4.0 | per bad discard (provisional) |
| `COMBO_STEP` | 0.25 | combo growth |
| `COMBO_CAP` | 2.0 | combo ceiling (cap at s=5) |
| `COMBO_MIN_STEPS` | 4 | min recipe steps to advance combo |
| `SHOW_READY_ACTIONS` | true | difficulty aid (false on hard tier) |
| `B_SECONDS` | 1.0 | per-decision latency the deadlines are priced at (METHODOLOGY §2) |
| `RP_BETA0`/`RP_BETA_IN`/`RP_BETA_OUT` *‡* | 0.30 / 0.0002 / 0.006 | RP token-proxy latency model (§3.2.1; coefficients provisional) |
| `TOKENIZER_ID` | `tiktoken-cl100k_base-v1` (`char4-v0` fallback) | pinned RP tokenizer, stamped in every output |
| `THETA_PASS` | 0.6 | episode passes a seed iff `score_raw ≥ THETA_PASS·S_ref` |
| `PASS_K` | 4 | trials per seed for Pass^k |
| `DEFAULT_TEMPERATURE` | 0.2 | sampling temperature for trials |
| `COOK_TIME[...]`/`BURN_WINDOW[...]` | §3.5 | base timers (procgen does NOT currently jitter them) |

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
10. **Full observation every turn; no model-facing `observe`.** The internal `observe` action remains (`OBSERVE_GS=1`) but is not in `TOOL_SCHEMAS` (§4.4).
11. **Navigation is automatic.** Station actions auto-walk and charge travel inline; `move_to` is optional pre-positioning. The benchmark tests the ACTION SEQUENCE under latency, not pathfinding. (Resolves the manual-move vs auto-nav fork; the legacy directional `move(direction, steps)` is dropped.)
12. **Deterministic layout, randomized orders only.** The walled-room kitchen is fixed per tier; only the seeded order stream varies, since with auto-nav randomizing station positions adds travel noise, not signal.
13. **Burn = auto-discard + auto-free.** A burned cook-job is binned and its burner reopened automatically; no manual clearing (§6.5).
14. **Plating is exact-match by count** (no extras/missing/duplicates, §5.7). *Rationale:* there is no persistent plate-accumulator entity (unlike Overcooked, where ingredients are added to a plate object one at a time and rejected at add-time). Held items are a single loose hand-pool and `plate(recipe)` is one atomic, declarative action validated against the held set — a cleaner function-calling primitive with minimal state. Requiring an EXACT match (rather than accepting a superset) keeps inventory discipline a tested skill: over-collecting isn't free, so the chef must hold precisely the right components when plating. The net rule matches Overcooked (no duplicates on a plate unless a recipe calls for it); the tradeoff is that batch-prepping across orders requires discarding the surplus.
15. **Truncation-invariance:** any order unresolved at episode end is force-EXPIRED (§13.6), keeping scoring consistent with `S_null` and the KR headline (§9.8).
