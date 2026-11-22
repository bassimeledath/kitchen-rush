"""Zero-cost deterministic offline solvers for calibrating Kitchen Rush v3 difficulty levels.

Three-rung offline calibration ladder:
1. SequentialSolver: strict 1-order-at-a-time (EDF), no cross-order burner overlap.
2. GreedyOverlapSolver: opportunistically fills free burners across active orders without
   burn-window / plating-collision foresight.
3. scheduled_witness: burner-aware parallel scheduler that overlaps active orders safely
   with zero burns and zero invalid actions, proving solvability and anchoring deadlines.
"""

from __future__ import annotations

import math
from typing import Any

from .. import config
from ..tools import ToolCall
from .env import MAX_DECISIONS, MAX_WAIT_GS, UnifiedEngine, UnifiedKitchen


def _active_orders(engine: UnifiedEngine) -> list[Any]:
    return sorted(
        [o for o in engine._order_list if o.status == "ACTIVE"],
        key=lambda o: (o.deadline_gs, o.arrival_gs, o.order_id),
    )


def _next_pending_arrival(engine: UnifiedEngine) -> float | None:
    pending = [o.arrival_gs for o in engine._order_list if o.status == "PENDING"]
    return min(pending) if pending else None


def _count_hands(engine: UnifiedEngine) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for h in engine.hands:
        if not h.is_plate:
            counts[(h.ingredient, h.state)] = counts.get((h.ingredient, h.state), 0) + 1
    return counts


def _unplated_count(engine: UnifiedEngine) -> int:
    return sum(1 for h in engine.hands if not h.is_plate)


def _held_plate_order(engine: UnifiedEngine) -> Any | None:
    for h in engine.hands:
        if h.is_plate:
            for o in _active_orders(engine):
                if o.dish == h.ingredient:
                    return o
    return None


def _exact_plate_ready(engine: UnifiedEngine, order: Any) -> bool:
    card = engine.spec.recipe_cards[order.dish]
    unplated = [h for h in engine.hands if not h.is_plate]
    if len(unplated) != len(card):
        return False
    counts: dict[tuple[str, str], int] = {}
    for h in unplated:
        counts[(h.ingredient, h.state)] = counts.get((h.ingredient, h.state), 0) + 1
    needed = {(ing, st): 1 for ing, st in card.items()}
    return counts == needed


def _missing_components(engine: UnifiedEngine, order: Any) -> list[tuple[str, str]]:
    """Components of `order` not currently held in terminal state."""
    card = engine.spec.recipe_cards[order.dish]
    held = _count_hands(engine)
    missing = []
    for ing, st in sorted(card.items()):
        if held.get((ing, st), 0) > 0:
            held[(ing, st)] -= 1
        else:
            missing.append((ing, st))
    return missing


def _burner_jobs(engine: UnifiedEngine) -> list[tuple[int, Any]]:
    return [(i, b.job) for i, b in enumerate(engine.burners) if b.job is not None and not b.job.burned]


def _free_burners(engine: UnifiedEngine) -> int:
    return sum(1 for b in engine.burners if b.job is None)


def _wait_step(seconds: float) -> ToolCall:
    s = max(1, min(MAX_WAIT_GS, int(math.ceil(seconds - 1e-9))))
    return ToolCall("wait", {"seconds": s})


def _do_collect_and_prep_to_cook(engine: UnifiedEngine, ing: str) -> ToolCall | None:
    """Return next single action to get `ing` onto a free burner."""
    rule = engine.ingredient_rules[ing]
    held = _count_hands(engine)
    if rule.cookable_from == config.CHOPPED:
        if held.get((ing, config.CHOPPED), 0) > 0:
            return ToolCall("cook", {"ingredient": ing})
        if held.get((ing, config.RAW), 0) > 0:
            return ToolCall("chop", {"ingredient": ing})
        if len(engine.hands) < config.HAND_SLOTS:
            return ToolCall("collect", {"ingredient": ing})
        return None
    else:
        if held.get((ing, config.RAW), 0) > 0:
            return ToolCall("cook", {"ingredient": ing})
        if len(engine.hands) < config.HAND_SLOTS:
            return ToolCall("collect", {"ingredient": ing})
        return None


