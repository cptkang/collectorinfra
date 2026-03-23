"""synonym_registrar 노드 테스트."""

import pytest

from src.nodes.synonym_registrar import _parse_registration_intent, synonym_registrar
from src.state import create_initial_state


class TestParseRegistrationIntent:
    """유사어 등록 의도 파싱 검증."""

    def test_all_registration_korean(self):
        mode, indices = _parse_registration_intent("전체 등록")
        assert mode == "all"

    def test_all_registration_variant(self):
        mode, _ = _parse_registration_intent("모두 등록해줘")
        assert mode == "all"

    def test_selective_with_comma(self):
        mode, indices = _parse_registration_intent("1, 3 등록")
        assert mode == "selective"
        assert indices == [1, 3]

    def test_selective_single(self):
        mode, indices = _parse_registration_intent("1번 등록")
        assert mode == "selective"
        assert indices == [1]

    def test_selective_multiple(self):
        mode, indices = _parse_registration_intent("1, 2, 3번 등록")
        assert mode == "selective"
        assert set(indices) == {1, 2, 3}

    def test_skip_korean(self):
        mode, _ = _parse_registration_intent("건너뛰기")
        assert mode == "skip"

    def test_skip_no_thanks(self):
        mode, _ = _parse_registration_intent("등록 안 해도 돼")
        assert mode == "skip"

    def test_skip_pass(self):
        mode, _ = _parse_registration_intent("pass")
        assert mode == "skip"

    def test_ambiguous_defaults_to_skip(self):
        """모호한 입력은 skip으로 처리한다."""
        mode, _ = _parse_registration_intent("잘 모르겠어요")
        assert mode == "skip"


class TestSynonymRegistrarNode:
    """synonym_registrar 노드 동작 검증."""

    async def test_no_pending_returns_empty_message(self):
        """pending 없으면 안내 메시지를 반환한다."""
        state = create_initial_state(user_query="전체 등록")
        state["pending_synonym_registrations"] = None

        result = await synonym_registrar(state)
        assert "등록할 유사어 항목이 없습니다" in result["final_response"]
        assert result["pending_synonym_registrations"] is None

    async def test_skip_clears_pending(self):
        """건너뛰기 시 pending이 해제된다."""
        state = create_initial_state(user_query="건너뛰기")
        state["pending_synonym_registrations"] = [
            {"index": 1, "field": "CPU 사용률", "column": "cpu_metrics.usage_pct", "db_id": "polestar"},
        ]
        state["parsed_requirements"] = {"synonym_registration": {"mode": "skip"}}

        result = await synonym_registrar(state)
        assert "건너뛰" in result["final_response"]
        assert result["pending_synonym_registrations"] is None
