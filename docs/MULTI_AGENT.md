# MULTI_AGENT — controlling 2+ players (design note, deferred)

Status: **design-only, simple.** One LLM controls 2 (or more) chefs in the same kitchen.
Each turn the model decides what *each* chef does. That's it. Single-chef play is just the
`C = 1` case.

## Why it's interesting (and cheap)

Latency is charged **once per model response**. A response can already contain several
chained tool calls — so the model just issues some for chef 0 and some for chef 1 in the
same turn. One "think" drives two bodies, so coordinating more chefs amortizes thinking
time. Good test, almost no new machinery.

## The whole change

1. **State:** `chef_pos`/`hands` become a list — one entry per chef. Everything else (grid,
   stations, burners, orders, clock, score) stays shared and unchanged.
2. **Tools:** add an optional `chef` index (default `0`) to each action call. A chained
   response may mix chefs, e.g. `[collect(bun, chef=0), move(north, 2, chef=1), cook(patty, chef=1)]`.
3. **Execution:** unchanged. Calls run in order on the one shared clock, exactly as today;
   each call just applies to the chef it names. Latency is charged once for the whole turn.
4. **Observation:** show a `chefs: [{index, position, hands}, ...]` list instead of a single
   chef. The prompt says "you control chefs 0..C-1."
5. **Scoring:** unchanged — one shared team score.

That's the minimal version, and it's enough.

## Deliberately *not* doing (unless we later find we need it)

- No simultaneous/parallel timing (max-vs-sum) — sequential on the shared clock is fine.
- No hand-off counters, no special collision physics — two chefs may share a cell; keep it
  simple. Add rules only if play shows they matter.

## Already safe

Nothing in Phase 1 blocks this: chef state is conceptually a list of one, and the turn
protocol already accepts multiple chained calls — adding a `chef` field is the only
call-shape change. We add no unused plumbing now.
