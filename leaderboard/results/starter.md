# Kitchen Rush — starter leaderboard (starter)

Ruleset `33034952fa7f` (gen 1.0, frozen=True) · tokenizer `tiktoken-cl100k_base-v1` · track RP (experimental β) · 576 episodes · total $67.68

KR = 100·clip((S−S_null)/(S_ref−S_null)), mean over seeds. `KR̄` = mean over tier×budget. `Δlat` = mean KR at the loosest budget − tightest (latency head-room). `·think` = reasoning on.

| # | model | reason | har B1 | har B5 | med B1 | med B5 | KR̄ | ±95%CI | Δlat | serve% | reason/ep | $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | claude-sonnet-4.6 | off | 37 | 37 | 36 | 52 | **40.6** | ±5.8 | +8 | 77% | 0 | 29.45 |
| 2 | gemini-3.1-flash-lite | off | 26 | 22 | 37 | 20 | **26.3** | ±9.8 | -11 | 58% | 0 | 0.79 |
| 3 | qwen3.7-plus | off | 8 | 7 | 12 | 7 | **8.3** | ±4.3 | -3 | 44% | 0 | 2.32 |
| 4 | deepseek-v4-pro | off | 2 | 10 | 6 | 13 | **7.8** | ±5.4 | +7 | 33% | 0 | 2.04 |
| 5 | gpt-oss-120b·think | low | 0 | 4 | 6 | 18 | **7.1** | ±3.4 | +8 | 46% | 17301 | 0.42 |
| 6 | gemini-3.5-flash·think | default(on) | 1 | 0 | 3 | 10 | **3.4** | ±3.2 | +3 | 33% | 31266 | 17.16 |
| 7 | grok-build·think | default(on) | 1 | 0 | 2 | 5 | **2.1** | ±1.9 | +1 | 10% | 64714 | 7.23 |
| 8 | deepseek-v4-flash·think | low | 0 | 0 | 0 | 3 | **0.9** | ±1.8 | +2 | 13% | 0 | 0.46 |
| 9 | deepseek-v4-flash | off | 0 | 0 | 1 | 0 | **0.4** | ±0.7 | -1 | 10% | 0 | 0.42 |
| 10 | mistral-small | off | 0 | 0 | 0 | 1 | **0.3** | ±0.6 | -0 | 6% | 0 | 0.44 |
| 11 | gpt-5.4-mini | off | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | +0 | 15% | 0 | 6.38 |
| 12 | llama-4-scout | off | 0 | 0 | 0 | 0 | **0.0** | ±0.0 | +0 | 7% | 0 | 0.57 |
