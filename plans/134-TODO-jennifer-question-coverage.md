# 134. 제니퍼 질문 전량 지원 — `docs/33` 사용 사례의 질문을 모두 채팅에서 답하게 한다 (채팅 결함 교정 · 읽기 API 전부 · 서비스·전체 범위 · 업무 단위 · 트랜잭션 심층 · 장기 추세 · 설정·관리·민감 조회)

> **작성일**: 2026-10-02 · **v1.0** · **v1.1**: 2026-10-02(사용자 결정 **D-296** — *"모든 api는 허용하고 조회할 수 있는 범위는 모두 가능하도록 정한다."* · 확인 응답 「민감 조회 API까지」·「보호 한도도 올림」 · 나머지 게이트 「따로 검토」)
> **상태**: **TODO(계획 · 코드 0)** — **D-296 확정**(G-2·G-3·G-4·G-5·G-6·G-8 — 읽기 API 전부 허용 · 쓰기·제어 차단 · 범위 상한 제거 · 호출 속도 상향) · **남은 게이트**: G-1·G-7·G-9·G-10(사용자 재설명 요청 — §10.2) · 신설 G-11(민감 조회를 누가·어떻게 보나) · G-12(호출 속도 값 — 제니퍼 운영 협의) · G-13(큰 조회를 어떻게 돌려주나 — D-267 ⑦ 처리 상한과의 관계)
> **성격**: 사용 사례 정리(`docs/33`) + 현행 실측(게이트웨이 · 본체 `apm_query` · 레지스트리 보기) + 제니퍼 Open API 5.6.4 스펙·v2 매뉴얼 재파싱 + 설계 + 단계별 구현 계획
>
> **요청 원문(2026-10-02)**: *"조사한 사용가능한 질문들은 docs폴더의 사용 사례를 별도로 정리하고 사용가능한 질문을 모두 사용할 수 있도록 구현할 계획을 plans폴더에 계획파일을 정리하라."* ·
> (v1.1) *"모든 api는 허용하고 조회할 수 있는 범위는 모두 가능하도록 정한다."*
>
> **산출 짝**: 사용 사례 = **`docs/33_jennifer_use_cases.md`**(질문 ID S-01~S-11 · O-01~O-17 · F-01~F-15 · 불가 X-01·X-02·X-03·X-05). 이 계획의 「질문 ID」는 그 문서를 가리킨다.
> **상위·인접 계획**: `plans/87`(게이트웨이 `apm_gateway/` 소유 · J0-L-b·J0-O 실데이터) · `plans/125`(본체 2단 처리기 `apm_query` · 레지스트리 보기 · A-6·A-8 잔여) · `plans/130`(인스턴스 이름·업무명 대상 해석 — TODO) ·
> `plans/132`(데이터 소스 우선 선별 · 소스별 유사어 — D-293·D-295) · `plans/121`(2단 조사 위임 소유) · `plans/123`(고지 `disclosures[]`)
> **관련 결정**: **D-296**(이 계획의 범위 결정) · D-003 · D-004 · D-120 · D-122 · D-162 · D-195 · D-232 · D-240 · D-251 · D-255 · D-262 · D-264 · D-265 · D-267 · D-274 ⑤ · D-280 · D-281 · D-283 · D-285 · D-287 · D-290 · D-293 · D-295
> **실측 기준**: 2026-10-02 · `multiintent` HEAD `22f9628` + 작업 트리(미커밋 다수 — `apm_query.py`·레지스트리·분해 프롬프트를 병행 세션이 고치고 있다 · 착수(W0) 때 `file:line` 재실측) · 스펙 = `plans/87` [J-23] 원천(`jennifer5-open-api` gh-pages `index.html`)을 다시 받아 파싱(39경로 · 63오퍼레이션 · `info.version` 5.6.4) · v2 매뉴얼 `spec/*.md` 8건(deploy · environment-variable · loaded-class · active-service-detail · active-service-color-range-boundary · db-path · instance-list-by-process-id · manage-rule-event). **실 제니퍼 서버 호출 0 · LLM 호출 0.**

---

## 0. 요약

**(1) 요구** — `docs/33`의 답할 수 있는 질문 **43건**(서비스 제공 현황 11 · 운영 17 · 장애 15)을 **채팅(2단 기준 경로)에서 묻고 답하게** 한다. D-296으로 제니퍼 Open API의 **읽기 경로 전부**(민감·관리 조회 포함)를 열고 **조회 범위의 자체 상한을 없앤다**. 쓰기·제어(조치·룰 변경)와 API가 없는 것(토폴로지·제니퍼 자체 AI)은 범위 밖이다(§1).

**(2) 지금과 이 계획 뒤** (`docs/33` 상태 칸 집계 · 2026-10-02)

| 상태(지금) | 건수 | 질문 ID | 이 계획 |
|---|---:|---|---|
| ✅ 된다 | 10 | S-01·S-02·S-03 · O-01·O-05·O-06 · F-01·F-11·F-12·F-14 | 회귀 없이 유지 · 상한 제거(W1) |
| ◐ 일부만 | 8 | S-04·S-05·S-08·S-10 · F-02·F-03·F-05·F-10 | **W1**(채팅 결함 교정) · W2 · W3 |
| 🔎 조사에서만 | 5 | O-02·O-04 · F-06·F-08·F-13 | W1(O-02·O-04·F-08) · W5(F-06) · F-13은 `plans/121` |
| 🔶 허용됐지만 도구 없음 | 6 | O-03·O-07·O-08·O-09·O-10 · F-09 | **W2** |
| ➕ 허용 확정(D-296) · 구현 전 | 13 | S-06·S-07·S-09 · O-11~O-17 · F-04·F-07·F-15 | W3 · W4 · W5 · **W7**(설정·관리·민감) · W1(F-04) |
| ✖ 지금 불가 | 1 | S-11 | **W6**(D-296이 하루 규칙 폐지) |
| **합계** | **43** | | 정확한 상태·사유는 `docs/33` 표가 정본이다 |

**(3) 한 줄 설계** — 제니퍼 읽기 API는 전부 게이트웨이 허용목록에 넣고(쓰기·제어는 계속 막고 자격증명 값은 버린다), 채팅은 **보기(닫힌 어휘)와 보기별 고정 인자**로 고른다. **조회 범위는 줄이지 않고** 비용은 병렬 호출·호출 속도 상향·진행 표시·요약 + 전체 CSV로 다룬다. 가장 값싼 이득은 **이미 받은 응답을 버리지 않는 것**이다(W1 — API 추가 0).

**(4) 권고 순서** — W0(재실측·재현) → **W1(채팅 결함 교정)** → W2(허용됐지만 놀던 API) → W3(서비스·전체 범위) → (W4 업무 ∥ W5 트랜잭션 심층) → W6(장기 추세) → W7(설정·관리·민감 조회 — G-1·G-11 뒤) → W8(보기 선택 신뢰성 — G-10 뒤) · W9(문서·매뉴얼·골드)는 Wave마다 같은 작업에서 · W10은 외부 전제(실데이터).

---

## 1. 요구 해석

| # | 요구 | 해석 |
|---|---|---|
| R-1 | 「사용가능한 질문」 | 제니퍼 Open API(스펙 5.6.4 + v2 매뉴얼의 읽기 경로)에 **답이 되는 데이터가 있는 질문**. `docs/33` S·O·F 43건이다 |
| R-2 | 「모두 사용할 수 있도록」 | 채팅에서 묻고 답한다(2단 기준 경로 `apm_query`). 장애 조사(`sre_agent`)는 이미 도구를 직접 부르므로 새 인자 사용 지침만 더한다 |
| R-3 | **「모든 api는 허용」(D-296 ①②)** | 제니퍼 Open API의 **읽기(GET) 경로 전부** — 서비스·업무·GUID·민감 조회(사용자 목록·환경변수·로드된 클래스·실행 중 요청 상세)·관리 조회(이벤트 룰·데이터 서버·DB 경로·프로세스 → 인스턴스). **쓰기·제어 API는 계속 막는다**(사용자가 「쓰기·제어까지 전부」를 고르지 않았다 · D-003). v1의 POST 변형·`.xml` 변형은 같은 데이터의 다른 표현이라 넣지 않는다 |
| R-4 | **「조회할 수 있는 범위는 모두 가능」(D-296 ④⑤)** | 기간·대상·건수의 **자체 상한을 없앤다**(창 10분·24시간 · 하루 넘은 기간 미조회 · 목록 200 · 대상 10 · `n` 20 · 이벤트 50 · 추세 인스턴스 2). 남는 한계는 제니퍼 쪽 사실(보존 기간 · API의 창·단위 제약 · 호출 속도)뿐이고, 호출 속도 상한은 올린다(값 G-12) |
| R-5 | 범위 밖 | X-01 토폴로지(API 없음) · X-02 조치(D-195 ③ L2 = `plans/87` J6) · X-03 룰·임계 **변경**(쓰기 — 조회는 O-11로 들어왔다) · X-05 제니퍼 자체 AI(API 없음). F-13(채팅 → 조사 위임 2단 배선)은 `plans/121` 소유(D-270 ⑯) — 이 계획은 통지만 한다 |

**불변(D-296 ③)** — 자격증명 값(`/restapi/users`의 `password` · 환경변수·시스템 속성의 비밀 패턴 값)은 어떤 경로(도구 결과 · 채팅 · CSV · 로그 · 감사)로도 내보내지 않는다. 개인정보를 누가 어떻게 보는지는 G-11이다.

