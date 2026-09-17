# Independent adversarial review — original v2 pilot

Read-only code review and offline counterexamples against pilot source `b5054321ecbc`, on 2026-09-16. No paid calls. **Do not promote the original pilot to release results. Three concrete engine defects need correction and fresh runs.** Deterministic replay reproduces these defects; replay agreement alone does not establish correct mechanics.

## Must fix before the full rerun

**1. Hidden future recipes change the legality of a public action.** `engine.py:109` derives `active_ingredients` from all recipe cards, including pending orders; `_a_collect` rejects anything outside that set. The v2 projection shows every dispenser. Two kitchens with identical public observations but different hidden cards therefore accept versus reject the same `collect(patty)` action. This leaks future membership through feedback and makes apparent dispenser availability misleading. Initial-observation equality tests miss it. Derive v2 availability from public dispensers and test equality of immediate action transitions while changing hidden cards. No matching rejection occurred in the 72 pilot episodes, but the contract invariant is broken.

**2. Pickup can burn food and then recover it as cooked.** `engine.py:529–535` saves READY status, advances the one-second pickup (which may burn and delete the job), then appends a COOKED component from the stale status. This occurred **nine times in seven pilot episodes**, including Mini's four-ingredient dollar trial and six Sonnet episodes. A short real prefix is `collect(egg)`, `cook(egg)`, early `collect_cooked(egg,1)`, `collect(lettuce)`, `collect_cooked(egg,1)`: the last action emits an egg burn at 12.05 seconds and then places that cooked egg in hand. Specify a coherent boundary rule: either remove food atomically when pickup starts, before advancing time, or require survival through pickup and reject appropriately. Test both sides of the burn boundary and verify the old prefixes under the corrected rule.

**3. Invalid actions can expire the last order without terminating, causing an extra billed request.** `engine.py:280–285` breaks on invalid before checking termination. Reproduction: wait until the latest deadline minus one second; choose `chop(bun)`. All orders expire during the invalid-action charge, but `terminated` remains false. The evaluator then pays for another selection; the next step terminates without executing it. **Five pilot episodes contain one more recorded action/request than action frames:** Jev seeds 44001/trial0/$0.40, 44002/trial1/$0.40, 44000/trial1/$0.05, 44000/trial0/$0.15; Sonnet seed45004/token2048. Check termination after invalid actions too, and audit an exact one-to-one correspondence between admitted selections and action frames.

## Measurement changes to validate

The proposed actual-cost admission rule removes the known conservative-reservation bias, provided its meaning is explicit: **nominal dollars fund an accepted action prefix; actual total API spending can include one additional boundary response.** Pay and archive the first response that would cross the allowance, execute none of it, and stop. Include its entire charge in actual-cost charts. Never use its content for recovery or a subsequent action. Do not penalize it as an invalid game action. Test exact equality, one nanodollar over, valid versus malformed crossing output, token/dollar simultaneous limits, and prohibition on a second boundary call. A global/stage reservation failure is an operational incomplete run, not poor model quality.

The existing pilot has only **three independent kitchens per view, one per recipe size**, and nine unique cards in each view. Repeated trials do not increase task coverage. The current bootstrap resamples seed means across the three fixed families; it does not preserve the declared equal-family sampling design. For release, use multiple independent seeds per family, resample within family and preserve model/budget pairing for comparisons. With one seed per family, mark family-stratified uncertainty unavailable rather than implying precise population estimates. B80 is a descriptive tested crossing, not proof that a model reliably reaches 80 on new kitchens.

## Honest limitations, not established defenses

- The grammar contains **3,552 distinct cards total; the main split has 355, including only 12 two-ingredient cards**. This is easily enumerable. Order combinations add complexity, but structural holdout and a failed R1/R2/R3 lookup baseline do not prove resistance to benchmark-specific post-training. Keep claims at "unfamiliar held-out recipe specifications"; an anti-memorization claim needs a stronger trained or compiled recipe-plan baseline plus fresh compositional transfer tests.
- Score clipping can give zero both to complete failure and to a model that delivered an order before making enough invalid calls. The fixed penalty weight 0.1 is a chosen utility tradeoff, not empirically established. Always show completion and invalid actions beside quality; publish sensitivity if promoting an overall winner.
- Shared candidate selection, auto-navigation, full observations and no persistent conversation are legitimate but narrow the construct to action selection in this environment. They do not test unrestricted tool argument construction or general-purpose agent memory.

