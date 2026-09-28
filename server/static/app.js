// CrushGuard control room (no build step, no libraries)
"use strict";

// ================================================================ helpers
const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const HISTORY_S = 120, FORECAST_S = 30, TAU = 15;
const LEVELS = ["green", "amber", "red"];
const LEVEL_TEXT = { green: "Normal", amber: "Building", red: "Critical", off: "Offline" };

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const COLORS = {};
function loadColors() {
  for (const k of ["good", "warning", "critical", "offline", "series", "grid", "muted", "text-2", "text",
    "panel", "steward", "accent", "stage", "dot"]) COLORS[k] = cssVar("--" + k);
}
const levelColor = (lv) => lv === "red" ? COLORS.critical : lv === "amber" ? COLORS.warning
  : lv === "green" ? COLORS.good : COLORS.offline;
const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ago = (s) => s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min`;
// Same damped extrapolation as the server (risk.forecast)
const forecastPts = (f, rate) => [0, 5, 10, 15, 20, 25, 30].map((t) => [t, Math.max(0, f + rate * TAU * (1 - Math.exp(-t / TAU)))]);

async function api(path, body, method) {
  const opts = body === undefined && !method ? {} :
    { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) };
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ================================================================ venue map
class VenueMap {
  constructor(svg, onSelect) {
    this.svg = svg; this.onSelect = onSelect; this.id = svg.id; this.venue = null; this.dots = [];
  }
  build(venue) {
    const svg = this.svg, P = this.id;
    this.venue = venue;
    svg.innerHTML = "";
    svg.setAttribute("viewBox", `0 0 ${venue.width_m} ${venue.height_m}`);
    const defs = el("defs", {}, svg);
    const glow = el("filter", { id: `${P}-glow`, x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
    el("feGaussianBlur", { stdDeviation: "0.55", result: "b" }, glow);
    const mg = el("feMerge", {}, glow); el("feMergeNode", { in: "b" }, mg); el("feMergeNode", { in: "SourceGraphic" }, mg);
    for (const lv of LEVELS) {
      const g = el("radialGradient", { id: `${P}-heat-${lv}` }, defs);
      el("stop", { offset: "0%", "stop-color": levelColor(lv), "stop-opacity": ".85" }, g);
      el("stop", { offset: "100%", "stop-color": levelColor(lv), "stop-opacity": "0" }, g);
    }
    for (let x = 0; x <= venue.width_m; x += 5) el("line", { x1: x, x2: x, y1: 0, y2: venue.height_m, class: "gridline" }, svg);
    for (let y = 0; y <= venue.height_m; y += 5) el("line", { y1: y, y2: y, x1: 0, x2: venue.width_m, class: "gridline" }, svg);

    const st = venue.stage;
    el("rect", { x: st.x, y: st.y, width: st.w, height: st.h, rx: 0.8, fill: COLORS.stage, stroke: "#26324a", "stroke-width": 0.15 }, svg);
    el("text", { x: st.x + st.w / 2, y: st.y + st.h / 2 + 0.5, "text-anchor": "middle" }, svg).textContent = "STAGE";

    this.heat = el("g", {}, svg);
    this.dotG = el("g", {}, svg);
    this.front = venue.segments.filter((s) => Math.abs(s.y1 - s.y2) < 0.1);
    this.frontY = Math.min(...venue.segments.map((s) => Math.min(s.y1, s.y2)));
    const xs = venue.segments.flatMap((s) => [s.x1, s.x2]);
    const minX = Math.min(...xs) + 0.8, maxX = Math.max(...xs) - 0.8;
    let seed = 7;
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    this.dots = Array.from({ length: 460 }, () => {
      const d = { x: minX + rnd() * (maxX - minX), y: this.frontY + 1 + rnd() * (venue.height_m - this.frontY - 3), ph: rnd() * 6.28 };
      d.node = el("circle", { r: 0.26, fill: COLORS.dot }, this.dotG);
      return d;
    });
    this.heatEls = {};
    for (const s of this.front) {
      this.heatEls[s.id] = el("ellipse", { cx: (s.x1 + s.x2) / 2, cy: s.y1 + 3.2, rx: Math.abs(s.x2 - s.x1) / 2 + 1.5, ry: 6, opacity: 0 }, this.heat);
    }
    for (const e of venue.entries || []) {
      el("rect", { x: e.x - 3, y: e.y - 0.6, width: 6, height: 1.2, rx: 0.4, fill: COLORS.accent, id: `${P}-entry-${e.id}` }, svg);
      el("text", { x: e.x, y: e.y - 1.2, "text-anchor": "middle" }, svg).textContent = e.label;
    }
    for (const g of venue.gates || []) {
      el("rect", { x: g.x - 0.9, y: g.y - 2.5, width: 1.8, height: 5, rx: 0.4, fill: "none", "stroke-width": 0.35,
        stroke: COLORS["text-2"], id: `${P}-gate-${g.id}` }, svg);
      el("text", { x: g.x, y: g.y + 4.2, "text-anchor": "middle" }, svg).textContent = g.id;
    }
    this.segEls = {};
    for (const s of venue.segments) {
      const grp = el("g", { class: "seg" }, svg);
      const halo = el("line", { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, "stroke-width": 2.4, "stroke-linecap": "round", opacity: 0.25, filter: `url(#${P}-glow)` }, grp);
      const line = el("line", { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, "stroke-width": 0.9, "stroke-linecap": "round", filter: `url(#${P}-glow)` }, grp);
      el("line", { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, class: "seg-hit", "stroke-width": 3 }, grp);
      const vertical = Math.abs(s.x1 - s.x2) < 0.1;
      const lx = (s.x1 + s.x2) / 2 + (vertical ? (s.x1 < venue.width_m / 2 ? -2.6 : 2.6) : 0);
      const ly = (s.y1 + s.y2) / 2 + (vertical ? 0 : -2.1);
      const lab = el("text", { x: lx, y: ly, "text-anchor": "middle", class: "seg-label" }, grp);
      const val = el("text", { x: lx, y: ly + 1.3, "text-anchor": "middle", class: "seg-val" }, grp);
      const stG = el("g", {}, grp);
      grp.addEventListener("click", () => this.onSelect && this.onSelect(s.id));
      this.segEls[s.id] = { halo, line, lab, val, stG, s, vertical };
    }
  }
  update(state, selected) {
    if (!this.venue) return;
    const P = this.id, red = state.thresholds.red_n, t = state.t;
    const segs = Object.fromEntries(state.segments.map((s) => [s.id, s]));
    for (const s of state.segments) {
      const e = this.segEls[s.id]; if (!e) continue;
      const lv = s.online ? s.level : "off", c = levelColor(lv);
      e.line.setAttribute("stroke", c); e.halo.setAttribute("stroke", c);
      const blink = lv === "red" ? (Math.sin(t * 8) > 0 ? 0.75 : 0.35) : lv === "amber" ? 0.45 : 0.2;
      e.halo.setAttribute("opacity", s.online ? blink : 0);
      e.line.setAttribute("stroke-dasharray", !s.online ? "0.6 0.6" : s.collapsed ? "1.2 0.7" : "");
      e.lab.textContent = s.id + (s.id === selected ? " ◂" : "");
      e.lab.setAttribute("fill", s.id === selected ? COLORS.accent : COLORS.text);
      e.val.textContent = !s.online ? "offline" : s.collapsed ? `DOWN · ${Math.round(s.tilt || 0)}°` : `${Math.round(s.f)} N`;
      e.val.setAttribute("fill", s.collapsed ? COLORS.critical : COLORS["text-2"]);
      const h = this.heatEls[s.id];
      if (h) {
        h.setAttribute("fill", `url(#${P}-heat-${s.online ? s.level : "green"})`);
        h.setAttribute("opacity", s.online ? Math.min(0.9, 0.08 + (s.score / 100) * 0.6) : 0);
      }
      // stewards covering this segment
      const names = s.stewards || [];
      const key = names.join("|") + (state._responding?.[s.id] || "");
      if (e.stKey !== key) {
        e.stKey = key; e.stG.innerHTML = "";
        const mx = (e.s.x1 + e.s.x2) / 2, my = (e.s.y1 + e.s.y2) / 2;
        names.forEach((n, i) => {
          const off = (i - (names.length - 1) / 2) * 1.6;
          const cx = e.vertical ? mx + (e.s.x1 < this.venue.width_m / 2 ? 1.8 : -1.8) : mx + off;
          const cy = e.vertical ? my + off : my + 2.1;
          const g = el("g", { class: "st-mark" }, e.stG);
          if (state._responding?.[s.id] === n) {
            el("circle", { cx, cy, r: 1.05, fill: "none", stroke: COLORS.steward, "stroke-width": 0.18 }, g);
          }
          el("circle", { cx, cy, r: 0.7, fill: COLORS.steward }, g);
          el("text", { x: cx, y: cy + 0.38, "text-anchor": "middle" }, g).textContent = n.slice(0, 1).toUpperCase();
          el("title", {}, g).textContent = `Steward ${n}`;
        });
      }
    }
    for (const d of this.dots) {
      const seg = this.front.find((s) => d.x >= Math.min(s.x1, s.x2) && d.x <= Math.max(s.x1, s.x2)) || this.front[0];
      const live = segs[seg.id];
      const p = live && live.online ? Math.min(1.2, live.f / red) : 0.2;
      const squeeze = 1 - 0.6 * Math.min(1, p);
      const jig = live ? Math.min(1.2, live.turb / 80) : 0;
      d.node.setAttribute("cx", (d.x + Math.cos(t * 1.1 + d.ph) * 0.3 * jig).toFixed(2));
      d.node.setAttribute("cy", (this.frontY + 0.8 + (d.y - this.frontY) * squeeze + Math.sin(t * 1.3 + d.ph) * 0.4 * jig).toFixed(2));
    }
    const sim = state.sim;
    for (const g of this.venue.gates || []) {
      const open = sim && sim.gates_open.includes(g.id);
      const n = $(`${P}-gate-${g.id}`);
      n.setAttribute("stroke", open ? COLORS.good : COLORS["text-2"]);
      n.setAttribute("stroke-dasharray", open ? "0.5 0.5" : "");
    }
    for (const e of this.venue.entries || []) {
      $(`${P}-entry-${e.id}`).setAttribute("fill", sim && !sim.entry_open ? COLORS.critical : COLORS.accent);
    }
  }
}

