# Storyboard — "The Robot Who Couldn't Remember"

> A **60s** silent animation for the Cookbook Memory talk. Embeddable in the deck,
> shareable standalone. **No audio** — every beat must read visually. Loopable.

## Concept in one line
A cartoon robot learns, sleeps, and forgets — until we look inside its head, give it a
conscious/subconscious memory architecture, and watch it wake up the next day *still
knowing what it learned*.

## Format & specs
- **Length:** 60s (three acts: ~20s · ~22s · ~18s).
- **Aspect:** 16:9, 1920×1080. Also export a 1:1 / 9:16 crop for sharing.
- **Style:** flat vector cartoon, rounded shapes, friendly robot. 2–3 accent colors
  pulled from the deck palette (blue `--blue`, violet `--violet`, orange/amber, green
  `--green`). Soft day/night background shift to mark time passing.
- **No dialog, no narration.** Use **on-screen labels sparingly** (3–4 words max) and
  simple iconography (gears, glowing dots = memories, ☀️/🌙 for day/night).
- **Pacing:** three acts. Act 1 = the problem (forgetting). Act 2 = the architecture
  (inside the head). Act 3 = the solution (remembering).
- **Loop seam:** end frame should rhyme with the open so it can loop cleanly.

---

## ACT 1 — THE PROBLEM (≈0:00–0:20)
*Tone: light, a little sad/comedic. Establish the pain of catastrophic forgetting.
Now with a fuller learning montage so the loss hurts more.*

> **Act 1 is a side-scrolling journey.** Instead of a static workbench, the robot
> **walks rightward** through a small parallax world (rolling hills, signposts), and
> the day's growth happens by *meeting people*: it learns, **earns a name**, and
> **develops a personality** — then night strips all of it.

### Shot 1.1 — Establish · "Day 1" walk begins (0:00–0:03)
- **Frame:** ☀️ rising, "DAY 1" label. A plain grey robot starts **walking right**
  across a rolling-hills world; the world scrolls past it (it stays center-frame).
- **Beat:** It's blank — no name, no accessories. A nobody, starting its day.

### Shot 1.2 — Meeting people · learning a self (0:03–0:10)
- **Action:** The robot walks past **3 flat-silhouette people**. Each **waves** and
  pops a **speech bubble** — a glowing **memory dot** (wrench / bulb / key) flies up
  to orbit the robot's head. With each meeting the robot **gains a personality item**
  (hat → scarf → badge) and its body takes a **warmer tint**; its walk gets livelier.
- **The key moment:** the **2nd person speaks its NAME — "Pixel"** — and a **name tag
  appears under its feet**, with a small **sparkle**. By the end of the walk it's a
  fully-realized character: named, decorated, carrying its memories.

### Shot 1.3 — Night · the loss (0:10–0:15)
- **Frame:** World dissolves to night, 🌙 rises, "Zzz". The robot **stops and slumps
  asleep** (eyes → flat line).
- **Action:** As it sleeps, **everything drains away, staggered** — the name tag
  fades, the hat/scarf/badge drop off, the warm tint washes back to grey, and every
  **memory dot floats up and evaporates**. Hold on the blank, sleeping robot.
- **Beat:** This is the gut-punch — we just watched a *self* assemble, and night
  erases all of it.

### Shot 1.4 — Day 2 · a stranger again (0:15–0:20)
- **Frame:** ☀️, "DAY 2", **back at the start of the world** (same first signposts).
- **Action:** A **blank grey robot** wakes — no name, no items, no memories. It looks
  around, confused: a glowing **"?"**. It has to become someone all over again.
- **Beat (the punchline):** **Title card snaps in:** `Every day starts from zero.`
- **Transition:** Camera pushes IN toward the robot's head → smash to Act 2.

---

## ACT 2 — INSIDE THE HEAD (≈0:20–0:42)
*Tone: shift to "aha" / reveal. We open the robot's head and build the architecture
from `architecture.md` — the two-band Conscious / Subconscious model. The extra time
lets each node and the key behaviors (route, rank, dedup, govern) actually read.*

### Shot 2.1 — Open the head (0:20–0:24)
- **Frame:** The robot's head opens / becomes transparent (X-ray cross-section).
  Inside is a clean stage we'll diagram on.
- **Action:** Two horizontal **bands** fade in, labeled:
  - Top band: `CONSCIOUS` (light blue tint) — "the live session, awake"
  - Bottom band: `SUBCONSCIOUS` (violet tint) — "offline, while idle / asleep"
