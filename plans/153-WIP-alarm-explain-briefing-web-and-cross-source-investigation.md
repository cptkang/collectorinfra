# 153. 알람 설명 3종 — 웹 UI 조사 브리핑 표시 · HolmesGPT 폴스타(SMS)×제니퍼(APM) 크로스소스 조사 · 노이즈 관제의 억제·강등 사유와 「왜 발생했나·어떻게 처리하나」

> **번호 변경(2026-10-13)**: 구 `plans/150` → **`plans/153`** · 결정 구 D-323 → **D-325** — `main` 머지 때 번호 충돌(`main`의 150 = ITAM 5회차 통합본 편 · D-323 = 제니퍼 APM 조회 벤치 `plans/152`). 파일명·참조·테스트 파일명(`test_plan150_*` → `test_plan153_*`)·픽스처 폴더(`plan150/` → `plan153/`)를 일괄 변경했고, 사용자 원문 인용(「150번 계획을 구현하라」)은 그대로 둔다.
> **작성일**: 2026-10-12 · **v1.1**
> **상태**: **WIP — W0~W6 구현 완료(커밋 없음) · 잔여: W6 MLX 대표 1회 미실행(두 평면 mlx 미확인 — 이 체크아웃엔 `.env`가 없어 워커 ollama·오케스트레이터 vllm로 해석) · W7 제외(G-9 (b))** · 게이트 G-1~G-9 확정(권고안) · D-325 등재 · 계획 끝 모듈 단위 회귀 원래 실패 68건만 · sre_agent 836 passed · verifier Major 3·보안 High 1 교정 완료
> **요청(사용자 2026-10-12)**: *"웹 UI에서도 holmesGPU의 브리핑 내용을 볼수 있는 기능을 추가하라. SMS, APM을 크로스소스 상관 분석을 holmesGPT에서 진행될 수 있어야 한다. 노이즈 관제에서 억제, 강등의 사유를 보여주는 기능과 왜 이런 알람이 발생했고 어떻게 처리해야 되는지에 대한 내용도 보여줘야 된다. 이 요건의 구현 계획을 파일로 생성하라."*
> **용어**: 「holmesGPU」 = HolmesGPT(`sre_agent/`) · 「SMS」 = 폴스타(`plans/130` Q-1 가정 · `plans/55` 머리) · 「APM」 = 제니퍼(`apm_gateway/`)
> **입력**: 저장소 `3c4adab` 읽기·grep 실측(서브에이전트 3갈래 — 브리핑 흐름·저장 / sre_agent 크로스소스 입력·도구 / 노이즈 관제 사유·분석 표시) + 대화 중 실측(`plans/144` 테스트 8파일 308 통과 · 리플레이 off/shadow/enforce)
> **관련 계획**: `plans/144`(크로스소스 사건 상관 — 사건 추적기·규칙 표) · `plans/112`(관제 드릴다운) · `plans/91`(장애 조사 잔여 — 1-1 결정적 상관·1-4 피드백 편승) · `plans/87`(제니퍼 연동 — J3 조사 안내·J8 다중 소스) · `plans/66`(sre_agent 실행 시퀀스) · `plans/50`(RCA 축 · §6.3 상관≠인과) · `plans/83`(피드백) · `plans/101`(ML RCA — 범위 밖)
> **관련 결정**: D-035(결정적=판단 · LLM=서술) · D-048(4-티어 · 억제≠삭제) · **D-118**·D-139(`sre_agent` 양방향 import 0 · MCP 계약만) · **D-123**(submit/poll 계약 · 잡 TTL) · **D-124**·**D-137**(트리거 배선 · 즉시통보+후속) · D-195 ②(WAS 판정은 게이트웨이 단일 정의) · **D-245**(관제 사용자 경로 읽기 5종) · **D-247**(드릴다운 공용 패널 · 근거 가산 필드 · 운영자 전용 키) · D-255(매뉴얼 동반) · **D-317**(크로스소스 사건 상관 · G-8 사건 상태는 워커 메모리) · D-127(과금 게이트) · D-303(회귀)
> **D-번호**: **D-325 등재**(2026-10-12 · `docs/decisions/D-325.md`). 착지 시 D-137·D-245·D-317 부기(§5 충돌 검토)
> **계획 시작 SHA**: `3c4adabde6d11797e5fdd831f9c3a6fb7bfb9ba6`
> **회귀 대상 파일(확정 — W0~W6 착지 · 교정 포함)**: `.env.example` `config/settings_help/noise_gate_core.yaml` `config/settings_help/noise_gate_investigation.yaml` `noise_gate/application/alarm_worker.py` `noise_gate/application/nodes/alarm_notifier.py` `noise_gate/application/nodes/investigation_trigger.py` `noise_gate/application/nodes/notification_gate.py` `noise_gate/domain/cross_source.py` `noise_gate/domain/investigation_briefing.py` `noise_gate/domain/investigation_payload.py` `noise_gate/infrastructure/decision_store.py` `noise_gate/orchestration/alarm_graph.py` `noise_gate/scripts/replay_noise.py` `noise_gate/tests/test_decision_store.py` `noise_gate/tests/test_investigation_followup.py` `noise_gate/tests/test_investigation_trigger.py` `noise_gate/tests/test_noise_gate_graph_integration.py` `noise_gate/tests/test_plan144_w4.py` `noise_gate/tests/test_stage_evidence.py` `scripts/manual/alarms.py` `scripts/manual/capture.py` `scripts/manual/captures.yaml` `scripts/manual/content/admin.md` `scripts/manual/content/user.md` `scripts/manual/features.yaml` `scripts/manual/run_capture.py` `scripts/manual/snapshot.py` `src/api/routes/alarm.py` `src/api/routes/noise_dashboard.py` `src/api/settings_catalog.py` `src/config.py` `src/static/admin/noise.html` `src/static/css/noise-console.css` `src/static/css/style.css` `src/static/index.html` `src/static/js/app.js` `src/static/js/noise-help.js` `src/static/js/noise.js` `src/static/manual/admin.html` `src/static/manual/img/captured.json` `src/static/manual/user.html` `src/static/noise.html` `sre_agent/.env.example` `sre_agent/sre_agent/application/briefing_builder.py` `sre_agent/sre_agent/application/evidence_prefetch.py` `sre_agent/sre_agent/application/investigation_dispatcher.py` `sre_agent/sre_agent/application/investigation_guidance.py` `sre_agent/sre_agent/application/investigation_jobs.py` `sre_agent/sre_agent/domain/correlation.py` `sre_agent/sre_agent/interface/mcp_service.py` `sre_agent/sre_agent/settings.py` `sre_agent/tests/test_briefing_builder.py` `sre_agent/tests/test_payload_contract.py` `tests/test_api/test_alarm_capabilities.py` `tests/test_api/test_noise_dashboard_api.py` `tests/test_api/test_settings_catalog.py` `mcp_server/tests/test_plan153_r4_unknown_source.py` `noise_gate/domain/stage_handling.py` `noise_gate/infrastructure/investigation_store.py` `noise_gate/testdata/plan153/briefing_mock.json` `noise_gate/testdata/plan153/console_responses.golden.json` `noise_gate/testdata/plan153/cross_source_incident.jsonl` `noise_gate/testdata/plan153/decision_records.golden.json` `noise_gate/testdata/plan153/trigger_payloads.golden.json` `noise_gate/tests/test_plan153_verify_contract.py` `noise_gate/tests/test_plan153_w0_baseline.py` `noise_gate/tests/test_plan153_w1_analysis_record.py` `noise_gate/tests/test_plan153_w2_investigation_store.py` `noise_gate/tests/test_plan153_w4_episode_investigation.py` `noise_gate/tests/test_plan153_w6_briefing_and_priority.py` `scripts/manual/fixtures/alarms/briefing.json` `src/static/js/briefing-view.js` `sre_agent/sre_agent/domain/cross_source_episode.py` `sre_agent/tests/fixtures/plan153/apm_trigger_payloads.json` `sre_agent/tests/fixtures/plan153/briefing_mock.json` `sre_agent/tests/fixtures/plan153/noise_gate_episode_payloads.json` `sre_agent/tests/test_cross_source_investigation.py` `sre_agent/tests/test_plan153_r4_apm_prefetch_source.py` `sre_agent/tests/test_plan153_verify_roundtrip.py` `sre_agent/tests/test_plan153_w0_fixtures.py` `tests/test_api/test_plan153_w0_console_baseline.py` `tests/test_api/test_plan153_w1_console_explain.py` `tests/test_api/test_plan153_w2_investigation_api.py` `tests/test_api/test_plan153_w3_briefing_view.py` `tests/test_api/test_plan153_w6_cross_source_e2e.py` (+ 매뉴얼 이미지 `src/static/manual/img/alarm-briefing.png`·`noise-drawer-cause.png`)

