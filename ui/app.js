/* app.js — Kitchen Rush replay viewer (vanilla).
 *
 * Loads a self-contained replay JSON (see report.build_replay) and plays it back on a continuous
 * game-clock. Chef position is INTERPOLATED between frames so travel reads as walking; discrete
 * state (hands, burners, orders, score) snaps at frame boundaries. The deliberation gap before a
 * `think` frame is rendered as a visible pause with a "thinking +Xgs" bubble — that is the latency
 * mechanic made legible: the world clock advances (and food can burn) while the model deliberates.
 */
(() => {
  const S = KR.sprites;
  const GAP = 0;                  // grid gap px (tiles abut for a seamless kitchen; matches CSS)
  const MS_PER_GS = 1000;        // 1x = real time (1 game-second = 1 real second); speed up to taste
  const SPEEDS = [1, 2, 4, 8, 0.5];
  const BUILTIN = ["replays/easy_seed0_gemini35flash.json", "replays/easy_seed0_oracle.json"];

  // ---- state --------------------------------------------------------------
  const st = {
    data: null, frames: [], horizon: 0, tEnd: 0,
    t: 0, playing: false, speedIdx: 1, raf: 0, lastWall: 0,
    curIdx: 0, lastFxIdx: -1, cellPx: 64, n: 7,
    stationByCell: new Map(), burnerCells: [], passCell: null, binCell: null, gridEl: null,
  };

  // ---- DOM ----------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const el = {
    grid: $("grid"), stage: $("stage"), chef: $("chef"), fx: $("fxLayer"),
    think: $("thinkBubble"), thinkAmt: $("thinkAmt"), caption: $("caption"),
    score: $("score"), scoreDelta: $("scoreDelta"), clock: $("clock"), horizon: $("horizon"),
    clockFill: $("clockFill"), bbudget: $("bbudget"), krVal: $("krVal"), combo: $("combo"),
    tickets: $("tickets"), hands: $("hands"), handsFree: $("handsFree"), burners: $("burners"),
    runMeta: $("runMeta"), runSelect: $("runSelect"), fileInput: $("fileInput"),
    btnPlay: $("btnPlay"), btnStepBack: $("btnStepBack"), btnStepFwd: $("btnStepFwd"),
    btnRestart: $("btnRestart"), btnSpeed: $("btnSpeed"), scrub: $("scrub"),
    tNow: $("tNow"), tEnd: $("tEnd"), frameIdx: $("frameIdx"), frameTot: $("frameTot"),
  };

  // ---- geometry -----------------------------------------------------------
  const key = (r, c) => `${r},${c}`;
  function cellCenter(r, c) {
    const stride = st.cellPx + GAP;
    const left = st.gridEl.offsetLeft + c * stride;
    const top = st.gridEl.offsetTop + r * stride;
    return { x: left, y: top };           // chef box is cell-sized; top-left placement centers it
  }
  const lerp = (a, b, f) => a + (b - a) * f;

  // ---- load ---------------------------------------------------------------
  async function init() {
    wireControls();
    // pick up generated sprites if present (assets/manifest.json maps keys -> png filenames).
    // Emoji fallbacks remain for anything not listed, so the viewer always renders.
    try {
      const r = await fetch("assets/manifest.json", { cache: "no-store" });
      if (r.ok) Object.assign(S.MANIFEST, await r.json());
    } catch { /* no sprites yet — emoji fallbacks */ }
    // populate built-in dropdown
    el.runSelect.innerHTML = "";
    for (const p of BUILTIN) {
      const o = document.createElement("option");
      o.value = p; o.textContent = p.split("/").pop().replace(/\.json$/, "");
      el.runSelect.appendChild(o);
    }
    el.runSelect.onchange = () => loadUrl(el.runSelect.value);
    let loaded = null;
    for (const url of BUILTIN) {                 // prefer the first that exists (gemini, else oracle)
      try { await loadUrl(url); loaded = url; break; } catch (e) { /* try next */ }
    }
    if (loaded) {
      el.runSelect.value = loaded;
      if (new URLSearchParams(location.search).has("autoplay")) play();
    } else {
      el.runMeta.textContent = "open a replay JSON →  (or serve this folder: python3 -m http.server)";
    }
  }

  async function loadUrl(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error("fetch failed");
    loadReplay(await res.json());
  }

  function loadReplay(data) {
    if (!data || !data.layout || !data.frames) { alert("not a Kitchen Rush replay JSON"); return; }
    st.data = data;
    st.frames = data.frames;
    st.n = data.layout.grid_n;
    st.horizon = data.layout.horizon_gs || (st.frames[st.frames.length - 1].clock_gs);
    st.tEnd = st.frames[st.frames.length - 1].clock_gs;
    buildLayout();
    const m = data.meta || {};
    const kr = krOf(m.score_raw, m.s_null, m.s_ref);
    el.runMeta.textContent =
      `${m.tier ?? "?"} · seed ${m.seed ?? "?"} · ${st.frames.length} frames · score ${fmt(m.score_raw)} · KR ${kr}`;
    el.bbudget.textContent = (data.layout.latency_budget ?? "–");
    el.krVal.textContent = kr;
    el.horizon.textContent = Math.round(st.horizon);
    el.tEnd.textContent = st.tEnd.toFixed(1);
    el.frameTot.textContent = st.frames.length - 1;
    seek(0); pause();
  }

  function krOf(score, nul, ref) {
    if (score == null || nul == null || ref == null || ref <= nul) return "–";
    return (100 * Math.max(0, Math.min(1, (score - nul) / (ref - nul)))).toFixed(0);
  }
  const fmt = (x) => (x == null ? "–" : (Math.round(x * 10) / 10));

  // ---- build static layout ------------------------------------------------
  function buildLayout() {
    const n = st.n;
    st.cellPx = Math.max(40, Math.min(74, Math.floor(540 / n)));
    el.stage.style.setProperty("--n", n);
    el.stage.style.setProperty("--cell", st.cellPx + "px");
    document.documentElement.style.setProperty("--cell", st.cellPx + "px");

    st.stationByCell.clear(); st.burnerCells = []; st.passCell = st.binCell = null;
    for (const s of st.data.layout.stations) {
      st.stationByCell.set(key(s.cell[0], s.cell[1]), s);
      if (s.type === "PASS") st.passCell = s.cell;
      if (s.type === "BIN") st.binCell = s.cell;
    }
    // burner cells in engine order = sorted stove cells
    st.burnerCells = st.data.layout.stations
      .filter((s) => s.type === "STOVE")
      .map((s) => s.cell)
      .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));

    // grid tiles
    // surfaces from the layout: stations + counters line the walls, interior is floor, one wall
    // cell is a doorway. Tiles abut and use the generated floor/counter/wall textures (or CSS
    // fallback colors). No labels — dispensers show their actual ingredient instead.
    const L = st.data.layout;
    const blocked = new Set((L.blocked || []).map((c) => key(c[0], c[1])));
    const door = L.door ? key(L.door[0], L.door[1]) : null;
    const corners = new Set([key(0, 0), key(0, n - 1), key(n - 1, 0), key(n - 1, n - 1)]);
    const bg = { floor: S.path("tile:floor"), counter: S.path("tile:counter"), wall: S.path("tile:wall") };
    if (bg.wall) {
      el.stage.style.backgroundImage = `url(${bg.wall})`;
      el.stage.style.backgroundRepeat = "repeat";
      el.stage.style.backgroundSize = "48px";
    }
    const surface = (tile, kind) => {
      tile.classList.add(kind);
      const img = bg[kind === "door" ? "floor" : kind];
      if (img) tile.style.backgroundImage = `url(${img})`;
    };

    el.grid.innerHTML = "";
    st.gridEl = el.grid;
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        const tile = document.createElement("div");
        tile.className = "tile";
        const ck = key(r, c);
        const s = st.stationByCell.get(ck);
        if (s) {
          surface(tile, "counter");
          tile.classList.add("station", s.type);
          tile.appendChild(s.type === "ING" && s.ingredient
            ? S.componentIcon(s.ingredient, "RAW") : S.stationIcon(s.type));
          if (st.burnerCells.some((bc) => bc[0] === r && bc[1] === c)) tile.classList.add("burner-cell");
        } else if (door && ck === door) {
          surface(tile, "door");
        } else if (blocked.has(ck)) {
          surface(tile, corners.has(ck) ? "wall" : "counter");
        } else {
          surface(tile, "floor");
        }
        el.grid.appendChild(tile);
      }
    }
    // chef: inner body (directional sprite; the bob animates here so it doesn't fight the
    // position transform on .chef) + a held-item layer composited at the hands.
    el.chef.innerHTML = "";
    const body = document.createElement("div");
    body.className = "chef-body";
    body.appendChild(S.chefIcon("front"));
    const hold = document.createElement("div");
    hold.className = "chef-hold";
    el.chef.append(body, hold);
    st.facing = "front";
  }

  // ---- frame lookup -------------------------------------------------------
  function frameIndexAt(t) {
    const f = st.frames;
    let i = 0;
    while (i + 1 < f.length && f[i + 1].clock_gs <= t) i++;
    return i;
  }

  // ---- render -------------------------------------------------------------
  function render() {
    const f = st.frames; if (!f.length) return;
    const i = frameIndexAt(st.t);
    const cur = f[i];
    const nxt = f[i + 1] || null;

    // chef interpolation
    let [cr, cc] = cur.chef_pos, walking = false;
    if (nxt) {
      const span = nxt.clock_gs - cur.clock_gs;
      const frac = span > 0 ? Math.max(0, Math.min(1, (st.t - cur.clock_gs) / span)) : 1;
      cr = lerp(cur.chef_pos[0], nxt.chef_pos[0], frac);
      cc = lerp(cur.chef_pos[1], nxt.chef_pos[1], frac);
      walking = (nxt.chef_pos[0] !== cur.chef_pos[0] || nxt.chef_pos[1] !== cur.chef_pos[1]) && frac < 1;
    }
    const p = cellCenter(cr, cc);
    el.chef.style.transform = `translate(${p.x}px, ${p.y}px)`;
    el.chef.classList.toggle("walking", walking);

    // facing from the movement delta toward the next frame (kept when idle)
    if (nxt && walking) {
      const dr = nxt.chef_pos[0] - cur.chef_pos[0], dc = nxt.chef_pos[1] - cur.chef_pos[1];
      st.facing = Math.abs(dc) >= Math.abs(dr) ? (dc > 0 ? "right" : "left") : (dr > 0 ? "front" : "back");
    }
    // composite the carried item (prefer a plated dish, else the most-recent component); when
    // carrying, the chef switches to the tray-holding pose so the item sits on the tray.
    const hands = cur.hands || [];
    const top = hands.find((h) => h.state === "PLATE") || hands[hands.length - 1];
    el.chef.querySelector(".chef-body").firstChild.replaceWith(S.chefIcon(st.facing, !!top));
    const hold = el.chef.querySelector(".chef-hold");
    hold.innerHTML = "";
    hold.dataset.facing = st.facing;
    if (top) hold.appendChild(S.heldIcon(top));

    // thinking bubble: we are in the deliberation gap if the upcoming frame is a `think`
    const thinking = nxt && nxt.kind === "think";
    if (thinking) {
      el.think.hidden = false;
      el.thinkAmt.textContent = "+" + (nxt.think_gs ?? (nxt.clock_gs - cur.clock_gs)).toFixed(1) + "s";
      const tp = cellCenter(cur.chef_pos[0], cur.chef_pos[1]);
      el.think.style.left = (tp.x + st.cellPx / 2) + "px";
      el.think.style.top = tp.y + "px";
    } else {
      el.think.hidden = true;
    }

    // HUD
    el.score.textContent = fmt(cur.score);
    el.clock.textContent = st.t.toFixed(1);
    el.clockFill.style.width = (100 * Math.min(1, st.t / st.horizon)) + "%";
    el.combo.textContent = cur.combo ?? 0;
    el.tNow.textContent = st.t.toFixed(1);
    el.frameIdx.textContent = i;
    if (!st.dragging) el.scrub.value = Math.round(1000 * (st.t / (st.tEnd || 1)));

    renderTickets(cur);
    renderHands(cur);
    renderBurners(cur);
    renderCaption(f, i);

    // FX + score delta when moving forward across frames
    if (i > st.curIdx) {
      for (let k = st.curIdx + 1; k <= i; k++) emitFx(f[k]);
      const ds = (f[i].score ?? 0) - (f[st.curIdx].score ?? 0);
      if (Math.abs(ds) >= 0.5) flashDelta(ds);
    }
    st.curIdx = i;
  }

  function renderTickets(cur) {
    const orders = (cur.orders || []).slice().sort((a, b) =>
      (orderRank(a) - orderRank(b)) || (a.deadline_gs - b.deadline_gs) || a.order_id.localeCompare(b.order_id));
    el.tickets.innerHTML = "";
    for (const o of orders) {
      const arrived = st.t >= o.arrival_gs && o.status !== "PENDING";
      const remaining = o.deadline_gs - st.t;
      const window = Math.max(1e-6, o.deadline_gs - o.arrival_gs);
      const frac = Math.max(0, Math.min(1, remaining / window));
      const card = document.createElement("div");
      let cls = "ticket";
      if (o.status === "SERVED") cls += " served";
      else if (o.status === "EXPIRED") cls += " expired";
      else if (!arrived) cls += " pending";
      else if (frac > 0.5) cls += " active";
      else if (frac > 0.22) cls += " warn";
      else cls += " urgent";
      card.className = cls;

      const ic = document.createElement("div"); ic.className = "tk-icon";
      ic.appendChild(S.dishIcon(o.dish));
      const main = document.createElement("div"); main.className = "tk-main";
      main.innerHTML = `<div class="tk-dish">${o.dish.replace(/_/g, " ")}</div>` +
        `<div class="tk-id">${o.order_id} · ${o.base_value} pts${!arrived ? " · incoming" : ""}</div>`;
      const time = document.createElement("div"); time.className = "tk-time";
      time.textContent = (o.status === "ACTIVE" && arrived) ? Math.max(0, remaining).toFixed(0) + "s" : "";
      const bar = document.createElement("div"); bar.className = "timebar";
      const fill = document.createElement("i");
      fill.style.width = (arrived && o.status === "ACTIVE" ? frac * 100 : (o.status === "SERVED" ? 100 : 0)) + "%";
      fill.style.background = frac > 0.5 ? "var(--green)" : frac > 0.22 ? "var(--accent2)" : "var(--red)";
      bar.appendChild(fill);

      card.append(ic, main, time, bar);
      el.tickets.appendChild(card);
    }
  }
  const orderRank = (o) => ({ ACTIVE: 0, PENDING: 1, SERVED: 2, EXPIRED: 2 }[o.status] ?? 3);

  function renderHands(cur) {
    const hands = cur.hands || [];
    el.handsFree.textContent = `${hands.length}/4`;
    el.hands.innerHTML = "";
    for (let i = 0; i < 4; i++) {
      const slot = document.createElement("div"); slot.className = "slot";
      const h = hands[i];
      if (h) {
        slot.classList.add("filled");
        slot.appendChild(h.state === "PLATE" ? S.dishIcon(h.ingredient) : S.componentIcon(h.ingredient, h.state));
      }
      el.hands.appendChild(slot);
    }
  }

  function renderBurners(cur) {
    el.burners.innerHTML = "";
    for (const b of (cur.burners || [])) {
      const row = document.createElement("div"); row.className = "burner";
      const ring = document.createElement("div"); ring.className = "ring";
      const main = document.createElement("div"); main.className = "b-main";
      const time = document.createElement("div"); time.className = "b-time";
      if (b.status === "FREE" || !b.ingredient) {
        row.classList.add("free");
        main.innerHTML = `<div>empty</div><div class="b-state">burner ${b.index}</div>`;
      } else {
        ring.appendChild(b.status === "BURNED"
          ? S.componentIcon(b.ingredient, "BURNED") : S.componentIcon(b.ingredient, "COOKED"));
        let pct = 0, label = "", tlabel = "";
        if (b.status === "COOKING") {
          pct = Math.max(0, Math.min(1, (st.t - b.start_gs) / Math.max(1e-6, b.ready_gs - b.start_gs)));
          label = "cooking"; tlabel = "ready in " + Math.max(0, b.ready_gs - st.t).toFixed(0) + "s";
        } else if (b.status === "READY") {
          row.classList.add("ready"); pct = 1; label = "ready";
          tlabel = "burns in " + Math.max(0, b.burn_gs - st.t).toFixed(0) + "s";
        } else if (b.status === "BURNED") {
          row.classList.add("burned"); pct = 1; label = "burned"; tlabel = "discard it";
        }
        ring.style.background = `conic-gradient(${b.status === "READY" ? "var(--green)" : b.status === "BURNED" ? "#3a2222" : "var(--accent)"} ${pct * 360}deg, #0006 0deg)`;
        main.innerHTML = `<div>${b.ingredient.replace(/_/g, " ")}</div><div class="b-state">${label} · burner ${b.index}</div>`;
        time.textContent = tlabel;
      }
      row.append(ring, main, time);
      el.burners.appendChild(row);
    }
  }

  function renderCaption(f, i) {
    // most recent action up to here
    let act = null;
    for (let k = i; k >= 0; k--) { if (f[k].kind === "action") { act = f[k].action; break; } if (f[k].kind === "think") break; }
    if (!act && f[i].action) act = f[i].action;
    if (!act) { el.caption.className = "caption"; el.caption.innerHTML = "&nbsp;"; return; }
    const a = act;
    const argstr = a.arguments && Object.keys(a.arguments).length
      ? "(" + Object.values(a.arguments).join(", ") + ")" : "";
    el.caption.className = "caption " + (a.ok ? "ok" : "bad");
    el.caption.innerHTML = `<b>${a.name}${argstr}</b> — ${a.note || (a.ok ? "ok" : "invalid")}`;
  }

  // ---- fx -----------------------------------------------------------------
  function emitFx(frame) {
    for (const ev of (frame.events || [])) spawnEvFx(ev);
    if (frame.kind === "action" && frame.action && frame.action.name === "serve" && frame.action.ok)
      floatAt(st.passCell || frame.chef_pos, "✨ +" + extractPts(frame.action.note), "good");
  }
  function spawnEvFx(ev) {
    const d = ev.detail || {};
    switch (ev.type) {
      case "order_arrived": floatAt(st.passCell, "🧾 order!", "info"); break;
      case "order_expired": case "force_expired_end": floatAt(st.passCell, "⏰ missed", "bad"); break;
      case "cook_ready": floatAt(burnerCell(d.burner_index), "🔔 ready", "warn"); break;
      case "burned": floatAt(burnerCell(d.burner_index), "💥 burned", "bad"); break;
    }
  }
  const burnerCell = (i) => st.burnerCells[i] || st.burnerCells[0] || [0, 0];
  function extractPts(note) { const m = /\+([\d.]+)/.exec(note || ""); return m ? m[1] : ""; }
  function floatAt(cell, text, kind) {
    if (!cell) return;
    const p = cellCenter(cell[0], cell[1]);
    const n = document.createElement("div");
    n.className = "fx " + (kind || "info"); n.textContent = text;
    n.style.left = (p.x + st.cellPx / 2) + "px";
    n.style.top = (p.y + st.cellPx / 2) + "px";
    el.fx.appendChild(n);
    setTimeout(() => n.remove(), 1150);
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
    render();
    st.raf = requestAnimationFrame(tick);
  }
  function play() {
    if (st.t >= st.tEnd) st.t = 0, st.curIdx = 0;
    st.playing = true; el.btnPlay.textContent = "❚❚"; el.btnPlay.title = "pause";
    st.lastWall = performance.now(); st.raf = requestAnimationFrame(tick);
  }
  function pause() {
    st.playing = false; cancelAnimationFrame(st.raf);
    el.btnPlay.textContent = "▶"; el.btnPlay.title = "play";
  }
  function seek(t) { st.t = Math.max(0, Math.min(st.tEnd, t)); st.curIdx = frameIndexAt(st.t); render(); }
  function stepFrame(dir) {
    pause();
    const i = frameIndexAt(st.t);
    const j = Math.max(0, Math.min(st.frames.length - 1, i + dir + (dir > 0 && st.frames[i].clock_gs < st.t ? 1 : 0)));
    st.curIdx = Math.max(0, j - 1);
    seek(st.frames[j].clock_gs);
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
    el.fileInput.onchange = (e) => {
      const file = e.target.files[0]; if (!file) return;
      const fr = new FileReader();
      fr.onload = () => { try { loadReplay(JSON.parse(fr.result)); } catch { alert("invalid JSON"); } };
      fr.readAsText(file);
    };
    // drag & drop
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => {
      e.preventDefault(); const file = e.dataTransfer.files[0]; if (!file) return;
      const fr = new FileReader(); fr.onload = () => { try { loadReplay(JSON.parse(fr.result)); } catch { alert("invalid JSON"); } };
      fr.readAsText(file);
    });
    // keyboard
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
