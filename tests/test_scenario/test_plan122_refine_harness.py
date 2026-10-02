"""plans/122 하네스 보완(refine) — 카탈로그 v2 이관이 하네스 공백으로 옮기지 못한 항목.

고정하는 계약:
  1. H-4 다단 머리글 — `file.header_row: N` · `file.header_rows: [N, …]`(1-based).
     이름 규칙 `상위/하위`(병합 범위 좌상단 값으로 빈 칸을 채우고 세로 병합·빈 값은 뺀다 ·
     공백 접기). `columns` · `filled_rows` · `filled_columns` · `optional_columns` · 값 단언(H-4)이
     모두 그 머리글로 본다. 선언이 없으면 종전(첫 행)과 같다. 저장소 양식(CPU·메모리·서버 목록)에
     카탈로그 H-10·H-15·H-16 이 쓰는 이름이 실제로 있다.
  2. `value_range` 별칭 목록 `[{columns: [별칭…], range: [lo, hi]}]` — `result`·`file` 공통 ·
     적은 순서의 첫 별칭 · 하나도 없으면 머리글과 함께 불합격 · 종전 매핑 형식은 기대값·상세가
     그대로다. 로더 검증.
  3. J-4 재판정 복원 불가 — 칸이 **없는** 행(옛 run)은 그 칸을 보는 단언을 보류하고, 칸이
     **있는데 빈** 행은 판정한다. SQL 은 run 단위(`executed_sqls`·`row_counts_by_db` 칸 유무)로
     가른다.
  4. J-4 송신 계약 변경 — 턴 `auth` · 시나리오 `upload_generate` 를 과거 run 이 그 방식으로
     보내지 않았으면 비교에서 뺀다(같은 카탈로그 지문 · 새 방식의 거절 상태면 비교한다).
  5. `row_is_invalid` — T-c 이후 행(`invalid_reason` 칸 있음)은 판정값만 본다
     (401 을 기대한 가드 턴이 무효로 빠지지 않는다).
전부 무과금이다(LLM·DB·서버 0).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import REPO_ROOT, rejudge
from scripts.scenario.assertions import (
    Observation,
    Verdict,
    evaluate_turn,
    row_is_invalid,
)
from scripts.scenario.catalog import (
    Catalog,
    Group,
    Scenario,
    Turn,
    judgement_digest,
    load_catalog,
)
from tests.test_scenario.conftest import GOOD_GROUP, write

openpyxl = pytest.importorskip("openpyxl")

GROUP = Group(id="T", name="t", latency_target_ms=1000)


# --- 1. H-4 다단 머리글 -------------------------------------------------------------------

def _judge_file(file_spec: dict[str, Any], artifact: Path) -> Verdict:
    sc = Scenario(id="T-01", group="T", plans=[122], title="t",
                  turns=[Turn(send={"query": "q"}, expect={"file": file_spec})])
    obs = Observation(status="completed", http_status=200, has_file=True, artifacts=[str(artifact)])
    return evaluate_turn(sc, 1, sc.turns[0], obs, GROUP)


def _two_level(path: Path, data: list[list[Any]]) -> Path:
    """1행 제목 · 3·4행 2단 머리글 양식(CPU_양식 축소판) + 5행부터 데이터."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["16. IT장비 사용현황", None, None, None, None, None])
    sheet.append([None] * 6)
    sheet.append(["구분", "호스트명", "처리능력", "월중평균\n사용률", None, "비고"])
    sheet.append(["분류", None, "(TPMC)", "M", "M+1", None])
    for row in data:
        sheet.append(row)
    for cells in ("B3:B4", "D3:E3", "F3:F4"):
        sheet.merge_cells(cells)
    book.save(path)
    return path


EXPECTED_HEADER = ["구분/분류", "호스트명", "처리능력/(TPMC)", "월중평균 사용률/M",
                   "월중평균 사용률/M+1", "비고"]


def test_h4_two_level_header_names(tmp_path: Path) -> None:
    """이름 규칙 - 가로 병합 상위 채움 · 세로 병합 중복 제거 · 병합 아닌 두 칸은 `상위/하위` ·
    공백 접기."""
    path = _two_level(tmp_path / "f.xlsx", [["A", "web-01", None, 10, 20, "x"]])
    view, problem = rejudge_view(path, {"header_rows": [3, 4]})
    assert problem is None
    assert view is not None and view[0] == EXPECTED_HEADER
    assert view[1][1] == "web-01"                  # 데이터는 마지막 머리글 행 다음부터


