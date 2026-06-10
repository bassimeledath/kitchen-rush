/* app.js — Kitchen Rush replay viewer (vanilla).
 *
 * One `KitchenView` per replay pane; a single conductor drives every pane off one shared game
 * clock. Because all replays of the same (seed, tier, B) play the IDENTICAL kitchen and order
 * stream, panes align frame-for-frame on clock_gs and the view becomes a fair side-by-side
 * race: you watch one model still thinking (🤔) while another is already plating.
 *
 *   ?replays=a.json,b.json   up to 4 panes (or multi-select / drag-drop several files)
 *   ?labels=name1,name2      optional pane labels (default: derived from filenames)
 *
 * n=1 is the classic single viewer (kitchen + its own ticket rack). n>=2 swaps in a SHARED
 * ticket rail — the orders are identical across panes, so tickets render once with one status
 * pip per model. n>=3 mutes per-pane fx floaters and captions (think bubbles always stay:
 * they're the point). The kitchen itself carries the state: cooking + burn progress live on
 * the stove tiles, the chef carries its held items, score/time overlay the kitchen corners.
 * Chef position is interpolated between frames; the gap before a `think` frame is the visible
 * deliberation pause (the latency cost).
 */
(() => {
  const S = KR.sprites;
  const MS_PER_GS = 1000;        // 1x = real time
  const SPEEDS = [1, 2, 4, 8, 0.5];
  const MAX_PANES = 4;
  const PANE_HUES = ["#6ec6ff", "#ff9f43", "#5fd07a", "#d29bff"];
  // Tried in order on load (single pane): first whatever the quickstart just exported, then
  // the bundled demos. For a race, pass ?replays=... instead.
  const BUILTIN = ["replays/easy_seed0.json", "replays/easy_seed1_gemini35flash.json",
                   "replays/easy_seed1_oracle.json"];
  const WORK_ACTIONS = new Set(["chop", "prep", "cook", "collect", "collect_cooked", "plate", "serve", "discard"]);

  const $ = (id) => document.getElementById(id);
  const el = {
    panes: $("panes"), orders: $("orders"), warn: $("warnbar"), fileInput: $("fileInput"),
    btnPlay: $("btnPlay"), btnStepBack: $("btnStepBack"), btnStepFwd: $("btnStepFwd"),
    btnRestart: $("btnRestart"), btnSpeed: $("btnSpeed"), scrub: $("scrub"),
    gclock: $("gclock"), gt: $("gt"), gh: $("gh"),
  };

  const key = (r, c) => `${r},${c}`;
  const lerp = (a, b, f) => a + (b - a) * f;
  const fmt = (x) => (x == null ? "0" : Math.round(x * 10) / 10);
  const stem = (name) => (name.split("/").pop().replace(/\.json$/i, "")
    .replace(/^(easy|medium|hard)_seed\d+_?/, "") || "replay");
  const instanceKey = (d) => `${d.meta.seed}|${d.meta.tier}|${d.layout.latency_budget ?? 1}`;

  // ---- one pane: a kitchen bound to one replay --------------------------------------------
  class KitchenView {
    constructor(container, data, { label, hue, compact, cellBudget }) {
      this.data = data; this.frames = data.frames; this.n = data.layout.grid_n;
      this.label = label; this.hue = hue; this.compact = compact;
      this.tEnd = this.frames[this.frames.length - 1].clock_gs;
      this.horizon = data.layout.horizon_gs || this.tEnd;
      this.facing = "front"; this.curIdx = 0; this.lastScore = 0;
      this.pose = "front"; this.handsKey = ""; this.rackOrder = ""; this.orderChips = new Map();
      this.stationByCell = new Map(); this.stoveTileByIndex = []; this.burnerCells = [];
      this.passCell = null; this._frameOrders = new Map();
      this.cellPx = Math.max(30, Math.min(82, Math.floor(cellBudget / this.n)));
      this._dom(container);
      this._buildLayout();
    }

    _dom(container) {
      const pane = document.createElement("section");
      pane.className = "pane";
      pane.style.setProperty("--phue", this.hue);
      pane.innerHTML = `
        <div class="pane-head"><i class="rank"></i><b class="plabel"></b>
          <span class="pcombo"></span><span class="pscore">0</span></div>
        <div class="stage">
          <div class="hud hud-left"><span class="k">score</span><b class="j-score">0</b><i class="delta j-delta"></i></div>
          <div class="hud hud-right"><b class="j-clock">0</b><span class="k">/<span class="j-horizon">0</span>s</span><i class="combo j-combo"></i></div>
          <div class="grid"></div>
          <div class="fx-layer"></div>
          <div class="chef"></div>
          <div class="think-bubble" hidden>🤔 <b class="j-thinkAmt"></b></div>
        </div>
        <div class="caption">&nbsp;</div>`;
      container.appendChild(pane);
      const q = (sel) => pane.querySelector(sel);
      this.el = {
        pane, stage: q(".stage"), grid: q(".grid"), fx: q(".fx-layer"), chef: q(".chef"),
        think: q(".think-bubble"), thinkAmt: q(".j-thinkAmt"), caption: q(".caption"),
        score: q(".j-score"), delta: q(".j-delta"), clock: q(".j-clock"), horizon: q(".j-horizon"),
        combo: q(".j-combo"), rank: q(".rank"), plabel: q(".plabel"),
        pscore: q(".pscore"), pcombo: q(".pcombo"),
      };
      this.el.plabel.textContent = this.label;
      this.el.horizon.textContent = Math.round(this.horizon);
    }

    _cellTopLeft(r, c) {
      const stride = this.cellPx;
      return { x: this.el.grid.offsetLeft + c * stride, y: this.el.grid.offsetTop + r * stride };
    }

    _buildLayout() {
      const n = this.n;
      this.el.stage.style.setProperty("--n", n);
      this.el.stage.style.setProperty("--cell", this.cellPx + "px");

      const L = this.data.layout;
      for (const s of L.stations) {
        this.stationByCell.set(key(s.cell[0], s.cell[1]), s);
        if (s.type === "PASS") this.passCell = s.cell;
      }
      this.burnerCells = L.stations.filter((s) => s.type === "STOVE").map((s) => s.cell)
        .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));

      const blocked = new Set((L.blocked || []).map((c) => key(c[0], c[1])));
      const door = L.door ? key(L.door[0], L.door[1]) : null;
      const corners = new Set([key(0, 0), key(0, n - 1), key(n - 1, 0), key(n - 1, n - 1)]);
      const bg = { floor: S.path("tile:floor"), counter: S.path("tile:counter"), wall: S.path("tile:wall") };
      if (bg.wall) {
        this.el.stage.style.backgroundImage = `url(${bg.wall})`;
        this.el.stage.style.backgroundRepeat = "repeat"; this.el.stage.style.backgroundSize = "48px";
      }
      const surface = (tile, kind) => { tile.classList.add(kind); const img = bg[kind]; if (img) tile.style.backgroundImage = `url(${img})`; };

      this.el.grid.innerHTML = "";
      for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
        const tile = document.createElement("div");
        tile.className = "tile";
        const ck = key(r, c);
        const s = this.stationByCell.get(ck);
        if (s) {
          surface(tile, "counter"); tile.classList.add("station", s.type);
          tile.appendChild(s.type === "ING" && s.ingredient ? S.componentIcon(s.ingredient, "RAW") : S.stationIcon(s.type));
          if (s.type === "STOVE") {
            const fx = document.createElement("div"); fx.className = "stove-fx"; tile.appendChild(fx);
            const bi = this.burnerCells.findIndex((bc) => bc[0] === r && bc[1] === c);
            if (bi >= 0) this.stoveTileByIndex[bi] = tile;
          }
        } else if (door && ck === door) {
          surface(tile, "wall"); tile.classList.add("door");
        } else if (blocked.has(ck)) {
          surface(tile, corners.has(ck) ? "wall" : "counter");
        } else {
          surface(tile, "floor");
        }
        this.el.grid.appendChild(tile);
      }
      this.el.chef.innerHTML = "";
      const body = document.createElement("div"); body.className = "chef-body"; body.appendChild(S.chefIcon("front"));
      const hold = document.createElement("div"); hold.className = "chef-hold";
      this.el.chef.append(body, hold);
    }

    frameIndexAt(t) {
      const f = this.frames; let i = 0;
      while (i + 1 < f.length && f[i + 1].clock_gs <= t) i++;
      return i;
    }

    // current per-order status map (used by the shared rail)
    ordersAt(t) {
      const i = this.frameIndexAt(t);
      let m = this._frameOrders.get(i);
      if (!m) {
        m = new Map();
        for (const o of (this.frames[i].orders || [])) m.set(o.order_id, o);
        this._frameOrders.set(i, m);
      }
      return m;
    }

    render(t, { withOwnRack }) {
      const f = this.frames; if (!f.length) return;
      const i = this.frameIndexAt(t);
      const cur = f[i]; const nxt = f[i + 1] || null;

      // chef position interpolation + facing
      let [cr, cc] = cur.chef_pos, walking = false;
      if (nxt) {
        const span = nxt.clock_gs - cur.clock_gs;
        const frac = span > 0 ? Math.max(0, Math.min(1, (t - cur.clock_gs) / span)) : 1;
        cr = lerp(cur.chef_pos[0], nxt.chef_pos[0], frac);
        cc = lerp(cur.chef_pos[1], nxt.chef_pos[1], frac);
        walking = (nxt.chef_pos[0] !== cur.chef_pos[0] || nxt.chef_pos[1] !== cur.chef_pos[1]) && frac < 1;
        if (walking) {
          const dr = nxt.chef_pos[0] - cur.chef_pos[0], dc = nxt.chef_pos[1] - cur.chef_pos[1];
          this.facing = Math.abs(dc) >= Math.abs(dr) ? (dc > 0 ? "right" : "left") : (dr > 0 ? "front" : "back");
        }
      }
      const p = this._cellTopLeft(cr, cc);
      this.el.chef.style.transform = `translate(${p.x}px, ${p.y}px)`;
      this.el.chef.classList.toggle("walking", walking);
      this.el.chef.classList.toggle("idle", !walking);

      // carried items on the chef; swap nodes only when the pose or held items change
      const hands = cur.hands || [];
      const pose = this.facing + (hands.length > 0 ? ":carry" : "");
      if (this.pose !== pose) {
        this.pose = pose;
        this.el.chef.querySelector(".chef-body").firstChild.replaceWith(S.chefIcon(this.facing, hands.length > 0));
      }
      const hold = this.el.chef.querySelector(".chef-hold");
      hold.dataset.facing = this.facing;
      const handsKey = hands.map((h) => h.ingredient + ":" + h.state).join(",");
      if (this.handsKey !== handsKey) {
        this.handsKey = handsKey;
        hold.innerHTML = ""; hold.classList.toggle("multi", hands.length > 1);
        for (const h of hands.slice(0, 4)) hold.appendChild(S.heldIcon(h));
      }

      // thinking pause (deliberation gap before a `think` frame)
      const thinking = nxt && nxt.kind === "think";
      if (thinking) {
        this.el.think.hidden = false;
        this.el.thinkAmt.textContent = "+" + (nxt.think_gs ?? (nxt.clock_gs - cur.clock_gs)).toFixed(1) + "s";
        const tp = this._cellTopLeft(cur.chef_pos[0], cur.chef_pos[1]);
        this.el.think.style.left = (tp.x + this.cellPx / 2) + "px"; this.el.think.style.top = tp.y + "px";
      } else { this.el.think.hidden = true; }

      // HUD (per-pane corners when single; the pane header when racing)
      this.lastScore = cur.score ?? 0;
      this.el.score.textContent = fmt(cur.score);
      this.el.pscore.textContent = fmt(cur.score);
      this.el.clock.textContent = t.toFixed(0);
      const comboTxt = (cur.combo || 0) > 1 ? "🔥×" + cur.combo : "";
      this.el.combo.textContent = comboTxt; this.el.pcombo.textContent = comboTxt;
      this.el.pane.classList.toggle("done", t > this.tEnd + 1e-6);

      this._renderStoves(cur, t);
      if (withOwnRack) this._renderOwnRack(cur, t);
      this._renderCaption(f, i);

      if (i > this.curIdx) {                       // moved forward across frame(s)
        for (let k = this.curIdx + 1; k <= i; k++) { this._emitFx(f[k]); this._maybePulse(f[k]); }
        const ds = (f[i].score ?? 0) - (f[this.curIdx].score ?? 0);
        if (Math.abs(ds) >= 0.5) this._flashDelta(ds);
      }
      this.curIdx = i;
    }

    // cooking state ON the stove tiles; contents rebuilt only when (status, ingredient)
    // changes — the cook ring / burn glow are per-frame style updates
    _renderStoves(cur, t) {
      for (const b of (cur.burners || [])) {
        const tile = this.stoveTileByIndex[b.index]; if (!tile) continue;
        const fx = tile.querySelector(".stove-fx");
        const sig = b.status + "|" + (b.ingredient || "");
        if (tile.dataset.sig !== sig) {
          tile.dataset.sig = sig;
          tile.classList.remove("cooking", "ready", "burned");
          tile.style.removeProperty("--burn"); fx.innerHTML = "";
          if (b.status === "COOKING" && b.ingredient) {
            tile.classList.add("cooking");
            const ring = document.createElement("div"); ring.className = "cook-ring";
            fx.append(ring, S.icon(["fx:flame"], { extraClass: "flame" }), S.componentIcon(b.ingredient, "RAW"));
          } else if (b.status === "READY" && b.ingredient) {
            tile.classList.add("ready");
            fx.append(S.icon(["fx:flame"], { extraClass: "flame" }), S.componentIcon(b.ingredient, "COOKED"));
          } else if (b.status === "BURNED" && b.ingredient) {
            tile.classList.add("burned");
            fx.appendChild(S.componentIcon(b.ingredient, "BURNED"));
          }
        }
        if (b.status === "COOKING" && b.ingredient) {
          const pct = Math.max(0, Math.min(1, (t - b.start_gs) / Math.max(1e-6, b.ready_gs - b.start_gs)));
          const ring = fx.querySelector(".cook-ring");
          if (ring) ring.style.background = `conic-gradient(var(--accent2) ${pct * 360}deg, #0006 0deg)`;
        } else if (b.status === "READY" && b.ingredient) {
          const togo = Math.max(0, Math.min(1, (b.burn_gs - t) / Math.max(1e-6, b.burn_gs - b.ready_gs)));
          tile.style.setProperty("--burn", (1 - togo).toFixed(2));
        }
      }
    }

    // classic single-viewer ticket rack (n=1 only; races use the shared rail instead)
    _renderOwnRack(cur, t) {
      const orders = (cur.orders || []).slice().sort((a, b) =>
        (orderRank(a) - orderRank(b)) || (a.deadline_gs - b.deadline_gs) || a.order_id.localeCompare(b.order_id));
      for (const o of orders) {
        let chip = this.orderChips.get(o.order_id);
        if (!chip) { chip = makeTicket(o.dish); this.orderChips.set(o.order_id, chip); }
        const arrived = t >= o.arrival_gs && o.status !== "PENDING";
        const { frac, remaining } = ticketTiming(o, t);
        let cls = "ticket";
        if (o.status === "SERVED") cls += " served";
        else if (o.status === "EXPIRED") cls += " expired";
        else if (!arrived) cls += " pending";
        else cls += urgency(frac);
        if (chip.className !== cls) chip.className = cls;
        chip.querySelector(".tk-t").textContent =
          (o.status === "ACTIVE" && arrived) ? Math.max(0, remaining).toFixed(0) : "";
        const bar = chip.querySelector(".tk-bar");
        bar.style.width = (arrived && o.status === "ACTIVE" ? frac * 100 : (o.status === "SERVED" ? 100 : 0)) + "%";
        bar.style.background = barColor(frac);
      }
      const rackOrder = orders.map((o) => o.order_id).join(",");
      if (this.rackOrder !== rackOrder) {
        this.rackOrder = rackOrder;
        for (const o of orders) el.orders.appendChild(this.orderChips.get(o.order_id));
      }
    }

    _renderCaption(f, i) {
      let act = null;
      for (let k = i; k >= 0; k--) { if (f[k].kind === "action") { act = f[k].action; break; } if (f[k].kind === "think") break; }
      if (!act && f[i].action) act = f[i].action;
      if (!act) { this.el.caption.className = "caption"; this.el.caption.innerHTML = "&nbsp;"; return; }
      const argstr = act.arguments && Object.keys(act.arguments).length ? "(" + Object.values(act.arguments).join(", ") + ")" : "";
      this.el.caption.className = "caption " + (act.ok ? "ok" : "bad");
      this.el.caption.innerHTML = `<b>${act.name}${argstr}</b> — ${act.note || (act.ok ? "ok" : "invalid")}`;
    }

    _maybePulse(frame) {
      if (frame.kind === "action" && frame.action && frame.action.ok && WORK_ACTIONS.has(frame.action.name)) {
        this.el.chef.classList.add("working");
        clearTimeout(this._workT); this._workT = setTimeout(() => this.el.chef.classList.remove("working"), 680);
      }
    }
    _emitFx(frame) {
      if (this.compact) return;                 // n>=3: floaters off, think bubbles stay
      for (const ev of (frame.events || [])) this._spawnEvFx(ev);
      if (frame.kind === "action" && frame.action && frame.action.name === "serve" && frame.action.ok) {
        this._floatSprite(this.passCell, "fx:burst", "good");
        this._floatAt(this.passCell, "+" + extractPts(frame.action.note), "good");
      }
    }
    _spawnEvFx(ev) {
      const d = ev.detail || {};
      switch (ev.type) {
        case "order_arrived": this._floatAt(this.passCell, "🧾 order", "info"); break;
        case "order_expired": case "force_expired_end": this._floatAt(this.passCell, "⏰ missed", "bad"); break;
        case "cook_ready": this._floatSprite(this._burnerCell(d.burner_index), "fx:smoke", "steam"); break;
        case "burned": for (let k = 0; k < 3; k++) this._floatSprite(this._burnerCell(d.burner_index), "fx:smoke", "smoke", k * 90); break;
      }
    }
    _burnerCell(idx) { return this.burnerCells[idx] || this.burnerCells[0] || [0, 0]; }
    _nodeAt(cell, cls) {
      if (!cell) return null;
      const p = this._cellTopLeft(cell[0], cell[1]); const node = document.createElement("div");
      node.className = cls; node.style.left = (p.x + this.cellPx / 2) + "px"; node.style.top = (p.y + this.cellPx / 2) + "px";
      this.el.fx.appendChild(node); return node;
    }
    _floatAt(cell, text, kind) { const n = this._nodeAt(cell, "fx " + (kind || "info")); if (!n) return; n.textContent = text; setTimeout(() => n.remove(), 1150); }
    _floatSprite(cell, spriteKey, kind, delay = 0) {
      setTimeout(() => { const n = this._nodeAt(cell, "fx fx-sprite " + (kind || "")); if (!n) return; n.appendChild(S.icon([spriteKey], { emojiChain: [spriteKey] })); setTimeout(() => n.remove(), 1000); }, delay);
    }
    _flashDelta(ds) {
      this.el.delta.textContent = (ds > 0 ? "+" : "") + fmt(ds);
      this.el.delta.style.color = ds >= 0 ? "var(--green)" : "var(--red)";
      this.el.delta.classList.remove("show"); void this.el.delta.offsetWidth; this.el.delta.classList.add("show");
      this.el.pscore.classList.remove("bump"); void this.el.pscore.offsetWidth; this.el.pscore.classList.add("bump");
    }
  }

  // ---- ticket helpers (shared between the single rack and the race rail) ------------------
  const orderRank = (o) => ({ ACTIVE: 0, PENDING: 1, SERVED: 2, EXPIRED: 2 }[o.status] ?? 3);
  const ticketTiming = (o, t) => {
    const remaining = o.deadline_gs - t;
    const win = Math.max(1e-6, o.deadline_gs - o.arrival_gs);
    return { remaining, frac: Math.max(0, Math.min(1, remaining / win)) };
  };
  const urgency = (frac) => (frac > 0.5 ? " ok" : frac > 0.22 ? " warn" : " urgent");
  const barColor = (frac) => (frac > 0.5 ? "var(--green)" : frac > 0.22 ? "var(--accent2)" : "var(--red)");
  const extractPts = (note) => { const m = /\+([\d.]+)/.exec(note || ""); return m ? m[1] : ""; };
  function makeTicket(dish) {
    const chip = document.createElement("div");
    chip.appendChild(S.dishIcon(dish));
    const t = document.createElement("span"); t.className = "tk-t";
    const bar = document.createElement("i"); bar.className = "tk-bar";
    chip.append(t, bar);
    return chip;
  }

  // ---- conductor: one clock, n panes -------------------------------------------------------
  const C = {
    views: [], t: 0, tEnd: 0, playing: false, speedIdx: 0, raf: 0, lastWall: 0,
    dragging: false, stepTimes: [], railChips: new Map(), railOrder: "", schedule: [],
  };

  function mount(datas, labels) {
    if (datas.length > MAX_PANES) {
      datas = datas.slice(0, MAX_PANES); labels = labels.slice(0, MAX_PANES);
      warn(`showing the first ${MAX_PANES} replays (the kitchen wall maxes out at ${MAX_PANES})`);
    } else warn(null);
    const keys = datas.map(instanceKey);
    if (new Set(keys).size > 1) {
      warn("⚠ these replays are different instances (seed/tier/B differ) — the race is not aligned");
    }
    pause();
    el.panes.innerHTML = ""; el.orders.innerHTML = "";
    C.railChips = new Map(); C.railOrder = "";
    C.views = [];
    const multi = datas.length > 1, compact = datas.length >= 3;
    el.panes.classList.toggle("multi", multi);
    el.panes.classList.toggle("compact", compact);
    el.gclock.hidden = !multi;
    const cellBudget = [560, 400, 310, 380][datas.length - 1] || 310;
    datas.forEach((d, i) => C.views.push(new KitchenView(el.panes, d, {
      label: labels[i] || `model ${i + 1}`, hue: PANE_HUES[i % PANE_HUES.length],
      compact, cellBudget,
    })));
    // four kitchens read as a 2x2 wall, not a 3+1 wrap
    el.panes.style.maxWidth = C.views.length === 4
      ? (2 * C.views[0].el.pane.offsetWidth + 18) + "px" : "";
    C.tEnd = Math.max(...C.views.map((v) => v.tEnd));
    C.stepTimes = [...new Set(C.views.flatMap((v) => v.frames.map((f) => f.clock_gs)))].sort((a, b) => a - b);
    // the shared race rail shows each order once (the schedule is identical across panes)
    C.schedule = multi ? (C.views[0].frames[0].orders || []).slice()
      .sort((a, b) => (a.arrival_gs - b.arrival_gs) || (a.deadline_gs - b.deadline_gs) || a.order_id.localeCompare(b.order_id)) : [];
    el.gh.textContent = Math.round(Math.max(...C.views.map((v) => v.horizon)));
    seek(0);
  }

  function warn(msg) { el.warn.hidden = !msg; el.warn.textContent = msg || ""; }

  function render() {
    if (!C.views.length) return;
    const single = C.views.length === 1;
    for (const v of C.views) v.render(C.t, { withOwnRack: single });
    if (!single) { renderRail(); renderRanks(); }
    el.gt.textContent = C.t.toFixed(0);
    if (!C.dragging) el.scrub.value = Math.round(1000 * (C.t / (C.tEnd || 1)));
  }

  // shared ticket rail: one chip per order, one status pip per model
  function renderRail() {
    for (const o of C.schedule) {
      let chip = C.railChips.get(o.order_id);
      if (!chip) {
        chip = makeTicket(o.dish);
        const pips = document.createElement("div"); pips.className = "tk-pips";
        for (const v of C.views) {
          const pip = document.createElement("i"); pip.className = "pip";
          pip.style.borderColor = v.hue; pips.appendChild(pip);
        }
        chip.appendChild(pips);
        C.railChips.set(o.order_id, chip);
      }
      const arrived = C.t >= o.arrival_gs;
      const { frac, remaining } = ticketTiming(o, C.t);
      const pips = chip.querySelectorAll(".pip");
      let unresolved = 0;
      C.views.forEach((v, i) => {
        const st = v.ordersAt(C.t).get(o.order_id)?.status;
        const pip = pips[i];
        const cls = st === "SERVED" ? "pip served" : st === "EXPIRED" ? "pip expired" : "pip";
        if (pip.className !== cls) pip.className = cls;
        if (st !== "SERVED" && st !== "EXPIRED") unresolved++;
      });
      let cls = "ticket";
      if (!arrived) cls += " pending";
      else if (unresolved === 0) cls += " settled";
      else cls += urgency(frac);
      if (chip.className !== cls) chip.className = cls;
      chip.querySelector(".tk-t").textContent = (arrived && unresolved > 0) ? Math.max(0, remaining).toFixed(0) : "";
      const bar = chip.querySelector(".tk-bar");
      bar.style.width = (arrived && unresolved > 0 ? frac * 100 : 0) + "%";
      bar.style.background = barColor(frac);
    }
    const order = C.schedule.map((o) => o.order_id).join(",");
    if (C.railOrder !== order) {
      C.railOrder = order;
      for (const o of C.schedule) el.orders.appendChild(C.railChips.get(o.order_id));
    }
  }

  function renderRanks() {
    const scores = C.views.map((v) => v.lastScore);
    for (const v of C.views) {
      const rank = 1 + scores.filter((s) => s > v.lastScore).length;
      const txt = "#" + rank;
      if (v.el.rank.textContent !== txt) v.el.rank.textContent = txt;
      v.el.rank.classList.toggle("lead", rank === 1);
    }
  }

  // ---- load --------------------------------------------------------------------------------
  async function init() {
    wireControls();
    try {
      const r = await fetch("assets/manifest.json", { cache: "no-store" });
      if (r.ok) { Object.assign(S.MANIFEST, await r.json()); await S.preload(); }
    } catch { /* emoji fallbacks */ }
    const qp = new URLSearchParams(location.search);
    const urls = (qp.get("replays") || "").split(",").map((s) => s.trim()).filter(Boolean);
    const labels = (qp.get("labels") || "").split(",").map((s) => s.trim());
    if (urls.length) {
      try {
        const datas = await Promise.all(urls.map((u) =>
          fetch(u, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(u); return r.json(); })));
        mount(datas, urls.map((u, i) => labels[i] || stem(u)));
      } catch (e) { warn(`couldn't load ${e.message}`); }
    } else {
      for (const url of BUILTIN) {
        try {
          const r = await fetch(url, { cache: "no-store" }); if (!r.ok) continue;
          mount([await r.json()], [stem(url)]); break;
        } catch (e) { console.warn("failed to load", url, e); }
      }
    }
    if (C.views.length && qp.has("autoplay")) play();
  }

  function loadFiles(files) {
    const picked = [...files].filter((f) => /\.json$/i.test(f.name)).slice(0, MAX_PANES);
    if (!picked.length) return;
    Promise.all(picked.map((f) => f.text().then(JSON.parse)))
      .then((datas) => {
        if (datas.some((d) => !d || !d.layout || !d.frames)) { alert("not a Kitchen Rush replay JSON"); return; }
        mount(datas, picked.map((f) => stem(f.name)));
      })
      .catch(() => alert("invalid JSON"));
  }

  // ---- playback ----------------------------------------------------------------------------
  function tick(now) {
    if (!C.playing) return;
    const dt = (now - C.lastWall) / 1000; C.lastWall = now;
    C.t += dt * 1000 / MS_PER_GS * SPEEDS[C.speedIdx];
    if (C.t >= C.tEnd) { C.t = C.tEnd; render(); pause(); return; }
    render(); C.raf = requestAnimationFrame(tick);
  }
  function play() {
    if (!C.views.length) return;
    if (C.t >= C.tEnd) { C.t = 0; for (const v of C.views) v.curIdx = 0; }
    C.playing = true; el.btnPlay.textContent = "❚❚"; C.lastWall = performance.now();
    C.raf = requestAnimationFrame(tick);
  }
  function pause() { C.playing = false; cancelAnimationFrame(C.raf); el.btnPlay.textContent = "▶"; }
  function seek(t) {
    C.t = Math.max(0, Math.min(C.tEnd, t));
    for (const v of C.views) v.curIdx = v.frameIndexAt(C.t);
    render();
  }
  function stepTime(dir) {
    pause();
    const ts = C.stepTimes; if (!ts.length) return;
    let j;
    if (dir > 0) { j = ts.findIndex((x) => x > C.t + 1e-9); if (j < 0) j = ts.length - 1; }
    else { j = ts.length - 1; while (j >= 0 && ts[j] >= C.t - 1e-9) j--; if (j < 0) j = 0; }
    for (const v of C.views) v.curIdx = Math.max(0, v.frameIndexAt(ts[j]) - 1);
    seek(ts[j]);
  }

  // ---- controls ----------------------------------------------------------------------------
  function wireControls() {
    el.btnPlay.onclick = () => (C.playing ? pause() : play());
    el.btnRestart.onclick = () => { pause(); for (const v of C.views) v.curIdx = 0; seek(0); };
    el.btnStepFwd.onclick = () => stepTime(+1);
    el.btnStepBack.onclick = () => stepTime(-1);
    el.btnSpeed.onclick = () => { C.speedIdx = (C.speedIdx + 1) % SPEEDS.length; el.btnSpeed.textContent = SPEEDS[C.speedIdx] + "×"; };
    el.scrub.oninput = () => { C.dragging = true; pause(); seek(C.tEnd * el.scrub.value / 1000); };
    el.scrub.onchange = () => { C.dragging = false; };
    el.fileInput.onchange = (e) => { if (e.target.files.length) loadFiles(e.target.files); };
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) loadFiles(e.dataTransfer.files); });
    document.addEventListener("keydown", (e) => {
      if (e.key === " ") { e.preventDefault(); C.playing ? pause() : play(); }
      else if (e.key === "ArrowRight") stepTime(+1);
      else if (e.key === "ArrowLeft") stepTime(-1);
      else if (e.key === "Home") { pause(); seek(0); }
    });
    window.addEventListener("resize", () => render());
  }

  init();
})();
