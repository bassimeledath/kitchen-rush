"""Model adapter interface (Phase 2 stub).

Phase 1 ships only baseline policies (no network). Phase 2 implements a single
LiteLLM-based ``ModelClient`` that turns a Kitchen Rush observation + tool schemas into
native function calls and measures wall-clock latency. The protocol below is the intended
contract (see docs/INTERFACE.md); the concrete client raises until implemented.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from .tools import ToolCall


@runtime_checkable
class ModelClient(Protocol):
    """messages + tool schemas in -> tool calls + text + latency out."""

    name: str

    def generate(self, *, system: str, messages: list[dict], tools: list[dict],
                 **kwargs: Any) -> "ModelResponse": ...


class ModelResponse:
    def __init__(self, tool_calls: list[ToolCall], text: str = "",
                 latency_s: float = 0.0, usage: dict | None = None) -> None:
        self.tool_calls = tool_calls
        self.text = text
        self.latency_s = latency_s
        self.usage = usage or {}


class LiteLLMClient:
    """Single multi-provider adapter (OpenAI / Anthropic / Gemini / vLLM / Nemotron)."""

    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model = model
        self.name = model

    def generate(self, *, system: str, messages: list[dict], tools: list[dict],
                 **kwargs: Any) -> ModelResponse:
        raise NotImplementedError("LiteLLMClient is implemented in Phase 2 (see docs/ROADMAP.md)")


ADAPTER_REGISTRY: dict[str, Callable[..., ModelClient]] = {"litellm": LiteLLMClient}


def register_adapter(provider: str, factory: Callable[..., ModelClient]) -> None:
    ADAPTER_REGISTRY[provider] = factory


def resolve_model(spec: str, **kwargs: Any) -> ModelClient:
    """Resolve ``provider:model`` (e.g. ``openai:gpt-4.1``) to a client instance."""
    provider, _, model = spec.partition(":")
    if provider not in ADAPTER_REGISTRY:
        # default everything through LiteLLM in Phase 2
        return LiteLLMClient(spec, **kwargs)
    return ADAPTER_REGISTRY[provider](model or provider, **kwargs)