def rejudge_view(path: Path, spec: dict[str, Any]) -> tuple[Any, Any]:
    from scripts.scenario.assertions import _header_view, _read_xlsx

    sheets, _ = _read_xlsx(path)
    assert sheets is not None
    name = next(iter(sheets))
    return _header_view(path, name, sheets[name], spec)


def test_h4_all_file_subkeys_use_the_declared_header(tmp_path: Path) -> None:
    """columns · filled_rows · filled_columns · optional_columns · value_range · empty_columns ·
    unique_by · columns_differ · column_equals 가 모두 2단 머리글 이름으로 합격한다."""
    path = _two_level(tmp_path / "ok.xlsx", [
        ["A", "web-01", None, 10, 20.5, "x"],
        ["A", "web-02", None, 99.9, 100.5, None],
    ])
    verdict = _judge_file({
        "header_rows": [3, 4],
        "columns": EXPECTED_HEADER,
        "filled_rows": {"min": 2},
        "filled_columns": ["호스트명", "월중평균 사용률/M"],
        "optional_columns": ["비고"],
        "value_range": {"월중평균 사용률/M": [0, 101], "월중평균 사용률/M+1": [0, 101]},
        "empty_columns": ["처리능력/(TPMC)"],
        "unique_by": ["호스트명"],
        "columns_differ": [["구분/분류", "호스트명"]],
        "column_equals": {"구분/분류": "A"},
    }, path)
    assert verdict.failures == [] and verdict.func == "pass"


def test_h4_two_level_header_failures_carry_composed_names(tmp_path: Path) -> None:
    """빈 월 열(값 0개) · 채워진 공란 열 → 불합격. 상세는 합성한 이름으로 나온다(H-10 실측 모양)."""
    path = _two_level(tmp_path / "bad.xlsx", [["A", "web-01", "31.1 GB", None, None, "x"]])
    verdict = _judge_file({
        "header_rows": [3, 4],
        "value_range": {"월중평균 사용률/M": [0, 101]},
        "empty_columns": ["처리능력/(TPMC)"],
    }, path)
    by_key = {f.key: f for f in verdict.failures}
    assert set(by_key) == {"file.value_range", "file.empty_columns"}
    assert by_key["file.value_range"].actual["checked"] == 0
    assert by_key["file.empty_columns"].actual["filled_rows"] == 1
    assert by_key["file.empty_columns"].actual["empty_by_column"] == {"처리능력/(TPMC)": "0/1"}


def test_h4_filled_rows_counts_declared_columns_under_header(tmp_path: Path) -> None:
    path = _two_level(tmp_path / "rows.xlsx", [
        ["A", "web-01", "8 GB", None, None, None],
        ["A", "web-02", None, None, None, None],
    ])
    ok = _judge_file({"header_rows": [3, 4], "filled_columns": ["처리능력/(TPMC)"],
                      "filled_rows": {"min": 1}}, path)
    assert ok.failures == []
    bad = _judge_file({"header_rows": [3, 4], "filled_columns": ["처리능력/(TPMC)"],
                       "filled_rows": {"min": 2}}, path)
    assert [f.key for f in bad.failures] == ["file.filled_rows.min"]
    assert bad.failures[0].actual["empty_by_column"] == {"처리능력/(TPMC)": "1/2"}


