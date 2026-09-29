"""plans/121 TP-1.5 · TP-1.9 · TP-1.13 — 호스트 조사 결과 모양 · `metric_trend` kind · 본체 감사.

고정하는 계약:
- TP-1.5 ① 거부·실패는 텍스트 결과다 — 실물 `run_host_inspect` → 실물 `_finalize_task`에서
  사유 문구가 그대로 나오고 TypeError 문구가 없다(1단은 조사 플래그 off에서도 도구가 노출된다).
  ② 성공한 DB 프로파일은 표로 최종화된다("처리 결과가 없습니다." 아님) — 서버 계약 키 불변.
  ③ 0행 성공은 결정적 요약이 원인을 담는다(process_query와 같은 규약).
- TP-1.9 ① `kind` = 입력 파서 `query_targets`(닫힌 열거) — 실물 `DBHubClient` 검증을 통과한다.
  ② 없음·모호(「디스크」)면 구조화 거부 + MCP 0. ③ 여러 종류면 종류별 호출(최대 4)을 잇는다.
  ④ 본체 `_METRIC_KINDS` = mcp_server `_METRIC_KIND_MAP`(서버 원문을 AST로 읽는다 —
  양방향 import 0).
- TP-1.13 ① 레지스트리 handler가 task당 감사 1건(`log_investigation` · backend)을 남긴다.
  ② `composite.audit_enabled`가 False면 0건 · 감사 실패는 조회를 막지 않는다.

LLM·DB·네트워크 0.
"""

from __future__ import annotations

import ast
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import src.orchestration.host_inspect as hi
import src.orchestration.investigation_audit as audit_mod
from src.config import DBHubConfig, QueryConfig, load_config
from src.dbhub.client import DBHubClient
from src.orchestration.host_inspect import DEGRADED_KEY, run_host_inspect
from src.orchestration.result_aggregator import _finalize_task
from src.orchestration.subagents import SUBAGENT_REGISTRY
from src.security.audit_logger import INVESTIGATION_FAILED, INVESTIGATION_OK
from src.state import create_initial_state

_ROOT = Path(__file__).resolve().parents[2]
_HOST = {
    "parsed_requirements": {"filter_conditions": [{"field": "hostname", "value": "svweb001"}]},
    "conversation_context": {},
}
_SERVER = {
    "parsed_requirements": {"filter_conditions": [{"field": "server_name", "value": "svweb001"}]},
    "conversation_context": {},
}


def _cfg(*, investigation: bool, audit: bool = True):
    # 캐시된 싱글톤을 고치면 뒤 테스트로 샌다 — 사본을 고친다.
    cfg = load_config().model_copy(deep=True)
    cfg.composite.investigation_enabled = investigation
    cfg.composite.audit_enabled = audit
    return cfg


class _Raw:
    class _Text:
        def __init__(self, text):
            self.text = text

    def __init__(self, payload):
        self.content = [self._Text(json.dumps(payload, ensure_ascii=False))]


def _real_client(monkeypatch, payload_for) -> list[tuple[str, dict]]:
    """실물 `DBHubClient`(인자 검증 포함) + 가짜 MCP 호출 — `get_db_client` 자리에 꽂는다."""
    client = DBHubClient(DBHubConfig(source_name="polestar_cm_gp"), QueryConfig())
    calls: list[tuple[str, dict]] = []

    async def _fake_call(tool_name, arguments):
        calls.append((tool_name, dict(arguments)))
        return _Raw(payload_for(tool_name, arguments))

    monkeypatch.setattr(client, "_ensure_connected", lambda: None)
    monkeypatch.setattr(client, "_call_tool", _fake_call)

    @asynccontextmanager
    async def _ctx(app_config, db_id=None):
        yield client

    monkeypatch.setattr(hi, "get_db_client", _ctx)
    return calls


def _db_payload(tool_name, arguments):
    rows = [{"stat_date": "2026-09-28 10", "avg_val": 12.5}] if "metric" in tool_name else [
        {"prop_name": "OSType", "prop_value": "Linux"}
    ]
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-09-28T10:00:00",
            "source_kind": "polestar_db", "source": arguments.get("source", ""),
            "engine": "postgres", **({"kind": arguments["kind"]} if "kind" in arguments else {})}


# ── TP-1.5 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flag_off_refusal_finalizes_to_reason_text(mock_config):
    """★ 1단 운영 결함 — 플래그 off 거부가 TypeError 문구가 아니라 사유 문구로 끝난다."""
    task = {"task_id": "t1", "agent": "host_inspect", "sub_query": "web-01 서버의 OS 정보",
            "order": 1}
    res = await run_host_inspect(task, {}, llm=None, app_config=_cfg(investigation=False))
    assert res[DEGRADED_KEY] == "composite_investigation_disabled"
    assert "organized_data" not in res
    out = await _finalize_task(task, res, create_initial_state("q"), AsyncMock(), mock_config)
    assert out["text"] == res["error"]
    assert "TypeError" not in out["text"] and "결과 생성 중 오류" not in out["text"]
    assert "string indices" not in out["text"]


