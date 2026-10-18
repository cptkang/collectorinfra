"""plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지(갱신이 필요하면
리팩터가 아니다).

APM 도구 출력 JSON 골든(스멜 M4 — 긴 함수 분해 전 안전망). 대상과 대표 입력:

| 대상 | 케이스 |
|---|---|
| `FleetTools.ranking`(실시간) | `fleet_ranking_*` |
| `FleetTools._period_ranking` | `fleet_period_ranking_*` |
| `ManageTools._event_rules` | `event_rules_*` |
| `ApmTools._business_search` | `business_search_*` |
| `ApmTools.apm_status_stats` | `status_stats_*` |
| 순수 함수 `_period_stats`·`_sum_stats`·`_stats_delta` | `pure_*` |

응답 원천은 기존 테스트의 합성 Open API(`conftest` 합성 픽스처 · `fleet_fake` · W6
`PeriodFake` · W7 `w7_handler` · plans/130 W2 업무 데이터)다 — `httpx.MockTransport`만 쓴다(네트워크
0). 시계는 `conftest.NOW_S` 고정이고, 임시 디렉터리 경로는 `<tmp>`로 바꾼다. 출력은
`json.dumps(sort_keys=True, ensure_ascii=False)`로 대조한다.

갱신: `UPDATE_PLAN162_SNAPSHOT=1`일 때만 파일을 쓴다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import ApmError
from conftest import make_tools, synthetic_handler
from mock_openapi import W7Mock
from test_plan130_w2_business import MAPPING, _one
from test_plan134_w6_period_ranking import T0, PeriodFake, _iso, _ready
from test_plan134_w7_tools import manage, w7_handler

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "plan166_w0" / "tool_output_golden.json"
UPDATE = os.environ.get("UPDATE_PLAN162_SNAPSHOT") == "1"

Case = Callable[[Path], Awaitable[Any]]


# ── FleetTools.ranking · _period_ranking ─────────────────────────


async def fleet_ranking_realtime(tmp_path: Path) -> Any:
    fleet = await _ready(PeriodFake(3), tmp_path)
    return await fleet.ranking(n=5)


async def fleet_ranking_period_metric_falls_back_to_current(tmp_path: Path) -> Any:
    fleet = await _ready(PeriodFake(2), tmp_path)
    return await fleet.ranking(
        metric="visit_day", reference_time=_iso(T0), lookback_minutes=60, n=3
    )


async def fleet_period_ranking_weighted(tmp_path: Path) -> Any:
    fake = PeriodFake(1, hosts=("apm.test",), per_domain=4)
    fake.app_rows = {
        ("apm.test", 100): [
            {
                "name": "/a",
                "calls": 10,
                "failures": 1,
                "responseTime": 100.0,
                "totalResponseTime": 1000,
                "maxResponseTime": 900,
            },
            {
                "name": "/b",
                "calls": 30,
                "failures": 3,
                "responseTime": 200.0,
                "totalResponseTime": 6000,
                "maxResponseTime": 1200,
            },
        ],
        ("apm.test", 101): [  # 총 응답시간 칸 없음 → 평균 계산 불가
            {"name": "/c", "calls": 5, "failures": 0, "responseTime": 50.0, "maxResponseTime": 70},
        ],
        ("apm.test", 102): [
            {
                "name": "/d",
                "calls": 2,
                "failures": 0,
                "responseTime": 10.0,
                "totalResponseTime": 20,
                "maxResponseTime": 15,
            },
        ],
        # 103: 통계 행 없음
    }
    fleet = await _ready(fake, tmp_path)
    ref = _iso(T0 + 20 * 60_000)
    return {
        "desc_avg": await fleet.ranking(reference_time=ref, lookback_minutes=90),
        "asc_calls": await fleet.ranking(
            metric="calls", order="asc", reference_time=ref, lookback_minutes=90
        ),
    }


async def fleet_period_ranking_partial(tmp_path: Path) -> Any:
    fake = PeriodFake(4, per_domain=2)
    fake.instance_fail = {("bank.test", 3)}
    fleet = await _ready(fake, tmp_path)
    fake.app_fail = {("bank.test", 100), ("bank.test", 101), ("common.test", 201)}
    return await fleet.ranking(metric="calls", n=5, lookback_minutes=120)


# ── ManageTools._event_rules ────────────────────────────────────


async def event_rules_all_types_for_host(tmp_path: Path) -> Any:
    return await manage(w7_handler()).apm_config("event_rules", hostname="was-host01")


async def event_rules_error_type_applied_and_individual(tmp_path: Path) -> Any:
    mock = W7Mock()
    mock.individual["1000/OUTOFMEMORY/1001"] = True
    return await manage(w7_handler(mock)).apm_config(
        "event_rules", hostname="was-host01", rule_type="error", error_type="outofmemory"
    )


async def event_rules_without_host_business_target(tmp_path: Path) -> Any:
    return await manage(w7_handler()).apm_config(
        "event_rules", target="business", error_type="OUTOFMEMORY"
    )


async def event_rules_compare_spelling_fallback(tmp_path: Path) -> Any:
    mock = W7Mock()
    mock.compare_404 = True
    return await manage(w7_handler(mock)).apm_config(
        "event_rules", hostname="was-host01", rule_type="compare", target="instance"
    )


# ── ApmTools._business_search ───────────────────────────────────


async def business_search_definition_trace(tmp_path: Path) -> Any:
    return await _one(tmp_path, []).apm_instance_map(business="결제")


async def business_search_union(tmp_path: Path) -> Any:
    return await _one(tmp_path, []).apm_instance_map(business="settle")


async def business_search_manual_mapping(tmp_path: Path) -> Any:
    return await _one(tmp_path, [], policy=MAPPING).apm_instance_map(business="CARD BIZ")


async def business_search_definition_failure_partial(tmp_path: Path) -> Any:
    tools = _one(tmp_path, [], fail={"/api/business": set()})
    return await tools.apm_instance_map(business="settle")


# ── ApmTools.apm_status_stats ───────────────────────────────────


def _synthetic_tools(tmp_path: Path) -> ApmTools:
    built = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        extra={"APM_SPOOL_DIR": str(tmp_path / "spool")},
    )
    tools: ApmTools = built[0]
    return tools


async def status_stats_application(tmp_path: Path) -> Any:
    return await _synthetic_tools(tmp_path).apm_status_stats(
        "application", "was-host01", sort_by="calls", n=5, application_name="/order"
    )


async def status_stats_sql_hour_widened(tmp_path: Path) -> Any:
    return await _synthetic_tools(tmp_path).apm_status_stats(
        "sql", "was-host01", reference_time="2026-09-29T10:23:00+09:00", lookback_minutes=30
    )


async def status_stats_external_call_full(tmp_path: Path) -> Any:
    return await _synthetic_tools(tmp_path).apm_status_stats(
        "external_call", "was-host01", sort_by="maxResponseTime", full=True
    )


# ── 순수 함수 ────────────────────────────────────────────────────

_APP_ROWS: list[dict[str, Any]] = [
    {"calls": 10, "failures": 1, "total_response_ms": 1000, "max_response_time_ms": 900},
    {"calls": 30, "failures": 3, "total_response_ms": 6000, "max_response_time_ms": 1200},
    {"calls": 0, "failures": 0, "max_response_time_ms": None},  # 호출 0 — 총 응답시간 없어도 됨
]


async def pure_period_stats(tmp_path: Path) -> Any:
    return {
        "weighted": ApmTools._period_stats(_APP_ROWS),
        "missing_total": ApmTools._period_stats(
            [*_APP_ROWS, {"calls": 5, "failures": 0, "max_response_time_ms": 70}]
        ),
        "zero_calls_only": ApmTools._period_stats(
            [{"calls": 0, "failures": 0, "total_response_ms": 0}]
        ),
        "empty": ApmTools._period_stats([]),
    }


async def pure_sum_stats(tmp_path: Path) -> Any:
    a, _ = ApmTools._period_stats(_APP_ROWS)
    b = {"calls": 5, "failures": 2, "total_response_ms": 500.0, "max_response_ms": 3000}
    no_total = {"calls": 4, "failures": 0, "total_response_ms": None, "max_response_ms": None}
    idle = {"calls": 0, "failures": 0, "total_response_ms": None, "max_response_ms": None}
    return {
        "two": ApmTools._sum_stats([a, b]),
        "missing_total": ApmTools._sum_stats([a, no_total]),
        "idle_ignored": ApmTools._sum_stats([b, idle]),
        "empty": ApmTools._sum_stats([]),
    }


async def pure_stats_delta(tmp_path: Path) -> Any:
    current = {
        "calls": 120,
        "failures": 6,
        "failure_rate": 0.05,
        "avg_response_ms": 250.0,
        "max_response_ms": 4000,
    }
    baseline = {
        "calls": 100,
        "failures": 2,
        "failure_rate": 0.02,
        "avg_response_ms": 200.0,
        "max_response_ms": None,
    }
    zero = {
        "calls": 0,
        "failures": 0,
        "failure_rate": None,
        "avg_response_ms": None,
        "max_response_ms": None,
    }
    return {
        "both": ApmTools._stats_delta(current, baseline),
        "baseline_zero": ApmTools._stats_delta(current, zero),
        "baseline_missing": ApmTools._stats_delta(current, None),
        "current_missing": ApmTools._stats_delta(None, baseline),
    }


CASES: dict[str, Case] = {
    case.__name__: case
    for case in (
        fleet_ranking_realtime,
        fleet_ranking_period_metric_falls_back_to_current,
        fleet_period_ranking_weighted,
        fleet_period_ranking_partial,
        event_rules_all_types_for_host,
        event_rules_error_type_applied_and_individual,
        event_rules_without_host_business_target,
        event_rules_compare_spelling_fallback,
        business_search_definition_trace,
        business_search_union,
        business_search_manual_mapping,
        business_search_definition_failure_partial,
        status_stats_application,
        status_stats_sql_hour_widened,
        status_stats_external_call_full,
        pure_period_stats,
        pure_sum_stats,
        pure_stats_delta,
    )
}


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


async def _run(name: str, tmp_path: Path) -> Any:
    """케이스 출력(오류면 오류 코드·사유) → JSON 왕복 · 임시 경로 정규화."""
    try:
        out = await CASES[name](tmp_path)
    except ApmError as e:
        out = {"raised": {"code": e.code, "reason": e.reason}}
    text = json.dumps(out, ensure_ascii=False, sort_keys=True).replace(str(tmp_path), "<tmp>")
    return json.loads(text)


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return data


COMPARE = pytest.mark.skipif(UPDATE, reason="UPDATE_PLAN162_SNAPSHOT=1 — 채록 모드(대조 생략)")
RECORD = pytest.mark.skipif(not UPDATE, reason="채록은 UPDATE_PLAN162_SNAPSHOT=1일 때만")
MISSING = f"스냅샷이 없다: {SNAPSHOT} — UPDATE_PLAN162_SNAPSHOT=1로 최초 채록하라"


@RECORD
@pytest.mark.asyncio
async def test_record_golden(tmp_path: Path) -> None:
    """채록 전용(평시 skip) — 전 케이스를 모아 파일을 1회 쓴다(케이스마다 별 임시 디렉터리)."""
    golden: dict[str, Any] = {}
    for name in CASES:
        case_dir = tmp_path / name
        case_dir.mkdir()
        golden[name] = await _run(name, case_dir)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(_dump(golden), encoding="utf-8")


@COMPARE
@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(CASES))
async def test_tool_output_matches_golden(name: str, tmp_path: Path) -> None:
    assert SNAPSHOT.exists(), MISSING
    golden = _load()
    assert name in golden, f"골든에 케이스 없음 — UPDATE_PLAN162_SNAPSHOT=1로 채록하라: {name}"
    actual = await _run(name, tmp_path)
    assert json.dumps(actual, ensure_ascii=False, sort_keys=True) == json.dumps(
        golden[name], ensure_ascii=False, sort_keys=True
    )


@COMPARE
def test_golden_has_no_stale_cases() -> None:
    assert SNAPSHOT.exists(), MISSING
    assert sorted(_load()) == sorted(CASES)
