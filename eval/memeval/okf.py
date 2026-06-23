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
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .schema import MemoryItem, RetrievedItem

# ``fcntl`` is POSIX-only. Import lazily-guarded so the module still loads (and the store
# still works single-process, just without the cross-process advisory lock) on a non-POSIX
# platform. ADR-013/014: the atomic-write + flock primitives ported here mirror
# ``dreaming/_state.py`` — a port, not an invention.
try:  # pragma: no cover - exercised on POSIX; the except is the non-POSIX fallback
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX (Windows) has no fcntl
    _fcntl = None  # type: ignore[assignment]

_logger = logging.getLogger(__name__)

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
# Durable-write primitives (ported from dreaming/_state.py — ADR-013/014)
# --------------------------------------------------------------------------- #
def _write_text_atomic(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` via ``tmp.replace(path)`` (ADR-013).

    Writes to a unique temp sibling (``<name>.<pid>.tmp`` so concurrent writers don't
    collide on one tmp name), ``fsync``s the file descriptor, ``os.replace``s it over the
    destination (atomic on the same filesystem), then ``fsync``s the parent directory so
    the rename itself survives power loss. A crash anywhere before the replace leaves the
    PRIOR file intact — the destination is never opened in ``"w"`` mode directly. Stdlib-only.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic same-fs rename: a crash leaves the prior doc intact
    except BaseException:
        # Best-effort cleanup so a failed write never strands a partial *.tmp.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # fsync the parent directory so the rename (the new dirent) is durable on power loss.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:  # directory fsync unsupported on some filesystems — the rename still landed
        pass


@contextmanager
def _bundle_write_lock(root: Path) -> Iterator[None]:
    """Hold a blocking exclusive advisory lock for the duration of a bundle persist.

    ``fcntl.flock`` (POSIX advisory, fd-bound, releases on process death — pinned over
    ``lockf`` per ADR-014) on a per-bundle ``.okf.lock`` file so concurrent OS processes
    (the MCP recall path and the Daydreamer) serialize their write/delete persists over
    one shared ``$MEMORY_STORE`` and never interleave a tmp-write+replace. ``LOCK_EX``
    (blocking) — a peer waits rather than clobbering. On a non-POSIX platform (``fcntl``
    absent) this is a no-op: the store still works single-process.
    """
    if _fcntl is None:  # non-POSIX: no advisory lock available; single-process still safe
        yield
        return
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".okf.lock"
    fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            except OSError as exc:
                _logger.warning("OKF flock LOCK_UN failed for %s: %s", root, exc)
    finally:
        try:
            os.close(fd)
        except OSError as exc:
            _logger.warning("OKF lock fd close failed for %s: %s", root, exc)


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
        _write_text_atomic(root / rel, memory_item_to_doc(it))
        by_type.setdefault(rel.split("/", 1)[0], []).append((rel, it))

    # per-type index.md
    for tdir, entries in by_type.items():
        lines = [f"# {tdir}", ""]
        for rel, it in sorted(entries):
            title = (it.metadata or {}).get("okf_title") or it.item_id
            lines.append(f"* [{title}](/{rel}) — {_summary(it.content, 100)}")
        _write_text_atomic(root / tdir / "index.md", "\n".join(lines) + "\n")

    # root index.md (the only place okf_version is permitted)
    root_lines = [f"{_FM_DELIM}", f'okf_version: "{OKF_VERSION}"', f"{_FM_DELIM}", "",
                  "# Subdirectories", ""]
    for tdir in sorted(by_type):
        root_lines.append(f"* [{tdir}](/{tdir}/index.md) — {len(by_type[tdir])} concept(s)")
    _write_text_atomic(root / "index.md", "\n".join(root_lines) + "\n")

    # log.md — chronological change history (newest first)
    dated = sorted(items, key=lambda x: x.timestamp or 0, reverse=True)
    log = ["# Log", ""]
    for it in dated:
        iso = _epoch_to_iso(it.timestamp) or "(undated)"
        rel = _doc_relpath(it)
        log.append(f"* {iso} — **v{it.version}** [{it.item_id}](/{rel})")
    _write_text_atomic(root / "log.md", "\n".join(log) + "\n")

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
        # Guard the per-file read/parse: one undecodable/torn .md (a half-written file
        # from a crashed peer, a foreign binary) must be SKIPPED (warn + continue), not
        # raise out and brick the whole store at construction (which would take the MCP
        # server + Daydreamer down). Pairs with the atomic-write fix (removes most torn
        # files at the source). ``errors='replace'`` keeps a partially-readable doc usable.
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            fm, _ = split_doc(text)
        except OSError as exc:
            _logger.warning("OKF import: skipping unreadable doc %s: %s", path, exc)
            continue
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
        self._autoload = autoload
        # Persisted monotonic GENERATION counter (the cross-process coherence seam). A
        # generation file ($BUNDLE/.okf-generation) is bumped UNDER the bundle lock on every
        # write/delete by ANY process. Each instance records the generation it has actually
        # loaded; a read whose on-disk generation EXCEEDS the loaded one reloads. This is
        # immune to mtime granularity (two writes in one tick get distinct integers) and to
        # the mtime-after-lock race (the loaded generation is set under the lock, so an
        # instance never "acknowledges" a peer commit it did not load). 0 == never loaded.
        self._loaded_generation = 0
        if autoload and self.root.exists():
            self._load_from_disk()

    # -- generation seam (cross-process coherence) -------------------------
    @property
    def _generation_path(self) -> Path:
        return self.root / ".okf-generation"

    def _read_generation(self) -> int:
        """Read the on-disk generation counter (cheap). 0 if absent/unreadable."""
        try:
            return int(self._generation_path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return 0

    def _bump_generation(self) -> int:
        """Increment + persist the generation counter and return the new value.

        MUST be called while holding the bundle lock (callers do) so the read-modify-write
        is serialized across processes — two concurrent writers can't collide on the same
        next value. Atomic-write so a crash never leaves a torn counter."""
        nxt = self._read_generation() + 1
        _write_text_atomic(self._generation_path, str(nxt))
        return nxt

    def _load_from_disk(self) -> None:
        """Rebuild the in-memory mirror from the on-disk bundle (a cold autoload).

        Snapshots the generation BEFORE reading the docs so a concurrent peer write that
        lands mid-read advances the counter past our snapshot and the NEXT refresh reloads
        again (never records a generation newer than the corpus we actually read)."""
        from .harness import InMemoryStore
        gen = self._read_generation()
        mem = InMemoryStore()
        if self.root.exists():
            for it in import_bundle(self.root):
                mem.write(it)
        self._mem = mem
        self._loaded_generation = gen

    def reload(self) -> None:
        """Explicitly re-read the bundle from disk so a long-lived reader (e.g. the MCP
        daemon) reflects peers' committed writes — defeats the frozen-at-``__init__``
        RAM mirror that would staleness-serve a peer's (Daydreamer's) memories until
        restart. Idempotent; safe to call before any read."""
        self._load_from_disk()

    def _maybe_refresh(self) -> bool:
        """Reload if a peer advanced the generation past what this instance loaded.

        Returns ``True`` iff a reload happened (so a caller — e.g. MarkdownStore — can
        rebuild a derived index in lockstep). Single-instance behavior is unchanged: this
        instance's own write/delete bumps the generation AND records it under the lock, so a
        self-write never triggers a reload and reads stay byte-equivalent. Only a *peer*
        process's commit (a higher generation) forces a refresh."""
        if not self._autoload:
            return False
        if self._read_generation() > self._loaded_generation:
            self._load_from_disk()
            return True
        return False

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        """Persist ``item`` as an OKF concept doc — atomic (tmp+replace+fsync, ADR-013)
        and serialized across processes by a bundle flock (ADR-014) so concurrent peers
        over one ``$MEMORY_STORE`` never interleave a write or torn-write a doc.

        ``_mem.write`` runs FIRST (it populates ``item.tokens`` in place when zero) so the
        rendered doc carries ``x_tokens`` — byte-equivalent to the pre-hardening single-process
        path. The generation is bumped AND recorded WHILE HOLDING THE LOCK, so this instance
        only ever acknowledges generations it has actually loaded (a peer's later commit gets
        a strictly higher generation and is detected on the next refresh)."""
        self._mem.write(item)  # populates item.tokens in place; the doc render below captures it
        rel = _doc_relpath(item)
        with _bundle_write_lock(self.root):
            _write_text_atomic(self.root / rel, memory_item_to_doc(item))
            self._loaded_generation = self._bump_generation()  # under the lock -> no stale-ack race

    def get(self, item_id: str) -> Optional[MemoryItem]:
        self._maybe_refresh()
        return self._mem.get(item_id)

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kw: Any) -> list[RetrievedItem]:
        self._maybe_refresh()
        return self._mem.search(query, k=k, as_of=as_of, **kw)

    def all(self) -> list[MemoryItem]:
        self._maybe_refresh()
        return self._mem.all()

    def delete(self, item_id: str) -> bool:
        """Remove ``item_id`` from the bundle (its on-disk doc(s) + the in-memory index). Idempotent.

        Fast path: the live item's deterministic canonical doc (``_doc_relpath``) is unlinked in O(1) —
        no full bundle scan. The O(N) rglob scan is the FALLBACK, taken only when the canonical doc is
        absent (a foreign-imported filename that does not match the slug layout), so correctness for the
        foreign case is preserved (every doc parsing to the id is unlinked) while the common canonical
        delete no longer goes quadratic under a Daydreamer dedup burst. Serialized + atomic via the same
        bundle flock as :meth:`write`. Returns ``False`` if the id was not present.
        """
        item = self._mem.get(item_id)
        if item is None:
            return False
        with _bundle_write_lock(self.root):
            canonical = self.root / _doc_relpath(item)
            unlinked = False
            if canonical.exists():
                # Verify the canonical file actually holds THIS id before fast-unlinking
                # (a slug collision could put a different id at this path — fall through to
                # the scan rather than delete a stranger's doc).
                if self._doc_is_item(canonical, item_id):
                    canonical.unlink(missing_ok=True)
                    unlinked = True
            if not unlinked and self.root.exists():
                # FALLBACK: foreign-imported filename(s). Scan + unlink every doc parsing to the id.
                for path in self.root.rglob("*.md"):  # canonical AND foreign filenames both parse to an id
                    if path.name in _RESERVED:
                        continue
                    if self._doc_is_item(path, item_id):
                        path.unlink(missing_ok=True)
            self._loaded_generation = self._bump_generation()  # under the lock (cf. write())
        return self._mem.delete(item_id)

    def _doc_is_item(self, path: Path, item_id: str) -> bool:
        """True if the concept doc at ``path`` parses to ``item_id`` (guards torn/foreign docs).

        Uses the SAME ``fallback_id`` as :func:`import_bundle` (the root-relative slug) so a
        foreign doc lacking ``x_item_id`` resolves to the same id on disk as it did in RAM —
        the scan can't miss a doc the autoload included.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        fm, _ = split_doc(text)
        if not fm.get("type"):
            return False
        try:
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            rel = path.name
        return doc_to_memory_item(text, fallback_id=_slug(rel[:-3])).item_id == item_id

    def flush_indexes(self) -> dict[str, Any]:
        """(Re)write the bundle's index.md/log.md from the current items."""
        result = export_bundle(self._mem.all(), self.root)
        with _bundle_write_lock(self.root):
            self._loaded_generation = self._bump_generation()
        return result


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
