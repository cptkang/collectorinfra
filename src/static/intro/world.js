/* 소개 페이지 3D 세계 (plans/124 · D-277)
 *
 * scroll.js 가 동적 import 한다. 장면 상태는 읽기만 하고 DOM 글자는 건드리지 않는다.
 * 정거장 A~F를 z축으로 늘어놓고, 카메라가 장면 진행률에 따라 정거장 사이를 난다.
 * 품질: 상(블룸) · 중(블룸 끔·입자 축소) · 정지(3D 없음). 실행 중 느리면 한 단계씩 내린다(올리지 않음).
 */
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { C, TAU, hdr, lerp, smooth, glow, stationA, stationB, stationC, stationD, stationE, stationF } from './stations.js';

const POS = {
  A: new THREE.Vector3(0, 0, 0),
  B: new THREE.Vector3(0, 3, -50),
  C: new THREE.Vector3(0, 1, -100),
  D: new THREE.Vector3(0, 2, -150),
  E: new THREE.Vector3(0, 1.5, -200),
  F: new THREE.Vector3(0, 4, -250),
};
const SLOW_FRAME_MS = 33; // 2초 평균이 이보다 길면 한 단계 내린다
const COLOR_KEYS = { cyan: C.cyan, kb: C.kb, red: C.red, mint: C.mint, violet: C.violet, muted: C.muted };

/** 단계·소스·칩 이름은 DOM 이 정본이다 — 3D는 읽어서 그린다 */
function readDom() {
  const colorOf = (li) => {
    const m = /--c:\s*var\(--([a-z]+)\)/.exec(li.getAttribute('style') || '');
    return COLOR_KEYS[m ? m[1] : 'cyan'] ?? C.cyan;
  };
  const badge = (li) => li.querySelector('.badge');
  const steps = [...document.querySelectorAll('#steps li')].map((li) => ({
    name: li.querySelector('b').textContent,
    status: badge(li).dataset.status,
    badge: badge(li).textContent,
    color: colorOf(li),
  }));
  const sources = [...document.querySelectorAll('#sources li')].map((li) => {
    const full = li.querySelector('b').textContent;
    return { name: full.includes(' · ') ? full.split(' · ').pop() : full, status: badge(li).dataset.status, badge: badge(li).textContent };
  });
  const names = [...document.querySelectorAll('#chips li')].map((li) => li.textContent);
  const dedup = document.querySelector('[data-fact="dedup_seconds"]');
  return { steps, sources, names, dedupLabel: dedup ? `${dedup.textContent}초 창` : '' };
}

