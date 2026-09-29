"""오라클 판정 `evaluate_oracle` (plans/122 O-2 · O-4 · §13.2 X-4).

합성 오라클 행(`run_oracle` 결과 모양)과 합성 CSV(`Observation.result` 모양 — 값은 문자열)로
compare 5종(count · keyset · rowset · argmax · value)마다 합격·불합격·보류 쌍을 고정한다.
판정 계약: 오라클 실패·0행·결과 미수집·열 미해결 = 보류(불합격 아님) · 불일치 = 불합격 + 차이
(행 수 · 키 차집합 상위 10 — 값은 키만). `source: fixture`(M군 정답표)는 DB 없이 판정한다.
"""

from __future__ import annotations

from typing import Any

from scripts.scenario.oracle import DIFF_LIMIT, evaluate_oracle


def _outcome(rows_by_db: dict[str, list[dict[str, Any]]], *, status: str = "ok",
             limits: dict[str, int] | None = None, reason: str | None = None) -> dict[str, Any]:
    return {"status": status, "reason": reason, "rows_by_db": rows_by_db, "elapsed_ms": 1.0,
            "phase": "post", "limit_by_db": limits or {db: 10000 for db in rows_by_db}}


def _csv(columns: list[str], rows: list[list[Any]], *, truncated: bool = False,
         total: int | None = None, status: str = "ok") -> dict[str, Any]:
    """러너 계약 모양 — CSV 라서 값은 문자열이고 None 은 빈 칸이다."""
    return {
        "status": status, "columns": columns,
        "rows": [dict(zip(columns, ["" if v is None else str(v) for v in row])) for row in rows],
        "total_rows": total if total is not None else len(rows), "truncated": truncated,
        "reason": None,
    }


# --- 보류 계약 ----------------------------------------------------------------------

def test_hold_when_oracle_missing_failed_or_empty() -> None:
    spec = {"id": "C-07", "compare": "keyset", "key": ["server_name"]}
    result = _csv(["server_name"], [["a"]])
    assert evaluate_oracle(spec, None, result)[0] == "hold"
    verdict, detail = evaluate_oracle(
        spec, _outcome({}, status="unavailable", reason="타임아웃 30s"), result)
    assert verdict == "hold" and "타임아웃" in detail["reason"]
    verdict, detail = evaluate_oracle(spec, _outcome({"polestar_cm_gp": []}), result)
    assert verdict == "hold" and "0행" in detail["reason"]


def test_hold_when_system_result_not_collected() -> None:
    spec = {"id": "C-07", "compare": "keyset", "key": ["server_name"]}
    outcome = _outcome({"polestar_cm_gp": [{"server_name": "a"}]})
    assert evaluate_oracle(spec, outcome, None)[0] == "hold"
    unavailable = {"status": "unavailable", "reason": "404 축출", "rows": []}
    verdict, detail = evaluate_oracle(spec, outcome, unavailable)
    assert verdict == "hold" and "404" in detail["reason"]


def test_hold_when_key_column_cannot_be_resolved() -> None:
    spec = {"id": "C-07", "compare": "keyset", "key": [["server_name", "서버명"]]}
    outcome = _outcome({"polestar_cm_gp": [{"server_name": "a"}]})
    verdict, detail = evaluate_oracle(spec, outcome, _csv(["hostname"], [["a"]]))
    assert verdict == "hold" and detail["header"] == ["hostname"]


def test_evaluate_never_raises() -> None:
    spec = {"id": "X", "compare": "value", "value": "n", "tol": "abc"}
    verdict, detail = evaluate_oracle(spec, _outcome({"d": [{"n": 1}]}), _csv(["n"], [["1"]]))
    assert verdict == "hold" and "평가 오류" in detail["reason"]
    assert evaluate_oracle(None, None, None)[0] == "hold"  # type: ignore[arg-type]


# --- count ----------------------------------------------------------------------------

def test_count_by_audit_is_per_db_not_just_the_sum() -> None:
    spec = {"id": "B-01", "compare": "count", "system": "row_counts_by_db"}
    outcome = _outcome({"polestar_cm_gp": [{"n": 100}], "polestar_b0": [{"n": 50}]})
    ok = evaluate_oracle(spec, outcome, None, row_counts_by_db={"polestar_cm_gp": 100,
                                                                "polestar_b0": 50})
    assert ok[0] == "pass" and ok[1]["oracle_total"] == 150
    # 합은 같아도 DB 별로 어긋나면 불합격이다(EAV 다중값 +1 과 누락 -1 상쇄).
    verdict, detail = evaluate_oracle(spec, outcome, None,
                                      row_counts_by_db={"polestar_cm_gp": 101, "polestar_b0": 49})
    assert verdict == "fail" and detail["mismatch_dbs"] == ["polestar_b0", "polestar_cm_gp"]
    assert evaluate_oracle(spec, outcome, None, row_counts_by_db={})[0] == "hold"


