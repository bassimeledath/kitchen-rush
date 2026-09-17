# Bounded PhoneLLM probe — prepared, not launched

`scripts/probe_phonellm_sandbox.py` is **unranked compatibility/accounting work**, separate from the frozen release. Its default command is offline. It does not alter the campaign ledger, roster, or provider adapter.

One B200 Sandbox requests **and limits** 4 physical CPU cores and 64 GiB RAM, with a **1,800-second maximum lifetime**. Base resource ceiling: `1800 × (0.001736 + 4×0.00003942 + 64×0.00000667) = $4.177008`; require a **$5 reserved campaign row**. The extra $0.822992 is operational margin, not measured expenditure. An existing immutable image avoids building; no persistent volume, snapshot, public endpoint, region pin, or separate warmup is created. Three sequential development-state requests use the existing public projection and complete syntactic action enum, including invalid actions. No inference retries or legality filter. This does not test complete episodes or establish a rank.

The deployed PhoneLLM app layout was read on **2026-09-17** and references image `im-KNuhx8x5CNGSM9Kp9F9Xw8`. The local deployment source names `lmsysorg/sglang:v0.5.18-cu130`; the API does not prove the historical registry tag. The probe uses the immutable image and fails before model loading unless SGLang is exactly `0.5.18` and one B200 is present. It records SGLang/PyTorch/CUDA/GPU metadata and loads model revision `8e76aaa6e8ce4765ac943ba3fb339494d4d48dca` explicitly. Memory sufficiency and strict output support remain untested.

**Verified account finding, 2026-09-17:** authenticated dashboard inspection found a provider usage limit that auto-raises and no manual spend limit. No settings changed. Do not treat that limit as a fixed probe ceiling. Installed Modal SDK/CLI **1.5.2** successfully read daily and hourly billing reports; its public settings API does not expose the usage cap. Billing requests subsequently hit rate limiting; no further reports were requested.

**Envelope evidence:** [Sandbox timeout/resource limits](https://modal.com/docs/sdk/py/latest/Sandbox), [Sandbox prices](https://modal.com/pricing), [default disk/resource billing](https://modal.com/docs/guide/resources), [region premiums apply when a region is specified](https://modal.com/docs/guide/region-selection). [Egress is not charged in September 2026](https://modal.com/docs/guide/network-egress-billing), but starts October 1. The launcher expires September 30 UTC with an additional lifetime check; re-audit prices/egress before any later use. The documented maximum lifetime supplies the resource bound. Public docs do not explicitly bound pre-start/post-exit billable intervals or shutdown slack: **$5 is a retained operational reserve, not a mathematical guarantee of the final invoice**. Exact billing boundaries still require reconciliation. No new image build, persistent storage or region charge is included in this envelope.

## Prepare and inspect (offline)

From the v2 worktree:

```sh
/Users/bassime/.local/share/uv/tools/modal/bin/python scripts/probe_phonellm_sandbox.py > /tmp/phonellm-plan.json
```

The project virtualenv has no Modal; the installed CLI interpreter above can also import the local benchmark. Review the plan, then the campaign owner must reserve $5 in the **authoritative current ledger**, accounting for all earlier spend. This script only reads that row and never settles/releases it. Do not make a standalone budget that forgets concurrent campaign liabilities.

Create a local receipt under `runs/` using actual values, not placeholders:

```json
{
  "plan_sha256": "FROM_OFFLINE_PLAN",
  "reserved_usd": 5,
  "ledger_path": "/absolute/path/to/authoritative/spend.sqlite",
  "reservation_id": "THE_NEW_RESERVED_ROW_ID",
  "workspace": "EXPECTED_ACTIVE_WORKSPACE",
  "existing_image_id": "im-KNuhx8x5CNGSM9Kp9F9Xw8",
  "resource_envelope_verified": true,
  "verified_at_unix": 0,
  "price_evidence_path": "/absolute/path/to/reviewed-local-evidence",
  "price_evidence_sha256": "SHA256_OF_THAT_FILE"
}
```

The evidence review must be within 15 minutes of launch. A receipt records the review; its hash is not proof of provider enforcement. Launch is an explicit paid action, **not performed during preparation**:

```sh
/Users/bassime/.local/share/uv/tools/modal/bin/python scripts/probe_phonellm_sandbox.py --launch --receipt runs/phonellm-receipt.json --out runs/phonellm-probe
```

One-use claim is keyed to the ledger reservation. Keep it after failure, timeout, interruption, or uncertain creation; do not copy/reuse the row to relaunch. The SDK internally reuses a per-call idempotency key on transport retries; the probe has no outer retry and names its one Sandbox. If creation is uncertain, retain the full reserve and inspect the named Sandbox; the remote lifetime limit still applies. The launcher records/attempts termination whenever it obtains a handle.

**Before ranking:** reconcile the full Sandbox lifecycle to a later closed-interval Modal report, including unsuccessful start, first inference, idle gaps and shutdown. The artifact's full-session wall-time estimate is diagnostic, not a bill or synchronous action-admission meter. Keep the full reserve until reviewed reconciliation; no invented per-token tariff and no automatic promotion into the leaderboard. A successful three-request probe still requires an episode adapter and a validated live hosting-cost allocation rule.
