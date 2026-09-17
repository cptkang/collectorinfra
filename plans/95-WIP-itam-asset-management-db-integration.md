# 95. 자산관리(ITAM) DB 연동 — MariaDB 방언 지원 + 서빙 개방 + 스키마 지식 정본화

> **작성일**: 2026-09-11 · **v2**(2026-09-11): 사용자 제공 스키마 실측 반영 — **엔진 = MariaDB 확정**(G-1 종결),
> `TCDMSIF80`(68컬럼)·`TCDMSIF79`(9컬럼) 전수 판독, 함정 T5(폴스타와 사용률 컬럼 정면 중복) 신설
> **v4**(2026-09-17): **로컬 테스트용 MariaDB 도커 샌드박스**(트랙 S · §4.6) 편입 — 사용자 지시 *"95번 계획에 로컬 테스트용
> 마리아 DB를 도커에 설치 구성하는 계획을 포함시켜라."* · 게이트 G-13(운영 서버 변수) 신설
> **v5**(2026-09-17): **1차 구현** — 사용자 지시 *"95번 계획을 구현하라."* 게이트와 무관한 WU를 랜딩하고
> 게이트에 걸린 WU는 차단 사유를 실측으로 적었다(§5.1). 파일명 `-TODO` → `-WIP`
> **v6**(2026-09-17): 사용자 요구(자산관리·폴스타 조회 구분 · 양방향 키 기반 연쇄 · 키는 데이터 내용으로 판단 · 필요 시 양쪽 조회 후 선택)로
> **트랙 C·E를 `plans/102`로 승계**(답변 영역 소유 · 값 기반 키 브리지 · 식별자 소재 프로브 · D-224 예약) · G-8 방향 (가) 사용자 확인(서버 사양만 102 G-1로 이관) ·
> W-4 로컬 연결 완료(`mcp_server/.env` `ITAM_CONNECTION` → 로컬 샌드박스 · 본체용 MCP 9099 재기동)
> **v7**(2026-09-17): 사용자 기준 *"기본은 시멘틱 라우터를 사용한다. 모든 동작은 시멘틱 라우터에서 동작되어야 한다. deepagents는 부가적으로 사용할 예정"*에 따라
> **검증 기준 경로를 사다리 1단(`deep_agent`) → 3단(`semantic_router`)으로 변경**(§0.2 ⑥ · W-12 · §6-2 — `plans/102` v2 트랙 L-6 · D-225 예약)
> **v8**(2026-09-17): 사용자 지시 *"95번 계획을 구현하라."* 재실행 — 게이트 없이 남은 **W-14 문서분**(`docs/18` 편입 체크리스트 정정 · D-214 상태를 v6 사실에 동기화)을 랜딩하고,
> v5 산출물 무회귀(단위 47 · `mcp_server` 247 · `RUN_DOCKER_IT=1` 5/5)를 재실측했다. **W-9 차단 사유를 정정**(비트 동일 불가는 수정 위치의 문제 — §5.1) · W-10 게이트 위치를 3단 기준으로 재실측
> **v9**(2026-09-17): 사용자 답변 *"자산관리는 존이 없고 1개의 시스템에서 모든 자산을 관리한다. 라우팅 평가는 mlx에서 진행하라."* — **G-7 확정 → W-9 구현**(실행기 한정 잔여 그룹 · 플래그 없음 · 비트 동일 매트릭스 대조) ·
> W-10은 요건만 확정(존 선택과 무관하게 itam 유지)하고 `plans/102` X-7 이후로 보류 · **W-8 경계 라우팅을 mlx 로컬 LLM으로 판정**(r-060~r-064 × 3 전건 일치·위반 0 · 전체 17/18)
> **v10**(2026-09-17): 사용자 지시 *"mlx는 과금 없으니 w-8은 진행하라. w-12는 itam db를 등록하고 13,14번 진행하라."* — **§5 순서 불변식(D-214 ⑤)을 사용자 결정으로 대체해 W-6 전 로컬 활성화를 검증**(프로세스 env 주입 · 루트 `.env`는 13:54 적용 후 main go 대기 조건으로 14:40 원복 — 반영은 go 대기) ·
> W-8 활성화 후 실노드 경계 판정 완료 · **W-13 회귀 0**(폴스타 단독 6질의 × 전 2·후 2, 노이즈 바닥 0) · 알려진 결과 T4 관찰(전역 유사어 오염 — 지정 삭제로 정리) · W-14 문서
> **v11**(2026-09-17): 사용자 결정 *"C로 진행하라. mlx 서버는 그대로 둬라."* — **수동 프로필 없는 DB의 LLM 유사어 전역 전파를 쓰기 지점에서 차단**(D-228 · W-12a) 후 **루트 `.env` 재적용(14:52:37 · W-12 ✅ 로컬)**. 재현에서 `synonyms:global` 124→124 · 차단 로그 1줄 · v10 보류 사유 문구를 실제 사유(전역 유사어 오염 처분 대기)로 정정
> **v12**(2026-09-17): 사용자 지시 *"db 관련 정보는 104번 계획에서 조사하여 자동으로 스키마, 캐시 등을 생성하는 기능을 구현할 계획이다. 이에 맞게 처리하라."* — **DB 정보 조사를 `plans/104` 관리자 흐름으로 이관**(§5.2): 물리 식별자(G-4)·코드값(G-5)·서버 변수(G-13)·`db_schema` 후보(G-2 일부)를 사용자에게 묻지 않고 104가 MCP로 수집·생성한다. W-6·W-7(수동 프로필·지식·시드)은 104 O-2~O-8(스키마 수집·캐시 등록 → 설명·유사어 초안 → 구조 분석 승인본 → 프로필 골격 조각)로 대체하고, 양식 LLM 매핑 자동 등록 처분(v11 보류)도 104 G-11로 넘긴다. 운영 활성화 조건은 104 준비도 C1~C7(D-214 ⑤ 대체). 코드 변경 0
> **v13**(2026-09-17): 사용자 결정 *"W-11은 그대로 보여주면 된다."* — **G-9 확정: 금액 4종·성명을 조회 결과에 마스킹 없이 노출** → W-11 종결(구현 불필요). FabriX PII 필터는 「이름」을 탐지하되 차단하지 않고 금액은 탐지 유형이 아니다(`docs/pii_filtering_rules.md:6,222`). 코드 변경 0
> **성격**: 구현 계획 · **상태: 부분 구현 — 트랙 A0(W-1~W-3)·W-4 소스 정의·W-5 엔진 값·트랙 S(W-S1~W-S3)·W-8·W-9·W-12(로컬)·W-12a·W-13 랜딩, 잔여는 W-4 운영 연결(G-3)·W-10(`plans/102` X-7) — **v13: W-11 종결(G-9 노출 확정)** · **v12: W-5 `db_schema`·W-6·W-7과 G-4·G-5·G-13 조사는 `plans/104`로 이관**, 운영 활성화는 104 준비도 C1~C7 충족 후(§5.1·§5.2·§8)**
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
| ⑥ | **활성화가 실행 경로를 바꾸지는 않는다** — 사다리 2·3단은 tri-state이나 운영 `.env`가 `ENABLE_SEMANTIC_ROUTING=true`(:158)·`ENABLE_INTENT_ORCHESTRATION=true`(:178)·`ENABLE_DEEPAGENTS_PACKAGE=true`(:185)로 명시 고정 | 사다리 재판정 리스크 없음(1단 `deep_agent` 유지). 단 **검증은 1단 경로에서** 해야 한다(§6) · **v7: 검증 기준은 3단 `semantic_router`**(사용자 기준 2026-09-17 · `plans/102` §3.7). 운영 `.env`가 아직 1단이므로 검증 기동은 `ENABLE_DEEPAGENTS_PACKAGE=false`·`ENABLE_INTENT_ORCHESTRATION=false`를 명시하고, 미입력 상태의 itam 활성화는 2단 자동 확정을 부른다(102 X-T11 — 102 L-1 선행) |
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

### 3.2 전사 정본 — `testdata/itam/schema.yaml`

**판독 결과의 단일 출처는 계획서가 아니라 스키마 파일이다**(사본 금지 — 두 곳에 적으면 한 곳이 늙는다).

- 파일: [`testdata/itam/schema.yaml`](../testdata/itam/schema.yaml) — 68+9컬럼 **전수** · 표준 코드 도메인 10종 ·
  조인 키 · 민감 컬럼 · 공통 규약 · `unresolved` 8건(§3.4 및 §8 게이트에 대응 — 제약·인덱스 2건은 게이트 없이 파일에만 있다).
- 성격: **전사본이지 정본(profile)이 아니다.** 런타임은 이 파일을 읽지 않는다 — 구조 정본 `config/db_profiles/itam.yaml`은
  물리 컬럼 식별자가 확정된 뒤(G-4) 이 파일을 재료로 만든다(W-6).
- 위치 근거: 비폴스타 DB 스키마 파일의 선례는 `testdata/generic_mon/schema.json`이다(Plan 63 P4-2). 형식만 YAML로 —
  한국어 컬럼 정의·미확정 표기·코드 도메인 주석을 담아야 하는데 JSON은 주석을 못 쓴다.
- 무결성 검증 완료(로드 후 단언): 컬럼 수 68/9 · 시트 순번 연속 · `var` 중복 0 · PK 순서 일치 ·
  코드 도메인 참조 무결 · 조인/민감/교차키 컬럼 실존.

| 테이블 | 컬럼 | PK(3열 복합) | 담는 축 |
|---|---|---|---|
| `TCDMSIF80` | 68 | `groupCoCd` + `sevrHostName` + `iPCtnt` | 서버 현황·자산 상세 — 가상화·OS·분류·**사양/사용률 11**·담당(PII)·물품·구매·유지보수·자산 회계·자산 분류·무상·시리얼·감사 |
| `TCDMSIF79` | 9 | **동일 키**(조인 가능) | HW/SW **지원 종료일(EOS/EOL)** — `hWSportEndYmd`·`sWSportEndYmd` |

> **전사 중 밟은 YAML 함정 3건** — `no:`·`on:`이 YAML 1.1 **불리언 키**로 파싱되고(→ `seq:`·`on_columns:`),
> flow 매핑 안의 `type: DECIMAL(12,2)`는 **쉼표가 항목 구분자로 먹혀** 값이 잘린다(인용 필수).
> 셋 다 **파일은 정상 로드되고 값만 조용히 틀어지는** 부류라, 스키마 파일은 반드시 로드 후 무결성 단언으로 검증한다
> (`docs/18_known_mistakes.md` 등재).

### 3.3 이 스키마가 만든 결정적 규칙 5건 (프로필 `query_guide`에 그대로 들어간다)

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

### 3.4 아직 없는 것 (✖ — 게이트 대상)

| # | 없는 것 | 왜 필요한가 |
|---|---|---|
| a | **물리 컬럼명** — 시트는 「컬럼명(한글)」과 「변수명(camelCase)」만 준다. 실 DDL 식별자가 `sevrHostName`인지 `SEVR_HOST_NAME`인지 한글인지 불명 | SQL을 못 쓴다. **G-4 최우선** |
| b | **테이블명 대소문자** — 파일명은 `tcdmsif80`, 시트 값은 `TCDMSIF80`. Linux MariaDB는 `lower_case_table_names=0`이면 **대소문자를 구분**한다 | DB2 `POLESTAR` 대문자 사건(D-057)과 같은 계열. 틀리면 전 질의가 "테이블 없음" |
| c | **database(스키마)명** — `INST1`이 실 database명인지 | `db_schema` 값 |
| d | **코드값 목록/코드 마스터 테이블** | §3.3-③ |
| e | **서버현황조회 쿼리문** — 제공 이미지 6장은 **전부 컬럼 정의 시트**였고 쿼리문은 보이지 않았다 | 실제 조회 패턴(조인·필터·정렬)이 프로필 `query_examples`의 최상급 재료다. **재요청 필요** |
| f | **행수·갱신 주기** — `TCDMSIF*`의 `IF`가 연계(interface) 테이블을 시사한다. 원장이 따로 있는지, 적재 주기가 언제인지 | 사용률 데이터의 신선도 → T5 우선순위 판단의 근거 |
| g | 그 외 테이블 목록 | 지금 아는 것은 2개뿐. 자산 대장 전체를 답하려면 더 필요할 수 있다 |

---

## 4. 트랙

