"""Scoring formulas (RULES.md §9, SCORING.md). Pure functions; the single rounding rule
is ``floor(x + 0.5)`` applied once per serve (RULES §11.6)."""

from __future__ import annotations

import math

from . import config


def base_value(recipe: str) -> float:
    """Superlinear base value in step count (RULES §9.1); prevents cheap-dish farming."""
    n = config.recipe_n_steps(recipe)
    return config.V0 + config.V1 * n + config.V2 * n * n


def time_factor(t: float, arrival_gs: float, deadline_gs: float) -> float:
    """Linear time-decay, no grace plateau (RULES §9.3). 1.0 at arrival -> FLOOR at deadline."""
    span = deadline_gs - arrival_gs
    if span <= 0:
        return config.FLOOR_FACTOR
    f = 1.0 - config.DECAY_RATE * (t - arrival_gs) / span
    return min(1.0, max(config.FLOOR_FACTOR, f))


def combo_multiplier(streak: int) -> float:
    """Combo/tip multiplier (RULES §9.7.3). 1.0 at streak<=1, capped at COMBO_CAP (streak=5)."""
    return min(config.COMBO_CAP, 1.0 + config.COMBO_STEP * max(0, streak - 1))


def round_half_up(x: float) -> int:
    """Round-half-up (NOT banker's rounding); the only rounding in the score path."""
    return math.floor(x + 0.5)


def delivery_reward(bv: float, tf: float, combo: float, q: float = 1.0) -> int:
    """earned = floor(base_value * time_factor * combo * q + 0.5) (RULES §9.2)."""
    return round_half_up(bv * tf * combo * q)


def expiry_penalty(bv: float) -> int:
    """Value-scaled expiry penalty magnitude as a negative int (RULES §9.5)."""
    return -round_half_up(config.EXPIRY_FRACTION * bv)
