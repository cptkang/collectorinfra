"""plans/134 W5·W6(독립분)·W7 독립 검증(verify-gw · 2026-10-06).

구현자 시험과 **다른 입력**으로 수치·경계를 다시 잰다. 기대값은 원자료에서 손으로 계산한 오라클이다.

- 계약 대조: 새 도구 7종 + `apm_transaction_profile` 확장의 실제 MCP 스키마(이름·형·필수).
- 수치: change_impact(경계 t · 가중 평균 · 원시 p95 · 기준 0 · %p · 실패 = None) · period_compare
  (Σtotal÷Σcalls · 시 경계 · 길이 차이 · 한쪽만 있는 인스턴스) · trace(중복 제거 키 · 정렬 ·
  trace_order · 창 우선순위 · 다른 GUID 제외 · 같은 txid 두 도메인 · 시작 시각 None).
- 예산: TTL 고정 창(첫 호출 기준) · 단일 토큰 `default`·무인증 `anonymous`의 실 SSE 전송.
- 허용목록·경계: 37템플릿 정본↔사본 · 거부 입력 HTTP 0회 · `compare`→`comparing`은 404일 때만 ·
  개별 설정 404 · v2 모양 위반 = `apm_api_error`.
- 발견 결함은 `xfail(strict=True, reason="VG-…")`로 재현한다(고치면 XPASS로 실패해 알린다).

외부 네트워크 0 — `httpx.MockTransport`와 127.0.0.1 임시 포트만 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import (
    ALLOWED,
    NotAllowedError,
    check_request,
    match_template,
)
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.spool import Spool
from apm_gateway.domain.errors import (
    API_ERROR,
    INVALID_ARGUMENT,
    RATE_LIMITED,
    SOURCE_UNAVAILABLE,
    ApmError,
)
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import DOMAIN, NOW_MS, TOKEN, make_cfg, make_tools, synthetic_handler

from apm_gateway.config import JobConfig

MIN = 60_000
HOUR = 3_600_000
NOT_CONNECTED = {"exception": {"message": "1000 Domain is not connected"}}


def _params(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


def _ids(request: httpx.Request) -> set[int]:
    return {int(x) for x in request.url.params.get("instance_id", "").split(",") if x}


# ── 1. 계약 대조 — 실제 MCP 스키마 ─────────────────────────────

COMMON = {
    "investigation_id": ("string|null", False),
    "thread_id": ("string|null", False),
    "owner": ("string|null", False),
    "wait_seconds": ("number|null", False),
}
CONTRACT = {
    "apm_transaction_trace": {
        "guid": ("string", True),
        "hostname": ("string|null", False),
        "instance_name": ("string|null", False),  # plans/130 N-3
        "reference_time": ("string|null", False),
        "lookback_minutes": ("integer|null", False),
        "around_ms": ("integer|null", False),
        "around_minutes": ("integer|null", False),
        "source_ids": ("array|null", False),
    },
    "apm_change_impact": {
        # plans/130 N-3 — hostname 대신 instance_name으로도 부른다(둘 다 선택 · 둘 다 없으면 오류)
        "hostname": ("string|null", False),
        "instance_name": ("string|null", False),
        "reference_time": ("string|null", False),
        "lookback_minutes": ("integer|null", False),
        "width_minutes": ("integer|null", False),
        "source_ids": ("array|null", False),
        "n": ("integer|null", False),
        "full": ("boolean", False),
    },
    "apm_period_compare": {
        "hostname": ("string|null", False),
        "instance_name": ("string|null", False),
        "current_start": ("string", True),
        "current_end": ("string", True),
        "baseline_start": ("string", True),
        "baseline_end": ("string", True),
        "source_ids": ("array|null", False),
        "n": ("integer|null", False),
        "full": ("boolean", False),
    },
    "apm_config": {
        "kind": ("string", True),
        "hostname": ("string|null", False),
        "source_ids": ("array|null", False),
        "rule_type": ("string|null", False),
        "target": ("string|null", False),
        "error_type": ("string|null", False),
        "process_id": ("integer|null", False),
        "search": ("string|null", False),
        "instance_name": ("string|null", False),
    },
    "apm_environment": {
        "hostname": ("string|null", False),
        "instance_name": ("string|null", False),
        "source_ids": ("array|null", False),
        "scope": ("string|null", False),
        "key": ("string|null", False),
    },
    "apm_users": {"user_id": ("string|null", False), "source_ids": ("array|null", False)},
    "apm_active_detail": {
        # 계약 「정수 · 음수 허용」 — active_ref의 txid가 문자열이라 문자열도 받는다(gw-w7 이탈)
        "domain_id": ("integer", True),
        "txid": ("integer|string", True),
        "session_id": ("integer|null", False),
        "thread_hash": ("integer|null", False),
        "source_id": ("string|null", False),
        "hostname": ("string|null", False),
        "instance_name": ("string|null", False),
    },
}


def _type(prop: dict) -> str:
    if "anyOf" in prop:
        return "|".join(p.get("type", "?") for p in prop["anyOf"])
    return prop.get("type", "?")


def _server(tmp_path):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(synthetic_handler()))
    cfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), jobs


@pytest.mark.asyncio
async def test_new_tool_schemas_match_the_contract(tmp_path):
    mcp, jobs = _server(tmp_path)
    try:
        schemas = {t.name: t.inputSchema for t in await mcp.list_tools()}
        for name, args in CONTRACT.items():
            props = schemas[name]["properties"]
            required = set(schemas[name].get("required", []))
            got = {k: (_type(v), k in required) for k, v in props.items()}
            assert got == {**args, **COMMON}, name
        profile = schemas["apm_transaction_profile"]["properties"]
        assert _type(profile["profile_no"]) == "integer|null"
        assert _type(profile["include_param_key"]) == "boolean|null"
        assert "principal" not in profile and "key" not in profile
    finally:
        await jobs.aclose()


# ── 2-a. apm_change_impact 수치 오라클 ─────────────────────────

# 변경 감지 시각 — 분·초 경계에 맞지 않는 값(1분 조각 경계와 어긋나게)
T = NOW_MS - 5 * HOUR + 17 * MIN + 333
W = 20  # 비교 폭(분)


def _tx(iid: int, end: int, resp: int, err: bool, k: int) -> dict:
    return {
        "domainId": DOMAIN,
        "instanceId": iid,
        "instanceName": "was01_b" if iid == 1002 else "was01_a",
        "txid": str(500_000 + k),
        "responseTime": resp,
        "endTime": str(end),
        "startTime": str(end - resp),
        "errorType": "TIMEOUT" if err else "",
    }


def _impact_data():
    w = W * MIN
    spec = [
        # 변경 전 [T−w, T) — 시작 경계 포함 · T−1 포함
        (1002, T - w, 100, False),
        (1002, T - w + 30_000, 100, False),
        (1002, T - 10 * MIN, 100, True),
        (1002, T - 5 * MIN, 100, False),
        (1002, T - 1, 10_000, True),
        (1002, T - w - 1, 77_777, True),  # 구간 밖(앞)
        # 변경 후 [T, T+w) — 경계 t는 뒤 구간
        *[(1002, T + k * MIN, 200, k == 0) for k in range(19)],
        (1002, T + w - 1, 900, False),
        (1002, T + w, 55_555, True),  # 구간 밖(뒤 · 지금 이전이라 끝 미포함)
        (1001, T + 2 * MIN, 99_999, True),  # 다른 인스턴스(원천이 섞어 줘도 세지 않는다)
    ]
    txs = [_tx(iid, end, resp, err, k) for k, (iid, end, resp, err) in enumerate(spec)]
    errors = [
        {
            "domainId": DOMAIN,
            "instanceId": iid,
            "instanceName": "was01_b",
            "errorType": etype,
            "message": "m",
            "time": str(at),
            "txid": "1",
            "applicationName": "/x",
        }
        for iid, at, etype in [
            (1002, T - w, "TYPE_A"),
            (1002, T - 15 * MIN, "TYPE_A"),
            (1002, T - 1, "TYPE_B"),
            (1002, T - w - 1, "TYPE_C"),  # 밖
            (1002, T, "TYPE_B"),
            (1002, T + w, "TYPE_C"),  # 밖
            (1001, T - 3 * MIN, "TYPE_Z"),  # 다른 인스턴스
        ]
    ]
    return txs, errors


def _impact_tools(changes, *, xview_fail=None, error_fail=None):
    txs, errors = _impact_data()
    seen: list[dict[str, str]] = []

    def tx_time(request):
        seen.append(_params(request))
        if xview_fail and xview_fail(request):
            return httpx.Response(500, json=NOT_CONNECTED)
        lo, hi = int(request.url.params["start_time"]), int(request.url.params["end_time"])
        # 원천은 instance_id를 무시하고 다 준다 — 도구가 걸러야 한다
        return httpx.Response(
            200, json={"result": [t for t in txs if lo <= int(t["endTime"]) <= hi]}
        )

    def err_search(request):
        if error_fail and error_fail(request):
            return httpx.Response(500, json=NOT_CONNECTED)
        lo, hi = int(request.url.params["start_time"]), int(request.url.params["end_time"])
        return httpx.Response(
            200, json={"result": [e for e in errors if lo <= int(e["time"]) <= hi]}
        )

    handler = synthetic_handler(
        override={
            f"/api-v2/deploy/{DOMAIN}": httpx.Response(200, json=changes),
            "/api/transaction/time": tx_time,
            "/api/dbsearch/error": err_search,
        }
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    return tools, seen


@pytest.mark.asyncio
async def test_change_impact_independent_oracle():
    tools, seen = _impact_tools([{"collectTime": T, "instanceId": 1002}])
    out = await tools.apm_change_impact("was-host01", width_minutes=W)
    assert out["row_count"] == 1 and not out.get("partial")
    row = out["rows"][0]
    b, a, d = row["before"], row["after"], row["delta"]
    assert (b["start_ms"], b["end_ms"], a["start_ms"], a["end_ms"]) == (
        T - W * MIN,
        T,
        T,
        T + W * MIN,
    )
    # 전: 5건(경계 T−w·T−1 포함) · 오류 2 · 평균 10400/5 · p95 = ⌈0.95×5⌉=5번째 = 10000
    assert (b["calls"], b["tx_errors"]) == (5, 2)
    assert b["error_rate"] == pytest.approx(0.4)
    assert b["avg_response_ms"] == pytest.approx(2080)
    assert (b["p95_response_ms"], b["max_response_ms"]) == (10_000, 10_000)
    # 후: 20건(경계 T 포함 · T+w 제외) · 오류 1 · 평균 4700/20 · p95 = 19번째 = 200 · 최대 900
    assert (a["calls"], a["tx_errors"]) == (20, 1)
    assert a["error_rate"] == pytest.approx(0.05)
    assert a["avg_response_ms"] == pytest.approx(235)
    assert (a["p95_response_ms"], a["max_response_ms"]) == (200, 900)
    # 오류 기록: 전 3(TYPE_A 2 · TYPE_B 1) · 후 1
    assert b["error_records"] == 3 and a["error_records"] == 1
    assert b["errors_by_type"] == [
        {"error_type": "TYPE_A", "count": 2},
        {"error_type": "TYPE_B", "count": 1},
    ]
    assert d["calls"] == {"abs": 15, "pct": pytest.approx(300)}
    assert d["tx_errors"] == {"abs": -1, "pct": pytest.approx(-50)}
    assert d["error_rate"]["abs"] == pytest.approx(5 - 40)  # %p
    assert d["error_rate"]["pct"] == pytest.approx(-87.5)
    assert d["avg_response_ms"]["abs"] == pytest.approx(235 - 2080)
    assert d["avg_response_ms"]["pct"] == pytest.approx((235 - 2080) / 2080 * 100)
    assert d["p95_response_ms"] == {"abs": -9800, "pct": pytest.approx(-98)}
    assert d["error_records"]["abs"] == -2
    assert d["error_records"]["pct"] == pytest.approx(-200 / 3)
    assert set(d) == {
        "calls",
        "tx_errors",
        "error_rate",
        "avg_response_ms",
        "p95_response_ms",
        "error_records",
    }
    # 조회 구간: 전 끝 = T−1 · 후 끝 = T+w−1(지금 이전이라 끝 미포함) — 서로 겹치지 않는다
    ends = sorted(int(p["end_time"]) for p in seen)
    assert T - 1 in ends and T + W * MIN - 1 in ends and T not in ends


@pytest.mark.asyncio
async def test_change_impact_two_changes_same_instance_and_none_time():
    changes = [
        {"collectTime": T, "instanceId": 1002},
        {"collectTime": T + 2 * HOUR, "instanceId": 1002},
        {"instanceId": 1001},  # 감지 시각 없음
    ]
    tools, _ = _impact_tools(changes)
    out = await tools.apm_change_impact("was-host01", width_minutes=W)
    # 최근 순 · 감지 시각 없는 변경은 비교하지 않고 partial
    assert [r["change_detected_ms"] for r in out["rows"]] == [T + 2 * HOUR, T]
    assert out["partial"] is True
    assert any("감지 시각이 없는 변경은 비교하지 못했다" in x for x in out["limits"])
    later = out["rows"][0]
    # T+2h 전후에는 거래가 없다 — 0건 · 비율·평균·p95 None · 증감률 N/A
    assert later["before"]["calls"] == 0 and later["after"]["calls"] == 0
    assert later["delta"]["calls"] == {"abs": 0, "pct": None}
    assert later["delta"]["error_rate"] == {"abs": None, "pct": None}
    assert out["summary"]["changes"] == 3 and out["summary"]["compared"] == 2


@pytest.mark.asyncio
async def test_change_impact_before_xview_failure_is_none_not_zero():
    tools, _ = _impact_tools(
        [{"collectTime": T, "instanceId": 1002}],
        xview_fail=lambda r: int(r.url.params["end_time"]) < T,
    )
    out = await tools.apm_change_impact("was-host01", width_minutes=W)
    row = out["rows"][0]
    assert out["partial"] is True
    for k in ("calls", "tx_errors", "error_rate", "avg_response_ms", "p95_response_ms"):
        assert row["before"][k] is None and row["delta"][k] == {"abs": None, "pct": None}
    assert row["after"]["calls"] == 20 and row["before"]["error_records"] == 3


@pytest.mark.asyncio
async def test_change_impact_everything_failed_is_error_not_zero_rows():
    tools, _ = _impact_tools(
        [{"collectTime": T, "instanceId": 1002}],
        xview_fail=lambda r: True,
        error_fail=lambda r: True,
    )
    with pytest.raises(ApmError) as exc:
        await tools.apm_change_impact("was-host01", width_minutes=W)
    assert exc.value.code == SOURCE_UNAVAILABLE


# ── 2-b. apm_period_compare 수치 오라클 ────────────────────────

CUR_HOUR = NOW_MS - 2 * HOUR  # 2026-09-29T08:00+09:00
BASE_HOUR = NOW_MS - 7 * 24 * HOUR - 2 * HOUR  # 2026-09-22T08:00+09:00


def _app_row(calls, failures, total, max_ms, avg=None):
    return {
        "name": "/svc",
        "calls": calls,
        "failures": failures,
        "totalResponseTime": total,
        "responseTime": avg if avg is not None else (total / calls if calls else 0),
        "maxResponseTime": max_ms,
    }


def _period_tools():
    asked: list[dict[str, str]] = []

    def app_status(request):
        p = _params(request)
        asked.append(p)
        (iid,) = _ids(request)
        current = int(p["start_time"]) >= CUR_HOUR
        rows = {
            # 1001 현재: 평균의 평균 200 ≠ 가중 평균 60000/400 = 150
            (1001, True): [_app_row(100, 5, 30_000, 2_000), _app_row(300, 0, 30_000, 900)],
            (1001, False): [_app_row(200, 10, 100_000, 5_000)],
            (1002, True): [],  # 현재 구간 행 없음 — 한쪽에만 있는 인스턴스
            (1002, False): [_app_row(50, 0, 5_000, 300)],
        }[(iid, current)]
        return httpx.Response(200, json={"result": rows})

    handler = synthetic_handler(override={"/api/status/application": app_status})
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    return tools, asked


@pytest.mark.asyncio
async def test_period_compare_independent_oracle():
    tools, asked = _period_tools()
    out = await tools.apm_period_compare(
        "was-host01",
        current_start="2026-09-29T08:20:00",
        current_end="2026-09-29T09:40:00",
        baseline_start="2026-09-22T08:00:00+09:00",
        baseline_end="2026-09-22T09:00:00+09:00",
    )
    # 시 경계로 넓힌 실제 질의 구간 — 현재 08:00~10:00(2시간) · 기준 08:00~09:00(1시간)
    spans = {(int(p["start_time"]), int(p["end_time"])) for p in asked}
    assert spans == {(CUR_HOUR, CUR_HOUR + 2 * HOUR), (BASE_HOUR, BASE_HOUR + HOUR)}
    assert len(asked) == 4  # 인스턴스 2 × 구간 2
    by_id = {r["instance_id"]: r for r in out["rows"]}
    a = by_id[1001]
    assert (a["current"]["calls"], a["current"]["failures"]) == (400, 5)
    assert a["current"]["avg_response_ms"] == pytest.approx(150)
    assert a["current"]["failure_rate"] == pytest.approx(0.0125)
    assert a["current"]["max_response_ms"] == 2_000
    assert a["baseline"]["avg_response_ms"] == pytest.approx(500)
    assert a["delta"]["avg_response_ms"] == {"abs": pytest.approx(-350), "pct": pytest.approx(-70)}
    assert a["delta"]["failure_rate"]["abs"] == pytest.approx(1.25 - 5.0)  # %p
    b = by_id[1002]
    assert b["current"] is None and b["baseline"]["calls"] == 50
    assert all(v == {"abs": None, "pct": None} for v in b["delta"].values())  # 0으로 채우지 않음
    # 정렬: 현재 호출 수 순 · 현재 없는 행은 뒤
    assert [r["instance_id"] for r in out["rows"]] == [1001, 1002]
    s = out["summary"]
    # 전체: 현재 = 1001만(400·5·60000) · 기준 = 1001+1002(250·10·105000)
    assert (s["current"]["calls"], s["current"]["failures"]) == (400, 5)
    assert s["current"]["avg_response_ms"] == pytest.approx(150)
    assert (s["baseline"]["calls"], s["baseline"]["failures"]) == (250, 10)
    assert s["baseline"]["avg_response_ms"] == pytest.approx(420)
    assert s["baseline"]["failure_rate"] == pytest.approx(0.04)
    assert s["delta"]["calls"] == {"abs": 150, "pct": pytest.approx(60)}
    assert s["delta"]["failure_rate"]["abs"] == pytest.approx(1.25 - 4.0)
    assert s["delta"]["failure_rate"]["pct"] == pytest.approx(-68.75)
    assert s["delta"]["avg_response_ms"]["pct"] == pytest.approx(-270 / 420 * 100)
    assert s["delta"]["max_response_ms"] == {"abs": -3_000, "pct": pytest.approx(-60)}
    assert (s["current"]["hours"], s["baseline"]["hours"]) == (2, 1)
    joined = "\n".join(out["limits"])
    assert "두 구간 길이가 다르다" in joined and "현재 2시간 · 기준 1시간" in joined
    assert "요청 2026-09-29T08:20:00+09:00~2026-09-29T09:40:00+09:00" in joined
    assert "p95는 싣지 않았다" in joined
    assert "was01_b은 현재 구간 통계 행이 없다" in joined
    assert not any("p95" in k for r in out["rows"] for k in (r["current"] or {}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        {"current_start": "2026-09-29T09:00:00", "current_end": "2026-09-29T09:00:00"},
        {"baseline_start": "2026-09-22T10:00:00", "baseline_end": "2026-09-22T09:00:00"},
        {"current_end": None},
        {"baseline_start": "어제"},
    ],
)
async def test_period_compare_bad_periods_make_no_http(kw):
    calls = []

    def count(request):
        calls.append(request.url.path)
        return synthetic_handler()(request)

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(count))
    args = {
        "current_start": "2026-09-29T08:00:00",
        "current_end": "2026-09-29T09:00:00",
        "baseline_start": "2026-09-22T08:00:00",
        "baseline_end": "2026-09-22T09:00:00",
        **kw,
    }
    with pytest.raises(ApmError) as exc:
        await tools.apm_period_compare("was-host01", **args)
    assert exc.value.code == INVALID_ARGUMENT and calls == []


# ── 2-c. apm_transaction_trace 수치·순서 오라클 ─────────────────

G = "G-7f3a"


def _trace_rows(domain: int) -> list[dict]:
    def tx(txid, iid, name, start, end, guid=G):
        return {
            "domainId": domain,
            "instanceId": iid,
            "instanceName": name,
            "txid": txid,
            "guid": guid,
            "startTime": None if start is None else str(start),
            "endTime": str(end),
            "responseTime": 10,
            "userId": "hong.gildong",
        }

    if domain == DOMAIN:
        return [
            tx("20", 1001, "was01_a", NOW_MS - 50_000, NOW_MS - 40_000),
            tx("20", 1001, "was01_a", NOW_MS - 50_000, NOW_MS - 40_000),  # 중복(같은 도메인·txid)
            tx("7", 1002, "was01_b", NOW_MS - 50_000, NOW_MS - 45_000),  # 같은 시작 — txid로 정렬
            tx("OTHER", 1001, "was01_a", NOW_MS - 49_000, NOW_MS - 48_000, guid="G-other"),
            tx("30", 1001, "was01_a", None, NOW_MS - 10_000),  # 시작 시각 None → 맨 뒤
        ]
    return [
        tx("20", 2001, "batch_x", NOW_MS - 55_000, NOW_MS - 45_000),  # 같은 txid · 다른 도메인
        tx("", 2001, "batch_x", NOW_MS - 52_000, NOW_MS - 51_000),  # txid 없음 — 중복 제거 안 함
    ]


def _trace_tools(*, fail_domains=(), empty=False):
    asked: list[dict[str, str]] = []

    def domains(request):
        return httpx.Response(
            200,
            json={"result": [{"domainId": DOMAIN, "name": "d1"}, {"domainId": 2000, "name": "d2"}]},
        )

    base = synthetic_handler()

    async def instances(request):
        if request.url.params["domain_id"] == "2000":
            return httpx.Response(
                200, json={"result": [{"instanceId": 2001, "name": "batch_x", "hostName": "b1"}]}
            )
        return await base(request)

    def guid(request):
        p = _params(request)
        asked.append(p)
        d = int(p["domain_id"])
        if d in fail_domains:
            return httpx.Response(
                500, json={"exception": {"message": f"{d} Domain is not connected"}}
            )
        rows = [] if empty else _trace_rows(d)  # 다른 GUID 행도 섞어 준다
        return httpx.Response(200, json={"result": rows})

    async def handler(request):
        path = request.url.path
        if path == "/api/domain":
            return domains(request)
        if path == "/api/instance":
            return await instances(request)
        if path == "/api/transaction/guid":
            return guid(request)
        return await base(request)

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    return tools, asked


@pytest.mark.asyncio
async def test_trace_independent_order_dedupe_and_summary():
    tools, asked = _trace_tools()
    out = await tools.apm_transaction_trace(f"  {G} ")
    assert {p["domain_id"] for p in asked} == {str(DOMAIN), "2000"} and len(asked) == 2
    assert all(p["guid"] == G for p in asked)  # 앞뒤 공백 제거
    order = [(r["domain_id"], r["txid"], r["trace_order"]) for r in out["rows"]]
    assert order == [
        (2000, "20", 1),
        (2000, "", 2),
        (DOMAIN, "20", 3),
        (DOMAIN, "7", 4),
        (DOMAIN, "30", 5),
    ]
    assert out["rows"][1]["profile_ref"] is None  # txid 없으면 참조 없음
    assert out["rows"][2]["profile_ref"] == {
        "source_id": "default",
        "domain_id": DOMAIN,
        "txid": "20",
        "time_ms": NOW_MS - 40_000,
    }
    assert all(r["user_id"] == "h***" for r in out["rows"])  # 식별자 가림
    s = out["summary"]
    assert s["transactions"] == 5
    assert (s["domains_queried"], s["domains_with_hits"], s["domains_failed"]) == (2, 2, 0)
    assert s["first_start_ms"] == NOW_MS - 55_000 and s["last_end_ms"] == NOW_MS - 10_000
    assert s["span_ms"] == 45_000
    assert s["instances"] == ["batch_x", "was01_a", "was01_b"]
    assert s["guid"] == G and s["sources"] == ["default"]
    joined = "\n".join(out["limits"])
    assert "호출 관계(토폴로지)를 뜻하지 않는다" in joined
    assert "시계 차이를 보정하지 않았다" in joined
    assert "다른 GUID의 거래 1건" in joined
    assert "기간 미지정 — 최근 60분" in joined
    assert not out.get("partial")


@pytest.mark.asyncio
async def test_trace_window_priority_on_the_wire():
    tools, asked = _trace_tools(empty=True)
    explicit = await tools.apm_transaction_trace(
        G,
        hostname="was-host01",
        reference_time="2026-09-29T09:00:00",
        lookback_minutes=15,
        around_ms=NOW_MS - 30 * MIN,
    )
    assert (int(asked[-1]["start_time"]), int(asked[-1]["end_time"])) == (
        NOW_MS - HOUR - 15 * MIN,
        NOW_MS - HOUR,
    )
    assert not any("기간 미지정" in x for x in explicit["limits"])
    around = await tools.apm_transaction_trace(
        G, hostname="was-host01", around_ms=NOW_MS - 30 * MIN, around_minutes=2
    )
    assert (int(asked[-1]["start_time"]), int(asked[-1]["end_time"])) == (
        NOW_MS - 32 * MIN,
        NOW_MS - 28 * MIN,
    )
    assert "[한계] 기간 미지정 — ±2분(앞 결과 시각 기준)" in around["limits"]
    default = await tools.apm_transaction_trace(G, hostname="was-host01")
    assert (int(asked[-1]["start_time"]), int(asked[-1]["end_time"])) == (NOW_MS - HOUR, NOW_MS)
    assert "[한계] 기간 미지정 — 최근 60분" in default["limits"]
    # hostname이 있으면 그 호스트의 정합 도메인만 묻는다
    assert {p["domain_id"] for p in asked} == {str(DOMAIN)}
    # 0건은 오류가 아니라 빈 행 + 구간 고지
    assert default["rows"] == [] and any("찾지 못했다(구간 " in x for x in default["limits"])


@pytest.mark.asyncio
async def test_trace_partial_and_all_failed():
    tools, _ = _trace_tools(fail_domains=(2000,))
    out = await tools.apm_transaction_trace(G)
    assert out["partial"] is True and out["summary"]["domains_failed"] == 1
    assert [r["domain_id"] for r in out["rows"]] == [DOMAIN, DOMAIN, DOMAIN]
    assert any("GUID 거래 조회 실패(도메인 2000)" in x for x in out["limits"])
    # 히트가 한 도메인뿐이면 시계 차이 고지는 없다
    assert not any("시계 차이" in x for x in out["limits"])
    tools, _ = _trace_tools(fail_domains=(DOMAIN, 2000))
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_trace(G)
    assert exc.value.code == SOURCE_UNAVAILABLE


# ── 3. 프로파일 예산 — TTL 고정 창 · default/anonymous 실 SSE ──────


async def _profile(tools, **kw):
    return await tools.apm_transaction_profile("was-host01", DOMAIN, "9000", NOW_MS - 30_000, **kw)


@pytest.mark.asyncio
async def test_budget_ttl_is_a_fixed_window_from_first_call():
    now = [1_790_643_600.0]
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        clock=lambda: now[0],
    )
    await _profile(tools, principal="investigation", investigation_id="inv-q")
    now[0] += 3000
    for _ in range(4):
        await _profile(tools, principal="investigation", investigation_id="inv-q")
    now[0] += 600  # 첫 호출 뒤 정확히 3600초 — 아직 만료 아님(`>`)
    with pytest.raises(ApmError) as exc:
        await _profile(tools, principal="investigation", investigation_id="inv-q")
    assert exc.value.code == RATE_LIMITED
    # 다른 주체의 같은 investigation_id 칸은 별개
    await _profile(tools, principal="alarm", investigation_id="inv-q")
    now[0] += 1  # 3601초 — 첫 호출 기준으로 만료(슬라이딩 아님)
    await _profile(tools, principal="investigation", investigation_id="inv-q")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _sse_profile_calls(app, calls: list[dict], token: str | None) -> list[dict]:
    import uvicorn
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    serving = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.01)
    base = {"hostname": "was-host01", "domain_id": DOMAIN, "txid": "9000", "time_ms": NOW_MS}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    out = []
    try:
        async with sse_client(f"http://127.0.0.1:{port}/sse", headers=headers) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                for extra in calls:
                    result = await session.call_tool("apm_transaction_profile", {**base, **extra})
                    out.append(json.loads(result.content[0].text))
    finally:
        server.should_exit = True
        await serving
    return out


@pytest.mark.asyncio
async def test_sse_single_token_default_has_budget_but_owners_are_isolated(tmp_path):
    from apm_gateway.interface.server import build_asgi_app

    mcp, jobs = _server(tmp_path)
    app = build_asgi_app(mcp, "tok-single-77")  # 문자열 하나 = 주체 default
    try:
        a = [{"owner": "user:a"}] * 6
        b = [{"owner": "user:b"}] * 1
        forged = [{"owner": "chat", "investigation_id": "chat"}] * 6
        got = await _sse_profile_calls(app, a + b + forged, "tok-single-77")
        assert all("error" not in r for r in got[:5]) and got[5]["error"] == RATE_LIMITED
        assert "error" not in got[6]  # 다른 owner 칸
        assert all("error" not in r for r in got[7:12]) and got[12]["error"] == RATE_LIMITED
        assert "주체 default" in got[5]["reason"]
    finally:
        await jobs.aclose()


@pytest.mark.asyncio
async def test_sse_no_token_anonymous_has_budget(tmp_path):
    from apm_gateway.interface.server import build_asgi_app

    mcp, jobs = _server(tmp_path)
    app = build_asgi_app(mcp, None)  # 무인증 = 주체 anonymous
    try:
        got = await _sse_profile_calls(app, [{}] * 6, None)
        assert all("error" not in r for r in got[:5]) and got[5]["error"] == RATE_LIMITED
        assert "주체 anonymous" in got[5]["reason"]
    finally:
        await jobs.aclose()


# ── 4. 허용목록 · 경계 ─────────────────────────────────────


def test_allowlist_is_37_templates_and_copy_matches_in_order():
    from jennifer_catalog import ALLOWED as COPY

    assert len(ALLOWED) == 37 and list(COPY) == list(ALLOWED)
    for t, ep in ALLOWED.items():
        c = COPY[t]
        assert (c.required, c.optional, c.accept, tuple(c.path_vars)) == (
            ep.required,
            ep.optional,
            ep.accept,
            ep.path_vars,
        ), t


@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("POST", "/api/auth/userlist", {}),
        ("PUT", "/api-v2/manage/rule/event/error/1000", {}),
        ("DELETE", "/restapi/user/op01", {}),
        ("PATCH", "/api-v2/environment-variable/1000", {}),
        ("GET", "/api/auth/userlist", {"token": "x"}),
        ("GET", "/api-v2/manage/instance", {"processId": "1", "Token": "x"}),
        ("GET", "/api-v2/manage/instance", {}),  # 필수 processId 누락
        ("GET", "/api-v2/manage/instance", {"processId": "1", "x": "2"}),
        ("GET", "/api/transaction/guid", {"domain_id": "1", "guid": "g", "start_time": "1"}),
        (
            "GET",
            "/api/transaction/guid",
            {
                "domain_id": "1",
                "guid": "g",
                "start_time": "1",
                "end_time": "2",
                "time_pattern": "x",
            },
        ),
        ("GET", "/api/transaction/txid", {"domain_id": "1", "txid": "1", "time": "1", "x": "1"}),
        ("GET", "/api/auth/userlist.xml", {}),
        ("GET", "/restapi/user/op01.XML", {}),
        ("GET", "/api-v2/test-response/json", {}),
        ("GET", "/api-v2/auth-test", {}),
        ("GET", "/api-v2/manage/rule/event/error/1000/error_x/applied", {}),  # 소문자 errorType
        ("GET", "/api-v2/manage/rule/event/compare/1000/business", {}),
        ("GET", "/api-v2/active-service/detail/1000/12a", {}),
        ("GET", "/api-v2/active-service/detail/1000/--5", {}),
        ("GET", "/api-v2/active-service/detail/1000/123456789012345678901", {}),  # 21자리
        ("GET", "/api-v2/loaded-class/1000/-1", {}),
        ("GET", "/restapi/user/..", {}),
        ("GET", "/restapi/user/a%2Fb", {}),
        ("GET", "/restapi/user/%ED%99%8D", {}),
        ("GET", "/api-v2/manage/db/path/1000/../../manage/data-server/control", {}),
    ],
)
def test_rejected_requests(method, path, query):
    with pytest.raises(NotAllowedError):
        check_request(method, path, query)


def test_signed_txid_and_optional_session_keys_are_allowed():
    ep = check_request(
        "GET",
        "/api-v2/active-service/detail/1000/-9223372036854775807",
        {"sessionId": "1", "threadHash": "-2"},
    )
    assert ep.template == "/api-v2/active-service/detail/{domainId}/{txid}"
    assert match_template("/api-v2/manage/rule/event/comparing/1000/domain") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template", "params", "path_vars"),
    [
        ("/restapi/user/{id}", None, {"id": "../admin"}),
        ("/restapi/user/{id}", None, {"id": "a.xml"}),
        ("/restapi/user/{id}", None, {"id": "홍길동"}),
        ("/api-v2/active-service/detail/{domainId}/{txid}", None, {"domainId": 1, "txid": "1e5"}),
        (
            "/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
            None,
            {"domainId": 1, "errorType": "a"},
        ),
        ("/api/auth/userlist", {"token": "t"}, None),
        ("/api-v2/test-response/json", None, None),
    ],
)
async def test_client_rejections_make_zero_http_calls(template, params, path_vars):
    hits: list[str] = []

    def handler(request):
        hits.append(str(request.url))
        return httpx.Response(200, json=[])

    cfg = make_cfg("http://apm.test")
    client = JenniferClient(cfg.sources[0], transport=httpx.MockTransport(handler))
    with pytest.raises(NotAllowedError):
        await client.get_json(template, params, path_vars)
    assert hits == [] and client.calls_total == 0


@pytest.mark.asyncio
async def test_redirect_on_w7_path_is_not_followed():
    hits: list[str] = []

    def handler(request):
        hits.append(request.url.path)
        return httpx.Response(302, headers={"Location": "http://evil.example/api/auth/userlist"})

    cfg = make_cfg("http://apm.test")
    client = JenniferClient(cfg.sources[0], transport=httpx.MockTransport(handler))
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api-v2/manage/data-server/domains")
    assert exc.value.code == API_ERROR and hits == ["/api-v2/manage/data-server/domains"]


def _manage(handler):
    base = synthetic_handler()
    calls: list[str] = []

    async def wrapped(request):
        calls.append(request.url.path)
        resp = handler(request)
        if resp is None:
            return await base(request)
        return resp

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(wrapped))
    return ManageTools(tools), calls


def _rules_handler(compare_status: int | None, individual: int | None = None):
    def handler(request):
        path = request.url.path
        if "/rule/event/compare/" in path:
            if compare_status == 200:
                return httpx.Response(200, json=[])
            return httpx.Response(compare_status or 404, text="no")
        if "/rule/event/comparing/" in path:
            return httpx.Response(200, json=[{"metricId": "m1", "level": "WARNING"}])
        if "/individual-setting/" in path:
            if individual == 200:
                return httpx.Response(200, json=False)
            return httpx.Response(individual or 404, text="no")
        if path.endswith("/applied"):
            return httpx.Response(200, json=True)
        if "/rule/event/" in path:
            return httpx.Response(200, json=[])
        return None

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "retried", "partial"),
    [(404, True, False), (405, False, True), (500, False, True), (200, False, False)],
)
async def test_compare_is_re_asked_as_comparing_only_on_404(status, retried, partial):
    manage, calls = _manage(_rules_handler(status))
    # 룰 3종(error · metric/domain · compare/domain) — compare만 실패하면 partial(전부 실패 아님)
    out = await manage.apm_config("event_rules", hostname="was-host01", target="domain")
    asked_comparing = any("/comparing/" in c for c in calls)
    assert asked_comparing is retried
    assert bool(out.get("partial")) is partial
    if retried:
        assert any("comparing으로 다시 물어" in x for x in out["limits"])
        assert [r["metric_id"] for r in out["rows"] if r["rule_type"] == "compare"] == ["m1"]


@pytest.mark.asyncio
async def test_individual_setting_404_is_absent_but_405_is_failure():
    manage, _ = _manage(_rules_handler(200, individual=404))
    out = await manage.apm_config(
        "event_rules", hostname="was-host01", rule_type="error", error_type="sql_exception"
    )
    indiv = [r for r in out["rows"] if r["rule_type"] == "error_individual"]
    assert {r["instance_id"] for r in indiv} == {1001, 1002}
    assert all(
        r["individual_setting"] is None and r["individual_setting_found"] is False for r in indiv
    )
    assert not out.get("partial")
    manage, _ = _manage(_rules_handler(200, individual=405))
    out = await manage.apm_config(
        "event_rules", hostname="was-host01", rule_type="error", error_type="SQL_EXCEPTION"
    )
    assert out["partial"] is True and any("HTTP 405" in x for x in out["limits"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path_part", "body", "call"),
    [
        ("/environment-variable/", [], ("apm_environment", {})),
        ("/environment-variable/", {"1001": ["PATH"]}, ("apm_environment", {})),
        (
            "/active-service-color-range-boundary",
            [1, 2],
            ("apm_config", {"kind": "color_boundary"}),
        ),
        (
            "/active-service-color-range-boundary",
            {"result": [1, 2, 3]},
            ("apm_config", {"kind": "color_boundary"}),
        ),
        ("/manual-rdb-export", {"result": []}, ("apm_config", {"kind": "rdb_export"})),
        ("/restapi/users", {"result": []}, ("apm_users", {})),
    ],
)
async def test_v2_shape_violation_is_api_error_not_empty(path_part, body, call):
    def handler(request):
        if path_part in request.url.path:
            return httpx.Response(200, json=body)
        if request.url.path == "/api/auth/userlist":
            return httpx.Response(200, json={"result": []})
        return None

    manage, _ = _manage(handler)
    name, kwargs = call
    if name == "apm_users":
        # 두 경로 중 하나만 모양 위반 — 그 단위는 실패(partial)이고 빈 결과로 강등하지 않는다
        out = await manage.apm_users(**kwargs)
        assert out["partial"] is True and any(API_ERROR in x for x in out["limits"])
        return
    with pytest.raises(ApmError) as exc:
        await getattr(manage, name)(**kwargs)
    assert exc.value.code == API_ERROR and "모양" in exc.value.reason


# ── 6. 발견 결함 재현(xfail strict — 고치면 XPASS로 실패해 알린다) ─────
# 2026-10-06 gw-fix: VG-1~7을 고쳐 표지를 지웠다. VG-8(mask_pii 숫자 오인 — 가리는 방향)은
# 팀 리드 처분으로 유지한다. VG-7은 운영 엔트리(`__main__`)가 하는 로거 설정을 시험에서도
# 적용한다(처분: 엔트리에서 httpx·httpcore를 WARNING으로 — 단언은 그대로).


@pytest.mark.asyncio
async def test_vg1_profile_keeps_sql_statements_without_select_keywords():
    """`{call …}`·`CALL`·`EXEC`·`BEGIN … END`·`COMMIT`·`SET`도 SQL 문이다 — da3ea4f는 `mask_sql`로
    리터럴만 가렸는데 지금은 앞 1자 + `***`로 바뀐다(조사 증거 소실)."""
    stmts = ["{call PKG_ORDER.SAVE(?, ?)}", "CALL audit_log(1, 'kim')", "COMMIT", "exec sp_who2"]
    h = synthetic_handler(
        override={
            "/api/transaction/sql": httpx.Response(
                200, json={"result": [{"sql": s} for s in stmts]}
            )
        }
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(h))
    out = await _profile(tools)
    assert out["rows"][0]["sqls"] == [
        "{call PKG_ORDER.SAVE(?, ?)}",
        "CALL audit_log(?, ?)",
        "COMMIT",
        "exec sp_who2",
    ]


@pytest.mark.asyncio
async def test_vg2_guid_response_shape_violation_is_not_reported_as_zero_hits():
    tx = {"domainId": DOMAIN, "instanceId": 1001, "txid": "1", "guid": G, "startTime": str(NOW_MS)}
    h = synthetic_handler(override={"/api/transaction/guid": httpx.Response(200, json=[tx])})
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(h))
    try:
        out = await tools.apm_transaction_trace(G)
    except ApmError as e:
        assert e.code == API_ERROR
        return
    # 오류가 아니면 적어도 「찾지 못했다」가 아니라 모양 위반을 알려야 한다
    assert not any("찾지 못했다" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_vg3_around_minutes_without_around_ms_is_not_silently_ignored():
    tools, _ = _trace_tools(empty=True)
    try:
        out = await tools.apm_transaction_trace(G, hostname="was-host01", around_minutes=30)
    except ApmError as e:
        assert e.code == INVALID_ARGUMENT
        return
    assert any("around_minutes" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_vg4_account_id_not_in_contract_violation_warning_log(caplog):
    def handler(request):
        if request.url.path.startswith("/restapi/user/"):
            return httpx.Response(
                500,
                json={"exception": {"message": "Required request parameter 'x' is not present"}},
            )
        return None

    manage, _ = _manage(handler)
    with caplog.at_level(logging.WARNING, logger="apm_gateway"), pytest.raises(ApmError):
        await manage.apm_users("rawacct77")
    assert "rawacct77" not in caplog.text


@pytest.mark.asyncio
async def test_vg7_account_id_not_in_info_logs_at_default_level(caplog):
    """운영 기본 `APM_GATEWAY_LOG_LEVEL=INFO`에서 httpx 라이브러리가 `HTTP Request: GET <URL>`을
    INFO로 남긴다 — `/restapi/user/<계정 ID>` 원값이 로그에 실린다(게이트웨이 DEBUG 줄
    템플릿화만으로는 막히지 않는다 · 실프로세스 INFO 실측 2026-10-06)."""

    def handler(request):
        if request.url.path.startswith("/restapi/user/"):
            return httpx.Response(200, json={"id": "rawacct77", "name": "Kim", "group": "g"})
        return None

    from apm_gateway.__main__ import _QUIET_HTTP_LOGGERS, quiet_http_loggers

    manage, _ = _manage(handler)
    saved = {name: logging.getLogger(name).level for name in _QUIET_HTTP_LOGGERS}
    quiet_http_loggers()  # 운영 엔트리가 로깅 설정 직후 하는 일(VG-7 처분)
    try:
        with caplog.at_level(logging.INFO):
            out = await manage.apm_users("rawacct77")
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)
    assert "rawacct77" not in json.dumps(out, ensure_ascii=False)  # 반환은 가렸다
    assert "rawacct77" not in caplog.text


@pytest.mark.xfail(strict=True, reason="VG-8 mask_pii가 숫자 설정 값을 주민번호·휴대폰으로 오인")
def test_vg8_mask_pii_keeps_numeric_config_values():
    """설정 값 전용 `mask_pii`(일반 설정을 훼손하지 않으려고 둔 함수)가 13자리 epoch ms를 `<rrn>`,
    010으로 시작하는 10자리 숫자를 `<phone>`으로 바꾼다(안전 방향의 과잉 가림)."""
    from apm_gateway.application.masking import mask_pii

    assert mask_pii("-Djennifer.boot=1790643600000") == "-Djennifer.boot=1790643600000"
    assert mask_pii("TIMEOUT_MS=0101234567") == "TIMEOUT_MS=0101234567"


@pytest.mark.asyncio
async def test_vg5_truncated_error_reason_does_not_leak_account_prefix():
    uid = "rawacct77abcdef"

    def handler(request):
        if request.url.path.startswith("/restapi/user/"):
            return httpx.Response(
                500, json={"exception": {"message": "x" * 205 + f" user {uid} not ok"}}
            )
        return None

    manage, _ = _manage(handler)
    with pytest.raises(ApmError) as exc:
        await manage.apm_users(uid)
    assert "rawacct" not in exc.value.reason


@pytest.mark.asyncio
async def test_vg6_hits_are_counted_by_the_hit_domain_not_the_query_domain():
    """원천이 domain_id를 무시하고 같은 거래를 두 도메인 질의에 모두 돌려주면(W10 미검증) 행은 중복
    제거로 1건인데 `domains_with_hits`=2 · 시계 차이 고지가 붙는다(계약: 히트가 걸친
    (소스, 도메인))."""
    one = {
        "domainId": DOMAIN,
        "instanceId": 1001,
        "txid": "11",
        "guid": G,
        "startTime": str(NOW_MS - 5_000),
        "endTime": str(NOW_MS - 4_000),
    }
    base = synthetic_handler()

    async def handler(request):
        path = request.url.path
        if path == "/api/domain":
            return httpx.Response(
                200,
                json={
                    "result": [{"domainId": DOMAIN, "name": "d1"}, {"domainId": 2000, "name": "d2"}]
                },
            )
        if path == "/api/instance" and request.url.params["domain_id"] == "2000":
            return httpx.Response(200, json={"result": []})
        if path == "/api/transaction/guid":
            return httpx.Response(200, json={"result": [one]})
        return await base(request)

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await tools.apm_transaction_trace(G)
    assert out["row_count"] == 1
    assert out["summary"]["domains_with_hits"] == 1
    assert not any("시계 차이" in x for x in out["limits"])


def test_token_constant_is_not_used_in_new_reasons():
    """테스트 상수 확인용(토큰 원값이 이 파일 기대값에 섞이지 않았다)."""
    assert TOKEN not in json.dumps(CONTRACT)
