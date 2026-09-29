/* 소개 페이지 3D 정거장 (plans/124 · D-277)
 *
 * 형상은 전부 코드로 만든다(모델 파일 0). 각 정거장은 { group, update(ctx), setQuality?(q) }를 돌려준다.
 * ctx = { state, t, dt, kf } — state 는 scroll.js 가 만든 장면 상태다(읽기만 한다).
 * 상태 인코딩(과대 표현 금지 · D-277 ⑥): 구현 = 실선 발광 · MVP = 반발광 · 대기·계획 = 점선·와이어프레임.
 */
import * as THREE from 'three';

export const TAU = Math.PI * 2;
export const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
export const C = { cyan: 0x5ce1ff, kb: 0xfcaf16, red: 0xff5a6a, mint: 0x52f2b8, violet: 0x9d8bff, muted: 0x9aa7c7, text: 0xeaf0ff };
export const hdr = (hex, k) => new THREE.Color(hex).multiplyScalar(k);
const css = (hex) => `#${new THREE.Color(hex).getHexString()}`;
const FONT = '"Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif';
const MONO = '"D2Coding", ui-monospace, Menlo, Consolas, monospace';

/* ───────── 공용 도구 ───────── */
export function canvasTex(c) {
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

let GLOW = null;
export function glow() {
  if (GLOW) return GLOW;
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, 'rgba(255,255,255,1)');
  gr.addColorStop(0.2, 'rgba(255,255,255,.5)');
  gr.addColorStop(0.55, 'rgba(255,255,255,.08)');
  gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr;
  g.fillRect(0, 0, 128, 128);
  GLOW = canvasTex(c);
  return GLOW;
}

function rr(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

function sprite(color, k, scale, opacity = 1) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glow(), color: hdr(color, k), blending: THREE.AdditiveBlending,
    depthWrite: false, transparent: true, opacity, toneMapped: false,
  }));
  s.scale.setScalar(scale);
  return s;
}

/** 알약 모양 글자 라벨. sub 가 있으면 두 줄. dashed = 대기·계획 상태 */
export function label(text, color, size, { sub = '', dashed = false } = {}) {
  const c = document.createElement('canvas');
  const g = c.getContext('2d');
  const main = `600 44px ${FONT}`;
  const small = `400 30px ${MONO}`;
  g.font = main;
  let w = g.measureText(text).width;
  if (sub) { g.font = small; w = Math.max(w, g.measureText(sub).width); }
  w = Math.ceil(w) + 68;
  const h = sub ? 132 : 84;
  c.width = w;
  c.height = h;
  rr(g, 3, 3, w - 6, h - 6, sub ? 32 : (h - 6) / 2);
  g.fillStyle = 'rgba(6,10,22,.84)';
  g.fill();
  g.lineWidth = 3;
  g.strokeStyle = css(color);
  if (dashed) g.setLineDash([12, 9]);
  g.stroke();
  g.setLineDash([]);
  g.textBaseline = 'middle';
  g.font = main;
  g.fillStyle = '#eaf0ff';
  g.fillText(text, 34, sub ? 46 : h / 2 + 2);
  if (sub) { g.font = small; g.fillStyle = css(color); g.fillText(sub, 34, 96); }
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: canvasTex(c), transparent: true, depthWrite: false, toneMapped: false }));
  s.scale.set((size * w) / 84, (size * h) / 84, 1);
  s.renderOrder = 10;
  return s;
}

function lineMat(color, k, opacity, dashed = false) {
  const o = { color: hdr(color, k), transparent: true, opacity, depthWrite: false, toneMapped: false };
  return dashed ? new THREE.LineDashedMaterial({ ...o, dashSize: 0.22, gapSize: 0.16 }) : new THREE.LineBasicMaterial(o);
}

function segments(flat, mat) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(flat, 3));
  const l = new THREE.LineSegments(geo, mat);
  if (mat.isLineDashedMaterial) l.computeLineDistances();
  return l;
}

function pointCloud(positions, colors, size, opacity = 1) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return new THREE.Points(geo, new THREE.PointsMaterial({
    size, map: glow(), vertexColors: true, transparent: true, opacity,
    depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false,
  }));
}

function basic(color, k, opacity = 1) {
  return new THREE.MeshBasicMaterial({ color: hdr(color, k), transparent: opacity < 1, opacity, toneMapped: false, depthWrite: opacity >= 1 });
}

/** 결정적 의사 난수 — 새로 고침마다 모양이 바뀌지 않게 */
function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

/** 상태 → 발광 배수·점선 여부 */
function look(status) {
  if (status === 'done' || status === 'partial') return { k: 2.6, dashed: false, solid: true };
  if (status === 'mvp') return { k: 1.7, dashed: false, solid: true };
  return { k: 1.1, dashed: true, solid: false };
}

