"""plans/215 W8 — 업무 현황 상세 「개요」 API.

구성 맵(`/{id}/topology`) · 24시간 추이(`/{id}/trend`).

인가·존 범위는 W6와 같은 실 `require_user`(사용자 JWT · `user_repo` 대역)로 본다. 210 연계 사본은
`hmget`만 있는 가짜 Redis, 상태 전이는 가짜 저장소, 시계열은 `zrangebyscore`를 더한 가짜 Redis다.
"""

from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application.biz_monitor_alarms import BizAlarmAggregator
from noise_gate.application.biz_monitor_overview import (
    FOLDED_LANE_CAP,
    build_state_band,
    build_topology,
)
from noise_gate.domain.apm_host_map import cache_value
from noise_gate.domain.biz_service_keys import apm_instance_member_key
from src.api.routes import biz_monitor
from tests.mocks.async_redis import FakeAsyncRedis
from tests.test_api.test_plan215_w6_biz_monitor import (
    ZA,
    ZB,
    _Cache,
    _Decisions,
    _hdr,
    _Incidents,
    _Users,
)

BASE = "/api/v1/biz-services/monitor"


class _TsRedis(FakeAsyncRedis):
    """가짜 Redis + ZSET 읽기(`zrangebyscore`) — 점수는 epoch 초."""

    def __init__(self) -> None:
        super().__init__()
        self.zsets: dict[str, list[tuple[float, str]]] = {}
        self.zcalls = 0

    async def zrangebyscore(self, name: str, min: Any, max: Any) -> list[str]:  # noqa: A002
        self.zcalls += 1
        lo = float(min)
        hi = float("inf") if max == "+inf" else float(max)
        return [m for s, m in sorted(self.zsets.get(name, [])) if lo <= s <= hi]


class _MapRedis:
    """210 연계 사본 대역 — `apmhostmap:{source}` 해시 · HMGET 호출 수를 센다."""

    def __init__(self, hashes: dict[str, dict[str, str]], *, fail: bool = False) -> None:
        self.hashes = hashes
        self.fail = fail
        self.calls: list[str] = []

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        self.calls.append(key)
        if self.fail:
            raise ConnectionError("redis down")
        return [self.hashes.get(key, {}).get(f) for f in fields]


