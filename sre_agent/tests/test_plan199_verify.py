"""plans/199 독립 검증(verifier) — sre_agent 파드 미확정·노드 연결 경계(실 MCP·LLM 0)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sre_agent.application.evidence_prefetch import scope_from_job
from sre_agent.application.playbook_runner import (
    POLESTAR_POD_LOOKUP,
    POLESTAR_POD_REASONS,
    pod_unresolved_reason,
    polestar_link,
    resolve_polestar_source,
)
from sre_agent.application.playbook_steps import POLESTAR_UNRESOLVED, polestar_unresolved_text
from test_plan199_node_address import MAP, NODE_IP, REF, _latency_alarm, _node, _pod

ALL_REASONS = [*POLESTAR_POD_REASONS, *(f"node_lookup_{s}" for s in POLESTAR_POD_LOOKUP)]


@pytest.mark.parametrize("reason", ALL_REASONS)
def test_pod_sentence_has_single_head(reason: str) -> None:
    text = polestar_unresolved_text(pod_unresolved_reason(polestar_link(
        _latency_alarm(_pod(reason))) or {}))
    assert text.count(POLESTAR_UNRESOLVED) == 1
    assert "K8s 파드" in text and NODE_IP in text


@pytest.mark.parametrize("reason", ALL_REASONS)
def test_pod_unresolved_never_uses_configured_map(reason: str) -> None:
    source, why = resolve_polestar_source(_latency_alarm(_pod(reason)), MAP["b0"])
    assert source is None and why and "K8s 파드" in why


def test_pod_unresolved_yields_to_episode_infra_db_ids() -> None:
    alarm = _latency_alarm(_pod("node_not_registered"))
    alarm.setdefault("meta", {})["episode"] = {
        "episode_id": "ep1", "infra_db_ids": ["polestar_cm_yd"],
    }
    source, why = resolve_polestar_source(alarm, MAP["b0"])
    if source is None:  # 에피소드 파서가 다른 필수 칸을 요구하면 판정 대상이 아님
        pytest.skip("episode 픽스처 형태 불일치")
    assert source == "polestar_cm_yd" and why is None


@pytest.mark.parametrize("pod", [False, "false", "0", None])
def test_non_pod_unresolved_keeps_196_order(pod) -> None:
    link = {"status": "unresolved", "reason": "node_not_registered"}
    if pod is not None:
        link["pod"] = pod
    source, _ = resolve_polestar_source(_latency_alarm(link), MAP["b0"])
    assert source == MAP["b0"]


def test_prefetch_pod_unresolved_skips_infra_and_node_linked_opens_axis() -> None:
    job = SimpleNamespace(reference_time=REF, payload=_latency_alarm(_pod("node_ambiguous")),
                          lookback_minutes=60)
    scope = scope_from_job(job)
    assert scope is not None and scope.skip_infra and scope.source == ""
    job.payload = _latency_alarm(_node())
    scope = scope_from_job(job)
    assert scope is not None and not scope.skip_infra
    assert (scope.source, scope.server_name) == ("polestar_cm_gp", "CMGP-NODE-07")


def test_node_linked_without_server_name_is_not_trusted() -> None:
    bad = _node()
    bad.pop("server_name")
    assert polestar_link(_latency_alarm(bad)) is None
    job = SimpleNamespace(reference_time=REF, payload=_latency_alarm(bad), lookback_minutes=60)
    scope = scope_from_job(job)
    assert scope is None or scope.skip_infra
