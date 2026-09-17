# Resume the recorded funding interruption

**Prepared, not activated.** The existing authorization covers these reruns once funding is restored; no new user approval is needed. Wait for refill confirmation and terminal accounting for every live campaign request. Do not poll funding before confirmation.

Only the recorded roster-canary Sonnet thinking HTTP402 and its Sonnet/Haiku peer pauses qualify. Each gets exactly one `--attempt2`, with the same kitchen, model, settings and allowance. Keep all original traces, paid prefixes and the **$6.24576 unknown liability**. Scores never qualify a job for retry; another failure needs review and cannot produce attempt three.

From the v2 worktree, using the project Python interpreter:

```sh
python scripts/release_v2.py funding-check --required-usd 6.24576
```

This makes two read-only OpenRouter GETs. Use the returned evidence path below. Evidence expires after five minutes; a later dispatch fetches its own fresh proof. Public evidence records sufficiency assertions, not balances or credentials, so readers cannot recompute private account balances from it.

```sh
funding_proof="runs/v2/release-r2/funding-preflights/RETURNED_ID.json"
funding_job="roster_canary-claude-sonnet-4-6-think-60100-3-0-d0.4-tNone"

python scripts/release_v2.py amend-funding-pause --batch roster_canary \
  --funded-evidence "$funding_proof" --note "Recorded funding interruption reviewed; funded proof verified."

python scripts/release_v2.py recover --batch roster_canary --job "$funding_job" \
  --funded-evidence "$funding_proof" --note "Exact HTTP402; all prefix costs and full unknown reserve retained."

for funding_peer in \
  roster_canary-claude-sonnet-4-6-60100-3-0-d0.4-tNone \
  roster_canary-claude-haiku-4-5-60100-3-0-d0.4-tNone
do
  python scripts/release_v2.py recover --batch roster_canary --job "$funding_peer" \
    --peer-job "$funding_job" --funded-evidence "$funding_proof" \
    --note "Exact dependent accounting pause; prefix retained and next reserve fits."
done

python scripts/release_v2.py run --batch roster_canary --workers 1
python scripts/audit_release_v2.py --batch roster_canary
```

The amendment and each recovery authorization are append-only; do not rerun already successful authorization commands. A claimed dispatch, incomplete replacement, insufficient funding or insufficient stage/global room halts progress. Do not delete claims or release liabilities to force a retry.

The new gate is **`roster_canary-funding-gate.json`**. Existing campaign, manifests, source archives and gates remain unchanged. Reporting separates the funding pause from provider failures, retains interrupted partial quality as unranked, and includes every attempt's known costs plus unknown liability. Replacement-affected token cells remain excluded from B80.

Validation: focused scope/hash/chain/funding/retained-liability/peer-room/one-retry tests and the existing release-audit regression pass. An independent budget review also verified restoration of the amended archive in a fresh process. No new amendment, recovery disposition, funding fetch or paid dispatch was performed during preparation.
