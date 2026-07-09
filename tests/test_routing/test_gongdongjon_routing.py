"""바(bare) "공동존" 위치의 DB 라우팅 회귀 테스트 (D-065).

버그(2026-07-09): "공동존 전체 서버" 폼필이 gp/yd가 아닌 polestar_b0로 라우팅됨.
원인: "공동존"이 target_db_hints로 추출되지 않아 priority_db_ids가 비고, 폼필 DB 선택이
active_db_ids 순서(b0 우선)로 잘못 확정.

수정: (1) gp/yd aliases에 "공동존" 추가 → priority 해소, (2) input_parser 결정적 가드로
"공동존"을 target_db_hints에 보강.
"""

from src.nodes.field_mapper import _resolve_priority_db_ids
from src.nodes.input_parser import _ensure_location_hints

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "cloud_portal", "itsm", "itam"]


class TestGongdongjonPriorityResolution:
    """aliases 기반 priority 해소가 공동존→[gp,yd]를 내는지, 복합어 구분이 유지되는지."""

    def test_bare_gongdongjon_maps_to_gp_and_yd(self):
        assert _resolve_priority_db_ids(["공동존"], _ACTIVE) == [
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]

    def test_gongdongjon_does_not_include_b0(self):
        assert "polestar_b0" not in _resolve_priority_db_ids(["공동존"], _ACTIVE)

    def test_compound_gongdongjon_gimpo_narrows_to_gp(self):
        """전용 분기가 아니라 aliases+배제 로직이라 '공동존 김포'는 gp만 나온다."""
        assert _resolve_priority_db_ids(["공동존 김포"], _ACTIVE) == ["polestar_cm_gp"]

    def test_compound_gongdongjon_yeouido_narrows_to_yd(self):
        assert _resolve_priority_db_ids(["공동존 여의도"], _ACTIVE) == ["polestar_cm_yd"]

    def test_bank_still_maps_to_b0(self):
        assert _resolve_priority_db_ids(["은행"], _ACTIVE) == ["polestar_b0"]


class TestEnsureLocationHints:
    """input_parser 결정적 가드가 원문 위치어를 target_db_hints에 보강하는지."""

    def test_adds_gongdongjon_when_missing(self):
        parsed = {"target_db_hints": []}
        out = _ensure_location_hints(parsed, "공동존 전체 서버 메트릭 조회")
        assert "공동존" in out["target_db_hints"]

    def test_does_not_duplicate_when_more_specific_present(self):
        """이미 '공동존 김포'가 있으면 '공동존'을 중복 추가하지 않는다(부분문자열)."""
        parsed = {"target_db_hints": ["공동존 김포"]}
        out = _ensure_location_hints(parsed, "공동존 김포 서버")
        assert out["target_db_hints"] == ["공동존 김포"]

    def test_handles_missing_hints_key(self):
        parsed = {}
        out = _ensure_location_hints(parsed, "여의도 공동존 서버")
        assert "여의도" in out["target_db_hints"]
        assert "공동존" in out["target_db_hints"]

    def test_no_location_leaves_hints_untouched(self):
        parsed = {"target_db_hints": []}
        out = _ensure_location_hints(parsed, "CPU 80% 이상 서버")
        assert out["target_db_hints"] == []

    def test_bank_query_adds_bank_hint(self):
        parsed = {"target_db_hints": []}
        out = _ensure_location_hints(parsed, "은행 전체 서버 스펙")
        assert "은행" in out["target_db_hints"]