export async function start(api) {
  const { state } = api;
  const forced = new URLSearchParams(location.search).has('quality');
  let q = { hi: state.tier === 'high', particles: state.tier === 'high' ? 1 : 0.4 };

  // 캔버스 글자가 대체 글꼴로 그려지지 않게 폰트를 잠깐 기다린다(최대 1.8초)
  await Promise.race([
    Promise.all([
      ...['600 44px', '800 66px', '500 32px'].map((f) => document.fonts.load(`${f} "Pretendard"`, '가A')),
      document.fonts.load('400 24px "D2Coding"', 'A'),
    ]),
    new Promise((r) => setTimeout(r, 1800)),
  ]);

  const canvas = document.querySelector('#gl');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: q.hi, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, q.hi ? 2 : 1));
  renderer.setSize(innerWidth, innerHeight, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x04060d);
  scene.fog = new THREE.FogExp2(0x04060d, 0.02);
  const camera = new THREE.PerspectiveCamera(state.mobile ? 62 : 42, innerWidth / innerHeight, 0.1, 600);

  scene.add(new THREE.HemisphereLight(C.cyan, 0x080a14, 0.5));
  const warm = new THREE.PointLight(C.kb, 26, 16, 1.6); // 사람(운영자) = KB 옐로
  warm.position.set(0.6, 2.8, 1.2);
  scene.add(warm);
  const screenLight = new THREE.PointLight(C.cyan, 6, 5, 2);
  screenLight.position.set(0, 1.6, -0.2);
  scene.add(screenLight);
  const rim = new THREE.DirectionalLight(0x7fd8ff, 1.1);
  rim.position.set(-4, 6, -8);
  scene.add(rim);

  /* 정거장 */
  const dom = readDom();
  const A = stationA({ names: dom.names, q });
  const B = stationB({ steps: dom.steps, q });
  const Cf = stationC({ dedupLabel: dom.dedupLabel, q });
  const D = stationD();
  const E = stationE({ feats: state.feats });
  const F = stationF({ sources: dom.sources });
  [[A, POS.A], [B, POS.B], [Cf, POS.C], [D, POS.D], [E, POS.E], [F, POS.F]].forEach(([s, p]) => {
    s.group.position.copy(p);
    scene.add(s.group);
  });
  const stations = [A, B, Cf, D, E, F];

  /* 별가루 */
  const NP = 2400;
  const dp = new Float32Array(NP * 3);
  const dc = new Float32Array(NP * 3);
  const tc = new THREE.Color();
  for (let i = 0; i < NP; i++) {
    dp.set([(Math.random() - 0.5) * 160, -10 + Math.random() * 70, 40 - Math.random() * 330], i * 3);
    const r = Math.random();
    tc.copy(r < 0.55 ? new THREE.Color(0xdfe8ff) : r < 0.85 ? new THREE.Color(C.cyan) : new THREE.Color(C.kb)).multiplyScalar(0.6 + Math.random() * 1.2);
    dc.set([tc.r, tc.g, tc.b], i * 3);
  }
  const dgeo = new THREE.BufferGeometry();
  dgeo.setAttribute('position', new THREE.BufferAttribute(dp, 3));
  dgeo.setAttribute('color', new THREE.BufferAttribute(dc, 3));
  const dust = new THREE.Points(dgeo, new THREE.PointsMaterial({
    size: state.mobile ? 0.34 : 0.26, map: glow(), vertexColors: true, transparent: true,
    depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false,
  }));
  scene.add(dust);

  /* 정거장을 잇는 빛줄기 — 끝은 관측 소스가 모이는 에이전트 코어 */
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  const curve = new THREE.CatmullRomCurve3([
    V(0, 0.3, 0), V(9, 6, -25), POS.B.clone().add(V(0, -3, 0)), V(-9, 6, -75), POS.C.clone().add(V(0, -3.5, 0)),
    V(9, 5, -125), POS.D.clone().add(V(0, -3.2, 0)), V(-9, 5, -175), POS.E.clone().add(V(0, -2.4, 0)), V(-9, 6, -225), V(-3, -5, -246), POS.F.clone(), // 마지막은 별자리 아래에서 올라온다(카메라 궤도와 겹치지 않게)
  ]);
  const trail = new THREE.Mesh(new THREE.TubeGeometry(curve, 700, 0.06, 6), new THREE.MeshBasicMaterial({ color: hdr(C.kb, 1.8), toneMapped: false }));
  const TRAIL_N = trail.geometry.index.count;
  trail.geometry.setDrawRange(0, 0);
  scene.add(trail);
  const spark = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow(), color: hdr(0xffd08a, 3), blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
  spark.scale.setScalar(2.2);
  scene.add(spark);

  /* 후처리(상 등급만) */
  let composer = null;
  function makeComposer() {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    composer.addPass(new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.85, 0.55, 0.9));
    composer.addPass(new OutputPass());
  }
  if (q.hi) makeComposer();

  function applyQuality() {
    stations.forEach((s) => s.setQuality && s.setQuality(q));
    dgeo.setDrawRange(0, Math.round(NP * (q.hi ? 1 : 0.4)));
  }
  applyQuality();

  /* 카메라 경로 */
  const camPos = new THREE.Vector3();
  const camLook = new THREE.Vector3();
  const tP = new THREE.Vector3();
  const tL = new THREE.Vector3();
  const off = new THREE.Vector3();
  const shift = { x: 0, y: 0 };
  const pointer = { x: 0, y: 0 };
  if (!state.mobile && !state.reduce) {
    addEventListener('pointermove', (e) => { pointer.x = (e.clientX / innerWidth) * 2 - 1; pointer.y = (e.clientY / innerHeight) * 2 - 1; });
  }
  function camTarget() {
    const { active: a, p, mobile } = state;
    let sx = 0;
    let sy = 0;
    const side = () => { if (mobile) sy = 0.2; else sx = -0.18; }; // 글자 패널 반대편에 피사체
    if (a === 0) {
      const th = 0.9 + p[0] * TAU;
      const s = Math.sin(p[0] * Math.PI);
      const r = (mobile ? 10 : 8.6) - 1.8 * s;
      tP.set(Math.sin(th) * r, (mobile ? 3.8 : 3.2) - 0.8 * s, Math.cos(th) * r);
      tL.set(0, 1.3, 0);
      if (mobile) sy = 0.12; else sx = -0.16;
    } else if (a === 1) {
      if (p[1] < 0.72) { tP.set(0, 11 + p[1] * 5, 15); tL.set(0, 1, 0); } else { tP.copy(POS.B).add(off.set(0, 6, 26)); tL.copy(POS.B); }
    } else if (a === 2) {
      const th = -0.5 + p[2] * Math.PI * 1.1;
      const r = (mobile ? 20 : 16) - 3 * p[2];
      tP.set(Math.sin(th) * r, 3.5 + 2.6 * Math.sin(p[2] * Math.PI), Math.cos(th) * r).add(POS.B);
      tL.copy(POS.B);
      side();
    } else if (a === 3) {
      const d = smooth(0, 1, p[3]); // 스크롤하면 깔때기를 따라 내려간다
      tP.copy(POS.C).add(off.set(7.5, lerp(10, 0.6, d), 13.5));
      tL.copy(POS.C).add(off.set(0, lerp(7, -0.8, d), 0));
      side();
    } else if (a === 4) {
      tP.copy(POS.D).add(off.set(lerp(-3, 3, p[4]), 3.4, 14));
      tL.copy(POS.D).add(off.set(lerp(-1.5, 1.5, p[4]), -0.3, 0));
      side();
    } else if (a === 5) {
      tP.copy(POS.E).add(off.set(0, 0.5, E.CR + (mobile ? 7.6 : 6.4)));
      tL.copy(POS.E).add(off.set(0, 0.05, E.CR));
      sy = mobile ? 0.04 : 0.02;
    } else if (a === 6) {
      tP.set(lerp(36, -36, p[6]), 58, 12);
      tL.set(0, 0, -120);
    } else if (a === 7) {
      const th = -0.4 + p[7] * Math.PI * 0.8;
      tP.copy(POS.F).add(off.set(Math.sin(th) * 15, 3.5, Math.cos(th) * 15));
      tL.copy(POS.F);
      side();
    } else {
      tP.set(Math.sin(1.3) * 10.5, 3.6, Math.cos(1.3) * 10.5);
      tL.set(0, 1.4, 0);
      sy = -0.16; // 제목 아래에 관제실이 오게
    }
    tP.x += pointer.x * 0.45;
    tP.y -= pointer.y * 0.25;
    return { sx, sy };
  }
  camTarget();
  camPos.copy(tP);
  camLook.copy(tL);

  function resize() {
    renderer.setSize(innerWidth, innerHeight, false);
    if (composer) composer.setSize(innerWidth, innerHeight);
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize);

  /* 품질 감시 — 시작 3초(셰이더 컴파일)는 보지 않는다 */
  let since = performance.now();
  let winStart = since;
  let acc = 0;
  let cnt = 0;
  function downgrade() {
    if (state.tier === 'high') {
      api.setTier('mid');
      composer = null;
      renderer.setPixelRatio(1);
      renderer.setSize(innerWidth, innerHeight, false);
      q = { hi: false, particles: 0.4 };
      applyQuality();
      since = performance.now();
      console.info('[intro] 프레임이 느려 블룸을 끕니다(중 등급)');
    } else if (state.tier === 'mid') {
      api.setTier('static');
      canvas.classList.remove('on');
      console.info('[intro] 프레임이 느려 3D를 멈춥니다(정지 등급)');
    }
  }
  function watch(raw) {
    const now = performance.now();
    if (now - since < 3000) { winStart = now; acc = 0; cnt = 0; return; }
    if (raw > 0 && raw < 250) { acc += raw; cnt += 1; } // 탭 전환 복귀 같은 긴 간격은 제외
    if (now - winStart < 2000) return;
    const avg = cnt ? acc / cnt : 0;
    api.fps = avg ? Math.round(1000 / avg) : 0;
    winStart = now;
    acc = 0;
    cnt = 0;
    if (avg > SLOW_FRAME_MS && !forced) downgrade();
  }

  let shown = false;
  function update(dt, t, raw) {
    const { active, p } = state;
    const k = state.reduce ? 1 : 1 - Math.exp(-dt * 2.6);
    const kf = state.reduce ? 1 : 1 - Math.exp(-dt * 5);
    const ctx = { state, t, dt, kf };
    stations.forEach((s) => s.update(ctx));

    warm.intensity = lerp(warm.intensity, active === 8 ? 60 : 26, k);

    const grow = active < 6 ? 0 : active === 6 ? 0.8 * smooth(0.05, 0.9, p[6]) : active === 7 ? 0.8 + 0.2 * smooth(0.46, 0.9, p[7]) : 1;
    trail.geometry.setDrawRange(0, Math.floor((TRAIL_N * grow) / 3) * 3);
    trail.visible = active === 6 || active === 7;
    spark.visible = trail.visible && grow > 0.01;
    if (spark.visible) curve.getPoint(((t * 0.1) % 1) * grow, spark.position);
    dust.rotation.y = t * 0.004;

    const { sx, sy } = camTarget();
    camPos.lerp(tP, k);
    camLook.lerp(tL, k);
    camera.position.copy(camPos);
    camera.lookAt(camLook);
    shift.x = lerp(shift.x, sx, k);
    shift.y = lerp(shift.y, sy, k);
    camera.setViewOffset(innerWidth, innerHeight, shift.x * innerWidth, shift.y * innerHeight, innerWidth, innerHeight);
    scene.fog.density = lerp(scene.fog.density, active === 6 ? 0.0035 : active === 5 ? 0.012 : active === 7 ? 0.014 : 0.02, k);

    if (composer) composer.render(); else renderer.render(scene, camera);
    if (!shown) { shown = true; canvas.classList.add('on'); }
    watch(raw);
  }

  return { update };
}
