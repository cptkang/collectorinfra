# 87. 제니퍼(JENNIFER) APM 연동 — 미들웨어(WAS) 장애 진단·대응·복구 범위 확대

> **작성일**: 2026-09-03
> **성격**: 구현 계획(조사·설계 완료, 구현 전) · **상태: 계획(미구현) — 사용자 확정 게이트 G-1~G-7 대기(§10)**
> **요청 취지(사용자 지시 원문)**: *"제니퍼라는 APM 솔루션을 연동하여 미들웨어(WAS) 모니터링을 연동하여
> 장애 진단과 대응을 지원하려고 한다. 연동방식은 폴스타와 동일하게 mcp DB를 연동하려고 한다. 필요한 경우
> 제니퍼와 api연동도 검토한다. 검색을 통해 제니퍼의 기능을 검토하여 sre agent에서 처리하는 장애 진단, 대응,
> 복구 처리를 위한 범위 확대 계획을 별도의 파일로 정리하라. 필요한 경우 관련 문헌, 논문 검토를 통해 장애
> 대응에 적절한 구현 방향을 계획에서 반영하라."*
> **⚠ 전제 정정(§0.2)**: JENNIFER 5는 RDBMS 리포지토리가 아니라 **자체 파일 DB**를 쓴다. "폴스타와 동일한
> MCP DB 연동"은 **뷰 서버의 RDB Export(PostgreSQL 적재)를 읽는 형태**로만 성립하고, 진단에 필요한
> 실시간·상세 데이터(액티브 서비스·X-View 프로파일·이벤트)는 **Open API**로만 얻는다. 따라서 본 계획은
> **SQL(적재본) + REST(Open API) + 이벤트 어댑터** 3중 연동을 권고한다.
> **상위/선행 계획**: **Plan 55**(멀티소스 관측 로드맵 — 본 계획은 그 M0·M1·M3(미들웨어)·M4(교차 상관 일부)의
> 실행 계획) · **Plan 78 W7-2단계**(APM 연계 · **D-168 예약** — 본 계획이 그 2단계의 구체화) · Plan 64 §8(조치
> 권고 거버넌스) · Plan 66(SRE Agent 통합 시퀀스) · `plans/sre-agent/02·04·05·06` · Plan 81(가용성 사전 판정) ·
> Plan 82(솔루션 축 실행 그룹 — `db_registry.yaml`의 `apm` 자리 예약)
> **관련 결정**: D-003(읽기 전용) · D-035(결정적=판단·LLM=서술) · D-118(`sre_agent` 경계) · **D-119**(`mcp_server`
> = 관측 데이터 읽기 경계 — 소스 추가는 이 경계의 확장) · D-122(고수준 도구 8종·값 인자 SQL 조립) · D-123/D-124
> (조사 계약·트리거 배선) · D-125(정적 Bearer) · D-127(과금 API 건별 승인) · D-139(패키지 경계) · D-161(폐기 실측
> 의무) · D-174(LLM 평면) · D-175(가용성 사전 판정) · D-176(솔루션 축) · **D-189**(L3 옵션 B — 허용목록
> read-only 명령 · 조치는 권고만)
> **신규 결정 예약**: **D-195**(제니퍼 APM 연동 — §11). `docs/02_decision.md` 「채번 이력」 표에 등재(2026-09-03).
> ※ 채번 실측 2026-09-03 — `## D-` 헤더 최댓값 **193** · 「변경 이력」 표 최댓값 **193** · 「채번 이력」 표
> (D-105·115·134·158·163~168·176 예약) 대조 → 다음 번호는 194이나, 작성 시점에 **D-194를 `plans/50` v2.1·
> `CAPABILITY-MAP-50.md`·`SPEC-briefing-contract.md`가 "예정"으로 쓰고 있었다**(표 미등재). 충돌을 피해 본 계획은
> **D-195**를 잡았고, **같은 날 D-194가 `plans/50` v2.2로 본문 등재**되어(`## D-194` 확인) 번호가 연속으로 맞았다. ※ 그 D-194는 2026-09-07 원격 병합 시 **D-197**로 재부여됐다(D-194는 FabriX 프로파일이 선점) — 이하 본문의 D-194는 당시 번호.
> **실측 기준**: 아래 모든 `file:line`·값은 2026-09-03 현 브랜치(`multiintent`)에서 직접 확인했다.
> **제니퍼 자료 범례**: ✔ 확인(출처 첨부) · △ 추정(근거 있음, 실측 필요) · ✖ 미확인(공개 자료 없음).
> 제니퍼 정보는 **JENNIFER 5.6.x 공개 자료 + 4.5 매뉴얼(구조·이벤트 유형의 보조 근거)** 기준이다.
> JENNIFER 6은 2026-09 기준 공개 근거가 없어 다루지 않는다.

---

## 0. 요약 — 이 계획이 실제로 푸는 문제

### 0.1 세 조각으로 갈라지는 요청

요청은 "제니퍼를 연동해 WAS 장애 진단·대응·복구를 지원한다" 한 줄이지만, 실측하면 **난이도와 선행 결정이
서로 다른 세 조각**이다.

| 조각 | 난이도 | 선행 결정 | 근거 |
|---|---|---|---|
| **① 데이터 소스 편입** — 제니퍼 데이터를 `mcp_server`의 세 번째 소스로 | 중 | 없음(D-119 ①이 이미 허용) | Prometheus 편입(D-119 R-B)이 청사진. 폴스타 도구 8종의 반환 계약·서버측 SQL 조립·감사·Bearer가 전부 재사용된다(`mcp_server/mcp_server/polestar_tools.py:636-641`, `config.py:70-89`) |
| **② 진단 범위 확대** — `sre_agent`가 WAS 관점 증거(트랜잭션·힙/GC·풀·큐잉)를 읽고 판정·권고 | 중 | 없음 | `sre_agent`는 `mcp_server` 한 엔드포인트의 도구를 **자동 발견**한다(`interface/mcp_service.py:97-113`, RemoteMCPToolset) → 도구가 늘어도 sre_agent 배선 변경 0. 확장 지점은 지침·시그니처·권고 표·브리핑뿐 |
| **③ 대응·복구 범위 확대** — 권고를 넘어 조치를 실행 | **높음** | **D-003 예외 신설 + Plan 78 §8.3 5조건** | 현행 불변식: *"조치는 권고만(자동 실행 경로 없음 · `HUMAN_GATED_NOTE` 강제)"*(D-189 불변식 · `briefing_builder.py:36`). 자율성 3단계 진입은 5조건 **동시 충족**이 요구된다(Plan 78 §8.3). 본 계획은 그 조건을 **충족시키는 설계**를 제시하되, 착수는 **사용자 확정(G-6)** 뒤로 둔다 |

→ ①·②는 기존 결정 안에서 착수 가능하고, ③은 **새 결정이 필요한 유일한 조각**이다. 세 조각을 한 계획에
두는 이유는 ③의 안전 설계(검증 루프·롤백)가 ①·②의 데이터(조치 후 `apm_app_health` 재조회)에 의존하기
때문이다 — 순서를 갈라도 설계는 함께 봐야 한다.

### 0.2 전제 정정 — "폴스타와 동일한 MCP DB 연동"은 그대로 성립하지 않는다

| 사용자 전제 | 실측 | 판정 |
|---|---|---|
| 제니퍼도 폴스타처럼 리포지토리 RDBMS가 있어 SQL로 직접 읽는다 | ✔ JENNIFER 5는 *"초 단위 데이터를 대용량 고속 처리용 **자체 파일 시스템**에 저장, 직접 접근이 제한적"*[J-1]. 4.x까지는 Derby/Oracle/DB2 리포지토리였으나[J-3] 5에서 파일 DB로 바뀌었다 | **직접 SQL 불가** |
| 그래도 DB로 읽을 길은 있는가 | ✔ 뷰 서버 내장 **RDB Export**가 Oracle 12 / MySQL 5.7 / **PostgreSQL 9.x**로 통계를 적재한다 — `DOMAIN_METRIC_{5MIN,1HOUR,1DAY}` · `INSTANCE_METRIC_{5MIN,1HOUR,1DAY}` · `APPLICATION_METRIC_1DAY` · `TRANSACTION_{domain}_YYYYMMDD`(1분)[J-2] | **적재본을 읽는 SQL 경로 성립** — 폴스타 경로(`SourceConfig`·asyncpg·`execute_sql` 게이트·`db_profiles`)를 그대로 재사용 |
| 그 DB만으로 진단이 되는가 | ✖ 적재본에는 **액티브 서비스(현재 실행 중 트랜잭션·스택)·X-View 프로파일·이벤트·실시간 지표가 없다**. 이들은 Bearer 토큰 기반 **Open API v1/v2**[J-4][J-5]가 유일한 경로다 | **진단에는 API가 필수** — "필요한 경우 검토"가 아니라 조사 도구의 절반이 API 위에 선다 |
| 이벤트(알람)는 어떻게 받는가 | ✔ EVENT 룰의 "외부연동" + **EventHandler 어댑터**(Java, `com.aries.extension`)[J-6] 또는 Open API `/api/dbsearch/event` 폴링[J-4] | 폴스타 TCP JSON → `alarm:raw`와 **같은 스트림**으로 편입 가능 |

**되돌리는 비용**(사용자가 "DB만"을 고집할 경우): 얻는 것은 5분 이상 집계된 인스턴스·도메인 지표와 일 통계
뿐이다. 잃는 것은 액티브 스택·프로파일·이벤트 — 곧 **미들웨어 장애 진단의 핵심 증거**다. §2.4 비교표가
근거이며, 이 판정은 **G-1 게이트**로 사용자에게 되돌린다.

### 0.3 한 줄 권고

*RDB Export 적재본(SQL)으로 추세·통계를, Open API(REST)로 실시간·프로파일을, 이벤트 어댑터로 알람을 받아
**모두 `mcp_server` 하나의 `apm_*` 도구 표면 뒤에 숨긴다**(벤더 무지·D-119). `sre_agent`는 도구를 자동
발견하므로 **지침·시그니처·권고·브리핑만 WAS 관점으로 확장**한다. 대응·복구는 **L1(권고) → L2(승인 후
실행)** 로 한 단만 올리되, 실행기는 LLM 평면 밖의 **결정적 런북 실행기**로 두고 D-003 예외·승인 UX·blast
radius·검증-롤백·적응형 공격 평가를 **G-6에서 사용자 확정 후** 착수한다.*

---

## 1. 요구 해석

### 1.1 요구 → 기능 매핑

| 요구 | 현행(폴스타 단일 소스) | 확대 후 | 절 |
|---|---|---|---|
| **진단** — WAS 장애의 원인 지목 | OS 근사(`ps`·`ss`·`top`·`journalctl`, Plan 78 W7-1 · `middleware_profile()` = `vm_profile()` 동일) | **APM 1차**(트랜잭션·힙/GC·스레드·풀·큐잉·슬로우 SQL·외부 호출) · OS 근사 2차 폴백 | §5.2·§5.4 |
| **대응** — 영향 완화(mitigation) 제안·실행 | 권고만(`remediation.recommend`, OS 시그니처 11종) | WAS 시그니처 8종 추가 + **제니퍼가 제공하는 완화 수단**(PLC 부하 제어·스레드 인터럽트·EVENT 룰) 카탈로그화 · **L2 승인 후 실행**(게이트) | §5.4·§5.7 |
| **복구** — 정상 상태 복원·검증 | 없음 | 조치 후 **검증 루프**(`apm_app_health` 재조회로 회복 판정) · 실패 시 롤백/에스컬레이션 · 사후 브리핑 | §5.7 |
| **연동 방식 = MCP DB** | 폴스타 DB(PG·DB2) | RDB Export 적재 PG를 `mcp_server` `[[sources]]`로 등록 | §5.2 |
| **API 연동 검토** | Prometheus HTTP(`promql_tools.py`) · 폴스타 프로세스 API(`polestar_tools.py:39` httpx) | 제니퍼 Open API 클라이언트(httpx 재사용) — 서버측 자격증명·타임아웃·감사 | §5.2 |
| **이벤트(알람)** | 폴스타 TCP JSON → `alarm:raw` → 노이즈 게이트 → 조사 트리거 | 제니퍼 이벤트를 **같은 스트림**으로(폴링 → 어댑터) + `app_impact` 축 | §5.5 |
| **질의(pull)** | text2sql 폴스타 3 DB | `apm` 솔루션 축 + `jennifer_export` DB 등록 → "OO WAS 응답시간 추이" 같은 질의 | §5.6 |

### 1.2 범위

**안**: `mcp_server` 소스·도구 확장, 엔티티 정합 파일, `sre_agent` 지침·시그니처·권고·브리핑 확장, 이벤트
편입(폴링·어댑터), 노이즈 게이트 `app_impact` 축, text2sql 등록, **L2 대응·복구의 설계와 게이트 정의**.

**밖**: 제니퍼 제품 도입·라이선스·에이전트 설치(전제 조건 — §1.3), DPM(DB 성능) 연동(Plan 55 M3의 DPM
축 — 별건), L3 완전 자율 조치(Plan 78 §8.3 — 본 계획도 2단계에서 멈추고 L2까지만 연다), 제니퍼 자체 AI
(Insight Chat)와의 통합(폐쇄망에서 외부 LLM 의존[J-8] — 미채택 근거 §2.7).

### 1.3 전제·가정 (명시)

1. **제니퍼 버전은 5.6.x**이며 온프레미스 설치형이다. 5.5.3+ 조건인 Open API v2와 5.6.0.5+ 조건인 API 서버가
   가용하다(△ — J0에서 실측).
2. **RDB Export를 PostgreSQL로 활성화할 수 있다**(뷰 서버 `server_view.conf` 편집·재기동 권한 — 제니퍼 운영
   조직과 협의). 문서 기준이 PG 9.x라 **최신 PG 호환은 실측**한다(△).
3. **읽기 전용 자격증명**을 두 종 받는다 — 적재 PG의 SELECT 전용 계정, Open API의 조회 전용 토큰(제니퍼 토큰에
   권한 등급이 있는지 ✖ — 없다면 관리 API(v2 EVENT 룰 변경 등)까지 열리므로 §8.2 통제로 막는다).
4. **네트워크 도달성**: `mcp_server` 호스트 → 적재 PG, 뷰 서버 Open API 포트. 폐쇄망 내부이므로 egress 없음.
5. WAS는 **Java 계열(Tomcat·JEUS·WebLogic 등)** 이 1차 대상이다. `config/middleware_signatures.yaml`의 기본 세트와
   맞춘다. .NET/PHP/Node 인스턴스는 제니퍼가 지원하나[J-7] 시그니처·권고 표는 Java 우선이다.
6. 폴스타 `hostname`과 제니퍼 인스턴스명이 **일치하지 않을 수 있다** — 정합은 선언적 매핑 파일로 푼다(§5.3).

### 1.4 용어

| 제니퍼 용어 | 뜻 | 폴스타 대응 |
|---|---|---|
| 도메인(domain) | 에이전트 묶음(서비스·업무 단위) | (없음 — 존/실행 그룹과 유사한 관리 축) |
| 인스턴스(instance)/에이전트 | 모니터링 대상 WAS 프로세스 1개(`instanceId`·`instanceName`) | `cmm_resource`의 서버(호스트) — **1:N**(한 호스트에 인스턴스 여럿) |
| X-View | 트랜잭션 응답시간 산점도 + 개별 트랜잭션 프로파일(메서드·SQL·외부 호출 타임라인)[J-9] | (없음) |
| 액티브 서비스 | 지금 실행 중인 트랜잭션(스레드) 목록·상태·경과 시간·스택[J-4] | (없음) |
| PLC(Peak Load Control) | 동시 액티브 서비스 상한 초과 요청을 거절/리다이렉트하는 부하 제어[J-3][J-10] | (없음) |
| 이벤트(EVENT) | 임계·예외 기반 알람. 레벨 normal/warning/fatal[J-6] | `cmm_alarm` 심각도 1/2/3 |

