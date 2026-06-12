# Kitchen Rush — current leaderboard (gen 1.0)

Ruleset `33034952fa7f` (gen 1.0, frozen) · tokenizer `tiktoken-cl100k_base-v1` · track RP (experimental β) · 816 episodes · total $107.59 · = [starter run](starter.md) + 2026-06-11 patch (gpt-5.4 family & haiku via direct keys, nemotron via OpenRouter)

KR = 100·clip((S−S_null)/(S_ref−S_null)), mean over seeds. `·think` = reasoning on (low effort). Not on the board: `gpt-5.4·think` (provider quota died mid-run — pending), `nemotron-3-ultra` (no tool_choice:required endpoint on OpenRouter), `gpt-oss-120b` reasoning-off (provider: reasoning is mandatory), `claude-sonnet-4.6·think` (Anthropic API: thinking may not be enabled when tool_choice forces tool use, which the harness contract requires).

| # | model | har B1 | har B5 | med B1 | med B5 | KR̄ | ±95%CI | serve% | reason/ep | $ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | claude-sonnet-4.6 | 37 | 37 | 36 | 52 | **40.6** | ±5.8 | 77% | 0 | 29.45 |
| 2 | gpt-5.4-mini·think | 5 | 28 | 32 | 59 | **31.1** | ±5.0 | 77% | 14886 | 6.5 |
| 3 | gemini-3.1-flash-lite | 26 | 22 | 37 | 20 | **26.3** | ±9.8 | 58% | 0 | 0.79 |
| 4 | gpt-5.4 | 16 | 22 | 23 | 32 | **23.2** | ±7.9 | 71% | 0 | 15.43 |
| 5 | qwen3.7-plus | 8 | 7 | 12 | 7 | **8.3** | ±4.3 | 44% | 0 | 2.32 |
| 6 | claude-haiku-4.5 | 8 | 8 | 10 | 4 | **7.8** | ±4.2 | 36% | 0 | 6.74 |
| 7 | deepseek-v4-pro | 2 | 10 | 6 | 13 | **7.8** | ±5.4 | 33% | 0 | 2.04 |
| 8 | gpt-oss-120b·think | 0 | 4 | 6 | 18 | **7.1** | ±3.4 | 46% | 17301 | 0.42 |
| 9 | nemotron-3-super | 1 | 0 | 12 | 8 | **5.1** | ±5.2 | 29% | 0 | 0.36 |
| 10 | gemini-3.5-flash·think | 1 | 0 | 3 | 10 | **3.4** | ±3.2 | 33% | 31266 | 17.16 |
| 11 | grok-build·think | 1 | 0 | 2 | 5 | **2.1** | ±1.9 | 10% | 64714 | 7.23 |
| 12 | deepseek-v4-flash·think | 0 | 0 | 0 | 3 | **0.9** | ±1.8 | 13% | 0 | 0.46 |
| 13 | deepseek-v4-flash | 0 | 0 | 1 | 0 | **0.4** | ±0.7 | 10% | 0 | 0.42 |
| 14 | mistral-small | 0 | 0 | 0 | 1 | **0.3** | ±0.6 | 6% | 0 | 0.44 |
| 15 | gpt-5.4-mini | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | 14% | 0 | 5.98 |
| 16 | llama-4-scout | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | 7% | 0 | 0.57 |
| 17 | nemotron-3-nano | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | 0% | 0 | 0.17 |
