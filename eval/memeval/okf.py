"""Open Knowledge Format (OKF) adapter — POC. Owner: Ken (eval-infra prototype).

OKF (Google Cloud, v0.1) represents knowledge as **a directory of markdown files
with YAML frontmatter**, cross-linked into a graph; the one required field is
``type``. Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf

Why this matters for our memory harness
---------------------------------------
OKF is a near-exact match for what a memory store *is* — curated, typed,
timestamped, tagged, cross-linked notes. This adapter makes OKF a first-class
**interchange format** for ``MemoryItem`` / ``MemoryStore``:

* **Portability / cross-harness sharing** — export a store to an OKF bundle and any
  OKF consumer (Google's Knowledge Catalog, the OKF visualizer, *another agent
  harness*) can read our memory; import a bundle others produced as memory. This
  is the concrete seam for the cross-harness memory-sharing work.
* **Brent's markdown backend, standardized** — ``stores/markdown_store.py`` is
  described as "memory as markdown + YAML frontmatter": that IS OKF. :class:`OKFStore`
  here is a working reference of exactly that, conformant to a published spec.
* **Dreaming/governance (Scott) get OKF's `log.md` + `index.md`** — chronological
  change history and progressive-disclosure indexes map onto our versioning
  (``MemoryItem.version``) and consolidation passes.

Round-trip is **lossless** for our items (native fields are preserved as ``x_``
frontmatter keys) and **graceful** for foreign bundles (no ``x_`` keys → derive a
``MemoryItem`` from ``type``/``tags``/``timestamp`` + body; capture OKF semantics
and outgoing links in ``metadata``).

Stdlib-only by default (a minimal frontmatter reader/writer); uses ``PyYAML`` when
installed for robust parsing of arbitrary external bundles.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .schema import MemoryItem, RetrievedItem

OKF_VERSION = "0.1"
_RESERVED = {"index.md", "log.md"}
_FM_DELIM = "---"
#: memeval-native MemoryItem fields carried as custom OKF keys (lossless round-trip).
_X = {
    "x_item_id": "item_id",
    "x_relevancy": "relevancy",
    "x_version": "version",
    "x_session_id": "session_id",
    "x_source": "source",
    "x_tokens": "tokens",
}
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")  # (anchor, target): anchor carries the relation verb


# --------------------------------------------------------------------------- #
# Time + slug helpers
# --------------------------------------------------------------------------- #
def _epoch_to_iso(ts: float) -> Optional[str]:
    if not ts or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _iso_to_epoch(s: Any) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    if not s:
        return 0.0
    txt = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _slug(s: str, *, default: str = "item") -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s).strip().lower()).strip("-")
    return out or default


# --------------------------------------------------------------------------- #
# Minimal YAML frontmatter (read/write); PyYAML used when available
# --------------------------------------------------------------------------- #
def _yaml_scalar(v: Any) -> str:
    """Emit a YAML scalar, quoting when needed (our controlled output)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{}&*!|>'\"%@`]", s) or s.strip() != s or s[:1] in "-?":
        return "'" + s.replace("'", "''") + "'"
    return s


