"""Latency tracks (SCORING §1.2).

Both tracks feed the same ``think_gs = LATENCY_SCALE * latency_seconds`` clock rule
(RULES §3.2.2). They differ only in how ``latency_seconds`` is obtained:

- RT (real, diagnostic): measured wall-clock around the model call.
- RP (reproducible, ranked): a deterministic token proxy, recomputable from logs.
"""

from __future__ import annotations

from . import config


def rp_latency_seconds(n_in: int, n_out: int) -> float:
    """RP token-proxy latency. ``n_out`` MUST include reasoning/thinking tokens."""
    return config.RP_BETA0 + config.RP_BETA_IN * n_in + config.RP_BETA_OUT * n_out
