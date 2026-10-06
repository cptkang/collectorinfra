"""plans/134 W6(독립분) — 분석 계산 `domain/analysis.py` · 변경 전후 `apm_change_impact`
(A-2 · F-09) · 기간 비교 `apm_period_compare`(A-1).

수치는 **오라클**로 단언한다 — 원자료(거래 응답시간·오류 수·통계 행)를 테스트가 정하고 기대값을
손으로 계산해 둔다(가중 평균 ≠ 평균의 평균 · 원시 p95 · 기준 0 = N/A · 누락 ≠ 0). 응답은
MockTransport 합성(스펙 필드명 · 실응답 모양 W10). 외부 네트워크 0.
"""

from __future__ import annotations

import json

import httpx
import pytest
from apm_gateway.domain import analysis as an
from apm_gateway.domain.errors import INVALID_ARGUMENT, SOURCE_UNAVAILABLE, ApmError
from conftest import DOMAIN, NOW_MS, make_tools, synthetic_handler

MIN = 60_000
HOUR = 3_600_000
DAY = 24 * HOUR
CAUSE = "[한계] 변경 감지 시각 전후의 동반 변화다 — 원인 확정이 아니다"
DETECTION = "[한계] 변경 감지(데이터 서버가 소스코드·리소스 변경을 인지한 시각) — 배포 확정 아님"


def _between(request: httpx.Request, ms: int) -> bool:
    p = request.url.params
    return int(p["start_time"]) <= ms <= int(p["end_time"])


def _instances(request: httpx.Request) -> set[int]:
    return {int(x) for x in request.url.params.get("instance_id", "").split(",") if x}


# ── domain/analysis.py ────────────────────────────────────


def test_weighted_mean_rate_delta_and_na():
    assert an.weighted_mean(50_000, 400) == 125
    assert an.weighted_mean(0, 5) == 0
    assert an.weighted_mean(100, 0) is None and an.weighted_mean(None, 3) is None
    assert an.rate(0, 10) == 0 and an.rate(3, 0) is None and an.rate(None, 4) is None
    assert an.delta(30, 20) == {"abs": 10, "pct": 50}
    assert an.delta(5, 0) == {"abs": 5, "pct": None}  # 기준 0 = N/A
    assert an.delta(None, 3) == {"abs": None, "pct": None}
    assert an.delta(3, None) == {"abs": None, "pct": None}
    got = an.rate_delta(0.25, 0.1)  # %p와 상대 증감
    assert got["abs"] == pytest.approx(15) and got["pct"] == pytest.approx(150)
    assert an.rate_delta(0.1, 0.0) == {"abs": pytest.approx(10), "pct": None}


def test_p95_comes_from_raw_values_not_window_p95_average():
    first = [100] * 19 + [10_000]  # 구간 p95 = 100
    second = [100] * 10  # 구간 p95 = 100
    assert an.p95(first) == 100 and an.p95(second) == 100
    assert an.p95(first + second) == 100
    burst = [100] * 18 + [9_000, 10_000]  # 구간 p95 = 19번째 = 9000
    quiet = [100] * 80  # 구간 p95 = 100
    assert an.p95(burst) == 9_000 and an.p95(quiet) == 100
    # 원시 100개의 p95 = 95번째 = 100 — 구간 p95의 평균(4550)이 아니다
    assert an.p95(burst + quiet) == 100 != (an.p95(burst) + an.p95(quiet)) / 2
    assert an.p95([]) is None


# ── apm_change_impact ─────────────────────────────────────

CHANGE_T = NOW_MS - 3 * HOUR  # 1001 변경 감지 시각(2026-09-29T07:00:00+09:00)
SHORT_T = NOW_MS - 30 * MIN  # 1002 변경 감지 — 뒤 구간이 30분뿐


