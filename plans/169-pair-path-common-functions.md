# 169. 쌍 경로 공통 함수 — 폼필 피벗 계획(N1(a)) · 존 역질문 공통 꼬리(O6)

> **작성일**: 2026-10-16 · **v1.1**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — N1(a)·O6 공통화 · N1(b) 보류 6쌍 판정(리터럴 5종 처리 · 5쌍 유지) · 계획 끝 회귀 실패 0(모듈 단위) · verifier Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 5 · **계획 ID 5b**(W3 쌍 경로 공통화 · 5a ∥ 5b → 5c)
> **성격**: 동작 불변 구조 리팩터(D-337) — SQL 바이트·프롬프트 렌더·응답·문구·로그 문구·기본값·플래그 불변 · 단계 1 스냅샷 갱신 금지 · 테스트 단언 변경 금지
> **계획 시작 SHA**: `3a0a1101`(RCA)
> **회귀 대상 파일(누적)**: 11 — `src/nodes/{query_generator,multi_db_executor}.py` · 신규 `src/db_adapters/polestar/form_fill_plan.py` · `scripts/overfit_baseline.json` · `src/orchestration/db_targeting.py` · `src/routing/semantic_router.py` · 신규 `src/routing/zone_clarification.py` · 신규 테스트 `tests/test_nodes/test_plan169_{form_fill_plan,verify}.py` · `tests/test_routing/test_{zone_clarification_common,plan169_verify_zone}.py` *(누적 끝 — 계획 끝 회귀 1회: §4)*
> **관련 결정**: D-337(리팩터 규약 ①~⑦) · D-066·D-067(단일/멀티 동등화 · 드리프트 가드) · D-089(DB별 특화는 `src/db_adapters/{db}/` · 노드의 어댑터 직접 import 허용 · 훅 확대 금지) · D-146~D-151(폼필 규칙) · D-143 후속2·3(존 역질문 후단 게이트) · D-232(조회 권한) · D-251(3단은 비교 arm) · D-303(회귀)
> **적용 스킬**: `.claude/skills/refactoring/`
> **병행**: 5a(`plans/168` · `src/api/routes/query.py` 중심) — 파일 겹침 없음. 5c(I1 · `multi_db_executor.py`의 `_redis_cache` 직접 접근 지점)는 이 계획 뒤 — 그 지점은 건드리지 않는다.

---

## 1. 범위 (166 §3 N1·O6 행 · §5.5 · 줄 번호는 `3a0a1101` 재실측)

| 스멜 | 위치(재실측) | 처방 | 성공 기준 |
|---|---|---|---|
| **N1(a)** | 단일 `src/nodes/query_generator.py:283-540` `_try_build_form_fill_pivot_sql` ↔ 멀티 `src/nodes/multi_db_executor.py:2174-2336` `_metric_field_predicate`·`_multi_resource_pivot_result` · `2373-2420` `_split_mapping_for_prompt` · `2423-2633` `_build_mapping_user_parts` — 조립기 `build_form_fill_pivot_sql`(`src/db_adapters/polestar/assembler.py:1198`)만 공유 | **DRY · Extract Function** — `FormFillPivotPlan`(dataclass)을 돌려주는 공용 순수 함수. 단일/멀티는 매핑 입력 정규화·로그·state 반영·조립기 호출만 남긴다 | W0 피벗 입력 덤프(`tests/test_nodes/test_plan166_w0_form_fill_pivot_inputs.py`) 동일 · gold_smq·폼필 골든 바이트 동일 · `tests/test_prompt_render_matrix.py` 무갱신 · `prompt_render_diff --ci` 0 |
| **O6** | 2단 `src/orchestration/db_targeting.py:93-188` `_zone_clarification_or_none_task`(꼬리 158-188) ↔ 3단 `src/routing/semantic_router.py:712-772` `_zone_clarification_or_none_router`(꼬리 745-772) | **SSOT** — 공통 꼬리(서버 식별 필터 → 조회 대상 파싱 → 전부 폴스타 대상 → 권한 거른 존 → `build_zone_clarification`)만 `zone_clarification_for_targets(...)`로 추출. **앞단 비발동 신호는 현행 유지**(`_has_new_location_db_signal` vs `ZONE_SKIP_SIGNAL_TERMS` — 166 §7 U-1 ①) | 양 단 역질문 테스트 통과(`tests/test_semantic_routing/` · `test_plan119_zone_early.py` · 존 역질문 단언 grep) |

**넘어온 판단(166 §5.3 W1 결과 — N1(b) 6쌍 보류분)**: `_render_samples_secure` · `_unmapped_fields_section` · `_build_stepwise_deps` · `_select_query_history_examples` · `_format_schema*` · `_SCHEMA_EXAMPLE_TABLE`(+ 형제 리터럴 4 `_ENTITY_RESOURCE_TYPE`·`_EAV_HOST_ATTRIBUTE`·`_EAV_LINK_COLUMN`·`_FOREIGN_SCHEMA_PREFIX` — qg:167-171 · multi:176-180). 이 계획에서 판정했다(§4 「N1(b) 보류 6쌍 판정」).

