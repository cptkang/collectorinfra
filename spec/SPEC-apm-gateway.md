# SPEC: apm-gateway — 제니퍼 Open API 게이트웨이 (`plans/87` J1·J2·J4·J8 생산측)

> 맵 `spec/CAPABILITY-MAP-87.md` · 계획서 `plans/87` §0.7·§5.2·§5.3·§5.4(b)·§5.5·§8 · **§0.13(J8 다중 소스)** · 결정 D-195·D-274·**D-287** · 스펙 원천 = 제니퍼 Open API 5.6.4(gh-pages `index.html` 인라인 스펙 — 2026-09-29 재파싱: 39경로)
> **[J8 · 2026-09-30]** 게이트웨이 1개가 제니퍼 소스(뷰 서버 — Open API URL + 토큰 1쌍) N개를 묶는다. 인스턴스 식별 = (`source_id`, `domain_id`, `instance_id`). 계약은 **추가만** 했다 — 선택 인자 `source_ids` · 행·`profile_ref`의 `source_id` · `instance_resolution.instance_refs[]` · 봉투 `sources[]` · `apm_transaction_profile`의 `source_id`. 봉투 `source_kind`·`source`는 그대로다. 소스 ↔ 존은 게이트웨이가 모른다(루트 레지스트리 `solutions[apm].sources[]` — 소비자가 푼다).
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
│   ├─ domain/                 순수 함수 · 벤더 무지 — signals.py(WAS 판정) · events.py(정규화·레벨·멱등 키) · sources.py(소스 id 규칙 · dbId)
│   ├─ adapters/jennifer/      벤더 리터럴 전용 — allowlist.py(정본) · client.py(소스당 1개 — 토큰·속도·응답 상한) · fields.py(식별자 매핑)
│   ├─ application/            sources.py(소스 묶음 · 선택 · 부분 실패) · resolver.py(소스 하나의 정합·인벤토리) · masking.py · tools.py(8종 코어) · poller.py
│   └─ interface/              server.py(FastMCP · Bearer · 등록) · audit.py
├─ tests/                      경계 · 계층 · 허용목록 · 클라이언트 · 계약(목 서버) · 판정 · 폴러
└─ testdata/jennifer/          J0-L 픽스처(기존)
```

- 기동: `cd apm_gateway && ../.venv/bin/python -m apm_gateway` · 기본 `127.0.0.1:9096` · SSE `/sse`.
- 테스트: `cd apm_gateway && ../.venv/bin/python -m pytest -q`(루트 수집 밖 — `sre_agent`·`mcp_server`와 같은 별도 실행).

### 2.1 설정 키 (`apm_gateway/.env` — 인라인 주석 금지 · list는 JSON 배열)

| 키 | 기본 | 뜻 |
|---|---|---|
| `JENNIFER_API_URL` · `JENNIFER_API_TOKEN` | 빈 값 | **단일 설정** — Open API 주소 · AIOps 전용 토큰. `JENNIFER_SOURCES`가 비고 URL이 있으면 소스 `default` 1개(v4 동작). 둘 다 비면 소스 0개 → 도구는 `not_configured` |
| `JENNIFER_SOURCES` | 빈 값 | **다중 설정**(J8) — 소스 id JSON 배열(예 `["bank","common","legacy"]` · 선언 순서 = 조회·표시 순서). id = 소문자 슬러그 `[a-z][a-z0-9_]{0,15}` · 예약어 `default`·`api` · 중복 금지. 루트 레지스트리 `solutions[apm].sources[].id`와 같게 둔다 |
| `JENNIFER_<ID>_API_URL` · `JENNIFER_<ID>_API_TOKEN` | — | 소스별 필수(`<ID>` = id 대문자 — `bank` → `JENNIFER_BANK_API_URL`) · 토큰은 소스별 AIOps 전용 토큰 |
| `JENNIFER_<ID>_DOMAIN_IDS` · `_API_TIMEOUT_SECONDS` · `_RATE_LIMIT_PER_SEC` · `_MAX_RESPONSE_BYTES` | 전역 값 | 소스별 선택 — 비면 아래 전역 `JENNIFER_*` 값 |
| `JENNIFER_DOMAIN_IDS` | `[]` | 조회 도메인 제한(비면 `/api/domain` 전체) — 좁히기만 |
| `JENNIFER_API_TIMEOUT_SECONDS` · `JENNIFER_RATE_LIMIT_PER_SEC` · `JENNIFER_MAX_RESPONSE_BYTES` | 10 · 5 · 4194304 | 호출별 timeout(서버 강제) · 초당 상한 · 응답 크기 상한(소스 클라이언트마다 따로 센다) |
| `APM_GATEWAY_HOST` · `APM_GATEWAY_PORT` · `APM_GATEWAY_LOG_LEVEL` | 127.0.0.1 · 9096 · INFO | MCP 서버 |
| `APM_GATEWAY_BEARER_TOKEN` | 빈 값 | 전송 인증(D-125) — 비면 무인증(운영 필수) |
| `APM_TIMEZONE` | Asia/Seoul | naive `reference_time` 해석 · `alarmTime` 렌더 |
| `APM_INSTANCE_CACHE_SECONDS` · `APM_PROFILE_CALLS_PER_INVESTIGATION` | 600 · 5 | 정합 캐시 TTL · 조사당 프로파일 호출 상한 |
| `APM_EVENT_POLLER_ENABLED` · `APM_EVENT_POLL_INTERVAL_SECONDS` · `APM_EVENT_MIN_LEVEL` · `APM_EVENT_STREAM_KEY` | false · 30(하한 10) · warning · `alarm:raw` | 폴러 |
| `REDIS_HOST` · `REDIS_PORT` · `REDIS_DB` · `REDIS_PASSWORD` | localhost · 6379 · 0 · 빈 값 | 폴러 XADD·커서·멱등 키 |

폴스타 DB 연결 문자열 키는 **없다**(테스트로 고정).

**기동 실패(J8 · D-287 ③)** — `JENNIFER_SOURCES`와 단일 설정 키(`JENNIFER_API_URL`·`JENNIFER_API_TOKEN`)를 함께 씀(정본 모호 — 침묵 선택 금지) · 소스 필수 키 누락 · id 형식 위반·예약어·중복 · `JENNIFER_SOURCES`가 문자열 JSON 배열이 아님 → 메시지 1줄(키 이름만 — 값 없음)과 종료 코드 2. 정합 파일이 설정에 없는 소스를 가리키면(`overrides[].source_id` · `per_source.<id>`) 경고 1줄(그 항목은 쓰이지 않는다). 기동 로그 1줄에는 소스 id·URL/토큰 설정 여부·도메인 필터만 싣는다(URL·토큰 값 없음 — R-20).

## 3. MCP 도구 계약 (소비자 계약 ①)

공통 인자: `investigation_id?: str` · `thread_id?: str` — 감사 레코드에만 싣는다(R-19). 구간 인자 `reference_time?`(ISO 8601 — naive면 `APM_TIMEZONE`) · `lookback_minutes?`(창 = `[reference_time − lookback, reference_time]`, `reference_time` 생략 = 지금).
**[J8] 소스 인자**: `apm_transaction_profile`을 뺀 `apm_*` 7종은 선택 인자 `source_ids?: list[str]`를 받는다 — 비면 전 소스(설정 선언 순서) · 모르는 id는 `invalid_argument`(사유에 설정된 id 목록). `apm_transaction_profile`은 `source_id?: str`를 받는다(앞 도구의 `profile_ref.source_id` — 소스가 2개 이상이면 **필수**, 1개면 생략 가능). 정합·호출은 그 소스 서버로만 나간다.

| 도구 | 인자 | 뒷단(허용목록) | 반환 `rows[]` 핵심 필드 |
|---|---|---|---|
| `apm_instance_map` | `hostname?` · `source_ids?` | 소스별 `/api/domain` → 도메인별 `/api/instance` | `source_id` · `instance_id` · `instance_name` · `domain_id` · `domain_name` · `host_name` · `ip_address` · `platform` · `status` · `agent_version` · `match_confidence` · `match_reason`(행마다 — 소스별 규칙이 다를 수 있다) |
| `apm_app_health` | `hostname` · `instance_id?` · 구간 · `source_ids?` | `/api/realtime/instance`(구간 끝 = 지금일 때) · `/api/transaction/time`(1분 분할 · 최근 10분 상한) · `/api/status/application`(창 > 10분 — 시 단위) | `response_time_avg_ms` · `tps` · `active_services` · `bad_response_active_services` · `reject_rate` · `concurrent_users` · `window{calls, errors, error_rate, response_time_p50_ms, response_time_p95_ms, response_time_max_ms}`(행) · 최상위 `hourly{calls, failures, failure_rate, response_time_avg_ms, max_response_time_ms, top_applications}`(창 > 10분일 때 — 인스턴스 합산) |
| `apm_runtime_health` | 같음(`source_ids?` 포함) | `/api/realtime/instance` · `/api/dbmetrics/instance`(`interval_minute=5` · 지표 3종 각 1호출) | `heap_used_mb` · `heap_committed_mb` · `heap_usage_ratio` · `non_heap_used_mb` · `gc_time_usage_pct` · `process_cpu_pct` · `process_memory_mb` · `thread_current` · `trend{heap_used_mb, heap_committed_mb, gc_time_usage_pct: [{time_ms, value}]}` |
| `apm_resource_pool` | `hostname` · `instance_id?` · `source_ids?` | `/api/realtime/instance` · `/api/activeService/list` | `db_pool_active` · `db_pool_idle_avg` · `db_pool_configured_avg` · `db_pool_usage_ratio` · `thread_current` · `active_services` · `active_by_running_mode{}` · `active_by_datasource{}` |
| `apm_slow_transactions` | `hostname` · `instance_id?` · 구간 · `n≤20`(기본 10) · `source_ids?` | `/api/transaction/time`(1분 분할 · 상한 10분) · `/api/status/application`(창 > 10분) | `source_id` · `instance_id` · `application` · `response_time_ms` · `cpu_ms` · `sql_ms` · `fetch_ms` · `external_ms` · `network_ms` · `error_type` · `end_time_ms` · `profile_ref{source_id, domain_id, txid, time_ms}` + 상위 `summary{calls, errors, error_rate, p50, p95, sql_fetch_share, external_share}` |
| `apm_active_services` | `hostname` · `instance_id?` · `n≤20`(기본 10) · `source_ids?` | `/api/activeService/list` | `source_id` · `instance_id` · `application` · `status` · `elapsed_ms` · `status_elapsed_ms` · `running_mode` · `running_text`(마스킹) · `datasource` · `client_ip`(마스킹) · `txid` · `start_time_ms` + `summary{total, by_running_mode, by_status}` |
| `apm_events` | `hostname` · 구간(기본 최근 30분 · 상한 24시간) · `level?`(최소 레벨 `fatal`·`warning`·`normal`) · `source_ids?` | `/api/dbsearch/event` · `/api/dbsearch/error` | `time_ms` · `level` · `event_type` · `event_kind`(`error`/`metric`) · `value` · `message`(마스킹) · `source_id` · `instance_id` · `instance_name` · `application`(마스킹) · `profile_ref` + 최상위 `errors_by_type[]`(오류 기록 유형별 건수 상위 10) |
| `apm_transaction_profile` | `hostname` · `source_id?`(소스 ≥2 필수) · `domain_id` · `txid` · `time_ms` · `top_k≤20`(기본 10) | 그 소스의 `/api/transaction/txid` · `/api/transaction/profile.txt`(`Accept: text/plain`) · `/api/transaction/sql` | `source_id` · `transaction{…분해}` · `profile_excerpt`(마스킹 · 줄 수·길이 상한) · `sqls[]`(리터럴 마스킹 · 상위 K) |
| `gateway_health` | 없음 | 소스마다 `/api/domain` 1회(병렬 · 캐시 30초) | **소스별 행** `source_id` · `status`(`ok`/`degraded`) · `jennifer_configured` · `jennifer_reachable` · `domain_count` · `allowlist_size` · `api_calls_total` + 최상위 `status`(`ok` 모두 정상 / `degraded` 하나라도 / `not_configured` 소스 0개 — 행 0) · `poller{enabled, interval_seconds, domains{"<source_id>:<domain_id>": state}}` |

- `apm_*`는 8종 상한(P4). `gateway_health`는 소비자 헬스체크용(holmes `health_check_tool`)이다.
- 도구별 추가 최상위 필드: `apm_slow_transactions`·`apm_active_services`는 `summary` · `apm_app_health`·`apm_slow_transactions`는 창 > 10분일 때 `hourly` · `apm_events`는 `errors_by_type` · `gateway_health`는 `poller`.
- 인스턴스 대상은 결정적으로 확정한다 — `hostname` 정합 결과(최대 5 인스턴스 · **소스 간 공유**). `instance_id`를 주면 정합 결과 안에서만 좁힌다(여러 소스에 같은 id가 있으면 모두 남는다 — `source_ids`로 좁힌다).
- **[J8] 소스 묶음 규칙**(D-287 ⑦): 고른 소스의 인벤토리를 병렬로 받는다. 한 소스가 실패하면(도메인 목록 실패 · 도메인 0건 · 전 도메인 조회 불가) 그 소스만 빠지고 `[한계] APM 소스 <id> 조회 불가(<code>) — 그 소스의 결과는 빠졌다` + `sources[]`로 드러낸다. **고른 소스가 전부 실패하면 오류** — 원인 코드가 모두 같으면 그 코드(소스 1개면 v4와 같은 코드·사유), 섞이면 `source_unavailable`(사유 = 소스별 나열). 실패·빈 인벤토리는 **30초만** 캐시한다(F-3 — 정상 인벤토리는 `APM_INSTANCE_CACHE_SECONDS`). 한 hostname이 여러 소스에서 정합되면 모두 싣는다. 정합 신뢰도는 정합된 소스가 모두 high일 때만 high이고, 근거는 소스별 근거를 `+`로 잇는다.
- **[J8] 정합 규칙**(M-8): `overrides[].source_id`가 있으면 그 소스에만 적용 · `per_source.<id>.match_rules`가 있으면 그 소스는 전역 `match_rules` 대신 쓴다.
- 소스가 1개면 `[한계]` 문구·오류 사유는 v4 문구 그대로다(소스 표기 없음). 2개 이상이면 위치에 `소스 <id> · 도메인 <n>`을 쓴다.

### 3.1 정상 반환

```json
{"rows": [...], "row_count": 1, "queried_at": "ISO", "source_kind": "apm_api", "source": "jennifer",
 "tool": "apm_runtime_health",
 "instance_resolution": {"matched": true, "confidence": "high", "reason": "host_name", "instances": [1001],
                         "instance_refs": [{"source_id": "bank", "domain_id": 1000, "instance_id": 1001}]},
 "window": {"start": "ISO", "end": "ISO", "minutes": 30},
 "was_signals": [ ... §4 ... ],
 "limits": ["[한계] ..."],
 "sources": [{"source_id": "bank", "status": "ok", "reason": ""},
             {"source_id": "common", "status": "unavailable", "reason": "source_unavailable: ..."}]}
