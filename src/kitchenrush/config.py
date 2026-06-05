"""Canonical constants, ingredient/recipe catalog, and difficulty tiers.

Single source of truth for RULES.md §16 (constants), §2 (entities), and §3.5 (timers).
Stdlib-only. Every other module imports values from here; none restates them.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- §16 constant table -------------------------------------------------------
GRID_N = 7
BURNER_COUNT = 2
HAND_SLOTS = 4
LATENCY_SCALE = 1.0          # latency_seconds -> game-seconds (the one knob, open-q #1)

MOVE_GS_PER_STEP = 0.25      # 4x faster walking (travel is flat overhead, not a tested skill)
COLLECT_GS = 2.0
CHOP_GS = 4.0
PREP_GS = 4.0
COOK_START_GS = 2.0
COOK_PICKUP_GS = 1.0
PLATE_GS = 5.0
SERVE_GS = 3.0
DISCARD_GS = 1.0
OBSERVE_GS = 1.0
INVALID_GS = 3.0

HORIZON_GS = 300.0
# The real time limit is the game-time HORIZON (the world clock). MAX_TURNS is only a paranoid
# anti-runaway ceiling that never binds for a model making progress; STALL_TURNS ends an
# episode that does no productive work for that many consecutive turns (the true safety net).
MAX_TURNS = 500
STALL_TURNS = 50
REFERENCE_MAX_TURNS = 20000   # the scripted oracle's turns are free; let game-time bound it
MAX_STEPS_PER_MOVE = 8       # single-leg cap
SCHEMA_MAX_STEPS = 12        # hard schema cap before clamping
MAX_CALLS_PER_RESPONSE = 6

DECAY_RATE = 0.6
FLOOR_FACTOR = 0.4
V0, V1, V2 = 6.0, 2.0, 0.5   # base_value = V0 + V1*n + V2*n^2
EXPIRY_FRACTION = 0.5
BURN_PENALTY = -8.0
INVALID_PENALTY = -5.0
DROP_PENALTY = -6.0
COMBO_STEP = 0.25
COMBO_CAP = 2.0
COMBO_MIN_STEPS = 4          # min recipe steps to advance the combo streak
SHOW_READY_ACTIONS = True

# RP (reproducible) latency-track proxy (SCORING §1.2; open questions #2/#3).
RP_BETA0 = 0.30              # s, fixed per-call overhead
RP_BETA_IN = 0.0002         # s per input token
RP_BETA_OUT = 0.006         # s per output token (~167 tok/s decode); incl. reasoning tokens

# Reliability + headline (SCORING §6).
THETA_PASS = 0.6            # an episode passes a seed iff score_raw >= THETA_PASS * S_ref
PASS_K = 4                  # trials per seed for Pass^k
DEFAULT_TEMPERATURE = 0.2   # sampling temperature for trials

# Realtime target (METHODOLOGY §2): deadlines are priced at B seconds per decision.
B_SECONDS = 1.0

# --- ingredient states (RULES §2.3) ------------------------------------------
RAW = "RAW"
CHOPPED = "CHOPPED"
COOKED = "COOKED"
BURNED = "BURNED"

# --- station types (RULES §2.2) ----------------------------------------------
ING = "ING"
BOARD = "BOARD"
STOVE = "STOVE"
PLATE = "PLATE"
PASS = "PASS"
BIN = "BIN"


@dataclass(frozen=True)
class Ingredient:
    name: str
    choppable: bool = False
    cookable_from: str | None = None   # the state required before cooking
    cook_time: float = 0.0             # gs (base; procgen may jitter later)
    burn_window: float = 0.0           # gs after ready before it burns


# Catalog (RULES §2.3, timers §3.5).
INGREDIENTS: dict[str, Ingredient] = {
    "patty": Ingredient("patty", cookable_from=RAW, cook_time=8, burn_window=6),
    "bun": Ingredient("bun"),
    "lettuce": Ingredient("lettuce", choppable=True),
    "tomato": Ingredient("tomato", choppable=True),
    "onion": Ingredient("onion", choppable=True),
    "cheese": Ingredient("cheese"),
    "broth_base": Ingredient("broth_base", cookable_from=RAW, cook_time=12, burn_window=8),
    "mushroom": Ingredient("mushroom", choppable=True, cookable_from=CHOPPED, cook_time=5, burn_window=5),
    "noodles": Ingredient("noodles", cookable_from=RAW, cook_time=6, burn_window=5),
    "egg": Ingredient("egg", cookable_from=RAW, cook_time=4, burn_window=3),
}

# Recipes (RULES §2.4): name -> {ingredient: required terminal state}.
RECIPES: dict[str, dict[str, str]] = {
    "burger": {"bun": RAW, "patty": COOKED},
    "soup": {"broth_base": COOKED},
    "salad": {"lettuce": CHOPPED, "tomato": CHOPPED},
    "mushroom_cheeseburger": {"bun": RAW, "patty": COOKED, "mushroom": COOKED, "cheese": RAW},
    "veggie_ramen": {"noodles": COOKED, "broth_base": COOKED, "egg": COOKED, "onion": CHOPPED},
}


def component_pipeline_len(ingredient: str, terminal_state: str) -> int:
    """Number of recipe steps to bring one component to its terminal state (RULES §9.1).

    collect (RAW) = 1; +chop for CHOPPED; +cook for COOKED (chop first if the
    ingredient cooks from CHOPPED, e.g. mushroom).
    """
    ic = INGREDIENTS[ingredient]
    if terminal_state == RAW:
        return 1
    if terminal_state == CHOPPED:
        return 2
    if terminal_state == COOKED:
        return 3 if ic.cookable_from == CHOPPED else 2
    raise ValueError(f"bad terminal state {terminal_state!r}")


def recipe_n_steps(recipe: str) -> int:
    """Total recipe steps incl. the single plate step (excl. serve/collect_cooked)."""
    comps = RECIPES[recipe]
    return 1 + sum(component_pipeline_len(i, s) for i, s in comps.items())


def recipe_ingredients(recipes: tuple[str, ...]) -> list[str]:
    """Sorted union of base ingredients across the given recipes (one dispenser each)."""
    seen: set[str] = set()
    for r in recipes:
        seen.update(RECIPES[r].keys())
    return sorted(seen)


@dataclass(frozen=True)
class Tier:
    name: str
    grid_n: int
    burner_count: int
    horizon_gs: float
    recipes: tuple[str, ...]
    arrival_rate: float        # orders per game-second (controls frequency / overlap)
    slack: float               # sigma: deadline = arrival + ceil(slack * C_o(B))
    show_ready_actions: bool
    max_orders: int


# NOTE: arrival rates are deliberately spaced so the current *sequential* greedy-EDF
# reference (oracle.py) can complete instances (a strong S_ref). Denser, more-overlapping
# tiers await a parallel reference scheduler — see docs/ROADMAP.md.
TIERS: dict[str, Tier] = {
    "easy": Tier(
        "easy", grid_n=7, burner_count=2, horizon_gs=260.0,
        recipes=("burger", "soup", "salad"),
        arrival_rate=1 / 45, slack=1.6, show_ready_actions=True, max_orders=5,
    ),
    "medium": Tier(
        "medium", grid_n=7, burner_count=2, horizon_gs=340.0,
        recipes=("burger", "soup", "salad", "mushroom_cheeseburger"),
        arrival_rate=1 / 50, slack=1.5, show_ready_actions=True, max_orders=6,
    ),
    "hard": Tier(
        "hard", grid_n=9, burner_count=2, horizon_gs=420.0,
        recipes=tuple(RECIPES.keys()),
        arrival_rate=1 / 55, slack=1.4, show_ready_actions=False, max_orders=7,
    ),
}

DIRECTIONS: dict[str, tuple[int, int]] = {
    "north": (-1, 0),
    "south": (+1, 0),
    "east": (0, +1),
    "west": (0, -1),
}
