#!/usr/bin/env python3
"""Generate Kitchen Rush UI sprites with Gemini "nano-banana" (image generation), SHEET-based.

Each "sheet" is a single generation: a grid of related sprites on a flat magenta field, in one
consistent style. We then slice the grid into cells, chroma-key the magenta to transparency,
trim/square/resize each, and write PNGs + a ``manifest.json`` the viewer auto-loads. A locked
**style anchor image** is passed as a reference to every sheet so the whole set matches.

Workflow:
    export GEMINI_API_KEY=...
    pip install pillow
    # dry-run the slicer on one sheet first:
    python3 ui/assets/generate_sprites.py --anchor /tmp/style_C_bold.png --only stations
    # then the rest:
    python3 ui/assets/generate_sprites.py --anchor /tmp/style_C_bold.png

Each run also drops /tmp/sheet_<name>.png (raw) and /tmp/sheet_<name>_sliced.png (per-cell
preview) so you can verify slicing before trusting it.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ASSETS = Path(__file__).resolve().parent

STYLE = ("Bold cute pixel-art style: thick solid black outlines, flat vibrant candy colors, "
         "minimal shading, clean chunky 16-bit shapes.")

SHEET_PROMPT = (
    "A {cols}x{rows} grid sprite sheet ({cols} columns, {rows} rows) on a SOLID FLAT pure-magenta "
    "#FF00FF background. Match the EXACT art style, palette, outline weight and shading of the "
    "provided reference image. {style} Each grid cell holds exactly ONE item, centered, drawn at a "
    "CONSISTENT scale, with WIDE magenta gutters so items never touch or overlap cell edges. "
    "No text, no labels, no faces on food. Items in reading order (left to right, top to bottom):\n"
    "{items}\n"
    "Leave any remaining cells completely empty (solid magenta)."
)

# Each sheet: name -> (cols, rows, [ (manifest_key, filename, subject), ... ])
SHEETS: dict[str, tuple[int, int, list[tuple[str, str, str]]]] = {
    "stove_fx": (2, 2, [
        ("station:STOVE", "station_stove.png",
         "a flat TOP-DOWN kitchen cooktop tile seen straight from above: a dark metal counter surface "
         "with one round recessed burner ring in the centre, unlit, NO oven door, NO side perspective box"),
        ("fx:flame",  "fx_flame.png",  "a small stylized cooking flame, blue base fading to orange tip, single flame"),
        ("fx:smoke",  "fx_smoke.png",  "a small puff of dark grey smoke cloud"),
        ("fx:burst",  "fx_burst.png",  "a bright yellow four-point sparkle star burst"),
    ]),
    "stations": (3, 2, [
        ("station:ING",   "station_ing.png",   "a wooden pantry shelf stacked with ingredient crates"),
        ("station:BOARD", "station_board.png", "a wooden cutting board with a chef's knife"),
        ("station:STOVE", "station_stove.png", "a metal stove range with two burners and blue flames"),
        ("station:PLATE", "station_plate.png", "a neat stack of clean round white plates"),
        ("station:PASS",  "station_pass.png",  "a serving pass window with a silver order bell"),
        ("station:BIN",   "station_bin.png",   "a metal kitchen trash bin"),
    ]),
    "chef_face": (4, 1, [
        ("chef:front", "chef_front.png", "a cute chef with a tall white hat and red neckerchief facing the viewer, walking pose, empty hands"),
        ("chef:back",  "chef_back.png",  "the SAME chef seen from behind facing away, walking pose, empty hands"),
        ("chef:left",  "chef_left.png",  "the SAME chef in side profile facing left, walking pose, empty hands"),
        ("chef:right", "chef_right.png", "the SAME chef in side profile facing right, walking pose, empty hands"),
    ]),
    "chef_carry": (4, 1, [
        ("chef:carry:front", "chef_carry_front.png", "the SAME cute chef facing the viewer, holding an EMPTY round serving tray forward with both hands"),
        ("chef:carry:back",  "chef_carry_back.png",  "the SAME chef seen from behind, holding an EMPTY round serving tray forward"),
        ("chef:carry:left",  "chef_carry_left.png",  "the SAME chef in side profile facing left, holding an EMPTY round serving tray forward"),
        ("chef:carry:right", "chef_carry_right.png", "the SAME chef in side profile facing right, holding an EMPTY round serving tray forward"),
    ]),
    "dishes": (5, 1, [
        ("dish:burger",                "dish_burger.png",                "a classic hamburger with bun and patty on a white plate"),
        ("dish:soup",                  "dish_soup.png",                  "a bowl of hot soup on a white plate"),
        ("dish:salad",                 "dish_salad.png",                 "a green salad with lettuce and tomato on a white plate"),
        ("dish:mushroom_cheeseburger", "dish_mushroom_cheeseburger.png", "a cheeseburger topped with mushrooms and melted cheese on a white plate"),
        ("dish:veggie_ramen",          "dish_veggie_ramen.png",          "a bowl of veggie ramen with noodles, egg and broth on a white plate"),
    ]),
    "veg": (4, 2, [
        ("ing:bun:RAW",        "bun_raw.png",        "a golden sesame-seed hamburger bun"),
        ("ing:cheese:RAW",     "cheese_raw.png",     "a square orange slice of cheddar cheese"),
        ("ing:lettuce:RAW",    "lettuce_raw.png",    "a whole round head of green lettuce"),
        ("ing:lettuce:CHOPPED","lettuce_chopped.png","a small tidy pile of shredded green lettuce"),
        ("ing:tomato:RAW",     "tomato_raw.png",     "a whole shiny red tomato"),
        ("ing:tomato:CHOPPED", "tomato_chopped.png", "a few round red tomato slices"),
        ("ing:onion:RAW",      "onion_raw.png",      "a whole yellow onion"),
        ("ing:onion:CHOPPED",  "onion_chopped.png",  "a small tidy pile of diced white onion"),
    ]),
    "cook_a": (3, 2, [
        ("ing:patty:RAW",    "patty_raw.png",    "a round raw pink beef patty"),
        ("ing:patty:COOKED", "patty_cooked.png", "a grilled browned beef patty with grill marks"),
        ("ing:patty:BURNED", "patty_burned.png", "a blackened burnt beef patty"),
        ("ing:egg:RAW",      "egg_raw.png",      "a single whole white egg"),
        ("ing:egg:COOKED",   "egg_cooked.png",   "a sunny-side-up fried egg"),
        ("ing:egg:BURNED",   "egg_burned.png",   "a burnt blackened fried egg"),
    ]),
    "cook_b": (3, 2, [
        ("ing:broth_base:RAW",    "broth_base_raw.png",    "a metal pot of cold uncooked broth"),
        ("ing:broth_base:COOKED", "broth_base_cooked.png", "a metal pot of hot steaming soup broth"),
        ("ing:broth_base:BURNED", "broth_base_burned.png", "a metal pot of burnt black broth"),
        ("ing:noodles:RAW",       "noodles_raw.png",       "a nest of dry uncooked ramen noodles"),
        ("ing:noodles:COOKED",    "noodles_cooked.png",    "a steaming bowl of cooked ramen noodles"),
        ("ing:noodles:BURNED",    "noodles_burned.png",    "a clump of burnt black noodles"),
    ]),
    "mush": (2, 2, [
        ("ing:mushroom:RAW",     "mushroom_raw.png",     "a whole brown button mushroom"),
        ("ing:mushroom:CHOPPED", "mushroom_chopped.png", "a small pile of sliced raw mushrooms"),
        ("ing:mushroom:COOKED",  "mushroom_cooked.png",  "a small pile of golden sauteed mushrooms"),
        ("ing:mushroom:BURNED",  "mushroom_burned.png",  "a small pile of burnt black mushrooms"),
    ]),
}


def gen_image(prompt: str, api_key: str, model: str, ref_bytes: bytes | None = None) -> bytes:
    """Call the Gemini image model and return raw image bytes. ``ref_bytes`` (the style anchor) is
    sent as an input image for style-consistent image-to-image."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    parts: list[dict] = [{"text": prompt}]
    if ref_bytes:
        mime = "image/jpeg" if ref_bytes[:2] == b"\xff\xd8" else "image/png"
        parts.insert(0, {"inlineData": {"mimeType": mime, "data": base64.b64encode(ref_bytes).decode()}})
    payload = {"contents": [{"parts": parts}]}
    for body in ({**payload, "generationConfig": {"responseModalities": ["IMAGE"]}}, payload):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            if body is payload:
                raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}") from e
            continue
        for part in resp.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            d = part.get("inlineData") or part.get("inline_data")
            if d and d.get("data"):
                return base64.b64decode(d["data"])
        raise RuntimeError("no image in response: " + json.dumps(resp)[:300])
    raise RuntimeError("image generation failed")


