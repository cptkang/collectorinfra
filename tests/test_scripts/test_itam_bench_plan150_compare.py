"""plans/150 W9 5번 보강 — `--compare` 의 「프롬프트 세트 교체」 고지와 범주별 판정 표.

두 run 의 `run.json` `scenario_file` 이 다르면 출력 머리에 고지 1줄과 「범주별 판정」 절을 낸다.
같으면(또는 한쪽이 기록하지 않았으면) 출력은 그 전과 같다. 입력은 합성 run 이다(`results/` 는
있으면 읽기만 한다). 실 LLM·DB 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.itam_bench import report as rp

_RESULTS = Path(__file__).resolve().parents[2] / "results" / "itam_bench"


def _record(sid: str, turn: int, category: str, verdict: str | None) -> dict[str, Any]:
    return {
        "id": sid,
        "turn": turn,
        "category": category,
        "oracle": {"verdict": verdict} if verdict else None,
        "taxonomy": [],
    }


def _write_run(directory: Path, run: dict[str, Any], records: list[dict[str, Any]]) -> Path:
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    (directory / "trace.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return directory


def _pair(tmp_path: Path, file_a: str | None, file_b: str | None) -> tuple[Path, Path]:
    base = {"tier": "intent_orchestration", "env": "closed", "assets": {"profile": "a"}}
    run_a = {**base, "run_id": "r5"}
    run_b = {**base, "run_id": "r6"}
    if file_a:
        run_a["scenario_file"] = file_a
    if file_b:
        run_b["scenario_file"] = file_b
    records_a = [
        _record("ITAM-001", 1, "service", "pass"),
        _record("ITAM-002", 1, "service", "fail"),
        _record("ITAM-003", 1, "asset", None),
    ]
    records_b = [
        _record("ITAM-150-P01", 1, "service", "pass"),
        _record("ITAM-150-P02", 1, "asset", "hold"),
        _record("ITAM-150-P03A", 1, "asset", "pass"),
    ]
    tag = f"{file_a}-{file_b}"
    return (
        _write_run(tmp_path / tag / "a", run_a, records_a),
        _write_run(tmp_path / tag / "b", run_b, records_b),
    )


class TestPromptSetSwap:
    def test_different_files_get_notice_and_category_table(self, tmp_path: Path) -> None:
        a, b = _pair(tmp_path, "scenarios.closed.yaml", "scenarios.closed.r6.yaml")
        out = rp.compare_runs(a, b)
        head = out.splitlines()[2]
        assert head.startswith("> **프롬프트 세트 교체** — `scenarios.closed.yaml` → ")
        assert "`scenarios.closed.r6.yaml`" in head
        assert "| 범주 | 전 | 후 |" in out
        # pass/fail/hold/observe 순
        assert "| asset | 0/0/0/1 | 1/0/1/0 |" in out
        assert "| service | 1/1/0/0 | 1/0/0/0 |" in out
        assert out.index("## 범주별 판정") < out.index("## 전이")

    @pytest.mark.parametrize(
        ("file_a", "file_b"),
        [("scenarios.closed.yaml", "scenarios.closed.yaml"), (None, "scenarios.closed.yaml")],
    )
    def test_same_or_unrecorded_file_is_unchanged(
        self, tmp_path: Path, file_a: str | None, file_b: str | None
    ) -> None:
        a, b = _pair(tmp_path, file_a, file_b)
        out = rp.compare_runs(a, b)
        assert "프롬프트 세트 교체" not in out
        assert "## 범주별 판정" not in out
        # 시나리오 파일 기록이 없는 경우와 바이트가 같다
        a0, b0 = _pair(tmp_path / "none", None, None)
        assert out == rp.compare_runs(a0, b0)

    def test_run4_to_run5_has_no_notice(self) -> None:
        a, b = _RESULTS / "20261008-133613", _RESULTS / "20261008-163344"
        if not (a / "run.json").is_file() or not (b / "run.json").is_file():
            pytest.skip("4·5회차 run 산출물 없음")
        out = rp.compare_runs(a, b)
        assert "프롬프트 세트 교체" not in out
        assert "## 범주별 판정" not in out
