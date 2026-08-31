# 83. 노이즈 캔슬링 피드백 루프 개선 + 알람 표시 레벨 선택 + 설정 UI 커버리지 완성

> **작성일**: 2026-08-28
> **성격**: 구현 계획(implementation-ready) · **상태: T1~T13 구현 완료(2026-08-28)** — SDD 산출물 `CAPABILITY-MAP-83.md` · `SPEC-alarm-feedback-loop.md` · `SPEC-alarm-view-level.md` · `SPEC-settings-ui-coverage.md` · `tasks/plan-83.md` · `tasks/todo-83.md`. 결정 **D-177~179 등재 완료**
> **요청 취지**: ① `docs/28` 운영자 가이드 작성 중 발견한 이슈 개선 ② **알람 수신이 UI에
> 보여지는 레벨을 사용자가 선택**하는 옵션 신설 ③ `.env` 옵션이 관리자 페이지에서 전부 UI로
> 정의 가능한지 검토 후 갭 개선
> **관련 결정**: D-048(4-티어 게이트)·D-048.11(E4 피드백 few-shot)·D-049(ack/incident 계측)·
> D-035(결정적=판단·LLM=주석)·D-129(설정 카탈로그 SSOT)·D-135(설정 즉시 반영 확대)·
> Plan 59 §17(알림 존 RBAC)
> **신규 결정 예약**: **D-177**(트랙 A 피드백 루프 신뢰성) · **D-178**(트랙 B 표시 레벨) ·
> **D-179**(트랙 C 설정 UI 경계). ※ 채번 실측 2026-08-28 — `## D-` 헤더 최댓값 174 · 변경 이력 표
> 최댓값 175 · 안내 라인 예약(D-105·115·134·158·163·**175=plans/81**·**176=plans/82**) 확인 → **177부터**.
> 예약은 `docs/02_decision.md` 안내 라인에 등재해야 효력이 있다(D-161 부기).
> **선행 문서**: `docs/28`(운영자 가이드 — 이 계획의 문제 정의 출처) · `docs/16`(게이트 테스트) ·
> `plans/52`(게이트 설계) · `plans/68`(설정 웹UI — 트랙 C의 기반)
> **실측 기준**: 아래 모든 `file:line`·수치는 2026-08-28 현 브랜치(`multiintent`)에서 직접 확인했다.

---

## 0. 요약

세 트랙은 서로 독립적이며 **병렬 착수 가능**하다. 우선순위는 A-1(보안) > B > A-나머지 > C다.

| 트랙 | 주제 | 작업 단위 | 코드 변경 규모(예상) |
|---|---|---|---|
| **A** | 피드백·ack 루프의 보안·정확성·성능 결함 9건 | A1~A9 | `noise_gate/infrastructure/feedback_store.py`, `src/api/routes/alarm.py`, `src/static/js/app.js` |
| **B** | 알람 UI 표시 레벨 사용자 선택 | B1~B5 | `src/api/routes/alarm.py`, `src/static/index.html`, `app.js`, (옵션) `noise_gate/application/nodes/alarm_notifier.py` |
| **C** | 설정 UI 커버리지 갭 | C1~C3 | `src/api/settings_catalog.py`, `src/static/js/admin.js` |

**트랙 C 선결론(중요)**: `.env`·`.env.example`의 **모든 키가 이미 카탈로그에 포함돼 있다 — 누락 0건**.
"UI로 정의 불가한 옵션"은 없다. 남은 갭은 **표시 품질(섹션 미분류 8건)**과 **경계 밖 설정 파일
(별도 프로세스 3종)**뿐이다. 실측 근거는 §3.1.

---

## 1. 트랙 A — 피드백·ack 루프 개선

### 1.0 발견 경위

`docs/28` 운영자 가이드를 쓰기 위해 피드백 경로를 전수 실측하면서 드러난 결함들이다. 기능이
"동작하지 않는" 것이 아니라, **운영에 올렸을 때 신뢰할 수 없게 되는** 항목들이다.

