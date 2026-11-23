# Kitchen Rush: three-level single-chef benchmark

**Decision, 2026-09-28.** Use the existing single-chef Kitchen Rush game. The benchmark asks: **Which model completes demanding kitchens most reliably for the least generation and dollar cost?** Planning, inventory management, and execution all count. Stop work on miniature puzzles, shared-batch variants, note interfaces, and extra-chef mechanics for this benchmark. Those remain archived experiments, not task families.

**Replication status, 2026-09-29:** All 24 assigned episodes were attempted under the separate $50 cap. Eleven reached scored terminal states and thirteen were unscored after provider transport/HTTP errors; only four matched pairs scored. See `runs/v2/severe-replication-20260928/REPORT.md`. This operational shortfall does not change the three-level task-family decision.

| Level | Frozen family | What changes |
| --- | --- | --- |
| Easy | Native `environment.generate(seed, "main", 3)` | Original three-order kitchens with conservative cumulative deadlines. |
| Medium | Existing `overlap_gap_v2.py` `gap-medium` family | Three orders, two burners, 50–70 game-second cooking, 10–15-second burn windows, and a certified deadline gap excluding one-order-at-a-time completion for the selected kitchens. |
| Hard | Existing `extreme_sol_kitchens_20260926.py` `severe` arm | Six orders, six burners, four hand slots, three cooked ingredients and one chopped side per order, 50–150-second unequal cooking, 18-second burn windows, individual deadlines, eight game seconds of margin over a replayed witness. |

All levels use one chef, the native station/inventory/action semantics, one action choice per model call, and no game-time charge for inference. The hard family's longer timer values use the existing `VariantEngine` timer-range override; the action types and physical behavior are the same. Wait durations are level-specific candidate values. A replayed witness proves each retained kitchen is solvable; it does not prove optimality or a minimum planning depth.

## Four frozen metrics

For each model configuration and level, over **scored terminal episodes**, let `S` be served orders, `N` requested orders, `T` generation tokens including reasoning, `P` frozen list-price dollars for all input and output tokens, `I` invalid decisions, and `A` total decisions. Let `F = N` only if the whole kitchen is completed, otherwise `F = 0`.

| Metric | Pooled definition | Direction |
| --- | --- | --- |
| Generation tokens per finished order | `sum(T) / sum(F)` | Lower |
| Frozen USD per finished order | `sum(P) / sum(F)` | Lower |
| Order completion | `sum(S) / sum(N)` | Higher |
| Invalid decision rate | `sum(I) / sum(A)` | Lower |

The first two numerators include **every scored attempt**, including partial kitchens. If `sum(F) = 0`, show **no kitchen completed** rather than zero or infinity. Show full-kitchen clears, scored counts, and unscored counts alongside every cell. Provider failures, transport errors, prompt ceilings, wall stops, and spending stops are unscored and reported separately. A native terminal episode or the native 160-decision limit is scored. Frozen tariffs are fixed within a model configuration for comparability; actual OpenRouter billing is tracked separately. Different tokenizers limit cross-provider interpretation of token counts.

Display model configurations as rows and **Easy → Medium → Hard** as columns, with a toggle for the four metrics and cost per finished order as the default. This is an ordered task-family label, not a scalar measure of intelligence. Kitchen-level paired comparisons and uncertainty matter more than per-order independence.

## Fixed hard-level replication

The next paid measurement is **exactly 12 fresh, preselected hard kitchens × GPT-6 Sol low/high = 24 planned episodes**. Each episode starts from the beginning. The paired seeds are `90603, 90604, 90605, 90606, 90607, 90608, 90609, 90611, 90612, 90613, 90614, 90615`. The builder considered consecutive seeds `90603` through `90615` and retained the first 12 with a clean replayed witness. Seed `90610` was excluded **before any model calls** because the existing witness burned a broth item. That exclusion and all 12 retained specs/actions are saved under `data/v2/severe-replication-20260928/`.

Both reasoning settings use the same pinned `openai/gpt-6-sol` OpenAI route, streamed OpenRouter transport, maximum 32,768 output tokens, frozen model tariff, native 160-decision limit, and **$3.25 per-episode operational fuse**. The 12 pairs run in seed order, low then high. A separate atomic ledger enforces the newly approved *total* campaign ceiling; the older nearly exhausted ledger and stopped process remain untouched. A 60-minute between-requests episode wall fuse and eight-hour campaign wall fuse prevent unbounded work. No episode is replaced or rerun based on its outcome. If a fuse or provider failure leaves missing or unscored data, report it as such; retain the fixed sample and the complete accounting trail.

The severe seed `90602` result (low 5/6, high 6/6) is exploratory development evidence. The other observed 3/6 versus 5/6 pair was the **brutal** arm, with different timer pressure, so it is not a second severe replication. Twelve new pairs are a pilot: report them regardless of who wins and do not change the hard task family in response. Then run a broader roster using the frozen families, with new level-specific seed banks fixed before model output.

## Source and archived evidence

- Native generator and scoring: `src/kitchenrush/v2/environment.py`, `scripts/overlap_gap_v2.py`, `scripts/extreme_sol_kitchens_20260926.py`.
- Frozen hard specs and feasible witnesses: `data/v2/severe-replication-20260928/kitchens.json`.
- Fixed hard runner: `scripts/run_severe_replication_20260928.py`.
- Development evidence: `docs/v2/EXTREME_SOL_RESULTS_20260926.md` and `runs/v2/extreme-sol-reasoning-r2-20260926/`.
- Prior complexity matrix proposal: `docs/v2/COMPLEXITY_MATRIX_PROPOSAL.md` is superseded by this three-level decision where the protocols differ.
