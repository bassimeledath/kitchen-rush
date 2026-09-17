# Full v2 model roster

All existing board entries remain in scope. Alias rows are explicit; no inference was run by this preflight.

| Existing label | Model endpoint | Route preflight |
|---|---|---|
| claude-haiku-4.5 | `anthropic/claude-haiku-4.5` | compatible_route_pending_canary |
| claude-sonnet-4.6 | `anthropic/claude-sonnet-4.6` | compatible_route_pending_canary |
| claude-sonnet-4.6·think† | `anthropic/claude-sonnet-4.6` | compatible_route_pending_canary |
| claude-sonnet-5 | `anthropic/claude-sonnet-5` | compatible_route_pending_canary |
| deepseek-v4-flash | `deepseek/deepseek-v4-flash` | compatible_route_pending_canary |
| deepseek-v4-flash·think | `deepseek/deepseek-v4-flash` | compatible_route_pending_canary |
| deepseek-v4-pro | `deepseek/deepseek-v4-pro` | compatible_route_pending_canary |
| gemini-3.1-flash-lite | `google/gemini-3.1-flash-lite` | compatible_route_pending_canary |
| gemini-3.5-flash | `google/gemini-3.5-flash` | compatible_route_pending_canary |
| gemini-3.5-flash-lite | `google/gemini-3.5-flash-lite` | compatible_route_pending_canary |
| gemini-3.5-flash·think | `google/gemini-3.5-flash` | compatible_route_pending_canary |
| gemini-3.6-flash·low | `google/gemini-3.6-flash` | compatible_route_pending_canary |
| gemini-3.7-flash·low | `google/gemini-3.7-flash` | compatible_route_pending_canary |
| glm-5.2 | `z-ai/glm-5.2` | compatible_route_pending_canary |
| glm-5.3-flash | `z-ai/glm-5.3-flash` | compatible_route_pending_canary |
| gpt-5.4 | `openai/gpt-5.4` | compatible_route_pending_canary |
| gpt-5.4-mini | `openai/gpt-5.4-mini` | compatible_route_pending_canary |
| gpt-5.4-mini·think | `openai/gpt-5.4-mini` | compatible_route_pending_canary |
| gpt-5.4·think | `openai/gpt-5.4` | compatible_route_pending_canary |
| gpt-5.6-luna | `openai/gpt-5.6-luna` | compatible_route_pending_canary |
| gpt-oss-120b | `openai/gpt-oss-120b` | Alias of gpt-oss-120b·think |
| gpt-oss-120b·think | `openai/gpt-oss-120b` | compatible_route_pending_canary |
| grok-build-0.1 | `x-ai/grok-build-0.1` | Alias of grok-build·think |
| grok-build·think | `x-ai/grok-build-0.1` | compatible_route_pending_canary |
| jev | `jev-1.13.0` | pilot_candidate |
| llama-4-scout | `meta-llama/llama-4-scout` | compatible_route_pending_canary |
| mistral-small | `mistralai/mistral-small-2603` | compatible_route_pending_canary |
| nemotron-3-nano | `nvidia/nemotron-3-nano-30b-a3b` | compatible_route_pending_canary |
| nemotron-3-super | `nvidia/nemotron-3-super-120b-a12b` | compatible_route_pending_canary |
| nemotron-3-ultra | `nvidia/nemotron-3-ultra-550b-a55b` | compatible_route_pending_canary |
| phonellm-alpha-1† | `modal:pipecat-ai/phonellm-alpha-1` | blocked_cost_attribution |
| qwen3.7-plus | `qwen/qwen3.7-plus` | compatible_route_pending_canary |

Route compatibility is metadata only. Reasoning settings and a real canary must be checked before each full-sweep configuration is eligible. Historical thinking/off variants remain listed; they must not be silently collapsed. PhoneLLM requires a defensible allocation of its compute bill before a dollar-budget ranking.

Source: the frozen JSON at `data/v2/roster.json`, its source-board hashes, and saved OpenRouter endpoint responses.
