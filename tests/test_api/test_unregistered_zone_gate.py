"""미등록 존 지목 시 존 선택 역질문 (plans/108 CU-B2 · G-3 사용자 확정 2026-09-21).

run `20260918-182507` R3-03(3회 전건 동일): "판교존 서버 목록"에 "판교존"이 스키마에 없다는
주석만 남기고 전 서버 1,690건을 반환했다(`row_count.max: 0` 위반). 지목한 스코프를 조용히
버린 것이라 「침묵적 폴백 금지」 위반이다.

텍스트·파일 두 경로 **대칭**을 함께 단언한다 — 한쪽만 고치는 비대칭이 이 저장소의 반복 원인이다.
전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes.query import (
    _file_zone_clarification_or_none,
    _unregistered_zone_clarification_or_none,
    _zone_clarification_or_none,
)
from src.api.schemas import QueryRequest

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _config(active=_ACTIVE, exclusive=True):
    return SimpleNamespace(
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: active,
            zone_group_exclusive=exclusive,
        )
    )


class TestHelper:
    def test_unregistered_zone_returns_zone_select(self):
        payload = _unregistered_zone_clarification_or_none("판교존 서버 목록", _config())
        assert payload is not None
        assert payload["kind"] == "zone_select"
        assert payload["options"], "선택지가 비면 역질문이 막다른 길이 된다"

    def test_question_names_the_unknown_zone(self):
        """'0건'이 아니라 '그런 존이 없다'를 말해야 한다(R3-03 manual_review)."""
        payload = _unregistered_zone_clarification_or_none("판교존 서버 목록", _config())
        assert "판교존" in payload["question"]
        assert "등록되지 않은 존" in payload["question"]

    def test_original_query_preserved_for_resend(self):
        payload = _unregistered_zone_clarification_or_none("판교존 서버 목록", _config())
        assert payload["original_query"] == "판교존 서버 목록"

    def test_has_file_flag_propagates(self):
        payload = _unregistered_zone_clarification_or_none(
            "판교존 채워줘", _config(), has_file=True
        )
        assert payload["has_file"] is True

    @pytest.mark.parametrize("query", ["은행존 서버 목록", "기존 서버 목록", "서버 목록"])
    def test_registered_or_ordinary_query_passes_through(self, query):
        assert _unregistered_zone_clarification_or_none(query, _config()) is None


class TestTextGate:
    """`_zone_clarification_or_none` — 좁히기 조건보다 앞서 발동해야 한다."""

    def test_r3_03_reproduction_now_asks(self):
        """R3-03 재현: 종전에는 None(=침묵 통과)이었다."""
        body = QueryRequest(query="판교존 서버 목록")
        payload = _zone_clarification_or_none(body, None, _config())
        assert payload is not None
        assert "판교존" in payload["question"]

    def test_fires_even_though_not_full_scan_query(self):
        """'모든/전체'가 없어 종전 좁히기 조건(is_full_scan_query)에 걸리지 않던 형태."""
        body = QueryRequest(query="판교존 CPU 사용률")
        assert _zone_clarification_or_none(body, None, _config()) is not None

    def test_resume_turn_not_intercepted(self):
        """선택 재개 턴은 가로채지 않는다 — 무한 역질문 방지."""
        body = QueryRequest(query="판교존 서버 목록", selected_db_ids=["polestar_cm_gp"])
        assert _zone_clarification_or_none(body, None, _config()) is None

    @pytest.mark.parametrize("query", [
        "기존 서버 목록 보여줘",
        "보존 정책 확인",
        "의존 관계 조회",
    ])
    def test_no_false_positive_on_ordinary_words(self, query):
        body = QueryRequest(query=query)
        payload = _zone_clarification_or_none(body, None, _config())
        assert payload is None or "등록되지 않은 존" not in payload["question"]

    def test_registered_zone_unaffected(self):
        """등록된 존은 종전 동작 그대로(역질문 없이 통과)."""
        body = QueryRequest(query="은행존 서버 목록")
        assert _zone_clarification_or_none(body, None, _config()) is None


class TestFileGateSymmetry:
    """파일(폼필) 경로도 같은 사실을 전달한다 — 단일/멀티 비대칭 금지."""

    def test_file_path_names_the_unknown_zone(self):
        payload = _file_zone_clarification_or_none("판교존 채워줘", None, _config())
        assert payload is not None
        assert "판교존" in payload["question"]
        assert payload["has_file"] is True

    def test_file_resume_turn_not_intercepted(self):
        payload = _file_zone_clarification_or_none(
            "판교존 채워줘", ["polestar_cm_gp"], _config()
        )
        assert payload is None or "등록되지 않은 존" not in payload["question"]
