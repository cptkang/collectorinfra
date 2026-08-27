# Todo — 차수 3-A

계획: `tasks/plan-3a.md` · 명세: `SPEC-composite-orchestration.md`
**공통 완료 기준**(전 작업): `plans/80` §5.4 6항 + `tasks/plan-3a.md` §4 체크포인트 5항.

---

## T0. 기준선 고정
- [x] 전체 회귀 실행 → failed/passed/errors/xfailed 기록
  - Acceptance: 숫자가 `tasks/plan-3a.md` §0에 기록된 값과 일치(다르면 원인 확정 후 진행)
  - Verify: `python -m pytest -q --ignore=tests/e2e`
  - Files: (없음)

---

## M1. prior-targets — WU-11 / 78 W1

- [x] **T1-1** `looks_like_process_rows`를 `query_gen_common`(utils)로 단일 출처 이동
  - Acceptance: `context_resolver`가 re-export로 소비, 동작 동일. 사본 0(D-053)
  - Verify: `pytest -q tests/test_nodes/ -k context_resolver`
  - Files: `src/utils/query_gen_common.py`, `src/nodes/context_resolver.py`
- [x] **T1-2** `src/utils/prior_targets.py` 신설 — `TargetRef`·`TargetResolution`·결정적 확정
  - Acceptance: `TargetRef{server_name, hostname, ip, db_id}`(C-2). 필수 키 누락·타입 불일치가
    `ValidationError`. 지시어·프로세스 행 배제. 상한 절단 시 `truncated`/`truncated_count` 노출
  - Verify: `pytest -q tests/test_composite/test_prior_targets.py`
  - Files: `src/utils/prior_targets.py`
- [x] **T1-3** 해석 3단(결정적 → 유사어 → LLM 컬럼 지목) — 주입 콜러블
  - Acceptance: 1·2단 확정 시 **LLM 호출 0회**. 3단이 결과 행에 **없는 컬럼명**을 반환하면
    대상 미생성 + 사유. 검증 탈락이 **3단 재호출 루프를 만들지 않는다**
  - Verify: 같은 파일 (mock LLM · 호출 카운터)
  - Files: `src/utils/prior_targets.py`
- [x] **T1-4** `AgentState.prior_targets` + 요청 스코프 명시 초기화
  - Acceptance: 상태에 실리는 값이 **`dict`**(`model_dump()`). `create_initial_state`에서 `None` 초기화
  - Verify: `pytest -q tests/test_composite/test_prior_targets.py -k state`
  - Files: `src/state.py`
- [x] **T1-5** `CompositeConfig` 신설(env_prefix `COMPOSITE_`) — 전 플래그 기본 off/보수값
  - Acceptance: `.env` 미설정 시 현행 동작 **비트동일**. 인라인 주석 0
  - Verify: `pytest -q tests/test_config*`
  - Files: `src/config.py`
- [x] **T1-6** 1단 주입 — `deepagents_tools` 생산자/소비자 게이트 확장
  - Acceptance: 생산자에 `process_query`·`fault_diagnosis` 추가. 소비자가 agent별로 분기
    (`data_query`/`alarm_query`→`prior_rows`, `process_query`/`fault_diagnosis`→`prior_targets`)
  - Verify: `pytest -q tests/test_composite/test_prior_targets_wiring.py -k tool`
  - Files: `src/orchestration/deepagents_tools.py`
- [x] **T1-7** 2단 주입 — `subagents._make_isolated_input`
  - Acceptance: 1단과 **동일 형태**로 `prior_targets` 주입(대칭 2건 단언)
  - Verify: 같은 파일 `-k isolated`
  - Files: `src/orchestration/subagents.py`
- [x] **T1-8** 3경로 공통화 — `process_query._resolve_hostname` · `fault_diagnosis._extract_targets`
      · `investigation_trigger`
  - Acceptance: 셋 다 `resolve_targets()` 경유. 우선순위 ①filter → ②prior_targets →
    ③previous_entities → ④알람 페이로드. **①이 ②를 이긴다**
  - Verify: `pytest -q tests/test_composite/ noise_gate/tests/test_investigation_trigger.py`
  - Files: `src/orchestration/process_query.py`, `src/nodes/fault_diagnosis.py`,
    `noise_gate/application/nodes/investigation_trigger.py`