**가정(틀리면 알려 주세요)**
- **A-1** 사용자가 말하는 「OO 서비스」는 제니퍼 **도메인**이다(`docs/31` §2 「도메인 = 에이전트 묶음(서비스·업무 단위)」). 운영 도메인 이름이 서비스명인지는 `plans/87` J0-O에서 확인한다. 업무 단위로 묻는 경우는 S-09로 따로 다룬다.
- **A-2** 운영 채팅이 2단으로 전환되는 것은 사용자 몫이다(D-251 · 운영 `.env`는 2026-08-31 실측 1단). 1단(`deep_agent`)에 제니퍼 처리기를 붙이는 일은 범위 밖이다.

---

## 2. 현행 실측 (2026-10-02)

### 2.1 채팅이 이미 받은 것을 버린다 · 보기 설정이 기능을 막는다

| # | 위치 | 실측 | 영향(질문 ID) |
|---|---|---|---|
| **C-1** | `src/orchestration/apm_query.py:451` `_collect` | 봉투에서 `rows`와 `limits`만 옮긴다. 게이트웨이가 함께 주는 **`summary`**(p50·p95·에러율·SQL/외부 호출 비중 · 실행 모드별 액티브) · **`hourly`**(시 단위 통계·상위 애플리케이션) · **`errors_by_type`**(오류 유형별 상위 10) · **`was_signals`**(WAS 판정 8종)를 버린다. `src/` 전체에 `was_signals` 소비처 0건(grep) | F-03 · F-05 · F-08 · O-04 · O-06 · S-04 · S-10 |
| **C-2** | `config/db_registry.yaml:92-99` · `apm_query.py:156` `plan_window` | `apm.runtime`·`apm.slow_tx`에 `window_max_minutes`가 없다 → `plan_window`가 `current`를 돌려 **기간 인자를 넘기지 않는다**. `apm_runtime_health`는 기간이 없으면 추세를 조회하지 않고(`apm_gateway/apm_gateway/application/tools.py:476`), `apm_slow_transactions`는 기본 10분이다. 기간을 말하면 「현재값 기준입니다(기간 조회를 지원하지 않는 보기)」라고 **잘못 고지**한다 | O-02 · O-04 · F-05 |
| **C-3** | `config/db_registry.yaml:89-91` | `apm.app_health` `window_max_minutes: 10` → 게이트웨이의 시 단위 보충(`tools.py:340` `_hourly` — 창 > 10분일 때만)에 **채팅이 도달하지 못한다** | S-04 · S-10 |
| **C-4** | `apm_query.py:262` `_call_args` | 넘기는 인자는 `hostname`·`thread_id`·창뿐이다. 입력 파서의 `limit`(「상위 5개」 — `src/prompts/input_parser.py:55`)은 `n`으로 가지 않고(기본 10), 이벤트 최소 레벨 `level`을 실을 칸이 없다 | O-06 · F-02 · F-05 |
| **C-5** | `apm_gateway/apm_gateway/adapters/jennifer/fields.py:134` `parse_realtime` | `RealtimeInstanceData`의 `visitDay`·`visitHour`·`hitDay`·`hitHour`·`activeServiceRangeCount0~3`·`instanceDescription`을 읽지 않는다(스펙 필드 36개 중 지표는 `METRIC_FIELDS` 17개만) | S-05 |
| **C-6** | `fields.py:165` `parse_transaction` · `:217` `parse_application_status` · `:145` `parse_active_service` | `TransactionData.guid`를 버린다(F-07 입력). `ApplicationStatus`는 25필드 중 6개만 읽는다. `ActiveServiceData`의 `sessionId`·`threadHash`를 버린다(F-15 실행 중 요청 상세의 필수 인자) | F-07 · F-15 · S-10 |

### 2.2 허용목록 · 자체 상한

| # | 위치 | 실측 | 영향 |
|---|---|---|---|
| **C-7** | `allowlist.py:47-70` · `adapters/jennifer/api.py` | 허용 16템플릿 중 **4개를 부르는 코드가 없다**: `/api/metrics` · `/api/status/sql` · `/api/status/external_call` · `/api-v2/deploy/{domainId}`(grep — `allowlist.py`에만 등장) | O-03 · O-07~O-10 · F-09 |
| **C-8** | `allowlist.py` 경로별 쿼리 키 | 스펙 선택 키 중 허용 밖: `/api/status/sql`·`external_call`의 `instance_id`·`sort_by_metrics`·`max_row` · `/api/status/application`의 `sort_by_metrics`·`application_name` · `/api/dbsearch/error`의 `error_type` | O-07 · O-08 · S-10 · F-04 |
| **C-9** | 스펙 5.6.4 · v2 매뉴얼 | 허용목록 밖 읽기 경로: 서비스·업무(`/api/realtime/domain` · `/api/dbmetrics/domain` · `/api/realtime/business` · `/api/dbmetrics/business`) · `/api/transaction/guid` · 민감 조회 6 · 관리 조회 8(§3.2) — **D-296이 전부 허용으로 정했다** | S-06·S-07·S-09 · F-07·F-15 · O-11~O-17 |
| **C-10** | `apm_gateway/tests/test_allowlist.py` | 민감 GET 9건을 「HTTP 0회로 거부」로 단언한다(`/api/auth/userlist` · `/restapi/users` · `/restapi/user/1` · `/api-v2/environment-variable/1000` · `/api-v2/active-service/detail/1000/1` · `/api-v2/loaded-class/1000/1` · `/api-v2/manage/data-server/system-property-config` · `/api-v2/manage/rule/event/error/1000` 등 — `docs/31` §5.3) | D-296으로 **허용 단언으로 뒤집히는 테스트** — 쓰기·변형 거부 단언은 그대로 |
| **C-11** | `tools.py:48-58` 상수 · `:384` 목록 200 · `apm_query.py:60` `MAX_VIEWS = 3` · `COMPOSITE_MAX_TARGETS`(10) | 자체 상한: `XVIEW_MAX_MINUTES` 10 · `EVENTS_MAX_MINUTES` 1440 · `EVENT_ROWS_MAX` 50 · `N_MAX` 20 · `TREND_MAX_INSTANCES` 2 · 추세 지표 3종 고정 · `PROFILE_LINES_MAX` 60 · `PROFILE_CHARS_MAX` 4000 · 인스턴스 목록 200 · 대상 10 · `apm_query.py:66` `_OUT_OF_WINDOW_AFTER` 1일(D-283 ④) | **D-296 ④로 제거 대상**(조사 쪽 프로파일 조사당 5회·정체 가드는 유지) |
| C-12 | `apm_gateway/apm_gateway/application/masking.py` | IP 끝 두 옥텟 · URL 쿼리 값 · SQL 리터럴 · 자유 텍스트의 이메일·주민번호·휴대폰·IP를 가린다. **자격증명 필드(`password`)·비밀 패턴 환경변수를 버리는 규칙은 없다**(그런 응답을 받은 적이 없다 — 민감 GET 거부) | D-296 ③ 신규 규칙 필요 |

### 2.3 전체 범위 · 시간 · 경로

| # | 위치 | 실측 | 영향 |
|---|---|---|---|
| **C-13** | `apm_query.py:402` `_insert_instances_step` · 레지스트리 `required_input: hostname`(6보기) | 대상이 없으면 인스턴스 목록 **앞 10대**를 골라 조회한다 — 「가장 느린 WAS」·「fatal 난 WAS 전부」는 순위·전수가 아니다. `plans/125` A-6 ①(도메인 단위 순위 도구)은 87 수용 대기 | S-08 · F-10 |
| **C-14** | D-195 부기(운영 실측) · `docs/31` §5.6 | 운영 제니퍼 도메인 **약 350개** · 게이트웨이 호출 상한 5회/초 → 도메인 전수 1회 ≈ 70초 · 토큰 사용량은 500 응답·타임아웃까지 센다 | 전체 범위 질문의 시간(G-12·G-13) |
| **C-15** | `src/config.py:194` `MCP_SOURCE_CALL_TIMEOUT` 10초 · `:527` `query_timeout` 120초(D-267 ⑦ — **요청 후 첫 답변까지**의 상한) · `sse_progress_events`(D-204 진행 신호 · 기본 on) | 분 단위로 걸리는 제니퍼 조회는 지금 구조로는 첫 답변 상한에 걸린다. 진행 신호 경로는 있다 | **G-13** |
| C-16 | `src/orchestration/conditional_agents.py` · `subagents.py:1913` `resolve_subagent` · `deepagents_tools.py:463` | `apm_query`는 2단 처리기(조건부 등록)다. 3단은 계획 루프(`TIER3_PLAN_LOOP_ENABLED` · 기본 off)에서만 같은 처리기를 찾고, 1단은 고정 목록만 도구로 만든다 | A-2 |
| C-17 | 레지스트리 보기 7종 · 분해 APM 절(`src/prompts/intent_planner.py:428`) · `plans/132` §6.6 | 보기 표는 레지스트리에서 렌더한다. 로컬 9B는 보기 표를 받고도 `views`를 4/4 비웠다(`plans/132` O-3). 132 계약: 소스 안 어휘(S2 — 보기 라벨)는 **계획 LLM에 렌더되는 재료일 뿐**이고 단어로 소스를 고르지 않는다 | G-10 |
| C-18 | `tests/test_routing/test_plan125_registry.py:44-48` | 보기 목록 순서와 `apm.app_health` `window_max_minutes == 10`을 단언한다 | 보기·창 변경 시 갱신 대상(Known Mistakes — 상수 단언 repo 전체 grep) |

---

