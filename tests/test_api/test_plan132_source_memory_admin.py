"""plans/132 W5 — 관리자 소스 선택 기억 API(조회·삭제·조직 공용 승격 · G-11 (a))."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import admin
from src.routing.registry import get_registry
from src.schema_cache import source_memory as sm
from tests.test_schema_cache.test_plan132_source_memory import FakeRedis

ADMIN = {"sub": "admin1", "role": "admin"}


def _request(ttl_days: int) -> SimpleNamespace:
    config = SimpleNamespace(router=SimpleNamespace(source_memory_ttl_days=ttl_days))
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


@pytest.fixture
def store(monkeypatch) -> sm.SourceMemoryStore:
    s = sm.SourceMemoryStore(FakeRedis())
    monkeypatch.setattr(sm, "open_store", AsyncMock(return_value=s))
    monkeypatch.setattr(admin, "_audit_admin_action", AsyncMock())
    return s


@pytest.mark.asyncio
async def test_list_off_state_still_shows_seeds(store) -> None:
    out = await admin.list_source_memory(_request(0), _admin=ADMIN)
    assert out["enabled"] is False and out["scopes"] == [] and out["seeds"]


@pytest.mark.asyncio
async def test_list_delete_promote(store) -> None:
    fp = sm.registry_fingerprint(get_registry())
    case = sm.build_case("인스턴스 목록", ["apm"], origin="user_choice", scope="user:u1",
                         registry_fp=fp)
    await store.save(case, ttl_seconds=86400)
    out = await admin.list_source_memory(_request(30), _admin=ADMIN)
    [group] = out["scopes"]
    assert group["scope"] == "user:u1" and group["cases"][0]["source_labels"] == [
        get_registry().system_label("apm")]
    promoted = await admin.promote_source_memory(_request(30), "user:u1", case["case_id"],
                                                 _admin=ADMIN)
    assert promoted["case"]["scope"] == "org" and promoted["case"]["promoted_by"] == "admin1"
    admin._audit_admin_action.assert_awaited()
    await admin.delete_source_memory(_request(30), "user:u1", case["case_id"], _admin=ADMIN)
    assert await store.load("user:u1") == []
    with pytest.raises(HTTPException) as err:
        await admin.delete_source_memory(_request(30), "user:u1", case["case_id"], _admin=ADMIN)
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_guards(store) -> None:
    with pytest.raises(HTTPException) as err:
        await admin.delete_source_memory(_request(30), "../etc", "x", _admin=ADMIN)
    assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        await admin.promote_source_memory(_request(30), "org", "x", _admin=ADMIN)
    assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        await admin.delete_source_memory(_request(0), "user:u1", "x", _admin=ADMIN)
    assert err.value.status_code == 409, "기능 off면 쓰기 작업 없음"
