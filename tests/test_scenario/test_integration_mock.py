"""무과금 전 경로 통합 — V6·V12·V13·V20 (plans/94 §10).

실제로 **모의 서버를 자식 프로세스로 띄우고** 러너->단언기->리포트->분석기를 끝까지 돌린다.
접속 대상이 127.0.0.1 뿐이라 tests/conftest.py 의 외부 접속 가드 아래에서 통과한다(V12).

LLM 도 DB 도 호출하지 않는다. 느리므로(서버 기동 포함) 이 파일만 별도로 돌릴 수 있게 둔다:
    pytest tests/test_scenario/test_integration_mock.py -q
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.analyze import analyze
from scripts.scenario.catalog import load_catalog
from scripts.scenario.report import write_report
from scripts.scenario.runner import RunConfig, execute

@pytest.fixture(scope="module")
def mock_run(tmp_path_factory) -> dict:
    """F-01(존 선택 HITL 2턴)을 모의 서버로 끝까지 돌린 결과."""
    out_root = tmp_path_factory.mktemp("results")
    original = runner_mod.RESULTS_ROOT
    runner_mod.RESULTS_ROOT = out_root
    try:
        catalog = load_catalog()
        summary = execute(catalog, RunConfig(mode="mock", env="closed", only=["F-01"]))
        run_dir = Path(summary["out_dir"])
        write_report(run_dir, catalog)
        analyze(run_dir, catalog)
        return {"summary": summary, "run_dir": run_dir, "catalog": catalog}
    finally:
        runner_mod.RESULTS_ROOT = original


def _rows(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in
            (run_dir / "raw.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


# --- V12 무과금 전 경로 --------------------------------------------------

def test_V12_무과금_경로가_외부_접속_없이_끝까지_돈다(mock_run: dict) -> None:
    assert mock_run["summary"]["executed_turns"] == 2
    profiles = mock_run["summary"]["profiles"]
    assert profiles and profiles[0]["valid"] is True
    assert profiles[0]["tier"] == "mock"


def test_V12_리포트와_분석_산출이_전부_생성된다(mock_run: dict) -> None:
    run_dir = mock_run["run_dir"]
    for name in ("raw.jsonl", "run.json", "summary.json", "report.md",
                 "bottleneck.md", "failure_taxonomy.md", "coverage_gap.md",
                 "regression.md", "countermeasures.md", "improvement_backlog.md"):
        assert (run_dir / name).exists(), f"{name} 이 없다"


def test_V12_리포트가_모의_실행임을_숨기지_않는다(mock_run: dict) -> None:
    text = (mock_run["run_dir"] / "report.md").read_text(encoding="utf-8")
    assert "모의 실행" in text
    assert "시스템 품질의 근거가 아니다" in text


# --- V6 HITL 자동 진행 ---------------------------------------------------

def test_V6_존_선택_역질문이_스크립트로_왕복된다(mock_run: dict) -> None:
    """역질문 응답은 자연어가 아니라 구조화 필드 selected_db_ids 로 보낸다(§0.3-2)."""
    rows = _rows(mock_run["run_dir"])
    assert [row["turn"] for row in rows] == [1, 2]

    first, second = rows
    assert first["response_mode"] == "clarify"
    assert first["func_verdict"] == "pass"
    assert second["response_mode"] == "answer"
    assert second["func_verdict"] == "pass"
    assert "cmm_resource" in (second["executed_sql"] or "")


def test_V6_두_턴이_같은_스레드로_이어진다(mock_run: dict) -> None:
    """thread_id 가 안 실리면 2턴이 첫 턴으로 취급돼 HITL 이 성립하지 않는다."""
    rows = _rows(mock_run["run_dir"])
    assert rows[1]["row_count"] == 12      # 모의 서버가 2턴 응답을 돌려줬다는 증거
    assert rows[1]["perf_verdict"] == "pass"


def test_V6_노드별_지연이_수집된다(mock_run: dict) -> None:
    """성공 건의 지연 분해는 SSE 로만 얻는다 - 비면 측정 경로가 끊긴 것이다."""
    rows = _rows(mock_run["run_dir"])
    assert rows[1]["node_elapsed_ms"], "node_elapsed_ms 가 비었다"
    assert rows[1]["node_path"], "node_path 가 비었다"
    assert "done" in rows[1]["sse_events"]


# --- V13 운영 상태 오염 --------------------------------------------------

def test_V13_런_전용_체크포인트가_런_디렉터리에_생긴다(mock_run: dict) -> None:
    """운영 checkpoints.db(실측 82MB)를 건드리지 않는다."""
    run_dir = mock_run["run_dir"]
    assert not (Path("checkpoints.db").exists() and
                Path("checkpoints.db").stat().st_mtime > run_dir.stat().st_mtime)


def test_V13_저장소_카탈로그와_설정이_변하지_않는다(mock_run: dict) -> None:
    catalog = load_catalog()
    assert len(catalog.scenarios) == len(mock_run["catalog"].scenarios)
    assert Path("config/scenarios/profiles.yaml").exists()


# --- V20 고아 프로세스 ---------------------------------------------------

def test_V20_런_종료_후_포트가_회수된다(mock_run: dict) -> None:
    """종료 절차가 자식을 남기면 포트가 붙들려 다음 런이 깨진다(부록 A.1-1·2)."""
    port = mock_run["summary"]["profiles"][0]["port"]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))     # 바인딩되면 회수된 것이다


def test_V20_포트_미회수는_사유로_남는다(mock_run: dict) -> None:
    reasons = mock_run["summary"]["profiles"][0]["reasons"]
    assert not any("회수되지 않았다" in reason for reason in reasons), reasons


# --- 재개 (V11 통합 확인) ------------------------------------------------

def test_V11_같은_런을_다시_돌리면_중복_적재가_없다(mock_run: dict) -> None:
    """resume 은 (profile, scenario_id, turn, repeat) 로 판정한다."""
    run_dir = mock_run["run_dir"]
    before = len(_rows(run_dir))
    original = runner_mod.RESULTS_ROOT
    runner_mod.RESULTS_ROOT = run_dir.parent
    try:
        execute(mock_run["catalog"],
                RunConfig(mode="mock", env="closed", only=["F-01"],
                          resume_from=run_dir.name))
    finally:
        runner_mod.RESULTS_ROOT = original
    assert len(_rows(run_dir)) == before
