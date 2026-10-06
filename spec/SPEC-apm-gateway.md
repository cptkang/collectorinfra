# SPEC: apm-gateway — 제니퍼 Open API 게이트웨이 (`plans/87` J1·J2·J4·J8 생산측)

> 맵 `spec/CAPABILITY-MAP-87.md` · 계획서 `plans/87` §0.7·§5.2·§5.3·§5.4(b)·§5.5·§8 · **§0.13(J8 다중 소스)** · 결정 D-195·D-274·**D-287** · 스펙 원천 = 제니퍼 Open API 5.6.4(gh-pages `index.html` 인라인 스펙 — 2026-09-29 재파싱: 39경로)
> **[J8 · 2026-09-30]** 게이트웨이 1개가 제니퍼 소스(뷰 서버 — Open API URL + 토큰 1쌍) N개를 묶는다. 인스턴스 식별 = (`source_id`, `domain_id`, `instance_id`). 계약은 **추가만** 했다 — 선택 인자 `source_ids` · 행·`profile_ref`의 `source_id` · `instance_resolution.instance_refs[]` · 봉투 `sources[]` · `apm_transaction_profile`의 `source_id`. 봉투 `source_kind`·`source`는 그대로다. 소스 ↔ 존은 게이트웨이가 모른다(루트 레지스트리 `solutions[apm].sources[]` — 소비자가 푼다).
> **[134 W0-B · 2026-10-02]** 장기 작업·대용량 전달·자격증명 제거·호출 주체(`plans/134` N-14·N-17·N-18 · `spec/SPEC-apm-question-coverage.md` §2·§3·§4 · D-296 · D-299 ④). 계약은 **추가만** 했다 — 데이터 도구 선택 인자 `owner`·`wait_seconds` · 봉투 `total_row_count`·`artifact`·`job`·`partial` · 작업 도구 3종(`apm_job_status`·`apm_job_cancel`·`apm_job_read`) · 오류 코드 `job_not_found`·`job_not_ready` · `gateway_health.jobs`. `wait_seconds`를 넘기지 않는 소비자(조사·알람)는 종전처럼 끝날 때까지 기다린다. 의미가 바뀐 것은 `JENNIFER_MAX_RESPONSE_BYTES`(상한 → 메모리 임계 — 넘는 응답은 오류가 아니라 스풀 임시 파일로 받는다 · D-296 ④) 하나다. 자격증명 값은 어댑터가 응답을 파싱한 직후 한 곳에서 제거한다(§6 9).
> **[134 W0-B 수정 · 2026-10-02]** 검증·보안 감사 반영 — 자격증명 규칙 확대(헤더 줄 끝까지 · 붙여 쓴 비밀 단어 · 명령줄 · 세션 쿠키 · 키-값 묶음 모양 · XML·JSON 문자열 재귀 · 유니코드 변형) · 오류 사유는 가린 뒤 자른다 · 선형 정규식·큰 본문 스레드 검사 · 감사 `api_calls`·`sources`는 작업별 · 감사 칸 제어 문자 이스케이프 · 스풀 0700/0600 · `wait_seconds` NaN·무한대 거부 · SIGTERM 정상 종료 때 진행 중 작업 interrupted(§3.2 표 복구 · §3.3 · §6 8·9).
> **[134 W1 · 2026-10-02]** 필드 보존·상한 제거(`spec/CAPABILITY-MAP-134.md` 표 B·C W1 행 · `spec/SPEC-apm-question-coverage.md` §2.1·§4.3·§5 W1) — 인자 `n`(1 이상 · 상한 없음)·`full`(slow_tx·active·events) · `level_mode`·`error_type`·`record`(events) · `top_k` 비우면 SQL 전부 · 상한 제거(X-View 10분 · 이벤트 24시간·50건 · `n` ≤ 20 · 추세 인스턴스 2 · 인스턴스 목록 200 · 호스트당 5 · 오류 유형 상위 10 · SQL 1,000자) · 필드 추가(실시간 방문·호출·액티브 구간·소켓/파일/스레드 · 트랜잭션 guid·건수·식별자 가림 · 액티브 `active_ref` · 오류 기록 행 · ApplicationStatus 25필드) · 「전체 파일 전용」 칸 · 프로파일 전문 결과 파일. 계약은 **추가만**이고 결과가 늘어나는 것은 의도된 변경이다(SPEC-coverage §2.1 — 「결과 바이트 동일」 기준 미적용).
> **[134 W2 · 2026-10-02]** 통계·지표·변경 감지(`plans/134` N-5~N-8 · `spec/CAPABILITY-MAP-134.md` 표 A·B·C W2 행 · `spec/SPEC-apm-question-coverage.md` §2.4·§5 W2) — 데이터 도구 3종 추가(`apm_status_stats` · `apm_metrics` · `apm_source_changes`) · `apm_runtime_health` 선택 인자 `metrics`·`interval_minute` · 허용목록 `/api/status/*` 선택 키 · 경로 변수 형식 선언(§2.4) · 설정 `APM_METRIC_CATALOG_TTL_SECONDS` · **행 텍스트 칸은 마스킹한 전문**(종전 300자 절단 제거 — 화면·LLM 입력 절단은 소비자 책임 · 오류 사유는 종전대로 자른다). 계약은 **추가만**이다(텍스트 칸 길이가 늘어나는 것은 의도된 변경).
> **[134 W2 수정 · 2026-10-02]** W1 통합 검증 결함 — `error_type` 표기 재질의(정규화 이름 → `ERROR_` → `WARNING_` 접두 · 최대 3회 · 맞은 표기 `[한계]` — M-1) · 프로파일 중첩 `transaction.instance_oid` 전체 파일 전용(L-3 · 「전체 파일 전용」 칸 이름에 `.` = 중첩 칸) · 봉투 `disclosures`의 `apm_masked_fields`(식별자 가림·자격증명 제거를 실제로 적용했을 때 칸 이름만 — L-5). 계약은 **추가만**이다.
> **[134 W2 검증 수정 · 2026-10-02]** W2 통합 검증 결함 — 선택 조건이 보기를 막지 않는다(W2V-B2: 카탈로그에 없는 지표는 빼고 조회 · 원천이 정렬 기준을 거부하면 그 조건·행 수 없이 다시 받아 로컬 정렬) · 변경 감지 행 `change_detected_at`(ISO 8601 · B6) · 변경 이력 조각 일부 실패 = partial(G1) · 맨 배열의 비객체 원소 = 오류(G2) · 정렬 대응 불가 + 묶음 2개 = 묶음별 상위 n(G3) · 카탈로그 군 단위 모양 위반 = 검증 불가(G4) · 접두만 있는 `error_type` = `invalid_argument`(G5) · `interval_minute` ASCII 숫자만(G6). 계약은 **추가만**이다(종전 `invalid_argument`·오류이던 일부 입력이 이제 `[한계]`와 함께 성공한다 — 의도된 변경).
> **[134 W2 후속 · 2026-10-02]** 선택 조건을 빼거나 바꿔 조회한 경우(모르는 지표 빼기 · 기본 추세 대체 · 정렬 기준 거부 뒤 재조회) 봉투 `disclosures`에 `apm_unresolved_condition`(의무 고지 · 사용자용 한 줄) — §3.1.
> **[134 W5·W6·W7 · 2026-10-06]** 데이터 도구 7종 추가(`apm_transaction_trace` · `apm_change_impact` · `apm_period_compare` · `apm_config` · `apm_environment` · `apm_users` · `apm_active_detail` — §3 표) · `apm_transaction_profile` 선택 인자 `profile_no`·`include_param_key`와 **프로파일 예산 주체 분리**(전송 주체 `chat` 면제 · 칸 `(주체, 조사 ID → owner → 미지정)` · 종전 `_anonymous` 전 주체 공유 폐지) · 오류 기록 행 `profile_ref.profile_no` · 허용목록 16 → **36**템플릿(거래 GUID 1 + 관리·민감 조회 19 — 사용자 목록·계정·환경변수·실행 중 요청 상세 등 **민감 GET 허용**(D-296 ①) · 같은 경로의 비GET·`.xml` 변형·시험 경로는 계속 거부) · 경로 변수 형식 `sint`(부호 있는 정수 — 실행 중 요청 txid) · `.xml` 꼬리 일반 거부 · 계산 순수 함수 `domain/analysis.py`(가중 평균 · 비율 · 증감(기준 0 = N/A · 비율 차는 %p) · 원시 p95). 설정 값(환경변수·데이터 서버 설정)의 이메일·주민번호·휴대폰 `mask_pii` · 프로파일 SQL 응답에서 SQL 문 칸(출처 칸 이름 `SQL_STATEMENT_KEYS`)이 아닌 문자열(바인드 값일 수 있음)은 `mask_identifier`. 보안 감사(AUDIT-1~12) 뒤 자격증명 규칙 개정 — 명령 문맥 칸은 실행 파일만 남김 · 구분자를 걷은 전체 키 판정 · 값 단독 접속 문자열 · 콜론 없는 URL 사용자 정보 · 꼬리 없는 값 끝 · 묶음 칸 끝맺음 · 비200 JSON 오류 본문 · POSIX `PWD`·`OLDPWD`(절대 경로 값) 예외 · 사용자 전화·메일 칸 단위 · `match_template` 전체 일치 · 계정 ID 경로 로그·사유 템플릿 · httpx 로거 WARNING(정본 `spec/SPEC-apm-question-coverage.md` §4.2·§4.3 W7 개정). 계약은 **추가만**이다(결과가 늘어나는 것은 의도된 변경).
> **[130 W1·W2 · 2026-10-06]** 대상 이름 해석(`plans/130` N-1~N-3·N-5·N-6 · D-290 · D-296 ④ · D-299 ③) — 계약은 **추가만** 했다(새 도구·새 플래그 없음 · 새 인자를 주지 않으면 비트 동일) — `apm_instance_map` 선택 인자 `query`(인스턴스 이름·설명 단계 검색)·`business`(업무명 → 인스턴스) · 데이터 도구 13종·관리 도구 3종 선택 인자 `instance_name`(정확 일치 — `hostname`은 선택이 됐고 둘 중 하나가 대상) · 봉투 `search`·`business.counts`·`suggestions` · 허용목록 **37템플릿**(`GET /api/business` + 필수 `domain_id`) · 감사 대상 `query:…`·`business:…`와 `search=` 꼬리 · 정합 파일 선택 키 `business_map`(§3.4).
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
│   ├─ domain/                 순수 함수 · 벤더 무지 — signals.py(WAS 판정) · events.py(정규화·레벨·멱등 키) · sources.py(소스 id 규칙 · dbId) · credentials.py(자격증명 제거) · call_context.py(호출 우선순위·작업 훅) · jobs.py(작업 상태·주체 어휘)
│   ├─ adapters/               throttle.py(우선순위 속도 제어) · json_stream.py(큰 JSON 점진 디코드) — 벤더 중립
│   ├─ adapters/jennifer/      벤더 리터럴 전용 — allowlist.py(정본) · client.py(소스당 1개 — 토큰·속도·메모리 임계·자격증명 제거 경계) · fields.py(식별자 매핑)
│   ├─ application/            sources.py(소스 묶음 · 선택 · 부분 실패) · resolver.py(소스 하나의 정합·인벤토리) · masking.py · tools.py(데이터 도구 8종 코어) · poller.py · jobs.py(장기 작업) · spool.py(결과 파일)
│   └─ interface/              server.py(FastMCP · 주체별 Bearer · 등록 · 작업 도구) · audit.py
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
| `JENNIFER_API_TIMEOUT_SECONDS` · `JENNIFER_RATE_LIMIT_PER_SEC` · `JENNIFER_MAX_RESPONSE_BYTES` | 10 · 5 · 4194304 | 호출별 timeout(서버 강제) · 초당 상한(폴러·동기 호출·백그라운드 작업이 나눈다 — 대기열은 우선순위 순 · §3.3) · **응답 본문 메모리 임계**(넘으면 `APM_SPOOL_DIR/tmp`의 임시 파일로 받아 점진 파싱 — 오류로 끊지 않는다 · 134 W0-B에서 「응답 크기 상한」에서 의미 변경 · D-296 ④) — 소스 클라이언트마다 따로 |
| `APM_GATEWAY_HOST` · `APM_GATEWAY_PORT` · `APM_GATEWAY_LOG_LEVEL` | 127.0.0.1 · 9096 · INFO | MCP 서버 |
| `APM_GATEWAY_BEARER_TOKEN` | 빈 값 | 전송 인증(D-125) — 호출 주체 `default`. 이것과 아래가 모두 비면 무인증(주체 `anonymous` · 운영 필수) |
| `APM_GATEWAY_BEARER_TOKENS` | 빈 값 | 소비자별 토큰(134 W0-B) — JSON 객체 `{"<주체>": "<토큰>"}`(예 `chat`·`investigation`·`alarm`). 주체 = 소문자 슬러그 `[a-z][a-z0-9_-]{0,31}` · 예약어 `anonymous`. 요청 토큰으로 주체를 정한다(§3.3) |
| `APM_SPOOL_DIR` | `var/spool` | 작업 기록·결과 청크·텍스트 부분·응답 임시 파일 디렉터리(상대 경로는 게이트웨이 루트 = 자체 cwd 기준 · 루트 `.gitignore`의 `apm_gateway/var/`) |
| `APM_INLINE_ROWS` · `APM_ARTIFACT_CHUNK_ROWS` | 500 · 2000 | 응답 `rows`에 싣는 앞 행 수 · 결과 파일 청크 1개의 행 수 — 전달 형태이고 조회 범위를 줄이지 않는다 |
| `APM_ARTIFACT_RETENTION_SECONDS` | 86400 | 작업 기록·결과 파일 보관(끝난 시각 기준) — 지나면 지우고 조회는 `job_not_found` |
| `APM_JOB_MAX_CONCURRENT` · `APM_JOB_STALL_SECONDS` · `APM_PRIORITY_AGING_SECONDS` | 4 · 300 · 10 | 백그라운드 작업 동시 실행 수(넘으면 다음 API 호출 앞에서 FIFO 대기 = `queued`) · 정체 판정(임대 `updated_at` 정지 초) · 속도 대기열 에이징(이만큼 기다릴 때마다 우선순위 한 단계 상승) |
| `APM_TIMEZONE` | Asia/Seoul | naive `reference_time` 해석 · `alarmTime` 렌더 |
| `APM_INSTANCE_CACHE_SECONDS` · `APM_PROFILE_CALLS_PER_INVESTIGATION` | 600 · 5 | 정합 캐시 TTL · 조사당 프로파일 호출 상한 |
| `APM_METRIC_CATALOG_TTL_SECONDS` | 3600 | 소스별 지표 카탈로그 캐시 수명(134 W2 — 지나면 다시 읽고 지문이 바뀌었으면 `[한계]`로 알린다 · 0 이하는 기동 실패) |
| `APM_EVENT_POLLER_ENABLED` · `APM_EVENT_POLL_INTERVAL_SECONDS` · `APM_EVENT_MIN_LEVEL` · `APM_EVENT_STREAM_KEY` | false · 30(하한 10) · warning · `alarm:raw` | 폴러 |
| `REDIS_HOST` · `REDIS_PORT` · `REDIS_DB` · `REDIS_PASSWORD` | localhost · 6379 · 0 · 빈 값 | 폴러 XADD·커서·멱등 키 |

