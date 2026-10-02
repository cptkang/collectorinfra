"""존 순회 결과 판정 (Plan 82 W5-T1 · SPEC-host-discovery).

★ 이 모듈이 지키는 핵심 구분은 **"없다"와 "확인하지 못했다"** 다. 둘을 합치면 사용자는
*다 찾아봤는데 없다* 로 읽고 서버명을 의심하게 된다 — 실제로는 조회가 실패한 것이다.

순수 함수만 다루므로 LLM·DB·네트워크 0(D-127).
"""

from __future__ import annotations

import pytest

from src.domain.host_availability import judge_availability
from src.domain.host_discovery import (
    AMBIGUOUS,
    NOT_FOUND,
    RESOLVED,
    SweepOutcome,
    ZoneHit,
    candidate_db_ids,
    classify,
    render_ambiguous,
    render_not_found,
    trace_payload,
)

BANK = ZoneHit(db_id="polestar_b0", zone_label="은행존", hostname="abd00.kb", server_name="ABD00")
GP = ZoneHit(db_id="polestar_cm_gp", zone_label="공동존 김포", hostname="abd00.gp")
YD = ZoneHit(db_id="polestar_cm_yd", zone_label="공동존 여의도", hostname="abd00.yd")

ALL_ZONES = ("은행존", "공동존 김포", "공동존 여의도")


class TestClassify:
    def test_single_hit_resolves(self):
        verdict = classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES, hits=(GP,)))

        assert verdict.state == RESOLVED
        assert verdict.db_id == "polestar_cm_gp"
        assert verdict.candidates == ()

    def test_multiple_hits_are_ambiguous(self):
        verdict = classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES, hits=(BANK, GP)))

        assert verdict.state == AMBIGUOUS
        assert verdict.db_id is None, "임의 선택 금지 — 고르는 것은 사용자다"
        assert len(verdict.candidates) == 2

    def test_no_hit_is_not_found(self):
        verdict = classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES))

        assert verdict.state == NOT_FOUND
        assert verdict.hit is None

    def test_no_hit_with_errors_is_still_not_found_but_errors_survive(self):
        """판정은 NOT_FOUND지만 **사유는 보존**된다 — 문구가 둘을 나눠 쓴다."""
        outcome = SweepOutcome(
            identifier="abd00", swept=ALL_ZONES, errors={"공동존 김포": "연결 거부"},
        )

        verdict = classify(outcome)

        assert verdict.state == NOT_FOUND
        assert verdict.outcome.errors == {"공동존 김포": "연결 거부"}


class TestRenderNotFound:
    def test_lists_every_swept_zone(self):
        text = render_not_found(SweepOutcome(identifier="abd00", swept=ALL_ZONES))

        for label in ALL_ZONES:
            assert label in text
        assert "abd00" in text
        assert "3개 존" in text

    def test_failed_zones_are_named_as_unverified_not_absent(self):
        """★ 조회 실패 존을 '없음'에 섞지 않는다."""
        text = render_not_found(SweepOutcome(
            identifier="abd00", swept=ALL_ZONES, errors={"은행존": "타임아웃"},
        ))

        assert "조회 자체가 실패" in text
        assert "'없음'이 아닙니다" in text
        assert "은행존(타임아웃)" in text

    def test_clean_sweep_suggests_next_step(self):
        text = render_not_found(SweepOutcome(identifier="abd00", swept=ALL_ZONES))

        assert "철자" in text and "권한" in text

    def test_no_authorized_zone_is_stated(self):
        text = render_not_found(SweepOutcome(identifier="abd00", swept=()))

        assert "확인 가능한 존이 없습니다" in text


class TestRenderAmbiguous:
    def test_names_each_zone(self):
        verdict = classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES, hits=(BANK, GP)))

        text = render_ambiguous(verdict)

        assert "2개 존" in text
        assert "은행존" in text and "공동존 김포" in text
        assert "선택해 주세요" in text

    def test_candidates_are_narrowed_to_found_zones(self):
        """★ 전체 존이 아니라 **발견된 존**만 선택지가 된다(U5)."""
        verdict = classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES, hits=(BANK, GP)))

        assert candidate_db_ids(verdict) == ["polestar_b0", "polestar_cm_gp"]
        assert "polestar_cm_yd" not in candidate_db_ids(verdict)

    def test_candidate_ids_are_deduplicated(self):
        dup = ZoneHit(db_id="polestar_cm_gp", zone_label="공동존 김포", hostname="abd00-b")
        verdict = classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES, hits=(GP, dup)))

        assert candidate_db_ids(verdict) == ["polestar_cm_gp"]


class TestTracePayload:
    def test_payload_is_json_safe_and_records_the_break(self):
        outcome = SweepOutcome(
            identifier="abd00", swept=ALL_ZONES, hits=(GP,), errors={"은행존": "타임아웃"},
        )

        payload = trace_payload(classify(outcome))

        import json

        json.dumps(payload)  # 체크포인터 직렬화 가능해야 한다
        assert payload["state"] == RESOLVED
        assert payload["swept"] == list(ALL_ZONES)
        assert payload["errors"] == {"은행존": "타임아웃"}
        assert payload["hits"][0]["db_id"] == "polestar_cm_gp"


class TestAvailabilityCarried:
    def test_hit_can_carry_availability_without_extra_lookup(self):
        """가용성은 같은 행의 컬럼에서 온다 — DB 왕복이 늘지 않는다(D-175)."""
        hit = ZoneHit(
            db_id="polestar_cm_gp", zone_label="공동존 김포", hostname="abd00.gp",
            availability=judge_availability(found=True, avail_status=1),
        )

        verdict = classify(SweepOutcome(identifier="abd00", swept=ALL_ZONES, hits=(hit,)))

        assert verdict.hit.availability is not None
        assert verdict.hit.availability.state
