"""plans/87 J4 — `noise_gate` 제니퍼(APM) 소비측 (spec/SPEC-apm-noise-gate.md).

검증 범위:
    A. 계약 픽스처 — 게이트웨이 `alarm:raw` 레코드(SPEC-apm-gateway §5를 **import 없이 복제**)가
       워커 파싱(`AlarmEvent`)과 조사 계약 필수 필드(serverName·hostname·severity)를 통과한다 ·
       미해소 이벤트는 트리거가 사유를 남기고 생략한다.
    B. R-16 — §2.3 제니퍼 유형 표본(`ERROR_`/`WARNING_` 접두 유무)이 전부 "apm"이고 OS kind가
       아니며 L3 kind 프로파일·"영향 프로세스" 표·동적 baseline이 붙지 않는다. E6 호스트 보강은
       "호스트 참고".
    C. 소스 배지 "제니퍼"(db_id가 아니라 소스 판정) — 역조회가 식별 정보를 채워도 배지는 그대로.
    D. 트리거 힌트 — APM 이벤트만 `meta.hints`.
    E. app_impact — 승격만(DASHBOARD·TICKET → PAGE) · 심각도3·억제·SUPPRESS 불변 · 게이트웨이
       오류 시 판정 불변 + 사유 기록.
    F. 게이트웨이 MCP 클라이언트 — 타임아웃·도구 오류·세션 중단을 통신 실패로 바꾼다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from src.config import AlarmConfig, NoiseGateConfig

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_context_enricher import (
    build_message_enrichment,
    enrich_processes,
)
from noise_gate.application.nodes.alarm_notifier import _enrichment_block_html
from noise_gate.application.nodes.investigation_trigger import investigation_trigger_node
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.application.server_identity import (
    SOURCE_DB,
    SOURCE_EVENT,
    attach_server_identity,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.investigation_payload import build_trigger_payload
from noise_gate.domain.notification_policy import (
    STAGE_MAINTENANCE,
    STAGE_MATRIX,
    STAGE_SEVERITY3,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
    decide_notification,
)
from noise_gate.domain.process_rank import KIND_APM, classify_alarm_kind
from noise_gate.infrastructure.apm_gateway_client import (
    ApmGatewayClient,
    ApmGatewayClientError,
    build_apm_gateway_client,
)
from noise_gate.infrastructure.host_diagnostic_collector import (
    resolve_profile as l3_resolve_profile,
)
from noise_gate.infrastructure.polestar_metric_baseline import DEFAULT_METRIC_SOURCE_BY_KIND
from noise_gate.infrastructure.polestar_process_api import ProcessApiResult

# ─── 계약 픽스처 — SPEC-apm-gateway §5 `alarm:raw` 레코드 복제(apm_gateway import 금지 · R-21) ───


def gateway_record(**over) -> dict:
    """게이트웨이 폴러가 XADD하는 페이로드(§5 표 그대로 · 값은 합성)."""
    record = {
        "dbId": "jennifer",
        "source": "jennifer",
        "serverName": "was-app-01",
        "hostname": "was-app-01",
        "ipAddress": "192.0.2.11",
        "resourceAncestry": "JENNIFER > 운영도메인 > was-app-01_8080",
        "alarmId": "jennifer:3f2a9c1d0b7e6a45",
        "severity": 3,
        "alarmStatus": "",
        "resourceType": "apm.Instance",
        "resourceName": "was-app-01_8080",
        "alarmName": "ERROR_OUTOFMEMORY",
        "alarmTime": "20260929101500",
        "conditions": "제니퍼 EVENT fatal — ERROR_OUTOFMEMORY",
        "conditionLog": "java.lang.OutOfMemoryError: Java heap space (value=1.0)",
        "apm": {
            "source": "jennifer",
            "domain_id": 1000,
            "domain_name": "운영도메인",
            "instance_id": 1001,
            "instance_name": "was-app-01_8080",
            "event_type": "ERROR_OUTOFMEMORY",
            "event_kind": "error",
            "level": "fatal",
            "value": 1.0,
            "txid": "7142093822391",
            "time_ms": 1790644500000,
            "application": "/order/**",
            "match_confidence": "high",
            "match_reason": "hostName",
            "was_signals": [
                {
                    "kind": "was_heap_pressure",
                    "level": "CRITICAL",
                    "category": "strong",
                    "label": "힙 메모리 압박",
                    "evidence": "event OUTOFMEMORY",
                    "instance_id": 1001,
                    "source_tool": "apm_events",
                }
            ],
            "idempotency_key": "3f2a9c1d0b7e6a45" + "0" * 48,
        },
    }
    record.update(over)
    return record


def unresolved_record() -> dict:
    """정합 실패 — hostname 빈 값 · serverName은 instanceName(§5)."""
    return gateway_record(
        serverName="was-app-01_8080",
        hostname="",
        ipAddress="",
        apm={**gateway_record()["apm"], "match_confidence": "none", "match_reason": ""},
    )


def xadd_fields(payload: dict) -> dict:
    """`XADD … {"data": json.dumps(payload, ensure_ascii=False)}` 형식(base_receiver.py:44)."""
    return {b"data": json.dumps(payload, ensure_ascii=False).encode("utf-8")}


# 조사 서비스 계약 — sre_agent `investigation_jobs.REQUIRED_EVENT_FIELDS` 복제(패키지 경계상
# import 금지).
REQUIRED_EVENT_FIELDS = ("serverName", "hostname", "severity")


def missing_required(payload: dict) -> list[str]:
    event = payload.get("event") or {}
    return [f for f in REQUIRED_EVENT_FIELDS if event.get(f) in (None, "")]


# ─── 공용 대역 ────────────────────────────────────────────────────────────────


class _WorkerRedis:
    async def xack(self, *a, **k):
        return 1

    async def xadd(self, *a, **k):
        return b"1-0"

    async def get(self, key):
        return None

    async def set(self, *a, **k):
        return True


class _FakeGraph:
    def __init__(self):
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


def _worker(resolver=None) -> tuple[AlarmWorker, _FakeGraph]:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            repeat_interval_seconds=14400,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    w = AlarmWorker(cfg)
    g = _FakeGraph()
    w._graph = g
    w._identity_resolver = resolver
    return w, g


async def _parse(payload: dict) -> AlarmEvent:
    w, g = _worker()
    await w._process(_WorkerRedis(), "alarm:raw", "g", b"1-1", xadd_fields(payload), {})
    assert g.states, "그래프에 도달하지 못했다(파싱 실패 → dead-letter)"
    return g.states[0]["alarm_event"]


REF = datetime(2026, 9, 29, 10, 15, 0)


def polestar_event(**kw) -> AlarmEvent:
    base = dict(
        db_id="polestar_cm_gp",
        server_name="was-app-01",
        hostname="was-app-01",
        ip_address="192.0.2.11",
        resource_ancestry="/Servers/was-app-01",
        alarm_id="P-1",
        severity=1,
        alarm_status="NOT_ACK",
        resource_type="server.Disks",
        resource_name="/data",
        alarm_name="디스크 사용률 임계 초과",
        alarm_time=REF,
        conditions=">90%",
        condition_log="92%",
    )
    base.update(kw)
    base.setdefault("is_clear", base["severity"] == 0)
    return AlarmEvent(**base)


def apm_event(**kw) -> AlarmEvent:
    rec = gateway_record()
    base = dict(
        db_id="jennifer",
        server_name=rec["serverName"],
        hostname=rec["hostname"],
        ip_address=rec["ipAddress"],
        resource_ancestry=rec["resourceAncestry"],
        alarm_id=rec["alarmId"],
        severity=3,
        alarm_status="",
        resource_type="apm.Instance",
        resource_name=rec["resourceName"],
        alarm_name=rec["alarmName"],
        alarm_time=REF,
        conditions=rec["conditions"],
        condition_log=rec["conditionLog"],
        raw_payload=rec,
    )
    base.update(kw)
    return AlarmEvent(**base)


# ═════════════════════════════════════════════════════════════════════════════
# A. 계약 픽스처 — 워커 파싱 · 조사 필수 필드
# ═════════════════════════════════════════════════════════════════════════════


class TestGatewayRecordContract:
    async def test_worker_parses_gateway_record_into_alarm_event(self):
        ev = await _parse(gateway_record())
        assert ev.db_id == "jennifer" and ev.resource_type == "apm.Instance"
        assert ev.severity == 3 and ev.is_clear is False
        assert ev.alarm_time == datetime(2026, 9, 29, 10, 15, 0)
        assert ev.server_name == "was-app-01" and ev.hostname == "was-app-01"
        assert ev.raw_payload["apm"]["instance_id"] == 1001  # 원문 apm 객체 보존(힌트 원천)
        assert classify_alarm_kind(ev) == KIND_APM

    async def test_resolved_record_passes_required_event_fields(self):
        ev = await _parse(gateway_record())
        decision = NotificationDecision(tier=TIER_PAGE, reason="심각도3", priority=330, signals={})
        payload = build_trigger_payload(ev, decision)
        assert missing_required(payload) == []
        assert payload["event"]["severity"] == 3

    async def test_unresolved_record_is_parsed_but_misses_hostname(self):
        ev = await _parse(unresolved_record())
        assert ev.hostname == "" and ev.server_name == "was-app-01_8080"
        payload = build_trigger_payload(ev, NotificationDecision("page", "r", 1, {}))
        assert missing_required(payload) == ["hostname"]

    @pytest.mark.parametrize("severity", [3, 2, 1, 0])
    async def test_gateway_severity_levels_parse(self, severity):
        # event_levels.yaml: fatal·critical→3 · warning→2 · normal→1 · recovery·clear→0
        ev = await _parse(gateway_record(severity=severity, alarmId=f"jennifer:{severity}"))
        assert ev.severity == severity and ev.is_clear is (severity == 0)


def _trigger_cfg(store=None, client=None) -> dict:
    return {
        "configurable": {
            "app_config": SimpleNamespace(
                noise_gate=SimpleNamespace(
                    investigation_trigger_enabled=True,
                    investigation_trigger_min_tier="PAGE",
                    investigation_poll_interval_seconds=0.0,
                    investigation_total_timeout_seconds=5.0,
                ),
                host_authz=SimpleNamespace(mode="admin_only"),
                composite=SimpleNamespace(prior_targets_enabled=False),
            ),
            "sre_agent_client": client,
            "decision_store": store,
        }
    }


class _SpyStore:
    def __init__(self):
        self.investigations: list[dict] = []
        self.records: list[dict] = []

    def record_investigation(self, **kw):
        self.investigations.append(kw)

    def record(self, decision, **kw):
        self.records.append({"decision": decision, **kw})


class _SpySreClient:
    def __init__(self):
        self.payloads: list[dict] = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def submit(self, payload):
        self.payloads.append(payload)
        return {"investigation_id": "inv-1", "status": "accepted"}

    async def poll(self, inv_id):
        return {"status": "done", "briefing": {"cause": "x"}, "verdict": None}


class TestInvestigationTriggerForApm:
    async def test_unresolved_apm_event_is_skipped_with_reason(self, caplog):
        store, client = _SpyStore(), _SpySreClient()
        ev = await _parse(unresolved_record())
        state = {
            "alarm_event": ev,
            "recurrence": None,
            "correlation_meta": None,
            "notification_decision": NotificationDecision(TIER_PAGE, "심각도3", 330, {}, "fp"),
        }
        with caplog.at_level(logging.WARNING):
            out = await investigation_trigger_node(state, _trigger_cfg(store, client))
        assert out == {} and client.payloads == []  # 조사 서비스 왕복 없음
        assert store.investigations[0]["status"] == "target_unresolved"
        assert "hostname" in store.investigations[0]["verdict"]
        assert any("조사 트리거 생략" in r.getMessage() for r in caplog.records)

    async def test_resolved_apm_event_submits_with_hints(self):
        store, client = _SpyStore(), _SpySreClient()
        ev = await _parse(gateway_record())
        state = {
            "alarm_event": ev,
            "recurrence": None,
            "correlation_meta": None,
            "notification_decision": NotificationDecision(TIER_PAGE, "심각도3", 330, {}, "fp"),
        }
        out = await investigation_trigger_node(state, _trigger_cfg(store, client))
        assert out.get("investigation_briefing") == {"cause": "x"}
        sent = client.payloads[0]
        assert missing_required(sent) == []
        assert sent["meta"]["hints"]["solution"] == "apm"


# ═════════════════════════════════════════════════════════════════════════════
# B. R-16 — 제니퍼 유형 표본 전부 "apm" · OS kind·L3·영향 프로세스·baseline 미적용
# ═════════════════════════════════════════════════════════════════════════════

# plans/87 §2.3 유형 표본(4.5 매뉴얼 11장 — 5.x 운영 명칭 U-1·U-13 미확정이라 접두 유무 둘 다).
JENNIFER_EVENT_TYPES = (
    "SERVICE_QUEUING",
    "PLC_REJECTED",
    "JDBC_CONNECTION_FAIL",
    "DB_CONNECTION_FAIL",
    "OUTOFMEMORY",
    "SYSTEM_DOWN",
    "PROCESS_DOWN",
    "JVM_DOWN",
    "JVM_CPU_HIGH_LONGTIME",
    "HIGH_RATE_REJECT",
    "HIGH_RATE_FAIL",
    "MAYBE_GC_TIME_DELAY",
    "MAYBE_BUSY_PROCESS",
    "UNCAUGHT_EXCEPTION",
    "TX_BAD_RESPONSE",
    "APP_BAD_RESPONSE",
    "DB_BAD_RESPONSE",
    "JDBC_BAD_RESPONSE",
    "TX_CALL_EXCEPTION",
    "JDBC_STMT_EXCEPTION",
    "JVM_CPU_HIGH",
    "JVM_HEAP_MEM_HIGH",
    "RESOURCE_LEAK",
    "DB_TOOMANY_FETCH",
    "DB_CONN_UNCLOSED",
)
JENNIFER_EVENT_NAMES = tuple(
    f"{prefix}{name}" for name in JENNIFER_EVENT_TYPES for prefix in ("", "ERROR_", "WARNING_")
)
OS_KINDS = ("cpu", "memory", "disk", "network", "process", "log")


class TestR16ApmKindPrecedence:
    def test_sample_covers_every_type_with_and_without_prefix(self):
        assert len(JENNIFER_EVENT_TYPES) == 25 and len(JENNIFER_EVENT_NAMES) == 75

    @pytest.mark.parametrize("alarm_name", JENNIFER_EVENT_NAMES)
    def test_every_jennifer_type_is_apm_not_os_kind(self, alarm_name):
        kind = classify_alarm_kind(apm_event(alarm_name=alarm_name))
        assert kind == KIND_APM and kind not in OS_KINDS

    @pytest.mark.parametrize(
        "resource_type", ["apm.Instance", "APM.INSTANCE", "apm.instance", " apm.Instance "]
    )
    def test_precedence_is_case_insensitive(self, resource_type):
        assert (
            classify_alarm_kind(
                apm_event(resource_type=resource_type, alarm_name="WARNING_JVM_HEAP_MEM_HIGH")
            )
            == KIND_APM
        )

    def test_without_apm_resource_type_names_would_hit_os_keywords(self):
        # 선판정이 없으면 생기는 오분류(§0.6 #28 실측 재현) — 이 대조가 선판정의 존재 이유다.
        wrong = {
            name: classify_alarm_kind(polestar_event(resource_type="", alarm_name=name))
            for name in (
                "WARNING_JVM_HEAP_MEM_HIGH",
                "ERROR_OUTOFMEMORY",
                "ERROR_JVM_CPU_HIGH_LONGTIME",
                "ERROR_PROCESS_DOWN",
            )
        }
        assert wrong == {
            "WARNING_JVM_HEAP_MEM_HIGH": "memory",
            "ERROR_OUTOFMEMORY": "memory",
            "ERROR_JVM_CPU_HIGH_LONGTIME": "cpu",
            "ERROR_PROCESS_DOWN": "process",
        }

    @pytest.mark.parametrize(
        "resource_type,alarm_name,expected",
        [
            ("server.Cpus", "CPU 사용률", "cpu"),
            ("server.Memory", "Memory High", "memory"),
            ("server.Disks", "디스크 사용률", "disk"),
            ("network.NMSNode", "트래픽 임계", "network"),
            ("server.Process", "PROCESS_DOWN", "process"),
            ("server.LogMonitor", "에러 로그", "log"),
            ("server.Server", "Host unreachable", None),
        ],
    )
    def test_polestar_classification_unchanged(self, resource_type, alarm_name, expected):
        assert (
            classify_alarm_kind(polestar_event(resource_type=resource_type, alarm_name=alarm_name))
            == expected
        )

    def test_l3_profile_table_has_no_apm_profile(self):
        assert l3_resolve_profile(KIND_APM) == (KIND_APM, ())

    def test_dynamic_baseline_whitelist_excludes_apm(self):
        assert KIND_APM not in DEFAULT_METRIC_SOURCE_BY_KIND

    async def test_no_affected_process_table_for_apm(self):
        client = _FakeProcessClient(
            ProcessApiResult(None, [{"name": "java", "pid": 7, "pmem": 80.0}])
        )
        cfg = AlarmConfig(process_enrich_enabled=True)
        ev = apm_event(alarm_name="WARNING_JVM_HEAP_MEM_HIGH", severity=2)
        assert await enrich_processes(ev, cfg, client) is None
        assert client.calls == []  # "영향 프로세스"(원인 서술) 조회 자체가 없다


class _FakeProcessClient:
    def __init__(self, result, mapped=True):
        self.result = result
        self.mapped = mapped
        self.calls: list[tuple] = []

    def get_base_url(self, db_id):
        return "http://polestar" if self.mapped else None

    async def list_by_hostname(self, db_id, hostname):
        self.calls.append((db_id, hostname))
        return self.result


class TestE6HostReferenceForApm:
    async def test_apm_gets_host_reference_block_not_cause(self):
        client = _FakeProcessClient(
            ProcessApiResult(
                None,
                [
                    {"name": "java", "pid": 7, "p100cpu": 10.0, "pmem": 80.0, "args": ""},
                    {"name": "backup", "pid": 9, "p100cpu": 55.0, "pmem": 5.0, "args": ""},
                ],
            )
        )
        ev = apm_event(alarm_name="WARNING_JVM_HEAP_MEM_HIGH", severity=2)
        block = await build_message_enrichment(ev, AlarmConfig(), NoiseGateConfig(), client)
        assert block is not None and block.kind == KIND_APM
        assert "호스트 참고" in block.title and "원인 판정 아님" in block.signals[0]
        # 정렬 기본값(cpu) — 메모리 알람처럼 pmem 정렬로 WAS 원인을 가리키지 않는다.
        assert [p.name for p in block.snapshot.top] == ["backup", "java"]
        html = _enrichment_block_html(block)
        assert "보강 컨텍스트 — 호스트 참고" in html and "영향 프로세스" not in html

    async def test_unmapped_apm_source_attaches_title_only(self):
        # 이벤트 db_id("jennifer")에 프로세스 API 매핑이 없으면 스냅샷 없이 요지만(graceful).
        client = _FakeProcessClient(ProcessApiResult(None, []), mapped=False)
        block = await build_message_enrichment(
            apm_event(severity=2), AlarmConfig(), NoiseGateConfig(), client
        )
        assert block.kind == KIND_APM and block.snapshot is None and client.calls == []


@pytest.fixture
def _clear_l3_tasks():
    notifier_mod._L3_TASKS.clear()
    yield
    notifier_mod._L3_TASKS.clear()


async def test_l3_enrichment_not_spawned_for_apm(_clear_l3_tasks):
    result = AlarmAnalysisResult(
        alarm_event=apm_event(alarm_name="ERROR_JVM_CPU_HIGH_LONGTIME"),
        severity_label="심각",
        summary="s",
        probable_cause="c",
        recommended_action="a",
        notification_channels=["workb"],
        notifications_sent={"workb": True},
    )
    gate = SimpleNamespace(
        l3_enrichment_enabled=True, l3_max_inflight=4, l3_profile_map_csv="apm=cpu"
    )
    decision = NotificationDecision(TIER_PAGE, "심각도3", 330, {}, "fp")
    notifier_mod._spawn_l3_enrichment(
        decision, result, SimpleNamespace(), gate, {"configurable": {}}
    )
    # 실행 중 루프가 있으니 스폰됐다면 태스크가 남는다 — 프로파일 매핑을 걸어도 OS 명령이
    # 나가지 않는다.
    assert not notifier_mod._L3_TASKS


# ═════════════════════════════════════════════════════════════════════════════
# C. 소스 배지 "제니퍼"
# ═════════════════════════════════════════════════════════════════════════════


class _RowResolver:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple] = []

    async def lookup_identity(self, db_id, hostname):
        self.calls.append((db_id, hostname))
        return self.row


class TestApmSourceBadge:
    async def test_badge_is_jennifer_with_domain_in_tooltip(self):
        ev = apm_event()
        ident = await attach_server_identity(ev, None)
        assert ident.source_label == "제니퍼"
        assert ident.source_detail == "제니퍼 — 운영도메인; jennifer"
        assert ident.source == SOURCE_EVENT and ident.zone_label == "" and ident.site_label == ""

    async def test_badge_stays_jennifer_when_reverse_lookup_finds_polestar_server(self):
        # 역조회(D-188)가 식별 정보를 채워도 배지는 "이벤트를 보낸 소스"다 — 출처는 source
        # 필드가 말한다.
        row = {
            "name": "WAS 운영 01",
            "ip_address": "192.0.2.11",
            "os_type": "Linux",
            "ambiguous": False,
        }
        ev = apm_event()
        ident = await attach_server_identity(ev, _RowResolver(row))
        assert ident.source == SOURCE_DB and ident.name == "WAS 운영 01"
        assert ident.source_label == "제니퍼" and ident.source_detail.startswith("제니퍼")

    async def test_unresolved_hostname_still_gets_badge_without_lookup(self):
        resolver = _RowResolver({"name": "x"})
        ev = apm_event(hostname="", server_name="was-app-01_8080", raw_payload=unresolved_record())
        ident = await attach_server_identity(ev, resolver)
        assert ident is not None and ident.source_label == "제니퍼" and ident.hostname == ""
        assert resolver.calls == [] and ev.server_name == "was-app-01_8080"  # 역조회·승격 없음

    async def test_source_field_alone_marks_apm_source(self):
        ev = apm_event(db_id="apm-gw", raw_payload={"source": "jennifer"})
        ident = await attach_server_identity(ev, None)
        assert ident.source_label == "제니퍼" and ident.source_detail == "제니퍼; apm-gw"

    async def test_polestar_event_without_hostname_still_returns_none(self):
        assert await attach_server_identity(polestar_event(hostname=""), None) is None

    async def test_worker_attaches_badge_before_graph(self):
        ev = await _parse(gateway_record())
        assert ev.server_identity.source_label == "제니퍼"
        ev2 = await _parse(unresolved_record())
        assert ev2.server_identity.source_label == "제니퍼" and ev2.server_identity.hostname == ""


# ═════════════════════════════════════════════════════════════════════════════
# D. 트리거 힌트
# ═════════════════════════════════════════════════════════════════════════════


class TestTriggerHints:
    def test_apm_event_carries_hints_from_raw_apm(self):
        payload = build_trigger_payload(apm_event(), NotificationDecision(TIER_PAGE, "r", 1, {}))
        assert payload["meta"]["hints"] == {
            "solution": "apm",
            "instance_id": 1001,
            "domain_id": 1000,
            "event_type": "ERROR_OUTOFMEMORY",
            "txid": "7142093822391",
        }

    def test_apm_event_without_raw_apm_keeps_fixed_keys(self):
        payload = build_trigger_payload(
            apm_event(raw_payload={}), NotificationDecision(TIER_PAGE, "r", 1, {})
        )
        assert payload["meta"]["hints"] == {
            "solution": "apm",
            "instance_id": None,
            "domain_id": None,
            "event_type": None,
            "txid": None,
        }

    def test_polestar_event_payload_has_no_hints(self):
        payload = build_trigger_payload(
            polestar_event(), NotificationDecision(TIER_PAGE, "r", 1, {})
        )
        assert set(payload["meta"]) == {"recurrence", "cluster", "root_resource", "source"}


# ═════════════════════════════════════════════════════════════════════════════
# E. app_impact — 승격만
# ═════════════════════════════════════════════════════════════════════════════

IMPACT = {
    "source": "jennifer",
    "fatal_events": 2,
    "event_types": ["ERROR_OUTOFMEMORY"],
    "was_signals": ["was_heap_pressure"],
}


def _policy_cfg(**over) -> SimpleNamespace:
    base = dict(
        enable_noise_gate=True,
        suppress_max_severity=2,
        importance_value_map={"1": "낮음", "2": "보통", "3": "높음"},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(importance="2", **extra) -> dict:
    ctx = {
        "importance_id": importance,
        "maintenance": False,
        "noti_policy": None,
        "parent_avail_status": None,
        "source": "polestar_db",
    }
    ctx.update(extra)
    return ctx


def _analysis(**kw) -> SimpleNamespace:
    base = dict(
        ai_message_severity=None,
        pattern_type="",
        is_routine=None,
        llm_actionability=None,
        error=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestPolicyAppImpactPromoteOnly:
    @pytest.mark.parametrize(
        "severity,importance,base_tier",
        [
            (1, "2", TIER_DASHBOARD),  # 1×보통
            (2, "2", TIER_TICKET),  # 2×보통
            (1, "3", TIER_TICKET),  # 1×높음
        ],
    )
    def test_dashboard_and_ticket_become_page(self, severity, importance, base_tier):
        ev = polestar_event(severity=severity)
        base = decide_notification(ev, None, _analysis(), _ctx(importance), _policy_cfg())
        up = decide_notification(
            ev, None, _analysis(), _ctx(importance, app_impact=IMPACT), _policy_cfg()
        )
        assert base.tier == base_tier and up.tier == TIER_PAGE and up.stage == STAGE_MATRIX
        assert "앱 영향 승격: APM fatal 2건" in up.reason and up.reason.endswith("→ 최종 page")
        assert up.evidence["app_impact_fatal_events"] == 2
        assert up.evidence["app_impact_event_types"] == ["ERROR_OUTOFMEMORY"]
        assert up.signals == base.signals  # §8.2 동결 스키마 불변
        assert up.priority > base.priority

    def test_page_stays_page_without_note(self):
        ev = polestar_event(severity=2)
        base = decide_notification(ev, None, _analysis(), _ctx("3"), _policy_cfg())
        up = decide_notification(ev, None, _analysis(), _ctx("3", app_impact=IMPACT), _policy_cfg())
        assert (up.tier, up.reason, up.priority) == (base.tier, base.reason, base.priority)
        assert base.tier == TIER_PAGE and "app_impact_fatal_events" not in up.evidence

    def test_matrix_suppress_from_demote_is_not_revived(self):
        ev = polestar_event(severity=1)
        routine = _analysis(is_routine=True)
        base = decide_notification(ev, None, routine, _ctx("2"), _policy_cfg())
        up = decide_notification(ev, None, routine, _ctx("2", app_impact=IMPACT), _policy_cfg())
        assert base.tier == TIER_SUPPRESS and up.tier == TIER_SUPPRESS
        assert (up.reason, up.priority) == (base.reason, base.priority)

    def test_suppression_stage_is_not_lifted(self):
        ev = polestar_event(severity=2)
        up = decide_notification(
            ev, None, _analysis(), _ctx("2", maintenance=True, app_impact=IMPACT), _policy_cfg()
        )
        assert up.tier == TIER_SUPPRESS and up.stage == STAGE_MAINTENANCE

    def test_severity3_unchanged(self):
        ev = polestar_event(severity=3)
        base = decide_notification(ev, None, _analysis(), _ctx("1"), _policy_cfg())
        up = decide_notification(ev, None, _analysis(), _ctx("1", app_impact=IMPACT), _policy_cfg())
        assert (up.tier, up.reason, up.stage) == (base.tier, base.reason, base.stage)
        assert base.tier == TIER_PAGE and base.stage == STAGE_SEVERITY3

    @pytest.mark.parametrize(
        "value", [None, {}, {"fatal_events": 0}, {"fatal_events": "x"}, "yes", 3]
    )
    def test_absent_or_invalid_app_impact_is_bit_identical(self, value):
        ev = polestar_event(severity=1)
        base = decide_notification(ev, None, _analysis(), _ctx("2"), _policy_cfg())
        got = decide_notification(ev, None, _analysis(), _ctx("2", app_impact=value), _policy_cfg())
        assert (got.tier, got.reason, got.priority, got.evidence) == (
            base.tier,
            base.reason,
            base.priority,
            base.evidence,
        )


class _FakeApmClient:
    def __init__(self, response=None, error=None, unreachable=None):
        self.response = response
        self.error = error
        self.unreachable = unreachable
        self.calls: list[dict] = []

    async def unreachable_reason(self):
        return self.unreachable

    async def apm_events(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response


def _fatal_rows(n=2) -> dict:
    return {
        "rows": [
            {"time_ms": 1, "level": "fatal", "event_type": f"ERROR_T{i}", "instance_id": 1001}
            for i in range(n)
        ],
        "row_count": n,
        "source": "jennifer",
        "source_kind": "apm_api",
        "tool": "apm_events",
        "was_signals": [{"kind": "was_heap_pressure"}, {"kind": "was_error_burst"}],
        "limits": [],
    }


def _gate_state(event, ctx=None, analysis=None) -> dict:
    return {
        "alarm_event": event,
        "analysis_result": analysis or _analysis(),
        "history_stats": None,
        "noise_context": ctx if ctx is not None else _ctx("2"),
    }


def _gate_config(client=None, store=None, **cfg_over) -> dict:
    gate = _policy_cfg(**{"app_impact_enabled": True, "app_impact_window_minutes": 10, **cfg_over})
    return {
        "configurable": {
            "app_config": SimpleNamespace(noise_gate=gate),
            "apm_client": client,
            "decision_store": store,
        }
    }


class TestGateNodeAppImpact:
    async def test_fatal_events_promote_dashboard_to_page(self):
        client, store = _FakeApmClient(_fatal_rows(2)), _SpyStore()
        ev = polestar_event(severity=1)
        out = await notification_gate_node(_gate_state(ev), _gate_config(client, store))
        d = out["notification_decision"]
        assert d.tier == TIER_PAGE and "앱 영향 승격: APM fatal 2건" in d.reason
        assert client.calls == [
            {
                "hostname": "was-app-01",
                "reference_time": "2026-09-29T10:15:00",
                "lookback_minutes": 10,
                "level": "fatal",
                "investigation_id": "P-1",
            }
        ]
        assert out["noise_context"]["app_impact"] == {
            "source": "jennifer",
            "fatal_events": 2,
            "event_types": ["ERROR_T0", "ERROR_T1"],
            "was_signals": ["was_error_burst", "was_heap_pressure"],
        }
        assert out["noise_context"]["importance_id"] == "2"  # 기존 컨텍스트 보존
        assert store.records[0]["stage_evidence"]["app_impact_fatal_events"] == 2

    async def test_window_setting_is_passed_as_lookback(self):
        client = _FakeApmClient(_fatal_rows(1))
        await notification_gate_node(
            _gate_state(polestar_event(severity=2)),
            _gate_config(client, app_impact_window_minutes=25),
        )
        assert client.calls[0]["lookback_minutes"] == 25

    @pytest.mark.parametrize(
        "severity,ctx,analysis,tier",
        [
            (2, _ctx("3"), _analysis(), TIER_PAGE),  # 매트릭스 PAGE
            (3, _ctx("1"), _analysis(), TIER_PAGE),  # 심각도3 단락
            (2, _ctx("2", maintenance=True), _analysis(), TIER_SUPPRESS),  # 유지보수 억제
            (1, _ctx("2"), _analysis(is_routine=True), TIER_SUPPRESS),  # 매트릭스 강등 SUPPRESS
        ],
    )
    async def test_not_asked_when_result_cannot_be_promoted(self, severity, ctx, analysis, tier):
        client = _FakeApmClient(_fatal_rows(3))
        out = await notification_gate_node(
            _gate_state(polestar_event(severity=severity), ctx, analysis), _gate_config(client)
        )
        assert (
            client.calls == []
            and out["notification_decision"].tier == tier
            and "noise_context" not in out
        )

    async def test_apm_event_is_not_asked(self):
        client = _FakeApmClient(_fatal_rows(1))
        ev = apm_event(severity=1, alarm_name="WARNING_TX_BAD_RESPONSE")
        out = await notification_gate_node(_gate_state(ev), _gate_config(client))
        assert client.calls == [] and out["notification_decision"].tier == TIER_DASHBOARD

    async def test_no_fatal_rows_keeps_decision(self):
        warn_only = {
            "rows": [{"level": "warning", "event_type": "WARNING_TX_BAD_RESPONSE"}],
            "source": "jennifer",
        }
        for resp in ({"rows": [], "source": "jennifer"}, warn_only):
            client, store = _FakeApmClient(resp), _SpyStore()
            out = await notification_gate_node(
                _gate_state(polestar_event(severity=1)), _gate_config(client, store)
            )
            d = out["notification_decision"]
            assert (
                d.tier == TIER_DASHBOARD
                and "앱 영향" not in d.reason
                and "noise_context" not in out
            )
            assert "app_impact_error" not in store.records[0]["stage_evidence"]

    @pytest.mark.parametrize(
        "client,reason_part",
        [
            (
                _FakeApmClient(
                    {
                        "error": "source_unavailable",
                        "reason": "Domain is not connected",
                        "source": "jennifer",
                        "tool": "apm_events",
                    }
                ),
                "source_unavailable",
            ),
            (
                _FakeApmClient({"error": "instance_unresolved", "reason": "no match"}),
                "instance_unresolved",
            ),
            (
                _FakeApmClient(error=ApmGatewayClientError("게이트웨이 호출 타임아웃")),
                "gateway_error",
            ),
            (_FakeApmClient(unreachable="127.0.0.1:9096 연결 불가"), "gateway_unreachable"),
            (_FakeApmClient({"source": "jennifer"}), "contract_violation"),
        ],
    )
    async def test_gateway_failure_keeps_decision_and_records_reason(
        self, client, reason_part, caplog
    ):
        ev = polestar_event(severity=1)
        expected = decide_notification(ev, None, _analysis(), _ctx("2"), _policy_cfg())
        store = _SpyStore()
        with caplog.at_level(logging.WARNING):
            out = await notification_gate_node(_gate_state(ev), _gate_config(client, store))
        d = out["notification_decision"]
        assert (d.tier, d.reason, d.priority, d.stage, d.signals) == (
            expected.tier,
            expected.reason,
            expected.priority,
            expected.stage,
            expected.signals,
        )
        assert "noise_context" not in out
        assert reason_part in store.records[0]["stage_evidence"]["app_impact_error"]
        assert any("app_impact" in r.getMessage() for r in caplog.records)  # 침묵 금지

    async def test_empty_hostname_records_reason_without_call(self):
        client, store = _FakeApmClient(_fatal_rows(1)), _SpyStore()
        await notification_gate_node(
            _gate_state(polestar_event(severity=1, hostname="")), _gate_config(client, store)
        )
        assert (
            client.calls == []
            and "hostname" in store.records[0]["stage_evidence"]["app_impact_error"]
        )

    async def test_missing_client_is_noop(self):
        store = _SpyStore()
        out = await notification_gate_node(
            _gate_state(polestar_event(severity=1)), _gate_config(None, store)
        )
        assert out["notification_decision"].tier == TIER_DASHBOARD
        assert "app_impact_error" not in store.records[0]["stage_evidence"]


class TestWorkerBuildsApmClient:
    def _worker(self, **ng) -> AlarmWorker:
        base = dict(
            enable_noise_gate=True,
            app_impact_enabled=True,
            apm_mcp_url="http://127.0.0.1:9096/sse",
            apm_mcp_token=SecretStr("t0k"),
        )
        base.update(ng)
        return AlarmWorker(
            SimpleNamespace(noise_gate=SimpleNamespace(**base), alarm=SimpleNamespace())
        )

    def test_builds_client_when_enabled_with_url(self):
        client = self._worker()._build_apm_gateway_client()
        assert isinstance(client, ApmGatewayClient)
        assert client._auth_headers() == {"Authorization": "Bearer t0k"}

    def test_off_or_gate_off_returns_none(self):
        assert self._worker(app_impact_enabled=False)._build_apm_gateway_client() is None
        assert self._worker(enable_noise_gate=False)._build_apm_gateway_client() is None

    def test_enabled_without_url_warns_and_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert self._worker(apm_mcp_url="")._build_apm_gateway_client() is None
        assert any("NOISE_APP_IMPACT_ENABLED" in r.getMessage() for r in caplog.records)

    async def test_worker_injects_client_into_graph_config(self):
        w, g = _worker()
        sentinel = object()
        w._apm_client = sentinel
        captured = {}

        async def _ainvoke(state, config=None):
            captured.update(config["configurable"])
            return state

        g.ainvoke = _ainvoke
        await w._process(
            _WorkerRedis(),
            "alarm:raw",
            "g",
            b"1-1",
            xadd_fields(
                {
                    "dbId": "polestar_cm_gp",
                    "serverName": "s",
                    "hostname": "h",
                    "alarmId": "P-9",
                    "severity": "2",
                    "alarmName": "x",
                }
            ),
            {},
        )
        assert captured["apm_client"] is sentinel


# ═════════════════════════════════════════════════════════════════════════════
# F. 게이트웨이 MCP 클라이언트
# ═════════════════════════════════════════════════════════════════════════════


class TestApmGatewayClient:
    def test_build_requires_url_and_reads_secret(self):
        assert build_apm_gateway_client(SimpleNamespace(apm_mcp_url="")) is None
        c = build_apm_gateway_client(
            SimpleNamespace(apm_mcp_url="http://127.0.0.1:9096/sse", apm_mcp_token=SecretStr(""))
        )
        assert c is not None and c._auth_headers() is None

    async def test_call_timeout_becomes_client_error(self, monkeypatch):
        client = ApmGatewayClient("http://127.0.0.1:9096/sse", call_timeout=0.05)

        async def _slow(tool, args):
            await asyncio.sleep(1)

        monkeypatch.setattr(client, "_call", _slow)
        with pytest.raises(ApmGatewayClientError, match="타임아웃"):
            await client.apm_events(
                hostname="h",
                reference_time="2026-09-29T10:15:00",
                lookback_minutes=10,
                level="fatal",
                investigation_id="A",
            )

    async def test_session_cancel_without_task_cancel_becomes_client_error(self, monkeypatch):
        client = ApmGatewayClient("http://127.0.0.1:9096/sse")

        async def _cancelled(tool, args):
            raise asyncio.CancelledError()  # anyio 취소 스코프가 올리는 모양(D-213)

        monkeypatch.setattr(client, "_call", _cancelled)
        with pytest.raises(ApmGatewayClientError, match="세션 중단"):
            await client.apm_events(
                hostname="h",
                reference_time="t",
                lookback_minutes=10,
                level="fatal",
                investigation_id="A",
            )

    async def test_tool_error_result_raises(self, monkeypatch):
        import mcp.client.sse

        result = SimpleNamespace(isError=True, content=[SimpleNamespace(text="boom")])

        class _Session:
            def __init__(self, *a):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def initialize(self):
                pass

            async def call_tool(self, name, args):
                return result

        class _Sse:
            def __init__(self, **kw):
                self.kw = kw

            async def __aenter__(self):
                return ("r", "w")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(mcp.client.sse, "sse_client", lambda **kw: _Sse(**kw))
        monkeypatch.setattr("mcp.ClientSession", _Session)
        client = ApmGatewayClient("http://127.0.0.1:9096/sse", bearer_token="t")
        with pytest.raises(ApmGatewayClientError, match="도구 오류"):
            await client.apm_events(
                hostname="h",
                reference_time="t",
                lookback_minutes=10,
                level="fatal",
                investigation_id="A",
            )
        result.isError = False
        result.content = [SimpleNamespace(text=json.dumps({"rows": [], "source": "jennifer"}))]
        assert await client.apm_events(
            hostname="h",
            reference_time="t",
            lookback_minutes=10,
            level="fatal",
            investigation_id="A",
        ) == {"rows": [], "source": "jennifer"}

    async def test_unreachable_probe_caches_reason(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # 닫힌 포트 → 즉시 연결 거부
        client = ApmGatewayClient(f"http://127.0.0.1:{port}/sse")
        first = await client.unreachable_reason()
        assert first and str(port) in first
        assert await client.unreachable_reason() == first  # 쿨다운 동안 재사용
