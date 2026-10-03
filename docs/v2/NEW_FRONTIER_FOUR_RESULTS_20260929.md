# Kitchen Rush: GPT-6.1 Sol and Claude Sonnet 5.5

Frozen Easy → Medium → Hard single-chef kitchens; 12 assigned kitchens per level and model setting. All episodes start from the beginning. Model settings run independently on the same seeds. The first two metrics charge every **scored** attempt but credit only fully cleared kitchens. Provider and operational failures remain unscored. Intervals, when shown, are exploratory 95% kitchen bootstrap intervals over scored episodes; they do not account for missing provider outcomes.

| Level | Model setting | Scored / 12 | Full clears | Orders served | Generation tokens / finished order | Frozen USD / finished order | Invalid decisions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Easy | gpt-6-1-sol-low | 11/12 | 11/11 | 33/33 (100.0%) | 199 | $0.0601 | 0/287 (0.0%) |
| Easy | gpt-6-1-sol-high | 10/12 | 10/10 | 30/30 (100.0%) | 827 | $0.0655 | 0/257 (0.0%) |
| Easy | claude-sonnet-5-5-low | 12/12 | 1/12 | 7/36 (19.4%) | 12,150 | $2.4893 | 19/639 (3.0%) |
| Easy | claude-sonnet-5-5-high | 11/12 | 4/11 | 21/33 (63.6%) | 9,056 | $0.5114 | 8/456 (1.8%) |
| Medium | gpt-6-1-sol-low | 0/12 | 0/0 | 0/0 (pending) | pending | pending | 0/0 (—) |
| Medium | gpt-6-1-sol-high | 0/12 | 0/0 | 0/0 (pending) | pending | pending | 0/0 (—) |
| Medium | claude-sonnet-5-5-low | 12/12 | 0/12 | 1/36 (2.8%) | no kitchen completed | no kitchen completed | 36/436 (8.3%) |
| Medium | claude-sonnet-5-5-high | 12/12 | 0/12 | 1/36 (2.8%) | no kitchen completed | no kitchen completed | 16/430 (3.7%) |
| Hard | gpt-6-1-sol-low | 0/12 | 0/0 | 0/0 (pending) | pending | pending | 0/0 (—) |
| Hard | gpt-6-1-sol-high | 0/12 | 0/0 | 0/0 (pending) | pending | pending | 0/0 (—) |
| Hard | claude-sonnet-5-5-low | 3/12 | 0/3 | 2/18 (11.1%) | no kitchen completed | no kitchen completed | 15/340 (4.4%) |
| Hard | claude-sonnet-5-5-high | 3/12 | 0/3 | 12/18 (66.7%) | no kitchen completed | no kitchen completed | 4/293 (1.4%) |

## Matched low-versus-high results

Each pair below uses the same kitchen seed. A pair counts only when both effort settings have a scored game ending.

| Level | Model | Scored pairs | High served more / Low served more / Ties | High full clears / Low full clears | High minus low total frozen USD |
| --- | --- | ---: | ---: | ---: | ---: |
| Easy | GPT-6.1 Sol | 10/12 | 0 / 0 / 10 | 10 / 10 | $+0.1680 |
| Easy | Claude Sonnet 5.5 | 11/12 | 8 / 1 / 2 | 4 / 1 | $-0.6098 |
| Medium | GPT-6.1 Sol | 0/12 | 0 / 0 / 0 | 0 / 0 | $+0.0000 |
| Medium | Claude Sonnet 5.5 | 12/12 | 1 / 1 / 10 | 0 / 0 | $+1.0875 |
| Hard | GPT-6.1 Sol | 0/12 | 0 / 0 / 0 | 0 / 0 | $+0.0000 |
| Hard | Claude Sonnet 5.5 | 3/12 | 3 / 0 / 0 | 0 / 0 | $-0.0537 |

## Coverage and accounting

81/144 episodes attempted; 74 scored, 0 still running, 7 unscored. The campaign ledger accounts for $48.086903 of its $189.00 inference cap, including $4.358144 currently reserved or retained liability. This is local accounting, not a provider invoice.

Unscored attempted episodes:

- easy 92010 claude-sonnet-5-5-high: quarantined (provider_or_accounting_failure)
- hard 90606 claude-sonnet-5-5-high: quarantined (provider_or_accounting_failure)
- hard 90606 claude-sonnet-5-5-low: quarantined (provider_or_accounting_failure)
- easy 92010 gpt-6-1-sol-high: quarantined (provider_or_accounting_failure)
- easy 92011 gpt-6-1-sol-high: quarantined (provider_or_accounting_failure)
- easy 92011 gpt-6-1-sol-low: quarantined (provider_or_accounting_failure)
- medium 92000 gpt-6-1-sol-low: quarantined (provider_or_accounting_failure)