def test_count_by_result_total_and_source_db_split() -> None:
    spec = {"id": "H-17", "compare": "count"}
    outcome = _outcome({"polestar_b0": [{"n": 3}]})
    assert evaluate_oracle(spec, outcome, _csv(["a"], [["x"], ["y"], ["z"]]))[0] == "pass"
    assert evaluate_oracle(spec, outcome, _csv(["a"], [["x"]]))[0] == "fail"
    # 잘렸어도 total_rows 가 있으면 전체 행 수로 판정한다 / 없으면 보류.
    cut_known = _csv(["a"], [["x"]], truncated=True, total=3)
    assert evaluate_oracle(spec, outcome, cut_known)[0] == "pass"
    cut = _csv(["a"], [["x"]], truncated=True)
    cut.pop("total_rows")
    assert evaluate_oracle(spec, outcome, cut)[0] == "hold"
    multi = _outcome({"polestar_cm_gp": [{"n": 1}], "polestar_cm_yd": [{"n": 1}]})
    rows = _csv(["_source_db", "a"], [["polestar_cm_gp", "x"], ["polestar_cm_gp", "y"]])
    verdict, detail = evaluate_oracle(spec, multi, rows)
    assert verdict == "fail" and detail["mismatch_dbs"] == ["polestar_cm_gp", "polestar_cm_yd"]


def test_count_rows_mode_holds_when_oracle_hit_its_limit() -> None:
    spec = {"id": "X", "compare": "count"}
    outcome = _outcome({"polestar_b0": [{"k": 1}, {"k": 2}]}, limits={"polestar_b0": 2})
    assert evaluate_oracle(spec, outcome, _csv(["k"], [["1"], ["2"]]))[0] == "hold"


# --- keyset ---------------------------------------------------------------------------

def test_keyset_equal_with_numeric_coercion_and_capped_diff() -> None:
    spec = {"id": "C-07", "compare": "keyset", "key": [["server_name", "서버명"], "cores"]}
    oracle_rows = [{"server_name": f"s{i:02d}", "cores": 4} for i in range(20)]
    outcome = _outcome({"polestar_cm_gp": oracle_rows})
    same = _csv(["서버명", "cores"], [[f"s{i:02d}", "4.0"] for i in range(20)])
    assert evaluate_oracle(spec, outcome, same)[0] == "pass"
    few = _csv(["서버명", "cores"], [["s00", "4"], ["zz", "4"]])
    verdict, detail = evaluate_oracle(spec, outcome, few)
    assert verdict == "fail"
    assert detail["missing_count"] == 19 and len(detail["missing"]) == DIFF_LIMIT
    assert detail["extra"] == [["zz", "4"]]
    assert "rows" not in detail


def test_keyset_subset_for_form_pairs_h04() -> None:
    """H-04 — 산출 (서버명, 호스트명) 쌍이 전부 오라클 쌍에 속해야 한다.

    name = hostname 인 정상 서버도 쌍 집합에 있으므로 오탐하지 않는다.
    """
    spec = {"id": "H-04", "compare": "keyset", "match": "subset",
            "key": [["server_name", "서버명"], ["hostname", "호스트명"]]}
    outcome = _outcome({"polestar_b0": [
        {"server_name": "sicwso01 (이미지 WAS#1)", "hostname": "sicwso01"},
        {"server_name": "dbsvr02", "hostname": "dbsvr02"},
        {"server_name": "apsvr03 (업무)", "hostname": "apsvr03"}]})
    good = _csv(["서버명", "호스트명", "IP"], [["sicwso01 (이미지 WAS#1)", "sicwso01", "10.0.0.1"],
                                            ["dbsvr02", "dbsvr02", "10.0.0.2"]])
    assert evaluate_oracle(spec, outcome, good)[0] == "pass"
    # D-148 오매핑 — 서버명 열에 호스트명(EAV Hostname)이 들어갔다.
    bad = _csv(["서버명", "호스트명"], [["sicwso01", "sicwso01"], ["dbsvr02", "dbsvr02"]])
    verdict, detail = evaluate_oracle(spec, outcome, bad)
    assert verdict == "fail" and detail["extra"] == [["sicwso01", "sicwso01"]]
    # subset 은 잘린 결과도 받은 행만으로 판정한다.
    assert evaluate_oracle(spec, outcome, _csv(["서버명", "호스트명"], [["dbsvr02", "dbsvr02"]],
                                              truncated=True, total=9))[0] == "pass"


