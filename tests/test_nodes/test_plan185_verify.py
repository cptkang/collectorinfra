"""plans/185 W1 독립 검증 — 회수 규칙 동등성·단일 비트 동일·병합 경로 오채움 도달성.

1. 멀티 회수 규칙: 옛 인라인 식(13dc5a3f)과 공통 함수가 같은 입력에서 같은 목록(순서 포함)을 낸다.
   단, 옛 식과 다른 두 입력(결합 필드가 통계 칼럼 매핑 묶음에 든 경우 · 미매핑과 통계 묶음 중복)은
   새 규칙이 빼거나 한 번만 낸다 — 의도된 차이로 고정한다.
2. 단일 경로: 통계 칼럼 매핑이 없으면 회수 대상이 옛 식과 같다(옛 식 재현 대조).
3. 오채움 도달성: 2단·1단의 병합 마감(`_finalize_merged_rows`)은 종전 organized에
   `resolved_mapping: None`을 넣어 writer가 top-level `column_mapping`(field_mapper 원본 —
   파이프라인의 mapping_updates가 승격되지 않는다)으로 채웠다. 두 사용률 필드가 같은 통계 칼럼에
   매핑되면 역매핑이 마지막 필드로 덮여 CPU 칸에 메모리 값이 들어갔다.
   plans/185 W2: 병합 마감이 병합 행 키로 `build_resolved_mapping`(Layer 1)을 만들어 싣는다 —
   공유 통계 칼럼은 각자 제 값, 비공유·공유 일반 칼럼·미해결 매핑은 종전과 셀 단위로 같다.
   단일 task 마감(result_organizer의 resolved_mapping)은 종전부터 정상이다.
"""

from __future__ import annotations

import importlib
import io
import itertools
import re
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook

from src.db_adapters.polestar.form_fill_plan import select_metric_recovery_fields
from src.nodes.output_generator import _generate_document_file
from src.utils.column_matcher import build_resolved_mapping
from tests.test_nodes.test_plan166_w0_form_fill_pivot_inputs import (  # noqa: F401 — 픽스처
    _run_multi,
    _run_single,
    recorder,
)
from tests.test_nodes.test_plan185_single_metric_recovery import _case

# 패키지가 노드 함수를 재노출해 `from … import`가 함수를 잡는다 — 서브모듈로 받는다
agg_mod = importlib.import_module("src.orchestration.result_aggregator")


def _is_metric(field: str) -> bool:
    return "사용률" in field


# ── 1. 멀티 회수 규칙 — 옛 인라인 식과의 동등성 ─────────────────────────────────────


def _old_multi_rule(
    unmapped: list[str],
    metric_entries: list[tuple[str, str]],
    dropped: list[str],
    month: set[str],
    concat: set[str],
    applied: set[str],
) -> list[str]:
    """13dc5a3f `_multi_resource_pivot_result`의 회수 대상 식(그대로 옮김)."""
    _um = [f for f in unmapped if f not in month and f not in concat]
    out = [f for f in _um if _is_metric(f)]
    out += [f for f, _ in metric_entries if f not in month and _is_metric(f)]
    out += [
        f for f in dropped
        if f not in month and f not in concat and f not in out and _is_metric(f)
    ]
    return [f for f in out if f not in applied]


def _new_multi_rule(unmapped, metric_entries, dropped, month, concat, applied) -> list[str]:
    _um = [f for f in unmapped if f not in month and f not in concat]
    return select_metric_recovery_fields(
        _um, [f for f, _ in metric_entries], dropped,
        exclude=month | concat | applied, is_metric=_is_metric,
    )


_POOL = ["A 사용률", "B 사용률", "C 사용률", "비고", "M 사용률|M"]


def _subsets(pool: list[str], k: int) -> list[list[str]]:
    return [list(c) for r in range(k + 1) for c in itertools.combinations(pool, r)]


