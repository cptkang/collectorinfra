# 태스크 목록 — Plan 82 Wave 5·6.5

> 계획: `tasks/plan-82-w56.md` · SPEC: `SPEC-host-discovery.md` · `SPEC-scope-select.md`
> 기준선(2026-08-28): 대상 영역 **0 failed** (사전존재 실패 13건 정리 완료)
> 판정: **0 failed를 유지**하고 신규 테스트를 더한다.

## W5 — host-discovery

- [x] **T1** 순회 결과 판정 (순수)
  - Acceptance: `src/domain/host_discovery.py` — `ZoneHit`·`SweepOutcome` 값 객체 + `classify(outcome)` 가 `resolved`(1건) / `ambiguous`(2건 이상) / `not_found`(0건) 를 판정. `not_found`는 **순회한 존 라벨 전량**과 **조회 실패 존**을 분리해 담는다(U6). 순수 함수 — I/O·LLM 0
  - Verify: `pytest tests/test_discovery/test_classify.py -q` · `arch_check --ci`
  - Files: `src/domain/host_discovery.py`, `tests/test_discovery/test_classify.py`

- [x] **T2** 존 순회 실행 + TTL 캐시
  - Acceptance: `src/orchestration/host_sweep.py` — `sweep_zones(identifier, db_ids, lookup, ...)` 가 `query_order` 순으로 순회. **전수 순회**가 기본이고 `early_exit=True`면 첫 히트에서 중단(U4). 순회 대상은 **인가된 존만**. 존별 예외는 `errors`에 사유로 남기고 계속 진행. TTL 60초 캐시(U12) — **0건은 캐시하지 않는다**. `lookup`은 주입점(테스트가 대역 주입)
  - Verify: `pytest tests/test_discovery/test_sweep.py -q`
  - Files: `src/orchestration/host_sweep.py`, `tests/test_discovery/test_sweep.py`

- [x] **T3** 배선 — `_resolve_db_id` ⑤ 탐색
  - Acceptance: `CompositeConfig`에 `host_discovery_enabled`(기본 **True**) · `discovery_early_exit`(기본 False) · `discovery_cache_ttl_seconds`(기본 60). `process_query`가 `db_id` 미해소 + 식별자 존재 + 플래그 on일 때만 탐색 진입. ①~④가 성립하면 **호출 0회**. 플래그 off면 현행 안내 문구 **바이트 동일**
  - Verify: `pytest tests/test_discovery -q` · `pytest tests/test_composite -q` · `arch_check --ci`
  - Files: `src/config.py`, `src/orchestration/process_query.py`, `tests/test_discovery/test_wiring.py`

- [x] **T4** 결과 응답 — 단일·다중·0건
  - Acceptance: `resolved` → 그 존으로 본 조회 이어감 + 가용성 병기(D-175 재사용, **추가 왕복 0**). `ambiguous` → **발견된 존으로 좁힌** clarification(U5). `not_found` → 순회한 존 목록 + 조회 실패 존 구분 안내(U6). `state.discovery_trace` 적재(요청 스코프). **탐색은 경과이지 결과가 아니므로 LLM 합성을 추가하지 않는다**(U7 — 기존 단일 합성이 처리)
  - Verify: `pytest tests/test_discovery/test_outcome_response.py -q`
  - Files: `src/orchestration/process_query.py`, `src/state.py`, `tests/test_discovery/test_outcome_response.py`

## W65 — scope-select

- [x] **T5** 발동 게이트 + 페이로드 (순수)
  - Acceptance: `src/domain/scope_select.py` — `scope_question_or_none(groups, ctx, enabled)`. 비발동: 플래그 off / 그룹 1개 / 비대화 채널 / `selected_db_ids` 재개 턴 / 승계 / **모호성 해소 대기**. 페이로드는 `"전체 조회"`가 **첫 선택지 + `default: true`**. **시간 임계 상수 없음**(U11) · 표본 n<20이면 초 표기 생략
  - Verify: `pytest tests/test_scope_select/test_gate.py -q` · `arch_check --ci`
  - Files: `src/domain/scope_select.py`, `tests/test_scope_select/test_gate.py`

