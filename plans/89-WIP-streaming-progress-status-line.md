# 89. 스트리밍 응답 진행 상태 표시 — 커서 아래 현재 단계·경과 시간 · 무이벤트 구간 계측

> **작성일**: 2026-09-09
> **성격**: 구현 계획(implementation-ready) · **상태: 구현 중(-WIP, 2026-09-09) — T0~T3·T5·T7·T9 랜딩, 잔여 T4(schema_analyzer 샘플 수집 마일스톤)·실 브라우저 체감 확인(과금 승인). T8 완료 — D-204 본문 등재(2026-09-09) · 회귀 1265 passed** ·
> v2(2026-09-09): `plans/88` 연계 — 복합 질의 단계 표시(§1.4 · §3.4 · T9 · G-5) 추가 ·
> v3(2026-09-09): 구현 랜딩 — 게이트 G-1~G-5는 권고안을 가정으로 채택(사용자 지시 "구현을 진행하라"), SDD 산출물
> `CAPABILITY-MAP-89.md` · `SPEC-sse-progress-contract.md` · `SPEC-composite-task-progress.md` · `SPEC-stream-status-ui.md` ·
> `tasks/plan-89.md` · `tasks/todo-89.md`. 신규 테스트 52건(계약 15 · 스파이크 4 · 헬퍼 10 · UI 정적 20 + 상류 `test_stream_guard` 3 통과) · 회귀 1215 passed · arch 0 · overfit 신규 유입 0 · 실 LLM 0
> **요청 취지(사용자 지시 원문)**: *"웹 UI 화면의 LLM응답 출력창에서 응답시간이 오래 걸릴 경우
> 커서만 깜박이고 있어 진행중인지, 중단되었는지 알 수 없는 상황이다. gemini나 chatgpt처럼
> 사용자 응답을 분석하거나 분석한 내용에 따라 처리가 진행되는 사항을 커서 아래쪽에 표현해주면
> 진행중임을 알 수 있을 것으로 보인다."*
> **관련 계획**: **`plans/88`(복합 질의 순차 의존 — R-1 미실행 사유 · R-5 경과 블록 · R-7 절단 · `skipped` 상태가
> 이 계획의 UI 접점이다, §3.4)** · `plans/90`(스레드 존 스코프 칩 — 같은 채팅 화면에 붙는 UI라 어휘·색을 통일한다)
> **관련 결정**: D-009(최종 합성만 토큰 스트리밍) · D-039(오케스트레이션 노드 처리 현황 화이트리스트 +
> `_extract_node_progress`) · D-066 후속(SSE 이벤트 fetch당 타임아웃) · D-154(schema_analyzer 무이벤트
> 구간 타임박스 — **"무이벤트 구간"이 이미 장애 원인으로 한 번 기록됐다**) · D-193(코드 기본값이 아니라
> `.env` 실제값으로 타임아웃 판단) · D-127(과금 API 승인 게이트) · D-161(경로 폐기 실측 4항) ·
> `plans/80` §5.4-③(신규 플래그 기본 off 원칙)
> **신규 결정 예약**: **D-204**(스트리밍 진행 상태 계약 — `progress`·`heartbeat` 이벤트 + 커서 아래
> 상태줄) — `docs/02_decision.md` 「채번 이력」 표에 예약 등재(2026-09-09, 병렬 계획 `plans/88`의 D-203 바로 위 행). 계획서에만 적은 예약은
> 효력이 없다(D-161 부기).
> ※ 채번 실측 2026-09-09 — `## D-` 헤더 최댓값 **197** · 「변경 이력」 표 최댓값 **197** ·
> 「채번 이력」 표: D-195(`plans/87` 제니퍼 APM) · D-196(`plans/54`) 점유. 최초 작성 시 D-203로
> 잡았으나, **같은 날 병렬 작성된 `plans/88`(복합 질의 순차 의존)이 D-203을 헤더에 선점**해
> 등재 직전 재실측으로 **D-204**로 옮겼다 — 채번은 반드시 **쓰기 직전**에 다시 확인한다(D-161 ·
> `plans/86`과 같은 사례). 계획서 번호도 같은 이유로 **89**를 쓴다(87=제니퍼 APM · 88=순차 복합 질의).
> 파일명 접미사 `-TODO`는 INDEX 「파일명 상태 접미사」 규칙(코드 0건) 적용.
> **실측 기준**: 아래 모든 `file:line`·값은 2026-09-09 현 브랜치(`multiintent`, `9945284`)에서 직접
> 확인했다. 실 LLM 호출 0.

---

## 0. 요약 — "커서만 깜박이는" 현상의 정체는 세 겹이다

사용자가 보는 증상은 하나지만, 원인은 **백엔드 1겹 + 프론트 1겹 + 계약 부재 1겹**이다.
한 겹만 고치면 증상이 남는다.