## 3. 근거 — 새로 쓰는 제니퍼 API (스펙 5.6.4 · v2 매뉴얼 재파싱 2026-10-02)

### 3.1 서비스·업무·거래 · 기존 경로의 선택 키

| 경로(GET) | 필수 · 선택 | 응답 핵심 필드 | 질문 |
|---|---|---|---|
| `/api/realtime/domain` | — · `domain_id` | `domainId`·`domainName`·`tps`·`responseTime`·`activeService`·`activeUser`·`concurrentUser`·`rejectRate`·`visitDay`·`visitHour`·`hitDay`·`hitHour`·`activeServiceRangeCount0~3` (+`ipAddress`·`port`) | S-06 · S-08 |
| `/api/dbmetrics/domain` | `domain_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | `[{time, value}]` · 도메인 지표 41종 · 지표 1개/호출 | S-07 |
| `/api/realtime/business` | `domain_id` · `business_id` | `businessId`·`businessName`·`tps`·`responseTime`·`activeService`·`concurrentUser` | S-09 |
| `/api/dbmetrics/business` | `domain_id`·`business_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | `[{time, value}]` · 업무 지표 29종 | S-09 |
| `/api/transaction/guid` | `domain_id`·`guid`·`start_time`·`end_time` | `TransactionData[]`(`clientIp`·`userId`·`clientId` 포함) | F-07 |
| `/api/status/sql` · `/api/status/external_call` | `domain_id`·`start_time`·`end_time`(시 단위) · `instance_id`·`sort_by_metrics`(기본 calls)·`max_row` | `name`(SQL 문 · 외부 호출 대상)·`calls`·`failures`·`badResponses`·`responseTime`·`maxResponseTime`·`totalResponseTime` | O-07 · O-08 |
| `/api/status/application` | 같음 · + `application_name` | 25필드 | S-04 · S-10 |
| `/api/dbsearch/error` | 같음 · + `error_type`(대문자) | `ErrorData` | F-04 |
| `/api/metrics` | — | `{domain[41], instance[60], business[29], application, sql, externalCall}` | O-03 · O-10 |
| `/api-v2/deploy/{domainId}` | `startTime`·`endTime`(25시간 이하) | `[{collectTime, instanceId}]` — 「소스코드(리소스) 변경 인지 시각」 | O-09 · F-09 |

### 3.2 설정·관리·민감 조회 (D-296 ①로 허용)

| 경로(GET) | 인자 | 응답(매뉴얼 예) | 민감 정보 | 질문 |
|---|---|---|---|---|
| `/api-v2/manage/rule/event/error/{d}` | 경로 `domainId` | `errorType`·`applied`·`level`·`checkTimeRange`·`thresholdErrorCount`·`iconRecoveryTime`·`customMessage`·`autoScriptCommand` | `autoScriptCommand`(서버 스크립트 경로) | O-11 |
| `/api-v2/manage/rule/event/metric/{d}/{대상}` · `…/compare/{d}/{대상}` | 대상 `domain`·`instance`·`business`(compare는 앞 둘) | `metricId`·`level`·`applied`·`expression`(예 `value>30`)·시간 값(ms) | 같음 | O-11 |
| `/api-v2/manage/rule/active-service-color-range-boundary` | — | 경과 시간 경계 4색(파랑·연두·주황·빨강) | 없음 | O-12 |
| `/api-v2/environment-variable/{d}` | 경로 `domainId` | `SYSTEM`(OS 환경변수) · `JAVA`(시스템 속성) | **비밀번호·토큰이 값으로 들어 있을 수 있다** | O-13 |
| `/api-v2/loaded-class/{d}/{i}` | `search`(클래스 이름 일부 · 선택) | `className`·`superClassName`·`interfaceClassNames`·`classLoaderName` · 6만 개 이하일 때만 | 낮음 | O-14 |
| `/api-v2/manage/instance` | `processId`(필수)·`hostname` | `{instanceId: {hostname}}` · Java 5.6.0.8+ | 없음 | O-15(호스트+PID → 인스턴스 · `plans/125` A-6 ③) |
| `/api-v2/manage/data-server/domains` · `…/resource` · `…/system-property-config` · `/api-v2/manage/db/path/{d}` | — · 경로 `domainId` | 데이터 서버별 도메인 배치 · CPU·메모리·디스크 · 시스템 속성 설정 · 저장 경로 | 설정에 비밀 값 가능 | O-16 |
| `/api/auth/userlist` | — | `id`·`name`·`email`·`phoneNumber` | **이메일·휴대폰** | O-17 |
| `/restapi/users` · `/restapi/user/{id}` | 경로 `id` | `id`·`name`·`group`·`allowIp`·**`password`** | **비밀번호 — 항상 버린다(D-296 ③)** | O-17 |
| `/api-v2/active-service/detail/{d}/{txid}` | `sessionId`·`threadHash`(액티브 서비스 목록 행의 값) | `userId`·`guid`·`sql`·`http{method, query}` 등 | **사용자 ID · SQL · HTTP 파라미터** | F-15 |

- `sort_by_metrics`·`interval_minute` 허용값 · 보존 기간 · 응답 실모양은 공개 자료에 없거나 미검증이다(`plans/87` U-2·U-3 · W10).
- 넣지 않는 것: v1 POST·`.xml` 변형(같은 데이터) · `/api-v2/test-response/json` · `/api-v2/auth-test`(시험 경로 — 데이터 없음) · 모든 쓰기·제어(GET 아님).

---

## 4. 설계

### 4.1 원칙

1. **읽기 API 전부 · 쓰기 0**(D-296 ①②) — 허용목록 방식(메서드 + 경로 정확 일치 · 코드 상수 · 그 밖 거부 · 쿼리 `token` 거부 · 리다이렉트 비추종)은 그대로 두고 목록만 넓힌다.
2. **자격증명 값은 어디로도 나가지 않는다**(D-296 ③) — 게이트웨이 응답을 만들기 전에 버린다(필드 제거·값 가림). 개인정보의 표시·역할은 G-11.
3. **조회 범위를 줄이지 않는다**(D-296 ④) — 기간·대상·건수 상한을 걷어낸다. 비용은 병렬 호출 · 호출 속도 상향(G-12) · 진행 표시 · 큰 결과는 요약 + 전체 CSV로 다룬다(G-13).
4. **LLM은 닫힌 어휘만 고른다** — 보기 id · 보기가 선언한 선택지. 대상 해석·창·인자 조립은 코드다(`plans/125` Q-2 · D-004).
5. **받은 것은 버리지 않는다** — 봉투의 집계·판정을 채팅 결과까지 옮긴다(C-1). 판정 문구는 결정적이다(LLM 0).
6. **비활성 배포는 바이트 불변** — APM 엔드포인트가 없으면 처리기·분해 절이 없다(D-283 ② · D-251 ⑥). 신규 `enable_*` 0.
7. **고지는 `plans/123` `disclosures[]` 규약** — 시 단위 통계 · 현재값 · 보존 기간 · 「배포가 아니라 소스 변경 감지」 · 가린 값.

### 4.2 도구 표면 — 인자 확장표 (도구 배치는 **G-1**에 따른다)

| 도구 | 추가 인자(값만) | 새 뒷단 | 질문 ID |
|---|---|---|---|
| `apm_instance_map` | (`plans/130`: `query`·`business`) | — | — |
| `apm_app_health` | `scope` = `instance`(기본 · 현행) · `service` · `ranking` · `business` / `service?`·`rank_by?`(`response_time`·`tps`·`active_service`·`error_rate`)·`n?` / `scope≠instance`면 `hostname` 생략 가능 | `/api/realtime/domain` · `/api/dbmetrics/domain` · `/api/realtime/business` · `/api/dbmetrics/business` | S-05~S-09 |
| `apm_runtime_health` | `metrics?`(인스턴스 지표 카탈로그 안 이름 — 개수 제한 없음) | `/api/metrics`(이름 검증) | O-02 · O-03 · S-11 |
| `apm_resource_pool` | — | — | — |
| `apm_slow_transactions` | `breakdown` = `transactions`(기본) · `applications` · `sql` · `external_call` / `sort_by?` · `application?` · `guid?` | `/api/status/{application·sql·external_call}`(선택 키) · `/api/transaction/guid` | S-10 · O-07 · O-08 · F-07 |
| `apm_active_services` | `detail?`(행의 `txid`·`session_id`·`thread_hash`) | `/api-v2/active-service/detail/{d}/{txid}` | F-15 |
| `apm_events` | `error_type?` · `kinds?` = `event`·`error`·`change` · `scope=all`(대상 없이) | `/api-v2/deploy/{domainId}` · `/api/dbsearch/error` `error_type` 키 | F-04 · F-09 · F-10 · O-09 |
| `apm_transaction_profile` | (인자 변경 없음 · 채팅에서도 사용) | — | F-06 |
| **설정·관리·민감 조회**(G-1 ①이면 기존 도구 인자로 흩고, ②면 새 도구 1개 `apm_config` 가칭) | `kind` = `event_rules` · `color_boundary` · `environment` · `loaded_classes` · `process_instance` · `jennifer_server` · `users` / `search?`·`process_id?` | §3.2 경로 전부 | O-11~O-17 |

- `scope`·`breakdown`·`kinds`의 기본값은 현행 동작과 같다 — `sre_agent`·`noise_gate`(현행 소비자)는 인자를 바꾸지 않으면 결과가 그대로다(계약 테스트로 고정).
- 도구 설명문은 벤더 중립을 유지한다(`apm_gateway/tests/test_server.py`).

