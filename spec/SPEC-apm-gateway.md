# SPEC: apm-gateway — 제니퍼 Open API 게이트웨이 (`plans/87` J1·J2·J4 생산측)

> 맵 `spec/CAPABILITY-MAP-87.md` · 계획서 `plans/87` §0.7·§5.2·§5.3·§5.4(b)·§5.5·§8 · 결정 D-195·D-274 · 스펙 원천 = 제니퍼 Open API 5.6.4(gh-pages `index.html` 인라인 스펙 — 2026-09-29 재파싱: 39경로)
> 이 문서 §3~§5는 **소비자(`sre_agent`·`noise_gate`)와의 계약**이다. 소비자는 import 없이 이 계약을 복제해 테스트한다(R-21).

## 1. 목표 · 비목표

- 목표: 제니퍼 Open API를 **GET + 경로 템플릿 정확 일치 허용목록**으로만 호출하고, 벤더 중립 `apm_*` 8종으로 WAS 진단 증거와 결정적 판정(`was_signals`)을 돌려주며, 이벤트를 폴링해 `alarm:raw`에 폴스타 템플릿 형식으로 발행한다.
- 비목표: 쓰기·제어 API · 원시 API 도구 · 폴스타 DB 접근 · OpenMetrics 노출(J7) · 조치 실행(J6).

## 2. 패키지 · 실행

```
apm_gateway/                   자체 pyproject · 자체 cwd · 루트 venv 공유 · 2단 중첩
├─ pyproject.toml · .env.example · config/{instance_map,event_levels,was_signatures}.yaml
├─ apm_gateway/
│   ├─ __main__.py             기동(uvicorn SSE + 폴러 태스크) — `python -m apm_gateway`
│   ├─ config.py               .env(→ os.environ, 기존 키 우선) + 정책 yaml 로드 — dataclass
│   ├─ domain/                 순수 함수 · 벤더 무지 — signals.py(WAS 판정) · events.py(정규화·레벨·멱등 키)
│   ├─ adapters/jennifer/      벤더 리터럴 전용 — allowlist.py(정본) · client.py · fields.py(식별자 매핑)
│   ├─ application/            resolver.py(정합) · masking.py · tools.py(8종 코어) · poller.py
│   └─ interface/              server.py(FastMCP · Bearer · 등록) · audit.py
├─ tests/                      경계 · 계층 · 허용목록 · 클라이언트 · 계약(목 서버) · 판정 · 폴러
└─ testdata/jennifer/          J0-L 픽스처(기존)
```

- 기동: `cd apm_gateway && ../.venv/bin/python -m apm_gateway` · 기본 `127.0.0.1:9096` · SSE `/sse`.
- 테스트: `cd apm_gateway && ../.venv/bin/python -m pytest -q`(루트 수집 밖 — `sre_agent`·`mcp_server`와 같은 별도 실행).

### 2.1 설정 키 (`apm_gateway/.env` — 인라인 주석 금지 · list는 JSON 배열)

| 키 | 기본 | 뜻 |
|---|---|---|
| `JENNIFER_API_URL` · `JENNIFER_API_TOKEN` | 빈 값 | Open API 주소 · AIOps 전용 토큰. URL이 비면 도구는 `not_configured` |
| `JENNIFER_DOMAIN_IDS` | `[]` | 조회 도메인 제한(비면 `/api/domain` 전체) — 좁히기만 |
| `JENNIFER_API_TIMEOUT_SECONDS` · `JENNIFER_RATE_LIMIT_PER_SEC` · `JENNIFER_MAX_RESPONSE_BYTES` | 10 · 5 · 4194304 | 호출별 timeout(서버 강제) · 초당 상한 · 응답 크기 상한 |
| `APM_GATEWAY_HOST` · `APM_GATEWAY_PORT` · `APM_GATEWAY_LOG_LEVEL` | 127.0.0.1 · 9096 · INFO | MCP 서버 |
| `APM_GATEWAY_BEARER_TOKEN` | 빈 값 | 전송 인증(D-125) — 비면 무인증(운영 필수) |
| `APM_TIMEZONE` | Asia/Seoul | naive `reference_time` 해석 · `alarmTime` 렌더 |
| `APM_INSTANCE_CACHE_SECONDS` · `APM_PROFILE_CALLS_PER_INVESTIGATION` | 600 · 5 | 정합 캐시 TTL · 조사당 프로파일 호출 상한 |
| `APM_EVENT_POLLER_ENABLED` · `APM_EVENT_POLL_INTERVAL_SECONDS` · `APM_EVENT_MIN_LEVEL` · `APM_EVENT_STREAM_KEY` | false · 30(하한 10) · warning · `alarm:raw` | 폴러 |
| `REDIS_HOST` · `REDIS_PORT` · `REDIS_DB` · `REDIS_PASSWORD` | localhost · 6379 · 0 · 빈 값 | 폴러 XADD·커서·멱등 키 |