def test_multi_rule_matches_old_on_disjoint_inputs():
    """호출부가 실제로 만드는 모양(필드는 미매핑·통계 매핑·강등 중 한 곳에만 · 결합 필드는 통계
    묶음 밖)에서 새 규칙 = 옛 식(순서 포함)."""
    checked = 0
    month = {"M 사용률|M"}
    for unmapped in _subsets(_POOL, 2):
        rest = [f for f in _POOL if f not in unmapped]
        for metric in _subsets(rest, 2):
            rest2 = [f for f in rest if f not in metric]
            for dropped in _subsets(rest2, 1):
                for concat in ({"없음"}, {unmapped[0]} if unmapped else set()):
                    for applied in (set(), {"B 사용률"}):
                        entries = [(f, "cmm_metric_stat_m.avg_val") for f in metric]
                        old = _old_multi_rule(unmapped, entries, dropped, month, concat, applied)
                        new = _new_multi_rule(unmapped, entries, dropped, month, concat, applied)
                        assert new == old, (unmapped, metric, dropped, concat, applied)
                        checked += 1
    assert checked > 500


def test_multi_rule_intentional_differences():
    """옛 식과 다른 두 입력 — 새 규칙이 결합 필드를 빼고 중복을 한 번만 낸다.

    실효: 중복 alias 방지.
    """
    entries = [("제조사 모델 사용률", "cmm_metric_stat_m.avg_val")]
    concat = {"제조사 모델 사용률"}
    assert _old_multi_rule([], entries, [], set(), concat, set()) == ["제조사 모델 사용률"]
    assert _new_multi_rule([], entries, [], set(), concat, set()) == []

    dup = [("A 사용률", "cmm_metric_stat_m.avg_val")]
    assert _old_multi_rule(["A 사용률"], dup, [], set(), set(), set()) == ["A 사용률", "A 사용률"]
    assert _new_multi_rule(["A 사용률"], dup, [], set(), set(), set()) == ["A 사용률"]


# ── 2. 단일 — 통계 칼럼 매핑이 없으면 옛 식과 같다 ─────────────────────────────────


async def test_single_without_stat_mapping_matches_old_rule(recorder):  # noqa: F811
    case = _case("postgresql", "")
    case["mapping"] = {
        "서버명": "cmm_resource.name",
        "호스트명": "cmm_resource.hostname",
        "CPU 평균 사용률": None,
        "메모리 최대 사용률": None,
        "비고": None,
    }
    single = await _run_single(case, recorder)
    multi = await _run_multi(case, recorder)
    # 옛 단일 식: 매핑 None ∧ 월·결합·적용 오버라이드 밖 ∧ 사용률 판정 — 매핑 순서
    assert single["inputs"]["metric_fields"] == ["CPU 평균 사용률", "메모리 최대 사용률"]
    assert single["inputs"] == multi["inputs"]
    assert single["sql"] == multi["sql"]


async def test_single_shared_stat_column_recovers_both_once(recorder):  # noqa: F811
    """두 사용률 필드가 같은 통계 칼럼에 매핑돼도 각자 필드명 alias로 한 번씩 회수 — 단일 = 멀티."""
    case = _case("postgresql", "")
    case["mapping"] = {
        "서버명": "cmm_resource.name",
        "CPU 평균 사용률": "cmm_metric_stat_h.cpu_avg_val",
        "메모리 평균 사용률": "cmm_metric_stat_h.cpu_avg_val",
    }
    case["sources"] = {f: "synonym" for f in case["mapping"]}
    single = await _run_single(case, recorder)
    multi = await _run_multi(case, recorder)
    assert single["inputs"]["metric_fields"] == ["CPU 평균 사용률", "메모리 평균 사용률"]
    assert single["inputs"]["metric_fields"] == multi["inputs"]["metric_fields"]
    assert single["sql"] == multi["sql"]
    assert single["sql"].count('AS "CPU 평균 사용률"') == 1


# ── 3. 오채움 도달성 — 병합 마감 경로 ─────────────────────────────────────────────

