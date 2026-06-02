# MULTI_AGENT — controlling 2+ chefs (design note, deferred phase)

Status: **design-only.** Nothing here is implemented. This documents the extension so the
architecture leaves room for it and we don't paint ourselves into a corner. It is a
deliberately *later* phase (see [ROADMAP.md](ROADMAP.md)).

## 1. What we mean

One model (a single "brain") controls **C ≥ 2 chefs** that share one kitchen and one team
**score**. This is multi-body / single-brain — the model must coordinate its own chefs. (A
separate, further-out variant is *multi-model* co-op or competition, one model per chef;
out of scope here.) Single-chef play is just the **C = 1** special case.

## 2. Why it belongs in *this* benchmark

It sharpens the core thesis rather than diluting it. Latency is charged **once per model
response** (RULES §3.2.4), but a response can dispatch actions to all C chefs. So a model
that coordinates C chefs per "think" gets ~C× the work done per unit of thinking time —
its *effective per-chef latency* drops. Multi-agent therefore directly measures **parallel
planning under latency**: the better you coordinate bodies per think, the more you amortize
your slowness. Coordinate badly (collisions, duplicated work, idle chefs) and you pay for
the extra bodies without the throughput.

## 3. Engine changes

| Concern | Single-chef (today) | Multi-chef |
|---|---|---|
| Position | `chef_pos` scalar | per-chef `position` (list of C) |
| Hands | one `hands` list | per-chef `hands` (list of C lists) |
| Grid / stations / burners / orders / clock / score | shared | **unchanged — still shared** |
| Action target | implicit | each call carries a `chef` index |

The shared resources (kitchen, clock, orders, score) do **not** change shape — only the
*actors* multiply. That is the seam to preserve: chef state must be indexable, with C=1 as
the degenerate case.

## 4. The one real decision: concurrent timing

Today the clock is a single cursor that advances per action. With C chefs acting at once,
"advance by the action's duration" is ambiguous. Two models:

- **(A) Synchronous turn — recommended first.** Each turn the model issues ≤1 action per
  chef. The engine advances the clock by the **max** of those action durations (parallel,
  not sum); chefs that finish early idle until the slowest completes. One latency charge per
  turn, fully deterministic, minimal change to the event sweep. Coarse (early-finishers
  waste time) but simple and faithful enough.
- **(B) Event-driven async — faithful real-time, much harder.** Each chef has its own
  *busy-until* timeline; the model is re-prompted whenever any chef becomes free, replanning
  only that chef. Rewards true pipelining (like real Overcooked) but raises hard questions:
  when exactly to query the model, how to charge latency per re-plan, and partial
  observability of in-flight chefs. Defer until (A) is proven.

This is the change with teeth: the clock goes from "one global cursor" to "per-agent busy
timelines + a shared event queue." Keeping all clock logic isolated in `engine.advance()`
(as it is now) is what makes this tractable later.

## 5. New concurrency rules to specify

- **Collisions.** Two chefs MUST NOT occupy the same cell. Resolve deterministically by
  ascending chef index (a blocked chef makes a partial/zero move, same as hitting a wall).
- **Station contention.** Board/plate are "1 in progress"; two chefs at the same station
  serialize or the later one is invalid. Burner sharing already exists (per-cell burners).
- **Hand-offs (the signature co-op mechanic).** Overcooked is built around *passing items
  across counters*. v1 deliberately dropped free-standing counter storage (hands-only,
  RULES §17.3) to kill stranded-item edge cases. True co-op wants it back as an optional
  shared **counter** station (place/pick-up). This is the biggest rule re-addition — but it
  is *optional*: chefs can parallelize independent dishes without ever handing off, so a
  first cut can skip counters and add them later.

## 6. Scoring, metrics, procgen

- **Score** stays a shared team accrual (RULES §9) — no formula change.
- **New diagnostics:** per-chef utilization / idle %, collisions, redundant/duplicated
  actions, work-balance, and **throughput-per-think** (to surface the amortization effect).
- **Procgen** can generate layouts that *require* coordination (split regions, a shared
  bottleneck, hand-off counters) as a difficulty knob (`num_chefs`, `coordination` tier).
- **Oracle** normalization becomes multi-agent scheduling (NP-hard in general) — use a
  heuristic upper bound or skip normalization for multi-chef at first.

## 7. Interface

- Add an optional `chef` integer arg to every action tool (default `0`); a chained response
  may address different chefs. Single-chef callers ignore it.
- The observation lists all chefs (`chefs: [{index, position, hands}, ...]`).
- The reference agent prompt explains it commands chefs `0..C-1` and should plan their
  moves jointly each turn.

## 8. What's already safe (no code written yet)

Nothing in Phase 1 blocks this:
- single-chef is the C=1 instance; chef state is conceptually a list of one;
- all clock/time logic is isolated in `engine.advance()`, ready to grow into per-agent
  timelines;
- the turn protocol already accepts **multiple chained tool calls** per response — adding a
  `chef` field is the only call-shape change.

We are intentionally **not** adding unused multi-chef plumbing now (that would be the
premature complexity we're avoiding). When the time comes, recommended order: model **(A)**
synchronous, no hand-offs → add hand-off counters → model **(B)** async.
