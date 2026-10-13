"""plans/154 W3 — 가짜 LLM 종단(L3 · §9.3.4).

세계 서버 + 가짜 LLM + 실제 sre_agent(`_serve.py`) 2실험군 × T01·T02 × k=1을 실프로세스로 한 번
돌리고 산출물(manifest · world_calls · raw · scores · report · 내보내기 묶음)을 본다. 실 LLM 0 —
가짜 LLM은 루프백이고 하위 프로세스의 Gemini 키는 공란이다. 앞쪽은 가짜 LLM 단위 검사다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from investigation_bench import export, judge, runner
from investigation_bench.fake_llm import SERVED_MODEL, FakeLLM
from investigation_bench.worlds import load_worlds

_TOP = Path(__file__).resolve().parents[1]
_FIX = _TOP / "tests" / "fixtures" / "investigation_bench"
_SPEC = _TOP / "testdata" / "investigation_bench" / "specs" / "fake_e2e.yaml"
_FIXED_PORTS = "ports: {fake_llm: 19180, world: 19190, world_apm: 19191, arm_base: 19200}"
_ZERO_PORTS = "ports: {fake_llm: 0, world: 0, world_apm: 0, arm_base: 0}"
_PROD_AUDIT = _TOP / ".data" / "investigation_audit.jsonl"
_SERVICE_PORTS = {9097, 9098, 9099}  # 운영 sre_agent·MCP 포트 — 벤치는 열지 않는다
_BUDGET_S = 120.0

_WORLDS = load_worlds([_FIX / "worlds"])
_TOOLS = [{"type": "function", "function": {"name": "polestar_metric_trend", "parameters": {}}}]


# ── 가짜 LLM 단위 ──


def _body(n_assistant: int, tools: bool = True) -> dict[str, Any]:
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "s"},
                                  {"role": "user", "content": "u"}]
    for i in range(n_assistant):
        msgs += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"}]
    return {"model": SERVED_MODEL, "messages": msgs, **({"tools": _TOOLS} if tools else {})}


def test_fake_llm_replays_script_by_assistant_turns(tmp_path: Path) -> None:
    llm = FakeLLM(_WORLDS, tmp_path / "fake.jsonl")
    llm.set_active("T01", trial=1, arm="control", script="model")
    first = llm.respond(_body(0))["choices"][0]
    assert first["finish_reason"] == "tool_calls"
    call = first["message"]["tool_calls"][0]["function"]
    assert call["name"] == "polestar_metric_trend"
    assert json.loads(call["arguments"])["kind"] == "filesystem"
    second = llm.respond(_body(1))
    assert second["choices"][0]["finish_reason"] == "stop"
    assert "98.4%" in second["choices"][0]["message"]["content"]
    assert second["usage"]["total_tokens"] > 0
    again = llm.respond(_body(1))  # 무상태 — 같은 요청 = 같은 응답(id·created는 시각이라 제외)
    assert (again["choices"], again["usage"]) == (second["choices"], second["usage"])
    assert llm.respond(_body(5))["choices"][0]["message"]["content"] == \
        second["choices"][0]["message"]["content"]  # 대본 끝 = 마지막 답
    with pytest.raises(KeyError):
        llm.set_active("T02", script="neg.F-DIST")  # T02에는 그 음성 대본이 없다


def test_fake_llm_without_world_does_one_tool_roundtrip() -> None:
    llm = FakeLLM(_WORLDS)
    reply = llm.respond(_body(0))["choices"][0]
    assert reply["message"]["tool_calls"][0]["function"]["name"] == "polestar_metric_trend"
    assert llm.respond(_body(1))["choices"][0]["finish_reason"] == "stop"


def test_fake_llm_http_surface() -> None:
    llm = FakeLLM(_WORLDS)
    port = llm.start(0)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            models = client.get("/v1/models").json()
            assert models["data"][0]["id"] == SERVED_MODEL
            streamed = client.post("/v1/chat/completions", json={**_body(0), "stream": True})
            assert streamed.status_code == 400
            assert client.post("/_bench/active", json={"world_id": "T01", "trial": 1, "arm": "a",
                                                       "script": "model"}).status_code == 200
            assert client.post("/v1/chat/completions", json=_body(1)).json()["choices"][0][
                "finish_reason"] == "stop"
    finally:
        llm.stop()


# ── 종단 1회 ──


def _stat(path: Path) -> tuple[float, int] | None:
    return (path.stat().st_mtime, path.stat().st_size) if path.exists() else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture(scope="module")
def e2e_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """CLI `--run --test`를 실프로세스로 1회. 운영 감사 파일 상태를 전후로 잰다."""
    tmp = tmp_path_factory.mktemp("bench_e2e")
    spec = tmp / "fake_e2e.yaml"
    spec.write_text(_SPEC.read_text(encoding="utf-8").replace(_FIXED_PORTS, _ZERO_PORTS),
                    encoding="utf-8")
    out_root = tmp / "runs"
    before = _stat(_PROD_AUDIT)
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(_TOP / "scripts" / "inv_bench.py"), "--run", "--spec", str(spec),
         "--test", "--out-root", str(out_root)],
        cwd=_TOP, capture_output=True, text=True, timeout=_BUDGET_S + 30,
    )
    elapsed = time.monotonic() - started
    run_dirs = [p for p in out_root.iterdir() if p.is_dir()] if out_root.exists() else []
    return {"proc": proc, "elapsed": elapsed, "run_dirs": run_dirs,
            "audit_before": before, "audit_after": _stat(_PROD_AUDIT)}


def _run_dir(e2e: dict[str, Any]) -> Path:
    proc = e2e["proc"]
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (run_dir,) = e2e["run_dirs"]
    return Path(run_dir)


def test_e2e_completes_within_budget(e2e_run: dict[str, Any]) -> None:
    run_dir = _run_dir(e2e_run)
    assert e2e_run["elapsed"] < _BUDGET_S
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "finished" and manifest["backend"] == "fake"
    assert manifest["test_mode"] is True and manifest["planned_runs"] == 4
    assert manifest["invalid_count"] == 0 and manifest["excluded_arms"] == {}
    assert manifest["judge_version"] == judge.JUDGE_VERSION
    assert manifest["preflight"]["live"]["tool_call_roundtrip"]["ok"] is True
    for name in ("world_calls.jsonl", "raw.jsonl", "scores.jsonl", "report.md", "pids.json"):
        assert (run_dir / name).is_file(), name
    assert run_dir.name in (run_dir / "report.md").read_text(encoding="utf-8")


def test_e2e_raw_carries_w1_instrumentation(e2e_run: dict[str, Any]) -> None:
    run_dir = _run_dir(e2e_run)
    rows = runner.read_jsonl(run_dir / "raw.jsonl")
    assert sorted((r["arm"], r["world_id"]) for r in rows) == [
        ("control", "T01"), ("control", "T02"), ("control_b", "T01"), ("control_b", "T02")]
    for row in rows:
        assert row["status"] == "done" and row["infra_error"] is None
        assert row["fingerprint"] == f"{run_dir.name}:{row['arm']}:{row['world_id']}:1"
        audit = runner.read_jsonl(run_dir / f"arm-{row['arm']}" / "audit.jsonl")
        (done,) = [e for e in audit
                   if e.get("investigation_id") == row["investigation_id"] and e["event"] == "done"]
        for key in runner.INSTRUMENTATION_KEYS:
            assert row[key] == done[key], key  # 감사 done 이벤트(W1)와 같은 값
        assert row["model"] == "openai/fake-bench" and row["tool_calls_n"] >= 1
        assert [t["tool"] for t in row["tool_trace"]] == ["polestar_metric_trend"]
        assert row["tool_trace"][0]["phase"] == "investigation"


def test_e2e_scores_model_scripts_pass(e2e_run: dict[str, Any]) -> None:
    rows = runner.read_jsonl(_run_dir(e2e_run) / "scores.jsonl")
    assert len(rows) == 4
    assert {r["failure_code"] for r in rows} == {"PASS"}
    assert all(r["rc1"] for r in rows if r["world_id"] == "T01")
    assert all(r["abstained"] for r in rows if r["world_id"] == "T02")  # 무장애 세계 = 기권


def test_e2e_world_calls_are_tagged(e2e_run: dict[str, Any]) -> None:
    calls = runner.read_jsonl(_run_dir(e2e_run) / "world_calls.jsonl")
    tagged = [c for c in calls if c.get("world_id")]
    assert {(c["world_id"], c["arm"]) for c in tagged if c["tool"] == "polestar_metric_trend"} == {
        ("T01", "control"), ("T01", "control_b"), ("T02", "control"), ("T02", "control_b")}


def test_e2e_isolated_from_service(e2e_run: dict[str, Any]) -> None:
    """운영 감사 파일 불변 · 운영 포트 미사용 · 실험군 Gemini 키 공란 · 자기 프로세스 정리."""
    run_dir = _run_dir(e2e_run)
    assert e2e_run["audit_after"] == e2e_run["audit_before"]
    procs = json.loads((run_dir / "pids.json").read_text(encoding="utf-8"))["processes"]
    assert [p["name"] for p in procs] == [
        "fake_llm", "world_server", "arm-control", "arm-control_b"]
    assert not {p["port"] for p in procs} & _SERVICE_PORTS
    assert not any(_alive(p["pid"]) for p in procs)
    for arm in ("control", "control_b"):
        arm_dir = run_dir / f"arm-{arm}"
        echo = json.loads((arm_dir / "settings_echo.json").read_text(encoding="utf-8"))
        assert echo["secret_fields_set"]["gemini_api_key"] is False
        assert echo["api_base"].startswith("http://127.0.0.1:")
        assert echo["investigation_audit_path"] == str(arm_dir / "audit.jsonl")
        for url in (echo["api_base"], echo["polestar_mcp_url"], echo["apm_mcp_url"]):
            assert not any(f":{p}/" in url for p in _SERVICE_PORTS)


def test_e2e_export_bundle(e2e_run: dict[str, Any]) -> None:
    run_dir = _run_dir(e2e_run)
    bundle = export.export_bundle(run_dir)
    assert bundle.is_dir()
    assert (bundle / "scores.jsonl").is_file() and (bundle / "report.md").is_file()
    assert str(run_dir) not in (bundle / "manifest.json").read_text(encoding="utf-8")
