"""plans/140 W1 테스트 공용 픽스처 — 결정적 가짜 DB 클라이언트 · 가짜 구조 저장소 · P1 실행기.

- 가짜 클라이언트는 조립 SQL 문자열만 보고 결정적으로 답한다(해시로 값 묶음·겹침을 고른다). 테스트가
  `responder`로 특정 조회(유일성·겹침)의 답을 덮어쓸 수 있다.
- 가짜 저장소는 `run_asset_profile`이 쓰는 메서드만 흉내 낸다(Redis·파일 0).
- 같은 하네스로 기준 커밋(`a3da2b7`)의 자산 해시를 뽑아 비트 동일 테스트에 박았다.

이 모듈에는 테스트가 없다(같은 디렉터리의 `test_plan140_w1_*` 테스트가 import한다).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from src.domain.schema_snapshot import build_snapshot
from src.schema_cache.asset_generation_service import AssetGenerationService
from src.schema_cache.db_structure_service import schema_dict_from_full_schema
from src.schema_cache.ddl_schema_parser import parse_ddl

ROOT = Path(__file__).resolve().parents[2]

VALUE_SETS: list[list[str]] = [
    ["1", "2"],
    ["20260101", "20260215", "20260301"],
    ["web01", "db02.example.com"],
    ["10.0.0.1", "10.0.0.2,10.0.0.3"],
    ["Y", "N"],
    [f"v{i}" for i in range(60)],
]
COMMENTS = ["상태(1:정상, 2:장애)", "등록 일자", "서버 호스트명"]

Responder = Callable[[str], list[dict[str, Any]] | None]


def stable_hash(text: str) -> int:
    """실행마다 같은 정수 해시(파이썬 `hash`는 프로세스마다 다르다)."""
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16)


def snapshot_from_ddl(path: Path, engine: str, default_schema: str | None = None) -> dict:
    """DDL 파일 → 스냅샷(D-292 DDL 등록과 같은 경로)."""
    parsed = parse_ddl(path.read_text(encoding="utf-8"), engine, default_schema=default_schema)
    schema_dict, table_schemas = schema_dict_from_full_schema(parsed.schema)
    return build_snapshot(schema_dict, table_schemas)


def snapshot_from_columns(
    tables: dict[str, list[tuple[str, str, bool]]],
    fks: dict[str, list[tuple[str, str, str]]] | None = None,
) -> dict:
    """``{테이블: [(컬럼, 타입, 기본키)]}`` → 스냅샷(FK는 ``{자식: [(컬럼, 부모, 부모 컬럼)]}``)."""
    schema_dict: dict[str, Any] = {"tables": {}, "relationships": []}
    for table, cols in tables.items():
        schema_dict["tables"][table] = {"columns": [
            {"name": c, "type": t, "nullable": True, "primary_key": pk} for c, t, pk in cols
        ]}
    for child, items in (fks or {}).items():
        for column, parent, parent_column in items:
            schema_dict["relationships"].append({
                "from": f"{child}.{column}", "to": f"{parent}.{parent_column}",
            })
    return build_snapshot(schema_dict)


class FakeClient:
    """조립 SQL에 결정적으로 답하는 읽기 전용 클라이언트(실행 SQL 기록)."""

    def __init__(self, snapshot: dict, responder: Responder | None = None) -> None:
        self.snapshot = snapshot
        self.responder = responder
        self.sql: list[str] = []

    async def execute_sql(self, sql: str) -> Any:
        self.sql.append(sql)
        rows = self.responder(sql) if self.responder else None
        if rows is None:
            rows = self._rows(sql)
        return SimpleNamespace(rows=rows, truncated=False)

    def _rows(self, sql: str) -> list[dict[str, Any]]:
        tables = self.snapshot["tables"]
        if "row_estimate" in sql:
            return [{"table_name": k.rpartition(".")[2], "table_comment": "",
                     "row_estimate": stable_hash(k) % 4} for k in tables]
        if "column_comment" in sql:
            rows = []
            for k, data in tables.items():
                for c in data["columns"]:
                    n = stable_hash(f"{k}.{c}") % 7
                    if n < len(COMMENTS):
                        rows.append({"table_name": k.rpartition(".")[2], "column_name": c,
                                     "column_comment": COMMENTS[n]})
            return rows
        if "AS code_value" in sql:
            return [{"code_value": "1", "code_label": "정상"},
                    {"code_value": "2", "code_label": "장애"}]
        if "AS sampled" in sql:
            return [{"sampled": 10, "matched": 10 if stable_hash(sql) % 2 == 0 else 6}]
        if "AS distinct_count" in sql:
            return [{"non_null": 10, "distinct_count": 10 if stable_hash(sql) % 2 == 0 else 7}]
        if sql.startswith("SELECT DISTINCT"):
            return [{"v": v} for v in VALUE_SETS[stable_hash(sql) % len(VALUE_SETS)]]
        raise AssertionError(f"예상하지 못한 SQL: {sql}")


class FakeStore:
    """`run_asset_profile`이 쓰는 구조 저장소 메서드만 흉내 낸다."""

    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot
        self.drafts: list[dict] = []

        async def _ok() -> bool:
            return True

        self.redis_cache = SimpleNamespace(ensure_connected=_ok)

    async def load_snapshot(self, source: str) -> dict:
        return {"snapshot": self.snapshot, "hash": "h"}

    def read_current_profile(self, source: str) -> None:
        return None

    async def load_ddl_comments(self, source: str) -> dict:
        return {}

    async def add_asset_draft(self, source: str, payload: dict) -> dict:
        self.drafts.append(payload)
        return {"draft_id": "d1", **payload}

    async def add_description_draft(self, source: str, payload: dict) -> dict:
        return {"draft_id": "dd1"}


class Ctx:
    """`JobContext` 대역."""

    async def progress(self, done: int, total: int, label: str = "") -> None:
        return None


async def profile(
    snapshot: dict,
    engine: str,
    db_schema: str = "",
    *,
    responder: Responder | None = None,
    offline: bool = False,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    """가짜 클라이언트로 P1을 돌려 ``{"draft", "result", "sql", "client"}``를 돌려준다."""
    store = FakeStore(snapshot)
    client = FakeClient(snapshot, responder)

    @asynccontextmanager
    async def factory(source: str | None) -> AsyncIterator[FakeClient]:
        if offline:
            raise ConnectionError("연결 거부")
        yield client

    registry = SimpleNamespace(get=lambda s: SimpleNamespace(engine=engine, db_schema=db_schema))
    config = SimpleNamespace(dbhub=SimpleNamespace(server_url="http://mcp.test:9099/sse"))
    service = AssetGenerationService(
        config, MagicMock(), client_factory=factory, registry_getter=lambda: registry,
        store=store, asset_store=MagicMock(),
    )
    result = await service.run_asset_profile("src_x", tables=tables, by="t", ctx=Ctx())
    return {"draft": store.drafts[-1], "result": result, "sql": client.sql, "client": client}


def digest(value: Any) -> str:
    """정렬 JSON의 SHA-256."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
