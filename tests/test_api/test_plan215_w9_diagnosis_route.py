"""plans/215 W9 2부 — `GET /biz-services/monitor/{id}/diagnosis`(D1 진단 스냅샷) 라우트.

인가(미로그인 401) · 플래그 off 404(진단기 생성 0 · 관측 호출 0) · 진단기 지연 생성(프로세스에 1개 ·
배선 인자) · 존 필터(권한 밖 구성원은 결손 건수만 · 전부 권한 밖이면 404) · 「정상」 어휘 0을 본다.

W6 라우트 테스트와 같은 대역(사용자 JWT · 가짜 사본 · 가짜 결정 이력)을 쓰고, 진단기는 실
`BizDiagnoser`를 게이트웨이·SQL 실행기 없이(즉석 조회 0) 만들어 `app.state`에 넣는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application import biz_diagnosis as bd
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

URL = f"{BASE}/1/diagnosis"


def _records() -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    return [
        {"ts": now, "alarm_id": "r1", "tier": "page", "signals": {"severity": 3},
         "db_id": "pg", "hostname": "loan-01"},
        {"ts": now, "alarm_id": "r2", "tier": "page", "signals": {"severity": 3},
         "db_id": "pg", "hostname": "loan-02"},
    ]


def _app(*, diagnosis_on: bool = True, real: bool = True) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.include_router(biz_monitor.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(
            biz_monitor_active=True, biz_monitor_metrics_active=False,
            biz_monitor_alarm_window_minutes=60, biz_diagnosis_active=diagnosis_on,
            biz_diagnosis_window_minutes=15, biz_diagnosis_max_apm_targets=50,
            investigation_store_path="", investigation_store_max_lines=2000,
        ),
    )
    app.state.user_repo = _Users()
    cache = _Cache(_services(), FakeAsyncRedis())
    app.state.biz_service_cache = cache
    app.state.apm_host_map_cache = None
    agg = BizAlarmAggregator(
        cache=cache, incident_store=_Incidents(), decision_store=_Decisions(_records()),
        window_minutes=60,
        settings_loader=lambda: SimpleNamespace(config=SimpleNamespace(sms_thresholds={}),
                                                generic_terms=frozenset()),
    )
    app.state.biz_monitor_alarms = agg
    if diagnosis_on and real:
        app.state.biz_diagnoser = bd.BizDiagnoser(
            aggregator=agg, redis=cache.redis, gateway=None, execute=None,
            settings_loader=lambda: BizServiceSettings(BizServiceConfig(), frozenset()),
            window_minutes=15,
        )
    return TestClient(app), app


def test_requires_user_token() -> None:
    client, _ = _app()
    assert client.get(URL).status_code == 401


def test_flag_off_404_and_no_diagnoser_built(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Any] = []
    monkeypatch.setattr(bd, "build_biz_diagnoser", lambda *a, **k: built.append(a))
    client, app = _app(diagnosis_on=False)
    resp = client.get(URL, headers=_hdr("a"))
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "biz_diagnosis_disabled"
    assert built == [] and not hasattr(app.state, "biz_diagnoser")


def test_lazy_build_once_with_route_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Fake:
        async def diagnose(self, service_id: int, *, scope: Any) -> dict[str, Any] | None:
            return {"service_id": service_id, "scope": sorted(scope) if scope else None}

    def _build(config: Any, **kwargs: Any) -> _Fake:
        calls.append(kwargs)
        return _Fake()

    monkeypatch.setattr(bd, "build_biz_diagnoser", _build)
    client, app = _app(real=False)
    body = client.get(URL, headers=_hdr("u")).json()
    assert body == {"service_id": 1, "scope": ["gongjon"]}
    assert client.get(URL, headers=_hdr("a")).json()["scope"] is None  # 전 존 = None
    assert len(calls) == 1  # 프로세스에 1개(app.state 보관)
    assert calls[0]["aggregator"] is app.state.biz_monitor_alarms
    assert calls[0]["map_cache"] is None and calls[0]["investigation_store"] is None
    assert isinstance(app.state.biz_diagnoser, _Fake)


def test_admin_body_keys_and_no_observation_calls() -> None:
    client, _ = _app()
    resp = client.get(URL, headers=_hdr("a"))
    assert resp.status_code == 200
    body = resp.json()
    assert {"service_id", "display_name", "computed_at", "window_minutes", "verdict",
            "verdict_text", "verdict_guidance", "hypothesis_label", "correlation_note",
            "anomalous_members", "timeline", "representative", "representative_reason", "gaps",
            "sources", "partial", "notices"} <= set(body)
    assert body["service_id"] == 1 and body["hypothesis_label"] == "가설 · 참고용"
    assert body["sources"]["calls"] == {"apm": 0, "sms": 0}
    assert {m["name"] for m in body["anomalous_members"]} == {"loan-01", "loan-02"}
    assert "정상" not in json.dumps(body, ensure_ascii=False)


def test_zone_user_sees_own_zone_and_hidden_counted() -> None:
    client, _ = _app()
    body = client.get(URL, headers=_hdr("u")).json()
    text = json.dumps(body, ensure_ascii=False)
    assert "loan-02" not in text
    assert [m["name"] for m in body["anomalous_members"]] == ["loan-01"]
    hidden = [g for g in body["gaps"] if g["code"] == "out_of_scope"]
    assert hidden and hidden[0]["count"] == 1


def test_all_hidden_or_unknown_service_404() -> None:
    client, _ = _app()
    resp = client.get(URL, headers=_hdr("n"))  # 존 없음 — 보이는 구성원 0
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "service_not_found"
    assert client.get(f"{BASE}/99/diagnosis", headers=_hdr("a")).status_code == 404
