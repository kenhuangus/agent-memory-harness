// ACT 1 — "The Problem": a side-scrolling journey, then catastrophic forgetting. 20.0s.
// Storyboard (presentation/STORYBOARD.md, 60s cut — Act 1 reworked as a journey):
//   DAY 1 (0.0 - 10.5): robot walks right, STOPPING at each of 3 tall South-Park-style
//     humans. At each stop the human's SPEECH BUBBLE appears first (tells the robot
//     about the lesson/tool), THEN the robot receives the memory dot and a personality
//     item, then walks on. The FIRST human names it "Rosie".
//   NIGHT (10.5 - 15.0): robot stops, slumps asleep; name, accessories, tint and every
//     memory dot drain away. It wakes blank.
//   DAY 2 (15.0 - 20.0): a fresh blank robot, no name, no items, back at the start.
//     title card "Every day starts from zero."
//
// Pure function of time: draw(ctx, t). Deterministic (seeded RNG only).
'use strict';
(() => {
  const L = window.LIB;
  const { W, H, C, seg, easeOut, easeIn, easeInOut, easeOutBack, lerp, clamp01,
          backdrop, robot, human, speechBubble, hills, signpost,
          memDot, dayLabel, titleCard, zzz, drawSparkle, vignette } = L;

  const BOT_SCREEN_X = W * 0.40;                      // robot stays here; world moves
  const NAME = 'Rosie';

  // Each human stands at a world-x. The robot stops a bit to its LEFT (meetX), so the
  // human is just to the robot's right when they talk.
  //   say:  the speech-bubble line shown BEFORE the gift (silent: short caption)
  //   gift: the memory-dot glyph received AFTER the bubble
  //   gain: the personality accessory gained at this stop
  //   name: if set, this human names the robot
  const PEOPLE = [
    { wx: 980,  color: C.violet, say: "You're Rosie!",       gift: 'wrench', gain: 'hat',   name: NAME },
    { wx: 1680, color: C.teal,   say: 'Run tests first',     gift: 'bulb',   gain: 'scarf' },
    { wx: 2380, color: C.orange, say: 'Small commits',       gift: 'key',    gain: 'badge' },
  ];

  // ---- stop-and-go timeline -------------------------------------------------
  // The robot walks to each station, pauses to talk+receive, then walks on.
  // Build a piecewise scroll(t): WALK segments interpolate world-x; STOP segments hold.
  const MEET_GAP = 180;                               // robot stops this far left of human
  const stationX = PEOPLE.map(p => p.wx - MEET_GAP);  // world-scroll value at each stop
  const WALK = [1.5, 1.6, 1.6, 2.0];                  // durations of the 4 walk legs (last = long exit)
  const STOP = 1.35;                                  // pause duration at each station
  // segment table: {t0, t1, from, to, kind}
  const SEGS = [];
  let tc = 0.5, sc = 0;                               // start a beat in, scroll 0
  for (let i = 0; i < PEOPLE.length; i++) {
    SEGS.push({ t0: tc, t1: tc + WALK[i], from: sc, to: stationX[i], kind: 'walk' });
    tc += WALK[i]; sc = stationX[i];
    SEGS.push({ t0: tc, t1: tc + STOP, from: sc, to: sc, kind: 'stop', person: i });
    tc += STOP;
  }
  // final long walk off toward dusk — scroll far enough that the LAST human clears the
  // left edge of the screen before nightfall. Person #3 (wx=2380) leaves the screen once
  // its screen-x (BOT_SCREEN_X + 2380 - scroll) < -180, i.e. scroll > 3328.
  const EXIT_SCROLL = PEOPLE[PEOPLE.length - 1].wx + BOT_SCREEN_X + 220;   // ~3380
  SEGS.push({ t0: tc, t1: tc + WALK[3], from: sc, to: EXIT_SCROLL, kind: 'walk' });
  tc += WALK[3];
  const WALK_END = tc;                                // robot keeps walking until dusk

  function sceneState(t) {
    // returns {scroll, walking, atPerson|null, stopProgress}
    if (t >= WALK_END) return { scroll: SEGS[SEGS.length - 1].to, walking: false, atPerson: null, stopProgress: 1 };
    for (const s of SEGS) {
      if (t >= s.t0 && t < s.t1) {
        if (s.kind === 'walk') {
          const k = easeInOut(seg(t, s.t0, s.t1));
          return { scroll: lerp(s.from, s.to, k), walking: true, atPerson: null, stopProgress: 0 };
        } else {
          return { scroll: s.from, walking: false, atPerson: s.person, stopProgress: seg(t, s.t0, s.t1) };
        }
      }
    }
    return { scroll: 0, walking: t > 0.5, atPerson: null, stopProgress: 0 };
  }

  // world-x -> screen-x given current scroll
  const toScreen = (wx, scroll) => BOT_SCREEN_X + (wx - scroll);

  window.SCENES.act1 = {
    duration: 23.0,                                     // +3s tail so the closing card can hang
    bg: '#bfe3ff',
    draw(ctx, t) {
      // ----- day/night -----
      // Night holds off until the robot has walked the last human off-screen (~11.3s).
      let night = 0;
      if (t < 11.4) night = 0;
      else if (t < 13.4) night = easeInOut(seg(t, 11.4, 13.4));
      else if (t < 15) night = 1;
      else if (t < 16.5) night = 1 - easeInOut(seg(t, 15, 16.5));
      else night = 0;

      const gy = backdrop(ctx, night);
      const st = sceneState(t);
      const scroll = t < 15 ? st.scroll : 0;            // day 2 resets to start of world

      // ===== parallax world =====
      hills(ctx, scroll, gy, night, { color: '#9ec2e0', amp: 90, span: 680, y: -40, speed: 0.12 });
      hills(ctx, scroll, gy, night, { color: '#a8c98f', amp: 70, span: 520, y: 6,   speed: 0.30 });
      for (const swx of [520, 1340, 2040, 2680]) {
        const sx = toScreen(swx, scroll);
        if (sx > -120 && sx < W + 120) signpost(ctx, sx, gy, night);
      }

      // which people has the robot already FINISHED meeting (received gift)?
      // a gift is received partway through the stop (after the bubble reads).
      const GIFT_AT = 0.55;                              // fraction of the stop when dot arrives
      const metCount = (() => {
        let n = 0;
        for (let i = 0; i < PEOPLE.length; i++) {
          // count as met once we've walked past that station's stop with gift received
          const stopSeg = SEGS.find(s => s.kind === 'stop' && s.person === i);
          if (t >= stopSeg.t0 + (stopSeg.t1 - stopSeg.t0) * GIFT_AT) n = i + 1;
        }
        return n;
      })();

      // ===== robot state =====
      const s = {
        x: BOT_SCREEN_X, baseY: gy, t,
        walk: (st.walking && t < WALK_END + 0.05) ? 1 : 0,
        face: 1, eye: 1, look: 0, emote: 'none',
        asleep: 0, accessories: [], tint: 0, name: null, nameA: 0,
      };

      // accrue personality from people already met (day 1)
      const dotsHeld = [];
      if (t < 15) {
        for (let i = 0; i < metCount; i++) {
          const p = PEOPLE[i];
          if (p.gain && !s.accessories.includes(p.gain)) s.accessories.push(p.gain);
          dotsHeld.push({ i, gift: p.gift, color: p.color });
          if (p.name) { s.name = p.name; }
        }
        s.tint = metCount / PEOPLE.length * 0.9;
        // name tag fades in right after the first gift
        if (s.name) {
          const stop0 = SEGS.find(ss => ss.kind === 'stop' && ss.person === 0);
          s.nameA = clamp01(seg(t, stop0.t0 + (stop0.t1 - stop0.t0) * GIFT_AT, stop0.t1 + 0.3));
        }
        // look toward the human while stopped; happy once it has a personality
        if (!st.walking && st.atPerson !== null) s.look = 0.5;
        if (!st.walking && metCount >= 1 && st.atPerson === null) s.emote = 'happy';
      }

      // ===== night: stop, slump, drain everything =====
      if (t >= WALK_END) {
        s.walk = 0;
        s.asleep = easeInOut(seg(t, 11.6, 13.4));
        s.eye = 1 - s.asleep;
        const drain = seg(t, 12.0, 14.4);
        s.tint = (1 - drain) * 0.9;
        const keep = Math.round((1 - easeIn(drain)) * PEOPLE.length);
        s.accessories = s.accessories.slice(0, keep);
        s.name = drain < 0.85 ? NAME : null;
        s.nameA = (1 - seg(t, 12.0, 13.4));
      }

      // ===== day 2: fresh blank robot =====
      if (t >= 15) {
        s.asleep = 1 - easeInOut(seg(t, 15, 16.3));
        s.eye = easeOut(seg(t, 15.6, 16.4));
        s.accessories = []; s.tint = 0; s.name = null; s.nameA = 0;
        if (t > 16.6) { s.emote = 'confused'; s.look = -0.2; }
      }

      // ===== draw people =====
      if (t < 15) {
        for (let i = 0; i < PEOPLE.length; i++) {
          const p = PEOPLE[i];
          const px = toScreen(p.wx, scroll);
          if (px < -160 || px > W + 200) continue;
          const talking = st.atPerson === i;
          human(ctx, px, gy, { color: p.color, scale: 1, wave: talking ? 1 : 0, t, face: -1 });
          // speech bubble appears DURING the stop, BEFORE the gift arrives, and lingers.
          // It floats ABOVE the human's head and points DOWN at them (the human speaks).
          if (talking && night < 0.3) {
            const stopSeg = SEGS.find(ss => ss.kind === 'stop' && ss.person === i);
            const a = seg(t, stopSeg.t0 + 0.1, stopSeg.t0 + 0.5);
            const headTop = gy - 258;                 // top of the human's head (scale 1)
            speechBubble(ctx, px, headTop, p.say, a * (1 - night), { dx: 40 });
          }
        }
      }

      // ===== robot =====
      robot(ctx, s);
      const headX = BOT_SCREEN_X;
      const headY = gy - 170 - 36 - 18;

      // ===== memory dots orbiting the robot =====
      if (t < 15) {
        const n = Math.max(dotsHeld.length, 1);
        dotsHeld.forEach((d, k) => {
          const fx = n === 1 ? 0 : (k / (n - 1) - 0.5);
          let x = headX + fx * 300;
          let y = headY - 250 + Math.abs(fx) * 50 - Math.sin(t * 1.5 + k) * 8;
          let alpha = 1, r = 32, glow = 20;
          // birth pop: the just-received dot flies from the human into orbit
          const stopSeg = SEGS.find(ss => ss.kind === 'stop' && ss.person === d.i);
          const born = stopSeg.t0 + (stopSeg.t1 - stopSeg.t0) * GIFT_AT;
          const pop = easeOutBack(seg(t, born, born + 0.55));
          if (t < born + 0.55) {
            const px = toScreen(PEOPLE[d.i].wx, scroll);
            x = lerp(px, x, easeOut(seg(t, born, born + 0.6)));
            y = lerp(gy - 230, y, easeOut(seg(t, born, born + 0.6)));
            r = 32 * Math.min(pop, 1);
          }
          // evaporate at night
          if (t >= 11.8) {
            const evap = seg(t, 12.0 + k * 0.5, 13.4 + k * 0.5);
            if (evap > 0) {
              y -= easeIn(evap) * 230; x += Math.sin(k * 2 + t) * 18 * evap;
              alpha = 1 - evap; r *= (1 - 0.35 * evap); glow = 20 + evap * 14;
              if (evap > 0.7) memDot(ctx, x, y, r, d.color, { alpha: (1 - evap) * 0.8, ring: (evap - 0.7) * 3, glow });
            }
          }
          if (alpha > 0.02) memDot(ctx, x, y, r, d.color, { glyph: d.gift, alpha, glow });
        });
      }

      // ===== Zzz while asleep =====
      if (t > 12.0 && t < 15.4) {
        const za = seg(t, 12.0, 12.8) * (1 - seg(t, 14.8, 15.4));
        zzz(ctx, headX + 110, headY - 60, t, za);
      }

      // ===== sparkle when newly named (right after first gift) =====
      const stop0 = SEGS.find(ss => ss.kind === 'stop' && ss.person === 0);
      const namedAt = stop0.t0 + (stop0.t1 - stop0.t0) * GIFT_AT;
      if (t > namedAt && t < namedAt + 0.9) {
        const a = seg(t, namedAt, namedAt + 0.3) * (1 - seg(t, namedAt + 0.5, namedAt + 0.9));
        for (const [dx, dy, sc2] of [[0, -330, 1], [-60, -300, 0.6], [64, -310, 0.7]])
          drawSparkle(ctx, headX + dx, headY + dy, 26 * sc2, C.amber, a);
      }

      // ===== day 2 confused "?" =====
      if (t > 16.6) {
        const a = seg(t, 16.7, 17.0);
        ctx.save(); ctx.globalAlpha = Math.min(1, a);
        ctx.font = L.FONT(86); ctx.fillStyle = C.red; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.shadowColor = C.red; ctx.shadowBlur = 24;
        ctx.fillText('?', headX, headY - 70 - easeOut(seg(t, 16.7, 17.9)) * 10);
        ctx.restore();
      }

      // ===== DAY labels =====
      if (t < 11.6) dayLabel(ctx, 'DAY 1', night);
      if (t > 16.2) dayLabel(ctx, 'DAY 2', night);

      // ===== title card — "Every day starts from zero." holds long enough to read =====
      if (t > 18.3) {
        ctx.save(); ctx.globalAlpha = seg(t, 18.3, 18.8) * 0.55;
        ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H); ctx.restore();
        // appears at 18.5 and dwells ~4s (through the end of the act) before the
        // film's push-into-the-head seam takes over.
        titleCard(ctx, 'Every day starts from zero.', t, 18.5, 4.2, { size: 78, color: '#fff' });
      }

      vignette(ctx);
    },
  };
})();
