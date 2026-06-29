// FILM — the three acts stitched into ONE continuous, native, looping animation.
// "The Robot Who Couldn't Remember": Act 1 (the problem) -> Act 2 (inside the head)
// -> Act 3 (the solution). 78s total. Embedded once in the deck as a single iframe.
//
// This is a pure compositor: it delegates to each act's own draw(ctx, localT) with a
// rebased time, and dips through black at each seam (the storyboard's "push into the
// head" / "pull back out" camera moves, read as a clean deterministic crossfade).
// No act code is duplicated — film.js just sequences them, so the acts stay editable
// in isolation and the seams live in one place.
'use strict';
(() => {
  const L = window.LIB;
  const { W, H } = L;

  // Each act, in order, with its source scene id. Durations are read from the
  // registered scenes so this never drifts from the acts themselves.
  const ORDER = ['act1', 'act2', 'act3'];
  const SEAM = 0.9;            // crossfade-through-black duration at each act boundary

  function acts() { return ORDER.map(id => window.SCENES[id]); }

  // Build the timeline: each act occupies [start, start+duration); the next act starts
  // SEAM/2 early-overlap is avoided — we keep acts sequential and just darken across the
  // last/first SEAM seconds of adjacent acts so nothing pops.
  function layout() {
    const A = acts();
    let tcur = 0; const spans = [];
    for (let i = 0; i < A.length; i++) {
      spans.push({ id: ORDER[i], scene: A[i], start: tcur, dur: A[i].duration });
      tcur += A[i].duration;
    }
    return { spans, total: tcur };
  }

  const seg = L.seg;

  window.SCENES.film = {
    // duration is computed from the acts at register time
    get duration() { return layout().total; },
    bg: '#0a0a0c',
    draw(ctx, t) {
      const { spans, total } = layout();
      const tt = Math.max(0, Math.min(t, total - 0.0001));
      // find the active act
      let cur = spans[spans.length - 1];
      for (const s of spans) { if (tt >= s.start && tt < s.start + s.dur) { cur = s; break; } }
      const localT = tt - cur.start;

      // draw the active act in its own local time
      cur.scene.draw(ctx, localT);

      // ---- seam dip-to-black ----
      // darken across the final SEAM seconds of an act and the first SEAM seconds of the
      // next, peaking at the boundary. This hides the staging jump (walk world -> head
      // diagram -> walk world) without any per-act change.
      let dark = 0;
      // fade OUT at the end of this act (unless it's the very last act)
      const isLast = cur === spans[spans.length - 1];
      if (!isLast) {
        const endK = seg(localT, cur.dur - SEAM, cur.dur);   // 0 -> 1 approaching the end
        dark = Math.max(dark, endK);
      }
      // fade IN at the start of this act (unless it's the very first act)
      const isFirst = cur === spans[0];
      if (!isFirst) {
        const startK = 1 - seg(localT, 0, SEAM);             // 1 -> 0 leaving the start
        dark = Math.max(dark, startK);
      }
      if (dark > 0.001) {
        ctx.save();
        ctx.globalAlpha = Math.min(1, dark);
        ctx.fillStyle = '#0a0a0c';
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
      }
    },
  };
})();
