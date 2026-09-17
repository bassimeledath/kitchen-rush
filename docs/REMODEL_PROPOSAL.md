# Kitchen Rush v2 — changes to review

**Useful work. Limited budgets. Unfamiliar kitchens.**

- **One shared leaderboard:** rerun the full roster, including Jev, on identical public states and candidate actions. Select one action per request, including waits and illegal choices. No adapter planners or repairs.
- **Quality:** `100 × max(0, (completed orders − 0.1 × invalid actions) / 3)`, averaged equally across kitchen-size families. Report completion and invalid calls alongside it.
- **Dollar view:** admit actions while cumulative actual charges fit the allowance. Pay and archive the first over-budget response, but do not execute it. Graph quality against actual total cost, including that boundary call.
- **Token view:** separate generation-token allowances, including reasoning once. B80 is the smallest tested uncensored allowance reaching quality 80; also show the lower-confidence-bound version. Jev participates in the dollar view; its B80 is N/A.
- **Kitchen clock:** inference pauses it. Actions and waits can still burn food or expire orders. Provider latency does not affect rankings.
- **Unfamiliar kitchens:** generated recipe cards plus changing cooking times, burn windows and mushroom preparation requirements. Same verbs and animation; no recipe graph machinery. Hold out recipe/rule combinations and test fixed-script baselines. No memorization-proof claim.
- **Presentation:** rebuild the README, graphs and replay around these three ideas. Replace legacy rankings with fresh evidence. Keep Kitchen Rush as the working name.

**Three checkpoints:** verify engine/accounting offline → audit revised controls and roster canaries → run matched dollar/token sweeps, repeat trials, confirm on fresh kitchens, then finish the presentation. Total spending stays under the $200 operational ceiling, including prior accounted costs. Token coverage comes before optional additional dollar-only depth. Unsupported configurations remain explicitly listed.

Earlier campaigns remain diagnostic: reviews found engine/admission defects, then per-call truncation and reservation gaps. R2 uses a shared 16,384-token call safeguard and all applicable tariff tiers. [Exact contract](v2/PROTOCOL.md).
