/* results · monitor — frontend
 * Polls /api/runs once on load to populate the dropdown, then polls
 * /api/run/<id> every 3s for the active selection. Chart.js drives the
 * four canvases; everything else is direct DOM updates. No frameworks. */

(() => {
  "use strict";

  const REFRESH_MS = 3000;

  // colors must stay in sync with styles.css :root tokens.
  const C = {
    bg:      "#0a0d10",
    bg1:     "#11161b",
    text:    "#e6e1d5",
    text2:   "#968f7f",
    text3:   "#5e5a4d",
    text4:   "#36342b",
    line:    "#1f2730",
    line2:   "#2a323d",
    amber:   "#d4a017",
    amberD:  "rgba(212, 160, 23, 0.18)",
    amberD2: "rgba(212, 160, 23, 0.42)",
    sage:    "#7eb069",
    sageD:   "rgba(126, 176, 105, 0.16)",
    terra:   "#d97757",
    terraD:  "rgba(217, 119, 87, 0.16)",
    crimson: "#c14953",
    crimsonD:"rgba(193, 73, 83, 0.16)",
    steel:   "#5b8db8",
    steelD:  "rgba(91, 141, 184, 0.16)",
    violet:  "#8a76b3",
    violetD: "rgba(138, 118, 179, 0.16)",
  };

  // Group event-type names to a stable color so chart colors don't shuffle
  // every poll (Object key insertion order would cause that).
  const EVENT_COLORS = [
    ["daydream.memory_written",     C.amber],
    ["daydream.candidate_rejected", C.text3],
    ["daydream.chunk_extracted",    C.sage],
    ["daydream.llm_call",           C.steel],
    ["llm_call_succeeded",          C.steelD],
    ["daydream.cli_resolved",       C.violet],
    ["daydream.prompt_resolved",    C.violetD],
    ["daydream.noise_filtered",     C.sageD],
    ["daydream.chunk_error",        C.crimson],
    ["chunk_skipped_parse_failed",  C.terra],
    ["chunk_skipped_unavailable_llm", C.terraD],
    ["chunk_partial_parse",         C.terra],
    ["daydream.rejected_field_missing", C.text2],
    ["sweep_skipped",               C.line2],
    ["sweep_completed",             C.text3],
  ];

  // --- state --------------------------------------------------------------

  const state = {
    runs: [],
    activeRunId: null,
    refreshOn: true,
    countdownMs: REFRESH_MS,
    lastSnapshot: null,
    charts: {},
  };

  // --- elements -----------------------------------------------------------

  const $ = (id) => document.getElementById(id);
  const runSelect = $("run-select");
  const statusLed = $("status-led");
  const statusLabel = $("status-label");
  const statusAge = $("status-age");
  const refreshToggle = $("refresh-toggle");
  const refreshCountdown = $("refresh-countdown");

  // --- chart.js defaults --------------------------------------------------

  Chart.defaults.font.family = "'IBM Plex Mono', ui-monospace, monospace";
  Chart.defaults.font.size = 10;
  Chart.defaults.color = C.text2;
  Chart.defaults.borderColor = C.line;
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = "#0a0d10";
  Chart.defaults.plugins.tooltip.borderColor = C.line2;
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.bodyColor = C.text;
  Chart.defaults.plugins.tooltip.titleColor = C.amber;
  Chart.defaults.plugins.tooltip.padding = 8;
  Chart.defaults.plugins.tooltip.cornerRadius = 0;
  Chart.defaults.plugins.tooltip.boxPadding = 4;
  Chart.defaults.plugins.tooltip.titleFont = { family: "'IBM Plex Sans Condensed', sans-serif", size: 10, weight: "600" };
  Chart.defaults.plugins.tooltip.bodyFont = { family: "'IBM Plex Mono', monospace", size: 11 };
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 4;
  Chart.defaults.elements.line.borderWidth = 1.5;
  Chart.defaults.animation = { duration: 350, easing: "easeOutCubic" };

  // --- formatting helpers -------------------------------------------------

  const fmtInt = (n) => (n == null ? "—" : new Intl.NumberFormat("en-US").format(Math.round(n)));
  const fmtPct = (r) => (r == null ? "—" : (r * 100).toFixed(r < 0.01 ? 2 : 1) + "%");
  const fmtUsd = (v) => (v == null ? "—" : "$" + v.toFixed(v < 10 ? 4 : 2));
  const fmtAge = (s) => {
    if (s == null) return "—";
    if (s < 60) return Math.floor(s) + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m ago";
  };
  const fmtHms = (ts) => {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-GB", { hour12: false });
  };

  // --- KPI rendering ------------------------------------------------------

  function renderKpis(snap) {
    const m = snap.metrics || {};
    const mem = m.memory || {};
    const sess = m.sessions || {};
    const cost = m.cost || {};
    const fail = m.failures || {};

    // memory ---------------------------------------------------------------
    setText("kpi-memory-primary", fmtInt(mem.memories_written));
    const subParts = [fmtInt(mem.memories_written) + " kept", fmtInt(mem.candidates_total) + " candidates"];
    if (mem.emit_drift) {
      subParts.push("store=" + fmtInt(mem.memories_from_store) + " vs diary=" + fmtInt(mem.memories_from_diary));
    }
    setText("kpi-memory-sub", subParts.join(" · "));
    setText("kpi-memory-rate", fmtPct(mem.keep_rate));
    const tagBits = [];
    tagBits.push(mem.noise_filter_engaged ? "filter on" : "filter off");
    if (mem.emit_drift) tagBits.push("emit drift");
    setText("kpi-memory-tag", tagBits.join(" · "));
    setBar("kpi-memory-rate-fill", (mem.keep_rate || 0));

    // sessions -------------------------------------------------------------
    setText("kpi-sessions-primary", fmtInt(sess.diaries));
    setText("kpi-sessions-total", sess.tasks_n != null ? fmtInt(sess.tasks_n) : "—");
    const resolved = sess.tasks_resolved != null ? fmtInt(sess.tasks_resolved) : "—";
    const graded   = sess.tasks_graded != null   ? fmtInt(sess.tasks_graded)   : "—";
    setText("kpi-sessions-sub", `${resolved} resolved · ${graded} graded · ${fmtInt(sess.sidecars)} sidecars`);
    const completePct = (sess.tasks_n && sess.diaries) ? (sess.diaries / sess.tasks_n) : 0;
    setText("kpi-sessions-rate", fmtPct(completePct));
    setBar("kpi-sessions-rate-fill", completePct);
    setText("kpi-sessions-tag", sess.tasks_n ? `of ${sess.tasks_n} tasks` : "no run json yet");

    // cost -----------------------------------------------------------------
    setText("kpi-cost-primary", fmtUsd(cost.cost_usd));
    setText("kpi-cost-sub", "in: " + fmtInt(cost.tokens_in) + " · out: " + fmtInt(cost.tokens_out));
    if (cost.budget_usd && cost.cost_usd != null) {
      const pct = Math.min(1, cost.cost_usd / cost.budget_usd);
      setText("kpi-cost-rate", fmtPct(pct));
      setBar("kpi-cost-rate-fill", pct);
      setText("kpi-cost-tag", "of $" + cost.budget_usd.toFixed(0));
    } else {
      setText("kpi-cost-rate", "—");
      setBar("kpi-cost-rate-fill", 0);
      setText("kpi-cost-tag", "no budget");
    }

    // failures -------------------------------------------------------------
    const v = fail.voyage_429 || 0;
    const c = fail.chunk_errors || 0;
    const h = fail.hook_subprocess_failed || 0;
    const t = fail.claude_timeouts || 0;
    setFailNum("fail-voyage", v);
    setFailNum("fail-chunk", c);
    setFailNum("fail-hook", h);
    setFailNum("fail-timeout", t);
    const anyFail = (v + c + h + t) > 0;
    document.querySelector('.kpi[data-kind="failures"]').classList.toggle("fail-active", anyFail);
    const preWarn = (fail.preflight_warnings || []).length;
    const runWarn = (fail.run_warnings || []).length;
    if (preWarn || runWarn) {
      setText("kpi-failures-sub",
        (preWarn ? `${preWarn} preflight warn` : "") +
        (preWarn && runWarn ? " · " : "") +
        (runWarn ? `${runWarn} run warn` : ""));
      setText("kpi-failures-tag", "investigate");
    } else {
      setText("kpi-failures-sub", "no preflight warnings");
      setText("kpi-failures-tag", anyFail ? "active" : "clear");
    }
  }

  function setText(id, value) {
    const el = $(id);
    if (!el) return;
    if (el.textContent !== String(value)) {
      el.textContent = value;
      el.classList.remove("flash");
      // force reflow then reapply for the cell-flash animation
      void el.offsetWidth;
      el.classList.add("flash");
    }
  }
  function setBar(id, ratio) {
    const fill = $(id);
    if (!fill) return;
    const pct = Math.max(0, Math.min(1, ratio || 0));
    fill.style.right = (100 - pct * 100) + "%";
  }
  function setFailNum(id, n) {
    const el = $(id);
    if (!el) return;
    el.textContent = fmtInt(n);
    el.classList.toggle("active", n > 0);
  }

  // --- charts -------------------------------------------------------------

  function ensureChart(id, ctorFn) {
    if (state.charts[id]) return state.charts[id];
    const canvas = document.getElementById(id);
    state.charts[id] = ctorFn(canvas.getContext("2d"));
    return state.charts[id];
  }

  function renderCharts(snap) {
    const ch = snap.charts || {};

    // 1. cumulative memories over time -----------------------------------
    const cum = ch.cumulative_memories || [];
    const cumChart = ensureChart("chart-cumulative", (ctx) =>
      new Chart(ctx, {
        type: "line",
        data: { labels: [], datasets: [{
          data: [],
          borderColor: C.amber,
          backgroundColor: (context) => {
            const { ctx, chartArea } = context.chart;
            if (!chartArea) return C.amberD;
            const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
            g.addColorStop(0, C.amberD2);
            g.addColorStop(1, "rgba(212, 160, 23, 0)");
            return g;
          },
          fill: true,
          tension: 0.18,
          stepped: false,
        }]},
        options: {
          maintainAspectRatio: false,
          responsive: true,
          interaction: { intersect: false, mode: "index" },
          scales: {
            x: {
              type: "linear",
              ticks: {
                color: C.text3,
                font: { size: 9 },
                maxTicksLimit: 5,
                callback: (v) => fmtHms(v),
              },
              grid: { color: C.line, drawTicks: false },
              border: { color: C.line2 },
            },
            y: {
              beginAtZero: true,
              ticks: { color: C.text3, font: { size: 9 }, precision: 0, maxTicksLimit: 5 },
              grid: { color: C.line, drawTicks: false },
              border: { color: C.line2 },
            },
          },
          plugins: { tooltip: { callbacks: {
            title: (items) => fmtHms(items[0].parsed.x),
            label: (item) => " " + item.parsed.y + " memories",
          }}},
        },
      })
    );
    cumChart.data.labels = cum.map(p => p.ts);
    cumChart.data.datasets[0].data = cum.map(p => ({ x: p.ts, y: p.count }));
    cumChart.update("none");
    setText("chart-cum-tag", cum.length ? `${cum.length} writes` : "no writes yet");

    // 2. per-session yield (stacked bar: kept | rejected) -----------------
    const ys = ch.per_session_yield || [];
    const ysTop = ys.slice(0, 30);
    const yieldChart = ensureChart("chart-yield", (ctx) =>
      new Chart(ctx, {
        type: "bar",
        data: { labels: [], datasets: [
          { label: "kept",     data: [], backgroundColor: C.amber, borderColor: C.amber, borderWidth: 0, stack: "a" },
          { label: "rejected", data: [], backgroundColor: C.line2, borderColor: C.line2, borderWidth: 0, stack: "a" },
        ]},
        options: {
          maintainAspectRatio: false,
          responsive: true,
          indexAxis: "y",
          scales: {
            x: {
              stacked: true, beginAtZero: true,
              ticks: { color: C.text3, font: { size: 9 }, maxTicksLimit: 5 },
              grid: { color: C.line, drawTicks: false },
              border: { color: C.line2 },
            },
            y: {
              stacked: true,
              ticks: { color: C.text3, font: { size: 9, family: "'IBM Plex Mono', monospace" } },
              grid: { display: false },
              border: { color: C.line2 },
            },
          },
          plugins: { tooltip: { callbacks: {
            title: (items) => items[0].label,
            label: (item) => " " + item.dataset.label + ": " + item.parsed.x,
          }}},
        },
      })
    );
    yieldChart.data.labels = ysTop.map(r => r.session_short);
    yieldChart.data.datasets[0].data = ysTop.map(r => r.kept);
    yieldChart.data.datasets[1].data = ysTop.map(r => r.rejected);
    yieldChart.update("none");
    setText("chart-yield-tag",
      ys.length ? `${ys.length} session${ys.length === 1 ? "" : "s"}` + (ys.length > 30 ? " · top 30" : "") : "no sessions");

    // 3. hook-fired vs successful emit (horizontal funnel) ----------------
    const hv = ch.hook_vs_emit || {};
    // Drop hook_fired when the harness didn't record it (some run configs
    // emit only `note` ops in events.jsonl). Falling back to cli_resolved
    // as the entry point preserves an honest conversion story.
    const hookKnown = (hv.hook_fired || 0) > 0;
    const funnelKeys   = hookKnown
      ? ["hook_fired", "cli_resolved", "llm_call", "llm_call_succeeded", "chunk_extracted", "memory_written"]
      : ["cli_resolved", "llm_call", "llm_call_succeeded", "chunk_extracted", "memory_written"];
    const funnelLabels = hookKnown
      ? ["hook fired", "cli resolved", "llm call", "llm ok", "chunk extracted", "memory written"]
      : ["cli invoked", "llm call", "llm ok", "chunk extracted", "memory written"];
    const funnelColors = hookKnown
      ? [C.text3, C.violet, C.steel, C.steelD, C.sage, C.amber]
      : [C.violet, C.steel, C.steelD, C.sage, C.amber];
    const funnelData = funnelKeys.map(k => hv[k] || 0);
    const funnelChart = ensureChart("chart-funnel", (ctx) =>
      new Chart(ctx, {
        type: "bar",
        data: { labels: funnelLabels, datasets: [{
          data: funnelData,
          backgroundColor: funnelColors,
          borderColor: funnelColors,
          borderWidth: 0,
          barThickness: 16,
        }]},
        options: {
          maintainAspectRatio: false,
          responsive: true,
          indexAxis: "y",
          scales: {
            x: {
              beginAtZero: true,
              ticks: { color: C.text3, font: { size: 9 }, maxTicksLimit: 5, precision: 0 },
              grid: { color: C.line, drawTicks: false },
              border: { color: C.line2 },
            },
            y: {
              ticks: {
                color: C.text2,
                font: { size: 10, family: "'IBM Plex Sans Condensed', sans-serif" },
              },
              grid: { display: false },
              border: { color: C.line2 },
            },
          },
          plugins: { tooltip: { callbacks: {
            title: (items) => items[0].label,
            label: (item) => " " + item.parsed.x,
          }}},
        },
      })
    );
    funnelChart.data.labels = funnelLabels;
    funnelChart.data.datasets[0].data = funnelData;
    funnelChart.data.datasets[0].backgroundColor = funnelColors;
    funnelChart.data.datasets[0].borderColor = funnelColors;
    funnelChart.update("none");
    const lastIdx = funnelData.length - 1;
    const ratio = funnelData[0] ? funnelData[lastIdx] / funnelData[0] : 0;
    setText("chart-funnel-tag", funnelData[0] ? `${(ratio * 100).toFixed(1)}% conversion` : "—");

    // 4. event-type breakdown (donut) -------------------------------------
    const eb = ch.event_breakdown || {};
    const seen = new Set();
    const ebSorted = EVENT_COLORS
      .map(([k, color]) => ({ k, color, v: eb[k] || 0 }))
      .filter(r => { seen.add(r.k); return r.v > 0; });
    // Any unknown event types not in EVENT_COLORS get bucketed as "other".
    let otherCount = 0;
    Object.entries(eb).forEach(([k, v]) => { if (!seen.has(k) && v > 0) otherCount += v; });
    if (otherCount) ebSorted.push({ k: "other", color: C.line2, v: otherCount });
    const total = ebSorted.reduce((acc, r) => acc + r.v, 0);

    const eventsChart = ensureChart("chart-events", (ctx) =>
      new Chart(ctx, {
        type: "doughnut",
        data: { labels: [], datasets: [{
          data: [],
          backgroundColor: [],
          borderColor: C.bg,
          borderWidth: 2,
          hoverOffset: 6,
        }]},
        options: {
          maintainAspectRatio: false,
          responsive: true,
          cutout: "62%",
          plugins: {
            legend: {
              display: true,
              position: "right",
              labels: {
                color: C.text2,
                font: { size: 9, family: "'IBM Plex Mono', monospace" },
                boxWidth: 8,
                boxHeight: 8,
                padding: 6,
              },
            },
            tooltip: { callbacks: {
              label: (item) => " " + item.label + ": " + item.parsed + " (" + ((item.parsed / total) * 100).toFixed(1) + "%)",
            }},
          },
        },
      })
    );
    eventsChart.data.labels = ebSorted.map(r => r.k.replace("daydream.", ""));
    eventsChart.data.datasets[0].data = ebSorted.map(r => r.v);
    eventsChart.data.datasets[0].backgroundColor = ebSorted.map(r => r.color);
    eventsChart.update("none");
    setText("chart-events-tag", total ? `${fmtInt(total)} events · ${ebSorted.length} types` : "no events yet");
  }

  // --- recent memories ----------------------------------------------------

  function renderRecent(snap) {
    const list = $("recent-list");
    const recent = snap.recent_memories || [];
    setText("recent-tag", recent.length ? `last ${recent.length}` : "none yet");
    if (!recent.length) {
      list.innerHTML = '<li class="recent-empty">no memories yet this run</li>';
      return;
    }
    list.innerHTML = recent.map(m => {
      const tags = (m.tags || []).map(t => `<span class="recent-tag-chip">${escapeHtml(t)}</span>`).join("");
      const rel = (m.relevancy != null) ? m.relevancy.toFixed(2) : "—";
      return `<li class="recent-row">
        <span class="recent-ts">${fmtHms(m.ts)}</span>
        <span class="recent-session">${escapeHtml(m.session_short || "—")}</span>
        <span class="recent-tags">${tags || '<span class="recent-tag-chip">—</span>'}</span>
        <span class="recent-content"><span class="recent-rel">${rel}</span>${escapeHtml(m.content || "—")}</span>
      </li>`;
    }).join("");
  }

  function renderRejects(snap) {
    const list = $("rejects-list");
    const top = snap.reject_top || [];
    const distinct = snap.reject_distinct || 0;
    setText("rejects-tag", top.length
      ? `top ${top.length} of ${distinct} distinct`
      : "no rejects yet");
    if (!top.length) {
      list.innerHTML = '<li class="recent-empty">no rejected candidates yet</li>';
      return;
    }
    const max = top[0].count || 1;
    list.innerHTML = top.map(r => {
      const fill = (r.count / max) * 100;
      return `<li class="reject-row">
        <span class="reject-count">${fmtInt(r.count)}</span>
        <span class="reject-text">
          <span class="reject-rationale">${escapeHtml(r.rationale)}</span>
          ${r.sample ? `<span class="reject-snippet">${escapeHtml(r.sample)}</span>` : ""}
          <span class="reject-bar"><span class="reject-bar-fill" style="right:${100 - fill}%"></span></span>
        </span>
      </li>`;
    }).join("");
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // --- status + footer ----------------------------------------------------

  function renderStatus(snap) {
    const la = snap.last_activity || {};
    const failures = (snap.metrics || {}).failures || {};
    const anyFail = (failures.voyage_429 || 0) + (failures.chunk_errors || 0) +
                    (failures.hook_subprocess_failed || 0) + (failures.claude_timeouts || 0);
    let stateName = "idle";
    if (la.is_active && anyFail > 0) stateName = "warn";
    else if (la.is_active) stateName = "active";
    else if (anyFail > 0) stateName = "warn";
    else if (la.age_s == null) stateName = "idle";
    statusLed.dataset.state = stateName;
    statusLabel.textContent = stateName;
    statusAge.textContent = la.age_s == null ? "—" : "· " + fmtAge(la.age_s);

    const pipe = snap.pipeline || {};
    const pipeLine = [pipe.benchmark, pipe.sequence, pipe.stage, pipe.model, pipe.git_sha]
      .filter(Boolean).join(" · ");
    $("foot-meta").textContent = "basedir: " + (snap.basedir || "—");
    $("foot-pipeline").textContent = pipeLine || "—";
  }

  // --- network ------------------------------------------------------------

  async function fetchRuns() {
    const res = await fetch("/api/runs", { cache: "no-store" });
    if (!res.ok) throw new Error("runs fetch failed: " + res.status);
    return res.json();
  }
  async function fetchRun(id) {
    const res = await fetch("/api/run/" + encodeURIComponent(id), { cache: "no-store" });
    if (!res.ok) throw new Error("run fetch failed: " + res.status);
    return res.json();
  }

  // --- dropdown -----------------------------------------------------------

  function rebuildRunPicker(runs, preserveId) {
    const prev = preserveId || runSelect.value;
    runSelect.innerHTML = "";
    runs.forEach(r => {
      const opt = document.createElement("option");
      opt.value = r.id;
      const ageBit = r.last_activity_age_s == null ? "[—]" : "[" + fmtAge(r.last_activity_age_s) + "]";
      const liveBit = r.is_active ? " ●" : "";
      const memBit = "  ·  " + (r.memories || 0) + " mem";
      opt.textContent = r.label + memBit + "  " + ageBit + liveBit;
      runSelect.appendChild(opt);
    });
    if (prev && runs.some(r => r.id === prev)) {
      runSelect.value = prev;
    } else if (runs.length) {
      // Sticky default: prefer an active run with memories; else the run with the
      // most memories overall; else the most-recent. Stops the dropdown from
      // landing on a fresh-but-empty run as the only thing visible.
      const active = runs.filter(r => r.is_active && (r.memories || 0) > 0);
      const candidates = active.length ? active : runs.filter(r => (r.memories || 0) > 0);
      const pick = candidates.length
        ? candidates.reduce((a, b) => (b.memories || 0) > (a.memories || 0) ? b : a)
        : runs[0];
      runSelect.value = pick.id;
    }
    state.activeRunId = runSelect.value || null;
  }

  // --- main poll loop -----------------------------------------------------

  async function pollOnce() {
    try {
      // refresh the run list every poll too so newly-started runs appear without reload.
      const list = await fetchRuns();
      state.runs = list.runs || [];
      rebuildRunPicker(state.runs, state.activeRunId);
      if (!state.activeRunId) return;
      const snap = await fetchRun(state.activeRunId);
      if (snap.error) {
        statusLabel.textContent = "error";
        statusLed.dataset.state = "error";
        return;
      }
      state.lastSnapshot = snap;
      renderKpis(snap);
      renderCharts(snap);
      renderRecent(snap);
      renderRejects(snap);
      renderStatus(snap);
    } catch (err) {
      console.error(err);
      statusLabel.textContent = "fetch err";
      statusLed.dataset.state = "error";
    }
  }

  // --- countdown timer ----------------------------------------------------

  function tickCountdown() {
    if (!state.refreshOn) {
      refreshCountdown.textContent = "off";
      return;
    }
    state.countdownMs -= 200;
    if (state.countdownMs <= 0) {
      state.countdownMs = REFRESH_MS;
      pollOnce();
    }
    refreshCountdown.textContent = (state.countdownMs / 1000).toFixed(1) + "s";
  }

  // --- wire ---------------------------------------------------------------

  runSelect.addEventListener("change", () => {
    state.activeRunId = runSelect.value || null;
    state.countdownMs = REFRESH_MS;
    pollOnce();
  });

  refreshToggle.addEventListener("click", () => {
    state.refreshOn = !state.refreshOn;
    refreshToggle.dataset.state = state.refreshOn ? "on" : "off";
    refreshToggle.textContent = state.refreshOn ? "refresh on" : "refresh off";
    state.countdownMs = REFRESH_MS;
  });

  // first paint
  pollOnce();
  setInterval(tickCountdown, 200);
})();
