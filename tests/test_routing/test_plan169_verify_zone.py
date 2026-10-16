"""plans/169 O6 독립 검증 — 존 역질문 공통 꼬리의 단 대칭·앞단 신호 비통일.

- 앞단 비발동 신호를 통과한 입력 조합(파싱·대상·권한·역할·상호배타·활성 DB) 전부에서
  2단 task 게이트 = 3단 router 게이트 = 공통 꼬리.
- 앞단 신호는 통일하지 않았다(166 §7 U-1 ①) — 2단 `_has_new_location_db_signal`은 DB명
  표면어로 비발동, 3단 `ZONE_SKIP_SIGNAL_TERMS`는 그 표면어를 모른다. 이 차이를 고치는 일은
  행동 변경(U-1 ②)이고, 그 계획이 이 단언을 함께 바꾼다.
전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

import itertools
import logging
from types import SimpleNamespace

import pytest

from src.orchestration.db_targeting import _zone_clarification_or_none_task
from src.routing.semantic_router import _zone_clarification_or_none_router
from src.routing.zone_clarification import zone_clarification_for_targets

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_PLAIN = "OS 버전 확인하시오"


def _cfg(active, polestar, exclusive):
    return SimpleNamespace(
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: active, zone_group_exclusive=exclusive,
        ),
        get_polestar_db_ids=lambda: polestar,
    )


_PARSED = {
    "ok": {"query_targets": ["os_type"], "filter_conditions": []},
    "host": {
        "query_targets": ["os_type"],
        "filter_conditions": [{"field": "hostname", "op": "=", "value": "web01"}],
    },
    "no_targets": {"query_targets": [], "filter_conditions": []},
    "none": None,
}
_TARGETS = {
    "all": [{"db_id": d} for d in _ACTIVE],
    "one": [{"db_id": "polestar_cm_gp"}],
    "mixed": [{"db_id": "polestar_cm_gp"}, {"db_id": "itam"}],
    "empty": [],
    "no_db_id": [{"name": "x"}],
}
_CFGS = {
    "excl": (_ACTIVE, set(_ACTIVE), True),
    "no_excl": (_ACTIVE, set(_ACTIVE), False),
    "no_active": ([], set(_ACTIVE), True),
    "no_polestar": (_ACTIVE, None, True),
}
_ALLOWED = {"none": None, "b0": ["polestar_b0"], "empty": [], "itam": ["itam"]}
_ROLES = {"none": None, "admin": "admin", "user": "user"}


def _task(query, parsed, targets, allowed, role, cfg):
    return _zone_clarification_or_none_task(
        {"task_id": "t", "agent": "data_query", "sub_query": query},
        {
            "zone_clarification_allowed": True,
            "original_user_query": query,
            "parsed_requirements": parsed,
            "allowed_db_ids": allowed,
            "user_role": role,
        },
        targets, db_pinned=False, db_succeeded=False, app_config=cfg,
    )


def _router(query, parsed, targets, allowed, role, cfg):
    return _zone_clarification_or_none_router(
        {
            "zone_clarification_allowed": True,
            "user_query": query,
            "parsed_requirements": parsed,
            "allowed_db_ids": allowed,
            "user_role": role,
        },
        targets, None, cfg,
    )


_MATRIX = list(itertools.product(_PARSED, _TARGETS, _CFGS, _ALLOWED, _ROLES))


@pytest.mark.parametrize("pk", list(_PARSED))
def test_tiers_equal_common_tail(pk):
    """파싱 축별로 나머지 축(대상·설정·권한·역할) 전 조합을 대조한다."""
    parsed = _PARSED[pk]
    for tk, ck, ak, rk in itertools.product(_TARGETS, _CFGS, _ALLOWED, _ROLES):
        targets, cfg = _TARGETS[tk], _cfg(*_CFGS[ck])
        allowed, role = _ALLOWED[ak], _ROLES[rk]
        common = zone_clarification_for_targets(
            parsed or {}, targets, _PLAIN,
            allowed_db_ids=allowed, user_role=role, app_config=cfg,
        )
        key = (pk, tk, ck, ak, rk)
        assert _task(_PLAIN, parsed, targets, allowed, role, cfg) == common, key
        assert _router(_PLAIN, parsed, targets, allowed, role, cfg) == common, key


def test_matrix_exercises_both_outcomes():
    """행렬이 발동·비발동을 모두 밟는다(전제 확인 — 전부 None이면 위 대조가 공허하다)."""
    fired = 0
    for pk, tk, ck, ak, rk in _MATRIX:
        if zone_clarification_for_targets(
            _PARSED[pk] or {}, _TARGETS[tk], _PLAIN,
            allowed_db_ids=_ALLOWED[ak], user_role=_ROLES[rk], app_config=_cfg(*_CFGS[ck]),
        ):
            fired += 1
    assert 0 < fired < len(_MATRIX)


def test_front_signal_not_unified_u1():
    """U-1 ①: DB명 표면어("itam")는 2단만 비발동시키고 3단은 공통 꼬리까지 간다."""
    q = "itam 서버 목록"
    cfg = _cfg(*_CFGS["excl"])
    args = (_PARSED["ok"], _TARGETS["all"], None, None, cfg)
    assert _task(q, *args) is None
    assert _router(q, *args) is not None


def test_router_emits_no_log_and_task_logs_once(caplog):
    """3단 게이트는 종전처럼 로그를 남기지 않고, 2단은 발동 로그 1줄(종전 로거)만 남긴다."""
    cfg = _cfg(*_CFGS["excl"])
    args = (_PARSED["ok"], _TARGETS["all"], None, None, cfg)
    with caplog.at_level(logging.DEBUG):
        assert _router(_PLAIN, *args) is not None
    assert [r for r in caplog.records if r.name.startswith("src.routing")] == []
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert _task(_PLAIN, *args) is not None
    recs = [r for r in caplog.records if r.name.startswith("src.")]
    assert [r.name for r in recs] == ["src.orchestration.subagents"]


@pytest.mark.parametrize("exclusive", [True, False])
def test_group_exclusive_flag_reaches_payload(exclusive):
    """존 그룹 상호배타 설정이 페이로드로 전달된다(공통 꼬리 — 양 단 공통)."""
    cfg = _cfg(_ACTIVE, set(_ACTIVE), exclusive)
    payload = zone_clarification_for_targets(
        _PARSED["ok"], _TARGETS["all"], _PLAIN,
        allowed_db_ids=None, user_role=None, app_config=cfg,
    )
    assert payload is not None
    assert payload.get("group_exclusive", False) is exclusive
