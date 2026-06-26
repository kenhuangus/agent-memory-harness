"""``python -m ui`` — combined monitor + inspector entry point.

Hosts BOTH views under one origin (defaults: ``http://127.0.0.1:8770``). The
shell page swaps which view is visible via a top-bar toggle. Use ``--store``
to bootstrap the inspector with a substrate at startup (otherwise the user
picks one in-UI). ``--results-root`` points the monitor at a ``results/``
directory; if omitted, the launcher walks up from ``cwd`` to find one.

Run from the repo root (the launcher wires PYTHONPATH + the venv):
      ./ui/run.sh
      ./ui/run.sh --seed --open
Or directly:  PYTHONPATH=. python -m ui   (needs ``memeval`` importable)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shim
    from .substrate import open_substrate
    from .server import build_server
    from . import fixtures
except ImportError:  # pragma: no cover
    from substrate import open_substrate  # type: ignore
    from server import build_server        # type: ignore
    import fixtures                          # type: ignore


# Launcher's bound on the eager substrate open. Covers a cold Voyage-backed
# accuracy-profile open on a typical pipeline substrate; longer-running opens
# can still be triggered via the inspector's `Browse…` / Load buttons after
# the page has loaded (the in-UI flow has no timeout).
_OPEN_TIMEOUT_S = 30.0


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from ``start`` to the first ancestor containing a ``results/`` directory."""
    for d in (start, *start.parents):
        if (d / "results").is_dir():
            return d
    return None


def discover_store(explicit: str | None) -> str | None:
    """Resolve the inspector substrate at startup.

    ``explicit`` always wins. Otherwise, the newest ``results/v*/_memory``
    is auto-selected so the legacy ``make viewer`` behavior is preserved.
    Returns ``None`` only when neither is available — the inspector then
    starts empty and the user picks a store via the UI picker.
    """
    if explicit:
        return explicit
    root = _find_repo_root(Path.cwd())
    if root is None:
        return None
    candidates = [p / "_memory" for p in sorted((root / "results").glob("v*"))
                  if (p / "_memory").is_dir()]
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest)