### 4.3 게이트웨이 (`apm_gateway/` — 87 패키지 · 실행 주체는 G-9)

| # | 내용 | Wave |
|---|---|---|
| N-1 | **필드 확장** — `parse_realtime`에 `visit_day`·`visit_hour`·`hit_day`·`hit_hour`·`active_range_0~3`·`instance_description` · `parse_transaction`에 `guid`(+ `plans/130` N-4 업무 필드와 같은 작업에서) · `parse_application_status` 25필드 · `parse_active_service`에 `session_id`·`thread_hash` · 벤더 리터럴은 `adapters/jennifer/`에만 | W1 |
| N-2 | `apm_app_health` 행에 방문·호출 수 — 필드 의미(「오늘」의 기준 시각)는 실데이터 확인 전까지 `[한계]`로 적는다(W10) | W1 |
| N-3 | `apm_events(error_type)` — `error_type` 쿼리 키 허용(D-296)으로 서버 쪽에서 거른다 | W1 |
| N-4 | **자체 상한 제거**(C-11 · D-296 ④) — X-View 창(1분 단위로 나눠 부른다 · 상한 없음) · 이벤트 창 · 이벤트 행 수 · `n` · 인스턴스 목록 · 추세 인스턴스 수 · 프로파일 발췌 길이. 응답 크기 상한(`JENNIFER_MAX_RESPONSE_BYTES` — HTTP 응답 1건의 메모리 보호)은 남기되 로드된 클래스처럼 큰 응답이 걸리면 사유를 고지한다. 조사의 조사당 프로파일 5회·정체 가드는 유지 | W1 |
| N-5 | `apm_slow_transactions(breakdown, sort_by, application)` — `/api/status/{application·sql·external_call}` 시 단위 호출(`_hourly` 재사용) · **SQL 문 리터럴 마스킹** · URL 쿼리 값 마스킹 · `[한계] 시 단위 통계` | W2 |
| N-6 | `apm_runtime_health(metrics)` — `/api/metrics` `instance` 카탈로그(소스별 · TTL 1시간)로 이름 검증 · 모르는 이름은 `invalid_argument`(비슷한 이름 ≤3) · 지표 1개/호출이라 지표 수만큼 병렬 호출 | W2 |
| N-7 | `apm_events(kinds=[…, "change"])` — `/api-v2/deploy/{domainId}`를 25시간 조각으로 나눠 호출 · 행 `event_kind="change"` · 문구 「소스코드(리소스) 변경 감지」 · `was_error_burst` 근거에 직전 변경 시각 첨부(판정 규칙 불변) | W2 |
| N-8 | **허용목록 갱신**(D-296 ①) — §3 경로·선택 키 전부 · 거부 단언 9건(C-10)을 허용 단언으로 바꾸고 **쓰기·변형·시험 경로·`token`·리다이렉트 거부 단언은 유지** · J0 사본(`apm_gateway/testdata/jennifer/scripts/jennifer_catalog.py`)·대조 테스트·목 서버 픽스처를 같은 작업에서 | W2·W3·W4·W5·W7 |
| N-9 | `apm_app_health(scope=service, service)` — `/api/realtime/domain` 소스별 1회(전 도메인) · 서비스 이름 → 도메인 해석은 도메인 이름 단계 검색(정확 → 정규화 → 접두 → 포함 · `plans/130` N-1 검색기 재사용) · 창이 있으면 `/api/dbmetrics/domain` 추세(지표·도메인 수 제한 없음 · 병렬) · `ipAddress`·`port` 미사용 | W3 |
| N-10 | `apm_app_health(scope=ranking, rank_by, n, service?)` — **전수**: `/api/realtime/instance`를 도메인마다(소스·도메인 병렬) 불러 전 인스턴스 순위. `service`·`source_ids`로 좁히면 그 범위만 · 진행 보고(N-14) · `plans/125` A-6 ① 해소 | W3 |
| N-11 | `apm_events(scope=all)` — 폴러가 켜진 배포는 **폴러 최근 버퍼**에서 답하고(Open API 호출 0 · 버퍼에 없는 구간만 추가 조회), 꺼져 있으면 도메인 전수 조회 | W3 |
| N-12 | `apm_app_health(scope=business, business)` — 업무 해석은 `plans/130` N-2(`/api/business`) · `/api/realtime/business`(현재) · `/api/dbmetrics/business`(추세) | W4 |
| N-13 | `apm_slow_transactions(guid)` — 창은 선행 행 시각 기준(기본 ±5분 · 사용자 기간이 있으면 그 기간) · `clientIp`·`userId`·`clientId`는 G-11 규칙으로 | W5 |
| N-14 | **호출 속도·병렬·진행**(D-296 ⑤) — 소스별 호출 속도 상한 `JENNIFER_<ID>_RATE_LIMIT_PER_SEC`(값 G-12 · 협의값을 코드 기본값으로) · 소스·도메인 병렬 호출 · 긴 도구 호출은 MCP 진행 알림(FastMCP `report_progress` — 실재 여부 W0 실측)으로 「n/N 도메인」을 보낸다 · 토큰 사용량 고지(`[한계]`) | W3 |
| N-15 | **설정·관리 조회**(D-296 ①) — 이벤트 룰 3종 · 색상 경계 · 프로세스 → 인스턴스(`processId` 필수 — 호스트 + PID 정합, `plans/125` A-6 ③) · 데이터 서버 도메인 배치·자원·시스템 속성 · DB 경로 · 로드된 클래스(`search` · 6만 개 제약 고지). `autoScriptCommand`·시스템 속성 값은 N-17 규칙을 거친다 | W7 |
| N-16 | **민감 조회**(D-296 ①) — 사용자 목록(`/api/auth/userlist`·`/restapi/users`·`/restapi/user/{id}`) · 환경변수 · 실행 중 요청 상세(`apm_active_services` 행의 `txid`·`session_id`·`thread_hash`) — 개인정보 표시는 G-11 | W7 |
| N-17 | **자격증명 제거**(D-296 ③ · 불변) — `masking.py`에 `strip_credentials`: ① 필드 이름 `password`·`passwd`·`pwd`는 값과 키를 함께 버린다 ② 환경변수·시스템 속성·룰 텍스트에서 키 이름이 비밀 패턴(`PASSWORD`·`PASSWD`·`SECRET`·`TOKEN`·`APIKEY`·`API_KEY`·`ACCESS_KEY`·`PRIVATE_KEY`·`CREDENTIAL`·`AUTH` — 대소문자 무시)이면 값을 `[가림]`으로 ③ 값 자체가 JDBC URL의 `password=`·`user:pass@` 형태면 그 부분을 가린다. 응답 어디서든(중첩 dict·list) 적용 · 감사에 가린 건수만 · 테스트 픽스처에 카나리아 비밀값을 넣어 도구 결과·채팅·CSV·로그·감사 **어디에도 0회** 단언 | W7 |

### 4.4 본체 (`src/` — `apm_query` · 레지스트리 · 분해 APM 절 · 실행 주체는 G-9)

