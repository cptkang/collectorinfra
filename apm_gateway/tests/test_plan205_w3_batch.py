"""plans/205 W3 — `targets` 배치 항목 동시 실행(`application/batch.py` · D-356).

- 봉투: `APM_FANOUT_CONCURRENCY` 4와 1의 배치 봉투가 같다(항목 순서·`target_index`·부분 실패
  고지·하위 봉투 고지(항목마다 다른 자격증명 메모)·최상위 `limits` 합집합·`batch` 항목 · 전부 실패
  봉투) — 가짜 전송이 뒤 항목을 먼저 끝내게 해서 항목이 실제로 겹쳤음을 함께 본다.
- 작업 경로(`JobManager`): 최종 `expected_calls`·`api_calls`·`calls_by_source` 동일 · 동시 1의 중간
  예상 신고 순서·값 = 16ea44fa 기준선(항목 순차 코드) 녹화.
- 중첩 동시 실행 금지(`fanout._ACTIVE`): 동시 항목 안의 `gather_ordered`는 차례로 · 표지는 바깥
  맥락에 새지 않음 · 차례 바깥 안의 호출은 동시 유지 · 배치 작업 1건의 미결 HTTP 최대 ≤ limit.
- `_Plan` 항목별 카운터의 동시 의미 · 소요 단축(느슨) · R-1 배치 조합(항목 4 × 하위 독립 호출 3 ×
  작업 4 — 소스 자리 8에서 자리 대기 초과 0건 · 시간 축척 0.1).

외부 네트워크 0 — `httpx.MockTransport`만 쓴다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.application.batch import (
    TARGET_KEYS,
    TARGET_KEYS_WITH_ID,
    Target,
    _ItemScope,
    _Plan,
    run_batch,
    targeted,
)
from apm_gateway.application.fanout import _ACTIVE, gather_ordered
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import PRIORITY_INTERACTIVE, CallScope, use_scope
from apm_gateway.domain.errors import API_ERROR, CONTRACT_VIOLATION, SOURCE_UNAVAILABLE, ApmError
from conftest import NOW_S, TOKEN, synthetic_handler
from test_plan134_w3_targets import _host_fixtures
from test_plan205_w2a_fanout import XVIEW, Fake, _run_job, _tools

from apm_gateway.config import load_config

Core = Callable[[ApmTools], Callable[[Target], Awaitable[dict[str, Any]]]]

# ── 봉투 동일 — 동시 4 vs 순차 1(작업 경로) ─────────────────────────────

# 전 단위 항목(소스 2 × 도메인 3 — 하위 도구 안에서도 동시 · 가장 느리다)을 앞에, 단위 하나로 좁힌
# 항목을 (소스, 도메인) 순으로 뒤에 둔다 — 가짜 전송은 뒤 단위일수록 빨리 답하므로 뒤 항목이 먼저
# 끝난다. `nohost.test`는 해석 실패(HTTP 0 · 바로 끝남) 항목이다.
TARGETS_WITH_ID = [
    {"hostname": "was-host01"},
    {"hostname": "was-host01", "source_id": "bank", "instance_id": 101},
    {"hostname": "nohost.test"},
    {"hostname": "was-host01", "source_id": "bank", "instance_id": 202},
    {"instance_name": "was01_a", "source_id": "common", "instance_id": 101},
    {"hostname": "was-host01", "source_id": "common", "instance_id": 302},
]
TARGETS_NO_ID = [  # instance_id를 받지 않는 도구(apm_events)
    {"hostname": "was-host01"},
    {"hostname": "was-host01", "source_id": "bank"},
    {"hostname": "nohost.test"},
    {"instance_name": "was01_b", "source_id": "common"},
]
_PATHS = (
    "/api/activeService/list",
    "/api/dbsearch/event",
    "/api/dbsearch/error",
    "/api/realtime/instance",
    XVIEW,
)


def _active(tools: ApmTools) -> Callable[[Target], Awaitable[dict[str, Any]]]:
    return lambda t: tools.apm_active_services(
        t.hostname, t.instance_id, None, t.sources(None), True, instance_name=t.instance_name
    )


def _health(tools: ApmTools) -> Callable[[Target], Awaitable[dict[str, Any]]]:
    return lambda t: tools.apm_app_health(
        t.hostname, t.instance_id, None, 3, t.sources(None), instance_name=t.instance_name
    )


def _events(tools: ApmTools) -> Callable[[Target], Awaitable[dict[str, Any]]]:
    return lambda t: tools.apm_events(
        hostname=t.hostname,
        source_ids=t.sources(None),
        record="error",
        full=True,
        instance_name=t.instance_name,
    )


CASES = [
    ("apm_active_services", _active, TARGETS_WITH_ID, TARGET_KEYS_WITH_ID),
    ("apm_app_health", _health, TARGETS_WITH_ID, TARGET_KEYS_WITH_ID),
    ("apm_events", _events, TARGETS_NO_ID, TARGET_KEYS),
]


class _Overlap:
    """항목 하위 호출의 동시 수와 끝난 순서(항목 번호)를 잰다."""

    def __init__(self, targets: list[dict[str, Any]]) -> None:
        keys = ("hostname", "instance_name", "instance_id", "source_id")
        self.index = {tuple(t.get(k) for k in keys): i for i, t in enumerate(targets)}
        self.inflight = 0
        self.peak = 0
        self.finished: list[int] = []

    def wrap(
        self, core: Callable[[Target], Awaitable[dict[str, Any]]]
    ) -> Callable[[Target], Awaitable[dict[str, Any]]]:
        async def run(t: Target) -> dict[str, Any]:
            index = self.index[(t.hostname, t.instance_name, t.instance_id, t.source_id)]
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            try:
                return await core(t)
            finally:
                self.inflight -= 1
                self.finished.append(index)

        return run


def _ok(fake: Fake) -> None:
    return None


def _partial(fake: Fake) -> None:
    """(bank, 2) 단위 전 경로 실패 — 그 단위로 좁힌 항목은 실패 · 전 단위 항목은 partial."""
    for path in _PATHS:
        fake.fail.add(("bank", 2, path))


def _all_fail(fake: Fake) -> None:
    fake.fail_all |= set(_PATHS)


async def _both(tmp_path, name: str, core: Core, targets, keys, setup):
    """동시 4와 순차 1로 같은 배치를 작업 경로로 돌린다 → [(봉투, 감사, 겹침)] (4, 1 순)."""
    out = []
    for fanout in (4, 1):
        fake = Fake(unit_delay=0.004)
        setup(fake)
        tools = _tools(fake, fanout)
        overlap = _Overlap(targets)
        _, call = targeted(
            tools, name, targets, overlap.wrap(core(tools)), label="*", keys=keys
        )
        envelope, audit = await _run_job(tmp_path, tools, name, lambda _t, c=call: c())
        out.append((envelope, audit, overlap))
    return out


@pytest.mark.parametrize("setup", [_ok, _partial, _all_fail])
@pytest.mark.parametrize(("name", "core", "targets", "keys"), CASES)
async def test_batch_envelope_and_audit_equal_sequential(
    tmp_path, name, core, targets, keys, setup
):
    (out4, audit4, ov4), (out1, audit1, ov1) = await _both(
        tmp_path, name, core, targets, keys, setup
    )
    assert out4 == out1
    assert audit4 == audit1  # api_calls · calls_by_source · expected_calls · 메모 · 진행
    assert audit4["expected_calls"] == audit4["api_calls"] or setup is not _ok
    n = len(targets)
    assert [b["index"] for b in out4["batch"]] == list(range(n))
    assert [b["target"] for b in out4["batch"]] == targets
    # 순차는 하나씩 · 입력 순서로 끝나고, 동시는 겹치며 뒤 항목이 먼저 끝났다
    assert ov1.peak == 1 and ov1.finished == list(range(n))
    assert ov4.peak > 1 and ov4.finished != list(range(n)), (ov4.peak, ov4.finished)
    statuses = [b["status"] for b in out4["batch"]]
    if setup is _all_fail:
        assert out4["error"] == API_ERROR  # 항목 코드가 섞인다(해석 실패 · 소스 불가)
        assert "rows" not in out4 and set(statuses) == {"error"}
        assert out4["reason"].startswith(f"대상 {n}개 모두 실패 — ")
        return
    assert "error" not in out4 and out4["partial"] is True  # 해석 실패 항목이 늘 있다
    failed = [x for x in out4["limits"] if x.startswith("[한계] 대상 ")]
    assert len(failed) == 1 and "nohost.test" in failed[0]
    assert {r["target_index"] for r in out4["rows"]} <= set(range(n))
    indexes = [r["target_index"] for r in out4["rows"]]
    assert indexes == sorted(indexes)  # 행은 항목 순
    # 항목마다 다른 자격증명 메모(응답 도착 역순)가 하위 봉투 고지로 실리고 최상위는 그 합집합
    noted = [b for b in out4["batch"] if any("자격증명" in x for x in b.get("limits") or [])]
    assert len(noted) >= 2
    sub = [x for b in out4["batch"] for x in b.get("limits") or []]
    assert out4["limits"] == list(dict.fromkeys([*failed, *sub]))
    if setup is _partial and name != "apm_events":  # (bank, 2)로 좁힌 항목만 실패
        assert statuses == ["ok", "ok", "error", "error", "ok", "ok"]


async def test_text_parts_names_follow_input_index(tmp_path):
    """결과 파일 텍스트 부분 이름 `_t{index}`는 입력 항목 번호다(도착 순서와 무관)."""

    def core(tools: ApmTools) -> Callable[[Target], Awaitable[dict[str, Any]]]:
        async def run(t: Target) -> dict[str, Any]:
            delay = {"a": 0.06, "b": 0.03, "c": 0.0}[str(t.hostname)]
            await asyncio.sleep(delay)
            out = tools.ok("apm_x", [{"h": t.hostname}])
            out["_text_parts"] = {"body": f"text-{t.hostname}"}
            return out

        return run

    outs = []
    for fanout in (4, 1):
        tools = _tools(Fake(), fanout)
        targets = [{"hostname": h} for h in ("a", "b", "c")]
        outs.append(
            await run_batch(tools, "apm_x", targets, core(tools), keys=TARGET_KEYS)
        )
    assert outs[0] == outs[1]
    assert [b.get("text_parts") for b in outs[0]["batch"]] == [
        ["body_t0"],
        ["body_t1"],
        ["body_t2"],
    ]


# ── 동시 1의 중간 예상 신고 = 16ea44fa 기준선 녹화 ─────────────────────────


class _Log(CallScope):
    def __init__(self) -> None:
        super().__init__()
        self.log: list[list[Any]] = []

    async def before_call(self, source_id: str = "") -> None:
        self.log.append(["call", source_id])

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        self.log.append(["count", source_id, shared])

    def expect_calls(self, count: int) -> None:
        self.log.append(["expect", count])

    def note(self, text: str) -> None:
        self.log.append(["note", text[:40]])


_C = ["call", "default"]
_SHARED = ["count", "default", True]
# 16ea44fa(항목 순차 코드 · 하위 도구 순차)에서 같은 시나리오를 돌려 녹화한 바깥 맥락 신고 순서
BASELINE = {
    "apm_slow_transactions": [_SHARED, _SHARED, ["expect", 12], _C, _C, _C, ["expect", -3]]
    + [_C] * 6,
    "apm_app_health": [_SHARED, _SHARED, ["expect", 4], _C, ["expect", 12], _C, _C, _C]
    + [["expect", -4]]
    + [_C] * 8,
}
_SEQ_TARGETS = [
    {"hostname": "host00"},
    {"hostname": "nohost"},
    {"hostname": "host02"},
    {"hostname": "host03", "instance_id": 2003},
]


async def _seq_log(tool: str, fanout: int) -> tuple[list[list[Any]], dict[str, Any]]:
    cfg = load_config(
        {
            "JENNIFER_API_URL": "http://apm.test",
            "JENNIFER_API_TOKEN": TOKEN,
            "JENNIFER_RATE_LIMIT_PER_SEC": "0",
            "APM_PARSE_WORKERS": "0",
            "APM_FANOUT_CONCURRENCY": str(fanout),
        }
    )
    handler = synthetic_handler(_host_fixtures("host", 4))
    tools = ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(handler)), cfg, clock=lambda: NOW_S
    )

    def call(t: Target) -> Awaitable[dict[str, Any]]:
        if tool == "apm_slow_transactions":
            return tools.apm_slow_transactions(t.hostname, t.instance_id, None, 3)
        return tools.apm_app_health(t.hostname, t.instance_id, None, 3)

    scope = _Log()
    with use_scope(scope):
        env = await run_batch(tools, tool, _SEQ_TARGETS, call, keys=TARGET_KEYS_WITH_ID)
    return scope.log, env


@pytest.mark.parametrize("tool", sorted(BASELINE))
async def test_sequential_expect_reports_match_baseline(tool):
    log1, env1 = await _seq_log(tool, 1)
    assert log1 == BASELINE[tool]
    log4, env4 = await _seq_log(tool, 4)
    assert env4 == env1
    assert [b["status"] for b in env1["batch"]] == ["ok", "error", "ok", "ok"]

    def total(log: list[list[Any]], kind: str) -> int:
        return sum(e[1] if kind == "expect" else 1 for e in log if e[0] == kind)

    # 동시는 중간 신고가 달라도 최종 총 예상·호출 수는 같다
    assert total(log4, "expect") == total(log1, "expect")
    assert total(log4, "call") == total(log1, "call")
    assert total(log4, "count") == total(log1, "count")


# ── 중첩 동시 실행 금지 — 작업 1건의 미결 호출 ≤ limit ─────────────────────


class _Peak:
    def __init__(self) -> None:
        self.inflight = 0
        self.peak = 0

    async def hit(self, delay: float) -> None:
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            await asyncio.sleep(delay)
        finally:
            self.inflight -= 1


async def test_nested_gather_inside_concurrent_item_runs_one_after_another():
    total = _Peak()

    async def item(i: int) -> list[Any]:
        assert _ACTIVE.get() is True
        me = asyncio.current_task()

        async def leaf(j: int) -> int:
            assert asyncio.current_task() is me  # 태스크 없이 항목 태스크에서 차례로
            await total.hit(0.01)
            return j

        got = await gather_ordered(range(3), leaf, limit=4)
        return [r for _, r in got]

    assert _ACTIVE.get() is False
    out = await gather_ordered(range(4), item, limit=4)
    assert [r for _, r in out] == [[0, 1, 2]] * 4
    assert total.peak == 4  # 항목 4 × 하위 차례 1 — 곱(12)이 아니다
    assert _ACTIVE.get() is False  # 바깥 맥락에 새지 않는다


async def test_nested_sequential_path_keeps_contract_violation_stop():
    called: list[int] = []

    async def leaf(j: int) -> int:
        called.append(j)
        if j == 1:
            raise ApmError(CONTRACT_VIOLATION, "x")
        return j

    async def item(i: int) -> list[Any]:
        return await gather_ordered(range(3), leaf, limit=4)

    out = await gather_ordered(range(2), item, limit=4)
    for _, got in out:
        assert [u for u, _ in got] == [0, 1]  # 계약 위반 항목까지만
    assert called == [0, 1, 0, 1]


async def test_gather_inside_sequential_outer_stays_concurrent():
    peak = _Peak()

    async def leaf(j: int) -> int:
        await peak.hit(0.02)
        return j

    async def item(i: int) -> list[Any]:
        return await gather_ordered(range(4), leaf, limit=4)

    await gather_ordered(range(2), item, limit=1)  # 바깥은 차례(태스크 없음) — 표지 없음
    assert peak.peak == 4


async def test_batch_job_outstanding_http_stays_within_limit(tmp_path):
    """배치 작업 1건(전 단위 항목 포함) — 동시 미결 HTTP 최대 ≤ `APM_FANOUT_CONCURRENCY`(4).
    명단은 미리 적재한다(공유 적재는 따로 만든 맥락에서 제 동시로 돈다)."""
    fake = Fake(unit_delay=0.004)
    peak = _Peak()
    original = fake.handler

    async def counted(request: httpx.Request) -> httpx.Response:
        peak.inflight += 1
        peak.peak = max(peak.peak, peak.inflight)
        try:
            return await original(request)
        finally:
            peak.inflight -= 1

    fake.handler = counted  # type: ignore[method-assign]
    tools = _tools(fake, 4)
    for src in tools.sources:
        await src.resolver.inventory()
    peak.peak = 0
    targets = [{"hostname": "was-host01"}] * 2 + TARGETS_WITH_ID
    _, call = targeted(
        tools, "apm_app_health", targets, _health(tools), label="*", keys=TARGET_KEYS_WITH_ID
    )
    out, audit = await _run_job(tmp_path, tools, "apm_app_health", lambda _t: call())
    assert "error" not in out and audit["api_calls"] > 4 * len(targets)
    assert 1 < peak.peak <= 4, peak.peak


# ── _Plan 동시 의미 ───────────────────────────────────────────────


class _Outer(CallScope):
    def __init__(self) -> None:
        super().__init__()
        self.expected = 0

    def expect_calls(self, count: int) -> None:
        self.expected += count


def test_plan_unit_is_first_item_to_report_and_final_is_sum():
    outer = _Outer()
    plan = _Plan(outer, 5)
    for i in (0, 1, 2):  # 세 항목이 함께 돈다
        plan.begin(i)
    assert outer.expected == 0
    _ItemScope(plan, 2).expect_calls(3)  # 셋째가 먼저 신고 → 단위 3
    assert outer.expected == 3 * 5  # 도는 셋 max(신고, 3) + 시작 전 둘 × 3
    _ItemScope(plan, 0).expect_calls(5)  # 첫째 신고는 단위를 바꾸지 않는다(처음 신고한 항목 기준)
    assert outer.expected == 5 + 3 + 3 + 3 * 2
    _ItemScope(plan, 2).expect_calls(1)  # 단위 항목이 늘면 따라 는다 → 4
    assert outer.expected == 5 + 4 + 4 + 4 * 2
    plan.end(2)  # 단위는 끝난 항목의 최종 신고(4)로 고정
    plan.end(1)  # 신고 없이 끝남(0)
    assert outer.expected == 5 + 0 + 4 + 4 * 2
    for i, count in ((3, 2), (4, 6)):
        plan.begin(i)
        _ItemScope(plan, i).expect_calls(count)
        plan.end(i)
    plan.end(0)
    assert outer.expected == 5 + 0 + 4 + 2 + 6  # 끝 = 항목 신고 합계


def test_plan_unit_moves_on_when_first_reporter_returns_to_zero():
    outer = _Outer()
    plan = _Plan(outer, 3)
    plan.begin(0)
    plan.begin(1)
    _ItemScope(plan, 1).expect_calls(2)
    _ItemScope(plan, 0).expect_calls(7)
    assert outer.expected == 7 + 2 + 2
    _ItemScope(plan, 1).expect_calls(-2)  # 단위 항목이 0으로 → 다음 신고 항목(0)이 단위
    assert outer.expected == 7 + 7 + 7


# ── 소요 단축(느슨) ───────────────────────────────────────────────


async def test_batch_concurrency_shortens_wall_time():
    """단위 하나씩 좁힌 항목 4개 × 응답 0.1s(1s 축척 0.1) — 명단은 미리 적재해 배치만 잰다."""
    targets = [
        {"hostname": "was-host01", "source_id": sid, "instance_id": iid}
        for sid in ("bank", "common")
        for iid in (101, 201)
    ]
    took = {}
    for fanout in (1, 4):
        fake = Fake()
        fake.fixed_delay = 0.1
        tools = _tools(fake, fanout)
        await tools.resolve("was-host01", None, None, None)
        started = time.monotonic()
        out = await run_batch(
            tools, "apm_active_services", targets, _active(tools), keys=TARGET_KEYS_WITH_ID
        )
        took[fanout] = time.monotonic() - started
        assert "error" not in out and [b["status"] for b in out["batch"]] == ["ok"] * 4
    print(f"\n배치 소요: 순차 {took[1]:.2f}s · 동시 4 {took[4]:.2f}s")
    assert took[1] >= 0.38
    assert took[4] < took[1] * 0.5


# ── R-1 배치 조합 — 작업 4 × 항목 4 × 하위 독립 호출 3 · 자리 8 ───────────────


async def run_r1_batch(
    scale: float, *, jobs: int = 4, items: int = 4, calls: int = 3
) -> dict[str, Any]:
    """동시 작업 `jobs`개 × 작업마다 `targets` 항목 `items`개 × 항목 하위 독립 호출 `calls`건(도구
    동시 4) — 응답 3s·5s · 자리 8 · 초당 5 · timeout 30s를 `scale`배 시간으로. (최대 자리 대기,
    초과 건수, 오류 수, 호출 수, 소요)."""
    cfg = load_config(
        {
            "JENNIFER_API_URL": "http://apm.test",
            "JENNIFER_API_TOKEN": TOKEN,
            "JENNIFER_RATE_LIMIT_PER_SEC": str(5 / scale),
            "JENNIFER_API_TIMEOUT_SECONDS": str(30 * scale),
            "JENNIFER_MAX_INFLIGHT_PER_SOURCE": "8",
            "APM_PARSE_WORKERS": "0",
            "APM_FANOUT_CONCURRENCY": "4",
        }
    )
    assert cfg.sources[0].max_inflight_per_source == 8 and cfg.perf.fanout_concurrency == 4

    async def handler(request: httpx.Request) -> httpx.Response:
        d = int(request.url.params["domain_id"])
        await asyncio.sleep((3 if d % 2 else 5) * scale)
        return httpx.Response(200, json={"result": []})

    tools = ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(handler)), cfg, clock=lambda: NOW_S
    )
    client: JenniferClient = next(iter(tools.sources)).api.client
    loop = asyncio.get_running_loop()
    waits: list[float] = []
    errors: list[ApmError] = []
    original = client._slots.acquire

    async def timed(priority: str = PRIORITY_INTERACTIVE, caller: str = "") -> None:
        started = loop.time()
        try:
            await original(priority, caller)
        finally:
            waits.append(loop.time() - started)

    client._slots.acquire = timed  # type: ignore[method-assign]

    async def item(t: Target) -> dict[str, Any]:
        base = int(str(t.hostname)[1:]) * calls
        got = await gather_ordered(
            range(calls),
            lambda i: client.get_json("/api/realtime/instance", {"domain_id": base + i + 1}),
            limit=cfg.perf.fanout_concurrency,
        )
        errors.extend(r for _, r in got if isinstance(r, ApmError))
        return tools.ok("apm_x", [])

    targets = [{"hostname": f"h{k}"} for k in range(items)]
    started = loop.time()
    outs = await asyncio.gather(
        *(run_batch(tools, "apm_x", targets, item, keys=TARGET_KEYS) for _ in range(jobs))
    )
    elapsed = loop.time() - started
    await client.aclose()
    over = [
        e for e in errors if e.code == SOURCE_UNAVAILABLE and "동시 호출 상한 대기 초과" in e.reason
    ]
    assert all("error" not in out for out in outs)
    return {
        "max_slot_wait": max(waits),
        "over": len(over),
        "errors": len(errors),
        "calls": client.calls_total,
        "elapsed": elapsed,
    }


async def test_r1_batch_no_slot_wait_timeout():
    result = await run_r1_batch(0.1)
    print(f"\nR-1 배치(축척 0.1): {result}")
    assert result["over"] == 0 and result["errors"] == 0
    assert result["calls"] == 4 * 4 * 3
    assert result["max_slot_wait"] < 3.0  # timeout(30s × 0.1)
