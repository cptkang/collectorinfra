"""plans/120 F-4 — 양식 LLM 매핑 자동 등록의 결정적 쓰기 가드.

막는 유형: ⓐ월 구조 필드명 ⓑEAV 피벗 DB의 구조 선언 밖 테이블 ⓒ날짜 접미사 스냅샷 테이블
ⓓEAV 속성으로 가는 「그룹|서브」 복합 필드명(D-148 지표 ③ — 일반 컬럼 대상 복합명은 범위 밖).
판정 함수(`synonym_registration_block_reason`)는 하나이고 오염 진단 도구가 그대로 재사용한다.
전역(`synonyms:global`)·EAV 속성명·DB별(`schema:{db_id}:synonyms`) 세 저장 공간 모두
같은 규칙을 받는다.
정상 매핑 등록은 비트 동일이다. LLM 0 · Redis는 모의/인메모리 페이크.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.document.field_mapper import (
    _register_llm_mappings_to_redis,
    _register_llm_synonym_discoveries_to_redis,
)
from src.document.synonym_write_guard import (
    BLOCK_DATE_SUFFIX_TABLE,
    BLOCK_EAV_COMPOSITE_FIELD,
    BLOCK_MONTH_STRUCTURE_FIELD,
    BLOCK_OUTSIDE_DECLARED_TABLES,
    column_table,
    eav_declared_tables,
    is_date_suffix_snapshot_table,
    synonym_registration_block_reason,
)

REPO = Path(__file__).resolve().parent.parent.parent
EAV_DB = "polestar_cm_gp"  # 수동 프로필(EAV 선언) — 구조 정보 있음
NEW_DB = "db_new"  # 구조 정보 없는 DB(DB별 등록 경로)
_MONTH_FIELD = "월중평균사용률(최근 6개월간)|M+2"


def _profile_meta(db_id: str) -> dict:
    """저장소 실제 프로필(구조 선언) — 캐시 매니저의 프로필 폴백과 같은 모양(`source` 제외)."""
    data = yaml.safe_load((REPO / "config" / "db_profiles" / f"{db_id}.yaml").read_text("utf-8"))
    return {k: v for k, v in data.items() if k != "source"}


@pytest.fixture(scope="module")
def gp_meta() -> dict:
    return _profile_meta(EAV_DB)


class TestBlockReason:
    def test_month_structure_field_blocked_for_any_target(self, gp_meta):
        for column in ("polestar.cmm_resource.name", "EAV:TotalSize", "mem_ratio"):
            assert synonym_registration_block_reason(
                _MONTH_FIELD, column, structure_meta=gp_meta
            ) == BLOCK_MONTH_STRUCTURE_FIELD

    def test_eav_composite_field_blocked(self, gp_meta):
        """ⓓ 「그룹|서브」 복합 필드명은 EAV 속성으로 등록하지 않는다(D-148 반례 그대로)."""
        assert synonym_registration_block_reason(
            "처리능력|(TPMC)", "EAV:TotalSize", structure_meta=gp_meta
        ) == BLOCK_EAV_COMPOSITE_FIELD

    def test_composite_scope_is_eav_only(self, gp_meta):
        """일반 컬럼 대상 복합명은 범위 밖(월 구조 필드만 막는다) · 월 구조가 먼저 판정된다."""
        assert synonym_registration_block_reason(
            "구분|분류", "polestar.cmm_resource.category", structure_meta=gp_meta
        ) is None
        assert synonym_registration_block_reason("구분|분류", "category") is None
        assert synonym_registration_block_reason(
            _MONTH_FIELD, "EAV:TotalSize"
        ) == BLOCK_MONTH_STRUCTURE_FIELD

    def test_date_suffix_snapshot_table_blocked_without_meta(self):
        assert synonym_registration_block_reason(
            "메모리 사용률", "MON_HW_20260806.MEM_RATIO"
        ) == BLOCK_DATE_SUFFIX_TABLE

    def test_outside_declared_table_blocked_for_eav_db(self, gp_meta):
        assert synonym_registration_block_reason(
            "비고", "polestar.ip_info.description", structure_meta=gp_meta
        ) == BLOCK_OUTSIDE_DECLARED_TABLES

    @pytest.mark.parametrize(
        "field, column",
        [
            ("호스트명", "polestar.cmm_resource.hostname"),
            ("심각도", "polestar.cmm_alarm.alarmseverity"),
            ("운영체제", "EAV:OSType"),
            ("호스트명", "hostname"),  # 전역 사전 bare 키
        ],
    )
    def test_normal_mapping_allowed(self, gp_meta, field, column):
        assert synonym_registration_block_reason(field, column, structure_meta=gp_meta) is None

    def test_outside_rule_needs_eav_and_allowed_tables(self):
        """EAV 선언이 없거나 테이블 선언이 없으면 ⓑ는 판정하지 않는다(종전 동작)."""
        no_eav = {"patterns": [], "allowed_tables": ["a"]}
        no_tables = {"patterns": [{"type": "eav", "entity_table": "ent"}]}
        for meta in (None, no_eav, no_tables):
            assert synonym_registration_block_reason(
                "비고", "x.other.col", structure_meta=meta
            ) is None

    def test_helpers(self):
        assert column_table("POLESTAR.IPAM_INFO.DESCRIPTION") == "ipam_info"
        assert column_table("EAV:OSType") is None
        assert column_table("hostname") is None
        assert is_date_suffix_snapshot_table("mon_hw_20260806")
        assert not is_date_suffix_snapshot_table("x_20261399")  # 실재하지 않는 날짜
        assert not is_date_suffix_snapshot_table("cmm_metric_stat_m")
        assert not is_date_suffix_snapshot_table("t_2026080")  # 7자리
        meta = {"patterns": [{"type": "eav"}], "allowed_tables": ["S.T"]}
        assert eav_declared_tables(meta) == {"t"}


# ── 등록 함수 — 모의 캐시 매니저 ─────────────────────────────────────────────


def _cache_manager(authorized: set[str]) -> MagicMock:
    cm = MagicMock()
    cm.redis_available = True
    cm.add_synonyms = AsyncMock(return_value=True)
    cm.add_global_synonym = AsyncMock(return_value=True)
    cm.has_structure_authority = AsyncMock(side_effect=lambda db_id: db_id in authorized)
    redis_cache = MagicMock()
    redis_cache.load_eav_name_synonyms = AsyncMock(return_value={})
    redis_cache.save_eav_name_synonyms = AsyncMock(return_value=True)
    redis_cache.add_global_synonym = AsyncMock(return_value=True)
    cm._redis_cache = redis_cache
    return cm


def _all_writes(cm: MagicMock) -> list[Any]:
    return [
        *cm.add_synonyms.await_args_list,
        *cm.add_global_synonym.await_args_list,
        *cm._redis_cache.add_global_synonym.await_args_list,
        *cm._redis_cache.save_eav_name_synonyms.await_args_list,
    ]


_VIOLATING_STEP3 = [
    {"field": _MONTH_FIELD, "db_id": EAV_DB, "column": "EAV:TotalSize"},
    {"field": "처리능력|(TPMC)", "db_id": EAV_DB, "column": "EAV:TotalSize"},
    {"field": "비고", "db_id": EAV_DB, "column": "polestar.ip_info.description"},
    {"field": "메모리 사용률", "db_id": EAV_DB, "column": "polestar.mon_hw_20260806.mem_ratio"},
]
_NORMAL_STEP3 = [
    {"field": "장비명", "db_id": EAV_DB, "column": "polestar.cmm_resource.name"},
    {"field": "운영체제명", "db_id": EAV_DB, "column": "EAV:OSType"},
]


class TestStep3Registration:
    async def test_three_types_not_registered_in_any_store(self, gp_meta, caplog):
        cm = _cache_manager(authorized={EAV_DB})
        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            await _register_llm_mappings_to_redis(
                cm, _VIOLATING_STEP3, eav_name_synonyms={}, structure_metas={EAV_DB: gp_meta}
            )
        assert _all_writes(cm) == []
        blocked = [r.getMessage() for r in caplog.records if "자동 등록 차단" in r.getMessage()]
        assert len(blocked) == 4
        assert any("월 구조 필드명" in m for m in blocked)
        assert any("복합 필드명" in m for m in blocked)
        assert any("구조 선언 밖" in m for m in blocked)
        assert any("날짜 접미사" in m for m in blocked)

    async def test_normal_registration_bit_identical(self, gp_meta):
        """가드가 있어도 정상 매핑의 쓰기 호출은 가드 없는 호출(구조 선언 미전달)과 같다."""
        guarded, plain = _cache_manager({EAV_DB}), _cache_manager({EAV_DB})
        await _register_llm_mappings_to_redis(
            guarded, _VIOLATING_STEP3 + _NORMAL_STEP3, eav_name_synonyms={},
            structure_metas={EAV_DB: gp_meta},
        )
        await _register_llm_mappings_to_redis(plain, _NORMAL_STEP3, eav_name_synonyms={})
        assert _all_writes(guarded) == _all_writes(plain)
        # 전역 bare 키는 종전 규칙(첫 '.' 뒤) 그대로 — 스키마 접두 컬럼이면 "table.column"이 된다
        guarded._redis_cache.add_global_synonym.assert_any_await("cmm_resource.name", ["장비명"])
        guarded._redis_cache.add_global_synonym.assert_any_await("OSType", ["운영체제명"])

    async def test_db_scope_path_blocks_month_and_snapshot(self):
        """구조 정보 없는 DB(DB별 등록 경로)도 ⓐ·ⓒ는 막는다 — ⓑ는 선언이 없어 판정하지 않는다."""
        cm = _cache_manager(authorized=set())
        details = [
            {"field": _MONTH_FIELD, "db_id": NEW_DB, "column": "asset.util"},
            {"field": "스냅샷값", "db_id": NEW_DB, "column": "snap_20250101.val"},
            {"field": "자산번호", "db_id": NEW_DB, "column": "asset.asset_no"},
        ]
        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms={}, structure_metas={})
        cm.add_synonyms.assert_awaited_once_with(
            NEW_DB, "asset.asset_no", ["자산번호"], source="llm"
        )

    async def test_standalone_call_loads_structure_once_per_db(self, gp_meta):
        """구조 선언을 넘기지 않은 단독 호출은 캐시 매니저에서 DB별 1회 조회한다."""
        cm = _cache_manager(authorized={EAV_DB})
        cm.get_structure_meta_or_profile = AsyncMock(return_value=gp_meta)
        await _register_llm_mappings_to_redis(
            cm, _VIOLATING_STEP3 + _NORMAL_STEP3, eav_name_synonyms={}
        )
        cm.get_structure_meta_or_profile.assert_awaited_once_with(EAV_DB)
        # 정상 2건만 쓴다 — 컬럼(전역 1) + EAV(속성명 사전 저장 1 · 전역 1)
        assert len(_all_writes(cm)) == 3


class TestStep28Registration:
    async def test_three_types_not_registered(self, gp_meta):
        cm = _cache_manager(authorized={EAV_DB})
        mapped = [
            (_MONTH_FIELD, "EAV:TotalSize", "eav"),
            ("처리능력|(TPMC)", "EAV:TotalSize", "eav"),
            ("비고", f"{EAV_DB}:polestar.ip_info.description", "column"),
            ("메모리 사용률", f"{EAV_DB}:polestar.mon_hw_20260806.mem_ratio", "column"),
            ("장비명", f"{EAV_DB}:polestar.cmm_resource.name", "column"),
        ]
        await _register_llm_synonym_discoveries_to_redis(
            cm, mapped, {}, eav_db_id=EAV_DB, structure_metas={EAV_DB: gp_meta}
        )
        assert _all_writes(cm) == [(("cmm_resource.name", ["장비명"]), {})]


class TestRealStore:
    """실제 SchemaCacheManager + 인메모리 Redis — 차단된 매핑은 어느 키에도 들어가지 않는다."""

    async def test_no_key_changes_for_blocked(self, tmp_path: Path, monkeypatch, gp_meta):
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

        await _register_llm_mappings_to_redis(
            mgr,
            [{**d, "db_id": NEW_DB} for d in _VIOLATING_STEP3],
            eav_name_synonyms={},
            structure_metas={NEW_DB: gp_meta},
        )
        assert fake.dump() == {}
