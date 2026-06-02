# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/). Note that the **ruleset** is versioned
separately (`RULESET_VERSION`); any change to a scoring constant or recipe starts a new
leaderboard generation. See [docs/SCORING.md](docs/SCORING.md) and [docs/DESIGN.md](docs/DESIGN.md).

## [Unreleased]

### Added
- Initial design and repository scaffold for Kitchen Rush v2 — a benchmark for **fast
  AND accurate** native tool calling, where **latency costs points by construction**.
- Full design docs: `RULES.md` (airtight deterministic ruleset), `SCORING.md` (math +
  two latency tracks + interior-optimum argument), `INTERFACE.md` (model adapters, CLI,
  JSON schemas), `PROCEDURAL.md` (seeded generation, splits, oracle), `MOVEMENT.md`
  (grid + chained tool calls), `DESIGN.md`, `MIGRATION.md`, `ROADMAP.md`, `CONTAMINATION.md`.
- Module scaffolding for `engine/`, `procgen/`, `tools/`, `adapters/`, `harness/`,
  `report/`, `leaderboard/`, baselines, and tests (stubs pending implementation).

_This is a pre-alpha scaffold. The engine, procgen, scoring, and adapters are not yet
implemented — see [docs/ROADMAP.md](docs/ROADMAP.md)._