| 트랙 | 한 줄 | 선행 | 산출물 |
|---|---|---|---|
| **A0. MariaDB 방언 지원** | `mcp_server`에 세 번째 엔진을 넣는다 | — (지금 착수 가능) | 드라이버 의존성 · `db.py` 실행기 1종 · `tools.py` 인트로스펙션 헬퍼 4종 · `config.py` 타입 · 테스트 |
| **A1. 연결 개방** | MCP 소스 정의 + 읽기 전용 계정 + 기동 확인 | A0 · G-2·G-3 | `config.toml` 소스 1개 · `mcp_server/.env` 1키 · `list_sources`·`describe_table` 응답 |
| **S. 로컬 MariaDB 샌드박스** | 도커에 MariaDB를 띄워 A0·D·E·F를 운영 접근 없이 실연결로 검증한다(§4.6) | — (지금 착수 가능 · G-4는 가정으로 진행) | `testdata/itam/` 컴포즈 · 전사본 파생 init SQL · SELECT 전용 계정 · `setup.sh` · `RUN_DOCKER_IT=1` 통합 테스트 |
| **B. 스키마 지식 정본화** | §3 실측 → 수동 프로필(+지식·시드) | G-4·G-5 | `config/db_profiles/itam.yaml` · `knowledge/itam/` · `synonym_seeds/itam.yaml` |
| **C. 라우팅 경계(T5)** | 폴스타와 겹치는 「사용률」을 어느 DB가 답하는가 | G-8 | 레지스트리 `description` 재작성 · 라우팅 골든셋 경계 케이스 · **v6: 답변 영역 소유표·소유 센서로 확장해 `plans/102` 트랙 R로 승계** |
| **D. 존·RBAC·마스킹** | 존 무관 DB의 그룹 탈락(T2)·HITL 비발동(T3) 처분 + 금액·성명 마스킹 | G-7·G-9 | `execution_groups` 처분(코드) · `sensitive_columns` 보강 · `default_allowed_db_ids` 방침 |
| **E. 교차 질의** | 폴스타 ↔ 자산 결과 결합(조인 불가 — 키 병합) | G-6 | 호스트 키 정합 규약 · 멀티 DB 결과 병합 확인 · **v6: 값 기반 키 판정·키 브리지 매니페스트·매칭 등급·소재 프로브로 구체화해 `plans/102` 트랙 K·P로 승계** |
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
| 호스트명 | `sevrHostName` `VARCHAR(300)` | `hostname` / `name` | 길이 300은 FQDN·별칭 혼입 가능(§3.3-⑤) |
| IP | `iPCtnt` `VARCHAR(255)` | `ipaddress` | 다중 IP가 한 칸에 들어갔을 수 있음 |

**G-6의 답이 트랙 E의 존재 여부를 결정한다.** 정합하지 않으면 트랙 E를 별건으로 분리하고 단독 질의부터 서빙한다.

### 4.4 솔루션 축 등재 여부

`itam`은 관측 솔루션이 아니다(자산 원장). `config/db_registry.yaml`의 `solutions:`에 넣으면 실행 그룹 순서 축에 들어오는데,
**존 그룹이 없어 그룹이 만들어지지 않는다**(T2). 따라서 **1차에서는 등재하지 않는다** — 등재는 T2 처분(G-7) 이후 별건.

### 4.5 마스킹·PII

현재 마스킹 기본값은 비밀·토큰·카드번호 계열 13종(`src/config.py:400-412`)이고 `mask_ip`·`mask_email`은 기본 off다.
자산 DB는 **금액 4종 + 성명 1종**을 실제로 갖고 있으며(§3.3-④), 이는 ①화면 노출 ②**FabriX PII 필터 차단**
(`src/security/pii_filter.py` · `docs/pii_filtering_rules.md`) 양쪽에 걸린다 — 후자는 질의가 **응답 없이 막히는** 증상이다.
`rspblPsnEmnm`은 원천에서 암호화 대상으로 표시돼 있으므로 **조회 계정이 평문을 보는지 자체가 확인 대상**이다(G-9).

### 4.6 트랙 S — 로컬 MariaDB 샌드박스(도커)

> 사용자 지시(2026-09-17): *"95번 계획에 로컬 테스트용 마리아 DB를 도커에 설치 구성하는 계획을 포함시켜라."*

#### 4.6.1 왜 필요한가 — 운영 접근 없이는 A0를 목으로만 검증하게 된다

- **운영 자산 DB는 아직 닿지 않는다** — 네트워크 경로·읽기 전용 계정이 미확정(G-3)이라 W-2·W-3의 verify가 **목 커넥션**에
  머문다. mock 통과 ≠ 프로덕션 동작이고, W-3의 핵심 단언 *"`information_schema` 결과 shape가 PG/DB2 헬퍼와 동형"*은
  실 엔진 없이 판정할 수 없다.
- **DB2의 전철을 밟지 않는다** — DB2 실 검증은 로컬 픽스처 부재로 무기한 보류됐다(D-126 ①). MariaDB 공식 이미지는 arm64를
  네이티브로 지원해 DB2 컴포즈(`db2/docker-compose.yml` — `platform: linux/amd64`·`privileged: true`·메모리 4g·
  `start_period: 600s`)의 제약이 없다. 같은 부채를 만들 이유가 없다.
  ※ D-126은 sre-agent M-D/R13 수용 기준의 **범위 한정**이지 로컬 픽스처 추가 금지가 아니다(충돌 없음 — 2026-09-17 대조).
- **활성화 전에 함정을 재현할 무대** — T1(PG 방언 주입)·§3.4-b(테이블명 대소문자)·T2(존 미배정 DB 침묵 탈락)·T5(사용률 출처)는
  전부 **실 MariaDB에 SQL이 도달해야** 양상이 보인다.

#### 4.6.2 ★로컬 샌드박스가 증명하는 것과 증명하지 못하는 것

로컬 DB는 **전사본(`testdata/itam/schema.yaml`)에서 파생**된다. 따라서 엔진 동작은 증명하지만 운영 사실은 증명하지 못한다.

| 증명한다 — 엔진 동작 | 증명하지 못한다 — 운영 사실(게이트로만 확정) |
|---|---|
| MariaDB 드라이버 연결·풀·타임아웃(W-1·W-2) | 물리 컬럼·테이블 식별자(G-4) |
| 인트로스펙션 결과 shape — 컬럼 수·PK 3열 순서(W-3) | 실 database명(G-2) |
| 읽기 전용 계정의 쓰기 거부 — D-003 DB층을 MCP `readonly`와 **독립으로** | 코드값 목록(G-5) |
| PG 방언 SQL의 실패 양상(T1) · 테이블명 대소문자 구분(§3.4-b) | 호스트 키 정합(G-6) |
| `CHAR(8)` 날짜 문자열 비교(§3.3-①) · 3열 복합 조인(§3.3-②) | 행수·적재 주기·신선도(G-11) |
| 교차 질의 결과 병합 · T2 침묵 탈락 재현 | 운영 서버 변수 — `sql_mode`·collation·`lower_case_table_names`(G-13) |

**파생 금지 규칙(순환 검증 차단)** — W-6 프로필·W-7 지식/시드는 **로컬 DB에서 추출하지 않는다.** 로컬 DB와 프로필이 같은
전사본에서 나오므로, 로컬 대조는 **전사본을 자기 자신과 비교**하는 일이다. W-6 verify의 *"실 DB `information_schema` 대조"*는
운영(또는 스테이징) 자산 DB로만 판정한다. 로컬 DB를 대상으로 돌린 자동 구조 분석(`source: auto`)·
`scripts/synonym_seeds.py derive` 산출물은 **커밋 금지** — 합성 코드값과 가정 식별자가 정본으로 승격된다
(전사본 헤더의 *"추정으로 채운 값을 넣지 말 것"*과 같은 규칙).

#### 4.6.3 구성 — 기존 픽스처 관례를 따른다(2026-09-17 실측)

| 항목 | 값 | 근거 |
|---|---|---|
| 위치 | `testdata/itam/` — `docker-compose.yml` · `init/` · `generate_init.py` · `setup.sh` · `README.md` | 폴스타 샌드박스 `testdata/pg/`(컴포즈 + `init/` + 생성기 `generate_full_schema.py` + README) 선례 · 전사본 `schema.yaml`과 같은 폴더 |
| 이미지 | `mariadb:11.4`(LTS) — **메이저 고정**, `latest` 금지 | 기존 `postgres:16-alpine` 고정 관례. 운영 버전은 미확정(G-2) — 확정되면 같은 메이저로 교체 |
| 컨테이너명 | `itam_mariadb` | `polestar_pg`·`infra_db2`·`infra_monitoring_db` |
| 포트 | `3307:3306` | PG·Redis·Prometheus 픽스처는 기본 포트를 비켜 둔다(5433·5434·6380·9190) — 로컬 설치 MySQL/MariaDB(3306)와 충돌 회피. 3306·3307 미점유 실측 |
| 데이터 볼륨 | named volume `itam_mariadbdata` — **bind mount 금지** | macOS APFS는 기본이 대소문자 비구분이라, 데이터 디렉터리를 호스트에 두면 테이블명 대소문자 동작이 운영(Linux)과 달라져 §3.4-b를 **재현하지 못한다**. named volume은 Docker VM의 Linux 파일시스템에 있다(`polestar_pgdata` 선례) |
| database | `INST1` — **가정** | 전사본 `naming.logical_schema`가 유일한 증거(`conf: check`). G-2 확정 시 컴포즈 환경변수 1곳 교체 |
| 서버 변수 | `--lower-case-table-names=0` · `--character-set-server=utf8mb4` · `--collation-server=utf8mb4_general_ci` · `sql_mode`는 이미지 기본값 유지 | 운영값 미확정(G-13) — 가정은 「Linux MariaDB 기본 동작」. 기본값에 기대지 않고 **명시로 고정**해 이미지 버전을 올려도 재현 조건이 흔들리지 않게 한다. 표준그룹 `MDB_UTF8`(§3.1)은 utf8 계열을 시사 |
| 헬스체크 | 이미지 내장 `healthcheck.sh --connect --innodb_initialized` | `setup.sh`의 준비 대기 루프와 짝(`db/setup.sh` 동형). 동작은 W-S2에서 실측 |
| 계정 | root(초기화 전용) · **`itam_ro`(SELECT 전용 — MCP가 쓰는 유일한 계정)** | 공식 이미지의 `MARIADB_USER`는 `MARIADB_DATABASE`에 **전체 권한**을 받는다 — 이 계정을 MCP 연결에 쓰면 D-003 DB층 검증이 **거짓 통과**한다. 읽기 전용 계정은 init SQL에서 `GRANT SELECT`로 따로 만든다 |
| 비밀번호 | 로컬 픽스처 전용 고정값 | 기존 `infra_pass_2024`·`polestar_pass_2024` 관례. 운영 자격과 무관 — 운영 연결 문자열은 `mcp_server/.env`에만 둔다 |

init SQL(`/docker-entrypoint-initdb.d`, 파일명 순 자동 실행):

| 파일 | 성격 | 내용 |
|---|---|---|
| `01_schema.sql` | **생성물** — `generate_init.py`가 전사본에서 파생 | `TCDMSIF80`(68)·`TCDMSIF79`(9) — 타입·NULL·3열 복합 PK. FK·인덱스는 넣지 않는다(전사본에 없음 — `unresolved`). 테이블명은 전사본 표기 그대로 대문자 |
| `02_seed.sql` | **생성물** — 같은 생성기 | 합성 데이터(§4.6.4) |
| `03_readonly_user.sql` | 수기 | `CREATE USER 'itam_ro'@'%'` · `GRANT SELECT ON INST1.* TO 'itam_ro'@'%'` |

- **DDL을 손으로 쓰지 않는다** — 68+9컬럼 정의가 전사본과 SQL 두 곳에 있으면 한 곳이 늙는다(§3.2 「사본 금지」). 생성물은
  커밋하되 **재생성 diff 0**을 단위 테스트로 고정한다(도커 불필요 — 기본 스위트에서 돈다). 생성기는 전사본을 로드한 뒤
  컬럼 수 68/9·PK 3열을 먼저 단언한다 — 전사 중 밟은 YAML 함정 3건(§3.2)은 **값만 조용히 틀어지는** 부류다.
- **★식별자 가정(G-4 미확정)** — 컬럼 식별자는 전사본 `var`(camelCase)를 그대로 쓴다. 생성물 헤더에 *"G-4 미확정 — 전사본
  `var` 가정"*을 박고, 식별자 변환은 생성기의 **한 함수**에 가둔다. G-4가 `SEVR_HOST_NAME`류로 확정되면 그 함수만 바꿔
  재생성한다. 전사본의 `physical_column_style: null`은 **건드리지 않는다**(가정을 전사본에 쓰면 추정이 정본이 된다).

#### 4.6.4 합성 시드 — 정답을 알고 있는 데이터

**운영 자산 데이터는 로컬로 반입하지 않는다**(금액 4종·성명 포함 — §3.3-④). 전부 합성하고, 각 행이 **어떤 질의의 경계를
시험하려고 들어갔는지** 생성기 주석에 남긴다.

