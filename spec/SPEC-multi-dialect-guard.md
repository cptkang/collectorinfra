# Spec: multi-dialect-guard

> Module id: `multi-dialect-guard` (`CAPABILITY-MAP-execution-groups.md`)
> 근거: `plans/82` §1.1 · §7 Wave 1 | 예약 결정: **D-176** | 계층: application (`src/nodes/`)

## ASSUMPTIONS I'M MAKING

1. **`TEXT2SQL_MULTI_FULL_VALIDATION` 전면 ON은 하지 않는다.** 위양성 실측이 라이브 질의를 요구하는데
   그건 D-127(과금 API 승인 게이트) 대상이다. 대신 **위양성이 구조적으로 불가능한 부분집합**만
   기본 ON으로 올린다 — 행 제한 절 방언은 SQL 텍스트만 보고 판정하므로 LLM 호출이 0이다.
2. **행 제한 절 자동 보정은 "추가"만 한다.** 이미 있는 `LIMIT`/`FETCH FIRST`를 다른 방언으로
   **바꾸지 않는다** — 재작성은 서브쿼리·CTE 안의 절까지 건드릴 위험이 있고, 그건 별건이다.
3. DB2 대상에 `LIMIT`이 온 경우는 **검증 실패로 처리해 재생성**시킨다(보정이 아니라 거부).
   `LIMIT n`을 `FETCH FIRST n ROWS ONLY`로 문자열 치환하는 것은 위치·중첩에 따라 틀릴 수 있다.
4. 실행 오류 후 재생성은 **1회**로 제한하고, 재생성 대상은 **SQL 문법 오류 계열만**이다
   (연결 실패·타임아웃은 재생성해도 같다).
5. 기존 `_validate_sql_simple`의 검사 항목은 **하나도 제거하지 않는다**.

→ 위 5개가 틀렸으면 지금 정정해 주십시오. 아니면 이대로 진행합니다.

## Objective

**무엇을**: 멀티 DB 경로(`multi_db_executor`)의 SQL 검증에 **엔진 방언 그물**을 복원하고,
실행 오류 시 **에러 컨텍스트를 실은 재생성 1회**를 추가한다.

**왜**: 단일 DB 경로에는 있고 멀티 경로에는 없다 — `plans/82` §1.1 실측.

| | 방언 검사 | 실행오류 후 재생성 |
|---|---|---|
| 단일 DB | **있음** (`sql_validation.py:194-197` 엔진별 행 제한 절 자동 보정) | **있음** (`query_executor`→`query_generator` 백엣지) |
| 멀티 DB | **없음** (`_validate_sql_simple`) | **없음** (`graph.py:543` 무조건 전진) |

사용자 보고 증상(*"동시에 조회시 쿼리가 db종류에 맞게 작성이 안 된다"*)의 실제 지점이 여기다.
DB2 대상에 `LIMIT`이 나오면 **검증을 통과하고 실행 시점에 SQL0104N으로 죽으며 재생성 기회가 없다.**

**사용자**: 은행존(DB2)과 공동존(PostgreSQL)을 함께 조회하는 운영자.
**성공**: 존 조합 조회에서 방언 불일치로 인한 무재시도 실패가 사라진다.

## Tech Stack

기보유만 사용 — **신규 라이브러리 0건**. Python 3.12/3.13 · pytest(+`pytest-asyncio`).
재사용 대상: `src/utils/sql_dialect.py`(`row_limit_clause`·`is_db2`) · `src/sql_validation.py`(`_has_row_limit`).

## Commands

