"""존 역질문 공통 꼬리 테스트 (plans/169 O6).

`zone_clarification_for_targets`의 판정 사슬(서버 식별 → 조회 대상 → 전부 폴스타 →
권한 거른 존 → 페이로드)을 직접 고정하고, 2단·3단 후단 게이트가 같은 입력에
같은 페이로드를 내는지(앞단 비발동 신호를 통과한 경우) 대조한다. 전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from src.routing.zone_clarification import zone_clarification_for_targets

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_POLESTAR = set(_ACTIVE)
_QUERY = "OS 종류, OS 버전 및 OS패치버전을 확인하시오"
_PARSED = {"query_targets": ["os_type"], "filter_conditions": []}


def _config(active=None, exclusive=True):
    active = _ACTIVE if active is None else active
    return SimpleNamespace(
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: active, zone_group_exclusive=exclusive,
        ),
        get_polestar_db_ids=lambda: _POLESTAR,
    )


def _targets(*db_ids):
    return [{"db_id": d} for d in db_ids]


def _call(parsed=None, targets=None, *, allowed=None, role=None, cfg=None):
    return zone_clarification_for_targets(
        _PARSED if parsed is None else parsed,
        _targets(*_ACTIVE) if targets is None else targets,
        _QUERY,
        allowed_db_ids=allowed,
        user_role=role,
        app_config=cfg or _config(),
    )


class TestCommonTail:
    """공통 꼬리 단독 판정."""

    def test_triggers(self):
        payload = _call()
        assert payload is not None and payload["kind"] == "zone_select"
        assert payload["original_query"] == _QUERY

    def test_host_identifier_skips(self):
        parsed = {
            "query_targets": ["os_type"],
            "filter_conditions": [{"field": "hostname", "op": "=", "value": "web01"}],
        }
        assert _call(parsed=parsed) is None

    def test_no_query_targets_skips(self):
        assert _call(parsed={"query_targets": []}) is None

    def test_empty_targets_skips(self):
        assert _call(targets=[]) is None

    def test_non_polestar_target_skips(self):
        assert _call(targets=_targets("polestar_b0", "itam")) is None

    def test_no_authorized_zone_skips(self):
        assert _call(allowed=[]) is None

    def test_authorized_subset_limits_options(self):
        payload = _call(allowed=["polestar_cm_gp"])
        assert payload is not None
        assert {o["db_id"] for o in payload["options"]} == {"polestar_cm_gp"}

    def test_admin_ignores_allowed_list(self):
        assert _call(allowed=[], role="admin") is not None


class TestTierParity:
    """앞단 비발동 신호를 통과한 같은 입력 → 2단·3단 같은 페이로드."""

    @pytest.mark.parametrize(
        "allowed", [None, ["polestar_b0"], ["polestar_cm_gp", "polestar_cm_yd"]],
    )
    @pytest.mark.parametrize("exclusive", [True, False])
    def test_same_payload(self, allowed, exclusive):
        from src.orchestration.db_targeting import _zone_clarification_or_none_task
        from src.routing.semantic_router import _zone_clarification_or_none_router

        cfg = _config(exclusive=exclusive)
        targets = _targets(*_ACTIVE)
        task_payload = _zone_clarification_or_none_task(
            {"task_id": "t1", "agent": "data_query", "sub_query": "OS 종류 확인"},
            {
                "zone_clarification_allowed": True,
                "conversation_context": None,
                "original_user_query": _QUERY,
                "parsed_requirements": _PARSED,
                "allowed_db_ids": allowed,
            },
            targets, db_pinned=False, db_succeeded=False, app_config=cfg,
        )
        router_payload = _zone_clarification_or_none_router(
            {
                "zone_clarification_allowed": True,
                "conversation_context": None,
                "user_query": _QUERY,
                "parsed_requirements": _PARSED,
                "allowed_db_ids": allowed,
            },
            targets, None, cfg,
        )
        assert task_payload is not None
        assert task_payload == router_payload == _call(targets=targets, allowed=allowed, cfg=cfg)

    def test_task_fire_log_kept_in_db_targeting(self, caplog):
        """2단 발동 로그는 호출부 로거(종전 이름) 그대로 남는다."""
        from src.orchestration.db_targeting import _zone_clarification_or_none_task

        with caplog.at_level(logging.INFO, logger="src.orchestration.subagents"):
            payload = _zone_clarification_or_none_task(
                {"task_id": "t1", "agent": "data_query", "sub_query": "OS 종류 확인"},
                {
                    "zone_clarification_allowed": True,
                    "original_user_query": _QUERY,
                    "parsed_requirements": _PARSED,
                },
                _targets(*_ACTIVE), db_pinned=False, db_succeeded=False,
                app_config=_config(),
            )
        assert payload is not None
        recs = [r for r in caplog.records if "존 역질문 후단 게이트 발동" in r.getMessage()]
        assert len(recs) == 1
        assert recs[0].name == "src.orchestration.subagents"
        assert str(_ACTIVE) in recs[0].getMessage()
