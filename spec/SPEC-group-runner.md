# Spec: group-runner

> Module id: `group-runner` | 의존: `group-registry` | 근거: `plans/82` §4.9 · §5.5 · §7 Wave 3
> 예약 결정: **D-176** | 계층: application(`src/nodes/`) + domain(state)

## ASSUMPTIONS I'M MAKING

1. **그룹 1개면 현행 경로와 바이트 동일**해야 한다. `execution_groups`가 없으면 단일 그룹으로
   폴백하고, 그 경로는 종전 `for target in targets` 루프와 **동일한 호출 순서·동일한 반환 키**를 낸다.
2. **소비처를 만들지 않는다.** `execution_groups`를 채우는 라우팅 배선은 2차 모듈(`host-discovery`·
   `solution-pipeline`) 소관이다. 이 모듈은 **루프·계측·부분 결과 방출까지만** 하고, 실제로는
   항상 단일 그룹으로 돈다 → **런타임 동작 변화 0**.
3. **부분 결과 방출은 콜백 주입**으로 한다. `multi_db_executor`가 SSE를 직접 알면 계층이 깨진다
   (application → interface 역방향). state에 `group_packets`를 쌓고, 방출은 라우트가 한다.
4. **계측은 저장소를 새로 만들지 않는다.** 기존 `investigation_metrics` 형식을 재사용한다.
5. D-153 소급 복구를 **그룹 내부로 이동**한다 — 복구 키가 `(engine, schema)`라 그룹을 넘나들 수 없다.
   그룹이 1개면 현행과 동일한 시점에 동일하게 동작한다.

## Objective

**실행 그룹을 순서대로 실행하고, 그룹마다 결과·소요·부분 결과를 남긴다.**

- **요구 1**(사용자): *"은행존을 먼저 조회하고 조회가 완료되면 공동존을 조회"* — 현재 순회는 있으나
  **순서가 `relevance_score`(LLM 자기보고)에 달려 있고 그룹 경계가 없다**(`plans/82` §1.2).
- **문헌 정정 ②**: 그룹 완료 즉시 **부분 결과를 내보낸다**(Online Aggregation, SIGMOD 1997).
  먼저 조회한 것을 먼저 보여주면 요구 1과 자연 결합한다.
- **P13**: 계측이 최적화보다 먼저다. 그룹별 소요를 남기지 않으면 이후 어떤 임계도 근거가 없다.

## Tech Stack

기보유만 — 신규 라이브러리 0건. LangGraph state(TypedDict) · pytest.

## Commands

```bash
python -m pytest -q tests/test_nodes/test_multi_db_group_loop.py \
                    tests/test_nodes/test_multi_db_merge.py tests/test_nodes/test_multi_db_recovery.py \
                    tests/test_observability
python scripts/arch_check.py --ci
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `src/state.py` | 수정 — `execution_groups`·`group_results`·`group_packets` 추가(요청 스코프) |
| `src/nodes/multi_db_executor.py` | 수정 — 그룹 루프 · `_collect_group` · 소급 복구 이동 |
| `src/nodes/result_merger.py` | 수정 — 버리던 `db_result_summary`를 **그룹 요약으로 승격 반환** |
| `src/observability/group_metrics.py` | **신규** — 그룹별 p50/p90 롤링 집계 |
| `tests/test_nodes/test_multi_db_group_loop.py` | **신규** |

## Code Style

기존 `_MultiRun` 데이터클래스·`_prepare_multi_run` 패턴을 그대로 따른다. 그룹 루프는 **기존
`_run_single_target` 호출을 감싸기만** 하고 그 함수 자체는 건드리지 않는다.

```python
    # 그룹 순차 실행(D-176 · plans/82 §4.9) — 순서 정본은 레지스트리 query_order이지
    # relevance_score가 아니다(LLM 자기보고에 순서를 맡기지 않는다 — D-035).
    # 그룹이 1개면 종전 단일 루프와 호출 순서·반환 키가 동일하다(회귀 0).
    for group in groups:
        run = await _prepare_multi_run(state, llm, app_config)
        for target in _targets_of(group, targets):
            await _run_single_target(target, run)
        await _recover_same_schema(run, state)      # D-153 — 그룹 내부로 이동
        group_results[group["group_key"]] = _collect_group(run, group, started)