class _Repo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls = 0

    async def list_state_log(
        self, service_id: int, *, since: datetime | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        self.calls += 1
        out = [
            r
            for r in self.rows
            if r["service_id"] == service_id
            and (since is None or datetime.fromisoformat(r["at"]) >= since)
        ]
        out.sort(key=lambda r: r["at"], reverse=True)
        return out[:limit]


def _sms(mid: int, rid: str, host: str, zone: str, layer: str | None) -> dict[str, Any]:
    return {
        "id": mid,
        "kind": "sms_server",
        "member_key": f"pg|{rid}",
        "db_id": "pg",
        "resource_id": rid,
        "hostname": host,
        "server_name": host,
        "zone": zone,
        "layer": layer,
    }


def _apm(mid: int, name: str, zone: str, layer: str | None = "was") -> dict[str, Any]:
    return {
        "id": mid,
        "kind": "apm_instance",
        "member_key": apm_instance_member_key("jen1", "100", "instance", name),
        "source_id": "jen1",
        "domain_id": "100",
        "instance_name": name,
        "zone": zone,
        "layer": layer,
    }


def _target(rid: str, host: str, *, gone: bool = False) -> dict[str, Any]:
    return {
        "kind": "sms",
        "db_id": "pg",
        "resource_id": rid,
        "server_name": host,
        "target_gone": gone,
    }


def _map_row(
    mid: int,
    name: str,
    targets: list[dict[str, Any]],
    *,
    origin: str = "manual",
    verified: bool = False,
    state: str = "active",
) -> tuple[str, str]:
    row = {
        "id": mid,
        "origin": origin,
        "basis": "manual" if origin == "manual" else "node_address",
        "state": state,
        "verified": verified,
        "targets": targets,
        "version": 1,
    }
    return f"instance|100|{name}", json.dumps(cache_value(row), ensure_ascii=False)


def _services() -> dict[int, dict[str, Any]]:
    return {
        1: {
            "id": 1,
            "service_key": "loan",
            "display_name": "여신",
            "tier": 1,
            "monitored": True,
            "verified": True,
            "origin": "auto",
            "members": [
                _sms(11, "101", "loan-web", ZA, "web"),
                _sms(12, "102", "loan-was", ZA, "was"),
                _sms(13, "103", "loan-db", ZB, "db"),
                _sms(14, "104", "loan-misc", ZA, None),
                _apm(21, "was-a", ZA),  # 수동 확정 → 실선
                _apm(22, "was-b", ZA),  # 자동 → 추정 선
                _apm(23, "was-c", ZA),  # 대상 사라짐 → 끊김
                _apm(24, "was-d", ZA),  # 매핑 없음
                _apm(25, "was-e", ZA),  # disabled
                _apm(26, "was-f", ZA),  # 권한 밖 서버(ZB loan-db)로
            ],
        },
        2: {
            "id": 2,
            "service_key": "card",
            "display_name": "카드",
            "tier": 2,
            "monitored": False,
            "members": [_sms(31, "201", "card-01", ZA, "web")],
        },
    }


def _map_hashes() -> dict[str, dict[str, str]]:
    rows = [
        _map_row(1, "was-a", [_target("102", "loan-was")]),
        _map_row(2, "was-b", [_target("102", "loan-was")], origin="auto"),
        _map_row(
            3, "was-c", [_target("104", "loan-misc", gone=True)], origin="auto", verified=True
        ),
        _map_row(5, "was-e", [_target("102", "loan-was")], state="disabled"),
        _map_row(6, "was-f", [_target("103", "loan-db"), _target("999", "elsewhere")]),
    ]
    return {"apmhostmap:jen1": dict(rows)}


def _app(
    *,
    metrics: bool = True,
    map_cache: Any = "default",
    services: dict[int, dict[str, Any]] | None = None,
    repo: _Repo | None = None,
) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.include_router(biz_monitor.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret="auth-secret-215w6-0123456789abcdef0123"),
        admin=SimpleNamespace(jwt_secret="admin-secret-215w6-0123456789abcdef012"),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(
            biz_monitor_active=True,
            biz_monitor_metrics_active=metrics,
            biz_monitor_alarm_window_minutes=60,
        ),
    )
    app.state.user_repo = _Users()
    redis = _TsRedis()
    cache = _Cache(services if services is not None else _services(), redis)
    app.state.biz_service_cache = cache
    if map_cache == "default":
        map_cache = SimpleNamespace(redis=_MapRedis(_map_hashes()))
    if map_cache is not None:
        app.state.apm_host_map_cache = map_cache
    app.state.biz_service_repo = repo
    app.state.biz_monitor_alarms = BizAlarmAggregator(
        cache=cache,
        incident_store=_Incidents(),
        decision_store=_Decisions([]),
        window_minutes=60,
        settings_loader=lambda: SimpleNamespace(
            config=SimpleNamespace(sms_thresholds={}), generic_terms=frozenset()
        ),
    )
    return TestClient(app), app


def _nodes(body: dict[str, Any]) -> dict[str, list[str]]:
    return {lane["layer"]: [n["name"] for n in lane["nodes"]] for lane in body["lanes"]}


# ─── 구성 맵 ────────────────────────────────────────────────────────────


