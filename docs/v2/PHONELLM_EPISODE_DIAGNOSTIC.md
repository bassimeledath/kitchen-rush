# PhoneLLM full-task diagnostic

Runs **three predeclared development kitchens**, sizes 2/3/4 and seeds 74002/74003/74004, at **8,192 generated tokens each**. Uses the frozen engine, public state, candidates, one-action payload and invalid-action scoring; maximum 160 decisions. Model revision, SGLang 0.5.18, thinking disabled, temperature 0, one B200, four physical CPUs and 64 GiB are pinned. This is unranked: no dollar admission, dollar leaderboard, or B80 claim.

The separate runner leaves the release roster, campaign, core and original probe unchanged. It archives the exact source and streams request/response/action evidence before independently replaying completed episodes. Failed/missing episodes remain explicit; their quality is not substituted with zero. All hosted cost fields remain `null` until gross provider billing is reconciled.

```sh
# Offline plan; no Modal import, inference, or ledger write.
PYTHONPATH=src python scripts/phonellm_episode_diagnostic.py

# Paid launch requires a NEW one-use receipt and separately reserved session liability.
# Uses the same independently reviewed price/image/expiry/ledger guards as the probe.
python scripts/phonellm_episode_diagnostic.py --launch --receipt RECEIPT.json --out RUN_DIRECTORY

# Offline replay, including regenerated public inputs and selected actions.
PYTHONPATH=src python scripts/phonellm_episode_diagnostic.py --verify RUN_DIRECTORY
```

One dedicated sandbox runs all three kitchens sequentially, then terminates. The hard lifetime is 1,800 seconds; no automatic retry or recreation. Transport/accounting failure stops the remaining work. Preserve the full reservation until reconciliation. Divide the **entire gross session cost by three scheduled kitchens**, including cold startup, failed work, idle and shutdown; never divide only by successful episodes. This aggregate is retrospective and cannot establish that any action was admitted under a dollar allowance.

The existing probe spent 413.85 seconds before readiness, versus 1.48 seconds across its three HTTP requests. Its private `SandboxGetResourceUsage` observation implied only 5.82 GPU-seconds; those counters have unvalidated coverage/freshness and must not be treated as total billing. Official [billing reports](https://modal.com/docs/guide/billing) can arrive after collection delays, and the [report API](https://modal.com/docs/sdk/py/latest/Workspace) excludes partial final intervals. Neither proves a synchronous per-action dollar meter.
