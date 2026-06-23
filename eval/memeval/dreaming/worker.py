"""Dreaming worker — Jobs 1 (dedup) + 4 (TTL pruning) + 2 (contradiction).

Layered job-by-job per ADR-002:
- **Job 1 (dedup) detection-only** shipped in PR #88: walk ``store.all()``,
  group items by stdlib-normalized content, return a JSON summary dict.
- **Job 1 mutation** shipped in PR #98 per ADR-021: under a basedir
  ``flock``, retire each cluster's losers via ``self.store.delete()`` (frozen
  protocol per PR #99).
- **Job 4 (TTL pruning) detection + mutation** shipped in PR #103: before
  clustering, drop items whose ``(now - item.timestamp) > retention_seconds``
  using the SAME basedir lock and the SAME ``self.store.delete()`` primitive.
- **Job 2 (contradiction, LLM-driven)** ships in this PR per
  ``JOB2_CONTRADICTION_RUBRIC.md`` + ADR-021 §Open-items closure: after
  dedup-deletes, batch-send post-TTL/post-dedup survivors to an
  ``LLMClient`` (default ``OpenRouterClient``); LLM identifies contradicting
  pairs; the worker deterministically picks the loser per Job 1 §D5a/D5b
  rule and retires via ``self.store.delete()``. Hard caps via
  ``DREAM_CONTRADICTION_MAX_CALLS`` (default 20). Fail-open per ADR-012.

Mutation contract is hard-delete, no CAS, no winner-write-back. The
Daydream-vs-Dream race (Shape 2) is closed by ``engine.daydream`` acquiring
the same basedir lock before the per-session lock (ADR-021 Decision 4).

Rubrics:
- ``eval/memeval/dreaming/tests/JOB1_MUTATION_RUBRIC.md`` (dedup half)
- ``eval/memeval/dreaming/tests/JOB4_TTL_RUBRIC.md`` (TTL half)
- ``eval/memeval/dreaming/tests/JOB2_CONTRADICTION_RUBRIC.md`` (contradiction)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import string
import time
from pathlib import Path
from typing import Any, NamedTuple

from ..protocols import MemoryStore
from ..schema import MemoryItem
from ._state import (
    _DreamLockHeld,
    _UnsupportedFsError,
    _basedir_dream_lock,
    _is_network_fs,
)
from .events import emit

log = logging.getLogger(__name__)

_PUNCT_TRANSLATION = str.maketrans("", "", string.punctuation)
_WHITESPACE_RUN = re.compile(r"\s+")
_SECONDS_PER_DAY: int = 86400
_DEFAULT_ITEM_RETENTION_DAYS: int = 30

# Job 2 contradiction constants.
_SECONDS_PER_HOUR: int = 3600
_DEFAULT_CONTRADICTION_MAX_CALLS: int = 20
_CONTRADICTION_BATCH_SIZE: int = 10
_CONTRADICTION_MAX_TOKENS: int = 1024
_RATIONALE_MAX_LEN: int = 200


def _now() -> float:
    """Module-level seam for ``time.time()`` — monkeypatchable in tests (JOB4 §J-TTL-1)."""
    return time.time()


def _make_llm_client() -> Any:
    """Production ``LLMClient`` constructor. Test seam — monkeypatch this in
    unit tests to return a stub or queue-driven client.

    Mirrors the ``_now()`` pattern. Lazy-imports ``make_client`` from ``.llm``
    so ``worker.py`` keeps zero top-level third-party imports
    (architecture.md §3; rubric §J-J2-2).
    """
    from .llm import make_client
    return make_client()


def _session_id_for_dream(basedir: Path) -> str:
    """Stable per-basedir token used as the contradiction-batch nonce-derivation seed.

    Dream has no per-session concept (Daydream-only; ADR-011 audit-file is
    Daydream-scoped). This shim provides a deterministic 16-hex-char token
    derived from the basedir so the envelope-wrap nonce derivation has the
    same SHAPE as Daydream's session_id (`_extract.py:73`).

    Pinned by rubric §G-J2-session-id.
    """
    return hashlib.sha256(str(basedir).encode("utf-8")).hexdigest()[:16]


def _read_item_retention_days() -> int:
    """Resolve ``$DREAM_ITEM_RETENTION_DAYS`` to an int days value.

    Per JOB4 Open-contracts pin #4/#9/#10:
    - Unset → default 30 days (pin #5).
    - ``"0"`` → 0 (treated as DISABLED by caller; not a magic prune-everything).
    - Negative or non-integer → 30-day default with a warning log.
    """
    raw = os.environ.get("DREAM_ITEM_RETENTION_DAYS")
    if raw is None or raw == "":
        return _DEFAULT_ITEM_RETENTION_DAYS
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "DREAM_ITEM_RETENTION_DAYS=%r is not an integer; falling back to %d-day default",
            raw, _DEFAULT_ITEM_RETENTION_DAYS,
        )
        return _DEFAULT_ITEM_RETENTION_DAYS
    if value < 0:
        log.warning(
            "DREAM_ITEM_RETENTION_DAYS=%d is negative; falling back to %d-day default",
            value, _DEFAULT_ITEM_RETENTION_DAYS,
        )
        return _DEFAULT_ITEM_RETENTION_DAYS
    return value


def _read_contradiction_max_calls() -> int:
    """Resolve ``$DREAM_CONTRADICTION_MAX_CALLS`` to an int.

    Per JOB2 Open-contracts pin #4/#6/#7:
    - Unset → default 20.
    - ``"0"`` → 0 (treated as DISABLED — no LLM call at all). Mirrors
      Job 4 §H-TTL-2 footgun-protection symmetry.
    - Negative → clamped to 0 (disable).
    - Non-integer → 20 default with a warning log.
    """
    raw = os.environ.get("DREAM_CONTRADICTION_MAX_CALLS")
    if raw is None or raw == "":
        return _DEFAULT_CONTRADICTION_MAX_CALLS
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "DREAM_CONTRADICTION_MAX_CALLS=%r is not an integer; falling back to %d default",
            raw, _DEFAULT_CONTRADICTION_MAX_CALLS,
        )
        return _DEFAULT_CONTRADICTION_MAX_CALLS
    return max(0, value)


def _pick_pruned(items: list[MemoryItem], now: float, retention_seconds: int) -> list[str]:
    """Return the lex-sorted item_ids whose age strictly exceeds retention.

    JOB4 §F-TTL-3 (strictly greater) + §B13 (sorted ascending in the dict).
    """
    pruned = [item.item_id for item in items if (now - item.timestamp) > retention_seconds]
    return sorted(pruned)


def _normalize(content: Any) -> str:
    """Lowercase + strip ASCII punctuation + collapse whitespace; ``None`` → ``""``."""
    if content is None:
        text = ""
    else:
        text = str(content)
    text = text.lower().translate(_PUNCT_TRANSLATION)
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _resolve_basedir() -> Path:
    """Per rubric preamble pin #4: ``Path($MEMORY_STORE)`` when set, else CWD fallback."""
    raw = os.environ.get("MEMORY_STORE")
    if raw:
        return Path(raw)
    return Path.cwd()


def _pick_winner(items: list[MemoryItem]) -> str:
    """Latest ``item.timestamp`` wins; ties broken by lexicographically lowest ``item_id``.

    Pinned by rubric preamble Open-contracts pin #3 + Job 1 §D5a/D5b + Job 2
    §D-J2-1/D-J2-2 (same rule applied to contradicting pairs).
    """
    return sorted(items, key=lambda i: (-i.timestamp, i.item_id))[0].item_id


# ── Job 2 contradiction-resolution data shapes + helpers ───────────────────


class ContradictionPair(NamedTuple):
    """One LLM-identified contradiction with deterministically-chosen loser.

    NamedTuple (not dataclass) to avoid a ``dataclasses`` import — keeps
    ``worker.py``'s allow-list at stdlib only (rubric §J-J2-2).
    """

    loser_id: str
    winner_id: str
    rationale: str


class ContradictionResult(NamedTuple):
    """Output of the contradiction pass — pairs + cost metrics."""

    pairs: list[ContradictionPair]
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    pairs_examined_estimate: int


def _wrap_batch_in_envelope(payload: str, *, session_id: str, now: float, batch_idx: int) -> Any:
    """Wrap a batch JSON payload in the same nonce-tagged transcript envelope
    Daydream's ``_extract._wrap_user_content_in_envelope`` uses.

    Per rubric §J-J2-envelope-named: this is the SECOND named envelope-format
    call site in the dreaming module; the ``test_extract.py`` audit is
    updated to allow both by NAME.

    Nonce length pinned at 8 hex chars (matches Daydream — rubric
    §G-J2-nonce-length). Returns ``RedactedText`` (lazy import keeps
    top-level imports clean per architecture.md §3).
    """
    from .prompts import _ENVELOPE_TEMPLATE
    from .redaction import RedactedText
    nonce_seed = f"{session_id}|{now}|{batch_idx}"
    nonce = hashlib.sha256(nonce_seed.encode("utf-8")).hexdigest()[:8]
    wrapped = _ENVELOPE_TEMPLATE.format(nonce=nonce, redacted=payload)
    return RedactedText(wrapped)


def _detect_contradictions(
    items: list[MemoryItem],
    client: Any,
    *,
    batch_size: int,
    max_calls: int,
    model: str,
    session_id: str,
    now: float,
    protected_ids: set[str] | None = None,
) -> ContradictionResult:
    """LLM-driven contradiction pass over the post-TTL/post-dedup working set.

    See ``JOB2_CONTRADICTION_RUBRIC.md`` for the full criterion list.
    Algorithm:

    1. Empty items OR ``max_calls <= 0`` → return empty result. NO event
       emitted (max_calls=0 is the disabled-pass case, Open-contracts pin #6).
    2. Deterministic shuffle of the working set, seeded by
       ``sha256(session_id || hour_bucket)[:16]``. Coverage varies across
       hours, reproducible within an hour (halliday observation O2).
    3. NON-OVERLAPPING window: each item appears in at most one batch per run.
    4. Per batch: redact each item value (``content``, every ``tag``,
       ``item_id``), JSON-serialize, wrap in nonce envelope, call
       ``client.complete()``.
    5. Empty completion OR exception → emit
       ``dream.contradiction_skipped_unavailable_llm`` with ``batch_index``,
       continue (ADR-012 inheritance, extended to exception per Pushback H).
    6. Parse JSON. On ``JSONDecodeError`` or missing ``"pairs"`` key, emit
       ``dream.contradiction_batch_parse_failed``, continue.
    7. Per pair from LLM:
       (a) Verify ``a_id`` and ``b_id`` both in this batch's id-set; on
           hallucinated id, emit
           ``dream.contradiction_invalid_id_dropped`` and skip the pair.
       (b) Reject ``a_id == b_id`` (LLM violated prompt).
       (c) Deterministically pick the loser via ``_pick_winner``
           (latest-timestamp wins; lex-lowest tiebreak).
    8. After all batches: collect candidate ``ContradictionPair``s. DROP any
       pair whose ``loser_id`` is in the "protected" set, defined as the union
       of (a) every contradiction ``winner_id`` from the same run, and (b)
       ``protected_ids`` passed by the caller (typically the prior-pass
       dedup-cluster winners). For every drop, emit
       ``dream.contradiction_pair_dropped_winner_collision``. Conservative
       posture (halliday B5 + CodeRabbit #105 — never delete a probable
       winner; keep the §C-J2-disjoint invariant intact PRE-delete so the
       worker never leaves the store in a partial-mutation state).
    9. Emit ``dream.contradiction_batch_complete`` per successful batch with
       ``{batch_index, tokens_in, tokens_out, cost_usd, n_pairs}``.
   10. Sort surviving pairs by ``(loser_id, winner_id)`` ascending.
   11. Loop terminated by cap (``max_calls > 0`` AND batches still pending)
       → emit ``dream.contradiction_call_cap_reached`` with
       ``{max_calls, batches_completed, batches_skipped, items_skipped}``.

    The caller (``DreamingWorker.run``) is responsible for
    ``self.store.delete(loser_id)`` for each returned pair.
    """
    # Step 1: disabled-pass or empty-input short-circuit.
    if not items or max_calls <= 0:
        return ContradictionResult(
            pairs=[], llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, pairs_examined_estimate=0,
        )

    # Lazy imports keep worker.py's top-level allow-list clean (rubric §J-J2-2).
    from ..cost import cost_of
    from .redaction import redact

    # Step 2: deterministic shuffle, hour-bucketed seed (halliday O2).
    hour_bucket = int(now // _SECONDS_PER_HOUR)
    seed_str = f"{session_id}|{hour_bucket}"
    seed_int = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed_int)
    shuffled = list(items)
    rng.shuffle(shuffled)

    # Step 3: non-overlapping batches.
    total_batches_needed = (len(shuffled) + batch_size - 1) // batch_size
    batches_to_run = min(max_calls, total_batches_needed)
    batches_skipped = total_batches_needed - batches_to_run

    item_by_id: dict[str, MemoryItem] = {it.item_id: it for it in items}

    candidate_pairs: list[ContradictionPair] = []
    llm_calls = 0
    total_tokens_in = 0
    total_tokens_out = 0
    pairs_examined = 0

    for batch_idx in range(batches_to_run):
        batch = shuffled[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        batch_id_set = {it.item_id for it in batch}

        # Step 4: redact every per-item value, JSON-serialize (halliday B1).
        batch_payload = json.dumps([
            {
                "id": str(redact(it.item_id)),
                "content": str(redact(it.content)) if it.content is not None else "",
                "timestamp": it.timestamp,
                "tags": [str(redact(t)) for t in it.tags],
            }
            for it in batch
        ])
        wrapped = _wrap_batch_in_envelope(
            batch_payload, session_id=session_id, now=now, batch_idx=batch_idx,
        )
        system_prompt = _get_contradiction_system_prompt()

        # Step 5: fail-open on empty completion OR exception (Pushback H).
        try:
            completion = client.complete(
                wrapped, system=system_prompt, max_tokens=_CONTRADICTION_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 — Pushback H: fail-open on any exception
            emit(
                "dream.contradiction_skipped_unavailable_llm",
                batch_index=batch_idx,
                reason=f"{type(exc).__name__}: {exc}",
            )
            llm_calls += 1
            continue

        llm_calls += 1
        if not completion.text:
            emit(
                "dream.contradiction_skipped_unavailable_llm",
                batch_index=batch_idx,
                reason="empty completion text",
            )
            continue

        # Step 6: parse JSON, fail-open on parse error.
        try:
            data = json.loads(completion.text)
        except json.JSONDecodeError as exc:
            emit(
                "dream.contradiction_batch_parse_failed",
                batch_index=batch_idx,
                reason=str(exc),
            )
            continue
        if not isinstance(data, dict) or "pairs" not in data:
            emit(
                "dream.contradiction_batch_parse_failed",
                batch_index=batch_idx,
                reason="missing or bad 'pairs' key",
            )
            continue
        raw_pairs = data["pairs"]
        if not isinstance(raw_pairs, list):
            emit(
                "dream.contradiction_batch_parse_failed",
                batch_index=batch_idx,
                reason=f"'pairs' not list: {type(raw_pairs).__name__}",
            )
            continue

        # Step 7: per-pair filtering + deterministic loser selection.
        kept_in_batch: list[ContradictionPair] = []
        n_dropped = 0
        for raw in raw_pairs:
            if not isinstance(raw, dict):
                n_dropped += 1
                continue
            a_id = raw.get("a_id")
            b_id = raw.get("b_id")
            rationale = raw.get("rationale", "")
            if not isinstance(a_id, str) or not isinstance(b_id, str):
                n_dropped += 1
                continue
            if not isinstance(rationale, str):
                rationale = ""
            if a_id == b_id:
                n_dropped += 1
                continue
            if a_id not in batch_id_set or b_id not in batch_id_set:
                emit(
                    "dream.contradiction_invalid_id_dropped",
                    batch_index=batch_idx,
                    a_id=a_id,
                    b_id=b_id,
                )
                continue
            winner_id = _pick_winner([item_by_id[a_id], item_by_id[b_id]])
            loser_id = b_id if winner_id == a_id else a_id
            kept_in_batch.append(ContradictionPair(
                loser_id=loser_id,
                winner_id=winner_id,
                rationale=rationale[:_RATIONALE_MAX_LEN],
            ))
        if n_dropped:
            emit(
                "dream.contradiction_partial_parse",
                batch_index=batch_idx,
                n_kept=len(kept_in_batch),
                n_dropped=n_dropped,
            )

        # Step 9: per-batch cost emit (halliday A1).
        batch_cost = cost_of(model, completion.tokens_in, completion.tokens_out)
        emit(
            "dream.contradiction_batch_complete",
            batch_index=batch_idx,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            cost_usd=batch_cost,
            n_pairs=len(kept_in_batch),
        )

        candidate_pairs.extend(kept_in_batch)
        total_tokens_in += completion.tokens_in
        total_tokens_out += completion.tokens_out
        pairs_examined += batch_size * (batch_size - 1) // 2

    # Step 8: winner-collision drop (halliday B5 + CodeRabbit #105).
    # Protects two classes of ids from being deleted as contradiction-losers:
    #   (a) within-pass: an item that's a winner of one pair cannot be deleted
    #       as a loser of another (halliday B5 — never delete a probable winner).
    #   (b) cross-pass: an item that's a prior-pass dedup-cluster winner cannot
    #       be deleted by contradiction. Otherwise the worker could delete a
    #       cluster's only survivor AFTER deleting all its older duplicates,
    #       leaving no representative of that normalized content — and the
    #       §C-J2-disjoint invariant `all_winners ⊥ contradicted_loser_ids`
    #       would fail post-mutation, leaving the store in a partial state.
    protected = set(protected_ids or ()) | {p.winner_id for p in candidate_pairs}
    final_pairs: list[ContradictionPair] = []
    for p in candidate_pairs:
        if p.loser_id in protected:
            emit(
                "dream.contradiction_pair_dropped_winner_collision",
                loser_id=p.loser_id,
                winner_id=p.winner_id,
            )
            continue
        final_pairs.append(p)

    # Step 10: sort surviving pairs by (loser_id, winner_id).
    final_pairs.sort(key=lambda p: (p.loser_id, p.winner_id))

    # Step 11: cap-reached event — only when max_calls > 0 AND cap hit (halliday B4).
    if max_calls > 0 and batches_skipped > 0:
        items_skipped = max(0, len(shuffled) - batches_to_run * batch_size)
        emit(
            "dream.contradiction_call_cap_reached",
            max_calls=max_calls,
            batches_completed=batches_to_run,
            batches_skipped=batches_skipped,
            items_skipped=items_skipped,
        )

    total_cost = cost_of(model, total_tokens_in, total_tokens_out)
    return ContradictionResult(
        pairs=final_pairs,
        llm_calls=llm_calls,
        tokens_in=total_tokens_in,
        tokens_out=total_tokens_out,
        cost_usd=total_cost,
        pairs_examined_estimate=pairs_examined,
    )


def _get_contradiction_system_prompt() -> Any:
    """Lazy-load and ``RedactedText``-wrap the ``CONTRADICTION_SYSTEM_PROMPT``.

    Per ADR-010 §Open-items: developer-authored constants wrapped in
    ``RedactedText`` are the documented bypass — only user-derived content
    needs the redaction boundary's ``redact()`` call.
    """
    from .prompts import CONTRADICTION_SYSTEM_PROMPT
    from .redaction import RedactedText
    return RedactedText(CONTRADICTION_SYSTEM_PROMPT)


def _disjointness_check(named_sets: list[tuple[str, set[str]]]) -> None:
    """Raise ``RuntimeError`` if any two sets share an element.

    Hard invariant (NOT ``assert`` — assertions disappear under
    ``python -O``). Pinned by rubric §F-J2-disjointness-raises per halliday
    blocker B5.
    """
    n = len(named_sets)
    for i in range(n):
        for j in range(i + 1, n):
            overlap = named_sets[i][1] & named_sets[j][1]
            if overlap:
                raise RuntimeError(
                    "Job 2 pass outputs not pairwise disjoint: "
                    f"{named_sets[i][0]} ∩ {named_sets[j][0]} = {sorted(overlap)!r}"
                )


class DreamingWorker:
    """Offline memory-consolidation engine — Jobs 1 (dedup) + 4 (TTL) + 2 (contradiction)."""

    def __init__(self, store: MemoryStore) -> None:
        """Bind the worker to the ``MemoryStore`` it will read + mutate during ``run()``."""
        self.store = store

    def run(self, *, trajectories_path: str | None = None, **kwargs: Any) -> dict:
        """One detection+mutation+pruning+contradiction pass; returns the summary dict.

        Order of operations (rubric Open-contracts pin #8 — TTL → dedup →
        contradiction; JOB1 §F12 + JOB4 §F-TTL-13 + JOB2 §F-J2-3 — all
        deletes complete BEFORE summary emit):

        1. Reject truthy ``trajectories_path`` BEFORE any lock or store access.
        2. NFS detection BEFORE basedir lock.
        3. Acquire basedir flock.
        4. Walk ``store.all()``.
        5. TTL pass: select pruned ids; ``self.store.delete()`` each.
        6. Re-scan surviving items + cluster by normalized content.
        7. Dedup pass: pick winner per cluster; ``self.store.delete()`` losers.
        8. Contradiction pass: batch surviving items through LLM;
           ``self.store.delete()`` losers (Job 2).
        9. Pairwise-disjoint hard check; raise ``RuntimeError`` on violation.
       10. Build the summary dict from the completed deletes.
       11. Emit ``dream.summary``.
        """
        if trajectories_path:
            raise ValueError(
                "trajectories_path not consumed in v1; pass None "
                "(detection+mutation worker reads store.all() exclusively)"
            )

        basedir = _resolve_basedir()
        if _is_network_fs(basedir):
            if os.environ.get("DREAM_ALLOW_NETWORK_FS") == "1":
                log.warning(
                    "DREAM_ALLOW_NETWORK_FS=1 set; proceeding against detected network FS at %s",
                    basedir,
                )
            else:
                raise _UnsupportedFsError(
                    f"basedir {basedir} appears to be on a network filesystem; "
                    f"set DREAM_ALLOW_NETWORK_FS=1 to override"
                )

        with _basedir_dream_lock(basedir):
            items: list[MemoryItem] = list(self.store.all())
            total_items = len(items)

            retention_days = _read_item_retention_days()
            retention_seconds = retention_days * _SECONDS_PER_DAY
            max_calls = _read_contradiction_max_calls()

            # JOB4 §D-TTL-4 cardinality contract: at most one _now() call per run.
            # Both TTL (Job 4) and contradiction (Job 2) share the same cached value.
            now_cached: float = 0.0
            if retention_days > 0 or max_calls > 0:
                now_cached = _now()

            # JOB4 pin #9: retention_days == 0 disables TTL pruning.
            if retention_days == 0:
                pruned_ids: list[str] = []
            else:
                pruned_ids = _pick_pruned(items, now_cached, retention_seconds)

            # JOB4 §F-TTL-2: TTL deletes complete BEFORE dedup deletes.
            for pid in pruned_ids:
                self.store.delete(pid)

            pruned_set = set(pruned_ids)
            survivors = [it for it in items if it.item_id not in pruned_set]

            groups: dict[str, list[MemoryItem]] = {}
            for item in survivors:
                key = _normalize(item.content)
                groups.setdefault(key, []).append(item)

            cluster_specs: list[dict] = []
            for key, group_items in groups.items():
                if len(group_items) < 2:
                    continue
                item_ids = [i.item_id for i in group_items]
                winner_id = _pick_winner(group_items)
                retired_ids = [iid for iid in item_ids if iid != winner_id]
                cluster_specs.append(
                    {
                        "normalized_key": key,
                        "item_ids": list(item_ids),
                        "count": len(item_ids),
                        "winner_id": winner_id,
                        "retired_ids": list(retired_ids),
                    }
                )

            # JOB1 §F12 + JOB4 §F-TTL-13: dedup deletes after TTL, before contradiction.
            retired_ids_set: set[str] = set()
            for cluster in cluster_specs:
                for retired_id in cluster["retired_ids"]:
                    self.store.delete(retired_id)
                    retired_ids_set.add(retired_id)

            # ── JOB 2 contradiction pass ────────────────────────────────────
            # Working set excludes prior-pass-retired ids (no race; same
            # in-memory snapshot under the basedir lock).
            contradiction_survivors = [
                it for it in items
                if it.item_id not in pruned_set and it.item_id not in retired_ids_set
            ]
            # Cluster winners are PROTECTED from being contradiction-losers
            # (CodeRabbit #105 fix): the §C-J2-disjoint invariant
            # `all_winners ⊥ contradicted_loser_ids` must hold pre-delete, not
            # post-delete — otherwise a deleted cluster-winner leaves no
            # representative of its normalized content. The conservative posture
            # (halliday B5) defers to the dedup pass's recency judgment.
            cluster_winners_set: set[str] = {c["winner_id"] for c in cluster_specs}
            llm_client = _make_llm_client()
            contradiction_result = _detect_contradictions(
                contradiction_survivors,
                llm_client,
                batch_size=_CONTRADICTION_BATCH_SIZE,
                max_calls=max_calls,
                model=getattr(llm_client, "model", "unknown"),
                session_id=_session_id_for_dream(basedir),
                now=now_cached,
                protected_ids=cluster_winners_set,
            )

            # JOB2 §F-J2-3: contradiction deletes complete BEFORE summary emit.
            contradicted_loser_ids: set[str] = set()
            for pair in contradiction_result.pairs:
                self.store.delete(pair.loser_id)
                contradicted_loser_ids.add(pair.loser_id)

            # JOB2 §F-J2-disjointness-raises: hard invariant, RuntimeError not assert
            # (assertions disappear under `python -O`).
            all_winners = (
                {p.winner_id for p in contradiction_result.pairs}
                | {c["winner_id"] for c in cluster_specs}
            )
            _disjointness_check([
                ("pruned_ids", pruned_set),
                ("retired_ids", retired_ids_set),
                ("contradicted_loser_ids", contradicted_loser_ids),
                ("all_winners", all_winners),
            ])

            duplicate_clusters = len(cluster_specs)
            items_in_duplicates = sum(c["count"] for c in cluster_specs)
            items_retired = sum(len(c["retired_ids"]) for c in cluster_specs)
            items_pruned = len(pruned_ids)
            items_contradicted = len(contradiction_result.pairs)

            summary = {
                "schema": "dream.summary",
                "version": 1,
                "mode": "detection_and_mutation_and_pruning_and_contradiction",
                "jobs_run": [
                    "dedup_detection",
                    "dedup_merge",
                    "ttl_pruning",
                    "contradiction_resolution",
                ],
                "skipped_jobs": ["governance"],
                "counts": {
                    "total_items": total_items,
                    "duplicate_clusters": duplicate_clusters,
                    "items_in_duplicates": items_in_duplicates,
                    "items_retired": items_retired,
                    "items_pruned": items_pruned,
                    "retention_seconds_effective": retention_seconds,
                    "items_contradicted": items_contradicted,
                    "contradiction_llm_calls": contradiction_result.llm_calls,
                    "contradiction_input_tokens": contradiction_result.tokens_in,
                    "contradiction_output_tokens": contradiction_result.tokens_out,
                    "contradiction_cost_usd_estimate": contradiction_result.cost_usd,
                    "contradiction_pairs_examined_estimate": contradiction_result.pairs_examined_estimate,
                },
                "clusters": cluster_specs,
                "pruned": {
                    "item_ids": list(pruned_ids),
                    "retention_seconds_effective": retention_seconds,
                },
                "contradicted": {
                    "pairs": [
                        {
                            "loser_id": p.loser_id,
                            "winner_id": p.winner_id,
                            "rationale": p.rationale,
                        }
                        for p in contradiction_result.pairs
                    ],
                    "model": getattr(llm_client, "model", "unknown"),
                },
            }

            emit(
                "dream.summary",
                mode=summary["mode"],
                total_items=total_items,
                duplicate_clusters=duplicate_clusters,
                items_retired=items_retired,
                items_pruned=items_pruned,
                retention_seconds_effective=retention_seconds,
                items_contradicted=items_contradicted,
                contradiction_llm_calls=contradiction_result.llm_calls,
                contradiction_input_tokens=contradiction_result.tokens_in,
                contradiction_output_tokens=contradiction_result.tokens_out,
                contradiction_cost_usd_estimate=contradiction_result.cost_usd,
                contradiction_pairs_examined_estimate=contradiction_result.pairs_examined_estimate,
            )

            return summary


def dream(store: MemoryStore, **kwargs: Any) -> dict:
    """Convenience: run one :class:`DreamingWorker` pass over ``store``."""
    return DreamingWorker(store).run(**kwargs)


__all__ = ["DreamingWorker", "dream", "ContradictionPair", "ContradictionResult"]
