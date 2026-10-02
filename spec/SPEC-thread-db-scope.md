# Spec: 스레드 DB 스코프 계약 — `db-scope-resolve` · `db-scope-source` · `db-scope-reset` · `db-scope-contract` · `deep-agent-selected-db`

> `CAPABILITY-MAP-90.md` · `plans/90` §3·§9 · D-205 · 작성 2026-09-09

## Objective

채팅 스레드가 **다음 질의에서 어느 폴스타(존)를 승계할지**를 서버가 매 응답에 보고하고, 사용자가 그 승계를
**끊을 수 있게** 한다. 표시 값은 다음 턴 `context_resolver`가 실제로 읽을 값과 **같은 함수**에서 나온다.

사용자 스토리:
1. 질의 응답마다 "이 창은 지금 은행존을 보고 있다(승계 중)"를 알 수 있다.
2. 칩에서 존을 고르면 다음 질의 1건이 그 존으로 가고, 이후는 승계로 이어진다.
3. ×로 해제하면 다음 질의는 첫 턴 규칙(존 미지정 대량 조회면 역질문)을 따른다.
4. 운영 1단(`deep_agent`)에서도 2·3이 동작한다.

## Tech Stack

Python 3.11 · FastAPI · LangGraph(`AsyncSqliteSaver` 델타 병합) · pydantic v2 · pytest(비동기 `pytest.mark.asyncio`) · LLM 호출 0

## Commands

```bash
pytest tests/test_routing/test_db_scope.py tests/test_multiturn/test_db_scope_reset.py \
       tests/test_api/test_db_scope_contract.py tests/test_orchestration/test_deep_agent_wiring.py -q
pytest tests/test_orchestration tests/test_scope_select tests/test_api tests/test_nodes tests/test_multiturn -q   # 회귀
python scripts/arch_check.py --ci && python scripts/overfit_check.py --ci
ruff check src/routing/db_scope.py src/api/routes/scope.py
```

## Project Structure

```
src/routing/db_scope.py            → [신규] resolve_thread_db_ids · build_db_scope · scope_axes_options (infrastructure)
src/nodes/context_resolver.py      → _extract_previous_db_ids를 db_scope.extract_state_db_ids로 위임 · db_scope_reset 처리
src/state.py                       → AgentState.db_scope_source · db_scope_reset(요청 스코프) · create_followup_input(reset_db_scope)
src/routing/semantic_router.py     → 대상 확정 지점에 db_scope_source
src/orchestration/subagents.py     → data_query 결과 db_origin
src/orchestration/result_aggregator.py → _collect_db_promotion이 db_scope_source도 승격
src/orchestration/deep_agent.py    → _AMBIENT_KEYS += selected_db_ids
src/api/schemas.py                 → QueryRequest.reset_db_scope · QueryResponse.db_scope
src/api/routes/query.py            → 4 응답 경로 db_scope · pre-gate 판정 · 후속 입력 reset
src/api/routes/scope.py            → [신규] GET /scope/options
src/api/server.py                  → 라우터 등록
tests/test_routing/test_db_scope.py · tests/test_multiturn/test_db_scope_reset.py · tests/test_api/test_db_scope_contract.py
```

## Code Style

```python
def resolve_thread_db_ids(state: Mapping[str, Any]) -> list[str]:
    """다음 턴 context_resolver가 previous_db_ids로 읽을 값 — 단일 출처(D-205)."""
    ids = extract_state_db_ids(state)
    if ids:
        return ids
    ctx = state.get("conversation_context") or {}
    return list(ctx.get("previous_db_ids") or [])
```
- 함수는 순수(state dict 입력 → dict 출력). 레지스트리는 `get_registry()`로만.
- 신규 state 키는 요청 스코프면 `create_followup_input`에서 매 턴 재공급(D-064).
- 문자열 reason 매칭 금지 — `db_origin`/`db_scope_source` 구조화 키.
- 응답 키는 4경로(`/query` · `/query/stream` done · `/query/file` · `/query/file/stream` done) **동시** 삽입.

## Testing Strategy

