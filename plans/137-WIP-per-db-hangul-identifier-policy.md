# 137. DB별 한글 식별자 허용 정책 — SQL 한글 토큰 가드(D-104)를 DB 설정으로 분기

> **작성일**: 2026-10-06
> **상태**: **v2.4 · W1~W8(`9e85556` · D-297) · W9~W12(`1ac2f93` · D-305) · W13 구현(D-306 — 집계 표 NULL 묶음 「(값 없음)」) · 잔여 = 폐쇄망 재검증(§8.7 · §9.4)** — 파일명 `-WIP`
> **개정 이력**: v1(2026-10-06 작성) → v1.1(2026-10-06 사용자 답 반영 — G-2 레지스트리 · G-4 폴스타 비고려 = 허용 off DB 현행 비트 동일 · G-5 테이블명 `TCDMS…` 영문 · DDL 불가 · SELECT 권한만 · 호출부 5번째(자산 생성 SQL 검증기) · 설정 조각 자동 제안 W8 추가) → v1.2(2026-10-06 — G-1 ⓐ 스키마 대조 · G-3 ⓐ 검증 오류 재생성 권고안 확정 · 허용 집합 출처·별칭 규칙 §3.2 2-1 · G-6 확인 방법) → v1.3(2026-10-06 — G-6 운영 `sql_mode` 실측값 수령 · ANSI_QUOTES off · W3 진행 확정) → v2(2026-10-06 — W1~W8 구현 · D-297 등재 · 파일명 `-TODO` → `-WIP`) → 번호 이동(2026-10-06 — 원 작성 번호 135가 `main`의 `plans/135`(ITAM 질의 벤치마크)와 겹쳐 137로 옮겨 `main` 최신 커밋 위에 다시 적용 · 내용 변경 없음) → v2.1(2026-10-06 — 폐쇄망 1차 결과 반영: 원인 A `NULLS LAST` 결정적 부가가 MariaDB 구문 오류 · 원인 B 테이블 선택 요약 15컬럼 절단 · 추가 개선 W9~W12 · 게이트 G-7) → v2.2(2026-10-06 — G-7 ⓑ 확정 · D-202 2차 개정 승인 · W9~W12 구현 · D-305 등재) → v2.3(2026-10-06 — origin `71d7ff6`(결정 문서 구조 변경 D-304) 위로 재적용 · D-304 번호 선점으로 이 작업의 결정 번호를 D-305로 · 결정 문서를 새 구조로 재작성) → v2.4(2026-10-06 — 폐쇄망 재검증 2차: W9·W10 효과 확인 · 정합성 쿼리로 빈 줄 = NULL 묶음 확정 · W13 「(값 없음)」 표시 채택·구현 · D-306)
> **요청(사용자 2026-10-06)**: *"현재 프로젝트에서 DB 쿼리 생성시 SQL에 한글이 포함되어 있을 경우 생성 실패를 반환하도록 되어있음. 폴스타의 경우 문제 없었으나, ITAM의 경우 DB 칼럼명을 한글로 구성하였음을 확인하였음. 따라서 각 DB 설정에 따라 한글로 된 SQL을 허용할 것인지에 대한 여부를 설정하는 기능을 추가해야 함."* → *"plans에 계획으로 정리하고, 확인/검토가 필요한 내용은 별도로 보고하시오"*
> **관련 계획**: `plans/95`(ITAM 편입 — **G-4 물리 식별자** 질문에 대한 사용자 답이 이번 요청의 전제) · `plans/133`(스키마 자산 자동 생성 — ITAM 프롬프트 섹션·`query_guide`) · `testdata/itam/README.md` 「리허설 기록」(MariaDB 큰따옴표 = 문자열 리터럴 실측)
> **관련 결정**: **D-104(개정 대상 — 생성 SQL 한글 토큰 잔존 validator 차단 · 2026-07-22 「변경 이력」)** · D-066(단일·멀티 경로 대칭) · D-088/D-089(공용 검증 코어는 DB를 모른다 · DB 특화는 어댑터) · D-214(ITAM 엔진 = MariaDB) · D-292(DDL 스키마 등록) · 신규 기능 플래그 기본 off = 현행 동작(`plans/80` §5.4-③)
> **D-번호**: **D-297**(2026-10-06 본문 등재 · D-104 개정).

---

## 0. 증거 규칙

- 과금 호출 0 · 서버 기동 0 · DB 호출 0 · 저장소 쓰기는 이 계획서·`plans/INDEX.md`뿐.
- 아래 §1 실측은 HEAD `d1f4b2a`의 함수를 스크래치 스크립트로 직접 호출한 결과다(LLM 0 · DB 0).

## 1. 현행 실측

### 1.1 가드 위치와 호출부

| 위치 | 내용 |
|---|---|
| `src/sql_validation.py:243-263` `find_bare_hangul_tokens()` | 주석·`'…'`·`"…"`를 지운 뒤 `[가-힣]+`가 남으면 토큰을 반환 |
| `src/sql_validation.py:126-138` `validate_sql()` 4.5 | 토큰이 있으면 오류 → 재생성 회귀 (단일 경로 · 멀티 full validation · 도구 · 관리자 「DB 구조」 공용) |
| `src/nodes/multi_db_executor.py:2843-2853` `_validate_sql_simple()` | 같은 함수로 같은 검사(멀티 간이 검증 — `multi_full_validation` 기본 off라 멀티의 실제 경로) |

`validate_sql()` 호출부 4곳과 `_validate_sql_simple()` 모두 **DB 식별자를 검사에 넘기지 않는다** — DB별 분기 지점이 없다.

| 호출부 | DB 식별자 확보 수단 |
|---|---|
| `src/nodes/query_validator.py:229` | `state["active_db_id"]` (어댑터 조회에 이미 사용 중 · 223행) |
| `src/nodes/multi_db_executor.py:2715` `_validate_sql()` → 2748 `_validate_sql_simple()` | `db_id` 인자 보유 · `_validate_sql_simple`에는 미전달 |
| `src/tools/validation.py:16` `validate_sql_draft()` | `db_id` 인자 보유 |
| `src/api/routes/db_structure.py:188` `asset_sql_checker(sql, schema_info, engine)` | **없음** — 자산 생성(`plans/133` · D-294)이 LLM이 쓴 ITAM 예시 SQL을 이 검증기로 거른다. 서명에 DB 식별자가 없어 `AssetGenerationService`가 `source`를 넘기도록 확장해야 한다. **ITAM 한글 컬럼 예시 SQL이 지금은 전부 이 가드에 거부된다** |

