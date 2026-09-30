/* 시스템 소개 페이지 — 장면 진행률과 글자 연출 (plans/124 · D-277)
 *
 * DOM만 다룬다. 3D(world.js)는 이 파일이 만든 상태(active · p[] · 파생값)를 읽기만 하므로,
 * 3D를 불러오지 못해도 이 파일만으로 이야기가 끝까지 읽힌다.
 * 모듈이 아닌 일반 스크립트로 둔다 — 서버가 .js 를 JavaScript MIME 으로 주지 않아도 글자 연출은 돈다.
 */
(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
  const pad = (n) => String(n).padStart(2, '0');

  const root = document.documentElement;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mobile = matchMedia('(max-width: 760px), (pointer: coarse)').matches;

  const sceneEls = $$('[data-scene]');
  const chips = $$('#chips li');
  const feats = $$('#featList > li').map((li) => ({
    name: li.querySelector('b').textContent,
    desc: li.querySelector('span').textContent,
    status: li.dataset.status,
    role: li.dataset.role,
    icon: li.dataset.icon,
  }));
  $('#s6').style.setProperty('--h', `${feats.length * 40 + 100}vh`);
  $('#featN').textContent = pad(feats.length);

  // 장면 번호: 0 도입 · 1 문제 · 2 5단계 · 3 선별 · 4 분석 · 5 기능 · 6 숫자 · 7 관측·로드맵 · 8 마무리
  const state = {
    active: 0,
    p: sceneEls.map(() => 0),
    reduce,
    mobile,
    tier: 'static',
    vdi: false, // 가상·소프트웨어 GPU 이거나 WebGL 없음 — pickTier 가 정한다
    feats,
    featF: 0, // 기능 링의 연속 위치(0 ~ N-1)
    wake: chips.map(() => 0), // 도입 장면 에이전트 깨어남(0~1) — 칩 하나에 에이전트 하나
    step: -1, // 5단계 강조 위치
    source: -1, // 관측 소스 강조 위치
  };

  /* ───────── 진행률 ───────── */
  function readScroll() {
    const vh = innerHeight;
    let active = 0;
    sceneEls.forEach((el, i) => {
      const r = el.getBoundingClientRect();
      const span = r.height - vh;
      state.p[i] = el.classList.contains('scene') && span > 1 ? clamp(-r.top / span) : clamp((vh - r.top) / vh);
      if (r.top <= vh * 0.5) active = i;
    });
    state.active = active;
    const { p } = state;

    state.wake = chips.map((_, i) => (active > 0 ? 1 : smooth(0.3 + i * 0.055, 0.38 + i * 0.055, p[0])));
    state.step = active < 2 ? -1 : active > 2 ? 4 : p[2] < 0.16 ? -1 : Math.min(4, Math.floor((p[2] - 0.16) / 0.16));
    if (active < 5) state.featF = 0;
    else if (active > 5) state.featF = feats.length - 1;
    else {
      const x = clamp(p[5] / 0.94) * (feats.length - 1);
      const f = Math.floor(x);
      state.featF = Math.min(feats.length - 1, f + smooth(0.3, 0.7, x - f));
    }
    state.source = active < 7 ? -1 : active > 7 ? 5 : p[7] < 0.04 ? -1 : Math.min(5, Math.floor((p[7] - 0.04) / 0.06));
  }

  /* ───────── 글자 연출 ───────── */
  const reveals = $$('[data-show]').map((el) => {
    const [a, b] = el.dataset.show.split(',').map(Number);
    return { el, a, b, i: +el.closest('[data-scene]').dataset.scene, last: -1 };
  });
  const lines = $$('#s2 [data-at]');
  const steps = $$('#steps li');
  const tiers = $$('#tiers li');
  const sources = $$('#sources li');
  const dots = $$('#dots a');
  const counters = $$('[data-count]').map((el) => ({ el, target: +el.textContent, last: -1 }));
  const featUI = { idx: $('#featIdx'), name: $('#featName'), desc: $('#featDesc'), role: $('#featRole'), badge: $('#featBadge') };
  const STATUS_TEXT = { done: '구현 완료', mvp: 'MVP' };
  const progress = $('#progress');

  let lastFeat = -1;
  let lastActive = -1;

  function updateUI() {
    const { p, active } = state;

    for (const r of reveals) {
      const f = 0.07;
      const pr = p[r.i];
      const vin = r.a <= 0 ? 1 : smooth(r.a, r.a + f, pr);
      const vout = r.b >= 1 ? 1 : 1 - smooth(r.b - f, r.b, pr);
      const o = Math.round(vin * vout * 100) / 100;
      if (o === r.last) continue;
      r.last = o;
      r.el.style.opacity = o;
      r.el.style.visibility = o <= 0.001 ? 'hidden' : 'visible';
      if (!reduce) r.el.style.transform = `translate3d(0, ${((1 - vin) * 28 - (1 - vout) * 28).toFixed(1)}px, 0)`;
    }

    chips.forEach((c, i) => c.classList.toggle('on', state.wake[i] > 0.6));

    let now = -1;
    if (active > 1) now = lines.length - 1;
    else if (active === 1) lines.forEach((l, i) => { if (p[1] >= +l.dataset.at) now = i; });
    const answer = lines.length - 1;
    lines.forEach((l, i) => {
      // 답 줄이 나오면 문제 네 줄은 흐리게 남긴다
      l.classList.toggle('now', i === now || (now === answer && i === answer));
      l.classList.toggle('past', now >= 0 && i < now);
    });

    steps.forEach((s, k) => {
      s.classList.toggle('now', k === state.step);
      s.classList.toggle('seen', state.step === -1 || k < state.step);
    });

    const tk = active !== 3 ? (active > 3 ? 3 : -1) : Math.floor((p[3] - 0.4) / 0.06);
    tiers.forEach((t, k) => t.classList.toggle('on', k <= tk));

    sources.forEach((s, k) => {
      s.classList.toggle('on', k <= state.source);
      s.classList.toggle('now', k === state.source && active === 7 && p[7] < 0.42);
    });

    const fi = Math.round(state.featF);
    if (fi !== lastFeat && feats[fi]) {
      lastFeat = fi;
      const f = feats[fi];
      featUI.idx.textContent = pad(fi + 1);
      featUI.name.textContent = f.name;
      featUI.desc.textContent = f.desc;
      featUI.role.textContent = f.role;
      featUI.badge.dataset.status = f.status;
      featUI.badge.textContent = STATUS_TEXT[f.status] || f.status;
    }

    const k = smooth(0.06, 0.34, p[6]);
    for (const c of counters) {
      const v = active > 6 ? c.target : Math.round(c.target * k);
      if (v !== c.last) { c.last = v; c.el.textContent = v; }
    }

    if (active !== lastActive) {
      lastActive = active;
      root.dataset.active = String(active);
      dots.forEach((d, i) => d.classList.toggle('on', i === active));
    }
    const max = document.documentElement.scrollHeight - innerHeight;
    progress.style.transform = `scaleX(${max > 0 ? (scrollY / max).toFixed(4) : 0})`;
  }

  /* ───────── 발표용 키보드 이동 — 장면 안의 멈춤 지점 단위 ───────── */
  function stopList() {
    const vh = innerHeight;
    const out = [];
    sceneEls.forEach((el) => {
      const top = el.getBoundingClientRect().top + scrollY;
      const span = el.offsetHeight - vh;
      let qs = (el.dataset.stops || '').split(',').filter(Boolean).map(Number);
      if (el.id === 's6') qs = feats.map((_, i) => (feats.length > 1 ? (i / (feats.length - 1)) * 0.94 : 0));
      if (!el.classList.contains('scene') || span <= 1 || !qs.length) out.push(top);
      else qs.forEach((q) => out.push(top + q * span));
    });
    return out.map(Math.round).sort((a, b) => a - b);
  }

  let pending = null;
  addEventListener('keydown', (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    let dir = 0;
    if (e.key === 'ArrowRight' || e.key === 'PageDown' || (e.key === ' ' && !e.shiftKey)) dir = 1;
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp' || (e.key === ' ' && e.shiftKey)) dir = -1;
    if (!dir) return;
    e.preventDefault();
    const y = pending !== null ? pending : scrollY;
    const list = stopList();
    const target = dir > 0 ? list.find((s) => s > y + 4) : [...list].reverse().find((s) => s < y - 4);
    if (target === undefined) return;
    pending = target;
    scrollTo({ top: target, behavior: reduce ? 'auto' : 'smooth' });
  });
  // 휠·터치로 움직이면 예약 지점을 버린다
  ['wheel', 'touchstart'].forEach((ev) => addEventListener(ev, () => { pending = null; }, { passive: true }));

  /* ───────── 품질 등급 · 3D 불러오기 ─────────
   * VDI 대응(D-277 ⑤ 개정)은 가상·소프트웨어 GPU 이거나 WebGL 이 없는 환경에서만 한다 —
   * reduced-motion 이어도 3D를 켜고(움직임은 world.js 가 줄인다), 3D를 못 쓰면 장면 영상을 보여 준다.
   * 그 밖의 PC는 기존 동작 그대로다(reduced-motion 이면 정지 · 정지 배경은 그라데이션). */
  const VIRTUAL_GPU = /swiftshader|llvmpipe|basic render|software|vmware|svga|citrix|virtualbox|parallels|hyper-v|remote|virgl|virtio|qxl|grid/i;

  /** WebGL 렌더러 이름 — WebGL 을 못 쓰면 null */
  function probeRenderer() {
    let gl = null;
    try {
      const c = document.createElement('canvas');
      gl = c.getContext('webgl2') || c.getContext('webgl');
    } catch (e) {
      gl = null;
    }
    if (!gl) return null;
    let renderer = '';
    try {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      renderer = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    } catch (e) {
      renderer = '';
    }
    const lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    return renderer;
  }

  function pickTier() {
    const renderer = probeRenderer();
    state.vdi = renderer === null || VIRTUAL_GPU.test(renderer);
    const q = new URLSearchParams(location.search).get('quality');
    if (q === 'high' || q === 'mid' || q === 'static') return q;
    if (reduce && !state.vdi) return 'static';
    if (renderer === null) return 'static';
    // 가상 PC 등 소프트웨어 렌더러는 블룸 없이 시작한다
    if (/swiftshader|llvmpipe|basic render|software/i.test(renderer)) return 'mid';
    return mobile ? 'mid' : 'high';
  }

  function setTier(t) {
    state.tier = t;
    root.dataset.tier = t;
    root.classList.toggle('no-gl', t === 'static');
  }

  /* ───────── 정지 등급 영상 — VDI 등에서 3D 없이도 장면이 보이게(D-277 ⑤ 개정) ─────────
   * 지금 장면의 영상만 받아 재생하고 나머지는 멈춘다. reduced-motion 이면 첫 프레임에 멈춰 둔다. */
  const clips = $$('.backdrop video');
  let clipOn = -1;
  function syncClips() {
    const want = state.tier === 'static' && state.vdi ? state.active : -1;
    if (want === clipOn) return;
    clipOn = want;
    clips.forEach((v, i) => {
      if (i !== want) { if (!v.paused) v.pause(); return; }
      v.preload = 'auto';
      if (!reduce) v.play().catch(() => {});
    });
  }

  let world = null;
  window.__intro = {
    state,
    setTier,
    get tier() { return state.tier; },
    fps: 0,
  };

  readScroll();
  updateUI();
  setTier(pickTier());
  if (state.tier !== 'static') {
    import('/static/intro/world.js?v=1')
      .then((m) => m.start(window.__intro))
      .then((w) => { world = w; })
      .catch((err) => {
        console.warn('3D를 켜지 못해 정지 화면으로 보여 줍니다', err);
        setTier('static');
      });
  }

  let last = performance.now();
  let t = 0;
  function frame(now) {
    const raw = now - last;
    last = now;
    const dt = Math.min(0.05, raw / 1000);
    if (!reduce) t += dt;
    if (pending !== null && Math.abs(scrollY - pending) < 2) pending = null;
    readScroll();
    updateUI();
    syncClips();
    if (world && state.tier !== 'static' && !document.hidden) world.update(dt, t, raw);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
