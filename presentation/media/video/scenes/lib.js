// Shared rendering library for the Cookbook Memory video.
// Every scene is a pure function of time: draw(ctx, t) with t in seconds.
// Deterministic — no Date.now(), no unseeded Math.random(). This keeps the scene
// (a) live-playable in the deck via RAF, and (b) frame-recordable to MP4.
//
// Modeled on the Corellia explainer pipeline (media/video/scenes/lib.js).

'use strict';

const W = 1920, H = 1080;

// Palette pulled from the deck (presentation/assets/style.css).
const C = {
  // day / night sky
  daySky:   '#bfe3ff',
  daySky2:  '#e9f6ff',
  nightSky: '#10243e',
  nightSky2:'#1b2f4e',
  ground:   '#d9c7a6',
  groundN:  '#3a3a52',
  // robot
  bot:      '#cdd7e2',
  botDark:  '#9fb0c2',
  botLine:  '#5b6b7d',
  visor:    '#10243e',
  eye:      '#8fe3ff',
  // deck accents
  blue:     '#1971c2',
  violet:   '#9c36b5',
  green:    '#2f9e44',
  orange:   '#f08c00',
  amber:    '#ffb454',
  teal:     '#0c8599',
  grape:    '#7048e8',
  accent:   '#ff6a2b',
  red:      '#e03131',
  ink:      '#10243e',
  inkN:     '#e8eef6',
  dim:      '#6b7785',
};

const FONT  = px => `600 ${px}px "Space Grotesk", "Inter", system-ui, sans-serif`;
const FONTR = px => `500 ${px}px "Inter", system-ui, sans-serif`;

// ---- easing ---------------------------------------------------------------
const clamp01 = x => Math.max(0, Math.min(1, x));
const seg = (t, a, b) => clamp01((t - a) / (b - a));         // progress through [a,b]
const easeOut   = x => 1 - Math.pow(1 - x, 3);
const easeIn    = x => x * x * x;
const easeInOut = x => x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const easeOutBack = x => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
const lerp = (a, b, x) => a + (b - a) * x;

// seeded RNG (mulberry32) — deterministic across runs
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

// ---- backdrop: day/night sky + ground -------------------------------------
// night: 0 = full day, 1 = full night. Drives sky gradient, sun/moon, ground.
function backdrop(ctx, night) {
  const sky = ctx.createLinearGradient(0, 0, 0, H);
  sky.addColorStop(0, mix(C.daySky,  C.nightSky,  night));
  sky.addColorStop(1, mix(C.daySky2, C.nightSky2, night));
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, W, H);

  // stars fade in at night
  if (night > 0.25) {
    const r = rng(7);
    ctx.fillStyle = `rgba(255,255,255,${seg(night, 0.35, 0.9) * 0.9})`;
    for (let i = 0; i < 70; i++) {
      const x = r() * W, y = r() * H * 0.62, s = 0.5 + r() * 1.8;
      ctx.beginPath(); ctx.arc(x, y, s, 0, Math.PI * 2); ctx.fill();
    }
  }

  // sun (day, top-right) sinks as moon (night) rises on an arc.
  const arc = night;                       // 0 day -> 1 night
  const cx = lerp(W * 0.80, W * 0.20, arc);
  const cy = lerp(H * 0.16, H * 0.16, 0) + Math.sin(arc * Math.PI) * 90;
  // sun
  ctx.globalAlpha = 1 - clamp01(arc * 1.4);
  if (ctx.globalAlpha > 0.01) {
    ctx.fillStyle = '#ffd23f';
    glowCircle(ctx, cx, cy, 70, '#ffd23f', 40);
  }
  ctx.globalAlpha = 1;
  // moon
  const ma = clamp01((arc - 0.45) * 2.2);
  if (ma > 0.01) {
    ctx.globalAlpha = ma;
    const mx = lerp(W * 0.80, W * 0.78, 1), my = H * 0.16;
    glowCircle(ctx, mx, my, 58, '#f4f1ea', 28);
    // crescent shadow
    ctx.fillStyle = mix(C.nightSky, C.nightSky2, 0.5);
    ctx.beginPath(); ctx.arc(mx + 22, my - 8, 54, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha = 1;
  }

  // ground
  const gy = H * 0.74;
  ctx.fillStyle = mix(C.ground, C.groundN, night);
  ctx.fillRect(0, gy, W, H - gy);
  // desk/workbench line
  ctx.strokeStyle = `rgba(0,0,0,${0.12})`;
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke();
  return gy;
}

function glowCircle(ctx, x, y, r, color, blur) {
  ctx.save();
  ctx.shadowColor = color; ctx.shadowBlur = blur;
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

// hex color mix
function mix(a, b, x) {
  x = clamp01(x);
  const pa = hx(a), pb = hx(b);
  const r = Math.round(lerp(pa[0], pb[0], x));
  const g = Math.round(lerp(pa[1], pb[1], x));
  const bl = Math.round(lerp(pa[2], pb[2], x));
  return `rgb(${r},${g},${bl})`;
}
function hx(h) { h = h.replace('#', ''); return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)]; }

