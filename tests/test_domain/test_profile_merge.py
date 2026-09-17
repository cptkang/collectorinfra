"""구조 프로필 필드 단위 병합 · 필드별 diff 순수 함수 테스트 (plans/104 G-4 · Wave 2 계약 §2).

픽스처 이름은 가상(`entity_t`·`attr_t` 등)이다 — 특정 운영 스키마를 가정하지 않는다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.domain.profile_merge import (
    FILL_IF_ABSENT_KEYS,
    LLM_PATTERN_LIST_KEYS,
    LLM_PATTERN_SCALAR_KEYS,
    LLM_TOP_LEVEL_KEYS,
    METADATA_KEYS,
    merge_profile,
    pattern_identity,
    profile_field_diff,
)


def _eav(**overrides: Any) -> dict[str, Any]:
    """수동 프로필 모양 EAV 패턴(수동 전용 하위 키 포함)."""
    pattern: dict[str, Any] = {
        "type": "eav",
        "entity_table": "entity_t",
        "config_table": "attr_t",
        "attribute_column": "attr_name",
        "value_column": "attr_value",
        "entity_columns": [{"name": "id", "description": "엔티티 ID"}],
        "excluded_join_columns": [
            {"table": "entity_t", "column": "legacy_id", "reason": "사용 안 함"},
        ],
        "value_joins": [
            {
                "eav_attribute": "HostKey",
                "eav_value_column": "attr_value",
                "entity_column": "host_key",
                "description": "수동 설명",
            },
        ],
        "direct_join": {"entity_column": "conf_id", "config_column": "owner_id"},
        "known_attributes": [{"name": "Kind", "synonyms": ["종류"]}],
    }
    pattern.update(overrides)
    return pattern


def _hierarchy(**overrides: Any) -> dict[str, Any]:
    pattern: dict[str, Any] = {
        "type": "hierarchy",
        "table": "node_t",
        "id_column": "id",
        "parent_column": "parent_id",
        "type_column": "node_kind",
        "name_column": "label",
    }
    pattern.update(overrides)
    return pattern


def _base() -> dict[str, Any]:
    """수동 프로필 모양 base(최상위 수동 전용 키 포함)."""
    return {
        "source": "manual",
        "patterns": [_eav(), _hierarchy()],
        "query_guide": "수동 안내",
        "query_examples": [{"q": "질의", "sql": "SELECT 1"}],
        "allowed_tables": ["entity_t", "attr_t"],
        "alarm_allowed_tables": ["event_t"],
        "column_synonyms": {"entity_t.label": ["이름"]},
        "entity_keys": {"host": "entity_t.host_key"},
    }


class TestConstants:
    def test_contract_constants(self):
        assert LLM_TOP_LEVEL_KEYS == ("patterns", "code_values")
        assert FILL_IF_ABSENT_KEYS == ("query_guide",)
        assert METADATA_KEYS == ("source", "environment")
        assert LLM_PATTERN_SCALAR_KEYS["eav"] == (
            "entity_table", "config_table", "join_condition", "attribute_column",
            "value_column", "lob_value_column", "lob_flag_column",
        )
        assert LLM_PATTERN_SCALAR_KEYS["hierarchy"] == (
            "table", "id_column", "parent_column", "type_column", "name_column",
        )
        assert dict(LLM_PATTERN_LIST_KEYS["eav"]) == {
            "value_joins": ("eav_attribute", "entity_column"),
        }


class TestPatternIdentity:
    def test_eav_ignores_schema_prefix_and_case(self):
        assert pattern_identity(_eav(entity_table="APP.ENTITY_T", config_table='"Attr_T"')) == (
            "eav", "entity_t", "attr_t",
        )

    def test_hierarchy_uses_table(self):
        assert pattern_identity(_hierarchy(table="app.Node_T", id_column="x")) == (
            "hierarchy", "node_t",
        )

    def test_other_is_canonical_json(self):
        a = pattern_identity({"type": "custom", "a": 1, "b": 2})
        b = pattern_identity({"b": 2, "a": 1, "type": "custom"})
        assert a == b and a[0] == "other"
        assert pattern_identity({"type": "custom", "a": 2}) != a


class TestMergeTopLevel:
    def test_manual_only_top_level_keys_preserved(self):
        base = _base()
        base["future_manual_key"] = {"x": 1}  # 목록 밖 신규 수동 키
        merged = merge_profile(base, {"patterns": [], "query_guide": "초안"}, local_sandbox=False)
        for key in (
            "query_examples", "allowed_tables", "alarm_allowed_tables",
            "column_synonyms", "entity_keys", "future_manual_key",
        ):
            assert merged[key] == base[key]

    def test_source_forced_manual_and_environment(self):
        base = {**_base(), "source": "auto", "environment": "local_sandbox"}
        prod = merge_profile(base, {}, local_sandbox=False)
        local = merge_profile(base, {}, local_sandbox=True)
        assert prod["source"] == "manual" and "environment" not in prod
        assert local["source"] == "manual" and local["environment"] == "local_sandbox"
        assert list(local)[:2] == ["source", "environment"]

    def test_query_guide_existing_value_preserved(self):
        merged = merge_profile(_base(), {"query_guide": "LLM 안내"}, local_sandbox=False)
        assert merged["query_guide"] == "수동 안내"

    @pytest.mark.parametrize("blank", ["", "   \n", None])
    def test_query_guide_blank_filled_from_draft(self, blank):
        base = {**_base(), "query_guide": blank}
        merged = merge_profile(base, {"query_guide": "LLM 안내"}, local_sandbox=False)
        assert merged["query_guide"] == "LLM 안내"

    def test_query_guide_absent_in_base_added(self):
        base = _base()
        del base["query_guide"]
        merged = merge_profile(base, {"query_guide": "LLM 안내"}, local_sandbox=False)
        assert merged["query_guide"] == "LLM 안내"

    def test_samples_and_unknown_draft_keys_not_written(self):
        draft = {
            "patterns": [],
            "query_guide": "x",
            "samples": [{"sql": "SELECT 1", "rows": [{"secret": "실데이터"}]}],
            "analysis_note": "초안 전용",
        }
        merged = merge_profile(None, draft, local_sandbox=False)
        assert "samples" not in merged and "analysis_note" not in merged

    def test_new_profile_from_draft(self):
        draft = {
            "patterns": [_hierarchy()],
            "query_guide": "안내",
            "code_values": {"node_t.node_kind": ["a"]},
        }
        merged = merge_profile(None, draft, local_sandbox=True)
        assert merged == {
            "source": "manual",
            "environment": "local_sandbox",
            "patterns": [_hierarchy()],
            "code_values": {"node_t.node_kind": ["a"]},
            "query_guide": "안내",
        }

    def test_inputs_not_mutated_and_result_is_deep_copy(self):
        base = _base()
        draft = {"patterns": [_eav(value_column="new_value")], "code_values": {"a.b": ["x"]}}
        base_snapshot, draft_snapshot = copy.deepcopy(base), copy.deepcopy(draft)
        merged = merge_profile(base, draft, local_sandbox=False)
        merged["patterns"][0]["known_attributes"].append({"name": "변조"})
        merged["allowed_tables"].append("변조")
        assert base == base_snapshot and draft == draft_snapshot

    def test_key_order_base_then_new(self):
        base = {"source": "manual", "allowed_tables": ["t"], "query_guide": "g"}
        merged = merge_profile(base, {"patterns": [], "code_values": {}}, local_sandbox=False)
        assert list(merged) == [
            "source", "allowed_tables", "query_guide", "patterns", "code_values",
        ]


class TestMergePatterns:
    def test_pattern_manual_subkeys_preserved(self):
        draft_pattern = {
            "type": "eav",
            "entity_table": "entity_t",
            "config_table": "attr_t",
            "attribute_column": "attr_name",
            "value_column": "attr_value",
            "known_attributes": [{"name": "LLM이 만든 값"}],
            "excluded_join_columns": [],
            "direct_join": {"entity_column": "x", "config_column": "y"},
            "entity_columns": [],
        }
        merged = merge_profile(_base(), {"patterns": [draft_pattern]}, local_sandbox=False)
        eav = merged["patterns"][0]
        base_eav = _eav()
        for key in ("known_attributes", "excluded_join_columns", "direct_join", "entity_columns"):
            assert eav[key] == base_eav[key]

    def test_scalar_llm_key_updated_only_when_draft_non_empty(self):
        draft_pattern = {
            "type": "eav",
            "entity_table": "entity_t",
            "config_table": "attr_t",
            "attribute_column": "",  # 빈 값 — base 유지
            "value_column": "short_value",  # 갱신
            "lob_value_column": None,  # 빈 값 — base에 없으니 추가도 안 됨
            "join_condition": "attr_t.owner_id = entity_t.id",  # 신규
        }
        merged = merge_profile(_base(), {"patterns": [draft_pattern]}, local_sandbox=False)
        eav = merged["patterns"][0]
        assert eav["attribute_column"] == "attr_name"
        assert eav["value_column"] == "short_value"
        assert "lob_value_column" not in eav
        assert eav["join_condition"] == "attr_t.owner_id = entity_t.id"

    def test_hierarchy_scalar_update(self):
        draft = {"patterns": [_hierarchy(name_column="title", type_column="  ")]}
        merged = merge_profile(_base(), draft, local_sandbox=False)
        hierarchy = merged["patterns"][1]
        assert hierarchy["name_column"] == "title"
        assert hierarchy["type_column"] == "node_kind"

    def test_value_joins_union_base_wins_on_conflict(self):
        draft_pattern = _eav(value_joins=[
            {  # base와 식별 키 충돌 — base 항목(수동 설명) 유지
                "eav_attribute": "HostKey",
                "eav_value_column": "other_value",
                "entity_column": "host_key",
                "description": "LLM 설명",
            },
            {  # 신규 — 추가
                "eav_attribute": "AddrKey",
                "eav_value_column": "attr_value",
                "entity_column": "addr",
            },
        ])
        merged = merge_profile(_base(), {"patterns": [draft_pattern]}, local_sandbox=False)
        joins = merged["patterns"][0]["value_joins"]
        assert joins == [
            _eav()["value_joins"][0],
            {"eav_attribute": "AddrKey", "eav_value_column": "attr_value", "entity_column": "addr"},
        ]

    def test_value_joins_empty_draft_keeps_base(self):
        merged = merge_profile(
            _base(), {"patterns": [_eav(value_joins=[])]}, local_sandbox=False
        )
        assert merged["patterns"][0]["value_joins"] == _eav()["value_joins"]

    def test_base_only_pattern_preserved_and_draft_only_added(self):
        new_eav = {
            "type": "eav", "entity_table": "item_t", "config_table": "item_attr_t",
            "attribute_column": "k", "value_column": "v",
        }
        merged = merge_profile(_base(), {"patterns": [new_eav]}, local_sandbox=False)
        assert merged["patterns"] == [_eav(), _hierarchy(), new_eav]

    def test_no_patterns_draft_keeps_manual_patterns(self):
        merged = merge_profile(_base(), {"patterns": []}, local_sandbox=False)
        assert merged["patterns"] == [_eav(), _hierarchy()]

    def test_identity_match_ignores_schema_prefix(self):
        draft_pattern = {
            "type": "eav", "entity_table": "APP.ENTITY_T", "config_table": "APP.ATTR_T",
            "value_column": "short_value",
        }
        merged = merge_profile(_base(), {"patterns": [draft_pattern]}, local_sandbox=False)
        assert len(merged["patterns"]) == 2
        assert merged["patterns"][0]["entity_table"] == "APP.ENTITY_T"  # 스칼라 LLM 키 갱신
        assert merged["patterns"][0]["known_attributes"] == _eav()["known_attributes"]

    def test_code_values_merged_by_column_key(self):
        base = {
            **_base(),
            "code_values": {"attr_t.attr_name": ["A", "B"], "node_t.node_kind": ["n"]},
        }
        draft = {"code_values": {"attr_t.attr_name": ["A", "B", "C"], "entity_t.state": ["on"]}}
        merged = merge_profile(base, draft, local_sandbox=False)
        assert merged["code_values"] == {
            "attr_t.attr_name": ["A", "B", "C"],
            "node_t.node_kind": ["n"],
            "entity_t.state": ["on"],
        }

    def test_code_values_absent_in_draft_keeps_base(self):
        base = {**_base(), "code_values": {"attr_t.attr_name": ["A"]}}
        merged = merge_profile(base, {"patterns": []}, local_sandbox=False)
        assert merged["code_values"] == {"attr_t.attr_name": ["A"]}


class TestFieldDiff:
    def test_identical_profiles_no_diff(self):
        assert profile_field_diff(_base(), copy.deepcopy(_base())) == []

    def test_new_profile_all_added(self):
        after = {"source": "manual", "patterns": [], "query_guide": "g"}
        diff = profile_field_diff(None, after)
        assert [(d["path"], d["change"]) for d in diff] == [
            ("patterns", "added"), ("query_guide", "added"), ("source", "added"),
        ]
        assert all(d["before"] is None for d in diff)

    def test_pattern_level_and_key_level_entries(self):
        before = _base()
        new_eav = {"type": "eav", "entity_table": "item_t", "config_table": "item_attr_t"}
        after = merge_profile(
            before,
            {
                "patterns": [_eav(value_column="short_value"), new_eav],
                "code_values": {"attr_t.attr_name": ["A"]},
            },
            local_sandbox=True,
        )
        diff = profile_field_diff(before, after)
        by_path = {d["path"]: d for d in diff}
        assert by_path["environment"]["change"] == "added"
        assert by_path["patterns[eav:entity_t/attr_t].value_column"] == {
            "path": "patterns[eav:entity_t/attr_t].value_column",
            "change": "changed",
            "before": "attr_value",
            "after": "short_value",
        }
        assert by_path["patterns[eav:item_t/item_attr_t]"]["change"] == "added"
        assert by_path["patterns[eav:item_t/item_attr_t]"]["after"] == new_eav
        assert by_path["code_values"]["change"] == "added"
        assert not any(p.startswith("patterns[hierarchy:") for p in by_path)

    def test_removed_pattern_and_value_join_items(self):
        before = _base()
        after = copy.deepcopy(before)
        after["patterns"] = [after["patterns"][0]]
        after["patterns"][0]["value_joins"].append(
            {"eav_attribute": "AddrKey", "entity_column": "addr"}
        )
        diff = profile_field_diff(before, after)
        assert [(d["path"], d["change"]) for d in diff] == [
            ("patterns[eav:entity_t/attr_t].value_joins[AddrKey/addr]", "added"),
            ("patterns[hierarchy:node_t]", "removed"),
        ]

    def test_code_values_key_level(self):
        before = {"code_values": {"a.b": ["1"], "c.d": ["x"]}}
        after = {"code_values": {"a.b": ["1", "2"], "e.f": ["y"]}}
        assert [(d["path"], d["change"]) for d in profile_field_diff(before, after)] == [
            ("code_values[a.b]", "changed"),
            ("code_values[c.d]", "removed"),
            ("code_values[e.f]", "added"),
        ]

    def test_deterministic_order_independent_of_key_order(self):
        before = _base()
        after = merge_profile(
            before,
            {
                "patterns": [_eav(value_column="v2"), _hierarchy(name_column="t2")],
                "query_guide": "",
            },
            local_sandbox=True,
        )
        after["allowed_tables"] = ["entity_t"]
        reordered_after = dict(reversed(list(after.items())))
        reordered_before = dict(reversed(list(before.items())))
        diff = profile_field_diff(before, after)
        paths = [d["path"] for d in diff]
        assert paths == sorted(paths)
        assert diff == profile_field_diff(reordered_before, reordered_after)

    def test_diff_values_are_copies(self):
        before = {"allowed_tables": ["a"]}
        after = {"allowed_tables": ["a", "b"]}
        diff = profile_field_diff(before, after)
        diff[0]["after"].append("변조")
        assert after["allowed_tables"] == ["a", "b"]
