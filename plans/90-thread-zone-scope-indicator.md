# 90. 채팅창 폴스타(존) 스코프 표시·선택·해제 — 암묵 승계를 보이게 하고, 끊을 수 있게 한다

> **작성일**: 2026-09-09
> **성격**: 구현 계획(implementation-ready) · **상태: 구현 완료(2026-09-09 · D-205 등재 — §10)** · **v4(2026-09-23 · 사용자 지시 — §12)**: 개방을 **코드 기본값**으로 전환(`zone_group_exclusive` `True`→`False`) — 종전에는 미추적 `.env` 한 줄에만 있어 그 줄이 없는 배포가 확정과 반대로 동작했다 · **v3(2026-09-09 · D-206 등재 — §11)**: 사용자 지시로 은행존+공동존 **체크박스 동시 선택 + 은행존→공동존 순차 조회** 개방 · 게이트 G-1~G-4는 사용자 지시("구현을 진행하라")에 따라 **권고안으로 진행**(다른 답이면 §4 표대로 되돌린다) ·
> **v2(2026-09-09)**: APM·DPM 연동 재검토(§9) — `db_scope`를 축 구조로(A1) · 옵션 엔드포인트를 축 배열로(A2) ·
> 칩 핀은 존 그룹 제약이지 대상 치환이 아님을 D-205에 명문화(A3). **§3.1·§3.4는 §9.3이 우선한다.**
> **요청 취지(사용자 지시 원문)**: *"은행존과 공동존 폴스타를 선택하는 상황이 같은 채팅창에서 1번
> 선택되면 향후 추가로 들어오는 프롬프트에는 선택된 폴스타로 계속 진행된다. 해당 창에 어떤 폴스타를
> 조회하는지 UI로 보여주고 사용자가 폴스타를 선택하거나 선택 제거할 수 있도록 기능을 추가하는
> 계획을 수립하라."*
> **관련 결정**: **D-143**(존 모호 시 역질문 + `selected_db_ids` 결정적 라우팅 — 후속2 후단 게이트 ·
> **후속3 존 그룹 상호배타** — 이 계획의 UI는 그 규칙을 그대로 따른다) · D-154(미선택 존 표면어 재작성) ·
> D-176 후속4(범위 사전 선택 · 재확장 패널 — 동형 UI 선례) · D-183(개인 상태는 브라우저 —
> 이 계획은 저장이 필요 없다, §3.4) · D-064(요청 스코프 상태 명시 초기화) · D-161(경로 폐기 실측 4항) ·
> D-127(과금 API 승인 게이트) · `plans/80` §5.4-③(신규 플래그 기본 off 원칙) · Plan 50-multiturn §3.2(DB 승계)
> **신규 결정 예약**: **D-205**(스레드 DB 스코프 계약 — 응답 메타 `db_scope` + 요청 `reset_db_scope` +
> 웹 UI 스코프 칩) — `docs/02_decision.md` 「채번 이력」 표에 예약 등재(2026-09-09). 계획서에만 적은
> 예약은 효력이 없다(D-161 부기).
> ※ 채번 실측 2026-09-09 — `## D-` 헤더 최댓값 **197** · 「변경 이력」 표 최댓값 **197** ·
> 「채번 이력」 표: D-195(`plans/87`) · D-196(`plans/54`) · **D-203(`plans/88`) · D-204(`plans/89`)가
> 같은 날 병렬 계획으로 선점** → **D-205**. 계획서 번호도 같은 이유로 **90**(87=제니퍼 APM ·
> 88=순차 복합 질의 · 89=스트리밍 진행 상태). 파일명 접미사 `-TODO`는 INDEX 「파일명 상태 접미사」
> 규칙(코드 0건) 적용.
> **실측 기준**: 아래 모든 `file:line`·값은 2026-09-09 현 브랜치(`multiintent`, `9945284`)에서 직접
> 확인했다. 실 LLM 호출 0 · 서버 기동 0(운영 단 판정은 `docs/21` 2026-08-20 실측 로그를 인용).

---

## 0. 요약 — 요청은 UI 한 줄이지만, 실측하면 난이도가 다른 세 조각이다

| 조각 | 난이도 | 실측 근거 |
|---|---|---|
| **① 표시** — "이 창은 지금 어느 폴스타를 보는가" | **중간 — 서버가 아무 응답에도 싣지 않는다** | `QueryResponse`(`src/api/schemas.py:76-127`)에 db_id·존 필드 **0개**. SSE `done` 2곳(`query.py:1326-1345`, `:1400-1417`)도 동일. `GET /conversation/{thread_id}`(`conversation.py:39-95`)는 messages·turn_count뿐 — 승계 캐리어 `conversation_context.previous_db_ids`를 읽는 API가 **없다**(§1.2) |
| **② 선택** — 창에서 폴스타를 고른다 | 낮음 — **채널이 이미 있다**, 단 1단 미배선 | `QueryRequest.selected_db_ids`(`schemas.py:26-70`) + 결정적 고정(`intent_planner.py:295-303` · `semantic_router.py:133-158` · `subagents.py:1001`). **그러나 운영 1단 `deep_agent`의 ambient 키 목록(`deep_agent.py:140-158`)에 `selected_db_ids`가 없다** → 1단에서는 격리 입력(`subagents.py:720`)이 `None`을 받는다(§1.4) |
| **③ 해제** — 선택을 지우고 승계를 끊는다 | **중간 — 존재하지 않는다** | 승계는 `context_resolver.py:107`의 sticky 추출(`_extract_previous_db_ids(state) or prior_ctx.previous_db_ids`)이라 **끊는 수단이 없다**. 프론트에 "새 대화" 버튼도 없고(`currentThreadId` 21곳 전부 송수신뿐, `app.js:246`) `thread_id`는 메모리 전용 — **새로고침=새 스레드**가 유일한 초기화 경로 |

**따라서 중심은 ①과 ③의 서버 계약(D-205)이다.** 프론트만 만들면 "표시"는 프론트가 추측한 값이 되고
(서버 승계값과 어긋난다), "해제"는 화면에서만 지워질 뿐 다음 질의는 여전히 직전 존으로 간다 — 침묵
강등의 새 형태다. 반대로 서버 계약만 정확하면 프론트는 **서버가 보고한 값을 그대로 보여주는
얇은 칩**으로 끝난다.

---

## 1. 실측 — 지금 "선택된 폴스타로 계속 진행"이 실제로 일어나는 방식

### 1.1 승계 흐름(턴1 선택 → 턴2 승계) — 사용자 관찰이 맞다, 단 메커니즘은 "암묵"이다

1. **턴1 역질문**: 라우트 pre-gate `_zone_clarification_or_none`(`query.py:905-933`)이 파이프라인
   **전에** `build_zone_clarification`(`src/utils/query_gen_common.py:1423-1462`) 페이로드
   `{kind:"zone_select", options:[{db_id,label,group}], original_query, group_exclusive}`를
   `status="clarification"`으로 즉시 반환한다(서버측 보류 상태 없음 — D-143).
