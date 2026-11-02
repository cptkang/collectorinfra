# 207. 다중 사용자 동시성 개선 — 장애 조사 동시 5건(1순위) · 관제 집계 루프 막힘 · 알람 워커 동시 처리

> **작성일**: 2026-11-02 · **v1.1**(같은 날 — 다중 사용자 점검 §7 · 장애 조사 동시성 §8 · 우선순위 개정)
> **상태**: **WIP** — 트랙 A **A0·A1·A1b 완료**(2026-11-02 · 측정 §8.6 · 교정 §8.8 · 폐쇄망 절차 §8.7) · A2·A3 보류(원인 미확인 · 폐쇄망 판정 뒤) · A5 매뉴얼 반영 · 트랙 A verifier Critical 0(Medium 2·Low 3 교정) · **트랙 C M-1·M-2·M-4 완료**(§7.5) · 트랙 B **W0·W1·W2·W3·W4·W5 완료**(계측·직렬 기준선 §2 C-0 · 의존 표 §1.4 · 동시 처리 실행기 C-2 — N=4 distinct ×3.98 · 판정 동등 · 드레인·회수 C-5(회수 기본 off) · 임베딩 스레드 이전 C-6 · 워커 분리·단일 인스턴스 잠금 C-7 — D-358 부기 1 · 운영 적용은 W0-H 뒤) · **트랙 C M-3 완료**(§7.5) · **트랙 B W6 완료**(설정 도움말·`docs/28` §4-D·매뉴얼) · **트랙 B·C 계획 끝 모듈 회귀 완료**(2026-11-03 · C-8 아래 「계획 끝 회귀」 — 신규 실패 0 · 범위 모듈 단위) · 잔여: 트랙 A A2·A3·A4 · 폐쇄망 측정 · A4(상한 2→5 코드 기본)는 폐쇄망 실측 뒤 · v1.1 사용자 결정(인터뷰 · §8.0) · 결정 D-358
> **계획 번호**: 당초 205 → **207로 재번호**(2026-11-02 · origin/main `afc5626c`가 `plans/205`(APM 게이트웨이 조회 가속 · D-356)·`plans/206`(MCP 서버 동시성 · D-357)을 선등재). 관계는 §9.
> **분석 기준 HEAD**: `16ea44fa` (계획 시작 SHA는 W0 착수 때 이 줄 아래에 적는다)
> **계획 시작 SHA**: `afc5626c`(2026-11-02 · 트랙 A 착수 · 16ea44fa→afc5626c는 plans 문서만 바뀌어 §1·§8.1 줄 번호 유효 — 재실측 확인) · **결정**: D-358
> **회귀 대상 파일**: (A0) `sre_agent/sre_agent/settings.py` · `sre_agent/sre_agent/infrastructure/{investigation_metrics,mcp_tool_client}.py` · `sre_agent/sre_agent/application/investigation_dispatcher.py` · `sre_agent/scripts/investigation_bench/{__main__,runner,concurrency,fake_llm,world_server,worlds,export}.py` · `sre_agent/tests/{test_plan207_investigation_metrics,test_bench_concurrency}.py` · `src/observability/loop_lag.py` · `src/config/services.py` · `src/api/server.py` · `scripts/investigation_live_probe.py` · `config/settings_help/observability_polestar.yaml` · `.env.example` · `tests/test_observability/test_loop_lag.py` · `tests/test_api/test_settings_catalog.py` · (A1) `sre_agent/sre_agent/infrastructure/{investigation_context,holmes_mcp_scope}.py` · `sre_agent/.env.example` · `sre_agent/tests/test_plan207_a1_tool_scope.py` · (A1b) `src/api/investigation_live_hub.py` · `src/api/routes/alarm.py` · `tests/test_api/test_plan207_a1b_mask_share.py` · `sre_agent/tests/test_plan207_verify_a1.py` · `tests/test_api/test_plan207_verify_a1b.py` · (A5) `scripts/manual/{content/admin.md,features.yaml}` · `src/static/manual/{admin.html,user.html,help_index.json}` · (C) `noise_gate/infrastructure/{decision_store,notification_bus}.py` · `src/api/noise_aggregate_cache.py` · `src/api/routes/noise_dashboard.py` · `src/config/alarm.py` · `src/api/settings_catalog.py` · `config/settings_help/noise_gate_core.yaml` · `tests/test_api/test_plan207_c_noise_aggregate.py` · `noise_gate/tests/test_plan207_m4_bus_queue_max.py` · `tests/test_api/test_plan207_verify_c.py` · (B W1) `noise_gate/tests/{equivalence_harness,test_plan207_w1_equivalence}.py` · (B W0) `noise_gate/application/{worker_timing,nodes/notification_gate}.py` · `noise_gate/application/alarm_worker.py` · `config/settings_help/alarm.yaml` · `noise_gate/scripts/worker_load_bench.py` · `noise_gate/tests/{test_plan207_w0_timing,test_plan207_verify_w0}.py` · (B W2) `noise_gate/application/worker_concurrency.py` · `noise_gate/domain/page_review.py` · `noise_gate/tests/test_plan207_w2_concurrency.py` · `noise_gate/tests/test_plan207_verify_w2.py` · (B W4) `noise_gate/application/{dedup_service,nodes/alarm_context_enricher}.py` · `noise_gate/tests/test_plan207_w4_to_thread.py` · (B W3) `noise_gate/infrastructure/redis_queue.py` · `noise_gate/tests/test_plan207_w3_drain.py` · (C M-3) `noise_gate/infrastructure/jsonl_tail.py` · `noise_gate/application/worker_wiring.py` · `noise_gate/scripts/{page_audit,replay_noise,mock_polestar_events,noise_gate_scenario_test}.py` · `scripts/manual/alarms.py` · `noise_gate/tests/test_plan207_m3_rotation.py` · `tests/test_api/test_plan207_m3_decision_scan.py` · (B W4 교정) `noise_gate/infrastructure/embedding_provider.py` · (B W3·W4 검증) `noise_gate/tests/test_plan207_verify_w3w4.py` · (C M-3 검증) `tests/test_api/test_plan207_verify_m3.py` · (B W5) `noise_gate/worker.py` · `noise_gate/infrastructure/worker_lock.py` · `scripts/arch_check.py` · `noise_gate/tests/test_plan207_w5_worker_process.py` · `tests/test_api/test_plan207_w5_in_process.py` · (B W6) `config/settings_help/alarm.yaml` · `.env.example` · `docs/28_noise_cancellation_operator_guide.md` · `scripts/manual/content/admin.md` · `src/static/manual/{admin.html,user.html,help_index.json}` · (B W5 검증·교정) `noise_gate/tests/test_plan207_verify_w5.py` · `tests/test_api/test_noise_page_review_console_verify.py`
> **사용자 제기(2026-11-02)**: *"noise gate를 멀티코어를 쓸수 있도록 수정하여 동시성을 개선할 수 있는지 검토하라."* → 검토 결론(멀티코어보다 프로세스 안 동시 처리가 맞다) 보고 뒤 *"동시성을 높일 수 있는 방안은 계획서에 반영하라."*
> **관련 결정**: D-048(4-티어 게이트) · D-184(워커 dead-letter · 실패도 ACK) · D-139(패키지 경계 — 워커는 본체와 같은 프로세스) · D-347(조사 부하 상한) · D-341(인라인 조사 시간 사슬) · D-162(신규 플래그 기본 off = 비트 동일) · D-255(매뉴얼 동반)
> **관련 계획**: **205·206(origin/main · 같은 날 동시성 계획 — §9)** · 194(조사 부하 상한 · S7 · W2 증분 캐시) · 162 §12.4 W0-H · **G-11**(같은 소비자 그룹 다중 소비) · 60(인메모리 상태 재기동 휘발 수용) · 144(크로스소스 사건) · 200(조사 실행 병렬화 — 조사 서비스 쪽 짝)

---

## 0. 요약

> **v1.1 (2026-11-02)**: 이 계획은 세 트랙이다. **A(1순위) 장애 조사 동시 5건** — §8 · **C 관제 집계 루프 막힘** — §7 · **B 알람 워커 동시 처리** — 아래 §0~§6(v1.0 본문). 2명이 시험해도 장애 조사가 현저히 느려진다는 사용자 제기로 A를 앞에 둔다.

알람 워커(`noise_gate/application/alarm_worker.py`)는 소비자 하나가 메시지를 **한 건씩 끝까지** 처리한다. 한 건의 시간은 대부분 외부 대기(서버명 역조회 · 컨텍스트 수집 MCP/REST · LLM 분석 · agentic 보강 · 인라인 조사 최대 45s)라서, 처리량은 「1 ÷ 한 건 지연」에 묶이고 알람이 몰리면 뒤 건이 줄을 선다. CPU를 쓰는 구간은 임베딩 유사도 정도로 작다.

**다중 프로세스(멀티코어)는 택하지 않는다.** 판정 상태(지문 dedup · 자가복구 짝맞춤 · 플래핑 · 스톰 · 상관 클러스터 · 사건 추적기 · PAGE 레지스트리)가 프로세스 메모리에 있고 **도착 순서**에 의존하므로, 메시지를 프로세스끼리 나누면 판정이 반쪽이 된다 — `plans/162` §12.4가 운영에서 의심하는 바로 그 증상(수신 59.7% 판정 기록 없음)이다.

대신 **한 프로세스 안에서** 다음을 한다.

1. **앞단(상태를 읽고 쓰는 게이트 사전 판정)은 지금처럼 직렬** — 상태 일관성을 그대로 둔다.
2. **그래프 실행(`graph.ainvoke`) 이후는 동시 N건** — I/O 대기를 겹친다.
3. **같은 충돌 키(호스트 · 팬아웃 · 사건)의 알람은 앞 건의 후처리가 끝난 뒤 앞단에 들어간다** — 그래프 뒤에 쓰이고 다음 알람 앞단이 읽는 상태(사건 멤버 티어 · PAGE 레지스트리)의 순서를 지킨다.
4. 신규 플래그 `ALARM_WORKER_MAX_CONCURRENCY` **기본 1 = 현행과 비트 동일**.

---

## 1. 현행 분석 (HEAD `16ea44fa`)

### 1.1 소비 루프

| 위치 | 현행 |
|---|---|
| `alarm_worker.py:82` | 소비자 이름 상수 `_CONSUMER_NAME = "worker-1"` |
| `alarm_worker.py:776-785` | `read_messages(count=10, block_ms=2000)` → `for … : await self._process(...)` — **한 건씩 직렬** |
| `noise_gate/infrastructure/redis_queue.py:65-68` | `XREADGROUP … {stream: ">"}` — 새 메시지만 읽음 · **기동 시 미ACK(pending) 회수 없음** |
| `alarm_worker.py:1231-1237` | 예외면 dead-letter 후 `finally`에서 ACK(D-184) — 처리 중 프로세스가 죽으면 그 1건은 pending으로 남는다 |
| `src/api/server.py:694` | 워커는 본체 uvicorn 이벤트 루프 안의 태스크 — API와 루프·CPU를 나눠 쓴다. 본체는 워커 메모리를 참조하지 않는다(태스크 핸들만 · SSE는 Redis 브리지) |

### 1.2 `_process` 한 건의 구간 (`alarm_worker.py:791-1236`)

| 구간 | 내용 | 대기 성격 | 워커 상태 |
|---|---|---|---|
| P1 파싱 | JSON · 심각도 정규화 · `AlarmEvent` | CPU 미미 | 없음 |
| P2 서버명 역조회 | `attach_server_identity`(타임아웃 3s · Redis 캐시) | **I/O** | 없음(이벤트만 변경) |
| P3 게이트 사전 판정 | 지문 dedup · 침묵 규칙 · 자가복구 · 인히비션 · 플래핑 · 스톰 · 상관(위상 그래프 로드 await) · 의미 근접중복 · 주석 신호(LLM 분류기 await · 옵트인) · 사건 관찰 · PAGE 레지스트리 신호 | 대부분 동기 · 일부 await | **읽기·쓰기 (순서 의존)** |
| P4 그래프 | `graph.ainvoke` — 컨텍스트 수집 · LLM 분석 · agentic 보강 · 게이트 판정 · 인라인 조사 · 통보 · 판정 감사 기록 | **I/O 대부분** | `active_firings` 읽기 전용 주입 · 저장소 append |
| P5 후처리 | `_record_episode_tier` · `_after_episode_graph`(await) · `_observe_page` | 일부 I/O | **쓰기 — 다음 알람 P3가 읽는다** |
| P6 ACK | `ack_message` | I/O | 없음 |

- 탐지 근거 인스턴스 필드(`_last_self_heal_evidence` 등 · 호스트 어댑터 `alarm_worker.py:271-303`)는 **P3 안에서 쓰고 곧바로(await 없이) 읽는다** → P3를 직렬로 두면 경합이 없다. P3를 동시화하지 않는 한 손대지 않는다.
- **P5 → 다음 P3 의존**: 사건 멤버 최종 티어(뒤 증상의 원인 조건 ④ 재료 · `alarm_worker.py:1218-1221`), PAGE 레지스트리 최종 판정(`signals_for(host_key, fanout_key)` · `alarm_worker.py:1223-1230`). 그래프를 동시화하면 이 순서가 깨질 수 있다 — §2 C-3이 다룬다.

### 1.4 워커 상태 의존 표 (W1 · C-1 · 2026-11-02 · HEAD `afc5626c` 실측)

구간 표기는 §1.2(P1 파싱 · P2 서버명 역조회 · P3 게이트 사전 판정 · P4 그래프 · P5 후처리 · P6 ACK).
「키」는 그 상태를 읽고 쓰는 단위다. **P3 안에서만 읽고 쓰는 상태는 P3를 직렬로 두는 것만으로 순서가 지켜지므로 충돌 키가 필요 없다** — 충돌 키는 P4·P5가 쓰고 다음 알람이 읽는 상태에만 필요하다.

#### (a) 워커 인스턴스 필드 — 게이트 판정 상태

