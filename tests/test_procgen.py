"""Procedural generation validity (PROCEDURAL.md): distinct stations, floor connectivity,
station access cells, required station types, and horizon-clamped deadlines."""

from collections import deque

import pytest

from kitchenrush import config, procgen


def _floor_connected(n, station_cells):
    floor = [(r, c) for r in range(n) for c in range(n) if (r, c) not in station_cells]
    seen = {floor[0]}
    q = deque([floor[0]])
    while q:
        r, c = q.popleft()
        for dr, dc in config.DIRECTIONS.values():
            nb = (r + dr, c + dc)
            if 0 <= nb[0] < n and 0 <= nb[1] < n and nb not in station_cells and nb not in seen:
                seen.add(nb)
                q.append(nb)
    return len(seen) == len(floor)


@pytest.mark.parametrize("tier", ["easy", "medium", "hard"])
@pytest.mark.parametrize("seed", [0, 1, 2, 7, 42])
def test_spec_valid(tier, seed):
    spec = procgen.generate(seed, tier)
    cells = [s.cell for s in spec.stations]
    station_cells = set(cells)
    n = spec.grid_n

    assert len(cells) == len(station_cells)  # all distinct
    for s in spec.stations:
        assert 0 <= s.cell[0] < n and 0 <= s.cell[1] < n
        has_access = any(
            (s.cell[0] + dr, s.cell[1] + dc) not in station_cells
            and 0 <= s.cell[0] + dr < n and 0 <= s.cell[1] + dc < n
            for dr, dc in config.DIRECTIONS.values()
        )
        assert has_access, f"{s} has no floor access cell"
    assert _floor_connected(n, station_cells)
    assert spec.chef_start not in station_cells

    types = [s.type for s in spec.stations]
    for required in (config.BOARD, config.PLATE, config.PASS, config.BIN):
        assert required in types
    assert types.count(config.STOVE) == spec.burner_count
    for ing in config.recipe_ingredients(spec.active_recipes):
        assert any(s.type == config.ING and s.ingredient == ing for s in spec.stations)

    assert spec.orders
    arrivals = [o.arrival_gs for o in spec.orders]
    assert arrivals == sorted(arrivals)
    for o in spec.orders:
        assert o.arrival_gs < o.deadline_gs <= spec.horizon_gs
