# 33. 제니퍼(JENNIFER) APM 사용 사례 — 무엇을 물을 수 있고 어떤 API가 답하는가

> **정본 범위**: 제니퍼 Open API로 답할 수 있는 질문을 **서비스 제공 현황 · 운영 · 장애** 세 갈래로 모으고, 질문마다
> 답하는 Open API · 게이트웨이 도구(`apm_*`) · 채팅 보기(`apm.*`) · **지금 상태**를 적는다. 연동 절차(설정·통제·검증)는
> `docs/31_jennifer_integration_guide.md`가, 설계 근거는 `plans/87`이, 이 표의 빈칸을 채우는 구현 계획은
> **`plans/134-TODO-jennifer-question-coverage.md`**가 정본이다.
> **작성**: 2026-10-02 · 기준 = `multiintent` 작업 트리(HEAD `22f9628` + 미커밋) · 제니퍼 Open API 공식 스펙 5.6.4(39경로 — `plans/87` [J-23] 원천을 2026-10-02 다시 받아 파싱) · v2 매뉴얼 `spec/*.md` 8건
> **범위 결정(D-296 · 2026-10-02)**: 제니퍼 Open API의 **읽기(GET) 경로는 민감·관리 조회까지 전부 허용**하고 **쓰기·제어는 계속 막는다**. 비밀번호·비밀 환경변수 값은 어떤 경로로도 내보내지 않는다. **조회 범위의 자체 상한(기간·대상·건수)은 없앤다**. 이 결정은 계획 단계이고 **코드는 아직 그대로**다 — 아래 「지금」 칸은 현재 동작이다.
> **상태 칸은 시점 값이다.** `plans/134`의 Wave가 랜딩할 때마다 같은 작업에서 이 문서의 상태 칸을 고친다(`plans/134` W9).

---

## 0. 30초 요약

- 게이트웨이(`apm_gateway/`)는 지금 제니퍼 Open API 중 **GET 16경로만** 부른다(허용목록 — `apm_gateway/apm_gateway/adapters/jennifer/allowlist.py`).
  그중 **12경로는 도구 8종(`apm_*`)이 쓰고, 4경로(`/api/metrics` · `/api/status/sql` · `/api/status/external_call` · `/api-v2/deploy/{domainId}`)는 허용만 돼 있고 부르는 도구가 없다.**
  D-296으로 나머지 읽기 경로(서비스·업무·GUID·민감·관리 조회)도 허용하기로 정했다(구현 전).
- 채팅은 2단 처리기 `apm_query`가 **보기 7종**(`apm.instances` · `apm.app_health` · `apm.runtime` · `apm.pool` · `apm.active` · `apm.slow_tx` · `apm.events`)으로만 조회한다
  (`config/db_registry.yaml` `solutions[apm].views`). 트랜잭션 프로파일(`apm_transaction_profile`)은 채팅 보기가 없어 **장애 조사(`sre_agent`)에서만** 쓴다.
- **채팅은 게이트웨이가 돌려주는 것의 일부만 쓴다** — 행(`rows`)과 `[한계]` 문구만 옮기고, 봉투의 집계(`summary` · `hourly` · `errors_by_type`)와
  WAS 판정(`was_signals`)은 버린다(`src/orchestration/apm_query.py` `_collect`). 보기 창 설정 때문에 추세·시 단위 통계도 채팅에서는 나오지 않는다(§2·§3 ◐ 행).
- 채팅 조회는 **사다리 2단(기준 경로)**에서만 된다. 1단(`deep_agent`)은 고정 처리기 목록만 도구로 쓰고, 3단은 계획 루프(`TIER3_PLAN_LOOP_ENABLED` · 기본 off)일 때만 같은 처리기를 부른다.
  또 제니퍼 엔드포인트(`MCP_SOURCE_ENDPOINTS`의 `apm`)가 설정된 배포에서만 동작한다.
- 대상은 지금 **hostname**으로 지정한다. 인스턴스 이름(부분 일치)·업무명으로 묻는 기능은 `plans/130`(TODO)이다.
- **실데이터로는 검증하지 않았다** — 로컬 Docker 제니퍼에 라이선스가 없어(도메인 0건) 응답 모양은 스펙으로 만든 합성 픽스처 기준이다(`plans/87` J0-L-b·J0-O).

---

## 1. 표 읽는 법