// ================================================================ force chart (past 2 min + 30 s forecast)
class ForceChart {
  constructor(svg, tip) {
    this.svg = svg; this.tip = tip; this.g = null;
    svg.addEventListener("mousemove", (e) => this.hover(e));
    svg.addEventListener("mouseleave", () => { tip.hidden = true; if (this.xh) this.xh.setAttribute("opacity", 0); });
  }
  draw(pts, th, now, fc) {
    const svg = this.svg;
    const W = svg.clientWidth || 500, H = svg.clientHeight || 210;
    const m = { l: 40, r: 12, t: 12, b: 22 };
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = "";
    const past = pts.filter((p) => p[0] >= now - HISTORY_S);
    const fcMax = fc && fc.length ? Math.max(...fc.map((p) => p[1])) : 0;
    const maxF = Math.max(th.red_n * 1.3, fcMax * 1.08, ...past.map((p) => p[1] * 1.08));
    const span = HISTORY_S + FORECAST_S;
    const x = (t) => m.l + ((t - (now - HISTORY_S)) / span) * (W - m.l - m.r);
    const y = (f) => H - m.b - (f / maxF) * (H - m.t - m.b);
    this.g = { x, y, m, W, H, pts: past, now, fc };
    // critical zone
    el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: Math.max(0, y(th.red_n) - m.t), fill: COLORS.critical, opacity: 0.08 }, svg);
    // future area
    el("rect", { x: x(now), y: m.t, width: x(now + FORECAST_S) - x(now), height: H - m.t - m.b, fill: COLORS.accent, opacity: 0.04 }, svg);
    const step = maxF > 900 ? 200 : 100;
    for (let v = 0; v <= maxF; v += step) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: COLORS.grid, "stroke-width": 1 }, svg);
      el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = v;
    }
    for (const s of [-120, -90, -60, -30, 0, 30]) {
      el("text", { x: x(now + s), y: H - 6, "text-anchor": s === -120 ? "start" : s === 30 ? "end" : "middle" }, svg)
        .textContent = s === 0 ? "now" : s > 0 ? `+${s}s` : `${s}s`;
    }
    el("line", { x1: x(now), x2: x(now), y1: m.t, y2: H - m.b, stroke: COLORS.muted, "stroke-width": 1, "stroke-dasharray": "2 3" }, svg);
    for (const [val, col, name] of [[th.amber_n, COLORS.warning, "Building"], [th.red_n, COLORS.critical, "Critical"]]) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(val), y2: y(val), stroke: col, "stroke-width": 1.2, "stroke-dasharray": "5 4", opacity: 0.9 }, svg);
      el("text", { x: m.l + 6, y: y(val) - 4 }, svg).textContent = `${name} ${val} N`;
    }
    if (past.length > 1) {
      const d = past.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
      el("path", { d: d + `L${x(past[past.length - 1][0]).toFixed(1)},${y(0)}L${x(past[0][0]).toFixed(1)},${y(0)}Z`, fill: COLORS.series, opacity: 0.1 }, svg);
      el("path", { d, fill: "none", stroke: COLORS.series, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    }
    if (fc && fc.length) {
      const d = fc.map((p, i) => `${i ? "L" : "M"}${x(now + p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
      const end = fc[fc.length - 1][1];
      const col = end >= th.red_n ? COLORS.critical : end >= th.amber_n ? COLORS.warning : COLORS.series;
      el("path", { d, fill: "none", stroke: col, "stroke-width": 2, "stroke-dasharray": "6 4" }, svg);
      el("circle", { cx: x(now + 30), cy: y(end), r: 3.5, fill: col }, svg);
      const tx = el("text", { x: x(now + 30) - 6, y: Math.max(m.t + 10, y(end) - 8), "text-anchor": "end" }, svg);
      tx.textContent = `forecast ${Math.round(end)} N`; tx.setAttribute("fill", col);
    }
    if (past.length) {
      const last = past[past.length - 1];
      el("circle", { cx: x(last[0]), cy: y(last[1]), r: 4, fill: COLORS.series, stroke: COLORS.panel, "stroke-width": 2 }, svg);
    }
    this.xh = el("line", { y1: m.t, y2: H - m.b, stroke: COLORS["text-2"], "stroke-width": 1, opacity: 0 }, svg);
  }
  hover(ev) {
    const g = this.g, tip = this.tip;
    if (!g || g.pts.length < 2) { tip.hidden = true; return; }
    const rect = this.svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * g.W;
    const t = g.now - HISTORY_S + ((px - g.m.l) / (g.W - g.m.l - g.m.r)) * (HISTORY_S + FORECAST_S);
    let label, xv;
    if (t > g.now && g.fc && g.fc.length) {
      let best = g.fc[0];
      for (const p of g.fc) if (Math.abs(g.now + p[0] - t) < Math.abs(g.now + best[0] - t)) best = p;
      label = `in ${best[0]} s · forecast ${Math.round(best[1])} N`; xv = g.x(g.now + best[0]);
    } else {
      let best = g.pts[0];
      for (const p of g.pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      label = `${Math.round(g.now - best[0])} s ago · ${Math.round(best[1])} N`; xv = g.x(best[0]);
    }
    this.xh.setAttribute("x1", xv); this.xh.setAttribute("x2", xv); this.xh.setAttribute("opacity", 1);
    tip.hidden = false; tip.textContent = label;
    tip.style.left = Math.min(rect.width - 190, Math.max(0, (xv / g.W) * rect.width + 10)) + "px";
    tip.style.top = "6px";
  }
}

// ================================================================ app state
let venue = null, live = null, selected = null, view = "venue";
const history = {};
const seenCritical = new Set();
const venueMap = new VenueMap($("map"), (id) => { selected = id; renderVenue(); });
const chart = new ForceChart($("chart"), $("tip"));

// ================================================================ banner (shared)
function renderBanner(st, prefix = "") {
  const b = $("banner"), lv = st.overall;
  b.dataset.level = lv;
  const on = st.segments.filter((s) => s.online);
  const worst = [...on].sort((a, c) => c.score - a.score)[0];
  $("bIcon").textContent = lv === "red" ? "!" : lv === "amber" ? "▲" : "✓";
  if (lv === "red") {
    const help = st.alerts.find((a) => a.active && a.key && a.key.startsWith("help:"));
    const down = st.alerts.find((a) => a.active && a.key && a.key.startsWith("collapse:"));
    $("bTitle").textContent = prefix + (down ? `CRITICAL: ${down.title}`
      : st.wave.length ? `CRITICAL: crowd wave across ${st.wave.join(", ")}`
      : help ? `CRITICAL: ${help.title}` : `CRITICAL: crush risk at ${worst ? worst.label : "venue"}`);
    $("bSub").textContent = "Stop entry, open relief gates, announce on PA now";
  } else if (lv === "amber") {
    $("bTitle").textContent = prefix + `Pressure building at ${worst.label}`;
    $("bSub").textContent = worst.eta_s != null ? `Critical in about ${Math.round(worst.eta_s)} s if this continues` : "Watch closely and slow down entry";
  } else {
    $("bTitle").textContent = prefix + "All barricades normal";
    $("bSub").textContent = `${on.length} of ${st.segments.length} sensors reporting`;
  }
}

// ================================================================ alerts
function renderAlertList(ul, list, now, withButtons) {
  const SEV = { critical: 0, warning: 1, info: 2 };
  const active = list.filter((a) => a.active)
    .sort((a, b) => SEV[a.severity] - SEV[b.severity] || b.started - a.started);
  const sorted = [...active, ...list.filter((a) => !a.active)].slice(0, 40);
  if (!sorted.length) { ul.innerHTML = '<li class="empty">No alerts yet</li>'; ul._keyed = false; return active; }
  if (!ul._keyed) { ul.innerHTML = ""; ul._keyed = true; }
  const keep = new Set();
  sorted.forEach((a, i) => {
    keep.add(String(a.id));
    let li = ul.querySelector(`li[data-id="${a.id}"]`);
    if (!li) {
      li = document.createElement("li"); li.dataset.id = a.id;
      li.innerHTML = '<span class="ico"></span><div><div class="a-title"></div><div class="a-act"></div><div class="a-resp"></div><div class="a-meta"></div></div><span class="a-btn"></span>';
    }
    li.className = `${a.severity} ${a.active ? "" : "inactive"}`;
    li.querySelector(".ico").textContent = a.severity === "critical" ? "!" : a.severity === "warning" ? "▲" : "i";
    li.querySelector(".a-title").textContent = a.title;
    li.querySelector(".a-act").textContent = a.action;
    li.querySelector(".a-resp").textContent = a.responder ? `● ${a.responder} responding · ${ago(now - a.responded_at)} ago` : "";
    li.querySelector(".a-meta").textContent = fmtTime(a.started) +
      (a.peak_severity === "critical" && a.severity !== "critical" ? " · reached CRITICAL" : "") +
      (a.active ? "" : " · cleared " + fmtTime(a.ended ?? a.updated)) + (a.acked ? " · acknowledged" : "");
    const slot = li.querySelector(".a-btn"), want = withButtons && a.active && !a.acked;
    if (want && !slot.firstChild) slot.innerHTML = `<button class="btn small" data-ack="${a.id}">Ack</button>`;
    if (!want && slot.firstChild) slot.innerHTML = "";
    if (ul.children[i] !== li) ul.insertBefore(li, ul.children[i] || null);
  });
  [...ul.children].forEach((li) => { if (!keep.has(li.dataset.id)) li.remove(); });
  return active;
}
$("alerts").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ack]");
  if (b) api(`/api/alerts/${b.dataset.ack}/ack`, {});
});

// ================================================================ venue view
function respondingMap(st) {
  const m = {};
  for (const a of st.alerts) if (a.active && a.responder && a.segment) m[a.segment] = a.responder;
  return m;
}

function renderVenue() {
  if (!live || !venue) return;
  const st = live;
  st._responding = respondingMap(st);
  if (view === "venue" || view === "national") renderBanner(st);
  venueMap.update(st, selected);
  $("alertCount").textContent = `${st.alerts.filter((a) => a.active).length} active`;
  const active = renderAlertList($("alerts"), st.alerts, st.t, true);
  for (const a of active) {
    if (a.severity === "critical" && !seenCritical.has(a.id + a.title)) {
      seenCritical.add(a.id + a.title);
      beep();
      if ($("autoPa").checked) playPA(a.segment);
    }
  }
  // detail
  const s = st.segments.find((x) => x.id === selected);
  if (s) {
    $("detailTitle").textContent = `${s.id} · ${s.label}`;
    $("tForce").innerHTML = s.online ? `${Math.round(s.f)}<small>N</small>` : "—";
    $("tRate").innerHTML = s.online ? `${s.rate > 0 ? "+" : ""}${Math.round(s.rate)}<small>N/s</small>` : "—";
    $("tEta").innerHTML = !s.online ? "—" : s.eta_s === 0 ? "NOW" : s.eta_s != null ? `${Math.round(s.eta_s)}<small>s</small>` : "safe";
    $("tEta").className = "t-val " + (s.eta_s === 0 ? "red" : s.eta_s != null && s.eta_s < 20 ? "amber" : "");
    const fc = s.forecast && s.forecast.length ? s.forecast : (s.online ? forecastPts(s.f, s.rate) : []);
    const end = fc.length ? fc[fc.length - 1][1] : null;
    $("tFc").innerHTML = end == null ? "—" : `${Math.round(end)}<small>N</small>`;
    $("tFc").className = "t-val " + (end >= st.thresholds.red_n ? "red" : end >= st.thresholds.amber_n ? "amber" : "");
    $("reasons").textContent = !s.online ? "Sensor offline, no data" :
      (s.reasons.length ? "Why: " + s.reasons.join(" · ") : `Status: ${LEVEL_TEXT[s.level]}`) +
      (s.stewards && s.stewards.length ? ` · Stewards here: ${s.stewards.join(", ")}` : " · No steward assigned");
    $("pushBtn").hidden = st.mode !== "simulation";
    $("restoreBtn").hidden = !s.collapsed;
    chart.draw(history[s.id] || [], st.thresholds, st.t, fc);
  }
  renderNodes(st);
  renderStewards(st);
  renderSim(st);
}

function renderNodes(st) {
  const tb = document.querySelector("#nodes tbody");
  const rows = st.segments.map((s) => {
    const lv = s.online ? s.level : "off";
    const bat = s.bat ? `${(s.bat / 1000).toFixed(2)} V` : "—";
    return `<tr data-id="${s.id}" class="${s.id === selected ? "sel" : ""}">
      <td><b>${s.id}</b></td><td>${esc(s.label)}</td>
      <td><span class="pill ${lv}">${LEVEL_TEXT[lv]}</span></td>
      <td class="num">${s.online ? Math.round(s.f) : "—"}</td>
      <td class="num">${s.online ? Math.round(s.score) : "—"}<span class="bar"><i style="width:${Math.min(100, s.score)}%"></i></span></td>
      <td>${s.stewards && s.stewards.length ? esc(s.stewards.join(", ")) : '<span class="muted">none</span>'}</td>
      <td>${s.online ? `${s.age_s}s ago` : s.age_s != null ? `lost ${Math.round(s.age_s)}s` : "never"}</td>
      <td class="num">${bat}</td><td class="num">${s.online ? s.rssi + " dBm" : "—"}</td></tr>`;
  });
  if (tb.rows.length !== rows.length) tb.innerHTML = rows.join("");
  rows.forEach((html, i) => {
    const tmp = document.createElement("tbody"); tmp.innerHTML = html;
    const src = tmp.rows[0], dst = tb.rows[i];
    dst.dataset.id = src.dataset.id; dst.className = src.className;
    if (dst.innerHTML !== src.innerHTML) dst.innerHTML = src.innerHTML;
  });
  const on = st.segments.filter((s) => s.online).length;
  $("nodeSummary").textContent = `${on}/${st.segments.length} online · red limit ${st.thresholds.red_n} N`;
}
document.querySelector("#nodes tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) { selected = tr.dataset.id; renderVenue(); }
});

function renderStewards(st) {
  const list = st.stewards || [];
  const online = list.filter((s) => s.online);
  $("stewardChip").textContent = `${online.length} steward${online.length === 1 ? "" : "s"}`;
  $("stewardSummary").textContent = `${online.length} online`;
  const alertById = Object.fromEntries(st.alerts.map((a) => [a.id, a]));
  const html = list.length ? list.map((s) => {
    const a = s.responding_to != null ? alertById[s.responding_to] : null;
    const resp = a && a.active ? `responding at ${a.segment || "venue"}` : "";
    return `<li class="${s.online ? "" : "off"}"><span class="av">${esc(s.name.slice(0, 1).toUpperCase())}</span>
      <div><div class="s-name">${esc(s.name)}</div><div class="s-meta">${s.segments.length ? esc(s.segments.join(", ")) : "no zone"} · ${s.online ? "online" : "offline " + ago(s.age_s)}</div></div>
      <span class="s-resp">${resp}</span></li>`;
  }).join("") : '<li class="muted small">No stewards checked in yet. Open the link above on a phone.</li>';
  const ul = $("stewardList");
  if (ul._html !== html) { ul.innerHTML = html; ul._html = html; }
}

async function loadConnectInfo() {
  try {
    const info = await api("/api/connect");
    $("stewardUrl").textContent = info.steward_url;
    if (info.qr) { $("qr").src = "/api/qr.svg?text=" + encodeURIComponent(info.steward_url); $("qr").hidden = false; }
  } catch (e) { $("stewardUrl").textContent = location.origin + "/steward"; }
}

function buildFailSelect() {
  $("failSel").innerHTML = venue.segments.map((s) => `<option value="${s.id}">${s.id} (node ${s.node})</option>`).join("");
}
function renderSim(st) {
  const sim = st.sim, ops = st.ops || { entry_open: true, gates_open: [] };
  document.querySelectorAll(".sim-only").forEach((n) => { n.hidden = !sim; });
  $("ctlTitle").textContent = sim ? "Controls" : "Response log";
  if (view !== "replay" && view !== "plan") {
    const chip = $("modeChip");
    chip.textContent = st.mode === "simulation" ? "SIMULATION" : "LIVE SENSORS";
    chip.className = "chip " + (st.mode === "simulation" ? "sim" : "live") + (st.gateway_ok ? "" : " bad");
    if (!st.gateway_ok) chip.textContent += " · gateway lost";
  }
  document.querySelectorAll("[data-gate]").forEach((b) => {
    const open = ops.gates_open.includes(b.dataset.gate);
    b.classList.toggle("on", open); b.textContent = (open ? "Close " : "Open ") + b.dataset.gate;
  });
  $("entryBtn").classList.toggle("on", !ops.entry_open);
  $("entryBtn").textContent = ops.entry_open ? "Stop entry" : "Resume entry";
  renderEnv(st.env);
  if (!sim) { $("simStatus").textContent = "press when you act, so the report has the timings"; return; }
  document.querySelectorAll("[data-scn]").forEach((b) => b.classList.toggle("on", b.dataset.scn === sim.scenario));
  $("failBtn").textContent = sim.offline.includes($("failSel").value) ? "Bring node back" : "Take node offline";
  $("collapseBtn").textContent = `Barricade collapse at ${selected}`;
  $("simStatus").textContent = `scenario: ${sim.scenario}` + (sim.collapsed.length ? ` · barricade down: ${sim.collapsed.join(", ")}` : "");
  if (!renderSim._envInit) { $("envT").value = sim.temp_c; $("envH").value = sim.rh; renderSim._envInit = true; }
}
function renderEnv(env) {
  const chip = $("envChip");
  if (!env) { chip.hidden = true; return; }
  chip.hidden = false;
  chip.textContent = `Feels like ${Math.round(env.heat_index_c)} °C · ${env.category}`;
  chip.className = "chip" + (env.multiplier >= 1.2 ? " heat-danger" : env.multiplier > 1 ? " heat-caution" : "");
  chip.title = `${env.temp_c} °C, ${env.rh}% humidity. Pressure limits ${env.multiplier > 1 ? "lowered by " + Math.round((1 - 1 / env.multiplier) * 100) + "%" : "normal"}.`;
}
document.querySelectorAll("[data-scn]").forEach((b) => b.onclick = () => api("/api/sim", { action: "scenario", value: b.dataset.scn }));
document.querySelectorAll("[data-gate]").forEach((b) => b.onclick = () =>
  api("/api/action", { action: "gate", value: b.dataset.gate, on: !(live.ops.gates_open || []).includes(b.dataset.gate) }));
$("entryBtn").onclick = () => api("/api/action", { action: "entry", on: !live.ops.entry_open });
$("failBtn").onclick = () => { const id = $("failSel").value; api("/api/sim", { action: "offline", value: id, on: !live.sim.offline.includes(id) }); };
$("pushBtn").onclick = () => api("/api/sim", { action: "push", value: selected });
$("collapseBtn").onclick = () => api("/api/sim", { action: "collapse", value: selected, on: true });
$("restoreBtn").onclick = () => api(`/api/segments/${selected}/restore`, {});
$("envBtn").onclick = () => api("/api/sim", { action: "env", value: `${$("envT").value},${$("envH").value}` });
document.querySelectorAll("[data-env]").forEach((b) => b.onclick = () => {
  const [t, h] = b.dataset.env.split(","); $("envT").value = t; $("envH").value = h;
  api("/api/sim", { action: "env", value: b.dataset.env });
});
$("identifyBtn").onclick = () => { const s = venue.segments.find((x) => x.id === selected); if (s) api(`/api/nodes/${s.node}/identify`, {}); };

// ================================================================ PA + sound
let audioCtx = null;
function beep() {
  try {
    audioCtx ||= new AudioContext();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.15, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.6);
    o.start(); o.stop(audioCtx.currentTime + 0.6);
  } catch (e) { /* audio needs one click on the page first */ }
}
async function playPA(segment) {
  const lang = $("paLang").value, q = new URLSearchParams({ lang });
  if (segment) q.set("segment", segment);
  const { text } = await api("/api/pa?" + q);
  $("bSub").textContent = "PA: " + text;
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = { en: "en-IN", hi: "hi-IN", ta: "ta-IN" }[lang];
  const v = speechSynthesis.getVoices().find((x) => x.lang.toLowerCase().startsWith(lang));
  if (v) u.voice = v;
  u.rate = 0.95; speechSynthesis.cancel(); speechSynthesis.speak(u);
}
$("paBtn").onclick = () => {
  const worst = live && [...live.segments].sort((a, b) => b.score - a.score)[0];
  playPA(worst && worst.score > 0 ? worst.id : selected);
};

// ================================================================ national view
const nat = { built: false, markers: {} };
function buildIndia() {
  const svg = $("india"), M = window.INDIA_MAP;
  svg.setAttribute("viewBox", M.viewBox);
  el("path", { d: M.path, class: "land" }, svg);
  nat.layer = el("g", {}, svg);
  nat.built = true;
}
const proj = (lat, lon) => { const M = window.INDIA_MAP; return [(lon - M.lon0) * M.k * M.s, (M.lat1 - lat) * M.s]; };
function renderNational() {
  const n = live && live.national;
  if (!n) return;
  if (!nat.built) buildIndia();
  $("kVenues").textContent = n.totals.venues;
  $("kSensors").textContent = `${n.totals.sensors_online}/${n.totals.sensors_total}`;
  $("kRed").textContent = n.totals.red;
  $("kAmber").textContent = n.totals.amber;
  for (const v of n.venues) {
    let mk = nat.markers[v.id];
    const [x, y] = proj(v.lat, v.lon);
    if (!mk) {
      const g = el("g", { class: "venue-dot" }, nat.layer);
      const pulse = el("circle", { cx: x, cy: y, r: 9, class: "pulse", fill: "none", "stroke-width": 3 }, g);
      const dot = el("circle", { cx: x, cy: y, r: v.is_main ? 11 : 8, stroke: "#06080c", "stroke-width": 3 }, g);
      const side = v.label || (x < 700 ? "right" : "left");
      const lx = side === "right" ? x + 15 : side === "left" ? x - 15 : x;
      const ly = side === "above" ? y - 16 : side === "below" ? y + 28 : y + 6;
      const anchor = side === "right" ? "start" : side === "left" ? "end" : "middle";
      const lab = el("text", { x: lx, y: ly, class: "v-label", "text-anchor": anchor }, g);
      lab.textContent = v.city + (v.is_main ? " ★" : "");
      g.addEventListener("click", () => { if (v.is_main) switchView("venue"); });
      g.addEventListener("mousemove", (e) => natTip(e, v.id));
      g.addEventListener("mouseleave", () => { $("natTip").hidden = true; });
      mk = nat.markers[v.id] = { g, pulse, dot };
    }
    const c = levelColor(v.overall);
    mk.dot.setAttribute("fill", c);
    mk.pulse.setAttribute("stroke", c);
    mk.pulse.style.display = v.overall === "green" ? "none" : "";
    mk.pulse.style.animationDuration = v.overall === "red" ? "1s" : "2s";
  }
  const order = { red: 0, amber: 1, green: 2 };
  const sorted = [...n.venues].sort((a, b) => order[a.overall] - order[b.overall] || b.max_score - a.max_score);
  const html = sorted.map((v) => `<li class="${v.overall}" data-id="${v.id}">
      <div class="v-top"><span class="v-name">${esc(v.name)}</span><span class="pill ${v.overall}">${LEVEL_TEXT[v.overall]}</span></div>
      <div class="v-where">${esc(v.city)}, ${esc(v.state)} · ${esc(v.type)} ${v.is_main ? '<span class="tag main">this venue</span>' : '<span class="tag">simulated</span>'}</div>
      <div class="v-strip">${v.segments.map((s) => `<i class="${s}"></i>`).join("")}</div>
      <div class="v-meta"><span>Peak push <b>${Math.round(v.max_force)} N</b></span><span>Sensors ${v.sensors_online}/${v.sensors_total}</span>
        <span>${v.critical ? `<b style="color:var(--critical)">${v.critical} critical</b>` : "0 critical"}</span>${v.wave ? '<span style="color:var(--critical)">crowd wave</span>' : ""}</div></li>`).join("");
  const ul = $("venueList");
  if (ul._html !== html) { ul.innerHTML = html; ul._html = html; }
}
function natTip(e, id) {
  const v = live.national.venues.find((x) => x.id === id); if (!v) return;
  const tip = $("natTip"), box = $("india").parentElement.getBoundingClientRect();
  tip.hidden = false;
  tip.innerHTML = `<b>${esc(v.name)}</b><br>${esc(v.city)} · ${LEVEL_TEXT[v.overall]} · peak ${Math.round(v.max_force)} N`;
  tip.style.left = (e.clientX - box.left + 12) + "px"; tip.style.top = (e.clientY - box.top + 12) + "px";
}
$("venueList").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li && li.dataset.id === "main") switchView("venue");
});

// ================================================================ replay view
const rp = { tl: null, t: 0, playing: false, speed: 5, seg: null, last: 0 };
const rmap = new VenueMap($("rmap"), (id) => { rp.seg = id; renderReplay(); });
const rchart = new ForceChart($("rchart"), $("rtip"));

async function loadSessions() {
  const items = await api("/api/sessions");
  const sel = $("sessionSel");
  const prev = sel.value;
  sel.innerHTML = items.length ? items.map((s) => {
    const d = new Date(s.start * 1000);
    const dur = s.duration_s < 60 ? `${Math.round(s.duration_s)} s` : `${Math.floor(s.duration_s / 60)} min ${Math.round(s.duration_s % 60)} s`;
    return `<option value="${s.name}">${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour12: false })} · ${dur}${s.current ? " · this session" : ""}</option>`;
  }).join("") : '<option value="">No logged sessions yet</option>';
  if (prev && items.some((s) => s.name === prev)) sel.value = prev;
  if (items.length) await loadReplay(sel.value);
}
async function loadReplay(name) {
  if (!name) return;
  const keepT = rp.tl && rp.tl.name === name ? rp.t : null;
  rp.tl = await api("/api/replay/" + encodeURIComponent(name));
  const v = { ...venue, segments: rp.tl.segments.map((s) => ({ ...(venue.segments.find((x) => x.id === s.id) || {}), ...s })) };
  rmap.build(v);
  rp.seg ||= rp.tl.segments[2] ? rp.tl.segments[2].id : rp.tl.segments[0].id;
  rp.t = keepT ?? rp.tl.start;
  // jump to the first critical moment if we are at the start
  if (keepT == null) {
    const firstRed = rp.tl.alerts.find((a) => a.peak_severity === "critical");
    if (firstRed) rp.t = Math.max(rp.tl.start, firstRed.started - 20);
  }
  renderReplay();
}
function frameAt(t) {
  const tl = rp.tl, i = Math.max(0, Math.min(tl.frames.length - 1, Math.round((t - tl.start) / tl.frame_s)));
  return [tl.frames[i], i];
}
function replayState(t) {
  const tl = rp.tl, [fr] = frameAt(t);
  const alerts = tl.alerts.filter((a) => a.started <= t).map((a) => {
    const active = a.ended == null ? true : t <= a.ended;
    return { ...a, active, responder: a.responded_at && a.responded_at <= t ? a.responder : null };
  }).reverse();
  const segs = tl.segments.map((s, k) => {
    const [f, rate, turb, score, eta, lv, on] = fr.s[k];
    const covering = [];
    return { ...s, f, rate, turb, score, eta_s: eta, level: LEVELS[lv], online: !!on, reasons: [], stewards: covering };
  });
  return { t: fr.t, overall: LEVELS[fr.o], wave: fr.w, thresholds: tl.thresholds, segments: segs, alerts, sim: null, mode: "replay" };
}
function renderReplay() {
  if (!rp.tl) return;
  const tl = rp.tl, st = replayState(rp.t);
  st._responding = respondingMap(st);
  if (view === "replay") renderBanner(st, "REPLAY · ");
  rmap.update(st, rp.seg);
  $("rTime").textContent = `${fmtTime(rp.t)}  (+${Math.round(rp.t - tl.start)} s of ${Math.round(tl.duration_s)} s)`;
  $("rClock").textContent = fmtTime(rp.t);
  const k = tl.segments.findIndex((s) => s.id === rp.seg);
  const s = st.segments[k];
  $("rDetailTitle").textContent = `${s.id} · ${s.label} · ${Math.round(s.f)} N`;
  const [, idx] = frameAt(rp.t);
  const pts = [];
  for (let i = Math.max(0, idx - HISTORY_S / tl.frame_s); i <= idx; i++) pts.push([tl.frames[i].t, tl.frames[i].s[k][0]]);
  rchart.draw(pts, tl.thresholds, st.t, s.online ? forecastPts(s.f, s.rate) : []);
  drawTimeline();
  renderLog(st);
  renderReplayStats();
}
function fmtDur(sec) {
  if (sec == null) return "—";
  const a = Math.abs(sec);
  const txt = a < 60 ? `${Math.round(a)} s` : `${Math.floor(a / 60)} min ${Math.round(a % 60)} s`;
  return sec < 0 ? `${txt} before` : txt;
}
function renderReplayStats() {
  const S = rp.tl.stats; if (!S) return;
  if (rp._statsFor === rp.tl.name + rp.tl.end) return;
  rp._statsFor = rp.tl.name + rp.tl.end;
  const inc = S.incidents[0];
  const cards = [
    ["Critical incidents", S.incidents.length, S.incidents.length ? "red" : "green", `${fmtDur(S.time_red_s)} in red in total`],
    ["Warning before critical", inc ? fmtDur(inc.warning_lead_s) : "—", "", "amber came this long before red"],
    ["Steward on it", inc && inc.steward ? fmtDur(inc.steward_delay_s) : "—", "", inc && inc.steward ? `${inc.steward}, after red` : "no steward response logged"],
    ["First action", inc && inc.first_action ? fmtDur(inc.action_delay_s) : "—", "", inc && inc.first_action ? `${inc.first_action}, after red` : "no action logged"],
    ["Recovered in", inc ? fmtDur(inc.duration_s) : "—", "", "red until back below critical"],
    ["Peak push", `${Math.round(S.peak_force_n)} N`, "", `${S.alerts.critical} critical / ${S.alerts.warning} warning alerts`],
  ];
  $("rStats").innerHTML = cards.map(([l, v, c, sub]) => `<div class="kpi ${c}"><div class="k-val">${esc(v)}</div><div class="k-label">${esc(l)}</div><div class="k-sub">${esc(sub)}</div></div>`).join("");
  $("rStatsNote").textContent = S.incidents.length > 1 ? `showing the first of ${S.incidents.length} incidents; the report lists all` : "";
}
$("reportBtn").onclick = () => { if (rp.tl) window.open("/report?session=" + encodeURIComponent(rp.tl.name), "_blank"); };
function eventText(e) {
  switch (e.kind) {
    case "respond": return [`${e.steward} responding at ${e.segment}`, "resp"];
    case "help": return [`${e.steward} requested backup at ${e.segment}`, "critical"];
    case "checkin": return [`Steward ${e.steward} checked in (${(e.segments || []).join(", ") || "no zone"})`, "resp"];
    case "ack": return [`Operator acknowledged alert #${e.alert}`, "act"];
    case "pa": return [`PA announcement played (${e.lang})`, "act"];
    case "sim":
      if (e.action === "scenario") return [`Simulator: scenario ${e.value}`, "act"];
      if (e.action === "entry") return [e.on ? "Operator: entry resumed" : "Operator: entry STOPPED", "act"];
      if (e.action === "gate") return [`Operator: ${e.value} ${e.on ? "opened" : "closed"}`, "act"];
      if (e.action === "push") return [`Simulator: push at ${e.value}`, "act"];
      if (e.action === "offline") return [`Simulator: node ${e.value} ${e.on ? "offline" : "back"}`, "act"];
      return [`Simulator: ${e.action}`, "act"];
    default: return [e.kind, "act"];
  }
}
function renderLog(st) {
  const tl = rp.tl, t = rp.t;
  const items = [];
  for (const a of tl.alerts) if (a.started <= t) items.push([a.started, a.title, a.peak_severity === "critical" ? "critical" : "warning"]);
  for (const e of tl.events) if (e.t <= t) { const [txt, cls] = eventText(e); items.push([e.t, txt, cls]); }
  items.sort((a, b) => b[0] - a[0]);
  const html = items.slice(0, 80).map(([ts, txt, cls]) => `<li><span class="lt">${fmtTime(ts)}</span><span class="${cls}">${esc(txt)}</span></li>`).join("")
    || '<li class="muted">Nothing yet at this point in the session.</li>';
  const ul = $("rLog");
  if (ul._html !== html) { ul.innerHTML = html; ul._html = html; }
}
function drawTimeline() {
  const svg = $("timeline"), tl = rp.tl;
  const W = svg.clientWidth || 900, H = svg.clientHeight || 96, m = { l: 8, r: 8, t: 8, b: 18 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (svg._for !== tl.name + tl.end + W) {
    svg._for = tl.name + tl.end + W;
    svg.innerHTML = "";
    const x = (t) => m.l + ((t - tl.start) / Math.max(1, tl.end - tl.start)) * (W - m.l - m.r);
    svg._x = x;
    const bw = Math.max(1, (W - m.l - m.r) / tl.frames.length);
    const hmax = H - m.t - m.b;
    for (const fr of tl.frames) {
      const maxScore = Math.max(0, ...fr.s.map((s) => s[3]));
      const h = Math.max(2, Math.min(1, maxScore / 130) * hmax);
      el("rect", { x: x(fr.t), y: H - m.b - h, width: bw + 0.5, height: h, fill: levelColor(LEVELS[fr.o]), opacity: fr.o ? 0.85 : 0.35 }, svg);
    }
    for (const e of tl.events) {
      const cls = e.kind === "respond" || e.kind === "checkin" ? COLORS.steward : e.kind === "help" ? COLORS.critical : COLORS.accent;
      const cx = x(e.t);
      el("rect", { x: cx - 4, y: m.t - 2, width: 8, height: 8, fill: cls, transform: `rotate(45 ${cx} ${m.t + 2})` }, svg);
    }
    const n = Math.min(8, Math.max(2, Math.floor(W / 140)));
    for (let i = 0; i <= n; i++) {
      const t = tl.start + (i / n) * (tl.end - tl.start);
      el("text", { x: x(t), y: H - 4, "text-anchor": i === 0 ? "start" : i === n ? "end" : "middle" }, svg).textContent = fmtTime(t);
    }
    svg._head = el("line", { y1: 2, y2: H - m.b, stroke: "#fff", "stroke-width": 2 }, svg);
  }
  const px = svg._x(rp.t);
  svg._head.setAttribute("x1", px); svg._head.setAttribute("x2", px);
}
function seekFromEvent(e) {
  const svg = $("timeline"), tl = rp.tl; if (!tl) return;
  const r = svg.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, ((e.clientX - r.left) / r.width * (svg.clientWidth) - 8) / (svg.clientWidth - 16)));
  rp.t = tl.start + frac * (tl.end - tl.start);
  renderReplay();
}
$("timeline").addEventListener("pointerdown", (e) => { $("timeline").setPointerCapture(e.pointerId); rp.drag = true; seekFromEvent(e); });
$("timeline").addEventListener("pointermove", (e) => { if (rp.drag) seekFromEvent(e); });
$("timeline").addEventListener("pointerup", () => { rp.drag = false; });
$("playBtn").onclick = () => {
  if (!rp.tl) return;
  if (!rp.playing && rp.t >= rp.tl.end - 0.5) rp.t = rp.tl.start;
  rp.playing = !rp.playing; rp.last = performance.now();
  $("playBtn").textContent = rp.playing ? "❚❚ Pause" : "▶ Play";
};
document.querySelectorAll("[data-speed]").forEach((b) => b.onclick = () => {
  rp.speed = +b.dataset.speed;
  document.querySelectorAll("[data-speed]").forEach((x) => x.classList.toggle("on", x === b));
});
$("sessionSel").onchange = () => loadReplay($("sessionSel").value);
$("refreshSessions").onclick = () => loadSessions();
function replayTick(now) {
  if (rp.playing && rp.tl) {
    rp.t += ((now - rp.last) / 1000) * rp.speed;
    if (rp.t >= rp.tl.end) { rp.t = rp.tl.end; rp.playing = false; $("playBtn").textContent = "▶ Play"; }
    renderReplay();
  }
  rp.last = now;
  requestAnimationFrame(replayTick);
}
requestAnimationFrame(replayTick);

