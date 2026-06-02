"""Pinned, deterministic token counter for the RP latency track (SCORING §1.2).

Placeholder calibration (open question #3): a char/4 heuristic — fully deterministic and
provider-independent, so RP scores are recomputable from a trajectory log. Swap for a real
pinned tokenizer (e.g. a cl100k_base-class BPE) later without changing this interface.
"""

from __future__ import annotations

import math

TOKENIZER_ID = "char4-v0"


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
