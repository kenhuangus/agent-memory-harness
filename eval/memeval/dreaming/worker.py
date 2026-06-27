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
from typing import Any, Iterator, NamedTuple

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

#: ADR-dreaming-028 §2 — page size used when iterating the store. Backends
#: implementing `iter_pages(page_size)` natively get cursor-based streaming
#: at this granularity; backends without an override fall back to a single
#: `[list(store.all())]` page (today's behavior). Tunable per-call via the
#: helper kwarg if a future pass wants finer control.
_DEFAULT_PAGE_SIZE: int = 1000

#: ADR-dreaming-028 §2 — number of nearest neighbors a per-item consolidation
#: pass examines via `store.search()`. The pivot item plus its K neighbors
#: form the working set for that item's dedup + contradiction judgments. K=10
#: matches Mem0's design point (cited in the ADR's Rationale) and gives the
#: LLM a small, focused stack to judge instead of an unbounded batch.
_DEFAULT_NEIGHBORHOOD_K: int = 10

#: ADR-dreaming-028 §1 — type-conditioned retention. The TTL pass looks up
#: each item's retention in days by its ``metadata["okf_type"]`` (set by the
#: parser per ADR-027). `None` means *no calendar TTL* — the item never
#: ages out by this pass; supersession only happens via dedup, contradiction,
#: or future code-change detection. Items whose `okf_type` is missing (pre-V5
#: memories) or unknown (off-list, which the parser already fell back to
#: ``"Memory"``) use the ``"Memory"`` row — preserving today's flat 30-day
#: default for unset content.
#:
#: ``DREAM_ITEM_RETENTION_DAYS`` is kill-switch-only in v2: ``"0"`` disables
#: ALL TTL regardless of type; any other value is IGNORED. Per-type values
#: below are code-level constants, not env-tunable — adjusting one is a
#: deliberate code change that goes through PR review.
TYPE_RETENTION_DAYS: dict[str, int | None] = {
    # Durable types — never age-bombed; supersession via non-age signals only.
    "Identity":      None,
    "Convention":    None,
    "Invariant":     None,
    "Workaround":    None,
    "Bug":           None,
    "Contradiction": None,  # ADR-028 §5 — worker-emitted disagreement records
    # Calendar-decay types.
    "Decision":      365,
    "Preference":    180,
    "Fix":           90,
    # Fallback for pre-V5 / off-list. Matches today's flat default — back-compat.
    "Memory":        _DEFAULT_ITEM_RETENTION_DAYS,
}

# Job 2 contradiction constants.
_SECONDS_PER_HOUR: int = 3600
_DEFAULT_CONTRADICTION_MAX_CALLS: int = 20
_CONTRADICTION_BATCH_SIZE: int = 10
_CONTRADICTION_MAX_TOKENS: int = 1024
_RATIONALE_MAX_LEN: int = 200

# Job 3 governance constants. `_RATIONALE_MAX_LEN` REUSED — no duplication.
_DEFAULT_GOVERNANCE_MAX_CALLS: int = 20
_GOVERNANCE_BATCH_SIZE: int = 10
_GOVERNANCE_MAX_TOKENS: int = 1024

# ── Induction (ADR-dreaming-028 §3 — the "generalizer", CREATE-only) ──────────
# Induction is the highest-risk pass (synthesis from LLM inference), so it ships
# DEFAULT OFF behind `DREAM_INDUCTION=1` with a small call budget, per ADR-028
# §Tradeoffs ("induction ships with a much lower call budget than deduction
# initially, gated on real-bench measurement of synthesis quality").
_DEFAULT_INDUCTION_MAX_CALLS: int = 5
_INDUCTION_MAX_TOKENS: int = 1024
_INDUCTION_CONTENT_MAX_LEN: int = 400
#: Minimum cluster size before induction will attempt a synthesis. ADR-028 §3:
#: "clusters of three or more `Fix` cards for structurally similar bugs."
_INDUCTION_MIN_CLUSTER: int = 3
#: Lower-durability source types induction generalizes UP from. The synthesized
#: card is typed Invariant/Convention (durable, no calendar TTL per §1).
_INDUCTION_SOURCE_TYPES: frozenset[str] = frozenset({"Fix", "Bug", "Workaround"})
#: Target types the induction LLM may emit; anything else is dropped.
_INDUCTION_TARGET_TYPES: frozenset[str] = frozenset({"Invariant", "Convention"})


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


def _read_use_neighborhood_dedup() -> bool:
    """ADR-dreaming-028 §2 — kill switch for the neighborhood-scoped LLM
    dedup pre-pass (PR #2e's ``_detect_duplicates_neighborhood``).

    **Default ON.** As of the flip-on-trust decision (PR #2h, post-#2g),
    v2 is the default consolidation behavior. Only ``"0"`` explicitly
    disables the pass; any other value (unset, empty, "1", "true",
    misspellings) reads as ON. This shape — "only the literal kill
    string disables" — mirrors the kill-switch contract
    ``DREAM_ITEM_RETENTION_DAYS=0`` from ADR-028 §1 PR #1.

    Operationally, the pass runs AFTER lexical dedup and BEFORE
    contradiction. Its retired ids feed into the contradiction pass's
    ``protected_ids`` so a contradiction loser doesn't also get marked
    as a duplicate-loser of the same pair.
    """
    return os.environ.get("DREAM_DEDUP_NEIGHBORHOOD") != "0"


def _read_use_neighborhood_contradiction() -> bool:
    """ADR-dreaming-028 §2 — kill switch for the neighborhood-scoped
    contradiction path (PR #2c's ``_detect_contradictions_neighborhood``)
    over the v1 batch-and-shuffle path (``_detect_contradictions``).

    **Default ON.** As of the flip-on-trust decision (PR #2h, post-#2g),
    v2 is the default. Only ``"0"`` falls back to the v1 path; any other
    value (unset, empty, "1", "true", misspellings) keeps v2. Matches the
    kill-switch shape of :func:`_read_use_neighborhood_dedup` and the
    ``DREAM_ITEM_RETENTION_DAYS=0`` kill switch from PR #1.
    """
    return os.environ.get("DREAM_CONTRADICTION_NEIGHBORHOOD") != "0"


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


def _read_governance_max_calls() -> int:
    """Resolve ``$DREAM_GOVERNANCE_MAX_CALLS`` to an int.

    Per JOB3 Open-contracts pin (mirrors Job 2's contradiction cap):
    - Unset → default 20.
    - ``"0"`` → 0 (treated as DISABLED — no LLM call at all). Footgun protection
      matching Job 4 §H-TTL-2 + Job 2.
    - Negative → clamped to 0 (disable).
    - Non-integer → 20 default with a warning log.
    """
    raw = os.environ.get("DREAM_GOVERNANCE_MAX_CALLS")
    if raw is None or raw == "":
        return _DEFAULT_GOVERNANCE_MAX_CALLS
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "DREAM_GOVERNANCE_MAX_CALLS=%r is not an integer; falling back to %d default",
            raw, _DEFAULT_GOVERNANCE_MAX_CALLS,
        )
        return _DEFAULT_GOVERNANCE_MAX_CALLS
    return max(0, value)


def _read_use_induction() -> bool:
    """ADR-028 §3: induction (create-only generalizer) is DEFAULT OFF.

    Opt in with ``DREAM_INDUCTION=1``. Any other value (including unset) leaves
    induction disabled — the conservative posture for the riskiest pass.
    """
    return os.environ.get("DREAM_INDUCTION") == "1"


def _read_induction_max_calls() -> int:
    """Resolve ``$DREAM_INDUCTION_MAX_CALLS`` to an int (separate budget per §3).

    Unset → default 5 (deliberately small). ``"0"``/negative → 0 (disabled).
    Non-integer → default with a warning. Independent of the dedup/contradiction
    budget so synthesis cost is capped on its own knob (ADR-028 §3 + §Tradeoffs).
    """
    raw = os.environ.get("DREAM_INDUCTION_MAX_CALLS")
    if raw is None or raw == "":
        return _DEFAULT_INDUCTION_MAX_CALLS
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "DREAM_INDUCTION_MAX_CALLS=%r is not an integer; falling back to %d default",
            raw, _DEFAULT_INDUCTION_MAX_CALLS,
        )
        return _DEFAULT_INDUCTION_MAX_CALLS
    return max(0, value)


