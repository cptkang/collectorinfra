"""제니퍼 응답 필드 → 벤더 중립 레코드 변환 · 지표 식별자 매핑 · 이벤트 유형 → WAS 시그니처
(plans/87 §5.2(c)).

벤더 필드명(camelCase)·지표 식별자(snake_case)·이벤트 유형 명칭은 이 모듈에만 둔다.
도메인·애플리케이션 계층은 여기서 만든 중립 키(`heap_used_mb` 등)만 본다. plans/134 W1(N-1)로
`spec/CAPABILITY-MAP-134.md` 표 C의 W1 필드와 SPEC §5 W1 행의 필드를 더 읽는다 — 마스킹·가림은
애플리케이션 계층(`masking.py`)이 행을 만들 때 한다(여기서는 원값을 옮길 뿐이다). 필드 정의 원천은
Open API 5.6.4 스키마
(`RealtimeInstanceData`·`ActiveServiceData`·`TransactionData`·`EventData`·`ErrorData`·`ApplicationStatus`·
`Instance`·`Domain`·`DBMetrics`)이고, **실데이터 모양은 J0-L-b 녹화 전까지 미검증**이다(§0.10 #19).
W2(N-5~N-7)로 `SqlAndExternalCallStatus`·`Metrics`(카탈로그 — 로컬 5.7.0.1 녹화 모양)·v2 변경 이력
(맨 배열 — v2 매뉴얼 `deploy.md`)을 더 읽는다.
"""

from __future__ import annotations

import re
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


def error_type_variants(error_type: Any) -> list[str]:
    """오류 유형의 API 표기 후보(물어볼 순서) — 정규화 이름(접두 없음) → `ERROR_` → `WARNING_` 접두.
    운영 명명이 미확정이라(U-13 · W10) 셋을 차례로 묻는다(W1 검증 M-1)."""
    base = normalize_event_type(error_type)
    if not base:
        return []
    return list(dict.fromkeys((base, f"ERROR_{base}", f"WARNING_{base}")))


def same_error_type(actual: Any, wanted: Any) -> bool:
    """오류 유형이 같은가 — 대문자·`ERROR_`·`WARNING_` 접두 차이는 같은 유형으로 본다
    (U-13 미확정)."""
    a, w = normalize_event_type(actual), normalize_event_type(wanted)
    return bool(a) and a == w


# ── 값 변환 헬퍼 ──────────────────────────────────────────────


