"""제니퍼 응답 필드 → 벤더 중립 레코드 변환 · 지표 식별자 매핑 · 이벤트 유형 → WAS 시그니처
(plans/87 §5.2(c)).

벤더 필드명(camelCase)·지표 식별자(snake_case)·이벤트 유형 명칭은 이 모듈에만 둔다.
도메인·애플리케이션 계층은 여기서 만든 중립 키(`heap_used_mb` 등)만 본다. 필드 정의 원천은 Open API
5.6.4 스키마
(`RealtimeInstanceData`·`ActiveServiceData`·`TransactionData`·`EventData`·`ErrorData`·`ApplicationStatus`·
`Instance`·`Domain`·`DBMetrics`)이고, **실데이터 모양은 J0-L-b 녹화 전까지 미검증**이다(§0.10 #19).
"""

from __future__ import annotations

from typing import Any

SOURCE = "jennifer"
SOURCE_LABEL = "JENNIFER"

# 지표 식별자 두 체계(§0.10 #17) — 중립 이름 ↔ realtime camelCase ↔ dbmetrics snake_case.
# dbmetrics 쪽이 None이면 구간 추세로 조회하지 않는다(식별자 미확인 — 예: TPS는 `tps` 식별자가
# 없다).
METRIC_FIELDS: dict[str, tuple[str, str | None]] = {
    "response_time_avg_ms": ("responseTime", "service_time"),
    "tps": ("tps", None),
    "active_services": ("activeService", "active_service"),
    "bad_response_active_services": ("badResponseActiveService", "bad_response_active_service"),
    "reject_rate": ("rejectRate", None),
    "concurrent_users": ("concurrentUser", "concurrent_user"),
    "arrival_rate": ("arrivalRate", None),
    "heap_used_mb": ("heapUsed", "heap_used"),
    "heap_committed_mb": ("heapCommitted", "heap_committed"),
    "non_heap_used_mb": ("nonHeapUsed", "nonheap_used"),
    "gc_time_usage_pct": ("gcTimeUsage", "gc_time_usage"),
    "process_cpu_pct": ("procCPU", "proc_cpu"),
    "process_memory_mb": ("procMemory", "proc_mem"),
    "thread_current": ("threadCurrent", "thread_current"),
    "db_pool_active": ("activeDBConnection", None),
    "db_pool_idle_avg": ("averageDbPoolIdleCount", "average_db_pool_idle_count"),
    "db_pool_configured_avg": ("averageDbPoolConfiguredCount", "average_db_pool_configured_count"),
}

# apm_runtime_health 구간 추세로 부르는 지표(지표 1개/호출 — §0.9 (2) #21).
RUNTIME_TREND_METRICS: tuple[str, ...] = ("heap_used_mb", "heap_committed_mb", "gc_time_usage_pct")

# 이벤트 유형(접두 `ERROR_`·`WARNING_` 제거 후 대문자) → (시그니처 kind, level, category).
# 운영 유형 명칭은 U-1·U-13 미확정 — 두 표기(접두 유무)를 모두 받는다.
EVENT_TYPE_SIGNALS: dict[str, tuple[str, str, str]] = {
    "SERVICE_QUEUING": ("was_service_queuing", "CRITICAL", "strong"),
    "PLC_REJECTED": ("was_service_queuing", "CRITICAL", "strong"),
    "HIGH_RATE_REJECT": ("was_service_queuing", "CRITICAL", "strong"),
    "JDBC_CONNECTION_FAIL": ("was_db_pool_exhaustion", "CRITICAL", "strong"),
    "DB_CONNECTION_FAIL": ("was_db_pool_exhaustion", "CRITICAL", "strong"),
    "DB_CONN_UNCLOSED": ("was_db_pool_exhaustion", "CRITICAL", "strong"),
    "MAYBE_GC_TIME_DELAY": ("was_gc_stall", "WARNING", "medium"),
    "OUTOFMEMORY": ("was_heap_pressure", "CRITICAL", "strong"),
    "JVM_HEAP_MEM_HIGH": ("was_heap_pressure", "WARNING", "medium"),
    "HTTP_IO_EXCEPTION": ("was_external_call_delay", "WARNING", "medium"),
    "HIGH_RATE_FAIL": ("was_error_burst", "CRITICAL", "strong"),
}


def normalize_event_type(raw: Any) -> str:
    """이벤트 유형을 대문자로 바꾸고 `ERROR_`·`WARNING_` 접두를 뗀다."""
    text = str(raw or "").strip().upper()
    for prefix in ("ERROR_", "WARNING_"):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def event_signal(event_type: Any) -> tuple[str, str, str] | None:
    """이벤트 유형에 대응하는 WAS 시그니처(kind, level, category). 없으면 None."""
    return EVENT_TYPE_SIGNALS.get(normalize_event_type(event_type))


# ── 값 변환 헬퍼 ──────────────────────────────────────────────


