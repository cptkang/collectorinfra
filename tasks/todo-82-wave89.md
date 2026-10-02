# 태스크 목록 — Plan 82 Wave 8·9

> 계획: `tasks/plan-82-wave89.md` · SPEC: `SPEC-empty-answer-diagnosis.md` · `SPEC-spike-condition.md`
> 기준선(2026-08-28): 대상 영역 **6 failed · 1523 passed · 1 skipped** (6건은 순수 HEAD와 동일한 기존 실패)
> 판정: **failed 6을 늘리지 않고** 신규 테스트를 더한다.

## W8 — empty-answer-diagnosis

- [x] **T1** 변화 어휘 선언 파일 + 로더
  - Acceptance: `config/change_terms.yaml`에 `spike_terms`(갑자기·급증·급등·치솟·급상승·spike…) · `default_delta_pp: 20` · `default_baseline: month` · `explicit_delta_patterns`. `src/domain/change_terms.py`가 `lru_cache`로 로드하고 **파일 부재 시 빈 규칙**(예외 아님). 정책은 코드 리터럴 0(선언 파일 정본)
  - Verify: `pytest tests/test_empty_answer/test_change_terms.py -q` · `arch_check --ci`
  - Files: `config/change_terms.yaml`, `src/domain/change_terms.py`, `tests/test_empty_answer/test_change_terms.py`

- [x] **T2** 미반영 조건 판정 (G-4)
  - Acceptance: `detect_unexpressed_conditions(user_query, filter_conditions, terms)` — 변화 어휘 있고 대응 조건 없음 → 사유 문구 / 어휘 없음 → 빈 리스트 / 어휘 있고 조건도 있음 → 빈 리스트. 순수 함수
  - Verify: `pytest tests/test_empty_answer/test_diagnosis.py -q -k unexpressed`
  - Files: `src/domain/empty_answer.py`, `tests/test_empty_answer/test_diagnosis.py`

- [x] **T3** 퍼널 조립 + XSS/MFS 판정 + G-5
  - Acceptance: `build_diagnosis(...)` — 단계 카운트에서 **MFS(최초 0)·XSS(마지막 >0)** 판정. 그룹 2개면 **그룹별 끊긴 지점**. `regenerable`은 **P0>0 → False / P0=0 → True**. `render_diagnosis()`가 표 + 끊긴 지점 + 미반영 경고 + 절단 사유를 낸다
  - Verify: `pytest tests/test_empty_answer/test_diagnosis.py -q` · `arch_check --ci`
  - Files: `src/domain/empty_answer.py`, `tests/test_empty_answer/test_diagnosis.py`

- [x] **T4** SQL conjunct 수술 + COUNT 프로브 조립
  - Acceptance: `split_user_conditions(sql)` — WHERE·HAVING **최상위** conjunct만, 서브쿼리 내부 불변, 문자열 리터럴 내 키워드 무시. 사용자 조건 = **지표 값 컬럼/별칭에 대한 수치 비교**(`>=` `>` `<=` `<`), `BETWEEN`·`IS NULL`·문자열 등호 **제외**. `build_probe_sqls(sql, k_max)` — 누적 prefix K개, `SELECT COUNT(*) FROM (…) t` 래핑(`ORDER BY`/`LIMIT`/`FETCH FIRST` 제거 · **별칭 필수**). 사용자 조건 0개 → **빈 리스트**
  - Verify: `pytest tests/test_empty_answer/test_condition_probe.py -q`
  - Files: `src/nodes/condition_probe.py`, `tests/test_empty_answer/test_condition_probe.py`

- [x] **T5** 배선 — 0건 응답 · G-5 · 플래그
  - Acceptance: `Text2SQLConfig`에 `empty_diagnosis_enabled`(기본 **False**) · `empty_diagnosis_max_probes`(기본 5). `state.empty_diagnosis` 추가(요청 스코프 · 기본 None). `result_organizer`가 0건 + 플래그 on일 때 프로브 실행 후 진단 적재 + G-5 판정. `output_generator._generate_empty_result_response`가 진단이 있으면 렌더, **없으면 현행 문구 바이트 동일**. 프로브 예외는 사유와 함께 강등
  - Verify: `pytest tests/test_empty_answer -q` · `pytest tests/test_nodes/test_output_generator.py tests/test_nodes/test_result_organizer.py -q` · `arch_check --ci`
  - Files: `src/config.py`, `src/state.py`, `src/nodes/result_organizer.py`, `src/nodes/output_generator.py`, `tests/test_empty_answer/test_wiring.py`

## W9 — spike-condition

