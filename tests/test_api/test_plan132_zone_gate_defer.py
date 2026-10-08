"""2단 다중 시스템 배포의 라우트 존 게이트 위임 (plans/132 v1.5 후속 ①).

폐쇄망 ITAM-102·105 「통합인증 서비스 서버들 담당 부서」(소스 지목 없음)가 그래프 진입 전 라우트
존 게이트에서 폴스타 존을 되물었다(ITAM 질문에 오답 되물음). 2단(`intent_orchestration`)이고 활성
시스템(활성 DB 소유 시스템 ∪ 활성 비DB 시스템)이 둘 이상이면 라우트는 묻지 않고, 분류 뒤 task 단위
게이트(`_zone_clarification_or_none_task`)가 대상이 전부 폴스타 존일 때만 묻는다. 범위 사전 선택도
같은 조건에서 묻지 않는다(대칭). 활성 시스템이 하나뿐이거나 2단이 아니면 종전과 같다.

래더는 모듈 전역(`src.observability.ladder._resolution`)이라 테스트마다 명시 주입한다 — 다른
테스트의 build_graph 확정이 새지 않게 한다. 활성 DB·APM 엔드포인트도 테스트 config로 명시한다
(.env 누수 방지). 레지스트리는 실제 `config/db_registry.yaml`. LLM·DB 0.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.observability.ladder as ladder
from src.api.routes.query import (
    _build_turn_input_state,
    _scope_select_or_none,
    _zone_clarification_or_none,
)
from src.api.schemas import QueryRequest
from src.config import CompositeConfig, MultiDBConfig
from src.utils.query_gen_common import ZONE_GROUP_EXCLUSIVE_QUESTION

_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
Q102 = "통합인증 서비스 서버들 담당 부서 알려줘"
Q_OS = "서버들의 OS 목록 보여줘"


def _config(active: list[str], *, apm: bool = False) -> SimpleNamespace:
    endpoints = {"apm": "http://127.0.0.1:9096/mcp"} if apm else {}
    return SimpleNamespace(
        composite=CompositeConfig(scope_select_enabled=True),
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(active), zone_group_exclusive=True),
        dbhub=SimpleNamespace(
            source_endpoint=lambda code: (endpoints[code], None) if code in endpoints else None
        ),
    )


MULTI_SYSTEM = {
    "polestar+itam": lambda: _config([*_ZONES, "itam"]),
    "polestar+apm": lambda: _config(_ZONES, apm=True),
}


@pytest.fixture
def set_tier(monkeypatch):
    """래더 확정을 테스트 안에서만 주입한다(monkeypatch가 원복)."""

    def _set(tier: str | None) -> None:
        value = None if tier is None else {
            "tier": tier, "degraded_reason": "", "resolved_by": "explicit_env",
        }
        monkeypatch.setattr(ladder, "_resolution", value)

    return _set


def _zone(query: str, cfg, **kw):
    return _zone_clarification_or_none(QueryRequest(query=query, **kw), None, cfg)


def _scope(query: str, cfg):
    body = SimpleNamespace(query=query, selected_db_ids=None)
    return _scope_select_or_none(body, None, cfg, None)


class TestDeferredInTier2MultiSystem:
    """(a) 2단 + 활성 시스템 둘 이상 → 라우트 존 게이트·범위 선택 모두 묻지 않는다."""

    @pytest.mark.parametrize("name", list(MULTI_SYSTEM))
    @pytest.mark.parametrize("query", [Q102, Q_OS, "모든 서버의 OS 버전을 조회해줘"])
    def test_route_gates_skip(self, set_tier, name: str, query: str) -> None:
        set_tier("intent_orchestration")
        cfg = MULTI_SYSTEM[name]()
        assert _zone(query, cfg) is None
        assert _scope(query, cfg) is None

    def test_skip_logged(self, set_tier, caplog) -> None:
        set_tier("intent_orchestration")
        with caplog.at_level("INFO", logger="src.api.routes.query"):
            assert _zone(Q102, MULTI_SYSTEM["polestar+itam"]()) is None
        assert any("task 단위 존 게이트에 위임" in r.getMessage() for r in caplog.records)


class TestUnchangedOtherwise:
    """(b) 단일 시스템 · (c) 2단 아님 → 종전 페이로드 그대로."""

    @pytest.mark.parametrize("query", [Q102, Q_OS])
    def test_tier2_single_system_identical(self, set_tier, query: str) -> None:
        """운영 폴스타 3존만(APM 없음)은 2단이어도 비트 동일 — 3존은 한 시스템이다."""
        cfg = _config(_ZONES)
        set_tier(None)
        before = (_zone(query, cfg), _scope("모든 서버의 OS 버전을 조회해줘", cfg))
        set_tier("intent_orchestration")
        after = (_zone(query, cfg), _scope("모든 서버의 OS 버전을 조회해줘", cfg))
        assert before[0] is not None and before[0]["kind"] == "zone_select"
        assert before[1] is not None and before[1]["kind"] == "scope_select"
        assert after == before

    @pytest.mark.parametrize("tier", [None, "semantic_router", "legacy", "deep_agent"])
    @pytest.mark.parametrize("name", list(MULTI_SYSTEM))
    def test_not_tier2_multi_system_still_asks(self, set_tier, tier, name: str) -> None:
        set_tier(tier)
        cfg = MULTI_SYSTEM[name]()
        payload = _zone(Q102, cfg)
        assert payload is not None and payload["kind"] == "zone_select"
        scope = _scope("모든 서버의 OS 버전을 조회해줘", cfg)
        assert scope is not None and scope["kind"] == "scope_select"


class TestPriorGatesStillFire:
    """(d) 상호배타·미등록 존·자리표시자는 2단 다중 시스템에서도 종전대로 묻는다."""

    @pytest.fixture(autouse=True)
    def _tier2(self, set_tier) -> None:
        set_tier("intent_orchestration")

    @pytest.mark.parametrize("name", list(MULTI_SYSTEM))
    def test_zone_group_exclusive(self, name: str) -> None:
        payload = _zone("은행존과 공동존 김포의 서버들에 대해 OS 조회", MULTI_SYSTEM[name]())
        assert payload is not None and payload["question"] == ZONE_GROUP_EXCLUSIVE_QUESTION

    @pytest.mark.parametrize("name", list(MULTI_SYSTEM))
    def test_unregistered_zone(self, name: str) -> None:
        payload = _zone("판교존 서버들 목록", MULTI_SYSTEM[name]())
        assert payload is not None and "판교존" in payload["question"]

    @pytest.mark.parametrize("name", list(MULTI_SYSTEM))
    def test_placeholder(self, name: str) -> None:
        payload = _zone("ㅇㅇ존 서버 OS 확인", MULTI_SYSTEM[name]())
        assert payload is not None and payload["kind"] == "zone_select"

    def test_resume_turn_passes(self) -> None:
        cfg = MULTI_SYSTEM["polestar+itam"]()
        assert _zone(Q_OS, cfg, selected_db_ids=["polestar_cm_gp"]) is None


class TestTaskGateReceives:
    """(e) 위임받는 task 게이트 — 텍스트 라우트가 허용 표지를 싣고, 분류 결과로 판정한다.

    분류 LLM(가짜)을 거친 종단 판정은 `tests/test_orchestration/test_plan119_zone_early.py`
    `TestBoundary::test_zoneless_active_db_keeps_post_gate`가 고정한다(폴스타 분류 → 묻는다 ·
    itam 분류 → 안 묻는다). 여기서는 같은 질문 형태로 게이트 함수만 직접 본다.
    """

    def test_text_route_allows_task_gate(self) -> None:
        state = _build_turn_input_state(QueryRequest(query=Q102), "t-1", None, {"sub": "u"})
        assert state["zone_clarification_allowed"] is True

    @pytest.mark.parametrize(("targets", "asks"), [
        (_ZONES, True), (["polestar_cm_gp"], True),
        (["itam"], False), (["polestar_b0", "itam"], False),
    ])
    def test_task_gate_by_classification(self, targets: list[str], asks: bool) -> None:
        from src.orchestration.subagents import _zone_clarification_or_none_task

        active = [*_ZONES, "itam"]
        cfg = SimpleNamespace(
            multi_db=SimpleNamespace(
                get_active_db_ids=lambda: list(active), zone_group_exclusive=True,
            ),
            get_polestar_db_ids=lambda: {*_ZONES, "polestar"},
        )
        isolated = {
            "zone_clarification_allowed": True,
            "conversation_context": None,
            "original_user_query": Q_OS,
            "user_query": Q_OS,
            "parsed_requirements": {"query_targets": ["os_type"], "filter_conditions": []},
            "allowed_db_ids": None,
            "user_role": None,
        }
        payload = _zone_clarification_or_none_task(
            {"task_id": "t1", "agent": "data_query", "sub_query": Q_OS}, isolated,
            [{"db_id": d, "relevance_score": 0.9} for d in targets],
            db_pinned=False, db_succeeded=False, app_config=cfg,
        )
        assert (payload is not None and payload["kind"] == "zone_select") is asks


class TestSystemCount:
    """시스템 셈 — 분류 경로(`_multiple_systems_active`)와 같은 DB 재료 + 서버 엔터티 비DB만.

    교정(verifier Minor-1·2): 간선 없는 문서 검색(doc)은 세지 않고, 선언 없는·미등록 db_id도
    세지 않는다(`registry.systems_of` — None 제외).
    """

    @pytest.fixture(autouse=True)
    def _tier2(self, set_tier) -> None:
        set_tier("intent_orchestration")

    @pytest.fixture
    def doc_on(self, monkeypatch) -> None:
        monkeypatch.setattr("src.orchestration.doc_query.doc_active", lambda _cfg: True)

    def test_doc_alone_does_not_defer(self, set_tier, doc_on) -> None:
        """폴스타 3존 + 문서 검색 on → 위임 안 함(종전 비트 동일)."""
        from src.orchestration.conditional_agents import active_conditional_systems

        cfg = _config(_ZONES)
        assert "doc" in active_conditional_systems(cfg)  # 문서 시스템은 실제로 활성
        after = (_zone(Q102, cfg), _scope("모든 서버의 OS 버전을 조회해줘", cfg))
        set_tier(None)
        before = (_zone(Q102, cfg), _scope("모든 서버의 OS 버전을 조회해줘", cfg))
        assert before[0] is not None and after == before

    def test_doc_with_apm_defers(self, doc_on) -> None:
        assert _zone(Q102, _config(_ZONES, apm=True)) is None

    @pytest.mark.parametrize("extra", ["bogus_db", "itsm"])
    def test_undeclared_or_unregistered_db_not_counted(self, extra: str) -> None:
        """미등록 db_id·시스템 선언 없는 DB가 섞여도 단일 시스템이면 위임 안 함."""
        payload = _zone(Q102, _config([*_ZONES, extra]))
        assert payload is not None and payload["kind"] == "zone_select"

    @pytest.mark.parametrize(("active", "apm", "expected"), [
        ([*_ZONES, "itam"], False, True),
        ([*_ZONES, "bogus_db"], False, False),
        ([*_ZONES, "itsm"], False, False),
        (_ZONES, True, False),  # 분류 경로는 비DB를 세지 않는다(§12.6 ①)
    ])
    def test_classification_path_shares_db_count(self, active, apm, expected) -> None:
        from src.orchestration.subagents import _multiple_systems_active

        assert _multiple_systems_active(_config(active, apm=apm)) is expected
