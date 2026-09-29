"""WAS 시그니처 결정적 판정 — 목업 시나리오 6종 + 스레드 정체·오류 급증
(plans/87 §5.4(b) · J2/J3 수용 기준).

입력은 어댑터가 만든 중립 레코드(도구 출력과 같은 키)이고 LLM은 0회다. 시나리오별 기대 kind는
`sre_agent` 소비측 테스트가 복제한 픽스처(SPEC-apm-gateway §4)와 같다.
"""

from __future__ import annotations

import pytest
from apm_gateway.adapters.jennifer.fields import event_signal, normalize_event_type
from apm_gateway.domain import signals as sig

from apm_gateway.config import load_policies

TH = sig.WasThresholds()


def _series(values, step_ms=300_000):
    return [{"time_ms": 1_000_000 + i * step_ms, "value": v} for i, v in enumerate(values)]


def _kinds(signals):
    return sorted({s["kind"] for s in signals})


# ── 목업 WAS 시나리오 6종(큐잉 · DB 풀 · GC stall · 힙 · 슬로우 SQL · 외부 지연) ──────────


def test_scenario_queuing():
    out = sig.judge_app({"reject_rate": 3.5}, None, TH, instance_id=1001)
    assert _kinds(out) == ["was_service_queuing"]
    assert out[0]["level"] == "CRITICAL" and out[0]["category"] == "strong"
    ev = sig.signal_from_event(
        event_signal("ERROR_PLC_REJECTED"),
        "ERROR_PLC_REJECTED",
        instance_id=1001,
        source_tool="apm_events",
    )
    assert ev["kind"] == "was_service_queuing"


def test_scenario_db_pool():
    out = sig.judge_pool(
        {"db_pool_active": 20.0, "db_pool_configured_avg": 20}, TH, instance_id=1001
    )
    assert _kinds(out) == ["was_db_pool_exhaustion"]
    assert (
        sig.judge_pool({"db_pool_active": 10.0, "db_pool_configured_avg": 20}, TH, instance_id=1)
        == []
    )


def test_scenario_gc_stall():
    out = sig.judge_runtime({"gc_time_usage_pct": 15.0}, None, TH, instance_id=1001)
    assert _kinds(out) == ["was_gc_stall"]
    assert out[0]["level"] == "WARNING" and out[0]["category"] == "medium"


def test_scenario_heap_sustained_and_oom_event():
    trend = {
        "heap_used_mb": _series([800, 910, 920, 950]),
        "heap_committed_mb": _series([1000, 1000, 1000, 1000]),
    }
    out = sig.judge_runtime(None, trend, TH, instance_id=1001)
    assert _kinds(out) == ["was_heap_pressure"]
    oom = sig.signal_from_event(
        event_signal("OUTOFMEMORY"), "OUTOFMEMORY", instance_id=1001, source_tool="x"
    )
    assert (oom["level"], oom["category"]) == ("CRITICAL", "strong")
    # 같은 (kind, instance)는 가장 강한 것 1건만
    merged = sig.dedupe(out + [oom])
    assert len(merged) == 1 and merged[0]["level"] == "CRITICAL"


def test_heap_leak_staircase():
    used = [300, 320, 310, 420, 430, 425, 540, 560, 550]
    trend = {"heap_used_mb": _series(used), "heap_committed_mb": _series([1000] * len(used))}
    out = sig.judge_runtime(None, trend, TH, instance_id=1)
    assert _kinds(out) == ["was_heap_pressure"] and "계단" in out[0]["evidence"]


def test_scenario_slow_sql():
    txs = [
        {"response_time_ms": 1000, "sql_ms": 600, "fetch_ms": 150, "external_ms": 0}
        for _ in range(3)
    ]
    assert _kinds(sig.judge_transactions(txs, TH, instance_id=1001)) == ["was_slow_sql"]


def test_scenario_external_delay():
    txs = [
        {"response_time_ms": 1000, "sql_ms": 50, "fetch_ms": 0, "external_ms": 800}
        for _ in range(3)
    ]
    assert _kinds(sig.judge_transactions(txs, TH, instance_id=1001)) == ["was_external_call_delay"]


# ── 나머지 두 kind · 경계 ─────────────────────────────────────


def test_thread_pool_exhaustion():
    sql = {"elapsed_ms": 700_000, "running_mode": "SQL"}
    ext = {"elapsed_ms": 900_000, "running_mode": "EXTERNAL_CALL"}
    fresh = {"elapsed_ms": 1_000, "running_mode": "SQL"}
    # 장기 실행 2건뿐 — 최소 3건 미달
    assert sig.judge_active_services([sql, sql, fresh, fresh], TH, instance_id=1) == []
    # 같은 모드 비율 2/4 = 0.5 < 0.7
    assert sig.judge_active_services([sql, sql, ext, ext], TH, instance_id=1) == []
    # 3/4 = 0.75 ≥ 0.7
    out = sig.judge_active_services([sql, sql, sql, ext], TH, instance_id=1)
    assert (
        _kinds(out) == ["was_thread_pool_exhaustion"] and "running_mode=SQL" in out[0]["evidence"]
    )


def test_error_burst_requires_min_calls():
    assert (
        sig.judge_app(None, {"calls": 10, "errors": 5, "error_rate": 0.5}, TH, instance_id=1) == []
    )
    out = sig.judge_app(None, {"calls": 40, "errors": 10, "error_rate": 0.25}, TH, instance_id=1)
    assert _kinds(out) == ["was_error_burst"]


def test_no_signal_when_healthy():
    assert (
        sig.judge_runtime(
            {"heap_used_mb": 300.0, "heap_committed_mb": 1000.0, "gc_time_usage_pct": 1.0},
            None,
            TH,
            instance_id=1,
        )
        == []
    )
    assert sig.judge_app({"reject_rate": 0.0}, None, TH, instance_id=1) == []
    assert (
        sig.judge_transactions([{"response_time_ms": 100, "sql_ms": 10}], TH, instance_id=1) == []
    )


@pytest.mark.parametrize(
    "raw,norm",
    [
        ("ERROR_SERVICE_QUEUING", "SERVICE_QUEUING"),
        ("warning_jvm_heap_mem_high", "JVM_HEAP_MEM_HIGH"),
        ("DB_CONN_UNCLOSED", "DB_CONN_UNCLOSED"),
    ],
)
def test_event_type_prefix_normalized(raw, norm):
    assert normalize_event_type(raw) == norm
    assert event_signal(raw) is not None


def test_unmapped_event_has_no_signal():
    assert event_signal("ERROR_UNCAUGHT_EXCEPTION") is None


def test_percentile_nearest_rank():
    assert sig.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert sig.percentile([100, 200], 50) == 100
    assert sig.percentile([], 50) is None


def test_thresholds_file_matches_defaults():
    """게이트웨이 정책 파일(was_signatures.yaml)과 코드 기본값이 같다 — 파일이 정본 표의
    사본이다."""
    assert load_policies().thresholds == TH


def test_signal_labels_cover_all_kinds():
    assert set(sig.LABELS) == {
        "was_service_queuing",
        "was_thread_pool_exhaustion",
        "was_db_pool_exhaustion",
        "was_gc_stall",
        "was_heap_pressure",
        "was_slow_sql",
        "was_external_call_delay",
        "was_error_burst",
    }
