"""instructor 어댑터 테스트 (Plan 79 트랙 E-3 · SPEC-structured-output-backend).

전부 **대역**으로 검증한다 — 실 LLM 호출 0건이므로 D-127 과금 게이트와 무관하다.
대역 구성은 `docs/instructor_intent_extraction_review.md`의 probe를 따른다.

검증 대상(SPEC §Success Criteria):
    S1  백엔드 비활성(기본)이면 어댑터가 관여하지 않는다
    S2  instructor 미설치에서도 앱이 죽지 않고 강등 로그가 남는다
    S3  전송 메시지에 영어 주입문("genius expert"/"Correct your JSON")이 없다
    S4  스키마 블록이 한국어 지시문과 함께 system 말미에 온다(기존 내용 보존)
    S5  검증 실패 재질의가 한국어이고 실패 필드 경로 + 받은 값을 포함한다
    S6  KBGenAI 대역에서 System 다음 빈 AIMessage가 삽입된다
    S7  content가 콘텐츠 블록 리스트여도 파싱된다
    S8  ChatOpenAI 계열이면 TOOLS, 평문 계열이면 MD_JSON
    S9  재시도 소진 시 구조화된 예외가 오르고 삼켜지지 않는다
"""

from __future__ import annotations

from typing import Literal

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from src.clients import instructor_adapter as ia


class Intent(BaseModel):
    intent: Literal["data_query", "alarm_query"]
    confidence: float


KOREAN_SYSTEM = "당신은 인프라 질의 라우터입니다.\n## intent 판단 우선순위\n1. cache_management"


# ─────────────────────────────── 대역 ───────────────────────────────

class _FakeLLM:
    """LangChain BaseChatModel 대역 — ainvoke만 갖는다."""

    _KIND = "plain"

    def __init__(self, *contents):
        self._contents = list(contents)
        self.seen: list = []

    async def ainvoke(self, messages, **_kw):
        self.seen.append(list(messages))
        c = self._contents[min(len(self.seen) - 1, len(self._contents) - 1)]
        return AIMessage(content=c)


class _FakeKBGenAI(_FakeLLM):
    """`is_kbgenai` 판정은 클래스명 비교다(src/utils/llm_compat.py)."""


_FakeKBGenAI.__name__ = "KBGenAIChat"


class _FakeChatOpenAI(_FakeLLM):
    pass


_FakeChatOpenAI.__name__ = "ChatOpenAI"


def _md(body: str) -> str:
    return f"```json\n{body}\n```"


GOOD = _md('{"intent":"data_query","confidence":0.85}')
BAD = _md('{"intent":"data_query","confidence":"높음"}')


def _msgs():
    return [SystemMessage(content=KOREAN_SYSTEM), HumanMessage(content="CPU 높은 서버")]


# ─────────────────────── S1·S2 — 게이트와 강등 ───────────────────────

class TestGatingAndDegradation:
    @pytest.mark.asyncio
    async def test_disabled_backend_returns_none(self):
        """S1 — 기본(none)이면 None을 돌려 호출자가 기존 경로로 간다."""
        llm = _FakeLLM(GOOD)
        out = await ia.try_structured_call(
            llm, _msgs(), Intent, backend="none",
        )
        assert out is None, "백엔드 비활성인데 어댑터가 관여했다"
        assert llm.seen == [], "비활성인데 LLM이 호출됐다"

    @pytest.mark.asyncio
    async def test_missing_instructor_degrades_gracefully(self, monkeypatch, caplog):
        """S2 — 미설치여도 앱이 죽지 않고, 강등 사실이 로그로 남는다(침묵 금지)."""
        def _boom():
            raise ImportError("No module named 'instructor'")

        monkeypatch.setattr(ia, "_load_instructor", _boom)
        llm = _FakeLLM(GOOD)
        with caplog.at_level("WARNING"):
            out = await ia.try_structured_call(
                llm, _msgs(), Intent, backend="instructor",
            )
        assert out is None, "미설치인데 예외가 아니라 값이 돌아왔다"
        assert any("instructor" in r.getMessage() for r in caplog.records), (
            "강등이 침묵했다 — 왜 기존 경로로 내려갔는지 운영에서 알 수 없다."
        )

    def test_availability_probe_does_not_raise(self):
        """가용성 판정은 예외를 던지지 않는다."""
        assert isinstance(ia.structured_backend_available(), bool)


# ─────────────────── S3·S4 — 한국어 핸들러(주입 통제) ───────────────────

