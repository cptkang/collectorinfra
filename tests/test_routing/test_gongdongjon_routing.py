"""바(bare) "공동존" 위치의 DB 라우팅 회귀 테스트 (D-065).

버그(2026-07-09): "공동존 전체 서버" 폼필이 gp/yd가 아닌 polestar_b0로 라우팅됨.
원인: "공동존"이 target_db_hints로 추출되지 않아 priority_db_ids가 비고, 폼필 DB 선택이
active_db_ids 순서(b0 우선)로 잘못 확정.

수정: (1) gp/yd aliases에 "공동존" 추가 → priority 해소, (2) input_parser 결정적 가드로
"공동존"을 target_db_hints에 보강.
"""

from src.document.field_mapper import MappingResult
from src.nodes.field_mapper import (
    _replicate_mapping_for_multi_location,
    _resolve_priority_db_ids,
)
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


class TestGenericProductTokenDoesNotPullB0:
    """지역 명시 + 제품명 단독("폴스타") 조합이 b0를 끌어들이지 않는지 (D-065 후속).

    버그(2026-07-09 재발): "공동존 폴스타의 모든 서버"에서 LLM이 target_db_hints에 bare "폴스타"를
    넣으면, "폴스타"가 b0 alias "은행 폴스타"에 부분매칭돼 b0가 priority에 들어가고 active 순서상
    b0가 우선 선택됐다. 지역 토큰이 있으면 제품명 단독 토큰을 제거해 방지한다.
    """

    def test_gongdongjon_plus_bare_polestar_excludes_b0(self):
        assert _resolve_priority_db_ids(["공동존", "폴스타"], _ACTIVE) == [
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]

    def test_gongdongjon_polestar_compound_excludes_b0(self):
        assert _resolve_priority_db_ids(["공동존 폴스타"], _ACTIVE) == [
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]

    def test_gongdongjon_gimpo_plus_polestar_narrows_to_gp(self):
        assert _resolve_priority_db_ids(["공동존 김포", "폴스타"], _ACTIVE) == ["polestar_cm_gp"]

    def test_bank_plus_polestar_still_b0(self):
        """은행(지역) + 폴스타 → b0 유지(지역이 은행이므로)."""
        assert _resolve_priority_db_ids(["은행", "폴스타"], _ACTIVE) == ["polestar_b0"]

    def test_bare_polestar_without_region_unchanged(self):
        """지역 없는 순수 '폴스타'는 종전대로 전체 폴스타 후보(변별 불가라 좁히지 않음)."""
        assert _resolve_priority_db_ids(["폴스타"], _ACTIVE) == [
            "polestar_b0",
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]


class TestCrossZoneMultiHint:
    """서로 다른 존을 지목하는 다중 hint가 각 DB를 모두 선택하는지 (D-065 후속2).

    버그(2026-07-13): "은행 폴스타와 공동존 김포 폴스타의 모든 서버" 폼필에서
    target_db_hints=["은행 폴스타", "공동존 김포 폴스타"]로 잘 파싱됐으나 결과에 은행존(b0)만
    포함되고 공동존 김포(gp)가 누락. 원인: 지역 배제 로직이 전체 hint를 훑어, gp는 "은행" hint에
    걸려 배제되고 b0는 "김포" hint에 걸려 배제 → priority가 비고 폴백으로 b0만 선택.
    수정: 배제를 hint 단위로 평가.
    """

    def test_bank_plus_gongdongjon_gimpo_selects_both(self):
        assert _resolve_priority_db_ids(
            ["은행 폴스타", "공동존 김포 폴스타"], _ACTIVE
        ) == ["polestar_b0", "polestar_cm_gp"]

    def test_order_independent(self):
        assert _resolve_priority_db_ids(
            ["공동존 김포 폴스타", "은행 폴스타"], _ACTIVE
        ) == ["polestar_b0", "polestar_cm_gp"]

    def test_bank_plus_gongdongjon_yeouido_selects_both(self):
        assert _resolve_priority_db_ids(
            ["은행 폴스타", "공동존 여의도 폴스타"], _ACTIVE
        ) == ["polestar_b0", "polestar_cm_yd"]

    def test_bank_plus_bare_gongdongjon_selects_all_three(self):
        assert _resolve_priority_db_ids(["은행", "공동존"], _ACTIVE) == [
            "polestar_b0",
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]


