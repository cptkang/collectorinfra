"""폼필 피벗 공용 전처리(`src/db_adapters/polestar/form_fill_plan.py`) — plans/169 N1(a).

공통화 전 두 노드가 인라인으로 쓰던 식과 같은 결과를 내는지 고정한다(동작 불변 리팩터).
조립기 입력 전체의 단일/멀티 동일성은 `test_plan166_w0_form_fill_pivot_inputs.py`가 덮는다.
"""

from __future__ import annotations

import importlib

from src.db_adapters.polestar import form_fill_plan as ffp
from src.db_adapters.polestar.assembler import (
    MonthSeries,
    apply_capacity_scope_rule,
    apply_remark_server_name_rule,
)

_EAV = {"entity_table": "cmm_resource"}


def test_drop_llm_inferred_mappings_mutates_in_place_and_keeps_keys():
    mapping = {"A": "t.a", "B": "t.b", "C": None, "D": "EAV:X"}
    sources = {"A": "llm_inferred", "B": "synonym", "C": "llm_inferred", "D": "llm_inferred"}
    dropped = ffp.drop_llm_inferred_mappings(mapping, sources)
    assert dropped == ["A", "D"]
    assert mapping == {"A": None, "B": "t.b", "C": None, "D": None}
    assert ffp.drop_llm_inferred_mappings({"A": "t.a"}, None) == []


def test_month_series_rule_updates_matches_sequential_updates():
    rule_mapping = {"처리능력|(GB)": None, "비고": "t.desc", "사용률|M": None, "서버명": "t.name"}
    attr_rt = {"TOTALSIZE": "server.Memory"}
    ms = MonthSeries(measures=[], fields=["사용률|M"], resource_type="server.Memory")

    patch, updates = ffp.month_series_rule_updates(rule_mapping, attr_rt, _EAV, ms)

    # 공통화 전 인라인 식(단일 경로 기준)
    cap = apply_capacity_scope_rule(rule_mapping, attr_rt, ms.resource_type)
    remark = apply_remark_server_name_rule(rule_mapping, "cmm_resource")
    expected_mapping = dict(rule_mapping)
    expected_mapping.update(cap)
    expected_mapping.update(remark)
    expected_updates: dict = {"선행": None}
    expected_updates.update(cap)
    expected_updates.update({f: None for f in ms.fields})
    expected_updates.update({f: None for f in remark})

    got_mapping = dict(rule_mapping)
    got_mapping.update(patch)
    got_updates: dict = {"선행": None}
    got_updates.update(updates)
    assert list(got_mapping.items()) == list(expected_mapping.items())
    assert list(got_updates.items()) == list(expected_updates.items())
    assert got_mapping["처리능력|(GB)"] == "EAV:TotalSize"
    assert got_mapping["비고"] == "cmm_resource.name"


def test_split_pivot_mapping_matches_inline_partition():
    mapping = {
        "서버명": "cmm_resource.name",
        "사용률": "cmm_metric_stat_h.cpu_avg_val",
        "OS": "EAV:OSVerson",
        "제조사(모델명)": "EAV:Model",
        "공란": None,
    }
    regular, eav, metric = ffp.split_pivot_mapping(mapping, exclude_fields={"제조사(모델명)"})
    # 단일 경로 인라인 식
    assert regular == [
        (f, c) for f, c in mapping.items()
        if c and not c.startswith("EAV:") and "cmm_metric_stat" not in c.lower()
        and f != "제조사(모델명)"
    ]
    assert eav == [
        (f, c[4:]) for f, c in mapping.items()
        if c and c.startswith("EAV:") and f != "제조사(모델명)"
    ]
    assert metric == [("사용률", "cmm_metric_stat_h.cpu_avg_val")]
    # 멀티 경로(제외 없음)
    _r, eav_all, _m = ffp.split_pivot_mapping(mapping)
    assert eav_all == [("OS", "OSVerson"), ("제조사(모델명)", "Model")]


def test_plan_form_fill_pivot_drops_unknown_column_and_injects_identity():
    schema = {"tables": {"cmm_resource": {"columns": [{"name": "name"}, {"name": "hostname"}]}}}
    plan = ffp.plan_form_fill_pivot(
        [("구분", "cmm_resource.category")], [], [], _EAV,
        schema_info=schema, concat_eav=[], month_series=None, form_intent=True,
        query_time=None,
    )
    assert plan.dropped == [("구분", "cmm_resource.category")]
    assert plan.regular_entries == [
        ("server_name", "cmm_resource.name"), ("hostname", "cmm_resource.hostname"),
    ]
    assert plan.concat_eav is None and plan.month_measures is None and plan.period is None

    kept = ffp.plan_form_fill_pivot(
        [("서버명", "cmm_resource.name")], [], [], _EAV,
        schema_info=schema, concat_eav=[], month_series=None, form_intent=False,
        query_time=None,
    )
    assert kept.regular_entries == [("서버명", "cmm_resource.name")] and kept.dropped == []


def test_nodes_share_adapter_literals():
    """두 노드의 `_` 리터럴 이름은 어댑터 정본을 가리킨다(N1(b) — 사본 2벌 해소)."""
    qg = importlib.import_module("src.nodes.query_generator")
    mdb = importlib.import_module("src.nodes.multi_db_executor")
    for alias, name in (
        ("_ENTITY_RESOURCE_TYPE", "ENTITY_RESOURCE_TYPE"),
        ("_EAV_HOST_ATTRIBUTE", "EAV_HOST_ATTRIBUTE"),
        ("_EAV_LINK_COLUMN", "EAV_LINK_COLUMN"),
        ("_SCHEMA_EXAMPLE_TABLE", "SCHEMA_EXAMPLE_TABLE"),
        ("_FOREIGN_SCHEMA_PREFIX", "FOREIGN_SCHEMA_PREFIX"),
    ):
        assert getattr(qg, alias) == getattr(ffp, name) == getattr(mdb, alias)
