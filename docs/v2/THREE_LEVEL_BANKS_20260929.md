# Frozen single-chef evaluation banks

The concrete Easy, Medium, and Hard instances are in
`data/v2/three-level-release-20260929/`. Each bank contains 12 kitchens.
`source-manifest.json` records the exact files needed to regenerate, replay,
and evaluate them. Run
`python3 scripts/freeze_three_level_release_20260929.py verify` to check
the banks, witnesses, and manifest without provider calls.

| Level | Bank | Selection | Wait choices (game seconds) |
| --- | --- | --- | --- |
| Easy | `easy.json` | Native `main` three-component generator, seeds 92000–92011 | 1, 2, 5, 10, 20 |
| Medium | `medium.json` | First 12 certified `gap-medium` kitchens among consecutive seeds 92000–92119; all rejected candidates before the last selection are recorded in `medium-exclusions.json` | 1, 2, 5, 10, 20 |
| Hard | `hard.json` | Exact copy of the existing 12-seed severe bank; seed 90610's pre-model witness failure remains in the original exclusion record | 1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150 |

The witness and, for Medium, the serial lower-bound certificate establish
feasibility and a need for overlapping order work in those selected kitchens.
They do not establish that one level is harder for every model. No Easy or
Medium paid model results are included here. The four metrics and the
full-kitchen efficiency denominator remain as stated in
`FROZEN_THREE_LEVEL_BENCHMARK_20260928.md`.

The first eight-seed severe recovery attempt stopped on HTTP 402 and
contributes no gameplay score. The second recovery attempt has its own
protocol and ledger; its results must be reported separately.
