# 33. 제니퍼(JENNIFER) APM 사용 사례 — 무엇을 물을 수 있고 어떤 API가 답하는가

> **정본 범위**: 제니퍼 Open API로 답할 수 있는 질문을 **서비스 제공 현황 · 운영 · 장애** 세 갈래로 모으고, 질문마다
> 답하는 Open API · 게이트웨이 도구(`apm_*`) · 채팅 보기(`apm.*`) · **지금 상태**를 적는다. 연동 절차(설정·통제·검증)는
> `docs/31_jennifer_integration_guide.md`가, 설계 근거는 `plans/87`이, 이 표의 빈칸을 채우는 구현 계획은
> **`plans/134-WIP-jennifer-question-coverage.md`**가 정본이다.
> **작성**: 2026-10-02 · 기준 = `multiintent` 작업 트리(HEAD `22f9628` + 미커밋) · 제니퍼 Open API 공식 스펙 5.6.4(39경로 — `plans/87` [J-23] 원천을 2026-10-02 다시 받아 파싱) · v2 매뉴얼 `spec/*.md` 8건
> **범위 결정(D-296 · 2026-10-02)**: 제니퍼 Open API의 **읽기(GET) 경로는 민감·관리 조회까지 전부 허용**하고 **쓰기·제어는 계속 막는다**. 비밀번호·비밀 환경변수 값은 어떤 경로로도 내보내지 않는다. **조회 범위의 자체 상한(기간·대상·건수)은 없앤다**. 이 결정은 계획 단계이고 **코드는 아직 그대로**다 — 아래 「지금」 칸은 현재 동작이다.
> **활용 계약(D-299 · 134 v1.2)**: 아래 43사례는 최소 회귀 집합이다. 전체 읽기 API·인자·필드, 복수 대상/보기/소스, 기간 비교·변경 전후·GUID 연계 분석까지 확장한다. 채팅·조사·알람 각각의 실제 경로로 검증하며 F-13 연동 전 전체 완료로 선언하지 않는다. 현재 상태 칸은 이 계획 개정으로 바뀌지 않는다.
> **상태 칸은 시점 값이다.** `plans/134`의 Wave가 랜딩할 때마다 같은 작업에서 이 문서의 상태 칸을 고친다(`plans/134` W9).
> **2026-10-06 갱신(W5·W6 독립분·W7 구현 · D-302)**: GUID 추적·앞 결과 행 참조(F-06·F-07·F-15) · 변경 전후(F-09) · 관리·민감 조회(O-11~O-17)를 올렸다. 근거 = 합성 픽스처 실프로세스 종단 + 로컬 MLX 9B 47문항 선택 측정(보기 47/47 · 무관한 기본 보기 대체 0). 수정 뒤 선택 정확도 재측정은 내부망 FabriX 몫이다(D-240 부기 — MLX 최소화).
> **2026-10-06 갱신(W3·W4 구현 · D-310)**: 서비스·업무·전 인스턴스 순위·전 도메인 이벤트(S-06~S-09 · F-10)와 여러 대상 한 번에(`targets`)를 올렸다. 근거 = 합성 픽스처(목 Open API 2소스) ↔ 게이트웨이 실프로세스 ↔ 본체 2단 종단 검증(가짜 LLM) + 로컬 MLX 9B 보기 선택 실측(14문항 중 13 정답). **실 제니퍼 응답은 미검증(W10)** 이고 350도메인 규모의 실제 소요는 재지 않았다. 운영 사다리가 아직 1단이라 채팅 효력은 2단 전환 뒤다(D-251).
> **2026-10-02 갱신(W0-B·W1·W2 구현 · D-300)**: 상태 칸은 **스펙 기반 합성 픽스처로 게이트웨이 실프로세스 ↔ 본체 처리기 종단 검증**과 **로컬 MLX 9B 보기 선택 실측(29문항)**으로 확인한 것만 올렸다. 실 제니퍼 응답은 미검증(W10)이고, 운영 사다리가 아직 1단이라 채팅 경로는 2단 전환 뒤 효력이 난다(D-251).

---

## 0. 30초 요약

- 게이트웨이(`apm_gateway/`)는 제니퍼 Open API 중 **GET 16경로**를 부른다(허용목록 — `apm_gateway/apm_gateway/adapters/jennifer/allowlist.py`). **W2로 16경로 모두 부르는 도구가 생겼다** — 데이터 도구 11종 + 작업 도구 3종(종전 8종 상한 폐지 — D-299 ③).
  D-296으로 나머지 읽기 경로도 허용하기로 정했다 — **W5·W7(2026-10-06)로 GUID·관리·민감 조회 20경로를 더해 허용목록 36템플릿 · 데이터 도구 18종**이다. `plans/130` W2(2026-10-06)로 업무 정의 `/api/business`를 더해 **37템플릿**이다(업무명 해석용 · 새 도구 없음). **`plans/134` W3·W4(2026-10-06)로 실시간 도메인·실시간 업무·도메인 시계열·업무 시계열 4경로를 더해 허용목록 41템플릿 · 데이터 도구 21종**(`apm_service_status`·`apm_business`·`apm_fleet` 추가 — 서비스·업무·전 인스턴스 순위·전 도메인 이벤트)이다.
- 채팅은 2단 처리기 `apm_query`가 **보기 12종**(종전 7종 + W2 `apm.app_stats` · `apm.sql_stats` · `apm.external_stats` · `apm.metrics` · `apm.changes`)과 조건(`view_args` — 개수·전체·레벨·오류 유형·정렬·지표 이름·간격)으로 조회한다
  (`config/db_registry.yaml` `solutions[apm].views`). **W3·W4(2026-10-06)로 보기 28종**(`apm.service`·`apm.service_trend`·`apm.ranking`·`apm.fleet_events`·`apm.business`·`apm.business_trend` 추가 — 아래 앞 22종 설명은 W5·W6·W7 시점)이다. **W5·W6·W7(2026-10-06)로 보기 22종** — 앞 결과 행을 번호로 골라 잇는 `apm.profile`·`apm.trace`·`apm.active_detail` · 변경 전후 `apm.change_impact` · 설정·관리 `apm.event_rules`·`apm.process`·`apm.jennifer_server`·`apm.loaded_classes`·`apm.environment`·`apm.users`.
