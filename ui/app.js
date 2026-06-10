/* app.js — Kitchen Rush replay viewer (vanilla).
 *
 * Plays a self-contained replay JSON on a real-time game clock (1x = 1 real second). The kitchen
 * itself carries the state: cooking food + progress + burn show ON the stove tiles, the chef carries
 * its held items and shows a working pulse, score/time overlay the kitchen corners, and orders are a
 * compact rack. No side panels. Chef position is interpolated between frames; discrete state snaps at
 * frame boundaries; the gap before a `think` frame is the visible deliberation pause (the latency cost).
 */
(() => {
  const S = KR.sprites;
  const GAP = 0;
  const MS_PER_GS = 1000;        // 1x = real time
  const SPEEDS = [1, 2, 4, 8, 0.5];
  // Tried in order on load: first whatever the quickstart just exported, then the bundled demos.
  const BUILTIN = ["replays/easy_seed0.json", "replays/easy_seed1_gemini35flash.json",
                   "replays/easy_seed0_oracle.json"];
  const WORK_ACTIONS = new Set(["chop", "prep", "cook", "collect", "collect_cooked", "plate", "serve", "discard"]);

  const st = {
    data: null, frames: [], horizon: 0, tEnd: 0,
    t: 0, playing: false, speedIdx: 0, raf: 0, lastWall: 0,
    curIdx: 0, cellPx: 64, n: 7, facing: "front", dragging: false,
    stationByCell: new Map(), tileByCell: new Map(), stoveTileByIndex: [], burnerCells: [],
    passCell: null, binCell: null, gridEl: null,
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    grid: $("grid"), stage: $("stage"), chef: $("chef"), fx: $("fxLayer"),
    think: $("thinkBubble"), thinkAmt: $("thinkAmt"), caption: $("caption"),
    score: $("score"), scoreDelta: $("scoreDelta"), clock: $("clock"), horizon: $("horizon"),
    combo: $("combo"), orders: $("orders"), fileInput: $("fileInput"),
    btnPlay: $("btnPlay"), btnStepBack: $("btnStepBack"), btnStepFwd: $("btnStepFwd"),
    btnRestart: $("btnRestart"), btnSpeed: $("btnSpeed"), scrub: $("scrub"),
  };

  // ---- geometry -----------------------------------------------------------
  const key = (r, c) => `${r},${c}`;
  function cellTopLeft(r, c) {
    const stride = st.cellPx + GAP;
    return { x: st.gridEl.offsetLeft + c * stride, y: st.gridEl.offsetTop + r * stride };
  }
  const lerp = (a, b, f) => a + (b - a) * f;
  const fmt = (x) => (x == null ? "0" : Math.round(x * 10) / 10);

  // ---- load ---------------------------------------------------------------
  async function init() {
    wireControls();
    try {
      const r = await fetch("assets/manifest.json", { cache: "no-store" });
      if (r.ok) Object.assign(S.MANIFEST, await r.json());
    } catch { /* emoji fallbacks */ }
    let loaded = null;
    for (const url of BUILTIN) { try { await loadUrl(url); loaded = url; break; } catch (e) { /* next */ } }
    if (loaded && new URLSearchParams(location.search).has("autoplay")) play();
  }
  async function loadUrl(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("fetch failed");
    loadReplay(await res.json());
  }
  function loadReplay(data) {
    if (!data || !data.layout || !data.frames) { alert("not a Kitchen Rush replay JSON"); return; }
    st.data = data; st.frames = data.frames; st.n = data.layout.grid_n;
    st.horizon = data.layout.horizon_gs || st.frames[st.frames.length - 1].clock_gs;
    st.tEnd = st.frames[st.frames.length - 1].clock_gs;
    buildLayout();
    el.horizon.textContent = Math.round(st.horizon);
    seek(0); pause();
  }

  // ---- static layout ------------------------------------------------------
  function buildLayout() {
    const n = st.n;
    st.cellPx = Math.max(46, Math.min(82, Math.floor(560 / n)));
    el.stage.style.setProperty("--n", n);
    el.stage.style.setProperty("--cell", st.cellPx + "px");
    document.documentElement.style.setProperty("--cell", st.cellPx + "px");

    const L = st.data.layout;
    st.stationByCell.clear(); st.tileByCell.clear(); st.stoveTileByIndex = [];
    st.burnerCells = []; st.passCell = st.binCell = null;
    for (const s of L.stations) {
      st.stationByCell.set(key(s.cell[0], s.cell[1]), s);
      if (s.type === "PASS") st.passCell = s.cell;
      if (s.type === "BIN") st.binCell = s.cell;
    }
    st.burnerCells = L.stations.filter((s) => s.type === "STOVE").map((s) => s.cell)
      .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));

    const blocked = new Set((L.blocked || []).map((c) => key(c[0], c[1])));
    const door = L.door ? key(L.door[0], L.door[1]) : null;
    const corners = new Set([key(0, 0), key(0, n - 1), key(n - 1, 0), key(n - 1, n - 1)]);
    const bg = { floor: S.path("tile:floor"), counter: S.path("tile:counter"), wall: S.path("tile:wall") };
    if (bg.wall) { el.stage.style.backgroundImage = `url(${bg.wall})`; el.stage.style.backgroundRepeat = "repeat"; el.stage.style.backgroundSize = "48px"; }
    const surface = (tile, kind) => { tile.classList.add(kind); const img = bg[kind]; if (img) tile.style.backgroundImage = `url(${img})`; };

    el.grid.innerHTML = ""; st.gridEl = el.grid;
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      const tile = document.createElement("div");
      tile.className = "tile";
      const ck = key(r, c);
      const s = st.stationByCell.get(ck);
      if (s) {
        surface(tile, "counter"); tile.classList.add("station", s.type);
        tile.appendChild(s.type === "ING" && s.ingredient ? S.componentIcon(s.ingredient, "RAW") : S.stationIcon(s.type));
        if (s.type === "STOVE") {
          const fx = document.createElement("div"); fx.className = "stove-fx"; tile.appendChild(fx);
          const bi = st.burnerCells.findIndex((bc) => bc[0] === r && bc[1] === c);
          if (bi >= 0) st.stoveTileByIndex[bi] = tile;
        }
      } else if (door && ck === door) {
        surface(tile, "wall"); tile.classList.add("door");      // doorway in the wall (point 2 fix)
      } else if (blocked.has(ck)) {
        surface(tile, corners.has(ck) ? "wall" : "counter");
      } else {
        surface(tile, "floor");
      }
      st.tileByCell.set(ck, tile);
      el.grid.appendChild(tile);
    }
    el.chef.innerHTML = "";
    const body = document.createElement("div"); body.className = "chef-body"; body.appendChild(S.chefIcon("front"));
    const hold = document.createElement("div"); hold.className = "chef-hold";
    el.chef.append(body, hold); st.facing = "front";
  }

  // ---- frame lookup -------------------------------------------------------
  function frameIndexAt(t) {
    const f = st.frames; let i = 0;
    while (i + 1 < f.length && f[i + 1].clock_gs <= t) i++;
    return i;
  }

  // ---- render -------------------------------------------------------------
  function render() {
    const f = st.frames; if (!f.length) return;
    const i = frameIndexAt(st.t);
    const cur = f[i]; const nxt = f[i + 1] || null;

    // chef position interpolation + facing
    let [cr, cc] = cur.chef_pos, walking = false;
    if (nxt) {
      const span = nxt.clock_gs - cur.clock_gs;
      const frac = span > 0 ? Math.max(0, Math.min(1, (st.t - cur.clock_gs) / span)) : 1;
      cr = lerp(cur.chef_pos[0], nxt.chef_pos[0], frac);
      cc = lerp(cur.chef_pos[1], nxt.chef_pos[1], frac);
      walking = (nxt.chef_pos[0] !== cur.chef_pos[0] || nxt.chef_pos[1] !== cur.chef_pos[1]) && frac < 1;
      if (walking) {
        const dr = nxt.chef_pos[0] - cur.chef_pos[0], dc = nxt.chef_pos[1] - cur.chef_pos[1];
        st.facing = Math.abs(dc) >= Math.abs(dr) ? (dc > 0 ? "right" : "left") : (dr > 0 ? "front" : "back");
      }
    }
    const p = cellTopLeft(cr, cc);
    el.chef.style.transform = `translate(${p.x}px, ${p.y}px)`;
    el.chef.classList.toggle("walking", walking);
    el.chef.classList.toggle("idle", !walking);

    // carried items on the chef (kills the hands panel)
    const hands = cur.hands || [];
    el.chef.querySelector(".chef-body").firstChild.replaceWith(S.chefIcon(st.facing, hands.length > 0));
    const hold = el.chef.querySelector(".chef-hold");
    hold.innerHTML = ""; hold.dataset.facing = st.facing; hold.classList.toggle("multi", hands.length > 1);
    for (const h of hands.slice(0, 4)) hold.appendChild(S.heldIcon(h));

    // thinking pause (deliberation gap before a `think` frame)
    const thinking = nxt && nxt.kind === "think";
    if (thinking) {
      el.think.hidden = false;
      el.thinkAmt.textContent = "+" + (nxt.think_gs ?? (nxt.clock_gs - cur.clock_gs)).toFixed(1) + "s";
      const tp = cellTopLeft(cur.chef_pos[0], cur.chef_pos[1]);
      el.think.style.left = (tp.x + st.cellPx / 2) + "px"; el.think.style.top = tp.y + "px";
    } else { el.think.hidden = true; }

    // HUD overlays
    el.score.textContent = fmt(cur.score);
    el.clock.textContent = st.t.toFixed(0);
    el.combo.textContent = (cur.combo || 0) > 1 ? "🔥×" + cur.combo : "";
    if (!st.dragging) el.scrub.value = Math.round(1000 * (st.t / (st.tEnd || 1)));

    renderStoves(cur);
    renderOrders(cur);
    renderCaption(f, i);

    if (i > st.curIdx) {                       // moved forward across frame(s)
      for (let k = st.curIdx + 1; k <= i; k++) { emitFx(f[k]); maybePulse(f[k]); }
      const ds = (f[i].score ?? 0) - (f[st.curIdx].score ?? 0);
      if (Math.abs(ds) >= 0.5) flashDelta(ds);
    }
    st.curIdx = i;
  }

  // cooking state ON the stove tiles (the lively-kitchen core)
  function renderStoves(cur) {
    for (const b of (cur.burners || [])) {
      const tile = st.stoveTileByIndex[b.index]; if (!tile) continue;
      const fx = tile.querySelector(".stove-fx");
      tile.classList.remove("cooking", "ready", "burned");
      tile.style.removeProperty("--burn"); fx.innerHTML = "";
      if (b.status === "FREE" || !b.ingredient) continue;
      if (b.status === "COOKING") {
        tile.classList.add("cooking");
        const pct = Math.max(0, Math.min(1, (st.t - b.start_gs) / Math.max(1e-6, b.ready_gs - b.start_gs)));
        const ring = document.createElement("div"); ring.className = "cook-ring";
        ring.style.background = `conic-gradient(var(--accent2) ${pct * 360}deg, #0006 0deg)`;
        fx.append(ring, S.icon(["fx:flame"], { extraClass: "flame" }), S.componentIcon(b.ingredient, "RAW"));
      } else if (b.status === "READY") {
        tile.classList.add("ready");
        const togo = Math.max(0, Math.min(1, (b.burn_gs - st.t) / Math.max(1e-6, b.burn_gs - b.ready_gs)));
        tile.style.setProperty("--burn", (1 - togo).toFixed(2));   // 0 fresh -> 1 about to burn
        fx.append(S.icon(["fx:flame"], { extraClass: "flame" }), S.componentIcon(b.ingredient, "COOKED"));
      } else if (b.status === "BURNED") {
        tile.classList.add("burned");
        fx.appendChild(S.componentIcon(b.ingredient, "BURNED"));
      }
    }
  }

  // compact order rack (tickets at the pass)
  const orderRank = (o) => ({ ACTIVE: 0, PENDING: 1, SERVED: 2, EXPIRED: 2 }[o.status] ?? 3);
  function renderOrders(cur) {
    const orders = (cur.orders || []).slice().sort((a, b) =>
      (orderRank(a) - orderRank(b)) || (a.deadline_gs - b.deadline_gs) || a.order_id.localeCompare(b.order_id));
    el.orders.innerHTML = "";
    for (const o of orders) {
      const arrived = st.t >= o.arrival_gs && o.status !== "PENDING";
      const remaining = o.deadline_gs - st.t;
      const win = Math.max(1e-6, o.deadline_gs - o.arrival_gs);
      const frac = Math.max(0, Math.min(1, remaining / win));
      const chip = document.createElement("div");
      let cls = "ticket";
      if (o.status === "SERVED") cls += " served";
      else if (o.status === "EXPIRED") cls += " expired";
      else if (!arrived) cls += " pending";
      else if (frac > 0.5) cls += " ok"; else if (frac > 0.22) cls += " warn"; else cls += " urgent";
      chip.className = cls;
      chip.appendChild(S.dishIcon(o.dish));
      const t = document.createElement("span"); t.className = "tk-t";
      t.textContent = (o.status === "ACTIVE" && arrived) ? Math.max(0, remaining).toFixed(0) : "";
      const bar = document.createElement("i"); bar.className = "tk-bar";
      bar.style.width = (arrived && o.status === "ACTIVE" ? frac * 100 : (o.status === "SERVED" ? 100 : 0)) + "%";
      bar.style.background = frac > 0.5 ? "var(--green)" : frac > 0.22 ? "var(--accent2)" : "var(--red)";
      chip.append(t, bar);
      el.orders.appendChild(chip);
    }
  }

  function renderCaption(f, i) {
    let act = null;
    for (let k = i; k >= 0; k--) { if (f[k].kind === "action") { act = f[k].action; break; } if (f[k].kind === "think") break; }
    if (!act && f[i].action) act = f[i].action;
    if (!act) { el.caption.className = "caption"; el.caption.innerHTML = "&nbsp;"; return; }
    const argstr = act.arguments && Object.keys(act.arguments).length ? "(" + Object.values(act.arguments).join(", ") + ")" : "";
    el.caption.className = "caption " + (act.ok ? "ok" : "bad");
    el.caption.innerHTML = `<b>${act.name}${argstr}</b> — ${act.note || (act.ok ? "ok" : "invalid")}`;
  }

  // ---- fx -----------------------------------------------------------------
  function maybePulse(frame) {
    if (frame.kind === "action" && frame.action && frame.action.ok && WORK_ACTIONS.has(frame.action.name)) {
      el.chef.classList.add("working");
      clearTimeout(st._workT); st._workT = setTimeout(() => el.chef.classList.remove("working"), 680);
    }
  }
  function emitFx(frame) {
    for (const ev of (frame.events || [])) spawnEvFx(ev);
    if (frame.kind === "action" && frame.action && frame.action.name === "serve" && frame.action.ok) {
      floatSprite(st.passCell, "fx:burst", "good"); floatAt(st.passCell, "+" + extractPts(frame.action.note), "good");
    }
  }
  function spawnEvFx(ev) {
    const d = ev.detail || {};
    switch (ev.type) {
      case "order_arrived": floatAt(st.passCell, "🧾 order", "info"); break;
      case "order_expired": case "force_expired_end": floatAt(st.passCell, "⏰ missed", "bad"); break;
      case "cook_ready": floatSprite(burnerCell(d.burner_index), "fx:smoke", "steam"); break;
      case "burned": for (let k = 0; k < 3; k++) floatSprite(burnerCell(d.burner_index), "fx:smoke", "smoke", k * 90); break;
    }
  }
  const burnerCell = (idx) => st.burnerCells[idx] || st.burnerCells[0] || [0, 0];
  const extractPts = (note) => { const m = /\+([\d.]+)/.exec(note || ""); return m ? m[1] : ""; };
  function nodeAt(cell, cls) {
    if (!cell) return null;
    const p = cellTopLeft(cell[0], cell[1]); const node = document.createElement("div");
    node.className = cls; node.style.left = (p.x + st.cellPx / 2) + "px"; node.style.top = (p.y + st.cellPx / 2) + "px";
    el.fx.appendChild(node); return node;
  }
  function floatAt(cell, text, kind) { const n = nodeAt(cell, "fx " + (kind || "info")); if (!n) return; n.textContent = text; setTimeout(() => n.remove(), 1150); }
  function floatSprite(cell, spriteKey, kind, delay = 0) {
    setTimeout(() => { const n = nodeAt(cell, "fx fx-sprite " + (kind || "")); if (!n) return; n.appendChild(S.icon([spriteKey], { emojiChain: [spriteKey] })); setTimeout(() => n.remove(), 1000); }, delay);
  }
  function flashDelta(ds) {
    el.scoreDelta.textContent = (ds > 0 ? "+" : "") + fmt(ds);
    el.scoreDelta.style.color = ds >= 0 ? "var(--green)" : "var(--red)";
    el.scoreDelta.classList.remove("show"); void el.scoreDelta.offsetWidth; el.scoreDelta.classList.add("show");
  }

  // ---- playback -----------------------------------------------------------
  function tick(now) {
    if (!st.playing) return;
    const dt = (now - st.lastWall) / 1000; st.lastWall = now;
    st.t += dt * 1000 / MS_PER_GS * SPEEDS[st.speedIdx];
    if (st.t >= st.tEnd) { st.t = st.tEnd; render(); pause(); return; }
    render(); st.raf = requestAnimationFrame(tick);
  }
  function play() { if (st.t >= st.tEnd) { st.t = 0; st.curIdx = 0; } st.playing = true; el.btnPlay.textContent = "❚❚"; st.lastWall = performance.now(); st.raf = requestAnimationFrame(tick); }
  function pause() { st.playing = false; cancelAnimationFrame(st.raf); el.btnPlay.textContent = "▶"; }
  function seek(t) { st.t = Math.max(0, Math.min(st.tEnd, t)); st.curIdx = frameIndexAt(st.t); render(); }
  function stepFrame(dir) {
    pause(); const i = frameIndexAt(st.t);
    const j = Math.max(0, Math.min(st.frames.length - 1, i + dir + (dir > 0 && st.frames[i].clock_gs < st.t ? 1 : 0)));
    st.curIdx = Math.max(0, j - 1); seek(st.frames[j].clock_gs);
  }

  // ---- controls -----------------------------------------------------------
  function wireControls() {
    el.btnPlay.onclick = () => (st.playing ? pause() : play());
    el.btnRestart.onclick = () => { pause(); st.curIdx = 0; seek(0); };
    el.btnStepFwd.onclick = () => stepFrame(+1);
    el.btnStepBack.onclick = () => stepFrame(-1);
    el.btnSpeed.onclick = () => { st.speedIdx = (st.speedIdx + 1) % SPEEDS.length; el.btnSpeed.textContent = SPEEDS[st.speedIdx] + "×"; };
    el.scrub.oninput = () => { st.dragging = true; pause(); seek(st.tEnd * el.scrub.value / 1000); };
    el.scrub.onchange = () => { st.dragging = false; };
    const loadFile = (file) => { const fr = new FileReader(); fr.onload = () => { try { loadReplay(JSON.parse(fr.result)); } catch { alert("invalid JSON"); } }; fr.readAsText(file); };
    el.fileInput.onchange = (e) => { if (e.target.files[0]) loadFile(e.target.files[0]); };
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]); });
    document.addEventListener("keydown", (e) => {
      if (e.key === " ") { e.preventDefault(); st.playing ? pause() : play(); }
      else if (e.key === "ArrowRight") stepFrame(+1);
      else if (e.key === "ArrowLeft") stepFrame(-1);
      else if (e.key === "Home") { pause(); seek(0); }
    });
    window.addEventListener("resize", () => render());
  }

  init();
})();
