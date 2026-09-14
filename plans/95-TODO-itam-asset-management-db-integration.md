# 95. 자산관리(ITAM) DB 연동 — MariaDB 방언 지원 + 서빙 개방 + 스키마 지식 정본화

> **작성일**: 2026-09-11 · **v2**(2026-09-11): 사용자 제공 스키마 실측 반영 — **엔진 = MariaDB 확정**(G-1 종결),
> `TCDMSIF80`(68컬럼)·`TCDMSIF79`(9컬럼) 전수 판독, 함정 T5(폴스타와 사용률 컬럼 정면 중복) 신설
> **성격**: 구현 계획(조사 완료, 구현 전) · **상태: 계획(미구현) — 사용자 확정 게이트 G-2~G-12 대기(§8)** · 코드 0건이라 파일명 `-TODO`
> **요청 취지(사용자 지시 원문)**: ① *"폴스타의 정보이외에 자산관리 시스템과 연동하려고 한다. 연동 방식은 기존 db mcp연동
> 방식이며 관련 db스키마 정보를 제공하려고 한다. 관련한 계획 파일을 정리하라."* · ② *"첨부된 이미지는 자산관리시스템의
> 서버현황조회쿼리문과 tcdmsif80테이블 컬럼 정보이다."* · ③ *"이 이미지는 tcdmsif79 테이블의 컬럼 정보이다."*
> **⚠ 해석 정정 1(§0.1)**: 이 일은 **"신규 DB 편입"이 아니다.** `itam`은 이미 `config/db_registry.yaml:243`에 `enabled: true`로
> **등록돼 있고**, 프롬프트 4곳이 이미 `itam`을 알려진 DB로 가르치며, 라우팅 골든셋에도 `itam` 기대 케이스가 있다.
> 막혀 있던 것은 **등록이 아니라 서빙**이다.
> **⚠ 해석 정정 2(v2 · §0.2 실측 ③)**: *"연동 방식은 기존 db mcp연동 방식"*은 **설정만으로 끝나지 않는다.** 제공된 스키마의
> `DBMS구분 = MARIADB`인데 `mcp_server`가 지원하는 엔진은 **PostgreSQL·DB2 둘뿐**이다(실측). 따라서 본 계획의 1차 산출물은
> 설정 4파일이 아니라 **MariaDB 방언 지원**(드라이버·인트로스펙션·방언 규칙)이며, 그 위에 설정·지식이 얹힌다.
> **상위/선행 계획**: **`plans/67` R2(D-131)** — 신규 DB 편입 단일 등록점 · **`plans/63`(D-088·D-089)** — 공용 계층 DB-agnostic화
> (비폴스타 DB 활성화 시 오지시 주입 제거가 이 계획의 전제) · **`plans/55` M0/M1** — 멀티소스 확장 로드맵 · **`plans/82`(D-176)** —
> 솔루션·존 그룹 실행 축 · `plans/87`(제니퍼 APM — 3번째 소스 편입 선례) · `plans/94`(시나리오 하네스 — 회귀 판정 도구)
> **관련 결정**: D-003(읽기 전용 3중 방어) · D-004(LLM 전용 시멘틱 라우팅) · D-006(런타임 활성 판정은 `.env` 단일 출처) ·
> D-035(결정적=판단·LLM=서술) · **D-057**(DB2 스키마 대문자 한정 — 본 계획의 MariaDB 테이블명 대소문자 함정과 동일 계열) ·
> D-065 · D-088 · D-089 · D-119(관측 데이터 읽기 경계=`mcp_server`) · D-127(과금 외부 API 건별 승인) · D-131 · D-139(패키지 경계) ·
> D-143 후속3/D-176/D-206(존 그룹·실행 그룹) · D-181 부수 발견 ①(`ITAM_CONNECTION` 유령 키)
> **신규 결정 예약**: **D-214**(§9). `docs/02_decision.md` 「채번 이력」 표에 등재(2026-09-11).
> ※ 채번 실측 2026-09-11 — `## D-` 헤더 최댓값 **211** · 「변경 이력」 표 대조 · 「채번 이력」 표 예약(D-105·115·134·158·
> 163~168·176·195·**210**·**212**) 대조 → **D-213**. **2026-09-14 원격 `multiintent` 병합 시 D-214로 재부여** — 원격 sre_agent 조사 결정이 D-213을 본문·코드에 먼저 푸시.
> **실측 기준**: `file:line`·값은 2026-09-11 현 브랜치(`multiintent`, HEAD `6a48c42`)에서 직접 확인했다.
> **자산 스키마 범례**: ✔ 제공 이미지에서 판독 · △ 판독했으나 해석 확인 필요 · ✖ 미제공(게이트 대상).
> 판독 원본은 사용자 제공 화면 캡처 6장(`업무룰리모델_RDB_테이블별컬럼(tcdmsif80).xlsx` 4장 · `(tcdmsif79).xlsx` 2장)이다.

---

## 0. 요약 — 이 계획이 실제로 푸는 문제

### 0.1 등록은 끝나 있다. 막힌 것은 넷이다

| 층 | 현황(실측) | 판정 |
|---|---|---|
| DB 등록(레지스트리) | `config/db_registry.yaml:243-255` — `itam` · `enabled: true` · 별칭 5종(`자산관리`·`자산관리 DB` 포함) · `signal_terms: ["itam"]` | ✅ **있다** |
| 라우팅 어휘·프롬프트 | `src/prompts/semantic_router.py:47,325` · `input_parser.py:48` · `intent_planner.py:60` — `itam`을 알려진 DB로 이미 가르친다 | ✅ **있다** |
| 골든셋 | `testdata/routing_gold/routing.yaml:65-72` r-012 *"인시던트가 발생한 서버들의 자산 정보와 현재 사양"* → `expect.databases: [itsm, itam]` | ✅ **있다** |
| **MariaDB 엔진 지원** | `mcp_server`는 PostgreSQL·DB2만(`config.py:74` · `db.py:37,56` · `tools.py:81,194`). 본체 방언도 이진(`src/utils/sql_dialect.py:14`) | ❌ **없다 — v2 신규** |
| **MCP 소스 정의** | `mcp_server/config.toml` `[[sources]]` 8종에 `itam` **없음** | ❌ **없다** |
| **스키마 지식 정본** | `config/db_profiles/`·`semantic_models/`·`knowledge/`·`synonym_seeds/` — **전부 `itam` 0건** | ❌ **없다** |
| **런타임 활성** | `.env:67` `ACTIVE_DB_IDS=polestar` 단독 | ❌ **없다** |

### 0.2 ★실측이 바꾼 것 7건

