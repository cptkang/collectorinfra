# 194. 알람 조사 수동 전용(선별 실행) 전환 · 조사로 인한 행(hang) 대책

> **작성일**: 2026-10-26 · **v1.0**
> **상태**: **WIP** — W0~W6 구현 완료(2026-10-26 · 커밋 없음) · W0 로컬 재현(§1.4 — 30k줄·진행 40건·탭 5에서 루프 최대 정지 121s → W2 후 27ms) · 검증 2라운드 Critical 0 · 계획 끝 모듈 단위 회귀 실패 귀속 0(§8.1.1) · **남은 것은 내부망 확인**(S7 스택 덤프 · DB2 쿼리 timeout · 본체 RSS R12)과 후속 잔여(§8.2)
> **계획 시작 SHA**: `2f64aa9c`
> **회귀 대상 파일**: W1 `src/config/alarm.py` · `src/api/routes/{alarm,alarm_schemas}.py` · `src/domain/investigation_reject_reasons.py`(신규) · `src/nodes/fault_diagnosis.py` · `noise_gate/application/nodes/notification_gate.py` · `config/settings_help/noise_gate_investigation.yaml` · 테스트 `tests/test_api/{test_plan194_w1_manual_only,test_alarm_capabilities,test_plan153_w2_investigation_api,test_plan187_w11_manual}.py` · `tests/test_nodes/test_plan187_w12_fault_diagnosis.py` · `tests/test_semantic_routing/test_fault_diagnosis.py` · `noise_gate/tests/test_plan153_w0_baseline.py` | W2 `noise_gate/infrastructure/{jsonl_tail(신규),investigation_store,decision_store}.py` · `src/api/investigation_live_hub.py` · `tests/test_api/test_plan194_w2_{store_tail,hub_notify}.py` | W3 `sre_agent/sre_agent/{settings,diagnosis}.py` · `application/{investigation_dispatcher,investigation_jobs}.py` · `infrastructure/mcp_tool_client.py` · `interface/mcp_service.py` · `sre_agent/.env.example` · 테스트 `sre_agent/tests/{test_plan194_w3_load_limits,test_mcp_tool_client,test_plan188_w4_markup,test_plan190_apm_timeout}.py` | W4a `mcp_server/{config.toml,.env.example}` · `mcp_server/mcp_server/{config,db}.py` · `mcp_server/tests/test_plan194_w4a_limits.py` | W4b `apm_gateway/.env.example` · `apm_gateway/apm_gateway/{config.py,adapters/throttle.py,adapters/jennifer/client.py,interface/config_admin_adapter.py}` · `apm_gateway/tests/test_plan194_w4b_inflight.py` | 검증·교정 `src/domain/investigation_reject_reasons.py`(`reject_display_text`) · `src/nodes/fault_diagnosis.py` · `src/api/routes/alarm.py` · `sre_agent/sre_agent/application/investigation_dispatcher.py` · `apm_gateway/apm_gateway/config.py` · 검증 테스트 `tests/test_api/test_plan194_verify_w1.py` · `noise_gate/tests/test_plan194_verify_jsonl_tail.py` · `sre_agent/tests/test_plan194_verify_{dispatcher_stress,global_limit}.py` · `mcp_server/tests/test_plan194_verify_limits.py` · `apm_gateway/tests/test_plan194_verify_slots_stress.py` | W5 백엔드 `src/infrastructure/investigation_request_limit.py` · `noise_gate/infrastructure/sre_agent_client.py` · `src/api/{investigation_service_health(신규),investigation_live_hub,investigation_poll,server}.py` · `src/api/routes/{alarm,noise_dashboard,admin_investigation(신규)}.py` · `noise_gate/application/nodes/alarm_notifier.py` · `sre_agent/sre_agent/{settings.py,application/investigation_dispatcher.py}` · `sre_agent/.env.example` · `sre_agent/scripts/investigation_bench/runner.py` · W5 UI `src/static/js/{briefing-view,diagnosis-view,app,noise,admin}.js` · `src/static/css/{style,noise-console}.css` · `src/static/{index,noise}.html` · `src/static/admin/{noise,dashboard}.html` · W5 테스트 `tests/test_api/test_plan194_w5_api.py` · `tests/test_api/test_ui_plan194_w5_{portal,noise_admin}.py` · `noise_gate/tests/test_plan194_w5_manual_only_notice.py` · `sre_agent/tests/{test_plan194_w5_health_budget,test_config_admin,test_bench_runner}.py` · `tests/test_api/{test_plan153_w2_investigation_api,test_plan187_w11_manual,test_plan160_static_assets,test_ui_plan187_w15_diagnosis,test_ui_plan187_w18_event_search,test_plan153_w3_briefing_view}.py` · `tests/{test_plan187_fe2_static,test_plan187_w5b_static}.py` | W6 `scripts/manual/{capture.py,captures.yaml,features.yaml}` · `scripts/manual/content/{user,admin}.md` · `scripts/manual/fixtures/alarms/{inv_start,inv_queued}.json` · `src/static/manual/{user.html,admin.html,help_index.json}` · 검증 `tests/test_api/test_plan194_verify_w5_{api,ui}.py` · `noise_gate/tests/test_plan194_verify_w5_notice.py`
> **사용자 제기(2026-10-26)**: *"장애진단이 자동으로 진행되어 서버에 행이 걸리는 이슈가 있다. 선별적으로 진행해야 한다. 이를 위해 UI 개선과 기능 개선을 진행하라."*
> **사용자 확정(2026-10-26)**: ① 선별 방식 = **수동 전용**(자동 조사 없음 · 사람이 고른 알람만 · 새벽 알람은 통보만 나가는 것을 감수) ② 조사 시작 = 그 알람 존 권한 사용자(현행 「이 알람 분석」 인가·사용자별 시간당 상한 유지) · 설정·규칙 편집 = 관리자 ③ 행 지점 = 본체 웹 포털 · 조사 서비스(sre_agent) · 조회 대상(폴스타 DB·제니퍼·MCP) 세 곳 모두
> **관련 결정**: **D-347**(이 계획 — 신규) · D-341 ⑦(PAGE 자동 트리거 → 개정)·㉑~㉔(이 알람 분석·합류·상한) · D-124(트리거 배선) · D-137(즉시 통보 + 후속 브리핑) · D-325(조사 브리핑 웹 표시) · D-335 ⑥(사용자별 상한) · D-344(APM 호출 지연) · D-346(본체 reload — 같은 증상 계열 · 「남는 CPU는 JSONL 재파싱 등 별도 실측」 주의 항목을 이 계획이 받는다) · D-162(기본 off 원칙 — 예외 근거 §5) · D-255(매뉴얼 동반)
> **관련 실수**: `docs/18` 2026-10-12 「종합 조사가 sre_agent 같은 호스트 가드·구간을 통과하는지 확인하지 않음」(수신 측 가드 전수 표) · 2026-10-19 「사용자 요청 경로마다 열람 인가를 따로 씀」(종류 × 사용자 유형 표)
> **겹치는 계획**: `plans/192`(W3 `_live_scope` — 진행 중 목록에 본인 접수 `kind=manual` 포함) · `plans/193`(W1 `noise.js` mount 리팩터). §6 참조.

