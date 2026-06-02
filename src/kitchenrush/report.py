"""Lean result container + JSON writer. (Full JSONL trajectory schema is Phase 2.)"""

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

    def to_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "tier": self.tier, "report": self.report, "steps": self.steps}


def write_json(result: EpisodeResult, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
