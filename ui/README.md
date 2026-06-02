# ui/ — replay & leaderboard dashboard (placeholder)

This folder is a deliberate placeholder. The UI is **lower priority than the game engine**
and will be designed later (see [../docs/ROADMAP.md](../docs/ROADMAP.md), Phase 4).

Intended scope (subject to change):

- **Trajectory replay** — step through a recorded JSONL run: the grid, the chef's moves, each
  response's tool calls, the game clock, cook/burn timers, order countdowns, and the running
  score. Make the latency-costs-points mechanic *visible* (watch food burn while the model
  thinks).
- **Run comparison** — baseline vs candidate on the same seeds.
- **Leaderboard view** — render `leaderboard/leaderboard.json`.

The hackathon repo's `ui/` (visual Kitchen Rush replay + assets) is a useful reference and
lives at `bassimeledath/kitchen-rush-hack`. Nothing here is implemented yet.
