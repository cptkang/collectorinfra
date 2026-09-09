"""스레드 DB 스코프 단일 출처 · 축 구조 보고 (plans/90 · D-205 · SPEC-thread-db-scope §Success 1~3).

★ 두 계약을 못박는다:
  ① `resolve_thread_db_ids(state_N)` == 턴 N+1 `context_resolver`가 읽을 `previous_db_ids`
     — 칩이 보여주는 값과 실제 승계값이 어긋나면 침묵 강등의 새 형태다.
  ② 축 판정은 레지스트리 접근자로만 — db_id 리터럴을 이 테스트 밖(구현)에 두지 않는다.
LLM·DB 0.
"""

from __future__ import annotations

import pytest

from src.nodes.context_resolver import _extract_previous_db_ids
from src.routing.db_scope import (
    SOURCE_CLASSIFIED,
    SOURCE_INHERITED,
    SOURCE_NONE,
    SOURCE_SELECTED,
    build_db_scope,
    extract_state_db_ids,
    resolve_thread_db_ids,
    scope_axes_options,
)
from src.routing.registry import get_registry

B0, GP, YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
ACTIVE = [B0, GP, YD]


class TestSingleSource:
    def test_context_resolver_delegates_to_same_function(self):
        state = {"target_databases": [{"db_id": GP}], "active_db_id": GP, "mapped_db_ids": [B0]}
        assert _extract_previous_db_ids(state) == extract_state_db_ids(state) == [GP, B0]

    def test_sticky_fallback_matches_context_resolver_rule(self):
        """이번 턴 대상이 없으면 직전 ctx를 승계한다 — context_resolver.py의 `or prior_ctx` 규칙과 동일."""
        state = {"target_databases": [], "active_db_id": None,
                 "conversation_context": {"previous_db_ids": [B0]}}
        assert resolve_thread_db_ids(state) == [B0]

    def test_default_is_excluded(self):
        assert extract_state_db_ids({"target_databases": [{"db_id": "default"}], "active_db_id": "default"}) == []


class TestAxes:
    def test_zone_group_from_registry(self):
        scope = build_db_scope({"target_databases": [{"db_id": B0}], "active_db_id": B0})
        reg = get_registry()
        assert scope["zone_group"]["code"] == reg.zone_group_of(B0)
        assert scope["zone_group"]["label"] == next(
            g.label for g in reg.zone_groups() if g.code == reg.zone_group_of(B0)
        )
        assert scope["zone_group"]["db_ids"] == [B0]

    def test_common_group_holds_both_dbs(self):
        scope = build_db_scope({"target_databases": [{"db_id": GP}, {"db_id": YD}]})
        assert scope["zone_group"]["db_ids"] == [GP, YD]
        assert scope["db_ids"] == [GP, YD]

    def test_solutions_axis_from_family(self):
        scope = build_db_scope({"active_db_id": B0})
        assert [s["code"] for s in scope["solutions"]] == ["polestar"]
        assert scope["solutions"][0]["db_ids"] == [B0]

    def test_unregistered_db_stays_in_db_ids_only(self):
        """미등록 db_id는 축에 못 올라가도 승계 집합(db_ids)에는 남는다 — 정보 손실 없음."""
        scope = build_db_scope({"active_db_id": "ghost_db"})
        assert scope["db_ids"] == ["ghost_db"]
        assert scope["zone_group"] is None
        assert scope["solutions"] == []


class TestSource:
    def test_none_when_nothing(self):
        scope = build_db_scope({})
        assert scope == {"zone_group": None, "zone_groups": [], "solutions": [], "db_ids": [], "source": SOURCE_NONE}

    def test_selected_wins(self):
        state = {"active_db_id": B0, "db_scope_source": "classified"}
        assert build_db_scope(state, selected_db_ids=[B0])["source"] == SOURCE_SELECTED
        assert build_db_scope({**state, "selected_db_ids": [B0]})["source"] == SOURCE_SELECTED

    def test_structured_source_is_reported(self):
        for src in ("hint", "inherited", "planned", "classified"):
            assert build_db_scope({"active_db_id": B0, "db_scope_source": src})["source"] == src

    def test_targets_without_signal_is_classified(self):
        assert build_db_scope({"active_db_id": B0})["source"] == SOURCE_CLASSIFIED

    def test_sticky_only_is_inherited(self):
        """이번 턴 대상 없이 직전 ctx만 남은 턴(분석 턴 등) — 승계 중임을 알린다."""
        state = {"conversation_context": {"previous_db_ids": [B0]}}
        assert build_db_scope(state)["source"] == SOURCE_INHERITED


class TestOptions:
    def test_zone_axis_db_granularity(self):
        payload = scope_axes_options(ACTIVE)
        assert [a["axis"] for a in payload["axes"]] == ["zone_group"]
        axis = payload["axes"][0]
        assert axis["exclusive"] is True
        assert [o["key"] for o in axis["options"]] == ACTIVE
        assert all(o["db_ids"] == [o["key"]] for o in axis["options"])

    def test_groups_match_registry(self):
        """옵션의 group은 레지스트리 존 그룹과 같아야 한다(리터럴 사본 동기 — 기존 정합 테스트 보강)."""
        reg = get_registry()
        for o in scope_axes_options(ACTIVE)["axes"][0]["options"]:
            assert o["group"] == reg.zone_group_of(o["key"])

    def test_inactive_and_unauthorized_excluded(self):
        opts = scope_axes_options([B0, GP], allowed_db_ids=[GP])["axes"][0]["options"]
        assert [o["key"] for o in opts] == [GP]

    def test_exclusive_flag_passthrough(self):
        assert scope_axes_options(ACTIVE, group_exclusive=False)["axes"][0]["exclusive"] is False


class TestZoneGroupsPlural:
    """D-206: 은행존+공동존을 함께 보는 스레드는 zone_groups에 두 그룹이 조회 순서(query_order)로 실린다."""

    def test_mixed_scope_reports_both_groups_in_query_order(self):
        scope = build_db_scope({"target_databases": [{"db_id": YD}, {"db_id": B0}, {"db_id": GP}]})
        assert [g["code"] for g in scope["zone_groups"]] == ["bank", "common"]
        assert scope["zone_groups"][1]["db_ids"] == [GP, YD]
        assert scope["zone_group"]["code"] == "bank"   # 첫 그룹(호환)

    def test_single_group_has_one_entry(self):
        scope = build_db_scope({"active_db_id": B0})
        assert len(scope["zone_groups"]) == 1 and scope["zone_groups"][0] == scope["zone_group"]

    def test_none_has_empty_list(self):
        assert build_db_scope({})["zone_groups"] == []
