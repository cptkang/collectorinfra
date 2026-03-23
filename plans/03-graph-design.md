# 03. LangGraph 그래프 설계

> 기존 `src/graph.py` 분석 및 개선 계획

---

## 1. 현재 그래프 구현 분석

기존 `src/graph.py`는 이미 올바른 구조를 갖추고 있다:

- 7개 업무 노드 + 1개 error_response 노드 등록
- 순방향 엣지: `START -> input_parser -> schema_analyzer -> query_generator -> query_validator`
- 조건부 라우팅 3개: `route_after_validation`, `route_after_execution`, `route_after_organization`
- 종단 엣지: `output_generator -> END`, `error_response -> END`
- SqliteSaver 기반 체크포인트

**개선이 필요한 영역:**
1. 체크포인트 생성 시 `sqlite3.connect`를 직접 호출 -- 리소스 관리 취약
2. LLM 인스턴스가 각 노드 내부에서 `load_config()` + `create_llm()`으로 매번 생성 -- 비효율
3. `error_message` 초기화 타이밍이 노드별로 다름 -- 일관성 부족
4. Phase 3용 Human-in-the-loop 인터럽트 지점 미설계

---

## 2. 그래프 흐름도

```
                    START
                      │
                      ▼
               ┌──────────────┐
               │ input_parser │
               └──────┬───────┘
                      │
                      ▼
            ┌──────────────────┐
            │ schema_analyzer  │
            └────────┬─────────┘
                     │
          ┌──────────▼──────────┐
          │                     │
          ▼                     │
  ┌───────────────┐             │
  │query_generator│◄────────────┤ (재시도 회귀)
  └───────┬───────┘             │
          │                     │
          ▼                     │
  ┌───────────────┐             │
  │query_validator│             │
  └───────┬───────┘             │
          │                     │
     ┌────┴────┐                │
     │ 분기    │                │
  통과│    실패│                │
     │    (retry<3)────────────┘
     │    (retry>=3)───────────┐
     ▼                         │
  ┌───────────────┐            │
  │query_executor │            │
  └───────┬───────┘            │
          │                    │
     ┌────┴────┐               │
     │ 분기    │               │
  성공│    에러│               │
     │    (retry<3)────────────┘
     │    (retry>=3)───────────┐
     ▼                         │
  ┌──────────────────┐         │
  │result_organizer  │         │
  └───────┬──────────┘         │
          │                    │
     ┌────┴────┐               │
  충분│    부족│               │
     │    (retry<3)────────────┘
     ▼                         │
  ┌──────────────────┐         │
  │output_generator  │         │
  └───────┬──────────┘         │
          │                    │
          ▼                    ▼
         END          ┌───────────────┐
                      │error_response │
                      └───────┬───────┘
                              │
                              ▼
                             END
```

---

## 3. 조건부 라우팅 함수 (현재 코드 유지 + 개선)

### 3.1 route_after_validation (현재 코드 OK)

```python
def route_after_validation(state: AgentState) -> str:
    if state["validation_result"]["passed"]:
        return "query_executor"
    if state["retry_count"] >= 3:
        return "error_response"
    return "query_generator"
```

### 3.2 route_after_execution (현재 코드 OK)

```python
def route_after_execution(state: AgentState) -> str:
    if state.get("error_message"):
        if state["retry_count"] >= 3:
            return "error_response"
        return "query_generator"
    return "result_organizer"
```

### 3.3 route_after_organization (현재 코드 OK)

```python
def route_after_organization(state: AgentState) -> str:
    if not state["organized_data"]["is_sufficient"]:
        if state["retry_count"] < 3:
            return "query_generator"
    return "output_generator"
```

---

## 4. 개선 항목

### 4.1 체크포인트 관리 개선

**현재 문제:** `sqlite3.connect()`로 직접 연결 생성 후 `SqliteSaver`에 전달. 앱 종료 시 연결이 명시적으로 닫히지 않음.

**개선안:**

