"""D-294 W5·W0 — 생성 자산의 범용 소비 (관계 · 규칙·코드값 블록 · 생성 템플릿 어댑터) · 알람 조립
가드.

- 폴스타 프로필(새 키 없음)은 규칙 블록이 빈 문자열이고 관계를 더하지 않는다 → 프롬프트 바이트 불변
- 프로필 관계는 두 끝 테이블이 스키마에 있을 때만 더하고, 추론 관계 줄에는 표시를 단다
- 승인된 DB 전용 섹션이 있는 DB만 생성 템플릿 어댑터를 받고(폴스타 담당 DB는 폴스타 그대로),
  그 DB의 알람 의도에는 폴스타 알람 조립기가 붙지 않는다(W0)
- 멀티 경로도 경로 대칭 플래그와 무관하게 같은 섹션을 받는다
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.db_adapters import get_adapter
from src.db_adapters.generated import BoundGeneratedTemplate, compose_template
from src.db_adapters.polestar.adapter import PolestarAdapter
from src.nodes.prompt_blocks import build_profile_rules_block, format_schema_text
from src.utils.schema_utils import attach_profile_relationships

REPO = Path(__file__).resolve().parents[2]
SECTION = "### 업무 개요\n`t_srv`는 서버 목록이다."


def _write_section(
    root: Path, db_id: str, section: str = SECTION, owner: str | None = None
) -> Path:
    path = root / "config" / "knowledge" / db_id / "prompt_template.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"db_id": owner or db_id, "section": section},
                                   allow_unicode=True), encoding="utf-8")
    return path


class TestRulesBlock:
    def test_renders_rules_and_codes(self):
        block = build_profile_rules_block({
            "query_rules": ["`t.d`는 'YYYYMMDD' 형식의 문자열 날짜다."],
            "code_values": {"t.st": ["1", "2", "9"]},
            "code_labels": {"t.st": {"1": "정상", "2": "장애"}, "t.yn": {"Y": "사용"}},
        })
        assert "### DB 쿼리 규칙\n  - `t.d`는" in block
        assert "  - t.st: 1=정상, 2=장애, 9\n" in block and "  - t.yn: Y=사용\n" in block

    @pytest.mark.parametrize(
        "path",
        sorted((REPO / "config" / "db_profiles").glob("polestar*.yaml")),
        ids=lambda p: p.name,
    )
    def test_existing_polestar_profiles_are_byte_identical(self, path):
        """폴스타 프로필에는 새 자산 키가 없다 → 블록 빈 문자열 · 관계 추가 0."""
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
        meta = {k: v for k, v in profile.items() if k != "source"}
        assert build_profile_rules_block(meta) == ""
        schema = {"tables": {"cmm_x": {"columns": []}}, "relationships": []}
        assert attach_profile_relationships(schema, meta) == 0
        assert schema["relationships"] == []


class TestRelationships:
    def test_attach_only_in_scope_with_key_spelling_and_marker(self):
        schema: dict[str, Any] = {
            "tables": {"app.T_SRV": {"columns": []}, "app.T_HW": {"columns": []}},
            "relationships": [{"from": "app.T_HW.hostNm", "to": "app.T_SRV.hostNm"}],
        }
        meta = {"relationships": [
            {"from": "t_hw.hostNm", "to": "t_srv.hostNm", "origin": "same_key"},  # 이미 있음
            {"from": "t_hw.grp", "to": "t_srv.grp", "origin": "same_key"},
            {"from": "t_part.grp", "to": "t_srv.grp", "origin": "inferred"},  # t_part 없음
            {"from": "t_hw.x", "to": "t_srv.x", "origin": "declared"},
        ]}

        assert attach_profile_relationships(schema, meta) == 2
        assert schema["relationships"][1:] == [
            {"from": "app.T_HW.grp", "to": "app.T_SRV.grp", "origin": "same_key"},
            {"from": "app.T_HW.x", "to": "app.T_SRV.x"},
        ]
        text = format_schema_text(schema)
        assert "app.T_HW.grp -> app.T_SRV.grp (추론 · 값 겹침 확인)" in text
        assert "app.T_HW.x -> app.T_SRV.x\n" in text + "\n"


class TestGeneratedAdapter:
    def test_owns_only_db_with_approved_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_section(tmp_path, "app_gen")
        _write_section(tmp_path, "app_wrong", owner="someone_else")

        bound = get_adapter("app_gen", set())
        assert isinstance(bound, BoundGeneratedTemplate) and bound.validator_checks() == []
        rendered = bound.system_template(None).format(
            schema="S", default_limit=10, structure_guide="G", db_engine_hint="H",
        )
        assert "G\n\n## DB 전용 규칙 (관리자 승인 생성본)\n\n### 업무 개요" in rendered
        assert "SELECT 문만 생성합니다" in rendered  # 범용 안전 규칙은 그대로
        assert get_adapter("app_wrong", set()) is None
        assert get_adapter("app_none", set()) is None
        assert isinstance(get_adapter("app_gen", {"app_gen"}), PolestarAdapter)  # 폴스타가 먼저

    def test_cache_follows_file_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _write_section(tmp_path, "app_gen")
        assert get_adapter("app_gen", set()) is not None
        path.unlink()
        assert get_adapter("app_gen", set()) is None

    def test_braces_are_escaped(self):
        template = compose_template("규칙 {x}")
        assert "규칙 {{x}}" in template
        assert template.format(schema="", default_limit=1, structure_guide="",
                               db_engine_hint="").count("규칙 {x}") == 1


class TestAlarmAssemblerGuard:
    async def test_single_and_multi_skip_polestar_alarm_for_generated_db(
        self, tmp_path, monkeypatch
    ):
        from src.nodes.multi_db_executor import _deterministic_alarm_sql_or_none
        from src.nodes.query_generator import _try_deterministic_alarm_single

        monkeypatch.chdir(tmp_path)
        _write_section(tmp_path, "app_gen")
        app_config = SimpleNamespace(
            text2sql=SimpleNamespace(alarm_deterministic=True),
            get_polestar_db_ids=lambda: set(),
        )
        state = {"routing_intent": "alarm_query", "active_db_id": "app_gen"}
        ctx = SimpleNamespace(app_config=app_config, adapter_db_ids=set(),
                              user_query="현재 활성 심각 알람", limit_value=10)
        run = SimpleNamespace(app_config=app_config, state=state)

        assert _try_deterministic_alarm_single(state, ctx) is None
        assert _deterministic_alarm_sql_or_none(run, db_engine="mariadb", db_id="app_gen") is None


class TestMultiPath:
    async def test_generated_section_applies_without_path_parity(self, tmp_path, monkeypatch):
        import src.nodes.multi_db_executor as mde

        monkeypatch.chdir(tmp_path)
        _write_section(tmp_path, "app_gen")

        async def _no_materials(_db_id, _cfg):
            return None

        async def _no_history(_db_id, _q, _cfg):
            return None

        monkeypatch.setattr(mde, "_load_schema_prompt_materials", _no_materials)
        monkeypatch.setattr(mde, "_select_query_history_examples", _no_history)
        schema = {
            "tables": {"t_srv": {"columns": [{"name": "hostNm", "type": "varchar"}]}},
            "relationships": [],
            "_structure_meta": {"query_rules": ["규칙 A"], "patterns": []},
        }

        prompt = await mde._build_multi_system_prompt(
            schema, {"original_query": "서버 목록"}, "서버 목록", 10, "mariadb", "app_gen", None,
        )

        assert "## DB 전용 규칙 (관리자 승인 생성본)" in prompt
        assert "### DB 쿼리 규칙\n  - 규칙 A" in prompt