def test_topology_lanes_order_and_unassigned_lane() -> None:
    client, _ = _app()
    res = client.get(f"{BASE}/1/topology", headers=_hdr("a"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "generated_at",
        "service_id",
        "member_count",
        "hidden_member_count",
        "folded",
        "lanes",
        "edges",
        "folded_edge_count",
        "links_status",
    }
    assert [(lane["layer"], lane["label"]) for lane in body["lanes"]] == [
        ("web", "웹"),
        ("was", "WAS"),
        ("db", "DB"),
        ("batch", "배치"),
        ("etc", "기타"),
        ("none", "미지정"),
    ]
    lanes = _nodes(body)
    assert lanes["web"] == ["loan-web"]
    assert lanes["db"] == ["loan-db"]
    assert lanes["none"] == ["loan-misc"]  # 계층 미지정 → 「미지정」 레인
    assert lanes["was"][0] == "loan-was"  # 같은 상태면 SMS 서버 먼저 → 이름 순
    assert lanes["was"][1:] == ["was-a", "was-b", "was-c", "was-d", "was-e", "was-f"]
    assert body["folded"] is False and body["links_status"] == {"available": True, "reason": ""}


def test_topology_edges_estimated_broken_outside_and_notes() -> None:
    client, app = _app()
    body = client.get(f"{BASE}/1/topology", headers=_hdr("a")).json()
    edges = {(e["from"], e["to"]): e for e in body["edges"]}
    assert set(edges) == {(21, 12), (22, 12), (23, 14), (26, 13)}
    assert edges[(21, 12)]["estimated"] is False and edges[(21, 12)]["broken"] is False
    assert edges[(22, 12)]["estimated"] is True  # 자동·미확인 = 추정 선
    assert edges[(23, 14)]["broken"] is True  # 대상 사라짐 = 끊김
    assert edges[(23, 14)]["estimated"] is False  # 자동이지만 관리자 확인
    assert "자동 · 추정" in edges[(22, 12)]["label"]
    nodes = {n["id"]: n for lane in body["lanes"] for n in lane["nodes"]}
    assert nodes[24]["link_note"] == "연계 매핑 없음"
    assert nodes[25]["link_note"] == "연계 없음(관리자 지정)"
    assert nodes[26]["outside_targets"] == 1  # 서비스 밖 서버는 건수만
    assert app.state.apm_host_map_cache.redis.calls == ["apmhostmap:jen1"]  # 소스당 1회


def test_topology_zone_filter_hides_names_and_lines() -> None:
    client, _ = _app()
    body = client.get(f"{BASE}/1/topology", headers=_hdr("u")).json()
    text = json.dumps(body, ensure_ascii=False)
    assert "loan-db" not in text
    assert body["hidden_member_count"] == 1
    nodes = {n["id"]: n for lane in body["lanes"] for n in lane["nodes"]}
    assert 13 not in nodes
    assert nodes[26]["outside_targets"] == 2  # 권한 밖 서버도 건수만
    assert all(e["to"] != 13 for e in body["edges"])


def test_topology_without_map_cache_draws_no_lines_with_reason() -> None:
    client, _ = _app(map_cache=None)
    body = client.get(f"{BASE}/1/topology", headers=_hdr("a")).json()
    assert body["edges"] == []
    assert body["links_status"]["available"] is False
    assert "연계 사본 없음" in body["links_status"]["reason"]


def test_topology_map_cache_failure_reason() -> None:
    client, _ = _app(map_cache=SimpleNamespace(redis=_MapRedis({}, fail=True)))
    body = client.get(f"{BASE}/1/topology", headers=_hdr("a")).json()
    assert body["edges"] == [] and "조회 실패" in body["links_status"]["reason"]


def test_topology_same_input_same_layout() -> None:
    """같은 입력(구성원 순서만 다름)이면 같은 배치 — 정렬 키 고정."""
    client, _ = _app()
    first = client.get(f"{BASE}/1/topology", headers=_hdr("a")).json()
    services = _services()
    random.Random(7).shuffle(services[1]["members"])
    client2, _ = _app(services=services)
    second = client2.get(f"{BASE}/1/topology", headers=_hdr("a")).json()
    first.pop("generated_at"), second.pop("generated_at")
    assert first == second


def test_topology_folds_lanes_over_sixty() -> None:
    members = [_sms(100 + i, str(1000 + i), f"web-{i:02d}", ZA, "web") for i in range(50)]
    members += [_sms(200 + i, str(2000 + i), f"db-{i:02d}", ZA, "db") for i in range(15)]
    detail = {
        "generated_at": "t",
        "service": {"id": 9},
        "hidden_member_count": 0,
        "members": [
            {**m, "name": m["server_name"], "state": "no_signal", "state_label": "이상 신호 없음"}
            for m in members
        ],
    }
    body = build_topology(detail, {})
    lanes = {lane["layer"]: lane for lane in body["lanes"]}
    assert body["folded"] is True
    assert len(lanes["web"]["nodes"]) == FOLDED_LANE_CAP and lanes["web"]["folded_count"] == 40
    assert len(lanes["db"]["nodes"]) == FOLDED_LANE_CAP and lanes["db"]["folded_count"] == 5
    assert [n["name"] for n in lanes["web"]["nodes"]][:2] == ["web-00", "web-01"]


def test_topology_worse_state_first_in_lane() -> None:
    detail = {
        "generated_at": "t",
        "service": {"id": 9},
        "hidden_member_count": 0,
        "members": [
            {
                "id": 1,
                "kind": "sms_server",
                "member_key": "pg|1",
                "name": "a",
                "layer": "web",
                "state": "no_signal",
            },
            {
                "id": 2,
                "kind": "sms_server",
                "member_key": "pg|2",
                "name": "b",
                "layer": "web",
                "state": "risk",
            },
            {
                "id": 3,
                "kind": "sms_server",
                "member_key": "pg|3",
                "name": "c",
                "layer": "WEB",
                "state": "caution",
            },
        ],
    }
    lanes = _nodes(build_topology(detail, {}))
    assert lanes["web"] == ["b", "c", "a"]


def test_topology_404_and_auth() -> None:
    client, _ = _app()
    assert client.get(f"{BASE}/1/topology").status_code == 401
    assert client.get(f"{BASE}/99/topology", headers=_hdr("a")).status_code == 404
    assert client.get(f"{BASE}/1/topology", headers=_hdr("n")).status_code == 404
    assert client.get(f"{BASE}/1/trend", headers=_hdr("n")).status_code == 404


# ─── 추이 ──────────────────────────────────────────────────────────────


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def test_trend_band_and_points() -> None:
    now = time.time()
    repo = _Repo(
        [
            {
                "service_id": 1,
                "from_state": "no_signal",
                "to_state": "caution",
                "at": _iso(now - 7200),
            },
            {"service_id": 1, "from_state": "caution", "to_state": "risk", "at": _iso(now - 3600)},
            {
                "service_id": 1,
                "from_state": None,
                "to_state": "no_signal",
                "at": _iso(now - 200000),
            },  # 창 밖
        ]
    )
    client, app = _app(repo=repo)
    redis = app.state.biz_service_cache.redis
    redis.zsets["bizmon:ts:1"] = [
        (now - 90000, json.dumps({"at": _iso(now - 90000), "state": "risk"})),  # 창 밖
        (
            now - 600,
            json.dumps(
                {
                    "at": _iso(now - 600),
                    "state": "risk",
                    "sms_max": {"cpu": 91.5},
                    "apm_max": {"error_rate": 2.0},
                }
            ),
        ),
        (
            now - 1200,
            json.dumps(
                {"at": _iso(now - 1200), "state": "caution", "sms_max": {"cpu": 70}, "apm_max": {}}
            ),
        ),
        (now - 300, "손상"),
    ]
    res = client.get(f"{BASE}/1/trend", headers=_hdr("a"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "generated_at",
        "service_id",
        "window_seconds",
        "since",
        "monitored",
        "hidden_member_count",
        "band",
        "series",
    }
    segs = body["band"]["segments"]
    assert [s["state"] for s in segs] == ["no_signal", "caution", "risk"]
    assert [s["state_label"] for s in segs] == ["이상 신호 없음", "주의", "위험"]
    assert body["band"]["reason"] == "" and body["band"]["truncated"] is False
    points = body["series"]["points"]
    assert [p["sms_max"].get("cpu") for p in points] == [70.0, 91.5]
    assert points[1]["apm_max"] == {"error_rate": 2.0}
    assert body["series"]["reason"] == ""


def test_trend_band_prior_state_when_no_transition_in_window() -> None:
    now = time.time()
    repo = _Repo(
        [{"service_id": 1, "from_state": None, "to_state": "caution", "at": _iso(now - 200000)}]
    )
    client, _ = _app(repo=repo)
    body = client.get(f"{BASE}/1/trend", headers=_hdr("a")).json()
    assert [s["state"] for s in body["band"]["segments"]] == ["caution"]
    assert repo.calls == 2


def test_trend_band_without_records_is_no_record_label() -> None:
    since, now = 1000.0, 2000.0
    segs = build_state_band([], None, since=since, now=now)
    assert segs == [
        {"start": _iso(since), "end": _iso(now), "state": None, "state_label": "기록 없음"}
    ]


def test_trend_unmonitored_and_metrics_off_reasons() -> None:
    client, app = _app(repo=_Repo([]))
    body = client.get(f"{BASE}/2/trend", headers=_hdr("a")).json()
    assert body["monitored"] is False and body["series"] == {
        "points": [],
        "reason": "감시 꺼짐 — 상태 띠만",
    }
    client, app = _app(metrics=False, repo=_Repo([]))
    body = client.get(f"{BASE}/1/trend", headers=_hdr("a")).json()
    assert body["series"]["reason"] == "지표 수집 꺼짐 — 알람 현황만"
    assert app.state.biz_service_cache.redis.zcalls == 0


def test_trend_without_repo_reason() -> None:
    client, _ = _app(repo=None)
    body = client.get(f"{BASE}/1/trend", headers=_hdr("a")).json()
    assert body["band"]["segments"] == [] and body["band"]["reason"] == "상태 전이 저장소 없음"


def test_trend_restricted_user_with_hidden_members_gets_reason_only() -> None:
    repo = _Repo([])
    client, app = _app(repo=repo)
    body = client.get(f"{BASE}/1/trend", headers=_hdr("u")).json()
    assert body["hidden_member_count"] == 1
    assert body["band"]["segments"] == [] and "권한 밖 구성원 1대" in body["band"]["reason"]
    assert body["series"]["points"] == [] and "권한 밖" in body["series"]["reason"]
    assert repo.calls == 0 and app.state.biz_service_cache.redis.zcalls == 0
    # 권한 밖 구성원이 없는 서비스는 제한 범위 사용자도 본다
    body2 = client.get(f"{BASE}/2/trend", headers=_hdr("u")).json()
    assert body2["band"]["reason"] == "" and repo.calls == 2  # 창 안 0건 → 직전 1건 조회


def test_trend_band_truncated_at_page_limit() -> None:
    now = time.time()
    rows = [
        {
            "service_id": 1,
            "from_state": "risk" if i % 2 else "caution",
            "to_state": "caution" if i % 2 else "risk",
            "at": _iso(now - 100 * (i + 1)),
        }
        for i in range(120)
    ]
    client, _ = _app(repo=_Repo(rows))
    body = client.get(f"{BASE}/1/trend", headers=_hdr("a")).json()
    assert body["band"]["truncated"] is True
    assert len(body["band"]["segments"]) == 100


# ─── 등록 조건 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("enabled", [False, True])
def test_overview_routes_only_when_active(enabled: bool) -> None:
    from src.api.server import create_app
    from src.config import AppConfig, AuthConfig, NoiseGateConfig

    app = create_app(
        AppConfig(
            db_backend="direct",
            db_connection_string="",
            log_level="WARNING",
            auth=AuthConfig(enabled=False),
            noise_gate=NoiseGateConfig(biz_service_enabled=True, biz_monitor_enabled=enabled),
        )
    )
    paths = set(app.openapi()["paths"])
    for sub in ("topology", "trend"):
        assert (f"{BASE}/{{service_id}}/{sub}" in paths) is enabled
