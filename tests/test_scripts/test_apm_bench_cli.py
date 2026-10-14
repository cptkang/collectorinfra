"""plans/152 통합 Wave — `python -m scripts.apm_bench` CLI 모드·exit 코드·산출물 (D-323).

드라이버(`driver.run`)는 대역으로 바꿔 끼운다 — 게이트웨이·본체·LLM 을 띄우지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import driver as dv
from scripts.apm_bench import hygiene as hy
from scripts.apm_bench import loop as lp
from scripts.apm_bench.scenarios import fill_placeholders

SCENARIO = "APM-INST-host"  # 단일 턴 · 대상 host:1 · 보기 apm.instances
OUTPUTS = {"trace.jsonl", "run.json", "probe.jsonl", "coverage.md", "report.md",
           "leak_check.json"}
#: run 디렉터리 관리 파일(plans/164 W2a) — 관문 성패와 무관하게 남는다.
RUN_DIR_FILES = {"run_meta.json", "run.log", "summary_safe.json", "manifest.sha256",
                 "scenarios.yaml"}


@pytest.fixture(autouse=True)
def _quarantine_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관문 실패 격리본이 실제 홈(`~/apm_bench_quarantine`)에 쌓이지 않게 한다 · E2 로컬 기본 파일
    (`testdata/apm_bench/local/` — plans/164 W2b) 자동 탐색이 실제 저장소 파일을 보지 않게 한다."""
    from scripts.apm_bench import rundir

    monkeypatch.setattr(rundir, "default_quarantine_base", lambda: tmp_path / "quarantine")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "LOCAL_DIR", tmp_path / "home" / "local")


def _observed(values: dict[str, str], *, apm_called: bool = True,
              response: str = "인스턴스 2개") -> dict[str, Any]:
    """APM-INST-host 정답 관측(드라이버 출력 모양)."""
    task = {"task_id": "t1", "agent": "apm_query", "views": ["apm.instances"], "view_args": {},
            "targets": [], "sources": [], "areas": ["was_instance"]}
    calls = [{"tool": "apm_instance_map", "view": "apm.instances",
              "hostname": values["host_1"], "reference": None,
              "args": {"source_ids": ["common"]}, "window": None, "rows": 2, "error": None,
              "elapsed_ms": 1, "api_calls": 0, "sources": []}] if apm_called else []
    return {"agents": ["apm_query"], "plan_raw": [task], "plan_final": [task],
            "sanitize_changes": [], "reselect": None, "targets": [], "untargeted": False,
            "time_resolution": None, "calls": calls, "apm_source_scope": None,
            "apm_source_clarification": None, "answer_lines": [], "disclosures": [],
            "clarify_kind": None, "clarify_text": None, "refused": False,
            "response": response, "response_len": len(response), "degraded_reason": None,
            "node_elapsed_ms": {}, "elapsed_ms": 10, "apm_called": apm_called, "error": None}


def _fake_driver(seen: dict[str, Any], **obs_kw: Any) -> Callable[..., Any]:
    def run(picked: Any, *, env: str, placeholders: dv.Placeholders, probe_fn: Any = None,
            **kw: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        seen.update(kw, env=env, picked=[s.id for s in picked], probe=probe_fn is not None)
        values = placeholders.values
        records: list[dict[str, Any]] = []
        for scenario in picked:
            for idx, turn in enumerate(scenario.turns):
                rec = dv._base_record(scenario, idx, turn, values,
                                      prompt=fill_placeholders(turn.prompt, values))
                rec["sent_at"] = "2026-10-13T09:00:00+09:00"
                rec["thread_id"] = "t-1"
                rec["observed"] = _observed(values, **obs_kw)
                rec["extra"] = {}
                records.append(rec)
        if probe_fn is not None:
            probe_fn(lambda name, args: {"row_count": 0}, lambda: True, records)
        called = sum(1 for r in records if r["observed"]["apm_called"])
        meta = {"env": env, "llm": kw.get("llm"), "tier": "intent_orchestration",
                "degraded_reason": "intent_flag_on",
                "planes": {"worker": "fake", "orchestrator": "fake"},
                "view_server_version": "e1-mock(해당 없음)",
                "gateway_settings": {"rate_per_sec": 2.0, "api_timeout_sec": 10.0},
                "inventory_names": [values["inst_1"], values["host_2"]],
                "inventory": {"calls": 2, "names": 2, "errors": []}, "gateway_pid": 4242,
                "guard": {"aborted": None, "calls_by_source": {"common": 1}, "total_calls": 1,
                          "timeouts": 0, "quota_hits": 0, "baseline_ms": 1.0},
                "aborted": None, "turns": len(records), "apm_observed_turns": called,
                "probe_calls": 0, "elapsed_sec": 0.1, "gateway_var_absent": True}
        return records, meta

    return run


def _run(tmp_path: Path, *extra: str, seen: dict[str, Any] | None = None,
         **obs_kw: Any) -> tuple[int, Path]:
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(out), *extra])
    code = cli.cmd_run(args, driver_run=_fake_driver({} if seen is None else seen, **obs_kw))
    return code, out