| 필드 | 쓰는 구간 | 읽는 구간 | 키 | 비고(코드) |
|---|---|---|---|---|
| `_gate_dedup` | P3(지문 dedup · 제니퍼 정책의 해제 pop) | P3 | 지문 + 전 키 만료 sweep | `dedup_service.py:163-199` · `alarm_worker.py:1365·1399` |
| `_firing_registry` | P3(자가복구 시드) | P3 | 지문 + 전 키 만료 sweep | `signal_detection.py:115-133` |
| `_active_firings` | P3(인히비션) | P3 · **P4(enricher `root_notified` — 다른 호스트=근본원인 스코프)** | `db_id\|server_name` + 전 스코프 만료 sweep | `signal_detection.py:161-191` · 주입 `alarm_worker.py:1196` · 읽기 `alarm_context_enricher.py:102·750` (multi_hop일 때만) |
| `_flap_states`·`_flap_flag`·`_flap_last_seen` | P3 | P3 | 지문 + 전 키 만료 sweep | `signal_detection.py:240-268` |
| `_storm_window` | P3 | P3 | `db_id\|server_name` | `signal_detection.py:279-292` |
| `_correlation_clusters` | P3 | P3 | **존(db_id) 또는 db_id\|도메인 — 호스트를 가로지름** + 전 스코프 sweep | `signal_detection.py:436-460·305-318` |
| `_topology_graph_cache` | P3(await 로드) | P3 | db_id(TTL) | 위상 가중 on일 때만 |
| `_recent_event_texts` | P3(의미 근접중복 주석) | P3 | **db_id(호스트를 가로지름)** + 전 스코프 sweep | `dedup_service.py:299-346` · 감사 주석 전용 |
| `_apm_persist`·`_apm_persist_pending`·`_apm_sent_severity` | P3 | P3 | 지문 + 전 키 sweep·상한 | `alarm_worker.py:1350-1403` |
| `_silence_cache` | P3 | P3 | 전역(TTL 캐시) | `alarm_worker.py:654-663` |
| `_last_*_evidence` · `_last_self_heal_duration` | P3 | P3(같은 알람) | 전역 1칸 | **`_last_self_heal_duration`은 `await _publish_incident_resolved` 뒤에 읽힌다**(`alarm_worker.py:994-1003`) → P3 잠금은 P3 안 await까지 감싸야 한다 |
| `_episodes`(사건 추적기) | P3(observe·resolve · idle sweep · 사건/멤버 상한) · **P5(`record_tier` 멤버 최종 티어 · `after_episode_graph`의 사건 필드 `apm_investigation_id`·`incident_investigation_id`·`episode_investigation_*`)** | P3(원인 조건 ④ `evaluate_link` · `related_effects` · `episode_investigation`·`incident_join`) · P5 | **사건 키 `zone\|host_key(hostname)`** — 폴스타·제니퍼가 db_id가 달라도 같은 키 | `cross_source.py:526-596` · `episode_coordinator.py:119-130·279-311·549-580` |
| `_late_promotion` · `_late_promotion_swept_at` | P5 | P5(다음 알람의 `late_promote`) | 사건 id + **전역 sweep 타이머·상한 2000** | `episode_coordinator.py:426-547` (플래그 off 기본) |
| `_episode_ctx` | P5 | P5(`maybe_episode_investigation`) | 사건 id + 상한 2000 | `episode_coordinator.py:329-404` (플래그 off 기본) |
| `_page_registry` | P3(`touch` · `signals_for`의 주기 sweep) · **P5(`observe` — 대표 PAGE·해소·반복 수 · 주기 sweep · 키 상한)** | P3(`signals_for`) | **지문(⊂ 호스트 키) · 호스트 키 `(db_id, server_name\|hostname)` · 동시발생 키 `(db_id, alarm_name)`** + **전역 sweep 타이머(60초)·상한** | `page_review.py:342-430·518-690` |

#### (b) configurable 주입물 — 그래프(P4)가 워커를 거쳐 읽고 쓰는 것

| 주입물 | P4 동작 | 워커 다른 구간과의 교차 | 키 |
|---|---|---|---|
| `active_firings` | 읽기(살아 있는 dict 참조) | **뒤 알람의 P3가 쓴다** — 위 (a) | 근본원인 스코프(P4에서야 알게 됨) |
| `decision_store` | 쓰기(판정 · 조사 · L3 상태) · **읽기 `last_l3_state(지문)`**(`alarm_notifier.py:764-779`, `l3_audit_enabled`) | P3도 append(재발 · 해소) · P5 append(사후 승격) | 지문(⊂ 호스트 키) · 나머지는 append 순서만 |
| `investigation_store` | 쓰기(조사 기록) | **P3·P5가 사건 조사 상태를 읽는다**(`episode_coordinator.py:244-311`) | 사건 키 |
| `history_redis`(Redis) | 노이즈 컨텍스트 캐시 읽기·쓰기 | 같은 키 다음 알람 P4 | `db_id:server_name:alarm_name`(`alarm_context_enricher.py:69-71`) |
| `ticket_queue` · `sse_publisher` · `incident_publisher` | append·발행 | P3도 incident resolved 발행 | 순서만(판정 무관) — open→resolved 순서는 지문(⊂ 호스트 키)로 보장 |
| `embedding_provider` | 읽기(유사도 · 내부 `threading.Lock`) | P3 주석 | 상태 없음 |
| `apm_noise_ctx` · `metric_baseline` · `feedback_store` · `noise_repo` · `history_repo` · `process_client` · `apm_client` · `sre_agent_client` · `process_kind_classifier` | 읽기 · 외부 조회 | 워커 상태 아님(기동 시 1회 생성) | — (외부 서비스 부하·상한은 C-4) |

#### (c) 워커 밖 공유 상태

| 상태 | 구간 | 키 | 비고 |
|---|---|---|---|
| 서버 식별 Redis 캐시 | P2 | hostname | 같은 매핑 재기록 — 판정 무관. **P2가 `server_name`을 바꾸므로 충돌 키는 P2 뒤에 계산한다** |
| `alarm_notifier._FOLLOWUP_TASKS` · 후속 poll 태스크 | P4 뒤 백그라운드 | — | 조사 상태를 저장소에 늦게 쓴다 — N=1에서도 비결정(이 계획 범위 밖) |
| `alarm_notifier._WARNED_WEB_BASES` | P4 | — | 로그 중복 방지만 |

#### (d) 구간을 건너는 의존 요약

| 방향 | 상태 | 처리 |
|---|---|---|
| 앞 알람 P5 → 다음 알람 P3 | 사건 멤버 티어·사건 조사 id · PAGE 레지스트리 | **충돌 키 대기**(아래) |
| 앞 알람 P4 → 다음 알람 P3/P4 | 조사 저장소(사건) · L3 상태(지문) · 노이즈 캐시 | 충돌 키 대기(사건 키 · 호스트 키 · 캐시 키) |
| 앞 알람 P5 → 다음 알람 P5 | 사후 승격 후보 · 사건 맥락 | 충돌 키(사건 키)로 같은 사건끼리는 P5가 직렬. 전역 sweep·상한은 아래 |
| **뒤 알람 P3 → 앞 알람 P4** | `active_firings`(근본원인 스코프 — 키를 P4에서야 안다) | **키로 못 막는다 → N≥2에서 P3 끝 시점 스냅샷 주입**(아래) |

#### 충돌 키 집합 (C-3 확정안)

P2 뒤 이벤트로 계산한다. 진행 중(P4~P5 미완) 알람과 하나라도 겹치면 P3 진입을 기다린다.

| 키 | 정의 | 켜는 조건 | 지키는 상태 |
|---|---|---|---|
| 호스트 키 | `host_key_of(db_id, server_name or hostname)` | 항상 | PAGE 레지스트리 지문·호스트 항목 · L3 상태(지문) · incident open→resolved 순서 · 노이즈 캐시(server_name 있을 때) — 지문은 `(db_id, server, alarm_name, resource)` 해시라 호스트 키에 포함된다 |
| 캐시 키 | `(db_id, server_name, alarm_name)` | 항상 | server_name이 빈 알람의 노이즈 캐시 공유(호스트 키로 안 잡힘) |
| 동시발생 키 | `fanout_key_of(db_id, alarm_name)` | `page_review_mode≠off` | PAGE 레지스트리 동시발생 대표(`_fanout`) |
| 사건 키 | `episode_key(zone, normalize_host_key(hostname))` (hostname 빈 값이면 없음) | 사건 추적기 있음(`cross_source_mode≠off` + 규칙 적재) | 사건 멤버 티어 · 사건 조사 id · 조사 저장소 상태 · 사후 승격 후보 · 사건 맥락 |

- 동시발생 키는 같은 알람명이 여러 서버에 퍼지는 폭주(처리량 개선의 주 대상)를 직렬로 만든다. `page_review_mode=off`면 넣지 않는다 — 그때는 이 키가 지키는 상태가 없다.
- **하네스 실측(모의 실행기 · N=4 · 지연 0~20ms · 시드 8~20개)**: 키 없음 → 합성·픽스처 모두 8/8 위반 · 호스트+캐시 → 합성 8/8(상관·동시발생 묶음) · +동시발생 → 합성 3/8(DEP-C1만) · +사건 → 픽스처 0/8(폴스타↔제니퍼 같은 호스트) · 합성 3/8(DEP-C1만) · **+`active_firings` 스냅샷 → 0/20 · 0/20**. 모의 실행기는 gather로 P1~P3를 시작 순서대로 끝내고(가짜 Redis라 P3 안 await가 실제로 양보하지 않는다) P4 지연부터 겹친다 — 완료 순서 45건 중 31자리가 바뀌었다. 스크립트: 스크래치 `emulate_w2.py`(저장소 밖).

#### 호스트를 가로지르는 전역 상태 — P5가 쓰고 다음 P3가 읽는가

| 상태 | P5 쓰기 | 다음 P3 읽기 | 판정 영향 | 처리안 |
|---|---|---|---|---|
| 스톰 창 · 상관 클러스터 · 의미 주석 풀 · 침묵 캐시 · 위상 캐시 · 각 dict의 전 키 만료 sweep | **없음**(P3 전용) | P3 | — | P3 직렬로 충분. 충돌 키 불요 |
| 사건 추적기 idle sweep · 사건/멤버 상한 | 없음(P3 전용) · P5 `record_tier`는 사건 키 단위 | P3 | 앞 알람 P5 전에 뒤 알람 P3의 idle sweep·상한이 그 사건을 닫으면 `record_tier` 무시. idle 900초 ≫ 그래프 지연(인라인 조사 최대 45초)이라 실질 없음. 상한(사건 수)은 최근 사건이 마지막에 밀린다 | 수용(상한 도달 시만 이론상 차이 — W2 테스트에 상한 경계 1건) |
| **PAGE 레지스트리 주기 sweep(`_swept_at` · 60초) · 키 상한** | **있음**(`observe` → `_maybe_sweep(now)` · `_cap`) | P3 `signals_for` → `_maybe_sweep` | sweep 시각이 바뀌면 보존 시간(미해소 48시간+) 경계에서 `fingerprint_page` 유무가 갈린다. 호스트·동시발생 창은 `review_page`가 다시 재므로 무관 | **sweep 기한 도래 시 전역 배리어**: 다음 알람이 P3에 들어가기 전 `now − _swept_at ≥ 60`이면 진행 중 전부의 P5를 기다린다(60초에 최대 1회 · 레지스트리에 공개 질의 `sweep_due(now)` 추가 — W2). 키 상한 도달(`len ≥ max_keys`) 때도 같은 배리어 |
| 사후 승격 sweep 타이머 · 후보/맥락 상한 | 있음(P5 전용) | P3는 안 읽음(다음 P5만) | 조회는 자기 사건 후보만이라 sweep 시각 무관. 상한 2000 도달 시 버리는 순서만 달라짐 | 수용(기본 off · 상한 경계만) |
| 판정 감사 jsonl | P3·P4·P5 append | 기동 복원만 | 줄 순서만 바뀜(집합 동일) — 관제 조회 정렬이 줄 순서 의존이면 표시 순서만 달라짐 | 수용(하네스는 집합 비교) |

**`active_firings` 처리안(W2)**: N≥2 경로에서만 그래프 configurable에 `dict(self._active_firings)`(P3 끝 시점 복사본)를 넣는다. 직렬에서는 P3 끝과 enricher 읽기 사이에 다른 쓰기가 없으므로 복사본과 살아 있는 참조가 같다 → 판정 동일. N=1은 기존 참조 그대로(비트 동일). 비용은 스코프 수만큼 얕은 복사(인히비션 창 300초 sweep으로 유한) · `dependency_suppression`+다홉이 꺼져 있으면 읽는 곳이 없으니 복사도 건너뛸 수 있다.

#### W2 구현 주의(실측에서 나온 것)

1. **P3 잠금은 P3 안 await를 감싼다** — `_last_self_heal_duration`을 await 뒤에 읽고(`:994-1003`), 위상 그래프 로드·주석 분류기·incident resolved 발행이 P3 안에서 await한다.
2. **충돌 키는 P2 뒤에 계산** — 역조회가 `server_name`을 등록명으로 바꾼다.
3. **현행 조기 반환(지문 중복 · 심각도 미달 · 게이트 off 중복)은 ACK를 두 번 한다**(본문 `ack_message` + `finally`). Redis XACK 두 번째는 0을 돌려 무해하지만 §4 기준 5 「이중 ACK 0」을 글자대로 재면 현행부터 걸린다 — W3 테스트는 「ACK 대상 집합 = 수신 집합 · 미ACK 0」으로 재거나 이 이중 ACK를 현행 동작으로 명시한다(N=1 비트 동일 원칙상 W2에서 고치지 않는다).
4. 하네스 사용: `noise_gate/tests/equivalence_harness.py` — `run_sequence(alarms, work_dir=…, executor=<N=k 실행기>, seed=…, max_delay=…)` 결과를 `serial_executor` 결과와 `assert_equivalent`로 비교. 입력은 `fixture_alarms()`(픽스처 25건) · `synthetic_alarms()`(합성 45건 — 같은 호스트 폭주 · 혼합 · 자가복구 짝 · 플래핑 · 상관 · 의존성 근본원인 · PAGE 재판정 3규칙 · 크로스소스 사건).

#### 팀 리드 판단 (W1 반영)
- 충돌 키는 위 「충돌 키 집합」 확정안(호스트 · 캐시 · 동시발생(`page_review_mode≠off`) · 사건(추적기 on)) + N≥2 경로의 `active_firings` P3 끝 스냅샷 + PAGE 레지스트리 정리 시점 전체 대기를 W2에 그대로 지시한다. 하네스(`noise_gate/tests/equivalence_harness.py`)의 모의 N=4에서 이 조합이 위반 0/20(픽스처·합성)이었다.
- §4 기준 5 「이중 ACK 0」은 현행 조기 반환 경로(지문 중복·심각도 미달·게이트 off 중복)가 본문 + `finally`에서 ACK를 두 번 한다(XACK는 멱등 — 무해). 그래서 기준을 「**N≥2 경로의 ACK 집합 = N=1 경로의 ACK 집합**(다중집합 동일 · 신규 경로가 추가 이중 ACK를 만들지 않음) · 유실 0」으로 읽는다. 현행 이중 ACK 자체는 바꾸지 않는다(N=1 비트 동일).

