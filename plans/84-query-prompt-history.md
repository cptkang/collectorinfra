# 84. 질의 프롬프트 이력 — 로컬 목록 탭 + 서버 감사 경로 복구

> **작성일**: 2026-08-28
> **성격**: 구현 계획(implementation-ready) · **상태: 구현 완료(2026-08-29 · D-183 등재) → UI 개정 완료(2026-08-31 — 세 번째 탭에서 **접이식 왼쪽 사이드바**로, D-183 개정 등재)** — SDD 산출물 `CAPABILITY-MAP-84.md` · `SPEC-query-history-ui.md` · `SPEC-query-audit-path.md` · `tasks/plan-84.md` · `tasks/todo-84.md`. 신규 테스트 17건 · arch 0 · 기준선 대비 신규 실패 0
> **요청 취지**: 사용자가 질의했던 프롬프트 내용을 **별도로 저장하여 목록으로 볼 수 있게** 한다.
> **사용자 확정(G-1~G-3, 2026-08-28)**: G-1 저장 위치 = **로컬 우선 + 서버 감사 보강** ·
> G-2 저장 항목 = **프롬프트 텍스트만** · G-3 목록 위치 = ~~세 번째 탭~~ → **접이식 왼쪽 사이드바**(2026-08-31 개정)
> **관련 결정**: D-182(뷰 탭 — 이 계획이 배선을 재사용한다) · D-178·D-180(개인 선호는 브라우저 ·
> 이번 저장 위치 결정의 전례) · D-129(설정 카탈로그 SSOT) · D-139(패키지 경계)
> **신규 결정**: **D-183**(질의 프롬프트 이력 — 목록은 브라우저, 감사는 라우트) — 본문 등재 완료.
> ※ 채번 실측 2026-08-28 — `## D-` 헤더 최댓값 **182** · 「변경 이력」 표 최댓값 **182** ·
> 「채번 이력」 표 확인(D-158·D-163~D-168 미등재 예약, 재사용 금지) → **183**.
> 예약은 `docs/02_decision.md` 「채번 이력」 표에 등재해야 효력이 있다(D-161 부기).
> **실측 기준**: 아래 모든 `file:line`·수치는 2026-08-28 현 브랜치(`multiintent`)에서 직접 확인했다.

---

## 0. 요약

요청은 한 문장이지만 실측해 보면 **서로 다른 두 결손**이 겹쳐 있다.

| | 결손 | 지금 상태 | 이 계획의 트랙 |
|---|---|---|---|
| 사용자 | 내가 뭘 물었는지 **다시 볼 수 없다** | `promptHistory`가 메모리 배열이라 새로고침하면 사라지고, ↑↓ 탐색 외에 목록 UI가 없다 | **트랙 A** (로컬 저장 + 탭) |
| 운영자 | 관리자 감사 화면에 **질의가 안 보인다** | `AuditService.log_user_request`가 **정의만 있고 호출부 0건** — DB `audit_logs`에 질의 이벤트가 한 건도 안 들어간다 | **트랙 B** (감사 경로 복구) |

두 트랙은 **독립적이며 병렬 착수 가능**하다. 트랙 A만으로도 사용자 요청은 충족된다.
트랙 B는 "별도로 저장"의 운영 측면(보존·조회·감사)을 채우는 보강이며, 사용자 확정 G-1의
후반부("서버 감사 보강")에 해당한다.

**중요한 전제**: 현재 **인증이 꺼져 있다**(`src/config.py:457` `enabled: bool = False`, `.env`·
`.encenv` 어디에도 `AUTH_ENABLED` 없음 → `src/api/dependencies.py:238`이 `ANONYMOUS_USER` 반환).
그래서 서버 저장은 **사용자 구분이 불가능**하다. 이것이 G-1에서 목록 자체를 서버가 아니라
브라우저에 두기로 한 이유다. 트랙 B의 DB 기록도 `AUTH_ENABLED=true` 전까지는 전부
`anonymous`로 남는다(§2.4에 표기 의무로 고정).

---

## 1. 실측 — 지금 무엇이 있고 무엇이 없는가

### 1.1 프론트엔드: 메모리 히스토리만 있다

