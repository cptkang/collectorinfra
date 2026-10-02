# 133. 스키마 자산 자동 생성 — 신규 DB(ITAM 등)의 프로필 키·유사어 시드·전용 프롬프트 템플릿을 스키마 파싱과 읽기 전용 데이터 조회로 만든다

> **작성일**: 2026-10-01 · **v1.0**
> **상태**: **WIP — W0~W8 구현 완료 · 잔여: 운영(폐쇄망) 산출 · P2 실 LLM 실측**(§9) — 게이트 G-1~G-4 사용자 확정(2026-10-01 · §7) · 결정 **D-294 본문 등재**
> **성격**: 현행 실측(서브에이전트 3갈래 조사 + 직접 실측) + 게이트 G-1 지시 검토 판정 + 설계 + 작업 분해
>
> **요청 취지(사용자 지시 원문, 2026-10-01)**: *"조사한 폴스타의 사례처럼 itam의 db도 yaml을 자동으로 만들거나 유사어를 등록하거나 코드 상수를 넣거나 하는 등의 작업이 스키마 파싱 기능과 관련 스키마 데이터 조회를 통해 진행되도록 해야 한다."*
>
> **상위·인접 계획**: `plans/104`(관리자 「DB 구조」 O-1~O-9 — 이 계획은 그 뒤에 붙는 자산 생성 단계) · **D-292**(DDL 스키마 등록 — O-2 대체 입력) · `plans/95`(ITAM 연동 — W-6 구조 정본 · W-7 지식·유사어를 104가 받기로 한 것의 실행 수단) · `plans/102`(폴스타↔ITAM 교차 질의 — `entity_keys` 공급원 대기, 처분 A)
> **관련 결정**: D-003(읽기 전용 3중 방어) · D-004(LLM 출력에 정합성을 맡기지 않음) · D-089(DB 특화 로직은 `db_adapters/`) · D-214 ②⑥(방언은 프로필 먼저 · 로컬 산출물 커밋 금지) · D-224 ③(키 대응은 DB별 `entity_keys`) · **D-227 ⑤ R11(개정 대상)** · D-227 ⑦⑨ · D-240(실 LLM = 로컬 MLX) · D-255(매뉴얼 동반) · D-292

---

## 0. 요약

**(1) 요구** — 폴스타가 쿼리 생성에 쓰는 스키마 지식(YAML 프로필·유사어·코드 상수)에 해당하는 것을 ITAM 같은 신규 DB에도 **스키마 파싱(D-292)과 읽기 전용 데이터 조회로 자동으로** 만든다.

**(2) 폴스타 지식은 두 종류다**(실측 §1)

| 종류 | 예 | 읽는 코드 | 신규 DB에 주는 방법 |
|---|---|---|---|
| 데이터 자산(YAML·Redis) | 프로필 `allowed_tables`·`query_examples`·`entity_keys`·`column_synonyms`·`query_guide` · 유사어 시드 · 컬럼 설명 | **대부분 범용** — 값만 채우면 바로 쓰인다 | 생성기를 만든다(대부분 지금 없음) |
| 코드 상수 | `src/db_adapters/polestar/` 전용 프롬프트·SQL 조립기·검증기 13종 | 폴스타 전용 | **G-1 판정(§2)**: 프롬프트 템플릿은 LLM이 생성(데이터 파일) · 조립기·검증기는 데이터 키 + 범용 소비 |

**(3) 만들 자산(G-4 — 1차 범위 4종 전부)**

| # | 자산 | 저장 | 만드는 방법 | LLM |
|---|---|---|---|---|
| A1 | 테이블 관계 `relationships`(신규 키) | 프로필 | 선언 FK + 기본키 일치 추론 + **값 겹침 조회로 검증** | 0 |
| A2 | 조회 대상 `allowed_tables` | 프로필 | 행 수(카탈로그) · 관계 연결 · 테이블 군(접두) → 후보 · 관리자 선택 | 0 |
| A3 | 컬럼 설명·유사어 | 설명 초안(기존 흐름) · 시드 `column_synonyms` | **DB 주석**(카탈로그 조회 · DDL `COMMENT`) — 주석 없는 컬럼만 기존 LLM 초안 | 0 |
| A4 | 코드값과 의미 | 프로필 `code_values` · 시드 `column_values` | 후보 컬럼 `DISTINCT` 조회(값 ≤50) · 주석 열거(`1:정상,2:장애`) 파싱 · 공통코드 테이블 값 겹침 | 0 |
| A5 | 교차 질의 식별 키 `entity_keys` | 프로필 | 이름·주석 + **값 형식 조회**(IP·호스트명 비율 · 쉼표 다중값) | 0 |
| A6 | 쿼리 규칙 `query_rules`(신규 키) | 프로필 | 값 형식 판정 → 결정적 문장(예: `CHAR(8)` = `YYYYMMDD` 문자열 날짜 · `Y/N` 플래그 · 다중값 칸) | 0 |
| A7 | 쿼리 예시 `query_examples` | 프로필 | LLM 초안 → **결정적 검증 + 실제 실행 성공** | O |
| A8 | DB 전용 프롬프트 섹션 | `config/knowledge/{db_id}/prompt_template.yaml` | A1~A6 요약을 LLM에 주고 **DB 전용 규칙 섹션만** 생성 → 결정적 검증 → 범용 생성 템플릿 어댑터가 적용 | O |