## Checks that held up

Recipe signatures are disjoint across configured splits; generation is independent of resource budgets; inference adds zero kitchen time; candidate sets retain syntactically valid but illegal choices; model adapters do not invoke the offline solver. Source archives and request/frame audits provide valuable provenance, but the three counterexamples show why independent semantic probes remain necessary.

## Independent correction recheck

The subsequent v2 engine edits resolve the three reproduced defects. **28 current v2 tests passed**, supplemented with independent probes:

- All **120 candidate actions** produced identical public successor states in a pair of kitchens differing only in a hidden card.
- The real five-action Sonnet prefix now lifts the egg without a false burn event. A separate item that burns during travel is correctly absent and its pickup invalid.
- An invalid action expiring the final order now immediately terminates; every selected/executed turn in the probe has exactly one action frame.
- Hidden recipe and order IDs are absent from the shared candidate set. The evaluator maps model output through this set, so a guessed hidden plate/serve action becomes an invalid selection instead of reaching hidden engine state. Directly injecting arbitrary ToolCalls into the engine bypasses that interface and remains outside the scored contract.

This recheck validates these engine corrections, not the pending full-roster adapters, new budget runner, statistical design, or release results. The original paid artifacts remain historical protocol evidence.

## Independent actual-cost accounting audit

The revised provider/evaluator/ledger implementation passed **32 v2 tests** and additional offline probes for valid versus malformed crossing responses, one-boundary-request termination, exact nanodollar amounts, immutable $10.532554644 prior liability, and concurrent operational reservations. The first unaffordable response is charged without selecting or executing its action. Operational stops are incomplete and rejected by aggregation. An actual charge exceeding its reservation is persisted before the run is quarantined; no dollars were lost in that fault probe.

Two corrections were requested during this review:

1. When a successfully admitted request exactly exhausts both token and dollar allowances, the next-loop dollar check takes priority and falsely marks the token cell dollar-censored. The reproduced case admitted one action costing 36 nanodollars and using all 16 tokens; additional dollars could not have extended its token-limited work. Prefer token exhaustion on this exact tie, while still marking a rejected over-budget boundary response as dollar-censored.
2. Settlement overrun raises before parsed usage is copied into request/artifact totals. The charge survives in the ledger and episode cost, but known token totals misleadingly remain zero. Persist the parsed usage before settlement can throw, while retaining quarantine and global overrun blocking.

Family-stratified bootstrap implementation now preserves equal family weighting, requires two independent seeds per family, and distinguishes the same seed number used in different families. A fixed-score-per-family probe returned the expected unchanged family mixture across bootstrap draws. This addresses the earlier resampling defect; two seeds per family still offer weak tail coverage.

Estimated TypeSafe reservations are not verified provider spending caps and must remain labeled estimates. Cost charts calculated only from complete protocol runs are conditional on those runs; expose failed-attempt spending separately and avoid interpreting them as unconditional deployment economics.

## Independent release gate implementation

Added `scripts/audit_release_v2.py --batch NAME`. It binds the gate to the exact manifest, campaign, full frozen roster, model configuration, source archive and current source. Historical labels must all remain accounted for, including explicit blocked configurations; an internally balanced subset cannot masquerade as the planned batch. It opens ledgers read-only, reconciles prior spending and every request, re-derives public inputs, independently parses responses and admission boundaries, and executes every admitted action in a fresh engine. Saved replay flags are ignored. The gate is written only after complete verification; failed re-audits remove stale successful gates.

Offline fixture validation passed a complete frozen-plan three-control batch with eight synthetic requests, even when saved replay flags were false. It rejected changed boundary admission, boundary selection, hidden input, usage totals, actions, frames, stopping reasons, missing paid requests, and an internally balanced model subset. Stale-gate invalidation passed. Monetary totals remain integer nanodollars through aggregation. Scoped Ruff checks passed. These were synthetic tests, not paid evaluation results.