| 표지 | 뜻 |
|---|---|
| ✅ | 지금 채팅에서 된다 |
| ◐ | 채팅에서 일부만 된다 — 비고에 무엇이 빠지는지 적었다 |
| 🔎 | 장애 조사(`sre_agent`)에서만 된다 — 채팅 보기 없음 |
| 🔶 | 허용목록에는 있지만 부르는 도구·보기가 없다 |
| ➕ | 허용하기로 정했다(D-296) — 허용목록·도구·보기 구현 전 |
| ✖ | 지금은 답하지 않는다 — 비고에 사유 |

- **구현 계획** 칸은 `plans/134`의 Wave다(W1 = 채팅 결함 교정·상한 제거 · W2 = 허용됐지만 놀던 API · W3 = 서비스·전체 범위 · W4 = 업무 단위 · W5 = 트랜잭션 심층 · W6 = 장기 추세 · W7 = 설정·관리·민감 조회 · W8 = 보기 선택 신뢰성).
- **용어** — 제니퍼 **도메인** = 에이전트 묶음(보통 서비스·업무 단위). 이 문서는 사용자가 「OO 서비스」라고 부르는 단위를 도메인으로 본다(`plans/134` A-1 가정).
  **인스턴스** = WAS 프로세스 1개(한 호스트에 여러 개가 뜰 수 있다). **업무(Business)** = 제니퍼에 규칙으로 정의한 트랜잭션 묶음.

---

## 2. 서비스 제공 현황

| ID | 이런 질문 | 답하는 Open API(쓰는 필드) | 도구 · 채팅 보기 | 지금 | 비고 · 구현 계획 |
|---|---|---|---|---|---|
| S-01 | 「제니퍼 인스턴스 목록 보여줘」 · 「web01에 떠 있는 WAS는?」 | `/api/domain` → 도메인별 `/api/instance`(`name`·`hostName`·`ipAddress`·`status`·`version`·`platform`) | `apm_instance_map` · `apm.instances` | ✅ | 지금 200개 상한(D-296으로 제거 — W1). 대상 없이 물으면 목록(D-293) |
| S-02 | 「web01 WAS 지금 응답시간·TPS 알려줘」 | `/api/realtime/instance`(`responseTime`·`tps`·`activeService`·`concurrentUser`·`rejectRate`) | `apm_app_health` · `apm.app_health` | ✅ | 현재값 |
| S-03 | 「web01 최근 10분 에러율·p95」 | `/api/transaction/time`(1분 창을 나눠 부른다 — 게이트웨이가 p50·p95·에러율 계산) | `apm_app_health`(행의 `window`) | ✅ | 지금은 창 10분 상한 — 더 긴 기간은 마지막 10분만(D-296으로 제거 — W1) |
| S-04 | 「web01 오늘 오전 처리 건수와 실패율」 · 「지난 3시간 상위 애플리케이션」 | `/api/status/application`(시 단위 · `calls`·`failures`·`responseTime`·`maxResponseTime`) | `apm_app_health`의 `hourly` | ◐ | 게이트웨이는 창 > 10분이면 시 단위 통계를 붙이지만 **채팅 보기 창 상한이 10분이라 도달하지 못하고**, 붙더라도 채팅이 `hourly`를 버린다 → **W1** |
| S-05 | 「web01 오늘 방문자 수·호출 수」 | `/api/realtime/instance`(`visitDay`·`visitHour`·`hitDay`·`hitHour`) | `apm_app_health` | ◐ | 응답에 있는 필드를 게이트웨이 파서가 버린다(`adapters/jennifer/fields.py` `parse_realtime`) → **W1** · 「하루」 기준 시각은 실데이터로 확인 |
| S-06 | 「인터넷뱅킹 서비스 지금 현황」 · 「오늘 서비스별 방문자」 | `/api/realtime/domain`(`visitDay`·`hitDay`·`tps`·`responseTime`·`concurrentUser`·`activeUser`·`rejectRate`) | — | ➕ | 도메인(서비스) 단위 현재값 · 한 번 호출로 전 도메인 → **W3** |
| S-07 | 「인터넷뱅킹 오늘 시간대별 호출 수·응답시간」 | `/api/dbmetrics/domain`(도메인 지표 41종 — `service_count`·`service_time`·`service_err_count`·`visit_hour`·`max_tps` 등 · 지표 1개/호출) | — | ➕ | → **W3**(하루 안) · **W6**(더 긴 기간) |
| S-08 | 「지금 응답시간 가장 느린 WAS 5개」 · 「TPS 높은 WAS 순위」 | `/api/realtime/instance`(`domain_id`만 주면 그 도메인 전 인스턴스) | — | ◐ | 지금은 대상이 없으면 인스턴스 목록 **앞 10대**만 골라 조회한다 — 순위가 아니다. D-296으로 전 도메인 전수 조회(운영 약 350개 — 호출 속도 상향·진행 표시) → **W3** |
| S-09 | 「대출 업무 응답시간·TPS」 · 「업무별 현황」 | `/api/business` → `/api/realtime/business`(`businessName`·`responseTime`·`tps`·`activeService`) · `/api/dbmetrics/business`(추세) | — | ➕ | 업무 목록은 `plans/130`이, 업무 지표 두 경로는 D-296(D-290 ④ 개정)으로 허용 → **W4** |
| S-10 | 「오늘 호출 많은 URL 상위 10」 · 「/login.do 응답시간」 | `/api/status/application`(`sort_by_metrics`·`application_name`·`max_row`) | `apm_app_health` `hourly.top_applications`(응답시간순 5개 고정) | ◐ | 정렬 기준·URL 지정 쿼리 키가 허용목록 밖이고, 채팅은 `hourly`를 버린다 → **W2** |
| S-11 | 「지난달 web01 일평균 응답시간」 · 「지난주 대비 처리량」 | `/api/dbmetrics/instance`(`interval_minute` · `service_time`·`service_count` 등) | — | ✖ | 지금 채팅은 **기간 끝이 하루 넘게 지난 질문은 조회하지 않는다**(D-283 ④). D-296이 이 규칙을 폐지했다(제니퍼 보존 기간까지) → **W6** |

