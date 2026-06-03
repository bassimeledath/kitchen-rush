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