---

## 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0 · 실 제니퍼 호출 0. §2 앵커는 저장소 읽기·grep으로 실측했다(`3c4adab`).
- 「(추론)」 표기는 코드 읽기로 판단했으나 실행으로 확인하지 않은 항목이다. 착수 Wave에서 테스트로 먼저 확정한다.
- 실 LLM 검증은 로컬 MLX 두 평면 확인 뒤 대표 1회만(D-127 · CLAUDE.md 「MLX 검증은 최소」). 기본 검증은 가짜 LLM·목·픽스처.

## 1. 결론

### 1.1 한 줄

세 요건은 **같은 빈칸 두 개**를 공유한다. ① 조사 브리핑과 AI 추정 원인이 **어디에도 저장되지 않는다**(workb 본문·휘발 SSE로만 나가고 버려진다). ② noise_gate가 계산한 **크로스소스 사건 맥락이 조사로 넘어가지 않는다**(`meta.episode_id` 문자열 하나뿐이고 sre_agent는 읽지 않는다). 이 계획은 먼저 저장(판정 레코드에 분석 텍스트 · 별도 조사 저장소에 브리핑)을 만들고, 그 위에 웹 표시(알람 카드 · 관제 드로어)와 크로스소스 조사(사건 스냅샷 페이로드 · 사건당 1회 종합 조사 · sre_agent 질문·지침·결정적 타임라인 확장)를 얹는다. 관제 화면의 「처리 방법」은 **결정적 단계별 처리 안내 + AI 추정 + 조사 브리핑** 3층으로 보여 주고 출처를 칸마다 표기한다.

### 1.2 요건 → 설계 대응

| 요건 | 현행(§2) | 설계 |
|---|---|---|
| **R-1** 웹 UI에서 HolmesGPT 브리핑 보기 | 브리핑은 workb에만 · 본체 저장 0 · sre_agent 메모리 TTL 1h · 조회 API 0 · 카드 UI 0 | 조사 저장소(§4.1) · 조회 API(§4.2) · 알람 카드 조사 섹션 + 관제 드로어 브리핑 섹션(§4.3) |
| **R-2** HolmesGPT에서 SMS×APM 크로스소스 상관 분석 | 두 소스 도구를 한 조사에서 부를 수는 있음(`APM_MCP_URL` 설정 시) · 사건 맥락 미전달 · 질문 인프라 고정 · 결정적 상관 폴스타 전용 · APM 트리거 사전수집 소스 오류(추론) | 사건 스냅샷 페이로드(§4.4) · 사건당 1회 종합 조사(§4.5) · sre_agent 질문·지침·사전수집·타임라인·가설 확장(§4.6) · 브리핑 `cross_source` 블록(§4.7) |
| **R-3a** 관제에서 억제·강등 사유 | **이미 있음** — 사유 문장 · 단계 라벨·설명 · `stage_evidence` 근거 · 크로스소스 `missing` 라벨 | 드로어 머리 **사유 요약 1문단**(결정적 템플릿) · 「원래 티어 → 최종 티어」 표기 · 라벨 누락(`analysis`) 교정(§4.8) |
| **R-3b** 관제에서 왜 발생했나·어떻게 처리하나 | 0 — 분석 텍스트는 억제 알람에도 이미 생성되지만 판정 레코드에 안 들어감 | 판정 레코드에 분석 텍스트 가산(추가 LLM 0) · 결정적 처리 안내 표 · 브리핑 연결(§4.9) |

### 1.3 범위 밖

- 억제·강등 알람에 대한 **새 조사 자동 발주** — 조사는 기본 PAGE 전용(`investigation_trigger_min_tier`)이고 이 계획은 바꾸지 않는다. 억제 알람의 「왜」는 이미 생성된 AI 추정 + 결정적 안내로 보인다. 운영자 수동 조사 요청은 W7(선택 · G-9).
- PAGE 카드가 웹에 오는 조건(`incident_tracking_enabled` + PG) 변경 — 현행 유지. 관제 드로어는 판정 레코드 기반이라 이 조건과 무관하게 브리핑을 보여 준다(§4.3).
- ML 기반 RCA(`plans/101`) · 트레이스 RCA · 판정 규칙 변경(억제·강등 로직은 그대로).
- sre_agent 잡 저장소 영속화 — 본체 조사 저장소가 정본이 되므로 불요(G-1 (d)는 비권고).

---

## 2. 현행 실측

### 2.1 브리핑 흐름과 저장 (R-1)

| 지점 | 실측 | 근거 |
|---|---|---|
| state 키 | `investigation_briefing`(dict · 있을 때만) · `investigation_id` · `investigation_escalation` · verdict는 감사로만 | `investigation_trigger.py:239-256` |
| 인라인 모드 | submit+poll을 `investigation_total_timeout_seconds`(기본 45s) 안에 | `investigation_trigger.py:215-224` · `src/config.py:1119` |
| 후속 모드 | submit만 → `investigation_pending` · notifier가 후속 태스크 spawn — **workb 발송 성공 시에만** | `investigation_trigger.py:359-365` · `alarm_notifier.py:503-508` |
| 브리핑 행선지 | **workb 본문뿐**(`_investigation_briefing_html`) · webhook 없음 · SSE/incident는 `investigation_id`만 | `alarm_notifier.py:141-160,335-337,806-808,961-962,1235-1280` |
| 본체 감사 | `record_investigation` = type·alarm_id·fingerprint·investigation_id·status·verdict·ts(+episode_id) — **브리핑 본문 없음** | `decision_store.py:324-333` |
| 감사 읽기 | `_tail_records`가 `type` 있는 레코드를 버림 → investigation 레코드 읽는 메서드 0 | `decision_store.py:641-642` |
| sre_agent 잡 | 메모리 + 감사 JSONL · 종결 후 TTL 3600s · 최대 500 · 재기동 시 done 잡·브리핑 복원 불가(감사 done에 briefing 없음) | `investigation_jobs.py:52-53,240-256,423-466` · `investigation_dispatcher.py:433-446` |
| 재조회 | `sre_get_investigation` 응답에 `briefing` 포함 → 종결 1h 안·무재기동일 때만 다시 받을 수 있음 | `mcp_service.py:241-246` · `investigation_jobs.py:84-102` |
| 조회 API | `src/api`에 조사 조회 라우트 0(피드백 모델의 `investigation_id` 필드뿐) | `alarm.py:417,1489` |
| 카드 | `renderAlarmMessage`에 조사 섹션 없음 · `investigation_id`는 피드백 POST에만 | `app.js:4861-5002,5147` |
| 렌더러 | `render_briefing_lines(briefing) -> [(라벨, 평문)]` 순수 함수(채팅·workb 공용) | `noise_gate/domain/investigation_briefing.py:99` |
| capabilities | 조사 관련 불리언 없음 | `alarm.py:505-523` |
| PAGE 웹 도달 | `incident_tracking_enabled` + PG INSERT 성공 시에만 재발행 | `noise_gate/infrastructure/incident_events.py:64-89` |

