"""``python -m results_monitor`` — live operator dashboard for benchmark runs."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

try:
    from .server import build_server
except ImportError:  # pragma: no cover
    from server import build_server  # type: ignore


def _find_repo_root(start: Path) -> Path | None:
    for d in (start, *start.parents):
        if (d / "results").is_dir():
            return d
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m results_monitor",
        description="Live operator dashboard for in-flight benchmark runs.",
    )
    parser.add_argument("--results-root", metavar="DIR", default=None,
                        help="path to results/ (default: walk up from cwd to the first that has it).")
    parser.add_argument("--port", type=int, default=8770, help="port (default 8770).")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1).")
    parser.add_argument("--open", action="store_true", help="open the page in a browser.")
    args = parser.parse_args(argv)

    if args.results_root:
        results_root = Path(args.results_root)
    else:
        root = _find_repo_root(Path.cwd())
        if root is None:
            raise SystemExit(
                "no results/ directory found from cwd; pass --results-root DIR or run from the repo root."
            )
        results_root = root / "results"

    if not results_root.is_dir():
        raise SystemExit(f"results dir not found: {results_root}")

    server = build_server(args.host, args.port, results_root)
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"[monitor] results root : {results_root}")
    print(f"[monitor] serving on   : {url}")
    sys.stdout.flush()
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[monitor] shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