---

## 2. 제니퍼 조사 결과 (2026-09-03 · 출처는 §12.3)

### 2.1 제품 구조 ✔

Agent → **Data Server**(에이전트 관리·데이터 처리) → **View Server**(화면·Open API·어댑터·RDB Export) +
Repository(자체 파일 DB, `db_data`/`db_view`) + HTML5 콘솔[J-11][J-1]. 지원 언어 Java · .NET · PHP · Python ·
Node.js(Go 없음)[J-7]. K8s 모니터링(5.6.1.1+)·MSA 토폴로지(2025-12)·OpenTelemetry 트레이스 수용(Collector 경유)
[J-12][J-13]이 5.x 마이너로 추가됐다. **OTel 메트릭/로그 수용·Prometheus exporter는 ✖**.

### 2.2 진단 관점 데이터 카탈로그 — 무엇을 어느 경로로 얻는가

| 데이터 | 경로 | 지연 | 진단 용도 | 확인 |
|---|---|---|---|---|
| 인스턴스 실시간 지표: `activeService`·`tps`·`responseTime`·`concurrentUser`·`rejectRate`·`heapUsed/heapCommitted`·`procCPU/procMemory`·`activeServiceRangeCount0~3` | **API** `/api/realtime/instance` | 초 | 골든 시그널·큐잉·거절 | ✔[J-4] |
| 메트릭 시계열(도메인/인스턴스/비즈니스, `interval_minute`) | **API** `/api/dbmetrics/{domain\|instance\|business}` | 분 | 사건창 추세 | ✔[J-4] |
| 메트릭 카탈로그(domain·instance·application·business·sql·externalCall) | **API** `/api/metrics` | — | 도구 매핑 실측 | ✔[J-4] |
| 인스턴스·도메인 5분/1시간/1일 통계, 애플리케이션 일 통계, 트랜잭션(1분) | **SQL** RDB Export 적재본 | 5분+ | 추세·기준선·질의 응답 | ✔ 테이블명[J-2] / ✖ 컬럼 DDL |
| 액티브 서비스 목록(`status`·`elapseTime`·`runningMode`·`runningFullText`·`cpuTime`·`clientIp`·`threadHash`·`runningDataSourceName`·`sqls`·`fetches`) | **API** `/api/activeService/list` | 초 | **큐잉·락·슬로우 SQL·외부 호출 대기 지목**(핵심) | ✔[J-4] |
| X-View 트랜잭션 검색(`txid`·`guid`·시간창 **1분 제한**)·프로파일 텍스트·SQL/파라미터 | **API** `/api/transaction/*` | 초 | 개별 트랜잭션 원인 분해(method/SQL/external/fetch/network) | ✔[J-4] |
| 이벤트·에러 검색(`level`·`instance_id[]`·`error_type[]`) | **API** `/api/dbsearch/event`·`/error` | 초 | 사건창 선행 이벤트 | ✔[J-4] |
| 애플리케이션·SQL·외부 호출 통계(`max_row` 기본 1000, `sort_by_metrics`) | **API** `/api/status/{application\|sql\|external_call}` | 초 | 상위 N 느린 트랜잭션·SQL | ✔[J-4] |
| 스레드 덤프·서비스 덤프·강제 GC·스레드 인터럽트/일시정지 | 콘솔 기능(4.x 매뉴얼 6.8·9.11·13장). **Open API 노출 ✖** | — | 대응(§2.5) | △ 5.x 콘솔 유지 여부 |
| 토폴로지(도메인→인스턴스→애플리케이션→트랜잭션 Call Chain, 프로토콜별 엣지) | 콘솔(MSA 뷰). API 노출 ✖ | — | 의존 전파 | ✔ 기능[J-12] / ✖ API |
| 이상 탐지·상관(Anomaly Event · Metrics Correlation · Stacktrace Insight) | 콘솔(Jennifer Insight, 2025-12) | — | (참고 — 자체 AI) | ✔[J-8] |

### 2.3 이벤트 체계 ✔ (4.5 매뉴얼 11장 — 5.x 명칭은 J0에서 `/api-v2/manage-rule-event`로 대조)

- **레벨**: `normal` / `warning` / `fatal` — 공식 PagerDuty 어댑터가 FATAL·CRITICAL→critical, WARNING→warning,
  NORMAL·RECOVERY·CLEAR→resolve로 매핑한다[J-14].
- **유형(접두 체계)**: `ERROR_*` — UNCAUGHT_EXCEPTION · **SERVICE_QUEUING** · **PLC_REJECTED** · JDBC_CONNECTION_FAIL ·
  DB_CONNECTION_FAIL · OUTOFMEMORY · SYSTEM_DOWN · PROCESS_DOWN · **JVM_DOWN** · JVM_CPU_HIGH_LONGTIME ·
  HIGH_RATE_REJECT · HIGH_RATE_FAIL · **MAYBE_GC_TIME_DELAY** · MAYBE_BUSY_PROCESS 등 / `WARNING_*` — **TX_BAD_RESPONSE** ·
  APP_BAD_RESPONSE · DB_BAD_RESPONSE · JDBC_BAD_RESPONSE · TX_CALL_EXCEPTION · JDBC_STMT_EXCEPTION · JVM_CPU_HIGH ·
  **JVM_HEAP_MEM_HIGH** · **RESOURCE_LEAK** · DB_TOOMANY_FETCH · **DB_CONN_UNCLOSED** · JDBC_*_UNCLOSED 등 /
  `USER_DEFINED_{FATAL,ERROR,WARNING,MESSAGE}` · `SYSTEM_MESSAGE`[J-3]. △ 5.x Application Insights 화면에
  `SERVICE_EXCEPTION`·`BAD_RESPONSE_TIME` 표기가 보여 일부 개명 가능성[J-8].
- **필드(EventData)**: `domainId`·`domainName`·`instanceId`·`instanceName`·`time`·`errorType`·`metricsName`·
  `eventLevel`·`message`·`value`·`otype`·`detailMessage`·`serviceName`·`txid`(Open API 응답은 `applicationName` 추가)[J-6].
- **외부 전송**: EVENT 룰 "외부연동" 토글 + 어댑터(Slack·Teams·PagerDuty·Rocket.Chat·LINE·JIRA·**SNMP trap**·
  eventlog 파일 — 공식 리포 30개)[J-15]. 범용 HTTP webhook 내장은 ✖(Slack/Teams 어댑터가 webhook 기반이므로
  같은 골격으로 커스텀 어댑터 작성 가능 △).

### 2.4 연동 지점 비교와 판정

| 후보 | 데이터 | 장점 | 단점·리스크 | 확인 | **판정** |
|---|---|---|---|---|---|
| **A. RDB Export → PostgreSQL을 `mcp_server` SQL 소스로** | 5분·1시간·1일 지표, 애플리케이션 일 통계, 트랜잭션(1분) | 폴스타와 **동일 경로**(`SourceConfig`·asyncpg·`db_profiles`·`execute_sql` 게이트) · 제니퍼 서버 부하 격리 · 장기 보관·조인 자유 | 실시간 아님(5분+) · 액티브 서비스·프로파일·이벤트 **없음** · 컬럼 DDL ✖ · PG 9.x 문서 기준 · 뷰 서버 설정·재기동 | ✔/✖ | **채택(J1)** — 추세·기준선·질의 응답 |
| **B. Open API(REST, Bearer)** | 실시간 지표·액티브 서비스·이벤트/에러 검색·X-View·프로파일·SQL·통계 | **가장 넓은 커버리지·실시간** · 제니퍼 자체 AI도 같은 API를 tool로 쓴다[J-8] | `/api/transaction/time` 1분 창 · `max_row` 1000 · 토큰 관리 · 뷰 서버 부하 · 정본 문서 사이트가 JS 렌더링(스펙은 `spec.json`으로 확보) | ✔ | **채택(J2)** — 진단의 핵심 |
| C. JDBC 드라이버 + API 서버(Calcite SQL, 5.6.0.5+) | 파일 DB 일자별 테이블 SQL | 적재 없이 SQL | **별도 프로세스·계정** · 일자별 테이블 단위 · 조인·성능 한계 · 문서 희소 | ✔/△ | **미채택** — A가 같은 자리를 더 단순하게 채운다. A가 불가할 때의 대체(§9 R-2) |
| D. 어댑터 push(TransactionHandler → 스트림) | 실시간 트랜잭션 스트림 | 지연 최소 | **Java 어댑터 개발·뷰 서버 배포** · 업그레이드 호환 · Kafka 공식 어댑터 ✖ | ✔/✖ | **보류** — 고카디널리티 원시 스트림은 필요 근거가 없다(Plan 55 C-5). 이벤트만 E로 |
| **E. 이벤트 어댑터(EventHandler) → `alarm:raw`** | 이벤트(레벨·유형·인스턴스·txid·value) | 폴스타 알람과 **동일 구조로 노이즈 게이트 편입** · 공식 어댑터 코드 재사용 | 이벤트만 · "외부연동" 토글 · Java 빌드 | ✔ | **채택(J4-2단계)** — 1단계는 B의 `/api/dbsearch/event` **폴링**(Java 코드 0) |

### 2.5 대응·복구 관점 — 제니퍼가 "할 수 있는 조작"과 "할 수 없는 것"

| 조작 | 제니퍼 제공 | 노출 경로 | 본 계획 위치 |
|---|---|---|---|
| **PLC 부하 제어**(동시 액티브 서비스 상한 · 거절 메시지/리다이렉트) | ✔ 에이전트 옵션 `set_limit_active_service`·`max_num_of_active_service`·`request_reject_type`[J-3][J-10] | 에이전트 설정(콘솔). API ✖ | L2 조치 후보 "유입 차단" — 실행 경로는 **콘솔 수동**(권고) 또는 설정 파일 반영(고위험) |
| 자동 서비스 덤프(`enable_dump_triggering`·`number_of_dump_trigger`) | ✔[J-3] | 에이전트 설정 | 진단 증거 채취 — **읽기성 조치**(L1에서 권고) |
| 스레드 인터럽트·우선순위 변경·일시정지·재시작(액티브 서비스 단위) | ✔ 4.x 콘솔 13장[J-3] | 콘솔. API ✖ · △ 5.x 유지 | L2 "특정 트랜잭션 중단" — **중위험**(단일 스레드 범위) |
| 강제 GC · VERBOSE:GC 토글 | ✔ 4.x 9.11.4[J-3] | 콘솔. API ✖ | L2 "힙 압박 완화" — 중위험(STW 유발) |
| EVENT 룰 on/off · 임계 변경 | ✔ Open API v2 `manage-rule-event*`[J-5] | **API ✔** | **관측 설정 변경**은 조치가 아니라 통제 대상(§8.2 — 조회 토큰으로 막을 것) |
| **인스턴스 재기동·배포 롤백·L4 트래픽 배제** | ✖ 제니퍼 기능 아님 | 외부 도구(WAS 관리 콘솔·스크립트·LB) | L2 고위험 조치 — 실행기는 제니퍼가 아니라 **별도 실행 경계**(§5.7) |

→ **제니퍼는 관측·차단(PLC)·스레드 제어까지**이고, 복구의 대표 조치(재기동·롤백)는 제니퍼 밖에 있다. 대응·
복구 설계(§5.7)는 그래서 "제니퍼 API로 조치"가 아니라 **"제니퍼 데이터로 판정·검증하고, 실행은 별도 경계"**
구조가 된다.

### 2.6 미확인 항목 → J0 실측 체크리스트

| # | 항목 | 실측 방법 | 좌우하는 설계 |
|---|---|---|---|
| U-1 | 5.6.x 이벤트 유형 정식 명칭·EVENT 룰 목록 | `/api-v2/manage-rule-event` · 콘솔 [관리 > EVENT 룰] | §5.5 심각도·시그니처 매핑 파일 |
| U-2 | RDB Export 테이블 **컬럼 DDL**·최신 PG 호환 | 적재 후 `information_schema.columns` · `pg_stat_user_tables` | §5.2 SQL 도구 조립 · `db_profiles/jennifer_export.yaml` |
| U-3 | 5.x 데이터 보존 기본값(파일 DB·적재본) | 뷰 서버 설정 · 적재본 최소 일자 | 조회 창 상한 · 인시던트 스코프 |
| U-4 | 인스턴스 명명 규칙 ↔ 폴스타 `hostname` 일치율 | `/api/instance` 전수 ↔ `cmm_resource` 대조 스크립트 | §5.3 매핑 파일 필요 여부(**최대 리스크 R-1**) |
| U-5 | Open API 토큰의 권한 등급(조회 전용 발급 가능?) | 콘솔 [관리 > 인증 토큰] · v2 쓰기 API 호출 시도(테스트 환경) | §8.2 관리 API 차단 방식 |
| U-6 | 액티브 서비스·프로파일 응답의 **PII 포함 여부**(URL 파라미터·SQL 바인드 값·clientIp) | 샘플 응답 수집 → `pii_probe.py` | §8.3 마스킹 규칙 |
| U-7 | 뷰 서버 Open API 호출 부하 상한(동시·초당) | 제니퍼 운영 조직 확인 | 도구 타임아웃·레이트 리밋 |
| U-8 | 스레드 덤프·PLC 조작의 5.x API 노출 여부 | v2 spec 전수 · 벤더 문의 | §5.7 조치 카탈로그의 실행 경로 |
| U-9 | 범용 webhook 내장 여부 · SNMP 어댑터 재사용 가능성 | 콘솔 어댑터 목록 | §5.5 2단계 전송 방식 |
| **U-10** | **폴스타 `was_object`·`was_connection`의 운영 3종 DB 실재 여부** — 샌드박스 DDL에 `agent_id`·`hostname`·`obj_name`과 `jennifer_url`·`jennifer_token`·`jennifer_domain_id`·`jennifer_version`이 있다(§3.3) | `information_schema.tables`·행 수·`agent_id` 채움률 대조 | **§5.3 정합 1순위 브릿지**(R-1 완화) · 폴스타가 이미 제니퍼와 연동돼 있다면 자격증명 보관 주체 재검토(§8.2) |

### 2.7 유사 벤더의 AI·MCP 동향 (참고)

Dynatrace(공식 원격 MCP 서버 GA 2026-01 · Davis CoPilot)[V-1] · Datadog(MCP 서버 GA · Bits AI SRE — 알림
자동 조사)[V-2] · New Relic(AI MCP 서버 public preview 2025-11)[V-3] · **WhaTap**(국내, 공식 MCP 서버 10개 도구 —
APM 이상탐지·토폴로지·PromQL)[V-4] · Scouter(오픈소스, Web API v1 · 공식 MCP ✖)[V-5]. **제니퍼소프트 공식 MCP
서버는 ✖**이며, Insight Chat(2025-10)이 Open API를 tool로 쓰는 자체 에이전트다[J-8]. 공통 패턴은 *"벤더
API를 MCP 도구로 감싸고, 조사는 read-only, 조치는 승인 뒤"* 로 본 계획의 구조와 같다. 제니퍼 자체 AI를
채택하지 않는 이유: ① 외부 LLM 의존(폐쇄망용 브라우저 LLM은 개발 중[J-8]) ② 조사 두뇌를 둘로 쪼개면
Plan 64 §0 중복 금지·D-118 경계 위반 ③ 제니퍼 데이터만 보므로 인프라↔앱 교차 상관(Plan 55 §6)이 불가.

