"""plans/187 W20b — 플레이북 보강(G-49): host.cpu·host.process v2 사슬(S2 → S3a → S3b) · 신규
액션 `apm.active_sql`.

S3a·S3b 밖의 스텝은 가짜 스텝(데이터 모양은 실제 스텝과 같다)으로 판독 R-E `cpu_owner`를
원하는 값으로 만들고, S3a(`apm.active_services`)·S3b(`apm.active_sql`)는 실제 스텝 함수를 가짜 APM
배치 콜러블로 돌린다. 실 LLM·실 제니퍼 호출 0.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sre_agent.application.playbook_runner import plan_stages, run_playbook
from sre_agent.application.playbook_steps import (
    ACTION_TITLES,
    ACTIVE_SQL_MAX_BYTES,
    APM_ACTIVE_DETAIL,
    APM_ACTIVE_SERVICES,
    STEP_LIBRARY,
    RunScope,
    RunWindows,
    StepContext,
    StepResult,
    active_sql,
    mask_active_sql,
)
from sre_agent.domain.playbook import Playbook
from sre_agent.infrastructure.playbook_loader import load_playbooks

ALARM = "2026-10-20T10:00:00"
NOW = datetime(2026, 10, 20, 10, 20, 0)
PAYLOAD = {"event": {"alarmTime": "20261020100000", "serverName": "host-a", "dbId": "polestar"}}

CHAIN_PLAYBOOKS = ("host.cpu", "host.process")
ORDERS = {
    "host.cpu": ["S4", "S6", "S0r", "S1", "S2", "S3a", "S3b", "S5", "S7", "S9"],
    "host.process": [
        "S4r", "S4", "S6", "S5", "S5a", "S0r", "S1", "S2", "S3a", "S3b", "S7", "S9",
    ],
}
STAGES = {
    "host.cpu": [["S4", "S6"], ["S0r"], ["S1", "S2"], ["S3a"], ["S3b", "S5", "S7"]],
    "host.process": [
        ["S4r", "S4", "S6", "S5", "S5a"], ["S0r"], ["S1", "S2"], ["S3a"], ["S3b", "S7"],
    ],
}

SECRET_SQL = (
    "SELECT * FROM orders WHERE customer='abc' AND amount > 123 AND rate = -4.5 "
    "/* password=hunter2 */"
)


# ── 가짜 APM 콜러블 ─────────────────────────────────────────────────────────


class FakeApm:
    """`apm_active_services`·`apm_active_detail`만 답하는 배치 콜러블(호출 기록)."""

    def __init__(self, services: list[dict], sql_by_tx: dict[str, str | None]) -> None:
        self.services = services
        self.sql_by_tx = sql_by_tx
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, calls):
        out = []
        for tool, args in calls:
            self.calls.append((tool, dict(args)))
            if tool == APM_ACTIVE_SERVICES:
                body = {"rows": self.services, "queried_at": "2026-10-20T10:19:00"}
            elif tool == APM_ACTIVE_DETAIL:
                tx = str(args.get("txid"))
                if tx not in self.sql_by_tx:
                    body = {"error": "SOURCE_UNAVAILABLE", "reason": "요청이 이미 끝났다"}
                else:
                    body = {
                        "rows": [{"txid": tx, "sql": self.sql_by_tx[tx]}],
                        "queried_at": "2026-10-20T10:19:30",
                    }
            else:
                body = {"error": "UNRECORDED", "reason": tool}
            out.append(json.dumps(body, ensure_ascii=False))
        return out


def _service(tx: str, cpu: int | None, elapsed: int, *, ref: bool = True) -> dict:
    return {
        "instance_name": "was1",
        "application": f"/app/{tx}",
        "cpu_ms": cpu,
        "elapsed_ms": elapsed,
        "sql_count": 3,
        "txid": tx,
        "active_ref": {
            "source_id": "j1",
            "domain_id": 1000,
            "txid": tx,
            "session_id": None,
            "thread_hash": 7,
        }
        if ref
        else None,
    }


SERVICES = [
    _service("101", 900, 5000),
    _service("102", 50, 9000),
    _service("103", 700, 3000),
    _service("104", 300, 2000),
    _service("105", None, 1000),
]
SQLS = {"101": SECRET_SQL, "103": "SELECT 1", "104": None}


# ── 가짜 스텝(판독 입력 모양) ───────────────────────────────────────────────


def _fixed(result: StepResult):
    def fn(ctx: StepContext) -> StepResult:
        return StepResult(
            status=result.status,
            verdict=result.verdict,
            verdict_source=result.verdict_source,
            data=dict(result.data),
            artifacts=dict(result.artifacts),
        )

    return fn


def _host_to_was(verdict: str, instances: list[str]):
    """역방향 S0′ 가짜 — degraded면 첫 인스턴스만 대상 · 판정 난 인스턴스 전부는 `host_was`."""

    def fn(ctx: StepContext) -> StepResult:
        names = [] if verdict == "no_was" else instances
        targets = (
            [{"instance": names[0], "verdict": "degraded", "verdict_source": "apm_gateway",
              "confidence": None}]
            if verdict == "degraded"
            else []
        )
        scope = RunScope(
            host="host-a",
            polestar_source="polestar",
            polestar_server="host-a",
            targets=targets,
            host_was=list(names),
        )
        return StepResult(
            status="ok",
            verdict=verdict,
            data={"scope": scope.to_dict(), "host": "host-a", "instances": []},
            artifacts={"scope": scope},
        )

    return fn


def _seen(seen: dict, action: str, result: StepResult):
    """스텝이 받은 대상(`ctx.targets`)을 기록하고 고정 결과를 돌려준다."""
    inner = _fixed(result)

    def fn(ctx: StepContext) -> StepResult:
        seen[action] = list(ctx.targets)
        return inner(ctx)

    return fn


def _runtime(cpus: dict[str, float], seen: dict):
    """S2 가짜 — 받은 대상마다 프로세스 CPU 추세 요약(실제 스텝의 data 모양)."""

    def fn(ctx: StepContext) -> StepResult:
        seen["apm.runtime_and_pool"] = list(ctx.targets)
        return StepResult(
            verdict="normal",
            data={"targets": {
                n: {"trend_summary": {"process_cpu_pct": {"max": cpus[n]}}} for n in ctx.targets
            }},
        )

    return fn


def _library(*, was: str = "degraded", host_peak: float = 95.0, was_cpu: float = 88.0,
             tps: float = 200.0, cpus: dict[str, float] | None = None,
             seen: dict | None = None) -> dict:
    cpus = cpus or {"was1": was_cpu}
    seen = {} if seen is None else seen
    s4 = StepResult(
        data={"findings": {"cpu": {"peak_value": host_peak, "is_anomalous": True}}}
    )
    s6 = StepResult(data={"rows": [{"name": "java", "pcpu": max(cpus.values())}]})
    first = next(iter(cpus))
    s1 = StepResult(
        verdict="degraded",
        data={"targets": {first: {"incident": {"tps": tps},
                                  "baselines": {"prev_day": {"tps": 100.0}}}}},
    )
    return {
        **STEP_LIBRARY,
        "host.usage_trend": _fixed(s4),
        "host.process_top": _fixed(s6),
        "scope.host_to_was": _host_to_was(was, list(cpus)),
        "apm.golden_signals": _fixed(s1),
        "apm.runtime_and_pool": _runtime(cpus, seen),
        "xsource.related_alarms": _seen(seen, "xsource.related_alarms", StepResult()),
        "host.resource_status": _fixed(StepResult()),
        "host.alarm_history": _fixed(StepResult()),
        "change.recent": _seen(seen, "change.recent", StepResult()),
    }


def _playbook(pid: str = "host.cpu") -> Playbook:
    return next(p for p in load_playbooks().playbooks if p.id == pid)


def _run(apm: FakeApm, pid: str = "host.cpu", **kw):
    return run_playbook(
        PAYLOAD,
        _playbook(pid),
        matched_by="test",
        polestar=None,
        apm=apm,
        now=lambda: NOW,
        max_workers=1,
        steps=_library(**kw),
    )


def _by_id(run) -> dict[str, StepResult]:
    return {s.step: s for s in run.sections}


# ── host.cpu · host.process v2 사슬 ────────────────────────────────────────

chain = pytest.mark.parametrize("pid", CHAIN_PLAYBOOKS)


@chain
def test_v2_order_and_version(pid: str) -> None:
    pb = _playbook(pid)
    assert pb.version == 4  # W20b 사슬 2 → W21 P3 S0r 단계 타임아웃 3 → W22 S0r 120s 4
    assert next(s for s in pb.steps if s.id == "S0r").timeout_s == 120
    assert [s.id for s in pb.steps] == ORDERS[pid]
    acts = {s.id: s.action for s in pb.steps}
    assert (acts["S2"], acts["S3a"], acts["S3b"]) == (
        "apm.runtime_and_pool", "apm.active_services", "apm.active_sql",
    )
    for sid in ("S2", "S3a", "S3b"):
        step = next(s for s in pb.steps if s.id == sid)
        assert step.narrate is not None and step.narrate.why


@chain
def test_was_load_runs_s2_s3a_s3b_and_masks_sql(pid: str) -> None:
    apm = FakeApm(SERVICES, SQLS)
    run = _run(apm, pid)
    sec = _by_id(run)
    assert run.readings is not None and run.readings.values["cpu_owner"] == "was_load"
    assert [sec[s].status for s in ("S2", "S3a", "S3b")] == ["ok", "ok", "ok"]
    # S3a는 R-E가 판정한 인스턴스(범위 첫 대상)를 본다 · n=5
    assert (APM_ACTIVE_SERVICES, {"instance_name": "was1", "n": 5}) in apm.calls
    # S3b — S3a cpu_ms 상위 3건(101·103·104)만 · active_ref 그대로(None 값 제외)
    detail = [a for t, a in apm.calls if t == APM_ACTIVE_DETAIL]
    assert [a["txid"] for a in detail] == ["101", "103", "104"]
    assert detail[0] == {"source_id": "j1", "domain_id": 1000, "txid": "101", "thread_hash": 7}
    s3b = sec["S3b"]
    assert s3b.title == "실행 중 SQL"
    assert s3b.table is not None
    assert s3b.table["columns"] == ["instance", "application", "cpu_ms", "elapsed_ms", "sql"]
    rows = s3b.data["rows"]
    assert set(rows[0]) == {"instance", "application", "txid", "cpu_ms", "elapsed_ms", "sql"}
    sql = rows[0]["sql"]
    assert "'abc'" not in sql and "123" not in sql and "4.5" not in sql
    assert "customer=?" in sql and "> ?" in sql
    assert "hunter2" not in sql
    assert rows[1]["sql"] == "SELECT ?"
    assert rows[2]["sql"] is None
    assert s3b.data["queried_at"] == "2026-10-20T10:19:30"
    assert s3b.badges and s3b.badges[0].startswith("현재값")


@chain
def test_no_was_skips_s2_s3a_s3b(pid: str) -> None:
    apm = FakeApm(SERVICES, SQLS)
    sec = _by_id(_run(apm, pid, was="no_was"))
    for sid in ("S2", "S3a", "S3b"):
        assert sec[sid].status == "skipped", sid
        assert (sec[sid].reason or "").startswith("when_false"), sid
    assert apm.calls == []


@pytest.mark.parametrize(
    ("kw", "owner"),
    [
        ({"host_peak": 95.0, "was_cpu": 30.0}, "other_process"),  # 호스트 − WAS ≥ 20%p
        ({"host_peak": 95.0, "was_cpu": 79.0}, "none"),           # WAS CPU < 80%
    ],
)
@chain
def test_other_cpu_owner_skips_s3a_s3b(pid: str, kw: dict, owner: str) -> None:
    apm = FakeApm(SERVICES, SQLS)
    run = _run(apm, pid, **kw)
    sec = _by_id(run)
    assert run.readings is not None and run.readings.values["cpu_owner"] == owner
    assert sec["S2"].status == "ok"
    assert sec["S3a"].status == "skipped" and sec["S3b"].status == "skipped"
    assert apm.calls == []


@chain
def test_healthy_was_cpu_owner_chain(pid: str) -> None:
    """사용자 예시 — WAS A2·A1 모두 응답 정상(degraded 0) · A1 프로세스 CPU 최대.

    S2는 이 호스트 WAS 전부(host_was)를 보고, R-E·S3a는 프로세스 CPU 최대 A1을 본다. S1(골든)은
    이상 인스턴스가 없어 생략되므로 부하/로직 구분 근거(TPS)가 없어 cpu_owner는 판단 보류(None)
    — 실행기 규칙(UNKNOWN은 실행)으로 S3a·S3b가 돈다. S5·S7의 대상은 종전처럼 비어 있다.
    """
    seen: dict = {}
    apm = FakeApm(SERVICES, SQLS)
    run = _run(apm, pid, was="normal", cpus={"A2": 40.0, "A1": 88.0}, seen=seen)
    sec = _by_id(run)
    assert sec["S1"].status == "skipped"
    assert seen["apm.runtime_and_pool"] == ["A2", "A1"]
    assert run.readings is not None and run.readings.values["cpu_owner"] is None
    ev = run.readings.evidence["cpu_owner"]
    assert ev.values["was_cpu_max"] == 88.0 and "TPS" in (ev.reason or "")
    assert [sec[s].status for s in ("S2", "S3a", "S3b")] == ["ok", "ok", "ok"]
    assert "when 판정 불가" in (sec["S3a"].reason or "")
    assert (APM_ACTIVE_SERVICES, {"instance_name": "A1", "n": 5}) in apm.calls
    assert seen["xsource.related_alarms"] == [] and seen["change.recent"] == []


def test_scope_to_dict_excludes_host_was() -> None:
    scope = RunScope(host="h", host_was=["A1"])
    assert "host_was" not in scope.to_dict()


@chain
def test_plan_stages_puts_s3a_after_cpu_owner_sources(pid: str) -> None:
    stages = [[s.id for s in stage] for stage in plan_stages(_playbook(pid))]
    pos = {sid: i for i, stage in enumerate(stages) for sid in stage}
    assert pos["S3a"] > max(pos["S2"], pos["S4"], pos["S6"], pos["S1"])
    assert pos["S3b"] > pos["S3a"]
    assert stages == STAGES[pid]


# ── apm.active_sql 단위 ─────────────────────────────────────────────────────


def _ctx(apm, prior: dict[str, StepResult]) -> StepContext:
    windows = RunWindows(
        alarm_time=ALARM,
        reference_time="2026-10-20T10:10:00",
        lookback_minutes=40,
        incident_start="2026-10-20T09:30:00",
        incident_end="2026-10-20T10:10:00",
    )
    return StepContext(
        payload=PAYLOAD,
        windows=windows,
        scope=RunScope(targets=[{"instance": "was1"}]),
        args={},
        targets=["was1"],
        polestar=None,
        apm=apm,
        now=lambda: NOW,
        prior=prior,
    )


def _s3a(rows: list[dict], status: str = "ok") -> dict[str, StepResult]:
    return {
        "S3a": StepResult(
            step="S3a", action="apm.active_services", status=status, data={"rows": rows}
        )
    }


def test_active_sql_registered() -> None:
    assert STEP_LIBRARY["apm.active_sql"] is active_sql
    assert ACTION_TITLES["apm.active_sql"] == "실행 중 SQL"


def test_mask_active_sql_literals_secrets_and_cap() -> None:
    out = mask_active_sql("SELECT a FROM t1 WHERE b = 'it''s' AND c IN (1, 2.5) AND t1.c2 = x")
    assert out == "SELECT a FROM t1 WHERE b = ? AND c IN (?, ?) AND t1.c2 = x"
    assert "hunter2" not in mask_active_sql("SELECT 1 -- password=hunter2")
    long = mask_active_sql("SELECT " + "가" * 2000)
    assert len(long.encode("utf-8")) <= ACTIVE_SQL_MAX_BYTES and long.endswith("…")


def test_active_sql_missing_paths() -> None:
    apm = FakeApm([], SQLS)
    assert active_sql(_ctx(None, _s3a(SERVICES))).status == "missing"
    no_prior = active_sql(_ctx(apm, {}))
    assert no_prior.status == "missing" and "apm.active_services" in (no_prior.reason or "")
    assert active_sql(_ctx(apm, _s3a(SERVICES, status="missing"))).status == "missing"
    no_ref = active_sql(_ctx(apm, _s3a([_service("101", 9, 9, ref=False)])))
    assert no_ref.status == "missing" and "active_ref" in (no_ref.reason or "")
    empty = active_sql(_ctx(apm, _s3a([])))
    assert empty.status == "ok" and empty.data["rows"] == []
    assert apm.calls == []


def test_active_sql_partial_and_total_failure() -> None:
    apm = FakeApm([], {"101": "SELECT 1"})  # 103·104는 이미 끝남(오류)
    res = active_sql(_ctx(apm, _s3a(SERVICES)))
    assert res.status == "ok" and [r["txid"] for r in res.data["rows"]] == ["101"]
    assert res.missing == [APM_ACTIVE_DETAIL] and "요청이 이미 끝났다" in (res.reason or "")
    gone = active_sql(_ctx(FakeApm([], {}), _s3a(SERVICES)))
    assert gone.status == "missing" and gone.missing == [APM_ACTIVE_DETAIL]
