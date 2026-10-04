"""plans/134 W2 통합 검증 결함 수정 — W2V-B2 · W2V-B6 · W2V-G4(검증자 재현은
`test_plan134_w2_verify.py` — 여기서는 그 밖의 경로를 고정한다).

- B2(a): 카탈로그에 없는 지표는 빼고 조회(시계열은 일부 빠지면 partial · 전부 모르면
  `invalid_argument` — 런타임은 기본 추세).
- B2(b): 원천이 정렬 기준을 거부하면 그 조건·행 수 없이 다시 받아 로컬 정렬(표기 정규화
  snake↔camel만) · 대응 칸이 없으면 원천 기본 순서 · 보기는 성공.
- B6: 변경 감지 행에 `change_detected_at`(ISO 8601 · `APM_TIMEZONE`).
- G4: 카탈로그 군 단위 모양 위반 = 검증 불가(시계열 검증을 건너뛰고 partial).
- 지표를 빼거나 정렬 기준 없이 다시 받았으면 봉투 `disclosures`에 `apm_unresolved_condition`
  (사용자용 한 줄 · 값은 지표·정렬 이름만 — 작업 밖 직접 호출은 예약 키 `_unresolved`).

외부 네트워크 0 — `httpx.MockTransport`만 쓴다.
"""

from __future__ import annotations

import httpx
import pytest
from apm_gateway.adapters.jennifer.api import JenniferApi
from apm_gateway.application.jobs import UNRESOLVED_KEY, UNRESOLVED_KIND
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError
from conftest import NOW_MS, make_tools, synthetic_handler


def _tools(handler, **kw):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler), **kw)
    return tools


def _sql_row(name: str, calls: int, avg: float) -> dict:
    return {
        "name": name,
        "calls": calls,
        "failures": 0,
        "badResponses": 0,
        "responseTime": avg,
        "maxResponseTime": int(avg * 2),
        "totalResponseTime": int(calls * avg),
    }


def _sort_refusing(seen: list[dict], rows: list[dict], message: str = "Unknown sort_by_metrics"):
    """정렬 기준을 주면 500으로 거부하고, 없으면 행을 원천 순서대로 준다."""
    inner = synthetic_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/status/sql":
            params = dict(request.url.params)
            seen.append(params)
            if "sort_by_metrics" in params:
                return httpx.Response(
                    500, json={"exception": {"message": f"{message}: {params['sort_by_metrics']}"}}
                )
            return httpx.Response(200, json={"result": rows})
        return await inner(request)

    return handler


_ROWS = [_sql_row("select fast", 90, 5.0), _sql_row("select slow", 3, 900.0)]


# ── B2(b) 정렬 기준 거부 → 조건 없이 다시 받아 로컬 정렬 ─────────────


def test_sort_field_normalizes_snake_to_camel_only():
    assert JenniferApi.status_sort_field("sql", "response_time") == "response_time_avg_ms"
    assert JenniferApi.status_sort_field("sql", "max_response_time") == "max_response_time_ms"
    assert JenniferApi.status_sort_field("sql", "total_response_time") == "total_response_ms"
    assert JenniferApi.status_sort_field("application", "sql_time_per_transaction") == (
        "sql_ms_per_tx"
    )
    assert JenniferApi.status_sort_field("sql", "sql_time_per_transaction") is None  # 앱 전용
    for guess in ("visitors", "slow", "elapsed", "errCount"):  # 뜻 추측 없음
        assert JenniferApi.status_sort_field("sql", guess) is None, guess


@pytest.mark.asyncio
async def test_refused_sort_by_is_retried_without_it_and_sorted_locally():
    seen: list[dict] = []
    out = await _tools(_sort_refusing(seen, _ROWS)).apm_status_stats(
        "sql", "was-host01", sort_by="response_time", n=1
    )
    assert [("sort_by_metrics" in q, "max_row" in q) for q in seen] == [
        (True, True),
        (False, False),
    ]  # 거부 뒤 조건·행 수 없이 1회 더
    assert [r["name"] for r in out["rows"]] == ["select slow"]  # 평균 응답시간 내림차순 상위 1
    note = next(x for x in out["limits"] if "받지 않아" in x)
    assert "원천이 정렬 기준 response_time를 받지 않아 전체를 받아 정렬했다" in note
    assert "Unknown sort_by_metrics: response_time" in note  # 원천 사유 그대로
    assert out.get("partial") is not True and "error" not in out