### 1.1 이슈 목록 (전부 실측 확인)

| # | 등급 | 이슈 | 근거 |
|---|---|---|---|
| **A-1** | **보안** | `/alarm/feedback`·`/alarm/incidents/{id}/ack`에 **존(zone) RBAC이 없다** | `alarm.py:1064`, `alarm.py:1122` — `require_user`만 요구. SSE(`alarm.py:938`)는 `alarm_zones_for_user`로 필터 |
| **A-2** | 기능 | UI가 `server_name`·`db_id`·`note`를 **전송하지 않아** 레코드에 항상 빈 값 | `app.js:2579` 요청 본문 5필드만. SSE payload에는 `db_id`·`server_name`이 이미 있다(`alarm_notifier.py:567`) |
| **A-3** | 정확성 | 저장 `pattern`은 **LLM 산출** `pattern_type`, 조회 `pattern`은 **결정적** `pre_classification` — 어긋나면 +1 가점 누락 | 저장 `alarm.py:1143`, 조회 `alarm_analyzer.py:244` |
| **A-4** | 감사 | 피드백에 **작성자가 없다**(ack는 `acked_by` 기록) — 오라벨 추적·회수 불가 | `feedback_store.py:66` 레코드 스키마에 주체 필드 없음 |
| **A-5** | 운영 | 라벨 **철회·수정 수단이 없다** — 오클릭이 영구 잔존 | `alarm.py` 라우트 목록에 삭제/수정 엔드포인트 없음 |
| **A-6** | 성능 | `find_similar`가 **매 알람마다 파일 전체를 동기 읽기**(async 노드 내 blocking), **회전·상한 없음** | `feedback_store.py:107` 전체 순회, `alarm_analyzer.py:245` await 없는 호출 |
| **A-7** | UX | 액션가능성 off인데 **버튼은 항상 보이고** 누르면 503 | `app.js:2508` 무조건 렌더, `alarm.py:1133` 503 |
| **A-8** | 관측 | **오억제(SUPPRESS) 확인이 파일 tail로만** 가능 — UI에 노출 경로 없음 | `alarm_notifier.py:633` SUPPRESS는 SSE 미발행 |
| **A-9** | 일관성 | 여러 운영자가 **상반된 라벨**을 남기면 최신 1건이 이김(집계·합의 없음) | `feedback_store.py:139` 가점 동점 시 최신 우선 |

### 1.2 작업 단위

#### A1. 존 RBAC 적용 (**최우선**)

피드백·ack 두 엔드포인트에 SSE와 **동일한** 존 판정을 넣는다.

```
src/api/routes/alarm.py
  submit_alarm_feedback:
    zones = alarm_zones_for_user(current_user, config.auth.enabled)
    if not zones: 403
    if body.db_id and db_id_to_zone(body.db_id) not in zones: 403
  ack_incident:
    incident 조회(또는 ack 결과)의 db_id로 동일 판정 → 403
```

- **설계 주의**: 현재 `AlarmFeedbackRequest.db_id`는 **선택 필드**이고 UI가 보내지 않는다(A-2).
  A-2를 먼저 해서 db_id를 항상 싣게 한 뒤, **db_id 없는 요청은 거부하지 말고 존 무판정으로 통과**
  시킨다(하위호환). 판정 강제는 A-2 배포가 안정화된 뒤 별도 스위치로 전환한다.
- `ack_incident`는 `incident_store`에 **id로 db_id를 조회하는 메서드가 없다** — `list_open`
  결과에 포함되는지 실측 후, 없으면 `IncidentStore`에 `get(incident_id)` 추가(포트=domain,
  구현=infrastructure).

#### A2. 피드백 요청 필드 보강

`app.js:2579` 요청 본문에 `db_id`·`server_name`을 추가한다(값은 이미 카드 `data`에 있다).
`note`는 **선택 입력**으로, 버튼 옆 접이식 한 줄 입력(최대 200자)을 붙인다 — 없으면 현행대로 빈 값.

