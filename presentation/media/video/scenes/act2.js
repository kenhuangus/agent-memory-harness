// ACT 2 — "Inside the Head": build the Conscious / Subconscious architecture. 40.0s.
// Faithful to architecture.md §1 (System diagram — Conscious / Subconscious):
//   CONSCIOUS (top, live path):  Plugin -> Session <- Orchestrator <-> Memory(3 stores)
//   SUBCONSCIOUS (bottom, offline): Logs(.jsonl) · Daydream · Model(not frontier) · Dream
//   cross-boundary: Session->Logs, Daydream->Orch->Mem (write), Mem->Dream->Orch->Mem (consolidate)
//
// Shots:
//   2.1 (0.0-3.5)   open the X-ray head; two bands fade in
//   2.2 (3.5-9.5)   build the Conscious row; a dot routes to ONE of 3 stores; recall pulls one back
//   2.3 (9.5-17.5)  build the Subconscious row; Session appends a log; Daydream mines the delta
//   2.4 (17.5-40.0) the machine OSCILLATES, looping forever (no fade): session-activity
//                   (recall Mem->Orch->Session + append Session->Logs), then — once the session
//                   goes quiet — dream-activity (Daydream->Orch->Mem promote, Mem->Dream->Orch->Mem
//                   consolidate), and back, a few times. Banner "Conscious works. Subconscious remembers."
//
// Pure function of time: draw(ctx, t). Deterministic (seeded RNG only).
'use strict';
(() => {
  const L = window.LIB;
  const { W, H, C, seg, clamp01, mix, lerp,
          diagramNode, diagramArrow, flowDot, xrayHead, band, glowCircle, zzz,
          FONT, FONTR, storeSlabCY } = L;

  // palette echoing architecture.md classDefs
  const HARNESS = { fill: '#eaf2ff', stroke: '#3b6fb0', ink: '#10243e' };  // plugin/session
  const CORE    = { fill: '#fff1df', stroke: '#c97a1a', ink: '#3e2710' };  // orchestrator
  const STORE   = { fill: '#fff8e0', stroke: '#b08900', ink: '#3e3410' };  // memory stores
  const SUB     = { fill: '#efe8ff', stroke: '#7a52c0', ink: '#2a1a4e' };  // day dream / dream / model
  const LOG     = { fill: '#eef0f2', stroke: '#7a8088', ink: '#1f2429' };  // logs

  // ---- stage geometry (inside the head) ----
  const HEAD_CX = W / 2, HEAD_CY = H / 2 + 10, HEAD_W = 1640, HEAD_H = 920;
  const TOP_Y = H * 0.36;        // conscious row center-y
  const BOT_Y = H * 0.66;        // subconscious row center-y
  const NW = 250, NH = 104;      // default node box

  // conscious row x positions
  const X_PLUGIN = W * 0.16, X_SESS = W * 0.385, X_ORCH = W * 0.60, X_MEM = W * 0.835;
  // subconscious row x positions
  const X_LOGS = W * 0.30, X_DAY = W * 0.48, X_MODEL = W * 0.645, X_DREAM = W * 0.80;

  const N = {
    plugin:  { x: X_PLUGIN, y: TOP_Y, w: NW,  h: NH, title: 'Plugin',       sub: 'skills · MCP · hooks', ...HARNESS },
    session: { x: X_SESS,   y: TOP_Y, w: NW,  h: NH, title: 'Session',      sub: 'message history',     ...HARNESS },
    orch:    { x: X_ORCH,   y: TOP_Y, w: 188, h: 188, title: 'Orchestrator', sub: 'route·rank·dedup', shape: 'hub', ...CORE },
    mem:     { x: X_MEM,    y: TOP_Y, w: 280, h: 150, title: 'Memory',       sub: 'vectors·graph·markdown', shape: 'store', ...STORE },
    logs:    { x: X_LOGS,   y: BOT_Y, w: 220, h: NH, title: 'Logs',         sub: '.jsonl',              ...LOG },
    day:     { x: X_DAY,    y: BOT_Y, w: 250, h: NH, title: 'Daydream',      sub: 'in-session / idle',   ...SUB },
    model:   { x: X_MODEL,  y: BOT_Y, w: 200, h: 86, title: 'Model',        sub: 'not frontier',        ...SUB },
    dream:   { x: X_DREAM,  y: BOT_Y, w: 250, h: NH, title: 'Dream',        sub: 'offline / after',     ...SUB },
  };

  // node edge anchors
  const right = n => ({ x: n.x + (n.shape === 'hub' ? n.w / 2 : n.w / 2), y: n.y });
  const left  = n => ({ x: n.x - (n.shape === 'hub' ? n.w / 2 : n.w / 2), y: n.y });
  const bottom= n => ({ x: n.x, y: n.y + (n.shape === 'hub' ? n.w / 2 : n.h / 2) });
  const top   = n => ({ x: n.x, y: n.y - (n.shape === 'hub' ? n.w / 2 : n.h / 2) });

  // generic appear ramp
  const appear = (t, t0, d = 0.5) => clamp01(seg(t, t0, t0 + d));

  window.SCENES.act2 = {
    duration: 40.0,
    bg: '#0b1626',
    draw(ctx, t) {
      // ---- AWAKE / ASLEEP mode signal (drives the day/night indicator + a head-wide wash) ----
      // night01: 0 = AWAKE (day), 1 = ASLEEP (night). Flat 0 during the build; oscillates once alive.
      const ALIVE_T = 17.5;
      const CYCLE = 9.0, AWAKE_WIN = 4.4, ASLEEP_WIN = 4.4;       // (mirrored below for stream gating)
      const alive0 = clamp01(seg(t, ALIVE_T, ALIVE_T + 1.0));
      const cyc0 = ((t - ALIVE_T) % CYCLE);
      // smoothstep up into night after the awake window, back down to day at the cycle's end
      const ss = x => x * x * (3 - 2 * x);
      const night01 = alive0 * (ss(clamp01(seg(cyc0, AWAKE_WIN + 0.1, AWAKE_WIN + 1.1)))
                              * (1 - ss(clamp01(seg(cyc0, CYCLE - 0.9, CYCLE - 0.1)))));

      // dark "inside the head" backdrop — warms slightly by day, cools/darkens by night
      const g = ctx.createRadialGradient(HEAD_CX, HEAD_CY, 120, HEAD_CX, HEAD_CY, 1100);
      g.addColorStop(0, mix('#1a2c43', '#101d33', night01)); g.addColorStop(1, mix('#0b1424', '#070d18', night01));
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

      // ---- 2.1 open the head + bands ----
      const headA = appear(t, 0.2, 1.0);
      xrayHead(ctx, HEAD_CX, HEAD_CY, HEAD_W, HEAD_H, headA * 0.9);

      const bandsA = appear(t, 1.0, 1.0);
      const bandX = HEAD_CX - HEAD_W / 2 + 40, bandW = HEAD_W - 80;
      band(ctx, bandX, TOP_Y - 150, bandW, 250, 'CONSCIOUS  ·  awake, in-loop',
           'rgba(59,111,176,0.16)', '#bcd8f5', bandsA);
      band(ctx, bandX, BOT_Y - 130, bandW, 240, 'SUBCONSCIOUS  ·  offline, while idle / asleep',
           'rgba(122,82,192,0.16)', '#d8c8f5', bandsA);

      // ---- day/night mode badge: makes the AWAKE <-> ASLEEP switch unmistakable ----
      drawModeBadge(ctx, HEAD_CX, 158, night01, headA, t);

      // ============ CONSCIOUS ROW (2.2) ============
      // nodes pop in left -> right
      const pPlugin = appear(t, 3.5, 0.5);
      const pSess   = appear(t, 4.1, 0.5);
      const pOrch   = appear(t, 4.7, 0.5);
      const pMem    = appear(t, 5.3, 0.5);

      // arrows draw on after their nodes
      const aPS = appear(t, 4.3, 0.4);   // plugin->session
      const aSO = appear(t, 4.9, 0.4);   // session<->orch
      const aOM = appear(t, 5.5, 0.4);   // orch<->mem

      // ---- which store is the router targeting? (highlight one, dim others) ----
      // the routing demo runs 6.0-8.0; recall pulls back 8.2-9.4
      const routing = t >= 6.0 && t < 8.2;
      const chosenStore = 1;             // graph (middle slab) for this demo
      const orchSpin = (t > 4.7) ? t * 2.2 : 0;

      // draw conscious arrows
      diagramArrow(ctx, right(N.plugin).x, right(N.plugin).y, left(N.session).x, left(N.session).y, aPS, { color: HARNESS.stroke });
      // Orchestrator feeds the Session (read / recall); the Session does not write, so the head points only at the Session.
      diagramArrow(ctx, left(N.orch).x, left(N.orch).y, right(N.session).x, right(N.session).y, aSO, { color: CORE.stroke, arrows: 'end', label: t > 5 ? 'read / recall' : null });
      diagramArrow(ctx, right(N.orch).x, right(N.orch).y, left(N.mem).x, left(N.mem).y, aOM, { color: STORE.stroke, arrows: 'both', label: t > 5.6 ? 'R / W' : null });

      // ============ SUBCONSCIOUS ROW (2.3) ============
      const pLogs  = appear(t, 9.7,  0.5);
      const pDay   = appear(t, 10.4, 0.5);
      const pModel = appear(t, 11.8, 0.5);
      const pDream = appear(t, 12.8, 0.5);

      // cross-boundary + bottom-row arrows (lay down the three memory streams' rails)
      const aSL = appear(t, 10.1, 0.4);   // session -> logs (append)
      const aDL = appear(t, 10.8, 0.4);   // logs <-> day (adapter, dashed)
      const aDM = appear(t, 12.1, 0.4);   // day <-> model
      const aDO = appear(t, 13.4, 0.4);   // day -> orch  (write/promote, up across boundary)
      const aMD = appear(t, 14.2, 0.4);   // mem -> dream (read)
      const aDD = appear(t, 14.9, 0.4);   // dream -> orch (consolidated write back up)

      // Stream A rail: Session -> Logs (append)
      diagramArrow(ctx, bottom(N.session).x, bottom(N.session).y, top(N.logs).x, top(N.logs).y, aSL, { color: LOG.stroke, label: aSL > 0.6 ? 'append' : null });
      diagramArrow(ctx, right(N.logs).x, right(N.logs).y, left(N.day).x, left(N.day).y, aDL, { color: SUB.stroke, dashed: true, arrows: 'both' });
      // Daydream consults the (non-frontier) Model
      diagramArrow(ctx, right(N.day).x, right(N.day).y, left(N.model).x, left(N.model).y, aDM, { color: SUB.stroke, arrows: 'both' });
      // Stream B rail: Daydream -> Orchestrator (write/promote), then Orchestrator -> Memory (the conscious R/W edge)
      diagramArrow(ctx, N.day.x, N.day.y - N.day.h / 2, bottom(N.orch).x - 30, bottom(N.orch).y, aDO, { color: SUB.stroke, label: aDO > 0.6 ? 'write' : null, bend: -60 });
      // Stream C rails: Memory -> Dream (read), then Dream -> Orchestrator (consolidated write back up)
      diagramArrow(ctx, bottom(N.mem).x, bottom(N.mem).y, top(N.dream).x, top(N.dream).y, aMD, { color: STORE.stroke, label: aMD > 0.6 ? 'read' : null, bend: 40 });
      diagramArrow(ctx, top(N.dream).x + 40, top(N.dream).y, bottom(N.orch).x + 30, bottom(N.orch).y, aDD, { color: SUB.stroke, label: aDD > 0.6 ? 'consolidate' : null, bend: 50 });

      // ---- node glows (active nodes light up during their beat) ----
      const glow = (a, b, base = 0) => (t >= a && t < b) ? 1 : base;
      // The whole machine comes alive once both rows are built (>= 17.5).  (ALIVE_T/CYCLE/*_WIN defined up top.)
      const alive = alive0;

      // ---- the held final state OSCILLATES between AWAKE and ASLEEP, a few times ----
      // AWAKE  (session running): recall + append + Daydream promotes in-session.   Dream is idle.
      // ASLEEP (session stopped): Dream consolidates offline.                       Daydream is idle.
      // (architecture: Daydream = "in-session / idle" worker; Dream = "offline / after" worker.)
      const cyc = cyc0;                                 // 0..CYCLE within the current oscillation
      // smooth envelope that ramps up over `fade`, holds, ramps down — within [a,b]
      const env = (x, a, b, fade = 0.7) => clamp01(seg(x, a, a + fade)) * (1 - clamp01(seg(x, b - fade, b)));
      const awakeAct  = alive * env(cyc, 0.0, AWAKE_WIN);                              // conscious + Daydream busy
      const asleepAct = alive * env(cyc, AWAKE_WIN + 0.4, AWAKE_WIN + 0.4 + ASLEEP_WIN); // Dream busy
      const breathe = (ph, act) => alive * 0.35 + act * (0.4 + 0.25 * Math.sin(t * 3 + ph));

      // draw all nodes (conscious) — bright while AWAKE
      diagramNode(ctx, { ...N.plugin,  pop: pPlugin,  glow: glow(6.0, 6.6) + breathe(0, awakeAct) });
      diagramNode(ctx, { ...N.session, pop: pSess,    glow: glow(6.4, 7.0) + glow(8.8, 9.4) + breathe(1, awakeAct) });
      // orchestrator hub with spinning ticks — alive in BOTH phases (it's the router for each)
      diagramNode(ctx, { ...N.orch,    pop: pOrch,    glow: (routing ? 1 : 0) + alive * 0.45 + Math.max(awakeAct, asleepAct) * 0.5 });
      if (pOrch > 0.5) drawHubTicks(ctx, N.orch.x, N.orch.y, N.orch.w / 2, orchSpin, CORE.stroke, routing || alive > 0);
      diagramNode(ctx, { ...N.mem,     pop: pMem,     glow: glow(7.4, 8.2) + alive * 0.4 + Math.max(awakeAct, asleepAct) * 0.4 });
      // store-slab highlight: the chosen backend lights, others dim, during routing
      if (routing || (t >= 8.2 && t < 9.4)) drawStoreHighlight(ctx, N.mem, chosenStore, t);

      // draw all nodes (subconscious) — Daydream lights while AWAKE, Dream lights while ASLEEP
      diagramNode(ctx, { ...N.logs,  pop: pLogs,  glow: glow(10.6, 11.4) + alive * 0.3 + awakeAct * 0.3 });
      diagramNode(ctx, { ...N.day,   pop: pDay,   glow: glow(10.6, 16.2) + breathe(2, awakeAct) });
      diagramNode(ctx, { ...N.model, pop: pModel, glow: glow(12.1, 12.8) + alive * 0.25 + awakeAct * 0.4 });
      diagramNode(ctx, { ...N.dream, pop: pDream, glow: glow(14.2, 16.6) + breathe(3, asleepAct) });

      // ============ ANIMATED FLOWS ============
      // 2.2 a memory dot travels Plugin -> Session -> Orch, then routes to ONE store
      if (t >= 6.0 && t < 8.2) {
        const k = seg(t, 6.0, 8.0);
        if (k < 0.30) {
          flowDot(ctx, right(N.plugin).x, TOP_Y, left(N.session).x, TOP_Y, k / 0.30, C.violet, { glyph: 'wrench' });
        } else if (k < 0.58) {
          flowDot(ctx, right(N.session).x, TOP_Y, left(N.orch).x, TOP_Y, (k - 0.30) / 0.28, C.violet, { glyph: 'wrench' });
        } else {
          // route from orch to the chosen store slab
          const sy = storeSlabY(N.mem, chosenStore);
          flowDot(ctx, right(N.orch).x, TOP_Y, N.mem.x, sy, (k - 0.58) / 0.42, C.violet, { glyph: 'wrench' });
        }
      }
      // 2.2->2.3 recall pulls a dot back out (Memory -> Orchestrator -> Session)
      if (t >= 8.2 && t < 9.4) {
        const sy = storeSlabY(N.mem, chosenStore);
        const k = seg(t, 8.3, 9.3);
        if (k < 0.5) flowDot(ctx, N.mem.x, sy, left(N.orch).x, TOP_Y, k / 0.5, C.teal, { glyph: 'bulb' });
        else flowDot(ctx, left(N.orch).x, TOP_Y, right(N.session).x, TOP_Y, (k - 0.5) / 0.5, C.teal, { glyph: 'bulb' });
      }

      // ---- 2.3 close the FIRST full write loop once, end-to-end, before the held oscillation ----
      // Session -> Logs (append) -> Daydream (mine delta) -> Orchestrator -> Memory (promote).
      if (t >= 14.9 && t < 15.5) {            // session appends a log line
        flowDot(ctx, bottom(N.session).x, bottom(N.session).y, top(N.logs).x, top(N.logs).y, seg(t, 14.9, 15.5), LOG.stroke, { r: 14 });
      }
      if (t >= 15.5 && t < 16.5) drawDeltaMine(ctx, N.logs, N.day, t);  // mine only the NEW entries
      if (t >= 16.4 && t < 17.4) {            // Daydream promotes the mined memory up through the router into the bank
        const k = seg(t, 16.4, 17.4);
        if (k < 0.5) flowDot(ctx, N.day.x, N.day.y - N.day.h / 2, bottom(N.orch).x - 30, bottom(N.orch).y, k / 0.5, C.green, { glyph: 'star', bend: -60 });
        else flowDot(ctx, right(N.orch).x, TOP_Y, N.mem.x, storeSlabY(N.mem, 0), (k - 0.5) / 0.5, C.green, { glyph: 'star' });
      }

      // ============ 2.4 — MEMORY STREAMS, oscillating AWAKE <-> ASLEEP ============
      if (t >= ALIVE_T) {
        const flow = (t - ALIVE_T);   // seconds since the machine came alive
        // ---- AWAKE phase: the conscious loop reads, and Daydream learns in-session ----
        // Recall: Memory -> Orchestrator -> Session — the read path back to the session.
        streamTrain(ctx, [P(left(N.mem)), P(right(N.orch)), P(left(N.orch)), P(right(N.session))],
                    flow, 0.16, 3, C.teal, { r: 11, glyph: 'bulb', alpha: awakeAct });
        // Daydream write-loop (one continuous chain): Session -> Logs -> Daydream -> Orchestrator -> Memory.
        streamTrain(ctx, [P(bottom(N.session)), P(top(N.logs)),
                          P(right(N.logs)), P(left(N.day)),
                          P(N.day.x, N.day.y - N.day.h / 2, -60), P(bottom(N.orch).x - 30, bottom(N.orch).y),
                          P(right(N.orch)), P(N.mem.x, storeSlabY(N.mem, 0))],
                    flow + 0.20, 0.085, 3, C.green, { r: 11, glyph: 'star', alpha: awakeAct });

        // ---- ASLEEP phase (session has stopped): Dream consolidates offline ----
        // Consolidate: Memory -> Dream -> Orchestrator -> Memory.
        streamTrain(ctx, [P(bottom(N.mem)), P(top(N.dream).x, top(N.dream).y, 40),
                          P(top(N.dream).x + 40, top(N.dream).y), P(bottom(N.orch).x + 30, bottom(N.orch).y, 50),
                          P(right(N.orch)), P(N.mem.x, storeSlabY(N.mem, 2))],
                    flow + 0.30, 0.13, 3, C.teal, { r: 11, glyph: 'bulb', alpha: asleepAct });
      }

      // ---- shot title captions (small, bottom) — none during the held final state ----
      caption(ctx, t, 6.0, 9.4, 'Router → one backend. Then recall pulls it back.');
      caption(ctx, t, 10.0, 17.2, 'Session logs everything · Daydream mines new entries');

      // ---- banner: enters once alive, then HOLDS forever (no fade-out — the diagram loops) ----
      if (t >= ALIVE_T + 0.8) {
        const a = seg(t, ALIVE_T + 0.8, ALIVE_T + 1.5);
        ctx.save(); ctx.globalAlpha = a;
        ctx.font = FONT(58); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillStyle = '#eaf2ff'; ctx.shadowColor = '#1971c2'; ctx.shadowBlur = 24;
        ctx.fillText('Conscious works.  Subconscious remembers.', W / 2, H * 0.90);
        ctx.restore();
      }
    },
  };

  // ---- helpers ----
  const storeSlabY = (mem, idx) => storeSlabCY(mem, idx);

  // A compact day/night badge: a sun (AWAKE) crossfading to a crescent moon (ASLEEP),
  // with a pill label and drifting z's while asleep.  night: 0=day, 1=night.
  function drawModeBadge(ctx, cx, cy, night, appearA, t) {
    if (appearA <= 0.01) return;
    const day = 1 - night;
    ctx.save();
    ctx.globalAlpha = appearA;

    const iconX = cx - 118, iconY = cy;   // icon sits left of the label
    // --- SUN (day) ---
    if (day > 0.01) {
      ctx.save(); ctx.globalAlpha = appearA * day;
      glowCircle(ctx, iconX, iconY, 22, '#ffd23f', 30);
      // rays
      ctx.strokeStyle = '#ffd23f'; ctx.lineWidth = 4; ctx.lineCap = 'round';
      for (let i = 0; i < 8; i++) {
        const a = i * Math.PI / 4 + t * 0.25;
        ctx.beginPath();
        ctx.moveTo(iconX + Math.cos(a) * 30, iconY + Math.sin(a) * 30);
        ctx.lineTo(iconX + Math.cos(a) * 40, iconY + Math.sin(a) * 40);
        ctx.stroke();
      }
      ctx.restore();
    }
    // --- MOON (night) ---
    if (night > 0.01) {
      ctx.save(); ctx.globalAlpha = appearA * night;
      glowCircle(ctx, iconX, iconY, 24, '#cdd7ea', 26);
      // crescent: punch an offset circle out with the backdrop colour
      ctx.fillStyle = mix('#101d33', '#070d18', 1);
      ctx.beginPath(); ctx.arc(iconX + 11, iconY - 6, 22, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      // a couple of twinkle stars + drifting z's
      ctx.save(); ctx.globalAlpha = appearA * night;
      ctx.fillStyle = '#cdd7ea';
      ctx.beginPath(); ctx.arc(iconX + 34, iconY - 22, 2.2, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(iconX - 30, iconY + 20, 1.8, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      zzz(ctx, iconX + 30, iconY - 18, t, appearA * night * 0.9);
    }

    // --- label pill ---
    const label = night > 0.5 ? 'ASLEEP' : 'AWAKE';
    const ink = mix('#ffe9a8', '#c3d0ea', night);
    ctx.font = FONT(30); ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    const lw = ctx.measureText(label).width;
    const px = cx - 70, py = cy - 24, pw = lw + 40, phh = 48;
    ctx.globalAlpha = appearA * 0.9;
    L.roundRect(ctx, px, py, pw, phh, 24, mix('rgba(255,210,63,0.14)', 'rgba(140,160,210,0.16)', night));
    ctx.strokeStyle = mix('rgba(255,210,63,0.5)', 'rgba(170,190,230,0.5)', night);
    ctx.lineWidth = 2; L.roundRectStroke(ctx, px, py, pw, phh, 24);
    ctx.globalAlpha = appearA;
    ctx.fillStyle = ink; ctx.fillText(label, px + 20, cy + 1);
    ctx.restore();
  }

  // Build a waypoint {x, y, bend} from a point object {x,y} or explicit coords.
  // bend bows the leg LEAVING this waypoint (matches diagramArrow/flowDot bow).
  function P(a, b, bend) {
    if (typeof a === 'object') return { x: a.x, y: a.y, bend: b || 0 };
    return { x: a, y: b, bend: bend || 0 };
  }
  // A train of `count` phase-offset dots traveling a polyline of waypoints.
  // `flow` is seconds-since-alive; `speed` is laps/sec. Periodic => seamless loop.
  // opts.alpha gates/fades the whole train (used to oscillate session vs dream activity).
  function streamTrain(ctx, pts, flow, speed, count, color, opts = {}) {
    const alpha = opts.alpha === undefined ? 1 : opts.alpha;
    if (alpha <= 0.02) return;
    // per-leg lengths (straight-line approximation) to pace dots evenly along the path
    const legs = [];
    let total = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const len = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y) || 1;
      legs.push(len); total += len;
    }
    ctx.save(); ctx.globalAlpha = alpha;
    for (let d = 0; d < count; d++) {
      const phase = (flow * speed + d / count) % 1;
      // map global phase -> which leg + local frac, weighted by leg length
      let dist = phase * total, li = 0;
      while (li < legs.length - 1 && dist > legs[li]) { dist -= legs[li]; li++; }
      const frac = clamp01(dist / legs[li]);
      const a = pts[li], b = pts[li + 1];
      flowDot(ctx, a.x, a.y, b.x, b.y, frac, color, { glyph: opts.glyph || null, r: opts.r || 12, bend: a.bend || 0 });
    }
    ctx.restore();
  }

  function drawStoreHighlight(ctx, mem, idx, t) {
    const w = mem.w, sh = mem.h * 0.30;
    for (let i = 0; i < 3; i++) {
      const cy = storeSlabCY(mem, i);
      ctx.save();
      if (i === idx) {
        ctx.strokeStyle = C.accent; ctx.lineWidth = 4; ctx.shadowColor = C.accent; ctx.shadowBlur = 18;
        L.roundRectStroke(ctx, mem.x - w / 2, cy - sh / 2, w, sh, 8);
      } else {
        ctx.globalAlpha = 0.5; ctx.fillStyle = 'rgba(10,19,34,0.5)';
        L.roundRect(ctx, mem.x - w / 2, cy - sh / 2, w, sh, 8, 'rgba(10,19,34,0.5)');
      }
      ctx.restore();
    }
  }
  function drawHubTicks(ctx, x, y, r, spin, color, active) {
    ctx.save();
    ctx.translate(x, y); ctx.rotate(spin);
    ctx.strokeStyle = color; ctx.lineWidth = 4; ctx.lineCap = 'round';
    ctx.globalAlpha = active ? 0.9 : 0.45;
    for (let i = 0; i < 8; i++) {
      ctx.rotate(Math.PI / 4);
      ctx.beginPath(); ctx.moveTo(r - 22, 0); ctx.lineTo(r - 8, 0); ctx.stroke();
    }
    ctx.restore();
  }
  function drawDeltaMine(ctx, logs, day, t) {
    // a little stack of log lines; the NEW ones (bottom 2) get grabbed, old ones dimmed
    const k = seg(t, 15.5, 16.5);
    ctx.save();
    const lx = logs.x, ly = logs.y;
    for (let i = 0; i < 4; i++) {
      const isNew = i >= 2;
      ctx.globalAlpha = isNew ? 1 : 0.3;
      ctx.fillStyle = isNew ? SUB.stroke : LOG.stroke;
      L.roundRect(ctx, lx - 70, ly - 30 + i * 16, 140, 8, 4, isNew ? SUB.stroke : LOG.stroke);
    }
    // new entries fly toward day dream
    if (k > 0.3) {
      const kk = (k - 0.3) / 0.7;
      flowDot(ctx, logs.x + 40, logs.y, day.x, day.y, kk, SUB.stroke, { r: 12 });
    }
    ctx.restore();
  }
  function caption(ctx, t, t0, t1, text) {
    const a = seg(t, t0, t0 + 0.4) * (1 - seg(t, t1 - 0.4, t1));
    if (a <= 0) return;
    ctx.save(); ctx.globalAlpha = a;
    ctx.font = FONTR(28); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = 'rgba(220,230,245,0.92)';
    ctx.fillText(text, W / 2, H * 0.92);
    ctx.restore();
  }
})();
