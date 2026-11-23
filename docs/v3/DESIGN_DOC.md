# Kitchen Rush v3: Full Design Document

> **One-sentence summary:** Kitchen Rush v3 is a tunable-difficulty **native tool-calling & token-efficiency benchmark** where a single AI chef manages an unfamiliar, procedurally generated kitchen under tight physical bottlenecks and deadlines—scaling cleanly from `D=1` (simple serial execution) to `D=5` (parallel 4-burner rush with razor margins).

---

## 1. The Core Vision & Honest Framing

Why do static LLM benchmarks saturate within 6–12 months? Because they are **finite exams** whose questions leak into training data.

**Kitchen Rush v3** tests real-time agentic planning and token efficiency on **fresh procedural kitchens**:
* **Practical native interface:** Instead of toy board moves or huge text menus, the model uses **8 clean JSON function-calling tools** (`collect`, `chop`, `cook`, `collect_cooked`, `plate`, `serve`, `discard`, `wait`) to manage inventory, parallel timers, and unexpected interruptions.
* **The "Infinite Kitchen" (Anti-Memorization):** Every seed generates fresh cook times, fresh burn windows, fresh recipes, shuffled stations (`D >= 3`), and fresh preparation rules (whether *any* cookable ingredient must be `RAW` or `CHOPPED` before hitting a stove). Combined with **fresh private evaluation seeds** (keeping `calibration_bank_v1.json` strictly as a public dev set), memorization fails immediately.
* **The Core Thesis (Token & Dollar Efficiency vs. Complexity):** Small, cheap models should crush expensive frontier models on **dollars per cleared order** when complexity $D$ is low—until rising complexity causes small models to fail full clears or burn excessive reasoning tokens, creating a measurable **efficiency crossover** where stronger models win.

### Honest Framing: The Witness Solver vs. Chess
A sharp question anyone should ask: *If a deterministic Python solver can beat every kitchen, doesn't that invalidate the benchmark?*