_FIELDS = ["서버명", "CPU 평균 사용률", "메모리 평균 사용률"]
_SHARED_MAPPING = {
    "서버명": "cmm_resource.name",
    "CPU 평균 사용률": "cmm_metric_stat_m.avg_val",
    "메모리 평균 사용률": "cmm_metric_stat_m.avg_val",
}
# 결정적 피벗 결과 행 — alias = 필드명(CPU 12 · 메모리 77)
_ROWS = [{"서버명": "srv-a", "CPU 평균 사용률": 12.0, "메모리 평균 사용률": 77.0}]


def _template(fields: list[str] | None = None) -> tuple[bytes, dict[str, Any]]:
    fields = fields or _FIELDS
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for i, f in enumerate(fields, start=1):
        ws.cell(row=1, column=i, value=f)
    buf = io.BytesIO()
    wb.save(buf)
    structure = {"sheets": [{
        "name": "Sheet1", "title_text": "서버 사용률 현황", "data_start_row": 2,
        "formula_cells": [],
        "header_cells": [{"col": i, "value": f} for i, f in enumerate(fields, start=1)],
    }]}
    return buf.getvalue(), structure


def _cells(data: bytes) -> dict[str, Any]:
    ws = load_workbook(io.BytesIO(data))["Sheet1"]
    return {f: ws.cell(row=2, column=i).value for i, f in enumerate(_FIELDS, start=1)}


def _grid(data: bytes, n_cols: int, n_rows: int) -> list[list[Any]]:
    ws = load_workbook(io.BytesIO(data))["Sheet1"]
    return [
        [ws.cell(row=r, column=c).value for c in range(1, n_cols + 1)]
        for r in range(2, 2 + n_rows)
    ]


def _doc_state(organized: dict[str, Any]) -> dict[str, Any]:
    data, structure = _template()
    return {
        "organized_data": organized,
        "column_mapping": dict(_SHARED_MAPPING),
        "template_structure": structure,
        "uploaded_file": data,
        "parsed_requirements": {"output_format": "xlsx"},
    }


async def _merged_output_state(
    monkeypatch, rows: list[dict[str, Any]], mapping: dict[str, Any],
) -> dict[str, Any]:
    """병합 마감을 실제로 태워 output_generator 입력 state를 그대로 받는다."""
    captured: dict[str, Any] = {}

    async def _fake_output_generator(state, **_kw):
        captured.update(state)
        return {"final_response": "ok"}

    monkeypatch.setattr(agg_mod, "output_generator", _fake_output_generator)
    data, structure = _template(list(mapping))
    state = {
        "user_query": "양식 채워줘",
        "parsed_requirements": {"output_format": "xlsx"},
        "column_mapping": dict(mapping),
        "template_structure": structure,
        "uploaded_file": data,
    }
    await agg_mod._finalize_merged_rows(rows, state, llm=None, app_config=None)  # type: ignore[arg-type]
    return captured


def test_single_task_finalize_with_resolved_mapping_fills_correctly():
    """단일 task 마감(result_organizer가 resolved_mapping을 만든 경로)은 각 칸이 제 값이다."""
    resolved, unresolved = build_resolved_mapping(dict(_SHARED_MAPPING), set(_ROWS[0]))
    assert unresolved == []
    out = _generate_document_file(_doc_state({
        "rows": _ROWS, "column_mapping": dict(_SHARED_MAPPING), "resolved_mapping": resolved,
    }), "xlsx")
    assert out and "file_bytes" in out
    cells = _cells(out["file_bytes"])
    assert cells["CPU 평균 사용률"] == 12
    assert cells["메모리 평균 사용률"] == 77


