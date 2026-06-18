# OpenCode Context Assembly, Compaction & Token Budget

> How OpenCode builds the context window, counts tokens, and compacts long
> histories — and where injected memory should live. Source repo: `../opencode`.
> Read `CONTEXT.md` (repo root) for the canonical vocabulary.

## 1. System Context Registry / Context Source

**Registry** — `packages/core/src/system-context/registry.ts:1–46`:
`SystemContextRegistry` is an Effect service over a `Ref<ReadonlyArray<Entry>>`,
`Entry = { key, load }`.

- `register(entry)` uses `Effect.acquireRelease` (auto-removed on scope close);
  duplicate keys fail.
- `load()` sorts entries by `key` (alphabetical), runs all `load` effects
  concurrently, merges via `SystemContext.combine(...)`.

**Context Source** — `packages/core/src/system-context/index.ts:32–169`. A
`Source<A>` requires `key`, `codec` (Schema.Codec for compare/store), `load`
(`A | Unavailable`), `baseline(current) => string`, `update(prev, current) =>
string`, optional `removed(prev) => string`. `SystemContext.make(source)` packs a
`load` effect exposing `baseline()` (full text + snapshot) and
`compare(previousJson)` → `Unchanged | Updated | Incompatible`.

**Lifecycle:** `initialize(ctx)` (`index.ts:194`) observes all sources, joins
baselines with `"\n\n"`; `reconcile(ctx, snapshot)` (`index.ts:214`) diffs and
emits update text; `replace(ctx, snapshot)` (`index.ts:279`) forces a fresh
baseline (at compaction / agent / model switch).

**Can we register a new source?** Yes — call `registry.register({ key, load })`
in a scoped layer with `key = "memory/retrieved"`; the `load` fetches the
session's current retrieved memories.

## 2. How AGENTS.md / CLAUDE.md instructions load (the memory analog)

`packages/core/src/instruction-context.ts:1–92` registers one source,
`"core/instructions"`. `observe()`:

1. resolves `location.directory` (start) and `project.directory` (stop);
2. respects `Flag.OPENCODE_DISABLE_PROJECT_CONFIG`;
3. `fs.up({ targets: ["AGENTS.md"], start, stop })` walks upward collecting files;
4. prepends global `AGENTS.md`;
5. dedupes + reads concurrently (any read failure → `unavailable`);
6. filters missing files.

Render (`instruction-context.ts:90`):

```ts
files.map((f) => `Instructions from: ${f.path}\n${f.content}`).join("\n\n")
```

`update` replaces all prior instructions; `removed` emits "Previously loaded
instructions no longer apply." **This is the exact pattern injected memory should
follow** — a `SystemContext` source whose `load` fetches from a store instead of
disk.

## 3. Final message array / prompt assembly order

`packages/core/src/session/runner/llm.ts:175–228` — `runTurnAttempt`:

```ts
const request = LLM.request({
  model,
  system: [agent.info?.system, system.baseline]   // SYSTEM
    .filter((p) => p && p.length > 0).map(SystemPart.make),
  messages: toLLMMessages(context, model),         // HISTORY
  tools: toolMaterialization.definitions,          // TOOLS
})
```

System order: (1) `agent.info?.system`, (2) `system.baseline` (all registered
sources joined by `"\n\n"`, sorted by key: `core/builtins` → `core/instructions`
→ `core/reference-guidance` → `core/skill-guidance`).

History (`runner/to-llm-message.ts:93–149`): `user`/`synthetic`/`shell` → `user`;
`system` → `system` (Mid-Conversation System Message, in chronological position);
`assistant` → assistant + tool messages; `compaction` → `user` (XML summary +
recent); `agent-switched`/`model-switched` → skipped. Filtering
(`session/history.ts:82–99`): only `seq >= compaction.seq`, and system messages
only if `seq > baselineSeq`.

Effective layout:

```
SYSTEM:   [agent.info.system?, ...registered_source_baselines]
MESSAGES: [history since compaction + epoch]
TOOLS:    [definitions]
```

## 4. Token counting & budgeting

`packages/core/src/util/token.ts` — the entire estimator:

```ts
const CHARS_PER_TOKEN = 4
export const estimate = (input) => Math.max(0, Math.round(input.length / CHARS_PER_TOKEN))
```

A **char/4 heuristic** — no tiktoken, no provider count. Budget check
(`session/compaction.ts:230–241`): compact when
`estimate(whole_request) > context_window − max(output_limit, buffer)`.
Constants (`compaction.ts:14–16`): `DEFAULT_BUFFER = 20_000`,
`DEFAULT_KEEP_TOKENS = 8_000`, `SUMMARY_OUTPUT_TOKENS = 4_096`. Limits come from
`model.route.defaults.limits` (`runner/model.ts:73`), populated from the catalog.

**Implication:** injected memory counts in this same heuristic — ~1,000 words ≈
5,000 chars ≈ 1,250 "tokens." The check runs before every provider call, so
injected memory participates fully. The 20k buffer means memory blocks under
~5,000 words won't themselves trigger compaction.

## 5. Compaction / summarization

**Triggers** (`session/compaction.ts:230–246`, `runner/llm.ts:228–229`):
pre-turn overflow (`compactIfNeeded` → `compactAfterOverflow`) and post-error
overflow recovery (`isContextOverflowFailure`).

**Process** (`compaction.ts:132–228`):
1. `select(entries, tokens)` walks newest→oldest until `config.tokens` (8k)
   exceeded → `{ head (summarize), recent (keep verbatim) }`.
