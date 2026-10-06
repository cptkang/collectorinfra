"""plans/134 W5 — GUID 추적 `apm_transaction_trace`(N-13 · A-3) · 허용목록 `/api/transaction/guid`
(N-8 · COV-TX-GUID).

목 Open API 서버(`/__mock/hits` = 실제로 나간 쿼리 키·값)와 MockTransport만 쓴다. 응답은 스펙 5.6.4
`TransactionData` 필드명 기반 합성이다(실응답 모양 W10). 외부 네트워크 0.
"""

from __future__ import annotations

import json
import time
import urllib.request

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import NotAllowedError, check_request
from apm_gateway.application.jobs import MASKED_KEY
from apm_gateway.domain.errors import INVALID_ARGUMENT, SOURCE_UNAVAILABLE, ApmError
from conftest import NOW_MS, _fx, make_tools, synthetic_fixtures, write_fixtures

GUID = "g-77"
DOMAINS = (1000, 2000, 3000)
TOPOLOGY = "[한계] GUID가 같은 거래 묶음이다 — 호출 관계(토폴로지)를 뜻하지 않는다"
CLOCK_SKEW = "[한계] 소스·도메인 시계 차이를 보정하지 않았다 — 순서는 각 원천 시각 기준"


def _tx(domain: int, instance: int, name: str, txid: int, start_s: int, guid: str = GUID) -> dict:
    return {
        "domainId": domain,
        "domainName": f"d{domain}",
        "instanceId": instance,
        "instanceName": name,
        "applicationName": f"/svc/{txid}?user=kim",
        "txid": str(txid),
        "guid": guid,
        "responseTime": 1000,
        "startTime": str(NOW_MS - start_s * 1000),
        "endTime": str(NOW_MS - start_s * 1000 + 800),
        "clientIp": "192.168.10.77",
        "userId": "kimcs01",
        "clientId": "client-kim",
        "errorType": "",
    }


GUID_ROWS = [
    _tx(1000, 1001, "was01_a", 9100, 50),
    _tx(1000, 1001, "was01_a", 9100, 50),  # 원천이 같은 거래를 두 번 — 중복 제거 대상
    _tx(2000, 2001, "api_b", 9100, 55),  # 같은 txid · 다른 도메인 — 별개 거래
    _tx(3000, 3001, "batch_c", 9300, 30),
    _tx(3000, 3001, "batch_c", 9400, 20, guid="g-other"),
]


def _fixtures(guid_rows: list[dict] | None = None) -> list[dict]:
    out = [fx for fx in synthetic_fixtures() if fx["request"]["template"] != "/api/domain"]
    out.append(
        _fx(
            "/api/domain",
            {"result": [{"domainId": d, "name": f"d{d}", "description": ""} for d in DOMAINS]},
        )
    )
    out.append(_fx("/api/transaction/guid", {"result": guid_rows or GUID_ROWS}))
    return out


def _hits(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/__mock/hits", timeout=5) as r:
        return json.loads(r.read())


def _post(base: str, path: str, body: dict) -> None:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5).read()


@pytest.fixture
def trace(mock_server_factory, tmp_path):
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", _fixtures()), "connected")
    tools, _ = make_tools(base)
    return base, tools


# ── 허용목록 ───────────────────────────────────────────────


def test_guid_path_allowed_with_required_keys_only():
    # T-COV-TX-GUID-allow · T-COV-TX-GUID-arg-{domain_id·guid·start_time·end_time}
    q = {"domain_id": "1", "guid": "g", "start_time": "1", "end_time": "2"}
    assert check_request("GET", "/api/transaction/guid", q).template == "/api/transaction/guid"
    for missing in q:
        with pytest.raises(NotAllowedError):
            check_request(
                "GET", "/api/transaction/guid", {k: v for k, v in q.items() if k != missing}
            )
    for extra in ({"time_pattern": "yyyyMMdd"}, {"token": "x"}, {"instance_id": "1"}):
        with pytest.raises(NotAllowedError):
            check_request("GET", "/api/transaction/guid", {**q, **extra})
    with pytest.raises(NotAllowedError):
        check_request("POST", "/api/transaction/guid", q)
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api/transaction/guid.xml", q)


# ── 범위 · 창 · 정렬 · 중복 제거 ────────────────────────────


