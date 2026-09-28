"""plans/119 하네스 트랙 — H-1 TTFT · H-2 알람 고지 증거 · T-0 단계 타임라인 · 상한 의미(D-267 ⑦).

전부 무과금이다. 서버를 띄우지 않고 합성 SSE·합성 `raw.jsonl`·합성 서버 로그로 검증한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import analyze, runner, utf8_open
from scripts.scenario import client as client_mod
from scripts.scenario.assertions import Observation, Verdict
from scripts.scenario.catalog import Scenario, Turn
from scripts.scenario.client import ClientConfig, ScenarioClient
from scripts.scenario.report import (
    TIER2_ALARM_NOTE,
    TIER2_ALARM_T1_NOTE,
    build_summary,
    cap_semantic_warning,
    render_markdown,
    t1_alarm_evidence,
    timeout_attribution,
    timeout_stage_table,
)

REAL_RUN = Path(__file__).resolve().parents[2] / "results" / "scenario" / "20260923-103638"


# ──────────────────────────────────────────────
# 공용 픽스처
# ──────────────────────────────────────────────

class FakeResponse:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def iter_lines(self) -> Iterator[str]:
        for event in self._events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}"


@pytest.fixture()
def client() -> Iterator[ScenarioClient]:
    instance = ScenarioClient(ClientConfig(port=1))
    yield instance
    instance.close()


def _consume_at(client: ScenarioClient, monkeypatch, events: list[dict[str, Any]],
                clock: list[float]) -> Observation:
    """이벤트마다 `perf_counter` 가 `clock` 값을 차례로 돌려준다(요청 송신 = 0.0초)."""
    ticks = iter(clock)
    monkeypatch.setattr(client_mod.time, "perf_counter", lambda: next(ticks))
    obs = Observation()
    client._consume_sse(FakeResponse(events), obs, started=0.0)
    return obs


def _timeline(**over: Any) -> dict[str, Any]:
    base = {"cap_semantic": "first_answer", "limit_sec": 240.0, "parse_end_ms": 7000.0,
            "plan_end_ms": 9000.0, "first_rows_ms": 30000.0, "answer_start_ms": 32000.0,
            "first_answer_ms": 33000.0, "end_ms": 60000.0, "timeout_stage": None,
            "timeout_kind": None}
    base.update(over)
    return base


def _row(scenario_id: str, group: str = "A", **over: Any) -> dict[str, Any]:
    base = {"run_id": "r", "profile": "baseline", "env": "closed", "mode": "run", "repeat": 0,
            "group": group, "scenario_id": scenario_id, "turn": 1, "plans": [119],
            "kind": "normal", "pair_id": None, "func_verdict": "pass", "perf_verdict": "fail",
            "response_mode": "answer", "forbidden_mode": None, "failed_assertions": [],
            "manual_notes": [], "wall_ms": 60000.0, "processing_time_ms": 60000.0,
            "node_elapsed_ms": {}, "node_calls": {}, "node_path": [], "sse_events": [],
            "executed_sql": None, "executed_sqls": [], "row_count": None,
            "row_counts_by_db": {}, "artifacts": [], "error": None,
            "unevaluated_reason": None}
    base.update(over)
    return base


def _write_run(tmp_path: Path, rows: list[dict[str, Any]], *, name: str = "20260928-000000",
               meta: dict[str, Any] | None = None, tier: str = "intent_orchestration") -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True)
    with utf8_open(run_dir / "raw.jsonl", "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    payload = {"meta": {"run_id": name, "env": "closed", "mode": "run", "repeat": 1,
                        **(meta or {})},
               "profiles": [{"name": "baseline", "valid": True, "tier": tier, "reasons": []}],
               "skipped": []}
    with utf8_open(run_dir / "run.json", "w") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return run_dir


_TIMEOUT_ERROR = "처리 시간이 초과되었습니다. 질의를 단순화해주세요."

#: run 20260923-103638 의 R3-08(2회차) 형태 — 21초에 데이터를 확보하고 서술 중 끊겼다.
_NARRATION_DEATH = dict(
    node_path=["context_resolver", "input_parser", "field_mapper", "intent_planner",
               "agent_orchestrator", "replanner", "result_aggregator"],
    node_calls={"context_resolver": 1, "input_parser": 1, "field_mapper": 1, "intent_planner": 1,
                "agent_orchestrator": 1, "replanner": 1},
    node_elapsed_ms={"context_resolver": 0.0, "input_parser": 5700.0, "field_mapper": 0.0,
                     "intent_planner": 0.0, "agent_orchestrator": 12300.0, "replanner": 2700.0},
    sse_events=["done", "node_complete", "node_start", "token"],
    row_counts_by_db={"polestar_cm_gp": 1690}, executed_sqls=["SELECT 1"],
    error=_TIMEOUT_ERROR, forbidden_mode="hang", func_verdict="error",
    unevaluated_reason="timeout", wall_ms=241000.0,
)
#: R4-09 형태 — 재계획 루프(agent_orchestrator 3회 · replanner 2회) 도중 끊겼다.
#: 서술 노드는 시작하지 않았다.
_LOOP_DEATH = dict(
    node_path=["context_resolver", "input_parser", "field_mapper", "intent_planner",
               "agent_orchestrator", "replanner"],
    node_calls={"context_resolver": 1, "input_parser": 1, "field_mapper": 1, "intent_planner": 1,
                "agent_orchestrator": 3, "replanner": 2},
    node_elapsed_ms={"context_resolver": 0.0, "input_parser": 7700.0, "field_mapper": 0.0,
                     "intent_planner": 4300.0, "agent_orchestrator": 206100.0,
                     "replanner": 18600.0},
    sse_events=["error", "node_complete", "node_start"],
    row_counts_by_db={"polestar_b0": 0}, executed_sqls=["SELECT 1"],
    error=_TIMEOUT_ERROR, forbidden_mode="hang", func_verdict="error",
    unevaluated_reason="timeout", wall_ms=241800.0,
)


# ──────────────────────────────────────────────
# H-1 — 첫 토큰(TTFT) 수집
# ──────────────────────────────────────────────

def test_ttft는_내용_있는_첫_token_수신_시각이고_ttfb와_기준이_같다(client, monkeypatch) -> None:
    obs = _consume_at(client, monkeypatch, [
        {"type": "node_start", "node": "context_resolver", "timestamp_ms": 5.0},
        {"type": "token", "content": ""},          # 빈 토큰은 답변이 아니다
        {"type": "token", "content": "서버"},
        {"type": "token", "content": " 3대"},
        {"type": "done", "response": "서버 3대"},
    ], clock=[0.02, 0.5, 1.2, 1.3, 2.0])
    assert obs.ttfb_ms == pytest.approx(20.0)     # 첫 node_start — 그래프 진입 신호
    assert obs.ttft_ms == pytest.approx(1200.0)   # 첫 **내용 있는** 토큰
    assert obs.response == "서버 3대"


def test_토큰_없이_done_만_온_턴은_ttft가_없다(client, monkeypatch) -> None:
    """역질문(pre-gate done)은 답변 토큰이 없다 — 0 으로 채우지 않는다."""
    obs = _consume_at(client, monkeypatch, [
        {"type": "done", "clarification": {"kind": "zone_select", "question": "어느 존?"}},
    ], clock=[0.3])
    assert obs.ttft_ms is None and obs.status == "clarification"


def test_러너_행에_ttft와_timeline이_실린다() -> None:
    scenario = Scenario(id="A-01", group="A", plans=[119], title="t",
                        turns=[Turn(send={"query": "q"}, expect={})])
    obs = Observation(status="completed", ttfb_ms=21.0, ttft_ms=1500.0,
                      timeline=_timeline())
    meta = {"run_id": "r", "env": "closed", "mode": "run"}
    row = runner._row(meta, "baseline", scenario, 1, 0, obs, Verdict())
    assert row["ttfb_ms"] == 21.0 and row["ttft_ms"] == 1500.0
    assert row["timeline"]["first_answer_ms"] == 33000.0


# ──────────────────────────────────────────────
# T-0 — 서버 timeline 저장 · 집계 · 사망 단계 표
# ──────────────────────────────────────────────

def test_done과_error의_timeline을_가공_없이_옮긴다(client, monkeypatch) -> None:
    done = _consume_at(client, monkeypatch, [
        {"type": "done", "response": "끝", "timeline": _timeline()},
    ], clock=[0.1])
    assert done.timeline == _timeline()

    timeline = _timeline(first_answer_ms=None, answer_start_ms=None, timeout_stage="retrieval",
                         timeout_kind="processing")
    failed = _consume_at(client, monkeypatch, [
        {"type": "error", "message": _TIMEOUT_ERROR, "code": "timeout", "timeline": timeline},
    ], clock=[0.1])
    assert failed.timeline == timeline and failed.status == "error"


def test_옛_서버_페이로드에는_timeline이_없다(client, monkeypatch) -> None:
    obs = _consume_at(client, monkeypatch, [{"type": "done", "response": "끝"}], clock=[0.1])
    assert obs.timeline is None


def test_단계별_p50_p90와_서버_귀속_사망_단계_표(tmp_path) -> None:
    ok = [_row(f"A-{i:02d}", timeline=_timeline(parse_end_ms=1000.0 * i), ttft_ms=900.0 * i)
          for i in range(1, 11)]
    died = _row("R3-08", func_verdict="error", error=_TIMEOUT_ERROR, unevaluated_reason="timeout",
                row_counts_by_db={"polestar_cm_gp": 1690}, executed_sqls=["SELECT 1"],
                timeline=_timeline(first_answer_ms=None, answer_start_ms=21000.0,
                                   timeout_stage="answer", timeout_kind="processing"))
    run_dir = _write_run(tmp_path, ok + [died])
    summary = build_summary(run_dir, None)

    marks = summary["timeline"]["marks"]
    assert summary["timeline"]["with_timeline"] == 11
    assert marks["parse_end_ms"]["n"] == 11 and marks["parse_end_ms"]["p50"] == 6000.0
    assert marks["parse_end_ms"]["p90"] is not None
    assert summary["timeline"]["ttft"]["n"] == 10

    [item] = summary["timeouts"]
    assert item["source"] == "server" and item["stage"] == "answer"
    assert item["entered_ms"] == 21000.0 and item["data"] == "rows" and item["streaming"] is False

    markdown = render_markdown(summary, run_dir, None)
    section3 = markdown.split("## 3.")[1].split("## 4.")[0]
    assert "### 단계 타임라인" in section3 and "첫 행 확보" in section3
    assert "| 응답 서술 | 서버 | 1 | 21초 |" in section3 and "processing 1" in section3
    assert "**추정**" not in section3


def test_답변이_나가던_중_끊기면_스트리밍_중_끊김으로_센다() -> None:
    row = _row("K-02", func_verdict="error", error=_TIMEOUT_ERROR, unevaluated_reason="timeout",
               ttft_ms=40000.0,
               timeline=_timeline(timeout_stage="delivery", timeout_kind="idle"))
    [item] = timeout_attribution([row])
    assert item["stage"] == "delivery" and item["entered_ms"] == 33000.0
    assert item["streaming"] is True and item["kind"] == "idle"


def test_부분_결과_done도_타임아웃으로_귀속한다() -> None:
    """`status=partial` 은 오류 문구가 없지만 사건은 타임아웃이다(plans/114 P-2)."""
    row = _row("B-05", status="partial", unevaluated_reason="timeout",
               timeline=_timeline(first_answer_ms=None, timeout_stage="answer",
                                  timeout_kind="processing"))
    assert [i["stage"] for i in timeout_attribution([row])] == ["answer"]


# ──────────────────────────────────────────────
# 옛 run — 노드 경과로 추정 귀속(plans/119 §2.9 방식)
# ──────────────────────────────────────────────

def test_옛_run은_노드_경과로_추정하고_추정이라고_적는다(tmp_path) -> None:
    rows = [_row("R3-08", **_NARRATION_DEATH), _row("R4-09", **_LOOP_DEATH), _row("A-01")]
    items = timeout_attribution(rows)
    narration, loop = items
    assert narration["source"] == "estimate" and narration["stage"] == "answer"
    assert narration["entered_ms"] == pytest.approx(20700.0)   # 완료 노드 경과의 합
    assert narration["data"] == "rows" and narration["streaming"] is True
    assert loop["stage"] == "retrieval" and loop["entered_ms"] == pytest.approx(12000.0)
    assert loop["data"] == "zero" and loop["streaming"] is False

    table = timeout_stage_table(items)
    assert [(c["stage"], c["source"]) for c in table] == [("retrieval", "estimate"),
                                                          ("answer", "estimate")]

    run_dir = _write_run(tmp_path, rows)
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    section3 = markdown.split("## 3.")[1].split("## 4.")[0]
    assert "서버 `timeline` 미수집" in section3
    assert "| 응답 서술 | **추정** | 1 | 21초 |" in section3
    assert "※ **추정** 행은" in section3


def test_전_노드가_끝났는데_끊겼으면_서술_노드는_전달_단계다() -> None:
    row = _row("X-01", **{**_NARRATION_DEATH,
                          "node_calls": {**_NARRATION_DEATH["node_calls"],
                                         "result_aggregator": 1},
                          "node_elapsed_ms": {**_NARRATION_DEATH["node_elapsed_ms"],
                                              "result_aggregator": 200000.0}})
    [item] = timeout_attribution([row])
    assert item["stage"] == "delivery"


def test_옛_run의_3절은_TTFT를_미측정으로_적는다(tmp_path) -> None:
    run_dir = _write_run(tmp_path, [_row("A-01")])    # `ttft_ms` 칸 자체가 없다
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    section3 = markdown.split("## 3.")[1].split("## 4.")[0]
    assert "| TTFT p50 | TTFT p90 |" in section3
    assert "| 미측정 | 미측정 |" in section3


def test_새_run의_3절은_군별_TTFT를_낸다(tmp_path) -> None:
    rows = [_row(f"A-{i:02d}", ttft_ms=1000.0 * i) for i in range(1, 4)] + [
        _row("A-09", ttft_ms=None)]   # 토큰 없는 턴은 표본이 아니다
    summary = build_summary(_write_run(tmp_path, rows), None)
    ttft = summary["groups"]["A"]["ttft"]
    assert ttft == {"n": 3, "p50": 2000.0, "p90": None, "measured": True}


# ──────────────────────────────────────────────
# H-2 — `tier2_alarm_caveat` 증거 기반화
# ──────────────────────────────────────────────

_SHORTCUT_LOG = (
    "2026-09-23 10:45:18,420 [INFO] src.nodes.input_parser: 입력 파싱 완료: targets=['서버']\n"
    "2026-09-23 10:45:18,420 [INFO] src.orchestration.intent_planner: intent_planner: "
    "selected_db_ids 감지, data_query 단일 task (존 선택 고정=['polestar_cm_gp'])\n"
    "2026-09-23 10:45:18,420 [INFO] src.orchestration.intent_planner: intent_planner: "
    "알람 조회 결정적 교정 — data_query→alarm_query (sub_query='심각 알람 목록')\n"
)
#: T-1 이전에도 있던 LLM 분해 폴백 경로의 교정(run 20260922-093837 실측 형태) — 증거가 아니다.
_LLM_PATH_LOG = (
    "2026-09-22 15:39:30,359 [WARNING] src.orchestration.intent_planner: intent_planner 분해 "
    "결과 없음/무효, 단일 data_query 폴백\n"
    "2026-09-22 15:39:30,359 [INFO] src.orchestration.intent_planner: intent_planner: "
    "알람 조회 결정적 교정 — data_query→alarm_query (sub_query='알람 임계치를 80으로 바꿔줘')\n"
)


def _alarm_run(tmp_path: Path, *, log: str | None = None,
               meta: dict[str, Any] | None = None) -> Path:
    run_dir = _write_run(tmp_path, [_row("D-03", "D", func_verdict="fail"), _row("A-01")],
                         meta=meta)
    if log is not None:
        (run_dir / "logs").mkdir()
        (run_dir / "logs" / "server-baseline.log").write_text(log, encoding="utf-8")
    return run_dir


def test_단락_뒤_교정만_T1_증거로_센다(tmp_path) -> None:
    path = tmp_path / "server.log"
    path.write_text(_SHORTCUT_LOG + _LLM_PATH_LOG + _SHORTCUT_LOG, encoding="utf-8")
    assert t1_alarm_evidence(path) == 2
    assert t1_alarm_evidence(tmp_path / "없음.log") == 0


def test_증거가_있으면_고지를_해석_제외_불요로_낮춘다(tmp_path) -> None:
    run_dir = _alarm_run(tmp_path, log=_SHORTCUT_LOG)
    summary = build_summary(run_dir, None)
    caveat = summary["tier2_alarm_caveat"]
    assert caveat["t1_evidence"] == {"source": "server_log", "count": 1,
                                     "by": {"server-baseline.log": 1}}
    markdown = render_markdown(summary, run_dir, None)
    assert f"[안내] {TIER2_ALARM_T1_NOTE}" in markdown.split("## 1.")[0]
    assert f"※ {TIER2_ALARM_NOTE}" not in markdown       # 시나리오별 해석 제외를 달지 않는다
    assert f"- **{TIER2_ALARM_NOTE}**" not in markdown


def test_증거가_없으면_종전_고지를_유지한다(tmp_path) -> None:
    run_dir = _alarm_run(tmp_path, log=_LLM_PATH_LOG)    # LLM 경로 교정뿐 — T-1 증거가 아니다
    summary = build_summary(run_dir, None)
    assert summary["tier2_alarm_caveat"]["t1_evidence"] is None
    markdown = render_markdown(summary, run_dir, None)
    assert f"[{TIER2_ALARM_NOTE}]" in markdown.split("## 1.")[0]
    assert f"※ {TIER2_ALARM_NOTE}: D-03" in markdown


def test_run_메타_표지가_로그보다_우선한다(tmp_path) -> None:
    run_dir = _alarm_run(tmp_path, log="", meta={"t1_alarm_evidence": {"baseline": 3}})
    caveat = build_summary(run_dir, None)["tier2_alarm_caveat"]
    assert caveat["t1_evidence"] == {"source": "run_meta", "count": 3, "by": {"baseline": 3}}


@pytest.mark.skipif(not (REAL_RUN / "logs").exists(), reason="실 run 산출물이 로컬에 없다")
def test_실_run_20260923_103638_은_T1_적용_run이다() -> None:
    from scripts.scenario.report import load_rows, t1_evidence, valid_rows

    evidence = t1_evidence(REAL_RUN, {})
    assert evidence and evidence["by"]["server-baseline.log"] == 19   # plans/119 §1 ⑧ 실측과 같다
    stages = {(c["stage"], c["turns"]) for c in
              timeout_stage_table(timeout_attribution(valid_rows(load_rows(REAL_RUN))))}
    assert stages == {("answer", 12), ("retrieval", 8)}                # plans/119 §2.9 와 같다


# ──────────────────────────────────────────────
# 상한 의미(D-267 ⑦) — run 메타 · 리포트 머리 · 회귀 비교 경고
# ──────────────────────────────────────────────

def test_러너는_상한_의미의_첫_관측값을_메타에_남긴다() -> None:
    meta: dict[str, Any] = {}
    runner.note_cap_semantic(meta, None)                               # 타임라인 없음 — 미관측
    assert "cap_semantic" not in meta
    runner.note_cap_semantic(meta, {"parse_end_ms": 1.0})             # 키 없음 = 옛 의미
    assert meta == {"cap_semantic": "total", "cap_semantic_source": "server"}
    runner.note_cap_semantic(meta, _timeline())                        # 첫 관측값을 지키지 않으면
    assert meta["cap_semantic"] == "total"                             # 재개가 값을 뒤집는다


def test_리포트_머리와_1절에_상한_의미를_싣는다(tmp_path) -> None:
    run_dir = _write_run(tmp_path, [_row("A-01", timeline=_timeline())],
                         meta={"cap_semantic": "first_answer", "cap_semantic_source": "server"})
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    head = markdown.split("## 1.")[0]
    assert "처리 상한 의미: **첫 답변까지(`first_answer`)** (서버 보고)" in head
    assert "| 처리 상한 의미 | 첫 답변까지(`first_answer`) - 서버 보고 |" in markdown


def test_칸도_타임라인도_없는_옛_run은_요청_전체로_읽는다(tmp_path) -> None:
    summary = build_summary(_write_run(tmp_path, [_row("A-01")]), None)
    assert summary["cap_semantic"] == {"value": "total", "source": "default", "observed": [],
                                       "mixed": False}


def test_한_run에_두_의미가_섞이면_머리에_경고한다(tmp_path) -> None:
    rows = [_row("A-01", timeline=_timeline(cap_semantic="total")),
            _row("A-02", timeline=_timeline())]
    run_dir = _write_run(tmp_path, rows)
    summary = build_summary(run_dir, None)
    assert summary["cap_semantic"]["mixed"] is True
    assert "한 run 에 처리 상한 의미가 섞였다" in render_markdown(summary, run_dir, None)


def test_직전_run과_상한_의미가_다르면_8절이_경고한다(tmp_path) -> None:
    _write_run(tmp_path, [_row("A-01")], name="20260923-103638")                       # total
    run_dir = _write_run(tmp_path, [_row("A-01", timeline=_timeline())], name="20260930-000000",
                         meta={"cap_semantic": "first_answer", "cap_semantic_source": "server"})
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    section8 = markdown.split("## 8.")[1].split("## 9.")[0]
    assert "직전 비교 대상: `20260923-103638`" in section8
    assert "처리 상한 의미가 다르다" in section8 and "직접 비교하지 않는다" in section8


def test_같은_의미면_경고하지_않는다() -> None:
    assert cap_semantic_warning("total", "total") is None
    assert "직접 비교하지 않는다" in cap_semantic_warning("total", "first_answer")


def test_분석기_회귀는_상한_의미가_달라도_기준선을_유지하고_경고한다(tmp_path, monkeypatch) -> None:
    """`cap_semantic` 은 비교 키의 **경고 축**이다 — 사다리 단처럼 기준선에서 빼지 않는다."""
    runs = tmp_path / "scenario"
    for name in ("20260923-103638", "20260930-000000"):
        (runs / name).mkdir(parents=True)

    def summary(cap: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "meta": {"env": "closed", "repeat": 3}, "invalid": {"over_threshold": False},
            "profiles": [{"name": "baseline", "tier": "intent_orchestration"}],
            "scenario_verdicts": {"A-01": {"verdict": "pass"}}}
        if cap:
            out["cap_semantic"] = {"value": cap}
        return out

    monkeypatch.setattr(analyze, "build_summary", lambda path, _cat: summary(None))
    text = analyze.regression(runs / "20260930-000000", summary("first_answer"))
    assert "직전 비교 대상: `20260923-103638`" in text
    assert "처리 상한 의미가 다르다" in text
    assert analyze.comparison_keys(summary("first_answer")) == {
        ("closed", "intent_orchestration", "baseline", None, "first_answer")}
