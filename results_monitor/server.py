"""ThreadingHTTPServer + handler for the live results monitor.

Routes
------
``GET  /``                    the dashboard page
``GET  /app.js`` / ``/styles.css``  whitelisted static assets (no path traversal)
``GET  /api/runs``            descriptors for every results/<run>/_memory/.cookbook-memory
``GET  /api/run/<id>``        full snapshot for one run

Stdlib only. Binds 127.0.0.1 by default.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from .aggregator import discover_runs, snapshot
except ImportError:  # pragma: no cover
    from aggregator import discover_runs, snapshot  # type: ignore

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class _State:
    """Holds the active ``results/`` root. Cheap; one per server."""

    def __init__(self, results_root: Path) -> None:
        self.results_root = results_root


class MonitorHandler(BaseHTTPRequestHandler):
    state: Any = None
    server_version = "ResultsMonitor/1.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in _STATIC:
            return self._serve_static(path)
        if path == "/api/runs":
            return self._json({"runs": discover_runs(self.state.results_root)})
        if path.startswith("/api/run/"):
            run_id = path[len("/api/run/"):]
            # Strict: only direct children of results_root, no traversal.
            if "/" in run_id or run_id in ("", ".", ".."):
                return self._json({"error": "bad run id"}, code=400)
            run_dir = self.state.results_root / run_id
            if not run_dir.is_dir():
                return self._json({"error": f"unknown run: {run_id}"}, code=404)
            return self._json(snapshot(run_dir))
        return self._json({"error": "not found", "path": path}, code=404)

    # -- helpers ------------------------------------------------------------
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

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def build_server(host: str, port: int, results_root: Path) -> ThreadingHTTPServer:
    handler = type("BoundMonitorHandler", (MonitorHandler,), {"state": _State(results_root)})
    return ThreadingHTTPServer((host, port), handler)


__all__ = ["build_server", "MonitorHandler"]