**(4) 한 줄 권고** — 자산 생성은 「초안 → 결정적 검증 → 관리자 검토 → 승인 → 버전·되돌리기」(104 구조 초안과 같은 결)로 하고, 쓰기는 자산 종류마다 한 곳으로 모은다. LLM은 A7·A8에만 쓰고 출력은 결정적 검증과 실행으로 거른다(D-004).

---

## 1. 현황 실측 (2026-10-01 · HEAD `22f9628` + 작업 트리)

### 1.1 폴스타 자산과 소비처 (서브에이전트 조사 · 대표 `config/db_profiles/polestar_cm_gp.yaml`)

- **범용 소비**: `allowed_tables`(`schema_analyzer`·`multi_db_executor`·`cache_manager._resolve_allowed_tables`) · `query_examples`(`prompt_blocks.build_query_examples`) · `column_synonyms`(`cache_manager._load_profile_column_synonyms`) · `entity_keys`(`entity_key_manifest`→`key_bridge`) · `query_guide`(`_format_structure_guide` 단일 · `multi_db_executor` 멀티) · `excluded_join_columns`·`value_joins`·`known_attributes`(EAV 범용 블록·검증).
- **런타임 소비처 0**: 프로필 `code_values`(관리자 초안·병합에만 등장) · `patterns[hierarchy]` · `lob_flag_column`.
- **폴스타 전용 코드**: 데이터·알람 시스템 프롬프트(`prompts.py` `_SYSTEM_TEMPLATE_SKELETON`·`_ALARM_TEMPLATE_SKELETON`) · 결정적 조립기(폼필 피벗·시맨틱 피벗·알람·급증) · 검증기 13종 · 엔티티 프로브. 선택은 `POLESTAR_DB_IDS` + `adapter.owns()`.
- **지식 카탈로그**(`config/knowledge/`) 빌더는 범용이지만 **스키마가 폴스타 모양**(EAV·지표 measure·알람 severity) — 자산 DB에는 해당이 적다(이 계획 비범위 §6).
- **열 값 유사어**(시드 `column_values` → Redis `synonyms:column_values`)는 `input_parser._apply_column_value_synonyms`가 필터 값을 DB 조건으로 치환하는 데 쓴다(`src/nodes/input_parser.py:736`).

### 1.2 자동 생성기 현황 (104 흐름 · 서브에이전트 조사)

- 있음: 스키마·스냅샷(O-2 · D-292) · LLM 컬럼 설명·유사어(O-4) · DB 설명(O-5) · 구조 `patterns`·`query_guide`·`code_values`(O-6) · EAV 값 인덱스(O-7).
- **없음**: `allowed_tables` · `query_examples` · `entity_keys` · 프로필 `column_synonyms` · 관계 추론 · 시드 파일(비폴스타) · 지식 파일 · 주석 수집(MCP `get_table_schema`는 주석을 돌려주지 않고, DDL 주석은 미리보기에만 쓰인다).
- 데이터 조회 수단: `collect_code_values`(`SELECT DISTINCT … IS NOT NULL` + 엔진별 LIMIT · 식별자 정규식 · 스냅샷 실존 · DB2 스키마 한정 · `SQLGuard.is_safe_select`) · 서버 변수 조회(`assert_constant_select` — `@@` 패턴만 예외).
- **`SQLGuard`는 `INFORMATION_SCHEMA.`·`sys.` 직접 조회를 인젝션 패턴으로 막는다**(`src/security/sql_guard.py:35,42`) → 카탈로그 조회(주석·행 수)는 서버 변수와 같은 「코드 상수 조회」 예외가 필요하다.

### 1.3 ITAM 현황 (서브에이전트 조사)

