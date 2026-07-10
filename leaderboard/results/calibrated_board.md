# Kitchen Rush — calibrated real-speed board

_Dated calibrated real-speed deployment snapshot — each model's game clock is its own measured serving speed (frozen). NOT a fully reproducible or provider-neutral benchmark; re-measuring on another day/endpoint/region can change results._

Snapshot **2026-07-10** · calibration `cal-2026-07-10` · ruleset `33034952fa7f` · single kitchen (the benchmark) · serial · one provider pinned per model · blended $/token from provider-billed cost. Rank = band (rows in a band are statistically tied). `$/Mtok` = billed $ per 1M (prompt+completion) tokens.

## B = 1s

| rank | model | KR | ±95%CI | serve% | $/Mtok | level |
|---|---|--:|--:|--:|--:|--|
| 1–4 | claude-sonnet-4.6 | 36 | ±10.8 | 79% | 3.193 | — |
| 1–4 | glm-5.2 | 33 | ±11.1 | 75% | 0.329 | off |
| 1–4 | gemini-3.5-flash | 31 | ±15.8 | 63% | 1.814 | — |
| 1–4 | gemini-3.1-flash-lite | 29 | ±13.9 | 65% | 0.286 | — |
| 5–9 | gpt-5.4 | 26 | ±11.2 | 73% | 2.773 | none |
| 5–9 | gpt-5.6-luna | 21 | ±12.1 | 61% | 1.578 | minimal |
| 5–9 | gpt-oss-120b | 19 | ±7.8 | 66% | 0.15 | low |
| 5–9 | gpt-5.4-mini | 16 | ±13.7 | 51% | 1.384 | low |
| 5–9 | deepseek-v4-pro | 16 | ±12.0 | 41% | 0.99 | — |
| 10–11 | claude-haiku-4.5 | 14 | ±10.9 | 38% | 1.155 | — |
| 10–11 | qwen3.7-plus | 10 | ±6.4 | 52% | 0.35 | — |
| 12–16 | grok-build-0.1 | 3 | ±2.7 | 8% | 1.687 | — |
| 12–16 | nemotron-3-super | 2 | ±3.2 | 20% | 0.103 | — |
| 12–16 | nemotron-3-nano | 0 | ±0.0 | 0% | 0.052 | — |
| 12–16 | mistral-small | 0 | ±0.0 | 8% | 0.063 | — |
| 12–16 | deepseek-v4-flash | 0 | ±0.0 | 10% | 0.132 | off |

## B = 5s

| rank | model | KR | ±95%CI | serve% | $/Mtok | level |
|---|---|--:|--:|--:|--:|--|
| 1–2 | claude-sonnet-4.6 | 55 | ±12.5 | 89% | 3.194 | — |
| 1–2 | glm-5.2 | 39 | ±15.9 | 79% | 0.313 | off |
| 3–4 | gpt-5.6-luna | 37 | ±12.2 | 92% | 1.646 | minimal |
| 3–4 | gemini-3.5-flash | 32 | ±18.9 | 63% | 1.762 | — |
| 5–10 | gemini-3.1-flash-lite | 25 | ±14.6 | 45% | 0.29 | — |
| 5–10 | qwen3.7-plus | 24 | ±15.0 | 66% | 0.352 | — |
| 5–10 | gpt-5.4 | 21 | ±11.6 | 52% | 2.788 | none |
| 5–10 | gpt-5.4-mini | 20 | ±13.3 | 76% | 1.374 | low |
| 5–10 | deepseek-v4-pro | 13 | ±14.3 | 38% | 0.993 | — |
| 5–10 | gpt-oss-120b | 11 | ±12.1 | 54% | 0.3 | medium |
| 11–16 | claude-haiku-4.5 | 7 | ±6.8 | 34% | 1.184 | — |
| 11–16 | deepseek-v4-flash | 6 | ±10.1 | 32% | 0.143 | low |
| 11–16 | mistral-small | 4 | ±7.5 | 15% | 0.076 | — |
| 11–16 | grok-build-0.1 | 3 | ±3.0 | 14% | 1.649 | — |
| 11–16 | nemotron-3-super | 1 | ±1.6 | 13% | 0.09 | — |
| 11–16 | nemotron-3-nano | 0 | ±0.0 | 0% | 0.052 | — |

## Calibration appendix

| model | provider | decode tok/s | β0 | selected level (B1/B5) | flags |
|---|---|--:|--:|--|--|
| gpt-5.6-luna | default | 135.5 | 2.237323 | minimal/minimal | — |
| claude-sonnet-4.6 | default | 52.4 | 1.575041 | —/— | CLOCK_FIT_WEAK |
| claude-haiku-4.5 | default | 58.9 | 1.091749 | —/— | CLOCK_FIT_WEAK |
| gpt-5.4 | default | 78.3 | 0.92439 | none/none | CLOCK_FIT_WEAK |
| gpt-5.4-mini | default | 127.0 | 2.230499 | low/low | CLOCK_FIT_WEAK,DROPPED_1_OUTLIERS,CLOCK_DRIFT_CORRECTED_x2.26 |
| gpt-oss-120b | default | 349.7 | 0.278232 | low/medium | — |
| glm-5.2 | default | 41.8 | 1.329603 | off/off | CLOCK_FIT_WEAK |
| gemini-3.1-flash-lite | default | 148.9 | 0.699743 | —/— | CLOCK_FIT_WEAK,CLOCK_DRIFT_CORRECTED_x1.42 |
| gemini-3.5-flash | default | 85.7 | 0.989091 | —/— | — |
| grok-build-0.1 | default | 127.8 | 0.05 | —/— | CAL_TOO_FEW,CLOCK_INTERCEPT_FLOOR |
| qwen3.7-plus | default | 18.1 | 0.86273 | —/— | — |
| deepseek-v4-pro | default | 30.7 | 1.674951 | —/— | — |
| deepseek-v4-flash | default | 15.4 | 4.204921 | off/low | CLOCK_FIT_WEAK,DROPPED_3_OUTLIERS |
| nemotron-3-super | default | 22.2 | 1.291589 | —/— | CLOCK_FIT_WEAK,DROPPED_19_OUTLIERS |
| nemotron-3-nano | default | 55.2 | 0.31507 | —/— | CLOCK_FIT_WEAK |
| mistral-small | default | 491.9 | 0.715968 | —/— | CLOCK_FIT_WEAK |
| llama-4-scout | default | — | — | —/— | CAL_TOO_FEW |
