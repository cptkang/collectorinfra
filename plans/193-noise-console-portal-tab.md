# 193. 노이즈 관제를 포털 안 뷰 탭으로 이식 — 관제 → 조사 진입을 같은 창 탭 전환으로

> **작성일**: 2026-10-26 · **v1.1**(K-1·K-2 확정 반영 — 코드 0건)
> **상태**: **완료(2026-10-26 · 미커밋)** — W0~W4 + 독립 검증·교정 1라운드 · 플래그 `NOISE_CONSOLE_PORTAL_TAB_ENABLED` 기본 off(켜는 시점은 사용자 확인) · 계획 끝 모듈 단위 회귀 rc 0(원래 실패 1) · 교정분 회귀 「이번 변경 탓」 1건 교정 · 결과 §6
> **계획 시작 SHA**: `84b400f5`(W0 착수 2026-10-26 — 192·194 병합 뒤 · 작업 트리 깨끗)
> **회귀 대상 파일**(41 · 이미지 png 제외): `config/settings_help/noise_gate_core.yaml` · `scripts/manual/capture.py` · `scripts/manual/captures.yaml` · `scripts/manual/content/admin.md` · `scripts/manual/content/user.md` · `scripts/manual/features.yaml` · `src/api/routes/alarm.py` · `src/api/routes/alarm_schemas.py` · `src/api/settings_catalog.py` · `src/config/alarm.py` · `src/static/admin/noise.html` · `src/static/css/noise-console.css` · `src/static/css/style.css` · `src/static/index.html` · `src/static/js/app.js` · `src/static/js/noise.js` · `src/static/manual/admin.html` · `src/static/manual/help_index.json` · `src/static/manual/img/captured.json` · `src/static/manual/user.html` · `src/static/noise.html` · `tests/test_api/test_alarm_capabilities.py` · `tests/test_api/test_noise_dashboard_api.py` · `tests/test_api/test_plan160_static_assets.py` · `tests/test_api/test_plan193_verify.py` · `tests/test_api/test_plan193_w4_break_glass.py` · `tests/test_api/test_settings_catalog.py` · `tests/test_api/test_thread_history.py` · `tests/test_api/test_ui_alarm_view.py` · `tests/test_api/test_ui_plan187_w15_diagnosis.py` · `tests/test_api/test_ui_plan187_w16_conversation_tab.py` · `tests/test_api/test_ui_plan192_w1_entry.py` · `tests/test_api/test_ui_plan192_w2_entry_labels.py` · `tests/test_api/test_ui_plan192_w4_conversation_restore.py` · `tests/test_api/test_ui_plan193_w1_mount.py` · `tests/test_api/test_ui_plan193_w2_portal_tab.py` · `tests/test_api/test_ui_plan193_w4_standalone.py` · `tests/test_api/test_ui_plan194_w5_noise_admin.py` · `tests/test_api/test_ui_query_history.py` · `tests/test_plan187_fe2_static.py` · `tests/test_plan187_w5b_static.py`
> **출처**: `plans/192` §5 Q-1 사용자 결정(2026-10-26) **C. 노이즈 관제를 포털 안 뷰 탭으로 이식** — 관제에서 조사를 열 때마다 새 브라우저 탭이 쌓이는 문제(192 P6)를 구조로 없앤다. 배경 이력(W16 이름 창 → W19 포털 창 재사용 → W23 항상 새 탭)은 192 §1.
> **선후 관계**: `plans/192` W1·W3·W4는 이 계획과 **독립**(어느 쪽이 먼저 끝나도 됨). 192 **W2(포털 안 진입 문구 규칙)는 이 계획 W3보다 먼저** 끝낸다 — 관제 탭의 진입 버튼이 그 규칙을 그대로 쓴다. 192 W1의 Q-3 줄(`noise.js` break-glass 조건)과 이 계획 W1(`noise.js` mount 리팩터)은 같은 파일이라 먼저 끝난 쪽 위에 다른 쪽을 얹는다. **`plans/194` W5 U5(관제 서랍 「조사 안 됨 → [조사 시작]」·「수동 조사 모드」 표지)가 먼저 들어간다(2026-10-26 · 미커밋) — 193 W1(`noise.js` mount 리팩터)은 그 위에 얹는다.**
> **관련 결정**: D-196(관제 대시보드 — 운영자 화면 `/static/admin/noise.html`·침묵 쓰기·정책 GET만) · D-245(사용자 관제 `/noise` — 읽기 5종만 · 침묵·정책·스트림은 운영자 · **마크업·JS 두 벌 복제 기각**) · D-247(관제 드릴다운·설명) · **D-248 ①**(포털의 Admin·노이즈 관제 링크는 새 탭 — 원래 탭의 채팅이 사라지지 않게) + 부기(관제 링크 `rel="opener"`) · **D-341 부기 10 ㊲**(관제 → 기존 포털 장애 진단 탭 · 「관제 페이지 안에 진단 화면을 만들지 않는다」) · **부기 14 ㊾**(관제 진입 = 항상 새 탭) · **D-338**(인증 저장소 장애 시 사용자 요청 503 · **break-glass는 진입로로 유지**) · D-069·D-070(통합 RBAC · 사용자/운영자 시크릿 분리) · D-255(매뉴얼 동반) · D-337(동작 불변 리팩터와 동작 변경 분리)
> **관련 실수**: `docs/18` 2026-10-22 「다른 브라우저 탭 재사용을 이름 창에 맡겨 새 창이 계속 열림」(진입 경로별 창 수 세기) · 2026-09-30 「admin_token만 읽어 401」(토큰 키 순서) · 2026-10-23 「관제 진입을 항상 새 탭으로 바꾸면서 break-glass 버튼과 로그인 복귀 경로를 다시 보지 않음」 · 2026-07-14 「권한 없는 사용자도 EventSource를 열어」

