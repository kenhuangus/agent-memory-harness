/* ============================================================
   Hand-drawn (Excalidraw-style) diagram engine via rough.js
   Figures for the Cookbook Memory project deck.
   ============================================================ */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const COL = {
    blue: "#1971c2", red: "#e03131", green: "#2f9e44", teal: "#0c8599",
    violet: "#9c36b5", orange: "#f08c00", grape: "#7048e8", yellow: "#f6b73c",
    ink: "#1e1e1e", accent: "#ff6a2b", slate: "#495057"
  };
  const FONT = "'Kalam', cursive";

  /* ---------- tiny scene helpers ---------- */
  function scene(host, w, h) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    host.appendChild(svg);
    return { svg, rc: rough.svg(svg), w, h };
  }
  function add(s, node) { s.svg.appendChild(node); return node; }
  function rect(s, x, y, w, h, o = {}) {
    return add(s, s.rc.rectangle(x, y, w, h, Object.assign(
      { roughness: 1.1, bowing: 1, stroke: COL.ink, strokeWidth: 1.6 }, o)));
  }
  function line(s, x1, y1, x2, y2, o = {}) {
    return add(s, s.rc.line(x1, y1, x2, y2, Object.assign(
      { roughness: 1.1, strokeWidth: 1.5, stroke: COL.ink }, o)));
  }
  function path(s, d, o = {}) {
    return add(s, s.rc.path(d, Object.assign(
      { roughness: 1, strokeWidth: 2.2, fill: "none" }, o)));
  }
  function dot(s, x, y, r, o = {}) {
    return add(s, s.rc.circle(x, y, r * 2, Object.assign(
      { roughness: .8, fill: COL.ink, fillStyle: "solid", stroke: COL.ink }, o)));
  }
  function txt(s, x, y, str, o = {}) {
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", x); t.setAttribute("y", y);
    t.setAttribute("font-family", o.family || FONT);
    t.setAttribute("font-size", o.size || 16);
    t.setAttribute("fill", o.col || COL.ink);
    t.setAttribute("text-anchor", o.anchor || "start");
    t.setAttribute("font-weight", o.weight || 400);
    if (o.spacing) t.setAttribute("letter-spacing", o.spacing);
    t.textContent = str;
    return add(s, t);
  }
  function wrap(s, x, y, str, max, o = {}) {
    const words = str.split(" "); const lines = []; let cur = "";
    for (const w of words) {
      if ((cur + " " + w).trim().length > max) { lines.push(cur.trim()); cur = w; }
      else cur += " " + w;
    }
    if (cur.trim()) lines.push(cur.trim());
    const lh = o.lh || (o.size || 16) * 1.25;
    lines.forEach((ln, i) => txt(s, x, y + i * lh, ln, o));
    return lines.length;
  }
  function chip(s, x, y, w, h, label, color, o = {}) {
    rect(s, x, y, w, h, { fill: o.fill || hex(color, .12), fillStyle: "solid", stroke: color, strokeWidth: 2 });
    const lines = label.split("\n");
    const cy = y + h / 2 - (lines.length - 1) * 9 + 6;
    lines.forEach((ln, i) => txt(s, x + w / 2, cy + i * 20, ln,
      { size: o.size || 16, anchor: "middle", col: o.col || COL.ink, weight: 700 }));
  }
  function hex(c, a) {
    const n = c.replace("#", ""); const r = parseInt(n.slice(0, 2), 16),
      g = parseInt(n.slice(2, 4), 16), b = parseInt(n.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }
  /* arrow = line + small arrowhead */
  function arrow(s, x1, y1, x2, y2, o = {}) {
    line(s, x1, y1, x2, y2, o);
    const a = Math.atan2(y2 - y1, x2 - x1), L = o.head || 11;
    line(s, x2, y2, x2 - L * Math.cos(a - .4), y2 - L * Math.sin(a - .4), o);
    line(s, x2, y2, x2 - L * Math.cos(a + .4), y2 - L * Math.sin(a + .4), o);
  }
  /* titled card: heading + wrapped body lines */
  function card(s, x, y, w, h, color, title, body, dark, o = {}) {
    rect(s, x, y, w, h, { fill: hex(color, dark ? .16 : .1), fillStyle: "solid", stroke: color, strokeWidth: 2.2 });
    txt(s, x + w / 2, y + 30, title, { size: o.tsize || 21, anchor: "middle", weight: 700, col: color });
    const tcol = dark ? "#e7eaf0" : COL.slate;
    wrap(s, x + 16, y + 56, body, o.max || 22, { size: o.bsize || 14.5, col: tcol, lh: 19 });
  }

  /* ============================================================
     Figures
     ============================================================ */

  /* 1 — the cookbook recipe: WHAT / WHEN / WHERE / HOW */
  function figRecipe(host, dark) {
    const s = scene(host, 960, 380);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    const cards = [
      [COL.blue, "WHAT", "Durable, transferable lessons — Invariant · Convention · Fix. Not task-specific facts."],
      [COL.green, "WHEN", "While working (recall), at session end (Daydream extract), at night (Dream consolidate)."],
      [COL.violet, "WHERE", "The right backend: Markdown · SQLite-vector · Graph. Router picks per query."],
      [COL.orange, "HOW", "LLM decides what to keep, embeds it, dedups, resolves contradictions, sets governance."],
    ];
    const w = 218, h = 200, gap = 28, y = 70;
    let x = (960 - (4 * w + 3 * gap)) / 2;
    txt(s, 480, 40, "A recipe for memory", { size: 22, anchor: "middle", weight: 700, col: tcol });
    cards.forEach(([c, t, b]) => { card(s, x, y, w, h, c, t, b, dark); x += w + gap; });
  }

  /* 2 — architecture: write / route / read + dreaming */
  function figArch(host, dark) {
    const s = scene(host, 960, 440);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    chip(s, 40, 180, 150, 80, "Coding\nAgent", COL.accent, { col: tcol });
    // write path
    arrow(s, 190, 205, 300, 175, { stroke: COL.green, strokeWidth: 2.2 });
    txt(s, 245, 165, "write", { size: 13, col: COL.green, anchor: "middle" });
    chip(s, 300, 150, 150, 60, "Persistence", COL.green, { col: tcol, size: 15 });
    // query path
    arrow(s, 190, 235, 300, 265, { stroke: COL.blue, strokeWidth: 2.2 });
    txt(s, 245, 290, "query", { size: 13, col: COL.blue, anchor: "middle" });
    chip(s, 300, 240, 150, 60, "Router", COL.blue, { col: tcol, size: 15 });
    // three backends
    const back = [["Markdown", COL.orange], ["SQLite-vector", COL.teal], ["Graph", COL.grape]];
    let by = 100;
    back.forEach(([t, c]) => {
      chip(s, 600, by, 200, 56, t, c, { col: tcol, size: 15 });
      path(s, `M 450 180 C 530 ${by + 28}, 540 ${by + 28}, 600 ${by + 28}`, { stroke: c, strokeWidth: 1.8 });
      path(s, `M 450 270 C 530 ${by + 28}, 540 ${by + 28}, 600 ${by + 28}`, { stroke: c, strokeWidth: 1.4, strokeLineDash: [5, 5] });
      by += 80;
    });
    txt(s, 700, 90, "memory store", { size: 14, col: COL.muted || COL.slate, anchor: "middle", col: tcol });
    // retrieval back to agent
    arrow(s, 600, 300, 120, 360, { stroke: COL.blue, strokeWidth: 1.8 });
    txt(s, 360, 350, "ranked context  (recency × relevancy)", { size: 13, col: COL.blue, anchor: "middle" });
    // dreaming
    chip(s, 600, 350, 200, 60, "🌙 Dreaming", COL.violet, { col: tcol, size: 15 });
    arrow(s, 700, 350, 700, 320, { stroke: COL.violet, strokeWidth: 1.8 });
    txt(s, 820, 385, "async curation", { size: 12, col: COL.violet, anchor: "middle" });
  }

  /* 3 — dreaming timeline */
  function figDream(host, dark) {
    const s = scene(host, 980, 320);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    const stages = [
      [COL.slate, "Session\nwork", "agent solves tasks"],
      [COL.green, "Stop hook", "session ends"],
      [COL.blue, "Daydream", "mine logs → extract"],
      [COL.violet, "Dream (night)", "dedup · contradiction · governance"],
      [COL.accent, "Clean store", "ranked, must-know tagged"],
    ];
    const w = 168, h = 92, gap = 30, y = 120;
    let x = (980 - (5 * w + 4 * gap)) / 2;
    stages.forEach(([c, t, sub], i) => {
      chip(s, x, y, w, h, t, c, { col: tcol, size: 16 });
      txt(s, x + w / 2, y + h + 24, sub, { size: 12.5, anchor: "middle", col: dark ? "#aab1bf" : COL.slate });
      if (i < stages.length - 1) arrow(s, x + w, y + h / 2, x + w + gap, y + h / 2, { stroke: c, strokeWidth: 2 });
      x += w + gap;
    });
    txt(s, 490, 60, "What, when & how it gets remembered", { size: 20, anchor: "middle", weight: 700, col: tcol });
  }

  /* 4 — the two benchmarks */
  function figBenchmarks(host, dark) {
    const s = scene(host, 960, 380);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    card(s, 60, 60, 380, 260, COL.blue, "SWE-Bench-CL",
      "Continual learning over a sequence of real GitHub fixes. Measures knowledge transfer across tasks and resistance to catastrophic forgetting. Native suite metrics — not an eval we invented.", dark, { max: 40, bsize: 15.5 });
    card(s, 520, 60, 380, 260, COL.violet, "VISTA",
      "Memory-safety under adversarial journeys. Native report: poisoning resistance, targeted attack success rate, and recursive self-improvement safety.", dark, { max: 40, bsize: 15.5 });
    txt(s, 250, 300, "does memory make it solve more?", { size: 13.5, anchor: "middle", col: COL.blue });
    txt(s, 710, 300, "does memory stay safe?", { size: 13.5, anchor: "middle", col: COL.violet });
  }

  /* 5 — the 4-stage gated improvement loop */
  function figLoop(host, dark) {
    const s = scene(host, 980, 380);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    const stages = [
      [COL.slate, "Propose", "a real code diff"],
      [COL.blue, "Tier 1", "retrieval-precision gate"],
      [COL.teal, "Tier 2", "consolidation-quality gate"],
      [COL.green, "Tier 3", "solve-rate (≥30% lift)"],
    ];
    const w = 188, h = 96, gap = 28, y = 150;
    let x = (980 - (4 * w + 3 * gap)) / 2;
    const xs = [];
    stages.forEach(([c, t, sub], i) => {
      xs.push(x);
      chip(s, x, y, w, h, t, c, { col: tcol, size: 18 });
      txt(s, x + w / 2, y + h + 22, sub, { size: 12.5, anchor: "middle", col: dark ? "#aab1bf" : COL.slate });
      if (i < stages.length - 1) arrow(s, x + w, y + h / 2, x + w + gap, y + h / 2, { stroke: c, strokeWidth: 2.2 });
      x += w + gap;
    });
    // land arrow
    arrow(s, x - gap + w - 6, y + h / 2, x + 36, y + h / 2, { stroke: COL.accent, strokeWidth: 2.4 });
    txt(s, x + 70, y + h / 2 + 6, "Land:\nADR + PR", { size: 14, col: COL.accent, weight: 700 });
    // reject loop back to Propose
    const x0 = xs[0] + w / 2, xe = xs[3] + w / 2;
    path(s, `M ${xe} ${y} C ${xe} 60, ${x0} 60, ${x0} ${y}`, { stroke: COL.red, strokeWidth: 1.8, strokeLineDash: [7, 6] });
    arrow(s, x0 + 30, 78, x0, y - 2, { stroke: COL.red, strokeWidth: 1.8 });
    txt(s, 490, 48, "fail a gate → reject, never spend Tier 3", { size: 14, anchor: "middle", col: COL.red });
  }

  /* 6 — memory inspector UI mockup */
  function figInspector(host, dark) {
    const s = scene(host, 900, 420);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    // window
    rect(s, 60, 40, 780, 350, { fill: dark ? "#0e1422" : "#ffffff", fillStyle: "solid", stroke: COL.slate, strokeWidth: 2 });
    rect(s, 60, 40, 780, 44, { fill: hex(COL.slate, .12), fillStyle: "solid", stroke: COL.slate, strokeWidth: 1.4 });
    dot(s, 84, 62, 6, { fill: COL.red, stroke: COL.red });
    dot(s, 104, 62, 6, { fill: COL.yellow, stroke: COL.yellow });
    dot(s, 124, 62, 6, { fill: COL.green, stroke: COL.green });
    txt(s, 430, 68, "Memory Inspector — live store", { size: 15, anchor: "middle", weight: 700, col: tcol });
    // run selector
    rect(s, 84, 104, 260, 40, { fill: hex(COL.accent, .12), fillStyle: "solid", stroke: COL.accent, strokeWidth: 1.8 });
    txt(s, 100, 130, "▼ vista · plugin-real-coding", { size: 14, col: tcol });
    rect(s, 600, 104, 220, 40, { fill: hex(COL.blue, .1), fillStyle: "solid", stroke: COL.blue, strokeWidth: 1.6 });
    txt(s, 710, 130, "2 memories", { size: 14, anchor: "middle", weight: 700, col: COL.blue });
    // memory rows
    const rows = [
      [COL.green, "INVARIANT", "recall key must be hashed for multi-line queries"],
      [COL.violet, "FIX", "discover_runs: cache last-activity to keep /api fast"],
    ];
    let ry = 170;
    rows.forEach(([c, tag, body]) => {
      rect(s, 84, ry, 736, 80, { fill: dark ? "#131b2d" : "#fbfaf7", fillStyle: "solid", stroke: COL.line || "#e6e1d6", strokeWidth: 1.2 });
      rect(s, 96, ry + 14, 116, 26, { fill: hex(c, .18), fillStyle: "solid", stroke: c, strokeWidth: 1.4 });
      txt(s, 154, ry + 32, tag, { size: 12.5, anchor: "middle", weight: 700, col: c });
      wrap(s, 230, ry + 30, body, 54, { size: 14, col: tcol, lh: 19 });
      ry += 94;
    });
    txt(s, 450, 410, "switch runs → blocking load, store snapshot in < 1s", { size: 13, anchor: "middle", col: dark ? "#aab1bf" : COL.slate });
  }

  /* 7 — SWE-Bench-CL results bars */
  function figResults(host, dark) {
    const s = scene(host, 960, 400);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    const axis = dark ? "#aab1bf" : COL.slate;
    function group(x0, title, bars, maxv) {
      txt(s, x0 + 150, 360, title, { size: 16, anchor: "middle", weight: 700, col: tcol });
      const base = 320, hmax = 220, bw = 56, gap = 22;
      line(s, x0 - 6, base, x0 + bars.length * (bw + gap), base, { stroke: axis, strokeWidth: 1.4 });
      let x = x0 + 10;
      bars.forEach(([label, v, c]) => {
        const h = (v / maxv) * hmax;
        rect(s, x, base - h, bw, h, { fill: hex(c, .55), fillStyle: "solid", stroke: c, strokeWidth: 2 });
        txt(s, x + bw / 2, base - h - 10, String(v), { size: 16, anchor: "middle", weight: 700, col: c });
        txt(s, x + bw / 2, base + 22, label, { size: 12.5, anchor: "middle", col: axis });
        x += bw + gap;
      });
    }
    txt(s, 480, 40, "Resolved tasks (Cursor, same commit, out of 50)", { size: 18, anchor: "middle", weight: 700, col: tcol });
    group(70, "SymPy", [["base", 45, COL.slate], ["builtin", 45, COL.blue], ["plugin", 48, COL.green]], 50);
    group(560, "Django", [["base", 10, COL.slate], ["builtin", 11, COL.blue], ["accum", 12, COL.teal], ["dreamed", 13, COL.green]], 50);
  }

  /* 8 — VISTA safety scorecard */
  function figVista(host, dark) {
    const s = scene(host, 960, 360);
    const tcol = dark ? "#e7eaf0" : COL.ink;
    const cards = [
      [COL.green, "1.0", "Poisoning resistance", "rejected every planted memory"],
      [COL.green, "0.0", "Targeted attack success", "no induced harmful action"],
      [COL.green, "1.0", "Self-improvement safety", "RSI stayed within guardrails"],
    ];
    const w = 270, h = 220, gap = 30, y = 70;
    let x = (960 - (3 * w + 2 * gap)) / 2;
    txt(s, 480, 40, "VISTA native safety report — 97-journey split", { size: 18, anchor: "middle", weight: 700, col: tcol });
    cards.forEach(([c, big, t, sub]) => {
      rect(s, x, y, w, h, { fill: hex(c, dark ? .16 : .1), fillStyle: "solid", stroke: c, strokeWidth: 2.4 });
      txt(s, x + w / 2, y + 100, big, { size: 64, anchor: "middle", weight: 700, col: c, family: "'Space Grotesk',sans-serif" });
      txt(s, x + w / 2, y + 145, t, { size: 16, anchor: "middle", weight: 700, col: tcol });
      wrap(s, x + 20, y + 172, sub, 30, { size: 13, anchor: "middle", col: dark ? "#aab1bf" : COL.slate, lh: 17 });
      x += w + gap;
    });
  }

  /* ---------- dispatch ---------- */
  const FIGS = {
    recipe: figRecipe, arch: figArch, dream: figDream, benchmarks: figBenchmarks,
    loop: figLoop, inspector: figInspector, results: figResults, vista: figVista,
  };
  function render() {
    document.querySelectorAll("[data-fig]").forEach(host => {
      if (host.dataset.done) return; host.dataset.done = "1";
      const dark = !!host.closest(".section--dark, .slide--dark");
      try { FIGS[host.dataset.fig](host, dark); } catch (e) { console.error(host.dataset.fig, e); }
    });
  }
  if (document.readyState !== "loading") { render(); }
  else document.addEventListener("DOMContentLoaded", render);
})();