def to_int(value: Any) -> int | None:
    """숫자·숫자 문자열(epoch ms 문자열 포함)을 int로. 아니면 None — 원천 값 하나(`'²'`·`Infinity`·
    `NaN`)가 도구 전체를 오류로 만들지 않는다(그 칸만 None · plans/134 W7 I-3)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isascii() and text.lstrip("-").isdecimal():
        return int(text)
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return None


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def to_list(value: Any) -> list[Any]:
    """배열 필드(업무 id·이름) — 배열이 아니면 빈 목록(단일 값이면 1원소)."""
    if isinstance(value, list):
        return list(value)
    return [] if value is None or value == "" else [value]


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
    return {
        "domain_id": to_int(raw.get("domainId")),
        "domain_name": str(raw.get("name") or ""),
        "domain_description": str(raw.get("description") or ""),
    }


def parse_business(raw: dict[str, Any]) -> dict[str, Any]:
    """업무 정의(`Business` — plans/130 N-2 B2) 중 업무명 해석에 쓰는 셋. 나머지 칸은 업무 보기
    (plans/134 W4) 몫이다."""
    return {
        "business_id": to_int(raw.get("businessId")),
        "business_name": str(raw.get("name") or ""),
        "business_description": str(raw.get("description") or ""),
    }


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
        "config_file_path": str(raw.get("configFilePath") or ""),
        "description": str(raw.get("description") or ""),
        "instance_oid": to_int(raw.get("instanceOid")),
    }


# 실시간 인스턴스 — 지표 밖 W1 필드(중립 키 ← 벤더 필드). 방문·호출 수의 단위·「하루」 경계는
# 미확인이다(COV E-07 — 도구가 `[한계]`로 알린다).
REALTIME_EXTRA_FIELDS: dict[str, str] = {
    "visit_day": "visitDay",
    "visit_hour": "visitHour",
    "hit_day": "hitDay",
    "hit_hour": "hitHour",
    "active_range_count_0": "activeServiceRangeCount0",
    "active_range_count_1": "activeServiceRangeCount1",
    "active_range_count_2": "activeServiceRangeCount2",
    "active_range_count_3": "activeServiceRangeCount3",
    "collection_count": "collectionCount",
    "file_count": "fileCount",
    "socket_count": "socketCount",
    "thread_daemon": "threadDaemon",
    "thread_started": "threadStarted",
}


def parse_realtime(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "instance_id": to_int(raw.get("instanceId")),
        "instance_name": str(raw.get("instanceName") or ""),
        "domain_id": to_int(raw.get("domainId")),
        "instance_description": str(raw.get("instanceDescription") or ""),
        "instance_oid": to_int(raw.get("instanceOid")),
        # 구간별 서비스 비율(원형 객체 — 키 의미 미공개 · W10)
        "service_rate_by_range": raw.get("serviceRateByRange")
        if isinstance(raw.get("serviceRateByRange"), (dict, list))
        else None,
    }
    for neutral, (field, _metric) in METRIC_FIELDS.items():
        out[neutral] = to_float(raw.get(field))
    for neutral, field in REALTIME_EXTRA_FIELDS.items():
        out[neutral] = to_int(raw.get(field))
    return out


def parse_active_service(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": to_int(raw.get("instanceId")),
        "instance_name": str(raw.get("instanceName") or ""),
        "instance_oid": to_int(raw.get("instanceOid")),
        "domain_id": to_int(raw.get("domainId")),
        "domain_name": str(raw.get("domainName") or ""),
        "application": str(raw.get("application") or ""),
        "application_alias": str(raw.get("alias") or ""),
        "status": str(raw.get("status") or ""),
        "status_name": str(raw.get("statusName") or ""),
        "status_message": str(raw.get("statusMessage") or ""),
        # elapseTime 단위는 스펙에 없다(U-12) — statusElapseTime(ms 명시)과 같은 ms로 가정한다.
        "elapsed_ms": to_int(raw.get("elapseTime")),
        "status_elapsed_ms": to_int(raw.get("statusElapseTime")),
        "running_ms": to_int(raw.get("runningTime")),
        "cpu_ms": to_int(raw.get("cpuTime")),
        "sql_count": to_int(raw.get("sqls")),
        "fetch_count": to_int(raw.get("fetches")),
        "running_mode": str(raw.get("runningMode") or ""),
        "running_text": str(raw.get("runningFullText") or ""),
        "running_hash": to_int(raw.get("runningHash")),
        "running_sherpa_oracle_instance": str(raw.get("runningSherpaOracleInstanceName") or ""),
        "running_sherpa_oracle_seq": to_int(raw.get("runningSherpaOracleSequence")),
        "datasource": str(
            raw.get("runningDataSourceName") or raw.get("runningConnectionName") or ""
        ),
        "client_ip": str(raw.get("clientIp") or ""),
        "txid": str(raw.get("txid") or ""),
        "session_id": to_int(raw.get("sessionId")),
        "thread_hash": to_int(raw.get("threadHash")),
        "start_time_ms": to_int(raw.get("startTime")),
        "business_ids": to_list(raw.get("businessId")),
        "business_names": [str(x) for x in to_list(raw.get("businessName"))],
    }


def parse_transaction(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain_id": to_int(raw.get("domainId")),
        "domain_name": str(raw.get("domainName") or ""),
        "instance_id": to_int(raw.get("instanceId")),
        "instance_name": str(raw.get("instanceName") or ""),
        "instance_oid": to_int(raw.get("instanceOid")),
        "application": str(raw.get("applicationName") or ""),
        "response_time_ms": to_int(raw.get("responseTime")),
        "cpu_ms": to_int(raw.get("cpuTime")),
        "sql_ms": to_int(raw.get("sqlTime")),
        "fetch_ms": to_int(raw.get("fetchTime")),
        "external_ms": to_int(raw.get("externalcallTime")),
        "network_ms": to_int(raw.get("networkTime")),
        "frontend_ms": to_int(raw.get("frontendTime")),
        "sql_count": to_int(raw.get("sqlCount")),
        "fetch_count": to_int(raw.get("fetchCount")),
        "external_call_count": to_int(raw.get("externalcallCount")),
        "error_type": str(raw.get("errorType") or ""),
        "start_time_ms": to_int(raw.get("startTime")),
        "end_time_ms": to_int(raw.get("endTime")),
        "collect_time_ms": to_int(raw.get("collectTime")),
        "txid": str(raw.get("txid") or ""),
        "guid": str(raw.get("guid") or ""),
        "client_ip": str(raw.get("clientIp") or ""),
        "client_id": str(raw.get("clientId") or ""),
        "user_id": str(raw.get("userId") or ""),
        "is_async": to_bool(raw.get("async")),
        "link_root": to_bool(raw.get("linkRoot")),
        "has_stacktrace": to_bool(raw.get("hasStacktrace")),
        "business_ids": to_list(raw.get("businessId")),
        "business_names": [str(x) for x in to_list(raw.get("businessName"))],
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
        "instance_oid": to_int(raw.get("instanceOid")),
    }


def parse_error(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "time_ms": to_int(raw.get("time")),
        "error_type": str(raw.get("errorType") or ""),
        "message": str(raw.get("message") or ""),
        "value": to_float(raw.get("value")),
        "domain_id": to_int(raw.get("domainId")),
        "domain_name": str(raw.get("domainName") or ""),
        "instance_id": to_int(raw.get("instanceId")),
        "instance_name": str(raw.get("instanceName") or ""),
        "instance_oid": to_int(raw.get("instanceOid")),
        "application": str(raw.get("applicationName") or ""),
        "txid": str(raw.get("txid") or ""),
        "profile_index": to_int(raw.get("profileIndex")),
    }


def parse_metric_point(raw: dict[str, Any]) -> dict[str, Any]:
    return {"time_ms": to_int(raw.get("time")), "value": to_float(raw.get("value"))}


# ApplicationStatus 25필드 중 아래 표 밖 6개(`name`·`calls`·`failures`·`badResponses`·
# `responseTime`·`maxResponseTime`)는 종전 키 그대로다.
APPLICATION_STATUS_EXTRA: dict[str, tuple[str, str]] = {
    "response_time_stddev_ms": ("responseTimeStandardDeviation", "float"),
    "cpu_ms_per_tx": ("cpuTimePerTransaction", "float"),
    "sql_ms_per_tx": ("sqlTimePerTransaction", "float"),
    "fetch_ms_per_tx": ("fetchTimePerTransaction", "float"),
    "external_ms_per_tx": ("externalCallTimePerTransaction", "float"),
    "sqls": ("sqls", "int"),
    "sqls_per_tx": ("sqlsPerTransaction", "float"),
    "fetches": ("fetches", "int"),
    "fetches_per_tx": ("fetchesPerTransaction", "float"),
    "external_calls": ("externalCalls", "int"),
    "external_calls_per_tx": ("externalCallsPerTransaction", "float"),
    "frontend_measurements": ("frontendMeasurements", "int"),
    "frontend_ms": ("frontendTime", "int"),
    "network_ms": ("networkTime", "int"),
    "total_response_ms": ("totalResponseTime", "int"),
    "total_cpu_ms": ("totalCpuTime", "int"),
    "total_sql_ms": ("totalSqlTime", "int"),
    "total_fetch_ms": ("totalFetchTime", "int"),
    "total_external_ms": ("totalExternalCallTime", "int"),
}


def parse_application_status(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "application": str(raw.get("name") or ""),
        "calls": to_int(raw.get("calls")) or 0,
        "failures": to_int(raw.get("failures")) or 0,
        "bad_responses": to_int(raw.get("badResponses")) or 0,
        "response_time_avg_ms": to_float(raw.get("responseTime")),
        "max_response_time_ms": to_int(raw.get("maxResponseTime")),
    }
    for neutral, (field, kind) in APPLICATION_STATUS_EXTRA.items():
        out[neutral] = to_float(raw.get(field)) if kind == "float" else to_int(raw.get(field))
    return out


# SqlAndExternalCallStatus 7필드(`/api/status/sql`·`/api/status/external_call` — plans/134 W2 N-5).
# `name`은 SQL 문(sql) 또는 호출 대상(external_call) — 마스킹은 애플리케이션 계층.
def parse_call_status(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(raw.get("name") or ""),
        "calls": to_int(raw.get("calls")) or 0,
        "failures": to_int(raw.get("failures")) or 0,
        "bad_responses": to_int(raw.get("badResponses")) or 0,
        "response_time_avg_ms": to_float(raw.get("responseTime")),
        "max_response_time_ms": to_int(raw.get("maxResponseTime")),
        "total_response_ms": to_int(raw.get("totalResponseTime")),
    }


# `/api/status/*` 정렬 기준 이름 → 행 칸(여러 (소스, 도메인) 결과를 합쳐 다시 정렬할 때만 쓴다 —
# 서버에는 받은 이름 그대로 넘긴다). 응답 필드 이름과, 의미가 같은 카탈로그 이름(`count`·
# `averageResponseTime`)을 받는다. 서버가 어느 이름 체계를 받는지는 미확인이다(COV E-05 · W10).
_STATUS_SORT_COMMON: dict[str, str] = {
    "calls": "calls",
    "count": "calls",
    "failures": "failures",
    "badResponses": "bad_responses",
    "responseTime": "response_time_avg_ms",
    "averageResponseTime": "response_time_avg_ms",
    "maxResponseTime": "max_response_time_ms",
    "totalResponseTime": "total_response_ms",
}
STATUS_SORT_DEFAULT = "calls"  # 스펙 설명의 기본 정렬 기준


def _camel(name: str) -> str:
    """snake_case → camelCase(`response_time` → `responseTime`) — 표기 변환만(뜻은 추측하지
    않는다)."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def status_sort_field(kind: str, sort_by: str | None) -> str | None:
    """정렬 기준 이름에 대응하는 행 칸(모르면 None). 받은 이름 그대로 → snake_case를 camelCase로
    바꾼 이름 순으로 찾는다(W2V-B2 — 표기 정규화만 · 뜻 추측 금지)."""
    name = sort_by or STATUS_SORT_DEFAULT
    for candidate in dict.fromkeys((name, _camel(name))):
        if candidate in _STATUS_SORT_COMMON:
            return _STATUS_SORT_COMMON[candidate]
        if kind == "application":
            for neutral, (field, _kind) in APPLICATION_STATUS_EXTRA.items():
                if field == candidate:
                    return neutral
    return None