---

## 3. 현행 실측 — 어디에 꽂히는가

> ⚠ **병렬 작업 주의**: 같은 날 `plans/50` v2.2(D-197 · 원 D-194) 작업이 `mcp_server/mcp_server/config.py` ·
> `sre_agent/sre_agent/interface/mcp_service.py` · `sre_agent/sre_agent/application/investigation_guidance.py`를
> **동시에 수정 중**임을 실측했다(디스크 변경 감지). 아래 `file:line`은 작성 시점 값이며, **각 Wave 착수 직전에
> 재실측**한다. 특히 `investigation_guidance.py`(§3.2 지침)는 D-197(원 D-194)이 만든 파일이라 §5.4-a의 확장 지점이
> 이동할 수 있다.

### 3.1 `mcp_server` — 관측 데이터 읽기 경계 (D-119)

- **소스 선언**: `config.toml` `[[sources]]`(name·type `postgresql|db2`·readonly·query_timeout·max_rows·풀 크기) +
  `.env`의 `{NAME}_CONNECTION`(`mcp_server/mcp_server/config.py:70-79`, `config.toml:46-82`). 적재 PG는 **항목 하나
  추가**로 끝난다.
- **도구 등록**: `server.py:123-135` — `register_tools`(SQL 일반·`list_sources`) · `register_polestar_tools`(옵션
  `expose_polestar_tools`) · `register_promql_tools(mcp, expose_raw_promql)`. 신규 `register_apm_tools(mcp, cfg)`가
  같은 자리에 선다.
- **반환 계약**: `{rows, row_count, queried_at, source_kind, source, engine}` / 오류 `{error}`
  (`polestar_tools.py:636-641`, D-122). `source_kind`는 `polestar_db`·`polestar_process_realtime`·`prometheus`가
  있다 — `apm_db`·`apm_api`를 추가한다.
- **HTTP 클라이언트**: `httpx`가 이미 쓰인다(`promql_tools.py:35`·`polestar_tools.py:39`). ⚠ `mcp_server/pyproject.toml`
  에는 미선언(루트 venv 공유로 동작 — 부채. 본 계획에서 선언 1줄 추가).
- **게이트**: `execute_sql` 기본 비노출(`expose_execute_sql=False`)·폴스타 도메인 deny·Bearer(D-125). 적재 PG에도
  같은 게이트가 자동 적용된다.

### 3.2 `sre_agent` — 조사 코어 (D-118·D-123)

- **도구 자동 발견**: `_build_mcp_servers()`가 `polestar_mcp_url` 하나를 `Config.mcp_servers`로 넘기고, 폴스타
  SQL·PromQL 도구가 **자동 발견**된다(`interface/mcp_service.py:97-113`). → `apm_*` 도구도 **배선 변경 0**으로
  보인다. 설정 키 이름이 `polestar_mcp_url`인 것은 의미상 어긋나지만(관측 경계 URL) **개명하지 않는다**(회귀 0).
- **프로파일**: 프로덕션은 `remote_vm_profile()` 고정(`mcp_service.py:130-134`). `middleware_profile()`은
  `vm_profile()`과 **allowlist가 동일**하고 차이는 조사 초점 노트뿐(`toolset_profiles.py:143-164`, W7-1).
- **지침**: `investigation_guidance.build_guidance()`가 `ANCHORED_TOOLS` 4종(`polestar_metric_trend`·`polestar_alarm_history`·
  `polestar_incident_alarms`·`prom_metric_range`)에 사건 구간 인자를 강제한다(`investigation_guidance.py:19-34`).
  → `apm_*` 시계열 도구를 여기에 추가해야 사건창이 강제된다.
- **결정적 판정**: `domain/severity_signatures.py`의 `SIGNATURES`(OS 시그니처 · `match_signatures(tool_outputs)`)와
  `domain/remediation.py`의 `_CANDIDATES_BY_SIGNATURE`(11 kind — `oom_kill`·`fs_readonly`·`service_restart_loop`·
  `soft_lockup`·`hung_task`·`segfault`·`conntrack_full`·`fd_exhaustion`·`inode_or_disk_full` 계열, 위험도
  low/medium/high, **고위험×저신뢰 강등** `_HIGH_RISK_MIN_CONFIDENCE`)이 확장 지점이다(`remediation.py:28-116`).
- **브리핑**: `briefing_builder.BRIEFING_ELEMENTS` 6요소 · 인용 검증(`_is_cited(line, tool_names)`) ·
  `HUMAN_GATED_NOTE`(`briefing_builder.py:20-36`). `tool_names`에 `apm_*`가 들어가야 APM 증거가 "인용됨"으로 판정된다.
- **설정**: `settings.py` — `remediation_recommender_enabled=False`·`severity_judge_enabled=False`(기본 off) ·
  `investigation_timeout_seconds=300`·`investigation_max_concurrent=2`.
- **조사 계약**: `investigation_jobs.REQUIRED_EVENT_FIELDS=("serverName","hostname","severity")` · `contract_version`
  검증(`investigation_jobs.py:45,112-119`). 제니퍼 이벤트도 이 계약으로 들어와야 하므로 **인스턴스 → hostname
  해소가 트리거 전에** 끝나야 한다(§5.3·§5.5).

### 3.3 본체 `src` — 질의·조사 진입

- `fault_diagnosis` 노드(`src/nodes/fault_diagnosis.py:111-156`): `_extract_targets(state)` → (server_name, hostname,
  db_id) → 가용성 사전 판정(D-175) → `SreAgentClient.diagnose(...)`. 그래프 배선은 `noise_gate.fault_diagnosis_enabled`
  off면 미배선(`src/graph.py:352-354`).
- `config/db_registry.yaml:41-67`: `solutions`에 **`apm` 자리(주석)** — `backend: rest` · `capabilities: [was_metric,
  jvm_heap, thread_pool, transaction]` · `requires: [host_location]`. `backend` 축은 `sql | rest | mcp`를 지원하고,
  *"apm·dpm은 실제 연동 시 주석을 풀어 등재 · 그때까지 등록 0건 회귀를 테스트가 단언"*(plans/82 Wave 7 비범위).
- **폴스타 스키마에 제니퍼 연동 흔적이 이미 있다** (샌드박스 DDL `testdata/pg/init/04_create_all_tables.sql:3802-3860`):
  `polestar.was_connection`에 **`jennifer_url`·`jennifer_token`·`jennifer_domain_id`·`jennifer_version`** 컬럼,
  `polestar.was_object`에 **`agent_id`·`hostname`(NOT NULL)·`obj_name`(NOT NULL)** 이 한 행에 있고,
  `was_object_datasource`·`was_instance_resource`(`resource_id`)·`was_instance_group`·`was_dashboard`가 딸려 있다.
  → 폴스타 제품이 제니퍼 연동을 내장하고 있을 가능성이 크며, **`was_object`가 인스턴스↔hostname 정합의 1순위
  브릿지 후보**다(Plan 55 C-1·Plan 78 §4.7.3-1이 "최대 난제"로 지목한 지점). 다만 이 파일은 **합성 더미 픽스처**라
  운영 DB 실재·채움률은 **U-10에서 실측**한다. `config/db_profiles`·`knowledge`에는 이 테이블들이 등재돼 있지 않다
  (grep 0건) — 실재하면 프로필 편입 대상이다.

### 3.4 `noise_gate` — 이벤트 수신·게이트·트리거

- 수신: `alarm_server`(TCP JSON 1행 → `xadd alarm:raw {"data": json}`, `base_receiver.py:44`, `tcp_receiver.py:78-90`).
  `BaseReceiver`가 추상 `start()`를 갖는다 → **새 수신기(폴링)** 를 같은 골격으로 둘 수 있다.
- 도메인: `AlarmEvent`는 폴스타 템플릿 변수와 **1:1**(`db_id`·`server_name`·`hostname`·`alarm_id`·`severity`·
  `resource_type`·`alarm_name`·`alarm_time`·`conditions`·`condition_log`, `domain/alarm.py:44-95`). `ServerIdentity.source_label`
  주석: *"소스 확장 시 family만 등록"*.
- 트리거: `application/nodes/investigation_trigger.py:95-170` — `resolve_targets` → `build_trigger_payload` →
  submit/poll. 대상 해소가 실패하면 사유를 남기고 생략(`:102-103`).

### 3.5 갭 — 무엇이 비어 있는가

| # | 갭 | 위치 |
|---|---|---|
| G1 | 제니퍼 소스·도구가 `mcp_server`에 없다(소스 0·도구 0) | §5.2 |
| G2 | 인스턴스 ↔ hostname 정합 규약·파일이 없다 | §5.3 |
| G3 | `sre_agent` 지침·시그니처·권고·브리핑이 OS 시그니처만 안다(WAS 시그니처 0) | §5.4 |
| G4 | 제니퍼 이벤트 수신 경로·심각도 매핑·`app_impact` 축이 없다 | §5.5 |
| G5 | text2sql에 `apm` 솔루션·`jennifer_export` DB가 미등록 | §5.6 |
| G6 | 조치 실행 경로가 **의도적으로** 없다(불변식). L2로 올리려면 결정·실행기·승인 UX·검증·롤백 전부 신설 | §5.7 · G-6 |
| G7 | `mcp_server/pyproject.toml`에 `httpx` 미선언 | §7 |

---

## 4. 문헌 조사 → 설계 원칙

> 서지 검증: arXiv API 메타데이터(제목·저자·게재일)로 실측했다(2026-09-03). 게재처(venue)는 확인된 것만
> 적고, 확인하지 못한 것은 arXiv로 둔다. Plan 78 §3.3·§11이 이미 검증한 보안 문헌(IPIGuard·Task Shield·Adaptive
> Attacks·Coding Agents Are Guessing)은 **재인용**만 하고 카드를 반복하지 않는다. 카드 형식: 기여 → **시사점**.

### 4.1 LLM 기반 장애 진단 에이전트

- **RCACopilot** — Chen et al., *Automatic Root Cause Analysis via Large Language Models for Cloud Incidents*
  (**EuroSys 2024** · arXiv 2305.15778 · DOI 10.1145/3627703.3629553 — v1의 "ICSE 2024" 표기는 오류, v1.1 정정).
  인시던트 유형별 **사전 정의된 증거 수집 핸들러**를 먼저 돌리고, LLM은 수집된
  증거로 원인 범주 예측·요약만 한다. → **P1** 증거 수집은 결정적 핸들러(우리의 `apm_*` 고수준 도구), LLM은
  해석 — D-035와 동형. WAS 이벤트 유형별 "먼저 볼 것" 순서를 지침으로 고정한다(§5.4-a).
- **RCAgent** — Wang et al., *Cloud Root Cause Analysis by Autonomous Agents with Tool-Augmented LLMs*
  (CIKM 2024 · arXiv 2310.16340). 도구 증강 에이전트 + **관측값 스냅샷 압축·자기 일관성 검증·전문가 에이전트
  분업**. → **P2** 원시 응답(액티브 서비스 1000행·프로파일 텍스트)은 서버측에서 상위 N·요약으로 줄여 LLM에
  넣는다(Context-Minimization, Plan 78 §3.3).
- Ahmed et al., *Recommending Root-Cause and Mitigation Steps for Cloud Incidents using LLMs* (ICSE 2023 ·
  arXiv 2301.03797). 4만 건 인시던트로 원인·완화 단계 생성을 평가 — **완화 단계는 원인보다 훨씬 어렵고
  환각이 잦다**. → **P3** 완화·복구 권고는 LLM 자유 생성이 아니라 **시그니처 → 조치 표**(결정적)에서 고른다
  (현행 `remediation.py` 구조 유지·WAS 항목 추가).
- Roy et al., *Exploring LLM-based Agents for Root Cause Analysis* (FSE 2024 Companion · arXiv 2403.04123).
  ReAct 에이전트는 도구를 주면 정확도가 오르나 **도구 오남용·과잉 호출**이 는다. → **P4** 도구 수는 적게
  (8종 상한), 이름은 질문형(`apm_slow_transactions`), 인자는 값만(D-122).
- **Flow-of-Action** — Pei et al. (WWW 2025 Companion · arXiv 2502.08224). **SOP(표준 운영 절차)** 를 지식으로
  주입한 멀티에이전트 RCA — SOP 없는 에이전트보다 환각·헤맴이 준다. → **P5** WAS 장애 SOP(큐잉·풀 고갈·GC
  stall·슬로우 SQL)를 **선언적 파일**(`config/apm_playbooks.yaml`)로 두고 지침에 주입한다.
- **Xpert** — Jiang et al. (ICSE 2024 · arXiv 2312.11988) · **Nissist** — An et al. (arXiv 2402.17531) ·
  **StepFly** — Mao et al. (arXiv 2510.10074) · **FixItFlow** — Unnikrishnan et al. (arXiv 2607.13035).
  TSG(트러블슈팅 가이드)를 기계가 따라가게 구조화하거나 인시던트에서 자동 생성한다. → **P5 보강**: 플레이북은
  DAG(단계·조건·다음 단계)로 쓰고, 조사 완료 후 **브리핑에서 플레이북 후보를 역생성**하는 것은 후속(범위 밖).
- **Stalled, Biased, and Confused** — Riddell et al. (FORGE 2026 · ICSE 워크숍 · arXiv 2601.22208 · DOI
  10.1145/3793655.3793732). LLM RCA의 **추론 실패 16종** 분류 — 앵커링 편향·반복/정체·임의 증거 선택·믿음 갱신
  실패가 정확도를 **15%p 이상** 깎는 핵심 부정 예측 인자. → **P6** 브리핑은 인용된 증거만 결론으로 승격하고(현행
  `_is_cited`), 가설은 `[가설]` 접두로 분리한다. → **P15 반증·정체 가드**: 지침에 "주 가설에 대한 반증 도구 호출
  1회"를 요구하고, 코드가 **동일 도구·동일 인자 반복 호출(3회)** 을 감지해 `max_steps=40` 소진 전에 "미결"로
  종료한다(§5.4-a).
- **Agentic RCA through Evidence-Grounded Reasoning** — Wei et al. (arXiv 2607.22385) · **OpsAgent** — Luo et al.
  (arXiv 2510.24145). 증거 그라운딩·다단 에이전트. → P6 보강.
- **mABC** — Zhang et al. (arXiv 2404.12135) · **Blueprint First, Model Second** — Qiu et al. (arXiv 2508.02721).
  멀티에이전트 합의 · **결정적 워크플로 골격 위에 LLM**. Blueprint First의 배포 사례 V-A **"Java Heap-Exhaustion
  Diagnosis"**(수만 대 JVM · 주 약 40건 OOM 클러스터를 `jstat` → 힙 덤프 → 로그 분석 런북으로 자동화)가 WAS 도메인의
  직접 전례다. → **P7** 조사 골격(수집 → 판정 → 권고 → 검증)은 코드가 정하고 LLM은 칸을 채운다 —
  `investigation_dispatcher`·`briefing_builder`가 이미 이 구조다.
  ※ v1이 함께 인용한 *Multi-Agent LLM Orchestration … Incident Response*(arXiv 2511.15755)는 **저자 철회본**
  (코드 감사에서 결과 조작 확인)으로 실측되어 **v1.1에서 제외**했다.

### 4.2 멀티모달·트레이스 기반 마이크로서비스/미들웨어 RCA

