# SPEC: apm-question-coverage — 제니퍼 전체 읽기 기능 활용 (`plans/134`)

> 계획서 `plans/134-WIP-jennifer-question-coverage.md` v1.2 · 전수 대응표 `spec/CAPABILITY-MAP-134.md`(COV) · 사용 사례 `docs/33_jennifer_use_cases.md` · 결정 **D-296**(범위) · **D-299**(광범위 활용·구현 계약)
> 게이트웨이 도구 계약의 정본은 `spec/SPEC-apm-gateway.md` §3이다. 이 문서는 134가 그 계약에 **더하는 것**(인자·봉투·작업·도구·보기·본체 흐름)과 Wave별 소유를 정한다. 게이트웨이 구현 Wave는 같은 작업에서 `SPEC-apm-gateway.md` §2.1·§3·§6을 맞춘다.
> 기준 커밋 `54e1597`(main) · 작성 2026-10-02 · 실 제니퍼·LLM 호출 0으로 정한 계약이다(실응답 shape는 W10).
> **[W3·W4 구현 · 2026-10-06 · D-310]** 아래 §5·§6·§7.4가 「목표」이던 W3·W4 행은 구현 사실로 바꿨다(도구 3종 · `apm_metrics` domain·business · `targets` · 보기 6종 · 소스 지목 `sources`). 게이트웨이 계약 정본은 `spec/SPEC-apm-gateway.md` §3.5이고 본체 흐름은 §7.4다.

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
  - **정정(W5 실측 · 2026-10-06)**: 위 「행이 넘어가면 같이 간다」는 사실이 아니었다 — `prior_rows`(`_make_isolated_input`)·`previous_entities`(`context_resolver`)는 **식별 키·값만** 나른다(APM 행 → `{hostname}`). W5 운반 경로: 같은 계획 선행 task의 원 행 `isolated.prior_result_rows`(`apm_query` task 한정 · LLM 입력 아님) · 직전 턴 결과의 참조 후보 `conversation_context.previous_result_refs`(`context_resolver` — 후속 턴 입력 `create_followup_input`이 `query_results`를 비우지 않아 2단 집계기가 올린 표시 순서 원 행이 다음 턴에 보인다 · 프롬프트에 렌더하지 않는다). 번호 규칙은 `src/domain/result_refs.py` 한 곳(§7.7).
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

`Endpoint`에 경로 변수별 형식을 선언한다: `int`(종전 숫자) · `enum[...]`(예 `targetType ∈ {domain, instance, business}`) · `token`(`[A-Z0-9_]{1,64}` — `errorType`) · `account`(`[A-Za-z0-9._@-]{1,64}` — 계정 ID). 템플릿 정규식도 변수 형식에 맞춘다. `..`·`//`·`%`·`\`·`?`·`#`·`://` 거부는 그대로이고, 형식 밖 값은 HTTP 0회로 `NotAllowedError`. 카탈로그 사본(`testdata/jennifer/scripts/jennifer_catalog.py`)도 같은 선언을 갖고 대조 테스트가 둘을 맞춘다. **W7 구현(2026-10-06)**: `sint`(`-?[0-9]{1,20}` — 실행 중 요청 상세의 txid는 음수일 수 있다) 추가 · 경로별 선언 완료(`account` — 계정 · `enum:domain|instance|business` · `enum:domain|instance` · `token` — 오류 유형) · **`.xml` 꼬리 일반 거부**(`account`가 `.`을 받아 `/restapi/user/x.xml`이 템플릿에 맞던 틈) · 허용목록 36템플릿.

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
- **W5 구현(2026-10-06) — 프로파일 예산 분리**: 전송 주체 `chat`(상수 · `APM_GATEWAY_BEARER_TOKENS`의 키)은 프로파일 예산을 쓰지 않는다. 그 밖 주체(`investigation`·`default`·`anonymous` 등)는 칸 `(주체, investigation_id → owner → "_unspecified")`마다 `APM_PROFILE_CALLS_PER_INVESTIGATION`(기본 5)회/1시간(TTL 종전). 주체는 서버가 전송 토큰으로만 정해 도구 코어에 넘긴다(MCP 스키마에 `principal` 없음) — `owner`·`investigation_id`·스키마 밖 인자로 면제를 얻을 수 없다. 종전 `_anonymous` 전 주체 공유 칸은 폐지. 단일 토큰 배포(`default`)는 채팅·조사를 가를 수 없어 면제가 없다(사용자별 owner 칸으로 격리) — 채팅 면제는 본체가 `chat` 토큰을 쓸 때만. **남는 위험**: 조사 LLM이 호출마다 새 `investigation_id`를 지어내면 칸이 새로 생긴다(서버가 조사 ID를 직접 받을 경로가 없다 — sre_agent는 프로세스당 정적 토큰 1개).

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
| 남긴 모양(W10 녹화본으로 판단) | `token C`·`api_key C`(구분자 없음) · `password -> C`·`password is C` · URL 인코딩·HTML 엔티티 변형 · 명령 문맥 밖 띄어 쓴 `-p 값`(`ssh -p 22`와 구분 불가) · (W7 수정 뒤) 명령 문맥의 구조화 객체 안 단일 토큰 인자 · 줄 중간 따옴표 없는 비밀 값의 공백 뒤 · 문장 안 접속 문자열(전체 일치가 아님) · `?`·`#` 뒤에만 `@`가 있는 숫자 시작 URL 비밀번호 |
| **W7 개정(2026-10-06 · 보안 감사 AUDIT-1~6 · D-302 ⑦)** | ① **명령 문맥 칸**(키에 COMMAND·SCRIPT·ARGS·EXEC… — 룰 `autoScriptCommand` 등)은 도구별 비밀번호 표기를 쫓지 않고 **첫 토큰(실행 파일 · 따옴표 경로 포함)만 남기고 나머지 인자를 통째로 `[가림]`** — 첫 토큰이 `NAME=값` 대입이면 값도 가린다 · 배열 명령은 첫 원소 뒤 전부 ② 키 판정은 토큰 단위에 더해 **구분자를 걷은 대문자 전체 키**에도 부분 문자열·끝맺음 규칙(약어+소문자 `APIkey`·`dbPASSword` 분할 우회 차단) · 어휘 `CREDS`·`PASSCODE`·`비밀번호`·`암호`·`패스워드` 추가 · **POSIX `PWD`·`OLDPWD`는 값이 절대 경로일 때만 비밀이 아니다**(작업 디렉터리 — 종전 「과잉 가림 의도」를 개정 · ODBC `PWD=<비밀>`은 종전대로 가림 · 소문자 `pwd` 계정 필드는 키째 제거) ③ 값 **전체**가 `name/secret@host…`이면 문맥 무관 가림 · 콜론 없는 `scheme://<토큰>@`도 사용자 정보 통째 · `?`·`#` 앞에 `@`가 있으면 포트로 보지 않는다 ④ 따옴표 값은 백슬래시 이스케이프 지원 · JVM `-D<비밀 키>=` 값은 공백까지 · 따옴표 없는 KV 비밀 값은 연결 문자열 문맥(`;키=`)이면 `;`까지 · 줄 머리 키면 줄 끝까지 · 그 밖 공백까지(종전 `; & ,` 구분자 꼬리 차단 — 과잉 가림 허용) ⑤ 이름/값 묶음 칸은 끝맺음(`…name`·`…key`·`…field`·`…param` / `…value`·`…val`)으로 ⑥ 비200 JSON 오류 본문은 `scrub_detail` 뒤 직렬화 · **재감사(REAUDIT) 반영**: ⑦ 명령 키가 아닌 칸에도 도구별 표기(`connect`/`attach … user X using <pw>` 같은 줄 · `-P <pw>`/`-P<pw>` · 띄어 쓴 `-a`·`-w` · `-u user,<pw>`·`-U user%<pw>`)를 가린다 — 명령 키 「첫 토큰만」과 **겹쳐** 적용(대체 아님 · `wget -w 5`·`mvn -P prod` 과잉 가림 허용 · `-agentlib`·`ls -al`·`JOIN … USING` 보존) ⑧ 명령 키 아래 값이 객체·맵이면 통째로 가림(불리언·None은 그대로) ⑨ 어휘 `CRED`(토큰 정확 일치 — `CREDIT` 아님)·`시크릿`·끝맺음 `…ASSERTION`·키 한정어 X509·RSA·DSA·ECDSA·ED25519·PGP·GPG(TLS·SSL 제외 — `javax.net.ssl.keyStore` 보존) ⑩ 운영 엔트리는 HTTP 클라이언트·MCP 전송(`mcp` 부모 로거)·`sse_starlette`·`uvicorn.access` 로거를 WARNING 이상으로(INFO·DEBUG 기동 모두 — DEBUG에서 들어온 도구 인자 본문이 찍히던 채널 차단). **남긴 모양(처분 · W10)**: 문장 속 접속 문자열(전체 일치만 봄 — REAUDIT-3) · 줄 중간 공백 있는 따옴표 없는 비밀 값(REAUDIT-5) · 큰따옴표·SQL 주석 속 사람 이름(G-11 — REAUDIT-6) · 다른 줄에 있는 DB2 `using` · 붙여 쓴 `-a<pw>` |

