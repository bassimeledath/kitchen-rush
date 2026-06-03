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


class LiteLLMClient:
    """Multi-provider adapter. ``spec`` is ``provider:model`` (e.g. ``openai:gpt-4.1``);
    it is translated to LiteLLM's ``provider/model`` form."""

    def __init__(self, spec: str, **kwargs: Any) -> None:
        self.name = spec
        self.litellm_model = spec.replace(":", "/", 1) if ":" in spec else spec
        self.extra = kwargs

    def generate(self, *, system: str, messages: list[dict], tools: list[dict],
                 temperature: float = 0.2, timeout: float = 90.0, num_retries: int = 2,
                 tool_choice: str = "required", **kwargs: Any) -> ModelResponse:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "litellm is required for model runs: pip install 'kitchenrush[providers]'"
            ) from exc

        full_messages = [{"role": "system", "content": system}, *messages]
        start = time.perf_counter()
        # timeout + retries guard against transient network blips (infra, not model speed).
        resp = litellm.completion(
            model=self.litellm_model,
            messages=full_messages,
            tools=tools,
            tool_choice=tool_choice,   # "required" -> tool call(s) only, no prose (faster)
            temperature=temperature,
            timeout=timeout,
            num_retries=num_retries,
            **{**self.extra, **kwargs},
        )
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
        usage = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            "reasoning_tokens": _reasoning_tokens(usage_obj),
        }
        return ModelResponse(tool_calls, getattr(choice, "content", "") or "", latency_s, usage)


def _reasoning_tokens(usage_obj: Any) -> int:
    details = getattr(usage_obj, "completion_tokens_details", None)
    if details is None:
        return 0
    return getattr(details, "reasoning_tokens", 0) or 0


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