> `note`는 few-shot 예시 줄에 그대로 붙어 LLM에 전달된다(`alarm_analyzer.py:203`). 입력란 옆에
> "민감정보·계정·키 입력 금지" 안내를 고정 노출한다.

#### A3. pattern 값 원천 통일

**결정적 값(`pre_classification`)을 단일 원천으로 삼는다** — LLM 산출값은 표시용으로만 쓴다.

1. SSE payload에 `pre_classification`을 추가(`_tier_sse_payload`·`_incident_open_payload`·
   `alarm.py:830` **3곳 모두** — 단일/멀티 경로 대칭 원칙).
2. UI는 `pattern_type` 대신 `pre_classification`을 피드백 본문에 싣는다.
3. 값이 없으면 빈 문자열(가점만 못 받고 후보 탈락은 없음 — 현행과 동일).

#### A4. 작성자 기록

`record_feedback(..., labeled_by: str = "")`를 추가하고 라우트가 `current_user`의 `sub`/`name`을
넘긴다(ack의 `acked_by` 전례 동일). **few-shot 프롬프트에는 싣지 않는다** — 감사·집계 전용이다
(작성자 이름이 LLM 판단에 개입할 이유가 없다).

#### A5. 라벨 철회

가장 단순하고 되돌리기 쉬운 안을 택한다: **tombstone append**(파일은 append-only 유지).

```
{"ts":…, "label":"retract", "target_ts":"<철회할 레코드 ts>", "alarm_name":…, "labeled_by":…}
```

- `find_similar`는 `_VALID_LABELS`("noise","valid")만 후보로 삼으므로 `retract` 줄은 **자동으로
  무시된다**(코드 변경 없이 안전). 다만 철회 대상 원본 레코드를 **후보에서 빼려면** 조회 시
  `retract` 집합을 먼저 수집해 `target_ts` 일치 레코드를 제외하는 로직이 필요하다(1패스 유지 가능).
- UI: 피드백 성공 후 메시지 옆 `취소` 링크(같은 카드 세션 안에서만 노출). 서버는
  `POST /api/v1/alarm/feedback/retract`.
- **대안(불채택)**: 파일 재작성(rewrite) — append-only 감사 원칙과 충돌하고 동시 쓰기 위험.

#### A6. 조회 성능·파일 증가 대응

측정 먼저, 최적화는 그 다음이다(추정 금지).

1. **계측**: `find_similar` 소요시간을 debug 로그에 남기고, 현재 파일 크기·라인 수를 기록한다.
2. **상한**: `NOISE_FEEDBACK_STORE_MAX_LINES`(신규, 기본 20000) 초과 시 **오래된 절반을
   `*.1` 파일로 이동**(단순 2세대 회전). 회전은 append 시점에만, 실패는 warning 후 무시(graceful).
3. **읽기**: 파일 끝에서부터 최대 N줄만 읽는 tail 방식으로 전환(최신 우선 랭킹과 정합).
   전체 스캔이 필요한 이유가 없다 — 가점 랭킹도 최신 구간에서 충분하다.
4. **blocking 회피**: 노드가 async이므로 `asyncio.to_thread`로 감싼다. 실패·타임아웃 시 빈 리스트
   (현행 graceful 유지).

> **주의**: 3의 tail 전환은 "오래된 유효 라벨이 후보에서 빠질 수 있다"는 동작 변경이다. 계획대로
> N=상한과 동일하게 두면 회전 전까지는 **현행과 동일 결과**다. 회귀 테스트로 고정한다.

#### A7. 버튼 가용성 사전 표시

`GET /api/v1/alarm/capabilities`(신규, 인증 필요)로 게이트 상태를 한 번에 내린다.

```json
{"feedback_enabled": false, "incident_tracking": false, "sse_bridge": true,
 "suppress_stream": false, "suppress_max_severity": 2}
```

- UI는 앱 기동 시 1회 조회해 캐시하고, `feedback_enabled=false`면 **버튼을 렌더하지 않는다**
  (또는 비활성 + "관리자가 비활성화함" 툴팁). 트랙 B의 레벨 셀렉트도 이 응답을 쓴다.