@pytest.mark.asyncio
async def test_refused_unmapped_sort_by_keeps_source_order():
    seen: list[dict] = []
    out = await _tools(_sort_refusing(seen, _ROWS)).apm_status_stats(
        "sql", "was-host01", sort_by="visitors", n=1
    )
    assert len(seen) == 2 and [r["name"] for r in out["rows"]] == ["select fast"]
    assert any("원천 기본 순서" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_no_retry_when_failure_is_not_a_refusal():
    seen: list[dict] = []
    tools = _tools(_sort_refusing(seen, _ROWS, message="1000 Domain is not connected"))
    with pytest.raises(ApmError) as exc:
        await tools.apm_status_stats("sql", "was-host01", sort_by="calls")
    assert exc.value.code == SOURCE_UNAVAILABLE and len(seen) == 1  # 원천 미접속은 다시 묻지 않음


@pytest.mark.asyncio
async def test_retry_failure_is_reported_with_its_own_reason():
    inner = synthetic_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/status/sql":
            if "sort_by_metrics" in request.url.params:
                return httpx.Response(500, json={"exception": {"message": "bad sort"}})
            return httpx.Response(500, json={"exception": {"message": "still broken"}})
        return await inner(request)

    with pytest.raises(ApmError) as exc:
        await _tools(handler).apm_status_stats("sql", "was-host01", sort_by="calls")
    assert "still broken" in exc.value.reason


# ── B2(a) 시계열 일부 지표 모름 ────────────────────────────────


@pytest.mark.asyncio
async def test_series_drops_unknown_metric_and_marks_partial():
    seen: list[str] = []
    inner = synthetic_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dbmetrics/instance":
            seen.append(request.url.params["metrics"])
        return await inner(request)

    out = await _tools(handler).apm_metrics(
        "series", hostname="was-host01", metrics=["heap_used", "heap_usd"], lookback_minutes=10
    )
    assert set(seen) == {"heap_used"} and out["row_count"] == 2 * 6
    assert out["partial"] is True
    assert any("heap_usd(후보: heap_used" in x and "빼고 조회" in x for x in out["limits"])


# ── G4 군 단위 모양 위반 = 검증 불가 ────────────────────────────


@pytest.mark.asyncio
async def test_invalid_instance_group_skips_validation_for_series():
    seen: list[str] = []
    inner = synthetic_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/metrics":
            return httpx.Response(200, json={"result": {"instance": "heap", "sql": ["count"]}})
        if request.url.path == "/api/dbmetrics/instance":
            seen.append(request.url.params["metrics"])
        return await inner(request)

    tools = _tools(handler)
    catalog = await tools.apm_metrics("catalog")
    assert catalog["partial"] is True and catalog["row_count"] == 1
    assert any("군 instance" in x and "검증 불가" in x for x in catalog["limits"])
    sql_only = await tools.apm_metrics("catalog", scope="sql")
    assert not any("모양 위반" in x for x in sql_only["limits"])  # 다른 군만 보면 고지 없음
    out = await tools.apm_metrics(
        "series", hostname="was-host01", metrics=["anything_new"], lookback_minutes=10
    )
    assert set(seen) == {"anything_new"} and out["partial"] is True
    assert any("instance 군 모양 위반" in x for x in out["limits"])


# ── B6 사람이 읽는 변경 감지 시각 ──────────────────────────────


@pytest.mark.asyncio
async def test_change_rows_carry_iso_time_in_configured_zone():
    inner = synthetic_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api-v2/deploy/"):
            return httpx.Response(200, json=[{"collectTime": NOW_MS - 1500, "instanceId": 1001}])
        return await inner(request)

    seoul = await _tools(handler).apm_source_changes("was-host01")
    row = seoul["rows"][0]
    assert row["change_detected_ms"] == NOW_MS - 1500  # 원시 값 유지
    assert row["change_detected_at"] == "2026-09-29T09:59:58+09:00"
    utc = await _tools(handler, extra={"APM_TIMEZONE": "UTC"}).apm_source_changes("was-host01")
    assert utc["rows"][0]["change_detected_at"] == "2026-09-29T00:59:58+00:00"


# ── 선택 조건을 빼거나 바꿨을 때의 의무 고지(apm_unresolved_condition) ─────


def _mcp(tmp_path, handler):
    from apm_gateway.application.jobs import JobManager
    from apm_gateway.application.spool import Spool
    from apm_gateway.interface.server import create_server

    from apm_gateway.config import JobConfig

    spool = tmp_path / "spool"
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(handler),
        extra={"APM_SPOOL_DIR": str(spool)},
    )
    jobs = JobManager(
        Spool(spool), JobConfig(spool_dir=spool), envelope=tools.ok, error_envelope=tools.err
    )
    return create_server(tools, jobs=jobs), tools, jobs


