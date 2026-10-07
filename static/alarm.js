/* alarm.js — rest-timer alarm sounds, shared by the Workout tab (fires the
   alarm) and Settings (sound picker + preview). Choice is per device in
   localStorage "rt:sound": beep | screamo | off. All synthesized via WebAudio
   — no audio files. iOS suspends the AudioContext between interactions, so
   playAlarm resumes first and emits only once the context is running. */
var _gymAC = null;
function _audio() {                    // lazily create + unlock (needs a user gesture)
  try {
    _gymAC = _gymAC || new (window.AudioContext || window.webkitAudioContext)();
    if (_gymAC.state === "suspended") _gymAC.resume();
    return _gymAC;
  } catch (e) { return null; }
}
var SOUNDS = ["beep", "screamo", "off"];
var SOUND_ICON = { beep: "🔊", screamo: "🔥", off: "🔇" };
function soundMode() { try { return localStorage.getItem("rt:sound") || "beep"; } catch (e) { return "beep"; } }
function setSoundMode(m) { try { localStorage.setItem("rt:sound", m); } catch (e) {} }

function playAlarm(mode) {
  mode = mode || soundMode();
  if (mode === "off") return;
  var ac = _audio();
  if (!ac) return;
  if (ac.state === "suspended") { ac.resume().then(function () { _emitAlarm(mode, ac); }).catch(function () {}); }
  else _emitAlarm(mode, ac);
}

function _emitAlarm(mode, ac) {
  var t0 = ac.currentTime;
  if (mode === "screamo") {            // harsh detuned saw stack + noise burst
    var dur = 1.1;
    var master = ac.createGain();
    master.gain.setValueAtTime(0.0001, t0);
    master.gain.exponentialRampToValueAtTime(0.5, t0 + 0.03);
    master.gain.setValueAtTime(0.5, t0 + dur - 0.15);
    master.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    master.connect(ac.destination);
    var dist = ac.createWaveShaper();
    var curve = new Float32Array(256);
    for (var i = 0; i < 256; i++) { var x = i / 128 - 1; curve[i] = Math.tanh(x * 4); }
    dist.curve = curve; dist.connect(master);
    [110, 138, 220, 277].forEach(function (f) {
      var o = ac.createOscillator(); o.type = "sawtooth";
      o.frequency.setValueAtTime(f, t0);
      o.frequency.exponentialRampToValueAtTime(f * 1.5, t0 + dur);
      var g = ac.createGain(); g.gain.value = 0.25;
      o.connect(g); g.connect(dist); o.start(t0); o.stop(t0 + dur);
    });
    var nb = ac.createBuffer(1, Math.floor(ac.sampleRate * dur), ac.sampleRate);
    var nd = nb.getChannelData(0);
    for (var j = 0; j < nd.length; j++) nd[j] = (Math.random() * 2 - 1) * 0.4;
    var ns = ac.createBufferSource(); ns.buffer = nb;
    var ng = ac.createGain(); ng.gain.value = 0.15;
    ns.connect(ng); ng.connect(dist); ns.start(t0); ns.stop(t0 + dur);
  } else {                             // beep: three rising sine blips
    [660, 880, 1180].forEach(function (f, i) {
      var o = ac.createOscillator(), g = ac.createGain();
      o.type = "sine"; o.frequency.value = f;
      var s = t0 + i * 0.16;
      g.gain.setValueAtTime(0.0001, s);
      g.gain.exponentialRampToValueAtTime(0.3, s + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, s + 0.15);
      o.connect(g); g.connect(ac.destination); o.start(s); o.stop(s + 0.16);
    });
  }
}
