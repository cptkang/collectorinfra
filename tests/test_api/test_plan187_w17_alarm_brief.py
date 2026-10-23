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


async def test_with_alarm_query_attaches_brief_without_live_view(tmp_path, monkeypatch):  # noqa: ANN001
    """(W23) 조사 대화 카드 첫 조회 — `with_alarm=true`면 실시간 화면이 꺼져도 요약을 싣는다."""
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod
    from tests.test_api.test_plan153_w2_investigation_api import _PollClient

    seed(tmp_path, "inv-a", alarm_ids=["a1", "a2"],
         alarm_db_ids={"a1": GONGJON_DB, "a2": GONGJON_DB})
    fake = _PollClient({"status": "running"})
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda ng: fake)
    app = _app(_cfg(tmp_path, live=False), U1)
    assert "alarm" not in (await _get(app, "/alarm/investigations/inv-a")).json()
    resp = await _get(app, "/alarm/investigations/inv-a", params={"with_alarm": "true"})
    alarm = resp.json()["alarm"]
    assert alarm["alarm_name"] == "CPU 사용률 임계 초과" and alarm["alarm_count"] == 2
    assert "condition_log" not in alarm


async def test_with_alarm_related_lists_other_alarms(tmp_path, monkeypatch):  # noqa: ANN001
    """(W23) 사건 묶음 — 나머지 알람의 이름·서버·시각을 `related`로 싣는다."""
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod
    from tests.test_api.test_plan153_w2_investigation_api import _PollClient

    config = _cfg(tmp_path, live=False)
    with open(config.noise_gate.decision_store_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-10-21T01:06:03+00:00", "alarm_id": "a2",
                             "alarm_name": "WAS 응답 지연", "server_name": "WAS-01",
                             "condition_log": "secret"}, ensure_ascii=False) + "\n")
    seed(tmp_path, "inv-a", alarm_ids=["a1", "a2", "a9"], alarm_db_ids={"a1": GONGJON_DB})
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client",
                        lambda ng: _PollClient({"status": "running"}))
    resp = await _get(_app(config, ADMIN), "/alarm/investigations/inv-a",
                      params={"with_alarm": "true"})
    alarm = resp.json()["alarm"]
    assert alarm["alarm_count"] == 3
    assert alarm["related"] == [
        {"alarm_id": "a2", "alarm_name": "WAS 응답 지연", "server_name": "WAS-01",
         "ts": "2026-10-21T01:06:03+00:00"},
        {"alarm_id": "a9"},
    ]


async def test_with_alarm_brief_names_alarm_source(tmp_path, monkeypatch):  # noqa: ANN001
    """(W24) 알람 발생 소스 — 판정 기록 dbId를 존·위치·수집 시스템 이름표로 푼다(관련 알람도)."""
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod
    from tests.test_api.test_plan153_w2_investigation_api import _PollClient

    config = _cfg(tmp_path, live=False)
    path = config.noise_gate.decision_store_path
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    rows[0]["db_id"] = "polestar_cm_gp"
    rows.append({"ts": "2026-10-21T01:06:03+00:00", "alarm_id": "a2",
                 "alarm_name": "WAS 응답 지연", "db_id": "jennifer_b0"})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    seed(tmp_path, "inv-a", alarm_ids=["a1", "a2"], alarm_db_ids={"a1": GONGJON_DB})
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client",
                        lambda ng: _PollClient({"status": "running"}))
    resp = await _get(_app(config, ADMIN), "/alarm/investigations/inv-a",
                      params={"with_alarm": "true"})
    alarm = resp.json()["alarm"]
    assert alarm["db_id"] == "polestar_cm_gp" and alarm["source"] == "공동존 김포 폴스타"
    assert alarm["related"][0]["source"] == "은행 제니퍼(은행존)"


def test_alarm_source_text_per_registry_source() -> None:
    """(W24) 레지스트리 정본에서 푼 소스 이름표 — DB·APM 소스·단일 설정·미등록."""
    from src.api.routes.alarm import _alarm_source_text

    assert _alarm_source_text("polestar_b0") == "은행존 폴스타"
    assert _alarm_source_text("polestar_cm_yd") == "공동존 여의도 폴스타"
    assert _alarm_source_text("jennifer_cm_prd") == "공동존 운영 제니퍼"
    assert _alarm_source_text("jennifer") == "제니퍼(존 미지정)"
    assert _alarm_source_text("nope") == "nope"