async def test_merged_finalize_passes_no_resolved_mapping(monkeypatch):
    """병합 마감이 넘기는 writer 입력 — top-level 원본 매핑 + 병합 행 키로 푼 resolved_mapping.

    plans/185 W2로 resolved가 None에서 Layer 1 해석 결과로 바뀌어 단언을 고쳤다.
    """
    captured = await _merged_output_state(monkeypatch, _ROWS, _SHARED_MAPPING)
    assert captured["organized_data"]["resolved_mapping"] == {
        "서버명": "서버명",
        "CPU 평균 사용률": "CPU 평균 사용률",
        "메모리 평균 사용률": "메모리 평균 사용률",
    }
    assert captured["organized_data"]["column_mapping"] is None
    assert captured["column_mapping"] == _SHARED_MAPPING
    assert captured["template_structure"]["sheets"][0]["header_cells"][1]["value"] == (
        "CPU 평균 사용률"
    )


async def test_merged_finalize_without_mapping_or_rows_keeps_none(monkeypatch):
    """매핑이 없거나 병합 행이 없으면 resolved_mapping은 종전대로 None이다."""
    captured = await _merged_output_state(monkeypatch, _ROWS, {})
    assert captured["organized_data"]["resolved_mapping"] is None
    captured = await _merged_output_state(monkeypatch, [], _SHARED_MAPPING)
    assert captured["organized_data"]["resolved_mapping"] is None


async def test_merged_finalize_shared_stat_column_fills_each_own_value(monkeypatch):
    """병합 마감에서 같은 통계 칼럼을 공유한 두 사용률 필드가 각자 제 값으로 채워진다."""
    captured = await _merged_output_state(monkeypatch, _ROWS, _SHARED_MAPPING)
    out = _generate_document_file(captured, "xlsx")
    assert out and "file_bytes" in out
    cells = _cells(out["file_bytes"])
    assert cells["메모리 평균 사용률"] == 77
    assert cells["CPU 평균 사용률"] == 12  # 종전: 77(메모리 값)


# 비공유·공유 일반 칼럼 — 병합 마감 양식 채움이 종전(resolved None)과 셀 단위로 같아야 한다
_UNCHANGED_CASES: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {
    "direct_column": (
        {"호스트명": "cmm_resource.hostname", "IP": "cmm_resource.ip_address"},
        [{"hostname": "h1", "ip_address": "10.0.0.1", "_source_db": "polestar"},
         {"hostname": "h2", "ip_address": "10.0.0.2", "_source_db": "polestar"}],
    ),
    "eav_attr_key": (
        {"호스트명": "cmm_resource.hostname", "OS종류": "EAV:OSType"},
        [{"hostname": "h1", "OSType": "Linux"}, {"hostname": "h2", "OSType": "AIX"}],
    ),
    "eav_field_name_alias": (
        {"호스트명": "cmm_resource.hostname", "OS종류": "EAV:OSType"},
        [{"hostname": "h1", "OS종류": "Linux"}, {"hostname": "h2", "OS종류": "AIX"}],
    ),
    "mapping_none": (
        {"호스트명": "cmm_resource.hostname", "비고": None},
        [{"hostname": "h1", "비고": "x"}, {"hostname": "h2", "비고": None}],
    ),
    "shared_plain_column": (
        {"호스트명": "cmm_resource.hostname", "서버 호스트": "cmm_resource.hostname"},
        [{"hostname": "h1"}, {"hostname": "h2"}],
    ),
    "name_to_server_name": (
        {"서버명": "cmm_resource.name", "호스트명": "cmm_resource.hostname"},
        [{"server_name": "s1", "hostname": "h1"}, {"server_name": "s2", "hostname": "h2"}],
    ),
    "camel_key": (
        {"호스트명": "cmm_resource.hostname", "시리얼": "cmm_resource.serial_no"},
        [{"hostname": "h1", "serialNo": "S1"}, {"hostname": "h2", "serialNo": "S2"}],
    ),
    "unresolved_keeps_original": (
        {"호스트명": "cmm_resource.hostname", "시리얼": "cmm_resource.sn_code"},
        [{"hostname": "h1", "제조번호": "S1"}, {"hostname": "h2", "제조번호": "S2"}],
    ),
}


