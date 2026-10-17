# 185. 단일 DB 폼필 사용률 회수 — 멀티 경로에 맞춤 (182 J1)

> **작성일**: 2026-10-17 · **v1.0**
> **상태**: **완료**(2026-10-17 · 커밋 전) — 단일 폼필이 `cmm_metric_stat` 칼럼 매핑 사용률 필드를 회수(멀티와 같은 공통 함수 `select_metric_recovery_fields`) · W2: 병합 마감이 결과 키 매핑(`build_resolved_mapping`)으로 양식을 채워 공유 칼럼 오채움 해소(사용자 결정) · 멀티 산출 비트 동일 · 기존 단언 수정 0(verifier 자기 테스트 1건 단언 갱신) · 계획 끝 회귀 실패 0(모듈 단위) · verifier Critical·Major 0
> **상위**: `plans/182` §1.4 **J1** · 출처 `plans/169` §(5b) 「행동 변경 후보」. 사용자 결정(2026-10-17): 「멀티에 맞춤」 — 단일 DB 양식에서도 사용률 칸이 채워지게(D-066 대칭).
> **성격**: 행동 변경(D-337 ⑥ — 구조 리팩터와 분리된 별도 계획) · 사용자 산출(양식) 변경 → 산출 대조 동반.
> **계획 시작 SHA**: `13dc5a3f`(RCA · 작업 트리 깨끗 — 루트 `Claude outputs/` 무관)
> **회귀 대상 파일(누적)**: `src/db_adapters/polestar/form_fill_plan.py` · `src/nodes/query_generator.py` · `src/nodes/multi_db_executor.py` · `src/orchestration/result_aggregator.py`(W2) · 새 `tests/test_nodes/test_plan185_single_metric_recovery.py` · verifier 새 테스트 `tests/test_nodes/test_plan185_verify.py` *(누적 끝 — 계획 끝 회귀 1회: §4)*
> **관련 결정**: D-066(단일/멀티 동등화 · 후속 RC2 — `cmm_metric_stat` 매핑은 강제 SELECT에서 빼고 피벗으로) · D-149(폼필 결정적 계약 · llm_inferred 사용률 회수) · D-151(답변 오버라이드 우선) · D-308(사용률 정본 = 관측 DB) · D-337 ⑥
> **관련 실수**: `docs/18` 2026-09-23 「한 경로만 보고 경로 비대칭으로 단정」 — 두 경로를 같은 입력으로 실측한 뒤에만 비대칭을 적는다.

---

## 1. W0 실측 (13dc5a3f)

두 경로 모두 공유 조립기 `build_form_fill_pivot_sql(..., metric_fields=…)`에 사용률 회수 대상을 넘긴다. 다른 것은 **회수 대상 집합을 고르는 규칙**뿐이다.

| | 단일 `src/nodes/query_generator.py` `_try_build_form_fill_pivot_sql` | 멀티 `src/nodes/multi_db_executor.py` `_multi_resource_pivot_result` |
|---|---|---|
| 매핑 분리 | `:401` `split_pivot_mapping(column_mapping, exclude_fields=concat_fields)` → 세 번째 묶음 `_metric_entries` **버림** | `_split_mapping_for_prompt` → `split_pivot_mapping` → `metric_entries` 인자로 전달 |
| 후보 ① 미매핑 | `:435-440` `column_mapping`에서 값이 `None`인 필드 | 전역 `unmapped_fields` |
| 후보 ② `cmm_metric_stat` 칼럼 매핑 | **없음** | `metric_entries`의 필드(`:2256-2259`) |
| 후보 ③ llm_inferred 강등(D-149) | ①에 포함(`drop_llm_inferred_mappings`가 매핑을 제자리 `None`으로) | `dropped_inferred` 별도(중복 제거) |
| 제외 | 월 시리즈 필드 · 결합(Vendor+Model) 필드 · 적용된 답변 오버라이드 | 같음(단 ②는 월 시리즈만 제외 — 결합 필드는 제조사·모델류라 판정에서 사용률이 아니므로 실효 차이 없음 — 공통화 뒤 미세 차이는 §4) |
| 판정 | `classify_metric_field(f, db_id, adapter_db_ids)` | 같은 함수(`_metric_field_predicate`) |