폴스타 DB 연결 문자열 키는 **없다**(테스트로 고정).

**기동 실패(J8 · D-287 ③ · 134 W0-B)** — `JENNIFER_SOURCES`와 단일 설정 키(`JENNIFER_API_URL`·`JENNIFER_API_TOKEN`)를 함께 씀(정본 모호 — 침묵 선택 금지) · 소스 필수 키 누락 · id 형식 위반·예약어·중복 · `JENNIFER_SOURCES`가 문자열 JSON 배열이 아님 · `APM_GATEWAY_BEARER_TOKENS`가 문자열 JSON 객체가 아님·주체 이름 형식 위반·예약어·빈 토큰 · **같은 토큰을 두 주체에 줌**(단일 토큰 `default` 포함 — 주체 모호) · 단일 토큰과 `APM_GATEWAY_BEARER_TOKENS`의 `default`를 함께 씀 · 작업 수치 키가 0 이하 → 메시지 1줄(키·주체 이름만 — 값 없음)과 종료 코드 2. 정합 파일이 설정에 없는 소스를 가리키면(`overrides[].source_id` · `per_source.<id>`) 경고 1줄(그 항목은 쓰이지 않는다). 기동 로그 1줄에는 소스 id·URL/토큰 설정 여부·도메인 필터·호출 주체 이름·작업 수치만 싣는다(URL·토큰 값 없음 — R-20). 기동 때 작업 스풀을 훑는다(§3.3 재기동).

## 3. MCP 도구 계약 (소비자 계약 ①)

공통 인자: `investigation_id?: str` · `thread_id?: str` — 감사 레코드에만 싣는다(R-19). 구간 인자 `reference_time?`(ISO 8601 — naive면 `APM_TIMEZONE`) · `lookback_minutes?`(창 = `[reference_time − lookback, reference_time]`, `reference_time` 생략 = 지금).
**[134 W0-B] 작업 인자**(데이터 도구 11종 · 선택): `owner?: str`(결과·작업 소유자 — 불투명 문자열 · 200자 이하 · 작업 도구는 같은 주체 + 같은 `owner`일 때만 응답) · `wait_seconds?: float`(0 이상 — 이 시간 안에 끝나지 않으면 작업 핸들을 돌려주고 백그라운드로 계속 · **생략 = 끝날 때까지 기다린다**(종전 소비자 의미) · 음수는 `invalid_argument`). 모든 데이터 도구 호출은 게이트웨이 안에서 작업으로 돈다(§3.3).
**[J8] 소스 인자**: `apm_transaction_profile`·`apm_active_detail`(134 W7)을 뺀 데이터 도구는 선택 인자 `source_ids?: list[str]`를 받는다 — 비면 전 소스(설정 선언 순서) · 모르는 id는 `invalid_argument`(사유에 설정된 id 목록). `apm_transaction_profile`·`apm_active_detail`은 `source_id?: str`를 받는다(앞 도구의 `profile_ref.source_id`·`active_ref.source_id` — 소스가 2개 이상이면 **필수**, 1개면 생략 가능). 정합·호출은 그 소스 서버로만 나간다.

