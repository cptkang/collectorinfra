"""plans/154 W3 — 러너 조립(§4.7 · §4.8 · §9.2 runner 행).

실 LLM 0 · sre_agent 프로세스 0. 실행 흐름은 프로세스 기동·MCP 호출을 바꿔 끼운 채로 검사하고,
PID 관리만 실제 무해한 `sleep` 프로세스로 검사한다(종단은 `test_bench_e2e_fake_llm.py`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from investigation_bench import preflight, runner
from investigation_bench.worlds import load_worlds

_TOP = Path(__file__).resolve().parents[1]
_FIX = _TOP / "tests" / "fixtures" / "investigation_bench"
_SPECS = _TOP / "testdata" / "investigation_bench" / "specs"
_FIXED_PORTS = "ports: {fake_llm: 19180, world: 19190, world_apm: 19191, arm_base: 19200}"
_ZERO_PORTS = "ports: {fake_llm: 0, world: 0, world_apm: 0, arm_base: 0}"


def _spec_file(tmp_path: Path) -> Path:
    path = tmp_path / "fake_e2e.yaml"
    text = (_SPECS / "fake_e2e.yaml").read_text(encoding="utf-8")
    path.write_text(text.replace(_FIXED_PORTS, _ZERO_PORTS), encoding="utf-8")
    return path


def _spec() -> dict[str, Any]:
    spec, _ = runner.load_spec(_SPECS / "fake_e2e.yaml")
    return spec


# ── 실험군 환경(§4.7 표) ──


def test_arm_env_has_every_table_key(tmp_path: Path) -> None:
    spec = _spec()
    spec["arms"]["h4"] = {"CITATION_VALUE_CHECK": True}
    spec["base_env"]["OVERRIDE_MAX_OUTPUT_TOKEN"] = 4096
    parent = {"PATH": "/usr/bin", "HOME": "/home/u", "GEMINI_API_KEY": "leak",
              "LLM_GEMINI_API_KEY": "leak2", "OPENAI_API_KEY": "leak3",
              "INVESTIGATION_HOURLY_BUDGET": "5", "SOME_SECRET": "x"}
    env = runner.build_arm_env(spec, "h4", run_dir=tmp_path, polestar_url="http://127.0.0.1:1/sse",
                               apm_url=None, api_base="http://127.0.0.1:2/v1", parent_env=parent)
    assert env["MODEL"] == "openai/fake-bench"
    assert env["API_BASE"] == "http://127.0.0.1:2/v1"
    assert env["API_KEY"] == "dummy"
    assert env["INVESTIGATION_LLM_ENABLED"] == "true"
    assert env["GEMINI_API_KEY"] == "" and env["LLM_GEMINI_API_KEY"] == ""
    assert env["POLESTAR_MCP_URL"] == "http://127.0.0.1:1/sse"
    assert env["APM_MCP_URL"] == ""  # APM 세계 없음 = 빈 값
    assert env["MAX_STEPS"] == "10" and env["INVESTIGATION_TIMEOUT_SECONDS"] == "60"
    assert env["OVERRIDE_MAX_OUTPUT_TOKEN"] == "4096"
    assert env["INVESTIGATION_MAX_CONCURRENT"] == "1"
    assert env["LITELLM_MODE"] == "PRODUCTION"  # litellm import 때 상위 .env 적재 끔
    assert env["INVESTIGATION_AUDIT_PATH"] == str(tmp_path / "arm-h4" / "audit.jsonl")
    assert env["CITATION_VALUE_CHECK"] == "true"  # 실험군 플래그
    assert "INVESTIGATION_DEDUP_TTL_SECONDS" not in env
    # 시간당 상한은 러너가 가드에 닿지 않는 값으로 고정(부모 "5"를 물려받지 않는다 · plans/194 W5)
    assert env["INVESTIGATION_HOURLY_BUDGET"] == str(runner.BENCH_HOURLY_BUDGET)
    # 부모에게서는 최소 키만 — Gemini·OpenAI 키·임의 비밀이 섞이지 않는다
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"
    assert "OPENAI_API_KEY" not in env and "SOME_SECRET" not in env
    assert set(env) - set(parent) >= {"MODEL", "API_BASE", "PYTHONPATH"}
    control = runner.build_arm_env(spec, "control", run_dir=tmp_path, polestar_url="p",
                                   apm_url="http://127.0.0.1:3/sse", api_base="a", parent_env={})
    assert "CITATION_VALUE_CHECK" not in control  # 대조군 = 현행 기본값
    assert control["APM_MCP_URL"] == "http://127.0.0.1:3/sse"


@pytest.mark.parametrize("key", ["GEMINI_API_KEY", "MODEL", "API_BASE", "INVESTIGATION_AUDIT_PATH",
                                 "INVESTIGATION_DEDUP_TTL_SECONDS", "INVESTIGATION_HOURLY_BUDGET"])
def test_spec_cannot_override_runner_keys(tmp_path: Path, key: str) -> None:
    for where in ("base_env", "arms"):
        spec = _spec()
        if where == "base_env":
            spec["base_env"][key] = "x"
        else:
            spec["arms"]["control"] = {key: "x"}
        with pytest.raises(runner.RunnerError) as exc:
            runner.build_arm_env(spec, "control", run_dir=tmp_path, polestar_url="p",
                                 apm_url=None, api_base="a", parent_env={})
        assert exc.value.exit_code == 2


def test_show_env_masks_secret_values(capsys: pytest.CaptureFixture[str]) -> None:
    assert runner.show_env(_SPECS / "fake_e2e.yaml") == 0
    out = capsys.readouterr().out
    assert "API_KEY=***" in out and "GEMINI_API_KEY=\n" in out and "LLM_GEMINI_API_KEY=\n" in out
    assert "dummy" not in out
    assert "MODEL=openai/fake-bench" in out


def test_arm_cwd_is_empty_dir(tmp_path: Path) -> None:
    arm_dir = runner.prepare_arm_dir(tmp_path, "control")
    assert arm_dir == tmp_path / "arm-control" and list(arm_dir.iterdir()) == []
    (arm_dir / ".env").write_text("GEMINI_API_KEY=x")
    with pytest.raises(runner.RunnerError):
        runner.prepare_arm_dir(tmp_path, "control")
    assert runner.prepare_arm_dir(tmp_path, "control", resume=True) == arm_dir


# ── 실행 계획 ──


def test_interleave_rotates_arms_per_world() -> None:
    plan = runner.plan_runs(["W1", "W2", "W3"], ["a", "b", "c"], 2)
    by_world = [[r["arm"] for r in plan[i:i + 3]] for i in range(0, len(plan), 3)]
    assert by_world == [["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"],
                        ["b", "c", "a"], ["c", "a", "b"], ["a", "b", "c"]]
    assert [r["trial"] for r in plan] == [1] * 9 + [2] * 9


def test_fingerprint_unique_per_run() -> None:
    plan = runner.plan_runs(["W1", "W2"], ["control", "control_b"], 3)
    fps = [runner.fingerprint("20261013-000000-local", r["arm"], r["world_id"], r["trial"])
           for r in plan]
    assert len(set(fps)) == len(fps) == 12
    assert fps[0] == "20261013-000000-local:control:W1:1"


# ── 실행 1건(§4.8) ──


_WORLDS = load_worlds([_FIX / "worlds"])
_KEY = {"world_id": "T01", "trial": 1, "arm": "control"}


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def _ctx(tmp_path: Path, calls: list[Any], replies: Iterator[Any], **kw: Any) -> runner.RunContext:
    clock = _Clock()

    def call(url: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, args))
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return dict(reply)

    return runner.RunContext(run_id="R", world_calls_path=tmp_path / "world_calls.jsonl",
                             poll_interval_s=1.0, per_run_timeout_s=5.0, call=call,
                             sleep=clock.sleep, clock=clock, audit_wait_s=0.0, **kw)


def test_poll_timeout_is_infra_error(tmp_path: Path) -> None:
    calls: list[Any] = []
    replies = iter([{"investigation_id": "i1", "status": "accepted"}]
                   + [{"status": "running"}] * 50)
    row = runner.execute_one(_ctx(tmp_path, calls, replies), _KEY, _WORLDS["T01"], "u",
                             tmp_path / "audit.jsonl")
    assert row["infra_error"].startswith("poll_timeout")
    assert row["status"] == "running"
    sub = calls[0]
    assert sub[0] == "sre_investigate_alarm" and sub[1]["wait_seconds"] == 0
    assert sub[1]["payload"]["decision"]["fingerprint"] == "R:control:T01:1"
    assert sub[1]["payload"]["contract_version"] == "1"
    assert sub[1]["payload"]["event"]["serverName"] == "bw-t01-app01"


@pytest.mark.parametrize("submit, terminal, prefix", [
    ({"investigation_id": "i1", "status": "rejected", "reason": "budget"}, None, "rejected"),
    ({"investigation_id": "i1", "status": "duplicate"}, None, "duplicate"),
    ({"investigation_id": "i1", "status": "accepted"},
     {"status": "stub", "verdict": "조사 미실행 — 스텁"}, "stub"),
    ({"investigation_id": "i1", "status": "accepted"},
     {"status": "rejected", "reason": "target_unavailable"}, "rejected"),
    (RuntimeError("연결 거부"), None, "exception:submit"),
])
def test_rejected_stub_exception_are_infra(tmp_path: Path, submit: Any, terminal: Any,
                                          prefix: str) -> None:
    replies = iter([submit] + ([terminal] if terminal else []))
    row = runner.execute_one(_ctx(tmp_path, [], replies), _KEY, _WORLDS["T01"], "u",
                             tmp_path / "audit.jsonl")
    assert row["infra_error"].startswith(prefix)


def test_done_row_collects_segment_audit_and_phase(tmp_path: Path) -> None:
    """구간의 호출 로그·감사 이벤트로 raw 1행 — 등록 헬스체크 제외 · 사전수집 phase · 계측 키."""
    wc = tmp_path / "world_calls.jsonl"
    runner.append_jsonl(wc, {"ts": "2026-10-13T00:00:00+00:00", "tool": "old", "args": {}})
    audit = tmp_path / "audit.jsonl"
    activations: list[Any] = []

    def activate(body: Any) -> None:
        activations.append(dict(body))
        if body.get("world_id"):  # 활성화 뒤 sre_agent가 부른 호출을 흉내
            for ts, tool, args in [
                ("2026-10-13T01:00:01+00:00", "polestar_incident_alarms", {"server_name": "h"}),
                ("2026-10-13T01:00:02+00:00", "list_sources", {}),
                ("2026-10-13T01:00:03+00:00", "polestar_metric_trend", {"kind": "filesystem"}),
                ("2026-10-13T01:00:04+00:00", "list_sources", {}),
            ]:
                runner.append_jsonl(wc, {"ts": ts, "tool": tool, "args": args, "matcher": "default",
                                         "arg_error": None, "reply_hash": "h"})
            prefetch_ts = runner._epoch("2026-10-13T01:00:01.500+00:00")
            for ev in ({"event": "accepted"}, {"event": "prefetch", "ts": prefetch_ts},
                       {"event": "done", "incomplete": False}):
                runner.append_jsonl(audit, {"ts": ev.get("ts", 0), "investigation_id": "i1", **ev})
            runner.append_jsonl(audit, {"ts": 0, "investigation_id": "other", "event": "done"})

    job = {"status": "done", "verdict": "v", "tokens": 12, "briefing": {"summary": "s"},
           "incomplete": False, "elapsed_s": 1.5, "prefetch_s": 0.2, "steps": 2,
           "tool_calls_n": 1, "model": "openai/fake-bench", "max_steps": 10,
           "tool_calls": [{"name": "polestar_metric_trend", "args_digest": "x",
                           "status": "success"}]}
    replies = iter([{"investigation_id": "i1", "status": "accepted"}, {"status": "running"}, job])
    ctx = _ctx(tmp_path, [], replies, activate=activate)
    row = runner.execute_one(ctx, _KEY, _WORLDS["T01"], "u", audit, "neg.F-DIST")
    assert activations[0] == {"world_id": "T01", "trial": 1, "arm": "control",
                              "script": "neg.F-DIST"}
    assert activations[-1]["world_id"] is None  # 구간 닫기
    assert row["infra_error"] is None and row["status"] == "done"
    assert row["briefing"] == {"summary": "s"} and row["tokens"] == 12
    for key in runner.INSTRUMENTATION_KEYS:
        assert row[key] == job[key]
    assert [e["event"] for e in row["audit"]] == ["accepted", "prefetch", "done"]
    trace = row["tool_trace"]
    assert [(t["seq"], t["tool"], t["phase"]) for t in trace] == [
        (1, "polestar_incident_alarms", "prefetch"),
        (2, "polestar_metric_trend", "investigation"),
        (3, "list_sources", "investigation"),  # 두 번째 헬스 도구 호출은 LLM 호출로 남긴다
    ]
    assert set(trace[0]) == {"seq", "phase", "tool", "args", "matcher", "arg_error", "reply_hash"}
    assert set(row) >= {"arm", "world_id", "split", "trial", "investigation_id", "fingerprint",
                        "submitted_at", "finished_at", "elapsed_s", "status", "incomplete",
                        "verdict", "tokens", "steps", "tool_calls_n", "briefing", "tool_trace",
                        "audit", "infra_error"}


# ── 실행 흐름(프로세스·MCP를 바꿔 끼움) ──


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """프로세스 기동·대기·실 조사를 가짜로 바꾼다. `no_echo`에 넣은 실험군은 에코를 쓰지 않는다."""
    state: dict[str, Any] = {"launched": [], "executed": [], "no_echo": set(), "results": {},
                             "stopped": [], "non_default": {}}

    class _Popen:
        pid = 999999

        def poll(self) -> None:
            return None

    def launch(self: runner.ProcessGroup, name: str, cmd: list[str], *, cwd: Path,
               env: dict[str, str], port: int | None, log_path: Path) -> Any:
        state["launched"].append({"name": name, "cmd": cmd, "cwd": Path(cwd), "env": dict(env),
                                  "cwd_empty": not any(Path(cwd).iterdir())})
        if name.startswith("arm-") and name[4:] not in state["no_echo"]:
            (Path(cwd) / "settings_echo.json").write_text(json.dumps({
                "model": env["MODEL"], "api_base": env["API_BASE"],
                "secret_fields_set": {"gemini_api_key": False},
                "investigation_hourly_budget": int(env["INVESTIGATION_HOURLY_BUDGET"]),
                "non_default_fields": state["non_default"].get(name[4:], ["model", "api_base"])}))
        return _Popen()

    def execute(ctx: runner.RunContext, key: dict[str, Any], world: Any, url: str,
                audit_path: Path, script: str = "model") -> dict[str, Any]:
        state["executed"].append(runner.run_key(key))
        row = runner.empty_raw_row(key, world, runner.fingerprint(ctx.run_id, key["arm"],
                                                                  key["world_id"], key["trial"]))
        row.update(status="done", wall_s=0.1)
        row.update(state["results"].get(runner.run_key(key), {}))
        return row

    live = {"serving": {"models": ["fake-bench"], "max_model_len": 1}, "versions": {},
            "host": {"gpu": {"status": "미수집"}},
            "tool_call_roundtrip": {"ok": True, "tool": "bench_ping"},
            "schema": {"status": "미대조", "snapshot_hash": "h"}}
    monkeypatch.setattr(runner.ProcessGroup, "launch", launch)
    monkeypatch.setattr(runner.ProcessGroup, "stop",
                        lambda self, name, grace=10.0: state["stopped"].append(name))
    monkeypatch.setattr(runner, "wait_http", lambda *a, **k: None)
    monkeypatch.setattr(runner, "wait_arm", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(preflight, "live_checks", lambda **k: live)
    monkeypatch.setattr(runner, "execute_one", execute)
    monkeypatch.setattr(runner, "score", lambda run_dir, manifest=None: 0)
    monkeypatch.setattr(runner, "git_commit", lambda: ("c0ffee", False))
    return state


def _only_run_dir(root: Path) -> Path:
    (run_dir,) = [p for p in root.iterdir() if p.is_dir()]
    return run_dir


def test_run_flow_cwd_env_and_order(tmp_path: Path, harness: dict[str, Any]) -> None:
    rc = runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), test=True,
                                      out_root=tmp_path / "out"))
    assert rc == 0
    names = [x["name"] for x in harness["launched"]]
    assert names == ["fake_llm", "world_server", "arm-control", "arm-control_b"]  # 세계 서버가 먼저
    run_dir = _only_run_dir(tmp_path / "out")
    for x in harness["launched"][2:]:
        assert x["cwd"] == run_dir / f"arm-{x['name'][4:]}" and x["cwd_empty"]
        assert x["env"]["GEMINI_API_KEY"] == "" and x["env"]["LLM_GEMINI_API_KEY"] == ""
        assert x["cmd"][1:3] == ["-m", "investigation_bench._serve"]
    assert harness["executed"] == [("control", "T01", 1), ("control_b", "T01", 1),
                                   ("control_b", "T02", 1), ("control", "T02", 1)]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "finished" and manifest["backend"] == "fake"
    assert manifest["commit"] == "c0ffee" and manifest["planned_runs"] == 4
    assert set(manifest["worlds"]) == {"T01", "T02"} and manifest["snapshot_hash"]
    assert manifest["arms"]["control"]["settings_echo"]["model"] == "openai/fake-bench"
    assert manifest["spec"]["llm"]["backend"] == "fake" and manifest["spec_text"]
    assert run_dir.name.endswith("-local") and manifest["run_id"] == run_dir.name


@pytest.mark.parametrize("result", [
    {"status": "timeout", "incomplete": True},  # sre_agent 조사 시간 초과
    {"status": "running", "infra_error": "poll_timeout: 90.0s 안에 종결되지 않음"},  # 폴링 초과
])
def test_timeout_restarts_only_that_arm(tmp_path: Path, harness: dict[str, Any],
                                        result: dict[str, Any]) -> None:
    """시간 초과로 끝난 실행 뒤 그 실험군만 재기동 — 다른 실험군·세계 서버·가짜 LLM은 그대로."""
    harness["results"][("control_b", "T01", 1)] = result
    assert runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), test=True,
                                        out_root=tmp_path / "out")) == 0
    names = [x["name"] for x in harness["launched"]]
    assert names == ["fake_llm", "world_server", "arm-control", "arm-control_b", "arm-control_b"]
    assert harness["stopped"] == ["arm-control_b"]
    again = harness["launched"][-1]
    assert again["cmd"] == harness["launched"][3]["cmd"]  # 같은 포트
    first = harness["launched"][3]
    assert again["cwd"] == first["cwd"] and again["env"] == first["env"]
    assert harness["executed"] == [("control", "T01", 1), ("control_b", "T01", 1),
                                   ("control_b", "T02", 1), ("control", "T02", 1)]
    run_dir = _only_run_dir(tmp_path / "out")
    rows = {runner.run_key(r): r for r in runner.read_jsonl(run_dir / "raw.jsonl")}
    assert rows[("control_b", "T01", 1)]["arm_restarted_after"] is True
    assert not any(r.get("arm_restarted_after") for k, r in rows.items()
                   if k != ("control_b", "T01", 1))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["arms"]["control_b"]["restarts"] == 1
    assert len(manifest["arms"]["control_b"]["restart_s"]) == 1
    assert manifest["arms"]["control"]["restarts"] == 0
    assert manifest["arms"]["control_b"]["settings_echo"]["model"] == "openai/fake-bench"


def test_done_or_failed_does_not_restart(tmp_path: Path, harness: dict[str, Any]) -> None:
    harness["results"][("control", "T01", 1)] = {"status": "failed"}
    harness["results"][("control_b", "T01", 1)] = {"status": "rejected",
                                                   "infra_error": "rejected: x"}
    assert runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), test=True,
                                        out_root=tmp_path / "out")) == 0
    assert harness["stopped"] == [] and len(harness["launched"]) == 4


def test_manifest_spec_secrets_redacted(tmp_path: Path, harness: dict[str, Any]) -> None:
    """명세 비밀 칸(api_key 등) 값은 manifest `spec`·`spec_text`에 싣지 않는다."""
    secret = "sk-vllm-Zq8w3Lr0bench"
    spec_path = _spec_file(tmp_path)
    spec_path.write_text(spec_path.read_text(encoding="utf-8").replace(
        "api_key: dummy", f"api_key: {secret}"), encoding="utf-8")
    assert runner.run(runner.RunOptions(spec_path=spec_path, test=True,
                                        out_root=tmp_path / "out")) == 0
    run_dir = _only_run_dir(tmp_path / "out")
    text = (run_dir / "manifest.json").read_text(encoding="utf-8")
    assert secret not in text
    manifest = json.loads(text)
    assert manifest["spec"]["llm"]["api_key"] == "<set>"
    assert "api_key: <set>" in manifest["spec_text"]
    assert manifest["spec_hash"] == runner.spec_hash(spec_path.read_text(encoding="utf-8"))
    assert harness["launched"][2]["env"]["API_KEY"] == secret  # 실험군에는 원래 값


def test_redact_spec_text_flow_block_and_comma() -> None:
    text = ("llm: {api_key: \"sk-1\", model: x}\nbase_env: {POLESTAR_MCP_TOKEN: abc}\n"
            "x:\n  api_key:   # 비어 있음\n  other_key: ab,cd  # c\n")
    spec = {"llm": {"api_key": "sk-1"}, "base_env": {"POLESTAR_MCP_TOKEN": "abc"},
            "x": {"api_key": None, "other_key": "ab,cd"}}
    out = runner.redact_spec_text(text, spec)
    assert "sk-1" not in out and "abc" not in out and "cd" not in out
    assert "api_key: <unset>" in out and "model: x" in out and "# 비어 있음" in out
    assert runner.redact_spec(spec)["x"] == {"api_key": "<unset>", "other_key": "<set>"}


@pytest.mark.parametrize("extra", [["investigation_dedup_ttl_seconds"], ["severity_judge_enabled"]])
def test_echo_with_outside_settings_aborts(tmp_path: Path, harness: dict[str, Any],
                                           extra: list[str]) -> None:
    """에코의 기본값과 다른 필드가 러너·명세 밖이면(.env 유입 등) 실행 전에 멈춘다."""
    harness["non_default"]["control_b"] = ["model", "api_base", *extra]
    rc = runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), test=True,
                                      out_root=tmp_path / "out"))
    assert rc == 2 and harness["executed"] == []


def test_verify_echo_rules() -> None:
    base = {"model": "m", "api_base": "a", "secret_fields_set": {"gemini_api_key": False},
            "investigation_hourly_budget": runner.BENCH_HOURLY_BUDGET,
            "investigation_dedup_ttl_seconds": None,
            "non_default_fields": ["api_base", "max_steps", "model"]}
    assert runner.verify_echo(base, model="m", api_base="a", allowed_fields={"max_steps"}) == []
    assert runner.verify_echo(base, model="m", api_base="a", allowed_fields=set())  # max_steps 밖
    assert runner.verify_echo({**base, "investigation_dedup_ttl_seconds": 60}, model="m",
                              api_base="a", allowed_fields={"max_steps"})
    # 시간당 상한이 러너 값이 아니면(코드 기본 20 · .env 값 · 가드 off None) 멈춘다(plans/194 W5)
    for budget in (20, None, 5):
        assert runner.verify_echo({**base, "investigation_hourly_budget": budget}, model="m",
                                  api_base="a", allowed_fields={"max_steps"})
    no_list = {k: v for k, v in base.items() if k != "non_default_fields"}
    assert runner.verify_echo(no_list, model="m", api_base="a", allowed_fields={"max_steps"})
    assert runner.spec_fields({"base_env": {"MAX_STEPS": 3}, "arms": {"h": {"X_Y": 1}}},
                              "h") == {"max_steps", "x_y"}


def test_arm_without_echo_is_excluded(tmp_path: Path, harness: dict[str, Any]) -> None:
    harness["no_echo"].add("control_b")
    assert runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), test=True,
                                        out_root=tmp_path / "out")) == 0
    run_dir = _only_run_dir(tmp_path / "out")
    rows = runner.read_jsonl(run_dir / "raw.jsonl")
    excluded = [r for r in rows if r["arm"] == "control_b"]
    assert len(excluded) == 2
    assert all(r["excluded"] == r["infra_error"] == runner.ECHO_MISSING for r in excluded)
    assert all(a == "control" for a, _, _ in harness["executed"])  # 제외 실험군은 돌리지 않는다
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["excluded_arms"] == {"control_b": runner.ECHO_MISSING}
    assert manifest["invalid_count"] == 2


def test_echo_plane_mismatch_aborts(tmp_path: Path, harness: dict[str, Any],
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """실효 설정 에코가 사전 점검 판정과 다르면(예: Gemini 키가 들어감) 실행 전에 멈춘다."""
    monkeypatch.setattr(runner, "read_echo", lambda d: {
        "model": "openai/fake-bench", "api_base": "x",
        "secret_fields_set": {"gemini_api_key": True}})
    rc = runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), test=True,
                                      out_root=tmp_path / "out"))
    assert rc == 2 and harness["executed"] == []


def test_resume_skips_finished_and_rejects_changes(tmp_path: Path, harness: dict[str, Any],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    spec_path = _spec_file(tmp_path)
    assert runner.run(runner.RunOptions(spec_path=spec_path, test=True,
                                        out_root=tmp_path / "out")) == 0
    run_dir = _only_run_dir(tmp_path / "out")
    raw = run_dir / "raw.jsonl"
    lines = raw.read_text(encoding="utf-8").splitlines()
    raw.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")  # 앞의 2건만 끝났다고 친다
    harness["executed"].clear()
    assert runner.run(runner.RunOptions(spec_path=None, test=True, resume_dir=run_dir)) == 0
    assert harness["executed"] == [("control_b", "T02", 1), ("control", "T02", 1)]
    assert len(runner.read_jsonl(raw)) == 4
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["resumed_at"]) == 1

    harness["executed"].clear()
    harness["launched"].clear()
    assert runner.run(runner.RunOptions(spec_path=None, test=True, resume_dir=run_dir)) == 0
    assert harness["launched"] == [] and harness["executed"] == []  # 남은 실행 0 = 프로세스 0

    monkeypatch.setattr(runner, "git_commit", lambda: ("deadbeef", False))
    assert runner.run(runner.RunOptions(spec_path=None, test=True, resume_dir=run_dir)) == 2
    monkeypatch.setattr(runner, "git_commit", lambda: ("c0ffee", False))
    spec_path.write_text(spec_path.read_text(encoding="utf-8").replace("k: 1", "k: 2"),
                         encoding="utf-8")
    assert runner.run(runner.RunOptions(spec_path=None, test=True, resume_dir=run_dir)) == 2
    assert harness["executed"] == []


def test_site_mismatch_rejected(tmp_path: Path, harness: dict[str, Any]) -> None:
    rc = runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), cli_site="closed",
                                      test=True, out_root=tmp_path / "out"))
    assert rc == 2
    assert not (tmp_path / "out").exists() and harness["launched"] == []


def test_dry_run_starts_no_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    def no_popen(*_: Any, **__: Any) -> Any:
        raise AssertionError("--dry-run은 프로세스를 띄우지 않는다")

    monkeypatch.setattr(runner, "git_commit", lambda: ("c0ffee", False))  # git 조회는 허용 대상 밖
    monkeypatch.setattr(subprocess, "Popen", no_popen)
    monkeypatch.setattr(runner.ProcessGroup, "launch", no_popen)
    rc = runner.run(runner.RunOptions(spec_path=_spec_file(tmp_path), test=True, dry_run=True,
                                      out_root=tmp_path / "out"))
    assert rc == 0 and not (tmp_path / "out").exists()
    out = capsys.readouterr().out
    assert "세계 2 × 실험군 2 × k 1 = 4회" in out and "예상 LLM 호출 상한: 40" in out


# ── PID 관리 ──


def _sleeper(marker: bool = True) -> subprocess.Popen[bytes]:
    args = [sys.executable, "-c", "import time; time.sleep(60)"]
    return subprocess.Popen(args + (["investigation_bench"] if marker else []))


def test_stop_all_kills_only_own_processes(tmp_path: Path) -> None:
    group = runner.ProcessGroup(tmp_path / "pids.json")
    outsider = _sleeper()
    try:
        cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
        own = [group.launch(f"p{i}", cmd, cwd=tmp_path, env={}, port=None,
                            log_path=tmp_path / f"p{i}.log") for i in range(2)]
        pids = json.loads((tmp_path / "pids.json").read_text())["processes"]
        assert [p["pid"] for p in pids] == [p.pid for p in own]
        assert all(set(p) >= {"pid", "port", "started_at"} for p in pids)
        group.stop_all(grace=5)
        assert all(p.poll() is not None for p in own)
        assert outsider.poll() is None
    finally:
        outsider.kill()
        outsider.wait()


def test_stop_one_kills_only_named_process(tmp_path: Path) -> None:
    """실험군 재기동 — 그 이름의 자기 프로세스만 내리고 pids.json에서 뺀다."""
    group = runner.ProcessGroup(tmp_path / "pids.json")
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    a, b = (group.launch(n, cmd, cwd=tmp_path, env={}, port=None, log_path=tmp_path / f"{n}.log")
            for n in ("arm-a", "arm-b"))
    try:
        group.stop("arm-a", grace=5)
        assert a.poll() is not None and b.poll() is None
        pids = json.loads((tmp_path / "pids.json").read_text())["processes"]
        assert [p["name"] for p in pids] == ["arm-b"]
    finally:
        group.stop_all(grace=5)
    assert b.poll() is not None


def test_stop_kills_whole_process_group(tmp_path: Path) -> None:
    """`start_new_session`으로 띄운 프로세스의 자식(예: uvicorn 하위)까지 그룹째 내린다."""
    child_pid = tmp_path / "child.pid"
    code = ("import subprocess, sys, time, pathlib; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"pathlib.Path({str(child_pid)!r}).write_text(str(c.pid)); time.sleep(60)")
    group = runner.ProcessGroup(tmp_path / "pids.json")
    parent = group.launch("arm-x", [sys.executable, "-c", code], cwd=tmp_path, env={},
                          port=None, log_path=tmp_path / "x.log")
    deadline = time.monotonic() + 10
    while not child_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(child_pid.read_text())
    group.stop("arm-x", grace=5)
    assert parent.poll() is not None
    deadline = time.monotonic() + 5
    while runner._alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not runner._alive(pid)


def test_cleanup_kills_only_pids_json_entries(tmp_path: Path) -> None:
    listed = _sleeper()
    # PID 재사용 방지 — 명령줄 표지가 없으면 안 죽인다
    listed_without_marker = _sleeper(marker=False)
    outsider = _sleeper()
    try:
        runner.write_json(tmp_path / "pids.json", {"processes": [
            {"name": "world_server", "pid": listed.pid, "port": 1, "started_at": "t"},
            {"name": "arm-x", "pid": listed_without_marker.pid, "port": 2, "started_at": "t"},
        ]})
        assert runner.cleanup(tmp_path) == [listed.pid]
        listed.wait(timeout=10)
        time.sleep(0.2)
        assert listed_without_marker.poll() is None and outsider.poll() is None
    finally:
        for p in (listed, listed_without_marker, outsider):
            if p.poll() is None:
                p.kill()
            p.wait()


def test_gpu_sample_shape_matches_report() -> None:
    """GPU 샘플 행 = report가 읽는 모양(`gpus[].util` · `procs_n`)."""
    def fake_smi(cmd: list[str], **_: Any) -> Any:
        gpus = "0, 87, 30000, 81920\n1, [N/A], 10, 81920\n"
        out = gpus if "--query-gpu" in cmd[1] else "11\n12\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out)

    row = runner.GpuSampler(Path("unused"), 1.0, run=fake_smi).sample()
    assert row["procs_n"] == 2 and row["ts"]
    assert row["gpus"][0] == {"index": 0.0, "util": 87.0, "mem_used_mb": 30000.0,
                              "mem_total_mb": 81920.0}
    assert row["gpus"][1]["util"] == "[N/A]"


# ── 떠돌이 호출(방어 2선) ──


def test_stray_calls_from_other_world_are_dropped(tmp_path: Path) -> None:
    """앞 실행의 버려진 조사가 다른 세계 호스트로 부른 호출 — tool_trace에서 빼고 개수를 남긴다."""
    wc = tmp_path / "world_calls.jsonl"
    worlds = {"T01": _WORLDS["T01"], "T02": _WORLDS["T02"]}

    def activate(body: Any) -> None:
        if not body.get("world_id"):
            return
        for tool, args in [
            ("polestar_alarm_history", {"server_name": "BW-T01-APP01"}),  # 다른 세계(T01) — 떠돌이
            ("apm_app_health", {"targets": [{"hostname": "bw-t01-db01"}]}),  # targets[]도 본다
            ("polestar_metric_trend", {"server_name": "bw-t02-web01", "kind": "memory"}),
            ("polestar_metric_trend", {"kind": "memory"}),  # 호스트 인자 없음 = 판별 불가 → 유지
            ("polestar_metric_trend", {"server_name": "unknown-host"}),  # 어느 세계도 아님 → 유지
        ]:
            runner.append_jsonl(wc, {"ts": "2026-10-13T01:00:00+00:00", "tool": tool, "args": args,
                                     "matcher": "default", "arg_error": None, "reply_hash": "h"})

    replies = iter([{"investigation_id": "i2", "status": "accepted"}, {"status": "done"}])
    ctx = _ctx(tmp_path, [], replies, activate=activate,
               world_hosts={w: runner.world_host_set(x) for w, x in worlds.items()})
    key = {"world_id": "T02", "trial": 1, "arm": "control"}
    row = runner.execute_one(ctx, key, _WORLDS["T02"], "u", tmp_path / "audit.jsonl")
    assert [(t["tool"], t["args"]) for t in row["tool_trace"]] == [
        ("polestar_metric_trend", {"server_name": "bw-t02-web01", "kind": "memory"}),
        ("polestar_metric_trend", {"kind": "memory"}),
        ("polestar_metric_trend", {"server_name": "unknown-host"}),
    ]
    assert [t["seq"] for t in row["tool_trace"]] == [1, 2, 3]
    assert row["stray_calls_n"] == 2
    assert all(c["stray"] is True for c in row["stray_calls"])
    assert [c["tool"] for c in row["stray_calls"]] == ["polestar_alarm_history", "apm_app_health"]
    assert len(runner.read_jsonl(wc)) == 5  # 원본 호출 로그는 그대로


def test_no_world_hosts_means_no_stray_check(tmp_path: Path) -> None:
    trace, _, strays = runner.build_tool_trace(
        [{"tool": "t", "args": {"hostname": "bw-t01-app01"}}], [], set(), ())
    assert len(trace) == 1 and strays == []
