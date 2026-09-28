// CrushGuard control-room dashboard (no build step, no libraries)
"use strict";

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const HISTORY_S = 120;
const LEVEL_TEXT = { green: "Normal", amber: "Building", red: "Critical" };

let venue = null, state = null, selected = null, ws = null;
const history = {};                 // segId -> [[t, f], ...]
const seenCritical = new Set();
let dots = [];

// ---------------------------------------------------------------- helpers
function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const levelColor = (seg) => !seg.online ? cssVar("--offline")
  : seg.level === "red" ? cssVar("--critical") : seg.level === "amber" ? cssVar("--warning") : cssVar("--good");
const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

// ---------------------------------------------------------------- websocket
function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "init") {
      venue = msg.venue;
      for (const [id, pts] of Object.entries(msg.history)) history[id] = pts;
      buildMap();
      buildFailSelect();
      if (!selected) selected = venue.segments[0].id;
    }
    state = msg;
    for (const s of msg.segments) {
      if (!s.online) continue;
      const h = (history[s.id] ||= []);
      h.push([msg.t, s.f]);
      while (h.length && h[0][0] < msg.t - HISTORY_S) h.shift();
    }
    render();
  };
  ws.onopen = () => { $("linkBadge").textContent = "server connected"; $("linkBadge").classList.remove("bad"); };
  ws.onclose = () => {
    $("linkBadge").textContent = "server disconnected"; $("linkBadge").classList.add("bad");
    setTimeout(connect, 1500);
  };
}

// ---------------------------------------------------------------- map
function buildMap() {
  const svg = $("map");
  svg.innerHTML = "";
  svg.setAttribute("viewBox", `0 0 ${venue.width_m} ${venue.height_m}`);
  $("venueName").textContent = venue.name;

  const st = venue.stage;
  el("rect", { x: st.x, y: st.y, width: st.w, height: st.h, rx: 0.8, fill: cssVar("--stage") }, svg);
  const t = el("text", { x: st.x + st.w / 2, y: st.y + st.h / 2 + 0.5, "text-anchor": "middle" }, svg);
  t.textContent = "STAGE";

  // crowd dots: fixed pseudo-random spots; the renderer squeezes them toward
  // the barricade as pressure rises, so the map "shows" the crowd compressing
  el("g", { id: "dots" }, svg);
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const xs = venue.segments.flatMap((s) => [s.x1, s.x2]);
  const minX = Math.min(...xs) + 0.8, maxX = Math.max(...xs) - 0.8;
  const frontY = Math.min(...venue.segments.map((s) => Math.min(s.y1, s.y2)));
  dots = Array.from({ length: 420 }, () => ({
    x: minX + rnd() * (maxX - minX), y: frontY + 1 + rnd() * (venue.height_m - frontY - 3), ph: rnd() * 6.28 }));
  const g = $("dots");
  for (const d of dots) d.node = el("circle", { r: 0.28, fill: cssVar("--dot") }, g);
  venue._frontY = frontY;

  for (const e of venue.entries || []) {
    el("rect", { x: e.x - 3, y: e.y - 0.6, width: 6, height: 1.2, rx: 0.4, fill: cssVar("--accent"), id: "entry-" + e.id }, svg);
    const lt = el("text", { x: e.x, y: e.y - 1.2, "text-anchor": "middle" }, svg); lt.textContent = e.label;
  }
  for (const gt of venue.gates || []) {
    el("rect", { x: gt.x - 0.9, y: gt.y - 2.5, width: 1.8, height: 5, rx: 0.4, fill: "none",
      "stroke-width": 0.35, stroke: cssVar("--text-2"), id: "gate-" + gt.id }, svg);
    const lt = el("text", { x: gt.x, y: gt.y + 4.2, "text-anchor": "middle" }, svg); lt.textContent = gt.id;
  }
  for (const s of venue.segments) {
    const grp = el("g", { class: "seg", "data-id": s.id }, svg);
    el("line", { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, "stroke-width": 3.2, "stroke-linecap": "butt",
      opacity: 0, id: "halo-" + s.id }, grp);
    el("line", { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, "stroke-width": 0.9, "stroke-linecap": "round",
      id: "line-" + s.id }, grp);
    el("line", { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, class: "seg-hit" }, grp).setAttribute("stroke-width", 2.6);
    const vertical = Math.abs(s.x1 - s.x2) < 0.1;
    const lx = (s.x1 + s.x2) / 2 + (vertical ? (s.x1 < venue.width_m / 2 ? -2.4 : 2.4) : 0);
    const ly = (s.y1 + s.y2) / 2 + (vertical ? 0 : -1.4);
    const lab = el("text", { x: lx, y: ly, "text-anchor": "middle", class: "seg-label", id: "lab-" + s.id }, grp);
    lab.textContent = s.id;
    grp.addEventListener("click", () => { selected = s.id; render(); });
  }
}