| 도구 | 인자 | 뒷단(허용목록) | 반환 `rows[]` 핵심 필드 |
|---|---|---|---|
| `apm_instance_map` | `hostname?` · `source_ids?` · `domain_id?`(130 W1-D — 정수 0 이상 · 그 도메인 인스턴스만 · hostname과 함께면 둘 다 만족 · 조회한 소스의 도메인 목록에 없으면 행 0 + `[한계]` + `_unresolved`) · `query?`(130 W1 — 인스턴스 이름·설명 단계 검색 · §3.4.1) · `business?`(130 W2 — 업무명 → 인스턴스 · §3.4.2) — `hostname`·`query`·`business`는 서로 함께 줄 수 없고 `domain_id`·`source_ids`와는 AND | 소스별 `/api/domain` → 도메인별 `/api/instance` | `source_id` · `instance_id` · `instance_name` · `domain_id` · `domain_name` · `domain_description`(mask_text) · `host_name` · `ip_address` · `platform` · `status` · `agent_version` · `config_file_path` · `description`(mask_text) · `instance_oid`(전체 파일 전용) · `match_confidence` · `match_reason`(행마다 — 소스별 규칙이 다를 수 있다) · 목록은 **전부**(134 W1 — 종전 200 상한 제거 · 인라인 초과는 `artifact`) · 검색·업무 모드의 추가 칸(`hostname`·`match_kind`·`match_tier`·`search_confidence`·`match_kinds`·`business_names`)과 봉투 키(`search`·`business`·`suggestions`)는 §3.4 |
| `apm_app_health` | `hostname` · `instance_id?` · 구간 · `source_ids?` | `/api/realtime/instance`(구간 끝 = 지금일 때 · 대상 `instance_id`) · `/api/transaction/time`(1분 분할 · **창 전체** — 134 W1에서 10분 상한 제거 · 긴 창은 작업) · `/api/status/application`(창 > 10분 — 시 단위 · `max_row` 미지정 = 서버 기본 행 수) | `response_time_avg_ms` · `tps` · `active_services` · `bad_response_active_services` · `reject_rate` · `concurrent_users` · `arrival_rate` · `visit_day`·`visit_hour`·`hit_day`·`hit_hour`(단위·「하루」 경계 미확인 `[한계]` — COV E-07) · `active_range_count_0`~`3` · `service_rate_by_range`(원형) · `instance_description`(mask_text) · `instance_oid`(전체 파일 전용) · `window{calls, errors, error_rate, response_time_p50_ms, response_time_p95_ms, response_time_max_ms}`(행) · 최상위 `hourly{calls, failures, failure_rate, response_time_avg_ms, max_response_time_ms, application_count, top_applications, hour_start, hour_end}`(창 > 10분일 때 — 합계는 받은 **전** 애플리케이션 행 · 평균은 `total_response_ms ÷ calls` · `top_applications`는 평균 응답시간 상위 5개 **요약**이고 행마다 ApplicationStatus 25필드) |
| `apm_runtime_health` | 같음(`source_ids?` 포함) · `metrics?: list[str]`(134 W2 — 인스턴스 지표 카탈로그 식별자 또는 중립 이름 · 카탈로그로 검증 · 비우면 기본 3종 · **카탈로그에 없는 지표는 빼고 조회**하고 `[한계]`에 후보 ≤3 — 전부 모르면 기본 3종 추세로 조회하고 `[한계]`(현재값은 그대로) · W2V-B2) · `interval_minute?`(양의 정수 · 기본 5 · 허용값 미공개 `[한계]`) — 둘 중 하나라도 주고 구간이 없으면 기본 30분 | `/api/realtime/instance` · `/api/dbmetrics/instance`(지표 1개/호출 · **정합된 인스턴스 전부** — 134 W1에서 2개 상한 제거) · `metrics`를 주면 `/api/metrics`(카탈로그 · TTL 캐시) | `heap_used_mb` · `heap_committed_mb` · `heap_usage_ratio` · `non_heap_used_mb` · `gc_time_usage_pct` · `process_cpu_pct` · `process_memory_mb` · `thread_current` · `thread_daemon` · `thread_started` · `socket_count` · `file_count` · `collection_count` · `trend{<지표>: [{time_ms, value}]}`(키 = 중립 이름이 있으면 중립 이름 · 없으면 지표 식별자 — 기본 `heap_used_mb`·`heap_committed_mb`·`gc_time_usage_pct`) |
| `apm_resource_pool` | `hostname` · `instance_id?` · `source_ids?` | `/api/realtime/instance` · `/api/activeService/list` | `db_pool_active` · `db_pool_idle_avg` · `db_pool_configured_avg` · `db_pool_usage_ratio` · `thread_current` · `active_services` · `active_by_running_mode{}` · `active_by_datasource{}` |
| `apm_slow_transactions` | `hostname` · `instance_id?` · 구간 · `n?`(1 이상 · 상한 없음 · 기본 10) · `full?`(전체 행) · `source_ids?` | `/api/transaction/time`(1분 분할 · 창 전체) · `/api/status/application`(창 > 10분) | `source_id` · `domain_id` · `domain_name` · `instance_id` · `instance_name` · `application`(mask_url) · `txid` · `guid` · `response_time_ms` · `cpu_ms` · `sql_ms` · `fetch_ms` · `external_ms` · `network_ms` · `frontend_ms` · `sql_count` · `fetch_count` · `external_call_count` · `error_type` · `start_time_ms` · `end_time_ms` · `collect_time_ms` · `client_ip`(mask_ip) · `client_id`·`user_id`(mask_identifier — 앞 1자 + `***`) · `is_async` · `link_root` · `has_stacktrace` · `business_ids` · `business_names` · `instance_oid`(전체 파일 전용) · `profile_ref{source_id, domain_id, txid, time_ms}` + 상위 `summary{calls, errors, error_rate, p50, p95, sql_fetch_share, external_share}`(판정·비중은 상위 `n`) |
| `apm_active_services` | `hostname` · `instance_id?` · `n?`(1 이상 · 기본 10) · `full?` · `source_ids?` | `/api/activeService/list` | `source_id` · `domain_id` · `domain_name` · `instance_id` · `instance_name` · `application`·`application_alias`(mask_url) · `status` · `status_name` · `status_message`(mask_text) · `elapsed_ms` · `status_elapsed_ms` · `running_ms` · `cpu_ms` · `sql_count` · `fetch_count` · `running_mode` · `running_text`(mask_text) · `running_hash` · `running_sherpa_oracle_instance` · `running_sherpa_oracle_seq` · `datasource` · `client_ip`(mask_ip) · `txid` · `session_id` · `thread_hash` · `start_time_ms` · `business_ids` · `business_names` · `instance_oid`(전체 파일 전용) · `active_ref{source_id, domain_id, txid, session_id, thread_hash}`(실행 중 요청 상세 입력 — W7) + `summary{total, by_running_mode, by_status}` |
| `apm_events` | `hostname` · 구간(기본 최근 30분 · **기간 상한 없음**) · `level?`(`fatal`·`warning`·`normal`) · `level_mode?`(`min` 기본 = 그 레벨 이상 · `exact` = 그 레벨만 — API `level`(대문자)로도 넘기고 응답을 다시 정확 일치로 거른다 · API 의미 W10) · `error_type?`(영문·숫자·밑줄 → 대문자 · `ERROR_`·`WARNING_` 접두 무시 — 이벤트는 같은 유형만 · `/api/dbsearch/error`에는 **정규화 이름(접두 없음) → `ERROR_` → `WARNING_` 접두 표기**를 (소스, 도메인)마다 차례로 물어(최대 3회) 처음 비지 않은 결과를 쓰고 맞은 표기를 `[한계] 오류 유형 API 표기 …로 조회`에, 셋 다 0건이면 그 사실을 `[한계]`에 적는다 — 운영 명명 U-13 미확정 · W10 · 134 W2 수정 M-1 · 접두뿐인 값(`ERROR_`)은 `invalid_argument` — W2V-G5) · `record?`(`event` 기본 · `error` = 행이 오류 기록) · `n?`(최근 N건 · 비우면 전부) · `full?` · `source_ids?` | `/api/dbsearch/event` · `/api/dbsearch/error` | `record=event`: `time_ms` · `level` · `event_type` · `event_kind`(`error`/`metric`) · `value` · `message`(mask_text) · `source_id` · `domain_id` · `domain_name` · `instance_id` · `instance_name` · `application`(mask_url) · `instance_oid`(전체 파일 전용) · `profile_ref` / `record=error`: `time_ms` · `error_type` · `value` · `message`(mask_text) · `source_id` · `domain_id` · `domain_name` · `instance_id` · `instance_name` · `application`(mask_url) · `txid` · `profile_index` · `instance_oid`(전체 파일 전용) · `profile_ref` + 최상위 `record` · `errors_by_type[]`(오류 기록 **전 유형** 건수 순 — 134 W1에서 상위 10 절단 제거) · 판정은 걸러진 이벤트 전부에서 |
| `apm_transaction_profile` | `hostname` · `source_id?`(소스 ≥2 필수) · `domain_id` · `txid` · `time_ms` · `top_k?`(SQL 개수 · 비우면 **전부** — 134 W1에서 ≤20 상한 제거) · **[134 W5]** `profile_no?`(0 이상 정수 → `/api/transaction/sql`만 · 오류 기록 행 `profile_ref.profile_no`) · `include_param_key?`(bool → sql만) — 원천 선택 키 `key`는 허용목록에만 있고 **보내지 않는다**(의미·값 출처 미확인 W10 — 항상 `[한계]`) · **프로파일 예산**: 전송 주체 `chat`은 면제 · 그 밖 주체는 칸 `(principal, investigation_id → owner → "_unspecified")`마다 `APM_PROFILE_CALLS_PER_INVESTIGATION`(기본 5)회/1시간 — 주체는 전송 토큰에서만 정한다(인자로 면제 불가 · 종전 `_anonymous` 전 주체 공유 폐지 · 넘으면 `rate_limited`) | 그 소스의 `/api/transaction/txid` · `/api/transaction/profile.txt`(`Accept: text/plain`) · `/api/transaction/sql` | `source_id` · `transaction{…분해 · slow_transactions와 같은 칸 · 식별자 가림 · `instance_oid`는 전체 파일 전용(134 W2 수정 L-3)}` · `profile_excerpt`(화면용 발췌 — 앞 60줄·4,000자 · 마스킹) · `profile_truncated` · `sqls[]`(리터럴 마스킹 · 전부 · 길이 자르지 않음) — 발췌가 잘렸으면 **전문(마스킹본)은 결과 파일** `artifact.text_parts` `profile`(`apm_job_read part=profile`) |
| `apm_status_stats`(134 W2 · N-5) | `kind`(`application`·`sql`·`external_call`) · `hostname` · `instance_id?` · 구간(기본 60분) · `sort_by?`(정렬 기준 지표 이름 — 허용값 미공개라 식별자 형식만 검사 · **원천이 거부하면(`apm_api_error`) 그 묶음은 정렬 기준·`max_row` 없이 다시 받아 로컬에서 정렬**하고 `[한계] 원천이 정렬 기준 X를 받지 않아 전체를 받아 정렬했다(… 원천 사유)` — 다시 받기도 실패하면 그 사유로 실패 · 미접속·timeout은 다시 묻지 않는다 · W2V-B2) · `n?`(1 이상 · 기본 10) · `full?` · `application_name?`(`kind=application`에서만 — 서비스 이름 패턴) · `source_ids?` | (소스, 도메인)마다 `/api/status/<kind>` 1호출 — 구간을 **정시 경계로 내림·올림**(`[한계]`에 요청·조회 구간) · `instance_id` = 그 도메인의 정합 인스턴스 전부(콤마) · `sort_by_metrics` = `sort_by` · `max_row` = `n`(`full`이면 생략 — 서버 기본 행 수 미공개 `[한계]`) · `application_name` | `source_id` · `domain_id` + `kind=application`: ApplicationStatus 25필드(`application`(mask_url) · `calls` · `failures` · `bad_responses` · `response_time_avg_ms` · `max_response_time_ms` · `response_time_stddev_ms` · `*_per_tx` · `sqls`·`fetches`·`external_calls` · `frontend_*` · `network_ms` · `total_response_ms` · `total_cpu_ms` · `total_sql_ms` · `total_fetch_ms` · `total_external_ms`) / `kind=sql`·`external_call`: SqlAndExternalCallStatus 7필드(`name`(sql = mask_sql · external_call = mask_url) · `calls` · `failures` · `bad_responses` · `response_time_avg_ms` · `max_response_time_ms` · `total_response_ms`) + 최상위 `kind` · `hour_start` · `hour_end` · `summary{row_count, calls, failures, failure_rate, bad_responses, response_time_avg_ms(= Σtotal_response_ms ÷ Σcalls — 호출 수 가중), max_response_time_ms}`(표시 행 기준 — `full`이 아니면 `[한계]`). 정렬 기준 → 행 칸 대응은 응답 필드 이름·동의어 `count`·`averageResponseTime` + **표기 정규화(snake_case → camelCase)만**이다(뜻 추측 없음 — `response_time` → `responseTime`). 묶음이 둘 이상이거나 원천이 정렬을 거부했으면 받은 행을 그 칸으로 **로컬 정렬** 뒤 상위 `n`. 대응 칸이 없으면 **묶음마다 원천 순서의 상위 `n`을 모두** 남기고 `[한계] … 전역 순위 아님 — 묶음별 상위 n`(W2V-G3 · 원천이 거부했으면 「원천 기본 순서」) |
| `apm_metrics`(134 W2 · N-6) | `mode`(`catalog` 기본 · `series`) · `scope?` · `source_ids?` + series: `hostname` · `instance_id?` · `metrics: list[str]`(필수 · 카탈로그 검증 · **전부** 모르면 `invalid_argument` + 후보 ≤3 · 일부만 모르면 빼고 조회 + `[한계]`(후보 ≤3) · partial — W2V-B2) · `interval_minute?`(기본 5) · 구간(기본 60분) | catalog: 소스별 `/api/metrics`(`result`가 **객체** — 전 지표 군 · TTL `APM_METRIC_CATALOG_TTL_SECONDS` · 다시 읽어 지문이 바뀌면 `[한계] 지표 카탈로그 변경 감지 … 추가 n · 삭제 m` · **군 단위 모양 위반**(목록이 아님·문자열이 아닌 항목)은 빈 군으로 받지 않고 그 군을 `[한계]`(partial)에 적는다 — series는 그 군을 「검증 불가」로 보고 검증 없이 조회 + `[한계]`(partial) · 정상 군이 하나도 없으면 `apm_api_error` · W2V-G4) / series: (인스턴스 × 지표)마다 `/api/dbmetrics/instance` 1호출 | catalog 행 `{source_id, scope, metric}`(scope = `domain`·`instance`·`business`·`application`·`sql`·`external_call` · `scope`로 거르기) / series 행 `{source_id, domain_id, instance_id, instance_name, metric, time_ms, value}`(긴 형식 · 데이터 점 0개인 (인스턴스, 지표)는 `[한계]`) + 최상위 `mode` · (series) `scope` · `interval_minute`. series `scope`는 `instance`만 — `domain`은 W3, `business`는 W4 예정(`invalid_argument`) |
| `apm_source_changes`(134 W2 · N-7) | `hostname` · 구간(기본 24시간 · 상한 없음) · `source_ids?` | 정합 인스턴스의 (소스, 도메인)마다 `/api-v2/deploy/{domainId}`를 **25시간 이하 조각**으로(이어 붙임) — v2 응답은 **맨 배열**(배열이 아니거나 객체가 아닌 항목이 있으면 `apm_api_error` — 빈 목록으로 강등하지 않는다 · COV E-18 · W2V-G2) · 한 묶음의 둘째 이후 조각이 실패하면 받은 조각의 행은 남기고 남은 조각은 생략 + `[한계] … 조각 i/N부터`(partial — 받은 조각이 없는 묶음만 실패로 센다 · W2V-G1) | `source_id` · `domain_id` · `instance_id` · `instance_name` · `change_detected_ms`(원시 epoch ms — 데이터 서버가 변경을 **인지한** 시각) · `change_detected_at`(같은 시각 ISO 8601 · `APM_TIMEZONE` · 초 단위 — W2V-B6) — 겹침 제거 키 (`source_id`, `domain_id`, `instance_id`, `change_detected_ms`) · 대상 인스턴스로 거름 · 최근 순. 항상 `[한계] 변경 감지(데이터 서버가 소스코드·리소스 변경을 인지한 시각) — 배포 확정 아님` |
| `apm_transaction_trace`(134 W5 · N-13 · A-3) | `guid`(필수 · 앞뒤 공백 제거 뒤 1~256자 · 공백·Cc·Cf 거부 — 형식은 W10 미검증) · `hostname?`(그 서버의 정합 도메인만 · 비우면 고른 소스의 **전 도메인**) · 구간 · `around_ms?`(기준 epoch ms — 앞 행의 `start_time_ms`·`end_time_ms`·`profile_ref.time_ms`) · `around_minutes?`(기본 5) · `source_ids?` — 창 우선순위: 명시 구간 > `around_ms ± around_minutes` > 최근 60분(명시가 아니면 `[한계] 기간 미지정 — ±N분(앞 결과 시각 기준)` · `[한계] 기간 미지정 — 최근 60분` — **이 문구는 계약이다**: 본체 결정적 GUID 줄이 「기간 미지정」 표지를 그대로 옮긴다 · `around_minutes`만 주면 `[한계] around_minutes는 around_ms 없이 쓰지 않았다`) | (소스, 도메인)마다 `/api/transaction/guid` 1호출(필수 `domain_id`·`guid`·`start_time`·`end_time`) · 호스트 미지정이면 소스 인벤토리 전 도메인(명단 조회에 실패한 도메인도 GUID는 묻는다) | `source_id` + `_tx_fields` 칸 전부(식별자 가림 · `instance_oid` 전체 파일 전용) + `profile_ref` + `trace_order`(1부터) · 중복 제거 키 (`source_id`, `domain_id`, `txid`) · 정렬 (`start_time_ms` — None은 맨 뒤 · `source_id` · `domain_id` · `txid`) · 원천이 **다른 GUID 행**을 주면 빼고 `[한계]` · 최상위 `summary{guid, transactions, domains_queried, domains_with_hits, domains_failed, sources, first_start_ms, last_end_ms, span_ms, instances}` · 항상 `[한계] GUID가 같은 거래 묶음이다 — 호출 관계(토폴로지)를 뜻하지 않는다` · 히트가 둘 이상 묶음이면 시계 차이 미보정 `[한계]` · 0건 = 빈 행 + `[한계]`(오류 아님) · 일부 도메인 실패 = partial · 전부 실패 = 오류 · 응답이 `{result: list}`가 아니면 `apm_api_error`(빈 결과로 강등 금지) · `domains_with_hits`·시계 차이 고지는 중복 제거 뒤 **행**의 (소스, 도메인)으로 센다 |
| `apm_change_impact`(134 W6 · A-2 · F-09) | `hostname` · 구간(변경 탐색 · 기본 24시간 — `apm_source_changes`와 같음) · `width_minutes?`(전후 비교 폭 · 기본 60 · 1 이상) · `n?`·`full?`(비교할 변경 건수 — 기본 전부 · 최근 순) · `source_ids?` | 변경 목록 = `apm_source_changes`와 같은 헬퍼(그 도구 반환은 바이트 불변 — 기준선 대조) · 변경마다 그 인스턴스의 X-View(전 `[t−w, t)` · 후 `[t, min(t+w, 지금))` · 1분 분할) + `/api/dbsearch/error`(같은 두 구간) | 변경 1건 = 1행: `source_id`·`domain_id`·`instance_id`·`instance_name`·`change_detected_ms`·`change_detected_at`·`width_minutes`·`before{start_ms, end_ms, calls, tx_errors, error_rate(분수), avg_response_ms(Σ÷calls), p95_response_ms(원시 nearest-rank), max_response_ms, error_records, errors_by_type}`·`after{…}`·`delta{calls, tx_errors, error_rate, avg_response_ms, p95_response_ms, error_records}`(각 `{abs, pct}` — `error_rate.abs`는 **%p** · 기준 0이면 `pct` None(N/A) · 한쪽 None이면 둘 다 None) · 1분 조각 하나라도 실패하면 그 구간 X-View 칸 전부 None(부분 건수를 구간 값으로 내지 않는다) · 감지 시각 None·미래 시각은 비교하지 않고 `[한계]` · 후 구간이 w보다 짧으면 `[한계]` · 항상 `[한계] 변경 감지 시각 전후의 동반 변화다 — 원인 확정이 아니다` · `summary{changes, compared, window, width_minutes}` · 변경 0건 = 빈 행 + `[한계]` · 비교 원천 전부 실패 = `source_unavailable` |
| `apm_period_compare`(134 W6 · A-1 · **조사 소비 · 채팅 배선 없음**) | `hostname` · `current_start`·`current_end`·`baseline_start`·`baseline_end`(ISO 8601 · naive = `APM_TIMEZONE` · start ≥ end·누락·형식 밖 = `invalid_argument` HTTP 0회) · `n?`·`full?`(행에만 — `summary`는 전 인스턴스) · `source_ids?` | **인스턴스 × 구간마다** `/api/status/application` 1호출(ApplicationStatus 행에 인스턴스 칸이 없다) · 구간은 정시 경계로 넓힌다(실제 시 범위 `[한계]`) | 인스턴스별 행(현재 구간 호출 수 순): `current`·`baseline` 각 `{calls, failures, failure_rate, avg_response_ms(Σtotal_response_ms÷Σcalls — 재료 없으면 None + 「계산 불가」 `[한계]` · 평균×호출 수로 대체하지 않는다), max_response_ms}` + `delta`(기준 0 = N/A · `failure_rate.abs`는 %p) · 한쪽에만 있는 인스턴스는 다른 쪽 None(0으로 채우지 않음) · 한 구간에서 인스턴스 조회가 하나라도 실패하면 그 구간 전체 합계 None + `[한계]` · `summary{current, baseline, delta}`(전체 합계 · 봉투에 `window` 키 없음) · 고지: 두 구간 길이 차이 · p95 미제공(시 단위 통계에 분포 없음) · 서버 기본 행 수 미공개(W10) |
| `apm_config`(134 W7 · N-15) | `kind`(필수 · `event_rules`·`color_boundary`·`process_instance`·`data_server`·`db_path`·`loaded_classes`·`rdb_export`) · `hostname?` · `source_ids?` · `rule_type?`(`error`·`metric`·`compare` · 비우면 셋 다) · `target?`(`domain`·`instance`·`business` — compare는 앞 둘) · `error_type?`(대문자 `[A-Z0-9_]{1,64}`) · `process_id?`(process_instance 필수 · 양의 정수) · `search?`(loaded_classes · 256자 · 제어 문자 거부) — kind에 쓰이지 않는 인자는 **빼고 조회 + `[한계]`**(오류 아님) | 범위: 도메인(event_rules·db_path — `hostname` 정합 도메인 · 없으면 고른 소스 전 도메인) · 인스턴스(loaded_classes — `hostname` 필수) · 소스(color_boundary·data_server·rdb_export·process_instance). 경로: `/api-v2/manage/rule/event/error/{d}` · `…/metric/{d}/{domain\|instance\|business}` · `…/compare/{d}/{domain\|instance}`(**404일 때만** `…/comparing/…`로 다시 묻고 답한 표기를 `[한계]` — COV E-01) · `error_type`이면 `…/error/{d}/{errorType}/applied` + `hostname`이면 인스턴스마다 `…/individual-setting/{i}`(404 = 개별 설정 없음 · 오류 아님) · `/api-v2/manage/rule/active-service-color-range-boundary` · `/api-v2/manage/instance?processId=&hostname=` · `/api-v2/manage/data-server/{domains,resource,system-property-config}` · `/api-v2/manage/db/path/{d}` · `/api-v2/loaded-class/{d}/{i}?search=` · `/api-v2/manual-rdb-export` | 봉투 `kind` · 행 칸(kind별 · 모든 행 `extra` = 스펙 표 밖 원형 키 — 문자열 `mask_text` · 키 이름만 `[한계]`): event_rules `source_id, domain_id, domain_name, rule_type(error·metric·compare·error_applied·error_individual), target_type` + 룰 칸(`error_type`·`metric_id`·`level`·`applied`·`expression`·`check_time_range_ms`·`threshold_error_count`·`icon_recovery_time_ms`·`custom_message`·`auto_script_command`(중앙 경계 + `mask_text`)·compare `target_operator`·`target_period`·`target_ratio_pct`·`filter_metric_id`·`filter_minimum_value` · individual `instance_id`·`instance_name`·`individual_setting`(bool\|null)·`individual_setting_found`) · color_boundary 소스당 4행 `color`·`color_label`·`lower_bound`·`upper_bound` · process_instance `domain_id, instance_id, hostname, instance_name` + 항상 버전 조건 `[한계]`(서버 5.6.0.21+ · Java 에이전트 5.6.0.8+ — COV E-03) · data_server `section`(domains·resource·system_properties)·`server` + 섹션별 칸(설정 값 문자열은 `mask_pii`) · `summary.data_server_count` · db_path `db_main_path`·`db_backup_path` · loaded_classes 클래스 1개 = 1행 `class_name`·`super_class_name`·`interface_class_names`·`class_loader_name`(서버 거부면 「6만 개 이하일 때만 응답 — search로 좁혀 다시」) · rdb_export `export_id`·`export_date`·`status` · v2 404·405 = 「이 제니퍼 버전이 경로를 지원하지 않을 수 있다(COV E-28)」 실패 · 모양 위반 = `apm_api_error` |
| `apm_environment`(134 W7 · N-16) | `hostname?`(그 서버 인스턴스만 · 비우면 전 도메인) · `scope?`(`SYSTEM`·`JAVA` · 대소문자 무시) · `key?`(이름 부분 일치 · 대소문자 무시 · 로컬 필터) · `source_ids?` | 도메인마다 `/api-v2/environment-variable/{d}`(응답 = 인스턴스 키 → 묶음) | 긴 형식 `source_id, domain_id, instance_id, instance_name, scope, name, value` · **키를 골라 줄이지 않는다**(D-296 ③) · 값의 비밀은 중앙 경계가 `[가림]` · 이메일·주민번호·휴대폰은 `mask_pii`(설정 훼손을 피해 SQL·URL·IP 규칙은 적용하지 않는다 — 가렸으면 `apm_masked_fields` `value`) · SYSTEM·JAVA 밖 묶음은 원래 키 이름을 `scope`로 + `[한계]`(COV E-13) · 대상 인스턴스가 응답에 없으면 `[한계]` |
| `apm_users`(134 W7 · N-16) | `user_id?`(계정 ID `[A-Za-z0-9._@-]{1,64}` · 점만·`.xml` 꼬리 거부) · `source_ids?` | 없으면 `/api/auth/userlist` + `/restapi/users` · 있으면 `/restapi/user/{id}`(404·빈 본문 = 0행 + `[한계] 계정 x*** 없음…`) | `source_id, origin(user_list·accounts·account), user_id·user_name(mask_identifier), email·phone_number(mask_text), group, allow_ip(mask_ip), extra` · `password`류는 중앙 경계가 키째 제거(파서도 옮기지 않음) · 계정 ID 원값은 감사(target `*`)·`[한계]`·오류 사유·DEBUG 로그(경로 템플릿)에 없다 |
| `apm_active_detail`(134 W7 · N-16 · F-15) | `domain_id`(필수)·`txid`(필수 · 부호 있는 정수 — 문자열 허용)·`session_id?`·`thread_hash?`(받은 값만 보낸다 — 필수 여부 미기재 COV E-15)·`source_id?`(소스 ≥2 필수)·`hostname?`(주면 정합 도메인인지 확인 — 아니면 `profile_ref_mismatch`) — 앞 도구 `apm_active_services` 행의 `active_ref`를 그대로 | `/api-v2/active-service/detail/{domainId}/{txid}?sessionId=&threadHash=` | 1행 `source_id, domain_id, txid(str), session_id, thread_hash, user_id(mask_identifier), guid, sql(mask_sql), http_method, http_query(mask_query), extra` · `limits[0]` = `[한계] 현재값 전용(실행 중 요청)…` · 404·405 = 오류(요청이 이미 끝났거나 버전 미지원) |
| `apm_job_status` | `job_id` · `owner?` | 없음(외부 호출 0) | 봉투 + `job`(§3.1) · 끝났으면(`completed`·`partial`) `result_meta`(행 뺀 결과 봉투 — `summary`·`hourly`·`was_signals`·`limits`·`window`·`sources`…) · `artifact` · `total_row_count` · `rows` = 앞 `APM_INLINE_ROWS`행 미리보기 · 실패·취소·중단이면 `job.error{code, reason}` |
| `apm_job_cancel` | `job_id` · `owner?` | 없음 | 봉투 + `job`(취소 뒤 상태 — 끝난 작업은 상태를 바꾸지 않고 현재 상태) |
| `apm_job_read` | `job_id` · `owner?` · `chunk?: int` · `part?: str` | 없음 | `chunk`(0부터 · 둘 다 없으면 0) → `rows` = 그 청크 전부 + `chunk` · `part` → `text`(텍스트 부분 전문 — 마스킹본) + `part` · 공통 `job`·`artifact`·`total_row_count` |
| `gateway_health` | 없음 | 소스마다 `/api/domain` 1회(병렬 · 캐시 30초) | **소스별 행** `source_id` · `status`(`ok`/`degraded`) · `jennifer_configured` · `jennifer_reachable` · `domain_count` · `allowlist_size` · `api_calls_total` + 최상위 `status`(`ok` 모두 정상 / `degraded` 하나라도 / `not_configured` 소스 0개 — 행 0) · `poller{enabled, interval_seconds, domains{"<source_id>:<domain_id>": state}}` · `jobs{running, queued, slots_in_use, max_concurrent}`(134 W0-B) |

