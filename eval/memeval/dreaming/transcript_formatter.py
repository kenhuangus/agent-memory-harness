#!/usr/bin/env python3
"""Render a Claude Code .jsonl session transcript as a readable structured log.

Two entry points share the same per-line logic via :func:`_format_lines`:

- :func:`main` — CLI form. Invoke as
  ``transcript_formatter.py <file.jsonl> [max_chars|"full"|"0"]``;
  prints the structured rendering of the whole file to stdout.
- :func:`format_chunk` — library form. Called by
  :func:`memeval.dreaming.engine.daydream` on the cursor-delta string read
  from the live JSONL log, returns the structured rendering as a string.
  This is the noise-filtering pre-pass before redaction + LLM (ADR-dreaming-026).

The library form tolerates malformed JSONL lines silently (skips them, matching
the daydream engine's pre-PR tolerance for bad bytes — see ``engine.py:141``).
The CLI form preserves the original strict behavior of failing on bad JSON via
the same code path: a JSONDecodeError is silently skipped there too, which is
a behavior change vs the pre-refactor CLI but a non-issue in practice (a
malformed line printed nothing useful before either; now it just doesn't error).
"""
import json
import sys

# 0 (or negative) disables truncation entirely; overridable via argv[2] in CLI
# mode, or via the ``limit`` parameter when called as a library.
LIMIT = 600

def short(s, n=None):
    s = str(s).replace("\r\n", "\n")
    if LIMIT <= 0:
        return s
    n = LIMIT if n is None else min(n, LIMIT)
    if len(s) > n:
        return s[:n] + f"\n    … [truncated, {len(s)} chars total]"
    return s

def render_blocks(content):
    """content is either a str or a list of content blocks."""
    out = []
    if isinstance(content, str):
        out.append(short(content))
        return out
    if not isinstance(content, list):
        out.append(short(json.dumps(content)))
        return out
    for b in content:
        if not isinstance(b, dict):
            out.append(short(str(b)))
            continue
        bt = b.get("type")
        if bt == "text":
            out.append("[text] " + short(b.get("text", "")))
        elif bt == "thinking":
            out.append("[thinking] " + short(b.get("thinking", ""), 400))
        elif bt == "tool_use":
            inp = json.dumps(b.get("input", {}))
            out.append(f"[tool_use: {b.get('name')}] {short(inp, 400)}")
        elif bt == "tool_result":
            c = b.get("content", "")
            if isinstance(c, list):
                c = " ".join(
                    x.get("text", "") if isinstance(x, dict) else str(x) for x in c
                )
            err = " (is_error)" if b.get("is_error") else ""
            out.append(f"[tool_result{err}] " + short(c, 400))
        elif bt == "image":
            out.append("[image]")
        else:
            out.append(f"[{bt}] " + short(json.dumps(b), 300))
    return out

def _format_lines(lines_iter, limit=0):
    """Format an iterable of raw JSONL lines into structured text.

    Sets module-level ``LIMIT`` for the duration of the call (matches the
    parser's original CLI ``LIMIT`` pattern; safe under the daydream engine's
    process-per-invocation model and under the replay tool's sequential
    in-process calls). Restores the prior ``LIMIT`` value on exit via
    ``try/finally`` so a stray exception can't leak state to a subsequent
    call (defensive against future in-process concurrency). Tolerates
    per-line ``json.JSONDecodeError`` silently — matches the engine's
    tolerance for bad bytes in the raw log delta.
    """
    global LIMIT
    prior_limit = LIMIT
    LIMIT = limit
    turn = 0
    lines = []
    try:
        for raw in lines_iter:
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue  # tolerate malformed lines — matches engine's bad-byte tolerance
            t = d.get("type")
            ts = d.get("timestamp", "")[:19].replace("T", " ")

            if t == "queue-operation":
                lines.append(f"--- [{ts}] queue-operation: {d.get('operation')}")
                if d.get("content"):
                    lines.append("    " + short(d["content"], 300))
                continue
            if t == "attachment":
                lines.append(f"--- [{ts}] attachment")
                continue
            if t == "last-prompt":
                lines.append(f"--- [{ts}] last-prompt marker")
                continue
            if t == "system":
                lines.append(f"--- [{ts}] system: {short(d.get('content',''),300)}")
                continue

            msg = d.get("message")
            if msg is None:
                lines.append(f"--- [{ts}] {t} (no message)")
                continue
            role = msg.get("role", t)
            model = f" ({msg.get('model')})" if msg.get("model") else ""
            turn += 1
            lines.append("")
            lines.append(f"━━━ #{turn} [{ts}] {role.upper()}{model} ━━━")
            for piece in render_blocks(msg.get("content", "")):
                lines.append("  " + piece.replace("\n", "\n  "))

        return "\n".join(lines)
    finally:
        LIMIT = prior_limit


def format_chunk(jsonl_text: str, limit: int = 0) -> str:
    """Library entry point — format a JSONL chunk string into structured text.

    Used by :func:`memeval.dreaming.engine.daydream` on the cursor-delta read
    from the live log. The default ``limit=0`` means no truncation; pass a
    positive int to cap per-block length to that many chars. The
    daydream-engine call site reads this from the ``DREAM_PARSER_LIMIT``
    env var.

    Empty input → empty string (no enclosing newline). Behaviour preserves
    the engine's empty-chunk early-return semantics at ``engine.py:137``.
    """
    return _format_lines(jsonl_text.splitlines(), limit=limit)


def main(path):
    """CLI entry — print structured rendering of ``path`` to stdout."""
    turn = 0
    lines = []
    with open(path, encoding="utf-8") as f:
        rendered = _format_lines(f, limit=LIMIT)
    sys.stdout.write(rendered + "\n")

if __name__ == "__main__":
    # Usage: transcript_formatter.py <file.jsonl> [max_chars]   (0 or "full" = no truncation)
    if len(sys.argv) > 2:
        arg = sys.argv[2]
        LIMIT = 0 if arg.lower() in ("full", "0") else int(arg)
    main(sys.argv[1])
