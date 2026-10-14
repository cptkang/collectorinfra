"""plans/164 W1 — E2 1회차 누출 관문 위반 교정(관문 규칙은 완화하지 않는다 · D-323 ⑤).

① 인벤토리 토큰이 보기 id 낱말과 겹쳐도 재선택 근거(`result_views`·`candidate_views`)는 통과
② OS 사용자·호스트가 응답에 부분 포함돼도 치환기가 표지로 바꿔 통과(값 없이 건수만)
③ 음성 — 실 원이름·카나리아·JWT 는 여전히 위반 · 코드 어휘 칸에 자유 문자열이 오면 위반
④ 음성 — 실행 호스트 이름이 인벤토리 이름의 조각이면 E2 는 턴 송신 전 exit 2
⑤ 음성 — `parsed_views`에 보기 id 가 아닌 문자열(자리표 원이름)이 오면 반출본에 남지 않는다
교정 2라운드 — 충돌 대조는 짧은 이름만(DNS 접미 제외) · FQDN·짧은 호스트 이름 · 대소문자 무시 치환 ·
오류 문장 치환 · 칸별 닫힌 어휘(이름 조각·사전 키) · 인자 묶음 아래 면제 해제 · 오류 코드 사본
교정 3라운드 — 레지스트리 밖 소스 id·라벨(소스 칸 닫힌 어휘 · 원이름 대조 집합 합류) · 사용자 정보 ↔
코드 어휘·산출물 키 충돌은 E2 턴 송신 전 exit 2 · 벤치 소유 칸 면제는 소유 위치만

순수 로직 — 네트워크·LLM 0. 이름은 전부 합성 값이다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import hygiene as hy
from tests.test_scripts.test_apm_bench_cli import SCENARIO, _fake_driver
from tests.test_scripts.test_apm_bench_hygiene import CANARY, VERSIONS, _record


@pytest.fixture(autouse=True)
def _quarantine_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관문 실패 격리본이 실제 홈(`~/apm_bench_quarantine`)에 쌓이지 않게 한다(plans/164 W2a) · E2
    로컬 기본 파일 자동 탐색이 실제 홈을 보지 않게 한다(W2b)."""
    from scripts.apm_bench import rundir

    monkeypatch.setattr(rundir, "default_quarantine_base", lambda: tmp_path / "quarantine")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


#: 보기 id 낱말(business·trend·jennifer·server)을 토큰으로 품는 합성 인벤토리(1회차 위반 모양).
INVENTORY = ["business-trend01", "jennifer-server02", "ord-was01", "ORD_api02", "pay-was01"]
USERS = {"login_id": "benchop", "os_user": "zbench", "host": "zbench-mac.lan",
         "home": "/home/zbench"}
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig"


def _failed(**observed: Any) -> dict[str, Any]:
    """실패 턴(근거 묶음 `full`) — 관측 칸을 덮어쓴다."""
    rec = _record()
    rec["observed"].update(observed)
    return rec


def _sanitize(records: list[dict[str, Any]], users: dict[str, str] | None = None,
              ) -> tuple[hy.RunSanitizer, hy.SanitizedRun]:
    san = hy.RunSanitizer(canaries=[CANARY], user_values=users or {})
    return san, san.sanitize(records, inventory_names=INVENTORY)


def _staged(out: hy.SanitizedRun) -> dict[str, str]:
    for rec in out.records:
        rec["evidence"] = hy.build_evidence(rec, versions=VERSIONS)
    return hy.stage_outputs(out.records, out.run_meta, out.probe_rows, "# coverage\n",
                            "# report\n")


def _gate(san: hy.RunSanitizer, records: list[dict[str, Any]],
          users: dict[str, str] | None = None) -> hy.ApmLeakGate:
    return hy.make_gate(inventory_names=INVENTORY, placeholders=records[0]["placeholders"],
                        user_values=users or {}, canaries=[CANARY], sanitizer=san)


def _reselect(**over: Any) -> dict[str, Any]:
    return {"happened": True, "candidates": ["apm.jennifer_server", "apm.business"],
            "parsed_views": ["apm.business_trend"], "parsed_view_args": {},
            "result_views": ["apm.business_trend"], "reason": "agreed", **over}


# --- ① 보기 id 낱말 ∩ 인벤토리 토큰 -------------------------------------------------------


def test_view_words_overlap_inventory_tokens_pass_gate(tmp_path: Path) -> None:
    rec = _failed(reselect=_reselect())
    san, out = _sanitize([rec])
    # 1회차와 같은 겹침 — 4라운드부터 보기 id 낱말은 보호 낱말이라 토큰 사전에 없다
    assert {"business", "trend", "jennifer", "server"}.isdisjoint(san.tokens)
    assert {"ord", "pay"} <= san.tokens
    staged = _staged(out)
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []
    trace = json.loads(staged["trace.jsonl"].splitlines()[0])
    ev = trace["evidence"]["reselect"]
    assert "output" not in ev and "candidates" not in ev  # 일반 이름 키 없음
    assert ev["result_views"] == ["apm.business_trend"]
    assert ev["candidate_views"] == ["apm.jennifer_server", "apm.business"]
    observed = trace["observed"]["reselect"]
    assert observed["candidate_views"] == ["apm.jennifer_server", "apm.business"]
    assert "candidates" not in observed
    # 대상 후보(인스턴스 원이름)는 이름이 그대로 — 치환·토큰 대조 대상(SKIP 밖)
    assert "candidates" in trace["observed"]["targets"][0]
    assert "ord-was01" not in staged["trace.jsonl"]


def test_generic_key_view_words_pass_but_name_tokens_still_trip() -> None:
    """일반 이름 키(`output`)라도 보기 id 낱말은 보호 낱말이라 토큰 대조에 걸리지 않고(4라운드 —
    1회차 거짓 양성의 원인 제거), 원이름 토큰(`ord`)은 여전히 걸린다."""
    rec = _failed(reselect=_reselect())
    san, out = _sanitize([rec])
    staged = _staged(out)
    line = json.loads(staged["trace.jsonl"].splitlines()[0])
    line["evidence"]["reselect"]["output"] = ["apm.business_trend", "ord 서버"]
    bad = {**staged, "trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"}
    fields = {v["field"] for v in _gate(san, [rec]).check(bad) if v["rule"] == "pii_value"}
    assert fields == {"evidence.reselect.output[1]"}


# --- ② 사용자 정보 부분 포함 응답 --------------------------------------------------------