/* ───────── A · 관제실 (S1 도입 · S2 알람 비) ───────── */
export function stationA({ names, q }) {
  const g = new THREE.Group();
  const floor = new THREE.Mesh(new THREE.CircleGeometry(16, 72), new THREE.MeshStandardMaterial({ color: 0x0b1122, roughness: 0.45, metalness: 0.55 }));
  floor.rotation.x = -Math.PI / 2;
  g.add(floor);
  const rings = [[1.5, 0.6, C.kb], [2.6, 0.45, C.cyan], [4.0, 0.25, C.cyan], [5.8, 0.14, C.cyan], [8.2, 0.08, C.cyan]].map(([r, o, col]) => {
    const m = new THREE.Mesh(new THREE.RingGeometry(r, r + 0.022, 144), basic(col, 1.6, o));
    m.rotation.x = -Math.PI / 2;
    m.position.y = 0.006;
    m.userData.o = o;
    g.add(m);
    return m;
  });
  { // 바깥 눈금 — 30° 마다 길게
    const pts = [];
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * TAU;
      const l = i % 6 === 0 ? 0.36 : 0.14;
      pts.push(Math.sin(a) * 2.66, 0.008, Math.cos(a) * 2.66, Math.sin(a) * (2.66 + l), 0.008, Math.cos(a) * (2.66 + l));
    }
    g.add(segments(pts, lineMat(C.cyan, 1.4, 0.45)));
  }

  // 운영자(사람) — 의자에 앉아 -z 방향 모니터를 본다
  const HEAD = new THREE.Vector3(0, 1.62, 0.72);
  const furn = new THREE.MeshStandardMaterial({ color: 0x1a2236, roughness: 0.5, metalness: 0.35 });
  const skin = new THREE.MeshStandardMaterial({ color: 0xe4d2c0, roughness: 0.55 });
  const cloth = new THREE.MeshStandardMaterial({ color: 0x2c3858, roughness: 0.7 });
  const box = (w, h, d, x, y, z, m = furn) => { const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); b.position.set(x, y, z); g.add(b); return b; };
  const limb = (r, a, b, m) => {
    const va = new THREE.Vector3(...a);
    const vb = new THREE.Vector3(...b);
    const d = vb.clone().sub(va);
    const mesh = new THREE.Mesh(new THREE.CapsuleGeometry(r, Math.max(0.01, d.length() - r * 2), 6, 12), m);
    mesh.position.copy(va).add(vb).multiplyScalar(0.5);
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize());
    g.add(mesh);
  };
  box(0.56, 0.06, 0.56, 0, 0.58, 0.88);
  box(0.56, 0.7, 0.06, 0, 0.97, 1.16);
  box(0.06, 0.56, 0.06, 0, 0.29, 0.88);
  limb(0.24, [0, 0.86, 0.9], [0, 1.4, 0.8], cloth);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.18, 32, 24), skin);
  head.position.copy(HEAD);
  g.add(head);
  limb(0.07, [0, 1.46, 0.78], [0, 1.56, 0.75], skin);
  for (const s of [-1, 1]) {
    limb(0.08, [s * 0.28, 1.34, 0.82], [s * 0.32, 1.06, 0.55], cloth);
    limb(0.07, [s * 0.32, 1.06, 0.55], [s * 0.22, 1.03, -0.1], cloth);
    limb(0.1, [s * 0.13, 0.66, 0.88], [s * 0.14, 0.64, 0.42], cloth);
    limb(0.085, [s * 0.14, 0.64, 0.42], [s * 0.15, 0.08, 0.38], cloth);
  }

  // 곡면 관제 책상 + 모니터 3면
  {
    const sh = new THREE.Shape();
    const a0 = -1.05;
    const a1 = 1.05;
    sh.absarc(0, 0, 1.55, a0, a1, false);
    sh.absarc(0, 0, 0.92, a1, a0, true);
    const desk = new THREE.Mesh(new THREE.ExtrudeGeometry(sh, { depth: 0.06, bevelEnabled: false, curveSegments: 32 }), furn);
    desk.rotation.x = -Math.PI / 2;
    desk.rotation.z = Math.PI / 2; // 호의 가운데가 -z(모니터 쪽)를 향하게
    desk.position.set(HEAD.x, 0.95, HEAD.z);
    g.add(desk);
    for (const [x, z] of [[-1.05, -0.3], [1.05, -0.3], [0, -0.75]]) box(0.06, 0.95, 0.06, x, 0.48, z);
  }
  const screens = [-0.62, 0, 0.62].map((a, i) => {
    const R = 1.32;
    const pos = new THREE.Vector3(HEAD.x + Math.sin(a) * R, 1.58, HEAD.z - Math.cos(a) * R);
    const frame = new THREE.Mesh(new THREE.BoxGeometry(1.14, 0.7, 0.04), furn);
    frame.position.copy(pos);
    frame.rotation.y = -a;
    g.add(frame);
    const c = document.createElement('canvas');
    c.width = 384;
    c.height = 224;
    const tex = canvasTex(c);
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(1.06, 0.62), new THREE.MeshBasicMaterial({ map: tex, color: hdr(0xffffff, 1.45), toneMapped: false }));
    scr.position.copy(pos).add(new THREE.Vector3(Math.sin(-a) * 0.025, 0, Math.cos(-a) * 0.025));
    scr.rotation.y = -a;
    g.add(scr);
    return { c, g2: c.getContext('2d'), tex, kind: i };
  });
  const HOME = new THREE.Vector3(0, 1.58, -0.55);

  function drawScreen(s, t, flood, found) {
    const g2 = s.g2;
    const W = 384;
    const H = 224;
    g2.fillStyle = '#050a18';
    g2.fillRect(0, 0, W, H);
    g2.fillStyle = '#0d1730';
    g2.fillRect(0, 0, W, 30);
    g2.font = `400 15px ${MONO}`;
    g2.textBaseline = 'middle';
    g2.fillStyle = '#5ce1ff';
    if (s.kind === 0) {
      g2.fillText('ALARM STREAM', 12, 16);
      const rows = 9;
      const shift = Math.floor(t * (flood ? 9 : 3));
      for (let j = 0; j < rows; j++) {
        const n = j + shift;
        const sev = flood ? (n * 7) % 5 < 4 ? 0 : 1 : (n * 7) % 5 === 0 ? 0 : (n * 3) % 4 === 0 ? 1 : 2;
        g2.fillStyle = ['#ff5a6a', '#fcaf16', '#52f2b8'][sev];
        g2.fillRect(12, 42 + j * 20, 10, 10);
        g2.fillStyle = 'rgba(154,167,194,.55)';
        g2.fillRect(30, 44 + j * 20, 60 + ((n * 97) % 180), 6);
      }
    } else if (s.kind === 1) {
      g2.fillText('TOPOLOGY', 12, 16);
      const nodes = [[70, 70], [190, 60], [310, 80], [120, 150], [250, 160], [190, 110]];
      g2.strokeStyle = 'rgba(154,167,194,.45)';
      g2.lineWidth = 2;
      for (const [a, b] of [[5, 0], [5, 1], [5, 2], [5, 3], [5, 4], [0, 3], [2, 4]]) {
        g2.beginPath(); g2.moveTo(...nodes[a]); g2.lineTo(...nodes[b]); g2.stroke();
      }
      nodes.forEach(([x, y], k) => {
        const hot = k === 5 && (flood || found);
        g2.fillStyle = hot ? '#ff5a6a' : k === 3 && flood ? '#fcaf16' : '#5ce1ff';
        g2.beginPath(); g2.arc(x, y, hot ? 9 + Math.sin(t * 6) * 2 : 7, 0, TAU); g2.fill();
      });
      if (found) {
        g2.strokeStyle = '#5ce1ff';
        g2.lineWidth = 3;
        g2.beginPath(); g2.arc(190, 110, 18 + Math.sin(t * 4) * 2, 0, TAU); g2.stroke();
      }
    } else {
      g2.fillText('METRIC', 12, 16);
      g2.strokeStyle = 'rgba(252,175,22,.6)';
      g2.setLineDash([6, 6]);
      g2.beginPath(); g2.moveTo(10, 80); g2.lineTo(W - 10, 80); g2.stroke();
      g2.setLineDash([]);
      g2.strokeStyle = '#5ce1ff';
      g2.lineWidth = 2.5;
      g2.beginPath();
      for (let x = 0; x <= W - 20; x += 6) {
        const u = x / 40 + t * 1.6;
        let y = 150 + Math.sin(u) * 18 + Math.sin(u * 2.7) * 8;
        if (flood && (Math.floor(u) % 5 === 0)) y -= 70;
        if (x === 0) g2.moveTo(10 + x, y); else g2.lineTo(10 + x, y);
      }
      g2.stroke();
    }
    s.tex.needsUpdate = true;
  }

  // 에이전트 — 기능 칩 하나에 하나, 칩이 켜질 때 깨어나 궤도에 오른다
  const agentCol = [C.cyan, C.mint, C.violet];
  const agents = names.map((name, i) => {
    const col = agentCol[i % 3];
    const ag = new THREE.Group();
    ag.add(new THREE.Mesh(new THREE.IcosahedronGeometry(0.12, 1), basic(col, 3)));
    ag.add(sprite(col, 1.6, 0.85));
    const lab = label(name, col, 0.2);
    lab.position.y = 0.34;
    ag.add(lab);
    g.add(ag);
    const lg = new THREE.BufferGeometry();
    lg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
    const line = new THREE.Line(lg, lineMat(col, 1.5, 0));
    g.add(line);
    const pulse = sprite(col, 3, 0.32, 0);
    g.add(pulse);
    return {
      ag, line, pulse,
      r: 2.2 + (i % 3) * 0.7 + i * 0.05,
      h: 1.4 + ((i * 37) % 5) * 0.28,
      speed: (0.1 + (i % 4) * 0.035) * (i % 2 ? -1 : 1),
      phase: (i / names.length) * TAU,
    };
  });

  // 알람 비(S2) — 알람 피로
  const RAIN = 900;
  const rnd = rng(7);
  const rp = new Float32Array(RAIN * 3);
  const rc = new Float32Array(RAIN * 3);
  const rOff = new Float32Array(RAIN);
  const rSpd = new Float32Array(RAIN);
  const tc = new THREE.Color();
  for (let i = 0; i < RAIN; i++) {
    const a = rnd() * TAU;
    const r = 1.6 + rnd() * 9;
    rp[i * 3] = Math.sin(a) * r;
    rp[i * 3 + 2] = Math.cos(a) * r;
    rOff[i] = rnd();
    rSpd[i] = 0.7 + rnd() * 0.6;
    tc.copy(rnd() < 0.8 ? hdr(C.red, 1.5) : hdr(C.kb, 1.3));
    rc.set([tc.r, tc.g, tc.b], i * 3);
  }
  const rain = pointCloud(rp, rc, 0.24, 0);
  rain.frustumCulled = false; // 위치를 매 프레임 바꾸므로 초기 경계 구로 자르지 않는다
  g.add(rain);

  let spread = 1;
  let clock = 1;
  return {
    group: g,
    HEAD,
    setQuality(qq) { rain.geometry.setDrawRange(0, Math.round(RAIN * qq.particles)); },
    update({ state, t, dt, kf }) {
      const { active, p } = state;
      spread = lerp(spread, active === 1 ? 2.0 : 1, state.reduce ? 1 : 1 - Math.exp(-dt * 1.5));
      agents.forEach((a, i) => {
        const w = state.wake[i] || 0;
        const ang = a.phase + t * a.speed + p[0] * Math.PI * 0.8;
        const r = a.r * spread;
        const target = new THREE.Vector3(Math.sin(ang) * r, a.h + Math.sin(t * 1.4 + i) * 0.12, Math.cos(ang) * r);
        const e = 1 - Math.pow(1 - w, 3);
        a.ag.position.copy(HOME).lerp(target, e);
        a.ag.scale.setScalar(0.001 + w);
        const pa = a.line.geometry.attributes.position;
        pa.setXYZ(0, HEAD.x, HEAD.y, HEAD.z);
        pa.setXYZ(1, a.ag.position.x, a.ag.position.y, a.ag.position.z);
        pa.needsUpdate = true;
        a.line.material.opacity = w * (active === 1 ? 0.16 : 0.5);
        const u = (t * 0.55 + i * 0.137) % 1;
        a.pulse.position.lerpVectors(HEAD, a.ag.position, u);
        a.pulse.material.opacity = w > 0.95 ? Math.sin(u * Math.PI) : 0;
      });

      const flood = active === 1;
      const found = active === 0 && p[0] > 0.7;
      clock += dt;
      if (clock > 0.2 || state.reduce) {
        clock = 0;
        screens.forEach((s) => drawScreen(s, t, flood, found));
      }
      rings.forEach((m) => { m.material.opacity = m.userData.o * (active === 8 ? 1.8 : 1); });

      const amt = active === 1 ? smooth(0, 0.08, p[1]) * (1 - smooth(0.64, 0.72, p[1])) : 0;
      rain.material.opacity = lerp(rain.material.opacity, amt, kf);
      rain.visible = rain.material.opacity > 0.01;
      if (rain.visible) {
        const pos = rain.geometry.attributes.position;
        for (let i = 0; i < RAIN; i++) pos.array[i * 3 + 1] = 14 - ((t * 2.6 * rSpd[i] + rOff[i] * 14) % 14);
        pos.needsUpdate = true;
      }
    },
  };
}

