"""``python -m router_ui`` — the inspector entry point.

Discovers a substrate (newest ``results/v*/_memory`` by default, or ``--store DIR``),
opens it read-only, and serves the inspector at ``http://127.0.0.1:<port>``. ``--seed``
builds a synthetic demo corpus first (refusing to write inside a real ``results/`` dir
unless ``--force``) and serves it.

Run from the repo root (the launcher wires PYTHONPATH + the venv):
      ./router_ui/run.sh
      ./router_ui/run.sh --seed --open
Or directly:  PYTHONPATH=. python -m router_ui   (needs `memeval` importable)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import webbrowser
from pathlib import Path

# Dual import: package context (``-m router_ui``) and standalone (this dir on
# sys.path, used by the run-dir self-test before promotion into the tree).
try:  # pragma: no cover - import shim
    from .substrate import open_substrate
    from .server import build_server
    from . import fixtures
except ImportError:  # pragma: no cover - import shim
    from substrate import open_substrate  # type: ignore
    from server import build_server        # type: ignore
    import fixtures                          # type: ignore


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from ``start`` to the first ancestor containing a ``results/`` directory."""
    for d in (start, *start.parents):
        if (d / "results").is_dir():
            return d
    return None


def discover_store(explicit: str | None) -> str:
    """Resolve the store dir: ``--store`` wins; else the newest ``results/v*/_memory``."""
    if explicit:
        return explicit
    root = _find_repo_root(Path.cwd())
    if root is None:
        raise SystemExit(
            "no results/ directory found from the cwd; pass --store DIR or --seed "
            "(run from inside the repo root, or use ./router_ui/run.sh)."
        )
    candidates = [p / "_memory" for p in sorted((root / "results").glob("v*"))
                  if (p / "_memory").is_dir()]
    if not candidates:
        raise SystemExit(
            f"no results/v*/_memory substrate under {root / 'results'}; pass --store DIR or --seed."
        )
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest)


def _guard_results(target: Path, force: bool) -> None:
    """Refuse to seed inside a real ``results/`` tree unless ``--force``."""
    if "results" in target.resolve().parts and not force:
        raise SystemExit(
            f"refusing to seed inside a results/ directory ({target}); this would write demo "
            "data into a real substrate. Pass --force to override, or choose another --store DIR."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m router_ui",
        description="Inspect plugin-saved memories and evaluate router effectiveness (local web UI).",
    )
    parser.add_argument("--store", metavar="DIR", default=None,
                        help="substrate dir (a .../_memory). Default: newest results/v*/_memory.")
    parser.add_argument("--port", type=int, default=8765, help="port to serve on (default 8765).")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1; local only).")
    parser.add_argument("--profile", choices=["speed", "fusion", "accuracy", "accuracy-local", "auto"],
                        default="auto",
                        help="routing profile (default auto = build_store's pick). accuracy-local "
                             "= local MiniLM + sqlite-vec ANN (needs the eval[local-ann] extra).")
    parser.add_argument("--margin-threshold", type=float, default=None,
                        help="ambiguity threshold in classifier-score units (default 1.0).")
    parser.add_argument("--seed", action="store_true", help="build the synthetic demo corpus, then serve it.")
    parser.add_argument("--force", action="store_true", help="allow --seed to write inside a results/ dir.")
    parser.add_argument("--open", action="store_true", help="open the page in a browser.")
    args = parser.parse_args(argv)

    if args.seed:
        target = Path(args.store) if args.store else Path(tempfile.mkdtemp(prefix="inspect-demo-")) / "_memory"
        _guard_results(target, args.force)
        manifest = fixtures.seed(str(target))
        print(f"[seed] wrote {manifest['total_written']} memories into {manifest['store_dir']}")
        print(f"[seed] anomalies: {', '.join(manifest['anomalies'])}")
        store_dir = manifest["store_dir"]
    else:
        store_dir = discover_store(args.store)

    kwargs = {}
    if args.margin_threshold is not None:
        kwargs["margin_threshold"] = args.margin_threshold
    substrate = open_substrate(store_dir, args.profile, **kwargs)
    for warning in substrate.warnings:
        print(f"[warn] {warning}", file=sys.stderr)

    server = build_server(args.host, args.port, substrate)
    url = f"http://{args.host}:{server.server_address[1]}"
    summary = substrate.summary()
    print(f"[inspect] store      : {summary['store_path']}")
    print(f"[inspect] profile    : {summary['profile']} ({summary['profile_source']})")
    print(f"[inspect] backends   : {summary['backend_status']}")
    print(f"[inspect] memories   : {summary['total_unique']} unique "
          f"({summary['flagged_count']} flagged, {summary['misroute_count']} mis-route)")
    print(f"[inspect] serving on : {url}")
    sys.stdout.flush()
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[inspect] shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
