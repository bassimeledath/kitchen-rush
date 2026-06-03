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
        "Optional: pre-position the chef near cell [row, col] to cut future travel time. You do "
        "NOT need this to use a station — every station action walks you there automatically. "
        "Targeting a station's own cell stops you on the floor cell beside it. Cost = distance.",
        {
            "row": {"type": "integer", "minimum": 0},
            "col": {"type": "integer", "minimum": 0},
        },
        ["row", "col"],
    ),
    _tool(
        "collect",
        "Collect one raw ingredient from its dispenser. Walks you to the dispenser (travel costs time).",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "chop",
        "Chop a held raw ingredient. Walks you to a cutting board (travel costs time).",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "prep",
        "Alias of chop. Walks you to a cutting board (travel costs time).",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "cook",
        "Put a held ingredient on a free burner to cook. Walks you to a free stove (travel costs time).",
        {"ingredient": {"type": "string", "enum": _INGREDIENT_ENUM}},
        ["ingredient"],
    ),
    _tool(
        "collect_cooked",
        "Take a ready (or burned) item off its burner. Walks you to that stove (travel costs time).",
        {
            "ingredient": {"type": "string", "enum": _INGREDIENT_ENUM},
            "burner_index": {"type": "integer", "minimum": 0},
        },
        ["ingredient"],
    ),
    _tool(
        "plate",
        "Assemble a finished dish from held components. Walks you to a plating counter (travel costs time).",
        {"recipe": {"type": "string", "enum": _RECIPE_ENUM}},
        ["recipe"],
    ),
    _tool(
        "serve",
        "Serve a held finished dish to an active order. Walks you to the pass (travel costs time).",
        {"order_id": {"type": "string"}},
        ["order_id"],
    ),
    _tool(
        "discard",
        "Throw a held item in the bin (free for burned items). Walks you to the bin (travel costs time).",
        {"item": {"type": "string"}},
        ["item"],
    ),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}
