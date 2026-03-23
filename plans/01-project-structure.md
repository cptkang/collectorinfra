# 01. 프로젝트 구조 및 설정 파일

> 기존 코드 기반 보완 계획

---

## 1. 현재 디렉토리 구조

```
collectorinfra/
├── src/
│   ├── __init__.py
│   ├── main.py              # CLI/서버 진입점
│   ├── config.py             # pydantic-settings 설정
│   ├── llm.py                # LLM 팩토리
│   ├── state.py              # AgentState 정의
│   ├── graph.py              # LangGraph 그래프 빌드
│   ├── nodes/                # 7개 노드 구현
│   │   ├── input_parser.py
│   │   ├── schema_analyzer.py
│   │   ├── query_generator.py
│   │   ├── query_validator.py
│   │   ├── query_executor.py
│   │   ├── result_organizer.py
│   │   └── output_generator.py
│   ├── prompts/              # LLM 프롬프트 템플릿
│   │   ├── input_parser.py
│   │   ├── query_generator.py
│   │   ├── result_organizer.py
│   │   └── output_generator.py
│   ├── dbhub/                # DBHub MCP 클라이언트
│   │   ├── client.py
│   │   └── models.py
│   ├── db/                   # PostgreSQL 직접 연결 클라이언트
│   │   └── client.py
│   ├── security/             # 보안 모듈
│   │   ├── sql_guard.py
│   │   ├── data_masker.py
│   │   └── audit_logger.py
│   └── api/                  # FastAPI 서버
│       ├── server.py
│       ├── schemas.py
│       └── routes/
│           ├── health.py
│           └── query.py
├── tests/                    # 테스트
├── docs/
│   └── requirements.md
├── plans/                    # 구현 계획서 (본 디렉토리)
├── dbhub.toml                # DBHub 설정
├── requirements.txt          # pip 의존성
└── spec.md                   # 요건 정의서
```

---

## 2. 목표 디렉토리 구조 (변경/추가 사항)

```
collectorinfra/
├── src/
│   ├── ...기존 파일들...
│   ├── document/             # [신규] Phase 2 - 양식 처리 모듈
│   │   ├── __init__.py
│   │   ├── excel_parser.py   # Excel 양식 파싱
│   │   ├── excel_writer.py   # Excel 파일 생성
│   │   ├── word_parser.py    # Word 양식 파싱
│   │   └── word_writer.py    # Word 파일 생성
│   ├── utils/                # [신규] 공통 유틸리티
│   │   ├── __init__.py
│   │   └── retry.py          # exponential backoff 재시도
│   └── db/
│       ├── __init__.py       # [수정] 클라이언트 팩토리 export (get_db_client)
│       ├── client.py         # 기존 PostgresClient
│       └── interface.py      # [신규] DB 클라이언트 추상 인터페이스 (Protocol)
├── pyproject.toml            # [신규] requirements.txt 대체
├── .env.example              # [신규] 환경변수 템플릿
├── .env                      # [기존 유지] 실제 환경변수 (gitignore 대상)
├── dbhub.toml                # 기존 유지
├── Makefile                  # [신규] 빌드/실행 편의 스크립트
└── alembic/                  # [Phase 3] DB 마이그레이션 (감사 로그 테이블)
```

---

## 3. pyproject.toml 설계

현재 `requirements.txt`를 `pyproject.toml`로 전환한다.

```toml
[project]
name = "collectorinfra"
version = "0.1.0"
description = "인프라 데이터 조회 에이전트 - 자연어로 인프라 DB를 질의하고 문서를 생성하는 AI 에이전트"
requires-python = ">=3.11"
license = {text = "MIT"}

dependencies = [
    # 에이전트 프레임워크
    "langgraph>=0.2.0",
    "langchain-core>=0.3.0",
    "langchain-anthropic",
    "langchain-openai",

    # MCP 클라이언트 (DBHub 모드)
    "mcp",

    # DB 직접 연결 (direct 모드)
    "asyncpg>=0.29.0",

    # 체크포인트
    "langgraph-checkpoint-sqlite",

    # API 서버
    "fastapi>=0.110.0",
    "uvicorn>=0.30.0",

    # 유틸리티
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "python-dotenv",
    "sqlparse>=0.5.0",
    "structlog>=24.0.0",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
document = [
    "openpyxl>=3.1.0",
    "python-docx>=1.0.0",
]
postgres-checkpoint = [
    "langgraph-checkpoint-postgres",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov",
    "ruff>=0.4.0",
    "mypy>=1.10",
]

[project.scripts]
collectorinfra = "src.main:main"

[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
where = ["."]
include = ["src*"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

---

## 4. 환경변수 설계 (.env.example)

```bash
# === LLM 설정 ===
LLM_PROVIDER=anthropic                    # anthropic | openai
LLM_MODEL=claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-xxxx
OPENAI_API_KEY=sk-xxxx                    # openai 사용 시

