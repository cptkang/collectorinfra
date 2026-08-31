# Spec: group-registry

> Module id: `group-registry` | 근거: `plans/82` §4.2 · §7 Wave 2 | 예약 결정: **D-176**
> 계층: config(YAML) + routing(파생 API) + utils(분할기)

## ASSUMPTIONS I'M MAKING

1. **추가 전용**이다. 기존 `zones`·`families`·`locations`·`databases` 키의 의미·순서를 바꾸지 않는다.
2. **`zones:` 선언 순서를 재정렬하지 않는다.** `all_zones()`는 알림 RBAC 선택지 순서를 만들고 있어
   조회 순서와 용도가 다르다 — 조회 순서는 `zone_groups[].query_order`라는 **별도 필드**로 둔다.
3. `solutions`에는 **`polestar` 하나만** 등록한다. apm·dpm은 주석 예시로만 둔다(등록 0건에서 회귀 0 단언).
4. `ZONE_CLARIFY_OPTIONS`의 `group` 필드를 레지스트리 파생으로 전환하되, **값은 현행과 동일**해야 한다
   (`bank`/`common`) — 골든 테스트로 고정한다.
5. 이 모듈은 **소비처를 만들지 않는다.** `partition_execution_groups`를 노출만 하고 아무도 호출하지
   않는다 → 코드 동작 변화 0.

## Objective

실행 그룹의 **축(axis)을 레지스트리에 선언**하고, db_id 목록을 순서 있는 그룹으로 나누는
**결정적 분할기**를 제공한다. `plans/82` §3.5 실측: 현재 레지스트리에는 실행 순서·그룹 경계·백엔드
종류를 표현하는 축이 없고, 라우터 출력도 `target_databases` 평면 리스트 하나다.

**성공**: `partition_execution_groups(["polestar_b0","polestar_cm_gp","polestar_cm_yd"])` 가
`[은행존(b0), 공동존(gp,yd)]` 순서로 반환한다. **순서는 LLM이 아니라 설정이 정한다**(D-035).

## Tech Stack

기보유만 — 신규 라이브러리 0건. YAML(기존 로더) · pydantic v2 · pytest.

## Commands

```bash
python -m pytest -q tests/test_semantic_routing/test_registry_config.py \
                    tests/test_orchestration/test_execution_groups.py
python scripts/arch_check.py --ci
python scripts/overfit_check.py     # 위치 어휘 리터럴 재유입 차단
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `config/db_registry.yaml` | 수정 — `solutions`·`zone_groups` 블록 **추가** |
| `src/routing/registry.py` | 수정 — `solutions()`·`zone_groups()`·`zone_group_of()` 파생 API |
| `src/utils/query_gen_common.py` | 수정 — `partition_execution_groups()` 신설 · `ZONE_CLARIFY_OPTIONS` 파생화 |
| `tests/test_orchestration/test_execution_groups.py` | **신규** |

## Code Style

레지스트리 파생 API는 기존 `location_db_hints()`·`zone_to_db_ids()` 패턴을 따른다 —
**정본은 YAML, 코드는 파생만**. 리터럴 재유입 금지(D-131 · `overfit_check` 감시 대상).

```python
def zone_groups(self) -> tuple[ZoneGroupEntry, ...]:
    """존 그룹 선언을 query_order 순으로 반환한다(정본은 db_registry.yaml).

    zones 선언 순서(=알림 RBAC 선택지 순서)와 **다른 축**이다 — 조회 순서를 zones에
    겹치면 한쪽 요구가 다른 쪽을 흔든다(plans/82 §4.2).
    """
```

## Testing Strategy

pytest · 전부 mock · 위치 `tests/test_orchestration/test_execution_groups.py`.

| 케이스 | 기대 |
|---|---|
| `partition_execution_groups([b0, gp, yd])` | `[bank(b0), common(gp,yd)]` — **순서 단언** |
| 입력 순서를 뒤집어도(`[yd, gp, b0]`) | 같은 결과 — 입력 순서에 의존하지 않는다 |
| `partition_execution_groups([gp, yd])` | `[common(gp,yd)]` 1개 |
| `partition_execution_groups([b0])` | `[bank(b0)]` 1개 |
| `[]` / `None` | `[]` |
| 미등재 db_id 포함 | 미등재분은 **무시**하고 나머지로 그룹 구성(기존 `mixed_zone_groups` 규약과 동일) |
| `ZONE_CLARIFY_OPTIONS` | 현행과 **완전 동일**(db_id·label·group) — 골든 |
| `registry.solutions()` | `polestar` 1건 · `order=10` · `backend="sql"` |
| D-131 리허설 테스트 | 그린 유지 |

## Boundaries

**Always** — YAML이 정본 · 파생만 코드에 · 기존 키 의미 불변 · `overfit_check` 통과
**Ask first** — `zones:` 순서 변경 · `families` 의미 변경 · apm/dpm 실제 등록
**Never** — 위치 어휘를 코드 리터럴로 재도입(D-131 AST 단언) · 기존 존 테스트 완화

## Success Criteria

1. `config/db_registry.yaml`에 `solutions`(1건)·`zone_groups`(bank/common) 선언이 있다.
2. `registry.zone_groups()`가 **`query_order` 순**(bank=10 → common=20)으로 반환한다.
3. `partition_execution_groups`가 위 표대로 동작하며 **입력 순서에 불변**이다.
4. `ZONE_CLARIFY_OPTIONS`가 레지스트리 파생인데 **값은 현행과 바이트 동일**하다.
5. **기존 존 관련 테스트 전량 그린**(게이트 미변경 — 이 모듈은 소비처를 만들지 않는다).
6. `arch_check --ci` · `overfit_check` exit 0.

## Open Questions

없음.