# 지표 카탈로그(`/api/metrics` — `result`가 배열이 아니라 **객체**다 · COV E-18) 군 → 중립 scope.
METRIC_CATALOG_SCOPES: dict[str, str] = {
    "domain": "domain",
    "instance": "instance",
    "business": "business",
    "application": "application",
    "sql": "sql",
    "externalCall": "external_call",
}


def parse_metric_catalog(body: Any) -> tuple[dict[str, list[str]], list[str]] | None:
    """`{"result": {군: [지표 식별자…]}}` → (`{scope: [지표 식별자…]}`, 모양 위반 scope 목록).

    군 값이 목록이 아니거나 비어 있지 않은 문자열이 아닌 항목이 있으면 그 군은 **모양 위반**이다
    (빈 군으로 받지 않는다 — 호출자가 「검증 불가」로 알린다 · W2V-G4). 결과가 객체가 아니거나 정상
    군이 하나도 없으면 None(호출자가 오류로 올린다 — 빈 카탈로그로 강등하지 않는다 · E-18). 모르는
    군은 snake_case 이름으로 싣는다."""
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return None
    out: dict[str, list[str]] = {}
    invalid: list[str] = []
    for group, names in result.items():
        scope = METRIC_CATALOG_SCOPES.get(str(group)) or re.sub(
            r"(?<!^)(?=[A-Z])", "_", str(group)
        ).lower()
        if isinstance(names, list) and all(isinstance(n, str) and n.strip() for n in names):
            out[scope] = list(names)
        else:
            invalid.append(scope)
    return (out, invalid) if out else None


