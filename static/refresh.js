/* refresh.js — pull-down-to-refresh for the app pages. Drag down from the
   top of the page past the threshold and release to reload (e.g. after the
   coach generates a new workout). Deliberately inert while a sheet/modal is
   open, while logging a session, or when the gesture starts on an input —
   an accidental reload mid-set would be rude (the draft survives, but still).
   Indicator is Sortie-styled: label + accent fill on a hairline track. */
(function () {
  if (!("ontouchstart" in window)) return;   // touch devices only

  var THRESH = 80;
  var startY = null, armed = false, fired = false;
  var ui = null, label = null, fill = null;

  function ensure() {
    if (ui) return;
    ui = document.createElement("div");
    ui.className = "srt-pull";
    label = document.createElement("div");
    label.className = "srt-pull-label";
    label.textContent = "Pull to refresh";
    var track = document.createElement("div");
    track.className = "srt-pull-track";
    fill = document.createElement("div");
    fill.className = "srt-pull-fill";
    track.appendChild(fill);
    ui.appendChild(label);
    ui.appendChild(track);
    document.body.appendChild(ui);
  }

  function blocked(target) {
    if (document.body.classList.contains("sheet-open")) return true;
    if (document.body.classList.contains("logging-active")) return true;
    if (document.querySelector(".modal-back")) return true;
    if (target && target.closest &&
        target.closest("input, textarea, select, .sheet, .modal")) return true;
    return false;
  }

  function hide() {
    if (ui) ui.classList.remove("show");
    armed = false;
  }

  document.addEventListener("touchstart", function (e) {
    startY = (window.scrollY <= 0 && !fired && !blocked(e.target))
      ? e.touches[0].clientY : null;
    armed = false;
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (startY == null || fired) return;
    if (window.scrollY > 0) { hide(); startY = null; return; }
    var pull = e.touches[0].clientY - startY;
    if (pull < 10) { hide(); return; }
    ensure();
    ui.classList.add("show");
    var p = Math.min(1, pull / THRESH);
    fill.style.width = (p * 100) + "%";
    armed = p >= 1;
    label.textContent = armed ? "Release to refresh" : "Pull to refresh";
  }, { passive: true });

  document.addEventListener("touchend", function () {
    if (startY == null || fired) return;
    startY = null;
    if (!armed) { hide(); return; }
    fired = true;
    label.textContent = "Refreshing";
    fill.style.width = "100%";
    ui.classList.add("go");
    setTimeout(function () { location.reload(); }, 150);
  }, { passive: true });
})();