// ---- the robot rig --------------------------------------------------------
// A friendly rounded robot at (x, baselineY). Reused by Act 1 & Act 3 so the
// character is identical across the before/after.
// state: {
//   x, baseY,            // feet position
//   bob,                 // idle bob amount (0..1)
//   asleep,              // 0 awake .. 1 fully slumped
//   eye,                 // 0 closed .. 1 open
//   armReach,            // 0 rest .. 1 reaching toward desk
//   look,                // -1 left .. 1 right (head tilt)
//   emote,               // 'none' | 'happy' | 'confused' | 'proud'
//   t,                   // time, for bob/blink phase
// }
// state additions for the side-scroll journey:
//   walk:    0 stand .. 1 full walk cycle amplitude (drives leg/arm swing + body bounce)
//   face:    1 facing right (default) .. -1 facing left
//   accessories: array of 'hat' | 'scarf' | 'badge'  (personality items that accrue)
//   tint:    0 default body .. 1 fully personality-tinted (warmer body color)
//   name:    string | null  — floats a name tag under the feet
//   nameA:   0..1 name-tag fade-in alpha
function robot(ctx, s) {
  const t = s.t || 0;
  const asleep = s.asleep || 0;
  const walk = (s.walk || 0) * (1 - asleep);
  const face = s.face || 1;
  // walk cycle phase: feet/arms swing out of phase; body bounces twice per stride
  const ph = t * 7.2;                                  // stride speed
  const stride = Math.sin(ph) * 26 * walk;             // leg swing (px)
  const bounce = Math.abs(Math.cos(ph)) * 10 * walk;   // up-down body bounce
  const bob = (s.bob ?? 1) * Math.sin(t * 2.0) * 6 * (1 - asleep) * (1 - walk) - bounce;
  const slump = asleep * 46;
  const cx = s.x;
  const feetY = s.baseY;
  const bodyW = 150, bodyH = 170;
  const bodyY = feetY - bodyH - 36 + bob + slump;     // top of body
  const headR = 66;
  const headY = bodyY - 18 + slump * 0.4;             // head center-ish
  const tilt = (s.look || 0) * 8 + face * 4 * walk;   // lean into walk direction
  // personality-tinted body color
  const bodyCol = s.tint ? mix(C.bot, '#ffe1c2', clamp01(s.tint)) : C.bot;
  const acc = s.accessories || [];

  ctx.save();
  // soft shadow on ground (squashes slightly with each step)
  ctx.fillStyle = 'rgba(0,0,0,0.16)';
  ctx.beginPath();
  ctx.ellipse(cx, feetY + 6, (92 - asleep * 10) - bounce * 0.6, 18, 0, 0, Math.PI * 2);
  ctx.fill();

  // legs — swing fore/aft when walking
  ctx.strokeStyle = C.botLine; ctx.lineWidth = 16; ctx.lineCap = 'round';
  const legL = -38 + stride * 0.6, legR = 38 - stride * 0.6;
  const liftL = Math.max(0, Math.sin(ph)) * 10 * walk, liftR = Math.max(0, -Math.sin(ph)) * 10 * walk;
  ctx.beginPath();
  ctx.moveTo(cx - 38, bodyY + bodyH - 8); ctx.lineTo(cx + legL, feetY - 6 - liftL);
  ctx.moveTo(cx + 38, bodyY + bodyH - 8); ctx.lineTo(cx + legR, feetY - 6 - liftR);
  ctx.stroke();
  // feet
  roundRect(ctx, cx + legL - 26, feetY - 12 - liftL, 52, 18, 8, C.botDark);
  roundRect(ctx, cx + legR - 26, feetY - 12 - liftR, 52, 18, 8, C.botDark);

  // arms — swing opposite to legs when walking; can also reach
  const reach = (s.armReach || 0) * (1 - asleep);
  ctx.strokeStyle = C.botDark; ctx.lineWidth = 18; ctx.lineCap = 'round';
  const armSwing = Math.sin(ph) * 30 * walk;
  // back arm (swings with -armSwing)
  const backHandX = cx - 92 - armSwing, backHandY = bodyY + 116 - reach * 30 + slump - Math.abs(armSwing) * 0.2;
  ctx.beginPath();
  ctx.moveTo(cx - 66, bodyY + 60);
  ctx.lineTo(backHandX, backHandY);
  ctx.stroke();
  glowDotRaw(ctx, backHandX, backHandY, 13, bodyCol, 0);
  // front arm (reaching, or swings with +armSwing)
  const handX = lerp(cx + 72 + armSwing, cx + 150, reach);
  const handY = lerp(bodyY + 116 - Math.abs(armSwing) * 0.2, bodyY + 96, reach) + slump * (1 - reach);
  ctx.beginPath();
  ctx.moveTo(cx + 66, bodyY + 60);
  ctx.lineTo(handX, handY);
  ctx.stroke();
  glowDotRaw(ctx, handX, handY, 13, bodyCol, 0);
  s._hand = { x: handX, y: handY };

  // body
  roundRect(ctx, cx - bodyW / 2, bodyY, bodyW, bodyH, 30, bodyCol);
  ctx.strokeStyle = 'rgba(91,107,125,0.5)'; ctx.lineWidth = 3;
  roundRectStroke(ctx, cx - bodyW / 2 + 16, bodyY + 22, bodyW - 32, bodyH - 44, 18);
  const chest = (1 - asleep) * (0.5 + 0.5 * Math.sin(t * 3));
  glowDotRaw(ctx, cx, bodyY + bodyH / 2 + 4, 10, mix(C.dim, C.teal, chest), 14 * chest);
  // badge accessory — a little star pinned to the chest
  if (acc.includes('badge')) drawGlyph(ctx, cx + 42, bodyY + 46, 16, 'star', C.amber);

  // scarf accessory — drawn over the neck, trailing behind the walk direction
  // neck
  ctx.fillStyle = C.botDark;
  roundRect(ctx, cx - 14, headY + headR - 10, 28, 22, 6, C.botDark);
  if (acc.includes('scarf')) {
    const ny = bodyY + 6;                           // scarf wraps just below the head
    ctx.fillStyle = C.red;
    // wide band across the shoulders (the head, drawn after, tucks over its top)
    roundRect(ctx, cx - bodyW / 2 + 8, ny, bodyW - 16, 26, 12, C.red);
    // a clear hanging tail off the back side, flapping with the stride
    const flap = Math.sin(ph) * 12 * walk;
    const tx = cx - (bodyW / 2 - 18) * face;        // hangs from the back shoulder
    ctx.beginPath();
    ctx.moveTo(tx - 16, ny + 18);
    ctx.quadraticCurveTo(tx - 24 + flap, ny + 54, tx - 10 + flap, ny + 84);
    ctx.lineTo(tx + 16 + flap, ny + 80);
    ctx.quadraticCurveTo(tx + 6, ny + 50, tx + 14, ny + 20);
    ctx.closePath(); ctx.fill();
  }

  // head
  roundRect(ctx, cx - headR, headY - headR + 4, headR * 2, headR * 2 - 6, 28, bodyCol);
  // antenna
  ctx.strokeStyle = C.botLine; ctx.lineWidth = 5;
  ctx.beginPath(); ctx.moveTo(cx, headY - headR + 4); ctx.lineTo(cx, headY - headR - 26); ctx.stroke();
  glowDotRaw(ctx, cx, headY - headR - 30, 8, asleep > 0.5 ? C.dim : C.amber, asleep > 0.5 ? 0 : 12);

  // hat accessory — a little cap over the top of the head
  if (acc.includes('hat')) {
    ctx.fillStyle = C.blue;
    roundRect(ctx, cx - headR - 6, headY - headR - 4, headR * 2 + 12, 20, 8, C.blue);   // brim
    roundRect(ctx, cx - 34, headY - headR - 30, 68, 32, 12, C.blue);                     // crown
    glowDotRaw(ctx, cx, headY - headR - 14, 5, C.amber, 0);                              // button
  }

  // visor + eyes
  roundRect(ctx, cx - 46 + tilt, headY - 22, 92, 50, 16, C.visor);
  const eyeOpen = (s.eye ?? 1) * (1 - asleep);
  drawEyes(ctx, cx + tilt, headY + 2, eyeOpen, s.emote || 'none', t);

  ctx.restore();

  // name tag floats just under the feet
  if (s.name && (s.nameA ?? 1) > 0.01) nameTag(ctx, cx, feetY + 40, s.name, s.nameA ?? 1);
}

