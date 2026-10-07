"""명시 소스 존 게이트 — 존 없는 소스를 이름으로 지목하면 존을 묻지 않는다 (plans/132 N-10).

ITAM 벤치 2회차(`results/itam_bench/20261007-152223`) ITAM-118:
「자산관리에서 … 서버들의 운영체제」가 그래프 진입 전 라우트 게이트에서 폴스타 존 선택
역질문(18ms)으로 끝났다. 자산관리에는 존이 없으므로 답할 수 없는 질문이다.
존 보유 시스템을 함께 지목했거나 소스 지목이 없으면 종전대로 묻는다
(102·105 「통합인증 서비스 서버들 담당 부서」는 이번 범위 밖 — 종전 동작 유지).

레지스트리는 실제 `config/db_registry.yaml`, 활성 DB는 테스트 config로 명시한다(.env 누수 방지).
LLM·DB 0.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes.query import _scope_select_or_none, _zone_clarification_or_none
from src.api.schemas import QueryRequest
from src.config import CompositeConfig, MultiDBConfig
from src.routing.db_scope import names_only_zoneless_sources

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam"]

Q118 = "자산관리에서 통합인증 업무 스토리지를 쓰는 서버들의 운영체제 알려줘"
Q102 = "통합인증 서비스 서버들 담당 부서 알려줘"

ZONELESS_ONLY = [
    Q118,
    "자산관리 시스템에서 서버들 목록",
    "ITAM에서 모든 서버 목록 보여줘",
    "제니퍼에서 서버들 응답시간 알려줘",
]
ASKS_AS_BEFORE = [
    "폴스타에서 서버들 목록",
    "서버들 목록 보여줘",
    Q102,
    "자산관리와 폴스타 서버들 비교",
]


def _config(*, scope_select: bool = True):
    return SimpleNamespace(
        composite=CompositeConfig(scope_select_enabled=scope_select),
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(_ACTIVE), zone_group_exclusive=True),
    )


class TestHelper:
    @pytest.mark.parametrize("query", ZONELESS_ONLY)
    def test_zoneless_only(self, query: str) -> None:
        assert names_only_zoneless_sources(query) is True

    @pytest.mark.parametrize("query", ASKS_AS_BEFORE)
    def test_not_zoneless_only(self, query: str) -> None:
        assert names_only_zoneless_sources(query) is False

    @pytest.mark.parametrize("query", ["", None])
    def test_empty(self, query: str | None) -> None:
        assert names_only_zoneless_sources(query) is False


class TestZoneGate:
    @pytest.mark.parametrize("query", ZONELESS_ONLY)
    def test_named_zoneless_source_skips_zone_question(self, query: str) -> None:
        assert _zone_clarification_or_none(QueryRequest(query=query), None, _config()) is None

    @pytest.mark.parametrize("query", ASKS_AS_BEFORE)
    def test_other_queries_still_ask(self, query: str) -> None:
        payload = _zone_clarification_or_none(QueryRequest(query=query), None, _config())
        assert payload is not None and payload["kind"] == "zone_select"

    def test_unregistered_zone_still_wins(self) -> None:
        """미등록 존 지목은 소스 지목과 무관하게 먼저 묻는다(plans/108 CU-B2 — 그대로)."""
        payload = _zone_clarification_or_none(
            QueryRequest(query="자산관리에서 판교존 서버들 목록"), None, _config()
        )
        assert payload is not None and "판교존" in payload["question"]


class TestScopeSelectSymmetry:
    """범위 사전 선택(`_scope_select_or_none`)도 같은 조건으로 묻지 않는다(존 게이트와 대칭)."""

    @pytest.mark.parametrize("query", ["ITAM에서 모든 서버 목록 보여줘", Q118])
    def test_named_zoneless_source_skips_scope_question(self, query: str) -> None:
        body = SimpleNamespace(query=query, selected_db_ids=None)
        assert _scope_select_or_none(body, None, _config(), None) is None

    def test_full_scan_without_source_still_asks(self) -> None:
        body = SimpleNamespace(query="모든 서버의 OS 버전을 조회해줘", selected_db_ids=None)
        payload = _scope_select_or_none(body, None, _config(), None)
        assert payload is not None and payload["kind"] == "scope_select"
