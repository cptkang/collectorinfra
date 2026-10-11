"""러너 실프로세스 테스트 — 드라이런 전 순서 · 반출물 자기 검사 · POC_MODE 아님 (plans/148 W3b).

가짜 KBGenAI·프록시를 하위 프로세스로 띄운다(실 LLM 0건). 끝에 자기 PID만 종료됐는지 본다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc_run as pr  # noqa: E402

OUTPUTS = ("summary.md", "results.jsonl", "env_check.md", "recommend.env")


@pytest.fixture()
def spawned(monkeypatch: pytest.MonkeyPatch) -> Any:
    """러너가 띄운 하위 프로세스를 모아 두고, 끝에 전부 종료됐는지 확인한다."""
    procs: list[subprocess.Popen[Any]] = []
    original = subprocess.Popen

    class Tracked(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            procs.append(self)

    monkeypatch.setattr(subprocess, "Popen", Tracked)
    yield procs
    alive = [p.pid for p in procs if p.poll() is None]
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert alive == [], f"남은 프로세스: {alive}"


def _exported(out: Path) -> list[Path]:
    return [p for p in out.iterdir() if p.is_file()]


def test_full_dry_run_produces_outputs_without_leaks(tmp_path: Path, spawned: Any) -> None:
    out = tmp_path / "out"
    assert pr.main(["run", "--dry-run", "--repeat", "1", "--out", str(out)]) == 0
    for name in OUTPUTS + ("probe_native.json", "probe_contents.json"):
        assert (out / name).is_file(), name
    assert len(spawned) >= 2  # 가짜 KBGenAI + 프록시

    records = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    scenarios = {r["scenario"] for r in records}
    assert scenarios >= {"S1", "S2", "S3", "S4", "S5", "S6", "F5"}
    fewshots = {r["options"]["fewshot"] for r in records}
    langs = {r["options"]["protocol_lang"] for r in records}
    assert fewshots >= {"none", "static", "dynamic"} and langs == {"en", "ko"}  # ⑤a · ⑤b
    assert all("raw_heads" not in r for r in records)

    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "RECOMMEND " in summary
    env_keys = {
        line.split("=", 1)[0]
        for line in (out / "recommend.env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert env_keys >= {"CONTENTS_MODE", "FEWSHOT", "FEWSHOT_PLACEMENT", "PROTOCOL_LANG"}

    guard = pr.build_guard([])
    guard.secret_needles.update({"dry_api": "dryrun-apikey-7Q3", "dry_client": "dryrun-clientk"})
    for path in _exported(out):
        assert guard.violations(path.read_text(encoding="utf-8")) == [], path.name
    assert "raw_heads" not in summary


def test_marker_in_results_aborts_and_removes_file(
    tmp_path: Path, spawned: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    original = pr.build_call_record

    def leaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
        record = original(*args, **kwargs)
        record["note"] = "ZX-4417"
        return record

    monkeypatch.setattr(pr, "build_call_record", leaky)
    out = tmp_path / "out"
    code = pr.main(["run", "--dry-run", "--only", "S4", "--repeat", "1", "--out", str(out)])
    assert code == 3
    assert not (out / "results.jsonl").exists()
    err = capsys.readouterr().err
    assert "leak_marker" in err and "ZX-4417" not in err


def test_marker_in_summary_aborts_and_removes_file(
    tmp_path: Path, spawned: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    original = pr.render_summary

    def leaky(*args: Any, **kwargs: Any) -> str:
        return original(*args, **kwargs) + "\nsee https://internal.example/x\n"

    monkeypatch.setattr(pr, "render_summary", leaky)
    out = tmp_path / "out"
    code = pr.main(["run", "--dry-run", "--only", "S4", "--repeat", "1", "--out", str(out)])
    assert code == 3
    assert not (out / "summary.md").exists()
    err = capsys.readouterr().err
    assert "url" in err and "internal.example" not in err


def test_proxy_without_poc_mode_stops_with_message(
    tmp_path: Path, spawned: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    with pr.dry_stack(poc_mode=False) as stack:
        monkeypatch.setattr(pr, "load_settings", lambda: stack.settings)
        out = tmp_path / "out"
        code = pr.main(["run", "--proxy-url", stack.proxy_url, "--only", "S1",
                        "--repeat", "1", "--out", str(out)])
    assert code == 1
    assert "POC_MODE" in capsys.readouterr().err
    assert not (out / "results.jsonl").exists()


def test_usage_errors_exit_2(tmp_path: Path, capsys: Any) -> None:
    out = str(tmp_path / "out")
    assert pr.main(["run", "--dry-run", "--repeat", "S9=1", "--out", out]) == 2
    assert pr.main(["run", "--dry-run", "--only", "S7", "--out", out]) == 2
    assert pr.main(["run", "--dry-run", "--concurrency", "0", "--out", out]) == 2
    assert "사용법" in capsys.readouterr().err


def test_s6_dry_run_judges_reflection_and_dumps_no_tool_use(
    tmp_path: Path, spawned: Any
) -> None:
    """가짜 KBGenAI 대본의 S6 차례 — 정상 반영 · 도구 미사용 · 유니코드 변종 반영 · 표지 누락."""
    out = tmp_path / "out"
    assert pr.main(["run", "--dry-run", "--only", "S6", "--repeat", "S6=8", "--out", str(out)]) == 0
    records = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    cases = {
        (r["case"], r["rep"]): (r["completed"], r["domain_tools_used"], r["reflected"],
                                r["failure_type"])
        for r in records if r["record"] == "case"
    }
    assert cases[("s6_agent_a", 1)] == (True, True, True, None)
    assert cases[("s6_agent_a", 2)] == (True, False, False, "no_tool_use")
    assert cases[("s6_agent_a", 3)] == (True, True, True, None)  # U+2011·U+202F 접기
    assert cases[("s6_agent_a", 4)] == (True, True, False, "history")
    assert all(not v[0] and v[3] == "format" for k, v in cases.items() if k[0] == "s6_agent_b")

    dumps = {p.name for p in (out / "failures").rglob("*.txt")}
    assert {"s6_agent_a_r2.txt", "s6_agent_a_r4.txt"} <= dumps  # 원출력은 failures/에만

    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "| S6 반영률(도메인 도구 사용 + 표지 포함) | 25.0% (2/8) | ≥90% | 미달 |" in summary
    assert "| 완주율(S6) | 50.0% (4/8) | — | 기록(판정 아님) |" in summary
    assert "도구 미사용 1" in summary
    guard = pr.build_guard([])
    for path in _exported(out):
        assert guard.violations(path.read_text(encoding="utf-8")) == [], path.name
        assert "Rei" not in path.read_text(encoding="utf-8"), path.name
