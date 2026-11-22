"""Clean AgentAdapter interface for Kitchen Rush v3.

Supports:
1. OpenRouterToolAdapter: native batched function calling (1..8 calls/turn) with cache-aligned
   static system prefix and compact dynamic user state.
2. DecisionModelAdapter: clean abstract base class for 1-step decision models (e.g. TypeSafe/Jev)
   that emit a single ToolCall per turn against the exact same engine and metrics.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools import ToolCall
from .env import MAX_BATCH_CALLS, RULES_TEXT, TOOL_SCHEMAS


DEFAULT_MAX_TOKENS = 16384

INSPECT_HISTORY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "inspect_history",
        "description": (
            "Zero-game-time read-only lookup: return the last N turns of your own turn history (1 to 50) "
            "in the next observation. Does not advance game time or consume a kitchen action."
        ),
        "parameters": {
            "type": "object",
            "properties": {"turns": {"type": "integer", "minimum": 1, "maximum": 50}},
            "required": ["turns"],
            "additionalProperties": False,
        },
    },
}


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, uncertain_billing: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.uncertain_billing = uncertain_billing


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_id: str
    tier: str  # "small" | "medium" | "frontier"
    input_price_per_m: float
    output_price_per_m: float
    max_tokens: int = DEFAULT_MAX_TOKENS
    reasoning: dict[str, Any] | None = None
    temperature: float | None = 0.0
    route_order: tuple[str, ...] | None = None


@dataclass
class TurnDecision:
    calls: list[ToolCall]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    cost_source: str = "provider"
    latency_s: float = 0.0
    finish_reason: str | None = None
    provider_route: str | None = None
    requested_effort: str | None = None
    max_tokens: int | None = None
    raw_content: str | None = None
    parse_errors: list[str] = field(default_factory=list)


class AgentAdapter(ABC):
    """Minimal unified interface for any model/policy evaluated on Kitchen Rush v3."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        return {}

    def max_turn_reserve_usd(self) -> float:
        return 0.0

    @abstractmethod
    def act(
        self,
        static_ctx: dict[str, Any],
        dyn_state: dict[str, Any],
        *,
        chat_messages: list[dict[str, Any]] | None = None,
        allow_inspect_history: bool = False,
    ) -> TurnDecision:
        ...


class DecisionModelAdapter(AgentAdapter, ABC):
    """Base adapter for single-step decision models (e.g. TypeSafe / Jev).

    Subclasses implement `choose_one(static_ctx, dyn_state)` returning one ToolCall;
    this adapter wraps it as a length-1 batch so decision models share the exact same
    engine, seed banks, replay verifier, and 4-metric reporting pipeline.
    """

    @abstractmethod
    def choose_one(self, static_ctx: dict[str, Any], dyn_state: dict[str, Any]) -> TurnDecision:
        ...

    def act(
        self,
        static_ctx: dict[str, Any],
        dyn_state: dict[str, Any],
        *,
        chat_messages: list[dict[str, Any]] | None = None,
        allow_inspect_history: bool = False,
    ) -> TurnDecision:
        decision = self.choose_one(static_ctx, dyn_state)
        if len(decision.calls) > 1:
            decision.calls = decision.calls[:1]
        return decision


def load_openrouter_key(env_paths: tuple[Path, ...] | None = None) -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    candidates = env_paths or (
        Path(".env"),
        Path(__file__).resolve().parents[3] / ".env",
        Path(__file__).resolve().parents[5] / ".env",
    )
    for p in candidates:
        if p.exists():
            for line in p.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    raise RuntimeError("OPENROUTER_API_KEY not found in environment or .env")


def _build_system_prompt(static_ctx: dict[str, Any], *, allow_inspect_history: bool = False) -> str:
    extra = (
        "\nYou also have an optional zero-game-time `inspect_history(turns)` tool if you ever want to review "
        "earlier turns beyond `last_turn`; it does not advance the kitchen clock or count as a kitchen action."
        if allow_inspect_history
        else ""
    )
    return (
        f"{RULES_TEXT}{extra}\n\n"
        "Respond by calling 1 to 8 kitchen tools in sequential execution order for this turn. "
        "Queue multiple deterministic steps (e.g., collect -> chop -> cook, or collect_cooked -> plate -> serve) "
        "whenever safe to save turns and tokens; stop your batch before an uncertain event if needed.\n\n"
        f"KITCHEN_SPEC_JSON:\n{json.dumps(static_ctx, separators=(',', ':'), sort_keys=True)}"
    )


def _parse_tool_calls(message: dict[str, Any]) -> tuple[list[ToolCall], list[str]]:
    raw_calls = message.get("tool_calls") or []
    parsed: list[ToolCall] = []
    errors: list[str] = []
    for tc in raw_calls[:MAX_BATCH_CALLS]:
        fn = tc.get("function") or {}
        name = fn.get("name")
        raw_args = fn.get("arguments", "{}")
        if not isinstance(name, str) or not name:
            errors.append("missing tool function name")
            continue
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
                if not isinstance(args, dict):
                    errors.append(f"non-dict arguments for {name}")
                    args = {}
            except json.JSONDecodeError as exc:
                errors.append(f"bad JSON args for {name}: {exc}")
                args = {}
        else:
            errors.append(f"invalid args type for {name}")
            args = {}
        parsed.append(ToolCall(name=name, arguments=args, id=tc.get("id")))
    return parsed, errors


