# Claude Code Plugin — MVP Build Plan (decisions + roadmap)

> The **HOW & WHERE** for the conscious in-session surface: the Claude Code
> **plugin** (skills / MCP / hooks) plus the **subconscious** (the `memory dream`
> CLI — day & night scope; "Daydreamer" below = its log-extraction work). Owner:
> **Keith** (@kmazanec). MVP targets **Claude Code only**, designed to the
> **Codex floor** so the same core lifts to OpenCode/Codex behind thin adapters.
>
> This is the plugin-layer companion to the cross-harness research
> ([`01-cross-harness-comparison.md`](01-cross-harness-comparison.md),
> [`02-claude-code.md`](02-claude-code.md)). It is written as ADR-style decision
> records (each: context · options · decision · rationale · tradeoffs ·
> consequences) followed by a vertical-slice roadmap and an owner-tagged
> open-questions list. Date: 2026-06-19.
>
> **Scope discipline:** this doc locks decisions **only for Keith's surface** —
> the plugin, the Daydreamer, the log adapter + redaction, the seam to the
> Orchestrator, and the events stream. Everything owned by storage (Brent) or
> dreaming (Scott B.) or the team is listed in [§Open questions](#open-questions)
> tagged with the owner who must resolve it. No silent assumptions on other
> owners' turf.

## 0. The architecture this plan builds to (the whiteboard)

The system has three tiers (per the 2026-06-18 design session):

```
   THE MEMORY SYSTEM  (its own package — harness-agnostic; knows nothing of the eval engine)
   ┌──────────────────────────────────────────────────────────────────────────┐
   │  CONSCIOUS                                                                 │
   │    Plugin (skills · MCP · hooks)  ──►  Session  ◄──►  Orchestrator ──► Mem │
   │                                          │           ("where/how",        │
   │                                          │            dedup, embeddings,   │
   │                                          ▼            returns memory ID)   │
   │                                       Logs (.jsonl)        ▲   ▲           │
   │  SUBCONSCIOUS                            │  Adapter+chunk   │   │ R/W       │
   │    Daydream ──reads──────────────────────┘  ──► Model ──► (writes thru Orch)│
   │    Dream    ──R/W through Orch, shares Model──────────────┘                │
   └──────────────────────────────────────────────────────────────────────────┘
         ▲ driven as a BLACK BOX: `claude -p` + $MEMORY_STORE, and the
         │ PUBLIC `memory dream` CLI between task-batches (run 5 → dream → run 5 …)
   ┌──────────────────────────────────────────────────────────────────────────┐
   │  EVAL ENGINE (memeval) — never imports the memory system's internals       │
   └──────────────────────────────────────────────────────────────────────────┘
```

The eval protocol drives the cycle **run 5 tasks → `memory dream --all` → run 5 →
dream → run 5 → dream → run 5 → measure**, per eval set, committing measurements to
this repo after each test. The between-batch step is **night** consolidation — a
**public CLI** (the same a human could run), so the eval invoking it is *driving a
public action*, not reaching into internals (the black box holds). **Day-dreaming
runs automatically inside each `claude -p` run** via the plugin's `Stop`/`PreCompact`
hook (ADR-P5) — the subconscious watches the session as it proceeds. The system ramps
over three iterations, each runnable before the next lands because everything is
fail-open (ADR-P10): **(1)** memory + dreaming both no-op → baseline; **(2)**
`recall`/`remember` + the Daydreamer wired, night `dream` still no-op; **(3)** night
consolidation live → iterate.

Two load-bearing principles fixed in this session, both of which **revise** the
repo's earlier docs (`architecture.md`, `01-cross-harness-comparison.md`) and win
where they conflict:

1. **The Orchestrator is the sole owner of the store; all memory access — both
   `recall` and `remember` — routes through it.** Plugin/Session, Daydream, and
   Dream are all *clients* of the Orchestrator; none touches the store directly.
   (The board's bidirectional Session↔Orch arrow governs over the looser meeting
   note "plugin reads directly".)
2. **The eval engine treats the memory system as a black box.** It drives the
   coding harness with the plugin installed (`claude -p`), points each run at a
   `$MEMORY_STORE`, and invokes the **public `memory dream` CLI** between batches —
   exactly the surfaces a human user has. It never imports the Orchestrator, store,
   or `dream()`; it never touches the store directly. Driving a public CLI is not a
   back door.

## Decision index

| ADR | Decision | Status | Contract |
|-----|----------|--------|----------|
| [ADR-P1](#adr-p1-extract-the-memory-system-into-its-own-package) | Extract the memory system into its own package; `memeval` stays pure eval | Accepted | no |
| [ADR-P2](#adr-p2-orchestrator-is-an-in-process-library-store-by-path) | Orchestrator is an in-process library; store-by-`$MEMORY_STORE`, no daemon | Accepted | **yes** |
| [ADR-P3](#adr-p3-claude-code-plugin-shape-mcp--hooks--skills) | Claude Code plugin = bundled MCP server + hooks + skills | Accepted | no |
| [ADR-P4](#adr-p4-recallremember-are-mcp-tools-through-the-orchestrator) | `recall`/`remember` are MCP tools that call the Orchestrator | Accepted | **yes** |
| [ADR-P5](#adr-p5-daydreamer--auto-stop-fired-day-dream--memory-dream---all-cli-night) | Daydreamer = auto `Stop`/`PreCompact`-fired (day, current session); Dream = `memory dream --all` CLI (night, entire memory) | Accepted | **yes** |
| [ADR-P6](#adr-p6-consolidation-model--swappable-llmclient-openrouter-first) | Consolidation model = swappable `LLMClient`, OpenRouter-first, cheap default | Accepted | **yes** |
| [ADR-P7](#adr-p7-log-extraction-chunking--one-turn--one-chunk-with-prior-summary-overlap) | `dream` log-extraction chunking = one turn = one chunk + prior-summary overlap; semantic later | Accepted | no |
| [ADR-P8](#adr-p8-dream-state--on-disk-json-sidecar-cursor--last_summary--recent_memory_ids) | `dream` state = on-disk JSON sidecar (cursor + last_summary + recent_memory_ids) | Accepted | no |
| [ADR-P9](#adr-p9-log-adapter-owns-redaction-before-any-model-call) | The log adapter redacts secrets before any model call | Accepted | no |
| [ADR-P10](#adr-p10-everything-fail-open-never-break-the-users-session) | Every hook/tool is fail-open — never break the user's session | Accepted | no |
| [ADR-P11](#adr-p11-structured-memory-events-stream-langfuse-bound) | Structured memory-events stream, observability-platform-bound (Langfuse) | Accepted | **yes** |

---

## ADR-P1: Extract the memory system into its own package

**Status:** Accepted · **Contract:** no

### Context
Today the Orchestrator pieces (`MemoryFramework`, `router.py`, `stores/`,
`dreaming/`) live *inside* `eval/memeval/`, and `architecture.md` hands the
framework to `agent.run_agent(store=…)` — i.e. the eval harness holds the memory
system directly. The binding principle established this session is the opposite:
**the eval engine must have zero knowledge of the memory system's internals** and
must interact with it only by driving the coding harness (`claude -p`) with the
plugin installed.

### Options considered
- **Extract to its own top-level package** (e.g. `cookbook_memory/`): Orchestrator
  + stores + router + dreaming + Daydreamer + the CC plugin live there; `memeval`
  keeps only eval/benchmark code.
- **Leave in `memeval`, enforce the boundary by discipline** (CODEOWNERS +
  convention): the coupling stays physically possible.
- **Document the target split, defer the move:** plan against the boundary now,
  physically extract later.

### Decision
**Extract the memory system into its own top-level package.** `memeval` becomes a
pure black-box driver.

### Rationale
The black-box boundary is only *real* if the eval engine *cannot* import the
memory internals. A shared package makes the wrong thing easy and the boundary a
matter of willpower; separate packages make the black-box principle structural.
It also makes the memory system the genuine, distributable, harness-agnostic
artifact the project is aiming for, rather than a subfeature of an eval harness.

### Tradeoffs & risks
This is a **refactor of Brent's and Scott's current paths** (`stores/`, `router.py`,
`dreaming/`) and needs team buy-in — it is not Keith's to execute unilaterally.
The frozen `schema.py`/`protocols.py` (the shared contract) must be reachable by
both packages, so they either stay shared or are published as a tiny contract
package both import. Until the move happens, the plugin can be built against the
current paths (see [§Open questions](#open-questions), team-owned).

### Consequences for the build
- The plugin and Daydreamer import from the **memory-system package**, never from
  `memeval`.
- The only eval↔memory seam is `$MEMORY_STORE` (a path) plus the plugin's
  externally-observable outputs (the events stream, ADR-P11).

---

## ADR-P2: Orchestrator is an in-process library; store-by-path

**Status:** Accepted · **Contract:** yes

### Context
The board makes the Orchestrator the sole owner of `Mem` and the waist all
memory R/W passes through. The question is whether "Orchestrator" is a *process*
everyone calls or a *library* each client runs in-process over a shared store.

### Options considered
- **In-process library + shared store path:** MCP server, Daydreamer, and Dream
  each construct `MemoryFramework(store=SqliteVectorStore($MEMORY_STORE), router=…)`
  and call it. "Through the Orchestrator" is a *code* waist; the store file + SQLite
  WAL is the cross-process coordination point.
- **Standalone Orchestrator service:** one process literally owns the store + the
  dedup / recently-written-ID cache; everyone RPCs in.

### Decision
**In-process library.** No daemon. The store file at `$MEMORY_STORE` (WAL mode) is
the coordination point.

### Rationale
A standalone service re-introduces an **unmanaged daemon lifecycle** — and Codex,
the floor we design to, has no session-end signal to clean one up
([`01`](01-cross-harness-comparison.md) §"Design to the Codex floor"). A library
keeps per-run isolation free (`$MEMORY_STORE`), keeps the Orchestrator logic in one
class (`MemoryFramework`, which already *is* a `MemoryStore`), and is the simplest
thing that honors the board's *code* waist. The diagram's "single waist" is
preserved as the rule **all persistence goes through `MemoryFramework`**, not as a
single OS process.

### Tradeoffs & risks
"Single owner of state" becomes logical, not physical: two processes (the MCP
server and the `Stop`-fired dream pass) hold their own Orchestrator over the same
file, so write-dedup consistency leans on the store (SQLite transaction + WAL),
not an in-RAM lock. The meeting's "recently-written-ID cache" is therefore
per-client (carried in the Daydreamer's sidecar, ADR-P8), not a global in-RAM
cache. **Requires SQLite WAL** so the MCP writer and the dream reader don't block
each other.

### Consequences for the build
- **Contract — source of truth:** the Orchestrator interface is the existing
  frozen `MemoryStore` protocol (`eval/memeval/protocols.py`) — `write(item)`,
  `get(id)`, `search(query, k, as_of)`, `all()` — as realized by `MemoryFramework`.
- **Shape:** `MemoryFramework(*, router, backends|store, dreamer)`; `write` returns
  the new/merged `item_id` (the meeting's "returns memory ID on every write").
- **Exhaustive consumers** that must construct/call the Orchestrator identically:
  the MCP server (ADR-P4), the Daydreamer (ADR-P5), Dream, and the `memory` CLI.
- Every client opens the store via `$MEMORY_STORE`; WAL is mandatory.

---

## ADR-P3: Claude Code plugin shape (MCP + hooks + skills)

**Status:** Accepted · **Contract:** no

### Context
The conscious surface on the board is "Plugin = skills / MCP / hooks." Claude Code
([`02`](02-claude-code.md)) bundles all of these into one installable plugin.

### Options considered
- **One Claude Code plugin** bundling `.mcp.json` (the memory MCP server),
  `hooks/hooks.json`, and `skills/`. (The documented CC pattern.)
- Hooks-only, or MCP-only — rejected: the board explicitly shows all three, and
  MCP is the *only* path to model-callable tools while hooks are the only path to
  lifecycle observation. They are complementary, not alternatives.

### Decision
**One Claude Code plugin** = bundled MCP server + hooks + skills, under the
memory-system package's `adapters/claude-code/`.

### Rationale
Matches the board and the CC plugin model ([`02`](02-claude-code.md) §5): one
installable unit covers tool registration (MCP), lifecycle observation (hooks),
and human-facing affordances (skills). Keeping it under
`adapters/claude-code/` makes Claude Code explicitly *an adapter* over the
harness-agnostic core, so OpenCode/Codex adapters drop in as siblings later.

### Tradeoffs & risks
Plugin-bundled subagents can't declare their own `hooks`/`mcpServers`
([`02`](02-claude-code.md) §5) — fine, we don't need that for MVP. The hook scripts
use `${CLAUDE_PLUGIN_DIR}` and must locate the memory-system entry points
(the `memory` console script), which the package install must put on PATH.

### Consequences for the build
- Plugin layout: `.claude-plugin/plugin.json`, `.mcp.json`, `hooks/hooks.json`,
  `skills/{recall,remember}/SKILL.md`.
- Hooks wired for MVP: `SessionStart` (init + post-compact memory re-inject),
  `UserPromptSubmit` (supplementary top-k push, ADR-P4/S6), **`Stop` (`async`) +
  `PreCompact` → the Daydreamer day pass** (ADR-P5), and `PostCompact` →
  re-inject top memories after compaction (S6). `SessionEnd` is available for a final
  flush. `PostToolUse` is available but not required for MVP (the Daydreamer reads the
  transcript, not per-tool hooks). **Night** consolidation is the separate public
  `memory dream --all` CLI, not a hook.

---

## ADR-P4: `recall`/`remember` are MCP tools through the Orchestrator

**Status:** Accepted · **Contract:** yes

### Context
The model needs native, in-loop memory. MCP is the only path to model-callable
tools in Claude Code, and is the universal substrate across all three harnesses
([`01`](01-cross-harness-comparison.md) §"MCP is the universal substrate"). Per
the board, both read and write go **through the Orchestrator**.

### Options considered
- **`recall` and `remember` both call the Orchestrator** (`MemoryFramework.search`
  / `.write`): router picks the backend on read; dedup-on-write; `remember` returns
  the memory ID.
- Reads bypass the Orchestrator (faster, no routing hop), only writes go through —
  rejected: the board's bidirectional arrow governs, and bypassing loses the
  router's "pick the best backend" on reads.

### Decision
**Both `recall` and `remember` route through the Orchestrator.** The MCP server is
a thin FastMCP wrapper that constructs the Orchestrator (ADR-P2) and calls it.

### Rationale
Keeps one waist (the board), one place for routing/dedup/embeddings, and one place
the `as_of`/`version` invariants live ([`architecture.md`](../../architecture.md)
§3). The model-pulled `recall` tool is the **primary** retrieval path
([`01`](01-cross-harness-comparison.md) constraint #1) because it's the only
mechanism uniform across all three harnesses; `UserPromptSubmit` injection is
*supplementary*.

### Tradeoffs & risks
A routing hop on every read (negligible vs. model latency). The MCP process is
long-lived per session — that is MCP's normal model, not a daemon we manage.

### Consequences for the build
- **Contract — source of truth:** the MCP tool signatures.
- **Shape:** `recall(query: str, k: int = 5) -> list[{id, content, score, tokens}]`;
  `remember(content: str, tags: list[str] = []) -> {id: str}`. Both delegate to
  `MemoryFramework`; `remember` returns the Orchestrator's memory ID.
- **Exhaustive consumers:** the CC `.mcp.json`, the OpenCode/Codex adapter configs
  (later), and `memory dream` (which writes via the same Orchestrator, not the MCP
  tool — see ADR-P5).
- `remember` is the **in-loop** memory-creation path (the model decides to save);
  `memory dream` is the **between-batch** path that additionally mines the logs for
  what the model didn't save (ADR-P5). The iter-2 ramp ("memory on, dream off")
  relies on `remember` working while `dream` is still a no-op.
- `RetrievedItem.tokens` must be populated so the eval efficiency metric works
  ([`architecture.md`](../../architecture.md) §3 invariants).

---

## ADR-P5: Daydreamer = auto `Stop`-fired (day); Dream = `memory dream --all` CLI (night)

**Status:** Accepted · **Contract:** yes

> **Update (post-extraction):** Daydreaming and Dreaming are **separate functions
> with totally isolated entrypoints** — they may share helper code under the hood,
> but they are **not** one `dream()` engine dispatched by a `scope` parameter. This
> ADR has been split into the authoritative
> [`docs/adrs/ADR-dreaming-001`](../adrs/ADR-dreaming-001-daydreaming-stop-fired.md)
> (Daydreaming) and
> [`docs/adrs/ADR-dreaming-002`](../adrs/ADR-dreaming-002-dreaming-consolidation-cli.md)
> (Dreaming); the text below is corrected to match.

### Context
The board splits the subconscious into **Day Dream** (in-session, light) and
**Dream** (offline, deep). These are **two separate functions with isolated
entrypoints** (they may share helpers, but neither is the other's `scope` argument).
Two distinct things were conflated in an earlier draft and are separated here:

- **Memory creation** happens two ways: the model writes *in-loop* via `remember`
  (ADR-P4), **and** the **Daydreamer** watches the session log and extracts memories
  the model didn't explicitly save.
- **Consolidation** (dedup / conflict-resolution / retention) is the deep **Dream**
  pass over the whole store.

The eval protocol also drives the cycle **run 5 → dream → run 5 → … → measure**, so
night consolidation must be invokable between batches via a public surface.

### Options considered
- **Two triggers, split by scope** (chosen): the Daydreamer fires **automatically**
  on the `Stop`/`PreCompact` hook (async) **during** each session — day scope,
  current session only — while night consolidation is the **public `memory dream
  --all` CLI**, driven by the eval between batches or a human.
- One CLI-only trigger for both (a prior draft): simpler black-box story, but loses
  the automatic "remember as you work" the Daydreamer exists to provide. Rejected —
  the Daydreamer is a first-class MVP component, not a manual step.

### Decision
**Two separate functions, two isolated entrypoints, two triggers:**

- **Daydreamer (day scope — current session, automatic).** The plugin's `Stop` hook
  (`async: true`, with `PreCompact` as a final pre-compaction pass) runs the
  Daydreamer over the new-since-cursor session log: adapter → chunk → cheap model →
  write through the Orchestrator → advance the cursor (ADR-P7/P8). It runs **as the
  session proceeds**, no manual invocation. Operates on **this session only**.
- **Dream (night scope — entire memory, CLI).** `memory dream --all` consolidates
  the **whole store across all sessions** (dedup / conflict / retention). It is a
  **public CLI** the eval invokes between batches (black-box-safe — a public action,
  not an internal seam, ADR-P1) and a human can run.

These are **separate entrypoints** — e.g. `daydream(*, session_id, log_path, store)`
and `consolidate(store, *, …)` — **not** a single `dream(store, *, scope, …)`
dispatched on scope. They may share internal helpers (the log adapter, the dedup
path, the `LLMClient` of ADR-P6), but the public entrypoints are isolated.

### Rationale
The Daydreamer-on-`Stop` gives automatic in-session memory capture — the
"subconscious watching the session" the board calls for — without an unmanaged
daemon (the `Stop` hook is the harness's own lifecycle event, self-backgrounded; the
Codex-floor call from [`01`](01-cross-harness-comparison.md), preserved). Keeping
**night** consolidation on a public CLI keeps the eval's run→dream→measure cycle a
clean black box and is exactly the cross-session pass that benefits from being run
deliberately between batches. The no-op ramp still holds: iter-2 ("memory on, dream
off") has the Daydreamer + `remember` creating memories while night `dream` is a
no-op; iter-3 turns on consolidation.

### Tradeoffs & risks
Two triggers, not one — but they're genuinely different jobs (per-session capture vs.
whole-store consolidation), so the split is honest, not incidental. The `Stop`-fired
Daydreamer runs inside every `claude -p` eval run, so the eval is **not** purely
"drive + dream between batches" — in-session day-dreaming happens automatically
during each run. That's intended (it's how a real user's session behaves), and it
stays black-box because the trigger is the harness's own hook, not an eval call.
A memory the Daydreamer writes mid-session reaches context via the next `recall`
(instantly searchable — ADR-P2) or the next-prompt `UserPromptSubmit` push (S6);
there is **no** force-injection before the next model call (no such CC hook exists —
[`02`](02-claude-code.md) §1).

### Consequences for the build
- **Contract — source of truth:** the **two isolated entrypoints** — `daydream(*,
  session_id, log_path, store)` and `consolidate(store, *, …)` (`dreaming/`) — plus
  the `memory daydream` / `memory dream --all` CLI surfaces. **Not** one
  scope-dispatched function.
- **Shape:** day = the `Stop`/`PreCompact` hook → `memory daydream --session <id>
  --log <transcript_path>` (async); night = `memory dream --all`. (Both accept
  `--store P`.)
- **Hooks wired:** `Stop` (`async: true`) and `PreCompact` → the Daydreamer day pass.
- **Exhaustive consumers:** the plugin `Stop`/`PreCompact` hooks (day trigger → the
  Daydreaming entrypoint); the eval protocol + a human (night trigger → the
  consolidation entrypoint).
- Both functions write **through the Orchestrator** (ADR-P2), never the store directly.

---

## ADR-P6: Consolidation model — swappable `LLMClient`, OpenRouter-first

**Status:** Accepted · **Contract:** yes

### Context
The board labels the model "Not frontier"; the meeting left "local vs cheap
OpenRouter" open. The `dream` model's task is extraction/classification ("what in
these logs is worth remembering?") and consolidation, not frontier reasoning. The
engine is stdlib-only at import — any model client must be lazy-imported.

### Options considered
- **Swappable `LLMClient` interface, OpenRouter-first, cheap default model:** one
  OpenRouter key reaches many cheap models; Anthropic and local are alternate impls.
- Hosted Anthropic (Haiku) default — coherent with the thesis but ties the
  subconscious to one provider.
- Local default (Ollama) — free/private but a heavy install prerequisite.

### Decision
**A swappable `LLMClient` interface with OpenRouter as the starting provider and a
cheap model as the default;** local and Anthropic impls drop in behind the same
interface.

### Rationale
OpenRouter gives the widest cheap-model menu behind one key, which suits a
"let benchmarking pick the model" posture — the open question becomes an empirical
*swap*, not a blocker for the Monday dry run. The interface keeps the engine's
"lazy-import heavy deps" rule and lets a privacy-sensitive user point at a local
model for free (relevant to ADR-P9's trust boundary).

### Tradeoffs & risks
API cost and latency per `dream` invocation; a network dependency; an OpenRouter
key required for the default path (acceptable for a research artifact, consistent
with [`01`](01-cross-harness-comparison.md) accepting a runtime dependency). The
default model id is config so we can change it without a code change.

### Consequences for the build
- **Contract — source of truth:** the `LLMClient` interface in the memory package.
- **Shape:** `LLMClient.complete(prompt: str, *, system: str|None, max_tokens:int)
  -> str`; impls: `OpenRouterClient` (default), `LocalClient`, `AnthropicClient`.
  Selected by `DREAM_MODEL` / `DREAM_PROVIDER` env.
- **Exhaustive consumers:** both `dream` scopes (day/night) share the one
  `LLMClient` (the board shows Day Dream and Dream sharing the model).

---

## ADR-P7: Log-extraction chunking — one turn = one chunk, with prior-summary overlap

**Status:** Accepted · **Contract:** no

### Context
When `memory dream` extracts memories from the session logs (ADR-P5), it must chunk
the new-since-cursor log slice before the model call. The meeting preferred
"semantic grouping over arbitrary line counts/time windows, with overlap," but
flagged the exact heuristic as open. The on-disk cursor (ADR-P8) gives a natural
"new since last dream" boundary; within that, the turn is the natural unit.

### Options considered
- **One turn = one chunk + prior-summary overlap:** send the new turn-slice
  (prompt + assistant + tool calls/results since cursor) as one chunk; overlap =
  carry the prior turn's one-line summary as a header.
- **Semantic segmentation within the slice** (topic-shift detection) — truest to
  the meeting, but the heuristic is itself the open problem.
- **Size-bounded sliding window + overlap** — the "arbitrary windows" the meeting
  said to avoid.

### Decision
**One turn = one chunk, with the prior turn's summary as overlap.** The Adapter is
designed so a semantic segmenter slots in later.

### Rationale
A turn is already a semantically meaningful unit of work. This gets working,
defensible log-extraction for Monday without solving the open segmentation problem,
and the Adapter's neutral event sequence is exactly the seam where a smarter
segmenter drops in once eval data shows turn-chunking is too coarse
(thinnest-slice-first; extend one axis later).

### Tradeoffs & risks
A long multi-tool turn is one large chunk — acceptable for MVP; the cheap model can
handle it, and ADR-P11's events let us *see* when chunks get unwieldy. Not
"semantic grouping" in the sophisticated sense — that's the planned next axis, not
abandoned.

### Consequences for the build
- The **Adapter** (`adapters/claude-code/log_adapter`) normalizes CC JSONL into a
  neutral `list[turn-event]`; chunking operates on that neutral shape, so OpenCode's
  SQLite log / Codex's format plug in behind the same Adapter interface later
  ([`01`](01-cross-harness-comparison.md) "start with JSONL only").

---

## ADR-P8: `dream` state — on-disk JSON sidecar (cursor + last_summary + recent_memory_ids)

**Status:** Accepted · **Contract:** no

### Context
`memory dream` (day-scope) is invoked repeatedly across a session's lifetime and
must not re-extract log it already processed, so it needs state that survives
between invocations on disk. The meeting named two pieces: the cursor (last
processed log line) and a "recently written memory cache."

### Decision
A small **JSON sidecar keyed by session id**, under the store dir:
`{cursor, last_summary, recent_memory_ids}`.

### Rationale
Lets each day-scope `memory dream --session <id>` resume where the last left off.
`cursor` = byte/line offset into the transcript (resume point — only newly-appended
log is extracted). `last_summary` = the prior-chunk summary used as overlap
(ADR-P7). `recent_memory_ids` = the meeting's "recently written cache" — tells the
next `dream` what was already written so it doesn't re-extract, and can hint the
Orchestrator's dedup. Per-session keying keeps concurrent sessions independent.
Night-scope (`--all`) consolidation reads memory hashes/timestamps from the store
itself, so it doesn't use the per-session cursor.

### Tradeoffs & risks
`recent_memory_ids` is per-client, not a global cache (consequence of ADR-P2's
library model) — bounded in size (last N), with the Orchestrator's dedup-on-write
as the real backstop.

### Consequences for the build
- Sidecar path: `${MEMORY_STORE%/*}/dream/<session_id>.json` (or a sibling dir).
  Read at the start of each day-scope `dream`, written at the end.

---

## ADR-P9: The log adapter owns redaction before any model call

**Status:** Accepted · **Contract:** no

### Context
The Daydreamer reads the full session transcript — which can contain secrets (API
keys, tokens, `.env` values, file contents) — and sends chunks to an external model
(OpenRouter, ADR-P6). The trust boundary is: untrusted/sensitive log content
crossing to a third party.

### Decision
**The log adapter performs a redaction pass before any chunk leaves the process** —
scrub obvious secret patterns (key/token shapes, `.env`-style assignments) from the
turn-slice prior to the model call. This is *our* boundary (the Daydreamer is ours).

### Rationale
The plugin controls the only point where session content leaves the machine (the
model call), so redaction belongs there, in the adapter, before chunking. It is a
documented, bounded boundary rather than a hand-wave.

### Tradeoffs & risks
Pattern-based redaction is best-effort, not exhaustive — it reduces, not
eliminates, leakage risk. Users wanting zero external exposure can select the local
`LLMClient` (ADR-P6 makes that free). **All persistence-side trust policy**
(whether/where memories are stored, retention, encryption at rest) is **deferred to
the storage owner** — see [§Open questions](#open-questions).

### Consequences for the build
- Redaction is a function in the log adapter, applied to every chunk pre-model;
  patterns are configurable.

---

## ADR-P10: Everything fail-open — never break the user's session

**Status:** Accepted · **Contract:** no

### Context
The Orchestrator's parts (`router`, `stores`, `dreaming`) are
`NotImplementedError` scaffolds today and depend on Brent's/Scott's work landing.
The plugin runs inside a live coding session.

### Decision
**Every hook and MCP tool is fail-open.** If the Orchestrator/store/model errors or
isn't ready: `recall` returns empty, `remember` no-ops (logs a warning), the `Stop`
Daydreamer pass swallows and logs. A memory failure must never crash or block the
user's turn.

### Rationale
A memory system that breaks the user's session is strictly worse than no memory.
Fail-open lets the plugin ship and be used while the engine matures.

### Tradeoffs & risks
Silent degradation can mask real breakage — mitigated by the events stream
(ADR-P11), which records "recall failed / store unavailable" so failures are
*visible* even though they're non-fatal.

### Consequences for the build
- Hook scripts and tool handlers wrap all engine calls; errors → log + safe default.
- Whether the plugin is first built against the working `InMemoryStore` reference
  vs. waits for real backends is a **sequencing call for the team**, not Keith's —
  see [§Open questions](#open-questions).

---

## ADR-P11: Structured memory-events stream, Langfuse-bound

**Status:** Accepted · **Contract:** yes

### Context
The system needs to surface "what got remembered / recalled / dreamed" — both for
debugging and so the **black-box eval can verify behavior from an output it reads,
without touching internals** (ADR-P1).

### Decision
The plugin, `memory dream`, and the Orchestrator emit a **structured memory-events
stream** (JSONL under `$MEMORY_STORE` for MVP), shaped to be **observability-
platform-friendly so it can be shipped to Langfuse** (or similar) later as
spans/traces.

### Rationale
Cheap, and it doubles as the exact machine-readable output the eval black box reads
to confirm "what got remembered." Designing the event shape as trace-friendly now
(operation, ids, timing, parent/child) means the Langfuse export is a sink swap,
not a re-instrumentation.

### Tradeoffs & risks
A second log alongside the engine's `Trajectory` JSONL — but reusing
`memeval.trajectory` would re-couple the plugin to eval internals (fights ADR-P1),
so a separate, plugin-owned events stream is correct.

### Consequences for the build
- **Contract — source of truth:** the memory-event schema in the memory package.
- **Shape:** `{ts, op: "recall"|"remember"|"dream"|"error", scope?:
  "session"|"all", session_id, ids:[...], query?, summary?, meta:{...}}` —
  span-friendly.
- **Exhaustive consumers:** the MCP tools, the Daydreamer (`Stop`/`PreCompact`), and
  `memory dream --all` (emitters); the `memory log`/`stats` CLI and the eval
  verification step (readers); a future Langfuse exporter (sink).

---

## Roadmap — vertical slices for the Claude Code MVP

Slices are ordered; each leaves the system more capable and is independently
demoable. Hard dependencies noted; everything else builds against the frozen
contracts (ADR-P2/P4/P6/P11). Owner: Keith, except where noted.

| # | Slice | After this, you can… | Depends on |
|---|-------|----------------------|------------|
| **S0** | **Package skeleton + `memory` CLI** | `pip install` the memory package; `memory --help` shows `mcp`/`dream`/`query`/`reset`/`stats`/`log`; store opens at `$MEMORY_STORE` (WAL). | ADR-P1 (team buy-in on extraction) |
| **S1** | **MCP server: `recall`/`remember` through the Orchestrator** | Run `memory mcp`; a model calls `mcp__memory__remember`/`recall` and gets an id / hits back — against `InMemoryStore` to start. This is the **in-loop memory-creation** path. | S0; ADR-P4 |
| **S2** | **Claude Code plugin bundle** | Install the plugin in CC; the MCP tools appear in a real session; `recall`/`remember` work end-to-end. | S1; ADR-P3 |
| **S3** | **Log adapter + redaction** | The Daydreamer reads a CC transcript, normalizes it to neutral turn-events, redacts secrets — printed, not yet stored. | S0; ADR-P7, P9 |
| **S4a** | **Daydreamer — auto `Stop`/`PreCompact` day pass** | A finished turn (and pre-compaction) fires the async Daydreamer: chunk → `LLMClient` → write **this session's** memories through Orch → advance cursor. Memories accrue automatically as the session runs. | S2, S3; ADR-P5, P6, P8 |
| **S4b** | **`memory dream --all` — night consolidation** | Run `memory dream --all` to dedup/merge/prune the **entire store across sessions**. The between-batch surface the eval drives. | S2, S4a; ADR-P5, P6 |
| **S5** | **Events stream + `memory log`/`stats`** | Inspect what was recalled/remembered/dreamed; the eval can read it as a black-box output. | S1, S4a; ADR-P11 |
| **S6** | **`UserPromptSubmit` injection + `PostCompact` re-inject** | Top-k memories pushed at turn start; **the Daydreamer's freshest/most-important memories re-injected after compaction** so they survive the summary. | S2, S4a; ADR-P3 |
| **S7** | **Black-box eval hook-up (the run→dream cycle)** | `memeval` drives `claude -p` with the plugin + a `$MEMORY_STORE` (the Daydreamer auto-runs in-session), runs the **5-tasks → `memory dream --all` → 5 → … → measure** cycle (invoking the public night CLI between batches), reads S5 events to verify behavior — no internal imports. | S5; ADR-P1, P5 |

The **walking skeleton is S0+S1+S2** — install the plugin, see the tools in a live
session, `recall`/`remember` working against the reference store (this alone is the
**iter-2** "memory on, dream off" shape: memory creation is in-loop `remember` plus
the **S4a Daydreamer**, while night `dream` stays off). **S4b** adds night
consolidation (the **iter-3** shape). S7 wires the run→dream→measure eval cycle. The
**iter-1 baseline** needs none of the engine — fail-open no-ops (ADR-P10) give an
empty-memory floor.

Build everything S1–S6 against the **`InMemoryStore` reference** so the plugin is
testable before Brent's backends and Scott's dreaming land (pending the team's
sequencing call — see below); swapping in the real Orchestrator backends is then a
construction-time change, not a plugin rewrite.

## Open questions

Tagged with the owner who must resolve each — **not** decided here.

- **[team] Package extraction (ADR-P1).** When and how to physically move
  `stores/`/`router.py`/`dreaming/`/`MemoryFramework` out of `eval/memeval/` into
  the memory package, and how the frozen `schema.py`/`protocols.py` contract is
  shared between the two packages. Needs Brent + Scott + Ken sign-off (CODEOWNERS).
- **[team] `architecture.md` reconciliation (co-owned doc — do not edit unilaterally).**
  The decisions here revise the frozen-contract-adjacent
  [`architecture.md`](../../architecture.md) in three places, which needs a
  `[CONTRACT]`-style PR with all owners' sign-off:
  - **§1 Components** + **§4 How components talk** describe the eval-side
    `MemoryFramework` being handed to `agent.run_agent(..., store=framework)` — i.e.
    the eval harness *holding* the Orchestrator. ADR-P1 makes the eval a black-box
    driver that never holds or imports it.
  - **§2 Module boundaries** locates `opencode/`, `stores/`, `router.py`, and
    `dreaming/` *inside* `eval/memeval/`. ADR-P1 moves the memory system to its own
    package; the ownership table should follow.
  - The implication that the harness reads the engine's `Trajectory` log to grade
    memory is superseded by the plugin-owned events stream (ADR-P11).
  Until that PR lands, the plugin is built against the current paths and the
  boundary is honored by discipline.
- **[team] Build-vs-wait sequencing (ADR-P10).** Whether the plugin is built/tested
  against `InMemoryStore` first (de-risks the dependency, some throwaway wiring) or
  waits for the real backends (no throwaway, but blocks plugin work). The roadmap
  assumes "build against `InMemoryStore` first," but the call is the team's.
- **[storage / Brent] Persistence-side trust policy (ADR-P9).** Local-only storage
  (the meeting said "local for MVP"), retention, encryption at rest, and dedup
  behavior on `remember`. The plugin owns redaction *before* the model call; the
  Orchestrator owns everything once content is persisted.
- **[storage / Brent] Dedup-on-write confidence threshold.** The meeting noted
  "dedup on write if confidence is high enough" and "returns memory ID." The exact
  threshold and merge-vs-new-version policy live in the Orchestrator.
- **[dreaming / Scott B.] The two isolated subconscious functions.** **Daydreaming**
  (current session only) and **Dreaming/consolidation** (the ENTIRE memory across all
  sessions; reads memory hashes/timestamps to find what changed) are **separate
  entrypoints** — `memory daydream --session <id>` and `memory dream --all` (ADR-P5)
  call distinct callables, not one `dream(scope=…)`. Any helpers they share and the
  consolidation logic are Scott's.
- **[Keith, later] Semantic chunker (ADR-P7).** Upgrade from one-turn-one-chunk to
  topic-shift segmentation, driven by eval data.
- **[Keith, later] Langfuse export (ADR-P11).** Wire the events stream to a real
  observability platform.

## TL;DR

A single **Claude Code plugin** (MCP + hooks + skills) is the conscious surface:
the model creates memory **in-loop** via the `remember` MCP tool and reads via
`recall`. The subconscious has two **separate functions with isolated entrypoints**: the
**Daydreamer** — **day**, current session, fired **automatically** by the plugin's
`Stop`/`PreCompact` hook — watches the session log (redacting adapter + turn-chunking
+ swappable OpenRouter-first model) and writes this session's memories as the run
proceeds; and **Dream** — **night**, the entire memory across all sessions — the
**public `memory dream --all` CLI** that consolidates (dedup/merge/prune). They are
two distinct callables that may share helpers — **not** one `dream(store, scope, …)`
engine. Everything reaches memory **only through the in-process
Orchestrator** (`MemoryFramework` over `$MEMORY_STORE`), which owns where/how.
Everything is **fail-open** — so the eval ramps cleanly: **(1)** no-op baseline,
**(2)** `recall`/`remember` + Daydreamer wired, night `dream` no-op, **(3)** night
consolidation live. The eval drives the **run 5 → `memory dream --all` → run 5 → …
→ measure** cycle as a **black box** (public CLI + `claude -p` with the Daydreamer
auto-running in-session, never an import), committing measurements after each test.
The whole memory system lives in **its own package**; MVP is Claude-Code-only but
every piece is an adapter over a harness-agnostic core, designed to the **Codex
floor**.
