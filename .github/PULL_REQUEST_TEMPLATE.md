<!-- For a leaderboard submission, fill this out. For code/docs PRs, delete it and describe your change. -->
## Leaderboard submission (if applicable)
- [ ] Added `leaderboard/submissions/<org>_<model>_<date>.json` (validates against `submission.schema.json`)
- [ ] Added a line to `leaderboard/manifest.json`
- [ ] `submission_type`: standard / custom
- [ ] `versions.ruleset` matches the current generation
- [ ] Ran the **full** frozen seed split (no seed/trial filtering), ≥4 trials
- [ ] Trajectory artifacts linked + sha256 recorded (publicly downloadable)
- [ ] Model is publicly accessible (open-weights or public API)
- [ ] Contamination declaration included (no training on instances; canary absent)

Trajectory link:
Notes / custom-harness changes (if `custom`):