## 3. 운영

| ID | 이런 질문 | 답하는 Open API(쓰는 필드) | 도구 · 채팅 보기 | 지금 | 비고 · 구현 계획 |
|---|---|---|---|---|---|
| O-01 | 「web01 WAS 힙 사용률·GC·CPU」 | `/api/realtime/instance`(`heapUsed`·`heapCommitted`·`gcTimeUsage`·`procCPU`·`procMemory`·`threadCurrent`) | `apm_runtime_health` · `apm.runtime` | ✅ | 현재값 |
| O-02 | 「web01 최근 1시간 힙·GC 추이」 | `/api/dbmetrics/instance`(5분 간격 · `heap_used`·`heap_committed`·`gc_time_usage`) | `apm_runtime_health`의 `trend` | 🔎 | 게이트웨이는 기간을 받으면 추세를 붙이지만, **채팅 보기 `apm.runtime`에 기간 설정이 없어 처리기가 기간을 넘기지 않는다** → 채팅은 현재값만 → **W1** |
| O-03 | 「web01 소켓 수·파일 수·시스템 CPU 추이」 | `/api/dbmetrics/instance` + `/api/metrics`(인스턴스 지표 카탈로그 60종) | — | 🔶 | 추세 지표가 3종으로 고정이다 → **W2** |
| O-04 | 「web01 메모리 누수 의심돼?」 · 「GC 때문에 느린 거야?」 | O-01·O-02 + 게이트웨이 판정 `was_heap_pressure`·`was_gc_stall` | `apm_runtime_health`의 `was_signals` | 🔎 | 채팅은 추세를 못 받고 판정(`was_signals`)도 버린다 → **W1** |
| O-05 | 「web01 DB 커넥션 풀 상태」 | `/api/realtime/instance`(`activeDBConnection`·`averageDbPoolIdleCount`·`averageDbPoolConfiguredCount`) + `/api/activeService/list`(데이터소스별) | `apm_resource_pool` · `apm.pool` | ✅ | 현재값 · WAS 스레드 풀 상한 필드는 스펙에 없어 근사(`[한계]`) |
| O-06 | 「web01 지금 실행 중인 서비스」 · 「오래 걸리는 요청」 | `/api/activeService/list`(`elapseTime`·`status`·`runningMode`·`runningFullText`) | `apm_active_services` · `apm.active` | ✅ | 현재값 · **채팅은 개수를 넘기지 않아 기본 10건** · 실행 모드별 집계(`summary`)는 채팅이 버린다 → **W1** |
| O-07 | 「web01 오늘 가장 오래 걸린 SQL」 · 「호출 많은 SQL」 | `/api/status/sql`(시 단위 · `name`·`calls`·`failures`·`responseTime`·`maxResponseTime`) | — | 🔶 | 도구 없음. 인스턴스 지정·정렬·건수 쿼리 키(`instance_id`·`sort_by_metrics`·`max_row`)도 허용 밖 → **W2** · SQL 문은 리터럴 마스킹 |
| O-08 | 「web01 외부 호출 중 느린 곳」 | `/api/status/external_call`(시 단위 · 같은 필드) | — | 🔶 | → **W2** |
| O-09 | 「web01 최근 하루 배포(소스 변경) 있었나」 | `/api-v2/deploy/{domainId}`(`collectTime`·`instanceId` · 25시간 이하 · 5.6.0.5+) | — | 🔶 | 정본 스펙 미수록(v2 매뉴얼 `spec/deploy.md`) · 응답은 「데이터 서버가 소스코드(리소스) 변경을 인지한 시각」뿐 — 배포 이력 그 자체가 아니다 → **W2** |
| O-10 | 「제니퍼에서 볼 수 있는 지표 목록」 | `/api/metrics`(domain 41 · instance 60 · business 29 · application · sql · externalCall — 로컬 5.7.0.1 녹화본) | — | 🔶 | → **W2**(추세 지표 이름 검증에도 쓴다) |
| O-11 | 「제니퍼 이벤트 룰·임계값이 어떻게 설정돼 있나」 · 「힙 경고 기준이 몇 %야?」 | `/api-v2/manage/rule/event/error/{d}` · `…/metric/{d}/{대상}` · `…/compare/{d}/{대상}`(`errorType`·`metricId`·`level`·`applied`·`expression`·`thresholdErrorCount`·`checkTimeRange`) | — | ➕ | 조회만 — 룰 **변경**은 X-03 · `autoScriptCommand`(서버 스크립트 경로)는 자격증명 제거 규칙을 거친다 → **W7** |
| O-12 | 「액티브 서비스 느림(빨간색) 기준이 몇 초야?」 | `/api-v2/manage/rule/active-service-color-range-boundary`(경과 시간 경계 4색) | — | ➕ | → **W7** |
| O-13 | 「web01 WAS 환경변수·JVM 시스템 속성」 | `/api-v2/environment-variable/{d}`(`SYSTEM`·`JAVA`) | — | ➕ | **비밀번호·토큰 같은 값은 가린다**(D-296 ③) · 누가 볼 수 있는지는 `plans/134` G-11 → **W7** |
| O-14 | 「web01에 로드된 클래스 중 OOO 찾아줘」 | `/api-v2/loaded-class/{d}/{i}?search=`(`className`·`superClassName`·`interfaceClassNames`·`classLoaderName`) | — | ➕ | 로드된 클래스가 6만 개 이하일 때만 응답(제니퍼 제약) → **W7** |
| O-15 | 「web01의 프로세스 1234는 어느 WAS 인스턴스야?」 | `/api-v2/manage/instance?processId=&hostname=`(`instanceId` → `hostname`) | — | ➕ | `processId` 필수 · Java 에이전트 5.6.0.8+ · 호스트 + PID 정합(`plans/125` A-6 ③) → **W7** |
| O-16 | 「제니퍼 데이터 서버 자원·도메인 배치·저장 경로」 | `/api-v2/manage/data-server/domains` · `…/resource`(CPU·메모리·디스크) · `…/system-property-config` · `/api-v2/manage/db/path/{d}` | — | ➕ | 시스템 속성 값은 자격증명 제거 규칙을 거친다 → **W7** |
| O-17 | 「제니퍼 사용자 목록」 · 「OOO 계정 정보」 | `/api/auth/userlist`(`id`·`name`·`email`·`phoneNumber`) · `/restapi/users` · `/restapi/user/{id}`(`id`·`name`·`group`·`allowIp`) | — | ➕ | **비밀번호 필드는 항상 버린다** · 이메일·휴대폰 표시·권한은 `plans/134` G-11 → **W7** |