- **(W1로 교정)** 채팅은 봉투의 집계(`summary` · `hourly` · `errors_by_type`)와 WAS 판정(`was_signals`)을 (보기, 대상)별로 옮겨 답 끝 **「판정·집계」 블록**으로 그대로 싣는다. 보기 창 상한이 없어져 기간을 말하면 추세·시 단위 통계가 붙는다.
- **(W0-B)** 오래 걸리는 조회는 **작업으로 접수**하고(작업 카드에서 진행·취소·결과 보기·전체 CSV), 큰 결과는 화면 앞 500행 + 전체 결과 파일이다.
- 채팅 조회는 **사다리 2단(기준 경로)**에서만 된다. 1단(`deep_agent`)은 고정 처리기 목록만 도구로 쓰고, 3단은 계획 루프(`TIER3_PLAN_LOOP_ENABLED` · 기본 off)일 때만 같은 처리기를 부른다.
  또 제니퍼 엔드포인트(`MCP_SOURCE_ENDPOINTS`의 `apm`)가 설정된 배포에서만 동작한다.
- 대상은 지금 **hostname**으로 지정한다. 인스턴스 이름(부분 일치)·업무명으로 묻는 기능은 `plans/130`으로 구현됐다(2026-10-06). **`plans/134` W3: 대상이 여럿이어도(예 12대) 한 번에 조회**하고 대상 수·보기 수 절단이 없다. 대상을 말하지 않은 대상 필수 보기는 인스턴스 목록 **전부**를 조회한다.
- **로컬 9B의 한계(W2 실측)** — 보기는 29문항 모두 냈고 무관한 기본 보기 대체는 0이지만, 말하지 않은 조건을 끼우는 일이 잦았다(조건이 필요 없는 12문항 중 10). 그중 F-01·F-03은 범위가 좁아졌다(◐). 운영 모델(FabriX)로 다시 재야 한다.
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
| S-01 | 「제니퍼 인스턴스 목록 보여줘」 · 「web01에 떠 있는 WAS는?」 · 「도메인 ID 1000인 인스턴스 목록」 | `/api/domain` → 도메인별 `/api/instance`(`name`·`hostName`·`ipAddress`·`status`·`version`·`platform`) | `apm_instance_map` · `apm.instances` | ✅ | 상한 없이 전 인스턴스(큰 목록은 화면 앞 500행 + 전체 결과 파일 — W1) · 대상 없이 물으면 목록(D-293) · 도메인 ID를 말하면 그 도메인 인스턴스만(`plans/130` W1-D — 게이트웨이 `domain_id` 필터) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| S-02 | 「web01 WAS 지금 응답시간·TPS 알려줘」 | `/api/realtime/instance`(`responseTime`·`tps`·`activeService`·`concurrentUser`·`rejectRate`) | `apm_app_health` · `apm.app_health` | ✅ | 현재값 |
| S-03 | 「web01 최근 10분 에러율·p95」 | `/api/transaction/time`(1분 창을 나눠 부른다 — 게이트웨이가 p50·p95·에러율 계산) | `apm_app_health`(행의 `window`) | ✅ | 창 상한 제거(W1) — 요청 기간 전체를 1분 조각으로 조회(오래 걸리면 작업으로 접수 — W0-B) · p50·p95·오류율은 「판정·집계」 블록 · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| S-04 | 「web01 오늘 오전 처리 건수와 실패율」 · 「지난 3시간 상위 애플리케이션」 | `/api/status/application`(시 단위 · `calls`·`failures`·`responseTime`·`maxResponseTime`) | `apm_app_health`의 `hourly` · `apm_status_stats`(kind=application) · `apm.app_stats` | ✅ | 시 단위 통계가 채팅에 실린다(W1 — 합계는 전 애플리케이션 행 · 정시 경계 고지) · URL별 통계 보기(W2) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| S-05 | 「web01 오늘 방문자 수·호출 수」 | `/api/realtime/instance`(`visitDay`·`visitHour`·`hitDay`·`hitHour`) | `apm_app_health` | ◐ | 기능은 됨 — 실시간 행에 방문·호출 수 4칸(W1 · 단위·「하루」 경계 미확인 고지). **로컬 9B는 이 질문에 통계 보기(`apm.app_stats`)를 골랐다**(W2 실측 오답) — 운영 모델 측정 필요 |
| S-06 | 「인터넷뱅킹 서비스 지금 현황」 · 「오늘 서비스별 방문자」 | `/api/realtime/domain`(`visitDay`·`hitDay`·`tps`·`responseTime`·`concurrentUser`·`activeUser`·`rejectRate`) | `apm_service_status` · `apm.service` | ✅ | 서비스(제니퍼 도메인) 현재값 — 이름 없음 = 한 번에 전 도메인 · 이름 = 단계 검색(못 찾으면 **데이터 호출 없이** 「찾지 못함」 + 비슷한 이름 ≤3 · 전체로 넓히지 않음) · **W3 구현** · 합성 픽스처 종단(실 제니퍼 미검증 — W10 · 무인자 호출의 전 도메인 응답 모양 미확인). 가동·중지·미라이선스 인스턴스 수(`instanceCount`)는 `/api/domain` 필드라 아직 안 싣는다 |
| S-07 | 「인터넷뱅킹 오늘 시간대별 호출 수·응답시간」 | `/api/dbmetrics/domain`(도메인 지표 41종 — `service_count`·`service_time`·`service_err_count`·`visit_hour`·`max_tps` 등 · 지표 1개/호출) | `apm_metrics`(scope=domain) · `apm.service_trend` | ◐ | **W3 구현** — 서비스 시계열(지표를 말하지 않으면 기본 지표: 평균 응답시간·호출 수·오류 수 · 모르는 지표는 빼고 조회하고 고지) · 합성 픽스처 종단(실 제니퍼 미검증). **◐ 사유**: 로컬 9B가 「서비스 시간대별 추세」를 통계 보기(`apm.app_stats`)로 고른 1건(MLX 14문항 중 정답 13 — 운영 모델 FabriX 재측정 필요) · 하루 넘게 지난 기간은 **W6** |
| S-08 | 「지금 응답시간 가장 느린 WAS 5개」 · 「TPS 높은 WAS 순위」 | `/api/realtime/instance`(`domain_id`만 주면 그 도메인 전 인스턴스) | `apm_fleet(mode=ranking)` · `apm.ranking` | ✅ | **W3 구현** — 범위 안 **모든** 도메인의 인스턴스를 모은 뒤 정렬(앞 10대 절단 제거 · 지표 30종 · 기본 응답시간) · 일부 도메인·소스가 실패하면 **「잠정 순위」**로 표시 · 값 없는 인스턴스는 순위 밖으로 세어 알림 · 서비스 이름으로 좁힐 수 있다 · 독립 오라클 대조(합성 도메인 120 × 소스 2) 통과. 운영 약 350도메인의 실제 소요는 **미측정**(오래 걸리면 작업으로 접수 — 진행 표시) |
| S-09 | 「대출 업무 응답시간·TPS」 · 「업무별 현황」 | `/api/business` → `/api/realtime/business`(`businessName`·`responseTime`·`tps`·`activeService`) · `/api/dbmetrics/business`(추세) | `apm_business` · `apm.business` · `apm.business_trend` | ✅ | **W4 구현** — 업무 단위 현재값(TPS·응답시간·액티브·동시 사용자)·업무 목록(정의·규칙) · 추세 `apm.business_trend` · 업무 이름이 여러 업무에 맞으면 전부 조회하고 이름 목록을 알림 · 못 찾으면 데이터 호출 없이 「찾지 못함」. 합성 픽스처 종단(실 제니퍼 미검증 — `business_id` 존중 여부 W10) · `plans/130`: 업무명 → 인스턴스 해석(「결제 업무 WAS 응답시간」(가상)은 인스턴스별 값)은 그대로 |
| S-10 | 「오늘 호출 많은 URL 상위 10」 · 「/login.do 응답시간」 | `/api/status/application`(`sort_by_metrics`·`application_name`·`max_row`) | `apm_status_stats`(kind=application · `sort_by`·`n`·`full`·`application_name`) · `apm.app_stats` | ✅ | URL별 시 단위 통계 상위 N·전체·이름 지정(W2) · 정렬 기준 예 calls·responseTime(원천이 거부하면 전체를 받아 로컬 정렬 + 고지) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| S-11 | 「지난달 web01 일평균 응답시간」 · 「지난주 대비 처리량」 | `/api/dbmetrics/instance`(`interval_minute` · `service_time`·`service_count` 등) | — | ✖ | 지금 채팅은 **기간 끝이 하루 넘게 지난 질문은 조회하지 않는다**(D-283 ④). D-296이 이 규칙을 폐지했다(제니퍼 보존 기간까지) → **W6** |

