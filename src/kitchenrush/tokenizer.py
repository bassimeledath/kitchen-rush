"""Pinned, deterministic token counter for the RP latency track (METHODOLOGY §latency).

RP latency must be reproducible and provider-neutral, so we apply ONE fixed tokenizer to every
model's text (the same principle as cross-model token units on speed leaderboards, and as the
hardware-independent token-based latency metrics used in simultaneous translation). The pin is
`cl100k_base` (via tiktoken, which rides in with the ``providers`` extra); if tiktoken is
unavailable (stdlib-only core, no model runs) it falls back to a char/4 heuristic. The active
choice is stamped as ``TOKENIZER_ID`` in every output artifact so results are never silently
compared across tokenizers.
"""

from __future__ import annotations

import math

try:  # pinned real tokenizer for official (provider) runs
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
    TOKENIZER_ID = "tiktoken-cl100k_base-v1"

    def _encode_len(text: str) -> int:
        return len(_ENC.encode(text))

except Exception:  # noqa: BLE001 - tiktoken absent / vocab unavailable -> rough deterministic proxy
    TOKENIZER_ID = "char4-v0"

    def _encode_len(text: str) -> int:
        return max(1, math.ceil(len(text) / 4))


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return _encode_len(text)