/* ───────── B · 장애 대응 5단계 고리 (S3) ───────── */
export function stationB({ steps, q }) {
  const g = new THREE.Group();
  const ring = new THREE.Group();
  ring.rotation.x = 0.28;
  g.add(ring);
  const R = 4.4;
  const rnd = rng(11);
  const hubs = steps.map((s, k) => {
    const a = -Math.PI / 2 + (k / steps.length) * TAU;
    const pos = new THREE.Vector3(Math.cos(a) * R, 0, Math.sin(a) * R);
    const lk = look(s.status);
    const hub = new THREE.Group();
    hub.position.copy(pos);
    if (lk.solid) hub.add(new THREE.Mesh(new THREE.SphereGeometry(0.34, 24, 16), basic(s.color, lk.k)));
    if (!lk.solid || s.status === 'mvp') {
      hub.add(segments(Array.from(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.5, 1)).attributes.position.array), lineMat(s.color, 1.6, 0.9)));
    }
    hub.add(sprite(s.color, lk.solid ? 1.8 : 0.9, lk.solid ? 2.4 : 1.3));
    ring.add(hub);
    const lab = label(s.name, s.color, 0.46, { sub: s.badge, dashed: lk.dashed });
    lab.position.copy(pos).multiplyScalar(1.42).add(new THREE.Vector3(0, 0.9, 0));
    ring.add(lab);

    // 노드 성운 — 단계 주변의 세부 신호
    const n = lk.solid ? 34 : 18;
    const pts = [];
    for (let i = 0; i < n; i++) {
      const v = new THREE.Vector3(rnd() - 0.5, rnd() - 0.5, rnd() - 0.5).normalize().multiplyScalar(0.7 + rnd() * 1.1);
      pts.push(v.add(pos));
    }
    const np = new Float32Array(n * 3);
    const nc = new Float32Array(n * 3);
    const col = hdr(s.color, lk.solid ? 1.7 : 0.9);
    pts.forEach((v, i) => { np.set([v.x, v.y, v.z], i * 3); nc.set([col.r, col.g, col.b], i * 3); });
    const cloud = pointCloud(np, nc, 0.34);
    ring.add(cloud);
    const flat = [];
    pts.forEach((v, i) => {
      if (i % 3 !== 2) flat.push(pos.x, pos.y, pos.z, v.x, v.y, v.z);
      const u = pts[(i + 1) % n];
      flat.push(v.x, v.y, v.z, u.x, u.y, u.z);
    });
    const edges = segments(flat, lineMat(s.color, lk.solid ? 1.4 : 0.9, 0, lk.dashed));
    ring.add(edges);
    return { hub, lab, cloud, edges, n, nEdge: flat.length / 3, s: 0.4 };
  });

  // 순환 고리 — 선별 → 조사 대상 확정 → 현황 → 원인 → 조치 → 축적 → 선별 기준 개선
  const curve = new THREE.CatmullRomCurve3(hubs.map((h) => h.hub.position.clone()), true, 'catmullrom', 0.5);
  const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 300, 0.035, 6, true), basic(C.muted, 1.2, 0.5));
  ring.add(tube);
  const sparks = Array.from({ length: 18 }, () => { const sp = sprite(C.text, 2, 0.5, 0); ring.add(sp); return sp; });

  return {
    group: g,
    update({ state, t, dt, kf }) {
      const { active, p } = state;
      const build = active < 2 ? (active === 1 && p[1] > 0.72 ? 0.25 : 0) : active === 2 ? Math.max(0.25, smooth(0.02, 0.3, p[2])) : 1;
      const hk = state.step;
      hubs.forEach((h, k) => {
        const on = hk === k;
        const base = hk === -1 ? 0.45 : on ? 1 : 0.14;
        h.cloud.geometry.setDrawRange(0, Math.floor(h.n * build));
        h.edges.geometry.setDrawRange(0, Math.floor((h.nEdge * smooth(0.2, 1, build)) / 2) * 2);
        h.edges.material.opacity = lerp(h.edges.material.opacity, base * build, kf);
        h.s = lerp(h.s, (0.4 + 0.6 * build) * (on ? 1.6 : 1), kf);
        h.hub.scale.setScalar(h.s);
        h.lab.material.opacity = build * (hk === -1 || on ? 1 : 0.35);
      });
      tube.material.opacity = 0.5 * build;
      sparks.forEach((sp, k) => {
        curve.getPoint((t * 0.05 + k / sparks.length) % 1, sp.position);
        sp.material.opacity = build * 0.8;
      });
      if (!state.reduce) ring.rotation.y += dt * 0.05;
    },
  };
}

