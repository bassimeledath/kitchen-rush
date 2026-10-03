#!/usr/bin/env python3
"""Measure how much game time an OVERLAPPING schedule saves over the serial oracle.

Offline only; costs nothing. The published v2.1 deadline is built by accumulating each
order's isolated worst-case path on top of the previous one and adding 35% slack
(environment.py:151-165), which is a serial schedule with a tip -- so serial play is always
affordable. Before any deadline can be retuned to REQUIRE overlap, we need the size of the
prize: if the best overlapping schedule is only a few percent faster than the serial oracle,
no deadline can separate them and the whole approach is dead.

The serial oracle (oracle.py RecipeReader) cannot be that witness: `_current` is sticky, one
order at a time, and line 136 refuses a second cook while any burner is busy. So it is the
NEGATIVE control here, and the witness is a beam search over the real engine -- a replayable
action sequence, not an analytic bound.

  probe [--seeds N] [--split main] [--width W]
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kitchenrush import config  # noqa: E402
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2.environment import (  # noqa: E402
    WAITS,
    Engine,
    RecipeReader,
    generate,
    public_state,
)

UNBOUNDED = 10_000.0


def unbind(spec):
    """Same kitchen, deadlines and horizon removed, so we measure MAKESPAN not success."""
    orders = [replace(o, deadline_gs=UNBOUNDED) for o in spec.orders]
    return replace(spec, orders=orders, horizon_gs=UNBOUNDED)


def serve_times(engine):
    """Clock at which each order was served, by order id.

    The engine has no dedicated serve event: a delivery is recorded by `_ok("serve", ...)`
    (engine.py:589) as an event of type "serve" whose note names the order, so read it from
    `engine.events` rather than looking for a frame that does not exist."""
    out = {}
    for event in engine.events:
        if event.type == "serve":
            match = re.search(r"served (\S+)", event.detail.get("note", ""))
            if match:
                out[match.group(1)] = event.clock_gs
    return out


# --- serial control ----------------------------------------------------------------------


def run_serial(spec, engine_cls=Engine, unbound=True):
    engine = engine_cls(unbind(spec) if unbound else spec, record_trace=True)
    agent = RecipeReader()
    decisions = 0
    while not engine.terminated and decisions < 400:
        call = agent.choose(public_state(engine))
        engine.step([call])
        decisions += 1
    return engine, decisions


# --- overlapping witness (beam search over the real engine) ------------------------------


def needs(state):
    """Component multiset still required, per ACTIVE order, plus the cards themselves."""
    out = {}
    for o in state["orders"]:
        if o["status"] == "ACTIVE":
            out[o["order_id"]] = (o["dish"], dict(state["recipe_cards"][o["dish"]]))
    return out


def successors(engine, focus=None):
    """A small set of PLAUSIBLY legal actions. Pruning is for search speed only -- every
    survivor is executed by the real engine, so nothing illegal can sneak into the witness.

    `focus` restricts component work to one order id, which is how the STRONG serial control
    is built: it may still use both burners inside that order, it just may not start work on
    a second order before finishing the first. Order-agnostic actions (serving, clearing a
    burner, dumping junk, waiting) are always allowed."""
    state = public_state(engine)
    every = needs(state)
    active = {k: v for k, v in every.items() if focus is None or k == focus}
    held = [(h["ingredient"], h["state"]) for h in state["hands"]]
    free = state["hand_slots_free"]
    rules = state["ingredient_rules"]
    calls = []

    # serve any plate that matches an active order -- never restricted by focus
    for ing, st in held:
        if st == "PLATE":
            for oid, (dish, _) in every.items():
                if dish == ing:
                    calls.append(ToolCall("serve", {"order_id": oid}))

    # plate any recipe whose components are all in hand
    for oid, (dish, card) in active.items():
        want = sorted(card.items())
        pool = list(held)
        ok = True
        for pair in want:
            if pair in pool:
                pool.remove(pair)
            else:
                ok = False
                break
        if ok and not [p for p in pool if p[1] != "PLATE"]:
            calls.append(ToolCall("plate", {"recipe": dish}))

    # take anything off a burner that is ready (or burned, to free the burner)
    for b in state["burners"]:
        if b["status"] in ("READY", config.BURNED) and free > 0:
            calls.append(
                ToolCall(
                    "collect_cooked",
                    {"ingredient": b["ingredient"], "burner_index": b["burner_index"]},
                )
            )

    # dump a burned or unneeded component to recover a hand slot
    wanted = {}
    for _, (_, card) in active.items():
        for ing, st in card.items():
            wanted[(ing, st)] = wanted.get((ing, st), 0) + 1
    for ing, st in held:
        if st == config.BURNED:
            calls.append(ToolCall("discard", {"item": ing}))
    # Escape hatch: with only four slots, a mix of components from different orders can
    # deadlock the search. Allow dropping any component once hands are full.
    if free == 0:
        for ing, st in held:
            if st != "PLATE":
                calls.append(ToolCall("discard", {"item": ing}))

    burner_free = any(b["status"] == "FREE" for b in state["burners"])
    on_heat = [b["ingredient"] for b in state["burners"] if b["status"] != "FREE"]

    # advance components for EVERY active order, not just one -- this is the overlap channel
    for _, (_, card) in active.items():
        for ing, term in card.items():
            if (ing, term) in held:
                continue
            item = config.Ingredient(**rules[ing])
            if term == config.COOKED:
                src = item.cookable_from
                if (ing, src) in held and burner_free and ing not in on_heat:
                    calls.append(ToolCall("cook", {"ingredient": ing}))
                elif src == config.CHOPPED and (ing, config.RAW) in held:
                    calls.append(ToolCall("chop", {"ingredient": ing}))
                elif (ing, config.RAW) not in held and free > 0 and ing not in on_heat:
                    calls.append(ToolCall("collect", {"ingredient": ing}))
            elif term == config.CHOPPED:
                if (ing, config.RAW) in held:
                    calls.append(ToolCall("chop", {"ingredient": ing}))
                elif free > 0:
                    calls.append(ToolCall("collect", {"ingredient": ing}))
            elif free > 0:
                calls.append(ToolCall("collect", {"ingredient": ing}))

    # waiting is only useful up to the next burner event or the next order arrival
    horizons = [b["ready_gs"] - state["clock_gs"] for b in state["burners"] if b["status"] == "COOKING"]
    horizons += [
        o["arrival_gs"] - state["clock_gs"]
        for o in state["orders"]
        if o["status"] == "PENDING" and o["arrival_gs"] > state["clock_gs"]
    ]
    if state["pending_orders"] and not horizons:
        horizons = [max(WAITS)]
    for h in horizons:
        pick = [n for n in WAITS if n <= max(1, h)]
        if pick:
            calls.append(ToolCall("wait", {"seconds": max(pick)}))

    seen, unique = set(), []
    for c in calls:
        k = (c.name, tuple(sorted(c.arguments.items())))
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique


def time_to_go(engine):
    """Lower bound on remaining game time: the ACTIVE work still owed.

    Cook waiting is deliberately excluded -- that is precisely the time an overlapping
    schedule hides behind other work, so counting it would bias the search back toward
    serial play. Travel is the per-action worst case in environment.py:155."""
    state = public_state(engine)
    pool = [(h["ingredient"], h["state"]) for h in state["hands"] if h["state"] != "PLATE"]
    plates = [h["ingredient"] for h in state["hands"] if h["state"] == "PLATE"]
    on_heat = {b["ingredient"]: b for b in state["burners"] if b["status"] != "FREE"}
    total = 0.0
    for o in state["orders"]:
        if o["status"] != "ACTIVE":
            continue
        card = state["recipe_cards"][o["dish"]]
        if o["dish"] in plates:
            total += config.SERVE_GS + 1.2
            plates.remove(o["dish"])
            continue
        for ing, term in card.items():
            if (ing, term) in pool:
                pool.remove((ing, term))
                continue
            item = config.Ingredient(**state["ingredient_rules"][ing])
            if term == config.COOKED and ing in on_heat:
                total += config.COOK_PICKUP_GS + 1.2
                continue
            total += config.COLLECT_GS + 1.2
            if term == config.CHOPPED or (term == config.COOKED and item.cookable_from == config.CHOPPED):
                total += config.CHOP_GS + 1.2
            if term == config.COOKED:
                total += config.COOK_START_GS + config.COOK_PICKUP_GS + 2.4
        total += config.PLATE_GS + config.SERVE_GS + 2.4
    # Orders that have not arrived are hidden from the public state (only their count is
    # published), so the witness plans for them the same way a model must: as unavoidable
    # future work of at least one plate-and-serve each.
    total += state["pending_orders"] * (config.PLATE_GS + config.SERVE_GS + 3 * config.COLLECT_GS)
    return total


def progress(engine):
    """Higher is closer to done. Used only to rank the beam.

    Credit is per ORDER, not per component: four components spread across three orders
    plate nothing, so a flat per-component bonus walks the search into a hand-slot deadlock.
    Completing one card is worth more than starting three."""
    state = public_state(engine)
    score = 1000 * state["completed_orders"]
    pool = [(h["ingredient"], h["state"]) for h in state["hands"] if h["state"] != "PLATE"]
    score += 200 * sum(1 for h in state["hands"] if h["state"] == "PLATE")
    for o in state["orders"]:
        if o["status"] != "ACTIVE":
            continue
        card = state["recipe_cards"][o["dish"]]
        matched = 0
        for pair in card.items():
            if pair in pool:
                pool.remove(pair)
                matched += 1
        score += 30 * matched + (70 if matched == len(card) else 0)
    score -= 20 * len(pool)  # components no active order can use
    for b in state["burners"]:
        if b["status"] == "COOKING":
            score += 15
        elif b["status"] == "READY":
            score += 20
    return score - 40 * state["invalid_actions"]


def state_key(engine):
    """Two search nodes that differ only in the ORDER prep happened are the same node.
    Without this the beam fills with permutations of one state and explores nothing."""
    state = public_state(engine)
    return (
        tuple(sorted((h["ingredient"], h["state"]) for h in state["hands"])),
        tuple(
            sorted(
                (b["ingredient"] or "", b["status"], round(b.get("ready_gs") or 0, 1))
                for b in state["burners"]
            )
        ),
        tuple(sorted((o["order_id"], o["status"]) for o in state["orders"])),
        state["completed_orders"],
        state["pending_orders"],
    )


def focus_options(engine, focus):
    """Which order the next component action may serve.

    None means unrestricted (the overlapping witness). Otherwise the node keeps working its
    current order until that order leaves ACTIVE, at which point it branches over every
    remaining order -- so the serial control picks its own best order SEQUENCE and is not
    handicapped by our guess at one."""
    if focus is None:
        return [None]
    state = public_state(engine)
    live = [o["order_id"] for o in state["orders"] if o["status"] == "ACTIVE"]
    if focus in live:
        return [focus]
    return live or [focus]


def run_beam(spec, width=250, max_depth=160, engine_cls=Engine, cross_order=True, unbound=True, report_partial=False, record_trace=True):
    """Best schedule we can find, replayed through the real engine.

    cross_order=False is the STRONG serial control: one order at a time, but free to use
    both burners within it and to order the orders as it likes. Beating that is what proves
    a deadline actually requires cross-order overlap, rather than merely requiring more
    competence than our deliberately-cautious shipped oracle."""
    root = engine_cls(unbind(spec) if unbound else spec, record_trace=record_trace)
    start = None if cross_order else "?"
    beam = [(root, [], start)]
    best = None
    most_served = 0
    for _ in range(max_depth):
        pool = {}
        for engine, path, focus in beam:
            for option in focus_options(engine, focus):
                for call in successors(engine, focus=None if cross_order else option):
                    child = copy.deepcopy(engine)
                    try:
                        child.step([call])
                    except Exception:
                        continue
                    trail = path + [call]
                    most_served = max(most_served, child.counters["orders_served"])
                    if child.counters["orders_served"] == len(spec.orders):
                        if best is None or child.clock_gs < best[0]:
                            best = (child.clock_gs, trail, child)
                        continue
                    if child.terminated or child.counters["invalid_actions"]:
                        continue
                    if best is not None and child.clock_gs >= best[0]:
                        continue
                    key = state_key(child) + (option,)
                    prior = pool.get(key)
                    if prior is None or child.clock_gs < prior[0].clock_gs:
                        pool[key] = (child, trail, option)
        nxt = list(pool.values())
        if not nxt:
            break
        # A*-style: elapsed + a lower bound on the work still owed. Ranking by progress
        # alone buys state at any price and returns schedules SLOWER than the serial
        # oracle. Half the beam is kept on raw progress so a deep line is not pruned by a
        # heuristic that cannot see an overlap two moves out.
        by_cost = sorted(nxt, key=lambda p: p[0].clock_gs + time_to_go(p[0]))
        by_prog = sorted(nxt, key=lambda p: (-progress(p[0]), p[0].clock_gs))
        beam, seen = [], set()
        for node in [x for pair in zip(by_cost, by_prog) for x in pair]:
            k = id(node[0])
            if k not in seen:
                seen.add(k)
                beam.append(node)
            if len(beam) >= width:
                break
    return (best, most_served) if report_partial else best


# --- probe -------------------------------------------------------------------------------


def probe(seeds, split, size, width):
    rows = []
    for seed in seeds:
        spec = generate(seed, split, size)
        serial_engine, serial_decisions = run_serial(spec)
        serial_done = serial_engine.counters["orders_served"] == len(spec.orders)
        serial_span = serial_engine.clock_gs if serial_done else None
        serial_serves = serve_times(serial_engine)

        found = run_beam(spec, width=width)
        if found is None:
            rows.append(dict(seed=seed, serial=serial_span, beam=None))
            print(f"seed {seed}: beam found no complete schedule", file=sys.stderr)
            continue
        beam_span, beam_path, beam_engine = found
        beam_serves = serve_times(beam_engine)
        rows.append(
            dict(
                seed=seed,
                serial_makespan=serial_span,
                serial_decisions=serial_decisions,
                serial_serves=serial_serves,
                beam_makespan=beam_span,
                beam_decisions=len(beam_path),
                beam_serves=beam_serves,
                ratio=(beam_span / serial_span) if serial_span else None,
                published_deadlines=[o.deadline_gs for o in spec.orders],
            )
        )
        print(
            f"seed {seed}: serial {serial_span:7.1f}gs ({serial_decisions} dec)  "
            f"overlap {beam_span:7.1f}gs ({len(beam_path)} dec)  "
            f"ratio {beam_span / serial_span:.3f}  "
            f"published deadlines {[o.deadline_gs for o in spec.orders]}"
        )
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--start", type=int, default=85000)
    ap.add_argument("--split", default="main")
    ap.add_argument("--size", type=int, default=3)
    ap.add_argument("--width", type=int, default=120)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    seeds = list(range(args.start, args.start + args.seeds))
    rows = probe(seeds, args.split, args.size, args.width)
    good = [r for r in rows if r.get("ratio")]
    if good:
        ratios = [r["ratio"] for r in good]
        print(
            f"\noverlap saves: median {100 * (1 - statistics.median(ratios)):.1f}%  "
            f"best {100 * (1 - min(ratios)):.1f}%  worst {100 * (1 - max(ratios)):.1f}%"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