def _xview_rows() -> list[dict]:
    rows = []
    # 변경 전(1001) — 응답 100..1000 · 오류 1
    for i in range(1, 11):
        rows.append((1001, CHANGE_T - (55 - i * 5) * MIN - 1_000, i * 100, i == 10))
    # 변경 후(1001) — 응답 200..4000 · 오류 5 + 경계(끝 = 감지 시각 · 뒤 구간에만 센다)
    for i in range(1, 21):
        rows.append((1001, CHANGE_T + i * 2 * MIN, i * 200, i <= 5))
    rows.append((1001, CHANGE_T, 5_000, False))
    # 변경 뒤 구간 밖(비교 폭 60분 밖) — 세지 않는다
    rows.append((1001, CHANGE_T + 61 * MIN, 99_999, True))
    # 1002 — 변경 전 0건 · 변경 후 2건
    rows.append((1002, SHORT_T + 5 * MIN, 300, False))
    rows.append((1002, SHORT_T + 10 * MIN, 500, True))
    return [
        {
            "domainId": DOMAIN,
            "instanceId": iid,
            "instanceName": "was01_a" if iid == 1001 else "was01_b",
            "txid": str(70_000 + k),
            "responseTime": resp,
            "endTime": str(end),
            "startTime": str(end - resp),
            "errorType": "SQL_EXCEPTION" if err else "",
        }
        for k, (iid, end, resp, err) in enumerate(rows)
    ]


def _error_rows() -> list[dict]:
    spec = [
        (1001, CHANGE_T - 30 * MIN, "SQL_EXCEPTION"),
        (1001, CHANGE_T - 20 * MIN, "SQL_EXCEPTION"),
        *[(1001, CHANGE_T + k * MIN, "SQL_EXCEPTION") for k in (5, 6, 7, 8)],
        (1001, CHANGE_T + 9 * MIN, "TIMEOUT"),
        (1001, CHANGE_T + 10 * MIN, "TIMEOUT"),
        (1002, CHANGE_T - 30 * MIN, "OTHER"),  # 다른 인스턴스 — 원천이 섞어 줘도 세지 않는다
    ]
    return [
        {
            "domainId": DOMAIN,
            "instanceId": iid,
            "instanceName": "was01_a",
            "errorType": etype,
            "message": "m",
            "time": str(at),
            "txid": "1",
            "applicationName": "/a",
        }
        for iid, at, etype in spec
    ]


