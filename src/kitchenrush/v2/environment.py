"""Versioned v2 task generator, public projection, action domain and replay verifier."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from dataclasses import asdict
from pathlib import Path

from .. import config
from ..engine import KitchenRushEngine
from ..oracle import OracleAgent
from ..procgen import KitchenSpec, OrderSpec, StationSpec
from ..tools import ToolCall

PROTOCOL = "kitchen-rush-v2-choice-1"
INVALID_WEIGHT = 0.1
MAX_DECISIONS = 160
WAITS = (1, 2, 5, 10, 20)
SPLITS = {"development": range(6), "pilot": range(6, 8), "confirmation": (8,), "main": (9,)}
RULES = (
    "Complete requested orders. Quality = 100 * max(0, (completed - 0.1 * invalid) / requested). "
    "Choose exactly one candidate action. Candidates are NOT filtered for legality. "
    "Station actions automatically walk to their station; walking and actions consume game time. "
    "Inference does not advance the kitchen. Feedback is supplied after every action. "
    "Use wait(seconds) to advance cooking or await orders. "
    "Collect gets RAW ingredients; chop requires a held RAW choppable ingredient; cook requires "
    "the ingredient state specified in ingredient_rules and a free burner. collect_cooked requires "
    "a READY item BEFORE walking; food may burn during travel. You have four hand slots. "
    "Plate requires exactly the card ingredients/states with no extra unplated components. "
    "Serve requires a matching held plate and ACTIVE order. Discard removes a held item. "
    "Burned food automatically disappears from its burner. Invalid actions consume three game "
    "seconds and reduce quality. All unresolved orders count as incomplete. "
    "Future order contents/deadlines are hidden until arrival. Do not infer recipes from card IDs. "
    "The episode ends at the resource allowance, all orders resolved, 160 decisions, or 12 "
    "consecutive nonproductive decisions. Explicit waits reset that stall counter."
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=list).encode()
    ).hexdigest()


def protocol_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    files = [root / f for f in ("engine.py", "config.py", "procgen.py", "oracle.py", "tools.py")]
    files += sorted((root / "v2").glob("*.py"))
    return digest(
        {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    )


def recipe_signature(card: dict) -> str:
    return digest(sorted(card.items()))


def state_domain(ingredient):
    item = config.INGREDIENTS[ingredient]
    return (
        ["RAW"]
        + (["CHOPPED"] if item.choppable else [])
        + (["COOKED"] if item.cookable_from else [])
    )


def recipe_pool(size: int, split: str) -> list[dict]:
    result = []
    for ingredients in itertools.combinations(sorted(config.INGREDIENTS), size):
        for states in itertools.product(*(state_domain(i) for i in ingredients)):
            card = dict(zip(ingredients, states, strict=True))
            if int(recipe_signature(card), 16) % 10 in SPLITS[split]:
                result.append(card)
    return result


def generate(seed: int, split: str = "development", size: int = 3) -> KitchenSpec:
    """Three orders, 2/3/4-component families. No resource allowance affects generation.

    Structural recipe signatures partition the entire finite card grammar. Layout stays
    fixed with dispensers for ALL ingredients, preventing future menus leaking via layout.
    Deadlines are conservatively constructed; the scripted witness verifies actual feasibility.
    """
    if split not in SPLITS or size not in (2, 3, 4):
        raise ValueError("unknown split or component count")
    rng = random.Random(seed)
    pool = recipe_pool(size, split)
    cards = {f"R{i + 1}": dict(card) for i, card in enumerate(rng.sample(pool, 3))}
    stations = []
    cells = [(0, c) for c in range(1, 6)] + [(r, 6) for r in range(1, 6)]
    cells += [(6, c) for c in range(5, 0, -1)] + [(r, 0) for r in range(5, 0, -1)]
    kinds = [(config.ING, i) for i in sorted(config.INGREDIENTS)]
    kinds += [
        (config.BOARD, None),
        (config.STOVE, None),
        (config.STOVE, None),
        (config.PLATE, None),
        (config.PASS, None),
        (config.BIN, None),
    ]
    for cell, (kind, ingredient) in zip(cells, kinds, strict=False):
        stations.append(StationSpec(cell, kind, ingredient))
    used = {s.cell for s in stations}
    blocked = frozenset(
        (r, c)
        for r in range(7)
        for c in range(7)
        if (r in (0, 6) or c in (0, 6)) and (r, c) not in used
    )
    order_cards = list(cards)
    rng.shuffle(order_cards)
    orders = []
    finish = 0.0
    for idx, rid in enumerate(order_cards):
        arrival = 0.0 if idx < 2 else float(rng.randint(35, 65))
        # Worst-case single-order path: each station action <= 8 steps (1.2s).
        intrinsic = config.PLATE_GS + config.SERVE_GS + 2.4
        for ingredient, state in cards[rid].items():
            ing = config.INGREDIENTS[ingredient]
            intrinsic += config.COLLECT_GS + 1.2
            if state == "CHOPPED" or (state == "COOKED" and ing.cookable_from == "CHOPPED"):
                intrinsic += config.CHOP_GS + 1.2
            if state == "COOKED":
                intrinsic += config.COOK_START_GS + ing.cook_time + config.COOK_PICKUP_GS + 2.4
        finish = max(arrival, finish) + intrinsic
        deadline = math.ceil(finish * 1.35 + 10)
        orders.append(OrderSpec(f"O{idx + 1}", rid, arrival, deadline, 1.0))
    return KitchenSpec(
        seed=seed,
        tier=f"v2-{split}-{size}",
        grid_n=7,
        burner_count=2,
        horizon_gs=600,
        show_ready_actions=False,
        active_recipes=tuple(cards),
        stations=stations,
        chef_start=(3, 3),
        orders=orders,
        recipe_cards=cards,
        latency_budget=0,
        blocked=blocked,
        door=(6, 3),
    )


def serialize_spec(spec):
    result = asdict(spec)
    result["blocked"] = sorted(spec.blocked)
    return json.loads(json.dumps(result))


def deserialize_spec(value):
    value = dict(value)
    value["stations"] = [
        StationSpec(tuple(s["cell"]), s["type"], s["ingredient"]) for s in value["stations"]
    ]
    value["orders"] = [OrderSpec(**o) for o in value["orders"]]
    value["blocked"] = frozenset(tuple(c) for c in value["blocked"])
    value["active_recipes"] = tuple(value["active_recipes"])
    return KitchenSpec(**value)


class Engine(KitchenRushEngine):
    def step(self, calls, think_gs=0.0):
        if think_gs != 0 or len(calls) != 1:
            raise ValueError("v2 requires one action and zero inference game time")
        return super().step(calls, 0)

    def _ok(self, action, note):
        # The v1 reward/combo calculation is not the v2 objective. Do not leak obsolete
        # reward numbers or discard-penalty labels into model feedback or replay captions.
        if action == "serve":
            note = note.split(" +", 1)[0]
        if action == "discard":
            note = note.replace(" (penalty)", "")
        return super()._ok(action, note)

    def _exec(self, call):
        if call.name == "wait":
            seconds = call.arguments.get("seconds")
            if type(seconds) is not int or seconds not in WAITS:
                return self._invalid("unsupported wait duration")
            self._charge(seconds)
            return self._ok("wait", f"waited {seconds}s")
        return super()._exec(call)


def public_state(engine: Engine) -> dict:
    """Allowlist, including derived fields. Hidden spec never enters the adapter."""
    obs = engine.observe()
    visible = [o for o in engine._order_list if o.status != "PENDING"]
    known_cards = {o.dish for o in visible}
    # Publicly held plates remain identifiable after their order has resolved.
    state = {
        k: obs[k]
        for k in (
            "clock_gs",
            "chef_pos",
            "grid_ascii",
            "hands",
            "hand_slots_free",
            "stations",
            "burners",
            "last_turn",
        )
    }
    state["orders"] = [
        dict(
            order_id=o.order_id,
            dish=o.dish,
            status=o.status,
            arrival_gs=o.arrival_gs,
            deadline_gs=o.deadline_gs,
        )
        for o in visible
    ]
    state["recipe_cards"] = {r: dict(engine.recipes[r]) for r in sorted(known_cards)}
    state["pending_orders"] = sum(o.status == "PENDING" for o in engine._order_list)
    state["requested_orders"] = len(engine._order_list)
    state["completed_orders"] = engine.counters["orders_served"]
    state["invalid_actions"] = engine.counters["invalid_actions"]
    state["ingredient_rules"] = {i: asdict(p) for i, p in config.INGREDIENTS.items()}
    state["action_seconds"] = dict(
        collect=2,
        chop=4,
        cook=2,
        collect_cooked=1,
        plate=5,
        serve=3,
        discard=1,
        invalid=3,
        move_per_cell=0.15,
    )
    return state


def action_key(call):
    return call.name + "(" + ",".join(str(v) for v in call.arguments.values()) + ")"


def candidates(state) -> dict[str, ToolCall]:
    """Syntactic domains only; no feasibility checks, hints, or hidden facts."""
    calls = []
    for i in sorted(state["ingredient_rules"]):
        calls += [ToolCall(name, {"ingredient": i}) for name in ("collect", "chop", "cook")]
        calls += [
            ToolCall("collect_cooked", {"ingredient": i, "burner_index": b["burner_index"]})
            for b in state["burners"]
        ]
        calls.append(ToolCall("discard", {"item": i}))
    for rid in sorted(state["recipe_cards"]):
        calls += [ToolCall("plate", {"recipe": rid}), ToolCall("discard", {"item": f"plate:{rid}"})]
    calls += [ToolCall("serve", {"order_id": o["order_id"]}) for o in state["orders"]]
    calls += [ToolCall("wait", {"seconds": n}) for n in WAITS]
    calls += [
        ToolCall("move_to", {"row": r, "col": c})
        for r, line in enumerate(state["grid_ascii"].splitlines())
        for c in range(len(line))
    ]
    return {action_key(c): c for c in calls}


def decision(state, budget):
    options = candidates(state)
    return {
        "state": {"rules": RULES, "kitchen": state, "resource": budget},
        "questions": {
            "next_action": {
                "type": "choice",
                "instructions": "Select the next action to maximize final quality "
                "within the remaining allowance. Judge legality from the kitchen state.",
                "criteria": dict.fromkeys(options),
            }
        },
    }, options


def quality(engine):
    return 100 * max(
        0,
        (engine.counters["orders_served"] - INVALID_WEIGHT * engine.counters["invalid_actions"])
        / len(engine.spec.orders),
    )


class RecipeReader(OracleAgent):
    """Offline feasibility/transfer baseline only. NEVER attached to a model adapter."""

    def choose(self, state):
        call = self._decide(state)
        if call is None:
            return ToolCall("wait", {"seconds": 1})
        if call.name == "collect_cooked":
            ready = next(
                b
                for b in state["burners"]
                if b["status"] == "READY" and b["ingredient"] == call.arguments["ingredient"]
            )
            call.arguments["burner_index"] = ready["burner_index"]
        return call


def scripted_episode(spec, *, memorize=None):
    engine = Engine(spec, record_trace=True)
    agent = RecipeReader()
    actions = []
    while not engine.terminated and len(actions) < MAX_DECISIONS:
        state = public_state(engine)
        if memorize is not None:
            state["recipe_cards"] = {
                r: memorize.get(r, next(iter(memorize.values()))) for r in state["recipe_cards"]
            }
        call = agent.choose(state)
        actions.append(asdict(call))
        engine.step([call])
    return {
        "spec": serialize_spec(spec),
        "actions": actions,
        "quality": quality(engine),
        "completed": engine.counters["orders_served"],
        "invalid": engine.counters["invalid_actions"],
        "frames": engine.trace,
    }


def verify_replay(episode):
    engine = Engine(deserialize_spec(episode["spec"]), record_trace=True)
    for action in episode["actions"]:
        if engine.terminated:
            raise ValueError("actions after termination")
        engine.step([ToolCall(**action)])
    if not math.isclose(quality(engine), episode["quality"], abs_tol=1e-9):
        raise ValueError("quality replay mismatch")
    if digest(engine.trace) != digest(episode["frames"]):
        raise ValueError("frame replay mismatch")
    return True