### 2.2 HolmesGPT 크로스소스 (R-2)

| 지점 | 실측 | 근거 |
|---|---|---|
| 페이로드 | `contract_version "1"` · event 11필드 · decision tier·reason·fingerprint·signals(`evidence` 미포함) · meta recurrence·cluster·root_resource·source + 조건부 target_state·root_resource_name·hints(APM)·**episode_id** | `investigation_payload.py:20,57-138` |
| 수신 검증 | 버전 + 필수 3필드만 · meta 추가 키 허용 | `investigation_jobs.py:41-44,122-139` |
| 사건 맥락 | 멤버·결합 근거(rule_id·cause·lag·missing) **미전달** · sre_agent에 `episode`·`cross_source` 소비 코드 0(테스트 포함) | grep |
| 사건당 1회 | APM 알람이고 사건에 기존 조사 id가 있으면 제출 생략(`episode_existing`) — 새 멤버 정보 추가 경로 없음 · 폴스타 알람은 매번 제출 | `investigation_trigger.py:93-108` · `alarm_worker.py:458-481` |
| 질문 | 서버명 + 발생 시각 + 「핵심 지표(CPU·메모리·디스크·네트워크)」 고정 | `mcp_service.py:85-102` |
| 지침 | kind → 플레이북 · APM 트리거면 APM 플레이북이 OS 플레이북을 **대체** · 인프라 트리거엔 APM 플레이북 없음 | `investigation_guidance.py:366-404,390-394` |
| 도구 | `polestar` MCP 기본 등록 · `apm` MCP는 `apm_mcp_url` 있을 때만(기본 "") · `apm_guidance_enabled` 기본 off | `mcp_service.py:113-130` · `settings.py:34,39,157` |
| 결정적 상관 | 사전수집 3도구 전부 `polestar_*` · 배치 호출기 polestar URL만 · 타임라인 kind `metric·alarm·change` · 상관 줄 라벨 `[인프라]` 고정 | `evidence_prefetch.py:31-33` · `mcp_tool_client.py:78-80` · `briefing_builder.py:45,211` |
| APM 사전수집 | `event.dbId`(`jennifer[_id]`)를 폴스타 source로 넘김 → mcp_server `_resolve_engine` 알 수 없는 소스 → 전 축 「결손」 **(추론)** | `evidence_prefetch.py:65` · `mcp_server/mcp_server/polestar_tools.py:689-694` |
| 사건 스냅샷 | `Episode.to_dict` 멤버에 alarm_name·kind·was_kinds·level 없음(`EpisodeAlarm`은 보유) · 상태는 워커 메모리(D-317 G-8) | `cross_source.py:118-133,289-312` |

### 2.3 노이즈 관제 (R-3)

| 지점 | 실측 | 근거 |
|---|---|---|
| 사유 표시 | 사유 문장(도메인 고정 한국어) · 단계 라벨·설명(`STAGE_LABELS`·`STAGE_DESCRIPTIONS` → `summary.stages[]`) · 단계 프로파일 질문+열 · `stageBasis` 근거 한 줄 · 드로어 「판단 요약」·「단계 근거」표·「신호 스냅샷」 · 크로스소스 `missing` 라벨 | `notification_policy.py:65-210` · `noise.js:981-1051,1279-1288,1441-1481,1680-1913,2008-2047` |
| 분석 텍스트 생성 | 게이트 **앞**에서 알람당 LLM 1회 → **SUPPRESS로 끝날 알람에도** summary·probable_cause·recommended_action 생성됨 | `alarm_graph.py:150-191` · `alarm_analyzer.py:297,362-366` |
| 분석 텍스트 저장 | `store.record(...)`에 분석 인자 없음 · 실패 흔적 `stage_evidence.analysis`만(라벨 없음 → 영문 키 노출) | `notification_gate.py:174-200` · `decision_store.py:112-127` |
| 분석 텍스트 행선지 | SSE `_tier_sse_payload`(TICKET·DASHBOARD · SUPPRESS는 플래그) · workb · incident payload — 관제 SSE 피드는 분석 필드를 버림 | `alarm_notifier.py:773-884` · `noise.js:866-875` |
| 사용자/운영자 | 같은 noise.js · `/noise/*` 읽기 5종(`require_user`) · `/admin/noise/*`(+운영자 표시) · `condition_log`·`created_by`는 운영자 경로만(fail-closed) · 문자열 마스킹 | `noise_dashboard.py:51-63,376-390,981-1000,1094-1121` |
| 응답 모델 | `DecisionsResponse.items: list[dict]` — 필드 가산이 자동으로 실림 | `noise_dashboard.py:185-187` |

---

## 3. 설계 원칙

1. **판정 불변** — 이 계획은 티어·사유·우선순위·단계를 바꾸지 않는다(설명·저장·조사 입력만). 판정 스냅샷 테스트(`noise_gate/tests/test_decision_snapshot.py`)가 비트 동일을 고정한다.
2. **결정적=판단·LLM=서술(D-035)** — 사유 요약·처리 안내·크로스소스 타임라인·규칙 가설은 결정적 템플릿/계산이다. LLM 산출(AI 추정 · 조사 서술)은 출처를 칸마다 표기하고 판정에 쓰지 않는다.
3. **출처 3층 표기** — 화면의 「왜·어떻게」는 ⓐ **판정 근거**(결정적) ⓑ **AI 추정**(알람 문구 기반 · 검증 안 됨) ⓒ **조사 브리핑**(도구 출력 인용 · `[가설]` 표기 유지)으로 나눠 보인다. 섞어서 하나의 문장으로 만들지 않는다.
4. **경계 유지(D-118·D-139)** — noise_gate ↔ sre_agent는 MCP JSON 계약만. 계약 확장은 `meta` 가산 키(수신 측이 이미 허용 — §2.2)로 하고 `contract_version`은 "1" 유지. 본체 API의 sre_agent 재조회는 기존 `SreAgentClient.poll`만 쓴다.
5. **WAS 판정은 게이트웨이(D-195 ②)** — sre_agent·noise_gate는 `was_signals[*].kind` 값을 옮기기만 한다.
6. **기본 off = 비트 동일** — 신규 동작은 플래그 뒤(§6.1). 웹 표시는 capabilities·데이터 유무로만 나타난다(데이터가 없으면 섹션 자체를 그리지 않는다).
7. **침묵 금지** — 조사 실패·타임아웃·거부·APM 도구 미연결은 상태와 사유를 화면과 브리핑 「한계」에 그대로 보인다.
8. **마스킹** — 자유 텍스트(AI 추정·브리핑)는 서버명·IP·명령줄을 담을 수 있다. 저장 시 길이 상한, 응답 시 기존 마스킹 경로(`_MASKED_TEXT_KEYS` 계열)를 탄다. 사용자 경로 노출 범위는 G-3.