def _dump_frontmatter(d: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, val in d.items():
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            if not val:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"- {_yaml_scalar(v)}" for v in val)
        else:
            lines.append(f"{key}: {_yaml_scalar(val)}")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse a YAML frontmatter block. Prefer PyYAML; fall back to a stdlib subset
    (key: value, block lists, indented continuation lines, simple quotes)."""
    try:  # robust path for arbitrary external bundles
        import yaml  # type: ignore
        out = yaml.safe_load(text)
        return out if isinstance(out, dict) else {}
    except Exception:
        pass
    data: dict[str, Any] = {}
    key: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and key is not None:  # block list item
            # A "key:" with an empty value seeds None; the first "- item" turns
            # it into a list (must replace the placeholder, not setdefault it).
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_unquote(line.lstrip()[2:].strip()))
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "[]":
                data[key] = []
            elif val == "":
                data[key] = None  # may become a block list (handled above) or stay empty
            else:
                data[key] = _unquote(val)
        elif key is not None and isinstance(data.get(key), str) and line.startswith((" ", "\t")):
            data[key] = (str(data[key]) + " " + line.strip()).strip()  # folded continuation
    return data


def _unquote(v: str) -> Any:
    s = v.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1].replace("''", "'")
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.startswith("[") and s.endswith("]"):  # inline flow list
        inner = s[1:-1].strip()
        return [_unquote(x) for x in inner.split(",")] if inner else []
    return s


def split_doc(text: str) -> tuple[dict[str, Any], str]:
    """Split an OKF markdown document into (frontmatter dict, body)."""
    if text.startswith(_FM_DELIM):
        end = text.find("\n" + _FM_DELIM, len(_FM_DELIM))
        if end != -1:
            fm = text[len(_FM_DELIM):end].strip("\n")
            body_start = end + len("\n" + _FM_DELIM)
            body = text[body_start:].lstrip("\n")
            return _parse_frontmatter(fm), body
    return {}, text


# --------------------------------------------------------------------------- #
# MemoryItem  <->  OKF concept document
# --------------------------------------------------------------------------- #
def memory_item_to_doc(item: MemoryItem) -> str:
    """Render a :class:`MemoryItem` as an OKF concept document (frontmatter+body)."""
    meta = dict(item.metadata or {})
    fm: dict[str, Any] = {
        "type": meta.pop("okf_type", None) or item.source or "Memory",
        "title": meta.pop("okf_title", None) or item.item_id,
        "description": meta.pop("okf_description", None) or _summary(item.content),
        "resource": meta.pop("okf_resource", None) or f"memeval://memory/{item.item_id}",
    }
    if item.tags:
        fm["tags"] = list(item.tags)
    iso = _epoch_to_iso(item.timestamp)
    if iso:
        fm["timestamp"] = iso
    # memeval-native fields as custom keys (spec: producers may add keys).
    fm["x_item_id"] = item.item_id
    fm["x_relevancy"] = float(item.relevancy)
    fm["x_version"] = int(item.version)
    if item.session_id:
        fm["x_session_id"] = item.session_id
    if item.source:
        fm["x_source"] = item.source
    if item.tokens:
        fm["x_tokens"] = int(item.tokens)
    meta.pop("okf_links", None)  # links live in the body, not re-emitted here
    if meta:
        fm["x_metadata_json"] = json.dumps(meta, sort_keys=True, default=str)
    return f"{_FM_DELIM}\n{_dump_frontmatter(fm)}\n{_FM_DELIM}\n\n{item.content.rstrip()}\n"


def doc_to_memory_item(text: str, *, fallback_id: str = "") -> MemoryItem:
    """Parse an OKF concept document into a :class:`MemoryItem`.

    Lossless for our own docs (``x_`` keys); graceful for foreign bundles —
    derives sensible defaults and records OKF semantics + outgoing links in
    ``metadata`` so nothing is silently dropped.
    """
    fm, body = split_doc(text)
    item_id = str(fm.get("x_item_id") or _id_from_resource(fm.get("resource")) or fallback_id or "okf-item")

    metadata: dict[str, Any] = {}
    raw_meta = fm.get("x_metadata_json")
    if raw_meta:
        try:
            metadata.update(json.loads(raw_meta))
        except Exception:
            pass
    # Preserve OKF semantics so a re-export reproduces them.
    for fld, mkey in (("type", "okf_type"), ("title", "okf_title"),
                      ("description", "okf_description"), ("resource", "okf_resource")):
        if fm.get(fld) is not None:
            metadata[mkey] = fm[fld]
    # Capture each link's ANCHOR text (the relation verb) alongside its target, as (anchor, target)
    # pairs. The graph store types the anchor via memeval.stores.relations (untyped anchors -> the
    # generic relates_to); okf.py stays a pure parser and does not classify relations here.
    links = [(anchor, tgt) for anchor, tgt in _LINK_RE.findall(body)
             if not tgt.startswith(("http://", "https://"))]
    if links:
        metadata["okf_links"] = links  # (anchor, target) typed directed edges -> graph store / dreaming

    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return MemoryItem(
        item_id=item_id,
        content=body.rstrip(),
        timestamp=_iso_to_epoch(fm.get("timestamp")),
        relevancy=float(fm.get("x_relevancy", 1.0)),
        session_id=fm.get("x_session_id"),
        source=fm.get("x_source") or fm.get("type") or "okf",
        tags=[str(t) for t in tags],
        tokens=int(fm.get("x_tokens", 0) or 0),
        version=int(fm.get("x_version", 1) or 1),
        metadata=metadata,
    )


def _summary(content: str, n: int = 140) -> str:
    s = " ".join((content or "").split())
    return (s[: n - 1] + "…") if len(s) > n else (s or "(empty)")


def _id_from_resource(resource: Any) -> Optional[str]:
    if not resource:
        return None
    tail = str(resource).rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def _doc_relpath(item: MemoryItem) -> str:
    """Bundle-relative path: <type-slug>/<id-slug>.md (the OKF directory layout)."""
    typ = (item.metadata or {}).get("okf_type") or item.source or "memory"
    return f"{_slug(typ, default='memory')}/{_slug(item.item_id)}.md"


# --------------------------------------------------------------------------- #
# Bundle export / import
# --------------------------------------------------------------------------- #
def export_bundle(items: Iterable[MemoryItem], out_dir: str | Path) -> dict[str, Any]:
    """Write ``items`` as a conformant OKF bundle under ``out_dir``.

    Lays out ``<type>/<id>.md`` concept docs, a per-type ``index.md`` (progressive
    disclosure), a root ``index.md`` carrying ``okf_version``, and a ``log.md``
    change history (ordered by item timestamp). Returns a small manifest.
    """
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    items = list(items)

    by_type: dict[str, list[tuple[str, MemoryItem]]] = {}
    for it in items:
        rel = _doc_relpath(it)
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(memory_item_to_doc(it), encoding="utf-8")
        by_type.setdefault(rel.split("/", 1)[0], []).append((rel, it))

    # per-type index.md
    for tdir, entries in by_type.items():
        lines = [f"# {tdir}", ""]
        for rel, it in sorted(entries):
            title = (it.metadata or {}).get("okf_title") or it.item_id
            lines.append(f"* [{title}](/{rel}) — {_summary(it.content, 100)}")
        (root / tdir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # root index.md (the only place okf_version is permitted)
    root_lines = [f"{_FM_DELIM}", f'okf_version: "{OKF_VERSION}"', f"{_FM_DELIM}", "",
                  "# Subdirectories", ""]
    for tdir in sorted(by_type):
        root_lines.append(f"* [{tdir}](/{tdir}/index.md) — {len(by_type[tdir])} concept(s)")
    (root / "index.md").write_text("\n".join(root_lines) + "\n", encoding="utf-8")

    # log.md — chronological change history (newest first)
    dated = sorted(items, key=lambda x: x.timestamp or 0, reverse=True)
    log = ["# Log", ""]
    for it in dated:
        iso = _epoch_to_iso(it.timestamp) or "(undated)"
        rel = _doc_relpath(it)
        log.append(f"* {iso} — **v{it.version}** [{it.item_id}](/{rel})")
    (root / "log.md").write_text("\n".join(log) + "\n", encoding="utf-8")

    return {
        "okf_version": OKF_VERSION,
        "root": str(root),
        "n_concepts": len(items),
        "types": {t: len(v) for t, v in by_type.items()},
    }


def import_bundle(in_dir: str | Path) -> list[MemoryItem]:
    """Parse every non-reserved ``.md`` concept doc in an OKF bundle into items.

    Works on bundles we produced and on foreign bundles (Google's samples, another
    harness). Reserved files (``index.md``/``log.md``) are skipped.
    """
    root = Path(in_dir)
    items: list[MemoryItem] = []
    for path in sorted(root.rglob("*.md")):
        if path.name in _RESERVED:
            continue
        text = path.read_text(encoding="utf-8")
        fm, _ = split_doc(text)
        if not fm.get("type"):
            continue  # not a conformant concept doc
        rel = path.relative_to(root).as_posix()
        items.append(doc_to_memory_item(text, fallback_id=_slug(rel[:-3])))
    return items


def validate_bundle(in_dir: str | Path) -> list[str]:
    """Return a list of OKF-conformance problems (empty == conformant).

    Checks the hard rules: every non-reserved .md has parseable frontmatter with a
    non-empty ``type``. Soft guidance (missing optional fields, unknown keys,
    broken links) is intentionally *not* flagged, per the spec.
    """
    root = Path(in_dir)
    problems: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if path.name in _RESERVED:
            continue
        fm, _ = split_doc(path.read_text(encoding="utf-8"))
        if not fm:
            problems.append(f"{path}: no parseable YAML frontmatter")
        elif not str(fm.get("type", "")).strip():
            problems.append(f"{path}: missing required 'type' field")
    return problems


# --------------------------------------------------------------------------- #
# OKFStore — a MemoryStore backed by an OKF bundle on disk
# --------------------------------------------------------------------------- #
class OKFStore:
    """A :class:`~memeval.protocols.MemoryStore` whose persistence IS an OKF bundle.

    ``write`` persists each item as an OKF concept doc; ``all`` reads the bundle;
    ``search`` reuses the reference token-overlap ranking. This is the working
    version of Brent's markdown backend, conformant to the OKF spec — and it means
    a run's memory is, on disk, a portable bundle any OKF consumer can read.
    """

    def __init__(self, path: str | Path, *, autoload: bool = True) -> None:
        from .harness import InMemoryStore  # local import: avoid cycle at module load
        self.root = Path(path)
        self._mem = InMemoryStore()
        if autoload and self.root.exists():
            for it in import_bundle(self.root):
                self._mem.write(it)

    def write(self, item: MemoryItem) -> None:
        self._mem.write(item)
        rel = _doc_relpath(item)
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(memory_item_to_doc(item), encoding="utf-8")

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self._mem.get(item_id)

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kw: Any) -> list[RetrievedItem]:
        return self._mem.search(query, k=k, as_of=as_of, **kw)

    def all(self) -> list[MemoryItem]:
        return self._mem.all()

    def delete(self, item_id: str) -> bool:
        """Remove ``item_id`` from the bundle (its on-disk doc(s) + the in-memory index). Idempotent.

        Unlinks EVERY concept doc that PARSES to ``item_id`` — the canonical path :meth:`write` uses AND any
        noncanonical filename from a foreign imported bundle — so a fresh autoload (``import_bundle``, which
        scans ``*.md`` and skips the reserved index.md/log.md) cannot resurrect it. :class:`InMemoryStore`
        has no delete (the frozen reference store), so the in-memory view is rebuilt from the survivors via
        the public API. Returns ``False`` if the id was not present.
        """
        from .harness import InMemoryStore  # local import: avoid cycle at module load (as in __init__)
        if self._mem.get(item_id) is None:
            return False
        if self.root.exists():
            for path in self.root.rglob("*.md"):  # canonical AND foreign filenames both parse to an id
                if path.name in _RESERVED:
                    continue
                text = path.read_text(encoding="utf-8")
                fm, _ = split_doc(text)
                if not fm.get("type"):
                    continue
                rel = path.relative_to(self.root).as_posix()
                if doc_to_memory_item(text, fallback_id=_slug(rel[:-3])).item_id == item_id:
                    path.unlink()
        survivors = [it for it in self._mem.all() if it.item_id != item_id]
        self._mem = InMemoryStore()
        for it in survivors:
            self._mem.write(it)
        return True

    def flush_indexes(self) -> dict[str, Any]:
        """(Re)write the bundle's index.md/log.md from the current items."""
        return export_bundle(self._mem.all(), self.root)


__all__ = [
    "OKF_VERSION",
    "memory_item_to_doc",
    "doc_to_memory_item",
    "split_doc",
    "export_bundle",
    "import_bundle",
    "validate_bundle",
    "OKFStore",
]
