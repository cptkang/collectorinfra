# 87. 제니퍼(JENNIFER) APM 연동 — 미들웨어(WAS) 장애 진단·대응·복구 범위 확대

> **작성일**: 2026-09-03 · **v2 갱신**: 2026-09-17(연동 방식 재조사 — §0.4) · **v2.1**: 2026-09-17(G-1·G-2 사용자 확정 · G-8 실측 판정 — §0.5)
> **성격**: 구현 계획(조사·설계 완료, 구현 전) · **상태: 계획(미구현) — G-1·G-2·G-8 확정(2026-09-17) · G-3~G-7·G-9 대기(§10)**
> **요청 취지(사용자 지시 원문)**: *"제니퍼라는 APM 솔루션을 연동하여 미들웨어(WAS) 모니터링을 연동하여
> 장애 진단과 대응을 지원하려고 한다. 연동방식은 폴스타와 동일하게 mcp DB를 연동하려고 한다. 필요한 경우
> 제니퍼와 api연동도 검토한다. 검색을 통해 제니퍼의 기능을 검토하여 sre agent에서 처리하는 장애 진단, 대응,
> 복구 처리를 위한 범위 확대 계획을 별도의 파일로 정리하라. 필요한 경우 관련 문헌, 논문 검토를 통해 장애
> 대응에 적절한 구현 방향을 계획에서 반영하라."*
> **v2 요청(사용자 지시 원문, 2026-09-17)**: *"87번의 제니퍼 연동을 위해서는 db가 아닌 제니퍼 api를 사용하거나
> 표준 연동 규격으로 연동해야 되는 것으로 알고 있다. 관련 자료를 조사하여 계획을 업데이트하라."*
> **⚠ 전제 정정(§0.2 · v2 재정정 §0.4)**: JENNIFER 5는 RDBMS 리포지토리가 아니라 **자체 파일 DB**를 쓴다. v1.1은
> 이를 뷰 서버 RDB Export(PostgreSQL 적재)로 우회해 **SQL(적재본) + REST(Open API) + 이벤트** 3중 연동을
> 권고했으나, **v2에서 SQL(적재본) 경로를 철회한다.** 제니퍼가 외부에 여는 연동 표면은 **Open API**이고(제니퍼
> 자체 AI와 공식 MCP 서버도 Open API 위에서 돈다[J-17]), 제니퍼가 내보내는 업계 표준 프로토콜은 **SNMP trap
> (이벤트)·Kafka(트랜잭션)·MCP**뿐이다(OTLP는 수신만, Prometheus/OpenMetrics는 양방향 ✖). → **조회는 Open API
> 단일 경로(`mcp_server` 경유) + 이벤트는 API 폴링(필요 시 어댑터 push)**으로 재구성한다.
> **v2.1 사용자 확정(원문, 2026-09-17)**: *"표준 연동 규격은 cncf openmatric에 정의된 규격을 말한다. api 위주로 가자.
> 3번은 실측하여 판정하라."* → **G-2 = CNCF OpenMetrics**(제니퍼는 OpenMetrics를 노출하지 않으므로 우리가 API로 받은
> 수치 지표를 **OpenMetrics 1.0으로 노출**하는 선택 트랙 J7 — §5.9) · **G-1 = API 위주(ⓐ)** · **G-8(공식 MCP) = 실측 판정
> 「미채택 — Open API 직접 호출 확정」**(§0.5 · 재검토 트리거 명시).
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
> JENNIFER 6은 2026-09 기준 공개 근거가 없어 다루지 않는다. **v2(2026-09-17)**: 최신 릴리즈는 **5.7.0(2026-08-13)**
> [J-18]이고, Open API 정본 스펙은 **5.6.4**(`openapi.jennifersoft.com`)[J-4]다. v2가 새로 인용한 제니퍼 자료 중 설계를
> 바꾸는 주장은 릴리즈 노트·설치 가이드 원문(`docs.jennifersoft.com`)과 스펙 원문으로 직접 확인했고, 조사 보고에만 근거한
> 항목은 △로 표기했다(사용자 매뉴얼·엔지니어 문서는 로그인 필요 — ✖).

---

## 0. 요약 — 이 계획이 실제로 푸는 문제

### 0.1 세 조각으로 갈라지는 요청

요청은 "제니퍼를 연동해 WAS 장애 진단·대응·복구를 지원한다" 한 줄이지만, 실측하면 **난이도와 선행 결정이
서로 다른 세 조각**이다.

| 조각 | 난이도 | 선행 결정 | 근거 |
|---|---|---|---|
| **① 데이터 소스 편입** — 제니퍼 데이터를 `mcp_server`의 세 번째 소스로 | 중 | 없음(D-119 ①이 이미 허용) | Prometheus 편입(D-119 R-B — **HTTP API 소스**)이 청사진. 폴스타 도구 8종의 반환 계약·서버측 결정적 조립·감사·Bearer가 전부 재사용된다(`mcp_server/mcp_server/polestar_tools.py:636-641`, `config.py:70-89`) |
| **② 진단 범위 확대** — `sre_agent`가 WAS 관점 증거(트랜잭션·힙/GC·풀·큐잉)를 읽고 판정·권고 | 중 | 없음 | `sre_agent`는 `mcp_server` 한 엔드포인트의 도구를 **자동 발견**한다(`interface/mcp_service.py:97-113`, RemoteMCPToolset) → 도구가 늘어도 sre_agent 배선 변경 0. 확장 지점은 지침·시그니처·권고 표·브리핑뿐 |
| **③ 대응·복구 범위 확대** — 권고를 넘어 조치를 실행 | **높음** | **D-003 예외 신설 + Plan 78 §8.3 5조건** | 현행 불변식: *"조치는 권고만(자동 실행 경로 없음 · `HUMAN_GATED_NOTE` 강제)"*(D-189 불변식 · `briefing_builder.py:36`). 자율성 3단계 진입은 5조건 **동시 충족**이 요구된다(Plan 78 §8.3). 본 계획은 그 조건을 **충족시키는 설계**를 제시하되, 착수는 **사용자 확정(G-6)** 뒤로 둔다 |

→ ①·②는 기존 결정 안에서 착수 가능하고, ③은 **새 결정이 필요한 유일한 조각**이다. 세 조각을 한 계획에
두는 이유는 ③의 안전 설계(검증 루프·롤백)가 ①·②의 데이터(조치 후 `apm_app_health` 재조회)에 의존하기
때문이다 — 순서를 갈라도 설계는 함께 봐야 한다.

### 0.2 전제 정정 — "폴스타와 동일한 MCP DB 연동"은 그대로 성립하지 않는다

| 사용자 전제 | 실측 | 판정 |
|---|---|---|
| 제니퍼도 폴스타처럼 리포지토리 RDBMS가 있어 SQL로 직접 읽는다 | ✔ JENNIFER 5는 성능 데이터를 **자체 파일 DB**에 저장한다 — 원문은 *"사용자가 원하는 가공 형태의 데이터를 조회하는데 어려움이 있습니다"* 로, SQL 조회 대안 둘(API 서버+JDBC · RDB Export)을 제시한다[J-1]. 4.x까지는 Derby/Oracle/DB2 리포지토리였으나[J-3] 5에서 파일 DB로 바뀌었다. ※ v1.1의 인용 *"직접 접근이 제한적"* 은 원문 문장이 아니어서 v2에서 정정 | **리포지토리 직접 SQL 불가** — API 서버+JDBC 드라이버로 SQL은 되나 `jennifer5-api-server` 최종 push **2023-02-07**(✔ GitHub API) · JDBC 드라이버 최종 릴리즈 v5.5.3.2(2021-03 △) · 5.6.2~5.7.0 릴리즈 노트 언급 0건(△) — **정체**[J-15] |
| 그래도 DB로 읽을 길은 있는가 | ✔ 뷰 서버 내장 **RDB Export**가 통계를 외부 RDB에 적재한다 — 2021년 Oracle 12 / MySQL 5.7 / PostgreSQL 9.x[J-2] · 이후 MariaDB·Tibero·SQL 통계·개별 ERROR(5.6.1 △) · 분 단위 애플리케이션 통계(5.6.3 ✔)[J-20] · **5.7.0에서도 PG/MSSQL 오류 수정**[J-18] — 지원·개선 중 | **적재본 SQL 경로는 기술적으로 성립** — 단 v2에서 **미채택**(§0.4) |
| 그 DB만으로 진단이 되는가 | ✖ 적재본에는 **액티브 서비스(현재 실행 중 트랜잭션·스택)·X-View 프로파일·이벤트·실시간 지표가 없다**. 이들은 Bearer 토큰 기반 **Open API**[J-4][J-5]가 유일한 경로다 | **진단에는 API가 필수** — v2에서는 추세·기준선까지 API(`/api/dbmetrics/*`)로 받아 **API 단일 경로**로 둔다 |
| 이벤트(알람)는 어떻게 받는가 | ✔ EVENT 룰의 "외부연동" + **EVENT 어댑터**(Java, `com.aries.extension`)[J-6] — 공식 **SNMP trap 어댑터** 포함[J-15] — 또는 Open API `/api/dbsearch/event` 폴링[J-4] | 폴스타 TCP JSON → `alarm:raw`와 **같은 스트림**으로 편입 가능 |

**되돌리는 비용**(사용자가 "DB만"을 고집할 경우): 얻는 것은 5분 이상 집계된 인스턴스·도메인 지표와 일 통계
뿐이다. 잃는 것은 액티브 스택·프로파일·이벤트 — 곧 **미들웨어 장애 진단의 핵심 증거**다. §2.4 비교표가
근거이며, 이 판정은 **G-1 게이트**로 사용자에게 되돌린다. (v2: 반대 방향 — SQL 경로를 **빼는** 비용은 §0.4.)

### 0.3 한 줄 권고

*(v2.1) **Open API(REST) 하나로** 실시간·프로파일·추세(`/api/dbmetrics/*`)를 받고, 이벤트는 **API 폴링**으로 받아
(어댑터 push는 폴링이 부족할 때만), **모두 `mcp_server` 하나의 `apm_*` 도구 표면 뒤에 숨긴다**(벤더 무지·D-119).
표준 연동 규격(**CNCF OpenMetrics**)은 제니퍼가 내지 않으므로, 받아 온 **수치 지표만** `mcp_server`가 OpenMetrics 1.0으로
다시 노출하는 선택 트랙(J7)으로 충족한다 — 이벤트·액티브 서비스·프로파일은 규격 밖이라 API가 정본이다. 같은 토큰이 쓰기·제어 API까지 여므로 `mcp_server`가 **GET·경로 허용목록을
코드로 강제**한다. `sre_agent`는 도구를 자동 발견하므로 **지침·시그니처·권고·브리핑만 WAS 관점으로 확장**한다.
대응·복구는 **L1(권고) → L2(승인 후 실행)** 로 한 단만 올리되, 실행기는 LLM 평면 밖의 **결정적 런북 실행기**로
두고 D-003 예외·승인 UX·blast radius·검증-롤백·적응형 공격 평가를 **G-6에서 사용자 확정 후** 착수한다.*
(v1.1 권고 *"RDB Export 적재본(SQL)으로 추세·통계를"* 은 v2에서 철회 — §0.4.)

### 0.4 v2 재판정 (2026-09-17) — "DB가 아닌 API 또는 표준 연동 규격"

**(1) 사용자 전제 판정 — 채택.** 벤더가 "DB 연동 금지"라고 적은 문장은 없지만(✖), 아래 근거로 **조회 경로를
Open API 단일로 재구성**한다.

| # | 근거 | 확인 |
|---|---|---|
| 1 | **제니퍼 자신이 외부 연동을 Open API 위에 올렸다** — 공식 MCP 서버: *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를 조회하므로…"*[J-17] · 제니퍼 AI: *"제니퍼 Open API 의 모니터링 데이터를 자동으로 분석"*(설치 가이드 11장 — △ 조사 보고 인용, 원문 미재확인) · Insight Chat도 Open API를 tool로 호출[J-8] | ✔ MCP 원문 / △ AI |
| 2 | Open API는 **OpenAPI 3.0.3 기계 판독 스펙**(`openapi.jennifersoft.com`, `info.version` 5.6.4, 52경로, `bearerAuth`)으로 공개된 계약이다[J-4]. 적재본 테이블은 컬럼 DDL이 공개돼 있지 않다(U-2 · 설정 상세는 비공개 엔지니어 문서) | ✔ 스펙 원문 |
| 3 | 진단 핵심 증거(액티브 서비스·X-View·이벤트·실시간)는 원래 API로만 나온다(§0.2) — SQL 경로를 둬도 **API 의존은 사라지지 않고 경로만 둘이 된다** | ✔ |
| 4 | 추세·기준선은 API `/api/dbmetrics/{domain,instance,business}`(`interval_minute`)로 대체된다[J-4] — 적재본이 유일하게 주던 가치가 줄어든다 | ✔ 스펙 · 보존 기간 U-3 |
| 5 | 적재본 경로는 **새 운영 부담**을 만든다 — 뷰 서버 `server_view.conf` 편집·재기동, 적재 DB 신설·버전 호환, 스키마 변경 추적. 벤더 API 경유는 스키마 진화를 벤더가 흡수하고 자격증명을 콘솔 권한 체계로 관리한다(산업 전례: SolarWinds SWIS *"allows SolarWinds to evolve the database schema while providing a consistent, backward-compatible object model"*[S-6]) | ✔ |
| 6 | **폴스타 제품 자신도** 제니퍼 연결을 URL+토큰으로 모델링한다 — `was_connection.jennifer_url`·`jennifer_token`·`jennifer_domain_id`가 Open API의 Bearer·`domain_id` 필수 인자와 정확히 대응(§3.3). 운영 사용 여부는 ✖(U-10) | △ 정황 |

**SQL 경로를 빼는 비용**: ① 적재본 위 **자유 SQL 집계·장기 보관 조인**(text2sql로 "지난 분기 WAS별 일평균 응답시간")을
잃는다 — `/api/dbmetrics/*`의 보존 기간(U-3) 안에서만 답한다. ② text2sql 질의 경로(J5)가 `backend: sql`로
**기존 SQL 파이프라인을 재사용하지 못하고** Plan 82 Wave 7의 그룹 실행자 훅(`backend: mcp` 변형)을 기다린다(§5.6).
③ 되돌리는 비용은 작다 — §5.2(a) `[[sources]]` 1항목 + `db_profiles` 편입으로 복원되며, 요구가 확인되면 **G-1 ⓑ**로 되살린다.

**(2) "표준 연동 규격" = CNCF OpenMetrics (v2.1 사용자 확정 · G-2)**

> v2는 후보를 넷(제니퍼 자체 규격 · 업계 표준 프로토콜 · 국내 공공·금융 표준 · 조직 내부 규격)으로 나눠 사용자에게
> 되물었고, 사용자가 **CNCF OpenMetrics**로 확정했다. 네 갈래 표는 §13 v2 이력으로 대체한다.

