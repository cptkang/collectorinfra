# SPEC: apm-question-coverage — 제니퍼 전체 읽기 기능 활용 (`plans/134`)

> 계획서 `plans/134-WIP-jennifer-question-coverage.md` v1.2 · 전수 대응표 `spec/CAPABILITY-MAP-134.md`(COV) · 사용 사례 `docs/33_jennifer_use_cases.md` · 결정 **D-296**(범위) · **D-299**(광범위 활용·구현 계약)
> 게이트웨이 도구 계약의 정본은 `spec/SPEC-apm-gateway.md` §3이다. 이 문서는 134가 그 계약에 **더하는 것**(인자·봉투·작업·도구·보기·본체 흐름)과 Wave별 소유를 정한다. 게이트웨이 구현 Wave는 같은 작업에서 `SPEC-apm-gateway.md` §2.1·§3·§6을 맞춘다.
> 기준 커밋 `54e1597`(main) · 작성 2026-10-02 · 실 제니퍼·LLM 호출 0으로 정한 계약이다(실응답 shape는 W10).

## 0. 불변 조건 (모든 Wave)

1. **읽기만** — 허용목록은 GET + 경로 템플릿 정확 일치 + 경로별 쿼리 키(D-195 ① · D-296 ②). 쓰기·제어·시험 경로·`.xml`·POST 변형·쿼리 `token`·리다이렉트는 HTTP 0회로 거부한다. 목록을 넓혀도 통제 방식은 그대로다.
2. **자격증명 값은 어떤 출력에도 없다**(D-296 ③) — 도구 반환·스풀 파일·감사·로그·LLM 입력·채팅 답·다운로드 전부. 규칙은 §4.
3. **개인정보 원값은 G-11 확정 전 내보내지 않는다** — 현행 마스킹(`masking.py`) + §4.3 식별자 가림. 원값 경로는 G-11(미결)이다.
4. **자체 상한 없음**(D-296 ④) — 기간·대상·건수·보기 수·발췌 길이를 코드가 줄이지 않는다. 남는 한계는 제니퍼 쪽 사실(보존 기간 · X-View 1분 창 · `/api/status/*` 시 단위 · 변경 이력 25시간 · 로드된 클래스 6만 · 호출 속도)뿐이고 전부 `[한계]`/고지로 드러낸다. 큰 결과는 **화면 = 요약·앞 행 / 전체 = 결과 파일**이다(§3).
5. **패키지 경계**(D-139 · D-274) — `apm_gateway/` ↔ `src/` import 0. 계약은 MCP 도구(JSON)와 `alarm:raw`뿐이다. 제니퍼 토큰은 게이트웨이에만 있다. 공유 로컬 경로에 기대지 않는다(본체는 스풀 디렉터리를 모른다).
6. **침묵 폴백 금지** — 모르는 인자 값·선택하지 못한 기능을 다른 조회로 성공 처리하지 않는다(G-10). 실패는 데이터 0으로 세지 않는다.
7. **WAS·미들웨어는 제니퍼 단독 소스** — 폴스타를 대안·되묻기 후보로 내지 않는다. 132 계약: 보기 라벨·예문은 계획 LLM에 렌더되는 재료이고, 단어 매칭으로 소스나 보기를 고르지 않는다. LLM 선택은 코드가 검증한다(G-7 · G-10).
8. **비활성 배포 바이트 불변** — APM 엔드포인트가 없으면 처리기·분해 프롬프트·스키마가 종전과 같다(D-162 · D-251 ⑥). 활성 배포의 변경은 D-296·D-299가 확정했다.
9. **조사(`sre_agent`) 프로파일 호출 상한 5회·정체 가드는 유지**(D-296 ④) — 채팅은 그 예산을 쓰지 않는다(W5).

## 1. W0 실측 (2026-10-02 · 기준 `54e1597`)

### 1.1 계획 §2 재대조 — C-1~C-18 모두 그대로다

| # | 재대조 결과 |
|---|---|
| C-1 | `src/orchestration/apm_query.py:451` `_collect` — `rows`·`limits`만 옮긴다. `src/` 전체에 `was_signals` 소비 0 |
| C-2 | `config/db_registry.yaml:92-99` `apm.runtime`·`apm.slow_tx`에 `window_max_minutes` 없음 → `plan_window`(`apm_query.py:156`)가 `current` + 「현재값 기준입니다(기간 조회를 지원하지 않는 보기)」 |
| C-3 | `db_registry.yaml:89-91` `apm.app_health` `window_max_minutes: 10` → `tools.py:439` `window.minutes > XVIEW_MAX_MINUTES`일 때만 `_hourly` — 채팅은 도달 불가 |
| C-4 | `apm_query.py:262` `_call_args` — `hostname`·`thread_id`·창만. `src/prompts/input_parser.py:55` 규칙 4의 `limit`은 전달되지 않는다 |
| C-5·C-6 | `fields.py:134`·`:145`·`:165`·`:217` — 필드 버림은 COV 표 C(「버림」 225행)가 전수 |
| C-7·C-8·C-9 | 허용 16템플릿 중 4경로 호출 0(grep — `allowlist.py`에만) · 선택 키 미허용 · 허용 밖 경로 — COV 표 A·B |
| C-10 | `apm_gateway/tests/test_allowlist.py:31-53` — 민감 GET 「HTTP 0회 거부」 단언 |
| C-11 | `tools.py:48-58` 상수 · `:399` 목록 200 · `apm_query.py:60` `MAX_VIEWS = 3` · `:66` `_OUT_OF_WINDOW_AFTER` · `src/config.py:1216` `max_targets = 10` · `sources.py` `MAX_INSTANCES_PER_HOST`(5) · `client.py` `max_response_bytes`(4 MiB — 넘으면 `apm_api_error`) |
| C-13 | `apm_query.py:402` `_insert_instances_step` — 대상 없으면 목록 앞 `max_targets`대 |
| C-15 | `src/config.py:194` `source_call_timeout`(10초) · `:527` `query_timeout`(120초 — D-267 ⑦ 첫 답변까지) |
| C-16 | `src/orchestration/subagents.py:1913` `resolve_subagent` · `deepagents_tools.py` 고정 목록 |
| C-17 | `src/prompts/intent_planner.py` `INTENT_PLANNER_APM_SECTION` — 보기 1~2개 지시 |
| C-18 | `tests/test_routing/test_plan125_registry.py:42-48` — 보기 7종 순서 · `window_max_minutes == 10` 단언 |

### 1.2 COV가 새로 드러낸 것 (설계 반영)

- **새 GET 3경로**(v2 매뉴얼 추가 수집): `/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied` · `…/{errorType}/individual-setting/{instanceId}`(설정 없으면 404) · `/api-v2/manual-rdb-export`(수동 RDB Export 작업 상태). D-296 ① 범위 — W7 `apm_config` kind로 둔다(§5).
- **경로 변수 형식**(COV E-17): 숫자만 받는 `build_path`로는 `{errorType}`·`{targetType}`·`{id}`(계정)를 부를 수 없다 → 템플릿 변수별 형식 선언(§2.4).
- **v2 봉투**(E-18): v2 응답은 `{result: …}`가 아니다(배열·객체·불리언) · `/api/metrics`는 `result`가 객체 → 현 `result_list`는 빈 목록을 돌려준다. 경로별 파서를 둔다(빈 결과로 침묵 강등 금지).
- **마스킹 결함**(E-20 · 실행 확인): `mask_url('a=1&b=2')` → `'a=1&b=<v>'` — 앞 구분자 없는 첫 값이 남는다. HTTP query 문자열 전용 규칙이 필요하다(W1에서 고친다 — 기존 소비처에도 영향 없는 강화).
- **자격증명 규칙 부재**(E-22 · 실행 확인): `mask_text('JAVA_OPTS=-Ddb.password=secret123')`·`mask_text('DB_PW2=abc')` → 원문 그대로 → §4 신설(W0-B).
- **중복 정본**(E-24): 업무 시계열은 `apm_metrics(scope=business)`가 정본이고 `apm_business`는 업무 정의·현재값을 맡는다.
- **보기 하나 · kind 여럿**(E-25): `view_args.kind`(열거 선택지)로 둔다 — 보기를 kind마다 쪼개지 않는다.

