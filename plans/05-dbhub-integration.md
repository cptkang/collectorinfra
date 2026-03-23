# 05. DBHub MCP 클라이언트 설계

> 기존 `src/dbhub/client.py` + `src/db/client.py` 분석 및 개선 계획

---

## 1. 현재 구현 분석

### 1.1 DBHubClient (src/dbhub/client.py)

- MCP 프로토콜을 통해 DBHub 서버와 통신
- `StdioServerParameters`로 `dbhub` CLI를 subprocess로 실행
- `ClientSession`으로 MCP 세션 관리
- `search_objects`, `get_table_schema`, `get_full_schema`, `get_sample_data`, `execute_sql` 메서드
- `asynccontextmanager`로 `get_dbhub_client()` 제공
- MCP SDK 미설치 시 폴백 모드 (connected=True이지만 MCP 없음)

### 1.2 PostgresClient (src/db/client.py)

- `asyncpg` 기반 직접 PostgreSQL 연결
- `DBHubClient`와 동일한 퍼블릭 메서드 (비공식 인터페이스)
- 커넥션 풀 (min=1, max=5)
- `Decimal -> float` 변환 처리
- `$1` 파라미터 바인딩 사용 (SQL 인젝션 방어)

### 1.3 데이터 모델 (src/dbhub/models.py)

- `ColumnInfo`, `TableInfo`, `SchemaInfo`, `QueryResult` (Pydantic BaseModel)
- `DBHubError`, `ConnectionError`, `QueryTimeoutError`, `QueryExecutionError` 예외 클래스

### 1.4 설정 (config.py)

- `db_backend: Literal["dbhub", "direct"]` -- 현재 기본값은 `"direct"`
- `db_connection_string` -- 직접 연결 시 사용
- `DBHubConfig.config_path`, `DBHubConfig.source_name`

---

## 2. 개선이 필요한 영역

### 2.1 공통 인터페이스 부재

두 클라이언트가 동일한 메서드를 가지고 있지만 공식적인 Protocol/ABC가 없다. `schema_analyzer`와 `query_executor`에서 `_get_client()` 헬퍼를 각각 중복 정의하고 있다.

### 2.2 재연결 로직 부재

`DBHubClient`에서 MCP 프로세스가 예기치 않게 종료되면 재연결 메커니즘이 없다.

### 2.3 헬스체크 효율성

현재 `health_check()`는 `SELECT 1`을 실행한다. 실패 시 에러를 삼키고 `False`를 반환하지만, 타임아웃이 30초로 설정되어 헬스체크가 느릴 수 있다.

### 2.4 PostgresClient의 get_sample_data 취약점

`f"SELECT * FROM {table_name} LIMIT {limit}"` -- table_name이 정규식으로 검증되지 않음. `get_table_schema`에서는 검증하지만 `get_sample_data`에서는 누락.

---

## 3. 공통 인터페이스 설계

```python
# src/db/interface.py

from __future__ import annotations
from typing import Any, Protocol, runtime_checkable

from src.dbhub.models import QueryResult, SchemaInfo, TableInfo


@runtime_checkable
class DBClient(Protocol):
    """DB 클라이언트 공통 인터페이스.

    DBHubClient와 PostgresClient가 모두 이 프로토콜을 만족해야 한다.
    """

    async def connect(self) -> None:
        """DB 연결을 수립한다."""
        ...

    async def disconnect(self) -> None:
        """DB 연결을 종료한다."""
        ...

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
        ...

    async def search_objects(
        self,
        pattern: str = "*",
        object_type: str = "table",
    ) -> list[TableInfo]:
        """DB 객체를 검색한다."""
        ...

    async def get_table_schema(self, table_name: str) -> TableInfo:
        """테이블 상세 스키마를 조회한다."""
        ...

    async def get_full_schema(self) -> SchemaInfo:
        """전체 DB 스키마를 수집한다."""
        ...

    async def get_sample_data(
        self, table_name: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """테이블 샘플 데이터를 조회한다."""
        ...

    async def execute_sql(self, sql: str) -> QueryResult:
        """SQL을 실행하고 결과를 반환한다."""
        ...
```

---

## 4. 통합 팩토리 함수

```python
# src/db/__init__.py

from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from src.config import AppConfig
from src.db.interface import DBClient


@asynccontextmanager
async def get_db_client(config: AppConfig) -> AsyncGenerator[DBClient, None]:
    """설정에 따라 적절한 DB 클라이언트를 생성하고 관리한다.

    Args:
        config: 애플리케이션 설정

    Yields:
        연결된 DB 클라이언트 인스턴스
    """
    if config.db_backend == "direct":
        from src.db.client import PostgresClient
        client = PostgresClient(
            dsn=config.db_connection_string,
            query_timeout=config.query.query_timeout,
            max_rows=config.query.max_rows,
        )
    else:
        from src.dbhub.client import DBHubClient
        client = DBHubClient(config.dbhub, config.query)

    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()
```

이로써 `schema_analyzer`와 `query_executor`의 `_get_client()` 중복을 제거한다:

```python
# src/nodes/schema_analyzer.py (변경)
from src.db import get_db_client

async def schema_analyzer(state: AgentState) -> dict:
    config = load_config()
    async with get_db_client(config) as client:
        # ... 기존 로직 ...
```

---

## 5. DBHubClient 재연결 로직