// a rounded name plate with the robot's earned name
function nameTag(ctx, x, y, name, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = FONT(34);
  const w = ctx.measureText(name).width + 44, h = 50;
  ctx.fillStyle = 'rgba(255,255,255,0.92)';
  roundRect(ctx, x - w / 2, y - h / 2, w, h, 16, 'rgba(255,255,255,0.92)');
  ctx.strokeStyle = C.accent; ctx.lineWidth = 3;
  roundRectStroke(ctx, x - w / 2, y - h / 2, w, h, 16);
  ctx.fillStyle = C.ink; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(name, x, y + 1);
  ctx.restore();
}

function drawEyes(ctx, cx, cy, open, emote, t) {
  const ex = 20, eyR = 10;
  ctx.save();
  ctx.fillStyle = C.eye;
  ctx.shadowColor = C.eye; ctx.shadowBlur = 12;
  if (open < 0.08) {
    // closed/sleeping: flat lines
    ctx.shadowBlur = 0; ctx.strokeStyle = C.dim; ctx.lineWidth = 5; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(cx - ex - 10, cy + 2); ctx.lineTo(cx - ex + 10, cy + 2);
    ctx.moveTo(cx + ex - 10, cy + 2); ctx.lineTo(cx + ex + 10, cy + 2); ctx.stroke();
    ctx.restore(); return;
  }
  const h = eyR * open;
  if (emote === 'happy' || emote === 'proud') {
    // ^ ^ happy arcs
    ctx.strokeStyle = C.eye; ctx.lineWidth = 6; ctx.lineCap = 'round'; ctx.shadowBlur = 10;
    for (const sx of [-ex, ex]) {
      ctx.beginPath(); ctx.arc(cx + sx, cy + 4, 11, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
    }
  } else {
    for (const sx of [-ex, ex]) {
      ctx.beginPath(); ctx.ellipse(cx + sx, cy, eyR, h, 0, 0, Math.PI * 2); ctx.fill();
    }
    if (emote === 'confused') {
      // one brow raised
      ctx.shadowBlur = 0; ctx.strokeStyle = C.amber; ctx.lineWidth = 5; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(cx + ex - 12, cy - 20); ctx.lineTo(cx + ex + 12, cy - 26); ctx.stroke();
    }
  }
  ctx.restore();
}

// ---- humans (South Park-style construction-paper cutouts) -----------------
// Deliberately TALLER than the robot, with a big round head, an upright torso
// (rounded-rectangle, NOT a fat oval — so the limbs read as separate limbs),
// distinct stubby cutout legs, two arms, and a flat little face.
// color = clothing/body color; wave animates the near arm; t for wave wobble.
function human(ctx, x, feetY, { color = C.violet, scale = 1.18, wave = 0, t = 0, face = -1, skin = '#f0c9a0' } = {}) {
  ctx.save();
  const S = scale;
  const headR = 60 * S;                        // big South-Park head
  const bodyW = 84 * S, bodyH = 150 * S;       // narrower upright torso (limbs read clearly)
  const bodyX = x - bodyW / 2;
  const bodyY = feetY - bodyH;                 // top of torso
  const headCY = bodyY - headR + 12 * S;       // head overlaps torso top slightly
  const legCol = mix(color, '#000', 0.5);
  const armW = 22 * S, armL = 78 * S;

  // shadow
  ctx.fillStyle = 'rgba(0,0,0,0.14)';
  ctx.beginPath(); ctx.ellipse(x, feetY + 4, 50 * S, 11 * S, 0, 0, Math.PI * 2); ctx.fill();

  // distinct legs (two clear cutout limbs with a gap between them)
  const legW = 26 * S, legH = 52 * S, legGap = 8 * S;
  roundRect(ctx, x - legGap - legW, feetY - legH, legW, legH, 9 * S, legCol);
  roundRect(ctx, x + legGap,        feetY - legH, legW, legH, 9 * S, legCol);
  // shoes
  roundRect(ctx, x - legGap - legW - 6 * S, feetY - 12 * S, legW + 10 * S, 14 * S, 6 * S, '#34303a');
  roundRect(ctx, x + legGap - 4 * S,        feetY - 12 * S, legW + 10 * S, 14 * S, 6 * S, '#34303a');

  // FAR arm first (behind torso, on the side away from the robot) — rests down
  ctx.save();
  ctx.translate(x - (bodyW / 2 - 2 * S) * face, bodyY + 30 * S);
  ctx.rotate(face * 0.22);
  roundRect(ctx, -armW / 2, 0, armW, armL, armW / 2, mix(color, '#000', 0.18));
  glowDotRaw(ctx, 0, armL, armW * 0.55, skin, 0);
  ctx.restore();

  // torso (upright rounded rectangle — clearly a body, not a blob)
  roundRect(ctx, bodyX, bodyY, bodyW, bodyH, 26 * S, color);
  // a subtle center seam so it reads as clothing
  ctx.strokeStyle = mix(color, '#000', 0.18); ctx.lineWidth = 3 * S;
  ctx.beginPath(); ctx.moveTo(x, bodyY + 24 * S); ctx.lineTo(x, bodyY + bodyH - 18 * S); ctx.stroke();

  // head (big round, skin)
  ctx.fillStyle = skin;
  ctx.beginPath(); ctx.arc(x, headCY, headR, 0, Math.PI * 2); ctx.fill();
  // simple hair cap (flat construction-paper arc) — color-coded per person
  ctx.fillStyle = mix(color, '#000', 0.35);
  ctx.beginPath();
  ctx.arc(x, headCY, headR, Math.PI * 1.05, Math.PI * 1.95, false);
  ctx.lineTo(x + Math.cos(Math.PI * 1.95) * headR, headCY - headR * 0.1);
  ctx.arc(x, headCY - headR * 0.12, headR * 0.98, Math.PI * 1.95, Math.PI * 1.05, true);
  ctx.closePath(); ctx.fill();
  // face: two dot eyes + a little smile, facing the robot (toward `face`)
  const ex = 18 * S, eo = face * 6 * S;
  ctx.fillStyle = '#2b2620';
  for (const sx of [-ex, ex]) { ctx.beginPath(); ctx.arc(x + sx + eo, headCY - 2 * S, 6 * S, 0, Math.PI * 2); ctx.fill(); }
  ctx.strokeStyle = '#2b2620'; ctx.lineWidth = 4 * S; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.arc(x + eo, headCY + 18 * S, 16 * S, Math.PI * 0.15, Math.PI * 0.85); ctx.stroke();

  // NEAR arm (toward the robot) — drawn LAST so a raised wave reads on top of the head.
  // resting: down-out along the body. waving: raised STRAIGHT UP beside the head (a clear
  // greeting), tilted slightly toward the robot and wobbling at the wrist.
  ctx.save();
  const shoulderX = x + (bodyW / 2 - 4 * S) * face;
  const shoulderY = bodyY + 52 * S;               // shoulder sits on the torso, below the neck
  ctx.translate(shoulderX, shoulderY);
  if (wave > 0) {
    // up = π from the down-vector; small outward+wobble tilt toward the robot side, so the
    // raised arm rises just OUTSIDE the head (not across the face)
    const wobble = Math.sin(t * 9) * 0.18;
    ctx.rotate(Math.PI + face * (0.30 - wobble));
  } else {
    ctx.rotate(face * 0.4);                        // relaxed, down and slightly out
  }
  roundRect(ctx, -armW / 2, 0, armW, armL, armW / 2, mix(color, '#000', 0.16));
  glowDotRaw(ctx, 0, armL, armW * 0.62, skin, 0); // hand
  ctx.restore();

  ctx.restore();
}

// speech bubble that floats ABOVE a speaker and points DOWN at them.
// (anchorX, anchorY) = the point on the speaker the tail should touch (top of head).
// The bubble body sits `rise` px above the anchor, horizontally nudged by `dx` so it
// clears neighbours. content: a glyph name (lib.drawGlyph) OR a short string.
function speechBubble(ctx, anchorX, anchorY, content, alpha = 1, { color = '#fff', dx = 0, rise = 78 } = {}) {
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  const isText = typeof content === 'string';
  ctx.font = FONT(30);
  const w = isText ? Math.max(110, ctx.measureText(content).width + 52) : 100;
  const h = 76;
  const cx = anchorX + dx;                 // bubble center-x
  const by = anchorY - rise;               // bubble bottom edge
  // soft drop shadow so it reads against the sky
  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,0.18)'; ctx.shadowBlur = 16; ctx.shadowOffsetY = 4;
  roundRect(ctx, cx - w / 2, by - h, w, h, 20, color);
  ctx.restore();
  // downward tail aimed at the anchor (kept near the speaker even when dx nudges the body)
  const txBase = Math.max(cx - w / 2 + 22, Math.min(cx + w / 2 - 22, anchorX));
  ctx.beginPath();
  ctx.moveTo(txBase - 14, by - 2);
  ctx.lineTo(anchorX, anchorY - rise * 0.30);   // point toward the head
  ctx.lineTo(txBase + 14, by - 2);
  ctx.closePath(); ctx.fillStyle = color; ctx.fill();
  // content
  if (isText) {
    ctx.fillStyle = C.ink; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = FONT(30); ctx.fillText(content, cx, by - h / 2 - 1);
  } else {
    drawGlyph(ctx, cx, by - h / 2 - 1, 22, content, C.blue);
  }
  ctx.restore();
}

