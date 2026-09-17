"""plans/104 서비스 테스트 공용 픽스처 — 가짜 MCP 세션 · 목 LLM · 설정 · 레지스트리 · 저장소.

- 가짜 MCP 세션은 실제 `DBHubClient`에 붙인다(`_mcp_session` 교체) — `get_full_schema`·
  `list_sources`·`health_check_detail`·`execute_sql`의 실제 파싱 경로를 그대로 통과한다.
- 목 LLM은 메시지 내용으로 호출 종류(구조 분석·샘플 SQL·컬럼 설명·DB 설명)를 가르고 호출을 센다.
- 이름은 전부 가상(`app_*`·`t_*`) — 운영 스키마 리터럴을 쓰지 않는다.
- 네트워크·실 Redis·실 LLM 0. 파일 쓰기는 tmp 아래뿐이다(작업 디렉터리를 tmp로 바꾼다).

이 모듈에는 테스트가 없다(같은 디렉터리의 `test_plan104_service_*` 테스트가 import한다).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import yaml

from src.config import DBHubConfig, QueryConfig
from src.dbhub.client import DBHubClient
from src.routing.registry import parse_registry
from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.structure_store import StructureStore
from tests.mocks.async_redis import FakeAsyncRedis, attach_fake_redis

REMOTE_ENV = "http://mcp.test:9099/sse"
LOCAL_ENV = "http://localhost:9099/sse"


# ──────────────────────────────────────────────
# 가짜 MCP 세션
# ──────────────────────────────────────────────


@dataclass
class FakeTable:
    """가짜 테이블 — 컬럼 `(이름, 타입, nullable, pk)` · FK `(컬럼, 대상 테이블, 대상 컬럼)`."""

    columns: list[tuple[str, str, bool, bool]]
    fks: list[tuple[str, str, str]] = field(default_factory=list)
    schema: str = "public"


class FakeMCPSession:
    """`mcp.ClientSession.call_tool` 대역 — 도구 호출을 기록하고 JSON 텍스트로 답한다."""

    def __init__(self) -> None:
        self.sources: list[dict[str, Any]] = []
        self.health: dict[str, bool] = {}
        self.tables: dict[str, dict[str, FakeTable]] = {}
        self.sql_rows: Callable[[str, str], list[dict[str, Any]]] = lambda source, sql: []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def add_source(
        self, name: str, engine: str, tables: dict[str, FakeTable], *, healthy: bool = True
    ) -> None:
        self.sources.append({
            "name": name, "type": engine, "readonly": True, "query_timeout": 30, "max_rows": 1000,
        })
        self.health[name] = healthy
        self.tables[name] = tables

    def tool_calls(self, tool: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == tool]

    def executed_sql(self) -> list[str]:
        return [args["sql"] for args in self.tool_calls("execute_sql")]

    @staticmethod
    def _result(payload: Any) -> SimpleNamespace:
        text = json.dumps(payload, ensure_ascii=False)
        return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> SimpleNamespace:
        self.calls.append((tool, dict(arguments)))
        source = arguments.get("source", "")
        if tool == "list_sources":
            return self._result(self.sources)
        if tool == "health_check":
            if self.health.get(source):
                return self._result({"status": "healthy"})
            return self._result({"status": "unhealthy", "message": "연결 거부"})
        if tool == "search_objects":
            return self._result([
                {"name": name, "schema": table.schema}
                for name, table in self.tables.get(source, {}).items()
            ])
        if tool == "get_table_schema":
            table = self.tables[source][arguments["table_name"]]
            return self._result({
                "table_name": arguments["table_name"],
                "columns": [
                    {"column_name": c, "data_type": t, "is_nullable": "YES" if n else "NO",
                     "is_primary_key": pk}
                    for c, t, n, pk in table.columns
                ],
                "foreign_keys": [
                    {"from_column": c, "to_table": tt, "to_column": tc} for c, tt, tc in table.fks
                ],
            })
        if tool == "execute_sql":
            try:
                rows = self.sql_rows(source, arguments["sql"])
            except Exception as e:  # noqa: BLE001 — MCP 서버의 오류 응답 흉내
                return self._result({"error": str(e)})
            return self._result({
                "columns": list(rows[0]) if rows else [], "rows": rows, "row_count": len(rows),
            })
        raise AssertionError(f"예상하지 못한 도구 호출: {tool}")


def client_factory(session: FakeMCPSession, *, env: str = REMOTE_ENV) -> Callable[..., Any]:
    """소스마다 가짜 세션에 붙인 실제 `DBHubClient`를 내주는 팩토리."""

    @asynccontextmanager
    async def _factory(source: str | None) -> AsyncIterator[DBHubClient]:
        client = DBHubClient(
            DBHubConfig(server_url=env, source_name=source or "_probe", mcp_call_timeout=5),
            QueryConfig(),
        )
        client._connected = True
        client._mcp_session = session
        yield client

    return _factory


def failing_client_factory(error: Exception) -> Callable[..., Any]:
    """연결 단계에서 실패하는 팩토리(MCP 미가용)."""

    @asynccontextmanager
    async def _factory(source: str | None) -> AsyncIterator[Any]:
        raise error
        yield  # pragma: no cover

    return _factory


# ──────────────────────────────────────────────
# 목 LLM
# ──────────────────────────────────────────────


class MockLLM:
    """`ainvoke`만 흉내 내는 LLM — 호출 종류별로 응답 함수를 고르고 호출을 기록한다."""

    def __init__(
        self,
        *,
        structure: Callable[[str], str] | None = None,
        samples: Callable[[str], str] | None = None,
        describe: Callable[[str], str] | None = None,
        db_description: str = "가상 업무 데이터를 관리하는 DB",
    ) -> None:
        self.structure = structure or (
            lambda prompt: json.dumps({"patterns": [], "query_guide": ""})
        )
        self.samples = samples or (lambda prompt: "[]")
        self.describe = describe or default_describe
        self.db_description = db_description
        self.calls: list[tuple[str, str]] = []

    def kinds(self, kind: str) -> list[str]:
        return [prompt for k, prompt in self.calls if k == kind]

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        prompt = "\n".join(str(getattr(m, "content", m)) for m in messages)
        if "## 구조 분석 결과" in prompt:
            kind, text = "samples", self.samples(prompt)
        elif "## DB 스키마" in prompt:
            kind, text = "structure", self.structure(prompt)
        elif "## 테이블:" in prompt:
            kind, text = "describe", self.describe(prompt)
        elif "## DB 식별자:" in prompt:
            kind, text = "db_description", self.db_description
        else:  # pragma: no cover — 새 호출 종류가 생기면 테스트가 알아야 한다
            raise AssertionError(f"알 수 없는 LLM 호출: {prompt[:120]}")
        self.calls.append((kind, prompt))
        return SimpleNamespace(content=text)


_TABLE_RE = re.compile(r"## 테이블: (\S+)")
_COLUMN_RE = re.compile(r"^- (\w+):", re.MULTILINE)


def table_in_prompt(prompt: str) -> str:
    match = _TABLE_RE.search(prompt)
    assert match, prompt[:200]
    return match.group(1)


def default_describe(prompt: str) -> str:
    """컬럼 설명 응답 — 프롬프트의 테이블·컬럼마다 설명 1개와 유사어 1개."""
    table = table_in_prompt(prompt)
    columns_block = prompt.split("## 컬럼 정보", 1)[1].split("## 샘플 데이터", 1)[0]
    return json.dumps({
        f"{table}.{col}": {"description": f"{col} 설명", "synonyms": [f"{col}_별칭"]}
        for col in _COLUMN_RE.findall(columns_block)
    }, ensure_ascii=False)


class FakeCtx:
    """`JobContext` 대역 — 진행률 호출을 기록한다."""

    def __init__(self) -> None:
        self.job_id = "job-test"
        self.progress_calls: list[tuple[int, int, str]] = []

    async def progress(self, done: int, total: int, label: str = "") -> None:
        self.progress_calls.append((done, total, label))


# ──────────────────────────────────────────────
# 설정 · 레지스트리 · 매니저 · 저장소
# ──────────────────────────────────────────────


def make_config(
    tmp_path: Path,
    *,
    env: str = REMOTE_ENV,
    active: tuple[str, ...] = (),
    concurrency: int = 2,
    group_max_tables: int = 40,
    value_retrieval: bool = False,
) -> MagicMock:
    """서비스가 읽는 필드만 명시한 설정(`.env` 누수 차단)."""
    config = MagicMock()
    config.db_backend = "dbhub"
    config.dbhub = DBHubConfig(server_url=env, source_name="", mcp_call_timeout=5)
    config.query = QueryConfig()
    config.llm.provider = "mlx"
    config.llm.mlx_model = "fake-local-model"
    config.llm.model = "fake-model"
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    config.schema_cache.fingerprint_ttl_seconds = 1800
    config.schema_cache.admin_llm_concurrency = concurrency
    config.schema_cache.structure_group_max_tables = group_max_tables
    config.multi_db.get_active_db_ids = lambda: list(active)
    config.auth.default_allowed_db_ids = ""
    config.synonym.value_retrieval = value_retrieval
    return config


def make_registry(*entries: dict[str, Any]) -> Any:
    """가상 레지스트리(`parse_registry` 실경로)."""
    return parse_registry({"version": 1, "databases": [dict(e) for e in entries]})


@dataclass
class Env:
    """서비스 테스트 환경 묶음."""

    tmp: Path
    config: MagicMock
    mgr: SchemaCacheManager
    fake_redis: FakeAsyncRedis
    store: StructureStore
    session: FakeMCPSession
    llm: MockLLM
    ctx: FakeCtx
    registry: Any

    @property
    def profiles_dir(self) -> Path:
        return self.store.profiles_dir

    def write_profile(self, db_id: str, profile: dict[str, Any], *, header: str = "") -> Path:
        path = self.profiles_dir / f"{db_id}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
        path.write_text(header + body, encoding="utf-8")
        return path

    def service_kwargs(self) -> dict[str, Any]:
        return {
            "client_factory": client_factory(self.session, env=str(self.config.dbhub.server_url)),
            "llm_factory": lambda: self.llm,
            "registry_getter": lambda: self.registry,
            "store": self.store,
        }


def make_env(tmp_path: Path, monkeypatch: Any, **config_kwargs: Any) -> Env:
    """tmp 작업 디렉터리 · 페이크 Redis 매니저 · 실제 저장소(tmp 경로)."""
    monkeypatch.chdir(tmp_path)
    config = make_config(tmp_path, **config_kwargs)
    mgr = SchemaCacheManager(config)
    fake_redis = attach_fake_redis(mgr._redis_cache)
    store = StructureStore(
        mgr._redis_cache, tmp_path / ".cache" / "structure", tmp_path / "config" / "db_profiles"
    )
    # DB 설명 생성기의 소유 안내는 실제 설정·레지스트리를 읽는다 — 테스트에서는 끈다
    monkeypatch.setattr(
        "src.schema_cache.description_generator._db_description_ownership_guidance",
        lambda db_id: "",
    )
    return Env(
        tmp=tmp_path, config=config, mgr=mgr, fake_redis=fake_redis, store=store,
        session=FakeMCPSession(), llm=MockLLM(), ctx=FakeCtx(), registry=make_registry(),
    )
