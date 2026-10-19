"""plans/187 W7 — 플레이북 모드 vs HolmesGPT 단독 평가 하네스.

- 엔진 비교표(`engine_compare`) 단위 — 합성 raw·채점 행 · 가짜 LLM 요청 로그.
- 명세 `plan187_w7_fake.yaml`의 실험군 오버라이드가 러너 env·에코 허용 필드로 이어지는지.
- 플레이북 픽스처 평가(`playbook_fixture_eval`) — 실 dispatcher + W0 녹화 응답 · 실 LLM·MCP 0.
- 종단 1회 — 가짜 LLM · 세계 T01 × (holmes · playbook_off) · 실프로세스.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from investigation_bench import engine_compare, playbook_fixture_eval, runner
from investigation_bench.__main__ import build_parser

_TOP = Path(__file__).resolve().parents[1]
_SPEC = _TOP / "testdata" / "investigation_bench" / "specs" / "plan187_w7_fake.yaml"
_FIX = _TOP / "tests" / "fixtures" / "investigation_bench"
_BUDGET_S = 150.0


# ── 엔진 비교표 ──


def _raw(arm: str, wid: str, *, playbook: bool, hyps: int = 0, steps: int | None = 3,
         pb_llm: int = 0, inconclusive: bool = False) -> dict[str, Any]:
    done: dict[str, Any] = {"event": "done", "investigation_id": f"{arm}-{wid}"}
    briefing: dict[str, Any] = {"root_cause_hypotheses": []}
    if playbook:
        done.update(playbook_id="host.cpu", playbook_matched_by="match:alarm_kind",
                    playbook_llm_calls=pb_llm, deep_dive_mode="off", deep_dive_status="skipped",
                    cache_real_calls=5, cache_hits=2)
        briefing["playbook"] = {
            "synthesis": {"hypotheses": [{"rank": i + 1, "title": f"h{i}"} for i in range(hyps)],
                          "top_inconclusive": inconclusive},
            "readings": {"db_split": "n/a"},
        }
    trace = [{"tool": "polestar_metric_trend"}, {"tool": "list_sources"},
             {"tool": "gateway_health"}, {"tool": "polestar_process_snapshot"}]
    return {"arm": arm, "world_id": wid, "split": "dev", "trial": 1, "status": "done",
            "elapsed_s": 1.5, "steps": steps, "briefing": briefing, "audit": [done],
            "tool_trace": trace}


def _score(arm: str, wid: str, rc1: bool) -> dict[str, Any]:
    return {"arm": arm, "world_id": wid, "trial": 1, "valid": True, "rc1": rc1, "rc3": rc1,
            "failure_code": "PASS" if rc1 else "F-MISS", "top1": {"cause": "원인"}}


def test_engine_row_counts_llm_tools_and_reads_playbook_keys() -> None:
    req = {("pb", "D01", 1): 9}
    row = engine_compare.engine_row(
        _raw("pb", "D01", playbook=True, hyps=2, steps=None, pb_llm=6, inconclusive=True),
        _score("pb", "D01", True), req)
    assert row["engine"] == "playbook" and row["playbook_id"] == "host.cpu"
    assert row["llm_calls"] == 6 and row["playbook_llm_calls"] == 6 and row["holmes_steps"] is None
    assert row["llm_requests_observed"] == 9
    assert (row["tool_calls"], row["health_calls"]) == (2, 2)  # 헬스체크는 따로
    assert (row["cache_real_calls"], row["cache_hits"]) == (5, 2)
    assert row["hypotheses_n"] == 2 and row["top_inconclusive"] is True
    assert row["db_split"] == "n/a" and row["rc1"] is True
    holmes = engine_compare.engine_row(_raw("h", "D01", playbook=False), _score("h", "D01", True))
    assert holmes["engine"] == "holmesgpt" and holmes["llm_calls"] == 3
    assert holmes["hypotheses_n"] is None and holmes["top_inconclusive"] is None
    assert holmes["llm_requests_observed"] is None  # 가짜 LLM 로그 없음(실측 회차)


def test_parallel_llm_calls_sum_both_engines() -> None:
    row = engine_compare.engine_row(_raw("p", "D01", playbook=True, hyps=1, steps=3, pb_llm=6),
                                    None)
    assert row["llm_calls"] == 9 and row["valid"] is None


def test_arm_all_no_hypothesis_is_undecidable_d219() -> None:
    rows = [engine_compare.engine_row(_raw("pb", w, playbook=True, hyps=0), _score("pb", w, False))
            for w in ("D01", "D02")]
    s = engine_compare.arm_summary(rows)
    assert s["rc1"] is None and s["rc3"] is None and s["rc_note"] == engine_compare.UNDECIDABLE
    assert s["playbook_no_hypothesis"] == 2
    rows.append(engine_compare.engine_row(_raw("pb", "D03", playbook=True, hyps=1),
                                          _score("pb", "D03", True)))
    s = engine_compare.arm_summary(rows)
    assert s["rc1"] == pytest.approx(1 / 3, abs=1e-4) and s["rc_note"] is None


def test_build_and_write_from_run_dir(tmp_path: Path) -> None:
    raws = [_raw("holmes", "D01", playbook=False),
            _raw("playbook_off", "D01", playbook=True, hyps=1, steps=None, pb_llm=6)]
    scores = [_score("holmes", "D01", True), _score("playbook_off", "D01", False)]
    fake = [{"arm": "holmes", "world_id": "D01", "trial": 1}] * 3 + \
        [{"arm": "playbook_off", "world_id": "D01", "trial": 1}] * 6
    for name, rows in (("raw.jsonl", raws), ("scores.jsonl", scores), ("fake_llm.jsonl", fake)):
        (tmp_path / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    jp, mp = engine_compare.write(tmp_path)
    result = json.loads(jp.read_text(encoding="utf-8"))
    assert list(result["arms"]) == ["holmes", "playbook_off"]
    assert result["arms"]["holmes"]["all"]["rc1"] == 1.0
    assert result["arms"]["playbook_off"]["dev"]["engines"] == {"playbook": 1}
    by_arm = {r["arm"]: r for r in result["rows"]}
    for r in by_arm.values():  # 감사 계측 = 가짜 LLM이 실제로 받은 요청 수
        assert r["llm_calls"] == r["llm_requests_observed"]
    md = mp.read_text(encoding="utf-8")
    assert "| holmes | all | 1/1 |" in md and "playbook_off" in md
    assert engine_compare.render_md(result) == md  # 같은 입력 = 같은 바이트


# ── 명세 · CLI ──


def test_w7_spec_arms_reach_env_and_echo_allowlist(tmp_path: Path) -> None:
    spec, _ = runner.load_spec(_SPEC)
    assert list(spec["arms"]) == ["holmes", "playbook_off", "playbook_parallel"]
    assert spec["llm"]["backend"] == "fake"
    env = {arm: runner.build_arm_env(spec, arm, run_dir=tmp_path, polestar_url="p", apm_url="a",
                                     api_base="http://127.0.0.1:1/v1", parent_env={})
           for arm in spec["arms"]}
    assert "SRE_PLAYBOOK_ENABLED" not in env["holmes"]  # 대조군 = 현행 기본(플레이북 off)
    assert (env["playbook_off"]["SRE_PLAYBOOK_ENABLED"],
            env["playbook_off"]["SRE_DEEP_DIVE_MODE"]) == ("true", "off")
    assert env["playbook_parallel"]["SRE_DEEP_DIVE_MODE"] == "parallel"
    assert all(e["GEMINI_API_KEY"] == "" for e in env.values())
    allowed = runner.spec_fields(spec, "playbook_off")
    assert {"sre_playbook_enabled", "sre_deep_dive_mode"} <= allowed


def test_cli_actions_are_exclusive() -> None:
    p = build_parser()
    assert p.parse_args(["--engine-compare", "x"]).engine_compare == Path("x")
    assert p.parse_args(["--playbook-fixtures", "y"]).playbook_fixtures == Path("y")
    with pytest.raises(SystemExit):
        p.parse_args(["--engine-compare", "x", "--report", "x"])


# ── 플레이북 픽스처 평가(오프라인) ──


def test_reading_equal_ignores_extra_descriptive_keys() -> None:
    exp = [{"source": "apm_source_changes", "at": "t", "minutes_before_alarm": 50}]
    assert playbook_fixture_eval.reading_equal(exp, [{**exp[0], "description": "x"}])
    assert not playbook_fixture_eval.reading_equal(exp, [{**exp[0], "at": "u"}])
    assert not playbook_fixture_eval.reading_equal(exp, [])
    assert playbook_fixture_eval.reading_equal("n/a", "n/a")


@pytest.fixture(scope="module")
def fixture_eval() -> dict[str, Any]:
    fx = playbook_fixture_eval.load_fixtures()
    return playbook_fixture_eval.evaluate({k: fx[k] for k in ("gc", "case1_jdbc_acquire",
                                                              "normal")})


def test_fixture_eval_runs_real_dispatcher_path(fixture_eval: dict[str, Any]) -> None:
    rows = {r["scenario"]: r for r in fixture_eval["rows"]}
    gc = rows["gc"]
    assert gc["status"] == "done" and gc["playbook_id"] == "was.latency"
    assert gc["hit"] and gc["title_hit"] and gc["readings_match"] == gc["readings_total"]
    assert gc["deep_dive_status"] == "skipped" and gc["deep_calls"] == 0  # 심층 off
    assert gc["llm_calls"] > 0 and gc["tool_calls"] > 0 and gc["unrecorded_calls"] == 0
    assert gc["cache_hits"] is not None
    assert gc["holmesgpt"] == playbook_fixture_eval.HOLMES_NOT_COMPARABLE


def test_fixture_eval_case1_rd_gap_is_top_inconclusive(fixture_eval: dict[str, Any]) -> None:
    """R-D — 게이트웨이에 커넥션 획득 구조화 필드가 없어 db_split=n/a · 1순위 판단 보류(§2.3)."""
    case1 = {r["scenario"]: r for r in fixture_eval["rows"]}["case1_jdbc_acquire"]
    assert case1["title_hit"] and not case1["hit"] and case1["top_inconclusive"] is True
    assert case1["db_split"] == "n/a" and case1["case_match"] == {"db_split": False}


def test_fixture_eval_normal_has_no_top(fixture_eval: dict[str, Any]) -> None:
    normal = {r["scenario"]: r for r in fixture_eval["rows"]}["normal"]
    assert normal["expected_top"] is None and normal["hypotheses_n"] == 0 and normal["hit"]


def test_fixture_eval_write(tmp_path: Path, fixture_eval: dict[str, Any]) -> None:
    jp, mp = playbook_fixture_eval.write(tmp_path / "out", fixture_eval)
    s = json.loads(jp.read_text(encoding="utf-8"))["summary"]
    assert s["fixtures"] == 3 and s["hit"] == 2 and s["title_hit"] == 3
    assert s["case_conclusion_total"] == 1 and s["case_conclusion_match"] == 0
    assert "비교 불가(D-219" in mp.read_text(encoding="utf-8")


# ── 종단 1회(가짜 LLM · 실프로세스) ──


@pytest.fixture(scope="module")
def w7_e2e(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("w7_e2e")
    spec = yaml.safe_load(_SPEC.read_text(encoding="utf-8"))
    spec["run"].update(worlds=["T01"], splits=["dev"], worlds_dir=str(_FIX / "worlds"),
                       snapshot=str(_FIX / "tool_schemas.json"),
                       ports={"fake_llm": 0, "world": 0, "world_apm": 0, "arm_base": 0})
    spec["arms"] = {k: spec["arms"][k] for k in ("holmes", "playbook_off")}
    path = tmp / "w7.yaml"
    path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    out_root = tmp / "runs"
    proc = subprocess.run(
        [sys.executable, str(_TOP / "scripts" / "inv_bench.py"), "--run", "--spec", str(path),
         "--test", "--out-root", str(out_root)],
        cwd=_TOP, capture_output=True, text=True, timeout=_BUDGET_S,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (run_dir,) = [p for p in out_root.iterdir() if p.is_dir()]
    proc = subprocess.run(
        [sys.executable, str(_TOP / "scripts" / "inv_bench.py"), "--engine-compare", str(run_dir)],
        cwd=_TOP, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return run_dir


def test_e2e_playbook_arm_takes_playbook_path(w7_e2e: Path) -> None:
    result = json.loads((w7_e2e / engine_compare.JSON_NAME).read_text(encoding="utf-8"))
    rows = {r["arm"]: r for r in result["rows"]}
    assert rows["holmes"]["engine"] == "holmesgpt" and rows["holmes"]["playbook_id"] is None
    pb = rows["playbook_off"]
    assert pb["status"] == "done" and pb["engine"] == "playbook" and pb["playbook_id"]
    assert pb["deep_dive_mode"] is not None and pb["cache_real_calls"] is not None
    for r in rows.values():  # 감사 계측 LLM 호출 = 가짜 LLM이 받은 요청
        assert r["llm_calls"] == r["llm_requests_observed"] > 0
    echo = json.loads((w7_e2e / "arm-playbook_off" / "settings_echo.json").read_text(
        encoding="utf-8"))
    assert echo["sre_playbook_enabled"] is True and echo["sre_deep_dive_mode"] == "off"
    assert (w7_e2e / engine_compare.MD_NAME).is_file()