| 항목 | 위치 | 실측 내용 |
|---|---|---|
| 저장소 | `src/static/js/app.js:250` | `var promptHistory = []` — **메모리 전용**. localStorage 저장·복원 코드 없음 |
| 기록 지점 | `app.js:606-609` | `handleSend()` 안에서 연속 중복만 걸러 `push`. **성공/실패와 무관하게 전송 시점에 기록** |
| 탐색 | `app.js:437-477` | ↑/↓ 키로 역순 탐색(`historyIndex`), 진입 전 입력값은 `savedCurrentInput`에 보존 |
| 목록 UI | — | **없음** |

`handleSend`(`app.js:579`)는 전송의 **단일 진입점**이다 — 전송 버튼(`:369`), Enter(`:380`),
웰컴 힌트 버튼(`:432`) 세 곳이 모두 이 함수를 부르고, 파일 업로드 경로도 이 함수를 지난다
(`:1441` 주석이 근거 — `handleSend`가 `clearFile()`로 입력창 파일을 비우므로 하위 경로에서
미리 캡처한다). 따라서 **기록 지점을 늘릴 필요가 없다.**

### 1.2 서버: 파일 감사에는 남고, DB 감사에는 안 남는다

```
src/nodes/input_parser.py:157
  └─ src/security/audit_logger.py:118  log_user_request()   →  logs/audit-YYYY-MM-DD.jsonl
src/security/audit_service.py:126      log_user_request()   →  호출부 0건  ✗
```

- 파일 감사는 **실제로 동작 중**이다. 오늘자 `logs/audit-2026-08-28.jsonl` 73KB, 엔트리 형식:
  `{"timestamp": "...", "event": "user_request", "user_query": "서버 목록", "output_format": "text", "has_file": false}`
- `user_id`·`thread_id`는 `None`이면 `to_dict()`가 제외하므로(`audit_logger.py:44`) **인증 off인
  지금은 아예 필드가 없다**.
- 반면 `AuditService.log_user_request`(`audit_service.py:126`)는 **어디서도 호출되지 않는다**
  (`grep -rn "audit_service" src/` 결과: `server.py`의 생성, `user_auth.py:70`·`admin.py:398`의
  `log()` 직접 호출뿐). 그래서 관리자 대시보드 "감사 로그" 탭(`dashboard.html:60`,
  `admin.js:1421`·`:1450`)은 `audit_logs` 테이블을 읽는데 **질의 이벤트가 거기 없다**.

### 1.3 저장소 인프라는 이미 있다

- Postgres 풀: `src/api/server.py:301` — `config.auth.auth_db_url or config.db_connection_string`
  (실제 운영값은 `.env:49` `postgresql://…@localhost:5433/infra_db`), `AUTH_ENABLED` **여부와
  무관하게** 풀·테이블을 만든다(`server.py:300` 주석·`:331` `_ensure_auth_tables`).
- 테이블: `ddl/auth_tables.sql` — `auth_users`, `audit_logs`(+ `user_id`·`event_type`·`created_at` 인덱스).
- 저장소 구현: `src/infrastructure/audit_repository.py` `PostgresAuditRepository.log_event/query_logs`.
- **`AuditService.log`는 JSONL과 DB에 둘 다 쓴다**(`audit_service.py:43`·`:51`), 기본값은
  `jsonl_enabled=True`·`db_enabled=True`(`config.py:544-545`, `.env`에 `AUDIT_*` 없음).
  → **트랙 B에서 이중 기록을 반드시 다뤄야 한다**(§2.2).

### 1.4 뷰 탭 배선(D-182)은 3번째 탭을 받을 준비가 거의 되어 있다

- 탭 순회는 `data-view` 기반 전수 순회다 — `app.js:2493`(`querySelectorAll(".view-tab")`),
  `:2536`(클릭 바인딩). **탭을 늘려도 이 두 곳은 수정이 필요 없다.**
- 반면 `setActiveView`(`app.js:2488-2507`)는 `chatView`/`alarmView` **두 개를 하드코딩**한
  toggle이다 → 뷰 등록 방식으로 일반화가 필요하다(§2.1 A3).
