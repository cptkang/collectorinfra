# Python 비동기(Async) 프로그래밍 가이드

> 이 문서는 `collectorinfra` 코드베이스에서 실제로 사용되는 비동기 패턴과 작성 방법을 코드 예제와 함께 설명합니다.

---

## 목차

1. [비동기 프로그래밍 개요](#1-비동기-프로그래밍-개요)
2. [기본 문법: `async def` / `await`](#2-기본-문법-async-def--await)
3. [이벤트 루프와 `asyncio.run()`](#3-이벤트-루프와-asynciorun)
4. [비동기 컨텍스트 매니저](#4-비동기-컨텍스트-매니저)
5. [비동기 제너레이터와 `yield`](#5-비동기-제너레이터와-yield)
6. [타임아웃 처리: `asyncio.wait_for()`](#6-타임아웃-처리-asynciowait_for)
7. [재연결 및 재시도 패턴](#7-재연결-및-재시도-패턴)
8. [LangGraph 노드에서의 비동기 패턴](#8-langgraph-노드에서의-비동기-패턴)
9. [FastAPI 라이프사이클과 비동기 초기화](#9-fastapi-라이프사이클과-비동기-초기화)
10. [비동기 DB 클라이언트 패턴](#10-비동기-db-클라이언트-패턴)
11. [동기 vs 비동기 체크포인터](#11-동기-vs-비동기-체크포인터)
12. [주요 주의사항 및 Best Practice](#12-주요-주의사항-및-best-practice)

---

## 1. 비동기 프로그래밍 개요

### 왜 비동기가 필요한가?

`collectorinfra`는 다음과 같이 I/O 대기 시간이 긴 작업을 많이 수행합니다.

- **LLM API 호출**: OpenAI, Anthropic 등 외부 AI 서비스 요청 (수초~수십 초)
- **DB 쿼리 실행**: MCP 서버를 통해 데이터베이스에 SQL 실행
- **Redis 캐시 조회/저장**: 네트워크 I/O
- **SSE(Server-Sent Events) 스트리밍**: 실시간 HTTP 연결 유지

동기(blocking) 방식으로 이를 처리하면, 한 요청이 LLM 응답을 기다리는 동안 **다른 모든 요청이 멈춥니다**. 비동기(async) 방식을 사용하면 대기 중인 동안 다른 작업을 처리할 수 있습니다.

### 동작 원리 요약

```
동기 방식:
  요청1 → LLM 호출 (3초 대기) → 응답 → 요청2 처리 시작
  총 시간: 순차 실행

비동기 방식:
  요청1 → LLM 호출 시작 (비동기) → [대기 중] → 요청2 처리 → LLM 응답 수신 → 요청1 완료
  총 시간: 겹쳐서 실행 (concurrent)
```

Python의 `asyncio`는 **단일 스레드**에서 이벤트 루프(event loop)를 통해 이를 구현합니다. CPU를 직접 사용하는 연산이 아닌 I/O 대기 구간에서 제어권을 다른 코루틴에 넘겨줍니다.

---

## 2. 기본 문법: `async def` / `await`

### `async def`: 비동기 함수(코루틴) 정의

```python
async def query_generator(state: AgentState, *, llm, app_config) -> dict:
    """SQL 쿼리를 생성하는 비동기 함수."""
    # ...
```

- `async def`로 정의된 함수는 **코루틴(coroutine)**입니다.
- 호출해도 즉시 실행되지 않고, **코루틴 객체**를 반환합니다.
- `await`를 붙여야 실제 실행됩니다.

### `await`: 비동기 함수 실행 및 결과 대기

```python
# src/nodes/query_generator.py
response = await llm.ainvoke(messages)   # LLM 호출 완료까지 대기
```

```python
# src/nodes/query_executor.py
result = await client.execute_sql(sql)   # SQL 실행 완료까지 대기
```

- `await` 뒤에는 반드시 **awaitable 객체**(코루틴, Task, Future)가 와야 합니다.
- `await`에 도달하면 이벤트 루프에 제어권을 반환하고, I/O가 완료되면 다시 이 지점에서 재개됩니다.

### 내부 호출 체인

노드 함수가 `async def`이면, 해당 함수 내부에서 호출하는 모든 비동기 함수도 `await`로 호출해야 합니다.

```python
async def schema_analyzer(state, *, llm, app_config) -> dict:
    async with get_db_client(app_config) as client:          # await (컨텍스트 매니저)
        schema = await _get_schema_with_cache(client, ...)   # await
        relevant = await _llm_select_relevant_tables(llm, ...)  # await
        ...
```

---

## 3. 이벤트 루프와 `asyncio.run()`

### CLI 진입점에서의 비동기 실행

동기 함수(`main()`)에서 비동기 함수를 실행할 때는 `asyncio.run()`을 사용합니다.

```python
# src/main.py

async def run_query(query: str) -> str:
    """비동기: 그래프를 실행하고 최종 응답 반환."""
    config = load_config()
    graph = build_graph(config)
    initial_state = create_initial_state(user_query=query)
    thread_config = {"configurable": {"thread_id": "cli-session"}}
    result = await graph.ainvoke(initial_state, thread_config)
    return result.get("final_response", "응답을 생성할 수 없습니다.")


def main() -> None:
    """동기 진입점 - asyncio.run()으로 비동기 함수를 실행."""
    args = parser.parse_args()
    if args.query:
        response = asyncio.run(run_query(args.query))  # 이벤트 루프 생성 및 실행
        print(response)
```

**주의**: `asyncio.run()`은 **새 이벤트 루프를 생성**합니다. 이미 이벤트 루프가 실행 중인 환경(FastAPI, Jupyter 등)에서는 사용할 수 없습니다.

### LangGraph의 `ainvoke()`

`graph.ainvoke()`는 LangGraph의 비동기 실행 메서드입니다. 내부적으로 모든 노드를 비동기로 실행합니다.

```python
result = await graph.ainvoke(initial_state, thread_config)
```

---

## 4. 비동기 컨텍스트 매니저

### `@asynccontextmanager`

`contextlib.asynccontextmanager` 데코레이터를 사용하면 `async with` 블록을 지원하는 팩토리 함수를 만들 수 있습니다.

```python
# src/dbhub/client.py

from contextlib import asynccontextmanager
from typing import AsyncGenerator

@asynccontextmanager
async def get_dbhub_client(
    dbhub_config: DBHubConfig,
    query_config: QueryConfig | None = None,
) -> AsyncGenerator[DBHubClient, None]:
    """DBHub 클라이언트를 생성하고 연결을 관리한다."""
    client = DBHubClient(dbhub_config, query_config)
    try:
        await client.connect()   # 진입 시 실행
        yield client             # with 블록 내부에 클라이언트 제공
    finally:
        await client.disconnect()  # 블록 종료(정상/예외) 시 항상 실행
```

**사용 예**:

```python
# src/nodes/query_executor.py

async with get_db_client(app_config) as client:
    result = await client.execute_sql(sql)
# 블록 종료 시 client.disconnect() 자동 호출
```

이 패턴의 장점:
- 연결/해제를 명시적으로 관리하지 않아도 됨
- 예외가 발생해도 `finally`가 실행되어 리소스 누수 방지

### `async with` 직접 사용

MCP SDK의 SSE 클라이언트는 자체적으로 비동기 컨텍스트 매니저를 제공합니다.

```python
# src/dbhub/client.py

self._sse_context = sse_client(url=self._config.server_url)
sse_transport = await self._sse_context.__aenter__()   # async with 진입
```

---

## 5. 비동기 제너레이터와 `yield`

### FastAPI 라이프사이클에서의 `yield`

`asynccontextmanager`와 결합하여 FastAPI의 `lifespan` 이벤트 핸들러를 구현합니다.

```python
# src/api/server.py

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """앱 시작/종료 시 실행되는 라이프사이클 관리자."""
    # --- 앱 시작 시 실행 ---
    config = load_config()
    checkpointer = await _create_checkpointer_async(config)
    app.state.graph = build_graph(config, checkpointer=checkpointer)

    # Redis 연결
    if config.schema_cache.backend == "redis":
        cache_mgr = get_cache_manager(config)
        await cache_mgr.ensure_redis_connected()

    yield   # ← 여기서 앱이 실행됨 (요청 처리 시작)

    # --- 앱 종료 시 실행 ---
    if hasattr(checkpointer, "conn"):
        await checkpointer.conn.close()
    await cache_mgr.disconnect()
```

`yield` 이전 코드는 **서버 시작 시**, `yield` 이후 코드는 **서버 종료 시** 실행됩니다. 이를 통해 리소스 초기화와 정리를 한 함수에 명확히 담을 수 있습니다.

---

## 6. 타임아웃 처리: `asyncio.wait_for()`

외부 서비스 호출 시 무한 대기를 방지하기 위해 `asyncio.wait_for()`로 타임아웃을 설정합니다.

```python
# src/dbhub/client.py

async def execute_sql(self, sql: str) -> QueryResult:
    try:
        result = await asyncio.wait_for(
            self._call_tool("execute_sql", {"source": self._config.source_name, "sql": sql}),
            timeout=self._config.mcp_call_timeout,   # 초 단위
        )
        ...
    except asyncio.TimeoutError:
        raise QueryTimeoutError(
            f"MCP 호출 타임아웃 ({self._config.mcp_call_timeout}초 초과): {sql[:100]}..."
        )
```

```python
# 헬스체크에서도 동일한 패턴 사용
async def health_check(self) -> bool:
    try:
        result = await asyncio.wait_for(
            self._call_tool("health_check", {"source": self._config.source_name}),
            timeout=self.HEALTH_CHECK_TIMEOUT,   # 5초
        )
        ...
    except Exception:
        return False
```

**패턴 요약**:
- `asyncio.wait_for(코루틴, timeout=초)`: 지정 시간 이내에 완료되지 않으면 `asyncio.TimeoutError` 발생
- 도메인 에러로 변환(`QueryTimeoutError`)하여 상위에서 일관되게 처리

---

## 7. 재연결 및 재시도 패턴

### 지수 백오프(Exponential Backoff) 재시도

`asyncio.sleep()`으로 비동기적으로 대기하며 재연결을 시도합니다.

```python
# src/dbhub/client.py

async def _ensure_connected_with_retry(self) -> None:
    """연결 상태를 확인하고 필요 시 재연결한다."""
    if self._connected and self._mcp_session:
        return

    for attempt in range(self.MAX_RECONNECT_ATTEMPTS):  # MAX=3
        try:
            await self.connect()
            return
        except Exception as e:
            if attempt < self.MAX_RECONNECT_ATTEMPTS - 1:
                delay = self.RECONNECT_DELAY * (attempt + 1)  # 2초, 4초, ...
                logger.warning(f"재연결 시도 {attempt + 1}/{self.MAX_RECONNECT_ATTEMPTS}, {delay}초 후 재시도: {e}")
                await asyncio.sleep(delay)   # ← 블로킹 없이 대기
            else:
                raise DBConnectionError(f"재연결 실패 ({self.MAX_RECONNECT_ATTEMPTS}회 시도): {e}") from e
```

**핵심**: `time.sleep()`이 아닌 `await asyncio.sleep()`을 사용해야 합니다.
- `time.sleep()` → 이벤트 루프 **전체를 블로킹** (다른 작업 불가)
- `await asyncio.sleep()` → 대기 동안 이벤트 루프가 **다른 코루틴 실행 가능**

---

## 8. LangGraph 노드에서의 비동기 패턴

### 노드 함수 시그니처

모든 노드 함수는 `async def`로 정의되며, `AgentState`를 읽고 딕셔너리를 반환합니다.

```python
# 표준 노드 시그니처
async def node_name(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,    # 외부 주입 (DI)
    app_config: AppConfig | None = None,  # 외부 주입 (DI)
) -> dict:
    """노드 설명.

    Returns:
        업데이트할 State 필드 딕셔너리.
        반환된 필드만 State에 반영된다.
    """
    # 폴백: 직접 생성 (테스트 등)
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    # ... 노드 로직 ...

    return {
        "generated_sql": sql,
        "retry_count": retry_count,
        "current_node": "node_name",
    }
```

### `functools.partial`을 이용한 의존성 주입

LangGraph는 `(state) -> dict` 형태의 함수를 노드로 등록합니다. 추가 인자가 있는 노드에는 `partial`로 고정합니다.

```python
# src/graph.py

from functools import partial

llm = create_llm(config)   # LLM은 한 번만 생성

graph.add_node(
    "query_generator",
    partial(query_generator, llm=llm, app_config=config),  # llm, app_config 고정
)
```

이렇게 하면 LangGraph가 노드를 호출할 때 `state`만 전달해도 됩니다.

### LLM 비동기 호출: `ainvoke()`

LangChain의 모든 LLM 클라이언트는 `ainvoke()`를 통해 비동기 호출을 지원합니다.

```python
# src/nodes/query_generator.py

messages = [
    SystemMessage(content=system_prompt),
    HumanMessage(content=user_prompt),
]
response = await llm.ainvoke(messages)
sql = _extract_sql_from_response(response.content)
```

**비동기 메서드 명명 규칙**: LangChain/LangGraph 생태계에서는 비동기 버전 메서드에 `a` 접두사를 붙입니다.
- `invoke()` → `ainvoke()`
- `stream()` → `astream()`
- `batch()` → `abatch()`

---

## 9. FastAPI 라이프사이클과 비동기 초기화

FastAPI는 그 자체가 비동기 웹 프레임워크입니다. 라우트 핸들러도 `async def`로 정의합니다.

### 라우트 핸들러

```python
@application.get("/")
async def user_page() -> FileResponse:
    """사용자 메인 화면."""
    return FileResponse(static_dir / "index.html")
```

### 비동기 체크포인터 초기화

서버 시작 시(`lifespan`) 비동기 SQLite/Redis 체크포인터를 초기화합니다.

```python
# src/graph.py

async def _create_checkpointer_async(config: AppConfig):
    """비동기 체크포인트 저장소를 생성한다. event loop 내에서 호출해야 한다."""
    if config.checkpoint_backend == "sqlite":
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn = await aiosqlite.connect(config.checkpoint_db_url)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA synchronous=NORMAL")
            return AsyncSqliteSaver(conn)
        except Exception as e:
            logger.warning("AsyncSqliteSaver 생성 실패, InMemory 폴백: %s", e)
            return InMemorySaver()
    return InMemorySaver()
```

`aiosqlite`는 SQLite의 비동기 래퍼입니다. `await conn.execute()`를 통해 DB 설정도 비동기로 수행합니다.

---

## 10. 비동기 DB 클라이언트 패턴

### 클래스 기반 비동기 클라이언트

```python
# src/dbhub/client.py (요약)

class DBHubClient:
    def __init__(self, dbhub_config, query_config=None) -> None:
        self._mcp_session = None
        self._connected = False

    async def connect(self) -> None:
        """연결 수립 (비동기)."""
        from mcp.client.sse import sse_client
        self._sse_context = sse_client(url=self._config.server_url)
        sse_transport = await self._sse_context.__aenter__()
        read_stream, write_stream = sse_transport
        self._session_context = ClientSession(read_stream, write_stream)
        self._mcp_session = await self._session_context.__aenter__()
        await self._mcp_session.initialize()
        self._connected = True

    async def disconnect(self) -> None:
        """연결 해제 (비동기)."""
        try:
            if self._session_context:
                await self._session_context.__aexit__(None, None, None)
            if self._sse_context:
                await self._sse_context.__aexit__(None, None, None)
        finally:
            self._connected = False

    async def execute_sql(self, sql: str) -> QueryResult:
        """SQL 실행 (비동기, 타임아웃 포함)."""
        await self._ensure_connected_with_retry()
        result = await asyncio.wait_for(
            self._call_tool("execute_sql", {"source": self._config.source_name, "sql": sql}),
            timeout=self._config.mcp_call_timeout,
        )
        return self._parse_query_result(result)
```

### 팩토리 함수로 클라이언트 생명주기 관리

```python
@asynccontextmanager
async def get_dbhub_client(dbhub_config, query_config=None) -> AsyncGenerator[DBHubClient, None]:
    client = DBHubClient(dbhub_config, query_config)
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()
```

**사용 패턴**:

```python
async with get_db_client(app_config) as client:
    result = await client.execute_sql(sql)
    samples = await client.get_sample_data(table_name, limit=5)
```

---

## 11. 동기 vs 비동기 체크포인터

코드베이스에는 용도에 따라 두 가지 버전의 체크포인터가 존재합니다.

| 함수 | 방식 | 사용 장소 |
|---|---|---|
| `_create_checkpointer_async()` | `async def`, `await aiosqlite.connect()` | FastAPI lifespan (이벤트 루프 내) |
| `_create_checkpointer_simple()` | 동기 `def`, `sqlite3.connect()` | CLI 모드, 테스트 |

```python
# 동기 버전 (CLI/테스트)
def _create_checkpointer_simple(config: AppConfig):
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    conn = sqlite3.connect(config.checkpoint_db_url, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(conn)

# 비동기 버전 (서버)
async def _create_checkpointer_async(config: AppConfig):
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    conn = await aiosqlite.connect(config.checkpoint_db_url)
    await conn.execute("PRAGMA journal_mode=WAL")
    return AsyncSqliteSaver(conn)
```

**선택 기준**: 이미 이벤트 루프가 실행 중인 환경에서는 비동기 버전, 동기 환경(스크립트, 테스트)에서는 동기 버전을 사용합니다.

---

## 12. 주요 주의사항 및 Best Practice

### ✅ DO: 올바른 패턴

```python
# 1. 비동기 함수 내에서 await 사용
async def my_node(state):
    result = await some_async_function()
    return {"field": result}

# 2. asyncio.sleep()으로 대기
await asyncio.sleep(2.0)

# 3. 비동기 컨텍스트 매니저로 리소스 관리
async with get_db_client(config) as client:
    result = await client.execute_sql(sql)

# 4. asyncio.wait_for()로 타임아웃 설정
result = await asyncio.wait_for(slow_coroutine(), timeout=30)

# 5. 동기 진입점에서 asyncio.run() 사용
if __name__ == "__main__":
    asyncio.run(main_async())
```

### ❌ DON'T: 잘못된 패턴

```python
# 1. async 함수를 await 없이 호출 (코루틴 객체만 반환, 실행 안 됨)
result = some_async_function()    # ❌ awaitable을 실행하지 않음
result = await some_async_function()   # ✅

# 2. 이벤트 루프 안에서 time.sleep() 사용 (루프 블로킹)
time.sleep(2)      # ❌ 이벤트 루프 전체 정지
await asyncio.sleep(2)   # ✅

# 3. 이벤트 루프 안에서 asyncio.run() 중첩 호출
async def handler():
    asyncio.run(other_async_func())  # ❌ RuntimeError 발생
    await other_async_func()          # ✅

# 4. async def 없이 await 사용
def sync_func():
    result = await async_func()  # ❌ SyntaxError
```

### LangGraph 노드 작성 체크리스트

```python
async def my_node(
    state: AgentState,          # 1. 첫 번째 인자는 항상 state
    *,                           # 2. 이후 인자는 키워드 전용
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:                       # 3. 반환 타입은 dict (업데이트할 필드만)
    # 4. 폴백 초기화
    if app_config is None:
        app_config = load_config()

    # 5. state 읽기 (.get()으로 안전하게)
    value = state.get("some_field", default_value)

    # 6. 비동기 작업
    response = await llm.ainvoke(messages)

    # 7. 반환: 변경할 필드만 포함
    return {
        "current_node": "my_node",
        "result_field": processed_result,
    }
```

---

## 전체 비동기 흐름 요약

```
HTTP 요청 (FastAPI)
    │
    ▼
@asynccontextmanager lifespan
  - await _create_checkpointer_async()    # 비동기 SQLite 연결
  - await cache_mgr.ensure_redis_connected()  # 비동기 Redis 연결
    │
    ▼
라우트 핸들러 (async def)
    │
    ▼
graph.ainvoke(state, config)   # LangGraph 비동기 실행
    │
    ├─ await context_resolver(state)
    ├─ await input_parser(state)
    ├─ await field_mapper(state)
    ├─ await schema_analyzer(state)
    │     └─ async with get_db_client() as client:
    │           await client.execute_sql()
    │           await llm.ainvoke()
    ├─ await query_generator(state)
    │     └─ await llm.ainvoke(messages)
    ├─ await query_validator(state)
    ├─ await query_executor(state)
    │     └─ async with get_db_client() as client:
    │           await asyncio.wait_for(client.execute_sql(), timeout=...)
    ├─ await result_organizer(state)
    └─ await output_generator(state)
          └─ await llm.ainvoke()
    │
    ▼
최종 응답 반환
```

모든 노드는 비동기로 실행되며, I/O 대기 구간에서 이벤트 루프가 다른 요청을 처리할 수 있습니다.
