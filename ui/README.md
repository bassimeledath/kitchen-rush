# ui/ — Kitchen Rush replay viewer

Watch any recorded episode play back as a little kitchen — and *see* the benchmark's
core idea: while the model "thinks", a 🤔 bubble appears, the clock keeps running, food keeps
cooking (and burning), and order timers keep draining. No build step, no dependencies — plain
HTML/CSS/JS.

## Race mode (up to 4 models, one shared clock)

Replays of the same `(seed, tier, B)` play the *identical* kitchen and order stream, so the
viewer can run several side by side in perfect sync — you watch one model still thinking while
another is already plating:

```
http://localhost:8000/?replays=replays/easy_seed1_gemini35flash.json,replays/easy_seed1_oracle.json&labels=gemini-3.5-flash,reference
```

- `?replays=` takes 1–4 comma-separated replay JSONs (or multi-select / drag-drop several
  files onto the page); `?labels=` optionally names the panes; `&autoplay` starts playback.
- With 2+ panes the order tickets merge into one **shared rail** (the orders are identical),
  with one status pip per model on each ticket, and each pane gets a header with its label,
  live **rank badge**, and score. Panes whose episode ends early dim and read "finished".
- With 3–4 panes the per-pane fx floaters and captions switch off to keep it readable —
  think bubbles always stay (they're the point). Four panes arrange as a 2×2 wall.
- Replays from *different* instances still load, but a warning banner tells you the race
  isn't aligned.

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

The viewer uses a single Midnight Food Truck art direction. Its complete 49-key sprite manifest
lives in `assets/`; the art layer does not change replay data, station placement, timers, or
scoring. Emoji fallbacks keep every entity readable if an image fails to load.

To regenerate the sprite set:

```bash
export GEMINI_API_KEY=...
pip install pillow
python3 ui/assets/generate_sprites.py
```

The script generates sprite sheets with Gemini image generation, slices them, chroma-keys the
background to transparency, and rewrites the PNGs + `manifest.json`. `ui/sprites.js` documents
the key naming (`station:TYPE`,
`ing:<name>:<STATE>`, `dish:<recipe>`, `chef`).