def test_user_info_in_free_text_replaced_and_counted(tmp_path: Path) -> None:
    rec = _failed(
        response="프로세스 소유자 zbench · 홈 /home/zbench/app · 실행 호스트 zbench-mac.lan",
        answer_lines=["user=zbench pid 4242"],
        clarify_text="zbench 계정 프로세스를 볼까요?",
        disclosures=[{"kind": "apm_unresolved_condition", "text": "조회자 benchop"}],
    )
    san, out = _sanitize([rec], USERS)
    staged = _staged(out)
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec], USERS))
    assert ok and violations == []
    observed = out.records[0]["observed"]
    assert hy.USER_INFO_MARK in observed["response"]
    assert observed["response"].count(hy.USER_INFO_MARK) == 3  # 홈이 OS 사용자보다 먼저
    assert observed["answer_lines"] == [f"user={hy.USER_INFO_MARK} pid 4242"]
    assert observed["disclosures"][0]["text"] == f"조회자 {hy.USER_INFO_MARK}"
    assert san.user_info_counts == {"home": 1, "host": 1, "os_user": 3, "login_id": 1}
    for value in USERS.values():
        assert value not in json.dumps(san.user_info_counts)


def test_user_info_replaced_after_name_substitution() -> None:
    """원이름 치환이 먼저다 — 사용자 정보 값이 원이름 일부여도 원이름은 가짜 값으로 바뀐다."""
    rec = _failed(response="ord-was01 응답")
    san, out = _sanitize([rec], {"host": "was01"})
    response = out.records[0]["observed"]["response"]
    assert "ord-was01" not in response and hy.USER_INFO_MARK not in response
    assert san.user_info_counts == {"host": 0}


def test_cli_reports_user_info_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"os_user": "zz-run-user-9"})
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(out)])
    code = cli.cmd_run(args, driver_run=_fake_driver({}, response="조회자 zz-run-user-9"))
    assert code == 0
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert meta["user_info_replaced"] == {"os_user": 1}
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "사용자 정보 치환" in report and "os_user 1" in report
    for name in ("trace.jsonl", "run.json", "report.md"):
        assert "zz-run-user-9" not in (out / name).read_text(encoding="utf-8")


# --- ③ 관문 미완화 ----------------------------------------------------------------------


@pytest.mark.parametrize("injected, rule", [
    ("ord-was01", "pii_value"),
    ("business-trend01", "pii_value"),
    (CANARY, "canary"),
    (JWT, "user_info"),
    ("zbench", "user_info"),
])
def test_gate_still_blocks_raw_values_in_free_text(tmp_path: Path, injected: str,
                                                   rule: str) -> None:
    rec = _failed(reselect=_reselect())
    san, out = _sanitize([rec], USERS)
    staged = _staged(out)
    line = json.loads(staged["trace.jsonl"].splitlines()[0])
    line["observed"]["response"] += " " + injected  # 치환 뒤 주입 — 관문만 본다
    bad = {**staged, "trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"}
    ok, violations = hy.write_outputs(tmp_path, bad, _gate(san, [rec], USERS))
    assert not ok and rule in {v["rule"] for v in violations}
    assert [p.name for p in tmp_path.iterdir()] == ["leak_check.json"]


@pytest.mark.parametrize("path, value", [
    (("observed", "agents"), ["주문 처리기"]),
    (("observed", "calls", 0, "tool"), "apm tool"),
    (("covers",), ["COV ord"]),
    (("verdict", "locus"), ["F1:apm.trace:args.주문"]),
    (("observed", "plan_raw", 0, "areas"), ["was instance"]),
])
def test_code_word_keys_reject_free_strings(path: tuple[Any, ...], value: Any) -> None:
    """치환기를 우회해 코드 어휘 칸(SKIP_KEYS)에 `CODE_WORD` 모양이 아닌 값이 남으면 관문
    `pii_value`(위치만) — 2차 방어."""
    rec = _failed()
    san, out = _sanitize([rec])
    staged = _staged(out)
    line = json.loads(staged["trace.jsonl"].splitlines()[0])
    node = line
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    bad = {**staged, "trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"}
    violations = _gate(san, [rec]).check(bad)
    assert any(v["rule"] == "pii_value" and v["field"].startswith(
        ".".join(str(p) for p in path if not isinstance(p, int)).split(".")[0])
        for v in violations)
    assert all(str(value) not in json.dumps(v, ensure_ascii=False) for v in violations)


def _free_code_record() -> dict[str, Any]:
    """코드 어휘 칸에 LLM 원출력 모양(한글·공백)이 섞인 실패 턴."""
    bad_locus = "F1:apm.trace:args.한글 인자"
    rec = _failed()
    rec["observed"]["plan_raw"][0].update(
        areas=["was_instance", "주문 영역"], task_id="t 1",
        view_args={"apm.trace": {"kind": "ord-was01 종류"}})
    rec["verdict"].update(
        categories=["arg_hallucination"], locus=[bad_locus, "code:resolve_apm_targets"],
        axis_detail={"categories": {"arg_hallucination": {
            "fewshot_fixable": True, "locus": [bad_locus, "F1"]}}})
    return rec


def test_sanitizer_drops_free_strings_in_code_word_keys(tmp_path: Path) -> None:
    rec = _free_code_record()
    raw = copy.deepcopy(rec)
    san, out = _sanitize([rec])
    assert rec == raw  # 입력(판정 원본)은 바꾸지 않는다
    staged = _staged(out)
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []
    got = out.records[0]
    task = got["observed"]["plan_raw"][0]
    assert task["areas"] == ["was_instance"] and task["areas_dropped"] == 1
    assert task["task_id"] is None and task["task_id_dropped"] == 1
    # 인자 묶음 아래 `kind`는 이름 면제가 없다 — 어휘 대조가 아니라 일반 치환(plans/164 M-3)
    kind = task["view_args"]["apm.trace"]["kind"]
    assert "ord-was01" not in kind and kind.endswith(" 종류")
    assert "kind_dropped" not in task["view_args"]["apm.trace"]
    verdict = got["verdict"]
    assert verdict["locus"] == ["code:resolve_apm_targets"] and verdict["locus_dropped"] == 1
    assert verdict["categories"] == ["arg_hallucination"] and "categories_dropped" not in verdict
    detail = verdict["axis_detail"]["categories"]["arg_hallucination"]  # 키 이름 그대로
    assert detail == {"fewshot_fixable": True, "locus": ["F1"]}
    assert verdict["axis_detail"]["categories_dropped"] == 1
    for text in ("한글 인자", "주문 영역", "ord-was01"):
        assert text not in staged["trace.jsonl"]