## 4. 장애

| ID | 이런 질문 | 답하는 Open API(쓰는 필드) | 도구 · 채팅 보기 | 지금 | 비고 · 구현 계획 |
|---|---|---|---|---|---|
| F-01 | 「web01 최근 30분 제니퍼 이벤트」 | `/api/dbsearch/event`(`time`·`eventLevel`·`errorType`/`metricsName`·`message`·`value`·`txid`) | `apm_events` · `apm.events` | ✅ | 지금 기본 30분 · 최대 24시간 · 최근 50건(상한은 D-296으로 제거 — W1). **폴스타 서버 알람이 아니다**(B-1) |
| F-02 | 「web01 fatal 이벤트만」 · 「warning 이상」 | 같음(게이트웨이가 최소 레벨로 거른다) | `apm_events(level)` | ◐ | 채팅은 `level`을 넘기지 않아 전 레벨이 나온다 → **W1** |
| F-03 | 「web01 오늘 어떤 예외가 많았어」 | `/api/dbsearch/error`(`errorType`·`message`) | `apm_events`의 `errors_by_type`(유형별 상위 10) | ◐ | 게이트웨이는 집계하지만 **채팅이 `errors_by_type`을 버린다** → **W1** |
| F-04 | 「web01 OutOfMemory 오류만」 | `/api/dbsearch/error`(`error_type` — 대문자) | — | ➕ | `error_type` 쿼리 키 허용(D-296) → **W1** |
| F-05 | 「web01 지금 왜 느려?」 · 「느린 트랜잭션 상위 10」 | `/api/transaction/time`(`responseTime`·`cpuTime`·`sqlTime`·`fetchTime`·`externalcallTime`·`networkTime`·`errorType`) | `apm_slow_transactions` · `apm.slow_tx` | ◐ | 행(트랜잭션별 시간 분해)은 나온다. **기간을 말해도 항상 최근 10분**이고 고지는 「현재값 기준」으로 잘못 나간다 · p95·SQL/외부 호출 비중(`summary`)과 시 단위 보충(`hourly`)은 채팅이 버린다 → **W1** |
| F-06 | 「그 트랜잭션 프로파일 보여줘」 · 「그때 실행된 SQL」 | `/api/transaction/txid` · `/api/transaction/profile.txt` · `/api/transaction/sql` | `apm_transaction_profile`(앞 결과의 `profile_ref`를 그대로) | 🔎 | 채팅 보기 없음 → **W5**(D-296 — 채팅에서도 허용) |
| F-07 | 「이 GUID 거래가 어느 서버를 거쳤나」 | `/api/transaction/guid`(`domain_id`·`guid`·`start_time`·`end_time`) | — | ➕ | 연계 거래 추적 → **W5** |
| F-08 | 「web01 요청이 밀리고 있어?」 · 「DB 풀 고갈이야?」 · 「GC 지연이야?」 | 위 API 조합 → 게이트웨이 결정적 판정 8종(`was_service_queuing`·`was_thread_pool_exhaustion`·`was_db_pool_exhaustion`·`was_gc_stall`·`was_heap_pressure`·`was_slow_sql`·`was_external_call_delay`·`was_error_burst`) | 각 도구의 `was_signals` | 🔎 | 조사·알람 판정에는 쓰인다. **채팅은 판정을 버린다** — 원자료 행만 나온다 → **W1** · 임계는 잠정치(`apm_gateway/config/was_signatures.yaml`) |
| F-09 | 「배포 직후 오류가 늘었나」 | `/api-v2/deploy/{domainId}` + `/api/dbsearch/error` | — | 🔶 | → **W2** |
| F-10 | 「오늘 fatal 이벤트가 난 WAS 전부」 | 도메인별 `/api/dbsearch/event`(인스턴스 지정 없이) | — | ◐ | 지금은 대상 없으면 목록 앞 10대만 조회한다. D-296으로 전 도메인(폴러가 켜져 있으면 폴러가 모아 둔 기록) → **W3** |
| F-11 | (알람) 「이 서버 알람이 앱에 영향이 있나」 | `/api/dbsearch/event`(fatal) | `noise_gate` `app_impact` 승격 | ✅ | 채팅 질문이 아니라 알람 판정이다(옵트인 `NOISE_APP_IMPACT_ENABLED`) |
| F-12 | (알람) 제니퍼 이벤트를 알람으로 받기 | `/api/dbsearch/event` 폴링 | 게이트웨이 폴러 → `alarm:raw` | ✅ | 옵트인 `APM_EVENT_POLLER_ENABLED` · 관제 화면 배지 「제니퍼」 |
| F-13 | 「web01 장애 원인 조사해 줘」 | 위 전부 | `sre_agent` 조사 | 🔎 | 알람 → 자동 조사는 된다. 채팅에서 조사로 넘기는 경로(`fault_diagnosis`)는 3단에만 있고 2단 배선은 `plans/121` 소유(미착수) |
| F-14 | 「web01 CPU랑 WAS 힙 같이」 · 「서버 상태 종합(OS·WAS·담당자)」 | 폴스타 + 제니퍼 (+ ITAM) | `data_query` + `apm_query` | ✅ | `plans/125` 조합 응답 · 4소스 시나리오 `testdata/scenarios/fs_four_source.yaml` |
| F-15 | 「지금 걸려 있는 그 요청의 SQL·파라미터 상세」 | `/api-v2/active-service/detail/{d}/{txid}?sessionId=&threadHash=`(`userId`·`guid`·`sql`·`http.method`·`http.query`) | — | ➕ | 실행 중 서비스 목록(O-06)의 행에서 이어 묻는다 · 사용자 ID·SQL·HTTP 파라미터 표시와 권한은 `plans/134` G-11 → **W7** |