def test_keyset_equal_holds_on_truncation_and_oracle_limit() -> None:
    spec = {"id": "C-07", "compare": "keyset", "key": ["server_name"]}
    outcome = _outcome({"polestar_cm_gp": [{"server_name": "a"}]})
    assert evaluate_oracle(spec, outcome, _csv(["server_name"], [["a"]], truncated=True,
                                               total=5))[0] == "hold"
    limited = _outcome({"polestar_cm_gp": [{"server_name": "a"}]}, limits={"polestar_cm_gp": 1})
    assert evaluate_oracle(spec, limited, _csv(["server_name"], [["a"]]))[0] == "hold"


def test_keyset_source_db_tag_separates_databases() -> None:
    spec = {"id": "C-07", "compare": "keyset", "key": ["server_name"]}
    outcome = _outcome({"polestar_cm_gp": [{"server_name": "a"}],
                        "polestar_cm_yd": [{"server_name": "b"}]})
    header = ["_source_db", "server_name"]
    right = _csv(header, [["polestar_cm_gp", "a"], ["polestar_cm_yd", "b"]])
    swapped = _csv(header, [["polestar_cm_yd", "a"], ["polestar_cm_gp", "b"]])
    assert evaluate_oracle(spec, outcome, right)[0] == "pass"
    assert evaluate_oracle(spec, outcome, swapped)[0] == "fail"
    # 태그가 없으면(단일 경로) 합집합으로 본다.
    assert evaluate_oracle(spec, outcome, _csv(["server_name"], [["b"], ["a"]]))[0] == "pass"


def test_keyset_empty_system_result_fails_and_disjoint() -> None:
    spec = {"id": "C-07", "compare": "keyset", "key": ["server_name"]}
    outcome = _outcome({"polestar_cm_gp": [{"server_name": "a"}]})
    verdict, detail = evaluate_oracle(spec, outcome, _csv([], [], status="empty"))
    assert verdict == "fail" and detail["missing"] == [["a"]]
    disjoint = {**spec, "match": "disjoint"}
    assert evaluate_oracle(disjoint, outcome, _csv(["server_name"], [["b"]]))[0] == "pass"
    assert evaluate_oracle(disjoint, outcome, _csv(["server_name"], [["a"]]))[0] == "fail"


# --- rowset (column_subset — 값으로 대응) -----------------------------------------------

B03_ORACLE = {"polestar_cm_gp": [{
    "hostname": "cocm-hdkapp01", "ipaddress": "10.61.0.***", "ostype": "Linux",
    "osversion": "RHEL 8.6", "cpu_model": "Xeon 6248R", "mem_size": "62.1 GB"}]}


def test_rowset_matches_by_values_not_column_names() -> None:
    spec = {"id": "B-03", "compare": "rowset"}
    system = _csv(["id", "server_name", "호스트명", "IP", "OS", "OS버전", "CPU", "메모리"],
                  [["9610003", "cocm-hdkapp01", "cocm-hdkapp01", "10.61.0.***", "Linux",
                    "RHEL 8.6", "Xeon 6248R", "62.1 GB"]])
    assert evaluate_oracle(spec, _outcome(B03_ORACLE), system)[0] == "pass"
    wrong = _csv(["호스트명", "IP", "OS", "OS버전", "CPU", "메모리"],
                 [["cocm-hdkapp01", "10.61.0.***", "Linux", "RHEL 8.6", "Xeon 6248R", ""]])
    verdict, detail = evaluate_oracle(spec, _outcome(B03_ORACLE), wrong)
    assert verdict == "fail" and detail["by_db"]["*"]["unmatched_columns"] == ["mem_size"]


