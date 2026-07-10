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

SYSTEM_PROMPT = f"""You are the chef in Kitchen Rush, a real-time kitchen. GOAL: serve each
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

CHAIN YOUR CALLS — this is essential: emit a SEQUENCE of actions in ONE response (up to
{config.MAX_CALLS_PER_RESPONSE} per response; any beyond that are dropped, not executed). They
run in order and you are charged thinking time only ONCE, so a multi-step chain is far faster
and scores better than one call per turn. Example for a burger (needs bun=RAW, patty=COOKED),
all in a SINGLE response:
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
            parts = []
            for c in results:
                name = c.get("call", c.get("action", "?"))
                if c.get("ok"):
                    parts.append(f"{name}: ok")
                else:
                    parts.append(f"{name}: INVALID [{c.get('category', 'invalid')}] — {c.get('note', '')}")
            lines.append("RESULT of your last response: " + "; ".join(parts))
    if obs.get("events_since_last"):
        lines.append("events: " + "; ".join(f"{e['type']}@{e['clock_gs']}" for e in obs["events_since_last"]))
    return "\n".join(lines)


class ModelAgent:
    """A policy that drives the engine with a ModelClient."""

    def __init__(self, client: ModelClient, *, track: str = "rp",
                 temperature: float = config.DEFAULT_TEMPERATURE,
                 system_prompt: str = SYSTEM_PROMPT, stall_seconds: float = 30.0,
                 clock: tuple[float, float, float] | None = None,
                 num_retries: int = 2, fail_fast: bool = False) -> None:
        if track not in ("rt", "rp", "calibrated"):
            raise ValueError(f"track must be 'rt', 'rp', or 'calibrated', got {track!r}")
        if track == "calibrated" and clock is None:
            raise ValueError("track='calibrated' requires clock=(beta0, beta_in, beta_out)")
        self.client = client
        self.track = track
        self.temperature = temperature
        self.system = system_prompt
        self.stall_seconds = stall_seconds
        self.num_retries = num_retries
        # fail_fast: re-raise client/infra errors instead of degrading to a scored stall, so an
        # unavailable endpoint / unsupported level becomes an infra-invalid episode (quarantined)
        # rather than a fake low-KR "model quality" result. Used by calibration + the scored board.
        self.fail_fast = fail_fast
        # Frozen per-model calibrated clock coefficients (beta0, beta_in, beta_out); beta_in is 0
        # in the 2-param fit but kept for the RP-compatible shape.
        self.clock = clock
        # Per-turn audit of the provider-trusted reasoning-token gap (RULES §3.2.1, METHODOLOGY
        # §3.1): whether the last response actually reported a reasoning-token count, and the count
        # used in the latency math. None until the first model call (stalls leave them unset).
        self.last_reasoning_reported: bool | None = None
        self.last_reasoning_tokens: int | None = None
        # Per-turn pinned token counts + measured wall-clock, surfaced for calibration + QA drift.
        self.last_n_in: int | None = None
        self.last_n_out: int | None = None
        self.last_live_latency_s: float | None = None

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
        # Clear per-turn audit state up front so a failed call never logs the previous turn's values.
        self.last_reasoning_reported = self.last_reasoning_tokens = None
        self.last_n_in = self.last_n_out = self.last_live_latency_s = None
        try:
            resp = self.client.generate(
                system=self.system, messages=messages, tools=tools, temperature=self.temperature,
                num_retries=self.num_retries,
            )
        except Exception as exc:  # noqa: BLE001
            if self.fail_fast:    # propagate infra/API errors -> caller quarantines (not a scored stall)
                raise
            import sys
            print(f"[ModelAgent] stall: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            return [], self.stall_seconds
        # Audit the provider-trusted reasoning-token gap (RULES §3.2.1). `reasoning_reported` is
        # False when the provider returned no reasoning-token field at all (vs reporting 0); the
        # count below is what actually enters the RP latency math.
        reasoning_tokens = int(resp.usage.get("reasoning_tokens", 0) or 0)
        self.last_reasoning_reported = bool(resp.usage.get("reasoning_reported", False))
        self.last_reasoning_tokens = reasoning_tokens
        # Pinned-tokenizer counts of ALL model-visible request content (system + observation + tool
        # schemas) and the canonical assistant output (text + each tool call's NAME and arguments),
        # plus the provider's self-reported reasoning tokens (NOT recomputable from the transcript —
        # RULES §3.2.1). Computed every turn so calibration/QA can log them regardless of track.
        n_in = (count_tokens(self.system) + count_tokens(user)
                + count_tokens(json.dumps(tools, sort_keys=True)))
        out_text = (resp.text or "") + "".join(
            c.name + json.dumps(c.arguments, sort_keys=True) for c in resp.tool_calls)
        n_out = count_tokens(out_text) + reasoning_tokens
        # Enforcement for hidden/encrypted reasoning (RULES §3.2.1): some APIs (e.g. claude-sonnet-5
        # adaptive thinking) return the reasoning encrypted and report 0 reasoning tokens while still
        # billing them inside completion_tokens. Without this the model would think for free. When
        # detected, charge the provider's true output count. Applied to n_out globally so BOTH the RP
        # and the calibrated clock charge it; gated on has_hidden_thinking → strict no-op for every
        # honest-reporting or non-thinking model.
        if resp.usage.get("has_hidden_thinking"):
            n_out = max(n_out, int(resp.usage.get("completion_tokens", 0) or 0))
        self.last_n_in, self.last_n_out = n_in, n_out
        self.last_live_latency_s = resp.latency_s
        if self.track == "rt":
            latency_s = resp.latency_s
        elif self.track == "calibrated":
            b0, b_in, b_out = self.clock
            latency_s = max(0.05, b0 + b_in * n_in + b_out * n_out)
        else:
            latency_s = rp_latency_seconds(n_in, n_out)
        return resp.tool_calls, latency_s
