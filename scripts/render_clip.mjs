#!/usr/bin/env node
/* Render a Kitchen Rush replay (or race) to video frames — no screen recording needed.
 *
 * Real-time capture is the wrong tool here: the viewer is deterministic and seekable, so we
 * step the game clock offline (window.KRplayer.step from ui/app.js), screenshot each frame
 * via headless Chrome, and let ffmpeg assemble a perfectly smooth clip at any fps.
 *
 * Usage (ESM resolves node_modules relative to the script, so run a copy next to the install):
 *   cd ui && python3 -m http.server 8000 &        # serve the viewer
 *   npm i --prefix /tmp/kr-clip puppeteer-core    # tiny; uses your installed Chrome
 *   cp scripts/render_clip.mjs /tmp/kr-clip/
 *   node /tmp/kr-clip/render_clip.mjs \
 *     --url "http://localhost:8000/?replays=replays/a.json,replays/b.json&labels=A,B" \
 *     --out /tmp/kr-frames --fps 24 --speed 4
 *   ffmpeg -framerate 24 -i /tmp/kr-frames/f_%05d.png -c:v libx264 -pix_fmt yuv420p \
 *     -vf scale=1280:-2 clip.mp4
 *
 * --speed S compresses S game-seconds into 1 second of video (like the viewer's S× button).
 */
import { mkdirSync } from "node:fs";
import puppeteer from "puppeteer-core";

const arg = (name, dflt) => {
  const i = process.argv.indexOf("--" + name);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const url = arg("url", null);
const out = arg("out", "/tmp/kr-frames");
const fps = parseFloat(arg("fps", "24"));
const speed = parseFloat(arg("speed", "4"));
const width = parseInt(arg("width", "1100"), 10);
const height = parseInt(arg("height", "640"), 10);
const chrome = arg("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
if (!url) { console.error("error: --url required"); process.exit(1); }
mkdirSync(out, { recursive: true });

const browser = await puppeteer.launch({ executablePath: chrome, headless: true });
try {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 2 });
  await page.goto(url, { waitUntil: "networkidle0" });
  await page.waitForFunction("window.KRplayer && KRplayer.ready()", { timeout: 15000 });
  const dur = await page.evaluate("KRplayer.duration()");
  const dt = speed / fps;
  const total = Math.floor(dur / dt) + 1;
  console.log(`rendering ${total} frames (${dur.toFixed(1)}gs at ${speed}x -> ${(dur / speed).toFixed(1)}s of video at ${fps}fps)`);
  for (let i = 0; i < total; i++) {
    const t = Math.min(i * dt, dur);
    await page.evaluate((tt) => window.KRplayer.step(tt), t);
    // two rAFs: let the seek paint (CSS transitions settle close enough at this cadence)
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.screenshot({ path: `${out}/f_${String(i).padStart(5, "0")}.png` });
    if (i % 100 === 0) console.log(`  frame ${i}/${total} (t=${t.toFixed(1)}gs)`);
  }
  console.log(`done -> ${out}/f_*.png`);
} finally {
  await browser.close();
}
