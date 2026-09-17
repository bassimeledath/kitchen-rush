# Checkpoint 3 — full-roster plan (not started)

**Every existing configuration plus Jev remains in scope.** The roster contains 32 labels,
30 distinct configurations after two documented aliases. Preserve thinking/off variants.
Twenty-nine currently have price metadata; PhoneLLM needs attributable compute costs.

| Work | Coverage | Maximum liability |
|---|---|---:|
| Integration canaries | One development episode per priced configuration, $0.40 cap | $11.60 |
| Primary dollar grid | All 29 × 3 fresh kitchens × $0.05/$0.15/$0.40 × one trial | $52.20 |
| Remaining main-stage capacity | From the original $100 main ceiling | At least $36.20 |
| Fresh confirmation | Original separate ceiling; scope frozen before dispatch | $30.00 |
| Recovery | Original separate reserve | $20.00 |

Including PhoneLLM makes canaries plus the primary grid **$66 maximum**, once its billing
is resolved. These are caps, not expected invoices. Earlier checkpoint underspend is not
automatically reassigned. The $200 overall ceiling remains unchanged.

Before any main run, freeze each configuration's provider, price, reasoning settings,
output cap, token-accounting eligibility and shared prompt. A metadata preflight is not a
successful API canary. Resolve blocked configurations explicitly; never silently omit them.
The pilot does not authorize the main run.

Run the primary grid in balanced blocks, using identical kitchens across configurations
and allowances. Reserve spend before every request, stop on transport/accounting failures,
and audit complete blocks before continuing. This guarantees a small common dollar grid
within the main ceiling for every priced configuration, assuming its canary passes.

**Extra coverage is conditional.** A second complete dollar-grid trial costs at most another
$52.20; canaries plus two trials total $116, exceeding the $100 main ceiling. The complete
token grid for 28 potentially eligible configurations, three kitchens and three allowances
with the pilot's $1 backstop has a separate maximum of **$252**. Neither fits by assumption.
Use measured pilot and full-roster canary costs to choose and freeze affordable balanced
extensions before dispatch. Report missing/cost-censored token cells explicitly; Jev's B80
remains N/A. If universal B80 coverage is a release requirement, hold the main launch until
an affordable design or revised allocation is agreed.

Three kitchens (one per family) support a coverage check, not a stable ranking. Prefer more
independent kitchens over repeated trials on the same three when extending the main sample.
Select confirmation scope by a declared rule, then evaluate fresh confirmation kitchens;
do not use them to tune prompts or replace inconvenient pilot results.

The release also includes the README rewrite, all replacement graphs, generated-card replays,
versioned results and reproduction instructions described in [README_OUTLINE.md](README_OUTLINE.md).
No legacy graph or score is relabeled v2.

## Pilot-informed target and allocation to review

The audited dollar pilot cost $4.3861 for 54 episodes ($0.08122/episode). The separate token
pilot cost $3.9235 for 18 episodes ($0.21797/episode). Applying those control-mix averages
gives the following **scenario, not a forecast or spending guarantee**. The full roster has
different prices and behavior; its canaries must replace these assumptions before dispatch.

| Target | Episodes | Control-mix cost scenario |
|---|---:|---:|
| 29 configurations × 9 fresh kitchens × 3 dollar allowances | 783 | $63.60 |
| 28 potentially eligible configurations × 3 kitchens × 3 token allowances | 252 | $54.93 |
| Full-roster canaries (maximum, not extrapolated) | 29 | $11.60 |
| Main target total | 1,064 | $130.13 |

Nine independent kitchens (three per family) are the preferred primary target. Add them in
matched three-kitchen rounds; the smaller grid above is the minimum affordable coverage.
The token sample remains exploratory unless confirmation expands it. Never present a
partially collected grid as complete.

**Proposed reallocation, not applied:** checkpoints 1–2 accounted for $10.5326, leaving
$189.4674 of the original $200. Moving their unused allocation to main would permit a
**$139.46 main ceiling**, while preserving $30 confirmation and $20 recovery. Reassess the
scenario after fixing or explicitly accepting the reservation policy; changes can affect
episode length and spending. This is a concrete checkpoint-3 budget decision.

A bounded confirmation option costs at most $28.80: three selected configurations × six
fresh confirmation kitchens × all three dollar allowances ($10.80), plus one eligible
configuration × six kitchens × all three token allowances with a $1 backstop ($18).
Freeze the selection rule before main results: take the highest-quality configuration at
each dollar allowance, fill duplicate selections by average quality over allowances, and
break ties by lower mean cost then label. For token confirmation, select the smallest B80,
breaking ties by higher quality there, then lower cost, then label; if none reaches B80,
select the highest quality at 8,192. Use only uncensored, eligible token cells; if none
exist, report token confirmation unavailable. Confirm without retuning; report all outcomes.

**Launch gate:** review conservative reservation-induced stopping; resolve PhoneLLM pricing
and per-route compatibility; freeze coverage and allocation. The pilot passed implementation
checks, but it is not an unconditional approval for a publication sweep.
