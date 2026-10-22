"""plans/187 W17 — 장애 진단 화면 머리의 「어떤 알람을 조사하는가」(`view.alarm`).

- 실시간 화면 플래그 켜짐 + `activity_since` 조회에만 판정 기록 요약을 붙인다(꺼짐 = 비트 동일)
- 사용자 경로: 운영자 전용 `condition_log` 제외 · 관리자: 포함 · 심각도는 `signals.severity`
- 판정 기록이 없거나 알람 없는 채팅 조사는 `alarm` 키 자체가 없다
"""

from __future__ import annotations

import json

from tests.test_api.test_plan187_w14_live_api import (
    ADMIN,
    GONGJON_DB,
    U1,
    _app,
    _config,
    _get,
    fake_sre,  # noqa: F401 — 픽스처 재노출
)
from tests.test_api.test_plan187_w14_live_hub import FakeJob, ev, seed


def _decisions(tmp_path) -> str:  # noqa: ANN001
    path = tmp_path / "decisions.jsonl"
    rec = {
        "ts": "2026-10-21T01:02:03+00:00", "alarm_id": "a1", "alarm_name": "CPU 사용률 임계 초과",
        "server_name": "WEB-01", "resource_name": "cpu", "tier": "page", "reason": "임계 초과",
        "condition_log": "CPU 97% > 90%", "signals": {"severity": 3},
    }
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def _cfg(tmp_path, *, live: bool = True):  # noqa: ANN001, ANN202
    config = _config(tmp_path, live=live)
    config.noise_gate.decision_store_path = _decisions(tmp_path)
    return config


async def test_user_gets_alarm_brief_without_condition_log(tmp_path, fake_sre):  # noqa: ANN001, F811
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    fake_sre.jobs["inv-a"] = FakeJob(events=[ev(1)])
    resp = await _get(_app(_cfg(tmp_path), U1), "/alarm/investigations/inv-a",
                      params={"activity_since": 0})
    assert resp.status_code == 200
    alarm = resp.json()["alarm"]
    assert alarm["alarm_name"] == "CPU 사용률 임계 초과" and alarm["server_name"] == "WEB-01"
    assert alarm["severity"] == 3 and alarm["tier"] == "page"
    assert "condition_log" not in alarm and "CPU 97%" not in resp.text


async def test_admin_gets_condition_log(tmp_path, fake_sre):  # noqa: ANN001, F811
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    fake_sre.jobs["inv-a"] = FakeJob(events=[ev(1)])
    resp = await _get(_app(_cfg(tmp_path), ADMIN), "/alarm/investigations/inv-a",
                      params={"activity_since": 0})
    assert resp.json()["alarm"]["condition_log"] == "CPU 97% > 90%"


async def test_no_brief_without_cursor_or_decision(tmp_path, fake_sre):  # noqa: ANN001, F811
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    seed(tmp_path, "inv-z", alarm_ids=["zz"], alarm_db_ids={"zz": GONGJON_DB})
    seed(tmp_path, "chat-u1", kind="diagnosis", owner_sub="u1", origin="chat")
    for key in ("inv-a", "inv-z", "chat-u1"):
        fake_sre.jobs[key] = FakeJob(events=[ev(1)])
    app = _app(_cfg(tmp_path), ADMIN)
    # 커서 없는 종전 조회(브리핑 화면) — 키를 붙이지 않는다
    assert "alarm" not in (await _get(app, "/alarm/investigations/inv-a")).json()
    for key in ("inv-z", "chat-u1"):
        resp = await _get(app, f"/alarm/investigations/{key}", params={"activity_since": 0})
        assert resp.status_code == 200 and "alarm" not in resp.json()


async def test_flag_off_has_no_alarm_key(tmp_path, monkeypatch):  # noqa: ANN001
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod
    from tests.test_api.test_plan153_w2_investigation_api import _PollClient

    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    fake = _PollClient({"status": "running"})
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda ng: fake)
    resp = await _get(_app(_cfg(tmp_path, live=False), ADMIN), "/alarm/investigations/inv-a",
                      params={"activity_since": 0})
    assert resp.status_code == 200 and "alarm" not in resp.json()