---

## 1. 행 원인 — 세 지점 (W0 · 코드 실측 2026-10-26 · HEAD `2f64aa9c`)

판정: **확정** = 코드 근거 · **측정** = 로컬 재현 수치(§1.4) · **미확정** = 추정.

### 1.1 본체 웹 포털 (`src/api/` · `noise_gate/infrastructure/`)

| # | 원인 | 근거 | 판정 |
|---|---|---|---|
| B1 | 조사·판정 저장소 읽기가 **매 호출 파일 전체 순회**(+ 끝 2000/20000줄 json 파싱) · 회전 없음(append만)이라 시간이 갈수록 비용 증가 · **이벤트 루프 위 동기 실행**(to_thread 0건) — AlarmWorker도 같은 루프 | `investigation_store.py:486-507` `_tail` · `decision_store.py:711-751·1302-1324` · `server.py:680` | 확정 |
| B2 | 실시간 허브: 미종결 조사 A건마다 폴러 1개(전역 상한 없음) · 5초마다 `_tail` 1+A회 · poll마다 SSE 세션 새로 연결 | `investigation_live_hub.py:259-280·473-491` · `investigation_poll.py:111-128` | 확정 |
| B3 | 허브가 **변화 없어도 poll마다 `_notify()`** → 열린 탭마다 깨어나 A행 목록 재생성 → 비용 ≈ 탭 수 × A² | `investigation_live_hub.py:529·779-807` | 확정(구조) |
| B4 | 알람 카드가 펼친 카드마다 15초 by-alarm 재조회 · 존 제한 사용자는 알람마다 판정 저장소 전체 파싱(존 검사) | `briefing-view.js:23` · `alarm.py:1162-1173` | 확정 |
| B5 | 자동 조사 인라인 모드(후속 off 기본)는 PAGE 알람마다 워커가 최대 45s 정지(알람 직렬 처리) · 후속 모드도 connect/initialize가 `wait_for` 밖 | `investigation_trigger.py:347-356·682-686` · `alarm_worker.py:770` | 확정 / connect 상한은 미확정 |
| B6 | 종결 기록 `clip_briefing` 반복 직렬화 O(n²) · 활동 요약 보관소 회전 시 전체 재작성 | `investigation_store.py:122-147·219-267` · `investigation_activity_store.py:118-131` | 확정(빈도 낮음) |

### 1.2 조사 서비스 (`sre_agent/`)

| # | 원인 | 근거 | 판정 |
|---|---|---|---|
| S1 | **타임아웃된 조사 스레드가 멈추지 않는다(좀비)** — 슬롯·호스트 키는 즉시 반환. 좀비가 LLM(요청당 최대 600s · 40 step)·도구를 계속 부른다 → 동시 상한 2를 넘는 실부하 · 단일 LLM 서버가 느려질수록 타임아웃 → 좀비 증가 → 더 느려지는 되먹임 | `investigation_dispatcher.py:720-748·1820-1860·1561-1621` · `diagnosis.py` `_StreamDrain` | 확정(구조) · 측정 §1.4 |
| S2 | **대기열 상한 없음** — 접수마다 스레드 즉시 생성 · 세마포어 무기한 대기 | `investigation_dispatcher.py:586-611·754-783` | 확정 |
| S3 | 시간당 예산·dedup TTL 기본 None(꺼짐) · `sre_diagnose`는 지문 없음 | `settings.py:134·138` · `_apply_sync_guards` | 확정 |
| S4 | LLM 동시 상한 없음(parallel 모드 1건당 심층 1 + 소견 4 + 종합 1) · 소견 60s/종합 90s는 **대기 상한일 뿐 요청을 끊지 않음** | `mcp_service.py:393-395` | 확정 |
| S5 | 접수 즉시 거부(가드)도 응답은 항상 `accepted` — 호출자는 poll해야 거부를 안다 | `investigation_jobs.py:352-408` | 확정 |
| S6 | 폴스타 MCP FAILED면 `_agent_getter`가 전역 lock 안에서 호출마다 에이전트 재생성 | `mcp_service.py:229-252` | 확정(조건부) |
| S7 | status 조회까지 무응답이 되는 실체(GIL 경합·소켓) | — | 미확정 — 내부망 SIGUSR1 스택 덤프로 확정(`run_service.py:39-40`) |

### 1.3 조회 대상 (`mcp_server/` · `apm_gateway/` · sre_agent 호출부)

