"""존 역질문 선택지를 사용자 조회 권한으로 거른다 (plans/116 §10.3 · D-232).

종전에는 선택지를 활성 DB 전체로 만들어, 샌드박스 권한만 있는 사용자에게도 은행존·공동존을
물었다. 규칙: 권한 내 존만 선택지로 · 권한 내 존이 0이면 묻지 않는다(존 전부 비활성과 같은
처리) · 1이상이면 종전처럼 묻는다 · 관리자와 `allowed_db_ids=None`은 전체.
LLM·네트워크 미사용.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes.query import (
    _file_zone_clarification_or_none,
    _unregistered_zone_clarification_or_none,
    _zone_clarification_or_none,
    _zone_group_exclusive_or_none,
)
from src.api.schemas import QueryRequest

_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_ACTIVE = ["polestar", *_ZONES]          # 샌드박스(존 미배정) + 존 3종
_MASS = "모든 서버들의 OS 종류를 확인해줘"


def _config(active=_ACTIVE):
    return SimpleNamespace(
        multi_db=SimpleNamespace(get_active_db_ids=lambda: active, zone_group_exclusive=False),
        get_polestar_db_ids=lambda: set(active),
    )


def _user(allowed, role="user"):
    return {"sub": "u1", "role": role, "allowed_db_ids": allowed}


def _ids(payload):
    return [o["db_id"] for o in payload["options"]]


class TestTextPreGate:
    def test_sandbox_only_user_is_not_asked(self):
        assert _zone_clarification_or_none(
            QueryRequest(query=_MASS), None, _config(), _user(["polestar"])
        ) is None

    def test_empty_allowed_list_is_not_asked(self):
        """`[]`=조회 가능 DB 없음 — 빈 목록이 "제한 없음"으로 읽혀 전 존이 나오면 안 된다."""
        assert _zone_clarification_or_none(
            QueryRequest(query=_MASS), None, _config(), _user([])
        ) is None

    def test_options_narrowed_to_authorized_zones(self):
        clar = _zone_clarification_or_none(
            QueryRequest(query=_MASS), None, _config(),
            _user(["polestar", "polestar_cm_gp", "polestar_cm_yd"]),
        )
        assert _ids(clar) == ["polestar_cm_gp", "polestar_cm_yd"]

    def test_single_authorized_zone_still_asked(self):
        """선택지 수 규칙은 종전과 같다 — 활성 존 1개일 때도 묻던 동작(test_zone_selection)."""
        clar = _zone_clarification_or_none(
            QueryRequest(query="ㅇㅇ존 모든 서버 조회"), None, _config(), _user(["polestar_b0"])
        )
        assert _ids(clar) == ["polestar_b0"]

    @pytest.mark.parametrize("user", [_user(None), _user(["polestar"], role="admin"), None],
                             ids=["allowed_none", "admin", "no_user"])
    def test_unrestricted_users_see_all_zones(self, user):
        clar = _zone_clarification_or_none(QueryRequest(query=_MASS), None, _config(), user)
        assert _ids(clar) == _ZONES


class TestOtherGates:
    def test_file_gate_filters(self):
        assert _file_zone_clarification_or_none(
            "사용률은 지난달 통계를 사용하시오", None, _config(), _user(["polestar"])
        ) is None
        clar = _file_zone_clarification_or_none(
            "사용률은 지난달 통계를 사용하시오", None, _config(), _user(["polestar_b0"])
        )
        assert _ids(clar) == ["polestar_b0"] and clar["has_file"] is True

    def test_unregistered_zone_gate_filters(self):
        clar = _unregistered_zone_clarification_or_none(
            "판교존 서버 목록", _config(), current_user=_user(["polestar_cm_gp"])
        )
        assert clar is not None and _ids(clar) == ["polestar_cm_gp"]

    def test_group_exclusive_gate_filters(self):
        cfg = _config()
        cfg.multi_db.zone_group_exclusive = True
        clar = _zone_group_exclusive_or_none(
            "은행존과 공동존 김포 서버 목록", None, cfg,
            current_user=_user(["polestar_b0", "polestar_cm_gp"]),
        )
        assert _ids(clar) == ["polestar_b0", "polestar_cm_gp"]


class TestRouterPostGate:
    """3단 semantic_router 후단 게이트 — 상태의 `allowed_db_ids`·`user_role`로 같은 규칙."""

    def _call(self, allowed, role="user"):
        from src.routing.semantic_router import _zone_clarification_or_none_router

        state = {
            "zone_clarification_allowed": True,
            "conversation_context": None,
            "user_query": "OS 종류, OS 버전 및 OS패치버전을 확인하시오",
            "parsed_requirements": {"query_targets": ["os_type"], "filter_conditions": []},
            "allowed_db_ids": allowed,
            "user_role": role,
        }
        targets = [{"db_id": d, "relevance_score": 0.9} for d in _ZONES]
        return _zone_clarification_or_none_router(state, targets, None, _config())

    def test_sandbox_only_user_is_not_asked(self):
        assert self._call(["polestar"]) is None
        assert self._call([]) is None

    def test_options_narrowed(self):
        assert _ids(self._call(["polestar_cm_yd"])) == ["polestar_cm_yd"]

    def test_unrestricted(self):
        assert _ids(self._call(None)) == _ZONES
        assert _ids(self._call([], role="admin")) == _ZONES
