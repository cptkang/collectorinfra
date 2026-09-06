# Todo 50 — 장애진단·원인분석 잔여

> `tasks/plan-50.md` · 모듈 id는 `CAPABILITY-MAP-50.md` 기준 · **전부 완료(2026-09-02 · D-194)**

## `briefing-contract` (M1)
- [x] T1 결함 고정 테스트(red) — `tests/test_briefing_contract.py`(16)
- [x] T2 공용 렌더러 `noise_gate/domain/investigation_briefing.py::render_briefing_lines`
- [x] T3 소비자 2곳 정렬(같은 커밋) — `_briefing_to_text`·`_append_briefing_extras`·`_investigation_briefing_html` · 스텁 비트 동일 · 잘못된 키를 굳힌 기존 테스트 정정
- [x] T4 생산자 대칭 계약 — `sre_agent/tests/test_briefing_builder.py::test_output_keys_match_consumer_contract`
- [x] T5 게이트 — arch/overfit 0

## `incident-window-tools` (M2)
- [x] 기본 경로 SQL 스냅샷(4종) 문자열 동일 · `parse_reference_time`·`incident_window` · `build_incident_alarms_sql` · 도구 3종 인자 + `polestar_incident_alarms` 신설 · `run_metric_range(reference_time)` — `mcp_server/tests/test_incident_window.py`(28)

## `investigation-guidance` (M3)
- [x] `application/investigation_guidance.py::build_guidance` · `_default_diagnose_fn` 배선 · `INVESTIGATION_GUIDANCE_EXTRA` — `test_investigation_guidance.py`

## `incident-scope` (M4)
- [x] `domain/incident_scope.py` · 잡 필드 `reference_time`/`lookback_minutes` · push `alarmTime` · `sre_diagnose` 인자 · 형식 오류 거부 — `test_incident_scope.py`
- [x] `src/domain/incident_time.py` 결정적 파서 · `fault_diagnosis` → `sre_agent_client.diagnose` 인자 전달(시각 없으면 미전송) — `tests/test_incident_time.py`(19)

## `evidence-correlation` (M5)
- [x] `domain/correlation.py`(벤더 중립 · 골든) · `application/evidence_prefetch.py` · `infrastructure/mcp_tool_client.py`(B′) · dispatcher `prefetch_fn` · 감사 · `EVIDENCE_CORRELATION_ENABLED` 기본 off · `pyproject` `mcp<2`

## `diagnosis-briefing` (M6)
- [x] `build_briefing(correlation=…)` — 상대시각 타임라인 · `root_cause_hypotheses` rank/confidence · `notes`→limitations · 상관 없으면 `[]` · 렌더러 `[원인 가설]`

## 문서 (M7)
- [x] D-194 등재(3곳) · `plans/50` §0.3 해소 표·§0.6 ✅·§18 v2.2 · `plans/INDEX.md` · SPEC 정정 · `docs/26` §3.7 · `docs/18`

## 남은 것(범위 밖)
- [ ] 실 조사 e2e(D-127 건별 승인) · 플래그 on 운영 실측 · Phase C′(다중서버 연쇄 · 진단 이력)
