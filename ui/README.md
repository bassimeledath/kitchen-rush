# ui/ — Kitchen Rush replay viewer

Watch any recorded episode play back as a little pixel-art kitchen — and *see* the benchmark's
core idea: while the model "thinks", a 🤔 bubble appears, the clock keeps running, food keeps
cooking (and burning), and order timers keep draining. No build step, no dependencies — plain
HTML/CSS/JS.

## Run it

```bash
# 1. export a replay (no API key needed — this uses the built-in scripted chef)
python3 -m kitchenrush.cli replay --oracle --tier easy --seed 0 --latency 1.0
#   -> writes ui/replays/easy_seed0.json
#   (model run instead:  ... replay --model anthropic:claude-... --tier easy --seed 0)

# 2. serve this folder and open it
cd ui && python3 -m http.server 8000     # then open http://localhost:8000
```

The viewer loads your freshly exported `easy_seed0.json` if it exists, and otherwise falls back
to the bundled demos (a real model run and a perfect oracle run). You can also open `index.html`
directly and use the **load** button or just drag-and-drop any replay JSON onto the page (the
auto-loading needs the folder served over http, not `file://`).

**Controls:** space = play/pause · ← / → = step one action · Home = restart · the speed button
cycles 1× / 2× / 4× / 8× / 0.5× · drag the scrubber to seek.

## Replay file format (self-contained)

`kitchenrush replay` writes one JSON via `report.build_replay`:

- `meta` — seed, tier, the reference/null anchor scores, and the final score.
- `layout` — `grid_n`, `horizon_gs`, `latency_budget` (B), and every station
  `{type, ingredient, cell}`. The viewer renders whatever cells this lists, so changed maps
  just work.
- `catalog` — ingredients × states and recipes × components (tells the UI which icons exist).
- `frames` — the per-action timeline. Each frame: `kind` (`start`/`think`/`action`/`end`),
  `clock_gs`, `chef_pos`, `hands`, `burners` (with cook/burn timers), `orders`, `score`, the
  `action` taken, and `events` fired since the last frame (arrivals/ready/burns/expiries).

The viewer interpolates `chef_pos` between frames (so travel reads as walking) and snaps
discrete state at frame boundaries. The gap before each `think` frame is the deliberation
pause — that's the latency cost, made visible.

## Sprites

The viewer works out of the box with emoji fallbacks; the bundled pixel-art sprites in
`assets/` simply override them (via `assets/manifest.json`, which `app.js` auto-loads). To
regenerate or restyle the art:

```bash
export GEMINI_API_KEY=...
pip install pillow
python3 ui/assets/generate_sprites.py
```

The script generates sprite sheets with Gemini image generation, slices them, chroma-keys the
background to transparency, and rewrites the PNGs + `manifest.json` — no code edits needed.
Edit its `SPECS`/`STYLE` to tune art direction; `--only <keys>` regenerates a subset,
`--force` overwrites. `ui/sprites.js` documents the key naming (`station:TYPE`,
`ing:<name>:<STATE>`, `dish:<recipe>`, `chef`).