* **What the witness solver is (and isn't):** Finding the mathematically *shortest possible* schedule with walking times, burn windows, and hidden arrivals is NP-hard. Our `scheduled_witness` is **not** an optimal solver—it is a **feasibility certificate** ("at least one valid, burn-free schedule beats the deadlines"), like playtesting a puzzle before publishing it.
* **What our audit proved:** Because deadlines are anchored to `scheduled_witness` (`deadline = witness_serve_time + slack`), even a **no-oracle** version of that ~200-line Python scheduler (with future-arrival peeking disabled) clears **100% of accepted `D=1..5` kitchens** in-process.
* **Why the benchmark still measures something real (the Stockfish analogy):** In Chess, Stockfish crushes every human and LLM—yet asking an LLM to play Chess *natively from board state without Stockfish* still exposes real planning limits. Likewise, in Kitchen Rush v3 the LLM has **no Python interpreter**: it must track concurrent timers in working memory, emit valid native tool calls in real time, and spend tokens efficiently. We disclose the no-oracle scheduler openly as our **"par / Stockfish" reference**, rather than claiming no heuristic can solve `D=1..5`.

---

## 2. Plain-English Glossary of Every Term

| Term | Plain-English Meaning | Everyday Analogy |
| :--- | :--- | :--- |
| **Episode / Kitchen / Seed** | One complete game generated deterministically from `(seed, D)`. Same `(seed, D)` = identical kitchen for every model. | One specific dinner service played by every competitor. |
| **Game-seconds (`gs`)** | Simulated time inside the kitchen. **Pauses while the LLM thinks.** Only executed tool actions advance the clock. | A chess clock that stops while you think and ticks only when pieces move—slow internet or GPU latency never skews scores. |
| **Action vs. Turn vs. Batch** | An **action** is one tool call (`collect`, `cook`, etc.). A **turn** is one LLM API call. A **batch** is 1 to 8 actions queued in a single turn. | Saying *"grab patty, chop it, put it on burner 0"* in one breath (1 turn, 3 actions) instead of pausing after every motion (3 turns). |
| **Event-Driven Interrupt** | If an action in a batch hits an unexpected event (**invalid action**, **new hidden order arrives**, **food burns**, or **order expires**), all *subsequent* queued calls in that batch are cancelled and control returns to the chef. | You planned 5 steps ahead, then the ticket printer *dings* with a surprise rush order after step 2—you pause and re-plan. |
| **Full Clear ("Clear")** | Serving **100% of the orders** in a kitchen with zero expirations ($S_e = N_e$). Primary pass/fail outcome. | Finishing the entire dinner service with zero walk-outs. |
| **Order Completion** | Fraction of all requested orders served before their deadlines ($\sum S_e / \sum N_e$). Partial credit shown alongside Full Clears. | Serving 4 out of 5 tables on time = 80% Order Completion. |
| **Witness Solver** | Deterministic offline scheduler run before any LLM sees a seed. Proves every admitted kitchen is solvable with 0 burns and 0 invalids, and anchors deadlines. | A playtester verifying a puzzle is genuinely solvable before publishing it. |
| **Provable Early-Stop** | Terminating an episode the instant it becomes *mathematically impossible* (even with free walking and relaxed capacity) to serve any remaining order on time. | Resigning when checkmate is unavoidable—preserves exact served count while saving wasted API turns. |
| **Prompt Caching / Cache %** | Putting static rules and kitchen spec at the start of the prompt so turns $2 \dots T$ reuse provider KV cache at 75–90% lower input price. | Reading the kitchen manual once at the start of your shift instead of re-reading all 10 pages every turn. |
| **Paired Seed Bootstrap** | Estimating uncertainty (95% CIs) by resampling matched **kitchen pairs** `{(A, B) on seed s}` 4,000 times. | Comparing two chefs on the exact same nights of dinner service so one unusually hectic night doesn't skew the gap. |

---

## 3. How the Kitchen Works (Mechanics, Interface & Simplifications)

### A. The 8 Native Tools (Zero Format Friction)
1. `collect(ingredient)` — Walk to dispenser and pick up 1 `RAW` unit into an empty hand slot (2s + walk).
2. `chop(ingredient)` — Walk to cutting board and chop a held `RAW` ingredient into `CHOPPED` (4s + walk).
3. `cook(ingredient)` — Walk to a free burner and start cooking a held ingredient matching `ingredient_rules.cookable_from` (2s + walk).
4. `collect_cooked(ingredient, burner_index?)` — Walk to a burner with a `READY` item and lift it into an empty hand slot (1s + walk; lifted off heat *on arrival* before handling time).
5. `plate(recipe)` — Walk to plating counter and assemble the recipe from held items (5s + walk). Exact match required; no extra unplated items (already-plated dishes in hand are allowed).
6. `serve(order_id)` — Walk to serving pass and deliver a held plated dish to an `ACTIVE` order (3s + walk).
7. `discard(item)` — Walk to bin and discard a held item or plate (1s + walk).
8. `wait(seconds)` — Advance the kitchen clock by an integer `1..120` game-seconds.

* **Automatic walking:** Every station tool walks automatically (`0.15s` per floor cell); no `move_to` micromanagement.
* **Atomic `wait(seconds)`:** A `wait(t)` runs to completion for the full `t` seconds; any arrival/expiry/burn that happens during the wait cancels *subsequent* queued calls after the wait, not the wait itself. Models must calculate wait durations rather than blindly calling `wait(120)`.
* **Invalid action penalty:** Illegal calls cost **3 game-seconds**, increment `invalid`, and interrupt the rest of that batch.
* **No hidden harness traps:**
  * **One generous, configurable output token ceiling (`DEFAULT_MAX_TOKENS = 32768`, overridable via `--max-tokens`) and `DEFAULT_REQUEST_TIMEOUT_S = 300` (overridable via `--timeout`)** applied uniformly across every model in a campaign so `reasoning_effort`—never silent truncation or premature HTTP timeout—governs thinking length. *(Earlier audits caught Haiku clipping 47 empty turns at `2048`, `gemini-4-neon-medium` clipping 2 opening planning turns at `16384`, and a legitimate `29,039`-token / `257.1s` turn on `gemini-4-neon-medium`; `32768` + `300s` gives `medium` reasoners full headroom, and anyone running `high` reasoning configs can pass `--max-tokens 65536`.)*
  * **Full reasoning-state continuity (`reasoning_details` + `raw_tool_calls`):** In `full_chat`, `OpenRouterToolAdapter` and `_append_full_chat_turn` preserve OpenRouter's `reasoning_details` (e.g. Google's encrypted `thought_signature`, OpenAI/Anthropic reasoning blocks) and raw `tool_calls` verbatim across turns (excluding truncated `finish_reason="length"` turns and filtering sliced call IDs). Restoring `reasoning_details` immediately lifted `gemini-4-neon` from `0%` clears and `~55%` cache hits to `7/7` clears and `71–81%` cache hits.
  * **Scaled action & turn limits (`max_actions = max_turns = max(160, 24 × (D + 1))`):** Gives generous `1.65×+` headroom over the ~95-action `D=5` witness and scales smoothly for `D=6+`, ensuring 1-call/turn models are never cut off early.
  * **Mandatory tool calling (`tool_choice="required"`):** Prevents models from wasting turns emitting plain-text chatter instead of kitchen actions.
  * **Immutable campaign manifest (`manifest.json`):** Stores bank/spec SHA-256 hashes, protocol, history mode, `tool_choice`, and exact planned episodes — refusing to silently reuse stale files on resume and aggregating strictly manifest members (reporting `planned / scored / missing` explicitly).
  * **Crash-safe dynamic per-attempt spend reservations:** Before every HTTP attempt, reserves conservatively against the actual serialized request byte size (with 1.25× input tariff buffer for cache writes) plus `max_tokens` output, persists the reservation to `spend.jsonl`, releases pre-send DNS/connect failures cleanly while retaining conservative uncertain liability on post-send read timeouts, and distinguishes provider-billed turns from estimated fallbacks.
  * **Full audit logging & Cap Alerts:** Every turn records `finish_reason`, `provider_route`, `requested_effort`, `max_tokens`, `empty_turns`, and `truncated_turns`, printing loud `[CAP_HIT]` diagnostics if any limit ever triggers.

