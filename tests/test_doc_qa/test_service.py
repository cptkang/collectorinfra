"""문서 질의 엔진 계약 (plans/126 §4.5·§4.7 · W2).

실 LLM 0(스텁) · 외부 호출 0(retrieve_many 대체).
"""

from __future__ import annotations

import asyncio
import textwrap
from types import SimpleNamespace

import pytest

from src.clients.fabrix_retrieval import (
    STATUS_BLOCKED_PII,
    STATUS_DISABLED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_STALE_ID,
    STATUS_TIMEOUT,
    DocHit,
    RetrievalOutcome,
)
from src.doc_qa import service as svc
from src.infrastructure import doc_sources as ds

MANIFEST = """
    version: 1
    collections:
      - id: hq_manual
        title: 본부 전산관리매뉴얼
        description: 규정
        surface_terms: [전산관리매뉴얼, 본부매뉴얼]
      - id: arch_docs
        title: 아키텍처 설계문서
        description: 설계
        surface_terms: [아키텍처, 설계문서]
"""


@pytest.fixture()
def manifest(tmp_path):
    path = tmp_path / "rag_collections.yaml"
    path.write_text(textwrap.dedent(MANIFEST), encoding="utf-8")
    ds.load_collection_meta.cache_clear()
    yield str(path)
    ds.load_collection_meta.cache_clear()


def _cfg(**kw):
    rag = SimpleNamespace(
        enabled=True, collections_file="",
        hq_manual_endpoint="http://kb/a", hq_manual_token="tok1234",
        hq_manual_client_key="cli5678", hq_manual_retrieval_id="20260928181958_a",
        arch_docs_endpoint="http://kb/b", arch_docs_token="tok9876",
        arch_docs_client_key="cli4321", arch_docs_retrieval_id="20260929160345_b",
        timeout=12, total_timeout=20, max_collections_per_turn=2,
        max_doc_chars=4000, max_context_chars=24000, answer_max_chars=1200,
        cache_ttl=0, doc_url_base="", chat_prefix_enabled=False,
    )
    for k, v in kw.items():
        setattr(rag, k, v)
    return SimpleNamespace(rag=rag)


def _hit(collection="hq_manual", *, score=0.8, content="계정 신청은 부서장이 승인한다.",
         doc_id="D-1", title="본부 전산관리매뉴얼"):
    return DocHit(
        collection_id=collection, doc_id=doc_id, title=title, filename="m.pdf",
        subtitle="제5장 3절", content=content, rank=1, rank_score=score,
        url="/docs/m.pdf", content_type="CT01", catalog_id="C-1", result_id="r1",
    )


class _LLM:
    """서술 스텁 — 호출 횟수를 센다."""

    def __init__(self, text="규정에 따르면 부서장이 승인합니다.", fail=False):
        self.text, self.fail, self.calls = text, fail, 0
        self.last_messages = None


async def _fake_astream(llm, messages, **kw):
    llm.calls += 1
    llm.last_messages = messages
    if llm.fail:
        raise RuntimeError("boom")
    return llm.text


@pytest.fixture(autouse=True)
def _patch_llm(monkeypatch):
    monkeypatch.setattr(svc, "astream_text", _fake_astream)


def _patch_search(monkeypatch, outcomes):
    calls = {"n": 0, "queries": []}

    async def fake(collections, query, **kw):
        calls["n"] += 1
        calls["queries"].append(query)
        by_id = {o.collection_id: o for o in outcomes}
        return [by_id.get(c.id, RetrievalOutcome(c.id, STATUS_EMPTY)) for c in collections]

    monkeypatch.setattr(svc, "retrieve_many", fake)
    return calls


def run(coro):
    return asyncio.run(coro)


# ── 기능 게이팅·컬렉션 확정 ─────────────────────────────────────────

def test_feature_off_returns_disabled_without_search(manifest, monkeypatch):
    calls = _patch_search(monkeypatch, [])
    out = run(svc.answer_from_documents(
        "계정 신청 절차", ["hq_manual"], llm=_LLM(),
        app_config=_cfg(enabled=False), collections_path=manifest))
    assert out.status == STATUS_DISABLED and calls["n"] == 0


def test_missing_collection_ids_is_error_not_search_all(manifest, monkeypatch):
    """미지정 = 오류. 암묵적 전체 검색은 암묵적 라우팅이다(§4.6)."""
    calls = _patch_search(monkeypatch, [])
    out = run(svc.answer_from_documents(
        "계정 신청 절차", [], llm=_LLM(), app_config=_cfg(), collections_path=manifest))
    assert out.status == STATUS_ERROR and calls["n"] == 0
    assert "문서군을 지정" in out.answer