## 3. 운영

| ID | 이런 질문 | 답하는 Open API(쓰는 필드) | 도구 · 채팅 보기 | 지금 | 비고 · 구현 계획 |
|---|---|---|---|---|---|
| O-01 | 「web01 WAS 힙 사용률·GC·CPU」 | `/api/realtime/instance`(`heapUsed`·`heapCommitted`·`gcTimeUsage`·`procCPU`·`procMemory`·`threadCurrent`) | `apm_runtime_health` · `apm.runtime` | ✅ | 현재값 |
| O-02 | 「web01 최근 1시간 힙·GC 추이」 | `/api/dbmetrics/instance`(5분 간격 · `heap_used`·`heap_committed`·`gc_time_usage`) | `apm_runtime_health`의 `trend` | ✅ | 기간을 넘겨 추세가 붙는다(W1 — C-2 교정) · 모르는 지표 이름을 내면 빼고 기본 추세로 조회 + 고지(W2 검증 수정) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증)(보기) |
| O-03 | 「web01 소켓 수·파일 수·시스템 CPU 추이」 | `/api/dbmetrics/instance` + `/api/metrics`(인스턴스 지표 카탈로그 60종) | `apm_runtime_health`(`metrics`·`interval_minute`) · `apm.runtime` · 지표 목록 `apm_metrics` · `apm.metrics` | ◐ | 기능은 됨 — 인스턴스 지표 카탈로그 전부를 추세로(W2 · 종단 `socket_count` 확인). **로컬 9B는 「소켓 수 추이」에 지표 목록 보기를 고르거나(1/2) 영역 라벨을 잘못 달았다**(W2 실측) — 운영 모델 측정 필요 |
| O-04 | 「web01 메모리 누수 의심돼?」 · 「GC 때문에 느린 거야?」 | O-01·O-02 + 게이트웨이 판정 `was_heap_pressure`·`was_gc_stall` | `apm_runtime_health`의 `was_signals` | ✅ | 추세 + 판정(`was_heap_pressure`·`was_gc_stall`)이 「판정·집계」 블록으로 실린다(W1) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증)(보기) |
| O-05 | 「web01 DB 커넥션 풀 상태」 | `/api/realtime/instance`(`activeDBConnection`·`averageDbPoolIdleCount`·`averageDbPoolConfiguredCount`) + `/api/activeService/list`(데이터소스별) | `apm_resource_pool` · `apm.pool` | ✅ | 현재값 · WAS 스레드 풀 상한 필드는 스펙에 없어 근사(`[한계]`) |
| O-06 | 「web01 지금 실행 중인 서비스」 · 「오래 걸리는 요청」 | `/api/activeService/list`(`elapseTime`·`status`·`runningMode`·`runningFullText`) | `apm_active_services` · `apm.active` | ✅ | 상위 N·전부(`n`·`full` — W1) · 실행 모드별 집계 · `active_ref`(F-15 입력) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| O-07 | 「web01 오늘 가장 오래 걸린 SQL」 · 「호출 많은 SQL」 | `/api/status/sql`(시 단위 · `name`·`calls`·`failures`·`responseTime`·`maxResponseTime`) | `apm_status_stats`(kind=sql) · `apm.sql_stats` | ✅ | SQL별 시 단위 통계(W2) · SQL 문은 리터럴 마스킹 · 화면은 300자 · 전문은 CSV · 「가장 오래 걸린」 = `sort_by=responseTime`(9B는 `response_time`을 냈다 — 원천이 거부해도 전체를 받아 로컬 정렬) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증)(보기) |
| O-08 | 「web01 외부 호출 중 느린 곳」 | `/api/status/external_call`(시 단위 · 같은 필드) | `apm_status_stats`(kind=external_call) · `apm.external_stats` | ✅ | 외부 호출별 시 단위 통계(W2) · URL 값 마스킹 · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증)(보기) |
| O-09 | 「web01 최근 하루 배포(소스 변경) 있었나」 | `/api-v2/deploy/{domainId}`(`collectTime`·`instanceId` · 25시간 이하 · 5.6.0.5+) | `apm_source_changes` · `apm.changes` | ✅ | 변경 감지 시각(25시간 조각 · `change_detected_at` — W2) · 「배포 확정 아님」 고지 · 변경 전후 분석은 **W6** · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| O-10 | 「제니퍼에서 볼 수 있는 지표 목록」 | `/api/metrics`(domain 41 · instance 60 · business 29 · application · sql · externalCall — 로컬 5.7.0.1 녹화본) | `apm_metrics`(mode=catalog) · `apm.metrics` | ✅ | 전 지표 군 목록(소스별 · 6군 — 로컬 녹화본 169개 · W2) · 대상 없이 조회 · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| O-11 | 「제니퍼 이벤트 룰·임계값이 어떻게 설정돼 있나」 · 「힙 경고 기준이 몇 %야?」 | `/api-v2/manage/rule/event/error/{d}` · `…/metric/{d}/{대상}` · `…/compare/{d}/{대상}`(`errorType`·`metricId`·`level`·`applied`·`expression`·`thresholdErrorCount`·`checkTimeRange`) | `apm_config(kind=event_rules)` · `apm.event_rules` | ◐ | 조회됨(W7 · 2026-10-06) — error·metric·compare 룰 · `compare` 먼저/404면 `comparing` · 오류 유형 `applied`·인스턴스 개별 설정 · `autoScriptCommand`는 실행 파일만 남기고 인자 가림. **로컬 9B가 「인스턴스 대상 지표 룰」의 `rule_type`·「OOM 룰 적용 여부」의 `error_type`을 빠뜨렸다**(결과가 넓어지는 쪽 · 내부망 재측정 잔여) · 실응답 W10 |
| O-12 | 「액티브 서비스 느림(빨간색) 기준이 몇 초야?」 | `/api-v2/manage/rule/active-service-color-range-boundary`(경과 시간 경계 4색) | `apm_config(kind=color_boundary)` · `apm.event_rules`(kind=color_boundary) | ✅ | W7 — 경계 4색 행 · 합성 픽스처 실프로세스 종단 · 로컬 9B 선택 정답 · 경계값 단위 미확인(W10) |
| O-13 | 「web01 WAS 환경변수·JVM 시스템 속성」 | `/api-v2/environment-variable/{d}`(`SYSTEM`·`JAVA`) | `apm_environment` · `apm.environment` | ✅ | W7 — 키를 줄이지 않고 비밀 값만 `[가림]` · 값의 이메일·주민번호·휴대폰 가림 · `scope`·`key` 조건 · 로컬 9B 선택 정답(`scope=SYSTEM`을 덧붙이는 경향) · 원값 표시는 G-11 |
| O-14 | 「web01에 로드된 클래스 중 OOO 찾아줘」 | `/api-v2/loaded-class/{d}/{i}?search=`(`className`·`superClassName`·`interfaceClassNames`·`classLoaderName`) | `apm_config(kind=loaded_classes)` · `apm.loaded_classes` | ✅ | W7 — `search` 전달 · 6만 개 초과 거부 사유 고지 · 로컬 9B 선택 정답 |
| O-15 | 「web01의 프로세스 1234는 어느 WAS 인스턴스야?」 | `/api-v2/manage/instance?processId=&hostname=`(`instanceId` → `hostname`) | `apm_config(kind=process_instance)` · `apm.process` | ◐ | W7 — `process_id` 필수(없으면 되묻기) · 버전 조건(서버 5.6.0.21+ · Java 5.6.0.8+) 고지. 로컬 9B에서 **PID 없는 질문이 보기 예문 값으로 채워지던 결함**(선택 재시도 합의)을 고쳤으나 수정 뒤 MLX 재측정은 하지 않았다(내부망 잔여) |
| O-16 | 「제니퍼 데이터 서버 자원·도메인 배치·저장 경로」 | `/api-v2/manage/data-server/domains` · `…/resource`(CPU·메모리·디스크) · `…/system-property-config` · `/api-v2/manage/db/path/{d}` | `apm_config(kind=data_server·db_path·rdb_export)` · `apm.jennifer_server` | ✅ | W7 — 데이터 서버 도메인·자원·시스템 속성(비밀 값 가림 · 설정 값 개인정보 가림) · DB 경로 · 수동 RDB Export 상태 · 로컬 9B 선택 정답 · 메모리·디스크 칸 실재 미확인(W10) |
| O-17 | 「제니퍼 사용자 목록」 · 「OOO 계정 정보」 | `/api/auth/userlist`(`id`·`name`·`email`·`phoneNumber`) · `/restapi/users` · `/restapi/user/{id}`(`id`·`name`·`group`·`allowIp`) | `apm_users` · `apm.users` | ✅ | W7 — 사용자 목록 + 계정 목록 · 계정 1건(`user_id`) · **비밀번호는 키째 제거** · ID·이름 앞 1자 · 메일 `@` 앞 가림 · 전화 `<phone>` · 허용 IP 가림 · 계정 ID 원값은 로그·감사·사유에 없음 · 원값 표시는 G-11 |