**차이**: field_mapper(유사어·힌트 출처)가 사용률 필드를 `cmm_metric_stat_[h|d|m].<칼럼>`에 매핑하면 — 단일은 그 필드를 정규 SELECT에서도(RC2) 회수 대상에서도 빼 **빈칸**, 멀티는 필드명 기반 피벗으로 **회수**한다. 미매핑·llm_inferred 강등 사용률 필드는 양쪽 다 회수한다.

기존 특성화 스냅샷 `tests/snapshots/plan166_w0/form_fill_pivot_inputs.json`(4케이스)에는 `cmm_metric_stat` 칼럼 매핑 케이스가 없다 — 4케이스 모두 단일·멀티 `metric_fields`가 이미 같다(`identical: true`). 이번 변경으로 이 스냅샷은 바뀌지 않아야 한다(바뀌면 의도 밖 변경).

## 2. 바꿀 규칙 (W1)

- 단일 경로의 회수 대상 = 미매핑(`None`) ∪ **`cmm_metric_stat` 칼럼 매핑 필드** — 제외(월·결합·적용 오버라이드)와 판정은 그대로.
- 회수 대상 선택을 `src/db_adapters/polestar/form_fill_plan.py`의 공통 함수 하나로 모아 두 경로가 함께 부른다(SSOT · plans/169 N1(a)와 같은 자리). 멀티 산출은 기존 테스트 기준 불변.
- 단일 SQL은 `cmm_metric_stat` 칼럼 매핑이 **있을 때만** 바뀐다(그 필드가 피벗 지표 칼럼으로 추가) — 없으면 비트 동일.

## 3. 수용 기준

1. 단일 폼필에서 `cmm_metric_stat` 매핑 사용률 필드가 `metric_fields`에 든다 · 같은 입력에서 단일·멀티 `metric_fields` 집합이 같다(새 테스트).
2. `test_plan166_w0_form_fill_pivot_inputs.py` 스냅샷 무변경 · 기존 폼필 테스트 통과(옛 빈칸 단언만 고치고 목록을 §5에 기록).
3. 산출 대조: 대표 폼필 입력으로 전후 SQL·양식 채움(가짜 결과 행 → `excel_writer`)을 비교해 바뀐 칸이 의도한 사용률 칸뿐.
4. 매뉴얼(D-255): 해당 서술 유무 확인 → 갱신 또는 「해당 없음」 근거.
5. 계획 끝 회귀 1회 `regress.py --base 13dc5a3f` 종료 코드 0(원래 실패만) · verifier Critical 0.

## 4. 진행 (W1 · 2026-10-17)

**구현**: `form_fill_plan.py`에 `select_metric_recovery_fields(*candidate_groups, exclude, is_metric) -> list[str]`(후보 묶음 순서대로 · 제외 집합 밖 · 사용률 판정 · 첫 등장 순서 중복 제거). 단일은 후보 = 매핑 None 필드 + `split_pivot_mapping` 성능지표 묶음(더는 버리지 않음), 멀티는 세 갈래 수집을 같은 함수 호출 하나로 바꿨다. 제외(월·결합·적용 오버라이드)·판정(`classify_metric_field`)은 그대로.

**멀티 불변**: verifier 행렬 290케이스(통계 매핑 없음·하나·공유 칼럼·비사용률 오매핑 × 미매핑 × synonym/llm_inferred × 결합 × 오버라이드 없음/blank/literal × PG/DB2 + 월 시리즈)에서 멀티 `metric_fields`·SQL sha·`mapping_updates` 비트 동일. 공통 함수로 멀티에 생긴 규칙 차이 둘 — 결합 필드 제외가 통계 묶음에도 걸림 · 묶음 간 중복 제거 — 은 옛 동작이 중복 alias를 만들던 입력에서만 갈리고 실 도달은 사실상 없다(`test_plan185_verify.py::test_multi_rule_intentional_differences`로 고정).

