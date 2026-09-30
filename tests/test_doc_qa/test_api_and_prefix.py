"""운영자 API(T-2) 계약 · 라우팅 경계 (plans/126 §4.17 · W4 → plans/127 W2·W5 개정).

실 LLM 0 · 외부 호출 0.

**T-4 명시 채팅 접두(`/문서`)는 2026-09-30 제거했다**(plans/127 W5 · G-11 — 사용자가 라우팅 off
상태 제거를 선택). 그 파싱·게이트 테스트도 함께 뺐다. 라우팅 미선점 가드(D-284 ①의 기계 검증)는
plans/127 게이트 확정(D-286)으로 **개정**했다 — 2단 파일은 조건부 처리기 `doc_query`를 싣고,
3단 파일과 상태 스키마는 여전히 문서 심볼 0건이다(3단은 103 동등성 항목 · 상태 필드 0이 계약).
"""

from __future__ import annotations

import pytest


class TestRouteWiring:
    """라우팅 경계(plans/127 §4.13 ②) — 3단·상태에는 문서 의도가 없고, 질의 라우트에 접두 진입이
    없다."""

    #: 3단 라우터·그래프·상태 스키마(+ 3단 DB 인가 통과 의도 목록) — 문서 심볼 0건이 계약이다.
    TIER3_AND_STATE_FILES = (
        "src/graph.py",
        "src/state.py",
        "src/prompts/semantic_router.py",
        "src/routing/semantic_router.py",
        "src/routing/db_authz.py",
    )

    def test_tier3_and_state_have_no_doc_intent(self):
        """3단 의도·그래프 노드·`AgentState` 필드로 문서가 들어오면 실패한다(G-9 (a) · §4.9)."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        offenders = []
        for rel in self.TIER3_AND_STATE_FILES:
            text = (root / rel).read_text(encoding="utf-8")
            for needle in ("doc_query", "doc_qa", "doc_retrieval"):
                if needle in text:
                    offenders.append(f"{rel}: {needle}")
        assert not offenders, (
            "3단·상태에 문서 의도가 생겼다 — 3단은 plans/103 동등성 항목이다: " + str(offenders)
        )

    def test_query_route_has_no_doc_prefix_entry(self):
        """T-4 접두 진입은 제거됐다 — 질의 라우트의 문서 진입은 0건(채팅 경로는 2단 처리기뿐)."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        text = (root / "src/api/routes/query.py").read_text(encoding="utf-8")
        assert "chat_prefix" not in text and "/문서" not in text
        assert not (root / "src/doc_qa/chat_prefix.py").exists()


class TestApiContract:
    """운영자 API 스키마·인가 계약(라우트 구현은 서버 기동 없이 스키마로 고정)."""

    def test_collection_ids_is_required(self):
        from pydantic import ValidationError
        from src.api.routes.doc_search import DocSearchRequest
        with pytest.raises(ValidationError):
            DocSearchRequest(query="질문")            # collection_ids 없음
        with pytest.raises(ValidationError):
            DocSearchRequest(collection_ids=[], query="질문")   # 빈 목록도 거부

    def test_defaults_are_conservative(self):
        from src.api.routes.doc_search import DocSearchRequest
        req = DocSearchRequest(collection_ids=["hq_manual"], query="질문")
        assert req.include_raw is False and req.search_only is False

    def test_routes_require_admin(self):
        """두 라우트 모두 관리자 의존성을 건다(R-18 — 시험 표면을 일반 사용자에게 열지 않는다)."""
        import inspect
        from src.api.routes import doc_search
        for fn in (doc_search.list_collections, doc_search.search_documents):
            params = inspect.signature(fn).parameters
            deps = [p for p in params.values() if getattr(p.default, "dependency", None)]
            assert deps, f"{fn.__name__}: 인가 의존성 없음"
            assert any(
                getattr(d.default.dependency, "__name__", "") == "require_admin_user"
                for d in deps
            ), f"{fn.__name__}: require_admin_user 아님"

    def test_no_connection_write_endpoint(self):
        """접속 저장 엔드포인트를 만들지 않는다 — 「환경변수 설정」 탭이 이미 한다(정본 이중화 금지)."""
        from src.api.routes import doc_search
        paths = [r.path for r in doc_search.router.routes]
        assert sorted(paths) == ["/doc/collections", "/doc/search"]
