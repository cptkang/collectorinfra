# 88. 복합 질의 순차 의존 처리 — 선행 조회 결과가 후속 조회의 대상이 되는 파이프라인

> **작성일**: 2026-09-09
> **성격**: 구현 계획 · **상태: 1차·2차 구현 + §8 게이트 전건 사용자 확정 + 실 검증(Gemini 11건 · 2단·1단 계약 실효 확인) + 운영 `.env` 반영 완료(2026-09-10 · §11) — 잔여 = §11.4 단계 4·5(2차 3종 on 판단)·7(W4 골든 실 평가) · R-E 합집합 폭(§11.6 부수 관측) · `-WIP`**
> **v4(2026-09-09)**: 사용자 지시("중단된 작업을 재개하라")로 2차(W2·W8·W9·W4)를 §8 권고안 가정 아래 구현. **실측 정정**: 플래너
> 프롬프트에 data→data 순차 예시(예시 3 *"찾아 그 서버들의 프로세스"*)가 **이미 있다** — v1 R-2의 "예시 0건"은 과장이었고 **프롬프트는
> 무변경**(G-4 불필요 · 골든 불변). 2차도 플래그 3종 기본 off(`COMPOSITE_PLAN_DAG_VALIDATION_ENABLED` · `COMPOSITE_SEQUENTIAL_REPLAN_ENABLED` ·
> `COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED`).
> **v2(2026-09-09)**: 사용자 지시로 v1의 「경계」 4건을 **범위로 편입**(§2 · E-1~E-4 · W6~W9) ·
> **v3(2026-09-09)**: 1차 4모듈(W1·W6·W3·W7 = `prior-dependency-gate`·`dependency-notes`·`scope-postcheck`·`prior-scope-by-db`)
> SDD·TDD 구현 — `CAPABILITY-MAP-88.md` · `SPEC-*.md` 4건 · `tasks/plan-88.md`·`tasks/todo-88.md`. 신규 테스트 78건 ·
> 관련 스위트 1,850+ passed · arch/overfit 0 · 플래그 3종 기본 off. 가정: G-1=(a) 미실행+사유 · G-2=표기만(§8 권고안).
> **요청 취지(사용자 지시 원문)**: *"복합질의에서 순차적으로 진행되어야 되는 질의가 있을 경우, 예를 들어
> 'CPU 사용률이 높은 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률을 보여줘' 라고 했을 때 첫 번째
> 'CPU 사용률이 높은 서버를 조회'하고 찾은 서버 정보를 이용하여 해당 서버의 최근 1개월 CPU 사용률을
> 조회하는 식으로 순차적으로 진행되어야 한다. 이런 순차적인 처리에 대한 구현 계획을 파일로 정리하라."*
> **관련 결정**: D-086(선행 task 결과 스코프 결정적 주입 — 2단 경로) · D-095(deepagents 경로 대칭 주입 —
> 1단 경로) · D-099(선행 스코프의 결정적 조립 편입 — HAVING) · D-066(단일/멀티 경로 동등화) ·
> D-100(서버 식별 컬럼 한정) · D-035(결정적=판단 · LLM=보조) · D-161(경로 폐기 4항 실측) ·
> D-176(실행 그룹 — **존 축**, 본 계획과 축이 다르다 §2.4) · D-127(과금 API 건별 승인)
> **신규 결정 예약**: **D-203**(복합 질의 순차 의존 계약 — 선행 결과 게이트 · 분해 보강 · 사후 대조 ·
> 경과 노출) — `docs/02_decision.md` 「채번 이력」 표 등재 완료(2026-09-09 · 예약은 그 표에 있어야 효력 — D-161).
> **번호 주의**: `plans/87`(제니퍼 APM 연동 · D-195)과 `plans/89`(스트리밍 진행 상태 · D-204)가 같은 날 작업
> 트리에 함께 들어와 본 계획은 **88**을 쓴다. D-196은 `plans/54` 등재 완료 — 재사용 금지. 파일명 `-TODO`
> 접미사는 INDEX 규칙(코드 0건).
> **실측 기준**: 브랜치 `multiintent` HEAD `9945284` + 사용자 미커밋 작업(스테이징 30+ 파일 — 본 계획이 여는
> `src/` 파일은 전부 clean) · `.env` 운영값 · `deepagents 0.6.10` 루트 venv 설치 확인.

---

## 0. 결론 먼저

**순차 배관은 이미 있다. 없는 것은 "끊겼을 때의 계약"이다.**

예시 질의를 지금 그대로 넣으면 다음 순서로 흐른다(§3 추적).

```
[2단 intent_orchestration]
  intent_planner ──LLM 분해──▶ t1 data_query "CPU 사용률이 높은 서버 조회"      depends_on=[]   input_from=[]
                               t2 data_query "선행 결과 서버들의 최근 1개월 CPU"  depends_on=[t1] input_from=[t1]
  agent_orchestrator ──topological_levels──▶ 레벨 1: t1 실행 → 레벨 2: t2 실행
  _make_isolated_input(t2) ──▶ prior_rows[t1] = t1 결과의 식별 컬럼 행(≤100)
  query_generator ──▶ server_scope=("hostname"|"name", [값…]) ──▶ 조립기 HAVING … IN (…)   (결정적, D-099)
                      + LLM 폴백 프롬프트 블록 "선행 작업 결과 서버 스코프 (필수 준수)"      (D-086)

[1단 deep_agent(정본)]
  오케스트레이터 LLM이 query_infra_db 도구를 두 번 호출 (순서는 LLM 루프가 결정)
  두 번째 호출의 sub_query에서 _dependency_scope 결정적 게이트(G1 값 일치·G2 참조어·G3 순위어)
  ──▶ 위와 **같은 합류점** _make_isolated_input 통과 (D-095)
```

즉 "찾은 서버 정보를 이용해 후속 조회"는 **코드가 결정적으로 스코프를 강제**하는 형태로 이미 구현돼 있다.
그런데 예시 질의를 끝까지 추적하면 **계약이 비어 있는 지점 7곳**이 드러난다(§1). 그중 R-1은 침묵
오류(사용자가 틀린 답을 맞는 답으로 받는다)라 최우선이다.

| # | 결손 | 어디서 | 현재 동작 | 위험 |
|---|---|---|---|---|
| **R-1** ★ | **선행 0건/실패 시 후속이 스코프 없이 실행된다** | 양 경로 공통 — `build_prior_rows_block`이 값 없으면 `""` 반환(`query_gen_common.py:1078-1081`), `_run_agent`는 선행 상태를 보지 않음(`agent_orchestrator.py:214-245`), 1단은 후보 행 없으면 미주입(`deepagents_tools.py:186-190`) | t1이 0건이면 t2가 **전체 서버의 1개월 CPU**를 돌려준다 | **침묵 오류** — "높은 서버가 없다"가 "모든 서버 목록"으로 둔갑 |
| **R-2** | 분해가 LLM에만 의존하고 실패는 침묵 폴백 | `intent_planner.py:713-737` 단일 `data_query` 폴백(로그만) · 프롬프트 예시 3-1은 **alarm→data**뿐(`prompts/intent_planner.py:150-165`), data→data("찾아 … 그 서버들") 예시 0건 · 1단은 순차 자체를 LLM 루프가 결정(`deep_agent.py:216`) | 단일 task로 뭉개지면 순차 없이 SQL 한 번(LLM이 서브쿼리를 쓰거나 못 쓰거나) | 사용자는 순차 처리가 안 됐음을 모른다 |
| **R-3** | 후속 결과의 사후 대조 부재 | 충족도 검증(`check_sufficiency`)은 `prior_targets` 소비자(process_query·fault_diagnosis)에만 발동(`agent_orchestrator.py:97-113` — `injected` 비면 건너뜀) | data_query 후속이 목록 밖 서버를 포함하거나 목록 안 서버가 누락돼도 판정 없음 | LLM 폴백 경로에서 스코프 이탈이 통과 |
| **R-4** | 멀티 DB 경로 비대칭 | `path_parity` 기본 `False`(`config.py:345`) → prior_block 있으면 결정적 컴파일 **통째 우회**(`multi_db_executor.py:1265-1272`), 단일 경로는 무조건 전달(`query_generator.py:660`) | 운영은 `ACTIVE_DB_IDS=polestar`(단일)라 미노출 | 존 편입 시 후속 조회가 LLM 프롬프트에만 의존 |
| **R-5** | 응답에 단계 경과·스코프 근거가 없다 | `result_aggregator.py`는 task별 본문 나열 + supersedes 숨김(D-043)만 — "N대 선별 → 그 N대의 …" 관계를 렌더하지 않음 | 두 결과가 나란히 나올 뿐 | 사용자가 "어떤 서버들에 대한 결과인가"를 재구성해야 함 |
| **R-6** | 평가 자산 0건 | `testdata/text2sql_gold/*.yaml`·`routing_gold`에 data→data 순차 케이스 없음(grep "찾아\|그 서버\|해당 서버" — 유사 사양 검색 1건뿐) | 회귀 판정 불가 | 프롬프트 변경의 효과·부작용 측정 불가 |
| **R-7** | 상한 절단이 침묵 | `_MAX_PRIOR_ROWS=100`(`subagents.py:66`) · `_MAX_PRIOR_SCOPE_VALUES=100`(`query_gen_common.py:988`) — `rows[:100]` 절단 사실을 어디에도 남기지 않음(`prior_targets`의 `TargetResolution.truncated`와 달리) | "높은"을 낮은 임계로 해석해 100대 초과면 101대째부터 조용히 빠짐 | 부분 결과가 전체로 보임 |

**설계 방향(§4)**: 새 실행 엔진을 만들지 않는다. 기존 합류점(`_make_isolated_input`) **앞뒤**에 결정적
계약 3개를 붙인다 — ①선행 결과 게이트(실행 전) ②사후 대조(실행 후) ③경과 렌더(응답). 분해는 LLM에
두되 표지 감지로 **재분해 1회 + 미적용 사유 노출**로 침묵 폴백을 없앤다. 전부 플래그 기본 off.

**v2 확대(§2)**: v1이 밖에 두었던 4건을 편입하면서 실측 3건이 더 드러났다 — **E-2** 78 W5-3의 사유 채널
`sufficiency_shortfalls`가 **쓰기만 있고 소비처 0건**(응답에 닿지 않는다) · **E-4** `prior_rows`가 행의
`_source_db`를 버려 **멀티 DB 후속 조회가 DB별로 분할되지 않는다** · **E-3** 운영 backend `none`의 분해
경로는 **DAG 검증이 없어 순환·미존재 참조가 조용히 병렬로 바뀐다**. 3·4단 경로(**E-1**)는 2단 파이프라인
함수를 노드로 재사용해 2-pass를 넣되, HITL 승인 게이트 우회 문제 때문에 조건부다.

---

## 1. 실측 — 현행 배관 (있는 것)

### 1.1 타입 계약과 생성

| 항목 | 위치 | 내용 |
|---|---|---|
| `TaskSpec` | `src/orchestration/schemas.py:27-47` | `depends_on`(실행 순서) · `input_from`(데이터 의존) **분리** — docstring이 "섞으면 전달되지 않거나 불필요한 직렬화" 명시 |
| 분해 | `src/orchestration/intent_planner.py:638 _llm_decompose` | 구조화 출력(`try_structured_call`, backend 기본 `"none"`) + JSON 폴백. **실패·빈 결과 → 단일 `data_query` 폴백**(`:713·:717·:737`, 로그만) |
| 결정적 교정 | `intent_planner.py:84 _coerce_alarm_intent` · `:114 _coerce_process_intent` · `:144 _coerce_host_inspect_intent` | LLM 분류를 코드가 교정하는 선례. `_coerce_alarm_intent`는 `input_from` 있는 task를 뒤집지 않는다(`:99`, `test_prior_rows_scope::test_dependent_task_not_coerced`) |
| 프롬프트 | `src/prompts/intent_planner.py:74-81` 의존성 규칙 · `:150-165` 예시 3-1 | 3-1 = *"활성 심각 알람 서버들의 최근 1개월 CPU"* → `alarm_query → data_query`. **data→data 예시 없음** |
| 재계획 | `src/orchestration/replanner.py:279 _assign_ids` | 신규 task의 `depends_on`/`input_from`/`supersedes` 보정·임시 id 재매핑 |

### 1.2 실행과 배관(2단)

| 항목 | 위치 | 내용 |
|---|---|---|
| 레벨 실행 | `src/orchestration/agent_orchestrator.py:73-95` | `topological_levels(pending)` — Kahn, `depends_on`만 사용. 레벨 간 순차·레벨 내 `asyncio.gather` |
| 배관 본체 | `src/orchestration/subagents.py:661 _make_isolated_input` → `:785-798` | `input_from`마다 `prior.get(tid)`의 `rows/query_results/organized_data.rows`를 `_extract_identity_rows`로 추려 `base["prior_rows"]`. 주석: *"1단·2단 양쪽의 합류점"*(`:803-806`) |
| 식별 행 추림 | `subagents.py:582 _extract_identity_rows` | `is_server_identity_col`(D-100) 컬럼만, `rows[:_MAX_PRIOR_ROWS]` |
| 상태 | `src/state.py:275 prior_rows` · `:279 prior_targets` · `:272-274 task_plan/task_results/is_composite` | 전부 요청 스코프 |
| 충족도 | `agent_orchestrator.py:97-113` + `src/orchestration/sufficiency.py` | `injected`(prior_targets 주입 기록)가 비면 **검증·재시도 모두 건너뜀** — data_query 후속에는 발동하지 않는다 |