- 데이터 도구 `apm_*` 18종(134 W2에서 `apm_status_stats`·`apm_metrics`·`apm_source_changes` · **W5·W6에서 `apm_transaction_trace`·`apm_change_impact`·`apm_period_compare` · W7에서 `apm_config`·`apm_environment`·`apm_users`·`apm_active_detail`** 추가 — W7 4종은 `interface/manage_server.py`가 등록) + 작업 도구 3종 + `gateway_health`. 종전 「`apm_*` 8종 상한(P4)」은 D-299 ③이 폐지했다(기능 응집으로 묶는다). `gateway_health`는 소비자 헬스체크용(holmes `health_check_tool`)이다.
- 도구별 추가 최상위 필드: `apm_slow_transactions`·`apm_active_services`는 `summary` · `apm_app_health`·`apm_slow_transactions`는 창 > 10분일 때 `hourly`(맥락 — 조회 상한 아님) · `apm_events`는 `record` · `apm_events`는 `errors_by_type` · `apm_status_stats`는 `kind`·`hour_start`·`hour_end`·`summary` · `apm_metrics`는 `mode`(+ series `scope`·`interval_minute`) · `gateway_health`는 `poller`.
- **[134 W2] 행 텍스트 칸은 마스킹한 전문**이다 — `message`(이벤트·오류 기록) · `running_text` · `status_message` · `description` · `instance_description` · `domain_description`. 종전 300자 절단을 걷었다(화면·LLM 입력 절단은 소비자 책임). 오류 사유(`reason`)와 `apm_transaction_profile`의 화면용 발췌(`profile_excerpt` — 전문은 결과 파일)만 자른다. 폴러가 싣는 `alarm:raw` `message`(§5)는 종전대로다.
- **[134 W2] 서버가 거부한 사유** — 허용값이 미공개인 인자(`sort_by` · `interval_minute` — COV E-05·E-06)를 서버가 거부하면(`apm_api_error`) 그 사유(마스킹본)를 `[한계]`에 그대로 붙이고, 모든 묶음이 같은 코드로 실패하면 그 코드·사유로 오류를 돌려준다. 단 `sort_by` 거부는 보기를 실패시키지 않는다 — 그 조건 없이 다시 받는다(W2V-B2 · `apm_status_stats` 행).
- 인스턴스 대상은 결정적으로 확정한다 — `hostname` 정합 결과(정합된 전부 · **소스 간 공유** — 134 W1에서 호스트당 5 상한 제거). `instance_id`를 주면 정합 결과 안에서만 좁힌다(여러 소스에 같은 id가 있으면 모두 남는다 — `source_ids`로 좁힌다). **[130 W1]** 표에서 `hostname`이라고 적은 대상은 `hostname?`·`instance_name?` 둘 중 하나다 — 정확한 인스턴스 이름으로도 정한다(§3.4.3).
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