2. **턴1 선택 재전송**: 프론트 `renderZoneClarification`(`app.js:1334-1428`)이 체크 결과를 자연어
   재조합 없이 `selected_db_ids`로 원문과 함께 재전송(`app.js:1418-1426` → `:1067-1069`).
   서버는 LLM 라우팅을 건너뛰고 대상 DB를 고정한다(§0 ②).
3. **턴1 저장**: 실행 결과의 `target_db_ids`가 `result_aggregator._collect_db_promotion`
   (`result_aggregator.py:357-380`)으로 top-level `active_db_id`/`target_databases`에 승격돼
   체크포인트(`AsyncSqliteSaver`, `graph.py:236-258`)에 남는다. **1단도 같은 경로다** —
   `deep_agent._aggregate_with_fabrix`(`deep_agent.py:440-480`)가 `result_aggregator`를 호출한다.
4. **턴2 진입**: `selected_db_ids`는 요청 스코프라 `create_followup_input`(`state.py:328`)이
   `None`으로 리셋하지만, `context_resolver`가 체크포인트의 `active_db_id`/`target_databases`/
   `mapped_db_ids`를 합쳐 `conversation_context.previous_db_ids`로 재생성한다
   (`context_resolver.py:107`, `_extract_previous_db_ids` `:159-192`). 이번 턴 추출이 비면
   **직전 ctx를 sticky 승계**한다(분석 턴을 건너뛰어도 유지 — D-056 후속).
5. **턴2 적용**: `_apply_db_succession`(`subagents.py:420-478`)이 `previous_db_ids[0]`을 단일 우선
   후보로 고정한다. 단 이번 턴 원문에 새 위치/DB 표면어가 있으면(`_has_new_location_db_signal`,
   `subagents.py:187-202` · 어휘 `registry.new_db_signal_terms()`) 승계를 포기하고 **이번 턴 신호가
   이긴다**. 우선순위(`subagents.py:996-1019`): **UI 확정(`selected_db_ids`) > 이번 턴 원문 힌트 핀
   > 승계 > LLM 분류 팬아웃**.

즉 "선택된 폴스타로 계속 진행"의 실체는 **선택값의 보존이 아니라 직전 실행 DB의 암묵 승계**다.
이 구분이 §2의 해석을 가른다 — 승계는 선택하지 않은 턴(텍스트 위치어로 다른 존을 본 턴)에도
똑같이 일어나며, 그 뒤로는 *그* 존으로 이어진다.

### 1.2 표시 — 사용자에게 나가는 것은 진행 패널의 raw db_id뿐

| 경로 | 무엇이 나가는가 | 한계 |
|---|---|---|
| SSE `node_complete`(`query.py:1247-1254`, `_extract_node_progress` `:452-467`, `_summarize_tasks` `:340-342`) | `semantic_router`의 `active_db_id`/`targets[]`, task의 `target_db_ids` | **스트리밍 중간 이벤트**일 뿐이고 `/query` 비스트리밍 응답에는 없다. 프론트는 진행 패널(`app.js:2295-2308`, `:2502-2504`)에 `polestar_b0` 같은 **raw db_id**로만 그린다. 패널이 접혀 있으면(`isPanelCollapsed` `app.js:2018`) 보이지 않는다 |
| `scope_reexpand.options[].db_ids`(`query.py:686-707`) | 범위를 좁힌 턴에만 | 게다가 SSE `done`에는 실리지 않는다(`query.py:1066`에서만 삽입 — `app.js:1179`는 항상 undefined). **키를 응답 경로 한 곳에만 넣은 비대칭 선례** — 본 계획의 `db_scope`는 4곳 동시 삽입을 검증 항목으로 둔다(§5 T3) |
| `GET /api/v1/health.db_status_map`(`schemas.py:135`, `app.js:169-176`) | 활성 db_id 목록·헬스 | 스레드와 무관 |

존 **라벨**(은행존/공동존 김포/공동존 여의도)은 프론트에 없다 — 서버 `ZONE_CLARIFY_OPTIONS`
(`query_gen_common.py:1369-1373`)가 단일 출처이고, 프론트는 역질문 페이로드로 받을 때만 안다.
(`app.js:3733 ALARM_ZONE_LABELS`는 알람 뷰가 복제한 선례 — 반복하지 않는다, §3.1.)

### 1.3 해제 — 없다, 그리고 "선택 안 함"은 "전체 조회"가 아니다

- `selected_db_ids` 미전송(None)이면 후속 턴은 §1.1-4의 암묵 승계로 돌아간다. 즉 **해제 = 전 존
  조사가 아니라 직전 존 지속**이다. 문구 설계의 핵심 함정.
- 라우트 pre-gate는 **체크포인트가 있기만 하면** 존 역질문을 내지 않는다(`query.py:920-921`
  `if checkpoint_state is not None: return None`). 승계할 것이 없어도(예: 해제 뒤) 첫 턴 규칙으로
  돌아가지 못한다 → §3.2 ⓒ에서 판정 기준을 "체크포인트 존재"에서 "승계 가능한 DB 스코프 존재"로 좁힌다.
- 후단 게이트(`subagents._zone_clarification_or_none_task` `:205-280`)는 "첫 턴(previous_db_ids
  없음)"을 조건으로 본다 — `previous_db_ids`만 비우면 그대로 첫 턴처럼 동작한다(D-143 후속2).

### 1.4 ★ 운영 1단(`deep_agent`)에는 `selected_db_ids`가 닿지 않는다

- 사다리 확정 실측(`docs/21` §5, 2026-08-20): `tier=deep_agent degraded_reason=none
  resolved_by=explicit_env`. 오늘 `.env`도 `ENABLE_DEEPAGENTS_PACKAGE=true`(`.env:180`),
  `deepagents 0.6.10` 설치(루트 venv 실측). **운영 경로는 1단이다.**
- 1단은 도구 클로저로 `_AMBIENT_KEYS`(`deep_agent.py:140-158`)만 넘긴다: `conversation_context`·
  `mapped_db_ids`·`parsed_requirements`는 있고 **`selected_db_ids`·`zone_clarification_allowed`는
  없다**. `_make_isolated_input`(`subagents.py:661-`, `:720 "selected_db_ids": state.get(...)`)은
  이 ambient dict를 `state`로 받으므로 `None` → `raw_targets` 없음 → `classify_dbs` 팬아웃.
- 그래도 지금까지 역질문 답이 "동작해 보인" 이유: `_substitute_zone_placeholder`(`query.py:646-661`)가
  `ㅇㅇ존` 플레이스홀더를 **선택 라벨 텍스트로 치환**하고, `input_parser._ensure_location_hints`
  (`input_parser.py:47-71`, D-065)가 그 텍스트에서 `target_db_hints`를 만들어
  `_apply_turn_hint_pinning`(`subagents.py:360-`)이 핀을 건다. 즉 **텍스트 우회로 맞는 것이지
  구조화 필드가 맞는 게 아니다.** 플레이스홀더도 혼합 열거도 없는 원문("모든 서버의 CPU") +
  선택 b0는 1단에서 3존 팬아웃이 된다. D-143 후속2가 *"트랙 B는 활성 런타임 아님"*(2026-08-04)이라
  적은 뒤 8-20에 1단이 정본이 됐는데 배선이 따라오지 않았다. `tests/test_orchestration/test_zone_*.py`
  4건 어디에도 `deep_agent`·ambient 경로 테스트가 없다(grep 실측).
