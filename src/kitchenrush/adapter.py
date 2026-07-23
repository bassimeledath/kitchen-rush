"""Model adapters (Phase 2).

A single LiteLLM-backed ``ModelClient`` covers OpenAI / Anthropic / Gemini / vLLM /
Nemotron via native function calling, and measures wall-clock latency. The contract is
deliberately tiny: messages + tool schemas in -> tool calls + text + latency + usage out.
All game logic stays in the engine. (Requires the ``providers`` extra: ``pip install
'kitchenrush[providers]'`` and provider API keys in the environment.)
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol, runtime_checkable

from .tools import ToolCall


class ModelResponse:
    def __init__(self, tool_calls: list[ToolCall], text: str = "",
                 latency_s: float = 0.0, usage: dict | None = None) -> None:
        self.tool_calls = tool_calls
        self.text = text
        self.latency_s = latency_s
        self.usage = usage or {}


@runtime_checkable
class ModelClient(Protocol):
    """messages + tool schemas in -> ModelResponse out."""

    name: str

    def generate(self, *, system: str, messages: list[dict], tools: list[dict],
                 **kwargs: Any) -> ModelResponse: ...


_NO_TEMPERATURE: set[str] = set()   # models that reject the temperature param (learned at runtime)

# Hard per-call output ceiling: bounds worst-case cost per call and stops OpenRouter from reserving
# the endpoint's full context window (a 402 source). Far above any real Kitchen Rush turn
# (heaviest observed reasoning burst ~8k tokens/turn), so it never truncates normal behaviour.
MAX_OUTPUT_TOKENS = 16000


class LiteLLMClient:
    """Multi-provider adapter. ``spec`` is ``provider:model`` (e.g. ``openai:gpt-4.1``);
    it is translated to LiteLLM's ``provider/model`` form."""

    def __init__(self, spec: str, **kwargs: Any) -> None:
        self.name = spec
        self.litellm_model = spec.replace(":", "/", 1) if ":" in spec else spec
        self.extra = kwargs

    def generate(self, *, system: str, messages: list[dict], tools: list[dict],
                 temperature: float | None = 0.2, timeout: float = 90.0, num_retries: int = 2,
                 tool_choice: str = "required", max_tokens: int = MAX_OUTPUT_TOKENS,
                 **kwargs: Any) -> ModelResponse:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "litellm is required for model runs: pip install 'kitchenrush[providers]'"
            ) from exc

        litellm.drop_params = True   # silently ignore params a given provider doesn't support
        full_messages = [{"role": "system", "content": system}, *messages]
        params = dict(
            model=self.litellm_model,
            messages=full_messages,
            tools=tools,
            tool_choice=tool_choice,   # "required" -> tool call(s) only, no prose (faster)
            timeout=timeout,
            num_retries=num_retries,
            max_tokens=max_tokens,     # hard per-call output ceiling (cost + 402 guard)
            **{**self.extra, **kwargs},
        )
        # Newer reasoning models (e.g. Opus 4.7) deprecate `temperature`; litellm's registry may
        # be too old to drop it. Learn once per model and stop sending it thereafter.
        if temperature is not None and self.litellm_model not in _NO_TEMPERATURE:
            params["temperature"] = temperature
        start = time.perf_counter()
        # timeout + retries guard against transient network blips (infra, not model speed).
        try:
            resp = litellm.completion(**params)
        except Exception as exc:  # noqa: BLE001
            if "temperature" in params and "temperature" in str(exc).lower():
                _NO_TEMPERATURE.add(self.litellm_model)
                params.pop("temperature", None)
                resp = litellm.completion(**params)
            else:
                raise
        latency_s = time.perf_counter() - start

        choice = resp.choices[0].message
        tool_calls: list[ToolCall] = []
        for tc in (getattr(choice, "tool_calls", None) or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(ToolCall(tc.function.name, args, id=getattr(tc, "id", None)))

        usage_obj = getattr(resp, "usage", None)
        reasoning = _reasoning_tokens(usage_obj)   # None iff the provider did not report the field
        # Encrypted/adaptive thinking (e.g. claude-sonnet-5): a signed thinking block is returned but
        # the reasoning-token count is 0/absent, so the thinking is billed only inside
        # completion_tokens. Flag it so the RP clock can charge the true output (RULES §3.2.1).
        thinking_blocks = getattr(choice, "thinking_blocks", None)
        # Actual billed cost: OpenRouter reports usage.cost; litellm often computes response_cost in
        # _hidden_params. Prefer this over local price tables so a pinned provider's real price (and
        # the spend cap) are exact, not estimated. None -> caller falls back to a price estimate.
        hidden = getattr(resp, "_hidden_params", None) or {}
        cost = hidden.get("response_cost")
        if cost is None:
            cost = getattr(usage_obj, "cost", None)
            if cost is None:
                details = getattr(usage_obj, "cost_details", None)
                cost = getattr(details, "upstream_inference_cost", None) if details else None
        usage = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            # reasoning_tokens is the count used for RP latency (0 when unreported, see latency math);
            # reasoning_reported distinguishes "provider said 0" from "provider didn't report it" so
            # the provider-trusted gap is auditable (RULES §3.2.1, METHODOLOGY §3.1).
            "reasoning_tokens": reasoning or 0,
            "reasoning_reported": reasoning is not None,
            "has_hidden_thinking": bool(thinking_blocks) and not (reasoning or 0),
            "cost": float(cost) if cost is not None else None,   # actual billed USD, if the provider reports it
            # Provider that actually served this generation (for pin verification / reroute detection).
            "provider_served": (getattr(resp, "provider", None)
                                or hidden.get("provider") or hidden.get("custom_llm_provider")),
        }
        return ModelResponse(tool_calls, getattr(choice, "content", "") or "", latency_s, usage)


def _reasoning_tokens(usage_obj: Any) -> int | None:
    """The provider's reported reasoning-token count, or None if the field is absent entirely.

    Returning None for genuine absence (rather than collapsing it to 0) lets callers record
    whether a thinking model actually disclosed its hidden reasoning cost.
    """
    details = getattr(usage_obj, "completion_tokens_details", None)
    if details is None:
        return None
    val = getattr(details, "reasoning_tokens", None)
    return None if val is None else (val or 0)


ADAPTER_REGISTRY: dict[str, Callable[..., ModelClient]] = {"litellm": LiteLLMClient}


def register_adapter(provider: str, factory: Callable[..., ModelClient]) -> None:
    ADAPTER_REGISTRY[provider] = factory


def resolve_model(spec: str, **kwargs: Any) -> ModelClient:
    """Resolve ``provider:model`` to a client. Custom providers registered via
    ``register_adapter`` win; everything else flows through LiteLLM."""
    provider, _, model = spec.partition(":")
    if provider in ADAPTER_REGISTRY and provider != "litellm":
        return ADAPTER_REGISTRY[provider](model or provider, **kwargs)
    return LiteLLMClient(spec, **kwargs)
