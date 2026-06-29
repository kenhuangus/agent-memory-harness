// FILM — the three acts stitched into ONE continuous, native, looping animation.
// "The Robot Who Couldn't Remember": Act 1 (the problem) -> Act 2 (inside the head)
// -> Act 3 (the solution). 78s total. Embedded once in the deck as a single iframe.
//
// This is a pure compositor: it delegates to each act's own draw(ctx, localT) with a
// rebased time, and joins the acts with the storyboard's CAMERA MOVES:
//   • Act 1 -> Act 2  : PUSH IN — zoom into the robot's head, emerge inside it.
//   • Act 2 -> Act 3  : PULL OUT — start close inside the head, recede back to the world.
// The zoom is a pure canvas transform here (scale about a focal point) plus a short
// black veil at the exact boundary to hide the content swap. No act code is duplicated —
// film.js just sequences + frames them, so the acts stay editable in isolation.
'use strict';
(() => {
  const L = window.LIB;
  const { W, H, clamp01 } = L;

  const ORDER = ['act1', 'act2', 'act3'];
  const SEAM = 1.2;            // length of each camera move (per side of a boundary)
  const ZOOM = 3.4;            // how far we push into the head at the peak of a seam

  // Camera move at each boundary between acts (ORDER[i] -> ORDER[i+1]):
  //   'in'  = PUSH IN  : outgoing act zooms 1->ZOOM into its head, incoming emerges ZOOM->1.
  //   'out' = PULL OUT : outgoing act holds wide (no zoom), incoming act OPENS at ZOOM on
  //                      its head and recedes ZOOM->1 — one continuous pull back to the world.
  const BOUNDARY = ['in', 'out'];   // act1->act2 dives in; act2->act3 pulls back out

  // Focal point of each act's "head", in canvas px — what the camera pushes toward / out of.
  //   Act 1 / Act 3: the walking robot's head (fixed screen-x, just above the body).
  //   Act 2: the x-ray skull fills the frame, so the focus is the canvas center.
  const GY = H * 0.74;
  const ROBOT_HEAD = { x: W * 0.40, y: GY - 224 };
  const HEAD_CENTER = { x: W / 2, y: H * 0.46 };
  const FOCUS = { act1: ROBOT_HEAD, act2: HEAD_CENTER, act3: ROBOT_HEAD };

  const seg = L.seg;
  const easeInOut = L.easeInOut;

  function acts() { return ORDER.map(id => window.SCENES[id]); }
  function layout() {
    const A = acts();
    let tcur = 0; const spans = [];
    for (let i = 0; i < A.length; i++) {
      spans.push({ id: ORDER[i], scene: A[i], start: tcur, dur: A[i].duration });
      tcur += A[i].duration;
    }
    return { spans, total: tcur };
  }

  // scale the canvas by `s` about focal point (fx,fy) — i.e. zoom keeping (fx,fy) fixed.
  function zoomAbout(ctx, fx, fy, s) {
    ctx.translate(fx, fy);
    ctx.scale(s, s);
    ctx.translate(-fx, -fy);
  }

  window.SCENES.film = {
    get duration() { return layout().total; },
    bg: '#0a0a0c',
    draw(ctx, t) {
      const { spans, total } = layout();
      const tt = Math.max(0, Math.min(t, total - 0.0001));
      let cur = spans[spans.length - 1];
      for (const s of spans) { if (tt >= s.start && tt < s.start + s.dur) { cur = s; break; } }
      const localT = tt - cur.start;
      const idx = spans.indexOf(cur);
      const isFirst = idx === 0, isLast = idx === spans.length - 1;

      // ---- camera scale for this frame ----
      // Each boundary is 'in' (push into the head) or 'out' (pull back to the world).
      //   OUTGOING half of act idx  uses BOUNDARY[idx]   (the move INTO the next act).
      //   INCOMING half of act idx  uses BOUNDARY[idx-1] (the move OUT of the prev act).
      // 'in' : outgoing 1->ZOOM,  incoming ZOOM->1  (dive in, then emerge in the next act).
      // 'out': outgoing stays 1,  incoming ZOOM->1  (next act OPENS close, recedes to world).
      let scale = 1, focus = FOCUS[cur.id];
      if (!isLast && BOUNDARY[idx] === 'in') {
        const k = easeInOut(seg(localT, cur.dur - SEAM, cur.dur));     // 0 -> 1
        if (k > 0) scale = 1 + (ZOOM - 1) * k;
      }
      if (!isFirst) {
        const k = easeInOut(seg(localT, 0, SEAM));                     // 0 -> 1
        if (k < 1) scale = 1 + (ZOOM - 1) * (1 - k);
      }

      ctx.save();
      if (scale !== 1) zoomAbout(ctx, focus.x, focus.y, scale);
      cur.scene.draw(ctx, localT);
      ctx.restore();

      // ---- black veil at the exact boundary ----
      // A brief dip right at the swap hides the content cut while the camera is deepest
      // in the head. Peaks at the boundary, narrower than the zoom so the motion shows.
      const VEIL = SEAM * 0.55;
      let dark = 0;
      if (!isLast) dark = Math.max(dark, seg(localT, cur.dur - VEIL, cur.dur));
      if (!isFirst) dark = Math.max(dark, 1 - seg(localT, 0, VEIL));
      if (dark > 0.001) {
        ctx.save();
        ctx.globalAlpha = clamp01(dark);
        ctx.fillStyle = '#0a0a0c';
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
      }
    },
  };
})();