- **Eadro** — Lee et al. (ICSE 2023 · arXiv 2302.05092): 로그·KPI·트레이스를 **하나의 그래프로 학습**해
  이상 탐지와 원인 지목을 동시에. **DiagFusion** — Zhang et al. (IEEE TSC 2023 · arXiv 2302.10512): 이벤트
  임베딩 + 배포 그래프. → **P8** 인프라(폴스타)·앱(제니퍼) 신호를 **같은 타임라인·같은 엔티티 키**로 병합해야
  상관이 선다(Plan 55 C-1·C-2). 학습 모델은 미채택(폐쇄망·라벨 부재) — 결정적 정렬만.
- **TraceDiag** — Ding et al. (FSE 2023 Industry · arXiv 2310.18740): 대규모 트레이스에서 **의존 그래프 가지치기
  (강화학습)** 로 해석 가능한 RCA. → 제니퍼 X-View의 프로파일(메서드·SQL·외부 호출 타임라인)이 곧 단일
  트랜잭션의 트레이스다. **P9** 느린 트랜잭션 상위 N의 **시간 분해(cpu/sql/fetch/externalcall/network)** 를
  결정적으로 집계해 "지연이 어디에 쌓였는가"를 먼저 판정한다(§5.4-b `apm_slow_transactions`).
- **MicroRank** — Yu et al. (WWW 2021 · DOI 10.1145/3442381.3449905) · **TraceRCA** — Li et al. (IEEE/ACM IWQoS 2021 ·
  DOI 10.1109/IWQOS52092.2021.9521340): 정상/비정상 트레이스가 각 구간을 지나는 비율(**스펙트럼 분석**)로 학습 없이
  원인을 랭킹 — "비정상은 많이, 정상은 적게 지나는 구간이 원인". **Nezha** — Yu et al. (FSE 2023 · DOI
  10.1145/3611643.3616249): 이종 관측 데이터를 **동질 이벤트로 정규화**한 뒤 패턴 마이닝(top-1 89.77%). →
  **P16 결정적 후보 축소**: 느린/에러 트랜잭션이 많이 지나고 정상은 적게 지나는 구간(SQL·외부 호출·메서드)을
  **서버측이 결정적으로 랭킹**해 후보를 줄인 뒤 LLM에 넘긴다(컨텍스트 비용과 앵커링을 동시에 줄임 · §5.2
  `apm_slow_transactions`의 분해 집계가 그 1차 구현). 제니퍼 이벤트와 폴스타 알람은 **하나의 이벤트 스키마**로
  정규화한다(§5.5 표 — Nezha의 골격).
- **CIRCA** — Li et al. (KDD 2022 · arXiv 2206.05871) · **RUN** — Lin et al. (AAAI 2024 · arXiv 2402.01140):
  개입 인식·Granger 인과로 원인 지목. → 인과 추론 모델은 미채택. 다만 **"선행 사건이 먼저"** 라는 순서 원칙
  (사건창 안의 선행 이벤트 확보 — 현행 `polestar_incident_alarms` 선행 규칙)을 APM 이벤트에도 적용한다.
- **RCAEval** — Pham et al. (WWW 2025 Companion · arXiv 2412.17015) · Wang et al., *A Comprehensive Survey on RCA
  in (Micro)Services* (arXiv 2408.00803). → 평가 축(원인 서비스·원인 지표·원인 유형)을 우리 골든 시나리오(§6 J3
  수용 기준)에 그대로 쓴다.

### 4.3 벤치마크·자율 운영 에이전트

- **OpenRCA** — Xu et al. (ICLR 2025 · 실제 장애 335건 + 68GB 텔레메트리): 최상위 모델도 **11.34%** 만 해결.
  **ITBench** — Jha et al. (ICML 2025 · PMLR 267 · arXiv 2502.05352, IBM): SOTA 에이전트가 SRE 시나리오 **13.8%**
  만 해결. **AIOpsLab** — Chen et al. (arXiv 2501.06706, Microsoft) · **OpsEval** — Liu et al. (FSE 2025 Companion ·
  arXiv 2310.07637 · 골든셋 80% 비공개 운영) · **SREGym** — Clark et al. (arXiv 2605.07161). 탐지→위치→진단→
  **완화**까지 단계별로 에이전트를 평가하며, 완화 단계 성공률이 가장 낮다. → **완전 자율 RCA·복구를 범위에서
  제외하고 "증거 수집 → 후보 축소 → 브리핑 → 승인형 완화"로 잡는 정량 근거**다(§5.7 L3 범위 밖). → **P10** 대응·복구는 진단과 별도
  **수용 기준**을 갖는다: "권고가 맞았는가"가 아니라 "실행 후 지표가 회복됐는가"(검증 루프). 그리고 착수 전에
  **목업 장애 시나리오**(Plan 65 목업 이벤트 생성기 확장 — WAS 시나리오)로 측정한다.
- **STRATUS** — Chen et al. (**NeurIPS 2025** · arXiv 2506.02009). 자율 SRE 멀티에이전트에 **Transactional
  No-Regression(TNR)** — (1) 완화 실패 시 항상 되돌릴 수 있고 (2) 건강 상태를 악화시키는 행동은 취소되도록 보장.
  AIOpsLab·ITBench 완화 성공률 1.5배 이상. → **P11** L2 실행기는 조치마다 (사전 상태 스냅샷,
  조치, 사후 검증, 실패 시 롤백)을 **하나의 트랜잭션**으로 묶는다(§5.7).

### 4.4 알람·이벤트 상관 (제니퍼 이벤트를 게이트에 넣을 때)

- **iPACK** — Liu et al. (ICSE 2023 · arXiv 2302.09520): 티켓·알람을 **인시던트 인식**으로 묶는다. **COLA** —
  Kuang et al. (ICSE-SEIP 2024 · arXiv 2403.06485): 상관 마이닝으로 시간·공간 관계를 잡고 **불확실한 케이스만
  LLM**에 넘기는 하이브리드(F1 0.901~0.930, 운영 배포). **DiLink** — Ghosh et al. (WWW 2024 Companion · arXiv
  2403.18639 — v1의 "LiLAC·FSE 2024" 표기는 오류): 텍스트 임베딩 + **서비스 의존 그래프** 정렬로 인시던트 연결
  (F1 0.96) — 상관 축에 **의존 토폴로지**를 넣으면 성능이 크게 오른다. **AlertGuardian** — Yu et al.
  (ASE 2025 · arXiv 2601.14912): 알람 생애주기 관리. **Oasis** — Jin et al. (FSE 2023 Industry · arXiv 2305.18084): 장애
  요약. → **P12** 제니퍼 이벤트는 **폴스타 알람의 대체가 아니라 상관 축**이다 — 같은 hostname·같은 사건창의
  인프라 알람과 묶어 하나의 사건으로 보고, `app_impact`는 **승격 전용**(억제를 되돌리지 않음 · 심각도 3 불변 —
  D-048 비대칭 계승)으로만 게이트에 영향을 준다.

### 4.5 대응·복구 자동화와 안전 (Plan 78 §3.3 문헌 재인용 + 추가)

- **IPIGuard**(EMNLP 2025) · **Task Shield**(ACL 2025) · **Adaptive Attacks**(NAACL Findings 2025) · **Coding Agents
  Are Guessing**(arXiv 2607.02294) — Plan 78 §3.3의 결론을 그대로 잇는다: *방어를 넣었다고 안전한 것이 아니라
  **실행될 명령이 아예 없어야** 인젝션이 성공해도 피해가 없다.* → **P13** L2에서도 **LLM 평면에는 실행 도구를
  주지 않는다**. 실행기는 LLM 미탑재·결정적·승인된 제안 id만 입력으로 받는 별도 경계다. HolmesGPT의
  `Toolset.approval_required_tools`(sre-agent/02 §9 실측)는 "LLM이 실행 도구를 갖되 승인만 붙이는" 방식이라
  **미채택**한다.
- **Google SRE Book ch.14 Managing Incidents · SRE Workbook ch.9 Incident Response**[S-1][S-2]: 역할 분리·
  명령 계통·IMAG 3C(Coordinate·Communicate·Control) · **"완화 먼저, 근본원인은 뒤"** — 롤백·드레인 같은 **일반
  완화(generic mitigation)** 우선 · 사후 검토. → **P14** 조치 카탈로그의 정렬 기준은 "효과"가 아니라 **가역성**
  이다(가역·저범위 먼저). → **P17** 브리핑에서 "즉시 적용 가능한 일반 완화"와 "근본원인 가설에 묶인 조치"를
  **분리된 섹션**으로 싣는다(§5.4-d).
- **Facebook FBAR(자가 치유)**[S-3] · **LinkedIn Nurse**[S-4]: 탐지 → 오류 클래스 **화이트리스트** 안에서만 자동
  복구 → 실패 시 **사람 티켓 종착**. 자동화 범위를 **가장 흔하고 가장 안전한 조치**부터 넓혔다. → P14 보강:
  L2 초기 카탈로그는 2~3개 조치로 시작하고, 카탈로그 밖은 실행기가 거부한다.
- **GIRA — Guarded Tool-Using LLM Agents for Incident Response** (OpenReview 워크숍 투고본 2026-03 · 저자·채택
  여부 미확인 — 보조 근거): 제안 생성과 행동 인가를 분리하는 다층 안전 게이트(차단 · dry-run 재작성 ·
  에스컬레이션), 툴 출력·티켓에 심긴 인젝션을 위협 모델에 포함, 지표로 **비인가 행동률**·blast radius. →
  §5.7 L2의 구조와 동형이며, J6 수용 기준에 "비인가 행동률 0"을 지표로 넣는다.
- **Agentic AIOps 가드레일 / AI SRE 성숙도 곡선**(Plan 78 §3.3 재인용): read-only → advised → **approval-based
  remediation** → autonomous. → 본 계획의 L2 = approval-based. L3(autonomous)는 범위 밖.

### 4.6 WAS 도메인 진단 지식 (1차 출처)

| 증상 | 진단 시그니처(제니퍼 데이터) | 표준 대응 | 출처 |
|---|---|---|---|
| **스레드 풀 고갈·서비스 큐잉** | `activeService` ≈ 상한 · `ERROR_SERVICE_QUEUING`/`PLC_REJECTED` · 액티브 서비스 다수가 같은 상태(`JEXENG`·외부 호출 대기)에 정체 · **stuck thread**(연속 작업 ≥ N초 — WebLogic `StuckThreadMaxTime` 기본 **600초**[W-6] · JEUS는 `max-thread-active-time` 초과 시 Blocked Thread 통지[W-7] △) · Tomcat은 `maxThreads` 포화 → `acceptCount` 큐 초과 시 거부[W-1] | 정체 지점(DB·외부 호출) 해소 → 원인 트랜잭션 격리 → 풀 상한·큐 조정은 재기동 필요 | Tomcat HTTP Connector·`Executor`[W-1] · WebLogic Overload[W-6] · JEUS[W-7] · JDK 21 Troubleshooting(행·루프)[W-8] |
| **커넥션 풀 고갈** | `runningDataSourceName` 대기 다수 · `JDBC_CONNECTION_FAIL` · `DB_CONN_UNCLOSED`/`RESOURCE_LEAK` · (HikariCP) pending↑ + `connectionTimeout` 예외 + `leakDetectionThreshold`(≥2000ms) 누수 로그 | 누수 트랜잭션 식별(X-View) → **풀 확대보다 누수·slow SQL 제거가 1순위** → 풀 상한·`removeAbandoned`·타임아웃 | Tomcat JDBC Pool[W-2] · HikariCP 풀 사이징(`core×2+spindle` · 데드락 최소 풀 `Tn×(Cm−1)+1`)[W-3] |
| **GC stall·힙 압박·누수** | `heapUsed/heapCommitted` 고점 지속 · `MAYBE_GC_TIME_DELAY` · `JVM_HEAP_MEM_HIGH` · `OUTOFMEMORY` · **OOM 메시지 7종**(Java heap space · GC overhead limit exceeded=GC 시간 98%·회수 2% 미만 5회 연속 · Metaspace · Requested array size · Out of swap · Compressed class space · Native method)[W-8] · **GC 후 old gen 하한이 계단식으로 상승 = 누수**(Cork · §12.1) | 힙 덤프(`HeapDumpOnOutOfMemoryError`·`jcmd GC.heap_dump`)·누수 객체 식별(컬렉션 유형 상위 우선) → 힙/GC 옵션 조정(재기동) | Oracle HotSpot GC Tuning Guide[W-4] · JDK 21 Troubleshooting(메모리 누수)[W-8] · OTel JVM 메트릭 컨벤션[W-5] |
| **슬로우 SQL** | `sqlTime`/`fetchTime` 비중↑ · `/api/status/sql` 상위 · `DB_TOOMANY_FETCH` | 실행계획·인덱스(DBA) — 앱 측은 페이징·fetch size | (DPM 축 — Plan 55 M3) |
| **외부 호출 지연** | `externalcallTime` 비중↑ · `HTTP_IO_EXCEPTION` | 타임아웃·서킷브레이커 · 의존 서비스 조사로 전이 | 토폴로지(제니퍼 MSA 뷰[J-12]) |
| **에러 급증** | `HIGH_RATE_FAIL` · `errorType` 분포 · 배포 직후 여부(`/api-v2/deploy` △) | 배포 롤백(고위험) | SRE Book rollback-first[S-1] |

### 4.7 설계 원칙 요약 (P1~P14 → 어디에 반영되는가)

| 원칙 | 반영 위치 |
|---|---|
| P1 증거 수집은 결정적 핸들러, LLM은 해석 | §5.2 도구 표면 · §5.4-a 지침 |
| P2 원시 응답은 서버측 축약 | §5.2 상위 N·마스킹 |
| P3 완화 권고는 시그니처→조치 표 | §5.4-c |
| P4 도구는 적게·질문형·값 인자 | §5.2 8종 상한 |
| P5 SOP/플레이북을 선언적 파일로 | §5.4-a `apm_playbooks.yaml` |
| P6 인용 승격·가설 분리·정체 종료 | §5.4-d 브리핑 |
| P7 골격은 코드·LLM은 칸 채우기 | 현행 구조 유지 |
| P8 같은 엔티티 키·같은 타임라인 | §5.3 정합 |
| P9 지연의 시간 분해 먼저 | §5.2 `apm_slow_transactions` |
| P10 대응·복구는 별도 수용 기준(회복 검증) | §6 J6 |
| P11 조치 = 트랜잭션(스냅샷·검증·롤백) | §5.7 |
| P12 APM 이벤트는 상관 축·승격 전용 | §5.5 |
| P13 LLM 평면에 실행 도구 없음 | §5.7·§8.1 |
| P14 가역성 우선·작게 시작 | §5.7 카탈로그 |
| P15 반증·정체 가드(동일 도구 반복 감지·반증 1회) | §5.4-a 지침 · J3 수용 기준 |
| P16 결정적 후보 축소(스펙트럼 랭킹)·이벤트 정규화 | §5.2 `apm_slow_transactions` · §5.5 표 |
| P17 일반 완화 섹션 분리(완화 먼저) | §5.4-d 브리핑 |

---

## 5. 목표 아키텍처

### 5.1 한 장 그림