| # | 실측 | 계획이 바뀐 지점 |
|---|---|---|
| ① | **"편입 = 레지스트리 + `.env` 2파일"(D-131)은 `DB_BACKEND=dbhub`에서 성립하지 않는다** — 운영 실측값은 `dbhub`(`.env:47`)이고 dbhub 경로의 연결 정의는 `mcp_server/config.toml` + `mcp_server/.env`에 있다(별도 프로세스·별도 cwd, D-139) | 설정 수정 파일은 **2개가 아니라 4개**(+ 지식 3종). §2 정정 체크리스트 · D-214에 기록 |
| ② | **`ITAM_DB_CONNECTION`은 읽는 코드가 0건이다** — 레지스트리 `env_connection_key`(`:251`)는 `src/routing/domain_config.py:60`까지 실려 오지만 **이 값으로 접속하는 코드가 없다**. dbhub 경로는 `mcp_server/.env`의 `ITAM_CONNECTION`만 본다. `config.toml`에 소스가 없으면 그 키마저 **오류·로그 없이 조용히 무시**(D-181 부수 발견 ① · `mcp_server/.env.example:100` 주석) | 루트 `.env`에 연결 문자열을 채우는 작업은 **비범위** |
| ③ | **★엔진이 MariaDB다**(제공 스키마 `DBMS구분` 열 = `MARIADB` · 서버명 `IT자산관리포털 운영 DB`) — 지원 엔진은 PG·DB2뿐 | **G-1 종결 · 계획 성격 변경**. 1차 산출물이 "설정"에서 **"MariaDB 방언 지원"**(§4 트랙 A0 · 파일 5개)으로 바뀜 |
| ④ | **존 미배정 DB는 실행 그룹에서 조용히 탈락한다** — `src/routing/execution_groups.py:57-59` `if not group_code: continue`. `itam`은 `zone`이 없어 `zone_group_of()`가 None(`registry.py:186`) | `ZONE_GROUP_EXCLUSIVE=false`(D-206 개방)에서 폴스타+자산 교차 질의 시 **자산 결과가 사유 없이 사라진다**. §4 트랙 D를 차단 조건으로 승격 |
| ⑤ | **존 역질문 게이트는 대상이 전부 폴스타일 때만 발동한다** — `src/orchestration/subagents.py:264-268`(`all(d in polestar_ids …)`) | 폴스타+자산 혼합 질의는 존 선택 HITL 없이 LLM 임의 팬아웃으로 직행. 기대 동작을 G-7로 확정 |
| ⑥ | **활성화가 실행 경로를 바꾸지는 않는다** — 사다리 2·3단은 tri-state이나 운영 `.env`가 `ENABLE_SEMANTIC_ROUTING=true`(:158)·`ENABLE_INTENT_ORCHESTRATION=true`(:178)·`ENABLE_DEEPAGENTS_PACKAGE=true`(:185)로 명시 고정 | 사다리 재판정 리스크 없음(1단 `deep_agent` 유지). 단 **검증은 1단 경로에서** 해야 한다(§6) |
| ⑦ | **★자산 DB에도 CPU·메모리·스토리지 「사용률」이 있다** — `TCDMSIF80`의 `sevrCPUUseRt`·`sevrMmryUseRt`·`wholStrgeUseRt`(전부 `DECIMAL(5,2)`) | 폴스타와 **답변 영역이 겹친다**. 라우팅 경계가 "선택"이 아니라 **충돌**이 됨 → 함정 T5 · 트랙 C가 선택 작업에서 **필수 작업**으로 승격 |

### 0.3 한 줄 권고

**트랙 A0(MariaDB 방언 지원)를 먼저 하고, 스키마 지식(`config/db_profiles/itam.yaml`)을 쓴 다음,
`ACTIVE_DB_IDS`에 `itam`을 넣는 것은 맨 마지막에 한다.**
순서를 뒤집으면 ①MariaDB에 PostgreSQL 방언 SQL이 나가 재시도 예산 3회를 태우고 ②스키마 지식 없는 DB가
LLM 자동 구조 분석(`source: auto`)으로 굴러 비결정적 SQL을 만들며 ③그 실패가 기존 폴스타 질의의 멀티 DB 팬아웃에 섞여
**회귀를 폴스타 쪽 결함으로 오진**하게 된다.

---

## 1. 현황 실측

### 1.1 `itam` 레지스트리 항목 (`config/db_registry.yaml:243-255`)

```yaml
  - db_id: itam
    enabled: true
    display_name: ITAM DB
    description: >-
      IT 자산 관리 데이터.
      IT 자산 목록, 자산 라이프사이클, 계약 정보,
      소프트웨어 라이선스, 하드웨어 자산 등      # ← 제공 스키마와 어긋남(§4.1에서 재작성)
    aliases: ["itam", "ITAM", "ITAM DB", "자산관리", "자산관리 DB"]
    env_connection_key: ITAM_DB_CONNECTION      # ← 읽는 코드 0건(실측 ②)
    env_type_key: ITAM_DB_TYPE
    engine: postgresql                          # ← ✖ 오류. 실제 MARIADB(실측 ③) → W-2에서 교정
    db_schema: ""                               # ← 미확정(G-2)
    signal_terms: ["itam"]
```

**비어 있는 축 3개** — `zone` 없음(존 RBAC·실행 그룹 비참여 → 실측 ④) · `family` 없음(폴스타와 다른 제품군이라 정상) ·
`solutions` 미등재(관측 솔루션 축 D-176의 구성원이 아님 — §4.4).

### 1.2 연동 배선 3층과 이름 규약

| 층 | 파일 | 키/값 | 현황 |
|---|---|---|---|
| ① MCP 소스 정의 | `mcp_server/config.toml` | `[[sources]] name = "itam"` · `type = "mariadb"` · `readonly = true` · `query_timeout` · `max_rows` | **없음** |
| ② MCP 연결 문자열 | `mcp_server/.env` | `ITAM_CONNECTION`(= 소스명 대문자 + `_CONNECTION`) | **없음** |
| ③ 클라이언트 활성 | 루트 `.env` | `ACTIVE_DB_IDS`에 `itam` 추가(소스명과 **정확히 일치**) | **없음** |

규약 위반 시 증상은 전부 **조용하다** — ①이 없으면 ②가 무시되고(오류·로그 없음), ③의 이름이 틀리면 *"알 수 없는 소스"*,
③만 있고 ①②가 없으면 라우터가 `itam`을 고른 뒤 실행에서 실패한다. `mcp_server/config.toml:42-56`의 체크리스트가 정본이다.

### 1.3 지원 엔진 실측 — MariaDB가 닿지 못하는 지점