| 설계 축 | 규칙 | 겨냥 |
|---|---|---|
| **날짜 = init 시점 상대값** | 고정 리터럴 대신 `DATE_FORMAT(CURDATE() + INTERVAL n MONTH, '%Y%m%d')`로 적재(분기 경계는 `QUARTER(CURDATE())`로 계산). 질의마다 경계 **안·밖·정확히 경계** 행을 둔다 | §6-1 질의 3종이 달력이 흘러도 기대 결과를 유지한다. 고정 리터럴이면 몇 달 뒤 기대 행이 **조용히 0건**이 된다. 컨테이너를 오래 두면 다시 늙으므로 `setup.sh`는 `down -v`로 재생성한다(`db/setup.sh` 관례) |
| **호스트 키 4형** | 폴스타 샌드박스에 실재하는 호스트명(`svr-web-01` 등 — `testdata/pg/init/02_insert_cmm_resource.sql`)으로 ①정확 일치 ②대소문자만 다름 ③FQDN ④한 칸에 다중 IP(`10.0.1.1,10.0.1.2`)를 섞는다 | G-6 답이 어느 쪽이든 트랙 E 병합이 두 경우를 다 겪는다. collation이 `_ci`면 ②가 `=`로 **일치해 버리는지** 자체가 관찰 대상(G-13과 짝) |
| **사용률 판별값(T5)** | `sevrCPUUseRt`·`sevrMmryUseRt`·`wholStrgeUseRt`를 식별 패턴(예: 소수부 `.37` 고정)으로 채우고, 폴스타 샌드박스 값과 겹치지 않는지 1회 확인 | 성공 기준 4(경계 일관성)를 로컬에서 판정할 때 **응답 숫자만으로 어느 DB가 답했는지** 가린다 |
| **합성 코드값 표식** | 코드 컬럼(`CHAR(1)`·`CHAR(2)`·`CHAR(4)`)은 실코드와 섞이지 않게 `Z`·`Z9`·`Z999`류 표식으로 채운다 | G-5 미확정 — 합성값이 프로필 `column_values`로 새어 들어가면 **표식으로 즉시 들킨다**(§4.6.2 파생 금지의 결정적 탐지) |
| **민감 컬럼** | 금액 4종은 합성 수치, `rspblPsnEmnm`은 성명 **형태**의 합성값(실존 인물 아님) | W-11(마스킹·`scripts/pii_regex_check.py`)이 성명 패턴을 실제로 만나게 한다 |
| **79 짝 누락 행** | `TCDMSIF80` 일부 행은 `TCDMSIF79` 짝을 두지 않는다 | 3열 복합 조인의 INNER/LEFT 차이가 결과 행수로 드러난다 |
| 규모 | `TCDMSIF80` 30행 안팎 | 판정은 행수가 아니라 경계 행으로 한다 — 크게 만들 이유가 없다 |

**정답 고정** — §6-1 질의 3종에 대해 **사람이 쓴 SQL**과 기대 행집합을 `testdata/itam/README.md`에 고정한다. 파이프라인과
무관한 **로컬 오라클**이다. 날짜가 상대값이므로 기대 행집합은 날짜가 아니라 **호스트명(= 어느 설계 행인가)**으로 적는다.

#### 4.6.5 배선 — 소스명은 로컬·운영이 하나를 공유한다

| 층 | 로컬 값 | 근거 |
|---|---|---|
| `mcp_server/config.toml` | `[[sources]] name = "itam"` · `type = "mariadb"` — **로컬·운영 공용 정의 1개** | 폴스타 선례 — `name = "polestar"` 하나가 *"개발: Docker :5434 PostgreSQL / 운영: 실제 DB2"*(`config.toml:80`)를 겸한다. `itam_local` 같은 별도 소스명은 레지스트리 `db_id`·`ACTIVE_DB_IDS`·프로필 파일명을 전부 이원화한다 |
| `mcp_server/.env` | `ITAM_CONNECTION=` `itam_ro` @ `localhost:3307` / `INST1` | 연결 문자열 **형식은 W-1 드라이버 선정에 종속**(DSN 해석은 W-2 소관) |
| `mcp_server/.env.example` | 로컬 예시 주석 1줄(`POLESTAR_CONNECTION` 로컬 예시 `:86` 동형) + `:101-102` *"ITAM_CONNECTION은 두지 않는다"* 주석 교체 | **W-4(소스 추가)와 같은 커밋**이어야 한다 — `test_no_ghost_connection_keys`(`mcp_server/tests/test_env_example_coverage.py:123`)는 소스 없는 `*_CONNECTION` 예제 키를, `test_defined_sources_have_documented_connection_key`(`:114`)는 소스에 대응하는 키의 누락을 실패시킨다. 어느 한쪽만 먼저 넣으면 테스트 하나가 깨진다 |
| 루트 `.env` | **W-12 전에는 건드리지 않는다** | §5 순서 불변식은 로컬에도 적용된다. W-12 전 로컬 검증은 **MCP 계층 직접 호출까지**(`list_sources`·`describe_table`·`execute_sql`). 본체 파이프라인 경유 리허설(T2·T5 재현)은 W-6 이후, `ACTIVE_DB_IDS` 수정은 사용자 확인 후 |

#### 4.6.6 테스트 게이트 — 기존 Docker 통합 테스트와 동형

- **옵트인 스위치는 `RUN_DOCKER_IT=1`** — `TestDockerIntegration`(`mcp_server/tests/test_polestar_tools.py:525-529`)과 같은 스위치다.
  새 환경변수를 만들지 않는다. 미옵트인 skip 사유에 픽스처 기동 명령(`testdata/itam/setup.sh`)을 적는다.
- **연결은 env 주입** — 하드코딩 금지(`plans/66` 2026-07-28 이력).
- **옵트인했는데 미도달이면 skip이 아니라 실패** — `:508-512` 선례(*"PG 픽스처 미도달 — dsn=… 확인"* `RuntimeError`).
  조용한 skip은 D-181에서 테스트를 전건 skip 통과로 무력화한 경로다.

단언(W-S3):

1. 인트로스펙션 — `TCDMSIF80` 68컬럼 · `TCDMSIF79` 9컬럼 · PK 3열 **순서**(`groupCoCd`→`sevrHostName`→`iPCtnt`).
2. `search_objects`·컬럼 조회 결과의 키 집합이 PG 헬퍼 결과와 **동일**(W-3 shape 동형).
3. `itam_ro`로 `INSERT`·`CREATE TABLE` → **DB 권한 오류** — MCP `readonly` 검증을 거치지 않는 드라이버 직접 경로로 실행해
   3중 방어의 DB층을 **독립으로** 확인한다.
4. 소문자 `tcdmsif80` 조회 → 테이블 없음 오류 — §3.4-b 재현이자 `lower_case_table_names=0`이 실제로 걸렸는지의 확인.
5. `execute_sql` 반환 행의 `DECIMAL`·`CHAR` 값이 PG 경로(`_normalize_row` — `db.py:112`)와 같은 파이썬 타입으로 정규화된다.

**리허설 기록(단언 아님 — W-6 `query_guide` 방언 규칙 블록의 재료)** — PG 방언 SQL 4종(`::numeric` 캐스트 · `'a' || 'b'` 결합 ·
`INTERVAL '1 day'` · `"col"` 인용)을 로컬에 흘려 **오류로 끝나는 것과 조용히 다른 값을 내는 것**을 가른다. `||`(OR로 해석)와
`"col"`(`ANSI_QUOTES`가 없으면 문자열 리터럴)은 오류가 아니라 **값**을 낼 수 있다 — 그러면 `query_executor` SQL 에러 회귀조차
발동하지 않는 **침묵 오답**이므로, 이 둘은 프롬프트 규칙만으로 두지 말고 반복 실패 실측 시 결정적 교정 후보(§4.2)로 올린다.
**v5 결과(2026-09-17 · 11.4.13 · 기본 sql_mode)**: `::numeric`·`INTERVAL '1 day'`는 구문 오류, `||`는 `0`, `"col"`은 문자열
리터럴(필터 0행) — 예측대로 침묵 오답 2종이 확인됐다. 결과표 정본은 `testdata/itam/README.md` 「리허설 기록」이다(사본 금지).

---

## 5. 작업 분해

| WU | 작업 | 선행 | verify |
|---|---|---|---|
| **W-0** | G-2~G-12 확정(사용자 인터뷰) | — | 답이 본문에 기록됨 |
| **W-1** | MariaDB 드라이버 선정·의존성 추가 | — | `mcp_server` 임포트·기동 무회귀 |
| **W-2** | `db.py` — 초기화·실행 3분기 + `_execute_mysql()` | W-1 | 단위 테스트(목 커넥션) · 기존 PG/DB2 경로 회귀 0 · 실연결은 W-S3 |
| **W-3** | `tools.py` — `_mysql_search_objects_sql`·`_get_columns`·`_get_primary_keys`·`_get_foreign_keys` | W-1 | `information_schema` 질의 결과 shape가 PG/DB2 헬퍼와 **동형**임을 단언 — 실 엔진 판정은 W-S3 |
| **W-4** | `config.py` 타입 허용 + `config.toml` 소스 + `mcp_server/.env` 연결 + `.env.example` 등재(같은 커밋 — §4.6.5) | W-2·W-3 · G-2·G-3 | 기동 로그 소스 등재 · `list_sources`에 `itam` · `describe_table TCDMSIF80` 68컬럼 반환 |
| **W-5** | 레지스트리 `engine: mariadb` · `db_schema` 확정 | G-2 | `pytest tests/test_semantic_routing/test_registry_config.py` 그린 |
| **W-6** | `config/db_profiles/itam.yaml` 작성 — 재료는 `testdata/itam/schema.yaml`(§3.2) + 방언 규칙 블록 + 결정적 규칙 5건 | G-4·G-5 · W-4 | 실 DB `information_schema` 대조로 **컬럼 전수 일치**(미리보기 일부 금지) |
| **W-7** | `knowledge/itam/catalog.yaml` + `synonym_seeds/itam.yaml` | W-6 | `scripts/catalog_diff.py` 동등 · 시드 로드 건수 |
| **W-8** | 트랙 C — `description` 재작성 + 경계 골든셋 | G-8 | 경계 케이스에서 기대 DB 선택 |
| **W-9** | T2 처분 — 존 미배정 DB의 실행 그룹 취급 | G-7 | 폴스타+itam 대상에서 **itam 미탈락** 단언 |
| **W-10** | T3 처분 — 혼합 질의 존 HITL 기대 동작 | G-7·G-8 | 단위 테스트로 고정 |
| **W-11** | 마스킹·PII 반영(금액 4·성명 1) | G-9 | 마스킹 단언 + `scripts/pii_regex_check.py` |
| **W-12** | `ACTIVE_DB_IDS`에 `itam` 추가(활성화) | W-4~W-11 · **v7: `plans/102` L-1** | 기동 로그 사다리 1줄 동일 — ~~1단 `deep_agent` 유지~~ **v7: 활성화 전후 확정 단이 같을 것(기준 3단 `semantic_router`)** |
| **W-13** | 회귀 판정 — 폴스타 단독 질의 동작 불변 | W-12 | `plans/94` 하네스 무과금 경로 + 기존 골드셋 대비 회귀 0 |
| **W-14** | 문서 — D-214 등재 · `docs/18` 체크리스트 정정 · INDEX · CLAUDE.md 「데이터 도메인」 표에 로컬 `itam` 샌드박스 행 | 전건 | 링크·번호 실존 확인 |
| **W-S1** | `testdata/itam/generate_init.py` — 전사본 → `init/01_schema.sql`·`02_seed.sql` 파생(§4.6.3·§4.6.4) | — (지금 착수 가능 · G-4는 가정) | 재생성 diff 0 단위 테스트(도커 불필요) · 전사본 로드 후 68/9컬럼·PK 3열 단언 · 생성물 헤더에 식별자 가정 기록 · 코드 컬럼 합성값 전부 표식 패턴 |
| **W-S2** | `testdata/itam/` `docker-compose.yml` · `init/03_readonly_user.sql` · `setup.sh` · `README.md`(접속 정보 · §6-1 질의 3종 수기 SQL과 기대 호스트 목록) | W-S1 | `setup.sh` 완주(준비 대기 → 행수 출력) · `SHOW GRANTS FOR itam_ro` = SELECT만 · 수기 SQL 3종이 설계한 경계 행만 반환 · `down -v` 후 재기동 멱등 |
| **W-S3** | `mcp_server` MariaDB 통합 테스트(`RUN_DOCKER_IT=1`) — §4.6.6 단언 1~5 | W-2·W-3·W-S2 | 옵트인 통과 · 옵트인 상태 미도달 시 실패 · 미옵트인 skip 사유 노출 · 기존 PG/DB2 테스트 무회귀 |

**순서 불변식**: W-12(활성화)는 **마지막**이다(§0.3). W-6 없이 W-12를 하면 T4가 발동한다.
**v10 — 로컬 환경에서 사용자 결정으로 대체**(2026-09-17 · D-214 ⑤): W-6 전 로컬 활성화를 프로세스 env 주입으로 검증했고, 루트 `.env` 1줄은 13:54 적용 → 14:40 원복 → **v11: 사용자 결정 C(D-228 전역 유사어 가드) 구현 후 14:52 재적용**했다. 운영 반영에는 이 불변식이 그대로 유효하다. 관찰된 T4 결과는 §5.1 W-12 행.
**트랙 S**: W-S1·W-S2는 선행이 없어 W-1과 병행 착수한다. 로컬 샌드박스도 위 불변식의 예외가 아니다 — W-12 전 로컬 검증은
MCP 계층 직접 호출까지다(§4.6.5).

