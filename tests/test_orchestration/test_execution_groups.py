"""실행 그룹 축·분할기 테스트 (D-176 · plans/82 §4.2 · SPEC-group-registry).

이 모듈은 **소비처를 만들지 않는다** — 축을 선언하고 분할기를 노출만 하므로
런타임 동작 변화가 0이어야 한다. 기존 존 게이트 테스트가 그린으로 남는 것이 그 증거다.

전부 mock/선언 검사 — LLM·네트워크·DB 미사용(D-127).
"""

from __future__ import annotations

import pytest

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"


class TestRegistrySolutionAxis:
    """`solutions` 선언과 파생 API — 정본은 YAML."""

    @pytest.fixture(scope="class")
    def reg(self):
        from src.routing.registry import get_registry

        return get_registry()

    def test_polestar_solution_declared(self, reg):
        sols = {s.code: s for s in reg.solutions()}
        assert "polestar" in sols
        assert sols["polestar"].order == 10
        assert sols["polestar"].backend == "sql"

    def test_only_polestar_registered(self, reg):
        """apm·dpm은 주석 예시로만 — 등록 0건에서 회귀 0을 단언한다."""
        assert [s.code for s in reg.solutions()] == ["polestar"]

    def test_solution_capabilities_include_host_location(self, reg):
        """탐색(host_location)은 폴스타가 제공한다 — 2차 모듈의 전제."""
        caps = {s.code: s.capabilities for s in reg.solutions()}
        assert "host_location" in caps["polestar"]

    def test_capability_providers_derived(self, reg):
        assert reg.capability_providers("host_location") == ("polestar",)
        assert reg.capability_providers("was_metric") == ()


class TestRegistryZoneGroupAxis:
    """`zone_groups` 선언 — `zones` 순서와 **별도 축**이다."""

    @pytest.fixture(scope="class")
    def reg(self):
        from src.routing.registry import get_registry

        return get_registry()

    def test_zone_groups_sorted_by_query_order(self, reg):
        """사용자 요구: 은행존을 먼저 조회한다. 순서 정본은 query_order다."""
        assert [g.code for g in reg.zone_groups()] == ["bank", "common"]

    def test_query_order_values(self, reg):
        by = {g.code: g for g in reg.zone_groups()}
        assert by["bank"].query_order == 10
        assert by["common"].query_order == 20

    def test_zone_group_labels(self, reg):
        by = {g.code: g.label for g in reg.zone_groups()}
        assert by == {"bank": "은행존", "common": "공동존"}

    def test_zones_declaration_order_unchanged(self, reg):
        """`zones` 선언 순서는 알림 RBAC 선택지 순서다 — 건드리지 않았다."""
        assert reg.zone_codes() == ("gongjon", "bankjon")

    def test_zone_group_of_db_id(self, reg):
        assert reg.zone_group_of(_B0) == "bank"
        assert reg.zone_group_of(_GP) == "common"
        assert reg.zone_group_of(_YD) == "common"
        assert reg.zone_group_of("unknown_db") is None


class TestPartitionExecutionGroups:
    """db_id 목록 → 순서 있는 실행 그룹."""

    def _part(self, ids):
        from src.routing.execution_groups import partition_execution_groups

        return partition_execution_groups(ids)

    def test_three_zones_split_into_two_groups_bank_first(self):
        groups = self._part([_B0, _GP, _YD])
        assert [g["group_key"] for g in groups] == ["polestar:bank", "polestar:common"]
        assert groups[0]["db_ids"] == [_B0]
        assert groups[1]["db_ids"] == [_GP, _YD]

    def test_input_order_does_not_matter(self):
        """입력 순서에 의존하지 않는다 — 순서 정본은 레지스트리다(D-035)."""
        a = self._part([_YD, _GP, _B0])
        b = self._part([_B0, _GP, _YD])
        assert [g["group_key"] for g in a] == [g["group_key"] for g in b]
        assert a[1]["db_ids"] == b[1]["db_ids"] == [_GP, _YD]

    def test_common_only(self):
        groups = self._part([_GP, _YD])
        assert [g["group_key"] for g in groups] == ["polestar:common"]

    def test_bank_only(self):
        groups = self._part([_B0])
        assert [g["group_key"] for g in groups] == ["polestar:bank"]

    @pytest.mark.parametrize("ids", [[], None])
    def test_empty(self, ids):
        assert self._part(ids) == []

    def test_unregistered_ids_ignored(self):
        """미등재 db_id는 판정에서 무시한다(기존 mixed_zone_groups 규약과 동일)."""
        groups = self._part([_B0, "nope"])
        assert [g["group_key"] for g in groups] == ["polestar:bank"]
        assert groups[0]["db_ids"] == [_B0]

    def test_only_unregistered(self):
        assert self._part(["nope", "nada"]) == []

    def test_group_shape(self):
        """§4.1 개념 모델의 필수 키를 갖는다."""
        g = self._part([_B0])[0]
        for key in ("group_key", "solution", "zone_group", "label", "db_ids",
                    "backend", "order", "kind"):
            assert key in g, key
        assert g["kind"] == "peer"
        assert g["backend"] == "sql"

    def test_duplicate_ids_deduped(self):
        groups = self._part([_GP, _GP, _YD])
        assert groups[0]["db_ids"] == [_GP, _YD]


class TestZoneClarifyOptionsUnchanged:
    """`ZONE_CLARIFY_OPTIONS`를 레지스트리 파생으로 바꿔도 **값은 현행과 동일**해야 한다."""

    def test_golden(self):
        from src.utils.query_gen_common import ZONE_CLARIFY_OPTIONS

        assert ZONE_CLARIFY_OPTIONS == (
            {"db_id": "polestar_b0", "label": "은행존", "group": "bank"},
            {"db_id": "polestar_cm_gp", "label": "공동존 김포", "group": "common"},
            {"db_id": "polestar_cm_yd", "label": "공동존 여의도", "group": "common"},
        )

    def test_zone_group_by_db_unchanged(self):
        from src.utils.query_gen_common import _ZONE_GROUP_BY_DB

        assert _ZONE_GROUP_BY_DB == {
            "polestar_b0": "bank",
            "polestar_cm_gp": "common",
            "polestar_cm_yd": "common",
        }

    def test_mixed_zone_groups_still_works(self):
        """기존 상호배타 판정이 그대로 동작한다(게이트 미변경)."""
        from src.utils.query_gen_common import mixed_zone_groups

        assert mixed_zone_groups([_B0, _GP]) is True
        assert mixed_zone_groups([_GP, _YD]) is False

    def test_group_values_match_registry(self):
        """리터럴과 레지스트리 파생값이 **어긋나지 않는다**(드리프트 가드).

        `ZONE_CLARIFY_OPTIONS`를 임포트 시점 파생으로 만들면 순환 임포트가 된다
        (`src/routing/__init__.py` → `semantic_router` → `query_gen_common`, 실측
        2026-08-28). 그래서 값은 리터럴로 두고 정합성은 이 테스트가 지킨다 —
        레지스트리 `zone_groups`를 고치고 여기를 안 고치면 실패한다.
        """
        from src.routing.registry import get_registry
        from src.utils.query_gen_common import ZONE_CLARIFY_OPTIONS

        reg = get_registry()
        for opt in ZONE_CLARIFY_OPTIONS:
            assert opt["group"] == reg.zone_group_of(opt["db_id"]), opt["db_id"]
