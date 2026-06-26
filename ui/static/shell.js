/* ui · shell — top-level view-switcher.
 *
 * Toggles body class (.ui-mode-monitor / .ui-mode-inspector) which the shell
 * CSS uses to show/hide the two per-view containers. Persists the choice in
 * localStorage so a reload keeps you on the same view. Hash-based deep links
 * (`#monitor` / `#inspector`) also work.
 *
 * The per-view JS bundles (monitor.js, inspector.js) load AFTER this script
 * and run unconditionally — they don't care which view is currently visible.
 */

(() => {
  "use strict";

  const VIEWS = ["monitor", "inspector"];
  const STORAGE_KEY = "ui.view";

  const tabs = Array.from(document.querySelectorAll(".ui-shell-tab"));
  const meta = document.getElementById("ui-shell-meta");

  function setView(view) {
    if (!VIEWS.includes(view)) view = "monitor";
    document.body.classList.remove("ui-mode-monitor", "ui-mode-inspector");
    document.body.classList.add(`ui-mode-${view}`);
    tabs.forEach(t => {
      const active = t.dataset.view === view;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (meta) {
      meta.textContent = view === "monitor"
        ? "live operator dashboard"
        : "memory store inspector";
    }
    try { localStorage.setItem(STORAGE_KEY, view); } catch (_) { /* private mode */ }
    if (window.location.hash !== `#${view}`) {
      history.replaceState(null, "", `#${view}`);
    }
    // Resize hint for Chart.js, which measures canvas at init and breaks
    // when the canvas is `display: none` during init. A resize event after
    // the view becomes visible nudges every chart to remeasure.
    window.dispatchEvent(new Event("resize"));
  }

  function pickInitialView() {
    const hash = (window.location.hash || "").replace(/^#/, "");
    if (VIEWS.includes(hash)) return hash;
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (VIEWS.includes(saved)) return saved;
    } catch (_) { /* ignored */ }
    return "monitor";
  }

  tabs.forEach(t => {
    t.addEventListener("click", () => setView(t.dataset.view));
  });
  window.addEventListener("hashchange", () => {
    const hash = (window.location.hash || "").replace(/^#/, "");
    if (VIEWS.includes(hash)) setView(hash);
  });

  setView(pickInitialView());
})();