### 1.3 MCP 진행·취소 시그니처 (설치본 `mcp` 1.29.1 · `inspect.signature`)

- 클라이언트 `ClientSession.call_tool(name, arguments=None, read_timeout_seconds: timedelta|None=None, progress_callback=None, *, meta=None)`.
- 서버 `Context.report_progress(progress, total=None, message=None)`.
- **판단**: 진행·취소를 MCP 진행 알림에 기대지 않는다. 본체 세션은 처리기 호출 1회 동안만 열리고(`open_source_session`) 진행 알림을 화면까지 나르는 배선이 없으며, 세션이 닫힌 뒤에도 작업은 이어져야 한다. → **서버 발급 작업 ID + 상태·취소·읽기 도구**(§3)로 한다. 전송에 무관하고, 조사·알람 소비자도 같은 방식으로 쓸 수 있다.

### 1.4 직전 결과 참조 · 조사 주체 전달 경로 (W5·W7 입력)

- **직전 결과 참조**: 본체는 행을 `prior_rows`·`conversation_context.previous_entities`로 다음 턴에 넘긴다(`resolve_apm_targets` — `apm_query.py:206`). `profile_ref`·`active_ref`는 행 안의 dict 칸이라 행이 넘어가면 같이 간다. 순번·지시어("그 트랜잭션")를 행 하나로 고르는 결정적 선택기는 없다 → M-6(W5).
- **조사 주체**: `sre_agent`는 게이트웨이를 HolmesGPT MCP 도구로 등록하고(`sre_agent/sre_agent/interface/mcp_service.py:128`), `investigation_id`는 **조사 LLM이 인자로 채운다**(`investigation_guidance.py:262` 지시문). 빠뜨리면 게이트웨이가 `_anonymous` 예산으로 묶는다(`tools.py:860`). 게이트웨이 전송 인증은 정적 Bearer 1개(`APM_GATEWAY_BEARER_TOKEN`)라 **호출 주체를 구별하지 못한다** → §3.6 주체 토큰(W0-B)으로 구별하고, 예산 분리는 W5.

## 2. 공통 계약 — 인자 · 봉투

### 2.1 인자 (모든 데이터 도구 · 추가만)

| 인자 | 형 | 뜻 | 기본 |
|---|---|---|---|
| `owner` | `str?` | 결과·작업 소유자(불투명 문자열 — 본체는 `user:<sub>`, 조사는 `investigation:<id>`). 작업 도구는 같은 `owner`와 같은 주체(§3.6)일 때만 응답한다 | 없음 |
| `wait_seconds` | `float?` | 이 시간 안에 끝나지 않으면 작업 핸들을 돌려주고 백그라운드로 계속한다(§3) | 없음 = 끝날 때까지 기다린다(기존 소비자 의미 유지) |
| `n` | `int?` | 상위 N(사용자가 정한 개수). 1 이상이면 상한 없음 | 도구별 종전 기본(10) 유지 |
| `full` | `bool` | 순위·상위 N 대신 **전체 행**(정렬은 유지) | `false` |

- 종전 `N_MAX`(20) 검사를 없앤다. `n < 1`만 `invalid_argument`.
- 기존 인자 이름·기본 의미는 바꾸지 않는다. 결과가 상한 제거로 늘어나는 것은 의도된 변경이다(계획 §4.6 — 「결과 바이트 동일」 수용 기준은 적용하지 않는다).

### 2.2 봉투 (정상 반환 — 추가만)

| 키 | 뜻 |
|---|---|
| `rows` · `row_count` | **화면용 행**(인라인) · 그 개수(종전 키 그대로 — `row_count = len(rows)`) |
| `total_row_count` | 전체 행 수(인라인보다 많으면 나머지는 `artifact`) |
| `artifact` | 전체 결과 파일 참조 `{job_id, total_rows, chunks: [{index, rows, bytes, sha256}], chunk_rows, columns, text_parts: [{name, bytes}]}` — 인라인보다 많거나 원문 텍스트(프로파일 전문 등)가 있을 때만 |
| `job` | 작업 핸들 `{job_id, state, progress{done,total,unit,label}, estimate{api_calls,seconds}, created_at, updated_at, expires_at}` + 실패·취소·중단이면 `error{code, reason}`(code = 소스 오류 코드 · `stalled` · `cancelled` · `interrupted`) — `wait_seconds` 안에 끝나지 않았거나 `artifact`가 있을 때 |
| `partial` | `true`면 일부 단위(소스·도메인·창 조각)가 실패했다 — 사유는 `limits`. 부분 결과를 전체 결과로 보이지 않게 소비자가 고지한다 |
| `disclosures` | 게이트웨이가 아는 사실의 구조 고지(선택) `[{kind, text}]` — 본체 `disclosures[]` 어휘(§7.5)와 같은 kind만 쓴다 |

- `job.state`가 `queued`·`running`이면 `rows`는 비고 `row_count = 0` · `total_row_count`는 없다. 데이터가 아니라 **접수**다 — 소비자는 이것을 데이터 답으로 세지 않는다.
- 오류 반환은 종전(`{"error", "reason", …}`) 그대로이고 오류 코드를 더한다: `job_not_found`(없음·남의 작업·보관 기간 경과 — 존재 여부를 드러내지 않는다) · `job_not_ready`(아직 끝나지 않은 작업의 청크 읽기).

### 2.3 인라인·청크 크기

- `APM_INLINE_ROWS`(기본 **500**) — 이보다 많으면 앞 500행만 `rows`에 싣고 전체는 `artifact`로 스풀한다. **조회는 전량**이고 이 값은 전달 형태다(조회 범위 축소 수단으로 쓰지 않는다 — 계획 §4.3 「대용량 소유·전달 경계」).
- `APM_ARTIFACT_CHUNK_ROWS`(기본 **2000**) — 청크 1개 = MCP 응답 1회 크기 제어.

### 2.4 허용목록 경로 변수 형식 (W2·W7에서 넓힌다)

`Endpoint`에 경로 변수별 형식을 선언한다: `int`(종전 숫자) · `enum[...]`(예 `targetType ∈ {domain, instance, business}`) · `token`(`[A-Z0-9_]{1,64}` — `errorType`) · `account`(`[A-Za-z0-9._@-]{1,64}` — 계정 ID). 템플릿 정규식도 변수 형식에 맞춘다. `..`·`//`·`%`·`\`·`?`·`#`·`://` 거부는 그대로이고, 형식 밖 값은 HTTP 0회로 `NotAllowedError`. 카탈로그 사본(`testdata/jennifer/scripts/jennifer_catalog.py`)도 같은 선언을 갖고 대조 테스트가 둘을 맞춘다.

## 3. 장기 작업 · 대용량 전달 (W0-B — N-14 · N-18 · M-10 · M-11)

**목적**: 상한을 걷어낸 조회(수백 도메인 · 긴 창 · 수만 행)가 MCP 호출 10초(`MCP_SOURCE_CALL_TIMEOUT`)·채팅 처리 상한(D-267 ⑦ 120초)에 끊기지 않게 한다. 짧은 조회는 종전 그대로 동기 응답이다. **사용자가 요청한 조회의 비동기 실행**이지 상시 수집이 아니다.

### 3.1 소유 경계

| 게이트웨이(`apm_gateway/application/jobs.py` · `spool.py`) | 본체(`src/`) |
|---|---|
| 원천 API 청크 수집 · 마스킹 · 분석 · 스풀 · 작업 상태 머신 · 속도·동시 실행 · 재기동 처리 · 보관 만료 | 사용자 작업 장부(소유자 = 사용자 `sub`) · 인가 · 진행 UI · 취소 버튼 · 다운로드 전달(CSV 변환 · D-262 마스킹 · 감사) |

본체는 스풀 경로를 모른다. 둘 사이는 작업 ID와 작업 도구 3종뿐이다. LLM은 작업 ID·파일 경로·다운로드 URL을 만들지 않는다(본체가 서버에서 붙인다).

### 3.2 실행 모델