### 1.3 다중 프로세스가 안 되는 이유 (택하지 않는 안 · §6)

- 순서 의존 상태 약 15종이 프로세스 메모리(`alarm_worker.py:420-480`). 프로세스별로 나누면 dedup·자가복구·스톰·상관·사건이 프로세스마다 반쪽이 되고, 판정 감사 파일도 프로세스마다 따로 쌓인다.
- 상관·스톰·사건은 **호스트를 가로지르는** 판정이라 호스트 해시 샤딩으로도 경계에서 깨진다.
- 상태를 전부 Redis로 옮겨 원자 갱신하는 설계는 G-11의 「설계 변경」 선택지와 같고 비용이 크다 — 이 계획 범위 밖이다.

---

## 2. 개선안

### C-0 측정 (W0 · 먼저 · 동작 불변)

- **구간 계측**: P2~P6 구간별 소요(ms)를 알람 1건당 DEBUG 1줄 + 판정 감사 레코드의 선택 키(`timing` — 계측 플래그 on일 때만 키를 넣어 off면 비트 동일)로 남긴다.
- **부하 하네스**: `noise_gate/scripts/worker_load_bench.py`(신규) — 가짜 Redis 스트림 + 가짜 LLM·MCP 지연(설정 가능 · 기본 LLM 2s · 수집 0.5s)으로 알람 M건을 넣고 처리량·대기 길이(stream lag)·p50/p95 처리 지연을 낸다. 실 LLM 미사용(과금 0).
- **산출**: 현행 직렬 기준선(N=1) 수치표. 내부망 측정은 계측 플래그만 켜서 반출한다(잔여로 이월 가능).

#### W0 결과 (2026-11-02 · 로컬 · 가짜 그래프 지연 · 실 LLM·실 Redis 0)

- **구현**: `ALARM_WORKER_TIMING_ENABLED`(기본 off · `src/config/alarm.py`) · `noise_gate/application/worker_timing.py`(`StageTimer` — P1~P6 `mark`) · `alarm_worker.py` 배선(ACK 뒤 DEBUG 1줄 · configurable `alarm_timing` 콜백) · 게이트 노드 → `DecisionStore.record(timing=…)`(`p1_ms·p2_ms·p3_ms·p4_until_audit_ms`) · `noise_gate/scripts/worker_load_bench.py` · 설명 `config/settings_help/alarm.yaml` · `.env.example`. off면 타이머·키·로그 모두 없음.
- **직렬 기준선**(`python -m noise_gate.scripts.worker_load_bench --count 40` · 가짜 수집 0.5s + LLM 2s · 팀 리드 재실측과 implementer 실측 일치):

| 시나리오 | 도착 간격 | 소요(s) | 처리량(건/s) | stream lag 최대 | 미처리 최대 | p50(s) | p95(s) |
|---|---|---|---|---|---|---|---|
| distinct(서로 다른 호스트 40) | 일괄 | 100.43 | 0.398 | 40 | 40 | 50.29 | 95.42 |
| same(같은 호스트 40) | 일괄 | 100.26 | 0.399 | 40 | 40 | 50.11 | 95.24 |
| distinct | 1.0s | 100.40 | 0.398 | 22 | 25 | 31.23 | 58.32 |
| same | 1.0s | 100.28 | 0.399 | 22 | 25 | 31.09 | 58.18 |

  → 처리량은 「1 ÷ 한 건 지연(2.5s)」에 정확히 묶인다. 도착이 1초 간격이면 22건이 읽히지 않고 쌓인다.
- **검증**: 자기 테스트(`test_plan207_w0_timing.py`) + verifier(`test_plan207_verify_w0.py` 8건 — off 예외·조기 반환 3경로 비트 동일 · on/off 하네스 판정 동일 · 원 레코드 `timing`·`ts` 외 바이트 동일). verifier Critical·Major·Medium 0 · Low 4(콜백 무예외 계약 주석 — 반영 · 설명 문구 「워커가 처리한 판정 기록」 — W6 반영 · `timing` 사용자 뷰 노출 — 민감 정보 아님·수용 · DEBUG 미출력 시 render 계산 — 무시할 비용·수용).
- **폐쇄망 잔여(§4 기준 7)**: 운영에서 `ALARM_WORKER_TIMING_ENABLED=true`로 24시간 돌려 판정 기록 `timing`과 DEBUG 줄을 반출 → 구간 지연(P2 역조회·P4 그래프 비중)과 stream lag 기준선 → N 초깃값(Q2) 근거. **W0-H(다중 소비 의심) 확인 전에는 N≥2를 켜지 않는다**(Q1).

### C-1 상태 의존 표와 동등성 하네스 (W1 · 동작 불변)

- **의존 표**: 워커 상태 필드마다 「쓰는 구간 · 읽는 구간 · 키(지문/호스트/팬아웃/존/사건)」를 표로 확정해 계획서 §1.2 아래에 붙인다. 그래프 노드가 워커 객체를 거쳐 읽고 쓰는 것(configurable 주입물)도 전수 확인한다.
- **동등성 하네스**: 같은 입력 알람 순서를 N=1과 N=k로 처리해 **판정 감사 레코드 집합**(티어 · 단계 · 신호 bool · 사건 id · PAGE 재판정 결과)을 비교하는 테스트 도구. 입력은 `noise_gate/testdata/cross_source/mock_events.jsonl` 등 기존 픽스처를 재사용하고, 가짜 지연을 무작위로 섞어 완료 순서를 뒤집는다.

### C-2 동시 처리 실행기 (W2 · 플래그 `ALARM_WORKER_MAX_CONCURRENCY` 기본 1)

- **구조**: 읽기 루프 → (P1·P2는 메시지별 독립이라 동시) → **P3 직렬 구간**(단일 순차 단계) → P4~P6 동시 태스크(세마포어 N).
- **백프레셔**: 진행 중 건수가 N이면 더 읽지 않는다(`count`를 남은 슬롯 수로). 읽어 놓고 붙잡는 메시지는 N을 넘지 않는다.
- **N=1이면 기존 `for … await self._process` 경로를 그대로 탄다**(코드 경로 자체를 분기 — 비트 동일 보장을 테스트가 아니라 구조로).
- **P2 선행 이동은 N≥2에서만**: 역조회는 워커 상태를 건드리지 않으므로 P3 직렬 구간 밖에서 미리 한다(최대 3s 대기가 직렬 구간을 막지 않게).
- **ACK·dead-letter 계약 불변**: 메시지별로 지금처럼 예외 → dead-letter → ACK. ACK 순서가 수신 순서와 달라도 Redis Stream 의미상 문제없다.

#### W2 결과 (2026-11-02 · 로컬 · 가짜 그래프 지연)

- **구현**: `ALARM_WORKER_MAX_CONCURRENCY`(기본 1 · 1~16 클램프 + WARNING) · `noise_gate/application/worker_concurrency.py`(`conflict_keys` · `SerialStageGate`(순번 · 충돌 키 대기 · 전역 배리어 · 진행 중 태스크 집합) · `StageSlot`) · `alarm_worker.py` — `run()`은 N≥2일 때만 `_consume_concurrent`로 가고 N=1 직렬 루프는 한 줄도 바뀌지 않았다. `_process`에는 `slot=None`이면 아무것도 안 하는 훅 3곳(P2 뒤 진입 · P3 끝 `leave_serial` · `finally` `finish`)과 `active_firings` 스냅샷 삼항식만 더했다(300여 줄 P3를 복제하지 않음 — 동등성을 구조로 보장). `PageRegistry.sweep_due(now, pending)`(진행 중 `observe`가 키를 늘려 상한 정리를 일으킬 수 있어 `pending` 추가 — 없으면 키 상한 시나리오 10/10 위반).
- **동등성**(하네스 · P2·P3 안 await에 무작위 지연 주입 — 가짜 Redis는 양보하지 않아 주입 없이는 순번이 시험되지 않는다): 합성 N=2·N=4 · 픽스처 N=4(page_review enforce/off) 모두 `assert_equivalent` · 완료 순서 뒤집힘 단언 · 예외 주입 3종(파싱 실패 · P3 예외 · 그래프 예외) · ACK 다중집합 동일·유실 0 · 진행 중 ≤ N · 같은 호스트 10건은 수신 순서 그대로. 변이 검증: 배리어 제거·순번 조기 반납·동시발생 키·사건 키·스냅샷 제거가 각각 테스트 실패로 잡힌다.
- **벤치**(N=4 · 수집 0.5s + LLM 2s · 40건 일괄):

| 시나리오 | 실행기 | 소요(s) | 처리량(건/s) | p50(s) | p95(s) | 직렬 대비 |
|---|---|---|---|---|---|---|
| distinct | n4 | 25.22 | 1.586 | 12.68 | 25.22 | **×3.98** (§4 기준 3 ≥ ×3 충족) |
| same | n4 | 100.22 | 0.399 | 50.11 | 95.20 | ×1.00 (충돌 키 직렬 — 기준 3 후단 충족) |

- **잔여**: 60초 정리 기한이 P3 진행 중에 걸리면 정리 1회가 진행 중 P5보다 앞설 수 있다(판정 영향은 보존 48시간 경계뿐) · 60초마다 진행 중 전부 대기 1회 · 키 상한(1만) 근처에서는 사실상 직렬 · 사건 상한 배리어 없음(상한 > N이면 진행 중 사건은 밀리지 않음 — 경계 테스트 고정) · 사후 승격·사건 맥락 상한(기본 off) 수용.
- **운영 적용**: N≥2는 W0-H(다중 소비 의심) 확인 뒤(Q1 · D-358 부기 1 ③).

### C-3 충돌 키 순서 보장 (W2 · C-2와 같은 Wave)

- **규칙**: 알람이 P3에 들어가기 전, 진행 중(P4~P5 미완) 알람 중 **충돌 키가 겹치는 것**이 있으면 그 후처리(P5)가 끝날 때까지 기다린다. 충돌 키 = `host_key`(db_id+서버) · `fanout_key`(db_id+알람명) · 사건 추적기 on이면 사건 소속 판정 키(W1 의존 표에서 확정). 키가 겹치지 않는 알람은 그대로 진행한다.
- **효과**: 같은 서버·같은 사건의 알람 폭주는 직렬로 처리되어(현행과 같은 순서 의미) 판정이 바뀌지 않는다. 서로 다른 서버의 알람만 겹쳐서 처리된다.
- **대기 중 앞지르기 금지 여부는 Q3** — 기본안은 「P3 진입 순서 = 수신 순서」(앞 건이 기다리면 뒤 건도 기다린다 · 단순·결정적). 대안은 키가 다른 뒤 건의 앞지르기 허용(처리량↑ · 순서가 입력과 달라짐).
- **수용 기준**: 동등성 하네스에서 N=k 결과가 N=1과 **완전히 같다**(§4 기준 2). 차이가 나면 의존 표의 키 누락으로 보고 키를 넓힌다 — 차이를 「허용 차이」로 문서화해 넘기지 않는다.

### C-4 뒤단 부하 상한 (W2)

- N은 LLM 뒤단 한도를 넘지 않게 잡는다 — 공유 MLX 서버는 동시 호출 시 Metal OOM 이력이 있고, FabriX는 호출 한도가 있다. 워커 쪽 동시 LLM 호출 상한은 N 자체로 묶는다(별도 세마포어 신설은 W0 측정에서 필요가 확인될 때만).
- 인라인 조사는 조사 서비스 상한(D-347 — 대기열 4·전역 도구 8 등)이 그대로 적용된다. N을 올려도 조사 서비스 쪽 거부·대기는 기존 결과 처리(timeout/down graceful)를 따른다.
- 설정 상한: `1 ≤ N ≤ 16`(범위 밖은 기동 시 경계값으로 자르고 WARNING 1줄).

### C-5 종료 드레인 · 미ACK 회수 (W3)

- **종료 드레인(N≥2에서만)**: 취소를 받으면 새로 읽지 않고, 진행 중 태스크를 `ALARM_WORKER_DRAIN_TIMEOUT_SECONDS`(기본 30) 동안 기다린 뒤 남은 것을 취소한다. 취소된 건은 ACK하지 않는다(pending으로 남음 — 아래 회수 대상).
- **기동 시 미ACK 회수(옵트인 · Q5)**: `ALARM_WORKER_RECLAIM_PENDING_ON_START`(기본 off). on이면 기동 직후 자기 소비자의 pending(`XREADGROUP id=0`)을 먼저 처리한다. 현행은 죽은 순간의 1건이 영구 pending이고, N≥2면 최대 N건이 된다. 재처리는 같은 alarm_id 중복 통보 위험이 있어 dedup이 지문 기준으로 걸러지는지 W3 테스트로 확인한다.

#### W3 결과 (2026-11-02)

