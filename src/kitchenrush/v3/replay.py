"""Export Kitchen Rush v3 episodes to the self-contained pixel-art UI replay format (`ui/`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config
from ..report import EpisodeResult, build_replay
from ..tools import ToolCall
from .env import UnifiedEngine, UnifiedKitchen


def build_v3_replay(episode: dict[str, Any], spec: UnifiedKitchen) -> dict[str, Any]:
    """Deterministically replay a v3 episode and return a `ui/`-compatible replay document."""
    engine = UnifiedEngine(spec, record_trace=True, early_stop=True)
    for batch in episode.get("batches", []):
        calls = [ToolCall(name=c["name"], arguments=dict(c.get("arguments") or {})) for c in batch]
        engine.step_batch(calls)
        if engine.terminated:
            break
    final = engine.final_report()
    engine.emit_end_frame()

    res = EpisodeResult(
        seed=spec.seed,
        tier=f"D{spec.difficulty}",
        report=final,
        trace=engine.trace,
    )
    doc = build_replay(res, spec)

    # Override catalog ingredient rules with this kitchen's procedural cook/burn parameters.
    for name, rule in engine.ingredient_rules.items():
        states = [config.RAW]
        if rule.choppable:
            states.append(config.CHOPPED)
        if rule.cookable_from is not None:
            states += [config.COOKED, config.BURNED]
        doc["catalog"]["ingredients"][name] = {
            "states": states,
            "choppable": rule.choppable,
            "cookable_from": rule.cookable_from,
            "cook_time": rule.cook_time,
            "burn_window": rule.burn_window,
        }

    doc["meta"]["protocol"] = "kitchen-rush-v3"
    doc["meta"]["model"] = episode.get("model")
    doc["meta"]["difficulty"] = spec.difficulty
    doc["meta"]["served"] = episode.get("served", engine.counters["orders_served"])
    doc["meta"]["requested"] = episode.get("requested", len(spec.orders))
    doc["meta"]["clear"] = bool(episode.get("clear"))
    doc["meta"]["output_tokens"] = episode.get("output_tokens", 0)
    doc["meta"]["cost_usd"] = episode.get("cost_usd", 0.0)
    doc["meta"]["turns"] = episode.get("turns", engine.turn_count)
    doc["meta"]["actions"] = episode.get("actions", engine._action_count)
    return doc


def export_v3_replay(episode_path: Path, spec: UnifiedKitchen, out_path: Path) -> Path:
    episode = json.loads(episode_path.read_text(encoding="utf-8"))
    doc = build_v3_replay(episode, spec)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    return out_path