### 5.1 구현 현황 (v5 · 2026-09-17 실측)

| WU | 상태 | 산출물 | verify 결과 · 사유 |
|---|---|---|---|
| W-0 | 미착수 | — | 게이트 답 없음(§8) |
| **W-1** | ✅ 완료 | `mcp_server/pyproject.toml` `aiomysql>=0.3.2` · 루트 `.venv`에 aiomysql 0.3.2 + PyMySQL 1.2.0 설치 | 선정 근거는 아래 표. API는 설치 후 `inspect.signature`로 실측(`create_pool(minsize, maxsize, …, **connect kwargs)` — DSN 문자열을 받지 않는다) |
| **W-2** | ✅ 완료 | `mcp_server/mcp_server/db.py` — `_mariadb_pools` · `_execute_mariadb` · 헬스체크·종료 분기 · `_mariadb_connect_kwargs`(`mariadb://`·`mysql://` DSN 해석, `?옵션`은 거부) | 목 드라이버 단위 테스트 14건(`mcp_server/tests/test_db_mariadb.py`) · 실연결은 W-S3 |
| **W-3** | ✅ 완료 | `mcp_server/mcp_server/tools.py` — `_mariadb_search_objects_sql`·`_mariadb_get_columns`·`_mariadb_get_primary_keys`·`_mariadb_get_foreign_keys`(+리터럴·테이블 조건 헬퍼) | SQL 생성·리터럴 이스케이프·`get_table_schema` 분기 단위 테스트 10건(`tests/test_tools.py`) · 결과 키가 PG 헬퍼와 동일함을 **실 엔진에서** 단언(W-S3 단언 2). 명명은 초안의 `_mysql_*`가 아니라 소스 타입값 `mariadb`에 맞췄다 |
| **W-4** | ◐ 부분 | `config.py` 타입 주석 · `config.toml` `[[sources]] name="itam" type="mariadb"` · `.env.example` 키 등재 + 커버리지 테스트에 `itam` 추가 | 연결 문자열을 env로 주입해 **실 서버 기동 후 MCP 클라이언트로 호출**: 기동 로그 `활성 소스: ['itam (mariadb)']` · `list_sources`에 itam · `get_table_schema TCDMSIF80` 68컬럼·PK 3열 · `execute_sql DELETE` 읽기 전용 거부. **잔여**: 운영 연결 문자열(G-2·G-3). ~~`mcp_server/.env`는 수정하지 않았다~~ → **v6(2026-09-17 사용자 지시 *"자산관리 시스템에 맞는 mcp_server 정보를 설정하여 연결하라"*)**: `mcp_server/.env`에 `ITAM_CONNECTION`(로컬 샌드박스 · `itam_ro`@3307/`INST1`) 추가 · 본체용 MCP 서버 9099 재기동(직전 연결 0건 확인 · SRE용 9097 미변경) · `list_sources`·`search_objects`·`get_table_schema`(68·PK 3열)·`execute_sql`(30/29행)·`DELETE` 거부·폴스타 무회귀(1597행) 실측 — 상세 `plans/102` §1.4. **`ACTIVE_DB_IDS`는 미변경(W-12 순서 불변식)** |
| **W-5** | ◐ 부분 | `config/db_registry.yaml` `itam.engine: mariadb` · 단언 1줄 | 레지스트리 테스트 그린. `engine` 소비처는 전부 대상 DB가 itam일 때만 닿는다(`get_domain_by_id`·`active_db_engine`) — 비활성인 현 운영에서 동작 불변. **잔여**: `db_schema`(G-2) → **v12 ↪ `plans/104` 이관**: 후보는 104 §3.8.4 레지스트리 조각이 MCP 수집 결과에서 제시하고, 104 준비도 C4는 DB2만 필수(MariaDB는 값 표시)다. 로컬은 `db_schema` 빈 값으로 itam 질의가 실행됐다(v11 재현 r-062 5행). G-2 질문은 철회 — 운영 database명은 W-4 연결 문자열 작성 때 확인 |
| W-6 · W-7 | ↪ `plans/104` 이관(v12) | — | ~~G-4(물리 식별자)·G-5. 로컬 DB에서 추출 금지(§4.6.2)~~ → **v12**: 사용자 지시로 구조 정본·지식·유사어는 수동 작성이 아니라 104 관리자 흐름(O-2 스키마 수집·캐시 등록 · O-4 컬럼 설명·유사어 초안 · O-5 DB 설명 · O-6 구조 분석 승인본 · O-7 시드·값 인덱스 · O-8 프로필 골격 조각)이 만든다 — 대응표 §5.2. §4.6.2 파생 금지는 104 R7(환경 표기)·A-9(로컬 산출물·조각 커밋 금지)로 이어진다 |
| **W-8** | ✅ 완료(v10) | `itam.description`을 자산 축으로 재작성(사양·사용률·용량 어휘 제거) · 경계 골든셋 r-060~r-064(금지 2 · 기대 3) · 판정 `forbid_databases` 추가(`scripts/eval_routing.py`) · 목업 대본 5줄 · 라우터 프롬프트 골든 2파일 각 1줄 갱신 | **운영 렌더 비트 동일 실측** — 설명은 활성 DB만 렌더되므로(`semantic_router.py:225` · `subagents.py:146` · `general_inference.py` 활성 필터) `ACTIVE_DB_IDS=polestar`에서 라우터 프롬프트(fault_dx on/off)·DB 목록 블록 SHA-256이 기준선 worktree와 같다. **단 평가 하네스는 등록 DB 전부를 렌더한다**(`eval_routing.py` `run()` → `DB_DOMAINS`) — S-1 측정 프롬프트가 2026-08-31 기준선 대비 이 한 줄만큼 바뀐다. 목업 18/18 통과. **미검증**: 실 LLM 경계 선택(D-127) · **G-8 사용자 확정** · **v9 로컬 LLM 경계 판정(2026-09-17 · 사용자 승인 — mlx 한정)**: 모델 `mlx-community/Qwen3.8-27B-4bit`(루프백 `mlx_lm.server` 0.31.3 · 워커·오케스트레이터 평면 모두 `mlx` · `external_planes=[]` · thinking off · max_tokens 4096). 평가 대상은 **3단 분류기** `src/routing/semantic_router._llm_classify` — 단 하네스는 **등록 DB 전부**를 렌더하고 fault_diagnosis off·Redis 설명 미사용(런타임 노드는 활성 DB만). 결과: r-060 `polestar_b0` · r-061 `polestar_b0` · r-062/063/064 `itam` — **3회 반복 전건 선택 일치 · `forbid_databases` 위반 0 · 15/15 통과**(호출당 6.7~8.0초 · 워밍업 59.7초). 골든셋 전체 1회 **17/18**(멀티 DB 보존 3/3 · 게이트 탈락 점수 0 · 호출 실패 0 · 합계 156.3초) — 실패 r-051 *"그 장비 정보"*는 intent `general_inference`(기대 `data_query`)이며 itam과 무관. 로컬 모델 결과는 운영 기준선이 아니다(D-174·D-222) — 2026-08-31 Gemini 리포트와 회귀 비교하지 않는다. **설명 변경 효과 대조**: 같은 코드·같은 모델에 HEAD 레지스트리(설명 변경 전 · 라우터 프롬프트 차이는 itam 설명 1줄)를 넣어 r-012·r-060~r-064 × 3 — **선택이 현재와 전건 동일**(r-012 `[itsm, itam, polestar_b0]`). 즉 이 모델에서는 재작성 전 설명도 경계를 지켰다 — 재작성의 효과는 이 측정으로 **입증되지 않았고** 해도 없었다. 산출물(미추적 — `reports/`는 gitignore 대상 아님): `reports/routing_mlx_boundary_20260917.json` · `routing_mlx_full_20260917.json` · `routing_mlx_itam_head_registry_20260917.json`. 재현: mlx 서버 기동(`scripts/mlx_server.sh`) 후 `for n in 1 2 3; do RUN_E2E=1 .venv/bin/python scripts/eval_routing.py --out reports/routing_mlx_full_$n.json; done`(하네스에 부분 실행 옵션이 없어 경계 3회는 전체 3회로 재현 · 본 측정은 하네스 함수 `load_gold`·`judge`·`summarize`를 재사용한 세션 스크래치 러너로 경계 5건만 3회 · 기준선은 `build_domains(load_registry(<git show HEAD:config/db_registry.yaml>))`). **v10 활성화 후 실노드 판정(마감)**: 실제 `build_graph()` 3단 그래프(`context_resolver → input_parser → field_mapper → semantic_router`)를 `interrupt_after=["semantic_router"]`로 멈춰 **활성 DB만 렌더되는 조건**(`polestar,itam`)에서 r-060~r-064 × 3 — r-060·r-061 `polestar` · r-062~r-064 `itam` · **3회 전건 일치 · 금지 위반 0 · 전건 `schema_analyzer` 직전 정지**(T4 산출물 0 — 스냅숏 불변). 모델은 `mlx-community/Qwen3.5-9B-OptiQ-4bit`(병행 세션이 13:51 `.env`를 27B→9B로 바꿔 그 값으로 기동 — v9 하네스 판정은 27B). 첫 회 26~111초(콜드) · 이후 4.8~6.0초. 산출물 `reports/routing_mlx_node_boundary_20260917.json`(미추적). **운영 모델 판정은 미수행(선택)** |
| **W-9** | ✅ 완료(v9) | `src/nodes/multi_db_executor.py` `_with_unzoned_group` · 테스트 8건(`tests/test_nodes/test_multi_db_auto_groups.py` `TestUnzonedResidualGroup` 3 · `TestUnzonedBitIdentical` 5) | **v9 구현(G-7 사용자 확정)**: 어느 그룹에도 들지 않은 대상을 존 그룹 뒤 마지막 잔여 그룹(`unzoned` · 라벨 「존 무관」 · 내부 레지스트리 선언 순)으로 실행 — 명시 `execution_groups` 경로도 같이 막는다. 공용 `partition_execution_groups`·`sweep_order`·존 RBAC 선택지·범위 선택 불변. **플래그 없음 — 비트 동일 증거**: 모의 실행 매트릭스(대상 5종 전 부분집합 × 입력 순서 2 × `ZONE_GROUP_EXCLUSIVE` 2 × 명시 그룹 유무 2 = 228건)를 HEAD worktree·수정 후로 덤프해 대조 — 실행 경로(명시 그룹 없음) 변화 **18건 = 은행존+공동존+존 미배정 9조합 × 입력 순서 2**, 전부 종전 탈락 대상 = 존 미배정 대상 · 수정 후 탈락 0 · 잔여 그룹 마지막 · 존 그룹 부분 불변. `exclusive=true` 57건·현 운영 `b0·gp·yd`·로컬 `polestar` 단독·`polestar+itam`·`gp+yd+itam` 불변. 신규 테스트를 HEAD에 얹으면 잔여 그룹 3건 실패(itam 탈락)·비트 동일 4건 통과. 2단·1단은 같은 `multi_db_executor`를 쓴다(`subagents.py:30` import) — 사본 없음. `src/routing/db_scope.py` `_zone_groups_axis`는 응답 메타의 존 축 **표시**라 실행 탈락이 아니다(미변경). **아래는 v5·v8 이력** — ~~미착수 — 사용자 결정~~ **비트 동일을 보장할 수 없다**: 로컬 `polestar` 샌드박스 자체가 존 미배정이라 현 `.env`(`ACTIVE_DB_IDS=polestar`)에서 이미 `partition_execution_groups(['polestar'])` = `[]`, `host_sweep.sweep_order(['polestar'])` = `[]`이다(실측). 존 미배정 DB를 잔여 그룹으로 싣는 순간 폴스타 샌드박스의 호스트 탐색·범위 선택 동작이 함께 바뀐다. **v8 정정 — 비트 동일 불가는 수정 위치의 문제다**: 위 서술은 `partition_execution_groups` 자체를 바꿀 때만 참이다. 이 함수는 소비처 5곳(`multi_db_executor._auto_execution_groups` · `host_sweep.sweep_order`/`authorized_zones` · `api/routes/query.py` 범위 축소 기록·범위 선택 역질문)이 공유하지만 **침묵 탈락이 실제로 나는 곳은 실행기뿐**이다 — 실행기는 존 그룹이 2개 이상일 때만 그룹 실행으로 간다(실측: `[gp,yd,itam]`·`[polestar,itam]` → `_auto_execution_groups` = None이라 전 대상 실행 / `[b0,gp,yd,itam]`만 itam 탈락). `sweep_order`가 itam을 빼는 것은 폴스타 호스트 해석기 순회라 **정상**이다. 따라서 실행기 한정 잔여 그룹안은 현 운영(`b0·gp·yd` — 존 미배정 대상 없음)·로컬 샌드박스에서 비트 동일이다. **그래도 착수하지 않았다** — D-214 ④ *"사용자 결정 전 착수하지 않는다"* · 처분 형태가 `plans/102` G-4(자산관리의 `solutions` 등재 여부)와 한 몸이라 형태 선택이 선행한다(레지스트리안은 `zone_group_of`가 존 기준이라 가상 존이 필요하고, 그러면 존 RBAC 선택지·`sweep_order`에 itam이 들어온다) |
| W-10 | 미착수 — 사용자 결정 | — | 혼합 질의의 존 역질문 기대 동작에 **기본 가정이 없다**(§8 G-7 기본값은 T2만 규정). 현 운영은 itam 비활성이라 혼합 대상이 생기지 않는다. **v8 재실측**: 비발동 조건은 사본 2곳이다 — 2단 `src/orchestration/subagents.py:282-284`(§0.2 ⑤ 인용 `:264-268`에서 이동) · **3단 `src/routing/semantic_router.py:442-444`**(v7 기준 경로). 발동으로 바꾸면 끝나지 않는다 — 선택지가 폴스타 존뿐(`ZONE_CLARIFY_OPTIONS`)이고 재개 턴은 `selected_db_ids`로 대상을 **고정**하므로(`semantic_router.py:136-158`) 재개 턴에서 itam이 빠진다. 선택지는 최종 보고로 사용자에게 올림. **v9 — 구현 보류 · 요건 확정**: `plans/102` X-7(소유 라우팅) 이후로 둔다(권고 (c)). 사용자 사실(자산관리는 존 없음·단일 시스템)에서 나오는 요건 — *존 역질문이 발동하더라도 itam은 존 선택과 무관하게 대상에 남아야 한다*(재개 턴 `selected_db_ids` 고정에서 itam 탈락 금지). `plans/102` W-9/W-10 행에 같은 요건 기록 · **선행 충족(2026-09-17 · `plans/102` v4)**: X-7(답변 영역 소유 라우팅)이 플래그 `ROUTER_CAPABILITY_OWNERSHIP_ENABLED` 뒤로 기본 off 랜딩됐다 → **W-10 재개 가능**. 102 구현 중 확인된 결함 — 분해 task의 소유 고정은 `selected_db_ids`가 없을 때만 적용돼, 존 선택 재개 턴의 자산 task가 폴스타로 간다(102 §4.1.6) — 이 W-10 요건과 같은 결함이다 |
| **W-11** | ✅ 종결(v13) — 사용자 결정 G-9 = 노출 | — (구현 불필요) | G-9 미답 + **마스킹이 결과 열 키 기준**(`data_masker._is_sensitive_column` 부분 일치) — 물리 컬럼명(G-4)이 없고, LLM 별칭이면 우회되며, 전역 `SecurityConfig.sensitive_columns`에 넣으면 폴스타 결과에도 부분 일치로 적용돼 비트 동일이 깨진다. DB별 위치(W-6 프로필)가 선행 · **v12**: 물리 컬럼명 선행 조건(G-4)은 104 O-2 스키마 수집으로 충족되고, DB별 마스킹 위치는 104 적용본·프로필 골격 확정 뒤 정한다. 남은 사용자 결정은 **G-9(노출 방침)**뿐 · v10 관찰에서 성명 컬럼이 이미 조회됐다(로컬 합성값) · **v13 종결**: 사용자 결정 *"W-11은 그대로 보여주면 된다."* — 금액 4종·성명을 마스킹하지 않는다. 따라서 결과 열 키 부분 일치 문제·전역 `sensitive_columns` 비트 동일 문제는 발생하지 않는다. FabriX PII 필터는 「이름」을 탐지하되 처리 정책이 미차단이고(`docs/pii_filtering_rules.md:6,222`) 금액은 탐지 유형이 아니라 응답 차단 위험도 없다. 남은 관찰 1건: 원천에서 암호화 대상으로 표시된 `rspblPsnEmnm`을 운영 조회 계정이 평문으로 받는지는 운영 연결(W-4) 때 확인 — 암호문이면 그대로 암호문이 보인다 |
| **W-12** | ✅ 완료(v11 · 로컬) | 루트 `.env` 85행 `ACTIVE_DB_IDS=polestar` → `polestar,itam` **반영 이력**: 13:54:42 적용 → 14:40:23 원복(보류 지시가 적용 뒤 도착한 메시지 순서 역전) → **14:52:37 재적용**(현재 `polestar,itam` · 되돌리기는 85행을 `polestar`로) · `POLESTAR_DB_IDS`·사다리 플래그 미변경. **보류 사유 정정(v11)**: v10에 적은 사유("main go 대기 · 병행 세션이 `polestar` 전제")는 그 세션 무응답으로 해소됐고, 실제 보류 사유는 아래 T4 ①(전역 유사어 오염)의 처분 결정 대기였다 → 사용자 결정 C(W-12a · D-228) 구현·검증 후 재적용. 13:54~14:40 구간에 PID 99411 세션이 새 프로세스로 `.env`를 읽었을 가능성은 사실로만 남긴다(그 세션 실행물 확인은 main 소관) | **사용자 결정으로 순서 불변식 대체**(D-214 ⑤ — W-6·`plans/102` L-1 미완 보고 후). **사다리 확정 로그 전후 동일**(프로세스 env 주입으로 전후 조건 생성): 3단 주입(`ENABLE_DEEPAGENTS_PACKAGE=false`·`ENABLE_INTENT_ORCHESTRATION=false` — 신 키라 별칭 순서 함정 무관, `.env`에 구 키 0건) `tier=semantic_router degraded_reason=flag_off resolved_by=explicit_env` / `.env` 그대로 `tier=deep_agent degraded_reason=none` — `.env`가 1·2단 플래그를 명시해 tri-state 자동 해석(`src/config.py:1394`) 미발동. 활성화 후 라우터 프롬프트에 itam 설명 렌더(활성 도메인 9,930→10,180자) · `POLESTAR_DB_IDS`(`polestar_b0·gp·yd·polestar`) 불변 · `polestar+itam`은 존 그룹 0개라 W-9 잔여 그룹이 아닌 종전 전 대상 실행 경로(`_auto_execution_groups`=None). **T4 관찰(itam 단독·교차 질의 각 1건 · 3단 · mlx 9B)**: ①Redis `schema:itam:{descriptions,fingerprint_checked_at,meta,relationships,synonyms,tables}` 6키 생성 + **LLM 유사어가 `synonyms:global`에 컬럼명 키 9필드로 전파**(`sWSportEndYmd`→"클라이언트종료일" 등 오역 — 전역 사전 오염) ②프로필 YAML·`structure_meta` 생성 0 · **구조 승인 HITL 미발동**(LLM 구조 분석이 특수 패턴을 못 찾으면 `_analyze_db_structure`가 None — `schema_analyzer.py:300-302`·`:1148`) ③자산 단독 r-062 SQL이 "이번 분기"를 2024-06~08로 하드코딩해 0행 → "데이터 없음" 응답(결정적 규칙 ① 부재) · 담당자 성명 `rspblPsnEmnm` SELECT(W-11 부재) ④교차 x-001 *"svr-web-01 서버의 CPU 사용률과 유지보수 계약 만료일"*은 `polestar`·`itam` 모두 실행(침묵 소실 0)했으나 폴스타 0행이라 **응답의 CPU 사용률 10.37%가 자산 DB 판별값**(T5 재현 · `plans/102` 소유 라우팅 소관) · itam SQL은 3열 복합 키가 아니라 `sevrHostName` 단일 조인(규칙 ②). ①은 증거를 스크래치에 덤프한 뒤 **6키 DEL + `synonyms:global` 9필드 HDEL(133→124)**로 지정 정리, 스냅숏이 기준선과 같음을 확인. **활성화를 유지하는 동안 itam 질의마다 ①이 재발한다** |
| **W-12a** | ✅ 완료(v11) — 사용자 결정 C | `src/schema_cache/cache_manager.py` `_has_manual_profile` · `get_schema_or_fetch` 가드 · 테스트 `tests/test_schema_cache/test_global_synonym_guard.py` 6건 · D-228 | **쓰기 경로 확정**: 질의 경로 `schema_analyzer._get_schema_with_cache`(`:821`)·`multi_db_executor`(`:1183`) → `cache_manager.get_schema_or_fetch` 캐시 미스 → `DescriptionGenerator.generate_for_db`(LLM) → `save_synonyms` → **`sync_global_synonyms(db_id)`** → `add_global_synonym` → `synonyms:global`. v10 관찰의 INFO 로그는 캡처되지 않아(stdout 필터) 코드로 확정했고, 재현 실행의 차단 로그로 이 지점 도달을 실측했다. **분류**: (가) 차단 = 위 경로 1곳 · (나) 불변 = `cache_management` 관리 명령(`:428`·`:473`·`:536`·`:592`·`:1043`·`:1111`·`:1229`) · `synonym_registrar:149` 사용자 선택 등록 · `synonym_loader` 시드·파일 로드(`schema_analyzer.py:1206-1220` `config/global_synonyms.yaml` 포함) · 관리자 API·CLI · 양식 피드백 `apply_mapping_feedback_to_redis` · (다) **보류** = 양식 LLM 매핑 자동 등록 `document/field_mapper.py` `_register_llm_mappings_to_redis`(`:424`→`:1427`)·`_register_llm_synonym_discoveries_to_redis`(`:1063`→`:1118`·`:1133`) — 컬럼 경로가 전역에만 써서 막으면 그 DB 양식 학습이 사라짐(차단 vs DB별 전환은 미정 설계) → 사용자 결정 대기. **가드**: `source: manual` 프로필(schema_analyzer `_load_manual_profile`과 같은 기준 · `catalog_builder.load_structure_profile` 재사용)일 때만 전역 병합, 아니면 WARNING `LLM 유사어 전역 전파 차단: db_id=…, columns=… (수동 프로필 없음 — DB별 캐시에만 저장)` · DB별 캐시 불변 · 플래그 없음(프로필 4종 전부 `manual` → 비트 동일). **검증**: 단위 6/6 · HEAD 대조 3 실패(차단 2 + 판정 함수 부재)/3 통과 · `tests/test_schema_cache`·`tests/test_nodes`·`test_plan67_phase0_core` 1,332 통과 + 기존 실패 4(클린 HEAD 동일) · arch/overfit 0 · ruff 25=25 · mypy 64=64. **재현(mlx 9B · PID 71431 시작·끝 동일 · 3단 · `.env` 활성)**: r-062·x-001 끝까지 — 차단 로그 `db_id=itam, columns=9` · **`synonyms:global` 124→124(추가·삭제·값 변경 0)** · `schema:itam:*` 6키(DB별 런타임 캐시) · `config/` 신규 파일 0. 폴스타 2질의(gp-003·r-060) digest가 W-13과 동일. r-062는 이번에 5행(v10 관찰은 날짜 하드코딩 0행) — 결과 차이 원인은 미분석. x-001의 T5(사용률 출처 치환)는 그대로 — `plans/102` 소관. **부수 발견**: `tests/test_schema_cache/test_cache_manager.py:171` `invalidate("test_db")`가 추적 파일 `config/db_profiles/test_db.yaml`을 삭제(104 S1 경로) — 검증 중 삭제돼 HEAD 내용으로 복원 |
| **W-13** | ✅ 완료(v10) | 스크래치 러너(실제 `build_graph()` 3단 · `create_initial_state` · InMemorySaver · 질의당 900초 제한) — 산출물은 세션 스크래치 | **회귀 0**. 하네스 선택: `eval_routing`은 등록 DB 전부를 렌더해 `ACTIVE_DB_IDS`에 무감 · `eval_text2sql` 골든셋은 gp/yd/b0 대상이라 로컬 EX 불가 → 실제 그래프 러너. 질의 6건(선정: 위치어 없는 폴스타 단독 — 구성 조회 `gp-001`·건수 `gp-003`·사용률 T5 `r-060`·사양 경계 `r-061`·알람 `gp-012`·호스트 지목 `svr-web-01`(자산 샌드박스에도 있는 호스트)) × **활성화 전 2회·후 2회 교차(B1→A1→B2→A2 · 24회 · 약 26분 · 회당 381~410초)**. **노이즈 바닥**(B1 대 B2·A1 대 A2) 전 축 차이 0 → **전후 비교**(B×A 4쌍) 전 축 차이 0: 선택 DB · 노드 경로 · 생성 SQL · 결과 행수·digest · 재시도(`h-001`만 전 조건 공통 1회) · HITL/역질문 · 응답 길이. 폴스타 질의의 itam 선택 **0/24**. 앱에 LLM 응답 캐시 없음(grep) · mlx 채팅 호출 실제 도달 — 동일성은 greedy 디코딩 결정성(워커 `temperature=0.0` `src/llm.py` `_create_mlx` · 오케스트레이터 경로 0.0 · `mlx_lm.server` 기본 `--temp 0.0` = argmax). **모델·서버 동일성**: 전 묶음(13:56~14:33)이 mlx 서버 PID 71431(13:51:49 기동 — 13:52·14:35·14:38·14:40 동일) 안에 있고 묶음마다 러너가 설정 모델 `Qwen3.5-9B-OptiQ-4bit`를 기록 · `.env`는 그 구간 무변경(mtime) — 묶음 경계마다 `/v1/models`를 찍지는 않았다(그 응답은 캐시 모델 전부를 나열해 적재 모델 식별이 안 된다). 소요 시간에는 병행 세션과의 서버 경합 노이즈가 섞였다. 전후 조건은 `.env`가 아니라 프로세스 env `ACTIVE_DB_IDS` 주입으로 만들었다(`.env` 원복과 무관하게 유효). 교차 질의 침묵 소실 0은 W-12 ④ · 존 그룹 경로는 W-9 테스트 |
| **W-14** | ◐ 부분 | 본 절 · §10 v5 · 파일명 `-WIP` · INDEX · D-214 등재(부분 확정) · `docs/05` itam 행 엔진 정정 · `docs/18` 실수 1건 | **미반영**: `CLAUDE.md` 「데이터 도메인」 표(사용자 소유 설정 문서) · ~~`docs/18` 편입 체크리스트 행 정정~~ **v8 반영**: `docs/18` 표 하단에 정정 행 1건(dbhub 실형 0~5단계 · 리허설 테스트가 연결 계층을 안 보는 근거 `test_registry_config.py:141`) + 2026-07-30 D-131 행에 포인터 · D-214 상태·③·주의 ①을 v6 사실(G-8 방향 사용자 확인 · `mcp_server/.env` 로컬 연결)로 동기화. **`CLAUDE.md` 행은 v8에서도 미반영** — 사용자 확인 대기(추가안 — db_id `itam` · 존 「— (미배정)」 · 엔진 「MariaDB」 · 스키마 칸 「로컬 도커 샌드박스(`testdata/itam` · 3307 · `INST1` 가정 · 활성화 전 — plans/95)」). **v10**: D-214 ⑤ 대체·활성화 검증·T4 관찰 등재 · `docs/18` 4건(zsh 변수 단어 분리로 env 주입이 조용히 뭉침 · 병행 세션과 mlx 서버 동시 기동 · T4 스냅숏이 전역 유사어 오염을 못 볼 뻔함 · 공유 자원 충돌 보고 후 답을 기다리지 않고 `.env` 수정) · INDEX. **`CLAUDE.md` 행은 v10에서도 이 세션이 직접 반영하지 않았다** — 에이전트 메시지로 전달된 지시로는 `CLAUDE.md`를 수정하지 않는 운영 제약 때문이며, 문안(아래)을 main에 넘겨 사용자 세션에서 반영하도록 했다: `itam` · 존 「— (미배정 · 단일 시스템)」 · 엔진 「MariaDB」 · 스키마 칸 「로컬 도커 샌드박스(`testdata/itam` · 3307 · `INST1` 가정) · 구조 정본 미작성(G-4) — plans/95」 → **main 세션이 사용자 지시(*"13,14번 진행하라"*)에 따라 같은 문안으로 반영 완료**(「데이터 도메인」 표 `polestar` 행 아래 1행 · 기존 미커밋 hunk 보존). W-14 문서분 잔여 0 |
| **W-S1** | ✅ 완료 | `testdata/itam/generate_init.py` → `init/01_schema.sql`·`init/02_seed.sql` | 재생성 diff 0 · 전사본 무결성(68/9 · seq · PK 순서 · 타입 형식)과 조용한 오염 4형 거부 · 코드 컬럼 전부 `Z9…` 표식 · 사용률 `.37` — 단위 테스트 13건(`tests/test_testdata/`) |
| **W-S2** | ✅ 완료 | `docker-compose.yml` · `init/03_readonly_user.sql` · `setup.sh` · `README.md` | `setup.sh` 완주(`down -v` 후 재기동 포함) · `SHOW GRANTS` = `USAGE` + `SELECT ON INST1.*` · 수기 SQL 3종이 설계 경계 행만 반환(README 오라클) · 서버 11.4.13 · `lower_case_table_names=0` · `utf8mb4_general_ci` |
| **W-S3** | ✅ 완료 | `mcp_server/tests/test_mariadb_integration.py` | `RUN_DOCKER_IT=1` 5/5 통과 · 포트를 틀리게 주입하면 5건 **실패**(사유 노출) · 미옵트인 skip 사유에 `setup.sh` 노출 · 기존 PG Docker IT는 기준선과 **같은 실패**(`cmm_resource` 1597 ≠ 1581 — 픽스처 데이터 드리프트, 이번 변경 무관) |

