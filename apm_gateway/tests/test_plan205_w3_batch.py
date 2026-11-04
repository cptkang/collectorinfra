"""`targets` 배치 항목 동시 실행 (plans/205 W3 · D-356 ①).

- ① 폭 4와 폭 1(종전 순차)의 배치 봉투가 같다 — 항목 순서·`target_index`·부분 실패 고지·하위
  봉투 고지(자격증명 메모·가린 칸)·최상위 `limits` 합집합 · 작업 감사(호출 수·최종 예상 호출 수).
  앞 항목일수록 늦게 끝나게 지연을 준다(응답 역순 도착).
- 전부 실패 오류 봉투가 같다(대상 해석 실패 · 전송 실패).
- 동시에 도는 항목끼리 하위 봉투 메모가 섞이지 않는다.
- `_Plan` 항목별 카운터 — 시작·신고·종료가 어떻게 엇갈려도 최종 예상 = 신고 합계(순차와 같음).
- ② 지연 가짜 전송에서 소요가 줄어든다(느슨).
- 위험 R-1 배치 조합(시간 축척 ×0.1): 동시 작업 4개 × 배치 항목 6개 × 하위 도구 독립 호출 4건 —
  자리 8 · 초당 5/0.1 · timeout 30×0.1초 · 지연 3·5×0.1초 → 자리 대기 초과(`source_unavailable`
  「동시 호출 상한 대기 초과」) 0건을 단언한다. 폭 4는 초과가 나서 `xfail(strict)`로
  고정했다 — 이 실측으로 기본값을 2로 정했다(D-356 부기 1). 폭 2(기본)는 통과.
  실 축척(×1)은 테스트 밖 스크립트로 1회 잰다.

외부 호출 0 — httpx `MockTransport`(지연) · 실 클라이언트·작업 관리자.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from apm_gateway.application.batch import (
    TARGET_KEYS,
    TARGET_KEYS_WITH_ID,
    Target,
    _ItemScope,
    _Plan,
    run_batch,
)
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import CallScope
from test_plan205_w2a_fanout import (
    HOST,
    SLOT_TIMEOUT,
    FakeApm,
    _as_job,
    _audit,
    _manager,
    _tools,
    _two_sources,
    _warm,
)

Call = Callable[[ApmTools, Target], Awaitable[dict[str, Any]]]

# 대상 항목 — 전 소스 호스트 · 소스 1개 이름 · 해석 실패 · 소스 한정 호스트 · 다른 소스 이름
MIXED_TARGETS: list[dict[str, Any]] = [
    {"hostname": HOST},
    {"instance_name": "bank-i100"},
    {"instance_name": "nope-i999"},
    {"hostname": HOST, "source_id": "common"},
    {"instance_name": "common-i201"},
]


def _health(tools: ApmTools, t: Target) -> Awaitable[dict[str, Any]]:
    return tools.apm_runtime_health(
        hostname=t.hostname,
        instance_name=t.instance_name,
        source_ids=t.sources(None),
        lookback_minutes=30,
    )


def _active(tools: ApmTools, t: Target) -> Awaitable[dict[str, Any]]:
    return tools.apm_active_services(
        hostname=t.hostname, instance_name=t.instance_name, source_ids=t.sources(None)
    )


def _pool(tools: ApmTools, t: Target) -> Awaitable[dict[str, Any]]:
    return tools.apm_resource_pool(
        hostname=t.hostname, instance_name=t.instance_name, source_ids=t.sources(None)
    )


def _slow_first(host: str, tag: str, key: int) -> float:
    """앞 항목이 늦게 끝나게 — `bank` 소스·작은 키일수록 느리다(응답 역순 도착)."""
    return 0.004 * (3 if host.startswith("bank") else 1) * (1 + 1 / (1 + key % 100))


async def _batch_runs(
    tmp_path,
    make_fake: Callable[[], FakeApm],
    tool: str,
    targets: list[dict[str, Any]],
    call: Call,
    *,
    keys: frozenset[str] = TARGET_KEYS,
    delay: Callable[[str, str, int], float] = _slow_first,
) -> dict[int, tuple[dict[str, Any], tuple[int, int, dict[str, int], int], int]]:
    """폭 1·4로 같은 배치를 작업으로 돌려 (봉투, 감사, 최대 in-flight)를 모은다."""
    out = {}
    for fanout in (1, 4):
        fake = make_fake()
        tools = _tools(fake, tmp_path / f"w{fanout}", fanout)
        await _warm(tools)
        fake.delay = delay
        fake.peak = 0
        env, job = await _as_job(
            _manager(tools),
            tools,
            tool,
            lambda tools=tools: run_batch(
                tools, tool, targets, lambda t: call(tools, t), keys=keys
            ),
        )
        out[fanout] = (env, _audit(job), fake.peak)
    return out


def _same(got: dict[int, tuple[dict[str, Any], Any, int]]) -> dict[str, Any]:
    (serial, serial_audit, serial_peak), (fan, fan_audit, fan_peak) = got[1], got[4]
    assert fan == serial
    assert fan_audit == serial_audit  # 호출 수·공유 호출·소스별 호출·최종 예상 호출 수
    assert serial_peak == 1 and fan_peak > 1
    return serial


def _with_notes(make: Callable[[], FakeApm]) -> Callable[[], FakeApm]:
    def wrapped() -> FakeApm:
        fake = make()
        fake.notes = True
        return fake

    return wrapped


# ── ① 봉투·감사 동일 — 부분 실패 · 하위 고지 · 최상위 limits ─────────────────


@pytest.mark.parametrize(
    "tool,call",
    [("apm_runtime_health", _health), ("apm_active_services", _active)],
)
async def test_batch_fanout_matches_serial(tmp_path, tool, call):
    got = await _batch_runs(
        tmp_path, _with_notes(_two_sources()), tool, MIXED_TARGETS, call
    )
    env = _same(got)
    # 항목 순서 · 대상 원문 · 부분 실패(해석 실패 1건)
    assert [e["index"] for e in env["batch"]] == list(range(len(MIXED_TARGETS)))
    assert [e["target"] for e in env["batch"]] == MIXED_TARGETS
    assert [e["status"] for e in env["batch"]] == ["ok", "ok", "error", "ok", "ok"]
    assert env["partial"] is True
    assert env["limits"][0].startswith("[한계] 대상 1/5 조회 실패: nope-i999(")
    # 행은 항목 순으로 붙는다(target_index 비감소 · 항목별 행 수 = 하위 row_count)
    idx = [r["target_index"] for r in env["rows"]]
    assert idx == sorted(idx) and set(idx) == {0, 1, 3, 4}
    assert [idx.count(i) for i in (0, 1, 3, 4)] == [
        env["batch"][i]["row_count"] for i in (0, 1, 3, 4)
    ]
    # 하위 봉투 고지 — 자격증명 메모·가린 칸이 그 항목 몫으로만 실린다
    bank_only = env["batch"][1]
    assert any("deep_bank_" in x for x in bank_only["limits"])
    assert not any("deep_common_" in x for x in bank_only["limits"])
    common_only = env["batch"][3]
    assert any("deep_common_" in x for x in common_only["limits"])
    assert not any("deep_bank_" in x for x in common_only["limits"])
    assert any(d["kind"] == "apm_masked_fields" for d in bank_only["disclosures"])
    # 최상위 limits = 실패 줄 + 하위 [한계] 합집합(항목 순 · 중복 제거)
    union = list(
        dict.fromkeys(x for e in env["batch"] if e["status"] == "ok" for x in e["limits"])
    )
    assert env["limits"][1:] == union


async def test_batch_with_instance_ids_matches_serial(tmp_path):
    targets = [
        {"hostname": HOST, "instance_id": 101, "source_id": "bank"},
        {"hostname": HOST, "instance_id": 300, "source_id": "common"},
        {"hostname": HOST, "instance_id": 99999},  # 정합 실패
        {"instance_name": "bank-i201"},
    ]

    def call(tools: ApmTools, t: Target) -> Awaitable[dict[str, Any]]:
        return tools.apm_resource_pool(
            hostname=t.hostname,
            instance_id=t.instance_id,
            instance_name=t.instance_name,
            source_ids=t.sources(None),
        )

    env = _same(
        await _batch_runs(
            tmp_path,
            _with_notes(_two_sources()),
            "apm_resource_pool",
            targets,
            call,
            keys=TARGET_KEYS_WITH_ID,
        )
    )
    assert [e["status"] for e in env["batch"]] == ["ok", "ok", "error", "ok"]


# ── 전부 실패 오류 봉투 ─────────────────────────────────────────


async def test_all_items_failed_gives_same_error_envelope(tmp_path):
    unresolved = [{"instance_name": f"nope-i{k}"} for k in range(5)]
    env = (
        await _batch_runs(
            tmp_path / "unresolved", _two_sources(), "apm_resource_pool", unresolved, _pool
        )
    )
    assert env[1][0] == env[4][0] and env[1][1] == env[4][1]
    serial = env[1][0]
    assert serial["error"] == "instance_unresolved"
    assert serial["reason"].startswith("대상 5개 모두 실패 — ")
    assert [e["status"] for e in serial["batch"]] == ["error"] * 5

    def all_down() -> FakeApm:
        fake = FakeApm(3)
        fake.fail = {
            (h, tag, d) for h in fake.hosts for d in fake.domains for tag in ("realtime", "active")
        }
        return fake

    names = [{"instance_name": f"{s}-i{d}00"} for s in ("bank", "common") for d in (1, 2, 3)]
    got = await _batch_runs(tmp_path / "down", all_down, "apm_resource_pool", names, _pool)
    serial = _same(got)
    assert serial["error"] == "source_unavailable", serial
    assert serial["reason"].startswith("대상 6개 모두 실패 — bank-i100(source_unavailable)")
    assert [e["status"] for e in serial["batch"]] == ["error"] * 6


# ── `_Plan` 항목별 카운터 ──────────────────────────────────────


class _Expect(CallScope):
    def __init__(self) -> None:
        super().__init__()
        self.reports: list[int] = []

    def expect_calls(self, count: int) -> None:
        self.reports.append(count)


def _sequential(counts: list[list[int]]) -> list[int]:
    outer = _Expect()
    plan = _Plan(outer, len(counts))
    for item in counts:
        scope = _ItemScope(plan)
        plan.begin()
        for c in item:
            scope.expect_calls(c)
        plan.end()
    return outer.reports


@pytest.mark.parametrize("seed", range(20))
def test_plan_final_estimate_equals_serial_for_any_interleaving(seed):
    rng = random.Random(seed)
    counts = [[rng.choice([0, 1, 2, 3, 5]) for _ in range(rng.randint(0, 3))] for _ in range(6)]
    outer = _Expect()
    plan = _Plan(outer, len(counts))
    pending = list(range(len(counts)))
    live: dict[int, tuple[int, _ItemScope, list[int]]] = {}  # 항목 → (번호, 맥락, 남은 신고)
    while pending or live:
        moves = (["begin"] if pending and len(live) < 4 else []) + (["step"] if live else [])
        if rng.choice(moves) == "begin":
            item = pending.pop(0)
            slot = plan.begin()
            live[item] = (slot, _ItemScope(plan, slot), list(counts[item]))
            continue
        item = rng.choice(sorted(live))
        slot, scope, rest = live[item]
        if rest:
            scope.expect_calls(rest.pop(0))
        else:
            plan.end(slot)
            del live[item]
    serial = _sequential(counts)
    assert sum(outer.reports) == sum(serial) == sum(map(sum, counts))
    assert plan.posted == sum(map(sum, counts))


def test_plan_one_at_a_time_matches_former_sequence():
    """한 번에 하나만 돌면(폭 1) 바깥 신고 차이 순서가 종전 「도는 항목 1개」 계산과 같다 —
    `test_plan134_w3_targets`의 단언 수치를 순서까지 고정한다."""
    assert _sequential([[3, 1], [2], [], [5]]) == [12, 4, -2, -4, 1]
    for counts in itertools.product([[], [2], [1, 3]], repeat=3):
        reports = _sequential(list(counts))
        assert sum(reports) == sum(map(sum, counts))


# ── ② 소요 단축(느슨) ───────────────────────────────────────────


async def test_batch_fanout_shortens_wall_time(tmp_path):
    targets = [{"instance_name": f"bank-i{d}00"} for d in range(1, 9)]
    elapsed = {}
    for fanout in (1, 4):
        fake = FakeApm(8, hosts=("bank.test",), per_domain=1)
        tools = _tools(fake, tmp_path / f"w{fanout}", fanout)
        await _warm(tools)
        fake.delay = lambda host, tag, key: 0.05
        started = time.monotonic()
        out = await run_batch(
            tools, "apm_resource_pool", targets, lambda t, tools=tools: _pool(tools, t),
            keys=TARGET_KEYS,
        )
        elapsed[fanout] = time.monotonic() - started
        assert [e["status"] for e in out["batch"]] == ["ok"] * 8
    print(f"\n[plans/205 W3 소요] 순차 {elapsed[1]:.3f}초 · 폭 4 {elapsed[4]:.3f}초")
    assert elapsed[1] >= 8 * 0.05 * 0.95
    assert elapsed[4] < elapsed[1] * 0.6


# ── 위험 R-1 배치 조합 — 자리 대기 초과 ─────────────────────────────


async def run_r1_batch(
    tmp_path,
    *,
    scale: float,
    fanout: int,
    jobs: int = 4,
    items: int = 6,
    calls: int = 4,
    same_owner: bool = False,
) -> dict[str, Any]:
    """작업 `jobs`개가 동시에 `apm_config(db_path)` 배치(대상 `items`개 · 항목마다 도메인
    `calls`개 = 독립 호출 `calls`건)를 부른다. 자리 8 · 초당 5/`scale` · timeout 30×`scale`초 · 지연
    3·5×`scale`초(도메인 홀짝). 실효 동시 요청 = 작업 × 항목 폭 × 도구 폭."""
    fake = FakeApm(calls, hosts=("bank.test",), per_domain=1)
    tools = _tools(
        fake,
        tmp_path,
        fanout,
        JENNIFER_MAX_INFLIGHT_PER_SOURCE="8",
        JENNIFER_RATE_LIMIT_PER_SEC=str(5 / scale),
        JENNIFER_API_TIMEOUT_SECONDS=str(30 * scale),
        APM_JOB_MAX_CONCURRENT=str(jobs),
    )
    await _warm(tools)
    fake.delay = lambda host, tag, key: (3 if key % 2 else 5) * scale
    manage = ManageTools(tools)
    targets = [{"hostname": HOST} for _ in range(items)]
    mgr = _manager(tools)
    started = time.monotonic()
    results = await asyncio.gather(
        *(
            _as_job(
                mgr,
                tools,
                "apm_config",
                lambda: run_batch(
                    tools,
                    "apm_config",
                    targets,
                    lambda t: manage.apm_config("db_path", t.hostname),
                    keys=TARGET_KEYS,
                ),
                owner="o" if same_owner else f"o{k}",
            )
            for k in range(jobs)
        )
    )
    took = time.monotonic() - started
    texts = [
        " ".join(
            [str(env.get("reason", "")), *(env.get("limits") or [])]
            + [str(e.get("reason", "")) for e in env.get("batch") or []]
        )
        for env, _ in results
    ]
    waits = [job.waits() for _, job in results]
    db_path_calls = sum(1 for _, tag, _ in fake.calls if tag == "dbpath")
    return {
        "seconds": round(took, 2),
        # 자리 대기 초과 호출은 전송에 닿지 않는다(속도 제어 대기·HTTP timeout은 여기서 나지 않는다)
        # — 하위 봉투는 도메인 실패를 코드만 적어 문구로는 다 못 센다(문구 수는 따로 싣는다)
        "slot_timeouts": jobs * items * calls - db_path_calls,
        "slot_timeout_texts": sum(t.count(SLOT_TIMEOUT) for t in texts),
        "errors": sum("error" in env for env, _ in results),
        "failed_items": sum(
            e["status"] == "error" for env, _ in results for e in env.get("batch") or []
        ),
        "slot_wait_max_ms": max(w["slot_wait_ms"]["max"] for w in waits),
        "throttle_wait_max_ms": max(w["throttle_wait_ms"]["max"] for w in waits),
        "db_path_calls": db_path_calls,
        "expected_calls": [job.expected_calls for _, job in results],
        "peak_in_flight": fake.peak,
    }


# 폭 4는 항목 폭 4 × 도구 폭 4 × 작업 4 = 동시 요청 64건이 자리 8을 다퉈 대기 초과가 난다(실측
# 축척 ×0.1 · ×1 모두) → 기본값을 2로 정했다(D-356 부기 1). 폭 2(기본 — 16건 × 4)는 0건.
R1_BATCH_CASES = [
    pytest.param(
        4,
        id="fanout4-rejected",
        marks=pytest.mark.xfail(
            strict=True,
            reason=(
                "plans/205 W3 R-1 배치 조합: 폭 4에서 자리 대기 초과 — "
                "그래서 기본값 2(D-356 부기 1)"
            ),
        ),
    ),
    pytest.param(2, id="fanout2-default"),
]


@pytest.mark.parametrize("fanout", R1_BATCH_CASES)
async def test_r1_batch_no_slot_wait_timeout_scaled(tmp_path, fanout):
    got = await asyncio.wait_for(run_r1_batch(tmp_path, scale=0.1, fanout=fanout), 15)
    print(f"\n[plans/205 W3 R-1 배치 축척 ×0.1 · 폭 {fanout}] {got}")
    assert got["expected_calls"] == [6 * 4] * 4  # 최종 예상 = 항목 신고 합계(초과가 나도 같다)
    assert got["peak_in_flight"] == 8  # 자리 상한까지 찼다(상한은 넘지 않는다)
    assert got["slot_timeouts"] == 0 and got["slot_timeout_texts"] == 0, got
    assert got["errors"] == 0 and got["failed_items"] == 0, got
    assert got["db_path_calls"] == 4 * 6 * 4
    assert got["slot_wait_max_ms"] < 30 * 0.1 * 1000