def _do_non_cooked_step(engine: UnifiedEngine, ing: str, req_state: str) -> ToolCall | None:
    held = _count_hands(engine)
    if req_state == config.CHOPPED:
        if held.get((ing, config.RAW), 0) > 0:
            return ToolCall("chop", {"ingredient": ing})
    if len(engine.hands) < config.HAND_SLOTS:
        return ToolCall("collect", {"ingredient": ing})
    return None


def run_sequential_solver(spec: UnifiedKitchen) -> dict[str, Any]:
    """Strict 1-order-at-a-time EDF baseline (never starts a second order's cooking early)."""
    engine = UnifiedEngine(spec, record_trace=False, early_stop=True)
    while not engine.terminated and engine._action_count < MAX_DECISIONS:
        plate_o = _held_plate_order(engine)
        if plate_o is not None:
            engine.step_batch([ToolCall("serve", {"order_id": plate_o.order_id})])
            continue

        actives = _active_orders(engine)
        if not actives:
            nxt = _next_pending_arrival(engine)
            if nxt is None:
                break
            engine.step_batch([_wait_step(max(1.0, nxt - engine.clock_gs))])
            continue

        target = actives[0]
        if _exact_plate_ready(engine, target):
            engine.step_batch([ToolCall("plate", {"recipe": target.dish})])
            continue

        missing = _missing_components(engine, target)
        # Check if any unplated item in hand is NOT needed for target (shouldn't happen in seq, discard if so)
        card = spec.recipe_cards[target.dish]
        extra = None
        for h in engine.hands:
            if not h.is_plate:
                req = card.get(h.ingredient)
                rule = engine.ingredient_rules[h.ingredient]
                ok_intermediate = (
                    (req == config.CHOPPED and h.state == config.RAW)
                    or (req == config.COOKED and h.state in (config.RAW, config.CHOPPED)
                        and (h.state == rule.cookable_from or (rule.cookable_from == config.CHOPPED and h.state == config.RAW)))
                )
                if req != h.state and not ok_intermediate:
                    extra = h.ingredient
                    break
        if extra is not None:
            engine.step_batch([ToolCall("discard", {"item": extra})])
            continue

        # 1. Pick up any READY cooked item needed for target
        ready_pick = None
        cooking_for_target = []
        missing_cooked = [ing for ing, st in missing if st == config.COOKED]
        for idx, job in _burner_jobs(engine):
            if job.ingredient in missing_cooked:
                if job.status(engine.clock_gs) == "READY":
                    ready_pick = (idx, job.ingredient)
                    break
                else:
                    cooking_for_target.append((idx, job))
        if ready_pick is not None and len(engine.hands) < config.HAND_SLOTS:
            engine.step_batch([ToolCall("collect_cooked", {"ingredient": ready_pick[1], "burner_index": ready_pick[0]})])
            continue

        # 2. Start cooking any remaining needed cooked component for target
        already_on_burner = [job.ingredient for _, job in _burner_jobs(engine)]
        to_cook = []
        temp_burn = list(already_on_burner)
        for ing in missing_cooked:
            if ing in temp_burn:
                temp_burn.remove(ing)
            else:
                to_cook.append(ing)
        # Prioritize longest cook_time first within target order
        to_cook.sort(key=lambda ing: -engine.ingredient_rules[ing].cook_time)

        act = None
        if to_cook and _free_burners(engine) > 0:
            act = _do_collect_and_prep_to_cook(engine, to_cook[0])
        if act is None:
            # 3. Prep non-cooked components of target while waiting
            for ing, st in missing:
                if st != config.COOKED:
                    act = _do_non_cooked_step(engine, ing, st)
                    if act is not None:
                        break
        if act is None:
            # 4. Wait until earliest burner for target is ready
            if cooking_for_target:
                rem = min(max(1.0, j.ready_gs - engine.clock_gs) for _, j in cooking_for_target)
                act = _wait_step(rem)
            else:
                act = ToolCall("wait", {"seconds": 1})
        engine.step_batch([act])

    return {
        "served": engine.counters["orders_served"],
        "total": len(spec.orders),
        "clear": engine.counters["orders_served"] == len(spec.orders),
        "invalid": engine.counters["invalid_actions"],
        "burns": engine.counters["burns"],
        "actions": engine._action_count,
        "clock_gs": round(engine.clock_gs, 2),
    }


