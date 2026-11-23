# Three-level benchmark and frontier pilot

This branch freezes the single-chef Easy, Medium, and Hard kitchen banks and publishes the current GPT-6.1 Sol / Sonnet 5.5 pilot summaries. It includes the generators, runner, audit and reporting scripts, source manifests, bank data, and the rule text used by the pilot.

- [Frozen benchmark](THREE_LEVEL_BANKS_20260929.md)
- [Pilot results](NEW_FRONTIER_FOUR_RESULTS_20260929.md) and [intervals](NEW_FRONTIER_FOUR_RESULTS_WITH_CI_20260929.md)
- [Independent result review](NEW_FRONTIER_FOUR_INDEPENDENT_REVIEW_20260929.md)
- [Cost review](NEW_FRONTIER_FOUR_COST_REVIEW_20260929.md)

The original campaign remains paused. It attempted 81 of 144 assigned episodes; 74 scored and seven were unscored. The published result tables are incomplete and must not be used as a full model ranking. In particular, there are no scored GPT-6.1 Sol Medium or Hard episodes in this pilot. The frozen dollar metric uses normalized list prices; the cost review separately reports provider charges and the GPT cache-write price difference.

The raw request and action traces, local spend ledger, and credentials are not on this branch. The run-level source manifests are included for reproduction; the audit summary and reports preserve aggregate results. The recovery runner requires the original local run artifacts and does not initiate calls unless explicitly invoked.

Offline verification:

```sh
PYTHONPATH=src:scripts python3 scripts/run_new_frontier_four_20260929.py dry-run
```