# === DB 연결 ===
DB_BACKEND=direct                         # dbhub | direct
DB_CONNECTION_STRING=postgresql://infra_user:password@localhost:5433/infra_db

# === DBHub 설정 (db_backend=dbhub 시) ===
DBHUB_CONFIG_PATH=./dbhub.toml
DBHUB_SOURCE_NAME=infra_db

# === 쿼리 제한 ===
QUERY_MAX_RETRY_COUNT=3
QUERY_TIMEOUT=30
QUERY_MAX_ROWS=10000
QUERY_DEFAULT_LIMIT=1000

# === 보안 ===
SECURITY_SENSITIVE_COLUMNS=password,secret,token,api_key,private_key,credential
SECURITY_MASK_PATTERN=***MASKED***

# === 체크포인트 ===
CHECKPOINT_BACKEND=sqlite                 # sqlite | postgres
CHECKPOINT_DB_URL=checkpoints.db

# === API 서버 ===
API_HOST=0.0.0.0
API_PORT=8000
API_CORS_ORIGINS=*                        # 운영: https://your-domain.com
```

---

## 5. DBHub TOML 설정 (현재 상태 유지 + 다중 DB 확장 대비)

현재 `dbhub.toml`은 이미 올바르게 구성되어 있다. Phase 3에서 다중 DB를 지원할 때 소스를 추가한다:

```toml
# 기존 (유지)
[[sources]]
name = "infra_db"
type = "postgresql"
connection = "${DB_CONNECTION_STRING}"
readonly = true
query_timeout = 30

# Phase 3: 다중 DB 소스 예시
# [[sources]]
# name = "monitoring_db"
# type = "mysql"
# connection = "${MONITORING_DB_CONNECTION_STRING}"
# readonly = true
# query_timeout = 30

[[tools]]
name = "execute_sql"
sources = ["infra_db"]
readonly = true
max_rows = 10000
```

---

## 6. Makefile

```makefile
.PHONY: install dev test lint run server format

install:
	pip install -e .

dev:
	pip install -e ".[dev,document]"

test:
	pytest tests/ -v --cov=src --cov-report=term-missing

lint:
	ruff check src/ tests/
	mypy src/

run:
	python -m src.main --query "$(Q)"

server:
	python -m src.main --server

format:
	ruff format src/ tests/
```

---

## 7. DB 클라이언트 추상 인터페이스 (신규)

현재 `DBHubClient`와 `PostgresClient`가 동일한 메서드를 가지고 있지만 공식적인 인터페이스가 없다. Protocol 클래스로 명시한다.

```python
# src/db/interface.py
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable

from src.dbhub.models import QueryResult, SchemaInfo, TableInfo


@runtime_checkable
class DBClient(Protocol):
    """DB 클라이언트 공통 인터페이스."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def health_check(self) -> bool: ...
    async def search_objects(
        self, pattern: str = "*", object_type: str = "table"
    ) -> list[TableInfo]: ...
    async def get_table_schema(self, table_name: str) -> TableInfo: ...
    async def get_full_schema(self) -> SchemaInfo: ...
    async def get_sample_data(
        self, table_name: str, limit: int = 5
    ) -> list[dict[str, Any]]: ...
    async def execute_sql(self, sql: str) -> QueryResult: ...
```

팩토리 함수:

```python
# src/db/__init__.py
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from src.config import AppConfig
from src.db.interface import DBClient


@asynccontextmanager
async def get_db_client(config: AppConfig) -> AsyncGenerator[DBClient, None]:
    """설정에 따라 적절한 DB 클라이언트를 반환한다."""
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

이를 통해 `schema_analyzer`와 `query_executor` 노드에서 `_get_client()` 중복 함수를 제거하고 `get_db_client`를 공통으로 사용한다.

---

## 8. 기존 코드 대비 변경 사항

| 항목 | 현재 | 변경 |
|------|------|------|
| 패키지 관리 | `requirements.txt` | `pyproject.toml`로 전환 |
| 환경변수 | `.env` (템플릿 미제공) | `.env.example` 템플릿 제공 |
| document 모듈 | 미존재 (input_parser에서 ImportError 스텁) | Phase 2에서 `src/document/` 디렉토리 생성 |
| DB 클라이언트 | `_get_client()` 노드별 중복 | `src/db/interface.py` Protocol + `src/db/__init__.py` 팩토리 통합 |
| 의존성 | `sqlparse`, `structlog`, `asyncpg`, `mcp` requirements.txt 누락 | pyproject.toml에 모두 포함 |
| 실행 명령 | `python -m src.main` | `pyproject.toml`의 `[project.scripts]`로 `collectorinfra` 명령 추가 |
| 빌드 스크립트 | 없음 | `Makefile` 추가 |
| 공통 유틸리티 | 없음 | `src/utils/retry.py` (exponential backoff) 추가 |
