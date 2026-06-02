"""Baseline policies that anchor the score range without a model (Phase 1).

A policy is ``(observation, tools) -> (list[ToolCall], latency_seconds)``.
- ``NullAgent`` does nothing every turn (lower-bound anchor).
- ``RandomAgent`` issues random tool calls, occasionally taking a suggested ready action.
"""

from __future__ import annotations

import random

from . import config
from .tools import ToolCall


class NullAgent:
    def __init__(self, latency: float = 0.5) -> None:
        self.latency = latency

    def __call__(self, obs: dict, tools: list[dict]) -> tuple[list[ToolCall], float]:
        return [], self.latency


class RandomAgent:
    def __init__(self, seed: int = 0, latency: float = 0.5) -> None:
        self.rng = random.Random(seed)
        self.latency = latency

    def __call__(self, obs: dict, tools: list[dict]) -> tuple[list[ToolCall], float]:
        ready = [a for a in obs.get("ready_actions", []) if "(" in a and "<" not in a]
        if ready and self.rng.random() < 0.6:
            action = self.rng.choice(ready)
            name, arg = action[:-1].split("(", 1)
            if name in ("collect", "chop", "cook", "collect_cooked"):
                call = ToolCall(name, {"ingredient": arg})
            elif name == "serve":
                call = ToolCall("serve", {"order_id": arg})
            elif name == "plate":
                call = ToolCall("plate", {"recipe": arg})
            else:
                call = ToolCall("observe", {})
        else:
            direction = self.rng.choice(list(config.DIRECTIONS))
            call = ToolCall("move", {"direction": direction, "steps": self.rng.randint(1, config.MAX_STEPS_PER_MOVE)})
        return [call], self.latency
