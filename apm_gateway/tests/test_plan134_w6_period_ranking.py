"""plans/134 W6 A-4 — `apm_fleet` 기간 순위(게이트웨이 쪽).

- 기간 인자가 없으면 종전 실시간 순위 그대로(`window_mode` 칸 없음 · 시 단위 통계 0회).
- 기간 순위 = 범위 안 인스턴스 **전부**의 시 단위 애플리케이션 통계를 모은 뒤 정렬(독립 오라클 —
  합성 원자료에서 바로 계산한 가중 평균으로 `heapq` 상위 n) · 인스턴스마다 1호출(인스턴스 1개씩)
  · 시 경계로 넓힌 조회 구간 · 호출 계획 신고 = 실제 호출 수.
- 가중 평균(Σ총 응답시간 ÷ Σ호출 — 평균의 평균 아님) · 기간 tps 정의 · 통계 행 없는 인스턴스는
  순위 밖(0으로 채우지 않음) · 가중치 없는 인스턴스 고지.
- 부분 실패(인스턴스·도메인·인스턴스 목록) = 잠정 순위 + 누락 표시 · 전부 실패 = 오류.
- 기간 통계가 없는 지표 = 현재값 순위 + `[한계]` + `window_mode="current"`.
- MCP 경계(도구 인자 → 봉투).

합성 Open API(`fleet_fake`)에 시 단위 통계 경로만 더한다 — 외부 네트워크 0 · 실 제니퍼 0.
"""

from __future__ import annotations

import heapq
import json
import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from apm_gateway.application import fleet_tools as fleet_module
from apm_gateway.application.fleet_tools import (
    ALL_RANKING_METRICS,
    PERIOD_RANKING_METRICS,
    RANKING_METRICS,
    FleetTools,
)
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import INVALID_ARGUMENT, ApmError
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import NOW_MS, NOW_S
from fleet_fake import NOT_CONNECTED, FakeFleet

from apm_gateway.config import JobConfig, load_config

T0 = NOW_MS  # 정시(UTC·KST 모두)
HOUR = 3_600_000
STATUS = "/api/status/application"
REALTIME = "/api/realtime/instance"
GW_ROOT = Path(__file__).resolve().parents[1]


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, ZoneInfo("Asia/Seoul")).isoformat()