- 운영 108테이블 · 6군(`TCDMSAM`·`BR`·`CM`·`GT`·`HR`·`IF`) · 선언 FK 0(전사본 기준) · 운영 MCP 연결(`SDQ000`) 미구성 · 레지스트리 `db_schema: ""`(G-2 확정값 `INST1` 미반영).
- `config/db_profiles/itam.yaml`(20줄)은 **로컬 샌드박스 승인본**(`environment: local_sandbox`) — `patterns: []` · LLM `query_guide`가 **"JOIN 관계 부재"** 로 적혀 프롬프트에 주입 중 · **git 추적 중(D-214 ⑥ 위반 · 게이트 테스트 실패 · 처분은 104 R-7 사용자 결정 대기)**.
- 로컬 샌드박스(`testdata/itam`, MariaDB 3307 `INST1`)는 **2테이블 축소판**(IF80 68컬럼·IF79 9컬럼 · 복합 PK 3열 · FK 없음 · 주석 없음 · 합성 시드).
- `scripts/itam_erd.py`: `information_schema` 읽기 전용 수집 → 스냅샷 JSON·Mermaid ERD(선언 FK 실선 / **기본키 일치 추론 점선**)·정의서. 운영 실행 산출물 없음.

### 1.4 어댑터 디스패치의 숨은 가정 (직접 실측)

- `get_adapter()`가 None이 아니면 **폴스타 알람 조립기**를 쓰는 곳이 2곳 있다 — `src/nodes/query_generator.py:1170`(`try_deterministic_alarm_sql`) · `src/nodes/multi_db_executor.py:573`. 두 번째 어댑터를 등록하면 그 DB의 알람 의도 질의에 폴스타 SQL이 조립된다. 나머지 호출부(`tools/validation.py:41` · `query_validator.py:223` · `result_organizer.py:415` · `orchestration/entity_link.py:66` · `tools/metrics.py:44`)는 훅을 `getattr`로 확인하거나 `validator_checks()`를 부르므로 안전하다.

---

## 2. G-1 지시 검토 — 「어댑터 자동 생성 + LLM으로 프롬프트 템플릿 생성」은 적절한가

**지시**: *"2번으로 하고 LLM을 통해 프롬프트 템플릿을 생성하도록 하는 방안을 검토하여 적절하면 진행하고 아니면 1번으로 진행하라."*

**판정: 프롬프트 템플릿 생성은 아래 형태로 제한하면 적절하다 → 진행. Python 어댑터 코드(조립기·검증기) 생성은 부적절하다 → 그 부분은 1번(데이터 키 + 범용 소비).**