```python
# src/dbhub/client.py (개선)

class DBHubClient:
    MAX_RECONNECT_ATTEMPTS = 3
    RECONNECT_DELAY = 2.0  # 초

    async def _ensure_connected_with_retry(self) -> None:
        """연결 상태를 확인하고 필요 시 재연결한다."""
        if self._connected and self._mcp_session:
            return

        for attempt in range(self.MAX_RECONNECT_ATTEMPTS):
            try:
                await self.connect()
                return
            except Exception as e:
                if attempt < self.MAX_RECONNECT_ATTEMPTS - 1:
                    delay = self.RECONNECT_DELAY * (attempt + 1)
                    logger.warning(
                        f"DBHub 재연결 시도 {attempt + 1}/{self.MAX_RECONNECT_ATTEMPTS}, "
                        f"{delay}초 후 재시도: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise ConnectionError(
                        f"DBHub 재연결 실패 ({self.MAX_RECONNECT_ATTEMPTS}회 시도): {e}"
                    ) from e

    async def execute_sql(self, sql: str) -> QueryResult:
        """SQL을 실행한다. 연결 끊김 시 재연결을 시도한다."""
        await self._ensure_connected_with_retry()
        # ... 기존 실행 로직 ...
```

---

## 6. 헬스체크 개선

```python
# 공통 개선: 헬스체크 전용 타임아웃 (짧게)
HEALTH_CHECK_TIMEOUT = 5  # 초

async def health_check(self) -> bool:
    """연결 상태를 확인한다. 5초 이내 응답하지 않으면 실패."""
    try:
        await asyncio.wait_for(
            self.execute_sql("SELECT 1"),
            timeout=HEALTH_CHECK_TIMEOUT,
        )
        return True
    except Exception:
        return False
```

---

## 7. PostgresClient 보안 보강

```python
# src/db/client.py (개선)

import re

_VALID_TABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


async def get_sample_data(self, table_name: str, limit: int = 5) -> list[dict]:
    """테이블 샘플 데이터를 안전하게 조회한다."""
    # 테이블명 검증 추가 (기존 get_table_schema와 동일한 검증)
    if not _VALID_TABLE_NAME.match(table_name):
        raise DBHubError(f"유효하지 않은 테이블명: {table_name}")

    result = await self.execute_sql(
        f"SELECT * FROM {table_name} LIMIT {limit}"
    )
    return result.rows
```

---

## 8. 연결 관리 라이프사이클

### 8.1 API 서버 모드

```python
# src/api/server.py (개선)

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    config = load_config()

    # 그래프 빌드
    app.state.graph = build_graph(config)
    app.state.config = config

    # DB 클라이언트를 앱 라이프사이클에서 관리 (선택)
    # 현재는 요청별로 생성/해제하므로 여기서 관리하지 않음

    yield

    logger.info("서버 종료")
```

### 8.2 CLI 모드

```python
# src/main.py (기존 유지)
# CLI 모드에서는 그래프 실행 시 노드 내부에서 요청별 연결/해제
```

---

## 9. DBHub TOML 설정 관리

### 9.1 현재 설정 (유지)

```toml
# dbhub.toml
[[sources]]
name = "infra_db"
type = "postgresql"
connection = "${DB_CONNECTION_STRING}"
readonly = true
query_timeout = 30

[[tools]]
name = "execute_sql"
sources = ["infra_db"]
readonly = true
max_rows = 10000
```

### 9.2 Phase 3: 다중 소스 설정

```toml
# 다중 DB 소스 예시
[[sources]]
name = "infra_db"
type = "postgresql"
connection = "${INFRA_DB_CONNECTION_STRING}"
readonly = true
query_timeout = 30

[[sources]]
name = "monitoring_db"
type = "mysql"
connection = "${MONITORING_DB_CONNECTION_STRING}"
readonly = true
query_timeout = 30

[[tools]]
name = "execute_sql"
sources = ["infra_db", "monitoring_db"]
readonly = true
max_rows = 10000
```

---

## 10. 데이터 모델 개선

### 10.1 ConnectionError 네이밍 충돌

`src/dbhub/models.py`에 정의된 `ConnectionError`가 Python 내장 `ConnectionError`와 이름이 충돌한다. 네이밍을 변경한다:

```python
# src/dbhub/models.py (개선)
class DBConnectionError(DBHubError):
    """DB 연결 실패."""
    pass

# 하위 호환: 기존 이름도 유지
ConnectionError = DBConnectionError  # deprecated alias
```

### 10.2 QueryResult에 메타데이터 추가

```python
class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: Optional[float] = None
    truncated: bool = False
    source_name: Optional[str] = None  # [신규] 어떤 DB 소스에서 실행되었는지
```

---

## 11. 기존 코드 대비 변경 사항 요약

| 항목 | 현재 | 변경 |
|------|------|------|
| 공통 인터페이스 | 없음 (암묵적 동일 메서드) | `src/db/interface.py`에 `DBClient` Protocol 정의 |
| 팩토리 함수 | `_get_client()` 노드별 중복 | `src/db/__init__.py`에 `get_db_client()` 통합 |
| 재연결 | 미구현 | `_ensure_connected_with_retry()` 추가 (3회 재시도) |
| 헬스체크 | 30초 타임아웃 | 5초 전용 타임아웃 |
| get_sample_data | 테이블명 미검증 (PostgresClient) | 정규식 검증 추가 |
| ConnectionError | Python 내장과 네이밍 충돌 | `DBConnectionError`로 변경 |
| QueryResult | source_name 미포함 | source_name 필드 추가 |
