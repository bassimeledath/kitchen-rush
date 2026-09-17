# PhoneLLM durable recovery (completed September 17)

The bounded recovery finished with both missing kitchens replay verified and sandbox termination confirmed. Each scored 0; the preserved size-2 kitchen also scored 0. These three diagnostic results are unranked, use different kitchens from the shared canary, and cannot establish B80. The new $5 hosting reservation remains fully accounted while billing is unresolved. Evidence is under `runs/v2/phonellm-durable-recovery-20260917/execution/`.

Independent trace review found repeated invalid choices: size 3 made 12 invalid actions in 16 requests; size 4 made 17 in 22. All responses finished normally, and both games reached their terminal state. These zero scores reflect the recorded choices, not truncated responses or lost logs.

`phonellm_durable_recovery.py` is a separate version. It retains the completed size-2 kitchen (Q0) and schedules only the original size-3/4 development kitchens, seeds 74003/74004. The original model revision, settings, public state, candidates, action rules, 8,192-token allowance per attempt and 160-decision limit remain. The known size-3 prefix tokens remain accounted; unobserved usage after the truncated log is explicitly unknown. Recovered results are unranked and cannot establish B80. The cost denominator remains **three originally scheduled kitchens**, including all previous hosting costs and liabilities.

Full events, episode snapshots and the plan are written atomically inside the sandbox. The worker publishes a checksummed ZIP and a small ready file, then waits at most 120 seconds for a verified acknowledgment. The host uses Modal's documented filesystem `read_bytes`, `copy_to_local` and `write_bytes` methods, checks every payload hash, replays both completed episodes, and acknowledges only after verification. Stdout carries no trace payloads. Missing, partial, corrupt or failed evidence produces a nonzero CLI result. The 1,800-second sandbox limit includes startup, inference, transfer and shutdown; new inference stops starting after 1,300 seconds to preserve transfer time.

The dedicated application is `kitchenrush-phonellm-durable-recovery-v1`, isolating its bill from the previous application's pending reconciliation. The run used the existing image, one B200, four physical CPUs, 64 GiB, a 1,800-second lifetime and a separately reviewed receipt and reservation. There are no automatic retries. Its one-use receipt is consumed; do not rerun it.

```sh
# Offline plan and exact source binding.
python scripts/phonellm_durable_recovery.py

# Future paid execution requires a separately authorized fresh receipt/reservation.
python scripts/phonellm_durable_recovery.py --launch --receipt NEW_RECEIPT.json --out NEW_DIRECTORY
```

Offline checks cover the original scope, full replay, successful transfer/ACK, missing/partial/corrupt transfers, missing ready evidence, nonzero failure exit, and the installed Modal 1.5.2 method signatures and command validation under Python 3.10. These checks do not establish live filesystem behavior or billing. Original source archives and failed-run evidence remain unchanged.

## CPU transport check (passed September 17)

The live CPU test downloaded and verified a 3.4 MB archive, replayed both synthetic fixtures, acknowledged the verified archive and exited successfully. Sandbox termination is confirmed. Independent review checked the archived source, resource limits, checksums and absence of model inference. This validates the transport path, not PhoneLLM quality. The full $0.25 reservation remains accounted because billing is unresolved.

Evidence: `runs/v2/phonellm-cpu-transport-20260917/execution/{plan.json,result.json,source.zip,READY.json,artifacts.zip}` and the adjacent accounting disposition. The one-use receipt has been consumed; do not rerun it.

`scripts/phonellm_cpu_transport_check.py` exercises the same Journal, READY/ZIP, stdin upload, SDK filesystem download, checksum/replay verification and ACK path using scripted `wait(20)` responses. Runtime metadata explicitly says CPU and no inference. A deterministic 3 MiB incompressible padding member makes the ZIP larger than the observed truncated-log boundary; it is transport-only and never enters model inputs or token accounting. Only `kind=transport_selftest` permits this member and synthetic runtime metadata; production verification still requires the real GPU runtime.

The separate application uses the existing image, **one CPU, 1 GiB, 120 seconds, no GPU**, no model download, no new image build and no retry. Its own fresh reviewed receipt must bind a distinct `phonellm-cpu-transport-...` reservation of at least $0.01 and at most $0.25 in the existing $200 campaign ledger. The resource estimate is $0.0055308; actual charges remain unknown until reconciled. This guard does not weaken or reuse the GPU probe's $5 reservation rule. The helper never writes the ledger and terminates the sandbox in `finally`.

```sh
# Offline source-bound plan.
python scripts/phonellm_cpu_transport_check.py

# Only after independent review, a new reservation/receipt and explicit launch authorization.
python scripts/phonellm_cpu_transport_check.py --launch --receipt NEW_CPU_RECEIPT.json --out NEW_CPU_DIRECTORY
```
