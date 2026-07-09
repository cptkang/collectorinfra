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

---

## D-049 — ack/incident UI 2개 surface + 백엔드 delta (미커밋 working-tree)

- 검증일: 2026-06-30
- 검증자: verifier (독립 correctness 심층 리뷰 — 코드 직접 단언)
- SSOT: `docs/02_decision.md` `## D-049` + 위 백엔드 섹션
- 결론: **UI 검증 통과 (Critical/Major 이슈 없음)** — 7항목 전부 PASS
- 라이브 브라우저 E2E: **미수행(정직 명시)** — 실 PostgreSQL/Redis/LLM 인프라 부재 환경. 대신 SSE→렌더 경로, API 계약, 이스케이프, 위치 인자 매핑을 정적·단위로 단언.

### 검증 환경 결과

- `node --check src/static/js/app.js` → OK. `node --check src/static/js/admin.js` → OK.
- `python scripts/arch_check.py --ci` → **exit 0, error 0**. WARN 3건은 모두 `src/orchestration/{intent_planner,replanner}.py`→`prompts` 직접참조(기존, D-049 무관). D-049 변경 파일에는 위반·경고 0.
- `python -m pytest tests/test_alarm/ -q` → **337 passed**(baseline 336 + verifier 신규 INSERT $오프셋 회귀 가드 1).

### 7항목 PASS/FAIL + 근거

**1. 백엔드 delta 1 (payload 보강) — PASS**
- `alarm_notifier.py::_incident_open_payload`(L305–) 가 `_tier_sse_payload`(L195–224)의 전체 표시필드(severity·severity_label·alarm_name·db_id·server_name·hostname·ip_address·resource_type·resource_name·alarm_status·summary·probable_cause·recommended_action·pattern_type·is_routine·pattern_analysis)+식별필드(type·fingerprint·priority·ts·tier)를 모두 포함. 유일 차이는 `tier_reason`(카드 미사용) 제외 — 빈 칸 유발 없음.
- `incident_events.py::_handle_open`(L71–89): `create_open(..., alarm_name=str(message.get("alarm_name","")), ...)` 전달 + 재발행 payload=`{**message, type:"alarm_notification", tier:"page", incident_id:iid}` — 보강필드+incident_id를 carry.
- `app.js::connectAlarmStream`(L2133)이 `type==="alarm_notification"`→`renderAlarmMessage`로 디스패치, 렌더 필드 전부 payload에 존재. process_snapshot/history_stats 제외분은 `renderProcessEvidence`(L1960 `if(!ps...)return""`)·`renderHistoryEvidence`(L1883 `if(!hs)return""`)가 graceful 생략 — 카드 깨짐 없음.

**2. 백엔드 delta 2 (alarm_name 컬럼) — PASS**
- DDL: `ddl/alarm_incidents.sql` + `src/api/server.py::_INCIDENT_DDL` 양쪽에 `alarm_name VARCHAR(255)` 추가.
- 멱등 ALTER: `server.py::_ensure_incident_tables`(L96–) `ALTER TABLE alarm_incidents ADD COLUMN IF NOT EXISTS alarm_name VARCHAR(255)` — 기존 테이블 커버, auth 패턴 일관·graceful try/except.
- port 전파: `incident_store.py::create_open` 시그니처 `alarm_name: str = ""` 추가 + docstring.
- `incident_repository.py`: `_row_to_dict`(L35) `alarm_name` 매핑, `create_open` INSERT 컬럼/값, `list_open` SELECT 컬럼 일관.
- **INSERT $오프셋 정확 — PASS(핵심)**: 컬럼 `(fingerprint,alarm_id,alarm_name,db_id,server_name,severity,priority,tier,status,created_at)` ↔ `VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'open',$9)` ↔ 위치 인자 `(fingerprint,alarm_id,alarm_name,db_id,server_name,severity,priority,tier,created_at)`. alarm_name=3번째 컬럼=$3, 이후 db_id$4·…·tier$8, status는 리터럴, created_at=$9. 1:1 정합, 밀림 버그 없음. verifier가 회귀 가드 `test_create_open_binds_args_in_exact_column_order` 추가(센티넬 args 튜플 순서 + `($1..$8,'open',$9)` 시퀀스 + `$10` 부재 단언).