### 1.3 실행과 배관(1단 deep_agent — 정본)

| 항목 | 위치 | 내용 |
|---|---|---|
| 백엔드 선택 | `src/orchestration/deep_agent.py:74` | `enable_deepagents_package AND orchestrator_available` — 운영 `.env`: `ENABLE_DEEPAGENTS_PACKAGE=true` · `ORCHESTRATOR_PROVIDER=gemini` · 패키지 0.6.10 설치 → **기동 시 Gemini 가용하면 1단이 정본으로 확정** |
| 도구 | `src/orchestration/deepagents_tools.py:53-63` 7종 고정 · 시그니처 `sub_query: str` 하나(`:315`) | LLM은 결과를 인자로 넘기지 않는다 |
| 의존 게이트 | `deepagents_tools.py:144 _dependency_scope` | 차단어(`"전체","모든","전 서버"`) 우선 → G1 값 일치 / G2 참조어(`"해당","그 서버","앞서","선별","조회된","중에서"…`) / G3 순위어(`"가장","최고","상위","top"…`). **후보는 행이 있는 성공 결과만**(`:174-185`) |
| 합류 | `deepagents_tools.py:266-274` | `depends_on=[]` 고정, `input_from`만 채워 `_make_isolated_input` 통과 |
| 지시문 | `src/prompts/orchestrator.py:12-13` | *"선행 결과로 대상이 선별된 경우 후속 sub_query에 선별된 식별자를 그대로 명시"* → G1 값 일치로 걸리게 설계 |

### 1.4 SQL 종단(선행 스코프 → SQL)

```
state.prior_rows
  ├─ prior_server_scope(prior_rows) → ("hostname"|"name", [값…≤100])     prompt_blocks.py:681 → query_gen_common.py:1021
  │     └─ compile_from_nl(server_scope=…) → compile_smq → _compile_ab → build_semantic_pivot_sql
  │           └─ HAVING MAX(CASE WHEN c.resource_type='server' THEN c.{col} END) IN (…)   assembler.py:993-1001  (D-099)
  └─ build_prior_rows_block(prior_rows) → 프롬프트 블록(규칙 4개: IN 필수·선별 조건 재표현 금지·목록 외 금지·식별 컬럼 SELECT)
        query_gen_common.py:1063 — 단일 `query_generator.py:784-791` · 멀티 `multi_db_executor.py:326-328`
```

기간 축은 대상 축과 **완전히 분리**된다 — `build_stat_month_block(stat_month, metric_table)`은 hostname을
받지 않으며, "최근 1개월"은 `_N_MONTHS_RE`(`query_gen_common.py:29`)가 직전 완결 월 기준으로 결정적으로
해석한다.

### 1.5 플래그(운영 실제값 기준 — 코드 기본값 아님)

| 플래그 | 운영 `.env` | 기본값 | 본 계획 관련 |
|---|---|---|---|
| `ENABLE_DEEPAGENTS_PACKAGE` / `ORCHESTRATOR_PROVIDER` | `true` / `gemini` | `False` / `vllm` | 1단 정본 성립 조건 |
| `ENABLE_INTENT_ORCHESTRATION` | `true` | `None`(자동) | 2단 폴백 |
| `ACTIVE_DB_IDS` | `polestar` | — | 단일 DB → R-4 미노출 |
| `STRUCTURED_OUTPUT_BACKEND` | 미기재 → `"none"` | `"none"` | 분해 검증·되먹임 없음(79 E-3a 대기) |
| `COMPOSITE_PRIOR_TARGETS_ENABLED` | 미기재 → `False` | `False` | **data_query 후속에는 무관**(prior_rows는 플래그 없이 항상 주입) |
| `TEXT2SQL_PATH_PARITY`(`path_parity`) | 미기재 → `False` | `False` | R-4 |

### 1.6 기존 테스트(회귀 기준선)

`tests/test_orchestration/test_orchestrator.py::test_data_dependent_chaining`(t1→t2 prior_rows 주입) ·
`tests/test_orchestration/test_prior_rows_scope.py`(**docstring이 정확히 "최근 1개월 CPU 사용률" 케이스** —
블록 렌더·단일/멀티 주입·coerce 불변) · `tests/test_composite/test_prior_targets_wiring.py`(1단·2단 대칭 13건) ·
`tests/test_composite/test_sufficiency.py`(1회 재시도) · `tests/snapshots/prompt_render_sha256.json`(프롬프트 바이트 골든).

---

## 2. 확대 범위 — v1의 「경계」 4건을 편입한다 (v2 · 사용자 지시)

> v1은 아래 4건을 "다루지 않는 것"으로 두었다. 사용자 지시(2026-09-09)로 전부 범위에 넣는다. 각 항목은
> **실측 → 설계 → Wave**로 이어지며, 설계 본문은 §4.7~§4.10, 구현 단위는 W6~W9다.

### 2.1 (E-1) 3단·4단 경로의 순차 지원

**실측**
- 3단: `route_after_semantic_router`(`src/graph.py:132-150`)의 분기는 `_INTENT_ROUTE_MAP`(캐시·유사어·일반추론·
  장애진단) + 존 역질문 END + `is_multi_db` → `multi_db_executor` / 그 외 `schema_analyzer`. **복합·순차 의도 항목이
  없다**(`grep composite|복합|is_composite src/routing/semantic_router.py` 0건). 질의 하나 = SQL 하나.
- 4단: `field_mapper → schema_analyzer` 직행(`graph.py:560`).
- 재사용 자산: **`run_data_query_pipeline(task, isolated, *, llm, app_config)`**(`src/orchestration/subagents.py:974`)이
  DB 선택 → 단일/멀티 분기 → `result_organizer`까지를 **함수로** 노출한다(2단 subagent 핸들러). 1단이 2단 모듈을
  도구로 쓰는 선례가 이미 있다(`docs/21` — *"배선은 배타적이지만 모듈 의존은 배타적이지 않다"*).
- **주의 실측**: HITL 게이트 2종(`structure_approval_gate`·`approval_gate`)은 그래프의 단일 DB 경로에
  `interrupt_before`로 배선돼 있다. `run_data_query_pipeline`은 그래프 밖에서 노드 함수를 직접 호출하므로
  **이 경로로 실행하면 승인 게이트를 거치지 않는다**(2단·1단이 이미 그렇다). 3·4단에 같은 함수를 넣으면 기본
  **on**인 `enable_structure_approval`이 순차 질의에 한해 우회된다 — 설계에서 막아야 한다(§4.7).

**편입 내용**: 3·4단 빌드에만 등록되는 노드 `sequential_runner`(2-pass · §4.7). 진입 조건이 성립하지 않으면
현행 경로 바이트 동일. HITL 승인 플래그가 on이면 **진입하지 않는다**(우회 금지).

### 2.2 (E-2) `prior_targets` 소비자(process_query · fault_diagnosis)와의 대칭

**실측**
- `_build_prior_targets_for_task`(`subagents.py:830-888`): `input_from`이 없으면 완료된 선행 결과 **전체**를 훑고,
  절단(`resolution.truncated`)은 **로그만**(`:877-880`). `process_query`는 자체 문구로 절단을 노출(`process_query.py:688-691`).
- **`sufficiency_shortfalls`(78 W5-3 "미충족 사유 노출")는 `agent_orchestrator.py:117-119`가 쓰지만 `src/` 전체에
  읽는 곳이 0건이고 `AgentState`에도 선언돼 있지 않다**(`grep -rn shortfall src/` — `sufficiency.py`·`agent_orchestrator.py`
  외 0건). 즉 사유는 만들어지고 버려진다 — 침묵 폴백 금지 원칙이 배선 단절로 무력화된 상태.
- 상한이 이원화돼 있다: data_query 후속은 `_MAX_PRIOR_ROWS=100`, process_query 후속은 `composite.max_targets=10`.
  같은 선행 결과가 후속 agent에 따라 100대/10대로 잘리는데 어디에도 대조 표기가 없다.

**편입 내용**: ①§4.1 게이트를 `_build_prior_targets_for_task` 앞에 동일 적용 ②사유 채널을 **하나로 통합**
(`dependency_notes`) — 게이트·사후 대조·충족도 미달·절단이 전부 §4.4 경과 블록으로 렌더된다(죽은 채널
`sufficiency_shortfalls`를 살려 여기에 합류) ③상한 이원화를 경과 블록에 명시하고 값 통일 여부는 **G-7**.

### 2.3 (E-3) 분해 계약 검증·되먹임 — backend 무관 결정적 층

**실측**
- 79 E-3a는 **구현돼 있다**: `try_structured_call → DecomposedPlan`(`intent_planner.py:690`, `src/clients/instructor_adapter.py`
  커밋 `c7d47e8` 2026-08-27) + 실패 시 `degraded` 사유 노출(`:696-707`). 단 backend `"instructor"` 옵트인이고
  **운영은 `"none"`**(§1.5).
- `"none"` 경로(`:709-740`)는 JSON을 dict로 옮길 뿐 **검증이 없다**: task_id 중복 · `input_from`이 없는 id를 참조 ·
  `input_from ⊄ depends_on` · 순환. 그 결과는 `topological_levels`가 *"순환/누락 의존은 남은 task를 한 레벨로
  안전 처리"*(`agent_orchestrator.py:191-195`)하므로 **순차가 조용히 병렬로 바뀐다** — 예시 질의에서 t2가 t1과
  같은 레벨에 놓이면 `prior_rows`가 비어 R-1과 같은 침묵 오류로 귀결된다.
- 폴백 3곳(`:713·:717·:737`)은 `return fallback`으로 사유 없이 단일 task를 돌려준다(구조화 경로와 비대칭).
- 소유권: `intent_planner.py`는 **79 트랙 E 소유**(79 v14 (a)안). 본 계획이 이 파일을 열려면 **G-5**.

**편입 내용**: `validate_plan_dag(tasks)`(결정적 · backend 무관) + 위반 시 되먹임 1회 + 보정 가능한 위반은 자동
보정 + 불가하면 `degraded` 사유(구조화 경로와 같은 shape)로 단일 폴백(§4.8). §4.2의 "표지 있는데 단일" 재분해와
**같은 재시도 예산(합계 1회)** 을 쓴다 — 두 번 되묻지 않는다.

### 2.4 (E-4) 두 축 동시 성립 — 선행 결과의 존 분포가 후속 조회의 DB 집합을 정한다

**실측**
- `prior_targets` 경로는 행별 `_source_db`를 정본으로 쓴다(D-176 `prior-scope-wiring` · `prior_targets.py:266-272`).
- **`prior_rows` 경로는 그 태그를 버린다**: `_extract_identity_rows`가 `is_server_identity_col`로 식별 컬럼만 남기고
  (`subagents.py:606-612`), `is_server_identity_col("_source_db")`는 False(`query_gen_common.py:999-1018` — 정확 매칭·
  접두 규칙 어디에도 없음). 따라서 후속 멀티 DB 조회는 `_prepare_multi_run`이 run 단위로 1회 만든 `prior_scope`
  (`multi_db_executor.py:329`)를 **모든 대상 DB에 같은 IN 목록으로** 적용한다(`_run_single_target(target, run)` `:536`은
  db_id별이지만 scope는 공유). 선별된 서버가 없는 존까지 조회하고, b0(DB2) 값이 gp/yd에 섞여 들어간다.
- 실행 그룹(D-176 `kind=discovery→dependent`)은 **존 소재 탐색**(어디서) 축이고, 그룹 간 데이터 전달 코드는
  없다(`_run_groups` `:663-746` 합집합 병합). `execution_groups`를 채우는 배선은 82 2차 대기(`SPEC-group-runner.md`
  2항) — **죽은 경로가 아니라 대기 중인 경로**(D-161 · 폐기 제안 아님).

