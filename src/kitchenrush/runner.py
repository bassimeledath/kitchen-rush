"""In-process episode runner (Phase 1).

A ``policy`` is any callable ``(observation: dict, tools: list[dict]) -> (calls, latency_s)``
where ``calls`` is a list of ``ToolCall`` and ``latency_s`` is the response latency in
seconds (converted to game-time via ``LATENCY_SCALE``). Baselines inject a fixed latency;
the Phase-2 model adapter will measure real wall-clock latency. No network is required.
"""

from __future__ import annotations

from typing import Callable, Iterable

from . import config, scoring
from .engine import KitchenRushEngine
from .procgen import KitchenSpec, generate
from .report import EpisodeResult
from .tools import TOOL_SCHEMAS, ToolCall

Policy = Callable[[dict, list[dict]], tuple[list[ToolCall], float]]
PolicyFactory = Callable[[int, int], Policy]


def anchors_for(spec: KitchenSpec) -> tuple[float, float]:
    """(S_null, S_ref) for an instance: the do-nothing floor and the greedy-EDF ceiling
    (METHODOLOGY §1). S_ref runs the oracle at zero latency."""
    from .oracle import null_score, reference_score  # lazy import to avoid a cycle
    return null_score(spec), reference_score(spec, 0.0)


def run_episode(spec: KitchenSpec, policy: Policy, *, max_turns: int | None = None,
                record_steps: bool = True, record_trace: bool = False,
                warmup: bool = True) -> EpisodeResult:
    engine = KitchenRushEngine(spec, record_trace=record_trace)
    max_turns = max_turns or config.MAX_TURNS
    # Warm up the model before scoring so a one-time cold-start never pollutes RT latency. Disabled
    # for calibration/board runs (warmup=False) — a per-episode warmup would be a paid call each
    # episode and would pollute token/cost tallies (calibrated clock is frozen anyway).
    if warmup and hasattr(policy, "warmup"):
        policy.warmup(TOOL_SCHEMAS)
    obs = engine.observe()
    steps: list[dict] = []

    while not engine.terminated and engine.turn_count < max_turns:
        calls, latency_s = policy(obs, TOOL_SCHEMAS)
        think_gs = config.LATENCY_SCALE * float(latency_s)
        obs = engine.step(calls, think_gs)
        if record_steps:
            step = {
                "turn": engine.turn_count,
                "calls": [{"name": c.name, "arguments": c.arguments} for c in calls],
                "latency_s": round(float(latency_s), 4),
                "think_gs": round(think_gs, 4),
                "clock_gs": obs["clock_gs"],
                "score": obs["score"],
                "last_turn": obs["last_turn"],
            }
            # Surface the provider-trusted reasoning-token gap for audit (RULES §3.2.1): record
            # whether the model actually reported a reasoning-token count this turn, and the count
            # that entered the RP latency math. Only model-backed policies expose these.
            reported = getattr(policy, "last_reasoning_reported", None)
            if reported is not None:
                step["reasoning_reported"] = reported
                step["reasoning_tokens"] = getattr(policy, "last_reasoning_tokens", None)
            # Per-turn pinned token counts + measured wall-clock (for speed calibration and the
            # calibrated-vs-live drift check); only model-backed policies expose these.
            n_in = getattr(policy, "last_n_in", None)
            if n_in is not None:
                step["n_in"] = n_in
                step["n_out"] = getattr(policy, "last_n_out", None)
                step["live_latency_s"] = getattr(policy, "last_live_latency_s", None)
            steps.append(step)

    report = engine.final_report()
    engine.emit_end_frame()   # capture end-of-game force-expiries + final score (no-op if not tracing)
    return EpisodeResult(seed=spec.seed, tier=spec.tier, report=report, steps=steps,
                         trace=engine.trace)


def run_suite(seeds: Iterable[int], tier: str, policy_factory: PolicyFactory, *,
              trials: int = 1, max_turns: int | None = None) -> list[EpisodeResult]:
    """Run ``trials`` episodes per seed; a fresh policy per episode (for sampling variety)."""
    episodes: list[EpisodeResult] = []
    for seed in seeds:
        spec = generate(seed, tier)
        s_null, s_ref = anchors_for(spec)
        for trial in range(trials):
            policy = policy_factory(seed, trial)
            result = run_episode(spec, policy, max_turns=max_turns)
            result.s_ref = s_ref
            result.s_null = s_null
            result.trial = trial
            episodes.append(result)
    return episodes