| # | 원인 | 근거 | 판정 |
|---|---|---|---|
| T1 | 조사 1건 수집 단계 = 스텝 워커 4 × 배치 동시 4 = **최대 16건 동시**(폴스타·제니퍼 합산) × 동시 조사 수 + 좀비분 · 세마포어가 배치 지역 변수라 프로세스 전역 상한 없음 | `playbook_runner.py:109` · `mcp_tool_client.py:121-129` · `settings.py:292-295` | 확정 |
| T2 | mcp_server: 요청 상한 0 · PG 풀(소스당 3~5) `acquire` 대기 timeout 없음 → MCP 안 무한 줄 · **DB2는 풀 없음·쿼리 timeout 미적용** | `mcp_server/db.py:56-60·178·182-222` · `__main__.py:57` | 확정 |
| T3 | apm_gateway: 스로틀은 시작 간격(5/s)만 · in-flight 상한 없음(httpx 기본 100) · 동기 도구 호출은 job 슬롯 밖 · X-View 결과 캐시 없음 | `throttle.py` · `client.py:124·227` · `call_context.py:51` | 확정 |
| T4 | 잡 사이 캐시 없음 · 키에 알람별 `reference_time` → 같은 서버 알람 N건이 같은 SQL·X-View를 N번 | `tool_result_cache.py` · `playbook_runner.py:597` | 확정 |
| T5 | 클라이언트 timeout(60s)으로 버린 뒤 서버 쪽 쿼리·제니퍼 연쇄 호출이 계속 도는지 | — | 미확정(FastMCP 취소 전파 미확인) |

### 1.4 로컬 재현 수치

기준 `2f64aa9c` 분리 worktree · 가짜 sre·가짜 diagnose(실 LLM·외부 호출 0) · 맥북 웜 페이지 캐시 · 줄 구성 상태 행 1.3KB 80% + 브리핑 행 28.8KB 20%(실 브리핑 행은 최대 ~64KB라 실제가 더 클 수 있음). 스크립트는 세션 scratchpad `w0-repro/`(커밋 대상 아님).

**R1 · `_tail` 1회 비용(B1)** — 2,000줄(13.6MB) 36ms · 10,000줄(67.8MB) 115ms · 30,000줄(203.5MB) 304ms. 끝 2000줄 파싱 비용은 일정하고 줄 수에 비례 → 비용 대부분이 파일 전체 순회. `active_records(fresh)`(=`_tail` 1+A회) 30,000줄에서 A=5 1.8s · A=20 5.9s · **A=40 11.3s**.

**R2 · 이벤트 루프 지연(B2·B3)** — 실제 `InvestigationLiveHub`+`stream_events` 소비자 S개 · poll 5s · 20ms sleep 드리프트.

| 줄 · A · S | 기준(`2f64aa9c`) 최대 정지 · 100ms↑ 누적 | W2 적용 후 최대 정지 · 누적 |
|---|---|---|
| 30k · 5 · 5 | 1.78s · 20.1s/60s | **21.6ms · 0s** |
| 30k · 20 · 5 | **59.3s** · 88.4s/106.6s | **31.7ms · 0s** |
| 30k · 40 · 5 | **121.2s**(루프 한 번도 못 돔) · 121.2s | **26.8ms · 0s** |
| 10k · 20 · 5 · poll 1s | 21.9s · 33.8s | (미측정) |

- 기제(측정 + 코드): `active_records` 캐시는 계산 시작 시각으로 찍혀 계산이 `poll_seconds`보다 길면 매번 만료 → poll마다 `_notify`가 S개 스트림을 모두 깨움 → 스트림마다 (1+A)회 `_tail`. 30k·A40·S5에서 `_tail` 410회 × 0.29s ≈ 120s로 셈이 맞는다. poll 주기를 줄이면 더 나빠진다.
- W2 후 `_tail` 최대 4.3ms(증분 tail) · 같은 창에서 스트림당 프레임 3 → 64(루프가 돌아 실제로 전달).

**R3 · sre_agent 좀비 스레드(S1·S2)** — 일반 경로 `max_concurrent=2` · timeout 2s · 가짜 diagnose 10s sleep · 서로 다른 호스트 20건 submit(3.4ms 만에 전부 반환 · 워커 스레드 20개 즉시 생성). 슬롯은 2인데 **살아 있는 조사 스레드 최대 10**(= 상한 × sleep/timeout). diagnose가 600s(사실상 행)면 t=22s에 잡 20건 모두 timeout · 슬롯 0 · **좀비 20**(선형 증가). 같은 호스트 가드: 동시 2건째 `rejected/host_investigation_in_flight`, 1건째가 timeout으로 확정되면 3건째가 통과해 같은 호스트 조사 스레드 2개가 동시 생존. → W3(좀비가 슬롯·호스트 키 유지 · 대기열 상한)의 근거.

**R4 · 인라인 모드 워커 정지(B5)** — 코드 근거만(미측정): `alarm_worker.py:770` 순차 `await` → `investigation_trigger.py:347` `wait_for(..., 45s)` → 조사 대상 알람 1건마다 워커 최대 ~45s 정지. 수동 전용 기본에서는 트리거가 꺼져 발생하지 않는다.

**한계**: 콜드 캐시·운영 디스크·네트워크 지연·HTTP 라우트 계층의 추가 `_tail`은 넣지 않았다 — 운영 비용은 이보다 크거나 같다.

### 1.5 요약

자동 조사가 알람 폭주를 **조사 폭주로 증폭**하고(PAGE마다 1건 · 상한 없는 대기), 조사 서비스는 타임아웃된 조사를 멈추지 못해 실제 동시 수가 상한을 넘으며(S1), 그 조사들이 조회 대상에 16배로 동시 호출을 보낸다(T1). 본체는 조사 수에 비례해 저장소 전체 파싱·poll·스트림 재생성을 이벤트 루프 위에서 한다(B1~B3). **수동 전용(D-347 ①)이 증폭기를 끊고**, W2~W4가 남은 경로(여러 사용자의 수동 조사·조회 화면)를 상한으로 막는다.