// ================================================================ plan view (pre-event capacity)
const PRESETS = {
  rally: { name: "Political rally, open ground", area_m2: 3000, crowd: 12000, front_share: 30, front_area_m2: 500, exit_width_m: 10, entry_width_m: 4, arrival_hours: 1.5, barricade_m: 64, target_egress_min: 10, stepped: false },
  temple: { name: "Temple festival, queue complex", area_m2: 1800, crowd: 6000, front_share: 50, front_area_m2: 300, exit_width_m: 6, entry_width_m: 3, arrival_hours: 3, barricade_m: 120, target_egress_min: 10, stepped: true },
  stadium: { name: "Stadium gates before a match", area_m2: 2500, crowd: 9000, front_share: 40, front_area_m2: 400, exit_width_m: 14, entry_width_m: 5, arrival_hours: 1, barricade_m: 80, target_egress_min: 8, stepped: false },
};
function planInputs() {
  const f = $("planForm"), o = {};
  for (const el_ of f.elements) {
    if (!el_.name) continue;
    o[el_.name] = el_.type === "checkbox" ? el_.checked : el_.type === "number" ? parseFloat(el_.value) : el_.value;
  }
  o.front_share = (o.front_share || 0) / 100;
  return o;
}
let planTimer = null;
async function runPlan() {
  const r = await api("/api/plan", planInputs());
  const lvl = r.overall;
  $("planOverall").className = "pill " + lvl;
  $("planOverall").textContent = lvl === "red" ? "Unsafe as planned" : lvl === "amber" ? "Needs changes" : "Within planning figures";
  drawGauge(r);
  const cards = [
    ["Front of stage", `${r.front_density} /m²`, r.front_band.level, r.front_band.label],
    ["Whole ground", `${r.avg_density} /m²`, r.avg_band.level, r.avg_band.label],
    ["Time to empty", `${r.egress_min} min`, r.egress_min > r.target_egress_min ? "amber" : "green", `target ${r.target_egress_min} min · need ${r.exit_width_needed_m} m of exits`],
    ["Entry queue at peak", r.queue_peak ? `${r.queue_peak.toLocaleString()} people` : "none", r.queue_peak ? "amber" : "green", `${r.arrival_rate_ppm.toLocaleString()} arriving/min vs ${r.entry_capacity_ppm.toLocaleString()} capacity`],
    ["Comfortable capacity", r.comfortable_capacity.toLocaleString(), "", `upper limit ${r.upper_capacity.toLocaleString()} at 5 /m²`],
    ["CrushGuard kit", `${r.sensors} sensors`, "", `about ₹${r.cost_inr.toLocaleString("en-IN")} incl. gateway`],
  ];
  $("planKpis").innerHTML = cards.map(([l, v, c, sub]) => `<div class="kpi ${c}"><div class="k-val">${esc(v)}</div><div class="k-label">${esc(l)}</div><div class="k-sub">${esc(sub)}</div></div>`).join("");
  $("planRecs").innerHTML = r.recommendations.map((x) => `<li>${esc(x)}</li>`).join("");
  $("planAssume").textContent = `Assumptions: density ${r.assumptions.density_bands}; flow ${r.assumptions.flow}; peak arrivals ${r.assumptions.peaking_factor}× the average. ` +
    "Planning guidance only: confirm with a crowd-safety professional and local rules (e.g. NDMA crowd management guidelines).";
}
function drawGauge(r) {
  const svg = $("densityGauge"), W = svg.clientWidth || 600, H = 86, m = { l: 10, r: 10 }, max = 8;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.innerHTML = "";
  const x = (d) => m.l + (Math.min(max, d) / max) * (W - m.l - m.r);
  const bands = [[0, 2, COLORS.good, "comfortable"], [2, 4, "#7a8f2a", "busy"], [4, 5, COLORS.warning, "very dense"], [5, 6, "#e0702a", "dangerous"], [6, 8, COLORS.critical, "crush risk"]];
  for (const [a, b, c, lab] of bands) {
    el("rect", { x: x(a) + 1, y: 30, width: x(b) - x(a) - 2, height: 14, rx: 3, fill: c, opacity: 0.85 }, svg);
    el("text", { x: (x(a) + x(b)) / 2, y: 60, "text-anchor": "middle" }, svg).textContent = lab;
  }
  for (const d of [0, 2, 4, 5, 6, 8]) el("text", { x: x(d), y: 76, "text-anchor": d === 0 ? "start" : d === 8 ? "end" : "middle" }, svg).textContent = d === 8 ? "8+ /m²" : d;
  for (const [d, lab, dy] of [[r.avg_density, "whole ground", 0], [r.front_density, "front of stage", 0]]) {
    const cx = x(d);
    el("path", { d: `M${cx - 6},12 L${cx + 6},12 L${cx},26 Z`, fill: COLORS.text }, svg);
    el("text", { x: Math.min(W - 60, Math.max(40, cx)), y: 9 + dy, "text-anchor": "middle" }, svg).textContent = `${lab} ${d}`;
  }
}
$("planForm").addEventListener("input", () => { clearTimeout(planTimer); planTimer = setTimeout(runPlan, 250); });
document.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => {
  const p = PRESETS[b.dataset.preset], f = $("planForm");
  for (const [k, v] of Object.entries(p)) { const i = f.elements[k]; if (!i) continue; if (i.type === "checkbox") i.checked = v; else i.value = v; }
  runPlan();
});