- CSS `.view-tabs`·`.view-tab`·`.view-hidden`(`style.css:302-378`)은 탭 개수에 무관하다.
- 인증 게이트도 이미 뷰 단위다 — `style.css:481-485`가 `body.auth-pending > .alarm-view`까지
  숨긴다. **새 뷰를 이 선택자 목록에 추가하지 않으면 로그인 전에 이력 목록이 잠깐 노출된다.**

### 1.5 질의 진입 엔드포인트는 4개다 (트랙 B 대칭 주입 대상)

| 엔드포인트 | 위치 | 질의 출처 |
|---|---|---|
| `POST /query` | `src/api/routes/query.py:727` | `body.query` |
| `POST /query/stream` | `query.py:842` | `body.query` |
| `POST /query/file` | `query.py:1159` | `Form(query)` |
| `POST /query/file/stream` | `query.py:1402` | `Form(query)` |

프론트가 실제로 쓰는 것은 `/query/stream`(`app.js:978`)·`/query`(`:1402`)·
`/query/file/stream`(`:1459`)이지만, **비대칭 주입은 이 저장소의 반복 실수 유형**이므로
(CLAUDE.md 「단일/멀티 경로 대칭」) 4개 전부에 넣고 테스트로 고정한다.

---

## 2. 설계

### 2.1 트랙 A — 로컬 이력 저장 + "질의 이력" 탭

#### A1. 저장소 (localStorage)

```js
var HISTORY_KEY = "query_prompt_history";   // 전례: alarm_receive_enabled(app.js:2805)
var HISTORY_MAX = 200;                       // 상한
// 저장 형식: [{ q: "질의문", t: 1756... }, ...]  최신이 배열 끝
```

- **항목은 질의문과 시각뿐이다**(G-2). SQL·결과·DB 식별자는 저장하지 않는다.
- **같은 질의를 다시 보내면 기존 항목을 제거하고 최신으로 재삽입**한다 — 같은 질의가 목록을
  도배하면 목록의 쓸모가 사라진다. ↑↓ 탐색용 `promptHistory`는 **현행 유지**(연속 중복만 방지):
  두 자료구조는 목적이 다르다(탐색은 "직전에 친 순서", 목록은 "무엇을 물었나").
- 상한 초과 시 **가장 오래된 것부터** 제거한다. 200건 × 평균 60자 ≈ 24KB로 localStorage 5MB
  대비 무시 가능하지만, 상한 없는 누적은 이 저장소의 금기다(`NOISE_FEEDBACK_STORE_MAX_LINES`
  전례).
- 모든 읽기·쓰기를 `try/catch`로 감싼다 — 사생활 모드·용량 초과에서 `localStorage`는 **던진다**.
  실패해도 질의 전송은 계속돼야 한다(감사 실패가 요청을 막지 않는 것과 같은 원칙).

#### A2. 기록 지점 — 늘리지 않는다

`app.js:606`의 기존 `promptHistory.push` 블록에 **한 줄 덧붙인다**. `handleSend`가 단일
진입점이므로(§1.1) 스트리밍·비스트리밍·파일 업로드 세 경로가 모두 여기를 지난다.
**전송 성공 여부와 무관하게 저장한다** — 기록 대상은 "무엇을 물었는가"이지 "성공했는가"가 아니다
(G-2가 결과를 저장하지 않기로 한 것과 같은 이유).

#### A3. `setActiveView` 일반화

지금:

```js
if (chatView) chatView.classList.toggle("view-hidden", isAlarm);
if (alarmView) alarmView.classList.toggle("view-hidden", !isAlarm);
```

→ 뷰 등록 테이블로 바꾼다(`{ chat: chatView, alarm: alarmView, history: historyView }`).
탭이 넷째로 늘어도 이 함수는 다시 손대지 않는다. 알람 탭 복귀 시의 스크롤 복원 분기
(`app.js:2501-2506`)는 **`view === "chat"`일 때로 조건을 바꾼다** — 지금은 `else`라서 이력 탭에서
채팅으로 돌아올 때가 아니라 **이력 탭으로 갈 때** 복원이 도는 버그가 생긴다.

#### A4. 마크업 (`index.html`)

