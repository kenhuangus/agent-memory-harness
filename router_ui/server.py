"""ThreadingHTTPServer + request handler for the memory inspector.

Serves one static page (``static/index.html`` + ``app.js`` + ``styles.css``) and a small
read-only JSON API over a shared, swappable :class:`~router_ui.substrate.Substrate` (held in
a :class:`_State` holder so ``POST /api/reopen`` can change the store dir live, no restart).
Binds ``127.0.0.1`` only. Stdlib only — no dependencies.

Routes
------
``GET  /``                         the inspector page
``GET  /app.js`` / ``/styles.css`` static assets (whitelisted; no path traversal)
``GET  /api/summary``              store path, profile, counts, fan-out histogram, flags
``GET  /api/memories``             the de-duped memory list (browse + routing)
``GET  /api/probe?q=...&k=5``      routing decision + per-backend + engine results
``GET  /api/backend-artifact``     one Browse memory's stored artifact in one backend (+ copy path)
``POST /api/capture``             append a captured eval case to captured_cases.jsonl
``POST /api/reopen``              swap the active substrate to a new store dir (live, no restart)
``POST /api/pick-store``          open a native OS folder-picker and return the chosen dir
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:  # dual import: package (``-m router_ui``) and standalone (run-dir self-test)
    from .substrate import open_substrate
    from .picker import pick_directory, PickerUnavailable
except ImportError:  # pragma: no cover - import shim
    from substrate import open_substrate  # type: ignore
    from picker import pick_directory, PickerUnavailable  # type: ignore

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Whitelisted static assets -> (filename, content-type). No arbitrary path serving.
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class _State:
    """Holds the active substrate. ``POST /api/reopen`` rebinds ``.substrate``, so every
    future request sees the new store without restarting the server. The substrate is
    read-only, so swapping the reference is safe even with in-flight requests holding the
    old one."""

    def __init__(self, substrate: Any) -> None:
        self.substrate = substrate


class InspectorHandler(BaseHTTPRequestHandler):
    """Read-only inspector handler. The active substrate lives in ``state`` (a swappable
    :class:`_State` holder) bound per-server in :func:`build_server`, so ``POST /api/reopen``
    can change the store directory live, without a restart."""

    state: Any = None            # _State holding the active (swappable) Substrate
    server_version = "MemoryInspector/1.0"
    protocol_version = "HTTP/1.1"

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in _STATIC:
            return self._serve_static(path)
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

    def _capture(self, payload: dict) -> None:
        try:
            result = self.state.substrate.capture(payload)
        except ValueError as exc:
            return self._json({"error": str(exc)}, code=400)
        return self._json(result)

    def _reopen(self, payload: dict) -> None:
        """Swap the active substrate to a new store directory, live (no restart). Reuses the
        current profile + margin threshold unless ``profile`` is given. Returns the new
        store's summary so the client can refresh."""
        store = (payload.get("store") or "").strip()
        if not store:
            return self._json({"error": "store is required"}, code=400)
        if not Path(store).exists():
            return self._json({"error": f"store dir not found: {store}"}, code=400)
        current = self.state.substrate
        profile = (payload.get("profile") or current.profile or "auto").strip()
        try:
            new_sub = open_substrate(store, profile, margin_threshold=current.margin_threshold)
        except ValueError as exc:
            return self._json({"error": str(exc)}, code=400)
        self.state.substrate = new_sub
        return self._json(new_sub.summary())

    def _pick_store(self, payload: dict) -> None:
        """Open a native OS folder-picker (the inspector runs on the user's own machine) and
        return the chosen absolute path as ``{"store": dir}``, or ``{"cancelled": true}`` if the
        user dismissed the dialog. Does NOT reopen — the client posts the result to
        ``/api/reopen`` so the existing not-found / bad-store handling applies uniformly."""
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
        except BrokenPipeError:  # client navigated away mid-response; not our problem
            pass

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - matches base signature
        """Quiet by default (one line per request to stderr would spam the terminal log)."""
        return


def _int(value: str, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def build_server(host: str, port: int, substrate) -> ThreadingHTTPServer:
    """A ThreadingHTTPServer bound to ``(host, port)`` serving ``substrate`` (shared across
    requests) through a swappable :class:`_State` holder, so ``POST /api/reopen`` can change
    the store directory live without a restart."""
    handler = type("BoundInspectorHandler", (InspectorHandler,), {"state": _State(substrate)})
    return ThreadingHTTPServer((host, port), handler)


__all__ = ["build_server", "InspectorHandler"]