**W-1 드라이버 선정 근거**(PyPI 메타데이터 실측 2026-09-17 · §7 위험 기준):

| 후보 | 라이선스 | 비동기 | 폐쇄망 반입 | 판정 |
|---|---|---|---|---|
| **aiomysql 0.3.2**(2025-10) + PyMySQL 1.2.0 | MIT · MIT | 네이티브 풀 | **순수 파이썬 wheel 2개**(`py3-none-any`) — OS·아키텍처 무관 | **채택** |
| asyncmy 0.2.14 | Apache-2.0 | 네이티브 | Cython 플랫폼별 wheel 64종 — 대상 OS·파이썬 조합을 맞춰 반입 | 차순위 |
| mariadb(Connector/Python) 1.1.14 | LGPL-2.1 | ✖ | PyPI wheel이 Windows뿐 — Linux는 MariaDB Connector/C + 컴파일러 필요 | 기각 |
| mysql-connector-python 26.7.0 | GPLv2(FOSS 예외) | 있음 | 플랫폼 wheel | 라이선스 검토 부담으로 기각 |
| PyMySQL + `asyncio.to_thread` | MIT | 래핑 | 순수 파이썬 | §7 폴백(aiomysql이 이미 PyMySQL 위에 선다) |

**구현 계약 3건(코드 주석에 근거)**: ①`autocommit=True` — aiomysql은 트랜잭션이 열린 연결을 풀 반환 시 닫는데 SELECT만 해도
트랜잭션이 열리므로, 없으면 풀이 매 요청 재연결로 퇴화한다(`aiomysql/pool.py` `release` 실측) ②문장 타임아웃은 서버가 강제 —
`init_command="SET SESSION max_statement_time=<query_timeout>"`(MariaDB 세션 변수) ③인트로스펙션 SQL은 `CONCAT`(MariaDB `||`는 OR)·
`` AS `schema` ``(예약어)·백슬래시 선이중화 리터럴(기본 sql_mode에서 `\'` 탈출 차단).