@pytest.mark.asyncio
async def test_trace_without_hostname_asks_every_domain_once_and_orders_by_start(trace):
    base, tools = trace
    out = await tools.apm_transaction_trace(GUID)
    calls = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/guid"]
    assert [c["domain_id"] for c in calls] == ["1000", "2000", "3000"]
    assert all(
        c == {
            "domain_id": c["domain_id"],
            "guid": GUID,
            "start_time": str(NOW_MS - 3_600_000),
            "end_time": str(NOW_MS),
        }
        for c in calls
    )
    # 원천 시작 시각 순 · 같은 (소스, 도메인, txid) 1행 · 같은 txid라도 도메인이 다르면 별개
    got = [(r["domain_id"], r["txid"], r["trace_order"]) for r in out["rows"]]
    assert got == [(2000, "9100", 1), (1000, "9100", 2), (3000, "9300", 3)]
    assert all(r["guid"] == GUID for r in out["rows"])
    assert out["rows"][0]["profile_ref"] == {
        "source_id": "default",
        "domain_id": 2000,
        "txid": "9100",
        "time_ms": NOW_MS - 55_000 + 800,
    }
    s = out["summary"]
    assert (s["guid"], s["transactions"], s["domains_queried"]) == (GUID, 3, 3)
    assert (s["domains_with_hits"], s["domains_failed"], s["sources"]) == (3, 0, ["default"])
    assert s["first_start_ms"] == NOW_MS - 55_000
    assert s["last_end_ms"] == NOW_MS - 30_000 + 800
    assert s["span_ms"] == 25_000 + 800
    assert s["instances"] == ["api_b", "was01_a", "batch_c"]
    assert TOPOLOGY in out["limits"] and CLOCK_SKEW in out["limits"]
    assert "[한계] 기간 미지정 — 최근 60분" in out["limits"]
    assert not out.get("partial")
    assert out["window"]["minutes"] == 60


@pytest.mark.asyncio
async def test_trace_masks_people_identifiers(trace):
    _, tools = trace
    out = await tools.apm_transaction_trace(GUID)
    row = out["rows"][0]
    assert row["user_id"] == "k***" and row["client_id"] == "c***"
    assert row["client_ip"] == "192.168.*.*" and "user=<v>" in row["application"]
    assert "kimcs01" not in json.dumps(out, ensure_ascii=False)
    assert set(out[MASKED_KEY]) == {"client_id", "user_id"}