function renderMap() {
  if (!venue || !state) return;
  const segs = Object.fromEntries(state.segments.map((s) => [s.id, s]));
  const red = state.thresholds.red_n;
  for (const s of state.segments) {
    const c = levelColor(s);
    $("line-" + s.id).setAttribute("stroke", c);
    const halo = $("halo-" + s.id);
    halo.setAttribute("stroke", c);
    halo.setAttribute("opacity", s.online ? Math.min(0.55, (s.f / red) * 0.45) : 0);
    $("line-" + s.id).setAttribute("stroke-dasharray", s.online ? "" : "0.6 0.6");
    $("lab-" + s.id).textContent = s.id + (s.id === selected ? " ◂" : "");
  }
  // dots: squeeze toward the barricade in proportion to the nearest front segment's force
  const front = venue.segments.filter((s) => Math.abs(s.y1 - s.y2) < 0.1);
  const t = state.t;
  for (const d of dots) {
    const seg = front.find((s) => d.x >= Math.min(s.x1, s.x2) && d.x <= Math.max(s.x1, s.x2)) || front[0];
    const live = segs[seg.id];
    const p = live && live.online ? Math.min(1.2, live.f / red) : 0.2;
    const squeeze = 1 - 0.6 * Math.min(1, p);
    const jig = live ? Math.min(1.2, live.turb / 80) : 0;
    const y = venue._frontY + 0.8 + (d.y - venue._frontY) * squeeze + Math.sin(t * 1.3 + d.ph) * 0.4 * jig;
    const x = d.x + Math.cos(t * 1.1 + d.ph) * 0.3 * jig;
    d.node.setAttribute("cx", x.toFixed(2));
    d.node.setAttribute("cy", y.toFixed(2));
  }
  const sim = state.sim;
  for (const gt of venue.gates || []) {
    const open = sim && sim.gates_open.includes(gt.id);
    $("gate-" + gt.id).setAttribute("stroke", open ? cssVar("--good") : cssVar("--text-2"));
    $("gate-" + gt.id).setAttribute("stroke-dasharray", open ? "0.5 0.5" : "");
  }
  for (const e of venue.entries || []) {
    const closed = sim && !sim.entry_open;
    $("entry-" + e.id).setAttribute("fill", closed ? cssVar("--critical") : cssVar("--accent"));
  }
}

// ---------------------------------------------------------------- banner, alerts
function renderBanner() {
  const b = $("banner");
  const lv = state.overall;
  b.dataset.level = lv;
  const worst = [...state.segments].filter((s) => s.online).sort((a, c) => c.score - a.score)[0];
  $("bannerIcon").textContent = lv === "red" ? "!" : lv === "amber" ? "▲" : "✓";
  if (lv === "red") {
    $("bannerTitle").textContent = state.wave.length
      ? `CRITICAL: crowd wave across ${state.wave.join(", ")}` : `CRITICAL: crush risk at ${worst.label}`;
    $("bannerSub").textContent = "Stop entry, open relief gates, announce on PA now";
  } else if (lv === "amber") {
    $("bannerTitle").textContent = `Pressure building at ${worst.label}`;
    $("bannerSub").textContent = worst.eta_s != null ? `Critical in about ${Math.round(worst.eta_s)} s if this continues`
      : "Watch closely and slow down entry";
  } else {
    $("bannerTitle").textContent = "All barricades normal";
    const on = state.segments.filter((s) => s.online).length;
    $("bannerSub").textContent = `${on} of ${state.segments.length} sensors reporting`;
  }
}

