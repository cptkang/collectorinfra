"""존 사전 게이트 — 제니퍼 데이터 서버 질문의 라우트 통과 고정 (plans/157 결함 ① · G-1 (a)).

벤치 E1 `APM-JSERVER-domains#0` 「데이터 서버별 도메인 배치랑 설정값 알려줘」가 그래프 전에 존을
되물었다 — 「서버별」(전량 조회 표현)·「서버」가 있고 위치어·소스 단어가 없어서다. 폐쇄망 기준 구성
(2단 · 폴스타 존 DB 활성 · 제니퍼 활성)에서는 활성 시스템이 둘이라 라우트가 task 게이트에 위임하고
(`_zone_gate_deferred_to_tasks`), APM task는 존 대상이 아니라 되묻지 않는다. 결함은 활성 DB 0건인
벤치 환경의 부정합이었다 — 제품 코드는 그대로 두고 E1이 활성 DB를 주입한다(`driver.E1_ENV`).

래더는 모듈 전역이라 테스트마다 명시 주입한다. 활성 DB·APM 엔드포인트도 테스트 config로 명시한다
(.env 누수 방지 · 픽스처는 `test_plan132_zone_gate_defer.py`와 같은 형태). LLM·DB 0.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.observability.ladder as ladder
from src.api.routes.query import _zone_clarification_or_none
from src.api.schemas import QueryRequest
from src.config import CompositeConfig, MultiDBConfig

Q_JSERVER = "데이터 서버별 도메인 배치랑 설정값 알려줘"
_ZONE_DBS = ["polestar_cm_gp", "polestar_cm_yd"]  # 벤치 E1 주입값과 같은 공동존 두 DB
_ALL_ZONE_DBS = ["polestar_b0", *_ZONE_DBS]


def _config(active: list[str], *, apm: bool = True) -> SimpleNamespace:
    endpoints = {"apm": "http://127.0.0.1:9096/mcp"} if apm else {}
    return SimpleNamespace(
        composite=CompositeConfig(scope_select_enabled=True),
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(active), zone_group_exclusive=True),
        dbhub=SimpleNamespace(
            source_endpoint=lambda code: (endpoints[code], None) if code in endpoints else None
        ),
    )


@pytest.fixture
def set_tier(monkeypatch):
    """래더 확정을 테스트 안에서만 주입한다(monkeypatch가 원복)."""

    def _set(tier: str | None) -> None:
        value = None if tier is None else {
            "tier": tier, "degraded_reason": "", "resolved_by": "explicit_env",
        }
        monkeypatch.setattr(ladder, "_resolution", value)

    return _set


def _zone(query: str, cfg):
    return _zone_clarification_or_none(QueryRequest(query=query), None, cfg)


def test_question_reaches_zone_gate_conditions() -> None:
    """전제 — 이 문장은 위치어·소스 단어 없이 전량 조회 조건을 채운다(게이트 판정 대상)."""
    from src.routing.db_scope import names_only_zoneless_sources
    from src.utils.query_gen_common import LOCATION_HINT_TERMS, is_full_scan_query, term_in_text

    assert is_full_scan_query(Q_JSERVER) and "서버" in Q_JSERVER
    assert not any(term_in_text(t, Q_JSERVER) for t in LOCATION_HINT_TERMS)
    assert not names_only_zoneless_sources(Q_JSERVER)


@pytest.mark.parametrize("active", [_ZONE_DBS, ["polestar_cm_gp"], _ALL_ZONE_DBS])
def test_tier2_zone_db_plus_apm_passes(set_tier, active: list[str]) -> None:
    """B-1 — 2단 + 폴스타 존 DB 활성 + 제니퍼 활성 → 라우트는 묻지 않는다(task 게이트 위임)."""
    set_tier("intent_orchestration")
    assert _zone(Q_JSERVER, _config(active)) is None


def test_apm_only_still_asks(set_tier) -> None:
    """B-2 — 활성 DB 0건(제니퍼만)이면 현행대로 존을 되묻는다(의도 동작 고정).

    존 없는 시스템만 활성이면 시스템이 하나라 위임이 서지 않는다. 존 질문이 틀린 질문이라서가 아니라
    로컬 구성의 한계다 — 폐쇄망 기준 구성은 폴스타 존 DB가 함께 활성이다(위 B-1).
    """
    set_tier("intent_orchestration")
    payload = _zone(Q_JSERVER, _config([]))
    assert payload is not None and payload["kind"] == "zone_select"


@pytest.mark.parametrize("tier", [None, "semantic_router", "legacy", "deep_agent"])
def test_not_tier2_still_asks(set_tier, tier: str | None) -> None:
    """2단이 아니면 활성 시스템이 둘이어도 라우트가 판정한다(G-1 (a) — 제품 동작 불변)."""
    set_tier(tier)
    payload = _zone(Q_JSERVER, _config(_ZONE_DBS))
    assert payload is not None and payload["kind"] == "zone_select"


def test_zone_db_without_apm_still_asks(set_tier) -> None:
    """제니퍼 비활성 + 폴스타만이면 시스템 하나 — 존을 가진 시스템의 전량 질문이라 되묻는다."""
    set_tier("intent_orchestration")
    payload = _zone(Q_JSERVER, _config(_ZONE_DBS, apm=False))
    assert payload is not None and payload["kind"] == "zone_select"
