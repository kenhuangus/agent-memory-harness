/* ============ MAESTRO deck navigation ============ */
(function () {
  const slides = Array.from(document.querySelectorAll(".slide"));
  const total = slides.length;
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");
  const count = document.getElementById("count");
  const dotsWrap = document.getElementById("dots");
  const progress = document.getElementById("progress");
  const title = document.getElementById("slideTitle");

  // build dots
  slides.forEach((_, i) => {
    const d = document.createElement("button");
    d.className = "dot"; d.setAttribute("aria-label", "Go to slide " + (i + 1));
    d.addEventListener("click", () => go(i));
    dotsWrap.appendChild(d);
  });
  const dots = Array.from(dotsWrap.children);

  let cur = 0;

  function clamp(i) { return Math.max(0, Math.min(total - 1, i)); }

  function go(i) {
    i = clamp(i);
    slides.forEach((s, idx) => {
      s.classList.toggle("is-active", idx === i);
      s.classList.toggle("is-prev", idx < i);
    });
    dots.forEach((d, idx) => d.classList.toggle("is-on", idx === i));
    cur = i;
    count.textContent = (i + 1) + " / " + total;
    title.textContent = slides[i].dataset.title || "";
    progress.style.width = ((i) / (total - 1) * 100) + "%";
    prevBtn.disabled = i === 0;
    nextBtn.disabled = i === total - 1;
    if (history.replaceState) history.replaceState(null, "", "#" + (i + 1));
  }

  const next = () => go(cur + 1);
  const prev = () => go(cur - 1);
  nextBtn.addEventListener("click", next);
  prevBtn.addEventListener("click", prev);

  // keyboard
  document.addEventListener("keydown", e => {
    if (["ArrowRight", "PageDown", " "].includes(e.key)) { e.preventDefault(); next(); }
    else if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); prev(); }
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(total - 1);
  });

  // touch swipe
  let x0 = null;
  document.addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, { passive: true });
  document.addEventListener("touchend", e => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0;
    if (Math.abs(dx) > 50) (dx < 0 ? next : prev)();
    x0 = null;
  }, { passive: true });

  // wheel = navigate (debounced) so a trackpad/mouse advances slides
  let wheelLock = false;
  document.addEventListener("wheel", e => {
    if (wheelLock) return;
    if (Math.abs(e.deltaY) < 24 && Math.abs(e.deltaX) < 24) return;
    wheelLock = true; setTimeout(() => (wheelLock = false), 650);
    (e.deltaY > 0 || e.deltaX > 0) ? next() : prev();
  }, { passive: true });

  // deep-link via hash
  const start = parseInt((location.hash || "").replace("#", ""), 10);
  go(isNaN(start) ? 0 : start - 1);
})();
