> **Historical checkpoint document — superseded for release.** The choice-1 pilot exposed engine and budget-admission defects in independent review. Its results are diagnostic only. The current contract is [PROTOCOL.md](PROTOCOL.md); the full remodel and roster rerun are authorized.

# README rebuild and graph specifications

The release README is written from scratch after checkpoint 3. No v1 graph is relabeled v2.

1. **Opening:** one sentence on useful actions under resource limits; a fresh v2 replay.
2. **Useful work:** valid actions complete orders; small worked example of the quality formula.
3. **Limited budgets:** common dollar view, generation-token view where eligible, B80, accounting.
4. **Unfamiliar kitchens:** generated ingredient/state cards, structural holdouts, and honest limits.
5. **Results:** full model roster with configurations and uncertainty. Pilot results labeled pilot.
6. **Reproduce:** frozen manifest, CLI, source/version hashes, replay verification, archived v1 link.
7. **Positioning:** bounded objective environment for sequential action selection. It does not test
   unconstrained argument generation, open-world tool discovery, or inference-provider speed.

## Publication graphs

- **Quality vs dollar allowance:** x=episode USD allowance (log), y=mean Q [0,100]. One line per
  fixed configuration, 95% seed-cluster intervals, matched task instances. Budget selector/table
  ranks models at that allowance; never combine incompatible protocols.
- **Cost–quality frontier:** x=actual mean episode API cost (log), y=Q. Points identify both model
  and budget; connect a model's operating points. Dominance is economic, not equal compute.
  No latency axis, sizing, filtering, or ranking. Include accessible source table and price date.
- **Quality vs generation allowance:** x=tested generated-token allowance (log), y=Q. Only eligible
  models; show 80 line and tested B80 crossing. Explain N/A and not-reached distinctly; suppress
  dollar-censored B80 cells. Include a conservative lower-bound crossing in the table.
- **Generalization check:** completion/invalid outcomes for recipe-reader and fixed-lookup
  baselines across families; explicitly a shortcut diagnostic, not a contamination guarantee.

Figures, tables and README numbers derive from the same verified result JSON. Check axes,
units, zero-cost handling, confidence intervals, budget/configuration labels, and source hashes.
The pilot may supply draft graphs for visual verification; release graphs await the full roster.
The release also updates package descriptions, CLI help and linked methodology/limitations
pages so old latency framing does not survive outside the README. The legacy CLI remains v1
during these checkpoints; use the explicit v2 runner for the new protocol.

## Scope and roster

The machine-readable roster is data/v2/roster.json. It preserves the union of current canonical
boards, aliases, and prior blocked/diagnostic entrants plus Jev. Aliases are reconciled explicitly
before counting configurations. A three-model pilot never replaces that full roster. Resolve
unavailable endpoints, reasoning compatibility and cost attribution before the main sweep.