## 2. 수정 방향

1. **수동 전용 기본** — 자동 트리거(`investigation_trigger_enabled`)는 코드 기본 이미 off · 사건 종합 조사는 trigger에 묶여 함께 off(`episode_coordinator.py:239`). 걸림돌은 「조사 가능」 신호가 trigger에 묶인 것: capabilities `investigation_enabled = gate ∧ trigger ∧ url`(`alarm.py:1086-1092`)이라 trigger off면 알람 카드의 조사 섹션·「분석 요청」이 사라진다. 수동 경로의 클라이언트·저장소는 라우트가 스스로 만들어 trigger와 무관하다(실측 — 착수 전 예상 「조사 클라이언트·저장소가 함께 죽는다」는 워커 전용 경로에만 해당).
2. **조사 서비스 상한** — 대기열 상한 · 좀비가 슬롯·호스트 키를 붙듦(유예 후 강제 반환) · 협조적 중단 · 전역 도구 동시 상한 · 소견 LLM timeout 전달 · 접수 응답에 즉시 거부 · 대기 순번 상시 노출 · 상태 게이지.
3. **본체 조회 비용** — 경로 단위 증분 tail(추가 바이트만 읽기) · 허브 무변화 깨움 제거.
4. **조회 대상 보호** — mcp_server 소스별 동시 상한·대기 timeout · DB2 쿼리 timeout · apm_gateway 소스별 in-flight 상한.
5. **넘친 요청은 사유를 보인다** — 수동 접수 응답 `reason_text`(한국어) · UI 표시(W5).
6. **통보** — 수동 전용이면 PAGE 통보에 브리핑이 붙지 않는다(본문은 조사 없는 통보와 같음). 「웹에서 조사 시작」 안내 한 줄 추가 여부는 **Q-1**(사용자 결정).

## 3. Wave

| Wave | 내용 | 대상 파일 | 상태 |
|---|---|---|---|
| **W0** | 원인 분석(§1) · 로컬 재현 수치(§1.4) | 없음(scratchpad) | 완료 — 수치 §1.4(W2 전후 대조 포함) |
| **W1** | 수동 전용 기본 — `investigation_manual_enabled` 기본 **true**(D-162 예외) · capabilities `investigation_enabled = gate ∧ (trigger ∨ manual) ∧ url` + `investigation_auto_enabled`·`investigation_manual_enabled` · 수동/추가 조사 접수 거부 `reason_text` · 설정 도움말 | `src/config/alarm.py` · `src/api/routes/alarm.py`(capabilities·접수 응답 — `_live_scope` 제외) · `config/settings_help/noise_gate_investigation.yaml` · 테스트 | 완료 |
| **W2** | 본체 조회 비용 — `noise_gate/infrastructure/jsonl_tail.py`(경로 단위 증분 tail) · 조사·판정 저장소 tail 교체(반환 의미 비트 동일) · 허브 변화 있을 때만 notify(+ 필요 시 30초 하트비트) | `noise_gate/infrastructure/{jsonl_tail,investigation_store,decision_store}.py` · `src/api/investigation_live_hub.py` · 테스트 | 완료 |
| **W3** | sre_agent 상한 — `investigation_max_queued`(4) · 좀비 슬롯·호스트 키 유지 + `investigation_abandon_grace_seconds`(120) · 협조적 중단 · `sre_tool_global_concurrency`(8 · 축별 프로세스 전역) · 소견 LLM timeout · 접수 응답 즉시 거부 · `queue_position` 상시 · 상태 게이지 | `sre_agent/sre_agent/{settings.py,application/*,infrastructure/mcp_tool_client.py,diagnosis.py,interface/mcp_service.py}` · 테스트 | 완료 |
| **W4a** | mcp_server — 소스별 동시 상한·대기 timeout(기본 = 풀 크기·query_timeout) · PG `acquire` timeout · DB2 쿼리 timeout | `mcp_server/mcp_server/{config.py,db.py,…}` · 테스트 | 완료 |
| **W4b** | apm_gateway — 소스별 in-flight 상한(기본 8) · httpx Limits | `apm_gateway/…/jennifer/client.py` · `config.py` · 테스트 | 완료 |
| **W5** | UI — **목업 먼저(사용자 확인 후 구현)**. §4 장면 | `src/static/js/{app.js,briefing-view.js,diagnosis-view.js,noise.js}` · `src/static/css` · 관리자 설정 화면 | 목업 완료 · 사용자 확인 대기 |
| **W6** | 통보 처리(Q-1 결과) · 매뉴얼 동반(D-255 — U-63·U-70·관리자 조사 설정) · 계획 끝 회귀 1회 · D-347 등재 마감 · D-341 ⑦ 개정 부기 | 매뉴얼 원천·생성물 · `noise_gate/application/nodes/alarm_notifier.py`(Q-1 채택 시) | 대기 |

## 4. UI 개선 (W5 — 목업 장면)

| 장면 | 화면 | 바뀌는 것 |
|---|---|---|
| U1 | 이벤트 알람 카드(포털) | 조사 없는 알람에 「조사 안 됨」 상태 + [조사 시작] 버튼이 접힘 없이 보임 · 자동 조사 꺼짐 안내 |
| U2 | 조사 접수 거부 | 상한 초과·같은 서버 조사 중·대기열 가득 → `reason_text` 한 줄과 다음 행동(진행 중 조사 열기·잠시 뒤) |
| U3 | 대기 중 조사 | 「대기 순번 N · 동시 조사 상한 M」 배지(sre_agent `queue_position`) |
| U4 | 장애 진단 「이벤트 찾아 조사」 | 조사 가능한 알람 목록(최근 PAGE·조사 안 됨 우선) · 행별 [조사 시작]·상태 |
| U5 | 관제 서랍(노이즈 콘솔) | 조사 섹션 「조사 안 됨 → [조사 시작]」 · 모드 표지 |
| U6 | 관리자 설정 | 조사 모드(수동 전용/자동) · 상한(동시·대기열·사용자별 시간당·전역 도구) 읽기 표시 + 조사 서비스 상태 게이지(진행·대기·정리 중) |