---

## 4. 설계

### 4.1 조사 저장소 (R-1 기반 · G-1)

- 신규 `noise_gate/infrastructure/investigation_store.py` — JSONL append(`logs/alarm_investigations.jsonl` · 설정 `NOISE_INVESTIGATION_STORE_PATH`). 판정 감사(`alarm_decisions.jsonl`)와 파일을 분리해 관제 tail 스캔(`max_lines`)에 큰 레코드가 끼지 않게 한다.
- 레코드(조사 1건의 상태 전이마다 1행 · 읽을 때 최신 행이 이긴다):
  `{investigation_id, alarm_ids[], episode_id?, kind: "alarm"|"episode", status: submitted|running|done|failed|timeout|rejected|episode_existing, reason?, verdict?, briefing?, briefing_lines?[{label,text}], cross_source?, ts, source: "inline"|"followup"|"api_poll"}`
  - `briefing`은 sre_agent 6요소 dict 원본(상한 32KB · 넘으면 `limitations`에 「잘림」 추가 후 절단) · `briefing_lines`는 `render_briefing_lines` 결과(화면 공용).
- 쓰는 지점: ① 인라인 모드 — `investigation_trigger` 종결 직후 ② 후속 모드 — submit 직후 `submitted`, `_poll_until_terminal` 종결 시 `done`+브리핑 ③ API 지연 조회 — 저장소에 `submitted`만 있고 sre_agent poll이 `done`이면 결과를 기록(§4.2).
- **후속 poll과 workb 발송 분리(G-4)** — 현행은 workb 발송 성공 시에만 후속 태스크가 돈다(`alarm_notifier.py:507-508`). poll·저장은 항상 돌고, **workb 후속 발송만** 기존 조건을 유지한다. 동시 상한(`investigation_followup_max_inflight`)·타임아웃은 그대로.
- 조회: `get_by_investigation(id)` · `get_by_alarm(alarm_id)`(최신 1건 · 사건 조사면 `alarm_ids`에 포함된 것) · tail 스캔 상한 설정.
- `record_investigation`(판정 감사 쪽 상태 행)은 그대로 둔다 — 기존 소비처·테스트 보존.

### 4.2 조회 API (R-1 · G-3)

| 경로 | 인증 | 응답 |
|---|---|---|
| `GET /api/v1/alarm/investigations/by-alarm/{alarm_id}` | `require_user` + 존 검사(`_alarm_zone_scope`·`_zone_permits` — 판정 레코드의 `db_id`로 존 판정) | `{status, investigation_id, kind, episode_id, briefing_lines, cross_source, verdict_level, reason, updated_at, source}` · 없으면 `{status:"none"}` |
| `GET /api/v1/alarm/investigations/{investigation_id}` | 같음(조사 레코드의 `alarm_ids[0]` → 판정 레코드 `db_id`) | 같음 |
| `GET /noise/decisions/{alarm_id}` · `/admin/noise/decisions/{alarm_id}` | 기존(D-245) | 응답에 `investigation` 블록 가산(위 형태 요약) — 읽기 5종 목록은 늘리지 않는다 |

- 지연 조회: 저장소 상태가 `submitted|running`이면 API가 `SreAgentClient.poll`(요청마다 새 클라이언트 — `alarm_notifier.py:704-706` 주석의 세션 충돌 회피)을 1회 부르고 종결이면 저장(§4.1 ③). poll 실패는 저장소 상태 그대로 + `reason`. 호출 타임아웃 = `investigation_mcp_call_timeout_seconds`.
- 사용자 경로 응답은 `briefing_lines` 텍스트에 마스킹 적용, 운영자 경로는 원문(G-3 (a)). 원 `briefing` dict는 운영자 경로에만.
- `GET /alarm/capabilities`에 `investigation_enabled`(트리거 on AND 서비스 URL 설정) 가산.

### 4.3 웹 표시 (R-1 · G-2)

- **알람 카드**(`app.js renderAlarmMessage`): 카드 페이로드에 `investigation_id`가 있거나 capabilities `investigation_enabled`면 「조사 브리핑」 접이식 섹션을 둔다.
  - 상태 칩: 조사 중 / 완료 / 실패(사유) / 같은 사건 조사로 연결됨 / 없음.
  - 펼칠 때 API 지연 로드(카드 렌더 시 자동 호출하지 않는다 — 알람 폭주 시 호출 폭증 방지). 조사 중이면 15초 간격 재조회, 최대 `investigation_followup_timeout_seconds` 동안(G-2 (a)).
  - 본문: `briefing_lines`를 라벨/텍스트로 렌더(전부 `escapeHtml`) · `[가설]`·`[미결]` 접두는 배지로 · 「한계」는 항상 펼친 상태 · 권고 아래 human-gated 문구 유지.
  - 크로스소스 블록(§4.7)이 있으면 「두 소스 타임라인」 소표.
- **관제 드로어**(`noise.js renderDrawer`): `decisions/{alarm_id}` 응답의 `investigation` 블록으로 같은 섹션(공용 렌더 함수 1개 — 카드·드로어가 같은 함수, D-247 「공용 패널」 원칙). 판정 레코드 기반이라 PAGE 카드가 웹에 오지 않는 환경(incident 계측 off)에서도 여기서 보인다.
- 렌더 함수는 신규 정적 JS 1파일(`src/static/js/briefing-view.js`)로 두고 `index.html`·`noise.html`·`admin/noise.html`이 공유한다.

### 4.4 조사 페이로드에 사건 스냅샷 (R-2 · G-5)

- 플래그 `NOISE_INVESTIGATION_EPISODE_CONTEXT_ENABLED`(기본 off) AND 사건 추적기 존재(`NOISE_CROSS_SOURCE_MODE` ≠ off — shadow면 판정 불변으로 사건만 만든다)일 때 `meta.episode` 가산:
  ```
  meta.episode = {
    episode_id, zone, host_key,
    members: [{alarm_id, source, db_id, alarm_name, kind, was_kinds, level, severity,
               occurred_at, tier, resolved}],            # 상한 20 · 최근 순
    links:   [{rule_id, cause_alarm_id, effect_alarm_id, lag_seconds, action, missing}],
    rules:   [{id, cause:{source,kinds|was_kinds|alarm_names}, effect:{…},
               window:{cause_before_seconds, cause_after_seconds}}],   # 사건에 걸린 규칙만
    infra_db_ids: [...]                                   # 폴스타 사전수집 소스 후보
  }
  ```
