# Calibrated real-speed board — consolidated implementation spec

Merged from two independent specs (Claude + gpt-5.6-sol-high). Full sources:
`.dispatch/tasks/calibrated-speed-spec/{claude-spec.md,output.md}`. This is the build reference.

## Product contract
- **One board, clocked on each model's real measured speed**, frozen & dated. Explicitly a
  *dated deployment snapshot — not fully reproducible*. Flat-167 RP dropped as headline.
- **Single kitchen** = the current `medium` params, relabeled "Kitchen Rush benchmark" (no tier
  label). `hard` not run/shown.
- **Separate B=1 and B=5 tables.** Lean columns: `rank · model · KR ±CI · serve% · $/token`.
- **One row per base model × pinned endpoint** (reasoning level is calibrated, so the duplicate
  off/·think config rows collapse). ~16 base models + gpt-5.6-luna = **17 rows**.
- Efficiency = **KR-vs-$ Pareto plot** per budget; no KR/$ column (KR already prices token bloat).

## Mandatory order
preflight → speed calibration (freeze clock) → reasoning-level calibration on that clock
(freeze per-budget levels) → 4-seed pilot + cost forecast (GATE) → extend to 12 seeds → QA reruns
→ build board.

## A. Frozen clock
`L_clock(model, turn) = max(0.05, β0 + β_out · n_out)`; `think_gs = 1.0 · L_clock`.
- Stored as RP-shape `β0 + β_in·n_in + β_out·n_out` with **β_in = 0** (2-param fit). Publish
  `decode_tps = 1/β_out`. β0 = measured fixed round-trip (overhead+network+TTFT+queueing) — NOT
  labeled pure TTFT.
- `n_out = tokenize(visible text + canonical tool names/args) + provider reasoning_tokens` (pinned
  cl100k, same as RP). Reasoning charged. A reasoning-enabled call missing `reasoning_tokens` →
  `REASONING_USAGE_MISSING` = publication blocker.
- New `ModelAgent` track **`calibrated`**: compute n_in/n_out as RP does, apply frozen per-model
  coeffs for the clock, keep `resp.latency_s` separately as `live_latency_s` (QA only). runner
  unchanged (advances clock before actions → trajectory changes; cannot be post-hoc rescored).

## B. Speed calibration
- Unit = (gateway, model id, pinned provider/variant, region, tool contract). Level-independent.
- 3 frozen observation fixtures from the medium kitchen (early / mid / late-congested), real
  system prompt + tool schemas; 2 warmups (excluded) + 4 repeats each = **12 valid calls**.
- **concurrency=1, num_retries=0**, provider pinned (`provider.order=[slug], allow_fallbacks=false`),
  verify observed provider each call.
- Fit constrained `latency ~ β0 + β_out·n_out`, β0≥0.05, β_out>0, fixtures equally weighted.
- Freeze `calibration/<id>/calibration.json` (per-model: coeffs, decode_tps, diagnostics, flags).
- Flags (blocking unless noted): `CAL_TOO_FEW`, `PROVIDER_MISMATCH`, `CAL_UNSTABLE` (CV>25% in ≥2
  fixtures or p95/p50>2 or bootstrap β_out rel-halfwidth>20%), `CLOCK_FIT_WEAK` (token span<3× or
  intercept at floor → add extreme-fixture calls, else conservative fallback `β0=0.05,
  β_out=Σ(lat-0.05)/Σn_out`, never silently reuse 0.006), `REASONING_USAGE_MISSING`.

## C. Reasoning-level calibration
- Only level-selectable models: gpt-5.4-mini {off,minimal,low,medium}, gpt-5.4 {off,low,medium},
  gpt-5.6-luna {off,minimal,low,medium}, glm-5.2 {off,low(,medium)}, deepseek-v4-flash {off,low},
  gpt-oss-120b {low,medium} (off unsupported). Skip forced-on grok / gemini-3.5-flash; skip sonnet
  (forced-tool-use bars thinking). Verify each level is actually honored (`drop_params=True` hides
  rejects).
- On the **frozen clock**, tuning seeds **1000–1003** (disjoint from scored 0–11), both budgets,
  1 trial, every verified level. Pick **highest mean KR per budget** (ties → lower cost → lower
  effort). Freeze `selected_level[{1,5}]`. `LEVEL_SELECTION_UNSTABLE` if winner gap <10 KR → add
  seeds 1004–1007 once, then freeze observed winner + flag. **Never reuse tuning episodes in the
  board** (winner's-curse).

## D. Scored run
- `sweep.py --track calibrated --calibration <f> --roster <f> --tiers medium --budgets 1,5
  --seed-list 0..11 --trials 1 --workers 1 --num-retries 0`.
- Per-row provider from roster; verify observed provider each call. Fail-closed if any row lacks a
  clock/level, is unpinned, fallbacks on, or hashes mismatch.
- Log `live_latency_s` + provider per call for drift; **clock always uses frozen coeffs**.
- API failure = infra-invalid episode → quarantine for targeted rerun, NOT a 30s stall. Preserve
  genuine model no-op/invalid behavior.

## E. QA + reruns
- `runs/<name>/qa.{json,md}` + `quarantine.jsonl`.
- `PROVIDER_MISMATCH` (any call; 2 for an endpoint halts it), `SPEED_DRIFT` (median
  live/clock outside [0.80,1.25]) / `SEVERE_SPEED_DRIFT` ([0.67,1.50] → not publishable),
  `KR_SEED_OUTLIER` (robust z>3.5; inspect, don't auto-rerun), `COST_MISMATCH` (>5%).
- Rerun ONLY confirmed infra failures (same model×B×seed, same clock/level). Severe drift →
  recalibrate + rerun that model both budgets all seeds. Never rerun because a score is surprising.

## F. Output
- Two tables (`B=1`, `B=5`): `rank | model | KR ±95%CI | serve% | $/1M tok`. KR = mean over 12
  seeds; CI = common-seed bootstrap (larger half-width). `$/token` = total billed $ / total tokens.
- **Rank bands:** new band only if mean gap ≥10 KR AND paired seed-bootstrap CI lower bound >0;
  render band as ordinal range.
- Two Pareto scatters (KR vs $/1M tok, frontier drawn, QA-flagged outlined). Adapt `plot_pareto.py`.
- Honesty header (dated snapshot, region, concurrency 1, 1 attempt, no fallback, artifact hash,
  ruleset, tokenizer, seed list, price date) + per-model calibration appendix.

## G. Cost controls
- **Hard stop $150** across everything; **my working cap $100** (leave buffer of the $132 balance).
- **Reserve before each episode** using p95 observed episode cost; refuse if `spent+reserve>cap`.
  Flush spend per call.
- Stage caps: speed cal $5, level cal $30 cumulative, pilot recompute forecast before extending;
  continue to 12 only if p95 forecast + $12 reserve ≤ cap.

## Pragmatic build notes (where I simplify sol's spec for a safe overnight build)
- Skip the full transcript-bundle hash-verifier; store coeffs + artifact + basic hashes only.
- Bootstrap CIs: 2000 draws (enough), not 10k.
- Calibration data collected via the existing `--track rt` sweep path with added **per-call step
  logging** (n_in, n_out, latency), fixtures implemented as fixed seeds/turns; fit offline.
- Reuse existing sweep infra; add the calibrated track, roster/provider-map, num_retries, and
  per-episode cost reservation. Test on 1–2 pennies-episodes before any scaled spend.
