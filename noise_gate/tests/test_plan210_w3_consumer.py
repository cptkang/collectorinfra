"""plans/210 W3 §3.1 되적재 — `apmhostmap:req` 소비자 · 알람 2회 종단 · 워커 PG 0 · 기동 배선.

PG는 대역 저장소(쓰기 규칙은 도메인 `plan_auto_write` 그대로), Redis는 스트림 consumer group까지
흉내 낸 `FakeAsyncRedis` 파생, 사본은 실제 `RedisApmHostMapCache`다.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application import apm_host_map_consumer as cons
from noise_gate.application import host_candidates as hc
from noise_gate.application.nodes import investigation_trigger as trig
from noise_gate.domain import apm_host_map as hm
from noise_gate.infrastructure import apm_host_map_cache as cm
from noise_gate.infrastructure import apm_host_map_repository as repo_mod

from .test_plan210_w3_fast_path import (
    NODE_IP,
    POD,
    SOURCES,
    WORKLOAD,
    _apm,
    _attach,
    _cfg,
    _event,
    _FakeProbe,
    _Redis,
)

POD2 = "bfm-kbland-admin-price-deploy-6dd49d5df6-q9z8w"
NODE_IP2 = "198.51.100.8"


class _StreamRedis(_Redis):
    """consumer group 읽기(XGROUP CREATE · XREADGROUP `>` · XACK)를 더한 대역."""

    def __init__(self) -> None:
        super().__init__()
        self.groups: dict[tuple[str, str], int] = {}
        self.acked: list[str] = []

    async def xgroup_create(self, name: str, group: str, id: str = "0",
                            mkstream: bool = False) -> bool:
        if (name, group) in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.groups[(name, group)] = 0
        self.streams.setdefault(name, [])
        return True

    async def xreadgroup(self, group: str, consumer: str, streams: dict[str, str],
                         count: int | None = None, block: int | None = None) -> list[Any]:
        out: list[Any] = []
        for name in streams:
            pos = self.groups[(name, group)]
            entries = self.streams.get(name, [])[pos: pos + (count or 10**9)]
            self.groups[(name, group)] = pos + len(entries)
            if entries:
                out.append([name, list(entries)])
        if not out and block:
            await asyncio.sleep(0.001)  # 실제 BLOCK처럼 루프에 양보한다
        return out

    async def xack(self, name: str, group: str, *ids: str) -> int:
        self.acked.extend(ids)
        return len(ids)


class _Repo:
    """PG 정본 대역 — 쓰기 판정은 도메인 `plan_auto_write`."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.next_id = 1
        self.touched: list[list[int]] = []
        self.get_calls: list[tuple[str, str, str, str]] = []

    async def get_by_key(self, source_id: str, domain_id: str, scope: str,
                         match_key: str) -> dict[str, Any] | None:
        key = (source_id, domain_id, scope, match_key)
        self.get_calls.append(key)
        row = self.rows.get(key)
        return dict(row) if row else None

    async def apply_auto_many(self, source_id: str, proposals: Any, *, now: Any = None,
                              retry_hours: int = 24) -> list[tuple[str, dict[str, Any] | None]]:
        out: list[tuple[str, dict[str, Any] | None]] = []
        for p in proposals:
            key = (source_id, p.domain_id, p.scope, p.match_key)
            existing = self.rows.get(key)
            write = hm.plan_auto_write(existing, p, now_iso="2026-11-03T00:00:00+00:00")
            if existing is None:
                row = {
                    "id": self.next_id, "source_id": source_id, "domain_id": p.domain_id,
                    "scope": p.scope, "match_key": p.match_key, "origin": "auto",
                    "basis": write.basis, "state": write.state, "verified": False,
                    "targets": [dict(t) for t in write.targets], "evidence": write.evidence,
                    "version": 1,
                }
                self.next_id += 1
            elif write.action in (hm.WRITE_UPDATE, hm.WRITE_SUGGEST):
                row = {**existing, "basis": write.basis, "state": write.state,
                       "targets": [dict(t) for t in write.targets],
                       "evidence": write.evidence, "version": existing["version"] + 1}
            else:
                row = existing
            self.rows[key] = row
            out.append((write.action, dict(row)))
        return out

    async def touch(self, map_ids: Any, *, at: Any = None) -> int:
        self.touched.append(list(map_ids))
        return len(map_ids)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeProbe:
    fake = _FakeProbe()
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (fake, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    return fake


def _consumer(redis: _StreamRedis, repo: _Repo, **kw: Any) -> cons.ApmHostMapConsumer:
    kw.setdefault("allowed_db_ids", lambda: list(SOURCES))
    return cons.ApmHostMapConsumer(
        repo, cm.RedisApmHostMapCache(redis), redis=redis, consumer="t1", **kw  # type: ignore[arg-type]
    )


def _node(name: str, ip: str) -> list[dict[str, Any]]:
    return [{"name": name, "hostname": name.lower(), "ipaddress": ip}]


# ── 알람 2회 종단 ───────────────────────────────────────────────────────
async def test_learn_then_second_alarm_hits(probe: _FakeProbe) -> None:
    probe.table["polestar_cm_gp"] = _node("NODE-01", NODE_IP)
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    await consumer.ensure_group()
    await consumer.ensure_group()  # 멱등

    first = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert first["basis"] == "node_address" and len(probe.calls) == 3
    assert await consumer.consume_once() == 1
    assert redis.acked == ["1-0"]
    inst = repo.rows[("jen", "7", "instance", POD)]
    wl = repo.rows[("jen", "7", "workload", WORKLOAD)]
    assert [t["server_name"] for t in inst["targets"]] == ["NODE-01"]
    assert [t["server_name"] for t in wl["targets"]] == ["NODE-01"]

    probe.calls.clear()
    second = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert second["basis"] == hm.HINT_BASIS and second["map_scope"] == "instance"
    assert second["map_id"] == inst["id"] and probe.calls == []

    # 같은 워크로드 다른 파드 — 자기 노드 주소로 linked(3순) + workload 합집합
    probe.table["polestar_cm_gp"] = _node("NODE-02", NODE_IP2)
    other = _event(_apm(instance_name=POD2, host_address=NODE_IP2), resource_name=POD2,
                   hostname=POD2, server_name=POD2)
    third = (await _attach(_cfg(), other, redis=redis))["polestar_link"]
    assert third["server_name"] == "NODE-02" and third["basis"] == "node_address"
    await consumer.consume_once()
    wl = repo.rows[("jen", "7", "workload", WORKLOAD)]
    assert {t["server_name"] for t in wl["targets"]} == {"NODE-01", "NODE-02"}
    assert repo.rows[("jen", "7", "instance", POD2)]["targets"][0]["server_name"] == "NODE-02"
    # 사본도 갱신됐다 — 노드 주소가 없는 세 번째 파드는 workload 2대로 hit(4순)
    pod3 = "bfm-kbland-admin-price-deploy-6dd49d5df6-abcde"
    probe.calls.clear()
    fourth = (await _attach(_cfg(), _event(
        _apm(instance_name=pod3, host_address="", host_address_status="not_found"),
        resource_name=pod3,
    ), redis=redis))["polestar_link"]
    assert fourth["map_scope"] == "workload" and len(fourth["targets"]) == 2
    assert probe.calls == []


async def test_touch_is_batched_per_minute() -> None:
    redis, repo = _StreamRedis(), _Repo()
    now = [100.0]
    consumer = _consumer(redis, repo, clock=lambda: now[0])
    await consumer.ensure_group()
    await cm.publish_request(redis, hm.touch_message(source_id="jen", map_ids=[3]))
    await cm.publish_request(redis, hm.touch_message(source_id="jen", map_ids=[3, 4]))
    assert await consumer.consume_once() == 2
    assert repo.touched == []  # 1분 전 — 모아만 둔다
    now[0] += cons.TOUCH_FLUSH_SECONDS
    await consumer.consume_once()
    assert repo.touched == [[3, 4]]
    assert redis.acked == ["1-0", "2-0"]


async def test_itam_request_handler_registration() -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    await consumer.ensure_group()
    msg = hm.itam_request_message(source_id="jen", domain_id="7", domain_name="KB")
    await cm.publish_request(redis, msg)
    assert await consumer.consume_once() == 1  # 처리기 없음 — 로그만 · ack
    got: list[dict[str, Any]] = []

    async def _handler(message: dict[str, Any]) -> None:
        got.append(message)

    consumer.set_itam_handler(_handler)
    await cm.publish_request(redis, msg)
    await consumer.consume_once()
    assert got == [msg] and len(redis.acked) == 2


async def test_bad_and_failing_messages_are_acked() -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    await consumer.ensure_group()
    redis.streams[cm.REQUEST_STREAM].append(("1-0", {"type": "nope", "data": "{}"}))

    async def _boom(*a: Any, **k: Any) -> Any:
        raise ConnectionError("pg down")

    repo.apply_auto_many = _boom  # type: ignore[method-assign]
    await cm.publish_request(redis, hm.learn_message(
        source_id="jen", domain_id="7", instance_name=POD, basis=hm.BASIS_NODE_ADDRESS,
        targets=[hm.make_target(db_id="polestar_cm_gp", server_name="N")],
    ))
    assert await consumer.consume_once() == 2
    assert len(redis.acked) == 2


async def test_manual_row_is_not_overwritten_by_learn() -> None:
    redis, repo = _StreamRedis(), _Repo()
    repo.rows[("jen", "7", "instance", POD)] = {
        "id": 50, "source_id": "jen", "domain_id": "7", "scope": "instance", "match_key": POD,
        "origin": "manual", "basis": "manual", "state": "active", "verified": False,
        "targets": [hm.make_target(db_id="polestar_b0", server_name="ADMIN")],
        "evidence": {}, "version": 3,
    }
    consumer = _consumer(redis, repo)
    await consumer.ensure_group()
    await cm.publish_request(redis, hm.learn_message(
        source_id="jen", domain_id="7", instance_name=POD, basis=hm.BASIS_NODE_ADDRESS,
        targets=[hm.make_target(db_id="polestar_cm_gp", server_name="N")],
    ))
    await consumer.consume_once()
    row = repo.rows[("jen", "7", "instance", POD)]
    assert row["origin"] == "manual" and row["targets"][0]["server_name"] == "ADMIN"
    assert "scan_suggests" in row["evidence"]


async def test_run_flushes_touches_on_cancel() -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    await consumer.ensure_group()
    await cm.publish_request(redis, hm.touch_message(source_id="jen", map_ids=[9]))
    task = asyncio.create_task(consumer.run())
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert repo.touched == [[9]]


# ── 워커 PG 연결 0 ──────────────────────────────────────────────────────
async def test_worker_paths_never_build_pg_repository(
    probe: _FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    """별도 워커 프로세스·in-process 모두 `AlarmWorker` → `_attach_polestar_link(redis=…)`다.

    플래그 on·auto on으로 hit·learn·miss를 모두 지나도 PG 저장소를 만들지 않는다.
    """
    def _no_pg(*a: Any, **k: Any) -> None:
        raise AssertionError("워커가 PG 저장소를 만들었다")

    monkeypatch.setattr(repo_mod.PostgresApmHostMapRepository, "__init__", _no_pg)
    probe.table["polestar_cm_gp"] = _node("NODE-01", NODE_IP)
    redis = _StreamRedis()
    await _attach(_cfg(), _event(), redis=redis)  # live + learn
    await _attach(_cfg(), _event(_apm(host_address="", host_address_status="error")),
                  redis=redis)  # miss + itam_request
    payload = {"meta": {"hints": {"solution": "apm", "source_id": "jen"}}}
    # 사건 종합 경로와 같은 인자(redis만)
    await trig._attach_polestar_link(_cfg(), _event(), payload, redis=redis)
    assert {m["type"] for m in redis.requests()} == {"learn", "itam_request"}

    root = Path(__file__).resolve().parents[1]
    for rel in ("application/alarm_worker.py", "application/worker_wiring.py", "worker.py",
                "application/apm_host_map_lookup.py",
                "application/nodes/investigation_trigger.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "apm_host_map_repository" not in text, rel
        assert "PostgresApmHostMapRepository" not in text, rel


def test_three_call_paths_pass_redis_or_cache() -> None:
    """자동·사건 종합·수동 세 경로가 같은 함수를 부르고, 사본 원천(redis·map_cache)을 넘긴다."""
    src = inspect.getsource(trig)
    assert "redis=configurable.get(\"history_redis\")" in src
    assert "await _attach_polestar_link(cfg, event, payload, redis=redis)" in src
    assert "map_cache=map_cache" in src
    assert "map_cache" in inspect.signature(trig.build_manual_payload).parameters


# ── API 기동 배선 ───────────────────────────────────────────────────────
async def test_server_starts_consumer_only_when_auto_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import server

    class _State(SimpleNamespace):
        pass

    async def _ddl(pool: Any) -> None:
        return None

    class _FakePgRepo:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def abort_running_scan_runs(self) -> int:
            return 0

        async def cleanup(self, **_: Any) -> dict[str, int]:
            return {}

        async def all_rows(self) -> list[dict[str, Any]]:
            return []

        async def snapshot_rows(self, table: str, **_: Any) -> list[dict[str, Any]]:
            return []

    redis = _StreamRedis()
    monkeypatch.setattr(server, "_ensure_apm_host_map_tables", _ddl)
    monkeypatch.setattr(repo_mod, "PostgresApmHostMapRepository", _FakePgRepo)
    monkeypatch.setattr(cm, "connect_redis", lambda cfg: redis)

    def _config(auto: bool) -> SimpleNamespace:
        return SimpleNamespace(
            noise_gate=SimpleNamespace(apm_host_map_enabled=True, apm_host_map_auto_enabled=auto,
                                       apm_host_map_max_targets=4, apm_host_map_retry_hours=6),
            redis=SimpleNamespace(host="127.0.0.1", port=6399, password="", db=0),
        )

    state = _State(auth_pool=object())
    task, _ = await server._start_apm_host_map(SimpleNamespace(state=state), _config(False))  # type: ignore[arg-type]
    assert not hasattr(state, "apm_host_map_consumer")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    state = _State(auth_pool=object())
    task, _ = await server._start_apm_host_map(SimpleNamespace(state=state), _config(True))  # type: ignore[arg-type]
    try:
        consumer = state.apm_host_map_consumer
        assert isinstance(consumer, cons.ApmHostMapConsumer)
        assert consumer._max_targets == 4 and consumer._retry_hours == 6
        for _ in range(20):
            await asyncio.sleep(0)
        assert (cm.REQUEST_STREAM, cons.CONSUMER_GROUP) in redis.groups
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.done()