## 5. 답할 수 없는 질문

| ID | 이런 질문 | 이유 |
|---|---|---|
| X-01 | 「어느 서비스에서 어디로 장애가 번졌나」(호출 관계·토폴로지) | Open API에 토폴로지 경로가 없다 — 콘솔(MSA 뷰) 전용(`plans/87` §2.2). 단 연계 거래 하나의 경로는 F-07(GUID)로 일부 본다 |
| X-02 | 「스레드 덤프 떠 줘」 · 「GC 돌려 줘」 · 「재기동해 줘」 · 「PLC 상한 낮춰 줘」 | 쓰기·제어는 막는다(D-296 ② · D-003). 조치는 권고만 하고, 승인 후 실행은 별도 실행 평면(D-195 ③ · `plans/87` J6 — 미구현). 덤프·PLC API는 공개 자료에 없다 |
| X-03 | 「이벤트 임계값 바꿔 줘」 · 「룰 꺼 줘」 | 관리 **쓰기** API — 막는다. 룰 **조회**는 O-11로 된다 |
| ~~X-04~~ | ~~민감 정보 조회~~ | **D-296으로 허용** → O-13(환경변수) · O-14(로드된 클래스) · O-17(사용자 목록) · F-15(실행 중 요청 상세) |
| X-05 | 「제니퍼 AI가 찾은 이상 징후」 | 제니퍼 인사이트(이상 탐지·상관 분석)는 콘솔 기능이고 Open API가 없다 |
| ~~X-06~~ | ~~데이터 서버 관리 조회~~ | **D-296으로 허용** → O-16 |

