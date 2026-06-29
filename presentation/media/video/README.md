# Cookbook Memory — explainer video

A 60s silent animation ("The Robot Who Couldn't Remember") for the talk, built in
three acts. See [`../STORYBOARD.md`](../STORYBOARD.md) for the full beat sheet.

**Status:** all three acts prototyped — Act 1 ("The problem"), Act 2 ("Inside the
head"), Act 3 ("The solution").

## How it works

Each scene is a **pure function of time** — `draw(ctx, t)` on an HTML5 canvas,
deterministic (seeded RNG only, no `Date.now`/`Math.random`). The same scene file
therefore:

- **plays live** in the browser (and **embeds in the deck** as a looping `<iframe>`), and
- **records frame-accurately to MP4** by stepping `window.seek(t)` headlessly into ffmpeg.

This mirrors the Corellia explainer pipeline (`gauntlet/corellia/media/video/`).

```
media/video/
  film.html            THE deck embed — all three acts as one continuous loop (?scene=film)
  act1.html            Act 1 player (autoplay + loop + scrub UI)
  act2.html            Act 2 player (defaults ?scene=act2; same shell as act1.html)
  act3.html            Act 3 player (defaults ?scene=act3; same shell)
  player.html          generic player — picks scene via ?scene=; used by the tools
  scenes/lib.js        shared rig: robot, memory dots, day/night, diagram primitives, easing, palette
  scenes/act1.js       Act 1 scene — draw(ctx, t), 20s
  scenes/act2.js       Act 2 scene — draw(ctx, t), 40s
  scenes/act3.js       Act 3 scene — draw(ctx, t), 18s
  scenes/film.js       compositor — sequences act1+act2+act3 into one draw(ctx, t), 78s
  tools/record-scene.js  headless render -> build/<scene>.mp4
  tools/snap.js          headless PNG snapshots at given times (review)
  tools/check-embed.js   screenshot the deck slide with the embed
  build/               rendered output (gitignored)
```

## Use it

```bash
# live preview / review (scrub bar, loop toggle):
open act1.html                       # Act 1; act2.html for Act 2
# (player.html?scene=act1|act2 works too — it loads every scene)

# embedded in the deck: the "The film" slide iframes film.html?embed=1 (UI hidden) —
# one unified, looping animation of all three acts. The per-act players are for review.
open ../../index.html#2

# render the MP4 (24fps default for size; pass --fps=30 for the final master):
cd tools && npm install
node record-scene.js film --fps=24   # -> build/film.mp4  (all three acts, 78s)
node record-scene.js act1 --fps=24   # -> build/act1.mp4
node record-scene.js act2 --fps=24   # -> build/act2.mp4
node record-scene.js act3 --fps=24   # -> build/act3.mp4

# snapshot specific beats for review:
node snap.js film 10 19.9 21 40 59.9 61 72 77.5   # across acts + the two seams
node snap.js act1 5 9.6 11.8 17.9 19
node snap.js act2 2.5 8.5 17 20 39
node snap.js act3 2.5 5.5 9.8 13 14.5 17
```

## Notes / gotchas

- **Capture via `canvas.toDataURL()`, not element screenshots.** A Playwright
  element screenshot drops canvas `shadowBlur`/glow layers — the memory dots'
  glow vanishes. The tools read the canvas backing store directly.
- **No emoji glyphs.** Headless Chromium has no emoji font (they render as tofu
  boxes). All icons (wrench/gear/bulb/key/star/check, sparkles, moon) are drawn
  as vectors in `lib.js`. `?` and the title text are plain fonts and are fine.
- **Palette + fonts match the deck** (`presentation/assets/style.css`,
  Space Grotesk / Inter) so the embed is seamless.
- The robot is a **reusable rig** (`lib.robot`) so Act 3 can mirror Act 1
  beat-for-beat with the identical character — the storyboard's key before/after.

## Act 1 beat map (20s) — side-scrolling journey

The robot **walks** (parallax world: hills, signposts) rightward through its day,
meeting people, learning, earning a **name** and a **personality** — then night
wipes all of it.

| Time | Beat |
|---|---|
| 0.5–11.3 | DAY 1 — robot walks right, STOPPING at each of 3 tall South-Park-style humans. At each stop the human **waves**, their **speech bubble** appears above their head (the lesson) **before** the robot receives the memory dot + a personality item. Person #1 **names it "Rosie"** (name tag + sparkle). Lessons (useful dev conventions): "You're Rosie!" → 🔧, "Run tests first" → 💡, "Small commits" → 🔑. Personality accrues: hat, scarf, badge, warmer tint. Robot then walks the last human off-screen before dusk. |
| 11.4–15.0 | Night — robot slumps asleep alone, Zzz; the name, accessories, body tint and every memory dot **drain away**, staggered. |
| 15.0–16.5 | DAY 2 — a **blank grey robot**, no name, no items, back at the start of the world; wakes up. |
| 16.6–20.0 | confused "?" → title card "Every day starts from zero." |

### Reusable rig pieces added for the journey (in `lib.js`)
- `robot()` now takes `walk` (walk-cycle phase → leg/arm swing + body bounce),
  `face`, `accessories` (`hat`/`scarf`/`badge`), `tint`, `name`/`nameA`.
- `human()` — tall South-Park cutout (upright torso + distinct limbs) whose near
  arm raises in a clear wave when greeting.
- `speechBubble()` — bubble that floats ABOVE a speaker and points DOWN at them
  (anchor = top of head); renders a vector glyph or short text.
- `hills()` / `signpost()` — parallax world props that tint at night.
- `nameTag()` — the earned-name plate under the feet.

These are all generic, so Act 3 reuses the same walking/personality system for the
"now it remembers" payoff.

## Act 2 beat map (40s) — "Inside the head"

We open the robot's head and watch the architecture build itself, then run. Two
bands inside a dashed skull, faithful to `architecture.md`'s system diagram:
**CONSCIOUS** (awake, in-loop) on top — Plugin → Session, Orchestrator ↔ Memory
(Orchestrator → Session is **read/recall** only — the Session never writes);
**SUBCONSCIOUS** (offline, while idle/asleep) below — Logs · Day Dream · Model · Dream.

The back half is built to **dwell**: once both rows exist (~17.5s) the diagram
settles into a held state that **oscillates and loops forever** (no fade-out), so
there's room to talk over the final picture before the clip moves on.

A **day/night badge** at top-center makes the mode switch unmistakable: a glowing
**sun + "AWAKE"** while the conscious row + Daydream are active, crossfading to a
**crescent moon + "ASLEEP"** (with drifting z's) while Dream consolidates. The skull
backdrop warms by day and cools/darkens by night in step with it.

| Time | Beat |
|---|---|
| 0–3.5 | The head opens: dashed skull + antenna, the two empty labelled bands fade in. |
| 3.5–6.0 | CONSCIOUS row builds: Plugin, Session, Orchestrator (spinning hub), Memory (a 3-slab store: vectors / graph / markdown) pop in; arrows draw on. |
| 6.0–8.2 | A memory dot routes **Plugin → Session → Orchestrator → ONE backend** (the router picks `graph`; chosen slab lights, others dim). |
| 8.2–9.4 | **Recall** pulls a dot back out (Memory → Orchestrator → Session). |
| 9.5–14.8 | SUBCONSCIOUS row builds: Logs, Daydream, Model, Dream pop in; the cross-boundary rails draw on (append · write · read · consolidate). |
| 14.9–17.4 | The **first full write loop** closes once, end-to-end: Session **appends** a log → **Daydream** mines only the *new* delta (consulting the non-frontier Model) → promotes it **Daydream → Orchestrator → Memory**. |
| 17.5–40.0 | The machine **oscillates and holds, looping forever** — alternating a few times between **AWAKE** and **ASLEEP** (badge + backdrop track the mode). **AWAKE** (sun; conscious + Daydream bright, Dream idle): recall **Memory → Orchestrator → Session** + the in-session write loop **Session → Logs → Daydream → Orchestrator → Memory**. Then the session goes quiet → **ASLEEP** (moon + z's; Dream bright, Daydream idle): consolidate **Memory → Dream → Orchestrator → Memory**. Banner: **"Conscious works. Subconscious remembers."** |

### Diagram primitives added for Act 2 (in `lib.js`)
- `diagramNode()` — pop-in box / hub / multi-slab store node with glow halo.
- `diagramArrow()` — bowed connector with animated draw-on, arrowheads (`end`/`both`), label plate.
- `flowDot()` — a glyphed memory dot traveling one edge.
- `xrayHead()` / `band()` — the dashed skull and the two labelled conscious/subconscious bands.

The looping streams are drawn by an act2-local `streamTrain()` helper: a
phase-offset train of `flowDot`s walking a multi-leg waypoint path, periodic in `t`
so it loops seamlessly, with an `alpha` gate that fades each stream in/out as the
held state oscillates between session- and dream-activity. All deterministic, so
Act 2 records to MP4 frame-accurately exactly like Act 1.

## Act 3 beat map (18s) — "The solution"

The **same journey as Act 1, with the identical "Rosie" character** — but now memory
**persists**. Act 3 deliberately reuses Act 1's rig (`robot`/`human`/`memDot`/world
props) and staging (3 humans, same gifts/personality/name) so the before/after
contrast is exact. The single most important rhyme: Act 1 Day 2 = a blank grey
amnesiac + `?`; Act 3 Day 2 = Rosie wakes **still herself** + `✓`.

| Time | Beat |
|---|---|
| 0–7.4 | **DAY 1**, redux — Rosie walks past the same 3 humans, earns the same name + hat/scarf/badge + wrench/bulb/key memories. **But** a **memory drawer** rides under her, and a **Daydream shimmer files each dot into it** as it's received (`Daydream → write`). The memories are *saved*, not just orbiting. |
| 7.4–11.4 | **NIGHT** — Rosie slumps asleep (same as Act 1's gut-punch) **but her head GLOWS and nothing drains**. The night **Dream** sweeps the drawer: tidies, dedups, and clips a **must-know ★** onto the most important memory (`Dream → organize`). Name, accessories, tint — all retained. |
| 11.4–13.8 | **DAY 2** — Rosie wakes **still named, decorated, memories intact**, back at the start of the world. She **recalls** a dot from the drawer to her hand and nails the task — confident, no fumble. |
| 13.8–16.0 | The payoff: a green **✓** pops above her; title card **"Now it remembers."** — the exact inverse of Act 1's `?` + "Every day starts from zero." |
| 16.0–18.0 | **End card / loop seam** — ◆ Cookbook Memory + "Persistent, self-curating memory for coding agents." End frame rhymes with the Act 1 open for a clean talk loop. |

## The full film (78s) — one unified animation

`scenes/film.js` is a thin **compositor**: it plays Act 1 → Act 2 → Act 3 as a single
continuous `draw(ctx, t)` by delegating to each act's own draw with a rebased local
time, and dips through black for `SEAM` (0.9s) at each act boundary (the storyboard's
"push into the head" / "pull back out" camera moves, read as a clean deterministic
crossfade). No act code is duplicated — the acts stay editable in isolation and the
seams live in one place. Total duration is read from the registered acts, so it never
drifts (20 + 40 + 18 = 78s).

This is what the deck embeds (`film.html?embed=1`) — a single looping iframe, so the
talk shows the whole arc as one native animation rather than three separate clips. The
per-act `actN.html` players remain for reviewing one act in isolation.

The persistent **memory drawer** (an act3-local `memoryDrawer()` helper) is the key
new visual: a small 3-slot store glued under Rosie in screen-space that travels with
her and holds the filed coins, glowing teal while the subconscious works. Filing uses
a brief `daydreamShimmer()` light-wipe; recall flies a dot back out to her hand. All
deterministic — records to MP4 frame-accurately exactly like Acts 1 & 2.