```
`window`는 구간 인자를 쓴 도구만 · `was_signals`는 판정 도구만(빈 배열 가능) · `limits`는 비어도 키가 있다.
`instance_resolution.instances`(id 목록)는 v4 그대로 두고 소스까지 담은 `instance_refs`를 더했다. `sources`는 `gateway_health`를 뺀 도구에 있다(고른 소스만 · 선언 순서) — `status` 어휘: `ok`(인벤토리 정상 · 정합 결과 있음 또는 목록) · `no_match`(인벤토리 정상 · 이 소스에 정합 인스턴스 없음) · `empty`(도메인 0건) · `unavailable`(도메인 목록 실패 · 전 도메인 조회 불가).

### 3.2 오류 반환 (예외 전파 없음)

`{"error": "<code>", "reason": "<마스킹된 설명>", "source_kind": "apm_api", "source": "jennifer", "tool": "<name>"}`

| code | 뜻 |
|---|---|
| `not_configured` | 소스 0개(`JENNIFER_API_URL`·`JENNIFER_SOURCES` 모두 미설정) |
| `invalid_argument` | 인자 오류(`hostname` 빈 값 · `profile_ref` 누락 · 범위 밖 `n` · 모르는 `source_ids` · 소스 ≥2인데 `source_id` 없음 등) |
| `instance_unresolved` | 정합 실패(빈 결과가 아니라 오류 — 침묵 폴백 금지) |
| `profile_ref_mismatch` | (`source_id`, `domain_id`)가 그 소스에서의 `hostname` 정합 도메인이 아님 |
| `source_unavailable` | 제니퍼 본문 *"Domain is not connected"*(HTTP 500) · 연결 실패 · timeout · 도메인 0건 · 고른 소스 전부 실패(원인 코드가 섞일 때) |
| `contract_violation` | 본문 *"Required request parameter …"*·*"Cannot parse null string"* — **게이트웨이 버그**(경고 로그 · 재시도 없음) |
| `apm_quota_exceeded` | HTTP 429(토큰 사용량 초과 응답의 실제 모양은 U-5 — 잠정) |
| `apm_api_error` | 그 밖의 비200 · 리다이렉트(비추종) · 응답 크기 상한 초과 · 파싱 실패 |
| `rate_limited` | 조사당 프로파일 호출 상한 초과 |

## 4. `was_signals` 항목 (소비자 계약 ②)

```json
{"kind": "was_heap_pressure", "level": "WARNING", "category": "medium", "label": "힙 메모리 압박",
 "evidence": "heap_used/committed=0.93 (930.0MB/1000.0MB)", "instance_id": 1001, "source_tool": "apm_runtime_health",
 "source_id": "bank"}
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
- 같은 `(kind, source_id, instance_id)`는 1건만(가장 강한 것 — J8: 소스가 다르면 다른 인스턴스). LLM은 임계를 판단하지 않는다(D-035).