2. `serialize(message)` → flat text (`[User]:`, `[Assistant]:`, `[Tool result]:`
   …); tool output truncated at `TOOL_OUTPUT_MAX_CHARS = 2_000`.
3. `buildPrompt` uses `SUMMARY_TEMPLATE` (Goal, Constraints, Progress, Key
   Decisions, Next Steps, Critical Context, Relevant Files).
4. Separate LLM call generates the summary (≤4,096 tokens).
5. Emits `Compaction.Started` / `Compaction.Ended` `{ text, recent }`.
6. Projector requests epoch replacement
   (`projector.ts:438–447`, `SessionContextEpoch.requestReplacement`).

**Old messages:** the `Compaction` message (`message.ts:170–176`) renders
(`to-llm-message.ts:126–143`) as a single user turn:

```
<conversation-checkpoint>
  <summary>…</summary>
  <recent-context>…</recent-context>
</conversation-checkpoint>
```

History queries filter to `seq >= compaction.seq`, so pre-compaction turns are no
longer replayed. After compaction `SystemContextEpoch.prepare()` rebuilds a fresh
baseline; prior Mid-Conversation System Messages stay in the durable log but are
excluded from the projected history.

## 6. Existing persistent cross-session memory

**None.** Grep found only: `permission.ts:286` `rememberedRules` (tool-permission
persistence), `tool/websearch.ts:28` "knowledge cutoff" (a description string),
`session/message-updater.ts:19` an in-process test `memory()` adapter. No vector
store, no knowledge graph, no RAG. **AGENTS.md is the closest analog** — durable
on-disk facts loaded at session start.

## Where injected memory should live

Cleanest: a **new registered `SystemContext` source** keyed `"memory/retrieved"`:

```ts
yield* registry.register({
  key: SystemContext.Key.make("memory/retrieved"),
  load: Effect.map(retrieveMemoriesForSession(sessionID), (memories) =>
    SystemContext.make({
      key: SystemContext.Key.make("memory/retrieved"),
      codec: Schema.toCodecJson(Schema.Array(MemorySchema)),
      load: Effect.succeed(memories),
      baseline: renderMemories,
      update: (_p, c) => `Retrieved memories updated:\n\n${renderMemories(c)}`,
      removed: () => "No retrieved memories are currently available.",
    }))
})
```

Benefits: lives in the **system baseline** (best Anthropic prompt-cache locality);
participates in the **snapshot/diff** mechanism for free (a Mid-Conversation
System Message is emitted when memories change after dreaming); lazy (observed at
Safe Provider-Turn Boundaries); **zero core modification**. Sorts after
`core/*` keys, so it lands after AGENTS.md and before the provider call, counting
against the `estimate()` budget.

**Alternative:** inject as a `synthetic` user-role message (lower cache
efficiency, but positioned after a specific conversation point).

> **Decision note.** Registering a `SystemContext` source requires editing OpenCode
> internals — i.e. a **fork**. Our chosen home is instead an OpenCode **plugin**
> using `experimental.chat.system.transform` (`session/llm/request.ts:69`), which
> injects the same `system[]` on every turn without touching core. The *only* thing
> the source-based fork adds is the snapshot/diff "Mid-Conversation System Message"
> machinery below (emit an update only when memories change) — a prompt-cache
> optimization, not a capability. We keep the fork as a documented fallback if that
> caching ever proves necessary. See [`05`](05-integration-strategy.md) and
> [`02`](02-extension-surfaces.md).

## Parallels to our harness

### Compaction ≈ Dreaming

| Dimension | OpenCode compaction | Harness dreaming |
|---|---|---|
| Trigger | token overflow (reactive) | scheduled / post-session (proactive) |
| Input | serialized session history | session transcript / trajectory |
| LLM call | yes (same model) | yes (consolidation model) |
| Output | structured summary in-session | persistent memories (vectors + facts) |
| Scope | one session, ephemeral | cross-session, durable |
| History effect | pre-compaction turns dropped from replay | memories surfaced via retrieval |

Compaction's `{ summary, recent }` output is a natural **input** to the dreaming
pipeline.

### AGENTS.md ≈ memory injection

Both inject system-prompt text from an external source. AGENTS.md is file-based
and manually curated; retrieved memories are indexed and dynamically selected. The
`InstructionContext` source is the exact template to copy.

## Summary

OpenCode builds context in two layers: a **system prompt** from the
`SystemContextRegistry` (environment, date, AGENTS.md, skills, references joined by
`"\n\n"`, prefixed by the agent's `system` string) and a **messages array** from
projected history since the last compaction checkpoint. Sources are typed,
codec-compared, and lazily reconciled at each Safe Provider-Turn Boundary, emitting
Mid-Conversation System Messages only on change. Token budgeting is a char/4
heuristic; compaction fires when `estimate(request) > context − max(output,
20_000)`, runs an LLM summarization, and replaces the epoch baseline. There is **no
existing cross-session memory** — AGENTS.md on disk is the only durable analog. The
cleanest injection point is a new `SystemContext` source keyed `memory/retrieved`,
which is zero-core-modification, system-prompt-cache-friendly, and free-rides the
snapshot diff. Compaction is directly parallel to our dreaming worker (distill
conversation → structured summary), differing mainly in being reactive and
session-scoped. The 20k compaction buffer makes the project's `<10%` memory-token
overhead target achievable as long as retrieved memories stay terse.
