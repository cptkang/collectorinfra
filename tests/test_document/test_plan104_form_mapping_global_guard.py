"""plans/104 B-8 ③ · G-11 (b) — 양식 LLM 매핑 자동 등록의 전역 쓰기 가드와 DB별 등록.

`_register_llm_mappings_to_redis`(Step 3)·`_register_llm_synonym_discoveries_to_redis`(Step 2.8)는
전역 사전(`synonyms:global`·EAV 속성명 유사어)에 쓴다. 대상 DB에 구조 정보(수동 프로필·승인본)가
없으면 전역 대신 그 DB의 유사어 캐시(`schema:{db_id}:synonyms`)에 출처 `llm`으로 등록하고
INFO 1줄을 남긴다. EAV 매핑은 구조 정보가 있는 DB에서만 성립하므로 등록하지 않고 WARNING 1줄.
판정 자체가 실패하면 전역에는 쓰지 않고 DB별로 처리한다.
사용자 명시 경로(`apply_mapping_feedback_to_redis`)는 불변이다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.document.field_mapper import (
    MappingResult,
    _apply_llm_synonym_discovery,
    _register_llm_mappings_to_redis,
    _register_llm_synonym_discoveries_to_redis,
    apply_mapping_feedback_to_redis,
)

MANUAL_DB = "db_manual"  # 수동 프로필 또는 승인본이 있는 DB
NEW_DB = "db_new"  # 구조 정보가 없는 DB


def _cache_manager(authorized: set[str]) -> MagicMock:
    """`has_structure_authority`가 `authorized`에 든 db_id만 참인 캐시 매니저 목."""
    cm = MagicMock()
    cm.redis_available = True
    cm.add_synonyms = AsyncMock(return_value=True)
    cm.add_global_synonym = AsyncMock(return_value=True)
    cm.has_structure_authority = AsyncMock(side_effect=lambda db_id: db_id in authorized)

    redis_cache = MagicMock()
    redis_cache.load_eav_name_synonyms = AsyncMock(return_value={})
    redis_cache.save_eav_name_synonyms = AsyncMock(return_value=None)
    redis_cache.add_global_synonym = AsyncMock(return_value=True)
    cm._redis_cache = redis_cache
    return cm


def _assert_no_global_writes(cm: MagicMock) -> None:
    cm.add_global_synonym.assert_not_called()
    cm._redis_cache.add_global_synonym.assert_not_called()
    cm._redis_cache.save_eav_name_synonyms.assert_not_called()


def _lines(caplog: pytest.LogCaptureFixture, level: int, marker: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == level and marker in r.getMessage()
    ]


def _db_scope_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return _lines(caplog, logging.INFO, "양식 LLM 매핑 DB별 등록:")


def _eav_skipped_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return _lines(caplog, logging.WARNING, "양식 LLM 매핑 EAV 등록 건너뜀")


def _db_scope_line(
    db_id: str, field: str, column: str, reason: str = "수동 프로필·승인본 없음"
) -> str:
    return (
        f"양식 LLM 매핑 DB별 등록: db_id={db_id}, field={field}, column={column} "
        f"({reason} — 전역 대신 DB별)"
    )


def _eav_skipped_line(db_id: str, field: str, column: str) -> str:
    return (
        f"양식 LLM 매핑 EAV 등록 건너뜀: db_id={db_id}, field={field}, column={column} "
        "(수동 프로필·승인본 없음 — "
        "EAV 매핑은 구조 정보가 있는 DB에서만 성립해 DB별로도 등록하지 않음)"
    )


# ──────────────────────────────────────────────
# Step 3 — _register_llm_mappings_to_redis
# ──────────────────────────────────────────────


async def test_step3_db_without_structure_registers_to_db_scope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """구조 정보 없는 DB: 일반 매핑은 DB별 캐시(출처 llm) + INFO 1줄 · EAV는 등록 0 + WARNING."""
    cm = _cache_manager(authorized=set())
    details = [
        {"field": "자산번호", "db_id": NEW_DB, "column": "asset.asset_no"},
        {"field": "담당자명", "db_id": NEW_DB, "column": "EAV:Owner"},
    ]

    with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms={})

    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_awaited_once_with(NEW_DB, "asset.asset_no", ["자산번호"], source="llm")
    assert _db_scope_lines(caplog) == [_db_scope_line(NEW_DB, "자산번호", "asset.asset_no")]
    assert _eav_skipped_lines(caplog) == [_eav_skipped_line(NEW_DB, "담당자명", "EAV:Owner")]
    # 같은 DB 판정은 호출 안에서 1회만
    cm.has_structure_authority.assert_awaited_once_with(NEW_DB)


async def test_step3_registers_db_with_structure_as_before(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """구조 정보가 있는 DB는 종전처럼 전역·EAV 사전에 등록하고 DB별 등록은 하지 않는다."""
    cm = _cache_manager(authorized={MANUAL_DB})
    details = [
        {"field": "자산번호", "db_id": MANUAL_DB, "column": "asset.asset_no"},
        {"field": "담당자명", "db_id": MANUAL_DB, "column": "EAV:Owner"},
    ]

    with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms={})

    cm._redis_cache.add_global_synonym.assert_any_await("asset_no", ["자산번호"])
    cm._redis_cache.add_global_synonym.assert_any_await("Owner", ["담당자명"])
    cm._redis_cache.save_eav_name_synonyms.assert_awaited_once_with({"Owner": ["담당자명"]})
    cm.add_synonyms.assert_not_called()
    assert _db_scope_lines(caplog) == []
    assert _eav_skipped_lines(caplog) == []


async def test_step3_mixed_dbs_split_global_and_db_scope() -> None:
    """한 호출에 두 DB가 섞이면 구조 정보 있는 DB는 전역, 없는 DB는 DB별로 나뉜다."""
    cm = _cache_manager(authorized={MANUAL_DB})
    details = [
        {"field": "자산번호", "db_id": NEW_DB, "column": "asset.asset_no"},
        {"field": "IP주소", "db_id": MANUAL_DB, "column": "node.ip_addr"},
    ]

    await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms=None)

    cm._redis_cache.add_global_synonym.assert_awaited_once_with("ip_addr", ["IP주소"])
    cm.add_synonyms.assert_awaited_once_with(NEW_DB, "asset.asset_no", ["자산번호"], source="llm")


async def test_step3_servername_to_hostname_guard_kept_for_db_scope() -> None:
    """서버명→hostname 오연관 차단은 DB별 등록 경로에서도 그대로다."""
    cm = _cache_manager(authorized=set())
    details = [{"field": "서버명", "db_id": NEW_DB, "column": "node.hostname"}]

    await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms=None)

    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_not_called()


async def test_step3_empty_db_id_skipped_without_lookup(caplog: pytest.LogCaptureFixture) -> None:
    """db_id가 빈 항목은 판정·등록 대상이 없어 전역·DB별 모두 쓰지 않는다.

    운영 경로에서는 Step 3(`_apply_llm_mapping_with_synonyms`)가 db_id 없는 응답을 먼저 버린다.
    """
    cm = _cache_manager(authorized={MANUAL_DB})
    details = [{"field": "자산번호", "db_id": "", "column": "asset.asset_no"}]

    with caplog.at_level(logging.WARNING, logger="src.document.field_mapper"):
        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms=None)

    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_not_called()
    cm.has_structure_authority.assert_not_called()
    assert _lines(caplog, logging.WARNING, "대상 db_id 없음") != []


async def test_step3_authority_check_failure_no_global_but_db_scope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """판정 자체가 실패하면 전역에는 쓰지 않고(안전 쪽) 일반 매핑은 DB별로 등록한다.

    DB별 캐시는 그 DB 질의에만 쓰여 오염이 번지지 않는다 — 실제로 구조 정보가 없던 DB면
    G-11 (b) 그대로이고, 있던 DB면 학습 범위만 좁아진다. EAV는 DB별 저장소가 없어 등록 0.
    """
    cm = _cache_manager(authorized=set())
    cm.has_structure_authority = AsyncMock(side_effect=RuntimeError("redis down"))
    details = [
        {"field": "자산번호", "db_id": MANUAL_DB, "column": "asset.asset_no"},
        {"field": "담당자명", "db_id": MANUAL_DB, "column": "EAV:Owner"},
    ]

    with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms={})

    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_awaited_once_with(
        MANUAL_DB, "asset.asset_no", ["자산번호"], source="llm"
    )
    assert _lines(caplog, logging.WARNING, "구조 정보 판정 실패 —") != []
    assert _db_scope_lines(caplog) == [
        _db_scope_line(MANUAL_DB, "자산번호", "asset.asset_no", reason="구조 정보 판정 실패")
    ]
    assert len(_eav_skipped_lines(caplog)) == 1
    # 실패도 호출 안에서 1회만 판정
    cm.has_structure_authority.assert_awaited_once_with(MANUAL_DB)


async def test_step3_db_scope_write_failure_logged(caplog: pytest.LogCaptureFixture) -> None:
    """DB별 등록이 False를 돌려주면 WARNING을 남기고 INFO(등록) 줄은 남기지 않는다."""
    cm = _cache_manager(authorized=set())
    cm.add_synonyms = AsyncMock(return_value=False)
    details = [{"field": "자산번호", "db_id": NEW_DB, "column": "asset.asset_no"}]

    with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms=None)

    _assert_no_global_writes(cm)
    assert _db_scope_lines(caplog) == []
    assert _lines(caplog, logging.WARNING, "양식 LLM 매핑 DB별 등록 실패") != []


# ──────────────────────────────────────────────
# Step 2.8 — _register_llm_synonym_discoveries_to_redis
# ──────────────────────────────────────────────


async def test_step28_db_without_structure_column_to_db_scope_eav_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Step 2.8 컬럼(matched_key의 db_id)은 DB별 등록 · EAV(eav_db_id)는 등록 0 + WARNING."""
    cm = _cache_manager(authorized=set())
    mapped = [
        ("자산번호", f"{NEW_DB}:asset.asset_no", "column"),
        ("시리얼", "EAV:SerialNo", "eav"),
    ]

    with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
        await _register_llm_synonym_discoveries_to_redis(
            cm, mapped, eav_name_synonyms={}, eav_db_id=NEW_DB
        )

    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_awaited_once_with(NEW_DB, "asset.asset_no", ["자산번호"], source="llm")
    assert _db_scope_lines(caplog) == [_db_scope_line(NEW_DB, "자산번호", "asset.asset_no")]
    assert _eav_skipped_lines(caplog) == [_eav_skipped_line(NEW_DB, "시리얼", "EAV:SerialNo")]
    cm.has_structure_authority.assert_awaited_once_with(NEW_DB)