목업은 매뉴얼 캡처 하네스(`scripts/manual/` 스냅샷 서버 18981 · Playwright · 제품 코드 무변경 DOM 목업)로 전/후 PNG + `manifest.json`.

## 5. 기본값 예외 근거 (D-162)

| 필드 | 기본 | 예외 근거 |
|---|---|---|
| `NOISE_INVESTIGATION_MANUAL_ENABLED` | true | 사용자 확정 수동 전용에서 조사 동선이 이것뿐 · `.env` 미추적 · 사용자 확정 설정은 코드 기본(메모리 규칙) |
| sre `INVESTIGATION_MAX_QUEUED` | 4 | 무제한 대기 스레드가 행 원인(S2) |
| sre `INVESTIGATION_ABANDON_GRACE_SECONDS` | 120 | 좀비 실부하 계상(S1) · 무한 정체 방지 유예 |
| sre `SRE_TOOL_GLOBAL_CONCURRENCY` | 8 | 조사 수 × 16 동시 호출(T1) |
| mcp_server 소스별 동시·대기 timeout | 풀 크기 · query_timeout | 무기한 대기(T2) — 정상 부하 무대기 |
| apm_gateway `max_inflight_per_source` | 8 | in-flight 무제한(T3) |

## 6. 겹치는 계획 — 선후

- **`plans/192` W3**(`alarm.py` `_live_scope`에 본인 접수 `kind=manual` 포함): 수동 전용에서는 모든 조사가 사용자 접수라 **192 W3이 194 W5보다 먼저** 끝나야 일반 사용자가 「진행 중 목록」에서 자기 조사를 찾는다. 194 W1은 `_live_scope`를 건드리지 않는다(같은 파일의 다른 함수 — capabilities·접수 응답만).
- **`plans/193` W1**(`noise.js` mount 동작 불변 리팩터): 194 W5 U5(관제 서랍)는 `noise.js`를 고친다 → 193 W1 뒤에 얹는다(먼저 끝난 쪽 위에). 목업 단계는 코드 무변경이라 무관.
- **`plans/192` W2**(진입 문구 규칙): U1·U4 버튼 문구는 192 W2 규칙(「장애 진단에서 보기」·「조사 대화로 열기」)을 따른다.

## 7. 검증

- Wave 중간: 자기 테스트 + `regress.py --base 2f64aa9c --no-tests --files …`. 계획 끝: `regress.py --base 2f64aa9c --files <회귀 대상 파일…>` 1회. 전체 회귀는 사용자 요청 시만.
- 과금·실 LLM: 불필요(가짜 sre·목). MLX를 쓸 일이 생기면 두 평면 `mlx` 확인 뒤 `RUN_LOCAL_LLM=1`만.
- 내부망 확인 항목: S7 스택 덤프 · DB2 쿼리 timeout 실동작 · 운영 `.env` 값(§8).

## 8. 운영(폐쇄망) `.env` 변경

| 파일 | 키 | 값 | 이유 |
|---|---|---|---|
| 본체 `.env` | `NOISE_INVESTIGATION_TRIGGER_ENABLED` | **false**(또는 줄 삭제) | 자동 조사 끔 — 명시값이 코드 기본을 덮으므로 반드시 고친다 |
| 본체 `.env` | `NOISE_INVESTIGATION_EPISODE_CONTEXT_ENABLED` | false(또는 삭제) | trigger off면 무동작이지만 의도 명시 |
| 본체 `.env` | `NOISE_INVESTIGATION_MANUAL_ENABLED` | true(또는 삭제 — 코드 기본 true) | 수동 조사 동선 |
| sre_agent `.env` | `INVESTIGATION_HOURLY_BUDGET` | **설정 불필요**(코드 기본 20 — 사용자 확정 Q-2) · 명시값이 있으면 그 값이 덮으므로 다른 값이면 지운다 | 전역 시간당 상한 |
| sre_agent `.env` | `INVESTIGATION_MAX_CONCURRENT` | 2 유지(LLM 서버 1대 기준) | |
| sre_agent `.env` | `INVESTIGATION_MAX_QUEUED` · `INVESTIGATION_ABANDON_GRACE_SECONDS` · `SRE_TOOL_GLOBAL_CONCURRENCY` | 코드 기본(4 · 120 · 8) — 넣지 않아도 됨 | 바꿀 때만 명시 |
| mcp_server `config.toml` `[[sources]]` | `max_concurrent_queries` · `queue_timeout_seconds` | 미기재(= 풀 크기 · query_timeout) | DB2 소스는 기본 4 |
| apm_gateway `.env` | `JENNIFER_MAX_INFLIGHT_PER_SOURCE` | 미기재(8) | 제니퍼 서버 여력에 맞춰 조정 |
| 재기동 | 본체 · sre_agent · mcp_server · apm_gateway | 4개 모두 | 코드·설정은 기동 시 1회 해석 |

## 8.1 구현 결과 (W1~W4 · 2026-10-26)

