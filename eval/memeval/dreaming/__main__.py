"""Package ``python -m memeval.dreaming`` entry point.

Mirrors the ``__main__`` guard in :mod:`memeval.dreaming.cli` so both
``python -m memeval.dreaming`` and ``python -m memeval.dreaming.cli``
(the form the Claude Code plugin's Stop/PreCompact hooks invoke via
``hooks_handler._daydream_command``) execute :func:`memeval.dreaming.cli.main`
rather than importing-and-exiting as a silent no-op.
"""

from __future__ import annotations

from memeval.dreaming.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