class TestKoreanHandler:
    @pytest.mark.asyncio
    async def test_no_english_injection(self):
        """S3 ★ — 기본 MD_JSON 핸들러의 영어 주입문이 남으면 안 된다.

        트랙 A가 고정한 한국어 few-shot과 경쟁할 수 있다(Known Mistakes:
        "프롬프트 강제가 프로필 few-shot 예시와 경쟁").
        """
        llm = _FakeLLM(GOOD)
        await ia.try_structured_call(llm, _msgs(), Intent, backend="instructor")
        blob = "\n".join(
            str(getattr(m, "content", m)) for m in llm.seen[0]
        )
        assert "genius expert" not in blob, "영어 스키마 지시문이 주입됐다"
        assert "Correct your JSON" not in blob, "영어 재질의문이 주입됐다"
        assert "Return the correct JSON response" not in blob

    @pytest.mark.asyncio
    async def test_korean_schema_appended_after_existing_system(self):
        """S4 — 기존 한국어 system은 보존되고, 스키마는 그 **말미**에 붙는다."""
        llm = _FakeLLM(GOOD)
        await ia.try_structured_call(llm, _msgs(), Intent, backend="instructor")
        sys_msgs = [m for m in llm.seen[0] if isinstance(m, SystemMessage)]
        assert sys_msgs, "system 메시지가 사라졌다"
        content = str(sys_msgs[0].content)
        assert KOREAN_SYSTEM in content, "기존 한국어 프롬프트가 덮였다"
        assert content.index(KOREAN_SYSTEM) < content.index("스키마"), (
            "스키마 블록이 기존 프롬프트보다 앞에 왔다 — 말미여야 한다."
        )

    @pytest.mark.asyncio
    async def test_reask_is_korean_with_field_and_value(self, caplog):
        """S5 ★ — 재질의가 한국어이고 **실패 필드 경로 + 받은 값**을 담는다."""
        llm = _FakeLLM(BAD, GOOD)
        out = await ia.try_structured_call(
            llm, _msgs(), Intent, backend="instructor", max_retries=1,
        )
        assert out is not None and out.confidence == 0.85, "재질의 후 복구되지 않았다"
        assert len(llm.seen) == 2, f"재질의가 일어나지 않았다: {len(llm.seen)}회 호출"
        reask = "\n".join(str(getattr(m, "content", m)) for m in llm.seen[1])
        assert "confidence" in reask, "실패 필드 경로가 재질의에 없다"
        assert "높음" in reask, "모델이 준 값이 재질의에 없다 — 무엇이 틀렸는지 알 수 없다"
        assert any(k in reask for k in ("검증", "오류", "다시")), (
            "재질의문이 한국어가 아니다"
        )


# ─────────────────── S6·S7·S8 — 어댑터 변환 규약 ───────────────────

class TestAdapterConversion:
    @pytest.mark.asyncio
    async def test_kbgenai_gets_empty_ai_message(self):
        """S6 — KBGenAI는 System 다음 빈 AIMessage를 요구한다(현재 8곳 산재 → 어댑터로 흡수)."""
        llm = _FakeKBGenAI(GOOD)
        await ia.try_structured_call(llm, _msgs(), Intent, backend="instructor")
        seq = llm.seen[0]
        kinds = [type(m).__name__ for m in seq]
        assert kinds[0] == "SystemMessage", f"첫 메시지가 System이 아니다: {kinds}"
        assert kinds[1] == "AIMessage" and seq[1].content == "", (
            f"System 다음 빈 AIMessage가 없다: {kinds}"
        )

    @pytest.mark.asyncio
    async def test_plain_llm_gets_no_empty_ai_message(self):
        """비-KBGenAI에는 넣지 않는다(불필요한 메시지는 프롬프트를 흔든다)."""
        llm = _FakeLLM(GOOD)
        await ia.try_structured_call(llm, _msgs(), Intent, backend="instructor")
        kinds = [type(m).__name__ for m in llm.seen[0]]
        assert "AIMessage" not in kinds, f"불필요한 빈 AIMessage가 삽입됐다: {kinds}"

    @pytest.mark.asyncio
    async def test_content_block_list_is_parsed(self):
        """S7 — 실 모델은 content를 콘텐츠 블록 **리스트**로 준다(2026-08-04 E1 실측).

        str 가정 시 어댑터가 여기서 깨진다.
        """
        llm = _FakeLLM([{"type": "text", "text": GOOD}])
        out = await ia.try_structured_call(llm, _msgs(), Intent, backend="instructor")
        assert out is not None and out.intent == "data_query", (
            "콘텐츠 블록 리스트 응답이 파싱되지 않았다 — coerce_content_text 경유 필요."
        )

    def test_mode_selection_by_llm_kind(self):
        """S8 — 평문 계열은 MD_JSON, 네이티브 tool-calling 계열은 TOOLS."""
        assert ia.select_mode(_FakeKBGenAI()) == ia.MODE_MD_JSON
        assert ia.select_mode(_FakeLLM()) == ia.MODE_MD_JSON
        assert ia.select_mode(_FakeChatOpenAI()) == ia.MODE_TOOLS


# ─────────────────────── S9 — 소진 시 예외 ───────────────────────

class TestRetryExhaustion:
    @pytest.mark.asyncio
    async def test_exhaustion_raises_structured_error(self):
        """S9 — 소진을 삼키지 않는다. 사유를 응답에 노출할 수 있어야 한다."""
        llm = _FakeLLM(BAD, BAD, BAD)
        with pytest.raises(ia.StructuredOutputError) as ei:
            await ia.try_structured_call(
                llm, _msgs(), Intent, backend="instructor", max_retries=1,
            )
        err = ei.value
        assert err.attempts >= 2, f"재시도가 일어나지 않았다: attempts={err.attempts}"
        assert "confidence" in str(err), "마지막 오류 내용이 예외에 실리지 않았다"