## 4. 장애

| ID | 이런 질문 | 답하는 Open API(쓰는 필드) | 도구 · 채팅 보기 | 지금 | 비고 · 구현 계획 |
|---|---|---|---|---|---|
| F-01 | 「web01 최근 30분 제니퍼 이벤트」 | `/api/dbsearch/event`(`time`·`eventLevel`·`errorType`/`metricsName`·`message`·`value`·`txid`) | `apm_events` · `apm.events` | ◐ | 기능은 됨 — 기간·건수 상한 없음(W1). **로컬 9B가 말하지 않은 조건(`level=normal`·`exact`)을 끼워 정상 이벤트만으로 좁혔다**(W2 실측 · 조건 환각) — 운영 모델 측정 필요 |
| F-02 | 「web01 fatal 이벤트만」 · 「warning 이상」 | 같음(게이트웨이가 최소 레벨로 거른다) | `apm_events(level)` | ✅ | `level`·`level_mode`(min·exact) 전달(W1) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| F-03 | 「web01 오늘 어떤 예외가 많았어」 | `/api/dbsearch/error`(`errorType`·`message`) | `apm_events`의 `errors_by_type`(유형별 상위 10) | ◐ | 기능은 됨 — 오류 유형별 건수 전 유형이 「판정·집계」 블록에(W1). **로컬 9B가 `level=fatal`을 끼워 범위를 좁혔다**(W2 실측) — 운영 모델 측정 필요 |
| F-04 | 「web01 OutOfMemory 오류만」 | `/api/dbsearch/error`(`error_type` — 대문자) | `apm_events`(`error_type` · `record=error`) | ✅ | `error_type` 전달(W1 — 정규화 이름 먼저, 0건이면 `ERROR_`·`WARNING_` 표기 재조회 + 맞은 표기 고지) · 오류 기록 행(`record=error`) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| F-05 | 「web01 지금 왜 느려?」 · 「느린 트랜잭션 상위 10」 | `/api/transaction/time`(`responseTime`·`cpuTime`·`sqlTime`·`fetchTime`·`externalcallTime`·`networkTime`·`errorType`) | `apm_slow_transactions` · `apm.slow_tx` | ✅ | 기간 전달(종전 「현재값 기준」 오고지 제거) · 상위 N·전부 · p95·SQL/외부 호출 비중·시 단위 보충이 「판정·집계」 블록에(W1) · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증) |
| F-06 | 「그 트랜잭션 프로파일 보여줘」 · 「그때 실행된 SQL」 | `/api/transaction/txid` · `/api/transaction/profile.txt` · `/api/transaction/sql` | `apm_transaction_profile` · `apm.profile`(앞 결과 행 참조) | ✅ | W5 — 「두 번째 트랜잭션 프로파일」을 앞 결과 표의 번호로 고른다(LLM `ref` · 코드 검증 · 모호하면 되묻기 · 표가 여럿이면 어느 표인지 되묻기) · 오류 기록 행이면 `profile_no` · 2턴·3턴 실 게이트웨이 종단 · 로컬 9B 참조 선택 8/9(누락 1 = 안전한 되묻기) · 채팅은 프로파일 예산 없음(`chat` 토큰일 때) |
| F-07 | 「이 GUID 거래가 어느 서버를 거쳤나」 | `/api/transaction/guid`(`domain_id`·`guid`·`start_time`·`end_time`) | `apm_transaction_trace` · `apm.trace` | ✅ | W5 — 허용된 전 소스·도메인에서 같은 GUID 거래(시작 시각순 · 중복 제거) · 결정적 줄에 조회 구간·「같은 GUID일 뿐 호출 관계 아님」 · 시계 차이 고지 · 로컬 9B 선택 정답 · GUID 형태·도메인 범위 W10 |
| F-08 | 「web01 요청이 밀리고 있어?」 · 「DB 풀 고갈이야?」 · 「GC 지연이야?」 | 위 API 조합 → 게이트웨이 결정적 판정 8종(`was_service_queuing`·`was_thread_pool_exhaustion`·`was_db_pool_exhaustion`·`was_gc_stall`·`was_heap_pressure`·`was_slow_sql`·`was_external_call_delay`·`was_error_burst`) | 각 도구의 `was_signals` | ✅ | 판정(`was_signals`)을 버리지 않고 「판정·집계」 블록으로 결정적으로 싣는다(W1) · 임계는 잠정치 · 합성 픽스처 종단·로컬 9B 선택 정답(W2 검증)(보기) |
| F-09 | 「배포 직후 오류가 늘었나」 | `/api-v2/deploy/{domainId}` + `/api/dbsearch/error` | `apm_change_impact` · `apm.change_impact` | ✅ | W6 — 변경 감지마다 전·후 호출·오류율·평균·p95·오류 기록과 증감(기준 0 = N/A · %p) · 「원인 확정 아님」 고지 · 기간 미지정 = 변경 탐색 24시간 · 로컬 9B 선택 정답(4/4). 「어제 배포 전후」처럼 상대 기간을 말하면 입력 파서 기준일 미주입(`plans/122` ⑥)으로 조회되지 않는다 |
| F-10 | 「오늘 fatal 이벤트가 난 WAS 전부」 | 도메인별 `/api/dbsearch/event`(인스턴스 지정 없이) | `apm_fleet(mode=events)` · `apm.fleet_events` | ✅ | **W3 구현** — 전 도메인 이벤트: 폴러가 켜져 있으면 폴러가 받아 둔 구간은 **버퍼**(메모리)에서 · 나머지는 도메인마다 API(폴러가 꺼져 있거나 재기동 직후는 전부 API) · 실패한 도메인은 0건이 아니라 「확인하지 못함」 · 이벤트 건수는 「M건(표시 n건)」으로 구별. 합성 픽스처 종단(실 제니퍼 미검증 — 이벤트 구간 양끝 포함·늦게 들어오는 이벤트 W10 · 버퍼 20만 건 메모리 미측정) |
| F-11 | (알람) 「이 서버 알람이 앱에 영향이 있나」 | `/api/dbsearch/event`(fatal) | `noise_gate` `app_impact` 승격 | ✅ | 채팅 질문이 아니라 알람 판정이다(옵트인 `NOISE_APP_IMPACT_ENABLED`) |
| F-12 | (알람) 제니퍼 이벤트를 알람으로 받기 | `/api/dbsearch/event` 폴링 | 게이트웨이 폴러 → `alarm:raw` | ✅ | 옵트인 `APM_EVENT_POLLER_ENABLED` · 관제 화면 배지 「제니퍼」 |
| F-13 | 「web01 장애 원인 조사해 줘」 | 위 전부 | `sre_agent` 조사 | 🔎 | 알람 → 자동 조사는 된다. 채팅에서 조사로 넘기는 경로(`fault_diagnosis`)는 3단에만 있고 2단 배선은 `plans/121` 소유(미착수) |
| F-14 | 「web01 CPU랑 WAS 힙 같이」 · 「서버 상태 종합(OS·WAS·담당자)」 | 폴스타 + 제니퍼 (+ ITAM) | `data_query` + `apm_query` | ✅ | `plans/125` 조합 응답 · 4소스 시나리오 `testdata/scenarios/fs_four_source.yaml` |
| F-15 | 「지금 걸려 있는 그 요청의 SQL·파라미터 상세」 | `/api-v2/active-service/detail/{d}/{txid}?sessionId=&threadHash=`(`userId`·`guid`·`sql`·`http.method`·`http.query`) | `apm_active_detail` · `apm.active_detail`(앞 결과 행 참조) | ✅ | W7 — 실행 중 서비스 목록 행의 `active_ref`를 그대로 · 사용자 ID 가림 · SQL 리터럴 가림 · HTTP 쿼리 값 가림 · 현재값 전용 · 로컬 9B 참조 선택 정답 · `sessionId` 필수 여부 W10 |

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

