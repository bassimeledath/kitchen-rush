# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/). Note that the **ruleset** is versioned
separately (`RULESET_VERSION`); any change to a scoring constant or recipe starts a new
leaderboard generation. See [docs/RULES.md](docs/RULES.md).

## [Unreleased]

### Added — gemini-3.5-flash reasoning-off (2026-07-03)
- New board row `gemini-3.5-flash` (reasoning **off**): **#4 at KR 25.6**, next to `gemini-3.1-flash-lite`
  (26.3). Its reasoning-on twin `gemini-3.5-flash·think` sits at #13 (KR 3.4) — an ~8× swing on the
  same weights, the cleanest single-model demonstration of the latency tax (its ~31k reasoning
  tokens/episode are charged and it can't afford them at these budgets).
- Reasoning-off is **impossible via OpenRouter** for this model ("reasoning is mandatory"), so it's
  routed through the **direct Gemini API** (`reasoning_effort=none`, verified to zero out reasoning
  tokens). `sweep.py` gained a `gemini35off` panel + direct-Gemini price. Board now 20 configs /
  960 episodes / $155.30.

### Fixed — GLM 5.2 was over-scored at B=5 (reasoning charged at zero) (2026-07-03)
- The 2026-06-18 GLM row ran reasoning **on** at B=5 but OpenRouter did not report its
  reasoning-token counts, so ~20k reasoning tokens/episode were priced at **zero** on the RP
  clock — inflating GLM to **2nd overall and tied for the B=5 lead (40.7)**. Neither honest
  config reproduces that: charged correctly, reasoning-on B=5 = **5.8** (reasoning overruns the
  budget); reasoning-**off** (its config re-run to match every other plain row) = **22.1** at B=5.
- Withdrew the inflated cells and replaced the GLM row with a fresh reasoning-off, both-budget,
  12-seed run (correctly charged): GLM is now **~21.0 overall, ~5th** (was 32.9/#2). README +
  charts + board updated; the reasoning-on collapse (5.8) is noted as the same latency-tax lesson
  as `claude-sonnet-5`. (Part of GLM's drop is also OpenRouter backend drift since June — its
  reasoning-off `medium B1` reproduced, but `hard B1` came in lower.)

### Added — claude-sonnet-5 (2026-06-30)
- New board row `claude-sonnet-5` (reasoning-off): lands **6th at KR 15.1**, below `gpt-5.4`,
  `gemini-3.1-flash-lite`, and `glm-5.2`. Confirmed a real behavioral failure (a "cook-spam
  spiral": `cook` ≫ `collect_cooked`, mass burns), not a harness artifact — every episode
  produced well-formed, correctly-parsed tool calls. README case study + charts updated
  (19 configs, 912 episodes, $150.89); `sweep.py` gained a `sonnet5` panel + price.
- Flagged Sonnet 5's *adaptive* thinking API as a reasoning-token metering edge case: it reports
  `reasoning_tokens: 0` while spending ~1000 hidden encrypted tokens/decision, so under the
  provider-trusted RP clock (RULES §3.2.1) a thinking-on run would think for free (inflated
  ~KR 44); charging the hidden tokens drops it to ~KR 7.6 at B=5, *below* reasoning-off. No
  thinking-on row published; documented in `docs/LIMITATIONS.md`.
- `build_board.py`: carry over out-of-band board rows (e.g. `glm-5.2`, committed straight to
  board.json with run data not in the repo) so a rebuild never silently drops them.

### Added — board patch (2026-06-11)
- Five new leaderboard rows via direct OpenAI/Anthropic keys + OpenRouter: `gpt-5.4`,
  `gpt-5.4-mini·think` (ties sonnet at B=5 for ~1/5 the cost), `claude-haiku-4.5`,
  `nemotron-3-super`, `nemotron-3-nano`; combined board at
  `leaderboard/results/board.{md,json}` (`scripts/build_board.py`); sweep runner grew
  `--panel` + direct-provider specs + `reasoning_effort none/minimal` modes.
- Documented unrunnable configs: gpt-oss-120b reasoning-off (provider: mandatory),
  nemotron-3-ultra (no `tool_choice:required` endpoint on OpenRouter).

### Changed — launch polish (2026-06-10)
- README leaderboard is now two charts (one per latency budget, 95% CIs —
  `scripts/plot_board.py` → `docs/assets/leaderboard_b*.png`) instead of a table, with a
  plain-language reading of what each budget means in deployment.
- KR is consistently spelled out as the **Kitchen Rush score**, introduced with a worked
  toy example (README + METHODOLOGY §1); "headline metric" jargon removed.
- Tagline order flipped to **accurate AND fast** (pyproject, package docstring, CITATION,
  repo description).

### Changed — realtime focus (2026-06-10)
- Kitchen Rush is now **one benchmark**: realtime tool calling under a user-selectable
  latency budget B. README rewritten around the why, the math of B (deadlines priced at
  `C(B) = A + K·B` with σ-headroom, plus the deployment-terms reading), and the starter-board
  results at B=1s / B=5s.
- METHODOLOGY: RP confirmed as the ranked track (experimental β), RT as the realism
  diagnostic; added §2.2 "Reading a B-board in deployment terms".

### Removed — realtime focus (2026-06-10)
- The zero-latency "KR-0" CLI mode (`--no-latency`) and all plans for a time-agnostic
  "intelligence" track: out of scope for the realtime product (explored on a side branch).
- Eight design-history docs (`SCORING`, `MOVEMENT`, `PROCEDURAL`, `INTERFACE`, `DESIGN`,
  `MIGRATION`, `MULTI_AGENT`, `ROADMAP`) — drifted from the implementation; RULES.md is the
  single spec and git history preserves the originals.

### Added
- Initial design and repository scaffold for Kitchen Rush v2 — a benchmark for **accurate
  AND fast** native tool calling, where **latency costs points by construction**.
- Full design docs: `RULES.md` (airtight deterministic ruleset) plus the original design
  suite (`SCORING`/`INTERFACE`/`PROCEDURAL`/`MOVEMENT`/`DESIGN`/`MIGRATION`/`ROADMAP`,
  since removed — see above) and `CONTAMINATION.md`.
- Module scaffolding for `engine/`, `procgen/`, `tools/`, `adapters/`, `harness/`,
  `report/`, `leaderboard/`, baselines, and tests (stubs pending implementation).

_This was the pre-alpha scaffold; the engine, procgen, scoring, adapters, replay viewer,
and starter leaderboard have since been implemented (see git history)._