def test_h4_single_header_row(tmp_path: Path) -> None:
    """`header_row: N` 은 한 줄 머리글 - 가로 병합이면 같은 이름이 이어진다(첫 열이 이긴다).

    그 아래 행은 전부 데이터다 - 2단 양식에 한 줄만 선언하면 하위 머리글 행(`M`)이 데이터로 읽혀
    값 단언에 걸린다(선언을 양식에 맞춰야 한다).
    """
    path = _two_level(tmp_path / "two.xlsx", [["A", "web-01", None, 10, 20, None]])
    view, _ = rejudge_view(path, {"header_row": 3})
    assert view[0] == ["구분", "호스트명", "처리능력", "월중평균 사용률", "월중평균 사용률", "비고"]
    wrong = _judge_file({"header_row": 3, "value_range": {"월중평균 사용률": [0, 100]}}, path)
    assert wrong.failures[0].actual["examples"] == ["M"]

    book = openpyxl.Workbook()
    sheet = book.active
    for row in (["제목"], [None], ["호스트명", "CPU 평균"], ["web-01", 12.5], ["web-02", 99]):
        sheet.append(row)
    book.save(tmp_path / "one.xlsx")
    verdict = _judge_file({"header_row": 3, "columns": ["호스트명", "CPU 평균"],
                           "filled_rows": {"min": 2}, "value_range": {"CPU 평균": [0, 100]}},
                          tmp_path / "one.xlsx")
    assert verdict.failures == []


def test_h4_undeclared_keeps_first_row_header(tmp_path: Path) -> None:
    """선언이 없으면 종전대로 첫 행이 머리글이다.

    제목 행 양식에서 열을 찾지 못한다(종전 불합격 그대로).
    """
    path = _two_level(tmp_path / "old.xlsx", [["A", "web-01", None, 10, 20, None]])
    verdict = _judge_file({"columns": ["호스트명"]}, path)
    assert [(f.key, f.expected, f.actual) for f in verdict.failures] == [
        ("file.columns", "호스트명", ["16. IT장비 사용현황"])]


def test_h4_header_row_beyond_sheet_fails(tmp_path: Path) -> None:
    path = _two_level(tmp_path / "short.xlsx", [])
    verdict = _judge_file({"header_rows": [4, 9], "columns": ["호스트명"]}, path)
    assert [f.key for f in verdict.failures] == ["file.header_rows"]
    assert verdict.failures[0].actual == {"reason": "머리글 행이 시트 행 수를 넘는다",
                                          "sheet_rows": 4}


#: 카탈로그 시나리오 → 그 `file` 단언이 부르는 열 이름을 저장소 양식에서 찾는다.
_CATALOG_FORMS = ("H-10", "H-15", "H-16")


