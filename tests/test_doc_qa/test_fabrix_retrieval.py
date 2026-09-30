"""FabriX Retrieval 클라이언트 계약 (plans/126 §4.2 · W1).

실 LLM 0 · 외부 호출 0 — `interpret_body`는 순수 함수이고, 호출 경로는 httpx MockTransport로 돈다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.clients import fabrix_retrieval as fr
from src.infrastructure.doc_sources import DocCollection

STALE_BODY = {
    "message": "retrieval does not exist. retrieval id: 20260928181958_abc",
    "error": "data is invalid",
    "errorCode": "NoContent",
}


def _hit(**kw):
    base = {
        "id": "r1", "system_id": "default", "has_permission": True,
        "content": "계정 신청은 부서장이 승인한다.", "filename": "manual.pdf",
        "title": "본부 전산관리매뉴얼", "doc_id": "D-1", "url": "/docs/manual.pdf",
        "content_type": "CT01", "rank_score": 0.81, "rank": 1,
        "content_info": {"subtitle": "제5장 3절 계정 신청"}, "catalog_id": "C-9",
        "executed_query": "계정 신청 승인 절차는 부서장 승인이다",
    }
    base.update(kw)
    return base


def _collection(**kw) -> DocCollection:
    base = dict(
        id="hq_manual", title="본부 전산관리매뉴얼", description="규정",
        endpoint="http://kb/retrieval", token="tok1234",
        client_key="cli5678", retrieval_id="20260928181958_abc",
    )
    base.update(kw)
    return DocCollection(**base)


# ── 판정 순서 계약: 오류 봉투가 results 보다 먼저 ─────────────────────

def test_stale_id_is_classified_from_envelope():
    out = fr.interpret_body("hq_manual", STALE_BODY)
    assert out.status == fr.STATUS_STALE_ID
    assert "문서 색인이 갱신" in out.reason
    assert out.hits == []


def test_envelope_wins_even_when_results_present():
    """오류 봉투 + results 가 함께 오면 오류다 — 「0건」으로 오독하지 않는다."""
    body = dict(STALE_BODY, results=[_hit()])
    assert fr.interpret_body("hq_manual", body).status == fr.STATUS_STALE_ID


def test_other_error_code_is_generic_error():
    body = {"message": "quota exceeded", "error": "limit", "errorCode": "TooMany"}
    out = fr.interpret_body("hq_manual", body)
    assert out.status == fr.STATUS_ERROR
    assert "TooMany" in out.reason


def test_nocontent_without_message_pattern_is_downgraded():
    """`errorCode`만 맞고 메시지가 다르면 보수적으로 generic error 로 낮춘다."""
    body = {"message": "no documents indexed yet", "error": "x", "errorCode": "NoContent"}
    assert fr.interpret_body("hq_manual", body).status == fr.STATUS_ERROR


def test_success_parses_hit_fields():
    out = fr.interpret_body("hq_manual", {"results": [_hit()], "hyde_query": "가상 답변"})
    assert out.status == fr.STATUS_OK
    hit = out.hits[0]
    assert hit.doc_id == "D-1" and hit.rank_score == pytest.approx(0.81)
    assert hit.subtitle == "제5장 3절 계정 신청"
    assert hit.display_title == "본부 전산관리매뉴얼"
    assert out.executed_query.startswith("계정 신청 승인")
    assert out.hyde_query == "가상 답변"


def test_empty_results_is_empty_status():
    out = fr.interpret_body("hq_manual", {"results": []})
    assert out.status == fr.STATUS_EMPTY
    assert "0건" in out.reason


def test_permission_denied_hits_are_dropped():
    body = {"results": [_hit(has_permission=False), _hit(id="r2", doc_id="D-2")]}
    out = fr.interpret_body("hq_manual", body)
    assert [h.doc_id for h in out.hits] == ["D-2"]


def test_all_hits_denied_becomes_empty():
    out = fr.interpret_body("hq_manual", {"results": [_hit(has_permission=False)]})
    assert out.status == fr.STATUS_EMPTY


@pytest.mark.parametrize("results", [None, "oops", {"a": 1}, 3])
def test_non_list_results_is_zero_hits(results):
    out = fr.interpret_body("hq_manual", {"results": results})
    assert out.hits == [] and out.status == fr.STATUS_EMPTY


def test_non_dict_items_are_skipped_not_fatal():
    """`data:null` 계열 결함 방어 — dict 아닌 항목은 건너뛰고 나머지를 살린다."""
    body = {"results": [None, "x", _hit(doc_id="D-3")]}
    out = fr.interpret_body("hq_manual", body)
    assert [h.doc_id for h in out.hits] == ["D-3"]


def test_missing_fields_get_safe_defaults():
    out = fr.interpret_body("hq_manual", {"results": [{"content": "본문만"}]})
    hit = out.hits[0]
    assert hit.rank_score == 0.0 and hit.rank == 0
    assert hit.subtitle == "" and hit.display_title == "(제목 없음)"


def test_agmented_query_typo_is_tolerated_and_unused():
    """계약의 철자(`agmented_query`)에 의존하지 않는다 — 있어도 없어도 동작한다."""
    out = fr.interpret_body("hq_manual", {"agmented_query": "q", "results": [_hit()]})
    assert out.status == fr.STATUS_OK


def test_non_mapping_body_is_error():
    assert fr.interpret_body("hq_manual", ["not", "a", "dict"]).status == fr.STATUS_ERROR


def test_pii_block_is_its_own_status():
    body = {"status": "FILTER_INVALID", "content": "blocked by the filter"}
    out = fr.interpret_body("hq_manual", body)
    assert out.status == fr.STATUS_BLOCKED_PII


# ── 호출 경로 (MockTransport — 네트워크 0) ────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_credentials_are_sent_per_collection():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("x-openapi-token")
        seen["client"] = request.headers.get("x-generative-ai-client")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"results": [_hit()]})

    col = _collection(endpoint="http://kbgenai-chat-trrn.example/gen/kb0/xyz/1/retrieval")
    out = _run(fr.retrieve_one(col, "계정 신청 절차", client=_client(handler)))
    assert out.status == fr.STATUS_OK
    # URL 은 조립하지 않고 받은 문자열 그대로 쓴다.
    assert seen["url"] == "http://kbgenai-chat-trrn.example/gen/kb0/xyz/1/retrieval"
    assert seen["token"] == "tok1234" and seen["client"] == "cli5678"
    assert "20260928181958_abc" in seen["body"] and "계정 신청 절차" in seen["body"]


def test_stale_id_over_http_200_is_detected():
    """게이트웨이가 200 + 오류 본문으로 답해도 `stale_id`다(HTTP 상태에 의존하지 않는다)."""
    handler = lambda req: httpx.Response(200, json=STALE_BODY)  # noqa: E731
    out = _run(fr.retrieve_one(_collection(), "q", client=_client(handler)))
    assert out.status == fr.STATUS_STALE_ID


def test_stale_id_over_http_400_is_detected():
    handler = lambda req: httpx.Response(400, json=STALE_BODY)  # noqa: E731
    out = _run(fr.retrieve_one(_collection(), "q", client=_client(handler)))
    assert out.status == fr.STATUS_STALE_ID


def test_http_error_with_normal_body_is_error():
    handler = lambda req: httpx.Response(503, json={"results": [_hit()]})  # noqa: E731
    out = _run(fr.retrieve_one(_collection(), "q", client=_client(handler)))
    assert out.status == fr.STATUS_ERROR and "503" in out.reason


def test_unparseable_json_is_error():
    handler = lambda req: httpx.Response(200, content=b"<html>nope</html>")  # noqa: E731
    out = _run(fr.retrieve_one(_collection(), "q", client=_client(handler)))
    assert out.status == fr.STATUS_ERROR and "JSON" in out.reason


def test_disabled_collection_short_circuits_without_call():
    called = {"n": 0}

    def handler(request):  # pragma: no cover — 불려선 안 된다
        called["n"] += 1
        return httpx.Response(200, json={"results": []})

    col = _collection(disabled_reason="접속 정보 미입력 — RAG_HQ_MANUAL_TOKEN")
    out = _run(fr.retrieve_one(col, "q", client=_client(handler)))
    assert out.status == fr.STATUS_DISABLED and called["n"] == 0
    assert "RAG_HQ_MANUAL_TOKEN" in out.reason


def test_read_timeout_maps_to_timeout_status():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    out = _run(fr.retrieve_one(_collection(), "q", timeout_sec=1, client=_client(handler)))
    assert out.status == fr.STATUS_TIMEOUT


def test_wall_clock_total_timeout_fires_even_if_bytes_trickle():
    """읽기 상한이 무력화되는 상황(D-198)에서 총상한이 끊는다."""
    async def handler(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    out = _run(fr.retrieve_one(
        _collection(), "q", timeout_sec=30, total_timeout_sec=0.2, client=client,
    ))
    assert out.status == fr.STATUS_TIMEOUT and "총상한" in out.reason


def test_connect_error_is_error_status():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    out = _run(fr.retrieve_one(_collection(), "q", client=_client(handler)))
    assert out.status == fr.STATUS_ERROR


def test_include_raw_controls_raw_payload():
    handler = lambda req: httpx.Response(200, json={"results": [_hit()]})  # noqa: E731
    without = _run(fr.retrieve_one(_collection(), "q", client=_client(handler)))
    with_raw = _run(fr.retrieve_one(
        _collection(), "q", include_raw=True, client=_client(handler)))
    assert without.raw is None
    assert with_raw.raw is not None and "results" in with_raw.raw


def test_elapsed_ms_is_recorded():
    handler = lambda req: httpx.Response(200, json={"results": [_hit()]})  # noqa: E731
    out = _run(fr.retrieve_one(_collection(), "q", client=_client(handler)))
    assert out.elapsed_ms >= 0


def test_retrieve_many_returns_partial_results_on_one_failure():
    """컬렉션 하나가 실패해도 나머지는 살린다(개별 try/except 계약)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "bad" in str(request.url):
            return httpx.Response(200, json=STALE_BODY)
        return httpx.Response(200, json={"results": [_hit()]})

    good = _collection(id="hq_manual", endpoint="http://kb/good/retrieval")
    bad = _collection(id="arch_docs", endpoint="http://kb/bad/retrieval")

    async def go():
        async with _client(handler) as client:
            return [
                await fr.retrieve_one(good, "q", client=client),
                await fr.retrieve_one(bad, "q", client=client),
            ]

    outs = {o.collection_id: o for o in _run(go())}
    assert outs["hq_manual"].status == fr.STATUS_OK
    assert outs["arch_docs"].status == fr.STATUS_STALE_ID


def test_retrieve_many_empty_input():
    assert _run(fr.retrieve_many([], "q")) == []


def test_logs_do_not_contain_token_or_content(caplog):
    handler = lambda req: httpx.Response(200, json={"results": [_hit()]})  # noqa: E731
    with caplog.at_level("INFO"):
        _run(fr.retrieve_one(_collection(), "계정 신청 절차", client=_client(handler)))
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "tok1234" not in blob
    assert "계정 신청은 부서장이 승인한다" not in blob  # 본문은 로그에 싣지 않는다
    assert "…1234" in blob
