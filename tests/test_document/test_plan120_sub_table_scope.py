"""plans/120 F-2 — EAV 피벗 DB의 Pass 2(서브 테이블) 유사어 매칭을 구조 선언 테이블로 한정한다.

벤치 run `20260923-140539` 2단 로그의 서브 테이블 정확 매칭 10종(§2.4)은 전부 결정적 피벗이
나중에 버리는 매핑이었다. 구조 선언은 **저장소 실제 프로필**(`config/db_profiles/*.yaml`)을 실제
`SchemaCacheManager.get_structure_meta_or_profile` 경로로 읽는다(mock shape 금지 — Known Mistakes).
LLM 0 · DB 0 · Redis 0.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.document.field_mapper import (
    MappingResult,
    _apply_synonym_mapping,
    _core_entity_tables,
    _load_structure_declarations,
    _sub_table_scope,
    perform_3step_mapping,
)
from src.document.synonym_write_guard import eav_declared_tables

REPO = Path(__file__).resolve().parent.parent.parent
B0, GP, YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
_AVG = "월중평균사용률(최근 6개월간)"
_PEAK = "월중 Peak시 사용률(최근 6개월간)"

#: 폐쇄망 run 로그의 서브 테이블 정확 매칭 10종 — (필드, 매핑된 컬럼, DB)
POLLUTED: list[tuple[str, str, str]] = [
    (f"{_AVG}|M", "MON_HW_20260806.MEM_RATIO", B0),
    (f"{_PEAK}|M+1", "MON_HW_20260806.MEM_RATIO", B0),
    ("비고", "polestar.ip_info.description", GP),
    ("비고", "IPAM_INFO.DESCRIPTION", B0),
    ("담당자", "polestar.rep_inst_send.manual_input_user", GP),
    ("리소스유형", "polestar.cmm_custom_mon_m_src.custom_monitor_resource_type", YD),
    ("도입일자", "polestar.sap_profile.create_date", YD),
    ("용도", "SERVER_DEFAULT_PORT_PERMISSION.PORT_DESC", B0),
    ("구분|분류", "CMM_ALARM_DEF_C_CON.DTYPE", B0),
    ("구분", "polestar.cmm_resource_type.category", GP),
]


def _real_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """저장소 프로필을 읽는 실제 매니저(파일 백엔드 · 승인 버전 없음 → 프로필 파일 폴백)."""
    from src.schema_cache.cache_manager import SchemaCacheManager

    monkeypatch.chdir(REPO)
    config = MagicMock()
    config.schema_cache.backend = "file"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = False
    return SchemaCacheManager(config)


@pytest.fixture
async def real_metas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    mgr = _real_manager(tmp_path, monkeypatch)
    metas = await _load_structure_declarations(mgr, [B0, GP, YD])
    assert set(metas) == {B0, GP, YD}
    return metas


def _polluted_synonyms() -> dict[str, dict[str, list[str]]]:
    """오염 사전 + 정상 항목(핵심 테이블 · 선언된 서브 테이블)."""
    syns: dict[str, dict[str, list[str]]] = {B0: {}, GP: {}, YD: {}}
    for field, column, db_id in POLLUTED:
        syns[db_id].setdefault(column, []).append(field)
    for db_id in syns:
        prefix = "" if db_id == B0 else "polestar."
        syns[db_id][f"{prefix}cmm_resource.hostname"] = ["호스트명"]
        syns[db_id][f"{prefix}cmm_alarm.alarmseverity"] = ["심각도"]
    return syns


class TestDeclaredTablesFromRealProfiles:
    """실제 프로필에서 선언 집합을 얻고, 오염 10종의 테이블이 그 밖인지 먼저 확인한다."""

    async def test_polluted_tables_are_outside_declarations(self, real_metas):
        for field, column, db_id in POLLUTED:
            declared = eav_declared_tables(real_metas[db_id])
            assert declared is not None, db_id
            table = column.split(".")[-2].lower()
            assert table not in declared, (field, column)

    async def test_declared_set_covers_core_metric_and_alarm_tables(self, real_metas):
        declared = eav_declared_tables(real_metas[GP])
        assert {"cmm_resource", "core_config_prop", "cmm_metric_stat_m", "cmm_alarm"} <= declared


class TestPass2Scope:
    """`_apply_synonym_mapping` 단위 — 월 필드 스킵(F-1)이 없어도 Pass 2가 스스로 거부한다."""

    async def test_all_ten_polluted_matches_rejected(self, real_metas, caplog):
        syns = _polluted_synonyms()
        fields = {f for f, _c, _d in POLLUTED}
        for db_order in ([B0, GP, YD], []):
            result = MappingResult()
            remaining = set(fields)
            with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
                _apply_synonym_mapping(
                    remaining, syns, db_order, result,
                    core_tables=_core_entity_tables(real_metas),
                    sub_table_scope=_sub_table_scope(real_metas, db_order or list(syns)),
                )
            assert result.db_column_mapping == {}
            assert remaining == fields
        rejected = [
            r.getMessage() for r in caplog.records if "매칭 거부(구조 선언 밖" in r.getMessage()
        ]
        assert rejected

    async def test_without_scope_polluted_matches_survive(self, real_metas):
        """대조군 — 제한이 없으면(종전 동작) 오염 항목이 그대로 매칭된다."""
        syns = _polluted_synonyms()
        result = MappingResult()
        remaining = {"비고", "담당자", "도입일자"}
        _apply_synonym_mapping(
            remaining, syns, [GP, YD], result, core_tables=_core_entity_tables(real_metas),
        )
        assert result.db_column_mapping[GP]["담당자"] == "polestar.rep_inst_send.manual_input_user"
        assert result.db_column_mapping[YD]["도입일자"] == "polestar.sap_profile.create_date"

    async def test_core_and_declared_sub_table_matches_unchanged(self, real_metas):
        syns = _polluted_synonyms()
        fields = {"호스트명", "심각도"}
        outs = []
        for scope in (None, _sub_table_scope(real_metas, [GP])):
            result = MappingResult()
            remaining = set(fields)
            _apply_synonym_mapping(
                remaining, syns, [GP], result,
                core_tables=_core_entity_tables(real_metas), sub_table_scope=scope,
            )
            outs.append((result.db_column_mapping, result.mapping_sources, remaining))
        assert outs[0] == outs[1]
        assert outs[1][0] == {
            GP: {
                "호스트명": "polestar.cmm_resource.hostname",
                "심각도": "polestar.cmm_alarm.alarmseverity",
            }
        }

    async def test_rejected_field_falls_through_to_next_db_in_scope(self, real_metas):
        """선언 밖 매칭을 거부한 뒤 다음 DB의 선언 안 매칭은 그대로 받는다."""
        syns = {
            GP: {"polestar.ip_info.description": ["비고"]},
            YD: {"polestar.cmm_alarm.message": ["비고"]},
        }
        result = MappingResult()
        remaining = {"비고"}
        _apply_synonym_mapping(
            remaining, syns, [GP, YD], result,
            core_tables=_core_entity_tables(real_metas),
            sub_table_scope=_sub_table_scope(real_metas, [GP, YD]),
        )
        assert result.db_column_mapping == {YD: {"비고": "polestar.cmm_alarm.message"}}

    async def test_fuzzy_fallback_does_not_readmit_rejected(self, real_metas):
        """Pass 3(퍼지)도 같은 한정 — 선언 밖 동일 단어(점수 1.0)가 되살아나지 않는다."""
        syns = {GP: {"polestar.ip_info.description": ["비고"]}}
        result = MappingResult()
        remaining = {"비고"}
        _apply_synonym_mapping(
            remaining, syns, [GP], result, fuzzy=True, min_score=0.85,
            core_tables=_core_entity_tables(real_metas),
            sub_table_scope=_sub_table_scope(real_metas, [GP]),
        )
        assert result.db_column_mapping == {}
        assert remaining == {"비고"}


class TestScopeNotApplicable:
    """EAV 선언이 없는 DB·선언을 못 얻은 DB는 종전 동작(차단하지 않음) + 로그."""

    def test_non_eav_db_is_unscoped(self):
        assert _sub_table_scope({"itam": {"patterns": [], "query_guide": ""}}, ["itam"]) == {}

    def test_missing_declaration_logs_and_is_unscoped(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            assert _sub_table_scope({}, ["db_x"]) == {}
        assert any("구조 선언을 얻지 못함" in r.getMessage() for r in caplog.records)

    def test_eav_without_allowed_tables_logs_and_is_unscoped(self, caplog):
        meta = {"patterns": [{"type": "eav", "entity_table": "ent", "config_table": "cfg"}]}
        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            assert _sub_table_scope({"db_x": meta}, ["db_x"]) == {}
        assert any("테이블 선언(allowed_tables)이 없음" in r.getMessage() for r in caplog.records)


class TestPerform3StepMapping:
    """통합 — 실제 프로필 구조 선언으로 perform_3step_mapping을 돌린다(LLM 응답 없음)."""

    async def test_polluted_matches_rejected_core_and_eav_kept(
        self, tmp_path, monkeypatch, caplog
    ):
        mgr = _real_manager(tmp_path, monkeypatch)
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="{}")
        fields = [
            "호스트명", "운영체제", "비고", "담당자", "리소스유형", "도입일자", "용도", "구분",
        ]

        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            result, details = await perform_3step_mapping(
                llm=llm,
                field_names=fields,
                field_mapping_hints=[],
                all_db_synonyms=_polluted_synonyms(),
                all_db_descriptions={},
                priority_db_ids=[GP, YD, B0],
                eav_name_synonyms={"OSType": ["운영체제"]},
                cache_manager=mgr,
                active_db_ids=[B0, GP, YD],
            )

        assert details == []
        assert result.column_mapping["호스트명"] == "polestar.cmm_resource.hostname"
        assert result.column_mapping["운영체제"] == "EAV:OSType"
        for field in ("비고", "담당자", "리소스유형", "도입일자", "용도", "구분"):
            assert result.column_mapping[field] is None, field
        assert not any("정확 매칭 확정(서브 테이블)" in r.getMessage() for r in caplog.records)