def _key_magenta(im, tol: int = 75):
    """Return a copy of RGBA image with the magenta background made transparent (+ slight despill)."""
    out = []
    for r, g, b, a in im.getdata():
        if r > 255 - tol and g < tol and b > 255 - tol:
            out.append((r, g, b, 0))
        else:
            if b > r and g < r:        # tame magenta fringe on anti-aliased edges
                b = (r + b) // 2
            out.append((r, g, b, a))
    keyed = im.copy()
    keyed.putdata(out)
    return keyed


def slice_and_save(raw: bytes, cols: int, rows: int, cells: list, size: int,
                   manifest: dict, sheet_name: str) -> tuple[int, int]:
    """Cut the sheet into a cols×rows grid; for each declared cell key the magenta, trim, square,
    pixel-resize, and save. Empty cells (model left blank / misaligned) are reported, not saved."""
    from PIL import Image

    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    W, H = im.size
    cw, ch = W / cols, H / rows
    preview = Image.new("RGBA", (cols * (size + 8), rows * (size + 8)), (42, 36, 31, 255))
    ok = empty = 0
    for idx, (mkey, fname, _subject) in enumerate(cells):
        r, c = divmod(idx, cols)
        cell = im.crop((int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch)))
        keyed = _key_magenta(cell)
        bbox = keyed.getbbox()
        if not bbox:
            empty += 1
            print(f"    · EMPTY cell {idx} ({mkey}) — model left it blank or misaligned")
            continue
        crop = keyed.crop(bbox)
        s = max(crop.size)
        canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        canvas.paste(crop, ((s - crop.width) // 2, (s - crop.height) // 2))
        canvas = canvas.resize((size, size), Image.NEAREST)   # keep pixel edges crisp
        canvas.save(ASSETS / fname)
        manifest[mkey] = fname
        preview.alpha_composite(canvas, (c * (size + 8), r * (size + 8)))
        ok += 1
    preview.convert("RGB").save(f"/tmp/sheet_{sheet_name}_sliced.png")
    return ok, empty


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate Kitchen Rush sprite sheets via Gemini image gen.")
    ap.add_argument("--model", default=os.environ.get("KR_IMAGE_MODEL", "gemini-3-pro-image-preview"))
    ap.add_argument("--anchor", default=os.environ.get("KR_ANCHOR", "/tmp/style_C_bold.png"),
                    help="style-anchor image fed as a reference into every sheet")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--only", nargs="*", default=None, help="subset of sheet names: " + ", ".join(SHEETS))
    ap.add_argument("--sleep", type=float, default=0.5)
    args = ap.parse_args(argv)

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("error: set GEMINI_API_KEY", file=sys.stderr); return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow required -> pip install pillow", file=sys.stderr); return 2
    anchor = Path(args.anchor).read_bytes() if Path(args.anchor).exists() else None
    if anchor is None:
        print(f"warning: anchor {args.anchor} not found — generating without a style reference")

    names = args.only or list(SHEETS)
    manifest_path = ASSETS / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    tot_ok = tot_empty = 0
    for name in names:
        if name not in SHEETS:
            print(f"skip unknown sheet {name!r} (have: {', '.join(SHEETS)})"); continue
        cols, rows, cells = SHEETS[name]
        items = "\n".join(f"{i+1}) {c[2]}" for i, c in enumerate(cells))
        prompt = SHEET_PROMPT.format(cols=cols, rows=rows, style=STYLE, items=items)
        print(f"[{name}] generating {cols}x{rows} sheet ({len(cells)} sprites)…")
        try:
            raw = gen_image(prompt, api_key, args.model, anchor)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ generation failed: {e}"); continue
        open(f"/tmp/sheet_{name}.png", "wb").write(raw)
        ok, empty = slice_and_save(raw, cols, rows, cells, args.size, manifest, name)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        tot_ok += ok; tot_empty += empty
        print(f"  ✓ {ok} saved, {empty} empty  (raw: /tmp/sheet_{name}.png, sliced: /tmp/sheet_{name}_sliced.png)")
        time.sleep(args.sleep)

    print(f"\ndone: {tot_ok} sprites, {tot_empty} empty cells. manifest -> {manifest_path}")
    return 0 if tot_empty == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