| 겹 | 위치 | 무엇이 빠져 있나 | 결과 |
|---|---|---|---|
| **① 백엔드 — 운영 경로의 노드가 알림 대상이 아니다** | `src/api/routes/query.py:1219-1231` (`_known_nodes`) | 사다리 **1단 정본 `deep_agent`가 화이트리스트에 없다**. `.env:180` `ENABLE_DEEPAGENTS_PACKAGE=true`라 운영은 1단이다 | `field_mapper` 이후 최종 토큰까지 **SSE 이벤트가 한 건도 안 나간다** |
| **② 백엔드 — 에이전트 내부는 원래 조용하다** | `src/orchestration/deep_agent.py:227` (`agent.ainvoke`) · `deepagents_tools.py:276` (`spec.handler` 직접 호출) | 오케스트레이터 LLM 호출·도구 실행·재개 루프(D-093)가 **바깥 그래프의 노드가 아니다**. 하위 파이프라인(schema_analyzer 등)은 노드가 아니라 **함수로 호출**돼 `on_chain_start`가 없다 | ①을 고쳐 `deep_agent` 시작 1건을 보내도, 그 뒤 수십 초는 여전히 침묵 |
| **③ 프론트 — 진행 표시를 스트림 시작 순간에 지운다** | `src/static/js/app.js:1120` (`removeProcessingMessage()`) → `:1121` (`createStreamingMessage()`) | 단계 칩(`#processingStages`)이 있던 말풍선을 **SSE 연결 직후 제거**하고, 커서(`#streamingCursor`)만 있는 새 말풍선으로 바꾼다. 이후 `updateProcessingStage()`(`:857`)는 대상 DOM이 없어 **무동작**이다 | 노드 이벤트가 와도 채팅창에는 반영되지 않는다 — 오른쪽 "처리 현황" 패널에만 쌓인다 |
| **④ 계약 — 살아 있음을 알릴 신호가 없다** | `query.py:1186-1204` (`wait_for(__anext__, timeout=effective_timeout)`) | 서버는 이벤트가 **없으면 아무것도 보내지 않고** `API_QUERY_TIMEOUT`(`.env:119` = **240s**, 코드 기본값 60이 아님 — D-193)까지 기다린다. 클라이언트에는 유휴 감지가 없다 | "진행 중"과 "죽음"을 **최대 4분간 구별할 수 없다** — 사용자 표현 그대로 |

**따라서 이 계획의 중심은 UI 위젯이 아니라 "진행 신호의 계약"이다.** 상태줄은 신호를 받아
그리는 마지막 조각일 뿐이고, 신호가 없으면 상태줄도 "처리 중..." 고정 문구로 다시 침묵한다.

**복합 질의(`plans/88`)에서는 침묵이 더 길고 더 위험하다.** *"CPU 높은 서버를 찾아 그 서버들의 최근 1개월
CPU"* 는 조회가 두 번 순차로 돌고, 2단 경로는 **모든 단계가 끝난 뒤에야** 작업 목록이 한 번 온다(§1.4).
88이 넣는 "선행 0건 → 후속 미실행" 판정(R-1)은 응답 말미 경과 블록(R-5)에만 실리므로, 사용자는 기다리는
동안 지금 몇 단계째인지·앞 단계가 몇 대를 골랐는지·왜 다음 단계가 건너뛰어졌는지를 알 수 없다. 이 계획은
그 단계 정보를 **실행 중에** 커서 아래로 끌어내리고, 완료 후 R-5 블록과 **같은 데이터**로 일치시킨다(§3.4).

---

## 1. 실측 — 지금 사용자는 정확히 무엇을 보는가

### 1.1 운영 경로(1단 `deep_agent`) 타임라인

`.env` 실제값: `ENABLE_DEEPAGENTS_PACKAGE=true`(`:180`) · `ENABLE_INTENT_ORCHESTRATION=true`(`:173`) ·
`ENABLE_SEMANTIC_ROUTING=true`(`:153`) · `ORCHESTRATOR_PROVIDER=gemini`(`:187`) → 사다리 1단.

| 시점 | 서버 | SSE 이벤트 | 채팅창 | 오른쪽 패널 |
|---|---|---|---|---|
| 전송 직후 | — | — | `#processingMessage`(점 3개 + 단계 칩 5개, `app.js:799-831`) | 초기화 |
| SSE 연결 | `event_generator` 진입 | — | **처리 말풍선 제거 → 커서만 있는 말풍선**(`:1120-1121`) | — |
| ~0.1s | `context_resolver`·`input_parser`·`field_mapper` | `node_start`/`node_complete` ×3 | 변화 없음(대상 DOM 없음) | 단계 3줄 |
| 0.1s ~ **수십 초** | `deep_agent`: 오케스트레이터 LLM ↔ 도구(`query_infra_db` 등) 반복 + D-093 재개 | **없음** | 커서 깜박임 | 변화 없음 |
| 말미 | `result_aggregator` 최종 합성(`USER_RESPONSE_TAG`, `result_aggregator.py:681`) | `token` 연속 | 텍스트 출력 | — |
| 완료 | `on_chain_end`(`final_response`) | `meta` → `done` | 커서 제거·메타 | — |

침묵 구간에서 사용자가 얻는 정보는 **0 bit**다. 이는 SLO(단순 <10s · 복합 <30s · 문서 <60s,
CLAUDE.md)의 대부분을 차지하는 구간이다.

> 소요시간 분포 실측은 **불가**했다 — `logs/audit-*.jsonl`에는 `processing_time_ms`류 필드가
> 없다(2026-09-07 파일 키: `timestamp, event, entry_point, targets, …`). 계획은 SLO와 타임아웃
> 실제값(240s/300s)을 기준으로 판단한다. 분포 계측은 §8 잔여 항목.

### 1.2 다른 경로도 정도만 다를 뿐 같은 병이다

| 경로 | 침묵 구간 | 근거 |
|---|---|---|
| 2단 `intent_orchestration` | `agent_orchestrator` 내부 서브에이전트 루프 | 화이트리스트에는 있으나(`:1225`) 내부 도구 호출은 노드가 아니다. `query.py:1260` 주석이 "중간 LLM 호출이 같은 노드에서 일어난다"고 명시 |
| 3단/4단 단일 DB | `schema_analyzer` 라이브 샘플 수집 | **D-154가 이미 겪었다** — "무이벤트·무로그 구간"이 SSE 타임아웃으로 터져 8s/20s 타임박스를 넣었다. 타임박스는 *죽지 않게* 했지 *보이게* 하진 않았다 |
| `ainvoke` 폴백(`:1349-1361`) | 전 구간 | 완료 시 `token` 1건으로 통째 전달 |
| 파일 질의 `/query/file/stream`(`:1696`) | 위와 동일 | 스트림 제너레이터가 **복사본**(`:1842-1906`)이라 수정은 두 곳 대칭 필수(Known Mistakes 「단일/멀티 경로 대칭」) |