### 1.2 스크래치 재현 (HEAD `d1f4b2a`)

| 입력 | `find_bare_hangul_tokens` 결과 | 판정 |
|---|---|---|
| ``SELECT `자산명` FROM TCDMSIF80`` | `['자산명']` | **오탐** — MariaDB 백틱 인용 식별자를 지우지 않는다 |
| `SELECT 자산명, 취득_금액2 FROM TCDMSIF80` | `['자산명', '취득', '금액']` | 토큰이 `[가-힣]+` 단위라 `취득_금액2`가 쪼개진다 — 스키마 대조에 그대로 쓸 수 없다 |
| `SELECT "자산명" FROM TCDMSIF80` | `[]` | 통과 — 그러나 **MariaDB 기본 sql_mode에서는 문자열 리터럴**(아래 1.3) |
| `SELECT t.자산명 FROM TCDMSIF80 t WHERE 해당` | `['자산명', '해당']` | 자연어 잔재 `해당`과 정당한 컬럼 `자산명`을 구별하지 못한다 |

- DDL 파서(D-292 `parse_ddl(…, "mariadb")`)는 `` `자산번호` ``·bare `자산명`·`` `취득_금액2` ``를 **모두 정상 컬럼으로 해석**했다(경고 0). 구조 수집 쪽은 막힘이 없다.
- 컬럼 존재 검사(`_validate_columns`, `(\w+)\.(\w+)`)는 파이썬 `\w`가 한글을 포함해 `t.없는컬럼`을 정상 검출했다. 단 `` `t`.`자산명` ``처럼 백틱으로 감싼 참조는 정규식에 걸리지 않아 **검사 없이 통과**한다(느슨한 쪽 · §5 잔여).

### 1.3 현행 가드가 MariaDB에서 유도하는 침묵 오답

`testdata/itam/README.md` 「리허설 기록」(2026-09-17 · MariaDB 11.4.13 · 기본 sql_mode · `ANSI_QUOTES` 없음) 실측:
`"sevrHostName"`은 **문자열 리터럴**로 평가되고 `WHERE "sevrHostName" = 'svr-web-01'`은 **0행**이다 — SQL 오류가 나지 않아 재생성 회귀조차 발동하지 않는다.

현행 가드의 재생성 지시문은 *"따옴표 안 별칭/문자열 리터럴 외의 한글은 모두 제거하고"*이다. 한글 컬럼 DB에서 LLM이 이 지시를 따르는 가장 쉬운 방법은 **컬럼을 큰따옴표로 감싸는 것**이고, 그 결과가 바로 위 침묵 오답이다. 즉 **ITAM에서 현행 가드는 "실패"에서 끝나지 않고 "조용히 틀린 답"으로 유도할 수 있다.** 이 계획이 허용 플래그만이 아니라 MariaDB 인용 규칙(W2·W3)을 함께 다루는 이유다.

## 2. 목표 · 비목표

**목표**
1. DB 레지스트리 항목 단위로 "구조 영역의 한글 식별자 허용" 여부를 선언한다. 미선언 = 현행과 비트 동일.
2. 허용한 DB에서도 자연어 잔재(`해당`·`현재` 등)는 계속 잡는다 — 허용 범위는 **스키마에 실재하는 식별자**로 한정한다(G-1 확정).
3. MariaDB의 백틱 인용 식별자를 인용으로 인정한다(설정과 무관한 결함 교정).
4. MariaDB에서 큰따옴표로 감싼 컬럼명(침묵 오답)을 결정적으로 잡는다(G-3 확정 · G-6 운영 ANSI_QUOTES off 실측).
5. 단일·멀티·도구·관리자(자산 생성 검증기) 경로에 대칭 배선한다(D-066).
6. **허용 off DB(폴스타 계열 등)는 현행과 비트 동일**하다 — 새 토큰 단위·허용 집합·문구 분기는 허용 on DB에서만 동작한다(G-4 확정).

**비목표**
- ITAM 프롬프트의 방언·인용 규칙 문구 작성 — `plans/95` W-6(`query_guide`)·`plans/133` 소관. 이 계획은 통지만 한다(§5).
- 백틱 참조의 컬럼 존재 검사 강화(§1.2 마지막 항) — 잔여로 기록만 한다.
- PII 필터(FabriX)의 한글 컬럼명 차단 여부 — 별도 실측 대상(§5).

## 3. 설계

### 3.1 설정 — `config/db_registry.yaml` DB 항목 필드 (G-2)

```yaml
  - db_id: itam
    engine: mariadb
    # 구조 영역(따옴표 밖)의 한글 식별자 허용 — 물리 컬럼명이 한글인 DB만 켠다(plans/137).
    # true여도 스키마에 실재하는 테이블·컬럼명만 통과하고 그 밖의 한글 토큰은 종전대로 재생성을 유도한다.
    allow_hangul_identifiers: true
```

- `DBEntry`(`src/routing/registry.py:207`)에 `allow_hangul_identifiers: bool = False` 추가, `parse_registry()`(705행 부근)에서 `bool(raw.get(...))`로 적재.
- 미선언 = `False` = 현행. 폴스타 4종·cloud_portal·itsm은 손대지 않는다.
- `.env`·`AppConfig`에는 두지 않는다 — "신규 DB는 레지스트리 + `.env` 둘만"(CLAUDE.md) 원칙에서 DB 단위 성질은 레지스트리가 정본이다(`engine`·`db_schema`와 같은 층).
- G-1 ⓐ 확정으로 설정은 bool 하나다(전면 허용 모드 없음).
- **설정 변경 절차(G-2 확정 · 사용자 질문 답)**: 바꾸는 곳은 **본체 앱(에이전트)의 `config/db_registry.yaml` 한 파일**뿐이다.
  - `.env`·MCP 서버(`mcp_server/`)·제니퍼 게이트웨이는 손대지 않는다 — MCP 서버의 읽기 전용 검증(`mcp_server/mcp_server/security.py` `validate_readonly`)에는 한글 차단이 없다(grep 실측). 한글 차단은 본체 검증 코어에만 있다.
  - 앱은 레지스트리 파일을 쓰지 않는다(D-227 R11 · 「DB 구조」 O-8 안내 문구 *"레지스트리 반영은 사람이 합니다 · 반영 뒤 앱 재기동"*). 사람이 파일을 고친 뒤 **앱을 재기동**해야 반영된다(레지스트리는 기동 시 적재·캐시).
  - 폐쇄망 배포는 `config/db_registry.yaml` 1파일 복사 + 재기동(코드 배포 뒤 설정만 바꿀 때).
  - 「DB 구조」 O-8 「설정 조각 내보내기」가 이 필드를 자동 제안하게 한다(W8) — 관리자가 손으로 판단하지 않아도 된다.