```

## Testing Strategy

pytest · 전부 mock(LLM·MCP·DB 미사용) · 위치 `tests/test_nodes/test_multi_db_group_loop.py`.

| 케이스 | 기대 |
|---|---|
| `execution_groups` 없음(폴백) | **현행과 동일** — 반환 dict 키 집합 동일 · `_run_single_target` 호출 순서 동일 |
| 그룹 2개 | `query_order` 순으로 실행 — 호출 순서를 mock으로 단언 |
| 그룹 2개 · 앞 그룹 전부 실패 | 뒤 그룹 **정상 실행**(실패 격리) · `db_errors`에 앞 그룹 사유 보존 |
| `sql_by_schema` | 그룹 스코프 격리 — gp/yd는 공유, b0는 별도 |
| D-153 소급 복구 | 그룹 내부에서 발동 · 그룹 1개면 현행과 동일 결과 |
| `group_results` | 그룹별 `row_count`·`elapsed_ms`·`errors`·`sqls` 분리 기록 |
| `group_packets` | **peer 그룹만** 적재 · `discovery`/`dependent`는 미적재 |
| `result_merger` | `db_result_summary`가 반환 dict에 실린다(종전엔 버려짐) |
| 계측 집계 | `(solution, zone_group, kind, backend)`별 p50/p90 · 표본 수 노출 |

## Boundaries

**Always**
- 그룹 1개 경로 **바이트 동일**(골든) · 요청 스코프 필드는 라우트에서 명시 초기화 대상으로 문서화
- `_run_single_target`·`_generate_validated_sql` **미수정**(다른 모듈 소관)
- `arch_check --ci` 통과 — application이 interface를 참조하지 않는다

**Ask first**
- `multi_db_executor` 반환 키 **제거·개명**(하류 소비처 다수)
- 그룹 병렬 실행 (요구는 순차다)
- `_prepare_multi_run`을 그룹마다 호출하지 않고 재사용하기 (PII H1 검증 가능성이 사라진다)

**Never**
- `multi_db_executor`에서 SSE·라우트 직접 참조(계층 역방향)
- 실 LLM 호출(D-127) · 기존 테스트 삭제·완화 · `CompositeConfig` 수정(동시 작업 충돌)

## Success Criteria

1. `execution_groups` 미설정 시 `multi_db_executor`의 **반환 키 집합과 `_run_single_target` 호출
   순서가 현행과 동일**하다(골든 테스트).
2. 그룹 2개 입력 시 **`query_order` 순으로 순차 실행**되며, 앞 그룹 완료 후 뒤 그룹이 시작한다.
3. 한 그룹의 전면 실패가 다른 그룹 실행을 막지 않고, 사유가 `db_errors`에 남는다.
4. `group_results[*]`에 `row_count`·`elapsed_ms`·`errors`·`sqls`가 그룹별로 분리 기록된다.
5. `group_packets`에 **peer 그룹의 부분 결과만** 쌓인다.
6. `result_merger`가 `db_result_summary`를 반환한다(종전 폐기분 복구).
7. `src/observability/group_metrics.py`가 그룹 유형별 p50/p90과 **표본 수**를 낸다
   (표본 부족 시 호출부가 시간 문구를 생략할 수 있도록 — `plans/82` §5.5 S-C).
8. 대상 스위트 **1042 passed 유지**(신규분 제외) · `arch_check --ci` exit 0.

## Open Questions

없음 — ASSUMPTION 3(부분 결과는 state 적재, 방출은 라우트)이 확정되면 진행.
