"""plans/132 v1.5 후속 ①② 독립 검증(verifier 1라운드).

① 라우트 존 게이트 위임(`_zone_gate_deferred_to_tasks`) — 실 레지스트리 시스템 묶음 · 시스템 선언
   없는 DB · 설정 대역(MagicMock) · 위임 뒤 task 게이트의 인가(D-232) 선택지.
② 사용량·사용 추이 판정(`utilization_kind`) 반례 · 안내 문구(D-264).

래더(`src.observability.ladder._resolution`)·활성 DB·비DB 엔드포인트는 테스트마다 명시 주입한다
(.env·다른 테스트의 build_graph 확정 누수 방지). 레지스트리는 실제 `config/db_registry.yaml`.
LLM·DB 0.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.observability.ladder as ladder
from src.api.routes.query import (
    _scope_select_or_none,
    _zone_clarification_or_none,
    _zone_gate_deferred_to_tasks,
)
from src.api.schemas import QueryRequest
from src.config import CompositeConfig, MultiDBConfig
from src.domain import knowledge_assets as ka
from src.routing.registry import get_registry
from src.routing.source_hints import utilization_notice_text

_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_Q_OS = "서버들의 OS 목록 보여줘"
_Q_FULL = "모든 서버의 OS 버전을 조회해줘"


def _config(active: list[str], *, apm: bool = False) -> SimpleNamespace:
    endpoints = {"apm": "http://127.0.0.1:9096/mcp"} if apm else {}
    return SimpleNamespace(
        composite=CompositeConfig(scope_select_enabled=True),
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(active), zone_group_exclusive=True),
        dbhub=SimpleNamespace(
            source_endpoint=lambda code: (endpoints[code], None) if code in endpoints else None
        ),
    )


@pytest.fixture
def tier(monkeypatch):
    def _set(value: str | None) -> None:
        monkeypatch.setattr(ladder, "_resolution", None if value is None else {
            "tier": value, "degraded_reason": "", "resolved_by": "explicit_env",
        })

    return _set


def _zone(query: str, cfg, user=None):
    return _zone_clarification_or_none(QueryRequest(query=query), None, cfg, user)


def _scope(query: str, cfg):
    return _scope_select_or_none(
        SimpleNamespace(query=query, selected_db_ids=None), None, cfg, None,
    )


# ── ① 시스템 묶음 실측 ────────────────────────────────────────────────────────


def test_real_registry_groups_zones_and_sandbox_into_one_system() -> None:
    reg = get_registry()
    assert {reg.system_of(d) for d in [*_ZONES, "polestar"]} == {"polestar"}


@pytest.mark.parametrize("active", [
    _ZONES,
    ["polestar", *_ZONES],          # 로컬 샌드박스 + 3존 동시 활성 — 여전히 한 시스템
    ["polestar"],
])
def test_single_polestar_system_not_deferred_and_bit_identical(tier, active) -> None:
    cfg = _config(active)
    tier(None)
    before = (_zone(_Q_OS, cfg), _scope(_Q_FULL, cfg))
    tier("intent_orchestration")
    assert _zone_gate_deferred_to_tasks(cfg, "존 게이트") is False
    assert (_zone(_Q_OS, cfg), _scope(_Q_FULL, cfg)) == before


@pytest.mark.parametrize(("extra", "deferred"), [
    ("itam", True), ("itsm", False), ("cloud_portal", False),
])
def test_other_db_system_counts_only_with_system_declaration(tier, extra, deferred) -> None:
    """선언 없는 DB(`system_of` None)는 세지 않는다 — 분류 경로 `_multiple_systems_active`와 같은
    기준(교정 Minor-2 · 팀 리드 결정). 폴스타와 함께여도 선언 있는 DB만 둘째 시스템이다."""
    tier("intent_orchestration")
    cfg = _config([*_ZONES, extra])
    assert _zone_gate_deferred_to_tasks(cfg, "존 게이트") is deferred
    assert (_zone(_Q_OS, cfg) is None) is deferred


def test_magicmock_config_is_not_deferred_and_does_not_raise(tier) -> None:
    """설정 대역(MagicMock)은 비DB 시스템 비활성으로 읽고 활성 DB가 없어 위임하지 않는다."""
    tier("intent_orchestration")
    assert _zone_gate_deferred_to_tasks(MagicMock(), "존 게이트") is False


def test_apm_inactive_without_endpoint(tier) -> None:
    tier("intent_orchestration")
    assert _zone_gate_deferred_to_tasks(_config(_ZONES, apm=False), "존 게이트") is False
    assert _zone_gate_deferred_to_tasks(_config(_ZONES, apm=True), "존 게이트") is True


# ── ① 위임 뒤 task 게이트의 인가(D-232) ──────────────────────────────────────


def _task_gate(targets: list[str], allowed: list[str] | None, role: str | None = None):
    from src.orchestration.subagents import _zone_clarification_or_none_task

    active = [*_ZONES, "itam"]
    cfg = SimpleNamespace(
        multi_db=SimpleNamespace(get_active_db_ids=lambda: list(active), zone_group_exclusive=True),
        get_polestar_db_ids=lambda: {*_ZONES, "polestar"},
    )
    isolated = {
        "zone_clarification_allowed": True, "conversation_context": None,
        "original_user_query": _Q_OS, "user_query": _Q_OS,
        "parsed_requirements": {"query_targets": ["os_type"], "filter_conditions": []},
        "allowed_db_ids": allowed, "user_role": role,
    }
    return _zone_clarification_or_none_task(
        {"task_id": "t1", "agent": "data_query", "sub_query": _Q_OS}, isolated,
        [{"db_id": d, "relevance_score": 0.9} for d in targets],
        db_pinned=False, db_succeeded=False, app_config=cfg,
    )


def test_task_gate_options_filtered_by_user_authorization() -> None:
    payload = _task_gate(_ZONES, ["polestar_cm_gp", "itam"])
    assert payload is not None
    assert [o["db_id"] for o in payload["options"]] == ["polestar_cm_gp"]


def test_task_gate_never_offers_non_zone_db_as_option() -> None:
    payload = _task_gate(_ZONES, None)
    assert payload is not None
    assert "itam" not in {o["db_id"] for o in payload["options"]}


def test_task_gate_silent_when_user_has_no_zone() -> None:
    assert _task_gate(_ZONES, ["itam"]) is None


def test_task_gate_admin_sees_all_zones() -> None:
    payload = _task_gate(_ZONES, [], role="admin")
    assert payload is not None
    assert {o["db_id"] for o in payload["options"]} == set(_ZONES)


# ── ② 사용량(현재값)·사용 추이 반례 ───────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "DRAM",
    "부하 분산 장비",
    "서버 부하",
    "메모리 용량",
    "CPU 코어 수",
    "서버 사양",
    "라이선스 사용 현황",
    "프로그램 사용량",
    "사용량",
    "스토리지 사용량 추이",
    "메모리 증설 추이",
    "디스크 교체 변화",
    "램 증설 추이",
])
def test_not_utilization(text: str) -> None:
    assert ka.utilization_kind(text) is None


@pytest.mark.parametrize("text, kind", [
    ("디스크 사용률 변화", "trend"),
    ("서버 메모리 사용량 변화", "trend"),
    ("CPU 사용량, 메모리 추이", "trend"),
    ("디스크 사용량 증가", "current"),
    ("CPU 사용률 높은 서버 담당자", "current"),
    ("파일시스템 사용률", "current"),
])
def test_utilization_kind_examples(text: str, kind: str) -> None:
    assert ka.utilization_kind(text) == kind


def test_current_value_with_unrelated_change_word_is_current() -> None:
    assert ka.utilization_kind("CPU 사용률 상위 서버의 담당자 변화") == "current"


def test_particle_between_resource_and_trend() -> None:
    assert ka.utilization_kind("CPU의 사용 추이") == "trend"


@pytest.mark.parametrize("text", [
    "CPU 사용률 일별 추이",
    "CPU 사용률 월별 변화",
    "메모리 사용률의 일주일 추이",
    "CPU 사용률이 어떻게 변화했나",
])
def test_trend_with_period_modifier_between(text: str) -> None:
    assert ka.utilization_kind(text) == "trend"


@pytest.mark.xfail(strict=True, reason="한계 보고: 영문 usage 미탐(종전부터)")
def test_english_usage() -> None:
    assert ka.utilization_kind("cpu usage trend") == "trend"


# ── ② 안내 문구(D-264) ───────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", [None, "current", "trend"])
@pytest.mark.parametrize("hints", [["자산관리"], ["자산관리", "자산관리"], []])
def test_notice_uses_user_wording_only(kind, hints) -> None:
    text = utilization_notice_text(hints, kind=kind)
    assert "ITAM" not in text and "itam" not in text
    for spec in get_registry().solutions():
        assert spec.code not in text.lower() or spec.code in "".join(hints).lower()
    if hints:
        assert text.count("「자산관리」") == 2   # 중복 지목은 한 번만 이름에 싣는다(문장 2곳)
    if kind == "trend":
        assert "사용 추이" in text and "사용량·사용률" not in text
    else:
        assert "사용량·사용률" in text and "사용 추이" not in text
