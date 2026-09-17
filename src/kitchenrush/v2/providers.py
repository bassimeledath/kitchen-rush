"""Thin native API wrappers. Transport failures never become scored model failures."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class ProviderError(RuntimeError):
    pass


def load_env(path):
    # Explicit local file only. Never print values or persist credentials in run records.
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


@dataclass(frozen=True)
class Model:
    key: str
    model: str
    provider: str
    input_price: float
    output_price: float
    reasoning: dict | None = None
    max_output: int = 2048


class Client:
    def __init__(self, model: Model, timeout=240):
        self.model = model
        self.timeout = timeout

    def payload(self, canonical, max_output=None):
        if self.model.provider == "TypeSafe":
            return {"model": self.model.model, **canonical}
        payload = {
            "model": self.model.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Select exactly one next kitchen action. "
                    'Return only JSON {"action":"exact candidate key"}. '
                    "Do not output probability distributions or unused alternatives.",
                },
                {"role": "user", "content": json.dumps(canonical, separators=(",", ":"))},
            ],
            "max_tokens": max_output if max_output is not None else self.model.max_output,
            "provider": {
                "order": [self.model.provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "next_action",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": list(canonical["questions"]["next_action"]["criteria"]),
                            }
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        if self.model.reasoning is not None:
            payload["reasoning"] = self.model.reasoning
        return payload

    def bound(self, payload):
        # Conservative byte bound + provider template overhead. Assumption is validated on
        # each response. One flat Jev question avoids speculative state replication.
        prompt_bound = len(json.dumps(payload, ensure_ascii=True).encode()) + 4096
        output_bound = 0 if self.model.provider == "TypeSafe" else payload["max_tokens"]
        return (
            prompt_bound,
            prompt_bound * self.model.input_price + output_bound * self.model.output_price,
        )

    def prepare(self, canonical, remaining_usd, remaining_tokens=None):
        maximum = self.model.max_output
        if remaining_tokens is not None:
            if self.model.provider == "TypeSafe":
                raise ValueError("Jev is not eligible for the generation-token view")
            maximum = min(maximum, remaining_tokens)
        payload = self.payload(canonical, maximum)
        _, bound = self.bound(payload)
        if bound > remaining_usd and self.model.provider != "TypeSafe":
            # Reserve the entire prompt upper bound, then choose the largest affordable
            # output cap. Never spend a token/dollar allowance on an unbounded request.
            input_bound, _ = self.bound(payload)
            maximum = min(
                maximum,
                math.floor(
                    (remaining_usd - input_bound * self.model.input_price) / self.model.output_price
                ),
            )
            if maximum < 16:
                return None
            payload = self.payload(canonical, maximum)
            _, bound = self.bound(payload)
        if maximum <= 0 or bound > remaining_usd:
            return None
        return payload, bound

    def evaluate(self, payload):
        jev = self.model.provider == "TypeSafe"
        env_name = "TYPESAFE_API_KEY" if jev else "OPENROUTER_API_KEY"
        key = os.environ.get(env_name)
        if not key:
            raise ProviderError(f"missing {env_name}")
        endpoint = (
            "https://api.typesafe.ai/v1/systemone"
            if jev
            else "https://openrouter.ai/api/v1/chat/completions"
        )
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"HTTP {exc.code}; no automatic retry") from None
        except (OSError, ValueError) as exc:
            raise ProviderError(
                f"transport failure {type(exc).__name__}; no automatic retry"
            ) from None

    def usage(self, response, payload):
        jev = self.model.provider == "TypeSafe"
        try:
            usage = response["usage"]
            n_in = usage["input_tokens" if jev else "prompt_tokens"]
            n_out = usage["output_tokens" if jev else "completion_tokens"]
            if any(type(n) is not int or n < 0 for n in (n_in, n_out)):
                raise ValueError("invalid tokens")
            cost = n_in * self.model.input_price if jev else float(usage["cost"])
            if not math.isfinite(cost) or cost < 0:
                raise ValueError("invalid cost")
            return n_in, n_out, cost
        except (KeyError, TypeError, ValueError):
            raise ProviderError("missing/invalid usage; retain full reservation") from None

    def validate_identity(self, response):
        if response.get("model") != self.model.model:
            raise ProviderError("model identity mismatch")
        if self.model.provider != "TypeSafe":
            # OpenRouter uses capitalized provider display names in responses.
            if response.get("provider", "").casefold() != self.model.provider.casefold():
                raise ProviderError("provider identity mismatch")

    def select(self, response):
        # Malformed/refused/truncated generated output is an invalid action, fully charged.
        try:
            if self.model.provider == "TypeSafe":
                answer = response["answers"]["next_action"]
                return answer["choice"] if answer["type"] == "choice" else None
            choice = response["choices"][0]
            if choice["finish_reason"] != "stop":
                return None
            parsed = json.loads(choice["message"]["content"])
            return (
                parsed["action"] if isinstance(parsed, dict) and set(parsed) == {"action"} else None
            )
        except (KeyError, TypeError, ValueError, IndexError):
            return None