---

## 1. 목표 · 범위

- 포털(`/`) 머리글 뷰 탭 줄에 **「노이즈 관제」 탭**을 둔다. 지금의 머리글 링크(새 탭 `/noise`)를 이 탭 전환으로 바꾼다.
- 관제 서랍의 「장애 진단에서 보기」/「조사 대화로 열기」는 포털 탭 안에서 **같은 창의 장애 진단·조사 대화 탭으로 전환**한다(`openInvestigationEntry` — 새 탭 0).
- 관제 화면 코드는 **한 벌**(D-245 — 복제 기각)이다. `noise.js`를 「불러오면 바로 실행되는 페이지 스크립트」에서 「지정한 영역에 붙이는 모듈」로 바꾸고, 독립 페이지와 포털 탭이 같은 모듈을 쓴다.
- **범위 밖**: 관제 기능 자체(퍼널·드로어·침묵·검토 콘솔)의 변경 · 관리자 대시보드(`/admin`) 탭 구조 · 서버 API(`/noise/*`·`/admin/noise/*`) 변경 — 인가·응답은 비트 동일.

## 2. 현 구조 실측 (2026-10-26 · HEAD `2f64aa9c`)

| 항목 | 실측 | 이식에 주는 영향 |
|---|---|---|
| 페이지 | 사용자 `src/static/noise.html`(160줄 · id 35개 · `data-noise-mode="user"` · 서버 `/noise` → `FileResponse` `src/api/server.py:959-962`) · 운영자 `src/static/admin/noise.html`(255줄 · id 63개 — 운영자 전용 28개 · `data-noise-mode="admin"` · 관리자 대시보드 머리 링크 `admin/dashboard.html:72` 같은 탭) | 포털 탭은 **사용자 모드 마크업**을 옮겨 온다(id 35개 — `index.html`과 겹치는 id 0개 실측) |
| 스크립트 | `noise-help.js` v6 · `briefing-view.js` v6(포털도 이미 로드) · `noise.js` v19(3,410줄 · 즉시 실행 IIFE) | `noise.js`가 로드 시점에 토큰 선택·리다이렉트·타이머를 바로 시작한다 → **mount 함수로 감싸야** 포털에서 지연 실행 가능 |
| 모드 판정 | `document.body.getAttribute("data-noise-mode")`(`noise.js:25`) | 포털 body에는 없음 → mount 인자로 받는다 |
| 인증 | 토큰 키 순서 user `["user_token","admin_token"]` / admin `["admin_token","user_token"]`(`:31-37`) · 없으면 `/login?next=<페이지>`(`:40`) · 401이면 그 키를 지우고 로그인(`:76-77`) · 403이면 `/`로(`:86`) · `IS_BREAK_GLASS = tokenKey === "admin_token"`(`:1855`) | 포털 탭에서는 포털 인증(`checkAuthOnLoad` · `getAuthHeaders` — `user_token`만)을 따른다. 401·403 처리는 포털의 `redirectToLogin`(192 W1 `next` 반영)으로 넘긴다. **포털 탭은 break-glass가 될 수 없다**(포털이 `admin_token`을 읽지 않음) |
| 실시간 | 운영자 모드만 `fetch` 스트림(`/admin/noise/stream` · 헤더 토큰 · `:848-870` · 401/403이면 재연결 없음) · 사용자 모드는 스트림 없음 — `setInterval` 재동기화 30초(`RESYNC_MS`)·집계 60초(`SUMMARY_MS`)(`:3403-3409`) | 포털 탭(사용자 모드)은 스트림 0. 타이머는 **탭이 보일 때만** 돌게 해야 포털 상주 부하가 늘지 않는다(`visibilitychange` 처리 현재 없음) |
| DOM 부착 | `document.body.appendChild` 3곳(툴팁 `:407` · 정보 팝오버 `:2041` · 검토 콘솔 스크림·패널 `:2715-2716`) · `getElementById` 70회 | 포털 레이아웃 위에 뜨는 층(z-index)·뷰 숨김 시 남는 팝오버 정리 필요 |
| 조사 진입 | 서랍 ⓒ 머리 `portalEntryButton`·`openInPortal`(`:1905-1929` — 항상 새 탭) · 장애 진단 켜짐 판정 `/active` 1회(`:1897-1902`) | 포털 탭에서는 mount 인자 `onOpenInvestigation(id)` → 포털 `openInvestigationEntry`로 바꾼다. 켜짐 판정은 포털의 `diagnosisOn()`을 쓴다(중복 `/active` 0 · 192 P3 경쟁 소멸) |
| 포털 뷰 탭 | `setActiveView`(`app.js:4280`)·`viewRegistry` · 「조사 대화」 탭은 대화 화면을 공유(`swapConversation`) · 답변 스트리밍 중 전환 거부 | 관제 탭은 독립 화면(공유 없음)이라 `swapConversation` 대상 아님 · 스트리밍 중에도 관제 탭 전환은 허용(채팅 DOM 유지) |
| CSS | `noise-console.css` 533줄 + `style.css`(포털은 v38, 사용자 관제는 v18 — 같은 파일 다른 버전 고정) | 포털에 `noise-console.css`를 더하면 선택자 충돌 점검 필요(W0) |
| 의존 테스트 | `noise.js`·`noise.html`을 읽는 테스트 11파일(`tests/test_api/test_noise_*`·`test_ui_plan187_w15/w16`·`test_plan153_*`·`test_plan187_fe2/w5b_static`·`test_admin_auth_redirect_loop`·`test_manual_coverage`) · 캡처 `noise-user`·`noise-drawer`·`noise-drawer-cause`·`noise-admin`(`captures.yaml:401-452`) · 매뉴얼 U-44(`features.yaml:105`) | 문자열 단언 테스트가 많다 — W1 동작 불변 리팩터에서 고정값 갱신 범위를 먼저 센다 |