- 탭: `<button class="view-tab" data-view="history" aria-controls="historyView">질의 이력</button>`
  — **배지 없음**(알람과 달리 "놓칠" 성질이 아니다).
- 뷰: `<div class="history-view view-hidden" id="historyView">` — 알람 뷰(`index.html:217-236`)와
  같은 골격(헤더 + 카운트 + 액션 버튼 + 목록 + 빈 상태)을 쓴다.
- **`style.css:481-485`의 `body.auth-pending >` 목록에 `.history-view`를 추가한다**(§1.4).

#### A5. 목록 렌더

- **최신순**(알람 뷰와 같은 관행).
- 한 행 = `MM-DD HH:mm` + 질의문 1줄(말줄임, `title` 속성에 전문) + `↩`(입력창에 채우기) + `✕`(삭제).
- **클릭은 입력창에 채우기만 한다 — 즉시 전송하지 않는다.** 이력의 질의가 지금도 유효하다는
  보장이 없고(존 이름 변경·대상 서버 폐기), 오전송은 되돌릴 수 없다. 채운 뒤 **채팅 탭으로
  전환 + 입력창 포커스**까지가 한 동작이다. (같은 취지의 전례: `plans/75` §2.2 — 기획 중 버튼을
  `disabled`로 두어 오전송을 원천 차단.)

#### A6. 검색·비우기

- 검색: 클라이언트 부분일치(대소문자 무시), 입력 즉시 필터. 서버 왕복 없음.
- "모두 지우기": 알람 뷰 전례(`app.js:2539-2547`)를 따라 **확인 대화상자 없이** 비우고,
  목록이 비면 버튼을 감춘다(`updateAlarmViewState`의 `display` 토글과 동일 패턴).
- 개별 삭제 `✕`도 같은 경로로 저장소를 갱신하고 다시 그린다.

#### A7. 빈 상태

`.alarm-empty` 패턴 재사용 — 아이콘 + "저장된 질의가 없습니다 / 질의를 보내면 여기에 쌓입니다".

### 2.2 트랙 B — 감사 경로 복구 (★ 이중 기록을 반드시 해소한다)

`AuditService.log`는 **JSONL과 DB에 둘 다 쓴다**(§1.3). 그래서 라우트에서 그냥 호출하면
`input_parser`의 파일 기록과 합쳐져 **파일에 같은 질의가 두 번** 남는다. 세 안을 검토한다.

| 안 | 내용 | 판정 |
|---|---|---|
| (a) | 라우트에서 `AuditService.log_user_request` 호출 + `input_parser` 호출 **유지** | **기각** — 파일 이중 기록. 감사 파일의 건수가 실제 질의 수와 달라지면 그 파일로 하는 모든 집계(예: `plans/82` §5.1의 918건 비용 실측)가 오염된다 |
| (b) | 라우트에서 `audit_repo.log_event`를 **직접** 호출(서비스 우회) | 차선 — 중복은 없지만 `AuditService`를 만든 이유(형식 통일·`client_ip`·`request_id`)를 우회한다 |
| **(c)** | **라우트로 일원화** — 4개 진입점에서 `AuditService.log_user_request` 호출, `input_parser`의 호출은 제거, **CLI 경로에는 동등한 파일 기록을 옮겨 심는다** | **채택** |

(c)를 택하는 이유:

1. **감사의 주체는 "요청 수신"이지 "파싱 노드"가 아니다.** 노드는 `app.state`에 접근할 수 없어
   `client_ip`·`request_id`·`session_id`를 영원히 채울 수 없다 — `AuditService.log_user_request`가
   호출부 없이 남아 있던 것도 이 계층 문제의 결과로 보인다.
2. **노드에서 감사 I/O를 하면 그래프 실행 구조에 기록이 묶인다.** 지금은 `input_parser`가 첫
   노드라 1회지만, 재실행·분기가 생기면 조용히 중복된다.
3. 파일+DB가 한 지점에서 나오므로 **두 저장소의 건수가 일치**한다.

**CLI 보존**: `src/main.py:71`이 `graph.ainvoke`를 직접 부르는 CLI 모드가 있다. `input_parser`에서
호출을 빼면 이 경로의 감사가 사라지므로, **`main.py`의 질의 실행 직전에 파일 감사
(`audit_logger.log_user_request`) 호출을 옮겨 심는다.** API·CLI 어느 쪽으로 들어와도 감사 1건이
남는다는 것이 이 트랙의 수용 기준이다.