| 검토 항목 | 판단 | 근거 |
|---|---|---|
| 템플릿을 LLM이 만드는 것 | **적절(제한형)** | 온보딩 때 1회 생성 → 결정적 검증 → 관리자 승인 → 버전 고정. 질의마다 바뀌지 않아 비결정성이 질의 경로로 새지 않는다(D-004). 승인 뒤 고정 문자열이라 프롬프트 접두·KV 캐시도 안정적이다 |
| 템플릿 **전체**를 LLM이 쓰는 것 | 부적절 | 범용 템플릿의 안전·형식 계약(SELECT 전용 · 행 제한 · `{schema}` 등 자리표시자 · ```sql 출력 형식 — 응답 파서가 기대)을 잃을 수 있다 → **프레임은 코드 소유, LLM은 「DB 전용 규칙 섹션」만** 쓴다 |
| 사실 오류(없는 테이블·컬럼) | 통제 가능 | 섹션의 식별자(백틱·`table.column`)를 스냅샷과 대조 · 섹션 안 SQL 예시는 `SQLGuard` + 실제 실행 성공 필수 · 실패하면 승인 불가(409) |
| Python 코드 생성 | **부적절** | 실행 중 앱이 LLM 출력을 코드로 쓰고 불러오면 임의 코드 실행 경계가 생긴다 · 조립기·검증기의 정답성은 자동 검증이 어렵다 · 리뷰·배포·`arch_check`·`overfit_check` 경로 밖이다 |
| 적용 수단 | 범용 어댑터 1개 | `GeneratedTemplateAdapter`(`src/db_adapters/generated.py`) — 승인된 템플릿 파일이 있는 db_id를 담당(폴스타 어댑터가 먼저 등록돼 폴스타 DB 영향 0) · `system_template()` = 프레임 + 섹션 · `validator_checks()` = `[]`. **선행 교정 §1.4 2곳**(알람 조립은 폴스타 어댑터일 때만) |

D-214 ②(「`src/db_adapters/itam/`은 반복 실패 실측 뒤 사람이 만든다」)는 **개정하지 않는다** — 생성 템플릿 어댑터는 DB 전용 코드가 아니라 데이터 파일을 읽는 범용 코드다.

---

## 3. 설계

### 3.1 흐름 — 「DB 구조」 상세에 「자산 자동 생성」 단계 추가

```
[스냅샷(O-2 MCP 또는 D-292 DDL)]
   │
   ├─ P1 프로파일링 잡(LLM 0)  ── 카탈로그 조회(주석·행 수) + 데이터 조회(값 분포·형식·겹침)
   │      → A1~A6 제안 + 근거(건수·비율·조회 SQL)
   ├─ P2 LLM 보조 잡(선택)     ── A7 쿼리 예시 · A8 DB 전용 섹션(입력 = P1 결과 요약)
   │      → 결정적 검증(식별자 실존 · 금지어 · 자리표시자 · 실행 성공)
   ▼
[자산 초안] ── 자산별 포함/제외 · 근거 · 현행 대비 diff
   │ 승인
   ▼
프로필(A1·A2·A4·A5·A6·A7) · 시드(A3·A4) · 지식(A8) · 설명 초안(A3 → 기존 「설명 초안 적용」)
   └─ 종류마다 쓰기 한 곳 · 적용 직전 현행 v0 보관 · 버전 · 되돌리기
```

### 3.2 데이터 조회 — 전부 읽기 전용 · 코드 조립

| 조회 | 엔진별 원천 | 안전 |
|---|---|---|
| 주석(테이블·컬럼) | MariaDB `information_schema.COLUMNS.COLUMN_COMMENT`·`TABLES.TABLE_COMMENT` · PG `col_description`/`obj_description` · DB2 `SYSCAT.COLUMNS.REMARKS`·`SYSCAT.TABLES.REMARKS` | **모듈 상수 SQL** + 스키마명은 식별자 정규식 통과 값만 · `SQLGuard` 금지어·인젝션 검사에서 카탈로그 패턴(`INFORMATION_SCHEMA.`·`sys.`)만 예외(서버 변수 조회 `assert_constant_select`와 같은 방식) |
| 행 수 추정 | MariaDB `TABLES.TABLE_ROWS` · PG `pg_class.reltuples` · DB2 `SYSCAT.TABLES.CARD` | 같음 |
| 값 종류(코드 후보) | `SELECT DISTINCT col … IS NOT NULL` + 엔진별 행 제한(기존 `collect_code_values` 재사용) | 식별자 정규식 · 스냅샷 실존 · DB2 스키마 한정 · `SQLGuard.is_safe_select` · 대상별 실패 격리 |
| 값 형식 표본 | `SELECT DISTINCT col … FETCH/LIMIT 200` | 같음 · **값은 저장하지 않고 비율만** 남긴다 |
| 관계 값 겹침 | 자식 키 표본(≤200 · 복합 키) 중 부모에 있는 비율 — `EXISTS` 1문장 | 같음 |
| 예산 | 잡당 조회 상한(기본 400 · 넘으면 남은 후보를 「예산 초과」로 표시) · MCP 서버 `readonly`·`max_rows`·타임아웃 | 침묵 생략 금지 — 생략 건수 표시 |

### 3.3 자산별 규칙

- **A1 관계**: 후보 = 선언 FK ∪ 기본키 일치 추론(부모 PK 컬럼이 전부 자식에 있음 · 테이블 절반 넘게 나오는 공통 컬럼만으로 된 PK는 부모 제외 · 진부분집합 후보 제거 — `scripts/itam_erd.py` 규칙을 `src/domain/schema_inference.py`로 옮긴다 — 스크립트는 「표준 라이브러리 + `pymysql`만으로 MCP 호스트에 단독 복사해 실행」하는 설계라 `src`를 import하지 않게 두고, **두 구현의 결과 동등성 테스트**로 표류를 막는다) → 추론 후보는 **값 겹침 ≥ 0.9**(기본)일 때만 채택 · 프로필 `relationships: [{from, to, origin: declared|inferred, overlap}]` · 소비: `schema_analyzer`가 관련 테이블 사이 관계를 스키마 관계 목록에 더한다(단일·멀티 대칭 · 렌더에 `(추론)` 표시).
- **A2 조회 대상**: 후보 = 행 수 > 0 · 관계로 연결된 테이블 우선 · 군(접두) 표시 → 관리자가 고른다 · 프로필에 이미 있으면 바꾸지 않는다(사람 값 보존).
- **A3 주석**: 카탈로그 주석 ∪ DDL 주석(D-292 해석 결과에 주석을 싣고, 등록 시 기준선 옆에 보관) → **설명 초안(출처 `comment`)** 을 기존 「설명 초안 검토·적용」에 넣는다 · 짧은 주석(라벨형)은 유사어 후보로 시드 `column_synonyms`에 · 주석 없는 컬럼만 O-4 LLM 범위로 남긴다.
- **A4 코드값**: 후보 컬럼 = 문자열 길이 ≤ 20 또는 정수 · 이름·주석 단서(코드·구분·여부·상태·유형·`_CD`·`YN`·`TYPE`·`STAT`) → `DISTINCT` 값 ≤ 50이면 코드 컬럼 · 의미(라벨): ① 주석 열거 파싱(`1:정상, 2:장애` · `Y=사용/N=미사용`) ② 공통코드 테이블 후보(코드·이름 컬럼 쌍 · 값 겹침 ≥ 0.9)에서 라벨 조회 → 프로필 `code_values: {table.column: [값]}` · 시드 `column_values: {table.column: {라벨: {op: "=", value: 값}}}`.
- **A5 식별 키**: 이름·주석 단서(host·호스트·ip·IP) + 값 형식 비율(IPv4 ≥ 0.9 · 호스트명 ≥ 0.9) · 쉼표 다중값 비율 → `multi_value` · 대소문자 혼재 → `compare: casefold` · 프로필에 이미 있으면 바꾸지 않는다.
- **A6 쿼리 규칙**: `CHAR(8)`/`VARCHAR(8)` 값이 `YYYYMMDD` ≥ 0.95 → 문자열 날짜 규칙 · 14자리 `YYYYMMDDHH24MISS` · `Y/N`·`0/1` 플래그 · 다중값 칸(쉼표) → `LIKE` 안내 · 결정적 문장 목록 `query_rules`.
- **A7 쿼리 예시**: LLM 입력 = 허용 테이블 컬럼·주석·관계·코드값·규칙 요약 · 출력 N건(기본 5) → `SQLGuard` · 식별자 실존 · 행 제한 · **실제 실행 성공**인 것만 · 프로필 `query_examples`에 질문 중복 없이 추가(사람 예시 보존).
- **A8 DB 전용 섹션**: 같은 입력 → 섹션(마크다운 · 상한 6,000자) → 중괄호 금지 · 식별자 실존 · 금지어 · 라우팅 어휘(위치·존) 금지 · 섹션 안 SQL 실행 성공 → 파일 저장 · 생성 템플릿 어댑터가 범용 프레임의 구조 안내 뒤에 「## DB 전용 규칙(관리자 승인)」으로 넣는다.

### 3.4 저장 — R11 개정(G-2 · D-294)

| 자산 | 파일 | 쓰는 곳(한 곳) | 버전 |
|---|---|---|---|
| A1·A2·A4·A5·A6·A7 | `config/db_profiles/{db_id}.yaml` | `StructureStore.apply_profile`(기존) — `profile_merge`에 새 키 병합 규칙 | 기존 프로필 버전 |
| A3(유사어)·A4(값 유사어) | `config/synonym_seeds/{db_id}.yaml` | 신규 `AssetFileStore.apply("seeds")` → 기존 O-7 로더로 Redis 반영 | `.cache/structure/{db_id}/assets/seeds/v{N}.yaml` |
| A8 | `config/knowledge/{db_id}/prompt_template.yaml` | `AssetFileStore.apply("prompt_template")` | `.cache/structure/{db_id}/assets/prompt_template/v{N}.yaml` |
| A3(설명) | Redis `:descriptions` + 백업 | 기존 설명 초안 적용 | 기존 |

- 병합 원칙: **사람 값 보존**. `relationships`·`query_examples`·`code_values`·`query_rules`는 합집합(사람 항목 우선) · `allowed_tables`·`entity_keys`는 비었을 때만 · 시드는 생성 머리말 + 사람 단어 보존 합집합.
- 로컬 샌드박스: 파일 머리말 + `environment: local_sandbox` · **git 추적 게이트 테스트를 시드·지식 파일까지 확장**(D-214 ⑥).
- 여전히 쓰지 않는 것: `config/db_registry.yaml` · `.env` · `mcp_server/`(조각 내보내기만).

### 3.5 화면·API

- API(전부 `require_admin_user` + `ADMIN_ACTION`): `GET {source}/assets`(초안 목록·파일 버전) · `POST {source}/assets/profile`(P1 잡 · 202) · `POST {source}/asset-drafts/{id}/llm`(P2 잡 · 202 · 화면이 provider 확인) · `POST {source}/asset-drafts/{id}/approve`(자산별 포함 목록 · 조회 대상 선택) · `…/reject` · `POST {source}/assets/{kind}/versions/{ver}/rollback`(`seeds`·`prompt_template`).
- 화면: 상세에 「자산 자동 생성」 섹션 — 실행 버튼 2개 · 초안 카드(자산별 체크 · 근거 표 · 현행 대비 diff · 검증 결과) · 승인/반려 · 자산 버전 표. 매뉴얼 A-NN 신설(D-255).

---

## 4. 작업 분해

| WU | 내용 | verify |
|---|---|---|
| **W0** | 선행 교정 — 알람 결정적 조립 2곳을 「알람 조립을 소유한 어댑터(`deterministic_alarm` 표시 = 폴스타)일 때만」으로(§1.4 · 클래스 import 대신 표시 — 노드 간 import 경고·폴스타 리터럴 무증가) | 비폴스타 어댑터가 등록돼도 알람 조립 미호출 테스트 · 폴스타 회귀 0 |
| **W1** | `src/domain/schema_inference.py` — 관계 추론 · 값 형식 분류 · 코드 후보 · 주석 열거 파싱 · 식별 키 판정 · 규칙 문장(순수 함수) · `itam_erd.py`는 단독 실행 설계라 그대로 두고 결과 동등성 테스트로 묶는다 | 단위 테스트 · 스크립트 구현과 동등 · `itam_erd` 기존 19건 통과 |
| **W2** | `src/schema_cache/schema_probe.py` — 엔진별 카탈로그 상수 조회(주석·행 수) · 값 표본·값 겹침 조회 · 예산 | 가짜 MCP 세션으로 세 엔진 SQL 모양·가드 통과 · 실패 격리 |
| **W3** | `AssetFileStore`(시드·지식 버전·되돌리기·샌드박스 표기) · `profile_merge` 새 키 규칙 · 게이트 테스트 확장 | 되돌리기 바이트 동일 · 사람 값 보존 · 추적 게이트 |
| **W4** | 자산 생성 서비스 — P1 잡(A1~A6) · P2 잡(A7·A8) · 결정적 검증 · 자산 초안 저장·승인·반려 · 주석 → 설명 초안 | 서비스 통합 테스트(가짜 MCP·목 LLM) · LLM 0 단언(P1) |
| **W5** | 범용 소비 — 관계 병합(단일·멀티) · `query_rules`·`code_values` 블록(공용 빌더 · 양쪽 주입) · `GeneratedTemplateAdapter` 등록 | 폴스타 프로필(새 키 없음) 프롬프트 바이트 불변 · 생성 템플릿 DB만 전용 섹션 |
| **W6** | API · 화면 · 매뉴얼 · 캡처 | API 테스트 · 화면 경로 계약 가드 · `tests/test_manual` |
| **W7** | 로컬 실측 — MCP 9099 `itam`(`INST1` 2테이블) P1 실행 · P2는 두 평면 `mlx`일 때만(D-240) · 산출물은 커밋하지 않는다 | 실행 로그·조회 수·근거 |
| **W8** | 문서 — D-294 본문 등재 · D-227 ⑤ R11 개정 부기 · `plans/95`·`plans/102` 연결 부기 · INDEX | — |

---

## 5. 성공 기준

1. 같은 스냅샷·같은 데이터에서 P1 결과가 **같다**(결정적 · LLM 0).
2. 생성 자산은 승인 전 어떤 파일·Redis 정본도 바꾸지 않는다(초안만).
3. 사람이 쓴 프로필 키·시드 단어는 승인 뒤에도 남는다.
4. 데이터 조회는 전부 `SELECT` · 코드 조립 · 식별자 검증을 거치고, 조회 수가 예산 안이며 생략은 표시된다.
5. 폴스타 4종의 쿼리 생성 프롬프트는 바이트 불변이다.
6. 생성 템플릿이 승인된 DB만 전용 섹션을 받고, 알람 의도에 폴스타 조립기가 붙지 않는다.

## 6. 위험 · 비범위

- **비범위**: 지식 카탈로그(`catalog.yaml` — 폴스타 모양) · 시맨틱 모델 · Python 조립기·검증기 생성(§2) · 레지스트리 `db_schema` 수정(R11 유지 — 조각만) · `itam.yaml` git 추적 처분(104 R-7 사용자 결정) · 기존 LLM `query_guide`("JOIN 관계 부재") 교체(관리자 편집 · 생성 자산의 관계·규칙 블록이 함께 실린다).
- **유사어 적재 자리(`plans/132` 협의)**: 시드의 값 유사어(`column_values`)는 DB 공용 사전(전역 키)에 들어간다. 처음에는 폴스타 해석 오염을 우려해 적재를 보류하기로 했으나, `plans/132` G-13 사용자 확정(2026-10-01 — 「폴스타와 ITAM은 유사어를 같이 사용해도 된다 · 제니퍼·RAG 등만 분리」)으로 **해소**됐다 — 승인 시 기존 O-7 로더로 DB별 `column_synonyms`와 DB 공용 `column_values`(테이블 한정 키)를 함께 적재한다.
- **위험**: ① 운영 108테이블 조회 비용 → 예산·후보 축소 ② 코드값이 민감 값일 가능성 → 값 ≤ 50 · 길이 상한 · 표본 값 미저장(비율만) ③ 추론 관계 오탐 → 값 겹침 하한 + 관리자 검토 ④ 로컬 샌드박스가 2테이블이라 관계·공통코드 실측이 안 된다 → 가짜 MCP 세트로 검증하고, 운영 산출은 폐쇄망 관리자 화면에서 만든다(D-214 ⑥).

## 7. 사용자 확정 게이트 (2026-10-01)

| 게이트 | 질문 | 확정 |
|---|---|---|
| G-1 | 폴스타 「코드 상수」에 해당하는 것 | **2번(어댑터 자동 생성) — LLM으로 프롬프트 템플릿 생성 방안을 검토해 적절하면 진행, 아니면 1번(데이터 키)** → §2 판정 |
| G-2 | 저장 위치 | **별도 파일도 앱이 직접 씀**(R11 개정) |
| G-3 | 생성 방식 | **결정적 우선 + LLM 보조**(DDL 주석도 설명·유사어 후보로 — D-292 G-3 「미리보기만」을 이 계획 범위에서 확장) |
| G-4 | 1차 범위 | **관계·조회 대상 · 주석 → 설명·유사어 · 코드값·식별 키 · 쿼리 규칙·예시**(4종 전부) |

## 8. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-01 | 작성 — 서브에이전트 3갈래 실측 · G-1~G-4 확정 · §2 판정 · D-294 예약 |
| v1.1 | 2026-10-01 | **W0~W8 구현 · D-294 본문 등재**(§9) — 로컬 실측이 드러낸 보완 2건(같은 기본키 군 양방향 측정 · 5테이블 미만 공통 컬럼 규칙 해제) · 시드 적재는 `plans/132` G-13(폴스타·ITAM 유사어 공유)에 따라 기존 O-7 로더 |
| v1.2 | 2026-10-02 | **코드 리뷰 반영**(§9.5) — LLM SQL 실행 방어(질의 경로 `validate_sql` 주입 · 부수효과 함수 차단 · 바깥 행 제한 · 모든 코드 펜스 검사 · 구조 실패 시 미실행) · 명시 조회 대상 덮어쓰기 · `unchanged` 보고 · 부분 적용 기록 · `code_labels` 코드값 단위 병합 · 카탈로그 잘림·스키마 일치·DB2 대문자 |

## 9. 구현 현황 (v1.1 · 2026-10-01)

### 9.1 WU 상태

| WU | 상태 | 산출 |
|---|---|---|
| W0 | 완료 | `query_generator._try_deterministic_alarm_single` · `multi_db_executor._deterministic_alarm_sql_or_none` — `deterministic_alarm` 표시 어댑터만 · `PolestarAdapter.deterministic_alarm = True` |
| W1 | 완료 | `src/domain/schema_inference.py` — 관계 추론(`min_tables_for_common` 추가) · 값 형식 · 코드 후보 · 주석 열거·라벨 · 식별 키 · 규칙 문장 · 식별자 추출 |
| W2 | 완료 | `src/schema_cache/schema_probe.py` — 카탈로그 상수 조회(3엔진) · `sample_distinct` · `check_overlap` · `sample_pairs` · `ProbeBudget` |
| W3 | 완료 | `src/schema_cache/asset_store.py`(`AssetFileStore`) · `profile_merge`(`UNION_LIST_KEYS`·`DICT_FILL_KEYS`·채움 키 확장) · `StructureStore` 자산 초안·DDL 주석 메서드 · 추적 게이트 확장 |
| W4 | 완료 | `src/schema_cache/asset_generation_service.py` · `src/prompts/asset_generation.py` · DDL 등록이 주석 보관(D-292 G-3 확장) |
| W5 | 완료 | `src/utils/schema_utils.attach_profile_relationships`(단일·멀티) · `prompt_blocks.build_profile_rules_block`(양쪽 같은 자리) · `src/db_adapters/generated.py` + `get_adapter`의 `bind` 훅 · 멀티 경로 `multi_path_always` |
| W6 | 완료 | API 6종(`src/api/routes/db_structure.py`) · 화면 「스키마 자산 자동 생성」 섹션(`admin-db-structure.js`) · 매뉴얼 A-67 · 캡처 `dbs-assets` |
| W7 | 부분 | 로컬 P1 실측 완료(§9.3) · P2 실 LLM 미실행(공유 MLX 서버를 병행 세션이 실측에 쓰는 중 — 동시 사용 OOM 위험) |
| W8 | 완료 | D-294 본문 · D-227 ⑤ R11 개정 부기 · D-292 G-3 확장 부기 · INDEX |

### 9.2 검증

- 신규 테스트 65건(추론 22 · 조회 14 · 저장·병합 5 · 서비스 7 · 소비 11 · API 6) + 계약 테스트 갱신 3건 — 전부 통과.
- 관련 스위트 6,756 passed · 실패 1건 = 세션 시작 커밋(`22f9628`)에서도 같은 기존 실패(`config/db_profiles/itam.yaml` 로컬 샌드박스 표기 추적 — 104 R-7).
- `arch_check --ci` error 0 · 경고 90(기준선 동일) · `overfit_check --ci` 신규 유입 0 · ruff 변경 줄 신규 0 · mypy(3.12) 신규 0(노드 4파일 기준선 대비 −1) · `tests/test_manual` 515 passed.

### 9.3 로컬 실측 (MCP 9099 · `itam` 2테이블 · 저장소 페이크 · LLM 0)

- 조회 31회(예산 400) · 파일 쓰기 0.
- **관계**: `TCDMSIF80`→`TCDMSIF79` 3열 기본키(같은 기본키 군 · 값 겹침 0.967) 채택 — 첫 실행은 0건이었다(2테이블에서 공통 컬럼 규칙이 모든 기본키 컬럼을 「공통」으로 보아 부모 후보가 사라짐) → `MIN_TABLES_FOR_COMMON = 5`로 보완.
- 쿼리 규칙 5건(`YYYYMMDD` 문자열 날짜) · 식별 키(호스트명 `casefold` + IP) · 코드 컬럼 14(합성 시드라 값이 `Z…` 1~2개) · 주석 0(샌드박스에 주석 없음).

### 9.4 잔여

1. **운영 산출** — 폐쇄망에서 운영 MCP(`SDQ000`) 연결 뒤 관리자 화면으로 P1 → 검토 → 승인(운영 108테이블 · 예산 400이 부족하면 범위 테이블을 나눠 실행).
2. **P2 실 LLM 실측** — 두 평면 `mlx`(D-240)·MLX 서버 여유 시 쿼리 예시·섹션 검증 통과율 확인.
3. 기존 로컬 승인본 `query_guide`("JOIN 관계 부재") 교체 — 관리자 편집(사람 값 보존 원칙상 자동 교체하지 않음).
4. ~~값 유사어 치환 키 형식~~ → **실측 확정(2026-10-02 · `plans/132` W6)**: 시드 `column_values`의 테이블 한정 키(`T_SRV.STATCD`)는 입력 파서 값 치환(`col == field or field.endswith(col)`)에서 맨 컬럼명 필드(`STATCD`)와 맞지 않아 **치환이 일어나지 않는다** — 폴스타 시드와 같은 기존 동작이고, 사용자 G-16(치환 위치·방식 현행 유지)에 따라 바꾸지 않는다(`tests/test_orchestration/test_plan132_w6_source_synonyms.py` 고정). ITAM 코드값 의미는 프롬프트 「코드값」 블록(`code_labels`)으로 LLM에 전달되므로 그 경로로 쓰인다.

### 9.5 코드 리뷰 반영 (v1.2 · 2026-10-02)

서브에이전트 검토(REQUEST CHANGES · Critical 0 · Important 4)를 모두 반영했다.

| 지적 | 반영 |
|---|---|
| 승인 전 실행하는 LLM SQL 방어가 약함(부수효과 함수 · 스냅샷 밖 테이블 · 바깥 행 제한 없음 · 표기 없는 펜스 우회 — DB 주석 경유 간접 프롬프트 주입 경로) | `_execute_check`: 문장 형식 → `SQLGuard` → 부수효과 함수 차단 → 주입된 `sql_checker`(= 질의 경로 `validate_sql`, API 조립부 `asset_sql_checker`) → `SELECT * FROM (…) q` + 엔진별 행 제한으로 실행 · 검증기 없으면 실행 안 함 · 섹션은 모든 펜스 · 블록 ≤ 5 · 구조 실패 시 미실행 |
| 관리자가 고른 `allowed_tables`가 조용히 무시됨 | 명시 선택은 병합 뒤 그대로 덮어씀 · 바뀌지 않은 자산은 `unchanged` 보고 · 화면 기본값은 현행 목록 |
| 부분 적용 · 감사 누락 | 첫 쓰기 뒤 실패는 `partially_applied` + `apply_errors`로 정상 응답(감사 남음) · 첫 쓰기 전 실패는 쓰기 0 |
| `code_labels`·`code_values` 어긋남 | 코드값 단위 병합(base 라벨 우선) |
| (제안) 카탈로그 잘림 · 다중 스키마 오부착 · DB2 소문자 스키마 · 이상값 소실 | 잘림 사유 기록 · 스키마 일치 확인 · DB2 대문자 · 리스트·dict 아닌 사람 값 보존 |
| (제안 · 미반영) 예약어 컬럼 인용 · 조립 실패 조회의 예산 차감 · PG 비 public 접두 | 기존 `build_code_value_sql`과 같은 규칙이라 그대로 둠 — 예약어 컬럼은 사유가 남고 해당 자산만 빠진다 |
