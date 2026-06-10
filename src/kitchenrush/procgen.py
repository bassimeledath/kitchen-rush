"""Seeded procedural generation of a solvable kitchen + order stream (RULES §11).

Lean MVP: stdlib ``random`` with four named sub-streams derived from the seed. Stations
are placed on the even/even lattice, which guarantees (a) every station has floor access
cells and (b) the floor stays 4-connected. A full feasibility *oracle* and per-instance
timer jitter are deferred to later phases; deadlines are made generous enough (via the
tier ``deadline_factor``) that a competent policy can complete them.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from . import config


@dataclass(frozen=True)
class StationSpec:
    cell: tuple[int, int]
    type: str
    ingredient: str | None = None


@dataclass(frozen=True)
class OrderSpec:
    order_id: str
    dish: str
    arrival_gs: float
    deadline_gs: float
    base_value: float


@dataclass
class KitchenSpec:
    seed: int
    tier: str
    grid_n: int
    burner_count: int
    horizon_gs: float
    show_ready_actions: bool
    active_recipes: tuple[str, ...]
    stations: list[StationSpec]
    chef_start: tuple[int, int]
    orders: list[OrderSpec]
    latency_budget: float = 1.0   # B: seconds/decision the deadlines were priced at
    blocked: frozenset = field(default_factory=frozenset)   # non-walkable counter / wall cells
    door: tuple[int, int] | None = None                     # decorative doorway cell (in the wall)


def _substreams(seed: int) -> tuple[random.Random, random.Random]:
    """Two independent, deterministic sub-streams (grid, orders)."""
    return random.Random(seed * 1000 + 1), random.Random(seed * 1000 + 2)


def critical_path(dish: str, grid_n: int, b: float | None = None) -> tuple[float, int, float]:
    """Reference critical path for one order, priced at ``b`` seconds per decision
    (METHODOLOGY §2). Returns (A_o intrinsic time, K_o decisions, C_o = A_o + K_o*b).
    ``b`` defaults to ``config.B_SECONDS`` (read at call time, not import time)."""
    if b is None:
        b = config.B_SECONDS
    comps = config.RECIPES[dish]
    n_collect = len(comps)
    n_chop = 0
    n_cook = 0
    cook_time_sum = 0.0
    for ing, state in comps.items():
        ic = config.INGREDIENTS[ing]
        if state == config.CHOPPED or (state == config.COOKED and ic.cookable_from == config.CHOPPED):
            n_chop += 1
        if state == config.COOKED:
            n_cook += 1
            cook_time_sum += ic.cook_time
    action_gs = (
        n_collect * config.COLLECT_GS
        + n_chop * config.CHOP_GS
        + n_cook * (config.COOK_START_GS + config.COOK_PICKUP_GS)
        + cook_time_sum
        + config.PLATE_GS
        + config.SERVE_GS
    )
    k_o = n_collect + n_chop + n_cook + n_cook + 2  # collects, chops, cooks, pickups, plate, serve
    travel = config.MOVE_GS_PER_STEP * (grid_n * 0.5) * k_o
    a_o = action_gs + travel
    return a_o, k_o, a_o + k_o * b


def _floor_connected(grid_n: int, station_cells: set[tuple[int, int]]) -> bool:
    floor = [(r, c) for r in range(grid_n) for c in range(grid_n) if (r, c) not in station_cells]
    if not floor:
        return False
    start = floor[0]
    seen = {start}
    q = deque([start])
    while q:
        r, c = q.popleft()
        for dr, dc in config.DIRECTIONS.values():
            nb = (r + dr, c + dc)
            if 0 <= nb[0] < grid_n and 0 <= nb[1] < grid_n and nb not in station_cells and nb not in seen:
                seen.add(nb)
                q.append(nb)
    return len(seen) == len(floor)


def _build_kitchen(tier: config.Tier):
    """Fixed, workflow-ordered kitchen: stations line the walls in a sensible order (ingredient
    dispensers, then prep, cook, plate, pass, bin), the interior is open floor, and the
    bottom-centre wall cell is the doorway.

    The layout is DETERMINISTIC per tier. With auto-navigation the model never reasons about
    coordinates (it calls ``cook(patty)``; the engine walks there), so randomizing station
    positions would only add travel-time noise, not test anything — only the order stream is
    randomized, which is what actually tests planning under latency.

    Returns (stations, chef_start, blocked, door); ``blocked`` is every non-walkable counter/wall
    cell that is not a station (empty counters + corners + the doorway)."""
    n = tier.grid_n
    ingredients = config.recipe_ingredients(tier.recipes)
    # workflow order along the counter: dispensers -> board -> stoves -> plate -> pass -> bin
    ordered: list[tuple[str, str | None]] = [(config.ING, ing) for ing in ingredients]
    ordered.append((config.BOARD, None))
    ordered.extend((config.STOVE, None) for _ in range(tier.burner_count))
    ordered.extend([(config.PLATE, None), (config.PASS, None), (config.BIN, None)])

    # perimeter cells, clockwise from the top-left, skipping corners (corners stay walls)
    top = [(0, c) for c in range(1, n - 1)]
    right = [(r, n - 1) for r in range(1, n - 1)]
    bottom = [(n - 1, c) for c in range(n - 2, 0, -1)]
    left = [(r, 0) for r in range(n - 2, 0, -1)]
    path = top + right + bottom + left
    if len(ordered) > len(path):
        raise ValueError(f"grid {n}x{n} too small for {len(ordered)} stations")

    # spread stations EVENLY around the whole perimeter (all four walls) rather than packing them
    # contiguously from the top-left, so the kitchen reads as balanced.
    step = len(path) / len(ordered)
    stations = [StationSpec(path[int(i * step)], typ, ing) for i, (typ, ing) in enumerate(ordered)]
    station_cells = {s.cell for s in stations}
    border = {(r, c) for r in range(n) for c in range(n) if r in (0, n - 1) or c in (0, n - 1)}
    blocked = border - station_cells                             # empty counters + corners + door

    # doorway: bottom-centre if free, else the free bottom-edge cell nearest centre
    mid_c = n // 2
    free_bottom = [(n - 1, c) for c in range(1, n - 1) if (n - 1, c) not in station_cells]
    door = ((n - 1, mid_c) if (n - 1, mid_c) in free_bottom
            else min(free_bottom, key=lambda cell: abs(cell[1] - mid_c)) if free_bottom else None)

    interior = [(r, c) for r in range(1, n - 1) for c in range(1, n - 1)]   # all open floor
    ref = door if door else (n - 1, mid_c)
    chef_start = min(interior, key=lambda c: abs(c[0] - ref[0]) + abs(c[1] - ref[1]))
    return stations, chef_start, frozenset(blocked), door


def _build_orders(rng: random.Random, tier: config.Tier, b: float) -> list[OrderSpec]:
    from . import scoring

    import math

    # 1. arrivals + dishes — Overcooked-style progressive release: 2 orders active at t=0,
    #    the rest released over time (Poisson gaps at tier.arrival_rate).
    raw: list[tuple[float, str]] = []
    t = 0.0
    for idx in range(1, tier.max_orders + 1):
        if idx <= 2:
            arrival = 0.0
        else:
            t += rng.expovariate(tier.arrival_rate)
            if t >= tier.horizon_gs:
                break
            arrival = t
        raw.append((arrival, rng.choice(tier.recipes)))
    if not raw:  # guarantee at least one solvable order
        raw.append((0.0, tier.recipes[0]))

    # 2. deadlines priced on a single-server (one-chef) queue at B s/decision, so that
    #    simultaneous/bunched orders stay feasible for the *sequential* reference oracle:
    #    an order's headroom covers its queue wait PLUS its own critical path, not just the
    #    isolated path. ``finish`` tracks when the chef clears each order in arrival order;
    #    deadline = arrival + ceil(slack * (finish - arrival)). Horizon scales in generate().
    orders: list[OrderSpec] = []
    busy = 0.0
    for idx, (arrival, dish) in enumerate(raw, start=1):
        _, _, c_o = critical_path(dish, tier.grid_n, b)
        finish = max(arrival, busy) + c_o
        busy = finish
        deadline = arrival + math.ceil(tier.slack * (finish - arrival))
        orders.append(
            OrderSpec(
                order_id=f"O{idx}",
                dish=dish,
                arrival_gs=round(arrival, 3),
                deadline_gs=float(deadline),
                base_value=scoring.base_value(dish),
            )
        )
    return orders


def generate(seed: int, tier: str = "easy", b: float | None = None) -> KitchenSpec:
    """Deterministically generate a kitchen instance from a seed, tier, and latency budget B.

    ``b`` (seconds/decision) prices the order deadlines (METHODOLOGY §2); defaults to
    ``config.B_SECONDS``. Different B = different difficulty for the same seed/tier."""
    if tier not in config.TIERS:
        raise ValueError(f"unknown tier {tier!r}; choose from {sorted(config.TIERS)}")
    if b is None:
        b = config.B_SECONDS
    t = config.TIERS[tier]
    _rng_grid, rng_orders = _substreams(seed)   # layout is fixed per tier; only orders are seeded
    stations, chef_start, blocked, door = _build_kitchen(t)
    orders = _build_orders(rng_orders, t, b)
    # Horizon scales to fit the (B-priced) schedule so a loose budget isn't clipped, while
    # keeping the RULES invariant "every deadline <= horizon" (§13.1).
    max_deadline = max((o.deadline_gs for o in orders), default=t.horizon_gs)
    horizon = max(t.horizon_gs, max_deadline + 1.0)
    return KitchenSpec(
        seed=seed,
        tier=tier,
        grid_n=t.grid_n,
        burner_count=t.burner_count,
        horizon_gs=horizon,
        show_ready_actions=t.show_ready_actions,
        active_recipes=t.recipes,
        stations=stations,
        chef_start=chef_start,
        orders=orders,
        latency_budget=b,
        blocked=blocked,
        door=door,
    )