- [x] **T6** 라우트 배선
  - Acceptance: `CompositeConfig`에 `scope_select_enabled`(기본 **True**). `_zone_clarification_or_none` **다음** 순위로 호출 — 모호성 해소가 이긴다. 응답은 기존 `status="clarification"` shape 재사용(`selected_db_ids` 왕복). 발동률 카운터 증가
  - Verify: `pytest tests/test_scope_select -q` · `pytest tests/test_api -q`
  - Files: `src/config.py`, `src/api/routes/query.py`, `tests/test_scope_select/test_route.py`

- [x] **T7** 프론트 — 전체 조회 기본 + 건너뛰기
  - Acceptance: `renderZoneClarification`이 `default: true` 옵션을 **미리 선택**하고, `kind="scope_select"`면 *"범위를 좁히시겠습니까?"* 문구 + **건너뛰기 버튼**(= 전체 조회)을 렌더한다. 답하지 않아도 진행 가능(U10). 기존 `zone_select` 동작 **무변경**
  - Verify: `pytest tests/test_api/test_ui_scope_select.py -q`
  - Files: `src/static/js/app.js`, `tests/test_api/test_ui_scope_select.py`

- [x] **T8** 미조회 범위 기록 + 재확장
  - Acceptance: 좁혔으면 `state.scope_narrowed = {selected, skipped}` 적재 + 응답 말미에 **미조회 범위 명시** + 재확장용 `selected_db_ids`(전체) 페이로드 첨부. 감사 로그에도 남긴다(침묵 절단 금지)
  - Verify: `pytest tests/test_scope_select/test_narrow_record.py -q`
  - Files: `src/state.py`, `src/api/routes/query.py`, `tests/test_scope_select/test_narrow_record.py`

## 마감

- [x] **T9** 전체 검증 + 문서
  - Acceptance: 대상 영역 **0 failed 유지** + 신규 전량 통과. `overfit_check` 0. `arch_check --ci` 0. `docs/02_decision.md`에 **D-176 후속3·후속4 등재**(계획서 개정 4건 U9·U11·U12·U13 근거 포함). `plans/82` 상태·`plans/INDEX.md`·`CAPABILITY-MAP` v5 갱신
  - Verify: 위 명령 전부
  - Files: `docs/02_decision.md`, `plans/82-*.md`, `plans/INDEX.md`, `CAPABILITY-MAP-execution-groups.md`

## 완료 실측 (2026-08-28)

| 항목 | 결과 |
|---|---|
| 대상 영역(16개 스위트 — `test_nodes`·`test_utils`·`test_db_adapters`·`test_middleware`·`test_orchestration`·`test_semantic_routing`·`test_empty_answer`·`test_spike`·**`test_discovery`**·**`test_scope_select`**·`test_api`·`test_composite`·`test_state*`·`test_config_env_reload`·`test_multiturn`) | **2465 passed · 0 failed · 1 skipped** |
| 신규 테스트 | **125건 전량 통과**(`test_discovery` 65 · `test_scope_select` 47 · `test_ui_scope_select` 13) |
| `arch_check --ci` | exit 0 |
| `overfit_check --ci` | 신규 유입 0 |

**정리 작업 동반 처리**: 사전존재 실패 **14건**을 전부 해소했다(대상 영역 6 · `test_routes` 6 ·
`test_config_env_reload` 1 · `test_multiturn` 1). 전부 **구현이 의도적으로 바뀌었는데 테스트가
옛 계약을 붙잡고 있던 것**이었고, 갱신은 계약을 **강화하는 방향**으로 했다.

**설정 카탈로그**: 카운터 정산 중 **5개 그룹 31개 설정이 관리자 UI에 렌더되지 않는** 누락을
발견해 해소하고, 숫자 대신 **파생 등가성 드리프트 가드**를 세웠다 — 숫자 단언은 낡지만
집합 단언은 낡지 않는다.

**범위 밖 잔여**(전체 저장소 · 이번 작업과 무관): `test_e2e_polestar`(DBHub 필요) ·
`test_plan33_join_prevention`(프로필 의존) 등. 착수 전 전체 스위트에서 이미 실패하던 항목이다.