**[134 W0-B] 추가 키** — 데이터 도구가 끝났을 때(`wait_seconds` 안 또는 생략):

| 키 | 언제 | 뜻 |
|---|---|---|
| `total_row_count` | 항상 | 전체 행 수(`row_count` = `len(rows)` = 인라인 행 수는 그대로) |
| `partial` | 일부 단위 실패 때만 `true` | 일부 소스·도메인·창 조각·호출이 실패했다(사유는 `limits`의 「…조회 실패」·「…조회 불가」) — 부분 결과를 전체로 보이지 않게 소비자가 고지한다 |
| `artifact` | 행이 `APM_INLINE_ROWS`보다 많거나 원문 텍스트 부분이 있을 때 | `{job_id, total_rows, chunks[{index, rows, bytes, sha256}], chunk_rows, columns, text_parts[{name, bytes}], file_only_columns?}` — 나머지 행은 `apm_job_read`로. `file_only_columns`(134 W1 · COV 「전체 파일 전용」 — 지금은 `instance_oid` · 프로파일은 중첩 `transaction.instance_oid` — 이름의 `.`은 중첩 칸)는 결과 파일 청크에만 있고 화면용 `rows`·`apm_job_status` 미리보기에서는 빠진다 |
| `disclosures` | 게이트웨이가 값을 실제로 가렸거나 선택 조건을 빼거나 바꿔 조회했을 때만(134 W2 수정 L-5 · W2V 후속) | **`apm_unresolved_condition`**(의무 고지 — 본체가 답 본문에 반드시 싣는다): 사용자용 한 줄 · 값은 지표·정렬 이름만 — 카탈로그에 없는 지표를 빼고 조회(`요청한 지표 X는 지표 목록에 없어 빼고 조회했습니다` — 런타임·시계열) · 전부 몰라 기본 지표로 조회(`요청한 지표 X를 지표 목록에서 찾지 못해 기본 지표(…)로 조회했습니다` — 런타임) · 원천이 정렬 기준을 거부해 조건 없이 다시 받음(`원천이 정렬 기준 X을(를) 받지 않아 그 조건 없이 받아 직접 정렬했습니다`/`… 원천 기본 순서로 실었습니다`). 같은 줄은 한 번 · `[한계]`와 별도(본체 요약은 `[한계]` 앞 3개만 싣는다). 아래 `apm_masked_fields`보다 앞에 둔다. 작업 밖 직접 호출은 예약 키 `_unresolved`로 남는다. **`apm_masked_fields`**: `[{"kind": "apm_masked_fields", "text": "개인정보·자격증명 보호를 위해 값을 가린 칸: <칸 이름…>"}]` — 칸 이름만(값 없음 · 20개 넘으면 「외 n개」). 대상 = 식별자 가림(`mask_identifier` — `client_id`·`user_id`의 원값이 있었을 때) · 자격증명 제거(어댑터 검사가 값을 바꾸거나 비밀번호 키를 지운 칸 — 응답 필드 이름 · 텍스트 응답은 `(본문)`). 인스턴스 명단 공유 적재에서 가린 칸은 그 적재를 시작한 요청에만 실린다. kind 어휘는 본체 `disclosures[]`(SPEC-coverage §7.5)와 같고 본체가 그대로 통과시킨다. 작업 밖 직접 호출(테스트)은 예약 키 `_masked_fields`로 남는다 |
| `job` | `artifact`가 있거나 `wait_seconds` 안에 끝나지 않았을 때 | `{job_id, state, progress{done, total, unit, label}, estimate{api_calls, seconds}, created_at, updated_at, expires_at}`(+ 실패·취소·중단이면 `error{code, reason}`) |

`wait_seconds` 안에 끝나지 않으면 **접수 봉투**다 — `rows: []` · `row_count: 0` · `total_row_count` 없음 · `job.state` = `running`(슬롯 있음) 또는 `queued`. 데이터 답이 아니다(소비자는 데이터 0건으로 세지 않는다).

```json
{"rows": [], "row_count": 0, "queried_at": "2026-10-02T10:00:08+09:00", "source_kind": "apm_api", "source": "jennifer",
 "tool": "apm_slow_transactions", "limits": [],
 "job": {"job_id": "5c0f…(32 hex)", "state": "running",
         "progress": {"done": 2, "total": 61, "unit": "api_calls", "label": "API 호출"},
         "estimate": {"api_calls": 61, "seconds": 12.2},
         "created_at": "2026-10-02T10:00:00+09:00", "updated_at": "2026-10-02T10:00:07+09:00", "expires_at": null}}
```
`progress.total`·`estimate.api_calls`는 도구가 아는 호출 계획(X-View 1분 조각 × (소스, 도메인) 묶음 · 실시간·시 단위·추세·이벤트 호출 수 · 134 W2 통계 · 지표 시계열 · 카탈로그(캐시가 없을 때만) · 변경 이력 25시간 조각)의 합이고, 모르면 `null`이다(인스턴스 명단 적재는 공유 적재라 작업 호출 수에 넣지 않는다). `estimate.seconds = api_calls ÷ 설정 소스 중 가장 느린 초당 상한`(상한 0 = `null`).
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
| `apm_api_error` | 그 밖의 비200 · 리다이렉트(비추종) · 파싱 실패(지나친 중첩 포함) · (작업 도구) 결과 파일 읽기 실패·sha256 불일치 — 134 W0-B부터 「응답 크기 상한 초과」는 오류가 아니다(메모리 임계 → 스풀) |
| `rate_limited` | 조사당 프로파일 호출 상한 초과 |
| `job_not_found` | (작업 도구) 작업 없음 · 다른 주체나 다른 `owner`의 작업 · 보관 기간 경과 · 작업 ID 형식 밖 — **사유 문구가 모두 같다**(존재 여부를 드러내지 않는다) |
| `job_not_ready` | (`apm_job_read`) 아직 `queued`·`running`인 작업 — `apm_job_status`로 진행을 본다 |

