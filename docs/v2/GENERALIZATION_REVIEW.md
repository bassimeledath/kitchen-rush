# Independent generalization audit

**The extension works against fixed recipe scripts. It is not memorization-proof.**

The implementation keeps the same ingredients, room, orders, states and verbs. Five cookable
ingredients receive per-kitchen cook durations of 4–12 seconds and burn windows of 3–8 seconds.
Mushroom cooking requires either RAW or CHOPPED preparation. Rules are instance-owned and
public; card and profile splits are separate. No paid calls were made in this audit.

## Verified evidence

A deterministic sample covers **1,200 kitchens**: 100 seeds × four splits × three recipe sizes.
Each policy used the real engine, its normal horizon/160-decision cap and exact replay checks.

| Offline policy | Mean quality | Perfect kitchens |
|---|---:|---:|
| Reads actual cards, ingredient rules and burner state | 100.00 | 1,200 / 1,200 |
| Lookup of every card, fixed preparation and timing | 24.71 | 266 / 1,200 |
| Correct cards, fixed preparation rules, live burner timing | 82.64 | 977 / 1,200 |

The full-rule solver had **zero invalid actions or burns** and used at most 53 actions.
All **3,600 policy runs replayed exactly**. The global ingredient catalog remained unchanged.
These are sampled validations, not an exhaustive feasibility proof.

The lookup is deliberately stronger than the old R1/R2/R3 control: its table contains **all
3,552 cards, including held-out cards**. It binds actual public recipe/order IDs, chooses the
next order by deadline, and knows the occupied burner index. Its preparation and waits are
fixed to the legacy cookbook; it has no automatic repair. The stronger third policy responds
to actual burner readiness, so its failures isolate preparation assumptions from fixed waits.

A positive control rules out a broken lookup implementation: on **120 paired kitchens**, the
fixed table scores **100 on every canonical-profile control**. Changing only profiles, with
identical cards, arrivals, layout and generous 500-second deadlines, lowers its mean quality
to **22.50** (27 perfect). Both members of every pair replayed: **240 additional verified runs**.
This is direct causal evidence that changed profiles defeat the fixed scripts.

## Limits that should remain visible

- The stronger fixed-preparation/live-timing policy still scores **82.64**. Its 223 failures
  exactly match kitchens requiring cooked mushrooms whose prerequisite changed to RAW.
  A small general controller can handle this variation; learning it is intended behavior.
- **81 of 1,200 kitchens contain no cooked ingredients.** Profiles have no causal effect in
  those episodes. A large product of possible timers exaggerates meaningful task diversity.
- Complete profile signatures are disjoint between splits. However, signatures restricted
  to ingredients actually cooked overlap: **12–23 distinct relevant signatures per split
  pair** in this sample. Do not claim every task-relevant physical combination is held out.
  Per-ingredient values are intentionally shared; the test concerns their composition.
- Finite recipe lookup combined with a controller that reads public rules can still succeed.
  Training on the generator can improve or saturate this benchmark. These results support
  “unfamiliar generated kitchens,” not literal infinity or broad real-world tool-use transfer.

The engine's chop/cook behavior, timers and public-rule solver use instance profiles.
One legacy combo/reward calculation still reads the fixed catalog; v2 quality excludes that
reward. Avoid claiming every legacy diagnostic has been redefined around instance profiles.

## Reproduce

From the v2 worktree:

```sh
/Users/bassime/Desktop/fullstack/kitchen-rush/.venv/bin/python scripts/audit_generalization_v2.py --output data/v2/release-r2/generalization-audit.json
```

[Audit script](../../scripts/audit_generalization_v2.py) ·
[Per-seed evidence, source hash and script hash](../../data/v2/release-r2/generalization-audit.json)

The script refuses to publish evidence if core source changes during the audit. It tests
positive controls as well as failures and exits unsuccessfully if the public-rule solver
fails, the global catalog changes, a canonical-profile lookup control fails, whole-profile
splits overlap, or changing profiles never harms the fixed-table controls. It records
whole-profile and task-relevant overlaps separately. All findings here concern this sampled
release candidate; changing the environment requires rerunning the audit.
