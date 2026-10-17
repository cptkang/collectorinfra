# 181. `OutputState` TypedDict 선언 · 노드 반환 키 정합 테스트 일반화 · `create_initial_state` 초기값 표 데이터화 (166 단계 7e · 스멜 N6)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — `OutputState`(39키 · 구간 TypedDict 4개 상속 · 비AgentState 8키) · `_build_output_state -> OutputState`(mypy가 키 추가·누락 검출) · `create_initial_state` 초기값 표 `_INITIAL_STATE_DEFAULTS`(136키) · 노드 반환 키 ⊆ 그래프 상태 정합 테스트(전 사다리 단 · 위반 3 고정) · 계획 끝 회귀 실패 0(모듈 단위) · verifier 동등 차분 0 · Critical 0 · §4
> **상위**: `plans/166` §0.1 단계 7 · **계획 ID 7e**(스멜 N6 — §3 N6 행 · §4.1 「TypedDict 선언」 · §5.7 W5 표). 선행 6c(`plans/174` — `src/orchestration/agent_spec.py` `HandlerResult`) 완료. 같은 시간에 7a(`plans/180` — `src/orchestration/apm_query.py`·새 `src/orchestration/apm/`)가 돈다 — 이 계획은 그 파일을 건드리지 않는다.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — **선언만**. 런타임 객체·dict 키·값·순서 불변 · 전면 dataclass/pydantic 전환 미도입 · 스냅샷 갱신 금지 · 기존 테스트 수정 0. 발견한 행동 변경 후보(미선언 키 읽기·반환 등)는 고치지 않고 §5에 기록만.
> **계획 시작 SHA**: `1ad7c161`(RCA) + 단계 6·7 미커밋 변경 위 — 기준 사본 `scratchpad/p181_base/`(2026-10-16 20:21:06 · `.venv`·`build`·`logs`·`.git`·`node_modules` 제외 rsync).
> **회귀 대상 파일(누적)**: `src/state.py` · `src/orchestration/result_aggregator.py` · 새 `tests/test_plan181_output_state.py` · 새 `tests/test_plan181_node_return_keys.py` *(누적 끝 — 계획 끝 회귀 1회: §4.2)* · verifier 새 테스트 `tests/test_plan181_verify.py`
> **관련 결정**: D-337(리팩터 규약) · D-303(회귀) · D-064·D-162(요청 스코프 명시 초기화) · D-186·D-177(허용목록 탈락 계열) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님)
> **관련 실수**: `docs/18` 2026-07-30(신규 state 키 경계 7곳) · 2026-08-26(AgentState 미선언 키 → LangGraph 폐기) · 2026-09-21(멀티 경로 노트 덮어쓰기 — 리듀서 없는 키) · 2026-10-06(출력 노드 게이트가 `_build_output_state` 허용목록에 없는 키를 읽음)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(기준 사본)

- `src/state.py` 819줄 — `AgentState`(86-448 · total) · `create_followup_input`(452-609) · `create_initial_state`(612-819 · 키워드 인자 약 150개를 손 나열한 `AgentState(...)` 호출).
- `src/orchestration/result_aggregator.py` `_build_output_state`(1666) — 반환 `dict[str, Any]` · 구간 함수 4개(`_task_result_inputs`·`_form_inputs`·`_zone_scope_inputs`·`_disclosure_inputs`)를 펼쳐 조립. `AgentState`에 없는 키 8종: `executed_sqls`·`period_sources`·`limit_sources`·`condition_check`·`task_id`·`per_task_finalize`·`all_null_downgrade`·`original_user_query`. 소비처: `src/nodes/output_generator.py`·`src/pipeline/output_notes.py`.
- **mypy가 지금 키 오류를 못 잡는 이유(팀 리드 실측)**: `output_generator(state: AgentState)`가 `state.get("executed_sqls")`를 읽어도 mypy는 TypedDict `.get`의 미선언 리터럴 키에 오류를 내지 않고 `object`를 돌려준다(`mypy src/nodes/output_generator.py` 20건 중 키 관련 0 — 2278행 `object` 인자 오류가 그 흔적). 미선언 키 **반환**은 LangGraph가 조용히 버린다(2026-08-26).
- 기존 키 존재 단언 테스트 5파일(`test_plan67_phase0_core` · `test_form_month_series` · `test_plan121_clarification_needed` · `test_plan121_zone_reentry`) — 개별 키만 본다. 일반화는 새 테스트로 하고 기존 파일은 고치지 않는다.