function renderAlerts() {
  const ul = $("alerts");
  const list = state.alerts;
  const active = list.filter((a) => a.active);
  $("alertCount").textContent = `${active.length} active`;
  if (!list.length) { ul.innerHTML = '<li class="empty">No alerts yet</li>'; return; }
  const sorted = [...active, ...list.filter((a) => !a.active)].slice(0, 40);
  // keyed, in-place updates: an alert's <li> and its Ack button stay put while
  // the text (e.g. "critical in ~12 s") changes 5 times a second
  const empty = ul.querySelector(".empty"); if (empty) empty.remove();
  const keep = new Set();
  sorted.forEach((a, i) => {
    keep.add(String(a.id));
    let li = ul.querySelector(`li[data-id="${a.id}"]`);
    if (!li) {
      li = document.createElement("li"); li.dataset.id = a.id;
      li.innerHTML = '<span class="ico"></span><div><div class="a-title"></div><div class="a-act"></div><div class="a-time"></div></div><span class="a-btn"></span>';
    }
    li.className = `${a.severity} ${a.active ? "" : "inactive"}`;
    li.querySelector(".ico").textContent = a.severity === "critical" ? "!" : a.severity === "warning" ? "▲" : "i";
    li.querySelector(".a-title").textContent = a.title;
    li.querySelector(".a-act").textContent = a.action;
    li.querySelector(".a-time").textContent = fmtTime(a.started) +
      (a.peak_severity === "critical" && a.severity !== "critical" ? " · reached CRITICAL" : "") +
      (a.active ? "" : " · cleared " + fmtTime(a.updated)) + (a.acked ? " · acknowledged" : "");
    const slot = li.querySelector(".a-btn"), want = a.active && !a.acked;
    if (want && !slot.firstChild) slot.innerHTML = `<button class="btn small" data-ack="${a.id}">Ack</button>`;
    if (!want && slot.firstChild) slot.innerHTML = "";
    if (ul.children[i] !== li) ul.insertBefore(li, ul.children[i] || null);
  });
  [...ul.children].forEach((li) => { if (!keep.has(li.dataset.id)) li.remove(); });

  for (const a of active) {
    if (a.severity === "critical" && !seenCritical.has(a.id + a.title)) {
      seenCritical.add(a.id + a.title);
      beep();
      if ($("autoPa").checked) playPA(a.segment);
    }
  }
}

$("alerts").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ack]");
  if (b) api(`/api/alerts/${b.dataset.ack}/ack`, {});
});

// ---------------------------------------------------------------- detail + chart
function renderDetail() {
  const s = state.segments.find((x) => x.id === selected);
  if (!s) return;
  $("detailTitle").textContent = `${s.id} · ${s.label}`;
  $("tForce").innerHTML = s.online ? `${Math.round(s.f)}<small>N</small>` : "—";
  $("tRate").innerHTML = s.online ? `${s.rate > 0 ? "+" : ""}${Math.round(s.rate)}<small>N/s</small>` : "—";
  $("tEta").innerHTML = !s.online ? "—" : s.eta_s === 0 ? "NOW" : s.eta_s != null ? `${Math.round(s.eta_s)}<small>s</small>` : "safe";
  $("tTurb").innerHTML = s.online ? `${Math.round(s.turb)}<small>N</small>` : "—";
  $("reasons").textContent = !s.online ? "Sensor offline, no data" :
    s.reasons.length ? "Why: " + s.reasons.join(" · ") : `Status: ${LEVEL_TEXT[s.level]}`;
  $("pushBtn").hidden = state.mode !== "simulation";
  drawChart(history[s.id] || [], state.thresholds, state.t);
}

