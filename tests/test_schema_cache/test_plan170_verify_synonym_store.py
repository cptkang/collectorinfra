"""plans/170 트랙 B(I1-c·I1-d) 독립 검증 — `RedisSynonymStore` Composition seam.

구현자와 독립으로 다음을 단언한다(실 Redis 없음 — fakeredis 대역만).

- 하위 저장소는 소유자 참조 하나만 들고, 연결 객체·연결 상태·키 상수·``_key``·
  ``_governance_enabled``·``_ensure_meta``·``_merge_llm_words``·유사어 메서드끼리의 호출을
  **호출 시점에 소유자를 거쳐** 읽는다 — 인스턴스 대입·``patch.object(RedisSchemaCache, ...)``가
  그대로 가로챈다.
- `RedisSchemaCache`의 유사어 메서드 27개는 같은 이름의 하위 메서드로 넘기는 순수 위임이다.
- 쓰기·읽기가 같은 전역 키(``GLOBAL_SYNONYMS_KEY``)를 쓴다 — 위임 docstring 때문에 공허해진
  `tests/test_semantic_routing/test_synonym_set_routing.py::TestConsumptionChain::
  test_write_and_read_share_the_same_redis_key`를 하위 저장소 소스·실동작으로 보강한다.
- 키 규칙 `schema_key`가 두 `_key`의 단일 출처다 · 로거 이름 불변 · 순환 import 없음.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache import redis_synonyms as store_mod
from src.schema_cache.redis_cache import RedisSchemaCache, schema_key
from src.schema_cache.redis_synonyms import RedisSynonymStore
from src.schema_cache.structure_store import StructureStore
from tests.mocks.async_redis import attach_fake_redis

ROOT = Path(__file__).resolve().parents[2]
_LOGGER = "src.schema_cache.redis_cache"

# 유사어 키 공간 공개·내부 메서드 — `RedisSchemaCache`에 위임으로 남아야 하는 이름(27 + 내부 2).
_PUBLIC_SYNONYM_METHODS = (
    "save_synonyms", "load_synonyms", "load_synonyms_with_sources", "add_synonyms",
    "remove_synonyms", "load_synonym_meta", "increment_synonym_usage", "prune_stale_synonyms",
    "save_global_synonyms", "load_global_synonyms", "load_global_synonyms_full",
    "add_global_synonym", "update_global_description", "get_global_description",
    "list_global_column_names", "remove_global_synonym", "delete_synonyms",
    "delete_global_synonyms", "save_resource_type_synonyms", "load_resource_type_synonyms",
    "save_eav_name_synonyms", "load_eav_name_synonyms", "save_column_value_synonyms",
    "load_column_value_synonyms", "sync_known_attributes_to_eav_synonyms",
)
_INTERNAL_SYNONYM_METHODS = ("_ensure_meta", "_merge_llm_words")


def _cache() -> RedisSchemaCache:
    return RedisSchemaCache(redis_config=MagicMock(), schema_cache_config=None)


@pytest.fixture
def gov_off():
    with patch.object(RedisSchemaCache, "_governance_enabled", return_value=False) as p:
        yield p


@pytest.fixture
def gov_on():
    with patch.object(RedisSchemaCache, "_governance_enabled", return_value=True) as p:
        yield p


# ---------------------------------------------------------------------------
# 1. 생성 시 캡처 없음 · 호출 시점 소유자 읽기
# ---------------------------------------------------------------------------


def test_store_holds_only_owner_reference():
    cache = _cache()
    assert isinstance(cache._synonyms, RedisSynonymStore)
    assert vars(cache._synonyms) == {"_owner": cache}
    assert cache._synonyms._owner is cache


async def test_redis_attached_after_construction_is_used(gov_off):
    """fakeredis를 생성 뒤에 붙여도 하위 저장소가 그 연결을 쓴다(호출 시점 읽기)."""
    cache = _cache()
    assert await cache.save_global_synonyms({"h": ["a"]}) is False  # 미연결
    fake = attach_fake_redis(cache)
    assert await cache.save_global_synonyms({"h": ["a"]}) is True
    assert json.loads(fake.dump()[RedisSchemaCache.GLOBAL_SYNONYMS_KEY]["h"]) == {"words": ["a"]}
    # 연결 객체 교체도 즉시 반영
    fake2 = attach_fake_redis(cache)
    assert await cache.load_global_synonyms() == {}
    assert fake2.dump() == {}
    # 연결 상태를 내리면 즉시 미연결 반환값
    cache._connected = False
    assert await cache.load_global_synonyms() == {}
    assert await cache.add_global_synonym("h", ["b"]) is False


async def test_instance_key_constant_override_is_read_at_call_time(gov_off):
    cache = _cache()
    fake = attach_fake_redis(cache)
    cache.GLOBAL_SYNONYMS_KEY = "verify:glob"
    cache.EAV_NAME_SYNONYMS_KEY = "verify:eav"
    await cache.add_global_synonym("col", ["w"])
    await cache.save_eav_name_synonyms({"E": ["e"]})
    keys = set(fake.dump())
    assert keys == {"verify:glob", "verify:eav"}


async def test_class_patch_of_key_builder_intercepts_db_scoped_methods(gov_off):
    cache = _cache()
    fake = attach_fake_redis(cache)
    with patch.object(RedisSchemaCache, "_key", lambda self, db_id, suffix: f"X:{db_id}:{suffix}"):
        await cache.save_synonyms("d1", {"t.c": ["a"]}, source="operator")
        assert await cache.load_synonyms("d1") == {"t.c": ["a"]}
    assert set(fake.dump()) == {"X:d1:synonyms"}


async def test_instance_key_builder_assignment_intercepts(gov_off):
    cache = _cache()
    fake = attach_fake_redis(cache)
    cache._key = lambda db_id, suffix: f"I:{db_id}:{suffix}"  # type: ignore[method-assign]
    await cache.add_synonyms("d1", "t.c", ["a"])
    assert set(fake.dump()) == {"I:d1:synonyms"}


async def test_governance_class_patch_controls_meta(gov_on):
    cache = _cache()
    fake = attach_fake_redis(cache)
    await cache.save_synonyms("d", {"t.c": ["x"]}, source="operator")
    await cache.add_synonyms("d", "t.e", ["y"])
    s = json.loads(fake.dump()["schema:d:synonyms"]["t.c"])
    e = json.loads(fake.dump()["schema:d:synonyms"]["t.e"])
    assert set(s["meta"]) == {"x"}
    assert set(e["meta"]) == {"y"}
    assert gov_on.call_count == 2


async def test_governance_off_writes_no_meta(gov_off):
    cache = _cache()
    fake = attach_fake_redis(cache)
    await cache.save_global_synonyms({"g": ["a"]})
    await cache.save_synonyms("d", {"t.c": ["x"]}, source="operator")
    assert json.loads(fake.dump()[RedisSchemaCache.GLOBAL_SYNONYMS_KEY]["g"]) == {"words": ["a"]}
    assert "meta" not in json.loads(fake.dump()["schema:d:synonyms"]["t.c"])


async def test_governance_instance_assignment_intercepts():
    cache = _cache()
    fake = attach_fake_redis(cache)
    cache._governance_enabled = lambda: True  # type: ignore[method-assign]
    await cache.save_synonyms("d", {"t.c": ["x"]}, source="operator")
    assert "meta" in json.loads(fake.dump()["schema:d:synonyms"]["t.c"])


async def test_ensure_meta_class_patch_intercepts(gov_on):
    cache = _cache()
    attach_fake_redis(cache)
    with patch.object(RedisSchemaCache, "_ensure_meta", autospec=True) as m:
        await cache.save_synonyms("d", {"t.c": ["x"]}, source="operator")
    assert m.call_count == 1
    assert m.call_args.args[0] is cache


async def test_merge_llm_words_class_patch_intercepts(gov_off):
    cache = _cache()
    attach_fake_redis(cache)
    sentinel = {"words": ["patched"], "sources": {"patched": "llm"}}
    with patch.object(RedisSchemaCache, "_merge_llm_words", return_value=sentinel) as m:
        await cache.save_synonyms("d", {"t.c": ["x"]}, source="llm")
    assert m.call_count == 1
    assert await cache.load_synonyms("d") == {"t.c": ["patched"]}


async def test_inter_method_calls_go_through_owner_instance(gov_off):
    """`sync_known_attributes_to_eav_synonyms`가 부르는 load/save를 인스턴스 대입이 가로챈다."""
    cache = _cache()
    fake = attach_fake_redis(cache)
    cache.load_eav_name_synonyms = AsyncMock(return_value={"E1": ["old"]})  # type: ignore[method-assign]
    cache.save_eav_name_synonyms = AsyncMock(return_value=True)  # type: ignore[method-assign]
    n = await cache.sync_known_attributes_to_eav_synonyms([{"name": "E1", "synonyms": ["new"]}])
    assert n == 1
    cache.load_eav_name_synonyms.assert_awaited_once_with()
    cache.save_eav_name_synonyms.assert_awaited_once_with({"E1": ["old", "new"]})
    assert fake.dump() == {}  # 실 저장은 가로채져 일어나지 않았다


async def test_inter_method_calls_go_through_owner_class_patch(gov_off):
    cache = _cache()
    attach_fake_redis(cache)
    with patch.object(RedisSchemaCache, "load_eav_name_synonyms",
                      AsyncMock(return_value={"E9": ["z"]})) as load_p, \
         patch.object(RedisSchemaCache, "save_eav_name_synonyms",
                      AsyncMock(return_value=True)) as save_p:
        assert await cache.sync_known_attributes_to_eav_synonyms(
            [{"name": "E9", "synonyms": ["y"]}]
        ) == 1
    load_p.assert_awaited_once()
    save_p.assert_awaited_once_with({"E9": ["z", "y"]})


async def test_owner_method_instance_mock_intercepts_public_call():
    """호출부가 소유자 공개 메서드를 인스턴스 대입으로 목 처리하면 하위 저장소에 닿지 않는다."""
    cache = _cache()
    cache._synonyms = MagicMock()  # 닿으면 AsyncMock이 아니라 await에서 실패한다
    cache.load_eav_name_synonyms = AsyncMock(return_value={"A": ["b"]})  # type: ignore[method-assign]
    assert await cache.load_eav_name_synonyms() == {"A": ["b"]}
    cache._synonyms.load_eav_name_synonyms.assert_not_called()


# ---------------------------------------------------------------------------
# 2. 순수 위임 — 27개 공개 + 2개 내부
# ---------------------------------------------------------------------------


def _body_without_docstring(fn: ast.AST) -> list[ast.stmt]:
    body = list(fn.body)  # type: ignore[attr-defined]
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _class_methods(cls: type) -> dict[str, ast.AST]:
    tree = ast.parse(inspect.getsource(inspect.getmodule(cls)))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls.__name__:
            return {
                n.name: n for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(cls)


@pytest.mark.parametrize("name", _PUBLIC_SYNONYM_METHODS + _INTERNAL_SYNONYM_METHODS)
def test_owner_method_is_single_statement_delegation(name):
    owner_methods = _class_methods(RedisSchemaCache)
    store_methods = _class_methods(RedisSynonymStore)
    assert name in store_methods, name
    fn = owner_methods[name]
    body = _body_without_docstring(fn)
    assert len(body) == 1, f"{name}: 위임 외 로직이 있다"
    stmt = body[0]
    call = stmt.value if isinstance(stmt, (ast.Return, ast.Expr)) else None
    if isinstance(call, ast.Await):
        call = call.value
    assert isinstance(call, ast.Call), name
    target = ast.unparse(call.func)
    assert target in (f"self._synonyms.{name}", f"RedisSynonymStore.{name}"), target
    # 시그니처(인자 이름·기본값·종류) 동일 — 타입 주석은 제외
    o_args = [a.arg for a in fn.args.args]  # type: ignore[attr-defined]
    s_args = [a.arg for a in store_methods[name].args.args]  # type: ignore[attr-defined]
    assert o_args == s_args, name
    o_def = [ast.unparse(d) for d in fn.args.defaults + fn.args.kw_defaults if d]  # type: ignore[attr-defined]
    s_def = [ast.unparse(d) for d in store_methods[name].args.defaults  # type: ignore[attr-defined]
             + store_methods[name].args.kw_defaults if d]  # type: ignore[attr-defined]
    assert o_def == s_def, name
    # 호출 인자가 매개변수를 이름 그대로 넘긴다(순서·키워드 보존)
    passed = [ast.unparse(a) for a in call.args] + [
        f"{k.arg}={ast.unparse(k.value)}" for k in call.keywords
    ]
    params = [a for a in o_args if a != "self"] + [
        a.arg for a in fn.args.kwonlyargs  # type: ignore[attr-defined]
    ]
    assert [p.split("=")[-1] for p in passed] == params, (name, passed, params)


@pytest.mark.parametrize("name", _PUBLIC_SYNONYM_METHODS)
def test_delegation_kind_matches(name):
    assert inspect.iscoroutinefunction(getattr(RedisSchemaCache, name))
    assert inspect.iscoroutinefunction(getattr(RedisSynonymStore, name))


def test_store_bodies_reach_owner_state_only_via_owner():
    """하위 저장소 본문은 ``self._owner`` 외의 자기 속성을 읽지 않는다(캡처 상태 0)."""
    tree = ast.parse(inspect.getsource(store_mod))
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and node.attr != "_owner"):
            offenders.append(f"{node.lineno}: self.{node.attr}")
    assert offenders == []


def test_store_module_has_no_src_imports():
    """순환 import 위험 0 — 하위 저장소는 소유자 모듈을 import하지 않는다."""
    tree = ast.parse(inspect.getsource(store_mod))
    mods = [
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    ] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in mods if m.startswith("src")], mods


@pytest.mark.parametrize("first", [
    "src.schema_cache.redis_synonyms",
    "src.schema_cache.structure_store",
    "src.schema_cache.redis_cache",
    "src.schema_cache.cache_manager",
])
def test_fresh_interpreter_import_order(first):
    code = (
        f"import {first}\n"
        "import src.schema_cache.redis_cache as rc\n"
        "import src.schema_cache.redis_synonyms as rs\n"
        "assert rc.RedisSynonymStore is rs.RedisSynonymStore\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


# ---------------------------------------------------------------------------
# 3. 쓰기·읽기 같은 전역 키 (공허해진 소스 가드 보강)
# ---------------------------------------------------------------------------


def test_store_writer_and_reader_reference_global_key_in_source():
    writer = inspect.getsource(RedisSynonymStore.add_global_synonym)
    reader = inspect.getsource(RedisSynonymStore.load_global_synonyms)
    full = inspect.getsource(RedisSynonymStore.load_global_synonyms_full)
    for src in (writer, reader, full):
        # docstring이 아니라 코드 경로에서 키를 쓴다
        assert "owner.GLOBAL_SYNONYMS_KEY" in src


async def test_registered_global_word_round_trips_on_same_key(gov_off):
    cache = _cache()
    fake = attach_fake_redis(cache)
    assert await cache.add_global_synonym("hostname", ["서버명"]) is True
    assert set(fake.dump()) == {RedisSchemaCache.GLOBAL_SYNONYMS_KEY}
    assert await cache.load_global_synonyms() == {"hostname": ["서버명"]}
    assert (await cache.load_global_synonyms_full())["hostname"]["words"] == ["서버명"]


# ---------------------------------------------------------------------------
# 4. 로거 이름 · 키 규칙 단일 출처
# ---------------------------------------------------------------------------


async def test_moved_code_logs_under_original_logger(gov_off, caplog):
    cache = _cache()
    fake = attach_fake_redis(cache)

    async def _boom(*_a, **_k):
        raise RuntimeError("down")

    fake.hgetall = _boom  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING):
        assert await cache.load_synonyms("d") == {}
    recs = [r for r in caplog.records if "유사 단어 로드 실패" in r.getMessage()]
    assert len(recs) == 1
    assert recs[0].name == _LOGGER
    assert store_mod.logger.name == _LOGGER


def test_schema_key_single_source():
    assert schema_key("db", "tables") == "schema:db:tables"
    assert _cache()._key("db", "tables") == "schema:db:tables"
    assert StructureStore._key("db", "structure_meta") == "schema:db:structure_meta"


def test_both_key_builders_delegate_to_schema_key():
    with patch("src.schema_cache.structure_store.schema_key", return_value="S") as s:
        assert StructureStore._key("a", "b") == "S"
    s.assert_called_once_with("a", "b")
    with patch("src.schema_cache.redis_cache.schema_key", return_value="R") as r:
        assert _cache()._key("a", "b") == "R"
    r.assert_called_once_with("a", "b")
