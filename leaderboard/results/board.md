# Kitchen Rush — current leaderboard (gen 1.0)

Ruleset `33034952fa7f` (gen 1.0, frozen) · tokenizer `tiktoken-cl100k_base-v1` · track RP (experimental β) · 960 episodes · total $155.30 · = [starter run](starter.md) + 2026-06-11 patch (gpt-5.4 family & haiku, nemotron) + 2026-06-30 claude-sonnet-5 + 2026-07-03 GLM 5.2 (re-run reasoning-off) + 2026-07-03 gemini-3.5-flash reasoning-off (direct Gemini API)

KR = 100·clip((S−S_null)/(S_ref−S_null)), mean over seeds. `·think` = reasoning on (low effort). Not on the board: `gpt-5.4·think` (provider quota died mid-run — pending), `nemotron-3-ultra` (no tool_choice:required endpoint on OpenRouter), `gpt-oss-120b` reasoning-off (provider: reasoning is mandatory), `claude-sonnet-4.6·think†` (runs as a flagged deviation under tool_choice:auto since Anthropic forbids thinking with forced tool use — partially run, Anthropic credit died mid-config, pending completion).

| # | model | har B1 | har B5 | med B1 | med B5 | KR̄ | ±95%CI | serve% | reason/ep | $ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | claude-sonnet-4.6 | 37 | 37 | 36 | 52 | **40.6** | ±5.8 | 77% | 0 | 29.45 |
| 2 | gpt-5.4-mini·think | 5 | 28 | 32 | 59 | **31.1** | ±5.0 | 77% | 14886 | 6.5 |
| 3 | gemini-3.1-flash-lite | 26 | 22 | 37 | 20 | **26.3** | ±9.8 | 58% | 0 | 0.79 |
| 4 | gemini-3.5-flash | 17 | 14 | 39 | 32 | **25.6** | ±8.1 | 57% | 0 | 5.33 |
| 5 | gpt-5.4 | 16 | 22 | 23 | 32 | **23.2** | ±7.9 | 71% | 0 | 15.43 |
| 6 | glm-5.2 | 13 | 14 | 26 | 30 | **21.0** | ±6.2 | 67% | 0 | 5.09 |
| 7 | claude-sonnet-5 | 8 | 8 | 26 | 17 | **15.1** | ±6.2 | 44% | 0 | 22.58 |
| 8 | qwen3.7-plus | 8 | 7 | 12 | 7 | **8.3** | ±4.3 | 44% | 0 | 2.32 |
| 9 | claude-haiku-4.5 | 8 | 8 | 10 | 4 | **7.8** | ±4.2 | 36% | 0 | 6.74 |
| 10 | deepseek-v4-pro | 2 | 10 | 6 | 13 | **7.8** | ±5.4 | 33% | 0 | 2.04 |
| 11 | gpt-oss-120b·think | 0 | 4 | 6 | 18 | **7.1** | ±3.4 | 46% | 17301 | 0.42 |
| 12 | nemotron-3-super | 1 | 0 | 12 | 8 | **5.1** | ±5.2 | 29% | 0 | 0.36 |
| 13 | gemini-3.5-flash·think | 1 | 0 | 3 | 10 | **3.4** | ±3.2 | 33% | 31266 | 17.16 |
| 14 | grok-build·think | 1 | 0 | 2 | 5 | **2.1** | ±1.9 | 10% | 64714 | 7.23 |
| 15 | deepseek-v4-flash·think | 0 | 0 | 0 | 3 | **0.9** | ±1.8 | 13% | 0 | 0.46 |
| 16 | deepseek-v4-flash | 0 | 0 | 1 | 0 | **0.4** | ±0.7 | 10% | 0 | 0.42 |
| 17 | mistral-small | 0 | 0 | 0 | 1 | **0.3** | ±0.6 | 6% | 0 | 0.44 |
| 18 | gpt-5.4-mini | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | 14% | 0 | 5.98 |
| 19 | llama-4-scout | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | 7% | 0 | 0.57 |
| 20 | nemotron-3-nano | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | 0% | 0 | 0.17 |