## 5. `alarm:raw` 레코드 (소비자 계약 ③ — 폴러)

`XADD <APM_EVENT_STREAM_KEY> {"data": json.dumps(payload, ensure_ascii=False)}` — `noise_gate/alarm_server/base_receiver.py:44`와 같은 형식.

```json
{"dbId": "jennifer_<source_id>", "source": "jennifer",
 "serverName": "<정합 hostname — 미해소면 instanceName>", "hostname": "<정합 hostname 또는 빈 값>",
 "ipAddress": "<Instance.ipAddress>", "resourceAncestry": "JENNIFER > <source_id> > <domainName> > <instanceName>",
 "alarmId": "jennifer:<멱등 키 앞 16자>", "severity": 3, "alarmStatus": "",
 "resourceType": "apm.Instance", "resourceName": "<instanceName>",
 "alarmName": "<errorType, 비면 metricsName>", "alarmTime": "yyyyMMddHHmmss(APM_TIMEZONE)",
 "conditions": "JENNIFER EVENT <level> — <alarmName>", "conditionLog": "<마스킹 message> (value=<value>)",
 "apm": {"source": "jennifer", "source_id": "bank", "domain_id": 1000, "domain_name": "...", "instance_id": 1001, "instance_name": "...",
         "event_type": "<alarmName>", "event_kind": "error|metric", "level": "fatal|warning|normal",
         "value": 0.0, "txid": "", "time_ms": 0, "application": "<마스킹>",
         "match_confidence": "high|medium|none", "match_reason": "...",
         "was_signals": [ ... §4 ... ], "idempotency_key": "<sha256>"}}
```