def run_greedy_overlap_solver(spec: UnifiedKitchen) -> dict[str, Any]:
    """Myopic overlap solver: fills all free burners across active orders greedily without
    checking burn-window conflicts or plating collisions."""
    engine = UnifiedEngine(spec, record_trace=False, early_stop=True)
    while not engine.terminated and engine._action_count < MAX_DECISIONS:
        plate_o = _held_plate_order(engine)
        if plate_o is not None:
            engine.step_batch([ToolCall("serve", {"order_id": plate_o.order_id})])
            continue

        actives = _active_orders(engine)
        if not actives:
            nxt = _next_pending_arrival(engine)
            if nxt is None:
                break
            engine.step_batch([_wait_step(max(1.0, nxt - engine.clock_gs))])
            continue

        # Plate any active order we already hold completely
        plated = False
        for o in actives:
            if _exact_plate_ready(engine, o):
                engine.step_batch([ToolCall("plate", {"recipe": o.dish})])
                plated = True
                break
        if plated:
            continue

        target = actives[0]
        # Count total needed cooked items across all active orders minus what is already held/cooking
        burner_ings = [j.ingredient for _, j in _burner_jobs(engine)]
        target_missing = _missing_components(engine, target)
        target_missing_cooked = [ing for ing, st in target_missing if st == config.COOKED]

        # 1. Pick up any READY item needed by target
        picked = False
        for idx, job in sorted(_burner_jobs(engine), key=lambda x: x[1].burn_gs):
            if job.status(engine.clock_gs) == "READY" and job.ingredient in target_missing_cooked:
                if len(engine.hands) < config.HAND_SLOTS:
                    engine.step_batch([ToolCall("collect_cooked", {"ingredient": job.ingredient, "burner_index": idx})])
                    picked = True
                    break
        if picked:
            continue

        # 2. If any burner is free AND hands have room, naively start cooking across active orders
        act = None
        if _free_burners(engine) > 0:
            temp_burn = list(burner_ings)
            for o in actives:
                for ing, st in _missing_components(engine, o):
                    if st == config.COOKED:
                        if ing in temp_burn:
                            temp_burn.remove(ing)
                        else:
                            cand = _do_collect_and_prep_to_cook(engine, ing)
                            if cand is not None:
                                act = cand
                                break
                if act is not None:
                    break

        # 3. Prep non-cooked components of target
        if act is None:
            for ing, st in target_missing:
                if st != config.COOKED:
                    act = _do_non_cooked_step(engine, ing, st)
                    if act is not None:
                        break

        # 4. Wait for earliest burner to become ready
        if act is None:
            cooking = [j for _, j in _burner_jobs(engine) if j.status(engine.clock_gs) == "COOKING"]
            if cooking:
                rem = min(max(1.0, j.ready_gs - engine.clock_gs) for j in cooking)
                act = _wait_step(rem)
            else:
                # Stalemate fallback: discard unneeded item if hands full
                card = spec.recipe_cards[target.dish]
                discard_item = None
                for h in engine.hands:
                    if not h.is_plate and card.get(h.ingredient) != h.state:
                        discard_item = h.ingredient
                        break
                if discard_item:
                    act = ToolCall("discard", {"item": discard_item})
                else:
                    act = ToolCall("wait", {"seconds": 1})
        engine.step_batch([act])

    return {
        "served": engine.counters["orders_served"],
        "total": len(spec.orders),
        "clear": engine.counters["orders_served"] == len(spec.orders),
        "invalid": engine.counters["invalid_actions"],
        "burns": engine.counters["burns"],
        "actions": engine._action_count,
        "clock_gs": round(engine.clock_gs, 2),
    }


