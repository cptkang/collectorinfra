"""plans/194 W5 독립 검증 — 판정 행 조사 상태 열람 인가 · 같은 서버 진행 중 id · 카드 대기 표시.

- V1 「종류 × 사용자 유형」 표(docs/18 2026-10-19): 판정 목록 행 `investigation`이 by-alarm 조회와
  같은 결과인가(보이면 같은 조사 id · by-alarm 403이면 null).
- V2 사건 조사에 연결 행(`episode_existing`)으로 붙은 타 존 알람 — by-alarm은 걸린 알람 전부로 존을
  보지만 판정 행 색인은 최신 상태 행의 `alarm_ids`만 본다(결함 재현 · xfail strict).
- V3 `existing_investigation_id`가 남의 추가 조사(알람 없는 사용자 요청 조사)를 흘리지 않는가.
- V4 U3 카드 — 지연 poll을 거친 by-alarm 응답으로 카드 대기 표시(`DiagnosisView.queueInfo`)가
  성립하는가(결함 재현 · xfail strict).

조사 서비스·실 LLM 호출 0(가짜 클라이언트·가짜 헬스).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import noise_gate.infrastructure.sre_agent_client as sre_client_mod
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api import test_plan187_w11_manual as w11
from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_user_client,
    _seed_decisions,
)
from tests.test_api.test_plan187_w14_live_hub import seed
from tests.test_api.test_plan194_w5_api import _Fetch, _use_health
from tests.test_api.test_ui_plan187_w15_diagnosis import _node

audits = w11.audits
_hostname = w11._hostname
_reset_poll_state = w11._reset_poll_state

GONGJON_DB = w11.GONGJON_DB
BANKJON_DB = w11.BANKJON_DB

USERS: dict[str, dict[str, Any]] = {
    "owner_gongjon": {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]},
    "other_gongjon": {"sub": "u3", "role": "user", "alarm_zones": [ZONE_GONGJON]},
    "other_bankjon": {"sub": "u2", "role": "user", "alarm_zones": [ZONE_BANKJON]},
    "other_allzone": {"sub": "u5", "role": "user", "alarm_zones": [ZONE_GONGJON, ZONE_BANKJON]},
    "admin": {"sub": "admin1", "role": "admin"},
}


def _cfg(tmp_path) -> SimpleNamespace:  # noqa: ANN001
    config = w11._config(tmp_path)
    config.host_authz = SimpleNamespace(mode="admin_only")
    return config


def _decisions(config, user: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
    resp = _make_user_client(config, user=user).get("/api/v1/noise/decisions")
    assert resp.status_code == 200, resp.text
    return {item["alarm_id"]: item.get("investigation") for item in resp.json()["items"]}


def _by_alarm(config, user: dict[str, Any], alarm_id: str) -> tuple[int, str | None]:  # noqa: ANN001
    client = w11._client(config, user)
    _use_health(client.app, _Fetch())
    resp = client.get(f"/api/v1/alarm/investigations/by-alarm/{alarm_id}")
    body = resp.json()
    return resp.status_code, (body.get("investigation_id") if resp.status_code == 200 else None)


# ── V1 종류 × 사용자 유형 ────────────────────────────────────────────────────
def _seed_table(tmp_path) -> None:  # noqa: ANN001
    rows = [("a1", GONGJON_DB), ("a2", GONGJON_DB), ("a3", BANKJON_DB), ("a4", BANKJON_DB)]
    _seed_decisions(
        tmp_path,
        [_decision_record(alarm_id=a, db_id=d, fingerprint=a, tier="page") for a, d in rows],
    )
    # 자동 조사(공동존) · 남(u9)이 접수한 분석(공동존) · u1이 접수한 분석(은행존) ·
    # 자동 조사(은행존)
    seed(tmp_path, "inv-a1", status="done", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    seed(tmp_path, "inv-a2", status="done", kind="manual", owner_sub="u9", origin="manual",
         alarm_ids=["a2"], alarm_db_ids={"a2": GONGJON_DB}, parent_alarm_id="a2")
    seed(tmp_path, "inv-a3", status="done", kind="manual", owner_sub="u1", origin="manual",
         alarm_ids=["a3"], alarm_db_ids={"a3": BANKJON_DB}, parent_alarm_id="a3")
    seed(tmp_path, "inv-a4", status="done", alarm_ids=["a4"], alarm_db_ids={"a4": BANKJON_DB})


@pytest.mark.parametrize("user_kind", list(USERS))
def test_decision_row_visibility_equals_by_alarm(tmp_path, user_kind) -> None:  # noqa: ANN001
    _seed_table(tmp_path)
    config = _cfg(tmp_path)
    user = USERS[user_kind]
    rows = _decisions(config, user)
    for alarm_id in ("a1", "a2", "a3", "a4"):
        code, inv = _by_alarm(config, user, alarm_id)
        shown = rows.get(alarm_id)
        if code != 200:
            assert shown is None, (user_kind, alarm_id, shown)
        else:
            assert shown is not None and shown["investigation_id"] == inv, (user_kind, alarm_id)


# ── V2 사건 조사 연결 행(타 존 멤버) ─────────────────────────────────────────
def _seed_episode_link(tmp_path) -> None:  # noqa: ANN001
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="a1", db_id=GONGJON_DB, fingerprint="a1", tier="page"),
        _decision_record(alarm_id="b1", db_id=BANKJON_DB, fingerprint="b1", tier="page"),
    ])
    store = w11._store(tmp_path)
    store.record(investigation_id="inv-ep", alarm_ids=["a1"], kind="episode", status="submitted",
                 source="test", episode_id="ep-1", alarm_db_ids={"a1": GONGJON_DB})
    # 같은 사건의 은행존 알람 b1이 기존 조사를 재사용(연결 행)
    store.record(investigation_id="inv-ep", alarm_ids=["b1"], kind="episode",
                 status="episode_existing", source="test", episode_id="ep-1",
                 alarm_db_ids={"b1": BANKJON_DB})


def test_episode_link_by_alarm_baseline(tmp_path) -> None:  # noqa: ANN001
    """기준: by-alarm은 걸린 알람 전부(a1·b1)로 존을 본다 — 공동존 사용자는 a1도 403."""
    _seed_episode_link(tmp_path)
    config = _cfg(tmp_path)
    assert _by_alarm(config, USERS["owner_gongjon"], "a1")[0] == 403
    assert _by_alarm(config, USERS["admin"], "b1") == (200, "inv-ep")


def test_episode_link_other_zone_member_hides_row(tmp_path) -> None:  # noqa: ANN001
    _seed_episode_link(tmp_path)
    rows = _decisions(_cfg(tmp_path), USERS["owner_gongjon"])
    assert rows["a1"] is None


def test_episode_link_member_row_shows_investigation(tmp_path) -> None:  # noqa: ANN001
    _seed_episode_link(tmp_path)
    rows = _decisions(_cfg(tmp_path), USERS["admin"])
    assert rows["b1"] is not None and rows["b1"]["investigation_id"] == "inv-ep"


# ── V3 같은 서버 진행 중 — 남의 추가 조사 id를 흘리지 않는다 ─────────────────
@pytest.mark.parametrize("user_kind", ["owner_gongjon", "other_allzone"])
def test_host_in_flight_hides_others_unbound_request(tmp_path, monkeypatch, audits, user_kind) -> None:  # noqa: ANN001, E501
    w11._seed_alarm(tmp_path)  # a1 · WEB-01 · 공동존
    # 남(u9)의 추가 조사(알람 없음) — 같은 서버
    seed(tmp_path, "inv-diag", status="running", kind="diagnosis", owner_sub="u9",
         origin="investigation_chat", parent_investigation_id="inv-x",
         subject={"server_name": "WEB-01", "hostname": "web-01-host"})
    w11._inject(monkeypatch, w11._FakeClient(status="rejected",
                                             reason="host_investigation_in_flight"))
    client = w11._client(_cfg(tmp_path), USERS[user_kind])
    body = w11._post(client).json()
    assert body["status"] == "rejected"
    assert "existing_investigation_id" not in body


# ── V4 U3 카드 대기 표시 ────────────────────────────────────────────────────
def _queued_view(tmp_path, monkeypatch) -> dict[str, Any]:  # noqa: ANN001
    from tests.test_api.test_plan153_w2_investigation_api import _PollClient

    w11._seed_alarm(tmp_path)
    seed(tmp_path, "inv-q", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    # sre_agent 대기 잡 poll 응답 shape(summary — phase=queued · queue_position)
    fake = _PollClient({"status": "running", "phase": "queued", "queue_position": 2})
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda ng: fake)
    client = w11._client(w11._config(tmp_path), USERS["owner_gongjon"])
    _use_health(client.app, _Fetch())
    return client.get("/api/v1/alarm/investigations/by-alarm/a1").json()


def test_card_view_carries_queue_position(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    view = _queued_view(tmp_path, monkeypatch)
    assert view["queue_position"] == 2 and view["service_load"]["running"] == 1


def test_card_queue_display_from_polled_view(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    view = _queued_view(tmp_path, monkeypatch)
    out = _node(f"var v = {json.dumps(view)}; out = DV.queueInfo(v, v.service_load);")
    assert out == {"position": 2, "running": 1}
