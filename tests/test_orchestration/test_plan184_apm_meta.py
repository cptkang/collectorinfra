"""plans/184 W2a — APM 선택 메타·source_status 예외 원문(S4) · 첫 홉 이중 괄호(M1) · 연결 실패
거부 고지 중복 제거(M2).

가짜 LLM·가짜 게이트웨이만 쓴다(실 LLM·네트워크 0).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import pytest
import structlog

from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.orchestration.apm.calls import _Call, _exception_error, _JobScope
from src.orchestration.apm.first_hop import _first_hop_text
from src.orchestration.apm.targets import _lookup
from src.orchestration.entity_link import UNLINKED
from src.routing.registry import ViewSpec
from tests.test_orchestration import test_plan134_w2_verify as w2

#: 예외 원문 대역 — DSN·내부 주소가 섞인 문자열(메타에 남으면 안 된다).
SECRET = "postgresql://svc:pw-9x@10.1.2.3/pdb · 내부 응답 원문"
#: 로그 단언용 — 비밀번호 뒤(호스트·DB·본문)만. 서버 로그에서 DSN 비밀번호는 가려진다(plans/167).
SECRET_LOGGED = SECRET.split("@", 1)[1]


@pytest.fixture
def bound_rid():
    structlog.contextvars.bind_contextvars(request_id="feed0184")
    yield "feed0184"
    structlog.contextvars.unbind_contextvars("request_id")


class ReadTimeout(Exception):  # noqa: N818 — httpx 예외 이름 흉내
    """이름이 Error/Exception으로 끝나지 않는 예외(httpx 계열 흉내)."""


class _RaisingLLM:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def ainvoke(self, messages: Any) -> Any:
        raise self.exc


def _down_factory():
    @asynccontextmanager
    async def down(url, headers):
        raise aq.SourceMcpError(SECRET)
        yield  # pragma: no cover

    return down


# ── S4: 보기 선택 재시도 오류 사유 ───────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [RuntimeError(SECRET), ReadTimeout(SECRET)],
                         ids=["error_suffix", "plain_name"])
async def test_retry_selection_error_keeps_kind_and_request_id_only(exc, bound_rid,
                                                                    caplog) -> None:
    with caplog.at_level(logging.WARNING):
        views, args, error = await aq._retry_selection(
            _RaisingLLM(exc), {"sub_query": "응답시간"}, ["was_performance"], {"thread_id": "t"})
    assert (views, args) == ([], {})
    assert error == f"{type(exc).__name__} (요청 {bound_rid})"
    assert SECRET not in error
    assert SECRET_LOGGED in caplog.text and bound_rid in caplog.text, "원문은 로그에 남는다"


@pytest.mark.asyncio
async def test_select_views_meta_error_has_no_exception_text(monkeypatch, bound_rid) -> None:
    """선택 메타(`selection.error`)에 예외 원문이 남지 않는다 — 종류와 요청 번호만."""
    monkeypatch.setattr(aq, "_uncovered", lambda views, areas, by_id: list(areas) or ["x"])
    sel = await aq._select_views({"sub_query": "질의", "areas": ["was_performance"]},
                                 {"thread_id": "t"}, _RaisingLLM(RuntimeError(SECRET)), {})
    assert sel.info["retried"] is True
    assert sel.info["error"] == f"RuntimeError (요청 {bound_rid})"


# ── S4·M2: 게이트웨이 연결 실패 거부 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_down_source_status_reason_hides_exception(monkeypatch, bound_rid,
                                                                 caplog) -> None:
    monkeypatch.setattr(aq, "_SESSION_FACTORY", _down_factory())
    with caplog.at_level(logging.WARNING):
        res = await w2._run(["apm.app_health"])
    assert res["degraded_reason"] == "source_unavailable"
    status = res["source_status"][0]
    assert status["status"] == "unavailable"
    assert status["reason"] == f"SourceMcpError (요청 {bound_rid})"
    assert SECRET not in str(res["source_status"]) and SECRET not in str(res[aq.META_KEY])
    assert SECRET_LOGGED in caplog.text and bound_rid in caplog.text


@pytest.mark.asyncio
async def test_gateway_down_refusal_dedupes_notices(monkeypatch) -> None:
    """연결 실패 거부도 다른 거부처럼 같은 고지를 한 번만 싣는다."""
    monkeypatch.setattr(aq, "_SESSION_FACTORY", _down_factory())
    original = aq._call_scope_step
    dup = disc.make(disc.APM_UNRESOLVED_CONDITION, "같은 고지", source="apm")

    def doubled(ctx) -> None:
        original(ctx)
        ctx.notices += [dict(dup), dict(dup)]

    monkeypatch.setattr(aq, "_call_scope_step", doubled)
    res = await w2._run(["apm.app_health"])
    assert res["degraded_reason"] == "source_unavailable"
    texts = [d["text"] for d in res.get("disclosures") or []]
    assert texts.count("같은 고지") == 1


# ── M1: 첫 홉 고지 괄호 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rank_failure_notice_has_no_nested_parentheses(monkeypatch, bound_rid) -> None:
    err = {"error": "apm_api_error", "reason": SECRET, "source_kind": "apm_api",
           "source": "jennifer", "tool": "apm_fleet"}
    gw = w2._Gateway({**w2._ALL_REPLIES, "apm_fleet": err})
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    res = await w2._run(["apm.app_health"])
    summary = res["organized_data"]["summary"]
    assert f"부하 순위를 받지 못해(apm_api_error · 요청 {bound_rid})" in summary
    assert f"(요청 {bound_rid}))" not in summary
    step = res["apm_query"]["inserted_steps"][0]
    assert step["rank_error"] == f"apm_api_error · 요청 {bound_rid}"


@pytest.mark.parametrize("why", ["순위에 오른 인스턴스 0개", "순위 행에 부를 수 있는 대상 없음",
                                 "순위 보기가 레지스트리에 없다"])
def test_rid_tail_rewrite_leaves_fixed_reasons(why) -> None:
    assert aq._RID_TAIL_RE.sub(r" · \1", why) == why


@pytest.mark.parametrize("step", [
    {"mode": "list", "instances": 3, "hosts": 2},
    {"mode": "list", "instances": 3, "hosts": 2, "listed": 9},
    {"mode": "list", "instances": 3, "hosts": 2, "read_error": "x", "total": 9},
], ids=["full_list", "partial_list", "read_error"])
def test_first_hop_text_rank_error_single_parentheses(step) -> None:
    text = _first_hop_text({**step, "rank_error": "apm_api_error · 요청 r1"})
    assert "부하 순위를 받지 못해(apm_api_error · 요청 r1)" in text



# ── S4b: 호출 단위 예외 사유(meta.failures·source_status·장부) ───────────────────

_HOST_VIEW = ViewSpec(id="apm.app_health", tool="apm_app_health", required_input="hostname")


class _FailingViewGateway(w2._Gateway):
    """보기 호출(`apm_app_health`)만 예외 원문을 싣고 실패하는 게이트웨이."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(w2._ALL_REPLIES)
        self.exc = exc

    async def call_tool(self, name: str, arguments: dict) -> Any:
        if name == "apm_app_health":
            raise self.exc
        return await super().call_tool(name, arguments)


