# Kitchen Rush — anticipated objections & responses

A living record of critiques we expect (or have heard) and how we answer them, with the supporting
data. Keep responses honest: where a critique has a real kernel, say so and link the fix.

---

## 1. "A model scored *worse* with a looser latency budget — the benchmark must be broken."

**Observed (starter board, gen 1.0).** `gemini-3.1-flash-lite` on medium scored **KR 37 at B=1s but
only 20 at B=5s** — worse with more time. Raw numbers:

| B | raw score | served | s_null | s_ref | turns | KR |
|---|---|---|---|---|---|---|
| 1s | +13.2 | 4.2 | −86.8 | 178.2 | 31.0 | 36.9 |
| 5s | −44.8 | 2.2 | −86.8 | 215.1 | 23.6 | 19.6 |

`s_null` is identical across B, confirming **the orders are the same at both budgets — only the
deadlines move later.** So this is not a different (harder) instance.

### Why this is NOT a benchmark flaw

1. **More time can't hurt an *optimal* policy — by construction.** At B=5s the agent faces the same
   orders with *later* deadlines: a strict superset of its B=1s opportunities. It could always ignore
   the slack and replay its B=1s policy, guaranteeing **≥** its B=1s score. Therefore any drop is
   *self-inflicted* — the model is failing to maintain its own strategy when pressure is removed.
   That is a **real, deployment-relevant weakness** (a model that needs a deadline to act well), which
   is exactly what this benchmark exists to surface — not an artifact.

2. **Good models do improve with slack — see the data.** `claude-sonnet-4.6` on medium goes
   **37 → 52** from B=1s to B=5s. If the metric were structurally invalid, *every* model would drop.
   They don't. Only the fast-shallow model does.

3. **KR is graded on a curve.** KR = fraction of the *achievable* gap (S_ref − S_null) that you
   capture. At B=5s more is achievable (`s_ref` 178 → 215), so the same absolute performance is a
   smaller fraction. "Lower KR with more time" literally means "fell further behind what was
   achievable" — standard behaviour for any reference-normalized metric (same raw points → lower
   percentile on an easier exam).

**Soundbite:** *More time only hurts a model that can't manage time. A model that scores worse with a
looser deadline is telling you it needs the deadline — the exact deployment risk this benchmark
surfaces. Strong models improve with slack (sonnet 37 → 52).*

### The one place the critique *can* legitimately land — and our answer

Part of the raw drop coincides with **fewer turns (31 → 24)**: the model terminated early, likely via
the no-progress **stall guard** (`STALL_TURNS=12`). If that guard cut the model off while it still had
**servable** orders and **would have recovered**, then we partly *manufactured* the result. This is the
only version of the critique that is valid, and it is **empirical, not rhetorical**.

Mitigations / status:
- [ ] Add a **termination-reason** field (horizon / stalled / orders-done) to episode records.
- [ ] For gemini-lite's B=5s episodes, check whether they stall-terminated **with unserved,
  still-servable orders**. If they were in genuine no-progress loops → result valid. If the guard cut
  off recoverable play → loosen the guard and re-run that model.
- [ ] **Report absolute serves alongside KR** on the board, so the effect reads as real behaviour, not
  a normalization trick (most disarming single move).

Until that check is done, we state the effect is **most likely genuine model behaviour** (consistent
in direction across both medium *and* hard: 37→20 and 26→22) but flag the stall-guard interaction
openly. See also [LIMITATIONS.md](LIMITATIONS.md) §1.

> Note: `gemini-3.1-flash-lite` had the widest seed-bootstrap CI in the panel (±9.8 on KR̄), so don't
> over-quantify the exact magnitude — but the *direction* is consistent across both tiers, so the
> effect itself is real.
