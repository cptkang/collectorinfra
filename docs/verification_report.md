# 검증 보고서

## D-049 — ack/incident 라이프사이클 계측 (백엔드)

- 검증일: 2026-06-30
- 검증자: verifier (독립 correctness 심층 리뷰)
- SSOT: `docs/02_decision.md` `## D-049`
- 결론: **검증 통과 (Critical/Major 이슈 없음)**

### 1. 테스트 결과 요약

| 항목 | 값 |
|------|----|
| `pytest tests/test_alarm/` | **336 passed** (팀리드 baseline 333 + verifier 신규 3) |
| 신규 추가 테스트 | 3 (`tests/test_alarm/test_incident_fingerprint_symmetry.py`) |
| `scripts/arch_check.py` | exit 0 · **error 0** · warning 3 |
| 회귀 | **0** (광역 스위트 pristine vs working 동일 — 팀리드 git stash 대조로 증명; 본 검증의 신규 3건도 전부 통과) |

광역 스위트의 631 failed/23 errors는 라이브 폴스타DB/Redis/LLM 부재 환경 baseline으로 D-049와 무관(팀리드 stash 대조에서 pristine·working 완전 동일).

### 2. 아키텍처 정합성 (arch-check)

- `python scripts/arch_check.py`: 검사 파일 119, 총 import 367, 허용 364, **위반(error) 0**, 경고(warning) 3.
- warning 3건은 모두 `src/orchestration/{deep_agent,intent_planner,replanner}.py` → `prompts` 직접참조(기존, D-049 무관). D-049 변경 파일에는 위반·경고 0.
- **arch-check error 0 → Critical 분류 사유 없음.**

| 계층 | D-049 파일 | 외부 IO import |
|------|-----------|---------------|
| domain | `incident_store.py` | asyncpg/redis **0** (ABC + stdlib 타입만) — 직접 grep 확인 |
| application | `nodes/alarm_notifier.py` | redis/asyncpg **0** (주입 publisher 덕타이핑) |
| application | `alarm_worker.py` | asyncpg **0** (redis.asyncio는 기존 스트림소비용; incident publisher는 infra에서 lazy import) |
| infrastructure | `incident_repository.py`, `incident_events.py` | asyncpg/redis 여기에만 집중 |
| entry | `api/server.py` lifespan | PG 풀·Redis 클라이언트 생성·정리 |

### 3. correctness 심층 리뷰 — 7개 항목

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 1 | 이벤트 계약 대칭성 / fingerprint 매칭 | **PASS** | 아래 상세 |
| 2 | aggregate의 resolution 레코드 제외 | **PASS** | 아래 상세 |
| 3 | 옵트인 게이팅(회귀 0) | **PASS** | 아래 상세 |
| 4 | graceful degradation | **PASS** | 아래 상세 |
| 5 | 계층 경계 | **PASS** | §2 매트릭스 |
| 6 | metrics 산출 정합 | **PASS** | 아래 상세 |
| 7 | resolve 경합 / ack 멱등 | **PASS** | 아래 상세 |

#### 항목 1 — 이벤트 계약 대칭성 (최상위 위험: 불일치 시 incident_mttr 환각 null)

- **publisher↔subscriber 키 일치**: notifier open payload(`_incident_open_payload`, `alarm_notifier.py:305`)={type,fingerprint,alarm_id,db_id,server_name,severity,priority,tier,ts} ↔ `_handle_open`(`incident_events.py:71`)이 동일 키 소비. worker resolved payload(`alarm_worker.py:478`)={type,fingerprint,alarm_id,db_id,server_name,severity,tier,ts,resolution} ↔ `_handle_resolved`(`incident_events.py:91`)이 fingerprint/ts/resolution 소비. 누락 키 없음.
- **fingerprint 동일 산출식**: open은 `decision.fingerprint`, 그 값은 `notification_policy.py:202`의 `_decision()`이 **유일 생성경로**로 `compute_fingerprint(event)`를 채움. resolved는 worker `_process`(`alarm_worker.py:343`)의 `fingerprint = compute_fingerprint(event)`. **양측 동일 함수.** `compute_fingerprint`(`notification_policy.py:51`)는 db_id/server|hostname/alarm_name/resource_name만 사용 — severity·is_clear·alarm_time 미포함 → firing↔clear 전이에 **불변**.
- **신규 검증(공백 보완)**: 기존 D-049 테스트는 fingerprint를 하드코딩("fp"/"fp-x")해 실제 일치를 증명하지 못함. verifier가 추가:
  - `test_compute_fingerprint_stable_across_firing_and_clear` — firing(sev2)·clear(sev0) 동일 fingerprint.
  - `test_notifier_open_and_worker_resolved_payloads_share_fingerprint` — notifier open payload.fingerprint == worker resolved payload.fingerprint (실 `compute_fingerprint` 사용). resolve 매칭 성립 입증.

#### 항목 2 — aggregate의 resolution 제외 (회귀 위험 최상)