def scheduled_witness(
    spec: UnifiedKitchen,
    *,
    allow_arrival_peek: bool = True,
) -> tuple[list[list[dict[str, Any]]], dict[str, float]]:
    """Burner-aware parallel scheduler that overlaps active orders safely (0 invalids, 0 burns).

    Key scheduling strategy:
    - Assigns each burner job explicitly to a specific order_id (no accidental stealing!).
    - Overlaps cooking of later active orders on free burners ONLY when their ready_gs falls
      safely AFTER (or close enough to) the current focus order's finish window so nothing burns
      while hands are busy plating another order.
    - Focuses unplated hands exclusively on the earliest-completable active order (so `plate`
      never collides with stray ingredients).
    - Emits clean batches (1 action per witness step, verified with exact serve-start times).
    - When `allow_arrival_peek=False`, operates as a disclosed no-oracle reference scheduler
      that never inspects hidden PENDING order arrival times.
    """
    engine = UnifiedEngine(spec, record_trace=False, early_stop=False)
    batches: list[list[dict[str, Any]]] = []
    serve_start_times: dict[str, float] = {}
    # Track which order each burner index is cooking for: burner_idx -> order_id
    burner_owner: dict[int, str] = {}
    # Track which order currently "owns" unplated hand slots
    hand_owner: str | None = None

    def do(call: ToolCall) -> set[int]:
        nonlocal hand_owner
        if engine.terminated or engine._action_count >= MAX_DECISIONS:
            raise RuntimeError(f"witness terminated before executing {call} (reason={engine.termination_reason})")
        if call.name == "serve":
            oid = call.arguments["order_id"]
            # Record serve start time (approximate arrival + current clock; engine checks clock <= deadline before serve)
            serve_start_times[oid] = engine.clock_gs
        before_burners = {i for i, b in enumerate(engine.burners) if b.job is not None}
        res = engine.step_batch([call])
        batches.append([{"name": call.name, "arguments": dict(call.arguments)}])
        if not res.get("calls") or not res["calls"][0]["ok"]:
            raise RuntimeError(f"witness invalid action {call}: {res}")
        if engine.counters["burns"] > 0:
            raise RuntimeError(f"witness burned food after {call} at t={engine.clock_gs}")
        # Clean up burner ownership for emptied burners
        for i, b in enumerate(engine.burners):
            if b.job is None:
                burner_owner.pop(i, None)
        # If hands now have no unplated items, release hand_owner
        if _unplated_count(engine) == 0:
            hand_owner = None
        return before_burners

    def order_cooked_needed(order: Any) -> list[str]:
        """Cooked ingredients of `order` neither held (when hand_owner==order) nor owned on a burner."""
        card = spec.recipe_cards[order.dish]
        needed = [ing for ing, st in sorted(card.items()) if st == config.COOKED]
        if hand_owner == order.order_id:
            held = _count_hands(engine)
            rem = []
            for ing in needed:
                if held.get((ing, config.COOKED), 0) > 0:
                    held[(ing, config.COOKED)] -= 1
                else:
                    rem.append(ing)
            needed = rem
        owned_on_burner = [
            engine.burners[i].job.ingredient
            for i, oid in burner_owner.items()
            if oid == order.order_id and engine.burners[i].job is not None
        ]
        rem2 = []
        for ing in needed:
            if ing in owned_on_burner:
                owned_on_burner.remove(ing)
            else:
                rem2.append(ing)
        # Longest cook time first!
        rem2.sort(key=lambda ing: -engine.ingredient_rules[ing].cook_time)
        return rem2

    def order_est_ready_time(order: Any) -> float:
        """Estimate when all cooked items of `order` will be ready on burners."""
        t = engine.clock_gs
        for i, oid in burner_owner.items():
            if oid == order.order_id and engine.burners[i].job is not None:
                t = max(t, engine.burners[i].job.ready_gs)
        for ing in order_cooked_needed(order):
            rule = engine.ingredient_rules[ing]
            prep = config.COLLECT_GS + (config.CHOP_GS if rule.cookable_from == config.CHOPPED else 0.0) + config.COOK_START_GS + 2.0
            t = max(t, engine.clock_gs + prep + rule.cook_time)
        return t

    def order_earliest_burn_time(order: Any) -> float:
        burns = [
            engine.burners[i].job.burn_gs
            for i, oid in burner_owner.items()
            if oid == order.order_id and engine.burners[i].job is not None
        ]
        return min(burns) if burns else math.inf

    def order_all_cooked_started(order: Any) -> bool:
        return len(order_cooked_needed(order)) == 0

    def launch_time_for_other(candidate_order: Any, ing: str, focus: Any) -> float:
        """Earliest clock time we can start `ing` for `candidate_order` so it becomes READY right
        around when `focus` finishes plating+serving (maximum safe pipelining!)."""
        rule = engine.ingredient_rules[ing]
        prep_cost = (
            config.COLLECT_GS
            + (config.CHOP_GS if rule.cookable_from == config.CHOPPED else 0.0)
            + config.COOK_START_GS
            + 2.0
        )
        focus_ready = order_est_ready_time(focus)
        # Non-cooked items of focus are prepped BEFORE focus finishes cooking, so clearing focus
        # after its last burner is ready only takes: pickup(1s) + plate(5s) + serve(3s) + walk(~2s) = ~11s.
        target_ready = focus_ready + 11.5
        # Aim for ready_gs >= target_ready - 2.0 (so burn_gs = ready_gs + burn_window >= target_ready + 5s)
        earliest = target_ready - 2.0 - (prep_cost + rule.cook_time)
        return max(engine.clock_gs, earliest)

    def can_safely_prestart_cook(candidate_order: Any, ing: str, focus: Any) -> bool:
        """Check whether starting `ing` for `candidate_order` right now is 100% burn-safe."""
        rule = engine.ingredient_rules[ing]
        prep_cost = (
            config.COLLECT_GS
            + (config.CHOP_GS if rule.cookable_from == config.CHOPPED else 0.0)
            + config.COOK_START_GS
            + 2.5
        )
        # 1. Must not cause ANY running burner to burn during prep + safe pickup margin.
        for b in engine.burners:
            if b.job is not None:
                if engine.clock_gs + prep_cost + 4.5 >= b.job.burn_gs:
                    return False

        new_ready = engine.clock_gs + prep_cost + rule.cook_time
        new_burn = new_ready + rule.burn_window

        if candidate_order.order_id != focus.order_id:
            if hand_owner == focus.order_id and _unplated_count(engine) > 0:
                return False
            focus_ready = order_est_ready_time(focus)
            # Don't start prepping other right as focus is about to become ready!
            if engine.clock_gs + prep_cost + 2.0 >= focus_ready:
                return False
            # New item must NOT burn before focus finishes clearing (~12s after focus_ready + 4s margin).
            if new_burn < focus_ready + 15.5:
                return False
            other_owned = {oid for oid in burner_owner.values() if oid != focus.order_id}
            if candidate_order.order_id not in other_owned and len(other_owned) >= 1:
                return False
            # Also ensure all items of candidate_order land cleanly within each other's burn windows.
            existing_cand_burns = [
                engine.burners[i].job.burn_gs
                for i, oid in burner_owner.items()
                if oid == candidate_order.order_id and engine.burners[i].job is not None
            ]
            if existing_cand_burns and new_ready + 4.5 >= min(existing_cand_burns):
                return False
        return True

    def start_cook_for(order: Any, ing: str) -> None:
        rule = engine.ingredient_rules[ing]
        do(ToolCall("collect", {"ingredient": ing}))
        if rule.cookable_from == config.CHOPPED:
            do(ToolCall("chop", {"ingredient": ing}))
        before = {i for i, b in enumerate(engine.burners) if b.job is not None}
        do(ToolCall("cook", {"ingredient": ing}))
        after = {i for i, b in enumerate(engine.burners) if b.job is not None}
        added = after - before
        if len(added) != 1:
            raise RuntimeError("expected exactly 1 newly occupied burner")
        burner_owner[added.pop()] = order.order_id

    while not engine.terminated and engine._action_count < MAX_DECISIONS:
        # 1. Always serve any held plate immediately!
        plate_o = _held_plate_order(engine)
        if plate_o is not None:
            do(ToolCall("serve", {"order_id": plate_o.order_id}))
            continue

        actives = _active_orders(engine)
        if not actives:
            if allow_arrival_peek:
                nxt = _next_pending_arrival(engine)
                if nxt is None:
                    break
                do(_wait_step(max(1.0, nxt - engine.clock_gs)))
            else:
                if not any(o.status == "PENDING" for o in engine._order_list):
                    break
                do(ToolCall("wait", {"seconds": 1}))
            continue

        # 2. Choose focus order: if hands already belong to an active order, stay on it!
        active_by_id = {o.order_id: o for o in actives}
        if hand_owner in active_by_id:
            focus = active_by_id[hand_owner]
        else:
            # Prefer an active order that already has items burning soonest / ready soonest
            def focus_key(o: Any) -> tuple[float, float, str]:
                has_burn = order_earliest_burn_time(o)
                if math.isfinite(has_burn):
                    return (0.0, has_burn, o.order_id)
                return (1.0, o.arrival_gs, o.order_id)
            focus = min(actives, key=focus_key)

        # 3. If focus is ready to plate, plate it right now!
        if hand_owner == focus.order_id and _exact_plate_ready(engine, focus):
            do(ToolCall("plate", {"recipe": focus.dish}))
            continue

        # 4. Pick up any READY burner item belonging to `focus` right away!
        ready_focus = [
            (i, engine.burners[i].job)
            for i, oid in burner_owner.items()
            if oid == focus.order_id
            and engine.burners[i].job is not None
            and engine.burners[i].job.status(engine.clock_gs) == "READY"
        ]
        ready_focus.sort(key=lambda x: x[1].burn_gs)
        if ready_focus and len(engine.hands) < config.HAND_SLOTS:
            idx, job = ready_focus[0]
            hand_owner = focus.order_id
            do(ToolCall("collect_cooked", {"ingredient": job.ingredient, "burner_index": idx}))
            continue

        # 5. Start cooking needed items for `focus` if burner is free and hands have space!
        focus_to_cook = order_cooked_needed(focus)
        if focus_to_cook and _free_burners(engine) > 0 and len(engine.hands) < config.HAND_SLOTS:
            if can_safely_prestart_cook(focus, focus_to_cook[0], focus):
                # Only start if hands won't clash (remember cook empties the hand slot immediately!)
                start_cook_for(focus, focus_to_cook[0])
                continue

        # 6. Pre-start cooking for OTHER active orders on free burners (overlap!) when hands are empty of unplated items!
        started_other = False
        if _free_burners(engine) > 0 and _unplated_count(engine) == 0 and len(engine.hands) < config.HAND_SLOTS:
            for other in actives:
                if other.order_id == focus.order_id:
                    continue
                other_to_cook = order_cooked_needed(other)
                if other_to_cook and can_safely_prestart_cook(other, other_to_cook[0], focus):
                    start_cook_for(other, other_to_cook[0])
                    started_other = True
                    break
        if started_other:
            continue

        # 7. Prep non-cooked components of `focus` (only when all focus cooked items are already started OR no free burner,
        #    and won't cause any focus burner item to burn during prep!)
        missing_focus = _missing_components(engine, focus)
        non_cooked = [(ing, st) for ing, st in missing_focus if st != config.COOKED]
        if non_cooked and len(engine.hands) < config.HAND_SLOTS:
            ing, st = non_cooked[0]
            step_cost = (config.CHOP_GS if st == config.CHOPPED else config.COLLECT_GS) + 3.0
            earliest_b = min((b.job.burn_gs for b in engine.burners if b.job is not None), default=math.inf)
            if engine.clock_gs + step_cost + 2.5 < earliest_b:
                act = _do_non_cooked_step(engine, ing, st)
                if act is not None:
                    hand_owner = focus.order_id
                    do(act)
                    continue

        # 8. Wait precisely until the earliest relevant event:
        #    - a pipelined launch window for the next active order (if a burner is free & hands empty!)
        #    - a focus burner becoming ready
        #    - a new pending order arriving while a burner is free
        cooking_focus = [
            engine.burners[i].job.ready_gs
            for i, oid in burner_owner.items()
            if oid == focus.order_id
            and engine.burners[i].job is not None
            and engine.burners[i].job.status(engine.clock_gs) == "COOKING"
        ]
        if cooking_focus:
            target_t = min(cooking_focus)
            # Check if we can wake up earlier to pipeline-launch the next order's cook!
            if _free_burners(engine) > 0 and _unplated_count(engine) == 0:
                other_owned = {oid for oid in burner_owner.values() if oid != focus.order_id}
                for other in actives:
                    if other.order_id == focus.order_id:
                        continue
                    if other.order_id not in other_owned and len(other_owned) >= 1:
                        continue
                    other_to_cook = order_cooked_needed(other)
                    if other_to_cook:
                        lt = launch_time_for_other(other, other_to_cook[0], focus)
                        if engine.clock_gs + 0.5 < lt < target_t - 1.0:
                            target_t = min(target_t, lt)
                            break
                # Also wake up if a hidden order arrives before focus finishes cooking!
                if allow_arrival_peek:
                    nxt_arr = _next_pending_arrival(engine)
                    if nxt_arr is not None and engine.clock_gs + 0.5 < nxt_arr < target_t - 1.0:
                        target_t = min(target_t, nxt_arr)
            # Cap wait so we don't accidentally burn ANY existing burner job!
            earliest_any_burn = min(
                (b.job.burn_gs for b in engine.burners if b.job is not None),
                default=math.inf,
            )
            dt = max(1.0, target_t - engine.clock_gs)
            if engine.clock_gs + dt >= earliest_any_burn - 2.0:
                if _unplated_count(engine) == 0:
                    ready_jobs = [
                        (i, b.job)
                        for i, b in enumerate(engine.burners)
                        if b.job is not None and b.job.status(engine.clock_gs) == "READY"
                    ]
                    if ready_jobs:
                        urgent = min(ready_jobs, key=lambda x: x[1].burn_gs)
                        hand_owner = burner_owner[urgent[0]]
                        do(ToolCall("collect_cooked", {"ingredient": urgent[1].ingredient, "burner_index": urgent[0]}))
                        continue
                dt = max(1.0, math.floor(earliest_any_burn - engine.clock_gs - 2.0))
            do(_wait_step(dt))
            continue

        # Fallback wait (e.g. waiting for a burner to free or order to arrive)
        all_cooking = [b.job.ready_gs for b in engine.burners if b.job is not None and b.job.status(engine.clock_gs) == "COOKING"]
        if all_cooking:
            do(_wait_step(max(1.0, min(all_cooking) - engine.clock_gs)))
        else:
            do(ToolCall("wait", {"seconds": 1}))

    return batches, serve_start_times