폴스타 DB 연결 문자열 키는 **없다**(테스트로 고정).

## 3. MCP 도구 계약 (소비자 계약 ①)

공통 인자: `investigation_id?: str` · `thread_id?: str` — 감사 레코드에만 싣는다(R-19). 구간 인자 `reference_time?`(ISO 8601 — naive면 `APM_TIMEZONE`) · `lookback_minutes?`(창 = `[reference_time − lookback, reference_time]`, `reference_time` 생략 = 지금).

| 도구 | 인자 | 뒷단(허용목록) | 반환 `rows[]` 핵심 필드 |
|---|---|---|---|
| `apm_instance_map` | `hostname?` | `/api/domain` → 도메인별 `/api/instance` | `instance_id` · `instance_name` · `domain_id` · `domain_name` · `host_name` · `ip_address` · `platform` · `status` · `agent_version` · `match_confidence` · `match_reason` |
| `apm_app_health` | `hostname` · `instance_id?` · 구간 | `/api/realtime/instance`(구간 끝 = 지금일 때) · `/api/transaction/time`(1분 분할 · 최근 10분 상한) · `/api/status/application`(창 > 10분 — 시 단위) | `response_time_avg_ms` · `tps` · `active_services` · `bad_response_active_services` · `reject_rate` · `concurrent_users` · `window{calls, errors, error_rate, response_time_p50_ms, response_time_p95_ms, response_time_max_ms}`(행) · 최상위 `hourly{calls, failures, failure_rate, response_time_avg_ms, max_response_time_ms, top_applications}`(창 > 10분일 때 — 인스턴스 합산) |
| `apm_runtime_health` | 같음 | `/api/realtime/instance` · `/api/dbmetrics/instance`(`interval_minute=5` · 지표 3종 각 1호출) | `heap_used_mb` · `heap_committed_mb` · `heap_usage_ratio` · `non_heap_used_mb` · `gc_time_usage_pct` · `process_cpu_pct` · `process_memory_mb` · `thread_current` · `trend{heap_used_mb, heap_committed_mb, gc_time_usage_pct: [{time_ms, value}]}` |
| `apm_resource_pool` | `hostname` · `instance_id?` | `/api/realtime/instance` · `/api/activeService/list` | `db_pool_active` · `db_pool_idle_avg` · `db_pool_configured_avg` · `db_pool_usage_ratio` · `thread_current` · `active_services` · `active_by_running_mode{}` · `active_by_datasource{}` |
| `apm_slow_transactions` | `hostname` · `instance_id?` · 구간 · `n≤20`(기본 10) | `/api/transaction/time`(1분 분할 · 상한 10분) · `/api/status/application`(창 > 10분) | `application` · `response_time_ms` · `cpu_ms` · `sql_ms` · `fetch_ms` · `external_ms` · `network_ms` · `error_type` · `end_time_ms` · `profile_ref{domain_id, txid, time_ms}` + 상위 `summary{calls, errors, error_rate, p50, p95, sql_fetch_share, external_share}` |
| `apm_active_services` | `hostname` · `instance_id?` · `n≤20`(기본 10) | `/api/activeService/list` | `application` · `status` · `elapsed_ms` · `status_elapsed_ms` · `running_mode` · `running_text`(마스킹) · `datasource` · `client_ip`(마스킹) · `txid` · `start_time_ms` + `summary{total, by_running_mode, by_status}` |
| `apm_events` | `hostname` · 구간(기본 최근 30분 · 상한 24시간) · `level?`(최소 레벨 `fatal`·`warning`·`normal`) | `/api/dbsearch/event` · `/api/dbsearch/error` | `time_ms` · `level` · `event_type` · `event_kind`(`error`/`metric`) · `value` · `message`(마스킹) · `instance_id` · `instance_name` · `application`(마스킹) · `profile_ref` + 최상위 `errors_by_type[]`(오류 기록 유형별 건수 상위 10) |
| `apm_transaction_profile` | `hostname` · `domain_id` · `txid` · `time_ms` · `top_k≤20`(기본 10) | `/api/transaction/txid` · `/api/transaction/profile.txt`(`Accept: text/plain`) · `/api/transaction/sql` | `transaction{…분해}` · `profile_excerpt`(마스킹 · 줄 수·길이 상한) · `sqls[]`(리터럴 마스킹 · 상위 K) |
| `gateway_health` | 없음 | `/api/domain` 1회(캐시 30초) | `status` · `jennifer_configured` · `jennifer_reachable` · `domain_count` · `allowlist_size` · `poller{enabled, interval_seconds, domains{id: state}}` |