**편입 내용**: `prior_rows`에 `_source_db` 패스스루 → DB별 스코프 분할 `{db_id: (col, values)}` → 선별 0대인 DB는
후속 대상에서 제외 + 경과 표기(§4.9). 실행 그룹이 채워질 때(82 2차)의 계약(dependent 그룹은 앞 task의 분할
스코프를 `group_key`별로 받는다)을 **여기서 확정**하고 배선은 82 2차가 한다. 두 축 동시 성립 질의(*"CPU 높은 서버를
찾아 그 서버들이 있는 존의 …"*)는 이 분할로 자연히 풀린다(존 = `_source_db` 집합).

---

## 3. 예시 질의 추적 — 어디서 끊기는가

`"CPU 사용률이 높은 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률을 보여줘"`

### 3.1 2단 경로

| 단계 | 정상 흐름 | 끊길 수 있는 지점 | 결손 |
|---|---|---|---|
| ① 분해 | t1 `data_query`("CPU 사용률이 높은 서버") · t2 `data_query`("선행 결과 서버들의 최근 1개월 CPU", `input_from=[t1]`) | 예시가 alarm→data뿐이라 **t1·t2 모두 data_query인 분해를 모델이 학습한 적 없음**. 단일 task로 나오면 로그만 남고 순차 없이 진행 | R-2 |
| ② t1 실행 | "높은" → LLM이 임계 또는 상위 N으로 해석해 SQL. 결과 행에 `hostname`/`name` 포함 | 해석이 임계형이고 0건이면 → ③에서 스코프 소실. 100대 초과면 절단 | R-1 · R-7 |
| ③ t2 스코프 주입 | `prior_rows[t1]` → `server_scope` → HAVING IN | **t1 0건/실패 → `""` → 무스코프 실행** | **R-1** |
| ④ t2 SQL | 결정적 컴파일(cpu 사용률 · stat_month=직전 완결 월 1개월 · server_scope) — 커버리지 안이면 LLM 0회 | 커버리지 밖이면 LLM 폴백 프롬프트(블록 규칙 4개) — 규칙 위반을 잡는 검사 없음 | R-3 |
| ⑤ 집계 | t1 본문 + t2 본문 | "N대 선별 → 그 N대"의 관계 표기 없음 | R-5 |

### 3.2 1단 경로(정본)

| 단계 | 정상 흐름 | 끊길 수 있는 지점 | 결손 |
|---|---|---|---|
| ① 첫 도구 호출 | `query_infra_db("CPU 사용률이 높은 서버")` | LLM이 한 번의 호출에 전체 질의를 넣으면 순차 없음 | R-2 |
| ② 둘째 호출 | 지시문대로 식별자를 sub_query에 명시 → G1 값 일치, 또는 "그 서버들" → G2 | 첫 결과가 0건이면 `candidates` 비어 **미주입 → 전체 서버 조회** | **R-1** |
| ③~⑤ | 2단과 동일 합류점 | 동일 | R-3 · R-5 · R-7 |

**공통 결론**: 양 경로 모두 "선행이 무언가를 돌려줬을 때"는 맞게 동작하고, "선행이 아무것도 돌려주지
않았을 때"의 계약이 비어 있다. 이것이 R-1을 1순위로 두는 근거다.

---

## 4. 설계

### 4.0 원칙(불변식)

1. **새 실행 엔진 없음** — task DAG(2단)·도구 루프(1단)·합류점 `_make_isolated_input`을 그대로 쓴다.
2. **순차 계약은 코드가 보장한다** — 선행 결과 유무·범위 이탈·절단은 결정적으로 판정하고 사유를 구조화해
   응답에 노출한다(침묵 폴백 금지). LLM은 분해와 SQL 표현에만 관여한다(D-035).
3. **양 경로 대칭** — 게이트·대조·경과는 합류점 공통 모듈에 두고 1단·2단 호출부가 같은 함수를 부른다
   (`test_prior_targets_wiring::test_all_three_paths_use_the_common_module` 방식으로 고정).
4. **플래그 기본 off = 비트 동일**(plans/80 §5.4-③). 만료일과 발동률 관측을 함께 둔다(82 §5.4).
5. **프롬프트 변경은 바이트 골든 갱신을 동반**하며 79 S-1 기준선 오염을 피하기 위해 G-4 승인 뒤에만 한다.

### 4.1 선행 결과 게이트 — R-1 (실행 전 · 양 경로 공통)

신규 `src/utils/prior_dependency.py`(utils 계층 — 소비처는 orchestration이라 의존 방향 정합):

```python
class DependencyVerdict(BaseModel):
    ok: bool
    reason: Optional[str]          # "prior_failed" | "prior_empty" | "prior_no_identity" | None
    source_task_ids: list[str]
    scope_col: str                 # "hostname" | "name" | ""
    scope_size: int
    truncated: bool                # R-7 — 상한 절단 여부
    truncated_count: int

def assess_prior_dependency(task: dict, prior: dict) -> DependencyVerdict:
    """input_from 선행 결과를 결정적으로 판정한다. LLM 0회."""
```

| 판정 | 조건 | 후속 처리 |
|---|---|---|
| `prior_failed` | 선행 결과에 `error` | 후속 **미실행**, task `status="skipped"`, 사유 노출 |
| `prior_empty` | 선행 행 0건 | 후속 **미실행** — *"1단계 조건(…)에 해당하는 서버가 0대라 2단계를 실행하지 않았습니다"* (G-1 기본안) |
| `prior_no_identity` | 행은 있으나 서버 식별 컬럼 없음(`collect_prior_identity_values` → `""`) | 후속 미실행 + *"선행 결과에 서버 식별 컬럼이 없어 대상을 확정할 수 없습니다"* — LLM이 무스코프로 진행하는 것보다 안전 |
| ok + `truncated` | 값 > 상한 | 실행하되 절단 사실을 경과에 표기(R-7) |

- **호출 지점**: 2단 `agent_orchestrator._run_agent` 진입 직후(`:228` 앞) · 1단 `deepagents_tools._run_subagent_tool`의
  `_dependency_scope` 직후(`:259` 뒤). 1단에서는 미실행 사유를 **도구 반환 문자열**로 오케스트레이터 LLM에 돌려주고
  collector에도 적재해 최종 집계(§4.4)가 읽게 한다.
- **상태**: `AgentState.dependency_verdicts: Optional[dict[str, dict]]`(요청 스코프 — 라우트에서 명시 초기화).
  `task_plan[i].status`에 `"skipped"` 값 추가 — `_normalize`·`result_aggregator`의 `status` 소비처를 grep으로 전수
  확인해 `completed/failed` 이외 값이 안전한지 실측한 뒤 도입한다.
- **`prior_targets` 소비자 대칭**: `_build_prior_targets_for_task`(`subagents.py:830`)도 같은 verdict를 읽어 `prior_empty`면
  주입하지 않고 사유를 남긴다 — 78 충족도 검증과 충돌하지 않는다(주입 자체가 없으면 `injected`가 비어 검증도 없다).
- **플래그** `COMPOSITE_SEQUENTIAL_GATE_ENABLED`(기본 **off**). off면 verdict를 계산만 하고 로그로 남긴다
  (발동률 관측 — on 전환 근거).

### 4.2 분해 보강 — R-2

**(a) 프롬프트 예시 3-2 추가**(`src/prompts/intent_planner.py` 예시 3-1 직후):

```
입력: "CPU 사용률이 높은 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률을 보여줘"
t1 data_query "CPU 사용률이 높은 서버 목록 조회(식별 컬럼 포함)"           depends_on=[] input_from=[]
t2 data_query "선행 결과의 서버들에 대해 최근 1개월 CPU 사용률 조회"        depends_on=[t1] input_from=[t1]
규칙: "…를 찾아/조회한 뒤 그(해당) 서버들의 …"처럼 앞 결과가 뒤 조회의 대상이면 같은 agent라도 2개 task로 분해한다.
```

- 렌더 골든(`tests/snapshots/prompt_render_sha256.json`) 갱신 동반 → **G-4**.
- 1단 지시문(`src/prompts/orchestrator.py:12-13`)에도 같은 규칙 1줄 — 프롬프트 접두 변경은 기동 시 1회 해석이라
  KV 캐시 원칙과 충돌하지 않는다.

**(b) 순차 표지 감지 + 재분해 1회**(결정적 판정 · 분해는 LLM):

```python
# src/utils/prior_dependency.py
_SEQUENTIAL_MARKERS = ("찾아", "찾은", "조회한 후", "조회한 뒤", "조회해서", "그 서버", "해당 서버",
                       "그 중", "그중", "선별된", "앞서")   # 좁게 못 박는다 — 정상 단일 질의 오탐 금지
def has_sequential_marker(query: str) -> bool
```

- `intent_planner`가 단일 task를 내놓았는데 `has_sequential_marker(user_query)`면 **재분해 1회**(힌트: *"이 질의는
  앞 결과가 뒤 조회의 대상이다 — 2개 task로 분해하라"*). 그래도 단일이면 **실행은 하되** `plan_notes=["sequential_not_applied"]`를
  응답에 노출(*"순차 분해가 적용되지 않아 한 번의 조회로 처리했습니다"*).
- 표지는 **판정에만** 쓴다 — 코드가 sub_query를 쪼개지 않는다(82 §4.4가 LLM DAG 분해를 기각한 이유는 *탐색 그룹*이
  고정 SQL이라 코드가 만들 수 있었기 때문이고, 자연어 분할은 그렇지 않다).
- 오탐 방어: *"김포 서버를 찾아줘"*(단일)는 표지 "찾아"에 걸리지만 재분해 결과도 단일이므로 실행 경로는 동일하고
  LLM 호출만 1회 늘어난다 → 플래그 `COMPOSITE_SEQUENTIAL_REPLAN_ENABLED`(기본 **off**) + 발동률·재분해 성공률 관측.
- 1단에는 (b)를 적용하지 않는다 — 도구 루프에 "재분해"가 없다. 1단은 (a) 지시문 + §4.1 게이트로 대응하고, 순차가
  일어나지 않았는지는 §4.4 경과 블록(도구 호출 1회 · 표지 있음)으로 표기한다.

### 4.3 사후 대조 — R-3 (실행 후 · 결정적)

`assess_scope_conformance(verdict, rows) -> ScopeConformance{outside: list[str], missing: list[str]}`:

- **outside**: 후속 결과 행의 식별 값 중 선행 스코프에 없는 것 → 행을 **제거**하고 사유 노출(LLM 폴백 경로에서
  규칙 3 위반을 잡는다). 결정적 컴파일 경로(HAVING IN)는 정의상 0건이라 no-op.
- **missing**: 스코프에 있으나 결과에 없는 서버 → *"선별 N대 중 M대는 해당 기간 통계가 없습니다"* — 82 Wave 8
  0건 진단과 같은 자세(부재를 명시).
- 비교 키는 `scope_col`과 같은 컬럼 종류(`hostname`↔`hostname`, `name`↔`name`) — D-061 혼합 금지.
- 호출 지점: 2단 `agent_orchestrator` 레벨 결과 정규화 직후(`:87-95`) · 1단 `_run_subagent_tool` 결과 적재 전.
- 플래그 `COMPOSITE_SCOPE_POSTCHECK_ENABLED`(기본 **off**).

### 4.4 경과 렌더 — R-5 · R-7 (응답 계약 · append-only)

`result_aggregator`에 결정적 블록 1개 추가(LLM 0회):

```
## 순차 처리 경과
1단계  "CPU 사용률이 높은 서버" → 7대 선별 (hostname: a, b, c, d, e, f, g)
2단계  선별 7대의 최근 1개월(2026-08) CPU 사용률 → 6대 조회, 1대(g) 통계 없음
※ 절단: 없음 / 범위 이탈 제거: 0건
```

- 데이터 원천은 `dependency_verdicts` + `ScopeConformance` + `stat_month` 해석값 — 전부 이미 결정적으로 존재하는 값.
- 미실행(R-1)·미적용(R-2)·절단(R-7) 사유는 이 블록에 실린다. 최종 본문은 기존 그대로(append-only).
- 1단은 collector의 (task, result) 순서로 같은 블록을 만든다(`_aggregate_with_fabrix` 앞).

### 4.5 경로 대칭 — R-4 (결정은 사용자 · G-3)

`path_parity`를 on으로 바꾸면 멀티 경로도 `server_scope`를 결정적 컴파일에 전달한다(이미 구현 · D-099 대칭). 본 계획은
**값을 바꾸지 않고** G-3에서 묻는다. 근거: 운영이 단일 DB라 지금은 무영향이지만, 존 편입(82 2차) 시 후속 조회가
LLM 프롬프트에만 의존하는 상태로 열리게 된다. 켜기로 하면 `.env`에 명시(tri-state 아님 · 기본값 변경 아님).

### 4.6 평가 자산 — R-6

| 자산 | 추가 |
|---|---|
| `testdata/routing_gold/` | 순차 케이스 3건(예시 질의 · 알람 선별형 · "그 중 상위 3대" 랭킹형) — 기대 분해 = task 2개 · `input_from` 배선 |
| `testdata/text2sql_gold/gp.yaml` | t2 형태(*"선행 결과 서버들의 최근 1개월 CPU"* + `server_scope`)의 기대 SQL 1건 — HAVING IN 골든 |
| `scripts/eval_routing.py` | 분해 결과의 `input_from` 정합을 채점 항목에 추가(현재 agent 라벨만) — `--dry-run` 선확인 |
| 실 LLM e2e | **D-127 건별 승인 뒤에만**. `RUN_E2E=1` 설정 자체가 승인 사항 |

### 4.7 (E-1) 3·4단 `sequential_runner` — 2-pass 노드

```
[3단]  field_mapper → semantic_router → route_after_semantic_router
                                          ├─ (신규·최우선) sequential_runner → END      진입 조건 §아래
                                          └─ 현행 분기 그대로
[4단]  field_mapper → (신규 조건부) sequential_runner | schema_analyzer
```

- **진입 조건(전부 AND · 결정적)**: `composite.sequential_fallback_tiers_enabled` **AND** `has_sequential_marker(user_query)`
  **AND** 라우팅 의도가 데이터 조회(캐시·유사어·일반추론·장애진단·존 역질문 제외) **AND**
  `enable_structure_approval == False AND enable_sql_approval == False`(**HITL 우회 금지** — §2.1 주의 실측).
  하나라도 불성립이면 노드 미진입 → 현행 바이트 동일.
- **동작**: ① `_llm_decompose` 재사용(§4.8 검증 포함) → task 2개 이상 + `input_from` 배선이 아니면 **무처리 통과**
  (현행 단일 SQL 경로로 되돌림 + `dependency_notes`에 "순차 분해 미적용") ② t1 `run_data_query_pipeline`
  ③ §4.1 게이트 ④ t2 동일 함수 + `prior_rows`(합류점 `_make_isolated_input` 재사용 — 4경로 대칭) ⑤ §4.3 사후 대조
  ⑥ 최종 응답은 t2의 `organized_data`를 기존 `output_generator` 계약으로 넘기고 §4.4 경과 블록을 덧붙인다.
- **왜 노드인가**: 그래프는 빌드 타임 배선이라 "3·4단에서만 존재"를 노드 등록 조건으로 표현하는 것이 사다리
  원칙(배타적 빌드)과 정합한다. 2단 그래프를 통째로 재배선하지 않는다.
- **범위 제한**: `data_query → data_query`만. 3·4단에는 `alarm_query`·`process_query` 핸들러가 라우팅 대상이 아니므로
  다른 agent 조합은 무처리 통과한다.
- **관측**: `sequential_runner` 진입/통과/미진입 사유 카운트. HITL 불성립으로 미진입한 건수는 별도 키 —
  운영이 승인 게이트를 켜둔 채 순차를 기대하는 상황을 드러낸다.

### 4.8 (E-3) `validate_plan_dag` — backend 무관 결정적 검증 + 되먹임 1회

```python
# src/orchestration/schemas.py (또는 prior_dependency.py — 계층 동일)
def validate_plan_dag(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """반환: (보정된 tasks, 보정 불가 위반 목록). LLM 0회."""
```

| 위반 | 판정 | 처리 |
|---|---|---|
| task_id 중복 | 결정적 | **보정 불가** → 되먹임 |
| `input_from`/`depends_on`이 없는 id 참조 | 결정적 | 보정 불가 → 되먹임 |
| `input_from ⊄ depends_on` | 결정적 | **자동 보정**(`depends_on`에 추가 — `TaskSpec` docstring: 데이터 의존은 순서 의존을 함의) + 보정 사실 기록 |
| 자기 참조 · 순환 | Kahn 잔여 검출 | 보정 불가 → 되먹임 |
| 단일 task + 순차 표지(§4.2-b) | 결정적 | 되먹임(같은 예산) |

- 되먹임은 **user 메시지 말미**에 위반 목록만 덧붙여 1회 재요청(시스템 접두 불변 — KV 캐시). 재위반이면 보정
  가능한 것은 보정하고, 불가하면 **구조화 경로와 같은 `degraded` shape**로 단일 폴백(`:696-707`과 동일 키:
  `stage`·`reason="plan_dag_invalid"`·`attempts`·`detail`).
- 폴백 3곳(`:713·:717·:737`)도 같은 shape로 사유를 싣는다 — 비대칭 해소.
- instructor 경로와의 관계: `DecomposedPlan.model_post_init`이 agent 이름을 검증하지만 DAG 규칙은 검증하지 않는다
  (`schemas.py:27-47` 실측). `validate_plan_dag`는 **두 경로 공통 후단**에 둔다(`_plan_from_model` 뒤에도 적용).
- 재시도 예산: §4.2-(b)와 합쳐 **총 1회**. `intent_planner`가 LLM을 최대 2회 부른다(현행 1회).
- 소유: `intent_planner.py` 편집은 79 트랙 E 소유 → **G-5**. 79 소유자가 E-3a 후속으로 가져가겠다고 하면 본 계획은
  `validate_plan_dag` 함수와 테스트만 제공하고 배선은 79가 한다.

### 4.9 (E-4) DB별 스코프 분할 — `prior_scope_by_db`

```python
# subagents._extract_identity_rows: _source_db 패스스루(식별 컬럼 아님 — 프롬프트 블록·HAVING 값에는 미노출)
# query_gen_common.collect_prior_identity_values_by_db(prior_rows) -> dict[str, tuple[str, list[str]]]
#   키 = _source_db(없으면 "" — 단일 DB·구 형식 행), 값 = (col, values)  · 컬럼 종류 우선 규칙은 기존 함수와 동일
# multi_db_executor._MultiRun.prior_scope_by_db: Optional[dict]
#   _run_single_target: scope = run.prior_scope_by_db.get(db_id) if 분할 있음 else run.prior_scope
```

| 상황 | 동작 |
|---|---|
| 행에 `_source_db` 없음(단일 DB 선행) | 분할 없음 → **현행 비트 동일** |
| 분할 있음 · 대상 DB에 선별 서버 0대 | 그 DB를 **후속 대상에서 제외** + 경과 *"공동존 여의도: 선별 0대 → 미조회"* |
| 분할 있음 · 대상 DB에 선별 서버 있음 | 그 DB 값만 `server_scope`/프롬프트 블록에 투입 |
| 프롬프트 블록(LLM 폴백) | DB별 `_generate_sql` 호출 시 해당 DB 값만 렌더 — `build_prior_rows_block`에 `db_id` 인자 추가(기본 None = 현행) |
| 실행 그룹이 채워졌을 때(82 2차) | dependent task의 그룹 순회는 같은 분할을 쓴다: `group.db_ids ∩ 분할 키`가 공집합인 그룹은 미조회 + 경과 표기. **계약만 확정** — 배선은 82 2차가 `execution_groups`를 채우며 한다 |

- `_source_db`는 `collect_prior_identity_values`가 이미 배제(`is_server_identity_col` False)하므로 패스스루가
  HAVING 값·프롬프트를 오염시키지 않는다 — 회귀 테스트로 고정.
- `prior_targets` 경로와의 대칭: 그쪽은 이미 행별 `_source_db`를 쓴다(D-176). 이 절로 두 경로가 같은 원칙이 된다.
- 플래그 `COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED`(기본 **off**). off면 패스스루도 하지 않는다(행 shape 불변).

### 4.10 (E-2) 사유 채널 통합 — `dependency_notes`

- `AgentState.dependency_notes: Optional[list[dict]]`(요청 스코프) — `{stage, task_id, kind, reason, detail}`.
  `kind ∈ {gate, postcheck, sufficiency, truncation, decompose}`.
- 생산자: §4.1 게이트 · §4.3 대조 · §4.8 분해 · `_build_prior_targets_for_task` 절단(`:877-880` 로그 → 노트) ·
  `agent_orchestrator`의 충족도 미달(**`sufficiency_shortfalls` 대신 이 채널에 적재** — 죽은 키는 제거하지 않고
  같은 값을 병기해 두었다가 폐기 기한 2027-03-09, D-161 ①).
- 소비자: §4.4 경과 블록(2단·1단·3·4단 `sequential_runner` 전부) + `/query` 응답 JSON에 `dependency_notes` 그대로 노출
  (UI 렌더는 범위 밖 — 텍스트 블록으로 이미 보인다).
- 상한 이원화 표기: 같은 선행 결과가 data_query 후속(100)·process_query 후속(10)에서 다르게 잘리면 두 절단 노트가
  나란히 실린다 — 값 통일은 **G-7**.

---

## 5. 상태·설정 변경

| 항목 | 변경 | 기본 | 만료 판단 |
|---|---|---|---|
| `AgentState.dependency_verdicts` | 신규 · 요청 스코프(`src/api/routes/query.py` 초기화 목록에 추가) | `None` | — |
| `AgentState.plan_notes` | 신규 · `["sequential_not_applied", …]` | `None` | — |
| `task_plan[].status` | `"skipped"` 값 추가 | — | 소비처 전수 grep 후 |
| `CompositeConfig.sequential_gate_enabled` | `COMPOSITE_SEQUENTIAL_GATE_ENABLED` | **False** | 발동률 관측 4주 후 on 판단(만료 2027-03-09) |
| `CompositeConfig.sequential_replan_enabled` | `COMPOSITE_SEQUENTIAL_REPLAN_ENABLED` | **False** | 재분해 성공률로 판단 |
| `CompositeConfig.scope_postcheck_enabled` | `COMPOSITE_SCOPE_POSTCHECK_ENABLED` | **False** | outside 검출률 0이면 결정적 경로 한정으로 off 유지 가능 |
| `AgentState.dependency_notes` | 신규 · 요청 스코프(§4.10) — `sufficiency_shortfalls`는 병기 후 폐기(2027-03-09) | `None` | — |
| `CompositeConfig.prior_scope_by_db_enabled` | `COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED`(§4.9) | **False** | 멀티 존 편입(82 2차) 시 on 판단 |
| `CompositeConfig.plan_dag_validation_enabled` | `COMPOSITE_PLAN_DAG_VALIDATION_ENABLED`(§4.8) | **False** | 위반 검출률 관측 후 on |
| `CompositeConfig.sequential_fallback_tiers_enabled` | `COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED`(§4.7) | **False** | G-6 결정 종속 |
| 관측 | ~~`src/observability/` 카운터 3종~~ → **로그 문구 3종으로 대체**(2026-09-10 사용자 확정 · §11.1 Q6): `순차 게이트 관측(off)` · `사후 대조 관측(off)` · `순차 재분해 관측(off)` — off일 때 판정만 하고 로그. 카운터는 미구현 | — | 1차 3종을 관측 없이 on 확정해 카운터의 원래 목적(1차 on 판단)이 소멸 |

`src/config.py`의 `CompositeConfig`(`:1016`)는 82·81이 동시 편집한 이력이 있다 — 착수 직전 `git status --short`로
hunk 겹침을 재확인한다(메모리 `concurrent-worktree-edits`).

---

## 6. 구현 Wave (test-first · 각 Wave 단독 랜딩 가능)

| Wave | 범위 | 대상 파일 | 검증(수용 기준) |
|---|---|---|---|
| **W1 게이트**(R-1·R-7) | `assess_prior_dependency` + 2단·1단 호출부 + `skipped` 상태 + 사유 노출 | `src/utils/prior_dependency.py`(신규) · `agent_orchestrator.py` · `deepagents_tools.py` · `subagents.py` · `state.py` · `routes/query.py` · `config.py` | `tests/test_composite/test_sequential_gate.py`(신규): 선행 error/0건/식별 컬럼 없음 → 후속 미실행 + 사유 / 정상 → 주입 동일 / 101행 → `truncated=True, truncated_count=1` / **플래그 off → `_run_agent` 호출 인자·결과 바이트 동일** / 1단·2단이 같은 함수를 호출(대칭 고정) |
| **W2 분해**(R-2) | 예시 3-2 · 1단 지시문 1줄 · `has_sequential_marker` · 재분해 1회 · `plan_notes` | `src/prompts/intent_planner.py` · `src/prompts/orchestrator.py` · `intent_planner.py` · `prompt_render_sha256.json` | `tests/test_orchestration/test_sequential_decompose.py`(신규): 표지 감지 정오표(오탐 케이스 5건 포함) / 단일→재분해 1회만(mock LLM 호출 수 단언) / 재분해 실패 → 실행 + `plan_notes` / **플래그 off → 프롬프트 외 바이트 동일**. 골든 갱신은 **G-4 승인 커밋에서만** |
| **W3 대조·경과**(R-3·R-5) | `assess_scope_conformance` + 집계 블록 | `prior_dependency.py` · `agent_orchestrator.py` · `deepagents_tools.py` · `result_aggregator.py` · `deep_agent.py` | `tests/test_composite/test_scope_postcheck.py` · `tests/test_orchestration/test_sequential_trace_block.py`: outside 제거·missing 표기 / 결정적 경로 no-op / 블록 렌더 골든(LLM 0회) / 기존 `test_result_aggregator` 회귀 0 |
| **W4 평가**(R-6) | 골드 3+1건 · `eval_routing` 채점 항목 | `testdata/` · `scripts/eval_routing.py` | `--dry-run` 통과 · 실 실행은 D-127 승인 후 |
| **W5 대칭**(R-4) | G-3 결정 반영(`.env` 명시) | `.env` | 결정 없으면 착수하지 않음 |
| **W6 채널 통합**(E-2 · §4.10) | `dependency_notes` 신설 · 게이트/대조/절단/충족도 미달 합류 · `prior_targets` 소비자 게이트 대칭 · 경과 블록이 이 채널을 렌더 | `state.py` · `agent_orchestrator.py` · `subagents.py` · `result_aggregator.py` · `deep_agent.py` · `routes/query.py` | `tests/test_composite/test_dependency_notes.py`(신규): 충족도 미달 사유가 **응답 본문에 실제로 나타난다**(현행 결함 재현 — 지금은 0건) / process_query 후속 선행 0건 → 미실행+노트 / 절단 노트 2종(100·10) 병기 / 플래그 off → `sufficiency_shortfalls` 값·응답 바이트 동일 |
| **W7 DB별 분할**(E-4 · §4.9) | `_source_db` 패스스루 · `collect_prior_identity_values_by_db` · `_MultiRun.prior_scope_by_db` · 0대 DB 제외 · `build_prior_rows_block(db_id=)` | `subagents.py` · `query_gen_common.py` · `multi_db_executor.py` · `prompt_blocks.py` | `tests/test_nodes/test_prior_scope_by_db.py`(신규): b0/gp 혼재 행 → DB별 IN 목록 분리(**SQL 문자열 단언**) / 선별 0대 DB 미조회 + 노트 / 태그 없는 행 → 현행 비트 동일 / `_source_db`가 HAVING 값·프롬프트에 **미노출** / 그룹 계약 단위 테스트(`execution_groups` mock — 82 2차 배선 전 계약만) |
| **W8 DAG 검증**(E-3 · §4.8) | `validate_plan_dag` · 되먹임 1회(§4.2-b와 예산 공유) · 폴백 3곳 `degraded` 사유 · 두 경로 공통 후단 | `schemas.py` · `intent_planner.py`(**G-5**) | `tests/test_orchestration/test_plan_dag_validation.py`(신규): 위반 5종 정오표 / `input_from ⊄ depends_on` 자동 보정 / 순환 → 되먹임 1회 → 재위반 시 `degraded` / **LLM 호출 수 ≤ 2** 단언 / 플래그 off → `_llm_decompose` 반환 바이트 동일 / 기존 `test_intent_planner` 회귀 0 |
| **W9 3·4단 러너**(E-1 · §4.7) | `sequential_runner` 노드 · 3단 조건부 최우선 분기 · 4단 조건부 엣지 · HITL 불성립 미진입 | `graph.py` · `src/nodes/sequential_runner.py`(신규) · `config.py` | `tests/test_graph.py` 확장 + `tests/test_nodes/test_sequential_runner.py`(신규): 3·4단 빌드에만 노드 존재(1·2단 빌드 노드 목록 불변) / 진입 조건 4항 각각 불성립 시 미진입 / **`enable_structure_approval=True`면 미진입**(우회 금지 골든) / 2-pass가 같은 합류점 `_make_isolated_input`을 부른다(대칭 고정) / 플래그 off → 그래프 배선·노드 목록 바이트 동일 |

**공통 게이트**: `pytest`(본체+noise_gate) 회귀 0 · `arch_check --ci` 0(`sequential_runner`는 `nodes/`=application에서
`orchestration/subagents`를 부르면 **역방향 의존** — `run_data_query_pipeline`을 부르는 얇은 진입은 `orchestration/`에 두고
노드는 그것만 import한다; 착수 시 `arch_check --verbose`로 방향 실측) · `overfit_check --ci` 신규 유입 0(`prior_dependency.py`에
폴스타 리터럴 금지 — 컬럼 종류 판정은 `is_server_identity_col` 재사용) · 기준선 대조는 `git worktree add`(스태시 금지).

**착수 순서**: W1 → W6 → W3 → W2 → W8 → W7 → W4 → W9 (→ W5).
- W6이 W3보다 앞: 사유 채널이 있어야 게이트·대조 결과가 응답에 닿는다(지금은 죽은 채널).
- W8은 G-5(79 소유자) 뒤. W9는 G-6 뒤이며 HITL 우회 금지 골든이 먼저 있어야 한다.
- W7은 운영이 단일 DB라 급하지 않지만 82 2차보다 **먼저** 있어야 그룹 계약이 배선될 때 스코프 분할이 준비돼 있다.

---

## 7. 검증 시나리오(수용)

| # | 질의 | 기대(플래그 on) |
|---|---|---|
| S1 | 예시 질의 · t1 7대 | t2가 7대 스코프로 실행 · 경과 블록 "7대 선별 → 7대 조회" · 결정적 경로면 SQL에 HAVING IN 7값 |
| S2 | 예시 질의 · t1 0대 | t2 **미실행** · *"조건에 해당하는 서버가 0대"* · 전체 서버 결과 **없음**(회귀 테스트로 고정 — 현행 결함 재현) |
| S3 | 예시 질의 · t1 error | t2 미실행 · 선행 오류 사유 노출 |
| S4 | 예시 질의 · t1 130대 | 100대로 절단 실행 + *"30대 절단"* 표기 |
| S5 | *"김포 서버를 찾아줘"* | 단일 task 유지(오탐 없음) · 재분해 최대 1회 · `plan_notes` 없음(단일이 정답) — **이 케이스는 표지 정오표에서 "단일이 정답"으로 분류돼야 한다** |
| S6 | 예시 질의 · LLM 폴백 SQL이 목록 밖 서버 포함 | outside 행 제거 + 사유 |
| S7 | 1단 경로 · 오케스트레이터가 도구 1회 호출 | 실행은 하되 경과 블록에 *"순차 분해 미적용"* |
| S8 | 플래그 전부 off | S1~S7 전부 **현행 동작·바이트 동일** |
| S9 | *"CPU 높은 서버를 찾아 그 서버들의 프로세스"* · t1 0대 | process_query 후속 **미실행** + 노트(E-2) — 현행은 `prior_targets` 미주입 상태로 실행됨 |
| S10 | 예시 질의 · 선행 행이 b0 3대 + gp 4대 · 대상 DB 3존 | t2가 b0에 3값·gp에 4값으로 **분리된 IN** · yd **미조회** + 노트(E-4) |
| S11 | LLM 분해가 `t2.input_from=["t9"]`(미존재) 또는 t1↔t2 순환 | 되먹임 1회 → 정상이면 순차 실행, 재위반이면 단일 폴백 + `degraded` 사유(E-3) — 현행은 **조용히 병렬** |
| S12 | 3단 기동(`ENABLE_INTENT_ORCHESTRATION=false`) · HITL off · 예시 질의 | `sequential_runner` 2-pass · 경과 블록(E-1). HITL on이면 미진입 + 현행 단일 SQL |

---

## 8. 사용자 확정 게이트 (착수 전)

| 게이트 | 질문 | 권고 | 근거 |
|---|---|---|---|
| **G-1** | 선행 0건일 때 후속을 (a) 미실행+사유 (b) 되묻기 (c) 현행(무스코프 실행) 중 무엇으로? | **(a)** | (c)는 침묵 오류. (b)는 82 §6 실측(무조건 묻기는 세션 시간 ~2배)과 "답하지 않아도 전체 조회"가 여기선 오답이라 불성립. 0건은 그 자체가 답이다 |
| **G-2** | *"높은"*의 기본 해석 — LLM 자유(현행) vs 결정적 기본(상위 N 또는 임계) + 응답 표기 | **현행 유지 + 경과 블록에 해석값 표기**(t1 SQL의 ORDER/LIMIT·임계를 결정적으로 추출해 표기) | 해석 강제는 D-035 범위를 넘고 골드셋 근거가 없다. 표기만으로 오해는 막힌다 |
| **G-3** | `path_parity`를 운영 `.env`에서 on으로 명시할지 | **on 권고** — 단, 82 2차(존 편입) 착수 시점에 | 지금은 무영향, 존 편입 후엔 R-4 노출 |
| **G-4** | 프롬프트 예시 3-2 추가(렌더 골든 갱신) 승인 | 승인 요청 | 79 S-1 기준선과 충돌 여부를 79 소유자가 판단 |
| **G-5** | `intent_planner.py` 편집(§4.8 DAG 검증 배선) — 79 트랙 E 소유 파일 | **본 계획이 함수·테스트를 만들고 배선까지 한다**(79 E-3a는 구현 완료·후속 없음 실측) — 79 소유자가 회수 원하면 배선만 넘긴다 | 79 v14 (a)안(소유) · 두 경로 공통 후단이라 E-3a와 충돌 없음 |
| **G-6** | 3·4단 `sequential_runner` 도입 여부 — (a) 도입(HITL off 조건부) (b) 표기만(*"이 경로는 순차 미지원"*) (c) 미편입 | **(a)** — 단 운영은 1·2단 플래그 true라 실효는 강등 기동에 한정. 비용 대비 낮으면 (b) | §2.1 실측 — 2-pass는 기존 함수 재사용으로 신규 로직이 얇다. HITL 우회는 조건으로 차단 |
| **G-7** | 선행 스코프 상한 통일 — data_query 후속 100 vs process_query 후속 10 | **값은 유지, 표기만**(§4.10) — 10은 대상 호스트 부하 가드(78 D-165), 100은 IN 절 상한이라 목적이 다르다 | 통일하면 한쪽 근거가 깨진다 |
| **D-127** | W4 실 LLM 평가 실행 | 건별 승인 | 과금 |

---

## 9. 위험 · 미결

| # | 항목 | 대응 |
|---|---|---|
| R-A | `status="skipped"` 도입이 `task_plan` 소비처(집계·replanner `_filter_futile_retries`·UI)를 깨뜨림 | W1 착수 시 `status ==`/`status in` 전수 grep → 소비처별 처리 확인 후 도입. 안 되면 `failed` + `error="skipped:prior_empty"`로 대체 |
| R-B | 재분해 1회가 KV 캐시 접두를 흔듦 | 재분해 프롬프트는 **힌트를 user 메시지 끝에만** 추가(시스템 접두 불변) |
| R-C | 표지 어휘가 정상 단일 질의를 오탐 | 표지는 재분해 발동에만 쓰고 실행 경로를 바꾸지 않는다. 정오표에 오탐 케이스 5건 이상 고정 |
| R-D | 두 축 동시 성립(*"CPU 높은 서버를 찾아 그 서버들이 있는 존의 …"*) | **v2에서 해소 방향 확정** — §4.9 DB별 분할(존 = `_source_db` 집합). 실행 그룹 배선은 82 2차 몫, 계약만 본 계획 |
| R-H | `sequential_runner`가 3·4단에서 HITL 승인 게이트를 우회 | 진입 조건에 HITL off 포함(§4.7) + 골든 테스트. 운영 기본은 `enable_structure_approval=on`이라 **기본 상태에서는 진입하지 않는다** — 이 사실을 §4.7 관측 키로 드러낸다 |
| R-I | `_source_db` 패스스루가 식별 컬럼으로 오인돼 HAVING/프롬프트에 새어 나감 | `is_server_identity_col` False 실측 + 미노출 골든(W7) |
| R-J | `validate_plan_dag`가 instructor 경로 `model_post_init`과 이중 검증 | 규칙 집합을 분리(agent 이름 vs DAG) · 공통 후단 1곳에서만 호출 |
| R-K | `nodes/sequential_runner` → `orchestration/subagents` 역방향 import(arch_check) | 얇은 진입을 `orchestration/`에 두고 노드는 그것만 import · 착수 시 `--verbose` 실측 |
| R-E | 선행 결과가 여러 `input_from`(t1·t2 → t3)일 때 스코프 합집합 vs 교집합 | 현행 `collect_prior_identity_values`는 **합집합**. 교집합 의미("둘 다 해당")는 자연어에서 구분이 안 되므로 합집합 유지 + 경과 블록에 원천 task별 대수 표기 |
| R-F | 1단에서 오케스트레이터 LLM이 미실행 사유를 무시하고 재호출 반복 | 도구 반환에 *"재호출하지 말 것"* 명시 + `recursion_limit`(25)이 상한. 재호출 횟수를 `sequential_gate` 관측에 포함 |
| R-G | 동시 작업 — 사용자 스테이징 분(`plans/87`·`CLAUDE.md`·`docs/02` 충돌 해소분)과 병행 세션(`plans/89`) | 사용자 git 작업은 건드리지 않는다. `docs/02`·`INDEX` 편집은 각각 1행 추가로 한정(병합 충돌 표면 최소화) — 사용자에게 고지 |

---

## 10. D-203 예약 초안 (착수 시 본문 등재)

- **결정**: 복합 질의의 순차 의존(`input_from`)은 코드가 계약을 보장한다 — ①선행 결과 게이트(실패·0건·식별 컬럼
  부재 → 후속 미실행 + 사유) ②사후 대조(범위 이탈 제거·부재 표기) ③경과 렌더(단계·대수·절단·해석값) ④분해는 LLM이
  하되 표지 감지 시 재분해 1회 + 미적용 사유 노출. 1단·2단 합류점 공통 모듈, 플래그 기본 off.
- **근거**: 배관(D-086·D-095·D-099)은 있으나 "선행이 비었을 때" 계약이 없어 전체 서버 결과가 선별 결과로 둔갑하는
  침묵 오류(본 계획 §3). 82 §4.4 원칙(LLM=무엇을, 코드=어디서/얼마나)의 연장.
- **v2 확대분**: ⑤사유 채널 단일화(`dependency_notes` — 죽은 `sufficiency_shortfalls` 합류·폐기 기한) ⑥`prior_rows`의
  DB별 스코프 분할(`_source_db` 패스스루 · 선별 0대 DB 미조회 · 실행 그룹 계약) ⑦분해 DAG 결정적 검증 + 되먹임 1회
  (backend 무관 · 예산 합계 1회) ⑧3·4단 `sequential_runner` 2-pass(HITL 승인 on이면 미진입).
- **대안 기각**: 되묻기(0건은 답이다) · 코드가 자연어를 분할(고정 SQL이 아니라 불가) · 실행 그룹 축 재사용(축이 다름 §2.4) ·
  3·4단에서 2단 그래프 재배선(사다리 배타성 훼손).

---

## 11. 플래그 6종 단계별 테스트 절차 · 실효화 결정 (v5 · 2026-09-09 인터뷰)

> **실측 기준(2026-09-09)**: 1차·2차 코드는 커밋 완료(관련 `src/` 전부 clean). 운영 `.env`에는 6종 플래그와
> `TEXT2SQL_PATH_PARITY`가 **한 줄도 없다** = 전부 off = R-1 침묵 오류가 운영에 그대로 살아 있다. §5의 관측
> 카운터(`src/observability/`)는 **미구현**이며 off 상태 관측은 `agent_orchestrator`·`deepagents_tools`의
> `순차 게이트 관측(off)` 로그 1줄뿐이다 — "발동률 4주 관측 후 on"은 실질적으로 성립하지 않는다.

### 11.1 사용자 확정 — 플래그 실효화 (인터뷰 Q1 · 2026-09-09)

**결정: 1차 3종 on · 2차 3종 off를 운영 `.env`에 명시한다.**

```
# .env (운영 · plans/88 §11.1 · D-203)
COMPOSITE_SEQUENTIAL_GATE_ENABLED=true
COMPOSITE_SCOPE_POSTCHECK_ENABLED=true
COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED=true
COMPOSITE_PLAN_DAG_VALIDATION_ENABLED=false
COMPOSITE_SEQUENTIAL_REPLAN_ENABLED=false
COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED=false
```

- 근거: 1차 3종은 LLM 호출 수를 바꾸지 않는 결정적 판정이고 현행 동작 자체가 결함이다(`plans/81` G-1이 같은
  논리로 기본 on 예외를 인정한 선례). 코드 기본값은 바꾸지 않는다(plans/80 §5.4-③ 유지 — 운영 파일에서만 on).
- 2차 3종은 §11.2 단계 4·5 테스트 결과를 본 뒤 별도 판단한다(재분해는 LLM 1회 추가, 3·4단 러너는 운영 1단
  기동에서 실효 없음).
- 적용 시점: §11.2 단계 2·3·6(1차 3종 실 실행)을 통과한 뒤 `.env`에 반영한다.

**인터뷰 확정 전건(2026-09-10 · `interview-me` · 되짚기에 명시적 "예")**

| # | 질문 | 확정 | 결과 |
|---|---|---|---|
| Q1 | 운영 `.env` 플래그 반영 | **1차 3종 on · 2차 3종 off 명시** | 위 블록. 적용은 단계 2·3·6 통과 후 |
| Q2 | v3·v4 가정 게이트 G-1·G-4·G-5·G-6·G-7 | **5건 전부 확정** — G-1(a) 미실행+사유 · G-4 프롬프트 무변경 · G-5 본 계획이 배선 · G-6 (a) 러너 도입(HITL on이면 미진입) · G-7 상한 유지+표기 | D-203 "사용자 확정 대기" → 확정 |
| Q3 | G-2 "높은" 해석값 표기(미구현분) | **범위에 넣는다** — t1 실행 SQL에서 결정적 추출, 실패 시 표기 생략 | §11.5 구현 완료 |
| Q4 | G-3 `TEXT2SQL_PATH_PARITY` | **`false` 명시 · on 전환은 82 2차(존 편입) 착수 조건으로 이관** — W5는 이관 종료 | `.env`에 `TEXT2SQL_PATH_PARITY=false` 추가(§11.1 블록과 함께) · `plans/82` 2차 체크리스트에 "PATH_PARITY on + 88 ③ 3-c" |
| Q5 | 실 LLM 실행 방식(D-127) | **단계 1 즉시(과금 0) · 단계 2부터 "단계 N · K건" 단위 승인** | §11.5 단계 1 실측 |
| Q6 | §5 관측 카운터 3종 | **미구현 · 로그로 대체 + off 관측 로그를 재분해·사후 대조에도 추가(게이트와 대칭)** | §11.5 구현 완료 |
| Q7 | 단계 2 1단 실측(§11.5) 후 재실행 | **2단 강등(`ENABLE_DEEPAGENTS_PACKAGE=false`)으로 3건 재실행 승인** — 1-c'는 샌드박스에 있는 월(2026년 7월) 지정 | §11.5 단계 2' |
| Q8 | 1단 게이트 발동 조건 공백(§11.6) | **(c) 2단 검증 후 판단** — 지금은 sub_query 관측 로그만, (b) 규칙 추가는 문구 표본 수집 뒤 별도 질문 | — |
| Q9 | 긍정 경로가 샌드박스 선행 결함에 막힘 | **샌드박스 프로필 생성(88 범위 밖 · 별도 항목) → 1-c'·단계 3·6 재승인 후 실행 → 통과 시 `.env` 반영** | §11.5 B1 해소 |
| Q10 | B1 해소 후 긍정 경로 재실행 | **4건 승인** — 2단: 1-c''(GATE) · 2-a'(+POSTCHECK) · 3-b'(+PRIOR_SCOPE_BY_DB) + 1단 정본 1건(GATE · sub_query 표본 수집) · 질의는 샌드박스에 있는 월(2026년 7월) | §11.5 단계 2'' |
| Q12 | 1단 차단어 수정 실 재검증 | **1건 승인** → 성공(§11.6 표) · 커밋은 사용자가 직접 | — |
| Q11 | 1단 게이트 우회 확정 원인(차단어 축 오판) | **(d) 차단어 축 한정** — `_is_global_scope`: `(전체|모든|전)\s*(서버|장비|호스트|대상|리소스|vm)`일 때만 차단. 실측 sub_query 정오표 고정 · 1단 재검증은 별도 승인 | §11.6 (d) 구현 완료 |
| — | Out of scope(확정) | 관측 카운터 · `PATH_PARITY` on · ③ 멀티 DB 런타임(3-c) · G-1 되묻기 · G-7 통일 · 프롬프트 변경 · 2차 3종 운영 on 판단(단계 4·5 뒤) | |

### 11.2 공통 준비

- **플래그는 기동 시 1회 해석** — 단계마다 프로세스를 새로 띄운다. `.env`를 고치지 않고 **쉘 환경변수 접두**로
  켠다(pydantic-settings는 환경변수가 `env_file`보다 우선).
  ```bash
  # [CWD=collectorinfra · 루트 venv]
  COMPOSITE_SEQUENTIAL_GATE_ENABLED=true python -m src.main --query "…"
  ```
- **실 파이프라인 실행은 전부 과금 경로**(`LLM_PROVIDER=gemini`·`ORCHESTRATOR_PROVIDER=gemini`) — 단계 2 이후는
  **건마다 D-127 승인**. 단계 1만 과금 0.
- 관찰 지점 3곳: ①응답 본문 말미 `## 순차 처리 경과` 블록(결정적 렌더 · CLI·웹 UI 공통) ②`/query` 응답 JSON
  `dependency_notes[{stage, task_id, kind, reason, detail}]` ③서버 로그(`LOG_LEVEL=INFO`) 고정 문구.

### 11.3 플래그별 절차

**① `COMPOSITE_SEQUENTIAL_GATE_ENABLED` — 선행 결과 게이트(R-1)**

| 단계 | 설정 | 질의 | 기대 |
|---|---|---|---|
| 1-a off 관측 | 접두 없음 | `CPU 사용률이 200%를 넘는 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률을 보여줘` | t1 0건인데 **t2가 전체 서버 결과**(현행 결함 재현). 로그 `순차 게이트 관측(off) task=t2 reason=prior_empty` |
| 1-b on 0건 | `…GATE_ENABLED=true` | 같은 질의 | t2 미실행 · 경과 블록 "0대" 사유 · 로그 `순차 게이트 — task t2 미실행(prior_empty)` · UI 단계 목록 `⊘ 건너뜀` |
| 1-c on 정상 | 같음 | `CPU 사용률이 높은 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률을 보여줘` | t2 SQL `HAVING … IN (선별 hostname)` · 경과 블록 "N대 선별 → N대 조회" |
| 1-d 절단 | 같음 | 101대 이상 선별되는 질의(예: `CPU 사용률이 0% 이상인 서버를 찾아 …`) | 실행되되 "절단 N대"(R-7) |

**② `COMPOSITE_SCOPE_POSTCHECK_ENABLED` — 사후 대조(R-3)** — 결정적 컴파일 경로는 outside 0건이라 런타임 강제
불가(단위 테스트 `test_scope_postcheck.py`가 고정). 런타임은 **missing**을 본다. 게이트를 함께 켠다(verdict를
스코프 정본으로 재사용).

| 단계 | 설정 | 질의 | 기대 |
|---|---|---|---|
| 2-a | `GATE=true SCOPE_POSTCHECK=true` | 1-c 질의 | 해당 월 통계 없는 선별 서버가 있으면 "선별 N대 중 M대는 해당 기간 통계가 없습니다" |
| 2-b | 같음 | 사양 축 선별(`메모리가 64GB 이상인 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률`) | missing 표기 |

**③ `COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED` — DB별 분할(E-4)** — 활성 DB 2개 이상에서만 의미. 운영
`ACTIVE_DB_IDS=polestar` 단일, 로컬 도커 PG 1대(`db/` 5433)라 현 환경은 **비트 동일 경로**만 밟힌다.

| 단계 | 설정 | 기대 |
|---|---|---|
| 3-a 단위 | `pytest tests/test_nodes/test_prior_scope_by_db.py -v` | DB별 IN 분리(SQL 문자열) · 0대 DB 미조회 노트 · `_source_db` 미노출 |
| 3-b 단일 DB | 플래그 on + 1-c | 현행과 동일(태그 없음 → 분할 없음) — 켜도 안전 |
| 3-c 멀티 DB | 공동존 환경 `ACTIVE_DB_IDS=polestar_cm_gp,polestar_cm_yd` + on | DB별 분리 IN · "여의도: 선별 0대 → 미조회" 노트 — **82 2차 환경 필요** |

**④ `COMPOSITE_PLAN_DAG_VALIDATION_ENABLED` — DAG 검증(E-3)** — 위반은 LLM 오분해 시에만 발생, 런타임 강제 불가.

| 단계 | 설정 | 기대 |
|---|---|---|
| 4-a 단위 | `pytest tests/test_orchestration/test_plan_dag_validation.py -v` | 위반 5종 · 자동 보정 · LLM ≤2 |
| 4-b dry-run(과금 0) | `python scripts/eval_routing.py --decomposition --dry-run` | 골든 5건 정합성 |
| 4-c mock(과금 0) | `python scripts/eval_routing.py --decomposition --mock` | 채점 경로 구동 |
| 4-d 런타임 | `…PLAN_DAG_VALIDATION_ENABLED=true` + 1-c | 정상이면 로그 없음 · 위반 시 `intent_planner 되먹임 재요청 1회(D-203)` · 재위반 `DAG 위반 잔존 — 단일 data_query 폴백` + `decompose` 노트 |

**⑤ `COMPOSITE_SEQUENTIAL_REPLAN_ENABLED` — 표지 재분해(R-2)** — 표지 16개(`"찾아 "`·`"찾아서"`·`"그 서버"`·
`"해당 서버"`·`"그 중"`·`"조회한 후"` …). `"찾아줘"`는 표지 아님. ④와 되먹임 예산 공유(합계 1회).

| 단계 | 설정 | 질의 | 기대 |
|---|---|---|---|
| 5-a 발동 | `…REPLAN_ENABLED=true` | 1-c 질의 | 첫 분해가 단일이면 `되먹임 재요청 1회` → 2 task |
| 5-b 미적용 노출 | 같음 | 같은 질의 반복 | 재분해 후에도 단일이면 "순차 분해가 적용되지 않아 한 번의 조회로 처리" |
| 5-c 오탐 | 같음 | `김포 운영 서버 목록을 찾아줘` | 재분해 0회 · 노트 없음(골든 d-004) |
| 5-d 오탐 2 | 같음 | `전체 서버의 OS 종류와 버전을 보여줘` | 단일 유지(골든 d-005) |

**⑥ `COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED` — 3·4단 러너(E-1)** — 운영 1단 정본에서는 실효 없음. 사다리를
강등해야 하며 HITL 플래그가 하나라도 on이면 미진입. 운영 `.env`에 `ENABLE_STRUCTURE_APPROVAL`이 없어 **기본 on** →
반드시 false로 내린다.

| 단계 | 설정 | 기대 |
|---|---|---|
| 6-a 단위 | `pytest tests/test_orchestration/test_sequential_runner.py tests/test_graph.py -v` | 3·4단 빌드에만 노드 · HITL on이면 미진입 골든 |
| 6-b 3단 | `ENABLE_DEEPAGENTS_PACKAGE=false ENABLE_INTENT_ORCHESTRATION=false ENABLE_SEMANTIC_ROUTING=true ENABLE_STRUCTURE_APPROVAL=false ENABLE_SQL_APPROVAL=false COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED=true python -m src.main --query "<1-c>"` | 기동 로그 사다리 확정 `semantic_router` · 2-pass + 경과 블록 · 표지 없는 질의는 현행 단일 SQL |
| 6-c 4단 | 6-b + `ENABLE_SEMANTIC_ROUTING=false` | 사다리 `legacy` · `field_mapper → sequential_runner` |
| 6-d 우회 금지 | 6-b + `ENABLE_STRUCTURE_APPROVAL=true` | 미진입 → 현행 단일 SQL + 승인 게이트 정상 |

### 11.4 권장 실행 순서 · 과금 건수

| 순서 | 내용 | 과금(Gemini) |
|---|---|---|
| 1 | 단위 6파일 + `--decomposition --dry-run` + `--mock` | 0 |
| 2 | ① 1-a → 1-b → 1-c | 3 |
| 3 | ② 2-a | 1 |
| 4 | ④+⑤ 동시 on: 1-c · 5-c · 5-d | 3 |
| 5 | ⑥ 6-b · 6-c · 6-d | 3 |
| 6 | ③ 3-b | 1 |
| 7 | `RUN_E2E=1 python scripts/eval_routing.py --decomposition`(W4 · 골든 5건) | 5 |

단위 6파일:
```bash
pytest tests/test_composite/test_sequential_gate.py tests/test_composite/test_scope_postcheck.py \
       tests/test_composite/test_dependency_notes.py tests/test_nodes/test_prior_scope_by_db.py \
       tests/test_orchestration/test_plan_dag_validation.py tests/test_orchestration/test_sequential_runner.py -v
```

### 11.5 실측·구현 기록 (2026-09-10)

**단계 1(과금 0) 실측**

| 항목 | 결과 |
|---|---|
| 단위 6파일 | **89 passed**(구현 전 기준) |
| `eval_routing.py --decomposition --dry-run` | 골든 5건 정합 OK(순차 3 포함) · `plan_dag_validation=False sequential_replan=False` |
| `eval_routing.py --decomposition --mock` | **채점 경로 구동만 확인** — FabriX KBGenAI 목업은 분해 JSON을 내지 않아 5건 전부 "분해 결과 없음 → 단일 폴백"으로 채점됨(순차 3건 ✗ · 단일 2건 ✓ · `sequential_preserved=0`). 판정값은 무의미하며 §11.3 4-c의 기대("채점 경로 구동")와 일치. 분해 정확도는 단계 7(실 LLM)에서만 측정된다 |

**G-2 구현(Q3)** — `src/utils/prior_dependency.py::extract_selection_basis(sql)`: 선행 결과의 `generated_sql`(1단·2단 공통
키 — `subagents.run_data_query_pipeline`이 적재)에서 ①`ORDER BY … LIMIT N` / `FETCH FIRST N ROWS ONLY`(DB2) → *"상위 N건(컬럼
내림차순)"* ②`WHERE/HAVING … 컬럼 op 숫자` → *"조건 컬럼 >= 값"*(CASE/집계 안의 컬럼도 경계 키워드 역추적으로 뽑는다)을 결정적으로
추출한다. **ORDER BY 없는 LIMIT은 상위 N이 아니다**(검증기 기본 상한 1000) · 기간 축 컬럼(`month/date/time/ymd/_dt/day/year/hour`)
비교는 임계에서 제외(§1.4 축 분리) · `<>`는 임계 아님. `DependencyVerdict.selection_basis` + detail *"선별 기준: …"* + 노트
`selection_basis` 키. 못 뽑으면 `""`(표기 생략). LLM 0회 · 선별 결과 불변.

**off 관측 로그 대칭(Q6)** — `observe_scope_postcheck`(`prior_dependency.py` · 1단 `deepagents_tools`·2단 `agent_orchestrator`
같은 함수): 플래그 off면 대조만 하고 `사후 대조 관측(off) task=… outside=N missing=M` INFO 로그(결과 불변). `intent_planner.
_enforce_plan_contract`: 재분해 off + 표지 있음 + 순차 배선 없음 → `순차 재분해 관측(off) — 표지 있음·순차 배선 없음(task N건)`
(반환 바이트 동일 — 테스트 고정).

**신규 테스트 16건**: `test_sequential_gate.py`(+12: 추출 정오표 10 · verdict 전파 2) · `test_scope_postcheck.py`(+2: 1단·2단 off
로그 + 결과 불변) · `test_plan_dag_validation.py`(+2: off 로그 1회 · 표지 없음/배선 있음이면 0회).

**회귀(2026-09-10 공유 트리)**: `tests/test_orchestration tests/test_composite tests/test_nodes/test_prior_scope_by_db.py
tests/test_nodes/test_multi_db_group_loop.py tests/test_state.py tests/test_api` → **1150 passed · 1 skipped**(112s) · `arch_check --ci` 위반 0 ·
`overfit_check --ci` 신규 유입 0. `ruff`·`mypy`는 루트 venv에 미설치라 미실행(실측).

**단계 2 실측(2026-09-10 · Gemini 3건 · 사용자 승인 · 1단 `deep_agent` 경로 · 샌드박스 `polestar`@5434 · mcp_server 9099)**

| 건 | 설정 | 결과 | 판정 |
|---|---|---|---|
| 1-a | off · 200% 질의 | 도구 3회 호출 전부 0건(1회차: 검증기 4회 거부 → 0건 · 2회차: `relation "cmm_resource" does not exist` · 3회차: 0건) → `general_inference` 안내문 | **게이트 미도달** — `순차 게이트 관측(off)` 로그 0건 |
| 1-b | GATE=true · 200% 질의 | 도구 2회 호출 전부 0건 → 안내문 | **게이트 미도달** — 미실행 로그 0건 · 경과 블록 없음 |
| 1-c | GATE=true · 정상 질의 | 1회차 실행 0건(`stat_date='202608'`) · 2회차 검증기 4회 거부 → 0건 → 안내문 | **게이트 미도달** |

**끊긴 지점(안쪽 추정이 아니라 로그로 확정 — Known Mistakes "0건 진단은 진입·게이트별로")**

- **B2 샌드박스 데이터 상한**: `cmm_metric_stat_m`의 마지막 `stat_date`가 **202607**(Utilization 12행). "최근 1개월"은 직전 완결 월
  **202608**로 결정적 해석되므로 어떤 SQL이든 **0건이 보장**된다 — 1-c의 긍정 경로(HAVING IN + 경과 블록)는 이 데이터로는 재현 불가.
  샌드박스에서 긍정 경로를 보려면 "2026년 7월"처럼 존재하는 월을 지정해야 한다.
- **B1 스키마 게이팅**: `schema_analyzer`가 LLM 선택 테이블 3개에 `core_config_prop`/`cmm_metric_stat_m`을 보강하지 못해 생성 SQL이
  `존재하지 않는 테이블 참조`로 **4회 연속 거부** → 도구가 0건을 돌려준다(실행 0회). `_supplement_eav_tables`는 수동 프로필의 EAV 쌍을
  읽는데 샌드박스 db_id `polestar`의 프로필 파일이 **없다**(`config/db_profiles/` 실측). 88 범위 밖의 선행 결함.
- **B3 비한정 테이블**: 알람형 SQL이 대문자·비한정 `CMM_RESOURCE`/`CMM_ALARM`(DB2 양식)으로 나와 PostgreSQL에서 실행 실패. 88 범위 밖.
- **★ 88 계약 공백(1단)**: 세 건 모두 후속 도구 호출이 있었는데 게이트가 **한 번도 평가되지 않았다.** 1단 게이트는 `_dependency_scope`
  (행 있는 선행만 후보) 또는 `_referenced_but_empty`(sub_query에 참조어·순위어)로만 발동하는데, 선행이 0건이면 전자가 비고, 후자는
  **오케스트레이터 LLM이 재표현한 sub_query 문구**에 의존한다(예: "CPU 사용률 200% 초과 서버 알람 이벤트 조회"처럼 참조어가 사라짐).
  즉 1단에서는 선행 0건 + 재표현이 겹치면 R-1 게이트가 우회된다. 이번엔 후속도 0건이라 오답은 안 나왔지만 계약은 비어 있다.
  sub_query가 로그에 남지 않아 확정이 안 됐으므로 **관측 로그 추가**: `deepagents 도구 호출 agent=… input_from=… sub_query=…`
  (`deepagents_tools._run_subagent_tool` · INFO). 대안 설계는 §11.6.
- 부수 관측: Gemini `429 Too Many Requests` 재시도 다수(1-b·1-c) — 연속 실행 간격 필요.

**단계 2' 실측(2026-09-10 · Gemini 3건 · 사용자 승인 Q7 · `ENABLE_DEEPAGENTS_PACKAGE=false` → 2단 `intent_orchestration` 확정 로그 확인)**

| 건 | 설정 | 결과 | 판정 |
|---|---|---|---|
| 1-a' | off · 200% 질의 | 분해 t1→t2(`input_from`) · t1 0건(검증기 4회 거부) · 로그 `순차 게이트 관측(off) task=t2 reason=prior_empty` · **t2가 스코프 없이 실행됨**(SQL 주석 "선행 결과의 서버들에 대해 …"인데 HAVING/IN 없음 → `data_insufficient`) · 응답 "조건에 해당하는 서버, CPU 데이터가 없습니다" | ✅ **현행 결함(R-1) 재현** — 샌드박스에 202608 데이터가 없어 0건으로 끝났을 뿐, 운영이면 전체 서버 결과 |
| 1-b' | GATE=true · 200% 질의 | t1 0건 → 로그 `순차 게이트 — task t2 미실행(prior_empty)` → 응답 말미 `## 순차 처리 경과 / - [t2] 선행 작업(t1)의 결과가 0건이라 이 단계를 실행하지 않았습니다.` | ✅ **기대 일치**(S2) |
| 1-c' | GATE=true · 2026년 7월 질의 | t1이 B1(검증기 `cmm_metric_stat_m` 거부 4회)로 0건 → 게이트 발동·경과 블록 렌더(1-b'와 동일) | ⚠ 게이트는 정상, **긍정 경로(HAVING IN·선별 기준 표기)는 B1에 막혀 미검증** |