| # | 실측 | 설계 함의 |
|---|---|---|
| O-1 | **규격 상태**: OpenMetrics **1.0 = "Status: Published · November 2020"** · 2024년 CNCF Prometheus 프로젝트로 편입 · **2.0 = "Experimental"(RC — *"we reserve the right to break the compatibility"*)**[OM-1][OM-2] | **1.0 고정**(`application/openmetrics-text; version=1.0.0; charset=utf-8` · `# EOF` 필수). 2.0은 정식화 후 재검토 — `plans/92`와 같은 기준 |
| O-2 | **규격 범위**: *"This standard expresses all system states as numerical values … Contrary to metrics, singular events occur at a specific time."*[OM-1] | **수치 지표만 규격 대상.** 제니퍼 EVENT·액티브 서비스 목록·X-View 프로파일·트랜잭션 목록은 **규격 밖** → 진단 핵심 증거는 **API가 정본**(G-1 "API 위주"와 정합) |
| O-3 | **제니퍼는 OpenMetrics를 노출하지 않는다**: 정본 스펙(52경로)의 응답 형식은 `application/json`·`text/plain`(프로파일 텍스트)뿐이고 `prometheus`·`openmetrics`·`exposition`·`scrape`·`otlp`·`opentelemetry` **0건**(2026-09-17 스펙 원문 검색)[J-4] · 릴리즈 노트·공개 리포 언급 0건(△ 조사 보고) · 웹 검색 0건 | **"제니퍼를 OpenMetrics로 연동"은 제니퍼 측만으로는 불성립.** 규격 준수는 **우리 쪽 브리지**로만 가능 — API로 받은 인스턴스 지표를 `mcp_server`가 OpenMetrics 1.0으로 노출(**J7 · §5.9 · 선택 트랙**) |
| O-4 | **노출 규칙**: 단위가 있으면 이름 접미사 필수(*"it MUST be a suffix of the MetricFamily name"*) · counter는 `_total` · *"MetricPoint timestamps should not be exposed"*[OM-1] | 제니퍼 응답시간(ms)은 **초로 변환**해 `_seconds` · TPS는 비율이라 counter가 아니라 **gauge** · 타임스탬프 미노출(스크레이프 시각은 수집기 몫) |
| O-5 | `plans/92` 트랙 B-2가 **폴스타 → OpenMetrics 브리지**(`mcp_server` `custom_route`)를 이미 설계했다 · FastMCP 1.29.1 `custom_route(path, methods, name=None, include_in_schema=True)` 실측 | J7은 **같은 기계 재사용**(직렬화기·인증·부하 가드) — 제니퍼 전용 코드는 "API 응답 → MetricFamily" 매핑뿐 |

→ 이벤트 push 방식(SNMP trap 등)은 OpenMetrics와 무관하므로 v2가 붙였던 "표준" 라벨을 떼고, **API 폴링이 정본**,
어댑터 push는 폴링의 부하·지연이 실측으로 문제될 때만 착수한다(§5.5 · G-4).

### 0.5 G-8 실측 판정 (v2.1 · 2026-09-17) — 제니퍼 공식 MCP 서버: **미채택, Open API 직접 호출 확정**

**실측 한계(먼저 밝힌다)**: 공식 MCP의 **런타임 `tools/list`는 실측하지 못했다.** 프록시 설치본(`jennfer-llm-1.x.x.zip`)의
획득 경로가 설치 가이드에 없고(원문: *"프록시 서버: jennfer-llm-1.x.x.zip 파일이 필요"* 뿐)[J-17], `jennifersoft` GitHub
조직 공개 리포에 llm·mcp 리포 0건(GitHub API)·Docker Hub `jennifersoft` 이미지는 2018년 1건뿐이며, 벤더 공개 프록시
`insight.jennifersoft.com`은 `302 → /login`(인증 필요)이다. 로컬·폐쇄망 모두 제니퍼 서버 접근 설정이 없다(`.env` 제니퍼 키 0건).
그래서 **실측 가능한 1차 자료와 우리 측 실측**으로 판정하고, 미실측 항목이 **최선의 값이어도 판정이 뒤집히지 않는지**를 따로 봤다.

| # | 판정 기준 | 실측 | 결과 |
|---|---|---|---|
| M-1 | 설치·운영 전제 | 프록시 설정 `server.llm/conf/server_llm.conf`에 **LLM 공급자**(`llm_proxy_model_provider` = `private_solar`·`azure_openai`·`aws_bedrock`·`private_openai`)와 **자체 대화 DB**(`llm_proxy_db_filename`·`username`·`password`)를 설정하고 `bin/startup_llm.sh`로 띄운다 · 뷰 서버에 `llm_proxy_host`·`llm_proxy_uuid`·`llm_proxy_org` 등록 · **5.6.5+ · JDK 17+**[J-17] | ✖ — MCP 하나를 쓰려고 **LLM 프록시 제품 전체**(LLM 연결·대화 DB·뷰 서버 등록)를 운영해야 한다 |
| M-2 | 권한·자격증명 | MCP 클라이언트가 매 요청 헤더로 `X-Jennifer-Api-Url`·`X-Jennifer-Api-Token`(콘솔 발급 **같은 Open API 토큰**)을 보낸다 · *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를 조회하므로"*[J-17] | ✖ 이점 0 — 권한 경계가 Open API 직접 호출과 **동일**(같은 토큰·같은 권한). 허용목록은 어차피 우리가 걸어야 한다 |
| M-3 | 계약 가시성·v1 동결(R-12) 흡수 | MCP 도구도 Open API 위에서 동작[J-17] → v1 변경 시 **프록시 갱신**이 필요. 결합 대상이 **공개 OpenAPI 3.0.3 스펙**(52경로)에서 **비공개 도구 스키마**(공개 예시 4종: `metrics_list`·`transaction_list_by_application`·`sql_statistics`·`jennifer-mcp-id_get_domain_list` — 마지막은 Open WebUI 서버 id 접두로 보임 △)로 바뀐다 | ✖ — 벤더 흡수 이점보다 **계약 검증 가능성 상실**이 크다(recorded JSON 계약 테스트의 기준이 사라짐) |
| M-4 | 데이터 경계(D-120) | 프록시에는 **벤더 운영 공개 프록시 모드**가 있다 — *"제니퍼 AI 에서 운영 중인 공개 프록시 서버 (insight.jennifersoft.com)"* · *"공개 프록시 서버의 데이터는 … 최대 30 일간 보관"* · 기본은 *"폐쇄망 내부에 자체 설치된 거대 언어 모델(LLM)을 사용하도록 설계"*[J-22] | △ — 사내 설치형만 허용 가능하나, **설정 하나로 외부 전송 모드가 되는 구성요소**를 조회 경로에 넣는 것 자체가 통제 지점 추가 |
| M-5 | 데이터 범위 | 제니퍼 AI가 Open API에서 읽는 범위: *"도메인, 인스턴스 목록, 도메인, 인스턴스 별 성능 메트릭, 트랜잭션, 스택 트레이스, 프로파일 텍스트 (SQL 파라미터 제외), 에러, 이벤트"*[J-22] | △ — §5.2 8종 영역과 대체로 겹칠 **가능성**(도구 단위 확인 불가). hostname 앵커·상위 N 축약·마스킹은 어느 경우든 우리 몫 |
| M-6 | 우리 측 기술 호환 | `mcp` 1.29.1 `streamablehttp_client(url, headers=None, timeout=30, sse_read_timeout=300, …)` — **헤더 주입 가능**(inspect.signature 실측) | ✔ — ②는 기술적으로 가능하다(판정을 가르는 기준이 아님) |

**판정**: **② `mcp_server` 뒤 백엔드 = 미채택 · ① Open API 직접 호출 = 확정 · ③ `sre_agent` 직결 = 기각 유지**(§2.8).
M-5(도구 커버리지)가 최선(전 영역 커버·쓰기 도구 0·JSON 구조화 반환)이어도 **M-1(LLM 프록시 운영)·M-2(권한 이점 0)·
M-3(계약 가시성 상실)은 그대로**라 결론이 바뀌지 않는다. → §5.2(d)의 "백엔드 교체 자리"는 **만들지 않는다**(단일 구현에
추상화 금지) — 벤더 호출은 `apm_client.py`에 캡슐화하는 것으로 충분하다(R-9).

**재검토 트리거**(셋 **모두** 충족 시에만 재판정 — 그때 `tools/list`를 실측한다): ⓐ 운영에 **사내 설치형** 제니퍼 LLM 프록시가
**이미 가동 중**(M-1 비용 소멸) ⓑ 벤더가 Open API v1의 **제거 일정을 공지**하고 MCP 도구가 대체 경로로 제시됨(M-3 역전) ⓒ `tools/list`
실측에서 §5.2 8종 뒷단 커버 · 쓰기·제어 도구 0(또는 서버측 차단 가능) · 구조화(JSON) 반환 확인.

---

## 1. 요구 해석

### 1.1 요구 → 기능 매핑

| 요구 | 현행(폴스타 단일 소스) | 확대 후 | 절 |
|---|---|---|---|
| **진단** — WAS 장애의 원인 지목 | OS 근사(`ps`·`ss`·`top`·`journalctl`, Plan 78 W7-1 · `middleware_profile()` = `vm_profile()` 동일) | **APM 1차**(트랜잭션·힙/GC·스레드·풀·큐잉·슬로우 SQL·외부 호출) · OS 근사 2차 폴백 | §5.2·§5.4 |
| **대응** — 영향 완화(mitigation) 제안·실행 | 권고만(`remediation.recommend`, OS 시그니처 11종) | WAS 시그니처 8종 추가 + **제니퍼가 제공하는 완화 수단**(PLC 부하 제어·스레드 인터럽트·EVENT 룰) 카탈로그화 · **L2 승인 후 실행**(게이트) | §5.4·§5.7 |
| **복구** — 정상 상태 복원·검증 | 없음 | 조치 후 **검증 루프**(`apm_app_health` 재조회로 회복 판정) · 실패 시 롤백/에스컬레이션 · 사후 브리핑 | §5.7 |
| **연동 방식** (v1: MCP DB → **v2: API·표준 규격**) | 폴스타 DB(PG·DB2) | ~~RDB Export 적재 PG를 `mcp_server` `[[sources]]`로 등록~~ → **v2 미채택**(§0.4). 제니퍼 데이터는 DB 소스 없이 `mcp_server` **API 소스**로만 편입 | §0.4·§5.2 |
| **API 연동** (v1: 검토 → **v2: 정본**) | Prometheus HTTP(`promql_tools.py`) · 폴스타 프로세스 API(`polestar_tools.py:39` httpx) | 제니퍼 Open API 클라이언트(httpx 재사용) — 서버측 자격증명·타임아웃·감사·**GET·경로 허용목록** | §5.2·§8.1 |
| **이벤트(알람)** | 폴스타 TCP JSON → `alarm:raw` → 노이즈 게이트 → 조사 트리거 | 제니퍼 이벤트를 **같은 스트림**으로(**API 폴링 정본** · 어댑터 push는 선택 — v2.1) + `app_impact` 축 | §5.5 |
| **질의(pull)** | text2sql 폴스타 3 DB | `apm` 솔루션 축(`backend: rest` — v2, DB 등록 없음) → "OO WAS 응답시간 추이" 같은 질의 | §5.6 |

### 1.2 범위

**안**: `mcp_server` 소스·도구 확장, 엔티티 정합 파일, `sre_agent` 지침·시그니처·권고·브리핑 확장, 이벤트
편입(폴링·어댑터), 노이즈 게이트 `app_impact` 축, text2sql 등록, **L2 대응·복구의 설계와 게이트 정의**.

**밖(v2 추가)**: RDB Export 적재본 SQL 경로(§0.4 — G-1 ⓑ로만 복원) · 제니퍼 API 서버+JDBC 드라이버(정체) ·
Kafka 트랜잭션 Export 소비(원시 스트림 필요 근거 없음 — §2.4 D).

**밖**: 제니퍼 제품 도입·라이선스·에이전트 설치(전제 조건 — §1.3), DPM(DB 성능) 연동(Plan 55 M3의 DPM
축 — 별건), L3 완전 자율 조치(Plan 78 §8.3 — 본 계획도 2단계에서 멈추고 L2까지만 연다), 제니퍼 자체 AI
(Insight Chat)와의 통합(폐쇄망에서 외부 LLM 의존[J-8] — 미채택 근거 §2.7).

### 1.3 전제·가정 (명시)

1. **제니퍼 버전은 5.6.4 이상**이며 온프레미스 설치형이다(v2 정정 — 정본 스펙 기준 5.6.4 · 최신 5.7.0[J-18] ·
   공식 MCP는 5.6.5+[J-17]). 운영 버전은 J0에서 실측한다(U-12). ~~5.6.0.5+ 조건인 API 서버~~ — v2 미사용.