@pytest.mark.asyncio
async def test_trace_window_priority_explicit_then_around_then_default(trace):
    base, tools = trace
    around = NOW_MS - 40_000
    out = await tools.apm_transaction_trace(GUID, around_ms=around)
    q = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/guid"][-1]
    assert (q["start_time"], q["end_time"]) == (str(around - 300_000), str(around + 300_000))
    assert "[한계] 기간 미지정 — ±5분(앞 결과 시각 기준)" in out["limits"]
    out = await tools.apm_transaction_trace(GUID, around_ms=around, around_minutes=2)
    assert "[한계] 기간 미지정 — ±2분(앞 결과 시각 기준)" in out["limits"]
    # 명시 기간이 앞선다 — 기본값 고지 없음
    out = await tools.apm_transaction_trace(
        GUID, reference_time="2026-09-29T10:00:00+09:00", lookback_minutes=15, around_ms=around
    )
    q = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/guid"][-1]
    assert (q["start_time"], q["end_time"]) == (str(NOW_MS - 900_000), str(NOW_MS))
    assert not any("기간 미지정" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_trace_zero_hits_is_empty_rows_not_error(trace):
    _, tools = trace
    out = await tools.apm_transaction_trace("g-none")
    assert out["rows"] == [] and out["summary"]["transactions"] == 0
    assert (
        "[한계] 구간 안에서 GUID 거래를 찾지 못했다(구간 2026-09-29T09:00:00+09:00~"
        "2026-09-29T10:00:00+09:00)"
    ) in out["limits"]
    assert CLOCK_SKEW not in out["limits"] and not out.get("partial")


@pytest.mark.asyncio
async def test_trace_partial_domain_failure_and_all_failed(trace):
    base, tools = trace
    _post(base, "/__mock/guid_fail_domains", {"domains": [2000]})
    out = await tools.apm_transaction_trace(GUID)
    assert out["partial"] is True
    assert [(r["domain_id"], r["txid"]) for r in out["rows"]] == [(1000, "9100"), (3000, "9300")]
    assert out["summary"]["domains_failed"] == 1 and out["summary"]["domains_with_hits"] == 2
    assert any(x.startswith("[한계] GUID 거래 조회 실패(도메인 2000)") for x in out["limits"])
    _post(base, "/__mock/guid_fail_domains", {"domains": list(DOMAINS)})
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_trace(GUID)
    assert exc.value.code == SOURCE_UNAVAILABLE  # 실패를 0건으로 세지 않는다


@pytest.mark.asyncio
async def test_trace_hostname_scopes_to_matched_domains():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/api/domain":
            return httpx.Response(
                200, json={"result": [{"domainId": d, "name": f"d{d}"} for d in (1000, 2000)]}
            )
        if path == "/api/instance":
            host = "was-host01" if params["domain_id"] == "1000" else "other-host"
            iid = 1001 if params["domain_id"] == "1000" else 2001
            return httpx.Response(
                200, json={"result": [{"instanceId": iid, "name": f"i{iid}", "hostName": host}]}
            )
        if path == "/api/transaction/guid":
            calls.append(params)
            return httpx.Response(200, json={"result": [_tx(1000, 1001, "i1001", 9100, 50)]})
        return httpx.Response(404, text="no fixture")

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await tools.apm_transaction_trace(GUID, hostname="was-host01")
    assert [c["domain_id"] for c in calls] == ["1000"]
    assert out["instance_resolution"]["matched"] is True
    assert out["summary"]["domains_queried"] == 1 and CLOCK_SKEW not in out["limits"]


@pytest.mark.asyncio
async def test_trace_drops_rows_of_another_guid_and_says_so():
    """원천이 guid 인자를 무시해도 다른 GUID 거래를 묶음에 넣지 않는다(의미 미검증 W10)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/domain":
            return httpx.Response(200, json={"result": [{"domainId": 1000, "name": "d"}]})
        if path == "/api/instance":
            return httpx.Response(200, json={"result": [{"instanceId": 1, "name": "a"}]})
        if path == "/api/transaction/guid":
            return httpx.Response(200, json={"result": GUID_ROWS})
        return httpx.Response(404)

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await tools.apm_transaction_trace(GUID)
    assert all(r["guid"] == GUID for r in out["rows"])
    assert any("다른 GUID의 거래 1건" in x for x in out["limits"])


# ── 인자 검증 ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", ["", "   ", "a b", "a\tb", "a\x00b", "a​b", "a b", "x" * 257, None]
)
async def test_guid_validation_rejects_without_http(bad):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"result": []})

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_trace(bad)
    assert exc.value.code == INVALID_ARGUMENT and seen == []


@pytest.mark.asyncio
async def test_guid_trimmed_and_256_chars_accepted(trace):
    base, tools = trace
    await tools.apm_transaction_trace("  " + "g" * 256 + " ")
    q = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/guid"][-1]
    assert q["guid"] == "g" * 256


def test_guid_validation_is_fast_on_1mb_input():
    """긴 입력을 끝까지 훑지 않는다 — 1MB 공격 문자열(Known Mistakes 2026-10-02)."""
    from apm_gateway.application.tools import ApmTools

    started = time.perf_counter()
    for attack in ("a" * (1 << 20), "a " * (1 << 19), "​" * (1 << 20)):
        with pytest.raises(ApmError):
            ApmTools._guid(attack)
    assert time.perf_counter() - started < 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs", [{"around_ms": 0}, {"around_ms": -5}, {"around_ms": 1, "around_minutes": 0}]
)
async def test_around_arguments_must_be_positive(trace, kwargs):
    _, tools = trace
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_trace(GUID, **kwargs)
    assert exc.value.code == INVALID_ARGUMENT


# ── 자격증명 경계(새 경로도 중앙 경계를 지난다 · SPEC-coverage §4.1) ────────


@pytest.mark.asyncio
async def test_trace_output_and_failure_reason_carry_no_credentials():
    canary = "hunter2-CANARY"

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/api/domain":
            return httpx.Response(
                200, json={"result": [{"domainId": d, "name": f"d{d}"} for d in (1000, 2000)]}
            )
        if path == "/api/instance":
            return httpx.Response(200, json={"result": [{"instanceId": 1, "name": "a"}]})
        if path == "/api/transaction/guid" and params["domain_id"] == "2000":
            return httpx.Response(
                400, json={"exception": {"message": f"bad request password={canary} -p{canary}"}}
            )
        if path == "/api/transaction/guid":
            tx = _tx(1000, 1, "a", 9100, 50)
            tx.update(
                applicationName=f"/login?password={canary}&user=kim",
                clientId=canary,
                userId=canary,
                password=canary,
            )
            return httpx.Response(200, json={"result": [tx]})
        return httpx.Response(404)

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await tools.apm_transaction_trace(GUID)
    assert out["partial"] is True and out["row_count"] == 1
    assert any("GUID 거래 조회 실패(도메인 2000): apm_api_error" in x for x in out["limits"])
    dumped = json.dumps(out, ensure_ascii=False)
    assert canary not in dumped and "CANARY" not in dumped