/* ───────── C · 알람 선별 깔때기 (S4) — 수량은 연출 예시 ───────── */
export function stationC({ dedupLabel, q }) {
  const g = new THREE.Group();
  const CH_X = [-4.5, -1.5, 1.5, 4.5];
  const TIER = [
    { name: '즉시 통보', c: C.red },
    { name: '티켓', c: C.kb },
    { name: '대시보드', c: C.cyan },
    { name: '억제', c: C.muted },
  ];
  // 병합·연쇄 단계 고리와 라벨
  const ring1 = new THREE.Mesh(new THREE.RingGeometry(4.6, 4.64, 160), basic(C.cyan, 1.8, 0.55));
  ring1.rotation.x = -Math.PI / 2;
  ring1.position.y = 6;
  g.add(ring1);
  const ring2 = new THREE.Mesh(new THREE.RingGeometry(3.7, 3.74, 160), basic(C.violet, 1.8, 0.55));
  ring2.rotation.x = -Math.PI / 2;
  ring2.position.y = 3;
  g.add(ring2);
  const l1 = label('중복 병합', C.cyan, 0.42, { sub: dedupLabel });
  l1.position.set(-6.4, 6.3, 0);
  g.add(l1);
  const l2 = label('토폴로지 연쇄', C.violet, 0.42, { sub: '상위 장애로 묶음' });
  l2.position.set(-5.6, 3.3, 0);
  g.add(l2);

  // 네 갈래 수로 + 도착지
  TIER.forEach((tr, k) => {
    const x = CH_X[k];
    g.add(segments([x, 2.6, 0, x, -1.4, 0], lineMat(tr.c, 1.8, 0.6)));
    const lab = label(tr.name, tr.c, 0.36);
    lab.position.set(x, -2.5, 0.6);
    g.add(lab);
  });
  const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.34, 24, 16), basic(C.red, 3));
  beacon.position.set(CH_X[0], -1.5, 0);
  g.add(beacon);
  const beaconHalo = sprite(C.red, 2, 2.2);
  beaconHalo.position.copy(beacon.position);
  g.add(beaconHalo);
  const ticket = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.5, 0.5), basic(C.kb, 1.2, 0.5));
  ticket.position.set(CH_X[1], -1.5, 0);
  g.add(ticket);
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(1.2, 0.7), basic(C.cyan, 1.1, 0.35));
  panel.position.set(CH_X[2], -1.4, 0);
  g.add(panel);
  // 억제 = 기록 원통 — 지우지 않고 쌓인다
  const jar = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 0.9, 1.8, 40, 1, true), basic(C.muted, 1.2, 0.14));
  jar.position.set(CH_X[3], -1.3, 0);
  g.add(jar);
  const jarRim = segments(Array.from(new THREE.EdgesGeometry(new THREE.CylinderGeometry(0.9, 0.9, 1.8, 40, 1), 20).attributes.position.array), lineMat(C.muted, 1.4, 0.6));
  jarRim.position.copy(jar.position);
  g.add(jarRim);
  const fill = new THREE.Mesh(new THREE.CylinderGeometry(0.84, 0.84, 1, 40), basic(C.muted, 1.1, 0.28));
  fill.position.set(CH_X[3], -2.2, 0);
  g.add(fill);
  const jarLab = label('기록 보존', C.muted, 0.3);
  jarLab.position.set(CH_X[3], 0.1, 1.0);
  g.add(jarLab);

  // 알람 입자 — 경로는 시간의 해석 함수(상태 없음)
  const rnd = rng(23);
  const G = 24;
  const groups = Array.from({ length: G }, (_, k) => {
    const a = k * 2.399963;
    const r = 3.2 * Math.sqrt((k + 0.5) / G);
    const w = rnd();
    return {
      x: Math.cos(a) * r, z: Math.sin(a) * r,
      tier: w < 0.1 ? 0 : w < 0.32 ? 1 : w < 0.62 ? 2 : 3,
      parent: k >= 15 ? k % 15 : -1, // 뒤 9개 묶음은 다른 묶음의 연쇄 자식
    };
  });
  const N = 560;
  const P = Array.from({ length: N }, (_, i) => {
    const a = rnd() * TAU;
    const r = Math.sqrt(rnd()) * 5.4;
    const gi = Math.floor(rnd() * G);
    return { sx: Math.cos(a) * r, sz: Math.sin(a) * r, o: rnd(), gi, sev3: i % 29 === 0, jx: (rnd() - 0.5) * 0.9, jz: (rnd() - 0.5) * 0.9 };
  });
  const pos = new Float32Array(N * 3);
  const col = new Float32Array(N * 3);
  const cloud = pointCloud(pos, col, 0.3);
  cloud.frustumCulled = false; // 위치를 매 프레임 바꾸므로 초기 경계 구로 자르지 않는다
  g.add(cloud);
  const ALARM = hdr(0xff8a6a, 1.3);
  const SEV3 = hdr(C.red, 2.6);
  const TC = TIER.map((tr) => hdr(tr.c, tr.c === C.muted ? 0.8 : 1.6));
  const tmp = new THREE.Color();

  return {
    group: g,
    setQuality(qq) { cloud.geometry.setDrawRange(0, Math.round(N * qq.particles)); },
    update({ state, t }) {
      const { active, p } = state;
      if (![2, 3, 4, 6].includes(active)) return;
      beacon.scale.setScalar(1 + Math.sin(t * 5) * 0.12);
      beaconHalo.material.opacity = 0.7 + Math.sin(t * 5) * 0.3;
      const level = active < 3 ? 0.1 : active > 3 ? 1 : 0.1 + 0.9 * smooth(0.55, 1, p[3]);
      fill.scale.y = Math.max(0.02, level * 1.5);
      fill.position.y = -2.2 + (level * 1.5) / 2;
      for (let i = 0; i < N; i++) {
        const a = P[i];
        const s = (t * 0.07 + a.o) % 1;
        const y = s < 0.3 ? lerp(10, 6, s / 0.3) : s < 0.55 ? lerp(6, 3, (s - 0.3) / 0.25) : s < 0.8 ? lerp(3, 0.3, (s - 0.55) / 0.25) : lerp(0.3, -1.6, (s - 0.8) / 0.2);
        let x;
        let z;
        let alpha = 1;
        if (a.sev3) { // 최고 심각도 — 어떤 억제도 거치지 않고 곧장 즉시 통보
          const m = smooth(0.1, 0.7, s);
          x = lerp(a.sx, CH_X[0], m);
          z = lerp(a.sz, 0, m);
          tmp.copy(SEV3);
          alpha = 1 - smooth(0.9, 1, s);
        } else {
          const gr = groups[a.gi];
          const m1 = smooth(0.04, 0.28, s);
          x = lerp(a.sx, gr.x, m1);
          z = lerp(a.sz, gr.z, m1);
          let tier = gr.tier;
          if (gr.parent >= 0) { // 연쇄 자식 — 부모 흐름에 흡수된다
            const pg = groups[gr.parent];
            const m2 = smooth(0.32, 0.5, s);
            x = lerp(x, pg.x, m2);
            z = lerp(z, pg.z, m2);
            tier = pg.tier;
            alpha = 1 - smooth(0.44, 0.54, s);
          }
          const m3 = smooth(0.56, 0.78, s);
          x = lerp(x, CH_X[tier] + a.jx, m3);
          z = lerp(z, a.jz, m3);
          tmp.copy(ALARM).lerp(TC[tier], m3);
          if (tier !== 3) alpha *= 1 - smooth(0.9, 1, s); // 억제는 사라지지 않고 기록 원통에 남는다
        }
        pos[i * 3] = x;
        pos[i * 3 + 1] = y;
        pos[i * 3 + 2] = z;
        col[i * 3] = tmp.r * alpha;
        col[i * 3 + 1] = tmp.g * alpha;
        col[i * 3 + 2] = tmp.b * alpha;
      }
      cloud.geometry.attributes.position.needsUpdate = true;
      cloud.geometry.attributes.color.needsUpdate = true;
    },
  };
}

