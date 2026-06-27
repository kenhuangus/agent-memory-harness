"""ThreadingHTTPServer + handler for the combined memory UI.

Hosts BOTH views under one origin:

* **Monitor** — live operator dashboard for in-flight benchmark runs.
  Auto-discovers ``results/<run>/_memory/.cookbook-memory`` basedirs and
  serves snapshots aggregated from the diary + harness events + run JSON.
* **Inspector** — read-only memory-store browser + routing-effectiveness
  view + query probe. Same surface as the legacy ``router_ui`` package
  (this module is its renamed home).

Monitor and inspector live behind separate API namespaces; the shell page
swaps which view's DOM is visible. Either can be used without the other:
* Inspector with no substrate loaded → its routes return 503 until the
  user opens ``POST /api/reopen`` from the inspector UI.
* Monitor with no ``--results-root`` → ``/api/runs`` returns an empty list.

Routes
------
Shell + assets
  ``GET  /``                      the shell page (defaults to monitor view)
  ``GET  /<asset>``               whitelisted static (no path traversal)
Monitor
  ``GET  /api/runs``              every run descriptor (newest first)
  ``GET  /api/run/<id>``          full snapshot for one run
  ``GET  /api/run/<id>/report.json``  snapshot dict verbatim, as a download
  ``GET  /api/run/<id>/report.md``    Markdown report of the snapshot, as a download
Inspector
  ``GET  /api/summary``           store path, profile, counts, fan-out histogram, flags
  ``GET  /api/memories``          the de-duped memory list (browse + routing)
  ``GET  /api/probe?q=...&k=5``   routing decision + per-backend + engine results
  ``GET  /api/backend-artifact``  one Browse memory's stored artifact in one backend
  ``POST /api/capture``           append a captured eval case to captured_cases.jsonl
  ``POST /api/reopen``            swap the active substrate to a new store dir, live
  ``POST /api/pick-store``        open a native OS folder-picker and return the chosen dir

Stdlib only. Binds 127.0.0.1 by default.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

#: Allowlisted run-id charset — real run dirs are ASCII alnum + ``.`` ``_`` ``-``.
#: Used to validate ``/api/run/<id>`` ids: blocks traversal and keeps the id safe to
#: embed verbatim in a Content-Disposition filename.
_RUN_ID_RE = re.compile(r"[A-Za-z0-9._-]+")

try:  # dual import: package (``-m ui``) and standalone (run-dir self-test)
    from .substrate import open_substrate
    from .picker import pick_directory, PickerUnavailable
    from .aggregator import discover_runs, snapshot, report_markdown
except ImportError:  # pragma: no cover - import shim
    from substrate import open_substrate                # type: ignore
    from picker import pick_directory, PickerUnavailable  # type: ignore
    from aggregator import discover_runs, snapshot, report_markdown  # type: ignore


_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Whitelisted static assets -> (filename, content-type). No arbitrary path serving.
_STATIC = {
    "/":                ("index.html",   "text/html; charset=utf-8"),
    "/index.html":      ("index.html",   "text/html; charset=utf-8"),
    # Shell chrome
    "/shell.css":       ("shell.css",    "text/css; charset=utf-8"),
    "/shell.js":        ("shell.js",     "application/javascript; charset=utf-8"),
    # Monitor view
    "/monitor.css":     ("monitor.css",  "text/css; charset=utf-8"),
    "/monitor.js":      ("monitor.js",   "application/javascript; charset=utf-8"),
    # Inspector view
    "/inspector.css":   ("inspector.css","text/css; charset=utf-8"),
    "/inspector.js":    ("inspector.js", "application/javascript; charset=utf-8"),
    # Graphs view
    "/graphs.css":      ("graphs.css",   "text/css; charset=utf-8"),
    "/graphs.js":       ("graphs.js",    "application/javascript; charset=utf-8"),
}


class _State:
    """Holds the active substrate (inspector) and the results root (monitor).

    Either may be ``None`` — each view degrades independently. ``POST /api/reopen``
    can install/swap the substrate live; the monitor's results root is fixed at
    server start.
    """

    def __init__(self, substrate: Any, results_root: Path | None = None) -> None:
        self.substrate = substrate
        self.results_root = results_root


class UIHandler(BaseHTTPRequestHandler):
    """Combined monitor + inspector handler. Either surface degrades when its
    backing data is absent; the other keeps working."""

    state: Any = None
    server_version = "CookbookUI/1.0"
    protocol_version = "HTTP/1.1"

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in _STATIC:
            return self._serve_static(path)

        # Graphs routes — re-scan the results dir on every call so freshly
        # merged result directories appear next refresh (no regen step).
        # Independent of the loaded substrate; degrades to empty when
        # results_root is unset.
        if path == "/api/graphs/benchmarks":
            root = self.state.results_root
            from . import aggregator as _agg
            benchmarks = _agg.discover_benchmarks(root) if root else []
            return self._json({"benchmarks": benchmarks})
        if path == "/api/graphs/manifest":
            root = self.state.results_root
            from . import aggregator as _agg
            qs = parse_qs(parsed.query)
            prefix = (qs.get("bench") or ["django_django_sequence"])[0]
            manifest = _agg.benchmark_manifest(root, prefix) if root else []
            return self._json({"manifest": manifest, "bench": prefix})
        if path == "/api/graphs/django":
            # Back-compat for the pre-multi-benchmark client.
            root = self.state.results_root
            from . import aggregator as _agg
            manifest = _agg.django_manifest(root) if root else []
            return self._json({"manifest": manifest})

        # Monitor routes
        if path == "/api/runs":
            return self._monitor_runs()
        if path.startswith("/api/run/"):
            rest = path[len("/api/run/"):]
            # Per-run report exports — same run-resolution as /api/run/<id>, but
            # served as downloadable attachments (JSON verbatim snapshot / MD report).
            if rest.endswith("/report.json"):
                return self._monitor_report(rest[: -len("/report.json")], fmt="json")
            if rest.endswith("/report.md"):
                return self._monitor_report(rest[: -len("/report.md")], fmt="md")
            return self._monitor_run(rest)

        # Inspector routes — require a loaded substrate.
        if path in ("/api/summary", "/api/memories", "/api/probe", "/api/backend-artifact"):
            if self.state.substrate is None:
                return self._json({"error": "no substrate loaded; use /api/reopen or the inspector picker"}, code=503)
            if path == "/api/summary":
                return self._json(self.state.substrate.summary())
            if path == "/api/memories":
                return self._json({"memories": self.state.substrate.memories()})
            if path == "/api/probe":
                qs = parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0]
                k = _int((qs.get("k") or ["5"])[0], default=5)
                return self._json(self.state.substrate.probe(query, k=k))
            if path == "/api/backend-artifact":
                qs = parse_qs(parsed.query)
                item_id = (qs.get("item_id") or [""])[0]
                backend = (qs.get("backend") or [""])[0]
                try:
                    return self._json(self.state.substrate.artifact_view(item_id, backend))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, code=400)
                except KeyError:
                    return self._json({"error": f"memory not found: {item_id}"}, code=404)

        return self._json({"error": "not found", "path": path}, code=404)

    # -- POST --------------------------------------------------------------
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/capture", "/api/reopen", "/api/pick-store"):
            return self._json({"error": "not found", "path": parsed.path}, code=404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            return self._json({"error": f"bad request body: {exc}"}, code=400)
        if parsed.path == "/api/reopen":
            return self._reopen(payload)
        if parsed.path == "/api/pick-store":
            return self._pick_store(payload)
        return self._capture(payload)

    # -- monitor surface ---------------------------------------------------
    def _monitor_runs(self) -> None:
        root = self.state.results_root
        if root is None or not root.is_dir():
            return self._json({"runs": []})
        return self._json({"runs": discover_runs(root)})

    def _resolve_run_dir(self, run_id: str):
        """Shared run-id resolution for /api/run/<id> and its report exports.

        Sends the matching error response and returns ``None`` on failure;
        returns the validated run ``Path`` on success."""
        root = self.state.results_root
        if root is None or not root.is_dir():
            self._json({"error": "results root not configured"}, code=404)
            return None
        # Allowlist the run-id charset (real run dirs are alnum + ``._-``). This both
        # blocks path traversal AND keeps the id safe to embed verbatim in a
        # Content-Disposition filename (no quotes / control bytes / separators).
        if not run_id or run_id in (".", "..") or not _RUN_ID_RE.fullmatch(run_id):
            self._json({"error": "bad run id"}, code=400)
            return None
        run_dir = root / run_id
        if not run_dir.is_dir():
            self._json({"error": f"unknown run: {run_id}"}, code=404)
            return None
        return run_dir

    def _monitor_run(self, run_id: str) -> None:
        run_dir = self._resolve_run_dir(run_id)
        if run_dir is None:
            return
        return self._json(snapshot(run_dir))

    def _monitor_report(self, run_id: str, *, fmt: str) -> None:
        """Download the selected run's report: the snapshot dict as JSON verbatim,
        or its Markdown rendering. Both are sent as ``attachment`` downloads."""
        run_dir = self._resolve_run_dir(run_id)
        if run_dir is None:
            return
        snap = snapshot(run_dir)
        if fmt == "json":
            body = json.dumps(snap, default=str, indent=2).encode("utf-8")
            return self._download(body, "application/json; charset=utf-8", f"{run_id}-report.json")
        body = report_markdown(snap).encode("utf-8")
        return self._download(body, "text/markdown; charset=utf-8", f"{run_id}-report.md")

    # -- inspector surface --------------------------------------------------
    def _capture(self, payload: dict) -> None:
        if self.state.substrate is None:
            return self._json({"error": "no substrate loaded"}, code=503)
        try:
            result = self.state.substrate.capture(payload)
        except ValueError as exc:
            return self._json({"error": str(exc)}, code=400)
        return self._json(result)

    def _reopen(self, payload: dict) -> None:
        """Install or swap the active substrate. Works when the inspector started
        with no substrate (the user can pick a store via the UI after the page loads)."""
        store = (payload.get("store") or "").strip()
        if not store:
            return self._json({"error": "store is required"}, code=400)
        if not Path(store).exists():
            return self._json({"error": f"store dir not found: {store}"}, code=400)
        current = self.state.substrate
        profile = (payload.get("profile") or (current.profile if current else "auto") or "auto").strip()
        margin = current.margin_threshold if current else None
        try:
            new_sub = open_substrate(store, profile, margin_threshold=margin)
        except ValueError as exc:
            return self._json({"error": str(exc)}, code=400)
        self.state.substrate = new_sub
        return self._json(new_sub.summary())

    def _pick_store(self, payload: dict) -> None:
        initial = (payload.get("initial") or "").strip() or None
        try:
            chosen = pick_directory(initial)
        except PickerUnavailable as exc:
            return self._json({"error": str(exc)}, code=501)
        if not chosen:
            return self._json({"cancelled": True})
        return self._json({"store": chosen})

    # -- helpers -----------------------------------------------------------
    def _serve_static(self, route: str) -> None:
        filename, ctype = _STATIC[route]
        try:
            data = (_STATIC_DIR / filename).read_bytes()
        except OSError:
            return self._json({"error": f"missing asset {filename}"}, code=500)
        self._raw(data, ctype)

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self._raw(body, "application/json; charset=utf-8", code=code)

    def _raw(self, data: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _download(self, data: bytes, ctype: str, filename: str, code: int = 200) -> None:
        """Like ``_raw`` but adds a Content-Disposition attachment header so the
        browser saves the body as ``filename``. ``filename`` is derived from an
        already-validated run id (no ``/`` or dot-segments), so it is safe."""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - matches base signature
        """Quiet by default (one line per request to stderr would spam the terminal log)."""
        return


def _int(value: str, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def build_server(host: str, port: int, substrate: Any, results_root: Path | None) -> ThreadingHTTPServer:
    """ThreadingHTTPServer bound to ``(host, port)`` serving both views.

    ``substrate`` may be ``None`` (the inspector starts empty; the user can
    load a store via the in-UI picker). ``results_root`` may be ``None`` (the
    monitor will report zero runs)."""
    handler = type("BoundUIHandler", (UIHandler,), {"state": _State(substrate, results_root)})
    return ThreadingHTTPServer((host, port), handler)


__all__ = ["build_server", "UIHandler"]