- 모든 데이터 도구 호출은 게이트웨이 안에서 **작업**으로 돈다(`JobManager.execute`). `wait_seconds` 안에 끝나면 결과 봉투를 바로 돌려주고(인라인 초과분은 스풀 → `artifact`), 못 끝나면 작업 핸들(`job.state = running`)을 돌려주고 같은 코루틴을 백그라운드에서 계속한다(**승격**). 끝나면 결과 봉투(행 제외)를 `result_meta`로, 행을 청크 파일로 스풀한다.
- `wait_seconds`가 없으면 끝날 때까지 기다린다(종전 소비자 — 조사·알람). 그때도 인라인 초과분은 `artifact`로 간다.
- 짧은 동기 작업은 스풀할 것이 없으면 기록을 남기지 않는다(디스크 0).
- 승격 전에 호출자가 끊기거나 도구 호출이 취소되면 작업도 취소한다(아무도 모르는 작업이 남지 않게). 승격 뒤에는 호출자 세션과 무관하게 계속한다(백그라운드 태스크는 요청 스코프 밖에서 만들고 강참조로 잡는다).

### 3.3 상태 머신

`queued → running → completed | partial | failed | cancelled` · `queued|running → interrupted`(재기동) · `running → failed`(정체 — 사유 `stalled`) · 보관 만료 뒤 기록 삭제(조회 = `job_not_found`).

- `partial` = 결과 봉투의 `partial: true`(일부 단위 실패). 행은 있으나 전체가 아니다.
- 작업 기록 = `<APM_SPOOL_DIR>/<job_id>/job.json`(원자적 쓰기: 임시 파일 → rename) · 청크 `rows-00000.jsonl`… · 텍스트 `text-<part>.txt`.
- 기록 칸: `job_id`(uuid4 hex — 서버 발급) · `tool` · `principal` · `owner` · `target`(마스킹된 대상 요약) · `state` · `created_at`·`started_at`·`updated_at`·`finished_at`·`expires_at` · `progress{done,total,unit,label}` · `estimate{api_calls,seconds}` · `api_calls`(실제) · `result_meta`(행 뺀 봉투) · `artifact{total_rows, chunks[{index,rows,bytes,sha256}], chunk_rows, columns, text_parts[{name,bytes}]}` · `limits[]` · `error{code,reason}`. 인자 원문·토큰·자격증명은 싣지 않는다.

### 3.4 속도 · 동시 실행 · 우선순위 (N-14)

- 소스별 호출 속도 `JENNIFER_RATE_LIMIT_PER_SEC`(기본 5 — G-12 협의 전 · D-296 ⑤)는 그대로다. 같은 토큰을 폴러·동기 호출·백그라운드 작업이 나눈다.
- **우선순위 속도 제어**: 소스 클라이언트의 throttle이 대기열을 우선순위로 판다 — `poller`(폴러) > `interactive`(승격 전 호출) > `background`(승격된 작업). 기아 방지: 대기 `APM_PRIORITY_AGING_SECONDS`(기본 10초)를 넘긴 요청은 한 단계 올린다. 우선순위는 호출 시점의 컨텍스트 값이다(승격되면 이후 호출부터 background).
- **동시 실행**: 승격된 작업의 동시 실행 수 `APM_JOB_MAX_CONCURRENT`(기본 4). 넘는 작업은 다음 API 호출 앞에서 슬롯을 기다린다(`state = queued` · FIFO). 동시 실행 수는 메모리·공정성 수단이고 총 조회량을 자르지 않는다.
- **비용 예측**: 도구가 호출 계획(예: X-View 1분 조각 수 × (소스, 도메인) 묶음 · 도메인 수)을 알면 `progress.total`·`estimate.api_calls`를 채우고 `estimate.seconds = api_calls / 속도`로 둔다. 모르면 `total = null`(호출 수만 센다).
- 호출마다 작업 기록의 `api_calls`·`updated_at`(임대 갱신)을 올린다.

### 3.5 취소 · 정체 · 재기동 · 만료

- **취소**: `apm_job_cancel` → 코루틴 취소 전파 → `cancelled`. 스풀된 조각은 지운다(취소한 결과를 전체로 오인하지 않게). 끝난 작업의 취소는 상태를 바꾸지 않고 현재 상태를 돌려준다.
- **정체**: 감시 루프가 `running` 작업의 `updated_at`이 `APM_JOB_STALL_SECONDS`(기본 300초) 넘게 멈추면 취소하고 `failed`(`error.code = stalled`). 제니퍼 호출 1회 상한이 10초라 정상 작업은 걸리지 않는다.
- **재기동**: 기동 시 스풀을 훑어 `queued`·`running` 기록을 `interrupted`(사유 「게이트웨이 재기동으로 중단 — 다시 요청해야 합니다」)로 바꾼다. 끝난 작업은 만료 전까지 그대로 읽힌다. (단위별 재개는 하지 않는다 — 다시 요청한다. 전수 순위·폴러 버퍼처럼 단위가 있는 작업은 W3에서 실패 단위만 다시 부르는 재조회를 더한다.)
- **만료**: `APM_ARTIFACT_RETENTION_SECONDS`(기본 **86400** — 24시간) 뒤 기록·청크를 지운다. 만료된 작업 조회는 `job_not_found`(사유에 보관 기간). 보관 기간은 조회 범위 축소 수단이 아니다.
- **단일 큰 응답**(N-18): 클라이언트는 응답 본문을 `JENNIFER_MAX_RESPONSE_BYTES`(기본 4 MiB — 의미 변경: **메모리 임계**)까지 메모리에, 넘으면 스풀 디렉터리의 임시 파일로 받는다(**오류로 끊지 않는다** — D-296 ④). `{"result": [...]}` 배열은 파일에서 원소 단위로 점진 디코드(표준 라이브러리 `json.JSONDecoder.raw_decode`)해 원문 전체를 메모리에 두지 않는다. 임시 파일은 파싱 뒤 지운다.

### 3.6 호출 주체 (M-11 · W5 예산 분리의 기반)

- `APM_GATEWAY_BEARER_TOKENS`(JSON 객체 `{"<principal>": "<token>"}` · 선택)로 소비자별 토큰을 둔다(예 `chat`·`investigation`·`alarm`). 종전 단일 `APM_GATEWAY_BEARER_TOKEN`은 주체 `default`다. 둘 다 비면 무인증(로컬 — 주체 `anonymous`). 같은 토큰을 두 주체에 주면 기동 실패(모호).
- 전송 미들웨어가 요청 토큰으로 주체를 정해 컨텍스트에 싣는다. 작업 기록에 `principal`을 남기고 작업 도구는 **같은 주체 + 같은 `owner`**일 때만 응답한다(아니면 `job_not_found`).
- 감사 1줄에 `principal=`·`job_id=`를 더한다(토큰 값 없음).
- 이 결정은 인가를 넓히지 않는다 — 게이트웨이 도구 접근은 여전히 Bearer 보유자 전원이다. 사용자별 인가는 본체(`allowed_sources` · 작업 장부 소유자)가 한다.

### 3.7 작업 도구 (MCP · W0-B)

| 도구 | 인자 | 반환 |
|---|---|---|
| `apm_job_status` | `job_id` · `owner?` | 봉투 + `job`(상태·진행·예측) + 끝났으면 `result_meta`(행 뺀 결과 봉투 — `summary`·`hourly`·`was_signals`·`limits`·`window`·`sources`…) · `artifact` · `rows`(**앞 `APM_INLINE_ROWS`행 미리보기**) |
| `apm_job_cancel` | `job_id` · `owner?` | 봉투 + `job`(취소 뒤 상태) |
| `apm_job_read` | `job_id` · `owner?` · `chunk?: int` · `part?: str` | `chunk` → `rows`(그 청크 전부) + `artifact` · `part` → `text`(텍스트 부분 전문 — 마스킹본) |

- 아직 끝나지 않은 작업의 `apm_job_read`는 `job_not_ready`. `chunk`·`part`가 둘 다 없으면 청크 0 · `chunk` 범위 밖·모르는 `part`·둘 다 지정·결과 없는 끝난 작업은 `invalid_argument`. 실패 사유에 스풀 경로를 싣지 않는다.
- 세 도구는 자기 감사 1줄을 남기고(`api_calls=0`), 제니퍼를 부르지 않는다.
- 구현 세부(W0-B 확정): `wait_seconds`는 0 이상의 유한수(NaN·무한대 거부) · `owner` ≤ 200자 · 완료 데이터 봉투에는 `total_row_count`가 항상 있다 · 에이징은 대기 `APM_PRIORITY_AGING_SECONDS`마다 한 단계씩 반복 · `APM_SPOOL_DIR` 상대 경로는 게이트웨이 루트 기준 · 스풀 디렉터리 0700·파일 0600 · 공유 인스턴스 명단 적재는 슬롯 밖(interactive)에서 돌고 호출 수는 시작한 요청에만 센다 · 동기 감사의 `api_calls`·`sources`는 그 작업의 값 · SIGTERM 정상 종료는 진행 중 작업을 `interrupted`로 기록하고 종료 감사를 남긴다.