- **결론**: R-1 계약(게이트·상태·사유·경과 블록)은 2단에서 **실 LLM으로 검증됐다.** 남은 미검증은 ①긍정 경로(S1 — B1 해소 필요)
  ②1단 게이트 발동(§11.6 (c) 대기 · sub_query 로그 수집 중).
- **B1 재확인**: `_get_eav_companion_tables → _load_manual_profile("polestar")` 가 프로필 부재로 빈 값 → EAV 보강 0건 → 생성 SQL의
  `core_config_prop`·`cmm_metric_stat_m` 참조가 매번 거부된다. 샌드박스 db_id에 `config/db_profiles/polestar.yaml`이 없다(실측:
  `polestar_b0`·`polestar_cm_gp`·`polestar_cm_yd`·`test_db`만 존재). **88 범위 밖 선행 결함 — 별도 작업 후보**(프로필 생성 또는
  샌드박스 db_id를 `polestar_cm_gp` 프로필에 연결).

**B1 해소(2026-09-10 · 사용자 확정 Q9 "샌드박스 프로필 생성 → 재실행" · 88 범위 밖 선행 결함 — 별도 항목)**

- 실측: `config/db_profiles/polestar.yaml`은 **두 번 삭제**됐다 — ①`ac2cdc8`(2026-07-01 · D-054 레거시 도메인 폐기 — 근거 있음)
  ②`52ceb0a`(2026-07-16 · D-086)는 결정문에 *"db_profiles 4종(gp/yd/b0/polestar) few-shot 예시"* 갱신을 적으면서 이 파일을
  570줄 통째로 지웠고, 그 삭제를 근거로 삼는 결정은 없다. D-054는 D-076 후속3(2026-07-14 · 샌드박스 편입 · `polestar.yaml` 재등재)이
  뒤집었으므로 복원은 결정과 충돌하지 않는다(D-161 4항: `.env` 등록 `POLESTAR_CONNECTION` 실측 · 도커 `polestar_pg` 기동 중 ·
  `git log` 최종 삭제 커밋 현 브랜치 소속 확인 · 역방향 소비처 `schema_analyzer._load_manual_profile`·`field_mapper:394`).
