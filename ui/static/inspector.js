"use strict";

// ---- tiny helpers ---------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const el = (tag, attrs = {}, children = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const c of [].concat(children)) n.append(c && c.nodeType ? c : document.createTextNode(c ?? ""));
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTs = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 10) : "—");
const fmtSession = (s) => (s ? String(s).slice(0, 8) : "—");
const BK = [["markdown", "md"], ["vectors", "vec"], ["graph", "graph"]];
const SHORT = { markdown: "md", vectors: "vec", graph: "graph" };
const BACKEND_LABEL = { markdown: "markdown", vectors: "sqlite-vector", graph: "graph" };

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status}`);
  return data;
}
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast hidden"), 2600);
}

// ---- state ----------------------------------------------------------------
let MEMORIES = [];
let SUMMARY = null;
let browseSort = { key: "item_id", dir: 1 };

// ---- tabs -----------------------------------------------------------------
$$(".tab").forEach((b) =>
  b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === b.dataset.tab));
  })
);

// ---- summary strip --------------------------------------------------------

// Empty-state summary — shown when /api/summary returns 503 (no substrate
// loaded). The picker UI (input + Load + Browse…) stays exposed so the user
// can actually pick something instead of staring at a dead error banner.
function renderEmptySummary(reason) {
  const storeInput = el("input", {
    class: "store-input", type: "text", value: "", spellcheck: "false",
    placeholder: "path to a .../_memory directory",
    title: "enter the substrate directory and press Enter, or click Browse…",
  });
  storeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") reopenStore(storeInput.value); });
  const storePill = el("span", { class: "pill store-pill" }, [
    el("b", {}, "store "), storeInput,
    el("button", { class: "store-load", type: "button",
      on: { click: () => reopenStore(storeInput.value) } }, "Load"),
    el("button", { class: "store-browse", type: "button",
      title: "pick a memory store with the system folder dialog",
      on: { click: (e) => pickStore(e.currentTarget) } }, "Browse…"),
  ]);
  const note = el("span", { class: "pill warn" }, reason || "no substrate loaded");
  const box = $("#summary");
  box.textContent = "";
  box.append(storePill, note);
}

function renderSummary(s) {
  const counts = BK.map(([n]) => `${SHORT[n]} ${s.counts[n] ?? 0}`).join(" · ");
  const fan = ["1", "2", "3"].map((k) => `${k}→${s.fanout_histogram[k] || 0}`).join(" ");
  const status = BK.map(([n]) => `${SHORT[n]}:${s.backend_status[n]}`).join(" ");
  const storeInput = el("input", {
    class: "store-input", type: "text", value: s.store_path, spellcheck: "false",
    title: "change the substrate directory (a .../_memory), then Load or Enter",
  });
  storeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") reopenStore(storeInput.value); });
  const storePill = el("span", { class: "pill store-pill" }, [
    el("b", {}, "store "), storeInput,
    el("button", { class: "store-load", type: "button",
      on: { click: () => reopenStore(storeInput.value) } }, "Load"),
    el("button", { class: "store-browse", type: "button",
      title: "pick a memory store with the system folder dialog",
      on: { click: (e) => pickStore(e.currentTarget) } }, "Browse…"),
  ]);
  const bits = [
    storePill,
    el("span", { class: "pill" }, [el("b", {}, "profile "), `${s.profile} (${s.profile_source})`]),
    el("span", { class: "pill" }, [el("b", {}, "backends "), status]),
    el("span", { class: "pill" }, [el("b", {}, "counts "), counts]),
    el("span", { class: "pill" }, [el("b", {}, "unique "), String(s.total_unique)]),
    el("span", { class: "pill" }, [el("b", {}, "fan-out "), fan]),
    el("span", { class: "pill" }, [el("b", {}, "flagged "), String(s.flagged_count)]),
    el("span", { class: "pill" }, [el("b", {}, "mis-route "), String(s.misroute_count)]),
    el("span", { class: "pill" }, [el("b", {}, "ambiguous "), String(s.ambiguous_count)]),
  ];
  if (s.intent_mismatch_count) bits.push(el("span", { class: "pill warn" }, `intent-mismatch ${s.intent_mismatch_count}`));
  for (const w of s.warnings || []) bits.push(el("span", { class: "pill warn" }, "⚠ " + w));
  // Loud signal when the loaded path has no cookbook-memory backends. Without
  // this, an all-absent load reads identically to a healthy 0-memory store.
  const allAbsent = BK.every(([n]) => s.backend_status[n] === "absent");
  if (allAbsent) {
    bits.push(el("span", { class: "pill warn" },
      "⚠ no backends found at this path — try the .../_memory subdir"));
  }
  const box = $("#summary");
  box.textContent = "";
  bits.forEach((b) => box.append(b));
}

// Open the system folder-picker (server-side native dialog, since the inspector runs on the
// user's own machine) and load whatever directory they choose. The browser can't read real
// filesystem paths, so the local server pops the dialog and hands back the chosen absolute path.
async function pickStore(btn) {
  const label = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = "Opening…"; }
  try {
    const res = await postJSON("/api/pick-store", { initial: (SUMMARY && SUMMARY.store_path) || "" });
    if (res.cancelled) return;            // user dismissed the dialog — no-op, no toast
    await reopenStore(res.store);
  } catch (e) {
    toast("folder picker unavailable: " + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

// Change the active substrate directory live (no inspector restart). POSTs the new dir,
// then refreshes summary + memories + the Browse/Routing views from the new store.
//
// Opens can take 10-20s on a cold Voyage-backed accuracy profile (the server's
// `/api/reopen` rebuilds the vector index synchronously), so the picker buttons
// are disabled and a clear "loading…" pill replaces the warning chip while
// reopen is in flight — no more deceptive "is it hung?" UX cliff.
async function reopenStore(store) {
  const dir = (store || "").trim();
  if (!dir) { toast("enter a store directory", true); return; }
  setReopenPending(dir);
  let summary;
  try {
    summary = await postJSON("/api/reopen", { store: dir });
  } catch (e) {
    setReopenPending(null);
    toast("could not open store: " + e.message, true);
    return;
  }
  SUMMARY = summary;
  renderSummary(SUMMARY);
  try {
    MEMORIES = (await getJSON("/api/memories")).memories;
  } catch (e) {
    MEMORIES = [];
    toast("opened store but failed to list memories: " + e.message, true);
  }
  renderBrowse();
  renderRouting();
  $("#modal").classList.add("hidden");      // close any popover from the previous store
  $("#probe-decision").textContent = "";    // clear stale probe results
  $("#probe-columns").textContent = "";
  toast("loaded " + SUMMARY.store_path);
}

// Visual feedback while `/api/reopen` is in flight. Disables every store-input/
// button in the summary strip and adds a "loading…" pill so the user can see
// the request is working — a Voyage-backed cold open can take ~15s.
function setReopenPending(dir) {
  const box = $("#summary");
  const inputs  = box.querySelectorAll("input, button");
  inputs.forEach((el) => { el.disabled = !!dir; });
  let pill = box.querySelector(".pill.loading");
  if (dir) {
    if (!pill) {
      pill = el("span", { class: "pill loading" }, "loading " + dir + " …");
      box.append(pill);
    } else {
      pill.textContent = "loading " + dir + " …";
    }
  } else if (pill) {
    pill.remove();
  }
}

// ---- score bars (shared by routing + probe decision) ----------------------
function scoreBars(scores, choice) {
  const max = Math.max(0.0001, ...BK.map(([n]) => Math.abs(scores[n] || 0)));
  const bars = BK.map(([n, cls]) => {
    const v = scores[n] || 0;
    const pct = Math.max(0, (Math.abs(v) / max) * 100);
    return el("div", { class: "bar-row" + (n === choice ? " winner" : "") }, [
      el("span", { class: "bk" }, SHORT[n]),
      el("div", { class: "bar-track" }, [el("div", { class: `bar-fill ${cls}`, style: `width:${pct}%` })]),
      el("span", { class: "val" }, v.toFixed(2)),
    ]);
  });
  return el("div", { class: "bars" }, bars);
}

function backendClass(name) {
  const found = BK.find(([n]) => n === name);
  return found ? found[1] : "";
}

function backendChips(membership, memory = null) {
  return el(
    "span",
    { class: "chips" },
    BK.map(([n, cls]) => {
      const klass = `chip ${cls} ` + (membership[n] ? "on" : "off");
      if (!memory) return el("span", { class: klass }, SHORT[n]);
      return el(
        "button",
        {
          type: "button",
          class: klass,
          title: `view ${BACKEND_LABEL[n]} stored artifact for ${memory.item_id}`,
          "aria-label": `view ${BACKEND_LABEL[n]} stored artifact for ${memory.item_id}`,
          on: { click: (e) => { e.stopPropagation(); openArtifactPopover(memory, n); } },
        },
        SHORT[n]
      );
    })
  );
}

// ---- Browse ---------------------------------------------------------------
function renderBrowse() {
  const filter = $("#browse-filter").value.trim().toLowerCase();
  const rows = MEMORIES.filter((m) => {
    if (!filter) return true;
    return (
      m.item_id.toLowerCase().includes(filter) ||
      (m.session_id || "").toLowerCase().includes(filter) ||
      (m.content || "").toLowerCase().includes(filter) ||
      (m.tags || []).join(" ").toLowerCase().includes(filter)
    );
  });
  const { key, dir } = browseSort;
  rows.sort((a, b) => {
    let av, bv;
    if (key === "tags") { av = (a.tags || []).join(","); bv = (b.tags || []).join(","); }
    else { av = a[key] ?? ""; bv = b[key] ?? ""; }
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
  const tbody = $("#browse-rows");
  tbody.textContent = "";
  for (const m of rows) {
    const tr = el("tr", { on: { click: () => openDetail(m) } }, [
      el("td", { class: "id" }, m.item_id),
      el("td", { class: "dim", title: m.session_id || null }, fmtSession(m.session_id)),
      el("td", { class: "content" }, m.snippet),
      el("td", { class: "dim" }, (m.tags || []).map((t) => el("span", { class: "tag" }, t))),
      el("td", { class: "dim" }, fmtTs(m.timestamp)),
      el("td", { class: "dim" }, String(m.version)),
      el("td", {}, backendChips(m.membership, m)),
    ]);
    tbody.append(tr);
  }
}

// ---- backend artifact popover (badge click) -------------------------------
// Clicking a memory's backend badge shows that backend's STORED ARTIFACT (not a
// retrieval): markdown → the real .md file + OKF frontmatter; vectors → the stored
// record + embedding meta; graph → the node + its edges. Each has a Copy-path button.
// Reuses the shared #modal (the same overlay as the row-detail view).
function artifactTitle(backend, itemId) {
  return el("h2", {}, [
    el("span", { class: `backend-name ${backendClass(backend)}` }, BACKEND_LABEL[backend] || backend),
    " · ",
    el("span", { class: "id" }, itemId),
  ]);
}

async function openArtifactPopover(m, backend) {
  const body = $("#modal-body");
  body.textContent = "";
  body.append(artifactTitle(backend, m.item_id));
  body.append(el("div", { class: "hint" }, "loading…"));
  $("#modal").classList.remove("hidden");
  let data;
  try {
    data = await getJSON(
      `/api/backend-artifact?item_id=${encodeURIComponent(m.item_id)}` +
      `&backend=${encodeURIComponent(backend)}`
    );
  } catch (e) {
    body.append(el("div", { class: "err" }, "artifact load failed: " + e.message));
    return;
  }
  renderArtifact(body, data);
}

function renderArtifact(body, data) {
  body.textContent = "";
  body.append(artifactTitle(data.backend, data.item_id));
  body.append(copyPathRow(data.copy_path, data.exists));
  if (data.kind === "markdown") renderMdArtifact(body, data);
  else if (data.kind === "vector") renderVecArtifact(body, data);
  else renderGraphArtifact(body, data);
}

function copyPathRow(path, exists) {
  const row = el("div", { class: "artifact-path" }, [
    el("span", { class: "label" }, "path"),
    el("code", { class: "path" }, path || "—"),
    el("button", {
      type: "button", class: "copy-path", title: "copy path to clipboard",
      on: { click: () => copyToClipboard(path) },
    }, "Copy path"),
  ]);
  if (exists === false) row.append(el("span", { class: "pill warn" }, "not on disk"));
  return row;
}

async function copyToClipboard(text) {
  if (!text) { toast("no path to copy", true); return; }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = el("textarea", {}, text);
      ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.append(ta); ta.select();
      document.execCommand("copy"); ta.remove();
    }
    toast("path copied to clipboard");
  } catch (e) {
    toast("copy failed: " + e.message, true);
  }
}

function kvBlock(pairs) {
  const kv = el("div", { class: "kv" });
  for (const [k, v] of pairs) { kv.append(el("span", { class: "k" }, k)); kv.append(el("span", {}, v)); }
  return kv;
}

function renderMdArtifact(body, data) {
  if (data.error) { body.append(el("div", { class: "err" }, data.error)); return; }
  if (!data.exists) {
    body.append(el("div", { class: "hint", style: "margin-top:12px" }, data.note || "no file on disk"));
    return;
  }
  const fm = data.frontmatter || {};
  const keys = Object.keys(fm);
  body.append(el("div", { class: "label", style: "margin-top:12px" }, "OKF frontmatter"));
  body.append(keys.length
    ? kvBlock(keys.map((k) => [k, typeof fm[k] === "string" ? fm[k] : JSON.stringify(fm[k])]))
    : el("div", { class: "hint" }, "(none)"));
  body.append(el("div", { class: "label", style: "margin-top:12px" }, "raw .md file"));
  body.append(el("pre", { class: "artifact-file" }, data.text || ""));
}

function renderVecArtifact(body, data) {
  const emb = data.embedding || {};
  body.append(el("div", { class: "label", style: "margin-top:12px" }, "embedding"));
  body.append(kvBlock([
    ["dim", emb.dim != null ? String(emb.dim) : "—"],
    ["model", emb.model || "—"],
    ["index", emb.index || "—"],
  ]));
  if (emb.note) body.append(el("div", { class: "note" }, emb.note));
  if (data.item) appendStoredItem(body, data.item);
  else body.append(el("div", { class: "hint", style: "margin-top:12px" }, "no record stored in this backend"));
}

function renderGraphArtifact(body, data) {
  if (data.edges && data.edges.length) {
    const edges = el("div", { class: "edges" }, [el("div", { class: "label", style: "margin-top:12px" }, "graph edges")]);
    for (const e of data.edges) {
      edges.append(el("div", { class: "edge" }, [
        el("span", { class: "rel" }, e.relation), " → ",
        el("span", { class: "tgt" }, e.target),
        e.anchor ? `  (anchor: "${e.anchor}")` : "",
      ]));
    }
    body.append(edges);
  } else {
    body.append(el("div", { class: "hint", style: "margin-top:12px" }, "no graph edges for this memory"));
  }
  if (data.node) appendStoredItem(body, data.node);
  else body.append(el("div", { class: "hint" }, "no node stored in this backend"));
}

function appendStoredItem(body, item) {
  const pairs = [["tags", (item.tags || []).join(", ") || "—"]];
  if (item.timestamp != null) pairs.push(["timestamp", `${fmtTs(item.timestamp)} (${item.timestamp})`]);
  if (item.version != null) pairs.push(["version", String(item.version)]);
  body.append(el("div", { class: "label", style: "margin-top:12px" }, "stored record"));
  body.append(kvBlock(pairs));
  body.append(el("div", { class: "label", style: "margin-top:8px" }, "content"));
  body.append(el("pre", {}, item.content || ""));
  body.append(el("div", { class: "label", style: "margin-top:8px" }, "metadata"));
  body.append(el("pre", {}, JSON.stringify(item.metadata || {}, null, 2)));
}
$("#browse-filter").addEventListener("input", renderBrowse);
$$(".grid th[data-sort]").forEach((th) =>
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    browseSort = { key, dir: browseSort.key === key ? -browseSort.dir : 1 };
    renderBrowse();
  })
);

// ---- detail modal ---------------------------------------------------------
function openDetail(m) {
  const body = $("#modal-body");
  body.textContent = "";
  body.append(el("h2", {}, m.item_id));
  body.append(el("div", { class: "snippet" }, ""));

  const kv = el("div", { class: "kv" });
  const add = (k, v) => { kv.append(el("span", { class: "k" }, k)); kv.append(el("span", {}, v)); };
  add("tags", (m.tags || []).join(", ") || "—");
  add("timestamp", `${fmtTs(m.timestamp)} (${m.timestamp})`);
  add("version", String(m.version));
  add("relevancy", String(m.relevancy));
  add("source", m.source || "—");
  add("session_id", m.session_id || "—");
  add("backends", BK.filter(([n]) => m.membership[n]).map(([n]) => SHORT[n]).join(", ") || "none");
  if (m.okf && (m.okf.title || m.okf.type || m.okf.resource)) {
    add("okf.title", m.okf.title || "—");
    add("okf.type", m.okf.type || "—");
    add("okf.resource", m.okf.resource || "—");
  }
  body.append(kv);

  if (m.edges && m.edges.length) {
    const edges = el("div", { class: "edges" }, [el("div", { class: "label" }, "graph edges")]);
    for (const e of m.edges) {
      edges.append(
        el("div", { class: "edge" }, [
          el("span", { class: "rel" }, e.relation),
          " → ",
          el("span", { class: "tgt" }, e.target),
          e.anchor ? `  (anchor: "${e.anchor}")` : "",
        ])
      );
    }
    body.append(edges);
  }

  body.append(el("div", { class: "label", style: "margin-top:12px" }, "full content"));
  body.append(el("pre", {}, m.content || ""));
  body.append(el("div", { class: "label", style: "margin-top:12px" }, "full metadata"));
  body.append(el("pre", {}, JSON.stringify(m.metadata || {}, null, 2)));
  $("#modal").classList.remove("hidden");
}
$("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").classList.add("hidden"); });

// ---- Routing-effectiveness ------------------------------------------------
function renderRouting() {
  const flaggedOnly = $("#routing-flagged-only").checked;
  const rows = MEMORIES.filter((m) => !flaggedOnly || m.routing.flagged);
  const box = $("#routing-rows");
  box.textContent = "";
  const t = SUMMARY ? SUMMARY.margin_threshold : 1.0;
  for (const m of rows) {
    const r = m.routing;
    const head = el("div", { class: "card-head" }, [
      el("span", { class: "id" }, m.item_id),
      r.flagged ? el("span", { class: "flag" }, "⚠ flagged") : el("span", { class: "hint" }, "ok"),
    ]);

    const actual = el("div", {}, [el("div", { class: "label" }, "actual landing"),
      backendChips(m.membership)]);
    const predicted = el("div", {}, [
      el("div", { class: "label" }, `predicted classify → ${SHORT[r.classify]}`),
      scoreBars(r.scores, r.classify),
      el("div", { class: "margin" + (r.ambiguous ? " low" : "") }, `margin ${r.margin.toFixed(2)} (thr ${t})`),
    ]);
    const plan = el("div", {}, [el("div", { class: "label" }, "write_plan"),
      el("span", { class: "chips" }, BK.map(([n, cls]) =>
        el("span", { class: `chip ${cls} ` + (r.write_plan.includes(n) ? "on" : "off") }, SHORT[n])))]);

    const card = el("div", { class: "card" + (r.flagged ? " flagged" : "") }, [
      head,
      el("div", { class: "snippet" }, m.snippet),
      el("div", { class: "row" }, [actual, predicted, plan]),
    ]);

    if (r.flag_reasons && r.flag_reasons.length) {
      const ul = el("ul", { class: "reasons" });
      r.flag_reasons.forEach((x) => ul.append(el("li", {}, x)));
      card.append(ul);
    }
    if (r.intent_mismatch) {
      card.append(el("div", { class: "mismatch" },
        `intent mismatch: human-labelled "${r.human_intent}" but classifier routes to "${r.classify}"`));
    }
    card.append(el("button", { class: "capture-btn", on: { click: () => captureRoute(m) } },
      "capture as routing eval case"));
    box.append(card);
  }
}
$("#routing-flagged-only").addEventListener("change", renderRouting);

async function captureRoute(m) {
  try {
    const res = await postJSON("/api/capture", {
      kind: "route",
      content: m.content,
      expected: { backend: m.routing.classify, write_plan: m.routing.write_plan },
      note: `captured from routing view for ${m.item_id}`,
    });
    toast(`captured route case #${res.count} → ${res.path.split("/").pop()}`);
  } catch (e) {
    toast("capture failed: " + e.message, true);
  }
}

