"""FP/FN audit-file writer for Daydream redaction (ADR-dreaming-011 §Decision §3).

The audit file contains pre-redaction (potentially secret-bearing) text. It
is **local-only**: never read by any LLM, never transmitted, never logged
remotely. The caller supplies the destination path; resolution per
ADR-harness-004 is a PR2/PR3 wiring concern.

The eval driver (Ken's lane) reads these files to compute FP/FN rates over
sample sessions.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

__all__ = ["audit_path_for", "write_audit_record"]


def audit_path_for(basedir: str | Path, session_id: str) -> Path:
    """Compose the audit-file path for one session.

    ``<basedir>/dream/<session_id>.redact-audit.jsonl`` per ADR-011
    §Consequences. Full ``${MEMORY_STORE%/*}`` env-var resolution is the
    caller's concern (PR2/PR3 wiring); this helper only composes the suffix.
    """
    return Path(basedir) / "dream" / f"{session_id}.redact-audit.jsonl"


def write_audit_record(
    path: str | Path,
    *,
    chunk_id: int,
    pre: str,
    post: str,
    detected: Mapping[str, int],
    ts: float | None = None,
) -> None:
    """Append one JSONL audit record to ``path`` (creates parent dir on demand).

    Shape (per ADR-011 §3)::

        {"ts": <unix>, "chunk_id": <int>, "pre": <raw>, "post": <redacted>,
         "detected": {"AWSKey": 1, "AnthropicKey": 0, ...}}

    Append-only. The writer never reads existing content, never connects to
    the network, never opens files outside the supplied path. Caller passes
    the resolved absolute path.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": ts if ts is not None else time.time(),
        "chunk_id": int(chunk_id),
        "pre": pre,
        "post": post,
        "detected": {str(k): int(v) for k, v in detected.items()},
    }
    with open(target, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
