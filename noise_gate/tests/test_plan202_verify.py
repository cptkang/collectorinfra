"""plans/202 독립 검증(verifier) — noise_gate 노드 주소 연결 경계.

DB·Redis 없음(프로브 대역 · FakeAsyncRedis). `xfail(strict=True)`는 검증에서 찾은 결함·권고를 고정한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.application import host_candidates as hc
from noise_gate.domain import host_link as hl
from noise_gate.tests.test_plan202_node_address_link import (  # noqa: F401 — fixture 재사용
    NODE_IP,
    POD,
    _apm,
    _attach,
    _cfg,
    _event,
    _pod_event,
    _row,
    _run,
    probe,
)


def test_same_node_registered_in_two_zones_is_ambiguous():
    rows = [("polestar_b0", _row("NODE-01")), ("polestar_cm_gp", _row("NODE-01"))]
    assert hl.node_address_match(rows, host_address=NODE_IP) == {
        "status": "unresolved", "reason": hl.NODE_AMBIGUOUS,
    }


def test_partial_failure_with_one_match_links_and_names_unchecked(probe):  # noqa: ANN001
    # D-355 부기 2 — 폴스타는 존 간 중복 등록이 거의 없어 나머지 존 1대 일치면 연결한다
    probe.table = {"polestar_b0": RuntimeError("down"), "polestar_cm_gp": [_row("NODE-01")]}
    for on in (True, False):
        link = _attach(_cfg(on), _pod_event())["polestar_link"]
        assert link["status"] == "linked" and link["db_id"] == "polestar_cm_gp"
        assert link["unchecked_db_ids"] == "polestar_b0"


def test_partial_failure_with_zero_match_stays_unchecked(probe):  # noqa: ANN001
    probe.table = {"polestar_b0": RuntimeError("down")}
    link = _attach(_cfg(True), _pod_event())["polestar_link"]
    assert link["status"] == "unresolved" and link["reason"] == hl.NODE_UNCHECKED
    assert link["pod"] is True


def test_source_list_resolution_failure_is_unchecked(monkeypatch):
    def _boom(app_config: Any) -> list[str]:
        raise RuntimeError("registry down")

    monkeypatch.setattr(hc, "node_lookup_db_ids", _boom)
    link = _attach(_cfg(False), _pod_event())["polestar_link"]
    assert link == {
        "status": "unresolved", "reason": hl.NODE_UNCHECKED, "pod": True, "host_address": NODE_IP,
    }


def test_no_sources_is_unchecked(monkeypatch):
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: [])
    link = _attach(_cfg(True), _pod_event(), redis=FakeAsyncRedis())["polestar_link"]
    assert link["reason"] == hl.NODE_UNCHECKED


@pytest.mark.parametrize("pod", ["true", 1, "True"])
def test_pod_must_be_json_true_not_truthy(probe, pod):  # noqa: ANN001
    """`apm.pod is True`만 파드 — 문자열·정수는 판정 불가(종전 경로)."""
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    hints = _attach(_cfg(False), _event(_apm(pod=pod, host_address=NODE_IP,
                                             host_address_status="ok")))
    assert "polestar_link" not in hints
    assert probe.calls == []


def test_ip_only_probe_and_exact_ip_filter(probe):  # noqa: ANN001
    """프로브는 IP 후보만 보내고(이름·접두 없음), 다른 IP 행은 세지 않는다."""
    probe.table = {"polestar_cm_gp": [_row("NODE-01"), _row("OTHER", ip="198.51.100.70")]}
    link = _attach(_cfg(False), _pod_event())["polestar_link"]
    assert link["status"] == "linked" and link["server_name"] == "NODE-01"
    assert all(c[1] == [] and c[2] == [] and c[3] == [NODE_IP] for c in probe.calls)


def test_stale_never_links_even_with_host_address(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    link = _attach(_cfg(True), _pod_event(status="stale"), redis=FakeAsyncRedis())["polestar_link"]
    assert link["status"] == "unresolved" and link["reason"] == "node_lookup_stale"
    assert probe.calls == []


def test_non_ipv4_host_address_is_not_probed(probe):  # noqa: ANN001
    evil = "1.2.3.4' OR '1'='1"
    hints = _attach(_cfg(False), _event(_apm(pod=True, host_address=evil,
                                             host_address_status="ok")))
    assert probe.calls == []
    assert hints["polestar_link"]["status"] == "unresolved"


@pytest.mark.xfail(
    strict=True,
    reason="잔여(구현자 보고 · verifier 확인): 수동 재분석 `_manual_event`가 raw_payload를 비워"
    " 파드 알람 재분석에 노드 연결·파드 사유가 실리지 않는다(§2.2 「세 경로 대칭」 미충족)",
)
def test_manual_route_event_keeps_apm_pod_block():
    from src.api.routes.alarm import _manual_event

    decision = {"server_name": POD, "hostname": POD, "db_id": "jennifer_b0"}
    event = _run(_manual_event(None, "a1", decision, None))
    assert event is not None
    assert (event.raw_payload or {}).get("apm", {}).get("pod") is True