- 이 엔드포인트는 **불리언만** 노출한다(경로·시크릿·임계 노출 금지).

#### A8. 오억제 점검 경로 → **트랙 B로 이관**

SUPPRESS를 UI에서 보게 하는 것은 표시 레벨 기능의 일부다. §2.4에서 다룬다.

#### A9. 라벨 합의 표시 (최소안)

관리자 화면 `열린 사건` 탭 옆에 **`알람 피드백`** 탭을 추가해 `(alarm_name, resource_name)`별
`valid`/`noise` 카운트와 최근 라벨·작성자를 표로 보여준다(`GET /api/v1/alarm/feedback/summary`).
**판정 로직은 바꾸지 않는다** — 상충을 사람이 보고 판단하게만 한다.

### 1.3 트랙 A 검증

| 항목 | 방법 |
|---|---|
| A1 | 존 A 사용자 토큰으로 존 B의 `db_id` 피드백 → 403. 기존 존 일치 요청은 200(회귀 0) |
| A2·A3 | 피드백 1건 후 JSONL 레코드에 `db_id`·`server_name`·결정적 `pattern` 존재 확인 |
| A4 | 레코드에 `labeled_by` 존재. few-shot 렌더 문자열에는 **미포함** 단언 |
| A5 | 라벨 → 철회 → `find_similar` 결과에서 해당 레코드 제외 확인 |
| A6 | 20k+ 라인 픽스처로 tail 읽기 결과가 전체 스캔과 동일함을 단언. 회전 후 `.1` 생성 확인 |
| A7 | `enable_llm_actionability=false`에서 버튼 미렌더. true에서 렌더 |
| A9 | 상반 라벨 2건 적재 후 summary가 `valid:1, noise:1`로 집계 |
| 공통 | `pytest`(본체+noise_gate) 전건 · `python scripts/arch_check.py --ci` 0위반 |

---

## 2. 트랙 B — 알람 UI 표시 레벨 사용자 선택

### 2.1 현행 실측

| 층 | 현재 동작 | 근거 |
|---|---|---|
| 발행 | PAGE는 incident 트래커 ON일 때만 카드화, TICKET/DASHBOARD는 SSE 브리지, **SUPPRESS는 발행 안 함** | `alarm_notifier.py:620·627·633`, `incident_events.py:87` |
| 서버 필터 | **존(zone) RBAC만** — 티어·심각도 필터 없음 | `alarm.py:945` `_visible()` |
| 개인 설정 | **on/off 토글 1개**(localStorage `alarm_receive_enabled`, 기본 on) | `app.js:2647`, `index.html:36` |

즉 지금 사용자가 고를 수 있는 것은 "전부 받기 / 전부 끄기" 뿐이다.

### 2.2 설계 — 4단계 표시 레벨

**티어를 기준축으로 삼는다.** 심각도가 아니라 티어여야 하는 이유: 게이트의 최종 산출물이 티어이고,
심각도로 거르면 "심각도 1인데 승격돼 PAGE가 된 알람"을 놓친다(재현율 우선 원칙 위배).

| 레벨 | 값 | 보이는 티어 | 대상 |
|---|---|---|---|
| 긴급만 | `page` | PAGE | 당직·야간 |
| **통보 대상**(기본) | `ticket` | PAGE, TICKET | 일반 운영자 |
| 전체 | `dashboard` | PAGE, TICKET, DASHBOARD | 관제 |
| 억제 포함(감사) | `suppress` | 전부 + SUPPRESS | **관리자 전용** · 오억제 점검(A-8) |

**규칙**

1. **티어가 없는 이벤트는 항상 통과**시킨다 — `alarm.py:830`(analyze 테스트 경로) payload에는
   `tier`가 없다. 미상을 막으면 테스트 카드가 사라진다. 재현율 우선과도 정합.
2. 레벨은 **권한을 넓히지 못한다** — 존 RBAC(`_visible`) 통과가 **선행**이고, 레벨은 그 뒤에서
   좁히기만 한다. `suppress` 레벨은 `role==admin`이 아니면 요청해도 `dashboard`로 강등한다.