### 2.1 W0 실측 (2026-10-26 · HEAD `84b400f5` · 코드 0)

§2 표의 행 번호는 `2f64aa9c` 기준이다. 192·194가 들어간 뒤 `noise.js`는 3,435줄(v21)이고, 아래가 착수 시점 값이다.

| 항목 | 실측 | 정한 방식 |
|---|---|---|
| CSS 충돌 | `noise-console.css` 선택자 177 클래스 · 요소·`body`·`:root` 선택자 0 · 포털 DOM(`index.html`·`app.js`·`diagnosis-view.js`·`briefing-view.js`·`result-viz.js`)이 실제로 다는 클래스와 겹치는 것은 `.btn` 1개뿐이고 그것도 `.dp-search .btn`(관제 안 한정) | 포털에 그대로 얹는다. 스타일시트는 **첫 mount 때** `noise.html` 머리의 `<link>`를 그대로 옮겨 붙인다(버전 단일 출처 · 플래그 off면 포털에 안 실림) — 실브라우저 계산 스타일 대조로 W2에서 재확인 |
| id 충돌 | 사용자 마크업 id 35개 × `index.html` id — 겹침 0 | 그대로 |
| 포털에 같은 표지 | `index.html:199` 「❓ 사용법」 버튼이 `data-help="1"`을 단다 — 지금의 `attachHelp(document)`를 포털에서 부르면 이 버튼에 관제 (!) 버튼이 붙는다 | `document` 범위 조회(`attachHelp` · `[data-drill]` · `[data-focus-key]` · `.mgmt-tabs`·`.pane` · `#rangeSeg button` · `.noise-wrap` · `.noise-brand h1`)를 mount 루트 범위로 바꾼다(독립 페이지는 루트 = `body`라 같은 결과) |
| 층 순서 | 관제 오버레이 z 30~50(`noise-console.css` 층 표) · 포털 전역 `header` sticky z 100 · `.diagnosis-view`는 `position:relative; z-index:1`(쌓임 맥락) | 포털 관제 뷰 컨테이너는 **쌓임 맥락을 만들지 않고**, 포털 안에서만 오버레이 층을 헤더 위(130~150)로 올린다(관제 CSS 층 표에 포털 행 추가 · 루트 클래스 범위). `document.body.appendChild` 3곳(툴팁·설명 팝오버·검토 콘솔)은 mount 루트에 붙인다 — 뷰가 숨으면(`view-hidden`) 같이 숨는다 |
| 문서 전역 리스너 | `keydown`(Esc 닫기 `:1331`) · `click`·`scroll`(팝오버 닫기 `:2223`·`:2233`) · `resize` 2곳 | 그대로 두되 포털에서 뷰가 숨어 있으면(`pause`) Esc는 무시 |
| 요청 수(사용자 모드) | mount 직후 `summary`·`timeseries`·`top-suppressed`·`decisions`(피드)·`decisions`(이력) 5건 + `/alarm/capabilities` 1 + `/alarm/investigations/active` 1 · 이후 30초마다 피드 1 · 60초마다 집계 1(분당 3건) · `visibilitychange` 처리 없음 | 포털에서는 뷰가 숨으면 두 타이머를 멈추고, 다시 보이면 마지막 갱신이 30초를 넘었을 때만 즉시 갱신 · `/active`는 포털 `diagnosisOn()`으로 대신(0건) |
| `/noise` 진입로 | 포털 머리글 `index.html:72`(새 탭 · `rel="opener"`) · 주소·즐겨찾기 · 서버 라우트 `server.py:962` · 매뉴얼 본문의 화면 이름(링크 아님) · 관리자 대시보드는 운영자 관제(`dashboard.html:96` 같은 탭) | 플래그 on이면 머리글 링크를 DOM에서 빼고 뷰 탭 버튼을 보인다(포털 안 `/noise` 링크 0) |
| `window.opener` 사용 | `noise.js`·`app.js` 모두 0(W23 뒤 관제는 `noopener` 새 탭) | 머리글 `rel="opener"`는 쓰임새 없음(P12) |
| 의존 테스트 | `noise.js`·`noise.html`을 읽는 테스트 14파일(§2 표의 11 + `test_ui_plan192_w1_entry`·`test_ui_plan192_w2_entry_labels`·`test_ui_plan194_w5_noise_admin`) | W1 들여쓰기 불변 방식(IIFE 본문을 mount 함수 본문으로 — 줄 들여쓰기 그대로)으로 문자열 단언 깨짐을 줄인다 |
| 마크업 한 벌 | 독립 `/noise`가 남으므로 포털은 `/static/noise.html`을 조회해 `<body>`의 스크립트 아닌 자식(관제 영역·스크림·서랍)을 mount 루트에 옮겨 붙인다(조각 조회 · 사본 0). 포털에서 뜻이 없는 「질의응답으로」 링크는 표지(`data-noise-standalone`)를 달아 포털 mount가 뺀다 | §3 운용 규칙 3 |
| 기능 플래그 전달 | `NoiseGateConfig`(`env_prefix="NOISE_"`)에 `console_portal_tab_enabled` → `NOISE_CONSOLE_PORTAL_TAB_ENABLED` · 화면 전달은 로그인 사용자 전원이 이미 부르는 `/api/v1/alarm/capabilities`(`initAlarmSubscription` → `loadAlarmCapabilities`)에 불리언 1개 | — |