| 지점 | `file:line` | 현황 | MariaDB 추가 시 필요한 것 |
|---|---|---|---|
| 소스 타입 선언 | `mcp_server/mcp_server/config.py:74` | `type: str = "postgresql"  # "postgresql" \| "db2"` | 세 번째 값 허용 |
| 커넥션 초기화 | `mcp_server/mcp_server/db.py:37`(pg)·`:56`(db2)·`else: 지원하지 않는 DB 타입` 경고 | 풀은 asyncpg만, DB2는 요청별 연결 | 비동기 MySQL 드라이버 풀 + `_execute_mysql()` |
| 실행 라우팅 | `mcp_server/mcp_server/db.py:107`(`_execute_pg`)·`:114`(`_execute_db2`) | 2분기 | 3분기 |
| 객체 검색 | `mcp_server/mcp_server/tools.py:81` · 헬퍼 `_pg_search_objects_sql:339` / `_db2_search_objects_sql:430` | 2종 | `_mysql_search_objects_sql` |
| 컬럼·PK·FK 인트로스펙션 | `tools.py:194` · 헬퍼 `_pg_get_columns:370`/`_pg_get_primary_keys:384`/`_pg_get_foreign_keys:401` · `_db2_*:453,470,487` | 3+3 | `_mysql_get_columns`/`_primary_keys`/`_foreign_keys` 3종 |
| 드라이버 의존성 | `mcp_server/pyproject.toml:10-11`(`asyncpg`·`ibm-db`) | 2종 | MySQL 계열 async 드라이버 1종 추가 |
| 본체 방언 판정 | `src/utils/sql_dialect.py:14` `is_db2()` — **"db2가 아니면 전부 PostgreSQL"** | 이진 | §4.2 판단(3항 분기 vs 현행 유지) |

**완화 요인 2건** — ①행 제한 절은 MariaDB도 `LIMIT`이라 `row_limit_clause()`·`sql_validation.py:195`는 **그대로 통과**한다.
②기본 프롬프트가 이미 3방언을 알고 있다(`src/prompts/query_generator.py:23` *"PostgreSQL/MySQL: `LIMIT {default_limit}`"*).
③`src/security/sql_guard.py`에는 방언 리터럴이 없다(읽기 전용 검증은 엔진 무관).

**따라서 실질 위험은 행 제한이 아니라 표현식 방언이다** — `::numeric` 축약 캐스트(PG 전용) · `||` 문자열 결합
(MariaDB 기본 모드에서는 **OR 연산자**로 해석) · 날짜 산술(`INTERVAL '1 day'` vs `DATE_SUB()`) · 식별자 인용(`"col"` vs `` `col` ``).
`src/utils/query_gen_common.py:708,863`에 PG 축약 캐스트를 **교정해 넣는** 로직이 있어, 이대로면 MariaDB에 PG 문법을 주입한다.

### 1.4 활성화 시 깨지는 것 — 함정 5건

| # | 함정 | 근거 | 증상 |
|---|---|---|---|
| **T1** | 지원 엔진이 PG·DB2뿐 | §1.3 표 | **연결 자체가 안 된다**(`db.py:37-59` else 경고 후 소스 미등록). 방언까지 열어도 PG 문법 주입 |
| **T2** | 존 미배정 DB의 실행 그룹 탈락 | `src/routing/execution_groups.py:57-59` · `registry.py:186` | `ZONE_GROUP_EXCLUSIVE=false`에서 교차 질의의 **자산 결과만 침묵 소실** |
| **T3** | 혼합 질의의 존 HITL 비발동 | `src/orchestration/subagents.py:264-268` | 존 선택 역질문 없이 전 존 팬아웃 → 비용·지연 증가 |
| **T4** | 스키마 지식 0 → LLM 자동 구조 분석 의존 | `src/nodes/schema_analyzer.py:572` · `config/db_profiles/test_db.yaml`(`source: auto` 산출 예시) | 호출마다 다른 구조 해석 → 비결정적 SQL |
| **T5** | **★폴스타와 답변 영역 중복** — 자산 DB에 CPU/메모리/스토리지 **사용률·사용량·용량**이 있다 | `TCDMSIF80`: `sevrCPUUseRt`·`sevrMmryUseRt`·`wholStrgeUseRt`(`DECIMAL(5,2)`) · `sevr*UseQanty`·`*Capc`(`DECIMAL(12,2)`) | *"서버 CPU 사용률"* 질의가 **두 DB 모두에 정당하게 매칭**된다. 라우터가 어느 쪽을 고르든 "틀렸다"고 말할 근거가 없어, **답이 조용히 달라진다**(폴스타=시계열 관측 / 자산=연계 스냅샷) |

T1·T2·T3·T5는 스키마 없이도 지금 판단 가능하다. T4만 지식 정본화(§3)가 선행한다.

### 1.5 이미 맞춰져 있는 것 (다시 만들지 말 것)

- **공용 계층 폴스타 누수 제거**(D-088 · `plans/63` P1·P4) — 비폴스타 DB 활성화 시의 오지시 주입은 해소됐고
  `scripts/overfit_check.py --ci`가 재유입을 막는다. 자산 DB용 프롬프트를 공용 계층에 넣으면 이 게이트가 막는다.
- **DB별 특화 격리**(D-089) — 자산 전용 SQL 로직이 필요해지면 `src/db_adapters/itam/`이 유일한 자리다.
- **폴스타 전용 프롬프트 경계** — `POLESTAR_DB_IDS`(`.env:408`)에 `itam`을 **넣지 않는 것이 정답**이다(`src/config.py:1285`).
- **읽기 전용 3중 방어**(D-003) — 프롬프트 + `sql_guard.py` + MCP `readonly = true`. 자산 DB에도 동일 적용.

---

## 2. 편입 체크리스트 정정 (D-131 → dbhub·비PG 엔진 실형)

`docs/18_known_mistakes.md:103`은 편입을 **"레지스트리 1항목 + `.env` 2파일"**로 기록한다. **본체 코드 수정 0**이라는 뜻으로는
정확하지만, 실제로 손대는 파일은 다음과 같다.

| 단계 | 파일 | 내용 | 조건 |
|---|---|---|---|
| **0** | `mcp_server/` 5파일(§1.3) + `pyproject.toml` | **MariaDB 방언 지원** | 엔진이 PG·DB2가 아닐 때 **(이번 경우 해당)** |
| 1 | `config/db_registry.yaml` | `engine: mariadb` · `db_schema` 확정 | 공통 |
| 2 | `mcp_server/config.toml` | `[[sources]] name = "itam"` | dbhub |
| 3 | `mcp_server/.env` | `ITAM_CONNECTION=…`(읽기 전용 계정) | dbhub |
| 4 | 루트 `.env` | `ACTIVE_DB_IDS=polestar,itam` | 공통 |
| 5 | `config/db_profiles/itam.yaml` | 구조 정본(`source: manual`) — §3 | 권장(없으면 T4) |
| 6 | `config/knowledge/itam/catalog.yaml` | 프로필에서 파생 못 하는 운영 판단만 | 선택 |
| 7 | `config/synonym_seeds/itam.yaml` | `scripts/synonym_seeds.py derive` 산출 | 선택 |

