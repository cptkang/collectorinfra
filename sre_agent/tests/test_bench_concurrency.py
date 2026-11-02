"""plans/207 A0-a — 벤치 동시 조사 측정(`--concurrency`) · 가짜 지연 주입 · Windows 분기.

단위(실 LLM 0): K 해석·세계 고르기·그룹 · K 분기(미지정 = 종전 경로) · 가짜 LLM·세계 서버의 동시
세계 판별과 지연 · 배율·결과 비교·집계·힌트 · 그룹 실행(가짜 서비스) · Windows 프로세스 제어.
종단 1회(모듈 끝): 세계 서버 + 가짜 LLM + 실제 sre_agent로 K=1,2.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from investigation_bench import __main__ as cli
from investigation_bench import concurrency, export, preflight, runner, schema
from investigation_bench.fake_llm import FALLBACK_ANSWER, SERVED_MODEL, FakeLLM
from investigation_bench.world_server import WorldServer
from investigation_bench.worlds import World, load_worlds

_TOP = Path(__file__).resolve().parents[1]
_FIX = _TOP / "tests" / "fixtures" / "investigation_bench"
_DEV = _TOP / "testdata" / "investigation_bench" / "worlds" / "dev"
_SPEC = _TOP / "testdata" / "investigation_bench" / "specs" / "fake_e2e.yaml"
_FIXED_PORTS = "ports: {fake_llm: 19180, world: 19190, world_apm: 19191, arm_base: 19200}"
_ZERO_PORTS = "ports: {fake_llm: 0, world: 0, world_apm: 0, arm_base: 0}"
_WORLDS = load_worlds([_FIX / "worlds"])
_SNAPSHOT = schema.load_snapshot(_FIX / "tool_schemas.json")


def _world(wid: str, hosts: list[str]) -> World:
    return World(id=wid, data={"id": wid, "hosts": hosts, "event": {"hostname": hosts[0]}})


# ── K 해석 · 세계 고르기 · 그룹 ──


def test_parse_ks() -> None:
    assert concurrency.parse_ks("1,2,5") == (1, 2, 5)
    assert concurrency.parse_ks(" 5, 2,2 ") == (2, 5)
    for bad in ("0", "a", "", "1,-2"):
        with pytest.raises(ValueError):
            concurrency.parse_ks(bad)


def test_cli_concurrency_only_with_run(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--preflight", "--spec", str(_SPEC), "--concurrency", "2"])
    with pytest.raises(SystemExit):
        cli.main(["--run", "--spec", str(_SPEC), "--concurrency", "0"])
    assert cli.build_parser().parse_args(["--run", "--concurrency", "1,5"]).concurrency == (1, 5)
    assert cli.build_parser().parse_args(["--run"]).concurrency is None


def test_pick_disjoint_worlds_skips_overlaps() -> None:
    worlds = {"A": _world("A", ["h1", "h2"]), "B": _world("B", ["h2"]),
              "C": _world("C", ["h3"]), "D": _world("D", ["h4"])}
    assert concurrency.pick_disjoint_worlds(worlds, 3) == ["A", "C", "D"]
    with pytest.raises(runner.RunnerError):
        concurrency.pick_disjoint_worlds(worlds, 4)
    dev = load_worlds([_DEV])
    assert concurrency.pick_disjoint_worlds(dev, 5) == ["D01", "D02", "D03", "D04", "D05"]


def test_plan_groups_always_has_solo_baseline() -> None:
    groups = concurrency.plan_groups(["A", "B", "C"], (2, 3))
    assert groups == [{"k": 1, "worlds": ["A"]}, {"k": 1, "worlds": ["B"]},
                      {"k": 1, "worlds": ["C"]}, {"k": 2, "worlds": ["A", "B"]},
                      {"k": 3, "worlds": ["A", "B", "C"]}]


# ── K 분기(러너 흐름 — 프로세스·MCP는 가짜) ──


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"launched": [], "executed": [], "groups": [], "scored": 0}

    class _Popen:
        pid = 999999

        def poll(self) -> None:
            return None

    def launch(self: runner.ProcessGroup, name: str, cmd: list[str], *, cwd: Path,
               env: dict[str, str], port: int | None, log_path: Path) -> Any:
        state["launched"].append({"name": name, "cmd": cmd, "env": dict(env)})
        if name.startswith("arm-"):
            fields = ["model", "api_base"]
            if env.get("SRE_INVESTIGATION_METRICS_ENABLED") == "true":
                fields += ["investigation_max_concurrent", "sre_investigation_metrics_enabled"]
            (Path(cwd) / "settings_echo.json").write_text(json.dumps({
                "model": env["MODEL"], "api_base": env["API_BASE"],
                "secret_fields_set": {"gemini_api_key": False},
                "investigation_hourly_budget": int(env["INVESTIGATION_HOURLY_BUDGET"]),
                "non_default_fields": fields}))
        return _Popen()

    def execute(ctx: runner.RunContext, key: dict[str, Any], world: Any, url: str,
                audit_path: Path, script: str = "model") -> dict[str, Any]:
        state["executed"].append(runner.run_key(key))
        row = runner.empty_raw_row(key, world, "fp")
        row.update(status="done", wall_s=0.1)
        return row

    def group(ctx: runner.RunContext, *, arm: str, k: int, group: int, world_ids: list[str],
              **kw: Any) -> list[dict[str, Any]]:
        state["groups"].append((arm, k, list(world_ids)))
        return [{"arm": arm, "k": k, "group": group, "world_id": w, "wall_s": 1.0 * k,
                 "status": "done", "infra_error": None, "poll_latency_s": [0.01],
                 "metrics": None, "signature": {"status": "done"}} for w in world_ids]

    live = {"serving": {"models": ["fake-bench"], "max_model_len": 1}, "versions": {},
            "host": {"gpu": {"status": "미수집"}},
            "tool_call_roundtrip": {"ok": True, "tool": "bench_ping"},
            "schema": {"status": "미대조", "snapshot_hash": "h"}}
    monkeypatch.setattr(runner.ProcessGroup, "launch", launch)
    monkeypatch.setattr(runner, "wait_http", lambda *a, **k: None)
    monkeypatch.setattr(runner, "wait_arm", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(runner, "post_active", lambda *a, **k: {})
    monkeypatch.setattr(preflight, "live_checks", lambda **k: live)
    monkeypatch.setattr(runner, "execute_one", execute)
    monkeypatch.setattr(concurrency, "execute_group", group)
    monkeypatch.setattr(runner, "git_commit", lambda: ("c0ffee", False))

    def score(run_dir: Path, manifest: Any = None) -> int:
        state["scored"] += 1
        return 0

    monkeypatch.setattr(runner, "score", score)
    return state


def _spec(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "spec.yaml"
    text = _SPEC.read_text(encoding="utf-8").replace(_FIXED_PORTS, _ZERO_PORTS)
    path.write_text(text.replace("arms:\n  control: {}\n  control_b: {}\n",
                                 "arms:\n  control: {}\n") + extra, encoding="utf-8")
    return path


def test_without_concurrency_path_is_unchanged(tmp_path: Path, harness: dict[str, Any]) -> None:
    rc = runner.run(runner.RunOptions(spec_path=_spec(tmp_path), test=True,
                                      out_root=tmp_path / "out"))
    assert rc == 0 and harness["scored"] == 1 and harness["groups"] == []
    assert harness["executed"] == [("control", "T01", 1), ("control", "T02", 1)]
    by = {x["name"]: x for x in harness["launched"]}
    assert by["arm-control"]["env"]["INVESTIGATION_MAX_CONCURRENT"] == "1"
    assert "SRE_INVESTIGATION_METRICS_ENABLED" not in by["arm-control"]["env"]
    assert "--delay-s" not in by["fake_llm"]["cmd"]
    assert "--tool-delay-s" not in by["world_server"]["cmd"]
    (run_dir,) = (tmp_path / "out").iterdir()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "concurrency" not in manifest and manifest["planned_runs"] == 2
    assert not (run_dir / concurrency.CONCURRENCY_MD).exists()


def test_concurrency_branch_runs_groups_and_skips_judge(tmp_path: Path,
                                                        harness: dict[str, Any]) -> None:
    spec = _spec(tmp_path, "world: {tool_delay_s: 0.3}\n").read_text(encoding="utf-8")
    path = tmp_path / "spec2.yaml"
    path.write_text(spec.replace("  script: model", "  script: model\n  fake_delay_s: 1.5"),
                    encoding="utf-8")
    rc = runner.run(runner.RunOptions(spec_path=path, test=True, out_root=tmp_path / "out",
                                      concurrency=(2,)))
    assert rc == 0 and harness["scored"] == 0 and harness["executed"] == []
    assert harness["groups"] == [("control", 1, ["T01"]), ("control", 1, ["T02"]),
                                 ("control", 2, ["T01", "T02"])]
    by = {x["name"]: x for x in harness["launched"]}
    env = by["arm-control"]["env"]
    assert env["INVESTIGATION_MAX_CONCURRENT"] == "2"
    assert env["SRE_INVESTIGATION_METRICS_ENABLED"] == "true"
    fake_cmd, world_cmd = by["fake_llm"]["cmd"], by["world_server"]["cmd"]
    assert fake_cmd[fake_cmd.index("--delay-s") + 1] == "1.5"
    assert world_cmd[world_cmd.index("--tool-delay-s") + 1] == "0.3"
    (run_dir,) = (tmp_path / "out").iterdir()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["concurrency"]["ks"] == [1, 2] and manifest["planned_runs"] == 4
    assert manifest["concurrency"]["judge"] == "skipped"
    rows = runner.read_jsonl(run_dir / concurrency.CONCURRENCY_JSONL)
    assert [(r["k"], r["world_id"], r["ratio"]) for r in rows] == [
        (1, "T01", 1.0), (1, "T02", 1.0), (2, "T01", 2.0), (2, "T02", 2.0)]
    md = (run_dir / concurrency.CONCURRENCY_MD).read_text(encoding="utf-8")
    assert "품질 채점: **생략**" in md and "| control | 2 | 2 |" in md


def test_concurrency_rejects_resume(tmp_path: Path, harness: dict[str, Any]) -> None:
    rc = runner.run(runner.RunOptions(spec_path=_spec(tmp_path), test=True,
                                      resume_dir=tmp_path, concurrency=(2,)))
    assert rc == 2


# ── 가짜 LLM · 세계 서버: 동시 세계 판별 · 지연 ──


def _chat(host: str | None, n_assistant: int = 0) -> dict[str, Any]:
    user = f"알람 호스트 {host}" if host else "u"
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "s"},
                                  {"role": "user", "content": user}]
    for i in range(n_assistant):
        msgs += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"}]
    tools = [{"type": "function", "function": {"name": "polestar_metric_trend",
                                               "parameters": {}}}]
    return {"model": SERVED_MODEL, "messages": msgs, "tools": tools}


def test_fake_llm_routes_by_host_in_multi_mode(tmp_path: Path) -> None:
    llm = FakeLLM(_WORLDS, tmp_path / "fake.jsonl")
    llm.set_actives([{"world_id": "T01", "trial": 1, "arm": "a"},
                     {"world_id": "T02", "trial": 1, "arm": "a"}])
    t1 = llm.respond(_chat("bw-t01-app01"))["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(t1["function"]["arguments"])["server_name"] == "bw-t01-app01"
    t2 = llm.respond(_chat("BW-T02-WEB01"))["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(t2["function"]["arguments"])["server_name"] == "bw-t02-web01"
    lost = llm.respond(_chat(None))["choices"][0]
    assert lost["finish_reason"] == "stop" and lost["message"]["content"] == FALLBACK_ANSWER
    assert llm.respond(_chat("bw-t01-app01x"))["choices"][0]["finish_reason"] == "stop"
    logged = [json.loads(x) for x in (tmp_path / "fake.jsonl").read_text().splitlines()]
    assert [r["world_id"] for r in logged] == ["T01", "T02", None, None]
    assert llm.state()["worlds"][0]["world_id"] == "T01"
    llm.set_active("T01")  # 단일 지정은 동시 목록을 지운다
    assert llm.actives == {} and "worlds" not in llm.state()


def test_fake_llm_delay_does_not_serialize_requests() -> None:
    llm = FakeLLM(_WORLDS, delay_s=0.4)
    port = llm.start(0)
    try:
        times: list[float] = []

        def one() -> None:
            t0 = time.monotonic()
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as c:
                assert c.post("/v1/chat/completions", json=_chat(None)).status_code == 200
            times.append(time.monotonic() - t0)

        t0 = time.monotonic()
        threads = [threading.Thread(target=one) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        total = time.monotonic() - t0
        assert len(times) == 3 and min(times) >= 0.39
        assert total < 1.0, total  # 직렬이면 1.2s 이상
    finally:
        llm.stop()


def test_world_server_routes_calls_by_host(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    server = WorldServer(_SNAPSHOT, _WORLDS, log)
    server.set_actives([{"world_id": "T01", "trial": 3, "arm": "a"},
                        {"world_id": "T02", "trial": 3, "arm": "a"}])
    args = {"source": "polestar", "kind": "filesystem"}
    server.call("polestar", "polestar_metric_trend", {**args, "server_name": "bw-t01-app01"})
    server.call("polestar", "polestar_metric_trend", {**args, "server_name": "bw-t02-web01"})
    miss = server.call("polestar", "polestar_metric_trend", {**args, "server_name": "nohost"})
    server.call("polestar", "list_sources", {})
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert [(r["world_id"], r["trial"], r.get("route")) for r in rows] == [
        ("T01", 3, None), ("T02", 3, None), (None, None, "unknown_host"), (None, None, "ambiguous")]
    assert rows[0]["matcher"] == "polestar_metric_trend#0" and rows[2]["arg_error"] == "host"
    assert "대상 없음" in miss.content[0].text  # type: ignore[union-attr]
    # 활성 세계가 하나면 호스트 인자가 없어도 그 세계다.
    server.set_actives([{"world_id": "T01", "trial": 4, "arm": "a"}])
    assert server.route("list_sources", {}).world_id == "T01"  # type: ignore[union-attr]
    server.set_active("T02", 1)
    assert server.route("list_sources", {}) is None and server.actives == {}


def test_world_server_tool_delay_is_concurrent(tmp_path: Path) -> None:
    server = WorldServer(_SNAPSHOT, _WORLDS, tmp_path / "calls.jsonl", tool_delay_s=0.5)
    ports = server.start({"polestar": 0})
    url = f"http://127.0.0.1:{ports['polestar']}/sse"
    try:
        server.set_active("T01", 1)
        durations: list[float] = []

        def one() -> None:
            t0 = time.monotonic()
            asyncio_out = runner.mcp_call(url, "list_sources", {}, timeout=10)
            assert asyncio_out is not None
            durations.append(time.monotonic() - t0)

        t0 = time.monotonic()
        threads = [threading.Thread(target=one) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        assert len(durations) == 3 and min(durations) >= 0.5
        assert time.monotonic() - t0 < 1.4  # 직렬이면 1.5s 이상
    finally:
        server.stop()


# ── 배율 · 결과 비교 · 집계 · 힌트 ──


def _sig(evidence: list[list[Any]] | None = None, level: str = "high") -> dict[str, Any]:
    job = {"status": "done", "briefing": {
        "severity": {"level": level}, "summary": "LLM 서술(비교 제외)",
        "root_cause_hypotheses": [{"rank": 1, "confidence": "medium", "cause": "서술"}]}}
    return concurrency.result_signature(job, evidence or [["t", "t#0", None, "h"]])


def test_result_signature_ignores_narrative_and_compares_parts() -> None:
    a = _sig()
    b = concurrency.result_signature(
        {"status": "done", "briefing": {"severity": {"level": "high"}, "summary": "다른 서술",
                                        "root_cause_hypotheses": [{"rank": 1, "confidence":
                                                                   "medium", "cause": "딴말"}]}},
        [["t", "t#0", None, "h"]])
    assert concurrency.compare_signatures(a, b) == dict.fromkeys(concurrency.SIG_PARTS, True)
    c = _sig(evidence=[["t", "default", None, "x"]], level="low")
    assert concurrency.compare_signatures(a, c) == {
        "status": True, "severity": False, "hypotheses": True, "evidence": False}
    assert not any(concurrency.compare_signatures(None, a).values())
    calls = [{"tool": "b", "matcher": "m", "reply_hash": "2"}, {"tool": "a", "matcher": "m"}]
    assert concurrency.evidence_signature(calls) == concurrency.evidence_signature(calls[::-1])
    health = [{"tool": "list_sources", "matcher": "default"}, {"tool": "gateway_health"}]
    assert concurrency.evidence_signature(calls + health) == concurrency.evidence_signature(calls)


def test_cross_host_mentions_word_boundary() -> None:
    briefing = {"cause": "bw-t02-web01 과부하 · bw-t02-web011 무관"}
    assert concurrency.cross_host_mentions(briefing, ["bw-t02-web01"]) == 1
    assert concurrency.cross_host_mentions(briefing, []) == 0


def test_ratio_against_solo_and_summary() -> None:
    def row(k: int, group: int, wid: str, wall: float, **m: Any) -> dict[str, Any]:
        base = {"llm_s_total": 2.0, "tool_exec_s": 1.0, "slot_wait_s_total": 0.0,
                "llm_calls": 2, "llm_s_max": 1.0, "process_cpu_start": 0.0,
                "process_cpu_end": 0.5, "wall_start": 100.0, "wall_end": 100.0 + wall}
        return {"arm": "c", "k": k, "group": group, "world_id": wid, "wall_s": wall,
                "poll_latency_s": [0.01, 0.02, 0.03, 0.2], "signature": _sig(),
                "metrics": {**base, **m}, "infra_error": None}

    solos = [row(1, 1, "A", 4.0), row(1, 2, "B", 5.0)]
    axes = {"holmes_mcp": 3.0, "polestar": 1.0}
    multi = [row(2, 3, "A", 10.0, slot_wait_s_total=4.0, slot_wait_s=axes, process_cpu_end=9.5),
             row(2, 3, "B", 11.0, slot_wait_s_total=4.0, slot_wait_s=axes, process_cpu_end=9.5)]
    concurrency.compare_with_solo(solos, {r["world_id"]: r for r in solos})
    concurrency.compare_with_solo(multi, {r["world_id"]: r for r in solos})
    assert [r["ratio"] for r in multi] == [2.5, 2.2] and all(r["result_match"] for r in multi)
    s1, s2 = concurrency.summarize(solos + multi)
    assert (s1["k"], s1["ratio_max"], s2["k"], s2["ratio_max"]) == (1, 1.0, 2, 2.5)
    assert s2["slot_wait_s_sum"] == 8.0 and s2["match_n"] == 2
    assert s2["slot_wait_axes"] == {"holmes_mcp": 6.0, "polestar": 2.0}
    assert concurrency._axes_text(s2["slot_wait_axes"]) == " (holmes_mcp 6.00 · polestar 2.00)"
    assert s2["cpu_pct"] == 86.4  # (9.5 − 0) / (111 − 100) × 100
    assert (s2["status_p50_ms"], s2["status_p95_ms"]) == (20, 200)
    assert s2["extra_s_mean"] == 6.0 and s2["d_slot_s_mean"] == 4.0
    text = concurrency.hint(s2, fake_llm=True)
    assert text.startswith("합격(×3 이하)") and "R3(프로세스 CPU 86%" in text and "R2(" in text
    assert concurrency.hint(s1, fake_llm=True) == "기준(혼자)"


def test_hint_llm_growth_reads_r1_real_r3_fake() -> None:
    s = {"k": 5, "ratio_max": 4.0, "cpu_pct": 30.0, "extra_s_mean": 10.0,
         "d_llm_s_mean": 8.0, "d_tool_s_mean": 0.0, "d_slot_s_mean": 0.0}
    real = concurrency.hint(s, fake_llm=False)
    assert real == "불합격(×3 초과) · R1(LLM 구간 증가 = 증가분의 80%)"
    assert "R3 의심(가짜 LLM" in concurrency.hint(s, fake_llm=True)
    calm = {**s, "ratio_max": 1.1, "d_llm_s_mean": 0.0, "extra_s_mean": 0.1}
    assert concurrency.hint(calm, fake_llm=True) == "목표(×2 이하) · 감속 미미"


def test_percentile_and_cpu() -> None:
    assert concurrency.percentile([], 50) is None
    assert concurrency.percentile([3, 1, 2], 50) == 2
    assert concurrency.group_cpu_pct([{}]) is None


# ── 그룹 실행(가짜 서비스) ──


def test_execute_group_attributes_calls_and_metrics(tmp_path: Path) -> None:
    calls_log = tmp_path / "world_calls.jsonl"
    audit = tmp_path / "audit.jsonl"
    activations: list[Any] = []
    lock = threading.Lock()
    submitted: dict[str, str] = {}

    def call(url: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "sre_investigate_alarm":
            host = args["payload"]["event"]["hostname"]
            iid = f"i-{host}"
            with lock:
                submitted[iid] = host
                runner.append_jsonl(calls_log, {"world_id": "T01" if "t01" in host else "T02",
                                                "tool": "polestar_metric_trend",
                                                "matcher": "m", "reply_hash": host})
            return {"investigation_id": iid, "status": "accepted"}
        iid = args["investigation_id"]
        with lock:
            runner.append_jsonl(audit, {"event": concurrency.METRICS_EVENT,
                                        "investigation_id": iid, "llm_calls": 2})
        return {"status": "done", "elapsed_s": 1.0, "tool_calls_n": 1,
                "briefing": {"severity": {"level": "x"}, "cause": "bw-t02-web01"
                             if iid == "i-bw-t01-app01" else "정상"}}

    ctx = runner.RunContext(run_id="r", world_calls_path=calls_log, poll_interval_s=0.01,
                            per_run_timeout_s=5, call=call, activate=activations.append)
    rows = concurrency.execute_group(ctx, arm="a", k=2, group=7, world_ids=["T01", "T02"],
                                     worlds=_WORLDS, arm_url="u", audit_path=audit)
    assert activations[0] == {"worlds": [
        {"world_id": "T01", "trial": 7, "arm": "a", "script": "model"},
        {"world_id": "T02", "trial": 7, "arm": "a", "script": "model"}]}
    assert activations[-1]["world_id"] is None
    t1, t2 = rows
    assert t1["fingerprint"] == "r:a:T01:c2-g7" and t1["status"] == "done"
    assert t1["metrics"] == {"llm_calls": 2} and t1["world_calls_n"] == 1
    assert t1["signature"]["evidence"] == [["polestar_metric_trend", "m", None, "bw-t01-app01"]]
    assert t1["cross_host_mentions"] == 1 and t2["cross_host_mentions"] == 0
    assert t1["poll_latency_s"] and t1["briefing_hash"]


# ── Windows 분기(맥에서 IS_WINDOWS를 켜고 POSIX 전용 함수를 지운 상태) ──


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    monkeypatch.setattr(runner, "IS_WINDOWS", True)
    for name in ("getpgid", "killpg"):
        monkeypatch.delattr(os, name, raising=False)
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    zero_kills: list[Any] = []
    real_kill = os.kill

    def kill(pid: int, sig: int) -> None:
        if sig == 0:
            zero_kills.append(pid)  # Windows에서는 TerminateProcess가 된다 — 부르면 안 된다
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", kill)
    return zero_kills


def test_windows_process_group_stop_without_posix_calls(tmp_path: Path,
                                                        windows: list[Any]) -> None:
    group = runner.ProcessGroup(tmp_path / "pids.json")
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    a = group.launch("arm-a", cmd, cwd=tmp_path, env={}, port=None, log_path=tmp_path / "a.log")
    b = group.launch("arm-b", cmd, cwd=tmp_path, env={}, port=None, log_path=tmp_path / "b.log")
    group.stop("arm-a", grace=5)
    assert a.poll() is not None and b.poll() is None
    group.stop_all(grace=5)
    assert b.poll() is not None and windows == []


def test_windows_alive_cleanup_use_tasklist_taskkill(tmp_path: Path, windows: list[Any],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []
    alive = {4242}

    def run_out(cmd: list[str]) -> str:
        ran.append(cmd)
        if cmd[0] == "tasklist":
            pid = int(cmd[2].split()[-1])
            return f'"python.exe","{pid}","Console"' if pid in alive else "정보: 없음"
        if cmd[0] == "powershell":
            return "python -m investigation_bench._serve"
        if cmd[0] == "taskkill":
            alive.discard(int(cmd[2]))
        return ""

    monkeypatch.setattr(runner, "_run_out", run_out)
    assert runner._alive(4242) and not runner._alive(4243)
    assert runner._cmdline(4242) == "python -m investigation_bench._serve"
    runner.write_json(tmp_path / "pids.json", {"processes": [
        {"name": "arm-x", "pid": 4242, "port": 1, "started_at": "t"}]})
    assert runner.cleanup(tmp_path, grace=1) == [4242]
    assert ["taskkill", "/PID", "4242", "/T", "/F"] in ran and windows == []


# ── 종단 1회(가짜 LLM · 실프로세스) ──


@pytest.fixture(scope="module")
def conc_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("bench_conc")
    spec = tmp / "spec.yaml"
    text = _SPEC.read_text(encoding="utf-8").replace(_FIXED_PORTS, _ZERO_PORTS)
    text = text.replace("arms:\n  control: {}\n  control_b: {}\n", "arms:\n  control: {}\n")
    text = text.replace("  script: model", "  script: model\n  fake_delay_s: 0.3")
    spec.write_text(text + "world: {tool_delay_s: 0.1}\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_TOP / "scripts" / "inv_bench.py"), "--run", "--spec", str(spec),
         "--test", "--out-root", str(tmp / "runs"), "--concurrency", "2"],
        cwd=_TOP, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (run_dir,) = [p for p in (tmp / "runs").iterdir() if p.is_dir()]
    return run_dir


def test_e2e_concurrency_rows_metrics_and_match(conc_run: Path) -> None:
    rows = runner.read_jsonl(conc_run / concurrency.CONCURRENCY_JSONL)
    assert [(r["k"], r["world_id"]) for r in rows] == [(1, "T01"), (1, "T02"), (2, "T01"),
                                                       (2, "T02")]
    for r in rows:
        assert r["status"] == "done" and r["infra_error"] is None, r
        m = r["metrics"]
        assert m and m["llm_calls"] >= 2 and m["llm_s_total"] >= 0.6, m
        assert m["process_cpu_end"] >= m["process_cpu_start"]
        assert r["world_calls_n"] >= 1 and r["result_match"], r["match"]
        assert r["cross_host_mentions"] == 0
    assert all(r["ratio"] is not None for r in rows)
    md = (conc_run / concurrency.CONCURRENCY_MD).read_text(encoding="utf-8")
    assert "| control | 2 | 2 | 0 |" in md
    echo = json.loads((conc_run / "arm-control" / "settings_echo.json").read_text())
    assert echo["sre_investigation_metrics_enabled"] is True
    assert echo["investigation_max_concurrent"] == 2
    assert not (conc_run / "scores.jsonl").exists()


def test_e2e_export_bundle_carries_concurrency_files(conc_run: Path) -> None:
    bundle = export.export_bundle(conc_run)
    assert (bundle / concurrency.CONCURRENCY_JSONL).is_file()
    assert (bundle / concurrency.CONCURRENCY_MD).is_file()
