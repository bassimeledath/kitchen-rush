# Checkpoint 2 — pilot results

**72 audited episodes; 1823 verified API requests.** These are pilot controls, not the full model leaderboard.

| Configuration | View | Allowance | Quality | 95% CI | Mean cost | Complete orders | Invalid calls |
|---|---|---:|---:|---|---:|---:|---:|
| Jev 1.13.0 | USD | 0.05 | 1.1 | 0.0–3.3 | $0.0064 | 28% | 16.5 |
| Jev 1.13.0 | USD | 0.15 | 1.1 | 0.0–3.3 | $0.0071 | 28% | 18.8 |
| Jev 1.13.0 | USD | 0.4 | 17.8 | 0.0–36.7 | $0.0060 | 33% | 13.7 |
| GPT-5.4 Mini · low | USD | 0.05 | 32.8 | 15.0–50.0 | $0.0403 | 33% | 0.3 |
| GPT-5.4 Mini · low | USD | 0.15 | 93.3 | 81.7–100.0 | $0.0929 | 94% | 0.3 |
| GPT-5.4 Mini · low | USD | 0.4 | 98.3 | 95.0–100.0 | $0.0946 | 100% | 0.5 |
| GPT-5.4 Mini · low | tokens | 512 | 0.0 | 0.0–0.0 | $0.0087 | 0% | 1.0 |
| GPT-5.4 Mini · low | tokens | 2048 | 10.0 | 0.0–30.0 | $0.0288 | 11% | 1.3 |
| GPT-5.4 Mini · low | tokens | 8192 | 86.7 | 63.3–100.0 | $0.1097 | 89% | 0.7 |
| Claude Sonnet 4.6 · thinking off | USD | 0.05 | 0.0 | 0.0–0.0 | $0.0123 | 0% | 0.0 |
| Claude Sonnet 4.6 · thinking off | USD | 0.15 | 11.1 | 0.0–33.3 | $0.1132 | 11% | 1.3 |
| Claude Sonnet 4.6 · thinking off | USD | 0.4 | 3.3 | 0.0–6.7 | $0.3582 | 22% | 8.5 |
| Claude Sonnet 4.6 · thinking off | tokens | 512 | 16.7 | 0.0–50.0 | $0.3494 | 33% | 10.3 |
| Claude Sonnet 4.6 · thinking off | tokens | 2048 | 45.6 | 0.0–93.3 | $0.4188 | 56% | 7.0 |
| Claude Sonnet 4.6 · thinking off | tokens | 8192 | 47.8 | 0.0–93.3 | $0.3923 | 56% | 7.0 |

Repeated trials are averaged within seed; intervals resample seeds. Small pilot samples cannot establish stable ranks. USD and token experiments are separate. Token cells stopped by the operational dollar backstop cannot establish B80.

The reservation policy can leave dollars unspent: it must cover a conservative input-token bound before sending another request. Especially at small allowances, these curves measure the configured model plus this spending policy. Review this effect before a release; do not interpret early stopping as the model choosing to stop. The zero floor also makes some episodes with completed orders score zero after invalid-action penalties; completion and invalid counts remain essential.

## B80

- Jev 1.13.0: N/A — generation-token comparability/enforcement not established.
- GPT-5.4 Mini · low: 8,192 tokens. Conservative B80: not reached.
- Claude Sonnet 4.6 · thinking off: Not reached within uncensored tested budgets. Conservative B80: not reached.

## Spending

This stage: $8.3096. Total accounted across stages: **$10.5326 / $50**. Recorded usage charges: $10.5138; permanently retained liability for abandoned attempts: $0.0187. No reserve is counted as a known invoice.

Jev cost uses its published input-token tariff; OpenRouter cost comes from response usage. Output tokens are recorded for both, including Jev probability output. No network timing enters the charts.

Protocol source hash: `b5054321ecbc7a3e69048f3d6aad761a5b0ffc94f9dfb0d356ab7d59196a8d35`.

![dollar-budget](assets/pilot-dollar-budget.png)
![cost-quality](assets/pilot-cost-quality.png)
![token-budget](assets/pilot-token-budget.png)