- `decision_store.py:168` — `rec.get("type")=="resolution"`이면 by_tier/total/latest_dt 갱신 **이전**(`:173~179`)에 `continue`. resolution 레코드는 by_tier/total/page_count/suppress_count/actionable/last_event_ts에서 **완전 제외**, `auto_recovery_mttr_seconds`(`:206`) 평균에만 사용.
- 키 충돌 없음: 일반 결정 레코드(`record()`, `:58`)는 `type` 키 미보유 → `.get("type")` None, 절대 오인 안 됨.
- 윈도우 필터(`:164`)가 resolution보다 먼저 적용 → resolution도 ts로 윈도우 집계됨(정합).
- **신규 검증**: `test_resolution_records_do_not_pollute_suppress_or_actionable_ratio` — 결정 4건(PAGE/TICKET/SUPPRESS×2) + resolution 3건 혼재에서 total=4, suppress_ratio=0.5, actionable_ratio=0.5, page_count=1, auto_recovery_mttr=60.0, last_event_ts≠null. **파생 비율지표 비오염 입증**(기존 `test_aggregate_tier_counts_unchanged_with_resolution_lines`가 안 다룬 suppress/actionable 비율 보완).

#### 항목 3 — 옵트인 게이팅 (기본 off → 회귀 0)

- lifespan(`server.py`): `app.state.incident_store/publisher`를 **if 이전 None 선설정** 후 `enable_noise_gate AND incident_tracking_enabled`일 때만 풀/스토어/subscriber 기동.
- notifier `_publish_incident_open`(`alarm_notifier.py`): `incident_publisher is None`이면 즉시 return(workb 경로 불변). `test_page_decision_without_publisher_skips_open_keeps_workb` 확인.
- worker `_build_incident_publisher`: gate off / tracking off / redis None → None(`test_build_incident_publisher_none_*` 3건). `_publish_incident_resolved`: publisher None이면 return.
- `/alarm/metrics`(`alarm.py:929`): store None이면 mtta/incident_mttr/conversion=null + open_incident_count=0 + `unavailable_metrics.reason` 유지. `test_metrics_store_none_keeps_null_and_reason` 확인.

#### 항목 4 — graceful degradation

- lifespan incident 블록 전체 `try/except Exception`(`server.py`) → 실패 시 store/publisher None + warning, **서버 기동 무차단**. 종료 정리(task cancel·redis aclose·pool close)도 각각 try/except.
- publisher(`RedisIncidentPublisher.publish`)·subscriber(`run_incident_event_subscriber` per-message except)·repository(전 메서드 try/except → 안전값) 모든 외부 IO 예외 삼킴.
- subscriber 구조는 **D-048.10 `sse_bridge.run_sse_bridge_subscriber`와 동형**(subscribe→while-try→cancel/stop→finally unsubscribe/close). 팀리드 승인 패턴과 일관.

#### 항목 6 — metrics 산출 정합

- conversion: `(incident_count / page_count) if page_count else None`(`incident_repository.py:222`) — 0분모 None, 0division 없음.
- mtta/incident_mttr: SQL `FILTER (WHERE acked_at/resolved_at IS NOT NULL)` → ack/resolve 없으면 AVG NULL → None(`:226~229`).
- 신규 응답필드 `incident_mttr_seconds`/`auto_recovery_mttr_seconds`/`open_incident_count` 추가, `mttr_seconds`는 `incident_mttr_seconds`와 **동일 값 채움**(하위호환, `alarm.py`). store None 시 둘 다 None — 일관.
- **윈도우 정합**: `agg=aggregate(window)` 와 `metrics(window_seconds=window, page_count=agg["page_count"])`가 **동일 `window=ng.meta_alert_window_seconds`** → conversion 분자/분모 윈도우 정렬(`alarm.py:916,920,939`).

#### 항목 7 — resolve 경합 / ack 멱등

- `resolve_by_fingerprint`: `SELECT id ... status='open' ORDER BY created_at DESC LIMIT 1` 후 `UPDATE ... WHERE id AND status='open'` — 가장 최근 open 1건만 해소(D-049 향후고려사항 기본정책 정합). 매칭 없으면 0. `test_resolve_updates_most_recent_open`/`test_resolve_returns_zero_when_no_open_match`.
- `ack`: `UPDATE ... WHERE id AND status='open'` — open만 영향, 이미 ack/resolved면 affected=0 → False. `test_ack_true_when_open_affected`/`test_ack_false_when_already_handled`.

### 4. 발견 이슈 목록

- **Critical**: 없음.
- **Major**: 없음.
- **Minor(설계 관찰, 결함 아님)**:
  - `auto_recovery_mttr_seconds`(JSONL self-heal)는 `incident_tracking_enabled`와 **독립**으로 산출됨 — 노이즈게이트+decision_store만 활성이면 incident 트래커 off라도 resolution 레코드가 적재되고 metrics에 노출된다. 이는 D-049의 "self-heal 소요시간을 JSONL로 저위험·선행 산출"(라벨 분리) 의도와 **일치**하며, 기존 운영지표(억제율 등)는 항목 2로 비오염 입증됨. 외부 JSONL 파서가 있다면 `type="resolution"` 레코드 신규 등장 인지 필요(내부 `aggregate`는 처리함).
  - fingerprint 매칭은 폴스타 clear 이벤트가 firing과 동일한 alarm_name/resource_name을 보낸다는 §6.1 dedup의 **기존 도메인 가정**에 의존(D-049 신규 위험 아님).

### 권고

- 변경 승인 가능. 팀리드 커밋 진행 권장.
- (후속, 비차단) ITSM/외부 incident id, ack 권한(`acked_by` 인증연계)은 D-049 향후고려사항대로 차기 확장.