> 1·4는 본체 코드 수정 0을 유지한다(`TestNewDBOnboardingRehearsal` 불변식 —
> `tests/test_semantic_routing/test_registry_config.py:139`). 0·2·3은 **별도 프로세스의 설정·코드**라 그 불변식의 대상이 아니다.
> **"코드 0줄 편입"은 「등록된 엔진이 이미 지원될 때」의 명제**임을 D-214에 명시한다.

---

## 3. 제공받은 스키마 실측

### 3.1 출처·메타 (제공 이미지 판독)

| 항목 | 값 | 범례 |
|---|---|---|
| DBMS 구분 | **MARIADB** | ✔ |
| 서버명 | **IT자산관리포털 운영 DB** | ✔ |
| 그룹회사코드 / 서버코드 | `KB0` / `R18S1` | ✔ |
| 표준그룹구분 | `차계정/MDB_UTF8` | ✔ |
| 스키마명(시트 B열) | `INST1` · 인스턴스 표준구분 `IC1` | △ — 논리 인스턴스명인지 실 database명인지 불명(**G-2**) |
| 테이블 | `TCDMSIF80`(68컬럼) · `TCDMSIF79`(9컬럼) | ✔ |
| APP 코드 / 등록 | `DMS` / 2026-07-23 등록 | ✔ |

### 3.2 `TCDMSIF79` — 서버 지원종료일(EOS/EOL) · 9컬럼

| # | 변수명 | 컬럼 정의 | 타입 | PK | Null |
|---|---|---|---|---|---|
| 1 | `groupCoCd` | 그룹회사코드 | `CHAR(3)` | **PK1** | NOT NULL |
| 2 | `sevrHostName` | 서버호스트명 | `VARCHAR(300)` | **PK2** | NOT NULL |
| 3 | `iPCtnt` | IP주소내용 | `VARCHAR(255)` | **PK3** | NOT NULL |
| 4 | `hWSportEndYmd` | 하드웨어 지원 종료일자 | `CHAR(8)` | | NULLABLE |
| 5 | `sWSportEndYmd` | 소프트웨어 지원 종료일자 | `CHAR(8)` | | NULLABLE |
| 6~9 | `sysRegiUno` `sysRegiPrcssYMS` `sysLastUno` `sysLastPrcssYMS` | 시스템 등록/최종 사용자·일시 | `CHAR(7)` / `CHAR(20)` | | NOT NULL |

### 3.3 `TCDMSIF80` — 서버 현황·자산 상세 · 68컬럼

**PK(3열 복합)**: `groupCoCd` + `sevrHostName` + `iPCtnt` — **`TCDMSIF79`와 동일 키**(조인 가능).

| 군 | 변수명 → 컬럼 정의 (타입) |
|---|---|
| **식별(PK)** | `groupCoCd` 그룹회사코드 `CHAR(3)` · `sevrHostName` 서버호스트명 `VARCHAR(300)` · `iPCtnt` IP주소내용 `VARCHAR(255)` |
| **가상화·군집** | `vrtlMgtSevrID` 가상화관리서버ID `VARCHAR(100)` · `clstRefID` 군집참조ID `VARCHAR(100)` · `vrtlSevrRefID` 가상화서버참조ID `VARCHAR(100)` · `vrtlSevrMapngYn` 가상화서버매핑여부 `CHAR(1)`〔코드 102132000 여부〕 |
| **OS·벤더** | `oSTypzCtnt` 운영체제타입내용 `VARCHAR(255)` · `vndrCtnt` 벤더내용 `VARCHAR(255)` · `oSVsnCtnt` 운영체제버전내용 `VARCHAR(255)` |
| **분류·위치** | `sevrPtrnDstcd` 서버유형구분코드 `CHAR(1)`〔145354000〕 · `inttAreaCtnt` 설치지역내용 `VARCHAR(255)` · `sevrMdelName` 서버모델명 `VARCHAR(75)` · `asstMdelName` 자산모델명 `VARCHAR(75)` |
| **★사양·사용률(11)** | `cPUCnt` CPU개수 `DECIMAL(3,0)` · `cPUSocktCnt` CPU소켓개수 `DECIMAL(3,0)` · `sevrCPUSped` 서버CPU속도 `DECIMAL(12,2)` · `sevrCPUUseQanty` CPU사용량 `DECIMAL(12,2)` · **`sevrCPUUseRt` CPU사용률 `DECIMAL(5,2)`** · `sevrMmryCapc` 메모리용량 `DECIMAL(12,2)` · `sevrMmryUseQanty` 메모리사용량 `DECIMAL(12,2)` · **`sevrMmryUseRt` 메모리사용률 `DECIMAL(5,2)`** · `wholStrgeCapc` 전체스토리지용량 `DECIMAL(12,2)` · `wholStrgeUseQanty` 스토리지사용량 `DECIMAL(12,2)` · **`wholStrgeUseRt` 스토리지사용률 `DECIMAL(5,2)`** |
| **담당(PII)** | `rspblPsnEmpid` 담당자직원번호 `CHAR(7)` · **`rspblPsnEmnm` 담당자직원명 `VARCHAR(50)` — 시트상 암호화/변환 항목구분 「성명」** · `rspblBrncd` 담당부점코드 `CHAR(4)`〔101370000〕 · `rspblBrnName` 담당부점명 `VARCHAR(75)` |
| **물품** | `cmdtsClsfiNo` 물품분류번호 `CHAR(8)` · `cmdtsUniqno` 물품고유번호 `CHAR(7)` · `cmdtsName` 물품명 `VARCHAR(75)` · `cmdtsUseUsagCtnt` 물품사용용도내용 `VARCHAR(255)` |
| **구매·취득(금액)** | `byCtrcNo` 구매계약번호 `CHAR(11)` · `byCtrcName` 구매계약명 `VARCHAR(300)` · `byCmdtsDtalsSerno` 구매물품세부일련번호 `DECIMAL(5,0)` · **`acqsiAmt` 취득금액 `DECIMAL(15,0)`** · `acqsiYmd` 취득년월일 `CHAR(8)` |
| **유지보수(금액·기간)** | `asstManmenDstcd` 자산유지보수구분코드 `CHAR(1)`〔133673000〕 · **`manmenCnpr` 유지보수계약금액 `DECIMAL(15,0)`** · `manmenCtrcStartYmd`/`manmenCtrcEndYmd`/`manmenCtrcTermiYmd` 계약 시작/종료/해지년월일 `CHAR(8)` · `sevrManmenCtrcNo` 서버유지보수계약번호 `CHAR(11)` · `manmenCtrcName` 유지보수계약명 `VARCHAR(300)` · `manmenCmdtsDtalsSerno` 유지보수물품세부일련번호 `DECIMAL(5,0)` · `manmenHopeYm` 유지보수희망년월 `CHAR(6)` |
| **운영환경·노후화** | `sevrOperEvirnDstcd` 서버운영환경구분코드 `CHAR(2)`〔145328000〕 · `elapsNoy` 경과년수 `DECIMAL(3,0)` · `osoaRplacMchtlDstcd` 노후교체기기구분코드 `CHAR(2)`〔145476000〕 · `asstStusDstcd` 자산상태구분코드 `CHAR(1)`〔102739000〕 |
| **자산 회계** | `asstMapngYn` 자산매핑여부 `CHAR(1)` · `asstHoldBrncd` 자산보유부점코드 `CHAR(4)` · `asstHoldBrnName` 자산보유부점명 `VARCHAR(300)` · **`rmainAcbkAmt` 잔존장부금액 `DECIMAL(15,0)`** · `stohusAsstYn` 창고자산여부 `CHAR(1)` |
| **자산 분류** | `asstClsfiDstcd` 자산분류구분코드 `CHAR(2)`〔102738000〕 · `asstClsfiDsticName` 자산분류구분명 `VARCHAR(75)` · `dtalsAsstClsfiDstcd` 세부자산분류구분코드 `CHAR(3)`〔114287000〕 · `dtalsAsstClsfiDsticName` 세부자산분류구분명 `VARCHAR(75)` |
| **무상·시리얼** | `grttStartYmd`/`grttEndYmd` 무상 시작/종료년월일 `CHAR(8)` · `srialNoCtnt` 시리얼번호내용 `VARCHAR(255)` · `cnfgItemDescCtnt` 구성항목설명내용 `VARCHAR(255)` |
| **감사** | `sysRegiUno` `CHAR(7)` · `sysRegiPrcssYMS` `CHAR(20)` · `sysLastUno` `CHAR(7)` · `sysLastPrcssYMS` `CHAR(20)` |

