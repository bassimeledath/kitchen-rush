"""Version + ruleset-hash stamping (METHODOLOGY / DESIGN §Versioning).

The *package* version is independent of the *ruleset*: a change to a scoring constant, recipe,
ingredient timer, or tier starts a new ruleset generation (``ruleset_hash``) so stale leaderboard
rows are invalidated without bumping the package release. ``versions()`` is stamped into every
output artifact (episode report, replay JSON, bench aggregate) for reproducibility and to gate
leaderboard generations.
"""

from __future__ import annotations

import hashlib
import json

__version__ = "0.1.0"
SCHEMA_VERSION = "0.1"        # report / replay JSON schema shape
GENERATOR_VERSION = "0.1"     # procgen instance generator

# Frozen ruleset generation. The official starter leaderboard is computed against this hash;
# any change to a load-bearing constant/recipe/tier (see _RULESET_CONSTANTS) produces a new hash
# and a new generation. NB: the provisional RP β-coefficients are part of the hash, so a future
# β-calibration will bump this — by design (RP is labelled experimental until then).
RULESET_VERSION = "1.0"
FROZEN_RULESET_HASH = "33034952fa7f"   # frozen 2026-06-06 after the calibration panel

# Load-bearing scoring/timing constants — changing any of these changes the ruleset hash.
_RULESET_CONSTANTS = (
    "GRID_N", "BURNER_COUNT", "HAND_SLOTS", "LATENCY_SCALE", "MOVE_GS_PER_STEP", "COLLECT_GS",
    "CHOP_GS", "PREP_GS", "COOK_START_GS", "COOK_PICKUP_GS", "PLATE_GS", "SERVE_GS", "DISCARD_GS",
    "OBSERVE_GS", "INVALID_GS", "HORIZON_GS", "MAX_TURNS", "STALL_TURNS", "MAX_CALLS_PER_RESPONSE",
    "DECAY_RATE", "FLOOR_FACTOR", "V0", "V1", "V2", "EXPIRY_FRACTION", "BURN_PENALTY",
    "INVALID_PENALTY", "DROP_PENALTY", "COMBO_STEP", "COMBO_CAP", "COMBO_MIN_STEPS",
    "RP_BETA0", "RP_BETA_IN", "RP_BETA_OUT", "THETA_PASS", "PASS_K",
)


def ruleset_hash() -> str:
    """Short stable hash of the load-bearing ruleset: constants + ingredient catalog + recipes +
    tiers. Changes whenever any scoring/timing/recipe/tier value changes → a new leaderboard
    generation. Deterministic across runs/machines (sorted JSON, no floats-from-env)."""
    from . import config
    payload = {
        "constants": {k: getattr(config, k) for k in _RULESET_CONSTANTS},
        "ingredients": {n: [i.choppable, i.cookable_from, i.cook_time, i.burn_window]
                        for n, i in sorted(config.INGREDIENTS.items())},
        "recipes": {r: dict(c) for r, c in sorted(config.RECIPES.items())},
        "tiers": {t: [x.grid_n, x.burner_count, x.horizon_gs, list(x.recipes), x.arrival_rate,
                      x.slack, x.show_ready_actions, x.max_orders]
                  for t, x in sorted(config.TIERS.items())},
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def versions() -> dict:
    """The version/hash record stamped into every output artifact."""
    from .tokenizer import TOKENIZER_ID
    return {
        "package": __version__,
        "ruleset": ruleset_hash(),
        "ruleset_version": RULESET_VERSION,
        "frozen": ruleset_hash() == FROZEN_RULESET_HASH,
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR_VERSION,
        "tokenizer": TOKENIZER_ID,
    }