### 1.3 프론트 자산 — 있는 것과 없는 것

| 있음(재사용) | 위치 |
|---|---|
| 단계 칩 마크업·CSS(`.processing-stages .stage`, `active/done`) | `app.js:809-813` · `style.css:2706-2747` |
| 점 3개 스피너(`.processing-dots`) + 문구(`#processingText`) | `app.js:821-824` · `style.css:2664-2703` |
| 노드 → 단계 매핑 `nodeToStage` · 문구 `stageMessages` | `app.js:841-849` · `:345-351` |
| 노드 라벨·툴팁(`nodeLabels`·`nodeTooltips`) | `app.js:355~` |
| SSE 파서 — **미지 `type`은 무시**(else 분기 없음) → 이벤트 추가가 구 클라이언트를 깨지 않는다 | `app.js:1145-1170` |
| 중단(Stop) 경로 `markStreamInterrupted` — 커서 제거 후 "중단됨" 표기 | `app.js:1012-1045` |

| 없음(신설) | 비고 |
|---|---|
| 스트리밍 말풍선 안의 상태 영역 | `createStreamingMessage()`는 text·cursor·meta·sql 4개 슬롯뿐(`:994-1001`) |
| 경과 시간 표시 | 완료 후 `TIME` 메타만 있다(`:1235~`) |
| 유휴(무이벤트) 감지 | `currentAbortController`는 사용자 중단 전용(`:665-672`) |
| 도구 이름 → 한국어 라벨 | `_TOOL_NAMES`(`deepagents_tools.py:54-63`)는 서버에만 있다 |

오른쪽 패널은 **1024px 이하에서 `display:none`**(`style.css:4230`, `:4295-4298`)이고 데스크톱에서도
접을 수 있다(`app.js:469`). 즉 **채팅창 안의 표시가 유일한 보편 채널**이다 — 패널 보강만으로는
요청을 충족하지 못한다.

### 1.4 복합 질의 경로에서 지금 보이는 것(`plans/88` 연계 실측)

| 항목 | 실측 | 의미 |
|---|---|---|
| 2단 작업 목록 이벤트 | `agent_orchestrator`의 `node_complete`는 **전 레벨 종료 후 1회**(`query.py:487-494` — `run` 반환 `task_plan`/`task_results`를 `_summarize_tasks`로 요약). 레벨 루프(`agent_orchestrator.py:74-92`)는 `status="in_progress"` 전이를 하지만 **밖으로 내보내지 않는다** | 실행 중 "1/2 단계 완료 → 7대 선별, 2/2 진행 중"을 **보여줄 데이터가 지금은 없다** |
| 1단 도구 순서 | `_run_subagent_tool`이 `order = len(collector)+1`로 task를 만들고(`deepagents_tools.py:255-274`) `collector`에 (task, result)를 적재 | 도구 호출 1건 = 단계 1개. `on_tool_start` 입력(`sub_query`)만으로도 "n번째 조회: …"는 그릴 수 있고, 행수·스코프는 핸들러 결과가 있어야 한다 |
| 패널 작업 목록 상태 | `renderTaskList`(`app.js:2477-2497`)는 `completed/failed/in_progress` + 그 외 → **"대기"** | 88 W1이 도입하는 **`status="skipped"`(선행 0건·실패)가 "대기"로 오표기**된다 — 88 §9 R-A가 UI 소비처를 지목한 그 지점. 89가 함께 고치지 않으면 R-1 사유가 UI에서 사라진다 |
| 88의 경과 렌더(R-5) | `result_aggregator`에 결정적 블록 "## 순차 처리 경과"를 **응답 본문 말미**에 append(88 §4.4) | 사후 기록이다. 실행 중 표시와 **데이터 원천(`dependency_verdicts`·`ScopeConformance`)을 공유**해야 두 표시가 어긋나지 않는다 |
| 라벨 사전 | `agentLabels`(`app.js:397-403`) 5종 — `process_query`·`host_inspect`·`fault_diagnosis` 없음 | 상태줄이 같은 사전을 쓰면 빠진 agent가 원시 이름으로 노출된다 → 보강 |

---

## 2. 요청 해석 — "gemini나 chatgpt처럼"이 뜻하는 것과 뜻하지 않는 것

두 제품의 공통 패턴은 응답 본문 **위/아래에 한 줄짜리 활동 표시**("검색 중…", "분석 중…",
"N개 소스 확인")가 있고, 필요하면 펼쳐서 **지나간 단계 목록**을 보는 것이다.

이 계획이 채택하는 것:

- **(a) 활동 한 줄** — 현재 하는 일 + 경과 시간 + 살아 있음(스피너). 커서 바로 아래.
- **(b) 지나간 단계** — 접이식 칩/목록. 기본은 접힘, 완료 단계 수만 노출.
- **(c) 상태 3분법** — *진행 중*(신호 수신 중) / *대기 중*(신호 끊김, 아직 타임아웃 전) / *중단·오류*.
  사용자가 물은 "진행중인지 중단됐는지"에 직접 답하는 것은 (c)다.

채택하지 않는 것(범위 밖 — §7):

- LLM의 **사고 과정 텍스트**("사용자는 …를 원하는 것 같다") 노출. 오케스트레이터의 자유 서술은
  D-093/D-092가 신뢰하지 않기로 한 대상이며, 상태 문구를 LLM에 만들게 하면 **비결정성·과금·PII
  필터 경로**가 UI에 그대로 들어온다. 상태 문구는 **도구명·노드명 → 결정적 라벨**로만 만든다.
- 응답 본문 스트리밍 방식 변경. 최종 합성 토큰 스트리밍(D-009)은 그대로다.