def test_run_writes_six_outputs(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    code, out = _run(tmp_path, seen=seen)
    assert code == 0
    assert {p.name for p in out.iterdir()} == OUTPUTS | RUN_DIR_FILES
    assert seen["picked"] == [SCENARIO] and seen["llm"] == "fake" and not seen["probe"]
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))
    for key in ("run_id", "sha", "dirty", "env", "tier", "planes", "llm", "view_server_version",
                "gateway_settings", "budget", "aborted", "abort_reason", "fingerprints",
                "apm_section"):
        assert key in meta, key
    assert meta["env"] == "e1" and meta["planes"] == {"worker": "fake", "orchestrator": "fake"}
    assert "inventory_names" not in meta and "gateway_pid" not in meta  # 원이름 집합 미반출
    assert not {"host", "operator", "login_user"} & set(meta)
    assert set(meta["apm_section"]) == {"fingerprint", "chars", "view_rows"}
    report = (out / "report.md").read_text(encoding="utf-8")
    assert cli.DIRECTIONAL_HEAD in report  # E1 = 방향 확인용
    assert hy.TERM_RISK_NOTE in report and "## 1. 축별 정확도" in report
    trace = [json.loads(line) for line in
             (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert trace[0]["verdict"]["turn_pass"] is True
    assert trace[0]["evidence"]["level"] == "summary"
    # 자리표 원값은 반출본에 남지 않는다(형식 보존 가짜 값)
    assert "was-host01" not in (out / "trace.jsonl").read_text(encoding="utf-8")
    assert json.loads((out / "leak_check.json").read_text(encoding="utf-8"))["passed"] is True


def test_run_probe_flag_wires_probe_fn(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    code, out = _run(tmp_path, "--probe", seen=seen)
    assert code == 0 and seen["probe"] is True
    rows = [json.loads(line) for line in
            (out / "probe.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and {r["kind"] for r in rows} == {"allowed_value"}


def test_run_invalid_exits_1(tmp_path: Path) -> None:
    code, out = _run(tmp_path, apm_called=False)
    assert code == 1
    assert (out / "run.json").exists()  # 무효여도 관문은 통과 — 원인 판독용으로 남긴다
    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["run_invalid"] is True


def test_gate_failure_writes_nothing_but_leak_check(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": "zz-run-user-9"})
    # 자유 문장 칸(response 등)의 사용자 정보는 치환기가 표지로 바꾼다(plans/164 W1) — 그 밖
    # 문장 칸(`degraded_reason`)에 두어 관문이 막는지 본다
    base = _fake_driver({})

    def driver_run(*a: Any, **k: Any) -> Any:
        records, meta = base(*a, **k)
        records[0]["observed"]["degraded_reason"] = "조회자 zz-run-user-9"
        return records, meta

    out = tmp_path / "out"
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(out)])
    code = cli.cmd_run(args, driver_run=driver_run)
    assert code == 1
    assert {p.name for p in out.iterdir()} == {"leak_check.json"} | RUN_DIR_FILES
    check = json.loads((out / "leak_check.json").read_text(encoding="utf-8"))
    assert check["passed"] is False and {v["rule"] for v in check["violations"]} == {"user_info"}


def test_missing_evidence_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hy, "check_evidence", lambda records: [(SCENARIO, 0, "window.sent_at")])
    code, _ = _run(tmp_path)
    assert code == 1


@pytest.mark.parametrize("argv, why", [
    (["--env", "e2", "--placeholders", "x.yaml", "--view-server-version", "5.6.4.1",
      "--llm", "mlx"], "e2+mlx"),
    (["--env", "e2", "--view-server-version", "5.6.4.1"], "e2 자리표 없음"),
    (["--env", "e2", "--placeholders", "x.yaml", "--view-server-version", "5.6.2.1"], "뷰 서버"),
])
def test_run_refusals_exit_2(argv: list[str], why: str) -> None:
    args = cli.build_parser().parse_args(["--run", *argv])
    called: list[Any] = []
    assert cli.cmd_run(args, driver_run=lambda *a, **k: called.append(1)) == 2, why
    assert called == []


def test_skip_heavy_drives_light_only_and_marks_heavy_skipped(tmp_path: Path) -> None:
    """`--skip-heavy`(§7 첫 회차 선택지) — 무거운 등급은 드라이버에 넘기지 않고 `heavy_off`로
    남긴다."""
    heavy = "APM-HEALTH-untargeted"
    seen: dict[str, Any] = {}
    code, out = _run(tmp_path, "--only", SCENARIO, heavy, "--skip-heavy", seen=seen)
    assert code == 0 and seen["picked"] == [SCENARIO]
    trace = [json.loads(line) for line in
             (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    skipped = {r["scenario_id"]: r["verdict"]["skipped"] for r in trace
               if r["verdict"].get("skipped")}
    assert skipped == {heavy: cli.HEAVY_OFF}
    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["skip_heavy"] is True
    # 무거운 등급만 고르면 돌 것이 없다
    assert _run(tmp_path / "h", "--only", heavy, "--skip-heavy")[0] == 1


def test_term_policy_path_and_unreadable_files_exit_2(tmp_path: Path) -> None:
    """`--term-policy` 로컬 사본을 쓰고, 자리표·허용 목록을 못 읽으면 드라이버 전에 exit 2."""
    policy = tmp_path / "terms.yaml"
    policy.write_text("terms: [WAS]\n", encoding="utf-8")
    assert _run(tmp_path / "ok", "--term-policy", str(policy))[0] == 0
    called: list[Any] = []
    for extra in (["--term-policy", str(tmp_path / "없음.yaml")],
                  ["--placeholders", str(tmp_path / "없음.yaml")]):
        args = cli.build_parser().parse_args(["--run", "--only", SCENARIO, *extra])
        assert cli.cmd_run(args, driver_run=lambda *a, **k: called.append(1)) == 2, extra
    policy.write_text("terms: ['123']\n", encoding="utf-8")  # 숫자만 = 형식 위반
    args = cli.build_parser().parse_args(["--run", "--only", SCENARIO, "--term-policy",
                                          str(policy)])
    assert cli.cmd_run(args, driver_run=lambda *a, **k: called.append(1)) == 2
    assert called == []


def _assert_aborted_run_dir(out: Path) -> None:
    """드라이버 전 중단 run — 산출물 없이 run 메타(aborted)·실행 로그·매니페스트만 남는다."""
    assert {p.name for p in out.iterdir()} == {"run_meta.json", "run.log", "manifest.sha256",
                                               "scenarios.yaml"}
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "aborted" and meta["exit_code"] == 2


def test_llm_mlx_blocked_exits_2(tmp_path: Path) -> None:
    """`--llm mlx` 사전 점검 실패(드라이버 `LLMBlockedError`)는 exit 2 · 산출물 없음."""
    seen: dict[str, Any] = {}

    def blocked(*a: Any, **kw: Any) -> Any:
        seen.update(kw)
        raise dv.LLMBlockedError("MLX 점검 중단: reachable=False")

    args = cli.build_parser().parse_args(
        ["--run", "--llm", "mlx", "--mlx-url", "http://127.0.0.1:9/v1", "--only", SCENARIO,
         "--out", str(tmp_path / "out")])
    assert cli.cmd_run(args, driver_run=blocked) == 2
    assert seen["llm"] == "mlx" and seen["mlx_url"] == "http://127.0.0.1:9/v1"
    _assert_aborted_run_dir(tmp_path / "out")


def test_llm_mlx_real_driver_blocks_before_start(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """실 드라이버 경로 — 원격 MLX 주소면 아무것도 띄우지 않고 exit 2."""
    monkeypatch.setattr(dv.gw, "start_gateway", lambda *a, **k: pytest.fail("기동하면 안 된다"))
    args = cli.build_parser().parse_args(
        ["--run", "--llm", "mlx", "--mlx-url", "http://10.1.2.3:8080/v1", "--only", SCENARIO,
         "--out", str(tmp_path / "out")])
    assert cli.cmd_run(args) == 2


def test_e2_billing_plane_real_driver_exits_2(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """E2 — 본체 평면이 과금(gemini)이면 아무것도 띄우지 않고 exit 2(우회 옵션 없음)."""
    import src.config as cfg_mod

    plane = type("Plane", (), {"provider": "gemini"})()
    monkeypatch.setattr(cfg_mod, "load_config",
                        lambda *a, **k: type("Cfg", (), {"llm": plane, "orchestrator": plane})())
    monkeypatch.setattr(dv.gw, "start_gateway", lambda *a, **k: pytest.fail("기동하면 안 된다"))
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e2", "--view-server-version", "5.6.2.18", "--placeholders",
         str(cli.PLACEHOLDERS_E1), "--term-policy", str(hy.TERM_POLICY_PATH), "--only", SCENARIO,
         "--out", str(tmp_path / "out")])
    assert cli.cmd_run(args) == 2
    _assert_aborted_run_dir(tmp_path / "out")


def test_fewshot_check_modes(monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--fewshot-check"]) == 0
    assert "다음에 돌릴 명령" in capsys.readouterr().out
    monkeypatch.setattr(lp, "fewshot_check", lambda *a, **k: [
        {"kind": "size", "message": "APM 절 99,999자 > 상한"}])
    assert cli.main(["--fewshot-check"]) == 1
    out = capsys.readouterr().out
    assert "[차단] APM 절 99,999자" in out and "다음에 돌릴 명령" in out


def test_dry_run_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "결과       : 통과" in capsys.readouterr().out


def _two_runs(tmp_path: Path) -> tuple[Path, Path]:
    code_a, out_a = _run(tmp_path / "a")
    code_b, out_b = _run(tmp_path / "b")
    assert code_a == code_b == 0
    return out_a, out_b


def test_compare_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_a, out_b = _two_runs(tmp_path)
    capsys.readouterr()
    dest = tmp_path / "cmp"
    assert cli.main(["--compare", str(out_a), str(out_b), "--out", str(dest)]) == 0
    text = (dest / "compare.md").read_text(encoding="utf-8")
    assert "# few-shot 회차 비교" in text and lp.DIRECTIONAL_LABEL in text
    assert cli.main(["--compare", str(out_a), str(tmp_path / "없음")]) == 1


def test_replay_blocked_and_injected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_a, _ = _two_runs(tmp_path)

    def blocked() -> Any:
        raise lp.ReplayBlockedError("과금 평면이 있어 재현하지 않는다: ['gemini']")

    args = cli.build_parser().parse_args(["--replay", str(out_a)])
    assert cli.cmd_replay(args, llm_factory=blocked) == 2
    assert cli.cmd_replay(cli.build_parser().parse_args(["--replay", str(tmp_path)]),
                          llm_factory=blocked) == 1  # run 산출물 아님

    class FakeLLM:
        """분해 1회 — 기대 보기 그대로."""

        async def ainvoke(self, messages: Any, **kw: Any) -> Any:
            from types import SimpleNamespace

            body = {"tasks": [{"task_id": "t1", "agent": "apm_query", "sub_query": "q",
                               "views": ["apm.instances"], "view_args": {},
                               "depends_on": [], "input_from": [], "order": 1}]}
            return SimpleNamespace(content=json.dumps(body, ensure_ascii=False))

    dest = tmp_path / "rp"
    args = cli.build_parser().parse_args(
        ["--replay", str(out_a), "--turns", f"{SCENARIO}#0", "--out", str(dest)])
    assert cli.cmd_replay(args, llm_factory=FakeLLM) == 0
    text = (dest / "replay.md").read_text(encoding="utf-8")
    assert lp.DIRECTIONAL_LABEL in text and f"## {SCENARIO}#0" in text


def test_replay_warns_on_holdout_turns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    """`--turns`에 holdout 턴이 섞이면 경고하고 실행은 한다(권고 ③)."""
    (tmp_path / "trace.jsonl").write_text("", encoding="utf-8")
    records = [{"scenario_id": "H", "turn": 0, "split": "holdout"},
               {"scenario_id": "D", "turn": 0, "split": "dev"}]
    monkeypatch.setattr(lp, "load_run", lambda _p: {"records": records})
    seen: dict[str, Any] = {}
    monkeypatch.setattr(lp, "replay", lambda _r, **kw: seen.update(kw) or [])
    args = cli.build_parser().parse_args(["--replay", str(tmp_path), "--turns", "H#0", "D#0"])
    assert cli.cmd_replay(args, llm_factory=lambda: object()) == 0
    assert "[경고] holdout 턴 지정 1개" in capsys.readouterr().out
    assert seen["turn_ids"] == ["H#0", "D#0"]
    args = cli.build_parser().parse_args(["--replay", str(tmp_path), "--turns", "D#0"])
    assert cli.cmd_replay(args, llm_factory=lambda: object()) == 0
    assert "[경고]" not in capsys.readouterr().out


def test_main_routes_every_mode() -> None:
    assert set(cli.MODES) == {"dry_run", "coverage", "run", "fewshot_check", "compare", "replay",
                              "audit_digest", "regate", "purge", "status", "init_local"}


def test_login_password_env_then_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--user` 비밀번호는 환경변수로만(명령줄 금지) · 없고 비대화형이면 막는다 · 계정 없으면 무관."""
    assert cli.login_password(None) == (None, None)
    monkeypatch.setenv(cli.PASSWORD_ENV, "pw-1")
    assert cli.login_password("ops01") == ("pw-1", None)
    assert cli.PASSWORD_ENV not in cli.os.environ  # 자식 프로세스로 새지 않게 읽은 뒤 지운다
    monkeypatch.setattr(cli.sys, "stdin", None)
    password, blocked = cli.login_password("ops01")
    assert password is None and blocked is not None and cli.PASSWORD_ENV in blocked


def test_run_user_values_uses_login_id() -> None:
    assert cli.run_user_values("ops01")["login_id"] == "ops01"
    assert cli.run_user_values()["login_id"]  # 내장 테스트 계정