// ---- parallax world props -------------------------------------------------
// Rolling hills band. offset = world scroll (px). night tints them.
function hills(ctx, offset, gy, night, { color = '#a8c98f', amp = 70, span = 520, y = 0, speed = 0.4 } = {}) {
  ctx.save();
  ctx.fillStyle = mix(color, '#26304a', night);
  ctx.beginPath();
  ctx.moveTo(0, gy);
  const o = offset * speed;
  for (let x = -span; x <= W + span; x += 8) {
    const yy = gy + y - (Math.sin((x + o) / span * Math.PI * 2) * 0.5 + 0.5) * amp;
    ctx.lineTo(x, yy);
  }
  ctx.lineTo(W, gy); ctx.closePath(); ctx.fill();
  ctx.restore();
}

// a simple signpost / lamp prop at world-x (already converted to screen-x)
function signpost(ctx, x, gy, night) {
  ctx.save();
  ctx.strokeStyle = mix('#8a7a5a', '#2a3350', night); ctx.lineWidth = 8; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(x, gy); ctx.lineTo(x, gy - 120); ctx.stroke();
  ctx.fillStyle = mix('#c2a878', '#3a4566', night);
  roundRect(ctx, x - 6, gy - 140, 64, 30, 6, mix('#c2a878', '#3a4566', night));
  ctx.restore();
}