- `apm_*`는 8종 상한(P4). `gateway_health`는 소비자 헬스체크용(holmes `health_check_tool`)이다.
- 도구별 추가 최상위 필드: `apm_slow_transactions`·`apm_active_services`는 `summary` · `apm_app_health`·`apm_slow_transactions`는 창 > 10분일 때 `hourly` · `apm_events`는 `errors_by_type` · `gateway_health`는 `poller`.
- 인스턴스 대상은 결정적으로 확정한다 — `hostname` 정합 결과(최대 5 인스턴스). `instance_id`를 주면 정합 결과 안에서만 좁힌다.

### 3.1 정상 반환

```json
{"rows": [...], "row_count": 1, "queried_at": "ISO", "source_kind": "apm_api", "source": "jennifer",
 "tool": "apm_runtime_health",
 "instance_resolution": {"matched": true, "confidence": "high", "reason": "hostName", "instances": [1001]},
 "window": {"start": "ISO", "end": "ISO", "minutes": 30},
 "was_signals": [ ... §4 ... ],
 "limits": ["[한계] ..."]}
```
`window`는 구간 인자를 쓴 도구만 · `was_signals`는 판정 도구만(빈 배열 가능) · `limits`는 비어도 키가 있다.

### 3.2 오류 반환 (예외 전파 없음)

`{"error": "<code>", "reason": "<마스킹된 설명>", "source_kind": "apm_api", "source": "jennifer", "tool": "<name>"}`

| code | 뜻 |
|---|---|
| `not_configured` | `JENNIFER_API_URL` 미설정 |
| `invalid_argument` | 인자 오류(`hostname` 빈 값 · `profile_ref` 누락 · 범위 밖 `n` 등) |
| `instance_unresolved` | 정합 실패(빈 결과가 아니라 오류 — 침묵 폴백 금지) |
| `profile_ref_mismatch` | `domain_id`가 `hostname` 정합 도메인이 아님 |
| `source_unavailable` | 제니퍼 본문 *"Domain is not connected"*(HTTP 500) · 연결 실패 · timeout |
| `contract_violation` | 본문 *"Required request parameter …"*·*"Cannot parse null string"* — **게이트웨이 버그**(경고 로그 · 재시도 없음) |
| `apm_quota_exceeded` | HTTP 429(토큰 사용량 초과 응답의 실제 모양은 U-5 — 잠정) |
| `apm_api_error` | 그 밖의 비200 · 리다이렉트(비추종) · 응답 크기 상한 초과 · 파싱 실패 |
| `rate_limited` | 조사당 프로파일 호출 상한 초과 |

## 4. `was_signals` 항목 (소비자 계약 ②)

```json
{"kind": "was_heap_pressure", "level": "WARNING", "category": "medium", "label": "힙 메모리 압박",
 "evidence": "heap_used/committed=0.93 (930.0MB/1000.0MB)", "instance_id": 1001, "source_tool": "apm_runtime_health"}
```

| kind | 판정(도구 출력 기반 · 잠정 임계 = `config/was_signatures.yaml`) | level | category | label |
|---|---|---|---|---|
| `was_service_queuing` | `reject_rate > 0` · 이벤트 `SERVICE_QUEUING`·`PLC_REJECTED`·`HIGH_RATE_REJECT` | CRITICAL | strong | WAS 서비스 큐잉(유입 대기·PLC 거절) |
| `was_thread_pool_exhaustion` | 액티브 서비스 중 동일 `running_mode` 비율 ≥ 0.7 · 해당 건 `elapsed_ms` ≥ 600000 · 최소 3건 | CRITICAL | strong | WAS 스레드 정체(동일 실행 모드 장기 정체) |
| `was_db_pool_exhaustion` | `db_pool_usage_ratio` ≥ 0.95 · 이벤트 `JDBC_CONNECTION_FAIL`·`DB_CONNECTION_FAIL`·`DB_CONN_UNCLOSED` | CRITICAL | strong | DB 커넥션 풀 고갈 |
| `was_gc_stall` | `gc_time_usage_pct` ≥ 10 · 이벤트 `MAYBE_GC_TIME_DELAY` | WARNING | medium | GC 지연(GC 시간 비중 과다) |
| `was_heap_pressure` | 이벤트 `OUTOFMEMORY` → CRITICAL/strong · 추세 연속 3샘플 `heap_usage_ratio` ≥ 0.9 · 추세 3구간 하한 계단 상승(누수 의심) · 추세 없으면 현재값 ≥ 0.9 · 이벤트 `JVM_HEAP_MEM_HIGH` → WARNING/medium | CRITICAL/WARNING | strong/medium | 힙 메모리 압박 |
| `was_slow_sql` | 상위 트랜잭션 합계 `(sql+fetch)/response` ≥ 0.6 · 최소 3건 | WARNING | medium | SQL 지연(SQL·Fetch 비중 과다) |
| `was_external_call_delay` | 합계 `external/response` ≥ 0.6 · 최소 3건 · 이벤트 `HTTP_IO_EXCEPTION` | WARNING | medium | 외부 호출 지연 |
| `was_error_burst` | 창 `error_rate` ≥ 0.2 · 최소 20건 · 이벤트 `HIGH_RATE_FAIL` | CRITICAL | strong | 오류 급증 |