- 조치: D-076 후속3과 같은 방법 — `polestar_cm_gp.yaml` **현행본**(D-086 이후 드리프트 포함)을 복제하고 헤더 + "공동존 폴스타"→
  "폴스타(로컬 샌드박스)" 13곳만 치환(635줄). 옛 버전(07-14) 복원을 택하지 않은 이유: 옛 버전은 `platform.server%` LIKE·MAX(stat_date)
  안내 등 이후 cm_gp에서 수정된 내용을 담고 있다(diff 210줄).
- 검증: `_load_manual_profile("polestar")` → source=manual · EAV 쌍 `(cmm_resource, core_config_prop)` · `allowed_tables`에 통계
  3종 포함 · `catalog_diff --db polestar --ci` **차이 0**(`knowledge/polestar/catalog.yaml`의 `structure_from: polestar_cm_gp`가
  그대로라 시맨틱 모델 파생은 불변) · `overfit_check --ci` 신규 유입 0.

**단계 2'' 실측(2026-09-10 · Gemini 4건 · 사용자 승인 Q10 · 질의 "CPU 사용률이 높은 서버를 찾아 그 서버들의 2026년 7월 CPU 사용률을 보여줘")**

| 건 | 경로·설정 | 결과 | 판정 |
|---|---|---|---|
| 1-c'' | 2단 · GATE | t1 **54건**(결정적 컴파일 `ORDER BY "cpus_max" DESC NULLS LAST LIMIT 10000` — "높은"에 임계·상위 N 없음 = 전 서버 정렬) → t2 SQL `HAVING MAX(CASE … c.name END) IN (54개 name)` → 54건 · 경과 블록 `[t2] 선행 작업(t1) 결과 54대(name)로 대상을 한정했습니다.` | ✅ **S1 긍정 경로**(HAVING IN 결정적 주입). ⚠ 선별 기준 미표기 — 추출기가 인용 별칭(`"cpus_max"`)을 못 읽음 → **수정**(아래) |
| 2-a' | 2단 · GATE+POSTCHECK | 위와 동일 54→54. 사후 대조 노트 없음 | ✅ 결정적 경로 no-op(S6 정의대로). 참고: 결정적 피벗은 LEFT JOIN이라 통계 없는 서버도 **행은 있고 값이 null** → `missing`(행 부재)이 아니다. "50대 통계 없음"은 응답 본문이 서술 |
| 3-b' | 2단 · +PRIOR_SCOPE_BY_DB | t1 **10건**(같은 질의인데 LLM이 이번엔 `LIMIT 10`형으로 — 비결정성) → t2 10대 한정 · `_source_db` 없음 → 분할 없음 | ✅ 단일 DB에서 켜도 현행 동일(3-b) |
| 1단 정본 | 1단 · GATE | t1 4건 → **둘째 호출 `input_from=[]`** · 새 로그: `sub_query='2026년 7월 전체 일자별 CPU 사용률 상위 서버 상세 통계 (dbora023, cocm-hdkapp01 등)'` → 스코프 미주입·게이트 미평가·경과 블록 없음. t2는 LLM이 알아서 4건을 냈다 | ❌ **1단 계약 우회 재현**(원인 확정 아래) |