- This mirrors the system diagram's two-band core idea.

### Shot 2.2 — Build the Conscious row (top band) (0:24–0:30)
*Build left→right, each node pops in with a connecting arrow drawing on.*
- **Plugin** (skills · MCP · hooks) — the only surface the outside world sees.
- → **Session** (message history) — the current train of thought.
- ↔ **Router / Orchestrator** (route · rank · dedup) — a little spinning hub.
- ↔ **Memory** — drawn as **3 stacked store icons**: `vectors · graph · markdown`.
- **Visual:** a memory dot travels Plugin → Session → Orchestrator and the
  **Router classifies it and sends it to ONE of the three stores** (highlight the
  chosen backend, dim the others). Then a query pulls a dot **back out** (recall).

### Shot 2.3 — Recall ranks (0:30–0:33)
- **Frame:** Zoom on the Memory stores during a recall.
- **Action:** A query fans out; several candidate dots light up; they **sort into a
  ranked list** — show a quick `recency × relevancy` formula/label as the dots
  reorder, the top one glowing brightest and zipping back to the Session.
- **On-screen (small):** `rank · recency × relevancy`.

### Shot 2.4 — Build the Subconscious row (bottom band) (0:33–0:39)
- **Logs** (`.jsonl`) — the Session drops a stack of paper/log icons down across the
  band boundary.
- **Day Dream** (in-session / idle) — reads the logs, **mines the delta** (show it
  skipping already-read entries, grabbing only the new ones).
- **Model** (small, "not frontier" — drawn smaller/cheaper than a big brain) — Day
  Dream consults it to decide *what to remember*; new candidate dots are born and sent
  **up** into the Orchestrator to be saved.
- **Dream** (offline / after session) — the night worker: **dedup · contradiction ·
  governance**. Show two duplicate dots **merging into one**, and a contradictory pair
  where one **updates/overwrites** the other (a small **version bump** number ticks up).
- **Visual:** arrows **cross the band boundary** — Day Dream writes up into the
  Orchestrator; Dream reads the whole Memory store.

### Shot 2.5 — The whole machine breathes (0:39–0:42)
- **Frame:** Pull back slightly. The full two-band diagram is now alive: dots flow
  along the conscious row while "awake," and at night the subconscious row lights up
  and **organizes** the store (dots snapping into neat, tagged rows; a **gold star /
  "must-know" tag** clips onto the most important one).
- **On-screen:** `Conscious works. Subconscious remembers.`
- **Transition:** Camera pulls back OUT of the head → robot whole again → Act 3.

---

## ACT 3 — THE SOLUTION (≈0:42–0:60)
*Tone: triumphant, satisfying. Same day-cycle as Act 1, but now memory persists.
Mirror Act 1's shots beat-for-beat so the contrast lands, then close.*

### Shot 3.1 — Day 1, again — now with Daydreaming (0:42–0:47)
- **Frame:** ☀️, "DAY 1" — same staging as Shot 1.1/1.2.
- **Action:** Robot does its tasks, glowing memory dots appear above its head as
  before — **but now** a faint **Daydream shimmer** periodically sweeps the dots and
  **files them into a visible "memory drawer"** in/near the robot (write to store).
  The dots no longer just float loose — they're being *saved*.
- **On-screen (small):** `Daydream → write`.

### Shot 3.2 — Night — Dreaming organizes (0:47–0:52)
- **Frame:** 🌙, robot powers down ("Zzz") — same as Shot 1.3 — **but the head glows
  softly** instead of going dark.
- **Action:** Inside the (semi-transparent) head, the **Subconscious row activates**:
  the night **Dream** sweeps the memory drawer — **dedups** (two dots merge), tidies,
  and **tags** them (colored governance tags + a **must-know star**). Dots arrange
  into neat, labeled rows. The knowledge is *kept and cleaned*, not lost.
- **On-screen (small):** `Dream → organize`.

### Shot 3.3 — Day 2 — it remembers! (0:52–0:57)
- **Frame:** ☀️, "DAY 2" — same staging as Shot 1.4 (the amnesia shot).
- **Action:** Robot wakes — head still holds its **tidy glowing memories**. Faces the
  **same task** from before. This time it **recalls** (a dot zips from the memory
  drawer to its "hand") and **does the task instantly / correctly** — confident, no
  fumble. Then it breezes through the *rest* of yesterday's tasks too, fast.