async def test_step28_eav_without_target_db_skipped() -> None:
    """EAV 매핑의 대상 DB를 모르면(eav_db_id 미전달) 판정·등록 없이 건너뛴다."""
    cm = _cache_manager(authorized={MANUAL_DB})

    await _register_llm_synonym_discoveries_to_redis(
        cm, [("시리얼", "EAV:SerialNo", "eav")], eav_name_synonyms={}
    )

    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_not_called()
    cm.has_structure_authority.assert_not_called()


async def test_step28_registers_db_with_structure_as_before() -> None:
    """구조 정보가 있는 DB는 종전처럼 전역에 등록하고 DB별 등록은 하지 않는다."""
    cm = _cache_manager(authorized={MANUAL_DB})
    mapped = [
        ("자산번호", f"{MANUAL_DB}:asset.asset_no", "column"),
        ("시리얼", "EAV:SerialNo", "eav"),
    ]

    await _register_llm_synonym_discoveries_to_redis(
        cm, mapped, eav_name_synonyms={}, eav_db_id=MANUAL_DB
    )

    cm.add_global_synonym.assert_awaited_once_with("asset_no", ["자산번호"])
    cm._redis_cache.save_eav_name_synonyms.assert_awaited_once_with({"SerialNo": ["시리얼"]})
    cm._redis_cache.add_global_synonym.assert_awaited_once_with("SerialNo", ["시리얼"])
    cm.add_synonyms.assert_not_called()