**3. delta 테스트 약화 여부 — PASS (약화 없음, 강화만)**
- `test_incident_events.py`: 이벤트에 `alarm_name:"CPU 임계"` 추가 후 `create_calls[0]["alarm_name"]=="CPU 임계"`(전파) + `refanout["alarm_name"]=="CPU 임계"`(재발행 carry) **신규 단언**. 기존 단언(fingerprint·severity·incident_id·unsubscribe) 보존.
- `test_incident_repository.py`: `create_open`에 alarm_name 추가 + `"alarm_name" in SQL`·`"CPU 임계" in args`; `list_open` row에 alarm_name + `rows[0]["alarm_name"]` 매핑 단언. 기존 단언 보존.
- `test_incident_worker_publish.py`: `payload["alarm_name"·severity_label·summary·hostname·recommended_action]` **신규 단언**(보강필드 적재). 기존 alarm_id·tier·priority·channels 보존.
- 멤버십(`value in args`) 단언이 위치 오류를 못 잡는 공백 1건 발견 → verifier가 항목 2의 위치-정합 가드로 보완(테스트 +1).

**4. 채팅 ack 버튼 (app.js) — PASS**
- `renderAlarmMessage`(L2037–2045): `if(data.incident_id)`일 때만 ackHtml 삽입 — 비-incident 알람 불변.
- `bindIncidentAck`(L2096–): 클릭→`POST /api/v1/alarm/incidents/{encodeURIComponent(id)}/ack`, `headers:getAuthHeaders()`(L32 Bearer 토큰). acked=true→`textContent="확인됨 · HH:MM:SS"`+disabled; acked=false→"이미 확인됨"; `.catch`→`btn.disabled=false`+"확인 실패 · 다시 시도"(카드 유지·재시도 가능).
- closure 캡처: `bindIncidentAck(ackBtn, ackMsg, data.incident_id)`(L2085) — **인라인 onclick 없음**. 시각 표시는 `textContent`(innerHTML 아님) — XSS 안전.

**5. admin 패널 (admin.js/dashboard.html) — PASS**
- `loadIncidents`(L709–): `apiRequest("GET","/api/v1/alarm/incidents?status=open&limit=100")`(L95 Authorization Bearer 자동). `renderIncidents`(L724–): 발생시각·경과(`formatElapsed`)·서버(db_id)·알람명·심각도(`sevColor`/`INCIDENT_SEVERITY_LABELS`)·tier·확인 버튼.
- 행별 ack: `.incident-ack-btn`→`ackIncident(dataset.iid)`→`apiRequest("POST",".../ack")`→`loadIncidents()` 재조회. acked=true→`showSuccess`, false→"이미 확인/해소된 사건입니다.".
- 빈 목록/트래커 off(빈 배열): `incidents.length===0`→`incidentsEmpty.style.display="block"`("열린 사건이 없습니다." dashboard.html L295).
- 탭 컨벤션: `dashboard.html` `data-tab="incidents"`→`#tab-incidents`, ID 정합(incidentsBody/Table/Loading/Empty/refreshIncidentsBtn). 제네릭 탭 스위처와 추가 리스너(loadIncidents) 양립.

**6. 플래그 off 회귀 0 — PASS**
- `GET /alarm/incidents`: `incident_store is None`→`IncidentListResponse(incidents=[])`(routes/alarm.py L1023–1027)→admin "열린 사건이 없습니다.".
- open 미발행: `alarm_notifier_node`(L157–161) `decision.tier==TIER_PAGE` + `incident_publisher` 주입 시에만 `_publish_incident_open`; 트래커 off→미주입→`_publish_incident_open`(L353 `if incident_publisher is None: return`)→subscriber 미기동→재발행 없음→`data.incident_id` 없음→**ack 버튼 미표시**.
- ALTER/payload 보강은 트래커 on lifespan 경로에서만 작동 → 기존 알람카드/admin 동작 불변.

**7. escape/안전 (XSS) — PASS**
- app.js `renderAlarmMessage`: severity_label·resource_name·hostname·alarm_name·summary·probable_cause·recommended_action·pattern_analysis·badgeText 전부 `escapeHtml`(L1200 textContent 기반, 따옴표 포함 인코딩) 통과. ack 영역은 정적 문자열만 — 서버 값 innerHTML 직삽입 0.
- admin.js `renderIncidents`: time·formatElapsed·server_name·db_id·alarm_name·sevLabel·tier·String(id) 전부 `escapeHtml`(L486, `& < > "` 인코딩). sevColor는 내부 상수(서버 무관) — 주입 불가.

### 발견 이슈