@pytest.mark.asyncio
async def test_success_finalizes_to_table_not_empty_text(monkeypatch, mock_config):
    _real_client(monkeypatch, _db_payload)
    task = {"task_id": "t1", "agent": "host_inspect", "sub_query": "svweb001 OS 정보 보여줘",
            "order": 1}
    res = await run_host_inspect(task, _HOST, llm=None, app_config=_cfg(investigation=True))
    state = create_initial_state("svweb001 OS 정보 보여줘")
    out = await _finalize_task(
        task, res, state, FakeListChatModel(responses=["OS는 Linux입니다."]), mock_config,
        stream_user_response=False,
    )
    assert out["text"] != "처리 결과가 없습니다."
    assert "OSType" in out["text"] and "Linux" in out["text"]


@pytest.mark.asyncio
async def test_empty_success_keeps_deterministic_summary(monkeypatch, mock_config):
    _real_client(monkeypatch, lambda t, a: {**_db_payload(t, a), "rows": [], "row_count": 0})
    task = {"task_id": "t1", "agent": "host_inspect", "sub_query": "svweb001 OS 정보", "order": 1}
    res = await run_host_inspect(task, _HOST, llm=None, app_config=_cfg(investigation=True))
    out = await _finalize_task(task, res, create_initial_state("q"), AsyncMock(), mock_config)
    assert out["text"] == "OS 구성 조회 결과 0행입니다(조회 시각 2026-09-28T10:00:00)."


# ── TP-1.9 ──────────────────────────────────────────────────────────────


def _trend_isolated(targets):
    return {**_SERVER, "parsed_requirements": {**_SERVER["parsed_requirements"],
                                               "query_targets": targets}}