class TestBankZoneSurfaceForms:
    """"은행존" 계열 표면어가 b0로 해소되는지 (D-065 후속4, 2026-07-16).

    버그: "은행존과 공동존 김포 폴스타 …" / "공동존 여의도와 은행 …" 폼필에서 공동존 DB만
    선택되고 b0 누락. LLM(규칙 10)이 "은행존"/"은행존 폴스타" 형태로 hint를 내는데, b0
    aliases에는 "은행 폴스타"뿐이라 "은행존"(존이 붙은 토큰)이 어느 alias와도 부분매칭 안 됨.
    _ensure_location_hints도 "은행"이 "은행존" 문자열에 이미 포함돼 bare "은행"을 보강 안 함.
    수정: b0 aliases에 "은행"/"은행존"/"은행존 폴스타" 단독 등재(gp/yd "공동존" D-065와 동일 패턴).
    """

    def test_bare_bank_zone_maps_to_b0(self):
        assert _resolve_priority_db_ids(["은행존"], _ACTIVE) == ["polestar_b0"]

    def test_bank_zone_polestar_maps_to_b0(self):
        assert _resolve_priority_db_ids(["은행존 폴스타"], _ACTIVE) == ["polestar_b0"]

    def test_bank_zone_plus_gongdongjon_gimpo_selects_both(self):
        """테스트 1 재현: '은행존과 공동존 김포 폴스타의 모든 서버' → b0+gp."""
        assert _resolve_priority_db_ids(["은행존", "공동존 김포 폴스타"], _ACTIVE) == [
            "polestar_b0",
            "polestar_cm_gp",
        ]

    def test_gongdongjon_yeouido_plus_bank_zone_selects_both(self):
        """테스트 2 재현: '공동존 여의도와 은행의 모든 서버' → yd+b0."""
        assert _resolve_priority_db_ids(["공동존 여의도", "은행존"], _ACTIVE) == [
            "polestar_b0",
            "polestar_cm_yd",
        ]

    def test_polestar_compound_forms_select_both(self):
        assert _resolve_priority_db_ids(
            ["공동존 여의도 폴스타", "은행존 폴스타"], _ACTIVE
        ) == ["polestar_b0", "polestar_cm_yd"]

    def test_bank_zone_does_not_pull_gongdongjon(self):
        """"은행존" 단독은 gp/yd를 끌어들이지 않는다."""
        assert _resolve_priority_db_ids(["은행존"], _ACTIVE) == ["polestar_b0"]


class TestMultiLocationReplication:
    """공동존(gp+yd 다중 priority)일 때 매핑이 전 priority DB에 복제돼 둘 다 조회되는지."""

    def _make_result(self, mapping_by_db):
        r = MappingResult()
        r.db_column_mapping = {k: dict(v) for k, v in mapping_by_db.items()}
        r.mapped_db_ids = list(mapping_by_db.keys())
        return r

    def test_gongdongjon_replicates_to_gp_and_yd(self):
        """gp에만 매핑됐어도 priority=[gp,yd]면 yd에도 복제되고 mapped_db_ids=[gp,yd]."""
        result = self._make_result({"polestar_cm_gp": {"서버명": "cmm_resource.name"}})
        _replicate_mapping_for_multi_location(
            result, ["polestar_cm_gp", "polestar_cm_yd"]
        )
        assert result.mapped_db_ids == ["polestar_cm_gp", "polestar_cm_yd"]
        assert result.db_column_mapping["polestar_cm_yd"] == {"서버명": "cmm_resource.name"}
        assert result.db_column_mapping["polestar_cm_gp"] == {"서버명": "cmm_resource.name"}

    def test_single_location_unchanged(self):
        """단일 위치(priority 1개)면 복제 안 함."""
        result = self._make_result({"polestar_cm_gp": {"서버명": "cmm_resource.name"}})
        _replicate_mapping_for_multi_location(result, ["polestar_cm_gp"])
        assert result.mapped_db_ids == ["polestar_cm_gp"]
        assert "polestar_cm_yd" not in result.db_column_mapping

    def test_empty_mapping_noop(self):
        """매핑이 비면(전부 미매핑) 아무 것도 안 함."""
        result = self._make_result({})
        _replicate_mapping_for_multi_location(
            result, ["polestar_cm_gp", "polestar_cm_yd"]
        )
        assert result.db_column_mapping == {}


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


class TestMultiRegionSingleHint:
    """복수 지역이 한 hint에 든 경우의 상호 배제 전멸 방지 (2026-07-29 라이브 실측 FIX-14).

    버그: "공동존 김포/여의도 서버에 대해 양식 채워라" → LLM이 hint를 ["공동존 김포/여의도"]
    한 덩어리로 추출 → hint 단위 배제에서 gp는 '여의도'에, yd는 '김포'에, b0는 둘 다에
    걸려 전 DB 배제 → 빈 priority → 폴백이 은행존(b0)으로 오판. 지역별 분해로 교정.
    """

    def test_slash_joined_regions_resolve_to_gp_and_yd(self):
        assert _resolve_priority_db_ids(["공동존 김포/여의도"], _ACTIVE) == [
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]

    def test_slash_joined_without_gongdongjon(self):
        assert _resolve_priority_db_ids(["김포/여의도"], _ACTIVE) == [
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]

    def test_conjunction_joined_regions(self):
        assert _resolve_priority_db_ids(["김포와 여의도"], _ACTIVE) == [
            "polestar_cm_gp",
            "polestar_cm_yd",
        ]

    def test_region_with_bank_in_one_hint(self):
        """'은행존과 김포' 같은 조합도 각 존으로 분해된다."""
        assert _resolve_priority_db_ids(["은행존과 김포"], _ACTIVE) == [
            "polestar_b0",
            "polestar_cm_gp",
        ]

    def test_single_region_hint_unchanged(self):
        """지역이 하나뿐인 hint는 분해 없이 기존 동작 유지."""
        assert _resolve_priority_db_ids(["공동존 김포"], _ACTIVE) == ["polestar_cm_gp"]
        assert _resolve_priority_db_ids(["은행"], _ACTIVE) == ["polestar_b0"]