## 3. 결정 충돌 점검 — K-1·K-2 **확정(2026-10-26)**

| 질문 | 사용자 선택 | 비고 |
|---|---|---|
| K-1 | **B. 두 독립 페이지 유지 + 포털 탭 추가** | 권고(A)와 다름. D-338 break-glass 진입로는 유지되어 충돌 없음. 대가: 사용자 관제 진입로가 포털 탭·`/noise` 독립 페이지 둘이 된다 → 아래 「K-1 B 운용 규칙」으로 동작 갈림을 줄인다 |
| K-2 | **A. 읽기 전용 사용자 모드** | 권고대로. 운영자 기능은 관리자 대시보드의 운영자 관제에만 |

**K-1 B 운용 규칙**(이 계획 안에서 지킨다)
1. **포털 머리글 「노이즈 관제」는 포털 탭 전환**이다(플래그 on). 독립 `/noise`는 주소·즐겨찾기·매뉴얼 링크로 들어오는 경우만 남고, 포털 안에서 `/noise`로 가는 링크는 0개로 만든다.
2. **독립 페이지(사용자·운영자)의 조사 진입은 종전대로 새 탭**(D-341 ㊾ 유지)이고, **포털 탭에서의 조사 진입만 같은 창 탭 전환**이다. 이 구분을 버튼 표지(독립 페이지는 「새 탭」)와 매뉴얼에 적는다 — 「어디서 열었느냐」에 따라 갈리는 동작을 화면에서 알 수 있게 한다.
3. **사용자 모드 마크업은 한 벌**(D-245 「두 벌이면 한쪽만 고쳐진다」의 뜻) — 독립 `/noise`가 남으므로 포털 템플릿에 마크업을 복사하지 않는다. 포털 탭은 `noise.html`의 관제 영역을 그대로 받아 붙이거나(조각 조회), 마크업 골격을 `noise.js`가 그리게 한다 — 방식은 W0 실측으로 정한다.

