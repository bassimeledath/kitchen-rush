"""Greedy-EDF reference scheduler + the null floor (METHODOLOGY §1).

The oracle is a deterministic scripted policy that plays an instance reasonably well:
earliest-deadline-first, one order at a time, with BFS navigation and intra-order use of
cook time. Run at zero latency it gives ``S_ref`` (the headline's reference ceiling); run at
injected latencies it drives difficulty calibration. ``null_score`` is the analytic floor
(serve nothing → every order expires).

It is NOT claimed optimal — it is a strong, consistent reference (job-shop with travel +
deadlines is NP-hard). Being deterministic, it keeps the KR scale reproducible.
"""

from __future__ import annotations

from collections import deque

from . import config, scoring
from .tools import ToolCall

_DELTA_BY_DIR = config.DIRECTIONS
_DIR_BY_DELTA = {v: k for k, v in config.DIRECTIONS.items()}
_RAW, _CHOPPED, _COOKED = config.RAW, config.CHOPPED, config.COOKED


def null_score(spec) -> float:
    """Analytic 'do nothing' floor: serve nothing, every order expires, no other penalties."""
    return float(sum(scoring.expiry_penalty(scoring.base_value(o.dish)) for o in spec.orders))


class OracleAgent:
    """Greedy-EDF policy usable as a runner policy: (obs, tools) -> (calls, latency)."""

    def __init__(self, latency: float = 0.0) -> None:
        self.latency = latency
        self._current: str | None = None   # order_id currently being assembled (sticky)

    # -- policy entrypoint ----------------------------------------------------
    def __call__(self, obs: dict, tools: list[dict]) -> tuple[list[ToolCall], float]:
        kind, *rest = self._decide(obs)
        if kind == "none":
            return [ToolCall("observe", {})], self.latency
        if kind == "wait":
            (cells,) = rest
            return self._go(obs, cells, None), self.latency
        # kind == "act"
        call, cells = rest
        return self._go(obs, cells, call), self.latency

    # -- navigation -----------------------------------------------------------
    def _go(self, obs: dict, station_cells: list[tuple], action: ToolCall | None) -> list[ToolCall]:
        chef = tuple(obs["chef_pos"])
        targets = [tuple(c) for c in station_cells]
        if self._adjacent(chef, targets):
            return [action] if action else [ToolCall("observe", {})]
        blocked, n = self._grid(obs)
        access = {
            (s[0] + d[0], s[1] + d[1])
            for s in targets for d in _DELTA_BY_DIR.values()
            if self._floor((s[0] + d[0], s[1] + d[1]), blocked, n)
        }
        path = self._bfs(chef, access, blocked, n)
        if not path:
            return [ToolCall("observe", {})]
        dest = path[-1]  # nearest reachable access cell; the engine handles the walk
        calls = [ToolCall("move_to", {"row": dest[0], "col": dest[1]})]
        if action:
            calls.append(action)
        return calls

    @staticmethod
    def _grid(obs: dict) -> tuple[set, int]:
        n = len(obs["grid_ascii"].split("\n"))
        blocked = {tuple(s["cell"]) for s in obs["stations"]}
        return blocked, n

    @staticmethod
    def _floor(cell, blocked, n) -> bool:
        r, c = cell
        return 0 <= r < n and 0 <= c < n and cell not in blocked

    @staticmethod
    def _adjacent(chef, cells) -> bool:
        return any(abs(chef[0] - s[0]) + abs(chef[1] - s[1]) == 1 for s in cells)

    def _bfs(self, start, goals, blocked, n):
        if not goals:
            return None
        prev = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            for d in _DELTA_BY_DIR.values():
                nb = (cur[0] + d[0], cur[1] + d[1])
                if nb in prev or not self._floor(nb, blocked, n):
                    continue
                prev[nb] = cur
                if nb in goals:
                    path = [nb]
                    p = cur
                    while p != start:
                        path.append(p)
                        p = prev[p]
                    path.reverse()
                    return path
                q.append(nb)
        return None

    @staticmethod
    def _legs(start, path):
        legs: list[list] = []
        cur = start
        for cell in path:
            delta = (cell[0] - cur[0], cell[1] - cur[1])
            name = _DIR_BY_DELTA[delta]
            if legs and legs[-1][0] == name:
                legs[-1][1] += 1
            else:
                legs.append([name, 1])
            cur = cell
        return [(d, s) for d, s in legs]

    # -- decision (greedy EDF) ------------------------------------------------
    def _decide(self, obs: dict):
        hands = obs["hands"]
        burners = obs["burners"]
        active = [o for o in obs["orders"] if o["status"] == "ACTIVE"]

        # cells by station type
        ing_cells: dict[str, list] = {}
        board, plate, pass_, bin_ = [], [], [], []
        for s in obs["stations"]:
            if s["type"] == config.ING:
                ing_cells.setdefault(s["ingredient"], []).append(tuple(s["cell"]))
            elif s["type"] == config.BOARD:
                board.append(tuple(s["cell"]))
            elif s["type"] == config.PLATE:
                plate.append(tuple(s["cell"]))
            elif s["type"] == config.PASS:
                pass_.append(tuple(s["cell"]))
            elif s["type"] == config.BIN:
                bin_.append(tuple(s["cell"]))
        free_stove = [tuple(b["cell"]) for b in burners if b["status"] == "FREE"]

        def have(ing, state):
            return any(h["ingredient"] == ing and h["state"] == state for h in hands)

        # 1. serve a finished plate to its earliest-deadline active order
        for h in hands:
            if h["state"] == "PLATE":
                cand = [o for o in active if o["dish"] == h["ingredient"]]
                if cand:
                    o = min(cand, key=lambda o: o["deadline_gs"])
                    return "act", ToolCall("serve", {"order_id": o["order_id"]}), pass_

        # 2. take any READY cook off the burner (it is needed; prevents a burn)
        for b in burners:
            if b["status"] == "READY":
                return "act", ToolCall("collect_cooked", {"ingredient": b["ingredient"]}), [tuple(b["cell"])]

        # cleanup: holding a BURNED item -> discard it
        for h in hands:
            if h["state"] == config.BURNED and bin_:
                return "act", ToolCall("discard", {"item": h["ingredient"]}), bin_

        # sticky target: keep building the current order until it leaves ACTIVE
        active_ids = {o["order_id"] for o in active}
        if self._current not in active_ids:
            self._current = None
            # if we hold orphan components (current order gone), dump them
            comps = [h for h in hands if h["state"] != "PLATE"]
            if comps and bin_:
                return "act", ToolCall("discard", {"item": comps[0]["ingredient"]}), bin_
        if self._current is None:
            if not active:
                return ("none",)
            self._current = min(active, key=lambda o: o["deadline_gs"])["order_id"]

        target = next(o for o in active if o["order_id"] == self._current)
        recipe = config.RECIPES[target["dish"]]

        cooking_cells: list = []
        waiting = False
        for ing, term in recipe.items():
            if have(ing, term):
                continue
            ic = config.INGREDIENTS[ing]
            on_burner = next((b for b in burners if b["ingredient"] == ing), None)
            if on_burner and on_burner["status"] == "COOKING":
                waiting = True
                cooking_cells.append(tuple(on_burner["cell"]))
                continue  # in progress; see if another component is actionable
            # not on a burner -> advance this component one step
            if term == _RAW:
                return "act", ToolCall("collect", {"ingredient": ing}), ing_cells.get(ing, [])
            if term == _CHOPPED:
                if have(ing, _RAW):
                    return "act", ToolCall("chop", {"ingredient": ing}), board
                return "act", ToolCall("collect", {"ingredient": ing}), ing_cells.get(ing, [])
            if term == _COOKED:
                if ic.cookable_from == _CHOPPED:
                    if have(ing, _CHOPPED):
                        if free_stove:
                            return "act", ToolCall("cook", {"ingredient": ing}), free_stove
                        waiting = True  # burners busy; wait
                        continue
                    if have(ing, _RAW):
                        return "act", ToolCall("chop", {"ingredient": ing}), board
                    return "act", ToolCall("collect", {"ingredient": ing}), ing_cells.get(ing, [])
                else:  # cookable from RAW
                    if have(ing, _RAW):
                        if free_stove:
                            return "act", ToolCall("cook", {"ingredient": ing}), free_stove
                        waiting = True
                        continue
                    return "act", ToolCall("collect", {"ingredient": ing}), ing_cells.get(ing, [])

        # all components present -> plate
        comps_needed = all(have(i, s) for i, s in recipe.items())
        if comps_needed and plate:
            return "act", ToolCall("plate", {"recipe": target["dish"]}), plate
        if waiting:
            return "wait", (cooking_cells or free_stove or plate)
        return ("none",)


def reference_score(spec, latency_s: float = 0.0, max_turns: int | None = None) -> float:
    """Score of the greedy-EDF oracle on this instance at the given per-decision latency."""
    from .runner import run_episode  # lazy import to avoid a cycle
    res = run_episode(spec, OracleAgent(latency=latency_s), max_turns=max_turns)
    return float(res.report["score_raw"])