### 3.2 검증 코어 — `src/sql_validation.py`

공용 코어는 계속 DB를 모른다(D-088/D-089). db_id가 아니라 **정책 값과 엔진**만 받는다.

1. `find_bare_hangul_tokens(sql, *, engine=None, allowed_identifiers=None)` 확장
   - 엔진이 `mariadb`·`mysql`이면 `` `…` ``도 인용으로 지운다(W2 — 설정 무관).
   - `allowed_identifiers`가 주어질 때만(= 허용 on DB) 토큰을 **한글을 포함한 식별자 단위**(`[\w]*[가-힣][\w]*`)로 추출해 — `취득_금액2`를 한 토큰으로 — 그 집합(대소문자 무시 비교)에 든 토큰을 제외한다.
   - `allowed_identifiers` 미전달(= 허용 off DB) 시 토큰 단위·판정·문구는 **현행 그대로**다(G-4 확정 — 폴스타 비고려 · 기존 `tests/test_nodes/test_query_validator_hangul.py` 단언 무변경).
2. `validate_sql(…, allow_hangul_identifiers: bool = False)` 키워드 추가
   - `True`면 `schema_info["tables"]`의 테이블 키(bare·한정 양쪽)와 각 `columns[*].name`으로 허용 집합을 만든다.
   - 허용 집합이 비면(구조 정보 없음) 검사를 건너뛰고 `warnings`에 사유를 남긴다 — 그대로 두면 한글 컬럼 DB의 모든 질의가 거부된다(침묵 강등 금지 원칙상 경고로 노출).
   - 오류 문구 분기: 허용 DB면 *"스키마에 없는 한글 토큰이 SQL 구조에 남아 있습니다: … - 컬럼명이 아닌 자연어 조각은 제거하세요."*, MariaDB면 인용 안내를 *"따옴표"*가 아니라 *"백틱(`)"*으로. **메시지는 ASCII 구두점만**(cp949 콘솔 — Known Mistakes 2026-07-16).
2-1. **허용 집합의 출처와 대조 규칙**(사용자 질문 2026-10-06 *"실제로 있는 컬럼명인지 여부는 어떻게 파악하는지?"*)
   - 출처는 검증기가 이미 받는 `schema_info`다 — 새 조회를 하지 않는다. 그 사슬: DB 카탈로그(`information_schema` · SELECT 권한) → MCP `get_full_schema` → 스키마 캐시(메모리 → Redis → 없으면 DB 직접 조회 · `cache_manager.get_schema_or_fetch`) → 이번 질의 관련 테이블만 추린 사본(단일 `schema_analyzer.py:778` `schema_to_dict(full_schema, relevant)` · 멀티 `_gate_schema_tables`) → `state["schema_info"]`.
   - 따라서 허용 집합 = **이번 질의에서 LLM 프롬프트에 실린 테이블들의 컬럼명**이다. 기존 「참조 컬럼 존재 검사」(`_validate_columns`)와 같은 근거라 두 검사의 판정이 어긋나지 않는다. 프롬프트에 없던 테이블의 컬럼을 쓰면 어차피 「존재하지 않는 테이블」로 걸린다.
   - 대조: 토큰과 컬럼명을 **유니코드 NFC 정규화** 후 정확 일치(라틴 문자는 대소문자 무시). 조사가 붙은 `자산명을`·띄어쓰기가 다른 `취득 금액` 등은 불일치 → 거부 → 재생성.
   - **SQL 안에서 선언한 별칭도 허용 집합에 넣는다**: `AS 자산이름`(따옴표 없는 한글 결과 별칭)·`FROM TCDMSIF80 자산`(한글 테이블 별칭). 넣지 않으면 정상 SQL `SELECT 자산명 AS 자산이름 … ORDER BY 자산이름`이 거부된다. 별칭 선언 자리(`AS` 뒤 · FROM/JOIN 테이블 뒤)에서만 수집한다.
   - 캐시가 오래돼 새 컬럼이 빠져 있으면 그 컬럼도 거부된다 — 기존 컬럼 존재 검사와 같은 한계이며, 「DB 구조」 재수집·캐시 갱신으로 해소한다.
3. MariaDB 큰따옴표 컬럼 가드(W3 · G-3 ⓐ 확정 · G-6 off 확인 → 진행)
   - 엔진이 `mariadb`·`mysql`이고 `"…"` 내용이 스키마 컬럼·테이블명과 일치하면 오류: *"MariaDB에서 큰따옴표는 문자열입니다 - 컬럼명은 백틱으로 감싸세요."*
   - **`AS "…"` 별칭 자리는 제외**한다 — 폼필은 결과 별칭을 한글 양식 필드명으로 강제하므로(`multi_db_executor.py:2170`·`prompt_blocks.py:468`) 필드명이 컬럼명과 같으면 오탐한다.
   - 설정(`allow_hangul_identifiers`)과 무관하게 MariaDB 전체에 건다(영문 컬럼도 같은 침묵 오답 — README 실측 `"sevrHostName"`). (G-3 확정)

### 3.3 배선 — 호출부 4곳 + 멀티 간이 검증

- 해석 헬퍼 1개: `registry.get(db_id)`의 `allow_hangul_identifiers`를 읽어 bool로 반환(미등록·None이면 False). 위치는 노드 공용 유틸(application 계층 — `src/nodes/` 또는 `src/utils/` 중 arch_check 통과하는 쪽).
- `query_validator`: `state["active_db_id"]`로 해석해 `validate_sql`에 전달.
- `multi_db_executor._validate_sql`: 보유한 `db_id`로 해석해 ① full 경로 `validate_sql`과 ② `_validate_sql_simple(…, allow_hangul_identifiers=…)` 양쪽에 전달. `_validate_sql_simple`은 `schema_info`를 이미 받으므로 같은 허용 집합 생성기를 공유한다.
- `tools/validation.py`: 보유한 `db_id`로 해석해 전달.
- `db_structure.py` `asset_sql_checker`: 서명에 DB 식별자가 없다. `AssetGenerationService`가 검증기를 부를 때 `source`(db_id)를 함께 넘기도록 확장하고(`sql_checker(sql, schema_info, engine, *, db_id=None)` — 미전달 = 현행), 검증기가 해석해 `validate_sql`에 전달한다. 서비스(`src/schema_cache` · infrastructure)는 레지스트리 해석을 하지 않고 db_id만 넘긴다(계층 방향 유지).
- `_validate_sql_simple`을 monkeypatch하는 기존 테스트는 `**_kw`로 받으므로(`test_multi_db_failure_audit.py:63` 등) 시그니처 추가에 깨지지 않는다 — 착수 시 grep으로 재확인.

