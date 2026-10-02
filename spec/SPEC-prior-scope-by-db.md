# Spec: prior-scope-by-db

> Module id: `prior-scope-by-db` | 근거: `plans/88` §4.9 · §2.4(E-4) · W7 | 예약 결정: **D-203**
> 계층: orchestration(`subagents._extract_identity_rows`) + utils(`query_gen_common`) + application(`multi_db_executor` · `prompt_blocks`)

## ASSUMPTIONS I'M MAKING

1. `_source_db`는 `_merge_results`가 붙이는 내부 태그(`multi_db_executor.py:2397`)이며 식별 컬럼이 아니다
   (`is_server_identity_col("_source_db") == False` 실측). 패스스루해도 `collect_prior_identity_values`·HAVING·프롬프트
   블록에 새지 않는다 — 테스트로 고정.
2. 분할 키가 없는 행(단일 DB 선행 · 구 형식)은 `""` 버킷이며, 그 값은 **모든 DB에** 적용한다(현행과 동일).
3. 분할이 존재하고 대상 DB에 버킷이 없으면 그 DB는 **미조회**(`db_errors`가 아니라 별도 `skipped_dbs`)하고 노트를 남긴다.
4. `build_prior_rows_block`에 `db_id` 인자를 추가하되 기본 `None`=현행(전체 값). 프롬프트 골든은 인자 미지정 경로가
   바이트 동일이라 영향 없다.
5. 플래그 `COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED` 기본 off — off면 `_extract_identity_rows`도 패스스루하지 않는다(행 shape 불변).
6. 실행 그룹(`execution_groups`)이 채워지는 배선은 82 2차 몫 — 여기서는 `_run_groups`가 이미 `targets`를 그룹별로 나눠
   `_run_single_target`을 부르므로, **DB 단위 분할이 곧 그룹 단위 분할**이다. 별도 그룹 코드는 만들지 않는다.

## Objective

**선행 결과의 존 분포가 후속 조회의 대상 DB 집합과 DB별 IN 목록을 결정한다.**

현행: `prior_scope`가 run 단위 1회 계산돼 모든 DB에 같은 IN 목록이 간다. b0 값이 gp/yd SQL에 섞이고, 선별 서버가 없는
존까지 조회한다(`plans/88` §2.4).

## Tech Stack / Commands

기보유만.
```bash
python -m pytest -q tests/test_nodes/test_prior_scope_by_db.py tests/test_orchestration/test_prior_rows_scope.py \
                    tests/test_nodes/test_multi_db_group_loop.py tests/test_composite/test_prior_scope_db_id.py
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `src/orchestration/subagents.py` | `_extract_identity_rows` — 플래그 on이면 `_source_db` 보존 |
| `src/utils/query_gen_common.py` | `collect_prior_identity_values_by_db(prior_rows) -> dict[str, tuple[str, list[str]]]` · `build_prior_rows_block(prior_rows, *, db_id=None)` |
| `src/nodes/prompt_blocks.py` | `prior_server_scope_by_db(prior_rows)` |
| `src/nodes/multi_db_executor.py` | `_MultiRun.prior_scope_by_db` · `_prepare_multi_run` 계산 · `_run_single_target` DB별 선택·미조회 · 반환에 `dependency_notes` |
| `src/config.py` | `CompositeConfig.prior_scope_by_db_enabled: bool = False` |
| `tests/test_nodes/test_prior_scope_by_db.py` | **신규** |

## Code Style

```python
def collect_prior_identity_values_by_db(prior_rows: dict) -> dict[str, tuple[str, list[str]]]:
    """prior_rows를 행의 `_source_db`로 나눠 DB별 (식별컬럼, 값목록)을 돌려준다 (D-203 · plans/88 §4.9).

    태그 없는 행은 "" 버킷 — 호출부가 전 DB에 적용한다(현행 동작). 컬럼 종류 우선 규칙은
    `collect_prior_identity_values`와 동일(hostname류 우선 · D-061 혼합 금지)하며 버킷별로 적용한다.
    """
```

## Testing Strategy

| 케이스 | 기대 |
|---|---|
| b0 3행 + gp 4행(`_source_db` 태그) | `{"polestar_b0": ("hostname",[3]), "polestar_cm_gp": ("hostname",[4])}` |
| 태그 없는 행 | `{"": (...)}` — `collect_prior_identity_values`와 값 동일 |
| `_extract_identity_rows` 플래그 on | `_source_db` 키 보존 · 다른 비식별 컬럼은 제거 |
| `_extract_identity_rows` 플래그 off | 현행과 동일(태그 제거) |
| `build_prior_rows_block(db_id="polestar_cm_gp")` | gp 값만 렌더 · `db_id=None`이면 현행 바이트 동일 |
| `_run_single_target` · 분할 있음 · yd 버킷 없음 | yd **미조회** · `skipped_dbs=["polestar_cm_yd"]` · 노트 `scope_db` |
| `_generate_sql` 호출 인자 | b0 호출 `prior_scope=("hostname",[b0 값])` · gp 호출은 gp 값(**mock 인자 단언**) |
| `_source_db` 미노출 | HAVING 값·프롬프트 블록 문자열에 `polestar_` 미포함 |
| 플래그 off | `_MultiRun.prior_scope_by_db is None` · `_generate_sql` 인자 현행 동일 |

## Boundaries

**Always** — 태그 없는 행은 현행 동작 · `_source_db`는 사용자 응답·CSV에 새로 노출하지 않는다(D-176 SPEC 계승)
**Ask first** — 미조회 DB를 `db_errors`로 취급(다운로드·row_count 영향) · 실행 그룹 kind별 분기 추가
**Never** — `_merge_results`의 태그 부착 위치 변경 · `TargetRef`/`prior_targets` 경로 수정

## Success Criteria

1. DB별 분할 함수가 표대로 동작한다.
2. 플래그 on에서 DB별 `_generate_sql` 인자가 분리되고 버킷 없는 DB는 미조회 + 노트.
3. 플래그 off 비트 동일 · `test_prior_rows_scope`·`test_multi_db_group_loop` 회귀 0.
4. `_source_db`가 HAVING·프롬프트에 새지 않는다(골든).

## Open Questions

없음.