def test_rowset_numeric_tolerance_row_count_and_per_db() -> None:
    spec = {"id": "C-02", "compare": "rowset", "tol": 0.01}
    outcome = _outcome({"polestar_cm_gp": [{"cpu_avg": 42.8, "io_avg": None},
                                           {"cpu_avg": 38.5, "io_avg": 125.3}],
                        "polestar_b0": [{"cpu_avg": 12.34, "io_avg": None}]})
    ok = _csv(["_source_db", "month", "cpu", "io"],
              [["polestar_cm_gp", "202607", "38.5", "125.30"],
               ["polestar_cm_gp", "202606", "42.80", ""],
               ["polestar_b0", "202606", "12.341", ""]])
    assert evaluate_oracle(spec, outcome, ok)[0] == "pass"
    short = _csv(["_source_db", "cpu", "io"], [["polestar_cm_gp", "38.5", "125.3"],
                                               ["polestar_b0", "12.34", ""]])
    verdict, detail = evaluate_oracle(spec, outcome, short)
    assert verdict == "fail" and detail["by_db"]["polestar_cm_gp"]["mismatch"] == "행 수 다름"
    assert "mismatch" not in detail["by_db"]["polestar_b0"]


# --- argmax -------------------------------------------------------------------------

B12 = {"polestar_cm_gp": [{"server_name": "g1", "cpu_avg": 90.0},
                          {"server_name": "g2", "cpu_avg": 70.0}],
       "polestar_b0": [{"server_name": "b1", "cpu_avg": 80.0},
                       {"server_name": "b2", "cpu_avg": 60.0}]}
B12_SPEC = {"id": "B-12", "compare": "argmax", "top": 3, "tol": 0.01,
            "key": [["server_name", "서버명"]], "value": ["cpu_avg", "평균"]}


def test_argmax_global_top_n_after_resorting_raw_csv() -> None:
    # CSV 는 재정렬 전 원본 — 하네스가 값 열로 다시 정렬한다.
    system = _csv(["서버명", "평균"], [["g2", "70.00"], ["g1", "90"], ["b1", "80.0"]])
    assert evaluate_oracle(B12_SPEC, _outcome(B12), system)[0] == "pass"
    wrong = _csv(["서버명", "평균"], [["g1", "90"], ["b1", "80"], ["b2", "60"]])
    verdict, detail = evaluate_oracle(B12_SPEC, _outcome(B12), wrong)
    assert verdict == "fail" and detail["not_allowed"] == [["b2"]]


def test_argmax_value_mismatch_and_ties() -> None:
    off = _csv(["서버명", "평균"], [["g1", "90"], ["b1", "80"], ["g2", "69.5"]])
    verdict, detail = evaluate_oracle(B12_SPEC, _outcome(B12), off)
    assert verdict == "fail" and detail["value_diffs"][0]["key"] == ["g2"]
    tie = {"d": [{"server_name": "a", "cpu_avg": 5}, {"server_name": "b", "cpu_avg": 3},
                 {"server_name": "c", "cpu_avg": 3}]}
    spec = {**B12_SPEC, "top": 2}
    for pick in ("b", "c"):
        system = _csv(["서버명", "평균"], [["a", "5"], [pick, "3"]])
        assert evaluate_oracle(spec, _outcome(tie), system)[0] == "pass"


def test_argmax_without_value_column_judges_keys_only_when_exactly_n_rows() -> None:
    """C-09 — 상승폭 열 이름이 별칭 목록에 없어도 시스템이 1행만 냈으면 서버만 판정한다."""
    spec = {**B12_SPEC, "top": 1, "value": ["increase"]}
    c09 = _outcome({"polestar_cm_gp": [{"server_name": "g1", "increase": 7.6},
                                       {"server_name": "g2", "increase": 3.7}]})
    one = _csv(["서버명", "상승"], [["g1", "7.6"]])
    verdict, detail = evaluate_oracle(spec, c09, one)
    assert verdict == "pass" and "키만" in detail["value_check"]
    assert evaluate_oracle(spec, c09, _csv(["서버명", "상승"], [["g2", "3.7"]]))[0] == "fail"
    many = _csv(["서버명", "상승"], [["g1", "7.6"], ["g2", "3.7"]])
    assert evaluate_oracle(spec, c09, many)[0] == "hold"


# --- value · pre_post (O-4) -------------------------------------------------------------

def test_value_scalar_within_tolerance() -> None:
    spec = {"id": "D-05", "compare": "value", "value": ["n", "건수"], "tol": 0}
    outcome = _outcome({"polestar_cm_gp": [{"n": 42}]})
    assert evaluate_oracle(spec, outcome, _csv(["건수"], [["42"]]))[0] == "pass"
    assert evaluate_oracle(spec, outcome, _csv(["건수"], [["41"]]))[0] == "fail"
    assert evaluate_oracle(spec, outcome, _csv(["건수"], [["1"], ["2"]]))[0] == "hold"
    two_rows = _outcome({"polestar_cm_gp": [{"n": 1}], "polestar_b0": [{"n": 2}]})
    assert evaluate_oracle(spec, two_rows, _csv(["건수"], [["3"]]))[0] == "hold"