- **W1**: `investigation_manual_enabled` 기본 true(getattr 폴백 3곳도 true — `config_fallback_check`) · capabilities `investigation_enabled = gate ∧ (trigger ∨ manual) ∧ url` + `investigation_auto_enabled`·`investigation_manual_enabled` · 거부 문구 표 `src/domain/investigation_reject_reasons.py`(수동 접수·추가 조사·채팅 조사·동기 진단이 공용) — 동기 진단은 접수 즉시 거부면 sre `verdict` 문구를 그대로 보인다. 부수 효과: 수동 on이면 판정 기록에 원 알람 시각·hostname·resource_type이 남는다(다시 분석 재료 · plans/187 W11). manual off 전제 기존 테스트 4개는 플래그를 명시 false로 고정(단언 불변) · `test_fault_diagnosis.py` `test_rejected_empty_text` → `test_rejected_reports_reason`(사유 노출로 의도 변경).
- **W2**: `jsonl_tail` 경로 단위 증분 tail — 캐시 키는 경로 하나(같은 경로를 여러 N으로 읽으면 큰 N 보존 · KB 집계 10000줄과 조회 2000줄의 중복 상주 방지) · 레코드 dict는 공유(호출부 전수 확인 — 변경 없음) · 허브는 상태·단계·대기 순번·커서·부분 브리핑·진척·오류가 바뀔 때만 notify(경과 시간은 화면이 로컬 시계로 그림 — 하트비트 불필요).
- **W3**: 대기 상한 거부 `queue_full`/좀비가 슬롯을 쥐고 있으면 `service_overloaded` · 보조 스레드(조사·사전수집·수집기·심층) 등록 → 워커 끝에 미종료면 `cancel_event` + 반환 대기 스레드(유예 뒤 `zombie_released` 감사) · holmes `call_stream(cancel_event=…)` · 축별 전역 `BoundedSemaphore`(비차단 획득 반복 · 대기 상한 = 도구 1건 상한) · 접수 응답에 즉시 거부(`status/reason/verdict`) · `sre_health` 부하 게이지(`running·queued·abandoned_slots·abandoned_threads`·상한).
- **W4a**: 소스별 `asyncio.Semaphore`(`max_concurrent_queries` 기본 = 풀 크기 · DB2 4) + 대기 timeout(`queue_timeout_seconds` 기본 = query_timeout) · PG `acquire(timeout=)` · DB2 `SQL_ATTR_QUERY_TIMEOUT`(가드된 getattr — **로컬 ibm_db 미설치로 미검증**) · TOML 전용 키(기존 소스별 필드 관행).
- **W4b**: `PrioritySlots`(우선순위·에이징 자리) — 순서 자리 → 속도 제어 → HTTP · `JENNIFER_MAX_INFLIGHT_PER_SOURCE`(8 · 소스별 `JENNIFER_<ID>_MAX_INFLIGHT_PER_SOURCE`) · httpx `Limits` 동일 수.

- **검증(2026-10-26)**: verifier 독립 검증 + 보안 한 라운드 — Critical 0 · High 0 · Medium 3 · Low 4. 계수 스트레스(스레드 16 × 8회 무작위 · 강제 반환 섞임)·`jsonl_tail` 종전 구현 무작위 대조(12 seed × 300 연산)·mcp/apm 자리 스트레스·인가(존 검사·사용자 상한·401) 문제없음. 교정: ① 거부 표시 문구 `reject_display_text(code, verdict)` — 가용성 거부(`target_unavailable`)만 verdict, 표에 있는 코드는 한국어 문구(실 sre는 verdict `"조사 거부 — <코드>"`를 붙여 코드 원문이 나가던 결함 · 수용 기준 ④) · 라우트 2곳·노드 2곳 공통 ② 워커 `start()` 실패 시 대기 자리·호스트 키·워커 등록 원복 + `service_overloaded` 거부 ③ `_abandoned` 정리를 가드 진입에도 ④ apm in-flight 키 정수 검증(키 이름 든 오류). Medium 2·3은 R13·R12. 교정 뒤 모듈 단위 재실행 rc=0(본체 1,041 · apm 1,691 · mcp 1,116 · sre 2,473 통과 · 실패 0 · `범위: 모듈 단위 — 전체 미실행`).


## 8.1.1 W5 구현 결과 (2026-10-26 · 목업 승인 뒤)

- **백엔드**: Q-4 시간당 사용자 상한은 rejected 제외(`hourly_retry_after` · 429 `detail={code:"hourly_limit", reason, retry_after_seconds}`) · Q-1 `alarm_notifier.manual_only_mode`(게이트 on ∧ 트리거 off ∧ 수동 on ∧ 조사 서비스 URL 있음 — URL 없으면 카드에 [조사 시작]이 없어 안내하지 않음) ∧ PAGE일 때만 안내 블록(웹 주소 있으면 링크 · 이스케이프) · Q-2 sre `investigation_hourly_budget` 기본 20 · C1 `service_load`(본체 앱 단위 10초 캐시 `src/api/investigation_service_health.py` · 잠금 1개로 합침 · 실패도 캐시) — `/active`·by-alarm 최상위·스트림 summary 프레임 `{items, service_load}`(게이지 5키가 바뀔 때만 재전송) · C2 판정 행 `investigation`(by-alarm과 같은 열람 순서 · 저장소 경로 없으면 키 없음) · C3 같은 서버 거부 `existing_investigation_id`(열 수 있을 때만) · C4 manual subject `server_name`·`hostname`·`alarm_name`·`label`(새 접수부터) · C5 `GET /api/v1/admin/investigation/summary`(관리자 의존성 + role 재검증) · 지연 poll 경로에서도 by-alarm `queue_position` 전달(`investigation_poll.live_fields`).
- **이탈(승인)**: 벤치 러너는 에코 20을 기대하지 않고 `BENCH_HOURLY_BUDGET=1_000_000`을 명시·보호 키로 두고 정확한 에코를 검사한다 — 기본 20이면 실험군 75건 중 21건째부터 `hourly_budget_exceeded`·심층 강등으로 측정이 오염된다(plans/154 §4.7 「가드에 막히지 않게」). 되돌리기는 상수 1개·검사 1줄.
- **포털 UI(U1~U4)**: `briefing-view.js`에 `manualMode`·`startOutcome`·카드 「조사」 칸 마크업(공용 렌더 — app.js·noise.js에 `bv-` 금지 규칙) · 카드 U1(펼친 조사 칸·자동 꺼짐 안내)·U2(거부 3종 다음 행동 · 합류 안내 + [장애 진단에서 보기])·U3(대기 칩 + 설명) · 장애 진단 U4(PAGE → 조사 안 됨 우선 · 조사 상태 열 · 등급 필터·「조사 안 됨만」) · 「대상」 칸 서버 · 알람. 목업과 다른 점: 대기 표시를 칩 + 설명 한 줄로 나눔 · 실패한 조사에도 [조사 시작] 허용 · E3·E4 「진행 보기」 문구는 plans/192 W2 몫이라 유지.
- **관제·관리자 UI(U5·U6)**: `noise.js` `manualOnlyMode`(capabilities 1회 · break-glass는 부르지 않음) · 머리·칸 표지 · `renderAnalyzeOnly`가 `manualMode`·합류 버튼 옵션 전달 · `admin.js` 「알람 조사 — 운영 요약」(설정 목록 위 · [새로 고침]만 · 자동 polling 없음 — 목업의 「30초마다 갱신」에서 이탈) · 상한 표에 「시간당 전체 예산」 행 추가.