`invalid_argument`에 더해진 경우(134 W2): `apm_status_stats`의 모르는 `kind` · `kind`가 application이 아닌데 `application_name` · `sort_by`가 식별자 형식 밖(영문으로 시작하는 영문·숫자·밑줄 128자 이하) · `apm_metrics`의 모르는 `mode`·`scope` · series에 `metrics` 없음 · series `scope` domain(W3 예정)·business(W4 예정)·application·sql·external_call(→ `apm_status_stats`) · 지표 이름이 형식 밖이거나 고른 소스 **전부**의 카탈로그에 없음(사유에 difflib 후보 ≤3 — 일부 소스에만 없으면 그 소스만 생략하고 `[한계]` · 카탈로그를 못 읽은 소스는 검증 없이 조회하고 `[한계]`(partial)) · `interval_minute` 1 미만·정수 아님(문자열은 ASCII 숫자만 — 위첨자·전각 숫자 거부 · W2V-G6) · `error_type`이 접두뿐(W2V-G5). **W2V-B2로 바뀐 것**: `apm_runtime_health`의 모르는 지표는 오류가 아니다(빼거나 기본 추세 + `[한계]`) · `apm_metrics(series)`는 요청 지표가 **전부** 카탈로그에 없을 때만 `invalid_argument`다. `apm_api_error`에 더해진 경우: 지표 카탈로그 응답의 `result`가 지표 군 객체가 아님 · 변경 이력 응답이 배열이 아니거나 객체가 아닌 항목이 있음(COV E-18 · W2V-G2) · 지표 카탈로그에 정상 군이 하나도 없음.

`invalid_argument`에 더해진 경우(134 W0-B): `wait_seconds` 음수·NaN·무한대(끝까지 기다리려면 생략) · `owner` 200자 초과 · `apm_job_read`의 `chunk` 범위 밖·모르는 `part`·`chunk`와 `part` 동시 · 결과가 없는 작업(`failed`·`cancelled`·`interrupted`)의 읽기.

`invalid_argument`·`instance_unresolved`·`profile_ref_mismatch`에 더해진 경우(130 W1·W2): `query`·`business` 형식과 조합 금지(§3.4.1·§3.4.2) · 대상이 필수인 도구에 `hostname`·`instance_name` 둘 다 없음 · `instance_name` 해소 실패(§3.4.3 오류 표). 종전 문구 「hostname이 비어 있음」은 「hostname 또는 instance_name이 필요하다(둘 다 비어 있음)」로 바뀌었다.

### 3.3 장기 작업 · 호출 주체 (134 W0-B — 정본 `spec/SPEC-apm-question-coverage.md` §3)

- **실행**: 데이터 도구 호출 1회 = 작업 1개(`application/jobs.py`). `wait_seconds` 안에 끝나면 결과 봉투(인라인 초과 행·텍스트 부분은 스풀 → `artifact`·`job`), 못 끝나면 접수 봉투를 돌려주고 같은 코루틴을 백그라운드로 계속한다(승격 — 요청 태스크 그룹 밖 태스크 · 강참조). 승격 전에 호출이 취소되면(세션 끊김) 작업도 취소한다. 짧은 동기 작업은 스풀할 것이 없으면 디스크에 아무것도 쓰지 않는다.
- **상태**: `queued → running → completed | partial | failed | cancelled` · `queued|running → interrupted`(재기동·종료) · `running → failed`(정체 — `error.code = stalled`). `partial` = 결과 봉투 `partial: true`. 도구가 오류 봉투로 끝나면 `failed`(`error` = 그 오류 코드·사유).
- **스풀**: `<APM_SPOOL_DIR>/<job_id>/job.json`(원자적 쓰기 — 임시 파일 → rename) · `rows-00000.jsonl`…(JSON Lines · 청크마다 행 수·바이트·sha256 — 읽을 때 대조) · `text-<part>.txt` · 응답 임시 파일 `<APM_SPOOL_DIR>/tmp/`(파싱 뒤 삭제 · 기동 시 잔여 삭제). 디렉터리 0700 · 파일 0600(umask와 무관 · 기동 시 루트 권한을 좁힌다). 기록 칸 = `job_id`·`tool`·`principal`·`owner`·`target`(마스킹 요약)·`state`·`created_at`·`started_at`·`updated_at`·`finished_at`·`expires_at`·`progress`·`estimate`·`api_calls`·`result_meta`·`artifact`·`limits`·`error` — 인자 원문·토큰·자격증명 없음.
- **속도·우선순위**: 소스 클라이언트의 속도 제어(초당 상한은 그대로)가 대기열을 `poller`(폴러) > `interactive`(승격 전 호출 · 작업 밖 호출) > `background`(승격된 작업) 순으로 판다. 대기가 `APM_PRIORITY_AGING_SECONDS`를 넘을 때마다 한 단계 올린다(기아 방지). 우선순위는 호출 시점 맥락 값(`domain/call_context.py`)이고 승격되면 이후 호출부터 `background`다. 인스턴스 명단 적재는 여러 요청이 함께 기다리는 공유 적재라 빈 맥락(`interactive` · 작업 훅 없음)에서 돈다.
- **동시 실행**: 승격된 작업은 슬롯 `APM_JOB_MAX_CONCURRENT`개를 나눈다 — 슬롯이 없으면 **다음 API 호출 앞에서** FIFO로 기다린다(그동안 `queued`). 호출마다 작업의 소스별 호출 수·`updated_at`(임대)을 올린다. 인스턴스 명단 공유 적재는 우선순위 `interactive`·슬롯 밖에서 돌고, 그 호출 수는 적재를 시작한 요청에만 센다(진행 `done`에는 넣지 않고 기록 `api_calls`·감사에는 넣는다).
- **취소**: `apm_job_cancel` → 코루틴 취소 → `cancelled` · 스풀 결과 조각 삭제(기록은 남는다). 결과를 쓰는 중이거나 끝난 작업은 상태를 바꾸지 않는다.
- **정체**: 감시 루프(`min(정체/5, 30)`초마다)가 `running`인 승격 작업의 `updated_at`이 `APM_JOB_STALL_SECONDS` 넘게 멈추면 취소하고 `failed`(`stalled`).
- **재기동**: 기동 시 스풀의 `queued`·`running` 기록을 `interrupted`(사유 「게이트웨이 재기동으로 중단 — 다시 요청해야 합니다」)로 바꾸고 결과 조각을 지운다. 끝난 작업은 만료 전까지 그대로 읽힌다. 정상 종료(SIGTERM·SIGINT) 때도 진행 중 백그라운드 작업을 `interrupted`로 끝내고 종료 감사를 남긴다 — uvicorn 0.52가 serve() 안에서 신호를 곧바로 다시 올려 정리가 돌지 않던 것을, 신호 재발생을 정리 뒤로 미뤄 고쳤다(종료 코드·신호 의미는 같다).
- **만료**: 끝난 시각 + `APM_ARTIFACT_RETENTION_SECONDS` 뒤 기록·결과를 지운다(조회 시점 확인 + 주기 정리). 만료된 작업 조회는 `job_not_found`.
- **호출 주체**: 전송 미들웨어가 요청 Bearer 토큰으로 주체를 정해(`APM_GATEWAY_BEARER_TOKENS` 주체 · 단일 토큰 = `default` · 무인증 = `anonymous` · 불일치 401) 요청 scope에 싣는다. 작업 기록에 `principal`을 남기고 작업 도구는 **같은 주체 + 같은 `owner`**일 때만 응답한다(아니면 `job_not_found`). 이 결정은 인가를 넓히지 않는다 — 사용자별 인가는 본체가 한다.

### 3.4 대상 이름 해석 — 인스턴스 이름 검색 · 업무명 · `instance_name` (`plans/130` W1·W2 · D-290 · D-296 ④ · D-299 ③)

정합(이름 → 인스턴스)은 게이트웨이 한 곳에서 한다(D-274 ⑤). 새 도구·새 플래그는 없다 — 아래 인자를 주지 않으면 반환이 종전과 비트 동일하다(테스트로 고정). 인자 검사는 모두 **HTTP 전에** 한다(1MB 검색어도 호출 0회). 행·후보 수 상한은 없다(D-296 ④ — 계획서 초안의 「후보 행 상한 50」은 쓰지 않는다).

#### 3.4.1 `apm_instance_map(query=…)` — 인스턴스 이름·설명 검색 (N-1)

- **인자**: 문자열 · 앞뒤 공백 제거 뒤 1~200자(`QUERY_MAX`) — 아니면 `invalid_argument`(「query는 문자열이어야 한다」·「query가 비어 있다」·「query는 200자 이하여야 한다(N자)」). 비지 않은 `hostname`과 함께 오면 `invalid_argument`(「hostname과 query는 함께 줄 수 없다」). `domain_id`·`source_ids`와는 AND.
- **대상**: 고른 소스의 캐시 인벤토리뿐이다(새 HTTP 없음 — 인벤토리가 비었으면 종전 적재 규칙대로 채운다).
- **단계**(결과가 있는 **첫 단계만** 채택 · 인스턴스마다 가장 앞 단계 하나로 센다):

| 단계 | 일치 | `search_confidence` |
|---|---|---|
| `exact` | 이름 casefold가 같다 | `high` |
| `normalized` | 구분자 `-`·`_`·`.`·공백을 뺀 이름이 같다 | `high` |
| `prefix` | 이름이 검색어로 시작하고 바로 뒤가 이름 끝이거나 구분자 | `medium` |
| `contains` | 정규화한 검색어가 3자 이상(`CONTAINS_MIN`)일 때만 — 정규화 이름에 들어 있거나 **마스킹한** 설명(casefold)에 검색어가 들어 있다(가린 원문으로 일치 여부를 흘리지 않는다) | `medium` |

- **행**: 목록 행 모양(§3 표) + `hostname`(역정합 결과 · 없으면 `""`) · `match_confidence`·`match_reason`(역정합 근거 `host_name`·`override`·`regex`·`unresolved`) · `match_kind="instance_name"` · `match_tier`(위 단계) · `search_confidence`. 정렬 = (소스 선언 순서, `domain_id`, `instance_name`).
- **봉투 키**:
  - `search: {"tier": <단계|null>, "counts": {"exact": n, "normalized": n, "prefix": n, "contains": n}}` — `query`를 준 호출에는 항상 있다(감사가 읽는다 · 숫자뿐).
  - `suggestions: [{"instance_name", "source_id", "domain_id"}]` — **0건일 때만**(후보가 없으면 `[]`). 정규화 이름의 difflib ratio ≥ 0.8 상위 3개 · 정렬 (-ratio, 소스 순서, 도메인, 이름). 행에 넣지 않고 자동 채택하지 않는다.
  - 0건이면 `_unresolved`(작업 봉투에서는 `disclosures[{kind: "apm_unresolved_condition"}]`): 「인스턴스 이름 '<마스킹한 검색어>'과(와) 일치하는 인스턴스를 찾지 못했습니다」. 모르는 `domain_id`면 W1-D 문구가 한 줄 더 붙는다.
  - `sources[].status` — 인벤토리는 정상인데 결과가 없는 소스는 `no_match`.

#### 3.4.2 `apm_instance_map(business=…)` — 업무명 → 인스턴스 (N-2 · G-3 ①)

- **인자**: `query`와 같은 형식 규칙(오류 문구의 인자 이름만 `business`). `query`나 비지 않은 `hostname`과 함께 오면 `invalid_argument`(「business는 query·hostname과 함께 줄 수 없다」). `domain_id`·`source_ids`와는 AND(모르는 `domain_id`는 W1-D와 같은 처리).
- **근거**(G-3 ① — **B0가 맞으면 B0만**, 없으면 B1~B3 합집합):

