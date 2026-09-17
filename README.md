<img src="assets/kitchen-rush-logo.png" alt="Kitchen Rush" width="420">

**How much useful work can a model complete with a limited allowance?**

Kitchen Rush is a reproducible kitchen task for evaluating model action selection.
Give models the same orders, rules and possible actions. Limit their resources. Measure
what gets completed and what it costs.

**V2 is being validated.** A roster canary exposed an overly restrictive per-request token
ceiling and incomplete operational tariff reservations. Both are corrected; the revised
configuration has passed its six-configuration canary: 150 requests independently audited.
The 29-configuration checkpoint has started but paused after an OpenRouter funding rejection;
its unfinished runs are unranked. Earlier runs remain diagnostic,
with every charge retained in the $200 total. [V1 results are archived](README.v1.md).

## Three ideas

1. **Useful work.** Complete requested orders through valid actions. Wrong choices cost
   game time and reduce quality. All models choose from the same candidates, including
   illegal actions and explicit waits. One action executes before the next observation.
2. **Limited budgets.** Compare quality at dollar allowances across model architectures.
   Separately measure quality under generation-token allowances where accounting is
   comparable. Reasoning, actions and recovery consume the episode's resources.
3. **Unfamiliar kitchens.** Recipes, preparation requirements and cooking timers vary.
   Stable operations must be applied to new combinations. Read the rules; a fixed recipe
   script is insufficient. The generator remains learnable and is not memorization-proof.

Inference pauses the kitchen. Actions and waits still advance cooking, burning and order
expiry. API/network speed does not affect scores or public charts.

## One task, two resource views

Jev and generative models share the action-selection task. Jev returns a typed choice;
generative models return the selected action as JSON. Neither receives a planner, legality
filter, automatic repair or hidden future orders. Shared automatic walking is disclosed
assistance. This evaluates action selection, not unconstrained tool-argument generation.

**Dollar view:** execute a selected action only if cumulative actual inference charges fit
the episode allowance. The first response crossing it is paid and archived, but not executed.
Actual cost therefore includes a possible extra boundary request. Compare quality against
known costs across every attempt of a scheduled job. Show unresolved charges separately;
only resolved-cost points enter the empirical Pareto frontier. Equal dollars do not mean equal compute.

**Token view:** enforce an episode allowance of generated tokens, including reported reasoning
once. Input tokens are reported separately and included in cost. Jev has no token-efficiency
rank until compatible counting and enforcement are established; it remains eligible for the
shared dollar view. Provider failures are unscored and reported with their spending.

**B80** is the smallest tested token allowance reaching mean quality 80. Conservative B80
requires the lower 95% confidence bound to reach 80. No interpolation; report “not reached
within tested budgets” when appropriate. A dollar backstop that cuts a token run short makes
it cost-censored and ineligible to establish B80. Cells containing replacement attempts are
also ineligible: a restarted wallet cannot prove the whole job fit the original token allowance.

## Scoring

For each kitchen, with three requested orders:

```text
quality = 100 × max(0, (completed orders − 0.1 × invalid actions) / 3)
```

Two completed orders and one invalid action score **63.3**. Also report raw completion and
invalid-action counts. Unfinished orders receive no credit. Token waste reduces the work
possible within the allowance; it does not receive a second arbitrary quality penalty.

Average repeated trials within kitchens, then kitchens within each size family, then weight
families equally. Uncertainty resamples kitchens within families; small samples remain
exploratory. Every episode must reproduce its actions, frames and final score exactly.

## Validation before rankings

Offline tests cover the engine, resource accounting, replay and failure handling.
An independent offline study exercised 1,200
kitchens and replayed three policies on each: a public-rule reader completed all perfectly;
a table containing every recipe but fixed preparation/timing averaged 24.7 quality. A stronger
controller with adaptive timing reached 82.6. These results expose brittle scripts and also
show that within-environment specialization remains possible.

The release sequence is a six-configuration canary, full-roster canaries, matched dollar and
token sweeps, repeated trials, then fresh confirmation. A second main seed per family follows
if funds allow. All prior charges, failed-attempt
liability and boundary responses count toward the $200 operational ceiling. Unsupported
configurations remain explicitly listed. Token coverage precedes additional dollar-only depth.

## First validation results

These are **six current-configuration canary episodes**, with 150 requests independently
audited, including two interrupted attempts and their replacements. All six configurations
faced the same development kitchen with a $0.15 allowance. This checks integration; one
kitchen cannot establish stable rankings. Token-budget sweeps and B80 are still pending.

![Canary cost and quality](docs/v2/release-r2-assets/revised-canary-cost-quality.png)

[Current figures, coverage and penalty sensitivity](docs/v2/RELEASE_RESULTS.md) ·
[Raw evidence and accounting archive](data/v2/release-r2/evidence-revised-canary.zip)

PhoneLLM's isolated compatibility probe succeeded, but hosted-cost accounting is not yet
comparable enough for this dollar leaderboard. Its full-task diagnostic remains separate;
[inclusion status and evidence](docs/v2/PHONELLM_INCLUSION_REVIEW.md) explain the remaining gap.

## Reproduce without API calls

```bash
python -m pip install -e '.[dev,v2]'
python scripts/release_v2.py offline
python scripts/audit_generalization_v2.py
python -m http.server 8000 --directory ui
# Open http://localhost:8000/v2/ for the current kitchen replay.
```

Paid runs require local provider credentials, a frozen campaign, a spending ledger and the
preceding checkpoint's audited gate. Keys and request headers are never written to artifacts.
The original `kitchenrush` CLI and v1 results are retained for historical reproduction.

- [Short list of v2 changes](docs/REMODEL_PROPOSAL.md)
- [Exact evaluation contract](docs/v2/PROTOCOL.md)
- [Frozen model roster and migration notes](data/v2/release-r2-roster.json)
- [Independent generalization evidence](docs/v2/GENERALIZATION_REVIEW.md)
- [Engine and accounting review](docs/v2/CONTRACT_ADVERSARIAL_REVIEW.md)
- [Related research and strongest objections](docs/v2/POSITIONING_REVIEW.md)
- [Working name candidates](docs/v2/NAME_CANDIDATES.md)

Apache-2.0 · [License](LICENSE)
