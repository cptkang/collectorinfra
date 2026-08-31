# 태스크 목록 — Plan 81 호스트 가용성 사전 판정

> 계획: `tasks/plan-81.md` · SPEC: `SPEC-host-availability-precheck.md`

- [x] **T1** 판정 도메인 신설
  - Acceptance: `judge_availability(avail_status, is_maintenance, *, now=None)` 가 SPEC §3.2 결정표대로 `HostAvailability` 반환. `blocks_collection`은 `unavailable`만 True. 문구는 "전원 off" 단정 금지
  - Verify: `pytest tests/test_domain/test_host_availability.py -q` (8건+) · `arch_check --ci`
  - Files: `src/domain/host_availability.py`, `tests/test_domain/test_host_availability.py`

- [x] **T2** 폴스타 조회 확장 (추가 왕복 0)
  - Acceptance: `build_hostname_sql`에 `avail_status`·`is_maintenance` 추가 · `resolve_with_status()` 신설 · `build_host_status_sql(db_id, values, engine)` 배치 조회 · **`resolve()` 반환형·동작 불변**
  - Verify: `pytest tests/test_orchestration/test_process_hostname_resolve.py -q` **무수정 통과** + 신규 6건
  - Files: `noise_gate/infrastructure/polestar_hostname_resolver.py`, `tests/test_orchestration/test_host_status_sql.py`

- [x] **T3** 설정 플래그
  - Acceptance: `CompositeConfig`에 `availability_precheck_enabled=True`(G-1) · `availability_block_on_unavailable=True` · `availability_staleness_enabled=False`
  - Verify: `pytest tests/test_config -q`(있으면) · 설정 로드 스모크
  - Files: `src/config.py`

- [x] **T4** `process_query` 단일 경로 배선
  - Acceptance: `unavailable`이면 **프로세스 API 미호출**(mock 0회)·`is_sufficient False`·사유 문구. 판정 실패/조회 실패면 종전 경로 그대로. 플래그 off면 비트 동일
  - Verify: `pytest tests/test_orchestration/test_process_availability.py -q`
  - Files: `src/orchestration/process_query.py`, `tests/test_orchestration/test_process_availability.py`

- [x] **T5** `process_query` fan-out 대칭 배선
  - Acceptance: N대상 판정을 **배치 1쿼리**로 선취 · 불가 대상은 수집 제외 · 실패 목록에 사유 코드 동반
  - Verify: 같은 파일 fan-out 테스트 (단일과 문구 규약 동일 단언)
  - Files: `src/orchestration/process_query.py`, `tests/test_orchestration/test_process_availability.py`

- [x] **T6** 결과 문구·메타
  - Acceptance: 요약 **맨 앞**에 판정 문구 · `process_query.availability` 메타 · fan-out 실패 요약 **사유별 분해** · `unavailable`에 "잠시 후 다시 시도" 문구 **미출력**
  - Verify: `pytest tests/test_orchestration -q`
  - Files: `src/orchestration/process_query.py`

- [x] **T7** `fault_diagnosis` 게이트 (G-2)
  - Acceptance: 대상 해소 직후 판정 · `unavailable`이면 `sre_agent_client` **호출 0**·사유 응답(기존 `_DENY_MESSAGES` 형태 따름)
  - Verify: `pytest tests/test_nodes -q -k fault_diagnosis`
  - Files: `src/nodes/fault_diagnosis.py`, 테스트

- [x] **T8** `sre_agent` 계약 확장 (후방 호환)
  - Acceptance: `build_trigger_payload`에 `meta.target_state` · `sre_diagnose(..., target_state=None)` 선택 인자. 기존 호출자 무변경 동작
  - Verify: `pytest noise_gate/tests -q -k payload` · `cd sre_agent && pytest -q -k payload`
  - Files: `noise_gate/domain/investigation_payload.py`, `sre_agent/sre_agent/interface/mcp_service.py`

- [x] **T9** `sre_agent` 6번째 가드
  - Acceptance: `target_state.state == "unavailable"` → `target_unavailable` 사유로 terminal 확정 + 브리핑 명시 · **필드 부재 시 통과(fail-open)** · in-flight 키 해제 누락 없음
  - Verify: `cd sre_agent && pytest -q`
  - Files: `sre_agent/sre_agent/application/investigation_dispatcher.py`, `sre_agent/tests/test_target_unavailable_guard.py`

- [x] **T10** `investigation_trigger` 배선 (G-3)
  - Acceptance: 트리거 전 판정 → `target_state` 동봉 · **DOWN 계열 알람은 예외**로 조사 진행 · 판정 실패 시 종전 동작
  - Verify: `pytest noise_gate/tests -q`
  - Files: `noise_gate/application/nodes/investigation_trigger.py`, `noise_gate/tests/test_investigation_target_state.py`

- [x] **T11** 문서·결정 등재
  - Acceptance: D-175 본문 등재(기본 on 예외 근거 포함) · 「변경 이력」 표 · `docs/25` L-5 · `plans/81` 상태 갱신 · `plans/INDEX.md` 갱신
  - Verify: 채번 grep 3곳 정합 · 링크 유효
  - Files: `docs/02_decision.md`, `docs/25_host_investigation_load_guard.md`, `plans/81-*.md`, `plans/INDEX.md`

## 실제 결과 (2026-08-28)

- 신규 테스트 **86건**: 도메인 30 · 조회 18 · 프로세스 20 · 진단 10 · 트리거 15 · `sre_agent` 18
  *(진단·트리거는 파라미터라이즈 포함 실집계)*
- **기존 테스트 갱신 3파일** — 전부 화이트박스 패치 지점 이동. 내역은 `SPEC-host-availability-precheck.md`
  「Testing Strategy」 표에 기록(조용한 기준선 완화 금지). 교훈은 `docs/18_known_mistakes.md` 등재.
- 계획 대비 변경 2건: ①공용 조회 함수를 `noise_gate/infrastructure`에 배치(계층 규칙 — D-171과 같은 함정)
  ②`meta.target_state`는 **값이 있을 때만** 키 생성(계약 테스트가 지적)