### 3.4 이 스키마가 만든 결정적 규칙 5건 (프로필 `query_guide`에 그대로 들어간다)

1. **날짜는 전부 `CHAR(8)` `YYYYMMDD` 문자열**이고 일시는 `CHAR(20)`이다 — DATE 타입이 아니다.
   기간 비교는 문자열 비교(`manmenCtrcEndYmd <= '20261231'`)로 하거나 `STR_TO_DATE()`를 쓴다.
   *"이번 달 만료되는 유지보수 계약"*류 질의의 결정적 조립 대상이다(LLM에 맡기면 `DATE` 함수를 쓴다).
2. **조인 키는 3열 복합**(`groupCoCd`+`sevrHostName`+`iPCtnt`)이다 — `TCDMSIF80` ↔ `TCDMSIF79`.
   한 열만으로 조인하면 중복 행이 나온다. FK 제약 유무는 ✖ 미확인(G-3).
3. **코드성 컬럼 9종이 표준 코드 ID만 갖고 값 목록이 없다** — 145354000(서버유형) · 102739000(자산상태) ·
   102738000(자산분류) · 114287000(세부자산분류) · 145328000(서버운영환경) · 133673000(자산유지보수) ·
   145476000(노후교체기기) · 101370000(부점) · 102132000(여부). **코드값을 모르면 WHERE가 못 써진다**(G-5).
4. **금액 4종·성명 1종이 민감 정보다** — `acqsiAmt`·`manmenCnpr`·`rmainAcbkAmt`(+`byCtrcName` 계약명) ·
   `rspblPsnEmnm`(시트가 암호화 대상 「성명」으로 표시). §4.5 마스킹·PII 처리 대상.
5. **`sevrHostName`이 `VARCHAR(300)`**이다 — 호스트명 치고 비정상적으로 길다. 실제로 FQDN·별칭·설명이 섞여 들어갔을
   가능성이 있고, 그렇다면 폴스타 `cmm_resource.hostname`과의 **정확 일치 조인이 실패**한다(G-6 — 교차 질의의 성패).

### 3.5 아직 없는 것 (✖ — 게이트 대상)

| # | 없는 것 | 왜 필요한가 |
|---|---|---|
| a | **물리 컬럼명** — 시트는 「컬럼명(한글)」과 「변수명(camelCase)」만 준다. 실 DDL 식별자가 `sevrHostName`인지 `SEVR_HOST_NAME`인지 한글인지 불명 | SQL을 못 쓴다. **G-4 최우선** |
| b | **테이블명 대소문자** — 파일명은 `tcdmsif80`, 시트 값은 `TCDMSIF80`. Linux MariaDB는 `lower_case_table_names=0`이면 **대소문자를 구분**한다 | DB2 `POLESTAR` 대문자 사건(D-057)과 같은 계열. 틀리면 전 질의가 "테이블 없음" |
| c | **database(스키마)명** — `INST1`이 실 database명인지 | `db_schema` 값 |
| d | **코드값 목록/코드 마스터 테이블** | §3.4-③ |
| e | **서버현황조회 쿼리문** — 제공 이미지 6장은 **전부 컬럼 정의 시트**였고 쿼리문은 보이지 않았다 | 실제 조회 패턴(조인·필터·정렬)이 프로필 `query_examples`의 최상급 재료다. **재요청 필요** |
| f | **행수·갱신 주기** — `TCDMSIF*`의 `IF`가 연계(interface) 테이블을 시사한다. 원장이 따로 있는지, 적재 주기가 언제인지 | 사용률 데이터의 신선도 → T5 우선순위 판단의 근거 |
| g | 그 외 테이블 목록 | 지금 아는 것은 2개뿐. 자산 대장 전체를 답하려면 더 필요할 수 있다 |

---

## 4. 트랙

