# 태스크 목록 — Plan 83 (T1~T13 구현 완료 · 2026-08-28) (피드백 루프 · 표시 레벨 · 설정 UI 커버리지)

> 계획: `tasks/plan-83.md` · 맵: `CAPABILITY-MAP-83.md`
> SPEC: `SPEC-alarm-feedback-loop.md`(A) · `SPEC-alarm-view-level.md`(B) · `SPEC-settings-ui-coverage.md`(C)

## 차수 1 — 보안 (alarm-feedback-loop)

- [x] **T2** 피드백 존 RBAC ★보안
  - Acceptance: `submit_alarm_feedback`이 `alarm_zones_for_user`로 존 판정. 존 없음→403, 타존 `db_id`→403, 같은 존→200. **`db_id` 미동반 요청은 통과**(하위호환)
  - Verify: `pytest tests/test_api/test_alarm_feedback_rbac.py -q` · `arch_check --ci`
  - Files: `src/api/routes/alarm.py`, `tests/test_api/test_alarm_feedback_rbac.py`

- [x] **T3** ack 존 RBAC
  - Acceptance: `IncidentStore.get_db_id(incident_id)` 포트 추가 + PG 구현. `ack_incident`가 대상 incident의 db_id로 존 판정 → 타존 403. 조회 실패(None)면 **차단하지 않음**(graceful). 트래커 off는 기존 503 유지
  - Verify: `pytest tests/test_api/test_alarm_feedback_rbac.py -q -k ack` · `arch_check --ci`
  - Files: `noise_gate/domain/incident_store.py`, `noise_gate/infrastructure/incident_repository.py`, `src/api/routes/alarm.py`, 테스트

## 차수 2 — 그물 먼저 (settings-ui-coverage)

- [x] **T1** 섹션 매핑 + 누락 감지 테스트
  - Acceptance: 미분류 8키에 구획 부여 → `field_index()`의 모든 `NOISE_` 키가 `SECTION_BY_KEY`에 존재. 신규 키를 매핑 없이 넣으면 **실패하는** 테스트 존재. `.env`·`.env.example` 키 미포함 0건 단언
  - Verify: `pytest tests/test_api/test_settings_catalog_sections.py -q`
  - Files: `src/api/settings_catalog.py`, `tests/test_api/test_settings_catalog_sections.py`

## 차수 3 — 저장소·계약 (alarm-feedback-loop)

- [x] **T4** feedback_store 확장
  - Acceptance: `labeled_by` 기록(few-shot 렌더에는 **미포함**) · `record_retract(target_ts, …)` tombstone append · `find_similar`가 철회 대상 제외 · tail 읽기(창=상한) · 2세대 회전(`.1`) · **표준 라이브러리만**
  - Verify: `pytest noise_gate/tests/test_feedback_store.py -q`(20k 픽스처 등가성 포함) · `arch_check --ci`
  - Files: `noise_gate/infrastructure/feedback_store.py`, `src/config.py`(`feedback_store_max_lines`), `noise_gate/tests/test_feedback_store.py`

- [x] **T5** capabilities 엔드포인트
  - Acceptance: `GET /api/v1/alarm/capabilities` → `feedback_enabled`·`incident_tracking`·`sse_bridge`·`suppress_stream`·`suppress_max_severity`. 인증 필요. **경로·시크릿 미노출**. `feedback_enabled`는 피드백 503 조건과 동일 식
  - Verify: `pytest tests/test_api/test_alarm_capabilities.py -q`
  - Files: `src/api/routes/alarm.py`, `tests/test_api/test_alarm_capabilities.py`

- [x] **T6** 결정적 pattern 배선 + blocking 회피
  - Acceptance: `pre_classification`이 SSE payload **3곳 모두**(`_tier_sse_payload`·`_incident_open_payload`·analyze push_to_ui)에 실림 · 피드백 라우트가 `labeled_by` 전달 · `find_similar` 호출이 `asyncio.to_thread` 경유
  - Verify: `pytest noise_gate/tests/ -q -k "notifier or actionability"` · `pytest tests/test_api/ -q -k alarm`
  - Files: `noise_gate/application/nodes/alarm_notifier.py`, `noise_gate/application/nodes/alarm_analyzer.py`, `src/api/routes/alarm.py`

