/*
  Interviewer orb: a glass globe with smoke, drawn on the graphics chip (WebGL).

  Purely additive. It does not change any interview logic: it only puts a canvas over the interviewer picture and reads the frame's
  data-state attribute. The picture, the Tavus video, the candidate's camera tile and every script stay as they were.
  If WebGL is unavailable, nothing is added and the old picture shows. To undo: remove the <script> tag for this file.
*/
(function () {
  "use strict";
  var frame = document.getElementById("iv2AvatarFrame");
  if (!frame) return;
  var COLORS = [[79,91,255],[62,123,255],[30,150,245],[20,175,225],[16,185,190],[26,185,150],[154,77,255],[214,70,230],[255,154,61],[22,185,129]];
  var VS = "attribute vec2 aPos; varying vec2 vUv;\nvoid main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }";
  var FS = "precision highp float;\nvarying vec2 vUv;\nuniform float uTime, uAmp, uThink;\nuniform vec3 uColor;\nconst int STEPS = 10;\n\nfloat hash(vec3 p) { p = fract(p * 0.3183099 + vec3(0.1, 0.2, 0.3)); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }\nfloat noise(vec3 x) {\n  vec3 i = floor(x), f = fract(x); f = f * f * (3.0 - 2.0 * f);\n  return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),\n             mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);\n}\nfloat fbm3(vec3 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 3; i++) { s += a * noise(p); p = p * 2.03 + vec3(1.7, 9.2, 3.1); a *= 0.5; } return s; }\nvec3 rotY(vec3 p, float a) { float c = cos(a), s = sin(a); return vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z); }\n\nfloat density(vec3 p, float t) {\n  float r = length(p);\n  // slow rising drift with a gentle sideways sway, no spinning: smoke billows, it does not swirl like a jelly\n  vec3 q = p;\n  q.x += 0.08 * sin(t * 0.05 + p.y * 2.1);\n  q.z += 0.08 * cos(t * 0.04 + p.y * 1.7);\n  vec3 up = vec3(0.0, -t * 0.022, 0.0) - normalize(p + 0.001) * t * 0.018;   // very slow lift, and a slow spreading outward\n  vec3 w = vec3(noise(q * 1.3 + up * 0.6 + 3.1), noise(q * 1.3 + up * 0.6 + 7.7), 0.5) - 0.5;\n  vec3 z = q * vec3(1.7, 1.2, 1.7) + w * (1.8 + uAmp * 2.2) + up;      // stretched upward: long wisps and plumes\n  float n = fbm3(z);\n  float ridge = 1.0 - abs(2.0 * n - 1.0);\n  float d = pow(ridge, 6.0);                                           // only the thin ridges survive: the rest is clear air\n  d *= 0.55 + 0.9 * noise(q * 1.1 + up * 0.3) + uAmp * 0.25;                         // patchy, so it thins out like real fog\n  return smoothstep(0.24 - uAmp * 0.08, 0.8, d) * smoothstep(1.0, 0.5, r);\n}\n\nvoid main() {\n  vec2 uv = vUv * 2.0 - 1.0;\n  float r2 = dot(uv, uv), rr = sqrt(r2);\n  float aa = smoothstep(1.0, 0.986, rr);\n  if (aa <= 0.0) { gl_FragColor = vec4(0.0); return; }\n  float z = sqrt(max(0.0, 1.0 - r2));\n  vec3 n = normalize(vec3(uv, z));\n\n  vec3 deep = uColor * 0.30 + vec3(0.02, 0.03, 0.09);\n  vec3 lite = mix(uColor, vec3(1.0), 0.62);\n  vec3 col = mix(deep, lite, pow(1.0 - rr, 1.6) * 0.42);\n\n  // smoke: march through the globe from the front to the back\n  float dt = (2.0 * z) / float(STEPS), alpha = 0.0;\n  vec3 acc = vec3(0.0);\n  vec2 bend = n.xy * 0.14 * (1.0 - z);                                       // the glass bends the view a little\n  for (int i = 0; i < STEPS; i++) {\n    float tt = (float(i) + 0.5) * dt;\n    vec3 p = vec3(uv + bend * tt * 0.6, z - tt);\n    float d = density(p, uTime);\n    float a = clamp(d * dt * (2.4 + uAmp * 2.0), 0.0, 1.0);\n    float lit = clamp(0.5 + 0.38 * dot(p, normalize(vec3(-0.55, 0.62, 0.55))) + 0.28 * d, 0.0, 1.0);   // lit from the top left\n    vec3 sc = mix(uColor * 0.5 + 0.42, vec3(1.0), lit);\n    acc += (1.0 - alpha) * a * sc; alpha += (1.0 - alpha) * a;\n  }\n  col = col * (1.0 - alpha) + acc;\n\n  // the glass shell\n  float fres = pow(1.0 - z, 3.0);\n  vec3 env = mix(vec3(0.72, 0.82, 1.0), vec3(1.0), smoothstep(-0.3, 0.9, n.y));\n  col += fres * env * 0.6;\n  vec2 h = uv - vec2(-0.40, 0.50);\n  h = vec2(h.x * 0.92 + h.y * 0.38, h.y * 1.18 - h.x * 0.30);\n  col += smoothstep(0.46, 0.10, length(h * vec2(1.0, 1.85))) * 0.62 * (0.6 + 0.4 * z);       // soft-box window reflection\n  vec3 L = normalize(vec3(-0.45, 0.55, 0.7));\n  col += pow(max(reflect(-L, n).z, 0.0), 90.0) * 0.95;                                         // sharp highlight\n  float cres = smoothstep(0.30, 0.0, abs(uv.y + 0.74 - 0.12 * uv.x * uv.x)) * smoothstep(0.75, 0.15, abs(uv.x));\n  col += cres * 0.30 * lite;                                                                    // light focusing through the bottom\n  col = mix(col, vec3(1.0), smoothstep(0.93, 1.0, rr) * 0.40);                                  // bright rim\n  col += uAmp * 0.10 * lite;\n\n  gl_FragColor = vec4(col * aa, aa);\n}";

  var wrap = document.createElement("div");
  wrap.className = "iv2-orb";
  var canvas = document.createElement("canvas");
  canvas.width = canvas.height = 480;
  canvas.setAttribute("aria-hidden", "true");
  wrap.appendChild(canvas);
  var gl = null;
  try { gl = canvas.getContext("webgl", { premultipliedAlpha: true, alpha: true, antialias: false }); } catch (e) { gl = null; }
  if (!gl) return;

  function sh(type, src) { var o = gl.createShader(type); gl.shaderSource(o, src); gl.compileShader(o); return gl.getShaderParameter(o, gl.COMPILE_STATUS) ? o : null; }
  var v = sh(gl.VERTEX_SHADER, VS), f = sh(gl.FRAGMENT_SHADER, FS);
  if (!v || !f) return;
  var prog = gl.createProgram(); gl.attachShader(prog, v); gl.attachShader(prog, f); gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
  gl.useProgram(prog);
  var buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
  var loc = gl.getAttribLocation(prog, "aPos"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  var uT = gl.getUniformLocation(prog, "uTime"), uA = gl.getUniformLocation(prog, "uAmp"), uK = gl.getUniformLocation(prog, "uThink"), uC = gl.getUniformLocation(prog, "uColor");
  gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.clearColor(0, 0, 0, 0);

  frame.insertBefore(wrap, frame.firstChild);
  frame.classList.add("has-orb");

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = frame.getAttribute("data-state") || "idle", q = COLORS.length - 1, cur = COLORS[0].slice(), flow = 0, amp = 0, think = 0, burst = 0;
  function lerp(a, b, k) { return a + (b - a) * k; }

  // a new colour for each question: the colour steps each time the interviewer starts speaking
  new MutationObserver(function () {
    var s = frame.getAttribute("data-state") || "idle";
    if (s === "speaking" && state !== "speaking") q = (q + 1) % COLORS.length;
    state = s;
  }).observe(frame, { attributes: true, attributeFilter: ["data-state"] });
  if (state === "speaking") q = 0;

  // the real loudness of the AI's voice, when the page announces its audio track (the motion then follows the actual speech)
  var actx = null, analyser = null, data = null, srcNode = null;
  window.addEventListener("jobpilot-ai-audio-track", function (ev) {
    try {
      var track = ev && ev.detail && ev.detail.track; if (!track) return;
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      if (srcNode) { try { srcNode.disconnect(); } catch (e) {} }
      srcNode = actx.createMediaStreamSource(new MediaStream([track]));
      analyser = analyser || actx.createAnalyser(); analyser.fftSize = 512; data = new Uint8Array(analyser.fftSize);
      if (actx.state === 'suspended') actx.resume();
      srcNode.connect(analyser);   // analysis only: not connected to the speakers, so the sound is untouched
    } catch (e) { analyser = null; }
  });
  function level() {
    if (!analyser) return -1;
    analyser.getByteTimeDomainData(data);
    var s = 0; for (var i = 0; i < data.length; i++) { var d = (data[i] - 128) / 128; s += d * d; }
    return Math.min(1, Math.sqrt(s / data.length) * 5);
  }

  var dpr = Math.min(1, window.devicePixelRatio || 1), scale = dpr * 0.8, ema = 16, frames = 0, last = performance.now(), t0 = last, visible = true;
  document.addEventListener("visibilitychange", function () { visible = !document.hidden; if (visible) requestAnimationFrame(draw); });
  function resize() { var px = Math.max(160, Math.min(340, Math.round((wrap.clientWidth || 480) * scale))); if (canvas.width !== px) { canvas.width = canvas.height = px; gl.viewport(0, 0, px, px); } }

  function draw(now) {
    if (!visible) return;
    var dt = now - last; last = now; ema = ema * .93 + Math.min(100, dt) * .07;
    if (++frames % 30 === 0) {   // quality follows the computer, so the interview never lags
      if (ema > 24 && scale > .35) scale = Math.max(.35, scale * .85); else if (ema < 14 && scale < dpr * .8) scale = Math.min(dpr * .8, scale * 1.06);
      resize();
    }
    var dts = Math.min(.1, dt / 1000), target = COLORS[q];
    for (var k = 0; k < 3; k++) cur[k] = lerp(cur[k], target[k], .05);
    var tgt, lv = level();
    if (state === "speaking") {
      if (lv >= 0) tgt = .1 + lv * 1.0;
      else { if (Math.random() < .045) burst = .55 + Math.random() * .45; burst *= .93; tgt = .3 + .25 * Math.abs(Math.sin(flow * 1.7)) + .2 * Math.abs(Math.sin(flow * .8 + 1)) + burst * .3; }
    }
    else if (state === "thinking" || state === "preparing") tgt = .12 + .04 * Math.sin(flow * 1.2);
    else if (state === "listening" || state === "candidate-speaking") tgt = .08;
    else tgt = .04;
    amp = lerp(amp, reduce ? Math.min(tgt, .12) : tgt, tgt > amp ? .45 : .07);   // quick to rise with the voice, slow to fall back
    flow += dts * (3.4 + amp * 20);
    think = lerp(think, (state === "thinking" || state === "preparing") ? 1 : 0, .08);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform1f(uT, reduce ? 0 : flow); gl.uniform1f(uA, amp); gl.uniform1f(uK, think); gl.uniform3f(uC, cur[0] / 255, cur[1] / 255, cur[2] / 255);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    requestAnimationFrame(draw);
  }
  resize();
  requestAnimationFrame(draw);
})();