def _at(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


class PeriodFake(FakeFleet):
    """`FakeFleet` + 시 단위 애플리케이션 통계(인스턴스별 행 · 인스턴스 칸 없음 — 실 스키마와 같다).
    `app_rows[(host, iid)]`가 없으면 빈 결과다. 인스턴스를 여럿 주면(쉼표) 시험 실패로 돌려준다."""

    def __init__(self, domains: int, *, seed: int = 11, **kw) -> None:
        super().__init__(domains, **kw)
        rnd = random.Random(seed)
        self.app_rows: dict[tuple[str, int], list[dict]] = {}
        self.app_fail: set[tuple[str, int]] = set()
        self.instance_fail: set[tuple[str, int]] = set()
        for host in self.hosts:
            for d in self.domains:
                for k in range(self.per_domain):
                    iid = d * 100 + k
                    if (d * 3 + k) % 17 == 0:  # 통계 행 없음 → 순위 밖
                        continue
                    rows = []
                    for a in range(rnd.randint(1, 3)):
                        calls = rnd.randint(1, 500)
                        total = rnd.randint(1, 10**7)
                        rows.append(
                            {
                                "name": f"/app/{a}",
                                "calls": calls,
                                "failures": rnd.randint(0, calls),
                                "responseTime": total / calls,
                                "totalResponseTime": total,
                                "maxResponseTime": rnd.randint(1, 90_000),
                            }
                        )
                    self.app_rows[(host, iid)] = rows

    def status_calls(self) -> list[tuple[str, dict[str, str]]]:
        return [(h, q) for h, p, q in self.calls if p == STATUS]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        query = dict(request.url.params)
        if path == STATUS:
            self.calls.append((host, path, query))
            ids = query.get("instance_id", "")
            if not ids.isdigit():
                return httpx.Response(400, json={"exception": {"message": f"bad ids {ids}"}})
            iid = int(ids)
            if (host, iid) in self.app_fail:
                return httpx.Response(500, json=NOT_CONNECTED)
            return httpx.Response(200, json={"result": self.app_rows.get((host, iid), [])})
        domain = int(query.get("domain_id", "0"))
        if path == "/api/instance" and (host, domain) in self.instance_fail:
            self.calls.append((host, path, query))
            return httpx.Response(500, json=NOT_CONNECTED)
        return await super().handler(request)


def _tools(fake: FakeFleet, tmp_path) -> ApmTools:
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool")))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return ApmTools(sources, cfg, clock=lambda: NOW_S)


async def _ready(fake: FakeFleet, tmp_path) -> FleetTools:
    tools = _tools(fake, tmp_path)
    for src in tools.sources:
        await src.resolver.inventory()
    fake.calls.clear()
    return FleetTools(tools)


def _stat(rows: list[dict]) -> dict:
    """오라클 — 합성 원자료에서 바로(구현 코드와 무관)."""
    calls = sum(r["calls"] for r in rows)
    failures = sum(r["failures"] for r in rows)
    weighted = all("totalResponseTime" in r for r in rows if r["calls"])
    total = sum(r.get("totalResponseTime", 0) for r in rows)
    return {
        "calls": calls,
        "failures": failures,
        "failure_rate": failures / calls if calls else None,
        "response_time_avg_ms": total / calls if calls and weighted else None,
        "max_response_time_ms": max((r["maxResponseTime"] for r in rows), default=None),
    }


def _oracle(fake: PeriodFake, metric: str, n: int, *, desc: bool, skip=frozenset()):
    """값 순 상위 n — 동점은 계약의 순서(소스 설정 순 · 도메인 · 인스턴스)로 가른다."""
    pool = []
    for (host, iid), rows in fake.app_rows.items():
        if (host, iid) in skip or not rows:
            continue
        value = _stat(rows)[metric]
        if value is not None:
            pool.append((value, fake.hosts.index(host), host.split(".")[0], iid // 100, iid))
    pick = heapq.nlargest if desc else heapq.nsmallest
    tie = (lambda x: (x[0], -x[1], -x[3], -x[4])) if desc else (lambda x: x[:2] + x[3:])
    return [(sid, d, iid, v) for v, _r, sid, d, iid in pick(n, pool, key=tie)]


def _got(out: dict) -> list[tuple]:
    return [(r["source_id"], r["domain_id"], r["instance_id"], r["value"]) for r in out["rows"]]


# ── 실시간 순위 그대로 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_window_keeps_realtime_ranking(tmp_path):
    fake = PeriodFake(5)
    fleet = await _ready(fake, tmp_path)
    out = await fleet.ranking(n=3)
    assert "window_mode" not in out["summary"] and "window" not in out
    assert set(out["summary"]) == {
        "metric", "order", "domains_total", "domains_ok", "domains_failed",
        "instances_total", "instances_ranked", "instances_unranked",
    }
    assert out["limits"][0] == "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다"
    assert fake.status_calls() == []
    assert len([1 for _h, p, _q in fake.calls if p == REALTIME]) == 10
    with pytest.raises(ApmError) as e:  # 기간 전용 지표를 기간 없이 — HTTP 0회
        fake.calls.clear()
        await fleet.ranking(metric="calls")
    assert e.value.code == INVALID_ARGUMENT and "기간 순위 전용" in e.value.reason
    assert fake.calls == []


def test_metric_sets():
    assert RANKING_METRICS[:2] == PERIOD_RANKING_METRICS[:2]  # 같은 이름 = 같은 뜻(기간 정의)
    assert set(PERIOD_RANKING_METRICS) <= set(ALL_RANKING_METRICS)
    assert ALL_RANKING_METRICS[len(RANKING_METRICS):] == (
        "calls", "failures", "failure_rate", "max_response_time_ms",
    )


# ── 기간 순위 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_period_ranking_collects_all_instances_then_sorts(tmp_path, monkeypatch):
    fake = PeriodFake(40, per_domain=3)
    fleet = await _ready(fake, tmp_path)
    planned: list[int] = []
    monkeypatch.setattr(fleet_module, "expect_calls", planned.append)
    window = {"reference_time": _iso(T0 + 20 * 60_000), "lookback_minutes": 90}
    desc = await fleet.ranking(n=10, **window)
    assert _got(desc) == _oracle(fake, "response_time_avg_ms", 10, desc=True)
    total = len(fake.hosts) * 40 * 3
    calls = fake.status_calls()
    # 인스턴스마다 1호출 · 인스턴스 1개씩 · 시 경계로 넓힌 구간 · 실시간 0회 · 계획 = 실제
    assert len(calls) == total and planned == [total]
    assert {(h, int(q["instance_id"])) for h, q in calls} == {
        (h, d * 100 + k) for h in fake.hosts for d in fake.domains for k in range(3)
    }
    assert {(int(q["start_time"]), int(q["end_time"])) for _h, q in calls} == {
        (T0 - 2 * HOUR, T0 + HOUR)
    }
    assert not any(p == REALTIME for _h, p, _q in fake.calls)
    empty = total - sum(1 for rows in fake.app_rows.values() if rows)
    assert empty > 0
    assert desc["summary"] == {
        "metric": "response_time_avg_ms",
        "order": "desc",
        "window_mode": "period",
        "requested": {"start": _at(T0 - 70 * 60_000), "end": _at(T0 + 20 * 60_000)},
        "queried": {"start": _at(T0 - 2 * HOUR), "end": _at(T0 + HOUR), "seconds": 10800.0},
        "domains_total": 80,
        "domains_ok": 80,
        "domains_failed": 0,
        "instances_total": total,
        "instances_ranked": total - empty,
        "instances_unranked": empty,
        "instances_failed": 0,
    }
    assert desc["provisional"] is False and "partial" not in desc and desc["mode"] == "ranking"
    assert desc["window"]["minutes"] == 90
    assert desc["limits"][0] == (
        "[한계] 시 단위 통계 — 기간 순위 구간을 시 경계로 맞췄다"
        f"(요청 {_at(T0 - 70 * 60_000)}~{_at(T0 + 20 * 60_000)}"
        f" → 조회 {_at(T0 - 2 * HOUR)}~{_at(T0 + HOUR)})"
    )
    assert "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다" not in desc["limits"]
    assert any(x.startswith(f"[한계] 통계 행이 없는 인스턴스 {empty}개는 순위 밖이다")
               for x in desc["limits"])
    # 다른 지표·오름차순·전부도 같은 오라클
    fake.calls.clear()
    asc = await fleet.ranking(metric="failure_rate", order="asc", n=7, **window)
    assert _got(asc) == _oracle(fake, "failure_rate", 7, desc=False)
    every = await fleet.ranking(metric="calls", full=True, **window)
    assert _got(every) == _oracle(fake, "calls", total, desc=True)
    assert every["row_count"] == total - empty
    top = every["rows"][0]
    assert top["hostname"] == f"{top['source_id']}-h{top['domain_id']}-{top['instance_id'] % 100}"
    assert set(PERIOD_RANKING_METRICS) <= set(top) and top["domain_name"]


@pytest.mark.asyncio
async def test_weighted_average_tps_and_no_zero_fill(tmp_path):
    fake = PeriodFake(1, hosts=("apm.test",), per_domain=4)
    fake.app_rows = {
        # 평균의 평균은 150 — 호출 수 가중 평균은 (1000 + 6000) ÷ 40 = 175
        ("apm.test", 100): [
            {"name": "/a", "calls": 10, "failures": 1, "responseTime": 100.0,
             "totalResponseTime": 1000, "maxResponseTime": 900},
            {"name": "/b", "calls": 30, "failures": 3, "responseTime": 200.0,
             "totalResponseTime": 6000, "maxResponseTime": 1200},
        ],
        # 총 응답시간 칸 없음 → 평균 계산 불가(순위 밖) · 호출 수 순위에는 든다
        ("apm.test", 101): [
            {"name": "/c", "calls": 5, "failures": 0, "responseTime": 50.0,
             "maxResponseTime": 70},
        ],
        ("apm.test", 102): [
            {"name": "/d", "calls": 2, "failures": 0, "responseTime": 10.0,
             "totalResponseTime": 20, "maxResponseTime": 15},
        ],
        # 103: 통계 행 없음 → 0으로 채우지 않는다
    }
    fleet = await _ready(fake, tmp_path)
    window = {"reference_time": _iso(T0), "lookback_minutes": 60}  # 정시 — 넓히지 않는다
    out = await fleet.ranking(**window)
    assert [(r["instance_id"], r["value"]) for r in out["rows"]] == [(100, 175.0), (102, 10.0)]
    first = out["rows"][0]
    assert first["calls"] == 40 and first["failures"] == 4 and first["failure_rate"] == 0.1
    assert first["max_response_time_ms"] == 1200 and first["application_count"] == 2
    assert first["tps"] == 40 / 3600
    assert out["summary"]["instances_unranked"] == 2 and out["summary"]["instances_ranked"] == 2
    assert out["limits"][0] == (
        f"[한계] 시 단위 통계 — 기간 순위 구간을 시 경계로 맞췄다({_at(T0 - HOUR)}~{_at(T0)})"
    )
    joined = "\n".join(out["limits"])
    assert "평균 응답시간 계산 불가 인스턴스 1개 — 총 응답시간 칸이 없는 통계 행이 있다(가중 평균" \
        " 재료 없음 · 도메인 1 인스턴스 apm-i1-1)" in joined
    assert "통계 행이 없는 인스턴스 1개는 순위 밖이다 — 0으로 채우지 않았다" \
        "(도메인 1 인스턴스 apm-i1-3)" in joined
    assert "기간 tps = 조회 구간 호출 수 ÷ 조회 구간 초(3600초" in joined
    # 오름차순 호출 수 — 행 없는 인스턴스가 0으로 맨 앞에 오지 않는다
    asc = await fleet.ranking(metric="calls", order="asc", **window)
    assert [(r["instance_id"], r["value"]) for r in asc["rows"]] == [(102, 2), (101, 5), (100, 40)]
    tps = await fleet.ranking(metric="tps", lookback_minutes=60, reference_time=_iso(T0))
    assert tps["rows"][0]["value"] == 40 / 3600


@pytest.mark.asyncio
async def test_partial_failures_are_provisional_and_listed(tmp_path):
    fake = PeriodFake(4, per_domain=2)
    fake.instance_fail = {("bank.test", 3)}  # 인스턴스 목록 실패 → 그 도메인은 호출 없이 실패
    fleet = await _ready(fake, tmp_path)
    fake.app_fail = {
        ("bank.test", 100), ("bank.test", 101),  # 도메인 1 전부 실패
        ("common.test", 201),  # 도메인 2 인스턴스 1개만
    }
    window = {"lookback_minutes": 120}  # 지금까지 120분
    out = await fleet.ranking(metric="calls", full=True, **window)
    skip = fake.app_fail | {("bank.test", 300), ("bank.test", 301)}
    assert _got(out) == _oracle(fake, "calls", 100, desc=True, skip=skip)
    assert not any(h == "bank.test" and q["instance_id"] in ("300", "301")
                   for h, q in fake.status_calls())
    s = out["summary"]
    assert (s["domains_total"], s["domains_ok"], s["domains_failed"]) == (8, 6, 2)
    assert s["instances_failed"] == 1
    assert out["provisional"] is True and out["partial"] is True
    assert (
        "[한계] 잠정 순위 — 조회 실패 도메인 2곳(소스 bank · 도메인 1, 소스 bank · 도메인 3)"
        " · 인스턴스 1개(소스 common · 도메인 2 인스턴스 common-i2-1)은 순위에 없습니다"
    ) in out["limits"]
    assert not any("전체 순위" in x for x in out["limits"])
    # 전부 실패 = 오류
    fake.app_fail = {(h, d * 100 + k) for h in fake.hosts for d in fake.domains for k in range(2)}
    with pytest.raises(ApmError):
        await fleet.ranking(**window)


@pytest.mark.asyncio
async def test_metric_without_period_stats_falls_back_to_current(tmp_path):
    fake = PeriodFake(3)
    fleet = await _ready(fake, tmp_path)
    out = await fleet.ranking(metric="heap_used_mb", reference_time=_iso(T0), lookback_minutes=60)
    assert fake.status_calls() == [] and any(p == REALTIME for _h, p, _q in fake.calls)
    assert out["summary"]["window_mode"] == "current"
    assert out["summary"]["requested"]["minutes"] == 60
    assert out["limits"][0] == "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다"
    assert out["limits"][1].startswith(
        "[한계] 지표 heap_used_mb는 기간 통계가 없어 현재값 순위다 — 요청 기간의 값이 아니다"
    )
    with pytest.raises(ApmError) as e:  # 기간 순위 모르는 지표 — 허용값에 두 목록
        await fleet.ranking(metric="nope", lookback_minutes=5)
    assert e.value.code == INVALID_ARGUMENT and "기간 순위: response_time_avg_ms" in e.value.reason


@pytest.mark.asyncio
async def test_period_ranking_narrowed_by_service(tmp_path):
    fake = PeriodFake(3, domain_names={1: "order", 2: "payment", 3: "batch"})
    fleet = await _ready(fake, tmp_path)
    out = await fleet.ranking(service="payment", lookback_minutes=60, full=True)
    assert {int(q["domain_id"]) for _h, q in fake.status_calls()} == {2}
    assert len(fake.status_calls()) == 4 and out["summary"]["domains_total"] == 2
    assert {r["domain_id"] for r in out["rows"]} == {2}


# ── MCP 경계 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_period_arguments_reach_period_ranking(tmp_path, monkeypatch):
    monkeypatch.setenv("APM_SPOOL_DIR", str(tmp_path / "spool"))
    fake = PeriodFake(2, hosts=("apm.test",))
    tools = _tools(fake, tmp_path)
    jcfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(Spool(jcfg.spool_dir), jcfg, envelope=tools.ok, error_envelope=tools.err,
                      rate_per_sec=0.0, on_finish=audit_job_finished)
    mcp = create_server(tools, jobs=jobs)
    try:
        schema = {t.name: t.inputSchema for t in await mcp.list_tools()}["apm_fleet"]
        assert schema["properties"]["metric"]["enum"] == list(ALL_RANKING_METRICS)
        period = _json(await mcp.call_tool("apm_fleet", {
            "mode": "ranking", "metric": "calls", "n": 2,
            "reference_time": None, "lookback_minutes": 60,
        }))
        current = _json(await mcp.call_tool("apm_fleet", {"mode": "ranking", "n": 2}))
        fallback = _json(await mcp.call_tool("apm_fleet", {
            "mode": "ranking", "metric": "thread_current", "lookback_minutes": 60,
        }))
        bad = _json(await mcp.call_tool("apm_fleet", {"mode": "ranking", "metric": "failures"}))
    finally:
        await jobs.aclose()
    assert period["summary"]["window_mode"] == "period" and period["row_count"] == 2
    assert period["rows"][0]["metric"] == "calls"
    assert not any("쓰지 않는다" in x for x in period["limits"])
    assert "window_mode" not in current["summary"]
    assert fallback["summary"]["window_mode"] == "current"
    assert any("지표 thread_current는 기간 통계가 없어 현재값 순위다" in x
               for x in fallback["limits"])
    assert bad["error"] == INVALID_ARGUMENT
    assert not (GW_ROOT / "var").exists()
