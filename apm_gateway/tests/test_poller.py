"""이벤트 폴러 — 커서 · 합성 멱등 키 · 정규화 · 오류 처리 (plans/87 §5.5 · J4 수용 기준 ·
SPEC-apm-gateway §5).

목 서버에 이벤트를 주입(`POST /__mock/events`)하고 Redis는 메모리 가짜로 대신한다. `alarm:raw`
레코드는 `noise_gate` 워커가 읽는 것과 같은 모양(`{"data": json}`)인지, 조사 트리거 필수
필드(`serverName`·`hostname`· `severity`)를 채우는지 **import 없이** 확인한다(R-21 — 계약 복제).
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime

import httpx
import pytest
from conftest import NOW_MS, NOW_S, TOKEN, make_cfg, synthetic_fixtures, write_fixtures

# 조사 트리거 계약(sre_agent investigation_jobs.REQUIRED_EVENT_FIELDS) — import 없이 복제.
REQUIRED_EVENT_FIELDS = ("serverName", "hostname", "severity")
# noise_gate 워커가 읽는 키(alarm_worker._process) — 복제.
WORKER_KEYS = (
    "dbId",
    "serverName",
    "hostname",
    "ipAddress",
    "resourceAncestry",
    "alarmId",
    "severity",
    "alarmStatus",
    "resourceType",
    "resourceName",
    "alarmName",
    "alarmTime",
    "conditions",
    "conditionLog",
)


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.stream: list[dict] = []
        self.fail_xadd = False

    async def get(self, key):
        value = self.kv.get(key)
        return value.encode() if isinstance(value, str) else value

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def delete(self, key):
        self.kv.pop(key, None)

    async def xadd(self, stream, fields):
        if self.fail_xadd:
            raise ConnectionError("redis down")
        self.stream.append({"stream": stream, **fields})


def _no_event_fixtures():
    return [fx for fx in synthetic_fixtures() if fx["request"]["template"] != "/api/dbsearch/event"]


def _inject(base: str, events: list[dict]) -> None:
    req = urllib.request.Request(
        f"{base}/__mock/events",
        data=json.dumps(events).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5).read()


def _event(**over) -> dict:
    base = {
        "domainId": 1000,
        "domainName": "demo-domain",
        "instanceId": 1001,
        "instanceName": "was01_a",
        "errorType": "ERROR_SERVICE_QUEUING",
        "metricsName": "",
        "eventLevel": "FATAL",
        "message": "queue full user=kim@example.com",
        "value": 12.0,
        "time": NOW_MS - 10_000,
        "txid": "77",
    }
    base.update(over)
    return base


def _poller(base: str, redis: FakeRedis, clock=lambda: NOW_S, transport=None, extra=None):
    from apm_gateway.application.poller import EventPoller
    from apm_gateway.application.sources import build_source_set

    cfg = make_cfg(base, extra={"APM_EVENT_POLLER_ENABLED": "true", **(extra or {})})
    return EventPoller(build_source_set(cfg, transport=transport), cfg, redis, clock=clock)


@pytest.fixture
def events_server(mock_server_factory, tmp_path):
    base, state = mock_server_factory(
        write_fixtures(tmp_path / "noev", _no_event_fixtures()), "connected"
    )
    return base


def _payloads(redis: FakeRedis) -> list[dict]:
    return [json.loads(rec["data"]) for rec in redis.stream]


@pytest.mark.asyncio
async def test_publishes_contract_payload(events_server):
    redis = FakeRedis()
    _inject(events_server, [_event()])
    poller = _poller(events_server, redis)
    assert await poller.poll_once() == 1
    rec = redis.stream[0]
    assert rec["stream"] == "alarm:raw" and set(rec) == {"stream", "data"}
    p = json.loads(rec["data"])
    assert set(WORKER_KEYS) <= set(p)
    assert all(p[f] not in (None, "") for f in REQUIRED_EVENT_FIELDS)
    assert (
        p["dbId"] == "jennifer"
        and p["source"] == "jennifer"
        and p["resourceType"] == "apm.Instance"
    )
    assert p["hostname"] == "was-host01.example.local" and p["serverName"] == p["hostname"]
    assert p["severity"] == 3 and p["alarmName"] == "ERROR_SERVICE_QUEUING"
    datetime.strptime(p["alarmTime"], "%Y%m%d%H%M%S")  # 워커 파서와 같은 형식
    assert p["alarmId"].startswith("jennifer:")
    assert "kim@example.com" not in p["conditionLog"]
    assert p["apm"]["instance_id"] == 1001 and p["apm"]["match_confidence"] == "high"
    assert p["apm"]["was_signals"][0]["kind"] == "was_service_queuing"
    assert TOKEN not in rec["data"]


@pytest.mark.asyncio
async def test_no_duplicates_on_repoll_and_restart(events_server):
    redis = FakeRedis()
    _inject(events_server, [_event()])
    first = _poller(events_server, redis)
    assert await first.poll_once() == 1
    assert await first.poll_once() == 0  # 경계 포함 재조회 — 멱등 키로 차단
    restarted = _poller(events_server, redis, clock=lambda: NOW_S + 5)
    assert await restarted.poll_once() == 0
    assert len(redis.stream) == 1 and first.duplicates_total == 1


@pytest.mark.asyncio
async def test_same_ms_events_and_metric_event(events_server):
    redis = FakeRedis()
    t = NOW_MS - 5_000
    _inject(
        events_server,
        [
            _event(time=t),
            _event(time=t, instanceId=1002, instanceName="was01_b"),
            _event(time=t, errorType="", metricsName="gc_time_usage", eventLevel="WARNING"),
        ],
    )
    poller = _poller(events_server, redis)
    assert await poller.poll_once() == 3
    metric = next(p for p in _payloads(redis) if p["apm"]["event_kind"] == "metric")
    assert metric["alarmName"] == "gc_time_usage" and metric["severity"] == 2
    assert len({p["alarmId"] for p in _payloads(redis)}) == 3


@pytest.mark.asyncio
async def test_min_level_filters_but_clear_passes(events_server):
    redis = FakeRedis()
    _inject(
        events_server,
        [
            _event(eventLevel="NORMAL", errorType="ERROR_X"),
            _event(eventLevel="RECOVERY", errorType="ERROR_Y"),
        ],
    )
    poller = _poller(events_server, redis)
    assert await poller.poll_once() == 1
    assert _payloads(redis)[0]["severity"] == 0


@pytest.mark.asyncio
async def test_unresolved_instance_published_with_empty_hostname(events_server):
    redis = FakeRedis()
    _inject(events_server, [_event(instanceId=1003, instanceName="api02_main")])
    poller = _poller(events_server, redis)
    assert await poller.poll_once() == 1
    p = _payloads(redis)[0]
    assert p["hostname"] == "" and p["serverName"] == "api02_main"
    assert p["apm"]["match_confidence"] == "none"


@pytest.mark.asyncio
async def test_cursor_advances_with_overlap(events_server):
    redis = FakeRedis()
    now = [NOW_S]
    poller = _poller(events_server, redis, clock=lambda: now[0])
    await poller.poll_once()
    # 첫 주기는 [지금 − 주기, 지금] — 커서는 뒤로 가지 않는다(max(시작, 끝 − 겹침 60초))
    assert int(redis.kv["apm_gateway:poller:cursor:default:1000"]) == NOW_MS - 30_000
    now[0] += 120
    await poller.poll_once()
    assert int(redis.kv["apm_gateway:poller:cursor:default:1000"]) == NOW_MS + 120_000 - 60_000


@pytest.mark.asyncio
async def test_disconnected_domain_keeps_cursor_and_backs_off(mock_server_factory, tmp_path):
    base, _ = mock_server_factory(write_fixtures(tmp_path / "d", _no_event_fixtures()), "connected")
    redis = FakeRedis()
    redis.kv["apm_gateway:poller:cursor:default:1000"] = str(NOW_MS - 90_000)
    now = [NOW_S]
    poller = _poller(base, redis, clock=lambda: now[0])
    await poller.sources.get("default").resolver.inventory()  # 도메인·인스턴스는 연결 상태에서 캐시
    urllib.request.urlopen(
        urllib.request.Request(
            f"{base}/__mock/mode", data=b'{"mode": "disconnected"}', method="POST"
        ),
        timeout=5,
    ).read()
    assert await poller.poll_once() == 0
    st = poller.status()["domains"]["default:1000"]
    assert st["state"] == "unavailable" and st["reason"] == "source_unavailable"
    assert redis.kv["apm_gateway:poller:cursor:default:1000"] == str(NOW_MS - 90_000)
    calls = poller.sources.calls_total
    now[0] += 30  # 백오프(주기 30초 × 2) 안 — 호출하지 않는다
    await poller.poll_once()
    assert poller.sources.calls_total == calls


@pytest.mark.asyncio
async def test_contract_violation_stops_domain():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/domain":
            return httpx.Response(200, json={"result": [{"domainId": 1000, "name": "d"}]})
        if path == "/api/instance":
            return httpx.Response(200, json={"result": []})
        return httpx.Response(
            500, json={"exception": {"message": "Required request parameter 'start_time'"}}
        )

    redis = FakeRedis()
    poller = _poller("http://apm.test", redis, transport=httpx.MockTransport(handler))
    await poller.poll_once()
    assert poller.status()["domains"]["default:1000"]["state"] == "stopped"
    calls = poller.sources.calls_total
    await poller.poll_once()
    assert poller.sources.calls_total == calls  # 캐시된 인벤토리 · 중지 도메인은 부르지 않는다


@pytest.mark.asyncio
async def test_xadd_failure_retries_next_cycle(events_server):
    redis = FakeRedis()
    redis.fail_xadd = True
    _inject(events_server, [_event()])
    poller = _poller(events_server, redis)
    assert await poller.poll_once() == 0
    assert "apm_gateway:poller:cursor:default:1000" not in redis.kv  # 커서 유지
    redis.fail_xadd = False
    assert await poller.poll_once() == 1
    assert len(redis.stream) == 1