| # | 내용 | Wave |
|---|---|---|
| M-1 | **봉투 집계 전달** — `_collect`가 `summary`·`hourly`·`errors_by_type`·`was_signals`를 meta와 `organized_data`에 옮긴다(행 형식은 W0에서 `result_aggregator` 계약을 보고 정한다). **판정 고지 줄은 결정적**: 「판정: 힙 메모리 압박(WARNING) — heap 사용률 ≥ 0.9 연속 3샘플」(게이트웨이 `label`·`evidence` 그대로 · LLM 0) | W1 |
| M-2 | **보기 창 교정 + 상한 제거** — 모든 보기에서 사용자가 말한 기간을 그대로 넘긴다(`apm.runtime` 추세 · `apm.slow_tx` · `apm.app_health`의 시 단위 보충 포함) · 기간을 말하지 않으면 현행 기본(현재값·최근 10분·30분) · 「현재값 기준」 오고지를 보기별 사실대로(C-2) · `window_max_minutes`는 「상한」이 아니라 「X-View 원자료로 보는 구간」으로 뜻을 바꾸거나 제거(W0에서 레지스트리 필드 처분 결정) · `test_plan125_registry.py` 단언 갱신(C-18) | W1 |
| M-3 | **보기 인자**(G-7) — 레지스트리 `ViewSpec`에 `args`(보기 고정 프리셋 · 예 `apm.sql_stats` = `{breakdown: sql}`) · `options`(보기가 받는 닫힌 선택지 · 예 `apm.events` `level ∈ {fatal, warning}`) · `required_input` 값에 `""`·`service`·`profile_ref`·`active_ref` 추가 · `sensitive`(M-11) / 분해 APM 절에 `view_args` 칸(APM 활성일 때만 렌더) / 파서 `limit` → `n`(상한 없음 · 「전부」면 전부) | W1 |
| M-4 | **보기 추가**(레지스트리 · 선언 순서 = 표시 순서) — W2 `apm.app_stats`·`apm.sql_stats`·`apm.external_stats`·`apm.changes` · W3 `apm.service`·`apm.ranking`·`apm.fleet_events` · W4 `apm.business` · W5 `apm.profile`·`apm.trace` · W7 `apm.event_rules`(룰·색상 경계)·`apm.process`·`apm.jennifer_server`·`apm.loaded_classes`·`apm.environment`·`apm.users`·`apm.active_detail`. 보기 7 → 최대 24 | W2~W7 |
| M-5 | **대상 없는 보기** — `required_input: ""` 보기는 첫 홉 삽입 없이 호출한다(기본 보기 규칙 D-293은 그대로 — 보기를 고른 경우만) · `service` 대상은 `plans/130` M-1 `targets` 칸에 `kind: service`를 더해 받는다(130 W4 뒤 · 그 전에는 존·소스 범위만) · 대상 상한 10(`COMPOSITE_MAX_TARGETS`)을 제니퍼 조회에는 적용하지 않는다(D-296 · D-290 ⑤ 개정) | W3 |
| M-6 | **후속 질문 대상** — 「그 트랜잭션」·「첫 번째 거래」·「그 요청 상세」 같은 후속 질문은 **직전 턴 결과 행의 `profile_ref`·`guid`·`txid`·`session_id`·`thread_hash`**를 결정적으로 고른다(순번 지시어 = 행 순서 · 지시어만 있고 후보가 둘 이상이면 되묻기 — D-280). 직전 턴 행이 실리는 경로는 W0에서 실측 | W5 |
| M-7 | **창 밖 규칙 폐지**(D-296 · D-283 ④ 개정) — `_OUT_OF_WINDOW_AFTER`를 없애고, 긴 창은 `interval_minute`을 창 길이로 고른다(≤1일 5 · ≤7일 60 · 그 밖 1,440 — 허용값은 W10 실측으로 확정) · 제니퍼가 빈 결과를 주면 「보존 기간 밖일 수 있음」 고지 | W6 |
| M-8 | **보기 선택 보강**(G-10) — §10.2 G-10 확정안대로 | W8 |
| M-9 | **고지** — 시 단위 통계 · 현재값 · 보존 기간 · 「배포가 아니라 소스 변경 감지」 · 가린 값 · 토큰 사용량을 `plans/123` `disclosures[]` 규약으로 | 전 Wave |
| M-10 | **큰 조회 처리**(G-13) — 예상 호출 수 = (도메인·인스턴스·지표·1분 창 수)로 실행 전에 계산(게이트웨이 인벤토리 기준 · LLM 0) → 진행 표시(D-204 SSE 진행 신호에 「n/N」) → 결과가 크면 채팅에는 요약·상위 행, **전체 행은 결과 다운로드(CSV — D-262 마스킹 그대로)**. 첫 답변 상한(`query_timeout` 120초 · D-267 ⑦)을 넘는 조회를 어떻게 돌려줄지는 G-13 | W3 |
| M-11 | **민감·관리 보기 인가**(G-11) — 레지스트리 보기에 `sensitive: true` · 판정은 실행 경계(`run_apm_query`의 `allowed_sources` 판정과 같은 자리 · D-285)에서 한다 · 분해 프롬프트 렌더는 **역할과 무관하게 활성 기준**(렌더를 사용자마다 바꾸면 프롬프트 접두가 흔들려 KV 캐시가 깨진다) · 거부 문구에 보기·소스 이름을 싣지 않는다(D-264 ②) | W7 |

### 4.5 `sre_agent` · `noise_gate`

- `sre_agent` — 지침 1~2줄: `was_slow_sql`이면 `apm_slow_transactions(breakdown=sql)` · `was_error_burst`면 `apm_events(kinds=[error, change])` · 조사 근거에 이벤트 룰 임계(`event_rules`)를 쓸 수 있다. **사용자 목록·환경변수·실행 중 요청 상세(민감 조회)는 조사 쪽에 노출하지 않는다** — 노출 차단 방법(도구 이름 단위 제외 등)은 W0에서 holmes MCP 도구 등록 방식을 실측해 정한다(G-1 ②면 도구 하나를 빼는 것으로 끝난다). 코드 판정 변경 없음(D-195 ②). `spec/SPEC-apm-sre-agent.md` 지침 문구 갱신.
- `noise_gate` — 변경 없음(`app_impact`는 종전 인자 그대로 · 계약 테스트로 고정).

---

## 5. 구현 계획

### 5.1 Wave

| Wave | 내용 | 해결하는 질문 ID | 검증 | 선행 · 게이트 |
|---|---|---|---|---|
| **W0** 착수 전 실측·재현 | `file:line` 재실측(`plans/130`·`132`·`125` R7 랜딩 상태) · **재현 테스트 6건**(C-1 집계 소실 · C-2 추세·기간 미전달 · C-2 오고지 · C-3 시 단위 미도달 · C-4 `n`·`level` 미전달) — 지금 트리에서 **실패해야** 한다 · `result_aggregator`가 처리기 meta를 읽는 계약 · 직전 턴 행 접근 경로(M-6) · MCP 진행 알림이 본체 세션(`src/clients/source_mcp_client.py`)까지 오는지(N-14) · holmes MCP 도구 제외 방법(§4.5) · 스펙 합성 픽스처 원천(테스트 임시 디렉터리에만 — `plans/87` §0.11 방식) | — | 재현 테스트 실패 확인 | 없음 |
| **W1** 채팅 결함 교정 · 상한 제거 | N-1~N-4 · M-1~M-3 | S-04·S-05 · O-02·O-04·O-06 · F-02·F-03·F-04·F-05·F-08 | W0 재현 테스트 통과 · 목 게이트웨이 종단 · 비활성 렌더 바이트 불변 · 활성 APM 절 렌더 지문 갱신(의도분만) | W0 · G-7(M-3) |
| **W2** 허용됐지만 놀던 API | N-5·N-6·N-7 · N-8(선택 키) · M-4(W2분) | S-10 · O-03·O-07·O-08·O-09·O-10 · F-09 | 목 서버 픽스처(비지 않은 `status/sql`·`external_call` · 소스 변경 목록) · 허용목록 테스트 · SQL 문 마스킹 단언 | W1 · G-1 |
| **W3** 서비스·전체 범위 | N-8(서비스 경로) · N-9·N-10·N-11·N-14 · M-4·M-5·M-10 | S-06·S-07(하루 안)·S-08 · F-10 | 도메인 다수(합성 350개)·소스 2개 픽스처 · **호출 수·병렬도·진행 보고 단언** · 폴러 버퍼 on/off 두 경로 · 큰 결과 → 요약 + CSV | W2 · **G-12**(값 — 협의 전 5로 구현 가능) · **G-13** · (서비스 이름 해석은 `plans/130` W1·W4 뒤) |
| **W4** 업무 단위 | N-8(업무 경로) · N-12 · M-4 | S-09 | 업무 2개 · 업무 지표 픽스처 | `plans/130` W2(`/api/business`) |
| **W5** 트랜잭션 심층 | N-8(GUID) · N-13 · M-4·M-6 | F-06·F-07 | 2턴 시나리오(느린 트랜잭션 → 「첫 번째 거래 프로파일」·「그 거래 추적」) · 지시어 모호 → 되묻기 · PII 프로브(`scripts/pii_probe.py`) | W1 · G-11(GUID 거래의 사용자 정보 표시) |
| **W6** 장기 추세 | M-7 · N-6(긴 창) | S-11 · S-07(장기) | 창 길이별 `interval_minute` 선택 단언 · 빈 결과 고지 | W1 (보존 기간은 W10에서 확인 — 고지 문구만 바뀐다) |
| **W7** 설정·관리·민감 조회 | N-8(§3.2 경로) · N-15·N-16·N-17 · M-4·M-11 · `sre_agent` 노출 차단(§4.5) | O-11~O-17 · F-15 | **카나리아 비밀값 0회 단언**(도구 결과·채팅·CSV·로그·감사) · 역할별 허용·거부 · 렌더 역할 무관 단언 · PII 프로브 | **G-1 · G-11** |
| **W8** 보기 선택 신뢰성 | M-8 · 로컬 MLX 측정(D-240 — 두 평면 `mlx`면 승인 없이) | 전 질문의 보기 선택 | 질문 골드(V-2)로 보기 선택 정확도 기준선 → 보강 후 재측정(목표 수치는 기준선 뒤) | W1~W7 · **G-10** |
| **W9** 문서·매뉴얼·골드 (Wave마다) | V-1~V-6 | — | `pytest tests/test_manual` · 골드 문항 수 = `docs/33` 질문 ID 수 | 각 Wave · G-9(매뉴얼 소유) |
| **W10** 실데이터 교체 (외부 전제) | 합성 픽스처 → 녹화본 · U-2(`interval_minute`)·U-3(보존 기간)·U-12·U-14 · `visitDay` 기준 시각 · `sort_by_metrics` 허용값 · 소스 변경·GUID·민감 조회 응답 실모양 · 비밀 패턴 누락 점검(운영 환경변수 키 목록) | — | 녹화본 계약 테스트 | `plans/87` J0-L-b(평가판 라이선스) · J0-O(운영 접근) |

순서: W0 → **W1** → W2 → W3 → (W4 ∥ W5) → W6 → W7 → W8. W9는 각 Wave와 같은 작업에서, W10은 외부 전제가 풀리는 대로.

### 5.2 검증·문서 항목 (V)

