# Spec: dependency-notes

> Module id: `dependency-notes` | 근거: `plans/88` §4.4 · §4.10 · §2.2(E-2) · W6 | 예약 결정: **D-203**
> 계층: domain(`state.py`) + orchestration(`agent_orchestrator`·`result_aggregator`·`deep_agent`) + interface(`routes/query.py`)

## ASSUMPTIONS I'M MAKING

1. 노트 한 건의 shape는 `{"kind", "task_id", "reason", "detail"}`이며 `kind ∈ {gate, trace, postcheck, sufficiency, truncation, scope_db}`.
   체크포인터 직렬화 대상이라 dict다.
2. 경과 블록은 **결정적 텍스트**(LLM 0회)이며 최종 응답 말미에 `---` 구분 후 덧붙인다 — `_apply_incomplete_notice`(D-092)와
   같은 자리·같은 방식. 노트가 없으면 응답 바이트 동일.
3. `sufficiency_shortfalls`는 **삭제하지 않는다** — 같은 내용을 노트(`kind=sufficiency`)로 병기하고 폐기 기한 2027-03-09를
   주석에 남긴다(D-161 ①).
4. 1단은 도구 클로저의 `ambient_state`에 노트를 쌓고(`ambient_state["dependency_notes"]`), `run_deep_agent`가 그것을
   집계 상태에 싣는다. `_AMBIENT_KEYS`는 바꾸지 않는다(노트는 입력이 아니라 출력).
5. 노트 렌더는 **플래그와 무관**하게 "노트가 있으면" 붙는다 — 생산자가 전부 플래그 뒤에 있으므로 off면 노트가 0건이라
   비트 동일. 단 `sufficiency` 노트는 생산 조건이 현행(`injected` 존재)과 같아 **현행에서 응답에 안 보이던 사유가
   보이게 된다** — 이것이 이 모듈의 의도된 동작 변화(죽은 채널 복구)이며 78 W5-3의 원래 계약이다.

## Objective

**게이트·대조·절단·충족도 미달의 사유가 사용자 응답에 실제로 도달하게 한다.**

실측(`plans/88` §2.2): `agent_orchestrator`가 `sufficiency_shortfalls`를 상태에 쓰지만 `src/` 어디에서도 읽지 않고
`AgentState`에 선언도 없다. 이 모듈은 채널 하나(`dependency_notes`)를 선언하고 생산자→렌더→API 노출을 한 줄로 잇는다.

**성공**: 노트가 있으면 최종 응답 말미에 "순차 처리 경과" 블록이 결정적으로 붙고, `/query` 응답 JSON에 `dependency_notes`가
그대로 실린다. 1단·2단 모두.

## Tech Stack

기보유만.

## Commands

```bash
python -m pytest -q tests/test_composite/test_dependency_notes.py tests/test_orchestration/test_result_aggregator.py \
                    tests/test_state.py tests/test_api
python scripts/arch_check.py --ci
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `src/state.py` | `dependency_notes: Optional[list[dict]]` 선언 + `create_initial_state`·`create_followup_input` 초기화 |
| `src/utils/prior_dependency.py` | `render_dependency_notes(notes) -> str`(결정적 렌더) · `sufficiency_notes(report) -> list[dict]` |
| `src/orchestration/agent_orchestrator.py` | 노트 수집·반환(`dependency_notes`) · 충족도 미달 병기 |
| `src/orchestration/result_aggregator.py` | `_apply_dependency_notes(result, state)` — 3개 반환 지점 |
| `src/orchestration/deep_agent.py` | ambient 노트 → `_aggregate_with_fabrix` 상태 주입 · 반환에 포함 |
| `src/orchestration/deepagents_tools.py` | 노트를 `ambient_state["dependency_notes"]`에 적재 |
| `src/api/routes/query.py` | 응답 JSON `dependency_notes` 노출 |
| `tests/test_composite/test_dependency_notes.py` | **신규** |

## Code Style

```python
def _apply_dependency_notes(result: dict, state: AgentState) -> dict:
    """순차 처리 경과를 최종 응답 말미에 **결정적으로** 덧붙인다 (D-203 · plans/88 §4.4).

    LLM 합성에 맡기면 누락된다(spike_notes와 같은 이유). 노트가 없으면 원본 그대로.
    """
    block = render_dependency_notes(
        list(state.get("dependency_notes") or []) + list(result.get("dependency_notes") or [])
    )
    if not block:
        return result
    ...
```

## Testing Strategy

| 케이스 | 기대 |
|---|---|
| 노트 0건 | `result_aggregator` 반환 `final_response` **바이트 동일** |
| gate 노트 1건 | 응답 말미에 `## 순차 처리 경과` 블록 + 사유 문장 |
| 충족도 미달(기존 `test_sufficiency` 시나리오) | `dependency_notes`에 `kind=sufficiency` 1건 **그리고** `sufficiency_shortfalls` 값 **현행과 동일**(병기) |
| **현행 결함 재현**: 충족도 미달 사유가 응답 본문에 나타난다 | 현행은 0건 → 이 모듈로 1건 |
| 1단: 도구가 노트를 남김 | `run_deep_agent` 반환 dict에 `dependency_notes` + 본문 블록 |
| 멀티턴: 직전 턴 노트 | `create_followup_input`이 `None`으로 초기화 |
| `/query` 응답 | `dependency_notes` 키 존재(없으면 `None`) |

## Boundaries

**Always** — 노트 없으면 바이트 동일 · 렌더는 LLM 0회 · 요청 스코프 초기화 2곳(초기·후속) 모두
**Ask first** — 블록 제목·문구 톤 변경(운영자 화면 어휘 정제 D-180 규약) · UI 전용 렌더 추가
**Never** — `sufficiency_shortfalls` 즉시 삭제 · `_AMBIENT_KEYS` 확장 · 노트에 SQL 원문·PII 값 포함(서버 식별 값은 최대 10개까지만 예시로)

## Success Criteria

1. `AgentState.dependency_notes` 선언 + 초기·후속 턴 초기화.
2. 노트가 있으면 2단·1단 최종 응답에 경과 블록이 붙고, 없으면 바이트 동일.
3. 충족도 미달 사유가 응답 본문에 실제로 나타난다(현행 0건 → 1건).
4. `/query` 응답에 `dependency_notes` 노출.
5. 기존 스위트 회귀 0(`sufficiency_shortfalls` 병기).

## Open Questions

없음.