**user_id 표기 의무**: 인증 off인 동안 DB 이력은 전부 `anonymous`다(§0). 관리자 감사 화면에서
`user_request` 행의 사용자가 전부 같게 보이는 것은 **버그가 아니라 설정 상태**이므로,
`admin.js` 감사 탭에 그 사실을 한 줄로 표기한다(D-179가 설정 UI에 경계를 표시한 것과 같은 방식).

### 2.3 하지 않는 것 (범위 밖)

- **서버 이력 조회 API·사용자별 이력 테이블 신설** — G-1이 목록을 브라우저에 두기로 확정했다.
  인증을 켠 뒤 기기 간 동기화가 실제 요구가 되면 그때 별건으로 계획한다.
- **`AUTH_ENABLED=true` 전환** — 이 계획의 선행조건이 아니다. 운영 영향(로그인 강제·기존
  사용자 계정 시딩)이 커서 별도 결정이 필요하다.
- **즐겨찾기·이름 붙여 저장·질의 공유** — 요청에 없다. 저장 형식(`{q,t}`)에 필드를 더하면
  나중에 얹을 수 있게만 열어 둔다.
- **질의문 마스킹** — 자연어 질의는 사용자가 직접 친 문장이고, 저장 위치가 그 사람의
  브라우저다. 마스킹보다 **"모두 지우기"의 존재**가 공용 PC에 대한 실질적 대응이다.

### 2.4 경계·주의

1. **모바일 탭 3개** — `--view-tabs-h`가 40px로 줄고(`style.css:3565`) 탭이 셋이 된다.
   414px 폭에서 넘치는지 **구현 중 실제 렌더로 확인**하고, 넘치면 `.view-tabs .container`에
   `overflow-x: auto`를 준다(본문 가로 스크롤은 만들지 않는다).
2. **이력 탭은 권한과 무관** — `setAlarmTabVisible`(`app.js:2529`)은 알람 탭만 감춘다.
   이력은 로컬 데이터라 누구에게나 보인다.
3. **`view-hidden`인 동안 목록을 다시 그리지 않는다** — 탭을 열 때 렌더한다(알람 뷰가 숨은
   동안 `scrollHeight`가 0이 되는 D-182 주의 ②와 같은 계열의 문제를 미리 피한다).
4. **저장 형식 버전** — 지금은 `{q,t}`뿐이라 마이그레이션이 필요 없지만, 파싱 실패(옛 형식·
   손상된 JSON)는 **조용히 빈 배열로 폴백**하고 콘솔 경고만 남긴다.

---

## 3. 작업 단위

| WU | 트랙 | 내용 | 파일 |
|---|---|---|---|
| **W1** | A | localStorage 저장소 헬퍼(`loadHistory`/`saveHistory`/`pushHistory`/`removeHistory`/`clearHistory`) + 상한·중복·try-catch | `app.js` |
| **W2** | A | `handleSend` 기록 한 줄 배선 | `app.js:606` |
| **W3** | A | 탭·뷰 마크업 + `auth-pending` 선택자 확장 + 뷰 CSS | `index.html`, `style.css` |
| **W4** | A | `setActiveView` 뷰 등록 테이블 일반화 + 스크롤 복원 조건 정정 | `app.js:2488` |
| **W5** | A | 목록 렌더·검색·개별 삭제·모두 지우기·빈 상태·채우기 후 채팅 탭 전환 | `app.js` |
| **W6** | B | 4개 진입점에 `AuditService.log_user_request` 대칭 주입 | `query.py:727·842·1159·1402` |
| **W7** | B | `input_parser` 감사 호출 제거 + `main.py` CLI 경로로 이설 | `input_parser.py:157`, `main.py` |
| **W8** | B | 관리자 감사 탭에 "인증 off = 전원 anonymous" 표기 | `admin.js` |
| **W9** | — | 테스트 신설 + 캐시 버스팅(`style.css?v=`·`app.js?v=` 증가) | `tests/`, `*.html` |