## 4. 작업 단위

| ID | 내용 | 파일 | verify |
|---|---|---|---|
| W1 | `DBEntry.allow_hangul_identifiers` 필드·적재 · ITAM 항목에 `true` | `src/routing/registry.py` · `config/db_registry.yaml` | 레지스트리 단위 테스트: 미선언 = False · ITAM = True |
| W2 | 백틱 인용 제거(MariaDB) · 식별자 단위 토큰 · 허용 집합 인자 | `src/sql_validation.py` | ``SELECT `자산명` …`` 통과(engine=mariadb) · engine=postgresql에서는 현행 판정 유지 |
| W3 | MariaDB 큰따옴표 컬럼 가드(별칭 자리 제외) — G-6 off 확인(진행) | `src/sql_validation.py` · `src/nodes/multi_db_executor.py` | `SELECT "자산명" FROM …` 오류 · `SELECT 자산명 AS "자산명" …` 통과 |
| W4 | `validate_sql`·`_validate_sql_simple` 정책 인자 · 빈 스키마 경고 · 문구 분기 | `src/sql_validation.py` · `src/nodes/multi_db_executor.py` | 허용 DB: `t.자산명` 통과 · `해당` 거부 · 스키마 없음 = 경고 1건 |
| W5 | 호출부 배선 + 해석 헬퍼 | `query_validator.py` · `multi_db_executor.py` · `tools/validation.py` · `db_structure.py` · `src/schema_cache/asset_generation_service.py`(db_id 전달만) | 같은 ITAM 사례가 단일·멀티(간이·full)·도구·자산 검증기에서 같은 판정(D-066 대칭 테스트) |
| W6 | 회귀 | `tests/test_nodes/test_query_validator_hangul.py` 확장 · 신규 대칭 테스트 | 폴스타 계열 기존 한글 가드 테스트 전부 무변경 통과 |
| W7 | 결정·문서 | `docs/02_decision.md`(신규 D — D-104 개정) · `plans/INDEX.md` 상태 · `plans/95`에 G-4 답 반영 통지(§6 G-5) | — |
| W8 | 「DB 구조」 O-8 설정 조각에 `allow_hangul_identifiers` 자동 제안 — 수집된 스키마의 컬럼명 중 한글을 포함한 것이 있으면 `true`와 근거 주석(한글 컬럼 수)을 싣고, 없으면 필드를 싣지 않는다(결정적 · LLM 0) | `src/schema_cache/db_registration_service.py` `config_snippets` | 한글 컬럼 스키마 → 조각에 `allow_hangul_identifiers: true` · 영문 스키마 → 필드 없음 |

품질 게이트: `python scripts/arch_check.py --ci` · `python scripts/overfit_check.py --ci`(공용 코어·독스트링에 `itam`·ITAM 컬럼 리터럴 금지 — 테스트에만 둔다) · `ruff` · `mypy src/`.
매뉴얼(D-255): 채팅 사용법 변경 없음. 단 W8은 관리자 화면 O-8 산출 내용이 바뀌므로 `scripts/manual/content/admin.md`의 O-8(설정 조각) 절에 새 필드 설명 1줄을 더하고 `python -m scripts.manual.build` · `pytest tests/test_manual`을 돌린다(버튼·탭 변경 없음 → 캡처 갱신 불요). `.env.example` 변경 없음.

### 4.1 한글 컬럼 픽스처 · 실 구조 확보 경로

- ITAM 샌드박스(`testdata/itam/init`)는 영문 식별자 가정이라 이 기능을 재현하지 못한다. 단위 테스트는 테스트 내부 `schema_info` dict(테이블 `TCDMSIF80` · 한글 컬럼)로 충분하다.
- **실 DDL은 확보 불가(G-5 확정 — SELECT 권한만)**. DDL 없이도 구조는 얻는다: 「DB 구조」 탭의 **MCP 스키마 수집(O-2)**은 `information_schema` 조회라 **SELECT 권한만으로 동작**한다(MariaDB는 권한 있는 객체를 `information_schema`에 보여 준다). DDL 붙여넣기(D-292)는 그 대체 입력일 뿐이라 필요 없다.
- 샌드박스를 한글 컬럼으로 교체할지는 운영 수집 결과를 본 뒤 `plans/95` 소관으로 판단한다.

## 5. 잔여 · 통지

| 항목 | 처리 |
|---|---|
| 백틱 참조(`` `t`.`자산명` ``)의 컬럼 존재 검사 누락 | 잔여 — 느슨한 쪽이라 이번 범위 밖. 실측 오답이 나오면 승격 |
| ITAM `query_guide`·프롬프트 섹션에 "컬럼은 백틱 · 큰따옴표 금지" 규칙 | `plans/95` W-6 · `plans/133` 통지 (결정적 가드 W3과 이중 방어) |
| FabriX PII 필터가 `담당자명`·`성명` 등 한글 컬럼명을 스키마 블록에서 차단하는지 | 폐쇄망 실측 항목 — `scripts/pii_probe.py`로 사전 점검 권장 |
| D-104 본문 절 부재 | 한글 가드는 D-104가 맞다(「변경 이력」 2026-07-22 행: *"D-104(생성 SQL 한글 토큰 잔존 validator 차단, 구 D-087)"*). 다만 `## D-104.` 본문 절은 없다(D-100 다음이 D-106). 신규 D는 이 가드를 **개정**하므로 본문에 D-104 원 결정 요지를 함께 적는다 |

## 6. 게이트

### 6.1 확정 (2026-10-06 사용자 답)