| # | 내용 |
|---|---|
| V-1 | 목 Open API 서버 픽스처 — 스펙 5.6.4·v2 매뉴얼 응답 예로 합성(새 경로 전부 · 도메인 다수 · 소스 2개 · 업무 2개 · GUID 거래 3건 · 비지 않은 SQL·외부 호출 통계 · 소스 변경 · **카나리아 비밀값을 넣은 사용자·환경변수·시스템 속성**). 합성 픽스처는 테스트 임시 디렉터리에만 둔다 |
| V-2 | **질문 골드** `testdata/scenarios/fs_apm_questions.yaml`(신설) — `docs/33` 질문 ID와 1:1(S·O·F 43건 · X 4건은 「조회하지 않고 안내」) · `requires_sources: [apm]` · 기대 = 처리기 `apm_query` · 보기 id · 도구 · (목 종단) 게이트웨이 감사의 Open API 경로. 형식은 `fs_four_source.yaml`을 따른다 |
| V-3 | 로컬 종단 — 목 Open API 서버(`apm_gateway/testdata/jennifer/scripts/mock_openapi.py`) + 게이트웨이 실프로세스 + 본체 2단 + 로컬 MLX. 포트 점유 먼저 확인 · 자기 PID만 종료 · 공유 MLX 서버는 1토큰 생성으로 생존 확인 |
| V-4 | 문서 — `docs/33` 상태 칸(Wave마다) · `docs/31` §5.3(허용목록 표 · 거부 사례 표의 민감 GET 행)·§5.5(자격증명 제거)·§5.6(호출 속도)·§6.1(도구 인자)·**§7.2(낡은 서술 「채팅에서 'OO WAS 응답시간' 같은 질문은 답할 수 없다」 교정)** · `spec/SPEC-apm-gateway.md` §3·§6 · `spec/SPEC-apm-sre-agent.md` |
| V-5 | **매뉴얼(D-255)** — 사용자 매뉴얼 「WAS(제니퍼)에 묻기」 절(질문 유형별 예문 · 기간·대상 요령 · 큰 조회의 진행·다운로드 · 답하지 않는 질문) · 관리자 매뉴얼(민감 조회 권한 — G-11 · 호출 속도 설정 — G-12) · `scripts/manual/features.yaml`(`no_ui` — 채팅 기능) · `case_na` 사유 · `plans/125` A-8(WAS 질의 절) 이관 여부는 G-9 · `python -m scripts.manual.build` → `pytest tests/test_manual` |
| V-6 | `sre_agent` 지침 1~2줄 · 민감 조회 비노출(§4.5) · `cd sre_agent && .venv/bin/python -m pytest tests -q` |

---

## 6. 수용 기준

- **질문 커버리지** — V-2 골드 43건이 목 종단(V-3)에서 기대 보기·도구·Open API 경로로 답한다. `docs/33` 상태 칸이 ✅로 바뀐다(W10 전제가 남는 항목은 사유를 적는다). X 4건은 조회 호출 0 + 안내.
- **허용목록** — 늘어난 것은 D-296 ①의 읽기 경로·선택 키뿐이다. 비GET · `.xml` · POST 변형 · 시험 경로 · `token` 키 · 허용 밖 키 · 리다이렉트는 전부 HTTP 0회 거부 · J0 사본과 일치.
- **자격증명** — 카나리아 비밀값(사용자 `password` · 환경변수·시스템 속성의 비밀 패턴 값 · JDBC URL 비밀번호)이 도구 결과 · 채팅 응답 · CSV · 로그 · 감사 **어디에도 0회**.
- **범위** — 기간·대상·건수 자체 상한 0(조사 쪽 프로파일 5회·정체 가드 제외). 긴 창은 나눠 부르고 진행 보고가 온다 · 큰 결과는 요약 + 전체 CSV.
- **집계·판정** — 채팅 결과에 `summary`·`hourly`·`errors_by_type`·`was_signals`가 실린다. 판정 고지 줄은 게이트웨이 `label`·`evidence`와 글자 그대로 같다(LLM 0).
- **도구 표면** — G-1 확정안대로. 새 인자의 기본값으로 부른 결과가 현행과 같다(`sre_agent`·`noise_gate` 계약 테스트 무변경 통과). 민감 조회는 조사 쪽에 노출되지 않는다.
- **인가** — 민감 보기는 G-11 규칙대로 허용·거부되고, 거부 문구에 보기·소스 이름이 없다. 분해 프롬프트 렌더는 역할과 무관하다.
- **프롬프트** — APM 비활성 배포 렌더 바이트 불변(`plans/125` §15.1 렌더 지문 방식) · 활성 APM 절 변경은 보기 행·`view_args` 칸뿐.
- **품질 게이트** — `arch_check --ci` 위반 0 · `overfit_check --ci` 신규 유입 0(게이트웨이 `adapters/jennifer/` 밖에 벤더 리터럴 0) · ruff·mypy 신규 0 · 게이트웨이 경계 테스트(`apm_gateway/tests/test_boundary.py`) 통과.

---

## 7. 다른 계획과의 경계

| 계획 | 관계 | 이 계획이 하는 것 / 하지 않는 것 |
|---|---|---|
| **87** | 게이트웨이 패키지 소유 | 하는 것: N-1~N-17(실행 주체 G-9). 하지 않는 것: J6(L2 조치) · J7(OpenMetrics) · J0-L-b·J0-O 실측(W10이 그 결과를 받는다) |
| **125** | `apm_query`·레지스트리 보기·분해 APM 절 | 하는 것: M-1~M-11. **A-6 ①(도메인 단위 순위)은 N-10, A-6 ③(호스트 + PID 정합)은 N-15가 해소** — 125 잔여 장부에 통지. A-6 ②(다건 hostname)는 하지 않는다. A-8(WAS 질의 매뉴얼)은 G-9(D-295 ⑦이 사용법 안내 APM 행은 이미 넣었다) |
| **130** | 대상 해석(인스턴스 이름 · 업무명 · `/api/business`) | 쓰는 것: 130 N-1 검색기(서비스 이름 = 도메인 이름 검색) · 130 M-1 `targets` 칸(`kind: service` 추가) · 130 N-2·N-5(업무 해석). **같은 파일(`apm_query.py`·`fields.py`·`allowlist.py`·레지스트리)을 고치므로 130 W1~W4와 착수 순서를 맞춘다**(R-9). D-290 ⑤(대상 상한 10 · 초과 시 후보 제시)는 D-296으로 제니퍼 조회에서 개정됐다 — 130 W4가 그 규칙을 구현하기 전에 통지 |
| **132** | 소스 선별 · 소스별 유사어(D-293·D-295) | W8 M-8은 132 §6.6 계약(소스 안 어휘 = 렌더 재료 · 단어로 소스를 고르지 않음)을 따른다 — G-10 |
| **121** | 2단 조사 위임(F-13) | 하지 않는다 — D-270 ⑯ |
| **123** | 고지 규약 | M-9가 `disclosures[]`를 따른다 |

---

## 8. 결정 충돌 검토 (CLAUDE.md 작업 전 필수)

| 결정 | 이 계획 | 판정 |
|---|---|---|
| D-290 ④ · D-290 ⑤ · D-283 ④ · D-195 정정 부기 | 업무 지표 허용 · 대상 상한 없음 · 하루 규칙 폐지 · 민감 GET 허용 | **D-296으로 해소**(사용자 확정 2026-10-02 · 각 결정에 부기) |
| D-003 | 쓰기·제어 API 차단 유지 · DB 쓰기 0 | 부합 |
| D-195 ① · G-3(`apm_*` ≤8) | 설정·관리·민감 조회의 도구 배치 | **G-1에서 결정** — ②(9개)면 D-195 ① 개정이 함께 필요 |
| D-267 ⑦(첫 답변까지 처리 상한 120초) · D-242 · D-265(시간 상한 부분 결과) | 분 단위로 걸리는 전체 범위 조회 | **G-13에서 결정** — 처리 상한을 늘리거나 비동기로 돌리는 것은 이 결정들과 맞물린다 |
| D-195 정정 부기(허용목록 = 메서드 + 경로 정확 일치 · 그 밖 거부) | 방식 유지 · 목록만 확대 | 부합(D-296 ②) |
| D-195 ③ · D-189 | 조치 질문(X-02) 범위 밖 | 부합 |
| D-120 · D-127 | 프로파일·SQL·민감 조회 결과가 채팅 LLM 입력에 들어간다(가린 뒤) | 폐쇄망 FabriX·로컬 MLX 경로만. 과금 외부 평면은 건별 승인(D-127) — 운영 데이터를 외부 LLM에 보내지 않는다 |
| D-262(CSV = 화면과 같은 마스킹) | 큰 결과 전체 CSV | 부합 — 민감 조회의 원값 CSV 여부는 G-11 |
| D-264 ②(권한 밖 비노출) · D-285(`allowed_sources`) | 민감 보기 인가 · 거부 문구 | 부합 — 인가는 실행 경계, 렌더는 활성 기준(M-11) |
| D-274 ⑤ | 서비스 이름·순위·판정·자격증명 제거는 게이트웨이 한 곳 | 부합 |
| D-004 · D-293 · D-295 | LLM은 보기 id·닫힌 선택지만 · 소스 안 어휘는 렌더 재료 | G-10 ①′이면 부합 · ②면 132 계약과 D-004 부기 확장 필요 |
| D-281 ① · D-290 ⑧ | 게이트웨이·본체를 이 계획이 실행 | G-9 ①이면 130 선례와 같은 방식(통지 행) |
| D-287 | 새 인자 모두 `source_ids` 존중 · 소스별 호출 속도 | 부합 |
| D-251 · D-162 | 신규 `enable_*` 0 · APM 엔드포인트가 있을 때만 | 부합. 활성 배포의 동작은 바뀐다(창·집계·상한) — 결함 교정과 사용자 결정(D-296)이고 운영 투입(J0-O) 전이다 |
| D-122 | 원시 API 도구 비노출 | 부합 — 새 경로도 도구 인자 뒤에만 |
| D-255 | 사용자·관리자 기능 확대 | V-5 같은 작업에서 매뉴얼 |
| D-240 | 실 LLM 확인은 로컬 MLX | 부합(W8·V-3) |