@pytest.mark.parametrize("name", sorted(_UNCHANGED_CASES))
async def test_merged_finalize_non_shared_cells_unchanged(monkeypatch, name):
    """비공유·공유 일반 칼럼·미해결 매핑은 새 resolved와 종전(None)의 채움이 셀 단위로 같다."""
    mapping, rows = _UNCHANGED_CASES[name]
    captured = await _merged_output_state(monkeypatch, rows, mapping)
    assert captured["organized_data"]["resolved_mapping"] is not None
    new = _generate_document_file(captured, "xlsx")
    old_state = {
        **captured,
        "organized_data": {**captured["organized_data"], "resolved_mapping": None},
    }
    old = _generate_document_file(old_state, "xlsx")
    assert new and old and "file_bytes" in new and "file_bytes" in old
    n_cols, n_rows = len(mapping), len(rows)
    assert _grid(new["file_bytes"], n_cols, n_rows) == _grid(old["file_bytes"], n_cols, n_rows)


async def test_multi_pivot_shared_stat_column_fills_each_own_value(recorder, monkeypatch):  # noqa: F811
    """멀티 결정적 피벗 — 공유 통계 칼럼 두 필드가 각자 alias로 SELECT되고, 그 행을 병합 마감에
    넣으면(다른 task 행과 서버 키로 병합) 각자 제 값으로 채워진다."""
    case = _case("postgresql", "")
    mapping = {
        "서버명": "cmm_resource.name",
        "CPU 평균 사용률": "cmm_metric_stat_h.cpu_avg_val",
        "메모리 평균 사용률": "cmm_metric_stat_h.cpu_avg_val",
    }
    case["mapping"] = mapping
    case["sources"] = {f: "synonym" for f in mapping}
    multi = await _run_multi(case, recorder)
    aliases = re.findall(r'AS "([^"]+)"', multi["sql"])
    for f in ("CPU 평균 사용률", "메모리 평균 사용률"):
        assert aliases.count(f) == 1, (f, aliases)
    # 실행 결과 행 — SELECT alias 그대로(서버 키 + 두 사용률 각자 값)
    values = {"CPU 평균 사용률": 12.0, "메모리 평균 사용률": 77.0}
    form_row = {a: values.get(a) for a in aliases}
    form_row["서버명"] = "srv-a"
    tasks = [
        {"task_id": "t1", "order": 1, "agent": "data_query"},
        {"task_id": "t2", "order": 2, "agent": "alarm_query"},
    ]
    results = {
        "t1": {"query_results": [form_row]},
        "t2": {"query_results": [{"server_name": "srv-a", "alarm_count": 3}]},
    }
    merged = agg_mod._merge_task_results_by_identity(tasks, results)
    assert merged and merged[0]["CPU 평균 사용률"] == 12.0
    captured = await _merged_output_state(monkeypatch, merged, mapping)
    out = _generate_document_file(captured, "xlsx")
    assert out and "file_bytes" in out
    assert _grid(out["file_bytes"], 3, 1) == [["srv-a", 12, 77]]


# ── 4. W2 재검증 — 정확 키 전환이 writer 선택을 바꾸는 입력 · Word 양식 ─────────────────


async def _new_old_grids(monkeypatch, mapping, rows):
    captured = await _merged_output_state(monkeypatch, rows, mapping)
    new = _generate_document_file(captured, "xlsx")
    old = _generate_document_file({
        **captured,
        "organized_data": {**captured["organized_data"], "resolved_mapping": None},
    }, "xlsx")
    assert new and old and "file_bytes" in new and "file_bytes" in old
    n_cols, n_rows = len(mapping), len(rows)
    return _grid(new["file_bytes"], n_cols, n_rows), _grid(old["file_bytes"], n_cols, n_rows)


