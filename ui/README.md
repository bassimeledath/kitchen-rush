# ui/ — Kitchen Rush replay viewer

A dependency-free (vanilla HTML/CSS/JS) replay viewer that plays back a recorded episode on the
game clock and makes the **latency-costs-points** mechanic visible: the world clock advances (and
food burns / orders expire) while the model "thinks", shown as a *thinking +Xgs* pause before each
move. Faithful n×n grid, data-driven layout (random or fixed maps both just work), per-action
playback with scrub / step / speed.

## Run it

```bash
# 1. export a replay (no API key needed — uses the deterministic oracle)
python3 -m kitchenrush.cli replay --oracle --tier easy --seed 0 --latency 1.0
#   -> ui/replays/easy_seed0_oracle.json
#   (or export a model run:  ... replay --model anthropic:claude-... --tier easy --seed 0)

# 2. serve the folder and open it
cd ui && python3 -m http.server 8000     # then open http://localhost:8000
```

You can also just open `index.html` and use **load JSON** / drag-and-drop a replay file (the
built-in dropdown needs the folder served over http, not `file://`).

Controls: space = play/pause · ← / → = step one action · Home = restart · speed button cycles
0.5×/1×/2×/4× · drag the scrubber to seek.

## Replay file format (self-contained)

`kitchenrush.cli replay` writes one JSON via `report.build_replay`:

- `meta` — seed, tier, B, `s_ref`/`s_null`, final score.
- `layout` — `grid_n`, `horizon_gs`, and every station `{type, ingredient, cell}` (the viewer
  renders whatever cells this lists — change the map freely).
- `catalog` — ingredients × states and recipes × components (tells the UI which icons exist).
- `frames` — the per-action timeline. Each frame: `kind` (`start`/`think`/`action`/`end`),
  `clock_gs`, `chef_pos`, `hands`, `burners` (with cook/burn timers), `orders`, `score`, the
  `action` taken, and `events` fired since the last frame (arrivals/ready/burns/expiries).

The viewer interpolates `chef_pos` between frames (so travel reads as walking) and snaps discrete
state at frame boundaries. The gap before each `think` frame is the deliberation pause.

## Sprites

The viewer ships with emoji/CSS fallbacks, so it renders with no assets. Real sprites are
generated with Gemini "nano-banana" and dropped into `ui/assets/`:

```bash
export GEMINI_API_KEY=...
pip install pillow
python3 ui/assets/generate_sprites.py          # ~36 sprites: stations, chef, ingredients×state, dishes
```

The script writes the PNGs (background chroma-keyed to transparent) plus `assets/manifest.json`,
which `app.js` auto-loads and merges over the emoji — no code edits needed. Edit
`generate_sprites.py`'s `SPECS`/`STYLE` to tune art direction; `--only <keys>` regenerates a
subset, `--force` overwrites. `ui/sprites.js` documents the key naming (`station:TYPE`,
`ing:<name>:<STATE>`, `dish:<recipe>`, `chef`).