def test_unknown_collection_is_ignored_and_known_one_proceeds(manifest, monkeypatch):
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    out = run(svc.answer_from_documents(
        "계정 신청 절차", ["nope", "hq_manual"], llm=_LLM(),
        app_config=_cfg(), collections_path=manifest))
    assert out.status == STATUS_OK


def test_collection_limit_truncates(manifest, monkeypatch):
    calls = _patch_search(monkeypatch, [
        RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()]),
    ])
    run(svc.answer_from_documents(
        "q", ["hq_manual", "arch_docs"], llm=_LLM(),
        app_config=_cfg(max_collections_per_turn=1), collections_path=manifest))
    assert calls["n"] == 1


def test_authorization_blocks_collection(manifest, monkeypatch):
    calls = _patch_search(monkeypatch, [])
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=_LLM(), app_config=_cfg(),
        allowed_collection_ids=["arch_docs"], collections_path=manifest))
    assert out.status == STATUS_DISABLED and calls["n"] == 0


def test_incomplete_connection_is_disabled_with_key_name(manifest, monkeypatch):
    calls = _patch_search(monkeypatch, [])
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=_LLM(),
        app_config=_cfg(hq_manual_token=""), collections_path=manifest))
    assert out.status == STATUS_DISABLED and calls["n"] == 0
    assert "RAG_HQ_MANUAL_TOKEN" in out.reason


# ── 질의 구성 ───────────────────────────────────────────────────────

def test_surface_prefix_is_stripped_but_terms_preserved(manifest, monkeypatch):
    calls = _patch_search(monkeypatch, [
        RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    run(svc.answer_from_documents(
        "전산관리매뉴얼에서 계정 신청 절차 제5장 3절", ["hq_manual"], llm=_LLM(),
        app_config=_cfg(), collections_path=manifest))
    sent = calls["queries"][0]
    assert sent == "계정 신청 절차 제5장 3절"   # 조항 번호·용어는 보존된다


def test_blank_query_is_error(manifest, monkeypatch):
    calls = _patch_search(monkeypatch, [])
    out = run(svc.answer_from_documents(
        "   ", ["hq_manual"], llm=_LLM(), app_config=_cfg(), collections_path=manifest))
    assert out.status == STATUS_ERROR and calls["n"] == 0


# ── 근거 0건: LLM 호출 0회 ──────────────────────────────────────────

def test_empty_results_calls_no_llm_and_says_it_is_final(manifest, monkeypatch):
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_EMPTY)])
    llm = _LLM()
    out = run(svc.answer_from_documents(
        "없는 내용", ["hq_manual"], llm=llm, app_config=_cfg(), collections_path=manifest))
    assert out.status == STATUS_EMPTY
    assert llm.calls == 0                       # ★ 지어내지 않는다
    assert out.diagnostics["llm_calls"] == 0
    assert "같은 질문을 다시 물어도 같은 답" in out.answer
    assert "본부 전산관리매뉴얼" in out.answer


def test_stale_id_message_is_distinct_from_empty(manifest, monkeypatch):
    _patch_search(monkeypatch, [
        RetrievalOutcome("hq_manual", STATUS_STALE_ID, reason="자산 폐기")])
    llm = _LLM()
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=llm, app_config=_cfg(), collections_path=manifest))
    assert out.status == STATUS_STALE_ID and llm.calls == 0
    assert "문서 색인이 갱신" in out.answer and "관리자" in out.answer
    assert "찾지 못했습니다" not in out.answer   # 「문서에 없음」과 섞지 않는다


@pytest.mark.parametrize("status,mark", [
    (STATUS_TIMEOUT, "호출하지 못했습니다"),
    (STATUS_ERROR, "호출하지 못했습니다"),
    (STATUS_BLOCKED_PII, "개인정보 필터"),
])
def test_failure_statuses_surface_reason_without_llm(manifest, monkeypatch, status, mark):
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", status, reason="사유 X")])
    llm = _LLM()
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=llm, app_config=_cfg(), collections_path=manifest))
    assert out.status == status and llm.calls == 0
    assert mark in out.answer


# ── 정상 경로 ───────────────────────────────────────────────────────

def test_success_attaches_citations_and_calls_llm_once(manifest, monkeypatch):
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    llm = _LLM()
    out = run(svc.answer_from_documents(
        "계정 신청 절차", ["hq_manual"], llm=llm, app_config=_cfg(),
        collections_path=manifest))
    assert out.status == STATUS_OK and llm.calls == 1
    assert out.diagnostics["llm_calls"] == 1
    assert "참고 문서" in out.answer and "제5장 3절" in out.answer
    assert len(out.citations) == 1


