/**
 * JARVIS — Multi-mode particle visualization.
 *
 * Floating particles with line connections between nearby ones.
 * Lines fade in/out based on state. Transition tumble on state change.
 * Speaking pulls particles closer for denser connections.
 */

import * as THREE from "three";

// "compacting": the brain is being swapped for a fresh one behind the scenes
// (a context rotation). He answers nothing for a few seconds, and silence with
// no explanation reads as a crash. It used to be followed by a spoken line;
// the user found that annoying, so the orb carries it instead: dim, slow,
// drawn inward, in a cooler colour -- unmistakably not "about to answer".
export type OrbState = "idle" | "listening" | "thinking" | "speaking" | "compacting";

export interface Orb {
  setState(s: OrbState): void;
  setPaused(p: boolean): void;
  /** One colour per state (see themes.ts). */
  setPalette(colours: Record<OrbState, string>): void;
  setAnalyser(a: AnalyserNode | null): void;
  destroy(): void;
}

export function createOrb(canvas: HTMLCanvasElement, zoom = 1): Orb {
  let destroyed = false;
  const N = 2000;

  // Transparent: the page's own background (grid, glow) shows through.
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 1, 1000);
  camera.position.z = 104 / zoom;

  // ── Particles ──
  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const phase = new Float32Array(N);

  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = Math.pow(Math.random(), 0.5) * 25;
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
    phase[i] = Math.random() * 1000;
  }

  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));

  const mat = new THREE.PointsMaterial({
    color: 0x4ca8e8, size: 0.4, transparent: true, opacity: 0.6,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  // ── Connection lines ──
  const MAX_LINES = 8000;
  const linePos = new Float32Array(MAX_LINES * 6);
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.BufferAttribute(linePos, 3));
  lineGeo.setDrawRange(0, 0);

  const lineMat = new THREE.LineBasicMaterial({
    color: 0x4ca8e8, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const lines = new THREE.LineSegments(lineGeo, lineMat);
  scene.add(lines);

  // ── Electrons — bright dots that travel along connections ──
  const MAX_ELECTRONS = 200;
  const electronGeo = new THREE.BufferGeometry();
  const electronPos = new Float32Array(MAX_ELECTRONS * 3);
  electronGeo.setAttribute("position", new THREE.BufferAttribute(electronPos, 3));
  electronGeo.setDrawRange(0, 0);

  const electronMat = new THREE.PointsMaterial({
    color: 0xffffff, size: 0.8, transparent: true, opacity: 1.0,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const electrons = new THREE.Points(electronGeo, electronMat);
  scene.add(electrons);

  // Each electron: start point, end point, progress (0-1), speed
  interface Electron { sx: number; sy: number; sz: number; ex: number; ey: number; ez: number; t: number; speed: number; }
  const activeElectrons: Electron[] = [];
  let electronSpawnRate = 0;
  let targetElectronRate = 0;
  let lastElectronSpawn = 0; // timestamp of last spawn

  // Store active connections for electron spawning
  let activeConnections: { x1: number; y1: number; z1: number; x2: number; y2: number; z2: number }[] = [];

  // ── HUD — arcs, a dial and a voice ring around the cloud, facing the camera ──
  const hud = new THREE.Group();
  scene.add(hud);
  const hudMat = new THREE.LineBasicMaterial({
    color: 0x4ca8e8, transparent: true, opacity: 0.3,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });

  // Unit-radius arcs; each span is [start, length] in turns.
  function arcs(spans: [number, number][], radius = 1): THREE.LineSegments {
    const v: number[] = [];
    for (const [start, len] of spans) {
      const steps = Math.max(2, Math.round(len * 160));
      for (let i = 0; i < steps; i++) {
        for (const k of [i, i + 1]) {
          const ang = (start + (len * k) / steps) * Math.PI * 2;
          v.push(Math.cos(ang) * radius, Math.sin(ang) * radius, 0);
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(v, 3));
    const l = new THREE.LineSegments(g, hudMat);
    hud.add(l);
    return l;
  }

  const ringA = arcs([[0, 0.22], [0.33, 0.22], [0.66, 0.22]], 1.14);
  const ringB = arcs([[0.05, 0.38], [0.55, 0.38]], 1.24);
  const ringC = arcs([[0, 0.08], [0.25, 0.08], [0.5, 0.08], [0.75, 0.08]], 1.27);

  // Dial: 72 ticks, every sixth one longer.
  const tickV: number[] = [];
  for (let i = 0; i < 72; i++) {
    const ang = (i / 72) * Math.PI * 2, c = Math.cos(ang), s = Math.sin(ang);
    const outer = i % 6 === 0 ? 1.4 : 1.36;
    tickV.push(c * 1.33, s * 1.33, 0, c * outer, s * outer, 0);
  }
  const tickGeo = new THREE.BufferGeometry();
  tickGeo.setAttribute("position", new THREE.Float32BufferAttribute(tickV, 3));
  const ticks = new THREE.LineSegments(tickGeo, hudMat);
  hud.add(ticks);

  // Voice ring: his own speech, drawn as a circular spectrum.
  const WAVE_N = 128;
  const wave = new Float32Array(WAVE_N);
  const wavePos = new Float32Array(WAVE_N * 3);
  const waveGeo = new THREE.BufferGeometry();
  waveGeo.setAttribute("position", new THREE.BufferAttribute(wavePos, 3));
  const waveMat = hudMat.clone();
  hud.add(new THREE.LineLoop(waveGeo, waveMat));

  // Gyroscope: three full rings tumbling in 3D, each carrying one bright satellite.
  const satMat = new THREE.PointsMaterial({
    color: 0xffffff, size: 0.9, transparent: true, opacity: 0.95,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const gyros = [1.46, 1.54, 1.62].map((r) => {
    const ring = arcs([[0, 1]], r);
    const satGeo = new THREE.BufferGeometry();
    satGeo.setAttribute("position", new THREE.Float32BufferAttribute([r, 0, 0], 3));
    ring.add(new THREE.Points(satGeo, satMat));
    const pivot = new THREE.Group();
    pivot.add(ring);
    hud.add(pivot);
    return { pivot, ring, angle: Math.random() * 6 };
  });

  // Radial spectrum: bars standing out from the dial, driven by his voice.
  const BAR_N = 96;
  const barLevel = new Float32Array(BAR_N);
  const barPos = new Float32Array(BAR_N * 6);
  const barGeo = new THREE.BufferGeometry();
  barGeo.setAttribute("position", new THREE.BufferAttribute(barPos, 3));
  hud.add(new THREE.LineSegments(barGeo, waveMat));

  // Core glow
  const glowCanvas = document.createElement("canvas");
  glowCanvas.width = glowCanvas.height = 128;
  const gctx = glowCanvas.getContext("2d")!;
  const grad = gctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.35, "rgba(255,255,255,0.25)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  gctx.fillStyle = grad;
  gctx.fillRect(0, 0, 128, 128);
  const glowMat = new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(glowCanvas), transparent: true, opacity: 0.15,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const glow = new THREE.Sprite(glowMat);
  scene.add(glow);

  // One colour per state, so a glance says what he is doing.
  const PALETTE: Record<OrbState, THREE.Color> = {
    idle: new THREE.Color(0x3d8bff),
    listening: new THREE.Color(0x22e3ff),
    thinking: new THREE.Color(0xa07bff),
    speaking: new THREE.Color(0xffb347),
    compacting: new THREE.Color(0x3a5f8a),
  };
  const HUD_OPACITY: Record<OrbState, number> = {
    idle: 0.22, listening: 0.4, thinking: 0.55, speaking: 0.55, compacting: 0.08,
  };
  let hudRadius = 22;

  // ── State ──
  let state: OrbState = "idle";
  let targetRadius = 25, currentRadius = 25;
  let targetSpeed = 0.3, currentSpeed = 0.3;
  let targetBright = 0.6, currentBright = 0.6;
  let targetSize = 0.4, currentSize = 0.4;
  let lineAmount = 0, targetLineAmount = 0;
  let lineDistance = 8;

  // Transition tumble
  let spinX = 0, spinY = 0, spinZ = 0;
  let transitionEnergy = 0;
  let lastState: OrbState = "idle";

  // Depth Z
  let cloudZ = 0, cloudZVel = 0;

  // ── Audio ──
  let analyser: AnalyserNode | null = null;
  let freqData = new Uint8Array(64);
  let bass = 0, mid = 0;

  const clock = new THREE.Clock();

  let paused = false;        // muted: stop redrawing 60 times a second

  function animate() {
    if (destroyed || paused) return;
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();

    switch (state) {
      case "idle":
        targetRadius = 28; targetSpeed = 0.2; targetBright = 0.5; targetSize = 0.35;
        targetLineAmount = 0.15; targetElectronRate = 0; break;
      case "listening":
        targetRadius = 22; targetSpeed = 0.3; targetBright = 0.65; targetSize = 0.4;
        targetLineAmount = 0.4; targetElectronRate = 0; break;
      case "thinking":
        targetRadius = 16; targetSpeed = 0.5; targetBright = 0.7; targetSize = 0.3;
        targetLineAmount = 1.0; targetElectronRate = 0.015; break;
      case "speaking":
        targetRadius = 18; targetSpeed = 0.2; targetBright = 0.7; targetSize = 0.4;
        targetLineAmount = 0.8; targetElectronRate = 0; break;
      case "compacting":
        // Smaller than idle, slower than anything, half the brightness, no
        // connecting lines: a held breath, not a working mind.
        targetRadius = 12; targetSpeed = 0.08; targetBright = 0.28; targetSize = 0.28;
        targetLineAmount = 0.0; targetElectronRate = 0; break;
    }

    currentRadius += (targetRadius - currentRadius) * 0.02;
    currentSpeed += (targetSpeed - currentSpeed) * 0.02;
    currentBright += (targetBright - currentBright) * 0.02;
    currentSize += (targetSize - currentSize) * 0.02;
    lineAmount += (targetLineAmount - lineAmount) * 0.02;
    electronSpawnRate += (targetElectronRate - electronSpawnRate) * 0.02;

    // Transition energy
    if (state !== lastState) { transitionEnergy = 1.0; lastState = state; }
    transitionEnergy *= 0.985;
    if (transitionEnergy > 0.05) {
      spinX += transitionEnergy * 0.012 * Math.sin(t * 1.7);
      spinY += transitionEnergy * 0.015;
      spinZ += transitionEnergy * 0.008 * Math.cos(t * 1.3);
    }

    // Audio
    bass = 0; mid = 0;
    if (analyser) {
      analyser.getByteFrequencyData(freqData);
      let bSum = 0, mSum = 0;
      for (let i = 0; i < 8; i++) bSum += freqData[i];
      for (let i = 8; i < 24; i++) mSum += freqData[i];
      bass = bSum / (8 * 255); mid = mSum / (16 * 255);
    }

    // Depth Z breathing
    let zTarget = Math.sin(t * 0.12) * 8;
    if (state === "thinking") zTarget = Math.sin(t * 0.3) * 15 + Math.sin(t * 0.9) * 6;
    else if (state === "speaking") zTarget = Math.sin(t * 0.15) * 6 - bass * 10;
    else if (state === "compacting") zTarget = -20 + Math.sin(t * 0.08) * 3;   // withdrawn, barely moving
    cloudZVel += (zTarget - cloudZ) * 0.008;
    cloudZVel *= 0.94;
    cloudZ += cloudZVel;

    points.rotation.x = spinX; points.rotation.y = spinY; points.rotation.z = spinZ;
    points.position.z = cloudZ;
    lines.rotation.x = spinX; lines.rotation.y = spinY; lines.rotation.z = spinZ;
    lines.position.z = cloudZ;

    // ── Update particles ──
    const p = geo.getAttribute("position") as THREE.BufferAttribute;
    const a = p.array as Float32Array;

    for (let i = 0; i < N; i++) {
      const i3 = i * 3;
      let x = a[i3], y = a[i3 + 1], z = a[i3 + 2];
      const px = phase[i];

      vel[i3] += Math.sin(t * 0.05 + px) * 0.001 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.06 + px * 1.3) * 0.001 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.055 + px * 0.7) * 0.001 * currentSpeed;
      vel[i3] += Math.sin(t * 0.02 + px * 2.1 + y * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.025 + px * 1.7 + z * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.022 + px * 0.9 + x * 0.1) * 0.0008 * currentSpeed;

      const dist = Math.sqrt(x * x + y * y + z * z) || 0.01;
      // The steady inward pull stops short of the centre: applied all the way in,
      // it beat the drift whenever he sat idle (muted) and the whole cloud
      // collapsed into one bright knot.
      const pull = Math.max(0, dist - currentRadius) * 0.002 + (dist > currentRadius * 0.75 ? 0.0003 : 0);
      vel[i3] -= (x / dist) * pull;
      vel[i3 + 1] -= (y / dist) * pull;
      vel[i3 + 2] -= (z / dist) * pull;

      if (bass > 0.05) {
        vel[i3] += (x / dist) * bass * 0.02;
        vel[i3 + 1] += (y / dist) * bass * 0.02;
        vel[i3 + 2] += (z / dist) * bass * 0.02;
      }
      if (state === "speaking" && mid > 0.1) {
        const pulse = Math.sin(t * 8 + px);
        vel[i3] += (x / dist) * mid * 0.012 * pulse;
        vel[i3 + 1] += (y / dist) * mid * 0.012 * pulse;
      }

      vel[i3] *= 0.992; vel[i3 + 1] *= 0.992; vel[i3 + 2] *= 0.992;
      a[i3] += vel[i3]; a[i3 + 1] += vel[i3 + 1]; a[i3 + 2] += vel[i3 + 2];
    }
    p.needsUpdate = true;

    // ── Update lines ──
    if (lineAmount > 0.01) {
      const lp = lineGeo.getAttribute("position") as THREE.BufferAttribute;
      const la = lp.array as Float32Array;
      let lineCount = 0;
      const maxDist = lineDistance * (1 + bass * 0.5);
      const maxDistSq = maxDist * maxDist;
      const step = Math.max(1, Math.floor(N / 600));

      for (let i = 0; i < N && lineCount < MAX_LINES; i += step) {
        const i3 = i * 3;
        const x1 = a[i3], y1 = a[i3 + 1], z1 = a[i3 + 2];
        for (let j = i + step; j < N && lineCount < MAX_LINES; j += step) {
          const j3 = j * 3;
          const dx = a[j3] - x1, dy = a[j3 + 1] - y1, dz = a[j3 + 2] - z1;
          if (dx * dx + dy * dy + dz * dz < maxDistSq) {
            const idx = lineCount * 6;
            la[idx] = x1; la[idx+1] = y1; la[idx+2] = z1;
            la[idx+3] = a[j3]; la[idx+4] = a[j3+1]; la[idx+5] = a[j3+2];
            lineCount++;
          }
        }
      }
      lineGeo.setDrawRange(0, lineCount * 2);
      lp.needsUpdate = true;
      lineMat.opacity = lineAmount * 0.12;

      // Store connections for electron spawning
      activeConnections = [];
      for (let c = 0; c < Math.min(lineCount, 500); c++) {
        const ci = c * 6;
        activeConnections.push({
          x1: la[ci], y1: la[ci+1], z1: la[ci+2],
          x2: la[ci+3], y2: la[ci+4], z2: la[ci+5],
        });
      }
    } else {
      lineGeo.setDrawRange(0, 0);
      activeConnections = [];
    }

    // ── Update electrons — only during thinking ──
    // One fires off every ~1 second, max 3 alive, takes 2-4s to travel
    if (activeConnections.length > 0 && electronSpawnRate > 0.005) {
      if (activeElectrons.length < 3 && (t - lastElectronSpawn) > 1.0) {
        const conn = activeConnections[Math.floor(Math.random() * activeConnections.length)];
        // speed: 1/fps * speed = progress per frame. At 60fps, speed 0.005 = 200 frames = 3.3s
        activeElectrons.push({
          sx: conn.x1, sy: conn.y1, sz: conn.z1,
          ex: conn.x2, ey: conn.y2, ez: conn.z2,
          t: 0,
          speed: 0.003 + Math.random() * 0.003, // 2-4 seconds to travel
        });
        lastElectronSpawn = t;
      }
    }

    // Update electron positions
    const ep = electronGeo.getAttribute("position") as THREE.BufferAttribute;
    const ea = ep.array as Float32Array;
    let aliveCount = 0;

    for (let e = activeElectrons.length - 1; e >= 0; e--) {
      const el = activeElectrons[e];
      el.t += el.speed;
      if (el.t >= 1) {
        activeElectrons.splice(e, 1);
        continue;
      }
      const ei = aliveCount * 3;
      ea[ei] = el.sx + (el.ex - el.sx) * el.t;
      ea[ei + 1] = el.sy + (el.ey - el.sy) * el.t;
      ea[ei + 2] = el.sz + (el.ez - el.sz) * el.t;
      aliveCount++;
    }

    electronGeo.setDrawRange(0, aliveCount);
    ep.needsUpdate = true;

    // Electrons follow the same rotation/position as the main group
    electrons.rotation.x = spinX; electrons.rotation.y = spinY; electrons.rotation.z = spinZ;
    electrons.position.z = cloudZ;

    mat.opacity = currentBright + bass * 0.08;
    mat.size = currentSize + bass * 0.05;

    mat.color.lerp(PALETTE[state], 0.04);
    lineMat.color.copy(mat.color);
    hudMat.color.copy(mat.color);
    waveMat.color.copy(mat.color);
    glowMat.color.copy(mat.color);

    // ── HUD ──
    hudRadius += (currentRadius * 0.74 - hudRadius) * 0.03;
    hud.scale.setScalar(hudRadius * (1 + bass * 0.06));
    hud.position.z = cloudZ * 0.3;
    const spin = currentSpeed / 0.2 + transitionEnergy * 4;
    ringA.rotation.z += 0.004 * spin;
    ringB.rotation.z -= 0.0025 * spin;
    ringC.rotation.z += 0.007 * spin;
    ticks.rotation.z -= 0.0006 * spin;
    hudMat.opacity += (HUD_OPACITY[state] - hudMat.opacity) * 0.04;
    waveMat.opacity = Math.min(1, hudMat.opacity * 1.6 + mid * 0.5);

    const bins = Math.min(freqData.length, 48);
    for (let i = 0; i < WAVE_N; i++) {
      const k = i < WAVE_N / 2 ? i : WAVE_N - 1 - i;          // mirrored: left matches right
      const ang = (i / WAVE_N) * Math.PI * 2 + Math.PI / 2;
      const level = analyser ? freqData[Math.floor((k / (WAVE_N / 2)) * bins)] / 255 : 0;
      const target = level * 0.3 + Math.sin(ang * 6 + t * 1.5) * 0.008;
      wave[i] += (target - wave[i]) * 0.35;
      const r = 1.04 + wave[i];
      wavePos[i * 3] = Math.cos(ang) * r;
      wavePos[i * 3 + 1] = Math.sin(ang) * r;
    }
    waveGeo.attributes.position.needsUpdate = true;

    gyros.forEach((g, i) => {
      g.angle += 0.0035 * (i + 1) * spin;
      g.pivot.rotation.set(1.0 + Math.sin(t * 0.13 + i * 2.1) * 0.45, g.angle, i * 1.1);
      g.ring.rotation.z += 0.012 * (i % 2 ? -1 : 1);
    });

    for (let i = 0; i < BAR_N; i++) {
      const k = i < BAR_N / 2 ? i : BAR_N - 1 - i;
      const ang = (i / BAR_N) * Math.PI * 2 + Math.PI / 2;
      const level = analyser ? freqData[Math.floor((k / (BAR_N / 2)) * bins)] / 255 : 0;
      barLevel[i] += (level - barLevel[i]) * 0.3;
      const c = Math.cos(ang), s = Math.sin(ang), r0 = 1.42, r1 = r0 + 0.015 + barLevel[i] * 0.5;
      barPos.set([c * r0, s * r0, 0, c * r1, s * r1, 0], i * 6);
    }
    barGeo.attributes.position.needsUpdate = true;

    glow.position.z = cloudZ;
    glow.scale.setScalar(currentRadius * 2.6 * (1 + bass * 0.5));
    glowMat.opacity = 0.1 + currentBright * 0.12 + bass * 0.3;

    camera.position.x = Math.sin(t * 0.02) * 5;
    camera.position.y = Math.cos(t * 0.03) * 3;
    camera.lookAt(0, 0, cloudZ * 0.2);

    renderer.render(scene, camera);
  }

  function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }

  window.addEventListener("resize", onResize);
  animate();

  return {
    setState(s: OrbState) { state = s; },
    setPalette(colours: Record<OrbState, string>) {
      for (const k of Object.keys(PALETTE) as OrbState[]) PALETTE[k].set(colours[k]);
    },
    setPaused(p: boolean) {
      const was = paused;
      paused = p;
      if (was && !p) animate();
    },
    setAnalyser(a: AnalyserNode | null) {
      analyser = a;
      if (a) freqData = new Uint8Array(a.frequencyBinCount);
    },
    destroy() {
      destroyed = true;
      window.removeEventListener("resize", onResize);
      renderer.dispose();
    },
  };
}