---

## 3. 설계

### 3.1 SSE 이벤트 계약(D-204) — 기존 6종 유지 + 2종 추가

| type | 페이로드 | 발생 | 후방 호환 |
|---|---|---|---|
| `progress` (신설) | `{node, kind: "tool"\|"step"\|"task", name, phase: "start"\|"end", label?: str, timestamp_ms, task?: {…}}` | 도구 시작/종료(§3.2-②), 핸들러 내부 마일스톤(§3.2-③), **복합 질의 단계(§3.4)** | 구 클라이언트는 무시(`app.js:1145` 실측) |
| `heartbeat` (신설) | `{elapsed_ms, last_activity_ms}` | 무이벤트 구간 N초마다(§3.2-④) | 동일 |
| `node_start`·`node_complete`·`token`·`meta`·`done`·`error` | 불변 | | |

`kind:"task"`의 `task` 페이로드(§3.4): `{task_id, order, total, agent, sub_query, input_from: [order…], status:
"in_progress"|"completed"|"failed"|"skipped", row_count?, scope_col?, scope_size?, truncated?, truncated_count?,
reason?: "prior_failed"|"prior_empty"|"prior_no_identity", notes?: ["sequential_not_applied"]}` — 88 §4.1
`DependencyVerdict`와 **필드명을 동일하게** 둔다(변환 계층 없음).

원칙: **서버는 원시 이름(`name`)을 보내고 라벨은 클라이언트가 붙인다** — 기존 `nodeLabels` 패턴과
동형이고, 서버 사전이 UI 문구를 소유하지 않는다. `label`은 핸들러가 명시 문구를 갖는 경우(예:
"샘플 수집 3/12 테이블")에만 선택적으로 실린다.

### 3.2 백엔드 — 신호 원천 4개

**① 화이트리스트 보정(`query.py:1219-1231`, `:1847-1859` 대칭)** — `deep_agent`·`fault_diagnosis`·
`cache_management`를 `_known_nodes`에 추가. `_extract_node_progress`(`:355`)에 `deep_agent` 분기
추가(도구 호출 횟수·재개 횟수·미완 todo 수 — `run_deep_agent`가 이미 계산하는 값, `deep_agent.py:262~`).
가장 싸고 효과가 큰 한 줄이며 D-039 화이트리스트 결정의 연장이다.

**② 도구 이벤트 승격** — `astream_events(version="v2")`는 중첩 러너블의 `on_tool_start`/`on_tool_end`도
내보낸다(부모 콜백은 contextvar로 자식 `ainvoke`에 전파 — `deep_agent.py:227`이 `config`에 콜백을
명시하지 않아도 된다는 전제. **이 전제는 T0 스파이크로 실측한다**, §5). 이벤트 루프(`:1213~`)에
`on_tool_start`/`on_tool_end` 분기를 추가해 `progress{kind:"tool"}`로 변환. 도구명은
`_TOOL_NAMES` 값(`query_infra_db`·`query_live_processes`·`query_alarm`·`manage_cache`·
`register_synonym`·`general_answer`·`inspect_host`)과 deepagents 0.6.10 내장(`write_todos`·`task`·
파일 도구)이 온다.

**③ 핸들러 내부 마일스톤** — `langchain_core.callbacks.adispatch_custom_event(name, data)`를 긴 구간
앞뒤에 심는다. `astream_events`에서 `on_custom_event`로 올라온다(부모 run 존재 시 — ②와 같은 전제).
대상은 **실측된 무이벤트 구간만**:

| 구간 | 위치 | 이벤트 |
|---|---|---|
| schema_analyzer 라이브 샘플 수집(D-154 타임박스 구간) | `src/nodes/schema_analyzer*.py` 수집 루프 | `schema.sample` start/end, `label="샘플 수집 k/n"` |
| SQL 생성·검증·실행(서브에이전트 핸들러 내부에서 함수로 호출될 때) | `src/orchestration/subagents.py` data_query/alarm_query 핸들러 | `pipeline.<stage>` start/end |
| D-093 재개 시도 | `deep_agent.py:245-259` 루프 | `agent.resume` (`label="재개 k/3"`) |
| 최종 합성 시작 | `deep_agent.py` `_aggregate_with_fabrix` 직전 | `agent.aggregate` start |

`get_stream_writer()`(langgraph 1.2.11에서 가용 실측)도 대안이지만, 중첩 그래프의 writer가 바깥
`astream_events`로 승격되는지는 별도 실측이 필요하다. `adispatch_custom_event`는 `astream_events`
전용으로 설계된 API라 **이쪽을 정본으로 하고** writer는 T0에서 비교만 한다.

**④ 하트비트 + 유휴 예산 보존** — 핵심 기술 리스크가 여기 있다.

현재 루프는 `asyncio.wait_for(_event_iter.__anext__(), timeout)`(`:1197-1200`)이다. 하트비트를 위해
짧은 타임아웃(예 5s)으로 바꾸고 **타임아웃 후 같은 `__anext__`를 다시 호출하면 안 된다** —
`wait_for`는 내부 태스크를 취소하고, 실행 중인 비동기 제너레이터의 `__anext__`가 취소되면
제너레이터가 깨진다(`anext(): asynchronous generator is already running`). 지금 코드는 타임아웃 시
즉시 `return`하기 때문에 이 문제가 드러나지 않았다.

따라서 구조를 **생산자 태스크 + `asyncio.Queue`**로 바꾼다:

```
producer task:  async for ev in graph.astream_events(...): await q.put(ev)  → 끝나면 sentinel
SSE generator:  loop:
                  try: ev = await asyncio.wait_for(q.get(), timeout=heartbeat_sec)
                  except TimeoutError:
                      if now - last_activity >= effective_timeout: yield error; cancel producer; return
                      yield heartbeat{elapsed_ms, last_activity_ms}; continue
                  ... 기존 분기 그대로 ...
```

