"""plans/187 W18 — 「이벤트 찾아 조사」 서버 쪽.

- 판정 기록 조회에 `alarm_q`(알람명)·`server_q`(서버명·hostname) 부분일치 — 미지정이면 종전과 같다
- `/alarm/investigations/active`의 `event_search` = 알람 다시 분석 접수 플래그
"""

from __future__ import annotations

from src.api.dependencies import require_user
from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_client,
    _make_config,
    _seed_decisions,
)
from tests.test_api.test_plan187_w14_live_api import ADMIN, _app, _config, _get

U1 = {"sub": "u1", "role": "user"}


def _client(tmp_path):  # noqa: ANN001, ANN202
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="a1", alarm_name="CPU 사용률 임계 초과", server_name="WEB-01"),
        _decision_record(alarm_id="a2", alarm_name="디스크 사용률", server_name="DB-01",
                         hostname="dbhost01"),
        _decision_record(alarm_id="a3", alarm_name="cpu load", server_name="WAS-01",
                         reason="WEB-01 연관"),
    ])
    client = _make_client(_make_config(tmp_path))
    client.app.dependency_overrides[require_user] = lambda: U1
    return client


def _ids(resp) -> list[str]:  # noqa: ANN001
    assert resp.status_code == 200, resp.text
    return [item["alarm_id"] for item in resp.json()["items"]]


def test_alarm_and_server_partial_filters(tmp_path) -> None:  # noqa: ANN001
    client = _client(tmp_path)
    base = "/api/v1/noise/decisions?range=24h"
    assert _ids(client.get(base)) == ["a3", "a2", "a1"]  # 미지정 = 종전
    assert _ids(client.get(base + "&alarm_q=CPU")) == ["a3", "a1"]  # 대소문자 무시
    # 서버 칸은 사유를 보지 않는다(q와 다르다) — a3 사유의 WEB-01은 걸리지 않는다
    assert _ids(client.get(base + "&server_q=web-01")) == ["a1"]
    assert _ids(client.get(base + "&server_q=dbhost")) == ["a2"]  # hostname도 본다
    assert _ids(client.get(base + "&alarm_q=cpu&server_q=was")) == ["a3"]  # AND
    assert client.get(base + "&server_q=" + "x" * 201).status_code == 422


async def test_active_reports_event_search_flag(tmp_path) -> None:  # noqa: ANN001
    on = _config(tmp_path)
    on.noise_gate.investigation_manual_enabled = True
    body = (await _get(_app(on, ADMIN), "/alarm/investigations/active")).json()
    assert body["event_search"] is True
    off = _config(tmp_path)
    off.noise_gate.investigation_manual_enabled = False
    body = (await _get(_app(off, ADMIN), "/alarm/investigations/active")).json()
    assert body["event_search"] is False