## 2. Wave

| Wave | 담당 | 내용 |
|---|---|---|
| W1-A | implementer | `OutputState` TypedDict(`src/state.py`) · `_build_output_state` 반환 어노테이션 · `create_initial_state` 초기값 표 데이터화 · 정합 테스트(OutputState 키 = 실제 반환 키) |
| W1-B | implementer | 노드 반환 키 ⊆ 그래프 상태 채널 정합 테스트(새 파일 1개 — 제품 코드 수정 0) |

두 갈래는 파일이 겹치지 않아 병렬로 돈다.

## 3. 수용 기준

1. `OutputState` 키 집합 = `_build_output_state`가 실제로 만드는 키 집합(런타임 단언). 8키 포함.
2. 그래프에 등록되는 노드의 반환 dict 리터럴 키 ⊆ 그 그래프 상태 스키마 키 — 위반이 있으면 테스트 안의 명시 예외 집합으로 고정하고 §5에 기록. 미선언 키를 반환하는 변이 사본에서 FAIL.
3. `create_initial_state` 반환 dict의 키·값·순서가 기준 사본과 동일(여러 입력 조합) · 가변 기본값은 호출마다 새 객체.
4. 바꾼 `src` 파일의 mypy 신규 오류 0 · `regress.py` 종료 0(원래 실패만) · arch_check error 0.

## 4. 결과

### 4.1 구조

| 대상 | 전 | 후 |
|---|---|---|
| `OutputState`(`src/state.py`) | 없음 — `_build_output_state -> dict[str, Any]` | 구간 TypedDict 4개(`OutputTaskResultInputs` 5 · `OutputFormInputs` 15 · `OutputZoneScopeInputs` 2 · `OutputDisclosureInputs` 10) + 자기 키 7 = 39키 · 비AgentState 8키 포함. 구간 함수 4개 반환도 같은 TypedDict로 어노테이션 — `**` 펼침을 mypy가 구간 단위로 검사 |
| `create_initial_state` | `AgentState(...)` 키워드 136개 손 나열 | 모듈 표 `_INITIAL_STATE_DEFAULTS`(순서 = 종전 키워드 순서 · 줄 주석 보존) → `copy.deepcopy` → 인자 21칸 `state.update(...)` → `cast(AgentState, …)`. `create_followup_input` 무변경 |
| 노드 반환 키 정합 | 키별 개별 단언 5파일 | `tests/test_plan181_node_return_keys.py` — `build_graph`를 사다리 4단·옵트인 5종 조합 5벌로 빌드하며 `add_node` 등록을 기록(partial·트레이싱 프록시 벗김 · 메인 `AgentState` + task 서브그래프 `TaskRunState`/`TaskOutcomeState`) → AST로 반환 리터럴 키(지역 이름 첨자·update·setdefault · 같은 모듈 헬퍼 3단계) ⊆ 그래프 스키마. 현재 위반 3건은 `KNOWN_UNDECLARED`로 고정(해소되면 FAIL — 낡은 예외 제거 강제) |

- **mypy 실효 범위(정직하게)**: TypedDict `.get("미선언")`은 mypy가 잡지 않는다(`object` 반환). 이번 계획의 mypy 이득은 `_build_output_state`·구간 함수 반환 리터럴의 **키 추가·누락**이다(verifier 변이 M1~M4 전부 `typeddict-unknown-key`/`typeddict-item` 검출). 두 파일 mypy 오류 건수 87→87(`output_generator` 인자 타입 문구만 `dict[str, Any]`→`OutputState`로 바뀜 — `output_generator(state: AgentState)` 시그니처는 손대지 않음).
- **이탈(런타임 동등 · 정적 검사 일부 후퇴)**: `create_initial_state`가 표에서 키를 만들어 `cast`하므로 **초기값의 값 타입**을 mypy가 더 이상 보지 않는다(verifier M5 — `retry_count="0"`을 기준 사본은 검출 · 현 트리 미검출). 키 집합·인자 칸 덮어쓰기는 테스트(`test_plan181_output_state` · `test_plan181_verify`)가 지킨다. 되돌리는 비용: `create_initial_state` 1함수를 기준 사본으로 복원(테스트 2파일 중 표 참조 단언 2개 삭제).

### 4.2 검증

