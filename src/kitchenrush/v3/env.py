"""Unified Kitchen Rush v3 engine, parametric difficulty generator, and native tool schemas.

Design goals:
- One engine and one parametric generator across all difficulty levels D >= 1.
- Procedural per-kitchen ingredient rules ("infinite kitchen" anti-memorization).
- 8 clean native tools (no move_to trap; universal integer wait 1..120s).
- Batched tool-call execution with event-driven interrupts on unforeseen events.
- Provable impossible-deadline early termination to avoid wasting budget on doomed runs.
- Deterministic replay verification of every episode.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import config
from ..engine import (
    INV_BAD_TARGET,
    INV_EARLY_PICKUP,
    INV_MALFORMED,
    INV_UNREACHABLE,
    INV_WRONG_INVENTORY,
    Component,
    KitchenRushEngine,
)
from ..procgen import KitchenSpec, OrderSpec, StationSpec
from ..tools import ToolCall

PROTOCOL = "kitchen-rush-v3-unified-batched"
MAX_BATCH_CALLS = 8
MAX_DECISIONS = 160
MAX_WAIT_GS = 120
INTERRUPT_EVENTS = frozenset({"order_arrived", "burned", "order_expired"})

COOKABLE_INGREDIENTS = tuple(
    name for name, item in sorted(config.INGREDIENTS.items()) if item.cookable_from is not None
)
NON_COOKABLE_INGREDIENTS = tuple(
    name for name, item in sorted(config.INGREDIENTS.items()) if item.cookable_from is None
)
ALL_INGREDIENTS = tuple(sorted(config.INGREDIENTS.keys()))


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool(
        "collect",
        "Walk to an ingredient dispenser and pick up 1 RAW unit into an empty hand slot.",
        {"ingredient": {"type": "string", "enum": list(ALL_INGREDIENTS)}},
        ["ingredient"],
    ),
    _tool(
        "chop",
        "Walk to a cutting board and chop a held RAW choppable ingredient into CHOPPED.",
        {"ingredient": {"type": "string", "enum": list(ALL_INGREDIENTS)}},
        ["ingredient"],
    ),
    _tool(
        "cook",
        "Walk to a free burner and start cooking a held ingredient (must match ingredient_rules.cookable_from).",
        {"ingredient": {"type": "string", "enum": list(ALL_INGREDIENTS)}},
        ["ingredient"],
    ),
    _tool(
        "collect_cooked",
        "Walk to a burner with a READY item of this ingredient and lift it into an empty hand slot.",
        {
            "ingredient": {"type": "string", "enum": list(ALL_INGREDIENTS)},
            "burner_index": {"type": "integer", "minimum": 0},
        },
        ["ingredient"],
    ),
    _tool(
        "plate",
        "Walk to the plating counter and plate a recipe using held items (exact match required; no extra unplated items).",
        {"recipe": {"type": "string"}},
        ["recipe"],
    ),
    _tool(
        "serve",
        "Walk to the serving pass and serve a held plated dish matching an ACTIVE order.",
        {"order_id": {"type": "string"}},
        ["order_id"],
    ),
    _tool(
        "discard",
        "Walk to the bin and throw away a held ingredient or plate (e.g. 'patty' or 'plate:R1').",
        {"item": {"type": "string"}},
        ["item"],
    ),
    _tool(
        "wait",
        f"Advance the kitchen clock by an integer number of game-seconds (1 to {MAX_WAIT_GS}) while cooking or awaiting orders.",
        {"seconds": {"type": "integer", "minimum": 1, "maximum": MAX_WAIT_GS}},
        ["seconds"],
    ),
]

RULES_TEXT = (
    "You are the sole chef in a turn-based kitchen. Maximize served orders cleanly while minimizing "
    "invalid actions, food burns, and wasted calls.\n"
    "- Inference pauses game time; only executed tool actions advance the clock.\n"
    "- All station actions automatically walk to the required station (0.15s per floor step). "
    "There is no separate move tool.\n"
    "- You may emit 1 to 8 sequential tool calls in a single turn to execute a planned sequence. "
    "A batch stops early (discarding remaining queued calls) if an action is invalid, a new order arrives, "
    "food burns, or an order expires.\n"
    "- Read this kitchen's ingredient_rules carefully! Every kitchen randomizes cook_time, burn_window, "
    "and whether each cookable ingredient must be RAW or CHOPPED before cooking.\n"
    "- You have 4 hand slots. `plate` requires holding the exact ingredients/states for that recipe with "
    "no extra unplated components (already-plated dishes in hand are allowed).\n"
    "- `collect_cooked` requires the item to be READY before walking; pickup lifts it off heat on arrival "
    "(before the 1s handling time), though it can still burn during travel if cut too close.\n"
    "- Burned food automatically disappears from its burner. Invalid actions cost 3 game-seconds.\n"
    "- Future orders are hidden until arrival. Episode ends when all orders resolve, no remaining order "
    "can mathematically meet its deadline, at 160 tool actions, or after 12 consecutive nonproductive turns."
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=list).encode()
    ).hexdigest()


@dataclass(frozen=True)
class DifficultyProfile:
    level: int
    n_orders: int
    n_burners: int
    grid_n: int
    min_components: int
    max_components: int
    min_cooked: int
    max_cooked: int
    cook_time_range: tuple[int, int]
    burn_window_range: tuple[int, int]
    initial_active: int
    arrival_gap_range: tuple[int, int]
    deadline_slack: float
    min_deadline_pad: float
    shuffle_stations: bool


def difficulty_profile(level: int) -> DifficultyProfile:
    """Single smooth, monotonic complexity dial D >= 1 (extensible past D=5)."""
    if level < 1:
        raise ValueError("difficulty level must be >= 1")
    table = {
        1: DifficultyProfile(
            level=1, n_orders=2, n_burners=2, grid_n=7,
            min_components=2, max_components=3, min_cooked=1, max_cooked=1,
            cook_time_range=(8, 14), burn_window_range=(14, 18),
            initial_active=2, arrival_gap_range=(0, 0),
            deadline_slack=0.70, min_deadline_pad=18.0, shuffle_stations=False,
        ),
        2: DifficultyProfile(
            level=2, n_orders=3, n_burners=3, grid_n=7,
            min_components=3, max_components=3, min_cooked=1, max_cooked=2,
            cook_time_range=(18, 28), burn_window_range=(12, 16),
            initial_active=3, arrival_gap_range=(0, 0),
            deadline_slack=0.22, min_deadline_pad=9.0, shuffle_stations=False,
        ),
        3: DifficultyProfile(
            level=3, n_orders=4, n_burners=3, grid_n=7,
            min_components=3, max_components=3, min_cooked=1, max_cooked=2,
            cook_time_range=(22, 36), burn_window_range=(10, 14),
            initial_active=3, arrival_gap_range=(14, 22),
            deadline_slack=0.14, min_deadline_pad=7.0, shuffle_stations=True,
        ),
        4: DifficultyProfile(
            level=4, n_orders=5, n_burners=4, grid_n=7,
            min_components=3, max_components=4, min_cooked=2, max_cooked=2,
            cook_time_range=(26, 44), burn_window_range=(9, 12),
            initial_active=3, arrival_gap_range=(16, 26),
            deadline_slack=0.09, min_deadline_pad=5.0, shuffle_stations=True,
        ),
        5: DifficultyProfile(
            level=5, n_orders=6, n_burners=4, grid_n=7,
            min_components=3, max_components=4, min_cooked=2, max_cooked=2,
            cook_time_range=(32, 54), burn_window_range=(8, 11),
            initial_active=4, arrival_gap_range=(16, 24),
            deadline_slack=0.04, min_deadline_pad=3.0, shuffle_stations=True,
        ),
    }
    if level in table:
        return table[level]
    # Smooth open-ended scaling for D >= 6 (chessboard-style unlimited headroom)
    extra = level - 5
    return DifficultyProfile(
        level=level,
        n_orders=6 + extra,
        n_burners=min(6, 4 + (extra + 1) // 2),
        grid_n=7 if level <= 7 else 9,
        min_components=3,
        max_components=4,
        min_cooked=2,
        max_cooked=2,
        cook_time_range=(32 + 4 * extra, 54 + 6 * extra),
        burn_window_range=(max(5, 8 - extra // 2), max(7, 11 - extra // 2)),
        initial_active=min(5, 4 + extra // 2),
        arrival_gap_range=(16, 26),
        deadline_slack=max(0.02, 0.04 * (0.75 ** extra)),
        min_deadline_pad=max(2.0, 3.0 - 0.5 * extra),
        shuffle_stations=True,
    )


@dataclass
class UnifiedKitchen(KitchenSpec):
    ingredient_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    difficulty: int = 1


def serialize_spec(spec: UnifiedKitchen) -> dict[str, Any]:
    data = asdict(spec)
    data["blocked"] = sorted(spec.blocked)
    return json.loads(json.dumps(data))


def deserialize_spec(data: dict[str, Any]) -> UnifiedKitchen:
    d = dict(data)
    d["stations"] = [
        StationSpec(tuple(s["cell"]), s["type"], s["ingredient"]) for s in d["stations"]
    ]
    d["orders"] = [OrderSpec(**o) for o in d["orders"]]
    d["blocked"] = frozenset(tuple(c) for c in d["blocked"])
    d["active_recipes"] = tuple(d["active_recipes"])
    return UnifiedKitchen(**d)


class UnifiedEngine(KitchenRushEngine):
    """Single deterministic engine supporting all v3 levels, procedural rules, and batched interrupts."""

    def __init__(self, spec: UnifiedKitchen, *, record_trace: bool = False, early_stop: bool = True):
        super().__init__(spec, record_trace=record_trace)
        self.early_stop = early_stop
        self.termination_reason: str | None = None
        profiles = getattr(spec, "ingredient_rules", {})
        if profiles:
            if set(profiles) != set(config.INGREDIENTS):
                raise ValueError("ingredient profile catalog mismatch")
            for name, raw in profiles.items():
                item = config.Ingredient(**raw)
                if item.name != name:
                    raise ValueError(f"ingredient rule name mismatch: {name}")
                if not math.isfinite(item.cook_time) or not math.isfinite(item.burn_window):
                    raise ValueError(f"non-finite timers for {name}")
                self.ingredient_rules[name] = item
        self.active_ingredients = {s.ingredient for s in spec.stations if s.type == config.ING}
        self._action_count = 0

    def _exec(self, call: ToolCall) -> dict[str, Any]:
        if call.name == "wait":
            seconds = call.arguments.get("seconds") if isinstance(call.arguments, dict) else None
            if type(seconds) is not int or not (1 <= seconds <= MAX_WAIT_GS):
                return self._invalid(f"wait seconds must be an integer in 1..{MAX_WAIT_GS}", INV_MALFORMED)
            self._charge(float(seconds))
            return self._ok("wait", f"waited {seconds}s")
        if call.name in ("move_to", "observe", "prep"):
            return self._invalid(f"tool {call.name!r} is not part of the v3 toolset", INV_MALFORMED)
        return super()._exec(call)

    def _a_collect_cooked(self, args: dict[str, Any]) -> dict[str, Any]:
        ingredient = args.get("ingredient")
        want = args.get("burner_index")
        if want is not None and type(want) is not int:
            return self._invalid("burner_index must be an integer", INV_MALFORMED)
        if len(self.hands) >= config.HAND_SLOTS:
            return self._invalid("hands full", INV_WRONG_INVENTORY)

        def matches(index: int, burner: Any) -> bool:
            return (
                burner.job is not None
                and burner.job.ingredient == ingredient
                and (want is None or index == want)
            )

        targets = {
            b.cell
            for i, b in enumerate(self.burners)
            if matches(i, b) and b.job.status(self.clock_gs) == "READY"
        }
        if not targets:
            cooking = any(
                matches(i, b) and b.job.status(self.clock_gs) == "COOKING"
                for i, b in enumerate(self.burners)
            )
            return self._invalid(
                f"{ingredient} is not ready yet"
                if cooking
                else f"no ready {ingredient} on a burner",
                INV_EARLY_PICKUP if cooking else INV_BAD_TARGET,
            )
        if not self._walk_to_cells(targets):
            return self._invalid(f"cannot reach the stove with {ingredient}", INV_UNREACHABLE)
        adjacent = set(self._adjacent_stations(config.STOVE))
        for i, burner in enumerate(self.burners):
            if (
                burner.cell in adjacent
                and matches(i, burner)
                and burner.job.status(self.clock_gs) == "READY"
            ):
                # Lift off heat immediately on arrival before charging 1s handling time.
                burner.job = None
                self.hands.append(Component(ingredient, config.COOKED))
                self._charge(config.COOK_PICKUP_GS)
                return self._ok("collect_cooked", f"took cooked {ingredient} off burner {i}")
        return self._invalid(f"no ready {ingredient} on a burner here", INV_EARLY_PICKUP)

    def _ok(self, action: str, note: str) -> dict[str, Any]:
        if action == "serve":
            note = note.split(" +", 1)[0]
        if action == "discard":
            note = note.replace(" (penalty)", "")
        return super()._ok(action, note)

    def _min_remaining_serve_start(self, order: Any) -> float:
        """Optimistic lower bound on the earliest game-second this order could start serving.

        Assumes zero walking time, unlimited burners, and unlimited hands — strictly a sound
        lower bound so early-stopping can NEVER cut off a salvageable order.
        """
        # If matching plate is already held, serve can start immediately (at arrival if pending).
        if any(h.is_plate and h.ingredient == order.dish for h in self.hands):
            return max(self.clock_gs, order.arrival_gs)

        card = self.spec.recipe_cards[order.dish]
        held_counts: dict[tuple[str, str], int] = {}
        for h in self.hands:
            if not h.is_plate:
                held_counts[(h.ingredient, h.state)] = held_counts.get((h.ingredient, h.state), 0) + 1

        burner_ready_times: dict[str, list[float]] = {}
        for b in self.burners:
            if b.job is not None and not b.job.burned:
                rem = max(0.0, b.job.ready_gs - self.clock_gs)
                burner_ready_times.setdefault(b.job.ingredient, []).append(rem)
        for times in burner_ready_times.values():
            times.sort()

        action_sum = config.PLATE_GS
        critical_path = config.PLATE_GS
        for ing, req_state in card.items():
            rule = self.ingredient_rules[ing]
            if held_counts.get((ing, req_state), 0) > 0:
                held_counts[(ing, req_state)] -= 1
                continue
            if req_state == config.RAW:
                action_sum += config.COLLECT_GS
                critical_path = max(critical_path, config.COLLECT_GS + config.PLATE_GS)
            elif req_state == config.CHOPPED:
                if held_counts.get((ing, config.RAW), 0) > 0:
                    held_counts[(ing, config.RAW)] -= 1
                    t = config.CHOP_GS
                else:
                    t = config.COLLECT_GS + config.CHOP_GS
                action_sum += t
                critical_path = max(critical_path, t + config.PLATE_GS)
            elif req_state == config.COOKED:
                # Could already be on a burner!
                if burner_ready_times.get(ing):
                    rem_cook = burner_ready_times[ing].pop(0)
                    action_sum += config.COOK_PICKUP_GS
                    critical_path = max(critical_path, rem_cook + config.COOK_PICKUP_GS + config.PLATE_GS)
                else:
                    prep = 0.0
                    if rule.cookable_from == config.CHOPPED:
                        if held_counts.get((ing, config.CHOPPED), 0) > 0:
                            held_counts[(ing, config.CHOPPED)] -= 1
                        elif held_counts.get((ing, config.RAW), 0) > 0:
                            held_counts[(ing, config.RAW)] -= 1
                            prep = config.CHOP_GS
                        else:
                            prep = config.COLLECT_GS + config.CHOP_GS
                    else:
                        if held_counts.get((ing, config.RAW), 0) > 0:
                            held_counts[(ing, config.RAW)] -= 1
                        else:
                            prep = config.COLLECT_GS
                    action_sum += prep + config.COOK_START_GS + config.COOK_PICKUP_GS
                    critical_path = max(
                        critical_path,
                        prep + config.COOK_START_GS + rule.cook_time + config.COOK_PICKUP_GS + config.PLATE_GS,
                    )
        start_time = max(self.clock_gs, order.arrival_gs)
        # Note: if pending, we can pre-prep components before arrival, so only serve waits for arrival.
        if order.status == "PENDING":
            return max(order.arrival_gs, self.clock_gs + max(action_sum, critical_path))
        return start_time + max(action_sum, critical_path)

    def _any_order_salvageable(self) -> bool:
        for o in self._order_list:
            if o.status in ("ACTIVE", "PENDING"):
                # Serve must start strictly before (or at) deadline_gs.
                if self._min_remaining_serve_start(o) <= o.deadline_gs + 1e-9:
                    return True
        return False

    def _check_terminate(self) -> bool:
        if self.terminated:
            return True
        if self.clock_gs >= self.spec.horizon_gs:
            self.terminated = True
            self.termination_reason = "horizon"
            self._record("terminated", {"reason": "horizon"})
        elif all(o.status in ("SERVED", "EXPIRED") for o in self._order_list):
            self.terminated = True
            self.termination_reason = "orders_exhausted"
            self._record("terminated", {"reason": "orders_exhausted"})
        elif self._action_count >= MAX_DECISIONS:
            self.terminated = True
            self.termination_reason = "max_decisions"
            self._record("terminated", {"reason": "max_decisions"})
        elif self.early_stop and not self._any_order_salvageable():
            self.terminated = True
            self.termination_reason = "mathematically_impossible"
            self._record("terminated", {"reason": "mathematically_impossible"})
        return self.terminated

    def step_batch(self, calls: list[ToolCall]) -> dict[str, Any]:
        """Execute 1..MAX_BATCH_CALLS sequentially, interrupting on invalid or unforeseen events."""
        if self.terminated:
            return {**self.observe(), "ok": False, "interrupted_reason": "already_terminated"}
        self._events_since_last = []
        self.turn_count += 1
        self._turn_productive = False

        results: list[dict[str, Any]] = []
        aborted: list[str] = []
        interrupted_reason: str | None = None
        failed_at: int | None = None

        capped = calls[:MAX_BATCH_CALLS]
        overflow = [c.name for c in calls[MAX_BATCH_CALLS:]]
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
                    interrupted_reason = interrupted_reason or self.termination_reason
                    break
                ev_start = len(self._events_since_last)
                self._action_count += 1
                self.counters["total_tool_calls"] += 1
                res = self._exec(call)
                res["call"] = call.name
                res["arguments"] = dict(call.arguments or {})
                results.append(res)
                self._emit_frame(
                    "action",
                    action={
                        "name": call.name,
                        "arguments": dict(call.arguments or {}),
                        "ok": res["ok"],
                        "note": res.get("note", ""),
                        "category": res.get("category"),
                    },
                )
                step_events = [ev.type for ev in self._events_since_last[ev_start:]]
                self._check_terminate()

                if not res["ok"]:
                    failed_at = i
                    interrupted_reason = "invalid_action"
                    if len(capped) > 1:
                        self.counters["chain_partial_failures"] += 1
                    aborted.extend(c.name for c in capped[i + 1 :])
                    break
                if self.terminated:
                    interrupted_reason = self.termination_reason
                    aborted.extend(c.name for c in capped[i + 1 :])
                    break
                hit = [k for k in step_events if k in INTERRUPT_EVENTS]
                if hit and i + 1 < len(capped):
                    interrupted_reason = hit[0]
                    aborted.extend(c.name for c in capped[i + 1 :])
                    break

        if self._turn_productive:
            self._no_progress_turns = 0
        else:
            self._no_progress_turns += 1
            if self._no_progress_turns >= config.STALL_TURNS and not self.terminated:
                self.terminated = True
                self.termination_reason = "stalled"
                self._record("terminated", {"reason": "stalled"})

        self._last_turn = {
            "calls": results,
            "aborted_calls": aborted,
            "interrupted_reason": interrupted_reason,
            "failed_at_index": failed_at,
            "overflow_dropped": overflow,
        }
        return {
            "turn": self.turn_count,
            "clock_gs": round(self.clock_gs, 2),
            "calls": results,
            "aborted_calls": aborted,
            "interrupted_reason": interrupted_reason,
            "events": [
                {"t": round(ev.clock_gs, 2), "kind": ev.type, "detail": ev.detail}
                for ev in self._events_since_last
                if ev.type in ("order_arrived", "cook_ready", "burned", "order_expired", "terminated")
            ],
            "terminated": self.terminated,
            "termination_reason": self.termination_reason,
        }

    def step(self, calls: list[ToolCall], think_gs: float = 0.0) -> dict[str, Any]:
        if think_gs != 0:
            raise ValueError("v3 uses zero inference game time")
        return self.step_batch(calls)


def static_context(engine: UnifiedEngine) -> dict[str, Any]:
    """Episode-invariant context placed once in the cached system prompt."""
    stations_compact = []
    for s in engine.spec.stations:
        label = f"{s.type}:{s.ingredient}" if s.ingredient else s.type
        stations_compact.append({"station": label, "cell": list(s.cell)})

    rules_compact = {}
    for name, rule in sorted(engine.ingredient_rules.items()):
        entry: dict[str, Any] = {"choppable": rule.choppable}
        if rule.cookable_from is not None:
            entry["cookable_from"] = rule.cookable_from
            entry["cook_time_gs"] = rule.cook_time
            entry["burn_window_gs"] = rule.burn_window
        rules_compact[name] = entry

    return {
        "protocol": PROTOCOL,
        "difficulty": engine.spec.difficulty,
        "grid_n": engine.spec.grid_n,
        "hand_slots": config.HAND_SLOTS,
        "burner_count": len(engine.burners),
        "action_seconds": {
            "collect": config.COLLECT_GS,
            "chop": config.CHOP_GS,
            "cook": config.COOK_START_GS,
            "collect_cooked": config.COOK_PICKUP_GS,
            "plate": config.PLATE_GS,
            "serve": config.SERVE_GS,
            "discard": config.DISCARD_GS,
            "invalid": config.INVALID_GS,
            "move_per_step": config.MOVE_GS_PER_STEP,
        },
        "ingredient_rules": rules_compact,
        "stations": stations_compact,
    }


def dynamic_state(engine: UnifiedEngine, recent_turns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compact per-turn dynamic state (never leaks hidden PENDING order contents/arrivals)."""
    obs = engine.observe()
    active_orders = []
    known_cards: dict[str, dict[str, str]] = {}
    pending_count = 0
    served_count = 0
    expired_count = 0
    for o in engine._order_list:
        if o.status == "PENDING":
            pending_count += 1
        elif o.status == "SERVED":
            served_count += 1
        elif o.status == "EXPIRED":
            expired_count += 1
        elif o.status == "ACTIVE":
            active_orders.append({
                "order_id": o.order_id,
                "recipe": o.dish,
                "deadline_gs": o.deadline_gs,
                "time_left_gs": round(o.deadline_gs - engine.clock_gs, 2),
            })
            known_cards[o.dish] = dict(engine.spec.recipe_cards[o.dish])

    for h in engine.hands:
        if h.is_plate and h.ingredient:
            known_cards[h.ingredient] = dict(engine.spec.recipe_cards[h.ingredient])

    burners = []
    for idx, b in enumerate(engine.burners):
        if b.job is None:
            burners.append({"burner_index": idx, "status": "FREE"})
        else:
            status = b.job.status(engine.clock_gs)
            entry: dict[str, Any] = {
                "burner_index": idx,
                "status": status,
                "ingredient": b.job.ingredient,
                "ready_gs": round(b.job.ready_gs, 2),
                "burn_gs": round(b.job.burn_gs, 2),
            }
            if status == "COOKING":
                entry["ready_in_gs"] = round(b.job.ready_gs - engine.clock_gs, 2)
            elif status == "READY":
                entry["burns_in_gs"] = round(b.job.burn_gs - engine.clock_gs, 2)
            burners.append(entry)

    state: dict[str, Any] = {
        "turn": engine.turn_count,
        "actions_used": engine._action_count,
        "actions_remaining": MAX_DECISIONS - engine._action_count,
        "clock_gs": round(engine.clock_gs, 2),
        "chef_pos": list(engine.chef_pos),
        "hands": obs["hands"],
        "hand_slots_free": obs["hand_slots_free"],
        "burners": burners,
        "active_orders": active_orders,
        "recipe_cards": known_cards,
        "progress": {
            "served": served_count,
            "expired": expired_count,
            "active": len(active_orders),
            "pending_hidden": pending_count,
        },
    }
    if recent_turns:
        state["recent_turns"] = recent_turns[-5:]
    return state


