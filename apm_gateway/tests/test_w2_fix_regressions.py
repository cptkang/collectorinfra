"""plans/134 W1 통합 검증 결함 수정(W2 수정 라운드) — M-1 · L-3 · L-5.

- M-1: `error_type` 접두 변형이면 오류 기록이 0건 — API에는 정규화 이름을 먼저 묻고 0건이면
  `ERROR_`·`WARNING_` 접두 표기를 차례로(최대 3회) 물어 처음 비지 않은 결과를 쓴다. 맞은 표기는
  `[한계]`에 적는다(운영 명명 U-13 미확정 · W10).
- L-3: 프로파일 행의 중첩 `transaction.instance_oid`도 「전체 파일 전용」(화면 행·미리보기에서
  뺀다).
- L-5: 식별자 가림·자격증명 제거를 실제로 적용한 응답이면 봉투 `disclosures`에
  `apm_masked_fields`(칸 이름만 · 값 없음).

외부 네트워크 0 — 127.0.0.1 목 Open API와 `httpx.MockTransport`만 쓴다.
"""

from __future__ import annotations

import json
import urllib.request

import httpx
import pytest
from apm_gateway.adapters.jennifer.api import JenniferApi
from apm_gateway.application.jobs import (
    MASKED_KEY,
    MASKED_KIND,
    JobManager,
    _strip,
    masked_disclosure,
)
from apm_gateway.application.spool import Spool
from apm_gateway.domain.credentials import ROOT_FIELD, scrub, scrub_detail
from apm_gateway.interface.server import create_server
from conftest import (
    DOMAIN,
    NOW_MS,
    make_tools,
    synthetic_fixtures,
    synthetic_handler,
    write_fixtures,
)

from apm_gateway.config import JobConfig

SECRET = "Sup3rS3cretPW"