| ID | 확정 내용 | 사용자 원문 | 반영 |
|---|---|---|---|
| **G-2** | 설정 위치 = `config/db_registry.yaml` DB 항목 | *"설정 위치는 레지스트리가 나을 것 같음. 그렇게 되면 에이전트에서만 설정을 변경하면 되지 않음?"* — 맞다: 본체 앱 레지스트리 1파일 + 재기동(§3.1 「설정 변경 절차」) | §3.1 · W1 · W8 |
| **G-4** | 폴스타 계열은 영문 컬럼이 확실하므로 고려하지 않는다 → **허용 off DB는 현행 비트 동일**(새 토큰 단위·문구는 허용 on DB에서만) | *"폴스타는 영어 칼럼명을 사용하는게 확실하므로 폴스타 DB는 고려하지 않음"* | §2 목표 6 · §3.2-1 |
| **G-5** | ITAM 테이블명은 `TCDMS…##` 형태(영문 대문자+숫자 — 한글 아님) · 컬럼명은 한글 · **DDL 확보 불가 · SELECT 권한만** · `sql_mode`는 미확인 | *"테이블명은 알고 있는 TCDMS**##와 같은 형태임. sql_mode가 뭔지 모름. DDL 불가능할 것 같음 SELECT 권한만 있음."* | 허용 집합의 실효 대상은 컬럼명 · 구조는 MCP 수집(O-2)으로 확보(§4.1) · `sql_mode`는 G-6 · `plans/95` G-4에 「테이블 영문 `TCDMS…` · 컬럼 한글」 통지 |
| **G-1** | 허용 on DB의 한글 허용 범위 = **ⓐ 스키마 대조** — 이번 질의 `schema_info`의 테이블·컬럼명과 같은 SQL에서 선언한 별칭만 통과, 그 밖의 한글 토큰은 거부(재생성). 설정은 bool `allow_hangul_identifiers` 유지(3단 모드 없음) | *"G-1과 G-3은 권장사항으로 문서에 반영하시오."* | §3.1 · §3.2-2·2-1 |
| **G-3** | MariaDB 큰따옴표 컬럼 = **ⓐ 검증 오류로 재생성 유도**(자동 교정 없음) · `AS "…"` 별칭 자리 제외 · 설정 무관 MariaDB 전체. **단 W3 착수는 G-6 결과에 따른다**(`ANSI_QUOTES` on이면 큰따옴표가 정상 식별자라 W3 불필요) | 위와 같음 | §3.2-3 · W3 |
| **G-6** | 운영 ITAM `sql_mode` = `STRICT_TRANS_TABLES, NO_AUTO_CREATE_USER, NO_ENGINE_SUBSTITUTION` → **`ANSI_QUOTES` 없음(off)** — 큰따옴표는 문자열 리터럴이다(샌드박스 리허설과 같은 동작). **W3 진행 확정**. `PIPES_AS_CONCAT`도 없어 `||`는 OR 연산(샌드박스 리허설의 침묵 오답 2종 모두 운영에 해당 — `||`는 `plans/95` W-6 방언 규칙 소관). 샌드박스 대비 `ERROR_FOR_DIVISION_BY_ZERO`가 없으나 이 모드는 쓰기 문장에만 작용해 읽기 전용 조회에는 영향 없음 | 사용자 전달 2026-10-06(확인 방법 = §6.2 이력: 「DB 구조」 O-1 서버 변수) | W3 · §1.3 |

### 6.2 대기

없음 — 전 게이트 확정. 구현 착수 가능(W1~W8).

## 7. 구현 결과 (v2 · 2026-10-06)

| ID | 반영 |
|---|---|
| W1 | `src/routing/registry.py` — `DBEntry.allow_hangul_identifiers` · 해석기 `hangul_identifiers_allowed(db_id)`(미등록·적재 실패 = False) · `config/db_registry.yaml` ITAM `true` |
| W2·W4 | `src/sql_validation.py` — `find_bare_hangul_tokens(sql, *, engine, allowed_identifiers)` · `check_hangul_tokens` · `hangul_token_error` · `_schema_identifiers`(목록·사전 `columns` 모두) · `_declared_aliases` · `validate_sql(…, allow_hangul_identifiers=)` |
| W3 | `src/sql_validation.py` `check_double_quoted_identifiers` — 단일 4.55 · 멀티 간이 검증 |
| W5 | `query_validator.py` · `multi_db_executor._validate_sql`/`_validate_sql_simple` · `tools/validation.py` · `db_structure.asset_sql_checker`(+ `asset_generation_service` `SqlChecker`·`_SqlCheck`에 `db_id`) |
| W6 | `tests/test_nodes/test_plan137_hangul_identifier_policy.py` 39건 통과 |
| W7 | `docs/02_decision.md` D-297 · 이 계획서 · INDEX |
| W8 | `db_registration_service.config_snippets` — 한글 컬럼 수 근거 주석·안내 · `_hangul_column_count` · 매뉴얼 A-26 주의 1줄(`scripts/manual/content/admin.md` → 빌드) |

**검증(로컬 · LLM 0 · DB 0)**: 신규 39건 통과 · 관련 기존 스위트(검증·멀티·도구·schema_cache·DB 구조 API) 1,023건 통과 — 실패 2건(`test_d294_asset_store_and_merge` CRLF 바이트 비교 · `test_plan104_local_sandbox_profile_gate` itam.yaml 추적)은 **HEAD 격리 worktree에서도 동일 실패**(이 변경과 무관 · 사전 존재) · `arch_check --ci` 위반 0 · `overfit_check --ci` 신규 유입 0. `ruff`·`mypy`는 이 PC의 Python에 미설치라 미실행.

**잔여**
- 폐쇄망 ITAM 실 질의 검증 — 배포 파일: `config/db_registry.yaml` · `src/routing/registry.py` · `src/sql_validation.py` · `src/nodes/query_validator.py` · `src/nodes/multi_db_executor.py` · `src/tools/validation.py` · `src/api/routes/db_structure.py` · `src/schema_cache/asset_generation_service.py` · `src/schema_cache/db_registration_service.py` · `src/static/manual/admin.html`·`user.html`. 확인 grep 심볼: `allow_hangul_identifiers` · `hangul_identifiers_allowed` · `check_hangul_tokens` · `check_double_quoted_identifiers` · `_hangul_column_count`.
- 확인 항목: ① 한글 컬럼 질의가 한글 가드에 걸리지 않고 실행되는가 ② LLM이 `"컬럼"`을 쓰면 재생성 뒤 백틱/무인용으로 바뀌는가 ③ 「DB 구조」 O-8 조각에 `allow_hangul_identifiers: true`가 나오는가 ④ 앱 재기동 후 반영되는가.
- §5 통지 항목(`plans/95` W-6 방언 규칙 · PII 필터 실측)은 각 소유 계획 소관.

## 8. 추가 개선 (v2.1 · 2026-10-06 — 폐쇄망 1차 결과)

