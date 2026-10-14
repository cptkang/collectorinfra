"""plans/164 W2b · W2 (d) 키트 · G-4 (a) · W4 — verifier 독립 검증.

통과 테스트는 경계 확인(경로 주입 · 최근 run 선택 · UTC 오프셋 창 · 로그 원문 미출력 · var 심볼릭
링크 · report.md 값 미출력)이고, 결함은 `xfail(strict=True, reason="V-n: …")`로 고정한다.

LLM·네트워크·제니퍼 0 — 드라이버는 `test_apm_bench_cli._fake_driver` 대역이다. 홈 · 결과 루트 ·
감사 로그 위치 · var 위치는 tmp 로 돌린다.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.apm_bench import SCENARIOS_PATH, kit
from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import audit_digest as ad
from scripts.apm_bench import rundir as rd
from scripts.apm_bench import safe_summary as ss
from scripts.apm_bench import scenarios as sc
from tests.test_scripts.test_apm_bench_cli import SCENARIO, _fake_driver

HOST_TURN = "APM-INST-host"
INST_TURN = "APM-HEALTH-inst"
ONLY = ["APM-INST-list", "APM-HEALTH-inst"]
CANARY = "zqverify-canary-host91"
V1 = ("V-1: 인벤토리 도구 일부 실패(names 누락)도 「없음」으로 "
      "판정 → 그 턴들이 정확도 분모에서 빠진다 "
      "(G-4 (a) 전제 = 완전한 인벤토리)")
V2 = ("V-2: --audit-digest 에 기본 경로와 같은 --scenarios 를 "
      "명시해도 run 사본이 덮는다 "
      "(명시 인자 우선 위반 · 기본값 동일 비교)")
V3 = ("V-3: pid_alive 가 EPERM(다른 계정 프로세스)을 살아 있음으로 "
      "본다 — 낡은 run.pid 가 재사용 PID 를 가리키면 "
      "--status 가 「실행 중」")
V4 = ("V-4: --since > --until 이면 날짜 목록이 비어 "
      "「감사 로그가 없다」로 오안내한다(창 역전 검사 없음)")
V5 = ("V-5: 살아 있는 run.pid 가 있는 run 디렉터리 "
      "(--out 재사용 · 같은 초 run id)를 거부하지 않고 덮어쓴다")
V6 = ("V-6: run id 모드 --audit-digest 마지막 줄이 출력 절대 경로 "
      "(홈 포함)를 찍는다 — 「자동: 출력」 줄은 display() 로 가린다")


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(rd, "default_quarantine_base", lambda: tmp_path / "quarantine")
    monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(ad, "AUDIT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(kit, "VAR_DIR", tmp_path / "var")
    monkeypatch.setattr(kit.os, "geteuid", lambda: 501, raising=False)
    monkeypatch.delenv(cli.PASSWORD_ENV, raising=False)
    return home


@pytest.fixture
def kst(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _row(ts: str, tid: str, profile: str) -> dict[str, Any]:
    return {"timestamp": ts, "event": "host_investigation", "entry_point": "chat",
            "target_count": 1, "outcome": "ok", "user_id": "anonymous", "thread_id": tid,
            "profile": profile, "backend": "apm_gateway", "duration_ms": 1.0, "degraded": []}


def _log(logs: Path, name: str, rows: list[dict[str, Any]]) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _meta_run(results: Path, run_id: str, *, started: str, ended: str | None,
              only: list[str] | None = None) -> Path:
    run_dir = results / run_id
    run_dir.mkdir(parents=True)
    shutil.copy(SCENARIOS_PATH, run_dir / rd.SCENARIO_COPY)
    ss.write_run_meta(run_dir, {
        "run_id": run_id, "env": "e2", "llm": "body",
        "status": "done" if ended else "running", "exit_code": 0 if ended else None,
        "started_at": started, "ended_at": ended,
        "scenarios": {"source": "default", "copied": True, "copy": rd.SCENARIO_COPY},
        "flags": {"skip_heavy": True, "only": only or ONLY, "probe": True},
        "bench_user": "anonymous",
    }, scenario_ids=[s.id for s in sc.load_scenarios(SCENARIOS_PATH)])
    return run_dir


def _placeholders(tmp_path: Path, **values: str) -> Path:
    data = yaml.safe_load(cli.PLACEHOLDERS_E1.read_text(encoding="utf-8"))
    data["values"].update(values)
    path = tmp_path / "ph.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _kit_driver(inventory: Callable[[dict[str, str]], list[str]],
                inv_errors: list[str] | None = None) -> Callable[..., Any]:
    base = _fake_driver({})

    def run(picked: Any, **kw: Any) -> Any:
        kw["kit_check"](inventory(kw["placeholders"].values), {})
        records, meta = base(picked, **kw)
        if inv_errors is not None:
            meta["inventory"] = {"calls": 2, "names": 1, "errors": list(inv_errors)}
        return records, meta

    return run


def _run(tmp_path: Path, driver_run: Callable[..., Any], *extra: str,
         only: tuple[str, ...] = (HOST_TURN,)) -> tuple[int, Path]:
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", *only, "--out", str(out), *extra])
    return cli.cmd_run(args, driver_run=driver_run), out


# --- 통과: 경계 확인 ------------------------------------------------------------------------


def test_audit_digest_traversal_arg_never_enters_run_id_mode(tmp_path: Path) -> None:
    """run id 정규식 밖 인자(`../x` · 절대 경로)는 경로 목록 모드 —
    창·--out 없으면 exit 2 · 쓰기 0."""
    for arg in ("../../etc", str(tmp_path / "home"), "20261013-090000-e2-aaaaaaa/../x"):
        assert cli.main(["--audit-digest", arg]) == 2
    assert not (tmp_path / "results").exists()


def test_latest_run_skips_symlink_and_audit_dirs(tmp_path: Path) -> None:
    results = tmp_path / "results"
    old = _meta_run(results, "20261012-100000-e2-aaaaaaa", started="2026-10-12T10:00:00+09:00",
                    ended="2026-10-12T10:30:00+09:00")
    outside = _meta_run(tmp_path / "elsewhere", "20261020-100000-e2-bbbbbbb",
                        started="2026-10-20T10:00:00+09:00", ended="2026-10-20T10:30:00+09:00")
    (results / "20261020-100000-e2-bbbbbbb").symlink_to(outside, target_is_directory=True)
    (results / "20261013_1827_audit").mkdir()
    assert rd.latest_run(results) == old


@pytest.mark.usefixtures("kst")
def test_audit_digest_utc_offset_meta_uses_local_dates(tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """메타가 UTC 오프셋이어도 감사 로그 파일 날짜는 실행 머신 로컬(KST) 날짜로 고른다."""
    results, logs = tmp_path / "results", tmp_path / "logs"
    run_id = "20261014-003000-e2-aaaaaaa"
    run_dir = _meta_run(results, run_id, started="2026-10-13T15:30:00+00:00",
                        ended="2026-10-13T15:50:00+00:00")
    _log(logs, "audit-2026-10-14.jsonl", [_row("2026-10-13T15:31:00+00:00", "t1",
                                               "apm.instances"),
                                          _row("2026-10-13T15:32:00+00:00", "t2",
                                               "apm.app_health")])
    _log(logs, "audit-2026-10-13.jsonl", [_row("2026-10-13T10:00:00+00:00", "tx",
                                               "apm.instances")])
    assert cli.main(["--audit-digest", run_id]) == 0
    printed = capsys.readouterr().out
    assert "audit-2026-10-14.jsonl" in printed and "audit-2026-10-13.jsonl" not in printed
    digest = json.loads((run_dir / "audit_digest.json").read_text(encoding="utf-8"))
    assert digest["input"]["files"] == 1 and digest["mapping"]["exact"] == 2


@pytest.mark.usefixtures("kst")
def test_audit_digest_running_run_window_ends_now(tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results"
    run_id = "20261013-090000-e2-aaaaaaa"
    run_dir = _meta_run(results, run_id, started="2026-10-13T09:00:00+09:00", ended=None)
    (run_dir / rd.PID_FILE).write_text(f"{os.getpid()}\n", encoding="utf-8")
    _log(tmp_path / "logs", "audit-2026-10-13.jsonl",
         [_row("2026-10-13T00:10:00+00:00", "t1", "apm.instances")])
    code = cli.main(["--audit-digest", run_id])
    assert "run 이 아직 돈다" in capsys.readouterr().out
    assert code in (0, 2)  # 지금 날짜 로그가 없으면 2 — 창 끝이 「지금」인지만 본다


@pytest.mark.usefixtures("kst")
def test_elapsed_log_content_not_echoed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """「소요 N초」 실행 로그의 다른 원문(호스트 이름)은 콘솔·진단표에 나오지 않는다."""
    monkeypatch.chdir(tmp_path)
    legacy = "20261013_1827"
    (tmp_path / f"run_{legacy}.log").write_text(
        f"[중단] {CANARY} 연결 실패 · 소요 600초\n", encoding="utf-8")
    _log(tmp_path / "logs", "audit-2026-10-13.jsonl",
         [_row("2026-10-13T09:30:00+00:00", "t1", "apm.instances")])
    assert cli.main(["--audit-digest", legacy, "--only", *ONLY]) == 0
    printed = capsys.readouterr().out
    out = tmp_path / "results" / f"{legacy}_audit" / "audit_digest.json"
    assert CANARY not in printed and CANARY not in out.read_text(encoding="utf-8")


def test_var_snapshot_does_not_follow_symlinked_dir(tmp_path: Path) -> None:
    var = tmp_path / "var"
    var.mkdir()
    target = tmp_path / "big"
    target.mkdir()
    (target / "a").write_text("x", encoding="utf-8")
    (var / "link").symlink_to(target, target_is_directory=True)
    before = kit.var_snapshot(var)
    assert before is not None and set(before) == {"link"}
    # 링크 대상 안 변화는 이 run 의 var 쓰기가 아니다
    (target / "b").write_text("y", encoding="utf-8")
    assert kit.var_changes(before, kit.var_snapshot(var)) == {
        "preexisting": True, "created": False, "new": 0, "changed": 0}


def test_kit_values_absent_from_report_and_leak_check(tmp_path: Path) -> None:
    ph = _placeholders(tmp_path, host_1=CANARY)
    code, out = _run(tmp_path, _kit_driver(lambda v: ["zz-other"]), "--placeholders", str(ph),
                     only=(HOST_TURN, INST_TURN))
    assert code == 0
    for name in ("report.md", "leak_check.json", "coverage.md", rd.MANIFEST_FILE):
        if (out / name).is_file():
            assert CANARY not in (out / name).read_text(encoding="utf-8"), name


def test_e1_default_flags_and_explicit_out_unchanged(tmp_path: Path) -> None:
    """하위 호환 — E1 기본은 skip-heavy·probe 끔 · 자동값 출처 기록 · --out 그대로."""
    seen: dict[str, Any] = {}
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(["--run", "--only", SCENARIO, "--out", str(out)])
    assert cli.cmd_run(args, driver_run=_fake_driver(seen)) == 0
    assert seen["probe"] is False
    flags = json.loads((out / ss.RUN_META_FILE).read_text(encoding="utf-8"))["flags"]
    assert flags["skip_heavy"] is False and flags["probe"] is False
    assert flags["placeholders"] == "e1_default" and flags["term_policy"] == "default"


# --- 결함 ------------------------------------------------------------------------------------


def test_v1_inventory_failure_does_not_shrink_denominator(tmp_path: Path) -> None:
    """`apm_instance_map` 실패로 인스턴스·호스트 이름이 비면 키트는 host_1·inst_1 을 「없음」으로
    보고 두 턴을 판정 불가로 뺀다 — 실패 원인이 게이트웨이인데 분모가 줄어 정확도가 부푼다."""
    ph = _placeholders(tmp_path)
    driver_run = _kit_driver(lambda v: [v["biz_1"], v["svc_1"]],
                             inv_errors=["apm_instance_map:timeout"])
    code, out = _run(tmp_path, driver_run, "--placeholders", str(ph),
                     only=(HOST_TURN, INST_TURN))
    safe = json.loads((out / ss.SAFE_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert code != 0 or safe["summary"]["counts"]["unjudged"] == 0


@pytest.mark.usefixtures("kst")
def test_v2_explicit_default_scenarios_not_overridden(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "20261013-090000-e2-aaaaaaa"
    _meta_run(tmp_path / "results", run_id, started="2026-10-13T09:00:00+09:00",
              ended="2026-10-13T09:30:00+09:00")
    used: list[str] = []

    def spy(args: Any) -> None:
        used.append(str(args.scenarios))
        return None

    monkeypatch.setattr(cli, "_load", spy)
    cli.main(["--audit-digest", run_id, "--scenarios", str(SCENARIOS_PATH)])
    assert used == [str(SCENARIOS_PATH)]


def test_v3_status_other_account_pid_not_running(tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    run_id = "20261013-090000-e2-aaaaaaa"
    run_dir = _meta_run(tmp_path / "results", run_id, started="2026-10-13T09:00:00+09:00",
                        ended=None)
    (run_dir / rd.PID_FILE).write_text("1\n", encoding="utf-8")  # launchd/init — 다른 계정
    assert cli.main(["--status", run_id]) == 0
    assert "실행 중(PID 1)" not in capsys.readouterr().out


@pytest.mark.usefixtures("kst")
def test_v4_reversed_window_reported_as_such(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    run_id = "20261013-090000-e2-aaaaaaa"
    _meta_run(tmp_path / "results", run_id, started="2026-10-13T09:00:00+09:00",
              ended="2026-10-13T09:30:00+09:00")
    _log(tmp_path / "logs", "audit-2026-10-13.jsonl",
         [_row("2026-10-13T00:10:00+00:00", "t1", "apm.instances")])
    assert cli.main(["--audit-digest", run_id, "--since", "2026-10-14T09:00:00+09:00",
                     "--until", "2026-10-13T09:00:00+09:00"]) == 2
    assert "감사 로그가 없다" not in capsys.readouterr().out


def test_v5_live_run_dir_reuse_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / rd.PID_FILE).write_text(f"{os.getpid()}\n", encoding="utf-8")
    args = cli.build_parser().parse_args(["--run", "--only", SCENARIO, "--out", str(out)])
    assert cli.cmd_run(args, driver_run=_fake_driver({})) == 2


@pytest.mark.usefixtures("kst")
def test_v6_audit_digest_console_hides_absolute_path(tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    run_id = "20261013-090000-e2-aaaaaaa"
    _meta_run(tmp_path / "results", run_id, started="2026-10-13T09:00:00+09:00",
              ended="2026-10-13T09:30:00+09:00")
    _log(tmp_path / "logs", "audit-2026-10-13.jsonl",
         [_row("2026-10-13T00:10:00+00:00", "t1", "apm.instances")])
    assert cli.main(["--audit-digest", run_id]) == 0
    assert str(tmp_path) not in capsys.readouterr().out