**1단 우회 원인(코드로 확정)**: `deepagents_tools._GLOBAL_SCOPE_MARKERS = ("전체", "모든", "전 서버")`가 **부분 문자열**로 검사돼
"전체 **일자별**"(기간 축)이 서버 전역 범위로 오판 → `_dependency_scope`가 G1(식별자 `dbora023` 등 포함)·G3("상위") 평가 **전에**
`([], {})` 반환. 즉 §11.6 (a)의 "참조어 어휘 부족"이 아니라 **차단어의 축 오판**이 1차 원인이다. 부수: 식별자가 hostname 형식인데
t1 식별 컬럼은 `name`(DB-ORA-023)이라 G1도 형식 불일치 가능성 — 차단어가 아니었어도 G3("상위")로는 주입됐을 것이다.

**G-2 추출기 수정(1-c'' 실측 반영)**: ①ORDER BY·비교 좌변의 **인용 식별자**(`"cpus_max"`) 지원 ②"상위 N건" 대신 **"정렬 {컬럼} {방향} ·
상한 {N}건"** — `LIMIT 10000`은 컴파일 기본 상한이라 "상위 10000건 선별"로 부르면 오해(실제는 전 서버 정렬). SQL이 하는 일만 적는다.
테스트 정오표 갱신(34 passed).

