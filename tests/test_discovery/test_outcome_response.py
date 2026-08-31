"""탐색 결과 응답 — 단일·다중·0건 (Plan 82 W5-T4 · SPEC-host-discovery).

★ 성공 경로에서 **어느 존에서 찾았는지 밝히는지**를 단언한다. 사용자가 위치를 말하지
않았으므로 밝히지 않으면 어느 존의 서버를 보고 있는지 모른 채 결과를 읽게 된다 —
동명 호스트가 있는 환경에서는 조용한 오답과 같다.

DB·LLM 0(D-127).
"""

from __future__ import annotations

import json

import pytest

from src.domain.host_discovery import (
    SweepOutcome,
    ZoneHit,
    classify,
    trace_payload,
)
from src.orchestration.process_query import _discovered_zone_label, _empty_result
from src.state import create_initial_state

GP_HIT = ZoneHit(db_id="polestar_cm_gp", zone_label="공동존 김포", hostname="abd00.gp")
ALL_ZONES = ("은행존", "공동존 김포", "공동존 여의도")


class TestEmptyResultCarriesDiscovery:
    def test_discovery_absent_keeps_shape_byte_identical(self):
        """탐색이 없으면 반환 dict가 종전과 같다(회귀 안전)."""
        result = _empty_result("없음", "polestar_cm_gp", "abd00")

        assert "discovery" not in result["process_query"]
        assert set(result["process_query"]) == {"db_id", "hostname", "total_count"}

    def test_discovery_trace_lands_in_process_query_meta(self):
        trace = trace_payload(classify(SweepOutcome(
            identifier="abd00", swept=ALL_ZONES, errors={"은행존": "조회 실패"},
        )))

        result = _empty_result(
            "못 찾음", None, "abd00", reason="not_found", discovery=trace,
        )

        meta = result["process_query"]
        assert meta["reason"] == "not_found"
        assert meta["discovery"]["swept"] == list(ALL_ZONES)
        assert meta["discovery"]["errors"] == {"은행존": "조회 실패"}
        json.dumps(result["process_query"])  # 직렬화 가능

    def test_empty_result_is_marked_insufficient(self):
        """0건 안내는 `is_sufficient=False` — 정상 결과로 서술되면 안 된다."""
        result = _empty_result("못 찾음", None, "abd00", reason="not_found")

        assert result["organized_data"]["is_sufficient"] is False
        assert result["query_results"] == []


class TestZoneDisclosure:
    def test_resolved_zone_label_is_extracted(self):
        trace = trace_payload(classify(SweepOutcome(
            identifier="abd00", swept=ALL_ZONES, hits=(GP_HIT,),
        )))

        assert _discovered_zone_label({"state": "resolved", "trace": trace}) == "공동존 김포"

    def test_no_label_when_discovery_did_not_run(self):
        assert _discovered_zone_label(None) == ""

    def test_no_label_when_ambiguous(self):
        """되묻는 상황에서는 존을 확정하지 않았으므로 밝힐 존도 없다."""
        trace = trace_payload(classify(SweepOutcome(
            identifier="abd00", swept=ALL_ZONES,
            hits=(GP_HIT, ZoneHit(db_id="polestar_b0", zone_label="은행존")),
        )))

        assert _discovered_zone_label({"state": "ambiguous", "trace": trace}) == ""

    def test_no_label_when_not_found(self):
        trace = trace_payload(classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES)))

        assert _discovered_zone_label({"state": "not_found", "trace": trace}) == ""


class TestStateField:
    def test_discovery_trace_is_request_scoped(self):
        """요청 스코프 — 체크포인터가 이전 턴 탐색 결과를 승계하면 엉뚱한 존을 쓴다."""
        state = create_initial_state(user_query="abd00 서버의 프로세스")

        assert state["discovery_trace"] is None
