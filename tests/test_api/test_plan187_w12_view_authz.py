"""plans/187 W12 교정 — 사용자 요청 조사 레코드의 소유자 열람 인가.

- 소유자(`owner_sub == sub`)는 존 검사 없이 연다 — 채팅 조사(`origin=chat`)는 존이 없다
- 사용자 뷰 마스킹은 소유자에게도 그대로 · 타인은 종전 판정(403)
- 그 레코드를 연 플래그가 꺼져 있으면 종전 동작(존 검사 — 빈 `alarm_ids`는 fail-closed 403)
- by-alarm 소유자 열람에는 `previous`를 붙이지 않는다(존 검사를 거치지 않았으므로)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api import investigation_poll
from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api.test_noise_dashboard_api import _make_config

GONGJON_DB = "polestar_cm_gp"
SECRET = "sk-abcdefghijklmnopqrstuvwx"  # DataMasker 값 패턴(API 키)

ADMIN = {"sub": "admin1", "role": "admin"}
OWNER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_BANKJON]}
OWNER_NO_ZONE = {"sub": "u1", "role": "user", "alarm_zones": []}
OTHER = {"sub": "u9", "role": "user", "alarm_zones": [ZONE_GONGJON]}


@pytest.fixture(autouse=True)
def _reset_poll_state():
    investigation_poll.POLL_AT.clear()
    investigation_poll.POLL_LOCKS.clear()
    yield
    investigation_poll.POLL_AT.clear()
    investigation_poll.POLL_LOCKS.clear()


def _config(tmp_path, *, chat: bool = True, manual: bool = True, additional: bool = False):  # noqa: ANN001, ANN202
    config = _make_config(tmp_path)
    ng = config.noise_gate
    ng.investigation_store_path = str(tmp_path / "inv.jsonl")
    ng.investigation_store_max_lines = 2000
    ng.investigation_trigger_enabled = True
    ng.investigation_service_url = "http://127.0.0.1:9/sse"
    ng.investigation_service_token = ""
    ng.investigation_mcp_call_timeout_seconds = 2.0
    ng.investigation_additional_enabled = additional
    ng.investigation_manual_enabled = manual
    ng.sre_chat_investigation_enabled = chat
    config.host_authz = SimpleNamespace(mode="admin_only")
    return config


def _client(config, user) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def _store(tmp_path) -> InvestigationStore:  # noqa: ANN001
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


def _seed_chat(tmp_path, *, kind: str) -> None:  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id="chat-1", alarm_ids=[], kind=kind, status="done", source="chat",
        owner_sub="u1", origin="chat", subject={"hostname": "wasdemo01", "note": SECRET},
        briefing={"summary": "채팅 조사", "limitations": []},
    )


def _seed_manual(tmp_path) -> None:  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id="man-1", alarm_ids=["a1"], kind="manual", status="done",
        source="manual", alarm_db_ids={"a1": GONGJON_DB}, owner_sub="u1", origin="manual",
        parent_alarm_id="a1", subject={"hostname": "wasdemo01", "note": SECRET},
        briefing={"summary": "사용자 요청 조사", "limitations": []},
    )


def _get(config, user, path: str):  # noqa: ANN001, ANN202
    return _client(config, user).get(f"/api/v1/alarm/investigations/{path}")


@pytest.mark.parametrize("kind", ["manual", "diagnosis"])
@pytest.mark.parametrize("user", [OWNER, OWNER_NO_ZONE])
def test_chat_owner_can_view_masked(tmp_path, kind, user):  # noqa: ANN001
    _seed_chat(tmp_path, kind=kind)
    resp = _get(_config(tmp_path), user, "chat-1")
    assert resp.status_code == 200
    assert resp.json()["investigation_id"] == "chat-1"
    assert SECRET not in resp.text


def test_chat_other_user_forbidden_admin_sees_raw(tmp_path):  # noqa: ANN001
    _seed_chat(tmp_path, kind="manual")
    config = _config(tmp_path)
    assert _get(config, OTHER, "chat-1").status_code == 403
    admin = _get(config, ADMIN, "chat-1")
    assert admin.status_code == 200 and SECRET in admin.text


@pytest.mark.parametrize("kind", ["manual", "diagnosis"])
def test_chat_flag_off_keeps_fail_closed(tmp_path, kind):  # noqa: ANN001
    _seed_chat(tmp_path, kind=kind)
    assert _get(_config(tmp_path, chat=False), OWNER, "chat-1").status_code == 403


def test_manual_owner_outside_zone(tmp_path):  # noqa: ANN001
    _seed_manual(tmp_path)
    config = _config(tmp_path)
    resp = _get(config, OWNER, "man-1")
    assert resp.status_code == 200 and SECRET not in resp.text
    by_alarm = _get(config, OWNER, "by-alarm/a1")
    assert by_alarm.status_code == 200
    assert by_alarm.json()["investigation_id"] == "man-1" and "previous" not in by_alarm.json()
    other_bank = {"sub": "u7", "role": "user", "alarm_zones": [ZONE_BANKJON]}
    assert _get(config, other_bank, "man-1").status_code == 403
    assert _get(config, other_bank, "by-alarm/a1").status_code == 403


def test_manual_flag_off_keeps_zone_check(tmp_path):  # noqa: ANN001
    _seed_manual(tmp_path)
    config = _config(tmp_path, manual=False)
    assert _get(config, OWNER, "man-1").status_code == 403
    assert _get(config, OWNER, "by-alarm/a1").status_code == 403