### B. Batched Execution with Event-Driven Interrupts

```mermaid
flowchart LR
    A["LLM Turn (1 API call)"] --> B["Emit 1..8 Tool Calls"]
    B --> C["Execute Call i"]
    C --> D{"Interrupt Event?\n(Invalid / New Order /\nBurn / Expiry / End)"}
    D -- "No, more calls queued" --> C
    D -- "Yes (cancel remaining calls)" --> E["Return Fresh State\n+ Last Turn Feedback"]
    D -- "Batch finished cleanly" --> E
    E --> F{"Episode Over?\n(All Done / Early-Stop /\n160 Acts or Turns / Stall)"}
    F -- "No" --> A
    F -- "Yes" --> G["Deterministic Replay\nVerification"]
```

### C. Context & Turn History Policy (Locked on Native `full_chat`)
Every turn keeps the static system prompt byte-identical and grows an append-only native multi-turn conversation (`history_mode="full_chat"`):
1. **Static System Prefix (cached across all turns):** Rules (prioritizing full kitchen clears, with partial credit tracked) + action durations + per-kitchen `ingredient_rules` + station coordinates (~1,350 tokens).
2. **Native Multi-Turn Transcript (`full_chat`, default for LLMs):** Standard OpenAI/Anthropic/Google `user(STATE_JSON) → assistant(tool_calls) → tool(tool_result) → user(next STATE_JSON)` loop, with explicit `cache_control: ephemeral` breakpoints for both Anthropic and Google (yielding **50–59% cache hits on Google Gemini**, **70–81% on Anthropic**, and **97–98% on OpenAI**).
3. **Stateless `last_turn` mode (for 1-step decision models):** Retained cleanly as `history_mode="last_turn"` for stateless 1-step policies (`DecisionModelAdapter` / TypeSafe Jev) that cannot hold a multi-turn chat transcript.

**Why `full_chat` won as the LLM default (controlled 3-way A/B/C on bank v1, pre-`tool_choice="required"`):**
We dropped the arbitrary 5-turn window and tested three non-arbitrary policies head-to-head on matched seeds (`last_turn`, an optional zero-game-time `inspect_history(turns)` tool, and native `full_chat`):
* **Models ignored `inspect_history`:** Haiku called it **1 time in 18 episodes**; `gpt-6.2-sol-low` called it **0 times in 12 episodes**.
* **`full_chat` delivered dramatic gains where planning horizon mattered most, with transparent trade-offs:**
  * **Haiku 5.5 (`low`, `D=3..5`):** D=3 clears rose `0/6 → 2/6` (`79.2%`), total output tokens dropped **~2×** (`144k` vs `283k`), and total spend dropped **20%** (`\$0.138` vs `\$0.173`); at D=4 partial completion was lower (`16/30` vs `22/30`) due in part to 6 empty turns prior to enabling `tool_choice="required"`.
  * **`gpt-6.2-sol-low` (`D=4, D=5`):** D=4 reached **6/6 full clears (100%, 0 invalids, 0 burns)** vs `5/6` (`last_turn`) at slightly higher USD/cleared order (`\$0.0323` vs `\$0.0246`); at D=5 clears quadrupled from **1/6 → 4/6 (94.4%)**, output tokens per cleared order fell **4×** (`1,765 → 435`), and USD per cleared order fell **2.3×** (`\$0.1208 → \$0.0535`) with **97.3–98.1% prompt caching**.

