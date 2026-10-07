/* theme.js — applies the athlete's theme preference before first paint.
   Preference lives in localStorage ("gym:theme": system | dark | light);
   "system" resolves via prefers-color-scheme. tdr.css keys its light palette
   off html[data-theme="light"], so this script is the single switch point.
   Loaded in <head> BEFORE the stylesheet so there is no wrong-theme flash. */
(function () {
  function resolve(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches)
      ? "light" : "dark";
  }
  function apply() {
    var pref = "system";
    try { pref = localStorage.getItem("gym:theme") || "system"; } catch (e) {}
    document.documentElement.dataset.theme = resolve(pref);
  }
  window.__gymTheme = {
    get: function () {
      try { return localStorage.getItem("gym:theme") || "system"; } catch (e) { return "system"; }
    },
    set: function (pref) {
      try { localStorage.setItem("gym:theme", pref); } catch (e) {}
      apply();
    },
  };
  if (window.matchMedia) {   // live-follow the OS while in "system" mode
    try { matchMedia("(prefers-color-scheme: light)").addEventListener("change", apply); } catch (e) {}
  }
  apply();

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