---

## 9. 위험

| # | 위험 | 대응 |
|---|---|---|
| R-1 | **호출 비용** — 상한을 없애면 「지난달 느린 트랜잭션 원자료」 같은 질문이 도메인당 수만 호출이 된다 · 토큰 사용량(500·타임아웃 포함) · 폴링과 같은 토큰 공유 | 실행 전 예상 호출 수·소요 계산과 진행 표시(M-10 · G-13) · 병렬 · 호출 속도 상향(G-12) · 폴러 버퍼(N-11) · 감사로 호출 수 기록 |
| R-2 | **보기 7 → 최대 24** — 로컬 9B는 7개에서도 `views`를 비웠다(C-17) | 보기 고정 프리셋(LLM은 id만) · 영역 묶음 렌더 · G-10 · V-2 골드로 기준선 → 재측정 |
| R-3 | 실데이터 모양 미확인(로컬 라이선스 없음) — 새 경로 전부 | 스펙·매뉴얼 합성 픽스처 · 관대한 파서(필드 없음 = None · 계약 위반만 오류) · W10 녹화본 교체 |
| R-4 | **개인정보 확대** — 사용자 목록(이메일·휴대폰) · 실행 중 요청 상세(`userId`·SQL·HTTP query) · GUID 거래(`clientIp`·`userId`) · SQL 통계 문장 | G-11 규칙 · 가린 값만 LLM 입력 · FabriX PII 필터 차단 덤프는 `logs/pii_block/`에만 · PII 프로브 |
| R-5 | **자격증명 누출** — 비밀 패턴에 걸리지 않는 이름의 환경변수·시스템 속성(예 `DB_PW2`) · 룰의 `autoScriptCommand` 인자 | 키 패턴 + 값 패턴(JDBC URL · `user:pass@`) 이중 · 운영 환경변수 키 목록으로 패턴 보정(W10) · 카나리아 단언 |
| R-6 | 「서비스」 ≠ 제니퍼 도메인(운영 명명이 다를 수 있다 — A-1) | 해석 고지 · 후보 제시 · J0-O에서 명명 확인 · 업무(S-09)와 구분 |
| R-7 | 시 단위 통계를 분 단위 질문의 답으로 오해 | 고지 필수(M-9) · 10분 이하 질문은 X-View 값을 먼저 |
| R-8 | 보존 기간·`interval_minute` 허용값 미확인 | 빈 결과 고지(M-7) · W10 확정 |
| R-9 | 병행 계획(130·132·125 R7)이 같은 파일을 고친다 | W0 재실측 · 130 W1~W4와 순서 합의 · 겹치는 파일은 착수 직전 `git status` 확인 · 기준선 대조는 `git worktree`(stash 금지) |
| R-10 | v1 API는 *"no longer maintained"* · v2 매뉴얼 경로는 정본 스펙 밖(`plans/87` R-12) | 녹화본 계약 테스트 · 관대한 파서 |
| R-11 | 큰 결과가 LLM 문맥을 넘친다 | 채팅은 요약·상위 행만 LLM에 · 전체는 CSV(M-10) |
| R-12 | 호출 속도를 올리면 뷰 서버가 느려진다 — 제니퍼 사용자 화면에도 영향 | 협의값만 쓴다(G-12) · 소스별 값 · 감사의 `elapsed_ms`·오류율로 감시 |

---

## 10. 사용자 확정 게이트

내부 용어: **게이트웨이** = 제니퍼 API를 부르는 우리 쪽 중계 프로그램(`apm_gateway`). **허용목록** = 게이트웨이가 부를 수 있는 제니퍼 API 주소 목록. **도구** = 게이트웨이가 장애 조사·채팅·알람 쪽에 내놓는 「조회 기능」 단위(지금 `apm_*` 8개). **보기** = 채팅이 고를 수 있는 「제니퍼 조회 종류」 목록(지금 7개). **도메인** = 제니퍼가 WAS를 묶는 단위(보통 서비스).

### 10.1 확정 (D-296 · 2026-10-02)

| # | 질문 | 확정 |
|---|---|---|
| G-2 | 서비스(도메인) 단위 API · 기존 API 선택 옵션 허용 | ✅ 허용 — D-296 ① |
| G-3 | 업무 단위 지표 API 허용(D-290 ④ 개정) | ✅ 허용 — D-296 ① · D-290 ④ 개정 |
| G-4 | GUID 거래 추적 API 허용 | ✅ 허용 — D-296 ① |
| G-5 | 트랜잭션 프로파일을 채팅에서도 | ✅ 허용 — 채팅 쪽 호출 상한 없음(D-296 ④) |
| G-6 | 하루 넘게 지난 기간 조회 | ✅ 제한 없음(제니퍼 보존 기간까지) — D-296 ④ · D-283 ④ 개정 |
| G-8 | 전체 범위 질문의 비용 처리 | ✅ 범위를 줄이지 않고 호출 속도 상한을 올린다 — D-296 ⑤(값 G-12) |
| (신규) | 민감·관리 조회 API | ✅ 읽기 전부 허용 · 쓰기·제어 차단 · 자격증명 값 제거 — D-296 ①②③ |

### 10.2 남은 게이트 (사용자 「따로 검토」 + 신설)

**G-1 — 제니퍼 조회 기능(도구)을 몇 개로 나눌까?**

지금 게이트웨이는 조회 기능 8개(인스턴스 목록 · 응답시간 · JVM · 커넥션 풀 · 실행 중 서비스 · 느린 트랜잭션 · 이벤트 · 트랜잭션 프로파일)를 내놓고, 결정 D-195 ①이 「8개 이하」로 정해 두었다. D-296으로 **설정·관리·민감 조회**(이벤트 룰 · 환경변수 · 로드된 클래스 · 데이터 서버 · 사용자 목록 등 7종)가 새로 들어오면서 이것들을 어디에 둘지가 문제다. 장애 조사 LLM은 게이트웨이의 도구를 **이름 단위로 전부** 본다.

| 선택 | 무엇이 달라지나 |
|---|---|
| ① 8개 유지 — 새 조회는 기존 도구의 옵션으로 | 「사용자 목록」을 예컨대 인스턴스 목록 도구의 옵션으로 넣게 된다 — 도구 뜻이 흐려지고, **장애 조사 LLM도 그 옵션을 보게 된다**(민감 조회를 조사 쪽에서 막으려면 옵션 단위로 따로 막아야 한다) |
| ② 8개 + 「설정·관리 조회」 1개 = **9개**(`apm_config` 가칭) | 성능·장애 조회는 지금 8개에 옵션만 더하고, 설정·관리·민감 조회는 새 도구 하나에 모은다. **장애 조사 쪽에서는 이 도구 하나만 등록하지 않으면** 민감 조회가 노출되지 않는다. D-195 ①(8개 이하)을 9개로 고쳐야 한다 |
| ③ API마다 도구 | 20개가 넘는다 — 장애 조사 LLM이 도구를 고르기 어려워진다 |

**권고: ② (v1.0 권고 ①에서 바꿈)** — v1.0 때는 새 조회가 모두 성능·장애 데이터라 기존 도구 옵션으로 자연스럽게 들어갔지만, 민감·관리 조회는 성격이 달라 한곳에 모아 두는 편이 막기도 쉽고 이해하기도 쉽다.

**G-7 — 「fatal만」·「호출 많은 순」 같은 조건을 어떻게 전달할까?**

예: 「web01 fatal 이벤트만」 → 이벤트 조회에 `level=fatal` · 「오늘 호출 많은 SQL 순으로」 → SQL 통계에 `sort_by=calls`.

| 선택 | 무엇이 달라지나 |
|---|---|
| ① 보기마다 받을 수 있는 선택지를 정해 두고(예: 이벤트 보기 = `fatal`·`warning`), 계획을 세우는 LLM이 그중에서 고르면 코드가 목록 안 값인지 검사한다. 「상위 5개」 같은 개수는 입력 파서가 이미 뽑는 값을 그대로 쓴다 | 「치명적인 것만」·「심각한 거」처럼 표현이 달라도 LLM이 `fatal`로 맞춘다. 목록 밖 값은 버리고 고지한다 |
| ② 질문 문구에서 키워드로 뽑는다(「fatal」이 있으면 `level=fatal`) | LLM 판단이 없어 결과가 늘 같지만, 키워드 목록에 없는 표현은 놓친다. 「의도 판단은 LLM이 한다」는 결정(D-004)과도 어긋난다 |

**권고: ①**

**G-9 — 누가 구현할까?**

제니퍼 쪽 코드는 두 군데다 — 게이트웨이(`plans/87` 소관)와 채팅 처리기 `apm_query`(`plans/125` 소관). 새 질문 하나를 쓰려면 보통 **두 곳을 같이** 고쳐야 한다(예: 게이트웨이 도구에 `breakdown=sql` 옵션 + 채팅 보기 `apm.sql_stats`).

| 선택 | 무엇이 달라지나 |
|---|---|
| ① 이 계획(134)이 두 곳을 다 고치고, 87·125·130 문서에 「이 부분은 134가 고쳤다」를 남긴다. 125에 남은 「WAS 질문 매뉴얼」(A-8)도 134가 맡는다 | 한 질문의 양 끝(게이트웨이 옵션 ↔ 채팅 보기)을 한 작업에서 맞추고 테스트한다. `plans/130`(D-290 ⑧)과 같은 방식 |
| ② 게이트웨이 부분은 87, 채팅 부분은 125의 남은 일로 넘긴다 | 87·125는 다른 남은 일이 많아 순서를 따로 맞춰야 하고, 한 질문이 두 계획이 다 끝나야 동작한다 |