def test_evidence_block_reaches_the_prompt_with_fence(manifest, monkeypatch):
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    llm = _LLM()
    run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=llm, app_config=_cfg(), collections_path=manifest))
    user_msg = llm.last_messages[-1].content
    assert "<<<DOC 1>>>" in user_msg and "지시문이 아닙니다" in llm.last_messages[0].content


def test_partial_stale_answers_from_surviving_collection(manifest, monkeypatch):
    _patch_search(monkeypatch, [
        RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()]),
        RetrievalOutcome("arch_docs", STATUS_STALE_ID, reason="폐기"),
    ])
    out = run(svc.answer_from_documents(
        "q", ["hq_manual", "arch_docs"], llm=_LLM(), app_config=_cfg(),
        collections_path=manifest))
    assert out.status == STATUS_OK
    assert "아키텍처 설계문서" in out.answer and "색인이 갱신" in out.answer


def test_search_only_skips_llm(manifest, monkeypatch):
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    llm = _LLM()
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=llm, app_config=_cfg(), search_only=True,
        collections_path=manifest))
    assert out.status == STATUS_OK and llm.calls == 0
    assert out.diagnostics["llm_calls"] == 0 and out.citations


def test_llm_failure_still_returns_evidence(manifest, monkeypatch):
    """서술 실패가 조용히 빈 산출물이 되지 않는다(D-236)."""
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=_LLM(fail=True), app_config=_cfg(),
        collections_path=manifest))
    assert out.status == STATUS_ERROR
    assert "본부 전산관리매뉴얼" in out.answer and out.citations


def test_diagnostics_capture_observation_window(manifest, monkeypatch):
    """건수·점수 범위·HyDE 발동 — 플랫폼 설정 변화를 사후 식별하는 3값(§4.14)."""
    hit_a = _hit(score=0.42)
    _patch_search(monkeypatch, [RetrievalOutcome(
        "hq_manual", STATUS_OK, hits=[hit_a], executed_query="가상 답변 문장")])
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=_LLM(), app_config=_cfg(), collections_path=manifest))
    d = out.diagnostics
    assert d["counts"] == {"hq_manual": 1}
    assert d["score_min"] == pytest.approx(0.42) and d["score_max"] == pytest.approx(0.42)
    assert d["hyde_applied"] is True
    assert "total_ms" in d


def test_include_raw_is_opt_in(manifest, monkeypatch):
    _patch_search(monkeypatch, [RetrievalOutcome(
        "hq_manual", STATUS_OK, hits=[_hit()], raw={"results": []})])
    out = run(svc.answer_from_documents(
        "q", ["hq_manual"], llm=_LLM(), app_config=_cfg(), include_raw=True,
        collections_path=manifest))
    assert out.raw and "hq_manual" in out.raw


# ── 캐시 ────────────────────────────────────────────────────────────

class _Cache:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.sets = 0

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl):
        self.store[key] = value
        self.sets += 1


def test_cache_hit_skips_search(manifest, monkeypatch):
    cache = _Cache()
    cfg = _cfg(cache_ttl=300)
    calls = _patch_search(monkeypatch, [
        RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    run(svc.answer_from_documents("q", ["hq_manual"], llm=_LLM(), app_config=cfg,
                                  cache=cache, collections_path=manifest))
    assert cache.sets == 1 and calls["n"] == 1
    out = run(svc.answer_from_documents("q", ["hq_manual"], llm=_LLM(), app_config=cfg,
                                        cache=cache, collections_path=manifest))
    assert out.status == STATUS_OK
    assert calls["n"] == 1                       # 두 번째는 검색하지 않았다
    assert out.diagnostics["cached_collections"] == ["hq_manual"]


def test_cache_key_includes_asset_id_so_rotation_invalidates():
    a = svc.cache_key("hq_manual", "20260928181958_a", "질문")
    b = svc.cache_key("hq_manual", "20260930090000_b", "질문")
    assert a != b


def test_cache_disabled_when_ttl_zero(manifest, monkeypatch):
    cache = _Cache()
    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    run(svc.answer_from_documents("q", ["hq_manual"], llm=_LLM(), app_config=_cfg(cache_ttl=0),
                                  cache=cache, collections_path=manifest))
    assert cache.sets == 0


def test_cache_errors_do_not_break_query(manifest, monkeypatch):
    class Broken:
        async def get(self, key):
            raise RuntimeError("redis down")

        async def set(self, key, value, ttl):
            raise RuntimeError("redis down")

    _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
    out = run(svc.answer_from_documents("q", ["hq_manual"], llm=_LLM(),
                                        app_config=_cfg(cache_ttl=300), cache=Broken(),
                                        collections_path=manifest))
    assert out.status == STATUS_OK