let chartGeom = null;
function drawChart(pts, th, now) {
  const svg = $("chart");
  const W = svg.clientWidth || 500, H = svg.clientHeight || 190;
  const m = { l: 40, r: 12, t: 10, b: 22 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = "";
  const maxF = Math.max(th.red_n * 1.25, ...pts.map((p) => p[1] * 1.08));
  const x = (t) => m.l + (1 - (now - t) / HISTORY_S) * (W - m.l - m.r);
  const y = (f) => H - m.b - (f / maxF) * (H - m.t - m.b);
  chartGeom = { x, y, m, W, H, pts, now };

  const step = maxF > 800 ? 200 : 100;
  for (let v = 0; v <= maxF; v += step) {
    el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: cssVar("--grid"), "stroke-width": 1 }, svg);
    const tx = el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg); tx.textContent = v;
  }
  for (const s of [120, 90, 60, 30, 0]) {
    const tx = el("text", { x: x(now - s), y: H - 6, "text-anchor": s === 120 ? "start" : s === 0 ? "end" : "middle" }, svg);
    tx.textContent = s === 0 ? "now" : `-${s}s`;
  }
  for (const [val, col, name] of [[th.amber_n, "--warning", "Building"], [th.red_n, "--critical", "Critical"]]) {
    el("line", { x1: m.l, x2: W - m.r, y1: y(val), y2: y(val), stroke: cssVar(col), "stroke-width": 1.5, "stroke-dasharray": "5 4" }, svg);
    const tx = el("text", { x: m.l + 6, y: y(val) - 4, "text-anchor": "start" }, svg);
    tx.textContent = `${name} ${val} N`;
  }
  if (pts.length > 1) {
    const d = pts.filter((p) => p[0] >= now - HISTORY_S)
      .map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
    el("path", { d, fill: "none", stroke: cssVar("--series"), "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    const last = pts[pts.length - 1];
    el("circle", { cx: x(last[0]), cy: y(last[1]), r: 4, fill: cssVar("--series"), stroke: cssVar("--surface"), "stroke-width": 2 }, svg);
  }
  el("line", { id: "xhair", y1: m.t, y2: H - m.b, stroke: cssVar("--muted"), "stroke-width": 1, opacity: 0 }, svg);
}

function chartHover(ev) {
  const tip = $("tip");
  if (!chartGeom || chartGeom.pts.length < 2) { tip.hidden = true; return; }
  const { x, m, W, pts, now } = chartGeom;
  const rect = $("chart").getBoundingClientRect();
  const px = ((ev.clientX - rect.left) / rect.width) * W;
  if (px < m.l || px > W - m.r) { tip.hidden = true; return; }
  const t = now - (1 - (px - m.l) / (W - m.l - m.r)) * HISTORY_S;
  let best = pts[0];
  for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
  const xh = $("xhair");
  if (xh) { xh.setAttribute("x1", x(best[0])); xh.setAttribute("x2", x(best[0])); xh.setAttribute("opacity", 1); }
  tip.hidden = false;
  tip.textContent = `${Math.round(now - best[0])} s ago · ${Math.round(best[1])} N`;
  const left = (x(best[0]) / W) * rect.width;
  tip.style.left = Math.min(rect.width - 130, Math.max(0, left + 10)) + "px";
  tip.style.top = "8px";
}
$("chart").addEventListener("mousemove", chartHover);
$("chart").addEventListener("mouseleave", () => { $("tip").hidden = true; const xh = $("xhair"); if (xh) xh.setAttribute("opacity", 0); });

// ---------------------------------------------------------------- nodes table
function renderNodes() {
  const tb = document.querySelector("#nodes tbody");
  const red = state.thresholds.red_n;
  const rows = state.segments.map((s) => {
    const lv = s.online ? s.level : "off";
    const bat = s.bat ? `${(s.bat / 1000).toFixed(2)} V` : "—";
    const sensors = !s.online ? "—" : [s.loadcell_ok ? "load ✓" : "load ✗", s.imu_ok ? "imu ✓" : "imu ✗"].join(" ");
    return `<tr data-id="${s.id}" class="${s.id === selected ? "sel" : ""}">
      <td>${s.id}</td><td>${esc(s.label)}</td>
      <td><span class="pill ${lv}">${s.online ? LEVEL_TEXT[s.level] : "Offline"}</span></td>
      <td class="num">${s.online ? Math.round(s.f) : "—"}</td>
      <td class="num">${s.online ? Math.round(s.score) : "—"}<span class="bar"><i style="width:${Math.min(100, s.score)}%"></i></span></td>
      <td>${s.online ? `${s.age_s}s ago` : s.age_s != null ? `lost ${Math.round(s.age_s)}s` : "never"}</td>
      <td class="num">${bat}</td><td class="num">${s.online ? s.rssi + " dBm" : "—"}</td><td>${sensors}</td></tr>`;
  });
  // keep <tr> elements alive so clicks are never lost during 5 Hz updates
  if (tb.rows.length !== rows.length) tb.innerHTML = rows.join("");
  rows.forEach((html, i) => {
    const tmp = document.createElement("tbody"); tmp.innerHTML = html;
    const src = tmp.rows[0], dst = tb.rows[i];
    dst.dataset.id = src.dataset.id; dst.className = src.className;
    if (dst.innerHTML !== src.innerHTML) dst.innerHTML = src.innerHTML;
  });
  const on = state.segments.filter((s) => s.online).length;
  $("nodeSummary").textContent = `${on}/${state.segments.length} online · red limit ${red} N`;
}

document.querySelector("#nodes tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) { selected = tr.dataset.id; render(); }
});