```bash
# 이 모듈 대상 스위트
python -m pytest -q tests/test_nodes/test_multi_db_dialect.py tests/test_nodes/test_multi_db_merge.py \
                    tests/test_nodes/test_multi_db_recovery.py

# 계층 규칙
python scripts/arch_check.py --ci

# 격리 검증 (공유 트리 오귀속 차단 — CAPABILITY-MAP 「동시 작업」)
git worktree add /private/tmp/.../baseline-82 HEAD
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `src/nodes/multi_db_executor.py` | 수정 — `_validate_sql_simple`에 방언 검사 · `_run_single_target`에 실행오류 재생성 |
| `src/utils/sql_dialect.py` | **읽기 재사용** (`is_db2`·`row_limit_clause`) — 수정하지 않는다 |
| `src/sql_validation.py` | **읽기 재사용** (`_has_row_limit` 로직 참조) — 수정하지 않는다 |
| `tests/test_nodes/test_multi_db_dialect.py` | **신규** |

## Code Style

기존 `_validate_sql_simple`의 스타일을 그대로 따른다 — 검사마다 한글 주석으로 **근거 결정 번호**를
달고, 실패 시 **사용자가 원인을 특정할 수 있는 메시지**를 반환한다(침묵 금지).

```python
    # 엔진 방언 — 행 제한 절이 대상 엔진 문법과 어긋나면 실행 시점에 죽는다(D-176 · plans/82 §1.1).
    # 단일 경로는 sql_validation이 엔진별로 자동 보정하는데 멀티 경로에는 그 그물이 없어,
    # DB2 대상의 LIMIT이 검증을 통과하고 SQL0104N으로 실패해 왔다(재생성 기회 없음).
    # 보정이 아니라 거부다 — LIMIT→FETCH FIRST 문자열 치환은 중첩·서브쿼리에서 틀릴 수 있다.
    dialect_error = check_row_limit_dialect(sql, db_engine)
    if dialect_error:
        return dialect_error
```

## Testing Strategy

- **프레임워크**: pytest. 위치 `tests/test_nodes/test_multi_db_dialect.py`.
- **전부 mock** — LLM·네트워크·DB 미사용(D-127 준수).
- 테스트 레벨: 순수 함수 단위(방언 판정) + `_run_single_target` 통합(재생성 경로).

| 케이스 | 기대 |
|---|---|
| `db_engine="db2"` + `LIMIT 100` | 거부 · 메시지에 `FETCH FIRST` 안내 포함 |
| `db_engine="db2"` + `FETCH FIRST 100 ROWS ONLY` | 통과 |
| `db_engine="postgresql"` + `LIMIT 100` | 통과 |
| `db_engine="postgresql"` + `FETCH FIRST 100 ROWS ONLY` | **통과** (PG도 표준 문법 지원 — 오탐 금지) |
| 행 제한 절 없음 (양 엔진) | 통과 — **이 모듈은 부재를 강제하지 않는다**(기존 동작 보존) |
| 문자열 리터럴 안의 `limit` | 통과 — 오탐 금지 |
| 실행 오류(문법) 발생 | 에러 컨텍스트를 실어 재생성 1회 → 성공 시 결과 반환 |
| 실행 오류(연결/타임아웃) | 재생성하지 않음 — 기존 `_record_failure` 경로 |
| 재생성도 실패 | `db_errors`에 **원 에러 + 재생성 시도 사실** 기록(침묵 금지) |

## Boundaries

**Always**
- 기존 `_validate_sql_simple` 검사 항목 전부 보존
- 실패 메시지에 원인·조치를 명시(`plans/82` 침묵 강등 금지)
- `arch_check --ci` 통과
- 격리 worktree에서 회귀 대조

**Ask first**
- `TEXT2SQL_MULTI_FULL_VALIDATION` 기본값 변경
- `sql_validation.py`·`sql_dialect.py` 수정 (다른 경로가 공유하는 자산)
- 재생성 횟수를 1회 초과로 늘리기 (토큰 비용 — D-159)

**Never**
- 실 LLM/과금 API 호출 (D-127)
- 기존 테스트 삭제·완화
- `LIMIT`↔`FETCH FIRST` 문자열 치환으로 "보정"
- `CompositeConfig` 수정 (동시 작업 충돌 회피 — CAPABILITY-MAP)

## Success Criteria

1. `check_row_limit_dialect("SELECT 1 LIMIT 10", "db2")` 가 **비-None**(거부 사유)을 반환한다.
2. `check_row_limit_dialect("SELECT 1 LIMIT 10", "postgresql")` 가 **None**(통과)을 반환한다.
3. `_validate_sql_simple`이 db_engine을 받아 위 판정을 수행하고, **기존 검사 결과는 불변**이다.
4. `_run_single_target`에서 SQL 문법 오류로 실행 실패 시 **에러 컨텍스트를 실은 재생성 1회**가 일어난다.
5. 재생성 후에도 실패하면 `db_errors[db_id]`에 **원 에러와 재생성 시도 사실**이 함께 남는다.
6. 신규 테스트 전부 통과 · **기존 멀티 DB 테스트 회귀 0** · `arch_check --ci` exit 0.
7. `db_engine`이 없거나 미지 엔진이면 **현행 동작 그대로**(통과) — 새 실패를 만들지 않는다.

## Open Questions

없음 — ASSUMPTIONS 5건이 확정되면 진행 가능.