/* ───────── D · 계층 타임라인 (S5) — 앱·DB 층은 관측 확대 전이라 점선 ───────── */
export function stationD() {
  const g = new THREE.Group();
  const LANES = [
    { name: '앱·WAS', y: 2.6, observed: false, sub: 'APM 연동 후' },
    { name: 'DB', y: 0, observed: false, sub: 'DPM 연동 후' },
    { name: '호스트', y: -2.6, observed: true, sub: '인프라 관제 · 관측 중' },
  ];
  const W = 13;
  const D = 2.6;
  LANES.forEach((ln) => {
    const c = ln.observed ? C.mint : C.muted;
    const slab = new THREE.Mesh(new THREE.PlaneGeometry(W, D), basic(c, 1, ln.observed ? 0.1 : 0.04));
    slab.rotation.x = -Math.PI / 2;
    slab.position.y = ln.y;
    g.add(slab);
    const h = W / 2;
    const d = D / 2;
    g.add(segments([-h, ln.y, -d, h, ln.y, -d, h, ln.y, -d, h, ln.y, d, h, ln.y, d, -h, ln.y, d, -h, ln.y, d, -h, ln.y, -d], lineMat(c, 1.5, ln.observed ? 0.8 : 0.5, !ln.observed)));
    const ticks = [];
    for (let x = -h + 1; x < h; x += 1) ticks.push(x, ln.y + 0.005, -d, x, ln.y + 0.005, d);
    g.add(segments(ticks, lineMat(c, 1, 0.08)));
    const lab = label(ln.name, c, 0.4, { sub: ln.sub, dashed: !ln.observed });
    lab.position.set(4.2, ln.y + 0.85, -d); // 층 위 오른쪽 — 왼쪽은 글자 패널 자리
    g.add(lab);
  });
  const axisLab = label('시간 →', C.muted, 0.3);
  axisLab.position.set(4.6, -3.9, 1.4);
  g.add(axisLab);

  // 증거 — 호스트 IO 급증 → DB 락 → 앱 지연 순서로 일어난다(연출 예시)
  const rnd = rng(31);
  const EV = [
    { x: -3.4, lane: 2, key: true, c: C.mint },
    { x: -0.7, lane: 1, key: true, c: C.muted },
    { x: 2.3, lane: 0, key: true, c: C.muted },
    ...Array.from({ length: 9 }, (_, i) => ({ x: -5.5 + i * 1.3 + rnd() * 0.4, lane: 2, key: false, c: C.cyan })),
  ].map((e) => {
    const ln = LANES[e.lane];
    const home = new THREE.Vector3(e.x, ln.y + 0.35, (rnd() - 0.5) * 1.2);
    const from = new THREE.Vector3((rnd() - 0.5) * 18, (rnd() - 0.5) * 10 + 4, (rnd() - 0.5) * 10);
    const node = new THREE.Group();
    if (ln.observed) node.add(new THREE.Mesh(new THREE.SphereGeometry(e.key ? 0.26 : 0.12, 20, 14), basic(e.c, e.key ? 2.6 : 1.4)));
    else node.add(segments(Array.from(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.3, 1)).attributes.position.array), lineMat(e.c, 1.6, 0.9)));
    const h = sprite(e.c, ln.observed ? 1.8 : 0.8, e.key ? 1.6 : 0.8, 0);
    node.add(h);
    g.add(node);
    return { ...e, home, from, node, halo: h };
  });

  // 거꾸로 짚어 가는 인과 화살표 — 관측 전 층으로 가는 선은 점선
  const arrows = [[0, 1], [1, 2]].map(([a, b]) => {
    const A = EV[a].home;
    const B = EV[b].home;
    const mid = A.clone().lerp(B, 0.5).add(new THREE.Vector3(0, 0.6, 1.2));
    const pts = new THREE.QuadraticBezierCurve3(A, mid, B).getPoints(40);
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const l = new THREE.Line(geo, lineMat(C.kb, 2, 0.9, true));
    l.computeLineDistances();
    g.add(l);
    return l;
  });
  const rootRing = new THREE.Mesh(new THREE.RingGeometry(0.5, 0.56, 64), basic(C.kb, 2.4, 0));
  rootRing.position.copy(EV[0].home);
  g.add(rootRing);
  const cursor = new THREE.Mesh(new THREE.PlaneGeometry(0.05, 8.4), basic(C.cyan, 2, 0));
  g.add(cursor);

  return {
    group: g,
    update({ state, t }) {
      const { active, p } = state;
      if (active < 3 || active > 6) return;
      const pp = active === 4 ? p[4] : active > 4 ? 1 : 0;
      const gather = smooth(0.0, 0.28, pp);
      const cx = lerp(-7, 7, smooth(0.32, 0.58, pp));
      cursor.position.set(cx, 0, 0.8);
      cursor.material.opacity = pp > 0.3 && pp < 0.62 ? 0.5 : 0;
      EV.forEach((e) => {
        e.node.position.copy(e.from).lerp(e.home, 1 - Math.pow(1 - gather, 3));
        const lit = pp >= 0.58 || cx > e.x ? 1 : 0.25;
        e.halo.material.opacity = lit * (e.key ? 1 : 0.6);
        e.node.scale.setScalar(0.6 + 0.4 * lit);
      });
      const ar = smooth(0.6, 0.8, pp);
      arrows.forEach((l, k) => l.geometry.setDrawRange(0, Math.floor(41 * smooth(k * 0.5, k * 0.5 + 0.5, ar))));
      rootRing.material.opacity = ar * (0.6 + Math.sin(t * 4) * 0.4);
    },
  };
}