**`.env` 반영(2026-09-10)**: Q1 조건(단계 2·3·6 통과) 충족 — 1-b'(게이트) · 1-c''(긍정) · 2-a'(대조 무해) · 3-b'(분할 무해) → 운영 `.env`에
§11.1 블록 + `TEXT2SQL_PATH_PARITY=false` 추가. `.env`는 git 미추적.

**테스트 귀속(2026-09-10 · 전체 스위트 최초 6,154 passed · 28 failed · 5 errors → 고정 후 **최종 6,167 passed · 26 failed · 5 errors — 실패 전부 아래 '기존 결함' 집합, 신규 0** · 클린 기준선 `git worktree`(HEAD + 내 블록 뺀 `.env` 사본)로 대조)**

| 분류 | 건수 | 처리 |
|---|---|---|
| 운영 `.env` 1차 3종 on → off 전제 테스트 누수 | 7 | 플래그 명시 고정(`mock_config.composite.* = False` 5건 · `by_db_off` 픽스처 2건) — CLAUDE.md Known Mistakes 원칙 |
| 샌드박스 프로필 복원 → 프로필 집합·시드 개수 변화 | 2 | `test_query_history_seed`: 프로필 db_id 집합에 `polestar` 추가 · 합계 55→**69**(골드 26 + 프로필 43 실측). 골드셋 db_id 집합(3종)은 불변 |
| `.encenv`(미추적) 키 누수 — 기준선 worktree엔 파일이 없어 통과 | 1 | `test_gemini_api_key_default_empty` — 환경 차이, 내 변경 아님(기존) |
| 기준선에서도 실패 — 기존 결함(DB·LLM·Redis 의존 · `polestar_pg.yaml` 부재 5 errors · 스냅샷 드리프트 등) | 27 | 손대지 않음(88 범위 밖) — 목록은 `tests/test_plan33_join_prevention`·`noise_gate/tests/test_alarm_*`·`test_e2e_polestar`·`test_prompt_render_matrix`·`test_schema_cache`·`test_xls_plan_integration` 등 |