- `Episode.to_dict` 멤버에 `alarm_name·kind·was_kinds·level` 가산(`EpisodeAlarm` 보유 값 · 관제 표시에도 쓴다). `links`는 `observe()` 신호를 사건에 누적 보관(상한 50)해 만든다 — 현재는 신호를 반환만 하고 사건에 남기지 않는다.
- `decision.evidence.cross_source`도 `meta.cross_source`로 옮긴다(이 알람 자신의 결합 판정).
- `contract_version` "1" 유지 — 수신 측은 meta 추가 키를 이미 허용(§2.2). 계약 테스트(`sre_agent/tests/test_payload_contract.py`)에 가산 키 케이스 추가.

### 4.5 사건당 1회 종합 조사 (R-2 · 시점 문제 · G-6)

**문제** — 원인 알람(폴스타 CPU)의 조사는 증상(제니퍼 큐잉)이 도착하기 **전에** 제출된다. 제출 시 스냅샷만으로는 반대편 소스가 비어 있다(`plans/91` 1-3 원안 폐기와 같은 시점 문제).

**설계(G-6 (a))** — ① 개별 조사는 지금처럼 제출하되 제출 시점 스냅샷을 싣는다. ② 사건에 **두 소스가 모두 들어오고 결합 판정(link 또는 demote)이 처음 성립하는 순간**, 그 사건에 대해 `kind="episode"` 종합 조사를 **1회** 제출한다.
- 조건: 플래그 on · 사건에 polestar·jennifer 멤버 각 ≥1 · 링크 ≥1 · 사건의 종합 조사 미제출 · 멤버 중 PAGE·TICKET 통보 ≥1(아무도 통보받지 않은 사건은 조사하지 않는다 — 예산 보호).
- 제출: 대표 알람 event + `meta.episode`(그 시점 전체) + `meta.investigation_kind="episode"`. 결과는 조사 저장소에 `alarm_ids`=사건 멤버 전부로 기록 → 어느 멤버의 카드·드로어에서도 같은 브리핑이 보인다.
- 기존 「제니퍼 알람발 조사 사건당 1회」(D-317 ⑦)는 유지하되, 종합 조사가 있으면 APM 후속 알람은 종합 조사 id로 연결(`episode_existing`)한다.
- 통보: 종합 조사 완료 시 workb 후속 메시지 1건(대표 알람 스레드 · `investigation_followup_enabled` 규칙 준용). 판정·티어는 바꾸지 않는다.
- 예산: dispatcher 동시 상한·시간당 예산(`investigation_hourly_budget`)·dedup TTL을 그대로 탄다. 사건 키 기반 지문(`episode:<id>`)으로 중복 제출 차단.

### 4.6 sre_agent 수신 확장 (R-2)

플래그 `CROSS_SOURCE_INVESTIGATION_ENABLED`(sre_agent settings · 기본 off). off면 `meta.episode`를 무시 → 비트 동일.

| 지점 | 변경 |
|---|---|
| 질문(`mcp_service._job_to_question`) | `meta.episode`에 두 소스 멤버가 있으면 크로스소스 질문: *"{host}에서 인프라(폴스타) 알람 {원인 후보}와 애플리케이션(제니퍼) 이벤트 {증상}이 한 사건으로 묶였다. 두 소스의 도구로 선후와 연결 근거를 확인하고, 규칙 가설이 맞는지 반증까지 검토하라. 모든 주장에 도구 출력을 인용하라."* — 규칙은 「가설」로만 제시 |
| 지침(`investigation_guidance.build_guidance`) | 혼합 사건이면 OS 플레이북 **과** APM 플레이북을 모두 붙인다(현행 대체 → 병기) · 사건 멤버 표(시각·소스·알람명·kind/was_kind) · 규칙 창 · apm 구간 도구 목록 · 「같은 호스트 키 strong 매칭만 묶였음」 고지 |
| 사전수집 소스(`evidence_prefetch.scope_from_job`) | APM 트리거면 `meta.episode.infra_db_ids[0]`를 폴스타 source로 쓰고, 없으면 폴스타 축을 **호출하지 않고** `notes`에 「인프라 소스 미상 — 폴스타 사전수집 생략」(현행 ValueError 결손 대체 · 플래그 무관 결함 교정 여부는 G-8과 별도로 §8 위험 R-4) |
| 결정적 타임라인(`domain/correlation.py`·`briefing_builder`) | 사건 멤버를 **추가 호출 없이** 타임라인 항목으로 편입: 새 kind `apm_event`(소스 라벨 `애플리케이션(APM)`) · 폴스타 멤버는 `alarm`(라벨 `인프라`) · 상대 시각 `T-` 동일 축 |
| 가설(`root_cause_hypotheses`) | 링크마다 규칙 기반 가설 1개: *"인프라 {kind} 알람이 {lag}초 선행 → 애플리케이션 {was_kind} (규칙 {rule_id})"* · confidence 상한 = 기존 상관 상한과 같음 · `CORRELATION_NOT_CAUSATION_NOTE` 병기 · LLM 인용이 이를 뒷받침하면 근거 줄에 표시, 반박하면 그대로 둘 다 보인다 |
| 한계 | `apm_mcp_url` 미설정 → *"애플리케이션(APM) 도구 미연결 — 인프라 근거와 사건 스냅샷만으로 판단"* 강제 · 멤버 상한 절단 · 매칭 weak 멤버 제외 사실 |
| 감사 | dispatcher 감사에 `episode_id`·`kind`·`cross_source_links` 수 |

### 4.7 브리핑 `cross_source` 블록 (R-2 → R-1 표시)

브리핑 dict에 선택 키 `cross_source` 가산(6요소는 그대로):
`{episode_id, host_key, sources: {polestar: n, jennifer: n}, apm_tools_connected: bool, apm_tools_called: n, links: [{rule_id, cause, effect, lag_seconds, supported_by_tools: bool|null}]}`
- `supported_by_tools`는 LLM 인용 줄에 양쪽 도구(`polestar_*`·`apm_*`) 출처가 모두 있으면 true, 한쪽만이면 false, 인용 없음 null — 결정적 판정(`evidence_source_label` 재사용).
- noise_gate `render_briefing_lines`에 「크로스소스」 라벨 줄 추가(채팅·workb·웹 공용).

### 4.8 관제 — 억제·강등 사유 보강 (R-3a · G-7 무관)

현행 사유 표시는 충분히 풍부하다. 부족한 것은 **한눈에 읽히는 요약**과 **「얼마나 내려갔나」**다.
- 드로어 머리에 **사유 요약 1문단** — 결정적 템플릿(`noise-help.js` 단계별 문장 + 근거 값 치환). 예:
  - 크로스소스: *"이 알람은 「크로스소스 사건 상관」 단계에서 PAGE → DASHBOARD로 강등됐습니다. 같은 서버(gpapp01)의 인프라 알람 「CPU 사용률」(10:00, PAGE 통보·미해소)이 원인으로 판단됐고, 이 알람은 그 270초 뒤 발생했습니다(규칙 host_resource_to_was_latency)."*
  - 인히비션: *"같은 서버의 상위 알람 「{알람}」(심각도 3, 2분 전)이 진행 중이라 이 알람은 음소거됐습니다."*
  - 묶기만(link): *"같은 사건으로 묶였지만 강등하지 않았습니다 — 빠진 조건: 호스트 매칭 신뢰도(제니퍼 정규식 매칭)."*