**v5 실측이 더한 것**

| # | 실측 | 영향 |
|---|---|---|
| ① | `lower_case_table_names=0`에서 `information_schema`의 `table_name` 비교도 대소문자를 구분한다(`tcdmsif80` → 0건) | describe와 SELECT 판정이 갈리지 않는다 — 인트로스펙션에 `BINARY` 강제 불필요 |
| ② | `utf8mb4_general_ci`에서 `sevrHostName = 'svr-web-02'`가 `SVR-WEB-02`와 일치 | 호스트 키 대소문자 처리는 운영 collation(G-13)에 달렸다 — G-6과 한 번에 확인 |
| ③ | PG 방언 리허설(§4.6.6): `::numeric`·`INTERVAL '1 day'` = 구문 오류 / `\|\|` = `0` · `"col"` = 문자열 리터럴(필터 0행) | 침묵 오답 2종 확정 — 결과표 정본은 `testdata/itam/README.md` |
| ④ | 로컬 `polestar` 샌드박스가 존 미배정이라 T2(침묵 탈락)가 **지금도** 호스트 탐색·범위 선택에서 성립 | W-9를 사용자 결정으로 올린 근거 |
| ⑤ | 스키마 캐시 fingerprint SQL(`src/schema_cache/fingerprint.py`)은 MariaDB에서 그대로 동작(`TCDMSIF79` 9 · `TCDMSIF80` 68) | 캐시 갱신 경로는 W-12 후 엔진 문제 없음 |
| ⑥ | 폼필 미매핑 안내(`query_generator._unmapped_fields_section` · `multi_db_executor`)의 캐스트 예시가 `decimal_cast_example`(`src/db_adapters/polestar/assembler.py:29`) **DB2/비DB2 이진 분기** — 비DB2면 PG `::numeric` | itam 활성화 후 양식 업로드 경로에서 T1 재현 가능(코드 경로 판독 · 실행 미검증) — W-12 전 확인 항목 |
| ⑦ | 컨테이너 `CURDATE()`는 UTC | 오라클 기준일이 한국 시간 09시 이전에는 하루 전 — README에 명시 |

### 5.2 (v12) DB 정보 조사는 `plans/104`로 — 대응표

> 사용자 지시(2026-09-17): *"db 관련 정보는 104번 계획에서 조사하여 자동으로 스키마, 캐시 등을 생성하는 기능을 구현할 계획이다. 이에 맞게 처리하라."*
> → 95가 사용자에게 요청하던 운영 DB 정보(`SHOW CREATE TABLE`·코드값·서버 변수)와 수작업 산출물(수동 프로필·지식·시드)을 **104 관리자 흐름(§3.8 O-1~O-9)이 MCP로 조사·생성**한다. 95는 더 이상 이 정보를 사용자에게 묻지 않는다.

| 95 항목 | 종전 해결 방식 | v12 이관 대상(`plans/104`) | 95에 남는 것 |
|---|---|---|---|
| **G-4** 물리 컬럼·테이블 식별자 · 테이블명 대소문자 | `SHOW CREATE TABLE` 결과 요청 | O-2 스키마 수집(MCP `get_table_schema`) · §3.4 스냅샷 | — |
| **G-2** database명 · **W-5** `db_schema` | 사용자 확인 | §3.8.4 레지스트리 조각의 `db_schema` 후보 · 준비도 C4(DB2만 필수) | 운영 연결 문자열의 database명(W-4 · G-3과 함께) |
| **G-5** 코드성 컬럼 9종의 값 목록 | 사용자 제공 | 104 **G-10**(v3) — 비EAV 코드성 컬럼 값 수집 범위 | — |
| **G-13** 운영 서버 변수(`sql_mode`·collation·`lower_case_table_names`) | 한 줄 조회 요청 | 104 O-1 연결 확인의 엔진 버전·서버 변수 표시(v3) | 로컬 컴포즈(W-S2) 교체 판단 |
| **W-6** 구조 정본 | `config/db_profiles/itam.yaml` 수동 작성 | O-6 구조 분석 → 승인본(준비도 C6) · O-8 프로필 골격 조각(사람 PR 승격은 선택) | §3.3 결정적 규칙 5건은 104 초안 검토 때 대조할 확인 목록(날짜 `CHAR(8)` · 3열 복합 조인 · 코드값 · 민감 컬럼 · 긴 호스트명) |
| **W-7** 지식·유사어 시드 | `knowledge/itam/`·`synonym_seeds/itam.yaml` 작성 | O-4 컬럼 설명·유사어 초안 → 적용 · O-5 DB 설명 · O-7 시드 로드(배포된 경우) | — |
| 양식 LLM 매핑 자동 등록 처분(v11 보류 · D-228 ④) | 사용자 결정 | 104 **G-11**(v3) | — |
| D-228 가드(수동 프로필 DB만 전역 동기화) | — | 104 **B-8**(v3) — 조건을 「수동 프로필 또는 승인본」으로 넓히고, B-6(질의 경로 지연 LLM 생성 제거)과 같은 결정 안에서 제거 | — |
| 활성화 순서 불변식(D-214 ⑤) | W-6 뒤 활성화 | 104 준비도 C1~C7 충족 뒤 운영 활성화(G-7 (b)) | 로컬 활성화는 v11 상태 유지 |

- **전사본 `testdata/itam/schema.yaml`의 지위는 그대로다** — 사용자 제공 시트의 판독본(한글 컬럼명·코드 도메인 ID·민감 표시)이라 104 초안(O-4·O-6)을 검토할 때 **운영 사실 대조 자료**로 쓴다. 런타임 입력으로 넣을지는 104 B-4 착수 때 판단한다.
- **로컬 산출물 경계 유지** — 로컬 샌드박스를 대상으로 돌린 104 흐름의 산출물(캐시·초안·승인본·조각)은 로컬 검증용이다(104 R7·A-9 · §4.6.2). 운영 정본은 운영 MCP 소스를 대상으로 한 104 승인본이다.
- **95 잔여**: W-4 운영 연결(G-3 — 계정·네트워크, 104 흐름의 전제) · W-10(`plans/102` X-7) · ~~W-11(G-9 노출 방침 + 104 컬럼명)~~ W-11 종결(v13 — 노출 확정) · 운영 활성화(104 준비도).

---

## 6. 성공 기준

1. **기능** — 자산 질의 3종이 실 데이터로 답한다: ①*"지원 종료일이 6개월 내인 서버"*(`TCDMSIF79`) ②*"유지보수 계약이
   이번 분기 만료되는 서버"*(`manmenCtrcEndYmd`) ③*"경과년수 N년 이상 노후 서버"*(`elapsNoy`·`osoaRplacMchtlDstcd`).
   실패 시 사유가 사용자 응답에 노출된다(침묵 폴백 금지).