- **계획 끝 회귀(1회)**: `regress.py --base 1ad7c161 --files <4파일>`(histogram diff) — 본체 통과 4,181 · 실패 0 · 에러 0 · 건너뜀 27 · 실패 귀속 없음 · arch·overfit·config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · `[전체 회귀 권고]` 블록 없음 · `범위: 모듈 단위 — 전체 미실행`. `src/state.py`로 `[웨이브 회귀 필요]`가 났고 단일 Wave라 이 회귀가 그것을 겸한다. 원래 실패 `test_plan160_chart_api…[True]`는 선택 범위 밖.
- **verifier**(기준 사본 `p181_base` 대조 · `scratchpad/p181/verify/`): `create_initial_state` 인자 조합 64 + 동일성 64 · `_build_output_state` 144조합 — 키 순서·값·타입 **차이 0** · 표 원본·다음 호출 오염 없음 · 가변값 공유 0. 정합 테스트 변이 17종 중 14 검출 · **생존 3** — V2 `intent_planner` 본문의 반환 이름 아닌 dict 첨자 대입 · V3 추적 밖 헬퍼 반환(추출기의 문서화된 한계 — 유지) · V17(표의 인자 칸을 `update`에서 빠뜨림 — `_FROM_ARGS`가 `None`과 같아 키 집합 단언 통과)은 verifier 새 테스트 `tests/test_plan181_verify.py`(2함수)로 메움. 추출기 보강 실측(상수 이름 키·제자리 수정 헬퍼·함수 표 루프) — 숨은 위반 추가 0. Critical 0.
- **팀 리드 재실행**: 새 테스트 3파일 13 passed(`-n 4` 병렬 · 5.4초).
- **매뉴얼**: 대상 아님 — 사용자 접점 변경 없음(D-255).

## 5. 후속 · 행동 변경 후보

고치지 않았다(D-337 ⑥ — 동작 변경은 별도 계획).

| 노드 · 키 | 현상 | 평가 |
|---|---|---|
| `agent_orchestrator` · `sufficiency_shortfalls` | 2단 그래프 노드 반환 — `AgentState` 미선언이라 LangGraph가 버린다. `replanner.py:1303`이 `state.get("sufficiency_shortfalls")`로 읽지만 그래프 경로에서는 늘 비어 있다 | 실제 전달은 `dependency_notes`(NOTE_SUFFICIENCY)가 한다 — `state.py:414` 주석이 병기 유지·2027-03-09 폐기를 이미 적어 둠. 사용자 영향 없음. 폐기일에 반환·읽기 둘 다 지우면 예외 해소 |
| `multi_db_executor` · `skipped_dbs` | 2단 서브에이전트는 함수를 직접 불러 반환 dict에서 읽는다(`subagents.py:1404`). 3단·task 서브그래프 경로에서는 버려진다 | 미조회 사유는 `dependency_notes`가 운반 — 3단에서 이 키를 소비하는 곳 없음 |
| `multi_db_executor` · `regen_stops` | 그래프 경로에서 버려진다 — 반환부 주석에 의도 명시(plans/119) | 의도된 폐기 · 사용자 사유는 `db_errors`가 운반 |

- W1-A 대략 grep(미확정 — 일부는 단일 경로 전용으로 의도일 수 있음): `output_generator.py`가 `OutputState` 밖 키를 읽는다 — `active_db_id`·`allowed_db_ids`·`conversation_context`·`csv_sheet_data`·`db_results`·`db_scope_source`·`error_message`·`generated_sql`·`is_multi_db`·`scope_narrowed`·`turn_disclosures` · `output_notes.py` — `active_db_id`·`db_executed_sqls`·`dependency_notes`·`resolved_limit`·`scope_narrowed`. 2단·1단 출구에서는 늘 비어 있다(docs/18 2026-10-06 계열). 키별로 단일 경로 전용인지 확인하는 것은 후속 계획 몫이다.
- 추출기 한계(V2·V3 생존): 반환 이름이 아닌 dict의 첨자 대입 · 다른 모듈 헬퍼 · 4단계 이상 헬퍼 · 제자리 수정 헬퍼 · 루프로 부르는 단계 함수 — 테스트 독스트링에 적었다.
- `output_generator(state: AgentState)`를 `AgentState | OutputState`로 넓히는 것은 범위 밖(`.get` 미선언 키를 mypy가 잡지 않아 이득이 작고, 소비처 수십 곳의 타입 좁히기가 필요).

## 6. 이력

- 2026-10-16 v1.0 — 착수(166 단계 7e).
- 2026-10-16 v1.1 — W1-A·W1-B 병렬 구현 · 계획 끝 회귀 · verifier 1라운드 · 완료(파일명 WIP 태그 제거).