- 「원래 티어 → 최종 티어」 칩 — `stage_evidence.capped_from` · 매트릭스 `base_tier` 등 이미 있는 값만 쓴다(없으면 칩 생략). KPI·패널 필터에 「강등됨」 보조 필터(최종 < 원래인 레코드).
- `stage_evidence.analysis` 라벨 추가(현재 영문 키 노출 — `noise-help.js evidence` 표).
- 사유 요약 템플릿 정본은 `noise-help.js`(D-247 「화면 문구는 noise-help.js」) — 도메인 단계 설명(`STAGE_DESCRIPTIONS`)과 중복 서술하지 않는다(설명=규칙, 요약=이 알람).

### 4.9 관제 — 왜 발생했나·어떻게 처리하나 (R-3b · G-7 · G-8)

드로어에 「발생 원인과 처리」 섹션, 출처 3층(§3-3):

| 층 | 내용 | 출처·생성 | 저장 |
|---|---|---|---|
| ⓐ 처리 안내(결정적) | 판정 단계·티어별 고정 안내. 예: 유지보수 억제 → *"유지보수 창 안의 알람입니다. 조치 불필요 — 창 종료 뒤 재발하면 확인하세요."* · 크로스소스 강등 → *"원인 알람 「CPU 사용률」을 먼저 처리하세요. 원인이 해소되면 이 증상도 함께 사라질 것으로 예상합니다. 원인 해소 뒤에도 남으면 개별 조치가 필요합니다."* · 스톰 → *"대표 알람 {id} 처리 시 함께 확인"* · 플래핑 · 침묵 · 자가복구 · 매트릭스 PAGE(*"즉시 확인 대상"*) 등 전 단계 | 신규 도메인 표 `noise_gate/domain/stage_handling.py`(`STAGE_HANDLING` · `STAGE_DESCRIPTIONS`와 같은 정본 방식 · `summary.stages[]`로 내려보냄) + 근거 값 치환 | 저장 불요(판정 레코드에서 계산) |
| ⓑ AI 추정 원인·권고 | alarm_analyzer의 `summary`·`probable_cause`·`recommended_action` — **「AI 추정 · 알람 문구 기반 · 검증 안 됨」** 배지 · 분석 실패면 폴백 문구 대신 「AI 분석 실패」 상태만 | 기존 LLM 1회(게이트 앞) — **추가 호출 0** | 판정 레코드 가산 필드 `analysis{summary,probable_cause,recommended_action,failed}`(각 500자 상한 · 마스킹 대상 · 플래그 G-7) |
| ⓒ 조사 브리핑 | §4.3과 같은 섹션(있을 때만 · 크로스소스 블록 포함) | sre_agent | 조사 저장소 |

- 억제·강등 알람에서 ⓑ의 권고가 ⓐ와 어긋날 수 있다(분석은 판정 전에 만들어짐). 화면은 ⓐ를 위에, ⓑ를 아래에 두고 ⓑ 머리에 *"아래 AI 추정은 노이즈 판정 전에 알람 문구만으로 만든 것입니다"*를 붙인다 — 순서로 우선순위를 드러내고 문장을 합성하지 않는다(G-8).
- 옛 레코드(분석 필드 없음)는 ⓑ 칸에 「이 판단이 기록될 때는 AI 추정을 저장하지 않았습니다」.
- 패널 열 프로파일에 `cause`(AI 추정 원인 짧게 80자)·`handling`(처리 안내 짧게) 열 추가 — suppress·dashboard KPI 프로파일에 기본 포함.
- 관제 SSE 피드(운영자)도 분석 필드를 버리지 않고 행에 담는다(`noise.js:866-875`).

---

## 5. 기존 결정과의 충돌 검토

| 결정 | 관계 | 처리 |
|---|---|---|
| D-124 · D-137 | 후속 poll이 workb 발송에 묶임 → **분리**(G-4) · 웹이 새 브리핑 소비처 | 착지 시 D-137 부기(「poll·저장은 항상, workb 후속만 조건부」) |
| D-245 | 사용자 경로 읽기 5종 — `decisions/{id}` 응답 가산만 하고 엔드포인트는 늘리지 않음. 조사 조회는 `/alarm/*`(알람 뷰 경로 · 존 검사)에 둔다 | 충돌 없음 · 부기(사용자 경로에 분석 텍스트·브리핑 마스킹 노출 — G-3) |
| D-247 | 판정 레코드 가산 필드 · 운영자 전용 키 · 공용 패널 | 같은 방식(가산 · fail-closed) — 충돌 없음 |
| D-118 · D-139 | 경계 | `meta` 가산 키 + 기존 poll만 — import 0 유지(`sre_agent/tests/test_boundary.py`) |
| D-123 | 계약 버전 · 잡 TTL | `contract_version` "1" 유지 · 본체 저장소가 정본이라 TTL 무관 |
| D-317 | G-8 사건 상태 워커 메모리 | 영속화하지 않음 — 제출 시 스냅샷을 페이로드로 복사 · 종합 조사는 ⑦ 「사건당 1회」의 확장 → 부기 |
| D-195 ② | WAS 판정 단일 정의 | `was_kinds` 값 전달만 — 충돌 없음 |
| D-035 | LLM은 판정자 아님 | 처리 안내·사유 요약·크로스소스 가설은 결정적 · LLM 텍스트는 출처 배지 — 충돌 없음 |

**사용자 확인이 필요한 충돌은 없다.** 다만 D-137 동작 변경(G-4)과 D-245 노출 범위(G-3)는 게이트로 묻는다.

---

## 6. 플래그 · 설정

### 6.1 신규 플래그(전부 기본 off = 비트 동일)

| 키 | 위치 | 효과 |
|---|---|---|
| `NOISE_DECISION_ANALYSIS_TEXT_ENABLED` | `src/config.py` NoiseGateConfig | 판정 레코드에 `analysis` 가산(§4.9 ⓑ) |
| `NOISE_INVESTIGATION_STORE_PATH` | 같음 | 조사 저장소 경로(저장소 자체는 조사 트리거가 돌 때만 쓰임 — 트리거 off면 파일 생성 0) |
| `NOISE_INVESTIGATION_EPISODE_CONTEXT_ENABLED` | 같음 | `meta.episode`·`meta.cross_source` 가산 + 종합 조사(§4.4·4.5) |
| `CROSS_SOURCE_INVESTIGATION_ENABLED` | `sre_agent/sre_agent/settings.py` | 크로스소스 질문·지침·타임라인·가설·`cross_source` 블록(§4.6·4.7) |

- 설정 카탈로그(`src/api/settings_catalog.py` · `config/settings_help/noise_gate_investigation.yaml`)·`.env.example` 동반.
- 크로스소스 조사가 실제로 돌려면: `NOISE_ENABLE_NOISE_GATE` · `NOISE_INVESTIGATION_TRIGGER_ENABLED` · `NOISE_APM_NOISE_POLICY_ENABLED` · `NOISE_CROSS_SOURCE_MODE≥shadow` · `NOISE_INVESTIGATION_EPISODE_CONTEXT_ENABLED` · sre_agent `CROSS_SOURCE_INVESTIGATION_ENABLED` + `APM_MCP_URL`(+ 권장 `APM_GUIDANCE_ENABLED`). 매뉴얼 A-71에 체크리스트로 싣는다.