아래 K-1·K-2 선택지 표는 질문 당시 원문을 보존한다.

C 선택으로 바뀌는 기존 결정 문구는 두 갈래다.

**(가) Q-1 C를 고르면서 이미 함께 정해진 것 — 질문 없이 구현 Wave에서 부기로 기록**
- **D-248 ①**: 「관제 링크는 새 탭」의 목적은 *포털 탭이 다른 화면으로 바뀌어 채팅이 사라지지 않게*(원문: Admin 링크가 같은 탭 이동이라 채팅이 사라졌다)였다. 포털 안 뷰 탭은 포털을 떠나지 않으므로 목적은 그대로 지켜진다. 문구만 「관제는 포털 뷰 탭(독립 페이지는 새 탭 유지)」로 부기. Admin 링크(새 탭)는 그대로.
- **D-248 부기(`rel="opener"`)**: 관제 진입이 같은 창이 되면 opener 관계가 필요 없다 → `rel="noopener"`로 되돌림(머리글 링크 자체가 탭 전환으로 바뀌면 소멸).
- **D-341 ㊲ 「관제 페이지 안에 진단 화면을 만들지 않는다」 · ㊾ 「항상 새 탭」**: 관제가 포털 탭이 되면 진단 화면은 여전히 관제 *밖*(형제 탭)이다 — ㊲의 뜻(화면 두 벌 금지)은 지켜진다. ㊾는 포털 탭 진입에 한해 「같은 창 탭 전환」으로 개정. 192 §5 Q-1 표의 C 행에 이 두 개정이 적혀 있었고 사용자가 C를 골랐다.
- **D-245 「복제 기각」**: 제약으로 지킨다(모듈 한 벌 · W1).

**(나) C만으로는 정해지지 않는 것 — 사용자 결정 필요**

### K-1. 독립 관제 페이지(`/noise` · `/static/admin/noise.html`)를 남길까

D-338은 *인증 저장소(사용자 DB) 장애 때 사용자 요청은 503으로 막히고 운영자 비상 로그인(break-glass)만 진입로로 남는다*고 정했다. 포털은 일반 로그인 토큰만 쓰고 그 장애 때 안내 문구만 보인다(`app.js:146-153`). 그래서 **관제를 포털 안에만 두면, 사용자 DB가 죽었을 때 비상 계정으로 관제를 볼 길이 없어진다** — D-338과 충돌한다.