def test_dropping_code_words_keeps_judgement_and_counts() -> None:
    """판정은 치환 전에 끝난다 — 턴 통과·축·분류 집계는 같고, 위치 집계에서 버린 위치만 빠진다."""
    from scripts.apm_bench.judge import summarize

    rec = _free_code_record()
    _san, out = _sanitize([rec])
    before, after = summarize([rec]), summarize(out.records)
    for key in ("counts", "turn", "axes", "categories", "primary_categories",
                "primary_failure_categories"):
        assert after[key] == before[key], key
    assert out.records[0]["verdict"]["turn_pass"] is rec["verdict"]["turn_pass"]
    assert set(before["fewshot_locus"]) - set(after["fewshot_locus"]) == {
        "F1:apm.trace:args.한글 인자"}


def test_code_word_shapes_of_real_values_pass() -> None:
    for word in ("apm.business_trend", "code:resolve_apm_targets", "F1:apm.trace:example#2",
                 "sources[].terms", "2026-10-14T10:24:39+09:00", "COV-RT-INSTANCE.instance_id",
                 "none", ""):
        assert hy.CODE_WORD.fullmatch(word), word
    for word in ("주문", "was instance", "a/b"):
        assert not hy.CODE_WORD.fullmatch(word), word


# --- ④ 사용자 정보 ↔ 인벤토리 충돌 -------------------------------------------------------


@pytest.mark.parametrize("users, kinds", [
    ({"host": "was01"}, ["host"]),  # 실행 호스트가 운영 이름 조각(ord-was01)
    ({"host": "WAS01"}, ["host"]),  # 대소문자 무시
    ({"home": "/home/ord-was01"}, ["home"]),  # 운영 이름이 사용자 정보 안에
    ({"os_user": "pay"}, ["os_user"]),  # 토큰 ⊂ 사용자 정보(영숫자 경계)
    ({"os_user": "payroll"}, []),  # 영숫자 경계 밖 — 치환기도 건드리지 않는다
    ({"login_id": "op"}, []),  # 3자 미만은 관문도 보지 않는다
    ({"login_id": "benchop", "os_user": None}, []),  # 값 없음은 건너뛴다
])
def test_user_info_conflicts(users: dict[str, str], kinds: list[str]) -> None:
    assert hy.user_info_conflicts(users, INVENTORY) == kinds


def test_user_info_conflicts_respects_term_policy() -> None:
    """허용 목록 토큰은 치환되지 않으므로 반대 방향(토큰 ⊂ 사용자 정보) 충돌이 아니다."""
    users = {"os_user": "app-pay"}
    assert hy.user_info_conflicts(users, ["pay-was01"]) == ["os_user"]
    policy = hy.TermPolicy(frozenset({"pay", "was"}))
    assert hy.user_info_conflicts(users, ["pay-was01"], policy) == []


def _e2_args(out: Path) -> Any:
    return cli.build_parser().parse_args(
        ["--run", "--env", "e2", "--only", SCENARIO, "--out", str(out),
         "--placeholders", str(cli.PLACEHOLDERS_E1), "--view-server-version", "5.6.2.18",
         "--term-policy", str(hy.TERM_POLICY_PATH)])


def _no_outputs(out: Path) -> bool:
    """턴 송신 전 중단 — 산출물·관문 기록 없음(run 디렉터리 관리 파일만 · plans/164 W2a)."""
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    return (meta["status"] == "aborted"
            and not {"trace.jsonl", "leak_check.json", "summary_safe.json"}
            & {p.name for p in out.iterdir()})


def _checking_driver(names: list[str], sent: list[str]) -> Any:
    base = _fake_driver({})

    def run(*a: Any, **k: Any) -> Any:
        k["inventory_check"](names)  # 드라이버와 같이 수집 직후 · 턴 송신 전
        sent.append("turns")
        return base(*a, **k)

    return run


def test_host_fragment_of_inventory_name_exits_2_before_turns(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"host": "zq-host7"})
    sent: list[str] = []
    out = tmp_path / "out"
    code = cli.cmd_run(_e2_args(out),
                       driver_run=_checking_driver(["ord-zq-host7.corp"], sent))
    assert code == 2 and sent == [] and _no_outputs(out)
    printed = capsys.readouterr().out
    assert "host" in printed and "zq-host7" not in printed  # 종류만 — 값 없음
    assert "zq-host7" not in (out / "run.log").read_text(encoding="utf-8")


def test_e1_conflict_warns_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"host": "zq-host7"})
    sent: list[str] = []
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(out)])
    assert cli.cmd_run(args, driver_run=_checking_driver(["ord-zq-host7.corp"], sent)) == 0
    assert sent == ["turns"]
    printed = capsys.readouterr().out
    assert "[경고]" in printed and "zq-host7" not in printed


# --- ⑤ 보기 id 칸 값 검증 ----------------------------------------------------------------


def test_parsed_views_non_view_strings_dropped_from_trace_and_evidence(tmp_path: Path) -> None:
    rec = _failed(reselect=_reselect(parsed_views=["apm.business_trend", "ord-was01", "주문 서버"],
                                     candidates=["apm.business", "pay-was01"]))
    rec["observed"]["plan_raw"][0]["views"] = ["apm.trace", "ORD_api02"]
    rec["observed"]["calls"][0]["view"] = "ord-was01"
    raw = copy.deepcopy(rec)
    san, out = _sanitize([rec])
    assert rec == raw  # 입력은 바꾸지 않는다
    staged = _staged(out)
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []
    dumped = staged["trace.jsonl"]
    for name in ("ord-was01", "ORD_api02", "pay-was01", "주문 서버"):
        assert name not in dumped
    observed = out.records[0]["observed"]
    assert observed["reselect"]["parsed_views"] == ["apm.business_trend"]
    assert observed["reselect"]["parsed_views_dropped"] == 2
    assert observed["reselect"]["candidate_views"] == ["apm.business"]
    assert observed["reselect"]["candidate_views_dropped"] == 1
    assert observed["plan_raw"][0]["views"] == ["apm.trace"]
    assert observed["plan_raw"][0]["views_dropped"] == 1
    assert observed["calls"][0]["view"] is None and observed["calls"][0]["view_dropped"] == 1
    ev = out.records[0]["evidence"]["reselect"]
    assert ev["parsed_views"] == ["apm.business_trend"] and ev["parsed_views_dropped"] == 2
    assert ev["candidate_views_dropped"] == 1