def _generate_profiles(rng: random.Random, prof: DifficultyProfile) -> dict[str, dict[str, Any]]:
    """Procedural per-kitchen rules: randomize cookable_from, cook_time, and burn_window for ALL cookables."""
    profiles: dict[str, dict[str, Any]] = {}
    for name, base in sorted(config.INGREDIENTS.items()):
        if base.cookable_from is not None:
            cook_from = rng.choice([config.RAW, config.CHOPPED])
            choppable = (cook_from == config.CHOPPED) or base.choppable
            cook_t = rng.randint(*prof.cook_time_range)
            burn_w = rng.randint(*prof.burn_window_range)
            item = config.Ingredient(
                name=name,
                choppable=choppable,
                cookable_from=cook_from,
                cook_time=float(cook_t),
                burn_window=float(burn_w),
            )
        else:
            item = base
        profiles[name] = asdict(item)
    return profiles


def _generate_recipes(
    rng: random.Random, prof: DifficultyProfile, profiles: dict[str, dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Generate distinct procedural recipes guaranteeing >= min_cooked cooked items per order."""
    cards: dict[str, dict[str, str]] = {}
    seen: set[tuple[tuple[str, str], ...]] = set()
    for i in range(prof.n_orders):
        for _ in range(200):
            n_comp = rng.randint(prof.min_components, prof.max_components)
            n_cook = min(n_comp, rng.randint(prof.min_cooked, prof.max_cooked))
            n_other = n_comp - n_cook
            chosen_cook = rng.sample(COOKABLE_INGREDIENTS, n_cook)
            remaining_pool = [x for x in ALL_INGREDIENTS if x not in chosen_cook]
            chosen_other = rng.sample(remaining_pool, n_other)

            card: dict[str, str] = {}
            for ing in chosen_cook:
                card[ing] = config.COOKED
            for ing in chosen_other:
                rule = profiles[ing]
                states = [config.RAW]
                if rule["choppable"]:
                    states.append(config.CHOPPED)
                card[ing] = rng.choice(states)

            sig = tuple(sorted(card.items()))
            if sig not in seen:
                seen.add(sig)
                cards[f"R{i + 1}"] = dict(sorted(card.items()))
                break
        else:
            raise RuntimeError("failed to generate unique recipe card")
    return cards


def _perimeter_cells(grid_n: int) -> list[tuple[int, int]]:
    cells = [(0, c) for c in range(1, grid_n - 1)]
    cells += [(r, grid_n - 1) for r in range(1, grid_n - 1)]
    cells += [(grid_n - 1, c) for c in range(grid_n - 2, 0, -1)]
    cells += [(r, 0) for r in range(grid_n - 2, 0, -1)]
    return cells


def generate_kitchen(
    seed: int,
    level: int,
    *,
    witness_fn=None,
) -> tuple[UnifiedKitchen, list[dict[str, Any]]]:
    """Generate a single parametric kitchen at difficulty `level` and anchor deadlines via witness_fn.

    If witness_fn is omitted, uses the default ScheduledWitnessSolver from `kitchenrush.v3.solvers`.
    Returns (spec, witness_batches) where witness_batches replays cleanly with 100% orders served.
    """
    if witness_fn is None:
        from .solvers import scheduled_witness
        witness_fn = scheduled_witness

    prof = difficulty_profile(level)
    rng = random.Random(f"kr-v3:D{level}:seed{seed}")
    profiles = _generate_profiles(rng, prof)
    cards = _generate_recipes(rng, prof, profiles)

    cells = _perimeter_cells(prof.grid_n)
    kinds: list[tuple[str, str | None]] = [(config.ING, ing) for ing in ALL_INGREDIENTS]
    kinds += [(config.BOARD, None)]
    kinds += [(config.STOVE, None)] * prof.n_burners
    kinds += [(config.PLATE, None), (config.PASS, None), (config.BIN, None)]
    if len(kinds) > len(cells):
        raise ValueError(f"grid_n={prof.grid_n} too small for {len(kinds)} stations")

    selected_cells = list(cells[: len(kinds)])
    if prof.shuffle_stations:
        rng.shuffle(selected_cells)
    stations = [StationSpec(cell, kind, ing) for cell, (kind, ing) in zip(selected_cells, kinds)]
    used = {s.cell for s in stations}
    blocked = frozenset(
        (r, c)
        for r in range(prof.grid_n)
        for c in range(prof.grid_n)
        if (r in (0, prof.grid_n - 1) or c in (0, prof.grid_n - 1)) and (r, c) not in used
    )

    arrivals: list[float] = []
    cur_arr = 0.0
    for idx in range(prof.n_orders):
        if idx < prof.initial_active:
            arrivals.append(0.0)
        else:
            cur_arr += float(rng.randint(*prof.arrival_gap_range))
            arrivals.append(cur_arr)

    # First construct an unconstrained-deadline draft to find a clean overlap witness.
    draft_orders = [
        OrderSpec(f"O{i + 1}", f"R{i + 1}", arrivals[i], 3000.0, 1.0)
        for i in range(prof.n_orders)
    ]
    draft = UnifiedKitchen(
        seed=seed,
        tier=f"v3-D{level}",
        grid_n=prof.grid_n,
        burner_count=prof.n_burners,
        horizon_gs=3600.0,
        show_ready_actions=False,
        active_recipes=tuple(cards.keys()),
        stations=stations,
        chef_start=(prof.grid_n // 2, prof.grid_n // 2),
        orders=draft_orders,
        recipe_cards=cards,
        latency_budget=0.0,
        blocked=blocked,
        door=(prof.grid_n - 1, prof.grid_n // 2),
        ingredient_rules=profiles,
        difficulty=level,
    )

    witness_batches, serve_times = witness_fn(draft)
    if len(serve_times) != prof.n_orders:
        raise RuntimeError(f"witness only served {len(serve_times)}/{prof.n_orders} on D={level} seed={seed}")

    # Anchor each order's deadline directly to the verified witness serve-start time + smooth slack!
    final_orders = []
    max_deadline = 0.0
    for o in draft_orders:
        s_start = serve_times[o.order_id]
        elapsed_since_arr = max(10.0, s_start - o.arrival_gs)
        pad = max(prof.min_deadline_pad, elapsed_since_arr * prof.deadline_slack)
        deadline = float(math.ceil(s_start + pad))
        max_deadline = max(max_deadline, deadline)
        final_orders.append(OrderSpec(o.order_id, o.dish, o.arrival_gs, deadline, 1.0))

    final_spec = copy.deepcopy(draft)
    final_spec.orders = final_orders
    final_spec.horizon_gs = float(math.ceil(max_deadline + 20.0))

    # Deterministically verify the witness replays with 100% completion, 0 invalids, 0 burns!
    check_engine = UnifiedEngine(final_spec, record_trace=True, early_stop=True)
    for batch in witness_batches:
        calls = [ToolCall(c["name"], c.get("arguments", {})) for c in batch]
        check_engine.step_batch(calls)
    if (
        check_engine.counters["orders_served"] != prof.n_orders
        or check_engine.counters["invalid_actions"] != 0
        or check_engine.counters["burns"] != 0
    ):
        raise RuntimeError(
            f"witness replay failed on D={level} seed={seed}: "
            f"served={check_engine.counters['orders_served']}/{prof.n_orders} "
            f"invalid={check_engine.counters['invalid_actions']} burns={check_engine.counters['burns']}"
        )
    return final_spec, witness_batches


def verify_replay(
    spec: UnifiedKitchen,
    batches: list[list[dict[str, Any]]],
    expected_served: int,
    expected_invalid: int,
    expected_burns: int,
    expected_trace_hash: str | None = None,
) -> dict[str, Any]:
    """Deterministically replay batched tool calls from scratch and assert exact match."""
    engine = UnifiedEngine(spec, record_trace=True, early_stop=True)
    for batch in batches:
        if engine.terminated:
            raise ValueError("batches present after episode termination")
        calls = [ToolCall(c["name"], c.get("arguments", {})) for c in batch]
        engine.step_batch(calls)
    trace_hash = digest(engine.trace)
    if engine.counters["orders_served"] != expected_served:
        raise ValueError(
            f"served mismatch: replay={engine.counters['orders_served']} expected={expected_served}"
        )
    if engine.counters["invalid_actions"] != expected_invalid:
        raise ValueError(
            f"invalid mismatch: replay={engine.counters['invalid_actions']} expected={expected_invalid}"
        )
    if engine.counters["burns"] != expected_burns:
        raise ValueError(
            f"burn mismatch: replay={engine.counters['burns']} expected={expected_burns}"
        )
    if expected_trace_hash is not None and trace_hash != expected_trace_hash:
        raise ValueError("trace hash mismatch on replay")
    return {
        "verified": True,
        "served": engine.counters["orders_served"],
        "invalid": engine.counters["invalid_actions"],
        "burns": engine.counters["burns"],
        "actions": engine._action_count,
        "clock_gs": round(engine.clock_gs, 2),
        "termination_reason": engine.termination_reason,
        "trace_hash": trace_hash,
    }