2. ~~RDB Export를 PostgreSQL로 활성화할 수 있다~~ — **v2 철회**(§0.4). 대신 **Open API 토큰을 AIOps 전용으로 발급**
   받는다 — 발급 위치 [설정 > JENNIFER 서버 > 인증토큰 발급][J-17] · **토큰별 사용량 제한**이 있다(*"사용량 제한을
   0으로 설정할 경우, 무제한 토큰으로 동작"*[J-20] — 제한 단위 ✖, U-5) · 뷰 서버에 Open API 비활성 비공식 옵션이
   있으므로(5.6.3[J-20]) 켜져 있는지 확인한다.
3. **읽기 전용 자격증명은 토큰 1종**이다. 제니퍼 토큰에 조회 전용 등급이 있는지 ✖ — **같은 스펙에 쓰기·제어 API가
   공존**한다(`POST /api-v2/manage/data-server/control` · `PUT /api-v2/manage/domain/put` · `PUT /api-v2/manage/instance/{domainId}/{instanceId}/domain-id`[J-4] ·
   도메인 단위 인스턴스 GC 요청 `/api-v2/manage/instance/<domain-id>/gc`(5.6.4.28)[J-19]). → §8.1 **GET·경로 허용목록을
   1차 통제로 격상**한다(토큰 권한은 협의되면 추가 방어). 폴스타 DB의 `was_connection.jennifer_token`을 읽어 재사용하지
   않는다(제니퍼 측 권한 통제·감사 우회 — §8.2).
4. **네트워크 도달성**: `mcp_server` 호스트 → 뷰 서버 Open API 포트(v2 — 적재 PG 경로 삭제). 이벤트 push 단계에서는
   뷰 서버 → `alarm_server` 수신 포트(TCP 또는 SNMP trap UDP 162 — G-4). 폐쇄망 내부이므로 egress 없음.
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
[J-12][J-13]이 5.x 마이너로 추가됐다. ~~OTel 메트릭/로그 수용·Prometheus exporter는 ✖~~ → **v2 정정**: OTel 메트릭
수용 **△**(5.7.0 *"stable `jvm.*` semantic convention 병행 인식"*·Go 런타임 메트릭 인식[J-18]) · 로그 수용 ✖ ·
**OTLP 내보내기 ✖ · Prometheus/OpenMetrics 양방향 ✖**. 5.6.5에 제니퍼 인사이트(서버 LLM·브라우저 LLM)[J-19],
5.7.0(2026-08-13)에 javax→jakarta(Jakarta EE 11) 전환[J-18]. **공식 MCP 서버 ✔**(LLM 프록시 겸용 — §2.8)[J-17].

### 2.2 진단 관점 데이터 카탈로그 — 무엇을 어느 경로로 얻는가

| 데이터 | 경로 | 지연 | 진단 용도 | 확인 |
|---|---|---|---|---|
| 인스턴스 실시간 지표: `activeService`·`tps`·`responseTime`·`concurrentUser`·`rejectRate`·`heapUsed/heapCommitted`·`procCPU/procMemory`·`activeServiceRangeCount0~3` | **API** `/api/realtime/instance` | 초 | 골든 시그널·큐잉·거절 | ✔[J-4] |
| 메트릭 시계열(도메인/인스턴스/비즈니스, `interval_minute`) | **API** `/api/dbmetrics/{domain\|instance\|business}` | 분 | 사건창 추세 | ✔[J-4] |
| 메트릭 카탈로그(domain·instance·application·business·sql·externalCall) | **API** `/api/metrics` | — | 도구 매핑 실측 | ✔[J-4] |
| ~~인스턴스·도메인 5분/1시간/1일 통계, 애플리케이션 일 통계, 트랜잭션(1분)~~ | ~~**SQL** RDB Export 적재본~~ → **v2 미채택**(추세·기준선은 위 `/api/dbmetrics/*`가 대체) | 5분+ | 추세·기준선·질의 응답 | ✔ 테이블명[J-2] / ✖ 컬럼 DDL |
| 액티브 서비스 목록(`status`·`elapseTime`·`runningMode`·`runningFullText`·`cpuTime`·`clientIp`·`threadHash`·`runningDataSourceName`·`sqls`·`fetches`) | **API** `/api/activeService/list` | 초 | **큐잉·락·슬로우 SQL·외부 호출 대기 지목**(핵심) | ✔[J-4] |
| X-View 트랜잭션 검색(`txid`·`guid`·시간창 **1분 제한**)·프로파일 텍스트·SQL/파라미터 | **API** `/api/transaction/*` | 초 | 개별 트랜잭션 원인 분해(method/SQL/external/fetch/network) | ✔[J-4] |
| 이벤트·에러 검색(`level`·`instance_id[]`·`error_type[]`) | **API** `/api/dbsearch/event`·`/error` | 초 | 사건창 선행 이벤트 | ✔[J-4] |
| 애플리케이션·SQL·외부 호출 통계(`max_row` 기본 1000, `sort_by_metrics`) | **API** `/api/status/{application\|sql\|external_call}` | 초 | 상위 N 느린 트랜잭션·SQL | ✔[J-4] |
| 스레드 덤프·서비스 덤프·강제 GC·스레드 인터럽트/일시정지 | 콘솔 기능(4.x 매뉴얼 6.8·9.11·13장). **Open API 노출 ✖** — v2 정정: **강제 GC는 도메인 단위 API ✔**(`/api-v2/manage/instance/<domain-id>/gc`, 5.6.4.28[J-19] · 정본 스펙 5.6.4엔 미수록). 덤프·인터럽트·PLC는 정본 스펙 경로 검색 0건(△ — U-8) | — | 대응(§2.5) | △ 5.x 콘솔 유지 여부 |
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
- **v2 보강**: SNMP 어댑터(`event.SNMPAdapter`, 5.2.3+)는 레벨별 trap OID(기본 셋 모두 `1.3.6.1.4.1.27767.1.1`)·
  community·대상 주소(기본 `127.0.0.1/162`)·**메시지 패턴(기본 time·domain·instance·level·name·value)** 을 설정한다
  — `txid`·`detailMessage`는 기본 패턴에 없다[J-15]. 뷰 서버에 **Kafka 트랜잭션 Export**가 내장됐다(5.6.2.7 · 트랜잭션만)[J-20].
  5.7.0부터 어댑터 패키지는 `extension_allowed_packages`에 등록해야 로드되고, javax→jakarta 전환의 기존 어댑터 영향은 ✖(U-9)[J-18].

### 2.4 연동 지점 비교와 판정

| 후보 | 데이터 | 장점 | 단점·리스크 | 확인 | **판정** |
|---|---|---|---|---|---|
| A. RDB Export → PostgreSQL을 `mcp_server` SQL 소스로 | 5분·1시간·1일 지표, 애플리케이션 일·분 통계, SQL 통계, 트랜잭션(1분) | 폴스타와 **동일 경로**(`SourceConfig`·asyncpg·`db_profiles`·`execute_sql` 게이트) · 제니퍼 서버 부하 격리 · 장기 보관·조인 자유 | 실시간 아님(5분+) · 액티브 서비스·프로파일·이벤트 **없음** · 컬럼 DDL ✖(비공개 엔지니어 문서) · 뷰 서버 설정·재기동 · 적재 DB 신설·운영 | ✔/✖ | ~~채택(J1)~~ → **v2 미채택(보류)** — 벤더 연동 표면이 아니라 사용자 가공용 적재본이다(§0.4). 장기 자유 집계 요구가 확인되면 G-1 ⓑ로 복원 |
| **B. Open API(REST, Bearer)** | 실시간 지표·액티브 서비스·이벤트/에러 검색·X-View·프로파일·SQL·통계·**메트릭 시계열(`/api/dbmetrics/*`)** | **가장 넓은 커버리지·실시간** · **OpenAPI 3.0.3 기계 판독 스펙**(`openapi.jennifersoft.com`)[J-4] · 제니퍼 자체 AI·공식 MCP도 같은 API를 쓴다[J-8][J-17] | `/api/transaction/time` 1분 창 · `max_row` 1000 · **토큰별 사용량 제한**[J-20] · 뷰 서버 부하 · **진단 조회가 전부 v1(`/api/*`)인데 v1은 *"not removed for compatibility, but are no longer maintained"*** [J-4](단 5.6.4에서도 v1 필드 추가 △ — 폐기 아님) · 같은 토큰으로 쓰기·제어 API 개방(§1.3-3) | ✔ | **채택(J1·J2 — v2 단일 조회 경로)** |
| C. JDBC 드라이버 + API 서버(Calcite SQL, 5.6.0.5+) | 파일 DB 일자별 테이블 SQL | 적재 없이 SQL | **별도 프로세스·계정** · 일자별 테이블 단위 · 조인·성능 한계 · 문서 희소 · **리포 2023-02 이후 정체**(§0.2) | ✔/△ | **미채택** — 정체된 비공식 SQL 우회로 |
| D. 트랜잭션 push(TransactionHandler 어댑터 · **Kafka 트랜잭션 Export**) | 실시간 트랜잭션 스트림 | 지연 최소 · v2: **Kafka Export가 뷰 서버에 내장**(5.6.2.7)[J-20] — Java 코드 0 | Kafka 브로커 신설·소비자 개발 · 업그레이드 호환 · ~~Kafka 공식 어댑터 ✖~~(v2 정정) | ✔ | **보류** — 고카디널리티 원시 스트림은 필요 근거가 없다(Plan 55 C-5). 이벤트만 E로 |
| **E. 이벤트 어댑터 → `alarm:raw`** — E-1 **SNMP trap 공식 어댑터** / E-2 **커스텀 EVENT 어댑터**(JSON TCP) | 이벤트(E-1: 메시지 패턴 필드 한정 · E-2: EventData 전 필드·txid) | 폴스타 알람과 **동일 구조로 노이즈 게이트 편입** · E-1은 **Java 코드 0·표준 프로토콜** · E-2는 기존 `tcp_receiver` 재사용 | 이벤트만 · "외부연동" 토글 · E-1: SNMP trap 수신기 신설·필드 빈약(API 보강 조회 필요) · E-2: Java 빌드·`extension_allowed_packages`·jakarta 호환 | ✔ | **채택(J4-2단계 · 방식은 G-4)** — 1단계는 B의 `/api/dbsearch/event` **폴링**(Java 코드 0) |
| F. **제니퍼 공식 MCP 서버**(LLM 프록시 `/mcp`) | Open API 위 벤더 정의 도구(예시 `metrics_list`·`transaction_list_by_application`·`sql_statistics`) | 벤더가 API 변화를 흡수 · 표준 프로토콜(MCP Streamable HTTP) — 우리 스택과 동일 | 도구 전체 목록·쓰기 도구 포함 여부·라이선스 ✖ · 5.6.5+·JDK 17 프록시 별도 설치 · **토큰을 클라이언트 헤더로 전달**(자격증명이 호출자에 있음) · hostname 앵커·마스킹·허용목록 없음 | ✔ 존재 / ✖ 상세 | ~~보류~~ → **v2.1 미채택**(G-8 실측 판정 · §0.5 — LLM 프록시 운영 전제·권한 이점 0·계약 가시성 상실) |

### 2.5 대응·복구 관점 — 제니퍼가 "할 수 있는 조작"과 "할 수 없는 것"

| 조작 | 제니퍼 제공 | 노출 경로 | 본 계획 위치 |
|---|---|---|---|
| **PLC 부하 제어**(동시 액티브 서비스 상한 · 거절 메시지/리다이렉트) | ✔ 에이전트 옵션 `set_limit_active_service`·`max_num_of_active_service`·`request_reject_type`[J-3][J-10] | 에이전트 설정(콘솔). API ✖ | L2 조치 후보 "유입 차단" — 실행 경로는 **콘솔 수동**(권고) 또는 설정 파일 반영(고위험) |
| 자동 서비스 덤프(`enable_dump_triggering`·`number_of_dump_trigger`) | ✔[J-3] | 에이전트 설정 | 진단 증거 채취 — **읽기성 조치**(L1에서 권고) |
| 스레드 인터럽트·우선순위 변경·일시정지·재시작(액티브 서비스 단위) | ✔ 4.x 콘솔 13장[J-3] | 콘솔. API ✖ · △ 5.x 유지 | L2 "특정 트랜잭션 중단" — **중위험**(단일 스레드 범위) |
| 강제 GC · VERBOSE:GC 토글 | ✔ 4.x 9.11.4[J-3] | 콘솔. v2 정정: **도메인 단위 GC 요청 API ✔**(`/api-v2/manage/instance/<domain-id>/gc`, 5.6.4.28[J-19]) | L2 "힙 압박 완화" — 중위험(STW 유발). **API는 도메인 전 인스턴스 대상이라 단일 인스턴스 원칙(§5.7 ③) 위반 → L2 카탈로그 비채택 · `mcp_server` 허용목록 밖(§8.1)** |
| EVENT 룰 on/off · 임계 변경 | ✔ Open API v2 `manage-rule-event*`[J-5] | **API ✔** | **관측 설정 변경**은 조치가 아니라 통제 대상(§8.2 — 조회 토큰으로 막을 것) |
| **인스턴스 재기동·배포 롤백·L4 트래픽 배제** | ✖ 제니퍼 기능 아님 | 외부 도구(WAS 관리 콘솔·스크립트·LB) | L2 고위험 조치 — 실행기는 제니퍼가 아니라 **별도 실행 경계**(§5.7) |

→ **제니퍼는 관측·차단(PLC)·스레드 제어까지**이고, 복구의 대표 조치(재기동·롤백)는 제니퍼 밖에 있다. 대응·
복구 설계(§5.7)는 그래서 "제니퍼 API로 조치"가 아니라 **"제니퍼 데이터로 판정·검증하고, 실행은 별도 경계"**
구조가 된다.

### 2.6 미확인 항목 → J0 실측 체크리스트

| # | 항목 | 실측 방법 | 좌우하는 설계 |
|---|---|---|---|
| U-1 | 5.6.x 이벤트 유형 정식 명칭·EVENT 룰 목록 | `/api-v2/manage-rule-event` · 콘솔 [관리 > EVENT 룰] | §5.5 심각도·시그니처 매핑 파일 |
| U-2 | ~~RDB Export 테이블 컬럼 DDL·최신 PG 호환~~ → **v2**: `/api/dbmetrics/{domain,instance,business}` **응답 필드 전수·`interval_minute` 허용값·1회 조회 창 상한** | 테스트 토큰으로 호출 → recorded JSON(`testdata/jennifer/`) | §5.2 구간 경로 조립 · 시그니처 임계 표본 |
| U-3 | 5.x 데이터 보존 기본값(파일 DB — v2: 적재본 삭제) | 뷰 서버 설정 · `/api/dbmetrics/*` 최소 조회 가능 일자 | 조회 창 상한 · 인시던트 스코프 · **§0.4 "SQL 경로를 빼는 비용" ①의 크기** |
| U-4 | 인스턴스 명명 규칙 ↔ 폴스타 `hostname` 일치율 | `/api/instance` 전수 ↔ `cmm_resource` 대조 스크립트 | §5.3 매핑 파일 필요 여부(**최대 리스크 R-1**) |
| U-5 | Open API 토큰의 권한 등급(조회 전용 발급 가능?) · **v2**: 토큰별 **사용량 제한의 단위·초과 시 응답** · Open API 비활성 비공식 옵션 설정 여부 | 콘솔 **[설정 > JENNIFER 서버 > 인증토큰 발급]**(5.6.2+ · v1의 [관리 > 인증 토큰]은 구 메뉴) · v2 쓰기 API 호출 시도(**테스트 환경만**) | §8.1 허용목록(조회 전용 불가여도 1차 통제로 성립) · §8.4 레이트 리밋 |
| U-6 | 액티브 서비스·프로파일 응답의 **PII 포함 여부**(URL 파라미터·SQL 바인드 값·clientIp) | 샘플 응답 수집 → `pii_probe.py` | §8.3 마스킹 규칙 |
| U-7 | 뷰 서버 Open API 호출 부하 상한(동시·초당) | 제니퍼 운영 조직 확인 | 도구 타임아웃·레이트 리밋 |
| U-8 | 스레드 덤프·PLC 조작의 5.x API 노출 여부 (v2: 강제 GC는 도메인 단위 API 존재 — [J-19]) | 정본 스펙(`openapi.jennifersoft.com`) + v2 GitHub 매뉴얼(`active-service/detail` 등 스펙 미수록분 △) 전수 · 벤더 문의 | §5.7 조치 카탈로그의 실행 경로 |
| U-9 | ~~범용 webhook 내장 여부 · SNMP 어댑터 재사용 가능성~~ → **v2**: SNMP 어댑터 존재 ✔ — 운영 뷰 서버의 **어댑터 배포 정책**(`extension_allowed_packages` 등록 주체) · **SNMP 메시지 패턴에 instanceId·errorType를 넣을 수 있는가** · 5.7.0 jakarta 전환 후 커스텀 어댑터 빌드 호환 | 콘솔 어댑터 관리 화면 · 테스트 뷰 서버에 SNMP 어댑터 등록 → trap 수신 캡처 | §5.5 2단계 전송 방식(G-4) |
| **U-10** | **폴스타 `was_object`·`was_connection`의 운영 3종 DB 실재 여부** — 샌드박스 DDL에 `agent_id`·`hostname`·`obj_name`과 `jennifer_url`·`jennifer_token`·`jennifer_domain_id`·`jennifer_version`이 있다(§3.3) | `information_schema.tables`·행 수·`agent_id` 채움률 대조 · **v2**: `was_connection.connection_type` 값 분포(JDBC형 vs 제니퍼 URL·토큰형)·`jennifer_version` · `was_object.agent_id` ↔ `/api/instance` `instanceId` 표본 대조 | **§5.3 정합 1순위 브릿지**(R-1 완화) · 폴스타가 이미 제니퍼와 연동돼 있다면 자격증명 보관 주체 재검토(§8.2). ⚠ `was_object`의 `obj_hash`·`obj_name`·`obj_type`·`wakeup`은 오픈소스 APM Scouter `ObjectPack`과 같은 구조라 **폴스타 자체 WAS 에이전트 모델일 수 있다**(△) — `agent_id`를 제니퍼 인스턴스 ID로 **대조 전 단정 금지** |
| **U-11** | **제니퍼 공식 MCP 서버** — **v2.1: 판정 완료(§0.5 미채택)로 J0 필수 항목에서 제외.** 남는 것은 재검토 트리거 ⓐ 확인뿐: 운영에 사내 설치형 `jennifer-llm` 프록시가 가동 중인가 | 제니퍼 운영 조직 확인(뷰 서버 `llm_proxy_host` 설정 유무) · 트리거 ⓐ·ⓑ 성립 시에만 `tools/list` 실측 | §0.5 재검토 트리거 |
| **U-12** | 운영 제니퍼 **버전**(정본 스펙 5.6.4 / 최신 5.7.0) · §5.2 도구가 쓰는 **v1 엔드포인트 전건의 실 응답** | `/api/instance` 등 호출 · recorded JSON 채집 | R-12(v1 유지보수 중단) 영향 · 계약 테스트 픽스처 |

### 2.7 유사 벤더의 AI·MCP 동향 (참고)

Dynatrace(공식 원격 MCP 서버 GA 2026-01 · Davis CoPilot)[V-1] · Datadog(MCP 서버 GA · Bits AI SRE — 알림
자동 조사)[V-2] · New Relic(AI MCP 서버 public preview 2025-11)[V-3] · **WhaTap**(국내, 공식 MCP 서버 10개 도구 —
APM 이상탐지·토폴로지·PromQL)[V-4] · Scouter(오픈소스, Web API v1 · 공식 MCP ✖)[V-5]. ~~제니퍼소프트 공식 MCP
서버는 ✖~~ → **v2 정정: 공식 MCP 서버 ✔** — 제니퍼 LLM 프록시가 MCP 서버를 겸한다(설치 가이드 10장 · §2.8)[J-17].
Insight Chat(2025-10)이 Open API를 tool로 쓰는 자체 에이전트다[J-8]. 공통 패턴은 *"벤더
API를 MCP 도구로 감싸고, 조사는 read-only, 조치는 승인 뒤"* 로 본 계획의 구조와 같다. 제니퍼 자체 AI를
채택하지 않는 이유: ① 외부 LLM 의존(폐쇄망용 브라우저 LLM은 개발 중[J-8] — v2: 5.6.5에서 서버 LLM·브라우저 LLM 출시[J-19]) ② 조사 두뇌를 둘로 쪼개면
Plan 64 §0 중복 금지·D-118 경계 위반 ③ 제니퍼 데이터만 보므로 인프라↔앱 교차 상관(Plan 55 §6)이 불가.
※ ①이 약해져도 ②·③은 그대로라 **제니퍼 AI(조사 두뇌) 미채택은 유지**한다. 공식 **MCP 서버(데이터 표면)** 는 별개
문제로 §2.8에서 판정한다.

### 2.8 제니퍼 공식 MCP 서버 — 어디에 붙일 수 있는가 (v2 신규 · **G-8**)

**확인된 사실**[J-17]: *"제니퍼 LLM 프록시 서버는 MCP(Model Context Protocol) 서버 역할을 동시에 수행합니다"* ·
MCP 서버 타입 (Streamable) HTTP · URL `<llm-proxy-server:port>/mcp` · *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를
조회하므로"* 헤더 `X-Jennifer-Api-Url`·`X-Jennifer-Api-Token` 필수 · 도구 예시 `metrics_list`·`transaction_list_by_application`·
`sql_statistics` · 제니퍼 인사이트 5.6.5+ · 프록시 JDK 17+ · 설치 파일 `jennifer-llm-1.x.x.zip`. 릴리즈 노트에는 MCP
언급이 없고 도구 전체 목록은 공개 자료에 없다(✖ → U-11).

| 안 | 구조 | 판정 |
|---|---|---|
| ① `mcp_server`가 Open API **직접** 호출(v1.1 설계) | `mcp_server` → Open API | **확정(v2.1 · §0.5)** — 필요한 엔드포인트가 정본 스펙으로 확인됐고, 허용목록·hostname 앵커·마스킹·감사·상위 N 축약을 우리가 쥔다 |
| ② `mcp_server`가 제니퍼 MCP의 **클라이언트**가 되어 `apm_*`로 재노출 | `mcp_server` → 제니퍼 MCP → Open API | **미채택(v2.1 실측 판정 · §0.5 — M-1·M-2·M-3)** — 이점: 벤더가 v1 동결·API 변화를 흡수(R-12 완화). 비용: hop 1개·프록시 가용성 의존·벤더 도구 스키마 변경 추적. **전환 조건**: 도구 목록이 §5.2 표의 8종 뒷단을 덮고 · 쓰기 도구가 없거나 서버측에서 거를 수 있고 · 라이선스가 기존 계약 안일 것 |
| ③ `sre_agent`가 제니퍼 MCP에 **직결**(두 번째 MCP 서버) | `sre_agent` → 제니퍼 MCP | **기각** — (a) D-119 ① 관측 읽기 경계 일원화 위반 (b) **토큰이 클라이언트 헤더로 전달**되므로 조사 프로세스가 제니퍼 자격증명을 보유(D-119 ④ 서버측 자격증명 위반) (c) hostname 앵커·마스킹·허용목록·감사가 없다 (d) 도구 수·이름이 P4(8종 상한·질문형) 통제 밖 |

②를 재검토 트리거(§0.5)로 다시 열더라도 `sre_agent`·본체가 보는 표면은 `apm_*` 그대로다(`apm_client.py` 내부 교체).

---

## 3. 현행 실측 — 어디에 꽂히는가

> ⚠ **병렬 작업 주의**: 같은 날 `plans/50` v2.2(D-197 · 원 D-194) 작업이 `mcp_server/mcp_server/config.py` ·
> `sre_agent/sre_agent/interface/mcp_service.py` · `sre_agent/sre_agent/application/investigation_guidance.py`를
> **동시에 수정 중**임을 실측했다(디스크 변경 감지). 아래 `file:line`은 작성 시점 값이며, **각 Wave 착수 직전에
> 재실측**한다. 특히 `investigation_guidance.py`(§3.2 지침)는 D-197(원 D-194)이 만든 파일이라 §5.4-a의 확장 지점이
> 이동할 수 있다.

### 3.1 `mcp_server` — 관측 데이터 읽기 경계 (D-119)

- **소스 선언**: `config.toml` `[[sources]]`(name·type `postgresql|db2`·readonly·query_timeout·max_rows·풀 크기) +
  `.env`의 `{NAME}_CONNECTION`(`mcp_server/mcp_server/config.py:70-79`, `config.toml:46-82`). ~~적재 PG는 항목 하나
  추가로 끝난다~~ → **v2: 제니퍼는 `[[sources]]`(DB)를 쓰지 않는다.** API 소스는 `PrometheusConfig` 전례(`config.py`의
  HTTP 소스 설정 객체)를 따른다.
- **도구 등록**: `server.py:123-135` — `register_tools`(SQL 일반·`list_sources`) · `register_polestar_tools`(옵션
  `expose_polestar_tools`) · `register_promql_tools(mcp, expose_raw_promql)`. 신규 `register_apm_tools(mcp, cfg)`가
  같은 자리에 선다.
- **반환 계약**: `{rows, row_count, queried_at, source_kind, source, engine}` / 오류 `{error}`
  (`polestar_tools.py:636-641`, D-122). `source_kind`는 `polestar_db`·`polestar_process_realtime`·`prometheus`가
  있다 — ~~`apm_db`·~~`apm_api`를 추가한다(v2: SQL 경로 철회로 `apm_db` 삭제).
- **HTTP 클라이언트**: `httpx`가 이미 쓰인다(`promql_tools.py:35`·`polestar_tools.py:39`). ⚠ `mcp_server/pyproject.toml`
  에는 미선언(루트 venv 공유로 동작 — 부채. 본 계획에서 선언 1줄 추가).
- **게이트**: `execute_sql` 기본 비노출(`expose_execute_sql=False`)·폴스타 도메인 deny·Bearer(D-125). ~~적재 PG에도
  같은 게이트가 자동 적용된다~~ → **v2**: SQL 게이트는 제니퍼와 무관해지고, 대신 **API 경로 허용목록**(§8.1)을 신설한다
  — `promql_tools`의 고수준/원시 분리(`expose_raw_promql`)가 전례다.

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
- **v2 재실측(2026-09-17 · 같은 DDL)**: `was_connection`에는 제니퍼 필드 옆에 **`connection_type`·`jdbc_url`·`jdbc_driver`·
  `db_user_name`·`db_user_pwd`·`db_sql`** 이 함께 있다 — 폴스타가 WAS 연결을 **JDBC형(DB 직결)과 제니퍼 URL·토큰형(API)
  둘 다 담는 테이블**로 모델링했다는 뜻이다(△). 제니퍼 5의 리포지토리가 파일 DB임을 감안하면 JDBC형은 4.x 리포지토리나
  타 APM용으로 보이며, **제니퍼 5 연결은 URL+토큰+`domain_id`(= Open API Bearer·`domain_id` 필수 인자)** 형태다 — 사용자
  전제("DB가 아니라 API")와 부합하는 정황이다. 샌드박스 값은 합성(`testdata/pg/init/05_insert_dummy_data.sql:2999` — `'connection_type_1'`·`'jennifer_token_1'` 류)이라
  운영 사용 여부는 ✖(U-10).
- `config/db_registry.yaml:59-64`의 `apm` 주석 자리는 원래부터 **`backend: rest`** 였다 — v1.1 §5.6이 이를 `backend: sql`로
  바꾸자고 했던 것을 v2에서 되돌린다. 단 `backend` 값은 `src/routing/registry.py:77`·`execution_groups.py:77`에서 **실어
  나르기만 하고 `rest`를 실행하는 코드는 0건**이다(grep 실측) — Plan 82 Wave 7 「`backend: rest` 그룹 실행자 훅」 소관.

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
| G5 | text2sql에 `apm` 솔루션이 미등록 (v2: `jennifer_export` DB 등록은 철회) · `backend: rest` 실행자 0건(Plan 82 Wave 7) | §5.6 |
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
 [제니퍼 View Server]  (v2: RDB Export 적재 경로 없음 · v2.1: 공식 MCP 서버 미채택 — §0.5)
   Open API /api/* · /api-v2/* (Bearer · 정본 스펙 5.6.4)
   (선택) EVENT 어댑터(SNMP trap | 커스텀 JSON) ──push──► alarm_server (§5.5)
        │  REST(httpx · GET·경로 허용목록)
        ▼
 ┌──────────────────────────── mcp_server (관측 읽기 경계 · D-119) ─────────────────────────────┐
 │  sources: polestar_*(PG/DB2)                            JenniferApiConfig(url·token·timeout·limits) │
 │  도구: polestar_* 8 · prom_* 7 · **apm_* ≤8** (벤더 중립 표면 · source_kind apm_api)                 │
 │  서버측: 인스턴스↔hostname 정합(config/apm_instance_map.yaml) · 상위 N 축약 · 마스킹 · 감사 · 허용목록 │
 │  (J7 선택) 표준 노출: GET /metrics/apm — OpenMetrics 1.0 (수치 지표만) ──► Prometheus·타 수집기      │
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

**(a) ~~SQL 소스 `jennifer_export`~~ — v2 미채택(§0.4).** 복원 절차(G-1 ⓑ 선택 시): `config.toml` `[[sources]]` 1항목 +
`.env` `JENNIFER_EXPORT_CONNECTION`(`type=postgresql`·`readonly=true`) + `db_profiles/jennifer_export.yaml`(U-2 DDL 실측 후).
기존 `list_sources`·`execute_sql` 게이트가 자동 적용되므로 **복원 비용은 설정 추가 수준**이다.

**(b) API 설정 `JenniferApiConfig`** — `PrometheusConfig` 동형(`url`·`auth_token`(Bearer)·`query_timeout`·
`max_rows`·`rate_limit_per_sec`·`expose_raw_api=False`). 토큰은 `.env`에만(`JENNIFER_API_TOKEN`), 로그 마스킹.
**v2 추가**: `allowed_paths`(GET 허용 경로 목록 — **코드 상수 기본값**, 설정으로 넓히지 않고 좁히기만 가능) ·
`api_version_expect`(기동 시 1회 버전 확인 결과를 기동 로그에 — R-12).

**(c) 도구 표면 — 벤더 중립 `apm_*`(Plan 78 §4.7.2 4종 + 진단 필수 4종 = 8종 상한)**

| 도구 | 인자(값만) | 뒷단 | 반환 핵심 필드(OTel 컨벤션[W-5]) | 용도 |
|---|---|---|---|---|
| `apm_app_health` | `hostname`(또는 `instance`), `reference_time?`, `lookback_minutes?` | API `/api/realtime/instance`(현재) · API `/api/dbmetrics/instance`(구간 — v2, 구 SQL `INSTANCE_METRIC_5MIN`) | `http.server.request.duration`(p50/p95) · `tps` · `error_rate` · `active_services` · `reject_rate` | 골든 시그널 |
| `apm_runtime_health` | 동상 | API 실시간 · API `/api/dbmetrics/instance`(구간) | `jvm.memory.used/committed` · `jvm.gc.duration`(△ 메트릭 존재 시) · `process.cpu.utilization` | 힙/GC/CPU |
| `apm_resource_pool` | `hostname` | API 액티브 서비스 집계 · API 실시간 인스턴스(△ 인스턴스별 Active DB Connection 필드 — U-12) | `db.client.connection.count{state}` · `thread_pool.active/max` | 풀 고갈 |
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
- **원시 도구**는 옵트인: ~~SQL은 기존 `execute_sql` 게이트(소스 `jennifer_export` 허용 시)~~(v2 삭제), REST는 `apm_raw_api`
  (`expose_raw_api=True`일 때만 · GET 화이트리스트 경로만 — `/api-v2/manage/*`·`/restapi/user*`는 원시 도구로도 불가).
- **반환 계약**: `{rows|data, row_count, queried_at, source_kind: "apm_api", source: "jennifer",
  window?, instance_resolution: {matched, confidence, reason}}`(v2: `apm_db` 삭제). 미매칭 시 **빈 결과가 아니라 `{error:
  "instance_unresolved", reason}`**(침묵 폴백 금지).

**(d) 벤더 매핑 계층(v2.1 개정 · G-8 확정)** — `apm_tools.py`(도구 표면·계약·정합·축약·마스킹)와 `apm_client.py`(Open API
호출·허용목록·레이트 리밋)를 나눈다. ~~후자를 백엔드 인터페이스로 두고 `JenniferMcpBackend` 자리를 남긴다~~ — **v2.1 삭제**:
공식 MCP 미채택(§0.5)이므로 구현이 하나뿐인 추상화를 만들지 않는다. 계약 테스트는 recorded JSON → `apm_*` 반환으로 쓰고,
§0.5 재검토 트리거가 성립하면 그때 `apm_client.py` 내부를 교체한다(표면·계약 테스트는 그대로 회귀 기준).

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
| `was_heap_pressure` | `jvm.memory.used/committed ≥ 0.9` 지속 ≥ k 샘플 · `JVM_HEAP_MEM_HIGH`·`OUTOFMEMORY`(메시지 7종 분기[W-8]) · GC 후 old gen 하한 **계단식 상승**(누수 시그니처 — ~~5분 통계 적재본~~ v2: `/api/dbmetrics/instance` 5분 간격으로 판정) | CRITICAL(OOM)/WARNING | strong/medium |
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

**2단계 — 어댑터 push (선택 · v2.1: API 위주 확정으로 착수 조건부)**. 1단계 폴링의 지연·뷰 서버 부하·토큰 사용량이 **실측으로 문제될 때만** 착수한다. 둘 다 지연 초 단위 · 폴링 부하 0 · 선행은 제니퍼 운영 조직의
어댑터 배포 정책(U-9 — 5.7.0부터 `extension_allowed_packages` 등록 필요[J-18]).

| 방식 | 구성 | 장점 | 단점 |
|---|---|---|---|
| **2-A SNMP trap(공식 어댑터)** | 공식 `event.SNMPAdapter`[J-15]를 **설정만으로** 등록(Java 코드 0) → 신규 `noise_gate/alarm_server/snmp_trap_receiver.py`(`BaseReceiver` 상속 · UDP) → 정규화 → `alarm:raw` | **벤더 공식 어댑터 · Java 코드 0 · 빌드·jakarta 호환 부담 0** (※ 사용자가 정한 표준 연동 규격은 OpenMetrics라 SNMP의 "표준" 여부는 선택 근거가 아니다 — v2.1) | 메시지 패턴 필드 한정(기본 time·domain·instance·level·name·value — `txid`·`detailMessage` 없음) → 트리거 전 `apm_events`로 **보강 조회 1회** · SNMP 라이브러리 신규 의존(`pyproject` extra) · community 문자열 = 평문 인증(v2c 추정 △ — 수신 포트를 뷰 서버 IP로 제한) |
| **2-B 커스텀 EVENT 어댑터** | 공식 `jennifer-view-extension-tutorial`[J-6] 골격으로 EVENT 어댑터(Java/Kotlin) → 기존 `tcp_receiver.py`로 JSON 1행 | EventData **전 필드**(`txid`·`errorType`·`instanceId` 포함) · 수신 측 코드 재사용 | Java 빌드·배포·**업그레이드마다 호환 확인**(5.7.0 javax→jakarta) · 벤더 규격 밖 산출물을 우리가 유지 |

권고(v2.1): 착수하게 되면 운영 조직이 커스텀 어댑터 배포를 거부하면 **2-A**, 아니면 필드 완전성 때문에 **2-B** — 어느 쪽이든
1단계(폴링)가 먼저 돌며, 2단계는 폴링을 **대체**한다(중복 발행 금지 — 커서·`eventId` 멱등). 이벤트는 OpenMetrics 범위 밖이다
(*"Contrary to metrics, singular events occur at a specific time"*[OM-1]) — 이벤트를 게이지로 바꿔 표준 노출에 태우지 않는다.

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

- **v2 재설계** — DB 등록 없이 솔루션 축만 연다.
  `db_registry.yaml`: `solutions`의 `apm` 주석을 **해제·수정** — ~~`backend: sql`(적재본)~~ → **`backend: mcp`** ·
  `family: jennifer` · `capabilities: [was_metric, jvm_heap, thread_pool, transaction, apm_event]` · `requires: [host_location]`.
  `families`에 `jennifer`(product_terms `["제니퍼","jennifer","APM"]`). ~~`databases`에 `jennifer_export`~~ — 등록하지 않는다.
- **`rest`가 아니라 `mcp`인 이유**: 본체가 제니퍼 REST를 직접 부르면 토큰·허용목록·마스킹이 `src/`에도 생긴다(D-119 경계
  이중화). 그룹 실행자는 `mcp_server`의 `apm_*` 도구를 호출해 결과를 받는다 — 자격증명·통제는 `mcp_server` 한 곳.
  레지스트리 주석의 원래 값 `rest`는 J5 착수 시 이 근거로 `mcp`로 고친다(Plan 82 Wave 7 「`backend: rest` 그룹 실행자 훅」의
  **`mcp` 변형** — 그쪽 착수 시 접점 통보).
- ~~`config/db_profiles/jennifer_export.yaml`·`knowledge/jennifer_export/`·`synonym_seeds/jennifer_export.yaml` 신설 · 일자별
  `TRANSACTION_{domain}_YYYYMMDD` 결정적 조립~~ — **v2 삭제**(SQL 경로 철회). 질의 → 도구 인자(hostname·구간·지표)
  매핑은 **결정적 조립**으로 두고 LLM이 벤더 경로·필드명을 만들지 않는다(Known Mistakes "고정 스키마는 코드가 조립" 준용).
- **v2 한계(§0.4 비용 ①)**: 자유 SQL 집계("분기 WAS별 일평균")는 답하지 못한다 — `/api/dbmetrics/*` 보존 기간·
  `interval_minute` 안의 **지표 조회형 질의만** 받고, 밖은 부분 응답 + 사유(Plan 82 Wave 7 「미제공 능력의 부분 응답」).
- ※ `plans/90` §9(A3)가 우려한 "`jennifer_export`가 db_id로 `previous_db_ids`에 섞이는" 문제는 DB 등록 철회로 **소멸**한다.
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
- (v2.1) `mcp_server` `expose_apm_openmetrics=false`(J7 · §5.9)도 같은 원칙.

### 5.9 표준 연동 규격(OpenMetrics) 노출 — 선택 트랙 J7 (v2.1 신규 · G-2 확정 · **G-9**)

**왜 선택 트랙인가**: 제니퍼는 OpenMetrics를 내지 않고(§0.4 O-3), 진단 핵심 증거는 규격 밖이다(O-2). 사용자 지시가
"API 위주"이므로 조사·질의 경로는 J1~J5(API)가 정본이고, J7은 **같은 API 결과를 표준 규격으로 외부에 내보내는** 추가
출구다 — Prometheus·타 수집기·대시보드가 제니퍼 지표를 **벤더 API 없이** 표준 형식으로 가져가게 한다.

- **위치**: `mcp_server` `custom_route("/metrics/apm", methods=["GET"])` — `plans/92` 트랙 B-2(`/metrics` 폴스타 브리지)와
  **직렬화기·인증·캐시 기계를 공유**하고 경로만 분리한다(스크레이프 잡·캐시 TTL·부하 상한을 소스별로 따로 둔다).
- **형식**: **OpenMetrics 1.0 고정** — `application/openmetrics-text; version=1.0.0; charset=utf-8` · `# TYPE`·`# UNIT`·`# HELP` ·
  `# EOF` · **타임스탬프 미노출**(*"MetricPoint timestamps should not be exposed"*[OM-1]). 2.0은 Experimental이라 제외(O-1).
- **내는 것(1차 · 인스턴스 단위 수치만 · 이름은 잠정 — U-2·U-12 recorded JSON으로 단위 확정 후 고정)**:

  | MetricFamily | 타입·UNIT | 원천(`/api/realtime/instance` 필드[J-4]) | 비고 |
  |---|---|---|---|
  | `jennifer_instance_info` | info | 도메인·인스턴스 목록 + `apm_instance_map` | 라벨 `nodename`·`jennifer_domain_id`·`jennifer_instance_id`·`jennifer_instance_name`·`match_confidence` |
  | `jennifer_active_services` | gauge | `activeService` | |
  | `jennifer_transactions_per_second` | gauge | `tps` | 비율이라 counter(`_total`) 아님 |
  | `jennifer_response_time_seconds` | gauge · `seconds` | `responseTime` | ms→초 변환(단위 △ U-12) · 단위 접미사 필수[OM-1] |
  | `jennifer_concurrent_users` | gauge | `concurrentUser` | |
  | `jvm_memory_used_bytes` / `jvm_memory_committed_bytes` | gauge · `bytes` | `heapUsed` / `heapCommitted` | 라벨 `jvm_memory_type="heap"` — OTel `jvm.memory.used`의 Prometheus 명명 변환 규칙(점→`_` · 단위 접미사 · UNIT 메타)[OM-3] |
  | `jennifer_bridge_up` | gauge | Open API 응답 성공 여부 | 1/0 — 브리지 자체 상태(침묵 실패 금지) |
  | `jennifer_bridge_truncated` | gauge | 인스턴스 상한 초과 여부 | 잘라낸 사실을 노출(`plans/92` B-2 동형) |

- **라벨 규약**: `nodename` = 폴스타 hostname(`apm_instance_map` 해소 · D-119 규약 — node_exporter·폴스타 브리지와 **같은 키로
  조인**) · **미정합 인스턴스는 `nodename` 없이 내지 않는다**(info의 `match_confidence="none"`로만 표시 — 잘못된 조인 방지).
  트랜잭션명·URL·SQL·txid·client IP는 **라벨 금지**(카디널리티·PII — §8.3).
  **[정정 2026-09-22 · `plans/92` v3 §0.0.4]** `nodename`의 값은 폴스타 **`server_name`**이다(OS hostname이 아니다).
  D-119 ③은 도구 인자 이름이 `hostname`일 뿐 그 값을 "`hostname(=server_name)`"로 규정한다. PromQL 도구(`promql_tools.py:447`)와
  `plans/92` B-2 폴스타 브리지도 `nodename = server_name`이다. 반면 §5.3의 `apm_instance_map`은 인스턴스를 **OS hostname**
  (`was_object.hostname` · 정합 파일 `hostname`)으로 해소한다. 공동존은 name≠hostname이다(D-046).
  따라서 J7은 해소한 OS hostname을 폴스타 `cmm_resource`(`server.Server` · `dtime IS NULL`)의 `name`으로 **한 번 더 결정적으로 역해소**해 `nodename`에 넣는다.
  이것은 D-046 해소기의 역방향이다. 역해소가 0건이나 다건이면 위 규칙대로 `nodename` 없이 `match_confidence="none"`으로만 낸다.
  은행존처럼 name = hostname인 존에서는 이 단계가 항등이다. 조사 경로(§5.3 · `REQUIRED_EVENT_FIELDS`)의 hostname 해소는 그대로 둔다.
  이 정정은 J7 노출 라벨에만 적용된다.
- **[부기 2026-09-22 · `plans/92` v3 §4.5 착수 조건 1]** `custom_route` 핸들러는 `mcp_server` lifespan 컨텍스트에 닿지 않는다.
  lifespan은 SSE 세션마다 열리고, 폴스타 DB 풀·설정은 도구의 `ctx.request_context.lifespan_context`에만 있기 때문이다.
  J7이 쓰는 폴스타 SQL(`was_object` 1순위 브릿지 · 위 역해소)과 Open API 클라이언트 설정도 B-2와 같은 방식으로 얻는다 —
  **브리지 전용 지연 자원**(첫 스크레이프 때 생성)이다. 공용 노출 모듈은 92 v3의 `om_exposition.py` 제안과 같은 것이다(아래 overfit 항목의 "공용 직렬화기").
- **내지 않는 것**: 이벤트·액티브 서비스 목록·X-View·프로파일·SQL 통계(규격 밖 O-2 또는 고카디널리티) → `apm_*` 도구(API)로만.
- **부하 가드**: 스크레이프마다 Open API를 치지 않는다 — 응답 캐시 TTL(기본 60s) · 스크레이프당 호출 = 도메인당 1회 ·
  인스턴스 상한 · **토큰 사용량 제한(§8.4)을 조사 경로와 공유**하므로 J7 전용 호출 예산을 둔다.
- **인증**: `mcp_server` Bearer 미들웨어를 그대로 통과(`plans/92` §4.5와 동일) — 무인증 노출 금지.
- **소비 측 선택지(우리 경로)**: 조사·질의는 `apm_*`가 정본이다. 운영 Prometheus가 이 출구를 스크레이프하면 기존 `prom_*`
  도구로도 추세를 볼 수 있으나, **같은 지표를 두 경로로 LLM에 주지 않는다**(소스 혼동 — D-119 대안 기각 사유 "A+B 병행"과 같은 이유).
- **overfit**: 벤더 리터럴(`jennifer_*`)은 `mcp_server/mcp_server/apm_openmetrics.py`에 격리하고 공용 직렬화기는 벤더 무지로 둔다.

---

## 6. 구현 계획 — Wave J0 ~ J6 (+ v2.1 선택 J7)

> 착수 판정은 `선행` 완료 + `게이트` 해제인 Wave만. 과금 API(Gemini 등) 실 호출이 필요한 검증은 **D-127 건별 승인**.
> 착수 시 최근 작업 단위 양식을 따른다 — 계획서 → `CAPABILITY-MAP-87.md` → 모듈별 `SPEC-*.md` →
> `tasks/plan-87.md`·`tasks/todo-87.md`(`tasks/`에 plan/todo 쌍 14개 전례). 각 Wave 착수 직전 §3의 `file:line`을 재실측한다.

| Wave | 내용 | 산출물 | 선행 | 게이트 |
|---|---|---|---|---|
| **J0** 선행 실측 | §2.6 U-1~U-12 실측 · 인스턴스↔hostname 일치율 · ~~DDL 채집~~ **v1 엔드포인트 recorded JSON 채집**(v2) · 토큰 권한·사용량 제한 · PII 샘플 · ~~공식 MCP `tools/list`(U-11)~~(v2.1 판정 완료 — §0.5) | `docs/29_jennifer_integration_survey.md`(가칭 — 착수 시 번호 재실측) · `testdata/jennifer/`(마스킹 샘플·recorded JSON) | 제니퍼 접근 권한 · 테스트 토큰 | ~~G-1·G-2·G-8~~ **v2.1 확정 완료** — 외부 전제(접근 권한)만 남음 |
| **J1** 데이터 평면 기반(API) — v2 재정의(구 "SQL") | `JenniferApiConfig` · `apm_client.py`(Open API 호출 — v2.1: 백엔드 인터페이스 없음 §5.2(d)) · **GET·경로 허용목록**(코드 상수) · 레이트 리밋·토큰 마스킹 · `apm_instance_map`(정합) · 기동 로그 1줄(버전·허용 경로 수) | `apm_client.py`(httpx) · `config/apm_instance_map.yaml` · 테스트(허용목록 밖 경로·비GET **100% 거부** · recorded JSON 계약 · `respx`) · `pyproject` httpx 선언 | J0(U-4·U-5·U-12) | — |
| **J2** 도구 표면(API) | `apm_*` 8종 — 실시간·**구간(`/api/dbmetrics/*`)**·액티브 서비스·트랜잭션(1분 창 분할)·이벤트 · 상위 N · 마스킹 | `apm_tools.py` · 계약 테스트(recorded JSON → `apm_*` 반환 · §5.2(d)) | J1 · J0(U-2·U-6) | **G-3** |
| **J3** 진단 확대 | 지침·플레이북·WAS 시그니처 8종·권고 표·브리핑 소스 라벨·프로파일 2단계 폴백 | `investigation_guidance.py`·`severity_signatures.py`·`remediation.py`·`briefing_builder.py` 변경 · `config/apm_playbooks.yaml`·`apm_signatures.yaml` · 목업 WAS 시나리오 6종(Plan 65 확장) | J2 | — |
| **J4** 이벤트 편입 | 1단계 폴링 수신기(경계 준수안) · 정규화 · `apm_event_levels.yaml` · `app_impact` 승격 축 · 트리거 힌트 · 2단계 어댑터 push(**2-A SNMP trap 수신기 또는 2-B 커스텀 어댑터** — 별건 착수) | `noise_gate/alarm_server/jennifer_poll_receiver.py` · `noise_gate/domain/apm_event.py`(순수 정규화 — 폴링·SNMP·TCP 세 입력 공통) · (2-A) `snmp_trap_receiver.py` · 게이트 테스트(플래그 off 비트 동일) | J2·J3 | **G-4** |
| **J5** 질의 경로 | `db_registry` `apm`(**`backend: mcp`** — v2)·`jennifer` family · ~~`jennifer_export` DB · 지식·유사어 시드~~(v2 삭제) · 라우팅 골든셋 추가 · 미제공 능력 부분 응답 · UI 배지 | 설정 파일 · `testdata/routing_gold` 추가 | J2 · **Plan 82 Wave 7(그룹 실행자 훅 — `mcp` 변형)** | **G-5** |
| **J6** 대응·복구 L2 | 승인 대기함 UI·API · `remediation_policy.yaml` · 실행기 패키지(트랜잭션·검증·롤백·감사) · 카탈로그 3종 · 적응형 공격 시나리오 | 신규 패키지 `remediation/`(권고) · 본체 어드민 라우트 · 테스트(정책 거부·롤백·이중 승인·"제안 변조 → 실행 불가") | J3 | **G-6·G-7 + D-195 ③ 등재** |
| **J7** 표준 노출(선택 · v2.1) | `GET /metrics/apm` OpenMetrics 1.0 브리지(§5.9) — 인스턴스 수치 지표 8패밀리 · `nodename` 라벨 규약 · 캐시·호출 예산 · 브리지 상태 게이지 | `mcp_server/mcp_server/apm_openmetrics.py`(벤더 매핑) · 공용 직렬화기(`plans/92` B-2와 공유) · 테스트(1.0 형식 검증 — `# EOF`·`_total`·단위 접미사·타임스탬프 부재 · 미정합 인스턴스 `nodename` 미노출 · 라벨 금지 목록 · 플래그 off 라우트 404) | J2 · (공유 시) `plans/92` O-B2 | **G-9** |

```
(v2)
J0 ──► J1 ──► J2 ──┬──► J3 ──► J4 ──► (J6)
                   ├──────────────► J5 ◄── Plan 82 Wave 7
                   └──────────────► (J7) ◄┄ plans/92 B-2 직렬화기(공유 시)
```
(v1.1은 J1(SQL)·J2(API)가 병렬이었고 J5가 J1에 매달렸다. v2는 API 단일 경로라 기반(J1)→표면(J2) 직렬이다.)

**수용 기준(요지)**
- J1·J2: `apm_*` 반환 계약 테스트 고정 · 미매칭 시 `error: instance_unresolved` · 1분 창 분할 상한 · 마스킹 검증
  (`pii_regex_check`) · 플래그 off 시 도구 미등록(비트 동일) · `mcp_server/tests` 무회귀 · **(v2) 허용목록 밖 경로·
  비GET 메서드 요청 100% 거부(`/api-v2/manage/*` 표본 포함) · 토큰이 로그·오류 메시지에 0회 노출**.
- J3: 목업 WAS 시나리오 6종(큐잉·DB 풀·GC stall·힙·슬로우 SQL·외부 지연)에서 **시그니처 판정 정확·권고 후보
  정확**(결정적 — LLM 미사용 테스트) · 브리핑에 APM 증거가 인용됨으로 판정 · APM 미가용 시 폴백 사유 노출.
  RCAEval 축(원인 지표·유형) 기준으로 골든 6/6. 골든셋 정답 일부는 **비공개**로 관리한다(OpsEval 방식 — 프롬프트
  과적합 방지). 반증·정체 가드(P15)는 "반복 호출 3회 → 미결 종료" 테스트로 고정한다.
- J4: 폴링 중복 0(커서) · 정규화 순수 함수 테스트 · `app_impact`가 **승격만** 하는 비대칭 테스트 · 심각도 3 불변 ·
  플래그 off 비트 동일(`test_plan60_flags_off_regression.py` 섹션 추가).
- J5: 라우팅 골든셋 회귀 0 · `apm` 등록 시 실행 그룹 순서(폴스타 → apm) · ~~`catalog_diff` 동등성~~(v2: DB 프로필 없음) ·
  보존 기간 밖·자유 집계 질의에 **부분 응답 + 사유**(침묵 0건) · 본체 `src/`에 제니퍼 토큰·URL 참조 0건(grep).
- J6(P10): 목업 시나리오에서 "승인 → 실행 → 검증 회복"과 "검증 실패 → 롤백 → 에스컬레이션" 둘 다 완주 ·
  정책 밖 요청 100% 거부 · 제안 변조 시나리오에서 실행 도달 0 · 감사 레코드 완전성.
- J7(v2.1): 노출 텍스트가 **OpenMetrics 1.0 파서로 무오류 파싱**(`prometheus_client.openmetrics.parser` 또는 `promtool check metrics`
  중 착수 시 실측해 택1) · `# EOF` 종결·단위 접미사·타임스탬프 부재 단언 · Open API 장애 시 `jennifer_bridge_up 0`(빈 응답·500 아님) ·
  캐시 TTL 안의 반복 스크레이프에서 Open API 호출 증가 0 · 라벨에 금지 키(트랜잭션명·URL·SQL·txid·IP) 0건.

---

## 7. 산출물·파일 배치·설정

| 패키지 | 신규/변경 | 파일 |
|---|---|---|
| `mcp_server/` | 신규 | `mcp_server/apm_tools.py` · `mcp_server/apm_client.py` · (J7 선택) `mcp_server/apm_openmetrics.py` |
|  | 변경 | `config.py`(`JenniferApiConfig`·`expose_apm_tools`) · `server.py`(`register_apm_tools`) · ~~`config.toml`(`[[sources]] jennifer_export`)~~(v2 삭제) · `.env.example`(~~`JENNIFER_EXPORT_CONNECTION`~~·`JENNIFER_API_URL`·`JENNIFER_API_TOKEN`) · `pyproject.toml`(httpx 선언 — G7) · ~~`security.py`(apm 도메인 deny)~~ → **v2: 허용목록은 `apm_client.py` 코드 상수**(deny 목록이 아니라 allow 목록 — 신규 관리 API가 추가돼도 자동 차단) |
| `config/` | 신규 | `apm_instance_map.yaml` · `apm_playbooks.yaml` · `apm_signatures.yaml` · `apm_event_levels.yaml` · ~~`db_profiles/jennifer_export.yaml` · `knowledge/jennifer_export/` · `synonym_seeds/jennifer_export.yaml`~~(v2 삭제) · (J6) `remediation_policy.yaml` |
|  | 변경 | `db_registry.yaml`(`apm` 솔루션 `backend: mcp`·`jennifer` family — v2: `jennifer_export` DB 없음) |
| `sre_agent/` | 변경 | `application/investigation_guidance.py` · `domain/severity_signatures.py` · `domain/remediation.py` · `application/briefing_builder.py` · `settings.py`(플래그 2) · `toolset_profiles.py`(APM 폴백 노트) |
| `noise_gate/` | 신규 | `alarm_server/jennifer_poll_receiver.py` · `domain/apm_event.py` · (J4-2단계 2-A 선택 시) `alarm_server/snmp_trap_receiver.py` |
|  | 변경 | `alarm_server/config.py`·`__main__.py`(수신기 선택) · `domain/alarm.py`(source family 배지 — 필드 추가 없이 `raw_payload` 활용 우선) · `application/nodes/notification_gate.py`(`app_impact` 승격 훅) · `application/nodes/investigation_trigger.py`(힌트) |
| `src/` | 변경 | `static/`(소스 배지) · (J6) `api/routes/remediation.py`(승인 대기함) |
| 신규 패키지(J6·G-7) | 신규 | `remediation/`(자체 `pyproject`·`tests`·`scripts`) |
| 문서 | 신규 | `docs/29_jennifer_integration_survey.md`(J0 실측) |

`.env` 신규 키(전부 미입력 시 현행 동작): ~~`JENNIFER_EXPORT_CONNECTION`~~(v2 삭제) · `JENNIFER_API_URL` · `JENNIFER_API_TOKEN` ·
`MCP_EXPOSE_APM_TOOLS` · `MCP_EXPOSE_RAW_API` · `APM_GUIDANCE_ENABLED` · `APM_SIGNATURES_ENABLED` ·
`JENNIFER_EVENTS_ENABLED` · `JENNIFER_POLL_INTERVAL_SECONDS` · `APP_IMPACT_ENABLED` · `REMEDIATION_EXECUTOR_ENABLED` ·
(2-A 선택 시) `JENNIFER_SNMP_TRAP_PORT`·`JENNIFER_SNMP_ALLOWED_SOURCES` · (J7 선택) `MCP_EXPOSE_APM_OPENMETRICS`·`APM_OPENMETRICS_CACHE_SECONDS`.
list/dict 값은 JSON 배열 형식 · 인라인 주석 금지(Known Mistakes).

---

## 8. 안전 통제·거버넌스

### 8.1 불변식 (D-003·D-035·D-189 계승 — L1까지)

- **읽기 전용** (v2 개정): ~~`jennifer_export`는 SELECT 전용 계정~~ · **`apm_client`가 GET + 경로 허용목록만 통과**시킨다
  (1차 · 코드 상수 · allow 목록). 근거: 정본 스펙에 쓰기·제어 API가 조회 API와 같은 토큰으로 공존하고
  (`POST /api-v2/manage/data-server/control` 등[J-4] · 도메인 GC 요청[J-19]), 조회 전용 토큰 등급은 ✖(U-5). 토큰 권한
  축소는 협의되면 **2차** 방어로 더한다 · `apm_raw_api` 기본 비노출(열어도 허용목록은 동일).
- **LLM 평면에 실행 도구 없음**(P13): `apm_*`는 전부 조회다. L2 실행기는 LLM 밖(§5.7).
- **결정적=판단·LLM=서술**: 정합·시그니처·임계·권고 선택·심각도 매핑은 코드·yaml. LLM은 서술만.
- **대상은 결정적 확정**: 인스턴스 해소는 정합 파일 · LLM이 인스턴스명을 추측하지 않는다(scope overflow 방지).
- **침묵 폴백 금지**: 미매칭·미가용·창 상한은 `error`/`[한계]`로 노출.
- **폐쇄망**: 제니퍼는 사내 설치형 — 외부 egress 0. 실 운영 데이터의 외부 LLM 송신은 D-120·D-127 규약.

### 8.2 신뢰 경계

- 제니퍼 응답(`running_text`·`message`·프로파일·SQL·URL 파라미터)은 **불신 데이터**다 — 지시문이 섞일 수 있다
  (Plan 78 §7.2와 동형). 서버측 상위 N·마스킹 후에만 LLM에 넣고, 원문은 저장하지 않는다.
- 관리 API 차단은 ~~토큰 권한(1차)과 `mcp_server` 경로 deny(2차)~~ → **v2: `mcp_server` 허용목록(1차 · 항상 성립)과
  토큰 권한(2차 · 협의 시)** 의 **이중**이다 — 조회 전용 토큰이 없어도 1차가 선다.
- **토큰 출처**: AIOps 전용 토큰을 제니퍼 콘솔에서 발급받는다. 폴스타 DB `was_connection.jennifer_token`을 읽어
  재사용하지 않는다(제니퍼 측 발급 주체·사용량 제한·감사를 우회 · 폴스타 토큰 회전 시 조사 경로가 조용히 끊김).
- **공식 MCP 서버**: v2.1 미채택(§0.5). 재검토 트리거로 다시 열더라도 토큰은 `mcp_server` → 제니퍼 MCP 호출 헤더에만
  싣고(`sre_agent` 비보유 — §2.8 ③ 기각 사유), 허용목록은 **도구 이름 단위**로 건다. **벤더 공개 프록시(`insight.jennifersoft.com`)
  모드는 어떤 경우에도 금지**(외부 LLM·최대 30일 보관[J-22] — D-120).
- **J7 OpenMetrics 출구**: 노출 텍스트도 `mcp_server` Bearer 뒤에만 둔다. 라벨은 §5.9 허용 키만(이름·IP·SQL·URL 금지).
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
- **(v2) 토큰 사용량 제한**[J-20]: AIOps 토큰의 제한값을 운영 조직과 정하고(U-5 — 단위 미확인), 초과 응답은
  `error: "apm_quota_exceeded"`로 구분해 브리핑 `[한계]`에 기재한다(빈 결과로 삼키지 않음). 폴링·조사·질의 경로가
  **같은 토큰을 공유**하므로 폴링 주기가 조사 예산을 잠식하지 않게 경로별 호출 상한을 둔다.

---

## 9. 리스크·미해결

| # | 리스크 | 심각도 | 대응 |
|---|---|---|---|
| R-1 | **인스턴스↔hostname 미정합** → 데이터가 있어도 무용(Plan 78 R-12) | High | **폴스타 `was_object` 브릿지 우선**(U-10) · J0 U-4 일치율 실측 · 정합 파일은 예외만 · 신뢰도 표기 · 미매칭 상관 보류 |
| R-2 | ~~RDB Export 비활성·PG 최신 버전 비호환·DDL 상이~~ → **(v2) API 단일 경로 의존** — 뷰 서버·Open API 장애(또는 비활성 옵션) 시 APM 증거 전무, 적재본 같은 우회 경로 없음 | Med | 기동·조사 시 APM 가용 사전 판정(§5.4-a) → **OS 근사 폴백 + 사유 명시**(침묵 강등 금지) · 보존 기간 밖·자유 집계는 부분 응답 · 요구 확인 시 G-1 ⓑ로 적재본 보조 경로 복원 |
| R-3 | 5.x 이벤트 명칭이 4.x와 달라 매핑 파일 오류 | Med | U-1 실측 후 파일 작성 · 미지 유형은 `unknown`으로 DASHBOARD(보수) |
| R-4 | Open API 토큰이 관리 API까지 허용 — **(v2) 스펙 실측으로 개연성 상향**: 쓰기·제어 API(데이터 서버 control·도메인 변경·도메인 GC)가 같은 인증 체계 | High | §8.1 **허용목록 1차(항상)** + 토큰 권한 2차(협의) · 비GET·목록 밖 경로 거부 테스트(J1 수용 기준) |
| R-5 | 뷰 서버 부하·1분 창 제한으로 조사 지연 | Med | §8.4 · 구간 상한 · 서버측 캐시(사건창 단위, TTL 60s) |
| R-6 | PII 노출(프로파일·바인드 값) | High | §8.3 · J0 샘플 검증 전 실 데이터 LLM 투입 금지 |
| R-7 | `app_impact`가 게이트를 과억제/과승격 | Med | 승격 전용 비대칭 · 심각도 3 불변 · 플래그 off 기본 · 결정 기록(decision_store) |
| R-8 | L2 실행기의 오작동·범위 초과 | **High** | 정책 파일 거부 · 단일 인스턴스 · 이중 승인 · 트랜잭션 롤백 · G-6 전 착수 금지 |
| R-9 | 벤더 API 변경(5.x 마이너 업그레이드) | Med | `apm_tools`가 벤더 매핑 계층을 캡슐화 · 계약 테스트(recorded JSON) |
| R-10 | 이벤트 폴링이 D-119 경계를 우회 | Low | G-4 권고안 ①(경계 경유) |
| R-11 | 미들웨어 OS 근사(W7-1)와 APM 판정 충돌 | Low | APM 1차·OS 2차 우선순위 설정 · 충돌 시 브리핑에 둘 다 표기 |
| **R-12** | (v2) **진단 조회가 전부 v1(`/api/*`)인데 v1은 "유지보수 중단"** — *"not removed for compatibility, but are no longer maintained"*[J-4]. 향후 버전에서 필드 변경·제거 가능 | Med | 폐기가 아니라 동결로 판정(5.6.4에도 v1 필드 추가 △) · recorded JSON 계약 테스트(U-12) · 기동 시 버전 에코 · 벤더 매핑 계층 캡슐화(§5.2(d)) · v2 대체 엔드포인트 출현 시 `apm_client.py` 내부만 교체 · v1 제거 공지 시 §0.5 재검토 트리거 ⓑ로 공식 MCP 재판정 |
| **R-13** | (v2) **5.7.0 javax→jakarta 전환**으로 커스텀 어댑터(2-B) 빌드·로드 실패 · `extension_allowed_packages` 미등록 시 어댑터 미로드(무증상) | Med | 2-A(SNMP 공식 어댑터) 우선 검토 · 2-B 선택 시 대상 버전 고정 빌드 + 기동 후 **테스트 이벤트 1건 수신 확인**을 배포 체크리스트에 |
| ~~**R-14**~~ | ~~(v2) "표준 연동 규격"이 조직 내부 규격으로 존재하는데 본 계획이 그와 다른 전송·인증을 택함~~ → **v2.1 해소**: 사용자가 CNCF OpenMetrics로 확정(G-2) | — | J7(§5.9)로 충족 · 이벤트·진단 데이터는 규격 범위 밖(O-2) |
| **R-15** | (v2.1) **J7 OpenMetrics 출구가 뷰 서버 부하·토큰 사용량을 잠식**하거나, 미정합 인스턴스가 잘못된 `nodename`으로 노출돼 인프라 지표와 **오조인** | Med | 캐시 TTL·도메인당 1호출·J7 전용 호출 예산(§5.9) · 미정합은 `nodename` 미부여 · `jennifer_bridge_up`/`_truncated`로 상태 노출 · 플래그 기본 off |

**미해결**: 제니퍼 도입 시점·라이선스(범위 밖 · 본 계획의 착수 전제) · DPM 연동(Plan 55 M3 DPM 축) · L3.

---

## 10. 사용자 확정 게이트

| # | 질문 | 권고 | 영향 |
|---|---|---|---|
| **G-1** ✅ **확정(2026-09-17)** | 연동 방식: ⓐ API 단일(Open API 조회 + 이벤트 폴링→어댑터) ⓑ API + RDB Export 적재본 보조 ⓒ SQL + API + 이벤트 3중(v1.1) | **사용자 확정: ⓐ "api 위주로 가자"** — ⓑ는 장기 자유 집계 요구가 확인될 때만 복원(§5.2(a)) | J1·J2·J5 형태 |
| **G-2** ✅ **확정(2026-09-17)** | "표준 연동 규격"의 실체 | **사용자 확정: CNCF OpenMetrics**("cncf openmatric에 정의된 규격") — 1.0 고정(2.0 Experimental) · 제니퍼 네이티브 노출 ✖ → 우리 브리지로 충족(J7 · §0.4 (2) · §5.9) · 이벤트·진단 데이터는 규격 밖 | J7 신설 · G-9 · R-14 해소 |
| **G-3** | 도구 표면 이름: 벤더 중립 `apm_*` vs `jennifer_*` | **`apm_*`**(Plan 78 §4.7.2 · 벤더 교체 시 매핑 계층만) | J2 |
| **G-4** | 이벤트 폴링 수신기의 API 호출 경로: ① `mcp_server` 경유 ② 직접 · 2단계 push 착수 여부·방식(2-A SNMP trap 공식 어댑터 / 2-B 커스텀 EVENT 어댑터) | **①** — D-119 일원화 · (v2.1) 2단계는 **폴링의 지연·부하·토큰 사용량이 실측으로 문제될 때만** 착수, 방식은 운영 조직의 어댑터 배포 수용 여부로(§5.5) | J4 |
| **G-5** | text2sql 편입 시점: ~~J1 직후~~ vs J3 이후 | (v2) **J2 + Plan 82 Wave 7 직후** — SQL 파이프라인 재사용이 사라져 솔루션 그룹 실행자(`backend: mcp`)가 선행이다 | J5 |
| **G-6** | **L2(승인 후 실행) 착수 여부와 D-003 예외 범위**(카탈로그 3종·단일 인스턴스·이중 승인) | 착수하되 **J3 완료·목업 검증 후** · 초기 카탈로그는 §5.7 3종 | J6 · D-195 ③ |
| **G-7** | 실행기 위치: ① `noise_gate/` 하위 ② **신규 최상위 `remediation/`** | **②** — 실행 자격증명 격리(D-139) | J6 |
| **G-8** ✅ **실측 판정(2026-09-17)** | 제니퍼 공식 MCP 서버 활용: ① 미사용 — `mcp_server`가 Open API 직접 ② `mcp_server` 뒤 백엔드 ③ `sre_agent` 직결 | **① 확정 · ② 미채택 · ③ 기각** — 사용자 지시 "실측하여 판정하라"에 따라 §0.5 M-1~M-6 실측(런타임 `tools/list`는 설치본 비공개·SaaS 로그인으로 불가 — 최선값 가정에서도 결론 불변) · 재검토 트리거 ⓐ~ⓒ 전부 충족 시에만 재판정 | J1 · §5.2(d) 추상화 삭제 |
| **G-9** (v2.1 신규) | J7(OpenMetrics 표준 노출) **착수 시점**: ① J2 직후 단독(직렬화기 자체 구현) ② `plans/92` 트랙 B-2 착수와 묶음(직렬화기·인증·캐시 공유) ③ 소비자(운영 Prometheus 등)가 확정될 때 | **②** — 같은 형식 기계를 두 번 만들지 않는다. 단 소비자가 먼저 확정되면 ①로 앞당긴다 | J7 · `plans/92` O-B2 |

---

## 11. 신규 결정 예약 — D-195 (등재는 J0 완료·G-1 확정 시)

> **채번 근거(2026-09-03 실측)**: `docs/02_decision.md` `## D-` 헤더·「변경 이력」 표 최댓값 **D-193** ·
> 「채번 이력」 표 예약(D-105·115·134·158·163~168·176). 다음 번호 194는 작성 시점에 `plans/50` v2.1·
> `CAPABILITY-MAP-50.md`·`SPEC-briefing-contract.md`가 "D-194 예정"으로 쓰고 있어(표 미등재) 충돌을 피해
> **D-195**를 「채번 이력」 표에 등재했다. 같은 날 **D-194는 `plans/50` v2.2로 본문 등재 완료**(`## D-194` 실측)
> — 결과적으로 번호가 연속이며 재조정은 불필요하다.

| 번호 | 결정(예약) | Wave |
|---|---|---|
| **D-195** | **제니퍼 APM 연동** — ① `mcp_server` 세 번째 소스(~~SQL 적재본 +~~ **Open API 단일 조회 경로 + 이벤트 API 폴링(어댑터 push는 조건부)** — v2 2026-09-17: RDB Export SQL 경로 미채택 · GET·경로 허용목록 1차 통제 · **v2.1 2026-09-17: 사용자 확정 G-1 API 위주 · G-2 표준 연동 규격 = CNCF OpenMetrics → 수치 지표를 OpenMetrics 1.0으로 노출하는 선택 출구(J7) · G-8 실측 판정 = 제니퍼 공식 MCP 서버 미채택(Open API 직접 호출 확정)**) · 벤더 중립 `apm_*` 도구 표면 · OTel 컨벤션 · 서버측 정합·축약·마스킹·감사(D-119 ① 적용 · **D-168 2단계의 구체화**) ② 진단 확대 — `sre_agent` 지침·플레이북·WAS 시그니처·권고 표·브리핑(결정적 판정 · APM 1차·OS 근사 2차 폴백 사유 명시) ③ **대응·복구 L2** — D-003의 **실행 평면 한정 예외**(카탈로그 조치·승인된 제안 id·단일 인스턴스·이중 승인·정책 파일·트랜잭션 검증-롤백·LLM 미탑재 실행기 · L3 범위 밖) — **③은 G-6 확정 전 등재하지 않는다** | J1~J6 |

**D-168과의 관계**: D-168은 Plan 78이 "미들웨어 조사 편입(1단계 OS 근사 · 2단계 APM 연계)"으로 예약한 번호다.
본 계획은 그 **2단계의 벤더 확정판**이므로, 등재 시 D-168을 소진(1단계 = 구현 완료 `middleware_profile`·
`middleware_signatures.yaml` · 2단계 = D-195로 위임)하고 D-195에서 상세를 갖는다. 둘을 한 번호로 합치지
않는 이유는 D-168이 Plan 78의 W7 범위 결정이고, D-195는 소스·조치 평면까지 포함하는 별개 범위이기 때문이다.

**파급 문서(등재 시 갱신)**: `plans/55` §7 선행 결정(대상 제품=제니퍼·연동 방식=~~3중~~ **API 단일(v2)**·엔티티 매핑=정합 파일) ·
`plans/78` W7-2단계 상태 · `plans/64` §8.3(선행 거버넌스 → D-195 ③) · `plans/sre-agent/README.md` 계획 표 ·
`config/db_registry.yaml` 주석(`apm` `backend: rest` → `mcp`) · `docs/18_known_mistakes.md`(J0에서 실수 발생 시).
**v2 파급(각 소유 계획 착수 시 반영 — 본 갱신에서는 편집하지 않음)**: `plans/82` Wave 7(그룹 실행자 훅의 `mcp` 변형이
J5 선행) · `plans/90` §9 A3 표(`jennifer_export` db_id 혼입 우려 → 소멸) · `plans/92` 머리말("벤더 exporter가
OpenMetrics를 내면 같은 파서 재사용" → 제니퍼는 OpenMetrics 노출 ✖로 해당 없음) — **2026-09-22 `plans/92` v3에서 반영 완료**
(F-12 · 관계를 "87 J7이 92 B-2 노출 기계를 재사용"으로 정정).

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

- [J-1] SQL로 제니퍼 데이터 조회하기 — 자체 파일 DB·API 서버·JDBC(제니퍼소프트 기술 블로그, 2021-06) · https://jennifersoft.com/ko/blog/tech/2021-06-02/ — v2: 원문 표현은 *"사용자가 원하는 가공 형태의 데이터를 조회하는데 어려움이 있습니다"* (v1.1의 "직접 접근이 제한적"은 요약이었음)
- [J-2] 제니퍼 성능 데이터 RDB에 적재해서 활용하기 — RDB Export(Oracle/MySQL/PostgreSQL·테이블 목록·`server_view.conf`) · https://jennifersoft.com/ko/blog/tech/2021-07-19/ · 영문 테이블 노출 예시 https://jennifersoft.com/en/blog/tech/2021-08-23/
- [J-3] JENNIFER 4.5 매뉴얼(PDF) — 이벤트 유형 11장 · PLC 6.7 · 서비스 덤프 6.8 · 강제 GC 9.11.4 · 액티브 서비스 제어 13장 · 리포지토리 12.12 · https://cdn.jennifersoft.com/wp-content/uploads/Documents/ko/JENNIFER4.5_Manual.pdf (4.x 기준 — 5.x 대조는 U-1·U-8)
- [J-4] **(v2 정본 교체)** JENNIFER5 API Reference — **https://openapi.jennifersoft.com/** (OpenAPI 3.0.3 · `info.version` 5.6.4 · 52경로 · `bearerAuth` · 설명 *"We are developing a new API v2. Existing Open APIs are not removed for compatibility, but are no longer maintained."*) · 원문: 리포 `jennifersoft/jennifer5-open-api` gh-pages `index.html`에 스펙 인라인(최종 push 2026-03-25) — 2026-09-17 직접 확인 · (구 사본·보조) `spec.json` https://github.com/jennifersoft/jennifer-developer-guide/blob/master/src/resources/spec.json · 개발자 가이드 https://jennifersoft.github.io/jennifer-developer-guide/
- [J-5] Open API v2 매뉴얼(Bearer·`auth-test`·`manage-rule-event*`·`manual-rdb-export`·`deploy`) · https://github.com/jennifersoft/jennifer5-open-api-v2-manual — v2: 최종 push 2023-04-04(보조 자료 · 정본은 [J-4])
- [J-6] **(v2 교체)** 뷰 서버 확장 튜토리얼 · https://github.com/jennifersoft/jennifer-view-extension-tutorial (최종 push 2026-06-15 ✔) · (구) 어댑터 튜토리얼(TransactionHandler·EventHandler·EventData 필드) · https://github.com/jennifersoft/jennifer-view-adapter-tutorial/blob/master/README_ko.md
- [J-7] 지원 플랫폼(Java·.NET·PHP·Python·Node.js·OpenTelemetry) · https://jennifersoft.com/ko/product/apm/platforms/
- [J-8] Jennifer AI / Jennifer Insight(Anomaly Event·Metrics Correlation·Insight Chat = Open API tool 호출·폐쇄망 LLM) · https://jennifersoft.com/ko/blog/tech/2025-12-22-jenniferai-jenniferinsights/ · https://jennifersoft.com/ko/blog/tech/2025-02-10-jennifer-ai/
- [J-9] X-View·프로파일 소개(JENNIFER v5 소개서 PDF) · https://www.cywell-integration.com/files/02.WASJENNIFER_v5_.pdf
- [J-10] PLC 소개 · https://www.theteams.kr/teams/2747/post/68554
- [J-11] 제품 구성(Agent·Data Server·View Server·Repository) · https://www.fin-ncloud.com/marketplace/jennifer
- [J-12] MSA 모니터링(토폴로지·Call Chain·프로토콜) · https://jennifersoft.com/ko/blog/tech/2026-12-17-jennifer-msa-monitoring/
- [J-13] OpenTelemetry 연동 How-to(Collector → 제니퍼 트레이스) · https://jennifersoft.com/ko/blog/tech/otel-jennifer-howto/
- [J-14] PagerDuty 어댑터(레벨 매핑) · https://github.com/jennifersoft/jennifer-view-adapter-pagerduty
- [J-15] 제니퍼소프트 공개 리포지토리 목록(어댑터·플러그인·API 서버·JDBC) · https://github.com/orgs/jennifersoft/repositories · SNMP 어댑터 https://github.com/jennifersoft/jennifer-view-adapter-snmp (v2 확인: `event.SNMPAdapter` · 5.2.3+ · 레벨별 trap OID 기본 `1.3.6.1.4.1.27767.1.1` · 메시지 패턴 기본 time·domain·instance·level·name·value · community·대상 `127.0.0.1/162` 기본값 · 최종 push 2025-04-11) · API 서버 https://github.com/jennifersoft/jennifer5-api-server · JDBC https://github.com/jennifersoft/jennifer5-jdbc-driver
- [J-16] 릴리즈 노트 5.6.5 · https://docs.jennifersoft.com/ko/jennifer5_releasenote/5_6_5
- **v2 추가(2026-09-17 · `docs.jennifersoft.com`은 JS 렌더링이라 원문 markdown 엔드포인트 `/r/markdown/chapter/{bookId}/{chapterId}`로 직접 확인)**
- [J-17] JENNIFER5 설치 가이드 10장 「제니퍼 AI 설치 및 구성 (서버 및 브라우저 LLM, MCP, 도움말 챗봇)」 — MCP 연결(LLM 프록시 = MCP 서버 · Streamable HTTP `<llm-proxy-server:port>/mcp` · `X-Jennifer-Api-Url`·`X-Jennifer-Api-Token` · *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를 조회하므로"* · 도구 예시 · 토큰 발급 [설정 > JENNIFER 서버 > 인증토큰 발급] · 5.6.5+ · JDK 17 · `jennifer-llm-1.x.x.zip`) · https://docs.jennifersoft.com/ko/jennifer5_installation_guide/1cb5cd771533968d (원문 `…/r/markdown/chapter/f364f298fe59cc82/1cb5cd771533968d`)
- [J-18] 릴리즈 노트 **5.7.0**(릴리즈 날짜 2026-08-13) — *"stable `jvm.*` semantic convention 병행 인식"* · OpenTelemetry 메트릭·프로파일 조회 경로 보강 · javax→jakarta(Jakarta EE 11 · Spring 7 · Jetty 12) · `extension_allowed_packages` 등록 · PostgreSQL/MSSQL RDB Export 오류 수정 · https://docs.jennifersoft.com/ko/jennifer5_releasenote/5_7_0
- [J-19] 릴리즈 노트 **5.6.5**(릴리즈 날짜 2025-10-29) — 5.6.4.28 *"[뷰서버] 도메인 단위 인스턴스 GC 를 요청하는 오픈 API 추가 - /api-v2/manage/instance/<domain-id>/gc"* · 5.6.4.10 *"오픈 API를 이용한 데이터 서버 확장"*(도메인 관리·데이터 서버 제어) · 제니퍼 인사이트(서버 LLM·브라우저 LLM) · [J-16]과 같은 문서 · ※ 5.6.4 Hotfix 노트는 GC API를 5.6.4.27로 표기(△ 버전 표기 상이 — 조사 보고)
- [J-20] 릴리즈 노트 **5.6.3**(릴리즈 날짜 2024-01-17) — *"인증 토큰 추가시 사용량 제한을 0으로 설정할 경우, 무제한 토큰으로 동작"* · *"오픈 api 를 비활성화 하기 위한 뷰서버 비공식 옵션 추가"* · *"카프카 트랜잭션 Export 기능 추가"*(5.6.2.7) · *"RDB Export 분 단위 애플리케이션 통계 추가"* · https://docs.jennifersoft.com/ko/jennifer5_releasenote/5_6_3
- [J-22] (v2.1) JENNIFER5 설치 가이드 11장 「제니퍼 AI 데이터 보안 정책 안내서」 — *"제니퍼 AI 는 제니퍼 Open API 의 모니터링 데이터를 자동으로 분석하는 기능"* · 읽는 데이터 범위(*"도메인, 인스턴스 목록, 도메인, 인스턴스 별 성능 메트릭, 트랜잭션, 스택 트레이스, 프로파일 텍스트 (SQL 파라미터 제외), 에러, 이벤트"*) · 공개 프록시 `insight.jennifersoft.com`(*"최대 30 일간 보관"*) · *"기본적으로 고객사의 폐쇄망 내부에 자체 설치된 거대 언어 모델(LLM)을 사용하도록 설계"* · https://docs.jennifersoft.com/ko/jennifer5_installation_guide/da3799f39e86eb9f (원문 `…/r/markdown/chapter/f364f298fe59cc82/da3799f39e86eb9f`) — 2026-09-17 직접 확인. ※ [J-17] 10장의 프록시 설정 키(`server.llm/conf/server_llm.conf` · `llm_proxy_model_provider` 등)·설치본 파일명 표기 *"jennfer-llm-1.x.x.zip"* 도 같은 날 원문 확인(획득 경로 기재 없음)

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
- [S-6] (v2) SolarWinds OrionSDK — *"Why get data from SWIS instead of just querying the Orion database directly?"* (스키마 진화를 API 매핑이 흡수 · DB 자격증명 대신 콘솔 권한 체계) · https://github.com/solarwinds/OrionSDK/blob/gh-pages/docs/about-swis/index.md — 모니터링 벤더가 내부 저장소 직접 조회 대신 API 경유를 권하는 1차 전례(§0.4 근거 5)
- [OM-1] (v2.1) OpenMetrics 1.0 — *"Version: 1.0 | Status: Published | Date: November 2020"* · *"This standard expresses all system states as numerical values … Contrary to metrics, singular events occur at a specific time."* · Content-Type `application/openmetrics-text; version=1.0.0; charset=utf-8` · *"Expositions MUST end with EOF"* · 단위 접미사 MUST · *"MetricPoint timestamps should not be exposed"* · https://prometheus.io/docs/specs/om/open_metrics_spec/ (원본 저장소 `prometheus/OpenMetrics`) — 2026-09-17 직접 확인
- [OM-2] (v2.1) OpenMetrics 2.0 **[EXPERIMENTAL]** — *"we reserve the right to break the compatibility if it's necessary"* · `version=2.0.0` · `_total`·단위 접미사 필수→권고 · `_created`→인라인 `st@` · *"Around 2024, the OpenMetrics project was incorporated under the CNCF Prometheus project umbrella"* · https://prometheus.io/docs/specs/om/open_metrics_spec_2_0/ — 2026-09-17 직접 확인
- [OM-3] (v2.1) OpenTelemetry Specification v1.59.0 — Prometheus and OpenMetrics Compatibility(점 등 비권장 문자 → `_` · UCUM 단위 → 단위 접미사 · 단조 합계 `_total` · 속성 → 라벨 · *"The resulting unit SHOULD be added to the metric as UNIT metadata"*) · https://github.com/open-telemetry/opentelemetry-specification/blob/v1.59.0/specification/compatibility/prometheus_and_openmetrics.md — 2026-09-17 직접 확인
- [S-4] Introducing Nurse: Auto-Remediation at LinkedIn (2015) · https://engineering.linkedin.com/sre/introducing-nurse-auto-remediation-linkedin — △ 현재 404, 검색 스니펫으로 내용 확인
- [S-5] Automate Java performance troubleshooting with AI-powered thread dump analysis on Amazon ECS and EKS (AWS Containers Blog, 2025-12) · https://aws.amazon.com/blogs/containers/automate-java-performance-troubleshooting-with-ai-powered-thread-dump-analysis-on-amazon-ecs-and-eks/ — "알람 → 덤프 수집 → LLM 요약 → 보고서(자동 교정 없음)"의 산업 표준형
- HolmesGPT(CNCF Sandbox) · https://github.com/HolmesGPT/holmesgpt — `Toolset.approval_required_tools`(sre-agent/02 §9 실측 · 본 계획 미채택 근거 §4.5)

### 12.5 내부 참조

- `plans/55`(멀티소스 로드맵 · §5 C-1~C-5 · §7 선행 결정) · `plans/78` §3.3(조치 위험·통제 문헌)·§4.7.1~4.7.3(미들웨어 방식·APM 편입 경로·체크리스트)·W7·§7.1·§8.3 · `plans/64` §7~§8(L3 통제·조치 거버넌스) · `plans/sre-agent/02` §9(human-gated) · `docs/25_host_investigation_load_guard.md` · `config/middleware_signatures.yaml`(선언적 정책 파일 전례)
- 조사 보조 텍스트(스크래치패드, 저장소 밖 · 세션 종료 시 소멸): 4.5 매뉴얼 추출본 `j45.txt` · v5 소개서 추출본 `cywell.txt` ·
  문헌 조사 보고서 `literature_report.md`(63건 카드 · OpenAlex/S2 인용수 병기 · 미확인·정정 목록) · 저장소 실측 보고서 `repo_report.md`
- v2 조사(2026-09-17): 조사 서브에이전트 2건(제니퍼 공식 연동 표면 — 릴리즈 노트 72편·설치 가이드 11장·공개 리포 45개 검색 /
  "표준 연동 규격" 후보·국내 연동 관행 — 공개 RFP·구축 사례에서 제니퍼 연동 방식 명시 문서 0건). 계획에 반영한 제니퍼
  주장은 [J-4]·[J-17]·[J-18]·[J-19]·[J-20]·SNMP 어댑터·GitHub push 일자를 **원문으로 재확인**했고, 재확인 못 한 것은 △로 남겼다

---

## 13. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-09-03 | **v1** — 최초 작성. 제니퍼 기능·연동 지점 조사(§2, 출처 16건) · 저장소 실측(§3) · 문헌 33편 서지 실측(§4·§12) · 전제 정정(§0.2 — 파일 DB → RDB Export+API+이벤트 3중) · 아키텍처(§5) · Wave J0~J6(§6) · 게이트 G-1~G-7(§10) · D-195 예약 등재(작성 중 D-194가 `plans/50` v2.2로 본문 등재됨 — 번호 연속 확인) · 파일명 `-TODO` 접미사(INDEX 「파일명 상태 접미사」 규칙, 코드 0건) |
| 2026-09-03 | **v1.1** — 서브에이전트 보고 반영. ① **정정 2건**: RCACopilot 게재처 ICSE→**EuroSys 2024** · 철회본(arXiv 2511.15755) 인용 삭제 · DiLink 표기 정정 ② **폴스타 스키마의 제니퍼 연동 필드 발견**(`was_connection.jennifer_*` · `was_object.agent_id/hostname/obj_name`) → §3.3 실측 · §5.3 **1순위 브릿지** · U-10 · R-1 완화 ③ 벤치마크 정량 근거(OpenRCA 11.34% · ITBench 13.8%) → L3 범위 밖 ④ 문헌 추가(MicroRank·TraceRCA·Nezha·GIRA·Nurse·JDK 21 Troubleshooting·WebLogic·JEUS·Cork) → **P15~P17** 신설(반증·정체 가드 / 결정적 후보 축소 / 일반 완화 분리) · §4.6 WAS 시그니처 임계 구체화(stuck 600s · OOM 7종 · old gen 계단 상승) ⑤ 병렬 작업 주의(§3 — D-194 작업이 같은 파일 수정 중) · 착수 시 SDD 양식(§6) |
| 2026-09-17 | **v2** — 사용자 지시 *"db가 아닌 제니퍼 api를 사용하거나 표준 연동 규격으로 연동"* 에 따른 재조사. ① **SQL(RDB Export 적재본) 경로 철회 → Open API 단일 조회 경로**(§0.4 근거 6건 · 빼는 비용 3건 · G-1 권고 ⓒ→ⓐ) ② **"표준 연동 규격" 4갈래 분해**(제니퍼 공식 규격 채택 · 업계 표준은 SNMP trap·Kafka·MCP만 송신 가능, OTLP 수신만·Prometheus/OpenMetrics ✖ · 국내 공공·금융 표준 근거 없음 · 조직 내부 규격은 **G-2 개정**으로 사용자 확인) ③ **정정 6건**: 공식 MCP 서버 ✖→✔(§2.7·§2.8 신설·**G-8**) · 강제 GC API ✖→도메인 단위 ✔ · Kafka 트랜잭션 Export ✖→✔ · OTel 메트릭 수용 ✖→△ · [J-1] 인용 문구 · 정본 스펙 `spec.json`→`openapi.jennifersoft.com`(5.6.4 · v1 "no longer maintained") ④ **통제 격상**: 같은 토큰에 쓰기·제어 API 공존 실측 → GET·경로 허용목록을 1차 통제로(§8.1·§8.2 · R-4) · 토큰 사용량 제한(§8.4) · 폴스타 저장 토큰 재사용 금지 ⑤ 이벤트 2단계 = **2-A SNMP trap 공식 어댑터 / 2-B 커스텀 어댑터**(§5.5 · G-4) ⑥ 질의 경로 `backend: sql`→**`mcp`**(DB 등록 철회 · Plan 82 Wave 7 선행 · G-5) ⑦ Wave 재편(J1=API 기반·J2=도구 표면 직렬) · U-2·U-3·U-5·U-8·U-9·U-10 개정 · U-11·U-12 신설 · R-2 개정 · R-12~R-14 신설 · 출처 [J-17]~[J-20]·[S-6] 추가(원문 재확인) · 최신 버전 5.7.0(2026-08-13) 반영 ⑧ `docs/02` D-195 예약 행 · `plans/INDEX.md` 87행 갱신. 코드 0건 유지 → `-TODO` 유지 |
| 2026-09-17 | **v2.1** — 사용자 확정 *"표준 연동 규격은 cncf openmatric에 정의된 규격을 말한다. api 위주로 가자. 3번은 실측하여 판정하라."* ① **G-2 확정 = CNCF OpenMetrics**: §0.4 (2) 네 갈래 표를 OpenMetrics 실측 O-1~O-5로 교체(1.0 Published·2.0 Experimental · 규격 범위 = 수치 지표만 · 제니퍼 정본 스펙에 OpenMetrics/Prometheus/OTLP 0건 → 네이티브 노출 ✖) → **선택 트랙 J7**(§5.9 `GET /metrics/apm` OpenMetrics 1.0 브리지 · `plans/92` B-2 기계 공유 · **G-9** 신설) · R-14 해소 · R-15 신설 ② **G-1 확정 = API 위주(ⓐ)** · 이벤트 2단계 push에서 "표준" 라벨 제거 → 폴링 부족이 실측될 때만 착수(§5.5 · G-4) ③ **G-8 실측 판정 = 공식 MCP 미채택 · Open API 직접 호출 확정**(§0.5 신설 — M-1 LLM 프록시 운영 전제 · M-2 권한 이점 0 · M-3 계약 가시성 상실 · M-4 공개 프록시 모드 · M-5 데이터 범위 · M-6 `mcp` 1.29.1 헤더 주입 가능 실측. 런타임 `tools/list`는 설치본 비공개·`insight.jennifersoft.com` 302→/login으로 **실측 불가를 명시**하고 최선값 가정에서도 결론 불변 확인 · 재검토 트리거 ⓐ~ⓒ) → §5.2(d) 백엔드 인터페이스 삭제(단일 구현 추상화 금지) · U-11 축소 · §2.8 표 판정 갱신 ④ 출처 [J-22]·[OM-1]~[OM-3] 추가(원문 확인) ⑤ `docs/02` D-195 예약 행 · `plans/INDEX.md` 87행 갱신. 코드 0건 유지 → `-TODO` 유지 |
| 2026-09-22 | **v2.1 부기** — `plans/92` v3 재검토(사용자 지시 *"고치지 않은 것들을 권고에 맞게 모두 수정하라"*)의 교차 정정. ① **§5.9 J7 `nodename` 어휘 정정**: 값은 폴스타 `server_name`이다(D-119 ③ `hostname(=server_name)` · `promql_tools.py:447` · 92 B-2와 같은 키). `apm_instance_map`이 해소한 OS hostname을 `cmm_resource.name`으로 결정적으로 역해소하는 단계를 추가했다(D-046 역방향 · 0건·다건이면 `match_confidence="none"` · 조사 경로 hostname 해소는 무변경) ② **§5.9 부기**: `custom_route` 핸들러는 SSE 세션 lifespan 자원에 닿지 않는다 → 브리지 전용 지연 자원(92 v3 §4.5 착수 조건 1과 같다) ③ §11 파급 목록의 `plans/92` 머리말 항목 = **반영 완료** 표기. 게이트·범위 변경 없음 · 코드 0건 `-TODO` 유지 |
