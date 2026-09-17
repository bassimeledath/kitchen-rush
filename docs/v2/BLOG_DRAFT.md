# We gave models a kitchen and a budget. Here is where the money went.

*Working draft. Add the complete audited model results and final figures before publication.*

A model can produce a convincing explanation and still select the wrong next action.
It can also finish a task correctly while spending much more than the task is worth.
Kitchen Rush makes those tradeoffs visible: give models the same kitchen, the same orders,
and a limited allowance, then watch what they actually accomplish.

The benchmark has three ideas: **useful work, limited budgets, unfamiliar kitchens.**

Useful work means completed orders. Every model chooses one action from the same set,
including actions that would be illegal in the current state. Collect ingredients, prepare
them, cook, plate, serve, or wait. Wrong choices reduce quality and consume resources.
There is no model-specific planner correcting its mistakes.

Limited budgets means two distinct comparisons. The shared leaderboard compares task
quality and dollar cost across model architectures, including Jev's typed choices and
generative models. A separate view enforces generation-token allowances, counting reported
reasoning once. Jev does not receive an invented equivalent token count.

A dollar allowance limits which actions can execute. If the next response takes cumulative
charges over the allowance, we pay for it and archive it, but do not execute its action.
The actual-cost graph includes that charge. This boundary matters: a conservative spending
estimate should not stop one model with most of its nominal allowance unused.

Unfamiliar kitchens means recipes and public ingredient rules change. A mushroom may need
chopping before cooking in one kitchen and raw preparation in another. Cooking and burning
times vary too. The operations remain small enough to explain and animate; the model must
apply them to the instructions in front of it.

We tested that distinction with a deliberately privileged baseline. It knew all 3,552 recipe
cards, including the evaluation cards, but followed fixed preparation and timing scripts.
Across 1,200 sampled kitchens it averaged 24.7 quality. A controller that read the current
rules completed all of them perfectly. A stronger baseline that adapted its timing but kept
fixed preparation rules reached 82.6. This is evidence against brittle scripts, not a claim
that the generator cannot be learned. Learning to apply the rules is what we want to reward.

## What the finished results need to show

Insert the audited full-roster cost–quality frontier, token-budget curves and B80 table.
B80 is the smallest tested token allowance reaching mean quality 80. Include uncertainty,
raw completion, invalid actions, configuration migrations, blocked models and all spending.
Show one matched replay that explains a real difference in outcomes; do not promote a
single canary kitchen as a model ranking.

## What this benchmark cannot tell you

This is sequential action selection, not unrestricted tool-argument generation or general
intelligence. A small deterministic controller can solve the game. Training on the generator
can improve a model, and success here does not establish transfer to a production workflow.

It also does not measure serving speed. Inference pauses the kitchen. Tokens, dollars and
latency are different quantities; an efficient model on this board is not automatically the
right choice for an application with a strict response-time requirement.

Cost-aware agent evaluation already exists in [HAL](https://hal.cs.princeton.edu/about) and
[Budget-Aware Tool Use](https://arxiv.org/abs/2511.17006). Generated interactive tasks already
exist in [τ²-bench](https://arxiv.org/abs/2506.07982). Kitchen Rush contributes a compact
experiment where readers can connect a point on the graph to the unnecessary wait, wrong
preparation or exhausted allowance that produced it.

That connection is the point: the graph shows the tradeoff, and the kitchen lets you inspect it.
