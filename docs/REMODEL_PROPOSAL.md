# Kitchen Rush v2 — proposed changes

**Useful work. Limited budgets. Unfamiliar kitchens.**

Approved scope for the remodel. Checkpoints 1–2 are implemented in this worktree; the full
roster run and release remain checkpoint 3. Existing Jev diagnostics are not v2 results.
See [the implemented protocol](v2/PROTOCOL.md) and [README rebuild outline](v2/README_OUTLINE.md).

| Area | What changes in v2 |
|---|---|
| **One shared leaderboard** | Rerun the full existing model roster, plus Jev, under one action-selection protocol. Rank by mean quality at a selected episode dollar allowance, identical for every configuration. The small pilot is only an early validation subset. |
| **Shared actions** | Identical public observations and candidates, including invalid actions and waits. Execute one action, then provide fresh feedback. Jev returns typed choices; LLMs return their selected action. Unconstrained argument generation and multi-action responses leave the benchmark. |
| **Resource accounting** | Count all API costs, including input, reasoning, unused speculative answers, and retries. Freeze prices and configurations per release. This measures practical API efficiency; equal dollars do not imply equal compute. |
| **Token-efficiency view** | Separately test generation-token caps, counting reasoning, generated text/action arguments, and recovery once. **B80**: smallest tested allowance reaching mean quality 80, otherwise “not reached.” Jev’s B80 is **N/A** pending comparable accounting and enforcement; it remains on the shared leaderboard. |
| **Kitchen clock** | Inference consumes allowance but pauses the kitchen. Actions and waits advance cooking, burning, and deadlines. Keep instances identical across budgets. Exclude provider latency from scoring, rankings, and public charts. |
| **Unfamiliar kitchens** | Generate ingredient/state cards with stable tools and physics. Hold out recipe specifications and order combinations. Verify solvability; test changed-requirement pairs and memorization baselines. No new dependency-graph mechanics. |
| **Quality** | Replace reference-chef normalization with `100 × mean(max(0, (completed − λ × invalid) / requested))`. Fix λ before scoring; also report completion and invalid calls. Typed choices can still be wrong actions. |
| **Results** | Show quality versus dollar allowance, actual episode cost, and the cost–quality Pareto frontier. Include repeated trials, confidence intervals, and a conservative B80 variant using the lower confidence bound. |
| **Fairness** | Candidates use public information only. No legality filters, hidden future orders, automatic repairs, or solver hints. Tune adapters with comparable effort on development instances. Freeze and disclose settings, adapters, prices, and stopping limits before held-out runs. |
| **README rebuild** | Rewrite from scratch around useful work, limited budgets, and unfamiliar kitchens. Explain scoring, dollar/token views, B80, generalization limits, and reproduction. Replace all legacy graphs and conclusions with fresh v2 evidence. |
| **Presentation** | Update methodology, replay UI, animations, labels, and linked documentation consistently. Show generated cards and remaining allowance. Choose the name separately; archive legacy results. |

**Three checkpoints:** (1) $0: validated offline environment, full model roster, README outline, and graph specifications. (2) Up to $50: staged canary/smoke/pilot, verified accounting and fairness, frozen protocol, and costed coverage for the entire roster. (3) $100 main + $30 confirmation: rerun all models on fresh instances, verify results, and finish the README, graphs, and replay experience. Retain $20 for recovery. Reserve costs before dispatch. Pilot costs determine balanced repeats; report any blocked model or insufficient budget before the main run, without silently shrinking the roster.

Jev basis: [typed choices and speculative questions](https://docs.typesafe.ai/primitives/choice); [reported usage and documented API controls](https://docs.typesafe.ai/api); [pricing](https://typesafe.ai/blog/introducing-system-one-models-and-jev).

**Pilot freeze:** paused inference clock; λ=0.1; dollar allowances $0.05/$0.15/$0.40;
generation allowances 512/2,048/8,192 with a $1 operational backstop. Name and the final
full-roster coverage remain release decisions; any protocol change requires fresh results.