async def test_mapping_key_and_alias_key_both_present_picks_same_as_old(monkeypatch):
    """매핑 칼럼 키와 필드명 alias 키가 한 행에 함께 있고 값이 다르면 — 새 resolved도 종전 writer도
    매핑 칼럼 키를 고른다(Layer 1 매핑 값 우선 = writer 1·2단계)."""
    mapping = {"호스트명": "cmm_resource.hostname", "OS종류": "EAV:OSType"}
    rows = [{"hostname": "h1", "OSType": "Linux", "OS종류": "WRONG"}]
    new, old = await _new_old_grids(monkeypatch, mapping, rows)
    assert new == old == [["h1", "Linux"]]


async def test_shared_stat_column_with_generic_key_present_same_as_old(monkeypatch):
    """공유 통계 칼럼의 일반 키(avg_val)가 행에 있으면 두 필드 모두 그 값 — 종전과 같다(이번 수정은
    alias 피벗 행만 바꾼다)."""
    mapping = dict(_SHARED_MAPPING)
    rows = [{"서버명": "srv-a", "avg_val": 5.0,
             "CPU 평균 사용률": 12.0, "메모리 평균 사용률": 77.0}]
    new, old = await _new_old_grids(monkeypatch, mapping, rows)
    assert new == old == [["srv-a", 5, 5]]


def _docx_template(fields: list[str]) -> tuple[bytes, dict[str, Any]]:
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=2, cols=len(fields))
    for i, f in enumerate(fields):
        table.rows[0].cells[i].text = f
    buf = io.BytesIO()
    doc.save(buf)
    structure = {"tables": [{"index": 0, "headers": fields, "has_placeholder_cells": False}],
                 "placeholders": []}
    return buf.getvalue(), structure


def _docx_grid(data: bytes) -> list[list[str]]:
    from docx import Document

    table = Document(io.BytesIO(data)).tables[0]
    return [[c.text for c in r.cells] for r in table.rows[1:]]


async def _docx_new_old(monkeypatch, mapping, rows):
    captured = await _merged_output_state(monkeypatch, rows, mapping)
    data, structure = _docx_template(list(mapping))
    base = {**captured, "template_structure": structure, "uploaded_file": data,
            "parsed_requirements": {"output_format": "docx"}}
    new = _generate_document_file(base, "docx")
    old = _generate_document_file({
        **base, "organized_data": {**base["organized_data"], "resolved_mapping": None},
    }, "docx")
    assert new and old and "file_bytes" in new and "file_bytes" in old
    return _docx_grid(new["file_bytes"]), _docx_grid(old["file_bytes"])


async def test_word_merged_shared_stat_column_fills_each_own_value(monkeypatch):
    """Word 양식 병합 마감 — 공유 통계 칼럼 두 필드가 각자 제 값(종전: Word writer는 역매핑 폴백이
    없어 alias 피벗 행의 세 칸 모두 빈칸 — 서버명(`cmm_resource.name`)도 못 찾았다)."""
    new, old = await _docx_new_old(monkeypatch, dict(_SHARED_MAPPING), _ROWS)
    assert new == [["srv-a", "12.0", "77.0"]]
    assert old == [["", "", ""]]


async def test_word_merged_non_shared_alias_row_now_filled(monkeypatch):
    """Word 양식 병합 마감 — 비공유 필드라도 행 키가 필드명 alias뿐이면 종전은 빈칸, 이제 채운다.

    공유 칼럼 밖의 행동 변화다(Excel은 writer 역매핑 폴백으로 종전부터 채웠다). 그래프 경로
    (result_organizer resolved)와 같은 결과로 맞춰진 것이다 — 변화 범위를 고정한다.
    """
    mapping = {"호스트명": "cmm_resource.hostname", "OS종류": "EAV:OSType"}
    rows = [{"hostname": "h1", "OS종류": "Linux"}]
    new, old = await _docx_new_old(monkeypatch, mapping, rows)
    assert new == [["h1", "Linux"]]
    assert old == [["h1", ""]]