def _hits(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/__mock/hits", timeout=5) as r:
        return json.loads(r.read())


def _error_queries(base: str) -> list[str | None]:
    return [
        h["query"].get("error_type")
        for h in _hits(base)
        if h["template"] == "/api/dbsearch/error"
    ]


def _fixtures(**errors_override) -> list[dict]:
    fx = synthetic_fixtures()
    for f in fx:
        if f["request"]["template"] == "/api/dbsearch/error" and errors_override:
            for r in f["response"]["body_json"]["result"]:
                r.update(errors_override)
    return fx


# ── M-1 error_type 표기 ─────────────────────────────────────


def test_error_type_variants_order_and_cap():
    assert JenniferApi.error_type_variants("warning_x") == ["X", "ERROR_X", "WARNING_X"]
    assert JenniferApi.error_type_variants("ERROR_OUTOFMEMORY") == [
        "OUTOFMEMORY",
        "ERROR_OUTOFMEMORY",
        "WARNING_OUTOFMEMORY",
    ]
    assert len(JenniferApi.error_type_variants("SQL_EXCEPTION")) == 3


@pytest.mark.asyncio
async def test_prefixed_error_type_asks_normalized_name_first(mock_server_factory, tmp_path):
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", _fixtures()), "connected")
    tools, _ = make_tools(base)
    out = await tools.apm_events("was-host01", record="error", error_type="ERROR_SQL_EXCEPTION")
    assert out["row_count"] == 1 and out["rows"][0]["error_type"] == "SQL_EXCEPTION"
    assert _error_queries(base) == ["SQL_EXCEPTION"]  # 첫 표기에서 찾았다 — 재질의 없음
    assert any("API 표기 SQL_EXCEPTION로 조회" in x for x in out["limits"])
    assert out["errors_by_type"] == [{"error_type": "SQL_EXCEPTION", "count": 1}]


@pytest.mark.asyncio
async def test_prefixed_data_is_found_on_retry(mock_server_factory, tmp_path):
    # 운영 데이터가 접두 표기(ERROR_OUTOFMEMORY)인데 사용자가 접두 없이 물은 경우
    fx = _fixtures(errorType="ERROR_OUTOFMEMORY")
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", fx), "connected")
    tools, _ = make_tools(base)
    out = await tools.apm_events("was-host01", record="error", error_type="outofmemory")
    assert _error_queries(base) == ["OUTOFMEMORY", "ERROR_OUTOFMEMORY"]
    assert out["row_count"] == 1
    note = next(x for x in out["limits"] if "오류 유형 API 표기" in x)
    assert "ERROR_OUTOFMEMORY로 조회" in note and "앞 표기 OUTOFMEMORY 0건" in note
    # 이벤트 쪽(접두 무시)과 어긋나지 않는다
    events = await tools.apm_events("was-host01", error_type="outofmemory")
    assert events["row_count"] == 1
    assert events["errors_by_type"] == [{"error_type": "ERROR_OUTOFMEMORY", "count": 1}]


@pytest.mark.asyncio
async def test_unknown_error_type_tries_three_spellings_then_says_so(
    mock_server_factory, tmp_path
):
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", _fixtures()), "connected")
    tools, _ = make_tools(base)
    out = await tools.apm_events("was-host01", record="error", error_type="NO_SUCH")
    assert _error_queries(base) == ["NO_SUCH", "ERROR_NO_SUCH", "WARNING_NO_SUCH"]  # 최대 3회
    assert out["row_count"] == 0 and "error" not in out
    assert any("모두 오류 기록 0건" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_without_error_type_asks_once_without_key(mock_server_factory, tmp_path):
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", _fixtures()), "connected")
    tools, _ = make_tools(base)
    out = await tools.apm_events("was-host01", record="error")
    assert _error_queries(base) == [None]
    assert not any("오류 유형 API 표기" in x for x in out["limits"])


# ── L-3 중첩 「전체 파일 전용」 ──────────────────────────────


def test_strip_nested_column_without_mutating_input():
    rows = [
        {"a": 1, "transaction": {"instance_oid": 5, "txid": "1"}},
        {"a": 2, "transaction": None},
        "not-a-row",
    ]
    out = _strip(rows, frozenset({"transaction.instance_oid", "a"}))
    assert out == [{"transaction": {"txid": "1"}}, {"transaction": None}, "not-a-row"]
    assert rows[0] == {"a": 1, "transaction": {"instance_oid": 5, "txid": "1"}}  # 원본 그대로


def _profile_handler(profile_lines: int):
    tx = {
        "domainId": DOMAIN,
        "instanceId": 1001,
        "txid": "1",
        "instanceOid": 50_000,
        "userId": "kimcs01",
        "clientId": "client-kim",
    }
    fx = synthetic_fixtures()
    for f in fx:
        tpl = f["request"]["template"]
        if tpl == "/api/transaction/txid":
            f["response"]["body_json"] = {"result": [tx]}
        if tpl == "/api/transaction/profile.txt":
            f["response"]["body_text"] = "\n".join(f"line {i}" for i in range(profile_lines))
    return synthetic_handler(fx)


def _mcp(tmp_path, handler):
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


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_profile_nested_instance_oid_is_file_only(tmp_path):
    mcp, _, jobs = _mcp(tmp_path, _profile_handler(profile_lines=100))
    args = {"hostname": "was-host01", "domain_id": DOMAIN, "txid": "1", "time_ms": NOW_MS}
    out = _json(await mcp.call_tool("apm_transaction_profile", args))
    assert "instance_oid" not in out["rows"][0]["transaction"]
    assert out["rows"][0]["transaction"]["txid"] == "1"
    job_id = out["artifact"]["job_id"]  # 프로파일 전문이 있어 결과 파일로 갔다
    status = _json(await mcp.call_tool("apm_job_status", {"job_id": job_id}))
    assert "instance_oid" not in status["rows"][0]["transaction"]  # 미리보기에서도 뺀다
    chunk = _json(await mcp.call_tool("apm_job_read", {"job_id": job_id, "chunk": 0}))
    assert chunk["rows"][0]["transaction"]["instance_oid"] == 50_000  # 결과 파일에는 남는다
    short = _mcp(tmp_path / "s", _profile_handler(profile_lines=3))
    small = _json(await short[0].call_tool("apm_transaction_profile", args))
    assert "instance_oid" not in small["rows"][0]["transaction"] and "artifact" not in small
    await jobs.aclose()
    await short[2].aclose()


# ── L-5 가림 고지 ───────────────────────────────────────────


def test_scrub_detail_reports_field_names_only():
    body = {
        "result": [
            {"name": "app", "password": "pw1", "props": [{"key": "DB_PASSWORD", "value": "x"}]},
            {"cmd": f"jdbc:mysql://app:{SECRET}@db/x", "ok": "plain"},
        ]
    }
    cleaned, notes, masked = scrub_detail(body)
    assert masked == {"password", "value", "cmd"} and notes == []
    assert scrub(body) == (cleaned, notes)  # 종전 함수는 그대로
    assert SECRET not in json.dumps(cleaned) and "pw1" not in json.dumps(cleaned)
    _, _, root = scrub_detail(f"Authorization: Bearer {SECRET}")
    assert root == {ROOT_FIELD}
    _, _, nothing = scrub_detail({"a": "plain", "sessionId": 12})
    assert nothing == set()


def test_masked_disclosure_lists_names_with_cap():
    one = masked_disclosure({"user_id", "client_id"})
    assert one == {
        "kind": MASKED_KIND,
        "text": "개인정보·자격증명 보호를 위해 값을 가린 칸: client_id, user_id",
    }
    many = masked_disclosure({f"f{i:02d}" for i in range(25)})
    assert many["text"].endswith("f19 외 5개")


@pytest.mark.asyncio
async def test_identifier_masking_is_disclosed_without_values(tmp_path):
    mcp, tools, jobs = _mcp(tmp_path, synthetic_handler())
    out = _json(
        await mcp.call_tool(
            "apm_slow_transactions", {"hostname": "was-host01", "lookback_minutes": 5}
        )
    )
    assert out["disclosures"] == [
        {
            "kind": "apm_masked_fields",
            "text": "개인정보·자격증명 보호를 위해 값을 가린 칸: client_id, user_id",
        }
    ]
    assert "kimcs01" not in json.dumps(out, ensure_ascii=False) and MASKED_KEY not in out
    direct = await tools.apm_slow_transactions("was-host01", lookback_minutes=5)
    assert direct[MASKED_KEY] == ["client_id", "user_id"]  # 작업 밖 직접 호출은 예약 키
    await jobs.aclose()


@pytest.mark.asyncio
async def test_credential_scrub_is_disclosed(tmp_path):
    fx = synthetic_fixtures()
    for f in fx:
        if f["request"]["template"] == "/api/activeService/list":
            for r in f["response"]["body_json"]["result"]:
                r["runningFullText"] = f"connect jdbc:mysql://app:{SECRET}@db.example/x"
    mcp, _, jobs = _mcp(tmp_path, synthetic_handler(fx))
    out = _json(await mcp.call_tool("apm_active_services", {"hostname": "was-host01"}))
    (note,) = out["disclosures"]
    assert note["kind"] == "apm_masked_fields" and "runningFullText" in note["text"]
    assert SECRET not in json.dumps(out, ensure_ascii=False)
    await jobs.aclose()


@pytest.mark.asyncio
async def test_text_response_scrub_is_disclosed(tmp_path):
    fx = synthetic_fixtures()
    for f in fx:
        if f["request"]["template"] == "/api/transaction/profile.txt":
            f["response"]["body_text"] = f"START\n  HTTP-HEADER Authorization: Basic {SECRET}\n"
    mcp, _, jobs = _mcp(tmp_path, synthetic_handler(fx))
    args = {"hostname": "was-host01", "domain_id": DOMAIN, "txid": "9000", "time_ms": NOW_MS}
    out = _json(await mcp.call_tool("apm_transaction_profile", args))
    (note,) = out["disclosures"]
    assert ROOT_FIELD in note["text"] and "user_id" in note["text"]
    assert SECRET not in json.dumps(out, ensure_ascii=False)
    await jobs.aclose()


@pytest.mark.asyncio
async def test_no_masking_means_no_disclosure_and_errors_have_none(tmp_path):
    mcp, _, jobs = _mcp(tmp_path, synthetic_handler())
    out = _json(await mcp.call_tool("apm_runtime_health", {"hostname": "was-host01"}))
    assert "disclosures" not in out and MASKED_KEY not in out
    bad = _json(await mcp.call_tool("apm_status_stats", {"kind": "cpu", "hostname": "x"}))
    assert "disclosures" not in bad
    await jobs.aclose()