**산출 대조**(가짜 결과 행 → `excel_writer` · 기준 `13dc5a3f` worktree · scratchpad `plan185/diff_report.txt`): 단일 SQL은 통계 칼럼 매핑이 없으면 비트 동일(72케이스). 바뀐 칸은 통계 칼럼에 매핑된 사용률 칸뿐(빈칸 → 자기 값) · 비사용률 필드(`비고`)를 통계 칼럼에 매핑해도 변화 없음 · 월 시리즈 필드는 제외 · 유사어 시드형(「CPU 사용률」 → `avg_val`)은 집계어가 없어 사용률로 판정되지 않아 변화 없음. 단일=멀티 집합 불일치 50 → 0.

**회귀**: `regress.py --base 13dc5a3f --files <위 4개>` rc=0 · 통과 2,315 · 실패 0 · 건너뜀 10 · arch/overfit/config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행`. verifier 테스트 포함 15 passed · 1 xfailed.

**매뉴얼(D-255)**: 해당 없음 — 사용 절차·화면 불변이고 매뉴얼에 단일 DB 사용률 칸 빈칸 서술이 없다(`grep -nE '단일 DB.{0,60}(양식|칸)|(빈칸|비어).{0,30}사용률' scripts/manual/content/*.md src/static/manual/*.html` 0건 · `user.md:1158` 월별 사용률 열은 월 시리즈라 회수 제외).

**W1 verifier Major → W2에서 해소(사용자 결정 2026-10-17 「커밋 전에 막는다」)**: 병합 마감 `src/orchestration/result_aggregator.py` `_finalize_merged_rows`가 `resolved_mapping` 없이 top-level 원본 `column_mapping`으로 양식을 채워, 두 사용률 필드가 같은 통계 칼럼(예: `…cmm_metric_stat_m.avg_val`)을 공유하면 writer 역매핑(`excel_writer.py` `{칼럼: 필드}`)이 마지막 필드로 덮여 CPU 칸에 메모리 값이 들어갔다(멀티는 종전부터, 단일은 W1 뒤). 매핑 None화 안(FIX-25 패턴)은 병합 경로가 서브 state `mapping_updates`를 승격하지 않아 막지 못해 비채택.

## 4b. W2 — 병합 마감 결과 키 매핑 (2026-10-17)

- **구현**: `result_aggregator.py`에 `_merged_resolved_mapping(column_mapping, merged_rows)` — top-level 매핑과 병합 행이 모두 있으면 그래프 경로 result_organizer와 같은 Layer 1 `build_resolved_mapping`(결과 키 = 병합 행 키 합집합 — `_merged_rows_in_order`가 모든 행을 같은 키로 펼쳐 첫 행 키와 같다)을 organized `resolved_mapping`에 싣는다. 없으면 None(종전). Layer 2(LLM)는 부르지 않는다 — 못 푼 필드는 원본 값 유지라 writer 종전 폴백.
- **호출부**: 2단 `graph.py:586-591`·계획 루프 `graph.py:701`·1단 `deep_agent.py:489-490` → `result_aggregator.py:190`(합성 + task 2개 이상) → `_finalize_merged_path` → `_finalize_merged_rows`. 다른 마감(`_merge_finalized`·`_merge_with_synthesis_skipped`·`_finalize_steps`·`_synthesize_finalized`)은 task별 `_finalize_task`가 res의 organized(resolved 포함)로 만든 파일을 써 원본 매핑 채움이 없다. `organized["resolved_mapping"]` 소비처는 `output_generator.py:2187`(문서 채움) 한 곳.
- **전후 대조(Excel)**: 직접 칼럼 · EAV 속성 키 · EAV 필드명 alias · 매핑 None · 공유 일반 칼럼(두 필드 → `cmm_resource.hostname`) · `name→server_name` · camel 키 · 미해결 — 8경우 셀 동일. 공유 통계 칼럼만 CPU 칸 77(메모리 값) → 12(자기 값).
- **결정 경위**: W1 verifier Major를 두고 팀 리드가 writer 1곳 축소안(공유 칼럼일 때 필드명 alias 우선 · `excel_writer.py`)을 권고해 사용자가 승인했으나, 메시지가 엇갈려 그 전에 받은 「`_finalize_merged_rows`에 `build_resolved_mapping` 적용」 지시로 이 안(b')이 이미 구현·회귀·재검증까지 끝나 있었다. 구현 완료 사실을 보고받은 사용자가 (b') 유지로 재결정했다(2026-10-17) — writer 안·걷어내기 지시는 취소, `excel_writer.py` 무변경.
- **Word 병합 마감 변화 — 일반 경로와 같아지는 개선으로 수용(사용자 결정)**: Word 양식 병합 마감은 공유 칼럼 밖에서도 바뀐다 — Word writer `_get_value_from_row`(`word_writer.py:341`)에는 역매핑(필드명 alias) 폴백이 없어 종전 병합 마감은 결과 키가 필드명 alias뿐인 칸이 빈칸이었고, 이제 그래프 경로와 같이 채워진다(빈칸 → 값 · 오값 없음). 되돌리는 비용: `_merged_resolved_mapping`을 공유 칼럼 필드로만 좁히면 된다(수 줄).
- **182 기록**: (b')는 이 계획에서 처리돼 후보에 올리지 않는다. 「멀티를 단일 쪽으로 맞추기」(아래 경계)만 `plans/182` §3.1 J7로 남겼다.
- **테스트**: `test_plan185_verify.py` strict xfail 제거 → 통과 · `test_merged_finalize_passes_no_resolved_mapping` 단언을 새 동작(resolved = Layer 1 dict)으로 갱신 · 추가 — 매핑/행 없음 None 유지 · 비공유 8경우 전후 동일 · 멀티 피벗 공유 통계 칼럼 병합 마감 각자 값 · 매핑 키·alias 키 공존 시 종전과 같은 키 · 공유 통계 칼럼 일반 키 존재 시 종전 동일 · Word 2건. 수정 전 코드(`_merged_resolved_mapping` → None)에서 공유 칼럼·멀티 단언이 값 오류로 실패함을 확인. 185 테스트 30 passed.
- **회귀(계획 끝 1회)**: `regress.py --base 13dc5a3f --files <185 바꾼 6개>` rc=0 · 통과 3,610 · 실패 0 · 건너뜀 11 · arch/overfit/config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행`. 그 뒤 verifier가 `test_plan185_verify.py`에 4건 추가 — 그 파일만 재실행 통과 · ruff 통과.
- **verifier 재검증**: Critical 0 · Major 0 · Minor 1(위 Word 이탈) · 참고 1(`resolve_column_key`가 근사 단계에서 set 순회 — 후보 둘 이상이면 키 선택이 프로세스마다 다를 수 있음 · 그래프 경로에 원래 있던 성질 · 병합 행은 키 집합이 같아 실 도달 드묾).

**경계(verifier Minor)**: per-DB `schema_info`에 통계 테이블이 없으면 멀티는 `filter_mapping_by_schema`가 통계 매핑을 먼저 버려 회수하지 않고, 단일은 회수한다(변경 전엔 둘 다 미회수). 피벗은 필드명 기반이라 단일 값이 옳고, 프로필 4종 모두 통계 테이블을 가져 도달은 스코프 축소·환각 테이블명일 때로 좁다 — 그대로 둔다(멀티를 단일 쪽으로 맞추는 안은 `plans/182` J7 후보).

## 5. 고친 기존 단언

없음 — 옛 동작(단일에서 통계 칼럼 매핑 필드 누락)을 단언하는 기존 테스트가 없었다. `test_query_gen_parity::TestMetricMappingDeferredToExamples`는 LLM 프롬프트 경로(RC2)만 단언해 영향 없음. 스냅샷(`tests/snapshots/plan166_w0/`) 무변경. 새 테스트 `test_plan185_single_metric_recovery.py`의 행동 테스트 4건은 옛 코드(`13dc5a3f`)에서 실패함을 확인했다(`…recovered_once` 1건은 옛 코드도 통과하는 회귀 가드).

---

- 2026-10-17 v1.3 — 결정 경위(writer 축소안 승인 → (b') 유지 재결정) · Word 변화 수용 · 182 J7 후보 기록
- 2026-10-17 v1.2 — W2 병합 마감 결과 키 매핑(사용자 결정) · 회귀·재검증 완료
- 2026-10-17 v1.1 — W1 구현·검증·회귀 완료 · 상태 완료 · 파일명 `WIP` 태그 제거
- 2026-10-17 v1.0 — 작성 · W0 실측
