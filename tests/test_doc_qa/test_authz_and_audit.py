"""문서군 인가·감사 계약 (plans/126 §4.10 · W5).

실 LLM 0 · 외부 호출 0.
"""

from __future__ import annotations

import asyncio
import textwrap
from types import SimpleNamespace

import pytest

from src.clients.fabrix_retrieval import STATUS_DISABLED, STATUS_EMPTY, STATUS_OK, RetrievalOutcome
from src.doc_qa import authz, service as svc
from src.infrastructure import doc_sources as ds
from tests.test_doc_qa.test_service import _cfg, _hit, _LLM, _patch_llm, _patch_search, run  # noqa: F401

MANIFEST = """
    version: 1
    collections:
      - id: hq_manual
        title: 본부 전산관리매뉴얼
        description: 규정
        sensitive: false
      - id: arch_docs
        title: 아키텍처 설계문서
        description: 설계
        sensitive: true
"""


@pytest.fixture()
def manifest(tmp_path):
    path = tmp_path / "rag_collections.yaml"
    path.write_text(textwrap.dedent(MANIFEST), encoding="utf-8")
    ds.load_collection_meta.cache_clear()
    yield str(path)
    ds.load_collection_meta.cache_clear()


def _cols(manifest_path, cfg):
    return ds.resolve_collections(cfg.rag, path=manifest_path)


class TestAllowedCollectionIds:
    def test_admin_sees_everything(self, manifest):
        cfg = _cfg()
        got = authz.allowed_collection_ids(
            _cols(manifest, cfg), user={"role": "admin"}, rag_config=cfg.rag)
        assert set(got) == {"hq_manual", "arch_docs"}

    def test_operator_role_counts_as_admin(self, manifest):
        cfg = _cfg()
        got = authz.allowed_collection_ids(
            _cols(manifest, cfg), user={"role": "operator"}, rag_config=cfg.rag)
        assert "arch_docs" in got

    def test_plain_user_gets_only_non_sensitive(self, manifest):
        cfg = _cfg()
        got = authz.allowed_collection_ids(
            _cols(manifest, cfg), user={"username": "kim"}, rag_config=cfg.rag)
        assert got == ["hq_manual"]

    def test_named_user_may_open_sensitive(self, manifest):
        cfg = _cfg(sensitive_allowed_users="kim, lee")
        got = authz.allowed_collection_ids(
            _cols(manifest, cfg), user={"username": "kim"}, rag_config=cfg.rag)
        assert set(got) == {"hq_manual", "arch_docs"}

    def test_empty_allowlist_means_nobody(self, manifest):
        """빈 값 = 전체 허용으로 두지 않는다 — 민감 자료의 기본값은 닫힘이다(D-232 정합)."""
        cfg = _cfg(sensitive_allowed_users="")
        got = authz.allowed_collection_ids(
            _cols(manifest, cfg), user={"username": "kim"}, rag_config=cfg.rag)
        assert got == ["hq_manual"]

    def test_anonymous_is_treated_as_plain_user(self, manifest):
        cfg = _cfg()
        got = authz.allowed_collection_ids(_cols(manifest, cfg), user=None, rag_config=cfg.rag)
        assert got == ["hq_manual"]

    def test_roles_list_form_is_recognised(self, manifest):
        cfg = _cfg()
        got = authz.allowed_collection_ids(
            _cols(manifest, cfg), user={"roles": ["viewer", "admin"]}, rag_config=cfg.rag)
        assert len(got) == 2

    @pytest.mark.parametrize("raw,expected", [
        (None, ()), ("", ()), ("kim", ("kim",)), (" kim , lee ", ("kim", "lee")),
        ("kim,,lee,", ("kim", "lee")),
    ])
    def test_parse_allowed_users(self, raw, expected):
        assert authz.parse_allowed_users(raw) == expected


