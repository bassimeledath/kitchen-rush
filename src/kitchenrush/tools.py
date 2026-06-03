"""Native function-calling tool schemas and the provider-neutral ToolCall type
(RULES.md §15, MOVEMENT.md §1). These schemas are passed to the model as ``tools``;
the engine dispatches a parsed ToolCall in ``engine.step``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import config


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


_INGREDIENT_ENUM = sorted(config.INGREDIENTS.keys())
_RECIPE_ENUM = sorted(config.RECIPES.keys())


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _tool(
        "move_to",
        "Walk to a floor cell [row, col]; the kitchen finds the path. You cannot stand on a "
        "station, so move to a floor cell next to it. Cost = path length in game-seconds.",
        {
            "row": {"type": "integer", "minimum": 0},
            "col": {"type": "integer", "minimum": 0},
        },
        ["row", "col"],
    ),
    _tool("observe", "Return the full kitchen observation. Costs game-time.", {}, []),
    _tool(
        "collect",
        "Pick up one raw ingredient. Must be next to its dispenser.",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "chop",
        "Chop a held raw ingredient. Must be next to a cutting board.",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "prep",
        "Alias of chop. Must be next to a cutting board.",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "cook",
        "Place a held ingredient on a free burner to cook. Must be next to a stove.",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "collect_cooked",
        "Take a ready (or burned) item off a burner. Must be next to a stove.",
        {
            "ingredient": {"type": "string", "enum": _INGREDIENT_ENUM},
            "burner_index": {"type": "integer", "minimum": 0},
        },
        ["ingredient"],
    ),
    _tool(
        "plate",
        "Assemble a finished dish from held components. Must be next to a plating counter.",
        {"recipe": {"type": "string", "enum": _RECIPE_ENUM}},
        ["recipe"],
    ),
    _tool(
        "serve",
        "Serve a held finished dish to an active order. Must be next to the pass.",
        {"order_id": {"type": "string"}},
        ["order_id"],
    ),
    _tool(
        "discard",
        "Throw a held item in the bin (free for burned items). Must be next to the bin.",
        {"item": {"type": "string"}},
        ["item"],
    ),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}
