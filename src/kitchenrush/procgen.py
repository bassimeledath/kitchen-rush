"""Seeded procedural generation of a solvable kitchen + order stream (PROCEDURAL.md).

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


def _substreams(seed: int) -> tuple[random.Random, random.Random]:
    """Two independent, deterministic sub-streams (grid, orders)."""
    return random.Random(seed * 1000 + 1), random.Random(seed * 1000 + 2)


def _work_estimate(dish: str, grid_n: int) -> float:
    """Rough lower bound on game-seconds to complete one order (for deadline sizing)."""
    comps = config.RECIPES[dish]
    action_gs = config.PLATE_GS + config.SERVE_GS
    for ing, state in comps.items():
        ic = config.INGREDIENTS[ing]
        action_gs += config.COLLECT_GS
        if state == config.CHOPPED or (state == config.COOKED and ic.cookable_from == config.CHOPPED):
            action_gs += config.CHOP_GS
        if state == config.COOKED:
            action_gs += config.COOK_START_GS + ic.cook_time + config.COOK_PICKUP_GS
    travel = config.MOVE_GS_PER_STEP * grid_n * (len(comps) + 2)
    return action_gs + travel


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


def _build_layout(rng: random.Random, tier: config.Tier) -> tuple[list[StationSpec], tuple[int, int]]:
    n = tier.grid_n
    ingredients = config.recipe_ingredients(tier.recipes)
    # Needed stations: one dispenser per ingredient + board, stoves, plate, pass, bin.
    needed: list[tuple[str, str | None]] = [(config.ING, ing) for ing in ingredients]
    needed.append((config.BOARD, None))
    needed.extend((config.STOVE, None) for _ in range(tier.burner_count))
    needed.extend([(config.PLATE, None), (config.PASS, None), (config.BIN, None)])

    lattice = [(r, c) for r in range(0, n, 2) for c in range(0, n, 2)]
    if len(lattice) < len(needed):
        raise ValueError(f"grid {n}x{n} too small for {len(needed)} stations")

    for _attempt in range(50):
        cells = lattice[:]
        rng.shuffle(cells)
        chosen = cells[: len(needed)]
        stations = [StationSpec(cell, typ, ing) for cell, (typ, ing) in zip(chosen, needed)]
        station_cells = {s.cell for s in stations}
        # chef spawns on a floor cell with a walkable neighbor (odd cells are always floor)
        floor = [
            (r, c)
            for r in range(n)
            for c in range(n)
            if (r, c) not in station_cells
        ]
        if not _floor_connected(n, station_cells):
            continue
        rng.shuffle(floor)
        chef_start = None
        for cell in floor:
            r, c = cell
            for dr, dc in config.DIRECTIONS.values():
                nb = (r + dr, c + dc)
                if 0 <= nb[0] < n and 0 <= nb[1] < n and nb not in station_cells:
                    chef_start = cell
                    break
            if chef_start:
                break
        if chef_start is not None:
            return stations, chef_start
    raise RuntimeError("failed to generate a valid layout")


def _build_orders(rng: random.Random, tier: config.Tier) -> list[OrderSpec]:
    from . import scoring

    orders: list[OrderSpec] = []
    t = 0.0
    idx = 1
    while len(orders) < tier.max_orders:
        t += rng.expovariate(tier.arrival_rate)
        if t >= tier.horizon_gs:
            break
        dish = rng.choice(tier.recipes)
        deadline = t + _work_estimate(dish, tier.grid_n) * tier.deadline_factor
        if deadline > tier.horizon_gs:
            # would be cut off by the horizon; stop the stream (RULES §13.1 guarantee)
            break
        orders.append(
            OrderSpec(
                order_id=f"O{idx}",
                dish=dish,
                arrival_gs=round(t, 3),
                deadline_gs=round(deadline, 3),
                base_value=scoring.base_value(dish),
            )
        )
        idx += 1
    if not orders:  # guarantee at least one solvable order
        dish = tier.recipes[0]
        deadline = min(tier.horizon_gs, _work_estimate(dish, tier.grid_n) * tier.deadline_factor)
        orders.append(OrderSpec("O1", dish, 0.0, round(deadline, 3), scoring.base_value(dish)))
    return orders


def generate(seed: int, tier: str = "easy") -> KitchenSpec:
    """Deterministically generate a kitchen instance from an integer seed and tier name."""
    if tier not in config.TIERS:
        raise ValueError(f"unknown tier {tier!r}; choose from {sorted(config.TIERS)}")
    t = config.TIERS[tier]
    rng_grid, rng_orders = _substreams(seed)
    stations, chef_start = _build_layout(rng_grid, t)
    orders = _build_orders(rng_orders, t)
    return KitchenSpec(
        seed=seed,
        tier=tier,
        grid_n=t.grid_n,
        burner_count=t.burner_count,
        horizon_gs=t.horizon_gs,
        show_ready_actions=t.show_ready_actions,
        active_recipes=t.recipes,
        stations=stations,
        chef_start=chef_start,
        orders=orders,
    )