- 본 계획의 "선택"은 이 채널에 얹으므로 **1단 배선 보정(§3.3 ⓑ)이 선행 조건**이다. 1줄 수정이지만
  범위 안이다 — 없으면 칩에서 고른 존이 운영에서 무시된다.

### 1.5 프론트 자산 — 있는 것

| 자산 | 위치 | 재사용 |
|---|---|---|
| 전송 단일 진입점 `handleSend()` | `app.js:674`(버튼·Enter·웰컴 힌트·파일이 전부 통과, 주석 `:706`) | 칩 상태 → 요청 필드 주입 지점 |
| 요청 본문 조립 3곳 | `executeStreamingQuery` `:1062-1069` · `executeFallbackQuery` `:1611-1617` · `executeFileQuery` `:1673-1678`(CSV) | **세 곳 동시** 수정(비대칭 반복 실수 유형 — `query.py:1697` 주석) |
| 존 선택 위젯 | `renderZoneClarification` `:1334-1428`(체크박스 + `group_exclusive` 라디오 동작 `:1372-1381`) | 칩의 선택 팝오버가 같은 마크업·같은 규칙 |
| 입력창 위 확인 바(Plan 86) | `index.html:191-197` `#promptConfirm`, `.prompt-confirm*`(`style.css:656-723`) | 칩의 **레이어·좌표**(입력창 바로 위) |
| 토글 칩 | `.alarm-chip`(+`.active`, `aria-pressed`, `style.css:477-528`) | 칩 시각 언어의 유일한 선례 |
| 헤더 툴팁 | `.status-tooltip` + `data-tooltip` 위임(`app.js:407-435`) | 칩 호버 설명 |
| UI 회귀 테스트 관례 | `tests/test_api/test_ui_scope_select.py` 등 5건 — `index_html`/`app_js`/`style_css` 픽스처 + 소스 문자열 단언(playwright 미설치) | 신규 `test_ui_zone_scope.py` |
| 개인 상태 저장 관례 | localStorage + try/catch(`app.js:281-311`, D-183) | **불필요** — `thread_id`가 메모리 전용이라 새로고침이 곧 새 스레드다(§3.4) |

---

## 2. 요청 해석 — 갈라지는 지점 2곳

### 2.1 "선택 제거"가 뜻하는 것 — 승계 끊기(권고) vs 전 존 조사 강제

- **(A) 승계 끊기**: 다음 질의는 **첫 턴 규칙**을 따른다 — 존 미지정 대량 조회면 역질문(D-143), 서버명
  지목이면 존 순회 탐색(D-176 후속3), 위치어가 있으면 그 존. 사용자 정신 모델: "이 창의 선택을
  지웠다, 다음엔 다시 물어봐라".
- **(B) 전 존 조사 강제**: 은행존+공동존을 한 번에 본다 — **D-143 후속3(존 그룹 상호배타, 기본 on)과
  정면 충돌**하고 PII 차단 미종결 이슈(b0+gp)를 다시 연다. 비채택.
- → **(A)로 진행**(G-1). 칩의 빈 상태 문구는 "존 미지정 — 다음 질의에서 다시 확인"이지 "전체"가 아니다.

### 2.2 "선택"의 강도 — 1회 전송 후 승계(권고) vs 매 턴 하드 핀

- **(A) 1회 전송 후 승계**: 칩에서 고르면 **다음 질의 1건**에 `selected_db_ids`를 실어 보낸다(역질문
  답과 완전히 같은 계약). 그 턴이 실행되면 §1.1-3으로 체크포인트에 남고 이후는 승계다. 이번 턴
  원문에 다른 존 위치어가 있으면 현행 우선순위대로 **원문이 이기고**, 서버가 보고한 `db_scope`를
  칩이 따라간다(사용자가 "김포 서버…"라 쓰면 칩이 "공동존 김포"로 바뀐다 — 놀라움 없음).
- **(B) 매 턴 하드 핀**: 칩이 켜진 동안 매 요청에 `selected_db_ids`를 실어 보낸다. 실측 부작용 3건 —
  ① pre-gate 항상 통과(`query.py:915`) ② `resolved_limit`이 매 턴 `_ZONE_SCAN_LIMIT`(100,000)로
  상향(`query.py:600-601` — 존 선택 재개 턴은 전량 조회 전제) ③ `scope_narrowed` 기록이 매 턴
  남는다(`:604`). 게다가 D-154 재작성이 원문의 다른 존 언급을 조용히 지운다 — 사용자가 쓴 "여의도"가
  사라지는 침묵 교정. 비채택.
- → **(A)로 진행**(G-2). 칩은 "지금 무엇이 선택돼 있나"의 **거울**이지 강제 장치가 아니다.

---

## 3. 설계

### 3.1 계약(D-205) — 응답 메타 `db_scope` · 요청 `reset_db_scope` · 존 옵션 조회

```jsonc
// 모든 질의 응답(4경로)에 동일 키 — QueryResponse.db_scope · SSE done.db_scope
"db_scope": {
  "db_ids": ["polestar_b0"],                       // 다음 턴이 승계할 집합(빈 배열=미지정)
  "source": "selected" | "inherited" | "hint" | "classified" | "none",
  "group": "bank" | "common" | null,               // ZONE_CLARIFY_OPTIONS[].group
  "labels": ["은행존"]                              // 서버가 붙인다 — 프론트 라벨 복제 금지
}
// 요청 — QueryRequest.reset_db_scope: bool = False (요청 스코프, 파일 라우트는 form 필드로 대칭)
// GET /api/v1/zones → {"options":[{db_id,label,group}], "group_exclusive": true}
//   = build_zone_clarification(active_db_ids, "", group_exclusive)의 options 부분 재사용 (읽기 전용·인증 정책은 /health와 동일)
```

- **`db_ids`의 단일 출처**: 다음 턴 `context_resolver`가 읽을 값과 **같은 함수**가 만든다.
  `_extract_previous_db_ids(state) or state.conversation_context.previous_db_ids`를
  `resolve_thread_db_scope(state)`로 묶어 `context_resolver`(턴 N+1)와 라우트(턴 N 보고)가 공유한다.
  라우트가 따로 계산하면 언젠가 어긋난다 — Known Mistakes "단일/멀티 경로 대칭"과 같은 결.
  계층: `nodes/`(application) → 라우트(interface)가 import — 방향 적법(`arch_check`).
- **`source`**는 라우트가 최종 state에서 결정적으로 판정한다: 이번 턴 `selected_db_ids` 있음 →
  `selected` · `target_databases[].user_specified`/reason이 힌트 핀 → `hint` · 승계 reason
  (`"이전 턴 DB 승계"`) → `inherited` · 그 외 대상 있음 → `classified` · 대상 없음 → `none`.
  reason 문자열 매칭이 아니라 **`targets[].reason`을 만드는 세 곳(`subagents.py:52·471`,
  `semantic_router.py:133-158`)에 `origin` 키를 추가**해 판정한다(문자열 의존 금지).
