> ⚠️ **Design history — not the current spec.** Parts of this document describe an earlier or
> aspirational design and may not match the implementation. The authoritative, code-verified spec
> is **[RULES.md](RULES.md)**; release tracking is in **[LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md)**.
# Kitchen Rush v2 — MOVEMENT, GRID & CHAINED TOOL CALLING

This is the action/tool space and grid mechanics. It is consistent with the **hands-only, ingredient/order-keyed** world model (RULES §2–§7) and the canonical schemas in `tools/schemas.py`. The legacy `pick_up`/`place`/`station_id` vocabulary is NOT used; items live in hands (RULES §2.5.3), cook items live on burners.

## 0. Load-bearing decisions (all resolved)
1. **`move(direction, steps)` only** (no `move_to`). Tests spatial reasoning + chaining + latency coupling; matches BALROG. Directions are full words `north|south|east|west`.
2. **Verbs are ingredient/order-keyed and hands-based:** `collect, chop, prep, cook, collect_cooked, plate, serve, discard, observe` (RULES §4). Stations are entered by **adjacency** (the engine resolves which station you're operating from `chef_pos`); the model does not pass `station_id`.
3. **Chained tool calling is required** and is the central latency lever: multiple `tool_calls` per response execute sequentially; **one `think_gs` charge per response**, then N action durations.
4. **Fail-fast-with-commit** partial-failure (prefix persists, failing call penalized + breaks combo, suffix aborted at zero cost). The inter-call event sweep CAN self-invalidate a later call.
5. **Float clock**; `MAX_STEPS_PER_MOVE=8` (schema hard-cap 12); `MAX_CALLS_PER_RESPONSE=6`.

## 1. Tool catalog & native-FC schemas (authoritative = `tools/schemas.py`)
```json
[
 {"type":"function","function":{"name":"move",
   "description":"Walk the chef in a straight line up to `steps` cells in `direction`. Stops at the first wall/station (no auto-turn). Chain move calls to turn corners.",
   "parameters":{"type":"object","properties":{
     "direction":{"type":"string","enum":["north","south","east","west"],
       "description":"north=row-1, south=row+1, east=col+1, west=col-1; origin (0,0) is top-left."},
     "steps":{"type":"integer","minimum":1,"maximum":12,
       "description":"Cells to attempt; clamped to 8. Movement stops at the first obstacle."}},
     "required":["direction","steps"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"observe",
   "description":"Return full kitchen state (grid, positions, orders, burners, hands, score). A full observation is already returned after every turn.",
   "parameters":{"type":"object","properties":{},"additionalProperties":false}}},
 {"type":"function","function":{"name":"collect",
   "description":"Pick up one raw ingredient. Must be 4-adjacent to that ingredient's dispenser; needs a free hand.",
   "parameters":{"type":"object","properties":{"ingredient":{"type":"string"}},"required":["ingredient"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"chop",
   "description":"Chop a held raw ingredient. Must be 4-adjacent to a cutting board.",
   "parameters":{"type":"object","properties":{"ingredient":{"type":"string"}},"required":["ingredient"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"prep",
   "description":"Prep (knife/assembly) a held raw ingredient. Must be 4-adjacent to a cutting board.",
   "parameters":{"type":"object","properties":{"ingredient":{"type":"string"}},"required":["ingredient"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"cook",
   "description":"Place a held ingredient on a free burner to cook (starts a countdown; burns if left past the ready window). Must be 4-adjacent to a stove. Frees you to work while it cooks.",
   "parameters":{"type":"object","properties":{"ingredient":{"type":"string"}},"required":["ingredient"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"collect_cooked",
   "description":"Take a cooked ingredient off a burner. READY→COOKED; overdue→BURNED (ruined). Must be 4-adjacent to a stove; needs a free hand.",
   "parameters":{"type":"object","properties":{"ingredient":{"type":"string"},
     "burner_index":{"type":"integer","minimum":0,"description":"Optional; disambiguates when two burners hold the same ingredient."}},
     "required":["ingredient"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"plate",
   "description":"Assemble the exact required held components into a finished dish. Must be 4-adjacent to a plating counter. All-or-nothing: missing/extra/wrong-state/burned components make it invalid.",
   "parameters":{"type":"object","properties":{"recipe":{"type":"string","enum":["burger","soup","salad","mushroom_cheeseburger","veggie_ramen"]}},"required":["recipe"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"serve",
   "description":"Serve a held finished plate to an active order at the pass. The plate is generic for its recipe; choose which order to credit. Must be 4-adjacent to the pass.",
   "parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"],"additionalProperties":false}}},
 {"type":"function","function":{"name":"discard",
   "description":"Throw a held item in the bin. Required (free) to clear burned food. Must be 4-adjacent to the bin.",
   "parameters":{"type":"object","properties":{"item":{"type":"string","description":"'ingredient', 'ingredient:state', or 'plate:recipe'"}},"required":["item"],"additionalProperties":false}}}
]
```
The engine emits exactly this set (minus inactive recipes/ingredients) via `engine.tool_specs()`. `serve` takes only `order_id`; `plate` takes only `recipe` (bind-at-serve, RULES §7.4).

## 2. Grid mechanics
**Tiles** (ASCII legend): `@`=chef, `#`=wall, `.`=floor, `D<x>`=dispenser, `B`=board, `P<i>`=stove/burner i, `L`=plate counter, `S`=pass, `T`=bin. Stations are non-walkable; the chef stands on a 4-adjacent floor cell. Adjacency is 4-neighbor (von Neumann). Single agent (no collisions).

**`move` semantics (deterministic, RULES §4.3/§5.1):** clamp `steps` to `[1,12]` (schema) then `MAX_STEPS_PER_MOVE=8` (config; over-clamp flagged `clamped:true`, not a mistake). Walk cell-by-cell; halt at the first wall/station/edge. `moved==requested` → clean. `0<moved<requested` → partial (`partial:true`, `blocked_by`), still `ok`, charges `moved×MOVE_GS_PER_STEP`, `overshoot+1`. `moved==0` → **mistake** (E02): `ok:false`, invalid penalty+time. No diagonals (`allow_diagonals=false`); turning a corner is a new `move` call. No auto-turn (this is why `move(direction,steps)` tests planning).

## 3. Chained tool calling
**Yes, multiple `tool_calls` per response**, capped at `MAX_CALLS_PER_RESPONSE=6` (overflow dropped silently, no cost). Treated as a **sequential plan** against shared state (not parallel). A zero-tool-call response is an empty-turn stall (RULES §13.7): charges `think_gs`, no action.

**Execution (RULES §3.2.3, §4.6):**
```
charge think_gs once  (advance clock + event sweep over the crossed interval)
for i, call in enumerate(calls[:MAX_CALLS_PER_RESPONSE]):
    if not precondition(call, current_state):       # validated vs CURRENT (post-prior-call) state
        clock += INVALID_GS; score += INVALID_PENALTY; combo = 0; record mistake
        abort calls[i+1:]  (zero time, logged as aborted); break        # FAIL-FAST WITH COMMIT
    apply(call); clock += duration(call); event_sweep()                 # sweep CAN burn/expire mid-chain
return aggregated per-call outcomes + ONE fresh full observation
```
- **think_gs is charged once, independent of chain length** — so a 5-call chain pays one think charge + 5 durations, while five single-call responses pay five think charges. Chaining is rewarded; this is the latency lever.
- **The inter-call sweep can self-invalidate a later call** (e.g., a soup's `burn_gs` falls between an earlier `move` and a later `plate` → the plate's component is now BURNED → plate invalid → chain aborts). This is the intended difficulty of blind multi-step planning.
- No mid-chain re-observation; the model commits the whole chain on the observation it had, trading latency savings for blind-planning risk.

## 4. Observation
Full state every turn (RULES §8): ASCII grid render (legend + `x→east, y→south`, origin top-left) plus the structured JSON block (chef pos, hands+slots, all stations, burners with `ready_gs`/`burn_gs`, orders with `deadline_gs`/`gs_remaining`, score, combo, `ready_actions` (toggle), `last_turn` per-call outcomes + `failed_at_index`, `events_since_last`). `observe()` is a fixed `OBSERVE_GS=1` cost with no free quota (rarely needed since every turn returns the full observation).

## 5. Step-count validation pipeline
1. Schema bound `1≤steps≤12` (coerce `<1→1`, `>12→12`, non-int→round).
2. Config clamp to `MAX_STEPS_PER_MOVE=8` (`clamped:true`, not a mistake).
3. Geometric clamp `effective = min(steps, distance_to_first_obstacle)`; `partial` if `effective<steps`.
4. No-op guard: `moved==0` → mistake (E02).
5. Upper bound exists so a model can't "teleport" — on larger grids straight corridors are one call but any turn requires chaining (`MAX_STEPS_PER_MOVE ≈ min(grid dims)`, scaled by procgen).

## 6. Worked chained-turn example
**Setup:** Chef `@` at `(2,2)`. Order `O1` needs `salad` (needs lettuce+tomato, both chopped, then plate). `disp_lettuce`(`Dl`) at `(1,3)`; `board_0`(`B`) at `(2,5)`; pot `P0` cooking a soup for `O2`, `ready_gs=35, burn_gs=43`. Clock 27.0 before the model's think.

```
LEGEND: @=you #=wall .=floor Dl=lettuce Dt=tomato B=board P=stove L=plate S=pass T=bin
     x0 x1 x2 x3 x4 x5 x6
y0   #  #  #  #  #  #  #
y1   #  .  .  .  .  L  #
y2   #  .  @  .  .  B  #
y3   #  Dl .  P0 .  .  #
y4   #  Dt .  .  .  S  #
y5   #  .  .  .  .  T  #
y6   #  #  #  #  #  #  #
```

**WRONG chain** the model emits (walks onto the dispenser):
```json
{"tool_calls":[
 {"name":"move","arguments":{"direction":"west","steps":1}},
 {"name":"move","arguments":{"direction":"south","steps":1}},
 {"name":"collect","arguments":{"ingredient":"lettuce"}},
 {"name":"move","arguments":{"direction":"north","steps":1}},
 {"name":"collect","arguments":{"ingredient":"tomato"}}]}
```
**Trace** (`think L=740ms → think_gs=0.74`):
| # | Call | vs state | Result | clock |
|---|---|---|---|---|
| – | think | sweep over (27.0, 27.74] | nothing fires | 27.0→27.74 |
| 1 | move(west,1) | (2,2)→(2,1) floor | ok, +1.0 | 27.74→28.74 |
| 2 | move(south,1) | (2,1)→(2,... wait (3,1) is `Dl` STATION | **moved=0 → mistake** (E02): +INVALID_GS=3, −5, combo→0 | 28.74→31.74 |
| 3 | collect(lettuce) | **aborted** (fail-fast) | 0 time | 31.74 |
| 4 | move(north,1) | **aborted** | 0 time | 31.74 |
| 5 | collect(tomato) | **aborted** | 0 time | 31.74 |

Returned: one fresh observation; `last_turn.failed_at_index=1`, `last_invalid_reason:"move blocked: Dl at (3,1) is a station — stand adjacent, don't walk onto it"`; combo reset. Note `P0` soup is still cooking (ready 35, burn 43) — the model now sees the looming burn.

**CORRECT chain** (the chef at `(2,1)` is already 4-adjacent to `Dl` at `(3,1)`):
```json
{"tool_calls":[
 {"name":"move","arguments":{"direction":"west","steps":1}},
 {"name":"collect","arguments":{"ingredient":"lettuce"}},
 {"name":"move","arguments":{"direction":"south","steps":1}},
 {"name":"collect","arguments":{"ingredient":"tomato"}},
 {"name":"move","arguments":{"direction":"east","steps":3}}]}
```
— one think charge, five durations, zero mistakes: now at `(3,...)` carrying both ingredients, heading toward `board_0`. This demonstrates (a) one-think-then-N-durations, (b) station **adjacency** (you stand beside `Dl`, never on it), (c) fail-fast-with-commit capping damage at one mistake, and (d) the clock advancing during thinking+actions surfacing the soup's burn deadline for the next turn.
