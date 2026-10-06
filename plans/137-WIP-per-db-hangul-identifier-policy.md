# 137. DB별 한글 식별자 허용 정책 — SQL 한글 토큰 가드(D-104)를 DB 설정으로 분기

> **작성일**: 2026-10-06
> **상태**: **v2 · 구현 완료(W1~W8 · 작업 트리 · 커밋 없음 · D-297) · 잔여 = 폐쇄망 ITAM 실 질의 검증(§7)** — 파일명 `-WIP`
> **개정 이력**: v1(2026-10-06 작성) → v1.1(2026-10-06 사용자 답 반영 — G-2 레지스트리 · G-4 폴스타 비고려 = 허용 off DB 현행 비트 동일 · G-5 테이블명 `TCDMS…` 영문 · DDL 불가 · SELECT 권한만 · 호출부 5번째(자산 생성 SQL 검증기) · 설정 조각 자동 제안 W8 추가) → v1.2(2026-10-06 — G-1 ⓐ 스키마 대조 · G-3 ⓐ 검증 오류 재생성 권고안 확정 · 허용 집합 출처·별칭 규칙 §3.2 2-1 · G-6 확인 방법) → v1.3(2026-10-06 — G-6 운영 `sql_mode` 실측값 수령 · ANSI_QUOTES off · W3 진행 확정) → v2(2026-10-06 — W1~W8 구현 · D-297 등재 · 파일명 `-TODO` → `-WIP`) → 번호 이동(2026-10-06 — 원 작성 번호 135가 `main`의 `plans/135`(ITAM 질의 벤치마크)와 겹쳐 137로 옮겨 `main` 최신 커밋 위에 다시 적용 · 내용 변경 없음)
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