def _resolve_results_root(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    root = _find_repo_root(Path.cwd())
    if root is None:
        return None
    return root / "results"


def _open_substrate_with_timeout(store_dir: str, profile: str, kwargs: dict, *, timeout_s: float) -> Any:
    """Open the substrate in a worker thread and return it if it lands within
    ``timeout_s``. Returns ``None`` on timeout or open failure — the inspector
    then starts empty and the user can pick another store via the UI."""
    import threading
    result: dict[str, Any] = {}

    def _open() -> None:
        try:
            sub = open_substrate(store_dir, profile, **kwargs)
            for warning in sub.warnings:
                print(f"[warn] {warning}", file=sys.stderr)
            result["substrate"] = sub
        except Exception as exc:  # noqa: BLE001 — fail-open is the point
            result["error"] = exc

    t = threading.Thread(target=_open, name="substrate-open", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return None  # still opening — daemon thread will keep going and exit with process
    err = result.get("error")
    if err is not None:
        print(f"[warn] could not open substrate ({err}); inspector will start empty.", file=sys.stderr)
        return None
    return result.get("substrate")


def _guard_results(target: Path, force: bool) -> None:
    """Refuse to seed inside a real ``results/`` tree unless ``--force``."""
    if "results" in target.resolve().parts and not force:
        raise SystemExit(
            f"refusing to seed inside a results/ directory ({target}); this would write demo "
            "data into a real substrate. Pass --force to override, or choose another --store DIR."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui",
        description="Cookbook memory UI — live operator dashboard + memory-store inspector.",
    )
    # Shared
    parser.add_argument("--port", type=int, default=8770, help="port to serve on (default 8770).")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1; local only).")
    parser.add_argument("--open", action="store_true", help="open the page in a browser.")
    # Monitor
    parser.add_argument("--results-root", metavar="DIR", default=None,
                        help="path to results/ (default: walk up from cwd to find it; monitor reports zero runs if unfound).")
    # Inspector
    parser.add_argument("--store", metavar="DIR", default=None,
                        help="substrate dir (a .../_memory) for the inspector. "
                             "Default: newest results/v*/_memory. Empty if no candidate.")
    parser.add_argument("--profile", choices=["speed", "fusion", "accuracy", "accuracy-local", "auto"],
                        default="auto",
                        help="routing profile (default auto = build_store's pick). accuracy-local "
                             "= local MiniLM + sqlite-vec ANN (needs the eval[local-ann] extra).")
    parser.add_argument("--margin-threshold", type=float, default=None,
                        help="ambiguity threshold in classifier-score units (default 1.0).")
    parser.add_argument("--seed", action="store_true",
                        help="build the synthetic demo corpus, then serve it as the inspector substrate.")
    parser.add_argument("--force", action="store_true", help="allow --seed to write inside a results/ dir.")
    args = parser.parse_args(argv)

    # Load the repo-root .env (VOYAGE_API_KEY, MEMEVAL_LOCAL_ANN, etc.) so profile
    # auto-selection sees the same keys without the user having to ``export``.
    # A shell-exported var still wins (override=False).
    from memeval.dotenv_loader import load_root_dotenv
    load_root_dotenv()

    # ---- substrate (inspector) --------------------------------------------
    substrate = None
    if args.seed:
        target = Path(args.store) if args.store else Path(tempfile.mkdtemp(prefix="inspect-demo-")) / "_memory"
        _guard_results(target, args.force)
        manifest = fixtures.seed(str(target))
        print(f"[seed] wrote {manifest['total_written']} memories into {manifest['store_dir']}")
        print(f"[seed] anomalies: {', '.join(manifest['anomalies'])}")
        substrate_path: str | None = manifest["store_dir"]
    else:
        substrate_path = discover_store(args.store)

    if substrate_path:
        kwargs = {}
        if args.margin_threshold is not None:
            kwargs["margin_threshold"] = args.margin_threshold
        # Bounded open. Two failure shapes this needs to cover:
        #   (a) the substrate's memory.db is locked by an in-flight bench writer
        #       (fails fast, no need to wait long), and
        #   (b) a cold Voyage-backed open on the accuracy profile, where the
        #       vector index rebuild + per-memory embedding lookups can take
        #       10-20s on a 50-100 memory store.
        # The previous 3s ceiling was tuned for (a) and silently failed (b),
        # leaving the inspector empty even though the store was healthy. 30s
        # comfortably covers (b) and still bounds a truly stuck open.
        substrate = _open_substrate_with_timeout(
            substrate_path, args.profile, kwargs, timeout_s=_OPEN_TIMEOUT_S,
        )
        if substrate is None:
            print(f"[warn] could not open substrate at {substrate_path} within "
                  f"{_OPEN_TIMEOUT_S:.0f}s; inspector will start empty (use the "
                  f"inspector picker to load it manually).", file=sys.stderr)

    # ---- results root (monitor) -------------------------------------------
    results_root = _resolve_results_root(args.results_root)

    # ---- serve -------------------------------------------------------------
    server = build_server(args.host, args.port, substrate, results_root)
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"[ui] serving on   : {url}")
    if substrate is not None:
        s = substrate.summary()
        print(f"[ui] inspector    : {s['store_path']}  profile={s['profile']} ({s['profile_source']})")
        print(f"[ui]               : memories={s['total_unique']} flagged={s['flagged_count']} mis-route={s['misroute_count']}")
    else:
        print("[ui] inspector    : (no substrate loaded — use the inspector picker)")
    if results_root and results_root.is_dir():
        print(f"[ui] monitor root : {results_root}")
    else:
        print("[ui] monitor root : (none found — monitor will show zero runs)")
    sys.stdout.flush()
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ui] shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
