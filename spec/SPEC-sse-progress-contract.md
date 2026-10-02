# Spec: sse-progress-contract — 스트리밍 진행 신호 계약

> 모듈 id `sse-progress-contract` (`CAPABILITY-MAP-89.md`) · `plans/89` §3.1·§3.2 · D-204 예약.
> **가정(게이트 권고안 채택)**: G-1 신규 이벤트·하트비트 **기본 on**(예외 근거를 config 주석에 남긴다) ·
> G-3 하트비트 **5s**.

## Objective

`/api/v1/query/stream`·`/api/v1/query/file/stream`이 무이벤트 구간에서도 "살아 있음"과 "지금 무엇을
하는지"를 클라이언트에 알린다. 운영 1단 `deep_agent` 경로에서 `field_mapper` 이후 최종 토큰까지
이벤트 0건이던 것을 없앤다. 무이벤트 상한(`API_QUERY_TIMEOUT` 240s/`API_FILE_QUERY_TIMEOUT`)의 의미는
그대로 보존한다(D-066 후속).

## Tech Stack

FastAPI `StreamingResponse` · LangGraph 1.2.11 `astream_events(v2)` · `langchain_core` 1.6.1
(`adispatch_custom_event`) · pydantic-settings(`ServerConfig`, prefix `API_`).

## Commands

```
pytest tests/test_api/test_stream_nested_events.py tests/test_api/test_query_stream_progress.py -q
pytest tests/test_api tests/test_multiturn -q
python scripts/arch_check.py --ci && python scripts/overfit_check.py --ci
ruff check src/api/routes/query.py src/config.py
```

## Project Structure

```
src/api/routes/query.py           두 스트림 제너레이터 — 공통 헬퍼 _graph_event_stream / _progress_sse_payload
src/config.py                     ServerConfig.sse_heartbeat_interval_sec · sse_progress_events
config/settings_help/infrastructure.yaml  두 설정의 큐레이션 설명(커버리지 100% 테스트)
.env.example                      API_SSE_* 주석 예시
tests/test_api/test_stream_nested_events.py   T0 스파이크(중첩 그래프 이벤트 전파 실측)
tests/test_api/test_query_stream_progress.py  계약 테스트(MockGraph)
```

## 계약 (SSE `data:` JSON)

| type | payload | 발생 |
|---|---|---|
| `heartbeat` | `{elapsed_ms, last_activity_ms}` | 큐에서 `sse_heartbeat_interval_sec` 동안 이벤트가 없을 때. 무이벤트 연속 시간이 `effective_timeout`을 넘으면 기존 `error`("처리 시간이 초과되었습니다…") 후 종료 |
| `progress` | `{kind: "tool"\|"step"\|"task", name, phase: "start"\|"end", node, timestamp_ms, label?, task?}` | `on_tool_start`/`on_tool_end` → `kind:"tool"`, `name`=도구명. `on_custom_event` → `name`이 `"task"`면 `kind:"task"`+`task` 페이로드, 그 외 `kind:"step"`(`data.phase`·`data.label`) |
| 기존 6종 | 불변 | |

- `_known_nodes`에 `deep_agent`·`fault_diagnosis`·`cache_management` 추가. `_extract_node_progress("deep_agent")`는
  `{tool_calls, resume_attempts?, incomplete?}`를 반환한다(있는 값만).
- `sse_progress_events=false`면 `progress`·`heartbeat`를 **내보내지 않는다**(바이트 열 현행 동일). 화이트리스트 보정은
  플래그와 무관하다(D-039 화이트리스트의 연장 — 누락 정정).
- 생산자 태스크는 소비자가 어떤 경로로 끝나든(`return`·예외·클라이언트 단절) `finally`에서 취소된다.

## Code Style

```python
async def _graph_event_stream(graph, input_state, thread_config, *, idle_timeout, heartbeat_interval):
    """astream_events를 생산자 태스크로 돌리고 (kind, payload)를 낸다.

    kind: "event" | "heartbeat" | "timeout". wait_for(__anext__) 재호출 금지 — 취소된
    __anext__는 제너레이터를 깨뜨린다(plans/89 §3.2-④).
    """
```

주석은 한국어, 결정 근거는 D-번호로 인용. 기존 두 제너레이터의 분기 구조·변수명은 유지하고 루프 머리만 교체한다.

## Testing Strategy

pytest · 실 LLM 0(D-127). MockGraph(`tests/e2e/conftest.py` 형식)를 `app.state.graph`에 주입해 SSE 바이트를
파싱한다. 느린 MockGraph(이벤트 간 지연)로 하트비트·타임아웃·취소를 검증한다.

## Boundaries

- Always: 두 스트림 라우트에 대칭 적용(공통 헬퍼로 강제) · 무이벤트 상한 의미 보존 · 미지 이벤트는 클라이언트가 무시
- Ask first: 타임아웃 기본값 변경 · SSE 이벤트 기존 6종의 shape 변경
- Never: 실 LLM/네트워크 호출 테스트 · 인라인 `.env` 주석 · 침묵 폴백(타임아웃은 반드시 `error`로 노출)

## Success Criteria

1. MockGraph에서 `deep_agent` `node_start`/`node_complete`가 **두 라우트 모두**에서 나온다.
2. 이벤트 간 지연 ≥ 2×하트비트 주기인 MockGraph에서 `heartbeat` ≥ 1건, `last_activity_ms`가 단조 증가하지 않는다(마지막 활동 고정).
3. 무이벤트가 `idle_timeout`을 넘으면 기존 문구의 `error` 1건 후 스트림 종료, 생산자 태스크 취소됨.
4. `on_tool_start` mock 이벤트 → `progress{kind:"tool", phase:"start"}`; `on_custom_event(name="task")` → `progress{kind:"task"}`.
5. `sse_progress_events=false`에서 동일 MockGraph의 SSE 바이트 열이 현행(변경 전 코드)과 동일.
6. T0: 노드 안에서 `ainvoke`된 중첩 CompiledGraph의 `on_tool_start`·`on_custom_event`가 바깥 `astream_events`에 나타난다.

## Open Questions

- T0가 불성립하면 `deep_agent.py:227`의 `ainvoke(config=…)`에 부모 콜백을 명시 전달한다(계획서 T0 대체 경로).