- [x] **T6** 기간 쌍 해석
  - Acceptance: `resolve_comparison_periods(user_query, today=None)` — "1달 전 대비"/"전월 대비"/"지난달 대비" → `(직전월-1, 직전월)`. **주 단위 표현**("1주일 전"·"지난주 대비") → `BlockedComparison(reason=...)`(보존기간 미확인 · 월 단위 대체 제안). 비교 표현 없음 → None. 기존 `resolve_stat_month_range` **무변경**
  - Verify: `pytest tests/test_spike/test_comparison_periods.py -q` · `pytest tests/test_utils -q`
  - Files: `src/utils/query_gen_common.py`, `tests/test_spike/test_comparison_periods.py`

- [x] **T7** 급증 의도·임계 해석
  - Acceptance: `resolve_spike_request(user_query, terms)` — 급증 어휘 매칭 시 `SpikeRequest(delta_pp, delta_source)`. 명시 수치("30%p 이상 상승"·"30% 이상 올라") 우선, 미명시면 **기본 20 + `delta_source="default"`**(응답 노출용). 어휘 없으면 None
  - Verify: `pytest tests/test_spike/test_spike_request.py -q`
  - Files: `src/domain/change_terms.py`, `tests/test_spike/test_spike_request.py`

- [x] **T8** 비교 SQL 결정적 조립 (엔진 분기)
  - Acceptance: `build_spike_sql(...)` — PG: `::numeric` · `LIMIT n` · 소문자 스키마. DB2: 집계 **내부** `CAST(… AS DOUBLE)` · `FETCH FIRST n ROWS ONLY` · **`POLESTAR.`** 대문자 · `::numeric` 부재. 공통: `GROUP BY … r.name`(**파일시스템 단위 유지**) · `HAVING` 2항(차분 AND 절대 임계) · 가드 `BETWEEN 0 AND 1000` · `dtime IS NULL`
  - Verify: `pytest tests/test_spike/test_spike_sql.py -q`
  - Files: `src/db_adapters/polestar/spike_sql.py`, `tests/test_spike/test_spike_sql.py`
  - 판정 경계: 5→10% 배제 / 75→85% 포함 / 85→90% 배제 / 60→85% 포함 (SQL의 HAVING으로 표현됨을 단언)

- [x] **T9** 배선 — `_try_deterministic` + 한계 표기
  - Acceptance: `Text2SQLConfig`에 `spike_condition_enabled`(기본 **False**) · `spike_default_delta_pp`(기본 20). `_try_deterministic`이 폼필 **다음** 순위로 급증 조립 시도(폼필 있으면 미진입 · 재시도 턴 미진입 · 플래그 off면 **반환 None**). 한계 3건(용량 변경 미대조 · 기본 임계값 · 주 단위 차단)을 state에 적재해 응답에 표기. `build_stat_month_block` **미주입** 단언
  - Verify: `pytest tests/test_spike -q` · `pytest tests/test_nodes/test_query_generator_mapping.py -q` · `arch_check --ci`
  - Files: `src/config.py`, `src/nodes/query_generator.py`, `src/state.py`, `tests/test_spike/test_wiring.py`

## 마감

- [x] **T10** 전체 검증 + 문서
  - Acceptance: 대상 영역 **6 failed 유지**(기존분만) + 신규 전량 통과. `overfit_check` 0. `arch_check --ci` 0. `docs/02_decision.md`에 **D-176 후속1·후속2 등재**. `plans/82` 상태·`plans/INDEX.md`·`CAPABILITY-MAP` 갱신
  - Verify: 위 명령 전부
  - Files: `docs/02_decision.md`, `plans/82-*.md`, `plans/INDEX.md`, `CAPABILITY-MAP-execution-groups.md`

## 완료 실측 (2026-08-28)

| 항목 | 결과 |
|---|---|
| 대상 영역(`test_nodes`·`test_utils`·`test_db_adapters`·`test_middleware`·`test_orchestration`·`test_semantic_routing` + 신규 2) | **6 failed · 1671 passed · 1 skipped** — 실패 6건은 착수 전 기준선과 **동일한 기존 실패** |
| 신규 테스트 | **130건 전량 통과**(`test_empty_answer` 48 · `test_spike` 82) |
| `arch_check --ci` | exit 0 |
| `overfit_check --ci` | 신규 유입 0 |

**범위 밖 기존 실패(내 변경과 무관 — 격리 worktree 실측)**: `tests/test_api/test_settings_catalog.py`
(카운터 251/19 단언)·`test_routes.py` 6건·`test_config_env_reload.py` 1건은 **순수 HEAD에서 10건 실패**로
재현된다(공유 트리 9건 — `.env` 갱신으로 1건 감소). 설정 카탈로그 카운터는 동시 작업(`plans/81`·`plans/83`)이
그룹 5개·필드 다수를 추가해 이미 깨져 있었고, 내 신규 필드 4개는 델타의 일부일 뿐이다 —
**공유 카운터는 전면 재생성하지 않는다**(자기 델타만으로는 등가성을 만들 수 없다).