- **`labels`·`group`**: `ZONE_CLARIFY_OPTIONS`에서 붙인다(비폴스타 DB는 `display_name`, group null).
- 신규 플래그 없음 — 응답 키 추가는 부가적(additive)이고 `reset_db_scope` 기본 False면 **현행
  바이트 동일**. `plans/80` §5.4-③ 충족.

### 3.2 백엔드 — 해제(`reset_db_scope=True`)의 3지점 대칭

| 지점 | 변경 | 근거 |
|---|---|---|
| ⓐ `create_followup_input`(`state.py:287-344`) | `reset_db_scope` 인자 추가 → `active_db_id=None`, `target_databases=[]`, `mapped_db_ids=[]`(G-4), `db_scope_reset=True`(요청 스코프 state 필드 — 매 턴 False로 재공급) | 체크포인터 델타 병합(D-064) — 비우려면 명시 초기화 |
| ⓑ `context_resolver`(`context_resolver.py:107-113`) | `state.db_scope_reset`이면 `previous_db_ids=[]`·`previous_location=""`으로 **sticky 폴백을 건너뛴다**. `previous_entities`는 유지(호스트 지시어 "그 서버"는 존과 무관) | sticky가 핵심 — ⓐ만 하면 `prior_ctx.previous_db_ids`로 되살아난다 |
| ⓒ pre-gate(`query.py:920-921`) | `if checkpoint_state is not None` → `if _has_inherited_db_scope(checkpoint_state) and not body.reset_db_scope` | §1.3 — 승계할 게 없으면 첫 턴 규칙으로 |
| ⓓ 파일 라우트 2곳(`query.py:1449-1455`, `:1697-1703`) | `reset_db_scope` form 필드 + 같은 델타 | 텍스트/파일 대칭 |

후단 게이트·`_apply_db_succession`은 `previous_db_ids` 부재를 이미 첫 턴으로 보므로 **무수정**.

### 3.3 백엔드 — 선택은 기존 채널, 단 1단 배선 보정

- ⓐ 칩 선택 → 다음 요청에 `selected_db_ids` 1회(§2.2 A). 서버 변경 0.
- ⓑ **`deep_agent._AMBIENT_KEYS`에 `selected_db_ids` 추가**(`deep_agent.py:140-158`) —
  `_make_isolated_input`(`subagents.py:720`)이 이미 읽으므로 1줄. `zone_clarification_allowed`도
  같은 결손이나 후단 게이트가 1단에서 어떻게 발동하는지는 T0에서 실측 후 결정(추정 수정 금지).
- ⓒ 혼합 선택(b0+gp)은 서버 `_zone_group_exclusive_or_none`(`query.py:877-902`)이 턴 유형 무관
  최우선으로 되묻는다 — 칩 팝오버가 라디오 규칙을 지켜도 **서버 검증은 그대로 둔다**(UI 게이트 ≠ 인가).

### 3.4 프론트 — 입력창 위 스코프 칩

```
┌ .input-bar-inner ───────────────────────────────────────────────┐
│ [알람에서 생성] …                              ← promptConfirm(기존) │
│ ◉ 조회 대상: 은행존  (승계 중) ▾   ×           ← #dbScopeChip(신규)  │
│ ┌ textarea ───────────────────────────────┐ [보내기]              │
└──────────────────────────────────────────────────────────────────┘
```

- **상태 4종**(칩 텍스트·`data-state`):
  `none` "존 미지정 — 첫 질의에서 확인" · `inherited|hint|classified` "은행존 (승계 중)" ·
  `selected` "은행존 (선택)" · `pending` "다음 질의부터: 공동존 김포"(선택만 하고 아직 안 보냄) ·
  `pending-reset` "다음 질의에서 다시 확인"(해제만 하고 아직 안 보냄).
- **▾ 팝오버**: `GET /api/v1/zones`의 options로 `renderZoneClarification`과 같은 마크업(체크박스 +
  그룹 라디오 동작)을 그린다. 확정 → `pendingDbIds`. 라벨은 서버 값.
- **×**: `pendingReset=true`. 재선택하면 reset은 해제.
- **전송**(`handleSend`): `pendingDbIds`가 있으면 3경로 모두에 `selected_db_ids`로, `pendingReset`이면
  `reset_db_scope=true`로 싣고 두 pending을 비운다. 역질문 답 재전송(`app.js:1418-1426`)은 현행
  유지 — 그 결과도 응답의 `db_scope`로 칩에 반영된다.
- **수신**: `done.db_scope`(SSE 2곳) · `/query` JSON · 파일 SSE → `renderDbScopeChip(db_scope)`.
  `clarification` 상태(역질문 중)에는 칩을 `none`으로 두지 않고 직전 값을 유지한다(아직 실행 전).
- **저장 없음**: `currentThreadId`는 메모리 전용(`app.js:246`) — 새로고침=새 스레드이므로 칩도
  새로 시작한다. localStorage에 두면 오히려 새 스레드에 옛 존이 붙는다(D-183과 무충돌, 저장 대상 아님).
- **진행 패널 무수정**: raw db_id 노출은 오선택 즉시 확인용(`app.js:2503` 주석)으로 목적이 다르다.
- `index.html:317` `app.js?v=9` → `v=10`(캐시 버스팅 관례).

### 3.5 비용·성능

LLM 호출 0 · DB 호출 0. `GET /api/v1/zones`는 정적 상수 필터. `db_scope` 판정은 최종 state dict 읽기.

---

## 4. 사용자 확정 게이트 — 답에 따라 구현이 달라지는 지점 4곳

| # | 질문 | 권고 | 바뀌는 것 |
|---|---|---|---|
| **G-1** | "선택 제거" = ⓐ 승계 끊기(다음 질의는 첫 턴 규칙) / ⓑ 전 존 조사 강제 | **ⓐ** | ⓑ면 D-143 후속3 상호배타 개정 + PII b0+gp 미종결 재개 — 별도 결정 필요 |
| **G-2** | 선택 강도 = ⓐ 1회 전송 후 승계 / ⓑ 매 턴 하드 핀 | **ⓐ** | ⓑ면 §2.2 부작용 3건에 대한 서버 분기(`pinned` 요청 필드)가 추가된다 |
| **G-3** | 칩 위치 = ⓐ 입력창 위(Plan 86 확인 바 레이어) / ⓑ 헤더(알림 토글 옆) | **ⓐ** | ⓑ면 스레드가 아니라 앱 전역 상태처럼 읽힌다 |
| **G-4** | 해제 시 폼필 고정 DB(`mapped_db_ids`)도 비우는가 | **예** (해제는 명시 의도, 재업로드 시 다시 고정) | 아니오면 폼필 스레드에서 해제해도 `_extract_previous_db_ids`가 mapped를 되살린다 — 칩은 "미지정"인데 승계는 계속되는 불일치 |

게이트 확정 전에도 T0(실측)·T1(계약 헬퍼)은 답과 무관하므로 착수 가능하다.

---

## 5. 작업 분해

