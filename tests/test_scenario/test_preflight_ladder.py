"""사전 점검의 사다리 단 판정 (plans/102 L-3 · D-225).

기준 경로는 3단 `semantic_router`, 1단 `deep_agent`는 부가 경로 opt-in 이다. 둘 다 의도한 단이라
OK 이고, 운영자가 고른 비기준 단(2단·4단)과 1단 opt-in 실패는 **조치를 동반한** 주의다.

`tests/test_scenario/test_preflight.py` 와 나눈 이유: 그 파일은 병행 작업이 크게 고치는 중이라
같은 파일에 넣으면 편집이 겹친다. 헬퍼는 그쪽 `_cfg` 와 같은 모양으로 둔다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.scenario import preflight as pf

_NON_ASCII_PUNCT = ("\u2014", "\u2013", "\u2018", "\u2019", "\u201c", "\u201d")


def _cfg(**kw) -> SimpleNamespace:
    base = dict(
        enable_deepagents_package=False,
        enable_intent_orchestration=False,
        enable_semantic_routing=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def ladder_check(monkeypatch):
    """오케스트레이터 헬스체크·deepagents 임포트를 대역으로 바꾼다 - 네트워크 0."""

    def run(cfg: SimpleNamespace, *, backend: str = "semantic_router", buildable: bool = False):
        monkeypatch.setattr(
            "src.orchestration.deep_agent.select_orchestration_backend", lambda _cfg: backend
        )
        monkeypatch.setattr(pf, "_deep_agent_buildable", lambda _cfg: buildable)
        report = pf.Report()
        pf._check_ladder(cfg, report)
        checks = [c for c in report.checks if c.key == "사다리 단"]
        assert len(checks) == 1
        return checks[0]

    return run


def test_tier3_canonical_is_ok(ladder_check) -> None:
    """3단 기준 경로는 OK 다(D-225 ①)."""
    check = ladder_check(_cfg())
    assert check.verdict == pf.VERDICT_OK
    assert check.observed == "semantic_router (3단 기준 경로)"
    # 1단을 재지 않는 run 이라는 사실(plans/99 §0)은 조치에 남는다.
    assert "ENABLE_DEEPAGENTS_PACKAGE=true" in check.action and "plans/99" in check.action


def test_tier1_optin_is_ok_and_says_optional(ladder_check) -> None:
    """1단 opt-in 은 OK 이고 부가 경로임을 밝힌다(D-225 ②)."""
    check = ladder_check(_cfg(enable_deepagents_package=True), backend="deep_agent", buildable=True)
    assert check.verdict == pf.VERDICT_OK
    assert check.observed == "deep_agent (1단 부가 경로)"
    assert "부가 경로 opt-in" in check.action
    assert "plans/99 목표 9 달성" in check.action


@pytest.mark.parametrize(
    "cfg_kw,observed,needle",
    [
        ({"enable_intent_orchestration": True},
         "intent_orchestration (degraded_reason=intent_flag_on)",
         "ENABLE_INTENT_ORCHESTRATION=false"),
        ({"enable_semantic_routing": False},
         "legacy (degraded_reason=semantic_routing_off)",
         "ENABLE_SEMANTIC_ROUTING=true"),
    ],
)
def test_non_canonical_tier_warns_with_flag_action(ladder_check, cfg_kw, observed, needle) -> None:
    """비기준 단(2·4단)은 주의이고 플래그 조치를 준다."""
    check = ladder_check(_cfg(**cfg_kw))
    assert check.verdict == pf.VERDICT_WARN
    assert check.observed == observed
    assert needle in check.action


@pytest.mark.parametrize(
    "backend,reason,needle",
    [
        ("semantic_router", "orchestrator_unavailable", "ORCHESTRATOR_PROVIDER"),
        ("deep_agent", "package_missing", "wheel"),
    ],
)
def test_optin_failure_warns_even_on_tier3(ladder_check, backend, reason, needle) -> None:
    """1단 opt-in 실패는 3단에 떨어져도 주의다 - 기준 단에 떨어졌다고 OK 로 넘기면 안 된다."""
    check = ladder_check(_cfg(enable_deepagents_package=True), backend=backend, buildable=False)
    assert check.verdict == pf.VERDICT_WARN
    assert check.observed == f"semantic_router (degraded_reason={reason})"
    assert needle in check.action


@pytest.mark.parametrize(
    "cfg_kw,backend,buildable",
    [
        ({}, "semantic_router", False),
        ({"enable_deepagents_package": True}, "deep_agent", True),
        ({"enable_intent_orchestration": True}, "semantic_router", False),
        ({"enable_semantic_routing": False}, "semantic_router", False),
        ({"enable_deepagents_package": True}, "semantic_router", False),
    ],
)
def test_ladder_actions_use_ascii_punctuation(ladder_check, cfg_kw, backend, buildable) -> None:
    """사다리 조치 문구는 ASCII 구두점만 쓴다 - cp949 콘솔에서 em-dash 하나가 런을 죽인다(W5)."""
    check = ladder_check(_cfg(**cfg_kw), backend=backend, buildable=buildable)
    emitted = check.action + check.observed + check.detail
    for ch in _NON_ASCII_PUNCT:
        assert ch not in emitted, f"ASCII 아닌 구두점 {ch!r}: {emitted[:80]}"