def _file_column_refs(spec: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("columns", "filled_columns", "optional_columns", "empty_columns"):
        refs.update(str(c) for c in spec.get(key) or [])
    ranges = spec.get("value_range") or {}
    refs.update(str(c) for c in ranges) if isinstance(ranges, dict) else None
    return refs


@pytest.mark.parametrize("scenario_id", _CATALOG_FORMS)
def test_h4_catalog_names_exist_in_repo_templates(scenario_id: str) -> None:
    """카탈로그가 적은 이름이 업로드 양식(저장소 추적 파일)의 합성 머리글에 실제로 있다."""
    scenario = load_catalog().by_id(scenario_id)
    assert scenario is not None and scenario.upload
    spec = scenario.turns[0].expect["file"]
    view, problem = rejudge_view(REPO_ROOT / scenario.upload, spec)
    assert problem is None
    missing = _file_column_refs(spec) - set(view[0])
    assert not missing, (missing, view[0])


def _filled_copy(template: Path, target: Path, rows: dict[str, list[Any]]) -> Path:
    """저장소 양식 사본의 7행부터 {합성 머리글 이름: 값} 을 채운다(제품 산출물 모양)."""
    shutil.copy(template, target)
    book = openpyxl.load_workbook(target)
    sheet = book.worksheets[0]
    view, _ = rejudge_view(template, {"header_rows": [5, 6]})
    header = view[0]
    for offset, values in enumerate(zip(*rows.values())):
        for name, value in zip(rows, values):
            sheet.cell(row=7 + offset, column=header.index(name) + 1, value=value)
    book.save(target)
    return target


def test_h4_h16_catalog_entry_on_real_template(tmp_path: Path) -> None:
    """H-16 실제 카탈로그 기대값 - 5열 공란이면 합격 · 도입일자가 epoch ms 로 차면 불합격.

    불합격 모양은 run 20260923 산출물 실측이다.
    """
    scenario = load_catalog().by_id("H-16")
    assert scenario is not None and scenario.upload
    template = REPO_ROOT / scenario.upload
    base: dict[str, list[Any]] = {"서버명": ["cob0-peawao01", "cob0-peaweo01"],
                                  "IP": ["10.0.0.1", "10.0.0.2"]}
    spec = {"file": scenario.turns[0].expect["file"]}
    turn = Turn(send={"query": "q"}, expect=spec)

    def judge(path: Path) -> list[str]:
        obs = Observation(status="completed", http_status=200, has_file=True,
                          artifacts=[str(path)])
        return [f.key for f in evaluate_turn(scenario, 1, turn, obs, GROUP).failures]

    assert judge(_filled_copy(template, tmp_path / "ok.xlsx", base)) == []
    epoch: dict[str, list[Any]] = {**base, "도입일자": [1677568395200, 1677568396856]}
    assert judge(_filled_copy(template, tmp_path / "bad.xlsx", epoch)) == ["file.empty_columns"]


# --- 2. value_range 별칭 목록 ---------------------------------------------------------------

def _judge_result(spec: dict[str, Any], columns: list[str], rows: list[list[Any]]) -> Verdict:
    sc = Scenario(id="T-01", group="T", plans=[122], title="t",
                  turns=[Turn(send={"query": "q"}, expect={"result": spec})])
    obs = Observation(status="completed", http_status=200, result={
        "status": "ok", "columns": columns, "rows": [dict(zip(columns, r)) for r in rows],
        "total_rows": len(rows), "truncated": False, "reason": None})
    return evaluate_turn(sc, 1, sc.turns[0], obs, GROUP)


ALIASES = [{"columns": ["cpu_avg_utilization", "cpu_avg_percent", "cpu_avg", "cpu_avg_usage"],
            "range": [0, 100]}]


@pytest.mark.parametrize("column", ["cpu_avg_utilization", "cpu_avg_percent", "cpu_avg",
                                    "cpu_avg_usage"])
def test_value_range_alias_any_observed_name_passes(column: str) -> None:
    verdict = _judge_result({"value_range": ALIASES}, ["server_name", column],
                            [["a", 67.88], ["b", 49.82]])
    assert verdict.failures == []


def test_value_range_alias_out_of_range_reports_resolved_column() -> None:
    verdict = _judge_result({"value_range": ALIASES}, ["server_name", "cpu_avg"],
                            [["a", 67.88], ["b", 250]])
    [failure] = verdict.failures
    assert failure.key == "result.value_range" and failure.expected == ALIASES[0]
    assert failure.actual["column"] == "cpu_avg" and failure.actual["out_of_range"] == 1


def test_value_range_alias_first_listed_wins() -> None:
    """두 별칭이 다 있으면 목록에서 먼저 적은 것을 본다(머리글 순서가 아니다)."""
    verdict = _judge_result({"value_range": [{"columns": ["b", "a"], "range": [0, 1]}]},
                            ["a", "b"], [[500, 0.5]])
    assert verdict.failures == []


def test_value_range_alias_none_present_fails_with_header() -> None:
    verdict = _judge_result({"value_range": ALIASES}, ["server_name", "avg_cpu"], [["a", 1]])
    [failure] = verdict.failures
    assert failure.actual["missing"] == "헤더에 없음"
    assert failure.actual["header"] == ["server_name", "avg_cpu"]
    assert failure.actual["columns"] == ALIASES[0]["columns"]


def test_value_range_legacy_mapping_unchanged() -> None:
    """종전 형식의 기대값·상세 모양은 그대로다(`{열: [lo, hi]}` · `column` 칸)."""
    verdict = _judge_result({"value_range": {"cpu": [0, 100]}}, ["cpu"], [[101]])
    [failure] = verdict.failures
    assert failure.expected == {"cpu": [0, 100]}
    assert failure.actual == {"column": "cpu", "checked": 1, "non_numeric": 0, "out_of_range": 1,
                              "examples": ["101"]}


def test_value_range_alias_in_file(tmp_path: Path) -> None:
    path = _two_level(tmp_path / "alias.xlsx", [["A", "web-01", None, 10, 20, None]])
    ok = _judge_file({"header_rows": [3, 4], "value_range": [
        {"columns": ["없는 열", "월중평균 사용률/M"], "range": [0, 100]}]}, path)
    assert ok.failures == []


def test_loader_accepts_alias_list_and_header_rows(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        "        expect: {status: completed}",
        "        expect: {result: {value_range: [{columns: [a, b], range: [0, 100]}]}, "
        "file: {header_rows: [5, 6], value_range: [{columns: [c], range: [0, 1]}], "
        "empty_columns: ['x/y']}}"))
    catalog = load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)
    assert catalog.scenarios[0].turns[0].expect["file"]["header_rows"] == [5, 6]