- [x] **T1-9** `T-G2` xfail 마커 제거 + R-13 경계 단언
  - Acceptance: `T-G2` 통과. 변경분에 `intent_planner`·`task_plan` 수정 **0**
  - Verify: `pytest -q tests/test_orchestration/test_composite_host_scope.py` · `git diff --name-only`
  - Files: `tests/test_orchestration/test_composite_host_scope.py`

## M2. investigation-audit — WU-14 / 78 W6 ★Tier 1

- [x] **T2-1** `log_investigation()` — 기존 `AuditEntry`/`_write_audit_file` 재사용(C-5)
  - Acceptance: `{request_id, user_id, targets, profile, commands, backend, rc, duration, authz}`.
    stdout 원문 **마스킹 후** 저장. 79가 신뢰도 필드를 **추가**할 수 있는 형태(계약 C-B v2)
  - Verify: `pytest -q tests/test_composite/test_investigation_audit.py`
  - Files: `src/security/audit_logger.py`
- [x] **T2-2** Tier 2 지표 4종 — 압축·캐시·라우팅·비용 귀속
  - Acceptance: 4종이 실제로 남는다. 지표 없이 M7 착수 불가함을 문서로 고정
  - Verify: 같은 파일 `-k metrics`
  - Files: `src/observability/investigation_metrics.py`
- [x] **T2-3** 기동 로그 1줄 — 조사 경로 활성 상태·플래그 확정값(사다리 로그 전례)
  - Acceptance: 플래그가 **기동 시 1회** 해석됨을 단언(78 P14)
  - Verify: 같은 파일 `-k startup`
  - Files: `src/observability/investigation_metrics.py`, `src/main.py`

## M3. target-fanout — WU-12 / 78 W2-1~6

- [x] **T3-1** `run_process_query` N-대상 확장 — `gather` + `Semaphore` + per-target timeout
  - Acceptance: 3개 입력 → **3개 조사**. 단일 대상 **기존 경로 회귀 0**
  - Verify: `pytest -q tests/test_composite/test_target_fanout.py`
  - Files: `src/orchestration/process_query.py`
- [x] **T3-2** 부분 실패 격리 + 전체 타임아웃 가드
  - Acceptance: 1개 실패 시 나머지 반환 + 실패 사유 **노출**. 전체 상한 실동작
  - Verify: 같은 파일 `-k partial or timeout`
  - Files: `src/orchestration/process_query.py`
- [x] **T3-3** 결정적 reduce + 중복 조사 직렬화 + 부하 가드 요구 전달
  - Acceptance: 응답에 대상 수/성공/실패/절단. 같은 호스트 동시 조사 **1건**
    (in-flight 키 `(db_id, hostname)`). 부하 가드 요구가 **계약에 명시**
  - Verify: 같은 파일 `-k reduce or inflight or loadguard`
  - Files: `src/orchestration/process_query.py`, `docs/` 계약 문서
- [x] **T3-4** `T-G3` xfail 마커 제거
  - Verify: `pytest -q tests/test_orchestration/test_composite_host_scope.py`
  - Files: `tests/test_orchestration/test_composite_host_scope.py`

## M4. mcp-highlevel-tools — WU-13 / 78 W3-1·4

- [x] **T4-1** `DBHubClient` 고수준 도구 4종 배선 — **실측 시그니처**(C-3)
  - Acceptance: `polestar_process_snapshot`은 **`source` 인자 없음**. 반환 계약
    `{rows,row_count,queried_at,source_kind,source,engine}` 그대로 소비. 오류는 `{error}`
  - Verify: `pytest -q tests/test_composite/test_mcp_tools.py`
  - Files: `src/dbhub/client.py`
- [x] **T4-2** 인자 스키마 실행 전 검증 + 구조화 실패 반환 + `profile` 흡수
  - Acceptance: 잘못된 `kind`/`sort`가 **호출 전에** 걸린다. 도구 수를 늘리지 않는다.
    `execute_sql` 노출 정책 **무변경**(D-122 ④)
  - Verify: 같은 파일 `-k validate or profile or readonly`
  - Files: `src/dbhub/client.py`

## M5. host-authz — WU-15 / 78 W3-5

