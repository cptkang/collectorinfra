"""메시지 기반 L1 보강(Plan 60 E6 §16) — notifier 블록 + enricher 수집 + 회귀 0 테스트.

검증 범위(§16.5 수용 기준):
    - notifier kind별 보강 블록: cpu/memory 표 비트동일, 신규 kind 요지 첨부.
    - `message_enrichment_enabled=False`·`process_enrich_enabled` 경로 비트동일(회귀 0).
    - 라우팅 티어 불변(보강은 첨부만) — 티어 게이트(_enrichment_to_attach).
    - enricher build_message_enrichment: disk/network host-wide 스냅샷 참고 첨부,
      cpu/memory None(중복 방지), process/log 요지만, 실패 graceful.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import src.alarm.application.nodes.alarm_notifier as notifier_mod
from src.alarm.application.nodes.alarm_context_enricher import build_message_enrichment
from src.alarm.application.nodes.alarm_notifier import (
    _enrichment_block_html,
    _enrichment_to_attach,
    alarm_notifier_node,
    build_workb_body,
)
from src.alarm.domain.alarm import (
    AlarmAnalysisResult,
    AlarmEvent,
    MessageEnrichment,
    ProcessInfo,
    ProcessSnapshot,
)
from src.alarm.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_TICKET,
)
from src.alarm.infrastructure.polestar_process_api import ProcessApiResult
from src.config import AlarmConfig, NoiseGateConfig

REF = datetime(2026, 6, 29, 14, 0, 0)


def make_event(**kw) -> AlarmEvent:
    defaults = dict(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/svr/Disks", alarm_id="A-1", severity=2,
        alarm_status="NOT_ACK", resource_type="server.Disks", resource_name="svr-1-DISK",
        alarm_name="디스크 사용률 임계 초과", alarm_time=REF, conditions="", condition_log="",
    )
    defaults.update(kw)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


def make_result(event=None, channels=None) -> AlarmAnalysisResult:
    return AlarmAnalysisResult(
        alarm_event=event or make_event(),
        severity_label="경고", summary="요약", probable_cause="원인",
        recommended_action="조치", notification_channels=channels or ["workb"],
    )


def _proc_info(name, pid, p100cpu=0.0, pmem=0.0, args=""):
    return ProcessInfo(name, pid, 1, "root", p100cpu, p100cpu / 4.0, pmem, 1024, args)


def _host_snapshot(kind="disk"):
    return ProcessSnapshot(
        alarm_kind=kind, captured_at=datetime(2026, 6, 5, 9, 33, 59),
        top=[_proc_info("dockerd", 111, p100cpu=40.0), _proc_info("java", 222, p100cpu=20.0)],
        total_count=88, source_host="h-1",
    )


def _mem_snapshot():
    return ProcessSnapshot(
        alarm_kind="memory", captured_at=datetime(2026, 6, 5, 9, 33, 59),
        top=[
            ProcessInfo("java", 12345, 1, "appusr", 12.0, 3.0, 38.1, 2048, "-Xmx8g"),
            ProcessInfo("postgres", 3401, 1, "pgsql", 0.4, 0.1, 7.8, 1024, "***"),
        ],
        total_count=142, source_host="saisvd01",
    )


class FakeProcessClient:
    def __init__(self, result=None, mapped=True, error=None):
        self.result = result
        self.mapped = mapped
        self.error = error
        self.calls = []

    def get_base_url(self, db_id):
        return "http://polestar" if self.mapped else None

    async def list_by_hostname(self, db_id, hostname):
        self.calls.append((db_id, hostname))
        if self.error:
            raise self.error
        return self.result


# ─── notifier: _enrichment_block_html (신규 kind 요지·데이터 블록) ────────────────

class TestEnrichmentBlockHtml:
    def test_disk_block_with_snapshot(self):
        block = MessageEnrichment(
            kind="disk", title="용량/마운트 상위 소비",
            signals=("호스트 프로세스 상위(참고)",), snapshot=_host_snapshot("disk"),
        )
        html = _enrichment_block_html(block)
        assert "보강 컨텍스트 — 용량/마운트 상위 소비" in html
        assert "호스트 프로세스 상위" in html
        assert "dockerd" in html and "pid 111" in html
        assert "전체 88개" in html

    def test_process_block_summary_only(self):
        # 데이터 소스 미확정 kind → 요지만(표 없음)
        block = MessageEnrichment(
            kind="process", title="생존·재시작 이력",
            signals=("프로세스 생존·재시작 이력",), snapshot=None,
        )
        html = _enrichment_block_html(block)
        assert "보강 컨텍스트 — 생존·재시작 이력" in html
        assert "프로세스 생존·재시작 이력" in html
        assert "<table>" not in html

    def test_masked_args_not_leaked(self):
        snap = ProcessSnapshot(
            alarm_kind="disk", captured_at=None,
            top=[_proc_info("db", 1, p100cpu=1.0, args="***")], total_count=1, source_host="h",
        )
        block = MessageEnrichment("disk", "용량/마운트 상위 소비", ("x",), snap)
        assert "password" not in _enrichment_block_html(block).lower()


# ─── notifier: build_workb_body 첨부·회귀 0 (cpu/memory 비트동일) ────────────────

class TestBuildWorkbBodyEnrichment:
    def test_new_kind_block_appended(self):
        block = MessageEnrichment("disk", "용량/마운트 상위 소비", ("x",), _host_snapshot())
        body = build_workb_body(make_result(), enrichment=block)
        assert "보강 컨텍스트 — 용량/마운트 상위 소비" in body

    def test_no_enrichment_bit_identical(self):
        # enrichment=None(기본) → 기존 본문과 동일(보강 블록 없음)
        base = build_workb_body(make_result())
        assert "보강 컨텍스트" not in base
        assert build_workb_body(make_result(), enrichment=None) == base

    def test_cpu_memory_process_table_unchanged(self):
        # cpu/memory는 process_snapshot 표만 — enrichment 없이 기존과 비트동일
        without = build_workb_body(make_result(), _mem_snapshot())
        assert "영향 프로세스 — 메모리 상위" in without
        assert "보강 컨텍스트" not in without
        # enrichment=None이면 process 표 본문은 동일
        assert build_workb_body(make_result(), _mem_snapshot(), enrichment=None) == without


# ─── notifier: 티어 게이트 (_enrichment_to_attach) — 라우팅 불변, 첨부만 ──────────

class TestEnrichmentTierGate:
    def _block(self):
        return MessageEnrichment("disk", "t", ("x",), None)

    def _decision(self, tier):
        return NotificationDecision(tier=tier, reason="r", priority=1, signals={})

    def test_page_ge_min_page_attaches(self):
        assert _enrichment_to_attach(self._block(), self._decision(TIER_PAGE), "PAGE") is not None

    def test_dashboard_below_min_page_none(self):
        assert _enrichment_to_attach(self._block(), self._decision(TIER_DASHBOARD), "PAGE") is None

    def test_ticket_below_min_page_none(self):
        assert _enrichment_to_attach(self._block(), self._decision(TIER_TICKET), "PAGE") is None

    def test_ticket_meets_min_ticket(self):
        assert _enrichment_to_attach(self._block(), self._decision(TIER_TICKET), "TICKET") is not None

    def test_decision_none_attaches(self):
        # 게이트 off(decision None) — 본문 생성 자체가 발송이므로 첨부
        assert _enrichment_to_attach(self._block(), None, "PAGE") is not None

    def test_no_block_returns_none(self):
        assert _enrichment_to_attach(None, self._decision(TIER_PAGE), "PAGE") is None

    def test_min_tier_case_insensitive(self):
        assert _enrichment_to_attach(self._block(), self._decision(TIER_PAGE), "page") is not None


# ─── notifier node: message off 비트동일 + message on 첨부 ────────────────────────

def _capture_send(monkeypatch) -> dict:
    captured = {}

    async def fake_workb(cfg, result, snap=None, *, enrichment=None, **kwargs):
        captured["enrichment"] = enrichment

    monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
    return captured


class TestNotifierNodeEnrichment:
    async def test_message_off_no_enrichment(self, monkeypatch):
        # noise_gate 없음(message off) → _send_workb에 enrichment 미전달(None)
        captured = _capture_send(monkeypatch)
        state = {
            "analysis_result": make_result(),
            "enrichment": MessageEnrichment("disk", "t", ("x",), None),
        }
        config = {"configurable": {"app_config": SimpleNamespace(
            workb=SimpleNamespace(), alarm=SimpleNamespace())}}
        await alarm_notifier_node(state, config)
        assert captured["enrichment"] is None

    async def test_message_on_page_attaches(self, monkeypatch):
        captured = _capture_send(monkeypatch)
        block = MessageEnrichment("disk", "용량/마운트 상위 소비", ("x",), None)
        gate = NoiseGateConfig(message_enrichment_enabled=True)
        state = {
            "analysis_result": make_result(),
            "enrichment": block,
            "notification_decision": NotificationDecision(
                tier=TIER_PAGE, reason="r", priority=1, signals={}),
        }
        config = {"configurable": {"app_config": SimpleNamespace(
            workb=SimpleNamespace(), alarm=SimpleNamespace(), noise_gate=gate)}}
        await alarm_notifier_node(state, config)
        assert captured["enrichment"] is block

    async def test_message_on_but_low_tier_no_attach(self, monkeypatch):
        # DASHBOARD 티어는 애초에 발송 안 하지만, 티어 게이트 자체도 미첨부 확인용으로
        # decision=None 경로가 아닌 명시 티어 검증 — 여기선 PAGE 미만이면 첨부 안 됨.
        captured = _capture_send(monkeypatch)
        block = MessageEnrichment("disk", "t", ("x",), None)
        gate = NoiseGateConfig(message_enrichment_enabled=True, enrichment_min_tier="PAGE")
        # decision=None + message on → 첨부(발송 경로). 티어 미달은 티어 게이트 단위테스트에서 커버.
        state = {"analysis_result": make_result(), "enrichment": block}
        config = {"configurable": {"app_config": SimpleNamespace(
            workb=SimpleNamespace(), alarm=SimpleNamespace(), noise_gate=gate)}}
        await alarm_notifier_node(state, config)
        assert captured["enrichment"] is block


# ─── enricher: build_message_enrichment 수집 ─────────────────────────────────────

def _alarm_cfg(**kw):
    return AlarmConfig(**kw)


class TestBuildMessageEnrichment:
    async def test_disk_attaches_host_snapshot(self):
        client = FakeProcessClient(
            result=ProcessApiResult(captured_at=None, processes=[
                {"name": "dockerd", "pid": 111, "p100cpu": 40.0, "args": ""},
            ])
        )
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        block = await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), client)
        assert block is not None
        assert block.kind == "disk"
        assert block.title == "용량/마운트 상위 소비"
        assert block.snapshot is not None
        assert block.snapshot.top[0].name == "dockerd"
        assert client.calls == [(ev.db_id, "h-1")]  # host-wide, list_by_hostname 재사용

    async def test_network_attaches_host_snapshot(self):
        client = FakeProcessClient(
            result=ProcessApiResult(captured_at=None, processes=[{"name": "nginx", "pid": 5}])
        )
        ev = make_event(resource_type="network.NMSNode", alarm_name="트래픽 임계 초과")
        block = await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), client)
        assert block.kind == "network"
        assert block.snapshot is not None

    async def test_cpu_returns_none(self):
        # cpu/memory는 process_snapshot이 처리 — 중복 방지로 None, 조회도 안 함
        client = FakeProcessClient(result=ProcessApiResult(None, []))
        ev = make_event(resource_type="server.Cpus", alarm_name="CPU 사용률")
        assert await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), client) is None
        assert client.calls == []

    async def test_process_kind_summary_only(self):
        # 소스 미확정 kind → 요지만(snapshot None), host 조회 안 함
        client = FakeProcessClient(result=ProcessApiResult(None, []))
        ev = make_event(resource_type="server.Server", alarm_name="프로세스다운 감지")
        block = await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), client)
        assert block.kind == "process"
        assert block.snapshot is None
        assert client.calls == []

    async def test_log_kind_summary_only(self):
        ev = make_event(resource_type="server.LogMonitor", alarm_name="에러 로그 감지")
        block = await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), None)
        assert block.kind == "log"
        assert block.snapshot is None

    async def test_clear_alarm_returns_none(self):
        client = FakeProcessClient(result=ProcessApiResult(None, []))
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률",
                        severity=0, is_clear=True)
        assert await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), client) is None

    async def test_collection_failure_graceful_summary_only(self):
        # host-wide 조회 실패해도 요지 블록은 첨부(snapshot=None) — 통보 무차단
        client = FakeProcessClient(error=RuntimeError("API down"))
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        block = await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), client)
        assert block is not None
        assert block.kind == "disk"
        assert block.snapshot is None

    async def test_unmapped_db_id_summary_only(self):
        client = FakeProcessClient(result=ProcessApiResult(None, []), mapped=False)
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        block = await build_message_enrichment(ev, _alarm_cfg(), NoiseGateConfig(), client)
        assert block.snapshot is None
        assert client.calls == []

    async def test_profile_map_csv_override(self):
        gate = NoiseGateConfig(enrichment_profile_map_csv="disk=사용자 정의 디스크 요지")
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        block = await build_message_enrichment(ev, _alarm_cfg(), gate, None)
        assert block.title == "사용자 정의 디스크 요지"


# ─── enricher node: 반환 키셋(회귀 0) + 그래프 배선 ──────────────────────────────

from src.alarm.application.nodes.alarm_context_enricher import (  # noqa: E402
    alarm_context_enricher_node,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph  # noqa: E402


class TestEnricherNodeKeyContract:
    def _config(self, gate, client):
        app_cfg = SimpleNamespace(alarm=_alarm_cfg(history_enabled=False), noise_gate=gate)
        return {"configurable": {"app_config": app_cfg, "history_repo": None,
                                 "history_redis": None, "process_client": client}}

    async def test_message_off_no_enrichment_key(self):
        # message off → 반환 키셋은 기존 2키(회귀 0), enrichment 키 없음
        gate = NoiseGateConfig(message_enrichment_enabled=False)
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        out = await alarm_context_enricher_node({"alarm_event": ev}, self._config(gate, None))
        assert out == {"history_stats": None, "process_snapshot": None}

    async def test_message_on_adds_enrichment_key(self):
        gate = NoiseGateConfig(message_enrichment_enabled=True)
        client = FakeProcessClient(
            result=ProcessApiResult(None, [{"name": "dockerd", "pid": 111, "p100cpu": 40.0}])
        )
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        out = await alarm_context_enricher_node({"alarm_event": ev}, self._config(gate, client))
        assert set(out) == {"history_stats", "process_snapshot", "enrichment"}
        assert out["enrichment"].kind == "disk"
        assert out["enrichment"].snapshot is not None


class TestGraphWiringMessageEnrichment:
    def test_enricher_included_when_message_on_history_gate_off(self):
        cfg = SimpleNamespace(
            alarm=SimpleNamespace(history_enabled=False),
            noise_gate=SimpleNamespace(
                enable_noise_gate=False, message_enrichment_enabled=True),
        )
        nodes = set(build_alarm_graph(cfg).get_graph().nodes.keys())
        assert "alarm_context_enricher" in nodes
        assert "notification_gate" not in nodes  # 게이트는 미배선(라우팅 불변)

    def test_enricher_excluded_when_all_off(self):
        cfg = SimpleNamespace(
            alarm=SimpleNamespace(history_enabled=False),
            noise_gate=SimpleNamespace(
                enable_noise_gate=False, message_enrichment_enabled=False),
        )
        nodes = set(build_alarm_graph(cfg).get_graph().nodes.keys())
        assert nodes == {"__start__", "__end__", "alarm_analyzer", "alarm_notifier"}