# --- 3·4. J-4 재판정 복원 불가 · 송신 계약 ----------------------------------------------------

META: dict[str, Any] = {"run_id": "20260914-000000", "env": "closed", "mode": "run", "commit": "x",
                        "dirty": False}


def _catalog(*scenarios: Scenario) -> Catalog:
    return Catalog(groups={"T": GROUP}, scenarios=list(scenarios), profiles={"baseline": {}})


def _scenario(sid: str, expect: dict[str, Any], **kw: Any) -> Scenario:
    auth = kw.pop("auth", None)
    return Scenario(id=sid, group="T", plans=[122], title="t",
                    turns=[Turn(send={"query": "q"}, expect=expect, auth=auth)], **kw)


def _row(sid: str, **kw: Any) -> dict[str, Any]:
    """새 러너 행(칸 전부 있음)의 최소 모양."""
    base: dict[str, Any] = {
        "scenario_id": sid, "turn": 1, "repeat": 0, "profile": "baseline", "mode": "run",
        "func_verdict": "pass", "failed_assertions": [], "manual_notes": [],
        "response_mode": "answer", "response_text": "정상 응답", "status": "completed",
        "sse_events": ["done"], "executed_sql": None, "row_count": 3, "row_counts_by_db": {},
        "db_ids": [], "clarification_options": None, "artifacts": [], "error": None,
        "invalid_reason": None,
    }
    base.update(kw)
    return base


def _old_row(sid: str, **kw: Any) -> dict[str, Any]:
    """옛 러너 행(run 20260914-185540 모양) - 응답 본문·선택지·라우팅·SQL 수집 칸이 없다."""
    row = _row(sid, **kw)
    for column in ("response_text", "clarification_options", "db_ids", "row_counts_by_db",
                   "invalid_reason", "status"):
        if column not in kw:
            row.pop(column, None)
    return row


def test_j4_absent_response_column_is_held_not_failed(tmp_path: Path) -> None:
    catalog = _catalog(_scenario("T-01", {"response_must_contain": ["김포"],
                                          "response_must_contain_any": ["여의도"]}))
    new_row, diff = rejudge.rejudge_row(_old_row("T-01"), catalog, META, tmp_path,
                                        sql_collected=False)
    assert new_row["func_verdict"] == "manual" and new_row["failed_assertions"] == []
    [note] = new_row["manual_notes"]
    assert note.startswith(rejudge.REJUDGE_HOLD_PREFIX) and "`response_text`" in note
    assert new_row["manual_sources"] == ["unobservable"]
    assert new_row["manual_source"] == "unobservable"
    assert diff["held"] == {"response_text": ["response_must_contain", "response_must_contain_any"]}
    assert diff["causes"] == ["column_absent"]


def test_j4_empty_response_column_is_observed(tmp_path: Path) -> None:
    """칸이 있는데 비어 있으면 관측 0 이다 - 판정한다(불합격)."""
    catalog = _catalog(_scenario("T-01", {"response_must_contain": ["김포"]}))
    new_row, diff = rejudge.rejudge_row(_row("T-01", response_text=""), catalog, META, tmp_path)
    assert new_row["func_verdict"] == "fail"
    assert [f["key"] for f in new_row["failed_assertions"]] == ["response_must_contain"]
    assert diff["held"] == {}


def test_j4_sql_held_only_for_runs_without_sql_collection(tmp_path: Path) -> None:
    expect = {"sql_must_match": ["(?i)cmm_resource"], "sql_must_not_match": ["(?i)delete"]}
    catalog = _catalog(_scenario("T-01", expect))
    old = _old_row("T-01")
    held_row, held_diff = rejudge.rejudge_row(old, catalog, META, tmp_path, sql_collected=False)
    assert held_row["func_verdict"] == "manual"
    assert held_diff["held"] == {"executed_sqls": ["sql_must_match", "sql_must_not_match"]}
    assert "SQL 수집 이전 run" in held_row["manual_notes"][0]
    # 같은 행이라도 run 에 SQL 수집 흔적이 있으면 「칸 없음 = SQL 0건」 관측이다.
    judged_row, judged_diff = rejudge.rejudge_row(old, catalog, META, tmp_path, sql_collected=True)
    assert judged_row["func_verdict"] == "fail"
    assert [f["key"] for f in judged_row["failed_assertions"]] == ["sql_must_match"]
    assert "executed_sqls" not in judged_diff["held"]


