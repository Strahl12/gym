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
})();
