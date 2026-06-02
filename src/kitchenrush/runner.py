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


def s_ref_for(spec: KitchenSpec) -> float:
    """Instant-serve upper bound: every order at full value, no decay (placeholder oracle)."""
    return sum(scoring.base_value(o.dish) for o in spec.orders)


def run_episode(spec: KitchenSpec, policy: Policy, *, max_turns: int | None = None,
                record_steps: bool = True) -> EpisodeResult:
    engine = KitchenRushEngine(spec)
    max_turns = max_turns or config.MAX_TURNS
    obs = engine.observe()
    steps: list[dict] = []

    while not engine.terminated and engine.turn_count < max_turns:
        calls, latency_s = policy(obs, TOOL_SCHEMAS)
        think_gs = config.LATENCY_SCALE * float(latency_s)
        obs = engine.step(calls, think_gs)
        if record_steps:
            steps.append({
                "turn": engine.turn_count,
                "calls": [{"name": c.name, "arguments": c.arguments} for c in calls],
                "latency_s": round(float(latency_s), 4),
                "think_gs": round(think_gs, 4),
                "clock_gs": obs["clock_gs"],
                "score": obs["score"],
                "last_turn": obs["last_turn"],
            })

    return EpisodeResult(seed=spec.seed, tier=spec.tier, report=engine.final_report(), steps=steps)


def run_suite(seeds: Iterable[int], tier: str, policy_factory: PolicyFactory, *,
              trials: int = 1, max_turns: int | None = None) -> list[EpisodeResult]:
    """Run ``trials`` episodes per seed; a fresh policy per episode (for sampling variety)."""
    episodes: list[EpisodeResult] = []
    for seed in seeds:
        spec = generate(seed, tier)
        s_ref = s_ref_for(spec)
        for trial in range(trials):
            policy = policy_factory(seed, trial)
            result = run_episode(spec, policy, max_turns=max_turns)
            result.s_ref = s_ref
            result.trial = trial
            episodes.append(result)
    return episodes