```python
# src/graph.py (개선)

from contextlib import contextmanager


@contextmanager
def _create_checkpointer(config: AppConfig):
    """체크포인트 저장소를 컨텍스트 매니저로 관리한다."""
    if config.checkpoint_backend == "sqlite":
        import sqlite3
        conn = sqlite3.connect(config.checkpoint_db_url, check_same_thread=False)
        try:
            yield SqliteSaver(conn)
        finally:
            conn.close()
    else:
        from langgraph.checkpoint.postgres import PostgresSaver
        saver = PostgresSaver.from_conn_string(config.checkpoint_db_url)
        try:
            yield saver
        finally:
            pass  # PostgresSaver가 내부적으로 풀 관리


def build_graph(config: AppConfig):
    """에이전트 그래프를 빌드한다."""
    graph = StateGraph(AgentState)

    # 노드 등록 (기존과 동일)
    graph.add_node("input_parser", input_parser)
    graph.add_node("schema_analyzer", schema_analyzer)
    graph.add_node("query_generator", query_generator)
    graph.add_node("query_validator", query_validator)
    graph.add_node("query_executor", query_executor)
    graph.add_node("result_organizer", result_organizer)
    graph.add_node("output_generator", output_generator)
    graph.add_node("error_response", _error_response_node)

    # 엣지 정의 (기존과 동일)
    graph.add_edge(START, "input_parser")
    graph.add_edge("input_parser", "schema_analyzer")
    graph.add_edge("schema_analyzer", "query_generator")
    graph.add_edge("query_generator", "query_validator")

    graph.add_conditional_edges(
        "query_validator", route_after_validation,
        {"query_executor": "query_executor",
         "query_generator": "query_generator",
         "error_response": "error_response"},
    )
    graph.add_conditional_edges(
        "query_executor", route_after_execution,
        {"result_organizer": "result_organizer",
         "query_generator": "query_generator",
         "error_response": "error_response"},
    )
    graph.add_conditional_edges(
        "result_organizer", route_after_organization,
        {"output_generator": "output_generator",
         "query_generator": "query_generator"},
    )

    graph.add_edge("output_generator", END)
    graph.add_edge("error_response", END)

    # 체크포인트 적용
    # 주의: 컨텍스트 매니저를 사용하려면 앱 라이프사이클에서 관리 필요
    checkpointer = _create_checkpointer_simple(config)
    compiled = graph.compile(checkpointer=checkpointer)

    logger.info("에이전트 그래프 빌드 완료")
    return compiled
```

### 4.2 LLM 인스턴스 공유

**현재 문제:** `input_parser`, `query_generator`, `output_generator` 각각에서 `load_config()` + `create_llm()`을 호출하여 매번 새 LLM 인스턴스를 생성한다.

**개선안:** 그래프 빌드 시 config를 주입하고, 노드 함수를 클로저 또는 클래스로 감싸서 LLM을 공유한다.

```python
def build_graph(config: AppConfig):
    llm = create_llm(config)

    # 노드를 부분 적용(partial)으로 등록
    from functools import partial

    graph.add_node("input_parser", partial(input_parser, llm=llm, config=config))
    graph.add_node("query_generator", partial(query_generator, llm=llm, config=config))
    graph.add_node("output_generator", partial(output_generator, llm=llm, config=config))
    # ...
```

이를 위해 각 노드 함수의 시그니처를 확장해야 한다:

```python
# 노드 함수 시그니처 변경 (예: input_parser)
async def input_parser(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    config: AppConfig | None = None,
) -> dict:
    if config is None:
        config = load_config()
    if llm is None:
        llm = create_llm(config)
    # ... 이하 동일
```

**하위 호환성:** 키워드 인자 기본값을 `None`으로 설정하여 기존 호출 방식도 유지된다.

### 4.3 error_message 초기화 일관성

**현재 문제:** `query_generator`에서 `error_message=None`으로 초기화하지만, `schema_analyzer`에서 에러 발생 시 설정한 `error_message`가 이후 노드에서 오인될 수 있다.