def _iter_store_pages(
    store: MemoryStore,
    *,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> Iterator[list[MemoryItem]]:
    """ADR-dreaming-028 §2 — iterate the store in pages of up to ``page_size``.

    If ``store`` implements ``iter_pages(page_size: int) -> Iterator[list[MemoryItem]]``
    (an opt-in extension to the :class:`~memeval.protocols.MemoryStore`
    protocol), this helper delegates to it directly. Backends that do are
    expected to yield real streaming pages from their underlying cursor
    (FTS5's `SELECT … LIMIT N OFFSET …`, sqlite_vec's row-iter, etc.).

    Backends that do NOT override ``iter_pages`` fall back to a single page
    wrapping ``store.all()`` — byte-identical to today's read pattern. This
    keeps the v1 contract intact and lets storage adopt streaming on its own
    schedule.

    Page-by-page consumption is intentionally caller-controlled: this helper
    only YIELDS pages; whatever the caller does with them (materialize into
    a single list per today's worker, or process page-by-page in future
    redesigns) stays inside the dream worker's domain.
    """
    if hasattr(store, "iter_pages"):
        yield from store.iter_pages(page_size=page_size)  # type: ignore[attr-defined]
        return
    yield list(store.all())


def _neighborhood_for(
    store: MemoryStore,
    item: MemoryItem,
    *,
    k: int = _DEFAULT_NEIGHBORHOOD_K,
) -> list[MemoryItem]:
    """ADR-dreaming-028 §2 — return ``item`` plus its ``k`` nearest neighbors
    in the store (the 1+K "neighborhood stack" the per-item consolidation
    passes operate against).

    The pivot ``item`` is ALWAYS the first element of the returned list, so
    callers can rely on ``result[0].item_id == item.item_id``. The remaining
    elements are the top-``k`` matches from ``store.search(item.content, k=k)``,
    in store-ranked order (best first), with the pivot itself filtered out
    if the search backend returns it (some do, some don't — this helper
    normalizes the behavior).

    Empty/degenerate cases — search returning nothing, search raising, or the
    store's `search` being unable to vectorize the content — all return
    ``[item]`` (the pivot alone). Callers can detect a thin neighborhood by
    checking ``len(result) == 1`` and skip the per-item judgment if desired.

    Why a free function and not a method on the worker
      The helper is a pure read primitive — no mutation, no event emission —
      and parallels the existing :func:`_iter_store_pages` shape. Keeping it
      a free function makes the test surface trivial (synthesize a store,
      call the helper) and means consolidation passes can adopt it without
      depending on worker-instance state.
    """
    try:
        hits = store.search(item.content, k=k)
    except Exception:
        # ADR-harness-006 fail-open: a search backend that throws (e.g., a
        # missing embedding API key on the accuracy profile) MUST NOT crash
        # the consolidation pass. The caller gets the pivot alone — same as
        # if the backend had returned no neighbors.
        return [item]
    neighbors: list[MemoryItem] = []
    for hit in hits:
        candidate = hit.item
        if candidate.item_id == item.item_id:
            continue  # backend included the pivot; filter it
        neighbors.append(candidate)
    return [item, *neighbors]


def _retention_seconds_for(item: MemoryItem, table: dict[str, int | None]) -> int | None:
    """ADR-028 §1 per-item retention lookup. Returns seconds, or ``None`` when
    the item's type has no calendar TTL (durable types). Items whose
    ``okf_type`` metadata is missing or off-list resolve to ``"Memory"``,
    preserving today's 30-day default for untyped content."""
    okf_type = (item.metadata or {}).get("okf_type") or "Memory"
    days = table.get(okf_type, _DEFAULT_ITEM_RETENTION_DAYS)
    if days is None:
        return None
    return int(days) * _SECONDS_PER_DAY


def _pick_pruned(
    items: list[MemoryItem],
    now: float,
    retention_seconds: int = -1,
    *,
    retention_table: dict[str, int | None] | None = None,
) -> list[str]:
    """Return the lex-sorted item_ids whose age strictly exceeds their
    type-conditioned retention.

    JOB4 §F-TTL-3 (strictly greater) + §B13 (sorted ascending in the dict).

    ADR-028 §1 changed the per-item retention from a single
    ``retention_seconds`` value to a per-type lookup via
    ``TYPE_RETENTION_DAYS``. The legacy ``retention_seconds`` positional arg
    is retained for back-compat with rubric-pinned call sites and tests but
    is IGNORED when ``retention_table`` is supplied (always, in production).
    A caller that wants to override the per-type table (tests) passes
    ``retention_table=`` as a kwarg.

    Items with no calendar TTL for their type (durable types per ADR-028 §1
    — ``Identity``, ``Convention``, ``Invariant``, ``Workaround``, ``Bug``,
    ``Contradiction``) are NEVER returned by this pass.
    """
    table = retention_table if retention_table is not None else TYPE_RETENTION_DAYS
    pruned: list[str] = []
    for item in items:
        retention = _retention_seconds_for(item, table)
        if retention is None:
            continue  # durable type — no calendar TTL
        if (now - item.timestamp) > retention:
            pruned.append(item.item_id)
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


class DedupPair(NamedTuple):
    """One LLM-identified dedup-able pair with deterministically-chosen loser.

    ADR-dreaming-028 §2 PR #2e. Parallel to :class:`ContradictionPair`; kept
    as a distinct type so the worker's downstream emit/audit code can tell
    a duplicate-merge from a contradiction-loser-delete at the type level."""

    loser_id: str
    winner_id: str
    rationale: str


class DedupResult(NamedTuple):
    """Output of the neighborhood-scoped dedup pass — pairs + cost metrics.

    ADR-dreaming-028 §2 PR #2e. Parallel to :class:`ContradictionResult`."""

    pairs: list[DedupPair]
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    pairs_examined_estimate: int


class GovernanceTag(NamedTuple):
    """One LLM-classification for the governance pass.

    ``batch_index`` is internal — used for the per-id ``dream.governance_blacklisted``
    audit emit (halliday A3) but PROJECTED OUT at summary construction time
    (rubric §B16 fixes summary entries to ``{item_id, rationale}``).
    """

    item_id: str
    rationale: str
    batch_index: int


class GovernanceResult(NamedTuple):
    """Output of the governance pass — three class lists + cost metrics."""

    must_know: list[GovernanceTag]
    must_do: list[GovernanceTag]
    blacklisted: list[GovernanceTag]
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    items_examined_estimate: int


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


def _wrap_governance_batch_in_envelope(payload: str, *, session_id: str, now: float, batch_idx: int) -> Any:
    """Wrap a governance batch JSON payload in the shared nonce-tagged envelope.

    Per rubric §J-J3-envelope-named (extends Job 2 §J-J2-envelope-named): this
    is the THIRD named envelope-format call site in the dreaming module. The
    ``test_extract.py`` AST audit allow-set is extended to authorize this name
    alongside ``_wrap_user_content_in_envelope`` and ``_wrap_batch_in_envelope``.

    Nonce derivation uses a ``gov`` discriminator to prevent accidental
    nonce-collision with Job 2's contradiction batches (Pushback M).
    """
    from .prompts import _ENVELOPE_TEMPLATE
    from .redaction import RedactedText
    nonce_seed = f"{session_id}|{now}|{batch_idx}|gov"
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


def _detect_contradictions_neighborhood(
    items: list[MemoryItem],
    store: MemoryStore,
    client: Any,
    *,
    max_calls: int,
    model: str,
    session_id: str,
    now: float,
    k: int = _DEFAULT_NEIGHBORHOOD_K,
    protected_ids: set[str] | None = None,
) -> ContradictionResult:
    """ADR-dreaming-028 §2 — neighborhood-scoped contradiction judgment.

    For each pivot item (up to ``max_calls`` pivots), fetches the K nearest
    neighbors via :func:`_neighborhood_for` and asks the LLM to find
    contradicting pairs WITHIN that neighborhood stack. Pairs are
    deduplicated across pivots: if both A and B are pivots and both
    neighborhoods surface the (A, B) pair, the first occurrence wins.

    Difference vs. :func:`_detect_contradictions` (the v1 path):
      - Per-pivot LLM call instead of non-overlapping shuffle windows.
      - ``max_calls`` bounds PIVOT count, not arbitrary batch count.
      - LLM judges semantically-related items only (the neighborhood is
        the K most similar by the store's search ranking), not arbitrary
        co-batched items. Narrows coverage but raises the per-call signal.

    This function is NOT yet wired into :meth:`DreamingWorker.run`. PR #2c
    of the ADR-028 implementation sequence adds the helper; a follow-up PR
    will wire it behind a feature flag for A/B measurement against the
    existing path before any promotion to default.

    Same fail-open contract as :func:`_detect_contradictions`: every LLM
    failure mode (exception, empty completion, parse error, bad pairs key,
    invalid types) becomes an event + continue, never raises. Conservative
    posture per [ADR-harness-006](../../docs/adrs/ADR-harness-006-fail-open-stop-hook.md).
    """
    if not items or max_calls <= 0:
        return ContradictionResult(
            pairs=[], llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, pairs_examined_estimate=0,
        )

    from ..cost import cost_of
    from .redaction import redact

    item_by_id: dict[str, MemoryItem] = {it.item_id: it for it in items}

    seen_pairs: set[frozenset[str]] = set()
    candidate_pairs: list[ContradictionPair] = []
    llm_calls = 0
    total_tokens_in = 0
    total_tokens_out = 0
    pairs_examined = 0
    pivots_processed = 0

    for pivot in items:
        if llm_calls >= max_calls:
            break
        # Per ADR-028 §2 PR #2b: fail-open returns `[pivot]` alone when
        # the search backend has no neighbors OR errors. A thin
        # neighborhood has no pairs to judge — skip and don't burn a call.
        neighborhood = _neighborhood_for(store, pivot, k=k)
        if len(neighborhood) < 2:
            continue
        pivots_processed += 1

        batch_id_set = {it.item_id for it in neighborhood}
        batch_payload = json.dumps([
            {
                "id": str(redact(it.item_id)),
                "content": str(redact(it.content)) if it.content is not None else "",
                "timestamp": it.timestamp,
                "tags": [str(redact(t)) for t in it.tags],
            }
            for it in neighborhood
        ])
        wrapped = _wrap_batch_in_envelope(
            batch_payload, session_id=session_id, now=now, batch_idx=pivots_processed - 1,
        )
        system_prompt = _get_contradiction_system_prompt()

        try:
            completion = client.complete(
                wrapped, system=system_prompt, max_tokens=_CONTRADICTION_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 — Pushback H: fail-open on any exception
            emit(
                "dream.contradiction_skipped_unavailable_llm",
                batch_index=pivots_processed - 1,
                reason=f"{type(exc).__name__}: {exc}",
            )
            llm_calls += 1
            continue
        llm_calls += 1
        if not completion.text:
            emit(
                "dream.contradiction_skipped_unavailable_llm",
                batch_index=pivots_processed - 1,
                reason="empty completion text",
            )
            continue

        try:
            data = json.loads(completion.text)
        except json.JSONDecodeError as exc:
            emit(
                "dream.contradiction_batch_parse_failed",
                batch_index=pivots_processed - 1,
                reason=str(exc),
            )
            continue
        if not isinstance(data, dict) or "pairs" not in data:
            emit(
                "dream.contradiction_batch_parse_failed",
                batch_index=pivots_processed - 1,
                reason="missing or bad 'pairs' key",
            )
            continue
        raw_pairs = data["pairs"]
        if not isinstance(raw_pairs, list):
            emit(
                "dream.contradiction_batch_parse_failed",
                batch_index=pivots_processed - 1,
                reason=f"'pairs' not list: {type(raw_pairs).__name__}",
            )
            continue

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
                    batch_index=pivots_processed - 1,
                    a_id=a_id, b_id=b_id,
                )
                continue
            # Cross-pivot dedupe: the same pair can surface from both A's
            # neighborhood and B's neighborhood. Keep the first occurrence.
            pair_key = frozenset({a_id, b_id})
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            # Pivot might not be in `items` dict (it could be a neighbor
            # from elsewhere in the store). Fall back to the neighborhood's
            # own copies for `_pick_winner`.
            nbhd_by_id = {it.item_id: it for it in neighborhood}
            a_item = item_by_id.get(a_id) or nbhd_by_id[a_id]
            b_item = item_by_id.get(b_id) or nbhd_by_id[b_id]
            winner_id = _pick_winner([a_item, b_item])
            loser_id = b_id if winner_id == a_id else a_id
            kept_in_batch.append(ContradictionPair(
                loser_id=loser_id,
                winner_id=winner_id,
                rationale=rationale[:_RATIONALE_MAX_LEN],
            ))
        if n_dropped:
            emit(
                "dream.contradiction_partial_parse",
                batch_index=pivots_processed - 1,
                n_kept=len(kept_in_batch),
                n_dropped=n_dropped,
            )

        batch_cost = cost_of(model, completion.tokens_in, completion.tokens_out)
        emit(
            "dream.contradiction_batch_complete",
            batch_index=pivots_processed - 1,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            cost_usd=batch_cost,
            n_pairs=len(kept_in_batch),
        )

        candidate_pairs.extend(kept_in_batch)
        total_tokens_in += completion.tokens_in
        total_tokens_out += completion.tokens_out
        # Estimate: pivot × (neighborhood - 1) pairs examined per call.
        pairs_examined += len(neighborhood) - 1

    # Same winner-collision protection as v1 — never delete a probable winner.
    protected = set(protected_ids or ()) | {p.winner_id for p in candidate_pairs}
    final_pairs: list[ContradictionPair] = []
    for p in candidate_pairs:
        if p.loser_id in protected:
            emit(
                "dream.contradiction_pair_dropped_winner_collision",
                loser_id=p.loser_id, winner_id=p.winner_id,
            )
            continue
        final_pairs.append(p)
    final_pairs.sort(key=lambda p: (p.loser_id, p.winner_id))

    total_cost = cost_of(model, total_tokens_in, total_tokens_out)
    return ContradictionResult(
        pairs=final_pairs,
        llm_calls=llm_calls,
        tokens_in=total_tokens_in,
        tokens_out=total_tokens_out,
        cost_usd=total_cost,
        pairs_examined_estimate=pairs_examined,
    )


def _detect_duplicates_neighborhood(
    items: list[MemoryItem],
    store: MemoryStore,
    client: Any,
    *,
    max_calls: int,
    model: str,
    session_id: str,
    now: float,
    k: int = _DEFAULT_NEIGHBORHOOD_K,
    protected_ids: set[str] | None = None,
) -> DedupResult:
    """ADR-dreaming-028 §2 PR #2e — neighborhood-scoped dedup judgment.

    For each pivot item (up to ``max_calls`` pivots), fetches the K nearest
    neighbors via :func:`_neighborhood_for` and asks the LLM ``DEDUP_SYSTEM_PROMPT``
    "which pairs say the same thing?" Pairs are deduplicated across pivots,
    same loser-collision protection as :func:`_detect_contradictions_neighborhood`
    applies.

    This is the parser-grade twin of the neighborhood contradiction pass:
    same neighborhood input shape, same envelope wrapping, same fail-open
    contract, same cross-pivot pair dedup. The ONLY semantic differences:

      - System prompt asks for SAME-THING pairs, not contradicting pairs.
      - The returned :class:`DedupPair` is a distinct type from
        :class:`ContradictionPair` so the worker's downstream code can tell
        a duplicate-merge from a contradiction-loser-delete by type.

    NOT wired into :meth:`DreamingWorker.run` yet. PR #2f will gate the
    wiring behind ``DREAM_DEDUP_NEIGHBORHOOD=1`` for A/B measurement.

    Fail-open contract per [ADR-harness-006](../../docs/adrs/ADR-harness-006-fail-open-stop-hook.md):
    every LLM failure mode (exception, empty completion, parse error,
    bad pairs key, invalid types) becomes an event + continue, never raises.
    """
    if not items or max_calls <= 0:
        return DedupResult(
            pairs=[], llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, pairs_examined_estimate=0,
        )

    from ..cost import cost_of
    from .redaction import redact

    item_by_id: dict[str, MemoryItem] = {it.item_id: it for it in items}
    seen_pairs: set[frozenset[str]] = set()
    candidate_pairs: list[DedupPair] = []
    llm_calls = 0
    total_tokens_in = 0
    total_tokens_out = 0
    pairs_examined = 0
    pivots_processed = 0

    for pivot in items:
        if llm_calls >= max_calls:
            break
        neighborhood = _neighborhood_for(store, pivot, k=k)
        if len(neighborhood) < 2:
            continue
        pivots_processed += 1

        batch_id_set = {it.item_id for it in neighborhood}
        batch_payload = json.dumps([
            {
                "id": str(redact(it.item_id)),
                "content": str(redact(it.content)) if it.content is not None else "",
                "timestamp": it.timestamp,
                "tags": [str(redact(t)) for t in it.tags],
            }
            for it in neighborhood
        ])
        wrapped = _wrap_batch_in_envelope(
            batch_payload, session_id=session_id, now=now, batch_idx=pivots_processed - 1,
        )
        system_prompt = _get_dedup_system_prompt()

        try:
            completion = client.complete(
                wrapped, system=system_prompt, max_tokens=_CONTRADICTION_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 — Pushback H: fail-open on any exception
            emit(
                "dream.dedup_skipped_unavailable_llm",
                batch_index=pivots_processed - 1,
                reason=f"{type(exc).__name__}: {exc}",
            )
            llm_calls += 1
            continue
        llm_calls += 1
        if not completion.text:
            emit(
                "dream.dedup_skipped_unavailable_llm",
                batch_index=pivots_processed - 1,
                reason="empty completion text",
            )
            continue

        try:
            data = json.loads(completion.text)
        except json.JSONDecodeError as exc:
            emit(
                "dream.dedup_batch_parse_failed",
                batch_index=pivots_processed - 1,
                reason=str(exc),
            )
            continue
        if not isinstance(data, dict) or "pairs" not in data:
            emit(
                "dream.dedup_batch_parse_failed",
                batch_index=pivots_processed - 1,
                reason="missing or bad 'pairs' key",
            )
            continue
        raw_pairs = data["pairs"]
        if not isinstance(raw_pairs, list):
            emit(
                "dream.dedup_batch_parse_failed",
                batch_index=pivots_processed - 1,
                reason=f"'pairs' not list: {type(raw_pairs).__name__}",
            )
            continue

        kept_in_batch: list[DedupPair] = []
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
                    "dream.dedup_invalid_id_dropped",
                    batch_index=pivots_processed - 1,
                    a_id=a_id, b_id=b_id,
                )
                continue
            pair_key = frozenset({a_id, b_id})
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            nbhd_by_id = {it.item_id: it for it in neighborhood}
            a_item = item_by_id.get(a_id) or nbhd_by_id[a_id]
            b_item = item_by_id.get(b_id) or nbhd_by_id[b_id]
            winner_id = _pick_winner([a_item, b_item])
            loser_id = b_id if winner_id == a_id else a_id
            kept_in_batch.append(DedupPair(
                loser_id=loser_id,
                winner_id=winner_id,
                rationale=rationale[:_RATIONALE_MAX_LEN],
            ))
        if n_dropped:
            emit(
                "dream.dedup_partial_parse",
                batch_index=pivots_processed - 1,
                n_kept=len(kept_in_batch),
                n_dropped=n_dropped,
            )

        batch_cost = cost_of(model, completion.tokens_in, completion.tokens_out)
        emit(
            "dream.dedup_batch_complete",
            batch_index=pivots_processed - 1,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            cost_usd=batch_cost,
            n_pairs=len(kept_in_batch),
        )

        candidate_pairs.extend(kept_in_batch)
        total_tokens_in += completion.tokens_in
        total_tokens_out += completion.tokens_out
        pairs_examined += len(neighborhood) - 1

    # Same winner-collision protection — never delete a probable winner.
    protected = set(protected_ids or ()) | {p.winner_id for p in candidate_pairs}
    final_pairs: list[DedupPair] = []
    for p in candidate_pairs:
        if p.loser_id in protected:
            emit(
                "dream.dedup_pair_dropped_winner_collision",
                loser_id=p.loser_id, winner_id=p.winner_id,
            )
            continue
        final_pairs.append(p)
    final_pairs.sort(key=lambda p: (p.loser_id, p.winner_id))

    total_cost = cost_of(model, total_tokens_in, total_tokens_out)
    return DedupResult(
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


def _get_governance_system_prompt() -> Any:
    """Lazy-load and ``RedactedText``-wrap the ``GOVERNANCE_SYSTEM_PROMPT`` (ADR-010 bypass)."""
    from .prompts import GOVERNANCE_SYSTEM_PROMPT
    from .redaction import RedactedText
    return RedactedText(GOVERNANCE_SYSTEM_PROMPT)


def _get_dedup_system_prompt() -> Any:
    """Lazy-load and ``RedactedText``-wrap the ``DEDUP_SYSTEM_PROMPT`` (ADR-010 bypass).
    Same pattern as :func:`_get_contradiction_system_prompt`; used by the
    neighborhood-scoped dedup helper (ADR-dreaming-028 §2 PR #2e)."""
    from .prompts import DEDUP_SYSTEM_PROMPT
    from .redaction import RedactedText
    return RedactedText(DEDUP_SYSTEM_PROMPT)


def _get_induction_system_prompt() -> Any:
    """Lazy-load and ``RedactedText``-wrap the ``INDUCTION_SYSTEM_PROMPT`` (ADR-010 bypass).
    Used by the create-only induction pass (ADR-dreaming-028 §3)."""
    from .prompts import INDUCTION_SYSTEM_PROMPT
    from .redaction import RedactedText
    return RedactedText(INDUCTION_SYSTEM_PROMPT)


class InductionCard(NamedTuple):
    """One synthesized card emitted by the induction pass (ADR-028 §3).

    ``item_id`` is the new card's id; ``synthesized_from`` names every source
    item the synthesis generalized (mandatory provenance per §3); ``okf_type``
    is the durable target type (Invariant/Convention).
    """

    item_id: str
    okf_type: str
    synthesized_from: tuple[str, ...]
    rationale: str


class InductionResult(NamedTuple):
    """Aggregate result of the induction pass (mirrors DedupResult shape)."""

    created: list[InductionCard]
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    clusters_examined: int


def _cluster_for_induction(
    items: list[MemoryItem],
    *,
    min_cluster: int = _INDUCTION_MIN_CLUSTER,
) -> list[list[MemoryItem]]:
    """Group lower-durability survivors into induction candidate clusters.

    ADR-028 §3 + §Open-items ("first cut uses tag-and-type clustering with a
    similarity floor"). Deterministic, embedding-free so it works against any
    backend: cluster by ``(okf_type, tag)`` over the source types in
    ``_INDUCTION_SOURCE_TYPES``, keeping only clusters with at least
    ``min_cluster`` distinct items. A card carrying several tags can seed
    several clusters; duplicate clusters (same id set) are emitted once.
    Ordering is stable (sorted cluster keys, items by item_id) so a given store
    state always yields the same clusters.
    """
    buckets: dict[tuple[str, str], list[MemoryItem]] = {}
    for it in items:
        okf_type = (it.metadata or {}).get("okf_type") or "Memory"
        if okf_type not in _INDUCTION_SOURCE_TYPES:
            continue
        for tag in sorted(set(it.tags or [])):
            buckets.setdefault((okf_type, tag), []).append(it)

    clusters: list[list[MemoryItem]] = []
    seen_keys: set[frozenset[str]] = set()
    for key in sorted(buckets):
        members = sorted(buckets[key], key=lambda i: i.item_id)
        if len(members) < min_cluster:
            continue
        id_key = frozenset(m.item_id for m in members)
        if id_key in seen_keys:
            continue
        seen_keys.add(id_key)
        clusters.append(members)
    return clusters


def _run_induction(
    items: list[MemoryItem],
    store: MemoryStore,
    client: Any,
    *,
    max_calls: int,
    model: str,
    session_id: str,
    now: float,
    min_cluster: int = _INDUCTION_MIN_CLUSTER,
) -> InductionResult:
    """ADR-dreaming-028 §3 — the induction / generalizer pass (CREATE-only).

    Reads post-deduction survivors, clusters related lower-durability cards
    (:func:`_cluster_for_induction`), and for each cluster (up to ``max_calls``)
    asks the LLM to synthesize ONE durable lesson. Each accepted synthesis is
    written as a NEW :class:`MemoryItem` typed Invariant/Convention with
    ``metadata.synthesized_from`` provenance naming the sources.

    Authority boundary (the load-bearing piece of the deduction/induction
    split): this function only ever calls ``store.write`` — never
    ``store.delete``. It cannot retire any card.

    Fail-open per [ADR-harness-006]: every LLM failure mode (exception, empty
    completion, parse error, bad/missing synthesis, invalid type) becomes an
    event + continue, never raises.
    """
    if not items or max_calls <= 0:
        return InductionResult(
            created=[], llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, clusters_examined=0,
        )

    from ..cost import cost_of
    from .redaction import redact

    clusters = _cluster_for_induction(items, min_cluster=min_cluster)
    emit("dream.induction_started", n_clusters=len(clusters), max_calls=max_calls)
    if not clusters:
        return InductionResult(
            created=[], llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, clusters_examined=0,
        )

    system_prompt = _get_induction_system_prompt()
    created: list[InductionCard] = []
    llm_calls = 0
    total_tokens_in = 0
    total_tokens_out = 0
    clusters_examined = 0

    for cluster in clusters:
        if llm_calls >= max_calls:
            break
        clusters_examined += 1
        cluster_idx = clusters_examined - 1

        payload = json.dumps([
            {
                "id": str(redact(it.item_id)),
                "content": str(redact(it.content)) if it.content is not None else "",
                "timestamp": it.timestamp,
                "tags": [str(redact(t)) for t in it.tags],
            }
            for it in cluster
        ])
        wrapped = _wrap_batch_in_envelope(
            payload, session_id=session_id, now=now, batch_idx=cluster_idx,
        )

        try:
            completion = client.complete(
                wrapped, system=system_prompt, max_tokens=_INDUCTION_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open on any exception
            emit(
                "dream.induction_skipped_unavailable_llm",
                cluster_index=cluster_idx,
                reason=f"{type(exc).__name__}: {exc}",
            )
            llm_calls += 1
            continue
        llm_calls += 1
        if not completion.text:
            emit(
                "dream.induction_skipped_unavailable_llm",
                cluster_index=cluster_idx, reason="empty completion text",
            )
            continue

        try:
            data = json.loads(completion.text)
        except json.JSONDecodeError as exc:
            emit("dream.induction_batch_parse_failed", cluster_index=cluster_idx, reason=str(exc))
            continue
        if not isinstance(data, dict) or "synthesis" not in data:
            emit(
                "dream.induction_batch_parse_failed",
                cluster_index=cluster_idx, reason="missing or bad 'synthesis' key",
            )
            continue

        total_tokens_in += completion.tokens_in
        total_tokens_out += completion.tokens_out

        synthesis = data["synthesis"]
        if synthesis is None:
            emit("dream.induction_synthesis_rejected", cluster_index=cluster_idx, reason="llm returned null")
            continue
        if not isinstance(synthesis, dict):
            emit(
                "dream.induction_batch_parse_failed",
                cluster_index=cluster_idx, reason="'synthesis' not an object",
            )
            continue

        okf_type = synthesis.get("type")
        content = synthesis.get("content")
        rationale = synthesis.get("rationale", "")
        if okf_type not in _INDUCTION_TARGET_TYPES:
            emit("dream.induction_invalid_type_dropped", cluster_index=cluster_idx, okf_type=str(okf_type))
            continue
        if not isinstance(content, str) or not content.strip():
            emit(
                "dream.induction_batch_parse_failed",
                cluster_index=cluster_idx, reason="empty or non-string content",
            )
            continue
        if not isinstance(rationale, str):
            rationale = ""

        source_ids = tuple(sorted(it.item_id for it in cluster))
        # Deterministic id derived from the source set + target type, so re-running
        # induction over an unchanged cluster does not mint a second copy.
        digest = hashlib.sha1(
            (" ".join(source_ids) + "|" + okf_type).encode("utf-8")
        ).hexdigest()[:16]
        new_id = f"induct-{digest}"

        # Idempotency: if a synthesized card with this id already exists, skip the
        # write (re-running the dream cycle must not duplicate syntheses).
        if store.get(new_id) is not None:
            emit("dream.induction_synthesis_rejected", cluster_index=cluster_idx, reason="already synthesized")
            continue

        merged_tags = sorted({t for it in cluster for t in (it.tags or [])})
        new_item = MemoryItem(
            item_id=new_id,
            content=content[:_INDUCTION_CONTENT_MAX_LEN],
            timestamp=now,
            source="dream-induction",
            session_id=session_id,
            tags=merged_tags,
            metadata={
                "okf_type": okf_type,
                "synthesized_from": list(source_ids),
                "induction_rationale": rationale[:_RATIONALE_MAX_LEN],
            },
        )
        # CREATE-only: the single store mutation this pass is permitted to make.
        store.write(new_item)
        card = InductionCard(
            item_id=new_id,
            okf_type=okf_type,
            synthesized_from=source_ids,
            rationale=rationale[:_RATIONALE_MAX_LEN],
        )
        created.append(card)
        emit(
            "dream.induction_card_emitted",
            item_id=new_id,
            okf_type=okf_type,
            n_sources=len(source_ids),
            cluster_index=cluster_idx,
        )

    total_cost = cost_of(model, total_tokens_in, total_tokens_out)
    created.sort(key=lambda c: c.item_id)
    return InductionResult(
        created=created,
        llm_calls=llm_calls,
        tokens_in=total_tokens_in,
        tokens_out=total_tokens_out,
        cost_usd=total_cost,
        clusters_examined=clusters_examined,
    )


def _dedup_first_seen(tags: list[GovernanceTag]) -> list[GovernanceTag]:
    """Within-class dedup. First-seen by ``item_id`` wins; silent (no event)."""
    seen: set[str] = set()
    out: list[GovernanceTag] = []
    for t in tags:
        if t.item_id not in seen:
            seen.add(t.item_id)
            out.append(t)
    return out


def _resolve_governance_collisions(
    raw_must_know: list[GovernanceTag],
    raw_must_do: list[GovernanceTag],
    raw_blacklisted: list[GovernanceTag],
    *,
    protected_ids: set[str],
) -> tuple[list[GovernanceTag], list[GovernanceTag], list[GovernanceTag]]:
    """Apply cross-class precedence → protected-id drops → within-class dedup.

    Per halliday B2: emits a SINGLE unified ``dream.governance_classification_dropped``
    event with ``reason ∈ {"protected","collision"}`` (replaces two parallel event
    names from the original plan).

    Precedence: ``must_know > must_do > blacklist`` (conservative; never delete an
    item the LLM also flagged as important — Pushback A).

    Ordering pinned by rubric §F-J3-resolver-ordering (halliday A5):
      1. Cross-class precedence first (so blacklist on a must_know id emits
         ``reason="collision"``, NOT ``reason="protected"``).
      2. Protected-id drops next (applies only to blacklist entries that
         survived precedence).
      3. Within-class dedup last (silent; first-seen wins).
    """
    must_know_ids = {t.item_id for t in raw_must_know}

    # Step 1: cross-class precedence — must_do entries colliding with must_know.
    surviving_must_do: list[GovernanceTag] = []
    for t in raw_must_do:
        if t.item_id in must_know_ids:
            emit(
                "dream.governance_classification_dropped",
                item_id=t.item_id,
                dropped_class="must_do",
                reason="collision",
                kept_class="must_know",
            )
        else:
            surviving_must_do.append(t)

    surviving_must_do_ids = {t.item_id for t in surviving_must_do}

    # Step 1 continued: blacklist colliding with must_know (priority 1) or must_do (priority 2).
    surviving_blacklisted: list[GovernanceTag] = []
    for t in raw_blacklisted:
        if t.item_id in must_know_ids:
            emit(
                "dream.governance_classification_dropped",
                item_id=t.item_id,
                dropped_class="blacklist",
                reason="collision",
                kept_class="must_know",
            )
        elif t.item_id in surviving_must_do_ids:
            emit(
                "dream.governance_classification_dropped",
                item_id=t.item_id,
                dropped_class="blacklist",
                reason="collision",
                kept_class="must_do",
            )
        else:
            surviving_blacklisted.append(t)

    # Step 2: protected-id drops (blacklist only — must_know/must_do can co-exist with winners).
    after_protected_blacklist: list[GovernanceTag] = []
    for t in surviving_blacklisted:
        if t.item_id in protected_ids:
            emit(
                "dream.governance_classification_dropped",
                item_id=t.item_id,
                dropped_class="blacklist",
                reason="protected",
            )
        else:
            after_protected_blacklist.append(t)

    # Step 3: within-class dedup (silent; first-seen wins).
    final_must_know = _dedup_first_seen(raw_must_know)
    final_must_do = _dedup_first_seen(surviving_must_do)
    final_blacklisted = _dedup_first_seen(after_protected_blacklist)

    return final_must_know, final_must_do, final_blacklisted


_GOVERNANCE_CLASSES: frozenset[str] = frozenset({"none", "must_know", "must_do", "blacklist"})


def _detect_governance(
    items: list[MemoryItem],
    client: Any,
    *,
    batch_size: int,
    max_calls: int,
    model: str,
    session_id: str,
    now: float,
    protected_ids: set[str] | None = None,
) -> GovernanceResult:
    """LLM-driven governance classification over the post-TTL/post-dedup/post-contradiction set.

    Per halliday B2: ``protected_ids`` is accepted in the signature for API
    symmetry with ``_detect_contradictions``, but the drop logic now lives
    entirely in ``_resolve_governance_collisions`` (called by the worker).
    This function returns the RAW LLM verdict — the resolver applies precedence,
    protected drops, and dedup downstream.

    Algorithm:
      1. Empty items OR ``max_calls <= 0`` → empty result (no event).
      2. Hour-bucketed shuffle seeded by ``sha256(session_id || hour_bucket || 'gov')``.
      3. NON-OVERLAPPING window: each item in at most one batch per run.
      4. Per batch: redact + JSON-serialize + envelope-wrap + ``client.complete()``.
      5. Empty completion / exception → ``dream.governance_skipped_unavailable_llm``.
      6. JSON parse failure → ``dream.governance_batch_parse_failed``.
      7. Per classification: validate types + class ∈ {none, must_know, must_do, blacklist}
         + ``item_id`` ∈ batch id-set (else ``dream.governance_invalid_id_dropped``).
         ``"none"`` is the conservative default — kept but added to NO class list.
      8. ``dream.governance_partial_parse`` if any drops.
      9. ``dream.governance_batch_complete`` per successful batch with
         ``n_classifications`` = RAW LLM count (PRE-resolver-drop per halliday B2).
     10. ``dream.governance_call_cap_reached`` if cap > 0 AND batches still pending.
    """
    if not items or max_calls <= 0:
        return GovernanceResult(
            must_know=[], must_do=[], blacklisted=[],
            llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, items_examined_estimate=0,
        )

    # Lazy imports (rubric §J-J3-2 / §J-J3-3).
    from ..cost import cost_of
    from .redaction import redact

    # Step 2: deterministic shuffle, hour-bucketed seed with 'gov' discriminator.
    hour_bucket = int(now // _SECONDS_PER_HOUR)
    seed_str = f"{session_id}|{hour_bucket}|gov"
    seed_int = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed_int)
    shuffled = list(items)
    rng.shuffle(shuffled)

    # Step 3: non-overlapping batches.
    total_batches_needed = (len(shuffled) + batch_size - 1) // batch_size
    batches_to_run = min(max_calls, total_batches_needed)
    batches_skipped = total_batches_needed - batches_to_run

    raw_must_know: list[GovernanceTag] = []
    raw_must_do: list[GovernanceTag] = []
    raw_blacklisted: list[GovernanceTag] = []

    llm_calls = 0
    total_tokens_in = 0
    total_tokens_out = 0
    items_examined = 0

    for batch_idx in range(batches_to_run):
        batch = shuffled[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        batch_id_set = {it.item_id for it in batch}

        # Step 4: redact every per-item value, JSON-serialize.
        batch_payload = json.dumps([
            {
                "id": str(redact(it.item_id)),
                "content": str(redact(it.content)) if it.content is not None else "",
                "timestamp": it.timestamp,
                "tags": [str(redact(t)) for t in it.tags],
            }
            for it in batch
        ])
        wrapped = _wrap_governance_batch_in_envelope(
            batch_payload, session_id=session_id, now=now, batch_idx=batch_idx,
        )
        system_prompt = _get_governance_system_prompt()

        # Step 5: fail-open on empty completion OR exception.
        try:
            completion = client.complete(
                wrapped, system=system_prompt, max_tokens=_GOVERNANCE_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 — Pushback H: fail-open inherited
            emit(
                "dream.governance_skipped_unavailable_llm",
                batch_index=batch_idx,
                reason=f"{type(exc).__name__}: {exc}",
            )
            llm_calls += 1
            continue

        llm_calls += 1
        if not completion.text:
            emit(
                "dream.governance_skipped_unavailable_llm",
                batch_index=batch_idx,
                reason="empty completion text",
            )
            continue

        # Step 6: parse JSON, fail-open on parse error.
        try:
            data = json.loads(completion.text)
        except json.JSONDecodeError as exc:
            emit(
                "dream.governance_batch_parse_failed",
                batch_index=batch_idx,
                reason=str(exc),
            )
            continue
        if not isinstance(data, dict) or "classifications" not in data:
            emit(
                "dream.governance_batch_parse_failed",
                batch_index=batch_idx,
                reason="missing or bad 'classifications' key",
            )
            continue
        raw_classifications = data["classifications"]
        if not isinstance(raw_classifications, list):
            emit(
                "dream.governance_batch_parse_failed",
                batch_index=batch_idx,
                reason=f"'classifications' not list: {type(raw_classifications).__name__}",
            )
            continue

        # Step 7: per-classification validation + accumulation.
        n_kept = 0
        n_dropped = 0
        for raw in raw_classifications:
            if not isinstance(raw, dict):
                n_dropped += 1
                continue
            item_id = raw.get("item_id")
            cls = raw.get("class")
            rationale = raw.get("rationale", "")
            if not isinstance(item_id, str) or not isinstance(cls, str):
                n_dropped += 1
                continue
            if not isinstance(rationale, str):
                rationale = ""
            if cls not in _GOVERNANCE_CLASSES:
                n_dropped += 1
                continue
            if item_id not in batch_id_set:
                # Hallucinated id — emit with the LLM's claimed class for forensics.
                # 'class' is a Python keyword so passed via dict-unpack.
                emit(
                    "dream.governance_invalid_id_dropped",
                    batch_index=batch_idx,
                    item_id=item_id,
                    **{"class": cls},
                )
                continue
            if cls == "none":
                # Conservative default — counts as kept; added to NO class list.
                n_kept += 1
                continue
            tag = GovernanceTag(
                item_id=item_id,
                rationale=rationale[:_RATIONALE_MAX_LEN],
                batch_index=batch_idx,
            )
            if cls == "must_know":
                raw_must_know.append(tag)
            elif cls == "must_do":
                raw_must_do.append(tag)
            else:  # cls == "blacklist"
                raw_blacklisted.append(tag)
            n_kept += 1

        if n_dropped:
            emit(
                "dream.governance_partial_parse",
                batch_index=batch_idx,
                n_kept=n_kept,
                n_dropped=n_dropped,
            )

        # Step 9: per-batch cost emit — n_classifications is RAW LLM count (PRE-drop).
        batch_cost = cost_of(model, completion.tokens_in, completion.tokens_out)
        emit(
            "dream.governance_batch_complete",
            batch_index=batch_idx,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            cost_usd=batch_cost,
            n_classifications=len(raw_classifications),
        )

        total_tokens_in += completion.tokens_in
        total_tokens_out += completion.tokens_out
        items_examined += len(batch)

    # Step 10: cap-reached event (only when max_calls > 0 AND cap hit).
    if max_calls > 0 and batches_skipped > 0:
        items_skipped = max(0, len(shuffled) - batches_to_run * batch_size)
        emit(
            "dream.governance_call_cap_reached",
            max_calls=max_calls,
            batches_completed=batches_to_run,
            batches_skipped=batches_skipped,
            items_skipped=items_skipped,
        )

    total_cost = cost_of(model, total_tokens_in, total_tokens_out)
    return GovernanceResult(
        must_know=raw_must_know,
        must_do=raw_must_do,
        blacklisted=raw_blacklisted,
        llm_calls=llm_calls,
        tokens_in=total_tokens_in,
        tokens_out=total_tokens_out,
        cost_usd=total_cost,
        items_examined_estimate=items_examined,
    )


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
                    "Dream worker pass outputs not pairwise disjoint: "
                    f"{named_sets[i][0]} ∩ {named_sets[j][0]} = {sorted(overlap)!r}"
                )


class DreamingWorker:
    """Offline memory-consolidation engine — Jobs 1 (dedup) + 4 (TTL) + 2 (contradiction) + 3 (governance)."""

    def __init__(self, store: MemoryStore) -> None:
        """Bind the worker to the ``MemoryStore`` it will read + mutate during ``run()``."""
        self.store = store

    def run(self, *, trajectories_path: str | None = None, **kwargs: Any) -> dict:
        """One pass through all four ADR-002 jobs; returns the summary dict.

        Order of operations (TTL → dedup → contradiction → governance;
        JOB1 §F12 + JOB4 §F-TTL-13 + JOB2 §F-J2-3 + JOB3 §F-J3 — all
        deletes complete BEFORE the single ``dream.summary`` emit):

         1. Reject truthy ``trajectories_path`` BEFORE any lock or store access.
         2. NFS detection BEFORE basedir lock.
         3. Acquire basedir flock.
         4. Walk ``store.all()``.
         5. TTL pass: select pruned ids; ``self.store.delete()`` each (Job 4).
         6. Re-scan surviving items + cluster by normalized content.
         7. Dedup pass: pick winner per cluster; ``self.store.delete()`` losers (Job 1).
         8. Contradiction pass: batch surviving items through LLM;
            ``self.store.delete()`` losers (Job 2).
         9. Governance pass: batch surviving items through LLM; resolve
            class collisions + protected-id drops; ``self.store.delete()``
            blacklist tags (Job 3). must_know/must_do are SOFT (advisory) —
            no mutation; surfaced in the ``governance`` summary block.
        10. Advisory backstop (halliday B5): if a refactor breaks the
            ``must_know ⊥ blacklisted`` resolver guarantee, emit
            ``dream.governance_advisory_invariant_violated`` and drop the
            blacklist.
        11. Pairwise-disjoint hard check over 5 mutation-affecting sets;
            raise ``RuntimeError`` on violation.
        12. Build the summary dict from the completed deletes.
        13. Emit ``dream.summary``.
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
            # ADR-028 §2 — route the store read through `_iter_store_pages` so
            # backends opting into `iter_pages()` get cursor-based streaming;
            # backends without the override fall through to today's `.all()`.
            # The worker still materializes the full list in this PR — the
            # passes downstream haven't moved to page-by-page yet, that's
            # follow-up work. The protocol surface change is what's new here.
            items: list[MemoryItem] = []
            for _page in _iter_store_pages(self.store):
                items.extend(_page)
            total_items = len(items)

            retention_days = _read_item_retention_days()
            # `retention_seconds` is now a back-compat-only summary field:
            # the value that pre-V5 ("Memory"-typed) items use, which matches
            # today's flat default. Per-item retention comes from
            # ``TYPE_RETENTION_DAYS`` via ``_pick_pruned`` (ADR-028 §1).
            retention_seconds = _DEFAULT_ITEM_RETENTION_DAYS * _SECONDS_PER_DAY
            max_calls = _read_contradiction_max_calls()
            max_governance_calls = _read_governance_max_calls()

            # JOB4 §D-TTL-4 cardinality contract: at most one _now() call per run.
            # TTL (Job 4) + contradiction (Job 2) + governance (Job 3) share the cached value.
            now_cached: float = 0.0
            if retention_days > 0 or max_calls > 0 or max_governance_calls > 0:
                now_cached = _now()

            # JOB4 pin #9 / ADR-028 §1: retention_days == 0 (env kill-switch)
            # disables TTL pruning entirely. Otherwise `_pick_pruned` reads
            # the per-type retention from TYPE_RETENTION_DAYS — durable types
            # (Identity/Convention/Invariant/Workaround/Bug/Contradiction)
            # never age out; Decision/Preference/Fix have their own values;
            # Memory falls back to the v1 default.
            if retention_days == 0:
                pruned_ids: list[str] = []
            else:
                pruned_ids = _pick_pruned(items, now_cached)

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

            # Construct the LLM client once and reuse across dedup +
            # contradiction + governance (test_make_llm_client_called_once_and_
            # reused_across_both_passes is the existing pin on this
            # optimization; the flip-on-trip default for the dedup pass means
            # the construction now feeds three consumers instead of two).
            llm_client = _make_llm_client()

            # ── JOB 1.5 LLM dedup pre-pass (ADR-028 §2 PR #2f, default ON #2h) ──
            # Kill switch: `DREAM_DEDUP_NEIGHBORHOOD=0` falls back to lexical-
            # only dedup. Runs AFTER lexical dedup (so it never re-judges
            # items already retired) and BEFORE contradiction (so its retired
            # ids flow into `cluster_winners_set ∪ dedup_loser_set` protected
            # from contradiction-loser-collision). Catches paraphrase clusters
            # lexical normalize misses; semantically additive to lexical dedup,
            # not a replacement.
            if _read_use_neighborhood_dedup():
                _lexical_dedup_survivors = [
                    it for it in items
                    if it.item_id not in pruned_set and it.item_id not in retired_ids_set
                ]
                # Share the contradiction call budget for now — separate
                # `DREAM_DEDUP_MAX_CALLS` knob is a follow-up if A/B
                # measurement shows a need to tune the two independently.
                _dedup_max_calls = _read_contradiction_max_calls()
                _dedup_result = _detect_duplicates_neighborhood(
                    _lexical_dedup_survivors,
                    self.store,
                    llm_client,
                    max_calls=_dedup_max_calls,
                    model=getattr(llm_client, "model", "unknown"),
                    session_id=_session_id_for_dream(basedir),
                    now=now_cached,
                    protected_ids={c["winner_id"] for c in cluster_specs},
                )
                for _dpair in _dedup_result.pairs:
                    self.store.delete(_dpair.loser_id)
                    retired_ids_set.add(_dpair.loser_id)

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
            # ADR-028 §2 PR #2d — switch between the v1 shuffle-batch
            # contradiction path and the v2 per-pivot neighborhood path.
            # Default ON as of the flip-on-trust decision (PR #2h, post-#2g);
            # `DREAM_CONTRADICTION_NEIGHBORHOOD=0` falls back to v1 as a
            # kill switch. The two v2 paths shipped without an A/B; if a
            # regression surfaces, flip this env to "0" while we measure.
            if _read_use_neighborhood_contradiction():
                contradiction_result = _detect_contradictions_neighborhood(
                    contradiction_survivors,
                    self.store,
                    llm_client,
                    max_calls=max_calls,
                    model=getattr(llm_client, "model", "unknown"),
                    session_id=_session_id_for_dream(basedir),
                    now=now_cached,
                    protected_ids=cluster_winners_set,
                )
            else:
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

            # JOB2 §F-J2-3: contradiction deletes complete BEFORE governance pass.
            contradicted_loser_ids: set[str] = set()
            for pair in contradiction_result.pairs:
                self.store.delete(pair.loser_id)
                contradicted_loser_ids.add(pair.loser_id)

            # ── JOB 3 governance pass ───────────────────────────────────────
            # Working set excludes all prior-pass retirements.
            governance_survivors = [
                it for it in items
                if it.item_id not in pruned_set
                and it.item_id not in retired_ids_set
                and it.item_id not in contradicted_loser_ids
            ]
            # Protected ids = cluster_winners ∪ contradiction_winners. The
            # resolver drops blacklist tags targeting protected ids (halliday B2);
            # must_know / must_do on protected ids are KEPT (no mutation).
            contradiction_winners_set: set[str] = {
                p.winner_id for p in contradiction_result.pairs
            }
            protected_for_governance: set[str] = (
                cluster_winners_set | contradiction_winners_set
            )
            governance_raw = _detect_governance(
                governance_survivors,
                llm_client,
                batch_size=_GOVERNANCE_BATCH_SIZE,
                max_calls=max_governance_calls,
                model=getattr(llm_client, "model", "unknown"),
                session_id=_session_id_for_dream(basedir),
                now=now_cached,
                protected_ids=protected_for_governance,
            )

            # Resolver (halliday B2): precedence → protected-id drops → dedup;
            # emits single unified `dream.governance_classification_dropped`.
            (
                resolved_must_know,
                resolved_must_do,
                resolved_blacklisted,
            ) = _resolve_governance_collisions(
                governance_raw.must_know,
                governance_raw.must_do,
                governance_raw.blacklisted,
                protected_ids=protected_for_governance,
            )

            # JOB3 §F-J3-advisory-backstop (halliday B5): the resolver guarantees
            # must_know_ids ⊥ blacklisted_ids and must_do_ids ⊥ blacklisted_ids
            # by construction. This backstop catches refactor drift — runs BEFORE
            # the delete loop so violating ids never reach `self.store.delete`
            # (otherwise the store is left in a partial-mutation state). Emits a
            # WARNING event (not RuntimeError, since advisory drift is not a
            # mutation-correctness failure that requires aborting the run).
            must_know_ids_set = {t.item_id for t in resolved_must_know}
            must_do_ids_set = {t.item_id for t in resolved_must_do}
            resolved_blacklisted_ids = {t.item_id for t in resolved_blacklisted}
            adv_violations = (must_know_ids_set | must_do_ids_set) & resolved_blacklisted_ids
            for vid in sorted(adv_violations):
                advisory_class = "must_know" if vid in must_know_ids_set else "must_do"
                emit(
                    "dream.governance_advisory_invariant_violated",
                    item_id=vid,
                    advisory_class=advisory_class,
                    dropped_blacklist=True,
                )
            safe_blacklisted = [
                t for t in resolved_blacklisted if t.item_id not in adv_violations
            ]

            # JOB3 §F-J3-3 + §I-J3-blacklisted-per-id (halliday B3 + A3): mutate
            # ONLY for blacklist tags that survived the advisory backstop; filter
            # to delete-True; per-id audit emit.
            delete_succeeded: list[GovernanceTag] = []
            for tag in safe_blacklisted:
                if self.store.delete(tag.item_id):
                    delete_succeeded.append(tag)
                    emit(
                        "dream.governance_blacklisted",
                        item_id=tag.item_id,
                        rationale=tag.rationale,
                        batch_index=tag.batch_index,
                    )
                else:
                    emit(
                        "dream.governance_blacklist_delete_failed",
                        item_id=tag.item_id,
                        rationale=tag.rationale,
                    )

            blacklisted_ids_set: set[str] = {t.item_id for t in delete_succeeded}

            # JOB3 §C-J3-disjoint + halliday B5: 5-set mutation-disjoint check.
            # Advisory sets (must_know_ids / must_do_ids) are NOT in the check —
            # they may overlap with all_winners (advisory is non-mutating).
            all_winners = (
                {p.winner_id for p in contradiction_result.pairs}
                | {c["winner_id"] for c in cluster_specs}
            )
            _disjointness_check([
                ("pruned_ids", pruned_set),
                ("retired_ids", retired_ids_set),
                ("contradicted_loser_ids", contradicted_loser_ids),
                ("blacklisted_ids", blacklisted_ids_set),
                ("all_winners", all_winners),
            ])

            # ── ADR-028 §3 induction (generalizer) — CREATE-only, DEFAULT OFF ──
            # Runs AFTER all deduction deletes complete (it generalizes from the
            # post-deduction survivors) and AFTER the disjointness check (its new
            # ids are fresh `induct-*` cards, never part of any delete set, so it
            # cannot affect the mutation-disjoint invariant). Gated by
            # `DREAM_INDUCTION=1` with its own `DREAM_INDUCTION_MAX_CALLS` budget.
            induction_result = InductionResult(
                created=[], llm_calls=0, tokens_in=0, tokens_out=0,
                cost_usd=0.0, clusters_examined=0,
            )
            if _read_use_induction():
                induction_survivors = [
                    it for it in items
                    if it.item_id not in pruned_set
                    and it.item_id not in retired_ids_set
                    and it.item_id not in contradicted_loser_ids
                    and it.item_id not in blacklisted_ids_set
                ]
                induction_result = _run_induction(
                    induction_survivors,
                    self.store,
                    llm_client,
                    max_calls=_read_induction_max_calls(),
                    model=getattr(llm_client, "model", "unknown"),
                    session_id=_session_id_for_dream(basedir),
                    now=now_cached if now_cached else _now(),
                )

            duplicate_clusters = len(cluster_specs)
            items_in_duplicates = sum(c["count"] for c in cluster_specs)
            items_retired = sum(len(c["retired_ids"]) for c in cluster_specs)
            items_pruned = len(pruned_ids)
            items_contradicted = len(contradiction_result.pairs)
            items_blacklisted = len(delete_succeeded)
            items_must_known = len(resolved_must_know)
            items_must_done = len(resolved_must_do)

            # Summary lists sorted by item_id ascending (§B19/B20/B21).
            sorted_must_know = sorted(resolved_must_know, key=lambda t: t.item_id)
            sorted_must_do = sorted(resolved_must_do, key=lambda t: t.item_id)
            sorted_blacklisted = sorted(delete_succeeded, key=lambda t: t.item_id)

            summary = {
                "schema": "dream.summary",
                "version": 1,
                "mode": "detection_and_mutation_and_pruning_and_contradiction_and_governance",
                "jobs_run": [
                    "dedup_detection",
                    "dedup_merge",
                    "ttl_pruning",
                    "contradiction_resolution",
                    "governance",
                ],
                "skipped_jobs": [],
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
                    "items_blacklisted": items_blacklisted,
                    "items_must_known": items_must_known,
                    "items_must_done": items_must_done,
                    "governance_llm_calls": governance_raw.llm_calls,
                    "governance_input_tokens": governance_raw.tokens_in,
                    "governance_output_tokens": governance_raw.tokens_out,
                    "governance_cost_usd_estimate": governance_raw.cost_usd,
                    "governance_items_examined_estimate": governance_raw.items_examined_estimate,
                    "items_synthesized": len(induction_result.created),
                    "induction_llm_calls": induction_result.llm_calls,
                    "induction_input_tokens": induction_result.tokens_in,
                    "induction_output_tokens": induction_result.tokens_out,
                    "induction_cost_usd_estimate": induction_result.cost_usd,
                    "induction_clusters_examined": induction_result.clusters_examined,
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
                "governance": {
                    "must_know": [
                        {"item_id": t.item_id, "rationale": t.rationale}
                        for t in sorted_must_know
                    ],
                    "must_do": [
                        {"item_id": t.item_id, "rationale": t.rationale}
                        for t in sorted_must_do
                    ],
                    "blacklisted": [
                        {"item_id": t.item_id, "rationale": t.rationale}
                        for t in sorted_blacklisted
                    ],
                    "model": getattr(llm_client, "model", "unknown"),
                },
                "synthesized": {
                    "cards": [
                        {
                            "item_id": c.item_id,
                            "okf_type": c.okf_type,
                            "synthesized_from": list(c.synthesized_from),
                            "rationale": c.rationale,
                        }
                        for c in induction_result.created
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
                items_blacklisted=items_blacklisted,
                items_must_known=items_must_known,
                items_must_done=items_must_done,
                governance_llm_calls=governance_raw.llm_calls,
                governance_input_tokens=governance_raw.tokens_in,
                governance_output_tokens=governance_raw.tokens_out,
                governance_cost_usd_estimate=governance_raw.cost_usd,
                governance_items_examined_estimate=governance_raw.items_examined_estimate,
                items_synthesized=len(induction_result.created),
                induction_llm_calls=induction_result.llm_calls,
                induction_cost_usd_estimate=induction_result.cost_usd,
            )

            return summary


def dream(store: MemoryStore, **kwargs: Any) -> dict:
    """Convenience: run one :class:`DreamingWorker` pass over ``store``."""
    return DreamingWorker(store).run(**kwargs)


__all__ = [
    "DreamingWorker",
    "dream",
    "ContradictionPair",
    "ContradictionResult",
    "GovernanceTag",
    "GovernanceResult",
    "InductionCard",
    "InductionResult",
]