- **설정**: `ALARM_WORKER_DRAIN_TIMEOUT_SECONDS`(30 · N≥2에서만 · 0이면 바로 취소) · `ALARM_WORKER_RECLAIM_PENDING_ON_START`(false · off면 pending 읽기 호출 0).
- **드레인**: 취소를 받으면 새로 읽지 않고 진행 중을 드레인 시간 동안 기다린 뒤 남은 것을 취소 — **N≥2에서 취소된 건은 ACK하지 않는다**(pending 유지 · `slot`이 있을 때만 갈림). N=1은 현행 그대로(처리 중 1건은 `finally` ACK — 비트 동일). §4 기준 5: 배달 = ACK ∪ pending · 교집합 0 · 신규 이중 ACK 0 · 드레인으로 마저 끝낸 N=4가 직렬과 동등.
- **팀 리드 판단(이탈 기록)**: W2 verifier의 xfail 테스트(`test_cancel_does_not_ack_message_waiting_before_p3`)와 W2 취소 테스트는 「진행 중에 취소된 건도 ACK」를 단언하고 있어 C-5(「취소된 건은 ACK 안 함 · 유실 0」)와 양립하지 않았다 → 계획서 C-5를 정본으로 두 테스트의 단언을 뒤집었다. 대안(P3 전 취소만 ACK 생략 · P3 뒤는 ACK — 재통보는 줄지만 최대 N건 유실)은 기각. 되돌리는 비용: `_process` 조건 1개 + 단언 2곳.
- **본체 종료 경로 실측**: lifespan 종료는 워커 태스크를 타임아웃 없이 await · uvicorn `timeout_graceful_shutdown` 없음 → 프로세스 안에서는 드레인 30초 보장. 끊을 수 있는 것은 프로세스 밖 강제 종료 유예(docker 10s · systemd 90s · k8s 30s) — 짧으면 SIGKILL로 남은 건은 pending(유실 아님). **폐쇄망 확인 항목**: 운영 종료 유예 ≥ 드레인(45초 이상 권장) 또는 드레인을 유예보다 작게.
- **회수**: `run()` 맨 앞에서 자기 소비자 pending을 10건씩 수신 순서로 직렬 `_process` 처리 후 ACK(묶음 커서 전진 — ACK 실패에도 무한 반복 없음 · 트림된 pending은 dead-letter 경로).
- **Q5 실측 — 재처리 중복 통보**: 재기동 뒤 회수 재처리는 인메모리 지문·alarm_id dedup이 비어 걸러지지 않는다 — PAGE 재판정 `off`·`shadow`에서 **PAGE 중복 통보**, `enforce`에서만 PAGE 레지스트리 복원으로 TICKET 강등(재처리 자체는 일어남). → **기본 off 유지**. 켜려면 재처리 중복을 막는 장치(예: 판정 저장소의 alarm_id 기록 대조)가 별도로 필요하다(범위 밖).
- **회수 on 전제 조건(W3·W4 verifier · 기본 off라 지금은 해당 없음)**: ① 재처리 중복 차단 장치 ② 배달 횟수·나이 상한(XPENDING 배달 횟수 미확인 — 처리 중 프로세스를 죽이는 메시지는 재기동마다 재처리되어 크래시 루프 · 처음 켤 때 쌓인 오래된 pending 전량 재통보 → 상한 넘으면 dead-letter 후 ACK) ③ 단일 소비 보장(소비자 이름 `worker-1` 고정 — 인스턴스가 둘이면 PEL을 공유해 이중 처리 → W5 단일 인스턴스 잠금 on 또는 W0-H 단일 소비 확인) ④ 회수 중 취소는 N과 무관하게 처리 중 1건을 ACK한다(회수는 slot 없는 직렬 경로 — C-5 N≥2 「취소 건 ACK 안 함」과 비대칭).
- **verifier 교정**: N≥2에서 dead-letter 적재 도중 취소되면 dead-letter 없이 ACK되던 것(L-1)을 pending 유지로 교정(N=1 현행 유지).

### C-6 CPU 구간 스레드 이전 (W4 · 작음)

- 임베딩 유사도 동기 호출(`alarm_context_enricher.py:141` `embedding_provider.similarity` · 의미 근접중복 주석 `dedup_service.py:310` `provider.most_similar`)을 `asyncio.to_thread`로 옮긴다 — 임베딩을 켰을 때만 이벤트 루프 막힘이 사라진다. provider에 이미 `threading.Lock`이 있다(`embedding_provider.py:95`) → **W3·W4 verifier 실측: 그 Lock은 LRU 캐시만 보호하고 모델 `encode`는 Lock 밖**이라 W4 뒤 N≥2·임베딩 두 플래그 on에서 P3·P4가 다른 스레드에서 동시에 `encode`한다 → `encode` 직렬화로 교정(W4 교정).
- 판정 감사 jsonl append는 건당 수백 바이트라 이전하지 않는다(W0 수치가 반대를 보이면 재검토).
- **W4 결과(2026-11-02)**: 스레드로 가는 것은 provider 호출 하나(`most_similar` · `similarity`)뿐이고, `_recent_event_texts` 스냅샷·추가·sweep과 enricher ctx 첨부는 루프 스레드에 남겼다(`DedupService._prepare_semantic_dedup`/`_finish_semantic_dedup` · async 변형 `annotate_semantic_dedup_async` · enricher `_annotate_root_text_similarity_async`). provider 없음·inert·빈 텍스트·후보 없음·플래그 off면 스레드 왕복 0(원 분기 그대로). 기존 동기 seam 유지 · 가짜 provider로 N=1·N=4 동등. 잔여: 첫 `is_available()`의 모델 lazy 로드는 여전히 루프에서(프로세스당 1회).

### C-7 (조건부 · G-11 결정 뒤) 워커 별도 프로세스 · 단일 인스턴스 가드 (W5)

- **진입점**: `python -m noise_gate.worker`(신규) — 본체 기동부의 `_start_alarm_worker`와 같은 조립을 독립 프로세스로 한다. 본체는 `ALARM_ENABLED=true`여도 `ALARM_WORKER_IN_PROCESS=false`(신규 · 기본 true = 현행)면 워커를 띄우지 않는다. 효과: 워커가 API와 다른 코어·다른 이벤트 루프를 쓴다(API 지연과 상호 간섭 제거).
- **단일 인스턴스 가드**: 워커 기동 시 Redis 잠금(`SET alarm:worker:lock <host:pid> NX EX 30` + 10초 갱신)을 잡지 못하면 소비하지 않고 ERROR 1줄을 남긴다 — G-11이 의심하는 다중 소비를 구조로 막는다. 옵트인 `ALARM_WORKER_SINGLETON_GUARD`(기본 off · 켜는 시점은 W0-H 결과 뒤).
- 이 Wave는 **Q1·Q4 결정 뒤에만** 착수한다 → **Q4 결정(2026-11-02): 착수**. 운영 적용(분리 전환·잠금 on)은 W0-H 뒤(Q1).

#### W5 결과 (2026-11-02 · 로컬 · 가짜 Redis)

- **구현**: 진입점 `noise_gate/worker.py`(SIGTERM/SIGINT → W3 드레인 경로 · 상한 `SHUTDOWN_GRACE_SECONDS` = 드레인 상한 + 5s 넘으면 재취소 · 설정 로드 실패 종료 코드 2 · in_process=true ∧ 가드 off에서 띄우면 이중 소비 경고 1줄) · `noise_gate/infrastructure/worker_lock.py` `WorkerSingletonLock`(키 `alarm:worker:lock` · 값 `<host>:<pid>` · `SET NX EX 30` · 10s Lua 비교 갱신 · Lua 비교 삭제 · 갱신 0 또는 Redis 예외 = 즉시 상실 → 소비 취소) · `alarm_worker.run()` 본문을 `_consume_stream()`으로 그대로 추출 + `_consume_guarded()` · `server.py` `_start_alarm_worker` 분기(INFO 1줄) · `AlarmConfig.worker_in_process=True`·`worker_singleton_guard=False` · `arch_check` `noise_gate.worker`=entry. 가드 off면 잠금 Redis 호출 0(현행 비트 동일).
- **본체 ↔ 워커 메모리 공유**: 본체(`src/`)가 워커 인스턴스·모듈 전역을 직접 읽는 경로 0건(실측) — 공유는 전부 파일·Redis.
- **분리 모드 제약(알려진 것 · 사용자 결정 대상은 ①)**: ① 담당 강조 프로세스 종류 판정 콜백(`src.domain.middleware.classify_process_kind`) 미주입 → 분리 모드에서 `top_process_kinds` 단서 빠짐(이 모듈은 pydantic·yaml을 써 D-139 부기의 `noise_gate → src` 허용 범위 밖 — 허용하려면 D-139 부기 필요. 팀 리드 판단: 제약 유지·매뉴얼 기재) ② 본체만 정지 중이면 워커가 발행한 incident open/resolved·SSE 이벤트 유실 ③ 설정 반영은 워커 프로세스 재기동 ④ 진입점 로그는 stdout만(파일 수집은 서비스 관리자 리다이렉트) ⑤ 임베딩 등 지연 로드 자원 메모리 이중. 잔여 위험: 잠금 상실 직후 드레인 중 진행분과 새 인스턴스가 드레인 상한 안에서 짧게 겹칠 수 있다.
- **테스트**: `test_plan207_w5_worker_process.py` 32 · `test_plan207_w5_in_process.py` 4. Lua 원자성 자체는 가짜 Redis로 검증 불가(스크립트 내용 단언 수준) — 폐쇄망·실 Redis 확인 항목.
- **매뉴얼**: A-59 처리 흐름 그림·「3. 워커 기동 조건」 · A-52 기동 절차(분리 모드 프로세스 표·설정 표) 반영(D-255).
- **W5 verifier(2026-11-03 · `test_plan207_verify_w5.py` 20건)**: Critical·Major 0. 중점 6관점(가드 off 비트 동일 · W3 ACK·드레인 계약 · 진입점 신호 · Lua 자기 값 비교 · 계층 · 비밀 노출) 충족. 지적과 교정: **Medium** 갱신 1회 일시 실패 = 영구 정지 + 정상 반환(종료 코드 0 → `Restart=on-failure` 미재기동) → 교정: Redis 예외는 「마지막 성공 갱신 뒤 경과 + 주기 < TTL」 동안 WARNING 후 재시도(Lua 0은 즉시 상실) · `AlarmWorker.lock_outcome` · 진입점 종료 코드 0 정상 · 1 예외·정지 상한 초과 · 2 설정 실패 · **3 잠금 상실 · 4 획득 실패** · 매뉴얼 A-52·설정 도움말에 「끝나면 서비스 관리자 재기동 필요」. **Low** `SET NX` 예외 시 연결 미종료 → `aclose()` 후 재발생(교정). **Low** 드레인 상한 뒤 재취소 대기 무상한 → `SHUTDOWN_GRACE_SECONDS` 상한 뒤 WARNING·종료 코드 1(교정 · 이 경우 루프 미정리라 「Task was destroyed」 로그 가능). **Low** 잠금 획득이 `_build_*`(임베딩 적재 포함) 뒤 → 잔여(획득 못 하는 인스턴스도 생성 비용을 치른다 · 동작 결함 아님). verifier의 「1회 실패 = 영구 정지」 특성 테스트는 새 계약(재시도·소비 계속)으로 팀 리드가 뒤집었다.

### C-8 설정·문서·매뉴얼 (W6)

- `src/config/alarm.py`(신규 필드) · `src/api/settings_catalog.py` 등재 · `config/settings_help/` 설명 · `.env.example`.
- **W6 결과(2026-11-03)**: `config/settings_help/alarm.yaml` 5키(`ALARM_WORKER_MAX_CONCURRENCY`·`_DRAIN_TIMEOUT_SECONDS`·`_RECLAIM_PENDING_ON_START`·`_IN_PROCESS`·`_SINGLETON_GUARD`) 도움말 + `ALARM_WORKER_TIMING_ENABLED` 범위를 「워커가 처리한 판정 기록」으로 좁힘(W0 verifier Low) · `.env.example` 5키 · `docs/28` §4-D(동시 처리·충돌 키·배리어·드레인·회수 전제·분리 모드 절차와 제약·운영 적용 순서) · 관리자 매뉴얼 A-52 설정 표 보강(D-255). 카탈로그 구획(`SECTION_BY_KEY`)은 기존 ALARM_WORKER 키 관례대로 미등재. `test_t2_curated_groups_have_full_coverage`(원래 실패) 누락 7→2(`API_RELOAD`·`NOISE_HOST_LINK_ENABLED` — 범위 밖).
- **계획 끝 회귀(2026-11-03 · 팀 리드)**: `regress.py --base afc5626c --files <회귀 대상 81>` — 본체 통과 11,716 · sre_agent 3,193 통과/실패 0 · 실패 귀속 원래 실패 31(e2e_polestar 19 · plan147_w4 gateway 3 · settings_catalog_sections 1 · settings_help t2 1 · plan201_w1 1 · plan201_w6_verify 1 · bench_plan118 1 등) · **이번 변경 탓 1**(`test_noise_page_review_console_verify.py::test_doc28_env_lines_have_no_inline_comment_and_real_keys` — §4-C 자르기가 새 §4-D까지 잡음 → 헬퍼를 다음 `## ` 머리에서 끊게 교정) · ruff 이번 diff 줄 1(verify_w3w4 E501 — 교정). W5 verifier 교정 뒤 교정 파일 회귀(12파일) 본체 통과 1,840 · 실패 0 · ruff·mypy 이번 diff 줄 0. 범위: 모듈 단위 — 전체 미실행. 권고 블록: 허브 모듈(`src.config.alarm`·`src.config.services` 87%) + 공개 반환 주석 변경 `--wide`(notification_bus.subscribe · mock_polestar_events.Judge.current_offset/wait_for · noise_gate_scenario_test.log_offset/wait_decision).
- **kb mtime 테스트 귀속**: `tests/test_api/test_plan187_w9_kb.py::test_kb_draft_wip_ignores_body_and_never_writes_under_sre_agent`는 계획 끝 회귀에서 통과. 회귀 전후 sre_agent 트리(.venv·__pycache__ 제외) mtime·size 스냅샷 차이 0, sre_agent 스위트 단독(3,193)도 쓰기 0 → 앞선 실패는 같은 시각 피어 세션(트랙 A)의 sre_agent 파일 편집 탓(코드 결함 아님).
- `docs/` 노이즈 운영 문서에 동시 처리·충돌 키·종료 드레인 설명. W5를 하면 관리자 매뉴얼(`scripts/manual/` → `src/static/manual/admin.html`)의 처리 흐름 그림(「AlarmWorker(본체 프로세스)」 문구)과 기동 절차를 갱신한다(D-255). W5를 안 하면 매뉴얼 대상은 설정 항목 설명뿐이다.

---

## 3. Wave 구성

> **v1.1 순서**: 트랙 A(장애 조사 동시성 · §8.4 A0~A5)를 먼저 하고, 트랙 C(관제 집계 루프 막힘 · §7 M-1·M-2·M-3·M-4)를 그다음, 아래 트랙 B(알람 워커 W0~W6)를 마지막에 한다. 트랙 B의 W번호는 그대로 둔다.

**트랙 B — 알람 워커**

