// ACT 3 — "The Solution": the SAME journey as Act 1, but now memory persists. 18.0s.
// Storyboard (presentation/STORYBOARD.md, Act 3 mirrors Act 1 beat-for-beat):
//   DAY 1 (0.0 - 7.4): Rosie walks right, STOPPING at the same 3 humans, receiving the
//     same gifts (wrench/bulb/key) + personality (hat/scarf/badge) + her name. BUT now a
//     MEMORY DRAWER rides under her, and a Daydream shimmer FILES each dot into it
//     (Daydream -> write). The dots are saved, not just orbiting.
//   NIGHT (7.4 - 11.4): Rosie slumps asleep — but her HEAD GLOWS and NOTHING drains. The
//     night Dream sweeps the drawer: dedups (two dots merge), tidies, clips a must-know
//     star (Dream -> organize). Name/accessories/tint all stay.
//   DAY 2 (11.4 - 16.0): Rosie wakes STILL named, decorated, memories intact, back at the
//     start. She RECALLS a dot to her hand and nails it: confident "check". "Now it
//     remembers." — the exact rhyme with Act 1's blank-robot "?".
//   END CARD (16.0 - 18.0): Cookbook Memory diamond + tagline. Loops back to the open.
//
// Pure function of time: draw(ctx, t). Deterministic (seeded RNG only). Reuses the Act 1
// rig (robot/human/memDot/...) so Rosie is the identical character across before/after.
'use strict';
(() => {
  const L = window.LIB;
  const { W, H, C, seg, easeOut, easeIn, easeInOut, easeOutBack, lerp, clamp01,
          backdrop, robot, human, speechBubble, hills, signpost,
          memDot, drawGlyph, glowDotRaw, roundRect, roundRectStroke,
          dayLabel, titleCard, zzz, drawSparkle, mix, FONT, FONTR, vignette } = L;

  const BOT_SCREEN_X = W * 0.40;                      // Rosie stays here; world moves (= Act 1)
  const NAME = 'Rosie';

  // SAME three people, SAME gifts/personality/name as Act 1 — so the journey rhymes.
  const PEOPLE = [
    { wx: 980,  color: C.violet, say: "You're Rosie!",   gift: 'wrench', gain: 'hat',   name: NAME },
    { wx: 1680, color: C.teal,   say: 'Run tests first', gift: 'bulb',   gain: 'scarf' },
    { wx: 2380, color: C.orange, say: 'Small commits',   gift: 'key',    gain: 'badge' },
  ];

  // ---- stop-and-go timeline (same shape as Act 1, slightly tighter to fit 18s) ----
  const MEET_GAP = 180;
  const stationX = PEOPLE.map(p => p.wx - MEET_GAP);
  const WALK = [1.1, 1.15, 1.15, 1.5];                // 4 walk legs (last = exit to dusk)
  const STOP = 1.05;                                  // pause at each station
  const SEGS = [];
  let tc = 0.4, sc = 0;
  for (let i = 0; i < PEOPLE.length; i++) {
    SEGS.push({ t0: tc, t1: tc + WALK[i], from: sc, to: stationX[i], kind: 'walk' });
    tc += WALK[i]; sc = stationX[i];
    SEGS.push({ t0: tc, t1: tc + STOP, from: sc, to: sc, kind: 'stop', person: i });
    tc += STOP;
  }
  const EXIT_SCROLL = PEOPLE[PEOPLE.length - 1].wx + BOT_SCREEN_X + 220;
  SEGS.push({ t0: tc, t1: tc + WALK[3], from: sc, to: EXIT_SCROLL, kind: 'walk' });
  tc += WALK[3];
  const WALK_END = tc;                                // ~7.4

  function sceneState(t) {
    if (t >= WALK_END) return { scroll: SEGS[SEGS.length - 1].to, walking: false, atPerson: null };
    for (const s of SEGS) {
      if (t >= s.t0 && t < s.t1) {
        if (s.kind === 'walk') {
          const k = easeInOut(seg(t, s.t0, s.t1));
          return { scroll: lerp(s.from, s.to, k), walking: true, atPerson: null };
        }
        return { scroll: s.from, walking: false, atPerson: s.person };
      }
    }
    return { scroll: 0, walking: t > 0.4, atPerson: null };
  }
  const toScreen = (wx, scroll) => BOT_SCREEN_X + (wx - scroll);

  // ---- the persistent memory drawer ----------------------------------------
  // A small store glued under Rosie's feet that travels WITH her (screen-space), holding
  // the filed memories as little coins. This is the visual "memory persists" payoff.
  // dots: array of {gift, color, star?} already filed. fill 0..1 = how built the drawer is.
  function memoryDrawer(ctx, cx, topY, dots, { build = 1, glow = 0, alpha = 1 } = {}) {
    if (build <= 0.001 || alpha <= 0.001) return [];
    ctx.save();
    ctx.globalAlpha = alpha;
    const slots = 3;
    const dw = 76, dh = 60, gap = 10;
    const totalW = slots * dw + (slots - 1) * gap;
    const x0 = cx - totalW / 2;
    const p = easeOutBack(clamp01(build));
    ctx.translate(cx, topY); ctx.scale(1, p); ctx.translate(-cx, -topY);
    // drawer body
    if (glow > 0) { ctx.shadowColor = C.teal; ctx.shadowBlur = 22 * glow; }
    roundRect(ctx, x0 - 14, topY - 12, totalW + 28, dh + 26, 14, 'rgba(20,36,62,0.92)');
    ctx.shadowBlur = 0;
    ctx.strokeStyle = mix('#2b6f86', C.teal, glow); ctx.lineWidth = 3;
    roundRectStroke(ctx, x0 - 14, topY - 12, totalW + 28, dh + 26, 14);
    // label
    ctx.fillStyle = 'rgba(180,230,245,0.9)'; ctx.font = FONTR(17);
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText('memory', cx, topY - 16);
    // slots + their coins; return each slot center so callers can fly dots into them
    const centers = [];
    for (let i = 0; i < slots; i++) {
      const sx = x0 + i * (dw + gap) + dw / 2, sy = topY + dh / 2 + 1;
      centers.push({ x: sx, y: sy });
      roundRect(ctx, sx - dw / 2, topY + 1, dw, dh, 10, 'rgba(255,255,255,0.06)');
      ctx.strokeStyle = 'rgba(143,227,255,0.25)'; ctx.lineWidth = 1.5;
      roundRectStroke(ctx, sx - dw / 2, topY + 1, dw, dh, 10);
      const d = dots[i];
      if (d) {
        memDot(ctx, sx, sy, 21, d.color, { glyph: d.gift, glow: glow > 0 ? 16 : 8 });
        if (d.star) drawGlyph(ctx, sx + 17, sy - 17, 11, 'star', C.amber);
      }
    }
    ctx.restore();
    return centers;
  }

  window.SCENES.act3 = {
    duration: 18.0,
    bg: '#bfe3ff',
    draw(ctx, t) {
      // ----- day/night: same arc as Act 1, but Day-2 wake keeps everything -----
      let night = 0;
      if (t < 7.5) night = 0;
      else if (t < 9.0) night = easeInOut(seg(t, 7.5, 9.0));
      else if (t < 11.0) night = 1;
      else if (t < 12.2) night = 1 - easeInOut(seg(t, 11.0, 12.2));
      else night = 0;

      const gy = backdrop(ctx, night);
      const st = sceneState(t);
      const scroll = t < 11.0 ? st.scroll : 0;          // Day 2 resets to start of world

      // ===== parallax world (identical props/positions to Act 1) =====
      hills(ctx, scroll, gy, night, { color: '#9ec2e0', amp: 90, span: 680, y: -40, speed: 0.12 });
      hills(ctx, scroll, gy, night, { color: '#a8c98f', amp: 70, span: 520, y: 6,   speed: 0.30 });
      for (const swx of [520, 1340, 2040, 2680]) {
        const sx = toScreen(swx, scroll);
        if (sx > -120 && sx < W + 120) signpost(ctx, sx, gy, night);
      }

      // which people has Rosie already finished meeting (gift received)?
      const GIFT_AT = 0.50;
      const metCount = (() => {
        let n = 0;
        for (let i = 0; i < PEOPLE.length; i++) {
          const stopSeg = SEGS.find(s => s.kind === 'stop' && s.person === i);
          if (t >= stopSeg.t0 + (stopSeg.t1 - stopSeg.t0) * GIFT_AT) n = i + 1;
        }
        return n;
      })();

      // ===== Rosie's state =====
      const s = {
        x: BOT_SCREEN_X, baseY: gy, t,
        walk: (st.walking && t < WALK_END + 0.05) ? 1 : 0,
        face: 1, eye: 1, look: 0, emote: 'none',
        asleep: 0, accessories: [], tint: 0, name: null, nameA: 0,
      };

      // personality + held dots accrue from people met (NEVER drains in Act 3)
      const dotsHeld = [];
      for (let i = 0; i < metCount; i++) {
        const p = PEOPLE[i];
        if (p.gain && !s.accessories.includes(p.gain)) s.accessories.push(p.gain);
        dotsHeld.push({ i, gift: p.gift, color: p.color });
        if (p.name) s.name = p.name;
      }
      s.tint = (metCount / PEOPLE.length) * 0.9;
      if (s.name) {
        const stop0 = SEGS.find(ss => ss.kind === 'stop' && ss.person === 0);
        s.nameA = clamp01(seg(t, stop0.t0 + (stop0.t1 - stop0.t0) * GIFT_AT, stop0.t1 + 0.3));
      }
      if (!st.walking && st.atPerson !== null) s.look = 0.5;
      if (!st.walking && metCount >= 1 && st.atPerson === null) s.emote = 'happy';

      // ===== which filed dots live in the drawer, and is one a must-know star? =====
      // A dot is "filed" shortly after it's received (the Daydream shimmer writes it).
      const FILE_LAG = 0.55;                            // seconds after receipt -> filed
      const filedDots = [];
      for (let i = 0; i < metCount; i++) {
        const p = PEOPLE[i];
        const stopSeg = SEGS.find(ss => ss.kind === 'stop' && ss.person === i);
        const born = stopSeg.t0 + (stopSeg.t1 - stopSeg.t0) * GIFT_AT;
        if (t >= born + FILE_LAG) filedDots.push({ gift: p.gift, color: p.color, born });
      }
      // night Dream clips a must-know star onto the most important memory (the name/wrench)
      const starOn = t >= 9.6;
      const drawerDots = filedDots.map((d, i) => ({ ...d, star: starOn && i === 0 }));

      // ===== night: slump asleep, but KEEP everything (head glows) =====
      let headGlow = 0;
      if (t >= WALK_END) {
        s.walk = 0;
        s.asleep = easeInOut(seg(t, 7.7, 9.2));
        s.eye = 1 - s.asleep;
        headGlow = seg(t, 8.4, 9.2) * (1 - seg(t, 10.6, 11.4));   // head lit while it dreams
        // name/accessories/tint intentionally UNCHANGED — nothing drains.
      }

      // ===== day 2: wake, still whole, confident =====
      if (t >= 11.0) {
        s.asleep = 1 - easeInOut(seg(t, 11.2, 12.4));
        s.eye = easeOut(seg(t, 11.6, 12.4));
        // KEEP name/items/tint (full self): re-assert them (metCount is 3 here anyway)
        s.accessories = ['hat', 'scarf', 'badge'];
        s.tint = 0.9; s.name = NAME; s.nameA = clamp01(seg(t, 11.8, 12.6));
        if (t > 12.8) { s.emote = 'proud'; s.look = 0.15; }
      }

      // ===== draw people (day 1 only) =====
      if (t < 7.6) {
        for (let i = 0; i < PEOPLE.length; i++) {
          const p = PEOPLE[i];
          const px = toScreen(p.wx, scroll);
          if (px < -160 || px > W + 200) continue;
          const talking = st.atPerson === i;
          human(ctx, px, gy, { color: p.color, scale: 1, wave: talking ? 1 : 0, t, face: -1 });
          if (talking && night < 0.3) {
            const stopSeg = SEGS.find(ss => ss.kind === 'stop' && ss.person === i);
            const a = seg(t, stopSeg.t0 + 0.1, stopSeg.t0 + 0.45);
            speechBubble(ctx, px, gy - 258, p.say, a * (1 - night), { dx: 40 });
          }
        }
      }

      // ===== Rosie =====
      robot(ctx, s);
      const headX = BOT_SCREEN_X;
      const headY = gy - 170 - 36 - 18;

      // soft head glow at night — Dreaming keeps the lights on while the body sleeps
      if (headGlow > 0.01) {
        ctx.save();
        ctx.globalAlpha = headGlow * 0.7;
        glowCircleSafe(ctx, headX, headY - 6, 78, C.teal, 40);
        ctx.restore();
      }

      // ===== the persistent memory drawer (rides under Rosie) =====
      const drawerBuild = clamp01(seg(t, 1.0, 1.6));    // appears once she files her first dot
      const drawerY = gy + 108;                          // below the name tag (clear of it)
      const drawerGlow = Math.max(headGlow, t >= 11.0 ? seg(t, 12.6, 13.2) : 0);
      const slotCenters = memoryDrawer(ctx, headX, drawerY, drawerDots, { build: drawerBuild, glow: drawerGlow });

      // ===== memory dots: received from a human, then FILED into the drawer =====
      if (t < 11.0) {
        dotsHeld.forEach((d, k) => {
          const stopSeg = SEGS.find(ss => ss.kind === 'stop' && ss.person === d.i);
          const born = stopSeg.t0 + (stopSeg.t1 - stopSeg.t0) * GIFT_AT;
          const slot = slotCenters[k] || { x: headX, y: drawerY + 30 };
          // orbit anchor above the head before filing
          const n = Math.max(dotsHeld.length, 1);
          const fx = n === 1 ? 0 : (k / (n - 1) - 0.5);
          const orbitX = headX + fx * 230;
          const orbitY = headY - 250 + Math.abs(fx) * 40 - Math.sin(t * 1.5 + k) * 6;

          if (t < born + 0.5) {
            // (1) fly from the human up to the orbit point
            const px = toScreen(PEOPLE[d.i].wx, scroll);
            const a = easeOut(seg(t, born, born + 0.55));
            const x = lerp(px, orbitX, a), y = lerp(gy - 230, orbitY, a);
            const r = 30 * Math.min(easeOutBack(seg(t, born, born + 0.55)), 1);
            memDot(ctx, x, y, r, d.color, { glyph: d.gift, glow: 20 });
          } else if (t < born + FILE_LAG + 0.45) {
            // (2) Daydream shimmer sweeps, then the dot FILES down into the drawer slot
            const fk = easeInOut(seg(t, born + FILE_LAG - 0.05, born + FILE_LAG + 0.45));
            const x = lerp(orbitX, slot.x, fk), y = lerp(orbitY, slot.y, fk);
            const r = lerp(30, 21, fk);
            // shimmer wipe across the dot as it's written
            daydreamShimmer(ctx, x, y, 36, seg(t, born + FILE_LAG - 0.15, born + FILE_LAG + 0.2));
            memDot(ctx, x, y, r, d.color, { glyph: d.gift, glow: 20 });
          }
          // after that, the dot simply LIVES in the drawer (drawn by memoryDrawer).
        });
      }

      // ===== "Daydream -> write" caption while filing (day 1) =====
      if (t > 1.2 && t < 7.4) {
        const a = (0.5 + 0.5 * Math.sin(t * 1.5)) * 0.9;
        smallCaption(ctx, headX, drawerY + 96, 'Daydream → write', C.teal, a * clamp01(seg(t, 1.2, 1.6)));
      }

      // ===== night Dream: dedup merge + tag, with caption =====
      if (t > 8.6 && t < 11.2) {
        // a faint sweep line crossing the drawer = the Dream pass organizing
        const sweep = (t * 0.5) % 1;
        ctx.save();
        ctx.globalAlpha = 0.3 * (1 - seg(t, 10.6, 11.2));
        const sx = lerp(headX - 150, headX + 150, sweep);
        const g = ctx.createLinearGradient(sx - 30, 0, sx + 30, 0);
        g.addColorStop(0, 'rgba(143,227,255,0)'); g.addColorStop(0.5, 'rgba(143,227,255,0.7)'); g.addColorStop(1, 'rgba(143,227,255,0)');
        ctx.fillStyle = g; ctx.fillRect(sx - 30, drawerY - 14, 60, 90);
        ctx.restore();
        // star pop when the must-know tag clips on
        if (t > 9.6 && t < 10.2 && slotCenters[0]) {
          const a = seg(t, 9.6, 9.85) * (1 - seg(t, 10.0, 10.2));
          drawSparkle(ctx, slotCenters[0].x + 17, slotCenters[0].y - 17, 18, C.amber, a);
        }
        smallCaption(ctx, headX, drawerY + 96, 'Dream → organize', '#8fe3ff', clamp01(seg(t, 8.6, 9.0)) * (1 - seg(t, 10.6, 11.2)));
      }

      // ===== Zzz while asleep (head still glowing) =====
      if (t > 8.2 && t < 11.4) {
        const za = seg(t, 8.2, 8.9) * (1 - seg(t, 10.8, 11.4));
        zzz(ctx, headX + 110, headY - 60, t, za);
      }

      // ===== sparkle when newly named (rhymes with Act 1) =====
      const stop0 = SEGS.find(ss => ss.kind === 'stop' && ss.person === 0);
      const namedAt = stop0.t0 + (stop0.t1 - stop0.t0) * GIFT_AT;
      if (t > namedAt && t < namedAt + 0.9) {
        const a = seg(t, namedAt, namedAt + 0.3) * (1 - seg(t, namedAt + 0.5, namedAt + 0.9));
        for (const [dx, dy, sc2] of [[0, -330, 1], [-60, -300, 0.6], [64, -310, 0.7]])
          drawSparkle(ctx, headX + dx, headY + dy, 26 * sc2, C.amber, a);
      }

      // ===== day 2 RECALL: a dot zips from the drawer to Rosie's hand =====
      if (t > 12.6 && t < 14.2) {
        const rk = easeInOut(seg(t, 12.6, 13.4));
        const from = slotCenters[0] || { x: headX, y: drawerY + 30 };
        const hand = s._hand || { x: headX + 90, y: headY + 120 };
        const x = lerp(from.x, hand.x, rk), y = lerp(from.y, hand.y, rk);
        // recall trail
        ctx.save(); ctx.globalAlpha = 0.4 * (1 - rk);
        ctx.strokeStyle = C.teal; ctx.lineWidth = 4; ctx.setLineDash([4, 8]); ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(x, y); ctx.stroke(); ctx.restore();
        memDot(ctx, x, y, 22, PEOPLE[0].color, { glyph: PEOPLE[0].gift, glow: 24 });
      }

      // ===== day 2 confident "check" (the rhyme with Act 1's "?") =====
      if (t > 13.4 && t < 16.2) {
        const a = seg(t, 13.5, 13.85);
        const pop = easeOutBack(seg(t, 13.5, 13.95));
        ctx.save();
        ctx.globalAlpha = Math.min(1, a) * (1 - seg(t, 15.6, 16.2));
        ctx.translate(headX, headY - 70 - easeOut(seg(t, 13.5, 14.6)) * 12);
        ctx.scale(pop, pop);
        glowDotRaw(ctx, 0, 0, 40, C.green, 26);
        drawGlyph(ctx, 0, 0, 30, 'check', '#ffffff');
        ctx.restore();
      }

      // ===== DAY labels (mirror Act 1) =====
      if (t < 7.6) dayLabel(ctx, 'DAY 1', night);
      if (t > 11.4 && t < 16.2) dayLabel(ctx, 'DAY 2', night);

      // ===== title card "Now it remembers." (the payoff) =====
      if (t > 13.8 && t < 16.4) {
        ctx.save(); ctx.globalAlpha = (seg(t, 13.8, 14.3) * (1 - seg(t, 15.9, 16.4))) * 0.5;
        ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H); ctx.restore();
        titleCard(ctx, 'Now it remembers.', t, 14.0, 2.0, { size: 80, color: '#fff', y: H * 0.30 });
      }

      // ===== end card / loop seam =====
      if (t >= 16.0) {
        const a = seg(t, 16.0, 16.6);
        ctx.save();
        ctx.globalAlpha = a;
        ctx.fillStyle = '#0b1424'; ctx.fillRect(0, 0, W, H);
        // diamond logo
        const cx = W / 2, cyl = H * 0.42;
        ctx.translate(cx, cyl);
        const dp = easeOutBack(seg(t, 16.2, 16.8));
        ctx.save(); ctx.scale(dp, dp);
        ctx.fillStyle = C.accent; ctx.shadowColor = C.accent; ctx.shadowBlur = 30;
        ctx.beginPath();
        ctx.moveTo(0, -54); ctx.lineTo(46, 0); ctx.lineTo(0, 54); ctx.lineTo(-46, 0); ctx.closePath(); ctx.fill();
        ctx.shadowBlur = 0; ctx.fillStyle = '#0b1424';
        ctx.beginPath(); ctx.moveTo(0, -22); ctx.lineTo(19, 0); ctx.lineTo(0, 22); ctx.lineTo(-19, 0); ctx.closePath(); ctx.fill();
        ctx.restore();
        ctx.restore();
        // wordmark + tagline
        const a2 = seg(t, 16.5, 17.0);
        ctx.save(); ctx.globalAlpha = a2;
        ctx.fillStyle = '#fff'; ctx.font = FONT(56); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('Cookbook Memory', W / 2, H * 0.42 + 120);
        ctx.fillStyle = 'rgba(205,215,226,0.92)'; ctx.font = FONTR(30);
        ctx.fillText('Persistent, self-curating memory for coding agents.', W / 2, H * 0.42 + 172);
        ctx.restore();
      }

      if (t < 16.0) vignette(ctx);
    },
  };

  // ---- act3-local helpers ---------------------------------------------------
  // a guarded glow circle (lib.glowCircle exists but keep a local fallback for safety)
  function glowCircleSafe(ctx, x, y, r, color, blur) {
    ctx.save(); ctx.shadowColor = color; ctx.shadowBlur = blur; ctx.fillStyle = color;
    ctx.globalAlpha *= 0.5;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  }
  // a brief light wipe across a filing dot — the Daydream "write" shimmer.
  function daydreamShimmer(ctx, x, y, r, k) {
    if (k <= 0 || k >= 1) return;
    ctx.save();
    ctx.globalAlpha = Math.sin(k * Math.PI) * 0.8;
    const wx = lerp(x - r, x + r, k);
    const g = ctx.createLinearGradient(wx - 14, 0, wx + 14, 0);
    g.addColorStop(0, 'rgba(143,227,255,0)'); g.addColorStop(0.5, 'rgba(170,240,255,0.9)'); g.addColorStop(1, 'rgba(143,227,255,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
  // small pill caption under the drawer
  function smallCaption(ctx, x, y, text, color, alpha) {
    if (alpha <= 0.01) return;
    ctx.save();
    ctx.globalAlpha = Math.min(1, alpha);
    ctx.font = FONTR(22);
    const w = ctx.measureText(text).width + 28;
    roundRect(ctx, x - w / 2, y - 17, w, 34, 17, 'rgba(11,20,36,0.82)');
    ctx.fillStyle = color; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y + 1);
    ctx.restore();
  }
})();