def test_view_detail_mapping_keeps_judge_scalars() -> None:
    """판정 상세(`verdict.axis_detail.views`)는 목록만 거르고 판정기 스칼라(basis)는 둔다."""
    detail = {"expected": ["apm.trace"], "observed": ["apm.trace", "ord-was01"],
              "basis": "plan_final", "reselected": False}
    rec = _failed()
    rec["verdict"]["axis_detail"] = {"views": detail}
    _san, out = _sanitize([rec])
    got = out.records[0]["verdict"]["axis_detail"]
    assert got["views"] == {"expected": ["apm.trace"], "observed": ["apm.trace"],
                            "basis": "plan_final", "reselected": False}
    assert got["views_dropped"] == 1


def test_clean_views_are_byte_identical() -> None:
    """보기 id 만 있는 칸은 그대로 — 건수 칸을 더하지 않는다."""
    rec = _failed(reselect=_reselect())
    _san, out = _sanitize([rec])
    dumped = json.dumps(out.records[0], ensure_ascii=False)
    assert hy.DROPPED_SUFFIX not in dumped
    assert out.records[0]["observed"]["plan_raw"][0]["views"] == ["apm.trace"]


# --- 교정 2라운드(verifier · security-auditor 지적) -----------------------------------------


@pytest.mark.parametrize("users, names, sent, code", [
    # DNS 접미만 공유 — 호스트 짧은 이름·홈 마지막 성분만 대조하므로 통과
    ({"host": "zq-dev7.corp.bank.co.kr", "home": "/home/zq-op"},
     ["ord-was01.corp.bank.co.kr", "pay-was01.corp.bank.co.kr"], ["turns"], None),
    # 짧은 이름이 인벤토리 이름 조각 — 턴 송신 전 exit 2
    ({"host": "zq-host7.corp.bank.co.kr"}, ["ord-zq-host7.corp.bank.co.kr"], [], 2),
    # 사용자명이 인벤토리 이름 조각 — 턴 송신 전 exit 2
    ({"login_id": "zqadmin"}, ["zqadmin-was01"], [], 2),
])
def test_e2_conflict_compares_short_names_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        users: dict[str, str], names: list[str], sent: list[str], code: int | None,
) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: users)
    got: list[str] = []
    out = tmp_path / "out"
    rc = cli.cmd_run(_e2_args(out), driver_run=_checking_driver(names, got))
    assert got == sent
    printed = capsys.readouterr().out
    if code is None:
        assert rc != 2 and "[중단] 사용자 정보" not in printed
        return
    assert rc == code and _no_outputs(out)
    assert "전용 실행 계정·호스트" in printed  # 해결 방법 · 종류만
    log = (out / "run.log").read_text(encoding="utf-8")
    for value in users.values():
        assert value not in printed and value.split(".")[0] not in printed
        assert value.split(".")[0] not in log


def test_run_user_values_adds_short_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "zq-mac7.corp.example")
    assert cli.run_user_values("op01")["host_short"] == "zq-mac7"
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "zq-mac7")
    assert "host_short" not in cli.run_user_values("op01")


def test_fqdn_and_short_host_both_replaced(tmp_path: Path) -> None:
    users = {"host": "zq-mac7.corp.example", "host_short": "zq-mac7"}
    rec = _failed(response="실행 호스트 zq-mac7.corp.example · 짧게 zq-mac7")
    san, out = _sanitize([rec], users)
    response = out.records[0]["observed"]["response"]
    assert "zq-mac7" not in response and response.count(hy.USER_INFO_MARK) == 2
    assert san.user_info_counts == {"host": 1, "host_short": 1}  # 긴 값 먼저
    ok, violations = hy.write_outputs(tmp_path, _staged(out), _gate(san, [rec], users))
    assert ok and violations == []


def test_user_info_replaced_case_insensitively(tmp_path: Path) -> None:
    """대문자 변형(ZBENCH · BENCHOP)도 치환한다 — 관문 `user_info`(대소문자 구분)는 그대로."""
    rec = _failed(response="소유자 ZBENCH · 경로 /HOME/ZBENCH/x · 호스트 ZBench-Mac.LAN · BENCHOP")
    san, out = _sanitize([rec], USERS)
    staged = _staged(out)
    for value in ("ZBENCH", "ZBench", "BENCHOP", "zbench", "benchop"):
        assert value not in staged["trace.jsonl"]
    assert san.user_info_counts == {"home": 1, "host": 1, "os_user": 1, "login_id": 1}
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec], USERS))
    assert ok and violations == []


def test_error_fields_get_user_info_replacement(tmp_path: Path) -> None:
    """턴 실패 사유·호출 오류 문장도 자유 문장 처리 — 관문 위반으로 반출 전체가 0이 되지 않는다."""
    rec = _failed(error="host zbench-mac.lan timeout")
    rec["observed"]["calls"][0]["error"] = "open /home/zbench/x failed"
    san, out = _sanitize([rec], USERS)
    staged = _staged(out)
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec], USERS))
    assert ok and violations == []
    observed = out.records[0]["observed"]
    assert observed["error"] == f"host {hy.USER_INFO_MARK} timeout"
    assert observed["calls"][0]["error"] == f"open {hy.USER_INFO_MARK}/x failed"
    assert observed["calls"][0]["tool"] == "apm_transaction_trace"  # 나머지 칸은 그대로