- `severity`: `config/event_levels.yaml` — fatal·critical → 3 · warning → 2 · normal → 1 · recovery·clear → 0 · 미지 → 2(보수). 대소문자 무시.
- **[J8] 소스 식별**: `dbId` = `jennifer_<source_id>` — 소비자가 루트 레지스트리로 존을 푼다(존 구독자 전달·ack · F-7 해소). 단일 설정 소스 `default`는 `dbId` = `jennifer` · `resourceAncestry` = `JENNIFER > <domainName> > <instanceName>`(v4 그대로 · 존 없음). `source`는 항상 `jennifer`(소비자 인식 키). `apm.source_id`는 `default` 포함 항상 있다.
- 멱등 키 = sha256(`source_id|domainId|instanceId|errorType 또는 metricsName|time|txid`) — 두 서버의 값이 같은 이벤트가 중복으로 버려지지 않는다. 커서 = (소스, 도메인)별 마지막 `time`(Redis 키 `apm_gateway:poller:cursor:<source_id>:<domain_id>` · 경계 포함 재조회). 발행 전 Redis `SET NX EX`로 중복 차단 → 재기동·경계 재조회에도 중복 발행 0. 소스 간은 병렬로, 소스 안은 도메인 순차로 폴링하고 한 소스의 실패가 다른 소스 폴링을 막지 않는다. 운영 발행 이력이 없어(§0.12) 종전 키(`…:cursor:<domain_id>`)는 옮기지 않았다.
- 도메인 미접속(`source_unavailable`) → 커서 전진 없음 · 백오프(주기 ×2, 최대 ×8 · (소스, 도메인)별) · 상태를 `gateway_health`에 노출. `contract_violation` → 그 (소스, 도메인) 폴링 중지 · 경고 로그.
- `REQUIRED_EVENT_FIELDS`(`serverName`·`hostname`·`severity`) — 미해소 이벤트는 `hostname=""`로 발행되고 조사 트리거가 사유를 남기고 생략한다(계획서 §5.5).