// ---------------------------------------------------------------- sim controls
function buildFailSelect() {
  $("failSel").innerHTML = venue.segments.map((s) => `<option value="${s.id}">${s.id} (node ${s.node})</option>`).join("");
}
function renderSim() {
  const sim = state.sim;
  $("simCard").hidden = !sim;
  $("modeBadge").textContent = state.mode === "simulation" ? "SIMULATION" : "LIVE";
  $("modeBadge").classList.toggle("sim", state.mode === "simulation");
  if (!state.gateway_ok) { $("modeBadge").textContent += " · gateway lost"; $("modeBadge").classList.add("bad"); }
  if (!sim) return;
  document.querySelectorAll("[data-scn]").forEach((b) => b.classList.toggle("on", b.dataset.scn === sim.scenario));
  document.querySelectorAll("[data-gate]").forEach((b) => {
    const open = sim.gates_open.includes(b.dataset.gate);
    b.classList.toggle("on", open);
    b.textContent = (open ? "Close " : "Open ") + b.dataset.gate;
  });
  $("entryBtn").classList.toggle("on", !sim.entry_open);
  $("entryBtn").textContent = sim.entry_open ? "Stop entry" : "Resume entry";
  const off = sim.offline.includes($("failSel").value);
  $("failBtn").textContent = off ? "Bring node back" : "Take node offline";
  $("simStatus").textContent = `scenario: ${sim.scenario}`;
}
document.querySelectorAll("[data-scn]").forEach((b) => b.onclick = () => api("/api/sim", { action: "scenario", value: b.dataset.scn }));
document.querySelectorAll("[data-gate]").forEach((b) => b.onclick = () =>
  api("/api/sim", { action: "gate", value: b.dataset.gate, on: !state.sim.gates_open.includes(b.dataset.gate) }));
$("entryBtn").onclick = () => api("/api/sim", { action: "entry", on: !state.sim.entry_open });
$("failBtn").onclick = () => {
  const id = $("failSel").value;
  api("/api/sim", { action: "offline", value: id, on: !state.sim.offline.includes(id) });
};
$("pushBtn").onclick = () => api("/api/sim", { action: "push", value: selected });
$("identifyBtn").onclick = () => {
  const s = venue.segments.find((x) => x.id === selected);
  if (s) api(`/api/nodes/${s.node}/identify`, {});
};

// ---------------------------------------------------------------- PA + sound
let audioCtx = null;
function beep() {
  try {
    audioCtx ||= new AudioContext();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.15, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.6);
    o.start(); o.stop(audioCtx.currentTime + 0.6);
  } catch (e) { /* audio blocked until the user clicks once */ }
}
async function playPA(segment) {
  const lang = $("paLang").value;
  const q = new URLSearchParams({ lang });
  if (segment) q.set("segment", segment);
  const { text } = await api("/api/pa?" + q);
  $("bannerSub").textContent = "PA: " + text;
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = { en: "en-IN", hi: "hi-IN", ta: "ta-IN" }[lang];
  const v = speechSynthesis.getVoices().find((x) => x.lang.toLowerCase().startsWith(lang));
  if (v) u.voice = v;
  u.rate = 0.95;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}
$("paBtn").onclick = () => {
  const worst = state && [...state.segments].sort((a, b) => b.score - a.score)[0];
  playPA(worst && worst.score > 0 ? worst.id : selected);
};

// ---------------------------------------------------------------- main
function render() {
  if (!state || !venue) return;
  renderMap(); renderBanner(); renderAlerts(); renderDetail(); renderNodes(); renderSim();
}
setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);
connect();