| 선택지 | 사용자가 보는 것 | 결정 충돌 · 비용 |
|---|---|---|
| **A. 운영자 관제 페이지는 그대로 두고, 사용자 관제 `/noise`는 포털 탭으로 보낸다**(권고) — `/noise` 주소·즐겨찾기는 `/?view=noise`로 넘긴다 | 일반 사용자: 포털 안 「노이즈 관제」 탭 하나 · 관리자: 포털 탭(읽기) + 관리자 대시보드의 운영자 관제(침묵·정책·실시간) · 비상 계정: 지금처럼 운영자 관제 | 충돌 없음(D-338 진입로 유지 · D-196 운영자 화면 유지) · 독립 페이지 1개 + 포털 탭 1개를 같은 모듈로 유지 |
| B. 두 독립 페이지를 모두 남기고 포털 탭을 하나 더 둔다 | 같은 사용자 관제가 두 곳(새 탭 `/noise` · 포털 탭) | 충돌 없음 · 진입로가 셋이라 「어디서 열었느냐」에 따라 동작이 또 갈린다(이번 문제의 재발) |
| C. 독립 페이지를 모두 없애고 포털 탭만 | 관제는 포털에서만 | **D-338 충돌**(사용자 DB 장애 시 비상 계정 관제 불가) · D-196 운영자 화면(침묵·정책·스트림)을 포털로 옮겨야 함 — 범위 큼 |

### K-2. 관리자(role=admin)가 포털 탭에서 보는 관제는 어느 모드인가

지금 포털 머리글 링크는 관리자에게도 사용자 모드 `/noise`(읽기 5종)를 연다. 운영자 기능(침묵 걸기·정책 보기·실시간 스트림·조건 로그)은 관리자 대시보드의 운영자 관제에만 있다(D-196 ⑤ · D-245).

| 선택지 | 결과 | 비용 |
|---|---|---|
| **A. 지금처럼 사용자 모드(읽기)**(권고) | 관리자도 포털 탭에서는 읽기만 · 침묵 등은 관리자 대시보드에서 | 작음 · 인가 경로 불변 |
| B. 관리자에게는 운영자 모드 | 포털 탭에서 침묵·스트림까지 | 운영자 마크업(id 28개) 이식 · 운영자 스트림 연결이 포털에 상주 · 운영자 시크릿 경로(`/admin/noise/*`)를 포털이 부름(D-245가 기각한 「사용자 화면이 `/admin/` 경로 호출」과 같은 모양) |

## 4. Wave

모든 Wave: 화면 변경이라 **매뉴얼 동반(D-255)** — U-44(노이즈 관제)·U-63(조사 대화)·U-70(장애 진단)·포털 머리글 장면·A-81 해당 문단 · 캡처 `noise-user`·`noise-drawer`·`noise-drawer-cause` 재촬영. 캐시 버전은 바꾼 스크립트마다. 과금·실 LLM 없음. 신규 동작은 플래그 `NOISE_CONSOLE_PORTAL_TAB_ENABLED`(기본 off = 현행 머리글 새 탭 링크 그대로 · 기동 시 1회 해석 · 기능 조회 응답으로 화면에 전달) 아래에 둔다 — 켜는 시점은 W4 검증 뒤 사용자 확인.