- **카나리아 테스트**(필수): 중첩 dict/list · `SYSTEM`/`JAVA` 묶음 · 키-값 배열 · `KEY=VALUE` 자유 텍스트 · JDBC URL · `-Ddb.password=` · `DB_PW2` · `autoScriptCommand` 인자 · `password` 필드 · 대소문자 변형. 카나리아 값이 도구 반환·스풀 파일·감사 로그·`limits`·오류 사유 어디에도 없음을 단언한다. 일반 설정값(`PATH`·`JAVA_HOME`·`java.vendor`)은 그대로임을 함께 단언한다.
- 패턴 테스트만으로 모든 비밀을 보장했다고 선언하지 않는다(계획 §9) — 운영 마스킹 녹화본 대조는 W10.

### 4.3 개인정보 식별자 (G-11 미결 동안)

`mask_text`의 이메일·휴대폰·주민번호·IP 규칙에 더해, 식별자 필드(`userId`·`clientId`·계정 `id`·사람 `name`)는 `mask_identifier`(앞 1자 + `***` · 2자 이하는 `***`)로 가린다. HTTP query 문자열(`http.query`)은 전용 규칙으로 **첫 값까지** 가린다(E-20 교정 · W7: `=` 없는 맨 항목도 식별자형이 아니면 `<v>`). SQL은 `mask_sql`(리터럴 `?` · W7: PG 달러 따옴표 · 결과에 `mask_pii`). **W7 개정(AUDIT-7·8 · VG-1)**: 사용자 `phone_number`는 값이 있으면 `<phone>` · `email`은 `@` 앞 `mask_identifier` · 사용자·실행 중 요청 `extra`의 식별자형 키(`…Id`·`…ID`·`…Name`·`nickname`·`emp…`) 문자열은 `mask_identifier` · 설정 값(환경변수·데이터 서버 설정)은 `mask_pii`(이메일·주민번호·휴대폰만 — 서버 IP는 인프라 정보라 가리지 않는다) · 프로파일 SQL 응답은 **출처 칸 이름**(`SQL_STATEMENT_KEYS` — `sql`·`sqlText`·`statement`·`query`… · 응답 모양 미공개라 추정 · W10)이면 `mask_sql`, 그 밖 칸(바인드 값일 수 있음)은 `mask_identifier` + `[한계]`(키워드 판정 금지 — 저장 프로시저 호출 보존). 계정 ID가 든 경로는 로그·사유에 템플릿으로 남기고, HTTP 클라이언트 라이브러리 로거(`httpx`·`httpcore`)는 운영 엔트리에서 WARNING이다. 원값 표시 경로는 G-11 결정 뒤 별도 개정이다(D-262 · D-299 ⑦).

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
| `apm_transaction_profile` | W1·**W5 구현** | txid·profile.txt·sql | W5 `profile_no?`·`include_param_key?`(sql만) | 발췌(앞 60줄)는 화면용으로 남고 발췌가 잘렸으면(`profile_truncated`) **전문은 `artifact.text_parts["profile"]`**(마스킹본) · SQL 전부(`top_k` 비우면 전부) · `key`는 허용만(미전달 + `[한계]` — W10) · SQL 문 칸이 아닌 문자열(바인드 값일 수 있음 — 출처 칸 이름으로 판정)은 `mask_identifier` · **예산 분리(W5 구현)**: 전송 주체 `chat` 면제 · 칸 `(주체, 조사 ID → owner → 미지정)` · 인자로 면제 불가(§3.6) |
| `apm_status_stats` | W2 | `/api/status/{application,sql,external_call}` | `kind` · `hostname`·`instance_id?` · 구간(기본 60분) · `sort_by?` · `n?`·`full?` · `application_name?` | 시 경계 고지 · `name` 마스킹(SQL·URL) · 도메인별 `max_row=n` 뒤 전역 재정렬(정렬 기준 대응을 모르면 「전역 순위 아님」 `[한계]`) · `summary` 평균 = Σ`total_response_ms` ÷ Σ`calls`(원자료 칸은 행에 남김 — W6 가중 평균 입력) · 표시 행 기준 합계면 그 사실을 `[한계]`에 |
| `apm_metrics` | W2(instance)·**W3·W4 구현**(domain·business) | `/api/metrics` · `/api/dbmetrics/{instance,domain,business}` | `mode=catalog\|series` · `scope` · `metrics` · `interval_minute?`(기본 5) · 대상 · 구간(기본 60분) · **W3·W4** `service?`·`business?`·`business_id?`·`domain_id?` · **W3** `targets?`(instance 시계열만) | 카탈로그 = 소스별 전 지표 군 행 `{source_id, scope, metric}`(`externalCall` → `external_call` · TTL `APM_METRIC_CATALOG_TTL_SECONDS` · 지문 변경 감지 · 모양이 다르면 오류) · 시계열 정본(업무 포함 — E-24) · 긴 형식 행 · **instance 시계열**: 모르는 지표 = 후보 ≤3 + `invalid_argument` · **domain·business 시계열(W3·W4)**: 지표 미지정 = 게이트웨이 기본 지표(`response_time_avg_ms`·`service_count`·`service_err_count`) · 일부 모름 = 빼고 조회 + 고지 · 전부 모름 = 기본 지표 + 고지(`partial`) · 카탈로그를 못 읽은 소스는 검증 없이 조회하고 `[한계]`·partial · 이름을 줬는데 못 찾으면 데이터 API 0회 |
| `apm_source_changes` | W2 | `/api-v2/deploy/{domainId}` | 대상 · 구간(기본 24시간) | 25시간 조각 · v2 맨 배열 전용 파서 · 겹침 제거 · 원시 시각 `change_detected_ms` 보존 · 「변경 감지(데이터 서버 인지 시각) — 배포 확정 아님」 |
| `apm_service_status` | **W3 구현**(N-9) | `/api/realtime/domain` | `service?`(str\|list) · `domain_id?` · `source_ids?` | 이름 없음 = 소스당 1호출 전 도메인 · 이름 = 도메인 이름 단계 검색(130 판정을 공용 함수로 뽑아 재사용 — 인스턴스·도메인·업무 같은 코드) · 한 이름에 여럿 = 전부 조회 + `[한계]` · **이름을 줬는데 하나도 못 찾으면 데이터 API 0회 · 행 0 · 전체로 넓히지 않음** + `apm_unresolved_condition` + 후보 ≤3 |
| `apm_fleet` | **W3 구현**(N-10·N-11) | realtime/instance 전 도메인 · dbsearch/event 전 도메인(+ 폴러 버퍼 N-11) | `mode=ranking\|events`(필수) · `metric`(30종 `RANKING_METRICS`)·`order`·`n`·`full` · `level`·`level_mode`·`error_type`·`reference_time`·`lookback_minutes` · `service?`·`domain_id?`·`source_ids?` | **ranking**: 전 도메인을 모은 뒤 정렬 · 실패 도메인·소스가 있으면 `provisional` + `partial` + 「잠정 순위」 · 값 없음은 순위 밖 · `summary`(도메인·인스턴스 수) · **events**: 버퍼가 확정한 구간은 버퍼 · 나머지는 도메인마다 API 1회 · 중복 제거는 버퍼↔API 겹침만 · 응답 모양을 모르는 도메인·실패 도메인은 0건이 아니라 「확인하지 못함」(`coverage.failed`) · `summary.events_total` · `service`로 좁힘 |
| `apm_business` | **W4 구현**(N-12) | `/api/business` · `/api/realtime/business` | `mode`(`current` 기본·`list`) · `business?`(str\|list) · `service?` · `domain_id?` · `source_ids?` | 업무 정의(`list` — 행에 `bad_response_time_ms`·`business_index`·`business_oid`(파일 전용)·`rules[]` 포함)·현재값 · 이름 해석 규칙은 `apm_service_status`와 같다(한 이름에 여럿 = 전부 조회 + `[한계]` · 못 찾으면 데이터 API 0회) · 시계열은 `apm_metrics(scope=business)` |
| 모든 `hostname` 데이터 도구 17종 | **W3 구현**(N-9 · M-5) | 각 도구 그대로 | `targets?: [{hostname?, instance_name?, instance_id?(7도구만), source_id?}]` | 한 호출 = 작업 1개 · 항목마다 도구 코어 호출 · 봉투 `batch[]`(`target_index` 행) · 일부 실패 `partial` · 전부 실패 오류 + `batch` · 최상위 `limits` 합집합 · 예상·진행은 배치 전체 · 감사 `targets(N)` · `apm_metrics`는 instance 시계열만 |
| `apm_transaction_trace` | **W5 구현** | `/api/transaction/guid` | `guid`(필수) · `hostname?` · 구간 · `around_ms?`·`around_minutes?`(기본 5) · `source_ids?` | 허용된 전 소스·도메인(호스트를 주면 그 정합 도메인) · 창 = 명시 > `around_ms ± 5분` > 최근 60분(기본이면 `[한계]`) · 중복 제거 (`source_id`, `domain_id`, `txid`) · 시작 시각순 `trace_order` · 다른 GUID 행 제외 · 토폴로지 아님·시계 차이 고지 · 부분 실패 partial (A-3) |
| `apm_change_impact` | **W6 구현**(A-2) | deploy + X-View + dbsearch/error | `hostname` · 구간(변경 탐색 · 기본 24시간) · `width_minutes?`(기본 60) · `n?`·`full?` | 변경마다 전 `[t−w, t)`·후 `[t, min(t+w, 지금))` 호출·오류·평균(Σ÷calls)·원시 p95·오류 기록 · `delta`(기준 0 = N/A · 비율 차 %p) · 조각 실패 = 그 구간 None · 원인 확정 아님 고지 |
| `apm_period_compare` | **W6 구현**(A-1 · 게이트웨이만 — 채팅 배선 없음) | status/application(인스턴스 × 구간) | `hostname` · `current_*`·`baseline_*`(ISO 절대 구간) · `n?`·`full?` | 시 경계 · Σtotal÷Σcalls(재료 없으면 계산 불가) · 한쪽만 있는 인스턴스 N/A · 길이 차이·p95 미제공 고지 — 조사(MCP)가 소비. 채팅은 두 구간 해석이 `plans/122` ⑥ 기준일 주입·M-7에 달려 잔여 |
| `apm_config` | **W7 구현** | 룰(error·metric·compare·applied·individual-setting) · 색상 경계 · 프로세스→인스턴스 · 데이터 서버(domains·resource·system-property-config) · DB 경로 · 로드된 클래스 · 수동 RDB Export 상태 | `kind`(7종) · `hostname?` · `rule_type?`·`target?`·`error_type?` · `process_id?` · `search?` · `source_ids?` | 자격증명 제거(§4) 통과 · **COV E-01 확정: `compare` 먼저, 404일 때만 `comparing`으로 다시 묻고 답한 표기를 `[한계]`에**(W1 `error_type` 표기 재질의 선례) · 개별 설정 404 = 설정 없음 · v2 404·405 = 버전 미지원 가능 · kind에 안 쓰는 인자는 빼고 조회 + `[한계]` · 설정 값 `mask_pii` · 행 칸 정본은 `spec/SPEC-apm-gateway.md` §3 |
| `apm_environment` · `apm_users` · `apm_active_detail` | **W7 구현** | environment-variable · auth/userlist·restapi/users·user/{id} · active-service/detail | `hostname?`·`scope?`·`key?` · `user_id?` · `active_ref` 칸(`domain_id`·`txid`·`session_id`·`thread_hash`·`source_id`) + `hostname?` | §4 자격증명 제거 · §4.3 식별자 가림 · 환경변수 키를 줄이지 않음 · 값의 이메일·주민번호·휴대폰 `mask_pii` · 계정 ID 원값은 감사·`[한계]`·오류 사유·DEBUG 로그에 없음 · 조사에서도 노출(일괄 비노출 금지 — D-299 ③ · `sre_agent` ⑥) |
| `apm_job_status`·`apm_job_cancel`·`apm_job_read` | W0-B | — | §3.7 | 작업 관리 |
| `gateway_health` | — | — | — | `allowlist_size` 값이 늘어난다(W3·W4에서 41) · `poller.event_buffer`(폴러가 켜졌을 때 — 보관·최대 건수·확정 도메인 수) |

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
# W5·W7 확장(2026-10-06): ViewSpec.target(""=종전 규칙 · optional=이번 턴 대상이 있으면 대상별 · 없으면 hostname 없이 1회(첫 홉 삽입 없음) ·
#          직전 턴 대상은 원문이 지시어일 때만 · reference=앞 결과 행의 참조 칸 · none=대상 해석 안 함) · ViewSpec.reference(profile_ref·active_ref·guid) ·
#          ViewArgSpec.required(무효·없음 = 그 보기만 조회하지 않고 되묻기) · default · targeted_choices(대상별 호출을 허용하는 kind 값 —
#          소스 범위 kind는 대상이 여럿이어도 1회) · 형식 opaque(공백·제어 문자 없음 1~256자 — GUID) · account(`[A-Za-z0-9._@-]{1,64}`) ·
#          token(대문자 `[A-Z0-9_]{1,64}` — 대소문자만 맞춤) — 게이트웨이 형식과 같게(본체 통과값이 게이트웨이에서 보기 전체를 실패시키지 않게)
# W3·W4 확장(2026-10-06): ViewSpec.target `named`(이름으로 찾는 보기 — 서비스·업무) + ViewSpec.target_arg(`service`|`business` — 이번 task 대상 텍스트 중
#          `instance` 종류가 아닌 것을 이 도구 인자의 목록으로 싣고, 이름이 없으면 인자 없이 전체 1회) · `named`가 아니면 target_arg를 받지 않고
#          `named`면 target_arg가 꼭 있다(파서 검증) · ViewArgSpec 형식 `enum`의 default(순위 지표·정렬) — 영역은 기존 `was_performance`·`apm_event`만 쓴다
# W2 확장: ViewSpec.notices(보기가 늘 붙이는 고지 kind — KIND_TABLE 대조) ·
#          CapabilitySpec.active_only(소유 시스템이 활성일 때만 분해 영역 카탈로그에 렌더 — 비활성 바이트 불변)
#          `text` = 1~200자 · 유니코드 Cc·Cf·Zl·Zp 문자 거부(URL 이름 등 식별자 형식이 아닌 값)
#          식별자 형식(`str`) = `[A-Za-z][A-Za-z0-9_]{0,63}`(게이트웨이 형식의 부분집합 — 본체 통과값이 게이트웨이에서 거부되지 않게)
```

- `window_max_minutes`는 **창 상한 의미를 폐지**한다(M-2). 레지스트리에서 지우고 `window`로 바꾼다. 소비처·테스트(C-18)를 같이 고친다.
- 레지스트리 YAML 형식은 위 칸을 그대로 쓴다(`args: [{name: n, type: int, min: 1}, …]`). 벤더 중립 어휘만(D-274 ③).

### 6.2 보기 목록 (출발 25 — 계획 §4.2 · 현재 레지스트리 **28** — W3·W4에서 6종 더함)

| Wave | 보기 | 도구(+고정 인자) | 창 | 대상 | view_args |
|---|---|---|---|---|---|
| 현행 | `apm.instances` | `apm_instance_map` | none | `domain_id`(정수 0 이상 — 그 도메인 인스턴스만 · `plans/130` W1-D)(첫 홉) | — |
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
| **W3 구현** | `apm.service` | `apm_service_status` | current | **named**(`service` — 도메인 이름 · 없으면 전체 1회) · 영역 `was_performance` | — |
| **W3 구현** | `apm.service_trend` | `apm_metrics`(mode=series · scope=domain) | range | named(`service`) · `was_performance` | `metrics`(catalog=domain · 미지정 = 게이트웨이 기본 지표) · `interval_minute` |
| **W3 구현** | `apm.ranking` | `apm_fleet`(mode=ranking) | current | none(대상 해석 안 함) · `was_performance` | `metric`(30종 enum · 기본 `response_time_avg_ms`) · `order`(`desc` 기본) · `n` · `full` · `service`(text) |
| **W3 구현** | `apm.fleet_events` | `apm_fleet`(mode=events) | range(기본 30분) | none · 영역 `apm_event` | `level` · `level_mode` · `error_type`(token) · `n` · `full` · `service`(text) |
| **W4 구현** | `apm.business` | `apm_business` | current | **named**(`business`) · `was_performance` | `mode`(`current` 기본·`list`) |
| **W4 구현** | `apm.business_trend` | `apm_metrics`(mode=series · scope=business) | range | named(`business`) · `was_performance` | `metrics`(catalog=business · 미지정 = 기본 지표) · `interval_minute` |
| **W5 구현** | `apm.profile` | `apm_transaction_profile` | none | **reference**(`profile_ref`) · 영역 `was_transaction`(신규 · active_only) | `ref`(int ≥1) · `top_k` · `include_param_key` |
| **W5 구현** | `apm.trace` | `apm_transaction_trace` | range | **reference**(`guid`) 또는 GUID 직접(`guid` opaque) · 사용자가 이번 턴에 말한 서버로만 좁힘 · `was_transaction` | `guid` · `ref` |
| **W6 구현** | `apm.change_impact` | `apm_change_impact` | range(변경 탐색) | hostname(종전 필수 규칙) · `was_change_detection` | `width_minutes` · `n` · `full` · 고정 고지 `apm_change_detection` |
| **W7 구현** | `apm.event_rules` | `apm_config`(kind는 view_args) | none | optional · 영역 `apm_management`(신규 · active_only) | `kind`(event_rules·color_boundary · 기본 event_rules · 대상별 호출은 event_rules만) · `rule_type` · `target` · `error_type`(token) |
| **W7 구현** | `apm.process` | `apm_config`(kind=process_instance) | none | optional · `apm_management` | `process_id`(int ≥1 · **필수**) |
| **W7 구현** | `apm.jennifer_server` | `apm_config`(kind는 view_args) | none | optional · `apm_management` | `kind`(data_server·db_path·rdb_export · 기본 data_server · 대상별은 db_path만) |
| **W7 구현** | `apm.loaded_classes` | `apm_config`(kind=loaded_classes) | none | hostname(종전 필수 규칙) · `apm_management` | `search` |
| **W7 구현** | `apm.environment` | `apm_environment` | none | optional · `apm_management` | `scope`(SYSTEM·JAVA) · `key` |
| **W7 구현** | `apm.users` | `apm_users` | none | none · `apm_management` | `user_id`(account) |
| **W7 구현** | `apm.active_detail` | `apm_active_detail` | none | **reference**(`active_ref`) · `was_activity` | `ref` |

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

### 7.4 복수 보기·대상 (M-5 · **W3·W4 구현 2026-10-06 · D-310 ①②③⑦**)

- **절단 제거**(D-296 ④ · D-299 ②): `MAX_VIEWS = 3` 상수를 지웠다(닫힌 어휘 검사·중복 제거는 유지). 분해 지시 「1~2개」는 「필요한 보기 id를 모두(개수 제한 없음)」로 바꿨다 — **활성 분해 절만**이고 비활성 렌더 바이트는 그대로다. APM 경로 hostname 대상의 `max_targets` 절단도 지웠다(`resolve_apm_targets`·`scoped_targets`·`explicit_targets`·`default_views`에서 매개변수 삭제 — 폴스타 공유 설정 `composite.max_targets`는 그대로).
- **다건 대상 = 배치 1호출**: 같은 보기 + 같은 공통 인자(대상 인자 `hostname`·`instance_name`·`instance_id`·`source_ids`를 뺀 나머지)이고 대상이 **2개 이상**이면 `targets` 한 번으로 부른다(hostname 항목과 해석 인스턴스 항목 `{instance_name, source_id, instance_id?}`가 한 배치에 섞일 수 있다 · 고른 소스는 배치 최상위 `source_ids`). **대상이 1개면 묶지 않는다 — 호출 인자가 종전과 같다**(`bac814c` 대비 본체 인자 8사례 · 게이트웨이 접근 기록 10호출 대조).
- **대상별 복원**: `batch[i]`와 `target_index == i` 행으로 i번째 대상의 가상 호출을 복원해 집계·판정·출처·정합 장부·감사 명령을 **종전처럼 대상별로** 남긴다. 항목 `status=error`는 그 대상의 호출 오류가 된다. 항목이 없는 경우(배치 자체 실패 · `batch` 없는 봉투 · 옛 게이트웨이)는 대상별 `batch_missing` 실패이고 **행 0건으로 읽지 않는다**. 배치 경과는 `meta["batches"]`(보기·도구·대상 수·실패 수·`job_id`·`accepted`). 결과 파일로 간 행은 대상별 봉투에 `rows_in_file`(= `row_count − 인라인 행 수`)을 달아 구별하고, 배치 단위 `apm_full_result_file` 고지 끝에 「일부 대상의 행은 결과 파일에만 있습니다(k개 대상)」를 붙인다(행 0 · 건수 양수 모순 없음 · 그 대상을 `empty`로 세지 않는다).
- **작업 승격**: 배치가 마감 안에 끝나지 않으면 작업 **1건** 접수 답이다(풀지 않는다 — 예상·진행은 배치 전체 · 대상 N개 · 재승인 질문 없음). 마감 안에 끝난 배치 작업은 `result_meta`에서 복원한다.
- **첫 홉 정리**(D-310 ③): 대상 필수 보기에 대상이 없을 때만 인스턴스 목록을 먼저 부르고 **절단 없이 전부** 대상으로 쓴다 — 호스트 정합 인스턴스 = hostname 대상(서버당 1개) · 호스트 없는 인스턴스 = `{instance_name, source_id, domain_id, instance_id}`. 목록이 인라인 상한을 넘으면(`total_row_count > len(rows)`) `apm_job_read`로 **결과 파일을 청크 0부터 끝까지** 읽는다. 끝까지 읽지 못하면 「전체」라고 쓰지 않고 「대상 서버 미지정 — 인스턴스 T개 중 M개(호스트 H대)만 조회 — 나머지는 목록 결과 파일을 끝까지 읽지 못했습니다」를 의무 `apm_partial_sources`로 낸다(상태 `partial`). 범위 고지 「대상 서버 미지정 — 전체 인스턴스 N개(호스트 H대) 조회」는 비의무이고 인스턴스가 0개면 내지 않는다. 대상 텍스트가 있었으면 첫 홉이 없고(D-290 ⑥) 명시 hostname 정합 실패는 그 대상의 실패로 끝난다. 전 대상 보기(`apm.ranking`·`apm.fleet_events`)·이름 보기·`apm.metrics`에는 첫 홉이 없다 — **명시 대상 미해결을 첫 홉으로 대체하지 않는다.**
- **이름 보기(`target: named`)**: 이름은 분해 `targets` 중 `kind != instance`인 텍스트다. 한 번 호출하면서 `{target_arg: [이름…]}`을 싣고 이름이 없으면 인자 없이 전체 1회를 부른다. 게이트웨이 검색어 상한(200자)을 넘는 이름은 싣지 않고 고지하며, 말한 이름이 모두 그렇다면 그 보기는 부르지 않는다(넓히지 않음). **「찾지 못함」** = 행 0건 + 봉투 고지에 `apm_unresolved_condition` → 실패 `{view, target, "대상 '…' 해석 0건"}` + 본체 고지 「… 찾지 못해 다른 서비스로 대신 조회하지 않았습니다. 비슷한 이름: … — 자동으로 고르지 않았습니다」(봉투 `suggestions` ≤3). 모든 호출이 실패했거나 「찾지 못함」뿐이면 `apm_target_unresolved` 답이고, 다른 보기에 행이 있으면 부분 결과로 답한다.
- **전 대상 보기(`target: none` + `service` 조건)의 대상 텍스트**(검증 V34-4): 이번 task 대상 텍스트 중 `instance` 종류가 아닌 것은 `service` 인자 목록에 **싣는다**(조건 `service`와 함께 오면 대소문자 무시로 중복을 지운 합친 목록 — 조건 값이 앞). 서비스가 있는 전 대상 호출도 이름 보기와 같은 「찾지 못함」 판정을 건다(조건 `service`만 있어도). **사용자가 말한 서버(이번 턴 식별자 `explicit_targets`와 `instance` 종류 대상 텍스트)로는 좁히지 않는다** — 조회는 전체(또는 서비스) 범위로 하고 의무 고지 `apm_unresolved_condition` 「{보기}: 말한 서버(…)로는 좁히지 않았습니다 — 서버별 값은 응답시간 등 서버 보기로 물어 주세요.」를 남긴다(조용히 버리지 않는다 · 복합 계획의 파서 식별자는 제외 · 직전 턴 대상은 지시어일 때만).
- **소스 지목 `sources`**(D-310 ⑦): 분해 task 칸 `sources`는 활성 + 레지스트리 소스가 **2개 이상**일 때만 렌더한다(골격 키·규칙 줄 포함). 처리기 `_plan_sources`는 레지스트리 id로 검증한다 — 모르는 id는 빼고 고지(`apm_unresolved_condition`) · 전부 무효면 **게이트웨이를 열지 않고** 되묻기(선택지 = 소스 표 라벨 · 전 소스로 넓히지 않음) · 소스 표가 없는 배포(소스 0개 — 단일 설정)는 쓰지 않고 비의무 고지(kind `trace`)로 알린다. 유효한 id는 그 task의 모든 게이트웨이 호출(해석 검색·E1r·첫 홉·배치 최상위·이름 보기)에 `source_ids`로 실린다. 운반 경로는 `targets`와 같다(`sanitize_sources` 형태 정제 → 분해 task 보존 → 구조화 스키마 → 재계획). `meta["source_selection"] = {given, used}`.
- **결정적 줄**(`**판정·집계**` 블록 · `_fleet_lines`): 순위 「{metric} 내림차순|오름차순 순위 — 전체 인스턴스 N개 중 상위 n[ · 값 없는 인스턴스 u개는 순위에서 뺐습니다]」 · `provisional`이면 「잠정 순위 — 조회 실패 도메인 k곳 제외」를 더하고 의무 `apm_partial_sources` + 상태 `partial`(`empty`로 세지 않음). 이벤트 「이벤트 M건 · 도메인 D곳(버퍼 b · API a[ · 혼합 m] · 실패 f)」 — M은 `summary.events_total`이고 표시 행 수와 다르면 **「이벤트 total건(표시 n건)」**(V34-5) · f > 0이면 「 — 실패 도메인은 0건이 아니라 확인하지 못함」을 붙인다. 지표 이름은 중립 이름 그대로다(본체에 표시 이름표 없음).
- **분해 프롬프트**: 비활성 렌더 지문은 불변(`06da76f1a17e4090` · 8,032자). 활성 지문은 `c336e53f4ea98eb9`(20,957자 — 세션 시작 대비 +3,670자: 보기 표 6행 · 소스 줄 · 지침 2줄 · 예시 3줄 · 골격 `sources` 키 · 규칙 1줄).

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

### 7.7 앞 결과 행 참조 (M-6 · W5 구현 2026-10-06)

- **후보 원천**: ① 같은 계획 선행 task의 원 행(`prior_result_rows` — `input_from` 먼저, 없으면 `depends_on`이 가리키는 APM task(검증 V-5) · 두 목록을 한 번호로 섞지 않는다) → 그 행들에 그 종류 참조 칸이 없으면 ② 직전 턴 참조 후보(`conversation_context.previous_result_refs` — `{turn, <종류>: [후보…], tables?}`).
- **표 단위 번호(코드 리뷰 R-1)**: 앞 결과가 **표 여러 개**(복합 턴)였으면 번호는 표 안 순서다. 집계기가 복합 턴 끝에 그 턴의 `conversation_context.result_tables`(`[{label, rows}]` — 행 수 합이 `query_results`와 같을 때만)를 남기고, 다음 턴 `context_resolver`가 한 번만 읽는다(구조적으로 그 다음 턴에는 남지 않는다 · 행·화면 표·CSV 칸 불변 — 행 표지·새 상태 키는 CSV 칸 증가·턴 시작 초기화 불가로 택하지 않았다). 요청한 참조 종류의 후보가 **표 둘 이상**에 걸치면 추측하지 않고 표(보기 라벨)마다 후보 ≤3을 들어 되묻는다(의무 고지 · 「원하는 목록만 다시 조회한 뒤 몇 번째인지」). 표를 고르는 조건 칸(`view_args.table` 등)은 두지 않았다(활성 프롬프트 변경 · 잔여).
- **번호**: 그 참조 칸(`profile_ref`·`active_ref`·`guid`)이 **있는** 행 사이의 표시 순서(1부터). 값이 null인 행도 자리를 지켜 화면 표 번호와 같다(그 행을 고르면 「…번째 행에는 참조가 없어」 되묻기).
- **선택**: `ref`(계획 LLM 값)를 코드가 범위·종류로 검증 → 밖이면 되묻기(후보 ≤3 라벨 + 「외 N건」) · `ref` 없음 + 후보 1 = 그 행 · 여럿 = 되묻기 · 0 = 조회하지 않고 사유(다른 보기·첫 홉으로 대신하지 않는다). 한국어 순번을 정규식으로 읽지 않는다 — **계획 LLM이 낸 `ref`가 형식·범위 밖이면 후보 수와 무관하게 되묻는다**(코드 리뷰 R-2 · 검증 V-8 — 종전 「후보 1개면 그 행」이 「5번째」·0·-1을 무시하고 조회했다) · 형식 밖 `ref`(「두번째」)는 되묻기 문구 하나에 합친다.
- **인자**: 행의 참조 칸을 **키 변환 없이** 도구 인자로 펼치고 행의 `hostname`을 함께 넘긴다(게이트웨이가 정합을 다시 검사). `ref`·`investigation_id`는 보내지 않는다.
- **인가**: 처리기 진입의 `is_source_allowed`(이번 턴 권한) · 참조는 스레드 체크포인트 상태 안에만 있다. **주의**: 스레드 소유 확인은 라우트 몫이다(`plans/134` §12.3 — 사용자 결정 필요).
- **유지·교체**: 직전 턴에 참조 칸 행이 없으면 그 앞 후보를 잇는다(`previous_entities` sticky와 같은 규칙) · 참조 칸 행이 새로 나오면 종류 무관 통째 교체 · 2턴 이상 전 목록이면 경과 노트.
- **GUID 추적 창**: 사용자 기간(range 규칙 · 「하루 넘게」 규칙 유지) > 참조 행 시각(`around_ms` = 시작 → 끝 → `profile_ref.time_ms`) > 게이트웨이 기본 60분. GUID 직접 지정이 `ref`보다 앞선다. 좁힘은 이번 턴 사용자가 말한 서버(단일 task)·지시어 직전 대상만 — 선행 task 행의 서버로 좁히지 않는다(연계 추적이 한 도메인으로 줄지 않게).
- **되묻기만 남으면 게이트웨이를 열지 않는다**(게이트웨이 가용성에 되묻기가 가려지지 않게).
- **같은 계획 「목록 → N번째 상세」는 단계별 답**(검증 V-1 · D-100 부기): APM 결과가 식별 키당 2행 이상이거나 참조 보기만 고른 task이면 서버 키 병합을 하지 않는다(접힌 행에 다른 거래의 상세가 붙던 침묵 손실).
- **GUID 추적 호출 사이 중복 제거**(V-4): 여러 서버로 좁힌 호출의 결과를 (`source_id`, `domain_id`, `txid`)로 합치고 GUID 줄을 하나로.
- **결정적 줄**: GUID 「GUID g: 거래 N건 · 도메인 H곳(조회 M곳 · 실패 K곳) · 조회 구간 {start} ~ {end}(기간 미지정이면 게이트웨이 고지 — 「앞 결과 시각 ±5분」·「최근 60분」) · 같은 GUID일 뿐 호출 관계가 아님 · 인스턴스 …」(0건 답에도 반드시 — 검증 V-3) · 변경 전후 「{인스턴스} 변경 감지 {시각} — 오류율 a → b(±%p) · 평균 응답 a → b(±%) · 오류 기록 a → b · 호출 a → b」(N/A 그대로 · 차이는 게이트웨이 `delta`) — `**판정·집계**` 블록.

### 7.8 대상 텍스트 해석 — 인스턴스 이름 · 업무명 (`plans/130` W3·W4 · M-1·M-2·M-4·M-5·M-6 · D-290 ⑥ · D-296 ④)

게이트웨이 계약은 `spec/SPEC-apm-gateway.md` §3.4(검색 `query` · 업무명 `business` · 정확 이름 `instance_name`)다. 본체는 LLM 0으로 해석·호출·고지만 한다. **대상 텍스트가 없으면 종전과 비트 동일**하다(테스트로 고정).

- **분해 칸 `targets`**(M-1): `apm_query` task의 선택 칸 `[{text, kind}]` — `kind` ∈ `instance`(인스턴스 이름 · 일부만 말해도 됨)·`business`(업무명)·`auto`(모름). 계획 프롬프트의 이 안내는 **APM 섹션 안에만** 있어 APM이 활성일 때만 렌더된다. hostname·IP는 넣지 않는다. 정제(`sanitize_targets` — 분해 정제와 처리기 진입에서 두 번 · 멱등): dict 항목만 · 앞뒤 공백 제거 · 비었거나 200자(`TARGET_TEXT_MAX`) 초과는 버림 · casefold 중복 제거 · 모르는 `kind`는 `auto`. 스키마는 항목 하나가 형식 밖이라고 분해 전체를 버리지 않는다.
- **이번 task의 대상 텍스트**(M-2 ①): `targets` ∪ 파서 서버명 대상 중 등록명 간선(E2)이 잇지 못한 것(`unlinked`·`not_queried`·**`ambiguous`** → `auto` — 여러 hostname이라 잇지 않은 이름도 첫 홉으로 보내지 않는다). 파서 hostname 대상과 E2가 이은 대상은 종전 hostname 호출이고, 같은 텍스트는 다시 해석하지 않는다. 해석은 대상을 쓰는 보기(대상 필수·선택 대상·첫 홉 목록)를 고른 task에서만 한다.
- **해석 규칙**(M-2 ② · G-3 ① — 근거마다 따로 시도하고 한 근거 실패가 다른 근거를 막지 않는다):
  - `instance`·`auto` → `apm_instance_map(query=<텍스트>)`.
  - `business`, 또는 `auto`인데 0건 → `apm_instance_map(business=<텍스트>)` **∥** 업무명 간선 **E6**(폴스타 서버 등록명·비고 → hostname · 간선 소유자 어댑터 `link_business_names` · 이번 턴 사용자가 권한을 가진 DB만 — `authorized_db_ids` · 2자 이상) → **E1r** `apm_instance_map(hostname=<그 hostname>)`(그 서버에 인스턴스가 없으면 `instance_unresolved`를 조용히 0건으로).
  - 합집합 키 = (`source_id`, `domain_id`, `instance_id`) · 합치는 순서는 게이트웨이 → 간선(결정적) · 인스턴스마다 근거 문구를 단다. 인스턴스 목록 보기의 검증된 `domain_id`가 있으면 검색에 AND로 싣는다. 종전 경로가 부르는 hostname에 있는 인스턴스는 뺀다(두 번 조회 없음).
  - E6 조회는 등록명·비고 칸의 소문자 부분 일치(`LIKE … ESCAPE '!'` — 검색어의 `!`·`%`·`_`를 이스케이프) · 서버 유형·미삭제 · 1000행 상한(PG `LIMIT` / DB2 `FETCH FIRST`)이다. 장부 상태 `linked`(1·다수)·`unlinked`·`not_queried`. E6을 조회하지 못하면 장부·요약에만 남고 고지는 없다.
- **첫 홉**(D-290 ⑥): 대상 텍스트가 **하나라도 있었으면**(해석 0건 포함) 인스턴스 목록 첫 홉을 끼우지 않는다 — 말한 대상과 무관한 인스턴스를 조회하지 않는다. 선택 대상 보기도 텍스트가 있으면 대상 없이 1회로 넓히지 않는다.
- **호출**(M-4): 해석한 인스턴스를 **상한 없이 전부** 부른다(D-296 ④ — 「상한 초과 시 후보 제시」 없음). 인자는 `instance_name` + `source_ids=[<그 소스>]`이고, 인스턴스 id를 받는 도구(`apm_app_health`·`apm_runtime_health`·`apm_resource_pool`·`apm_slow_transactions`·`apm_active_services`·`apm_status_stats`·`apm_metrics` — `INSTANCE_ID_TOOLS`)는 `instance_id`를 더한다(받지 않는 도구는 (소스, 이름)당 1회). hostname은 보내지 않는다. 인스턴스 목록 보기 + 텍스트는 해석 행이 답이다(게이트웨이에 다시 묻지 않는 합성 호출 · 행에 `target_text`·`match_evidence` · 출처 인자 `{"targets": [...]}`).
- **모두 0건**: 호출이 없고 텍스트가 전부 0건이면 거절 사유 `apm_target_unresolved` · `source_status` = `not_queried`(「대상 텍스트 해석 0건」) — 답 본문은 「찾지 못함」 고지와 후보다(다른 인스턴스로 대신 조회하지 않는다). 일부만 0건이면 0건 텍스트를 `failures`(`{view: null, hostname: null, target, reason}`)로 센다(부분 결과).
- **고지**(M-6 · `disclosures[]`):

| kind | 언제 | 문구 |
|---|---|---|
| `bridge`(중립) | 해석 성공 | 「'{텍스트}' → 제니퍼 인스턴스 N개(근거: 제니퍼 인스턴스 이름 2 · 폴스타 비고 1 …)」 |
| `apm_unresolved_condition`(의무) | 0건 | 「'{텍스트}'에 해당하는 제니퍼 인스턴스를 찾지 못해 조회하지 않았습니다(검색: 제니퍼 인스턴스 이름·설명 · 제니퍼 업무명 · 폴스타 등록명·비고). 다른 인스턴스로 대신 조회하지 않았습니다. 비슷한 이름: a · b · c — 자동으로 고르지 않았습니다.」 — 「검색:」에는 답을 받은 검색만 · 후보는 게이트웨이 `suggestions` ≤3 · 검색이 모두 실패면 「…확인하지 못해 조회하지 않았습니다(검색 실패)」 |
| `apm_partial_sources`(의무) | 근거 하나가 실패(다른 근거로 계속) | 「'{텍스트}' 대상 해석: <실패 사유>」 |

  근거 문구: 제니퍼 인스턴스 이름 · 업무 수동 매핑 · 제니퍼 도메인 이름 · 제니퍼 업무 정의 · 제니퍼 인스턴스 이름·설명 · 폴스타 등록명 · 폴스타 비고(시스템 이름은 레지스트리 짧은 라벨).
- **메타**: `meta.apm_query.targets` = `{texts: [{text, kind, searched, instances, evidence, suggestions, failures}], instances: [TargetRef…]}` · 요약 끝 「· 해석 인스턴스 N개」 · 게이트웨이 `[한계]`(B2 최근 처리 한정 등)는 `limits`로.
- **대상 계약**(M-5): `TargetRef`에 `apm_instance_name`·`apm_source_id`(값 없으면 직렬화에서 빠짐 — 종전 바이트 불변). 서버 식별자 없이 `apm_instance_name`만 있어도 유효하고, 서버 대상 해소(`resolve_targets`)는 그런 대상을 `apm_only_target`으로 뺀다(폴스타 조회·프로세스·장애 조사는 건너뛴다).

## 8. 분석 계약 (W6 — 계획 §4.5 A-1~A-4를 그대로 정본으로 삼는다)

W2~W5는 원자료 계약(시각 원값 · 호출 수 · 총 응답시간 · 단위 · 해상도 · 실패 단위)을 봉투에 남겨 W6 계산이 가중 평균·누락 구간·기준 0(N/A)을 다룰 수 있게 한다. 계산은 게이트웨이(도메인 집계 — D-274 ⑤)가 하고 본체는 단계 연결·조합만 한다.

**W6 구현 범위(2026-10-06 · 실측으로 분할)**: 계산 정본은 `apm_gateway/domain/analysis.py`(순수 함수 — `weighted_mean`·`rate`·`delta`(기준 0 = `pct` None)·`rate_delta`(%p)·원시 `p95`). **A-2** 변경 전후 = `apm_change_impact`(게이트웨이 + 채팅 보기 `apm.change_impact`) · **A-1** 기간 비교 = `apm_period_compare`(게이트웨이 · 조사 소비 — 채팅 배선 없음) · **A-3** = `apm_transaction_trace`(W5). **하지 않은 것**: M-7(「하루 넘게 지난 기간」 폐지 · 해상도 자동 선택 · 빈 결과 ≠ 보존 만료 고지) · A-1 채팅 배선 · A-4(W3 `apm_fleet` 선행). **[2026-10-06 W3·W4 이후]** A-4의 선행 원자료 `apm_fleet`은 구현됐다(`ranking`·`events`). 잔여는 그대로 M-7 · A-1 채팅 · A-4 분석 보기이고 W8 · W10도 남는다(D-310 상태 줄). 근거 실측(로컬 MLX 9B 입력 파서 12문항 × 3회 · 2026-10-06): 기간 없는 질문 21/21은 `time_range = null`(변경 전후 기본 경로 · GUID · 참조 · 설정은 파서 기간을 쓰지 않는다), **상대 기간(어제·지난주·오늘 오전과 어제 오전·지난 3시간·최근 1시간) 15/15가 프롬프트 예시 날짜(2026-03-12~13)로 풀려** 현행 규칙에서 전부 「하루 넘게 지난 기간」으로 조회되지 않았다. 규칙을 폐지하면 15/15가 엉뚱한 과거를 조회한다 — `plans/122` ⑥(기준일 주입) 선행이 필요하다. 「오늘 오전과 어제 오전」은 구간 하나로만 풀렸다(두 구간 해석은 파서 계약 밖).

## 9. Wave · 파일 소유 · 인계

| Wave | 게이트웨이(`apm_gateway/`) | 본체(`src/`·`config/`·`tests/`) | 문서(팀 리드) |
|---|---|---|---|
| W0 | — | — | COV · 이 SPEC |
| W0-B | `application/jobs.py`·`spool.py` · `domain/credentials.py`(신규) · `adapters/jennifer/client.py`(우선순위 throttle · 큰 응답 스풀) · `interface/server.py`(작업 도구 · 주체 토큰 · `owner`·`wait_seconds`) · `interface/audit.py` · `config.py` · `.env.example` · tests | `apm_query.py`(owner·wait·작업 처리) · 작업 장부·서비스(신규) · `src/api/routes/apm_jobs.py`(신규) · `src/domain/disclosure.py` · 화면 작업 카드 · 매뉴얼 · tests | SPEC-apm-gateway §2.1·§3·§6 |
| W1 | `fields.py` · `tools.py` · `masking.py` · `allowlist.py`(`error_type`) · `sources.py`(호스트당 5 상한) · 카탈로그 사본 · tests | `apm_query.py`(M-1·M-2·M-3) · `src/routing/registry.py`(`ViewSpec`) · `config/db_registry.yaml` · `src/prompts/intent_planner.py`(APM 절 view_args) · `tests/test_routing/test_plan125_registry.py` 등 | docs/33 상태 |
| W2 | `allowlist.py`(status 선택 키 · 경로 변수 형식) · `api.py`·`fields.py`(v2 파서) · `tools.py`(신규 3도구) · 목 서버 · tests | 보기 5종 · 선택 재시도(M-8) · tests | |
| W5·W6·W7(2026-10-06) | `tools.py`(trace·change_impact·period_compare·예산) · `domain/analysis.py` · `api.py`·`fields.py`·`allowlist.py` · `manage_api.py`·`manage_fields.py`·`manage_tools.py`·`manage_server.py`(신규) · `credentials.py`·`masking.py`·`client.py`·`__main__.py`(감사 수정) · 목 서버·카탈로그 사본 · tests | `apm_query.py`(보기 10 · 참조 M-6 · 선택 대상 · 필수 조건 · 결정적 줄) · `registry.py`(`target`·`reference`·`required`·`default`·`targeted_choices`·형식 3) · `db_registry.yaml` · `src/domain/result_refs.py`(신규) · `context_resolver.py` · `subagents.py` · `result_aggregator.py`(V-1·표 경계) · `agent_orchestrator.py`(경과 노트) · `output_generator.py`(C-06 범위) · `intent_planner.py` · 매뉴얼 U-52 · `sre_agent` 지침 | SPEC 3종 · COV · `docs/31`·`docs/33` · D-302 |
| **W3·W4**(2026-10-06 · D-310) | `application/batch.py`·`fleet_tools.py`·`event_buffer.py`·`scope_tools.py`(신규) · `tools.py`(이름 검색 공용 함수 · `apm_metrics` domain·business · `_filter_events`) · `interface/fleet_server.py`·`scope_server.py`(신규) · `server.py`·`manage_server.py`(`targets`) · `jobs.py`(`settle_notices`) · `poller.py`(버퍼 기록) · `config.py`·`__main__.py`·`.env.example`(버퍼 설정) · `allowlist.py`·`api.py`·`fields.py`(허용 4경로 · 실시간 파서 · 기본 지표) · 카탈로그 사본 · tests | `apm_query.py`(배치·복원·named·첫 홉·소스 지목·결정적 줄) · `registry.py`(`named`·`target_arg`) · `intent_planner.py`(프롬프트·분해 정제)·`conditional_agents.py`·`schemas.py`·`replanner.py`(`sources` 운반) · `config/db_registry.yaml`(보기 6) · 매뉴얼 U-54 · `tests/test_orchestration/test_plan134_w34_*.py` | SPEC-apm-gateway §3.5 · 이 SPEC · COV · docs/31·33 · 조사 지침 한 줄(`sre_agent` `investigation_guidance.py` — D-310 ⑨) |

- **130 인계**: 130(TODO · 코드 0)이 `apm_instance_map` 인자 확장(인스턴스 이름 단계 검색·업무 해석)을 소유한다. 134 W1·W2는 `apm_instance_map`의 **반환 칸·상한만** 바꾸고 인자·정합 규칙은 건드리지 않는다. 134 W3(서비스 해석)·W4(업무)는 130 검색기를 재사용해야 하므로 **130 W1 착수 뒤**로 둔다(같은 파일 동시 수정 회피 — **130 구현 완료 뒤 W3·W4를 구현했다**: 130 판정을 `name_tier`·`search_names`·`suggest_names`로 뽑아 인스턴스·도메인·업무가 함께 쓴다).
- **132**: 소스 선택·소스 어휘(`aliases`)는 132 소유 — 134는 보기·view_args만.
- **125**: A-6 ①(순위 = `apm_fleet` W3 — **구현 2026-10-06**) · ②(다건 hostname = M-5 W3 — **구현 2026-10-06** · 증거 `tests/test_orchestration/test_plan134_w34_apm.py::test_plan125_a6_2_twelve_hostnames_one_batch_per_view`) · ③(PID 연계 = `apm_config(kind=process_instance)` W7) · A-8(매뉴얼)을 134가 수행하고 125 장부에 증거를 남긴다.
- **121**: F-13(2단 → 조사 위임) 소유. 134는 확장 도구·참조·권한 계약과 통합 테스트를 제공한다 — 연동 전 134 전체 완료 선언 금지.

## 10. 테스트 · 수용 대응

| 수용(계획 §6) | 증거 |
|---|---|
| ① COV 누락 0 | `spec/CAPABILITY-MAP-134.md` §8(현재 0 — Wave 배정 기준) · Wave마다 구현 상태 칸 갱신 |
| ③ 잘리지 않음 | 게이트웨이 계약 테스트: 목 서버 대량 합성(인스턴스 1,200 · 이벤트 5,000 · X-View 60분) → `total_row_count` = 원천 수 · 청크 합 = 원천 수 · 순서 보존 |
| ④ 장기 작업 | 가상 시계·지연 목 서버: 10초 초과 → 작업 핸들 · 120초 초과 작업 완료 · 취소 · 정체 · 재기동(`interrupted`) · 소유자 불일치 `job_not_found` · 만료 정리 · 본체 작업 API 소유자 403/404 · 다운로드 감사 |
| ⑥ 자격증명 카나리아 | §4.2 카나리아 전 형태 × (도구 반환 · 스풀 · 감사 · `limits` · 오류 사유) 부재 단언 |
| W3·W4 수용(D-310) | 독립 검증 실프로세스 종단(게이트웨이 ↔ MCP SSE ↔ 본체 2단 · 가짜 LLM · 목 Open API 2소스) E1~E8 성립 — hostname 12 × 보기 4 배치 1호출 · 전 대상 순위 독립 오라클 대조 · 이벤트 실패 도메인 ≠ 0건 · 서비스·업무 현재값·추세 · 배치 작업 승격 · `sources` 지목 · 첫 홉 전부 · 대상 1개 = 종전 모양 · 정적 계약 대조 336사례(본체 실제 인자 403호출 — 모르는 키·빠진 필수·형 불일치 0) · 적대 58건 · 자격증명 카나리아 배치 경로 0 |
| ⑦ 통제 유지 | 허용목록 정본 ↔ 사본 · 비GET·`token`·리다이렉트·형식 밖 경로 변수 HTTP 0회 · 비활성 렌더 바이트 불변 |
| ⑧ 게이트 | `arch_check --ci` · `overfit_check --ci` · `apm_gateway/tests/test_boundary.py` · ruff·mypy(기준선 대조) · 본체·게이트웨이·조사·noise_gate 테스트 · D-255 매뉴얼 |

## 11. 측정하지 못한 것 (W10 · 외부 전제)

**[W3·W4]** 무인자 `/api/realtime/domain`이 전 도메인을 돌려주는지 · `/api/realtime/business`의 `business_id` 존중 · 이벤트 구간 양끝 포함 · 이벤트 `time` 축 · 늦게 들어오는 이벤트가 60초 겹침 안인지 · 빈 결과 모양 · 같은 이벤트의 중복 응답 · 기본 지표 3종이 운영 카탈로그에 있는지 · 버퍼 20만 건 실제 메모리 · 실 속도(5회/초) 아래 대상 수백 개·350도메인 소요(예상 시간은 호출 수/속도 모델일 뿐) · 로컬 MLX 9B 분해 실측(보기 14문항 중 13 정답 — 「서비스 시간대별 추세」를 `apm.app_stats`로 고른 1건)은 로직 확인용이고 선택 정확도·지연은 내부망 FabriX 측정 잔여 · 3단 경로의 `targets`·`sources` 운반 미구현 · 운영 사다리 1단(D-251)이라 채팅 효력은 2단 전환 뒤. 실 제니퍼 응답 shape·단위·보존 기간·`sort_by_metrics`·`interval_minute` 허용값 · 운영 호출 속도(G-12) · 큰 응답의 실제 크기 · 운영 도메인 350개 실소요 · 개인정보 원값 정책(G-11) · v2 매뉴얼 원천 불일치(COV E-01·E-02). 로컬 Docker 제니퍼는 라이선스가 없어 도메인 0건이다 — 계약 테스트는 스펙 기반 합성 픽스처다.

## 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-02 | 최초 작성(W0) — C-1~C-18 재대조 · COV 신규 발견 반영(새 GET 3 · 경로 변수 형식 · v2 봉투 · 마스킹 결함 · 자격증명 규칙 부재) · MCP 1.29.1 진행·취소 시그니처 실측과 작업 도구 방식 판단 · 공통 인자·봉투 · 장기 작업·스풀·주체 토큰 · 자격증명·식별자 규칙 · 도구 표면 · 보기 25 · view_args·재선택 · 본체 흐름·고지 kind · Wave 소유·인계 |
| 2026-10-02 | W0-B 반영 — §2.2 `artifact.chunks` 모양·`job.error` · §3.7 읽기 기본값·구현 세부 · §3.8 본체 구현 세부(재확인 마감·첫 홉·혼합 결과·partial 상태·감사·중첩 마스킹·카드 복원) · §4.2 자격증명 규칙 확대(검증·보안 감사 발견 High 2·Medium 5 반영: 붙여 쓴 비밀 단어 · 헤더 줄 끝까지 · 세션 쿠키 · 키-값 묶음 모양 · 가린 뒤 자르기 · 제곱 시간 정규식 · 정규화 · 숫자 세션 ID 보존) |
| 2026-10-02 | W1·W2 반영 — §5 도구 표(`apm_events` `n` 기본 전부·`level` 계약 값·`error_type` 접두 변형 재조회 · `profile_truncated` · `apm_status_stats` 전역 재정렬·원자료 칸 · `apm_metrics` 카탈로그 행·scope Wave · `change_detected_ms`) · §6.1 확장 칸(`label`·`text`·`notices`·`active_only`) · §6.3 선택 조건 무효는 고지 후 조회(H-2)·`null` 무고지·평면 view_args·골격 키(H-1)·`limit→n` 단일 task 한정·`level` enum · §6.4 일부 영역만 덮으면 덮은 보기 조회 · §7.1 `**판정·집계**` 블록·APM 표시 절단 |
| 2026-10-02 | W2 검증 반영 — §6.1 `text` 거부 범주·식별자 형식(영문 시작) · §6.3 원천·게이트웨이가 거부한 선택 조건 처리(빼고 조회·로컬 정렬 · 고지) · §6.4 영역별 후보 · 합의 보기(`agreed`) · §7.5 비의무 고지 본문 3줄 · 게이트웨이 고지 통과 |
| 2026-10-06 | **W5·W6(독립분)·W7 반영(D-302)** — §1.4 정정(`prior_rows`·`previous_entities`는 참조 칸을 나르지 않는다) · §2.4 `sint`·`.xml` 꼬리 거부 · §3.6 프로파일 예산 주체 분리 · §4.2·§4.3 W7 개정(보안 감사 AUDIT-1~12 · 재감사 REAUDIT-1·2·4·7 반영 · 남긴 모양) · §5 도구 7종 구현 행 · §6.1 `ViewSpec`·`ViewArgSpec` 확장 · §6.2 보기 10종 · §7.7 앞 결과 행 참조(표 단위 번호 · 형식 밖 `ref` 되묻기 · 단계별 답 · GUID 조회 구간) · §8 W6 분할 실측(파서 상대 기간 15/15 예시 날짜) · §9 W5~W7 소유 |
| 2026-10-06 | **`plans/130` W3·W4 반영** — §7.8 대상 텍스트 해석(분해 칸 `targets` · E2 미연결·모호 서버명 포함 · 인스턴스 이름 검색 → 업무명 ∥ 업무명 간선 E6 → E1r 합집합 · 대상 텍스트가 있으면 첫 홉 없음 · 해석 인스턴스 전부 호출(상한 없음 · D-296 ④) · `instance_name`+`instance_id`+`source_ids` · `apm_target_unresolved` · 고지 `bridge`·`apm_unresolved_condition`·`apm_partial_sources` · `TargetRef.apm_instance_name`·`apm_source_id`) — 게이트웨이 계약은 `spec/SPEC-apm-gateway.md` §3.4 |
| 2026-10-06 | **W3·W4 반영(D-310)** — §5 도구 표(`apm_service_status`·`apm_fleet`·`apm_business` 구현 행 · `apm_metrics` domain·business · `targets` 행 · `gateway_health.poller.event_buffer`) · §6.1 `ViewSpec.target: named`·`target_arg` · §6.2 보기 6종(`apm.service`·`apm.service_trend`·`apm.ranking`·`apm.fleet_events`·`apm.business`·`apm.business_trend` — 레지스트리 22 → 28) · §7.4 복수 보기·대상(절단 제거 · 배치 1호출 · 대상별 복원 · 결과 파일 표지 · 첫 홉 전부 · named 보기 · 전 대상 보기의 대상 텍스트 · 소스 지목 `sources` · 결정적 줄 · 활성 프롬프트 지문) · §8 이후 상태 · §9 W3·W4 소유 · §10 수용 증거 · §11 W10 잔여 — 게이트웨이 계약은 `spec/SPEC-apm-gateway.md` §3.5 |