**개선안:** 각 노드가 정상 완료 시 반드시 `error_message=None`을 반환하도록 통일한다 (현재 대부분의 노드가 이미 이렇게 하고 있으나 `input_parser`에서 누락).

---

## 5. Phase 3: Human-in-the-loop 설계

```python
# Phase 3에서 query_validator 이후에 승인 단계 삽입

def route_after_validation_with_approval(state: AgentState) -> str:
    if state["validation_result"]["passed"]:
        if state.get("require_approval", False):
            return "await_user_approval"  # 인터럽트 노드
        return "query_executor"
    if state["retry_count"] >= 3:
        return "error_response"
    return "query_generator"


async def await_user_approval(state: AgentState) -> dict:
    """사용자 승인을 대기한다. LangGraph의 interrupt 메커니즘 사용."""
    from langgraph.prebuilt import interrupt

    approval = interrupt({
        "type": "sql_approval",
        "sql": state["generated_sql"],
        "message": "다음 SQL을 실행하시겠습니까?",
    })

    if approval.get("approved"):
        return {"current_node": "await_user_approval"}
    else:
        user_feedback = approval.get("feedback", "사용자가 실행을 거부했습니다.")
        return {
            "error_message": user_feedback,
            "current_node": "await_user_approval",
        }
```

---

## 6. 그래프 빌드 의사코드 (전체)

```python
def build_graph(config: AppConfig):
    graph = StateGraph(AgentState)

    # 1. 노드 등록
    graph.add_node("input_parser", input_parser)
    graph.add_node("schema_analyzer", schema_analyzer)
    graph.add_node("query_generator", query_generator)
    graph.add_node("query_validator", query_validator)
    graph.add_node("query_executor", query_executor)
    graph.add_node("result_organizer", result_organizer)
    graph.add_node("output_generator", output_generator)
    graph.add_node("error_response", _error_response_node)
    # Phase 3:
    # graph.add_node("await_user_approval", await_user_approval)

    # 2. 순방향 엣지
    graph.add_edge(START, "input_parser")
    graph.add_edge("input_parser", "schema_analyzer")
    graph.add_edge("schema_analyzer", "query_generator")
    graph.add_edge("query_generator", "query_validator")

    # 3. 조건부 엣지
    graph.add_conditional_edges("query_validator", route_after_validation, {...})
    graph.add_conditional_edges("query_executor", route_after_execution, {...})
    graph.add_conditional_edges("result_organizer", route_after_organization, {...})

    # 4. 종단 엣지
    graph.add_edge("output_generator", END)
    graph.add_edge("error_response", END)

    # 5. 체크포인트
    checkpointer = _create_checkpointer(config)
    compiled = graph.compile(checkpointer=checkpointer)

    return compiled
```

---

## 7. 그래프 실행 예시

```python
# CLI 모드
async def run_query(query: str) -> str:
    config = load_config()
    graph = build_graph(config)
    initial_state = create_initial_state(user_query=query)
    thread_config = {"configurable": {"thread_id": "cli-session"}}
    result = await graph.ainvoke(initial_state, thread_config)
    return result.get("final_response", "응답을 생성할 수 없습니다.")

# API 서버 모드 (기존 코드 유지)
# graph는 app.state.graph에 저장하여 재사용
```

---

## 8. 기존 코드 대비 변경 사항 요약

| 항목 | 현재 | 변경 |
|------|------|------|
| 체크포인트 | `sqlite3.connect()` 직접 호출 | 컨텍스트 매니저로 리소스 관리 강화 |
| LLM 인스턴스 | 각 노드에서 매번 생성 | 그래프 빌드 시 한 번 생성, partial로 주입 |
| error_message 초기화 | 노드별로 불일치 | 모든 노드에서 정상 완료 시 `None` 반환 통일 |
| Human-in-the-loop | 미설계 | Phase 3 설계 포함 (await_user_approval 노드) |
| 노드 함수 시그니처 | `(state) -> dict` | `(state, *, llm=None, config=None) -> dict` (하위 호환) |