3. 서버에서 거른다(클라이언트 필터 아님) — 안 볼 이벤트를 SSE로 흘리지 않아 대역·렌더 비용을 줄이고,
   억제 이벤트가 비관리자 브라우저에 도달하는 일을 원천 차단한다.

### 2.3 저장 위치 — 권고안과 대안

| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **B-권고: 서버 사용자 필드** | `auth_users.alarm_view_level TEXT` 추가, JWT 클레임 전파 | 디바이스·브라우저 무관 일관, 서버 필터에 직결, 감사 가능 | 마이그레이션·클레임·API 3곳 수정 |
| 대안 1: localStorage | `alarm_receive_enabled` 전례 그대로 | 최소 변경 | 기기마다 다름, **서버 필터 불가**(레벨을 서버가 모름) |
| 대안 2: 하이브리드 | 서버 저장 + 즉시 반영 로컬 캐시 | 둘의 장점 | 동기화 코드 추가 |

**권고 = B-권고(서버 필드)**. `alarm_zones`가 이미 동일 경로를 전부 뚫어놨다 —
`auth_users` 컬럼(`server.py:34`, ALTER `server.py:62`) → `User` 도메인(`user.py:45`) →
JWT 클레임(`dependencies.py:166`) → API 스키마(`schemas.py:162`) → UI. **같은 5단 배선을 그대로
복제**하면 되고, SSE 스트림이 이미 사용자 객체를 갖고 있어(`alarm.py:936`) 필터 주입점도 준비돼 있다.

단, `alarm_zones`는 **관리자가 부여하는 권한**이고 `alarm_view_level`은 **본인이 고르는 선호**다.
따라서 수정 엔드포인트는 관리자용(`admin.py`)이 아니라 **본인용**(`user_auth.py`)에 둔다.

### 2.4 SUPPRESS 스트리밍 (감사 레벨의 전제)

`suppress` 레벨은 **워커가 SUPPRESS 이벤트를 발행해야** 성립한다. 현재는 로그만 남긴다.

- 신규 플래그 **`NOISE_SSE_SUPPRESSED_ENABLED`**(기본 `false`) — 켜면 `_route_non_page_tier`의
  SUPPRESS 분기에서도 `_publish_tier_sse`를 호출한다.
- 기본 off면 **현행과 비트동일**(회귀 0). 켜더라도 비관리자에게는 §2.2 규칙 2로 도달하지 않는다.
- 부하 주의: 억제율이 높은 환경에서 SUPPRESS는 **가장 큰 트래픽**이다. 관리자 세션이 붙어 있을
  때만 발행하는 최적화는 1차 범위 밖(플래그 on/off로 충분).

### 2.5 작업 단위

| WU | 내용 | 파일 |
|---|---|---|
| **B1** | `auth_users.alarm_view_level` 컬럼 + ALTER 마이그레이션, `User` 필드, JWT 클레임, 응답 스키마 | `src/api/server.py`, `src/domain/user.py`, `src/api/dependencies.py`, `src/api/schemas.py` |
| **B2** | 본인 설정 변경 API `PUT /api/v1/user/preferences`(레벨만) + 관리자 화면 표시(읽기) | `src/api/routes/user_auth.py`, `src/static/js/admin.js` |
| **B3** | SSE 필터에 레벨 조건 추가(`_visible` 뒤 티어 게이트, 미상 통과, admin 아니면 suppress 강등) | `src/api/routes/alarm.py:945` |
| **B4** | UI 셀렉트 박스(수신 토글 옆), 툴팁에 현재 레벨 표기, 변경 시 API 저장 + **SSE 재연결** | `src/static/index.html:33`, `src/static/js/app.js:2679` |
| **B5** | `NOISE_SSE_SUPPRESSED_ENABLED` 플래그 + SUPPRESS 발행 분기 + 설정 카탈로그 등재 | `noise_gate/application/nodes/alarm_notifier.py:633`, `src/config.py`, `.env`/`.env.example` |

