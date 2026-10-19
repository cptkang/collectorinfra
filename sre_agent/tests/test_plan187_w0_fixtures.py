"""plans/187 W0 — 플레이북 골든 픽스처 모양 고정.

`tests/fixtures/plan187/*.json` 9건은 W1~W6 플레이북 실행기·판독 규칙(R-A~R-H)·브리핑의
입력·기대값이다. 도구 응답 키 집합은 99ec1ea4 실측(apm_gateway 합성 Open API를
MockTransport로 돌린 응답 · mcp_server 폴스타 도구 SQL 별칭)을 아래 상수로 고정한다.
apm_gateway·mcp_server는 import하지 않는다(D-118 · 양방향 import 0) — 리터럴 사본이다.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sre_agent.application.evidence_prefetch import (
    EvidenceScope,
    build_calls,
    prefetch_and_correlate,
)
from sre_agent.application.investigation_guidance import classify_alarm_kind

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plan187"
PLAN153 = Path(__file__).resolve().parent / "fixtures" / "plan153" / "apm_trigger_payloads.json"

SCENARIOS = {
    "normal",
    "latency_sql",
    "latency_external",
    "gc",
    "partial_missing",
    "case1_jdbc_acquire",
    "case2_ldap_external",
    "case3_cpu_loop",
    "case4_oom_periodic",
    "polestar_unresolved",
}
TOP_KEYS = {
    "fixture_version",
    "scenario",
    "title",
    "alarm_time",
    "tool_reference_time",
    "incident_window",
    "baseline_windows",
    "notes",
    "alarm",
    "tool_responses",
    "expected",
}

# ── §4.2 판독 어휘 ─────────────────────────────────────────────────────
READING_ENUMS: dict[str, set[str]] = {
    "scope": {"instance", "host", "shared_dependency"},
    "service_spread": {"single", "many"},
    "load_mode": {"load_increase", "processing_delay", "buckle"},
    "little_check": {"consistent", "active_excess"},
    "dominant": {"sql", "fetch", "external", "cpu", "network", "other"},
    "db_split": {"acquire", "execute", "n/a"},
    "cpu_owner": {"was_load", "was_logic", "other_process", "none"},
    "heap_shape": {"leak_suspect", "periodic", "load_proportional", "normal"},
    "host_saturation": {"cpu", "memory", "fs", "none"},
}
READING_KEYS = set(READING_ENUMS) | {"fs_eta", "change_candidates"}
PLAYBOOK_IDS = {
    "was.latency",
    "was.error",
    "was.active",
    "was.heap",
    "was.pool",
    "host.cpu",
    "host.memory",
    "host.fs",
    "host.disk_io",
    "host.process",
    "host.network",
    "db.*",
    "fallback",
}
STEP_ORDER = {
    "was.latency": ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"],
    "was.heap": ["S0", "S2", "S1", "S7", "S4", "S9"],
}
SECTION_STATUS = {"ok", "missing", "skipped"}

# ── 게이트웨이 was_signals kind 어휘(apm_gateway 판정기 실측) ───────────
WAS_SIGNAL_KINDS = {
    "was_service_queuing",
    "was_db_pool_exhaustion",
    "was_gc_stall",
    "was_heap_pressure",
    "was_external_call_delay",
    "was_error_burst",
    "was_slow_sql",
    "was_thread_pool_exhaustion",
}
WAS_SIGNAL_KEYS = {
    "kind",
    "level",
    "category",
    "label",
    "evidence",
    "instance_id",
    "source_tool",
    "source_id",
}

# ── 실측 응답 모양 — 도구별 (최상위 키, 행 키 변형들) ────────────────────
_ENV = {
    "rows",
    "row_count",
    "queried_at",
    "source_kind",
    "source",
    "tool",
    "limits",
    "sources",
    "total_row_count",
}
_RES = {"instance_resolution"}
_INST_BASE = {
    "agent_version",
    "config_file_path",
    "description",
    "domain_description",
    "domain_id",
    "domain_name",
    "host_name",
    "instance_id",
    "instance_name",
    "ip_address",
    "platform",
    "source_id",
    "status",
    "match_confidence",
    "match_reason",
}
_INST_QUERY = _INST_BASE | {"hostname", "match_kind", "match_tier", "search_confidence"}
_TX = {
    "application",
    "business_ids",
    "business_names",
    "client_id",
    "client_ip",
    "collect_time_ms",
    "cpu_ms",
    "domain_id",
    "domain_name",
    "end_time_ms",
    "error_type",
    "external_call_count",
    "external_ms",
    "fetch_count",
    "fetch_ms",
    "frontend_ms",
    "guid",
    "has_stacktrace",
    "instance_id",
    "instance_name",
    "is_async",
    "link_root",
    "network_ms",
    "response_time_ms",
    "sql_count",
    "sql_ms",
    "start_time_ms",
    "txid",
    "user_id",
}
_CHANGE = {
    "change_detected_at",
    "change_detected_ms",
    "domain_id",
    "hostname",
    "instance_id",
    "instance_name",
    "source_id",
}
_INST_ROW = {"source_id", "instance_id", "instance_name", "domain_id", "hostname"}
APM_SHAPES: dict[str, list[tuple[set[str], set[str]]]] = {
    "apm_instance_map": [
        (_ENV | _RES, _INST_BASE | {"kind", "port"}),
        (_ENV | {"search"}, _INST_QUERY),
        (_ENV | {"business"}, _INST_QUERY | {"business_names", "match_kinds"}),
    ],
    "apm_app_health": [(_ENV | _RES | {"hourly", "was_signals", "window"}, _INST_ROW | {"window"})],
    "apm_period_compare": [
        (_ENV | _RES | {"summary"}, _INST_ROW | {"current", "baseline", "delta"})
    ],
    "apm_runtime_health": [(_ENV | _RES | {"was_signals", "window"}, _INST_ROW | {"trend"})],
    "apm_resource_pool": [
        (
            _ENV | _RES | {"was_signals"},
            _INST_ROW
            | {
                "active_by_datasource",
                "active_by_running_mode",
                "active_services",
                "db_pool_active",
                "db_pool_configured_avg",
                "db_pool_idle_avg",
                "db_pool_usage_ratio",
                "thread_current",
            },
        )
    ],
    "apm_slow_transactions": [
        (
            _ENV | _RES | {"disclosures", "hourly", "summary", "was_signals", "window"},
            _TX | {"hostname", "profile_ref", "source_id"},
        )
    ],
    "apm_transaction_profile": [
        (
            _ENV | _RES | {"disclosures"},
            {
                "source_id",
                "domain_id",
                "txid",
                "time_ms",
                "profile_no",
                "transaction",
                "profile_excerpt",
                "profile_truncated",
                "sqls",
            },
        )
    ],
    "apm_events": [
        (
            _ENV | _RES | {"errors_by_type", "record", "was_signals", "window"},
            {
                "application",
                "domain_id",
                "domain_name",
                "event_kind",
                "event_type",
                "hostname",
                "instance_id",
                "instance_name",
                "level",
                "message",
                "profile_ref",
                "source_id",
                "time_ms",
                "value",
            },
        )
    ],
    "apm_active_services": [
        (
            _ENV | _RES | {"summary", "was_signals"},
            {
                "active_ref",
                "application",
                "application_alias",
                "business_ids",
                "business_names",
                "client_ip",
                "cpu_ms",
                "datasource",
                "domain_id",
                "domain_name",
                "elapsed_ms",
                "fetch_count",
                "hostname",
                "instance_id",
                "instance_name",
                "running_hash",
                "running_mode",
                "running_ms",
                "running_sherpa_oracle_instance",
                "running_sherpa_oracle_seq",
                "running_text",
                "session_id",
                "source_id",
                "sql_count",
                "start_time_ms",
                "status",
                "status_elapsed_ms",
                "status_message",
                "status_name",
                "thread_hash",
                "txid",
            },
        )
    ],
    "apm_source_changes": [(_ENV | _RES | {"window"}, _CHANGE)],
    "apm_change_impact": [
        (
            _ENV | _RES | {"summary", "window"},
            _CHANGE | {"width_minutes", "before", "after", "delta"},
        )
    ],
}
APM_NESTED = {
    ("apm_app_health", "window"): {
        "calls",
        "errors",
        "error_rate",
        "response_time_p50_ms",
        "response_time_p95_ms",
        "response_time_max_ms",
    },
    ("apm_period_compare", "current"): {
        "calls",
        "failures",
        "failure_rate",
        "avg_response_ms",
        "max_response_ms",
        "total_response_ms",
        "application_count",
    },
    ("apm_period_compare", "delta"): {
        "calls",
        "failures",
        "failure_rate",
        "avg_response_ms",
        "max_response_ms",
    },
    ("apm_change_impact", "before"): {
        "start_ms",
        "end_ms",
        "calls",
        "tx_errors",
        "error_rate",
        "avg_response_ms",
        "p95_response_ms",
        "max_response_ms",
        "error_records",
        "errors_by_type",
    },
    ("apm_transaction_profile", "transaction"): _TX,
}
APM_SUMMARY = {
    "apm_slow_transactions": {
        "calls",
        "errors",
        "error_rate",
        "response_time_p50_ms",
        "response_time_p95_ms",
        "response_time_max_ms",
        "sql_fetch_share",
        "external_share",
    },
    "apm_period_compare": {"current", "baseline", "delta", "instances"},
    "apm_change_impact": {"changes", "compared", "window", "width_minutes"},
    "apm_active_services": {"total", "by_running_mode", "by_status"},
}
APM_ERROR_KEYS = {"error", "reason", "source_kind", "source", "tool"}
APM_ERROR_CODES = {
    "not_configured",
    "invalid_argument",
    "instance_unresolved",
    "profile_ref_mismatch",
    "source_unavailable",
    "contract_violation",
    "apm_quota_exceeded",
    "apm_api_error",
    "rate_limited",
}
# 도구 인자(investigation_id·thread_id·owner·wait_seconds·targets 제외 — 픽스처는 쓰지 않는다)
APM_ARGS = {
    "apm_instance_map": {"hostname", "source_ids", "domain_id", "query", "business"},
    "apm_app_health": {
        "hostname",
        "instance_name",
        "instance_id",
        "reference_time",
        "lookback_minutes",
        "source_ids",
    },
    "apm_runtime_health": {
        "hostname",
        "instance_name",
        "instance_id",
        "reference_time",
        "lookback_minutes",
        "source_ids",
        "metrics",
        "interval_minute",
    },
    "apm_resource_pool": {"hostname", "instance_name", "instance_id", "source_ids"},
    "apm_slow_transactions": {
        "hostname",
        "instance_name",
        "reference_time",
        "lookback_minutes",
        "n",
        "source_ids",
        "full",
    },
    "apm_active_services": {"hostname", "instance_name", "n", "source_ids", "full"},
    "apm_events": {
        "hostname",
        "instance_name",
        "reference_time",
        "lookback_minutes",
        "level",
        "source_ids",
        "level_mode",
        "error_type",
        "record",
        "n",
        "full",
    },
    "apm_transaction_profile": {
        "hostname",
        "instance_name",
        "domain_id",
        "txid",
        "time_ms",
        "top_k",
        "source_id",
        "profile_no",
        "include_param_key",
    },
    "apm_source_changes": {
        "hostname",
        "instance_name",
        "reference_time",
        "lookback_minutes",
        "source_ids",
    },
    "apm_change_impact": {
        "hostname",
        "instance_name",
        "reference_time",
        "lookback_minutes",
        "width_minutes",
        "source_ids",
        "n",
        "full",
    },
    "apm_period_compare": {
        "current_start",
        "current_end",
        "baseline_start",
        "baseline_end",
        "hostname",
        "instance_name",
        "source_ids",
        "n",
        "full",
    },
}

_PS_ENV = {"rows", "row_count", "queried_at", "source_kind", "source", "engine"}
_ALARM_COLS = {"alarm_id", "alarm_time", "severity", "alarm_status", "resource_name"}
POLESTAR_SHAPES: dict[str, tuple[set[str], set[str]]] = {
    "polestar_metric_trend": (
        _PS_ENV | {"kind", "granularity", "window"},
        {"stat_date", "avg_val", "min_val", "max_val"},
    ),
    "polestar_incident_alarms": (
        _PS_ENV | {"window"},
        _ALARM_COLS | {"alarm_name", "resource_type"},
    ),
    "polestar_change_history": (
        _PS_ENV | {"window"},
        {"id", "resource_id", "resource_type", "lifecycle_type", "description", "event_time"},
    ),
    "polestar_process_snapshot": (
        {
            "rows",
            "row_count",
            "total_processes",
            "captured_at",
            "queried_at",
            "source_kind",
            "note",
        },
        {"name", "pid", "ppid", "user", "p100cpu", "pcpu", "pmem", "rss", "args"},
    ),
}
POLESTAR_ARGS = {
    "polestar_metric_trend": {
        "source",
        "server_name",
        "kind",
        "granularity",
        "periods",
        "reference_time",
        "lookback_minutes",
        "baseline_periods",
    },
    "polestar_incident_alarms": {
        "source",
        "server_name",
        "reference_time",
        "lookback_minutes",
        "alarm_name",
    },
    "polestar_change_history": {
        "source",
        "server_name",
        "hours",
        "reference_time",
        "lookback_minutes",
    },
    "polestar_process_snapshot": {"hostname", "top_n", "sort"},
}
FAKE_HOSTS = {"wasdemo01", "wasdemo02"}
SME_HEADER = ["ID", "규칙", "입력", "출력 enum", "임계[제안]", "근거", "SME 의견"]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _all() -> list[dict]:
    return [_load(n) for n in sorted(SCENARIOS)]


def _lookup(fixture: dict, tool: str, args: dict) -> dict | None:
    """녹화 조회 규칙 — 녹화 args가 호출 args의 부분집합인 첫 항목. 없으면 None(미녹화)."""
    for entry in fixture["tool_responses"].get(tool, []):
        if all(args.get(k) == v for k, v in entry["args"].items()):
            return entry["response"]
    return None


def test_fixture_files_are_exactly_nine_scenarios():
    assert {p.stem for p in FIXTURES.glob("*.json")} == SCENARIOS
    for fx in _all():
        assert set(fx) == TOP_KEYS
        assert fx["fixture_version"] == 1
        assert (FIXTURES / f"{fx['scenario']}.json").exists()


def test_alarm_follows_plan153_trigger_payload_format():
    sample = next(iter(json.loads(PLAN153.read_text(encoding="utf-8")).values()))
    for fx in _all():
        alarm = fx["alarm"]
        assert set(alarm) == set(sample)
        assert set(alarm["event"]) == set(sample["event"])
        assert set(alarm["meta"]) == set(sample["meta"]) - {"episode_id"}
        assert set(alarm["meta"]["hints"]) == set(sample["meta"]["hints"])
        assert alarm["contract_version"] == "1"
        assert alarm["event"]["resourceType"] == "apm.Instance"
        assert alarm["event"]["resourceName"] == "A1"
        assert alarm["event"]["hostname"] in FAKE_HOSTS
        assert (
            classify_alarm_kind(alarm["event"]["resourceType"], alarm["event"]["alarmName"])
            == "apm"
        )


def _check_apm(tool: str, entry: dict) -> None:
    assert set(entry["args"]) <= APM_ARGS[tool], (tool, entry["args"])
    resp = entry["response"]
    if "error" in resp:
        assert (
            set(resp) == APM_ERROR_KEYS
            and resp["error"] in APM_ERROR_CODES
            and resp["tool"] == tool
        )
        return
    assert resp["source_kind"] == "apm_api" and resp["tool"] == tool
    assert resp["row_count"] == len(resp["rows"])
    variants = APM_SHAPES[tool]
    assert any(set(resp) == top for top, _ in variants), (tool, sorted(resp))
    rows_ok = [cols for top, cols in variants if set(resp) == top]
    for row in resp["rows"]:
        assert set(row) in rows_ok, (tool, sorted(set(row) ^ rows_ok[0]))
        for (t, key), cols in APM_NESTED.items():
            if t == tool:
                assert set(row[key]) == cols, (tool, key)
        if "hostname" in row:
            assert row["hostname"] in FAKE_HOSTS
    if tool in APM_SUMMARY:
        assert set(resp["summary"]) == APM_SUMMARY[tool]
    for sig in resp.get("was_signals") or []:
        assert set(sig) == WAS_SIGNAL_KEYS and sig["kind"] in WAS_SIGNAL_KINDS


def _check_polestar(tool: str, entry: dict) -> None:
    assert set(entry["args"]) <= POLESTAR_ARGS[tool], (tool, entry["args"])
    resp = entry["response"]
    if "error" in resp:
        assert set(resp) == {"error"}
        return
    top, cols = POLESTAR_SHAPES[tool]
    assert set(resp) == top, (tool, sorted(resp))
    assert resp["row_count"] == len(resp["rows"])
    for row in resp["rows"]:
        assert set(row) == cols, tool


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_tool_responses_match_measured_shapes(name):
    fx = _load(name)
    assert fx["tool_responses"]
    for tool, entries in fx["tool_responses"].items():
        assert tool in APM_ARGS or tool in POLESTAR_ARGS, tool
        assert entries
        for entry in entries:
            assert set(entry) == {"args", "response"}
            (_check_apm if tool.startswith("apm_") else _check_polestar)(tool, entry)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_expected_uses_section_4_2_vocabulary(name):
    exp = _load(name)["expected"]
    assert exp["playbook_id"] in PLAYBOOK_IDS
    assert exp["section_order"] == STEP_ORDER[exp["playbook_id"]]
    assert set(exp["section_status"]) == set(exp["section_order"])
    assert set(exp["section_status"].values()) <= SECTION_STATUS
    readings = exp["readings"]
    assert set(readings) == READING_KEYS
    for key, allowed in READING_ENUMS.items():
        assert readings[key] is None or readings[key] in allowed, (key, readings[key])
    # W0 실측: 게이트웨이에 커넥션 획득 구조화 필드가 없어 R-D는 n/a — 사례 결론은 case_conclusion
    assert readings["db_split"] == "n/a"
    for value in exp.get("case_conclusion", {}).values():
        assert value in READING_ENUMS["db_split"]
    for cand in readings["change_candidates"]:
        assert cand["minutes_before_alarm"] <= 1440
    s0 = exp["s0"]
    assert s0["alarm_instance"] == "A1" and s0["host"] in FAKE_HOSTS
    assert s0["targets"][0]["instance"] == "A1"
    assert {t["verdict"] for t in s0["targets"]} <= {"normal", "degraded"}
    assert set(exp["realtime_steps"]) <= set(exp["section_order"])


def test_jennifer_cases_carry_literature_conclusions():
    assert _load("case1_jdbc_acquire")["expected"]["case_conclusion"] == {"db_split": "acquire"}
    assert _load("latency_sql")["expected"]["case_conclusion"] == {"db_split": "execute"}
    assert _load("case2_ldap_external")["expected"]["readings"]["cpu_owner"] == "other_process"
    assert _load("case3_cpu_loop")["expected"]["readings"]["cpu_owner"] == "was_logic"
    assert _load("case4_oom_periodic")["expected"]["readings"]["heap_shape"] == "periodic"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_polestar_recordings_feed_existing_prefetch(name):
    """S4·S5 녹화가 현행 `build_calls` 인자로 찾아지고 `prefetch_and_correlate`가 해석한다."""
    fx = _load(name)
    scope = EvidenceScope(
        source="polestar",
        server_name=fx["expected"]["s0"]["polestar_server"],
        reference_time=fx["tool_reference_time"],
        lookback_minutes=fx["incident_window"]["minutes"],
    )

    has_s5 = (
        "S5" in fx["expected"]["section_order"]
    )  # was.heap은 S5(연관 알람)가 없다 — 알람 축 미녹화

    def call_batch(calls):
        out = []
        for tool, args in calls:
            resp = _lookup(fx, tool, args)
            if resp is None and tool == "polestar_incident_alarms" and not has_s5:
                resp = {"error": "미녹화"}
            assert resp is not None, (tool, args)
            out.append(json.dumps(resp, ensure_ascii=False))
        return out

    result = prefetch_and_correlate(scope, call_batch)
    assert not [n for n in result.notes if "해석 실패" in n]
    missing = fx["expected"].get("missing", {}).get("S4", [])
    assert set(result.metric_findings) == {
        k for k in ("cpu", "memory", "filesystem", "disk_io") if k not in missing
    }
    assert len([n for n in result.notes if "축 결손" in n]) == len(missing) + (0 if has_s5 else 1)
    assert build_calls(scope)  # 폴스타 축 호출이 실제로 생성됐다


def test_sme_review_csvs():
    for name, ids in (
        ("sme_review_catalog.csv", PLAYBOOK_IDS),
        ("sme_review_rules.csv", {"R-A", "R-B", "R-B′", "R-C", "R-D", "R-E", "R-F", "R-G", "R-H"}),
    ):
        with (FIXTURES / name).open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == SME_HEADER
        assert {r[0] for r in rows[1:]} == ids
        assert all(r[-1] == "" for r in rows[1:])
