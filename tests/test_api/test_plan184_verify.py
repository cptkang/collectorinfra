"""plans/184 독립 검증(verifier) — 구현자 테스트가 덮지 않은 경로 보강.

- K7: 호출자 재대입을 지운 뒤에도 /query·/query/stream **두 진입 모두** 인가한 선택이 본문에 남아
  입력 조립(`_build_turn_input_state`)과 턴 문맥(`TurnContext`)에 그대로 간다(라우트 함수 구동).
- K8: 문서 경로에 붙는 4종 노트가 양식 안내([기준월 안내])보다 앞에 오고, 각 1회만 실린다
  (매뉴얼 U-27 문구와 같은 순서 · 중복 고지 없음).

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.api.schemas import QueryRequest

q = importlib.import_module("src.api.routes.query")
og = importlib.import_module("src.nodes.output_generator")


# ─── K7: 두 텍스트 진입 모두 인가 결과가 본문에 남는다 ────────────────────────


class _StopError(Exception):
    """입력 조립 지점에서 라우트를 멈춘다(그래프 미구동)."""


class _Turn:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass


def _patch_route(monkeypatch: pytest.MonkeyPatch, seen: list[Any]) -> None:
    async def noop_async(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(q, "_audit_user_request", noop_async)
    monkeypatch.setattr(q, "TurnRecorder", _Turn)
    monkeypatch.setattr(q, "_shadow_input_guard", lambda *a, **k: None)
    monkeypatch.setattr(q, "_get_checkpoint_state", noop_async)
    monkeypatch.setattr(q, "_zone_clarification_or_none", lambda *a, **k: None)
    monkeypatch.setattr(q, "_scope_select_or_none", lambda *a, **k: None)
    monkeypatch.setattr(q, "_form_memory_delete_or_none", noop_async)
    monkeypatch.setattr(q, "_resolve_turn_approval", noop_async)

    def build(body: Any, *a: Any, **k: Any) -> dict:
        seen.append(list(body.selected_db_ids or []))
        raise _StopError

    monkeypatch.setattr(q, "_build_turn_input_state", build)


def _request() -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        graph=object(), config=SimpleNamespace(),
    )))


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["process_query", "process_query_stream"])
async def test_text_entry_passes_authorized_selection_downstream(monkeypatch, entry) -> None:
    seen: list[Any] = []
    _patch_route(monkeypatch, seen)
    body = QueryRequest(query="서버 목록", selected_db_ids=["polestar_b0", "polestar_cm_gp"])
    user = {"sub": "u1", "role": "user", "allowed_db_ids": ["polestar_cm_gp"]}
    with pytest.raises(_StopError):
        await getattr(q, entry)(_request(), body, user)
    assert seen == [["polestar_cm_gp"]]
    assert body.selected_db_ids == ["polestar_cm_gp"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["process_query", "process_query_stream"])
async def test_text_entry_denies_fully_unauthorized_selection(monkeypatch, entry) -> None:
    """전부 비인가면 입력 조립 전에 거부로 끝난다(재대입 제거가 거부 경로를 바꾸지 않는다)."""
    seen: list[Any] = []
    _patch_route(monkeypatch, seen)

    class _TurnWithResponse(_Turn):
        async def response(self, resp: Any) -> Any:
            return resp

        def stream(self, gen: Any) -> Any:
            return gen

    monkeypatch.setattr(q, "TurnRecorder", _TurnWithResponse)
    body = QueryRequest(query="서버 목록", selected_db_ids=["polestar_b0"])
    user = {"sub": "u1", "role": "user", "allowed_db_ids": ["polestar_cm_gp"]}
    await getattr(q, entry)(_request(), body, user)
    assert seen == []  # 입력 조립까지 가지 않았다
    assert not body.selected_db_ids


# ─── K8: 4종 노트는 [기준월 안내] 앞 · 각 1회 ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["xlsx", "docx"])
async def test_doc_notes_precede_form_month_notice_once(monkeypatch, output_format) -> None:
    from tests.test_nodes.test_output_generator import _make_streaming_llm
    from tests.test_nodes.test_plan184_doc_notes import _doc_state

    state = _doc_state(output_format)
    state["form_month_anchor"] = {"start": "202605", "end": "202610", "source": "default"}
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda st, fmt: {
            "file_bytes": b"stub", "file_name": f"out.{fmt}", "total_filled": 2,
            "fill_stats": None,
        },
    )
    result = await og.output_generator(
        state, llm=_make_streaming_llm("문서 요약"), app_config=MagicMock()
    )
    body = result["final_response"]
    markers = [
        "[안내] 조회 기간에 진행 중인 달",
        "[안내] 결과가 조회 상한(LIMIT 2)",
        "[안내] 조회 SQL에 생성기가 남긴 메모",
        "[안내] 실행한 SQL에서 요청 조건 'cpu >= 80'",
    ]
    anchor_at = body.index("[기준월 안내]")
    for marker in markers:
        assert body.count(marker) == 1, marker
        assert body.index(marker) < anchor_at, marker
    assert body.count("[기준월 안내]") == 1


# ─── S4 잔여: 호출 단위 실패 사유가 source_status.reason에 원문으로 남는다 ──────────


@pytest.mark.asyncio
async def test_call_failure_source_status_reason_hides_exception() -> None:
    import structlog

    from src.orchestration import apm_query as aq
    from tests.test_orchestration import test_plan134_w2_verify as w2

    secret = "postgresql://svc:pw-9x@10.1.2.3/pdb"

    class _FailingGateway(w2._Gateway):
        async def call_tool(self, name: str, arguments: dict) -> Any:
            if name == "apm_app_health":
                raise aq.SourceMcpError(secret)
            return await super().call_tool(name, arguments)

    mp = pytest.MonkeyPatch()
    mp.setattr(aq, "_SESSION_FACTORY", _FailingGateway(w2._ALL_REPLIES).factory())
    structlog.contextvars.bind_contextvars(request_id="rid184")
    try:
        res = await w2._run(["apm.app_health"], "web01")
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
        mp.undo()
    assert res["degraded_reason"] == "apm_calls_failed"
    assert secret not in str(res.get("final_response"))  # 화면 문구는 이미 안전
    assert secret not in str(res["source_status"])  # ← 현재 원문이 남는다