@pytest.mark.asyncio
async def test_view_call_exception_meta_failures_keep_kind_and_request_id(
        monkeypatch, bound_rid, caplog) -> None:
    """호출 예외 사유는 「예외종류 (요청 rid)」 — meta.failures·source_status·장부·답 어디에도
    원문이 없고, 원문은 warning 로그에 남는다."""
    monkeypatch.setattr(aq, "_SESSION_FACTORY",
                        _FailingViewGateway(aq.SourceMcpError(SECRET)).factory())
    with caplog.at_level(logging.WARNING):
        res = await w2._run(["apm.app_health"], "web01")
    assert res["degraded_reason"] == "apm_calls_failed"
    meta = res[aq.META_KEY]
    assert [f["reason"] for f in meta["failures"]] == [f"SourceMcpError (요청 {bound_rid})"]
    assert res["source_status"][0]["reason"] == f"SourceMcpError (요청 {bound_rid})"
    assert SECRET not in str(res)
    # 답 문구에는 종류가 보이고 요청 번호는 한 번만 붙는다
    assert "web01: SourceMcpError" in res["final_response"]
    assert res["final_response"].count(bound_rid) == 1
    assert SECRET_LOGGED in caplog.text and bound_rid in caplog.text


@pytest.mark.asyncio
async def test_lookup_exception_reason_has_no_exception_text(bound_rid, caplog) -> None:
    """대상 해석 조회(`_lookup`) 예외도 같은 규약 — 원문은 로그 1줄."""

    class _Raising:
        async def call_tool(self, name, args):
            raise RuntimeError(SECRET)

    calls = [_Call(_HOST_VIEW, None, {}), _Call(_HOST_VIEW, None, {})]
    scope = _JobScope(sub=None, owner="o", thread_id="t", call_timeout=1.0, poll_until=0.0,
                      app_config=None)
    with caplog.at_level(logging.WARNING):
        await _lookup(_Raising(), calls, 2, scope)
    assert [c.error for c in calls] == [f"RuntimeError (요청 {bound_rid})"] * 2
    assert caplog.text.count(SECRET_LOGGED) == 1


def test_call_exception_code_phrase_stays_visible(bound_rid) -> None:
    """세션이 원문 없이 지은 사유(시간 초과)는 그대로 — 고지에 사유가 계속 보인다."""
    phrase = "APM 게이트웨이 도구 호출 시간 초과(apm_app_health, 30초)"
    assert _exception_error(aq.SourceMcpError(phrase), "apm_app_health") == phrase
    tool_error = aq.SourceMcpError(f"APM 게이트웨이 도구 오류(apm_app_health): {SECRET}")
    assert _exception_error(tool_error, "apm_app_health") == f"SourceMcpError (요청 {bound_rid})"


@pytest.mark.parametrize("kind", ["SourceMcpError", "RuntimeError"])
def test_exception_reason_shows_kind_once(kind, bound_rid) -> None:
    """새 꼴도 고지에 종류가 보이고 요청 번호가 두 번 붙지 않는다."""
    err = f"{kind} (요청 {bound_rid})"
    assert aq._shown_call_error(err) == kind
    assert aq._public_call_error(err, where="t") == err
    assert aq._call_failure_text("APM", [("web01", err)]) == (
        f"web01: {kind} (요청 {bound_rid} · 상세 사유는 서버 로그)")
    assert aq._shown_call_error(f"{kind} (요청 r1) 꼬리") is None


def test_hop_ledger_keeps_only_gateway_code_reason() -> None:
    """장부(LinkEntry) 사유는 게이트웨이 봉투 오류(instance_unresolved)뿐 — 예외 사유는
    싣지 않는다."""
    unresolved = _Call(_HOST_VIEW, "web01", {}, error="instance_unresolved: web01 불일치")
    raised = _Call(_HOST_VIEW, "web02", {}, error="SourceMcpError (요청 r1)")
    entries = [e.as_dict() for e in aq._apm_hop_ledger([unresolved, raised])]
    assert entries == [{"key": "web01", "facet": "apm_instance", "edge": "E1r",
                        "status": UNLINKED,
                        "reason": "instance_unresolved: web01 불일치"}]
