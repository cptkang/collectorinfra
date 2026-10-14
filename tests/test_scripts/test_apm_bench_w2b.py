"""plans/164 W2b — 실행 명령 간소화 · run id 기반 자동화.

E2 로컬 기본 파일 자동 탐색 · E2 기본 `--skip-heavy`·`--probe` · `--smoke` · run 메타 자동 채움 칸 ·
`--audit-digest [<run_id>]`(창·로그·시나리오·옵션 자동 · 메타 없는 run 추정) · `--status` · `--bg` ·
`--compare`·`--replay`·`--regate` run id 해석.

LLM·네트워크·제니퍼 0 — 드라이버는 `test_apm_bench_cli._fake_driver` 대역 · 분리 실행은 `popen`
대역이다. 홈(`HOME`)·결과 루트·감사 로그 위치는 tmp 로 돌린다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import SCENARIOS_PATH
from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import audit_digest as ad
from scripts.apm_bench import hygiene as hy
from scripts.apm_bench import loop as lp
from scripts.apm_bench import rundir as rd
from scripts.apm_bench import safe_summary as ss
from scripts.apm_bench import scenarios as sc
from tests.test_scripts.test_apm_bench_cli import SCENARIO, _fake_driver

LOCAL = "apm_bench_local"
VIEW = "5.6.5.11"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """홈 · 격리 기준 · 결과 루트 · 감사 로그 위치를 tmp 로."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(rd, "default_quarantine_base", lambda: tmp_path / "quarantine")
    monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(ad, "AUDIT_LOG_DIR", tmp_path / "logs")
    monkeypatch.delenv(cli.PASSWORD_ENV, raising=False)
    return home


