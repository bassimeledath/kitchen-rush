> Historical r1 review. The current r2 roster adds an explicit Gemini minimal migration, raises the shared call ceiling to 16,384, and includes nested tariff/cache-duration maxima. Run `python scripts/audit_roster_v2.py` for its independent offline checks.

# Independent roster review — 2026-09-16

**Verdict: the roster is preserved, but it is not ready for an automatic full sweep.**
No paid calls or credentials were used in this review. The three source-board hashes match
the original checkout. The verified `board_b10.json` source is now archived at
`data/v2/provenance/board_b10.json`. All 29 labels from the three boards and their excluded-entry maps are present. The three
additional labels are GLM 5.3 Flash, Jev and PhoneLLM. The declared 30 configurations are a v2
migration policy, not proof that 32 historical labels contained only 30 identical settings.

## Confirmed issues and resolutions

- OpenRouter's current public model metadata makes Gemini 3.5 Flash reasoning mandatory.
  Its legacy off row cannot use the current OpenRouter wrapper. Preserve it as blocked,
  test a direct Gemini adapter, and require explicit off support and usage evidence.
  Google's current thinking documentation lists minimal/low/medium/high, not off, for
  this model; historical LiteLLM success is insufficient evidence today.
- Luna's current supported efforts omit `minimal`; DeepSeek Flash lists `high`/`xhigh`,
  not the old thinking row's `low`. Either verify an explicit legacy mode through a
  supported route, or disclose migration to Luna low and DeepSeek high in the new row.
  Never silently label a changed configuration as unchanged.
- Sonnet 5 now defaults to thinking on/high in public metadata. Freeze thinking disabled
  to preserve the intended old row; do not rely on the stale script's default-off comment.
- The calibrated `gpt-oss-120b` row used low at B1 and medium at B5; `gpt-oss-120b·think`
  used low. Freeze low throughout v2, keep both historical labels in the migration map,
  and describe consolidation explicitly. Grok's two labels can share its mandatory default.
- Mini pilot used low reasoning. It belongs to the thinking configuration. The old plain
  Mini label is ambiguous between boards: keep explicit off and low v2 configurations.
- GLM's historical script mentions mixed off/low, but the **current** board records
  `glm_offboth` and the calibrated board is off at both budgets. Freeze off in v2.
- Endpoint tags and provider display names must be different fields. The current wrapper
  uses one field both in routing and identity checks. Exact tags matter for regions,
  quantization, price and service tiers. Preserve `allow_fallbacks=false` and
  `require_parameters=true`; a route's schema support does not prove its effort support.