def to_int(value: Any) -> int | None:
    """숫자·숫자 문자열(epoch ms 문자열 포함)을 int로. 아니면 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    try:
        return int(float(text))
    except ValueError:
        return None


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def result_list(body: Any) -> list[dict[str, Any]]:
    """`{"result": [...]}` 응답에서 dict[str, Any] 항목만 꺼낸다(모양이 다르면 빈 목록)."""
    if isinstance(body, dict):
        result = body.get("result")
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
    return []


# ── 레코드 변환(응답 1건 → 중립 dict) ─────────────────────────


def parse_domain(raw: dict[str, Any]) -> dict[str, Any]:
    return {"domain_id": to_int(raw.get("domainId")), "domain_name": str(raw.get("name") or "")}


def parse_instance(raw: dict[str, Any], domain_id: int | None, domain_name: str) -> dict[str, Any]:
    return {
        "instance_id": to_int(raw.get("instanceId")),
        "instance_name": str(raw.get("name") or ""),
        "domain_id": domain_id,
        "domain_name": domain_name,
        "host_name": str(raw.get("hostName") or ""),
        "ip_address": str(raw.get("ipAddress") or ""),
        "platform": str(raw.get("platform") or ""),
        "status": str(raw.get("status") or ""),
        "agent_version": str(raw.get("version") or ""),
    }


def parse_realtime(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "instance_id": to_int(raw.get("instanceId")),
        "instance_name": str(raw.get("instanceName") or ""),
        "domain_id": to_int(raw.get("domainId")),
    }
    for neutral, (field, _metric) in METRIC_FIELDS.items():
        out[neutral] = to_float(raw.get(field))
    return out


def parse_active_service(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": to_int(raw.get("instanceId")),
        "application": str(raw.get("application") or ""),
        "status": str(raw.get("status") or ""),
        "status_name": str(raw.get("statusName") or ""),
        # elapseTime 단위는 스펙에 없다(U-12) — statusElapseTime(ms 명시)과 같은 ms로 가정한다.
        "elapsed_ms": to_int(raw.get("elapseTime")),
        "status_elapsed_ms": to_int(raw.get("statusElapseTime")),
        "running_mode": str(raw.get("runningMode") or ""),
        "running_text": str(raw.get("runningFullText") or ""),
        "datasource": str(
            raw.get("runningDataSourceName") or raw.get("runningConnectionName") or ""
        ),
        "client_ip": str(raw.get("clientIp") or ""),
        "txid": str(raw.get("txid") or ""),
        "start_time_ms": to_int(raw.get("startTime")),
    }


def parse_transaction(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain_id": to_int(raw.get("domainId")),
        "instance_id": to_int(raw.get("instanceId")),
        "application": str(raw.get("applicationName") or ""),
        "response_time_ms": to_int(raw.get("responseTime")),
        "cpu_ms": to_int(raw.get("cpuTime")),
        "sql_ms": to_int(raw.get("sqlTime")),
        "fetch_ms": to_int(raw.get("fetchTime")),
        "external_ms": to_int(raw.get("externalcallTime")),
        "network_ms": to_int(raw.get("networkTime")),
        "error_type": str(raw.get("errorType") or ""),
        "end_time_ms": to_int(raw.get("endTime")),
        "txid": str(raw.get("txid") or ""),
    }


def parse_event(raw: dict[str, Any]) -> dict[str, Any]:
    error_type = str(raw.get("errorType") or "")
    metrics_name = str(raw.get("metricsName") or "")
    return {
        "time_ms": to_int(raw.get("time")),
        "level": str(raw.get("eventLevel") or "").strip().lower(),
        # ERROR 기반은 errorType, 지표 기반은 metricsName이 찬다(§0.9 판단 ③).
        "event_type": error_type or metrics_name,
        "event_kind": "error" if error_type else ("metric" if metrics_name else ""),
        "value": to_float(raw.get("value")),
        "message": str(raw.get("message") or ""),
        "instance_id": to_int(raw.get("instanceId")),
        "instance_name": str(raw.get("instanceName") or ""),
        "domain_id": to_int(raw.get("domainId")),
        "domain_name": str(raw.get("domainName") or ""),
        "application": str(raw.get("applicationName") or ""),
        "txid": str(raw.get("txid") or ""),
    }


def parse_error(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "time_ms": to_int(raw.get("time")),
        "error_type": str(raw.get("errorType") or ""),
        "message": str(raw.get("message") or ""),
        "instance_id": to_int(raw.get("instanceId")),
        "application": str(raw.get("applicationName") or ""),
        "txid": str(raw.get("txid") or ""),
    }


def parse_metric_point(raw: dict[str, Any]) -> dict[str, Any]:
    return {"time_ms": to_int(raw.get("time")), "value": to_float(raw.get("value"))}


def parse_application_status(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "application": str(raw.get("name") or ""),
        "calls": to_int(raw.get("calls")) or 0,
        "failures": to_int(raw.get("failures")) or 0,
        "bad_responses": to_int(raw.get("badResponses")) or 0,
        "response_time_avg_ms": to_float(raw.get("responseTime")),
        "max_response_time_ms": to_int(raw.get("maxResponseTime")),
    }


def extract_sql_texts(body: Any, limit: int) -> list[str]:
    """`/api/transaction/sql` 응답(스키마 `object` — 모양 미공개)에서 SQL 문자열을 모은다."""
    found: list[str] = []

    def walk(node: Any, key: str = "") -> None:
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and node.strip():
            lowered = key.lower()
            if "sql" in lowered or "query" in lowered or "text" in lowered:
                found.append(node)

    walk(body)
    return found[:limit]