// ================================================================ tabs
function switchView(v) {
  view = v;
  for (const name of ["venue", "national", "replay", "plan"]) $("view-" + name).hidden = name !== v;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.view === v));
  $("bActions").hidden = v === "replay" || v === "plan";
  $("banner").hidden = v === "plan";
  if (v === "plan") { $("modeChip").textContent = "PLANNING"; $("modeChip").className = "chip replay"; runPlan(); }
  else if (v === "replay") {
    $("modeChip").textContent = "REPLAY"; $("modeChip").className = "chip replay";
    if (!rp.tl) loadSessions().catch(() => {}); else renderReplay();
  } else if (live) { renderVenue(); renderNational(); }
  try { localStorage.setItem("cg-view", v); } catch (e) { /* storage blocked */ }
}
document.querySelectorAll(".tab").forEach((t) => t.onclick = () => switchView(t.dataset.view));

// ================================================================ websocket
function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "init") {
      venue = msg.venue;
      $("venueName").textContent = venue.name;
      for (const [id, pts] of Object.entries(msg.history)) history[id] = pts;
      venueMap.build(venue);
      buildFailSelect();
      selected ||= venue.segments[2] ? venue.segments[2].id : venue.segments[0].id;
    }
    live = msg;
    for (const s of msg.segments) {
      if (!s.online) continue;
      const h = (history[s.id] ||= []);
      h.push([msg.t, s.f]);
      while (h.length && h[0][0] < msg.t - HISTORY_S) h.shift();
    }
    if (view !== "replay" && view !== "plan") { renderVenue(); if (view === "national") renderNational(); }
    else renderVenue();   // keep alerts/sound live while reviewing
  };
  ws.onopen = () => { $("linkChip").textContent = "server connected"; $("linkChip").classList.remove("bad"); };
  ws.onclose = () => { $("linkChip").textContent = "server disconnected"; $("linkChip").classList.add("bad"); setTimeout(connect, 1500); };
}

loadColors();
setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);
loadConnectInfo();
connect();
try { const v = localStorage.getItem("cg-view"); if (v && v !== "venue") switchView(v); } catch (e) { /* ignore */ }