## 차수 4 — 카드 UI (alarm-feedback-loop)

- [x] **T7** 피드백 카드 UI
  - Acceptance: 요청 본문에 `db_id`·`server_name`·`pre_classification` 포함 · note 접이식 입력(200자·민감정보 금지 안내) · 성공 후 `취소` 링크(→ retract API) · `capabilities.feedback_enabled=false`면 **버튼 미렌더**
  - Verify: 수동 확인(카드 렌더·전송 payload) + `POST /alarm/feedback` 레코드 필드 확인
  - Files: `src/static/js/app.js`, `src/api/routes/alarm.py`(retract 라우트)

## 차수 5 — 표시 레벨 (alarm-view-level)

- [x] **T8** 플래그 + 구획 등재
  - Acceptance: `NOISE_SSE_SUPPRESSED_ENABLED`(기본 `false`) 추가 · `.env`/`.env.example` 반영(**인라인 주석 금지**) · `SECTION_BY_KEY` 등재 → T1 테스트 통과
  - Verify: `pytest tests/test_api/test_settings_catalog_sections.py -q`
  - Files: `src/config.py`, `.env`, `.env.example`, `src/api/settings_catalog.py`

- [x] **T9** SUPPRESS 발행 분기
  - Acceptance: 플래그 on이면 SUPPRESS도 `_publish_tier_sse` 호출 · **기본 off면 발행 0(비트 동일)** · 감사 기록 경로 불변
  - Verify: `pytest noise_gate/tests/test_notifier_suppress_sse.py -q`
  - Files: `noise_gate/application/nodes/alarm_notifier.py`, 테스트

- [x] **T10** 스트림 관리자 판정
  - Acceptance: `tier=="suppress"` 이벤트는 **`role=="admin"` 구독자에게만** 전송. 그 외 티어는 현행(존 필터)대로. 인증 비활성(개발 모드)은 기존 진입성 유지
  - Verify: `pytest tests/test_api/test_alarm_stream_suppress.py -q` (플래그×role 4조합)
  - Files: `src/api/routes/alarm.py`, 테스트

- [x] **T11** 레벨 셀렉트·필터 UI
  - Acceptance: 수신 토글 옆 셀렉트(4단계·기본 `dashboard`) · `localStorage["alarm_view_level"]` 유지 · `isTierVisible()` 순수 함수로 필터(**tier 미상 통과**) · 툴팁에 현재 레벨 표기 · 비관리자에게 `억제 포함` 옵션 미노출
  - Verify: 레벨 4종 × 티어 4종 + 미상 조합 수동 확인 · 새로고침 후 유지 확인
  - Files: `src/static/index.html`, `src/static/js/app.js`

## 차수 6 — 마감

- [x] **T12** 경계 안내 + 시크릿 배지 (settings-ui-coverage)
  - Acceptance: 설정 탭에 "이 화면 범위 밖 설정"(`mcp_server/.env`·`config.toml`·`sre_agent/.env` — 경로·관리 주체·재기동) 안내 · 시크릿 필드에 `.encenv` 관리 배지 상시 노출
  - Verify: 화면 확인 + 기존 설정 저장 회귀
  - Files: `src/static/admin/dashboard.html`, `src/static/js/admin.js`

- [x] **T13** 피드백 summary API + 관리자 탭 (alarm-feedback-loop)
  - Acceptance: `GET /api/v1/alarm/feedback/summary` → (alarm_name, resource_name)별 valid/noise 카운트·최근 라벨·작성자. 관리자 화면 `알람 피드백` 탭. **판정 로직 무변경**
  - Verify: `pytest tests/test_api/ -q -k feedback_summary`
  - Files: `src/api/routes/alarm.py`, `src/static/admin/dashboard.html`, `src/static/js/admin.js`

- [x] **T14** 문서·결정 등재
  - Acceptance: `docs/28`에 버튼 동작 변경·표시 레벨 절 반영 · `docs/02_decision.md`에 D-177~179 **본문 등재**(예약 → 채번 완료 전환) · 「변경 이력」 표 행 추가
  - Verify: 문서 정합성 확인(`grep`으로 예약 라인 상태 전환 확인)
  - Files: `docs/28_noise_cancellation_operator_guide.md`, `docs/02_decision.md`