// ---- Query Probe ----------------------------------------------------------
async function runProbe() {
  const q = $("#probe-q").value;
  const k = $("#probe-k").value || 5;
  if (!q.trim()) { toast("enter a query", true); return; }
  let data;
  try { data = await getJSON(`/api/probe?q=${encodeURIComponent(q)}&k=${encodeURIComponent(k)}`); }
  catch (e) { toast("probe failed: " + e.message, true); return; }

  const d = data.decision;
  const dec = $("#probe-decision");
  dec.textContent = "";
  dec.append(
    el("div", { class: "decision-box" }, [
      el("div", {}, [el("div", { class: "label" }, "routing decision"),
        el("div", {}, [el("span", { class: "id" }, `classify → ${SHORT[d.choice]}`)])]),
      scoreBars(d.scores, d.choice),
      el("div", { class: "margin" + (d.margin < (SUMMARY ? SUMMARY.margin_threshold : 1) ? " low" : "") },
        `margin ${d.margin.toFixed(2)}`),
      el("button", { class: "capture-btn", on: { click: () => captureRetrieval(data) } },
        "capture as retrieval eval case"),
    ])
  );

  const cols = $("#probe-columns");
  cols.textContent = "";
  for (const [n, cls] of BK) {
    cols.append(probeColumn(cls, n, data.per_backend[n], data.score_semantics[n], data.errors[n]));
  }
  cols.append(probeColumn("engine", "engine (RouterStore.search)", data.engine, "routed: the backend the query was sent to", data.engine_error));
}
function probeColumn(cls, title, hits, sem, err) {
  const col = el("div", { class: "col" }, [el("h3", { class: cls }, title), el("div", { class: "sem" }, sem || "")]);
  if (err) { col.append(el("div", { class: "err" }, err)); return col; }
  if (!hits || !hits.length) { col.append(el("div", { class: "hint" }, "no hits")); return col; }
  for (const h of hits) {
    col.append(el("div", { class: "hit" }, [
      el("span", { class: "hit-id" }, h.item_id),
      el("span", { class: "hit-meta" }, `  #${h.rank}  score ${Number(h.score).toFixed(3)}`),
      el("span", { class: "hit-snip" }, h.snippet),
    ]));
  }
  return col;
}
async function captureRetrieval(data) {
  const ids = (data.engine || []).map((h) => h.item_id);
  try {
    const res = await postJSON("/api/capture", {
      kind: "retrieval",
      query: data.query,
      expected: { backend: data.decision.choice, ids },
      note: `captured from probe view (k=${data.k})`,
    });
    toast(`captured retrieval case #${res.count} → ${res.path.split("/").pop()}`);
  } catch (e) {
    toast("capture failed: " + e.message, true);
  }
}
$("#probe-go").addEventListener("click", runProbe);
$("#probe-q").addEventListener("keydown", (e) => { if (e.key === "Enter") runProbe(); });

// ---- boot -----------------------------------------------------------------
async function boot() {
  try {
    SUMMARY = await getJSON("/api/summary");
    renderSummary(SUMMARY);
    MEMORIES = (await getJSON("/api/memories")).memories;
    renderBrowse();
    renderRouting();
  } catch (e) {
    // Server returned 503 when no substrate is loaded — show the picker so
    // the user can actually load one, instead of a dead error banner.
    const msg = String(e && e.message || e);
    if (msg.includes("503")) {
      renderEmptySummary("no substrate loaded — pick a .../_memory directory");
    } else {
      renderEmptySummary("failed to load: " + msg);
    }
    MEMORIES = [];
    renderBrowse();
    renderRouting();
  }
}
boot();