/* ───────── E · 주요 기능 카드 링 (S6) ───────── */
function icon(g2, kind, x, y, s, color) {
  g2.save();
  g2.translate(x, y);
  g2.scale(s / 100, s / 100);
  g2.strokeStyle = color;
  g2.fillStyle = color;
  g2.lineWidth = 7;
  g2.lineCap = 'round';
  g2.lineJoin = 'round';
  g2.beginPath();
  if (kind === 'funnel') {
    g2.moveTo(8, 12); g2.lineTo(92, 12); g2.lineTo(58, 52); g2.lineTo(58, 86); g2.lineTo(42, 94); g2.lineTo(42, 52); g2.closePath();
  } else if (kind === 'search') {
    g2.arc(42, 42, 30, 0, TAU); g2.moveTo(64, 64); g2.lineTo(92, 92);
  } else if (kind === 'chart') {
    g2.moveTo(10, 90); g2.lineTo(90, 90); g2.moveTo(22, 78); g2.lineTo(22, 58); g2.moveTo(44, 78); g2.lineTo(44, 40); g2.moveTo(66, 78); g2.lineTo(66, 52); g2.moveTo(14, 36); g2.lineTo(40, 18); g2.lineTo(60, 30); g2.lineTo(88, 10);
  } else if (kind === 'chat') {
    g2.moveTo(14, 16); g2.lineTo(86, 16); g2.lineTo(86, 66); g2.lineTo(46, 66); g2.lineTo(26, 88); g2.lineTo(28, 66); g2.lineTo(14, 66); g2.closePath(); g2.moveTo(32, 36); g2.lineTo(68, 36); g2.moveTo(32, 50); g2.lineTo(58, 50);
  } else if (kind === 'doc') {
    g2.moveTo(20, 8); g2.lineTo(64, 8); g2.lineTo(82, 26); g2.lineTo(82, 92); g2.lineTo(20, 92); g2.closePath(); g2.moveTo(34, 42); g2.lineTo(68, 42); g2.moveTo(34, 58); g2.lineTo(68, 58); g2.moveTo(34, 74); g2.lineTo(56, 74);
  } else {
    g2.moveTo(50, 6); g2.lineTo(86, 20); g2.lineTo(82, 58); g2.quadraticCurveTo(76, 82, 50, 94); g2.quadraticCurveTo(24, 82, 18, 58); g2.lineTo(14, 20); g2.closePath(); g2.moveTo(34, 50); g2.lineTo(46, 62); g2.lineTo(68, 38);
  }
  g2.stroke();
  g2.restore();
}