| 근거 | `match_kind` | 일치 규칙 | 신뢰도 |
|---|---|---|---|
| B0 수동 매핑 | `business_map` | 정합 파일 `business_map`의 `business`·`aliases` 중 하나가 업무명과 같다(대소문자·구분자 무시) → 그 항목 `instances`의 **정확한 이름**(strip+casefold)만. 항목에 `source_id`가 있으면 그 소스가 이번 선택에 들 때만 적용(적용되는 B0가 0건이면 B1~B3로 간다). 인벤토리에 없는 이름은 `[한계]` 1줄(쉼표로 이음) · 다른 근거 호출 없음 · `suggestions` = `[]` | `high` |
| B1 도메인 이름 | `domain` | 제니퍼 도메인 이름이 업무명과 같다(대소문자·구분자 무시) → 그 도메인의 전 인스턴스 | `medium` |
| B2 업무 정의 | `business` | 도메인마다 `GET /api/business`(업무 정의)의 이름 또는 **마스킹한** 설명에 업무명이 들어 있다(casefold) → 그 `businessId`를 현재 액티브 서비스 + 최근 5분 트랜잭션(1분 창 5회)으로 역추적한 인스턴스. 정의가 하나라도 맞으면 `[한계] 업무 정의 근거는 최근 처리한 인스턴스만 찾는다(현재 액티브 서비스 + 최근 5분 트랜잭션)` | `medium` |
| B3 인스턴스 이름·설명 | `instance_text` | §3.4.1 검색을 업무명으로 | `medium` |

- **행**: (`source_id`, `domain_id`, `instance_id`)당 1행 — 목록 행 모양 + `hostname`(역정합) · `match_kind`(첫 근거 — `business_map` → `domain` → `business` → `instance_text` 순) · `match_kinds`(근거 전부) · `match_tier`(`instance_text` 근거일 때 §3.4.1 단계 · 아니면 `""`) · `search_confidence`(`business_map`이면 `high` · 그 밖 `medium`) · `business_names`(B2로 맞은 업무 이름 · 아니면 `[]`) — 칸은 **항상** 싣는다. 정렬은 §3.4.1과 같다.
- **봉투 키**: `business: {"counts": {"business_map": n, "domain": n, "business": n, "instance_text": n}}`(근거별 인스턴스 수 — 감사가 읽는다 · 이 모드에는 `search` 키가 없다) · 0건이면 `suggestions`(B3 검색의 후보)와 `_unresolved` 「업무명 '<마스킹>'에 해당하는 APM 인스턴스를 찾지 못했습니다」.
- **부분 실패**: B2 업무 목록·추적 조회 실패는 단위마다 `[한계]`·`partial`이고 B1·B3 행은 그대로 돌려준다. 연결 끊긴 도메인(`inv.unavailable`)은 B2 추적에서 건너뛴다.
- **캐시·비용**: 업무 정의 목록은 (소스, 도메인)별 10분(`BUSINESS_CACHE_SECONDS=600`) · 관측표(`businessId` → 인스턴스)는 (소스, 도메인)별 10분이며 액티브 + 5개 창이 **전부 성공했을 때만** 저장한다(일부 실패를 10분 동안 굳히지 않는다). **첫 질의 주의** — 캐시가 비면 도메인마다 `/api/business`를 1회 부른다(도메인 약 350개 · 초당 5회면 약 70초 — 장기 작업 승격 판단에 `expect_calls`로 호출 계획을 미리 알린다). 같은 도메인의 다른 업무명은 캐시로 HTTP 0회다.
- **정합 파일 `business_map`**(`apm_gateway/config/instance_map.yaml` 선택 키 · 게이트웨이 소유):

```yaml
business_map:
  - business: "대출"                     # 필수 · 비지 않은 문자열
    aliases: ["여신"]                     # 선택 · 문자열 목록
    instances: ["WAS-EXAMPLE-01", "WAS-EXAMPLE-02"]   # 필수 · 비지 않은 문자열 목록(정확한 인스턴스 이름)
    source_id: bank                       # 선택 · 그 소스에서만 찾는다
```
  형식이 틀린 항목은 기동 시 순번만 경고하고 뺀다(침묵 금지 · 목록이 아니면 전체 무시). `source_id`가 설정 소스에 없으면 기동 경고(`overrides`의 미등록 소스 경고와 같은 방식). 운영 업무명·인스턴스 실명은 저장소 기본값에 넣지 않는다(기본 `business_map: []`).

#### 3.4.3 `instance_name` — 인스턴스 이름으로 부르기 (N-3)

- **적용**: 데이터 도구 13종(`apm_app_health`·`apm_runtime_health`·`apm_resource_pool`·`apm_slow_transactions`·`apm_active_services`·`apm_events`·`apm_transaction_profile`·`apm_status_stats`·`apm_metrics`·`apm_source_changes`·`apm_transaction_trace`·`apm_change_impact`·`apm_period_compare`) + 관리 도구 3종(`apm_config`(kind `event_rules`·`db_path`·`loaded_classes`)·`apm_environment`·`apm_active_detail`) — MCP에 `instance_name`이 있는 도구는 16종이다. MCP 스키마는 `hostname: str | None = None`·`instance_name: str | None = None`(hostname이 required에서 빠졌다 · `apm_period_compare`의 required는 `current_start`·`current_end`·`baseline_start`·`baseline_end`). 도구 설명 한 줄: 「hostname 대신 정확한 인스턴스 이름(instance_name)으로도 부를 수 있다 — 부분 이름은 apm_instance_map(query=…)로 먼저 찾는다」.
- **해소**: **정확 일치만**(strip+casefold) — 부분 이름은 해소하지 않는다(부분 검색은 §3.4.1이 하고, 조회 도구가 부분 일치로 여러 인스턴스를 부르지 않게 한다). 같은 이름이 여러 소스·도메인에 있으면 모두 싣는다(`source_ids`·`instance_id`로 좁힌다). hostName이 빈 인스턴스도 찾는다. 빈 문자열·공백뿐이면 주지 않은 것으로 본다. 값이 있으면 `query`와 같은 형식 검사를 HTTP 전에 한다(문자열 · 앞뒤 공백 제거 뒤 200자 이하 — 아니면 `invalid_argument` 「instance_name는 200자 이하여야 한다(N자)」 · 검증 V130-5).
- **AND**: `hostname`과 함께 주면 hostname 정합 결과 중 그 이름만 남긴다.
- **`instance_resolution`**: `confidence="high"` · `reason="instance_name"` · `instance_refs[]`에 `hostname`(역정합 — **이름으로 불렀을 때만**) · 봉투 `hostname`은 역정합 host가 하나로 정해질 때만 그 값(아니면 `""`).
- **행**: 이름으로 불렀을 때만, (`source_id`, `instance_id`)가 있고 `hostname` 칸이 없는 행에 `hostname`(역정합 또는 `""`)을 붙인다 — 폴스타 결과와 합치는 결합 키다. hostname으로 부른 행·refs에는 이 칸이 생기지 않는다(비트 동일).
- **대상이 선택인 도구**(`apm_metrics` catalog · `apm_transaction_trace` · `apm_config`의 그 밖 kind · `apm_environment` · `apm_active_detail`)는 둘 다 없으면 종전처럼 동작한다. `apm_config`의 프로세스·소스 단위 kind는 `instance_name`을 공통 `[한계]`(「kind X는 인자 instance_name를 쓰지 않는다 — 빼고 조회했다」)로 무시한다.
- **오류**:

| 상황 | code | 사유 |
|---|---|---|
| 이름 0건 | `instance_unresolved` | 「instance_name '<x>'에 해당하는 APM 인스턴스 없음 — apm_instance_map(query=…)로 검색」 |
| `instance_id`가 이름 결과에 없음 | `instance_unresolved` | 「instance_id N는 instance_name '<x>' 결과에 없음」 |
| hostname ∧ 이름 교집합이 빔 | `instance_unresolved` | 「instance_name '<x>'은 hostname '<h>' 정합 결과에 없음」 |
| 대상 필수 도구에 둘 다 없음 | `invalid_argument` | 「hostname 또는 instance_name이 필요하다(둘 다 비어 있음)」 |
| 이름이 문자열이 아니거나 200자 초과 | `invalid_argument` | 「instance_name는 문자열이어야 한다」·「instance_name는 200자 이하여야 한다(N자)」(HTTP 0) |
| `apm_config(kind=loaded_classes)`에 둘 다 없음 | `invalid_argument` | 「kind loaded_classes에는 hostname 또는 instance_name이 필요하다」 |
| `apm_active_detail`·`apm_transaction_profile`의 도메인 불일치 | `profile_ref_mismatch` | 대상 표시가 hostname 대신 「instance_name '<x>'」 |

#### 3.4.4 감사 (N-6)

- 대상 칸: `apm_instance_map`은 `hostname` → `query:<검색어>` → `business:<업무명>` → `*` 순(§6 8의 마스킹 · 이메일은 `query:<email>`로 남는다) · 데이터·관리 도구는 `hostname or instance_name or *`(`apm_config`는 `kind:<hostname or instance_name or *>`).
- 정상 줄 끝 꼬리: 인스턴스 검색이면 ` search=<단계|none>(exact:n,normalized:n,prefix:n,contains:n)` · 업무명이면 ` search=business(business_map:n,domain:n,business:n,instance_text:n)`. 결과에 `search`·`business` 키가 있을 때만 붙고, 승격된 작업은 완료 줄에서 `result_meta`로 붙인다. 오류 줄 형식은 그대로다.

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