| Wave | 범위 | 산출 | 회귀 |
|---|---|---|---|
| **W0** | C-0 측정 | 구간 계측(플래그 off 비트 동일) · `worker_load_bench.py` · 직렬 기준선 수치표 | 자기 테스트 + `regress.py --no-tests` |
| **W1** | C-1 의존 표·동등성 하네스 | 계획서 §1.2 의존 표 · 하네스(테스트 유틸) | 자기 테스트 + 정적 게이트 |
| **W2** | C-2·C-3·C-4 실행기·충돌 키·상한 | `alarm_worker.py` 실행기 분리 · `src/config/alarm.py` 필드 → `[웨이브 회귀 필요]` 예상 | Wave 끝 모듈 단위 회귀 |
| **W3** | C-5 드레인·회수 | 종료 드레인 · pending 회수(옵트인) | 자기 테스트 + 정적 게이트 |
| **W4** | C-6 스레드 이전 | 임베딩 호출 `to_thread` | 자기 테스트 + 정적 게이트 |
| **W5** | C-7 (조건부) | `noise_gate/worker.py` 진입점 · 본체 기동 분기 · 단일 인스턴스 가드 | 자기 테스트 + 정적 게이트 |
| **W6** | C-8 문서·설정 | 카탈로그 · 설명 · 문서 · (W5 시) 매뉴얼 | 계획 끝 모듈 단위 회귀 1회(D-303) |

---

## 4. 수용 기준

1. **N=1 비트 동일**: 신규 플래그 전부 기본값에서 기존 워커 테스트 전부 통과 · 동등성 하네스 N=1 결과가 변경 전 HEAD와 같다.
2. **N=k 판정 동일**: 동등성 하네스에서 N=4(가짜 지연 무작위 · 완료 순서 뒤집힘)의 판정 감사 레코드 집합이 N=1과 **완전히 같다**(타임스탬프·소요 시간 키 제외).
3. **처리량**: 부하 하네스(가짜 LLM 2s · 서로 다른 호스트 알람 40건)에서 N=4 처리량이 N=1의 **3배 이상**. 같은 호스트 40건이면 N=4도 N=1과 같은 순서로 처리된다(충돌 키 직렬).
4. **백프레셔**: 진행 중 건수가 어느 시점에도 N을 넘지 않는다.
5. **종료**: 드레인 시간 안에 끝난 건은 ACK, 못 끝난 건은 pending — 유실·이중 ACK 0(테스트로 확인).
6. **게이트**: `arch_check.py --ci` · `overfit_check.py --ci` 통과 · 바꾼 파일 ruff·mypy 신규 0.
7. **내부망(잔여 이월 가능)**: 계측 on 24h 반출로 구간 지연·stream lag 기준선 확보 → N 선택 근거.

---

## 5. 미결 질문 (사용자 결정)

| # | 질문 | 권고 |
|---|---|---|
| **Q1** | `plans/162` W0-H(운영 다중 소비 의심) 확인과 이 계획의 순서 | W0~W5 구현은 W0-H와 독립이라 병행 · **운영에서 N≥2를 켜는 것 · 단일 인스턴스 잠금 on · 워커 별도 프로세스 전환은 W0-H로 단일 소비가 확인된 뒤** (다중 소비 상태에서는 동시성 효과·판정 수치를 믿을 수 없다) — D-358 부기 1 ③ |
| **Q2** | 폐쇄망 운영 N 초깃값 | 코드 기본 1(off) 유지 · W0 내부망 측정 뒤 2 → 4 단계 상향 |
| **Q3** | 충돌 키가 다른 뒤 건의 P3 앞지르기 허용 | 불허(기본안 · 수신 순서 유지 · 결정적). 측정에서 머리 막힘이 크면 재검토 |
| **Q4** | W5(워커 별도 프로세스 · 단일 인스턴스 가드) 착수 여부 | **결정(2026-11-02 · D-358 부기 1)**: 착수 — 별도 프로세스 + 단일 인스턴스 잠금 둘 다 · 플래그 기본은 현행(본체 안 · 잠금 off) · 운영에서 켜는 시점은 W0-H 뒤 |
| **Q5** | 기동 시 pending 회수 기본값 | 기본 off · W3 테스트로 재처리 중복 통보가 지문 dedup에 걸리는지 확인한 뒤 on 검토 → **W3 실측: 걸리지 않는다**(재기동 뒤 인메모리 dedup 공백 — off·shadow에서 PAGE 중복) — 기본 off 유지 · on 전 중복 차단 장치 필요 |

---

## 6. 하지 않는 것

- **다중 프로세스·다중 소비자로 메시지 분산**(상태 Redis 이전 포함) — §1.3. 필요해지면 G-11 「설계 변경」 선택지로 별도 계획.
- **P3(게이트 사전 판정) 동시화** — 순서 의존 상태를 잠금으로 쪼개는 비용 대비 이득이 작다(P3는 대부분 동기 · 짧다). W0 수치가 P3를 병목으로 보이면 재검토.
- 그래프 노드 내부 병렬화(컨텍스트 수집 태스크 등)는 이미 노드 안에서 하고 있는 것 이상으로 바꾸지 않는다.
- 조사 서비스(sre_agent) 쪽 동시성 — `plans/200` 소관.
- 실 LLM 측정 — 가짜 지연 하네스로 한다. MLX 확인은 바뀐 경로 대표 1회만(필요 시).

---

## 7. 다중 사용자 동시성 점검 (v1.1 · 2026-11-02)

> **사용자 제기(2026-11-02)**: *"다중 사용자의 동시성을 제공할 수 있도록 계획이 되어 있는지 확인하여 계획을 업데이트하고 정해야될 사항은 인터뷰를 진행하라."*
> **점검 결론**: v1.0(§0~§6)은 **알람 소비 처리량**(워커 1개)만 다뤘고, 여러 사용자가 동시에 쓰는 API·화면 경로는 다루지 않았다. 아래 빈 곳 U1~U5를 찾았다. 범위·우선순위는 인터뷰(§7.4)로 정한 뒤 Wave에 넣는다.

### 7.1 이미 갖춘 것

| 항목 | 근거 |
|---|---|
| 알람·노이즈 API 핸들러 전부 `async def` | `src/api/routes/{alarm,noise_dashboard,investigation_label}.py` |
| 조사 요청 부하 상한(대기열·전역 도구·시간당 예산) | D-347 |
| 실시간 조사 허브 — 조사당 폴러 1개를 구독자가 공유 | `plans/187` W14 · `src/api/investigation_live_hub.py` |
| 24시간 요약 집계 — 스레드 이전 + 5초 상한 + 중단 신호 (**U1·U2 교정의 선례**) | `src/api/routes/duty_areas.py:233-250` |
| 호스트 연결 저장 WATCH 원자화 · 호출 상한 Redis 공유 | D-348 부기 1 |
| uvicorn 다중 워커 기동 검출 시 경고 1줄 | `plans/184` S3 · `src/api/server.py` `_warn_multi_worker` |

### 7.2 빈 곳

| # | 현황 | 영향 | 근거 |
|---|---|---|---|
| **U1** | 관제 집계가 판정 파일 **전체**를 이벤트 루프에서 동기로 읽는다 — `plans/194` W2의 증분 꼬리 캐시 **밖**이라 매 호출 전체를 다시 읽는다. `/admin/noise/health`와 `/alarm/metrics`는 한 요청에 `aggregate` + `meta_alerts`(안에서 `aggregate` 재호출 + 자체 순회). 판정 파일은 **보존 상한이 없다** | 순회 동안 같은 프로세스의 **모든 요청**(질의응답 SSE 포함)과 알람 워커가 멈춘다. 관제 화면은 사용자마다 60초마다 집계를 부른다 → 사용자 수 × 파일 크기에 비례 | `decision_store.py:509`(`aggregate` 전 파일 순회) · `:615`·`:677`(`meta_alerts`) · `:849`(「보존 상한 없이 쌓이고」) · `noise_dashboard.py:822-823` · `alarm.py:643-644` · `src/static/js/noise.js:75`(`SUMMARY_MS = 60000`) |
| U2 | 깔때기·시계열·상위 억제·목록은 꼬리 2만 줄(`decision_store_max_lines`) + `plans/194` W2 증분 캐시(경로 단위 · 새 바이트만 파싱)로 **대부분 해소됐다** — 같은 프로세스 첫 호출만 약 0.3s, 이후 27ms | 기동 직후 첫 호출만 짧게 정지 · 별도 조치 불요(트랙 C에서 확인만) | `noise_dashboard.py:720·763·793·897` · `decision_store.py:742` |
| **U3** | SSE 팬아웃 버스의 구독자 큐에 상한이 없다(`asyncio.Queue()` · `await q.put`) | 느린 구독자(탭 백그라운드·느린 망)의 큐가 계속 자란다. 화면은 30초 재동기화로 누락을 메우므로 상한을 두고 오래된 것을 버려도 안전하다 | `noise_gate/infrastructure/notification_bus.py:18-31` · `noise.js:74`(`RESYNC_MS`) |
| **U4** | 본체는 uvicorn 단일 프로세스 전제(`workers` 미지정). 코어를 늘리려고 다중 워커로 띄우면 프로세스마다 알람 워커가 떠서 **다중 소비(G-11)**가 되고, 프로세스 메모리 상태(`alarm_bus` · 조사 실시간 허브 · 질의 결과 저장소)가 프로세스별로 갈린다 | 지금은 한 코어(GIL 하나)로 모든 사용자를 처리 | `src/main.py:103-108` · `src/api/query/store.py:24` · `plans/187` §W14 다중 워커 전제 |
| **U5** | 알람 워커가 API와 같은 이벤트 루프를 쓴다 | 알람 폭주 때 워커의 동기 구간(P3)과 화면·질의 요청이 서로 지연시킨다. C-2(N≥2)로 워커 처리량이 늘면 루프 점유도 늘 수 있다 | `src/api/server.py:694` |

**U1 합성 측정**(2026-11-02 · 이 맥 · 레코드 약 1KB · `aggregate(window_seconds=86400)` 1회 — `/admin/noise/health`는 이 순회를 3회 한다):

| 판정 파일 | 크기 | `aggregate` | `funnel` (꼬리 2만 줄) | `list_decisions` |
|---:|---:|---:|---:|---:|
| 2만 건 | 21MB | 144ms | 181ms | 10ms |
| 10만 건 | 106MB | 694ms | 218ms | 11ms |
| 30만 건 | 318MB | 2,089ms | 292ms | 10ms |

재측정(30만 건 · 라우트처럼 요청마다 새 인스턴스 · 같은 프로세스 2회 연속): `aggregate` 2,085 → **2,097ms**(캐시 없음) · `meta_alerts` 2,167 → **2,116ms**(캐시 없음) · `funnel` 330 → **27ms**(증분 캐시). 즉 `/admin/noise/health` 1회 ≈ **4.2s** 루프 정지가 매번 반복된다.

폐쇄망 수신은 1회차 기준 하루 약 2만 건이다(`plans/162` §12 F5 — 미집계 11,942건 = 59.7%). 판정 레코드는 그보다 적지만 상한이 없어 수 주~수 개월이면 위 표의 아래쪽에 닿는다. 실 레코드 크기는 내부망 파일로 다시 잰다.

### 7.3 개선안 후보 (범위는 §7.4 인터뷰로 확정)

| 후보 | 내용 | 대상 |
|---|---|---|
| **M-1** 루프 밖 실행 | 집계·목록 조회를 `asyncio.to_thread` + 상한 시간 + 중단 신호로 옮긴다(24시간 요약 선례와 같은 형태) | U1 |
| **M-2** 사용자 공유 짧은 캐시 | 같은 창(window) 집계 결과를 짧게(예: 15초) 캐시해 사용자 수와 무관하게 순회 1회로 만든다. 동시에 같은 집계를 부르면 하나만 계산하고 나머지는 결과를 기다린다 | U1 |
| **M-3** 집계 근본 교정 | (a) 꼬리 역방향 읽기로 창 밖에서 멈춤(`iter_recent_decisions` 방식) · (b) 판정 파일 보존·회전 · (c) 증분 집계(읽은 위치 기억) 중 택 | U1 |
| **M-4** SSE 큐 상한 | 구독자 큐에 상한 + 넘치면 가장 오래된 이벤트를 버림(재동기화가 메움) · 버림 건수 로그 | U3 |
| **M-5** 워커 분리 | C-7(W5)과 같음 — 워커를 별도 프로세스로 옮겨 API 루프와 분리 | U5 · U4의 전제 |
| **M-6** 본체 다중 워커 | uvicorn `workers≥2` 허용 — M-5 + 프로세스 메모리 상태(`alarm_bus` · 실시간 허브 · 질의 결과 저장소)의 Redis 공유화 | U4 |

### 7.4 인터뷰 결과 (2026-11-02 · §8.0)

- 사용자가 실제로 겪은 느림은 **장애 조사**였다 → 1순위는 §8(트랙 A).
- 트랙 C(이 절의 U1·U3·U5 교정 — M-1·M-2·M-3·M-4)는 트랙 A 다음. 단 트랙 A 측정(A0)에서 조사 화면 경로가 U1(전체 순회)을 타는 것이 보이면 A 안에서 함께 고친다.
- **M-6(본체 uvicorn 다중 워커)은 범위 밖**(사용자 확인). M-5(워커 분리)는 트랙 B의 W5(조건부)로 남는다.
- M-3 근본 교정 방식(꼬리 역방향 읽기 / 보존·회전 / 증분 집계)은 트랙 C 착수 때 정한다 — 판정 파일 보존 정책은 감사 요구와 얽혀 있어 그때 묻는다.
- **사용자 결정(2026-11-02 · D-358 부기 1)**: M-3 = **(a) 최근 구간만 읽기 + (b) 판정 파일 회전 둘 다**. 회전 조각은 지우지 않고 보관(삭제·보존 기한은 범위 밖) · 판정 파일을 읽는 곳은 모두 조각을 가로질러 읽는다 · 플래그 기본 off(기본 on 여부는 따로 묻는다). M-5(워커 분리) = 트랙 B **W5 착수**(별도 프로세스 + 단일 인스턴스 잠금 · 운영 적용은 W0-H 뒤).

### 7.5 트랙 C 결과 (2026-11-02 · M-1·M-2·M-4 · M-3 진행 — 사용자 결정 §7.4)

| 설정(신규 · 재시작 반영) | 기본 | 동작 |
|---|---|---|
| `NOISE_AGGREGATE_OFFLOAD_ENABLED` | false(현행) | 켜면 `/admin/noise/health`·`/alarm/metrics` 집계를 루프 밖(`to_thread`) + 상한 시간 + 중단 신호로 돌리고, 같은 키(파일 실경로·기록 활성·창·임계값 3) 결과를 사용자 공유 캐시 + 단일 비행으로 나눈다. `health_snapshot`이 `aggregate`+`meta_alerts`를 파일 1회 순회로 낸다(종전 2~3회) |
| `NOISE_AGGREGATE_TIMEOUT_SECONDS` | 30 | 넘기면 순회 중단 + **503 + 사유**(직전 캐시로 대신하지 않음 — 무수신 워치독 의미 보존 · 화면은 첫 로드 실패만 빨간 알림, 60초 갱신 실패는 마지막 값 유지) |
| `NOISE_AGGREGATE_CACHE_SECONDS` | 15 | 0이면 캐시 없이 동시 호출만 합친다 · 실패는 캐시하지 않음 |
| `NOISE_ALARM_BUS_QUEUE_MAX` | 0(무상한 · 현행) | 양수면 구독자 큐 상한 · 넘치면 가장 오래된 것 버림 · 구독자별 버림 수 · WARNING 첫 1회 + 60초 요약. **주의**: 관제 피드(`/admin/noise/stream`)는 30초 재동기화가 메우지만 **포털 알람 카드 스트림(`/alarm/notifications/stream` · `app.js`)은 재동기화가 없어 버린 카드가 다시 나오지 않는다** — 켜는 운영이면 별도 검토 |