def test_j4_run_sql_collected_is_run_level() -> None:
    rows = [_old_row("T-01"), _old_row("T-02")]
    assert rejudge.run_sql_collected(rows) is False
    rows.append(_old_row("T-03", executed_sqls=[{"sql": "SELECT 1", "source": "polestar"}]))
    assert rejudge.run_sql_collected(rows) is True
    assert rejudge.run_sql_collected([_row("T-01")]) is True       # row_counts_by_db 칸


def test_j4_db_ids_held_unless_executed_sources(tmp_path: Path) -> None:
    catalog = _catalog(_scenario("T-01", {"db_ids": ["polestar_cm_gp"]}))
    held_row, _ = rejudge.rejudge_row(_old_row("T-01"), catalog, META, tmp_path,
                                      sql_collected=False)
    assert held_row["func_verdict"] == "manual"
    restored, _ = rejudge.rejudge_row(
        _old_row("T-01", executed_sqls=[{"sql": "SELECT 1", "source": "polestar_cm_gp"}]),
        catalog, META, tmp_path, sql_collected=True)
    assert restored["func_verdict"] == "pass"            # 실행 DB 로 되살린다(row_db_ids)


def test_j4_clarification_and_form_memory_panel_held(tmp_path: Path) -> None:
    catalog = _catalog(
        _scenario("T-01", {"clarification": {"kind": "zone_select"}}),
        _scenario("T-02", {"form_memory_panel": "present"}),
    )
    row, diff = rejudge.rejudge_row(_old_row("T-01", response_mode="clarify"), catalog, META,
                                    tmp_path, sql_collected=False)
    assert row["func_verdict"] == "manual" and diff["held"] == {"clarification_options":
                                                                ["clarification"]}
    # form_memory_panel 은 러너가 행에 싣지 않는다 - 새 행에서도 보류다.
    row, diff = rejudge.rejudge_row(_row("T-02"), catalog, META, tmp_path)
    assert row["func_verdict"] == "manual"
    assert diff["held"] == {"form_memory_panel": ["form_memory_panel"]}


def test_j4_holds_do_not_hide_other_failures(tmp_path: Path) -> None:
    catalog = _catalog(_scenario("T-01", {"status": "completed", "response_must_contain": ["x"]}))
    row, _ = rejudge.rejudge_row(_old_row("T-01", status="error"), catalog, META, tmp_path,
                                 sql_collected=False)
    assert row["func_verdict"] == "fail"
    assert [f["key"] for f in row["failed_assertions"]] == ["status"]
    assert row["manual_notes"][0].startswith(rejudge.REJUDGE_HOLD_PREFIX)


AUTH_EXPECT = {"http_status": 401, "response_must_contain": ["인증이 필요합니다."]}


def test_j4_send_contract_auth_excluded_when_run_sent_with_token(tmp_path: Path) -> None:
    """J-08 모양 - 과거 run 은 러너 토큰으로 보내 200 을 받았다 → 비교 제외(거짓 불합격 방지)."""
    catalog = _catalog(_scenario("J-08", AUTH_EXPECT, auth="none"))
    row = _row("J-08", func_verdict="manual", manual_notes=["러너가 만료 토큰을 보낼 수 없다"])
    new_row, diff = rejudge.rejudge_row(row, catalog, META, tmp_path)
    assert diff["status"] == "send_contract" and diff["causes"] == ["send_contract"]
    assert diff["changed"] is False and diff["detail_changed"] is False
    assert rejudge.SEND_CONTRACT_NOTE in diff["reason"] and "HTTP 200" in diff["reason"]
    assert new_row["func_verdict"] == "manual" and new_row["failed_assertions"] == []
    assert new_row["rejudge_excluded"] == "send_contract"
    assert new_row["manual_notes"][0].startswith(rejudge.SEND_CONTRACT_NOTE)


