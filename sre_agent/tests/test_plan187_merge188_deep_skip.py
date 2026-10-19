"""plans/187 심층 조사 × plans/188 — 툴셋 실패·도구 호출 마크업 답은 심층 서술로 쓰지 않는다."""

from types import SimpleNamespace

from sre_agent.application.investigation_dispatcher import _deep_skip_reason
from sre_agent.domain.answer_markup import MALFORMED_ANSWER_LIMITATION


def test_normal_answer_is_not_skipped():
    assert _deep_skip_reason(SimpleNamespace(answer="CPU 포화 — apm_app_health 근거")) is None


def test_tools_unavailable_is_skipped_with_reason():
    result = SimpleNamespace(answer="조사 불가", tools_unavailable="조사 불가 — 관측 도구 연결 실패")
    assert _deep_skip_reason(result) == "조사 불가 — 관측 도구 연결 실패"


def test_tool_call_markup_answer_is_skipped_without_raw_text():
    result = SimpleNamespace(answer="<|tool_call>call:polestar_metric_trend{}<tool_call|>")
    assert _deep_skip_reason(result) == MALFORMED_ANSWER_LIMITATION