---

## 6. 묻는 요령 (지금 기준)

1. **대상 서버(hostname)를 함께 적는다.** 예: 「web01 WAS 응답시간」. 대상이 없으면 인스턴스 목록을 보여 주거나(목록 질문), 목록 앞 10대를 골라 조회한다.
2. 소스를 확실히 하려면 **「제니퍼」·「APM」을 넣는다**(데이터 소스 유사어 — D-293). 「WAS」만으로도 제니퍼로 간다(WAS 정보는 제니퍼 단독 소스).
3. 기간은 지금 **응답시간 10분 · 이벤트 24시간**까지다. 그보다 긴 기간은 잘라서 보고 고지한다. 하루 넘게 지난 기간은 조회하지 않는다. (D-296으로 이 제한들을 없애기로 했다 — 구현 전)
4. 결과가 0건이거나 제니퍼에 연결할 수 없으면 사유를 알려 주고, **폴스타 값으로 대신 답하지 않는다**(`plans/132` G-1).
5. 서버 알람(폴스타)과 WAS 이벤트(제니퍼)는 다르다. 「제니퍼 이벤트」·「WAS 이벤트」라고 적으면 제니퍼를 본다.

## 7. API 지도 — 제니퍼 읽기 경로와 이 저장소의 처리

`.xml` 변형(5개)과 v1 POST 변형은 같은 데이터의 다른 표현이라 넣지 않는다(D-296 ②). 「허용」 = 지금 `allowlist.py`의 16템플릿. 「허용 확정」 = D-296으로 허용하기로 정했고 구현 전.