W1~W8 반영 뒤 폐쇄망 ITAM에서 확인 질문 6건을 돌렸다(사용자 전달 2026-10-06). 한글 컬럼 SQL은 한글 가드를 통과해
실행됐다(#1 정상 — W1~W8 목적 달성). 남은 실패는 **두 원인**으로 갈린다 — 둘 다 이 계획(ITAM 질의를 성립시키는 것)의
연장이라 여기서 다룬다.

### 8.1 폐쇄망 1차 결과 (사용자 전달 요약)

| # | 질문(요지) | 결과 | 원인 |
|---|---|---|---|
| 1 | 자산 목록 10건 | 정상 | — |
| 2 | 자산분류별 자산 수 | `1064 … near 'NULLS LAST LIMIT 10000'` 3회 반복 → 4회차 통과. 결과는 `tcdmsif78.물품분류번호` 기준 빈 값 1그룹 275,982건 | A(구문) · B(테이블) |
| 3 | 담당 부점별 취득금액 상위 5 | `NULLS LAST LIMIT 5` 구문 오류로 재시도 → 통과. 1위가 빈 부점 `""` | A(구문) · 범위 밖(§8.5) |
| 4 | 유지보수계약 종료일이 올해 안 | `tcdmsif72`(해당 컬럼 없음)의 `시스템등록처리일시`로 조회 → 오답. 재질의에서는 `tcdmsif80` 68컬럼 56건(정답 테이블) | B(테이블) |
| 5 | 하드웨어 지원 종료 서버 호스트명 | 호스트명만 출력 → 추가 요청에 종료일 포함 정상 | 범위 밖(§8.5) |
| 6 | 폴스타 회귀 | 정상 | — |

### 8.2 원인 A — `NULLS LAST` 결정적 부가가 MariaDB 구문 오류를 만든다 (확정 · 코드)

- `ensure_ranking_nulls_last()`(`src/db_adapters/polestar/validators.py:390` · D-202 2차)는 「집계 내림차순 + 행 제한」이면
  `DESC` → `DESC NULLS LAST`로 **생성 직후 결정적으로 고친다**. 엔진을 보지 않는다 — 독스트링이 *"NULLS LAST는
  PostgreSQL·DB2 공통 문법이라 방언 분기 불필요"*라고 적었고, MariaDB 편입(D-214) 전에 쓰인 문장이다.
- 호출부 3곳이 DB를 가리지 않는다: `src/nodes/query_generator.py:966`(단일) · `src/nodes/multi_db_executor.py:1729`(다중 후보 선택) ·
  `:1767`(멀티 생성). 2단 데이터 질의 task도 `subagents.py` → `schema_analyzer` → `query_generator`를 타므로 같은 지점을 지난다.
- MariaDB에는 `NULLS LAST` 문법이 없다 → `1064`. 프롬프트에는 `NULLS LAST` 지시가 없다(grep 0건) — **LLM이 고쳐 와도
  이 교정기가 다시 붙여** 재시도 예산을 소진했다. #2 4회차 통과는 LLM이 정렬 별칭을 백틱(`` `asset_count` ``)으로 감싸
  교정기 패턴을 우연히 벗어난 결과다.
- 의미: MariaDB는 NULL을 가장 작은 값으로 정렬한다 — `DESC`에서 NULL은 이미 맨 뒤다(`src/nodes/result_merger.py:174`
  `_NULLS_SMALLEST_ENGINES`가 같은 사실을 쓴다). 교정기가 지키려던 의미(값 없는 행이 1위가 되지 않음)는 MariaDB에서
  부가 없이 성립한다.
- 검증기 쪽(`check_ranking_order_by_nulls_last`)은 폴스타 어댑터 훅이라 ITAM에는 걸리지 않는다 — 고칠 곳은 교정기뿐이다.

### 8.3 원인 B — 테이블 선택 요약이 테이블당 앞 15컬럼만 보여 준다 (확정 · 로그)

- 테이블 선택(`src/nodes/schema_analyzer.py` `_llm_select_relevant_tables`)은 테이블마다 **컬럼 순서상 앞 15개**만 싣고(테이블은 이름순, 컬럼은 수집 순서)
  나머지는 `… (외 N개)`로 줄인다(`col_names[:15]`).
- ITAM 실측(사용자 전달 2026-10-06):
  - #4 로그 `관련 테이블:` = `tcdmsif44, 72~95`(25개 · 연속 번호). **정답 후보 `tcdmsif43`은 빠졌고 `tcdmsif80`은 들어 있다.**
  - 컬럼 소재(`information_schema` · 사용자 전달): 유지보수계약 시작·종료·해지년월일 = `tcdmsif43`·`tcdmsif80` ·
    자산분류구분명 = `tcdmsif41`·`tcdmsif80`.
  - 벤더 시트 순서(`testdata/itam/schema.yaml` — 운영 `tcdmsif80` 68컬럼과 수 일치): `유지보수계약 종료년월일` 46번째 ·
    `자산분류 구분명` 57번째 · `물품분류번호` 29번째 — **정답 컬럼이 전부 15번째 밖이라 선택 프롬프트에 보이지 않는다.**
- 테이블명이 `TCDMSIF80` 같은 코드라 이름에 뜻도 없다 → LLM은 근거 없이 「IF 계열 통째로」를 골랐다(25개 연속 번호).
  후보 25개의 전 컬럼이 SQL 생성 프롬프트에 실려 생성 단계가 흔들린다(#4 1회차 `tcdmsif72` 오답 · 재질의 `tcdmsif80` 정답).
- 폴스타는 테이블명이 뜻을 가지고(`cmm_resource`) 테이블당 컬럼이 적어 이 절단이 드러나지 않았다.

### 8.4 작업 단위

| ID | 내용 | 파일 | verify |
|---|---|---|---|
| W9 | **원인 A** — `ensure_ranking_nulls_last(sql, *, db_engine=None)`: 엔진이 MariaDB·MySQL이면 원문 그대로 반환(의미 근거 §8.2). 호출부 3곳에 엔진 전달(단일 `state["active_db_engine"]` · 멀티 `db_engine` 인자). 미전달 = 현행(폴스타 비트 동일). 독스트링의 「방언 분기 불필요」 정정 | `src/db_adapters/polestar/validators.py` · `src/nodes/query_generator.py` · `src/nodes/multi_db_executor.py` | MariaDB: `ORDER BY cnt DESC LIMIT 5` 불변 · PostgreSQL·DB2: 종전대로 `NULLS LAST` 부가 · 기존 D-202 테스트 무변경 통과 |
| W10 | **원인 B** — 테이블 선택 요약에 「질의 단어와 이름이 겹치는 15번째 밖 컬럼」 절을 **테이블 목록 뒤에** 덧붙인다(아래 설계). 결정적(LLM 0) · DB 리터럴 없음 · 적용 범위는 G-7 | `src/nodes/schema_analyzer.py` | 「유지보수계약 종료일」 질의 → `tcdmsif80: 유지보수계약종료년월일` 등이 선택 프롬프트에 실린다 · 겹침 없으면 프롬프트 바이트 불변 |
| W11 | 회귀 — W9·W10 단위 테스트 · 폴스타 테이블 선택 프롬프트 렌더 불변(`scripts/prompt_render_diff.py`) | `tests/test_nodes/test_plan137_hangul_identifier_policy.py`(또는 신규) | 관련 모듈 회귀(D-303 — 모듈 단위) |
| W12 | 결정·문서 — 신규 D(D-202 2차 「방언 분기 불필요」 개정 · 테이블 선택 요약 확장) · INDEX · 매뉴얼 대상 아님(화면·사용법 무변경) | `docs/02_decision.md` · `plans/INDEX.md` | — |

**W10 설계**
- 질의 단어: `user_query`·`query_targets`에서 한글 2자 이상·라틴 3자 이상 낱말(조사 등 꼬리 1자 제거 정도의 단순 규칙 ·
  형태소 분석기 도입 없음).
- 겹침 판정: 컬럼명(NFC·대소문자 무시)이 질의 낱말을 **포함**하면 겹침 — 「유지보수계약 종료일」 → 낱말 `유지보수계약` →
  `유지보수계약종료년월일`·`유지보수계약시작년월일` 겹침.
- 대상: 앞 15컬럼에 이미 보인 컬럼은 제외 · 테이블당 최대 10개 · 전체 상한(토큰 예산 — 108테이블 DB) 설정.
- **위치는 테이블 목록 뒤 별도 절**(`질의 단어와 겹치는 컬럼(앞 15개 밖):`) — 테이블 목록 접두를 질의마다 바꾸지 않는다.
  선택 프롬프트 접두는 KV 캐시 적중을 위해 순서까지 고정해 둔 부분이다(`plans/121` TP-11.10 · D-222 부기 ④).
- 겹침이 없으면 절 자체를 싣지 않는다 → 프롬프트 바이트 불변.

### 8.5 범위 밖 — 연결만

| 항목 | 이유 · 소유 |
|---|---|
| 막연한 질문(「자산 목록」)의 테이블 선택 | 질의 단어와 컬럼명이 겹치지 않으면 W10이 돕지 못한다 — 테이블 설명이 필요하다. `main` `plans/133`(스키마 자산 자동 생성) · `plans/135`(ITAM 질의 벤치마크)가 근거·효과 측정 소유 |
| #4 재질의 `SELECT *` 68컬럼 · #5 호스트명만 출력 | 출력 컬럼 선택은 ITAM 프롬프트 규칙 영역 — `plans/95` W-6 |
| #3 빈 부점 `""` 1위 · 부점 코드로 집계(부점명 아님) | 데이터 실태(빈 문자열 다수)와 컬럼 의미 선택 — ITAM 지식(`plans/95` W-6 · `plans/135`) |
| #2 `tcdmsif78.물품분류번호` 빈 값 275,982건 | W10으로 `자산분류구분명`이 보이면 해소될 것으로 본다 — 재검증에서 확인. 남으면 `plans/135` |

### 8.6 게이트 — **G-7 ⓑ 확정**(사용자 2026-10-06 *"권장사항에 따라 (b)안 적용"*) · D-202 2차 개정 승인(*"당연히 지원하지 않는 문구를 그대로 사용하도록 하면 안되기 때문임"*)

| ID | 질문 | 권고 | 영향 |
|---|---|---|---|
| **G-7** | W10(선택 요약 확장)의 적용 범위: ⓐ 전 DB(겹침이 있을 때만 절 추가 · 접두 불변) ⓑ 한글 식별자 허용 DB(`allow_hangul_identifiers: true`)만 | **ⓑ** — 폴스타는 고려하지 않는다는 G-4와 같은 원칙(허용 off DB 비트 동일). 문제(코드형 테이블명 + 한글 컬럼 + 넓은 테이블)도 ITAM 형태에 고유하다. 다른 DB에 필요해지면 ⓐ로 넓힌다 | W10 분기 조건 |

W9는 게이트 없이 착수할 수 있다(구문 오류 교정 · 폴스타 비트 동일).

### 8.7 폐쇄망 재검증 (W9·W10 반영 후)

| # | 질문 | 기대 |
|---|---|---|
| 2 | `ITAM에서 자산분류별 자산 수를 많은 순으로 보여줘` | `NULLS LAST` 구문 오류 0회 · 테이블 `tcdmsif41` 또는 `tcdmsif80`의 `자산분류구분명` 기준 집계 |
| 3 | `ITAM에서 담당 부점별 취득금액 합계 상위 5개` | 구문 오류 0회(값 해석은 §8.5) |
| 4 | `ITAM에서 유지보수계약 종료일이 올해 안에 끝나는 자산 보여줘` | 로그 `관련 테이블:`에 `tcdmsif43` 또는 `tcdmsif80` 포함 · SQL이 `유지보수계약종료년월일` 조건 사용 |

옮겨 적을 것: 위 3건의 성공·실패와, #4의 `관련 테이블:` 목록 1줄.

### 8.8 구현 결과 (v2.2 · 2026-10-06)

| ID | 반영 |
|---|---|
| W9 | `src/db_adapters/polestar/validators.py` — `ensure_ranking_nulls_last(sql, *, db_engine=None)` · `_NO_NULLS_ORDERING_ENGINES`(mariadb·mysql) · 독스트링 「방언 분기 불필요」 정정. 호출부: `src/nodes/query_generator.py`(`_dialect_engine(state)` — state 엔진, 없으면 레지스트리) · `src/nodes/multi_db_executor.py` 2곳(`db_engine` 인자) |
| W10 | `src/nodes/schema_analyzer.py` — `_query_words` · `_query_matched_columns_text` · `_llm_select_relevant_tables(…, match_columns=)` · 호출부 `match_columns=hangul_identifiers_allowed(db_id)`(G-7 ⓑ). 상수 `_SELECT_SUMMARY_COLUMNS=15` · `_MATCH_COLUMNS_PER_TABLE=10` · `_MATCH_COLUMNS_TOTAL=80`. 낱말은 한글·라틴 3자 이상(구현 중 조정 — 2자 「자산」·「에서」가 108테이블에서 잡음) |
| W11 | `tests/test_nodes/test_plan137_hangul_identifier_policy.py` +15건(총 54건 통과) — 엔진 분기 · 레지스트리 폴백 · 호출부 3곳 엔진 전달 · 겹침 절 내용·상한·꼬리 1자 · 접두 불변 · 허용 off 프롬프트 바이트 동일 · 호출부 레지스트리 게이트 |
| W12 | `docs/decisions/D-305.md` 신설 · 색인(`docs/02_decision.md`) 행 · `docs/decisions/CHANGELOG.md` · D-202 부기(2차 개정) · D-297 상태 정정(커밋 `9e85556`) · 이 계획서 · INDEX(240자) · `plans/INDEX-CHANGELOG.md` — D-304 결정 문서 구조(색인 + 결정별 파일)에 맞춤 |

**검증(로컬 · LLM 0 · DB 0)**: 변경 모듈을 import하는 테스트 122파일(D-303 모듈 단위) — 2,629 통과 · 실패 18 · 오류 10. 실패·오류 28건은 **변경 전 HEAD(`9e85556`) 격리 worktree에서도 동일**(파일 업로드 스트림 API `[file]` 변형 · e2e 브라우저 미설치 · `plan107_intent_frame` 설정 로그) — 이 변경과 무관. `prompt_render_diff --ci` 차이 0 · `arch_check --ci` 위반 0 · `overfit_check --ci` 신규 유입 0. `ruff`·`mypy`는 이 PC의 Python에 미설치라 미실행.

**폐쇄망 반영(파일별 수동 복사)** — W1~W8 파일이 이미 반영돼 있어야 한다(`schema_analyzer.py`가 W1의 `src/routing/registry.py` `hangul_identifiers_allowed`를 import한다 — 없으면 기동 실패).

| 파일 | 확인 grep 심볼 |
|---|---|
| `src/db_adapters/polestar/validators.py` | `_NO_NULLS_ORDERING_ENGINES` |
| `src/nodes/query_generator.py` | `_dialect_engine` |
| `src/nodes/multi_db_executor.py` | `ensure_ranking_nulls_last(sql, db_engine=db_engine)` |
| `src/nodes/schema_analyzer.py` | `_query_matched_columns_text` · `match_columns=hangul_identifiers_allowed` |

반영 뒤 앱 재기동 → §8.7 재검증.

## 9. 추가 개선 2 — 집계 결과 표의 NULL 묶음 기준 표시 (v2.4 · 2026-10-06)

### 9.1 폐쇄망 재검증 결과 (W9·W10 반영 후 · 사용자 전달)

| # | 결과 | 판정 |
|---|---|---|
| 2 자산분류별 자산 수 | `NULLS LAST` 오류 0 · `t.자산분류구분`·`t.자산분류구분명` 두 컬럼으로 집계 · 표 가운데에 빈 분류 줄 | W9 효과 확인 · 빈 줄 = §9.2 |
| 3 부점별 취득금액 | 같은 형태(부서 A · 부서 B · 빈칸) | 같음 |
| 4 유지보수계약 종료일 | `관련 테이블:`에 `tcdmsif80` 포함 | W10 효과 확인 |

### 9.2 정합성 확인 (DB 직접 조회 · 사용자 전달)

- `tcdmsif80` 전체 3,843 = 기계장치 2,386 + NULL 1,419 + 부외자산 38 — 누락·중복 없음, 앱 SQL에 의도하지 않은 조건 없음.
- 빈 줄은 빈 문자열이 아니라 **NULL**(`자산분류구분명` NULL 1,419 · 빈 문자열 0 · `담당부점명` NULL 1,616 · 빈 문자열 0).
- `취득금액` = decimal(합계 정확) · `취득금액` NULL 1,419(분류 NULL과 같은 수 — 같은 행일 가능성).
- 정렬은 건수·금액 순 그대로 — NULL 묶음도 제 크기 순서에 온다. `NULLS LAST`는 정렬 기준 값(집계)이 NULL일 때의 규칙이라 무관하다. **데이터·정렬은 정상이고 표시만 오해를 부른다.**

### 9.3 W13 — 사용자 확정 사항과 구현

| 항목 | 확정 |
|---|---|
| 적용 위치 | 화면 결과 표 렌더 직전(표시용 사본) — 저장 결과·CSV·양식 채우기·후속 질의 값은 NULL 그대로(출력 경로 전수 조사 근거 — D-306) |
| 적용 DB | **(가) ITAM만** — 레지스트리 새 항목 `label_null_group_keys`(「한글 식별자 허용」과 뜻이 달라 분리) · 멀티 DB 병합 표 제외 |
| 문구 | **「(값 없음)」** |
| 묶음 기준 판정 | 실행 SQL 최상위 SELECT 목록의 집계 함수 아닌 항목(별칭·단순 컬럼). `GROUP BY` 없음·`WITH`·`*`·집계 없음이면 미적용 |
| 행 조건 | 묶음 기준 NULL이고 같은 행의 집계 값이 있을 때만 |

구현: `src/routing/registry.py`(`DBEntry.label_null_group_keys` · `null_group_label_enabled` · 공용 `_entry_flag`) · `config/db_registry.yaml`(ITAM `label_null_group_keys: true`) ·
`src/nodes/output_generator.py`(`NULL_GROUP_LABEL` · `group_key_columns` · `_null_group_key_columns` · `_label_null_group_keys` · `_render_result_table(…, null_label_keys=)`) ·
매뉴얼 U-20 주의 1줄(`scripts/manual/content/user.md` → 빌드) · 테스트 +18건(계획서 137 테스트 총 72건).
2단 경로도 같은 함수로 최종 표를 만든다(`result_aggregator._finalize_merged_rows` → `output_generator`).

남는 차이(D-306 주의): LLM 요약 입력은 NULL 그대로 · 시간 상한 부분 결과 표·진행 패널 미리보기는 빈칸 그대로 · 별칭 없는 계산식 묶음 기준은 미표시.

### 9.4 폐쇄망 반영 · 재검증

| 파일 | 확인 grep 심볼 |
|---|---|
| `config/db_registry.yaml` | `label_null_group_keys` (내부망에서 이 파일을 고친 적이 있으면 덮어쓰지 말고 ITAM 항목에 `label_null_group_keys: true` 한 줄만 추가) |
| `src/routing/registry.py` | `null_group_label_enabled` |
| `src/nodes/output_generator.py` | `NULL_GROUP_LABEL` |
| `src/static/manual/user.html`·`admin.html` | (매뉴얼 — 선택) |

재검증: #2 `ITAM에서 자산분류별 자산 수를 많은 순으로 보여줘` → 표의 NULL 줄이 `| (값 없음) | (값 없음) | 1419 |`로 보이는지 · CSV는 빈칸 그대로인지. #3 같은 확인.
