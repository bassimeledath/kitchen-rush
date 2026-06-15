# CONTAMINATION — anti-overfitting & data-hygiene policy

Kitchen Rush is procedurally generated, so there is no fixed "test set" to memorize — but a
benchmark is only credible if models cannot be quietly tuned on the exact instances they are
scored on. This document states the policy. See [PROCEDURAL.md](PROCEDURAL.md) for the
generation algorithm and [RULES.md](RULES.md) §9 for how scores are computed (`SCORING.md` is archived design history).

## Seed bands (splits)

A single integer seed deterministically produces one game instance. Seeds are partitioned
into disjoint, published bands:

| Split | Visibility | Purpose |
|---|---|---|
| `train` | public | free for prompt/agent tuning and fine-tuning |
| `dev` | public | validation / ablation during development |
| `test` | public | standard reported number; reproducible by anyone |
| `challenge` | **maintainer-run, hidden** | headline / audit; generated from secret seeds |

Because generation is a pure function of `(seed, tier, GENERATOR_VERSION)`, anyone can
regenerate `train`/`dev`/`test`, but the `challenge` band's seeds are withheld and run by
maintainers for top submissions.

## Canary GUID

Every generated instance embeds a fixed **canary GUID** string in its serialized form. If
that GUID appears in a model's training data or outputs, it is evidence the eval instances
leaked into training. Do not train on text containing the canary.

## Versioning ties scores to rules

Each result is stamped with `RULESET_VERSION`, `GENERATOR_VERSION`, `SCHEMA_VERSION`,
`config_hash`, and `seeds_hash`. A submission only appears on the active headline board if
these match the current official split; changing any scoring constant or recipe bumps
`RULESET_VERSION` and starts a new board generation.

## Standard vs Custom submissions

Following τ-bench, the board separates **Standard** submissions (a general-purpose model on
the default scaffold/prompt) from **Custom** submissions (modified scaffolds, ensembles,
extra tools, or models trained on Kitchen-Rush-like data). Custom submissions must disclose
methodology. Training specifically on Kitchen Rush instances (any split) must be declared and
lands in the Custom track.

## Open governance questions

See `open_questions` in [ROADMAP.md](ROADMAP.md) — notably whether the headline number is the
public `test` split or the hidden `challenge` band, and the Pass^k sampling temperature.