| # | 작업 | 파일 | 검증 |
|---|---|---|---|
| **T0** | 1단 실측 — `_AMBIENT_KEYS` 결손으로 `selected_db_ids`가 격리 입력에 `None`으로 도달함을 **테스트로 고정**(mock 도구 경로, LLM 0). `zone_clarification_allowed`의 1단 경로도 같이 실측 | `tests/test_orchestration/test_deep_agent_wiring.py` | 실패하는 테스트 1건 → T2에서 통과 |
| **T1** | `resolve_thread_db_scope(state)` 신설 + `context_resolver`가 이를 사용(동작 동일) · `targets[].origin` 키 3곳 | `src/nodes/context_resolver.py`, `subagents.py`, `semantic_router.py` | 기존 `test_nodes`·`test_zone_*` 무변화 + origin 단언 |
| **T2** | `_AMBIENT_KEYS += selected_db_ids` | `deep_agent.py` | T0 통과 |
| **T3** | `db_scope` 스키마 + 4경로 삽입(`/query` · SSE done 텍스트·파일 · `/query/file`) + `GET /api/v1/zones` | `schemas.py`, `query.py`, 신규 `routes/zones.py`(또는 `health.py`) | `tests/test_api/test_db_scope.py` — **4경로 전부 키 존재** 단언(`scope_reexpand` 비대칭 재발 방지) · source 5종 |
| **T4** | `reset_db_scope` — ⓐⓑⓒⓓ 대칭 | `state.py`, `context_resolver.py`, `query.py` | 리셋 후 pre-gate 재발동 · sticky 미부활 · `previous_entities` 유지 · 기본 False 바이트 동일 |
| **T5** | 칩 마크업·CSS·렌더·팝오버·×·전송 주입(3경로) | `index.html`, `app.js`, `style.css` | `tests/test_api/test_ui_zone_scope.py`(문자열 단언 관례) |
| **T6** | 문서 — D-205 등재(예약→확정), `docs/02` D-143에 후속4 부기(1단 배선 결손·해제 채널), `plans/INDEX.md` 90 갱신·접미사 해제, `docs/18` (실수 발생 시) | — | grep |
| **T7** | 실 브라우저·실 파이프라인 확인 — **과금 경로, D-127 건별 승인 후** `RUN_E2E=1` | — | 턴1 선택→턴2 승계 칩 · ×→턴3 역질문 재발동 |

의존: T0→T2 · T1→T3→T5 · T4는 T1 뒤 · T7은 전부 뒤. 예상 규모: 서버 ~150줄 · 프론트 ~200줄 ·
테스트 ~250줄.

---

## 6. 검증 계획

- **단위(LLM 0)**: `_extract_previous_db_ids` 동작 보존 · `resolve_thread_db_scope` = 다음 턴
  `previous_db_ids`(같은 state로 두 함수 결과 동일 단언) · `reset` 뒤 `context_resolver`가 sticky를
  건너뜀 · pre-gate가 reset 턴에 `zone_select`를 반환 · 혼합 선택은 여전히 상호배타 재요청 ·
  `db_scope.source` 5종 · 1단 ambient에 `selected_db_ids` 통과.
- **회귀**: `pytest tests/test_orchestration tests/test_scope_select tests/test_api tests/test_nodes`
  기준선 대비 신규 실패 0 · `arch_check --ci`(nodes→routes import 방향 확인) · `overfit_check --ci`
  (`ZONE_CLARIFY_OPTIONS` 리터럴은 `utils`에 이미 있음 — 라우트에 **db_id 리터럴을 새로 쓰지 않는다**).
- **UI 정적**: 칩 DOM id·4상태 `data-state`·3경로 주입 코드·`v=10`.
- **e2e(승인 후)**: §5 T7 시나리오 3턴.

---

## 7. 리스크와 범위 밖

- **리스크 1 — `source` 판정이 경로별로 다르다**: 1단은 `task_results`, 2단은 `target_databases`,
  3단은 `active_db_id`. `resolve_thread_db_scope`가 셋을 합치므로 `db_ids`는 안전하나 `source`는
  `origin` 키가 없는 경로에서 `classified`로 떨어질 수 있다 — 테스트가 단별로 단언한다.
- **리스크 2 — 해제 뒤 첫 턴 규칙이 "역질문"이 아닐 수 있다**: 원문에 "서버"+전량 표면어가 없으면
  pre-gate는 비발동이고 후단 게이트(D-143 후속2 조건 7개)도 아닐 수 있다 → LLM 분류 팬아웃. 이것은
  현행 첫 턴과 동일한 동작이며 본 계획이 바꾸지 않는다. 칩 문구가 "다시 묻는다"가 아니라
  "**첫 질의처럼 확인**"인 이유.
- **리스크 3 — 동시 편집**: `docs/02_decision.md`·`plans/INDEX.md`는 오늘 병렬 계획(88·89)이 편집 중이다.
  등재 직전 `git status`·채번 재실측(D-161 · `plans/86`·`89` 선례).
- **범위 밖(명시)**: ① 스레드 복원 UI·`GET /conversation`에 `db_scope` 추가(복원 UI가 없어 소비처
  없음 — 생기면 같은 헬퍼로 1줄) ② "새 대화" 버튼(해제가 첫 창내 초기화 수단이 되지만 스레드
  자체를 바꾸는 건 별건) ③ 진행 패널 라벨화 ④ 존 동시 조회(D-176 예약 영역) ⑤ `scope_reexpand`가
  SSE done에 빠진 기존 비대칭 수정(관찰만 기록 — 별건, 수술적 변경 원칙).

---

## 8. 산출물·기록

- 본 계획서 `plans/90-thread-zone-scope-indicator.md` · `plans/INDEX.md` 90행
- `docs/02_decision.md` 「채번 이력」 D-205 예약 행(등재 완료 2026-09-09) → 구현 시 본문 `## D-205` +
  D-143 후속4 부기
- 구현 시 SDD 산출물: `SPEC-thread-db-scope.md`(계약 §3.1) · `tests/` 신규 3파일(§5)

---

## 9. v2 재검토 — APM·DPM 연동 시에도 이 계획이 적정한가 (2026-09-09 · 사용자 지시)

> **결론: 방향은 유지하되 계약 3곳을 지금 고친다.** v1은 존 축(은행존/공동존)만 전제해 `db_scope`를
> **db_id 평면 목록**으로 잡았다. 솔루션 축(폴스타/APM/DPM)이 오면 이 목록에 두 축이 뒤섞이고, 칩·옵션
> 엔드포인트·핀 의미를 전부 다시 설계해야 한다. 지금 고치면 스키마 형태와 엔드포인트 이름 수준이고,
> 나중에 고치면 칩 재설계 + 엔드포인트 하나 더 + D-205 개정이다. §9.3의 A1~A3을 v1 설계에 덮어쓴다.

### 9.1 APM·DPM은 어떤 형태로 들어오는가 — 실측