function wrapText(g2, text, maxW, maxLines) {
  const out = [];
  let cur = '';
  for (const ch of text) { // 한글은 글자 단위로 끊는다
    if (g2.measureText(cur + ch).width > maxW && cur) { out.push(cur); cur = ch.trim(); } else cur += ch;
  }
  if (cur) out.push(cur);
  if (out.length > maxLines) { out.length = maxLines; out[maxLines - 1] = `${out[maxLines - 1].slice(0, -1)}…`; }
  return out;
}

function cardTexture(f, i, n) {
  const W = 900;
  const H = 560;
  const c = document.createElement('canvas');
  c.width = W;
  c.height = H;
  const g2 = c.getContext('2d');
  const done = f.status === 'done';
  const accent = done ? '#52f2b8' : '#fcaf16';
  rr(g2, 6, 6, W - 12, H - 12, 40);
  const bg = g2.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, 'rgba(22,31,60,.97)');
  bg.addColorStop(1, 'rgba(7,11,24,.97)');
  g2.fillStyle = bg;
  g2.fill();
  const st = g2.createLinearGradient(0, 0, W, H);
  st.addColorStop(0, '#5ce1ff');
  st.addColorStop(1, accent);
  g2.lineWidth = 4;
  g2.strokeStyle = st;
  g2.stroke();
  icon(g2, f.icon, 64, 60, 110, '#5ce1ff');
  const badge = done ? '구현 완료' : 'MVP';
  g2.font = `600 28px ${FONT}`;
  const bw = g2.measureText(badge).width + 44;
  rr(g2, W - 60 - bw, 70, bw, 52, 26);
  g2.fillStyle = done ? 'rgba(82,242,184,.14)' : 'rgba(252,175,22,.14)';
  g2.fill();
  g2.strokeStyle = accent;
  g2.lineWidth = 2;
  g2.stroke();
  g2.fillStyle = accent;
  g2.textBaseline = 'middle';
  g2.fillText(badge, W - 60 - bw + 22, 97);
  g2.textBaseline = 'alphabetic';
  g2.font = `500 32px ${FONT}`;
  g2.fillStyle = '#9aa7c7';
  g2.fillText(f.role, 64, 262);
  g2.font = `800 66px ${FONT}`;
  g2.fillStyle = '#ffffff';
  wrapText(g2, f.name, W - 128, 2).forEach((ln, k) => g2.fillText(ln, 62, 346 + k * 78));
  g2.font = `400 24px ${MONO}`;
  g2.fillStyle = 'rgba(154,167,199,.7)';
  g2.fillText('KB AIOPS', 64, H - 46);
  const idx = `${String(i + 1).padStart(2, '0')} / ${String(n).padStart(2, '0')}`;
  g2.fillText(idx, W - 64 - g2.measureText(idx).width, H - 46);
  return canvasTex(c);
}

