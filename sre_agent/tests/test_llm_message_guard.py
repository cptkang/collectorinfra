"""비선두 system 메시지 강등 가드 검증 (D-213 후속 — holmes 압축 ↔ Qwen 템플릿 비호환).

폐쇄망 실측(2026-09-11): holmes 컨텍스트 압축이 산출하는
`[system, user, assistant, system]`의 **마지막 system** 때문에 vLLM(Qwen)이
`BadRequestError: System message must be at the beginning.`으로 거부해 조사가
전건 실패했다. 가드는 그 모양만 교정하고 정상 목록은 건드리지 않는다.

실 LLM 호출 0건 — 순수 함수 + 스텁 클래스로 검증한다.
"""

import logging

from sre_agent.infrastructure.llm_message_guard import (
    install_system_message_guard,
    normalize_system_messages,
)
from sre_agent.settings import AgentSettings

# holmes 압축이 실제로 만드는 모양(compaction.py 실측 — 말미 role="system").
COMPACTED = [
    {"role": "system", "content": "당신은 SRE 조사 에이전트다"},
    {"role": "user", "content": "cob0-hdsapd0a 장애 원인 분석"},
    {"role": "assistant", "content": "지금까지 조사 요약"},
    {"role": "system", "content": "The conversation history has been compacted. Continue."},
]


# ── no-op 보장(정상 경로 비트 동일) ─────────────────────────────────


def test_healthy_messages_returned_unchanged_identity():
    """교정할 것이 없으면 **같은 객체**를 돌려준다 — 정상 조사의 요청이 바뀌지 않는다."""
    healthy = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "t"},
    ]
    assert normalize_system_messages(healthy) is healthy


def test_leading_system_only_is_untouched():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    assert normalize_system_messages(msgs) is msgs


def test_non_list_and_short_inputs_pass_through():
    for value in (None, "not-a-list", [], [{"role": "system", "content": "only"}]):
        assert normalize_system_messages(value) is value


# ── 교정 동작 ───────────────────────────────────────────────────────


def test_compacted_shape_trailing_system_becomes_user():
    out = normalize_system_messages(COMPACTED)

    assert out is not COMPACTED, "교정 시에는 사본을 반환한다(원본 불변)"
    assert COMPACTED[3]["role"] == "system", "원본은 변형되지 않아야 한다"

    assert out[0]["role"] == "system", "0번 시스템 프롬프트는 그대로 둔다"
    assert out[3]["role"] == "user", "말미 system → user 강등"
    assert out[3]["content"] == COMPACTED[3]["content"], "content는 보존한다(요약 손실 금지)"
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]


def test_multiple_misplaced_systems_all_demoted():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "mid-1"},
        {"role": "assistant", "content": "a"},
        {"role": "system", "content": "mid-2"},
    ]
    out = normalize_system_messages(msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert out[1]["content"] == "mid-1" and out[3]["content"] == "mid-2"


def test_demotion_is_logged(caplog):
    """침묵 교정 금지 — 무엇을 고쳤는지 로그로 가시화한다."""
    with caplog.at_level(logging.INFO):
        normalize_system_messages(COMPACTED)
    assert any("user로 강등" in r.getMessage() for r in caplog.records)


# ── LLM 경계 설치 ───────────────────────────────────────────────────


class _StubLLM:
    """DefaultLLM 대역 — completion이 받은 messages를 기록한다."""

    def __init__(self):
        self.seen = None

    def completion(self, messages, tools=None, **kwargs):
        self.seen = messages
        return "ok"


def _install_on(cls) -> bool:
    """테스트용: 임의 클래스에 가드를 설치한다(실제 holmes 대신 스텁을 쓴다)."""
    import sre_agent.infrastructure.llm_message_guard as g

    original = cls.completion
    if getattr(original, g._GUARD_FLAG, False):
        return False
    import functools

    @functools.wraps(original)
    def guarded(self, messages, *args, **kwargs):
        return original(self, g.normalize_system_messages(messages), *args, **kwargs)

    setattr(guarded, g._GUARD_FLAG, True)
    cls.completion = guarded
    return True


def test_guard_normalizes_at_llm_boundary():
    """경계에 걸면 압축 모양이 **전송 직전에** 교정된다."""
    class C(_StubLLM):
        pass

    assert _install_on(C) is True
    llm = C()
    llm.completion(COMPACTED)
    assert [m["role"] for m in llm.seen] == ["system", "user", "assistant", "user"]


def test_guard_install_is_idempotent():
    class C(_StubLLM):
        pass

    assert _install_on(C) is True
    assert _install_on(C) is False, "두 번째 설치는 건너뛴다(중첩 래핑 금지)"


def test_install_on_real_holmes_is_safe_and_idempotent():
    """실 holmes에 설치 — 예외 없이 동작하고 재호출은 False(멱등)."""
    install_system_message_guard()          # 첫 호출(이미 설치돼 있을 수도 있다)
    assert install_system_message_guard() is False


# ── 플래그 ──────────────────────────────────────────────────────────


def test_flag_defaults_on():
    """기본 on — off면 압축 발동 시 조사가 실패한다(명시적 예외 · 만료일 2027-03-11)."""
    assert AgentSettings(_env_file=None, model="m").system_message_position_fix is True


def test_flag_can_be_disabled():
    s = AgentSettings(_env_file=None, model="m", system_message_position_fix=False)
    assert s.system_message_position_fix is False