---

## 4. The Tunable Complexity Dial: What Changes from $D = n$ to $D = n + 1$?

One unified parametric generator `generate_kitchen(seed, level=D)` controls all difficulty levels.

### Supported Range (`D = 1..5` Official Ladder, Extensible to `D = 6+`)

| Dial $D$ | Tier Role | Orders ($N$) | Burners ($B$) | Cooked / Burner | Cook Time Range | Burn Window | Initial Active | Hidden Arrivals | Station Layout | Slack $\sigma(D)$ (min pad) | **Error Tolerance** |
| :-: | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :--- | :-: | :-: | :-: |
| **D=1** | Easy / Floor | 2 | 2 | `1.00` | 8–14s | 14–18s | 2 | None | Fixed | **+65%** (16s) | **`15.9s`** |
| **D=2** | Basic Overlap | 3 | 3 | `1.48` | 18–28s | 12–15s | 3 | None | Fixed | **+22%** (9s) | **`9.1s`** |
| **D=3** | Multi-Wave | 4 | 3 | `2.10` | 22–36s | 10–13s | 3 | 1 hidden (+14–22s) | **Shuffled** | **+12%** (6s) | **`5.2s`** |
| **D=4** | **Hard** | **6** | **4** | **`3.00`** | 30–50s | **8–10s** | 4 | 2 hidden (+15–23s) | **Shuffled** | **+4%** (3.5s) | **`2.8s`** |
| **D=5** | **Near-Perfection** | **8** | **4** | **`4.00`** | 34–58s | **6–8s** | 4 | 4 hidden (+14–20s) | **Shuffled** | **+2%** (2.0s) | **`1.2s`** |

*(At **every** level, all 5 cookable ingredients independently randomize `cookable_from ∈ {RAW, CHOPPED}`, `cook_time`, and `burn_window`. `Error Tolerance` is the mean maximum single-stall delay in seconds that the verified witness can absorb and still full-clear, measured across 40 fresh seeds/level. `D >= 6` remains parametrically extensible in `difficulty_profile` with `cooked/burner >= 4.0 + 0.5*(D-5)` and `max_actions = max(160, 24 × (D + 1))`.)*

### Why We Locked `best_parallel_witness` to `full_burner_witness`-Only (v3.2 Monotonicity Audit)
Two major calibration audits hardened the witness and level ladder:
1. **Bank v1 → v2 (`full_burner_witness`):** Frontier models out-scheduled our original 2-order-throttled witness by 80–95s, so we upgraded to an un-throttled $N$-burner pipelining witness (`full_burner_witness`).
2. **Bank v2 → v3 (Eliminating `D=6` Inversion & Fallback Bimodality):** A 50-seed Monte Carlo audit caught two subtle defects in the earlier 6-level `bank v2`: (a) `best_parallel_witness` fell back to the slower conservative `scheduled_witness` on `40%` of `D=5` seeds and `60%` of `D=6` seeds, creating bimodal "easy-mode" deadlines (`+50–100%` looser); and (b) adding a 5th burner at `D=6` for only 1 extra order actually *dropped* cooked-items-per-burner (`3.00 → 2.80`) and stove utilization (`53% → 43%`).

Removing the `scheduled_witness` fallback (`full_burner_witness` only across 100% of seeds) and consolidating to a **5-level strictly monotonic ladder (`D=1..5`)** with `4` burners at both `D=4` (`6` orders) and `D=5` (`8` orders) makes every structural dial, stove utilization metric, error tolerance, and deterministic baseline strictly monotonic (enforced in CI by `test_difficulty_ladder_monotonicity`):