export function stationE({ feats }) {
  const g = new THREE.Group();
  const N = feats.length;
  const CW = 2.6;
  const CH = CW / 1.607;
  const CR = Math.max(4.2, (N * (CW + 0.6)) / TAU);
  const ring = new THREE.Group();
  g.add(ring);
  const cards = feats.map((f, i) => {
    const a = (i / N) * TAU;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(CW, CH), new THREE.MeshBasicMaterial({ map: cardTexture(f, i, N), color: new THREE.Color(0.82, 0.82, 0.82), transparent: true, toneMapped: false, depthWrite: false })); // 흰 글자가 블룸 임계(0.9) 아래
    m.position.set(Math.sin(a) * CR, 0, Math.cos(a) * CR);
    m.rotation.y = a;
    ring.add(m);
    return m;
  });
  for (const [y, o] of [[-CH / 2 - 0.35, 0.5], [CH / 2 + 0.35, 0.22]]) {
    const t = new THREE.Mesh(new THREE.RingGeometry(CR - 0.015, CR + 0.015, 200), basic(C.cyan, 1.8, o));
    t.material.side = THREE.DoubleSide;
    t.rotation.x = -Math.PI / 2;
    t.position.y = y;
    g.add(t);
  }
  const fc = document.createElement('canvas');
  fc.width = 512;
  fc.height = 340;
  {
    const g2 = fc.getContext('2d');
    g2.shadowColor = '#5ce1ff';
    g2.shadowBlur = 40;
    g2.strokeStyle = '#5ce1ff';
    g2.lineWidth = 6;
    rr(g2, 50, 50, 412, 240, 30);
    g2.stroke();
    g2.stroke();
  }
  const frame = new THREE.Mesh(new THREE.PlaneGeometry(CW * 1.24, CH * 1.33), new THREE.MeshBasicMaterial({ map: canvasTex(fc), color: hdr(0xffffff, 1.6), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
  frame.position.set(0, 0, CR - 0.06);
  frame.scale.setScalar(1.14);
  g.add(frame);

  let ringF = 0;
  return {
    group: g,
    CR,
    update({ state, kf }) {
      ringF = state.reduce ? state.featF : lerp(ringF, state.featF, kf);
      ring.rotation.y = (-ringF / N) * TAU;
      cards.forEach((c, i) => {
        let rel = ((i - ringF) / N) * TAU;
        rel = Math.atan2(Math.sin(rel), Math.cos(rel));
        const focus = 1 - smooth(0, (TAU / N) * 0.85, Math.abs(rel));
        c.scale.setScalar(1 + 0.14 * focus);
        c.material.opacity = 0.16 + 0.84 * Math.max(focus, 0.5 * Math.max(0, Math.cos(rel)) ** 2);
      });
      frame.material.opacity = 0.9 * (1 - Math.min(1, Math.abs(ringF - Math.round(ringF)) * 3)) * (state.active === 5 ? 1 : 0.3);
    },
  };
}

/* ───────── F · 관측 소스 별자리 (S8) — 데이터가 흐르는 선은 연동 완료뿐 ───────── */
export function stationF({ sources }) {
  const g = new THREE.Group();
  const core = new THREE.Group();
  core.add(new THREE.Mesh(new THREE.IcosahedronGeometry(0.7, 1), basic(C.cyan, 2.2)));
  core.add(segments(Array.from(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.1, 1)).attributes.position.array), lineMat(C.cyan, 1.4, 0.5)));
  core.add(sprite(C.cyan, 1.1, 2.4));
  g.add(core);
  const coreLab = label('AIOps 에이전트', C.cyan, 0.42);
  coreLab.position.set(0, 1.9, 0);
  g.add(coreLab);
  const COL = { done: C.mint, wait: C.cyan, plan: C.muted };
  const R = 5.4;
  const stars = sources.map((s, k) => {
    const a = -Math.PI / 2 + (k / sources.length) * TAU;
    const pos = new THREE.Vector3(Math.cos(a) * R, Math.sin(a * 2) * 0.8, Math.sin(a) * R * 0.75);
    const c = COL[s.status] || C.muted;
    const done = s.status === 'done';
    const node = new THREE.Group();
    node.position.copy(pos);
    if (done) node.add(new THREE.Mesh(new THREE.SphereGeometry(0.3, 20, 14), basic(c, 2.6)));
    else node.add(segments(Array.from(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.34, 1)).attributes.position.array), lineMat(c, 1.4, s.status === 'wait' ? 0.9 : 0.55)));
    if (s.status === 'wait') node.add(new THREE.Mesh(new THREE.SphereGeometry(0.1, 12, 8), basic(c, 1.1)));
    const h = sprite(c, done ? 1.6 : 0.8, done ? 2.2 : 0.9, 0);
    node.add(h);
    g.add(node);
    const link = segments([0, 0, 0, pos.x, pos.y, pos.z], lineMat(c, 1.5, 0, s.status !== 'done'));
    g.add(link);
    const lab = label(s.name, c, 0.3, { sub: s.badge, dashed: !done });
    lab.position.copy(pos).add(new THREE.Vector3(0, 0.95, 0));
    g.add(lab);
    const pulse = s.status === 'done' ? sprite(c, 3, 0.4, 0) : null;
    if (pulse) g.add(pulse);
    return { node, h, link, lab, pos, pulse, status: s.status };
  });

  return {
    group: g,
    update({ state, t, dt, kf }) {
      const { active } = state;
      if (active < 6) return;
      stars.forEach((s, k) => {
        const on = k <= state.source ? 1 : 0.12;
        const base = s.status === 'done' ? 0.9 : s.status === 'wait' ? 0.45 : 0.25;
        s.link.material.opacity = lerp(s.link.material.opacity, on * base, kf);
        s.h.material.opacity = lerp(s.h.material.opacity, on * base, kf);
        s.lab.material.opacity = on;
        if (s.pulse) {
          const u = (t * 0.5) % 1;
          s.pulse.position.copy(s.pos).multiplyScalar(1 - u); // 소스 → 에이전트 방향으로 데이터가 흐른다
          s.pulse.material.opacity = on > 0.5 ? Math.sin(u * Math.PI) : 0;
        }
      });
      if (!state.reduce) core.rotation.y += dt * 0.3;
    },
  };
}