```
 [제니퍼 View Server]                                   [제니퍼 RDB Export → PostgreSQL]
   Open API v1/v2 (Bearer)                                 DOMAIN/INSTANCE_METRIC_{5MIN,1HOUR,1DAY}
   EVENT 어댑터 / 이벤트 폴링                              APPLICATION_METRIC_1DAY · TRANSACTION_*
        │  REST(httpx)                                            │  SQL(asyncpg, readonly)
        ▼                                                         ▼
 ┌──────────────────────────── mcp_server (관측 읽기 경계 · D-119) ─────────────────────────────┐
 │  sources: polestar_*(PG/DB2) · jennifer_export(PG)      JenniferApiConfig(url·token·timeout·limits) │
 │  도구: polestar_* 8 · prom_* 7 · **apm_* ≤8** (벤더 중립 표면 · source_kind apm_db|apm_api)          │
 │  서버측: 인스턴스↔hostname 정합(config/apm_instance_map.yaml) · 상위 N 축약 · 마스킹 · 감사 · Bearer │
 └───────────────┬──────────────────────────────────────────────────────┬───────────────────────────┘
                 │ 자동 발견(RemoteMCPToolset)                            │ DBHubClient
                 ▼                                                        ▼
 ┌──────── sre_agent (조사 · D-118) ────────┐     ┌──────────── src 본체 / noise_gate ─────────────────┐
 │ 지침: APM 조사 순서·사건창(ANCHORED_TOOLS+)│     │ pull: text2sql `apm` 솔루션 축 · fault_diagnosis  │
 │ 판정: WAS 시그니처 8종(결정적·yaml 임계)  │     │ push: 제니퍼 이벤트 → alarm:raw → 게이트(app_impact│
 │ 권고: WAS 조치 표(가역성·위험도·검증법)   │     │       승격 전용) → investigation_trigger           │
 │ 브리핑: APM 증거 절 · 인용 검증           │     │ UI: 소스 배지 "제니퍼" · 조치 승인 화면(L2)        │
 └───────────────┬──────────────────────────┘     └──────────────────────────┬─────────────────────────┘
                 │ RemediationProposal(id·근거·위험도·검증법)  ── 승인(HITL) ──┘
                 ▼
 ┌──── remediation_executor (L2 · 신규 경계 · LLM 미탑재 · 옵트인 · G-6 후) ────┐
 │ 입력: 승인된 proposal id만 · 카탈로그 밖 조치 불가 · blast radius 한도          │
 │ 트랜잭션: 사전 스냅샷 → 조치 → 검증(apm_app_health 재조회) → 실패 시 롤백/에스컬 │
 │ 감사: 누가·무엇을·언제·근거·결과                                                │
 └────────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 데이터 평면 — `mcp_server` 세 번째 소스 (G1)

**(a) SQL 소스 `jennifer_export`** — `config.toml` `[[sources]]` 1항목 + `.env` `JENNIFER_EXPORT_CONNECTION`.
`type=postgresql`·`readonly=true`·`max_rows` 보수값. 기존 `list_sources`·`execute_sql` 게이트가 자동 적용된다.

**(b) API 설정 `JenniferApiConfig`** — `PrometheusConfig` 동형(`url`·`auth_token`(Bearer)·`query_timeout`·
`max_rows`·`rate_limit_per_sec`·`expose_raw_api=False`). 토큰은 `.env`에만(`JENNIFER_API_TOKEN`), 로그 마스킹.

**(c) 도구 표면 — 벤더 중립 `apm_*`(Plan 78 §4.7.2 4종 + 진단 필수 4종 = 8종 상한)**

| 도구 | 인자(값만) | 뒷단 | 반환 핵심 필드(OTel 컨벤션[W-5]) | 용도 |
|---|---|---|---|---|
| `apm_app_health` | `hostname`(또는 `instance`), `reference_time?`, `lookback_minutes?` | API `/api/realtime/instance`(현재) · SQL `INSTANCE_METRIC_5MIN`(구간) | `http.server.request.duration`(p50/p95) · `tps` · `error_rate` · `active_services` · `reject_rate` | 골든 시그널 |
| `apm_runtime_health` | 동상 | API 실시간 · SQL 구간 | `jvm.memory.used/committed` · `jvm.gc.duration`(△ 메트릭 존재 시) · `process.cpu.utilization` | 힙/GC/CPU |
| `apm_resource_pool` | `hostname` | API 액티브 서비스 집계 · SQL(△ 풀 메트릭 존재 시) | `db.client.connection.count{state}` · `thread_pool.active/max` | 풀 고갈 |
| `apm_slow_transactions` | `hostname`, `n≤20`, 구간 | API `/api/status/application`(sort) + `/api/transaction/*` | 상위 N: `name` · `duration` · **분해**(`cpu`·`sql`·`fetch`·`external`·`network`) · `error_type` | P9 시간 분해 |
| `apm_active_services` | `hostname`, `n≤20` | API `/api/activeService/list` | `status` · `elapsed` · `running_mode` · `running_text`(마스킹) · `datasource` · `client_ip`(마스킹) | 큐잉·정체 지점 |
| `apm_events` | `hostname`, 구간, `level?` | API `/api/dbsearch/event` | `event_type` · `level` · `time` · `value` · `message`(마스킹) | 선행 이벤트 |
| `apm_transaction_profile` | `txid` | API `/api/transaction/profile.txt`·`/sql` | 프로파일 **요약**(단계별 시간 상위 K, SQL 바인드 값 마스킹) | 개별 트랜잭션 |
| `apm_instance_map` | `hostname?` | 정합 파일 + `/api/instance` | `instances[]{instance_id, name, domain, port, kind, match_confidence}` | 대상 확정·다중 인스턴스 |

- **구간 인자 규약**: `reference_time`·`lookback_minutes`는 현행 `incident_scope`와 동일 형식. 인자 없으면 "현재"
  이며, 지침(§5.4-a)이 사건 조사에서는 인자를 강제한다.
- **1분 창 제한**(`/api/transaction/time`)은 서버측이 구간을 1분 단위로 **분할 호출·상한(기본 10분)** 한다 — LLM에
  노출하지 않는다.
- **상위 N·마스킹은 서버측**(P2): `running_text`·`message`·SQL 바인드·URL 파라미터·`client_ip`는 `pii_filter`
  규칙(§8.3)으로 마스킹 후 반환. 원문은 감사 로그에도 남기지 않는다(마스킹본만).
- **원시 도구**는 옵트인: SQL은 기존 `execute_sql` 게이트(소스 `jennifer_export` 허용 시), REST는 `apm_raw_api`
  (`expose_raw_api=True`일 때만 · GET 화이트리스트 경로만).
- **반환 계약**: `{rows|data, row_count, queried_at, source_kind: "apm_db"|"apm_api", source: "jennifer",
  window?, instance_resolution: {matched, confidence, reason}}`. 미매칭 시 **빈 결과가 아니라 `{error:
  "instance_unresolved", reason}`**(침묵 폴백 금지).

### 5.3 엔티티 정합 — 인스턴스 ↔ hostname (G2 · **R-1**)

- **1순위 브릿지 — 폴스타 `was_object`**(`agent_id`·`hostname`·`obj_name`, §3.3): 운영 DB에 실재하고 `agent_id`가
  채워져 있으면(U-10) 이것이 정본이다 — 폴스타가 이미 관리하는 매핑을 재사용하고 새 매핑 파일은 **예외만** 담는다.
  조회는 `mcp_server`의 SQL 소스(폴스타)로 하며, 결과는 `apm_instance_map` 도구가 캐시(TTL 10분)한다.
- 2순위 정본: **`config/apm_instance_map.yaml`**(선언적 · `middleware_signatures.yaml`과 같은 자세 — "정책은 파일에").
  ```yaml
  version: 1
  match_rules:                     # 자동 매칭(순서대로, 첫 성공)
    - kind: polestar_was_object    # 폴스타 was_object(agent_id ↔ hostname) — 운영 실재 시 1순위(U-10)
    - kind: exact                  # instanceName == hostname
    - kind: prefix                 # instanceName.startswith(hostname + "_")  → 인스턴스 구분자
    - kind: regex
      pattern: '^(?P<hostname>[a-z0-9-]+)[-_](?P<inst>\w+)$'
  overrides:                       # 수동 매핑(자동 규칙보다 우선)
    - instance_name: "WAS-KIMPO-01"
      hostname: "fgtsidd0"
      port: 8080
      kind: tomcat
  ```
- 해소 결과에 **신뢰도**(exact=high · prefix/regex=medium · override=high)와 **사유**를 싣고, `medium` 이하는
  브리핑에 "정합 근거"를 표기한다. 신뢰도 없음(미매칭)은 상관 보류(Plan 55 R-1 보수 원칙).
- 한 호스트 다중 인스턴스: `apm_instance_map`이 목록을 돌려주고, 사건 이벤트에 `instanceId`가 있으면 그것을
  우선, 없으면 **전 인스턴스를 순회**(상한 5)해 집계한다.
- 역방향(제니퍼 이벤트 → hostname)은 §5.5 수신기가 **같은 파일**로 해소한 뒤 `alarm:raw`에 넣는다 — 트리거
  계약 `REQUIRED_EVENT_FIELDS`(`hostname`)를 만족시키기 위해서다.

### 5.4 진단 확대 — `sre_agent` (G3)

**(a) 지침** — `investigation_guidance.py`
- `ANCHORED_TOOLS`에 `apm_app_health`·`apm_runtime_health`·`apm_events`·`apm_slow_transactions` 추가(사건창 강제).
- **APM 조사 순서 노트**(`APM_FOCUS_NOTE`) — RCACopilot의 유형별 핸들러 순서를 지침으로: ① `apm_instance_map`으로
  대상 확정 → ② `apm_events`(선행 이벤트) → ③ `apm_app_health`·`apm_runtime_health`(골든 시그널·런타임) →
  ④ 증상별 분기(큐잉이면 `apm_active_services`, 지연이면 `apm_slow_transactions` → `apm_transaction_profile`,
  풀이면 `apm_resource_pool`) → ⑤ 인프라 대조(`polestar_metric_trend`·`prom_metric_range`) → ⑥ OS 근사(W7-1)는
  **APM이 답하지 못한 것만**.
- **반증·정체 가드**(P15): 지침에 "주 가설에 대한 **반증 도구 호출 1회**"를 요구하고, 코드는 동일 도구·동일 인자
  **반복 호출 3회**를 감지해 조사를 "미결"로 종료한다(`max_steps` 소진 전 · 사유 브리핑 `[한계]`에 기재).
- **플레이북** `config/apm_playbooks.yaml`(P5): 이벤트 유형/시그니처 → 확인 순서·판정 기준·권고 후보 키.
  지침에는 해당 시그니처의 플레이북만 주입한다(컨텍스트 최소화).
- **프로파일 2단계**: `middleware_profile()`은 유지하되, **APM 가용 여부**를 `sre_health`류로 사전 확인해 미가용
  시 "APM 미가용 — OS 근사로 폴백(사유)"를 지침과 브리핑 `[한계]`에 남긴다(Plan 78 W7-2 ④ · 침묵 강등 금지).

**(b) WAS 시그니처** — `domain/severity_signatures.py` 확장(결정적 · 임계는 `config/apm_signatures.yaml`)

| kind | 판정(도구 출력 기반) | level | category |
|---|---|---|---|
| `was_service_queuing` | `active_services ≥ 0.9·limit` 또는 이벤트 `SERVICE_QUEUING`/`PLC_REJECTED` | CRITICAL | strong |
| `was_thread_pool_exhaustion` | 액티브 서비스 상위 N 중 동일 `running_mode` 정체 비율 ≥ 0.7, `elapsed` ≥ T(기본 **600s** — WebLogic `StuckThreadMaxTime` 전례[W-6]) | CRITICAL | strong |
| `was_db_pool_exhaustion` | `db.client.connection.count{state=wait}` > 0 지속 또는 `JDBC_CONNECTION_FAIL`·`DB_CONN_UNCLOSED` | CRITICAL | strong |
| `was_gc_stall` | `MAYBE_GC_TIME_DELAY` 또는 `jvm.gc.duration` 비중 ≥ x% | WARNING | medium |
| `was_heap_pressure` | `jvm.memory.used/committed ≥ 0.9` 지속 ≥ k 샘플 · `JVM_HEAP_MEM_HIGH`·`OUTOFMEMORY`(메시지 7종 분기[W-8]) · GC 후 old gen 하한 **계단식 상승**(누수 시그니처 — 5분 통계 적재본으로 판정) | CRITICAL(OOM)/WARNING | strong/medium |
| `was_slow_sql` | 상위 N 트랜잭션의 `sql+fetch` 비중 ≥ 0.6 | WARNING | medium |
| `was_external_call_delay` | `external` 비중 ≥ 0.6 · `HTTP_IO_EXCEPTION` | WARNING | medium |
| `was_error_burst` | `error_rate` 기준선 대비 ≥ 3σ 또는 `HIGH_RATE_FAIL` | CRITICAL | strong |
임계값은 **잠정**이며 J0 실측·목업 시나리오(J3 수용 기준)로 보정한다. LLM은 임계를 판단하지 않는다(D-035).

**(c) 권고 표** — `domain/remediation.py` `_CANDIDATES_BY_SIGNATURE`에 WAS 항목 추가(P3·P14 — **가역성 순**)

| kind | 후보(요지) | 위험도 |
|---|---|---|
| `was_service_queuing` | 정체 지점(DB/외부) 확인 후 해당 트랜잭션 인터럽트(제니퍼 콘솔) | medium |
|  | PLC 상한 임시 하향으로 유입 차단(리다이렉트 페이지) | medium |
|  | 인스턴스 재기동(원인 미제거 시 재발) | **high** |
| `was_db_pool_exhaustion` | 누수 의심 트랜잭션(X-View `DB_CONN_UNCLOSED`) 식별·개발팀 전달 | low |
|  | 풀 상한·`removeAbandoned` 조정 후 재기동 | high |
| `was_heap_pressure` | 힙 덤프 채취(서비스 덤프) 후 누수 객체 분석 | low |
|  | 강제 GC(일시 완화 · STW) | medium |
|  | 힙 옵션 조정 후 재기동 | high |
| `was_error_burst` | 최근 배포 여부 확인 → 롤백 검토 | high |
| `was_slow_sql` | 상위 SQL을 DBA 검토로 전달(DPM 축) | low |
| `was_external_call_delay` | 의존 서비스 조사로 전이(토폴로지) · 타임아웃 확인 | low |
고위험×저신뢰 강등 규칙(`_HIGH_RISK_MIN_CONFIDENCE`)은 그대로 적용된다. 각 항목에 **검증 방법**(예: 조치 후
`apm_app_health` p95·error_rate 회복)과 **롤백**(예: PLC 원복)을 필드로 추가한다 — L2(§5.7)의 입력이 된다.

**(d) 브리핑** — `briefing_builder`
- 인용 검증 `tool_names`에 `apm_*` 포함. 6요소 중 `evidence`에 **"애플리케이션(APM)"·"인프라"** 소스 라벨을 구분해
  싣는다(교차 상관의 가시화 · Plan 55 §6).
- `[한계]`에 정합 신뢰도·APM 미가용 폴백·1분 창 분할 상한 도달을 반드시 기재한다.
- **일반 완화 섹션 분리**(P17 · IMAG): 권고를 "즉시 적용 가능한 **일반 완화**(격리·트래픽 배제·PLC 유입 차단·
  재기동·롤백)"와 "**근본원인 가설에 묶인 조치**(근거·신뢰도)"로 나눠 싣는다 — 완화가 먼저, 근본원인은 뒤.
- `HUMAN_GATED_NOTE`는 L1에서 유지. L2 활성 시에는 "승인 대기 제안 id"로 문구가 바뀐다(§5.7).

### 5.5 이벤트 편입(push) — 제니퍼 이벤트 → 노이즈 게이트 → 조사 (G4)

**1단계 — 폴링 수신기(Java 코드 0)**: `noise_gate/alarm_server/jennifer_poll_receiver.py`(`BaseReceiver` 상속) —
주기(기본 30s)로 `/api/dbsearch/event?level=warning,fatal&start=…`를 호출해 신규 이벤트만(`eventId`/`time`
커서) `alarm:raw`에 발행한다. ⚠ 이 수신기는 **`mcp_server`를 거치지 않고 Open API를 직접 호출**한다 — 관측
경계 일원화(D-119)와 긴장하는 지점이다. 선택지: ① 수신기가 `mcp_server`의 `apm_events`를 호출(경계 준수 ·
지연·의존 추가) ② 직접 호출(단순 · 자격증명 2곳). **권고 ①** — 자격증명·감사를 한 곳에 두는 것이 D-119의
취지이고, `alarm_server`는 이미 Redis만 아는 얇은 프로세스다. **G-4 게이트**.

**2단계 — EventHandler 어댑터 push**: 공식 `jennifer-view-adapter-tutorial`[J-6] 골격으로 EVENT 타입 어댑터
(Java/Kotlin)를 만들어 뷰 서버에 배포, `alarm_server` TCP 수신기(기존 `tcp_receiver.py`)로 JSON 1행을 보낸다.
지연 초 단위 · 폴링 부하 0. 선행: 제니퍼 운영 조직의 어댑터 배포·업그레이드 정책(U-9).

**페이로드 정규화(공통)**: 폴스타 템플릿 필드로 매핑해 `AlarmEvent`가 그대로 받게 한다.

| 제니퍼 EventData | `alarm:raw` 필드 | 비고 |
|---|---|---|
| `instanceName`·`instanceId` | `hostname`·`serverName`(정합 파일로 해소) + `raw_payload.apm.instance_*` | 미해소 시 `hostname=""` → 트리거가 사유 남기고 생략 |
| `eventLevel` fatal/warning/normal | `severity` 3/2/1 · 해소(RECOVERY/CLEAR △) → 0 | 선언적 매핑 `config/apm_event_levels.yaml` |
| `errorType` | `alarmName`(원문 유지) · `resourceType="apm.Instance"` | `resource_type`으로 소스 구분 |
| `time`·`value`·`message`·`txid` | `alarmTime`·`conditionLog`·`conditions`·`raw_payload.apm.txid` | `message` 마스킹 |
| (상수) | `dbId="jennifer"` · `source="jennifer"` | `ServerIdentity.source_label` 배지 = family "제니퍼" |

**게이트 통합** — `app_impact` 축(Plan 55 §3·D-048.6 자리): 같은 `hostname`·사건창(기본 10분)에 폴스타 알람과
제니퍼 fatal 이벤트가 겹치면 **승격만**(DASHBOARD→PAGE 등). 억제를 되돌리거나 심각도 3을 건드리지 않는다.
제니퍼 이벤트 **단독**은 유형별 정책(`apm_event_levels.yaml`의 `notify: page|dashboard|suppress`)을 따른다.
트리거 페이로드에 `hints: {solution: "apm", instance_id, event_type}`를 실어 `sre_agent` 지침이 플레이북을 고르게 한다.

### 5.6 질의 경로(pull) — text2sql (G5)

- `db_registry.yaml`: `solutions`의 `apm` 주석을 **해제·수정** — `backend: sql`(적재본) · `family: jennifer` ·
  `capabilities: [was_metric, jvm_heap, thread_pool, transaction, apm_event]` · `requires: [host_location]`.
  `families`에 `jennifer`(product_terms `["제니퍼","jennifer","APM"]`) · `databases`에 `jennifer_export`
  (`engine: postgresql`, `env_connection_key: JENNIFER_EXPORT_CONNECTION`, 존은 적재본 배치에 따라 J0에서 확정).
- `config/db_profiles/jennifer_export.yaml`·`config/knowledge/jennifer_export/`·`config/synonym_seeds/jennifer_export.yaml`
  신설 — 컬럼 DDL(U-2) 실측 후. 일자별 `TRANSACTION_{domain}_YYYYMMDD` 테이블은 **결정적 조립**(날짜 → 테이블명)
  대상이다(LLM이 테이블명을 만들게 두지 않는다 — Known Mistakes "고정 스키마는 코드가 조립").
- 라우팅: 실행 그룹이 `apm` 솔루션을 `order: 20`으로 폴스타 뒤에 순차 실행(Plan 82 D-176). "김포 WAS 응답시간"
  → `host_location`(폴스타) 선행 → `apm` 조회. `fault_diagnosis` 의도는 변경 없음(대상 확정 후 조사 위임).
- UI: 응답·알람 카드에 소스 배지 "제니퍼"(기존 `source_label` 경로).

### 5.7 대응·복구 — 자율성 사다리와 L2 실행 경계 (G6 · **G-6 게이트**)

| 단 | 이름 | 할 수 있는 것 | 상태 |
|---|---|---|---|
| L0 | 관측 | 조회·브리핑 | 현행 |
| L1 | 권고(advised) | 시그니처→조치 표에서 후보 제시(위험도·신뢰도·검증법·롤백) | 현행(`remediation_recommender_enabled`) — 본 계획이 WAS 항목 추가 |
| **L2** | **승인 후 실행(approval-based)** | 운영자가 제안 id를 승인하면 **결정적 실행기**가 카탈로그 조치를 트랜잭션으로 수행 | **본 계획이 여는 유일한 단** — D-003 예외·G-6 확정 뒤 |
| L3 | 자율(autonomous) | 승인 없이 실행 | **범위 밖**(Plan 78 §8.3) |

**L2 설계 — Plan 78 §8.3 5조건에 대한 응답**

| 조건 | 설계 |
|---|---|
| ① D-003 명시적 예외 | **D-195 ③**로 신설 — 예외 범위는 "카탈로그에 등재된 조치 · 승인된 제안 id · 대상 1 인스턴스"로 한정. 읽기 전용 원칙은 조사 평면에서 **불변**, 실행 평면만 예외 |
| ② 승인 UX·주체·권한 | 본체 어드민(운영자 role)에 **승인 대기함**: 제안(근거 인용·위험도·검증법·롤백·blast radius) 표시 → 승인/기각 · 고위험은 **이중 승인**(운영자 + 관리자). JWT `type`·role 명시 검증(보안 원칙) |
| ③ blast radius·롤백·policy-as-code | 정책 파일 `config/remediation_policy.yaml`: 조치별 허용 여부·최대 범위(인스턴스 1·사건당 1회·쿨다운 30분·동시 1)·롤백 절차·금지 시간대. 실행기는 정책 밖 요청을 **거부**한다 |
| ④ 감사·사후 검증 | 트랜잭션(P11): 사전 스냅샷(`apm_app_health`·`apm_runtime_health`) → 조치 → **검증**(N분 뒤 재조회, 회복 기준 = 정책 파일) → 실패 시 롤백 → 결과 브리핑. 전 단계 감사(누가·무엇을·언제·근거·결과) |
| ⑤ 적응형 공격 평가 | 실행기는 **LLM 미탑재**(P13)이므로 인젝션이 성공해도 실행될 명령이 카탈로그 밖에 없다. 그래도 J6 수용 기준에 **적응형 공격 시나리오**(도구 출력에 지시문 주입 → 제안 내용 변조 시도)를 넣어 "제안 변조가 실행에 도달하지 않음"을 측정한다 |

**실행기 경계** — `remediation_executor`는 어느 패키지인가: `sre_agent`(조사)와 **분리**한다(권한 분리 · 조사
프로세스에 실행 자격증명을 두지 않음). 후보: ① `noise_gate/` 하위(승인 대기함·감사가 본체 인증 계층에 있음)
② 신규 최상위 패키지 `remediation/`(D-139 — 자체 tests·scripts). **권고 ②** — 실행 자격증명(WAS 관리 API·
SSH 키·LB API)은 독립 프로세스·독립 venv에 두어 본체·조사와 격리한다. 통신은 MCP 계약뿐(D-139 준용). **G-7**.

**초기 카탈로그(P14 — 작게)**: ① 힙/스레드 덤프 채취(읽기성, 위험 low) ② PLC 상한 임시 하향·원복(가역, medium)
③ 인스턴스 재기동(high · 이중 승인 · 단일 인스턴스 · 다중 인스턴스 호스트에서 최소 1개 가용 확인 후). 재기동
실행 채널은 WAS 관리 API 또는 허용목록 스크립트이며 **D-189의 read-only allowlist와 별개 경계**다(섞지 않는다).

### 5.8 관측·감사·설정

- 모든 `apm_*` 호출은 `mcp_server` 감사(도구·인자·행수·소요·source_kind)에 남고 `sql_log`와 같은 형식이다.
- 플래그(전부 **기본 off** — 현행 비트 동일): `mcp_server` `expose_apm_tools=false`·`expose_raw_api=false` /
  `sre_agent` `apm_guidance_enabled=false`·`apm_signatures_enabled=false` / `noise_gate` `jennifer_events_enabled=false`·
  `app_impact_enabled=false` / 본체 `db_registry` `apm` 솔루션 `enabled`(등록 자체) / `remediation_executor_enabled=false`.
- 기동 시 1회 해석(플래그 원칙). 기동 로그 1줄로 "APM 소스 등록 여부·정합 파일 로드 건수"를 남긴다.

---

## 6. 구현 계획 — Wave J0 ~ J6

> 착수 판정은 `선행` 완료 + `게이트` 해제인 Wave만. 과금 API(Gemini 등) 실 호출이 필요한 검증은 **D-127 건별 승인**.
> 착수 시 최근 작업 단위 양식을 따른다 — 계획서 → `CAPABILITY-MAP-87.md` → 모듈별 `SPEC-*.md` →
> `tasks/plan-87.md`·`tasks/todo-87.md`(`tasks/`에 plan/todo 쌍 14개 전례). 각 Wave 착수 직전 §3의 `file:line`을 재실측한다.

| Wave | 내용 | 산출물 | 선행 | 게이트 |
|---|---|---|---|---|
| **J0** 선행 실측 | §2.6 U-1~U-9 실측 · 인스턴스↔hostname 일치율 · DDL 채집 · 토큰 권한 · PII 샘플 | `docs/29_jennifer_integration_survey.md`(가칭) · `testdata/jennifer/`(마스킹 샘플·DDL) | 제니퍼 접근 권한 | **G-1·G-2** |
| **J1** 데이터 평면(SQL) | `[[sources]] jennifer_export` · `apm_app_health/runtime_health`의 **구간(SQL) 경로** · `db_profiles/jennifer_export.yaml` · 결정적 날짜 테이블 조립 | `mcp_server/mcp_server/apm_tools.py`(SQL 빌더 순수 함수) · 테스트(빌더 단위 · Docker PG 픽스처 통합) | J0(U-2) | — |
| **J2** 데이터 평면(API) | `JenniferApiConfig` · `apm_*` API 경로 8종 · 1분 창 분할 · 상위 N · 마스킹 · 레이트 리밋 · `apm_instance_map` | `apm_tools.py`(API) · `apm_client.py`(httpx) · `config/apm_instance_map.yaml` · 픽스처 서버(`respx`/recorded JSON) 테스트 · `pyproject` httpx 선언 | J0(U-4·U-5·U-6) | **G-3** |
| **J3** 진단 확대 | 지침·플레이북·WAS 시그니처 8종·권고 표·브리핑 소스 라벨·프로파일 2단계 폴백 | `investigation_guidance.py`·`severity_signatures.py`·`remediation.py`·`briefing_builder.py` 변경 · `config/apm_playbooks.yaml`·`apm_signatures.yaml` · 목업 WAS 시나리오 6종(Plan 65 확장) | J1·J2 | — |
| **J4** 이벤트 편입 | 1단계 폴링 수신기(경계 준수안) · 정규화 · `apm_event_levels.yaml` · `app_impact` 승격 축 · 트리거 힌트 · 2단계 어댑터(별건 착수) | `noise_gate/alarm_server/jennifer_poll_receiver.py` · `noise_gate/domain/apm_event.py`(순수 정규화) · 게이트 테스트(플래그 off 비트 동일) | J2·J3 | **G-4** |
| **J5** 질의 경로 | `db_registry` `apm`·`jennifer` family·`jennifer_export` DB · 지식·유사어 시드 · 라우팅 골든셋 추가 · UI 배지 | 설정 파일 · `testdata/routing_gold` 추가 · `scripts/catalog_diff.py` 통과 | J1 | **G-5** |
| **J6** 대응·복구 L2 | 승인 대기함 UI·API · `remediation_policy.yaml` · 실행기 패키지(트랜잭션·검증·롤백·감사) · 카탈로그 3종 · 적응형 공격 시나리오 | 신규 패키지 `remediation/`(권고) · 본체 어드민 라우트 · 테스트(정책 거부·롤백·이중 승인·"제안 변조 → 실행 불가") | J3 | **G-6·G-7 + D-195 ③ 등재** |

```
J0 ──► J1 ──┬──► J3 ──► J4 ──► (J6)
      └► J2 ──┘         │
      J1 ────────────► J5
```

**수용 기준(요지)**
- J1·J2: `apm_*` 반환 계약 테스트 고정 · 미매칭 시 `error: instance_unresolved` · 1분 창 분할 상한 · 마스킹 검증
  (`pii_regex_check`) · 플래그 off 시 도구 미등록(비트 동일) · `mcp_server/tests` 무회귀.
- J3: 목업 WAS 시나리오 6종(큐잉·DB 풀·GC stall·힙·슬로우 SQL·외부 지연)에서 **시그니처 판정 정확·권고 후보
  정확**(결정적 — LLM 미사용 테스트) · 브리핑에 APM 증거가 인용됨으로 판정 · APM 미가용 시 폴백 사유 노출.
  RCAEval 축(원인 지표·유형) 기준으로 골든 6/6. 골든셋 정답 일부는 **비공개**로 관리한다(OpsEval 방식 — 프롬프트
  과적합 방지). 반증·정체 가드(P15)는 "반복 호출 3회 → 미결 종료" 테스트로 고정한다.
- J4: 폴링 중복 0(커서) · 정규화 순수 함수 테스트 · `app_impact`가 **승격만** 하는 비대칭 테스트 · 심각도 3 불변 ·
  플래그 off 비트 동일(`test_plan60_flags_off_regression.py` 섹션 추가).
- J5: 라우팅 골든셋 회귀 0 · `apm` 등록 시 실행 그룹 순서(폴스타 → apm) · `catalog_diff` 동등성.
- J6(P10): 목업 시나리오에서 "승인 → 실행 → 검증 회복"과 "검증 실패 → 롤백 → 에스컬레이션" 둘 다 완주 ·
  정책 밖 요청 100% 거부 · 제안 변조 시나리오에서 실행 도달 0 · 감사 레코드 완전성.

---

## 7. 산출물·파일 배치·설정

| 패키지 | 신규/변경 | 파일 |
|---|---|---|
| `mcp_server/` | 신규 | `mcp_server/apm_tools.py` · `mcp_server/apm_client.py` |
|  | 변경 | `config.py`(`JenniferApiConfig`·`expose_apm_tools`) · `server.py`(`register_apm_tools`) · `config.toml`(`[[sources]] jennifer_export`) · `.env.example`(`JENNIFER_EXPORT_CONNECTION`·`JENNIFER_API_URL`·`JENNIFER_API_TOKEN`) · `pyproject.toml`(httpx 선언 — G7) · `security.py`(apm 도메인 deny — 관리 API 경로 차단) |
| `config/` | 신규 | `apm_instance_map.yaml` · `apm_playbooks.yaml` · `apm_signatures.yaml` · `apm_event_levels.yaml` · `db_profiles/jennifer_export.yaml` · `knowledge/jennifer_export/` · `synonym_seeds/jennifer_export.yaml` · (J6) `remediation_policy.yaml` |
|  | 변경 | `db_registry.yaml`(`apm` 솔루션·`jennifer` family·`jennifer_export` DB) |
| `sre_agent/` | 변경 | `application/investigation_guidance.py` · `domain/severity_signatures.py` · `domain/remediation.py` · `application/briefing_builder.py` · `settings.py`(플래그 2) · `toolset_profiles.py`(APM 폴백 노트) |
| `noise_gate/` | 신규 | `alarm_server/jennifer_poll_receiver.py` · `domain/apm_event.py` |
|  | 변경 | `alarm_server/config.py`·`__main__.py`(수신기 선택) · `domain/alarm.py`(source family 배지 — 필드 추가 없이 `raw_payload` 활용 우선) · `application/nodes/notification_gate.py`(`app_impact` 승격 훅) · `application/nodes/investigation_trigger.py`(힌트) |
| `src/` | 변경 | `static/`(소스 배지) · (J6) `api/routes/remediation.py`(승인 대기함) |
| 신규 패키지(J6·G-7) | 신규 | `remediation/`(자체 `pyproject`·`tests`·`scripts`) |
| 문서 | 신규 | `docs/29_jennifer_integration_survey.md`(J0 실측) |

`.env` 신규 키(전부 미입력 시 현행 동작): `JENNIFER_EXPORT_CONNECTION` · `JENNIFER_API_URL` · `JENNIFER_API_TOKEN` ·
`MCP_EXPOSE_APM_TOOLS` · `MCP_EXPOSE_RAW_API` · `APM_GUIDANCE_ENABLED` · `APM_SIGNATURES_ENABLED` ·
`JENNIFER_EVENTS_ENABLED` · `JENNIFER_POLL_INTERVAL_SECONDS` · `APP_IMPACT_ENABLED` · `REMEDIATION_EXECUTOR_ENABLED`.
list/dict 값은 JSON 배열 형식 · 인라인 주석 금지(Known Mistakes).

---

## 8. 안전 통제·거버넌스

### 8.1 불변식 (D-003·D-035·D-189 계승 — L1까지)

- **읽기 전용**: `jennifer_export`는 SELECT 전용 계정 · Open API 토큰은 조회 전용(U-5 — 불가하면 `security.py`가
  `/api-v2/manage-*`·`/restapi/user*`·`deploy`·`rdb-export*` 경로를 **서버측 deny**) · `execute_sql`·`apm_raw_api` 기본 비노출.
- **LLM 평면에 실행 도구 없음**(P13): `apm_*`는 전부 조회다. L2 실행기는 LLM 밖(§5.7).
- **결정적=판단·LLM=서술**: 정합·시그니처·임계·권고 선택·심각도 매핑은 코드·yaml. LLM은 서술만.
- **대상은 결정적 확정**: 인스턴스 해소는 정합 파일 · LLM이 인스턴스명을 추측하지 않는다(scope overflow 방지).
- **침묵 폴백 금지**: 미매칭·미가용·창 상한은 `error`/`[한계]`로 노출.
- **폐쇄망**: 제니퍼는 사내 설치형 — 외부 egress 0. 실 운영 데이터의 외부 LLM 송신은 D-120·D-127 규약.

### 8.2 신뢰 경계

- 제니퍼 응답(`running_text`·`message`·프로파일·SQL·URL 파라미터)은 **불신 데이터**다 — 지시문이 섞일 수 있다
  (Plan 78 §7.2와 동형). 서버측 상위 N·마스킹 후에만 LLM에 넣고, 원문은 저장하지 않는다.
- 관리 API 차단은 토큰 권한(1차)과 `mcp_server` 경로 deny(2차)의 **이중**이다.
- 자격증명은 `mcp_server`(조회)와 `remediation/`(실행)에 **분리 보관** — 조사 프로세스가 실행 자격증명을 갖지
  않는다.

### 8.3 PII·마스킹

- 액티브 서비스 `client_ip` · 트랜잭션 URL 파라미터 · SQL 바인드 값 · `message`/`detailMessage`는 J0 샘플로
  `pii_probe.py`를 돌려 규칙을 확정한다(`docs/pii_filtering_rules.md` 갱신). FabriX PII 필터 차단 시 덤프는
  `logs/pii_block/`에만.
- 감사 로그에도 마스킹본만 남긴다.

### 8.4 부하 가드

- 뷰 서버 보호: 도구별 타임아웃(기본 10s) · 초당 호출 상한(`rate_limit_per_sec`, 기본 5) · 사건당 `apm_transaction_profile`
  호출 상한(기본 5) · 조사 동시성은 현행 `investigation_max_concurrent=2`.
- 폴링 수신기 주기 하한 10s · 백오프.

---

## 9. 리스크·미해결

| # | 리스크 | 심각도 | 대응 |
|---|---|---|---|
| R-1 | **인스턴스↔hostname 미정합** → 데이터가 있어도 무용(Plan 78 R-12) | High | **폴스타 `was_object` 브릿지 우선**(U-10) · J0 U-4 일치율 실측 · 정합 파일은 예외만 · 신뢰도 표기 · 미매칭 상관 보류 |
| R-2 | RDB Export 비활성·PG 최신 버전 비호환·DDL 상이 | Med | J0 U-2 · 대체: C(JDBC+API 서버) 또는 **API 전용 운영**(SQL 경로 없이도 J2·J3 성립 — 질의 경로 J5만 축소) |
| R-3 | 5.x 이벤트 명칭이 4.x와 달라 매핑 파일 오류 | Med | U-1 실측 후 파일 작성 · 미지 유형은 `unknown`으로 DASHBOARD(보수) |
| R-4 | Open API 토큰이 관리 API까지 허용 | High | §8.1 이중 차단 · 토큰 발급 시 최소 권한 협의 |
| R-5 | 뷰 서버 부하·1분 창 제한으로 조사 지연 | Med | §8.4 · 구간 상한 · 서버측 캐시(사건창 단위, TTL 60s) |
| R-6 | PII 노출(프로파일·바인드 값) | High | §8.3 · J0 샘플 검증 전 실 데이터 LLM 투입 금지 |
| R-7 | `app_impact`가 게이트를 과억제/과승격 | Med | 승격 전용 비대칭 · 심각도 3 불변 · 플래그 off 기본 · 결정 기록(decision_store) |
| R-8 | L2 실행기의 오작동·범위 초과 | **High** | 정책 파일 거부 · 단일 인스턴스 · 이중 승인 · 트랜잭션 롤백 · G-6 전 착수 금지 |
| R-9 | 벤더 API 변경(5.x 마이너 업그레이드) | Med | `apm_tools`가 벤더 매핑 계층을 캡슐화 · 계약 테스트(recorded JSON) |
| R-10 | 이벤트 폴링이 D-119 경계를 우회 | Low | G-4 권고안 ①(경계 경유) |
| R-11 | 미들웨어 OS 근사(W7-1)와 APM 판정 충돌 | Low | APM 1차·OS 2차 우선순위 설정 · 충돌 시 브리핑에 둘 다 표기 |

**미해결**: 제니퍼 도입 시점·라이선스(범위 밖 · 본 계획의 착수 전제) · DPM 연동(Plan 55 M3 DPM 축) · L3.

---

## 10. 사용자 확정 게이트

| # | 질문 | 권고 | 영향 |
|---|---|---|---|
| **G-1** | 연동 방식: ⓐ SQL(적재본)만 ⓑ API만 ⓒ **SQL + API + 이벤트(3중)** | **ⓒ** — §0.2·§2.4. ⓐ는 진단 핵심 증거를 잃고, ⓑ는 질의 경로·기준선을 잃는다 | J1·J2·J5 존폐 |
| **G-2** | RDB Export 활성화·PG 배치 주체와 위치(존) | 제니퍼 운영 조직과 협의 — 적재 PG는 `mcp_server` 도달 가능한 존에 | J1 · `db_registry` 존 |
| **G-3** | 도구 표면 이름: 벤더 중립 `apm_*` vs `jennifer_*` | **`apm_*`**(Plan 78 §4.7.2 · 벤더 교체 시 매핑 계층만) | J2 |
| **G-4** | 이벤트 폴링 수신기의 API 호출 경로: ① `mcp_server` 경유 ② 직접 | **①** — D-119 일원화 | J4 |
| **G-5** | text2sql 편입 시점: J1 직후 vs J3 이후 | J1 직후(질의는 진단과 독립 · 사용자 가치 조기) | J5 |
| **G-6** | **L2(승인 후 실행) 착수 여부와 D-003 예외 범위**(카탈로그 3종·단일 인스턴스·이중 승인) | 착수하되 **J3 완료·목업 검증 후** · 초기 카탈로그는 §5.7 3종 | J6 · D-195 ③ |
| **G-7** | 실행기 위치: ① `noise_gate/` 하위 ② **신규 최상위 `remediation/`** | **②** — 실행 자격증명 격리(D-139) | J6 |

---

## 11. 신규 결정 예약 — D-195 (등재는 J0 완료·G-1 확정 시)

> **채번 근거(2026-09-03 실측)**: `docs/02_decision.md` `## D-` 헤더·「변경 이력」 표 최댓값 **D-193** ·
> 「채번 이력」 표 예약(D-105·115·134·158·163~168·176). 다음 번호 194는 작성 시점에 `plans/50` v2.1·
> `CAPABILITY-MAP-50.md`·`SPEC-briefing-contract.md`가 "D-194 예정"으로 쓰고 있어(표 미등재) 충돌을 피해
> **D-195**를 「채번 이력」 표에 등재했다. 같은 날 **D-194는 `plans/50` v2.2로 본문 등재 완료**(`## D-194` 실측)
> — 결과적으로 번호가 연속이며 재조정은 불필요하다.

| 번호 | 결정(예약) | Wave |
|---|---|---|
| **D-195** | **제니퍼 APM 연동** — ① `mcp_server` 세 번째 소스(SQL 적재본 + Open API + 이벤트) · 벤더 중립 `apm_*` 도구 표면 · OTel 컨벤션 · 서버측 정합·축약·마스킹·감사(D-119 ① 적용 · **D-168 2단계의 구체화**) ② 진단 확대 — `sre_agent` 지침·플레이북·WAS 시그니처·권고 표·브리핑(결정적 판정 · APM 1차·OS 근사 2차 폴백 사유 명시) ③ **대응·복구 L2** — D-003의 **실행 평면 한정 예외**(카탈로그 조치·승인된 제안 id·단일 인스턴스·이중 승인·정책 파일·트랜잭션 검증-롤백·LLM 미탑재 실행기 · L3 범위 밖) — **③은 G-6 확정 전 등재하지 않는다** | J1~J6 |

**D-168과의 관계**: D-168은 Plan 78이 "미들웨어 조사 편입(1단계 OS 근사 · 2단계 APM 연계)"으로 예약한 번호다.
본 계획은 그 **2단계의 벤더 확정판**이므로, 등재 시 D-168을 소진(1단계 = 구현 완료 `middleware_profile`·
`middleware_signatures.yaml` · 2단계 = D-195로 위임)하고 D-195에서 상세를 갖는다. 둘을 한 번호로 합치지
않는 이유는 D-168이 Plan 78의 W7 범위 결정이고, D-195는 소스·조치 평면까지 포함하는 별개 범위이기 때문이다.

**파급 문서(등재 시 갱신)**: `plans/55` §7 선행 결정(대상 제품=제니퍼·연동 방식=3중·엔티티 매핑=정합 파일) ·
`plans/78` W7-2단계 상태 · `plans/64` §8.3(선행 거버넌스 → D-195 ③) · `plans/sre-agent/README.md` 계획 표 ·
`config/db_registry.yaml` 주석 · `docs/18_known_mistakes.md`(J0에서 실수 발생 시).

---

## 12. 참고 문헌

> 서지 검증(2026-09-03): §12.1·§12.2는 arXiv API로 제목·저자·게재일을 실측했다. 게재처는 확인된 것만 적었다.
> v1.1은 문헌 조사 보고서(OpenAlex·Semantic Scholar 교차 검증 63건 — §12.5)의 **정정 2건**(RCACopilot 게재처 ·
> 철회본 arXiv 2511.15755 제외)과 게재처 확인분(ITBench·OpsEval·STRATUS·AlertGuardian·StepFly·Riddell)을 반영했다.
> 인용수는 OpenAlex 과소집계 문제(Plan 78 §11 실측)로 싣지 않는다 — 선정 기준은 본 계획과의 논리적 적합성이다.

### 12.1 동료심사 문헌

| 문헌 | 게재 | 식별자 | 반영 |
|---|---|---|---|
| Chen et al., Automatic Root Cause Analysis via Large Language Models for Cloud Incidents (RCACopilot) | **EuroSys 2024** | arXiv 2305.15778 · DOI 10.1145/3627703.3629553 | §4.1 P1 |
| Ahmed et al., Recommending Root-Cause and Mitigation Steps for Cloud Incidents using LLMs | ICSE 2023 | arXiv 2301.03797 | §4.1 P3 |
| Roy et al., Exploring LLM-based Agents for Root Cause Analysis | FSE 2024 Companion | arXiv 2403.04123 | §4.1 P4 |
| Wang et al., RCAgent: Cloud Root Cause Analysis by Autonomous Agents with Tool-Augmented LLMs | CIKM 2024 | arXiv 2310.16340 | §4.1 P2 |
| Jiang et al., Xpert: Empowering Incident Management with Query Recommendations via LLMs | ICSE 2024 | arXiv 2312.11988 | §4.1 P5 |
| Pei et al., Flow-of-Action: SOP Enhanced LLM-Based Multi-Agent System for RCA | WWW 2025 Companion | arXiv 2502.08224 | §4.1 P5 |
| Lee et al., Eadro: An End-to-End Troubleshooting Framework for Microservices on Multi-source Data | ICSE 2023 | arXiv 2302.05092 | §4.2 P8 |
| Zhang et al., Robust Failure Diagnosis of Microservice System through Multimodal Data (DiagFusion) | IEEE TSC 2023 | arXiv 2302.10512 | §4.2 P8 |
| Ding et al., TraceDiag: Adaptive, Interpretable, and Efficient RCA on Large-Scale Microservice Systems | FSE 2023 Industry | arXiv 2310.18740 | §4.2 P9 |
| Li et al., Causal Inference-Based RCA for Online Service Systems with Intervention Recognition (CIRCA) | KDD 2022 | arXiv 2206.05871 | §4.2 |
| Lin et al., Root Cause Analysis In Microservice Using Neural Granger Causal Discovery (RUN) | AAAI 2024 | arXiv 2402.01140 | §4.2 |
| Pham et al., RCAEval: A Benchmark for RCA of Microservice Systems with Telemetry Data | WWW 2025 Companion | arXiv 2412.17015 | §4.2·§6 J3 |
| Liu et al., Incident-aware Duplicate Ticket Aggregation for Cloud Systems (iPACK) | ICSE 2023 | arXiv 2302.09520 | §4.4 P12 |
| Kuang et al., Knowledge-aware Alert Aggregation in Large-scale Cloud Systems: a Hybrid Approach (COLA) | ICSE-SEIP 2024 | arXiv 2403.06485 | §4.4 P12 |
| Ghosh et al., Dependency Aware Incident Linking in Large Cloud Systems (DiLink) | WWW 2024 Companion | arXiv 2403.18639 · DOI 10.1145/3589335.3648311 | §4.4 P12 |
| Jin et al., Assess and Summarize: Improve Outage Understanding with LLMs (Oasis) | FSE 2023 Industry | arXiv 2305.18084 | §4.4 |
| IPIGuard · Task Shield · Adaptive Attacks (Plan 78 §11.1 검증 완료 — 재인용) | EMNLP 2025 · ACL 2025 · NAACL Findings 2025 | (Plan 78 §11.1) | §4.5 P13 |
| Xu et al., OpenRCA: Can Large Language Models Locate the Root Cause of Software Failures? | ICLR 2025 | https://github.com/microsoft/OpenRCA | §4.3 |
| Jha et al., ITBench: Evaluating AI Agents across Diverse Real-World IT Automation Tasks | ICML 2025 (PMLR 267) | arXiv 2502.05352 | §4.3 P10 |
| Liu et al., OpsEval: A Comprehensive Benchmark Suite for Evaluating LLMs' Capability in IT Operations Domain | FSE 2025 Companion | arXiv 2310.07637 · DOI 10.1145/3696630.3728572 | §4.3 · §6 J3 |
| Chen et al., STRATUS: A Multi-agent System for Autonomous Reliability Engineering of Modern Clouds | NeurIPS 2025 | arXiv 2506.02009 | §4.3 P11 |
| Riddell et al., Stalled, Biased, and Confused: Uncovering Reasoning Failures in LLMs for Cloud-Based RCA | FORGE 2026 (ICSE 워크숍) | arXiv 2601.22208 · DOI 10.1145/3793655.3793732 | §4.1 P6·P15 |
| Mao et al., StepFly: Agentic Troubleshooting Guide Automation for Incident Diagnosis | Proc. ACM Softw. Eng. (FSE 2026) | arXiv 2510.10074 · DOI 10.1145/3808143 | §4.1 P5 |
| Yu et al., AlertGuardian: Intelligent Alert Life-Cycle Management for Large-scale Cloud Systems | ASE 2025 | arXiv 2601.14912 | §4.4 |
| Yu et al., MicroRank: End-to-End Latency Issue Localization with Extended Spectrum Analysis in Microservice Environments | WWW 2021 | DOI 10.1145/3442381.3449905 | §4.2 P16 |
| Li et al., Practical Root Cause Localization for Microservice Systems via Trace Analysis (TraceRCA) | IEEE/ACM IWQoS 2021 | DOI 10.1109/IWQOS52092.2021.9521340 | §4.2 P16 |
| Yu et al., Nezha: Interpretable Fine-Grained Root Causes Analysis for Microservices on Multi-modal Observability Data | FSE 2023 | DOI 10.1145/3611643.3616249 | §4.2 P16 · §5.5 |
| Jump & McKinley, Cork: Dynamic Memory Leak Detection for Garbage-Collected Languages | POPL 2007 | DOI 10.1145/1190215.1190224 | §4.6 · §5.4-b |

### 12.2 Preprint (보조 근거)

| 문헌 | arXiv | 반영 |
|---|---|---|
| An et al., Nissist: An Incident Mitigation Copilot based on Troubleshooting Guides | 2402.17531 | §4.1 P5 |
| Unnikrishnan et al., FixItFlow: Automated Troubleshooting Guide Generation from Cloud Incidents | 2607.13035 | §4.1 |
| Wei et al., Agentic Root Cause Analysis through Evidence-Grounded Reasoning | 2607.22385 | §4.1 P6 |
| Luo et al., OpsAgent: An Evolving Multi-agent System for Incident Management in Microservices | 2510.24145 | §4.1 |
| Zhang et al., mABC: multi-Agent Blockchain-Inspired Collaboration for RCA in micro-services architecture | 2404.12135 | §4.1 P7 |
| Qiu et al., Blueprint First, Model Second: A Framework for Deterministic LLM Workflow | 2508.02721 | §4.1 P7 |
| Wang et al., A Comprehensive Survey on Root Cause Analysis in (Micro) Services | 2408.00803 | §4.2 |
| Chen et al., AIOpsLab: A Holistic Framework to Evaluate AI Agents for Enabling Autonomous Clouds | 2501.06706 | §4.3 P10 |
| Clark et al., SREGym: A Live Benchmark for AI SRE Agents with High-Fidelity Failure Scenarios | 2605.07161 | §4.3 P10 |
| Shan et al., RCA Copilot: Transforming Network Data into Actionable Insights via LLMs | 2507.03224 | §4.1 (참고) |
| Coding Agents Are Guessing: Action-Boundary Violations in Underspecified DevOps Instructions (Plan 78 재인용) | 2607.02294 | §4.5 |
| GIRA: Guarded Tool-Using LLM Agents for Incident Response — Safety-Gated Architecture (워크숍 투고본 2026-03 · 저자·채택 미확인) | [OpenReview LBt5eX6OKx](https://openreview.net/forum?id=LBt5eX6OKx) | §4.5 · §6 J6 지표 |

> ※ v1이 §12.2에 실었던 *Multi-Agent LLM Orchestration … Incident Response*(arXiv 2511.15755)는 **저자 철회본**으로
> 확인되어 v1.1에서 삭제했다(§4.1 P7 부기).

### 12.3 제니퍼 1차 자료 (J-번호)

- [J-1] SQL로 제니퍼 데이터 조회하기 — 자체 파일 DB·API 서버·JDBC(제니퍼소프트 기술 블로그, 2021-06) · https://jennifersoft.com/ko/blog/tech/2021-06-02/
- [J-2] 제니퍼 성능 데이터 RDB에 적재해서 활용하기 — RDB Export(Oracle/MySQL/PostgreSQL·테이블 목록·`server_view.conf`) · https://jennifersoft.com/ko/blog/tech/2021-07-19/ · 영문 테이블 노출 예시 https://jennifersoft.com/en/blog/tech/2021-08-23/
- [J-3] JENNIFER 4.5 매뉴얼(PDF) — 이벤트 유형 11장 · PLC 6.7 · 서비스 덤프 6.8 · 강제 GC 9.11.4 · 액티브 서비스 제어 13장 · 리포지토리 12.12 · https://cdn.jennifersoft.com/wp-content/uploads/Documents/ko/JENNIFER4.5_Manual.pdf (4.x 기준 — 5.x 대조는 U-1·U-8)
- [J-4] Open API v1 스펙(`spec.json` — 엔드포인트·필드 전체) · https://github.com/jennifersoft/jennifer-developer-guide/blob/master/src/resources/spec.json · 개발자 가이드 https://jennifersoft.github.io/jennifer-developer-guide/
- [J-5] Open API v2 매뉴얼(Bearer·`auth-test`·`manage-rule-event*`·`manual-rdb-export`·`deploy`) · https://github.com/jennifersoft/jennifer5-open-api-v2-manual
- [J-6] 뷰 서버 어댑터 튜토리얼(TransactionHandler·EventHandler·EventData 필드) · https://github.com/jennifersoft/jennifer-view-adapter-tutorial/blob/master/README_ko.md
- [J-7] 지원 플랫폼(Java·.NET·PHP·Python·Node.js·OpenTelemetry) · https://jennifersoft.com/ko/product/apm/platforms/
- [J-8] Jennifer AI / Jennifer Insight(Anomaly Event·Metrics Correlation·Insight Chat = Open API tool 호출·폐쇄망 LLM) · https://jennifersoft.com/ko/blog/tech/2025-12-22-jenniferai-jenniferinsights/ · https://jennifersoft.com/ko/blog/tech/2025-02-10-jennifer-ai/
- [J-9] X-View·프로파일 소개(JENNIFER v5 소개서 PDF) · https://www.cywell-integration.com/files/02.WASJENNIFER_v5_.pdf
- [J-10] PLC 소개 · https://www.theteams.kr/teams/2747/post/68554
- [J-11] 제품 구성(Agent·Data Server·View Server·Repository) · https://www.fin-ncloud.com/marketplace/jennifer
- [J-12] MSA 모니터링(토폴로지·Call Chain·프로토콜) · https://jennifersoft.com/ko/blog/tech/2026-12-17-jennifer-msa-monitoring/
- [J-13] OpenTelemetry 연동 How-to(Collector → 제니퍼 트레이스) · https://jennifersoft.com/ko/blog/tech/otel-jennifer-howto/
- [J-14] PagerDuty 어댑터(레벨 매핑) · https://github.com/jennifersoft/jennifer-view-adapter-pagerduty
- [J-15] 제니퍼소프트 공개 리포지토리 목록(어댑터·플러그인·API 서버·JDBC) · https://github.com/orgs/jennifersoft/repositories · SNMP 어댑터 https://github.com/jennifersoft/jennifer-view-adapter-snmp · API 서버 https://github.com/jennifersoft/jennifer5-api-server · JDBC https://github.com/jennifersoft/jennifer5-jdbc-driver
- [J-16] 릴리즈 노트 5.6.5 · https://docs.jennifersoft.com/ko/jennifer5_releasenote/5_6_5

### 12.4 산업 자료·표준 (V·S·W-번호)

- [V-1] Dynatrace MCP 서버(GA) · https://docs.dynatrace.com/docs/dynatrace-intelligence/dynatrace-mcp
- [V-2] Datadog MCP 서버 · https://www.datadoghq.com/product/ai/mcp-server/
- [V-3] New Relic AI MCP 서버 · https://newrelic.com/blog/news/new-relic-ai-mcp-server-launch
- [V-4] WhaTap MCP 서버 · https://docs.whatap.io/mcp/how-to-use
- [V-5] Scouter Web API 가이드 · https://github.com/scouter-project/scouter/blob/master/scouter.document/tech/Web-API-Guide_kr.md
- [S-1] Google SRE Book — Managing Incidents · https://sre.google/sre-book/managing-incidents/
- [S-2] Google SRE Workbook — Incident Response · https://sre.google/workbook/incident-response/
- [S-3] Making Facebook Self-Healing (FBAR) · https://engineering.fb.com/2011/09/15/data-center-engineering/making-facebook-self-healing/
- [W-1] Apache Tomcat 9 — The Executor (thread pool) · https://tomcat.apache.org/tomcat-9.0-doc/config/executor.html
- [W-2] Apache Tomcat 9 — JDBC Connection Pool · https://tomcat.apache.org/tomcat-9.0-doc/jdbc-pool.html
- [W-3] HikariCP — About Pool Sizing · https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing
- [W-4] Oracle — HotSpot Virtual Machine Garbage Collection Tuning Guide · https://docs.oracle.com/en/java/javase/17/gctuning/
- [W-5] OpenTelemetry Semantic Conventions — JVM 런타임 메트릭 · https://opentelemetry.io/docs/specs/semconv/runtime/jvm-metrics/ · DB 클라이언트 메트릭 · https://opentelemetry.io/docs/specs/semconv/database/database-metrics/
- [W-6] Oracle WebLogic Server 12.2.1.3 — Avoiding and Managing Overload(Stuck Thread · `StuckThreadMaxTime` 기본 600초) · https://docs.oracle.com/middleware/12213/wls/CNFGD/overload.htm
- [W-7] TmaxSoft JEUS 9 웹 엔진 안내서 — 스레드 풀(`max-thread-active-time` · Blocked Thread 통지) · https://docs.tmaxsoft.com/ko/jeus/9/web-engine-guide/chapter-thread-pool.html — △ 조사 시 호스트 접근 불가(검색 스니펫 기반) · **운영 JEUS 버전 문서로 재확인 필요**
- [W-8] Oracle Java SE 21 Troubleshooting Guide — Troubleshoot Memory Leaks(OOM 메시지 7종 · `HeapDumpOnOutOfMemoryError` · `jcmd GC.heap_dump`) · https://docs.oracle.com/en/java/javase/21/troubleshoot/troubleshooting-memory-leaks.html · Troubleshoot Process Hangs and Loops(스레드 덤프·데드락) · https://docs.oracle.com/en/java/javase/21/troubleshoot/troubleshoot-process-hangs-loops.html
- [S-4] Introducing Nurse: Auto-Remediation at LinkedIn (2015) · https://engineering.linkedin.com/sre/introducing-nurse-auto-remediation-linkedin — △ 현재 404, 검색 스니펫으로 내용 확인
- [S-5] Automate Java performance troubleshooting with AI-powered thread dump analysis on Amazon ECS and EKS (AWS Containers Blog, 2025-12) · https://aws.amazon.com/blogs/containers/automate-java-performance-troubleshooting-with-ai-powered-thread-dump-analysis-on-amazon-ecs-and-eks/ — "알람 → 덤프 수집 → LLM 요약 → 보고서(자동 교정 없음)"의 산업 표준형
- HolmesGPT(CNCF Sandbox) · https://github.com/HolmesGPT/holmesgpt — `Toolset.approval_required_tools`(sre-agent/02 §9 실측 · 본 계획 미채택 근거 §4.5)

### 12.5 내부 참조

- `plans/55`(멀티소스 로드맵 · §5 C-1~C-5 · §7 선행 결정) · `plans/78` §3.3(조치 위험·통제 문헌)·§4.7.1~4.7.3(미들웨어 방식·APM 편입 경로·체크리스트)·W7·§7.1·§8.3 · `plans/64` §7~§8(L3 통제·조치 거버넌스) · `plans/sre-agent/02` §9(human-gated) · `docs/25_host_investigation_load_guard.md` · `config/middleware_signatures.yaml`(선언적 정책 파일 전례)
- 조사 보조 텍스트(스크래치패드, 저장소 밖 · 세션 종료 시 소멸): 4.5 매뉴얼 추출본 `j45.txt` · v5 소개서 추출본 `cywell.txt` ·
  문헌 조사 보고서 `literature_report.md`(63건 카드 · OpenAlex/S2 인용수 병기 · 미확인·정정 목록) · 저장소 실측 보고서 `repo_report.md`

---

## 13. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-09-03 | **v1** — 최초 작성. 제니퍼 기능·연동 지점 조사(§2, 출처 16건) · 저장소 실측(§3) · 문헌 33편 서지 실측(§4·§12) · 전제 정정(§0.2 — 파일 DB → RDB Export+API+이벤트 3중) · 아키텍처(§5) · Wave J0~J6(§6) · 게이트 G-1~G-7(§10) · D-195 예약 등재(작성 중 D-194가 `plans/50` v2.2로 본문 등재됨 — 번호 연속 확인) · 파일명 `-TODO` 접미사(INDEX 「파일명 상태 접미사」 규칙, 코드 0건) |
| 2026-09-03 | **v1.1** — 서브에이전트 보고 반영. ① **정정 2건**: RCACopilot 게재처 ICSE→**EuroSys 2024** · 철회본(arXiv 2511.15755) 인용 삭제 · DiLink 표기 정정 ② **폴스타 스키마의 제니퍼 연동 필드 발견**(`was_connection.jennifer_*` · `was_object.agent_id/hostname/obj_name`) → §3.3 실측 · §5.3 **1순위 브릿지** · U-10 · R-1 완화 ③ 벤치마크 정량 근거(OpenRCA 11.34% · ITBench 13.8%) → L3 범위 밖 ④ 문헌 추가(MicroRank·TraceRCA·Nezha·GIRA·Nurse·JDK 21 Troubleshooting·WebLogic·JEUS·Cork) → **P15~P17** 신설(반증·정체 가드 / 결정적 후보 축소 / 일반 완화 분리) · §4.6 WAS 시그니처 임계 구체화(stuck 600s · OOM 7종 · old gen 계단 상승) ⑤ 병렬 작업 주의(§3 — D-194 작업이 같은 파일 수정 중) · 착수 시 SDD 양식(§6) |