async def test_step28_authority_check_failure_no_global_but_db_scope() -> None:
    """Step 2.8도 판정 실패면 전역 쓰기 0 · 컬럼 매핑은 DB별 등록."""
    cm = _cache_manager(authorized=set())
    cm.has_structure_authority = AsyncMock(side_effect=RuntimeError("redis down"))

    await _register_llm_synonym_discoveries_to_redis(
        cm, [("자산번호", f"{MANUAL_DB}:asset.asset_no", "column")], eav_name_synonyms={}
    )

    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_awaited_once_with(
        MANUAL_DB, "asset.asset_no", ["자산번호"], source="llm"
    )


async def test_step28_discovery_passes_target_db_and_keeps_request_mapping() -> None:
    """발견 단계는 대상 DB를 가드에 넘기고, 전역 대신 DB별로 등록해도 요청 매핑 결과는 그대로다."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=json.dumps(
                {
                    "자산번호": {"matched_key": f"{NEW_DB}:asset.asset_no"},
                    "시리얼": {"matched_key": "EAV:SerialNo"},
                }
            )
        )
    )
    cm = _cache_manager(authorized=set())
    remaining = {"자산번호", "시리얼"}
    result = MappingResult()

    await _apply_llm_synonym_discovery(
        llm=llm,
        remaining=remaining,
        all_db_synonyms={NEW_DB: {"asset.asset_no": []}},
        eav_name_synonyms={"SerialNo": []},
        priority_db_ids=[NEW_DB],
        result=result,
        cache_manager=cm,
    )

    assert result.db_column_mapping[NEW_DB] == {
        "자산번호": "asset.asset_no",
        "시리얼": "EAV:SerialNo",
    }
    assert remaining == set()
    _assert_no_global_writes(cm)
    cm.add_synonyms.assert_awaited_once_with(NEW_DB, "asset.asset_no", ["자산번호"], source="llm")
    cm.has_structure_authority.assert_awaited_once_with(NEW_DB)


# ──────────────────────────────────────────────
# 사용자 명시 경로 불변(D-228 ③)
# ──────────────────────────────────────────────


async def test_user_feedback_path_not_guarded() -> None:
    """양식 매핑 사용자 피드백 반영은 구조 정보 유무와 무관하게 종전대로 쓴다."""
    cm = _cache_manager(authorized=set())
    diff = {
        "added": [{"field": "자산번호", "db_id": NEW_DB, "column": "asset.asset_no"}],
        "modified": [],
        "deleted": [],
    }

    out = await apply_mapping_feedback_to_redis(cm, diff)

    assert out["registered"] == 1
    cm.add_global_synonym.assert_awaited_once_with("asset_no", ["자산번호"])
    cm.has_structure_authority.assert_not_called()


# ──────────────────────────────────────────────
# 실제 SchemaCacheManager(`has_structure_authority`) + 인메모리 Redis 페이크
# ──────────────────────────────────────────────


def _real_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """실제 판정 함수를 쓰는 매니저 — 작업 디렉터리를 tmp로 옮겨 저장소 `config/`를 보지 않는다."""
    from src.schema_cache.cache_manager import SchemaCacheManager
    from tests.mocks.async_redis import attach_fake_redis

    monkeypatch.chdir(tmp_path)
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    fake = attach_fake_redis(mgr._redis_cache)
    mgr._redis_available = True
    return mgr, fake


class TestRealAuthority:
    """mock 통과 ≠ 실동작 — 실제 판정(수동 프로필 파일·승인본)·실제 Redis 쓰기로 키 변화를 본다."""

    async def test_db_without_structure_writes_db_scope_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """구조 정보 없는 DB: `schema:{db_id}:synonyms`에 출처 llm으로 등록 · 전역 키는 없다."""
        mgr, fake = _real_manager(tmp_path, monkeypatch)
        details = [
            {"field": "자산번호", "db_id": NEW_DB, "column": "asset.asset_no"},
            {"field": "담당자명", "db_id": NEW_DB, "column": "EAV:Owner"},
        ]

        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            await _register_llm_mappings_to_redis(mgr, details, eav_name_synonyms={})
            await _register_llm_synonym_discoveries_to_redis(
                mgr, [("시리얼", f"{NEW_DB}:asset.serial_no", "column")], eav_db_id=NEW_DB
            )

        assert await fake.exists("synonyms:global") == 0
        assert await fake.exists("synonyms:eav_names") == 0
        # Redis에 생긴 키는 전부 그 DB 범위다(전역 쓰기 0)
        assert fake.dump() and all(k.startswith(f"schema:{NEW_DB}:") for k in fake.dump())

        stored = {
            col: json.loads(raw)
            for col, raw in (await fake.hgetall(f"schema:{NEW_DB}:synonyms")).items()
        }
        assert set(stored) == {"asset.asset_no", "asset.serial_no"}
        assert stored["asset.asset_no"]["words"] == ["자산번호"]
        assert stored["asset.asset_no"]["sources"] == {"자산번호": "llm"}
        assert stored["asset.serial_no"]["sources"] == {"시리얼": "llm"}

        assert _db_scope_lines(caplog) == [
            _db_scope_line(NEW_DB, "자산번호", "asset.asset_no"),
            _db_scope_line(NEW_DB, "시리얼", "asset.serial_no"),
        ]
        assert _eav_skipped_lines(caplog) == [_eav_skipped_line(NEW_DB, "담당자명", "EAV:Owner")]

    async def test_db_scope_keeps_existing_operator_words(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB별 등록은 기존 운영자 단어·출처를 덮지 않고 병합한다."""
        mgr, fake = _real_manager(tmp_path, monkeypatch)
        await mgr.add_synonyms(NEW_DB, "asset.asset_no", ["자산 코드"], source="operator")

        await _register_llm_mappings_to_redis(
            mgr, [{"field": "자산번호", "db_id": NEW_DB, "column": "asset.asset_no"}]
        )

        entry = json.loads((await fake.hgetall(f"schema:{NEW_DB}:synonyms"))["asset.asset_no"])
        assert entry["words"] == ["자산 코드", "자산번호"]
        assert entry["sources"] == {"자산 코드": "operator", "자산번호": "llm"}
        assert await fake.exists("synonyms:global") == 0

    async def test_manual_profile_db_registers_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """수동 프로필 DB는 종전처럼 전역에 등록하고 DB별 키는 만들지 않는다."""
        mgr, fake = _real_manager(tmp_path, monkeypatch)
        profiles = tmp_path / "config" / "db_profiles"
        profiles.mkdir(parents=True)
        (profiles / f"{MANUAL_DB}.yaml").write_text("source: manual\n", encoding="utf-8")

        await _register_llm_mappings_to_redis(
            mgr, [{"field": "자산번호", "db_id": MANUAL_DB, "column": "asset.asset_no"}]
        )

        raw = (await fake.hgetall("synonyms:global"))["asset_no"]
        assert "자산번호" in json.dumps(json.loads(raw), ensure_ascii=False)
        assert await fake.exists(f"schema:{MANUAL_DB}:synonyms") == 0

    async def test_approved_structure_db_registers_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """승인본만 있는 DB(수동 프로필 파일 없음)는 전역에 등록한다.

        승인본 기록 API는 저장소 모듈 소관(plans/104 Wave 2에서 개정)이라, 실제 판정 함수가 읽는
        조회 경계(`list_versions`·`has_versions`·`has_approved_version`)만 "승인 버전 있음"으로
        고정한다.
        """
        mgr, fake = _real_manager(tmp_path, monkeypatch)
        store = mgr.structure_store
        monkeypatch.setattr(store, "list_versions", AsyncMock(return_value=[{"ver": 1}]))
        monkeypatch.setattr(store, "has_versions", lambda _db_id: True, raising=False)
        monkeypatch.setattr(store, "has_approved_version", lambda _db_id: True, raising=False)

        await _register_llm_synonym_discoveries_to_redis(
            mgr, [("시리얼", "EAV:SerialNo", "eav")], eav_name_synonyms={},
            eav_db_id="approved_db",
        )

        assert "SerialNo" in await fake.hgetall("synonyms:eav_names")
        assert "SerialNo" in await fake.hgetall("synonyms:global")
        assert await fake.exists("schema:approved_db:synonyms") == 0
