---
id: ADR-dreaming-015
domain: dreaming
title: Per-session filesystem state — Python path resolution + uniform retention TTL
status: Accepted
date: 2026-06-21
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-21 (halliday adversarial-review Findings #8 + #9)
---

# ADR-dreaming-015: Per-session filesystem state — Python path resolution + uniform retention TTL

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
Four dreaming-domain ADRs put per-session state files on disk under a
common `<basedir>/dream/` directory:

- [`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md) — sidecar
  state JSON.
- [`ADR-dreaming-009`](ADR-dreaming-009-events-shim.md) —
  `*.daydream-events.jsonl` diary.
- [`ADR-dreaming-011`](ADR-dreaming-011-expanded-redaction-scope.md) —
  `*.redact-audit.jsonl` audit file.
- [`ADR-dreaming-014`](ADR-dreaming-014-concurrent-daydream-flock.md) —
  `*.lock` advisory lock files.

Halliday (2026-06-21) flagged two operational gaps:

- **Finding #9 (MED) — `${MEMORY_STORE%/*}` shell syntax in Python
  contexts** is fragile. If `MEMORY_STORE` is a directory (not a file),
  `%/*` returns the wrong path. If it has no slash, `%/*` returns the
  original. Trailing-slash semantics are undefined across bash vs
  Python `.parent`. Python consumers need an unambiguous resolution
  rule.
- **Finding #8 (MED) — unbounded growth.** At N=10k sessions, the dir
  contains 10k+ files of each class (40k+ total) with no cleanup
  policy. Inode pressure becomes real on local FS; backup tools choke;
  operators don't notice (everything is gitignored).

## Options considered
- **Single ADR covering basedir resolution + uniform retention TTL +
  sweeper-on-invocation** (chosen) — same surface, same operational
  story, one rule to learn.
- Per-file-class TTLs (different retention per file type) — more
  complex, no clear reason different files need different retention.
- Cleanup via external cron — operationally heavier; couples to OS
  scheduler; doesn't work on systems without cron.
- Indefinite retention with periodic operator cleanup — matches v1's
  personal-machine assumption but breaks at scale and surprises
  operators.

## Decision

### 1. `<basedir>` Python resolution rule
```python
def resolve_basedir() -> Path:
    """Return the directory that holds <basedir>/dream/."""
    memstore = Path(os.environ["MEMORY_STORE"]).resolve()
    if not memstore.exists():
        raise FileNotFoundError(
            f"MEMORY_STORE points to non-existent path: {memstore}"
        )
    if memstore.is_dir():
        raise ValueError(
            f"MEMORY_STORE must point to a file, not a directory: {memstore}"
        )
    return memstore.parent
```

- `MEMORY_STORE` must point to a file (per the existing
  [`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md)
  assumption, now enforced).
- All `<basedir>/dream/` references in dreaming-domain ADRs are
  computed via this function. No more shell parameter expansion in
  Python contexts.
- `<basedir>/dream/` is created via `mkdir(parents=True,
  exist_ok=True)` on first use.

### 2. Uniform 30-day retention TTL
All per-session state files in `<basedir>/dream/` are subject to a
single TTL: **30 days from mtime**. Applies to:
- `*.json` (sidecar state per ADR-harness-004)
- `*.daydream-events.jsonl` (per ADR-009)
- `*.redact-audit.jsonl` (per ADR-011)
- `*.lock` (per ADR-014 — but with caveat below)

For `*.lock`, "30 days" is a hard ceiling. A lock file older than
**24 hours** is presumed stale (the holding process has died); the
acquirer reclaims it via the stale-lock protocol that ADR-014's open
items track.

### 3. Sweeper-on-invocation, throttled
Each Daydream invocation triggers a one-shot sweep of expired files in
`<basedir>/dream/` — but throttled: the sweeper runs at most once per
60 minutes per `<basedir>` (via mtime-check of a `.last-swept` marker
file in `<basedir>/dream/`). This keeps Stop-hook latency from
spiking on slow filesystems with many old files.

### 4. Env-var overrides
- `DREAM_RETENTION_DAYS` (int, default `30`) — overrides the TTL.
- `DREAM_SWEEP_INTERVAL_MIN` (int, default `60`) — overrides the
  sweeper throttle.

## Rationale
Defining `<basedir>` in Python removes a class of bugs (shell-syntax-
in-Python is brittle and surprises new contributors). A uniform TTL is
the simplest possible retention story; if a future use case needs per-
class TTLs, a successor ADR can split. Sweeper-on-invocation requires
no external cron, no daemon, and no operator action — the system
cleans itself as it runs. Throttling prevents the cleanup from
blowing up Stop-hook latency.

## Tradeoffs & risks
- **Requires `MEMORY_STORE` to point to a file** — enforces an
  assumption that's been implicit; downstream users who passed a
  directory will see a clear error rather than silent wrong-path
  behavior. Acceptable upgrade.
- **30 days might be wrong for some setups** — too long for tiny
  disks, too short for forensics. Mitigated by `DREAM_RETENTION_DAYS`
  override.
- **Sweeper-on-invocation adds latency on slow filesystems** — 60-
  minute throttle is the mitigation; can be lowered via
  `DREAM_SWEEP_INTERVAL_MIN` for high-volume users who'd rather pay
  more often for smaller chunks.
- **Sweeper runs inside the Daydream process** — failure in the
  sweeper is fail-open per
  [`ADR-harness-006`](ADR-harness-006-fail-open.md) (log + skip; never
  break the agent's session). Sweep failure does NOT abort the chunk.
- **Doesn't address backup-tool / git-ignore visibility** — files are
  still gitignored per ADR-009/ADR-011; operators looking at disk
  usage will see them. No change.

## Consequences for the build
- **Contract — source of truth:** `resolve_basedir()` and the sweeper
  function in the dreaming package (likely
  `eval/memeval/dreaming/_state.py` alongside the sidecar helpers).
- **Shape:**
  ```python
  def resolve_basedir() -> Path: ...
  def sweep_old_state(basedir: Path, *, ttl_days: int = 30,
                      throttle_min: int = 60) -> int: ...  # returns # files deleted
  ```
- **Policy — every state-file write site** uses
  `resolve_basedir()` rather than constructing the path via
  `${MEMORY_STORE%/*}` or hand-rolled `Path` arithmetic.
- **Policy — Daydream invocation triggers `sweep_old_state()`** before
  acquiring the per-session lock; sweep is throttled per the marker
  file; sweep failure is fail-open.
- **Policy — defaults are env-overridable**:
  `DREAM_RETENTION_DAYS`, `DREAM_SWEEP_INTERVAL_MIN`.
- **Policy — every deletion emits an event** via the
  [`ADR-009`](ADR-dreaming-009-events-shim.md) shim:
  `emit("state_file_pruned", path=str(p), reason="ttl_expired")` so
  operators can audit what was cleaned.
- **Exhaustive consumers:** sidecar writer (ADR-004), events diary
  writer (ADR-009), audit writer (ADR-011), lock acquisition
  (ADR-014).

## Open items (dreaming-owned)
- **Cross-platform path semantics** — Windows behavior of
  `.resolve()` differs around case + UNC paths; v1 test surface is
  macOS/Linux only.
- **Per-class TTLs** — if 30 days proves wrong for one specific class,
  successor ADR.
- **Sweeper observability** — surface a summary event
  (`sweep_completed`, count + duration) so the throttle is tunable
  from data.