class OpenRouterToolAdapter(AgentAdapter):
    """Native batched tool-calling adapter over OpenRouter Chat Completions API."""

    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, spec: ModelSpec, api_key: str | None = None, timeout: int = 120):
        self.spec = spec
        self.api_key = api_key or load_openrouter_key()
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self.spec.key

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.spec.model_id,
            "tier": self.spec.tier,
            "requested_effort": (self.spec.reasoning or {}).get("effort"),
            "max_tokens": self.spec.max_tokens,
        }

    def max_turn_reserve_usd(self) -> float:
        # Worst-case reservation for one turn: 6k prompt tokens + full max_tokens output.
        return (6000 * self.spec.input_price_per_m + self.spec.max_tokens * self.spec.output_price_per_m) / 1e6

    def _build_payload(
        self,
        static_ctx: dict[str, Any],
        dyn_state: dict[str, Any],
        *,
        chat_messages: list[dict[str, Any]] | None = None,
        allow_inspect_history: bool = False,
    ) -> dict[str, Any]:
        sys_text = _build_system_prompt(static_ctx, allow_inspect_history=allow_inspect_history)
        is_anthropic = self.spec.model_id.startswith("anthropic/")
        if is_anthropic:
            sys_content: Any = [
                {"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            sys_content = sys_text

        user_text = f"STATE_JSON:\n{json.dumps(dyn_state, separators=(',', ':'))}"
        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_content}]
        if chat_messages:
            last_user_idx = max((i for i, m in enumerate(chat_messages) if m.get("role") == "user"), default=-1)
            for idx, m in enumerate(chat_messages):
                if is_anthropic and idx == last_user_idx and isinstance(m.get("content"), str):
                    messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": m["content"], "cache_control": {"type": "ephemeral"}}],
                    })
                else:
                    messages.append(m)
        messages.append({"role": "user", "content": user_text})

        tools = list(TOOL_SCHEMAS) + ([INSPECT_HISTORY_TOOL] if allow_inspect_history else [])
        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": self.spec.max_tokens,
            "usage": {"include": True},
        }
        if self.spec.temperature is not None and not self.spec.reasoning:
            payload["temperature"] = self.spec.temperature
        if self.spec.reasoning is not None:
            payload["reasoning"] = self.spec.reasoning
        if self.spec.route_order:
            payload["provider"] = {
                "order": list(self.spec.route_order),
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        return payload

    def act(
        self,
        static_ctx: dict[str, Any],
        dyn_state: dict[str, Any],
        *,
        chat_messages: list[dict[str, Any]] | None = None,
        allow_inspect_history: bool = False,
    ) -> TurnDecision:
        payload = self._build_payload(
            static_ctx,
            dyn_state,
            chat_messages=chat_messages,
            allow_inspect_history=allow_inspect_history,
        )
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/bassimeledath/kitchen-rush",
            "X-Title": "Kitchen Rush v3 Calibration",
        }

        last_err: Exception | None = None
        for attempt in range(3):
            t0 = time.monotonic()
            req = urllib.request.Request(self.ENDPOINT, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                latency = time.monotonic() - t0
                break
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")[:400]
                if exc.code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    last_err = ProviderError(f"HTTP {exc.code}: {err_body}", exc.code)
                    continue
                raise ProviderError(f"HTTP {exc.code}: {err_body}", exc.code) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    last_err = ProviderError(f"Transport error: {exc}", uncertain_billing=True)
                    continue
                raise ProviderError(f"Transport error after retries: {exc}", uncertain_billing=True) from exc
        else:
            raise last_err or ProviderError("Unknown provider failure")

        if "error" in raw:
            err = raw["error"]
            raise ProviderError(f"Provider JSON error: {err}", err.get("code") if isinstance(err, dict) else None)

        choices = raw.get("choices") or []
        if not choices:
            raise ProviderError(f"Empty choices in response: {str(raw)[:300]}")
        choice = choices[0]
        msg = choice.get("message") or {}
        calls, parse_errors = _parse_tool_calls(msg)

        usage = raw.get("usage") or {}
        p_tok = int(usage.get("prompt_tokens") or 0)
        c_tok = int(usage.get("completion_tokens") or 0)
        p_det = usage.get("prompt_tokens_details") or {}
        c_det = usage.get("completion_tokens_details") or {}
        cached_tok = int(p_det.get("cached_tokens") or 0)
        reason_tok = int(c_det.get("reasoning_tokens") or 0)

        raw_cost = usage.get("cost")
        if raw_cost is not None:
            cost_usd = float(raw_cost)
            cost_source = "provider"
        else:
            cost_usd = (p_tok * self.spec.input_price_per_m + c_tok * self.spec.output_price_per_m) / 1e6
            cost_source = "estimated"

        return TurnDecision(
            calls=calls,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            reasoning_tokens=reason_tok,
            cached_tokens=cached_tok,
            cost_usd=cost_usd,
            cost_source=cost_source,
            latency_s=round(latency, 3),
            finish_reason=choice.get("finish_reason"),
            provider_route=raw.get("provider"),
            requested_effort=(self.spec.reasoning or {}).get("effort"),
            max_tokens=self.spec.max_tokens,
            raw_content=msg.get("content"),
            parse_errors=parse_errors,
        )
