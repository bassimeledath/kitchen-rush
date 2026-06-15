# Kitchen Rush — SCORING (ARCHIVED design history)

> ⛔ **ARCHIVED — not the spec, not a formula source.** This document described an earlier,
> aspirational scoring scheme (the `RTTC` gated headline and the `η = clamp(S/S*, 0, 1)`
> oracle-relative normalization). **Neither is implemented.** It is kept only as design history.
>
> The **authoritative scoring formulas** now live in **[RULES.md](RULES.md) §9** (events, values,
> decay, combo, penalties, and the KR headline §9.8). The **headline-metric rationale** (why
> null-floor normalization, why no √-gates, the 1-second preference, the parameter taxonomy) lives
> in **[METHODOLOGY.md](METHODOLOGY.md)**. Constants are the canonical values in
> `src/kitchenrush/config.py` (mirrored in RULES.md §16).

## What is actually scored (pointer, not a restatement)

The one headline the code computes (`src/kitchenrush/metrics.py`) is the **null-floor Kitchen
Rush score**:

```
KR = 100 · mean over (seeds × trials) of  clip( (S_model − S_null) / (S_ref − S_null), 0, 1 )
```

- `S_model` — raw game score with the model's latency charged into the world clock.
- `S_null` — the analytic do-nothing floor (`oracle.null_score`): every order expires, no
  invalid/burn/drop penalties.
- `S_ref` — the greedy-EDF reference scheduler at zero latency (`oracle.reference_score`); a
  **reference anchor, not a proven optimum**, so KR clips at 100.

Raw score, completion/expiry/invalid rates, latency percentiles, and Pass^k are **diagnostics**,
not multiplied into the headline. See RULES.md §9.8 and METHODOLOGY.md §1 for the full statement.
The ranked latency track is **RP** (METHODOLOGY.md §3).

## What was removed (and why)

The following appeared in earlier drafts of this file and are **obsolete — do not use them**:

- **`RTTC = 100 · η · √OnTime · √(1−Invalid) · √Pass⁴`** — the gated headline. Dropped: the gates
  double-count behavior the game already prices and add arbitrary exponents (METHODOLOGY.md §1).
  The implemented headline is the un-gated KR above.
- **`η = clamp(S_raw / S*, 0, 1)`** — bare-ratio "oracle-relative" normalization. Replaced by the
  null-floor normalization `(S − S_null)/(S_ref − S_null)`. These are *different metrics with
  different orderings*; only the null-floor one is implemented.
- The earlier penalty constants quoted here (`BURN=−8, INVALID=−5, DROP=−6`) and
  `STALL_TURNS=50` were **wrong**; the canonical values are `BURN=−5, INVALID=−3, DROP=−4`,
  `STALL_TURNS=12` (config.py / RULES.md §16).
- References to an `RP` "recomputable / provider-independent" guarantee and a submission validator
  that rejects null reasoning tokens — the validator does not exist, and RP is **provider-trusted
  on reasoning tokens** for hidden-reasoning models (METHODOLOGY.md §3.1, RULES.md §3.2.1).
