"""Greedy-EDF reference scheduler + the null floor (METHODOLOGY §1).

The oracle is a deterministic scripted policy that plays an instance robustly: earliest-
deadline-first, one order at a time, cooking one item at a time so nothing burns while it is
busy elsewhere, and recovering from burns / junk inventory so it never deadlocks. Station
actions auto-navigate, so the oracle only decides WHAT to do (no pathfinding of its own).

Run at zero latency it gives ``S_ref`` (the headline's reference ceiling); run at injected
latencies it drives difficulty calibration. ``null_score`` is the analytic floor (serve
nothing → every order expires).

It is NOT claimed optimal — it is a strong, consistent, *complete* reference (job-shop with
travel + deadlines is NP-hard). Being deterministic, it keeps the KR scale reproducible.
"""

from __future__ import annotations

from . import config, scoring
from .tools import ToolCall

_RAW, _CHOPPED, _COOKED = config.RAW, config.CHOPPED, config.COOKED


def null_score(spec) -> float:
    """Analytic 'do nothing' floor: serve nothing, every order expires, no other penalties."""
    return float(sum(scoring.expiry_penalty(scoring.base_value(o.dish)) for o in spec.orders))


class OracleAgent:
    """Greedy-EDF policy usable as a runner policy: (obs, tools) -> (calls, latency).

    Station actions auto-walk to their station, so each decision is a single tool call and
    travel time is charged inside it (same game-time as manual movement, fewer turns)."""

    def __init__(self, latency: float = 0.0) -> None:
        self.latency = latency
        self._current: str | None = None   # order_id currently being assembled (sticky)

    def __call__(self, obs: dict, tools: list[dict]) -> tuple[list[ToolCall], float]:
        call = self._decide(obs)
        if call is None:                              # nothing to do / waiting on a cook
            return [ToolCall("observe", {})], self.latency
        return [call], self.latency

    # -- decision (robust greedy EDF) -----------------------------------------
    def _decide(self, obs: dict) -> ToolCall | None:
        hands = obs["hands"]
        burners = obs["burners"]
        active = [o for o in obs["orders"] if o["status"] == "ACTIVE"]
        have_bin = any(s["type"] == config.BIN for s in obs["stations"])
        slots_free = obs["hand_slots_free"]
        cooking = [b for b in burners if b["status"] == "COOKING"]

        def have(ing: str, state: str) -> bool:
            return any(h["ingredient"] == ing and h["state"] == state for h in hands)

        # 1. serve a finished plate to its earliest-deadline matching order
        for h in hands:
            if h["state"] == "PLATE":
                cand = [o for o in active if o["dish"] == h["ingredient"]]
                if cand:
                    o = min(cand, key=lambda o: o["deadline_gs"])
                    return ToolCall("serve", {"order_id": o["order_id"]})

        # 2. recover hand slots: dump held BURNED items and stale plates (no matching order)
        for h in hands:
            if h["state"] == config.BURNED and have_bin:
                return ToolCall("discard", {"item": h["ingredient"]})
        for h in hands:
            if h["state"] == "PLATE" and have_bin \
                    and not any(o["dish"] == h["ingredient"] for o in active):
                return ToolCall("discard", {"item": f"plate:{h['ingredient']}"})

        # 3. clear a BURNED burner so it can be reused (collected -> discarded next turn)
        for b in burners:
            if b["status"] == config.BURNED and slots_free > 0:
                return ToolCall("collect_cooked", {"ingredient": b["ingredient"]})

        # 4. take any READY item off its burner promptly (prevents burns, frees the burner)
        for b in burners:
            if b["status"] == "READY" and slots_free > 0:
                return ToolCall("collect_cooked", {"ingredient": b["ingredient"]})

        # pick the sticky EDF target
        active_ids = {o["order_id"] for o in active}
        if self._current not in active_ids:
            self._current = None
        if self._current is None:
            if not active:
                for h in hands:                       # idle: dump leftover components, start clean
                    if h["state"] != "PLATE" and have_bin:
                        return ToolCall("discard", {"item": h["ingredient"]})
                return None
            self._current = min(active, key=lambda o: o["deadline_gs"])["order_id"]
        target = next(o for o in active if o["order_id"] == self._current)
        recipe = config.RECIPES[target["dish"]]

        # 5. orphan recovery: discard a held component the target does not use, or a duplicate
        needed: dict[str, int] = {}
        for ing in recipe:
            needed[ing] = needed.get(ing, 0) + 1
        held: dict[str, int] = {}
        for h in hands:
            if h["state"] != "PLATE":
                held[h["ingredient"]] = held.get(h["ingredient"], 0) + 1
        for h in hands:
            if h["state"] != "PLATE" and have_bin:
                ing = h["ingredient"]
                if ing not in needed or held[ing] > needed[ing]:
                    return ToolCall("discard", {"item": ing})

        # 6. advance the target — non-cook components first, then cooks ONE AT A TIME so nothing
        #    burns while we are busy elsewhere (robust reference: correctness over throughput).
        for ing, term in sorted(recipe.items(), key=lambda kv: kv[1] == _COOKED):
            if have(ing, term):
                continue
            ic = config.INGREDIENTS[ing]
            on_burner = next((b for b in burners
                              if b["ingredient"] == ing and b["status"] == "COOKING"), None)
            if on_burner is not None:
                return None                            # our item is cooking — wait for it
            if term == _RAW:
                return ToolCall("collect", {"ingredient": ing})
            if term == _CHOPPED:
                if have(ing, _RAW):
                    return ToolCall("chop", {"ingredient": ing})
                return ToolCall("collect", {"ingredient": ing})
            if term == _COOKED:
                if ic.cookable_from == _CHOPPED and not have(ing, _CHOPPED):
                    if have(ing, _RAW):
                        return ToolCall("chop", {"ingredient": ing})
                    return ToolCall("collect", {"ingredient": ing})
                if ic.cookable_from == _RAW and not have(ing, _RAW):
                    return ToolCall("collect", {"ingredient": ing})
                if cooking:                            # one cook at a time — wait for the burner
                    return None
                return ToolCall("cook", {"ingredient": ing})

        if all(have(i, s) for i, s in recipe.items()):
            return ToolCall("plate", {"recipe": target["dish"]})
        return None


def reference_score(spec, latency_s: float = 0.0, max_turns: int | None = None) -> float:
    """Score of the greedy-EDF oracle on this instance at the given per-decision latency.

    The reference runs effectively uncapped on turns (turns are free for a scripted policy);
    its budget is the game-time horizon. Pass ``max_turns`` only to test truncation."""
    from .runner import run_episode  # lazy import to avoid a cycle
    res = run_episode(spec, OracleAgent(latency=latency_s),
                      max_turns=max_turns if max_turns is not None else config.REFERENCE_MAX_TURNS)
    return float(res.report["score_raw"])