| 항목 | 근거 | 이 계획에 미치는 것 |
|---|---|---|
| 레지스트리는 **이미 2축**이다 — `solutions:`(1차 축, `order`·`backend`·`capabilities`·`requires`)와 `zone_groups:`(솔루션 **내부**의 2차 축, `solution: polestar`) | `config/db_registry.yaml:41-89` · 접근자 `zone_groups()`·`zone_group_of(db_id)`·`capability_providers()`(`registry.py:176-198`) | 존 그룹은 **폴스타의 하위 축**이다. "이 창의 존"은 솔루션과 독립된 개념이 아니라 폴스타 그룹의 속성이다 |
| APM·DPM 자리는 주석으로 예약 — `backend: rest`, **`requires: [host_location]`**(폴스타 선행) | `db_registry.yaml:55-67` · plans/82 §4.2 "존 그룹과 솔루션은 같은 타입 — 존만을 위한 특수 경로를 만들지 않는다"(`82:383`) | APM 질의는 **폴스타(host_location) → APM** 파이프라인이다(`82:490-503` G0→G2). 존 스코프는 G0에 걸리고 G2는 그 결과에 종속된다 |
| 제니퍼는 `jennifer_export` **DB 엔트리**(PG 적재본, `family: jennifer`) + REST + 이벤트 3중으로 편입 — **존은 J0에서 확정** | plans/87 §5.6(`87:640-649`) · G-2(`87:808`) | 존이 있는 APM DB가 `previous_db_ids`에 **db_id로** 섞여 들어온다. 존 축 판정(`zone_group_of`)은 레지스트리로 되지만 `ZONE_CLARIFY_OPTIONS` 리터럴에는 없다 |
| 범위 선택 도메인은 이미 **`axis` 키**를 낸다(`"axis": "zone_group"`) · 솔루션 축은 `selected_scope: {axis:"solution", keys}`로 예고 | `src/domain/scope_select.py:141` · plans/82 §6(`82:674-684`) | 축 어휘가 있다. v1의 `GET /api/v1/zones`는 이 어휘 밖에서 존만 다루는 **두 번째 옵션 경로**가 된다 |
| ★ `selected_db_ids`는 플래너를 **단일 `data_query` task로 단락**시킨다 | `intent_planner.py:295-303` `_single_task_plan("data_query", db_ids=selected)` · API에 `selected_scope`는 **미구현**(grep 0건) — 범위 선택 답도 `selected_db_ids`로 실린다 | v1 §2.2(A)의 핀은 이 채널에 얹힌다. APM이 등록된 뒤 칩으로 은행존을 고르고 *"abd00 WAS 힙"* 을 물으면 **b0 단일 SQL task**가 되어 G2(APM)가 아예 만들어지지 않는다 — 존 핀이 솔루션을 잘라내는 **부작용** |
| 승계는 `previous_db_ids[0]` **단일 후보** | `subagents.py:466-471` | 직전 턴이 폴스타+APM 두 db_id를 남기면 다음 폴스타 질의가 `[0]`(관련도 순)에 따라 **APM DB를 승계**할 수 있다. plans/82 Wave 7·87 J5의 결손이지 본 계획의 결손은 아니다 — 다만 칩이 이 드리프트를 **보이게** 만든다(이득) |
| `_zone_group_exclusive`·`ZONE_CLARIFY_OPTIONS`·1단 `_AMBIENT_KEYS` 보정 | `query.py:877-902` · `query_gen_common.py:1369` · §1.4 | **영향 없음** — 상호배타는 폴스타 존 그룹의 규칙이고 APM db_id는 그룹 판정에서 None으로 빠진다. `_AMBIENT_KEYS` 보정은 축과 무관 |

DPM은 레지스트리 예약이 APM과 동형(`requires: [host_location]`, `backend: rest`)이라 위 판정이 그대로 적용된다.

### 9.2 v1에서 깨지는 곳 3건

1. **`db_scope.db_ids` 평면 목록 + `group`/`labels`를 `ZONE_CLARIFY_OPTIONS`에서 붙이는 설계.** APM 턴 뒤
   `["polestar_b0", "jennifer_export"]`가 되고, 라벨은 폴스타 리터럴에 없어 `display_name` 폴백, `group`은
   첫 항목 기준 — 칩이 "은행존, 제니퍼"처럼 **축이 다른 것을 한 줄에** 보여준다. 리터럴 복제 대신
   레지스트리 접근자를 써야 새 DB가 자동으로 맞는 축에 놓인다.
2. **`GET /api/v1/zones`.** 존 전용 엔드포인트라 솔루션 축 옵션이 생기면 하나 더 만들거나 이름을 바꿔야
   한다. `scope_select`가 이미 `axis` 어휘를 쓰므로 **축 배열을 반환하는 하나의 엔드포인트**여야 한다.
3. **칩 핀의 의미가 미정의.** v1은 "선택 = `selected_db_ids` 1회 전송"으로 끝냈다. 오늘은 존 축뿐이라
   문제가 없지만, 플래너 단락(`intent_planner.py:295`)이 **"대상 집합 전체를 이 db_ids로 치환"** 이라
   솔루션 축이 생기면 존 핀이 파이프라인을 자른다. D-205에 **"칩 핀은 존 그룹 제약이지 대상 집합
   치환이 아니다"** 를 명문화하지 않으면 82 Wave 7이 이 단락을 손댈 때 근거가 없다.

### 9.3 개정(A1~A3) — v1 §3.1·§3.4에 덮어쓴다

**A1. `db_scope`를 축 구조로** (§3.1 대체)

```jsonc
"db_scope": {
  "zone_group": {"code": "bank", "label": "은행존", "db_ids": ["polestar_b0"]} | null,   // 폴스타 2차 축
  "solutions": [{"code": "polestar", "label": "폴스타", "db_ids": ["polestar_b0"]}],     // 1차 축(오늘은 항상 1개)
  "db_ids": ["polestar_b0"],            // 다음 턴 승계 집합 원본(resolve_thread_db_scope) — 디버그·감사용
  "source": "selected" | "inherited" | "hint" | "classified" | "dependent" | "none"
}
```
- `zone_group`은 `registry.zone_group_of(db_id)`로, `solutions`는 `DBEntry.family` ↔ `SolutionSpec.family`로
  판정한다(**리터럴 0 — `overfit_check` 부담 없음**). 라벨은 `zone_groups[].label`·`solutions[].label`.
- 칩은 **`zone_group`만** 본문으로 그린다(사용자 요청 축). `solutions`는 `len(registry.solutions()) > 1`일
  때만 보조 줄로 그린다 — 오늘은 조건이 거짓이라 **v1과 화면 동일**.
- `source`에 `dependent`(파이프라인 후속 그룹 — `scope_from`으로 좁혀진 대상, plans/82 G2)를 예약한다.
  문자열 필드로 두고 닫힌 enum으로 만들지 않는다.

**A2. 옵션 엔드포인트를 축 배열로** (§3.1 `GET /api/v1/zones` 대체)

```jsonc
GET /api/v1/scope/options →
{"axes": [{"axis": "zone_group", "exclusive": true, "options": [{"key":"bank","label":"은행존","db_ids":["polestar_b0"]}, …]}]}
```
- `axis`·`key`·`db_ids` 어휘는 `scope_select` 페이로드(`scope_select.py:125-150`)와 같다 — 프론트 렌더러
  하나가 역질문·범위 선택·칩 팝오버를 모두 그린다. 솔루션 축은 82 Wave 7이 `axes`에 항목을 **추가**할 뿐
  엔드포인트·프론트 분기는 늘지 않는다.
