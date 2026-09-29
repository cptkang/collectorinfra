"""운영자 API(T-2)·명시 채팅 접두(T-4) 계약 (plans/126 §4.17 · W4).

실 LLM 0 · 외부 호출 0.
"""

from __future__ import annotations

import pytest

from src.doc_qa import chat_prefix


class TestChatPrefixParse:
    """접두 파싱은 **결정적**이다 — 추론이 없으므로 라우팅이 아니다."""

    @pytest.mark.parametrize("text,expected", [
        ("/문서 계정 신청 절차", "계정 신청 절차"),
        ("/doc account request", "account request"),
        ("/docs 계정 신청", "계정 신청"),
        ("/DOC 대문자도", "대문자도"),
        ("  /문서   앞뒤 공백  ", "앞뒤 공백"),
    ])
    def test_prefix_forms(self, text, expected):
        cmd = chat_prefix.parse(text)
        assert cmd is not None and cmd.query == expected
        assert cmd.collection_ids == ()

    def test_scoped_form_picks_collection(self):
        cmd = chat_prefix.parse("/문서:hq_manual 계정 신청 절차")
        assert cmd is not None
        assert cmd.collection_ids == ("hq_manual",)
        assert cmd.query == "계정 신청 절차"

    @pytest.mark.parametrize("text", [
        "계정 신청 절차",            # 접두 없음
        "문서 검색해줘",              # 슬래시 없음
        "/문서화 작업 진행",          # 다른 낱말 — 경계가 막는다
        "/docsearch 어쩌고",          # 접두 뒤 공백 없음
        "",
    ])
    def test_non_prefix_is_untouched(self, text):
        assert chat_prefix.parse(text) is None

    def test_prefix_only_gives_usage_hint(self):
        cmd = chat_prefix.parse("/문서")
        assert cmd is not None and cmd.query == ""
        hint = chat_prefix.usage_hint(["hq_manual", "arch_docs"])
        assert "/문서" in hint and "hq_manual" in hint


class TestChatPrefixGate:
    """기본 off — 기능 on + 접두 플래그 on 둘 다 필요(G-17)."""

    def _cfg(self, enabled, prefix):
        from types import SimpleNamespace
        return SimpleNamespace(rag=SimpleNamespace(enabled=enabled, chat_prefix_enabled=prefix))

    @pytest.mark.parametrize("enabled,prefix,expected", [
        (False, False, False),
        (True, False, False),      # 기능만 켜도 접두는 안 열린다
        (False, True, False),
        (True, True, True),
    ])
    def test_gate_requires_both(self, enabled, prefix, expected):
        assert chat_prefix.is_enabled(self._cfg(enabled, prefix)) is expected

    def test_missing_rag_group_is_off(self):
        from types import SimpleNamespace
        assert chat_prefix.is_enabled(SimpleNamespace()) is False


class TestRouteWiring:
    """라우팅 미선점 — 라우터·플래너·의도 집합·그래프에 문서 심볼이 없다."""

    ROUTING_FILES = (
        "src/graph.py",
        "src/state.py",
        "src/prompts/semantic_router.py",
        "src/prompts/intent_planner.py",
        "src/routing/semantic_router.py",
        "src/routing/db_authz.py",
        "src/orchestration/subagents.py",
    )

    def test_routing_files_have_no_doc_intent(self):
        """`doc_query` 의도·처리기가 라우팅 경로에 생기면 실패한다(plans/126 부록 D 보류)."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        offenders = []
        for rel in self.ROUTING_FILES:
            text = (root / rel).read_text(encoding="utf-8")
            for needle in ("doc_query", "doc_qa", "doc_retrieval"):
                if needle in text:
                    offenders.append(f"{rel}: {needle}")
        assert not offenders, (
            "라우팅 경로에 문서 질의가 선점됐다 — plans/125 소유 영역이다: " + str(offenders)
        )

    def test_chat_prefix_is_the_only_query_route_touchpoint(self):
        """질의 라우트의 문서 진입은 **접두 분기 하나**뿐이다(추론 0)."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        text = (root / "src/api/routes/query.py").read_text(encoding="utf-8")
        assert text.count("doc_chat_prefix.parse(") == 1
        assert "doc_chat_prefix.is_enabled(config)" in text


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