- **W5 검증·교정**: verifier 한 라운드 — Critical 0 · High 0 · Medium 2 · Low 4 → 전부 교정. ① 판정 행 조사 색인이 사건 연결 행을 합치지 않아 by-alarm 열람 규칙과 어긋남(타 존 알람에 조사 id·상태 노출 · 관리자에게 「조사 안 됨」 오표시) → `InvestigationStore.resolved_records()`(조사 id별 `_resolve`와 같은 병합) · by-alarm과 판정 목록이 공개 함수 `assert_alarm_investigation_access` 하나를 부른다 ② 지연 poll이 `phase`를 버려 카드 대기 표시가 안 보임 → `phase`·`queue_position` 전달 + 클라이언트도 정수 순번이면 대기 ③ 헬스 캐시 만료 때 응답이 갱신을 기다림 → stale-while-revalidate ④ 같은 서버 거부에 열 조사가 없으면 버튼 없이 사유만(카드·이벤트 찾아 조사) ⑤ 자동 모드에서는 「조사 안 됨」 칸 대신 종전 섹션 ⑥ 판정 기록 반복 조회 요청 단위 메모.
- **W6 캡처에서 찾은 결함 교정**: U4 등급 필터·판정 열이 실제 등급(page·ticket·dashboard·suppress)과 달랐음(옛 `digest`·`drop` 어휘 — plans/187 W18부터) → 관제 화면 문구로 · 사용자 요청 조사 배지 「요청 분석 · 도구 출력 인용」 · 대기·진행 중 [다시 분석] 숨김(자동 조사에도 적용) · 검색 전 빈 표 숨김 · 필터 줄 글꼴.
- **W6 매뉴얼(D-255)**: 사용자 U-40·U-57·U-64·U-70·U-44 갱신 · **U-71 신규**(접수·합류·대기·거부 3종·시간당 상한·PAGE 안내 · 거부는 시간당 건수 제외) · 관리자 **A-83 신규**(운영 요약) · A-79 `NOISE_INVESTIGATION_MANUAL_ENABLED` 기본 true·trigger 명시값 주의 · A-77 새 상한 키 · 192 W3 문장 유지 + 「남이 연 조사·자동 조사는 존 권한 검사」로 좁힘(break-glass 문장은 192 W1 몫으로 유지). 새 캡처 6(alarm-inv-start·alarm-inv-reject·alarm-inv-queued·diagnosis-event-search·noise-drawer-manual·admin-inv-summary) + settings 4장 재촬영 · `capture.py`에 응답 가로채기(`routes`) 추가. `pytest tests/test_manual` 687 passed.
- **완성 화면 캡처(목업 대조)**: scratchpad `ui-mockups-194/impl/`(11장 + `manifest.json` — 장면별 목업 대비 차이 메모 · 깨짐 0).
- **계획 끝 모듈 단위 회귀**(`regress.py --base 2f64aa9c --files <회귀 대상 61개>`): 본체 통과 3,811 · 실패 72 · apm 1,691 · mcp 1,116 · sre 2,476(실패 0) · arch·overfit·config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · `범위: 모듈 단위 — 전체 미실행`. **실패 72건 귀속 = plans/192 피어 세션 몫**: `tests/test_api/test_ui_plan192_{w1_entry,w2_entry_labels,w4_conversation_restore}.py`(13:19 생성 미추적 · 구현 전에 쓴 테스트). 클린 worktree(`2f64aa9c` + 그 3파일만)에서 97건 실패, 현 트리 72건 — 현 트리에서만 실패하는 테스트 0건. 첫 실행은 `test_plan186_auth_fail_closed.py`의 수집 시각 JWT 매개변수가 xdist 워커 사이에 달라 수집 불일치로 무효(기존 시간 의존 결함 · R16).

### 8.2 잔여·이탈

