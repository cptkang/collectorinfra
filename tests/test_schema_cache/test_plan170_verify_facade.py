"""plans/170 트랙 A(I1-a·I1-b) 독립 검증 — `SchemaCacheManager` Facade.

- ``_prefer_redis()`` 치환이 원 식(``backend == "redis" and await ensure_redis_connected()``)과
  같은 단락 평가·호출 횟수를 갖는지 — 합친 분기(`generate_global_synonyms` seed 폴백)와 부정형
  (`_seed_knowledge_descriptions`)을 진리표로 고정한다. 앞 절(``_TRUTH``)은 공개 동작만 보므로
  기준 SHA 트리에서도 그대로 통과해야 한다(검증자가 기준 worktree에서 대조 실행).
- 새 공개 위임(``redis_cache``·``load_resource_type_synonyms``·``load_eav_name_synonyms``·
  ``load_global_synonyms``)이 연결 확인·예외 처리 없는 순수 위임이고 ``_redis_cache``를 호출 시점에
  읽는지 · ``get_global_synonyms``(연결 확인 + YAML 폴백)와 구분되는지.
- 멀티 경로 재료 조회(`_load_schema_prompt_materials`)가 진짜 매니저 + fakeredis에서 새 공개
  메서드로 resource_type·EAV 유사어를 싣는지.

테스트 config는 MagicMock에 검증 대상 필드만 명시한다(`.env` 누수 차단).
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.cache_manager import SchemaCacheManager
from tests.mocks.async_redis import attach_fake_redis


def _mgr(tmp_path: Path, backend: str) -> SchemaCacheManager:
    config = MagicMock()
    config.schema_cache.backend = backend
    config.schema_cache.cache_dir = str(tmp_path / "cache")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    mgr._knowledge_root = tmp_path / "knowledge"
    return mgr


def _llm_raising() -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
    return llm


# ---------------------------------------------------------------------------
# 1. 단락 평가 진리표 — 공개 동작만(기준 트리 대조 가능)
# ---------------------------------------------------------------------------

_TRUTH = [
    # backend, connected, seed, ensure 호출 수, 저장 호출 수, 반환
    ("file", True, ["s"], 0, 0, {"words": ["s"], "description": ""}),
    ("file", False, [], 0, 0, {"words": [], "description": ""}),
    ("redis", True, ["s"], 1, 1, {"words": ["s"]}),
    ("redis", False, ["s"], 1, 0, {"words": ["s"], "description": ""}),
    ("redis", True, [], 0, 0, {"words": [], "description": ""}),
    ("redis", True, None, 0, 0, {"words": [], "description": ""}),
]


@pytest.mark.parametrize(("backend", "connected", "seed", "n_ensure", "n_save", "expected"), _TRUTH)
async def test_seed_fallback_short_circuit(
    tmp_path, backend, connected, seed, n_ensure, n_save, expected
):
    """`seed_words and _prefer_redis()` — seed가 비면 연결 확인조차 하지 않는다(원 식과 동일)."""
    mgr = _mgr(tmp_path, backend)
    ensure = AsyncMock(return_value=connected)
    mgr.ensure_redis_connected = ensure  # type: ignore[method-assign]
    if mgr._redis_cache is not None:
        mgr._redis_cache.save_global_synonyms = AsyncMock(return_value=True)  # type: ignore[method-assign]
    result = await mgr.generate_global_synonyms("col", _llm_raising(), seed_words=seed)
    assert result == expected
    assert ensure.await_count == n_ensure
    if mgr._redis_cache is not None:
        assert mgr._redis_cache.save_global_synonyms.await_count == n_save
        if n_save:
            mgr._redis_cache.save_global_synonyms.assert_awaited_once_with(
                {"col": {"words": ["s"]}}
            )


@pytest.mark.parametrize(("backend", "connected", "n_ensure", "done", "warned"), [
    ("file", True, 0, True, True),
    ("redis", False, 1, False, True),
])
async def test_knowledge_seed_negative_branch(
    tmp_path, caplog, backend, connected, n_ensure, done, warned
):
    """부정형 분기 — 파일 백엔드는 연결 확인 없이 끝냄 · Redis 미연결은 다음 로드에 다시."""
    mgr = _mgr(tmp_path, backend)
    kdir = tmp_path / "knowledge" / "kdb"
    kdir.mkdir(parents=True)
    (kdir / "column_descriptions.yaml").write_text("t:\n  c: 설명\n", encoding="utf-8")
    ensure = AsyncMock(return_value=connected)
    mgr.ensure_redis_connected = ensure  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="src.schema_cache.cache_manager"):
        out = await mgr._seed_knowledge_descriptions("kdb", {"tables": {}}, {"a": "b"})
    assert out == {"a": "b"}
    assert ensure.await_count == n_ensure
    assert ("kdb" in mgr._knowledge_done) is done
    msgs = [r.getMessage() for r in caplog.records if "설명 정본 파일 적재 보류" in r.getMessage()]
    assert len(msgs) == (1 if warned else 0)


@pytest.mark.parametrize("backend", ["file", "redis"])
async def test_simple_branch_calls_ensure_only_for_redis(tmp_path, backend):
    mgr = _mgr(tmp_path, backend)
    ensure = AsyncMock(return_value=False)
    mgr.ensure_redis_connected = ensure  # type: ignore[method-assign]
    assert await mgr.list_global_column_names() == []
    assert await mgr.save_global_synonyms({"a": ["b"]}) is False
    assert ensure.await_count == (2 if backend == "redis" else 0)


async def test_db_description_origin_branch_unchanged(tmp_path):
    """미치환 1곳(`get_db_description_origin`) — `_redis_cache is None`이면 연결 확인 안 함."""
    mgr = _mgr(tmp_path, "redis")
    mgr._redis_cache = None
    ensure = AsyncMock(return_value=True)
    mgr.ensure_redis_connected = ensure  # type: ignore[method-assign]
    assert await mgr.get_db_description_origin("x") is None
    assert ensure.await_count == 0


# ---------------------------------------------------------------------------
# 2. 새 공개 위임 — 순수 위임 · 호출 시점 읽기 · 이름 구분
# ---------------------------------------------------------------------------

_DELEGATES = ["load_resource_type_synonyms", "load_eav_name_synonyms", "load_global_synonyms"]


@pytest.mark.parametrize("name", _DELEGATES)
async def test_delegate_reads_redis_cache_at_call_time_without_connect(tmp_path, name):
    mgr = _mgr(tmp_path, "redis")
    ensure = AsyncMock(return_value=True)
    mgr.ensure_redis_connected = ensure  # type: ignore[method-assign]
    sentinel = {"k": ["v"]}
    replacement = MagicMock()
    setattr(replacement, name, AsyncMock(return_value=sentinel))
    mgr._redis_cache = replacement  # 생성 뒤 대입(테스트 seam)
    assert await getattr(mgr, name)() is sentinel
    getattr(replacement, name).assert_awaited_once_with()
    assert ensure.await_count == 0


@pytest.mark.parametrize("name", _DELEGATES)
async def test_delegate_propagates_exceptions(tmp_path, name):
    mgr = _mgr(tmp_path, "redis")
    replacement = MagicMock()
    setattr(replacement, name, AsyncMock(side_effect=ConnectionError("boom")))
    mgr._redis_cache = replacement
    with pytest.raises(ConnectionError):
        await getattr(mgr, name)()


@pytest.mark.parametrize("name", _DELEGATES)
async def test_delegate_on_file_backend_raises_like_former_direct_access(tmp_path, name):
    """파일 백엔드(``_redis_cache is None``) — 종전 직접 접근과 같은 AttributeError."""
    mgr = _mgr(tmp_path, "file")
    with pytest.raises(AttributeError) as new_exc:
        await getattr(mgr, name)()
    with pytest.raises(AttributeError) as old_exc:
        await getattr(mgr._redis_cache, name)()
    assert str(new_exc.value) == str(old_exc.value)


async def test_redis_cache_property_is_identity_and_read_only(tmp_path):
    mgr = _mgr(tmp_path, "redis")
    assert mgr.redis_cache is mgr._redis_cache
    other = MagicMock()
    mgr._redis_cache = other
    assert mgr.redis_cache is other
    with pytest.raises(AttributeError):
        mgr.redis_cache = MagicMock()  # type: ignore[misc]
    assert _mgr(tmp_path, "file").redis_cache is None


async def test_load_global_vs_get_global_synonyms_differ_on_empty_redis(tmp_path, monkeypatch):
    """`load_global_synonyms`(순수 위임)는 빈 Redis에 `{}` · `get_global_synonyms`는 YAML 폴백."""
    mgr = _mgr(tmp_path, "redis")
    attach_fake_redis(mgr._redis_cache)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "global_synonyms.yaml").write_text(
        "columns:\n  hostname:\n    words: [서버명]\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    assert await mgr.load_global_synonyms() == {}
    assert await mgr.get_global_synonyms() == {"hostname": ["서버명"]}


async def test_multi_db_materials_use_public_delegates(tmp_path):
    """멀티 경로 재료 조회 — 진짜 매니저 + fakeredis에서 resource_type·EAV 유사어가 실린다."""
    from src.nodes import multi_db_executor as mde

    mgr = _mgr(tmp_path, "redis")
    attach_fake_redis(mgr._redis_cache)
    mgr._redis_available = True
    rc = mgr._redis_cache
    with patch.object(type(rc), "_governance_enabled", return_value=False):
        await rc.save_resource_type_synonyms({"server.Cpu": ["씨피유"]})
        await rc.save_eav_name_synonyms({"OSType": ["운영체제"]})
        spy_rt = AsyncMock(wraps=mgr.load_resource_type_synonyms)
        spy_eav = AsyncMock(wraps=mgr.load_eav_name_synonyms)
        mgr.load_resource_type_synonyms = spy_rt  # type: ignore[method-assign]
        mgr.load_eav_name_synonyms = spy_eav  # type: ignore[method-assign]
        with patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mgr):
            materials = await mde._load_schema_prompt_materials("dbz", MagicMock())
    assert materials["resource_type_synonyms"] == {"server.Cpu": ["씨피유"]}
    assert materials["eav_name_synonyms"] == {"OSType": ["운영체제"]}
    spy_rt.assert_awaited_once()
    spy_eav.assert_awaited_once()


async def test_multi_db_materials_skip_when_redis_unavailable(tmp_path):
    from src.nodes import multi_db_executor as mde

    mgr = _mgr(tmp_path, "redis")
    attach_fake_redis(mgr._redis_cache)
    mgr._redis_available = False
    mgr.ensure_redis_connected = AsyncMock(return_value=False)  # type: ignore[method-assign]
    mgr.load_resource_type_synonyms = AsyncMock()  # type: ignore[method-assign]
    mgr.load_eav_name_synonyms = AsyncMock()  # type: ignore[method-assign]
    with patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mgr):
        materials = await mde._load_schema_prompt_materials("dbz", MagicMock())
    assert materials["resource_type_synonyms"] == {}
    mgr.load_resource_type_synonyms.assert_not_awaited()
    mgr.load_eav_name_synonyms.assert_not_awaited()


def test_facade_bypass_residue_is_documented_seams_only():
    """I1-a 잔여 — 노드·문서 계층의 ``_redis_cache`` 직접 접근은 seam 주석이 붙은 3곳뿐."""
    root = Path(__file__).resolve().parents[2]
    expected = {
        "src/nodes/field_mapper.py": 1,
        "src/document/excel_csv_converter.py": 2,
        "src/nodes/schema_analyzer.py": 0,
        "src/nodes/multi_db_executor.py": 0,
    }
    for rel, n in expected.items():
        text = (root / rel).read_text(encoding="utf-8")
        assert text.count("._redis_cache.") == n, rel
        if n:
            assert "seam(D-337 ②)" in text, rel
