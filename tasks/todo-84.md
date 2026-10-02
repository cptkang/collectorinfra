# TODO — Plan 84 (질의 프롬프트 이력)

> 계획: `tasks/plan-84.md` · 스펙: `SPEC-query-history-ui.md`·`SPEC-query-audit-path.md`

## 트랙 A — `query-history-ui`

- [x] **A1. localStorage 저장소 헬퍼**
  - Acceptance: `HISTORY_KEY`·`HISTORY_MAX`(=200) 상수 + `loadHistory`/`saveHistory`/`pushHistory`/
    `removeHistoryAt`/`clearHistory`. 모든 접근이 try/catch. 같은 질의는 제거 후 최신 재삽입.
    상한 초과 시 오래된 것부터 제거.
  - Verify: `node --check src/static/js/app.js`
  - Files: `src/static/js/app.js`

- [x] **A2. 기록 배선 (지점을 늘리지 않는다)**
  - Acceptance: `handleSend`의 기존 `promptHistory.push` 블록에서 `pushHistory(query)` 호출.
    전송 성공 여부와 무관. `promptHistory`(↑↓ 탐색)의 기존 동작은 **불변**.
  - Verify: `node --check` · 브라우저에서 질의 후 `localStorage.getItem("query_prompt_history")`
  - Files: `src/static/js/app.js`

- [x] **A3. 탭·뷰 마크업 + 인증 게이트**
  - Acceptance: `data-view="history"` 탭(배지 없음) · `id="historyView"` 컨테이너(헤더·카운트·
    검색·모두 지우기·목록·빈 상태) · `style.css`의 `body.auth-pending >` 목록에 `.history-view` 추가 ·
    `.history-view` 계열 스타일(알람 뷰 토큰 재사용).
  - Verify: `pytest tests/test_api/test_ui_query_history.py -q` (테스트 1·2·3)
  - Files: `src/static/index.html`, `src/static/css/style.css`

- [x] **A4. `setActiveView` 일반화**
  - Acceptance: 뷰 등록 테이블(`{chat, alarm, history}`)로 toggle. 스크롤 복원 분기를
    **`view === "chat"`** 조건으로 명시. 알람 배지 리셋 동작 불변.
  - Verify: `pytest tests/test_api/test_ui_alarm_view.py tests/test_api/test_ui_query_history.py -q`
  - Files: `src/static/js/app.js`

- [x] **A5. 목록 렌더·검색·재사용·삭제**
  - Acceptance: 최신순 · `MM-DD HH:mm` + 질의문 1줄(말줄임, `title`에 전문) · ↩(입력창 채우기 +
    채팅 탭 전환 + 포커스, **전송 없음**) · ✕(개별 삭제) · 검색 부분일치 · 모두 지우기 ·
    빈 상태 · 건수 표시 · 탭 열 때 렌더(숨은 동안 렌더 안 함).
  - Verify: `node --check` · headless Chrome 렌더 확인 · 테스트 4~9
  - Files: `src/static/js/app.js`

## 트랙 B — `query-audit-path`

- [x] **B1+B2. 라우트 대칭 주입 + 노드 제거 + CLI 이설** *(한 단위 — 나누면 이중 기록 구간 발생)*
  - Acceptance: `/query`·`/query/stream`·`/query/file`·`/query/file/stream` 4곳에서
    `audit_service.log_user_request` 호출(방어적 `getattr` + try/except + `logger.warning`) ·
    `input_parser.py`의 `log_user_request` 호출·임포트 제거 · `main.py` CLI 질의 실행 직전에
    `audit_logger.log_user_request` 호출 추가.
  - Verify: `pytest tests/test_api/test_query_audit_path.py tests/test_nodes -q` · `arch_check --ci`
  - Files: `src/api/routes/query.py`, `src/nodes/input_parser.py`, `src/main.py`

- [x] **B3. 관리자 감사 탭 인증 상태 표기**
  - Acceptance: 감사 로그 탭에 "인증 비활성 시 사용자가 전부 anonymous로 기록된다"는 안내가
    보인다(D-179의 경계 표시 방식).
  - Verify: `pytest tests/test_api/test_query_audit_path.py -q` (테스트 8)
  - Files: `src/static/js/admin.js` 또는 `src/static/admin/dashboard.html`

## 마무리

- [x] **C1. 테스트 신설**
  - Acceptance: `test_ui_query_history.py` 9건 · `test_query_audit_path.py` 8건 전부 통과.
  - Verify: `pytest tests/test_api -q`
  - Files: `tests/test_api/test_ui_query_history.py`, `tests/test_api/test_query_audit_path.py`

- [x] **C2. 캐시 버스팅**
  - Acceptance: `index.html`의 `style.css?v=`·`app.js?v=` 증가(다른 페이지는 CSS만).
  - Files: `src/static/index.html`, `src/static/login.html`, `src/static/register.html`,
    `src/static/admin/login.html`, `src/static/admin/dashboard.html`

- [x] **C3. 회귀 대조 + 문서**
  - Acceptance: 기준선 대비 **신규 실패 0** · `arch_check --ci` exit 0 ·
    `docs/02_decision.md`에 **D-183 본문 + 변경 이력 행** 등재 · 채번 이력 표를 "채번 완료"로 갱신 ·
    `plans/84` 상태를 "구현 완료"로 · `plans/INDEX.md` 갱신.
  - Verify: `pytest tests noise_gate --ignore=tests/e2e -q` 를 `baseline84.txt`와 대조