pytest · 모두 mock/순수(LLM·DB 0, D-127). 단위: `db_scope.py` 함수 · reset 3지점 · `_AMBIENT_KEYS`.
계약: `QueryResponse(**response_data)`가 `db_scope`를 받는다 · 4경로 코드에 `"db_scope"` 키 존재를
소스 정적 단언(`scope_reexpand` SSE 누락 선례 차단) · `GET /scope/options` TestClient.
회귀: 기존 `test_zone_*`·`test_scope_select`·`test_result_aggregator`·`test_context_resolver` 무변화.

## Boundaries

- **Always**: `resolve_thread_db_ids`를 `context_resolver`와 라우트 양쪽에서 호출 · 기본값(`reset_db_scope=False`)에서 현행 바이트 동일 · 레지스트리 접근자만(리터럴 0)
- **Ask first**: 플래그 신설 · `target_databases` shape 변경 · 파일 라우트 form 필드 추가
- **Never**: LLM 호출 · `selected_db_ids` 의미 변경(플래너 단락 재정의는 82 Wave 7·87 J5 소관) · `ZONE_GROUP_EXCLUSIVE` 완화

## Success Criteria

1. `resolve_thread_db_ids(state_N)` == 턴 N+1 `context_resolver`의 `previous_db_ids`(같은 state로 두 경로 결과 동일).
2. `build_db_scope`: `zone_group.code`는 `registry.zone_group_of`, `solutions[].code`는 `DBEntry.family`↔`SolutionSpec.family`로 판정. 미등록 db_id는 `zone_group=None`·solution 누락 없이 `db_ids`에만 남는다.
3. `source`: `selected_db_ids` 있음 → `selected` · `db_scope_source` 있음 → 그 값 · 대상 없고 sticky만 → `inherited` · 대상 있고 신호 없음 → `classified` · 아무것도 없음 → `none`.
4. reset: `create_followup_input(reset_db_scope=True)` → `active_db_id=None`·`target_databases=[]`·`mapped_db_ids=None`·`db_scope_reset=True`; `context_resolver`는 `db_scope_reset`이면 `previous_db_ids=[]`·`previous_location=""`이고 `previous_entities`는 유지; pre-gate는 reset 턴 또는 승계 스코프 부재 턴을 첫 턴으로 본다(기존 `test_followup_turn_passes`는 `{"prev": True}`처럼 스코프 없는 체크포인트를 쓰므로 **판정 기준 변경 후에도 통과해야 한다** — 즉 "체크포인트 존재 & 스코프 부재"는 여전히 비발동. ⓒ는 reset 플래그에만 반응한다. §Open Questions 1).
5. 1단: `_extract_ambient_state({"selected_db_ids": ["polestar_b0"]})`에 키가 있다.
6. 4 응답 경로 코드에 `"db_scope"` 키 · `GET /api/v1/scope/options`가 `axes[0].axis=="zone_group"`·옵션 라벨이 레지스트리 `zone_groups[].label`.
7. `arch_check --ci`·`overfit_check --ci` 0 · 회귀 기준선 신규 실패 0.

## Open Questions (구현 중 확정한 판단)

1. **pre-gate ⓒ의 범위**: 계획 §3.2는 "승계할 스코프가 없으면 첫 턴 규칙"이었으나 기존 테스트
   `test_followup_turn_passes`·`test_follow_up_turn_inherits_scope`가 **스코프 없는 체크포인트도 비발동**으로
   못박고 있다(후속 턴 = 승계·previous_entities 우선). 이를 뒤집지 않는다 — ⓒ는 **`reset_db_scope=True`인
   턴에만** 첫 턴 규칙을 적용한다. 자연 소진(스코프 없는 후속 턴)은 현행 유지.
2. **파일 라우트 reset 필드**: `create_initial_state`가 `conversation_context=None`·`target_databases=[]`·
   `active_db_id=None`으로 매 파일 턴을 시작하므로 파일 턴은 **원래 승계하지 않는다**(실측 `state.py`).
   reset form 필드는 죽은 입력이 되므로 **추가하지 않는다**(계획 §3.2 ⓓ 이탈 — 근거 첨부). `db_scope` 응답
   메타는 파일 2경로에도 넣는다(표시 대칭).
3. **`target_databases` shape**: 기존 테스트가 `[{"db_id": ...}]` 정확 일치를 단언하므로 origin을 그 안에
   넣지 않고 top-level `db_scope_source`로 승격한다.