## 6. 안전 통제 (수용 기준)

1. 허용목록 정본 = `adapters/jennifer/allowlist.py`의 16템플릿(메서드 GET · 경로 템플릿 정확 일치 · 경로별 쿼리 키). 목록 밖·비GET·`.xml`·`..`·`//`·`%`·쿼리 `token`·허용 밖 쿼리 키·필수 키 누락은 **HTTP 0회**로 `NotAllowedError`(계획서 §5.2(e) 거부 입력 23건 전부 + v1 POST 변형).
2. `follow_redirects=False` — 3xx는 `apm_api_error`, 리다이렉트 대상 호출 0회.
3. 응답 크기 상한 — Content-Length 선검사 + 스트림 누적 검사.
4. 토큰은 `Authorization` 헤더로만 — 로그·오류·감사·도구 반환에 0회(테스트로 고정). **[J8]** 토큰은 소스 클라이언트마다 따로이고 한 소스의 토큰이 다른 소스 요청에 실리지 않는다(목 서버 `/__mock/hits`의 Bearer 지문 `bearer_fp`로 단언 — 값은 기록하지 않는다). 거부 입력은 소스마다 HTTP 0회. 허용목록·거부 규칙은 전 소스 공통(M-10).
5. 카탈로그 사본(`testdata/jennifer/scripts/jennifer_catalog.py`) ↔ 정본 대조 테스트(템플릿·필수/선택 키·Accept).
6. 계약 테스트는 목 서버를 상대로 돌리고 끝에 `GET /__mock/hits`로 `allowlisted=false` 0건 · `query_token` 0건을 단언.
7. 마스킹 — `client_ip`·URL 쿼리 값·SQL 리터럴·이메일·휴대폰·주민번호를 반환 전에 가린다. 감사에는 마스킹본만.
8. **[J8] 감사** — 감사 1줄에 `sources=<소스 id>:<HTTP 호출 수>,…`(없으면 `-`)를 싣는다(도구 1회 전후 소스별 호출 수 차이 — 캐시로 호출이 없었으면 `-`). `api_calls`는 그 합이다(M-9).
