"""synonym_registrar 노드 테스트."""

import pytest

from src.nodes.synonym_registrar import _parse_registration_intent, synonym_registrar
from src.state import create_initial_state


class _MockLLM:
    """등록 의사 분류 목 LLM (실 호출 0 — D-127)."""

    def __init__(self, content: str):
        self.content = content
        self.calls: list = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)

        class _R:
            pass

        r = _R()
        r.content = self.content
        return r


class _FailingLLM:
    async def ainvoke(self, messages, **kwargs):
        raise RuntimeError("LLM 불가")


def _config(intent_llm_assist: bool):
    """intent_llm_assist만 있는 경량 config 스텁."""

    class _Q:
        pass

    class _C:
        pass

    q = _Q()
    q.intent_llm_assist = intent_llm_assist
    c = _C()
    c.query = q
    return c


def _pending():
    return [
        {"index": 1, "field": "CPU 사용률", "column": "cpu.usage_pct", "db_id": "polestar"},
        {"index": 2, "field": "메모리 사용률", "column": "mem.usage_pct", "db_id": "polestar"},
        {"index": 3, "field": "디스크 사용률", "column": "disk.usage_pct", "db_id": "polestar"},
    ]


class TestParseRegistrationIntent:
    """유사어 등록 의도 결정적 파싱 검증 (Plan 67 R3-(ii) — 확정 불가는 None)."""

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

    def test_ambiguous_returns_none(self):
        """모호한 입력은 확정하지 않는다(종전 기본값 skip → 판정 불가).

        호출부가 LLM 분류·재질의로 넘긴다 — 임의 skip으로 후보를 버리지 않는다.
        """
        assert _parse_registration_intent("잘 모르겠어요") is None

    @pytest.mark.parametrize(
        "query",
        ["2번만 빼고 전부", "3번 제외하고 등록해줘", "1번 말고 다 등록", "2번 외에 전부 등록"],
    )
    def test_exclusion_expression_is_not_resolved_deterministically(self, query):
        """제외 표현은 결정적으로 다루지 않는다 — 종전에는 제외 번호를 등록 대상으로 뒤집었다(A11)."""
        assert _parse_registration_intent(query) is None

    def test_polite_registration_is_not_skipped(self):
        """"괜찮아요, 등록해주세요"가 skip으로 뒤집히지 않는다(부분 문자열 매칭 제거)."""
        assert _parse_registration_intent("괜찮아요, 등록해주세요") != ("skip", [])

    def test_all_scope_adverb_is_not_all_registration(self):
        """"전체적으로 …"는 전체 등록 표현이 아니다."""
        assert _parse_registration_intent("전체적으로 알려줘") is None


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

    async def test_ambiguous_reasks_and_keeps_pending(self):
        """확정 불가 입력은 재질의하고 후보를 유지한다(침묵 skip 금지 — Plan 67 R3-(ii))."""
        state = create_initial_state(user_query="잘 모르겠어요")
        state["pending_synonym_registrations"] = _pending()

        result = await synonym_registrar(state, app_config=_config(False))
        assert "확정하지 못했습니다" in result["final_response"]
        assert result["pending_synonym_registrations"] == _pending()

    async def test_exclusion_expression_reasks_when_llm_disabled(self):
        """제외 표현은 LLM OFF에서 재질의로 처리한다 — 정반대 등록이 나가지 않는다."""
        state = create_initial_state(user_query="2번만 빼고 전부")
        # 상위 파싱(input_parser LLM)이 제외를 이해하지 못해 2번 등록으로 준 상황
        state["parsed_requirements"] = {
            "synonym_registration": {"mode": "selective", "indices": [2]}
        }
        state["pending_synonym_registrations"] = _pending()

        result = await synonym_registrar(state, app_config=_config(False))
        assert "확정하지 못했습니다" in result["final_response"]
        assert result["pending_synonym_registrations"] == _pending()

    async def test_exclusion_expression_resolved_by_llm(self, monkeypatch):
        """LLM ON이면 제외 표현이 나머지 전부(1·3번) 등록으로 해소된다."""
        registered: list[tuple] = []

        class _CacheMgr:
            async def add_synonyms(self, db_id, column, fields, source=""):
                registered.append((db_id, column, tuple(fields)))

            async def add_global_synonym(self, column, fields):
                registered.append(("global", column, tuple(fields)))

        monkeypatch.setattr(
            "src.schema_cache.cache_manager.get_cache_manager", lambda cfg: _CacheMgr()
        )

        state = create_initial_state(user_query="2번만 빼고 전부 등록해줘")
        state["parsed_requirements"] = {
            "synonym_registration": {"mode": "selective", "indices": [2]}
        }
        state["pending_synonym_registrations"] = _pending()

        llm = _MockLLM('{"mode": "selective", "indices": [1, 3], "reason": "2번 제외"}')
        result = await synonym_registrar(state, llm=llm, app_config=_config(True))

        assert result["pending_synonym_registrations"] is None
        assert "2건 유사어 등록 완료" in result["final_response"]
        assert "CPU 사용률" in result["final_response"]
        assert "디스크 사용률" in result["final_response"]
        assert "메모리 사용률" not in result["final_response"]

    async def test_llm_failure_falls_back_to_reask(self):
        """LLM 실패 시 재질의로 폴백한다(침묵 오답 금지)."""
        state = create_initial_state(user_query="음... 그거 해줘")
        state["pending_synonym_registrations"] = _pending()

        result = await synonym_registrar(
            state, llm=_FailingLLM(), app_config=_config(True)
        )
        assert "확정하지 못했습니다" in result["final_response"]
        assert result["pending_synonym_registrations"] == _pending()

    async def test_llm_unclear_falls_back_to_reask(self):
        """LLM이 unclear를 주면 재질의한다."""
        state = create_initial_state(user_query="글쎄요")
        state["pending_synonym_registrations"] = _pending()

        llm = _MockLLM('{"mode": "unclear", "indices": [], "reason": "의사 불명"}')
        result = await synonym_registrar(state, llm=llm, app_config=_config(True))
        assert "확정하지 못했습니다" in result["final_response"]

    async def test_llm_not_called_when_deterministic_resolves(self):
        """결정적 판정이 확정한 입력은 LLM을 호출하지 않는다(동작 불변·비용 0)."""
        state = create_initial_state(user_query="건너뛰기")
        state["pending_synonym_registrations"] = _pending()

        llm = _MockLLM('{"mode": "all", "indices": []}')
        result = await synonym_registrar(state, llm=llm, app_config=_config(True))
        assert llm.calls == []
        assert "건너뛰" in result["final_response"]
