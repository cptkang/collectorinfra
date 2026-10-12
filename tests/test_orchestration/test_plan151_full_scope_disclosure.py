"""plans/151 W3 — 거짓 「전체」 고지(F3 · G-3 (a)) (LLM 0 · DB 0).

「서버들」·「서버별」·「각 서버」가 LIMIT 상향용 판정(`has_all_scope_keyword`)을 통해 「전체」로
읽혀 ITAM 답에 「전체가 아니라 … 미조회」가 붙던 결함의 재현이다. 고지는 D-279 ⑦ 문구
(전체·모든·모두)일 때만, 그리고 레지스트리 존이 배정된 DB끼리만 따진다.
"""

from __future__ import annotations

import importlib

import pytest

from src.domain import disclosure as disc
from src.utils.query_gen_common import has_all_scope_keyword, has_full_scope_request

# 패키지 `__init__`이 같은 이름의 함수를 다시 내보내 `from … import 모듈`이 함수를 가리킨다
og = importlib.import_module("src.nodes.output_generator")
ra = importlib.import_module("src.orchestration.result_aggregator")

_ZONED = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_ACTIVE = [*_ZONED, "itam"]
_ITAM_QUERIES = [
    "그 서버들 지원 종료일도 알려줘",
    "서버별 유지보수 계약 만료일 알려줘",
    "각 서버의 취득일과 취득금액",
]
_NARROWED = {
    "selected": ["공동존 김포"],
    "skipped": ["은행존", "공동존 여의도"],
    "skipped_db_ids": ["polestar_b0", "polestar_cm_yd"],
}


# ── 판정 함수 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query", ["전체 서버 수", "모든 서버 조회", "서버 모두 보여줘", "전체를 조회", "전체서버 조회"]
)
def test_full_scope_request_accepts_decision_terms(query: str) -> None:
    assert has_full_scope_request(query) is True


@pytest.mark.parametrize(
    "query",
    [*_ITAM_QUERIES, "서버 별 CPU 평균", "전체적으로 CPU 높은 서버", "전체적인 추이", "", None],
)
def test_full_scope_request_rejects_listing_and_derivatives(query: str | None) -> None:
    assert has_full_scope_request(query) is False


@pytest.mark.parametrize("query", [*_ITAM_QUERIES, "서버 별 CPU 평균", "전체 서버"])
def test_limit_judgement_unchanged(query: str) -> None:
    """LIMIT 상향용 판정은 종전 표면어(서버별·서버들·각 서버 포함) 그대로다."""
    assert has_all_scope_keyword(query) is True


# ── scope_partial_text (W-5) ─────────────────────────────────────────────────


@pytest.mark.parametrize("query", _ITAM_QUERIES)
def test_itam_listing_queries_not_disclosed(query: str) -> None:
    assert og.scope_partial_text(
        {"user_query": query}, queried_db_ids=["itam"],
        db_origin="classified", active_db_ids=_ACTIVE,
    ) is None


def test_unzoned_only_query_not_disclosed_even_with_full_term() -> None:
    """존 없는 DB만 조회했으면 「전체」라도 미조회 존이 성립하지 않는다."""
    assert og.scope_partial_text(
        {"user_query": "전체 서버 자산 목록"}, queried_db_ids=["itam"],
        db_origin="classified", active_db_ids=_ACTIVE,
    ) is None


def test_polestar_listing_query_no_longer_disclosed() -> None:
    """폴스타 「서버별 …」 질문도 「전체」 요청이 아니다(3단 비교 arm 응답 변화)."""
    assert og.scope_partial_text(
        {"user_query": "서버별 CPU 사용률 평균"}, queried_db_ids=["polestar_cm_gp"],
        db_origin="classified", active_db_ids=_ACTIVE,
    ) is None


def test_full_scope_with_partial_zones_still_disclosed() -> None:
    """「전체 서버」 + 존 일부 조회는 종전대로 고지하고, 존 없는 DB는 미조회로 적지 않는다."""
    text = og.scope_partial_text(
        {"user_query": "전체 서버 수"}, queried_db_ids=["polestar_cm_gp"],
        db_origin="classified", active_db_ids=_ACTIVE,
    )

    assert text is not None and text.startswith("전체가 아니라")
    assert "ITAM" not in text


def test_all_zones_queried_with_unzoned_active_not_disclosed() -> None:
    """존 DB를 전부 조회했으면 활성 목록에 존 없는 DB가 남아도 고지하지 않는다."""
    assert og.scope_partial_text(
        {"user_query": "전체 서버 수"}, queried_db_ids=_ZONED,
        db_origin="classified", active_db_ids=_ACTIVE,
    ) is None


# ── 좁힌 범위 문구 머리(W-2 · render_narrowed_note) ──────────────────────────


def test_narrowed_note_head_follows_full_scope_request() -> None:
    listing = og._append_scope_note(
        "본문", {"user_query": "서버별 CPU 평균", "scope_narrowed": _NARROWED}
    )
    full = og._append_scope_note(
        "본문", {"user_query": "전체 서버 CPU 평균", "scope_narrowed": _NARROWED}
    )

    assert "조회하지 않았습니다" in listing and "전체가 아니라" not in listing
    assert "전체가 아니라 공동존 김포만" in full


# ── 2단 집계기 턴 단위 고지 ──────────────────────────────────────────────────


@pytest.mark.parametrize("query", _ITAM_QUERIES)
def test_aggregator_itam_turn_has_no_scope_partial(query: str) -> None:
    narrowed, items = ra._turn_level_disclosures(
        {"target_databases": [{"db_id": "itam"}], "db_scope_source": "classified"},
        {"user_query": query},
    )

    assert narrowed is None
    assert disc.SCOPE_PARTIAL not in [d["kind"] for d in items]