1. **대상 서버(hostname)를 함께 적는다.** 예: 「web01 WAS 응답시간」. 대상이 없으면 인스턴스 목록을 보여 주거나(목록 질문), **인스턴스 전부를 조회**한다(W3 — 앞 10대 절단 제거). 「전체 WAS 중 응답시간 가장 느린 5개」처럼 서버를 정하지 않은 순위는 전 인스턴스 순위(`apm.ranking`)로, 「주문 서비스 지금 상태」·「결제 업무 TPS」(가상 이름)는 서비스·업무 보기로 답한다. 지표 목록처럼 대상이 필요 없는 질문은 대상 없이 조회한다.
   **(`plans/130`)** hostname을 모르면 **인스턴스 이름이나 업무명**으로 물어도 된다. 예: 「abc-was 응답시간」·「결제 업무 WAS 힙 사용률」(가상 이름). 이름이 정확하지 않아도 비슷한 인스턴스를 찾아 조회하고 근거를 알려 준다. 찾지 못하면 다른 인스턴스로 대신 조회하지 않고 비슷한 이름 후보를 보여 준다(`docs/31` §6.4).
2. 소스를 확실히 하려면 **「제니퍼」·「APM」을 넣는다**(데이터 소스 유사어 — D-293). 「WAS」만으로도 제니퍼로 간다(WAS 정보는 제니퍼 단독 소스).
3. **(W1)** 기간·건수 상한이 없다 — 말한 기간 전체를 조회하고, 오래 걸리면 작업으로 접수한다. 「상위 5개」·「전부」·「fatal만」·「OutOfMemory 오류만」 같은 조건이 전달된다. 다만 **하루 넘게 지난 기간은 아직 조회하지 않는다**(W6에서 해상도 선택과 함께 폐지).
4. 결과가 0건이거나 제니퍼에 연결할 수 없으면 사유를 알려 주고, **폴스타 값으로 대신 답하지 않는다**(`plans/132` G-1).
5. 서버 알람(폴스타)과 WAS 이벤트(제니퍼)는 다르다. 「제니퍼 이벤트」·「WAS 이벤트」라고 적으면 제니퍼를 본다.