- 합성 측정(`/admin/noise/health` 동시 5요청 · `LoopLagSampler`): 10만 건 off 루프 최대 정지 **5,000ms** → on **46ms** · 30만 건 off **15,135ms** → on **30ms**.
- U2 확인: 요약·추이·상위 억제·목록·결정 추적은 모두 `_tail_records` → `jsonl_tail.tail_records` 증분 꼬리 캐시 경로. 조사 화면 `get_decision`은 데운 뒤 2.8~6.1ms(첫 호출만 146~199ms) — 루프 정지 요인 아님.
- 매뉴얼(D-255) 반영: 관리자 「헬스 조회」·「반영 시점과 영향」·「문제 해결」·실시간 피드 절.
- **M-3 결과(2026-11-02 · (a)+(b) · D-358 부기 1 ①)**:

| 설정(신규 · 재시작 반영) | 기본 | 동작 |
|---|---|---|
| `NOISE_DECISION_STORE_RECENT_SCAN_ENABLED` | false(현행) | 집계 4종(`aggregate`·`meta_alerts`·`health_snapshot` 등)을 파일 끝에서 거꾸로 읽다가 「창 하한 − 600초」보다 오래된 결정을 만나면 멈춘다 |
| `NOISE_DECISION_STORE_ROTATE_MAX_BYTES` | 0(회전 없음) | 크기 이상이면 다음 쓰기 직전에 회전 |
| `NOISE_DECISION_STORE_ROTATE_DAILY` | false | 날짜(UTC)가 바뀌면 다음 쓰기 직전에 회전 |

  - 회전 조각 `<파일>.<UTC YYYYmmddTHHMMSSffffffZ>[-순번]` · **지우지 않음**(보존 기한·디스크 상한은 범위 밖 — 디스크 사용은 계속 는다). 쓰기 경로 `flock` + inode·조건 재확인 뒤 rename(다중 프로세스에서도 1회) · **fcntl 없는 플랫폼(Windows)은 회전 설정을 무시하고 경고 1줄**(교정 — 무조건 `import fcntl`이 Windows 임포트를 깨뜨리던 것을 팀 리드 검토에서 잡음).
  - 조각 횡단 읽기: 집계 4종 · `_tail_records` 계열(화면·라우트·조사 brief — 증분 꼬리 캐시 fstat 동일성) · `iter_recent_decisions`(역방향) · `recent_decisions`·`count_recent_values`(순방향 전량 — 기동·점검용) · `last_l3_state` · 스크립트 5종(커서 `(dev, ino, size)` · 정수 커서 호환).
  - **창보다 넓은 기간을 쓰는 지표: 없음**(워치독 total · last_event · MTTR · collection_failed 모두 창 안). on/off 차이 가능 2가지(멈춘 지점 앞의 시각 없는·깨진 레코드 · 창 하한보다 10분 넘게 오래된 결정보다 파일상 앞에 놓인 창 안 레코드)는 정상 writer(ts=now)에서는 생기지 않음 — 테스트에 「알려진 차이」로 고정.
  - 합성 측정(30만 건 · 283MB): `aggregate(86400)` 1,433 → **109ms** · `aggregate(3600)`·`health_snapshot(3600)` 1,476 → **6ms** · `meta_alerts(3600)` 2,852 → **11ms** · 결과 off = on · append 1건 62.7 → 69.4µs.
  - 매뉴얼(D-255): 관리자 「결정 기록 파일과 줄 상한」·헬스 조회·설정 항목·「24. 결정 기록」·「남는 곳 — 파일」 회전 표.
- 결정 전 상태(기록): 상한 시간을 넘길 만큼 판정 파일이 커지면 켠 상태에서도 헬스가 503이 된다. 선택지 (a) 꼬리 역방향 읽기로 창 밖에서 멈춤 · (b) 판정 파일 보존·회전(감사 요구와 얽힘) · (c) 증분 집계(읽은 위치 기억).

---

## 8. 장애 조사 동시성 — 트랙 A (v1.1 · 1순위)

### 8.0 사용자 결정 (인터뷰 2026-11-02)

| 항목 | 결정 |
|---|---|
| 겪은 증상 | 2명이 각자 장애 조사를 돌리면 **둘 다 진행은 되지만 각각 혼자일 때보다 몇 배 오래 걸린다**(대기열 대기가 아니라 자원 공유로 인한 감속) |
| 목표 | **동시 5건**의 장애 조사가 각각 **혼자 돌 때의 2~3배 안**에 끝난다(3배 = 합격선 · 2배 = 목표) |
| D-347 개정 | 동시 실행 상한 `INVESTIGATION_MAX_CONCURRENT` 2 → **5** 승인. 전역 도구 동시 8 · 시간당 예산 20은 측정 뒤 조정. **코드 기본값 변경은 측정이 목표 충족을 보인 뒤**(A4) — 그때 D-347 부기를 쓴다 |
| 제약 | LLM 뒤단 용량은 코드로 늘릴 수 없다 — 원인이 LLM 대기면 코드 쪽은 호출 수 줄이기·동시 호출 상한까지, 나머지는 서빙 증설 요청 |
| 범위 밖 | 본체 uvicorn 다중 워커(M-6) · 알람 워커 다중 프로세스 · 과금 LLM 측정 |

### 8.1 현행 (코드 실측 · HEAD `16ea44fa`)

| 항목 | 현행 | 동시 5건 목표에 대한 의미 |
|---|---|---|
| 동시 실행 조사 | `investigation_max_concurrent` **2**(`sre_agent/sre_agent/settings.py:136`) · `threading.BoundedSemaphore`(`investigation_dispatcher.py:631`) | 5건이면 3건이 대기 → 목표 불가. 상한부터 막는다 |
| 대기열 | `investigation_max_queued` 4(`settings.py:142`) | 실행 5 + 대기 4 |
| 시간당 예산 | `investigation_hourly_budget` 20(`settings.py:152`) | 5명이 반복 시험하면 한 시간 안에 닿을 수 있다 → 측정 뒤 조정 |
| 실행 단위 | 조사마다 **스레드**(`investigation_dispatcher.py:693` `threading.Thread(target=self._worker)`) — **프로세스 하나 · GIL 하나** | CPU 구간(HolmesGPT 응답 파싱·프롬프트 조립·서술 생성·JSON 처리)이 겹치면 코어 하나를 나눠 쓴다 |
| 조사 에이전트 | HolmesGPT `DiagnosisAgent` 1개를 프로세스 안에서 공유(생성 시만 잠금 · `interface/mcp_service.py:241-250`) | 실행은 스레드별 — 공유 객체의 스레드 안전성은 A0에서 확인 |
| 플레이북 작업자 | `sre_playbook_max_workers` 4(조사마다 · `settings.py:350`) | 5건 × 4 = 최대 20 스텝 동시 |
| 도구 배치 동시 | `sre_tool_batch_concurrency` 4(`settings.py:374`) | — |
| **전역 도구 동시** | `sre_tool_global_concurrency` **8**(축별 · 모든 조사가 공유 · `settings.py:381` · `mcp_tool_client.py:46-75`) | 5건이 나눠 쓰면 조사당 평균 1.6자리 — **자리 대기가 감속 원인 후보** |
| 조회 대상 보호 | mcp_server 소스별 동시 상한 · apm_gateway 소스별 in-flight 8 · 제니퍼 소스당 5/s(D-347 ③ · `plans/200` §1.4) | 전역 도구 상한을 올려도 이 상한에서 다시 막힐 수 있다 — 바꾸지 않는다 |
| 같은 호스트 가드 | 같은 호스트를 조사 중이면 새 조사를 **거부**하고 진행 중 조사에 합류시킨다(`investigation_dispatcher.py:766-771` · L-4) | **동시 5건 목표는 서로 다른 호스트 5건 기준**이다. 같은 장애를 5명이 누르면 1건 + 합류 4명(이미 목표 충족) |
| 미확정 실체 | 조사 서비스 status 조회까지 무응답이 되는 현상(GIL 경합·소켓) — `plans/194` S7 · D-347 주의 ③ | A0의 CPU·GIL 측정이 S7 판정 재료를 겸한다 |
| 측정 도구 | `sre_agent/scripts/investigation_bench/`(runner · `fake_llm`(asyncio 서버) · `world_server` · `playbook_timeline`) — **동시 실행 옵션 없음** | A0에서 `--concurrency K` 추가 |

### 8.2 감속 원인 후보

| # | 후보 | 판별 지표 (A0) |
|---|---|---|
| **R1** | LLM 뒤단 처리량 — 조사 하나가 LLM을 연달아 부르는데, 뒤단이 동시 요청을 줄 세우거나 나눠 처리 | LLM 호출 1회 지연의 K=1 대비 K=5 배율 |
| **R2** | 도구 자리 다툼 — 전역 도구 8자리(+ 조회 대상 상한) 대기 | 조사별 「자리 얻기까지 대기 시간」 합 · 대상 상한 대기 |
| **R3** | `sre_agent` 프로세스 CPU·GIL 다툼 | 조사 중 `sre_agent` 프로세스 CPU 사용률(1코어 포화 여부) · 스레드 CPU 시간 대비 벽시계 · 가짜 LLM(고정 지연)에서도 K에 따라 늘어나는지 |

가짜 LLM은 지연이 K와 무관하게 고정이므로, **가짜 LLM으로 K=5가 2배를 넘으면 R2·R3**, 가짜에서는 괜찮고 내부망 실 LLM에서만 늘어나면 **R1**이다.

### 8.3 원인별 교정안

| 안 | 대상 | 내용 | 플래그 |
|---|---|---|---|
| **A-1** 동시 실행 상한 | 공통 | `investigation_max_concurrent` 5를 설정으로 먼저 열어 측정(코드 기본은 A4에서) | 기존 설정 |
| **A-2** 도구 자리 배분 | R2 | 전역 상한을 「조사 수 × 조사당 몫」으로 다시 잡거나(예: 5 × 4 = 20 — 조회 대상 상한 D-347 ③은 그대로), 조사별 공정 배분(한 조사가 자리를 독식하지 않게 조사당 상한)을 둔다. **(§9 C1)** APM 게이트웨이 호출에 조사별 `owner`를 실어 `plans/205` W3b 공정 분배에서 조사 5건이 키 1개로 묶이지 않게 한다 | `SRE_TOOL_PER_INVESTIGATION_SHARE`(신규 · 기본 off) |
| **A-3** 조사 실행 멀티프로세스 | R3 | 접수·대기열·예산·같은 호스트 가드는 부모 프로세스에 두고, **조사 실행만 자식 프로세스**(프로세스 풀 · 크기 = 동시 상한)에서 돌린다. 진행 상황·부분 브리핑·실행 기록은 프로세스 간 큐로 부모에 올린다. 부수 효과: 타임아웃된 조사를 프로세스째 끝낼 수 있다(D-347 ② 「버린 스레드를 멈추지 못함」 해소) · status 조회가 조사 CPU와 분리된다(S7). **조건(§9 C2)**: 전역 도구·LLM 동시 상한은 부모가 쥐는 공유 세마포어로 서비스 전체 합을 지킨다(자식 수만큼 곱해지지 않게) · spawn | `SRE_INVESTIGATION_PROCESS_ISOLATION`(신규 · 기본 off) |
| **A-4** LLM 호출 | R1 | 조사당 LLM 호출 수·프롬프트 길이 실측 → 줄일 곳(중복 서술·재요약) · 프로세스 전역 LLM 동시 상한(뒤단 한도에 맞춤 — 넘치면 대기가 아니라 순번) · 서빙 증설 요청 근거표 | 필요 시 신규 · 기본 off |

### 8.4 Wave (트랙 A)

| Wave | 범위 | 산출 | 회귀 |
|---|---|---|---|
| **A0** | 측정 | `investigation_bench`에 `--concurrency K`(서로 다른 호스트 K건 동시 접수) · 조사별 계측(총 소요 · LLM 호출 수·지연 · 도구 자리 대기 · 도구 실행 · 프로세스 CPU%·스레드 CPU 시간) · 가짜 LLM K=1/2/5 표 · 본체 쪽 영향(실시간 허브 폴러 ×5 · 이벤트 루프 최대 정지)도 함께 기록 · **(§9 C3)** `plans/205`·`206` 반영 전/후 두 조건 · 게이트웨이 `slot_wait_ms`·`throttle_wait_ms`와 MCP 서버 `조회 대기 초과` 함께 수집 · 내부망 실 LLM K=1/2/5 실행 절차(반출 형식) | 자기 테스트 + `regress.py --no-tests` |
| **A1** | A-2 (R2가 확인되면) | 조사별 공정 배분 또는 전역 상한 재산정 | 자기 테스트 + 정적 게이트 |
| **A2** | A-3 (R3가 확인되면) | 조사 실행 자식 프로세스화 · 진행·부분 브리핑·실행 기록 중계 · 타임아웃 시 프로세스 종료 | Wave 끝 모듈 단위 회귀(`sre_agent` 설정·디스패처 변경 → `[웨이브 회귀 필요]` 예상) |
| **A3** | A-4 (R1이 확인되면) | LLM 호출 절감 · 전역 LLM 동시 상한 · 증설 근거표 | 자기 테스트 + 정적 게이트 |
| **A4** | 상한 확정 | 측정이 목표를 충족하면 `investigation_max_concurrent` 코드 기본 5 · 전역 도구·시간당 예산 조정값 · D-347 부기 · 설정 설명(`sre_agent/.env.example` · `config/settings_help/`) | 자기 테스트 + 정적 게이트 |
| **A5** | 문서·매뉴얼 | 관리자 매뉴얼의 조사 부하 상한 설명 갱신(D-255) · `docs/` 운영 문서 | 트랙 A 끝 모듈 단위 회귀 1회(D-303) |