### 3.8 본체 — 작업 수명과 화면 (M-10 · M-11)

- `apm_query`는 데이터 도구에 `owner = "user:<sub>"`(인증 꺼짐이면 `user:anonymous`)와 `wait_seconds = max(1, min(source_call_timeout − 2, 조회 마감까지 남은 시간))`을 넘긴다(`src/utils/deadline.py` — 묶이지 않은 컨텍스트는 `source_call_timeout − 2`). 호출 상한(`source_call_timeout`)보다 항상 짧게 둬 MCP 10초에 끊기지 않게 한다.
- 작업 핸들이 오면 본체 **작업 장부**에 등록한다(`job_id` · 소유자 `sub` · `thread_id` · 보기 · 대상 · 생성 시각 · 게이트웨이 상태 캐시). 장부는 Redis(키 TTL = 보관 기간) — Redis가 없으면 프로세스 메모리(재기동 시 사라짐을 고지).
- 처리 마감 안에서는 `apm_job_status`를 짧은 간격으로 다시 본다. 처리 마감 전에 끝나면 미리보기 행·`result_meta`로 종전처럼 답한다(전체 행이 인라인을 넘으면 결과 파일 고지). 끝나지 않으면 **접수 답**으로 끝낸다 — 「오래 걸리는 조회라 작업으로 실행 중입니다(범위 · 예상 시간 · 진행). 끝나면 작업 카드에서 결과를 보고 내려받을 수 있습니다.」 + 고지 kind `apm_job_accepted`(§7.5) + 작업 참조. 접수 답은 데이터 답이 아니다 — `source_status.status = "accepted"`, 하네스도 완료로 세지 않는다.
- **API**(인증 필수 · 소유자 또는 관리자 — D-262와 같은 판정 · 다운로드 감사 `_audit_file_download` 동형):
  - `GET  /api/v1/apm/jobs` — 내 작업 목록(장부)
  - `GET  /api/v1/apm/jobs/{job_id}` — 상태·진행·예측·미리보기(`DataMasker`로 가림)·전체 행 수·`limits`
  - `POST /api/v1/apm/jobs/{job_id}/cancel`
  - `GET  /api/v1/apm/jobs/{job_id}/download?format=csv|jsonl|txt` — 청크를 차례로 받아 흘려보낸다(전량을 메모리에 모으지 않는다 · CSV는 화면과 같은 `DataMasker` · BOM · RFC 5987 파일명). `completed`·`partial`만 받는다(그 밖 409 + 상태 사유). `partial`은 파일 이름 접미 `_partial`과 응답 헤더 `X-Apm-Job-State: partial`로 전체가 아님을 드러낸다.
  - 장부에 없는 작업·남의 작업 = 404/403(소유자 확인 → 게이트웨이 호출 순서). 게이트웨이 `owner`도 다시 맞춘다(이중 확인).
- **화면**: 응답의 `disclosures[]` 중 작업 참조가 있는 항목을 **작업 카드**로 렌더한다 — 상태·진행 막대·예상 시간·취소 버튼·완료 시 「결과 보기」(미리보기 표)·「전체 결과 받기(CSV)」. 카드는 상태 API를 폴링한다(끝나면 멈춤). 접수/진행 카드는 「완료」로 표시하지 않는다.
- 장기 작업은 D-267 일반 요청 마감과 분리된 수명이다(D-299 ④) — 일반 요청의 처리 상한을 올리지 않는다.
- 구현 세부(W0-B 확정): 재확인은 **조회 마감**(처리 마감 − 서술 예약 · D-267 ⑥)까지(마감이 묶이지 않은 컨텍스트는 `query_timeout − answer_reserve_sec`) · 재확인 호출 상한도 남은 조회 마감 · 대상 선정용 첫 홉(인스턴스 목록)이 승격돼 마감을 넘기면 그 작업을 취소하고 실패 사유를 남긴다(사용자가 맡긴 조회가 아니라 장부에 올리지 않는다) · 행과 접수가 섞인 결과는 데이터 답 + `source_status = partial` + 접수 고지 · 게이트웨이 `partial: true`도 `source_status`·조사 감사 outcome이 partial · 다운로드 감사는 완료·중단 모두(실제로 보낸 바이트) · 내보내기는 중첩 값의 민감 키·값도 가린다 · 재계획기는 접수 task를 종결로 본다 · 작업 카드는 대화를 다시 불러와도 복원된다.

## 4. 자격증명 제거 · 개인정보 가림 (N-17 · W0-B 공통 경계 → W7 확장)

### 4.1 위치

**원본 외부 응답을 받은 직후, 출력·로그·스풀·감사를 만들기 전**에 한 함수(`apm_gateway/domain/credentials.py` `scrub` — 표준 라이브러리만 쓰는 순수 함수라 domain 계층)를 지난다. 어댑터가 JSON·텍스트 응답을 파싱한 직후(`JenniferClient`/`JenniferApi` — 모든 경로 공통) 적용해 이후 계층은 가린 값만 본다. 새 경로를 더해도 이 경계를 빠져나갈 수 없게 한 곳에 둔다. 자유 텍스트 마스킹(`mask_text`)은 그 뒤 단계다.

### 4.2 규칙 (값만 가린다 · 키 이름은 남긴다 · 일반 설정값은 가리지 않는다)

| 대상 | 처리 |
|---|---|
| 계정 객체의 비밀번호 필드(`password`·`passwd`·`pwd` — 대소문자 무시) | **키째 제거**(D-296 ③ 「사용자 password 필드는 제거」) |
| 키-값 묶음 — dict · 이름 칸 {`key`·`name`·`k`·`id`·`label`·`propertyName`·`property`} + 값 칸 {`value`·`val`·`v`·`values`·`propertyValue`} · 2원소 리스트 `[이름, 값]` · 평행 배열 `{keys|names, values|vals}` · `KEY=VALUE`·`KEY: VALUE` 문자열에서 **이름 칸 하나라도 비밀 패턴**인 항목 | 값 → `[가림]`(비밀 키 아래 중첩 잎 전부 · 따옴표 없는 값은 `; & ,`·줄바꿈까지) |
| 비밀 패턴(키) | 정규화(NFKD → 서식·결합 문자 제거 → NFC) 뒤 camelCase·구분자(`_ . - 공백 / :`)로 나눠 대문자화한 토큰 중 ① 부분 문자열 `PASSWORD`·`PASSWD`·`PASSPHRASE`·`SECRET`·`CREDENTIAL`·`APIKEY`·`ACCESSKEY`·`PRIVATEKEY`·`TOKEN`·`COOKIE`·`JSESSIONID`·`SESSID`·`JWT`을 포함(붙여 쓴 `PGPASSWORD`·`DBPASSWORD`) ② 끝이 `PASS`·`PWD`·`PW`(뒤 숫자 무시 — `DB_PW2`·`rootpw`) ③ `AUTH`·`AUTHORIZATION`·`BEARER`·`PRIVATE` ④ 토큰 `KEY`가 `API`·`ACCESS`·`SECRET`·`PRIVATE`·`ENCRYPT(ION)`·`SIGNING`·`HMAC`·`MASTER`·`SSH`·`PRIV` 뒤(`API_KEY`·`sshKey`) ⑤ `SESSION`·`SESSIONID`·`SID`는 **숫자가 아닌 값만**(제니퍼 `ActiveServiceData.sessionId`는 정수 에이전트 세션 ID로 F-15 필수 인자다 — 보존 · `ORACLE_SID` 제외). 일반 단어(`KEYBOARD`·`MONKEY`·`PATH`·`JAVA_HOME`·`java.vendor`)는 아님. 과잉 가림(`passCount`·`tokenCount`·`bypass`·`PWD` 디렉터리 변수)은 의도된 쪽이다 — 제니퍼 현행 필드 어휘 무영향은 테스트로 고정 |
| 값 안의 자격증명(어느 키든) | URL·JDBC 사용자 정보(`scheme://user:pass@host` — 비밀번호는 **마지막 `@`까지**, `@` 없는 `scheme://user:` 꼬리는 포트·경로가 아니면 통째) · Oracle `user/pw@db` · 쿼리/속성 `password=…`·`;Password=…;`·SQL Server `{…}` · JVM `-D<비밀 키>=…` · 헤더 줄 `Authorization`·`Proxy-Authorization`·`Cookie`·`Set-Cookie`(**줄 끝까지** — Basic·Digest·NTLM·Token·Bearer) · CLI(`--password …`·`--password=…` · 붙은 `-p<값>` · `sshpass -p` · `-u/--user 이름:비밀` · Oracle 도구 `sqlplus`·`expdp` 등의 `user/pw`) · 명령 문맥(키에 COMMAND·SCRIPT·ARGS·EXEC)에서만 띄어 쓴 `-p 값`·일반 `user/pw@db` · JSON 문자열 안 JSON(재귀 디코드 최대 8겹)·XML 요소/속성 |
| 성능·순회 | 정규식은 앞쪽 고정·길이 상한(제곱 시간 금지 — 100KB 공격 문자열 1초 이내 회귀) · 큰 본문(256 KiB 초과) 검사는 이벤트 루프 밖 스레드 · 순회는 명시 스택(깊이와 무관하게 같은 규칙) |
| 오류 사유 | **가린 뒤 자른다**(제니퍼 오류 본문 → `scrub_text` → 240자) — `sources[].reason`·`result_meta`도 같다 |
| 처리하지 못한 모양(깊이 32 초과 등) | 같은 규칙을 적용하고 봉투 `limits`에 `[한계] 자격증명 검사: 예상 밖 응답 모양(<경로>) — 깊이 32 넘는 중첩도 같은 규칙으로 검사했다(응답 모양 확인 필요)` |
| 남긴 모양(W10 녹화본으로 판단) | `token C`·`api_key C`(구분자 없음) · `password -> C`·`password is C` · URL 인코딩·HTML 엔티티 변형 · 명령 문맥 밖 띄어 쓴 `-p 값`(`ssh -p 22`와 구분 불가) |