@pytest.fixture
def kst(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """실행 머신 로컬 시간대 = KST(감사 로그 파일 이름 날짜 계산을 고정)."""
    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _local_files(home: Path, *, view: str | None = VIEW, extra: str = "") -> Path:
    local = home / LOCAL
    local.mkdir(exist_ok=True)
    shutil.copy(cli.PLACEHOLDERS_E1, local / cli.LOCAL_PLACEHOLDERS)
    shutil.copy(hy.TERM_POLICY_PATH, local / cli.LOCAL_TERM_POLICY)
    if view is not None:
        (local / cli.LOCAL_BENCH).write_text(f"view_server_version: {view}\n{extra}",
                                             encoding="utf-8")
    return local


def _run(argv: list[str], seen: dict[str, Any] | None = None) -> int:
    args = cli.build_parser().parse_args(argv)
    return cli.cmd_run(args, driver_run=_fake_driver({} if seen is None else seen))


def _meta(out: Path) -> dict[str, Any]:
    return json.loads((out / ss.RUN_META_FILE).read_text(encoding="utf-8"))


# ① E2 로컬 기본 파일 자동 탐색 ------------------------------------------------------------


def test_e2_local_defaults_autodiscovered(tmp_path: Path, _isolate: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    _local_files(_isolate, extra="operator_note: x\n")
    seen: dict[str, Any] = {}
    out = tmp_path / "out"
    assert _run(["--run", "--env", "e2", "--only", SCENARIO, "--out", str(out)], seen) == 0
    assert seen["env"] == "e2" and seen["view_server_version"] == VIEW and seen["probe"]
    printed = capsys.readouterr().out
    assert f"자동: 자리표 — ~/{LOCAL}/{cli.LOCAL_PLACEHOLDERS}" in printed
    assert f"자동: 일반 단어 허용 목록 — ~/{LOCAL}/{cli.LOCAL_TERM_POLICY}" in printed
    assert f"자동: 뷰 서버 버전 — ~/{LOCAL}/{cli.LOCAL_BENCH}" in printed
    assert "모르는 키 1개 무시" in printed and "operator_note" not in printed
    assert str(_isolate) not in printed  # 홈 경로 미출력
    flags = _meta(out)["flags"]
    assert flags["placeholders"] == flags["term_policy"] == "local_default"
    assert flags["view_server_version"] == VIEW
    assert flags["view_server_version_source"] == "bench_local"
    assert flags["skip_heavy"] is True and flags["probe"] is True


def test_e2_missing_local_files_exit_2_with_guidance(tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """로컬 파일이 없으면 둘 위치를 안내하고 exit 2 — 드라이버(제니퍼 접속) 전 · run 디렉터리
    없음."""
    called: list[Any] = []
    args = cli.build_parser().parse_args(["--run", "--env", "e2"])
    assert cli.cmd_run(args, driver_run=lambda *a, **k: called.append(1)) == 2
    printed = capsys.readouterr().out
    for name in (cli.LOCAL_PLACEHOLDERS, cli.LOCAL_TERM_POLICY, cli.LOCAL_BENCH):
        assert f"~/{LOCAL}/{name}" in printed
    assert "view_server_version" in printed and str(tmp_path) not in printed
    assert called == [] and not (tmp_path / "results").exists()
    # 명시 인자로 채운 칸은 안내하지 않는다
    args = cli.build_parser().parse_args(["--run", "--env", "e2", "--view-server-version", VIEW])
    assert cli.cmd_run(args, driver_run=lambda *a, **k: called.append(1)) == 2
    printed = capsys.readouterr().out
    assert cli.LOCAL_BENCH not in printed and cli.LOCAL_PLACEHOLDERS in printed


def test_bench_local_without_version_or_broken_exit_2(_isolate: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    local = _local_files(_isolate, view=None)
    (local / cli.LOCAL_BENCH).write_text("other: 1\n", encoding="utf-8")
    argv = ["--run", "--env", "e2", "--only", SCENARIO]
    assert _run(argv) == 2
    assert f"~/{LOCAL}/{cli.LOCAL_BENCH} — `view_server_version" in capsys.readouterr().out
    (local / cli.LOCAL_BENCH).write_text("- 5.6.5.11\n", encoding="utf-8")  # 매핑 아님
    assert _run(argv) == 2
    (local / cli.LOCAL_BENCH).write_text("view_server_version: 5.6.1.0\n", encoding="utf-8")
    assert _run(argv) == 2  # 최소 버전 미만(D-323 ①)은 자동값이어도 막는다


def test_e2_defaults_on_and_no_flags_turn_off(tmp_path: Path, _isolate: Path) -> None:
    _local_files(_isolate)
    seen: dict[str, Any] = {}
    out = tmp_path / "off"
    assert _run(["--run", "--env", "e2", "--only", SCENARIO, "--no-skip-heavy", "--no-probe",
                 "--out", str(out)], seen) == 0
    assert seen["probe"] is False
    flags = _meta(out)["flags"]
    assert flags["skip_heavy"] is False and flags["probe"] is False
    # E1 기본은 종전 그대로(둘 다 끔 · 저장소 자리표)
    seen = {}
    out = tmp_path / "e1"
    assert _run(["--run", "--only", SCENARIO, "--out", str(out)], seen) == 0
    meta = _meta(out)
    assert seen["probe"] is False and meta["env"] == "e1"
    assert meta["flags"]["skip_heavy"] is False and meta["flags"]["placeholders"] == "e1_default"
    assert meta["flags"]["view_server_version_source"] is None


def test_explicit_args_win_over_local_defaults(tmp_path: Path, _isolate: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    _local_files(_isolate)
    seen: dict[str, Any] = {}
    out = tmp_path / "out"
    assert _run(["--run", "--env", "e2", "--only", SCENARIO, "--view-server-version", "5.6.3.1",
                 "--placeholders", str(cli.PLACEHOLDERS_E1), "--term-policy",
                 str(hy.TERM_POLICY_PATH), "--out", str(out)], seen) == 0
    assert seen["view_server_version"] == "5.6.3.1"
    printed = capsys.readouterr().out
    assert "자동: 자리표" not in printed and "자동: 뷰 서버 버전" not in printed
    flags = _meta(out)["flags"]
    assert flags["placeholders"] == flags["term_policy"] == "explicit"
    assert flags["view_server_version_source"] == "explicit"


# ② --smoke ------------------------------------------------------------------------------


def test_smoke_ids_exist_in_scenario_file() -> None:
    ids = {s.id for s in sc.load_scenarios(SCENARIOS_PATH)}
    assert len(cli.SMOKE_IDS) == len(set(cli.SMOKE_IDS)) == 10
    assert set(cli.SMOKE_IDS) <= ids


def test_smoke_runs_list_and_conflicts(tmp_path: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    called: list[Any] = []
    args = cli.build_parser().parse_args(["--run", "--smoke", "--only", SCENARIO])
    assert cli.cmd_run(args, driver_run=lambda *a, **k: called.append(1)) == 2 and called == []
    assert cli.main(["--dry-run", "--smoke"]) == 2  # --run 전용
    seen: dict[str, Any] = {}
    assert _run(["--run", "--smoke"], seen) in (0, 1)  # 판정 결과는 대역 관측 모양에 달렸다
    assert set(seen["picked"]) == set(cli.SMOKE_IDS)
    run_dir = rd.latest_run(tmp_path / "results")
    assert run_dir is not None and run_dir.name.endswith("-smoke")
    meta = _meta(run_dir)
    assert ss.RUN_ID_RE.fullmatch(meta["run_id"]) and meta["flags"]["smoke"] is True
    assert set(meta["flags"]["only"]) == set(cli.SMOKE_IDS)
    assert "자동: --only — 스모크 목록 10개" in capsys.readouterr().out


def test_run_id_format() -> None:
    from datetime import datetime

    from scripts.apm_bench.driver import KST

    now = datetime(2026, 10, 14, 9, 30, 5, tzinfo=KST)
    assert cli.make_run_id("e2", "a" * 40, now) == "20261014-093005-e2-aaaaaaa"
    smoke = cli.make_run_id("e1", "unknown", now, smoke=True)
    assert smoke == "20261014-093005-e1-unknown-smoke"
    assert ss.RUN_ID_RE.fullmatch(smoke) and ss.RUN_ID_RE.fullmatch("20261014-093005-e2-unknown")
    assert not ss.RUN_ID_RE.fullmatch("20261014-093005-e2-aaaaaaa-full")


# ③ run 메타 자동 채움 칸 ------------------------------------------------------------------


def test_run_meta_records_autofill_fields(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert _run(["--run", "--only", SCENARIO, "--out", str(out)]) == 0
    meta = _meta(out)
    assert meta["scenarios"]["copy"] == rd.SCENARIO_COPY and (out / rd.SCENARIO_COPY).is_file()
    assert meta["bench_user"] == "anonymous" and meta["flags"]["smoke"] is False
    assert ss.ISO_RE.fullmatch(meta["started_at"]) and ss.ISO_RE.fullmatch(meta["ended_at"])
    assert ss.SHA_RE.fullmatch(meta["sha"]) and meta["llm"] == "fake"


def test_run_meta_doc_new_fields_are_closed_vocab() -> None:
    doc = ss.run_meta_doc({
        "bench_user": "op01", "scenarios": {"copy": "/home/op01/s.yaml"},
        "flags": {"placeholders": "/home/op01/p.yaml", "term_policy": "local_default",
                  "view_server_version_source": "op01", "smoke": "yes"},
    }, scenario_ids=())
    assert doc["bench_user"] is None and doc["scenarios"]["copy"] is None
    assert doc["flags"]["placeholders"] is None and doc["flags"]["term_policy"] == "local_default"
    assert doc["flags"]["view_server_version_source"] is None and doc["flags"]["smoke"] is False


# ④ --audit-digest [<run_id>] -------------------------------------------------------------


def _row(ts: str, tid: str, profile: str, user: str = "anonymous") -> dict[str, Any]:
    return {"timestamp": ts, "event": "host_investigation", "entry_point": "chat",
            "target_count": 1, "outcome": "ok", "user_id": user, "thread_id": tid,
            "profile": profile, "backend": "apm_gateway", "duration_ms": 1.0, "degraded": []}


def _log(logs: Path, name: str, rows: list[dict[str, Any]]) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _meta_run(results: Path, run_id: str, *, started: str, ended: str | None,
              only: list[str] | None = None, env: str = "e2", skip_heavy: bool = True,
              bench_user: str = "anonymous", copy: bool = True) -> Path:
    run_dir = results / run_id
    run_dir.mkdir(parents=True)
    if copy:
        shutil.copy(SCENARIOS_PATH, run_dir / rd.SCENARIO_COPY)
    ss.write_run_meta(run_dir, {
        "run_id": run_id, "env": env, "llm": "body", "status": "done" if ended else "running",
        "exit_code": 0 if ended else None, "started_at": started, "ended_at": ended,
        "scenarios": {"source": "default", "copied": copy,
                      "copy": rd.SCENARIO_COPY if copy else None},
        "flags": {"skip_heavy": skip_heavy, "only": only, "probe": True},
        "bench_user": bench_user,
    }, scenario_ids=[s.id for s in sc.load_scenarios(SCENARIOS_PATH)])
    return run_dir


ONLY = ["APM-INST-list", "APM-HEALTH-inst"]


@pytest.mark.usefixtures("kst")
def test_audit_digest_latest_run_window_midnight_and_rotation(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """인자 없음 → 최근 run(메타 있음 우선) · 창 = 메타 시작·끝 ± 5분 · 자정 넘김 두 날짜 ·
    로테이션 파일 포함 · 시나리오 사본 · --env·--skip-heavy·--only 자동 · 출력 = run 디렉터리."""
    results, logs = tmp_path / "results", tmp_path / "logs"
    _meta_run(results, "20261012-100000-e2-aaaaaaa", started="2026-10-12T10:00:00+09:00",
              ended="2026-10-12T10:30:00+09:00", only=ONLY)
    run_dir = _meta_run(results, "20261013-235000-e2-bbbbbbb",
                        started="2026-10-13T23:50:00+09:00", ended="2026-10-14T00:20:00+09:00",
                        only=ONLY)
    (results / "20261020-000000-e2-ccccccc").mkdir()  # 메타 없는 더 최근 이름 — 메타 있는 쪽 우선
    _log(logs, "audit-2026-10-13.1.jsonl", [_row("2026-10-13T14:52:00+00:00", "t1",
                                                 "apm.instances")])
    _log(logs, "audit-2026-10-13.jsonl", [_row("2026-10-13T14:40:00+00:00", "t0",
                                               "apm.instances")])  # 23:40 KST — 창 밖
    _log(logs, "audit-2026-10-14.jsonl", [_row("2026-10-13T15:05:00+00:00", "t2",
                                               "apm.app_health")])
    _log(logs, "audit-2026-10-12.jsonl", [_row("2026-10-12T01:10:00+00:00", "tx",
                                               "apm.instances")])  # 창 날짜 밖 — 안 읽는다
    assert cli.main(["--audit-digest"]) == 0
    printed = capsys.readouterr().out
    assert "자동: run — 최근 20261013-235000-e2-bbbbbbb" in printed
    assert "2026-10-13T23:45:00+09:00 ~ 2026-10-14T00:25:00+09:00" in printed
    assert "audit-2026-10-13.1.jsonl" in printed and "audit-2026-10-12" not in printed
    digest = json.loads((run_dir / "audit_digest.json").read_text(encoding="utf-8"))
    assert digest["input"]["files"] == 3 and digest["input"]["out_of_window"] == 1
    assert digest["expected"]["env"] == "e2" and digest["expected"]["scenarios"] == 2
    assert digest["expected"]["window_estimated"] is False
    assert digest["mapping"]["exact"] == 2


@pytest.mark.usefixtures("kst")
def test_audit_digest_run_id_explicit_args_win(tmp_path: Path) -> None:
    results, logs = tmp_path / "results", tmp_path / "logs"
    run_id = "20261013-090000-e2-aaaaaaa"
    _meta_run(results, run_id, started="2026-10-13T09:00:00+09:00",
              ended="2026-10-13T09:30:00+09:00", only=ONLY)
    _log(logs, "audit-2026-10-13.jsonl", [_row("2026-10-13T00:10:00+00:00", "t1",
                                               "apm.instances"),
                                          _row("2026-10-13T00:20:00+00:00", "t2",
                                               "apm.app_health")])
    out = tmp_path / "o"
    assert cli.main(["--audit-digest", run_id, "--until", "2026-10-13T09:15:00+09:00",
                     "--only", "APM-INST-list", "--out", str(out)]) == 0
    digest = json.loads((out / "audit_digest.json").read_text(encoding="utf-8"))
    assert digest["expected"]["scenarios"] == 1 and digest["input"]["out_of_window"] == 1


@pytest.mark.usefixtures("kst")
def test_audit_digest_meta_edge_cases_exit_2(tmp_path: Path) -> None:
    results, logs = tmp_path / "results", tmp_path / "logs"
    _log(logs, "audit-2026-10-13.jsonl", [_row("2026-10-13T00:10:00+00:00", "t1",
                                               "apm.instances")])
    user_run = "20261013-090000-e2-aaaaaaa"
    _meta_run(results, user_run, started="2026-10-13T09:00:00+09:00",
              ended="2026-10-13T09:30:00+09:00", bench_user="login_user")
    assert cli.main(["--audit-digest", user_run]) == 2  # --user run — 계정을 모른다
    killed = "20261013-091000-e2-bbbbbbb"
    _meta_run(results, killed, started="2026-10-13T09:10:00+09:00", ended=None)
    assert cli.main(["--audit-digest", killed]) == 2  # 끝 시각 없음 · PID 없음
    assert cli.main(["--audit-digest", killed, "--duration", "600"]) == 0
    digest = json.loads((results / killed / "audit_digest.json").read_text(encoding="utf-8"))
    assert digest["expected"]["window_estimated"] is True
    empty = "20261015-090000-e2-ccccccc"
    _meta_run(results, empty, started="2026-10-15T09:00:00+09:00",
              ended="2026-10-15T09:30:00+09:00")
    assert cli.main(["--audit-digest", empty]) == 2  # 그 날짜 감사 로그 없음
    assert not (results / empty / "audit_digest.json").exists()


LEGACY = "20261013_1827"


@pytest.mark.usefixtures("kst")
def test_audit_digest_legacy_run_from_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    """메타 없는 구 방식 run — 실행 로그 「소요 N초」로 끝 추정 · HEAD 시나리오 경고 · exit 0."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / f"run_{LEGACY}.log").write_text(
        "중단 없음 · 소요 12.0초 · 예산 {}\n중단 사유  : 없음 · 소요 8102.7초 · x\n",
        encoding="utf-8")
    _log(tmp_path / "logs", "audit-2026-10-13.jsonl",
         [_row("2026-10-13T10:00:00+00:00", "t1", "apm.instances")])
    assert cli.main(["--audit-digest", LEGACY, "--only", *ONLY]) == 0
    printed = capsys.readouterr().out
    assert f"자동: 소요 — 실행 로그 현재 디렉터리/run_{LEGACY}.log" in printed
    assert "2026-10-13T18:22:00+09:00 ~ 2026-10-13T20:47:02+09:00 (끝 추정)" in printed
    assert "run SHA 미상 — HEAD 시나리오 사용" in printed
    assert "APM-SRC-common → APM-SRC-cm-prd" in printed
    out = tmp_path / "results" / f"{LEGACY}_audit" / "audit_digest.json"
    digest = json.loads(out.read_text(encoding="utf-8"))
    assert digest["expected"]["window_estimated"] is True and digest["expected"]["env"] == "e2"


@pytest.mark.usefixtures("kst")
def test_audit_digest_legacy_duration_home_log_and_none(
        tmp_path: Path, _isolate: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    _log(tmp_path / "logs", "audit-2026-10-13.jsonl",
         [_row("2026-10-13T10:00:00+00:00", "t1", "apm.instances")])
    # 둘 다 없음 → 창을 넓히지 않고 exit 2
    assert cli.main(["--audit-digest", LEGACY]) == 2
    assert "--duration" in capsys.readouterr().out
    assert not (tmp_path / "results").exists()
    # --duration
    assert cli.main(["--audit-digest", LEGACY, "--duration", "600"]) == 0
    capsys.readouterr()
    # ~/apm_bench_out/ 실행 로그
    shutil.rmtree(tmp_path / "results")
    (_isolate / "apm_bench_out").mkdir()
    (_isolate / "apm_bench_out" / f"run_{LEGACY}.log").write_text("소요 60초\n",
                                                                  encoding="utf-8")
    assert cli.main(["--audit-digest", LEGACY]) == 0
    printed = capsys.readouterr().out
    assert f"~/apm_bench_out/run_{LEGACY}.log" in printed and str(_isolate) not in printed


def test_audit_digest_no_runs_exit_2() -> None:
    assert cli.main(["--audit-digest"]) == 2


def test_audit_digest_helpers(tmp_path: Path, kst: None) -> None:
    from datetime import datetime

    since = datetime.fromisoformat("2026-10-13T23:30:00+09:00")
    until = datetime.fromisoformat("2026-10-15T00:10:00+09:00")
    assert [d.isoformat() for d in ad.local_dates(since, until)] == [
        "2026-10-13", "2026-10-14", "2026-10-15"]
    for name in ("audit-2026-10-13.jsonl", "audit-2026-10-13.2.jsonl", "audit-2026-10-16.jsonl",
                 "audit-2026-10-130.jsonl"):
        _log(tmp_path / "logs", name, [])
    assert [p.name for p in ad.audit_log_files(since, until, tmp_path / "logs")] == [
        "audit-2026-10-13.2.jsonl", "audit-2026-10-13.jsonl"]
    assert ad.elapsed_from_log("소요 1.5초\n소요 30초") == 30.0
    assert ad.elapsed_from_log("소요 없음") is None


# ⑤ --compare · --replay · --regate run id 해석 -------------------------------------------


def _two_runs(tmp_path: Path) -> tuple[str, str]:
    ids = ("20261013-090000-e1-aaaaaaa", "20261013-100000-e1-aaaaaaa-smoke")
    for run_id in ids:
        assert _run(["--run", "--only", SCENARIO, "--out",
                     str(tmp_path / "results" / run_id)]) == 0
    return ids


def test_compare_and_replay_accept_run_ids(tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    run_a, run_b = _two_runs(tmp_path)
    capsys.readouterr()
    assert cli.main(["--compare", run_a, run_b]) == 0
    assert "# few-shot 회차 비교" in capsys.readouterr().out
    shutil.copytree(tmp_path / "results" / run_a, tmp_path / "results" / "base_x")
    assert cli.main(["--compare", run_a, "base_x"]) == 0  # results 아래 이름
    assert cli.main(["--compare", run_a, "20261013-110000-e1-aaaaaaa"]) == 1  # 없는 run

    def blocked() -> Any:
        raise lp.ReplayBlockedError("과금 평면")

    assert cli.cmd_replay(cli.build_parser().parse_args(["--replay", run_a]),
                          llm_factory=blocked) == 2  # 산출물을 찾았다 → MLX 점검에서 멈춤
    assert cli.cmd_replay(cli.build_parser().parse_args(
        ["--replay", "20261013-110000-e1-aaaaaaa"]), llm_factory=blocked) == 1


def test_regate_and_resolve_run(tmp_path: Path) -> None:
    run_id = "20261013-090000-e2-aaaaaaa-smoke"
    args = cli.build_parser().parse_args(["--regate", run_id])
    q_dir, out_dir = cli._resolve_regate(run_id, args)  # type: ignore[misc]
    assert q_dir == tmp_path / "quarantine" / run_id and out_dir == tmp_path / "results" / run_id
    root = tmp_path / "results"
    assert rd.resolve_run(run_id, root) == root / run_id
    assert rd.resolve_run("없는-이름", root) is None
    (root / "base_x").mkdir(parents=True)
    assert rd.resolve_run("base_x", root) == root / "base_x"
    assert rd.resolve_run("..", root) == Path("..")  # 있는 경로는 그대로(이름 해석 아님)


# ⑥ --status -----------------------------------------------------------------------------


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_status_running_dead_done_and_latest(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results"
    assert cli.main(["--status"]) == 2  # run 없음
    run_id = "20261013-090000-e2-aaaaaaa"
    run_dir = _meta_run(results, run_id, started="2026-10-13T09:00:00+09:00", ended=None)
    (run_dir / rd.LOG_FILE).write_text("# head\n       12/40 APM-INST-host\n\n",
                                       encoding="utf-8")  # 드라이버 진행 줄 문형
    (run_dir / rd.PID_FILE).write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert cli.main(["--status"]) == 0
    printed = capsys.readouterr().out
    assert f"자동: run — 최근 {run_id}" in printed and f"실행 중(PID {os.getpid()})" in printed
    assert "마지막 줄  : 12/40 APM-INST-host" in printed
    (run_dir / rd.PID_FILE).write_text(f"{_dead_pid()}\n", encoding="utf-8")
    assert cli.main(["--status", run_id]) == 0
    assert "프로세스 없음 — 강제 종료·크래시 추정" in capsys.readouterr().out
    done = _meta_run(results, "20261013-100000-e1-aaaaaaa", started="2026-10-13T10:00:00+09:00",
                     ended="2026-10-13T10:01:00+09:00", env="e1")
    assert cli.main(["--status", str(done)]) == 0
    printed = capsys.readouterr().out
    assert "상태       : done · exit 0" in printed and "마지막 줄  : 없음" in printed
    assert cli.main(["--status", "20261013-110000-e1-aaaaaaa"]) == 2


# ⑦ --bg ---------------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, code: int | None) -> None:
        self.pid = 4321
        self._code = code

    def poll(self) -> int | None:
        return self._code


def test_bg_spawns_detached_child(monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "BG_WAIT_SEC", 0.2)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def popen(cmd: list[str], **kw: Any) -> _FakeProc:
        calls.append((cmd, kw))
        return _FakeProc(None)

    argv = ["--run", "--env", "e1", "--smoke", "--bg"]
    args = cli.build_parser().parse_args(argv)
    args.raw_argv = argv
    assert cli.cmd_run(args, popen=popen) == 0
    (cmd, kw), = calls
    assert cmd[:3] == [sys.executable, "-m", "scripts.apm_bench"] and "--bg" not in cmd
    run_id = cmd[cmd.index("--run-id") + 1]
    assert ss.RUN_ID_RE.fullmatch(run_id) and run_id.endswith("-smoke")
    assert kw["start_new_session"] is True
    assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL
    printed = capsys.readouterr().out
    assert f"--status {run_id}" in printed and "PID 4321" in printed
    assert not (tmp_path / "results").exists()  # 부모는 run 디렉터리를 만들지 않는다


def test_bg_refusals_and_early_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "BG_WAIT_SEC", 0.2)
    assert cli.main(["--dry-run", "--bg"]) == 2
    calls: list[Any] = []
    args = cli.build_parser().parse_args(["--run", "--bg", "--user", "op01"])
    assert cli.cmd_run(args, popen=lambda *a, **k: calls.append(1)) == 2 and calls == []
    argv = ["--run", "--only", SCENARIO, "--bg"]
    args = cli.build_parser().parse_args(argv)
    args.raw_argv = argv
    assert cli.cmd_run(args, popen=lambda *a, **k: _FakeProc(2)) == 2  # 자식이 바로 실패


def test_bg_child_runs_foreground_with_given_run_id(tmp_path: Path) -> None:
    run_id = "20261013-090000-e1-aaaaaaa"
    assert _run(["--run", "--only", SCENARIO, "--bg", "--run-id", run_id]) == 0
    assert _meta(tmp_path / "results" / run_id)["run_id"] == run_id
    assert _run(["--run", "--only", SCENARIO, "--run-id", "../x"]) == 2


def test_main_records_raw_argv_for_bg(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setitem(cli.MODES, "run", lambda args: seen.setdefault("argv", args.raw_argv)
                        and 0)
    cli.main(["--run", "--only", SCENARIO, "--bg"])
    assert seen["argv"] == ["--run", "--only", SCENARIO, "--bg"]


# ⑧ 교정 라운드 — 감사 H1 · M1 · M2 · L1 · L2 · L3 ----------------------------------------------

HOSTNAME = "prodhost-01"


def test_status_last_line_only_whitelisted_shapes(tmp_path: Path) -> None:
    """마지막 줄은 직렬화 문형 틀로 다시 쓴 조각만 — 문형 밖(ASCII 호스트 이름 포함)은 검증 불가."""
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    log = run_dir / rd.LOG_FILE
    ids = frozenset({"APM-INST-host"})
    cases = {
        f"Traceback: user=op01 host={HOSTNAME} /home/apmbench": rd.LOG_UNVERIFIED,
        HOSTNAME: rd.LOG_UNVERIFIED,
        "        3/40 APM-INST-host": "3/40 APM-INST-host",
        f"        3/40 APM-{HOSTNAME}": rd.LOG_UNVERIFIED,  # 시나리오 id 집합 밖
        f"[1/4] 게이트웨이 기동 — e2 · 소스 {HOSTNAME} · 초당 2회": "[1/4] 게이트웨이 기동 — e2",
        f"      사다리 확정: intent_orchestration (사유 {HOSTNAME})":
            "사다리 확정: intent_orchestration",
        f"결과       : 실패 — {HOSTNAME}": "결과       : 실패",
        f"[중단] 가드 — {HOSTNAME}": "[중단] (사유는 run.log)",
        f"산출물     : <--out>/{HOSTNAME} (관문 통과)": "산출물     : (관문 통과)",
        "# apm_bench run.log — run_id 20261013-090000-e2-aaaaaaa · 시작 2026-10-13T09:00:00+09:00":
            "# apm_bench run.log — run_id 20261013-090000-e2-aaaaaaa · 시작 "
            "2026-10-13T09:00:00+09:00",
    }
    for line, want in cases.items():
        log.write_text(f"# head\n{line}\n", encoding="utf-8")
        assert rd.last_log_line(run_dir, ids) == want, line
    log.write_text("x" * 20000 + "\n" + "[3/4] 로그인 완료\n", encoding="utf-8")
    assert rd.last_log_line(run_dir, ids) == "[3/4] 로그인 완료"  # 꼬리만 읽는다


def test_status_last_line_refuses_symlink_and_foreign_owner(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    target = tmp_path / "elsewhere.log"
    target.write_text("[3/4] 로그인 완료\n", encoding="utf-8")
    (run_dir / rd.LOG_FILE).symlink_to(target)
    assert rd.last_log_line(run_dir) == rd.LOG_UNVERIFIED
    (run_dir / rd.LOG_FILE).unlink()
    shutil.copy(target, run_dir / rd.LOG_FILE)
    assert rd.last_log_line(run_dir) == "[3/4] 로그인 완료"
    monkeypatch.setattr(rd.os, "geteuid", lambda: os.getuid() + 1)
    assert rd.last_log_line(run_dir) == rd.LOG_UNVERIFIED


def test_status_cli_hides_unlisted_last_line(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    run_id = "20261013-090000-e2-aaaaaaa"
    run_dir = _meta_run(tmp_path / "results", run_id, started="2026-10-13T09:00:00+09:00",
                        ended="2026-10-13T09:30:00+09:00")
    (run_dir / rd.LOG_FILE).write_text(f"{HOSTNAME} 응답 없음\n", encoding="utf-8")
    assert cli.main(["--status", run_id]) == 0
    printed = capsys.readouterr().out
    assert HOSTNAME not in printed and f"마지막 줄  : {rd.LOG_UNVERIFIED}" in printed


def test_e2_local_files_must_be_trusted(tmp_path: Path, _isolate: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """로컬 디렉터리·파일: 현재 계정 소유 · 심볼릭 링크 아님 · 그룹·기타 쓰기 없음(감사 M1)."""
    local = _local_files(_isolate)
    argv = ["--run", "--env", "e2", "--only", SCENARIO, "--out", str(tmp_path / "out")]
    local.chmod(0o777)
    assert _run(argv) == 2
    printed = capsys.readouterr().out
    assert f"~/{LOCAL}/ 를 신뢰할 수 없다 — 그룹·기타 쓰기 권한이 있다" in printed
    assert str(_isolate) not in printed
    local.chmod(0o700)
    (local / cli.LOCAL_PLACEHOLDERS).chmod(0o666)
    assert _run(argv) == 2
    assert f"~/{LOCAL}/{cli.LOCAL_PLACEHOLDERS} 를 신뢰할 수 없다" in capsys.readouterr().out
    (local / cli.LOCAL_PLACEHOLDERS).chmod(0o600)
    real = tmp_path / "real_bench.yaml"
    (local / cli.LOCAL_BENCH).rename(real)
    (local / cli.LOCAL_BENCH).symlink_to(real)
    assert _run(argv) == 2
    assert "심볼릭 링크다" in capsys.readouterr().out
    (local / cli.LOCAL_BENCH).unlink()
    real.rename(local / cli.LOCAL_BENCH)
    assert _run(argv) == 0
    loose = tmp_path / "loose.yaml"
    shutil.copy(cli.PLACEHOLDERS_E1, loose)
    loose.chmod(0o666)
    assert _run([*argv, "--placeholders", str(loose)]) == 2  # 명시 파일도 같은 조건
    printed = capsys.readouterr().out
    assert "--placeholders 파일을 신뢰할 수 없다" in printed and str(tmp_path) not in printed
    assert _run(["--run", "--only", SCENARIO, "--placeholders", str(loose),
                 "--out", str(tmp_path / "e1")]) == 0  # E1 은 종전 그대로


def test_pid_record_state_and_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    assert rd.pid_state(run_dir) == ("none", None)
    assert rd.claim_pid(run_dir, "20261013-090000-e1-aaaaaaa", 1_791_000_000.0)
    assert rd.read_pid_record(run_dir) == (os.getpid(), "20261013-090000-e1-aaaaaaa",
                                           1_791_000_000.0)
    assert not rd.claim_pid(run_dir, "20261013-090001-e1-aaaaaaa", 0.0)  # 살아 있는 PID
    assert rd.pid_state(run_dir, run_id="20261013-090000-e1-aaaaaaa")[0] == "alive"
    assert rd.pid_state(run_dir, run_id="20261013-090001-e1-aaaaaaa")[0] == "stale"
    assert rd.pid_state(run_dir, started_at="2026-10-13T09:00:00+09:00")[0] == "stale"
    (run_dir / rd.PID_FILE).write_text(f"{_dead_pid()}\n", encoding="utf-8")
    assert rd.pid_state(run_dir)[0] == "dead"
    assert rd.claim_pid(run_dir, "20261013-090002-e1-aaaaaaa", 0.0)  # 낡은 파일은 갈아쓴다

    def eperm(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(rd.os, "kill", eperm)
    assert rd.pid_alive(1) is None and rd.pid_state(run_dir)[0] == "unknown"
    assert not rd.claim_pid(run_dir, "20261013-090003-e1-aaaaaaa", 0.0)  # 확인 불가도 거부


def test_run_writes_pid_record_with_run_id(tmp_path: Path) -> None:
    seen_pid: list[Any] = []
    out = tmp_path / "out"
    base = _fake_driver({})

    def driver(picked: Any, **kw: Any) -> Any:
        seen_pid.append(rd.read_pid_record(out))
        return base(picked, **kw)

    args = cli.build_parser().parse_args(["--run", "--only", SCENARIO, "--out", str(out)])
    assert cli.cmd_run(args, driver_run=driver) == 0
    (pid, run_id, started), = seen_pid
    meta = _meta(out)
    assert pid == os.getpid() and run_id == meta["run_id"]
    assert abs(started - datetime.fromisoformat(meta["started_at"]).timestamp()) < 1
    assert not (out / rd.PID_FILE).exists()


def test_bg_child_argv_and_env_hide_user_and_paths(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    """자식 명령줄은 `--run-id`뿐 · 인자는 env(JSON)(감사 L1) · env 는 부모 그대로(셸 export 설정이
    `--bg`로 달라지면 LLM 평면이 바뀐다 — 교정 2) · 안내 줄에 `--out` 원경로 없음(감사 M2)."""
    monkeypatch.setattr(cli, "BG_WAIT_SEC", 0.2)
    monkeypatch.setenv(cli.PASSWORD_ENV, "pw-x")
    monkeypatch.setenv("LLM_PROVIDER", "mlx")
    monkeypatch.setenv("ORCHESTRATOR_PROVIDER", "mlx")
    monkeypatch.setenv(cli.BG_ARGV_ENV, '["--stale"]')  # 부모에 남은 값은 덮는다
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def popen(cmd: list[str], **kw: Any) -> Any:
        calls.append((cmd, kw))
        return _FakeProc(None)

    out = tmp_path / "bgout"
    argv = ["--run", "--only", SCENARIO, "--user", "op01", "--out", str(out), "--bg"]
    args = cli.build_parser().parse_args(argv)
    args.raw_argv = argv
    assert cli.cmd_run(args, popen=popen) == 0
    (cmd, kw), = calls
    assert cmd[3:] == ["--run-id", cmd[4]] and "op01" not in " ".join(cmd)
    env = kw["env"]
    assert json.loads(env[cli.BG_ARGV_ENV]) == argv[:-1]
    assert env[cli.PASSWORD_ENV] == "pw-x"  # 부모가 꺼낸 비밀번호는 자식에게만
    assert env["LLM_PROVIDER"] == env["ORCHESTRATOR_PROVIDER"] == "mlx"
    assert env["PATH"] == os.environ["PATH"]
    printed = capsys.readouterr().out
    assert str(tmp_path) not in printed and "--status <--out>/bgout" in printed


def test_main_consumes_bg_argv_env_only_for_child(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setitem(cli.MODES, "run", lambda args: seen.append(
        (args.only, args.user, args.run_id)) or 0)
    monkeypatch.setenv(cli.BG_ARGV_ENV, json.dumps(["--run", "--only", SCENARIO,
                                                    "--user", "op01"]))
    assert cli.main(["--run-id", "20261013-090000-e1-aaaaaaa"]) == 0
    assert seen == [([SCENARIO], "op01", "20261013-090000-e1-aaaaaaa")]
    assert cli.BG_ARGV_ENV not in os.environ  # 손자(본체·게이트웨이)로 넘기지 않는다
    monkeypatch.setenv(cli.BG_ARGV_ENV, "{broken")
    assert cli.main(["--run-id", "20261013-090000-e1-aaaaaaa"]) == 2


def test_audit_digest_path_mode_hides_out_and_rejects_reversed_window(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "audit-2026-10-13.jsonl"
    log.write_text("", encoding="utf-8")
    out = tmp_path / "digest_out"
    base = ["--audit-digest", str(log), "--out", str(out), "--env", "e1", "--only", SCENARIO]
    assert cli.main([*base, "--since", "2026-10-13T10:00:00+09:00",
                     "--until", "2026-10-13T09:00:00+09:00"]) == 2
    assert ad.WINDOW_REVERSED in capsys.readouterr().out
    assert cli.main([*base, "--since", "2026-10-13T09:00:00+09:00",
                     "--until", "2026-10-13T10:00:00+09:00"]) == 0
    printed = capsys.readouterr().out
    assert str(tmp_path) not in printed and "산출물     : <--out>/digest_out/audit_digest.json" \
        in printed


def test_view_server_version_bad_format_value_not_echoed() -> None:
    from scripts.apm_bench import guard as gd

    ok, why = gd.check_view_server_version("e2", "token-zq91")
    assert ok is False and "token-zq91" not in why


def test_bg_child_env_is_parent_env_minus_secrets() -> None:
    parent = {"LLM_PROVIDER": "fabrix", "ORCHESTRATOR_PROVIDER": "vllm", "PATH": "/bin",
              cli.PASSWORD_ENV: "pw", cli.BG_ARGV_ENV: "[]"}
    assert cli.bg_child_env(parent) == {"LLM_PROVIDER": "fabrix",
                                        "ORCHESTRATOR_PROVIDER": "vllm", "PATH": "/bin"}


def test_status_last_line_summary_and_warning_shapes() -> None:
    """정상 끝 요약 줄 · `[경고]` 줄은 그대로(코드·숫자·고정 문구만 재구성) — 자유 문자열은
    빠진다."""
    same = [
        "키트 검사: 자리표 있음 4 · 없음 0 · 여럿 2 · 검사 불가 0 · 검사 제외 1 · "
        "소스 대응 없음 6 · 소스 불일치 0",
        "자리표 실재: host_1 여럿(부분 일치 2), inst_1 있음, biz_2 없음",
        "[경고] apm_gateway/var — 이번 run 에서 새 파일 2 · 바뀐 파일 0",
        "[경고] 지연 기준선 측정 실패: TimeoutError",
        "사다리     : intent_orchestration (사유 intent_flag_on)",
        "턴         : 실행 11 · APM 관측 10 · 건너뜀 없음 · 판정 불가 2",
        "중단 사유  : 없음 · 소요 60.7초 · 뷰 서버 5.6.5.11",
        "턴 정확도  : 90.0% (9/10) · dev 100.0% (6/6) · holdout 75.0% (3/4)",
        "축         : source 100.0% · views — · target 90.0%",
        "대표 분류  : 실패 턴 target_miss 1, args_drift 2",
        "(통과 턴 포함) 없음",
        "오류 호출  : 없음 · resolve_miss 턴 0 · 호출 0",
        "target 사유: 없음",
        "locus 상위 : tool_args 3",
        "근거 누락  : 0",
    ]
    for line in same:
        assert rd.safe_log_line("      " + line) == line, line
    rebuilt = {
        f"[경고] 자리표 여럿 host_1, host_2 — 경고만({HOSTNAME})":
            "[경고] 자리표 여럿 host_1, host_2",
        f"[경고] 자리표 없음 {HOSTNAME} — 이 자리표를": "[경고] (내용은 run.log)",
        f"[경고] {HOSTNAME} 연결 실패": "[경고] (내용은 run.log)",
        f"[경고] 격리 보관 실패 — {HOSTNAME}": "[경고] 격리 보관 실패",
        f"[방향 확인용 — 결론 금지] env e2 · 평면 {{'worker': '{HOSTNAME}'}}":
            "[방향 확인용 — 결론 금지] env e2",
        f"호출       : 소스별 {{'{HOSTNAME}': 3}} · 타임아웃 1 · 429 0 · 기준선 742.0ms · 인벤토리 "
        "도구 2회 · 인스턴스 10 · 인벤토리 실패 {'x': 1} · P층 0회":
            "호출       : 타임아웃 1 · 429 0 · 기준선 742.0ms · 인벤토리 도구 2회 · 인스턴스 10 · "
            "P층 0회",
        f"중단 사유  : {HOSTNAME} 없음 · 소요 1.0초 · 뷰 서버 해당 없음":
            "중단 사유  : (사유는 run.log) · 소요 1.0초 · 뷰 서버 해당 없음",
        f"오류 호출  : apm_x·{HOSTNAME}·call 1 · resolve_miss 턴 1 · 호출 2":
            "오류 호출  : (목록은 run.log) · resolve_miss 턴 1 · 호출 2",
        f"근거 누락  : 1 — APM-INST-host#1 {HOSTNAME}": "근거 누락  : 1",
        f"사용자 정보: 치환 {{'{HOSTNAME}': 1}}": "사용자 정보: 치환 있음",
    }
    for line, want in rebuilt.items():
        assert rd.safe_log_line(line) == want, line
    for line in (f"locus 상위 : {HOSTNAME} 3", f"축         : {HOSTNAME} 100.0%",
                 f"자리표 실재: {HOSTNAME} 있음"):
        assert rd.safe_log_line(line) is None, line