| 트랙 | 한 줄 | 선행 | 산출물 |
|---|---|---|---|
| **A0. MariaDB 방언 지원** | `mcp_server`에 세 번째 엔진을 넣는다 | — (지금 착수 가능) | 드라이버 의존성 · `db.py` 실행기 1종 · `tools.py` 인트로스펙션 헬퍼 4종 · `config.py` 타입 · 테스트 |
| **A1. 연결 개방** | MCP 소스 정의 + 읽기 전용 계정 + 기동 확인 | A0 · G-2·G-3 | `config.toml` 소스 1개 · `mcp_server/.env` 1키 · `list_sources`·`describe_table` 응답 |
| **B. 스키마 지식 정본화** | §3 실측 → 수동 프로필(+지식·시드) | G-4·G-5 | `config/db_profiles/itam.yaml` · `knowledge/itam/` · `synonym_seeds/itam.yaml` |
| **C. 라우팅 경계(T5)** | 폴스타와 겹치는 「사용률」을 어느 DB가 답하는가 | G-8 | 레지스트리 `description` 재작성 · 라우팅 골든셋 경계 케이스 |
| **D. 존·RBAC·마스킹** | 존 무관 DB의 그룹 탈락(T2)·HITL 비발동(T3) 처분 + 금액·성명 마스킹 | G-7·G-9 | `execution_groups` 처분(코드) · `sensitive_columns` 보강 · `default_allowed_db_ids` 방침 |
| **E. 교차 질의** | 폴스타 ↔ 자산 결과 결합(조인 불가 — 키 병합) | G-6 | 호스트 키 정합 규약 · 멀티 DB 결과 병합 확인 |
| **F. 검증** | 회귀 0 + 자산 질의 합격 판정 | A·B | `routing_gold` r-012 실측 가능화 · `text2sql_gold` 자산 케이스 · `plans/94` 시나리오 군 |

### 4.1 트랙 C — T5는 "경계"가 아니라 "충돌"이다

자산 DB에 `sevrCPUUseRt`가 있으므로 *"서버 CPU 사용률 알려줘"*는 **두 DB 모두 정답 후보**다. 라우터가 무엇을 고르든
사용자는 다른 숫자를 받는다(폴스타=모니터링 시계열, 자산=연계 스냅샷). 해결은 셋 중 하나다.

| 안 | 내용 | 대가 |
|---|---|---|
| **(가) 폴스타 우선 · 자산은 자산 축만** | 사용률·사양 질의는 폴스타가 정본. `itam` description에서 **사용률·용량 어휘를 빼고** 계약·금액·자산상태·EOL·담당자·분류로 한정 | 자산 DB의 사용률 컬럼은 답에 안 쓰임(데이터 낭비) |
| **(나) 명시 지목 시에만 자산** | *"자산관리에서"*·*"자산대장"*이 붙을 때만 `itam` | 사용자가 매번 지목해야 함 |
| **(다) 둘 다 답하고 출처 병기** | 교차 질의로 두 값을 나란히 | 지연·비용 증가, 값 불일치의 설명 책임이 생김 |

**권고 (가)** — `plans/82`의 솔루션 축(D-176)에서 폴스타는 `capabilities: [server_spec, server_usage, …]`를 이미 소유한다.
자산 DB에 같은 capability를 주면 **두 솔루션이 같은 능력을 주장**해 실행 그룹 선택이 불안정해진다.
경계는 **키워드 사전이 아니라 `description` 재작성으로** 푼다 — 사전 분류 재도입은 D-004 위반이다.
현행 description(*"IT 자산 목록, 자산 라이프사이클, 계약 정보, 소프트웨어 라이선스, 하드웨어 자산"*)은 제공 스키마와도
어긋나므로(소프트웨어 라이선스 컬럼은 없다) 어차피 다시 써야 한다.

### 4.2 트랙 A0 — 방언 처리의 범위를 좁힌다

`src/utils/sql_dialect.py`의 이진 판정(`is_db2`)을 3항으로 바꿀지는 **필요한 만큼만** 결정한다.

- `row_limit_clause()` — MariaDB도 `LIMIT` → **변경 불필요**.
- `sql_literal()` — MariaDB는 기본 모드에서 백슬래시 이스케이프를 해석한다. 현재는 `'` → `''`만 처리(`sql_dialect.py:41`).
  **결정적 조립이 `itam`을 대상으로 emit할 때만** 문제이므로, 1차 범위(LLM 생성 경로)에서는 발생하지 않는다 → **2차 판단**.
- 표현식 방언(`::numeric`·`||`·날짜 함수) — 이것이 실질이다. **프로필 `query_guide`의 방언 규칙 블록**으로 먼저 대응하고
  (프롬프트 층), 반복 실패가 실측되면 그때 결정적 교정을 `src/db_adapters/itam/`에 둔다(D-089 · LLM 비결정성 대응 원칙).

→ **A0의 코드 변경은 `mcp_server/`에 집중하고 `src/`는 레지스트리 `engine` 값 외에 건드리지 않는 것을 1차 목표로 한다.**

### 4.3 트랙 E — 조인은 없다, 키 정합만 있다

DB 간 SQL 조인은 불가능하다(서로 다른 인스턴스). 교차 질의는 **DB별 개별 질의 → 결과 병합**이다.

| 축 | 자산(`TCDMSIF80`) | 폴스타(`cmm_resource`) | 위험 |
|---|---|---|---|
| 호스트명 | `sevrHostName` `VARCHAR(300)` | `hostname` / `name` | 길이 300은 FQDN·별칭 혼입 가능(§3.4-⑤) |
| IP | `iPCtnt` `VARCHAR(255)` | `ipaddress` | 다중 IP가 한 칸에 들어갔을 수 있음 |

**G-6의 답이 트랙 E의 존재 여부를 결정한다.** 정합하지 않으면 트랙 E를 별건으로 분리하고 단독 질의부터 서빙한다.

### 4.4 솔루션 축 등재 여부

`itam`은 관측 솔루션이 아니다(자산 원장). `config/db_registry.yaml`의 `solutions:`에 넣으면 실행 그룹 순서 축에 들어오는데,
**존 그룹이 없어 그룹이 만들어지지 않는다**(T2). 따라서 **1차에서는 등재하지 않는다** — 등재는 T2 처분(G-7) 이후 별건.

### 4.5 마스킹·PII

현재 마스킹 기본값은 비밀·토큰·카드번호 계열 13종(`src/config.py:400-412`)이고 `mask_ip`·`mask_email`은 기본 off다.
자산 DB는 **금액 4종 + 성명 1종**을 실제로 갖고 있으며(§3.4-④), 이는 ①화면 노출 ②**FabriX PII 필터 차단**
(`src/security/pii_filter.py` · `docs/pii_filtering_rules.md`) 양쪽에 걸린다 — 후자는 질의가 **응답 없이 막히는** 증상이다.
`rspblPsnEmnm`은 원천에서 암호화 대상으로 표시돼 있으므로 **조회 계정이 평문을 보는지 자체가 확인 대상**이다(G-9).

---

## 5. 작업 분해