1. 허용목록 정본 = `adapters/jennifer/allowlist.py`의 **37템플릿**(**[130 W2]** `GET /api/business`(업무 정의 · 필수 `domain_id` — D-290 ④ · §3.4.2 B2 · `/api/realtime/business`·`/api/dbmetrics/business`는 계속 거부) · 134 W5 `/api/transaction/guid` + txid `key`·sql `profile_no`·`key`·`include_param_key` 선택 키 · **134 W7 관리·민감 조회 19** — `/api/auth/userlist` · `/restapi/users` · `/restapi/user/{id}`(`account`) · `/api-v2/manage/data-server/{domains,resource,system-property-config}` · `/api-v2/manage/rule/active-service-color-range-boundary` · `/api-v2/active-service/detail/{domainId}/{txid}`(`int`·`sint` · 선택 `sessionId`·`threadHash`) · `/api-v2/manage/db/path/{domainId}` · `/api-v2/environment-variable/{domainId}` · `/api-v2/manage/instance`(필수 `processId` · 선택 `hostname`) · `/api-v2/loaded-class/{domainId}/{instanceId}`(선택 `search`) · `/api-v2/manage/rule/event/error/{domainId}` · `…/metric/{domainId}/{targetType}`(`enum:domain|instance|business`) · `…/compare/…`·`…/comparing/…`(`enum:domain|instance` — COV E-01 두 표기 · 404 재질의 전용) · `…/error/{domainId}/{errorType}/applied`(`token`) · `…/individual-setting/{instanceId}` · `/api-v2/manual-rdb-export` — 종전 「민감 GET 거부」 단언은 허용 단언으로 반전(C-10) · 같은 경로의 PUT·POST·DELETE·PATCH · `.xml` 꼬리(경로 변수 `account`가 `.`을 받아 `/restapi/user/x.xml`이 맞던 틈을 막는 일반 거부) · 시험 경로는 거부 유지 · 종전 16템플릿: 메서드 GET · 경로 템플릿 정확 일치 · 경로별 쿼리 키 — 134 W1에서 `/api/dbsearch/error`에 선택 키 `error_type` 추가 · **134 W2**에서 `/api/status/application`에 `sort_by_metrics`·`application_name`, `/api/status/sql`·`/api/status/external_call`에 `instance_id`·`sort_by_metrics`·`max_row` 추가 · 카탈로그 사본 동기화). **[134 W2] 경로 변수 형식**(SPEC-coverage §2.4) — `Endpoint.path_vars`에 변수마다 `int`(숫자) · `token`(`[A-Z0-9_]{1,64}`) · `account`(`[A-Za-z0-9._@-]{1,64}`) · `enum:a|b`를 선언하고 템플릿 정규식·`build_path`가 그 형식만 받는다(선언 없는 변수는 기동 시점 실패 · 형식 밖 값은 HTTP 0회 — 134 W7에서 `sint`(`-?[0-9]{1,20}`) 추가 · 경로별 선언 완료). 목록 밖·비GET·`.xml`·`..`·`//`·`%`·쿼리 `token`·허용 밖 쿼리 키·필수 키 누락은 **HTTP 0회**로 `NotAllowedError`(계획서 §5.2(e) 거부 입력 23건 전부 + v1 POST 변형).
2. `follow_redirects=False` — 3xx는 `apm_api_error`, 리다이렉트 대상 호출 0회.
3. 응답 본문 **메모리 임계**(134 W0-B · D-296 ④) — Content-Length 선검사 또는 스트림 누적이 `JENNIFER_MAX_RESPONSE_BYTES`를 넘으면 `APM_SPOOL_DIR/tmp`의 임시 파일로 받고 `{"result": [...]}`·최상위 배열은 원소 단위 점진 디코드(`json.JSONDecoder.raw_decode` — 표준 라이브러리)로 읽는다. 임시 파일은 파싱 뒤(오류여도) 지운다. 비200 큰 본문은 앞 64 KiB로만 분류한다.
4. 토큰은 `Authorization` 헤더로만 — 로그·오류·감사·도구 반환·작업 기록에 0회(테스트로 고정). 게이트웨이 전송 토큰(주체별)도 같다 — 감사에는 주체 이름(`principal=`)만, 비교는 상수 시간. **[J8]** 토큰은 소스 클라이언트마다 따로이고 한 소스의 토큰이 다른 소스 요청에 실리지 않는다(목 서버 `/__mock/hits`의 Bearer 지문 `bearer_fp`로 단언 — 값은 기록하지 않는다). 거부 입력은 소스마다 HTTP 0회. 허용목록·거부 규칙은 전 소스 공통(M-10).
5. 카탈로그 사본(`testdata/jennifer/scripts/jennifer_catalog.py`) ↔ 정본 대조 테스트(템플릿·필수/선택 키·Accept · 134 W2부터 경로 변수 형식 선언 · 134 W7에서 사본 템플릿 정규식을 이름 없는 묶음으로 — 변수 둘 이상 템플릿의 묶음 이름 중복으로 import가 깨졌다).
6. 계약 테스트는 목 서버를 상대로 돌리고 끝에 `GET /__mock/hits`로 `allowlisted=false` 0건 · `query_token` 0건을 단언.
7. 마스킹 — `client_ip`·URL 쿼리 값·SQL 리터럴·이메일·휴대폰·주민번호를 반환 전에 가린다. 감사에는 마스킹본만. **[134 W0-B]** `mask_url`은 앞 구분자 없이 시작하는 첫 쌍도 가린다(COV E-20 — 종전 `mask_url('a=1&b=2')` = `a=1&b=<v>`) · HTTP query 전용 `mask_query`(첫 값까지 전부) · 식별자 `mask_identifier`(앞 1자 + `***` · 2자 이하 `***`) — 두 함수의 필드별 적용은 W1. **[134 W2 수정]** 식별자 가림·자격증명 제거를 실제로 적용한 응답은 봉투 `disclosures`에 `apm_masked_fields`(칸 이름만 · §3.1)를 싣는다. **[134 W7]** 설정 값 전용 `mask_pii`(이메일·주민번호·휴대폰만 — `mask_text`의 SQL 리터럴·URL 쿼리 규칙이 `-Dport=8080` 같은 설정을 훼손하고, 서버 IP는 인프라 정보라 가리지 않는다 · 인스턴스 목록 `ip_address`와 같은 처분) · 사용자 계정 `id`·`name` `mask_identifier` · 실행 중 요청 `userId` `mask_identifier`·`sql` `mask_sql`·`http.query` `mask_query`. **[134 W5 · 수정]** 프로파일 SQL 응답(모양 미공개 — COV E-11)은 출처 칸 이름(`SQL_STATEMENT_KEYS`)이 SQL 문 칸이면 `mask_sql`(+ PG 달러 따옴표 · `mask_pii`), 그 밖 칸(바인드 값일 수 있음)은 `mask_identifier` + `[한계]` + `apm_masked_fields` `sqls` — 키워드 판정은 저장 프로시저 호출(`{call …}`·`EXEC`)을 훼손해 쓰지 않는다(VG-1). 클라이언트 DEBUG 로그는 계정 ID가 들어가는 경로(`account` 형식)를 원값 대신 템플릿으로 남긴다.
8. **[J8] 감사** — 감사 1줄에 `sources=<소스 id>:<HTTP 호출 수>,…`(없으면 `-`)를 싣는다(도구 1회 전후 소스별 호출 수 차이 — 캐시로 호출이 없었으면 `-`). `api_calls`는 그 합이다(M-9). **[134 W0-B]** 끝에 `principal=<주체 이름>`·`job_id=<작업 ID 또는 ->`를 더한다. 데이터 도구의 `api_calls`·`sources`는 **그 작업의** 호출 수다(같은 시간에 도는 다른 작업의 호출이 섞이지 않는다 · 이 요청이 시작한 명단 적재 포함). 접수(승격) 때 1줄, 백그라운드 작업이 끝날 때 1줄(작업 수명 전체 `api_calls`·`sources` · `rows` = 전체 행 수 · 실패·취소·중단이면 `error=`), 작업 도구는 호출마다 1줄(`api_calls=0`), `gateway_health`도 요청 주체를 싣는다. 모든 칸의 제어·서식 문자는 이스케이프한다(`\n` → `\\n` — 대상·조사 ID 값으로 감사 줄을 위조하지 못하게). **[130 W1·W2]** 대상 칸은 `apm_instance_map`이면 `hostname` → `query:<검색어>` → `business:<업무명>` → `*`, 데이터·관리 도구는 `hostname or instance_name or *`이고(마스킹 같음) 정상 줄 끝에 ` search=<단계|none>(exact:n,normalized:n,prefix:n,contains:n)` 또는 ` search=business(business_map:n,domain:n,business:n,instance_text:n)`를 붙인다(§3.4.4 · 오류 줄 형식은 그대로).
9. **[134 W0-B] 자격증명 제거**(D-296 ③ · N-17) — 어댑터(`JenniferClient.get_json`·`get_text`·오류 사유)가 응답을 파싱한 **직후** `domain/credentials.py`를 지난다(새 경로도 이 두 출구를 지나므로 빠져나갈 수 없다). 오류 사유는 **가린 뒤 자른다**. 규칙:
   - **키**(NFKC + 서식·결합 문자 제거 → camelCase·`_ . - 공백 / :` 토큰 · 뒤 숫자 무시): 부분 문자열 PASSWORD·PASSWD·PASSPHRASE·SECRET·CREDENTIAL·APIKEY·ACCESSKEY·PRIVATEKEY·TOKEN·COOKIE·JSESSIONID·SESSID·JWT · 끝이 PASS·PWD·PW(`PGPASSWORD`·`dbpass`·`rootpw`) · 토큰 AUTH·AUTHORIZATION·BEARER·PRIVATE · `KEY`가 API·ACCESS·SECRET·PRIVATE·PRIV·ENCRYPT·ENCRYPTION·SIGNING·HMAC·MASTER·SSH 뒤(붙여 쓴 꼴 포함). **세션 식별자**(SESSION·SESSIONID·SID 토큰 — `ORACLE_SID` 제외)는 숫자가 아닌 값만 가린다(제니퍼 `ActiveServiceData.sessionId`는 int32 상세 조회 인자라 남는다). 계정 비밀번호 필드(`password`·`passwd`·`pwd`)는 키째 제거.
   - **구조**: dict · 이름/값 칸 묶음(이름 {key,name,k,id,label,propertyName,property} 중 하나라도 비밀이면 값 {value,val,v,values,propertyValue} 전부) · 2원소 리스트 `[이름, 값]` · 평행 배열 `{keys|names, values|vals}` · 비밀 키 아래 중첩 잎 전부 · JSON 문자열 값은 디코드해 같은 규칙을 다시(최대 8겹) · 깊이 제한 없음(반복 순회 — 32 넘는 중첩은 봉투 `limits` 메모 `[한계] 자격증명 검사: 예상 밖 응답 모양(<경로>) — …`).
   - **텍스트**: `Authorization`·`Proxy-Authorization`·`Cookie`·`Set-Cookie` 헤더는 줄 끝까지 · 비밀 키의 따옴표 없는 값은 `; & ,`·줄바꿈까지(SQL Server `{…}` 포함) · URL·JDBC 사용자 정보(비밀번호에 `/ # @`가 섞여도 마지막 `@`까지 · `@` 없는 `scheme://user:` 꼬리는 포트·경로가 아니면 통째) · Oracle `jdbc:oracle:…:user/pass@`·`sqlplus`·`expdp`… `user/pass@db` · `-D<비밀 키>=` · `--password …` · 붙은 `-p<값>`(문맥 무관 — `-pthread`·`-port` 같은 플래그도 가린다) · `sshpass -p` · `-u`/`--user 이름:비밀번호` · `Bearer …` · JSON(일반·이스케이프·작은따옴표) 속 `"<비밀 키>": 값`(숫자 포함) · XML 요소 `<password>…</password>`·`name="password" value="…"` 속성 · 명령줄 문맥(키에 COMMAND·SCRIPT·ARGS·EXEC…)에서는 띄어 쓴 `-p 값`·`user/pass@db`도.
   - **성능**: 정규식은 앞쪽 고정·길이 상한·소유 수량자로 선형(100KB 공격 문자열 1초 이내 — 회귀 테스트) · 64 KiB 넘는 텍스트는 줄 경계 조각 · 256 KiB 넘는 본문의 검사는 스레드(이벤트 루프 정체 방지) · 반환 마스킹(`mask_text`)은 길이 상한이 있으면 출력 길이의 8배(최소 4,096자)까지만 훑고, **134 W2** 행 텍스트 전문(`limit=None`)은 전체를 훑는다 — 규칙 정규식이 모두 선형이다(URL 쿼리 키 글자에서 구분자 `?`·`;`를 빼 `?a?a…` 100KB 33초 → 0.02초 · 빈 키 `?=v`도 값을 가린다 · 1MB 공격 문자열 1초 이내 — `tests/test_w2_coverage.py`).
   - 일반 설정값(`PATH`·`JAVA_HOME`·`java.vendor`·`KEYBOARD_LAYOUT`·`ORACLE_SID`)은 가리지 않는다. 부분 문자열 규칙의 과잉 가림(`passCount`·`tokenCount`)은 제니퍼 필드 어휘에 영향 없음을 테스트로 고정한다. 남긴 모양(W10 녹화본으로 판단): `token C`·`password -> C`·`password is C`·URL 인코딩 키(`pass%77ord`)·`==`·줄바꿈을 낀 `KEY\n=값`·`key=X value=Y`. 카나리아 테스트가 도구 반환·스풀 파일·감사·로그·`limits`·오류 사유 부재를 단언한다(`tests/test_credentials.py`·`test_w0b_fix_regressions.py`) — 패턴 테스트만으로 모든 비밀을 보장한다고 선언하지 않는다(운영 녹화본 대조는 W10).
