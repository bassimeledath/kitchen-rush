"""Kitchen Rush — a benchmark for fast AND accurate native tool calling.

Latency costs points by construction: a model's per-response thinking time is converted to
game-seconds that advance a shared world clock *before* each action resolves, so while the
model deliberates, food burns and orders expire.

Phase 1 (engine + procgen + baselines + CLI) is stdlib-only and importable here. The model
adapter and leaderboard apparatus arrive in later phases (see docs/ROADMAP.md).
"""

from __future__ import annotations

from .adapter import ModelResponse, register_adapter, resolve_model
from .config import TIERS
from .engine import KitchenRushEngine
from .procgen import KitchenSpec, generate
from .runner import run_episode
from .tools import TOOL_SCHEMAS, ToolCall
from .version import __version__

__all__ = [
    "__version__",
    "TIERS",
    "KitchenRushEngine",
    "KitchenSpec",
    "generate",
    "run_episode",
    "TOOL_SCHEMAS",
    "ToolCall",
    "register_adapter",
    "resolve_model",
    "ModelResponse",
]