---

## 7. Wave

| Wave | 범위 | 산출 | 검증 |
|---|---|---|---|
| **W0** | 기준선 | 판정 스냅샷·관제 응답·조사 페이로드 flags-off 비트 동일 테스트 · 픽스처: 크로스소스 사건 + 브리핑 목업(`noise_gate/testdata/cross_source/` 재사용) · §2.2 APM 사전수집 소스 오류(추론)를 실패 테스트로 확정 | 신규 테스트만 |
| **W1** | R-3 (관제 설명) | 판정 레코드 `analysis` 가산(플래그) · `stage_handling.py` + `summary.stages[]` · noise.js 사유 요약·티어 칩·「발생 원인과 처리」 섹션(ⓐⓑ)·열 `cause`/`handling`·SSE 피드 보존 · `analysis` 라벨 | `test_decision_store*` · `test_stage_evidence` · `test_noise_dashboard_api` · 신규 `test_plan153_w1_*` · 사용자 경로 마스킹·`condition_log` fail-closed 회귀 |
| **W2** | R-1 저장·API | `investigation_store.py` · 인라인/후속 기록 · 후속 poll-workb 분리(G-4) · `/alarm/investigations/*` 2종 · `decisions/{id}` `investigation` 블록 · capabilities · API 지연 poll | `test_investigation_trigger` · `test_investigation_followup` · 신규 API 테스트(존 403·마스킹·none·poll 실패) · FakeSreClient |
| **W3** | R-1 웹 | `briefing-view.js` 공용 렌더 · 카드 섹션(지연 로드·재조회 상한) · 관제 드로어 ⓒ · CSS | JS 단위(기존 방식) · 실 프로세스 종단(가짜 sre 서비스) 1회 · 스크린샷 |
| **W4** | R-2 noise_gate | `Episode` 멤버 필드·링크 누적 · `meta.episode`·`meta.cross_source` · 종합 조사 트리거(조건·지문·1회) · `episode_existing` 연결 · 조사 저장소 `alarm_ids` 다중 | `test_plan144_w4` 확장 · 신규 `test_plan153_w4_*` · 리플레이 하네스에 종합 조사 발주 수 집계 |
| **W5** | R-2 sre_agent | 질문·지침 병기·사전수집 소스·`apm_event` 타임라인·규칙 가설·한계·`cross_source` 블록·감사 | `sre_agent/tests` 신규 `test_cross_source_investigation.py` · `test_payload_contract` 가산 키 · `test_briefing_builder` · 경계 테스트 |
| **W6** | 통합·문서 | 웹에 크로스소스 소표 · `render_briefing_lines` 「크로스소스」 줄 · 가짜 LLM e2e(폴스타 CPU → 제니퍼 큐잉 → 종합 조사 → 웹 조회) · **MLX 대표 1회**(두 평면 mlx 확인 뒤) · 매뉴얼 · 결정 등재 | 모듈 단위 회귀(§9) |
| W7(선택 · G-9) | 운영자 수동 조사 요청 | 관제 드로어 「조사 요청」 버튼(운영자 경로만 · dispatcher 예산 공유 · 감사) | — |

의존: W1 독립 · W2 → W3 · W4 → W5 → W6(W3 필요). W1과 W2·W4는 병렬 가능.

---

## 8. 위험

| ID | 위험 | 대응 |
|---|---|---|
| R-1 | 자유 텍스트(AI 추정·브리핑)에 호스트명·IP·명령줄 → 사용자 화면 노출 | 저장 상한 · 응답 마스킹 · 사용자 경로는 원 dict 제외 · G-3 |
| R-2 | 종합 조사 폭증(알람 폭풍 시 사건 다수) | 사건당 1회 · 통보 멤버 ≥1 조건 · dispatcher 예산·동시 상한 · 리플레이로 발주 수 측정 |
| R-3 | 규칙 가설이 LLM 서술보다 단정적으로 읽힘 | 가설 줄에 규칙 id·「상관≠인과」 병기 · confidence 상한 · `supported_by_tools`로 도구 뒷받침 여부 표시 |
| R-4 | §2.2 APM 사전수집 소스 오류는 플래그와 무관한 기존 결함일 수 있음 | W0에서 실패 테스트로 확정 → 확정되면 플래그 밖 결함 교정으로 처리(사용자 보고) |
| R-5 | 판정 레코드 크기 증가 → 관제 tail 스캔 성능 | 분석 3필드 각 500자 · 브리핑은 별도 파일 · W1에서 `max_lines` 스캔 시간 측정 |
| R-6 | 웹 카드 재조회가 sre_agent에 부하 | 펼칠 때만 · 15초 간격 · 상한 · API가 저장소 우선, 미종결일 때만 poll |
| R-7 | 운영 제니퍼 이벤트 모양 미확정(`plans/87` J0-L-b) → 사건 결합이 운영에서 드묾 | 크로스소스 조사는 사건 추적기에 의존 — `plans/144` W6 shadow 결과로 실효 확인 · 단독 조사는 그대로 동작 |

---

## 9. 검증 · 회귀 · 매뉴얼

- 회귀(D-303): Wave 중간은 자기 테스트 + `regress.py --no-tests --files …` · 계획 끝 1회 `python scripts/regress.py --base 3c4adab --files <회귀 대상>` · `src/config.py` 변경 Wave(W1·W2·W4)는 `[웨이브 회귀 필요]` 규칙. `sre_agent`는 자체 `.venv`로 `pytest sre_agent/tests`. 전체 회귀는 사용자 요청 시만.
- 품질 게이트: `arch_check --ci`(noise_gate 계층: `stage_handling`=domain · `investigation_store`=infrastructure) · `overfit_check --ci`(`noise_gate/domain`·`sre_agent/sre_agent/domain` 스캔 — 처리 안내 문구에 폴스타 스키마 리터럴 금지) · 경계 테스트 2종.
- 과금: 실 LLM은 MLX 대표 1회(W6) — `python -m scripts.bench --show-env`로 두 평면 mlx 확인 뒤. sre_agent 실 조사도 로컬 MLX 경로만.
- **매뉴얼(D-255)**: U-40(알람 카드 — 조사 브리핑 섹션) · U-44(관제 화면 읽기 — 사유 요약·발생 원인과 처리) · U-45(걸러지는 원리 — 처리 안내) · A-42(사용자 화면과 다른 점 — 마스킹·원문) · A-47(운영 판단 요령) · A-71(크로스소스 — 조사 연계 체크리스트) · 필요 시 신설 항목(조사 브리핑 읽는 법). 절차: `features.yaml` → `content/*.md` → 캡처 → build → `pytest tests/test_manual`.

---

## 10. 게이트 — 사용자 결정