## 7. API 지도 — 제니퍼 읽기 경로와 이 저장소의 처리

`.xml` 변형(5개)과 v1 POST 변형은 같은 데이터의 다른 표현이라 넣지 않는다(D-296 ②). 「허용」 = 지금 `allowlist.py`의 **41템플릿**(2026-10-06 W3·W4 기준 — 표 아래 행들이 같은 날 W5~W7·130이 더한 경로를 포함한다). 「허용 확정」 = D-296으로 허용하기로 정했고 구현 전(W3·W4 이후 남은 행 없음).

| 경로(GET) | 필수 · 선택 파라미터 | 지금 | 쓰는 곳 · 질문 ID |
|---|---|---|---|
| `/api/domain` | — | 허용 · 사용 | 인스턴스 목록 1단계 · `gateway_health` · S-01 |
| `/api/instance` | `domain_id` | 허용 · 사용 | `apm_instance_map` · S-01 |
| `/api/realtime/instance` | `domain_id` · (`instance_id`) | 허용 · 사용 | `apm_app_health`·`apm_runtime_health`·`apm_resource_pool`·`apm_fleet(mode=ranking)`(W3) · S-02·S-05·S-08·O-01·O-05 |
| `/api/realtime/domain` | (`domain_id`) | 허용 · 사용(W3) | `apm_service_status`·`apm_fleet(service=…)` · S-06·S-08 |
| `/api/realtime/business` | `domain_id` · (`business_id`) | 허용 · 사용(W4 · D-290 ④ 개정) | `apm_business(mode=current)` · S-09 |
| `/api/dbmetrics/instance` | `domain_id`·`instance_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | 허용 · 3지표만 사용 | `apm_runtime_health` 추세 · O-02·O-03·S-11 |
| `/api/dbmetrics/domain` | `domain_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | 허용 · 사용(W3) | `apm_metrics(scope=domain)` · S-07(하루 넘는 기간 W6) |
| `/api/dbmetrics/business` | `domain_id`·`business_id`·`interval_minute`·`metrics`·`start_time`·`end_time` | 허용 · 사용(W4 · D-290 ④ 개정) | `apm_metrics(scope=business)` · S-09 |
| `/api/metrics` | — | 허용 · 사용(W2) | `apm_metrics`(카탈로그 · 지표 이름 검증) · O-03·O-10 |
| `/api/business` | `domain_id` | 허용 · 사용(`plans/130` W2 · D-290 ④) | 업무명 해석 · `apm_business(mode=list)`(W4) · S-09 |
| `/api/activeService/list` | `domain_id` · (`instance_id`) | 허용 · 사용 | `apm_active_services`·`apm_resource_pool` · O-05·O-06 |
| `/api/transaction/time` | `domain_id`·`start_time`·`end_time` · (`instance_id`) · 1분 창 | 허용 · 사용 | `apm_slow_transactions`·`apm_app_health` · S-03·F-05 |
| `/api/transaction/guid` | `domain_id`·`guid`·`start_time`·`end_time` | 허용 · 사용(W5) | `apm_transaction_trace` · F-07 |
| `/api/transaction/txid` · `/profile.txt` · `/sql` | `domain_id`·`txid`·`time` · (sql `profile_no`·`include_param_key`) | 허용 · 사용(W5 — 채팅 `apm.profile`·조사) | `apm_transaction_profile` · F-06 |
| `/api/dbsearch/event` | `domain_id`·`start_time`·`end_time` · (`instance_id`·`level`) | 허용 · 사용 | `apm_events` · `apm_fleet(mode=events)`(W3) · 폴러 · F-01·F-02·F-10·F-11·F-12 |
| `/api/dbsearch/error` | 같음 · (`instance_id`·`error_type`) | 허용 · 사용(`error_type` W1) | `apm_events`(`record=error`) · F-03·F-04 |
| `/api/status/application` | `domain_id`·`start_time`·`end_time`(시 단위) · (`instance_id`·`max_row`·`sort_by_metrics`·`application_name`) | 허용 · 사용(선택 키 전부 W2) | `apm_app_health`·`apm_slow_transactions`의 `hourly` · `apm_status_stats` · S-04·S-10 |
| `/api/status/sql` · `/api/status/external_call` | 같음(시 단위) · (`instance_id`·`sort_by_metrics`·`max_row`) | 허용 · 사용(W2 · 선택 키 전부) | `apm_status_stats` · O-07·O-08 |
| `/api-v2/deploy/{domainId}` | `startTime`·`endTime`(25시간 이하 · 정본 미수록) | 허용 · 사용(W2 · 25시간 조각) | `apm_source_changes`·`apm_change_impact`(W6) · O-09·F-09 |
| `/api-v2/manage/rule/event/{error/{d} · metric/{d}/{대상} · compare/{d}/{대상}}` | 경로 변수 | 허용 · 사용(W7) | `apm_config(kind=event_rules)` · O-11 |
| `/api-v2/manage/rule/active-service-color-range-boundary` | — | 허용 · 사용(W7) | `apm_config(kind=color_boundary)` · O-12 |
| `/api-v2/manage/rule/event/error/{d}/{errorType}/applied` · `…/{errorType}/individual-setting/{instanceId}` | 경로 변수(`errorType` 대문자 · 인스턴스 개별 설정은 없으면 404) | 허용 · 사용(W7 · 개별 설정 404 = 설정 없음) | `apm_config(kind=event_rules, error_type)` · O-11 |
| `/api-v2/manual-rdb-export` | — | 허용 · 사용(W7) | `apm_config(kind=rdb_export)` · O-16 |
| `/api-v2/environment-variable/{d}` | 경로 `domainId` | 허용 · 사용(W7 · 비밀 값 가림) | `apm_environment` · O-13 |
| `/api-v2/loaded-class/{d}/{i}` | (`search`) | 허용 · 사용(W7) | `apm_config(kind=loaded_classes)` · O-14 |
| `/api-v2/manage/instance` | `processId` · (`hostname`) | 허용 · 사용(W7) | `apm_config(kind=process_instance)` · O-15 |
| `/api-v2/manage/data-server/{domains · resource · system-property-config}` · `/api-v2/manage/db/path/{d}` | — · 경로 `domainId` | 허용 · 사용(W7) | `apm_config(kind=data_server·db_path)` · O-16 |
| `/api/auth/userlist` · `/restapi/users` · `/restapi/user/{id}` | — · 경로 `id` | 허용 · 사용(W7 · `password` 키째 제거) | `apm_users` · O-17 |
| `/api-v2/active-service/detail/{d}/{txid}` | `sessionId`·`threadHash` | 허용 · 사용(W7) | `apm_active_detail` · F-15 |
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
- 대상 해석(인스턴스 이름·업무명): `plans/130-WIP-apm-instance-and-business-name-targeting.md` · 소스 선별: `plans/132-WIP-source-first-selection.md`
- 이 표의 빈칸을 채우는 계획: **`plans/134-WIP-jennifer-question-coverage.md`**
- 제니퍼 원천: Open API 스펙 5.6.4 `https://raw.githubusercontent.com/jennifersoft/jennifer5-open-api/gh-pages/index.html`(`plans/87` [J-4]·[J-23]) · v2 매뉴얼 `https://github.com/jennifersoft/jennifer5-open-api-v2-manual`의 `spec/*.md`(`plans/87` [J-5])
- 결정: **D-296**(조회 범위) · D-003 · D-195 · D-274 · D-281 · D-283 · D-287 · D-290 · D-293 · D-295