def bare_list(body: Any) -> list[dict[str, Any]] | None:
    """v2 응답(`{result: …}` 봉투 없는 맨 배열 — COV E-18). 배열이 아니거나 객체가 아닌 항목이
    있으면 None(호출자가 오류로 올린다 — 조용히 0행으로 강등하지 않는다 · W2V-G2)."""
    if isinstance(body, list) and all(isinstance(r, dict) for r in body):
        return body
    return None


def parse_source_change(raw: dict[str, Any]) -> dict[str, Any]:
    """소스코드(리소스) 변경 이력 1건 — `collectTime`은 데이터 서버가 변경을 **인지한** 시각이다
    (배포 시각으로 확정하지 않는다 · plans/134 N-7)."""
    return {
        "instance_id": to_int(raw.get("instanceId")),
        "change_detected_ms": to_int(raw.get("collectTime")),
    }


# `/api/transaction/sql` 응답에서 SQL **문**을 담는 칸 이름(소문자 · `_`·`-`·`.` 제거 뒤 정확 일치).
# 응답 모양이 미공개라(COV E-11 · W10) 키에 sql·query·text가 든 문자열을 모으되, 문 칸은 관례상
# 문을 담는 이름만 고른다 — 제니퍼의 다른 응답이 SQL 문을 `sql`로 싣고(실행 중 요청 상세), 그 밖은
# 흔한 표기(`sqlText`·`statement`·`query`)다. `sqlParams`·`paramText`·`bindText` 같은 칸은 바인드
# 값일 수 있어 문으로 보지 않는다(plans/134 W7 AUDIT-8 · VG-1 — SQL 키워드로 가르지 않는다).
SQL_STATEMENT_KEYS = frozenset(
    {
        "sql",
        "sqls",
        "sqltext",
        "sqlstring",
        "sqlstatement",
        "statement",
        "statements",
        "query",
        "querytext",
    }
)


def is_sql_statement_key(key: str) -> bool:
    """SQL 문을 담는 칸 이름인가(`SQL_STATEMENT_KEYS`)."""
    return re.sub(r"[_.\-]", "", str(key)).lower() in SQL_STATEMENT_KEYS


def extract_sql_texts(body: Any, limit: int | None = None) -> list[tuple[str, str]]:
    """`/api/transaction/sql` 응답(스키마 `object` — 모양 미공개)에서 SQL 칸 문자열을 (가장 가까운
    칸 이름, 값)으로 모은다(`limit`이 없으면 전부). 문인지는 칸 이름으로 가른다
    (`is_sql_statement_key`)."""
    found: list[tuple[str, str]] = []

    def walk(node: Any, key: str = "") -> None:
        if limit is not None and len(found) >= limit:
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
                found.append((key, node))

    walk(body)
    return found if limit is None else found[:limit]