| WU | 작업 | 선행 | verify |
|---|---|---|---|
| **W-0** | G-2~G-12 확정(사용자 인터뷰) | — | 답이 본문에 기록됨 |
| **W-1** | MariaDB 드라이버 선정·의존성 추가 | — | `mcp_server` 임포트·기동 무회귀 |
| **W-2** | `db.py` — 초기화·실행 3분기 + `_execute_mysql()` | W-1 | 단위 테스트(목 커넥션) · 기존 PG/DB2 경로 회귀 0 |
| **W-3** | `tools.py` — `_mysql_search_objects_sql`·`_get_columns`·`_get_primary_keys`·`_get_foreign_keys` | W-1 | `information_schema` 질의 결과 shape가 PG/DB2 헬퍼와 **동형**임을 단언 |
| **W-4** | `config.py` 타입 허용 + `config.toml` 소스 + `mcp_server/.env` 연결 | W-2·W-3 · G-2·G-3 | 기동 로그 소스 등재 · `list_sources`에 `itam` · `describe_table TCDMSIF80` 68컬럼 반환 |
| **W-5** | 레지스트리 `engine: mariadb` · `db_schema` 확정 | G-2 | `pytest tests/test_semantic_routing/test_registry_config.py` 그린 |
| **W-6** | `config/db_profiles/itam.yaml` 작성(§3 + 방언 규칙 블록 + 결정적 규칙 5건) | G-4·G-5 · W-4 | 실 DB `information_schema` 대조로 **컬럼 전수 일치**(미리보기 일부 금지) |
| **W-7** | `knowledge/itam/catalog.yaml` + `synonym_seeds/itam.yaml` | W-6 | `scripts/catalog_diff.py` 동등 · 시드 로드 건수 |
| **W-8** | 트랙 C — `description` 재작성 + 경계 골든셋 | G-8 | 경계 케이스에서 기대 DB 선택 |
| **W-9** | T2 처분 — 존 미배정 DB의 실행 그룹 취급 | G-7 | 폴스타+itam 대상에서 **itam 미탈락** 단언 |
| **W-10** | T3 처분 — 혼합 질의 존 HITL 기대 동작 | G-7·G-8 | 단위 테스트로 고정 |
| **W-11** | 마스킹·PII 반영(금액 4·성명 1) | G-9 | 마스킹 단언 + `scripts/pii_regex_check.py` |
| **W-12** | `ACTIVE_DB_IDS`에 `itam` 추가(활성화) | W-4~W-11 | 기동 로그 사다리 1줄 동일(1단 `deep_agent` 유지) |
| **W-13** | 회귀 판정 — 폴스타 단독 질의 동작 불변 | W-12 | `plans/94` 하네스 무과금 경로 + 기존 골드셋 대비 회귀 0 |
| **W-14** | 문서 — D-214 등재 · `docs/18` 체크리스트 정정 · INDEX | 전건 | 링크·번호 실존 확인 |

**순서 불변식**: W-12(활성화)는 **마지막**이다(§0.3). W-6 없이 W-12를 하면 T4가 발동한다.

---

## 6. 성공 기준

1. **기능** — 자산 질의 3종이 실 데이터로 답한다: ①*"지원 종료일이 6개월 내인 서버"*(`TCDMSIF79`) ②*"유지보수 계약이
   이번 분기 만료되는 서버"*(`manmenCtrcEndYmd`) ③*"경과년수 N년 이상 노후 서버"*(`elapsNoy`·`osoaRplacMchtlDstcd`).
   실패 시 사유가 사용자 응답에 노출된다(침묵 폴백 금지).
2. **회귀 0** — 폴스타 단독 질의(기존 골드셋)의 산출 SQL·결과가 활성화 전후 동일. **판정은 사다리 1단(`deep_agent`)에서** 한다.
3. **침묵 소실 0** — 폴스타+자산 교차 질의에서 어느 DB도 사유 없이 빠지지 않는다(T2 해소 단언).
4. **경계 일관성(T5)** — *"CPU 사용률"* 단독 질의가 **항상 같은 DB**로 간다(안 (가) 채택 시 폴스타).
5. **결정성** — 동일 자산 질의 3회 반복 시 생성 SQL 동일. 자동 분석(`source: auto`) 의존 0.
6. **게이트** — `arch_check --ci` · `overfit_check --ci` exit 0. 자산 스키마 리터럴이 공용 계층·독스트링에 들어가지 않는다(D-179).
7. **읽기 전용** — 자산 DB 계정이 SELECT 권한만 보유(DB 레벨) + MCP 소스 `readonly = true`.
8. **기존 엔진 무회귀** — PG·DB2 소스의 `describe_table`·`execute_sql` 결과가 A0 전후 동일.

---

## 7. 위험 · 비범위

**위험**

| 위험 | 완화 |
|---|---|
| MariaDB 드라이버 선정(폐쇄망 반입·라이선스·비동기 지원) | W-1을 독립 WU로 분리. 반입 불가 시 동기 드라이버 + `asyncio.to_thread` 래핑(DB2 선례 `db.py:56`)로 폴백 |
| 물리 컬럼명 미확정(§3.5-a) | **G-4 없이 W-6 착수 금지.** 한글 컬럼명으로 SQL을 쓰면 인용 규칙까지 얽힌다 |
| 테이블명 대소문자(§3.5-b) | D-057 선례대로 **실 DB 조회로 확정**하고 프로필에 못 박는다 |
| 코드값 미확보(§3.5-d) | 코드 마스터 테이블이 있으면 조인, 없으면 프로필 `column_values`에 수기 등재. 없으면 코드 컬럼 필터 질의는 비범위 |
| T5 경계 미확정으로 답이 흔들림 | G-8을 **활성화(W-12) 전에** 반드시 닫는다 |
| 폴스타 질의 품질 희석 | 라우팅 후보가 늘면 오분류 가능. W-8 골든셋에 **경계 혼동 케이스**를 반드시 포함 |
| 자산 DB 부하 | MCP 소스 `query_timeout`·`max_rows`가 서버측 상한(클라이언트 설정 아님). 초기값은 기존 소스와 동일(30s/10000행) |
| 과금 | 라우팅·text2sql 평가 실행은 실 LLM 호출 — **D-127 건별 승인 + `RUN_E2E=1`** 뒤에만 |

**비범위**

- ITSM DB 서빙 개방(`itsm`도 등록만 된 상태지만 본 계획은 자산관리만. 같은 절차가 재사용된다).
- 자산 데이터 쓰기·동기화(읽기 전용만, D-003).
- 루트 `.env`의 `ITAM_DB_CONNECTION` 채우기(실측 ② — 읽는 코드 0건).
- `cloud_portal` 서빙 개방 · 자산 기반 자동 조치.
- `TCDMSIF79`·`TCDMSIF80` 외 테이블(§3.5-g — 제공 시 범위 확장).

---