**범위 밖**: 신호 함수 통일(U-1 ②) · plans/69 플래그 OFF 비대칭(`TEXT2SQL_MULTI_FULL_VALIDATION`) · 5c 지점 · 로그 문구 통일(경로별 `DB '%s'` 접두 차이는 유지).

## 2. 설계 제약(팀 리드 실측 · 2026-10-16)

- **patch seam(D-337 ②)**: 테스트가 노드 모듈 네임스페이스에서 바꿔 끼우는 이름 — `build_form_fill_pivot_sql`(W0 덤프 테스트 · `test_plan122_t4_sql_paths.py` 단일·멀티 모두) · `recognize_month_series`(같은 파일) · `get_domain_by_id`(`multi_db_executor` 8곳) · (구현 중 추가 실측) `resolve_stat_month_range`(`test_r3_surface_fallback.py`가 노드 모듈 속성에 직접 대입 — `patch`/`setattr` grep에 안 걸린다). → 공용 함수는 이 셋을 **부르지 않는다**. 호출부가 부르고 결과를 넘기거나, 공용 함수가 조립기 인자(plan)만 만들고 조립기 호출은 호출부에 남긴다.
- **로거 이름**: 로그는 호출부에 남긴다(`%(name)s`·caplog 불변 — 공용 모듈로 옮기면 로거 이름이 바뀐다).
- **계층**: 공용 피벗 계획은 폴스타 스키마 리터럴(`cmm_resource` 기본값·`cmm_metric_stat` 판정·`server.Server`)을 쓴다 → D-089에 따라 `src/db_adapters/polestar/` 아래가 자연스럽다(overfit EXCLUDE 대상 · application 계층 — 노드에서 직접 import 허용). 존 역질문 꼬리는 2단(orchestration)·3단(routing=infrastructure)이 함께 쓰므로 infrastructure 이하에 둔다.
- O6 꼬리 함수에는 테스트 patch seam이 없다(`authorized_db_ids`·`build_zone_clarification`·`has_host_identifier_filter`·`ZONE_CLARIFY_OPTIONS` 0건). 바깥 함수(`_zone_clarification_or_none_task`·`_router`)는 patch되므로 이름·위치 유지.

## 3. 진행

| Wave | 내용 | 담당 | 상태 |
|---|---|---|---|
| W1 | N1(a) 트랙 ∥ O6 트랙(파일 겹침 0) · N1(b) 보류 6쌍 판정 | implementer 2 | 완료 |
| W2 | 검증(verifier) · 계획 끝 회귀 1회 · 기록 | team-lead | 완료 |

## 4. 결과 (2026-10-16 · 시작 `3a0a1101` · 커밋 전)

**N1(a)** — 신규 `src/db_adapters/polestar/form_fill_plan.py`(D-089 어댑터 아래 · 로그·seam 호출 0): 순수 단계 함수 4개 `drop_llm_inferred_mappings`·`month_series_rule_updates`·`resolve_answer_overrides`·`split_pivot_mapping` + 피벗 꼬리 `plan_form_fill_pivot` → `FormFillPivotPlan`(frozen · 스키마 검증·식별 컬럼 주입·month_measures·concat·period · `dropped`). 단일·멀티가 같은 함수 객체를 쓰고, 조립기 `build_form_fill_pivot_sql`·`recognize_month_series`·`get_domain_by_id`·`resolve_stat_month_range` 호출과 로그·state 반영은 각 노드에 남는다. 함수 길이(AST): 단일 `_try_build_form_fill_pivot_sql` 231→197 · 멀티 `_build_mapping_user_parts` 211→194 · `_split_mapping_for_prompt` 48→35 · `_multi_resource_pivot_result` 146→139(두 노드 diff +98/−160 · 공용 모듈 212줄 신규 — 저장소 줄 수는 늘고 같은 로직 사본이 2벌→1벌).

**O6** — 신규 `src/routing/zone_clarification.py` `zone_clarification_for_targets(parsed, targets, query_text, *, allowed_db_ids, user_role, app_config)`(infrastructure). 2단·3단 꼬리 복제(31줄·28줄)를 1벌로. 앞단 비발동 신호는 현행 유지(U-1 ①) · 2단 발동 로그는 `db_targeting`에 남김(로거 `src.orchestration.subagents` 불변).

**N1(b) 보류 6쌍 판정**