def _words(value: Any) -> set[str]:
    """값 안의 문자열 전부(사전 키 포함)."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {w for k, v in value.items() for w in (*_words(k), *_words(v))}
    if isinstance(value, list):
        return {w for v in value for w in _words(v)}
    return set()


#: 이름 조각·변형 표기가 코드 모양으로 코드 어휘 칸에 실린 경우(security-auditor M-2 재현).
_FRAGMENTS: list[tuple[tuple[Any, ...], Any, Any]] = [
    (("observed", "plan_raw", 0, "task_id"), "t_ord_cpu", None),
    (("observed", "plan_raw", 0, "task_id"), "ordwas01", None),
    (("observed", "plan_raw", 0, "sources"), ["cm_prd", "ordapp"], ["cm_prd"]),
    (("observed", "plan_raw", 0, "areas"), ["was_instance", "ord"], ["was_instance"]),
    (("observed", "plan_raw", 0, "agent"), "ord-agent", None),
    (("observed", "calls", 0, "tool"), "ord_was_tool", None),
    (("observed", "targets", 0, "kind"), "ord-was-01", None),
    (("verdict", "categories"), ["view_wrong", "ordwas"], ["view_wrong"]),
    (("verdict", "locus"), ["F1:apm.trace:args.ORDWAS", "F1:apm.trace:args.guid"],
     ["F1:apm.trace:args.guid"]),
    (("verdict", "locus"), ["F1:ordwas01", "sources:ordapp", "sources:b0"], ["sources:b0"]),
    (("verdict", "axes"), {"source": "pass", "ordwas": "pass", "views": "ord"},
     {"source": "pass", "views": None}),
    (("observed", "plan_raw", 0, "sources"), {"ordwas": "x"}, {}),
]


@pytest.mark.parametrize("path, value, kept", _FRAGMENTS)
def test_closed_vocab_drops_name_fragments(tmp_path: Path, path: tuple[Any, ...], value: Any,
                                           kept: Any) -> None:
    rec = _failed()
    node = rec
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    san, out = _sanitize([rec])
    got = out.records[0]
    for part in path[:-1]:
        got = got[part]
    assert got[path[-1]] == kept
    assert got[f"{path[-1]}{hy.DROPPED_SUFFIX}"] >= 1
    staged = _staged(out)
    for word in _words(value) - _words(kept):
        assert json.dumps(word) not in staged["trace.jsonl"], word
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []


@pytest.mark.parametrize("path, value, kept", _FRAGMENTS)
def test_gate_blocks_fragments_that_bypass_sanitizer(path: tuple[Any, ...], value: Any,
                                                     kept: Any) -> None:
    """치환기를 우회해 반출본에 직접 실린 어휘 밖 값·사전 키 → 관문 `pii_value`(위치만)."""
    rec = _failed()
    san, out = _sanitize([rec])
    line = json.loads(_staged(out)["trace.jsonl"].splitlines()[0])
    node = line
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    bad = {"trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"}
    violations = _gate(san, [rec]).check(bad)
    assert any(v["rule"] == "pii_value" for v in violations)
    reported = json.dumps(violations)  # 위치만 — 어휘 밖 값·키는 경로에도 없다
    assert all(w not in reported for w in _words(value) - _words(kept) if len(w) >= 4)


def test_real_vocab_words_pass_even_when_they_share_inventory_tokens(tmp_path: Path) -> None:
    """코드 어휘 칸은 토큰 대조 대신 닫힌 어휘 대조다 — 저장소 어휘(`was_instance`·`cm_prd`·
    `apm_app_health`)가 인벤토리 토큰(was·prd·app)과 겹쳐도 거짓 양성이 없다."""
    inventory = ["prd-was01", "app-health09"]
    rec = _failed()
    rec["observed"]["plan_raw"][0].update(areas=["was_instance"], sources=["cm_prd"])
    rec["observed"]["calls"][0]["tool"] = "apm_app_health"
    rec["verdict"]["locus"] = ["F1:apm.trace:example#2", "sources:cm_prd", "code:route_pre_gate"]
    san = hy.RunSanitizer(canaries=[CANARY])
    out = san.sanitize([rec], inventory_names=inventory)
    assert hy.DROPPED_SUFFIX not in json.dumps(out.records[0])
    gate = hy.make_gate(inventory_names=inventory, placeholders=rec["placeholders"],
                        user_values={}, canaries=[CANARY], sanitizer=san)
    ok, violations = hy.write_outputs(tmp_path, _staged(out), gate)
    assert ok and violations == []


def test_arg_subtrees_lose_name_exemption(tmp_path: Path) -> None:
    """인자 묶음(`view_args`·`parsed_view_args`·`args`) 아래 코드 어휘 이름 칸은 일반 치환·토큰
    대조를 받는다 — 깊이와 상관없이 면제되던 구멍(security-auditor M-3)."""
    rec = _failed(reselect=_reselect(parsed_view_args={"apm.trace": {"areas": "ord-was01"}}))
    rec["observed"]["plan_raw"][0]["view_args"] = {"apm.trace": {"kind": "ord-was01",
                                                                 "field": "pay"}}
    rec["observed"]["calls"][0]["args"]["tool"] = "ORD_api02"
    san, out = _sanitize([rec])
    staged = _staged(out)
    for name in ("ord-was01", "ORD_api02", '"pay"'):
        assert name not in staged["trace.jsonl"]
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []
    # 치환기를 우회한 원값은 관문이 토큰으로 막는다
    line = json.loads(staged["trace.jsonl"].splitlines()[0])
    line["observed"]["calls"][0]["args"]["kind"] = "ord"
    bad = {"trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"}
    fields = {v["field"] for v in _gate(san, [rec]).check(bad) if v["rule"] == "pii_value"}
    assert "observed.calls[0].args.kind" in fields


def test_error_code_copy_matches_gateway_source() -> None:
    """`record.GATEWAY_ERROR_CODES`(벤치 사본)가 게이트웨이 정본 오류 어휘와 같다(import 0 —
    파일 텍스트로 대조 · D-274)."""
    import re

    from scripts.apm_bench import REPO_ROOT
    from scripts.apm_bench.record import GATEWAY_ERROR_CODES

    text = (REPO_ROOT / "apm_gateway" / "apm_gateway" / "domain" / "errors.py").read_text(
        encoding="utf-8")
    consts = dict(re.findall(r'^([A-Z_]+) = "([a-z_]+)"$', text, flags=re.MULTILINE))
    block = re.search(r"ERROR_CODES: frozenset\[str\] = frozenset\(\s*\{(.*?)\}", text, re.S)
    assert block is not None
    names = re.findall(r"[A-Z_]+", block.group(1))
    assert {consts[n] for n in names} == GATEWAY_ERROR_CODES


@pytest.mark.parametrize("reason, code", [
    ("ordwas01: 응답 없음", None),  # 소문자 호스트명 접두 — 코드 모양이어도 어휘 밖
    ("bankweb: instance_unresolved", None),
    ("instance_unresolved: 대상 없음", "instance_unresolved"),
    ("job_not_ready: 진행 중", "job_not_ready"),
    ("batch_missing: 배치 결과에 이 대상의 항목이 없습니다", "batch_missing"),
])
def test_error_code_accepts_known_codes_only(reason: str, code: str | None) -> None:
    from scripts.apm_bench import driver as dv

    assert dv._error_code(reason) == code


def test_closed_vocab_keeps_every_judged_scenario_turn_and_summary() -> None:
    """실 시나리오 전 턴(기대값 그대로의 합성 관측 + 실 판정) — 코드 어휘 칸에서 버리는 값이 0이고
    판정 집계(summary)가 치환 전후 같다(판정은 치환 전 · 어휘가 실값을 막지 않는다)."""
    from scripts.apm_bench import judge as jd
    from scripts.apm_bench import scenarios as sc
    from tests.test_scripts.test_apm_bench_judge import synth

    registry = sc.load_registry_info()
    records = []
    for scenario in sc.load_scenarios():
        for index in range(len(scenario.turns)):
            rec = synth(scenario, index, registry)
            rec["verdict"] = dict(jd.judge_turn(scenario, index, rec, registry))
            records.append(rec)
    san = hy.RunSanitizer(canaries=[CANARY])
    out = san.sanitize(records, inventory_names=INVENTORY)
    assert len(out.records) > 100
    dumped = json.dumps(out.records, ensure_ascii=False)
    closed = hy.SKIP_KEYS | hy.SOURCE_ID_KEYS | set(hy.SOURCE_CONTEXT_FIELDS)
    dropped = [k for k in closed if f'"{k}{hy.DROPPED_SUFFIX}"' in dumped]
    assert dropped == []
    before, after = jd.summarize(records), jd.summarize(out.records)
    assert after == before


# --- 교정 3라운드(security-auditor 재감사 N-1~N-3) -----------------------------------------

#: 레지스트리 밖 소스(E2 게이트웨이 `.env` 소스 모양) — 합성 값.
ALIEN_SOURCE = "zetaledger-prd"
ALIEN_LABEL = "원장시스템 운영"


def _source_record() -> dict[str, Any]:
    """소스 칸이 전부 레지스트리 값인 실패 턴(소스 범위 · 소스 되묻기 · 자동 답 · 소스 축 상세)."""
    rec = _failed(
        apm_source_scope={"ids": ["cm_prd", "*"], "basis": "named",
                          "last_target_sources": ["cm_prd"],
                          "last_targets": [{"text": "ord-was01", "kind": "instance",
                                            "source_id": "cm_prd"}]},
        apm_source_clarification={"question": "어느 제니퍼를 볼까요?",
                                  "options": [{"key": "cm_prd", "label": "공동존 운영 제니퍼",
                                               "zone": "gongjon"},
                                              {"key": "*", "label": "전체", "zone": ""}],
                                  "allow_all": True, "multi": True,
                                  "original_query": "ord-was01 SQL 통계"},
    )
    rec["observed"]["calls"][0]["args"]["source_ids"] = ["b0"]
    rec["observed"]["calls"][0]["sources"] = ["b0"]
    rec["extra"] = {"source_auto_answer": {
        "source": "cm_stg", "sent": True,
        "first": {"apm_called": False, "clarification": {
            "options": [{"key": "cm_stg", "label": "공동존 개발/스테이징 제니퍼"}]}}}}
    rec["verdict"]["axis_detail"] = {"source": {"expected_sources": ["cm_prd"],
                                                "observed_sources": ["cm_prd"]}}
    return rec


#: 레지스트리 밖 소스 id·라벨이 소스 칸에 실린 경우(security-auditor N-1 · repro2 item4 모양).
_SOURCE_CASES: list[tuple[tuple[Any, ...], Any, Any]] = [
    (("observed", "apm_source_scope", "ids"), [ALIEN_SOURCE, "cm_prd", "*"], ["cm_prd", "*"]),
    (("observed", "apm_source_scope", "last_target_sources"), [ALIEN_SOURCE], []),
    (("observed", "apm_source_scope", "last_target_sources"), {"x": [ALIEN_SOURCE]}, {}),
    (("observed", "apm_source_scope", "last_targets", 0, "source_id"), ALIEN_SOURCE, None),
    (("observed", "apm_source_clarification", "options", 0, "key"), ALIEN_SOURCE, None),
    (("observed", "apm_source_clarification", "options", 0, "id"), ALIEN_SOURCE, None),
    (("observed", "apm_source_clarification", "options", 0, "label"), ALIEN_LABEL, None),
    (("observed", "calls", 0, "args", "source_ids"), [ALIEN_SOURCE, "b0"], ["b0"]),
    (("observed", "calls", 0, "sources"), [ALIEN_SOURCE, "b0"], ["b0"]),
    (("extra", "source_auto_answer", "source"), ALIEN_SOURCE, None),
    (("extra", "source_auto_answer", "first", "clarification", "options", 0, "label"),
     ALIEN_LABEL, None),
    (("verdict", "axis_detail", "source", "observed_sources"), [ALIEN_SOURCE, "cm_prd"],
     ["cm_prd"]),
]


def _put(rec: dict[str, Any], path: tuple[Any, ...], value: Any) -> None:
    node: Any = rec
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def _get(rec: dict[str, Any], path: tuple[Any, ...]) -> Any:
    node: Any = rec
    for part in path:
        node = node[part]
    return node


def test_registry_source_fields_pass_unchanged(tmp_path: Path) -> None:
    """레지스트리 소스 id·라벨·「전체」 표지(`*`·전체)는 저장소 공개 값 — 그대로 반출 · 버림 0."""
    rec = _source_record()
    san, out = _sanitize([rec])
    got = out.records[0]
    assert got["observed"]["apm_source_scope"]["ids"] == ["cm_prd", "*"]
    options = got["observed"]["apm_source_clarification"]["options"]
    assert [o["label"] for o in options] == ["공동존 운영 제니퍼", "전체"]
    assert got["extra"]["source_auto_answer"]["source"] == "cm_stg"
    assert got["observed"]["calls"][0]["args"]["source_ids"] == ["b0"]
    assert hy.DROPPED_SUFFIX not in json.dumps(got, ensure_ascii=False)
    ok, violations = hy.write_outputs(tmp_path, _staged(out), _gate(san, [rec]))
    assert ok and violations == []


@pytest.mark.parametrize("path, value, kept", _SOURCE_CASES)
def test_unregistered_source_ids_and_labels_dropped(tmp_path: Path, path: tuple[Any, ...],
                                                    value: Any, kept: Any) -> None:
    """N-1 — 레지스트리 밖 소스 id·라벨은 소스 칸에서 버리고 건수만(`<칸>_dropped`) · 관문 통과."""
    rec = _source_record()
    _put(rec, path, value)
    san, out = _sanitize([rec])
    got = out.records[0]
    assert _get(got, path) == kept
    assert _get(got, (*path[:-1], f"{path[-1]}{hy.DROPPED_SUFFIX}")) >= 1
    staged = _staged(out)
    assert "zetaledger" not in staged["trace.jsonl"] and "원장시스템" not in staged["trace.jsonl"]
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []


@pytest.mark.parametrize("path, value, kept", _SOURCE_CASES)
def test_gate_blocks_unregistered_sources_that_bypass_sanitizer(
        path: tuple[Any, ...], value: Any, kept: Any) -> None:
    """치환기를 우회해 소스 칸에 직접 실린 레지스트리 밖 소스 id·라벨 → 관문 `pii_value`(위치만)."""
    rec = _source_record()
    san, out = _sanitize([rec])
    line = json.loads(_staged(out)["trace.jsonl"].splitlines()[0])
    _put(line, path, value)
    violations = _gate(san, [rec]).check(
        {"trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"})
    assert any(v["rule"] == "pii_value" for v in violations)
    reported = json.dumps(violations, ensure_ascii=False)
    assert "zetaledger" not in reported and "원장시스템" not in reported


def test_unregistered_sources_from_audit_lines() -> None:
    """감사 줄 소스(`calls[].sources` · 가드 `calls_by_source`) 중 레지스트리 밖만(정렬)."""
    rec = _failed()
    rec["observed"]["calls"][0]["sources"] = ["b0", ALIEN_SOURCE]
    meta = {"guard": {"calls_by_source": {"cm_prd": 3, "zeta-dr": 1, "?": 2}}}
    assert hy.unregistered_sources([rec], meta, {"b0", "cm_prd", "cm_stg"}) == [
        "zeta-dr", ALIEN_SOURCE]
    assert hy.unregistered_sources([_failed()], {}, {"b0"}) == []


def test_unregistered_source_in_free_text_is_substituted(tmp_path: Path) -> None:
    """레지스트리 밖 소스 id 를 원이름 대조 집합에 합치면 자유 문장·실행 메타에서도 바뀌고, 남으면
    관문이 막는다(`produce_outputs`가 `unregistered_sources`를 합친다)."""
    rec = _failed(response=f"{ALIEN_SOURCE} 제니퍼에서 조회했다")
    rec["observed"]["calls"][0]["sources"] = [ALIEN_SOURCE]
    names = [*INVENTORY, *hy.unregistered_sources([rec], {}, {"b0", "cm_prd", "cm_stg"})]
    san = hy.RunSanitizer(canaries=[CANARY])
    out = san.sanitize([rec], inventory_names=names,
                       run_meta={"aborted": f"budget:{ALIEN_SOURCE}"})
    staged = _staged(out)
    assert all("zetaledger" not in body for body in staged.values())
    gate = hy.make_gate(inventory_names=names, placeholders=rec["placeholders"],
                        user_values={}, canaries=[CANARY], sanitizer=san)
    ok, violations = hy.write_outputs(tmp_path, staged, gate)
    assert ok and violations == []
    leaked = dict(staged, **{"report.md": f"# r\n소스 {ALIEN_SOURCE}\n"})
    assert any(v["rule"] == "pii_value" for v in gate.check(leaked))


@pytest.mark.parametrize("users, kind", [
    ({"host": "was.corp.example", "host_short": "was"}, "host"),  # 보기·영역 id 조각(was_*)
    ({"os_user": "dev"}, "os_user"),  # 시나리오 split 값
])
def test_e2_user_value_inside_code_vocab_exits_2_before_turns(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        users: dict[str, str], kind: str,
) -> None:
    """N-2 — 사용자 정보가 코드 어휘·산출물 키 이름의 조각이면 E2 는 턴 송신 전 exit 2(종류만)."""
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: users)
    sent: list[str] = []
    out = tmp_path / "out"
    code = cli.cmd_run(_e2_args(out), driver_run=_checking_driver(["zq-node01"], sent))
    assert code == 2 and sent == [] and _no_outputs(out)
    printed = capsys.readouterr().out
    assert kind in printed and hy.CONFLICT_VOCAB in printed
    assert hy.CONFLICT_INVENTORY not in printed and "전용 실행 계정·호스트" in printed
    log = (out / "run.log").read_text(encoding="utf-8")
    for value in users.values():
        assert f" {value} " not in printed and value not in log.replace(kind, "")


def test_e1_user_value_inside_code_vocab_warns_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"os_user": "dev"})
    sent: list[str] = []
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(tmp_path / "out")])
    cli.cmd_run(args, driver_run=_checking_driver(["zq-node01"], sent))
    assert sent == ["turns"]
    assert "[경고]" in capsys.readouterr().out


def test_user_info_vocab_conflicts() -> None:
    words = {"was_instance", "split", "dev", "apm.trace"}
    assert hy.user_info_vocab_conflicts({"host": "WAS.corp", "os_user": "zq9"}, words) == ["host"]
    assert hy.user_info_vocab_conflicts({"home": "/home/split"}, words) == ["home"]
    assert hy.user_info_vocab_conflicts({"login_id": "de"}, words) == []  # 3자 미만


#: LLM 원출력 사전 안의 벤치 소유 칸(security-auditor N-3) — 인벤토리 밖 이름도 실린다.
_NESTED_BENCH: list[tuple[str, Any]] = [
    ("scenario_id", "zetaledger01"),
    ("split", "SECRETAPP"),
    ("covers", ["zetaledger01"]),
    ("v", "ord-was01"),
    ("sent_at", "zeta"),
]


@pytest.mark.parametrize("key, value", _NESTED_BENCH)
def test_nested_bench_keys_lose_exemption(tmp_path: Path, key: str, value: Any) -> None:
    """N-3 — 벤치 소유 칸 면제는 레코드 최상위뿐 — `plan_raw` 안 같은 이름 칸은 버린다."""
    rec = _failed()
    rec["observed"]["plan_raw"][0][key] = value
    san, out = _sanitize([rec])
    task = out.records[0]["observed"]["plan_raw"][0]
    assert task[key] in (None, []) and task[f"{key}{hy.DROPPED_SUFFIX}"] >= 1
    assert out.records[0][key] == rec[key]  # 최상위는 그대로
    staged = _staged(out)
    for word in _words(value):
        assert json.dumps(word) not in staged["trace.jsonl"]
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []


@pytest.mark.parametrize("key, value", _NESTED_BENCH)
def test_gate_blocks_nested_bench_keys(key: str, value: Any) -> None:
    rec = _failed()
    san, out = _sanitize([rec])
    line = json.loads(_staged(out)["trace.jsonl"].splitlines()[0])
    line["observed"]["plan_raw"][0][key] = value
    violations = _gate(san, [rec]).check(
        {"trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"})
    assert any(v["rule"] == "pii_value" and "plan_raw" in (v["field"] or "")
               for v in violations)


def test_bench_keys_kept_at_owned_positions(tmp_path: Path) -> None:
    """레코드·P층 행 최상위와 근거 묶음 사본(`evidence.question`·`evidence.window`·
    `evidence.execution.probe[]`)의 벤치 소유 칸은 모양만 — 버림 0 · 관문 통과."""
    probe_row = {"scenario_id": "APM-T1", "turn": 0, "kind": "empty_check", "view": "apm.trace"}
    rec = _failed()
    rec["probe"] = [probe_row]
    san = hy.RunSanitizer(canaries=[CANARY])
    out = san.sanitize([rec], inventory_names=INVENTORY, probe_rows=[probe_row])
    staged = _staged(out)
    assert hy.DROPPED_SUFFIX not in staged["trace.jsonl"] + staged["probe.jsonl"]
    line = json.loads(staged["trace.jsonl"].splitlines()[0])
    assert line["evidence"]["question"]["split"] == "dev"
    assert line["evidence"]["execution"]["probe"][0]["scenario_id"] == "APM-T1"
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []


# --- 교정 4라운드(E2 토큰 거짓 양성 · N-4 정규화) ------------------------------------------


def test_e2_unregistered_source_tokens_do_not_break_registry_ids(tmp_path: Path) -> None:
    """E2 모양 — 게이트웨이 `.env` 소스 `zetaledger-prd`(레지스트리 밖)를 원이름 대조 집합에 합쳐도
    토큰 `prd`는 레지스트리 id 낱말이라 토큰 사전에서 빠진다: `budget:cm_prd`·자유 문장 `cm_prd`는
    그대로 관문 통과 · `zetaledger-prd`와 라벨 「원장시스템 운영」 원문은 치환·버림·차단."""
    from scripts.apm_bench import driver
    from scripts.apm_bench import gateway as gw

    env = tmp_path / ".env"
    env.write_text(f'JENNIFER_SOURCES=["cm_prd", "{ALIEN_SOURCE}"]\n', encoding="utf-8")
    registry_ids = {"b0", "cm_prd", "cm_stg"}
    names = driver.guarded_names(INVENTORY, gw.dotenv_sources(env), registry_ids)
    assert ALIEN_SOURCE in names and "cm_prd" not in names
    rec = _source_record()
    rec["observed"]["response"] = f"cm_prd 와 {ALIEN_SOURCE} 제니퍼에서 조회했다"
    rec["observed"]["apm_source_clarification"]["options"][0].update(
        key=ALIEN_SOURCE, label=ALIEN_LABEL)
    meta = {"aborted": "budget:cm_prd",
            "guard": {"calls_by_source": {"cm_prd": 2, ALIEN_SOURCE: 1}}}
    san = hy.RunSanitizer(canaries=[CANARY])
    out = san.sanitize([rec], inventory_names=names, run_meta=meta)
    assert "prd" not in san.tokens and "zetaledger" in san.tokens
    staged = _staged(out)
    run = json.loads(staged["run.json"])
    assert run["aborted"] == "budget:cm_prd" and "cm_prd" in run["guard"]["calls_by_source"]
    assert out.records[0]["observed"]["response"].startswith("cm_prd 와 ")
    assert all("zetaledger" not in body and "원장시스템" not in body for body in staged.values())
    gate = hy.make_gate(inventory_names=names, placeholders=rec["placeholders"],
                        user_values={}, canaries=[CANARY], sanitizer=san)
    ok, violations = hy.write_outputs(tmp_path / "out", staged, gate)
    assert ok and violations == []
    leaked = dict(staged, **{"report.md": f"# r\n{ALIEN_SOURCE}\n"})
    assert any(v["rule"] == "pii_value" for v in gate.check(leaked))


def test_gate_drops_protected_tokens_like_sanitizer() -> None:
    """관문 토큰 집합 = 치환기와 같은 보호 낱말을 뺀 것(대칭) · 치환기가 없으면 전부(닫힌 쪽)."""
    names = ["prd-was01", "zeta-app07"]
    san = hy.RunSanitizer(canaries=[CANARY])
    san.sanitize([_failed()], inventory_names=names)
    with_san = hy.make_gate(inventory_names=names, placeholders={}, user_values={},
                            canaries=[CANARY], sanitizer=san)
    bare = hy.make_gate(inventory_names=names, placeholders={}, user_values={},
                        canaries=[CANARY])
    probe = {"run.json": json.dumps({"note": "budget:cm_prd was_instance"})}
    assert with_san.check(probe) == []
    assert any(v["rule"] == "pii_value" for v in bare.check(probe))
    assert "zeta" in san.tokens and not {"prd", "was", "app"} & san.tokens


def test_protected_words_include_safe_summary_vocab() -> None:
    """호출 측이 넘긴 안전 요약 어휘 낱말(시나리오 id·split 등)도 토큰 사전에서 빠진다."""
    names = ["holdout-zq01"]
    plain = hy.RunSanitizer(canaries=[CANARY])
    plain.sanitize([_failed()], inventory_names=names)
    guarded = hy.RunSanitizer(canaries=[CANARY], protected_words={"holdout"})
    guarded.sanitize([_failed()], inventory_names=names)
    assert "holdout" in plain.tokens and "holdout" not in guarded.tokens
    assert "zq" in guarded.tokens


def test_fullwidth_and_zero_width_variants_replaced(tmp_path: Path) -> None:
    """N-4 — 자유 문장은 NFKC + 제로폭 제거 뒤 치환한다(사용자 정보·원이름 모두)."""
    users = {"login_id": "benchop", "os_user": "zbench"}
    rec = _failed(response="ｚｂｅｎｃｈ · Z​bench · ＢＥＮＣＨＯＰ · ｏｒｄ－ｗａｓ０１ · "
                           "ord⁠-was01")
    san, out = _sanitize([rec], users)
    response = out.records[0]["observed"]["response"]
    assert response.count(hy.USER_INFO_MARK) == 3
    for word in ("zbench", "benchop", "ord-was01", "ｏｒｄ"):
        assert word not in response.casefold()
    assert "​" not in response and "⁠" not in response
    ok, violations = hy.write_outputs(tmp_path, _staged(out), _gate(san, [rec], users))
    assert ok and violations == []