- 이벤트 유형 비교는 대문자화 후 접두 `ERROR_`·`WARNING_`를 떼고 한다(운영 명칭 U-1·U-13 미확정 — 두 표기 모두 수용).
- 같은 `(kind, instance_id)`는 1건만(가장 강한 것). LLM은 임계를 판단하지 않는다(D-035).

## 5. `alarm:raw` 레코드 (소비자 계약 ③ — 폴러)

`XADD <APM_EVENT_STREAM_KEY> {"data": json.dumps(payload, ensure_ascii=False)}` — `noise_gate/alarm_server/base_receiver.py:44`와 같은 형식.

```json
{"dbId": "jennifer", "source": "jennifer",
 "serverName": "<정합 hostname — 미해소면 instanceName>", "hostname": "<정합 hostname 또는 빈 값>",
 "ipAddress": "<Instance.ipAddress>", "resourceAncestry": "JENNIFER > <domainName> > <instanceName>",
 "alarmId": "jennifer:<멱등 키 앞 16자>", "severity": 3, "alarmStatus": "",
 "resourceType": "apm.Instance", "resourceName": "<instanceName>",
 "alarmName": "<errorType, 비면 metricsName>", "alarmTime": "yyyyMMddHHmmss(APM_TIMEZONE)",
 "conditions": "JENNIFER EVENT <level> — <alarmName>", "conditionLog": "<마스킹 message> (value=<value>)",
 "apm": {"source": "jennifer", "domain_id": 1000, "domain_name": "...", "instance_id": 1001, "instance_name": "...",
         "event_type": "<alarmName>", "event_kind": "error|metric", "level": "fatal|warning|normal",
         "value": 0.0, "txid": "", "time_ms": 0, "application": "<마스킹>",
         "match_confidence": "high|medium|none", "match_reason": "...",
         "was_signals": [ ... §4 ... ], "idempotency_key": "<sha256>"}}
```

- `severity`: `config/event_levels.yaml` — fatal·critical → 3 · warning → 2 · normal → 1 · recovery·clear → 0 · 미지 → 2(보수). 대소문자 무시.
- 멱등 키 = sha256(`domainId|instanceId|errorType 또는 metricsName|time|txid`). 커서 = 도메인별 마지막 `time`(경계 포함 재조회). 발행 전 Redis `SET NX EX`로 중복 차단 → 재기동·경계 재조회에도 중복 발행 0.
- 도메인 미접속(`source_unavailable`) → 커서 전진 없음 · 백오프(주기 ×2, 최대 ×8) · 상태를 `gateway_health`에 노출. `contract_violation` → 그 도메인 폴링 중지 · 경고 로그.
- `REQUIRED_EVENT_FIELDS`(`serverName`·`hostname`·`severity`) — 미해소 이벤트는 `hostname=""`로 발행되고 조사 트리거가 사유를 남기고 생략한다(계획서 §5.5).

## 6. 안전 통제 (수용 기준)

1. 허용목록 정본 = `adapters/jennifer/allowlist.py`의 16템플릿(메서드 GET · 경로 템플릿 정확 일치 · 경로별 쿼리 키). 목록 밖·비GET·`.xml`·`..`·`//`·`%`·쿼리 `token`·허용 밖 쿼리 키·필수 키 누락은 **HTTP 0회**로 `NotAllowedError`(계획서 §5.2(e) 거부 입력 23건 전부 + v1 POST 변형).
2. `follow_redirects=False` — 3xx는 `apm_api_error`, 리다이렉트 대상 호출 0회.
3. 응답 크기 상한 — Content-Length 선검사 + 스트림 누적 검사.
4. 토큰은 `Authorization` 헤더로만 — 로그·오류·감사·도구 반환에 0회(테스트로 고정).
5. 카탈로그 사본(`testdata/jennifer/scripts/jennifer_catalog.py`) ↔ 정본 대조 테스트(템플릿·필수/선택 키·Accept).
6. 계약 테스트는 목 서버를 상대로 돌리고 끝에 `GET /__mock/hits`로 `allowlisted=false` 0건 · `query_token` 0건을 단언.
7. 마스킹 — `client_ip`·URL 쿼리 값·SQL 리터럴·이메일·휴대폰·주민번호를 반환 전에 가린다. 감사에는 마스킹본만.
