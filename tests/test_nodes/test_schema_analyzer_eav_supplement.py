"""EAV 동반 테이블 자동 보충 로직 검증 테스트.

_supplement_eav_tables()가 수동 프로필의 EAV 패턴을 기반으로
entity 테이블이 선택되었을 때 config 테이블을 자동 포함하는지 검증한다.
"""

import pytest

from src.nodes.schema_analyzer import _supplement_eav_tables


class TestSupplementEavTables:
    """_supplement_eav_tables() 함수 단위 테스트."""

    def test_entity_selected_adds_config(self, tmp_path, monkeypatch):
        """entity 테이블이 선택되면 config 테이블이 자동 추가된다."""
        # 수동 프로필 준비
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: eav\n"
            "    entity_table: cmm_resource\n"
            "    config_table: core_config_prop\n"
            "    attribute_column: name\n"
            "    value_column: stringvalue_short\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.cmm_resource"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert "polestar.cmm_resource" in result
        assert "polestar.core_config_prop" in result

    def test_config_already_selected_no_duplicate(self, tmp_path, monkeypatch):
        """config 테이블이 이미 선택되어 있으면 중복 추가하지 않는다."""
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: eav\n"
            "    entity_table: cmm_resource\n"
            "    config_table: core_config_prop\n"
            "    attribute_column: name\n"
            "    value_column: stringvalue_short\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.cmm_resource", "polestar.core_config_prop"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert result.count("polestar.core_config_prop") == 1

    def test_no_entity_selected_no_change(self, tmp_path, monkeypatch):
        """entity 테이블이 선택되지 않았으면 변경 없다."""
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: eav\n"
            "    entity_table: cmm_resource\n"
            "    config_table: core_config_prop\n"
            "    attribute_column: name\n"
            "    value_column: stringvalue_short\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.other_table"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop", "polestar.other_table"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert result == ["polestar.other_table"]

    def test_no_manual_profile_no_change(self):
        """수동 프로필이 없으면 원본 그대로 반환한다."""
        relevant = ["polestar.cmm_resource"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "nonexistent_db_id")

        assert result == ["polestar.cmm_resource"]

    def test_no_eav_pattern_no_change(self, tmp_path, monkeypatch):
        """EAV 패턴이 아닌 프로필이면 변경 없다."""
        profiles_dir = tmp_path / "config" / "db_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "test_db.yaml").write_text(
            "source: manual\n"
            "patterns:\n"
            "  - type: hierarchy\n"
            "    table: cmm_resource\n"
            "    id_column: id\n"
            "    parent_column: parent_resource_id\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        relevant = ["polestar.cmm_resource"]
        all_tables = ["polestar.cmm_resource", "polestar.core_config_prop"]

        result = _supplement_eav_tables(relevant, all_tables, "test_db")

        assert result == ["polestar.cmm_resource"]