- `effective_timeout`(240s/300s)의 **"무이벤트 연속 시간" 의미는 그대로** 보존된다(D-066 후속 불변).
- 클라이언트 중단(연결 끊김) 시 생산자 태스크 취소를 `finally`에 둔다 — 현재 `return`으로 끝내던
  취소 전파 경로와 동등해야 한다(`stopStreaming` §11 동작 회귀 테스트).
- 하트비트에 `last_activity_ms`를 실어 클라이언트가 "마지막 신호 N초 전"을 계산한다.

**설정**: `ServerConfig`(`src/config.py:437~`, prefix `API_`)에 `sse_heartbeat_interval_sec: int`와
`sse_progress_events: bool` 추가. 기본값은 **G-1**(§4).

### 3.3 프론트 — 커서 아래 상태 영역

`createStreamingMessage()`(`app.js:984`)의 말풍선에 슬롯 하나를 추가한다:

```
.message-bubble
  #streamingText        (기존)
  #streamingCursor      (기존)
  #streamingStatus      (신설)  ← 커서 바로 아래
     .stream-status-line    [spinner] [현재 활동 문구]            [경과 12.4s]
     .stream-status-steps   (접이식) 완료 단계 칩 — .processing-stages 재사용
  #streamingMeta / #streamingSql (기존)
```

**동작 규칙(상태 기계)**:

| 상태 | 진입 조건 | 표시 |
|---|---|---|
| `waiting` | 말풍선 생성 | "요청 분석 중…" + 경과 |
| `active` | `node_start`/`progress(start)` 수신 | 라벨 문구(예 "인프라 DB 조회 중… (2번째)") + 경과 |
| `streaming` | 첫 `token` 수신 | 문구 "응답 작성 중…"으로 고정, 칩은 유지 |
| `stalled` | 마지막 이벤트(하트비트 포함) 후 `stall_sec` 경과 | 문구 앞에 경고색 "서버 신호 대기 중 · 마지막 신호 N초 전". 하트비트가 오면 `active`로 복귀 |
| `done` | `done` 수신 | 상태 영역 처리 = **G-2** |
| `interrupted`·`error` | 기존 경로 | `markStreamInterrupted`/`showError` 그대로 + 상태 영역 제거 |

- 경과 시간은 **클라이언트 타이머**(1s `setInterval`)로 그린다 — 서버 신호와 무관하게 "시간이
  흐른다"는 최소 피드백을 보장한다(폴백 `ainvoke` 경로에서도 동작). 타이머는 말풍선 종료 시 반드시
  해제(`finally`).
- ③겹 수정: `removeProcessingMessage()`(`:1120`)는 유지하되, 단계 칩 상태를 **새 말풍선의
  `.stream-status-steps`로 이관**한다. `updateProcessingStage()`가 `#streamingStatus` 안의 칩을
  대상으로 하도록 셀렉터 범위를 넓힌다(기존 `#processingMessage` 안 칩도 계속 지원 — 폴백 경로).
