"""Reference model-backed policy (Phase 2).

Stateless single-shot per turn: the full observation is the prompt. Kitchen Rush returns
the complete state every turn (RULES §8), so the task is Markovian and no fragile
multi-turn tool-message threading is needed. Each turn the agent renders the observation,
asks the model for native tool calls, and reports the per-turn latency for the chosen track
(rt: measured wall-clock; rp: deterministic token proxy).
"""

from __future__ import annotations

import json

from . import config
from .adapter import ModelClient
from .latency import rp_latency_seconds
from .tokenizer import count_tokens
from .tools import ToolCall

SYSTEM_PROMPT = """You are the chef in Kitchen Rush, a real-time kitchen. GOAL: serve each
order before its deadline. Each active order shows its TIME LEFT; serve it in time to score
(sooner = more — value decays to a floor), or it EXPIRES and you LOSE points. Burned food and
impossible actions also cost points.

TIME IS REAL: the clock that counts every order down also advances while you think and while
you act (walking, prepping, and cooking all take game-time). Make every response count.

ACTIONS — you do NOT navigate manually; each action walks you to the right station
automatically (the walk costs travel time):
- collect(ing): take a raw ingredient from its dispenser.
- chop(ing): chop a held raw ingredient.
- cook(ing): put a held ingredient on a free burner; it becomes READY after a while, then
  BURNS if left too long.
- collect_cooked(ing): take a READY item off the burner (before it burns).
- plate(recipe): assemble the dish once your hands hold EXACTLY its components.
- serve(order_id): deliver a held plated dish to that order.
- move_to(row, col): OPTIONAL — only to pre-position yourself; usually unnecessary.

CHAIN YOUR CALLS — this is essential: emit a SEQUENCE of actions in ONE response. They run in
order and you are charged thinking time only ONCE, so a multi-step chain is far faster and
scores better than one call per turn. Example for a burger (needs bun=RAW, patty=COOKED), all
in a SINGLE response:
  collect("patty"), cook("patty"), collect("bun")
Then, on a later turn once the patty is READY:
  collect_cooked("patty"), plate("burger"), serve("O1")
(You cannot plate until every component is ready, so cook first and do other work while it
cooks.)

MULTITASK: several orders are active and more arrive over time. While a patty cooks, prep or
serve another order — do not wait idly. You need not finish one order before starting the next.

Each turn you receive the full current state: your position, hands, station locations, burner
timers, and every active order WITH ITS TIME LEFT. Respond with tool call(s) ONLY — no prose.
"""


def render_observation(obs: dict) -> str:
    """Render the full, up-to-date state with the deadline-driven ORDERS up front (the
    priority), each with its time left and recipe. No hints — no pre-computed paths and no
    valid-action list; the model reads the grid and reasons itself."""
    grid_rows = obs["grid_ascii"].split("\n")
    n = len(grid_rows)
    cr, cc = obs["chef_pos"]
    active = [o for o in obs["orders"] if o["status"] == "ACTIVE"]
    pending = [o for o in obs["orders"] if o["status"] == "PENDING"]

    lines = [f"clock={obs['clock_gs']}gs  score={obs['score']}  combo={obs['combo_count']}",
             "ACTIVE orders (serve each before its time left hits 0 — this is the goal):"]
    if active:
        for o in sorted(active, key=lambda o: o["gs_remaining"]):
            need = ", ".join(f"{i}={s}" for i, s in config.RECIPES[o["dish"]].items())
            lines.append(f"  {o['order_id']}: {o['dish']}  —  {o['gs_remaining']:.0f}s LEFT  "
                         f"(value {o['base_value']:g}; needs {need})")
    else:
        lines.append("  (none active right now)")
    if pending:
        nxt = min(p["arrival_gs"] for p in pending) - obs["clock_gs"]
        lines.append(f"  (+{len(pending)} more orders incoming; next in ~{max(0, nxt):.0f}s)")

    lines.append(f"you: [row {cr}, col {cc}]   hands={obs['hands']} (free {obs['hand_slots_free']})")
    lines.append("burners: " + ", ".join(
        f"#{b['burner_index']}@{b['cell']}:{b['status']}"
        + (f"({b['ingredient']} ready@{b['ready_gs']}gs/burns@{b['burn_gs']}gs)" if b["ingredient"] else "")
        for b in obs["burners"]))
    lines.append("kitchen grid (row 0 = top; " + obs["grid_legend"] + "):")
    lines.append("      col " + " ".join(str(c) for c in range(n)))
    for r in range(n):
        lines.append(f"  row {r}: " + "  ".join(grid_rows[r][c] for c in range(n)))
    lines.append("stations: " + ", ".join(
        f"{s['type']}{('/' + s['ingredient']) if s['ingredient'] else ''}@[{s['cell'][0]},{s['cell'][1]}]"
        for s in obs["stations"]))
    if obs.get("last_turn"):
        results = obs["last_turn"].get("calls", [])
        if results:
            lines.append("last turn: " + "; ".join(
                f"{c.get('action', '?')}:{'ok' if c['ok'] else 'INVALID'}({c.get('note', '')})"
                for c in results))
    if obs.get("events_since_last"):
        lines.append("events: " + "; ".join(f"{e['type']}@{e['clock_gs']}" for e in obs["events_since_last"]))
    return "\n".join(lines)


class ModelAgent:
    """A policy that drives the engine with a ModelClient."""

    def __init__(self, client: ModelClient, *, track: str = "rp",
                 temperature: float = config.DEFAULT_TEMPERATURE,
                 system_prompt: str = SYSTEM_PROMPT, stall_seconds: float = 30.0) -> None:
        if track not in ("rt", "rp"):
            raise ValueError(f"track must be 'rt' or 'rp', got {track!r}")
        self.client = client
        self.track = track
        self.temperature = temperature
        self.system = system_prompt
        self.stall_seconds = stall_seconds

    def warmup(self, tools: list[dict]) -> None:
        """Spin up the model (e.g. a cold NIM endpoint) with one throwaway call so the first
        *scored* turn isn't charged for one-time cold-start. Result discarded; errors ignored.
        Only the measured-wall-clock (RT) track is affected by cold-start; harmless for RP."""
        try:
            self.client.generate(system=self.system,
                                 messages=[{"role": "user", "content": "Reply 'ready'."}],
                                 tools=tools, temperature=self.temperature)
        except Exception:  # noqa: BLE001 - warmup is best-effort
            pass

    def __call__(self, obs: dict, tools: list[dict]) -> tuple[list[ToolCall], float]:
        user = render_observation(obs)
        messages = [{"role": "user", "content": user}]
        try:
            resp = self.client.generate(
                system=self.system, messages=messages, tools=tools, temperature=self.temperature
            )
        except Exception as exc:  # noqa: BLE001 - any model/infra error degrades to a stall (RULES §13.7)
            import sys
            print(f"[ModelAgent] stall: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            return [], self.stall_seconds
        if self.track == "rt":
            latency_s = resp.latency_s
        else:
            n_in = count_tokens(self.system) + count_tokens(user)
            out_text = (resp.text or "") + "".join(json.dumps(c.arguments) for c in resp.tool_calls)
            n_out = count_tokens(out_text) + int(resp.usage.get("reasoning_tokens", 0) or 0)
            latency_s = rp_latency_seconds(n_in, n_out)
        return resp.tool_calls, latency_s