- 존 그룹 옵션의 원천은 `registry.zone_groups()` + `zone_to_db_ids()` ∩ 활성 DB. `ZONE_CLARIFY_OPTIONS`와의
  동등성은 기존 정합 테스트(`test_execution_groups.py::test_group_values_match_registry`)에 단언 1건을 더한다.

**A3. 핀 의미를 D-205에 명문화** (§2.2·§3.3 보강)

- **칩 핀 = 폴스타 존 그룹 제약.** 전송 채널은 오늘의 유일한 채널 `selected_db_ids`를 그대로 쓰되,
  D-205 결정문에 *"`selected_db_ids`는 존 축 제약이며, 솔루션 축이 등록되면 플래너 단락(`intent_planner.py`
  ②.5)은 **'대상 치환'이 아니라 '폴스타 그룹 필터'로 재정의**해야 한다"* 를 적는다. 그 재정의의 착수
  주체는 plans/82 Wave 7(`solution-pipeline`) · plans/87 J5이며, 본 계획은 **선행 조건을 남길 뿐 손대지 않는다**
  (오늘은 솔루션이 1개라 두 해석이 같은 결과 — 동작 변화 0).
- 해제(`reset_db_scope`)는 **전 축 초기화**로 유지한다 — 축별 해제는 솔루션 축 UI가 생길 때 `axis` 인자로
  확장 가능하고, 오늘 필요가 없다.
- 승계 단일 후보(`previous_db_ids[0]`)의 다중 솔루션 드리프트는 **범위 밖**으로 두되 §7 리스크에 등재하고
  82·87에 접점으로 통보한다(칩이 드리프트를 즉시 보이게 하므로 발견 비용은 오히려 내려간다).

### 9.4 개정 후 작업 분해 델타

| # | 변경 |
|---|---|
| T1 | `resolve_thread_db_scope(state)`가 db_ids만이 아니라 **축 구조**(A1)를 만든다 — 레지스트리 접근자 사용 |
| T3 | `db_scope` 스키마를 A1로 · `GET /api/v1/scope/options`(A2) · 정합 단언 1건 |
| T5 | 칩 렌더러가 `axes` 페이로드를 받도록(역질문·범위 선택과 렌더러 공유) · 보조 줄은 조건부 |
| T6 | D-205 본문에 A3 문구 · plans/82 §14·plans/87 §J5에 접점 1줄씩 통보(각 소유자 계획서의 편집은 그쪽 착수 시) |
| 규모 | 서버 +40줄 · 프론트 ±0(렌더러 공유로 오히려 감소 가능) · 테스트 +30줄 |

### 9.5 게이트 영향

G-1~G-4는 그대로다. 새 게이트는 없다 — A1~A3은 "미래에 두 번 만들지 않기 위한 형태 변경"이고 사용자
가시 동작은 오늘 v1과 같다. 단 **G-2(선택 강도)의 ⓑ(매 턴 하드 핀)는 솔루션 축 도입 시 파이프라인 절단이
매 턴 일어나므로 v2에서 비채택 근거가 하나 더 붙는다.**

---

## 10. 구현 결과 (2026-09-09 · D-205)

- **모듈 6개 전부 랜딩**(`CAPABILITY-MAP-90.md` 빌드 순서대로): `deep-agent-selected-db` → `db-scope-resolve`(`src/routing/db_scope.py`) → `db-scope-source` → `db-scope-reset` → `db-scope-contract`(`db_scope` 4경로 · `reset_db_scope` · `GET /api/v1/scope/options`) → `scope-chip-ui`.
- **계획서에서 이탈한 지점 3건(근거는 `SPEC-thread-db-scope.md` Open Questions)**: ①pre-gate ⓒ는 "승계 스코프 부재"가 아니라 **해제 턴에만** 첫 턴 규칙 — 기존 테스트가 스코프 없는 후속 턴의 비발동을 계약으로 고정하고 있었다 ②파일 라우트 reset 필드 미추가 — `create_initial_state`가 매 파일 턴을 초기 상태로 시작해 죽은 입력 ③출처는 `target_databases[].origin`이 아니라 top-level `db_scope_source` — shape 정확 일치 테스트 존중. 그리고 §9.3 A2의 옵션 원천은 레지스트리 `zone_groups`가 아니라 **DB 입도의 `ZONE_CLARIFY_OPTIONS`**(공동존 김포/여의도를 따로 골라야 하고 레지스트리 `display_name`은 서술형) — 그룹 값의 레지스트리 동등성은 테스트가 단언.
- **검증**: 신규 테스트 80건 · 회귀 1932 passed(원격 병합 `b45ac9e` 후 재실행) · `arch_check`·`overfit_check` 0 · `node --check` 통과 · 실 LLM 0.
- **채번**: 최초 D-200 → 원격 병합이 D-200을 선점해 **D-205**로 재부여(협업 세션이 참조 일괄 치환).
- **잔여**: T7 실 브라우저·실 파이프라인 3턴 시나리오(과금 — D-127 건별 승인).
- **작업 중 실수 1건**: 기준선 대조에 `git stash`를 씀 — `docs/18` 2026-09-09 등재.

---

## 11. v3 — 존 동시 선택·순차 조회 개방 (2026-09-09 · 사용자 지시 · D-206)

> 지시 원문: *"은행존과 공동존을 라디오 버튼이 아닌 체크 박스로 같이 조회하는 방법으로 할 수 있게 구현하라. UI만
> 확인하지 말고 조회도 순차적으로 진행할 수 있도록 구현해야 한다."* — D-143 후속3(상호배타)과 D-176("존 동시
> 조회는 아직 열리지 않았다")을 사용자가 뒤집는 결정이다.

### 11.1 실측 — 열 수 있는 것과 죽어 있던 것

| 항목 | 실측 | 판정 |
|---|---|---|
| 상호배타 4지점(pre-gate·파일 게이트·후단 게이트 2곳)·UI 라디오·문구 | 전부 `ZONE_GROUP_EXCLUSIVE` 뒤(`config.py:548` 기본 `true`, plans/82 U1 (a) "false면 분할") | **코드 변경 0 — `.env` 한 줄** |
| 존 순차 루프 `_run_groups`(`multi_db_executor.py:758`) · 계측 · 부분 결과 패킷 | 구현돼 있으나 **`execution_groups`를 state에 싣는 코드가 0건** → 항상 단일 그룹 폴백(한 run에 b0·gp·yd 혼재) | ★**죽은 경로 — 배선 보정 필요** |
| 승계 `_apply_db_succession` | `previous_db_ids[0]` 단일 후보 → 혼합 스레드가 다음 턴에 첫 DB로 좁혀짐 | 개방 시 전체 승계 필요 |
| `db_scope.zone_group` | 단일 그룹 전제(v2 A1) | `zone_groups` 복수 필요 |
| 세 단 모두 `multi_db_executor`를 공유(`subagents.py:1134`, 1단은 도구 경유) | — | 실행기 안에서 나누면 단 대칭 자동 |

### 11.2 구현 (D-206)

