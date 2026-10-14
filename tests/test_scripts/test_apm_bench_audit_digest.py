"""plans/164 W0a — 본체 감사 로그 진단표(`scripts/apm_bench/audit_digest.py`) · `--audit-digest`.

합성 감사 줄로 화이트리스트 출력(호스트 이름·금지 칸 미출력)·어휘 밖 드롭·순서 보존 정렬의 표지·
시간 창·다른 사용자 제외·자기 점검·게이트웨이 오류 코드 사본 정합을 본다(LLM·네트워크 0).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import audit_digest as ad
from scripts.apm_bench.scenarios import Expect, Scenario, Turn

VIEWS = frozenset({"apm.instances", "apm.app_health", "apm.runtime", "apm.slow_tx", "apm.pool"})
SINCE = datetime.fromisoformat("2026-10-13T09:00:00+09:00")
UNTIL = datetime.fromisoformat("2026-10-13T10:00:00+09:00")
HOST = "nohost-zz"
SECRET_SQL = "SELECT secret_col FROM polestar.cmm_resource"
TID = "46d2960f-4699-46ca-9e11-1ffefa753711"


def _sc(sid: str, views: tuple[str, ...], *, target: str = "inst:1", split: str = "dev",
        refusal: str | None = None) -> Scenario:
    expect = Expect(agents=(), views=views, target=target, window="default", refusal=refusal)
    return Scenario(id=sid, covers=(), split=split, turns=(Turn(prompt="p", expect=expect),))


def _ts(minute: int, second: int = 0) -> str:
    return f"2026-10-13T00:{minute:02d}:{second:02d}.123456+00:00"  # 09:MM KST


def _inv(tid: str, minute: int, profile: str | None, *, outcome: str = "ok",
         degraded: list[dict[str, Any]] | None = None, user: str | None = "anonymous",
         **extra: Any) -> dict[str, Any]:
    return {"timestamp": _ts(minute), "event": "host_investigation", "entry_point": "chat",
            "target_count": 1, "outcome": outcome, "user_id": user, "thread_id": tid,
            "profile": profile, "backend": "apm_gateway", "duration_ms": 1.2,
            "degraded": degraded, **extra}


def _clar(tid: str, minute: int, kind: str = "apm_source", axis: str | None = "apm_source",
          user: str | None = "anonymous") -> dict[str, Any]:
    return {"timestamp": _ts(minute), "event": "clarification_issued", "kind": kind,
            "axis": axis, "option_count": 4, "user_id": user, "thread_id": tid}


def _write(path: Path, rows: list[Any]) -> Path:
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False))
                            + "\n" for r in rows), encoding="utf-8")
    return path


def _digest(tmp_path: Path, rows: list[Any], expected: list[Scenario]) -> tuple[int, Path]:
    log = _write(tmp_path / "audit-2026-10-13.jsonl", rows)
    threads, counts, dropped = ad.read_threads([log], since=SINCE, until=UNTIL, user="anonymous",
                                               view_ids=VIEWS)
    digest = ad.build_digest(threads, counts, dropped, expected, {"env": "e2"})
    out = tmp_path / "out"
    code = ad.write_digest(digest, ad.vocabulary(expected, VIEWS), out, say=lambda _: None)
    return code, out / "audit_digest.json"


def _thread(tid: str, order: int, views: set[str]) -> ad.Thread:
    return ad.Thread(tid=tid, first=(SINCE, order), views=views)


# ① ② 호스트 이름·금지 칸 미출력 -----------------------------------------------------------


def test_free_text_reason_and_forbidden_fields_never_reach_output(tmp_path: Path) -> None:
    rows = [
        _clar(TID, 1),
        _inv(TID, 1, "apm.instances", outcome="partial",
             degraded=[{"reason": f"instance_unresolved: hostname '{HOST}'에 대응하는 APM 인스턴스"
                                  " 없음(목록 3건)"},
                       {"reason": "apm_partial_sources", "detail": f"apm.instances:{HOST}"},
                       {"reason": "apm_job_accepted", "detail": "job-1234"}],
             targets=[{"hostname": HOST}], commands=[f"apm_instance_map hostname={HOST}"],
             stdout=f"{HOST} up", authz={"allowed": True, "principal": "anonymous"},
             task_id="t1"),
        {"timestamp": _ts(2), "event": "query_execution", "sql": SECRET_SQL, "user_id": "anonymous",
         "thread_id": TID},
    ]
    code, path = _digest(tmp_path, rows, [_sc("APM-INST-host", ("apm.instances",))])
    assert code == 0
    text = path.read_text(encoding="utf-8")
    for leaked in (HOST, "job-1234", "secret_col", "anonymous", TID, "2026-10-13", "00:01",
                   "principal", '"targets"', '"commands"', '"detail"', '"stdout"', '"authz"',
                   '"user_id"', '"thread_id"', '"timestamp"', '"task_id"', '"sql"'):
        assert leaked not in text, leaked
    digest = json.loads(text)
    assert digest["input"]["other_event"] == 1  # query_execution 은 건수만
    assert digest["aggregates"]["reason_codes"] == {
        "apm_job_accepted": 1, "apm_partial_sources": 1, "instance_unresolved": 1}
    assert digest["aggregates"]["hypotheses"]["T1_H1"] == {
        "instance_unresolved": 1, "instance_unresolved_threads": 1,
        "after_apm_source_clarification": 1}
    assert digest["threads"][0]["mapping"] == "exact"


# ③ 어휘 밖 문자열 드롭 --------------------------------------------------------------------


def test_out_of_vocabulary_strings_dropped_and_counted(tmp_path: Path) -> None:
    rows = [
        _clar(TID, 1, kind="mystery_kind", axis="mystery_axis"),
        _inv(TID, 2, "apm.instances,apm.unknown_view,web01", outcome="bogus",
             degraded=[{"reason": "weird_code: web01 down"}, {"reason": "대상 web01 해석 실패"},
                       {"reason": "apm_target_unresolved"}]),
    ]
    code, path = _digest(tmp_path, rows, [_sc("APM-INST-host", ("apm.instances",))])
    assert code == 0
    text = path.read_text(encoding="utf-8")
    for leaked in ("mystery", "unknown_view", "web01", "weird_code", "bogus", "해석"):
        assert leaked not in text, leaked
    digest = json.loads(text)
    assert digest["dropped"] == {"reasons": 2, "views": 2, "outcomes": 1, "kinds": 1, "axes": 1}
    assert digest["aggregates"]["reason_dropped"] == 2
    assert digest["aggregates"]["reason_codes"] == {"apm_target_unresolved": 1}


# ④ 순서 보존 정렬 ------------------------------------------------------------------------


def test_silent_scenario_does_not_shift_later_matches() -> None:
    expected = [_sc("A", ("apm.instances",)), _sc("B", ("apm.app_health",)),
                _sc("C", ("apm.runtime",)), _sc("D", ("apm.pool",)), _sc("E", ("apm.slow_tx",))]
    threads = [_thread("ta", 1, {"apm.instances"}), _thread("tc", 2, {"apm.runtime"}),
               _thread("td", 3, {"apm.pool"}), _thread("te", 4, {"apm.slow_tx"})]
    labels, missing, stats = ad.label_threads(threads, expected)
    # 무이벤트 B 의 앞뒤 짝은 강등(이웃 틀린 보기 위험) · 그 뒤 대응은 밀리지 않는다
    assert [(label, s.id if s else [c.id for c in cands]) for label, s, cands in labels] == [
        ("ambiguous", ["A", "B"]), ("ambiguous", ["B", "C"]), ("exact", "D"), ("exact", "E")]
    assert stats == {"downgraded_silent_neighbor": 2, "downgraded_split_thread": 0,
                     "silent_candidates": 1}
    assert missing == []  # 후보가 된 B 는 missing 으로 단정하지 않는다


def test_wrong_view_next_to_silent_scenario_is_not_exact() -> None:
    # E1(기대 a)은 무이벤트 · E2 턴이 틀린 보기 a 를 골랐다 — 비용상 E1↔O 가 유일 최적이다
    expected = [_sc("E1", ("apm.instances",)), _sc("E2", ("apm.app_health",))]
    labels, missing, stats = ad.label_threads([_thread("o", 1, {"apm.instances"})], expected)
    assert [label for label, _, _ in labels] == ["ambiguous"]
    assert [c.id for c in labels[0][2]] == ["E1", "E2"]
    assert stats["downgraded_silent_neighbor"] == 1 and missing == []


def test_same_view_run_with_silent_member_is_ambiguous_not_exact() -> None:
    expected = [_sc("P1", ("apm.slow_tx",)), _sc("P2", ("apm.slow_tx",)),
                _sc("P3", ("apm.slow_tx",)), _sc("Q", ("apm.pool",))]
    threads = [_thread("t1", 1, {"apm.slow_tx"}), _thread("t2", 2, {"apm.slow_tx"}),
               _thread("tq", 3, {"apm.pool"})]
    labels, missing, _ = ad.label_threads(threads, expected)
    assert [label for label, _, _ in labels] == ["ambiguous", "ambiguous", "exact"]
    assert labels[2][1] is not None and labels[2][1].id == "Q"
    assert [s.id for s in labels[0][2]] == ["P1", "P2"]
    assert [s.id for s in labels[1][2]] == ["P2", "P3"]
    assert missing == []  # P 가운데 무엇이 이벤트 0 인지 가를 수 없다


def test_disjoint_views_and_edge_contamination_are_not_exact() -> None:
    expected = [_sc("A", ("apm.instances",)), _sc("B", ("apm.app_health",))]
    # 창 앞에 같은 보기 순서의 시험 run 이 끼었다 — 어느 쪽이 진짜인지 가를 수 없다
    threads = [_thread("f1", 1, {"apm.instances"}), _thread("a", 2, {"apm.instances"}),
               _thread("b", 3, {"apm.runtime"})]
    labels, _, _ = ad.label_threads(threads, expected)
    assert "exact" not in [label for label, _, _ in labels]


def test_missing_row_marks_expected_silent(tmp_path: Path) -> None:
    expected = [_sc("APM-X-control", (), target="none", refusal="write_control"),
                _sc("APM-X-rule-write", (), target="none", refusal="write_control"),
                _sc("APM-INST-host", ("apm.instances",))]
    code, path = _digest(tmp_path, [_inv(TID, 1, "apm.instances")], expected)
    assert code == 0
    digest = json.loads(path.read_text(encoding="utf-8"))
    # 이웃(X-rule-write)은 강등 후보로 빠지고 그 너머 X-control 만 missing 이다
    assert digest["mapping"]["missing"] == 1
    assert digest["mapping"]["downgraded_silent_neighbor"] == 1
    # 기대 3 − 대응 스레드 1 = 무이벤트 하한 2 · 강등 후보 이웃 1
    assert digest["mapping"]["silent_lower_bound"] == 2
    assert digest["aggregates"]["hypotheses"]["source_miss_G1"] == {
        "missing_expected_apm": 0, "zone_select_only_threads": 0, "silent_lower_bound": 2,
        "silent_candidates": 1}
    assert digest["missing"] == [{"order": 1, "scenario_id": "APM-X-control", "split": "dev",
                                  "turn_count": 1, "target_kinds": ["none"],
                                  "expected_views": [], "expected_silent": True}]
    assert digest["aggregates"]["hypotheses"]["source_miss_G1"]["missing_expected_apm"] == 0


def test_target_text_phrases_become_codes_without_names(tmp_path: Path) -> None:
    rows = [_inv(TID, 1, "apm.instances", outcome="partial",
                 degraded=[{"reason": f"대상 '{HOST}' 해석 0건"},
                           {"reason": f"대상 '{HOST}, web-9' 해석 0건"},
                           {"reason": f"대상 '{HOST}…' 이름 길이 초과"},
                           {"reason": f"대상 '{HOST}' 해석 0건 (추가 문장)"}])]  # 문형 밖 → 버림
    code, path = _digest(tmp_path, rows, [_sc("APM-INST-host", ("apm.instances",))])
    assert code == 0
    text = path.read_text(encoding="utf-8")
    assert HOST not in text and "web-9" not in text
    digest = json.loads(text)
    assert digest["aggregates"]["reason_codes"] == {"target_name_too_long": 1,
                                                    "target_text_unresolved": 2}
    assert digest["dropped"]["reasons"] == 1
    assert digest["aggregates"]["target_resolution"]["inst"]["failed"] == 1


def test_odd_shapes_counted_not_raised(tmp_path: Path) -> None:
    rows: list[Any] = [
        _inv(TID, 1, "apm.instances", degraded="instance_unresolved"),  # 문자열 → 버림
        _inv(TID, 2, "apm.instances", degraded={"reason": "error"}),     # dict → 버림
        _inv(TID, 3, "apm.instances", degraded=12345),                   # 정수 → 버림
        _inv(TID, 4, "apm.instances", degraded=[HOST, {"reason": "no_result"}]),
        "[" * 100_000 + HOST,                                            # RecursionError
        {**_inv(TID, 5, "apm.instances"), "timestamp": 20261013},        # 시각이 정수
    ]
    code, path = _digest(tmp_path, rows, [_sc("APM-INST-host", ("apm.instances",))])
    assert code == 0
    digest = json.loads(path.read_text(encoding="utf-8"))
    assert digest["input"]["broken"] == 2 and digest["input"]["used"] == 4
    assert digest["dropped"]["reasons"] == 4
    assert digest["aggregates"]["reason_codes"] == {"no_result": 1}


# ⑤ ⑥ 시간 창 · 다른 사용자 · 깨진 줄 ------------------------------------------------------


def test_window_user_and_broken_lines_counted_only(tmp_path: Path) -> None:
    rows: list[Any] = [
        _inv(TID, 1, "apm.instances"),
        {**_inv("other-tid", 1, "apm.instances"), "timestamp": "2026-10-13T02:00:00+00:00"},
        _inv("t-alice", 2, "apm.instances", user="alice"),
        _inv("t-none", 2, "apm.instances", user=None),
        {**_inv("x", 3, "apm.instances"), "thread_id": None},
        {**_inv("y", 3, "apm.instances"), "timestamp": "2026-10-13T00:03:00"},  # 오프셋 없음
        "{not json",
    ]
    code, path = _digest(tmp_path, rows, [_sc("APM-INST-host", ("apm.instances",))])
    assert code == 0
    digest = json.loads(path.read_text(encoding="utf-8"))
    assert digest["input"] == {"files": 1, "lines": 7, "broken": 2, "out_of_window": 1,
                               "other_user": 2, "other_event": 0, "no_thread": 1, "used": 1}
    assert digest["mapping"]["threads"] == 1
    assert "alice" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("since,until", [
    ("2026-10-13T09:00:00", "2026-10-13T10:00:00+09:00"),
    ("2026-10-13T09:00:00+09:00", "2026-10-13T10:00:00"),
    (None, "2026-10-13T10:00:00+09:00"),
])
def test_cli_requires_offset_exit_2(tmp_path: Path, since: str | None, until: str) -> None:
    log = _write(tmp_path / "a.jsonl", [_inv(TID, 1, "apm.instances")])
    argv = ["--audit-digest", str(log), "--until", until, "--out", str(tmp_path / "o")]
    if since:
        argv += ["--since", since]
    assert cli.main(argv) == 2
    assert not (tmp_path / "o").exists()


def test_cli_missing_file_or_out_exit_2(tmp_path: Path) -> None:
    window = ["--since", "2026-10-13T09:00+09:00", "--until", "2026-10-13T10:00+09:00"]
    assert cli.main(["--audit-digest", str(tmp_path / "nope.jsonl"), *window,
                     "--out", str(tmp_path / "o")]) == 2
    log = _write(tmp_path / "a.jsonl", [_inv(TID, 1, "apm.instances")])
    assert cli.main(["--audit-digest", str(log), *window]) == 2


def test_cli_end_to_end_with_repo_scenarios(tmp_path: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    rows = [_clar("t1", 1), _inv("t1", 1, "apm.instances", outcome="failed",
                                 degraded=[{"reason": "apm_unresolved_condition"}]),
            _inv("t1", 2, "apm.instances"), _inv("t2", 3, "apm.app_health"),
            _inv("t9", 4, "apm.runtime", user="u1")]
    log = _write(tmp_path / "audit-2026-10-13.jsonl", rows)
    out = tmp_path / "o"
    code = cli.main(["--audit-digest", str(log), "--since", "2026-10-13T09:00+09:00",
                     "--until", "2026-10-13T10:00+09:00", "--env", "e2",
                     "--only", "APM-INST-list", "APM-HEALTH-inst", "--out", str(out)])
    assert code == 0
    digest = json.loads((out / "audit_digest.json").read_text(encoding="utf-8"))
    assert digest["expected"]["scenarios"] == 2 and digest["expected"]["light"] == 2
    assert digest["mapping"]["exact"] == 2 and digest["input"]["other_user"] == 1
    assert [t["scenario_id"] for t in digest["threads"]] == ["APM-INST-list", "APM-HEALTH-inst"]
    printed = capsys.readouterr().out
    assert '"exact": 2' in printed and "u1" not in printed


# ⑦ 게이트웨이 오류 코드 — 벤치 유일 사본(`record.py`)을 쓴다 ------------------------------------
# 정본(`apm_gateway/.../errors.py`) 대조는 `test_apm_bench_leak_e2.py`
# `test_error_code_copy_matches_gateway_source` 하나만 둔다(plans/164 W2a).


def test_gateway_error_codes_come_from_record_copy() -> None:
    from scripts.apm_bench import record

    assert ad.GATEWAY_ERROR_CODES is record.GATEWAY_ERROR_CODES


# ⑧ 자기 점검 -----------------------------------------------------------------------------


def test_self_check_refuses_to_write_out_of_vocabulary(tmp_path: Path) -> None:
    expected = [_sc("APM-INST-host", ("apm.instances",))]
    log = _write(tmp_path / "a.jsonl", [_inv(TID, 1, "apm.instances")])
    threads, counts, dropped = ad.read_threads([log], since=SINCE, until=UNTIL, user="anonymous",
                                               view_ids=VIEWS)
    digest = ad.build_digest(threads, counts, dropped, expected, {"env": "e2"})
    allowed = ad.vocabulary(expected, VIEWS)
    assert ad.violations(digest, allowed) == 0
    said: list[str] = []
    for bad in ({**digest, "threads": [{**digest["threads"][0], "note": "x"}]},  # 어휘 밖 키
                {**digest, "expected": {"env": HOST}}):  # 어휘 밖 값
        assert ad.write_digest(bad, allowed, tmp_path / "out", say=said.append) == 1
        assert not (tmp_path / "out" / "audit_digest.json").exists()
    assert all(HOST not in line for line in said)