def test_pre_post_count_between_snapshots() -> None:
    spec = {"id": "D-05", "compare": "count", "snapshot": "pre_post"}
    pre, post = _outcome({"d": [{"n": 5}]}), _outcome({"d": [{"n": 7}]})
    rows = [["x"]] * 6
    assert evaluate_oracle(spec, post, _csv(["a"], rows), pre=pre)[0] == "pass"
    assert evaluate_oracle(spec, post, _csv(["a"], [["x"]] * 9), pre=pre)[0] == "fail"
    assert evaluate_oracle(spec, post, _csv(["a"], rows), pre=None)[0] == "hold"


def test_pre_post_keyset_active_alarms() -> None:
    """D-01 — 활성 알람은 수시로 바뀐다.

    전·후 모두 있던 키는 있어야 하고, 둘 다 없던 키는 없어야 한다.
    """
    spec = {"id": "D-01", "compare": "keyset", "key": ["alarm_id"], "snapshot": "pre_post"}
    pre = _outcome({"d": [{"alarm_id": 1}, {"alarm_id": 2}]})
    post = _outcome({"d": [{"alarm_id": 2}, {"alarm_id": 3}]})
    for ids in (["2"], ["2", "3"], ["1", "2", "3"]):
        result = _csv(["alarm_id"], [[i] for i in ids])
        assert evaluate_oracle(spec, post, result, pre=pre)[0] == "pass", ids
    verdict, detail = evaluate_oracle(spec, post, _csv(["alarm_id"], [["1"], ["3"]]), pre=pre)
    assert verdict == "fail" and detail["lost_stable"] == [["2"]]
    verdict, detail = evaluate_oracle(spec, post, _csv(["alarm_id"], [["2"], ["9"]]), pre=pre)
    assert verdict == "fail" and detail["outside_union"] == [["9"]]


def test_pre_post_value_between() -> None:
    spec = {"id": "D-05", "compare": "value", "value": "n", "snapshot": "pre_post", "tol": 0}
    pre, post = _outcome({"d": [{"n": 10}]}), _outcome({"d": [{"n": 12}]})
    assert evaluate_oracle(spec, post, _csv(["n"], [["11"]]), pre=pre)[0] == "pass"
    assert evaluate_oracle(spec, post, _csv(["n"], [["13"]]), pre=pre)[0] == "fail"


# --- source: fixture (M군 정답표 · X-4) -------------------------------------------------

M01 = ["svr-web-05", "svr-web-08", "svr-was-03", "svr-was-07", "svr-db-04",
       "svbatch009", "svr-app-03", "svr-bat-02"]


def test_fixture_keyset_reads_answer_table_without_db() -> None:
    spec = {"id": "M-01", "source": "fixture", "compare": "keyset",
            "key": [["hostname", "sevrHostName"]], "field": "producer.expected"}
    good = _csv(["hostname", "avail_status"], [[h, "1"] for h in reversed(M01)])
    assert evaluate_oracle(spec, None, good)[0] == "pass"
    verdict, detail = evaluate_oracle(spec, None, _csv(["hostname"], [[h] for h in M01[:5]]))
    assert verdict == "fail" and detail["missing_count"] == 3
    # 브리지 없음 목록은 결합 결과에 나오면 안 된다(disjoint).
    none = {**spec, "field": "bridge.none", "match": "disjoint"}
    linked = _csv(["sevrHostName"], [["svr-web-05"], ["svr-db-04"]])
    assert evaluate_oracle(none, None, linked)[0] == "pass"
    assert evaluate_oracle(none, None, _csv(["sevrHostName"], [["svr-app-03"]]))[0] == "fail"


def test_fixture_count_and_holds() -> None:
    spec = {"id": "M-01", "source": "fixture", "compare": "count", "field": "bridge.link"}
    assert evaluate_oracle(spec, None, _csv(["h"], [["x"]] * 5))[0] == "pass"
    assert evaluate_oracle(spec, None, _csv(["h"], [["x"]] * 4))[0] == "fail"
    assert evaluate_oracle(spec, None, None)[0] == "hold"
    missing = {**spec, "id": "M-99"}
    assert evaluate_oracle(missing, None, _csv(["h"], [["x"]]))[0] == "hold"
    keyset = {"id": "M-01", "source": "fixture", "compare": "keyset", "key": ["hostname"],
              "field": "producer.expected"}
    assert evaluate_oracle(keyset, None, _csv(["server"], [["svr-web-05"]]))[0] == "hold"