- **Critical**: 없음.
- **Major**: 없음.
- **Minor**:
  - (보완 완료) delta 단위 테스트의 `value in args` 멤버십 단언이 INSERT 위치 오류를 못 잡는 공백 → verifier가 위치-정합 회귀 가드 추가(`test_create_open_binds_args_in_exact_column_order`). 현 INSERT 매핑은 정확.
  - (관찰, 결함 아님) admin.js `escapeHtml`는 `'`(single quote)를 인코딩하지 않으나, 단일 속성 삽입 지점은 `data-iid='{escapeHtml(String(inc.id))}'`(DB BIGSERIAL 정수)뿐이라 실질 주입 불가. 나머지 escapeHtml 호출은 모두 텍스트 노드 컨텍스트.
  - (관찰, D-049 무관) `list_incidents`는 `status` 쿼리 파라미터 값을 사용하지 않고 항상 `list_open` 호출 — admin.js가 `status=open`만 보내므로 현 동작 정상(기존 라우트, 본 delta 무관).

### 권고

- UI 검증 통과. 팀리드 커밋 진행 가능(verifier는 커밋하지 않음).

---

## Plan 52 E4 — LLM 액션가능성 판단(피드백 few-shot) 검증 (2026-07-01, D-048.11)

구현: implementer-e4 서브에이전트 / 검증: 팀리드 **독립 재실행 + 전 diff 리뷰**(보고 신뢰 아님).

### 품질 게이트 (팀리드 독립 재실행 결과)

| 게이트 | 결과 |
|--------|------|
| `pytest tests/test_alarm/ -q` | **359 passed** (E3/D-049 baseline 337 + 신규 22, 실패 0) |
| `scripts/arch_check.py --ci` | **exit 0** (WARN은 기존 orchestration→prompts replanner 건, E4 무관) |
| `python -c "from src.config import AppConfig; AppConfig()"` | **OK** |
| `node --check src/static/js/app.js` | **OK** |

### 안전 불변식 검증 (diff 리뷰 + 테스트 고정)

1. **심각도3 절대 PAGE — PASS**: E4는 step9만 관여, sev3는 step3에서 단락. `test_severity3_always_page_regardless`(noise여도 PAGE) 고정. `llm_actionability` 추출을 첫 `_decision` 이전으로 hoist하여 sev3 조기반환 경로 `_signals()` 클로저 NameError도 차단.
2. **승격 비대칭·재현율 우선 — PASS**: `actionable`→promote(항상), `noise`→demote(단 `effective_severity≤suppress_max_severity` 가드). 승격우선 기계가 promote 공존 시 noise 무시(`test_noise_with_promote_signal_promotion_wins`). 1단계 이내·SUPPRESS 하한 불변(`test_noise_demotes_one_step`/`test_noise_respects_suppress_floor`).
3. **게이트오프 회귀 0 — PASS**: `enable_llm_actionability=False`면 policy는 값 미독·None(`test_disabled_ignores_actionability`, E3와 동일 티어), analyzer는 few-shot 미조회·재파싱 스킵(`test_disabled_skips_fewshot_and_reparse`).
4. **추가 LLM 호출 없음 — PASS**: analyzer가 기존 단일 응답 재파싱(D-048.5 패턴). `_CapturingLLM` 단일 `ainvoke`로 확인.
5. **계층 경계 — PASS**: `feedback_store.py`=infrastructure·stdlib만(domain/외부 import 0), `notification_policy.py`=stdlib 유지. arch_check exit 0.
6. **캡처 안전 — PASS**: `POST /alarm/feedback` require_user + 게이트/액션가능성 off면 503 + label 검증 400. app.js 피드백 버튼 closure 바인딩(인라인 onclick 0)·`textContent` XSS 안전·503 처리·graceful 재시도.

### signals 스키마 가드

`signals["llm_actionability"]` 키 추가에 따라 set-equality 단언 **2곳 전수 갱신**(`test_notification_policy.py::TestSignalsSchema.REQUIRED_KEYS`·`test_polestar_noise_context_integration.py` expected) — D-048.8 "매트릭스/상수 변경 시 단언 전수 grep" 교훈 준수.

### 발견 이슈

- **Critical/Major**: 없음.
- **Minor(관찰)**: `test_noise_respects_suppress_floor`가 `tier in (DASHBOARD, "suppress")`로 다소 느슨하나(실제=SUPPRESS), 1단계 하한 규칙을 문서화하는 의도로 결함 아님.

### 권고

- E4 검증 통과. E5(deepagents Advisory Enricher, D-048.7)는 vLLM 가용성 확인 후 별도 착수(§13.1 #8).