- **카나리아 테스트**(필수): 중첩 dict/list · `SYSTEM`/`JAVA` 묶음 · 키-값 배열 · `KEY=VALUE` 자유 텍스트 · JDBC URL · `-Ddb.password=` · `DB_PW2` · `autoScriptCommand` 인자 · `password` 필드 · 대소문자 변형. 카나리아 값이 도구 반환·스풀 파일·감사 로그·`limits`·오류 사유 어디에도 없음을 단언한다. 일반 설정값(`PATH`·`JAVA_HOME`·`java.vendor`)은 그대로임을 함께 단언한다.
- 패턴 테스트만으로 모든 비밀을 보장했다고 선언하지 않는다(계획 §9) — 운영 마스킹 녹화본 대조는 W10.

### 4.3 개인정보 식별자 (G-11 미결 동안)

`mask_text`의 이메일·휴대폰·주민번호·IP 규칙에 더해, 식별자 필드(`userId`·`clientId`·계정 `id`·사람 `name`)는 `mask_identifier`(앞 1자 + `***` · 2자 이하는 `***`)로 가린다. HTTP query 문자열(`http.query`)은 전용 규칙으로 **첫 값까지** 가린다(E-20 교정). SQL은 `mask_sql`(리터럴 `?`). 원값 표시 경로는 G-11 결정 뒤 별도 개정이다(D-262 · D-299 ⑦).

## 5. 도구 표면 (목표 — Wave별)

D-195 ①의 8종 상한을 D-299 ③이 폐지했다. 기능 응집으로 묶고, API마다 도구를 만들지 않는다.

| 도구 | Wave | 뒷단 | 인자(추가분) | 요지 |
|---|---|---|---|---|
| `apm_instance_map` | W1 | `/api/domain`·`/api/instance` | — | 목록 200 상한 제거(인라인 초과 → `artifact`) · `description`·`configFilePath`·도메인 `description` 보존 |
| `apm_app_health` | W1 | realtime·X-View·status/application | — | 방문·호출 수(`visit_day`·`visit_hour`·`hit_day`·`hit_hour` — 단위·하루 경계 미확인 고지) · 액티브 구간 4칸 · X-View 10분 상한 제거(긴 창은 작업) · 시 단위 합계는 **전체 애플리케이션**에서(종전 `max_row=20` 합계 누락 교정) |
| `apm_runtime_health` | W1·W2 | realtime·dbmetrics/instance | W2 `metrics?: list[str]` · `interval_minute?` | 추세 인스턴스 2개 상한 제거 · W2 지표 카탈로그 전체(기본 3종 유지) |
| `apm_resource_pool` | — | realtime·activeService | — | 변경 없음 |
| `apm_slow_transactions` | W1 | X-View·status/application | `full?` | `n` 상한 제거 · 10분 상한 제거 · `guid`·`client_ip`(마스킹)·`user_id`·`client_id`(식별자 가림)·`start_time_ms`·SQL/fetch/외부 호출 건수 보존 |
| `apm_active_services` | W1 | activeService/list | `full?` | `session_id`·`thread_hash`·`active_ref{source_id, domain_id, txid, session_id, thread_hash}`(F-15 입력) · CPU·SQL·fetch 건수 · `status_message`(마스킹) |
| `apm_events` | W1 | dbsearch/event·error | `level_mode?: min\|exact` · `error_type?` · `record?: event\|error` · `full?`·`n?` | 24시간·50건 상한 제거(`n` 기본 = **전부** — 종전 50은 상한이었다) · `level`은 게이트웨이 계약 값 fatal·warning·normal · `exact`는 API `level`(대문자) + 재검증 · `error_type`은 정규화 이름 먼저, 0건이면 접두 변형(`ERROR_`·`WARNING_`)을 차례로 다시 묻고 맞은 표기를 `[한계]`에(U-13 · W10) · 이벤트도 같은 유형으로 거른다 · `record=error`면 행 = 오류 기록 · `errors_by_type` = 전 유형 |
| `apm_transaction_profile` | W1·W5 | txid·profile.txt·sql | — | 발췌(앞 60줄)는 화면용으로 남고 발췌가 잘렸으면(`profile_truncated`) **전문은 `artifact.text_parts["profile"]`**(마스킹본) · SQL 전부(`top_k` 비우면 전부) · W5 예산 분리 |
| `apm_status_stats` | W2 | `/api/status/{application,sql,external_call}` | `kind` · `hostname`·`instance_id?` · 구간(기본 60분) · `sort_by?` · `n?`·`full?` · `application_name?` | 시 경계 고지 · `name` 마스킹(SQL·URL) · 도메인별 `max_row=n` 뒤 전역 재정렬(정렬 기준 대응을 모르면 「전역 순위 아님」 `[한계]`) · `summary` 평균 = Σ`total_response_ms` ÷ Σ`calls`(원자료 칸은 행에 남김 — W6 가중 평균 입력) · 표시 행 기준 합계면 그 사실을 `[한계]`에 |
| `apm_metrics` | W2(instance)·W3(domain)·W4(business) | `/api/metrics` · `/api/dbmetrics/{instance,domain,business}` | `mode=catalog\|series` · `scope` · `metrics` · `interval_minute?`(기본 5) · 대상 · 구간(기본 60분) | 카탈로그 = 소스별 전 지표 군 행 `{source_id, scope, metric}`(`externalCall` → `external_call` · TTL `APM_METRIC_CATALOG_TTL_SECONDS` · 지문 변경 감지 · 모양이 다르면 오류) · 시계열 정본(업무 포함 — E-24) · 긴 형식 행 · 모르는 지표 = 후보 ≤3 + `invalid_argument` · 카탈로그를 못 읽은 소스는 검증 없이 조회하고 `[한계]`·partial |
| `apm_source_changes` | W2 | `/api-v2/deploy/{domainId}` | 대상 · 구간(기본 24시간) | 25시간 조각 · v2 맨 배열 전용 파서 · 겹침 제거 · 원시 시각 `change_detected_ms` 보존 · 「변경 감지(데이터 서버 인지 시각) — 배포 확정 아님」 |
| `apm_service_status` | W3 | `/api/realtime/domain` | `service?` · `source_ids?` | 도메인(서비스) 현재값 전부 · 서비스 해석은 130 검색기 |
| `apm_fleet` | W3 | realtime/instance 전 도메인 · dbsearch/event 전 도메인(+ 폴러 버퍼 N-11) | `mode=ranking\|events` · `metric`·`order`·`n`·`full` · `level`… | 전 대상 수집 뒤 정렬 · 실패 대상 있으면 `partial` + 「잠정 순위」 |
| `apm_business` | W4 | `/api/business` · `/api/realtime/business` | 업무 · 도메인 | 업무 정의·현재값 · 시계열은 `apm_metrics(scope=business)` |
| `apm_transaction_trace` | W5 | `/api/transaction/guid` | `guid` · 소스/도메인? · 구간 | 허용된 전 소스·도메인 · 시각순·중복 제거·부분 실패 고지 |
| `apm_config` | W7 | 룰(error·metric·compare·applied·individual-setting) · 색상 경계 · 프로세스→인스턴스 · 데이터 서버(domains·resource·system-property-config) · DB 경로 · 로드된 클래스 · 수동 RDB Export 상태 | `kind` · 대상 · `search?`·`process_id?`… | 자격증명 제거(§4) 통과 · `compare`/`comparing` 원천 불일치는 W7 착수 때 확정(COV E-01) |
| `apm_environment` · `apm_users` · `apm_active_detail` | W7 | environment-variable · auth/userlist·restapi/users·user/{id} · active-service/detail | 대상 · 계정 ID · `active_ref` | §4 자격증명 제거 · §4.3 식별자 가림 · 조사에서도 노출(일괄 비노출 금지 — D-299 ③) |
| `apm_job_status`·`apm_job_cancel`·`apm_job_read` | W0-B | — | §3.7 | 작업 관리 |
| `gateway_health` | — | — | — | `allowlist_size` 값이 늘어난다 |

