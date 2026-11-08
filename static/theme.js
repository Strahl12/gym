/* theme.js — the app is hangar (dark) only; Sortie's daylight theme is not
   offered. Pinning data-theme ignores any gym:theme preference stored by the
   old theme picker. Loaded in <head> BEFORE the stylesheets so fonts warm
   early and the native shell is tagged before first paint. */
(function () {
  document.documentElement.dataset.theme = "hangar";

  // Warm the webfont faces that only JS-rendered content uses (numerals,
  // headings). Browsers load faces lazily on first use, which for fetched
  // content is after the load event — kicking the loads off at
  // DOMContentLoaded means numbers never paint blank.
  document.addEventListener("DOMContentLoaded", function () {
    if (!(document.fonts && document.fonts.load)) return;
    ["600 32px 'Chakra Petch'", "500 22px 'Chakra Petch'", "400 20px 'Chakra Petch'",
     "400 15px Saira", "500 15px Saira", "400 12px 'Share Tech Mono'",
     "400 10px Michroma"].forEach(function (f) { document.fonts.load(f); });
  });

  // iOS app (Capacitor shell appends GymCoachNative to the UA): tag the
  // document so CSS can diverge, and lock the viewport — no pinch zoom and,
  // critically, no auto-zoom when focusing inputs under 16px.
  if (navigator.userAgent.indexOf("GymCoachNative") !== -1) {
    document.documentElement.classList.add("native");
    var vp = document.querySelector('meta[name="viewport"]');
    if (vp) {
      var c = vp.getAttribute("content") || "width=device-width, initial-scale=1";
      if (c.indexOf("maximum-scale") === -1) {
        vp.setAttribute("content", c + ", maximum-scale=1, user-scalable=no");
      }
    }
  }
})();
