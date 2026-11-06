"""plans/215 W8c — `GET /biz-services/monitor/{id}/metrics` 라우트.

인가 · 플래그 off 404(조회기 생성 0) · 구성원 id 검증 400 · 존 권한 밖 구성원 건수만 · 429를 본다.
W6 라우트 테스트와 같은 대역을 쓰고, 조회기는 가짜 SQL 실행기로 만들어 `app.state`에 넣는다.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application import biz_monitor_history as h
from noise_gate.application.biz_monitor_alarms import BizAlarmAggregator
from noise_gate.application.biz_service_config import BizServiceSettings
from noise_gate.domain.biz_service_model import BizServiceConfig
from src.api.routes import biz_monitor
from tests.mocks.async_redis import FakeAsyncRedis
from tests.test_api.test_plan215_w6_biz_monitor import (
    ADMIN_SECRET,
    AUTH_SECRET,
    BASE,
    _Cache,
    _Decisions,
    _hdr,
    _Incidents,
    _services,
    _Users,
)

URL = f"{BASE}/1/metrics"


class _Exec:
    """서버마다 cpu 값 1점을 돌려주는 가짜 SQL 실행기(SELECT 기록)."""

    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
        self.sqls.append(sql)
        ids = re.search(r"IN \(([\d, ]+)\)", sql).group(1).split(", ")  # type: ignore[union-attr]
        since = re.search(r"BETWEEN '(\d+)'", sql).group(1)  # type: ignore[union-attr]
        return [{"server_id": int(i), "resource_type": "server.Cpus",
                 "definition_name": "Utilization", "stat_date": since, "avg_val": 5.0}
                for i in ids]


def _app(*, history_on: bool = True) -> tuple[TestClient, FastAPI, _Exec]:
    app = FastAPI()
    app.include_router(biz_monitor.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(
            biz_monitor_active=True, biz_monitor_metrics_active=False,
            biz_monitor_alarm_window_minutes=60, biz_monitor_history_active=history_on,
        ),
    )
    app.state.user_repo = _Users()
    cache = _Cache(_services(), FakeAsyncRedis())
    app.state.biz_service_cache = cache
    app.state.biz_monitor_alarms = BizAlarmAggregator(
        cache=cache, incident_store=_Incidents(), decision_store=_Decisions([]), window_minutes=60,
        settings_loader=lambda: SimpleNamespace(config=SimpleNamespace(sms_thresholds={}),
                                                generic_terms=frozenset()),
    )
    ex = _Exec()
    if history_on:
        app.state.biz_monitor_history = h.BizMetricsHistory(
            redis=cache.redis, gateway=None, execute=ex,
            settings_loader=lambda: BizServiceSettings(
                BizServiceConfig(), frozenset(), (), {"cpu": ("server.Cpus", "Utilization")}),
            engine_of=lambda db_id: "postgresql",
        )
    return TestClient(app), app, ex


def test_requires_user_token() -> None:
    client, _, _ = _app()
    assert client.get(URL).status_code == 401


def test_flag_off_404_and_no_history_built(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Any] = []
    monkeypatch.setattr(h, "build_biz_metrics_history", lambda *a, **k: built.append(a))
    client, app, ex = _app(history_on=False)
    resp = client.get(URL, headers=_hdr("a"))
    assert resp.status_code == 404 and resp.json()["detail"] == "지표 보기가 꺼져 있습니다."
    assert built == [] and ex.sqls == []
    assert not hasattr(app.state, "biz_monitor_history")


def test_on_admin_body_keys_and_memo() -> None:
    client, _, ex = _app()
    body = client.get(URL, headers=_hdr("a")).json()
    assert {"service_id", "window", "compare", "range", "selection", "members", "series", "sms",
            "apm", "thresholds", "links", "partial", "notices", "memo",
            "hidden_member_count"} <= set(body)
    assert body["window"] == "24h" and body["selection"]["queried"] == 2
    assert {s["member_id"] for s in body["series"]} == {11, 12}
    assert body["thresholds"] == {} and len(ex.sqls) == 1
    again = client.get(URL, headers=_hdr("a")).json()
    assert again["memo"] is True and len(ex.sqls) == 1  # 60초 메모 — 원천 호출 0


def test_zone_user_sees_only_own_zone_and_hidden_counted() -> None:
    client, _, ex = _app()
    body = client.get(URL, headers=_hdr("u")).json()  # 자동 선택
    assert {s["member_id"] for s in body["series"]} == {11}
    assert [m["id"] for m in body["members"]] == [11]
    assert body["hidden_member_count"] == 1  # 권한 밖은 건수만
    assert "IN (101)" in ex.sqls[0]


def test_zone_user_hidden_member_id_same_400_as_foreign() -> None:
    client, _, ex = _app()
    hidden = client.get(URL, params={"members": "11,12"}, headers=_hdr("u"))  # 12 = 권한 밖
    foreign = client.get(URL, params={"members": "11,999"}, headers=_hdr("u"))
    assert hidden.status_code == foreign.status_code == 400
    assert hidden.json() == foreign.json()  # 숨김 여부가 응답으로 구별되지 않는다
    assert "12" not in hidden.json()["detail"]
    assert ex.sqls == []
    ok = client.get(URL, params={"members": "11,12"}, headers=_hdr("a"))  # 관리자는 둘 다 보인다
    assert ok.status_code == 200 and ok.json()["selection"]["queried"] == 2


@pytest.mark.parametrize(
    "params",
    [
        {"members": "11,999"},  # 이 서비스 구성원 아님
        {"members": "21"},  # 다른 서비스 구성원
        {"members": "11;12"},
        {"members": ",".join(str(i) for i in range(1, 22))},  # 상한 20 초과
        {"compare": "lastweek"},
        {"window": "2h"},
        {"window": "7d", "compare": "yesterday"},
    ],
)
def test_bad_params_400_and_no_select(params: dict[str, str]) -> None:
    client, _, ex = _app()
    assert client.get(URL, params=params, headers=_hdr("a")).status_code == 400
    assert ex.sqls == []


def test_unknown_service_404() -> None:
    client, _, _ = _app()
    assert client.get(f"{BASE}/99/metrics", headers=_hdr("a")).status_code == 404


def test_global_concurrency_429_message() -> None:
    client, app, ex = _app()
    app.state.biz_monitor_history._running = 2  # 다른 조회 2건이 진행 중
    resp = client.get(URL, headers=_hdr("a"))
    assert resp.status_code == 429 and resp.json()["detail"] == "다른 지표 조회가 진행 중입니다"
    assert ex.sqls == []
