"""plans/130 독립 검증(verify-130 · 2026-10-06) — 게이트웨이 보안 경계.

1. 허용목록 `/api/business` 정확 일치 — 대소문자·이중 슬래시·상위 경로·인코딩·공백·`token` 키·
   대소문자만 다른 쿼리 키·GET 외 메서드는 거절한다(W2 테스트가 덮지 않은 변형).
2. 업무 캐시(TTL 10분)가 소스·도메인 경계를 넘지 않는다 — 한 소스의 업무 정의로 다른 소스 인스턴스를
   업무 근거로 잡지 않는다 · 다른 도메인 질의가 앞 도메인 캐시를 쓰지 않는다.
3. 큰 입력 — `business` 1MB는 HTTP 0 · 데이터 도구 `instance_name` 1MB도 `query`·`business`처럼
   HTTP 전에 `invalid_argument`(V130-5 교정) · 사유(400자)·감사(마스킹·상한)로 묶인다.
실 제니퍼 0 — httpx MockTransport(W2 테스트의 합성 데이터)만 쓴다.
"""

from __future__ import annotations

import json
import logging

import pytest
from apm_gateway.adapters.jennifer.allowlist import NotAllowedError, check_request
from apm_gateway.domain.errors import INVALID_ARGUMENT, ApmError
from test_plan130_w2_business import _one, _two


@pytest.fixture
def calls() -> list:
    return []


# ── 1. 허용목록 정확 일치 ───────────────────────────────────────────────────────

@pytest.mark.parametrize(("method", "path", "params"), [
    ("GET", "/API/business", {"domain_id": "1000"}),
    ("GET", "//api/business", {"domain_id": "1000"}),
    ("GET", "/api/business/../instance", {"domain_id": "1000"}),
    ("GET", "/api/business%2F", {"domain_id": "1000"}),
    ("GET", "/api/business ", {"domain_id": "1000"}),
    ("GET", "/api/business", {"domain_id": "1000", "token": "x"}),
    ("GET", "/api/business", {"domain_id": "1000", "DOMAIN_ID": "2000"}),
    ("HEAD", "/api/business", {"domain_id": "1000"}),
    ("DELETE", "/api/business", {"domain_id": "1000"}),
])
def test_business_endpoint_rejects_every_non_exact_variant(method, path, params) -> None:
    with pytest.raises(NotAllowedError):
        check_request(method, path, params)


# ── 2. 업무 캐시 경계 ──────────────────────────────────────────────────────────

async def test_business_cache_is_keyed_by_source_and_domain(tmp_path, calls) -> None:
    """bank 도메인 1000에만 「결제」 정의가 있다 — common 인스턴스는 업무 근거가 아니다."""
    tools = _two(tmp_path, calls)
    first = await tools.apm_instance_map(business="결제")
    business_rows = [r for r in first["rows"] if "business" in r["match_kinds"]]
    assert business_rows and {r["source_id"] for r in business_rows} == {"bank"}
    assert set(tools._business_defs) >= {("bank", 1000), ("common", 1000)}
    assert tools._business_defs[("common", 1000)][1] == []

    calls.clear()
    again = await tools.apm_instance_map(business="결제")
    assert calls == []  # 캐시 적중 — 같은 답
    assert [r["instance_id"] for r in again["rows"]] == [r["instance_id"] for r in first["rows"]]


async def test_other_domain_query_does_not_reuse_the_first_domain_cache(tmp_path, calls) -> None:
    tools = _one(tmp_path, calls)
    whole = await tools.apm_instance_map(business="결제")
    assert {r["domain_id"] for r in whole["rows"] if "business" in r["match_kinds"]} == {1000}
    narrowed = await tools.apm_instance_map(business="결제", domain_id=1130)
    assert all(r["domain_id"] == 1130 for r in narrowed["rows"]), narrowed["rows"]
    assert not [r for r in narrowed["rows"] if "business" in r["match_kinds"]]


# ── 3. 큰 입력 ─────────────────────────────────────────────────────────────────

async def test_megabyte_business_is_rejected_before_http(tmp_path, calls) -> None:
    tools = _one(tmp_path, calls)
    with pytest.raises(ApmError) as exc:
        await tools.apm_instance_map(business="가" * 1_000_000)
    assert exc.value.code == INVALID_ARGUMENT and "200자 이하" in exc.value.reason
    assert len(exc.value.reason) < 200
    assert calls == []


async def test_megabyte_instance_name_is_bounded_in_reason_and_audit(tmp_path, calls,
                                                                     caplog) -> None:
    from apm_gateway.interface.server import create_server

    mcp = create_server(_one(tmp_path, calls))
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        result = await mcp.call_tool("apm_app_health", {"instance_name": "a" * 1_000_000})
    text = (result[0] if isinstance(result, tuple) else result)[0].text
    payload = json.loads(text)
    assert payload["error"] == "invalid_argument"  # V130-5 교정 — 길이 검사(HTTP 전)
    assert len(payload["reason"]) <= 400 and len(text) < 2_000
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert lines and all(len(line) < 1_000 for line in lines)
    assert calls == []


async def test_instance_name_pii_is_masked_in_reason_and_audit(tmp_path, calls, caplog) -> None:
    from apm_gateway.interface.server import create_server

    mcp = create_server(_one(tmp_path, calls))
    with caplog.at_level(logging.INFO, logger="apm_gateway.audit"):
        result = await mcp.call_tool("apm_app_health", {"instance_name": "kim@example.com"})
    text = (result[0] if isinstance(result, tuple) else result)[0].text
    assert "kim@example.com" not in text and "<email>" in text
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert lines and all("kim@example.com" not in line for line in lines)


async def test_megabyte_instance_name_is_rejected_before_http(tmp_path, calls) -> None:
    tools = _one(tmp_path, calls)
    with pytest.raises(ApmError) as exc:
        await tools.apm_app_health(instance_name="a" * 1_000_000)
    assert exc.value.code == INVALID_ARGUMENT
    assert calls == []