def _impact_tools(
    changes: list[dict],
    *,
    fail: dict | None = None,
    xview_fail_after: int | None = None,
):
    """`fail`: 경로 → 실패할 요청 조건(request → bool) · `xview_fail_after`: 그 시각 이후 조각
    실패."""
    xview = _xview_rows()
    errors = _error_rows()
    fail = fail or {}

    def boom() -> httpx.Response:
        return httpx.Response(500, json={"exception": {"message": "1000 Domain is not connected"}})

    def tx_time(request: httpx.Request) -> httpx.Response:
        if xview_fail_after is not None and int(request.url.params["start_time"]) >= (
            xview_fail_after
        ):
            return boom()
        wanted = _instances(request)
        return httpx.Response(
            200,
            json={
                "result": [
                    r
                    for r in xview
                    if r["instanceId"] in wanted and _between(request, int(r["endTime"]))
                ]
            },
        )

    def error_search(request: httpx.Request) -> httpx.Response:
        if "error" in fail and fail["error"](request):
            return boom()
        return httpx.Response(
            200, json={"result": [r for r in errors if _between(request, int(r["time"]))]}
        )

    handler = synthetic_handler(
        override={
            f"/api-v2/deploy/{DOMAIN}": httpx.Response(200, json=changes),
            "/api/transaction/time": tx_time,
            "/api/dbsearch/error": error_search,
        }
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    return tools


@pytest.mark.asyncio
async def test_change_impact_numeric_oracle():
    tools = _impact_tools([{"collectTime": CHANGE_T, "instanceId": 1001}])
    out = await tools.apm_change_impact("was-host01")
    assert out["row_count"] == 1
    row = out["rows"][0]
    assert (row["instance_id"], row["instance_name"]) == (1001, "was01_a")
    assert row["change_detected_ms"] == CHANGE_T
    assert row["change_detected_at"] == "2026-09-29T07:00:00+09:00"
    assert row["width_minutes"] == 60
    before, after, delta = row["before"], row["after"], row["delta"]
    assert (before["start_ms"], before["end_ms"]) == (CHANGE_T - HOUR, CHANGE_T)
    assert (after["start_ms"], after["end_ms"]) == (CHANGE_T, CHANGE_T + HOUR)
    # 변경 전: 10건 · 오류 1 · 평균 550 · p95 = 1000(10번째) · 최대 1000 · 오류 기록 2
    assert (before["calls"], before["tx_errors"]) == (10, 1)
    assert before["error_rate"] == pytest.approx(0.1)
    assert before["avg_response_ms"] == pytest.approx(550)
    assert (before["p95_response_ms"], before["max_response_ms"]) == (1000, 1000)
    assert before["error_records"] == 2
    assert before["errors_by_type"] == [{"error_type": "SQL_EXCEPTION", "count": 2}]
    # 변경 후: 21건(경계 포함) · 오류 5 · 평균 47000/21 · p95 = 20번째 = 4000 · 최대 5000 · 기록 6
    assert (after["calls"], after["tx_errors"]) == (21, 5)
    assert after["error_rate"] == pytest.approx(5 / 21)
    assert after["avg_response_ms"] == pytest.approx(47_000 / 21)
    assert (after["p95_response_ms"], after["max_response_ms"]) == (4000, 5000)
    assert after["error_records"] == 6
    assert after["errors_by_type"] == [
        {"error_type": "SQL_EXCEPTION", "count": 4},
        {"error_type": "TIMEOUT", "count": 2},
    ]
    assert delta["calls"] == {"abs": 11, "pct": pytest.approx(110)}
    assert delta["tx_errors"] == {"abs": 4, "pct": pytest.approx(400)}
    assert delta["error_rate"]["abs"] == pytest.approx(5 / 21 * 100 - 10)  # %p
    assert delta["error_rate"]["pct"] == pytest.approx((5 / 21 - 0.1) / 0.1 * 100)
    assert delta["avg_response_ms"]["abs"] == pytest.approx(47_000 / 21 - 550)
    assert delta["p95_response_ms"] == {"abs": 3000, "pct": pytest.approx(300)}
    assert delta["error_records"] == {"abs": 4, "pct": pytest.approx(200)}
    assert CAUSE in out["limits"] and DETECTION in out["limits"]
    assert out["summary"] == {
        "changes": 1,
        "compared": 1,
        "window": out["window"],
        "width_minutes": 60,
    }
    assert out["window"]["minutes"] == 24 * 60
    assert not out.get("partial")


@pytest.mark.asyncio
async def test_change_impact_short_after_window_and_zero_baseline_is_na():
    tools = _impact_tools([{"collectTime": SHORT_T, "instanceId": 1002}])
    out = await tools.apm_change_impact("was-host01")
    row = out["rows"][0]
    assert row["after"]["end_ms"] == NOW_MS  # 지금까지
    assert (
        "[한계] 변경 뒤 구간이 아직 30분이다(비교 폭 60분) — was01_b 2026-09-29T09:30:00+09:00"
    ) in out["limits"]
    assert row["before"]["calls"] == 0 and row["before"]["error_rate"] is None
    assert row["before"]["avg_response_ms"] is None and row["before"]["p95_response_ms"] is None
    assert (row["after"]["calls"], row["after"]["tx_errors"]) == (2, 1)
    assert row["delta"]["calls"] == {"abs": 2, "pct": None}  # 기준 0 = N/A
    assert row["delta"]["error_rate"] == {"abs": None, "pct": None}
    assert row["delta"]["avg_response_ms"] == {"abs": None, "pct": None}


@pytest.mark.asyncio
async def test_change_impact_failed_source_is_none_not_zero():
    tools = _impact_tools(
        [{"collectTime": CHANGE_T, "instanceId": 1001}],
        fail={"error": lambda r: int(r.url.params["start_time"]) >= CHANGE_T},
    )
    out = await tools.apm_change_impact("was-host01")
    row = out["rows"][0]
    assert out["partial"] is True
    assert row["after"]["error_records"] is None and row["after"]["errors_by_type"] is None
    assert row["delta"]["error_records"] == {"abs": None, "pct": None}
    assert row["after"]["calls"] == 21  # X-View 칸은 그대로
    assert any("변경 후 오류 기록 조회 실패" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_change_impact_xview_chunk_failure_blanks_xview_fields():
    """1분 조각 하나라도 실패하면 부분 건수를 그 구간 값으로 내지 않는다."""
    tools = _impact_tools(
        [{"collectTime": CHANGE_T, "instanceId": 1001}], xview_fail_after=CHANGE_T + 30 * MIN
    )
    out = await tools.apm_change_impact("was-host01")
    row = out["rows"][0]
    assert out["partial"] is True
    assert all(
        row["after"][k] is None
        for k in ("calls", "tx_errors", "error_rate", "avg_response_ms", "p95_response_ms")
    )
    assert row["before"]["calls"] == 10 and row["after"]["error_records"] == 6
    assert row["delta"]["calls"] == {"abs": None, "pct": None}
    assert any("변경 후 X-View 조회 실패" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_change_impact_all_sources_failed_is_error():
    tools = _impact_tools(
        [{"collectTime": CHANGE_T, "instanceId": 1001}],
        fail={"error": lambda r: True},
        xview_fail_after=0,
    )
    with pytest.raises(ApmError) as exc:
        await tools.apm_change_impact("was-host01")
    assert exc.value.code == SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_change_impact_without_changes_is_empty_rows():
    tools = _impact_tools([])
    out = await tools.apm_change_impact("was-host01")
    assert out["rows"] == [] and out["summary"]["changes"] == 0
    assert "[한계] 구간 안 변경 감지 0건" in out["limits"] and CAUSE in out["limits"]


@pytest.mark.asyncio
async def test_change_impact_n_compares_most_recent_and_width():
    tools = _impact_tools(
        [
            {"collectTime": CHANGE_T, "instanceId": 1001},
            {"collectTime": SHORT_T, "instanceId": 1002},
        ]
    )
    out = await tools.apm_change_impact("was-host01", n=1, width_minutes=15)
    assert [r["instance_id"] for r in out["rows"]] == [1002]
    assert out["summary"]["changes"] == 2 and out["summary"]["compared"] == 1
    assert out["rows"][0]["width_minutes"] == 15
    assert out["rows"][0]["before"]["start_ms"] == SHORT_T - 15 * MIN
    assert any("변경 감지 2건 중 최근 1건" in x for x in out["limits"])
    full = await tools.apm_change_impact("was-host01", n=1, full=True)
    assert [r["instance_id"] for r in full["rows"]] == [1002, 1001]


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [0, -3, "x"])
async def test_change_impact_width_must_be_positive(width):
    tools = _impact_tools([])
    with pytest.raises(ApmError) as exc:
        await tools.apm_change_impact("was-host01", width_minutes=width)
    assert exc.value.code == INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_source_changes_envelope_is_unchanged_by_shared_helper():
    """`apm_source_changes`는 공용 헬퍼로 뽑은 뒤에도 종전 봉투 그대로다(행·limits)."""
    tools = _impact_tools(
        [
            {"collectTime": CHANGE_T, "instanceId": 1001},
            {"collectTime": SHORT_T, "instanceId": 1002},
            {"collectTime": SHORT_T, "instanceId": 1003},
        ]
    )
    out = await tools.apm_source_changes("was-host01")
    assert out["limits"] == [DETECTION]
    assert [(r["instance_id"], r["change_detected_ms"]) for r in out["rows"]] == [
        (1002, SHORT_T),
        (1001, CHANGE_T),
    ]
    assert set(out["rows"][0]) == {
        "source_id",
        "domain_id",
        "instance_id",
        "instance_name",
        "change_detected_ms",
        "change_detected_at",
    }
    assert "summary" not in out


# ── apm_period_compare ───────────────────────────────────

CUR = ("2026-09-29T08:30:00", "2026-09-29T10:00:00")  # 시간대 없음 = APM_TIMEZONE · 08시로 넓힌다
BASE = ("2026-09-22T08:00:00+09:00", "2026-09-22T10:00:00+09:00")
CUR_START = NOW_MS - 2 * HOUR
BASE_START = NOW_MS - 7 * DAY - 2 * HOUR


def _app(calls: int, failures: int, avg: float, total: int | None, mx: int) -> dict:
    row = {
        "name": "/svc?user=kim",
        "calls": calls,
        "failures": failures,
        "badResponses": 0,
        "responseTime": avg,
        "maxResponseTime": mx,
    }
    if total is not None:
        row["totalResponseTime"] = total
    return row


STATUS = {
    # (인스턴스, 구간 시작) → 행 — 평균의 평균(150)이 아니라 가중 평균(125)이 맞다
    (1001, CUR_START): [_app(100, 5, 200.0, 20_000, 900), _app(300, 15, 100.0, 30_000, 1500)],
    (1001, BASE_START): [_app(200, 2, 200.0, 40_000, 800)],
    (1002, CUR_START): [_app(50, 0, 100.0, 5_000, 300)],
    (1002, BASE_START): [],  # 기준 구간에 없는 인스턴스 — N/A(0으로 채우지 않는다)
}


def _period_tools(status: dict | None = None, *, fail: set | None = None):
    status = STATUS if status is None else status
    calls: list[dict] = []

    def app_status(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        key = (int(params["instance_id"]), int(params["start_time"]))
        if fail and key in fail:
            return httpx.Response(
                500, json={"exception": {"message": "1000 Domain is not connected"}}
            )
        return httpx.Response(200, json={"result": status.get(key, [])})

    handler = synthetic_handler(override={"/api/status/application": app_status})
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    return tools, calls


@pytest.mark.asyncio
async def test_period_compare_numeric_oracle_and_hour_widening():
    tools, calls = _period_tools()
    out = await tools.apm_period_compare("was-host01", *CUR, *BASE)
    # 인스턴스·구간마다 1호출 · 시 경계 · max_row 미전달
    assert sorted((c["instance_id"], c["start_time"], c["end_time"]) for c in calls) == sorted(
        [
            (str(i), str(s), str(s + 2 * HOUR))
            for i in (1001, 1002)
            for s in (CUR_START, BASE_START)
        ]
    )
    assert all("max_row" not in c and c["domain_id"] == str(DOMAIN) for c in calls)
    rows = {r["instance_id"]: r for r in out["rows"]}
    a = rows[1001]
    assert a["current"]["calls"] == 400 and a["current"]["failures"] == 20
    assert a["current"]["failure_rate"] == pytest.approx(0.05)
    assert a["current"]["avg_response_ms"] == pytest.approx(125)  # ≠ (200 + 100) / 2
    assert a["current"]["max_response_ms"] == 1500
    assert a["baseline"]["avg_response_ms"] == pytest.approx(200)
    assert a["delta"]["calls"] == {"abs": 200, "pct": pytest.approx(100)}
    assert a["delta"]["failures"] == {"abs": 18, "pct": pytest.approx(900)}
    assert a["delta"]["failure_rate"]["abs"] == pytest.approx(4)  # %p
    assert a["delta"]["failure_rate"]["pct"] == pytest.approx(400)
    assert a["delta"]["avg_response_ms"] == {"abs": pytest.approx(-75), "pct": pytest.approx(-37.5)}
    assert a["delta"]["max_response_ms"] == {"abs": 700, "pct": pytest.approx(87.5)}
    b = rows[1002]
    assert b["baseline"] is None
    assert all(v == {"abs": None, "pct": None} for v in b["delta"].values())
    assert any(
        "인스턴스 was01_b은 기준 구간 통계 행이 없다 — 증감 N/A" in x for x in out["limits"]
    )
    # 전체 = 인스턴스 합(가중) — 기준 구간에는 1002가 없다
    s = out["summary"]
    assert (s["current"]["calls"], s["current"]["failures"]) == (450, 20)
    assert s["current"]["avg_response_ms"] == pytest.approx(55_000 / 450)
    assert s["current"]["total_response_ms"] == 55_000
    assert (s["baseline"]["calls"], s["baseline"]["avg_response_ms"]) == (200, 200)
    assert s["delta"]["calls"] == {"abs": 250, "pct": pytest.approx(125)}
    assert s["current"]["hour_start"] == "2026-09-29T08:00:00+09:00"
    assert s["current"]["start"] == "2026-09-29T08:30:00+09:00"
    assert s["current"]["hours"] == s["baseline"]["hours"] == 2
    assert s["instances"] == 2
    assert (
        "[한계] 시 단위 통계 — 현재 구간을 시 경계로 맞췄다(요청 2026-09-29T08:30:00+09:00~"
        "2026-09-29T10:00:00+09:00 → 조회 2026-09-29T08:00:00+09:00~2026-09-29T10:00:00+09:00)"
    ) in out["limits"]
    assert (
        "[한계] 시 단위 통계 — 기준 구간을 시 경계로 맞췄다(2026-09-22T08:00:00+09:00~"
        "2026-09-22T10:00:00+09:00)"
    ) in out["limits"]
    assert any(x.startswith("[한계] p95는 싣지 않았다") for x in out["limits"])
    assert not any("두 구간 길이가 다르다" in x for x in out["limits"])
    assert "p95" not in json.dumps([out["rows"], out["summary"]])
    assert "kim" not in json.dumps(out, ensure_ascii=False)  # 애플리케이션 이름은 싣지 않는다
    assert [r["instance_id"] for r in out["rows"]] == [1001, 1002]  # 현재 호출 수 순
    assert not out.get("partial")


@pytest.mark.asyncio
async def test_period_compare_length_difference_missing_weights_and_zero_base():
    status = {
        (1001, CUR_START): [_app(100, 5, 200.0, None, 900)],  # 총 응답시간 칸 없음
        (1001, NOW_MS - 7 * DAY - 3 * HOUR): [_app(0, 0, 0.0, 0, 0)],  # 기준 호출 0
        (1002, CUR_START): [_app(10, 1, 100.0, 1_000, 200)],
        (1002, NOW_MS - 7 * DAY - 3 * HOUR): [_app(10, 0, 100.0, 1_000, 200)],
    }
    tools, _ = _period_tools(status)
    out = await tools.apm_period_compare(
        "was-host01", *CUR, "2026-09-22T07:00:00+09:00", "2026-09-22T10:00:00+09:00"
    )
    assert any(
        "두 구간 길이가 다르다 — 합계 비교 주의(평균·비율은 비교 가능)(현재 2시간 · 기준 3시간)"
        in x
        for x in out["limits"]
    )
    rows = {r["instance_id"]: r for r in out["rows"]}
    assert rows[1001]["current"]["avg_response_ms"] is None  # 가중 평균 재료 없음
    assert any(x.startswith("[한계] 평균 응답시간 계산 불가") for x in out["limits"])
    assert rows[1001]["baseline"]["calls"] == 0
    assert rows[1001]["delta"]["calls"] == {"abs": 100, "pct": None}  # 기준 0 = N/A
    assert rows[1001]["delta"]["failure_rate"] == {"abs": None, "pct": None}
    assert rows[1002]["delta"]["failure_rate"]["abs"] == pytest.approx(10)
    assert out["summary"]["current"]["avg_response_ms"] is None  # 재료가 하나라도 없으면


@pytest.mark.asyncio
async def test_period_compare_failed_instance_is_not_counted_as_zero():
    tools, _ = _period_tools(fail={(1002, BASE_START)})
    out = await tools.apm_period_compare("was-host01", *CUR, *BASE)
    assert out["partial"] is True
    rows = {r["instance_id"]: r for r in out["rows"]}
    assert rows[1002]["baseline"] is None and rows[1001]["baseline"]["calls"] == 200
    assert out["summary"]["baseline"]["calls"] is None  # 부분 합을 전체로 내지 않는다
    assert out["summary"]["delta"]["calls"] == {"abs": None, "pct": None}
    limits = out["limits"]
    assert any("기준 구간 전체 합계 계산 불가 — 조회 실패 인스턴스 1002" in x for x in limits)
    assert any(x.startswith("[한계] 기준 구간 통계 조회 실패(인스턴스 1002)") for x in limits)
    every = {(i, s) for i in (1001, 1002) for s in (CUR_START, BASE_START)}
    tools, _ = _period_tools(fail=every)
    with pytest.raises(ApmError) as exc:
        await tools.apm_period_compare("was-host01", *CUR, *BASE)
    assert exc.value.code == SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_period_compare_n_limits_rows_not_summary():
    tools, _ = _period_tools()
    out = await tools.apm_period_compare("was-host01", *CUR, *BASE, n=1)
    assert [r["instance_id"] for r in out["rows"]] == [1001]
    assert out["summary"]["current"]["calls"] == 450
    assert any("인스턴스 2개 중 현재 구간 호출 수 상위 1개" in x for x in out["limits"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        ("2026-09-29T10:00:00", "2026-09-29T10:00:00", *BASE),  # start == end
        ("2026-09-29T11:00:00", "2026-09-29T10:00:00", *BASE),
        (*CUR, "2026-09-22T10:00:00", "2026-09-22T09:00:00"),
        (None, CUR[1], *BASE),
        ("어제", CUR[1], *BASE),
        (*CUR, BASE[0], ""),
    ],
)
async def test_period_compare_invalid_periods_without_http(args):
    tools, calls = _period_tools()
    with pytest.raises(ApmError) as exc:
        await tools.apm_period_compare("was-host01", *args)
    assert exc.value.code == INVALID_ARGUMENT and calls == []


# ── MCP 등록(작업 경로) ───────────────────────────────────


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_new_tools_are_registered_as_jobs(tmp_path):
    from apm_gateway.application.jobs import JobManager
    from apm_gateway.application.spool import Spool
    from apm_gateway.interface.server import audit_job_finished, create_server
    from conftest import synthetic_fixtures

    from apm_gateway.config import JobConfig

    tx = next(
        fx for fx in synthetic_fixtures() if fx["request"]["template"] == "/api/transaction/time"
    )["response"]["body_json"]["result"][0]

    def app_status(request: httpx.Request) -> httpx.Response:
        key = (int(request.url.params["instance_id"]), int(request.url.params["start_time"]))
        return httpx.Response(200, json={"result": STATUS.get(key, [])})

    handler = synthetic_handler(
        override={
            "/api/status/application": app_status,
            "/api/transaction/guid": httpx.Response(200, json={"result": [tx]}),
        }
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    cfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    mcp = create_server(tools, jobs=jobs)
    try:
        compare = _json(
            await mcp.call_tool(
                "apm_period_compare",
                {
                    "hostname": "was-host01",
                    "current_start": CUR[0],
                    "current_end": CUR[1],
                    "baseline_start": BASE[0],
                    "baseline_end": BASE[1],
                    "owner": "investigation:x",
                },
            )
        )
        assert compare["tool"] == "apm_period_compare" and compare["total_row_count"] == 2
        changes = _json(await mcp.call_tool("apm_change_impact", {"hostname": "was-host01"}))
        assert changes["tool"] == "apm_change_impact" and "summary" in changes
        trace = _json(await mcp.call_tool("apm_transaction_trace", {"guid": "guid-0000"}))
        assert trace["tool"] == "apm_transaction_trace" and trace["summary"]["transactions"] == 1
        assert trace["rows"][0]["trace_order"] == 1
        bad = _json(await mcp.call_tool("apm_transaction_trace", {"guid": "a b"}))
        assert bad["error"] == INVALID_ARGUMENT
    finally:
        await jobs.aclose()