These checks use [OpenRouter model metadata](https://openrouter.ai/api/v1/models),
[reasoning configuration documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens),
[provider routing documentation](https://openrouter.ai/docs/guides/routing/provider-selection),
and [Google's thinking documentation](https://ai.google.dev/gemini-api/docs/thinking).

## Recommended explicit freeze

This is a proposal to encode and canary, not a claim of API compatibility. `off` means
`reasoning.enabled=false`; named efforts mean `reasoning.effort=<value>`; `native` means
omit unsupported reasoning parameters. All generated configurations use a common 2,048
maximum completion tokens per call, further constrained by the episode token allowance.
This ceiling includes thinking and must be disclosed; failures to support it are adapter
blocks, not zero quality. Omit temperature consistently and archive provider defaults.

| v2 configuration / retained label | Exact route tag | Reasoning |
|---|---|---|
| claude-haiku-4.5 | anthropic | off |
| claude-sonnet-4.6 | anthropic | off |
| claude-sonnet-4.6·think† | anthropic | low |
| claude-sonnet-5 | anthropic | off; verify |
| deepseek-v4-flash | deepinfra/fp8 | off |
| deepseek-v4-flash·think | deepinfra/fp8 | high; explicit migration from low |
| deepseek-v4-pro | deepinfra/fp8 | off |
| gemini-3.1-flash-lite | google-ai-studio | off; verify |
| gemini-3.5-flash | direct Gemini, pending verification | off; blocked until verified |
| gemini-3.5-flash-lite | google-ai-studio | minimal |
| gemini-3.5-flash·think | google-ai-studio | medium |
| gemini-3.6-flash·low | google-ai-studio | low |
| gemini-3.7-flash·low | google-ai-studio | low |
| glm-5.2 | deepinfra/fp4 | off |
| glm-5.3-flash | deepinfra/fp4 | low |
| gpt-5.4 | openai | none |
| gpt-5.4-mini | openai | none |
| gpt-5.4-mini·think | openai | low |
| gpt-5.4·think | openai | low |
| gpt-5.6-luna | openai | low; explicit migration from minimal |
| gpt-oss-120b·think (alias gpt-oss-120b) | groq | low; explicit consolidation |
| grok-build·think (alias grok-build-0.1) | xai | mandatory native default |
| jev | TypeSafe direct, jev-1.13.0 | typed Choice; no generation-token rank |
| llama-4-scout | deepinfra/fp8 | native non-reasoning; omit parameter |
| mistral-small | mistral | off |
| nemotron-3-nano | crusoe/fp8 | off |
| nemotron-3-super | dekallm/fp8 | off |
| nemotron-3-ultra | deepinfra/fp4 | off |
| phonellm-alpha-1† | dedicated Modal/SGLang; pending | off; billing and adapter blocked |
| qwen3.7-plus | alibaba | off |

Display identities for these tags: `anthropic`→Anthropic, `openai`→OpenAI,
`google-ai-studio`→Google AI Studio, `deepinfra/*`→DeepInfra, `groq`→Groq,
`xai`→xAI, `mistral`→Mistral, `crusoe/fp8`→Crusoe, `dekallm/fp8`→DekaLLM,
`alibaba`→Alibaba. Read tariffs/context limits from the matching endpoint object, not
model-wide cheapest pricing. Selected saved endpoint objects have no separate request fee;
web-search fees are irrelevant because search is not enabled. Still reject new nonzero
request fees unless accounted. Cache discounts may reduce bills; never presume them for
operational liability. Context ceilings vary from 131,072 (Groq OSS) to 1,050,000 (GPT 5.4/Luna).

## PhoneLLM

The original checkout has `scripts/modal_phonellm_sglang.py`: pinned model revision
`8e76aaa6e8ce4765ac943ba3fb339494d4d48dca`, SGLang v0.5.18-cu130, one B200,
concurrency target 4 and zero minimum containers. This stack is absent from the v2
worktree. There is no verified per-request billing adapter here. The old LiteLLM adapter
silently drops parameters and retries, so it cannot simply be reused for strict v2.

A defensible self-hosted cost requires measured container resource time and allocation of
startup, warmup, idle time, overlapping requests, CPU/RAM and storage; client response
latency alone is insufficient. Modal lists B200 at $0.001736/second, before other resources.
A 30-minute startup allowance can already expose $3.1248 GPU liability, exceeding a $0.40
canary allowance. Reserve deployment/session liability separately, cap concurrency and
container lifetime, and reconcile actual resource usage. Credits must not make the benchmark
cost zero. Until that exists, retain an explicit unavailable dollar row and do not invent
an equivalent per-token price. See [Modal pricing](https://modal.com/pricing).

## Staged spend — corrected boundary accounting

Prior accounted spend is **$10.532554644**. The following is a **nominal allocation, not a
guarantee of completing this coverage** when the admission rule allows a paid request to
cross an episode dollar boundary:

| Work | Nominal allocation |
|---|---:|
| Corrected fresh control validation | $10 |
| One canary per configuration at $0.40 | $12 |
| All 30 × six fresh kitchens × dollar allowances .05/.15/.40 | $108 |
| Fresh confirmation | $30 |
| Recovery and special hosting setup | $20 |
| Total, including prior spend | **$190.532554644** |

This leaves $9.467445356 unassigned. Dispatch the six-kitchen primary grid as two matched
three-kitchen rounds ($54 nominal episode allowances each), with an audit before round two. Preserve PhoneLLM
and unsupported rows as explicit pending entries; their matrix capacity stays reserved.
A hosted canary's setup exposure belongs in recovery, not hidden within its episode cap.
Do not proceed with hosting if an enforceable allocation/lifetime bound cannot fit.

A crossing request can be charged even when its action is excluded from the scored prefix.
Its cost is real. For episode allowance B and validated maximum request cost U, a one-boundary-
request policy may spend less than B + U, not necessarily at most B. Thus the displayed
$190.532554644 is not a worst-case bound. This correction replaces my earlier guarantee.

The frozen roster's conservative context-size bounds sum to **$29.5314312 per one request
from each of the 27 OpenRouter configurations**. At one extra boundary call for each of
18 primary episodes plus one canary per configuration, this allows **$561.0971928** of
additional conservative liability, before unbounded/blocked Jev, direct Gemini and hosted
PhoneLLM cases. This is deliberately loose and is not a forecast of invoices; it proves
that the simple sum of episode allowances does not establish the claimed guarantee.
Cache-write premiums are included in those input-price maxima.

Use two separate ledgers/limits: scored episode allowances and operational project liability.
Before dispatch, reserve every request against the $200 global cap, retain uncertain charges,
and charge boundary responses. Before promising a complete balanced block, establish a
validated finite bound for the block including boundary calls and hosting overhead. If a
full block cannot fit that bound, reduce the announced coverage *before* dispatch, improve
its valid request bound, or treat collection as conditional and mark missing cells explicitly.
A model must never receive quality zero because the experimenter's operational funds ran out.
There is no honest unconditional six-kitchen guarantee from the current reservation evidence.

**Universal uncensored B80 cannot be guaranteed within this budget.** Even the old
28-model token plan (three kitchens × three allowances × $1 backstop) reserves $252.
Prioritize common dollar coverage. Spend actual underspend on a complete balanced token
block only after reserving the whole block, or publish B80 only for tested eligible models
and mark all other cells not tested. Do not select token-tested models because their main
scores look favorable. A token budget lowered merely to fit dollars must be reported as
cost-censored and cannot establish B80. The six-kitchen dollar sample is still preliminary;
uncertainty must accompany rankings. No existing result should be relabeled as a v2 rerun.

The machine-readable freeze is `data/v2/release-roster.json`; all 32 retained labels map to
30 configurations, with two explicit blocked rows and fresh public reasoning metadata in
`data/v2/provenance/release-openrouter-models.json`. No inference was run to produce it.
