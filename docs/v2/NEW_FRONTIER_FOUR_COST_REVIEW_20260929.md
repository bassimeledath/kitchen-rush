# Cost review: GPT-6.1 Sol and Sonnet 5.5 pilot

The original campaign is stopped. These amounts come from its frozen local request ledger (`spend.sqlite`) and independently replayed episode artifacts. They are provider-reported charges, not an external invoice. The 402 response and OpenRouter's credit endpoint show insufficient balance for another 32,768-output-token request. No continuation calls have been made.

| Model setting | Settled charges | Maximum unconfirmed failed-request liability | Requests | Scored episodes |
| --- | ---: | ---: | ---: | ---: |
| GPT-6.1 Sol low | $2.492658 | $1.245184 | 292 | 11 |
| GPT-6.1 Sol high | $2.628577 | $1.245184 | 284 | 10 |
| Sonnet 5.5 low | $19.464300 | $0.622592 | 1,463 | 27 |
| Sonnet 5.5 high | $19.143224 | $1.245184 | 1,232 | 26 |
| **Total** | **$43.7287585** | **$4.358144** | **3,271** | **74** |

Sonnet accounts for $38.607524, or 88.3% of settled charges. It made 2,695 of the 3,271 requests. The benchmark asks for one game action per model call and resends the evolving kitchen state each time. A scored Sonnet Hard episode averaged about 100 decisions and roughly $2 in reported charges. Sonnet high generated more output tokens, but its fewer decisions made its total charges similar to Sonnet low.

## Cost-per-order display correction

The frozen **normalized** dollar metric priced every input token at $2/M and every output token at $10/M. OpenRouter marked almost every GPT-6.1 Sol input token as a **cache write**, charged at $2.50/M. Every saved response's reported charge reconciles exactly to the published prompt/cache-write/output rates. Sonnet had no cache-write tokens, so its normalized and reported dollar figures match.

| Easy setting | Normalized frozen USD / finished order | Actual provider-reported USD / finished order |
| --- | ---: | ---: |
| GPT-6.1 Sol low | $0.060106 | **$0.074621** |
| GPT-6.1 Sol high | $0.065497 | **$0.079792** |
| Sonnet 5.5 low | $2.489287 | **$2.489287** |
| Sonnet 5.5 high | $0.511381 | **$0.511381** |

The original frozen metric is reproducible but should be labeled **normalized list-price dollars**, not actual spending. Before a broader roster, the cost metric should explicitly account for cache-write/read prices or show actual provider-reported cost alongside the normalized comparison. No original scores or traces were overwritten.

The combined local accounting maximum is $48.0869025: settled charges plus retained liability. Retained liability is deliberately conservative and **is not a confirmed bill**. The OpenRouter balance at the pause was about negative $0.07 after delayed usage posting; the pilot consumed most, but the ledger alone does not identify every other charge on the shared account.

Seventy fixed assignments lack a scored result: 51 GPT-6.1 Sol assignments and 19 Sonnet 5.5 assignments. No further paid work should start until the user decides whether the remaining information is worth its cost. The prepared continuation has a separate $100 inference ceiling, which would make original plus continuation accounting at most $148.0869025, below the earlier $189 inference ceiling. That cap is a safety limit, **not a prediction or spending commitment**. Hard-level GPT-6.1 Sol cost is still unmeasured in this campaign.
