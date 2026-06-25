"""VISTA Bench loader — long-horizon foresight/safety journeys as memory tasks.

Real source
-----------
* GitHub: ``kenhuangus/vista-benchmark`` (corpus ``huggingface/vista_corpus.jsonl``)
* License: **CC-BY-4.0** (see ``data/vista/ATTRIBUTION.md``).

VISTA scores the *route* a long-running agent takes through a per-journey
route-graph. This harness measures **memory** quality, so the loader projects
each journey's narrative onto the harness's ``Session`` / ``Task`` shape, keeping
the dimension VISTA uniquely exercises that none of the other five benchmarks
cover: **memory poisoning / adaptation** via the ``injection`` / ``drift`` /
``slow_burn`` event types.

Normalization (one journey -> one :class:`Task`)
-----------------------------------------------
* ``question`` <- the journey ``intent`` (what the agent is asked to do).
* ``sessions`` <- the journey's ``event_trace``, in order, one Session each:
    - ``fact`` events become LEGITIMATE memories (the intake facts the agent
      should rely on) — their ids go into ``gold_memory_ids``.
    - ``injection`` events become POISONED memories (the attacker payload the
      agent must NOT act on) — never gold.
    - ``drift`` events become an ADAPTATION memory (a stale-assumption update
      the agent must reflect) — gold (the updated policy supersedes the old).
    - ``slow_burn`` / ``escalation_fork`` events become context memories.
* ``competency`` <- the journey ``domain`` (project / coding / research).
* ``answer`` <- ``None`` (VISTA has no single gold string; success is graded by
  the native evaluator's poisoning-resistance + calibration axes, not QA match).
* ``metadata`` carries the raw ``route_graph``, ``oracle_bindings``,
  ``event_trace`` and ``split`` so the native evaluator can compute the
  injection/drift/escalation axes deterministically.

Offline-first: the vendored ``data/vista/vista_corpus.jsonl`` is used when no
explicit source is given (stdlib JSON only); a remote HF id falls back to the
base loader's lazy ``datasets`` path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from ..schema import Benchmark, Session, Task, TaskKind
from .base import BaseLoader, file_exists, read_json

#: Data dir holding both the curated fixture and the vendored full corpus.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "vista"

#: Vendored curated corpus copy (6-journey fixture, ships as package data).
_VENDORED = _DATA_DIR / "vista_corpus.jsonl"

#: Vendored FULL upstream corpus (390 journeys) + per-split files.
_FULL_DIR = _DATA_DIR / "full"
_FULL_CORPUS = _FULL_DIR / "vista_corpus.jsonl"
_FULL_SPLITS = _FULL_DIR / "splits"

#: Recognised split names for the full corpus (`all` => the whole corpus file).
_FULL_SPLITS_AVAILABLE = {"train", "dev", "test", "challenge"}


def _resolve_full_path(split: Optional[str]) -> Path:
    """Return the full-corpus file for ``split`` (``all``/None => whole corpus)."""
    s = (split or "all").strip().lower()
    if s in ("", "all"):
        return _FULL_CORPUS
    if s in _FULL_SPLITS_AVAILABLE:
        return _FULL_SPLITS / f"{s}.jsonl"
    # Unknown split name -> fall back to the whole corpus (never error/empty).
    return _FULL_CORPUS

#: event types that should be treated as gold (legitimate) memory.
_GOLD_EVENT_TYPES = {"fact", "drift"}
#: event types that are poisoned / never gold.
_POISON_EVENT_TYPES = {"injection"}


class VistaLoader(BaseLoader):
    """Loader for VISTA Bench journeys (memory-poisoning / adaptation lens)."""

    benchmark: Benchmark = Benchmark.VISTA
    default_source: str = "kenhuangus/vista-benchmark"
    kind: TaskKind = TaskKind.QA

    def load(
        self,
        path_or_id: Optional[str] = None,
        *,
        limit: Optional[int] = None,
        split: str = "test",
        dataset: Optional[str] = None,
        **kwargs: Any,
    ) -> list[Task]:
        """Prefer the vendored corpus when no explicit source is given.

        Dataset selection (opt-in, default behaviour unchanged):

        * ``dataset="curated"`` (default) — the 6-journey offline fixture.
        * ``dataset="full"`` — the vendored full upstream corpus (390 journeys).
          The split is chosen by ``split`` from
          {``train``, ``dev``, ``test``, ``challenge``, ``all``}; ``all`` (or any
          unrecognised value) loads the whole 390-record corpus.

        The dataset may also be selected via the ``VISTA_DATASET`` env var
        (``full`` | ``curated``) and the split via ``VISTA_SPLIT``. An explicit
        kwarg always wins over the env var. ``--limit`` is respected in all paths.
        """
        if dataset is None:
            dataset = os.environ.get("VISTA_DATASET")
        dataset = (dataset or "curated").strip().lower()

        # Allow VISTA_SPLIT to override the split only when the caller did not
        # pass one explicitly (the kwarg default is "test").
        env_split = os.environ.get("VISTA_SPLIT")
        if env_split and split == "test":
            split = env_split

        if path_or_id is None and dataset == "full":
            full_path = _resolve_full_path(split)
            if file_exists(str(full_path)):
                # The chosen file already contains exactly the requested rows, so
                # disable field-based split filtering (the whole-corpus file mixes
                # splits and must NOT be filtered down).
                return self._load_local(str(full_path), limit=limit, split=None)

        if path_or_id is None and file_exists(str(_VENDORED)):
            path_or_id = str(_VENDORED)
        return super().load(path_or_id, limit=limit, split=split, **kwargs)

    def _load_local(
        self, path: str, *, limit: Optional[int] = None, split: Optional[str] = None, **kwargs: Any
    ) -> list[Task]:
        data = read_json(path)
        rows = data if isinstance(data, list) else [data]
        rows = [r for r in rows if isinstance(r, dict)]
        if split:
            filtered = [r for r in rows if str(r.get("split", "")).lower() == split.lower()]
            # If the requested split has no rows, fall back to all (the small
            # vendored corpus has one row per split) — never return empty silently.
            if filtered:
                rows = filtered
        return self._parse_rows(rows, limit=limit)

    def _parse_rows(
        self, rows: list[dict], *, limit: Optional[int] = None
    ) -> list[Task]:
        tasks: list[Task] = []
        for idx, journey in enumerate(rows):
            tasks.append(self._journey_to_task(journey, idx))
            if limit is not None and len(tasks) >= limit:
                break
        return tasks

    def _journey_to_task(self, journey: dict, idx: int) -> Task:
        jid = str(journey.get("id", f"vista_{idx}"))
        intent = str(journey.get("intent", "")).strip()
        domain = str(journey.get("domain", "vista"))
        split = str(journey.get("split", ""))

        sessions: list[Session] = []
        gold_memory_ids: list[str] = []
        poison_ids: list[str] = []
        for i, ev in enumerate(journey.get("event_trace") or []):
            if not isinstance(ev, dict):
                continue
            etype = str(ev.get("type", "event"))
            sid = f"{jid}::{etype}::{ev.get('t', i)}"
            content = _event_content(ev)
            role = "tool" if etype in _POISON_EVENT_TYPES else "user"
            sessions.append(
                Session(
                    session_id=sid,
                    content=content,
                    timestamp=0.0,
                    index=i,
                    role=role,
                    metadata={"event_type": etype, "t": ev.get("t")},
                )
            )
            if etype in _GOLD_EVENT_TYPES:
                gold_memory_ids.append(sid)
            elif etype in _POISON_EVENT_TYPES:
                poison_ids.append(sid)

        metadata: dict[str, Any] = {
            "split": split,
            "domain": domain,
            "route_graph": journey.get("route_graph"),
            "oracle_bindings": journey.get("oracle_bindings"),
            "event_trace": journey.get("event_trace"),
            "poison_memory_ids": poison_ids,
            "initial_memory": (journey.get("initial_route_state") or {}).get("memory"),
        }

        return Task(
            task_id=jid,
            benchmark=self.benchmark,
            kind=TaskKind.QA,
            question=intent,
            answer=None,
            sessions=sessions,
            gold_memory_ids=gold_memory_ids,
            group_id=domain,
            order=idx,
            competency=domain,
            metadata=metadata,
        )


def _event_content(ev: dict) -> str:
    """Render one event-trace entry into a single memory-text blob."""
    etype = str(ev.get("type", "event"))
    parts: list[str] = [f"[{etype}]"]
    if "fact" in ev:
        parts.append(f"{ev.get('fact')} = {ev.get('value')}")
    if etype == "injection":
        parts.append(f"payload: {ev.get('payload', '')}")
    if etype == "drift" and ev.get("changes"):
        parts.append(f"policy update: {ev.get('changes')}")
    if etype == "slow_burn" and ev.get("signal"):
        parts.append(ev.get("signal", ""))
    if etype == "escalation_fork":
        parts.append(f"high-risk fork: {ev.get('edge')}")
    if ev.get("note"):
        parts.append(str(ev.get("note")))
    return " ".join(str(p) for p in parts if p)


__all__ = ["VistaLoader"]