> **B4 주의**: 레벨을 바꾸면 **EventSource를 다시 열어야** 서버 필터가 갱신된다(스트림은 연결 시점
> 사용자 정보로 판정). `setupAlarmToggle`의 연결/해제 로직을 재사용한다.

### 2.6 트랙 B 검증

| 항목 | 방법 |
|---|---|
| 필터 정확성 | 레벨별로 tier 4종 이벤트를 버스에 주입 → 도달 집합이 정의표와 일치 |
| 미상 통과 | `tier` 없는 payload는 모든 레벨에서 도달 |
| 권한 상한 | 비관리자가 `suppress` 저장 시도 → 저장은 되되 스트림은 `dashboard`로 동작(또는 400 — §5 게이트) |
| 존 우선 | 다른 존 이벤트는 레벨과 무관하게 미도달 |
| 회귀 0 | `NOISE_SSE_SUPPRESSED_ENABLED=false` + 레벨 미설정(기본 `ticket`)에서 기존 동작과 동일 |

> **회귀 주의**: 기본값을 `ticket`으로 두면 **지금 DASHBOARD 카드를 보던 사용자가 못 보게 된다.**
> 현행 동작 보존을 우선한다면 기본값은 `dashboard`여야 한다 — §5 게이트 2로 올린다.

---

## 3. 트랙 C — 설정 UI 커버리지

### 3.1 실측 결과 — "빠진 옵션은 없다"

2026-08-28 `field_index()` 직접 실행:

```
카탈로그 총 필드: 290        NOISE_ 필드: 92        시크릿: 12        미소비: 20
.env 키 139  → 카탈로그 미포함 0건
.env.example 키 186 → 카탈로그 미포함 0건
```

Plan 68이 채택한 **pydantic 인트로스펙션 SSOT**가 의도대로 동작하고 있다. 파일에 있는 키는 물론,
파일에 없는 config 전용 필드까지 UI에 나온다(290 > 186).

### 3.2 남은 갭 3건

| # | 갭 | 실측 | 개선안 |
|---|---|---|---|
| **C-1** | `SECTION_BY_KEY` **미분류 8키** → 소제목 없이 렌더돼 발견성이 떨어진다 | `settings_catalog.py:70` 매핑 누락, `admin.js:404`는 `item.section` 있을 때만 소제목 생성 | 8키에 구획 부여: `NOISE_ANOMALY_METRIC_SOURCE_MAP_CSV`→이상탐지, `NOISE_ANNOTATION_LLM_*` 4건→주석 LLM, `NOISE_INVESTIGATION_FOLLOWUP_*` 3건→조사 연계 |
| **C-2** | **별도 프로세스 설정이 UI 밖** — `mcp_server/.env`(8키)·`mcp_server/config.toml`·`sre_agent/.env.example`(5키) | 카탈로그는 본체 `AppConfig`만 인트로스펙션 | **읽기 전용 표시 + 명시적 경계 안내**. 쓰기는 하지 않는다(별도 venv·별도 프로세스·재기동 주체가 다름 — D-139 경계) |
| **C-3** | 시크릿 12키가 왜 편집 불가인지 화면에서 불명확 | `settings_catalog.py:638` 저장 거부 메시지는 있으나 사전 안내 약함 | 시크릿 필드에 "`.encenv`에서 관리 — 웹UI 수정은 무효" 상시 배지(현 마스킹 표시 옆) |

> **C-2 판단 근거**: 별도 패키지 설정을 본체 UI에서 **쓰기**까지 하면, 본체가 다른 프로세스의
> 파일 시스템·재기동을 전제하게 되어 D-139(패키지 경계·양방향 import 0)를 침식한다. "어디서
> 관리하는지"를 화면에 명시하는 것이 옳은 해법이다.

### 3.3 작업 단위