// ---- memory dots ----------------------------------------------------------
// A glowing memory: a colored coin with a soft glow, a thin dark rim so it pops
// against the bright sky, a small specular highlight, and a hand-drawn vector
// glyph (NOT an emoji — emoji render as tofu boxes in headless Chromium).
function memDot(ctx, x, y, r, color, { glyph = null, glow = 18, alpha = 1, ring = 0 } = {}) {
  ctx.save();
  ctx.globalAlpha = alpha;
  // expanding poof ring
  if (ring > 0) {
    ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.globalAlpha = alpha * 0.55;
    ctx.beginPath(); ctx.arc(x, y, r + 6 + ring * 12, 0, Math.PI * 2); ctx.stroke();
    ctx.globalAlpha = alpha;
  }
  // glow + body
  glowDotRaw(ctx, x, y, r, color, glow);
  // darker rim for contrast against sky
  ctx.strokeStyle = 'rgba(0,0,0,0.18)'; ctx.lineWidth = Math.max(2, r * 0.08);
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
  // glyph (drawn in white, vector)
  if (glyph) drawGlyph(ctx, x, y, r * 0.62, glyph, 'rgba(255,255,255,0.95)');
  // small specular highlight (kept small so it doesn't wash out the color)
  ctx.fillStyle = 'rgba(255,255,255,0.5)';
  ctx.beginPath(); ctx.arc(x - r * 0.4, y - r * 0.42, r * 0.18, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

// simple vector glyphs at center (x,y) sized to s; all stroke/fill in `col`.
function drawGlyph(ctx, x, y, s, glyph, col) {
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = col; ctx.fillStyle = col;
  ctx.lineWidth = Math.max(2.5, s * 0.22); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  switch (glyph) {
    case 'wrench': {
      ctx.rotate(-Math.PI / 4);
      ctx.beginPath(); ctx.moveTo(-s * 0.1, s * 0.7); ctx.lineTo(-s * 0.1, -s * 0.2); ctx.stroke();
      ctx.beginPath(); ctx.arc(-s * 0.1, -s * 0.45, s * 0.32, Math.PI * 0.15, Math.PI * 1.85); ctx.stroke();
      break;
    }
    case 'gear': {
      const teeth = 8;
      ctx.beginPath();
      for (let i = 0; i < teeth; i++) {
        const a = (i / teeth) * Math.PI * 2;
        ctx.moveTo(Math.cos(a) * s * 0.55, Math.sin(a) * s * 0.55);
        ctx.lineTo(Math.cos(a) * s * 0.85, Math.sin(a) * s * 0.85);
      }
      ctx.stroke();
      ctx.beginPath(); ctx.arc(0, 0, s * 0.5, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.arc(0, 0, s * 0.18, 0, Math.PI * 2); ctx.fill();
      break;
    }
    case 'bulb': {
      ctx.beginPath(); ctx.arc(0, -s * 0.15, s * 0.5, Math.PI * 0.15, Math.PI * 0.85, true); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-s * 0.22, s * 0.35); ctx.lineTo(s * 0.22, s * 0.35);
      ctx.moveTo(-s * 0.16, s * 0.55); ctx.lineTo(s * 0.16, s * 0.55); ctx.stroke();
      break;
    }
    case 'key': {
      ctx.beginPath(); ctx.arc(-s * 0.35, 0, s * 0.32, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-s * 0.05, 0); ctx.lineTo(s * 0.75, 0);
      ctx.moveTo(s * 0.55, 0); ctx.lineTo(s * 0.55, s * 0.28);
      ctx.moveTo(s * 0.75, 0); ctx.lineTo(s * 0.75, s * 0.28); ctx.stroke();
      break;
    }
    case 'star': {
      ctx.beginPath();
      for (let i = 0; i < 10; i++) {
        const a = -Math.PI / 2 + i * Math.PI / 5;
        const rr = i % 2 === 0 ? s * 0.85 : s * 0.38;
        const px = Math.cos(a) * rr, py = Math.sin(a) * rr;
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
      }
      ctx.closePath(); ctx.fill();
      break;
    }
    case 'check': {
      ctx.beginPath(); ctx.moveTo(-s * 0.5, 0); ctx.lineTo(-s * 0.1, s * 0.42);
      ctx.lineTo(s * 0.6, -s * 0.5); ctx.stroke();
      break;
    }
  }
  ctx.restore();
}
function glowDotRaw(ctx, x, y, r, color, glow) {
  ctx.save();
  if (glow > 0) { ctx.shadowColor = color; ctx.shadowBlur = glow; }
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

// ---- shapes ---------------------------------------------------------------
function roundRect(ctx, x, y, w, h, r, fill) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
}
function roundRectStroke(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
  ctx.stroke();
}

// ---- labels / cards -------------------------------------------------------
// corner DAY label
function dayLabel(ctx, text, night) {
  ctx.save();
  ctx.font = FONT(40);
  ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  ctx.fillStyle = night > 0.5 ? C.inkN : C.ink;
  ctx.globalAlpha = 0.85;
  ctx.fillText(text, 70, 70);
  ctx.restore();
}

// centered title card text with fade
function titleCard(ctx, text, t, t0, dur, { y = H * 0.5, size = 92, color = '#fff', sub = null } = {}) {
  const a = Math.min(seg(t, t0, t0 + 0.45), 1 - seg(t, t0 + dur - 0.45, t0 + dur));
  if (a <= 0) return;
  ctx.save();
  ctx.globalAlpha = a;
  ctx.font = FONT(size);
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillStyle = color;
  // slight rise
  const rise = (1 - easeOut(seg(t, t0, t0 + 0.5))) * 18;
  ctx.fillText(text, W / 2, y + rise);
  if (sub) {
    ctx.font = FONTR(36);
    ctx.fillStyle = 'rgba(255,255,255,0.8)';
    ctx.fillText(sub, W / 2, y + size * 0.78 + rise);
  }
  ctx.restore();
}

// a floating emote symbol above the robot (? or ✓ or ✨)
function emoteSymbol(ctx, x, y, sym, t, t0, color) {
  const a = seg(t, t0, t0 + 0.35);
  if (a <= 0) return;
  const pop = easeOutBack(a);
  ctx.save();
  ctx.globalAlpha = Math.min(1, a);
  ctx.translate(x, y - 10 * easeOut(seg(t, t0, t0 + 1.2)));
  ctx.scale(pop, pop);
  ctx.font = FONT(86);
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillStyle = color;
  ctx.shadowColor = color; ctx.shadowBlur = 24;
  ctx.fillText(sym, 0, 0);
  ctx.restore();
}

// "Zzz" sleep marks
function zzz(ctx, x, y, t, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = C.inkN;
  ctx.font = FONT(34);
  ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  for (let i = 0; i < 3; i++) {
    const ph = (t * 0.6 + i * 0.33) % 1;
    const yy = y - i * 30 - ph * 18;
    ctx.globalAlpha = alpha * (1 - ph) * (0.5 + i * 0.2);
    ctx.font = FONT(24 + i * 10);
    ctx.fillText('z', x + i * 22 + Math.sin((t + i) * 2) * 4, yy);
  }
  ctx.restore();
}

// a four-point sparkle/twinkle (drawn, not emoji)
function drawSparkle(ctx, x, y, s, color, alpha = 1) {
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.shadowColor = color; ctx.shadowBlur = 18;
  ctx.beginPath();
  // 4-point star using quadratic pinch
  const pts = [[0, -s], [s * 0.22, -s * 0.22], [s, 0], [s * 0.22, s * 0.22],
               [0, s], [-s * 0.22, s * 0.22], [-s, 0], [-s * 0.22, -s * 0.22]];
  ctx.moveTo(x + pts[0][0], y + pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(x + pts[i][0], y + pts[i][1]);
  ctx.closePath(); ctx.fill();
  ctx.restore();
}

function vignette(ctx) {
  const g = ctx.createRadialGradient(W/2, H/2, H*0.35, W/2, H/2, H*0.8);
  g.addColorStop(0, 'rgba(0,0,0,0)');
  g.addColorStop(1, 'rgba(0,0,0,0.22)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
}

// ---- Act 2 diagram primitives --------------------------------------------
// A labeled node box. `pop` 0..1 scales/fades it in. `shape`: 'box' | 'store' | 'hub'.
// title + optional sub line. `glow` adds a colored halo (for the active node).
function diagramNode(ctx, n) {
  const { x, y, w, h, title, sub = null, fill = '#eaf2ff', stroke = '#3b6fb0',
          ink = C.ink, pop = 1, shape = 'box', glow = 0, dim = 0 } = n;
  if (pop <= 0.001) return;
  ctx.save();
  ctx.globalAlpha = Math.min(1, pop) * (1 - dim * 0.62);
  const p = easeOutBack(clamp01(pop));
  ctx.translate(x, y);
  ctx.scale(p, p);
  ctx.translate(-x, -y);
  if (glow > 0) { ctx.shadowColor = stroke; ctx.shadowBlur = 26 * glow; }
  if (shape === 'store') {
    // three stacked slabs (vectors · graph · markdown), each labeled inside.
    const labels = ['vectors', 'graph', 'markdown'];
    for (let i = 2; i >= 0; i--) {
      const sy = storeSlabCY(n, i);
      const sh = h * 0.30;
      // opaque light backing so the slab reads bright against the dark head, then the tint
      roundRect(ctx, x - w / 2, sy - sh / 2, w, sh, 8, '#fffdf4');
      roundRect(ctx, x - w / 2, sy - sh / 2, w, sh, 8, mix(fill, '#ffffff', 0.35 + i * 0.12));
      ctx.shadowBlur = 0;
      ctx.strokeStyle = stroke; ctx.lineWidth = 2.5; roundRectStroke(ctx, x - w / 2, sy - sh / 2, w, sh, 8);
      ctx.fillStyle = ink; ctx.font = FONTR(20); ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText(labels[i], x - w / 2 + 16, sy);
      if (glow > 0) ctx.shadowBlur = 26 * glow;
    }
    ctx.shadowBlur = 0;
    // title sits ABOVE the stack so it never overlaps the slabs
    ctx.fillStyle = '#cfe6ff'; ctx.font = FONT(28); ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText(title, x, y - h / 2 - 14);
    ctx.restore();
    return;
  } else if (shape === 'hub') {
    ctx.fillStyle = fill;
    ctx.beginPath(); ctx.arc(x, y, w / 2, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0; ctx.strokeStyle = stroke; ctx.lineWidth = 3.5;
    ctx.beginPath(); ctx.arc(x, y, w / 2, 0, Math.PI * 2); ctx.stroke();
  } else {
    roundRect(ctx, x - w / 2, y - h / 2, w, h, 16, fill);
    ctx.shadowBlur = 0; ctx.strokeStyle = stroke; ctx.lineWidth = 3;
    roundRectStroke(ctx, x - w / 2, y - h / 2, w, h, 16);
  }
  ctx.shadowBlur = 0;
  // labels (box / hub)
  ctx.fillStyle = ink; ctx.textAlign = 'center';
  ctx.textBaseline = sub ? 'alphabetic' : 'middle';
  ctx.font = FONT(sub ? 30 : 32);
  ctx.fillText(title, x, sub ? y - 2 : y + 1);
  if (sub) { ctx.font = FONTR(21); ctx.fillStyle = mix(ink, '#fff', 0.28); ctx.fillText(sub, x, y + 26); }
  ctx.restore();
}
// canonical center-y of store slab i (0=top) — shared by node, highlight, and flows.
function storeSlabCY(n, i) { return n.y - n.h / 2 + n.h * 0.18 + i * (n.h * 0.30); }

// A connector between two points. `draw` 0..1 animates the line drawing on.
// arrows: 'end' | 'both' | 'none'. `dashed` for the offline/adapter edges.
function diagramArrow(ctx, x1, y1, x2, y2, draw = 1, { color = '#7a8088', width = 3.5,
                       arrows = 'end', dashed = false, label = null, bend = 0 } = {}) {
  if (draw <= 0.001) return;
  ctx.save();
  ctx.globalAlpha = Math.min(1, draw + 0.0);
  ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round';
  if (dashed) ctx.setLineDash([10, 9]);
  // optional perpendicular bow so parallel edges don't overlap
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len, ny = dx / len;
  const cxp = mx + nx * bend, cyp = my + ny * bend;
  // animate by interpolating the visible end along the quadratic
  const k = easeInOut(clamp01(draw));
  const qx = (1 - k) * ((1 - k) * x1 + k * cxp) + k * ((1 - k) * cxp + k * x2);
  const qy = (1 - k) * ((1 - k) * y1 + k * cyp) + k * ((1 - k) * cyp + k * y2);
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(cxp, cyp, qx, qy); ctx.stroke();
  ctx.setLineDash([]);
  // arrowheads at the fully-drawn ends
  if (k > 0.98) {
    const head = (ax, ay, fromx, fromy) => {
      const a = Math.atan2(ay - fromy, ax - fromx), s = 13;
      ctx.beginPath(); ctx.moveTo(ax, ay);
      ctx.lineTo(ax - s * Math.cos(a - 0.5), ay - s * Math.sin(a - 0.5));
      ctx.lineTo(ax - s * Math.cos(a + 0.5), ay - s * Math.sin(a + 0.5));
      ctx.closePath(); ctx.fill();
    };
    if (arrows === 'end' || arrows === 'both') head(x2, y2, cxp, cyp);
    if (arrows === 'both') head(x1, y1, cxp, cyp);
  }
  // edge label
  if (label && k > 0.6) {
    ctx.globalAlpha = (k - 0.6) / 0.4;
    ctx.font = FONTR(20); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const lx = mx + nx * (bend * 0.5 + (bend >= 0 ? 16 : -16)), ly = my + ny * (bend * 0.5 + (bend >= 0 ? 16 : -16));
    ctx.fillStyle = 'rgba(255,255,255,0.92)';
    const lw = ctx.measureText(label).width + 14;
    roundRect(ctx, lx - lw / 2, ly - 13, lw, 26, 8, 'rgba(255,255,255,0.92)');
    ctx.fillStyle = color; ctx.fillText(label, lx, ly + 1);
  }
  ctx.restore();
}

// A pulse traveling along a segment (a memory dot moving between nodes). frac 0..1.
function flowDot(ctx, x1, y1, x2, y2, frac, color, { glyph = null, r = 18, bend = 0 } = {}) {
  const k = clamp01(frac);
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len, ny = dx / len;
  const cxp = mx + nx * bend, cyp = my + ny * bend;
  const x = (1 - k) * ((1 - k) * x1 + k * cxp) + k * ((1 - k) * cxp + k * x2);
  const y = (1 - k) * ((1 - k) * y1 + k * cyp) + k * ((1 - k) * cyp + k * y2);
  memDot(ctx, x, y, r, color, { glyph, glow: 20 });
  return { x, y };
}

// A translucent X-ray robot head outline framing the diagram stage.
function xrayHead(ctx, cx, cy, rw, rh, alpha, stroke = '#8fe3ff') {
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  // skull
  roundRect(ctx, cx - rw / 2, cy - rh / 2, rw, rh, 60, 'rgba(143,227,255,0.05)');
  ctx.strokeStyle = stroke; ctx.lineWidth = 4; ctx.setLineDash([2, 10]); ctx.lineCap = 'round';
  roundRectStroke(ctx, cx - rw / 2, cy - rh / 2, rw, rh, 60);
  ctx.setLineDash([]);
  // antenna nub up top
  ctx.beginPath(); ctx.moveTo(cx, cy - rh / 2); ctx.lineTo(cx, cy - rh / 2 - 40); ctx.stroke();
  glowDotRaw(ctx, cx, cy - rh / 2 - 46, 9, C.amber, 12);
  ctx.restore();
}

// horizontal band with a side label (CONSCIOUS / SUBCONSCIOUS).
function band(ctx, x, y, w, h, label, fill, ink, alpha) {
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  roundRect(ctx, x, y, w, h, 26, fill);
  ctx.fillStyle = ink; ctx.globalAlpha = alpha * 0.9;
  ctx.font = FONT(26); ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  // vertical-ish side tag
  ctx.fillText(label, x + 28, y + 20);
  ctx.restore();
}

window.LIB = {
  W, H, C, FONT, FONTR, clamp01, seg, easeOut, easeIn, easeInOut, easeOutBack, lerp,
  rng, mix, backdrop, robot, human, speechBubble, hills, signpost, nameTag,
  memDot, drawGlyph, glowDotRaw, roundRect, roundRectStroke,
  dayLabel, titleCard, emoteSymbol, zzz, drawSparkle, vignette, glowCircle,
  diagramNode, diagramArrow, flowDot, xrayHead, band, storeSlabCY,
};
window.SCENES = window.SCENES || {};