| Wave | 내용 | 대상 파일 | 결정 의존 | 검증 |
|---|---|---|---|---|
| **W0** | 실측(코드 0) — `noise-console.css` × 포털 `style.css` 선택자 충돌 · `document.body` 부착 3곳의 포털 위 층 순서 · 의존 테스트 11파일의 고정 문자열 목록 · 포털 상주 시 요청 수(30초·60초 타이머) · `/noise` 주소 진입 경로(즐겨찾기·통보·매뉴얼 링크 `noise.html` `/manual/user#u-44`) 목록 | 없음 | — | 결과를 이 계획서 §2에 추가 |
| **W1** | **동작 불변 리팩터**(D-337 — 동작 변경과 섞지 않음 · `refactoring` 스킬) — `noise.js`를 `window.NoiseConsole.mount(root, {mode, token, onAuthFail, onForbidden, onOpenInvestigation, liveView})` 모듈로. 독립 두 페이지는 로드 시 `mount(document.body, {mode: data-noise-mode …})`를 불러 **지금과 비트 동일**하게 동작(토큰 선택·리다이렉트·새 탭 진입 그대로) · `unmount`/`pause`/`resume`(타이머·팝오버·스트림 정리) 추가 | `src/static/js/noise.js` · `src/static/{noise.html,admin/noise.html}` | 없음 | 기존 관제 테스트 11파일 통과(고정값 갱신은 문자열 위치만) · Playwright로 두 독립 페이지 S1·S3 재실행 결과가 192 §4와 같음 |
| **W2** | 포털 「노이즈 관제」 뷰 탭(K-2 A — 관리자도 사용자 모드) — 사용자 모드 마크업은 §3 운용 규칙 3(한 벌 · W0에서 정한 방식) · 첫 활성 때 `mount`(지연) · 탭이 숨으면 `pause`, 보이면 `resume` · 인증은 포털(`getAuthHeaders` · 401 → `redirectToLogin` · 403 → 안내) · 머리글 링크 → 탭 전환(플래그 on) · `?view=noise` 진입 | `src/static/{index.html,css/style.css}` · `src/static/js/app.js` · `src/config/`(플래그) · 기능 조회 라우트 | K-2 A 확정 | node 단위(mount 인자·pause) · Playwright: 머리글 → 창 수 1→1 · 숨김 동안 관제 요청 0 · 답변 스트리밍 중 탭 전환 |
| **W3** | 관제 탭 → 조사 진입 같은 창 — 포털 탭의 서랍 버튼은 `onOpenInvestigation` → `openInvestigationEntry`(192 W2 문구 규칙 · 장애 진단 켜짐 판정은 포털 `diagnosisOn()`) · 독립 운영자 페이지는 종전(새 탭 · 192 W1 Q-3로 break-glass 숨김) · D-341 ㊾·D-248 ① 부기 | `noise.js` · `app.js` · `docs/decisions/{D-341,D-248}.md` | 192 W2 완료 | Playwright: 관제 탭 서랍 → 장애 진단/조사 대화 탭 · 창 수 1→1 · 두 번 눌러도 1 |
| **W4** | 독립 페이지 정리(K-1 B — 두 독립 페이지 유지 · `/noise` 넘김 없음) — 포털 안 `/noise` 링크 0개 확인 · 독립 페이지 조사 진입 버튼에 「새 탭」 표지(§3 운용 규칙 2) · 사용자 관제 「질의응답으로」 링크(192 P10)·머리글 `rel="opener"`(192 P12) 정리 · 매뉴얼·캡처 · 계획 끝 회귀 1회(`regress.py --base <계획 시작 SHA> --files …`) | `src/static/{noise.html,index.html}` · `src/static/js/noise.js` · `scripts/manual/*` · `src/static/manual/*` | K-1 B 확정 | Playwright 진입 경로별 창 수(머리글 · `/noise` 주소 · 관리자 대시보드 → 운영자 관제 · break-glass · 인증 저장소 장애 503 상태에서 break-glass 운영자 관제 접근) · `pytest tests/test_manual` |

## 5. 수용 기준

1. 플래그 off면 머리글 링크·두 독립 관제 페이지·관제 진입이 지금과 비트 동일하다(W1 이후 기존 테스트 전건 통과). **해석(2026-10-26 팀 리드)**: 「비트 동일」은 여는 곳(새 탭) 기준이다 — 독립 페이지 「(새 탭)」 표지(기준 7)와 머리글 `rel="opener"→"noopener"`(192 P12 · `window.opener` 읽는 코드 0)는 의도된 변경이다.
2. 플래그 on이면 포털 머리글 「노이즈 관제」가 같은 창의 뷰 탭으로 열리고, 그 탭의 서랍에서 조사를 열면 같은 창의 장애 진단(꺼짐이면 조사 대화) 탭으로 전환된다 — 진입 경로마다 창 수 1→1.
3. 관제 탭이 숨겨져 있는 동안 관제 재동기화·집계 요청이 나가지 않는다.
4. 관제 코드는 한 벌이다(`noise.js` 하나 · 사용자 모드 마크업도 `noise.html` 한 곳 — 포털 템플릿 사본 0 · K-1 B 운용 규칙 3).
5. 인증 저장소 장애(503) 중에도 break-glass 운영자 관제에 들어갈 수 있다(K-1 B).
7. 포털 안에서 독립 `/noise`로 가는 링크가 0개이고(플래그 on · **기능 조회 응답 뒤** — 응답 전·조회 실패 중에는 플래그 off와 같은 링크가 폴백으로 남는다 · 검증 m-2 · 플래그 off 비트 동일과 맞바꿈), 독립 페이지의 조사 진입 버튼은 「새 탭」 표지를 단다(K-1 B 운용 규칙 1·2).
6. 매뉴얼·캡처가 새 동선과 맞다(`pytest tests/test_manual`).

## 6. 결과 (2026-10-26 · 미커밋)