- `.env` `ZONE_GROUP_EXCLUSIVE=false`(코드 기본값 유지 · `.env.example`에 키 문서화). **서버 재기동 필요**(플래그 1회 해석).
- `multi_db_executor`: `execution_groups` 없음 & 플래그 `false` → `_auto_execution_groups`(`partition_execution_groups`, 그룹 2개 이상일 때만) → `_run_groups`. 설정 부재·MagicMock은 배타로 간주(회귀 0).
- `subagents._apply_db_succession(inherit_all=not exclusive)` · task 결과 `group_results` 승격 → `_summarize_tasks` → 진행 패널 "실행 그룹(순차): 은행존 N건 · Xs → 공동존 M건 · Ys".
- `db_scope.zone_groups`(query_order 순) · 칩 라벨 "은행존 + 공동존" · 비배타 문구에 순차 안내(역질문·파일·팝오버).

### 11.3 검증 · 리스크 · 잔여

- 신규 테스트 20건(`test_multi_db_auto_groups` · `test_mixed_zone_succession` · 기존 4파일 보강) · 회귀 1955 passed · arch/overfit 0.
- **리스크**: b0+gp FabriX PII 차단(D-155 후속) 미종결 — 그룹별 run 격리로 은행존 결과는 살아남고, 재발 시 롤백은 env 한 줄. 운영 LLM이 gemini인 로컬 `.env`에서는 해당 없음.
- **잔여**: 부분 결과 즉시 노출 UI(`group_packets` — plans/82 Wave 6 `group-ui`) · `plans/82` §14 U1 상태 갱신(소유 세션) · 실 브라우저·실 파이프라인 확인(과금 승인).

---

## 12. v4 — 상호배타 개방을 **코드 기본값**으로 (2026-09-23 · 사용자 지시)

> 지시 원문: *"기본값을 변경하고 검토한 내용을 계획서에 반영하라."* — 앞선 질문
> *"현재 구현이 은행존과 공동존을 같이 선택할 수 없도록 되어 있다. 이유가 뭐냐?"* 에 대한 검토의 결론이다.

### 12.1 검토 — 왜 아직 막혀 보였나

D-206(§11)이 개방을 확정했는데도 **막힌 화면을 보게 되는 경로**가 남아 있었다. 기본값이 `.env` 한 줄에만
있었기 때문이다.

| 확인 | 실측(2026-09-23) | 판정 |
|---|---|---|
| 코드 기본값 | `config.py` `zone_group_exclusive` **`default=True`** | 개방은 `.env`가 있어야만 성립 |
| 로컬 `.env` | `ZONE_GROUP_EXCLUSIVE=false`(90행) · `AppConfig().multi_db.zone_group_exclusive` → `False` | 이 트리에서는 열려 있었다 |
| `.env.example` | 해당 줄이 **주석**(473행) | 예시로 만든 배포는 기본값 `true` = 차단 |
| `.env` 추적 | git 미추적 | 확정이 **클론·배포에 따라가지 않는다** |
| 플래그 소비 지점 | 라우트 pre-gate·파일 게이트·후단 게이트 2곳·`multi_db_executor`·`scope/options`·UI 2곳 전부 설정을 읽는다(하드코딩 배타 0건) | 기본값만 바꾸면 전 지점이 따라온다 |

즉 "구현이 막아 둔 것"이 아니라 **기본값이 확정과 반대**였다. 이것은
[`confirmed-settings-become-code-defaults`] 지시(2026-09-10 — *".env는 ignore 파일이라 적용되지 않을 수
있다. 필요하면 설정값을 기본 동작으로 코드를 수정하라"*)가 D-206(2026-09-09) **다음 날** 나왔기 때문에
D-206 당시에는 적용되지 않은 것이다.

도입 근거 2건의 현재 상태도 함께 봤다.

- **근거 ①(담당 조직 분리 — 존 조합 실수요 없음, 2026-08-05 확정)**: 2026-09-09 사용자 지시로 **뒤집혔다**(D-206).
- **근거 ②(b0+gp FabriX PII 차단 · D-155)**: **미종결**이다. 다만 D-206의 그룹별 `_prepare_multi_run`
  격리로 은행존 재료와 공동존 재료가 한 요청에 섞이지 않는다 — 회피 수단이 코드에 있다.

### 12.2 변경 (코드 4파일 · 동작 1건)

| 파일 | 변경 |
|---|---|
| `src/config.py` | `zone_group_exclusive` **`default=True` → `False`**. 주석에 전환 근거(근거 ① 뒤집힘 · 근거 ② 격리로 회피 · `.env` 미추적 · `plans/80` §5.4-③의 명시적 예외 · 되돌리는 길)를 남겼다 |
| `.env.example` | "코드 기본값은 true" → **"코드 기본값이 false"**, 주석 줄은 기본값과 같아 적지 않아도 된다는 안내 + 롤백 경로(`=true`) |
| `config/settings_help/multi_db.yaml` | `기본` 라벨을 `"true"` → `"false"` 로 이동(기본 on 관례의 반대 적용) · `summary`에 기본 꺼짐 명시 · `stability`·`recommendation`을 전환 후 문장으로 · `references`에 D-206 후속 |
| (변경 없음) | 게이트·UI·실행기 코드 — 전부 설정을 읽으므로 손댈 것이 없다 |

**`getattr(..., True)` 폴백은 그대로 두었다.** 이 폴백은 *구버전 `config.py`가 배포된 경우*만 탄다 —
그 배포는 실행기·UI도 구버전이라 배타 동작이 맞다. 새 `config.py`가 있으면 폴백은 도달하지 않는다.
`multi_db_executor._zone_group_exclusive`가 MagicMock을 배타로 보는 규칙도 유지했다(테스트 대역 가드).

### 12.3 검증

- 기본값 실측: `MultiDBConfig(_env_file=None).zone_group_exclusive` → **`False`** · `AppConfig()` → `False`.
- 존 게이트 관련 8파일 **185건 통과**(전환 전 동일 범위 123건 통과 — 모든 테스트가 플래그를 명시 주입해
  기본값에 의존하지 않는다. 그래서 이 전환으로 갱신해야 할 단언이 0건이다).
- 전체 회귀 **5,845 passed · 22 skipped**(432초). 유일한 실패
  `test_global_synonym_guard.py::test_repository_manual_profiles_are_detected`는 **이 변경과 무관**하다 —
  병행 세션이 만든 미추적 파일 `config/db_profiles/itam.yaml`(plans/95)을 그 테스트가 "수동 프로필 없음"으로
  단언하기 때문이다. 소유 세션 확인 사항.
- `arch_check --ci` exit 0 · `overfit_check --ci` exit 0.

### 12.4 잔여 · 되돌리는 길

- **서버 재기동이 필요하다** — 플래그는 기동 시 1회 해석한다.
- 폐쇄망 배포의 `.env`에 `ZONE_GROUP_EXCLUSIVE=true`가 **명시**돼 있으면 이 전환이 덮이지 않는다. 그
  줄을 지워야 기본값이 산다(배포별 확인 사항 — 이 세션에서는 폐쇄망 `.env`를 보지 못했다).
- b0+gp PII 차단이 재발하면 `ZONE_GROUP_EXCLUSIVE=true` 한 줄이 D-143 후속3 동작 전부를 복원한다.
- §11.3 잔여(부분 결과 즉시 노출 UI · `plans/82` §14 U1 상태 갱신 · 실 파이프라인 확인)는 그대로다.
