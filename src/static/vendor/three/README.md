# three.js (벤더링 사본)

시스템 소개 페이지(`/intro` · `plans/124` · D-277)의 3D 연출용이다. 폐쇄망에서는 CDN을 쓸 수 없어 필요한 파일만 저장소에 둔다.

- 패키지: `three@0.160.0`(npm) · 라이선스 MIT(`LICENSE`)
- 반입: 2026-09-29 개발 맥에서 `npm pack three@0.160.0`으로 받아 아래 파일만 복사했다(수정 없음).
- 범위: 코어 모듈 + 블룸 후처리 경로(EffectComposer · RenderPass · UnrealBloomPass · OutputPass와 그 의존 파일). 이 밖의 addon은 없다 — 새로 쓰려면 같은 버전에서 복사하고 아래 표에 더한다.
- 소개 페이지의 importmap이 `three` → `build/three.module.min.js`, `three/addons/` → `examples/jsm/`로 연결한다.

| 파일 | sha256 |
|---|---|
| `build/three.module.min.js` | `3e690ac7d180b0aadf0891bea39eec643e29e2d3e75c99b18689518665f69ba6` |
| `examples/jsm/postprocessing/EffectComposer.js` | `d234e578618fa816955ebdc059c049c577e203e650e33cf22bde3f232c29e669` |
| `examples/jsm/postprocessing/MaskPass.js` | `328cf7db0da5d9be83ffe39d54b01d5ac1fddf108cc98182ddbb056f5c8b537f` |
| `examples/jsm/postprocessing/OutputPass.js` | `13817fc7a87f662d29d2c5e00f44d3a4588c9afac4a372de0cacb0e44e368ffd` |
| `examples/jsm/postprocessing/Pass.js` | `b3c6128340eaa37e40a6a2f1b738e894c855239417d50959759b34a2b5e89f92` |
| `examples/jsm/postprocessing/RenderPass.js` | `1c90c085312871c4bcdccfcf519499c6276dd503363fcf7cb7f703add45cf4a2` |
| `examples/jsm/postprocessing/ShaderPass.js` | `3b28a1ee27e0eb96c0eab137a1f442ccf127a926904eced2d51e125ec44af781` |
| `examples/jsm/postprocessing/UnrealBloomPass.js` | `8f09315c0cec117a0ca2494d3e3586035b3c4323d6dcb037537cc51b04c3cdba` |
| `examples/jsm/shaders/CopyShader.js` | `4e3346db194db56a596cd074e9bdb39fb5eb52040c333e0d29dc4eb1324d3b1d` |
| `examples/jsm/shaders/LuminosityHighPassShader.js` | `3d841cc594a0c1767d1b0185720b32761a0133c5f1b70b56658e28f2fb9b7900` |
| `examples/jsm/shaders/OutputShader.js` | `53a52e430c27bc36ceaab8ae90a2b4af7b02672d7ee4b29d6ba3c28e09c92c2a` |
