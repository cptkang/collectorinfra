"""멀티턴 API 테스트."""

import pytest

from src.api.routes.query import _parse_approval


class TestParseApproval:
    """승인 의도 파싱 검증."""

    def test_approve_korean(self):
        action, _ = _parse_approval("실행")
        assert action == "approve"

    def test_approve_english(self):
        action, _ = _parse_approval("approve")
        assert action == "approve"

    def test_approve_yes(self):
        action, _ = _parse_approval("네")
        assert action == "approve"

    def test_reject_cancel(self):
        action, _ = _parse_approval("취소")
        assert action == "reject"

    def test_reject_english(self):
        action, _ = _parse_approval("reject")
        assert action == "reject"

    def test_modify_with_sql(self):
        action, sql = _parse_approval("SELECT * FROM servers WHERE id > 5")
        assert action == "modify"
        assert "SELECT" in sql

    def test_ok_approves(self):
        action, _ = _parse_approval("ok")
        assert action == "approve"

    def test_no_rejects(self):
        action, _ = _parse_approval("no")
        assert action == "reject"


class TestParseApprovalFailClosed:
    """승인 오탐 차단 검증 (Plan 67 Phase 0 ⑧).

    기본값이 approve이고 승인어를 prefix로 매칭하던 시절에는 "확인해보고 알려줘"처럼
    승인이 아닌 입력이 승인으로 해석되어 미승인 SQL이 실행됐다.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "확인해보고 알려줘",       # 승인어 prefix 오탐 (실제 사고 사례)
            "네 그럼 메모리도 보여줘",  # 승인어로 시작하지만 새 질의
            "실행 중인 프로세스 목록",   # "실행" prefix 오탐
            "노드 목록 알려줘",         # 승인과 무관한 후속 질의
            "그럼 CPU 사용률은?",
            "",
        ],
    )
    def test_ambiguous_input_is_not_approved(self, query):
        action, _ = _parse_approval(query)
        assert action != "approve"

    @pytest.mark.parametrize(
        "query, expected",
        [
            ("실행해줘", "approve"),
            ("실행해 주세요", "approve"),
            ("승인합니다", "approve"),
            ("네!", "approve"),
            ("ok.", "approve"),
            ("ㅇㅇ", "approve"),
            ("취소해줘", "reject"),
            ("아니요", "reject"),
            ("거부합니다", "reject"),
        ],
    )
    def test_explicit_intent_still_recognized(self, query, expected):
        """어미·문장부호가 붙은 명시적 승인·거부는 그대로 인식한다."""
        action, _ = _parse_approval(query)
        assert action == expected

    def test_unrecognized_input_defaults_to_reject(self):
        """의도 불명이면 실행하지 않는다(fail-closed)."""
        action, sql = _parse_approval("이 쿼리 결과를 엑셀로 정리해줘")
        assert action == "reject"
        assert sql == ""


class TestApprovalLLMAssist:
    """승인 의사 LLM 보조 (Plan 67 R3-(ii) / A12) — fail-closed 불변(D-130).

    결정적 판정이 확정한 입력은 LLM을 거치지 않고, 확정 불가일 때만 LLM 분류를 시도한다.
    LLM이 고신뢰 approve를 주지 않으면 거부를 유지한다. 실 LLM 호출 0(목 — D-127).
    """

    class _MockLLM:
        def __init__(self, content):
            self.content = content
            self.calls = []

        async def ainvoke(self, messages, **kwargs):
            self.calls.append(messages)

            class _R:
                pass

            r = _R()
            r.content = self.content
            return r

    def _config(self, intent_llm_assist):
        class _Q:
            pass

        class _C:
            pass

        q = _Q()
        q.intent_llm_assist = intent_llm_assist
        c = _C()
        c.query = q
        return c

    def _patch_llm(self, monkeypatch, llm):
        import src.llm as llm_mod
        import src.routing.intent_confirm as ic

        monkeypatch.setattr(llm_mod, "create_llm", lambda cfg, **kw: llm)
        return ic

    async def test_flag_off_keeps_deterministic_reject(self, monkeypatch):
        """플래그 OFF면 LLM을 호출하지 않고 종전 거부 판정을 유지한다."""
        from src.api.routes.query import resolve_approval_action

        llm = self._MockLLM('{"intent": "approve", "confidence": 1.0}')
        self._patch_llm(monkeypatch, llm)
        action, sql = await resolve_approval_action(
            "네 실행해주세요 감사합니다", self._config(False)
        )
        assert (action, sql) == ("reject", "")
        assert llm.calls == []

    async def test_deterministic_approve_skips_llm(self, monkeypatch):
        """단독 승인 표현은 LLM 없이 그대로 승인(동작 불변)."""
        from src.api.routes.query import resolve_approval_action

        llm = self._MockLLM('{"intent": "reject", "confidence": 1.0}')
        self._patch_llm(monkeypatch, llm)
        action, _ = await resolve_approval_action("실행해줘", self._config(True))
        assert action == "approve"
        assert llm.calls == []

    async def test_llm_recovers_polite_approval(self, monkeypatch):
        """결정적 판정이 놓치는 "네 실행해주세요 감사합니다"를 LLM이 승인으로 회복한다."""
        from src.api.routes.query import resolve_approval_action

        llm = self._MockLLM('{"intent": "approve", "confidence": 0.95, "reason": "실행 승인"}')
        self._patch_llm(monkeypatch, llm)
        action, _ = await resolve_approval_action(
            "네 실행해주세요 감사합니다", self._config(True)
        )
        assert action == "approve"
        assert len(llm.calls) == 1

    @pytest.mark.parametrize(
        "content",
        [
            '{"intent": "approve", "confidence": 0.5}',   # 확신도 미달
            '{"intent": "unclear", "confidence": 0.9}',   # 판정 불가
            '{"intent": "approve"}',                      # 확신도 누락
            "JSON 아님",                                   # 형식 오류
        ],
    )
    async def test_llm_uncertainty_stays_rejected(self, monkeypatch, content):
        """LLM이 확신하지 못하면 승인하지 않는다(fail-closed 불변)."""
        from src.api.routes.query import resolve_approval_action

        llm = self._MockLLM(content)
        self._patch_llm(monkeypatch, llm)
        action, _ = await resolve_approval_action("확인해보고 알려줘", self._config(True))
        assert action == "reject"

    async def test_llm_failure_stays_rejected(self, monkeypatch):
        """LLM 호출이 실패하면 종전 결정적 판정(거부)을 유지한다."""
        from src.api.routes.query import resolve_approval_action

        class _Boom:
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("LLM 불가")

        self._patch_llm(monkeypatch, _Boom())
        action, _ = await resolve_approval_action("그럼 CPU 사용률은?", self._config(True))
        assert action == "reject"

    def test_both_text_routes_resolve_approval(self):
        """두 텍스트 라우트가 동일한 승인 해소 헬퍼를 호출한다(비대칭 방지 — D-066)."""
        import inspect

        from src.api.routes import query as query_routes

        for handler in (query_routes.process_query, query_routes.process_query_stream):
            src = inspect.getsource(handler)
            assert "_resolve_turn_approval" in src, (
                f"{handler.__name__} 가 승인 해소 헬퍼를 호출하지 않음"
            )

    async def test_non_approval_turn_skips_resolution(self):
        """승인 대기 턴이 아니면 승인 해소를 하지 않는다(None)."""
        from src.api.routes.query import _resolve_turn_approval
        from src.api.schemas import QueryRequest

        body = QueryRequest(query="CPU 사용률 조회", thread_id="t-1")
        assert await _resolve_turn_approval(body, None, self._config(True)) is None
        assert await _resolve_turn_approval(body, {"awaiting_approval": False}, self._config(True)) is None

    async def test_llm_approve_requires_deterministic_corroboration(self, monkeypatch):
        """LLM의 approve 하나만으로는 실행하지 않는다 — 원문에 승인 어휘가 없으면 거부."""
        from src.api.routes.query import resolve_approval_action

        llm = self._MockLLM('{"intent": "approve", "confidence": 0.99, "reason": "환각"}')
        self._patch_llm(monkeypatch, llm)
        action, _ = await resolve_approval_action(
            "이 쿼리 결과를 엑셀로 정리해줘", self._config(True)
        )
        assert action == "reject"
        assert len(llm.calls) == 1  # LLM은 호출했으나 보강 신호가 없어 인정하지 않음

    @pytest.mark.parametrize(
        "query", ["네 실행해주세요 감사합니다", "그대로 진행해줘 고마워", "ㅇㅇ 승인할게 부탁해"]
    )
    async def test_llm_approve_with_corroboration_is_accepted(self, monkeypatch, query):
        """승인 어휘가 있는 변형 표현은 LLM 고신뢰 판정으로 회복한다."""
        from src.api.routes.query import resolve_approval_action

        llm = self._MockLLM('{"intent": "approve", "confidence": 0.95, "reason": "실행 승인"}')
        self._patch_llm(monkeypatch, llm)
        action, _ = await resolve_approval_action(query, self._config(True))
        assert action == "approve"

    def test_confidence_threshold_is_a_code_constant(self):
        """고신뢰 기준은 코드 상수다(설정·프롬프트로 낮출 수 없음)."""
        from src.routing.intent_confirm import APPROVAL_MIN_CONFIDENCE

        assert isinstance(APPROVAL_MIN_CONFIDENCE, float)
        assert APPROVAL_MIN_CONFIDENCE >= 0.8

    async def test_decision_basis_is_logged(self, monkeypatch, caplog):
        """판정 근거(결정적/LLM·확신도)가 로그에 남는다(감사 가능성)."""
        import logging

        from src.api.routes.query import resolve_approval_action

        with caplog.at_level(logging.INFO, logger="src.api.routes.query"):
            await resolve_approval_action("실행해줘", self._config(False))
        assert any(
            "[승인판정]" in r.getMessage() and "basis=deterministic" in r.getMessage()
            and "action=approve" in r.getMessage()
            for r in caplog.records
        )

        caplog.clear()
        llm = self._MockLLM('{"intent": "approve", "confidence": 0.93, "reason": "승인"}')
        self._patch_llm(monkeypatch, llm)
        with caplog.at_level(logging.INFO, logger="src.api.routes.query"):
            await resolve_approval_action("네 실행해주세요 감사합니다", self._config(True))
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "[승인판정]" in m and "basis=llm" in m and "confidence=0.93" in m for m in messages
        )