@pytest.mark.asyncio
async def test_metric_trend_passes_real_client_validation(monkeypatch):
    calls = _real_client(monkeypatch, _db_payload)
    res = await run_host_inspect(
        {"sub_query": "svweb001 사용률 추세 보여줘"}, _trend_isolated(["서버", "CPU"]),
        llm=None, app_config=_cfg(investigation=True),
    )
    assert "error" not in res, res
    assert calls == [("polestar_metric_trend",
                      {"source": "polestar_cm_gp", "server_name": "svweb001", "kind": "cpu"})]
    assert res["kind"] == "cpu" and res["rows"][0]["avg_val"] == 12.5
    assert "지표 CPU · 기간은 최근 24시간(시간 단위)입니다." in res["organized_data"]["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("targets", [[], ["서버"], ["디스크"], ["CPU", "디스크"], ["네트워크"]])
async def test_metric_trend_without_clear_kind_is_refused_without_mcp(monkeypatch, targets):
    calls = _real_client(monkeypatch, _db_payload)
    res = await run_host_inspect(
        {"sub_query": "svweb001 사용률 추세"}, _trend_isolated(targets),
        llm=None, app_config=_cfg(investigation=True),
    )
    assert res[DEGRADED_KEY] == "metric_kind_unresolved"
    assert res["final_response"] == res["error"]
    assert calls == []


@pytest.mark.asyncio
async def test_metric_trend_multiple_kinds_are_joined(monkeypatch):
    calls = _real_client(monkeypatch, _db_payload)
    res = await run_host_inspect(
        {"sub_query": "svweb001 사용률 추세"}, _trend_isolated(["CPU", "메모리"]),
        llm=None, app_config=_cfg(investigation=True),
    )
    assert [a["kind"] for _, a in calls] == ["cpu", "memory"]
    assert res["kinds"] == ["cpu", "memory"] and res["row_count"] == 2
    assert [r["kind"] for r in res["rows"]] == ["cpu", "memory"]
    assert "지표 CPU, 메모리" in res["organized_data"]["summary"]


@pytest.mark.asyncio
async def test_metric_trend_partial_kind_failure_is_reported(monkeypatch):
    def _payload(tool, args):
        if args.get("kind") == "memory":
            return {"error": "통계 테이블 없음"}
        return _db_payload(tool, args)

    _real_client(monkeypatch, _payload)
    res = await run_host_inspect(
        {"sub_query": "svweb001 사용률 추세"}, _trend_isolated(["CPU", "메모리"]),
        llm=None, app_config=_cfg(investigation=True),
    )
    assert res["kinds"] == ["cpu"] and res["kind_errors"] == {"memory": "통계 테이블 없음"}
    summary = res["organized_data"]["summary"]
    assert "메모리 추세는 조회하지 못했습니다(통계 테이블 없음)." in summary


def test_client_metric_kinds_match_server_map():
    tree = ast.parse((_ROOT / "mcp_server" / "mcp_server" / "polestar_tools.py").read_text("utf-8"))
    server_keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_METRIC_KIND_MAP":
            server_keys = {k.value for k in node.value.keys}
    assert server_keys, "서버 _METRIC_KIND_MAP을 찾지 못했다"
    assert set(DBHubClient._METRIC_KINDS) == server_keys
    assert set(hi._METRIC_KIND_BY_TARGET.values()) <= server_keys


# ── TP-1.13 ─────────────────────────────────────────────────────────────


@pytest.fixture
def audit_calls(monkeypatch):
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(audit_mod, "log_investigation", _fake)
    return calls


@pytest.mark.asyncio
async def test_registry_handler_audits_once_per_task(monkeypatch, audit_calls):
    _real_client(monkeypatch, _db_payload)
    handler = SUBAGENT_REGISTRY["host_inspect"].handler
    isolated = {**_HOST, "request_id": "r1", "user_id": "u1", "thread_id": "th1"}
    await handler({"task_id": "t1", "sub_query": "svweb001 OS 정보"}, isolated,
                  llm=None, app_config=_cfg(investigation=True))
    assert len(audit_calls) == 1
    rec = audit_calls[0]
    assert rec["backend"] == "mcp_server" and rec["outcome"] == INVESTIGATION_OK
    assert rec["profile"] == "os_config" and rec["task_id"] == "t1"
    assert rec["request_id"] == "r1" and rec["user_id"] == "u1" and rec["thread_id"] == "th1"
    assert rec["targets"][0]["hostname"] == "svweb001"


@pytest.mark.asyncio
async def test_refusal_is_audited_as_failed(audit_calls):
    handler = SUBAGENT_REGISTRY["host_inspect"].handler
    await handler({"task_id": "t1", "sub_query": "web-01 서버의 OS 정보"}, {},
                  llm=None, app_config=_cfg(investigation=False))
    assert audit_calls[0]["outcome"] == INVESTIGATION_FAILED
    assert audit_calls[0]["degraded"] == [{"reason": "composite_investigation_disabled"}]


@pytest.mark.asyncio
async def test_audit_disabled_writes_nothing(audit_calls):
    handler = SUBAGENT_REGISTRY["host_inspect"].handler
    await handler({"task_id": "t1", "sub_query": "web-01 OS 정보"}, {},
                  llm=None, app_config=_cfg(investigation=False, audit=False))
    assert audit_calls == []


@pytest.mark.asyncio
async def test_audit_failure_does_not_block_result(monkeypatch):
    async def _boom(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(audit_mod, "log_investigation", _boom)
    handler = SUBAGENT_REGISTRY["host_inspect"].handler
    res = await handler({"task_id": "t1", "sub_query": "web-01 OS 정보"}, {},
                        llm=None, app_config=_cfg(investigation=False))
    assert res[DEGRADED_KEY] == "composite_investigation_disabled"


@pytest.mark.asyncio
async def test_process_query_success_audit_carries_resolver_sql(monkeypatch, audit_calls):
    async def _fake_run(task, isolated, *, llm, app_config):
        return {
            "organized_data": {"summary": "s", "rows": [{"pid": 1}]},
            "query_results": [{"pid": 1}],
            "process_query": {"db_id": "polestar_cm_gp", "server_name": "svweb001",
                              "hostname": "svweb001.local", "captured_at": "t"},
        }

    wrapped = audit_mod.audited_investigation(_fake_run, audit_mod.BACKEND_PROCESS_API)
    await wrapped({"task_id": "t1"}, {}, llm=None, app_config=_cfg(investigation=False))
    rec = audit_calls[0]
    assert rec["backend"] == "process_api" and rec["outcome"] == INVESTIGATION_OK
    assert len(rec["commands"]) == 1 and "'svweb001'" in rec["commands"][0]
    assert rec["commands"][0].lstrip().upper().startswith("SELECT")


@pytest.mark.asyncio
async def test_handler_exception_is_audited_and_reraised(audit_calls):
    async def _raise(task, isolated, *, llm, app_config):
        raise RuntimeError("boom")

    wrapped = audit_mod.audited_investigation(_raise, audit_mod.BACKEND_MCP)
    with pytest.raises(RuntimeError):
        await wrapped({"task_id": "t1"}, {}, llm=None, app_config=_cfg(investigation=True))
    assert audit_calls[0]["degraded"] == [{"reason": "exception", "detail": "RuntimeError"}]