A1·A2·A3은 **A0 결과로 확인된 원인만** 착수한다(원인이 여럿이면 효과 큰 순). A0 결과는 이 절 아래 「측정 결과」로 붙이고 착수 Wave를 사용자에게 보고한다.

### 8.5 수용 기준 (트랙 A)

1. **목표**: 서로 다른 호스트 5건 동시 조사에서 각 조사 소요 ≤ 혼자 1건 소요 × **3**(합격) · × **2**(목표). 가짜 LLM 벤치와 내부망 실 LLM 양쪽에서 잰다(내부망은 잔여 이월 가능 · 그 경우 R1 판정 미완으로 적는다).
2. **응답성**: 5건 동시 중 조사 서비스 status 조회 p95 < 1s · 본체 이벤트 루프 최대 정지 < 100ms.
3. **보호 불변**: 조회 대상 보호(D-347 ③ — mcp_server 소스별 상한 · apm in-flight 8 · 제니퍼 5/s)는 바꾸지 않는다 · 같은 호스트 가드 동작 불변.
4. **비트 동일**: 신규 플래그 전부 기본 off에서 기존 `sre_agent`·본체 조사 테스트 통과.
5. **결과 동일**: 같은 시나리오의 조사 결과(판독·가설·근거 섹션)가 K=1과 K=5에서 같다(LLM 서술 문장 제외 · 가짜 LLM 기준).
6. **(§9 반영) 상한 곱셈 없음**: A-3(자식 프로세스) 켠 상태에서도 `sre_tool_global_concurrency`·LLM 동시 상한이 **조사 서비스 전체 합**으로 지켜진다(자식 수만큼 늘지 않음).
7. **(§9 반영) 게이트웨이 공정 분배와 공존**: `plans/205` W3b 기본 on 상태에서 조사 5건이 APM 호출자 키 1개로 묶이지 않는다(조사별 또는 요청 사용자별 `owner`).


### 8.6 A0 측정 결과 (2026-11-02 · 로컬 · 가짜 LLM)

**산출(코드)**: `investigation_bench --run --concurrency 1,2,5`(K 미지정 = 기존 경로 비트 동일 · 동시 모드는 품질 채점·`--resume` 없음 · `concurrency.jsonl`·`concurrency.md` · 반출 묶음 포함 · Windows 분기) · 스펙 `llm.fake_delay_s`·`world.tool_delay_s`(기본 0) · sre_agent 옵트인 계측 `SRE_INVESTIGATION_METRICS_ENABLED`(기본 off · 감사 `investigation_metrics` 이벤트 1건/조사 — LLM 호출 수·지연 · 축별 자리 대기 · 도구 실행 · 스레드·프로세스 CPU) · 본체 옵트인 루프 정지 감시 `OBS_LOOP_LAG_MONITOR_ENABLED`(기본 off · 임계 `OBS_LOOP_LAG_THRESHOLD_MS` 100 · 요약 `OBS_LOOP_LAG_REPORT_SECONDS` 60) · 프로브 `scripts/investigation_live_probe.py`.

**① 조사 서비스**(가짜 LLM 2.0s/회 · 세계 도구 0.4s · dev D01~D05 · `INVESTIGATION_MAX_CONCURRENT=5` 러너 주입):

| K | 소요 평균/최대(s) | 배율 평균/최대 | LLM 지연 합 평균/1회 최대(s) | 자리 대기 합(s) | 도구 실행 합(s) | 프로세스 CPU% | status p50/p95(ms) | 결과 일치 | 섞임 |
|---:|---|---|---|---|---:|---:|---|---|---:|
| 1 | 8.15/9.70 | 1.00/1.00 | 6.50/2.27 | 0 | 4.72 | 6.5 | 28/40 | 5/5 | 0 |
| 2 | 7.37/7.63 | 0.92/0.95 | 6.04/2.02 | 0.43(holmes_mcp) | 1.72 | 8.5 | 37/45 | 2/2 | 0 |
| 5 | 8.84/10.14 | 1.09/1.21 | 6.46/2.03 | 4.27(holmes_mcp) | 4.75 | 17.8 | 48/78 | 5/5 | 0 |

- **R3 아님**(K=5 CPU 17.8% · LLM 1회 지연 불변). **R2 확인 — 새 갈래**: HolmesGPT(0.36.0) `toolset_mcp.py` `_invoke`가 같은 MCP URL 호출을 **프로세스 전역 `get_server_lock` 하나로 직렬화**한다(호출마다 SSE 새 연결 · 응답까지 잠금 보유 · 실측 확인). 동시 조사 K건의 HolmesGPT 도구 호출이 모두 이 잠금 앞에 줄을 선다 — 사실상 「MCP 서버당 동시 1」 숨은 상한이며 `SRE_TOOL_GLOBAL_CONCURRENCY` 밖이다. 로컬은 도구 0.4s라 배율 1.21(목표 안)이지만, 폐쇄망은 실 조회 시간만큼 잠금을 쥐므로 가장 큰 변수 후보다. 205·206은 도구 1회를 빠르게 할 뿐 이 직렬화를 풀지 못한다.
- R1은 로컬에서 판정 불가(가짜 LLM) — §8.7 폐쇄망 측정.

**② 본체**(프로브 · 폴링 5초 · K마다 30초 · 사용자 구독자 3 + 운영자 1 · 각 구독자가 K건 모두 구독):

| 조건 | K=1 최대/p99(ms) | K=2 | K=5 |
|---|---|---|---|
| 진행 중 조사(이벤트 300건 누적)에 화면을 처음 붙임 | 378.9 / 4.5 | 757.8 / 3.7 | **1195.1** / 7.8 |
| 평상시(누적 0 시작 · 15초) | 3.5 / 2.8 | 21.9 / 9.5 | 55.7 / 15.6 |
| 누적 300건 · 운영자 구독자만(마스킹 없음) | 10.9 / 8.9 | 14.0 / 5.7 | 46.2 / 8.2 |

- 수용 기준 2(루프 정지 < 100ms)를 깨는 원인은 **사용자 경로 마스킹의 구독자별 반복**(이 계획의 R4로 둔다): `investigation_live_hub.py:752` `_watch_frames`의 `user_events`가 사용자 구독자마다 같은 이벤트를 동기로 다시 가린다(`src/api/routes/alarm.py:1165` `_investigation_mask_fn._mask` — 프로파일 `re.sub` 약 14.5만 회) → 정지 ∝ K × 사용자 구독자 수. 상세 GET(`activity_payload` `:382`)도 같은 경로. 발췌 300건 × 2KB는 상한 가정이라 실 크기는 더 작을 수 있다.
- **조사 화면 경로는 U1(판정 파일 전체 순회)을 타지 않는다**(`diagnosis-view.js`는 `/api/v1/alarm/investigations/*`·`/api/v1/noise/decisions`만). 다만 `/{id}?activity_since`·`/by-alarm`은 `_investigation_alarm_brief` → `DecisionStore.get_decision` → 꼬리 2만 건 증분 캐시를 **루프 위에서 선형 순회**한다(U1 아님 · 트랙 C에서 함께 본다).

**착수 판단**: A1(A-2 · R2 — HolmesGPT MCP 잠금 범위 · 조사별 몫 · §9 C1 APM owner)과 A1b(R4 — 본체 마스킹 공유)를 병렬 착수. A2(A-3 자식 프로세스)는 R3 미확인이라 보류 — 폐쇄망 측정에서 CPU ≥ 85%가 나오면 재개. A3(A-4 LLM)은 폐쇄망 R1 판정 뒤.

### 8.8 A1·A1b 결과 (2026-11-02 · 로컬)

**A1 — A-2 R2 교정 + §9 C1** (sre_agent · 신규 설정 3개 모두 기본 off · 셋 다 기본이고 계측 off면 훅 0):

| 설정 | 기본 | 동작 |
|---|---|---|
| `SRE_HOLMES_MCP_LOCK_SCOPE` | `server`(현행) | `investigation`이면 HolmesGPT MCP 잠금을 (URL, 조사)로 나눈다 — 조사 안 직렬은 현행 그대로 · 조사끼리 병렬. 그 호출은 배치 경로와 **같은** 축 전역 세마포어(`SRE_TOOL_GLOBAL_CONCURRENCY`)를 잡는다(상한 합 불변 · 수용 기준 6). 조사 밖 스레드·폴스타/APM 외 URL(stdio 포함)은 서버 잠금 |
| `SRE_TOOL_PER_INVESTIGATION_SHARE` | 비움(현행) | 조사 1건이 축별로 동시에 쥘 전역 자리 수(배치 + holmes 공통) |
| `SRE_APM_OWNER_PER_INVESTIGATION_ENABLED` | false | APM 데이터·작업 도구 호출에 `owner="investigation:<id>"`(LLM 값 덮어씀) — 205 W3b 공존(수용 기준 7) · 키 형식은 잠정(Q-X1 합의 남음) |

- HolmesGPT 0.36.0 동시 안전성: `_invoke_async`가 호출마다 새 SSE·`ClientSession`·`httpx.AsyncClient`를 `asyncio.run` 안에서 열고 닫는다(캐시 없음 · `toolset_mcp.py:255-318`·`:524-531`) — stdio(`_get_mcp_log_file` 재오픈)만 위험해 제외.
- 로컬 효과(가짜 LLM 2.0s · 세계 도구 1.5s · K=1,5): control K=5 배율 평균/최대 **1.64/2.08** · holmes_mcp 대기 합 36.5s → `investigation` K=5 **0.97/1.04** · 대기 0 · 결과 일치 5/5 · CPU 13.6% · status p95 80ms.

**A1b — R4 본체 사용자 마스킹 공유**(플래그 없음 · 출력 바이트 동일 테스트): 같은 이벤트는 엔트리마다 한 번만 가려 사용자 구독자·상세 GET이 공유(마스크 함수는 `security.mask_pattern`에만 의존 → 꼴별 같은 함수 객체 · 엔트리 캐시 300 상한 · sweep 동반) · 안 가린 이벤트 16건 초과 묶음은 `asyncio.to_thread`. 프로브 최대 정지 K=1 370→**14ms** · K=2 763→**19ms** · K=5 2016→**54ms**(재측정 42ms) — 수용 기준 2(< 100ms) 로컬 충족.

**보류·이월**: A2(A-3 자식 프로세스) — R3 미확인(K=5 CPU ≤ 18%) · 폐쇄망에서 CPU ≥ 85%면 재개. A3(A-4 LLM) — 폐쇄망 R1 판정 뒤. A4(상한 2→5 코드 기본 · D-347 부기) — §8.7 폐쇄망 측정이 목표 충족을 보인 뒤(권장 측정 조합: control vs `SRE_HOLMES_MCP_LOCK_SCOPE=investigation`). 

### 8.7 폐쇄망 측정 절차 (A0 · A4 판정 재료 · 실 LLM K=1/2/5)

> 실 LLM 목표 판정(수용 기준 1)과 A4(상한 2→5 코드 기본 · D-347 부기)는 이 측정 결과로 한다. 로컬 공유 MLX 서버에는 동시 5건을 걸지 않는다(Metal OOM 위험).

목적: 서로 다른 호스트 알람 5건을 동시에 조사할 때 조사 1건 소요가 혼자 돌 때의 몇 배인지 재고,
느려진 원인을 R1(LLM 뒤단) · R2(전역 도구 자리 대기) · R3(sre_agent 프로세스 CPU·GIL) 중에서 가린다.
합격: K=5 배율 최대 ≤ ×3 · 목표 ≤ ×2.

#### 0. 먼저 알아 둘 것 — 이 측정이 재는 것과 못 재는 것

- 벤치(`inv_bench.py`)는 **도구를 세계 서버(가짜 MCP)로 받는다** — 실험군의 `POLESTAR_MCP_URL`·
  `APM_MCP_URL`은 러너가 세계 서버로 고정한다. 그래서 벤치 run 동안 **실 mcp_server·apm_gateway에는
  호출이 가지 않는다.** 벤치가 재는 것은 실 LLM(R1) · sre_agent 프로세스(R3) · sre_agent 안의 자리
  대기(R2 중 `holmes_mcp`·`polestar`·`apm` 축)다.
- 실 도구 지연(DB 조회·제니퍼 API)은 세계 도구 지연 `world.tool_delay_s`로 흉내 낸다. 그 값은 아래
  §4의 운영 로그에서 실측한 도구 1회 지연(p50)을 쓴다.
- mcp_server 「조회 대기 초과」와 apm_gateway `slot_wait_ms`·`throttle_wait_ms`는 **실 서비스 쪽**
  R2다. 벤치 run에서는 나오지 않는 것이 정상이며, 운영(또는 스테이징) 시간대 로그에서 따로 모은다(§4).
- 로컬 측정에서 확인된 사실: HolmesGPT(holmesgpt 0.36.0)는 **같은 MCP URL로 가는 도구 호출을 프로세스
  전역 잠금 하나로 직렬화**한다(`holmes/plugins/toolsets/mcp/toolset_mcp.py` `get_server_lock` —
  호출마다 SSE 연결을 새로 열고 응답을 받을 때까지 잠금을 쥔다). 동시 조사 K건의 HolmesGPT 도구 호출은
  모두 이 잠금 앞에 줄을 선다. 계측은 이 대기를 축 `holmes_mcp` 자리 대기로 싣는다. 실 도구가 느릴수록
  (잠금을 오래 쥘수록) 커지므로 폐쇄망에서 가장 먼저 볼 지표다.

#### 1. 실행 명세 만들기

`sre_agent/testdata/investigation_bench/specs/closed_roundN.template.yaml`을 복사해
`closed_207a0.yaml`(이름 자유 · 저장소 밖에 둬도 된다)로 만든다. 바꿀 곳:

```yaml
run:
  site: closed
  splits: [dev]                       # 홀드아웃은 쓰지 않는다
  worlds: [D01, D02, D03, D04, D05]   # dev 세계는 호스트가 서로 다르다(러너가 다시 검사)
  worlds_dir: testdata/investigation_bench/worlds
  k: 1                                # 동시 모드는 k를 쓰지 않는다(세계당 혼자 1회 + K별 1그룹)
  order: interleave
  poll_interval_s: 2                  # 배율 오차 = 폴링 간격 — 2s면 혼자 소요 60s 기준 ±3%
  per_run_timeout_s: 1200             # ×3을 넘겨도 타임아웃으로 잘리지 않게(배율이 검열되면 판독 불가)
llm:
  backend: vllm                       # 또는 fabrix_proxy(closed_round1_fabrix.yaml의 llm 블록)
  model: "openai/<served>"
  api_base: "http://127.0.0.1:8000/v1"
  api_key: dummy
world:
  tool_delay_s: <§4에서 실측한 도구 1회 지연 p50(초) · 모르면 1.0>
host:
  gpu_sample_interval_s: 10           # R1 판정 보조 — GPU 사용률·메모리 표본
base_env:
  MAX_STEPS: 40
  INVESTIGATION_TIMEOUT_SECONDS: 900  # 조사 서비스 쪽 타임아웃도 ×3 여유
  # OVERRIDE_MAX_CONTENT_SIZE: <운영 실효값>
  # OVERRIDE_MAX_OUTPUT_TOKEN: <운영 실효값>
  # 운영에서 켠 조사 플래그(사전수집·플레이북 엔진 등)는 운영과 같은 값으로 — 꺼져 있으면
  # 자리 대기 polestar·apm 축은 0으로 나온다(배치 경로를 안 타므로).
arms:
  control: {}
```

- `INVESTIGATION_MAX_CONCURRENT`·`SRE_INVESTIGATION_METRICS_ENABLED`는 **명세에 쓰지 않는다.** 동시
  모드에서 러너가 max(K)·`true`로 넣는다(쓰면 보호 키라 거부된다).
- 가짜 LLM 지연 `llm.fake_delay_s`는 실 LLM에서는 쓰지 않는다.

#### 2. 명령 (sre_agent 폴더 · 내부망 venv)

```bash
cd sre_agent
# (선택) 하네스 확인 — 가짜 LLM · 과금 0 · 1~2분
.venv/bin/python scripts/inv_bench.py --run --test --spec <가짜 LLM 명세> --concurrency 1,2

# 환경·사전 점검 — 과금 평면 0 확인 · /v1/models · tool-calling 왕복 1회
.venv/bin/python scripts/inv_bench.py --show-env  --spec closed_207a0.yaml --site closed
.venv/bin/python scripts/inv_bench.py --preflight --spec closed_207a0.yaml --site closed

# 계획 확인(프로세스 0) → 본 실행(조사 12건: 혼자 5 + K=2 2 + K=5 5)
.venv/bin/python scripts/inv_bench.py --run --spec closed_207a0.yaml --site closed --concurrency 1,2,5 --dry-run
.venv/bin/python scripts/inv_bench.py --run --spec closed_207a0.yaml --site closed --concurrency 1,2,5

# 반출 묶음(가림 + 잔존 검사) — concurrency.jsonl·concurrency.md 포함
.venv/bin/python scripts/inv_bench.py --export <run_dir>
```

- 끊기면 `--cleanup <run_dir>` 뒤 처음부터 다시 돈다(동시 모드는 `--resume` 불가).
- 진행 중 GPU 서버를 다른 작업과 나눠 쓰지 않는다(R1 판독이 오염된다). 같은 시간대 다른 조사도 멈춘다.
- Windows 운영 PC에서도 같은 명령이다(`.venv\Scripts\python`). 러너는 Windows에서 프로세스 그룹 대신
  `CREATE_NEW_PROCESS_GROUP`·`taskkill /T /F`·`tasklist`를 쓴다.

#### 3. 두 조건 — plans/205·206 반영 전 / 후

| 조건 | 언제 | 벤치 `world.tool_delay_s` | 실 서비스 로그(§4) |
|---|---|---|---|
| **전** | 205(APM 게이트웨이 조회 가속)·206(DB2 연결 풀 기본 on) 배포 전 | 배포 전 운영 로그의 도구 1회 지연 p50 | 배포 전 1~2일치 |
| **후** | 두 계획 배포 뒤 | 배포 뒤 운영 로그의 도구 1회 지연 p50 | 배포 뒤 1~2일치 |

- 같은 명세 · 같은 LLM 백엔드 · 같은 세계로 두 번 돈다(바꾸는 값은 `tool_delay_s` 하나).
- 205·206은 도구 1회를 빠르게 할 뿐 HolmesGPT 전역 잠금(직렬화)은 그대로다 — 「후」에서 `holmes_mcp`
  대기가 줄어드는 폭이 도구 지연 단축 폭과 비슷하면 잠금이 병목이라는 뜻이다.

#### 4. 실 서비스 쪽 R2 수집 (벤치와 별개 · 같은 조건 기간의 운영 로그)

| 출처 | 무엇 | 어떻게 |
|---|---|---|
| mcp_server 로그 | 「조회 대기 초과 (<소스>): 동시 상한 N · 대기 Xs」 (`mcp_server/mcp_server/db.py` `_query_slot`) | 건수 · 소스별 · 시각 — `grep -c "조회 대기 초과"` + 소스별 집계 |
| mcp_server 로그 | 도구 1회 지연 | 조회 실행 시간 로그(있는 만큼)로 p50·p95 → 벤치 `tool_delay_s` |
| apm_gateway 로그·응답 | `slot_wait_ms` · `throttle_wait_ms` | **205 구현 뒤 생긴다** — 있으면 p50·p95·최대, 없으면 「미구현」으로 적는다 |
| sre_agent 감사 JSONL | `investigation_metrics` 이벤트 | 운영 sre_agent에 `SRE_INVESTIGATION_METRICS_ENABLED=true`를 켠 기간만 생긴다(기본 off) — 켤 수 있으면 실 동시 조사의 축별 자리 대기·LLM 지연을 바로 본다 |

반출 전 로그는 호스트명·IP·계정을 가린다(`--export` 대상 밖 파일이므로 수동 · 건수·통계만 반출해도 충분).

#### 5. 반출 파일

- `<run_dir>.bundle/` 전체(가림 완료) — 핵심은 `concurrency.md`(요약표) · `concurrency.jsonl`(조사별:
  소요·배율·계측·결과 서명·섞임) · `manifest.json`(조건·지연값·K) · `arm-control/settings_echo.json`
  (`investigation_max_concurrent`=5 · `sre_investigation_metrics_enabled`=true 확인) · `gpu_samples.jsonl`
- §4 집계(전/후 각각): 「조회 대기 초과」 건수·소스별 · 도구 지연 p50/p95 · apm `slot_wait_ms`·
  `throttle_wait_ms` 통계(있으면)
- 조건 표기: 205·206 배포 여부 · LLM 백엔드·모델 · GPU 공유 여부 · `tool_delay_s` 값

#### 6. 판정 규칙

먼저 `concurrency.md` 표의 K=5 행을 본다.

1. **배율** — 배율 최대 ≤ ×2 목표 · ≤ ×3 합격 · 초과 불합격. 무효(infra_error)·타임아웃이 있으면 판정
   보류(타임아웃 연장 후 재측정).
2. **R3(sre_agent 프로세스)** — 프로세스 CPU% ≥ 85(1코어 포화 = GIL 단일 프로세스 상한)면 R3.
   보조: status p95(ms)가 K와 함께 수백 ms 이상으로 뛰면 프로세스 안 경합.
3. **R2(전역 자리 대기)** — 늘어난 소요(혼자 대비) 중 자리 대기 증가가 ≥ 30%면 R2. 축으로 갈래를 가른다:
   - `holmes_mcp` → HolmesGPT MCP URL 전역 잠금(sre_agent 안 · 205/206으로는 안 풀림 — 잠금 우회·연결
     재사용이 대책 후보)
   - `polestar`·`apm` → sre_agent 전역 도구 동시 상한(`SRE_TOOL_GLOBAL_CONCURRENCY` 등)
   - §4의 「조회 대기 초과」 > 0 또는 apm `slot_wait_ms` p95 상승 → 실 서비스 쪽 상한(206 · 205 대상)
4. **R1(LLM 뒤단)** — LLM 지연 합 평균 · 1회 최대가 K와 함께 늘고(증가분의 ≥ 50%) CPU가 낮으면 R1.
   `gpu_samples.jsonl`의 GPU 사용률 포화·vLLM 대기열(서빙 쪽 지표가 있으면)로 확인한다.
5. **결과 동일** — 결과 일치 n/n이 아니어도 실 LLM은 도구 선택이 흔들릴 수 있다. **섞임 = 0**이면
   동시성 오염(다른 조사 호스트가 브리핑에 섞임)은 없다. 섞임 > 0이면 배율과 무관하게 결함으로 보고.

구간(LLM·도구·자리 대기)은 서로 겹치고 조사 안에서 병렬로도 돌므로 비중은 단서다 — 두 원인이 함께
걸리면 표의 판정 힌트에 둘 다 나온다.

---

## 9. origin/main `plans/205`·`plans/206`과의 관계 (2026-11-02 점검)

> **점검 대상**: origin/main `afc5626c` — `plans/205-TODO-apm-gateway-parallel-calls-multicore-parse.md`(D-356 · APM 게이트웨이 도구 안 동시 호출 · 큰 응답 파싱 프로세스 풀 · W3b 다중 사용자 공정 분배 · 기본 on) · `plans/206-TODO-mcp-server-concurrency-multicore-review.md`(D-357 · MCP 서버 다중 워커 미채택 · DB2 연결 풀 기본 on · 소스별 프로세스 분리는 측정 뒤). 셋 다 같은 기준 SHA `16ea44fa`.

### 9.1 판정 요약

| 구분 | 결과 |
|---|---|
| 번호 | **충돌** — 이 계획을 205 → **207**로 재번호(뒤에 만든 쪽 · 미커밋). 결정 번호는 D-356·D-357이 선점 → 이 계획 착수 시 D-358 이후 |
| 범위 중복 | **없음** — 205는 `apm_gateway/` 내부, 206은 `mcp_server/` 내부, 이 계획은 `sre_agent/` 조사 실행(트랙 A)·본체 관제 집계(트랙 C)·`noise_gate` 알람 워커(트랙 B) |
| 결정 충돌 | **없음** — 세 계획 모두 D-347 ③(조회 대상 보호 상한) 불변 · uvicorn 다중 워커 미채택(D-356 ④ · D-357 ① · 이 계획 M-6 범위 밖)으로 같은 방향. 이 계획이 바꾸는 것은 D-347 ②의 동시 실행 조사 수(2→5)뿐 |
| 맞물림 | **4건** — 아래 C1~C4. C1·C2는 이 계획 목표(동시 5건)를 직접 깎을 수 있어 트랙 A에 반영했다 |

### 9.2 맞물리는 지점

| # | 내용 | 영향 | 조치 (이 계획) |
|---|---|---|---|
| **C1** | `plans/205` W3b는 게이트웨이 자리·작업 슬롯을 **호출자 키**(주체 + `owner`)별로 공정 분배한다(기본 on · 호출자별 동시 상한 `JENNIFER_MAX_INFLIGHT_PER_CALLER` 4 · owner별 실행 작업 `APM_JOB_MAX_CONCURRENT_PER_OWNER` 2). **owner가 없는 호출(sre_agent 조사)은 주체 단위 키 1개**라고 명시돼 있다. 현행 `sre_agent`는 owner를 싣지 않는다(`mcp_tool_client.py`에 owner·caller 없음 · 실측) | 다른 사용자의 APM 질의와 겹치면 **조사 5건 전체가 APM 동시 호출 4 · 동시 작업 2를 나눠 쓴다**(일 보존이라 혼자일 때는 무관) → 동시 5건 목표의 R2(도구 자리)를 키운다 | A-2에 「조사별 APM `owner`(`investigation:<id>` 또는 요청 사용자 `user:<sub>`) 전달」을 더한다. 키 형식은 205 W3b 구현과 맞춰 정한다 — **205 쪽 담당과 합의 필요**(Q-X1) |
| **C2** | `sre_agent`의 전역 도구 상한은 **프로세스 안** `threading.BoundedSemaphore`(`mcp_tool_client.py:46-75` `_GLOBAL_LIMITS`)다. 206 ①·205 ④가 다중 워커를 기각한 근거가 「상한이 프로세스 수만큼 곱해진다」였다 | A-3(조사 실행 자식 프로세스)을 그대로 하면 전역 도구 8 · 도구 결과 캐시 · LLM 동시 상한이 자식 수만큼 늘어난다 — 같은 기각 사유에 걸린다 | A-3 설계 조건으로 못 박는다: 상한은 **부모가 쥐는 공유 세마포어**(`multiprocessing` 관리 객체 또는 부모 중계 호출)로 서비스 전체 합을 지킨다 · 자식은 spawn(205 W1과 같은 이유 — 실행 중 스레드가 있는 프로세스의 fork 회피) · `__main__` 가드 실측. 수용 기준 6 |
| **C3** | 205 W2는 도구 1건 안의 독립 호출을 동시 4로 보낸다(기본 on). 205 R-1: 작업 4 × 동시 4 = 16 > 자리 8 → 자리 대기 초과(`SOURCE_UNAVAILABLE`)가 새로 날 수 있음. 206 W1은 DB2 연결 풀(기본 on)로 질의당 접속 비용을 없앤다 | 조사 동시 5건 × 플레이북 작업자 4는 205 R-1의 전제(작업 4)보다 큰 부하다 — 205·206이 들어온 뒤와 전의 조사 소요가 다르다 | A0 측정을 **205·206 기본값 반영 상태와 미반영 상태 둘 다** 기록한다(게이트웨이·MCP 서버 쪽 대기 지표 `slot_wait_ms`·`throttle_wait_ms`·`조회 대기 초과` 함께 수집). 205 R-1 검증 조합에 「조사 5건 동시」를 추가해 달라고 205 쪽에 전달(Q-X2) |
| **C4** | 같은 파일: 206 W2가 `sre_agent/sre_agent/settings.py`·`infrastructure/mcp_tool_client.py`(소스별 MCP URL 맵)를 바꾼다. 이 계획 A1·A2도 같은 두 파일을 바꾼다 | 병행 구현 시 충돌 | 착수 전 `git log origin/main -- <두 파일>`로 206 W2 진행 확인 · 먼저 들어간 쪽 위에서 작업 · 206 W2는 측정 판정 뒤라 시점이 늦을 가능성이 크다 |

### 9.3 205·206 쪽에 전달할 것

| # | 내용 |
|---|---|
| **Q-X1** | 205 W3b 호출자 키에서 sre_agent 조사를 조사별(또는 요청 사용자별) owner로 구분할지 — 이 계획 A-2가 sre_agent 쪽 전달을 맡는다 |
| **Q-X2** | 205 R-1 검증(자리 대기 초과 0건)에 「조사 5건 동시 × 플레이북 작업자 4」 조합 추가 |