- **Beat (payoff):** Replace Act 1's `?` with a confident `✓`. Title card:
  `Now it remembers.`

### Shot 3.4 — End card / loop seam (0:57–0:60)
- **Frame:** Clean end card: **Cookbook Memory** logo (◆) + one line:
  `Persistent, self-curating memory for coding agents.`
- **Optional micro-beat:** a tiny "Haiku + memory → thinks like Opus" tagline flickers
  in, matching the deck's central bet (keep it ≤1.5s, only if it reads cleanly).
- **Loop option:** hold ~1s, then optionally cut back to the "DAY 1" open for a clean
  loop during the talk.

---

## Beat sheet (60s timing reference)
| Time | Act | Shot | Beat |
|---|---|---|---|
| 0:00–0:04 | 1 | 1.1 | Day 1, learns first task → first dot |
| 0:04–0:10 | 1 | 1.2 | Learning montage, ~4–5 dots accumulate |
| 0:10–0:15 | 1 | 1.3 | Sleeps, dots evaporate, empty head |
| 0:15–0:20 | 1 | 1.4 | Day 2, re-fumbles same task → `?` · "Every day starts from zero" |
| 0:20–0:24 | 2 | 2.1 | Head opens → Conscious / Subconscious bands |
| 0:24–0:30 | 2 | 2.2 | Build Conscious row; Router → one of 3 stores |
| 0:30–0:33 | 2 | 2.3 | Recall ranks by recency × relevancy |
| 0:33–0:39 | 2 | 2.4 | Build Subconscious row; Day Dream mines delta, Dream dedups/governs |
| 0:39–0:42 | 2 | 2.5 | Whole machine breathes · "Conscious works. Subconscious remembers." |
| 0:42–0:47 | 3 | 3.1 | Day 1 redux; Daydream writes dots to drawer |
| 0:47–0:52 | 3 | 3.2 | Night; Dream dedups + tags (must-know star) |
| 0:52–0:57 | 3 | 3.3 | Day 2; recalls, nails same task → `✓` · "Now it remembers" |
| 0:57–0:60 | 3 | 3.4 | End card / logo / loop seam |

---

## Visual motif cheat-sheet (keep consistent across acts)
| Element | Represents | How it's drawn |
|---|---|---|
| Glowing dot | one memory / lesson | small circle + tiny icon, soft glow |
| Dot evaporating / poof | catastrophic forgetting | dot drifts up, fades, pops |
| Memory drawer / store stack | the persistent store | 3 stacked slabs = vectors · graph · markdown |
| Router highlight | route to one backend | chosen store lights, others dim |
| Ranked list | recency × relevancy | dots reorder, top one brightest |
| Shimmer sweep (day) | Daydream (write at session end) | light wipe across the dots |
| Glowing head at night | Dreaming (offline consolidation) | head lit while body sleeps |
| Dots merging | dedup / conflict resolution | two → one, version bump number |
| Colored tag / gold star | governance · must-know | small tag clipped to a dot |
| ☀️ / 🌙 + DAY N label | passage of time | corner label, sky color shift |
| `?` vs `✓` | forgot vs remembered | the core before/after contrast |

## The single most important contrast
Act 1 Shot 1.4 (`?`, re-fumbles the same task) and Act 3 Shot 3.3 (`✓`, nails it
instantly) must be **the same shot, same task, same staging** — only the outcome
differs. That side-by-side is the whole point of the video; storyboard everything
else around protecting that beat.

## Production notes
- **Tooling options:** After Effects / Rive / Lottie for hand-built vector; or a
  generative tool for a rougher first pass. SVG-based (Rive/Lottie) keeps it crisp at
  any embed size and tiny in file size for the web deck.
- **Color/asset source of truth:** reuse the deck palette + the two-band diagram in
  `architecture.md` §1 ("System diagram — Conscious / Subconscious") and
  `assets/system-diagram.svg` so the Act 2 reveal matches the architecture slide
  audiences will also see.
- **Accessibility:** since it's silent, keep labels high-contrast and on screen long
  enough to read (~1.5s minimum). It should read with sound off in any context.
- **Budget the middle:** Act 2 is the densest. If it runs long, the safe cuts are Shot
  2.3 (recall ranking) first, then collapsing Shot 2.4's dedup/contradiction into a
  single merge — never cut the before/after task beats in Acts 1 & 3.
