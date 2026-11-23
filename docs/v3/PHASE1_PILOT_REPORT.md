# Kitchen Rush v3.2 — Phase 1 Pilot Report (Pre-Scale External Review)

> **Purpose:** Intermediate checkpoint report after **Phase 1 (`n = 3` seeds/level × `5` levels × `15` models = `225` planned, `218` scored, `$39.73` actual OpenRouter spend)** on the private held-out seed bank (`data/v3/private_leaderboard_v1.json`, gitignored) so an external reviewer can audit protocol integrity, monotonicity, forensic anomalies, and budget projections before greenlighting Phase 2 (`n = 10`, `~$143` total) or Phase 3 (`n = 20`, `~$286` total).

---

## 1. Locked `v3.2` Evaluation Protocol

- **Single unified deterministic engine (`src/kitchenrush/v3/env.py`):** 8 native JSON function-calling tools (`collect`, `chop`, `cook`, `collect_cooked`, `plate`, `serve`, `discard`, `wait`), 1–8 batched calls/turn with event-driven interrupts, automatic walking, procedural per-seed `ingredient_rules` (`cookable_from ∈ {RAW, CHOPPED}`, `cook_time`, `burn_window`), shuffled stations (`D >= 3`), hidden staggered arrivals (`D >= 3`), provable early-stop, and SHA-256 deterministic replay verification (`verify_replay`).
- **Context & reasoning continuity (`src/kitchenrush/v3/adapters.py`, `runner.py`):**
  - Append-only native multi-turn `full_chat` transcript with explicit `cache_control: ephemeral` on Anthropic & Google and automatic caching on OpenAI.
  - Preserves OpenRouter `reasoning_details` (e.g. Google's encrypted `thought_signature`) and `raw_tool_calls` verbatim across turns (excluding truncated `finish_reason="length"` turns).
  - Uniform ceiling: `DEFAULT_MAX_TOKENS = 32768`, `DEFAULT_REQUEST_TIMEOUT_S = 300`, `max_actions = max_turns = 160` (`D=1..5`).
  - `tool_choice = "required"` on all providers except Anthropic reasoning models, which require `tool_choice = "auto"` (Anthropic API rejects `"any"`/`"required"` when extended thinking is enabled; recorded transparently per episode in `manifest.json`).
- **Strictly monotonic 5-level ladder (`full_burner_witness` only, zero conservative fallback):**

| Dial | Role | Orders / Burners | Cooked / Burner | Burn Window | Slack / Pad | Witness Actions (`n=20` private bank) | Error Tolerance (`40`-seed Monte Carlo) |
| :-: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **D=1** | Easy / Floor | 2 / 2 | `1.00` | `14–18s` | `65% / 16s` | `15.2` | `15.9s` |
| **D=2** | Basic Overlap | 3 / 3 | `1.48` | `12–15s` | `22% / 9s` | `28.6` | `9.1s` |
| **D=3** | Multi-Wave | 4 / 3 | `2.10` | `10–13s` | `12% / 6s` | `39.3` | `5.2s` |
| **D=4** | **Hard** | **6 / 4** | **`3.00`** | **`8–10s`** | **`4% / 3.5s`** | **`71.1`** | **`2.8s`** |
| **D=5** | **Near-Perfection** | **8 / 4** | **`4.00`** | **`6–8s`** | **`2% / 2.0s`** | **`97.6`** | **`1.2s`** |

- **Primary Metrics (Full-Clear Gated):** `Full Clears`, `Order Completion` (partial credit), `Out Tok / Cleared Ord`, `USD / Cleared Ord` (all scored-episode spend pooled in numerator / orders in fully cleared kitchens; `"no clear"` when 0 clears), secondary `USD / Served Ord`, `Inv+Burn / 100 Acts`, and `Empty / Trunc Turns`.
- **Checkpointing & Resume Guarantees:** Atomic `.tmp -> os.replace` episode writes, immutable `manifest.json` spec/config validation, persistent crash-safe `spend.jsonl` reservations, 5-attempt jittered exponential backoff (`429/502/503/504/529` + `Retry-After` + OpenRouter 200-error retries), and automatic archival/retry of incomplete (`stopped_cap`/`error`) episodes on resume. Zero completed episodes are ever re-run across phases.

---

## 2. Phase 1 Summary Scoreboard (`218/225` Scored, `$39.73` Spent, `0` Provider Errors)

> The `$40.00` Phase 1 cap engaged cleanly on the final 7 episodes of Seed 3 at `D=5` (`6` `stopped_cap` + `1` skipped; `3,589/3,589` turns provider-billed, `0` estimated fallbacks). All 7 incomplete episodes will auto-retry cleanly on resume.

| Model | Tier | Scored | Spend | D=1 | D=2 | D=3 | **D=4 (Hard)** | **D=5 (Near-Perf.)** | D=5 Comp | Trunc / Emp | Est. `n=10` | Est. `n=20` |
| :--- | :---: | :---: | ---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | ---: | ---: |
| **`gpt-6.2-sol-medium`** | Frontier | 14/15 | `$2.93` | 3/3 | 3/3 | 3/3 | **3/3** (`$0.054`) | **1/2** (`$0.123`) | **93.8%** | 0/0 | `$10.5` | `$21` |
| **`gemini-4-carbon-medium`** | Frontier | 14/15 | `$4.96` | 3/3 | 3/3 | 3/3 | **2/3** (`$0.172`) | 0/2 *(0i+0b)* | **87.5%** | 0/0 | `$18.4` | `$37` |
| **`gemini-4-neon-medium`** | Frontier | 14/15 | `$4.67` | 3/3 | 3/3 | 2/3 | **1/3** (`$0.304`) | 0/2 *(0i+0b)* | **87.5%** | 0/0 | `$18.1` | `$36` |
| **`gpt-6.1-terra-medium`** | Mid | 15/15 | `$1.15` | 3/3 | 3/3 | **3/3** (`$0.014`) | 0/3 | 0/3 *(0i+0b)* | 75.0% | 0/0 | `$3.8` | `$8` |
| **`qwen4-max-medium`** | Mid | 14/15 | `$3.04` | 3/3 | 2/3 | **3/3** (`$0.029`) | 0/3 | 0/2 | **87.5%** | 1/1 | `$10.5` | `$21` |
| **`deepseek-v4.1-flash-low`** | Budget | 15/15 | `$0.85` | 3/3 | 2/3 | **2/3** (`$0.018`) | 0/3 | 0/3 | 70.8% | **8/8** | `$2.8` | `$6` |
| `grok-4.7-medium` | Frontier | 14/15 | `$4.18` | 3/3 | 3/3 | 1/3 | 0/3 | 0/2 | 50.0% | 0/0 | `$14.4` | `$29` |
| `deepseek-v4-pro-medium` | Mid | 14/15 | `$6.08` | 3/3 | 3/3 | 1/3 | 0/3 | 0/2 | 75.0% | 0/0 | `$22.7` | `$45` |
| `gemini-4-hydrogen-medium` | Mid | 15/15 | `$2.30` | 3/3 | 2/3 | 1/3 | 0/3 | 0/3 | 79.2% | 0/0 | `$7.7` | `$15` |
| `claude-sonnet-5.5-medium` | Mid | 15/15 | `$2.36` | 2/3 | 2/3 | 1/3 | 0/3 | 0/3 | 58.3% | 0/0 | `$7.9` | `$16` |
| `claude-haiku-5.5-low` | Budget | 15/15 | `$0.10` | 3/3 | **1/3** (`$0.004`) | 0/3 | 0/3 | 0/3 | 45.8% | 0/0 | `$0.3` | `$0.6` |
| `gemini-3.8-flash-low` | Budget | 15/15 | `$0.42` | 3/3 | 1/3 | 0/3 | 0/3 | 0/3 | 33.3% | 0/0 | `$1.4` | `$3` |
| `claude-opus-5.5-medium` | Frontier | 14/15 | `$6.26` | 3/3 | 1/3 | 0/3 | 0/3 | 0/2 | 68.8% | 0/0 | `$23.4` | `$47` |
| `gpt-6-luna-low` | Budget | 15/15 | `$0.09` | **3/3** (`$0.0008`) | 0/3 | 0/3 | 0/3 | 0/3 | 0.0% | 0/0 | `$0.3` | `$0.6` |
| `nemotron-3.5-super-vl-low` | Budget | 15/15 | `$0.35` | 2/3 | 0/3 | 0/3 | 0/3 | 0/3 | 25.0% | 1/1 | `$1.2` | `$2.3` |

---

## 3. Forensic Audit of Key Findings & Anomalies

1. **Strict Monotonicity & Efficiency Crossover Validated:**
   - Every single model degrades monotonically from `D=1 -> D=5`.
   - Cost-per-cleared-order winners step cleanly across tiers: `gpt-6-luna-low` (`$0.0008`) at `D=1` → `claude-haiku-5.5-low` (`$0.0035`) / `gpt-6.1-terra-medium` (`$0.0122`) at `D=2` → `gpt-6.1-terra-medium` (`$0.0136`) & `deepseek-v4.1-flash-low` (`$0.0179`) at `D=3` → `gpt-6.2-sol-medium` (`$0.0537`) at `D=4` → `gpt-6.2-sol-medium` (`$0.1231`, sole `D=5` clear) at `D=5`.
2. **Forensic Check on `claude-opus-5.5-medium` (`1/3` D2, `0/3` D3..5, `$6.26` spent):**
   - Verified **zero harness bugs**: `0` provider errors, `0/0` empty/truncated turns, `68.7%` reasoning tokens (`42.4K` rsn / `61.7K` out on `D=3..4`), `71–84%` prompt caching.
   - Trace replay of `D=2 seed=50000` confirmed genuine planning errors: Opus filled all 4 hand slots with mixed ingredients from `O2` and `O3`, then called `discard` on its own cooked mushroom & chopped lettuce to free slots for plating `O2`, making `O3` mathematically impossible. On `D=3 seed=50000`, it made 2 `early_pickup` cook-timer errors and 2 exact-hand `plate` collisions.
3. **Forensic Check on `deepseek-v4.1-flash-low` (`8/8` Trunc/Emp turns):**
   - Across 6 distinct third-party hosts (`Reka`, `Ionstream`, `InferenceNet`, `DigitalOcean`, `River`, `Nebius`), it entered a 32K hidden-reasoning loop (`out=32768, rsn=32768, calls=[]`) mostly on Turn 1, whereas its normal turns averaged `1.3K–5.6K` median tokens (`max 24K`).
   - Our runner recovers cleanly at **0 game-seconds lost** (e.g., on `D=2 seed=50000` it looped twice on Turns 1–2 and still full-cleared `3/3`), while charging those wasted tokens honestly to `Out Tok / Cleared Ord` and `USD / Cleared Ord`.
   - 12 of 15 models had `0/0` empty/truncated turns across `3,589` total turns, confirming `max_tokens = 32,768` is non-restrictive for normal planning.
4. **Behavioral Divergence Between Top Frontier Families:**
   - OpenAI (`gpt-6.2-sol-medium`, `gpt-6.1-terra-medium`) emits `1.00` action/turn with `95–98%` prompt caching and tiny output volume (`20K–32K` tokens across 6 `D=3..4` episodes), achieving top reliability *and* 3–5× lower frontier cost.
   - Google Gemini 4 (`carbon`, `neon`, `hydrogen`) and `qwen4-max` macro-batch `5.4–7.4` actions/turn with heavy reasoning (`95–97%` reasoning share) and near-zero mistakes (`0i+0b` on `D=5` for both `carbon` and `neon`).

---

## 4. Exact Full-Run Cost Projections (From Real Phase 1 Receipts)

Measured exact mean cost for **1 complete seed across all 15 models × `D=1..5` (`75` episodes): `$14.31/seed`** (`D=1`: `$0.47`, `D=2`: `$1.25`, `D=3`: `$1.94`, `D=4`: `$4.55`, `D=5`: `$6.10`).

| Target `n` (Seeds / Level) | Total Episodes | Est. Total Cost | Remaining From Here (`$39.73` spent) |
| :---: | :---: | ---: | ---: |
| **`n = 6`** | 450 | `~$86` | `~$46` *(fits in existing `$61.47` balance)* |
| **`n = 10`** | 750 | `~$143` | `~$103` |
| **`n = 12`** | 900 | `~$172` | `~$132` |
| **`n = 15`** | 1,125 | `~$215` | `~$175` |
| **`n = 20`** | 1,500 | `~$286` | `~$247` |

**Top 5 cost drivers (68% of total spend):** `claude-opus-5.5-medium` (`$2.34/seed`), `deepseek-v4-pro-medium` (`$2.27/seed`), `gemini-4-carbon-medium` (`$1.84/seed`), `gemini-4-neon-medium` (`$1.81/seed`), `grok-4.7-medium` (`$1.44/seed`). The remaining 10 models combined — including `gpt-6.2-sol-medium` (`$1.05/seed`) — total only `$4.61/seed`.

---

## 5. Questions for External Reviewer Sign-Off

1. **Methodology & Fairness:** Any concerns with `tool_choice="auto"` on Anthropic reasoning models (required by Anthropic API) vs `tool_choice="required"` on all other providers, or with leaving `max_tokens=32768` uniform despite `deepseek-v4.1-flash-low`'s 8 Turn-1 reasoning loops?
2. **Difficulty Calibration:** Does the `D=1..5` spread (`D=4` separating top 3 frontier models from all mid/budget models; `D=5` yielding `1/2` clear for `gpt-6.2-sol-medium` and `87.5%` partial completion for `carbon`/`neon`/`qwen4-max`) look well-calibrated for the full run?
3. **Budget vs. Sample Size (`n`):** Should we target **`n = 10` symmetric across all 15 models (`~$143` total)**, **`n = 20` full (`~$286` total)**, or **`n = 10` for all 15 + `n = 20` on `D=4..5` for top contenders (`~$180` total)**?
4. **Roster Tweaks:** Any models to swap/drop before Phase 2 (e.g., `deepseek-v4-pro-medium` at `$2.27/seed` or adding a `-low` vs `-medium` pair for crossover)?

---

## 6. Appendix — Complete Phase 1 Per-Level Table (`runs/v3/official_v1/REPORT.md`)

| D | Model | Clears | Order Completion | Out Tok / Cleared Ord | USD / Cleared Ord | USD / Served Ord | Inv+Burn / 100 Acts | Empty / Trunc Turns | Avg Batch | Cache % | Spend |
| :-: | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| D=1 | claude-haiku-5.5-low | 3/3 | 6/6 (100.0%) | 1,527 | $0.0014 | $0.0014 | 2.1 (1i+0b) | 0/0 | 4.80 | 41.8% | $0.0087 |
| D=1 | claude-opus-5.5-medium | 3/3 | 6/6 (100.0%) | 936 | $0.0417 | $0.0417 | 3.7 (2i+0b) | 0/0 | 4.91 | 49.7% | $0.2499 |
| D=1 | claude-sonnet-5.5-medium | 2/3 | 5/6 (83.3%) | 1,100 | $0.0255 | $0.0204 | 5.8 (2i+1b) | 0/0 | 5.20 | 50.8% | $0.1020 |
| D=1 | deepseek-v4-pro-medium | 3/3 | 6/6 (100.0%) | 5,582 | $0.0366 | $0.0366 | 4.3 (2i+0b) | 0/0 | 7.67 | 0.0% | $0.2194 |
| D=1 | deepseek-v4.1-flash-low | 3/3 | 6/6 (100.0%) | 4,436 | $0.0039 | $0.0039 | 4.3 (2i+0b) | 0/0 | 5.11 | 45.6% | $0.0236 |
| D=1 | gemini-3.8-flash-low | 3/3 | 6/6 (100.0%) | 568 | $0.0076 | $0.0076 | 8.8 (4i+1b) | 0/0 | 3.56 | 0.0% | $0.0456 |
| D=1 | gemini-4-carbon-medium | 3/3 | 6/6 (100.0%) | 1,352 | $0.0225 | $0.0225 | 0.0 (0i+0b) | 0/0 | 5.62 | 0.0% | $0.1349 |
| D=1 | gemini-4-hydrogen-medium | 3/3 | 6/6 (100.0%) | 1,861 | $0.0197 | $0.0197 | 4.3 (2i+0b) | 0/0 | 6.71 | 16.0% | $0.1183 |
| D=1 | gemini-4-neon-medium | 3/3 | 6/6 (100.0%) | 1,524 | $0.0362 | $0.0362 | 0.0 (0i+0b) | 0/0 | 5.62 | 11.1% | $0.2171 |
| D=1 | gpt-6-luna-low | 3/3 | 6/6 (100.0%) | 246 | $0.0008 | $0.0008 | 11.3 (7i+0b) | 0/0 | 1.44 | 86.5% | $0.0047 |
| D=1 | gpt-6.1-terra-medium | 3/3 | 6/6 (100.0%) | 343 | $0.0070 | $0.0070 | 0.0 (0i+0b) | 0/0 | 1.00 | 86.6% | $0.0418 |
| D=1 | gpt-6.2-sol-medium | 3/3 | 6/6 (100.0%) | 209 | $0.0132 | $0.0132 | 0.0 (0i+0b) | 0/0 | 1.00 | 85.6% | $0.0794 |
| D=1 | grok-4.7-medium | 3/3 | 6/6 (100.0%) | 2,322 | $0.0192 | $0.0192 | 0.0 (0i+0b) | 0/0 | 5.62 | 61.9% | $0.1153 |
| D=1 | nemotron-3.5-super-vl-low | 2/3 | 5/6 (83.3%) | 8,656 | $0.0051 | $0.0041 | 7.8 (4i+0b) | 0/0 | 3.19 | 72.3% | $0.0203 |
| D=1 | qwen4-max-medium | 3/3 | 6/6 (100.0%) | 1,022 | $0.0065 | $0.0065 | 4.3 (2i+0b) | 0/0 | 7.67 | 54.0% | $0.0390 |
| D=2 | claude-haiku-5.5-low | 1/3 | 6/9 (66.7%) | 3,489 | $0.0035 | $0.0018 | 0.0 (0i+0b) | 0/0 | 4.73 | 57.9% | $0.0106 |
| D=2 | claude-opus-5.5-medium | 1/3 | 7/9 (77.8%) | 5,897 | $0.2340 | $0.1003 | 5.0 (4i+1b) | 0/0 | 4.17 | 70.8% | $0.7021 |
| D=2 | claude-sonnet-5.5-medium | 2/3 | 8/9 (88.9%) | 1,407 | $0.0374 | $0.0280 | 8.3 (7i+1b) | 0/0 | 3.84 | 70.5% | $0.2243 |
| D=2 | deepseek-v4-pro-medium | 3/3 | 9/9 (100.0%) | 9,452 | $0.0592 | $0.0592 | 1.1 (1i+0b) | 0/0 | 5.56 | 71.0% | $0.5326 |
| D=2 | deepseek-v4.1-flash-low | 2/3 | 8/9 (88.9%) | 26,567 | $0.0202 | $0.0151 | 3.7 (2i+1b) | 3/3 | 4.10 | 71.3% | $0.1212 |
| D=2 | gemini-3.8-flash-low | 1/3 | 7/9 (77.8%) | 4,065 | $0.0311 | $0.0133 | 2.3 (2i+0b) | 0/0 | 4.30 | 45.1% | $0.0934 |
| D=2 | gemini-4-carbon-medium | 3/3 | 9/9 (100.0%) | 2,257 | $0.0354 | $0.0354 | 0.0 (0i+0b) | 0/0 | 6.85 | 26.4% | $0.3188 |
| D=2 | gemini-4-hydrogen-medium | 2/3 | 8/9 (88.9%) | 3,514 | $0.0400 | $0.0300 | 1.2 (0i+1b) | 0/0 | 6.23 | 30.8% | $0.2400 |
| D=2 | gemini-4-neon-medium | 3/3 | 9/9 (100.0%) | 2,303 | $0.0478 | $0.0478 | 0.0 (0i+0b) | 0/0 | 7.25 | 49.5% | $0.4303 |
| D=2 | gpt-6-luna-low | 0/3 | 0/9 (0.0%) | no clear | no clear | — | 18.2 (12i+0b) | 0/0 | 1.35 | 87.8% | $0.0061 |
| D=2 | gpt-6.1-terra-medium | 3/3 | 9/9 (100.0%) | 592 | $0.0122 | $0.0122 | 0.0 (0i+0b) | 0/0 | 1.00 | 92.8% | $0.1097 |
| D=2 | gpt-6.2-sol-medium | 3/3 | 9/9 (100.0%) | 662 | $0.0235 | $0.0235 | 0.0 (0i+0b) | 0/0 | 1.00 | 92.9% | $0.2116 |
| D=2 | grok-4.7-medium | 3/3 | 9/9 (100.0%) | 8,725 | $0.0622 | $0.0622 | 1.1 (1i+0b) | 0/0 | 5.29 | 60.8% | $0.5594 |
| D=2 | nemotron-3.5-super-vl-low | 0/3 | 5/9 (55.6%) | no clear | no clear | $0.0093 | 3.1 (2i+0b) | 0/0 | 2.83 | 39.1% | $0.0467 |
| D=2 | qwen4-max-medium | 2/3 | 8/9 (88.9%) | 3,487 | $0.0226 | $0.0170 | 3.6 (3i+0b) | 0/0 | 5.60 | 71.8% | $0.1357 |
| D=3 | claude-haiku-5.5-low | 0/3 | 8/12 (66.7%) | no clear | no clear | $0.0021 | 3.0 (1i+2b) | 0/0 | 4.00 | 69.8% | $0.0168 |
| D=3 | claude-opus-5.5-medium | 0/3 | 8/12 (66.7%) | no clear | no clear | $0.1294 | 7.6 (9i+1b) | 0/0 | 3.97 | 73.1% | $1.0352 |
| D=3 | claude-sonnet-5.5-medium | 1/3 | 10/12 (83.3%) | 3,176 | $0.0797 | $0.0319 | 4.8 (4i+2b) | 0/0 | 4.28 | 72.2% | $0.3188 |
| D=3 | deepseek-v4-pro-medium | 1/3 | 7/12 (58.3%) | 28,720 | $0.2126 | $0.1215 | 5.7 (5i+1b) | 0/0 | 5.25 | 69.7% | $0.8505 |
| D=3 | deepseek-v4.1-flash-low | 2/3 | 11/12 (91.7%) | 21,638 | $0.0179 | $0.0130 | 4.9 (5i+1b) | 1/1 | 5.86 | 90.7% | $0.1431 |
| D=3 | gemini-3.8-flash-low | 0/3 | 6/12 (50.0%) | no clear | no clear | $0.0102 | 4.6 (3i+2b) | 0/0 | 3.52 | 49.2% | $0.0612 |
| D=3 | gemini-4-carbon-medium | 3/3 | 12/12 (100.0%) | 4,081 | $0.0535 | $0.0535 | 0.0 (0i+0b) | 0/0 | 7.44 | 59.9% | $0.6424 |
| D=3 | gemini-4-hydrogen-medium | 1/3 | 10/12 (83.3%) | 9,404 | $0.1036 | $0.0414 | 0.9 (1i+0b) | 0/0 | 6.24 | 51.4% | $0.4143 |
| D=3 | gemini-4-neon-medium | 2/3 | 11/12 (91.7%) | 3,862 | $0.0831 | $0.0605 | 0.0 (0i+0b) | 0/0 | 7.00 | 53.7% | $0.6651 |
| D=3 | gpt-6-luna-low | 0/3 | 1/12 (8.3%) | no clear | no clear | $0.0122 | 16.8 (16i+0b) | 0/0 | 1.13 | 92.5% | $0.0122 |
| D=3 | gpt-6.1-terra-medium | 3/3 | 12/12 (100.0%) | 481 | $0.0136 | $0.0136 | 0.0 (0i+0b) | 0/0 | 1.00 | 94.6% | $0.1631 |
| D=3 | gpt-6.2-sol-medium | 3/3 | 12/12 (100.0%) | 754 | $0.0277 | $0.0277 | 0.0 (0i+0b) | 0/0 | 1.00 | 95.1% | $0.3321 |
| D=3 | grok-4.7-medium | 1/3 | 10/12 (83.3%) | 28,660 | $0.1975 | $0.0790 | 2.8 (2i+1b) | 0/0 | 4.95 | 75.8% | $0.7901 |
| D=3 | nemotron-3.5-super-vl-low | 0/3 | 2/12 (16.7%) | no clear | no clear | $0.0203 | 11.9 (13i+0b) | 0/0 | 2.42 | 85.5% | $0.0405 |
| D=3 | qwen4-max-medium | 3/3 | 12/12 (100.0%) | 4,602 | $0.0288 | $0.0288 | 0.8 (1i+0b) | 0/0 | 6.67 | 75.9% | $0.3454 |
| D=4 | claude-haiku-5.5-low | 0/3 | 7/18 (38.9%) | no clear | no clear | $0.0042 | 4.3 (2i+4b) | 0/0 | 3.54 | 78.1% | $0.0292 |
| D=4 | claude-opus-5.5-medium | 0/3 | 13/18 (72.2%) | no clear | no clear | $0.1244 | 3.7 (4i+3b) | 0/0 | 3.90 | 81.1% | $1.6171 |
| D=4 | claude-sonnet-5.5-medium | 0/3 | 10/18 (55.6%) | no clear | no clear | $0.0664 | 13.8 (13i+12b) | 0/0 | 3.42 | 82.3% | $0.6639 |
| D=4 | deepseek-v4-pro-medium | 0/3 | 8/18 (44.4%) | no clear | no clear | $0.2295 | 13.9 (22i+5b) | 0/0 | 3.59 | 90.2% | $1.8362 |
| D=4 | deepseek-v4.1-flash-low | 0/3 | 11/18 (61.1%) | no clear | no clear | $0.0180 | 6.9 (3i+8b) | 2/2 | 4.00 | 98.1% | $0.1981 |
| D=4 | gemini-3.8-flash-low | 0/3 | 9/18 (50.0%) | no clear | no clear | $0.0092 | 10.0 (11i+6b) | 0/0 | 3.95 | 76.2% | $0.0829 |
| D=4 | gemini-4-carbon-medium | 2/3 | 17/18 (94.4%) | 13,158 | $0.1715 | $0.1210 | 1.0 (2i+0b) | 0/0 | 6.44 | 82.0% | $2.0575 |
| D=4 | gemini-4-hydrogen-medium | 0/3 | 14/18 (77.8%) | no clear | no clear | $0.0553 | 3.3 (4i+2b) | 0/0 | 5.75 | 81.2% | $0.7742 |
| D=4 | gemini-4-neon-medium | 1/3 | 16/18 (88.9%) | 14,949 | $0.3042 | $0.1141 | 0.0 (0i+0b) | 0/0 | 7.15 | 75.3% | $1.8251 |
| D=4 | gpt-6-luna-low | 0/3 | 1/18 (5.6%) | no clear | no clear | $0.0194 | 18.7 (29i+0b) | 0/0 | 1.57 | 93.7% | $0.0194 |
| D=4 | gpt-6.1-terra-medium | 0/3 | 13/18 (72.2%) | no clear | no clear | $0.0266 | 1.1 (0i+2b) | 0/0 | 1.00 | 96.4% | $0.3463 |
| D=4 | gpt-6.2-sol-medium | 3/3 | 18/18 (100.0%) | 1,295 | $0.0537 | $0.0537 | 0.0 (0i+0b) | 0/0 | 1.00 | 96.9% | $0.9662 |
| D=4 | grok-4.7-medium | 0/3 | 11/18 (61.1%) | no clear | no clear | $0.1303 | 9.1 (11i+5b) | 0/0 | 4.09 | 71.5% | $1.4336 |
| D=4 | nemotron-3.5-super-vl-low | 0/3 | 5/18 (27.8%) | no clear | no clear | $0.0219 | 6.4 (6i+3b) | 1/1 | 2.35 | 85.6% | $0.1096 |
| D=4 | qwen4-max-medium | 0/3 | 11/18 (61.1%) | no clear | no clear | $0.1529 | 7.0 (7i+7b) | 0/0 | 4.85 | 88.0% | $1.6823 |
| D=5 | claude-haiku-5.5-low | 0/3 | 11/24 (45.8%) | no clear | no clear | $0.0029 | 0.7 (0i+1b) | 0/0 | 3.83 | 77.0% | $0.0318 |
| D=5 | claude-opus-5.5-medium | 0/2 | 11/16 (68.8%) | no clear | no clear | $0.2077 | 4.9 (2i+6b) | 0/0 | 3.62 | 84.4% | $2.6522 |
| D=5 | claude-sonnet-5.5-medium | 0/3 | 14/24 (58.3%) | no clear | no clear | $0.0751 | 10.3 (21i+6b) | 0/0 | 3.25 | 87.7% | $1.0510 |
| D=5 | deepseek-v4-pro-medium | 0/2 | 12/16 (75.0%) | no clear | no clear | $0.1866 | 2.8 (1i+4b) | 0/0 | 4.62 | 80.5% | $2.6375 |
| D=5 | deepseek-v4.1-flash-low | 0/3 | 17/24 (70.8%) | no clear | no clear | $0.0214 | 7.3 (10i+10b) | 2/2 | 4.22 | 95.8% | $0.3638 |
| D=5 | gemini-3.8-flash-low | 0/3 | 8/24 (33.3%) | no clear | no clear | $0.0166 | 6.0 (7i+5b) | 0/0 | 2.99 | 87.5% | $0.1327 |
| D=5 | gemini-4-carbon-medium | 0/2 | 14/16 (87.5%) | no clear | no clear | $0.1126 | 0.0 (0i+0b) | 0/0 | 6.30 | 80.7% | $1.8056 |
| D=5 | gemini-4-hydrogen-medium | 0/3 | 19/24 (79.2%) | no clear | no clear | $0.0397 | 2.1 (3i+2b) | 0/0 | 5.42 | 80.2% | $0.7548 |
| D=5 | gemini-4-neon-medium | 0/2 | 14/16 (87.5%) | no clear | no clear | $0.1097 | 0.0 (0i+0b) | 0/0 | 6.25 | 82.4% | $1.5360 |
| D=5 | gpt-6-luna-low | 0/3 | 0/24 (0.0%) | no clear | no clear | — | 19.6 (37i+0b) | 0/0 | 1.06 | 96.4% | $0.0465 |
| D=5 | gpt-6.1-terra-medium | 0/3 | 18/24 (75.0%) | no clear | no clear | $0.0271 | 0.0 (0i+0b) | 0/0 | 1.00 | 97.2% | $0.4874 |
| D=5 | gpt-6.2-sol-medium | 1/2 | 15/16 (93.8%) | 2,446 | $0.1231 | $0.0656 | 0.5 (1i+0b) | 0/0 | 1.00 | 97.6% | $1.3392 |
| D=5 | grok-4.7-medium | 0/2 | 8/16 (50.0%) | no clear | no clear | $0.1182 | 11.3 (11i+6b) | 0/0 | 3.68 | 78.7% | $1.2846 |
| D=5 | nemotron-3.5-super-vl-low | 0/3 | 6/24 (25.0%) | no clear | no clear | $0.0220 | 9.3 (16i+3b) | 0/0 | 2.28 | 78.4% | $0.1317 |
| D=5 | qwen4-max-medium | 0/2 | 14/16 (87.5%) | no clear | no clear | $0.0453 | 2.1 (4i+0b) | 0/0 | 5.97 | 89.2% | $0.8393 |