| # | 잔여 | 처리 |
|---|---|---|
| R1 | 소견·종합 LLM 요청 timeout 전달(W3 ⑦) — holmes 0.36 `DefaultLLM.completion`에 timeout 인자가 없고 내부에서 `LLM_REQUEST_TIMEOUT`으로 고정 호출 | **보류(C)** — 사본·프록시(A)나 litellm 직접 호출(B)은 holmes 내부 의존·조립 중복. 좀비 계상(W3)이 슬롯을 붙들어 실부하는 상한 안에 들어온다. 필요하면 운영 env `LLM_REQUEST_TIMEOUT` 하향 검토 |
| R2 | 협조적 중단 불가 지점 — 이미 나간 LLM 1건·시작한 도구 호출·에이전트 생성 선행검사·사전수집/수집기(종전 stop만)·소견기 풀·holmes 자체 MCP 도구(전역 도구 상한 밖) | 유예 120s 강제 반환으로 무한 점유는 막힘 · 내부망 관측 후 판단 |
| R3 | 본체: 동기 I/O가 이벤트 루프 위(to_thread 미적용) · 저장소 회전 없음 · `clip_briefing` O(n²) · 스트림이 poll 간격마다 목록 재생성(탭 × A) · 허브 poll마다 SSE 새 연결 | 증분 tail로 정상 상태 비용은 추가 바이트만 · 첫 읽기는 파일 전체 — 회전은 후속 |
| R4 | 자동 경로(`investigation_trigger._submit_and_poll`)는 즉시 거부의 `verdict` 문구를 버리고 코드만 저장 | 자동 off 기본이라 보류 |
| R5 | 채팅 조사 접수에서 조사 id 없이 거부가 오면 문구가 코드 원문 | **해소**(검증 교정 ① — `reject_display_text`) |
| R6 | mcp_server 헬스체크(DB2·MariaDB)는 세마포어 밖 · MariaDB `acquire`는 timeout 인자 없음(세마포어로 막힘) · DB2 취소 시 스레드 안 쿼리는 쿼리 timeout으로만 멈춤 | 내부망 확인 |
| R7 | apm_gateway 최악 지연 = 자리 대기(30s) + 스로틀 + HTTP(30s) > sre 도구 1건 상한(60s) 가능 · 자리 대기 시간초과도 작업 `api_calls`에 1건 | 관측 후 |
| R8 | 잡 사이 조회 결과 캐시(T4) · 같은 서버 알람 묶음 조사 | 수동 전용에서 동기 약함 — 후속 판단 · **같은 서버 알람 묶음 조사는 `plans/198` I-11로 이관**(2026-10-28 · Q6 (a) 권고 적용 · 사용자 확인 전) · 잡 사이 캐시(T4)는 여기 잔류 |
| R9 | sre 신규 상한의 None(종전 동작)은 `.env`로 줄 수 없음(빈 값·null 검증 실패) | 의도 — 상한 해제는 큰 값으로 |
| R10 | 매뉴얼(D-255) — 관리자 매뉴얼 `admin.md`의 `NOISE_INVESTIGATION_MANUAL_ENABLED` 기본 표기(false) · 새 상한 키 · 사용자 매뉴얼(조사 시작 동선) | W6(UI 확정 뒤 한 번에) |
| R11 | 기준선 실패 `tests/test_api/test_settings_help.py::test_t2_curated_groups_have_full_coverage`(`API_RELOAD` 큐레이션 누락 — D-346 커밋분 · 이 계획 무관) | 별건 |
| R12 | 증분 tail 캐시가 파싱한 레코드를 경로별로 상주시킨다(판정 20000줄 · 조사 10000줄) — 로컬 판정 레코드 평균 485B × 20000 = 41MB 실측, 운영 레코드(근거·AI 서술)는 더 커서 수백 MB 가능(검증 지적 3) | 내부망 기동 뒤 본체 RSS 확인 · 크면 바이트 상한 추가(후속) |
| R13 | **해소(W5 · Q-4)** — 서비스 거부(queue_full·service_overloaded·같은 서버 진행 중)도 사용자 시간당 상한 3건을 소모한다(`src/infrastructure/investigation_request_limit.py:83-92` — 계산은 종전과 같으나 queue_full은 정상 혼잡에서도 남) | 사용자 결정 Q-4 |
| R14 | 워커 시작 실패(스레드 고갈) 때 가드가 이미 기록한 dedup 지문·시간당 예산 창은 되돌리지 않음 — 같은 지문 재요청이 TTL 동안 `dedup_ttl_active`로 거부될 수 있음 | 스레드 고갈 때만 · 후속 |
| R15 | 「이벤트 찾아 조사」의 시작·끝·알람명·서버 입력 라벨과 날짜 자리표시 글꼴 자간 · 정렬 안내 문구 세로 위치 · 채팅 조사(kind diagnosis·origin chat) 배지가 「자동 조사 · …」 — 모두 이 계획 전부터 있던 표시 | 경미 · 후속 |
| R16 | `tests/test_api/test_plan186_auth_fail_closed.py`가 수집 시각으로 JWT `exp`를 만들어 xdist 워커가 초 경계를 넘으면 수집 불일치로 본체 회귀 전체가 무효가 된다(이 계획 무관 · 간헐) | 별건 — 매개변수를 고정 시각으로 |

## 9. 사용자 결정 (2026-10-26 확정)

| # | 질문 | 권고 |
|---|---|---|
| Q-1 ✅ **넣음 — PAGE만**(D-347 ⑥) | 수동 전용에서 PAGE 통보에 「자동 조사 꺼짐 — 웹 알람 카드에서 [조사 시작]」 한 줄(+ `NOISE_INVESTIGATION_WEB_BASE_URL` 있으면 링크)을 넣을지 | 넣음 · 수동 전용일 때 PAGE만 |
| Q-2 ✅ **20 · 코드 기본값으로** | sre_agent 시간당 전역 조사 상한(`INVESTIGATION_HOURLY_BUDGET`) 운영 값 | 20(사용자별 3 × 동시 사용자 수 고려) |
| Q-3 ✅ **U1~U6 목업대로 구현**(U6 위치·형태 포함) | W5 목업(§4) 승인 | — |
| Q-4 ✅ **세지 않음 — 실제 접수만**(D-347 ⑤ · D-335 ⑥ 부기) | 조사 서비스가 혼잡으로 거부한 요청(대기열 가득·서비스 과부하·같은 서버 진행 중)도 사용자 시간당 3건에 셀지 | 세지 않음 — 「잠시 뒤 다시 요청」 안내를 따르면 조사 0건으로 1시간 막힘 · D-335 ⑥ 해석 확인 필요 |