**권고: ①**

**G-10 — 보기가 7개에서 최대 24개로 늘 때, LLM이 보기를 잘 고르게 하려면?**

로컬 소형 모델은 보기 7개일 때도 보기를 비운 적이 있다(「인스턴스 목록」 질문이 응답시간 조회로 바뀜 — `plans/132` O-3). 한편 2026-10-02 확정된 `plans/132` 계약(D-295)은 「소스 안의 어휘(보기 이름·설명)는 **LLM에게 보여 주는 재료**이고, 단어로 무엇을 고르지 않는다」로 정했다.

| 선택 | 무엇이 달라지나 |
|---|---|
| ①′ 보기 설명에 대표 단어·예문을 넣고 영역(성능 · 런타임 · 활동 · 이벤트 · 설정)별로 묶어 보여 준다. LLM이 비우면 지금처럼 기본 보기(대상 있으면 응답시간 · 없으면 목록)로 간다. 질문 골드로 정확도를 재고 모자라면 다시 판단한다 | 132 계약 그대로 · 추가 LLM 호출 0 |
| ② 보기마다 대표 단어를 등록해 두고, LLM이 비우거나 엇갈리면 **코드가 단어로 보기를 정한다** | 소형 모델에서 더 안정적일 수 있지만, 132 계약(단어로 고르지 않음)과 D-004 부기를 넓혀야 한다 |

**권고: ①′ (v1.0 권고 ①에서 바꿈)** — v1.0 작성 때는 132 게이트가 확정 전이었다. 확정된 계약과 맞추고, 측정(W8)에서 모자라면 ②를 다시 올린다.

**G-11 (신설) — 민감 조회(사용자 목록 · 환경변수 · 실행 중 요청 상세)를 누가, 어떻게 볼까?**

자격증명 값(비밀번호 · 비밀 환경변수 값)은 어떤 선택이든 **항상 버린다**(D-296 ③). 여기서 정할 것은 이메일·휴대폰·사용자 ID·SQL·HTTP 파라미터 같은 개인정보다. 채팅 답변은 LLM이 만들기 때문에 개인정보 원값을 LLM에 넣으면 사내 LLM(FabriX)의 개인정보 필터가 요청을 막을 수 있다.

| 선택 | 무엇이 달라지나 |
|---|---|
| ① **관리자만** 민감 조회 가능 · 채팅 답변과 LLM 입력에는 가린 값(예 `hong***@…` · `010-****-1234`) · 원값은 관리자의 결과 다운로드(CSV)에만 | 일반 사용자에게는 민감 조회가 「권한 없음」. 관리자는 원값이 필요하면 CSV로 받는다 |
| ② **모든 사용자** · 가린 값만(원값은 어디에도 없음) | 누구나 물을 수 있지만 원값은 볼 수 없다 |
| ③ 관리자만 · 원값을 채팅 표에도 그대로 | 표는 LLM을 거치지 않게 만들어야 하고(설계 추가), 화면에 개인정보가 남는다 |

**권고: ①**

**G-12 (신설 · 확인 요청) — 호출 속도 상한을 얼마로 올릴까?**

지금 초당 5회다. 올릴 값은 **제니퍼 운영 조직이 정해야** 한다(뷰 서버 부하 · 토큰 사용량 한도 — `plans/87` U-7). 협의값을 알려 주시면 코드 기본값으로 고정한다(소스마다 다르면 소스별로). 그 전까지는 5로 구현하고 병렬·진행 표시만 먼저 넣는다.
참고 — 도메인 350개를 한 번씩 부르는 질문의 소요: 초당 5회 ≈ 70초 · 10회 ≈ 35초 · 20회 ≈ 18초.

**G-13 (신설) — 1분이 넘게 걸리는 큰 조회를 채팅에서 어떻게 돌려줄까?**

지금 채팅은 **질문 후 120초 안에 첫 답변**이 나와야 한다(D-267 ⑦ · 관리자 설정 `API_QUERY_TIMEOUT`). 범위 제한을 없앴으니(D-296) 「전체 WAS 순위」·「지난주 시간대별 추세」는 이 시간을 넘을 수 있다.

| 선택 | 무엇이 달라지나 |
|---|---|
| ① 실행 전에 예상 시간을 계산해, **120초를 넘으면 「약 N분 걸립니다 — 계속할까요?」를 먼저 묻고**, 계속을 고르면 그 질문만 처리 상한을 예상 시간만큼 늘려 진행 막대를 보여 주며 끝까지 조회한다 | 사용자가 기다릴지 고를 수 있다. 다른 질문의 상한은 그대로다 |
| ② 묻지 않고 처리 상한을 넉넉히(예: 10분) 늘려 끝까지 조회한다 | 단순하지만, 잘못 넓게 물은 질문도 분 단위로 붙잡힌다 |
| ③ 큰 조회는 백그라운드 작업으로 돌리고 끝나면 알림·다운로드 링크를 준다 | 채팅이 막히지 않지만 작업 관리(진행·취소·결과 보관) 기능을 새로 만들어야 한다 |

**권고: ①** — D-296 「범위는 모두 가능」을 지키면서, 오래 걸리는 사실은 미리 알린다(줄이지 않는다).

---

## 11. 측정하지 못한 것 · 확인 요청

- **G-12** 제니퍼 운영 조직과 협의한 호출 속도 값(소스별).
- **A-1 가정** — 운영 제니퍼 도메인 이름이 사용자가 말하는 「서비스」명과 맞는지. → `plans/87` J0-O(소스마다)
- 새 경로 전부의 **실응답 모양**(로컬 Docker 라이선스 없음 · 도메인 0건). 특히 v2 매뉴얼 경로(정본 스펙 밖)와 민감 조회. → J0-L-b
- `visitDay`·`hitDay`의 「하루」 기준 시각 · `sort_by_metrics` 허용값 · `interval_minute` 허용값 · 보존 기간 · GUID 형식 · 운영 환경변수 키 이름(비밀 패턴 보정). → J0-L-b·J0-O(W10)
- MCP 진행 알림이 본체까지 오는지 · 직전 턴 결과 행 경로 · holmes MCP 도구 제외 방법 — W0 실측.
- 운영 폐쇄망 `.env`의 사다리 단·APM 엔드포인트 설정 — 이 저장소에서는 측정할 수 없다(A-2).
- 보기 24개에서의 로컬 9B 보기 선택 정확도 — W8 기준선 전에는 수치가 없다.

---

## 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-02 | **v1.0** — 최초 작성(사용자 지시 *"조사한 사용가능한 질문들은 docs폴더의 사용 사례를 별도로 정리하고 사용가능한 질문을 모두 사용할 수 있도록 구현할 계획을 plans폴더에 계획파일을 정리하라."* · 코드 0). 사용 사례 `docs/33` 신설(질문 S 11 · O 10 · F 14 · X 6). 현행 실측(**채팅이 봉투 집계·`was_signals`를 버림** · `apm.runtime`·`apm.slow_tx` 창 미선언으로 추세·기간 미전달과 오고지 · `apm.app_health` 10분 상한으로 시 단위 미도달 · `limit`·`level` 미전달 · 파서가 방문·호출 필드 버림 · 허용 16경로 중 4경로 미사용 · 스펙 선택 키 미허용 · 전체 범위 질문은 앞 10대 임의 조회 · 운영 도메인 350개 비용 제약). 스펙 5.6.4 재파싱 · v2 매뉴얼 `deploy`. 설계(도구 8종 인자 확장 · 보기 7 → 최대 17) · Wave W0~W9 · 결정 충돌 2건(D-290 ④ · D-283) · 위험 R-1~R-10 · 게이트 G-1~G-10. 파일명 `-TODO` |
| 2026-10-02 | **v1.1** — 사용자 결정 반영(*"모든 api는 허용하고 조회할 수 있는 범위는 모두 가능하도록 정한다."* · 확인 응답 「민감 조회 API까지」·「보호 한도도 올림」·나머지 게이트 「따로 검토」) → **D-296 본문 등재**(D-195 정정 부기 · D-290 ④⑤ · D-283 ④ 개정 부기). 범위: 제니퍼 읽기 API 전부(민감·관리 조회 포함 · 쓰기·제어 차단 · 자격증명 값 제거 불변) · 자체 상한 제거 · 호출 속도 상향. v2 매뉴얼 8건 재파싱(§3.2 — `/restapi/users`에 `password` · 환경변수 · 실행 중 요청 상세 · 이벤트 룰 · 로드된 클래스 · 프로세스 → 인스턴스 · 데이터 서버). 질문 43건(O-11~O-17 · F-15 신설 · X-04·X-06 흡수). 설계: N-4(상한 제거) · N-14(호출 속도·병렬·진행) · N-15~N-17(설정·관리·민감 · **자격증명 제거**) · M-10(큰 조회) · M-11(민감 보기 인가) · Wave에 W7(설정·관리·민감) 추가 · 기존 W7~W9 → W8~W10. 게이트: G-2·G-3·G-4·G-5·G-6·G-8 확정 · G-1·G-10 **권고 변경**(G-1 ① → ② 「설정·관리 조회」 도구 1개 추가 = 9개 · G-10 ① → ①′ 132 계약 준수) · G-11(민감 조회 표시·역할)·G-12(호출 속도 값)·G-13(큰 조회 반환 — D-267 ⑦) 신설 · §10.2 쉬운 말 재설명 |
