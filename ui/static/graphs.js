/* ui · graphs — fetch the django manifest from /api/graphs/django, render
 * the 4 SVG panels + the sortable run-manifest table.
 *
 * Re-fetched on every "graphs" tab activation. The aggregator scans the
 * filesystem on each call, so freshly merged result dirs show up next refresh.
 */
(function () {
  let manifest = [];
  let sortKey = "ts";
  let sortAsc = false;

  // ---- helpers ----------------------------------------------------------
  // A row is "complete" only when it ran to the full pipeline.n_tasks slate
  // AND yielded a solved count. Partial runs would otherwise show up as
  // X/50 in the charts and misstate the benchmark — gate on partial here.
  const isComplete = r => r && r.solved != null && r.partial !== true;
  // Plain-text DOM helper: avoids innerHTML on data-derived strings.
  // Pass an array of child specs: a string becomes a text node, an object
  // {tag, text, attrs, html, children} becomes an element. `html` is opt-in
  // and reserved for static template strings only.
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) for (const k of Object.keys(attrs)) {
      if (k === "style" && typeof attrs[k] === "object") Object.assign(e.style, attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    if (children != null) {
      const list = Array.isArray(children) ? children : [children];
      for (const c of list) {
        if (c == null || c === false) continue;
        if (typeof c === "string" || typeof c === "number") e.appendChild(document.createTextNode(String(c)));
        else if (c instanceof Node) e.appendChild(c);
      }
    }
    return e;
  }

  // ---- main entry --------------------------------------------------------
  function loadAndRender() {
    fetch("/api/graphs/django", { cache: "no-store" })
      .then(r => r.ok ? r.json() : { manifest: [] })
      .then(d => {
        manifest = Array.isArray(d.manifest) ? d.manifest : [];
        setRefreshTimestamp();
        renderPeaksPanel();
        renderTimelinePanel();
        renderScatterPanel();
        renderDreamPanel();
        renderManifestTable();
        attachSortHandlers();
      })
      .catch(err => {
        console.warn("graphs: fetch failed", err);
        manifest = [];
        renderManifestTable();
      });
  }
  // Re-fetch each time the user activates the graphs tab.
  document.querySelectorAll('.ui-shell-tab[data-view="graphs"]').forEach(btn => {
    btn.addEventListener("click", () => setTimeout(loadAndRender, 50));
  });
  // Initial load if we're booting straight into #graphs.
  if (document.body.classList.contains("ui-mode-graphs")) {
    document.addEventListener("DOMContentLoaded", loadAndRender);
    if (document.readyState !== "loading") setTimeout(loadAndRender, 0);
  } else {
    // First load even if not active; charts then re-fetch on activation anyway.
    document.addEventListener("DOMContentLoaded", loadAndRender);
    if (document.readyState !== "loading") setTimeout(loadAndRender, 0);
  }

  function setRefreshTimestamp() {
    const el = document.getElementById("gx-refresh");
    if (el) {
      const d = new Date();
      el.textContent = d.toTimeString().slice(0, 8);
    }
    const c = document.getElementById("gx-rowcount");
    if (c) c.textContent = String(manifest.length);
  }

  // ---- color tokens via CSS vars ----------------------------------------
  function token(name) {
    const v = getComputedStyle(document.getElementById("view-graphs")).getPropertyValue(name).trim();
    return v || "#fff";
  }
  function modeColor(stage) {
    return ({
      "base": token("--gx-t100"),
      "builtin": token("--gx-plum"),
      "plugin-blank": token("--gx-cyan"),
      "plugin-accum": token("--gx-gold"),
      "plugin-dreamed": token("--gx-amber"),
    }[stage] || token("--gx-t60"));
  }
  function svgEl(tag, attrs, text) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const k of Object.keys(attrs || {})) el.setAttribute(k, attrs[k]);
    if (text !== undefined) el.textContent = text;
    return el;
  }

  // ---- panel 1: peak pass-rate × mode -----------------------------------
  function renderPeaksPanel() {
    const svg = document.getElementById("gx-p1");
    if (!svg) return;
    svg.innerHTML = "";
    const order = ["plugin-dreamed", "plugin-accum", "builtin", "base", "plugin-blank"];
    const peaks = {};
    for (const r of manifest) {
      if (!isComplete(r)) continue;
      if (!peaks[r.stage] || r.solved > peaks[r.stage].solved) peaks[r.stage] = r;
    }
    const W = 640, H = 320, padL = 138, padR = 64, padT = 22, padB = 30;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const rowH = innerH / order.length;
    const max = 50;

    for (let v = 0; v <= max; v += 10) {
      const x = padL + (v / max) * innerW;
      svg.append(svgEl("line", { class: "gridline", x1: x, y1: padT, x2: x, y2: H - padB }));
      svg.append(svgEl("text", { class: "lbl-axis", x: x, y: H - padB + 14, "text-anchor": "middle" }, String(v)));
    }
    svg.append(svgEl("text", { class: "lbl-axis", x: padL + innerW / 2, y: H - 4, "text-anchor": "middle" }, "tasks solved · 50 total"));

    order.forEach((mode, i) => {
      const r = peaks[mode];
      const y = padT + i * rowH + 6;
      const h = rowH - 12;
      svg.append(svgEl("rect", { class: "bar-track", x: padL, y: y, width: innerW, height: h }));
      const fillW = r ? (r.solved / max) * innerW : 0;
      if (r) svg.append(svgEl("rect", { x: padL, y: y, width: fillW, height: h, fill: modeColor(mode), opacity: mode === "plugin-dreamed" ? 1 : 0.78 }));
      svg.append(svgEl("rect", { x: padL + 0.5, y: y + 0.5, width: innerW - 1, height: h - 1, fill: "none", stroke: token("--gx-border") }));
      svg.append(svgEl("text", { class: "lbl-major", x: padL - 12, y: y + h / 2 + 4, "text-anchor": "end" }, mode));
      const label = r ? `${r.solved}/50` : "—";
      const valX = Math.max(padL + fillW + 8, padL + 36);
      svg.append(svgEl("text", { class: "lbl-major", x: valX, y: y + h / 2 + 4 }, label));
      if (r) svg.append(svgEl("text", { x: W - padR + 6, y: y + h / 2 + 4, fill: token("--gx-cyan"), "font-family": "Space Mono, monospace", "font-size": 10 }, r.sha));
    });

    const peak = Math.max(...Object.values(peaks).map(r => r.solved), 0);
    const peakRow = Object.values(peaks).find(r => r.solved === peak);
    const note = document.getElementById("gx-p1-note");
    if (note && peakRow) {
      note.replaceChildren(
        document.createTextNode("Peak across all modes: "),
        el("b", { style: { color: "var(--gx-amber)" } }, `${peakRow.solved}/50`),
        document.createTextNode(" on SHA "),
        el("code", null, peakRow.sha),
        document.createTextNode(` (${peakRow.stage}). Each row shows the highest pass-rate any run of that mode achieved across all SHAs.`),
      );
    }
  }

  // ---- panel 2: SHA timeline --------------------------------------------
  function renderTimelinePanel() {
    const svg = document.getElementById("gx-p2");
    if (!svg) return;
    svg.innerHTML = "";
    const W = 640, H = 320, padL = 44, padR = 96, padT = 22, padB = 36;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    // Build per-SHA max timestamp from complete runs that ALSO have a ts;
    // a row with an unparseable `ts` would crash `.localeCompare()` later.
    const shaToMaxTs = {};
    for (const r of manifest) {
      if (!r.sha || typeof r.ts !== "string" || !r.ts) continue;
      if (!shaToMaxTs[r.sha] || r.ts > shaToMaxTs[r.sha]) shaToMaxTs[r.sha] = r.ts;
    }
    const shas = Object.keys(shaToMaxTs).sort((a, b) => shaToMaxTs[a].localeCompare(shaToMaxTs[b]));
    if (!shas.length) return;
    const xOf = i => padL + (shas.length === 1 ? innerW / 2 : (i / (shas.length - 1)) * innerW);
    // Dynamic yMax derived from the data — fall back to a sensible floor
    // so an empty/early dataset still renders gridlines. The +2 headroom
    // keeps labels off the top edge.
    const completeRows = manifest.filter(isComplete);
    const dataMax = completeRows.length ? Math.max(...completeRows.map(r => r.solved)) : 0;
    const yMax = Math.max(15, dataMax + 2);
    const yOf = v => padT + innerH - (v / yMax) * innerH;
    // Step picks the closest "round" gridline spacing so we never end up
    // with too many or too few horizontal rules as yMax grows.
    const yStep = yMax <= 20 ? 3 : yMax <= 40 ? 5 : 10;
    for (let v = 0; v <= yMax; v += yStep) {
      const y = yOf(v);
      svg.append(svgEl("line", { class: "gridline", x1: padL, y1: y, x2: padL + innerW, y2: y }));
      svg.append(svgEl("text", { class: "lbl-axis", x: padL - 8, y: y + 4, "text-anchor": "end" }, String(v)));
    }
    shas.forEach((sha, i) => {
      const x = xOf(i);
      svg.append(svgEl("line", { class: "gridline", x1: x, y1: padT, x2: x, y2: padT + innerH }));
      svg.append(svgEl("text", { x: x, y: padT + innerH + 14, "text-anchor": "middle", fill: token("--gx-cyan"), "font-family": "Space Mono, monospace", "font-size": 10 }, sha));
      svg.append(svgEl("text", { class: "lbl-axis", x: x, y: padT + innerH + 28, "text-anchor": "middle" }, shaToMaxTs[sha].slice(4, 8)));
    });
    svg.append(svgEl("text", { class: "lbl-axis", x: 6, y: padT - 8 }, "solved / 50"));

    const modes = ["plugin-dreamed", "plugin-accum", "builtin", "base", "plugin-blank"];
    modes.forEach(mode => {
      const points = [];
      shas.forEach((sha, i) => {
        const matches = manifest.filter(r => r.stage === mode && r.sha === sha && isComplete(r));
        if (!matches.length) return;
        const mean = matches.reduce((s, r) => s + r.solved, 0) / matches.length;
        points.push({ x: xOf(i), y: yOf(mean), solved: mean });
      });
      if (!points.length) return;
      const c = modeColor(mode);
      if (points.length > 1) {
        const path = points.map((p, k) => (k ? "L" : "M") + p.x + "," + p.y).join(" ");
        svg.append(svgEl("path", { d: path, stroke: c, "stroke-width": 1.5, fill: "none" }));
      }
      points.forEach(p => {
        svg.append(svgEl("circle", { cx: p.x, cy: p.y, r: 4, fill: c, stroke: token("--gx-bg"), "stroke-width": 1.5 }));
        svg.append(svgEl("text", { x: p.x + 8, y: p.y - 6, fill: c, "font-size": 10 }, p.solved.toFixed(p.solved % 1 ? 1 : 0)));
      });
      const last = points[points.length - 1];
      svg.append(svgEl("text", { x: padL + innerW + 8, y: last.y + 4, fill: c, "font-size": 10, "font-weight": 500 }, mode));
    });
    const note = document.getElementById("gx-p2-note");
    if (note) note.textContent = "Pass-rate per mode across SHAs, in chronological order. Where multiple runs exist at the same (sha, stage), the line shows their mean.";
  }

  // ---- panel 3: memory × pass-rate scatter ------------------------------
  function renderScatterPanel() {
    const svg = document.getElementById("gx-p3");
    if (!svg) return;
    svg.innerHTML = "";
    const W = 640, H = 320, padL = 44, padR = 24, padT = 22, padB = 40;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const complete = manifest.filter(isComplete);
    const xMax = Math.max(250, ...complete.map(r => r.mem || 0));
    const yMax = Math.max(15, ...complete.map(r => r.solved || 0), 0) + 2;
    const xOf = v => padL + (v / xMax) * innerW;
    const yOf = v => padT + innerH - (v / yMax) * innerH;
    for (let v = 0; v <= xMax; v += Math.ceil(xMax / 5 / 10) * 10) {
      const x = xOf(v);
      svg.append(svgEl("line", { class: "gridline", x1: x, y1: padT, x2: x, y2: padT + innerH }));
      svg.append(svgEl("text", { class: "lbl-axis", x: x, y: padT + innerH + 14, "text-anchor": "middle" }, String(v)));
    }
    for (let v = 0; v <= yMax; v += 3) {
      const y = yOf(v);
      svg.append(svgEl("line", { class: "gridline", x1: padL, y1: y, x2: padL + innerW, y2: y }));
      svg.append(svgEl("text", { class: "lbl-axis", x: padL - 8, y: y + 4, "text-anchor": "end" }, String(v)));
    }
    svg.append(svgEl("text", { class: "lbl-axis", x: padL + innerW / 2, y: padT + innerH + 28, "text-anchor": "middle" }, "memory items in store"));
    svg.append(svgEl("text", { class: "lbl-axis", x: 6, y: padT - 8 }, "solved / 50"));

    const peakSolved = Math.max(...complete.map(r => r.solved), 0);
    complete.forEach(r => {
      const c = modeColor(r.stage);
      const isPeak = r.solved === peakSolved;
      svg.append(svgEl("circle", {
        cx: xOf(r.mem || 0), cy: yOf(r.solved),
        r: isPeak ? 6 : 4,
        fill: isPeak ? c : "none",
        stroke: c,
        "stroke-width": 1.5,
      }));
      if (isPeak) {
        svg.append(svgEl("text", {
          x: xOf(r.mem || 0) + 9, y: yOf(r.solved) + 4,
          fill: c, "font-size": 11, "font-weight": 600,
        }, `peak ${r.solved}/50 @ ${r.mem || 0}mem`));
      }
    });
    const legX = padL + innerW - 130;
    let legY = padT + 10;
    ["plugin-dreamed", "plugin-accum", "plugin-blank", "builtin", "base"].forEach(mode => {
      svg.append(svgEl("circle", { cx: legX, cy: legY - 3, r: 3.5, fill: "none", stroke: modeColor(mode), "stroke-width": 1.5 }));
      svg.append(svgEl("text", { x: legX + 10, y: legY + 1, fill: token("--gx-t60"), "font-size": 10 }, mode));
      legY += 13;
    });
    const note = document.getElementById("gx-p3-note");
    if (note) note.textContent = "Memory abundance does not predict pass-rate. The peak run is highlighted; the largest stores often sit near the bottom.";
  }

  // ---- panel 4: dream-cycle metrics -------------------------------------
  function renderDreamPanel() {
    const tbody = document.querySelector("#gx-p4 tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    // Only complete runs where the dream cycle ran — partials would otherwise
    // surface here as X/50 and misstate the dream pass's relationship to the
    // final benchmark grade.
    const dreamed = manifest.filter(r => r.dream === "ran" && r.dreamCounts && isComplete(r)).slice();
    dreamed.sort((a, b) => (b.solved ?? -1) - (a.solved ?? -1));
    const peakSolved = Math.max(...dreamed.map(r => r.solved ?? 0), 0);
    for (const r of dreamed) {
      const isPeak = r.solved === peakSolved;
      const dc = r.dreamCounts;
      const tagCls = isPeak ? "dreamed-best" : "dreamed";
      const tr = el("tr", null, [
        el("td", { class: "gx-sha" }, r.sha),
        el("td", null, el("span", { class: `gx-tag ${tagCls}` }, r.seed || "—")),
        el("td", { class: "gx-num-c gx-k" }, String(r.mem ?? 0)),
        el("td", { class: "gx-num-c gx-dim" }, String(dc.retired)),
        el("td", { class: "gx-num-c gx-dim" }, String(dc.pruned)),
        el("td", { class: "gx-num-c gx-dim" }, String(dc.contradicted)),
        el("td", { class: "gx-num-c gx-k" }, String(dc.calls)),
        el("td", { class: "gx-num-c", style: { color: "var(--gx-gold)" } }, String(dc.must_known)),
        el("td", {
          class: "gx-num-c",
          style: { color: isPeak ? "var(--gx-amber)" : "var(--gx-t100)", fontWeight: isPeak ? "600" : "500" },
        }, `${r.solved ?? "—"}/50`),
      ]);
      tbody.appendChild(tr);
    }
    const note = document.getElementById("gx-p4-note");
    if (note) {
      note.replaceChildren(
        document.createTextNode("Only runs where the dream cycle was invoked. "),
        el("code", null, "retired"), document.createTextNode("/"),
        el("code", null, "pruned"), document.createTextNode("/"),
        el("code", null, "contr"), document.createTextNode(" count items the worker removed; "),
        el("code", null, "calls"), document.createTextNode(" is the contradiction LLM budget consumed; "),
        el("code", null, "must_know"), document.createTextNode(" is governance flagging recall-worthy items."),
      );
    }
  }

  // ---- panel 5: sortable manifest table ---------------------------------
  function sortKeyFn(key) {
    return r => {
      switch (key) {
        case "ts":          return r.ts || "";
        case "sha":         return r.sha || "";
        case "benchmark":   return r.benchmark || "";
        case "stage":       return ({base:1,builtin:2,"plugin-blank":3,"plugin-accum":4,"plugin-dreamed":5}[r.stage] || 99);
        case "harness":     return r.harness || "";
        case "agent":       return r.agent || "";
        case "ddVariant":   return r.ddVariant || "";
        case "ddModel":     return r.ddModel || "";
        case "attempted":   return r.attempted ?? -1;
        case "budget":      return r.budget ?? 0;
        case "cost":        return r.cost ?? -1;
        case "tokens":      return (r.tokIn ?? 0) + (r.tokOut ?? 0);
        case "dur":         return r.dur ?? -1;
        case "mem":         return r.mem ?? 0;
        case "dream":       return r.dream === "ran" ? 1 : 0;
        case "solved":      return r.solved ?? -1;
        default:            return 0;
      }
    };
  }

  function attachSortHandlers() {
    document.querySelectorAll("#gx-p5 th[data-sort-key]").forEach(th => {
      const btn = th.querySelector(".gx-sort-btn") || th;
      btn.onclick = () => {
        const k = th.dataset.sortKey;
        if (k === sortKey) sortAsc = !sortAsc;
        else { sortKey = k; sortAsc = (k === "stage" || k === "sha" || k === "ddVariant"); }
        renderManifestTable();
      };
    });
  }

  function renderManifestTable() {
    const tbody = document.querySelector("#gx-p5 tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    // aria-sort is the source of truth for the active column visual; the
    // separate gx-sort-active class stays as a styling fallback for tests
    // that still query by class.
    document.querySelectorAll("#gx-p5 th[data-sort-key]").forEach(th => {
      const active = th.dataset.sortKey === sortKey;
      th.setAttribute("aria-sort", active ? (sortAsc ? "ascending" : "descending") : "none");
      th.classList.toggle("gx-sort-active", active);
      th.classList.toggle("gx-sort-asc", active && sortAsc);
    });
    const rows = manifest.slice();
    const fn = sortKeyFn(sortKey);
    rows.sort((a, b) => {
      const va = fn(a), vb = fn(b);
      if (typeof va === "number" && typeof vb === "number") return sortAsc ? va - vb : vb - va;
      const sa = String(va), sb = String(vb);
      return sortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa);
    });
    // Peak is over COMPLETE runs only — a partial run could have a higher
    // attempted/solved ratio temporarily but isn't comparable to a full
    // 50-task grade and shouldn't claim the peak amber highlight.
    const peakSolved = Math.max(...rows.filter(isComplete).map(r => r.solved), 0);
    for (const r of rows) tbody.appendChild(renderManifestRow(r, peakSolved));
    const c = document.getElementById("gx-rowcount");
    if (c) c.textContent = String(rows.length);
    const note = document.getElementById("gx-p5-note");
    if (note) {
      note.replaceChildren(
        document.createTextNode("Sorted by "),
        el("b", null, sortKey),
        document.createTextNode(` ${sortAsc ? "ascending" : "descending"}. Click any header to re-sort. `),
        el("b", null, "dd-prompt"),
        document.createTextNode(" is inferred from SHA history — the artifact does not yet record the live "),
        el("code", null, "DREAM_EXTRACTION_VARIANT"),
        document.createTextNode("."),
      );
    }
  }

  function renderManifestRow(r, peakSolved) {
    const tagCls = { base:"base", builtin:"builtin", "plugin-blank":"blank", "plugin-accum":"accum", "plugin-dreamed":"dreamed" }[r.stage] || "base";
    const variantColors = { V5:"var(--gx-amber)", V4:"var(--gx-gold)", V3:"var(--gx-jade)", V2:"var(--gx-cyan)", V1:"var(--gx-t60)", V0:"var(--gx-t40)" };
    const vCol = variantColors[r.ddVariant] || "var(--gx-t100)";
    const isPeak = isComplete(r) && r.solved === peakSolved && peakSolved > 0;
    const ts = typeof r.ts === "string" ? r.ts : "";
    const tsShort = ts && ts.length >= 13
      ? `${ts.slice(4,6)}-${ts.slice(6,8)} ${ts.slice(9,11)}:${ts.slice(11,13)}`
      : "—";
    const k = n => n == null ? "—" : (n >= 1000000 ? (n/1000000).toFixed(2)+"M" : n >= 1000 ? (n/1000).toFixed(0)+"k" : String(n));
    const tokText = r.tokIn == null ? "—" : `${k(r.tokIn)}/${k(r.tokOut)}`;

    // Stage cell: tag + optional seed sub-tag
    const stageCell = el("td", null, [
      el("span", { class: `gx-tag ${tagCls}` }, r.stage || ""),
      r.seed ? el("span", {
        class: "gx-tag",
        style: { marginLeft: "4px", fontSize: "9px", borderColor: "var(--gx-t20)", color: "var(--gx-t60)" },
      }, r.seed) : null,
    ]);
    // Dream cell: ran/not-run colored span
    const dreamCell = el("td", null,
      r.dream === "ran"
        ? el("span", { style: { color: "var(--gx-amber)" } }, "ran")
        : el("span", { style: { color: "var(--gx-t40)" } }, "not run"));
    // Solved cell: partial vs complete
    const solvedCell = el("td", { class: "gx-num-c" }, r.solved == null
      ? [
          el("span", { style: { color: "var(--gx-coral)" } }, "partial"),
          document.createTextNode(" "),
          el("span", { style: { color: "var(--gx-t40)" } }, `${r.attempted}/50`),
        ]
      : el("span", {
          style: {
            color: isPeak ? "var(--gx-amber)" : "var(--gx-t100)",
            fontWeight: isPeak ? "600" : "500",
          },
        }, `${r.solved}/50`));
    // Duration cell: number + tiny "m"
    const durCell = el("td", { class: "gx-num-c gx-dim" }, [
      r.dur != null ? String(r.dur) : "—",
      r.dur != null ? el("span", { style: { fontSize: "9px", color: "var(--gx-t40)" } }, "m") : null,
    ]);

    return el("tr", null, [
      el("td", { class: "gx-dim" }, tsShort),
      el("td", { class: "gx-sha" }, r.sha || "—"),
      el("td", { class: "gx-k" }, r.benchmark || "—"),
      stageCell,
      el("td", { class: "gx-dim" }, r.harness || "—"),
      el("td", { class: "gx-k" }, r.agent || "—"),
      el("td", { style: { color: vCol, fontWeight: "500" } }, r.ddVariant || "—"),
      el("td", { class: "gx-dim" }, r.ddModel || "—"),
      el("td", { class: "gx-num-c gx-dim" }, r.attempted != null ? String(r.attempted) : "—"),
      el("td", { class: "gx-num-c gx-dim" }, r.budget ? "$" + r.budget : "—"),
      el("td", { class: "gx-num-c gx-k" }, r.cost == null ? "—" : "$" + Number(r.cost).toFixed(2)),
      el("td", { class: "gx-num-c gx-dim" }, tokText),
      durCell,
      el("td", { class: "gx-num-c gx-k" }, String(r.mem ?? 0)),
      dreamCell,
      solvedCell,
    ]);
  }
})();