| ID | 질문 | 선택지 | 권고 |
|---|---|---|---|
| **G-1** | 브리핑 저장 위치 | (a) noise_gate 별도 JSONL `logs/alarm_investigations.jsonl` (b) 판정 감사 파일에 본문 가산 (c) PG 테이블 (d) sre_agent 잡 영속화만 | **(a)** — 관제 tail 스캔과 분리 · 기존 JSONL 감사 방식 · 폐쇄망 PG 의존 없음 |
| **G-2** | 웹 갱신 방식 | (a) 펼칠 때 지연 로드 + 조사 중 15초 재조회(상한) (b) SSE `investigation_update` 이벤트 | **(a)** — SSE 브리지·incident 계측이 기본 off라 (b)는 도달 조건이 좁다 |
| **G-3** | 사용자 경로 노출 | (a) 사용자 경로도 보임 — 마스킹·존 검사, 원 dict는 운영자만 (b) 운영자 경로만 | **(a)** — 요건이 「웹 UI에서」이고 알람 카드는 사용자 화면 |
| **G-4** | 후속 poll과 workb 분리(D-137 동작 변경) | (a) poll·저장은 항상, workb 후속 발송만 조건부 (b) 현행 유지(workb 없으면 웹에도 안 옴) | **(a)** |
| **G-5** | 크로스소스 조사의 사건 원천 | (a) noise_gate 사건 추적기(`NOISE_CROSS_SOURCE_MODE≥shadow`) 필요 (b) sre_agent가 apm 조회로 사건을 독자 재구성 | **(a)** — 결합 판정 단일 출처 · shadow는 판정 불변 |
| **G-6** | 시점 문제(원인 조사가 증상 도착 전 제출) | (a) 제출 시 스냅샷 + 결합 성립 시 사건당 1회 종합 조사 (b) 스냅샷만 (c) 원인 조사 제출을 창만큼 지연 | **(a)** — (c)는 PAGE 조사 지연, (b)는 반대편 소스가 비기 쉬움 |
| **G-7** | AI 추정 원인·권고 저장 | (a) 전 티어 저장(추가 LLM 0) · 500자 상한 · 플래그 기본 off (b) PAGE·TICKET만 (c) 저장 안 함(조사 브리핑만) | **(a)** — 억제 알람의 「왜」를 보일 유일한 재료 |
| **G-8** | 처리 방법 표시 구성 | (a) 결정적 단계별 처리 안내(위) + AI 추정(아래) + 브리핑, 출처 배지 (b) AI 추정만 (c) LLM으로 한 문단 합성 | **(a)** — D-035 · 억제 알람에서 AI 권고와 판정이 어긋나는 문제를 순서로 해소 |
| **G-9** | 운영자 수동 조사 요청 버튼(W7) | (a) 이번 계획에 포함 (b) 제외(후속 계획) | **(b)** — 억제 알람 조사는 비용·예산 정책이 따로 필요 |

**확정(2026-10-12)**: 사용자 「150번 계획을 구현하라」 → G-1 (a) · G-2 (a) · G-3 (a) · G-4 (a) · G-5 (a) · G-6 (a) · G-7 (a) · G-8 (a) · G-9 (b). W7은 이번 계획에서 제외한다.

---

## 10.1 잔여 (2026-10-12 · W0~W6 착지 뒤)

- **MLX 대표 1회 미실행** — `python -m scripts.bench --show-env`가 워커 `ollama` · 오케스트레이터 `vllm`로 해석(이 체크아웃엔 루트 `.env` 없음). 두 평면 `mlx` 확인 뒤 바뀐 프롬프트(sre_agent 크로스소스 질문·지침) 대표 1회.
- **마스킹 한계** — 사용자 경로 마스킹은 `DataMasker.SENSITIVE_VALUE_PATTERNS` 값 전체 일치라 AI 추정·브리핑 서술 속 호스트명·IP·명령줄은 가려지지 않는다(매뉴얼 A-42 고지 · 근본책은 서술문 부분 치환 — 후속 결정 필요).
- **증상 선착 사건** — 증상(제니퍼)이 원인(폴스타)보다 먼저 오면 `observe()` 신호에 링크가 없어 종합 조사가 나가지 않는다.
- **조사 예산** — 증상 단독 조사도 두 소스 스냅샷으로 크로스소스 조사가 되고, 그 직후 종합 조사가 1회 더 나간다(R-2 · 내부망 shadow 리플레이로 발주 수 측정 필요).
- **break-glass 운영자 토큰** — `/alarm/investigations/*`·`/alarm/capabilities`는 `require_user`라 401 → 관제 드로어는 `decisions/{id}`의 저장소 블록으로 대체(지연 poll 없음 · 재조회 상한 300초 고정).
- **「강등됨만」 필터** — 클라이언트 전용(지금 쪽 50건 안).
- **매뉴얼 캡처** — 신규 2장(`alarm-briefing.png`·`noise-drawer-cause.png`)만 찍음 · 다음 전체 재캡처 때 예시 판정 2건이 관제 KPI에 섞인다.
- **테스트 도구** — 이 인터프리터엔 pytest-xdist가 없어 `regress.py`를 직렬 래퍼로 돌렸고, 체크아웃에 `sre_agent/.venv`가 없어 regress의 sre_agent 패키지 단계는 「미실행」 → 형제 체크아웃 venv로 따로 돌림(836 passed · 3 skipped).

## 11. 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-10-12 | **v1.0 신규(`-TODO`)** — 사용자 요청 3요건(웹 브리핑 · HolmesGPT 크로스소스 · 관제 사유·원인·처리) · 현행 실측 3갈래(§2) · 설계 §4.1~4.9 · 충돌 검토(사용자 확인 필요 충돌 없음 · D-137·D-245·D-317 부기 예정) · 플래그 4종 기본 off · Wave W0~W6(+W7 선택) · 게이트 G-1~G-9 · D-325 예약 · 코드 0 |
| 2026-10-12 | **v1.1(`-TODO` → `-WIP`)** — 게이트 G-1~G-9 권고안 확정(사용자 「150번 계획을 구현하라」) · W7 제외(G-9 (b)) · D-325 본문 등재 · 구현 착수 |
| 2026-10-12 | **v1.2 — W0~W6 구현(커밋 없음)** — W0 기준선 골든·R-4 확정(플래그 무관 기존 결함 · `EVIDENCE_CORRELATION_ENABLED`만으로 재현) · W1 판정 레코드 `analysis`·`stage_handling.py`·드로어 사유 요약·티어 칩·「발생 원인과 처리」 ⓐⓑ · W2 조사 저장소·`/alarm/investigations/*`·후속 poll-workb 분리(D-137 ④ 부기) · W3 `briefing-view.js` 카드·드로어 ⓒ(실 렌더 확인) · W4 `meta.episode`·사건당 1회 종합 조사 · W5 sre_agent 크로스소스 질문·지침 병기·`apm_event` 타임라인·규칙 가설·`cross_source` 블록·R-4 교정 · W6 「크로스소스」 줄·조회 우선순위·종합 조사 후속 표지·가짜 LLM e2e·매뉴얼(U-40·44·45·57 신설·A-42·47·71). 검증 라운드 교정: 종합 조사 같은 호스트 가드 키 분리·기준시각 = 최신 멤버(sre_agent · 플래그 on) · 카드 by-alarm 조회 · 거부·실패 종합 조사에 제니퍼 후속 미연결 · 존 RBAC fail-closed(제한 스코프 db_id 미상 403 · 멤버 전부 검사 · 레코드 `alarm_db_ids`) · 지연 poll 10초 스로틀·id별 락·종결 중복 쓰기 차단 · 사유 보존 · 프롬프트 문자열 정리. MLX 미실행(두 평면 mlx 미확인) |