def test_j4_send_contract_auth_compared_when_row_shows_401(tmp_path: Path) -> None:
    """새 방식으로 보낸 run(401 · T-c 이후 행) - 비교하고 합격한다(무효로 빠지지 않는다)."""
    catalog = _catalog(_scenario("J-08", AUTH_EXPECT, auth="none"))
    row = _row("J-08", error="http 401 - 인증 필요",
               response_text='{"detail":"인증이 필요합니다."}',
               status="error", mode_evidence="status=error http=401", response_mode="error")
    assert row_is_invalid(row) is False
    new_row, diff = rejudge.rejudge_row(row, catalog, META, tmp_path)
    assert diff["status"] == "rejudged" and new_row["func_verdict"] == "pass"


def test_j4_send_contract_same_digest_is_compared(tmp_path: Path) -> None:
    """run 이 같은 카탈로그(지문)로 돌았으면 그 방식으로 보낸 것이다.

    200 이어도 비교한다(회귀를 가리지 않는다).
    """
    catalog = _catalog(_scenario("J-08", AUTH_EXPECT, auth="none"))
    meta = {**META, "judgement_contract": {"catalog_digest": judgement_digest(catalog)}}
    new_row, diff = rejudge.rejudge_row(_row("J-08"), catalog, meta, tmp_path)
    assert diff["status"] == "rejudged" and new_row["func_verdict"] == "fail"
    assert "http_status" in [f["key"] for f in new_row["failed_assertions"]]


@pytest.mark.parametrize(("row_kw", "status"), [
    ({}, "send_contract"),                                                       # 200 - 옛 방식
    ({"error": "http 400: 파일 크기가 10MB를 초과합니다.", "response_mode": "error"}, "rejudged"),
])
def test_j4_send_contract_upload_generate(
    tmp_path: Path, row_kw: dict[str, Any], status: str,
) -> None:
    catalog = _catalog(_scenario("J-09", {"http_status": 400}, endpoint="file_stream",
                                 upload_generate={"ext": ".xlsx", "size_bytes": 12582912}))
    _new_row, diff = rejudge.rejudge_row(_row("J-09", **row_kw), catalog, META, tmp_path)
    assert diff["status"] == status


def test_j4_summary_and_markdown_report_holds_and_exclusions(tmp_path: Path) -> None:
    catalog = _catalog(_scenario("T-01", {"response_must_contain": ["x"]}),
                       _scenario("J-08", AUTH_EXPECT, auth="none"))
    run_dir = tmp_path / META["run_id"]
    run_dir.mkdir()
    rows = [_old_row("T-01"), _old_row("J-08")]
    (run_dir / "raw.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps({"meta": META, "profiles": [], "skipped": []}),
                                      encoding="utf-8")
    result = rejudge.rejudge_run(run_dir, catalog)
    summary = rejudge.summarize(result)
    assert summary["send_contract"] == 1 and summary["held_rows"] == 1
    assert summary["sql_collected"] is False
    assert summary["held_by_column"] == {"response_text": {"rows": 1,
                                                           "keys": ["response_must_contain"]}}
    text = rejudge.render_markdown(result, summary, {"label": "t", "digest": "d",
                                                     "identity": False})
    assert "## 복원 불가 보류" in text and f"## {rejudge.SEND_CONTRACT_NOTE} (1건" in text


# --- 5. row_is_invalid ----------------------------------------------------------------------

@pytest.mark.parametrize(("row", "expected"), [
    # T-c 이후 행 - 판정값만 본다.
    ({"func_verdict": "pass", "error": "http 401 - x", "invalid_reason": None}, False),
    ({"func_verdict": "fail", "error": "http 403 - x", "invalid_reason": None}, False),
    ({"func_verdict": "invalid", "error": "http 401 - x",
      "invalid_reason": "러너 인증 실패"}, True),
    # T-c 이전 행(칸 없음) - 종전대로 오류 문구로 추정한다.
    ({"func_verdict": "error", "error": "http 401 - x"}, True),
])
def test_row_is_invalid_trusts_verdict_after_tc(row: dict[str, Any], expected: bool) -> None:
    assert row_is_invalid(row) is expected