### 11.6 1단 게이트 발동 조건 — (d) 확정·구현(2026-09-10 · Q11)

> **구현**: `deepagents_tools._GLOBAL_SCOPE_MARKERS`(부분 문자열 3종) → `_GLOBAL_SCOPE_RE` + `_is_global_scope()`(대상 명사 동반 시만 차단) ·
> 호출부 2곳(`_dependency_scope`·`_referenced_but_empty`) 교체. 테스트: 정오표 9건(실측 sub_query·"모든 월"·"전체 기간"은 비차단, "전체 서버"·
> "모든 장비"·"전 서버"·"전체호스트"는 차단) + `_dependency_scope`가 실측 sub_query에서 G3("상위")로 주입 + 서버 축 전역은 여전히 미주입.
> 관련 스위트 135 passed. **1단 실 재검증(Gemini 1건)은 별도 승인.** (b) 최상위 질의 표지 규칙은 미채택(t1 재시도 차단 위험).

**1단 실 재검증(2026-09-10 · Gemini 1건 · Q12 승인 · 운영 `.env` 반영 상태 = GATE·POSTCHECK·BY_DB on)** — ✅ **성공**

| 호출 | sub_query(오케스트레이터 재표현 · 새 로그) | input_from | 결과 |
|---|---|---|---|
| 1 | (t1) | `[]` | 54건 |
| 2 | "2026년 7월 서버별 CPU 사용률 통계 상위 목록 및 상세 수치 조회" | `['tool_data_query_1']` (G3 "상위") | 54건 · 경과 노트 + **선별 기준: 정렬 cpus_avg 내림차순 · 상한 1000건**(G-2 1단 렌더) |
| 3 | "… 평균 CPU 사용률 높은 순 상위 10개 서버와 사용률 조회" | `[1, 2]` | 10건 · 사후 대조 **missing 44대** 노트(운영 `.env` POSTCHECK on) |
| 4 | "2026년 7월 svr-web-10, svr-app-03, svr-bkp-01, …"(식별자 열거 · G1) | `[1, 2, 3]` | 54건 |

- 종전(차단어 부분 문자열)엔 둘째 호출이 `input_from=[]`였다(§11.5 단계 2''). 수정 후 같은 질의에서 주입·게이트·경과 블록·선별 기준·
  사후 대조가 1단 정본 경로에서 전부 동작한다. 비결정성 주의: 이번 재표현엔 "전체"가 없었으므로 차단어 수정의 직접 효과는 정오표
  테스트(실측 sub_query 고정)가 보증하고, 이 실행은 1단 계약 전체의 실효를 보인다.
- **부수 관측(R-E 실측)**: 1단은 성공한 선행 결과를 **전부** `input_from`에 누적하고 스코프는 **합집합**이라(§9 R-E 결정), 4번째 호출이
  10대(3번째 결과)를 열거했는데도 스코프가 54대(1·2번째 합집합)로 넓어져 54건을 돌려줬다. 후속이 직전 결과만 가리키는 경우 합집합이
  의도보다 넓다. 오답은 아니지만(모두 선별 집합 안) "가장 최근 선별로 좁히기"는 별도 결정 후보 — 본 회차 범위 밖.


| 안 | 내용 | 장점 | 위험 |
|---|---|---|---|
| (a) 현행 유지 + 관측 로그 | 참조어·순위어·값 일치에만 의존. 이번 추가 로그로 sub_query 문구를 모아 표지 어휘를 보강 | 오탐 없음 | LLM 재표현에 따라 R-1 우회 지속 |
| **(d) 차단어 축 한정**(단계 2'' 실측 후 신설) | `_GLOBAL_SCOPE_MARKERS` 부분 문자열 검사 → **대상 명사 동반**일 때만 차단(`(전체|모든|전)\s*(서버|장비|호스트|대상|리소스)`) · "전체 일자별/전체 기간/모든 월" 같은 기간·컬럼 축은 차단 아님 | 결정적 · 1단 우회의 확정 원인 제거 · G1/G3 기존 규칙 그대로 | "전체"만 단독으로 쓴 sub_query("전체 조회")는 차단에서 빠짐 → 그 경우 G2/G3가 없으면 어차피 미주입이라 실질 위험 낮음 |
| (b) 최상위 질의 표지 규칙 추가 | `has_sequential_marker(user_query)` **AND** collector에 생산자 결과가 있고 전부 0건/실패 **AND** 이번 호출이 data_query면 게이트 | LLM 재표현과 무관(결정적) | 오케스트레이터가 **t1을 다른 표현으로 재시도**하는 호출(이번 1-a의 2·3회차가 그 형태)을 t2로 오인해 차단 — 재시도 자체를 막는다 |
| (c) 2단 강등 검증으로 계약 확인 후 (b) 판단 | `ENABLE_DEEPAGENTS_PACKAGE=false`로 2단(intent_orchestration) 재실행 — 분해가 `input_from`을 결정적으로 배선하므로 게이트 발동은 LLM 문구와 무관 | R-1 계약 자체는 2단에서 즉시 검증 가능 | 운영 정본은 1단이라 1단 공백은 남는다 |

---

## 개정 이력

- v1(2026-09-09) — 최초 작성. 실측 7건(R-1~R-7) · 경계 4건 · 예시 질의 양 경로 추적 · 설계 6절 · Wave 5 · 게이트 4건 ·
  D-203 예약(채번 표 등재).
- v2(2026-09-09) — **사용자 지시로 「경계」 4건을 범위에 편입**(§2 → E-1~E-4). 추가 실측 3건: `sufficiency_shortfalls`
  소비처 0건(죽은 채널) · `_extract_identity_rows`가 `_source_db`를 버려 DB별 분할 불가 · backend `none` 분해 경로 DAG
  검증 부재(순환·미존재 참조가 조용히 병렬화). 설계 §4.7~§4.10 · Wave W6~W9 · 시나리오 S9~S12 · 게이트 G-5~G-7 ·
  위험 R-H~R-K · D-203 초안 ⑤~⑧. HITL 승인 게이트 우회 위험(3·4단)을 진입 조건으로 차단.
- v3(2026-09-09) — **1차 구현 완료 · D-203 본문 등재**(`/spec-driven-development` — 지도 → SPEC 4건 → plan/todo → TDD).
  구현: `src/utils/prior_dependency.py`(신규 · 판정·대조·렌더·노트) · `agent_orchestrator._gate_level`/`deepagents_tools`
  게이트+사후 대조(같은 함수) · `AgentState.dependency_notes` + `result_aggregator._apply_dependency_notes`(4개 반환 지점
  단일 통과점) + `QueryResponse.dependency_notes` · `collect_prior_identity_values_by_db`/`filter_prior_rows_for_db`/
  `_MultiRun.prior_scope_by_db`/`_prior_for_db` · `_extract_identity_rows` `_source_db` 패스스루 · UI "건너뜀" 배지 ·
  설정 도움말 3건. 실측 정정: `sufficiency_shortfalls`가 응답에 안 닿은 원인은 **AgentState 미선언 → LangGraph가 노드
  반환 시 폐기**(state.py 주석의 D-187 선례와 동일). 옆길: 테스트 대역(SimpleNamespace) 설정·run에서 새 속성 접근이
  두 번 깨져 `getattr`+`isinstance` 방어 읽기로 정정. 잔여 2차는 §8 게이트 답 이후.
- v4(2026-09-09) — **2차 구현 완료(재개 지시 · §8 권고안 가정: G-5 본 계획이 배선 · G-6 (a) 도입 · G-7 표기만 · G-4 불필요 판정 · G-3 미변경)**.
  W8 `validate_plan_dag`(`schemas.py` · 중복 id·미존재 참조·`input_from⊄depends_on` 자동 보정·순환) + `_enforce_plan_contract`
  (`intent_planner._llm_decompose` — 되먹임 1회, 시스템 접두 불변, 재위반 시 단일 폴백+`degraded`) · W2 `has_sequential_marker`
  (좁은 표지 — "찾아줘"는 표지 아님) + 표지 단일 계획 재분해 1회(예산은 W8과 공유 = 총 LLM ≤2) + `sequential_not_applied` 노트 ·
  `intent_planner` 노드가 `degraded`를 `dependency_notes(kind=decompose)`로 승격(종전엔 노드에서 버려짐) · W9 `src/orchestration/
  sequential_runner.py`(2단 부품 `_llm_decompose→agent_orchestrator→result_aggregator` 재사용 · `sequential_entry` 진입 4조건 ·
  **HITL 승인 플래그 on이면 미진입** · 3단 `route_after_semantic_router_sequential` · 4단 `route_after_field_mapper_legacy` ·
  1·2단 빌드 미등록) · W4 `testdata/routing_gold/decomposition.yaml`(순차 3 + 단일 오탐 2) + `eval_routing.py --decomposition`
  (task 수·input_from 엣지·agent·단일 오탐 채점 · dry-run/mock/D-127 게이트 동일). 신규 테스트 2파일 31건 · 관련 스위트 792 passed ·
  arch/overfit 0. **병합 사고**: 작업 중 사용자 `stash pop`이 원격 6커밋과 충돌(6파일 UU) → 마커 해소(query.py는 plans/89 쪽 채택) ·
  원격이 D-198~D-202를 선점해 **D-198→D-203 재부여**(plans/89·90도 D-204·D-205로 — 병행 세션 collectorinfra-99가 마무리).
- v5(2026-09-09~10) — **인터뷰로 §8 게이트 전건 확정**(`interview-me` · Q1~Q6 · §11.1 표). §11 신설: 플래그 6종 단계별 테스트 절차(단위·dry-run·mock은 과금 0, 실 실행 16건은 D-127 단계별 승인) · 실효화 결정 **"1차 3종 on · 2차 3종 off · `PATH_PARITY=false`를 운영 `.env`에 명시"**(코드 기본값 불변) · G-1·G-4~G-7 가정 5건 확정 · G-3 on은 82 2차로 이관 · 관측 카운터는 로그로 대체. 구현: **G-2 선별 기준 결정적 추출**(`extract_selection_basis`) + **off 관측 로그 대칭**(사후 대조·재분해). 단계 1 실측(§11.5): 단위 89 passed · dry-run OK · mock은 채점 경로만(목업이 분해 JSON을 내지 않음). 실측 정정: §5 관측 카운터는 미구현, 운영 `.env`에 6종·`PATH_PARITY` 전부 부재.