착수 순서: **W1→W2→W3→W4→W5**(트랙 A, 이것만으로 요청 충족) → **W6→W7→W8**(트랙 B) → **W9**.
W7은 W6 직후에 **같은 커밋에서** 해야 한다 — 사이에 벌어지면 그 구간에 파일 이중 기록이 남는다.

---

## 4. 테스트 계획

브라우저 e2e가 이 환경에서 돌지 않으므로, D-182의 `tests/test_api/test_ui_alarm_view.py`와 같은
방식으로 **계약이 되는 지점만 정적으로 고정**한다. 신규 파일 `tests/test_api/test_ui_query_history.py`.

| # | 대상 | 단언 |
|---|---|---|
| 1 | `index.html` | `data-view="history"` 탭과 `id="historyView"` 컨테이너가 있다 |
| 2 | `index.html` | 탭이 **3개**다(chat·alarm·history) — 하나가 조용히 빠지는 회귀 차단 |
| 3 | `style.css` | `body.auth-pending >` 목록에 `.history-view`가 있다(로그인 전 노출 차단) |
| 4 | `app.js` | `HISTORY_KEY`·`HISTORY_MAX` 상수가 있고 상한이 유한하다 |
| 5 | `app.js` | 기록이 `handleSend` 안, `promptHistory` 인접에서 일어난다(기록 지점 분산 차단) |
| 6 | `app.js` | 이력 항목 클릭 경로에 **전송 호출이 없다**(입력창 채우기만 — A5) |
| 7 | `app.js` | `setActiveView`가 뷰를 하드코딩 toggle하지 않는다(등록 테이블 사용) |
| 8 | `query.py` | **4개 진입점 전부** `log_user_request`를 호출한다(대칭 — 비대칭 회귀 차단) |
| 9 | `input_parser.py` / `main.py` | 노드에는 감사 호출이 **없고**, CLI 경로에는 **있다**(이중 기록 차단) |
| 10 | 서버 | `AuditService.log_user_request` 호출 시 `audit_repo.log_event`가 `event_type="user_request"`로 1회 불린다(가짜 repo) |
| 11 | 서버 | 감사 기록이 실패해도 질의 응답이 성공한다(감사 실패가 요청을 막지 않는다) |

**회귀 기준선**: 착수 전 `git worktree add <dir> HEAD`로 격리 사본을 만들어 기준선을 뜬다
(`git stash` 금지 — CLAUDE.md). 현재 공유 트리의 `tests/test_api`는 **8 failed**이고 클린 HEAD는
**10 failed**로, 둘 다 이 계획과 무관한 기존 실패다(2026-08-28 실측).

---

## 5. 수용 기준

1. 질의를 보낸 뒤 **새로고침해도** "질의 이력" 탭에 그 질의가 남아 있다.
2. 목록에서 항목을 클릭하면 **입력창에 채워지고 채팅 탭으로 전환**되며, **전송되지는 않는다**.
3. 검색어를 치면 목록이 즉시 걸러진다.
4. 개별 삭제·모두 지우기가 즉시 반영되고, 비면 빈 상태 안내가 나온다.
5. 사생활 모드(localStorage 차단)에서 **질의 전송이 정상 동작**한다(목록만 비어 있다).
6. 질의 1건당 `logs/audit-*.jsonl`에 `user_request` **정확히 1건**, DB `audit_logs`에 **1행**.
7. 관리자 "감사 로그" 탭에서 `user_request` 이벤트가 조회된다.
8. `python scripts/arch_check.py --ci` exit 0 · 클린 기준선 대비 **신규 실패 0**.

---

## 6. 열린 질문 (착수 전 확인 불필요 — 구현 중 판단)

- 목록 행에 **존/DB 배지**를 붙일지 — G-2가 "프롬프트 텍스트만"이라 **붙이지 않는다**.
  나중에 필요해지면 저장 형식에 필드를 더한다(§2.4 ④).
- 상한 200건이 실제 사용량에 맞는지 — 감사 로그로 일 평균 질의 수를 재서 조정한다.
  (참고: `logs/audit-2026-08-28.jsonl` 73KB, `2026-08-27` 40KB)