2. **회귀 0** — 폴스타 단독 질의(기존 골드셋)의 산출 SQL·결과가 활성화 전후 동일. ~~**판정은 사다리 1단(`deep_agent`)에서** 한다.~~ **v7: 판정은 사다리 3단(`semantic_router`) 확정 기동에서 한다**(기동 로그 첨부).
3. **침묵 소실 0** — 폴스타+자산 교차 질의에서 어느 DB도 사유 없이 빠지지 않는다(T2 해소 단언).
4. **경계 일관성(T5)** — *"CPU 사용률"* 단독 질의가 **항상 같은 DB**로 간다(안 (가) 채택 시 폴스타).
5. **결정성** — 동일 자산 질의 3회 반복 시 생성 SQL 동일. 자동 분석(`source: auto`) 의존 0.
6. **게이트** — `arch_check --ci` · `overfit_check --ci` exit 0. 자산 스키마 리터럴이 공용 계층·독스트링에 들어가지 않는다(D-179).
7. **읽기 전용** — 자산 DB 계정이 SELECT 권한만 보유(DB 레벨) + MCP 소스 `readonly = true`.
8. **기존 엔진 무회귀** — PG·DB2 소스의 `describe_table`·`execute_sql` 결과가 A0 전후 동일.
9. **로컬 실연결** — `testdata/itam` 샌드박스에서 `RUN_DOCKER_IT=1` MariaDB 통합 테스트(§4.6.6 단언 1~5)가 통과한다.
   **이 통과는 G-4·G-5·G-6·G-13의 확정을 대신하지 않는다**(§4.6.2).

---

## 7. 위험 · 비범위

**위험**

| 위험 | 완화 |
|---|---|
| MariaDB 드라이버 선정(폐쇄망 반입·라이선스·비동기 지원) | W-1을 독립 WU로 분리. 반입 불가 시 동기 드라이버 + `asyncio.to_thread` 래핑(DB2 선례 `db.py:56`)로 폴백 |
| 물리 컬럼명 미확정(§3.4-a) | **G-4 없이 W-6 착수 금지.** 한글 컬럼명으로 SQL을 쓰면 인용 규칙까지 얽힌다 |
| 테이블명 대소문자(§3.4-b) | D-057 선례대로 **실 DB 조회로 확정**하고 프로필에 못 박는다 |
| 코드값 미확보(§3.4-d) | 코드 마스터 테이블이 있으면 조인, 없으면 프로필 `column_values`에 수기 등재. 없으면 코드 컬럼 필터 질의는 비범위 |
| T5 경계 미확정으로 답이 흔들림 | G-8을 **활성화(W-12) 전에** 반드시 닫는다 |
| 폴스타 질의 품질 희석 | 라우팅 후보가 늘면 오분류 가능. W-8 골든셋에 **경계 혼동 케이스**를 반드시 포함 |
| 자산 DB 부하 | MCP 소스 `query_timeout`·`max_rows`가 서버측 상한(클라이언트 설정 아님). 초기값은 기존 소스와 동일(30s/10000행) |
| 과금 | 라우팅·text2sql 평가 실행은 실 LLM 호출 — **D-127 건별 승인 + `RUN_E2E=1`** 뒤에만 |
| **로컬 DB로 정본을 검증하는 순환** — 로컬 DB와 프로필이 같은 전사본에서 파생 | §4.6.2 파생 금지 규칙 · W-6 verify는 실 자산 DB로만 · 합성 코드값 표식으로 유입 탐지 |
| 로컬·운영 서버 변수 차이(버전·`sql_mode`·collation·`lower_case_table_names`) — 로컬 통과 방언이 운영에서 깨짐 | G-13 한 줄 조회로 확정 후 컴포즈 교체 · 그 전까지 로컬 결과는 「기본값 가정 하 통과」로만 기록 |

**비범위**

- ITSM DB 서빙 개방(`itsm`도 등록만 된 상태지만 본 계획은 자산관리만. 같은 절차가 재사용된다).
- 자산 데이터 쓰기·동기화(읽기 전용만, D-003).
- 루트 `.env`의 `ITAM_DB_CONNECTION` 채우기(실측 ② — 읽는 코드 0건).
- `cloud_portal` 서빙 개방 · 자산 기반 자동 조치.
- `TCDMSIF79`·`TCDMSIF80` 외 테이블(§3.4-g — 제공 시 범위 확장).
- 운영 자산 데이터(행)의 로컬 샌드박스 반입 — 금액·성명이 있다(§3.3-④). 로컬은 합성 시드만(§4.6.4).

---

## 8. 사용자 확정 게이트

> **G-1(엔진)은 제공 스키마로 종결** — **MariaDB**. 아래는 잔여 게이트다.

| # | 질문 | 왜 막히는가 | 기본 가정(답 없으면 이걸로 진행) |
|---|---|---|---|
| **G-2** | 실 **database(스키마)명**은? 시트의 `INST1`이 그것인가, 아니면 논리 인스턴스명인가? MariaDB **버전**은? | `db_schema` 값 · 드라이버 호환 | 없음 — 답 없이 연결 불가 |
| **G-3** | **읽기 전용 계정** 발급과 `mcp_server` VM → 자산 DB **네트워크 경로**가 가능한가? | D-003 · 폐쇄망 방화벽 | 열려 있다고 가정하지 않는다 |
| **G-4** | **물리 컬럼·테이블 식별자**는? (`sevrHostName` / `SEVR_HOST_NAME` / 한글 중 무엇인가, 테이블명은 대문자인가) | §3.4-a·b — **SQL을 못 쓴다** | 없음 — **W-6 착수 불가**. `SHOW CREATE TABLE TCDMSIF80` 한 줄이면 끝난다 |
| **G-5** | **코드값 목록**(9종 코드 컬럼) 또는 코드 마스터 테이블은? | §3.3-③ — WHERE 조건을 못 만든다 | 코드 필터 질의는 1차 비범위 |
| **G-6** | 자산 `sevrHostName`/`iPCtnt`가 폴스타 `hostname`/`ipaddress`와 **정확 일치**하는가? (샘플 5건 대조 요청) | 교차 질의(트랙 E)의 전제 | 불일치 가정 → 단독 질의만 1차 서빙 |
| **G-7** ✅ | 자산 DB는 **존 무관**인가? 존 RBAC에서 누가 볼 수 있는가? | T2·T3 처분 방향 | 존 무관 유지 + T2는 "잔여 그룹으로 실어 탈락 방지" · **v9 확정(2026-09-17)**: *"자산관리는 존이 없고 1개의 시스템에서 모든 자산을 관리한다"* → 존 무관. 역할 제한 언급이 없어 **전체 사용자 기본값 유지**(기본값 적용 — 제한이 필요하면 별도 지시). T2 → W-9 구현 · T3 → W-10 요건만 확정 |
| **G-8** | **★T5 경계** — "서버 CPU 사용률"을 물으면 폴스타와 자산 중 어디가 답해야 하는가? (§4.1 (가)/(나)/(다)) | 답이 조용히 달라진다 | **(가) 폴스타 우선 · 자산은 계약·금액·EOL·자산상태 축만** |
| ~~**G-9**~~ ✅ | **민감 컬럼 취급** — 금액 4종(`acqsiAmt`·`manmenCnpr`·`rmainAcbkAmt`·계약명)·성명(`rspblPsnEmnm`)을 조회 결과에 노출하는가? 조회 계정이 `rspblPsnEmnm` 평문을 보는가? | 마스킹·PII 필터 차단(§4.5) | 성명 마스킹 · 금액은 노출하되 감사 로그 강화 · **v13 확정(2026-09-17)**: *"W-11은 그대로 보여주면 된다."* → **마스킹 없이 노출**(성명·금액 모두) |
| **G-10** | **서버현황조회 쿼리문** 재제공 — 첨부 6장은 전부 컬럼 정의 시트였고 쿼리문은 보이지 않았다 | 실제 조회 패턴이 `query_examples`의 최상급 재료 | 없으면 우리가 §6-1의 3종으로 대체 |
| **G-11** | `TCDMSIF*`의 **IF**는 연계 테이블인가? 적재 주기와 행수는? 원장 테이블이 따로 있는가? | 사용률 신선도 → G-8 판단 근거 · §3.4-f | 연계 스냅샷으로 가정(→ G-8 (가) 보강) |
| **G-12** | 활성화 범위 — `ACTIVE_DB_IDS`에 `itam`을 운영에 바로 넣는가, 스테이징 선행인가? | 멀티 DB 모드 전환의 폴스타 회귀 위험 | **스테이징/로컬 선행 후 운영** |
| **G-13** | **운영 서버 변수** — `SELECT @@version, @@sql_mode, @@lower_case_table_names, @@character_set_server, @@collation_server;` 결과는? | 로컬 샌드박스 재현 조건(§4.6.3) · `\|\|`·`"…"` 해석(`sql_mode`의 `PIPES_AS_CONCAT`·`ANSI_QUOTES`) · 테이블명 대소문자(§3.4-b) · 호스트명 비교의 대소문자(collation → G-6) | MariaDB 기본값 — `sql_mode` 기본 · `lower_case_table_names=0` · `utf8mb4`/`utf8mb4_general_ci`. G-2의 버전 질문과 **한 번에** 답해진다 |

> **G-8 v6(2026-09-17)**: 사용자 요구 *"자산관리 = 하드웨어·소프트웨어 자산 정보·계약정보·담당자 정보 / 폴스타 = 모니터링 위주·서버 현황"*가 **방향 (가)를 확인**했다. 남은 겹침은 **서버 사양**(CPU 코어·메모리·디스크 용량 — 「하드웨어 자산 정보」와 「서버 현황」 양쪽에 걸림) 하나이며 `plans/102` **G-1**로 이관한다.

> **v12(2026-09-17) — 조사형 게이트는 사용자에게 묻지 않는다**: G-4·G-5·G-13과 G-2의 `db_schema` 부분은 `plans/104`가 MCP로 조사해 자동 생성한다(§5.2). 남은 사용자 게이트는 **G-3**(운영 계정·네트워크 경로) · ~~**G-9**(민감 컬럼 노출 방침)~~(v13 확정 — 노출) · **G-12**(운영 활성화 범위)이고, G-6은 `plans/102`(값 기반 키 브리지) 소관이다. G-10(쿼리문 재제공)·G-11(연계 테이블 적재 주기)은 선택 정보로 남긴다(행수는 104 스냅샷에서 보인다).

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
6. **로컬 샌드박스 경계** — `testdata/itam` MariaDB 도커 샌드박스는 **엔진 동작**(드라이버·인트로스펙션·권한·방언 실패 양상)
   검증 전용이다. 운영 사실(식별자·코드값·호스트 키 정합·서버 변수)의 근거로 쓰지 않고, 프로필·지식·시드를 로컬 DB에서
   추출해 커밋하지 않는다(순환 검증). 소스명은 로컬·운영이 `itam` 하나를 공유한다.

등재 시 `docs/02_decision.md`의 `## D-` 헤더·「변경 이력」·「채번 이력」 표를 재확인하고 최댓값+1을 재부여한다(예약 소진 가능).

---