## 8. 사용자 확정 게이트

> **G-1(엔진)은 제공 스키마로 종결** — **MariaDB**. 아래는 잔여 게이트다.

| # | 질문 | 왜 막히는가 | 기본 가정(답 없으면 이걸로 진행) |
|---|---|---|---|
| **G-2** | 실 **database(스키마)명**은? 시트의 `INST1`이 그것인가, 아니면 논리 인스턴스명인가? MariaDB **버전**은? | `db_schema` 값 · 드라이버 호환 | 없음 — 답 없이 연결 불가 |
| **G-3** | **읽기 전용 계정** 발급과 `mcp_server` VM → 자산 DB **네트워크 경로**가 가능한가? | D-003 · 폐쇄망 방화벽 | 열려 있다고 가정하지 않는다 |
| **G-4** | **물리 컬럼·테이블 식별자**는? (`sevrHostName` / `SEVR_HOST_NAME` / 한글 중 무엇인가, 테이블명은 대문자인가) | §3.5-a·b — **SQL을 못 쓴다** | 없음 — **W-6 착수 불가**. `SHOW CREATE TABLE TCDMSIF80` 한 줄이면 끝난다 |
| **G-5** | **코드값 목록**(9종 코드 컬럼) 또는 코드 마스터 테이블은? | §3.4-③ — WHERE 조건을 못 만든다 | 코드 필터 질의는 1차 비범위 |
| **G-6** | 자산 `sevrHostName`/`iPCtnt`가 폴스타 `hostname`/`ipaddress`와 **정확 일치**하는가? (샘플 5건 대조 요청) | 교차 질의(트랙 E)의 전제 | 불일치 가정 → 단독 질의만 1차 서빙 |
| **G-7** | 자산 DB는 **존 무관**인가? 존 RBAC에서 누가 볼 수 있는가? | T2·T3 처분 방향 | 존 무관 유지 + T2는 "잔여 그룹으로 실어 탈락 방지" |
| **G-8** | **★T5 경계** — "서버 CPU 사용률"을 물으면 폴스타와 자산 중 어디가 답해야 하는가? (§4.1 (가)/(나)/(다)) | 답이 조용히 달라진다 | **(가) 폴스타 우선 · 자산은 계약·금액·EOL·자산상태 축만** |
| **G-9** | **민감 컬럼 취급** — 금액 4종(`acqsiAmt`·`manmenCnpr`·`rmainAcbkAmt`·계약명)·성명(`rspblPsnEmnm`)을 조회 결과에 노출하는가? 조회 계정이 `rspblPsnEmnm` 평문을 보는가? | 마스킹·PII 필터 차단(§4.5) | 성명 마스킹 · 금액은 노출하되 감사 로그 강화 |
| **G-10** | **서버현황조회 쿼리문** 재제공 — 첨부 6장은 전부 컬럼 정의 시트였고 쿼리문은 보이지 않았다 | 실제 조회 패턴이 `query_examples`의 최상급 재료 | 없으면 우리가 §6-1의 3종으로 대체 |
| **G-11** | `TCDMSIF*`의 **IF**는 연계 테이블인가? 적재 주기와 행수는? 원장 테이블이 따로 있는가? | 사용률 신선도 → G-8 판단 근거 · §3.5-f | 연계 스냅샷으로 가정(→ G-8 (가) 보강) |
| **G-12** | 활성화 범위 — `ACTIVE_DB_IDS`에 `itam`을 운영에 바로 넣는가, 스테이징 선행인가? | 멀티 DB 모드 전환의 폴스타 회귀 위험 | **스테이징/로컬 선행 후 운영** |

---

## 9. 신규 결정 예약 — D-214

**제목(예정)**: 자산관리(ITAM) MariaDB 편입 — 세 번째 엔진 지원 + 편입 체크리스트 정정 + 관측/자산 답변 경계

**담을 내용**:
1. **편입 체크리스트 정정** — D-131의 "레지스트리 + `.env` 2파일"은 **본체 코드 수정 0**이며 **「등록 엔진이 이미 지원될 때」**의
   명제다. `DB_BACKEND=dbhub`의 설정 파일은 4개이고, 미지원 엔진이면 그 앞에 `mcp_server` 코드 작업이 선다(§2).
2. **세 번째 엔진(MariaDB) 지원 경계** — 코드 변경은 `mcp_server/`에 가두고 `src/`는 레지스트리 `engine` 값만 바꾼다.
   표현식 방언은 프로필 `query_guide`(프롬프트 층)로 먼저 대응하고, 반복 실패 실측 후에만 `src/db_adapters/itam/`(D-089)로 내린다.
3. **관측/자산 답변 경계(T5)** — 같은 지표를 두 DB가 가질 때 정본을 하나로 못 박는다(권고: 폴스타). 경계는 레지스트리
   `description` 재작성으로 풀고 키워드 사전은 추가하지 않는다(D-004).
4. **존 미배정 DB 처분** — `partition_execution_groups`가 `zone_group` 없는 db_id를 건너뛰어 침묵 탈락시키는 동작의 처분(G-7).
5. **활성화 순서 불변식** — 스키마 지식 정본화(W-6) 전 `ACTIVE_DB_IDS` 활성화 금지(T4).

등재 시 `docs/02_decision.md`의 `## D-` 헤더·「변경 이력」·「채번 이력」 표를 재확인하고 최댓값+1을 재부여한다(예약 소진 가능).

---

## 10. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-09-11 | 최초 작성. 해석 정정(등록은 완료 — 막힌 것은 MCP 소스·스키마 지식·활성화) · 실측 6건 · 함정 T1~T4 · 인테이크 양식 · 트랙 A~F · 게이트 G-1~G-9 · D-214 예약 |
| **v2** | 2026-09-11 | **사용자 제공 스키마 반영** — ①**엔진 MariaDB 확정**(G-1 종결) → 트랙 **A0(세 번째 엔진 지원)** 신설·계획 성격 변경 ②`TCDMSIF80` 68컬럼·`TCDMSIF79` 9컬럼 전수 판독(§3) ③**함정 T5 신설**(자산 DB에도 CPU·메모리·스토리지 사용률이 있어 폴스타와 답변 영역 충돌) ④스키마가 만든 결정적 규칙 5건(§3.4 — `CHAR(8)` 날짜 · 3열 복합 PK · 코드값 부재 · 금액/성명 민감 · `VARCHAR(300)` 호스트명) ⑤미제공 7건(§3.5) → 게이트 G-2~G-12로 재구성(물리 컬럼명 G-4 · 코드값 G-5 · 경계 G-8 · 쿼리문 재요청 G-10 · IF 테이블 성격 G-11) ⑥WU 15건으로 재분해 |
