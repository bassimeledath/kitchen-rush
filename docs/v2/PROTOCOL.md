# Kitchen Rush v2: frozen evaluation contract

**Useful work. Limited budgets. Unfamiliar kitchens.**

V2 measures action selection under resource constraints. It does not measure network speed,
unconstrained tool-argument generation, or equal computational effort across architectures.
The full roster rerun, README rebuild, graphs and replay presentation are authorized.
Release epoch r2 supersedes the diagnostic 2,048-token per-request configuration.
Legacy and preliminary `choice-1` pilot scores do not enter this release.

## Shared task

Each episode has a 7×7 room, ten dispensers, two burners, four hand slots and three orders.
Cards require two, three or four ingredients in specified RAW/CHOPPED/COOKED states.
Every model receives the same public state and syntactic candidate actions, including illegal
choices and explicit waits. It selects exactly one action per request. Shared automatic walking
is disclosed assistance; adapters supply no planner, legality filter or recovery policy.

Each request receives current public state and the previous action result; earlier conversation
and private scratchpads are not carried forward. Future orders stay hidden until arrival. Public ingredient rules cover all dispensers and vary
independently of the menu: cooking takes 4–12 game seconds, food burns 3–8 seconds later,
and mushrooms require either RAW or CHOPPED preparation. Rules and recipes belong to each
instance. Recipe and whole-profile signatures use disjoint development, pilot, main and
confirmation hash buckets. This tests unfamiliar combinations, not immunity to training on
the public generator. Learning a controller that reads these rules is intended progress.

Inference pauses the kitchen. Movement, actions and waits advance game time; food can burn
and orders expire. Pickup removes food from heat upon arrival, before its handling duration.
An invalid action consumes three game seconds. Termination is checked after invalid actions too.
The episode ends when all orders resolve, at 600 game seconds, 160 decisions, 12 consecutive
nonproductive decisions, or resource exhaustion. Valid waits reset the stall counter.

## Quality and budgets

For completed orders D, invalid actions I and requested orders N=3:

    q = 100 max(0, (D − 0.1 I) / N).

Report completion and invalid-action counts alongside quality. Average trials within kitchens,
kitchens within each size family, then the three families equally. Unfinished orders receive
no credit. Wrong actions reduce quality and consume resources; wasted tokens reduce the work
possible within the allowance.

**Shared dollar view:** each request is charged before its action is admitted. Execute its
selection only when cumulative actual inference charges are at most the episode allowance B.
The first response crossing B is archived and paid, but its action is never executed and it
adds no invalid-action penalty. Then stop. Thus B limits the admitted action prefix; **actual
API spending can exceed B by one request**. Plot quality against actual total cost, including
that boundary request. Show failed attempts and their costs separately; completed-run costs
alone are conditional economics. A Pareto point costs no more and scores no less than another,
with at least one strict improvement. Freeze model configurations, routes and tariff provenance. Dollar calls use a common
16,384-generation-token safeguard supported by every selected endpoint; truncation is fully
charged and reported. This is a configured policy, not an unlimited-compute comparison.

**Generation-token view:** use separate episode allowances, including reasoning tokens once
within reported completion usage. Bound every request by min(16,384, remaining tokens). The tested allowances
512 / 2,048 / 8,192 are below that safeguard, so the episode wallet determines each call cap. Input tokens count
toward dollars and are reported separately. Jev is ineligible for this view until compatible
accounting and cap enforcement exist. Dollar backstops that truncate a token episode are
marked censored. B80 is the smallest tested, uncensored token allowance with mean quality≥80;
conservative B80 requires its 95% lower confidence bound≥80. No interpolation. Otherwise report
“not reached within tested budgets.” Untested models are marked “not tested.”

Resample independent kitchens within each family, preserving trials, for 4,000 bootstrap draws.
Intervals require at least two kitchens per family and remain exploratory at small samples.
Provider response timing is operational debug data and does not enter scores or public graphs.

## Execution and evidence

The total experiment allowance is $200, including $16.175129022 of prior accounted costs and
retained uncertain liability. A durable ledger reserves operational liability before dispatch,
settles actual charges, and blocks overspend or unresolved accounting. These reservations do
not determine scored action admission. OpenRouter reserves the frozen endpoint context ceiling
at the maximum input/cache-write tariff across all nested tiers and durations, plus capped
output at the maximum output/reasoning tariff and request fees. Jev lacks a verified input
ceiling; its explicitly estimated one-million-input-token reserve is not a provider guarantee.
No automatic retries or auto-recharge are enabled by this experiment. A manually reviewed
HTTP 408/429/5xx or transport failure can receive one identical replacement episode.
The failed prefix must replay and reconcile; its trace and full uncertain liability are
preserved. Completed, invalid, truncated or accounting-failure episodes cannot qualify.
A second failure remains unscored; no third attempt is allowed.

Operational stops and provider/accounting failures are unscored, not quality zero. Unknown
charges retain liability. Each run records exact source/configuration, task, public requests,
responses, usage, admitted actions and replay frames. An independent audit regenerates prompts,
reconciles charges, and replays every action before advancing: six-configuration canary → roster canaries → matched dollar sweep →
token sweep → repeated trials → fresh confirmation → second main seed if funds permit.
PhoneLLM remains explicitly blocked pending verified hosting accounting. Gemini 3.5 Flash
historical “off” migrates explicitly to supported “minimal”; the medium-effort row stays separate.
