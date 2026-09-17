# Positioning and strongest objections

**Kitchen Rush has a useful, explainable niche; the research does not support a claim that
cost-aware agents, generated tasks, or stateful tool evaluation are new.** Its differentiation
is the combination: one small visual environment, explicit resource allowances, objectively
checked outcomes, and replayable mistakes under changing public rules.

## Closest prior work

- **Stateful interaction:** [τ-bench](https://arxiv.org/abs/2406.12045) checks final database
  state and repeated-trial reliability in tool/user conversations.
  [τ²-bench](https://arxiv.org/abs/2506.07982) adds shared user/agent control and a compositional
  task generator. Generated, verifiable tasks are already established.
- **Function calling:** [BFCL](https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html)
  evaluates function names/arguments, execution and relevance; its documentation includes
  cost, latency and multi-turn extensions. Do not describe competing tool benchmarks as
  merely single-call accuracy tests.
- **State dependencies:** [ToolSandbox](https://arxiv.org/abs/2408.04682) evaluates stateful
  execution and intermediate/final milestones with a simulated user.
  [AgentBench](https://arxiv.org/abs/2308.03688) spans eight interactive environments.
  Kitchen Rush trades this breadth for a controlled, inspectable game.
- **Efficiency:** [AI Agents That Matter](https://arxiv.org/abs/2407.01502) motivates joint
  accuracy/cost optimization and stronger holdouts. [HAL](https://hal.cs.princeton.edu/about)
  already offers cost-controlled comparisons, usage tracking and traces.
  [Budget-Aware Tool Use](https://arxiv.org/abs/2511.17006) studies explicit tool budgets,
  token/tool costs and Pareto curves; [BAGEN](https://arxiv.org/abs/2606.00198) tests remaining
  budget estimation and trainable budget awareness. Budget sensitivity itself is not the moat.

## Recommended framing

**Useful work. Limited budgets. Unfamiliar kitchens.**

“Kitchen Rush measures how much a configured model accomplishes through valid actions as
its resource allowance changes. Every run is a kitchen you can watch and independently
replay. Recipes and ingredient rules change, so familiar action scripts can fail.”

The practical advantage is a legible experiment: readers can connect the quality/cost curve
to the exact unnecessary wait, wrong preparation or exhausted allowance. The defensible moat
would be trusted execution, broad maintained model coverage, transparent configurations and
accumulating evidence of transfer—not the kitchen theme or an uncopyable generator.

## Criticisms to answer directly

1. **“This is multiple choice, not general tool calling.”** Correct: it tests sequential
   action selection from a shared candidate set, including invalid choices. It does not test
   unrestricted argument construction, tool discovery or multi-action generation. Label it so.
2. **“A hand-coded chef solves it.”** That is a feasibility control. General-purpose models
   must read the current specifications; successful kitchen play does not establish broad
   intelligence or predict performance on a production workflow.
3. **“I can train on your generator.”** Yes. Profile changes defeat fixed scripts; a learned
   rule-reading controller can succeed. Publish the stronger lookup controls and remaining
   overlap limitations. Claim unfamiliar instances, never memorization immunity.
4. **“You measured pricing and the harness.”** Dollar comparisons measure configured service
   utility, not equal compute. Freeze routes/tariffs/settings, separate invoice-derived and
   tariff-derived costs, and include rejected boundary calls in actual-cost reporting.
5. **“Tokens are not architecture-neutral compute.”** Report provider generation usage,
   including reported reasoning, with inputs separate. Hidden computation and tokenizer
   differences remain. Jev belongs in the dollar view; no invented token exchange rate.
6. **“The score and winner are arbitrary.”** Show completions, invalid calls, penalty
   sensitivity and uncertainty beside quality/B80. Eighty is a declared target, not a
   universal capability threshold. Small samples cannot establish stable ranks.
7. **“Does this identify fast models?”** No. V2 excludes serving latency. Do not reuse the
   old speed headline or promise to resolve the tweet's latency comparison.

Suggested blog angle: **“We gave models a kitchen and a budget. Here is where the money went.”**
