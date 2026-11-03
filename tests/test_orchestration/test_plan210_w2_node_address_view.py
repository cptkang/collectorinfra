"""plans/210 W2 §3.6 — 채팅 노드 주소 보기(`apm.node_address`) 본체 배선 (D-300 ⑧ · D-274).

- 레지스트리: 보기가 게이트웨이 `apm_node_addresses` · 창 none · 대상 none · 조건 `domain_id`
  (도구 인자 `domain_ids`)로 선언된다.
- 처리기: 보기를 고르면 도구를 **1회** 부른다(첫 홉·대상 해석 없음 · 도메인 조건은 목록).
  게이트웨이가 도구를 등록하지 않았으면(플래그 off — FastMCP `Unknown tool`) 비활성 사유로 답을
  끝낸다. `ok`가 아닌 소스·인스턴스는 사유를 결정적 답 줄에 싣는다.
- 단어 매칭 0: 원문에 「노드 주소」가 있어도 보기를 고르지 않았으면 도구를 부르지 않는다.

실 게이트웨이·실 LLM 0 — 모의 게이트웨이 세션(실 `SourceMcpSession` 래퍼를 지난다).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.orchestration.apm import node_address as na
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 11, 3, 10, 0, 0)
VIEW = "apm.node_address"
TOOL = "apm_node_addresses"
LABEL = "WAS 인스턴스가 도는 노드(호스트) 주소 — K8s 파드의 노드 IP"  # 답 줄의 범위(보기 라벨)


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-11-03T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


def _row(iid: int, status: str, host: str = "") -> dict:
    return {"source_id": "b0", "domain_id": 1000, "instance_id": iid,
            "instance_name": f"order-api-{iid}", "ip": f"10.139.0.{iid % 250}",
            "host_address": host, "pod": True, "status": status}


def _ok_envelope() -> dict:
    return _env(TOOL, [_row(1, "ok", "10.136.1.7"), _row(2, "ok", "10.136.1.8")],
                node_address_sources=[{"source_id": "b0", "status": "ok", "reason": "",
                                       "domains": 1, "failed_domains": [], "instances": 2,
                                       "instance_status": {"ok": 2}}])


def _mixed_envelope() -> dict:
    reason = "웹 계정 미설정(JENNIFER_<ID>_WEB_USER·_WEB_PASSWORD) — 노드 주소를 조회하지 않았다"
    return _env(TOOL, [_row(1, "ok", "10.136.1.7"), _row(2, "not_found"),
                       {**_row(3, "disabled"), "source_id": "cm"}],
                partial=True,
                limits=[f"[한계] APM 소스 cm 노드 주소 disabled — {reason}"],
                node_address_sources=[
                    {"source_id": "b0", "status": "ok", "reason": "", "domains": 1,
                     "failed_domains": [], "instances": 2,
                     "instance_status": {"ok": 1, "not_found": 1}},
                    {"source_id": "cm", "status": "disabled", "reason": reason, "domains": 1,
                     "failed_domains": [{"domain_id": 1000, "status": "disabled"}],
                     "instances": 1, "instance_status": {"disabled": 1}}])


class _Gateway:
    """모의 게이트웨이 — `registered=False`면 FastMCP 미등록 도구 응답(isError)을 낸다."""

    def __init__(self, envelope: dict | None = None, *, registered: bool = True) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.envelope = envelope or _ok_envelope()
        self.registered = registered

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def _single(self, name: str, args: dict) -> dict:
        if name == TOOL:
            return self.envelope
        return _env(name, [{"tool_row": name, "hostname": args.get("hostname"),
                            "source_id": "b0", "instance_id": 11, "instance_name": "w1",
                            "domain_id": 1}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if name == TOOL and not self.registered:
            return SimpleNamespace(content=[SimpleNamespace(text=f"Unknown tool: {name}")],
                                   isError=True)
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


def _install(monkeypatch, gw: _Gateway) -> _Gateway:
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    return gw


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens='{"apm": "gw"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


def _isolated(query: str) -> dict:
    return {"parsed_requirements": {"filter_conditions": [], "time_range": None, "limit": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice",
            "original_user_query": query, "user_query": query}


async def _run(task: dict, query: str = "order-api 파드가 도는 노드 주소") -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "sub_query": query,
                                   "input_from": [], **task},
                                  _isolated(query), llm=None, app_config=_cfg(), now=NOW)


# ── 레지스트리 ──────────────────────────────────────────────────────────────────


def test_node_address_view_is_declared() -> None:
    view = {v.id: v for v in aq.apm_views()}[VIEW]
    assert view.tool == TOOL and view.window == "none" and view.target == "none"
    assert view.capability == "was_instance"
    assert view.label == LABEL
    assert not view.required_input and not view.first_hop
    [arg] = view.args
    assert (arg.name, arg.type, arg.min, arg.tool_arg) == ("domain_id", "int", 0, "domain_ids")
    assert [v.id for v in aq.apm_views()][-1] == VIEW  # 목록 끝(앞 보기 순서 불변)


# ── 처리기: 보기 → 도구 1회 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_view_calls_tool_once_without_first_hop(monkeypatch) -> None:
    gw = _install(monkeypatch, _Gateway())
    res = await _run({"views": [VIEW]})
    assert [n for n, _ in gw.calls if not n.startswith("apm_job_")] == [TOOL]
    [args] = gw.named(TOOL)
    assert "domain_ids" not in args and "hostname" not in args
    assert res["query_results"][0]["host_address"] == "10.136.1.7"
    assert res["source_status"][0]["status"] == "ok"
    assert res[aq.ANSWER_LINES_KEY] == [f"{LABEL}: 노드 주소 확인 2/2개"]


@pytest.mark.asyncio
async def test_domain_condition_is_sent_as_list(monkeypatch) -> None:
    gw = _install(monkeypatch, _Gateway())
    await _run({"views": [VIEW], "view_args": {VIEW: {"domain_id": 1000}}})
    [args] = gw.named(TOOL)
    assert args["domain_ids"] == [1000]


@pytest.mark.asyncio
async def test_sources_are_passed_through(monkeypatch) -> None:
    gw = _install(monkeypatch, _Gateway())
    await _run({"views": [VIEW], "sources": ["b0"]})
    [args] = gw.named(TOOL)
    assert args["source_ids"] == ["b0"]  # 분해 `sources`를 검증해 그대로 싣는다(M-5)


# ── 게이트웨이 플래그 off(도구 미등록) → 비활성 사유로 끝 ─────────────────────────


@pytest.mark.asyncio
async def test_unregistered_tool_ends_with_disabled_reason(monkeypatch) -> None:
    gw = _install(monkeypatch, _Gateway(registered=False))
    res = await _run({"views": [VIEW]})
    assert len(gw.named(TOOL)) == 1
    assert res["error"]
    text = res["final_response"]
    assert "게이트웨이 노드 주소 조회 비활성(JENNIFER_NODE_ADDRESS_ENABLED)" in text
    assert res["source_status"][0]["status"] == "unavailable"
    reasons = [f["reason"] for f in res[aq.META_KEY]["failures"]]
    assert reasons == [f"{na.NODE_ADDRESS_DISABLED}: {na.DISABLED_REASON}"]


def test_missing_tool_error_only_for_node_address_tool() -> None:
    text = "제니퍼 도구 오류(apm_node_addresses): Unknown tool: apm_node_addresses"
    assert na.missing_tool_error(TOOL, text) == f"{na.NODE_ADDRESS_DISABLED}: {na.DISABLED_REASON}"
    assert na.missing_tool_error(TOOL, "제니퍼 도구 오류(apm_node_addresses): boom") is None
    assert na.missing_tool_error("apm_metrics", "Unknown tool: apm_metrics") is None


# ── ok 아닌 소스·인스턴스 → 사유를 답에 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_ok_sources_and_rows_carry_reasons(monkeypatch) -> None:
    _install(monkeypatch, _Gateway(_mixed_envelope()))
    res = await _run({"views": [VIEW]})
    lines = res[aq.ANSWER_LINES_KEY]
    assert lines[0] == (
        f"{LABEL}: 노드 주소 확인 1/3개(확인 못 함 — not_found 1개 · disabled 1개)")
    assert lines[1].startswith(f"{LABEL}: 소스 cm 노드 주소 disabled — 웹 계정 미설정")
    assert res["source_status"][0]["status"] == "partial"  # 봉투 partial — 완료로 세지 않는다
    assert {r["status"] for r in res["query_results"]} == {"ok", "not_found", "disabled"}


def test_lines_ignore_other_aggregates() -> None:
    assert na.node_address_lines({"view": "apm.instances", "summary": {}}) is None


# ── 단어 매칭 0 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_words_alone_do_not_pick_the_view(monkeypatch) -> None:
    gw = _install(monkeypatch, _Gateway())
    await _run({"views": ["apm.instances"]}, query="order-api 파드가 도는 노드 주소 노드 IP")
    assert gw.named(TOOL) == []
