"""Deterministic discrete-event Kitchen Rush engine (RULES.md).

Authority on all game state and time. ``step(calls, think_gs)`` is called once per model
response: it advances the clock by the thinking time (RULES §3.2.3 step 1), then executes
the chained calls in order with fail-fast-commit (§4.6). Latency is load-bearing — the
event sweep that runs on every clock advance can burn food and expire orders while the
model deliberates.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from . import config, scoring
from .procgen import KitchenSpec
from .tools import ToolCall


@dataclass
class Component:
    """A held item: a (ingredient, state) component, or a finished plate (state==PLATE,
    ingredient==recipe name)."""

    ingredient: str
    state: str

    @property
    def is_plate(self) -> bool:
        return self.state == "PLATE"


@dataclass
class CookJob:
    ingredient: str
    start_gs: float
    ready_gs: float
    burn_gs: float
    burned: bool = False

    def status(self, clock: float) -> str:
        if self.burned or clock >= self.burn_gs:
            return config.BURNED
        if clock >= self.ready_gs:
            return "READY"
        return "COOKING"


@dataclass
class Order:
    order_id: str
    dish: str
    arrival_gs: float
    deadline_gs: float
    base_value: float
    status: str = "PENDING"   # PENDING -> ACTIVE -> SERVED | EXPIRED


@dataclass
class Burner:
    cell: tuple[int, int]
    job: CookJob | None = None


@dataclass
class Event:
    type: str
    clock_gs: float
    detail: dict = field(default_factory=dict)


# Invalid-action categories (diagnostic taxonomy, surfaced in final_report). These turn the
# benchmark into a tool that tells you HOW a model fails, not just that it scored low.
INV_EARLY_PICKUP = "early_pickup"     # collect_cooked before the item is READY
INV_WRONG_INVENTORY = "wrong_inventory"  # plate/cook/chop/serve without the right held items
INV_BURNER_FULL = "burner_full"       # cook with every burner busy
INV_EXPIRED_SERVE = "expired_serve"   # serve an order that already expired
INV_BAD_TARGET = "bad_target"         # unknown order/ingredient/recipe, or not on the menu
INV_UNREACHABLE = "unreachable"       # no reachable station of the needed type
INV_MALFORMED = "malformed"           # bad args / unknown tool
INV_CATEGORIES = (INV_EARLY_PICKUP, INV_WRONG_INVENTORY, INV_BURNER_FULL, INV_EXPIRED_SERVE,
                  INV_BAD_TARGET, INV_UNREACHABLE, INV_MALFORMED)


def _new_counters() -> dict[str, Any]:
    return {
        "serves_ok": 0, "invalid_actions": 0, "burns": 0, "expiries": 0, "drops": 0,
        "observe_calls": 0, "total_tool_calls": 0, "chained_turns": 0,
        "chain_partial_failures": 0, "total_think_gs": 0.0, "total_action_gs": 0.0,
        "empty_turns": 0, "max_combo": 0,
        "orders_total": 0, "orders_served": 0, "orders_expired": 0,
        "overflow_calls": 0,                       # calls past MAX_CALLS_PER_RESPONSE (dropped)
        "invalid_by_reason": {c: 0 for c in INV_CATEGORIES},
    }


class KitchenRushEngine:
    """A single playable Kitchen Rush episode."""

    def __init__(self, spec: KitchenSpec, *, record_trace: bool = False) -> None:
        self.spec = spec
        self.grid_n = spec.grid_n
        self.show_ready_actions = spec.show_ready_actions
        self.active_recipes = tuple(spec.active_recipes)
        self.active_ingredients = set(config.recipe_ingredients(self.active_recipes))

        self.stations = {s.cell: s for s in spec.stations}
        self.blocked: set[tuple[int, int]] = set(spec.blocked)   # non-walkable counters / walls
        self.door: tuple[int, int] | None = tuple(spec.door) if spec.door else None
        self.burners: list[Burner] = [
            Burner(cell) for cell in sorted(s.cell for s in spec.stations if s.type == config.STOVE)
        ]

        self.chef_pos: tuple[int, int] = tuple(spec.chef_start)  # type: ignore[assignment]
        self.hands: list[Component] = []

        self.orders: dict[str, Order] = {
            o.order_id: Order(o.order_id, o.dish, o.arrival_gs, o.deadline_gs, o.base_value)
            for o in spec.orders
        }
        self._order_list = list(self.orders.values())

        self.clock_gs = 0.0
        self.score = 0.0
        self.combo_count = 0
        self.terminated = False
        self.turn_count = 0
        self.last_invalid_reason: str | None = None

        self.counters = _new_counters()
        self.counters["orders_total"] = len(self.orders)
        self.events: list[Event] = []
        self._events_since_last: list[Event] = []
        self._last_turn: dict[str, Any] = {}
        self._finalized = False
        self._no_progress_turns = 0    # consecutive turns doing no productive work (stall guard)
        self._turn_productive = False  # set by _charge/_charge_serve when real work happens

        # Opt-in fine-grained replay trace (one frame per think-window and per action). Off by
        # default so benchmark runs pay nothing; the replay exporter turns it on.
        self.record_trace = record_trace
        self.trace: list[dict[str, Any]] = []
        self._frame_cursor = 0         # index into self.events already attached to a frame

        self._record("game_start", {})
        self.advance(0.0)  # fire any arrivals scheduled at t=0
        self._emit_frame("start")

    # -- grid helpers ---------------------------------------------------------
    def _in_bounds(self, cell: tuple[int, int]) -> bool:
        r, c = cell
        return 0 <= r < self.grid_n and 0 <= c < self.grid_n

    def _is_floor(self, cell: tuple[int, int]) -> bool:
        return self._in_bounds(cell) and cell not in self.stations and cell not in self.blocked

    def _adjacent_stations(self, type_: str, ingredient: str | None = None) -> list[tuple[int, int]]:
        r, c = self.chef_pos
        out = []
        for dr, dc in config.DIRECTIONS.values():
            cell = (r + dr, c + dc)
            st = self.stations.get(cell)
            if st and st.type == type_ and (ingredient is None or st.ingredient == ingredient):
                out.append(cell)
        return out

    def _near(self, type_: str, ingredient: str | None = None) -> bool:
        return bool(self._adjacent_stations(type_, ingredient))

    # -- event sweep (RULES §3.4, §11.5) --------------------------------------
    def advance(self, dt: float, *, exempt_order: str | None = None) -> None:
        """Advance the clock by dt (>=0), firing passive events in time + tie-break order."""
        if dt < 0:
            raise ValueError("clock cannot move backwards")
        start = self.clock_gs
        target = start + dt
        evs: list[tuple[float, int, str, str, Any]] = []  # (time, category, id_key, kind, ref)
        for o in self._order_list:
            # arrival fires once the clock reaches arrival_gs (inclusive; handles t=0 arrivals)
            if o.status == "PENDING" and o.arrival_gs <= target:
                evs.append((min(target, max(o.arrival_gs, start)), 3, o.order_id, "arrival", o))
            if o.status in ("PENDING", "ACTIVE") and o.order_id != exempt_order \
                    and start < o.deadline_gs <= target:
                evs.append((o.deadline_gs, 1, o.order_id, "expiry", o))
        for idx, b in enumerate(self.burners):
            job = b.job
            if job and not job.burned:
                if self.clock_gs < job.ready_gs <= target:
                    evs.append((job.ready_gs, 4, f"{idx:03d}", "ready", idx))
                if self.clock_gs < job.burn_gs <= target:
                    evs.append((job.burn_gs, 2, f"{idx:03d}", "burn", idx))
        evs.sort(key=lambda e: (e[0], e[1], e[2]))

        for time_, _cat, _idk, kind, ref in evs:
            self.clock_gs = time_
            if kind == "arrival":
                ref.status = "ACTIVE"
                self._record("order_arrived", {"order_id": ref.order_id, "dish": ref.dish})
            elif kind == "expiry":
                if ref.status == "ACTIVE":
                    ref.status = "EXPIRED"
                    self.score += scoring.expiry_penalty(ref.base_value)
                    self.combo_count = 0
                    self.counters["expiries"] += 1
                    self.counters["orders_expired"] += 1
                    self._record("order_expired", {"order_id": ref.order_id})
            elif kind == "burn":
                b = self.burners[ref]
                if b.job and not b.job.burned:
                    self.score += config.BURN_PENALTY
                    self.combo_count = 0
                    self.counters["burns"] += 1
                    self._record("burned", {"burner_index": ref, "ingredient": b.job.ingredient})
                    b.job = None   # auto-free: the burnt item is binned automatically, burner reopens
            elif kind == "ready":
                b = self.burners[ref]
                if b.job:
                    self._record("cook_ready", {"burner_index": ref, "ingredient": b.job.ingredient})
        self.clock_gs = target

    def _check_terminate(self) -> bool:
        # The primary terminator is game-time (the horizon); orders_exhausted ends it early when
        # everything is resolved. The per-turn ceiling lives in run_episode (and the stall guard
        # in step), NOT here — so a generous reference budget is not capped at MAX_TURNS.
        if self.terminated:
            return True
        if self.clock_gs >= self.spec.horizon_gs:
            self.terminated = True
            self._record("terminated", {"reason": "horizon"})
        elif all(o.status in ("SERVED", "EXPIRED") for o in self._order_list):
            self.terminated = True
            self._record("terminated", {"reason": "orders_exhausted"})
        return self.terminated

    # -- turn entry point (RULES §3.2.3) --------------------------------------
    def step(self, calls: list[ToolCall], think_gs: float = 0.0) -> dict[str, Any]:
        if self.terminated:
            return {**self.observe(), "ok": False}
        self._events_since_last = []
        self.turn_count += 1
        self._turn_productive = False
        results: list[dict[str, Any]] = []
        aborted: list[str] = []
        failed_at: int | None = None

        # 1. thinking time advances the world first
        self.counters["total_think_gs"] += think_gs
        self.advance(think_gs)
        # Replay: a "think" frame captures the latency cost — the clock jumped and any food may
        # have burned / orders expired while the model deliberated, before it acted.
        self._emit_frame("think", think_gs=think_gs)

        capped = calls[: config.MAX_CALLS_PER_RESPONSE]
        overflow = [c.name for c in calls[config.MAX_CALLS_PER_RESPONSE:]]  # NOT silently dropped
        if overflow:
            self.counters["overflow_calls"] += len(overflow)
        if len(capped) > 1:
            self.counters["chained_turns"] += 1
        if not capped:
            self.counters["empty_turns"] += 1

        if not self._check_terminate():
            for i, call in enumerate(capped):
                if self.terminated:
                    aborted.extend(c.name for c in capped[i:])
                    break
                self.counters["total_tool_calls"] += 1
                res = self._exec(call)
                res["call"] = call.name          # keep the attempted tool name (esp. for invalids)
                results.append(res)
                self._emit_frame("action", action={
                    "name": call.name, "arguments": call.arguments,
                    "ok": res["ok"], "note": res.get("note", ""),
                    "category": res.get("category"),
                })
                if not res["ok"]:
                    failed_at = i
                    self.counters["chain_partial_failures"] += 1 if len(capped) > 1 else 0
                    aborted.extend(c.name for c in capped[i + 1 :])
                    break
                self._check_terminate()

        self.counters["max_combo"] = max(self.counters["max_combo"], self.combo_count)
        # Stall guard: end an episode that burns turns without doing productive work (no-op
        # moves, repeated invalids, empty responses) — the safety net so the game-time horizon,
        # not a small turn cap, is the real limit. Any successful action resets it.
        if self._turn_productive:
            self._no_progress_turns = 0
        else:
            self._no_progress_turns += 1
            if self._no_progress_turns >= config.STALL_TURNS and not self.terminated:
                self.terminated = True
                self._record("terminated", {"reason": "stalled"})
        self._last_turn = {
            "think_gs": round(think_gs, 4),
            "calls": results,
            "aborted_calls": aborted,
            "failed_at_index": failed_at,
            "overflow_dropped": overflow,   # calls past the per-response cap (not executed)
        }
        return self.observe()

    # -- action dispatch ------------------------------------------------------
    def _exec(self, call: ToolCall) -> dict[str, Any]:
        handler = {
            "move_to": self._a_move_to, "observe": self._a_observe, "collect": self._a_collect,
            "chop": self._a_chop, "prep": self._a_chop, "cook": self._a_cook,
            "collect_cooked": self._a_collect_cooked, "plate": self._a_plate,
            "serve": self._a_serve, "discard": self._a_discard,
        }.get(call.name)
        if handler is None:
            return self._invalid(f"unknown tool {call.name!r}", INV_MALFORMED)
        try:
            return handler(call.arguments or {})
        except (KeyError, TypeError, ValueError) as exc:
            return self._invalid(f"malformed call {call.name}: {exc}", INV_MALFORMED)

    def _ok(self, action: str, note: str) -> dict[str, Any]:
        self.last_invalid_reason = None
        self._record(action, {"note": note})
        return {"ok": True, "action": action, "note": note}

    def _invalid(self, reason: str, category: str = INV_MALFORMED) -> dict[str, Any]:
        self.advance(config.INVALID_GS)
        self.counters["invalid_actions"] += 1
        self.counters["invalid_by_reason"][category] = \
            self.counters["invalid_by_reason"].get(category, 0) + 1
        self.counters["total_action_gs"] += config.INVALID_GS
        self.score += config.INVALID_PENALTY
        self.combo_count = 0
        self.last_invalid_reason = reason
        self._record("invalid", {"reason": reason, "category": category})
        return {"ok": False, "action": "invalid", "note": reason, "category": category}

    def _charge(self, gs: float) -> None:
        self.counters["total_action_gs"] += gs
        self._turn_productive = True   # a successful action ran (resets the stall guard)
        self.advance(gs)

    def _held(self, ingredient: str, state: str) -> Component | None:
        for h in self.hands:
            if not h.is_plate and h.ingredient == ingredient and h.state == state:
                return h
        return None

    def _shortest(self, start: tuple[int, int], goal: tuple[int, int]) -> int | None:
        """BFS shortest-path length over floor cells (None if unreachable)."""
        if start == goal:
            return 0
        seen = {start}
        q = deque([(start, 0)])
        while q:
            cur, dist = q.popleft()
            for dr, dc in config.DIRECTIONS.values():
                nb = (cur[0] + dr, cur[1] + dc)
                if nb in seen or not self._is_floor(nb):
                    continue
                if nb == goal:
                    return dist + 1
                seen.add(nb)
                q.append((nb, dist + 1))
        return None

    def _a_move_to(self, args: dict) -> dict[str, Any]:
        try:
            r = int(args.get("row"))
            c = int(args.get("col"))
        except (TypeError, ValueError):
            return self._invalid("row and col must be integers", INV_MALFORMED)
        target = (r, c)
        if not self._in_bounds(target):
            return self._invalid(f"cell [{r}, {c}] is off the grid", INV_BAD_TARGET)
        if target in self.stations:
            # "Walk up to the counter": aiming at a station means stand on the nearest
            # reachable floor cell beside it (move_to already does the pathfinding). This is
            # a navigation affordance, not a hint — the chef still chooses which station.
            adj = self._nearest_access_cell(target)
            if adj is None:
                return self._invalid(f"no reachable floor cell next to station [{r}, {c}]", INV_UNREACHABLE)
            target = adj
        if target == self.chef_pos:
            return self._ok("move_to", "already there")
        dist = self._shortest(self.chef_pos, target)
        if dist is None:
            return self._invalid(f"cell [{r}, {c}] is unreachable", INV_UNREACHABLE)
        self._charge(dist * config.MOVE_GS_PER_STEP)
        self.chef_pos = target
        suffix = f" (next to [{r}, {c}])" if target != (r, c) else ""
        return self._ok("move_to", f"moved to {list(target)} ({dist} steps){suffix}")

    def _nearest_access_cell(self, station: tuple[int, int]) -> tuple[int, int] | None:
        """The reachable floor cell orthogonally adjacent to ``station`` that is closest
        (fewest steps) to the chef; the chef's current cell if it is already adjacent."""
        best: tuple[int, int] | None = None
        best_dist: int | None = None
        for dr, dc in config.DIRECTIONS.values():
            cell = (station[0] + dr, station[1] + dc)
            if not self._is_floor(cell):
                continue
            dist = 0 if cell == self.chef_pos else self._shortest(self.chef_pos, cell)
            if dist is None:
                continue
            if best_dist is None or dist < best_dist:
                best, best_dist = cell, dist
        return best

    def _walk_to_cells(self, station_cells) -> bool:
        """Auto-navigation: walk to the nearest reachable floor cell adjacent to any of
        ``station_cells`` (charging the travel game-time), so a station action need not be
        preceded by an explicit move_to. Returns True if the chef ends up adjacent (incl. if
        it already was). Movement still costs time, so the grid layout still matters; this only
        removes the brittle move->act split, not any strategic choice."""
        best: tuple[int, int] | None = None
        best_dist: int | None = None
        for cell in station_cells:
            access = self._nearest_access_cell(cell)
            if access is None:
                continue
            dist = 0 if access == self.chef_pos else self._shortest(self.chef_pos, access)
            if dist is None:
                continue
            if best_dist is None or dist < best_dist:
                best, best_dist = access, dist
        if best is None:
            return False
        if best != self.chef_pos:
            self._charge(best_dist * config.MOVE_GS_PER_STEP)
            self.chef_pos = best
        return True

    def _walk_to(self, type_: str, ingredient: str | None = None) -> bool:
        """Walk to the nearest station of ``type_`` (optionally matching ``ingredient``)."""
        cells = [c for c, s in self.stations.items()
                 if s.type == type_ and (ingredient is None or s.ingredient == ingredient)]
        return self._walk_to_cells(cells)

    def _a_observe(self, args: dict) -> dict[str, Any]:
        self.counters["observe_calls"] += 1
        self._charge(config.OBSERVE_GS)
        return self._ok("observe", "looked around")

    def _a_collect(self, args: dict) -> dict[str, Any]:
        ing = args.get("ingredient")
        if ing not in self.active_ingredients:
            return self._invalid(f"{ing} not used in this kitchen", INV_BAD_TARGET)
        if len(self.hands) >= config.HAND_SLOTS:
            return self._invalid("hands full", INV_WRONG_INVENTORY)
        if not self._walk_to(config.ING, ing):
            return self._invalid(f"cannot reach a {ing} dispenser", INV_UNREACHABLE)
        self._charge(config.COLLECT_GS)
        self.hands.append(Component(ing, config.RAW))
        return self._ok("collect", f"collected {ing}")

    def _a_chop(self, args: dict) -> dict[str, Any]:
        ing = args.get("ingredient")
        if ing not in config.INGREDIENTS or not config.INGREDIENTS[ing].choppable:
            return self._invalid(f"{ing} is not choppable", INV_BAD_TARGET)
        item = self._held(ing, config.RAW)
        if item is None:
            return self._invalid(f"not holding raw {ing}", INV_WRONG_INVENTORY)
        if not self._walk_to(config.BOARD):
            return self._invalid("cannot reach a cutting board", INV_UNREACHABLE)
        self._charge(config.CHOP_GS)
        item.state = config.CHOPPED
        return self._ok("chop", f"chopped {ing}")

    def _a_cook(self, args: dict) -> dict[str, Any]:
        ing = args.get("ingredient")
        ic = config.INGREDIENTS.get(ing)
        if ic is None or ic.cookable_from is None:
            return self._invalid(f"{ing} is not cookable", INV_BAD_TARGET)
        item = self._held(ing, ic.cookable_from)
        if item is None:
            return self._invalid(f"need {ing} in state {ic.cookable_from} to cook", INV_WRONG_INVENTORY)
        free_cells = {b.cell for b in self.burners if b.job is None}
        if not free_cells:
            return self._invalid("all burners are busy", INV_BURNER_FULL)
        if not self._walk_to_cells(free_cells):
            return self._invalid("cannot reach a free stove", INV_UNREACHABLE)
        free = [i for i, c in enumerate(self.burners) if c.cell in self._adjacent_stations(config.STOVE) and c.job is None]
        if not free:
            return self._invalid("no free burner here", INV_BURNER_FULL)
        self._charge(config.COOK_START_GS)
        self.hands.remove(item)
        idx = free[0]
        self.burners[idx].job = CookJob(
            ing, self.clock_gs, self.clock_gs + ic.cook_time, self.clock_gs + ic.cook_time + ic.burn_window
        )
        return self._ok("cook", f"cooking {ing} on burner {idx}")

    def _a_collect_cooked(self, args: dict) -> dict[str, Any]:
        ing = args.get("ingredient")
        if len(self.hands) >= config.HAND_SLOTS:
            return self._invalid("hands full", INV_WRONG_INVENTORY)
        want = args.get("burner_index")
        # stoves carrying a ready/burned matching item (status read before any travel)
        target_cells = {
            b.cell for i, b in enumerate(self.burners)
            if b.job and b.job.ingredient == ing
            and b.job.status(self.clock_gs) in ("READY", config.BURNED)
            and (want is None or i == want)
        }
        if not target_cells:
            # distinguish "grabbed too early" (it's still cooking) from "nothing there"
            still_cooking = any(
                b.job and b.job.ingredient == ing and b.job.status(self.clock_gs) == "COOKING"
                and (want is None or i == want)
                for i, b in enumerate(self.burners)
            )
            if still_cooking:
                return self._invalid(f"{ing} is not ready yet (still cooking)", INV_EARLY_PICKUP)
            return self._invalid(f"no ready/burned {ing} on a burner", INV_BAD_TARGET)
        if not self._walk_to_cells(target_cells):
            return self._invalid(f"cannot reach the stove with {ing}", INV_UNREACHABLE)
        adj = set(self._adjacent_stations(config.STOVE))
        candidates = [
            i for i, b in enumerate(self.burners)
            if b.cell in adj and b.job and b.job.ingredient == ing
            and b.job.status(self.clock_gs) in ("READY", config.BURNED)
            and (want is None or i == want)
        ]
        if not candidates:
            return self._invalid(f"no ready/burned {ing} on a burner here", INV_EARLY_PICKUP)
        idx = candidates[0]
        job = self.burners[idx].job
        assert job is not None
        status = job.status(self.clock_gs)
        self._charge(config.COOK_PICKUP_GS)
        self.burners[idx].job = None
        state = config.BURNED if status == config.BURNED else config.COOKED
        self.hands.append(Component(ing, state))
        return self._ok("collect_cooked", f"took {state.lower()} {ing} off burner {idx}")

    def _a_plate(self, args: dict) -> dict[str, Any]:
        recipe = args.get("recipe")
        if recipe not in self.active_recipes:
            return self._invalid(f"{recipe} not on the menu", INV_BAD_TARGET)
        required = dict(config.RECIPES[recipe])
        comps = [h for h in self.hands if not h.is_plate]
        have: dict[tuple[str, str], int] = {}
        for c in comps:
            have[(c.ingredient, c.state)] = have.get((c.ingredient, c.state), 0) + 1
        need = {(i, s): 1 for i, s in required.items()}
        if have != need:
            return self._invalid(f"held items don't exactly match {recipe} (no extra/missing components allowed)",
                                 INV_WRONG_INVENTORY)
        if not self._walk_to(config.PLATE):
            return self._invalid("cannot reach a plating counter", INV_UNREACHABLE)
        self._charge(config.PLATE_GS)
        for i, s in required.items():
            self.hands.remove(self._held(i, s))  # type: ignore[arg-type]
        self.hands.append(Component(recipe, "PLATE"))
        return self._ok("plate", f"plated {recipe}")

    def _a_serve(self, args: dict) -> dict[str, Any]:
        order_id = args.get("order_id")
        order = self.orders.get(order_id)
        if order is None:
            return self._invalid(f"no such order {order_id!r}", INV_BAD_TARGET)
        if order.status != "ACTIVE":
            return self._invalid(f"order {order_id} is {order.status}, not ACTIVE", INV_EXPIRED_SERVE)
        plate = next((h for h in self.hands if h.is_plate and h.ingredient == order.dish), None)
        if plate is None:
            return self._invalid(f"not holding a plated {order.dish}", INV_WRONG_INVENTORY)
        if not self._walk_to(config.PASS):
            return self._invalid("cannot reach the pass", INV_UNREACHABLE)
        if order.status != "ACTIVE":  # the order may have expired while walking to the pass
            return self._invalid(f"order {order_id} expired before you reached the pass", INV_EXPIRED_SERVE)
        self._charge_serve(order_id)
        tf = scoring.time_factor(self.clock_gs, order.arrival_gs, order.deadline_gs)
        qualifies = config.recipe_n_steps(order.dish) >= config.COMBO_MIN_STEPS
        if qualifies:
            self.combo_count += 1
            eff_streak = self.combo_count
        else:
            eff_streak = min(self.combo_count, 1)  # cheap dishes can't build the combo
        earned = scoring.delivery_reward(order.base_value, tf, scoring.combo_multiplier(eff_streak))
        self.score += earned
        self.hands.remove(plate)
        order.status = "SERVED"
        self.counters["serves_ok"] += 1
        self.counters["orders_served"] += 1
        return self._ok("serve", f"served {order_id} ({order.dish}) +{earned}")

    def _charge_serve(self, order_id: str) -> None:
        self.counters["total_action_gs"] += config.SERVE_GS
        self._turn_productive = True
        self.advance(config.SERVE_GS, exempt_order=order_id)

    def _a_discard(self, args: dict) -> dict[str, Any]:
        item = args.get("item")
        held = next((h for h in self.hands if h.ingredient == item or (h.is_plate and item == f"plate:{h.ingredient}")), None)
        if held is None:
            return self._invalid(f"not holding {item!r}", INV_WRONG_INVENTORY)
        if not self._walk_to(config.BIN):
            return self._invalid("cannot reach the bin", INV_UNREACHABLE)
        self._charge(config.DISCARD_GS)
        self.hands.remove(held)
        if held.state != config.BURNED:
            self.score += config.DROP_PENALTY
            self.counters["drops"] += 1
            return self._ok("discard", f"discarded {item} (penalty)")
        return self._ok("discard", f"discarded burned {item}")

    # -- replay trace (per-action snapshots for the UI) -----------------------
    def _snapshot(self) -> dict[str, Any]:
        """Compact full-state snapshot at the current clock — everything the replay UI needs
        to redraw the kitchen. Unlike ``observe()`` this lists ALL orders (incl. served/expired)
        so the ticket rail can animate status changes, and exposes the burner cook timers."""
        return {
            "clock_gs": round(self.clock_gs, 3),
            "score": round(self.score, 3),
            "combo": self.combo_count,
            "chef_pos": list(self.chef_pos),
            "hands": [{"ingredient": h.ingredient, "state": h.state} for h in self.hands],
            "burners": [
                {
                    "index": i, "cell": list(b.cell),
                    "status": b.job.status(self.clock_gs) if b.job else "FREE",
                    "ingredient": b.job.ingredient if b.job else None,
                    "start_gs": round(b.job.start_gs, 3) if b.job else None,
                    "ready_gs": round(b.job.ready_gs, 3) if b.job else None,
                    "burn_gs": round(b.job.burn_gs, 3) if b.job else None,
                }
                for i, b in enumerate(self.burners)
            ],
            "orders": [
                {
                    "order_id": o.order_id, "dish": o.dish, "status": o.status,
                    "arrival_gs": o.arrival_gs, "deadline_gs": o.deadline_gs,
                    "base_value": o.base_value,
                }
                for o in self._order_list
            ],
        }

    def _emit_frame(self, kind: str, *, think_gs: float | None = None,
                    action: dict | None = None) -> None:
        """Append one replay frame: the current snapshot plus the passive/action events fired
        since the previous frame (arrivals, ready, burns, expiries, serves). No-op unless
        ``record_trace`` is on."""
        if not self.record_trace:
            return
        new_events = [
            {"type": e.type, "clock_gs": e.clock_gs, "detail": e.detail}
            for e in self.events[self._frame_cursor:]
        ]
        self._frame_cursor = len(self.events)
        frame: dict[str, Any] = {"kind": kind, "turn": self.turn_count,
                                 **self._snapshot(), "events": new_events}
        if think_gs is not None:
            frame["think_gs"] = round(think_gs, 4)
        if action is not None:
            frame["action"] = action
        self.trace.append(frame)

    def emit_end_frame(self) -> None:
        """Emit the terminal frame after ``final_report`` (captures end-of-game force-expiries
        and the final score). Called by the runner once, after the episode is finalized."""
        self._emit_frame("end")

    # -- observation (RULES §8) ----------------------------------------------
    def _record(self, type_: str, detail: dict) -> None:
        ev = Event(type_, round(self.clock_gs, 4), detail)
        self.events.append(ev)
        self._events_since_last.append(ev)

    def _grid_ascii(self) -> str:
        sym = {config.ING: "I", config.BOARD: "B", config.STOVE: "S",
               config.PLATE: "P", config.PASS: "R", config.BIN: "X"}
        rows = []
        for r in range(self.grid_n):
            line = []
            for c in range(self.grid_n):
                if (r, c) == self.chef_pos:
                    line.append("@")
                elif (r, c) in self.stations:
                    line.append(sym[self.stations[(r, c)].type])
                elif self.door and (r, c) == self.door:
                    line.append("D")
                elif (r, c) in self.blocked:
                    line.append("#")
                else:
                    line.append(".")
            rows.append("".join(line))
        return "\n".join(rows)

    def ready_actions(self) -> list[str]:
        if not self.show_ready_actions:
            return []
        out: list[str] = []
        r, c = self.chef_pos
        for dr, dc in config.DIRECTIONS.values():
            st = self.stations.get((r + dr, c + dc))
            if not st:
                continue
            if st.type == config.ING:
                out.append(f"collect({st.ingredient})")
            elif st.type == config.PASS:
                for h in self.hands:
                    if h.is_plate:
                        for o in self._order_list:
                            if o.status == "ACTIVE" and o.dish == h.ingredient:
                                out.append(f"serve({o.order_id})")
            elif st.type == config.PLATE:
                out.append("plate(<recipe>)")
        return sorted(set(out))

    def observe(self) -> dict[str, Any]:
        return {
            "ok": True,
            "clock_gs": round(self.clock_gs, 4),
            "horizon_gs": self.spec.horizon_gs,
            "remaining_gs": round(self.spec.horizon_gs - self.clock_gs, 4),
            "chef_pos": list(self.chef_pos),
            "grid_ascii": self._grid_ascii(),
            "grid_legend": "@=you I=dispenser B=board S=stove P=plate R=pass X=bin #=wall/counter D=door .=floor",
            "hands": [{"ingredient": h.ingredient, "state": h.state} for h in self.hands],
            "hand_slots_free": config.HAND_SLOTS - len(self.hands),
            "stations": [
                {"type": s.type, "ingredient": s.ingredient, "cell": list(s.cell)}
                for s in self.spec.stations
            ],
            "burners": [
                {
                    "burner_index": i, "cell": list(b.cell),
                    "status": b.job.status(self.clock_gs) if b.job else "FREE",
                    "ingredient": b.job.ingredient if b.job else None,
                    "ready_gs": round(b.job.ready_gs, 4) if b.job else None,
                    "burn_gs": round(b.job.burn_gs, 4) if b.job else None,
                }
                for i, b in enumerate(self.burners)
            ],
            "burner_summary": {"active": sum(1 for b in self.burners if b.job), "max": len(self.burners)},
            "orders": [
                {
                    "order_id": o.order_id, "dish": o.dish, "status": o.status,
                    "arrival_gs": o.arrival_gs, "deadline_gs": o.deadline_gs,
                    "gs_remaining": round(o.deadline_gs - self.clock_gs, 4),
                    "base_value": o.base_value,
                }
                for o in self._order_list
                if o.status in ("ACTIVE", "PENDING")
            ],
            "last_turn": self._last_turn,
            "events_since_last": [
                {"type": e.type, "clock_gs": e.clock_gs, "detail": e.detail}
                for e in self._events_since_last
            ],
            "score": round(self.score, 4),
            "combo_count": self.combo_count,
            "ready_actions": self.ready_actions(),
            "last_invalid_reason": self.last_invalid_reason,
            "terminated": self.terminated,
        }

    # -- final report (RULES §13.6) -------------------------------------------
    def final_report(self) -> dict[str, Any]:
        # Truncation-invariance: any order still unresolved at episode end (e.g. the run was
        # cut by MAX_TURNS before its deadline) counts as a MISS. Otherwise a fast agent can
        # dodge expiry penalties by running out of turns and beat the null floor without
        # serving anything (spurious positive KR). Keeps scoring consistent with S_null
        # (which assumes all unserved orders expire). Idempotent.
        if not self._finalized:
            self._finalized = True
            for o in self._order_list:
                if o.status in ("PENDING", "ACTIVE"):
                    o.status = "EXPIRED"
                    self.score += scoring.expiry_penalty(o.base_value)
                    self.combo_count = 0
                    self.counters["expiries"] += 1
                    self.counters["orders_expired"] += 1
                    self._record("force_expired_end", {"order_id": o.order_id})
        return {
            "seed": self.spec.seed,
            "tier": self.spec.tier,
            "score_raw": round(self.score, 4),
            "score_display": round(max(0.0, self.score), 4),
            "clock_gs": round(self.clock_gs, 4),
            "horizon_gs": self.spec.horizon_gs,
            "turns": self.turn_count,
            "terminated": self.terminated,
            "counters": dict(self.counters),
            "orders": [
                {"order_id": o.order_id, "dish": o.dish, "status": o.status}
                for o in self._order_list
            ],
        }
