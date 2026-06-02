"""Kitchen Rush — a benchmark for fast AND accurate native tool calling.

Latency costs points by construction: a model's per-response thinking time is converted to
game-seconds that advance a shared world clock *before* each action resolves, so while the
model deliberates, food burns and orders expire.

This is a pre-alpha scaffold. The public API below is the intended surface; submodules are
stubs pending implementation (see docs/ROADMAP.md). Imports are kept lazy/minimal so the
package remains importable while modules are filled in.
"""

from __future__ import annotations

from .version import __version__

__all__ = ["__version__"]

# Intended public API (uncomment as the modules are implemented):
# from .adapters.registry import register_adapter, resolve_model
# from .engine.engine import KitchenRushEngine
# from .procgen.generator import generate_spec
# __all__ += ["register_adapter", "resolve_model", "KitchenRushEngine", "generate_spec"]