| Wave | 변경 | 검증 |
|---|---|---|
| W0 | §2.1 실측(코드 0) | — |
| W1 | `noise.js` → `window.NoiseConsole.mount(root, opts)`(본문 들여쓰기 불변 · 한 인스턴스 · `body[data-noise-mode]`일 때만 자동 mount) · 핸들 `pause/resume/unmount` · document 범위 조회 → mount 루트 · v=22 | 의존 14파일 전후 1,138 passed 동일 · 새 `test_ui_plan193_w1_mount.py` 14 · 실브라우저 두 독립 페이지 전후 JSON 동일 |
| W2+W3 | 플래그 `NoiseGateConfig.console_portal_tab_enabled` · 기능 조회 `noise_console_portal_tab` · 설정 카탈로그·도움말 · 포털 `#noiseTab`/`#noiseView` · 조각 조회(`/static/noise.html` DOMParser — 사본 0) · 첫 활성 때 mount(사용자 모드) · 숨김 `pause`/보임 `resume` · `?view=noise` · 서랍 진입 → `openInvestigationEntry`(같은 창) · `.noise-view` 층 z 130~150 | 실브라우저 66/66 · 창 수 1→1 · 숨김 중 관제 요청 0 |
| W4 | 독립 페이지 조사 진입 「(새 탭)」 표지 + 머리 버튼 `title` · 머리글 `rel="opener"→"noopener"` · 매뉴얼 U-10·10장 머리·U-44·U-63·U-70·A-42·A-81·신규 A-84 · 캡처 재촬영 3 + 신규 `noise-portal-tab` · `capture.py` `merge` 라우트 옵션 | `pytest tests/test_manual` 691 · 새 `test_ui_plan193_w4_standalone.py` 10 · `test_plan193_w4_break_glass.py` 5(D-338 실 라우터) · 실브라우저 53/53 |
| 검증·교정 | Critical 0 · Major 1(M-1 스트리밍 중 거부 사유가 숨은 `#chatError`에만) → 관제 뷰 일시 안내 · m-1 mount 실패 안내 · m-3 pause 때 서랍 브리핑 폴러 정지 · m-4 토큰 게터 · m-5 U-44 문구 · m-6 resume 차트 재렌더 · m-2는 문서화(기준 7) | 새 `test_plan193_verify.py` · 교정 지정 271/확장 1,011 passed |

- **계획 끝 모듈 단위 회귀**(`--base 84b400f5` · 39파일): rc 0 · 본체 통과 2,812 · 실패 1(`test_settings_help.py::test_t2_curated_groups_have_full_coverage` — 원래 실패) · 건너뜀 3 · arch/overfit/config_fallback/ruff/mypy 통과 · R16 미발생. 권고 블록: `[전체 회귀 권고] 사유: 허브 모듈 변경(전이 import 영향 ≥ 85%) — src.config.alarm 88%`. 범위: 모듈 단위 — 전체 미실행.
- **교정분 회귀**(17파일): 본체 통과 1,874 · 「이번 변경 탓」 1(`test_noise_dashboard_api.py::test_noise_js_stream_sends_token_in_header_not_url` — m-4가 바꾼 식 `"Bearer " + currentToken()`을 옛 정규식이 단언) → 정규식만 새 식에 맞춤(의도 불변) · 그 모듈 재실행 153 passed · `docs/18` 기록. 범위: 모듈 단위 — 전체 미실행.
- **이탈**: ① §2 행 번호는 194 W5 반영 뒤 기준(noise.js 3,435줄 · v21)으로 다시 셈 ② W2·W3은 같은 파일(`app.js`)이라 한 implementer로 묶음 ③ 「비트 동일」 해석은 여는 곳 기준(기준 1) ④ m-2는 코드 대신 기준 7 문구로 ⑤ 「새 탭」 표지는 버튼 문구 「(새 탭)」 + 머리 버튼 `title` — 수동 모드 버튼은 `briefing-view.js` 범위 밖이라 `title` 없음 ⑥ 관리자 설정 화면 항목 A-84 신설 · `config/settings_help` 항목 추가 ⑦ `capture.py` `merge` 옵션 추가(캡처용) ⑧ 192 P10(「질의응답으로」)은 코드 변경 없이 플래그로 해소(독립 페이지는 `data-noise-standalone` 표지만).
- **잔여**: m-2 응답 전 폴백 링크 · resume마다 BriefingView 300초 상한 창 재시작 · 다크 테마 미캡처 · 조사 id 형식 오류 거부 무안내 · 403 뒤 주기 갱신 계속 · 플래그 켜는 시점(사용자 확인).