## 10. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-09-11 | 최초 작성. 해석 정정(등록은 완료 — 막힌 것은 MCP 소스·스키마 지식·활성화) · 실측 6건 · 함정 T1~T4 · 인테이크 양식 · 트랙 A~F · 게이트 G-1~G-9 · D-214 예약 |
| **v2** | 2026-09-11 | **사용자 제공 스키마 반영** — ①**엔진 MariaDB 확정**(G-1 종결) → 트랙 **A0(세 번째 엔진 지원)** 신설·계획 성격 변경 ②`TCDMSIF80` 68컬럼·`TCDMSIF79` 9컬럼 전수 판독(§3) ③**함정 T5 신설**(자산 DB에도 CPU·메모리·스토리지 사용률이 있어 폴스타와 답변 영역 충돌) ④스키마가 만든 결정적 규칙 5건(§3.3 — `CHAR(8)` 날짜 · 3열 복합 PK · 코드값 부재 · 금액/성명 민감 · `VARCHAR(300)` 호스트명) ⑤미제공 7건(§3.4) → 게이트 G-2~G-12로 재구성(물리 컬럼명 G-4 · 코드값 G-5 · 경계 G-8 · 쿼리문 재요청 G-10 · IF 테이블 성격 G-11) ⑥WU 15건으로 재분해 |
| **v3** | 2026-09-15 | **스키마 전사본 분리** — 제공 이미지 판독 결과를 계획서 표에서 떼어 `testdata/itam/schema.yaml`로 정본화(68+9컬럼 전수 · 코드 도메인 10종 · 조인/민감/교차키 · `unresolved` 8건). 종전 §3.2·§3.3 두 표를 포인터 + 요약으로 축약(사본 금지)하고 이하 절 번호를 한 칸씩 당김. W-6 재료 명시. 전사 중 발견한 YAML 함정 3건(`no:`/`on:` 불리언 키 · flow 매핑 쉼표) `docs/18` 등재. ※D-213→**D-214** 재부여는 2026-09-14 원격 병합분(본 개정과 무관) |
| **v4** | 2026-09-17 | **로컬 테스트용 MariaDB 도커 샌드박스 편입**(사용자 지시) — 트랙 **S** 신설(§4.6): `testdata/itam/` 컴포즈(`mariadb:11.4` · `3307` · named volume — macOS bind mount는 테이블명 대소문자를 재현 못 함) · 전사본 → init SQL **생성기**(사본 금지 · G-4 식별자는 `var` 가정) · **SELECT 전용 `itam_ro`**(공식 이미지 `MARIADB_USER`는 전체 권한이라 D-003 DB층이 거짓 통과) · 합성 시드 설계 축(날짜 init 상대값 · 호스트 키 4형 · 사용률 판별값 · 합성 코드 표식 · 민감 컬럼 · 79 짝 누락) · `RUN_DOCKER_IT=1` 통합 테스트 단언 5종 + 방언 리허설. ★**증명 범위 표**(엔진 동작 O / 운영 사실 X)와 **파생 금지 규칙**(로컬 DB → 프로필·시드 추출 금지 — 순환 검증). WU **W-S1~W-S3** 추가 · W-2·W-3·W-4·W-14 보강 · 성공 기준 9 · 위험 2 · 비범위 1 · 게이트 **G-13**(운영 서버 변수 한 줄 조회) · D-214 담을 내용 ⑥ |
| **v5** | 2026-09-17 | **1차 구현**(사용자 지시 *"95번 계획을 구현하라."*) — §5.1 구현 현황 신설. 랜딩: **W-1** aiomysql 선정(순수 파이썬 wheel · MIT)·**W-2** `db.py` MariaDB 풀·실행·DSN 해석·**W-3** 인트로스펙션 헬퍼 4종·**W-4** 소스 정의·`.env.example`(운영 연결 문자열 제외)·**W-5** `engine: mariadb`(`db_schema` 제외)·**W-S1~W-S3** 샌드박스 생성기·컴포즈·읽기 전용 계정·통합 테스트 5단언·**W-8** G-8 기본 가정 (가)로 `itam` 설명 재작성 + 경계 골든셋 5건 + 판정 `forbid_databases`. 차단·보류: W-6·W-7(G-4) · W-9(비트 동일 불가 — 로컬 `polestar` 샌드박스가 이미 존 미배정 탈락 대상) · W-10(기대 동작 기본 가정 없음) · W-11(G-9·G-4 · 전역 마스킹 부분 일치) · W-12·W-13. 실측 7건 추가(§5.1). §4.6.6 리허설 결과 포인터. 파일명 `-TODO` → `-WIP`. D-214 본문 등재(부분 확정) |
| **v6** | 2026-09-17 | **교차 시스템 라우팅 승계 + 로컬 연결** — 사용자 요구(자산관리·폴스타 조회 구분 · 폴스타 키→자산 / 자산 키→폴스타 연쇄 · 키는 호스트명·IP 등 데이터 내용으로 판단 · 필요 시 양쪽 조회 후 선택)로 트랙 C·E를 `plans/102`(D-224 예약)로 승계. G-8 방향 (가) 사용자 확인 · 서버 사양 경계만 102 G-1로 이관. W-4: `mcp_server/.env` 로컬 `ITAM_CONNECTION` 추가 · 본체용 MCP 9099 재기동 · MCP 클라이언트 실연결 검증(§5.1). 루트 `.env` `ACTIVE_DB_IDS` 미변경(W-12) |
| **v7** | 2026-09-17 | **검증 기준 경로 1단 → 3단** — 사용자 기준(*"기본은 시멘틱 라우터를 사용한다. 모든 동작은 시멘틱 라우터에서 동작되어야 한다. deepagents는 부가적으로 사용할 예정"*)에 따라 §0.2 ⑥ · W-12 verify · §6-2의 "1단 `deep_agent`에서 판정"을 3단 `semantic_router`로 바꿨다. W-12 선행에 `plans/102` L-1(tri-state 미입력 시 2단 자동 확정 차단) 추가. 근거·전환 작업은 `plans/102` v2 §3.7 트랙 L · D-225 예약 |
| **v8** | 2026-09-17 | **재실행 — 게이트 무관 잔여 문서분 랜딩 + 차단 판정 재실측**(사용자 지시 *"95번 계획을 구현하라."*). 랜딩: W-14 `docs/18` 편입 체크리스트 정정 행 + D-131 행 포인터 · D-214 상태/③/주의 ① 동기화(v6 사실). 무회귀 재실측: `test_registry_config`·`tests/test_testdata`·`tests/test_routing_eval` 47 passed · `mcp_server` 247 passed/7 skipped · `RUN_DOCKER_IT=1` MariaDB 통합 5/5. **W-9 차단 사유 정정**(비트 동일 불가는 `partition_execution_groups` 전역 수정일 때만 — 실행기 한정안은 비트 동일, 단 D-214 ④·`plans/102` G-4로 형태 결정 선행) · **W-10 위치 재실측**(3단 `semantic_router.py:442-444` 사본 · 재개 턴 `selected_db_ids` 고정으로 itam 탈락). 여전히 차단: W-4 운영분·W-5 `db_schema`(G-2·G-3) · W-6·W-7(G-4·G-5) · W-9·W-10(사용자 결정) · W-11(G-9·G-4) · W-12·W-13(선행 + `plans/102` L-1 미랜딩 실측 `src/config.py:1394` + 루트 `.env` 사용자 확인) · W-8 실 LLM 경계 판정(D-127) · `CLAUDE.md` 행(사용자 확인). 코드 변경 0 |
| **v9** | 2026-09-17 | **G-7 확정 → W-9 구현 + mlx 경계 라우팅 판정**(사용자 답변 *"자산관리는 존이 없고 1개의 시스템에서 모든 자산을 관리한다. 라우팅 평가는 mlx에서 진행하라."*). ①G-7 = 존 무관(역할 제한 언급 없음 → 전체 사용자 기본값 적용) · 가상 존·`solutions` 등재안 기각 ②**W-9**: `multi_db_executor._with_unzoned_group` — 그룹에 들지 않은 대상을 마지막 잔여 그룹(`unzoned`·「존 무관」)으로 실행, 공용 분할·호스트 탐색·존 RBAC 선택지 불변, 플래그 없음(모의 매트릭스 228건 HEAD 대조 — 실행 경로 변화 18건 전부 종전 침묵 탈락 조합) · 테스트 8건 · 변경 영역 pytest 1,467 passed · arch/overfit 0 · ruff·mypy 신규 0 ③**W-10**: 구현 보류(`plans/102` X-7 이후) · 요건 확정(재개 턴에서도 itam 유지) ④**W-8**: `mlx-community/Qwen3.8-27B-4bit`로 r-060~r-064 × 3 전건 일치·위반 0 · 전체 1회 17/18 · HEAD 레지스트리 대조 선택 동일(설명 재작성 효과는 이 모델에서 미입증) ⑤D-214 ④ 확정 · `plans/102` G-4·X-T7·W-9/W-10 행 부기. 남은 차단: W-4 운영분·W-5(G-2·G-3) · W-6·W-7(G-4·G-5) · W-10(102 X-7) · W-11(G-9·G-4) · W-12·W-13(선행 + `plans/102` L-1 + 루트 `.env` 확인) · `CLAUDE.md` 행 |
| **v10** | 2026-09-17 | **로컬 활성화 검증 + 회귀 판정 + W-8 마감**(사용자 지시 *"mlx는 과금 없으니 w-8은 진행하라. w-12는 itam db를 등록하고 13,14번 진행하라."*). ①§5 순서 불변식·D-214 ⑤를 **사용자 결정으로 대체**(로컬 한정 — 운영 반영 시 유효) ②**W-12**: 프로세스 env 주입으로 활성화 검증 · 루트 `.env` 85행은 13:54 `polestar,itam` 적용 → main go 대기 조건(늦게 도착)으로 14:40 `polestar` 원복 — 1줄 반영은 go 대기 · 사다리 확정 로그 전후 동일(3단 주입 `semantic_router` / `.env` 그대로 `deep_agent`) · itam 설명 렌더 · `POLESTAR_DB_IDS` 불변 ③**W-8 마감**: 활성 DB만 렌더되는 실노드(라우터 직후 정지)에서 r-060~r-064 × 3 전건 일치·금지 위반 0 — 운영 모델 판정 미수행(선택) ④**W-13 회귀 0**: 폴스타 단독 6질의 × 전 2·후 2(24회) · 노이즈 바닥 0 · 전후 전 축 차이 0 · itam 선택 0/24 ⑤**T4 관찰**: itam 질의 1건마다 Redis `schema:itam:*` 6키 + `synonyms:global` 9필드(LLM 오역 포함) 전파 — 지정 삭제로 정리 · 구조 승인 HITL 미발동 · 자산 단독 SQL 날짜 하드코딩 0행 · 성명 컬럼 SELECT · 교차 질의에서 CPU 사용률이 자산 판별값으로 답변(T5 재현) ⑥모델: 병행 세션의 `.env` 변경으로 mlx 9B(v9 하네스는 27B) ⑦`docs/18` 4건 · D-214 ⑤·검증 등재 · INDEX. `CLAUDE.md` 행은 team-lead가 직접 반영하지 않고 문안을 main에 전달 → main 세션이 사용자 지시로 반영. 코드 변경 0. 남은 것: W-4 운영분·W-5 `db_schema`(G-2·G-3) · W-6·W-7(G-4·G-5) · W-10(`plans/102` X-7) · W-11(G-9·G-4) |
| **v11** | 2026-09-17 | **사용자 결정 C — LLM 유사어 전역 전파 차단 후 재활성화**(원문 *"C로 진행하라. mlx 서버는 그대로 둬라."*). ①**W-12a**(D-228): 쓰기 경로를 코드로 확정(`get_schema_or_fetch` 캐시 미스 → LLM 생성 → `sync_global_synonyms`)하고 그 지점에 `source: manual` 프로필 판정 가드 + WARNING 1줄 · DB별 캐시·관리 명령·사용자 등록·시드 로더 불변 · 양식 LLM 매핑 자동 등록(`document/field_mapper`)은 보류(사용자 결정 대기) · 단위 6건(HEAD 대조 3 실패) · 게이트 신규 0 ②**W-12 ✅ 로컬**: 루트 `.env` 14:52:37 재적용 · v10 보류 사유 문구("main go 대기 · 병행 세션 전제")를 실제 사유(전역 유사어 오염 처분 대기 → C로 해소)로 정정 ③재현(mlx 9B · PID 71431): 차단 로그 `db_id=itam, columns=9` · `synonyms:global` 124→124 · `schema:itam:*` 6키 · `config/` 신규 0 · 폴스타 2질의 digest W-13 동일 ④부수: `tests/test_schema_cache` 실행이 추적 파일 `config/db_profiles/test_db.yaml`을 지운다(`invalidate` — 104 S1) — 복원 · `docs/18` 등재 ⑤`plans/104` §1.7.2에 재평가 부기. 남은 것: W-4 운영분·W-5(G-2·G-3) · W-6·W-7(G-4·G-5) · W-10(`plans/102` X-7) · W-11(G-9·G-4) · 양식 LLM 매핑 자동 등록 처분(사용자 결정) |
| **v12** | 2026-09-17 | **DB 정보 조사를 `plans/104`로 이관**(사용자 지시 *"db 관련 정보는 104번 계획에서 조사하여 자동으로 스키마, 캐시 등을 생성하는 기능을 구현할 계획이다. 이에 맞게 처리하라."*). §5.2 대응표 신설 — G-4(물리 식별자)→104 O-2 스키마 수집 · G-2 `db_schema`→104 §3.8.4 조각 후보·C4 · G-5(코드값)→104 G-10 · G-13(서버 변수)→104 O-1 · W-6·W-7→104 O-4~O-8 · 양식 LLM 매핑 자동 등록 처분→104 G-11 · D-228 가드→104 B-8 · 활성화 불변식→104 준비도 C1~C7. §5.1 W-5·W-6·W-7 「↪ 104 이관」 · W-11 선행 조건 갱신(남은 결정 G-9) · §8 v12 부기(남은 사용자 게이트 G-3·G-9·G-12). 코드 변경 0 |
| **v13** | 2026-09-17 | **G-9 확정 → W-11 종결**(사용자 결정 *"W-11은 그대로 보여주면 된다."*) — 금액 4종·성명 마스킹 없음 · 구현 불필요 · FabriX 필터 차단 위험 없음 근거(「이름」 미차단 · 금액 비탐지 — `docs/pii_filtering_rules.md:6,222`) · 운영 조회 계정의 `rspblPsnEmnm` 평문 여부는 W-4 때 관찰. 남은 것: W-4 운영 연결(G-3) · W-10(`plans/102` X-7) · 운영 활성화(`plans/104` 준비도 C1~C7) |