| WU | 내용 | 파일 |
|---|---|---|
| **C1** | `SECTION_BY_KEY`에 8키 구획 추가 + 섹션 누락 방지 테스트(신규 NOISE 키가 미분류면 실패) | `src/api/settings_catalog.py`, `tests/test_api/test_settings_catalog_sections.py` |
| **C2** | 설정 탭 하단에 "이 화면 범위 밖 설정" 안내 블록(파일 경로·관리 주체·재기동 방법) | `src/static/admin/dashboard.html`, `admin.js` |
| **C3** | 시크릿 배지 문구 보강 | `admin.js` |

### 3.4 트랙 C 검증

- C1: `field_index()`의 모든 `NOISE_` 키가 `SECTION_BY_KEY`에 존재함을 단언하는 테스트 추가
  (신규 플래그가 늘 때 자동 감지 — 트랙 B의 `NOISE_SSE_SUPPRESSED_ENABLED`도 이 테스트에 걸린다).
- C2·C3: 화면 확인(스크린샷) + 기존 설정 저장 회귀.

---

## 4. 실행 순서

```
1차(보안·회귀 0)         A1 → A7 → C1
2차(신규 기능)           B1 → B2 → B3 → B4 → B5
3차(신뢰성)              A2 → A3 → A4 → A6
4차(운영 편의)           A5 → A9 → C2 → C3
```

- **A1을 먼저** 하는 이유: 다른 존 알람에 라벨/ack가 가능한 현 상태가 유일한 보안 결함이다.
- **C1을 1차에** 넣는 이유: B5가 새 `NOISE_` 키를 추가하므로, 섹션 누락 감지 테스트가 먼저 있어야
  같은 실수가 재발하지 않는다.
- A6(성능)은 계측 → 상한 → tail 순서로, 각 단계마다 회귀 테스트를 통과시킨 뒤 다음으로 간다.

---

## 5. 착수 게이트 — 사용자 확정이 필요한 항목

| # | 쟁점 | 권고안 | 대안 |
|---|---|---|---|
| **1** | 표시 레벨 저장 위치 | **서버 사용자 필드**(§2.3) — `alarm_zones` 배선 복제 | localStorage(최소 변경·서버 필터 불가) |
| **2** | 표시 레벨 **기본값** | **`dashboard`**(현행 동작 보존 — 지금 보이던 카드가 사라지지 않음) | `ticket`(기본을 조용하게) |
| **3** | 비관리자가 `suppress` 선택 시 | **저장 허용 + 스트림에서 강등**(조용한 안전) | 400 거부(명시적이지만 UX 마찰) |
| **4** | `note` 입력란 신설(A2) | **신설**(few-shot 품질 향상) | 미신설(민감정보 유입 위험 최소화) |
| **5** | A5 철회 방식 | **tombstone append**(감사 원칙 보존) | 관리자 화면에서 물리 삭제 |

---

## 6. 비범위 (이번에 하지 않는 것)

- 피드백 라벨의 **다수결·가중 합의 알고리즘** — A9는 표시까지만. 판정 로직 변경은 D-035 경계
  재검토가 선행돼야 한다.
- 피드백을 **ML 모델 학습**에 사용 — D-048.11이 "ML 미사용·few-shot 한정"으로 확정한 사항이다.
- 억제 규칙 자체의 임계 튜닝(`suppress_max_severity` 등) — 운영 데이터 축적 후 별건.
- 별도 패키지 설정의 **웹UI 쓰기**(C-2 근거).
- 알람 카드의 시각 디자인 개편.

## 7. 산출물

- 코드: §1.2·§2.5·§3.3의 파일들
- 테스트: `tests/test_api/`(RBAC·SSE 필터·설정 카탈로그), `noise_gate/tests/`(feedback_store 회전·tail·철회)
- 문서: `docs/28` 갱신(버튼 동작·레벨 옵션 반영), `docs/02_decision.md`(D-177~179 등재)
- 설정: `.env`/`.env.example`에 `NOISE_SSE_SUPPRESSED_ENABLED`·`NOISE_FEEDBACK_STORE_MAX_LINES` 추가
  (인라인 주석 금지 — 주석은 별도 줄)
