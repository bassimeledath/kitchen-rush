# Separate paired recovery: eight incomplete severe kitchens

This is a second, versioned cohort. The original 24 attempts, ledger, manifest,
and audit remain untouched. The eight original seeds without a scored low/high
pair are `90605, 90606, 90609, 90611, 90612, 90613, 90614, 90615`. Both
reasoning settings restart each of these kitchens from the beginning. The
original low prefix on 90605 had already missed an order; it remains an
unscored historical attempt and is never reclassified or overwritten.

The kitchen specs, game engine, one-action prompt, reasoning settings, model
route, frozen tariffs, maximum 32,768 output tokens, decision ceiling, and
four scoring rules are identical to the first campaign. Provider failures and
operational fuses are unscored. Every request uses the same 600-second socket
timeout as before plus a new hard 600-second total request deadline. The
versioned transport preserves allowlisted HTTP/stream error details and
complete partial stream chunks. No retry or seed substitution is allowed.

The eight seed pairs run in ascending order. Attempt order alternates: high
then low on `90605, 90609, 90612, 90614`; low then high on `90606, 90611,
90613, 90615`. The full order is in
`data/v2/severe-recovery-20260929/campaign.json`.

The new, separate local ledger has a **$52 maximum accounting ceiling**:
16 episodes times the unchanged $3.25 per-episode fuse. Charges reported by
returned responses settle; failed requests retain their full reservation as
maximum possible liability. The 60-minute episode and eight-hour campaign
wall checks remain. The campaign stops immediately after an HTTP 400, 401,
402, 403, or 429; after two consecutive quarantined episodes of any other
kind; or after an operational fuse. No continuation is inferred from provider
errors. A local cap is not a statement about external credits or billing.

Report original and recovery cohorts separately. An all-12-seed combined
view may be shown with its cohort difference labeled. Do not call the
combined view an uninterrupted preregistered 12-pair replication. The tested
prompt still describes partial order and invalid-action quality; changing it
would be a separate condition.