| Bank v3 (`D=1..5`, 10 fresh seeds/level) | Witness Actions | Sequential Baseline | GreedyOverlap Baseline |
| :-: | :-: | :---: | :---: |
| **D=1** | 16.0 | 100.0% (`10/10` clears) | 100.0% (`10/10` clears) |
| **D=2** | 27.2 | 43.3% (`0/10` clears) | 86.7% (`6/10` clears) |
| **D=3** | 38.5 | 37.5% (`0/10` clears) | 57.5% (`1/10` clears) |
| **D=4 (Hard)** | 71.7 | 23.3% (`0/10` clears) | 11.7% (`0/10` clears) |
| **D=5 (Near-Perfection)** | 94.9 | 16.2% (`0/10` clears) | 5.0% (`0/10` clears) |

### Plain-English Analogies: What Each Step $D \to D+1$ Feels Like
* **$D = 1$ ("Sunday dinner for two"):** 2 orders, 1 cooked item each, short cooks (8–14s), +65% slack (`~15.9s` error tolerance). Serial execution clears 100% of kitchens. *Tests basic native tool calling and randomized `cookable_from` rules.*
* **$D = 1 \to 2$ ("Three guests eating together"):** 3 orders across 3 burners, cook times double (18–28s), slack drops to +22% (`~9.1s` tolerance). Serial cooking fails full clears. *Forces basic parallel cooking.*
* **$D = 2 \to 3$ ("Friday lunch in a brand-new kitchen"):** 4 orders on 3 burners, **all station locations shuffle**, burn windows shrink (10–13s), slack drops to +12% (`~5.2s` tolerance), and **Order 4 arrives hidden mid-episode**. *Tests dynamic replanning under burner contention.*
* **$D = 3 \to 4$ ("Hard: Peak 4-burner rush"):** 6 orders (12 cooked items across 4 burners, `3.0 c/b`), 3–4 ingredients/dish, 2 hidden arrivals, 8–10s burn windows, +4% slack (`~2.8s` tolerance). *Breaks low-reasoning and mid-tier models (`GreedyOverlap = 11.7%`, `0/10` clears).*
* **$D = 4 \to 5$ ("Near-Perfection: Sustained 8-order marathon"):** 8 orders (16 cooked items crammed onto 4 burners, `4.0 c/b`, ~95 actions), 4 hidden arrivals, razor 6–8s burn windows, and only **+2% slack (`~1.2s` tolerance)**. *A single burn, invalid call, or scheduling slip breaks the full clear.*

### Exact Deadline Calibration Math
1. Build the draft kitchen with unconstrained deadlines (`3000s`).
2. Run `best_parallel_witness` on the draft kitchen and record the game-second $t_i^*$ when the witness initiates `serve` for each order $O_i$.
3. Compute active elapsed time since arrival $a_i$:
   $$\Delta_i = \max(10.0,\; t_i^* - a_i)$$
4. Set order $O_i$'s deadline with level slack $\sigma(D)$ and minimum pad $p_{\min}(D)$:
   $$\text{deadline}_i = \left\lceil t_i^* + \max\big(p_{\min}(D),\; \Delta_i \cdot \sigma(D)\big) \right\rceil$$
5. Replay the witness on the finalized kitchen and **reject the seed** unless it achieves `100% served`, `0 invalids`, and `0 burns` (bounded by `max_attempts` so bank building always terminates cleanly).

*Worked Example:* At $D=3$ ($\sigma = 0.14$, $p_{\min} = 7.0$), if `O2` arrives at $a_2 = 0$ and the witness starts serving at $t_2^* = 80.0\text{s}$:
$$\Delta_2 = 80.0\text{s}, \quad \text{pad} = \max(7.0,\; 80.0 \times 0.14) = 11.2\text{s}, \quad \text{deadline}_2 = \lceil 80.0 + 11.2 \rceil = 92\text{s}.$$

---

## 5. Scoring Metrics: Full-Clear Gated Efficiency + Partial Credit

For each `(model, level)` cell over scored episodes $e = 1, \dots, K$, let:
* $S_e$ = orders served before deadline; $N_e$ = orders requested
* $\text{Clear}_e = \mathbb{1}[S_e = N_e]$ (1 if the entire kitchen was cleared, else 0)
* $N_{\text{clear}, e} = N_e \cdot \text{Clear}_e$ (orders in fully cleared kitchens)
* $T_{\text{out}, e}$ = output tokens (including reasoning tokens)
* $C_e$ = actual provider-settled USD (directly from OpenRouter `usage.cost`, including real prompt cache reads/writes)
* $I_e$ = invalid tool calls; $B_e$ = burned items; $A_e$ = executed tool actions

