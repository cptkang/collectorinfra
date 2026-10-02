"""범위 질문 라우트 배선 (Plan 82 W65-T6 · SPEC-scope-select).

★ 두 계약을 못박는다:
  ① **모호성 해소가 이긴다** — `_zone_clarification_or_none`이 반환하면 이 질문은 뜨지 않는다
  ② **전량 조회 형태에만 발동** — 서버 하나를 찾는 질의는 탐색이 ~150ms에 끝내므로
     여기서 붙잡는 것은 순손실이다(§5.2)

DB·LLM 0(D-127).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes.query import _scope_select_or_none
from src.config import CompositeConfig, MultiDBConfig

ALL_DBS = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _config(*, enabled: bool = True, active=None):
    return SimpleNamespace(
        composite=CompositeConfig(scope_select_enabled=enabled),
        multi_db=MultiDBConfig(
            active_db_ids_csv=",".join(ALL_DBS if active is None else active)
        ),
    )


def _body(query: str, selected=None):
    return SimpleNamespace(query=query, selected_db_ids=selected)


FULL_SCAN = "모든 서버의 OS 버전을 조회해줘"


class TestFires:
    def test_full_scan_over_two_zone_groups_asks(self):
        payload = _scope_select_or_none(_body(FULL_SCAN), None, _config(), None)

        assert payload is not None
        assert payload["kind"] == "scope_select"
        assert payload["options"][0]["default"] is True

    def test_options_cover_both_zone_groups(self):
        payload = _scope_select_or_none(_body(FULL_SCAN), None, _config(), None)

        labels = [o["label"] for o in payload["options"][1:]]
        assert labels == ["은행존", "공동존"]


class TestDoesNotFire:
    def test_disabled(self):
        assert _scope_select_or_none(
            _body(FULL_SCAN), None, _config(enabled=False), None
        ) is None

    def test_resume_turn(self):
        assert _scope_select_or_none(
            _body(FULL_SCAN, selected=["polestar_b0"]), None, _config(), None
        ) is None

    def test_follow_up_turn_inherits_scope(self):
        """후속 턴은 직전 존 승계가 우선이다(zone_select와 같은 규칙)."""
        assert _scope_select_or_none(_body(FULL_SCAN), {"any": "state"}, _config(), None) is None

    def test_single_target_query_is_left_to_discovery(self):
        """★ 'abd00 서버의 프로세스'는 탐색이 ~150ms에 끝낸다 — 물으면 순손실이다."""
        assert _scope_select_or_none(
            _body("abd00 서버의 프로세스를 조회해줘"), None, _config(), None
        ) is None

    def test_location_term_already_narrows(self):
        """위치어가 있으면 D-065가 결정적으로 좁힌다 — 정해진 것을 되묻지 않는다."""
        assert _scope_select_or_none(
            _body("김포의 모든 서버 OS 버전"), None, _config(), None
        ) is None

    def test_single_zone_group_has_nothing_to_narrow(self):
        assert _scope_select_or_none(
            _body(FULL_SCAN), None, _config(active=["polestar_cm_gp", "polestar_cm_yd"]), None
        ) is None


class TestAuthorizationFilter:
    def test_unauthorized_zone_is_not_offered(self):
        """★ 인가 밖 존이 선택지에 뜨면 권한 밖 존의 존재가 노출된다."""
        payload = _scope_select_or_none(
            _body(FULL_SCAN), None, _config(),
            {"allowed_db_ids": ["polestar_cm_gp", "polestar_cm_yd"]},
        )

        assert payload is None, "허용 존이 한 그룹뿐이면 좁힐 여지가 없다"

    def test_allowed_none_means_all(self):
        payload = _scope_select_or_none(
            _body(FULL_SCAN), None, _config(), {"allowed_db_ids": None},
        )

        assert payload is not None