- [x] **T5-1** `src/domain/host_authz.py` — 순수 정책(mode×principal → 판정)
  - Acceptance: `admin_only`는 `admin`·`system`만 허용. **미상 role·미상 mode 전부 차단**
    (fail-closed). 거부에 **사유**
  - Verify: `pytest -q tests/test_composite/test_host_authz.py`
  - Files: `src/domain/host_authz.py`
- [x] **T5-2** `user_role` 전파 배선(C-4) — state → 라우트 3곳 → subagents → deep_agent
  - Acceptance: 전파 누락 시 **차단**(fail-open 아님)을 단언
  - Verify: 같은 파일 `-k propagat`
  - Files: `src/state.py`, `src/api/routes/query.py`, `src/orchestration/subagents.py`,
    `src/orchestration/deep_agent.py`
- [x] **T5-3** 실행 경계 게이트 + 감사 슬롯 채우기 + 채팅·이벤트 대칭
  - Acceptance: 미인가 호스트는 조사 **시작되지 않는다**(호출 0회). 양쪽 동일하게 막힌다(2건).
    판정 결과가 M2 레코드에 실린다
  - Verify: 같은 파일 `-k boundary or symmetry`
  - Files: `src/orchestration/process_query.py`, `src/nodes/fault_diagnosis.py`,
    `noise_gate/application/nodes/investigation_trigger.py`

## M6. sufficiency-replan — WU-16 / 78 W5

- [x] **T6-1** 결정적 충족도 체크(LLM 미사용) + **1회만** 재계획
  - Acceptance: **무한 루프 부재** · 재시도 1회 상한 · 미충족 사유 **노출**
  - Verify: `pytest -q tests/test_composite/test_sufficiency.py`
  - Files: `src/orchestration/agent_orchestrator.py`
- [x] **T6-2** 실행 전 준비 검증 + 대상 정합 사후 대조
  - Acceptance: 경로 미가용을 **조사 실패로 기록하지 않는다**. `prior_targets`에 없는 hostname이
    결과에 실리면 **오류로 잡힌다**
  - Verify: 같은 파일 `-k readiness or reconcile`
  - Files: `src/orchestration/agent_orchestrator.py`

## M7. fanout-compaction — WU-17 / 78 W2-7·8 (Tier 2)

- [x] **T7-1** 결정적 2단 축약 + 압축 손실 기록 + **원문 전량 보존**
  - Acceptance: 절단 시 **호스트별 절단 행 수**가 결과에 존재. 원문 전량이 감사·CSV에 남는다
  - Verify: `pytest -q tests/test_composite/test_compaction.py`
  - Files: `src/orchestration/process_query.py`
- [x] **T7-2** 단기 조사 캐시 — TTL·나이 표기·**만료 sweep**
  - Acceptance: TTL 내 재조회는 **수집기 호출 0회** + 수집 시각 표기. TTL 경과 후 재수집.
    만료 키 sweep(dict 무한 증가 없음)
  - Verify: `pytest -q tests/test_composite/test_investigation_cache.py`
  - Files: `src/orchestration/investigation_cache.py`, `src/orchestration/process_query.py`

## M8. diagnosis-consumption — WU-19 / 78 W4

- [x] **T8-1** 브리핑 6요소·`Remediation` 소비 — `_BRIEFING_ORDER` 재사용
  - Acceptance: 권고 생성 코드 **부재 단언**. 위험도·신뢰도 **원본 그대로**
  - Verify: `pytest -q tests/test_composite/test_diagnosis_consumption.py`
  - Files: `src/nodes/fault_diagnosis.py`
- [x] **T8-2** 부분 결과 표시 + **변경 명령 실행 경로 부재** 고정
  - Acceptance: 조사 실패·타임아웃·인가 거부 사유가 노출. 실행 경로 부재 단언 통과
  - Verify: 같은 파일 `-k noexec or partial`
  - Files: `src/nodes/fault_diagnosis.py`

---

## T9. 마감
- [x] 전체 회귀 최종 대조(기준선 + xfail 2건 전이)
- [x] `arch_check --ci` 본체·`noise_gate` 양쪽 exit 0
- [x] 라우팅 단언 0건 grep
- [x] `plans/80` §5.2 WU 상태 갱신(WU-11~17·19)
- [x] C-1~C-5를 `plans/78` §6.1·`plans/80`에 반영 · `docs/02_decision.md` 신규 결정 기록