## 6. 채팅 보기 (레지스트리 `solutions[apm].views`)

### 6.1 `ViewSpec` 확장 (W1)

```
ViewSpec(id, label, capability, tool, required_input, first_hop, limit,      # 종전
         window: "current" | "range" | "hourly" | "none" = "current",      # 창 의미(상한 아님 — M-2)
         fixed_args: Mapping[str, Any] = {},                               # 도구 고정 인자(예 kind)
         args: tuple[ViewArgSpec, ...] = (),                               # 허용 view_args
         examples: tuple[str, ...] = ())                                   # 계획 LLM에 렌더하는 예문
ViewArgSpec(name, type: "int"|"bool"|"enum"|"str"|"text"|"str_list"|"catalog",
            choices: tuple[str, ...] = (), min: int|None, catalog: str|None,   # catalog = 지표 군 이름
            tool_arg: str|None, label: str = "")                             # 도구 인자 이름 · 계획 LLM용 설명
# W2 확장: ViewSpec.notices(보기가 늘 붙이는 고지 kind — KIND_TABLE 대조) ·
#          CapabilitySpec.active_only(소유 시스템이 활성일 때만 분해 영역 카탈로그에 렌더 — 비활성 바이트 불변)
#          `text` = 1~200자 · 유니코드 Cc·Cf·Zl·Zp 문자 거부(URL 이름 등 식별자 형식이 아닌 값)
#          식별자 형식(`str`) = `[A-Za-z][A-Za-z0-9_]{0,63}`(게이트웨이 형식의 부분집합 — 본체 통과값이 게이트웨이에서 거부되지 않게)
```

- `window_max_minutes`는 **창 상한 의미를 폐지**한다(M-2). 레지스트리에서 지우고 `window`로 바꾼다. 소비처·테스트(C-18)를 같이 고친다.
- 레지스트리 YAML 형식은 위 칸을 그대로 쓴다(`args: [{name: n, type: int, min: 1}, …]`). 벤더 중립 어휘만(D-274 ③).

### 6.2 보기 목록 (출발 25 — 계획 §4.2)

| Wave | 보기 | 도구(+고정 인자) | 창 | 대상 | view_args |
|---|---|---|---|---|---|
| 현행 | `apm.instances` | `apm_instance_map` | none | 없음(첫 홉) | — |
| W1 | `apm.app_health` | `apm_app_health` | range | hostname | — |
| W1 | `apm.runtime` | `apm_runtime_health` | range | hostname | W2 `metrics`(catalog=instance) · `interval_minute` |
| 현행 | `apm.pool` | `apm_resource_pool` | current | hostname | — |
| W1 | `apm.active` | `apm_active_services` | current | hostname | `n` · `full` |
| W1 | `apm.slow_tx` | `apm_slow_transactions` | range | hostname | `n` · `full` |
| W1 | `apm.events` | `apm_events` | range | hostname | `level` · `level_mode` · `error_type` · `record` · `n` · `full` |
| W2 | `apm.app_stats` | `apm_status_stats`(kind=application) | hourly | hostname | `sort_by` · `n` · `full` · `application_name` |
| W2 | `apm.sql_stats` | `apm_status_stats`(kind=sql) | hourly | hostname | `sort_by` · `n` · `full` |
| W2 | `apm.external_stats` | `apm_status_stats`(kind=external_call) | hourly | hostname | `sort_by` · `n` · `full` |
| W2 | `apm.metrics` | `apm_metrics`(mode=catalog) | none | 없음 | `scope` |
| W2 | `apm.changes` | `apm_source_changes` | range | hostname | — |
| W3 | `apm.service` · `apm.ranking` · `apm.fleet_events` | `apm_service_status` · `apm_fleet`(mode=ranking) · `apm_fleet`(mode=events) | current/range | 없음(전체) | W3 SPEC |
| W4 | `apm.business` | `apm_business` | current/range | 업무 | W4 SPEC |
| W5 | `apm.profile` · `apm.trace` | `apm_transaction_profile` · `apm_transaction_trace` | — | 참조(`profile_ref` · `guid`) | W5 SPEC |
| W7 | `apm.event_rules` · `apm.process` · `apm.jennifer_server` · `apm.loaded_classes` · `apm.environment` · `apm.users` · `apm.active_detail` | `apm_config`(kind …) · `apm_environment` · `apm_users` · `apm_active_detail` | none | 도메인·인스턴스·참조 | `kind` 선택지(E-25) · W7 SPEC |

### 6.3 view_args 검증 (M-3)

- 분해 task JSON에 `view_args: {"<보기 id>": {"<이름>": 값}}`을 더한다(활성 배포의 APM 절에서만 렌더 — 비활성 바이트 불변).
- 코드가 `ViewArgSpec`으로 형·범위·선택지·카탈로그를 검증한다. **모르는 이름·형식 밖 값은 버리고 `apm_unresolved_condition`(의무) 고지를 남긴 채 보기는 조회한다** — 선택 조건이 무효라고 정상 조회를 막지 않는다(W1 검증 H-2). `null`은 「미지정」(무고지). 보기를 조회하지 않는 것은 그 보기의 **필수** 조건이 무효일 때만이다(W1·W2 보기에는 필수 조건이 없다). 보기 id 없는 평면 `view_args`는 task 보기가 하나면 그 보기 것으로, 여럿이면 버리고 고지한다.
- APM 활성 렌더에서는 분해 「출력 형식」 골격 task 줄에 `views`·`view_args`가 들어가고 「`apm_query` task에는 반드시 적는다 · 기간·시간은 view_args가 아니다」 규칙이 붙는다(W1 검증 H-1 — 로컬 9B는 골격에 없는 키를 내지 않았다: 0/15 → 실험 14/15).
- 입력 파서 `limit`(규칙 4)은 해당 보기에 `n`이 없을 때 `n`으로 옮긴다(C-4) — **단일 task 계획에서만**(파서 `limit`은 질의 전체 값이라 복합 계획에서는 다른 task 몫이 섞인다 · 복합 계획은 계획 LLM이 그 task에 낸 `n`만). 「전체·모두」를 명시한 목록 요청은 `full: true`(계획 LLM이 낸 값 — 단어 매칭 아님).
- `sort_by`·`interval_minute` 값은 원천 허용값이 미공개라(COV E-05·E-06) **임의 소수 enum으로 줄이지 않는다** — 식별자 형식(`[A-Za-z][A-Za-z0-9_]{0,63}`)·양의 정수로 검증한다. 원천·게이트웨이가 선택 조건을 거부하면 보기 전체를 실패시키지 않는다(W2 검증 B2): 모르는 지표는 빼고 조회(런타임 전부 모름 = 기본 추세 · 시계열 전부 모름만 `invalid_argument`) · 정렬 기준 거부(`apm_api_error`)는 그 조건·`max_row` 없이 다시 받아 로컬 정렬(snake↔camel 표기 변환만 · 뜻 추측 금지) — 어느 쪽이든 `[한계]` + 봉투 고지 `apm_unresolved_condition`(의무 → 답 본문). 보기 표의 `sort_by` 예시는 원천 응답 필드 이름(`calls`·`failures`·`responseTime`·`maxResponseTime`·`badResponses` · 미지정 = calls). `level`은 게이트웨이 계약이 받는 값(fatal·warning·normal — 대소문자 무시)으로 검증한다(원천 값이 아니라 게이트웨이 도구 계약이라 enum이 맞다 · W1 검증 L-4).

