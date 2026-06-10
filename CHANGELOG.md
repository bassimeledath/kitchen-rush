# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/). Note that the **ruleset** is versioned
separately (`RULESET_VERSION`); any change to a scoring constant or recipe starts a new
leaderboard generation. See [docs/RULES.md](docs/RULES.md).

## [Unreleased]

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
- Initial design and repository scaffold for Kitchen Rush v2 — a benchmark for **fast
  AND accurate** native tool calling, where **latency costs points by construction**.
- Full design docs: `RULES.md` (airtight deterministic ruleset) plus the original design
  suite (`SCORING`/`INTERFACE`/`PROCEDURAL`/`MOVEMENT`/`DESIGN`/`MIGRATION`/`ROADMAP`,
  since removed — see above) and `CONTAMINATION.md`.
- Module scaffolding for `engine/`, `procgen/`, `tools/`, `adapters/`, `harness/`,
  `report/`, `leaderboard/`, baselines, and tests (stubs pending implementation).

_This was the pre-alpha scaffold; the engine, procgen, scoring, adapters, replay viewer,
and starter leaderboard have since been implemented (see git history)._