- 라벨 사전 신설 `toolLabels`(클라이언트, `nodeLabels` 옆): 위 도구 7종 + `write_todos`("작업 계획
  수립") · `task`("하위 작업 위임") · 파일 도구("작업 메모"). 미지 이름 → "도구 실행: {name}".
- `nodeToStage`(`:841`)에 `deep_agent`·`agent_orchestrator`·`intent_planner` → 신규 단계 `agent`("에이전트
  실행")를 추가한다. 단계 칩 5개는 3·4단 전용 어휘였다 — 1·2단에서는 `parse → agent → result`로
  보이는 것이 정직하다.
- 오른쪽 패널: `progress{kind:"tool"}`를 `deep_agent` 스텝의 **하위 행**으로 붙인다(`handleNodeStart`
  구조 재사용, `pipeline-step-body` 안 목록). 이건 같은 이벤트를 두 번째 소비자에 배선하는 것이라
  비용이 작다 — 포함 여부는 **G-4**.

**접근성**: 상태줄은 `role="status" aria-live="polite"`. `stalled` 전환 문구만 읽히도록 활동 문구
갱신은 `aria-atomic` 최소화(폭주 방지). 스피너는 `prefers-reduced-motion`에서 정지.

### 3.4 복합 질의 단계 표시 — `plans/88` 연계

**목표**: 순차 복합 질의가 도는 동안 커서 아래에 다음이 보인다. 완료 후에는 88의 R-5 블록이 같은 내용을
본문에 남긴다.

```
[●] 2/2 단계 · 선별 7대의 최근 1개월 CPU 사용률 조회 중…                         18.2s
    ✓ 1/2  CPU 사용률이 높은 서버 조회 → 7대 선별 (hostname)
    ▸ 2/2  대상 7대 · 진행 중
```

건너뜀·절단·미적용은 그 자리에서 보인다(88 R-1·R-7·R-2 — 사후 블록만이 아니라 실행 중에도):

```
    ✓ 1/2  CPU 사용률이 높은 서버 조회 → 0대
    ⊘ 2/2  건너뜀 — 1단계 조건에 해당하는 서버가 0대라 실행하지 않았습니다
```

**신호 원천(양 경로 대칭 — 88 §4.0 ③과 같은 합류점)**:

| 경로 | 시작 | 종료 | 위치 |
|---|---|---|---|
| 2단 | 레벨 루프가 `status="in_progress"` 전이하는 곳 | `_normalize` 직후 `status` 확정 시 | `agent_orchestrator.py:76-86` — `adispatch_custom_event("task", {...})` 2회. 레벨 내 `gather` 병렬 task는 각각 start를 먼저 내고 end는 완료 순 |
| 1단 | `_run_subagent_tool` task 조립 직후(`deepagents_tools.py:266`) | `collector.append` 직전(`:280`) | 같은 함수 호출. `order`는 이미 있고 `total`은 1단에서 **알 수 없다**(LLM 루프가 결정) → `total: null`이면 UI는 "n번째 조회"로 표기 |
| 88 W1 verdict | `assess_prior_dependency` 결과 | — | 88 W1이 랜딩하면 `reason/scope_size/truncated`를 같은 이벤트에 싣는다. **랜딩 전에는 필드가 비어 있을 뿐 이벤트는 동일** — 89는 88에 착수 순서로 종속되지 않는다 |

이벤트 생성은 `src/orchestration/` 안의 공통 헬퍼 1개(`emit_task_progress(task, phase, verdict=None,
result=None)`)로 두고 1단·2단 호출부가 같은 함수를 부른다 — 88의 `test_all_three_paths_use_the_common_module`
방식으로 대칭을 테스트에 고정한다.

**일치 규칙(불변식)**: 실행 중 표시(89)와 완료 후 경과 블록(88 R-5)은 **같은 값 객체**(`dependency_verdicts`
+ task_results 행수)에서 나온다. 문구 사전은 클라이언트(89)와 `result_aggregator`(88)에 각각 있되 **사유 코드는
서버 코드 문자열 하나**(`prior_empty` 등)로 통일한다 — 88이 한국어 문장을 먼저 정하면 89는 그 문장을 그대로 쓴다.

**프론트 변경**:

- `#streamingStatus` 안에 `.stream-status-tasks` 목록(§3.3 슬롯 하위) — `kind:"task"` 이벤트로만 채운다.
  단일 DB 경로(3·4단)에서는 이벤트가 없으므로 목록이 생기지 않는다(비복합 UI 불변).
- 상태줄 문구 우선순위: `task(in_progress)` > `tool` > `node`. 단계가 진행 중이면 "k/N 단계 · {sub_query 요약}"이
  도구명보다 앞선다(사용자 어휘에 가깝다).
- `renderTaskList`(패널)에 `skipped` 배지("건너뜀") + `reason` 문구 + `truncated_count`("N대 절단") 추가 —
  88 R-A의 UI 소비처를 이 계획이 맡는다. `agentLabels`에 `process_query`("프로세스 조회") · `host_inspect`("호스트
  점검") · `fault_diagnosis`("장애 진단") 보강.
- 완료 시 목록 처리는 **G-5**. 권고는 접기(한 줄 요약 "2단계 · 7대 → 6대")다 — 본문의 R-5 블록이 정본이라
  펼친 채 두면 같은 내용이 두 번 보인다.
- `plans/90` 스코프 칩과 같은 화면에 붙는다. 칩·배지 색은 `.step-data-badge--{info,success,warning,error}`
  기존 4종만 쓰고 신규 색을 만들지 않는다.

### 3.5 비용·성능

- 추가 LLM 호출 **0**. 하트비트는 5s당 ~60바이트, 도구 이벤트는 호출당 2건.
- 프론트 렌더는 기존 rAF 코얼레싱(`scheduleStreamingRender`)과 무관한 별도 DOM(텍스트 노드 교체)
  이라 토큰 렌더에 간섭하지 않는다.

---

## 4. 사용자 확정 게이트 — 답에 따라 구현이 달라지는 지점 5곳

| 게이트 | 질문 | 권고 | 근거 |
|---|---|---|---|
| **G-1** | 신규 이벤트·하트비트를 **기본 on**으로 둘 것인가? (`plans/80` §5.4-③ 원칙은 기본 off) | **on**(예외 등재) | 구 클라이언트가 미지 타입을 무시함을 실측(§1.3). off면 이 계획이 푸는 증상이 운영에서 그대로 남는다. 예외 근거는 config 주석에 남긴다(`COMPOSITE_*` 선례) |
| **G-2** | 완료 후 상태 영역을 (a) 제거 / (b) "N단계 · 23.1s" 한 줄로 접어 유지 | **(b)** | 오른쪽 패널이 숨는 화면(≤1024px)에서는 이것이 유일한 사후 근거. 기존 `TIME` 메타와 중복되므로 칩 목록은 접고 한 줄만 |
| **G-3** | 하트비트 주기 / 정지 판정 임계 | **5s / 15s**(하트비트 3회 결손) | D-154의 타임박스가 8s라 5s 하트비트면 샘플 수집 1건 안에 최소 1회 신호. 임계는 오탐(정상 지연을 '대기 중'으로 표시)과 늦은 경고 사이 — 15s는 SLO 단순 질의 상한보다 크다 |
| **G-4** | 오른쪽 패널에 도구 하위 행을 붙일 것인가 | **붙인다(T7)** | 이벤트는 이미 오고, 패널의 `deep_agent` 스텝이 비어 있으면 "처리 현황"이 여전히 반쪽이다. 다만 채팅창 상태줄이 우선이라 마지막 태스크로 |
| **G-5** | 복합 질의 단계 목록을 완료 후 (a) 접어 한 줄 요약 / (b) 펼친 채 유지 / (c) 제거 | **(a)** | 88 R-5 블록이 본문에 같은 내용을 남긴다(정본). (b)는 중복, (c)는 R-5가 꺼져 있거나(88 미착수) 1단에서 블록이 없을 때 근거가 사라진다 |

게이트 답이 오기 전에도 T0·T1·T2는 답에 의존하지 않으므로 먼저 착수할 수 있다.

---

## 5. 작업 분해

각 항목은 **검증 기준이 통과해야 완료**다. 신규 테스트는 실 LLM 호출 0(D-127). 위치는 실측한 기존
디렉토리만 쓴다(`tests/test_api/` · `tests/e2e/`).

| # | 작업 | 파일 | 검증 |
|---|---|---|---|
| **T0** | **스파이크 — 중첩 이벤트 전파 실측**: 노드 안에서 `ainvoke`한 내부 `CompiledGraph`의 `on_tool_start`·`on_custom_event`가 바깥 `astream_events(v2)`에 나타나는지. FakeMessagesListChatModel(tool_calls 포함) + 더미 도구로 구성 | `tests/test_api/test_stream_nested_events.py`(신규) | 두 이벤트가 관측되면 §3.2-②③ 진행. **안 되면** `deep_agent.py:227`의 `config`에 부모 콜백을 명시 전달하는 경로로 대체하고 계획서에 기록 |
| **T1** | 화이트리스트 보정 + `deep_agent` 진행 데이터 | `query.py:1219-1231`·`:1847-1859`·`_extract_node_progress` | MockGraph(`tests/e2e/conftest.py:102` 패턴)로 `deep_agent` `node_start`/`node_complete`가 **두 스트림 라우트 모두**에서 나오는지 |
| **T2** | 생산자 태스크 + 큐 + 하트비트, 유휴 예산 보존, 설정 2개 | `query.py` 두 제너레이터(공통 헬퍼로 추출해 복사본 비대칭 해소) · `config.py` `ServerConfig` | ① 느린 MockGraph(이벤트 간 12s)에서 하트비트 ≥2건 ② 무이벤트 `effective_timeout` 초과 시 기존 `error` 문구 그대로 ③ 클라이언트 연결 종료 시 생산자 태스크 취소됨 ④ `sse_progress_events=false`면 이벤트 바이트 열이 현행과 동일 |
| **T3** | `on_tool_start/end` → `progress` 변환 | `query.py` 이벤트 루프 | MockGraph가 `on_tool_start` 이벤트를 내면 `progress{kind:"tool", name, phase}`가 나온다 |
| **T4** | 핸들러 마일스톤 `adispatch_custom_event` 4곳 | `schema_analyzer` 수집 루프 · `subagents.py` 핸들러 · `deep_agent.py` 재개/합성 | 각 구간 단위 테스트에서 이벤트 디스패치 호출 확인(모의 콜백). `arch_check --ci` 0(`langchain_core` 의존은 nodes/orchestration에 기존재) |
| **T5** | 프론트 상태 영역 — 마크업·상태 기계·경과 타이머·`toolLabels`·`agent` 단계·`stalled` | `app.js`(`createStreamingMessage`·SSE 루프·`updateProcessingStage`·`finalizeStreamingMessage`·`markStreamInterrupted`) · `style.css` · `index.html`(없음 — 말풍선은 JS 생성) | 정적 계약 테스트(`tests/test_api/test_ui_scope_select.py` 선례): `#streamingStatus` 생성, `progress`/`heartbeat` 분기 존재, 타이머 해제가 `finally`에 있음, `role="status"` 존재 |
| **T6** | e2e 갱신 — P-01~P-10 중 "처리 인디케이터가 스트림 시작 시 사라진다"를 전제한 단언 수정 + 신규 P-11(상태줄)·P-12(stalled) | `tests/e2e/test_progress_display.py` · `conftest.py` MockGraph에 지연·도구 이벤트 옵션 | playwright는 `RUN_E2E=1` 옵트인 — **실행은 사용자 승인 후**(D-127). 코드 작성까지가 이 태스크 |
| **T7** | (G-4) 오른쪽 패널 도구 하위 행 | `app.js` `handleNodeStart`/`renderNodeData` | 정적 계약 테스트 1건 |
| **T9** | **복합 질의 단계 표시(§3.4)** — `emit_task_progress` 공통 헬퍼 + 2단 레벨 루프·1단 도구 러너 호출 + SSE `kind:"task"` 변환 + 상태줄 단계 목록 + `renderTaskList` `skipped`/절단 + `agentLabels` 보강 | `src/orchestration/agent_orchestrator.py` · `deepagents_tools.py` · 신규 헬퍼(`orchestration/`) · `query.py` · `app.js` | ① 2단 mock 실행에서 task마다 start/end 2건, 레벨 순서 보존 ② 1단 `_run_subagent_tool` mock에서 동일 헬퍼 호출(대칭 고정 테스트) ③ verdict 없이도 이벤트 shape 동일(88 미착수 호환) ④ 정적 계약: `skipped` 배지·`reason` 렌더·`stream-status-tasks` 존재 ⑤ 단일 DB 경로에서 목록 DOM 미생성 |
| **T8** | 문서 — D-204 본문 등재(결정·근거·대안), `plans/INDEX.md` 상태 갱신, `docs/18`(T0 결과가 전제와 달랐다면), `docs/16`류 SSE 계약 문서에 이벤트 2종 추가 | `docs/02_decision.md` 외 | grep으로 D-204이 세 표에 모두 있는지 |

순서: T0 → T1 → T2 → T3 → T5(여기서 사용자가 체감 확인 가능) → **T9** → T4 → T6 → T7 → T8.
T9를 T4 앞에 두는 이유: 복합 질의는 침묵이 가장 긴 형태이고, T9의 헬퍼가 88 W1·W3과 같은 호출 지점을
쓰므로 **88 착수 전에 이벤트 자리를 먼저 만들어 두면** 88은 verdict 필드만 채우면 된다(충돌 표면 최소).
T4는 T5 뒤로 미룬 이유: ①②④만으로 1단 경로의 침묵이 "도구 단위"까지 깨지고, 핸들러 내부
마일스톤은 그 위의 세분화라 체감 확인 뒤 필요한 곳만 심는 것이 과잉 계측을 막는다.

---

## 6. 검증 계획

| 단계 | 명령 | 통과 기준 |
|---|---|---|
| 단위·계약 | `pytest tests/test_api -q` | 신규 실패 0 (기준선은 `git worktree add` 격리 사본에서 채취 — Known Mistakes) |
| 아키텍처 | `python scripts/arch_check.py --ci` · `python scripts/overfit_check.py --ci` | 0 |
| 린트 | `ruff check src/ tests/` | 0 |
| 체감 | 서버 기동 후 브라우저 — **실 LLM 경로는 과금**. 대신 `tests/e2e/conftest.py` MockGraph를 지연 모드로 띄워 `RUN_E2E=1` 없이 정적 자산만 확인하거나, 사용자 승인 후 1회 실 질의 | 상태줄이 도구 단위로 바뀌고, 네트워크 탭에서 `heartbeat`가 5s 간격으로 보이며, 서버 프로세스를 SIGSTOP하면 15s 뒤 "신호 대기 중"으로 전환 |

---

## 7. 리스크와 범위 밖

| 리스크 | 완화 |
|---|---|
| **중첩 이벤트 전파 전제 불성립**(T0) | 대체 경로 명시(부모 콜백 전달). 계획 전체가 이 한 점에 걸려 있어 **T0를 최우선**으로 둔다 |
| 큐 전환으로 취소 전파(Stop 버튼 → 서버 취소, §11)가 끊김 | T2 ③ 회귀 테스트 + `finally`에서 생산자 취소 |
| `on_tool_start` 폭주(도구 루프가 길 때) | 상태줄은 마지막 이벤트만 그린다. 칩은 도구명 단위로 dedupe 후 횟수 표기 |
| 도구명이 deepagents 버전 업에서 바뀜 | 미지 이름 폴백 "도구 실행: {name}"으로 침묵 없이 강등 |
| 두 스트림 라우트 복사본 비대칭 | T2에서 공통 헬퍼로 추출 — 이 계획이 복사본을 세 번째로 갈라놓지 않게 한다 |
| 2단(`agent_orchestrator`) 내부는 도구가 아니라 함수 호출 | T4의 `subagents.py` 핸들러 마일스톤이 1·2단 공용이라 함께 덮인다 |
| **88과의 동시 편집** — `agent_orchestrator.py:76-92`·`deepagents_tools.py:255-280`은 88 W1·W3의 호출 지점과 같다 | T9 헬퍼는 **호출 2줄만** 끼워 넣고 판정 로직은 넣지 않는다. 착수 직전 `git status`·88 진행 상태 재확인(메모리 `concurrent-worktree-edits`). 먼저 랜딩한 쪽이 이벤트 자리를 만들고 뒤쪽은 필드만 채운다 |
| 88 `status="skipped"` 도입이 미뤄지면(R-A) `reason`만 오고 상태는 `failed` | UI는 `reason`이 있으면 상태값과 무관하게 "건너뜀" 문구를 우선한다 |
| 1단 `total` 미상 | "n번째 조회"로 표기하고 완료 시 최종 개수로 치환 — 진행률 막대는 만들지 않는다 |

범위 밖(별건):

- LLM 사고 과정·중간 답변 텍스트 노출(§2).
- 세션 새로고침 뒤 진행 상태 복구(현재 아키텍처는 SSE 1회성 — 체크포인터 재접속 설계 필요).
- 소요시간 분포 계측(감사 로그에 `processing_time_ms` 부재, §1.1) — 상태줄과 무관하게 `plans/76`
  실행 로깅 축의 잔여 항목으로 넘긴다.

---

## 8. 산출물·기록

- 코드: §5 파일 목록. 신규 테스트 예상 16~24건(T9 포함).
- 문서: D-204 본문(`docs/02_decision.md`) · `plans/INDEX.md` 89행 상태 · SSE 이벤트 계약 문서.
- 잔여(구현 후): 실 브라우저 체감 확인 1회(과금 승인) · `RUN_E2E=1` playwright 실행(승인) ·
  소요시간 분포 계측 착수 여부.

---

## 개정 이력

- v1(2026-09-09) — 최초 작성. 원인 실측 3겹+계약 부재 · 설계 4절 · 게이트 4건 · T0~T8 · D-204 예약.
- v2(2026-09-09) — **`plans/88` 연계**: §1.4(복합 경로 실측 — 2단 작업 목록이 사후 1회 · `skipped` 오표기 예정 ·
  R-5 사후 블록과 원천 공유 필요) · §3.1 `kind:"task"` 페이로드(88 `DependencyVerdict` 필드명 동일) ·
  §3.4(실행 중 단계 목록 · 양 경로 공통 헬퍼 · 일치 불변식 · 패널 `skipped`/절단 · `agentLabels` 보강) ·
  G-5 · T9 · 리스크 4건. 88과 호출 지점이 겹치므로 착수 순서·충돌 표면을 명시.
- v3(2026-09-09) — **구현 랜딩(-WIP)**. ★T0 실측: 중첩 `CompiledGraph`의 `on_tool_start`·`on_custom_event`가 바깥
  `astream_events(v2)`로 전파된다(대체 경로 불필요). ★`plans/88` W1이 같은 날 먼저 랜딩(`_gate_level`·`skip_result`·
  `status="skipped"`)해 T9는 verdict 필드를 **실제 값**으로 싣는다. ★상류 fast-forward(`b45ac9e`)가 같은 문제를
  `_next_event_or_timeout`(D-203 상류: 검출·취소 분리)로 고쳐 `query.py` 두 루프가 충돌 → 큐 기반 구현을 취하고
  F2 취지는 `finally`의 유한 대기(`_PRODUCER_CANCEL_GRACE_SEC`=1s)로 반영. 상류 헬퍼는 `test_stream_guard.py`가
  임포트해 정의만 유지. ★상류가 D-203~D-202를 선점해 본 계획의 D-204는 **D-204로 재부여**(병렬 세션 일괄 치환).
  잔여: T4 · T8 · 실 브라우저 확인.
