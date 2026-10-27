"""plans/187 W20 독립 검증(verifier) — sre_agent가 실제로 내는 조사 흐름 이벤트·부분 브리핑 모양.

host.cpu v2 사용자 예시(java WAS · 인스턴스 A1 CPU 대부분 · 실행 중 요청·SQL)를 디스패처 종단으로
돌려(실행 기록 on · 가짜 LLM) 이벤트·부분 브리핑·종결 브리핑을 JSON 픽스처로 떨어뜨린다. 본체
`tests/test_api/test_plan187_w20_verify.py`가 그 픽스처를 읽어 `diagnosis-view.js`에 넣는다(양방향
import 0 — 파일 계약만).

S3a·S3b(`apm.active_services`·`apm.active_sql`)는 실제 스텝 함수, 나머지는 실제 도구 이름으로
`ctx.call`을 부른 뒤(조회 문장 `phrase` 생성) 고정 결과를 돌려주는 가짜 스텝이다. 실 LLM 0.

픽스처 재생성: `W20_VERIFY_WRITE=1 .venv/bin/python -m pytest tests/test_plan187_w20_verify.py`
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sre_agent.application import playbook_steps as steps_mod
from sre_agent.application.playbook_narrator import (
    NarratorLimits,
    PlaybookNarrator,
    StepNarration,
    StepOpinionStream,
)
from sre_agent.application.playbook_steps import (
    APM_ACTIVE_DETAIL,
    APM_ACTIVE_SERVICES,
    RunScope,
    StepContext,
    StepResult,
    mask_active_sql,
)
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.domain.step_findings import summarize
from sre_agent.infrastructure.activity_masking import mask_activity_text
from test_plan187_w4_dispatch import FakeLLM, build, make_job, make_settings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "plan187" / "snapshots" / (
    "w20_flow_hostcpu.json"
)
SECRET = "hunter2"
SQL_A = (
    "SELECT o.id FROM orders o WHERE o.cust = 'kim' AND o.amt > 1500 "
    "/* password=hunter2 */"
)
PAYLOAD = {
    "contract_version": "1",
    "event": {
        "dbId": "polestar",
        "serverName": "SAE0011",
        "hostname": "SAE0011",
        "alarmId": "polestar:w20v",
        "severity": 2,
        "alarmName": "CPU 사용률 95%",
        "resourceType": "server",
        "resourceName": "SAE0011",
        "alarmTime": "20261019100000",
    },
    "decision": {"tier": "page"},
    "meta": {"episode": {"episode_id": "ep", "infra_db_ids": ["polestar"], "members": []}},
}


# ── 가짜 조회 콜러블(폴스타·APM 공용) ─────────────────────────────────────────


def _service(tx: str, cpu: int, elapsed: int, sql_count: int) -> dict:
    return {
        "instance_name": "A1", "application": f"/order/{tx}", "cpu_ms": cpu,
        "elapsed_ms": elapsed, "sql_count": sql_count, "txid": tx,
        "active_ref": {"source_id": "j1", "domain_id": 1000, "txid": tx, "thread_hash": 7},
    }


SERVICES = [_service("101", 900, 5000, 7), _service("102", 300, 9000, 2),
            _service("103", 700, 3000, 4)]
SQLS = {"101": SQL_A, "103": "SELECT 1 FROM dual", "102": "UPDATE t SET a = 'x'"}


class Caller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, calls):  # noqa: ANN001
        out = []
        for tool, args in calls:
            with self._lock:
                self.calls.append((tool, dict(args)))
            if tool == APM_ACTIVE_SERVICES:
                body: dict = {"rows": SERVICES, "queried_at": "2026-10-19T10:01:00"}
            elif tool == APM_ACTIVE_DETAIL:
                tx = str(args.get("txid"))
                body = {"rows": [{"txid": tx, "sql": SQLS.get(tx)}],
                        "queried_at": "2026-10-19T10:01:10"}
            else:
                body = {"rows": []}
            out.append(json.dumps(body, ensure_ascii=False))
        return out


# ── 가짜 스텝 — 실제 도구 이름으로 조회(문장 생성) 후 고정 결과 ─────────────────


def _calling(axis: str, tool: str, args: dict, make):  # noqa: ANN001
    def fn(ctx: StepContext) -> StepResult:
        ctx.call(axis, [(tool, dict(args))])
        return make(ctx)

    return fn


def _s4(ctx: StepContext) -> StepResult:
    return StepResult(
        verdict="degraded", verdict_source="playbook_rule",
        table={"columns": ["kind", "peak_value", "baseline_mean"], "rows": [["cpu", 96.0, 43.0]]},
        data={"findings": {"cpu": {"peak_value": 96.0, "baseline_mean": 43.0,
                                   "is_anomalous": True}}},
    )


def _s6(ctx: StepContext) -> StepResult:
    rows = [{"name": "java", "pcpu": 78.0}, {"name": "sshd", "pcpu": 3.0}]
    return StepResult(
        table={"columns": ["name", "pcpu"], "rows": [[r["name"], r["pcpu"]] for r in rows]},
        data={"rows": rows},
    )


def _s0r(ctx: StepContext) -> StepResult:
    scope = RunScope(host="SAE0011", polestar_source="polestar", polestar_server="SAE0011",
                     targets=[], host_was=["A2", "A1"])
    insts = [{"instance": "A1", "verdict": "normal"}, {"instance": "A2", "verdict": "normal"}]
    return StepResult(
        verdict="normal", verdict_source="apm_gateway",
        table={"columns": ["instance", "verdict"], "rows": [["A1", "normal"], ["A2", "normal"]]},
        data={"scope": scope.to_dict(), "host": "SAE0011", "instances": insts},
        artifacts={"scope": scope},
    )


#: S2 프로세스 CPU — 사슬(S3a·S3b)이 돌려면 R-E가 WAS(≥80%)로 봐야 한다.
S2_CPUS: dict[str, float] = {"A1": 88.0, "A2": 6.0}


def _s2(ctx: StepContext) -> StepResult:
    cpus = S2_CPUS
    return StepResult(
        verdict="normal",
        table={"columns": ["instance", "metric", "max"],
               "rows": [[n, "process_cpu_pct", cpus[n]] for n in ctx.targets]},
        data={"targets": {n: {"trend_summary": {"process_cpu_pct": {"max": cpus[n]}}}
                          for n in ctx.targets}},
    )


def _plain(ctx: StepContext) -> StepResult:
    return StepResult(table={"columns": ["n"], "rows": []})


FAKES = {
    "host.usage_trend": _calling("polestar", "polestar_metric_trend",
                                 {"server_name": "SAE0011", "kind": "cpu",
                                  "reference_time": "2026-10-19T10:10:00",
                                  "lookback_minutes": 40}, _s4),
    "host.process_top": _calling("polestar", "polestar_process_snapshot",
                                 {"server_name": "SAE0011", "sort": "cpu", "top_n": 10}, _s6),
    "scope.host_to_was": _calling("apm", "apm_instance_map", {"hostname": "SAE0011"}, _s0r),
    "apm.runtime_and_pool": _calling("apm", "apm_runtime_health",
                                     {"instance_name": "A1", "metrics": ["process_cpu"]}, _s2),
    "xsource.related_alarms": _calling("polestar", "polestar_incident_alarms",
                                       {"server_name": "SAE0011"}, _plain),
    "change.recent": _calling("polestar", "polestar_change_history",
                              {"server_name": "SAE0011"}, _plain),
}


def _run_dispatcher(monkeypatch: pytest.MonkeyPatch, llm: Any) -> tuple[Any, list[dict]]:
    for action, fn in FAKES.items():
        monkeypatch.setitem(steps_mod.STEP_LIBRARY, action, fn)
    caller = Caller()
    job = make_job(PAYLOAD, "inv-w20v")
    job.activity = ActivityLog(masker=mask_activity_text)
    snaps: list[dict] = []

    # 부분 브리핑은 대입될 때마다 기록한다 — 도구 호출 시점 표본은 병렬 단계([S3b,S5,S7])
    # 완료 순서에 따라 S3b가 빠질 수 있어 픽스처가 비결정이 된다(팀 리드 교정).
    class _Recording(type(job)):  # type: ignore[misc]
        def __setattr__(self, key: str, value: Any) -> None:
            if key == "partial_briefing" and value:
                snaps.append(json.loads(json.dumps(value)))
            super().__setattr__(key, value)

    job.__class__ = _Recording
    d = build(make_settings(), caller, llm=llm)
    d(job)
    d.wait_workers(30)
    return job, snaps


@pytest.fixture(scope="module")
def run(request: pytest.FixtureRequest) -> dict:
    mp = pytest.MonkeyPatch()
    try:
        job, snaps = _run_dispatcher(mp, FakeLLM("CPU가 높게 유지되고 있다."))
    finally:
        mp.undo()
    events = job.activity.since(0)["events"]
    return {"job": job, "events": events, "snaps": snaps}


def _by_kind(events: list[dict], kind: str) -> dict[str, dict]:
    return {e["step"]: e for e in events if e["kind"] == kind and e.get("engine") == "playbook"}


# ── 1. 실제 이벤트 모양 ───────────────────────────────────────────────────────


def test_hostcpu_v2_chain_runs_and_events_carry_sentences(run: dict) -> None:
    job, events = run["job"], run["events"]
    assert job.status == "done", job.summary()
    assert job.briefing["playbook"]["id"] == "host.cpu"
    starts, ends, skips = (_by_kind(events, k) for k in ("step_start", "step_end", "step_skip"))
    for sid in ("S4", "S6", "S0r", "S2", "S3a", "S3b", "S5", "S7"):
        assert starts[sid].get("because"), sid
        assert ends[sid].get("finding"), sid
    assert set(skips) == {"S1"}
    assert skips["S1"]["label"] == "S1 WAS 골든 시그널"
    assert "==" not in skips["S1"]["because"] and "S0r." not in skips["S1"]["because"]
    tools = [e for e in events if e["kind"] in ("tool_start", "tool_end")]
    assert tools and all(e.get("phrase") for e in tools)
    # 실행 중 SQL — 리터럴·비밀값이 문장 키(label·finding·because·phrase)에 없다
    sentences = json.dumps(
        [{k: e.get(k) for k in ("label", "finding", "because", "phrase")} for e in events],
        ensure_ascii=False,
    )
    assert SECRET not in sentences and "'kim'" not in sentences and "1500" not in sentences


def test_active_detail_excerpt_has_no_sql_literals(run: dict) -> None:
    """게이트웨이가 리터럴을 이미 가렸다는 전제 없이(계약 §3 「다시 가린다」) 발췌도 가려지는가."""
    ex = [e.get("excerpt") or "" for e in run["events"] if e.get("tool") == "apm_active_detail"]
    assert ex and not any("'kim'" in x or "1500" in x for x in ex)


def test_partial_sections_carry_finding_opinion_and_skip(run: dict) -> None:
    snaps = run["snaps"]
    assert snaps
    last = snaps[-1]["playbook"]["sections"]
    by = {s["step"]: s for s in last}
    assert by["S1"]["status"] == "skipped" and by["S1"]["because"]
    assert set(by["S1"]) == {"step", "action", "title", "status", "because"}
    assert all(s.get("finding") for s in last if s["status"] != "skipped")
    with_op = [s for s in last if "opinion" in s]
    assert with_op and all(s["opinion"]["basis"] == "이 단계까지의 근거" for s in with_op)
    # 순서 = 플레이북 순서
    order = ["S4", "S6", "S0r", "S1", "S2", "S3a", "S3b", "S5", "S7"]
    seen = [s["step"] for s in last]
    assert seen == [s for s in order if s in seen]


def test_final_briefing_sections_have_no_finding(run: dict) -> None:
    """종결 브리핑 섹션에는 `finding`이 없다 — 화면은 `step_end` 이벤트·보관 요약에서 읽는다."""
    secs = run["job"].briefing["playbook"]["sections"]
    assert all("finding" not in s for s in secs)


def test_write_fixture_for_main_ui_test(run: dict) -> None:
    """본체 화면 종단 테스트 입력 — 이벤트·마지막 부분 브리핑·종결 브리핑(시각만 정규화)."""
    events = []
    for e in run["events"]:
        e = dict(e)
        e["at"] = 1792395000 + e["seq"]  # 결정적 시각
        e.pop("elapsed_ms", None)
        events.append(e)
    final = run["job"].briefing
    payload = {
        "events": events,
        "partial": run["snaps"][-1],
        "final_playbook": {
            "sections": [
                {k: v for k, v in s.items() if k != "elapsed_ms"}
                for s in final["playbook"]["sections"]
            ],
            "synthesis": final["playbook"].get("synthesis"),
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    if os.environ.get("W20_VERIFY_WRITE") == "1" or not FIXTURE.exists():
        FIXTURE.write_text(text + "\n", encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # 구조(단계·종류·키 집합)만 비교 — 문장은 본체 테스트가 본다
    def shape(p: dict) -> list:
        kinds = ("step_start", "step_end", "step_skip", "tool_start", "tool_end")
        return sorted(
            (e["kind"], e.get("step") or "", tuple(sorted(e)))
            for e in p["events"] if e["kind"] in kinds
        )

    assert shape(stored) == shape(json.loads(text))


# ── 2. 표 밖 수치 (수용 22) ──────────────────────────────────────────────────

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _table_numbers(section: dict) -> set[str]:
    out: set[str] = set()
    for row in section["table"]["rows"]:
        for cell in row:
            out.update(_NUM.findall(str(cell)))
    return out


def test_active_services_finding_numbers_are_in_table(run: dict) -> None:
    """S3a 결과 한 줄의 수치가 그 단계 표(columns)에 있는가 — `sql_count`는 표 열이 아니다."""
    sec = next(s for s in run["job"].briefing["playbook"]["sections"] if s["step"] == "S3a")
    ends = _by_kind(run["events"], "step_end")
    finding = ends["S3a"]["finding"]
    nums = set(_NUM.findall(finding.split("·")[0])) - {"1", "3"}  # 순위·행 수 제외
    missing = nums - _table_numbers(sec)
    assert not missing, f"표 밖 수치 {missing} — finding={finding!r} cols={sec['table']['columns']}"


# ── 3. 실행 중 SQL 리터럴 가림 (보안) ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "leak"),
    [
        ("SELECT * FROM t WHERE a = 'it''s secret'", "secret"),
        ("SELECT * FROM t WHERE id IN (101, 202, -303)", "202"),
        ("SELECT * FROM t WHERE amt > -4.5", "4.5"),
        ("SELECT * FROM t WHERE x = X'DEADBEEF'", "DEADBEEF"),
    ],
)
def test_mask_active_sql_covers_common_literals(sql: str, leak: str) -> None:
    assert leak not in mask_active_sql(sql)


@pytest.mark.parametrize(
    ("sql", "leak"),
    [
        ("SELECT * FROM t WHERE a = 'it\\'s kim secret'", "kim secret"),  # MySQL \\' 이스케이프
        ("SELECT * FROM t WHERE a = \"kim-secret\"", "kim-secret"),  # MariaDB 큰따옴표 문자열
        ("SELECT * FROM t WHERE h = 0xDEADBEEF", "DEADBEEF"),  # 16진 리터럴
        ("SELECT * FROM t WHERE v = 1e10", "1e10"),  # 지수 표기
        ("SELECT * FROM t WHERE v = .5", ".5"),  # 앞자리 0 생략
        ("SELECT * FROM t WHERE p = $$kim secret$$", "kim secret"),  # PG 달러 인용
    ],
)
def test_mask_active_sql_known_gaps(sql: str, leak: str) -> None:
    """리터럴 가림이 놓치는 꼴 — 실패하면(=가려지면) 지적 해소."""
    assert leak not in mask_active_sql(sql)


# ── 4. 단계 끝 해석 실행기 — 경합·종료 후 통지 ───────────────────────────────


def _req(step: str) -> StepNarration:
    return StepNarration(
        step=step,
        section={"step": step, "title": "t", "verdict": "degraded",
                 "table": {"columns": ["p95_ms"], "rows": [[6200]]}},
        focus="p95", max_sentences=3, readings={}, prior_findings=(),
    )


def test_stream_no_notify_after_close_even_if_llm_returns_late() -> None:
    gate = threading.Event()

    def slow(messages):  # noqa: ANN001
        gate.wait(2)
        return "p95 6200ms로 지연이 있다."

    stream = StepOpinionStream(PlaybookNarrator(slow, NarratorLimits(step_timeout_s=5)))
    got: list = []
    stream.submit(_req("S1"), got.append)
    stream.close()
    gate.set()
    time.sleep(0.2)
    assert got == []


def test_stream_concurrent_notifications_with_shared_lock_no_deadlock() -> None:
    """통지 콜백이 바깥 락을 잡고, 같은 바깥 락을 쥔 스레드가 제출해도 교착이 없다."""
    outer = threading.Lock()
    seen: list[str] = []

    def on_done_for(sid: str):  # noqa: ANN202
        def cb(op):  # noqa: ANN001
            with outer:
                seen.append(sid)
        return cb

    stream = StepOpinionStream(PlaybookNarrator(FakeLLM("p95 6200ms로 지연이 있다."),
                                                NarratorLimits(max_workers=4)))
    for i in range(20):
        with outer:
            pass
        stream.submit(_req(f"S{i}"), on_done_for(f"S{i}"))
    out = stream.drain(timeout=5)
    assert len(out) == 20 and sorted(seen) == sorted(f"S{i}" for i in range(20))


def test_capped_stream_notifies_inside_submit_holding_lock() -> None:
    """상한 초과 단계는 submit 안(스트림 락 보유)에서 통지 — 콜백이 바깥 락을 잡아도 제출자가
    그 바깥 락을 쥐고 있지 않으면 안전(디스패처는 락 밖에서 제출 — `investigation_dispatcher`)."""
    stream = StepOpinionStream(PlaybookNarrator(FakeLLM("p95 6200ms."),
                                                NarratorLimits(max_step_calls=1)))
    got: list = []
    stream.submit(_req("S1"))
    assert stream.submit(_req("S2"), got.append) is False
    assert got and "상한" in got[0].dropped_reason
    stream.drain(timeout=5)


# ── 5. 실행 기록 off — 새 키 0 ────────────────────────────────────────────────


def test_activity_off_hostcpu_has_no_flow_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for action, fn in FAKES.items():
        monkeypatch.setitem(steps_mod.STEP_LIBRARY, action, fn)
    caller = Caller()
    job = make_job(PAYLOAD, "inv-w20v-off")
    snaps: list[dict] = []

    def snap_caller(calls):  # noqa: ANN001
        if job.partial_briefing:
            snaps.append(json.loads(json.dumps(job.partial_briefing)))
        return caller(calls)

    d = build(make_settings(), snap_caller, llm=FakeLLM())
    d(job)
    d.wait_workers(30)
    assert job.status == "done"
    for snap in snaps:
        for s in snap["playbook"]["sections"]:
            assert {"finding", "opinion", "because"}.isdisjoint(s)
            assert s["status"] != "skipped"
    assert "activity" not in job.summary()


def test_summarize_is_deterministic_and_llm_free() -> None:
    sec = {"status": "ok", "verdict": "degraded", "table": {"columns": [], "rows": []},
           "data": {"findings": {"cpu": {"peak_value": 96.0, "is_anomalous": True}}}}
    assert summarize("host.usage_trend", sec, {}) == summarize("host.usage_trend", sec, {})


# ── 6. 계획서 예시 수치(호스트 96% · A1 74~78%)에서 사슬이 이어지는가 (수용 21) ──────


@pytest.mark.xfail(strict=True, reason="지적 — R-E가 WAS<80%를 none·other_process로 읽음")
@pytest.mark.parametrize("a1", [74.0, 78.0])
def test_plan_example_numbers_reach_active_sql(monkeypatch: pytest.MonkeyPatch, a1: float) -> None:
    monkeypatch.setitem(S2_CPUS, "A1", a1)
    job, _ = _run_dispatcher(monkeypatch, FakeLLM("CPU가 높다."))
    secs = {s["step"]: s for s in job.briefing["playbook"]["sections"]}
    assert secs["S3a"]["status"] == "ok" and secs["S3b"]["status"] == "ok"


def test_healthy_was_chain_s3a_because_says_previous_step_missing(run: dict) -> None:
    """WAS 정상(S1 생략) 사용자 예시에서 S3a는 UNKNOWN 규칙으로만 돈다 — 「왜」는 판정 못 한 조건과
    판독 사유만 말하고, 확인되지 않은 작성자 문장(「CPU를 WAS가 쓰고 있어」)을 단정하지 않는다."""
    because = _by_kind(run["events"], "step_start")["S3a"]["because"]
    assert "판정할 수 없어" in because and "(사유: TPS 결손" in because
    assert "WAS가 쓰고 있어" not in because and "앞 단계 결손" not in because
