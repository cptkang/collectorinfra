"""진입점 표면 — 사용자가 실제로 치는 명령 (plans/94 「실행 가이드」).

명령은 네 개뿐이고 **인자 없는 기본 동작이 무과금**이다. 이 파일은 그 약속을 고정한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.scenario import utf8_open
from scripts.scenario.__main__ import main
from scripts.scenario import __main__ as cli
from scripts.scenario import runner as runner_mod


@pytest.fixture()
def fake_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """리포트/분석이 읽을 최소 run 디렉터리."""
    root = tmp_path / "results"
    run_dir = root / "20260911-000001"
    run_dir.mkdir(parents=True)
    row = {
        "run_id": run_dir.name, "profile": "baseline", "env": "sandbox", "mode": "mock",
        "repeat": 0, "group": "L", "scenario_id": "SYN-A-01", "turn": 1, "plans": [37],
        "kind": "normal", "pair_id": None, "func_verdict": "pass", "perf_verdict": "pass",
        "response_mode": "answer", "forbidden_mode": None, "failed_assertions": [],
        "manual_notes": [], "wall_ms": 10.0, "processing_time_ms": 10.0,
        "node_elapsed_ms": {"a": 5.0}, "node_path": ["a"], "sse_events": ["done"],
        "executed_sql": "SELECT 1", "row_count": 1, "artifacts": [], "error": None,
    }
    with utf8_open(run_dir / "raw.jsonl", "w") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with utf8_open(run_dir / "run.json", "w") as handle:
        json.dump({"meta": {"run_id": run_dir.name, "env": "sandbox", "mode": "mock",
                            "repeat": 1, "provider": "mock"},
                   "profiles": [], "skipped": []}, handle, ensure_ascii=False)
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", root)
    monkeypatch.setattr(cli, "RESULTS_ROOT", root)
    return run_dir


# --- 무과금 기본 동작 순서 ------------------------------------------------

def test_인자가_없으면_1단_2단_3단_순서로_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "cmd_dry_run", lambda a: (calls.append("dry"), 0)[1])
    monkeypatch.setattr(cli, "cmd_mock", lambda a: (calls.append("mock"), 0)[1])
    monkeypatch.setattr(cli, "cmd_estimate", lambda a: (calls.append("est"), 0)[1])
    monkeypatch.setattr(cli, "cmd_run", lambda a: (calls.append("run"), 0)[1])

    assert main([]) == 0
    assert calls == ["dry", "mock", "est"]
    assert "run" not in calls, "기본 동작이 과금 경로를 건드렸다"


def test_1단을_통과하지_못하면_2단은_시작되지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "cmd_dry_run", lambda a: (calls.append("dry"), 1)[1])
    monkeypatch.setattr(cli, "cmd_mock", lambda a: (calls.append("mock"), 0)[1])
    monkeypatch.setattr(cli, "cmd_estimate", lambda a: (calls.append("est"), 0)[1])

    assert main([]) == 1
    assert calls == ["dry"]


# --- --mock ---------------------------------------------------------------

def test_mock_은_리포트까지_만들고_0을_돌려준다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path / "results")
    assert main(["--mock", "--only", "F-01", "--env", "closed"]) == 0
    out = capsys.readouterr().out
    assert "모의 실행 완료" in out and "리포트" in out
    runs = list((tmp_path / "results").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "report.md").exists()
    assert (runs[0] / "summary.json").exists()


# --- --report -------------------------------------------------------------

def test_report_는_지정한_run_을_재생성한다(fake_run: Path, capsys) -> None:
    (fake_run / "report.md").unlink(missing_ok=True)
    assert main(["--report", fake_run.name]) == 0
    assert (fake_run / "report.md").exists()
    assert "리포트 재생성" in capsys.readouterr().out


def test_report_는_인자_없이_최신_run_을_쓴다(fake_run: Path) -> None:
    assert main(["--report"]) == 0
    assert (fake_run / "summary.json").exists()


def test_report_는_없는_run_에_1을_돌려준다(fake_run: Path, capsys) -> None:
    assert main(["--report", "없는런"]) == 1
    assert "없습니다" in capsys.readouterr().err


# --- --analyze ------------------------------------------------------------

def test_analyze_는_제안_문서_6종을_만든다(fake_run: Path, capsys) -> None:
    assert main(["--analyze"]) == 0
    for name in ("bottleneck.md", "failure_taxonomy.md", "coverage_gap.md",
                 "regression.md", "countermeasures.md", "improvement_backlog.md"):
        assert (fake_run / name).exists(), name
    assert "분석 산출 6종" in capsys.readouterr().out


def test_analyze_도_run_을_지정할_수_있다(fake_run: Path) -> None:
    assert main(["--analyze", fake_run.name]) == 0
    assert (fake_run / "countermeasures.md").exists()


def test_analyze_는_run_이_없으면_1을_돌려준다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path / "empty")
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path / "empty")
    assert main(["--analyze"]) == 1
    assert "없습니다" in capsys.readouterr().err


# --- 선택지 --------------------------------------------------------------

def test_only_는_쉼표로_여러_건을_받는다(capsys) -> None:
    assert main(["--dry-run", "--only", "F-01,F-02", "--env", "closed"]) == 0
    assert "선택: 2건" in capsys.readouterr().out


def test_group_은_반복_지정된다(capsys) -> None:
    assert main(["--dry-run", "--group", "R3", "--group", "R4", "--env", "closed"]) == 0
    out = capsys.readouterr().out
    assert "R3" in out and "R4" in out


def test_env_불일치_시나리오는_선택에서_빠진다(capsys) -> None:
    """폐쇄망 전용 케이스를 샌드박스에서 돌린 결과는 판정 근거가 아니다(§2-5)."""
    main(["--dry-run", "--only", "F-01", "--env", "sandbox"])
    assert "선택: 0건" in capsys.readouterr().out


def test_비대화_환경에서는_승인_없이_과금_경로를_열지_않는다(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """CI/파이프에서 input() 은 EOFError 다. 승인을 못 받으면 멈추는 것이 맞다(D-127)."""
    monkeypatch.setenv("RUN_E2E", "1")

    def no_tty(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_tty)
    monkeypatch.setattr(cli, "execute", lambda *a, **k: pytest.fail("실행되면 안 된다"))
    assert main(["--run", "--only", "F-01", "--env", "closed"]) == 130
    assert "--yes" in capsys.readouterr().out


def test_resume_대상이_없으면_새_런을_만들지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """없는 run_id 로 이어붙이면 같은 이름의 새 런이 생겨 처음부터 다시 돈다."""
    monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path / "results")
    assert main(["--mock", "--resume", "오타런"]) == 1
    assert "--resume 대상이 없습니다" in capsys.readouterr().err
    assert not (tmp_path / "results" / "오타런").exists()


def test_도움말이_기본_동작이_무과금임을_말한다(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "무과금" in capsys.readouterr().out
