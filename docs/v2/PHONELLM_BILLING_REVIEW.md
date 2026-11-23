# PhoneLLM billing review — September 17, 2026

The provider reported **$1.93543341 gross usage** across the two earlier GPU attempts. This is observed billing before credits, not an invoice-final amount. Their **$10 permanent operational hold remains committed**; it is not an additional $10 paid charge and must not be added to observed billing as spending.

| Attempt | CPU | B200 | Memory | Reported gross | Retained hold |
| --- | ---: | ---: | ---: | ---: | ---: |
| Compatibility probe | $0.06731242 | $0.73927184 | $0.18168345 | **$0.98826771** | $5 |
| Full episodes, attempt 2 | $0.06475231 | $0.70833338 | $0.17408001 | **$0.94716570** | $5 |

Both read-only reports cover **08:00–09:00 UTC**, collected after 09:20 UTC. Each contains one app-level row; its resource costs sum exactly to its gross total. No credits were subtracted. The amounts are below the corresponding reservations and consistent with approximately seven minutes of the requested B200, four physical CPU cores, and 64 GiB RAM. Resource billing durations need not exactly match local wall time.

A separate bounded, app-filtered metadata read included finished sandboxes and exhausted pagination. Each dedicated app contained exactly the expected sandbox, with the expected immutable image, B200, 4,000 milli-CPU limit, 65,536 MiB limit, and 1,800-second lifetime. The launch sources create no other compute in these apps. The earlier local argument-validation failure created no sandbox. This supports attribution to the intended attempts; the reports themselves remain **app aggregates**, not per-sandbox invoices. Sandbox inventory does not independently prove that no non-sandbox compute ever ran in an app, or that all delayed billing has arrived.

Evidence: the [diagnostic evidence archive](../../data/v2/release-r2/evidence-diagnostics-20260917.zip) contains `gross-billing-fetch-v2.json` under the probe directory and `gross-billing-attempt2-v2.json` under the episode directory; [scoped metadata](../../data/v2/release-r2/phone-billing-app-scope-review.json) is published separately. Each fetch binds the original result and receipt hashes; the metadata review binds both billing hashes. Original failed-fetch and execution artifacts remain intact.

No ledger entries were changed. These amounts cover only these two attempts—not the later CPU transport check or durable recovery. Failed work remains included in campaign accounting; no per-request tariff, ranked dollar result, or B80 result follows from these app-level totals.
