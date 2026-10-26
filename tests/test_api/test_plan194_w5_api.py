"""plans/194 W5 — 조사 부하 게이지·판정 행 조사 상태·거부 가산 키·관리자 요약 (D-347 · D-335 ⑥).

- C1 `service_load`: 조사 서비스 `sre_health` 부하 게이지(앱 캐시 10초 · 실패·구버전 null)가
  `/active` 최상위 · by-alarm · 스트림 `summary` 프레임(`{items, service_load}`)에 실린다
- C2 판정 목록 행 `investigation`: 그 알람의 가장 최근 거부 아닌 조사 · 열람 범위 = by-alarm
- C3 거부 가산 키: 같은 서버 진행 중이면 열 수 있는 `existing_investigation_id` · 시간당 상한 429면
  `detail.retry_after_seconds`
- Q-4: 시간당 상한은 실제 접수만 센다(조사 서비스 거부 기록 제외)
- C5 관리자 요약 `GET /admin/investigation/summary`

조사 서비스는 띄우지 않는다 — 헬스는 실 응답 shape(sre `load_snapshot` 키)의 가짜 조회 함수로,
접수는 가짜 클라이언트로 대신한다. 실 LLM·외부 호출 0.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import noise_gate.infrastructure.sre_agent_client as sre_client_mod
from src.api import investigation_service_health as health_mod
from src.api.dependencies import require_admin_user
from src.api.investigation_service_health import NO_GAUGE_REASON, ServiceHealthCache
from src.api.routes import admin_investigation
from src.infrastructure import investigation_request_limit as limit_mod
from tests.test_api import test_plan187_w11_manual as w11
from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_client,
    _make_config,
    _make_user_client,
    _seed_decisions,
)
from tests.test_api.test_plan187_w14_live_api import (  # noqa: F401 — 픽스처 재사용
    _app,
    _close_hubs,
    _frames,
    _get,
    _of,
    _short_stream,
    _stream,
    fake_sre,
)
from tests.test_api.test_plan187_w14_live_api import _config as _live_config
from tests.test_api.test_plan187_w14_live_hub import FakeJob, seed

# 픽스처(autouse 포함) 재사용 — 판정 기록 hostname 해소·감사 가짜·poll 상태 초기화
audits = w11.audits
_hostname = w11._hostname
_reset_poll_state = w11._reset_poll_state

GONGJON_DB = w11.GONGJON_DB
BANKJON_DB = w11.BANKJON_DB
ADMIN = w11.ADMIN
U1 = w11.GONGJON_USER

#: sre_agent `sre_health` 실 응답 shape(기본 키 + `load_snapshot` 가산 키 — plans/194 W3·W5).
HEALTH: dict[str, Any] = {
    "status": "ok",
    "version": "0.9.0",
    "contract_version": "1",
    "holmes_ready": True,
    "running": 1,
    "queued": 2,
    "abandoned_slots": 0,
    "abandoned_threads": 1,
    "max_concurrent": 2,
    "max_queued": None,
    "abandon_grace_seconds": 30.0,
    "tool_global_concurrency": 4,
    "hourly_budget": 20,
}
LOAD_VIEW = {
    "running": 1, "queued": 2, "max_concurrent": 2, "max_queued": None, "abandoned_slots": 0,
}


class _Fetch:
    """헬스 조회 가짜 — 응답 차례대로(마지막 값 유지) · 호출 수 기록."""

    def __init__(self, *results: tuple[dict | None, str | None]) -> None:
        self.results = list(results) or [(dict(HEALTH), None)]
        self.calls = 0

    async def __call__(self, ng: Any) -> tuple[dict | None, str | None]:
        self.calls += 1
        return self.results[min(self.calls, len(self.results)) - 1]


def _use_health(app: FastAPI, fetch: _Fetch, **kw: Any) -> None:
    app.state.investigation_service_health = ServiceHealthCache(
        app.state.config.noise_gate, fetch=fetch, **kw
    )


# ── 헬스 캐시·조회 ──────────────────────────────────────────────────────────
class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def _drain(cache: ServiceHealthCache) -> None:
    task = cache._refresh_task
    if task is not None:
        await task


async def test_cache_reuses_within_ttl_and_serves_stale_while_refreshing() -> None:
    clock = _Clock()
    busier = {**HEALTH, "running": 2}
    fetch = _Fetch((dict(HEALTH), None), (busier, None))
    cache = ServiceHealthCache(SimpleNamespace(), clock=clock, fetch=fetch)
    assert await cache.service_load() == LOAD_VIEW  # 첫 조회는 기다린다
    clock.now += 9.9
    await asyncio.gather(cache.snapshot(), cache.snapshot())
    assert fetch.calls == 1
    clock.now += 0.2
    # 만료 — 직전 값을 바로 주고 백그라운드 갱신은 하나만
    stale = await asyncio.gather(cache.service_load(), cache.service_load())
    assert stale == [LOAD_VIEW, LOAD_VIEW]
    await _drain(cache)
    assert fetch.calls == 2
    assert (await cache.service_load())["running"] == 2


async def test_hanging_refresh_does_not_block_readers() -> None:
    clock = _Clock()
    gate = asyncio.Event()
    calls = 0

    async def _fetch(ng: Any) -> tuple[dict | None, str | None]:
        nonlocal calls
        calls += 1
        if calls > 1:
            await gate.wait()  # TCP는 받고 응답을 붙드는 조사 서비스
        return dict(HEALTH), None

    cache = ServiceHealthCache(SimpleNamespace(), clock=clock, fetch=_fetch)
    await cache.snapshot()
    clock.now += 11
    for _ in range(3):
        snap = await asyncio.wait_for(cache.snapshot(), timeout=0.5)
        assert snap.health == HEALTH
        await asyncio.sleep(0)  # 백그라운드 갱신이 돌 기회
    assert calls == 2  # 붙든 갱신 1건만
    gate.set()
    await _drain(cache)


async def test_failure_is_cached_and_logged_once(caplog) -> None:  # noqa: ANN001
    clock = _Clock()
    fetch = _Fetch((None, "조사 서비스에 연결할 수 없습니다: refused"))
    cache = ServiceHealthCache(SimpleNamespace(), clock=clock, fetch=fetch)
    with caplog.at_level("WARNING", logger=health_mod.__name__):
        snap = await cache.snapshot()
        await cache.snapshot()  # 캐시 안 — 다시 묻지 않는다
        clock.now += 11
        await cache.snapshot()  # 직전 실패를 주고 백그라운드로 다시 묻는다
        await _drain(cache)  # 같은 사유 — 로그를 다시 남기지 않는다
    assert snap.health is None and snap.error and snap.checked_at.endswith("+00:00")
    assert await cache.service_load() is None
    assert fetch.calls == 2
    assert len([r for r in caplog.records if "부하 게이지" in r.getMessage()]) == 1


async def test_fetch_exception_becomes_reason() -> None:
    async def _boom(ng: Any) -> Any:
        raise RuntimeError("x")

    snap = await ServiceHealthCache(SimpleNamespace(), fetch=_boom).snapshot()
    assert snap.health is None and "RuntimeError" in (snap.error or "")


def test_service_load_view_shapes() -> None:
    assert sre_client_mod.service_load_view(dict(HEALTH)) == LOAD_VIEW
    # 구버전(부하 키 없음)·실행기 미구성은 null
    assert sre_client_mod.service_load_view({"status": "ok", "version": "0.1"}) is None
    assert sre_client_mod.service_load_view({"running": True, "queued": 0}) is None
    assert sre_client_mod.service_load_view(None) is None


class _HealthClient:
    def __init__(self, *, unreachable: str | None = None, delay: float = 0.0,
                 error: Exception | None = None) -> None:
        self.unreachable, self.delay, self.error = unreachable, delay, error
        self.connected = self.disconnected = 0

    async def unreachable_reason(self) -> str | None:
        return self.unreachable

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def health(self) -> dict:
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return dict(HEALTH)


@pytest.mark.parametrize(
    "client, ok, reason_part",
    [
        (_HealthClient(), True, None),
        (_HealthClient(unreachable="refused"), False, "연결할 수 없습니다"),
        (_HealthClient(delay=1.0), False, "타임아웃"),
        (_HealthClient(error=RuntimeError("bad")), False, "RuntimeError"),
        (None, False, "URL"),
    ],
)
async def test_fetch_health_outcomes(monkeypatch, client, ok, reason_part) -> None:  # noqa: ANN001
    monkeypatch.setattr(sre_client_mod, "_new_client", lambda gate_cfg: client)
    built: list[Any] = []
    # 헬스 조회는 접수 경로 공유 팩토리를 쓰지 않는다
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda g: built.append(g))
    health, reason = await sre_client_mod.fetch_health(SimpleNamespace(), timeout=0.2)
    assert (health == HEALTH) is ok
    assert (reason is None) is ok and (reason_part is None or reason_part in reason)
    assert built == []
    if client is not None and client.unreachable is None:
        assert client.connected == client.disconnected == 1  # 실패·시간 초과에도 닫는다


# ── C1: by-alarm · /active · 스트림 summary ─────────────────────────────────
def test_by_alarm_carries_service_load(tmp_path) -> None:  # noqa: ANN001
    w11._seed_alarm(tmp_path)
    w11._seed_auto(tmp_path)
    client = w11._client(w11._config(tmp_path), U1)
    fetch = _Fetch()
    _use_health(client.app, fetch)
    body = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    assert body["service_load"] == LOAD_VIEW and body["investigation_id"] == "inv-auto"
    admin = w11._client(client.app.state.config, ADMIN)
    admin.app.state.investigation_service_health = client.app.state.investigation_service_health
    none = admin.get("/api/v1/alarm/investigations/by-alarm/zz").json()
    assert none["status"] == "none" and none["service_load"] == LOAD_VIEW
    assert fetch.calls == 1  # 10초 캐시


def test_by_alarm_old_service_gives_null(tmp_path) -> None:  # noqa: ANN001
    w11._seed_alarm(tmp_path)
    client = w11._client(w11._config(tmp_path), U1)
    _use_health(client.app, _Fetch(({"status": "ok", "version": "0.1"}, None)))
    assert client.get("/api/v1/alarm/investigations/by-alarm/a1").json()["service_load"] is None


def test_default_cache_uses_fetch_health_once(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    fetch = _Fetch()
    monkeypatch.setattr(sre_client_mod, "fetch_health", fetch)
    w11._seed_alarm(tmp_path)
    client = w11._client(w11._config(tmp_path), U1)
    for _ in range(3):
        body = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
        assert body["service_load"] == LOAD_VIEW
    assert fetch.calls == 1


async def test_active_top_level_service_load(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    seed(tmp_path, "auto-1", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    fake_sre.jobs["auto-1"] = FakeJob()
    app = _app(_live_config(tmp_path), ADMIN)
    _use_health(app, _Fetch())
    body = (await _get(app, "/alarm/investigations/active")).json()
    assert body["service_load"] == LOAD_VIEW
    assert [i["investigation_id"] for i in body["items"]] == ["auto-1"]


async def test_stream_summary_frame_resends_on_gauge_change(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    seed(tmp_path, "auto-1", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    fake_sre.jobs["auto-1"] = FakeJob()
    app = _app(_live_config(tmp_path), ADMIN)
    busier = {**HEALTH, "running": 2, "queued": 0}
    _use_health(app, _Fetch((dict(HEALTH), None), (busier, None)), ttl=0)
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    summaries = [f["data"] for f in _of(frames, "summary")]
    assert all(set(s) == {"items", "service_load"} for s in summaries)
    # 목록이나 게이지가 바뀐 때만 다시 보낸다(같은 프레임 반복 없음) — 게이지는 직전 값을 먼저 주고
    # 백그라운드 갱신 뒤 새 값(stale-while-revalidate)
    assert all(a != b for a, b in zip(summaries, summaries[1:]))
    running = [s["service_load"]["running"] for s in summaries]
    assert [k for k, _ in itertools.groupby(running)] == [1, 2]
    assert {i["investigation_id"] for i in summaries[-1]["items"]} == {"auto-1"}


async def test_stream_summary_null_gauge_when_service_down(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    app = _app(_live_config(tmp_path), ADMIN)
    _use_health(app, _Fetch((None, "조사 서비스에 연결할 수 없습니다: refused")))
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    assert [f["data"] for f in _of(frames, "summary")] == [{"items": [], "service_load": None}]


@pytest.mark.parametrize("res, expected", [
    ({"status": "running", "phase": "queued", "queue_position": 2}, 2),
    ({"status": "running"}, None),  # 대기 중이 아니면 조사 서비스가 키를 싣지 않는다
    ({"status": "running", "queue_position": True}, None),
    ({"status": "done", "phase": "queued", "queue_position": 2,
      "briefing": {"summary": "s"}}, None),  # 종결이면 싣지 않는다
])
def test_by_alarm_delayed_poll_passes_queue_position(tmp_path, monkeypatch, res, expected) -> None:  # noqa: ANN001, E501
    from tests.test_api.test_plan153_w2_investigation_api import _PollClient

    w11._seed_alarm(tmp_path)
    seed(tmp_path, "inv-q", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    fake = _PollClient(res)
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda ng: fake)
    client = w11._client(w11._config(tmp_path), U1)
    _use_health(client.app, _Fetch())
    first = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    again = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()  # 간격 안 — 직전 poll 값
    assert first.get("queue_position") == again.get("queue_position") == expected
    # 실행 단계도 미종결일 때만 함께 싣는다(카드 「대기 중 · 순번 N」 판정)
    assert first.get("phase") == again.get("phase") == ("queued" if expected else None)
    assert ("queue_position" in first) is (expected is not None)
    assert fake.poll_calls == ["inv-q"]  # 추가 호출 없음(지연 poll 1회)


# ── C2: 판정 목록 행 investigation ──────────────────────────────────────────
def _dash_config(tmp_path, *, manual: bool = True) -> SimpleNamespace:  # noqa: ANN001
    config = _make_config(tmp_path)
    ng = config.noise_gate
    ng.investigation_store_path = str(tmp_path / "inv.jsonl")
    ng.investigation_store_max_lines = 2000
    ng.investigation_manual_enabled = manual
    ng.investigation_additional_enabled = False
    config.host_authz = SimpleNamespace(mode="admin_only")
    return config


def _seed_dashboard(tmp_path) -> None:  # noqa: ANN001
    rows = [("a1", GONGJON_DB), ("a2", BANKJON_DB), ("a3", GONGJON_DB), ("a4", GONGJON_DB),
            ("a5", GONGJON_DB), ("a6", BANKJON_DB)]
    _seed_decisions(
        tmp_path, [_decision_record(alarm_id=a, db_id=d, fingerprint=a) for a, d in rows]
    )
    seed(tmp_path, "inv-1", status="running", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    seed(tmp_path, "inv-2", status="done", alarm_ids=["a2"], alarm_db_ids={"a2": BANKJON_DB})
    seed(tmp_path, "inv-3", status="rejected", alarm_ids=["a3"], alarm_db_ids={"a3": GONGJON_DB})
    seed(tmp_path, "inv-5a", status="done", alarm_ids=["a5"], alarm_db_ids={"a5": GONGJON_DB})
    seed(tmp_path, "inv-5b", status="failed", alarm_ids=["a5"], alarm_db_ids={"a5": GONGJON_DB})
    # 남의 존 알람이지만 내가 직접 접수한 분석(kind=manual) — 소유자 열람
    seed(tmp_path, "inv-6", status="submitted", kind="manual", owner_sub="u1", origin="manual",
         alarm_ids=["a6"], alarm_db_ids={"a6": BANKJON_DB}, parent_alarm_id="a6")


def _by_alarm(resp) -> dict[str, Any]:  # noqa: ANN001
    assert resp.status_code == 200, resp.text
    return {item["alarm_id"]: item["investigation"] for item in resp.json()["items"]}


def _inv(inv: str, status: str) -> dict[str, Any]:
    return {"investigation_id": inv, "status": status, "queue_position": None}


def test_resolved_records_merge_link_rows_once(tmp_path) -> None:  # noqa: ANN001
    store = w11._store(tmp_path)
    store.record(investigation_id="inv-ep", alarm_ids=["a1"], kind="episode", status="submitted",
                 source="t", episode_id="ep-1", alarm_db_ids={"a1": GONGJON_DB})
    store.record(investigation_id="inv-new", alarm_ids=["a1"], status="done", source="t",
                 alarm_db_ids={"a1": GONGJON_DB})
    store.record(investigation_id="inv-ep", alarm_ids=["b1"], kind="episode",
                 status="episode_existing", source="t", episode_id="ep-1",
                 alarm_db_ids={"b1": BANKJON_DB})
    got = store.resolved_records()
    # 연결 행이 붙어도 오래된 사건 조사는 뒤로 가지 않는다(상태 행 위치 순)
    assert [r["investigation_id"] for r in got] == ["inv-ep", "inv-new"]
    assert got[0]["alarm_ids"] == ["a1", "b1"] and got[0]["status"] == "submitted"
    assert got[0]["alarm_db_ids"] == {"a1": GONGJON_DB, "b1": BANKJON_DB}
    assert got[0] == store.get_by_investigation("inv-ep")


def test_user_rows_follow_by_alarm_visibility(tmp_path) -> None:  # noqa: ANN001
    _seed_dashboard(tmp_path)
    resp = _make_user_client(_dash_config(tmp_path), user=U1).get("/api/v1/noise/decisions")
    assert _by_alarm(resp) == {
        "a1": _inv("inv-1", "running"),
        "a2": None,  # 남의 존(존재도 드러내지 않는다)
        "a3": None,  # 조사 서비스가 거부한 기록은 「조사 안 됨」
        "a4": None,
        "a5": _inv("inv-5b", "failed"),  # 가장 최근
        "a6": _inv("inv-6", "submitted"),
    }


def test_operator_rows_see_all(tmp_path) -> None:  # noqa: ANN001
    _seed_dashboard(tmp_path)
    resp = _make_client(_dash_config(tmp_path)).get("/api/v1/admin/noise/decisions")
    got = _by_alarm(resp)
    assert got["a2"] == _inv("inv-2", "done") and got["a6"] == _inv("inv-6", "submitted")
    assert got["a3"] is None and got["a4"] is None


def test_manual_flag_off_hides_manual_records(tmp_path) -> None:  # noqa: ANN001
    _seed_dashboard(tmp_path)
    resp = _make_client(_dash_config(tmp_path, manual=False)).get("/api/v1/admin/noise/decisions")
    assert _by_alarm(resp)["a6"] is None


def test_no_store_path_keeps_rows_unchanged(tmp_path) -> None:  # noqa: ANN001
    _seed_decisions(tmp_path, [_decision_record()])
    resp = _make_client(_make_config(tmp_path)).get("/api/v1/admin/noise/decisions")
    items = resp.json()["items"]
    assert items and all("investigation" not in item for item in items)


def test_other_zone_user_sees_only_own_zone(tmp_path) -> None:  # noqa: ANN001
    _seed_dashboard(tmp_path)
    other = {"sub": "u7", "role": "user", "alarm_zones": [w11.ZONE_BANKJON]}
    resp = _make_user_client(_dash_config(tmp_path), user=other).get("/api/v1/noise/decisions")
    got = _by_alarm(resp)
    assert got["a1"] is None and got["a5"] is None
    # 은행존 알람 조사는 존 검사로 열린다(남이 접수한 분석이어도 — by-alarm과 같다)
    assert got["a2"] == _inv("inv-2", "done") and got["a6"] == _inv("inv-6", "submitted")


# ── C3: 거부 가산 키 · Q-4 ─────────────────────────────────────────────────
def _seed_in_flight(tmp_path, *, db_id: str, status: str = "running") -> None:  # noqa: ANN001
    _seed_decisions(tmp_path, [_decision_record(alarm_id="a2", db_id=db_id, server_name="WEB-01")])
    seed(tmp_path, "inv-old", status=status, alarm_ids=["a2"], alarm_db_ids={"a2": db_id})


def _in_flight_client() -> w11._FakeClient:
    return w11._FakeClient(status="rejected", reason="host_investigation_in_flight")


def test_host_in_flight_reveals_openable_existing_id(tmp_path, monkeypatch, audits) -> None:  # noqa: ANN001
    w11._seed_alarm(tmp_path)  # a1 · WEB-01 · 공동존
    _seed_in_flight(tmp_path, db_id=GONGJON_DB)
    w11._inject(monkeypatch, _in_flight_client())
    body = w11._post(w11._client(w11._config(tmp_path), U1)).json()
    assert body["status"] == "rejected" and body["reason"] == "host_investigation_in_flight"
    assert body["existing_investigation_id"] == "inv-old" and body["reason_text"]


@pytest.mark.parametrize("db_id, status", [(BANKJON_DB, "running"), (GONGJON_DB, "done")])
def test_host_in_flight_hides_unopenable_or_finished(tmp_path, monkeypatch, audits, db_id, status) -> None:  # noqa: ANN001, E501
    w11._seed_alarm(tmp_path)
    _seed_in_flight(tmp_path, db_id=db_id, status=status)
    w11._inject(monkeypatch, _in_flight_client())
    body = w11._post(w11._client(w11._config(tmp_path), U1)).json()
    assert body["status"] == "rejected" and "existing_investigation_id" not in body


def _seed_mine(tmp_path, status: str, minutes_ago: list[int]) -> None:  # noqa: ANN001
    store = w11._store(tmp_path)
    for i, ago in enumerate(minutes_ago):
        store.record(
            investigation_id=f"mine-{status}-{i}", alarm_ids=[f"x{i}"], status=status,
            source="manual", kind="manual", owner_sub="u1", origin="manual",
            parent_alarm_id=f"x{i}", ts=datetime.now(UTC) - timedelta(minutes=ago),
        )


def test_hourly_limit_429_carries_retry_after(tmp_path, monkeypatch, audits) -> None:  # noqa: ANN001
    w11._seed_alarm(tmp_path)
    _seed_mine(tmp_path, "done", [50, 40, 30])
    fake = w11._FakeClient()
    w11._inject(monkeypatch, fake)
    resp = w11._post(w11._client(w11._config(tmp_path), U1))
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["code"] == "hourly_limit" and detail["reason"]
    assert 590 <= detail["retry_after_seconds"] <= 601  # 가장 오래된 접수(50분 전)가 빠질 때까지
    assert fake.submit_calls == []


def test_rejected_records_do_not_count_toward_hourly_limit(tmp_path, monkeypatch, audits) -> None:  # noqa: ANN001
    w11._seed_alarm(tmp_path)
    _seed_mine(tmp_path, "rejected", [30, 20, 10])
    fake = w11._FakeClient()
    w11._inject(monkeypatch, fake)
    resp = w11._post(w11._client(w11._config(tmp_path), U1))
    assert resp.status_code == 200 and resp.json()["status"] != "rejected"
    assert len(fake.submit_calls) == 1
    # C4: 새 레코드의 subject는 목록 「대상」 칸용 표시 이름을 싣는다
    rec = w11._store(tmp_path).get_by_investigation(resp.json()["investigation_id"])
    assert rec is not None and rec["subject"]["label"] == "WEB-01 · CPU 사용률 임계 초과"


def test_hourly_retry_after_uses_window_and_ignores_rejected(tmp_path) -> None:  # noqa: ANN001
    ng = SimpleNamespace(investigation_additional_user_hourly_limit=3)
    store = w11._store(tmp_path)
    assert limit_mod.hourly_retry_after(store, ng, "u1") is None
    _seed_mine(tmp_path, "rejected", [59])
    _seed_mine(tmp_path, "done", [55, 50, 40, 30])
    retry = limit_mod.hourly_retry_after(store, ng, "u1")
    # 4건 중 상한 3 — 두 번째로 오래된(50분 전) 접수가 빠져야 한 건이 빈다
    assert retry is not None and 590 <= retry <= 601
    assert limit_mod.hourly_retry_after(store, ng, "") is None
    assert limit_mod.hourly_retry_after(
        store, SimpleNamespace(investigation_additional_user_hourly_limit=0), "u1"
    ) is None


# ── C5: 관리자 요약 ────────────────────────────────────────────────────────
def _admin_client(config, user: dict | None = ADMIN, fetch: _Fetch | None = None) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(admin_investigation.router, prefix="/api/v1")
    app.state.config = config
    if user is None:
        def _deny() -> None:
            raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

        app.dependency_overrides[require_admin_user] = _deny
    else:
        app.dependency_overrides[require_admin_user] = lambda: user
    _use_health(app, fetch or _Fetch())
    return TestClient(app)


_SUMMARY = "/api/v1/admin/investigation/summary"


def test_admin_summary_shape(tmp_path) -> None:  # noqa: ANN001
    config = w11._config(tmp_path, investigation_trigger_enabled=False)
    body = _admin_client(config).get(_SUMMARY).json()
    assert body["mode"] == {"auto_enabled": False, "manual_enabled": True}
    assert body["limits"] == {
        "max_concurrent": 2, "max_queued": None, "per_user_active": 1, "per_user_hourly": 3,
        "tool_global_concurrency": 4, "hourly_budget": 20,
    }
    assert body["load"] == {"running": 1, "queued": 2, "abandoned_slots": 0, "abandoned_threads": 1}
    assert body["service_error"] is None and body["checked_at"]
    json.dumps(body)  # 직렬화 가능


def test_admin_summary_service_down_and_old_version(tmp_path) -> None:  # noqa: ANN001
    config = w11._config(tmp_path)
    down = _admin_client(config, fetch=_Fetch((None, "조사 서비스에 연결할 수 없습니다: refused")))
    body = down.get(_SUMMARY).json()
    assert body["mode"] == {"auto_enabled": True, "manual_enabled": True}
    assert body["load"] is None
    assert body["service_error"].startswith("조사 서비스에 연결할 수 없습니다")
    assert body["limits"]["max_concurrent"] is None and body["limits"]["per_user_hourly"] == 3
    old = _admin_client(config, fetch=_Fetch(({"status": "ok", "version": "0.1"}, None)))
    body = old.get(_SUMMARY).json()
    assert body["load"] is None and body["service_error"] == NO_GAUGE_REASON


def test_admin_summary_gate_off_mode(tmp_path) -> None:  # noqa: ANN001
    config = w11._config(tmp_path)
    config.noise_gate.enable_noise_gate = False
    assert _admin_client(config).get(_SUMMARY).json()["mode"] == {
        "auto_enabled": False, "manual_enabled": False,
    }


def test_admin_summary_authz(tmp_path) -> None:  # noqa: ANN001
    config = w11._config(tmp_path)
    assert _admin_client(config, user=None).get(_SUMMARY).status_code == 403
    # 의존성을 통과해도 role 클레임을 다시 본다
    plain_user = {"sub": "u1", "role": "user"}
    assert _admin_client(config, user=plain_user).get(_SUMMARY).status_code == 403
    config.auth.enabled = False  # 개발 모드는 예외
    assert _admin_client(config, user={"sub": "anon"}).get(_SUMMARY).status_code == 200
