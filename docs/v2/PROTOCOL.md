# Kitchen Rush v2 protocol — checkpoint implementation

Useful work. Limited budgets. Unfamiliar kitchens.

This is a new benchmark generation. No legacy scores carry over. The release name remains
Kitchen Rush until a replacement is chosen. Checkpoints 1 and 2 are authorized; the full
roster run and release are checkpoint 3.

## Task and action contract

A 7×7 kitchen, ten fixed ingredient types, two burners, four hand slots, and three requested
orders per episode. Families require two, three, or four ingredients per card. Ingredient
physics and verbs are stable; cards specify terminal RAW, CHOPPED, or COOKED states. Recipes
belong to the instance, never to a patched process-global catalog.

Every model receives the same public-state projection and full syntactic candidate set.
All grid coordinates, ingredient/action combinations, known recipes and order IDs remain
available even when invalid. Future dishes, recipe requirements, arrival times, deadlines,
values, and derived horizon hints are hidden. Dispensers cover every ingredient irrespective
of future orders. The pending order count is public. Recipe IDs carry no semantic meaning.

One selected action executes, then a fresh observation is supplied. There is no conversation
memory beyond public state and the previous action result. All adapters share this contract.
Jev answers one flat Choice question (under its 255-option limit); generative models output
only strict-schema JSON containing the chosen candidate key. No model is asked to imitate another model's
probability outputs. Malformed/refused/truncated model output is charged and counts as one
invalid action. Transport or accounting failures quarantine an episode; they are not model
mistakes and do not enter rankings. No retries occur automatically. Network requests use a 240-second operational timeout.

Auto-navigation to a chosen station and nearest-free-burner selection are shared engine
assistance inherited from v1. Neither plans dishes nor removes invalid choices. No model
adapter calls the scripted solver, fixes choices, filters legality, or reads private state.

## Time and termination

Inference advances no game time. Actions, movement and explicit waits of 1/2/5/10/20 seconds
advance cooking, burning, arrivals and expiry. Kitchens are identical across resource levels.
The kitchen ends when all orders resolve, at 600 game seconds, at 160 decisions, or after 12
consecutive nonproductive decisions. Successful waits reset the stall counter. Resource
exhaustion can end it earlier. Unfinished and not-yet-arrived orders count as incomplete.
API timing is debug metadata, excluded from score, B80 and public plots.

## Quality and resource views

For episode i, requested orders N_i=3, completions D_i and invalid actions I_i:

    q_i = 100 max(0, (D_i - 0.1 I_i) / N_i).

Quality is the equal-weight mean over seeds, with repeated trials averaged within each seed.
Use equal seed counts per task family. Report raw completion and invalid calls alongside Q.
There is no reference-chef normalization or latency penalty. Bad actions reduce Q and consume
resources; waste consumes allowance that could have completed orders.

Main view: rank Q at a selected episode USD allowance. The pilot grid is $0.05/$0.15/$0.40.
Plot each configuration's Q against allowance and actual mean episode API cost. A point is
Pareto-dominated if another costs no more, scores no less, and strictly improves one axis.
These are economic comparisons, not equal-compute claims. Freeze prices and provider routes.
OpenRouter usage.cost is provider-reported billing; Jev cost is usage.input_tokens multiplied
by the published $0.042/M input tariff, with free output pricing. Mark that provenance.

Token view: separate runs with 512/2,048/8,192 generation-token allowances. Provider completion
usage includes reasoning: count it ONCE, never add its reasoning subset again. Inputs are
recorded separately and included in cost. Max output per request is bounded by remaining
allowance and model configuration. Jev is N/A until comparable generation-token accounting
and cap control are established. A $1.00 operational backstop applies to pilot token runs;
any cost-censored cell is explicitly excluded from B80, not silently treated as a pure token
experiment. B80 is the smallest TESTED allowance with mean Q>=80. The conservative variant
requires the 95% lower confidence bound >=80. No interpolation; no crossing means not reached.

Resample seeds with replacement, retaining within-seed trials, for 4,000 deterministic bootstrap
draws. Publish percentile 95% intervals; fewer than three seeds means interval unavailable.
Pilot intervals are exploratory and never establish a full-roster winner.

## Generalization and feasibility

The generator partitions normalized ingredient/state specifications by SHA-256 modulo ten:
development buckets 0–5, pilot 6–7, confirmation 8, main 9. Consequently even a fresh seed cannot
reuse a development recipe in pilot/main. Order permutations and release timing vary too.
Public seeds and grammar are reproducible, not permanently secret. After disclosure, fresh
seeds do not cure training on the entire distribution. This is a large combinatorial kitchen,
not a literal infinite or memorization-proof benchmark. Publication must say this clearly.

The offline recipe-reading solver is a feasibility witness, never a model hint or score
normalizer. Verify every scored instance through the same engine before dispatch. Compare
recipe-reading with a fixed-card lookup baseline and test same-state changed-requirement pairs.
Successful learning of stable operations is intended; benchmark-specific specialization remains
a limitation to measure through broader transfer work, not something these checks prove absent.

## Spending, failures and provenance

A SQLite ledger atomically reserves a request before dispatch against episode, stage and total
caps ($5 canary, $15 smoke, $30 pilot; $50 total). Reservations survive crashes. Settlements are
idempotent; uncertain attempts retain their full liability and halt further dispatch. Any
provider overrun is recorded before raising a stop. A request ID cannot be dispatched twice.

Input liability uses an intentionally conservative serialized-byte token bound plus 4,096
provider-template tokens; output liability uses the enforced per-request cap. Jev uses one
question, avoiding speculative replication. These assume providers honor documented usage and
prices; every response checks usage against the reservation. Never release an uncertain reserve
on timeout alone. An abandoned unscored attempt may permanently retain its full liability
(with actual billing explicitly unknown) so independent experiments can proceed within the cap. Unused residual allowance and inability to reserve another request are reported.

Each episode preserves its full instance, protocol/source hash, frozen model settings, budget,
public prompts, candidates, responses, usage, executed actions, and replay frames. Secrets and
request headers are never recorded. Replay the actions in a fresh engine and compare every frame
and final Q. A change to any load-bearing source invalidates mixing affected result generations.

Stage progression additionally requires manifest completeness, matched seed/budget cells,
reconciled ledger totals, no unresolved requests/overruns, and reviewed traces. Development
canaries/smoke inform one fixed adapter configuration per model before fresh pilot evaluation.
No main/confirmation model sweep is launched as part of the current authorization.

Sources: [TypeSafe API](https://docs.typesafe.ai/api),
[Choice](https://docs.typesafe.ai/primitives/choice),
[Jev pricing](https://typesafe.ai/blog/introducing-system-one-models-and-jev),
[OpenRouter reasoning usage](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