## 10. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-10-02 | 최초 작성(사용자 지시 *"조사한 사용가능한 질문들은 docs폴더의 사용 사례를 별도로 정리하고 사용가능한 질문을 모두 사용할 수 있도록 구현할 계획을 plans폴더에 계획파일을 정리하라."*). 질문 35건(S 11 · O 10 · F 14) + 답할 수 없는 질문 6건(X) · API 지도 · 실측으로 바로잡은 것: 채팅이 봉투 집계(`summary`·`hourly`·`errors_by_type`)와 `was_signals`를 버린다 · `apm.runtime`·`apm.slow_tx` 보기에 기간 설정이 없어 추세·기간 지정이 채팅에서 안 된다 · `apm.app_health` 10분 상한 때문에 시 단위 통계에 도달하지 못한다 · 파서 `limit`·이벤트 `level`이 채팅에서 전달되지 않는다 |
| 2026-10-02 | **D-296 반영**(사용자 *"모든 api는 허용하고 조회할 수 있는 범위는 모두 가능하도록 정한다."* · 「민감 조회 API까지」 · 「보호 한도도 올림」) — 읽기 API 전부 허용(쓰기·제어 차단 · 비밀번호·비밀 값 제거) · 자체 상한 제거. 질문 43건으로 확대: O-11(이벤트 룰 조회) · O-12(액티브 색상 경계) · O-13(환경변수) · O-14(로드된 클래스) · O-15(프로세스 → 인스턴스) · O-16(데이터 서버) · O-17(사용자 목록) · F-15(실행 중 요청 상세) 신설 · X-04·X-06 흡수 · X-03은 변경만 남김 · S-09 ⛔ → ➕ · 표지 ⛔ 삭제 · Wave 번호를 `plans/134` v1.1에 맞춤(W7 = 설정·관리·민감 · W8 = 보기 선택) · API 지도에 v2 매뉴얼 읽기 경로 추가 |
| 2026-10-02 | **D-299 · plans/134 v1.2** — 43사례는 최소 회귀 집합, 전체 읽기 기능·복수 조회·분석으로 확장. metrics 직접 보기, GUID/변경 전후 분석 Wave 연결, 진입점별 검증·121 필수 연동. 코드 0이므로 현재 상태 표기는 유지 |
| 2026-10-02 | **`plans/134` W0-B·W1·W2 구현 반영(D-300)** — 상태 갱신: ✅ S-01·S-03·S-04·S-10·O-02·O-04·O-06·O-07·O-08·O-09·O-10·F-02·F-04·F-05·F-08 · ◐(기능은 됨 · 로컬 9B 선택·조건 오답) S-05·O-03·F-01·F-03 · ◐ F-09(전후 분석 W6). 근거 = 합성 픽스처 실프로세스 종단 + 로컬 MLX 9B 29문항 실측(실 제니퍼 미검증 — W10). §0·§6·§7 갱신(도구 11+3 · 보기 12 · 작업 접수·결과 파일 · 기간 상한 제거 · 하루 넘은 기간은 W6). 새로 찾은 GET 3경로(이벤트 룰 `applied`·`individual-setting` · 수동 RDB Export 상태)는 `spec/CAPABILITY-MAP-134.md` — W7 |
| 2026-10-06 | **`plans/134` W5·W6(독립분)·W7 구현 반영(D-302)** — 상태 갱신: ✅ F-06·F-07·F-09·F-15·O-12·O-13·O-14·O-16·O-17 · ◐ O-11(조건 누락 — 결과가 넓어지는 쪽) · ◐ O-15(PID 없는 질문 결함 수정 뒤 MLX 미재측정). §0 갱신 행 · API 지도(GUID·거래 상세·deploy·v2 관리·민감 경로 → 허용·사용). 근거 = 합성 픽스처 실프로세스 종단 + 로컬 MLX 9B 47문항(실 제니퍼 미검증 — W10 · 운영 사다리 1단 — D-251) |
| 2026-10-06 | **`plans/130` W1~W4 반영(W5 문서)** — §0 허용목록 37템플릿(`/api/business`) · S-09 비고(업무명 → 인스턴스 해석 구현 · 업무 단위 지표는 W4) · §6 묻는 요령 1에 인스턴스 이름·업무명 질문 예(가상 이름) |
| 2026-10-06 | **`plans/134` W3·W4 구현 반영(D-310)** — 상태 갱신: ✅ S-06·S-08·S-09·F-10 · ◐ S-07(로컬 9B 선택 오답 1건 · 하루 넘는 기간 W6). §0 허용목록 41템플릿 · 데이터 도구 21종 · 보기 28종 · 대상 여럿 한 번에 · §6 묻는 요령 1 · §7 API 지도 4경로 「허용 확정」 → 「허용 · 사용」(+ `apm_fleet`·`apm_business` 사용처). 근거 = 목 Open API 2소스 합성 픽스처 실프로세스 종단(실 제니퍼 미검증 — W10) + 로컬 MLX 9B 14문항 중 13 정답(운영 모델 FabriX 재측정 필요) · 350도메인 실제 소요·버퍼 20만 건 메모리 미측정 · 채팅 효력은 2단 전환 뒤(D-251). 운영자 문서·사례표라 화면·버튼 추가가 아니다(사용자 매뉴얼 U-54는 별도 — D-255) |