| # | Metric | Formula | Direction | What It Measures |
| :-: | :--- | :---: | :-: | :--- |
| **1** | **Full Clears** *(+ Order Completion)* | $\dfrac{\sum_e \text{Clear}_e}{K}$ &nbsp;and&nbsp; $\dfrac{\sum_e S_e}{\sum_e N_e}$ | Higher | Primary pass/fail capability + partial progress within kitchens. |
| **2** | **Out Tok / Cleared Ord** | $\dfrac{\sum_e T_{\text{out}, e}}{\sum_e N_{\text{clear}, e}}$ | Lower | Total reasoning/output tokens spent across **all** attempts per order in a fully cleared kitchen (`no clear` if 0 clears). |
| **3** | **USD / Cleared Ord** | $\dfrac{\sum_e C_e}{\sum_e N_{\text{clear}, e}}$ | Lower | True economic cost across **all** attempts per order in a fully cleared kitchen (`no clear` if 0 clears). *(Secondary `USD / Served Ord` also shown.)* |
| **4** | **Reliability** | $\dfrac{100 \sum_e (I_e + B_e)}{\sum_e A_e}$ + Empty / Trunc turns | Lower | Invalid calls and burns per 100 actions, plus explicit empty-turn and token-cap truncation counts. |

**Why Full-Clear Gating matters (and why we dropped speculative D5 hacks):**
* If you divide dollars by *individually served orders*, a model that spends \$0.01 and cherry-picks 1 of 6 orders looks cheaper than a model that spends \$0.12 and clears all 6.
* Pooling **all** episode spend in the numerator and counting **cleared-kitchen orders** in the denominator fixes that completely—and naturally drives failed high-complexity cells to `0/6 clears` (`no clear`) while keeping Order Completion right next to it for partial progress.
* **Why we rejected speculative D5 "shared wave deadlines" and "two-strike" hacks:** Independent replay checks proved fixed wave targets like 105s are physically impossible on some D5 recipes (hard lower bound 113s on seed 3001), while two-strike rules distorted rankings on existing traces (penalizing models that recovered after early slips). Keeping pure kitchen physics + Full-Clear Gating is simpler, fairer, and free of arbitrary rule tweaks.

**Worked Example:** Two kitchens at $D=3$ ($N=4$ orders each):
* Kitchen 1: 4/4 served (**Full Clear**), 1,200 output tokens, \$0.04, 0 invalids, 0 burns, 36 actions.
* Kitchen 2: 3/4 served (*not a clear*), 1,600 output tokens, \$0.06, 1 invalid, 1 burn, 44 actions.
* **Full Clears:** `1/2` (50.0%) | **Order Completion:** `(4 + 3) / 8 = 87.5%`.
* **Out Tok / Cleared Ord:** `(1200 + 1600) / 4 = 700` tok/cleared-order.
* **USD / Cleared Ord:** `($0.04 + $0.06) / 4 = $0.0250` / cleared-order *(secondary USD / Served Ord = `\$0.10 / 7 = \$0.0143`)*.
* **Reliability:** `100 × (1 + 1) / (36 + 44) = 2.5` inv+burns per 100 actions (`0` empty turns, `0` truncated turns).
* **Uncertainty:** Single-model Clear Rate uses a **two-sided 95% Wilson score interval** (valid even at `0/6 → [0%, 39%]` and `6/6 → [61%, 100%]`); model-vs-model differences use **4,000-draw paired-seed bootstrap CIs** on shared kitchens.

---

## 6. Empirical Validation Evidence (Calibration v1 + v2 + History A/B)

### A. Reasoning-Effort & Tier Crossover (`runs/v3/calibration_v2/`, `max_tokens=16384`, `last_turn`, 90/90 scored, \$5.48, 0 cap hits)

| Level | `claude-haiku-5.5-low` | `gpt-6.2-sol-low` | `gpt-6.2-sol-medium` | Winner on `USD / Cleared Ord` |
| :-: | :--- | :--- | :--- | :--- |
| **D=1** | **6/6 (\$0.0010)** | 6/6 (\$0.0100) | 6/6 (\$0.0122) | Small (`haiku`, 10× cheaper) |
| **D=2** | **2/6 (\$0.0042)** | 6/6 (\$0.0149) | 6/6 (\$0.0215) | Small (`haiku`, 3.5× cheaper) |
| **D=3** | 0/6 (`no clear`) | **4/6 (\$0.0229)** | 5/6 (\$0.0259) | Frontier `low` (`6.2-sol-low`) |
| **D=4** | 0/6 (`no clear`) | **5/6 (\$0.0246)** | 5/6 (\$0.0359) | Frontier `low` (`6.2-sol-low`, 4× fewer out tok) |
| **D=5** | 0/6 (`no clear`) | 1/6 (\$0.1208) | **2/6 (\$0.1025)** | **Frontier `medium` flips cheaper than `low`!** |