class TestEngineHonoursAuthorization:
    def test_sensitive_collection_is_blocked_for_plain_user(self, manifest, monkeypatch):
        calls = _patch_search(monkeypatch, [])
        cfg = _cfg()
        out = run(svc.answer_from_documents(
            "설계 근거", ["arch_docs"], llm=_LLM(), app_config=cfg,
            allowed_collection_ids=["hq_manual"], collections_path=manifest))
        assert out.status == STATUS_DISABLED and calls["n"] == 0
        assert "인가" in out.reason or "지정한 문서군" in out.reason

    def test_mixed_request_keeps_permitted_only(self, manifest, monkeypatch):
        calls = _patch_search(monkeypatch, [
            RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
        out = run(svc.answer_from_documents(
            "질문", ["hq_manual", "arch_docs"], llm=_LLM(), app_config=_cfg(),
            allowed_collection_ids=["hq_manual"], collections_path=manifest))
        assert out.status == STATUS_OK
        assert calls["n"] == 1
        assert list(out.diagnostics["counts"]) == ["hq_manual"]


class TestAudit:
    """감사는 **엔진 한 곳**에서 남긴다 — 반환 경로가 많아 진입점마다 흩으면 빠진다."""

    def _capture(self, monkeypatch):
        seen = {}

        async def fake_log(**kw):
            seen.update(kw)

        monkeypatch.setattr("src.security.audit_logger.log_doc_retrieval", fake_log)
        return seen

    def test_success_is_audited_with_scrubbed_query(self, manifest, monkeypatch):
        seen = self._capture(monkeypatch)
        _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
        run(svc.answer_from_documents(
            "010-1234-5678 로 연락되는 담당자 승인 절차", ["hq_manual"], llm=_LLM(),
            app_config=_cfg(), collections_path=manifest,
            audit_context={"user_id": "kim", "thread_id": "t1", "source": "admin_api"}))
        assert seen["status"] == STATUS_OK
        assert seen["user_id"] == "kim" and seen["thread_id"] == "t1"
        assert seen["source"] == "admin_api"
        assert seen["collection_ids"] == ["hq_manual"]
        assert seen["hit_count"] == 1
        assert "010-1234-5678" not in seen["query"]      # 스크럽 통과본만 남는다

    def test_empty_result_is_also_audited(self, manifest, monkeypatch):
        seen = self._capture(monkeypatch)
        _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_EMPTY)])
        run(svc.answer_from_documents(
            "없는 내용", ["hq_manual"], llm=_LLM(), app_config=_cfg(),
            collections_path=manifest, audit_context={"user_id": "kim"}))
        assert seen["status"] == STATUS_EMPTY and seen["hit_count"] == 0

    def test_disabled_path_is_audited(self, manifest, monkeypatch):
        """조회가 아예 안 된 경우도 남는다 — "물었다"는 사실이 감사 요건이다."""
        seen = self._capture(monkeypatch)
        _patch_search(monkeypatch, [])
        run(svc.answer_from_documents(
            "질문", ["hq_manual"], llm=_LLM(), app_config=_cfg(enabled=False),
            collections_path=manifest, audit_context={"user_id": "kim"}))
        assert seen["status"] == STATUS_DISABLED

    def test_no_context_means_no_audit(self, manifest, monkeypatch):
        """CLI 같은 개발 경로는 감사 대상이 아니다(신원이 없다)."""
        seen = self._capture(monkeypatch)
        _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
        run(svc.answer_from_documents(
            "질문", ["hq_manual"], llm=_LLM(), app_config=_cfg(), collections_path=manifest))
        assert seen == {}

    def test_audit_failure_does_not_break_the_answer(self, manifest, monkeypatch):
        async def boom(**kw):
            raise RuntimeError("audit down")

        monkeypatch.setattr("src.security.audit_logger.log_doc_retrieval", boom)
        _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit()])])
        out = run(svc.answer_from_documents(
            "질문", ["hq_manual"], llm=_LLM(), app_config=_cfg(),
            collections_path=manifest, audit_context={"user_id": "kim"}))
        assert out.status == STATUS_OK          # 감사 실패가 사용자 경로를 막지 않는다

    def test_doc_ids_are_capped(self, manifest, monkeypatch):
        seen = self._capture(monkeypatch)
        hits = [_hit(doc_id=f"D-{i}", score=0.9 - i / 100) for i in range(8)]
        _patch_search(monkeypatch, [RetrievalOutcome("hq_manual", STATUS_OK, hits=hits)])
        run(svc.answer_from_documents(
            "질문", ["hq_manual"], llm=_LLM(), app_config=_cfg(),
            collections_path=manifest, audit_context={"user_id": "kim"}))
        assert len(seen["doc_ids"]) == 5        # 상위 5건만

    def test_audit_never_carries_document_body(self, manifest, monkeypatch):
        """본문은 남기지 않는다 — 감사 파일이 문서 사본이 되어선 안 된다."""
        seen = self._capture(monkeypatch)
        secret = "대외비 본문 문장입니다"
        _patch_search(monkeypatch, [
            RetrievalOutcome("hq_manual", STATUS_OK, hits=[_hit(content=secret)])])
        run(svc.answer_from_documents(
            "질문", ["hq_manual"], llm=_LLM(), app_config=_cfg(),
            collections_path=manifest, audit_context={"user_id": "kim"}))
        assert secret not in str(seen)


class TestAuditLoggerSignature:
    def test_log_doc_retrieval_writes_event(self, monkeypatch):
        from src.security import audit_logger

        recorded = {}

        async def fake_record(event, **fields):
            recorded["event"] = event
            recorded.update(fields)

        monkeypatch.setattr(audit_logger, "_record_event", fake_record)
        asyncio.run(audit_logger.log_doc_retrieval(
            collection_ids=["hq_manual"], status="ok", hit_count=2,
            elapsed_ms=1234.56, query="계정 신청", doc_ids=["D-1"],
            source="admin_api", user_id="kim",
        ))
        assert recorded["event"] == "doc_retrieval"
        assert recorded["doc_status"] == "ok"        # status 키 충돌을 피한 이름
        assert recorded["elapsed_ms"] == 1234.6
        assert recorded["user_query"] == "계정 신청"