def _call(result) -> dict:
    import json

    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _unresolved(out: dict) -> list[str]:
    return [d["text"] for d in out.get("disclosures") or [] if d["kind"] == UNRESOLVED_KIND]


@pytest.mark.asyncio
async def test_dropped_metric_is_disclosed_as_unresolved_condition(tmp_path):
    mcp, tools, jobs = _mcp(tmp_path, synthetic_handler())
    args = {"hostname": "was-host01", "lookback_minutes": 30}
    some = _call(
        await mcp.call_tool("apm_runtime_health", {**args, "metrics": ["proc_cpu", "nope_x"]})
    )
    assert _unresolved(some) == ["요청한 지표 nope_x는 지표 목록에 없어 빼고 조회했습니다"]
    assert UNRESOLVED_KEY not in some
    every = _call(await mcp.call_tool("apm_runtime_health", {**args, "metrics": ["nope_x"]}))
    (text,) = _unresolved(every)
    assert text.startswith("요청한 지표 nope_x를 지표 목록에서 찾지 못해 기본 지표(")
    series = _call(
        await mcp.call_tool(
            "apm_metrics",
            {"mode": "series", **args, "metrics": ["heap_used", "nope_x"]},
        )
    )
    assert _unresolved(series) == ["요청한 지표 nope_x는 지표 목록에 없어 빼고 조회했습니다"]
    known = _call(await mcp.call_tool("apm_runtime_health", {**args, "metrics": ["proc_cpu"]}))
    assert "disclosures" not in known  # 뺀 조건이 없으면 고지 없음
    direct = await tools.apm_runtime_health("was-host01", lookback_minutes=30, metrics=["nope_x"])
    assert direct[UNRESOLVED_KEY] and "disclosures" not in direct  # 작업 밖은 예약 키
    await jobs.aclose()


@pytest.mark.asyncio
async def test_refused_sort_is_disclosed_as_unresolved_condition(tmp_path):
    seen: list[dict] = []
    mcp, _, jobs = _mcp(tmp_path, _sort_refusing(seen, _ROWS))
    mapped = _call(
        await mcp.call_tool(
            "apm_status_stats",
            {"kind": "sql", "hostname": "was-host01", "sort_by": "response_time"},
        )
    )
    assert _unresolved(mapped) == [
        "원천이 정렬 기준 response_time을(를) 받지 않아 그 조건 없이 받아 직접 정렬했습니다"
    ]
    unmapped = _call(
        await mcp.call_tool(
            "apm_status_stats", {"kind": "sql", "hostname": "was-host01", "sort_by": "visitors"}
        )
    )
    assert _unresolved(unmapped) == [
        "원천이 정렬 기준 visitors을(를) 받지 않아 그 조건 없이 받아 원천 기본 순서로 실었습니다"
    ]
    await jobs.aclose()


@pytest.mark.asyncio
async def test_unresolved_and_masked_disclosures_coexist(tmp_path):
    from apm_gateway.application.jobs import MASKED_KIND, JobManager
    from apm_gateway.application.spool import Spool

    from apm_gateway.config import JobConfig

    spool = tmp_path / "spool"
    jobs = JobManager(
        Spool(spool),
        JobConfig(spool_dir=spool),
        envelope=lambda *a, **k: {},
        error_envelope=lambda *a: {},
    )
    envelope = {
        "rows": [],
        "limits": [],
        UNRESOLVED_KEY: ["a", "a", "b"],
        "_masked_fields": ["user_id"],
    }

    async def call() -> dict:
        return envelope

    out = await jobs.execute("apm_runtime_health", call, principal="anonymous")
    kinds = [d["kind"] for d in out["disclosures"]]
    assert kinds == [UNRESOLVED_KIND, UNRESOLVED_KIND, MASKED_KIND]
    assert [d["text"] for d in out["disclosures"][:2]] == ["a", "b"]
    await jobs.aclose()
