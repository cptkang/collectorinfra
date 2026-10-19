"""plans/187 독립 검증 — 사용자 요청 조사 레코드의 타인 열람(W12 소유자 열람 규칙 · D-325 ④).

W10 추가 조사(`kind=diagnosis`)는 플래그 on이면 「소유자 또는 관리자」만 연다. 같은 계열인 W12 채팅
조사(`kind=manual` · `origin=chat` · 알람 없음)는 종전에 소유자가 아니면 존 판정으로 떨어져
**전 존을 부여받은 비관리자**가 타인의 채팅 조사를 id로 열었다(검증 지적 H1). 교정 뒤에는 알람 없는
사용자 요청 조사를 소유자 또는 관리자만 연다.
"""

from __future__ import annotations

import pytest

from src.routing.zones import all_zones
from tests.test_api.test_plan187_w12_view_authz import _config, _get, _seed_chat

ALL_ZONE_USER = {"sub": "u9", "role": "user", "alarm_zones": sorted(all_zones())}


@pytest.mark.parametrize("chat_flag", [True, False])
def test_all_zone_non_admin_cannot_read_other_users_chat_investigation(tmp_path, chat_flag):  # noqa: ANN001
    """[교정 H1] 비소유·비관리자는 전 존이라도 403 — W10 `diagnosis` 규칙과 대칭."""
    _seed_chat(tmp_path, kind="manual")
    resp = _get(_config(tmp_path, chat=chat_flag), ALL_ZONE_USER, "chat-1")
    assert resp.status_code == 403


def test_owner_and_admin_still_read_chat_investigation(tmp_path):  # noqa: ANN001
    """[교정 H1] 소유자·관리자는 그대로 연다 — 사용자 뷰에는 `owner_sub`가 없다(관리자 뷰만)."""
    _seed_chat(tmp_path, kind="manual")
    owner = _get(_config(tmp_path), {"sub": "u1", "role": "user", "alarm_zones": []}, "chat-1")
    assert owner.status_code == 200 and "owner_sub" not in owner.json()
    admin = _get(_config(tmp_path), {"sub": "admin1", "role": "admin"}, "chat-1")
    assert admin.status_code == 200 and admin.json().get("owner_sub") == "u1"


def test_all_zone_non_admin_cannot_read_other_users_diagnosis_when_additional_on(tmp_path):  # noqa: ANN001
    """대조군 — 추가 조사 플래그가 켜진 `diagnosis`는 비소유 전 존 사용자에게 403."""
    _seed_chat(tmp_path, kind="diagnosis")
    resp = _get(_config(tmp_path, additional=True), ALL_ZONE_USER, "chat-1")
    assert resp.status_code == 403


def test_kb_routes_apply_same_owner_rule_to_unbound_requests(tmp_path):  # noqa: ANN001
    """[교정 H1] KB 라우트도 알람 없는 사용자 요청 조사는 소유자 또는 관리자만."""
    from tests.test_api.test_plan187_w9_kb import _briefing
    from tests.test_api.test_plan187_w9_kb import _client as _kb_client
    from tests.test_api.test_plan187_w9_kb import _config as _kb_config
    from tests.test_api.test_plan187_w9_kb import _store as _kb_store

    _kb_store(tmp_path).record(
        investigation_id="chat-kb", alarm_ids=[], kind="manual", status="done", source="chat",
        owner_sub="u1", origin="chat", briefing=_briefing(),
    )
    url = "/api/v1/alarm/investigations/chat-kb/engine-choice"
    other = _kb_client(_kb_config(tmp_path), ALL_ZONE_USER).post(url, json={"choice": "playbook"})
    assert other.status_code == 403
    owner = {"sub": "u1", "role": "user", "alarm_zones": sorted(all_zones())}
    mine = _kb_client(_kb_config(tmp_path), owner).post(url, json={"choice": "playbook"})
    assert mine.status_code == 200, mine.text
