# System Diagram — Cookbook Memory

> Clean version of the team whiteboard sketch. The memory layer (orchestrator +
> stores + the offline "subconscious") sitting beside a harness session, with the
> plugin as the only thing the harness sees. Renders inline on GitHub.
>
> Vocabulary ties back to [`01-cross-harness-comparison.md`](01-cross-harness-comparison.md)
> and [`../opencode/05-integration-strategy.md`](../opencode/05-integration-strategy.md).

```mermaid
flowchart LR
  %% ---------- Harness side ----------
  subgraph HARNESS["Coding harness (OpenCode / Claude Code / Codex)"]
    direction TB
    Plugin["<b>Plugin / Adapter</b><br/>skills · MCP · hooks"]
    Session["<b>Session</b><br/>message history / turns"]
    Logs[("<b>Logs</b><br/>.jsonl trajectory")]
    Plugin ==>|registers tools<br/>+ hooks| Session
    Session -->|every step| Logs
  end

  %% ---------- Memory core ----------
  subgraph CORE["Memory core (portable, Python)"]
    direction TB
    Orch(("<b>Orchestrator</b><br/>route · rank · dedup"))
    Mem["<b>Memory stores</b><br/>markdown · vectors · graph"]
    Orch <==>|R / W| Mem
  end

  %% ---------- Subconscious (offline) ----------
  subgraph SUB["Subconscious — consolidation"]
    direction TB
    DayDream["<b>Day Dream</b><br/>in-session / idle<br/>light, frequent"]
    Dream["<b>Dream</b><br/>offline / after session<br/>deep: dedup · conflict · retention"]
    Model["<b>Model</b><br/>non-frontier (cheap)"]
    DayDream <-->|when / what| Dream
    DayDream -->|consolidate| Model
    Dream -->|consolidate| Model
  end

  %% ---------- Cross-cluster wiring ----------
  Session <==>|where / how<br/>recall · remember| Orch
  DayDream ==>|write| Orch
  Dream <==>|R / W| Orch
  Mem -->|read| Dream
  DayDream -.->|adapter:<br/>chunk / batch| Logs

  %% ---------- Styling ----------
  classDef harness fill:#eef4ff,stroke:#3b6fb0,stroke-width:1px,color:#10243e;
  classDef core fill:#fff3e6,stroke:#c97a1a,stroke-width:1px,color:#3e2710;
  classDef sub fill:#f0eaff,stroke:#7a52c0,stroke-width:1px,color:#2a1a4e;
  classDef store fill:#fffbe6,stroke:#b08900,stroke-width:1px,color:#3e3410;
  class Plugin,Session harness;
  class Logs store;
  class Orch core;
  class Mem store;
  class DayDream,Dream,Model sub;
```

## Legend

| Element | What it is |
|---|---|
| **Plugin / Adapter** | The thin per-harness piece (skills · MCP · hooks) — the *only* thing the harness exposes. Registers `recall`/`remember` and observation hooks. |
| **Session** | The harness's live message history / turn loop. |
| **Logs (.jsonl)** | The trajectory log the eval harness grades from — one step per record. |
| **Orchestrator** | The memory core's read/write brain: routes a query to the right store, ranks by `recency × relevancy`, dedups, returns a tight context. Handles the **where / how** of memory. |
| **Memory stores** | The three indexed backends — markdown+YAML, SQLite+vectors, graph. |
| **Subconscious** | The offline consolidation band. |
| **Day Dream** | **In-session / idle** consolidation — light, frequent (e.g. between batches). |
| **Dream** | **Offline / after-session** consolidation — deep: cross-session dedup, conflict resolution, retention/pruning. |
| **Model (non-frontier)** | The cheap model that powers consolidation — *not* the frontier model running the agent. |

## How to read the flows

- **Plugin → Session:** the adapter wires memory tools + hooks into the harness loop.
- **Session ⇄ Orchestrator** (*where / how*): in-loop `recall` / `remember` — the
  model pulls memory and writes it back through the core.
- **Orchestrator ⇄ Memory stores** (*R/W*): the core reads/writes the backends.
- **Session → Logs:** every step is recorded as `.jsonl` for grading.
- **Day Dream → Orchestrator** (*write*) and **Dream ⇄ Orchestrator** (*R/W*):
  consolidation reads from and writes back into the memory path.
- **Memory stores → Dream** (*read*): deep dreaming reads the full store to
  consolidate.
- **Day Dream ⇄ Dream** (*when / what*): the light pass decides when/what to hand
  to the deep pass.
- **Day Dream / Dream → Model:** both call the cheap, non-frontier model to do the
  actual summarizing/extraction.
- **Day Dream ⇢ Logs** (*adapter: chunk/batch*): consolidation also emits batched
  records to the trajectory log.

> This is a conceptual proposal mirroring the whiteboard — not the frozen contract
> ([`../../architecture.md`](../../architecture.md)). The four modules map to the
> plan: persistence + router + retrieval = **Orchestrator + stores**; the dreaming
> component = **Subconscious (Day Dream + Dream)**.
