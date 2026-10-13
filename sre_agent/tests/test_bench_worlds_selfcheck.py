"""plans/154 W4 — 세계 자기검증(L4 · §9.1 · §9.2).

세계 24건 각각: 모범 대본(`script.model`) → PASS · 음성 대본(`script.neg.<코드>`) → 그 코드.
L3 장치(세계 서버 + 가짜 LLM + 실 sre_agent 프로세스)를 재사용하되 sre_agent는 **모듈당 1개**만
띄우고 세계·대본만 바꿔 돈다. 세계 서버·가짜 LLM은 테스트 프로세스 안 스레드(포트 0)다. 실 LLM 0.

세계 하나가 PASS를 못 내면 세계(또는 대본)를 고친다 — 채점기를 세계에 맞춰 고치지 않는다.
"""

from __future__ import annotations

import itertools
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from investigation_bench import judge, lint, runner, schema
from investigation_bench.fake_llm import FakeLLM
from investigation_bench.world_server import WorldServer
from investigation_bench.worlds import World, load_worlds

_TOP = Path(__file__).resolve().parents[1]
_SPEC = _TOP / "testdata" / "investigation_bench" / "specs" / "selfcheck_fake.yaml"
_WORLDS: dict[str, World] = load_worlds([lint.DEFAULT_WORLDS_ROOT])
_ARM = "control"
_NEG_CODES = ("F-ARG", "F-LOOP", "F-MISS", "F-READ", "F-DIST", "F-HALLU", "F-CITE", "F-ABST",
              "F-INC")


def _cases() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for wid, world in sorted(_WORLDS.items()):
        out.append((wid, "model", "PASS"))
        for code in sorted((world.data.get("script") or {}).get("neg") or {}):
            out.append((wid, f"neg.{code}", code))
    return out


_CASES = _cases()


# ── 정적 ──


def test_lint_passes_all_worlds() -> None:
    report = lint.lint()
    assert report.ok, "\n".join(str(f) for f in report.errors)
    assert not report.warnings, "\n".join(str(f) for f in report.warnings)


def test_world_set_shape() -> None:
    """개발 16 · 홀드아웃 8(D-324 ④) · 홀드아웃·개발 문구 중복 0 · 음성 대본이 9개 코드를 덮는다."""
    splits = [w.split for w in _WORLDS.values()]
    assert splits.count("dev") == 16 and splits.count("holdout") == 8
    dev = [w for w in _WORLDS.values() if w.split == "dev"]
    for h in (w for w in _WORLDS.values() if w.split == "holdout"):
        for d in dev:
            assert lint.duplicate_phrases(h, d) == [], (h.id, d.id)
    covered = {code for _, script, code in _CASES if script.startswith("neg.")}
    assert set(_NEG_CODES) <= covered, sorted(set(_NEG_CODES) - covered)


# ── 실행 장치(모듈당 sre_agent 1개) ──


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("worlds_selfcheck")
    spec, _ = runner.load_spec(_SPEC)
    run_cfg = spec["run"]
    calls = tmp / "world_calls.jsonl"
    world = WorldServer(schema.load_snapshot(schema.DEFAULT_SNAPSHOT_PATH), _WORLDS, calls)
    llm = FakeLLM(_WORLDS, tmp / "fake_llm.jsonl")
    procs = runner.ProcessGroup(tmp / "pids.json")
    try:
        ports = world.start({"polestar": 0, "apm": 0})
        api_base = f"http://127.0.0.1:{llm.start(0)}/v1"
        arm_dir = runner.prepare_arm_dir(tmp, _ARM)
        env = runner.build_arm_env(
            spec, _ARM, run_dir=tmp, polestar_url=f"http://127.0.0.1:{ports['polestar']}/sse",
            apm_url=f"http://127.0.0.1:{ports['apm']}/sse", api_base=api_base)
        port = runner.free_port()
        cmd = [sys.executable, "-m", "investigation_bench._serve", "--port", str(port)]
        popen = procs.launch(f"arm-{_ARM}", cmd, cwd=arm_dir, env=env, port=port,
                             log_path=arm_dir / "stderr.log")
        url = f"http://127.0.0.1:{port}/sse"
        runner.wait_arm(url, popen)

        def activate(body: dict[str, Any]) -> None:
            world.set_active(body.get("world_id"), body.get("trial"), body.get("arm"))
            llm.set_active(body.get("world_id"), body.get("trial"), body.get("arm"),
                           body.get("script"))

        ctx = runner.RunContext(
            run_id="selfcheck", world_calls_path=calls,
            poll_interval_s=float(run_cfg["poll_interval_s"]),
            per_run_timeout_s=float(run_cfg["per_run_timeout_s"]), activate=activate)
        yield {"ctx": ctx, "url": url, "audit": arm_dir / "audit.jsonl", "calls": calls,
               "trials": itertools.count(1), "stderr": arm_dir / "stderr.log"}
    finally:
        procs.stop_all()
        world.stop()
        llm.stop()


def _diagnose(raw: dict[str, Any], score: dict[str, Any]) -> str:
    briefing = raw.get("briefing") or {}
    trace = [(t["tool"], t["args"], t["arg_error"]) for t in raw.get("tool_trace") or []]
    return (f"code={score['failure_code']} status={raw.get('status')} "
            f"incomplete={raw.get('incomplete')} infra={raw.get('infra_error')}\n"
            f"notes={score.get('notes')}\ntop1={score.get('top1')}\n"
            f"ecr={score.get('ecr')} hallu={score.get('hallu')} "
            f"abstained={score.get('abstained')}\n"
            f"cause={briefing.get('cause')!r}\n"
            f"trace={trace}")


@pytest.mark.parametrize(("world_id", "script", "expected"), _CASES,
                         ids=[f"{w}-{s}" for w, s, _ in _CASES])
def test_script_scores_expected_code(bench: dict[str, Any], world_id: str, script: str,
                                     expected: str) -> None:
    world = _WORLDS[world_id]
    key = {"world_id": world_id, "trial": next(bench["trials"]), "arm": _ARM}
    raw = runner.execute_one(bench["ctx"], key, world, bench["url"], bench["audit"], script)
    score = judge.score_one(raw, world, judge.reply_index(runner.read_jsonl(bench["calls"])))
    assert score["failure_code"] == expected, _diagnose(raw, score)