### 6.4 보기 선택 재시도 · 미해결 (M-8 · G-10 · W2부터)

1. 계획 LLM이 낸 `views`(닫힌 어휘로 거른 뒤)와 task `areas`(D-295)를 대조한다 — 요청 영역(APM 소유 capability)을 고른 보기들의 capability가 덮지 못하거나 `views`가 비었는데 영역이 일반 현황(`was_performance`·`was_instance`)이 아니면 **선택 재시도 1회**: APM 보기 카탈로그(라벨·예문·view_args 형식)만 담은 짧은 선택 프롬프트로 같은 LLM을 1회 부른다(D-299 ⑤ — 이 재시도만 「추가 LLM 0」 예외).
2. 재시도 뒤에도 덮지 못한 영역은 조회하지 않고 **필요한 선택만 묻는다**(텍스트 되묻기 — 후보 보기 ≤3 라벨) · `apm_unresolved_condition`. 일부 영역만 덮었으면 **덮은 보기는 조회**하고 못 덮은 영역만 되묻는다(정상 답을 버리지 않는다). 명시한 기능(환경변수·SQL 통계 등)을 응답시간·목록 보기로 바꾸지 않는다. 후보는 영역마다 최소 1개(합계 ≤3 · 영역이 3개를 넘으면 앞 3개 영역 1개씩). 계획 보기와 재시도 보기가 같은 보기로 모이면(합의) 영역 라벨이 그 보기를 덮지 않아도 그 보기를 조회하고 되묻지 않는다(W2 검증 B7 — 두 LLM이 함께 틀릴 위험은 `result=agreed` 비율로 측정). 메타 `apm_query.selection` = `areas`·`planned`·`retried`·`retried_views`·`agreed`·`uncovered`·`latency_ms`·`result`(planned|default|retried|partial|agreed|unresolved)·`candidates`.
3. 영역 신호가 없는 일반 현황 질문의 기본 보기는 종전대로다(대상 있음 = `apm.app_health` · 없음 = `apm.instances` — D-293).
4. 재시도 횟수·지연·미해결률을 메타(`apm_query.selection`)와 로그에 남긴다 — 매 Wave MLX 측정(D-240).

## 7. 본체 흐름 (`src/orchestration/apm_query.py`)

### 7.1 집계 운반 (M-1 · W1)

`_collect`가 봉투의 `summary`·`hourly`·`errors_by_type`·`was_signals`·`window`·`sources`·`partial`·`artifact`·`job`을 **(보기, 대상)별 구조**로 `meta["aggregates"]`에 옮긴다(덮어쓰기 금지 — 같은 보기를 여러 대상에 부르면 대상마다 한 항목). `was_signals`는 `meta["was_signals"]`(중복은 `(kind, source_id, instance_id)`로 제거).
결정적 줄(`answer_lines` — 판정 `label`·`level`·`evidence` · 창 집계(호출 수·오류율·p50·p95) · 실행 중 건수 · 시 단위 합계 · 오류 유형별 건수 · 상한 없이 전부)을 요약(`organized_data.summary`)에 싣고, 2단 집계기가 task 본문 뒤에 `**판정·집계**` 블록으로 **그대로** 붙인다(LLM 산문에 맡기지 않는다 — 요약은 LLM 입력이라 그것만으로는 최종 답 포함이 보장되지 않는다). 단일·병합·단계별 경로에서 최종 답 포함을 테스트로 고정했다(1단·3단 합성 경로는 대상 밖). 긴 문자열 셀은 화면(`organized_data.rows`)에서 300자로 줄이고 전문은 저장 결과·CSV에 둔다(APM 결과 한정).

### 7.2 창 (M-2 · W1 / M-7 · W6)

- `window: range` 보기는 파서 기간을 **자르지 않고** 그대로 넘긴다(`reference_time`·`lookback_minutes`). 기간이 없으면 도구 기본(종전).
- `window: current` 보기(`apm.pool`·`apm.active`)에 기간을 말하면 「현재값 전용」 고지(`apm_current_only`). `apm.runtime`·`apm.slow_tx`는 이제 기간을 넘긴다(C-2 교정 — 오고지 제거).
- 「하루 넘게 지난 기간 = 창 밖」(`_OUT_OF_WINDOW_AFTER`) 폐지와 해상도 자동 선택은 **W6(M-7)**이다. W1~W5 동안 그 규칙은 남고, 고지 문구는 「W6 전 미지원」이 아니라 사실(「하루 넘게 지난 기간은 아직 조회하지 않습니다 — 보존 기간 확인 전」)로 둔다.

### 7.3 인자 (M-3 · W1)

§6.3. 도구 호출 인자 = 대상 + 창 + `fixed_args` + 검증된 `view_args` + `owner` + `wait_seconds`.

### 7.4 복수 보기·대상 (M-5 · W3)

`MAX_VIEWS = 3` 절단 제거 · 다건 hostname 전부 · 대상 없는 전체 보기(`apm.ranking`·`apm.fleet_events`·`apm.service`·`apm.metrics`)에는 인스턴스 앞부분 삽입 금지 · 명시 대상 미해결을 첫 홉으로 대체 금지.

### 7.5 고지 kind (M-9 · 각 Wave — `src/domain/disclosure.py` `KIND_TABLE`에 등재 · drift 테스트)

| kind | 등급 | 의무 | 범위 | 뜻 |
|---|---|---|---|---|
| `apm_job_accepted` | partial | ✔ | task | 오래 걸리는 조회를 작업으로 접수 — 데이터 답 아님(W0-B) |
| `apm_full_result_file` | neutral | ✔ | task | 화면은 앞 N행 · 전체 M행은 결과 파일(W0-B) |
| `apm_partial_sources` | partial | ✔ | task | 일부 소스·도메인·조각 실패(게이트웨이 `partial`) |
| `apm_current_only` | neutral | — | task | 기간을 말했지만 현재값만 있는 보기 |
| `apm_hourly_resolution` | neutral | — | task | 시 단위 통계 — 요청 구간보다 넓은 정시 경계 |
| `apm_change_detection` | neutral | — | task | 변경 감지 시각이며 배포 확정 아님(W2) |
| `apm_masked_fields` | neutral | — | task | 개인정보·자격증명 가림(G-11 미결 · 게이트웨이 봉투 `disclosures`가 칸 이름만 실어 보낸다) |
| `apm_unresolved_condition` | guide | ✔ | task | 해석하지 못한 조건·기능 — 다른 조회로 대신하지 않았음 |

의무 고지는 모두 답 본문에, 비의무 처리기 고지는 우선순위 순 **본문 최대 3줄**(`[안내] …`)이고 나머지는 구조 필드에만 남는다(plans/123 W-9 — W2 검증 B1 이전에는 비의무가 본문에 0줄이었다). 게이트웨이 봉투의 `disclosures`는 등록 kind만 통과시킨다. 작업 카드용 참조는 고지 항목의 선택 칸 `ref`(`{"apm_job_id": …}`)로 싣는다 — `Disclosure`에 선택 칸 `ref`를 더하고 `make`·`dedupe`가 보존한다. 네 진입점(비스트림·스트림 × 텍스트·파일)은 이미 `disclosures[]`를 같은 모양으로 싣는다(plans/123 W-8).

### 7.6 인가 (M-11)

