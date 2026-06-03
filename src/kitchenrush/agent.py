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

SYSTEM_PROMPT = """You are the chef in Kitchen Rush, a real-time kitchen game scored on points.

GOAL: maximize points by serving as many orders as possible before their deadlines and
before food burns. Points decay the longer an order waits, so be FAST and accurate.

CRITICAL: your thinking time IS game time. While you deliberate, the clock advances, food
burns, and orders expire. Decide quickly and act.

HOW TO PLAY:
- You stand on a grid and must be next to the right station to act: collect at a dispenser,
  chop at a cutting board, cook at a stove, plate at a plating counter, serve at the pass,
  discard at the bin.
- The kitchen is a grid of [row, col] cells (row 0 = top). You cannot stand ON a station —
  to use one, be on a floor cell orthogonally adjacent to it.
- move_to(row, col) walks you to that floor cell (the kitchen finds the path; cost = path
  length). So to act at a station, move_to a floor cell next to it, then call the action.
- Cooked items sit on a burner; collect_cooked when READY (before they BURN).
- plate(recipe) needs exactly the right finished components in hand; serve(order_id) an
  ACTIVE order whose dish matches your plated dish.
- You MAY issue several tool calls in one response (e.g. move then collect then cook). They
  run in order and you are charged thinking time only ONCE — so plan a few steps ahead.

Respond with native tool call(s) ONLY — no prose, no explanation. Emitting a few chained
calls in one response is encouraged; the kitchen only acts on the calls.
"""


def render_observation(obs: dict) -> str:
    """Render only what a human player perceives: the kitchen layout, where things are, the
    order tickets/timers, the chef's hands, and what just happened. NO hints — no
    pre-computed directions/paths and no list of currently-valid actions (a human gets
    neither; they read the grid and reason themselves)."""
    grid_rows = obs["grid_ascii"].split("\n")
    n = len(grid_rows)
    cr, cc = obs["chef_pos"]
    lines = [
        f"clock={obs['clock_gs']}gs  remaining={obs['remaining_gs']}gs  "
        f"score={obs['score']}  combo={obs['combo_count']}",
        f"you are at [row {cr}, col {cc}]; hands={obs['hands']} (free slots {obs['hand_slots_free']})",
        "kitchen grid (row 0 = north/top; " + obs["grid_legend"] + "):",
        "      col: " + " ".join(str(c) for c in range(n)),
    ]
    for r in range(n):
        lines.append(f"  row {r}:  " + "  ".join(grid_rows[r][c] for c in range(n)))
    lines.append("you must stand on a floor cell ADJACENT to a station to use it; "
                 "move_to(row, col) takes you to any reachable floor cell.")
    lines.append("stations (positions are visible on the grid above):")
    for s in obs["stations"]:
        tag = f"{s['type']}{('/' + s['ingredient']) if s['ingredient'] else ''}"
        lines.append(f"  {tag} @[{s['cell'][0]},{s['cell'][1]}]")
    lines.append(
        "burners: " + ", ".join(
            f"#{b['burner_index']}@{b['cell']}:{b['status']}"
            + (f"({b['ingredient']} ready{b['ready_gs']}/burn{b['burn_gs']})" if b["ingredient"] else "")
            for b in obs["burners"]
        )
    )
    lines.append("orders:")
    for o in obs["orders"]:
        lines.append(f"  {o['order_id']} {o['dish']} [{o['status']}] "
                     f"deadline {o['deadline_gs']}gs ({o['gs_remaining']}gs left) value {o['base_value']}")
    if obs["last_turn"]:
        results = obs["last_turn"].get("calls", [])
        if results:
            lines.append("last turn: " + "; ".join(f"{c.get('action','?')}:{'ok' if c['ok'] else 'INVALID'} "
                                                    f"({c.get('note','')})" for c in results))
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