*(Protocol note: Section 6A reflects the earlier `bank v1` + `last_turn` + `tool_choice="auto"` pilot; across all 30 episodes of `gpt-6.2-sol-medium`, it made **0 invalid actions** and **0 empty/truncated turns**. Note that the `2/6` vs `1/6` D=5 crossover rests on 6 seeds and is sensitive to seed 3000 — confirmatory low vs. medium comparisons belong on a larger held-out private bank under the locked `full_chat` + `tool_choice="required"` protocol.)*

### B. 3-Way History Mode Comparison (`D=4..5` on `gpt-6.2-sol-low`, 6 matched seeds, 0 cap hits)

| Mode (`gpt-6.2-sol-low`) | D=4 Clears (Comp) | D=5 Clears (Comp) | D=5 Out Tok / Cleared Ord | D=5 USD / Cleared Ord | Cache % |
| :--- | :---: | :---: | ---: | ---: | :-: |
| `last_turn` *(1 turn)* | 5/6 (96.7%) | 1/6 (83.3%) | 1,765 | \$0.1208 | 72.1% |
| `on_demand` *(`inspect_history`)* | 3/6 (90.0%) | 1/6 (83.3%) | 2,056 | \$0.1320 | 71.6% |
| **`full_chat` *(locked default)*** | **6/6 (100.0%)** | **4/6 (94.4%)** | **435** | **\$0.0535** | **98.1%** |

**Head-to-head frontier check (`full_chat`, `google/gemini-4-neon (low)` vs. `openai/gpt-6.2-sol (low)` on old bank v1):**
* **D=4 (6 seeds):** Tied `6/6` (`100%`) clears — `gpt-6.2-sol-low` (**\$0.0323**/cleared ord, `1.00` avg batch, `97.3%` cache) vs. `gemini-4-neon-low` (**\$0.0366**/cleared ord, **`6.71` avg batch**, `50.4%` cache).
* **D=5 (same 4 seeds `3000–3003`):** Tied `3/4` (`95.8%`) clears — `gpt-6.2-sol-low` (**\$0.0493**/cleared ord) vs. `gemini-4-neon-low` (**\$0.0736**/cleared ord, `5.16` avg batch, `59.4%` cache).
* That `75%` clear rate at `low` reasoning on bank v1 directly motivated our **full-burner witness upgrade (`bank v2`)**, which compressed `D=4..5` makespans by ~20% and unlocked `D=6`.

### C. Reasoning-Continuity Fix & Live Verification of Strictly Monotonic `Bank v3` (`v3.2`)

1. **`reasoning_details` preservation fix (verified on `bank v2`):** Earlier `gemini-4-neon` runs stripped OpenRouter's `reasoning_details` (`google-gemini-v1` encrypted `thought_signature`), cutting cache hits to `48–61%` and breaking multi-turn state continuity. Echoing `reasoning_details` + `raw_tool_calls` verbatim in `full_chat` immediately restored `71–81%` cache hits, dropped mistakes to `0`, and lifted `gemini-4-neon-medium` to `7/7` clears on `D=6 seed=6001` (`92.9%` completion, `7.10` avg batch).
2. **Live `D=4` (Hard) vs `D=5` (Near-Perfection) validation on `calibration_bank_v3.json` (`runs/v3/bank_v3_d4_d5_val/`, `12/12` scored, `\$4.12` total, `0` truncations, `0` empty turns, `0` cap hits, `555/555` provider-billed turns):**