- 실행 경계: `is_source_allowed("apm", …)`(종전) — 작업 장부 등록 전에 판정한다. 작업 API는 장부 소유자(또는 관리자 — D-262)만.
- 후속 턴 참조(`profile_ref`·`active_ref`·`guid`)는 그 턴의 권한으로 다시 판정한다(W5).
- LLM 입력에는 게이트웨이 마스킹본·요약만 들어간다. 다운로드 파일도 D-262 마스킹(`DataMasker`)을 거친다.

## 8. 분석 계약 (W6 — 계획 §4.5 A-1~A-4를 그대로 정본으로 삼는다)

W2~W5는 원자료 계약(시각 원값 · 호출 수 · 총 응답시간 · 단위 · 해상도 · 실패 단위)을 봉투에 남겨 W6 계산이 가중 평균·누락 구간·기준 0(N/A)을 다룰 수 있게 한다. 계산은 게이트웨이(도메인 집계 — D-274 ⑤)가 하고 본체는 단계 연결·조합만 한다.

## 9. Wave · 파일 소유 · 인계

| Wave | 게이트웨이(`apm_gateway/`) | 본체(`src/`·`config/`·`tests/`) | 문서(팀 리드) |
|---|---|---|---|
| W0 | — | — | COV · 이 SPEC |
| W0-B | `application/jobs.py`·`spool.py` · `domain/credentials.py`(신규) · `adapters/jennifer/client.py`(우선순위 throttle · 큰 응답 스풀) · `interface/server.py`(작업 도구 · 주체 토큰 · `owner`·`wait_seconds`) · `interface/audit.py` · `config.py` · `.env.example` · tests | `apm_query.py`(owner·wait·작업 처리) · 작업 장부·서비스(신규) · `src/api/routes/apm_jobs.py`(신규) · `src/domain/disclosure.py` · 화면 작업 카드 · 매뉴얼 · tests | SPEC-apm-gateway §2.1·§3·§6 |
| W1 | `fields.py` · `tools.py` · `masking.py` · `allowlist.py`(`error_type`) · `sources.py`(호스트당 5 상한) · 카탈로그 사본 · tests | `apm_query.py`(M-1·M-2·M-3) · `src/routing/registry.py`(`ViewSpec`) · `config/db_registry.yaml` · `src/prompts/intent_planner.py`(APM 절 view_args) · `tests/test_routing/test_plan125_registry.py` 등 | docs/33 상태 |
| W2 | `allowlist.py`(status 선택 키 · 경로 변수 형식) · `api.py`·`fields.py`(v2 파서) · `tools.py`(신규 3도구) · 목 서버 · tests | 보기 5종 · 선택 재시도(M-8) · tests | |
| W3~ | 130 대상 계약 선행 확인 | | |

- **130 인계**: 130(TODO · 코드 0)이 `apm_instance_map` 인자 확장(인스턴스 이름 단계 검색·업무 해석)을 소유한다. 134 W1·W2는 `apm_instance_map`의 **반환 칸·상한만** 바꾸고 인자·정합 규칙은 건드리지 않는다. 134 W3(서비스 해석)·W4(업무)는 130 검색기를 재사용해야 하므로 **130 W1 착수 뒤**로 둔다(같은 파일 동시 수정 회피).
- **132**: 소스 선택·소스 어휘(`aliases`)는 132 소유 — 134는 보기·view_args만.
- **125**: A-6 ①(순위 = `apm_fleet` W3) · ②(다건 hostname = M-5 W3) · ③(PID 연계 = `apm_config(kind=process_instance)` W7) · A-8(매뉴얼)을 134가 수행하고 125 장부에 증거를 남긴다.
- **121**: F-13(2단 → 조사 위임) 소유. 134는 확장 도구·참조·권한 계약과 통합 테스트를 제공한다 — 연동 전 134 전체 완료 선언 금지.

## 10. 테스트 · 수용 대응

| 수용(계획 §6) | 증거 |
|---|---|
| ① COV 누락 0 | `spec/CAPABILITY-MAP-134.md` §8(현재 0 — Wave 배정 기준) · Wave마다 구현 상태 칸 갱신 |
| ③ 잘리지 않음 | 게이트웨이 계약 테스트: 목 서버 대량 합성(인스턴스 1,200 · 이벤트 5,000 · X-View 60분) → `total_row_count` = 원천 수 · 청크 합 = 원천 수 · 순서 보존 |
| ④ 장기 작업 | 가상 시계·지연 목 서버: 10초 초과 → 작업 핸들 · 120초 초과 작업 완료 · 취소 · 정체 · 재기동(`interrupted`) · 소유자 불일치 `job_not_found` · 만료 정리 · 본체 작업 API 소유자 403/404 · 다운로드 감사 |
| ⑥ 자격증명 카나리아 | §4.2 카나리아 전 형태 × (도구 반환 · 스풀 · 감사 · `limits` · 오류 사유) 부재 단언 |
| ⑦ 통제 유지 | 허용목록 정본 ↔ 사본 · 비GET·`token`·리다이렉트·형식 밖 경로 변수 HTTP 0회 · 비활성 렌더 바이트 불변 |
| ⑧ 게이트 | `arch_check --ci` · `overfit_check --ci` · `apm_gateway/tests/test_boundary.py` · ruff·mypy(기준선 대조) · 본체·게이트웨이·조사·noise_gate 테스트 · D-255 매뉴얼 |

## 11. 측정하지 못한 것 (W10 · 외부 전제)

실 제니퍼 응답 shape·단위·보존 기간·`sort_by_metrics`·`interval_minute` 허용값 · 운영 호출 속도(G-12) · 큰 응답의 실제 크기 · 운영 도메인 350개 실소요 · 개인정보 원값 정책(G-11) · v2 매뉴얼 원천 불일치(COV E-01·E-02). 로컬 Docker 제니퍼는 라이선스가 없어 도메인 0건이다 — 계약 테스트는 스펙 기반 합성 픽스처다.

## 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-02 | 최초 작성(W0) — C-1~C-18 재대조 · COV 신규 발견 반영(새 GET 3 · 경로 변수 형식 · v2 봉투 · 마스킹 결함 · 자격증명 규칙 부재) · MCP 1.29.1 진행·취소 시그니처 실측과 작업 도구 방식 판단 · 공통 인자·봉투 · 장기 작업·스풀·주체 토큰 · 자격증명·식별자 규칙 · 도구 표면 · 보기 25 · view_args·재선택 · 본체 흐름·고지 kind · Wave 소유·인계 |
| 2026-10-02 | W0-B 반영 — §2.2 `artifact.chunks` 모양·`job.error` · §3.7 읽기 기본값·구현 세부 · §3.8 본체 구현 세부(재확인 마감·첫 홉·혼합 결과·partial 상태·감사·중첩 마스킹·카드 복원) · §4.2 자격증명 규칙 확대(검증·보안 감사 발견 High 2·Medium 5 반영: 붙여 쓴 비밀 단어 · 헤더 줄 끝까지 · 세션 쿠키 · 키-값 묶음 모양 · 가린 뒤 자르기 · 제곱 시간 정규식 · 정규화 · 숫자 세션 ID 보존) |
| 2026-10-02 | W1·W2 반영 — §5 도구 표(`apm_events` `n` 기본 전부·`level` 계약 값·`error_type` 접두 변형 재조회 · `profile_truncated` · `apm_status_stats` 전역 재정렬·원자료 칸 · `apm_metrics` 카탈로그 행·scope Wave · `change_detected_ms`) · §6.1 확장 칸(`label`·`text`·`notices`·`active_only`) · §6.3 선택 조건 무효는 고지 후 조회(H-2)·`null` 무고지·평면 view_args·골격 키(H-1)·`limit→n` 단일 task 한정·`level` enum · §6.4 일부 영역만 덮으면 덮은 보기 조회 · §7.1 `**판정·집계**` 블록·APM 표시 절단 |
| 2026-10-02 | W2 검증 반영 — §6.1 `text` 거부 범주·식별자 형식(영문 시작) · §6.3 원천·게이트웨이가 거부한 선택 조건 처리(빼고 조회·로컬 정렬 · 고지) · §6.4 영역별 후보 · 합의 보기(`agreed`) · §7.5 비의무 고지 본문 3줄 · 게이트웨이 고지 통과 |
