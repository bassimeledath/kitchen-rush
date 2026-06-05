"""Result container + JSON/JSONL writers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EpisodeResult:
    seed: int
    tier: str
    report: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)  # per-action replay frames (opt-in)
    s_ref: float | None = None     # greedy-EDF reference ceiling (zero latency), set by run_suite
    s_null: float | None = None    # do-nothing floor (all orders expire), set by run_suite
    trial: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "tier": self.tier,
            "trial": self.trial,
            "s_ref": self.s_ref,
            "s_null": self.s_null,
            "report": self.report,
            "steps": self.steps,
        }


def write_json(result: EpisodeResult, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result.to_dict(), indent=2) + "\n")


def write_jsonl(result: EpisodeResult, path: str | Path) -> None:
    """One JSON object per line: a meta header, then one line per turn, then the report."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as fh:
        fh.write(json.dumps({"kind": "meta", "seed": result.seed, "tier": result.tier,
                             "trial": result.trial, "s_ref": result.s_ref}) + "\n")
        for step in result.steps:
            fh.write(json.dumps({"kind": "step", **step}) + "\n")
        fh.write(json.dumps({"kind": "report", **result.report}) + "\n")


def build_replay(result: EpisodeResult, spec: Any) -> dict[str, Any]:
    """Assemble a single self-contained JSON document the replay UI can render with zero game
    logic: a static ``layout`` (grid + station cells), a ``catalog`` (which ingredient states /
    dishes exist, so the UI knows which sprites to show), the per-action ``frames`` timeline, and
    the final ``report``. Requires ``result.trace`` (run the episode with ``record_trace=True``)."""
    from . import config, version

    ingredients: dict[str, Any] = {}
    for name, ing in config.INGREDIENTS.items():
        states = [config.RAW]
        if ing.choppable:
            states.append(config.CHOPPED)
        if ing.cookable_from is not None:
            states += [config.COOKED, config.BURNED]
        ingredients[name] = {
            "states": states, "choppable": ing.choppable,
            "cookable_from": ing.cookable_from,
            "cook_time": ing.cook_time, "burn_window": ing.burn_window,
        }

    layout = {
        "grid_n": spec.grid_n,
        "horizon_gs": spec.horizon_gs,
        "burner_count": spec.burner_count,
        "chef_start": list(spec.chef_start),
        "latency_budget": spec.latency_budget,
        "show_ready_actions": spec.show_ready_actions,
        "stations": [
            {"type": s.type, "ingredient": s.ingredient, "cell": list(s.cell)}
            for s in spec.stations
        ],
        "blocked": [list(c) for c in sorted(spec.blocked)],   # non-walkable counter/wall cells
        "door": list(spec.door) if spec.door else None,
    }
    catalog = {
        "ingredients": ingredients,
        "recipes": {r: dict(config.RECIPES[r]) for r in spec.active_recipes},
        "station_types": {
            config.ING: "dispenser", config.BOARD: "cutting board", config.STOVE: "stove",
            config.PLATE: "plating counter", config.PASS: "pass", config.BIN: "bin",
        },
        "states": {"RAW": config.RAW, "CHOPPED": config.CHOPPED,
                   "COOKED": config.COOKED, "BURNED": config.BURNED, "PLATE": "PLATE"},
    }
    return {
        "meta": {
            "generator": "kitchenrush", "version": version.__version__,
            "versions": version.versions(),
            "seed": result.seed, "tier": result.tier, "trial": result.trial,
            "s_ref": result.s_ref, "s_null": result.s_null,
            "score_raw": result.report.get("score_raw"),
            "score_display": result.report.get("score_display"),
        },
        "layout": layout,
        "catalog": catalog,
        "frames": result.trace,
        "report": result.report,
    }


def write_replay(result: EpisodeResult, spec: Any, path: str | Path) -> None:
    """Write the self-contained replay document (see ``build_replay``) as compact JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(build_replay(result, spec), separators=(",", ":")) + "\n")