| 경로(GET) | 필수 · 선택 파라미터 | 지금 | 쓰는 곳 · 질문 ID |
|---|---|---|---|
| `/api/domain` | — | 허용 · 사용 | 인스턴스 목록 1단계 · `gateway_health` · S-01 |
| `/api/instance` | `domain_id` | 허용 · 사용 | `apm_instance_map` · S-01 |
| `/api/realtime/instance` | `domain_id` · (`instance_id`) | 허용 · 사용 | `apm_app_health`·`apm_runtime_health`·`apm_resource_pool` · S-02·S-05·S-08·O-01·O-05 |
| `/api/realtime/domain` | (`domain_id`) | 허용 확정 | S-06·S-08 → W3 |
| `/api/realtime/business` | `domain_id` · (`business_id`) | 허용 확정(D-290 ④ 개정) | S-09 → W4 |
| `/api/dbmetrics/instance` | `domain_id`·`instance_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | 허용 · 3지표만 사용 | `apm_runtime_health` 추세 · O-02·O-03·S-11 |
| `/api/dbmetrics/domain` | `domain_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | 허용 확정 | S-07 → W3·W6 |
| `/api/dbmetrics/business` | `domain_id`·`business_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | 허용 확정(D-290 ④ 개정) | S-09 → W4 |
| `/api/metrics` | — | 허용 · **미사용** | O-10 → W2 |
| `/api/business` | `domain_id` | 허용 확정(`plans/130` — D-290 ④) | 업무명 해석 · S-09 |
| `/api/activeService/list` | `domain_id` · (`instance_id`) | 허용 · 사용 | `apm_active_services`·`apm_resource_pool` · O-05·O-06 |
| `/api/transaction/time` | `domain_id`·`start_time`·`end_time` · (`instance_id`) · 1분 창 | 허용 · 사용 | `apm_slow_transactions`·`apm_app_health` · S-03·F-05 |
| `/api/transaction/guid` | `domain_id`·`guid`·`start_time`·`end_time` | 허용 확정 | F-07 → W5 |
| `/api/transaction/txid` · `/profile.txt` · `/sql` | `domain_id`·`txid`·`time` | 허용 · 조사에서만 | `apm_transaction_profile` · F-06 |
| `/api/dbsearch/event` | `domain_id`·`start_time`·`end_time` · (`instance_id`·`level`) | 허용 · 사용 | `apm_events` · 폴러 · F-01·F-02·F-10·F-11·F-12 |
| `/api/dbsearch/error` | 같음 · (`instance_id`·**`error_type`**) | 허용 · 사용(`error_type` 키는 허용 확정) | `apm_events` · F-03·F-04 |
| `/api/status/application` | `domain_id`·`start_time`·`end_time`(시 단위) · (`instance_id`·`max_row`·**`sort_by_metrics`·`application_name`**) | 허용 · 사용(굵은 키는 허용 확정) | `apm_app_health`·`apm_slow_transactions`의 `hourly` · S-04·S-10 |
| `/api/status/sql` · `/api/status/external_call` | 같음(시 단위) · (**`instance_id`·`sort_by_metrics`·`max_row`**) | 허용 · **미사용**(선택 키는 허용 확정) | O-07·O-08 → W2 |
| `/api-v2/deploy/{domainId}` | `startTime`·`endTime`(25시간 이하 · 정본 미수록) | 허용 · **미사용** | O-09·F-09 → W2 |
| `/api-v2/manage/rule/event/{error/{d} · metric/{d}/{대상} · compare/{d}/{대상}}` | 경로 변수 | 허용 확정(v2 매뉴얼) | O-11 → W7 |
| `/api-v2/manage/rule/active-service-color-range-boundary` | — | 허용 확정(v2 매뉴얼) | O-12 → W7 |
| `/api-v2/environment-variable/{d}` | 경로 `domainId` | 허용 확정(민감 · v2 매뉴얼) | O-13 → W7 |
| `/api-v2/loaded-class/{d}/{i}` | (`search`) | 허용 확정(v2 매뉴얼) | O-14 → W7 |
| `/api-v2/manage/instance` | `processId` · (`hostname`) | 허용 확정(v2 매뉴얼) | O-15 → W7 |
| `/api-v2/manage/data-server/{domains · resource · system-property-config}` · `/api-v2/manage/db/path/{d}` | — · 경로 `domainId` | 허용 확정(스펙 · v2 매뉴얼) | O-16 → W7 |
| `/api/auth/userlist` · `/restapi/users` · `/restapi/user/{id}` | — · 경로 `id` | 허용 확정(민감 · `password` 필드는 버림) | O-17 → W7 |
| `/api-v2/active-service/detail/{d}/{txid}` | `sessionId`·`threadHash` | 허용 확정(민감 · v2 매뉴얼) | F-15 → W7 |
| `/api-v2/test-response/json` · `/api-v2/auth-test` | — | 넣지 않음(시험 경로 — 데이터 없음) | — |
| 쓰기·제어(POST·PUT·DELETE 전부) | — | **거부 유지**(D-296 ② · D-003) | X-02·X-03 |

## 8. 응답을 읽는 법

- **현재값**(`apm.runtime`·`apm.pool`·`apm.active`)은 조회한 순간의 값이다. 과거 기준 시각으로 물으면 현재값은 생략하고 `[한계]`로 알린다.
- **시 단위 통계**(`/api/status/*`)는 정시 경계로 집계한 값이다 — 「10:17~10:42」를 물어도 10시~11시 값이다(스펙 *"Units below hour must be set to zero"*).
- **p95·에러율**은 스펙에 백분위 필드가 없어 게이트웨이가 X-View 1분 창 결과로 계산한다. X-View가 전수인지 표본인지는 미확인이다(U-14).
- 여러 제니퍼 소스(은행존 · 공동존 · 레거시)가 있으면 행에 `source_id`가 붙고, 한 소스가 실패하면 그 소스만 빠진 채 `[한계]`로 알린다(D-287).
- 인스턴스 정합 신뢰도가 medium 이하이거나 정합하지 못한 대상(`instance_unresolved`)은 결과와 함께 고지된다.
- (D-296 구현 뒤) 큰 조회는 진행 상황을 보여 주고, 결과가 크면 채팅에는 요약·상위 행을, 전체는 결과 다운로드(CSV)로 준다. 가린 값(비밀번호·비밀 환경변수 값)은 「[가림]」으로 표시된다.

## 9. 참조

- 연동 절차·통제·도구 계약: `docs/31_jennifer_integration_guide.md` §5·§6 · 계약 정본 `spec/SPEC-apm-gateway.md`
- 설계·제품 조사: `plans/87-WIP-jennifer-apm-integration.md` §2.2(데이터 카탈로그) · §0.9(스펙 대조)
- 채팅 처리기: `plans/125-WIP-four-source-intent-routing-and-composed-answer.md` · `src/orchestration/apm_query.py` · 보기 정본 `config/db_registry.yaml` `solutions[apm].views`
- 대상 해석(인스턴스 이름·업무명): `plans/130-TODO-apm-instance-and-business-name-targeting.md` · 소스 선별: `plans/132-WIP-source-first-selection.md`
- 이 표의 빈칸을 채우는 계획: **`plans/134-TODO-jennifer-question-coverage.md`**
- 제니퍼 원천: Open API 스펙 5.6.4 `https://raw.githubusercontent.com/jennifersoft/jennifer5-open-api/gh-pages/index.html`(`plans/87` [J-4]·[J-23]) · v2 매뉴얼 `https://github.com/jennifersoft/jennifer5-open-api-v2-manual`의 `spec/*.md`(`plans/87` [J-5])
- 결정: **D-296**(조회 범위) · D-003 · D-195 · D-274 · D-281 · D-283 · D-287 · D-290 · D-293 · D-295

## 10. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-02 | 최초 작성(사용자 지시 *"조사한 사용가능한 질문들은 docs폴더의 사용 사례를 별도로 정리하고 사용가능한 질문을 모두 사용할 수 있도록 구현할 계획을 plans폴더에 계획파일을 정리하라."*). 질문 35건(S 11 · O 10 · F 14) + 답할 수 없는 질문 6건(X) · API 지도 · 실측으로 바로잡은 것: 채팅이 봉투 집계(`summary`·`hourly`·`errors_by_type`)와 `was_signals`를 버린다 · `apm.runtime`·`apm.slow_tx` 보기에 기간 설정이 없어 추세·기간 지정이 채팅에서 안 된다 · `apm.app_health` 10분 상한 때문에 시 단위 통계에 도달하지 못한다 · 파서 `limit`·이벤트 `level`이 채팅에서 전달되지 않는다 |
| 2026-10-02 | **D-296 반영**(사용자 *"모든 api는 허용하고 조회할 수 있는 범위는 모두 가능하도록 정한다."* · 「민감 조회 API까지」 · 「보호 한도도 올림」) — 읽기 API 전부 허용(쓰기·제어 차단 · 비밀번호·비밀 값 제거) · 자체 상한 제거. 질문 43건으로 확대: O-11(이벤트 룰 조회) · O-12(액티브 색상 경계) · O-13(환경변수) · O-14(로드된 클래스) · O-15(프로세스 → 인스턴스) · O-16(데이터 서버) · O-17(사용자 목록) · F-15(실행 중 요청 상세) 신설 · X-04·X-06 흡수 · X-03은 변경만 남김 · S-09 ⛔ → ➕ · 표지 ⛔ 삭제 · Wave 번호를 `plans/134` v1.1에 맞춤(W7 = 설정·관리·민감 · W8 = 보기 선택) · API 지도에 v2 매뉴얼 읽기 경로 추가 |
