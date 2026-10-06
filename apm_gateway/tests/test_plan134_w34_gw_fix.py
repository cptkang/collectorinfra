"""plans/134 W3·W4 게이트웨이 교정(gw-fix-34 · 검증 V34-1 · 리뷰 R34-2·3·5·8·9·10 · 검증 보탬).

- G-1 도메인·업무 시계열: 지표 미지정 → 기본 지표(카탈로그에 있는 것) + `[한계]` · 일부 모름 → 빼고
  조회 + 후보 고지 · 전부 모름 → 기본 지표 + 후보 고지(오류 아님) · 인스턴스 시계열은 종전 그대로.
- G-2 버퍼 기록 도중 예외 → 아무것도 바꾸지 않음(건수 불변식) · 그 구간 확정 없음.
- G-3 모르는 이벤트 응답 모양 → 버퍼 확정 없음 · fleet API 경로는 그 도메인 실패(0건 아님) ·
  폴러 발행·`apm_events`는 종전 그대로.
- G-4 fleet 합치기는 버퍼↔API 겹침만 — 같은 출처 응답 안의 서로 다른 이벤트를 합치지 않는다.
- G-5 배치 최상위 `limits` = 실패 줄 + 하위 `[한계]` 합집합 · G-6 `apm_metrics` `targets`는 instance
  시계열에서만 · G-7 공용 상수·공개 봉투·`RANKING_METRICS` 리터럴 · G-8 항목 hostname 200자.

외부 네트워크 0 — `httpx.MockTransport` · 가상 시계 · 가짜 Redis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from apm_gateway.application import scope_tools, tools
from apm_gateway.application.event_buffer import EventBuffer
from apm_gateway.application.fleet_tools import RANKING_METRICS, FleetTools
from apm_gateway.application.poller import EventPoller
from apm_gateway.domain.errors import INVALID_ARGUMENT, ApmError
from conftest import NOW_MS, NOW_S, synthetic_handler
from fleet_fake import Clock, FakeFleet, event
from test_plan134_w3_fleet import _iso
from test_plan134_w3_fleet import _tools as _fleet_tools
from test_plan134_w3_targets import _counting, _server
from test_plan134_w34_scope import _hits, _tools, calls  # noqa: F401 — calls는 픽스처
from test_poller import FakeRedis

T0 = NOW_MS
DEFAULTS = ["service_time", "service_count", "service_err_count"]
DEFAULT_LABELS = "response_time_avg_ms, service_count, service_err_count"
PACKAGE = Path(__file__).resolve().parents[1] / "apm_gateway"


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


# ── G-1 도메인·업무 시계열 기본 지표 ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_name", ["domain", "business"])
async def test_series_without_metrics_uses_default_metrics(tmp_path, calls, scope_name):  # noqa: F811
    t = _tools(tmp_path, calls)
    extra = {"domain_id": 1000} if scope_name == "domain" else {"business": "order"}
    out = await t.apm_metrics("series", scope_name, **extra)
    asked = [p["metrics"] for _, p in _hits(calls, f"/api/dbmetrics/{scope_name}")]
    assert asked == DEFAULTS
    assert f"[한계] 지표 미지정 — 기본 지표({DEFAULT_LABELS})로 조회" in out["limits"]
    assert "partial" not in out and "_unresolved" not in out
    assert {r["metric"] for r in out["rows"]} == set(DEFAULTS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "hint"),
    [
        (["tps"], "tps(후보: max_tps"),  # 도메인 카탈로그에는 tps가 없다(max_tps만)
        (["responseTime"], "responseTime(후보: response_time_avg_ms"),  # 검증 ⑤의 모양
    ],
)
async def test_series_all_unknown_falls_back_to_defaults_with_candidates(
    tmp_path, calls, requested, hint  # noqa: F811
):
    t = _tools(tmp_path, calls)
    out = await t.apm_metrics("series", "domain", metrics=requested, domain_id=1000)
    asked = [p["metrics"] for _, p in _hits(calls, "/api/dbmetrics/domain")]
    assert asked == DEFAULTS and "error" not in out
    (note,) = out["_unresolved"]
    assert note.startswith(
        f"요청한 지표 {requested[0]}는 목록에 없어 기본 지표({DEFAULT_LABELS})로"
        " 조회했습니다 · 후보 "
    )
    assert hint in note
    assert out["partial"] is True and out["row_count"] == 9


@pytest.mark.asyncio
async def test_series_partly_unknown_drops_it_with_candidates(tmp_path, calls):  # noqa: F811
    t = _tools(tmp_path, calls)
    out = await t.apm_metrics(
        "series", "business", metrics=["service_count", "tps"], business="order"
    )
    assert [p["metrics"] for _, p in _hits(calls, "/api/dbmetrics/business")] == ["service_count"]
    (note,) = out["_unresolved"]
    assert note.startswith("요청한 지표 tps(후보: ") and note.endswith("빼고 조회했습니다")
    assert out["partial"] is True


@pytest.mark.asyncio
async def test_series_defaults_without_catalog_are_unverified_and_partial(tmp_path, calls):  # noqa: F811
    t = _tools(tmp_path, calls, fail={"/api/metrics": set()})
    out = await t.apm_metrics("series", "domain", domain_id=1000)
    assert [p["metrics"] for _, p in _hits(calls, "/api/dbmetrics/domain")] == DEFAULTS
    assert out["partial"] is True
    assert any("기본 지표를 검증하지 못하고 그대로 조회했다" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_instance_series_rules_are_unchanged(tmp_path):
    _mcp, core, jobs = _server(tmp_path, synthetic_handler())
    await jobs.aclose()
    with pytest.raises(ApmError) as exc:
        await core.apm_metrics("series", "instance", hostname="was-host01")
    assert exc.value.code == INVALID_ARGUMENT and "metrics" in exc.value.reason
    with pytest.raises(ApmError) as exc:
        await core.apm_metrics("series", None, hostname="was-host01", metrics=["no_such_x"])
    assert exc.value.code == INVALID_ARGUMENT  # 인스턴스 시계열은 전부 모르면 종전대로 오류
    partly = await core.apm_metrics(
        "series", "instance", hostname="was-host01", metrics=["heap_used_mb", "no_such_x"]
    )
    assert partly["_unresolved"] == ["요청한 지표 no_such_x는 지표 목록에 없어 빼고 조회했습니다"]


def test_default_series_metric_names():
    from apm_gateway.adapters.jennifer.api import JenniferApi

    assert JenniferApi.default_series_metrics("domain") == DEFAULTS
    assert JenniferApi.default_series_metrics("business") == DEFAULTS
    assert JenniferApi.default_series_metrics("instance") == []


# ── G-2 버퍼 기록 도중 예외 ──────────────────────────────────────


def _rec(time_ms, **over):
    base = {
        "time_ms": time_ms,
        "level": "warning",
        "event_type": "ERROR_X",
        "event_kind": "error",
        "value": 1.0,
        "message": "m",
        "instance_id": 1,
        "instance_name": "i1",
        "domain_id": None,
        "domain_name": "",
        "application": "/a",
        "txid": "",
        "instance_oid": 5,
    }
    base.update(over)
    return base


def _invariant(buffer: EventBuffer) -> None:
    doms = buffer._domains.values()
    assert buffer._total == sum(sum(d.counts.values()) for d in doms)
    assert all(set(d.rows) == set(d.counts) == {k for _, k in d.order} for d in doms)


def test_record_failure_changes_nothing_and_confirms_nothing():
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    buffer.record("s", 1, T0 - 90_000, T0 - 80_000, [_rec(T0 - 85_000)])
    broken = _rec(T0 - 70_000)
    del broken["level"]  # 행을 만들 때 KeyError
    with pytest.raises(KeyError):
        buffer.record("s", 1, T0 - 79_999, T0 - 60_000, [_rec(T0 - 75_000, value=2.0), broken])
    _invariant(buffer)
    assert buffer.status()["events"] == 1
    assert [r["time_ms"] for r in buffer.rows("s", 1, 0, T0)] == [T0 - 85_000]
    assert buffer.gaps("s", 1, T0 - 79_999, T0 - 60_000) == [(T0 - 79_999, T0 - 60_000)]
    with pytest.raises(KeyError):  # 처음 보는 도메인에서 실패해도 빈 키를 남기지 않는다
        buffer.record("s", 2, T0 - 10_000, T0 - 5_000, [broken])
    assert ("s", 2) not in buffer._domains
    buffer.record("s", 1, T0 - 79_999, T0 - 60_000, [_rec(T0 - 75_000, value=2.0)])
    _invariant(buffer)
    assert buffer.gaps("s", 1, T0 - 90_000, T0 - 60_000) == []


def test_eviction_keeps_invariant_with_multiplicity():
    buffer = EventBuffer(max_events=3, clock=Clock(NOW_S))
    same = _rec(T0 - 50_000)
    buffer.record("s", 1, T0 - 60_000, T0 - 40_000, [same, dict(same), _rec(T0 - 45_000, value=3)])
    _invariant(buffer)
    assert buffer.status()["events"] == 3  # 한 응답 안의 같은 레코드 둘은 둘로 센다
    buffer.record("s", 2, T0 - 30_000, T0 - 20_000, [_rec(T0 - 25_000)])
    _invariant(buffer)
    assert buffer.status()["events"] <= 3 and buffer.status()["evicted_total"] == 2


# ── G-3 모르는 응답 모양 ─────────────────────────────────────────


async def _poll(
    fake: FakeFleet, buffer: EventBuffer, clock: Clock, tmp_path: Path, polls: int = 4
) -> FakeRedis:
    redis = FakeRedis()
    t, cfg = _fleet_tools(fake, tmp_path, clock=clock, APM_EVENT_POLLER_ENABLED="true")
    poller = EventPoller(t.sources, cfg, redis, clock=clock, event_buffer=buffer)
    for k in range(polls):
        clock.t = NOW_S + 30 * k
        await poller.poll_once()
    return redis


@pytest.mark.asyncio
async def test_unknown_event_shape_is_not_confirmed_but_empty_envelope_is(tmp_path):
    fake = FakeFleet(2, hosts=("apm.test",))
    fake.event_body[("apm.test", 1)] = {"unexpected": True}  # 봉투가 아닌 본문
    fake.event_body[("apm.test", 2)] = {"result": []}  # 알아본 0건
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    await _poll(fake, buffer, clock, tmp_path)
    assert buffer.gaps("default", 1, T0, T0 + 30_000) == [(T0, T0 + 30_000)]
    assert buffer.gaps("default", 2, T0, T0 + 30_000) == []


@pytest.mark.asyncio
async def test_unknown_shape_is_a_failed_domain_in_fleet_but_apm_events_is_unchanged(tmp_path):
    fake = FakeFleet(2, hosts=("apm.test",))
    fake.event_body[("apm.test", 1)] = ["not", "an", "envelope"]
    fake.add_event("apm.test", event(2, 200, T0 - 60_000, txid="1"))
    t, _ = _fleet_tools(fake, tmp_path)
    out = await FleetTools(t, None).events()
    assert out["coverage"]["failed"] == 1 and out["summary"]["domains_failed"] == 1
    assert out["partial"] is True and {r["domain_id"] for r in out["rows"]} == {2}
    assert any("이벤트 조회 실패 도메인 1곳(도메인 1)" in x for x in out["limits"])
    single = await t.apm_events(hostname="apm-h1-0")  # 종전대로 관대(0건) — 바꾸지 않는다
    assert single["row_count"] == 0 and "error" not in single


@pytest.mark.asyncio
async def test_publishing_is_byte_identical_for_unknown_shapes(tmp_path):
    def fake_with_shapes() -> FakeFleet:
        fake = FakeFleet(2, hosts=("apm.test",))
        fake.event_body[("apm.test", 1)] = {"unexpected": True}
        for k in range(6):
            fake.add_event("apm.test", event(2, 200, T0 + k * 20_000 - 25_000, txid=str(k),
                                             level=("FATAL", "WARNING", "NORMAL")[k % 3]))
        return fake

    runs = []
    for make in (lambda c: EventBuffer(clock=c), lambda c: None):
        clock = Clock(NOW_S)
        redis = FakeRedis()
        fake = fake_with_shapes()
        t, cfg = _fleet_tools(fake, tmp_path, clock=clock, APM_EVENT_POLLER_ENABLED="true")
        poller = EventPoller(t.sources, cfg, redis, clock=clock, event_buffer=make(clock))
        for k in range(5):
            clock.t = NOW_S + 30 * k
            await poller.poll_once()
        runs.append((json.dumps(redis.stream), dict(redis.kv)))
    assert runs[0] == runs[1] and runs[0][0] != "[]"


# ── G-4 합치기는 버퍼↔API 겹침만 ─────────────────────────────────


def _twins_fake() -> FakeFleet:
    """txid 없음 · 같은 ms·유형 · 값·메시지가 다른 두 이벤트(폴러 멱등 키 칸은 같다)."""
    fake = FakeFleet(1, hosts=("apm.test",))
    for value, msg in ((1.0, "first"), (2.0, "second")):
        e = event(1, 100, T0 + 100_000, message=msg)
        e["value"] = value
        fake.add_event("apm.test", e)
    return fake


@pytest.mark.asyncio
async def test_distinct_events_with_same_alarm_key_are_not_merged(tmp_path):
    fake = _twins_fake()
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    await _poll(fake, buffer, clock, tmp_path, polls=13)
    t, _ = _fleet_tools(fake, tmp_path, clock=clock)
    window = {"reference_time": _iso(T0 + 300_000), "lookback_minutes": 5}
    fake.calls.clear()
    from_buffer = await FleetTools(t, buffer).events(**window)
    assert fake.event_calls() == [] and from_buffer["coverage"]["from_buffer"] == 1
    from_api = await FleetTools(t, None).events(**window)
    assert sorted(r["message"] for r in from_buffer["rows"]) == ["first", "second"]
    assert json.dumps(from_buffer["rows"], ensure_ascii=False) == json.dumps(
        from_api["rows"], ensure_ascii=False
    )
    single = await t.apm_events(hostname="apm-h1-0", **window)
    assert single["row_count"] == from_api["row_count"] == 2  # apm_events와 같은 건수


@pytest.mark.asyncio
async def test_overlap_between_buffer_and_api_is_removed_once(tmp_path):
    fake = _twins_fake()
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    await _poll(fake, buffer, clock, tmp_path, polls=13)  # 확정 [T0−30초, T0+300초]
    fake.ignore_window = True  # 원천이 창 밖까지 돌려준다 — 버퍼 쪽 행과 겹친다
    t, _ = _fleet_tools(fake, tmp_path, clock=clock)
    out = await FleetTools(t, buffer).events(lookback_minutes=5)  # 지금 T0+360초 — 섞임
    assert out["coverage"]["mixed"] == 1
    assert sorted(r["value"] for r in out["rows"]) == [1.0, 2.0]  # 겹친 사본은 한 번만


# ── G-5 배치 최상위 limits ───────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_top_level_limits_carry_sub_limits_once(tmp_path):
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    try:
        out = _json(
            await mcp.call_tool(
                "apm_resource_pool",
                {"targets": [{"hostname": "was-host01"}, {"instance_name": "api02_main"},
                             {"hostname": "nohost"}]},
            )
        )
    finally:
        await jobs.aclose()
    assert out["limits"][0].startswith("[한계] 대상 1/3 조회 실패: nohost(")
    union = list(dict.fromkeys(x for b in out["batch"] for x in b.get("limits") or []))
    assert out["limits"][1:] == union
    assert out["limits"].count("[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다") == 1


# ── G-6 apm_metrics targets 범위 ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"mode": "catalog"},
        {"mode": "catalog", "scope": "instance"},
        {"mode": "series", "scope": "domain", "metrics": ["service_time"]},
        {"mode": "series", "scope": "business", "metrics": ["service_time"]},
    ],
)
async def test_metrics_targets_only_for_instance_series(tmp_path, args):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, _counting(synthetic_handler(), seen))
    try:
        out = _json(
            await mcp.call_tool("apm_metrics", {**args, "targets": [{"hostname": "was-host01"}]})
        )
    finally:
        await jobs.aclose()
    assert out["error"] == INVALID_ARGUMENT and "scope instance" in out["reason"]
    assert seen == [] and "batch" not in out


@pytest.mark.asyncio
async def test_metrics_targets_instance_series_still_batches(tmp_path):
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    try:
        out = _json(
            await mcp.call_tool(
                "apm_metrics",
                {"mode": "series", "metrics": ["heap_used_mb"],
                 "targets": [{"hostname": "was-host01"}, {"instance_name": "api02_main"}]},
            )
        )
    finally:
        await jobs.aclose()
    assert [b["status"] for b in out["batch"]] == ["ok", "ok"]


# ── G-7 공용 상수 · 공개 봉투 · 순위 지표 리터럴 ──────────────────────


def test_visit_hit_note_has_one_definition():
    assert scope_tools.VISIT_HIT_NOTE is tools.VISIT_HIT_NOTE
    text = tools.VISIT_HIT_NOTE
    head = text[: text.index("(")]
    hits = [p.name for p in PACKAGE.rglob("*.py") if head in p.read_text(encoding="utf-8")]
    assert hits == ["tools.py"]


def test_fleet_uses_public_scope_envelope():
    source = (PACKAGE / "application" / "fleet_tools.py").read_text(encoding="utf-8")
    assert "._envelope(" not in source and "self._scope.envelope(" in source


def test_ranking_metrics_literal_tuple():
    assert RANKING_METRICS == (
        "response_time_avg_ms",
        "tps",
        "active_services",
        "bad_response_active_services",
        "reject_rate",
        "concurrent_users",
        "arrival_rate",
        "heap_used_mb",
        "heap_committed_mb",
        "non_heap_used_mb",
        "gc_time_usage_pct",
        "process_cpu_pct",
        "process_memory_mb",
        "thread_current",
        "db_pool_active",
        "db_pool_idle_avg",
        "db_pool_configured_avg",
        "visit_day",
        "visit_hour",
        "hit_day",
        "hit_hour",
        "active_range_count_0",
        "active_range_count_1",
        "active_range_count_2",
        "active_range_count_3",
        "collection_count",
        "file_count",
        "socket_count",
        "thread_daemon",
        "thread_started",
    )


# ── G-8 항목 hostname 길이 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_target_hostname_longer_than_200_is_invalid_without_http(tmp_path):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, _counting(synthetic_handler(), seen))
    try:
        long_host = _json(
            await mcp.call_tool("apm_app_health", {"targets": [{"hostname": "h" * 201}]})
        )
        at_limit = _json(
            await mcp.call_tool("apm_app_health", {"targets": [{"hostname": "h" * 200}]})
        )
        single = _json(await mcp.call_tool("apm_app_health", {"hostname": "h" * 201}))
    finally:
        await jobs.aclose()
    assert long_host["error"] == INVALID_ARGUMENT and "200자" in long_host["reason"]
    assert at_limit["error"] != INVALID_ARGUMENT  # 형식은 통과(정합 실패)
    assert single["error"] != INVALID_ARGUMENT  # 단건 hostname은 종전 그대로