| 쌍 | 판정 | 근거 |
|---|---|---|
| 리터럴 5종(`_ENTITY_RESOURCE_TYPE`·`_EAV_HOST_ATTRIBUTE`·`_EAV_LINK_COLUMN`·`_SCHEMA_EXAMPLE_TABLE`·`_FOREIGN_SCHEMA_PREFIX`) | **처리** | 정본을 `form_fill_plan.py`(공개명)에 두고 두 노드가 같은 `_` 이름으로 import · arch 경고 17 유지 · overfit 기준선은 노드 경로 항목 5개 삭제(자기 델타) |
| `_render_samples_secure` | 유지 | 본문 동일이나 모듈 전역 `is_scrub_samples_enabled`를 읽고 테스트 3곳이 노드 네임스페이스에서 patch(D-337 ②) |
| `_unmapped_fields_section` | 유지 | 공용 빌더 위 어댑터 · 멀티만 `hangul_alias=True`(의도된 차이) |
| `_build_stepwise_deps` | 유지 | 시그니처·sync/async·유사어 출처·path 라벨 상이 |
| `_select_query_history_examples` | 유지 | 인자 상이(state vs db_id) · qg 쪽 테스트 patch |
| `_format_schema_for_prompt` ↔ `_format_schema` | 유지 | 단일만 `relationships_header` 한글 지정 · 시그니처 상이 |

**이탈(근거 · 되돌리는 비용)**: ① 「공용 함수 1개가 plan 전체」 대신 단계 함수 4 + 꼬리 plan 1 — 두 경로가 강등→월 인식(seam)→규칙→결합→오버라이드→분할 사이에 게이트·입력 모양·로그를 끼우는 위치가 달라, 한 함수로 묶으려면 플래그 5개 이상과 seam 우회가 필요했다(되돌리는 비용: 노드 2파일 인라인 복원). ② `stat_month` 해석은 공용화하지 않음 — `tests/test_nodes/test_r3_surface_fallback.py`가 `qg.resolve_stat_month_range = fake`·`mdb.resolve_stat_month_range = fake`로 **직접 대입**(254·329·418·452·513행)하는 seam이고 그 폴백 계측 로그가 `dropped` 경고 뒤에 나와야 한다.

**남긴 shim(다음 계획 제거 대상 · D-337 ①)**: `multi_db_executor.split_mapping_entries` 재노출(`# noqa: F401`) — D-067 패리티 가드 `tests/test_nodes/test_query_gen_parity.py:376`이 객체 동일성을 단언해서 남김. 멀티는 더 이상 직접 부르지 않아 그 가드는 멀티 쪽에서 실효가 없다 — 대체 가드 `test_plan169_verify.py::test_both_nodes_share_common_function_objects`. 다음 계획에서 가드 목록에 공용 함수를 넣고 재노출을 걷어낸다.

**행동 변경 후보(기록만 — D-337 ⑥)**: 단일 경로는 `cmm_metric_stat` 칼럼에 매핑된 필드를 metric_fields로 회수하지 않고 버리지만 멀티는 metric_entries로 회수한다(현행 비대칭).

**검증**
- 게이트: `prompt_render_diff --ci` 0 · `catalog_diff --ci` 0 · arch error 0 · 경고 17(유지) · overfit 통과(기준선 자기 델타 5항목 삭제) · config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · import 스모크(`src.graph`·`src.api.server`) · W0 스냅샷·렌더 매트릭스 무갱신.
- verifier: Critical 0 · Major 0 · Minor 4(멀티 주석 위치 — 반영 · `split_mapping_entries` 가드 실효 — 위 shim으로 기록 · 단일 `metric_period` 2회 평가(같은 객체 · 유지) · 계획서 판정 기록 — 반영). 기준선 worktree 차분 하네스로 단일 22건·멀티 44건(반환·조립기 입력·SQL·`form_fill_out` 순서·로그) · O6 2단/3단 각 2,880조합 **바이트 동일**. 변이 9개 중 등가 1개 제외 8개 검출(검증 테스트 2파일 25건 추가).
- 계획 끝 회귀(1회): `python scripts/regress.py --base 3a0a1101 --files <9>` — 본체 통과 2,710 · 실패 0 · 에러 0 · 건너뜀 10 · 실패 귀속 「실패 없음」 · `[전체 회귀 권고]`·`[웨이브 회귀 필요]` 블록 없음 · `범위: 모듈 단위 — 전체 미실행`. 이후 추가분(verifier 테스트 2파일 · 주석 이동 1곳)은 자기 테스트 110 passed + `--no-tests` 11파일 통과.
- 매뉴얼 미반영: 사용자 접점 변경 없음(D-255).

## 5. 이력

- 2026-10-16 v1.1 — 완료: N1(a)·O6 · N1(b) 판정 · 계획 끝 회귀 실패 0 · 파일명 태그 제거
- 2026-10-16 v1.0 — 작성 · 착수(166 §0.1 단계 5 · 5b)
