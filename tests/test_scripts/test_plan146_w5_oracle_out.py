"""plans/146 W5 (3) — `--check-oracle --out <run 디렉터리>`가 값 0인 `oracle_check.yaml`을
누출 관문을 거쳐 `<run>/oracle_check/`에 남긴다.

- `--out` 없으면 화면 출력·종료 코드가 종전과 같다(바이트 고정).
- 파일에는 오라클 ID · 판정 범주(`ok`/`zero_rows`/`error`) · 행 수 · 소요 ms ·
  시나리오 ID·턴 · 앵커만 — DB 오류 문구(원문·가린 문구 모두)·값·SQL 없음.
- 관문 실패면 `oracle_check.yaml` 없이 `leak_check.json`만 · 기존 run 산출물(`run.json`·
  `leak_check.json`)은 건드리지 않는다.

DB·LLM 0 — `run_oracle`·`load_config`는 목.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import __main__ as cli

REPO = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO / "testdata" / "itam_bench" / "scenarios.closed.probe.yaml"
FIXED_NOW = datetime(2026, 10, 8, 10, 15, 0, tzinfo=cli.KST)
#: 오류 사유에 섞여 들어오는 값 같은 문자열 — 파일에는 실리면 안 된다
LEAKY_REASON = "Unknown column 'tcdmsif72.zz_secret_val' near 'SELECT 10.20.30.40 admin_kim'"

OUTCOMES: dict[str, dict[str, Any]] = {
    "ITAM-146-P01": {
        "status": "ok", "rows_by_db": {"itam": [{"k": 1}, {"k": 2}]}, "elapsed_ms": 12.4
    },
    "ITAM-146-P02": {"status": "ok", "rows_by_db": {"itam": []}, "elapsed_ms": 3.0},
    "ITAM-146-P03": {"status": "unavailable", "reason": LEAKY_REASON, "rows_by_db": {}},
}

ARGV = ["--check-oracle", "--env", "closed", "--scenarios", str(PROBE_PATH)]
ONLY = ["--only", "ITAM-146-P01,ITAM-146-P02,ITAM-146-P03"]


@pytest.fixture
def mocked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """`run_oracle`·`load_config`·시각·DSN 을 고정한다 — 호출된 오라클 ID 목록을 돌려준다."""
    from scripts.scenario import oracle as oracle_mod

    calls: list[str] = []

    def fake_run_oracle(spec: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        calls.append(str(spec["id"]))
        return dict(OUTCOMES[str(spec["id"])])

    monkeypatch.setattr(oracle_mod, "run_oracle", fake_run_oracle)
    monkeypatch.setattr("src.config.load_config", lambda: SimpleNamespace(db_backend="dbhub"))
    monkeypatch.setattr(cli, "_now", lambda: FIXED_NOW)
    monkeypatch.setattr(cli, "_itam_dsn", lambda: None)
    return calls


def _expected_screen() -> str:
    reason = cli.rd.redact_text(LEAKY_REASON, vault=cli.rd.PiiVault())
    return (
        "  ITAM-146-P01 ok     행   2 · 12ms (ITAM-146-P01 턴1)\n"
        "  ITAM-146-P02 0행 — 정답을 구하지 못한다(시드·날짜 경계 확인) 행   0 · 3ms "
        "(ITAM-146-P02 턴1)\n"
        f"  ITAM-146-P03 불가 — {reason} 행   0 · 0ms (ITAM-146-P03 턴1)\n"
        "오라클 3건 중 실패·0행 2건 (앵커 2026-10-08T10:15:00+09:00)\n"
    )


def test_without_out_screen_and_exit_unchanged(
    mocked: list[str], capsys: pytest.CaptureFixture[str], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = cli.main([*ARGV, *ONLY])
    assert rc == 1
    assert capsys.readouterr().out == _expected_screen()
    assert mocked == ["ITAM-146-P01", "ITAM-146-P02", "ITAM-146-P03"]
    assert list(tmp_path.iterdir()) == []


def test_all_ok_exit_zero_without_out(
    mocked: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([*ARGV, "--only", "ITAM-146-P01"]) == 0
    assert capsys.readouterr().out == (
        "  ITAM-146-P01 ok     행   2 · 12ms (ITAM-146-P01 턴1)\n"
        "오라클 1건 중 실패·0행 0건 (앵커 2026-10-08T10:15:00+09:00)\n"
    )


def test_out_writes_value_free_file_under_subdir(
    mocked: list[str], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    run_dir = tmp_path / "run3"
    run_dir.mkdir()
    (run_dir / "run.json").write_text('{"run_id": "run3"}\n', encoding="utf-8")
    (run_dir / "leak_check.json").write_text('{"passed": true}\n', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in run_dir.iterdir()}

    rc = cli.main([*ARGV, *ONLY, "--out", str(run_dir)])

    assert rc == 1  # 실패·0행이 있으면 1 — 종전 규칙
    out = capsys.readouterr().out
    assert out.startswith(_expected_screen())  # 화면 줄은 그대로, 기록 줄만 덧붙는다
    assert "산출물 기록" in out[len(_expected_screen()):]
    # 기존 run 산출물 무손상
    assert {p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()} == before
    sub = run_dir / cli.ORACLE_CHECK_DIR
    assert sorted(p.name for p in sub.iterdir()) == ["leak_check.json", cli.ORACLE_CHECK_FILE]
    leak = json.loads((sub / "leak_check.json").read_text(encoding="utf-8"))
    assert leak["passed"] is True
    assert leak["checked_files"] == [cli.ORACLE_CHECK_FILE]

    text = (sub / cli.ORACLE_CHECK_FILE).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert doc["kind"] == "oracle_check"
    assert doc["anchor_at"] == "2026-10-08T10:15:00+09:00"
    assert doc["run_id"] == "check-20261008-101500"
    assert doc["env"] == "closed"
    assert doc["scenario_file"] == PROBE_PATH.name
    assert doc["summary"] == {"oracles": 3, "ok": 1, "zero_rows": 1, "error": 1, "failed": 2}
    assert doc["oracles"] == [
        {"id": "ITAM-146-P01", "scenario": "ITAM-146-P01", "turn": 1, "verdict": "ok",
         "rows": 2, "elapsed_ms": 12},
        {"id": "ITAM-146-P02", "scenario": "ITAM-146-P02", "turn": 1, "verdict": "zero_rows",
         "rows": 0, "elapsed_ms": 3},
        {"id": "ITAM-146-P03", "scenario": "ITAM-146-P03", "turn": 1, "verdict": "error",
         "rows": 0, "elapsed_ms": 0},
    ]
    # DB 오류 문구는 원문·가린 문구 모두 파일에 없다
    for fragment in ("zz_secret_val", "10.20.30.40", "admin_kim", "Unknown column", "SELECT",
                     "불가", "***"):
        assert fragment not in text, fragment


def test_rerun_replaces_previous_file(
    mocked: list[str], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert cli.main([*ARGV, "--only", "ITAM-146-P01", "--out", str(tmp_path)]) == 0
    assert cli.main([*ARGV, *ONLY, "--out", str(tmp_path)]) == 1
    doc = yaml.safe_load(
        (tmp_path / cli.ORACLE_CHECK_DIR / cli.ORACLE_CHECK_FILE).read_text(encoding="utf-8")
    )
    assert doc["summary"]["oracles"] == 3


def test_gate_failure_leaves_only_leak_check(
    mocked: list[str], capsys: pytest.CaptureFixture[str], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 먼저 통과 run 으로 옛 파일을 만든다 — 관문 실패 때 옛 파일이 새것처럼 남으면 안 된다
    assert cli.main([*ARGV, "--only", "ITAM-146-P01", "--out", str(tmp_path)]) == 0
    capsys.readouterr()
    # 사용자 정보 원값이 파일 글(시나리오 ID)과 겹치게 해 관문 `user_info`를 일부러 건다
    real = cli.user_values
    monkeypatch.setattr(
        cli, "user_values",
        lambda **kw: {**real(**kw), "os_user": "ITAM-146-P01"},
    )
    rc = cli.main([*ARGV, "--only", "ITAM-146-P01", "--out", str(tmp_path)])
    assert rc == 1  # 오라클은 전부 ok 지만 관문 실패는 실패
    out = capsys.readouterr().out
    assert "미기록(누출 관문 실패)" in out
    assert "user_info" in out
    assert "ITAM-146-P01 · " not in out.split("미기록")[1]  # 위치만(값 없음) — 파일·칸·규칙
    sub = tmp_path / cli.ORACLE_CHECK_DIR
    assert sorted(p.name for p in sub.iterdir()) == ["leak_check.json"]
    leak = json.loads((sub / "leak_check.json").read_text(encoding="utf-8"))
    assert leak["passed"] is False
    assert {v["rule"] for v in leak["violations"]} == {"user_info"}


def test_out_help_names_both_modes() -> None:
    action = next(a for a in cli.build_parser()._actions if "--out" in a.option_strings)
    assert "--validate-knowledge" in str(action.help)
    assert "--check-oracle" in str(action.help)
