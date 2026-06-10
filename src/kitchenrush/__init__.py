"""Kitchen Rush — a benchmark for fast AND accurate native tool calling.

Latency costs points by construction: a model's per-response thinking time is converted to
game-seconds that advance a shared world clock *before* each action resolves, so while the
model deliberates, food burns and orders expire.

The core (engine + procgen + baselines + CLI) is stdlib-only; the model adapter needs the
``providers`` extra (LiteLLM).
"""

from __future__ import annotations

from .adapter import ModelResponse, register_adapter, resolve_model
from .agent import ModelAgent
from .config import TIERS
from .engine import KitchenRushEngine
from .metrics import aggregate
from .procgen import KitchenSpec, generate
from .runner import run_episode, run_suite
from .tools import TOOL_SCHEMAS, ToolCall
from .version import __version__

__all__ = [
    "__version__",
    "TIERS",
    "KitchenRushEngine",
    "KitchenSpec",
    "generate",
    "run_episode",
    "run_suite",
    "aggregate",
    "TOOL_SCHEMAS",
    "ToolCall",
    "ModelAgent",
    "register_adapter",
    "resolve_model",
    "ModelResponse",
]