| Level | Model | Clears | Order Completion | Out Tok / Cleared Ord | USD / Cleared Ord | USD / Served Ord | Inv+Burn / 100 Acts | Cache % | Spend |
| :-: | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **D=4 (Hard)** | `gemini-4-neon-low` | **0/3** | 14/18 (`77.8%`) | no clear | no clear | `\$0.0585` | `0.0` (0i+0b) | `66.6%` | `\$0.82` |
| **D=4 (Hard)** | `gpt-6.2-sol-medium` | **3/3** | 18/18 (`100.0%`) | `1,063` | `\$0.0522` | `\$0.0522` | `0.5` (0i+1b) | `97.0%` | `\$0.94` |
| **D=5 (Near-Perfection)** | `gemini-4-neon-low` | **0/3** | 17/24 (`70.8%`) | no clear | no clear | `\$0.0718` | `2.1` (1i+4b) | `81.9%` | `\$1.22` |
| **D=5 (Near-Perfection)** | `gpt-6.2-sol-medium` | **1/3** | 20/24 (`83.3%`) | `2,428` | `\$0.1425` | `\$0.0570` | `1.1` (1i+2b) | `98.4%` | `\$1.14` |

**What this proves:**
* **Strict monotonicity with zero inversions:** Both models step down cleanly from `D=4 → D=5` across clears, order completion, mistake rate, and cost per dish (`gpt-6.2-sol-medium`: `100% → 33%` clears, `\$0.0522 → \$0.1425`/cleared ord).
* **Two genuinely hard top rungs, no saturation:** `D=4` (`6` orders / `4` burners, `2.8s` tolerance) already breaks low-reasoning models (`0/3`), while `D=5` (`8` orders / `4` burners, `1.2s` tolerance, `6–8s` burn window) demands spotless execution (`8/8` clear achieved with 0 mistakes on `seed=8004`, while 1–2 slips failed `8000` and `8005`).
* **Ready for official leaderboard:** Generate a fresh private held-out seed bank (`D=1..5`, `n = 20` seeds/level, outside Git) and run the locked model roster under the unified `v3.2` protocol (`full_chat`, `tool_choice="required"`, `reasoning_details` preserved, `DEFAULT_MAX_TOKENS=32768`, `DEFAULT_REQUEST_TIMEOUT_S=300`, `full_burner`-only witness, Full-Clear Gated metrics).

---

## 7. Modular Architecture & Visual Replay Hook

All v3 code lives cleanly in 6 compact modules inside `src/kitchenrush/v3/`:
* [`env.py`](file:///Users/bassime/Desktop/fullstack/kitchen-rush/.worktrees/v3-unified/src/kitchenrush/v3/env.py) — `UnifiedEngine`, `difficulty_profile(D)`, `generate_kitchen(seed, D)`, 8 native `TOOL_SCHEMAS`, `static_context`, `dynamic_state` (`last_turn`), `verify_replay`.
* [`solvers.py`](file:///Users/bassime/Desktop/fullstack/kitchen-rush/.worktrees/v3-unified/src/kitchenrush/v3/solvers.py) — `run_sequential_solver`, `run_greedy_overlap_solver`, `scheduled_witness`, and `best_parallel_witness` (with `allow_arrival_peek=False` no-oracle reference mode).
* [`adapters.py`](file:///Users/bassime/Desktop/fullstack/kitchen-rush/.worktrees/v3-unified/src/kitchenrush/v3/adapters.py) — `AgentAdapter`, `OpenRouterToolAdapter` (uniform configurable `DEFAULT_MAX_TOKENS=32768`, explicit Anthropic/Google caching, dynamic payload spend reservations, route/finish-reason capture), and `DecisionModelAdapter` (1-step base class for future TypeSafe / Jev integration).
* [`metrics.py`](file:///Users/bassime/Desktop/fullstack/kitchen-rush/.worktrees/v3-unified/src/kitchenrush/v3/metrics.py) — Full-clear gated primary metrics + partial credit + secondary per-served-order ratios + Wilson 95% single-model CIs + 4,000-draw paired-seed bootstrap CIs.
* [`runner.py`](file:///Users/bassime/Desktop/fullstack/kitchen-rush/.worktrees/v3-unified/src/kitchenrush/v3/runner.py) — Crash-safe persistent `SpendLedger`, immutable `manifest.json` validation, scaled `max_turns = max_actions`, resumable seed-wave execution, mandatory `verify_replay`.
* [`replay.py`](file:///Users/bassime/Desktop/fullstack/kitchen-rush/.worktrees/v3-unified/src/kitchenrush/v3/replay.py) — Zero-cost exporter converting any v3 episode JSON into the existing pixel-art [ui/](file:///Users/bassime/Desktop/fullstack/kitchen-rush/ui/README.md) viewer format (supporting 1–4 model synchronized **Race Mode** duels).
