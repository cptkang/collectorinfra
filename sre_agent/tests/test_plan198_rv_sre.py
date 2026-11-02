"""plans/198 sre_agent 측 독립 재검토(2라운드) — 결함 재현과 off 경로 확인.

결함 재현 4건은 교정(fix198 sre) 뒤 xfail 마커를 지워 회귀 테스트로 둔다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sre_agent.application.evidence_prefetch import (
    TOOL_TOPOLOGY,
    EvidenceScope,
    build_calls,
    prefetch_and_correlate,
)
from sre_agent.domain.evidence_ledger import build_ledger, check_claim

REF = "2026-10-19T07:20:00"


def _scope(**kw) -> EvidenceScope:
    base = dict(source="polestar_cm_gp", server_name="web01", reference_time=REF,
                lookback_minutes=60, baseline_periods=24)
    base.update(kw)
    return EvidenceScope(**base)


def _batch(neighbor_alarm_time: str | None):
    """대표 web01 알람 0건 · 토폴로지 1홉 db01 · db01 알람(주어진 시각 1건)."""

    def _call(calls):
        out = []
        for tool, args in calls:
            if tool == TOOL_TOPOLOGY:
                out.append(json.dumps({"rows": [{"resource_name": "db01", "hop": 1}]}))
            elif tool == "polestar_incident_alarms" and args["server_name"] == "db01":
                rows = ([{"alarm_time": neighbor_alarm_time, "severity": 3, "alarm_name": "cpu"}]
                        if neighbor_alarm_time else [])
                out.append(json.dumps({"rows": rows}))
            else:
                out.append(json.dumps({"rows": []}))
        return out

    return _call


# ── off 경로 ────────────────────────────────────────────────────────


def test_build_calls_without_neighbors_has_no_topology_call():
    assert all(t != TOOL_TOPOLOGY for t, _ in build_calls(_scope()))


def test_topology_call_is_one_hop_and_last():
    calls = build_calls(_scope(neighbors_max=5, related_servers=("db09",)))
    assert calls[-1] == (TOOL_TOPOLOGY, {"source": "polestar_cm_gp", "server_name": "web01",
                                         "max_hops": 1})


def test_prefetch_without_neighbors_has_no_neighbors_key():
    assert "neighbors" not in prefetch_and_correlate(_scope(), _batch(None)).to_dict()


# ── 결함 재현 ───────────────────────────────────────────────────────


def test_neighbor_batch_failure_keeps_representative_correlation():
    first = _batch(None)
    state = {"n": 0}

    def _call(calls):
        state["n"] += 1
        if state["n"] == 2:   # 이웃 알람 배치(두 번째 배치)만 실패
            raise TimeoutError("neighbor batch hung")
        return first(calls)

    result = prefetch_and_correlate(_scope(neighbors_max=5), _call)
    assert result.neighbors is not None   # 이웃은 결손 노트로, 대표 상관은 보존


def test_neighbor_lead_note_consistent_with_neighbor_block():
    result = prefetch_and_correlate(_scope(neighbors_max=5), _batch("2026-10-19 07:30:00"))
    host = result.neighbors["hosts"][0]
    assert host["first_alarm_offset_min"] == 10 and host["leads"] is False
    assert not any("db01" in n and "대표보다 선행" in n for n in result.notes)


def _ledger():
    rec = SimpleNamespace(tool_name="polestar_metric_trend", output='{"cpu": 95.2}',
                          params={"server_name": "web01"}, description="")
    return build_ledger([rec])


def test_numbered_list_marker_not_counted():
    """대조군 — `1. ` 목록 머리표는 수치로 세지 않는다."""
    assert check_claim("4. web01 CPU 95%", _ledger()).unmatched == ()


@pytest.mark.parametrize("line", ["### 4. 권고 조치", "**3) 원인 후보**"])
def test_heading_section_number_not_counted(line):
    assert check_claim(line, _ledger()).unmatched == ()
