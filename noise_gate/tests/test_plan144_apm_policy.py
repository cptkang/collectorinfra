"""plans/144 W1 — 제니퍼 단독 노이즈 캔슬링(유형 정책 표 · 지속 조건 · 정규화 유형) 수용 테스트.

  A. 플래그 off 비트 동일 — 제니퍼 sev2 = 신호 수집 실패 PAGE · 지문 원문 · 워커 공급자 미생성
  B. 공급자 — 계약 키 · 유형별 중요도/통보 정책 · 인스턴스 오버라이드 · 적재 실패 · suppress 행
  C. 정책 — 유형별 티어 · 지속 조건 미달 강등(하한 DASHBOARD) / 충족 · 심각도3 불변 · evidence
  D. enricher 분기 — 제니퍼는 정책 공급자, 폴스타는 기존 리포지토리(불변)
  E. 워커 — 정규화 지문으로 해소 짝맞춤 · 지속 조건 카운트 · 조건 충족 시 1회 재판정 · sweep·상한

apm_gateway는 import하지 않는다(D-139) — 게이트웨이 `alarm:raw` 레코드는 아래에서 합성한다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_context_enricher import (
    alarm_context_enricher_node,
)
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import (
    STAGE_COLLECTION_FAILED,
    STAGE_MATRIX,
    STAGE_SEVERITY3,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    compute_fingerprint,
    decide_notification,
)
from noise_gate.infrastructure.apm_noise_context import (
    DEFAULT_POLICY_PATH,
    ApmNoiseContext,
    event_type_norm,
    normalize_event_type,
)
from noise_gate.infrastructure.polestar_noise_context import _NOISE_CTX_KEYS, _unavailable

REF = datetime(2026, 10, 7, 10, 0, 0)


# ─── 합성 픽스처 ──────────────────────────────────────────────────────────────


def _record(
    alarm_name: str = "ERROR_OUTOFMEMORY",
    *,
    severity: int = 2,
    kind: str = "error",
    norm: str | None = None,
    instance: str = "was-app-01_8080",
    alarm_id: str = "jennifer:1",
) -> dict:
    """게이트웨이가 XADD하는 `alarm:raw` 레코드(SPEC-apm-gateway §5 모양 · 값은 합성)."""
    apm = {
        "source": "jennifer",
        "source_id": "default",
        "domain_id": 1000,
        "instance_id": 1001,
        "instance_name": instance,
        "event_type": alarm_name,
        "event_kind": kind,
        "level": "warning",
        "was_signals": [],
    }
    if norm is not None:
        apm["event_type_norm"] = norm
    return {
        "dbId": "jennifer",
        "source": "jennifer",
        "serverName": "was-app-01",
        "hostname": "was-app-01",
        "ipAddress": "192.0.2.11",
        "resourceAncestry": "JENNIFER > 도메인 > " + instance,
        "alarmId": alarm_id,
        "severity": severity,
        "alarmStatus": "",
        "resourceType": "apm.Instance",
        "resourceName": instance,
        "alarmName": alarm_name,
        "alarmTime": "20261007100000",
        "conditions": f"JENNIFER EVENT warning — {alarm_name}",
        "conditionLog": "event (value=1)",
        "apm": apm,
    }


def _apm_event(alarm_name: str = "ERROR_OUTOFMEMORY", *, severity: int = 2, **kw) -> AlarmEvent:
    rec = _record(alarm_name, severity=severity, **kw)
    return AlarmEvent(
        db_id=rec["dbId"],
        server_name=rec["serverName"],
        hostname=rec["hostname"],
        ip_address=rec["ipAddress"],
        resource_ancestry=rec["resourceAncestry"],
        alarm_id=rec["alarmId"],
        severity=severity,
        alarm_status="",
        resource_type=rec["resourceType"],
        resource_name=rec["resourceName"],
        alarm_name=alarm_name,
        alarm_time=REF,
        conditions=rec["conditions"],
        condition_log=rec["conditionLog"],
        is_clear=(severity == 0),
        raw_payload=rec,
    )


def _polestar_event(severity: int = 2) -> AlarmEvent:
    return AlarmEvent(
        db_id="polestar_cm_gp",
        server_name="srv-1",
        hostname="srv-1",
        ip_address="",
        resource_ancestry="",
        alarm_id="P-1",
        severity=severity,
        alarm_status="NOT_ACK",
        resource_type="server.Server",
        resource_name="r1",
        alarm_name="CPU 사용률 임계 초과",
        alarm_time=REF,
        conditions=">90",
        condition_log="95",
        is_clear=(severity == 0),
    )


def _gate_cfg(**over) -> SimpleNamespace:
    base = dict(suppress_max_severity=2, importance_value_map={}, resolved_to_dashboard=False)
    base.update(over)
    return SimpleNamespace(**base)


def _provider() -> ApmNoiseContext:
    ctx = ApmNoiseContext.load()
    assert ctx.available, ctx.error
    return ctx


def _apm_policy(event: AlarmEvent, provider: ApmNoiseContext, persistence=None) -> dict:
    m = provider.match(event)
    return {
        "event_type_norm": m.event_type_norm,
        "policy_row": m.row_key,
        "persistence": persistence,
    }


def _decide(event: AlarmEvent, noise_ctx, *, apm_policy=None, analysis=None, **cfg):
    return decide_notification(
        event, None, analysis, noise_ctx, _gate_cfg(**cfg), apm_policy=apm_policy
    )


# ═════════════════════════════════════════════════════════════════════════════
# A. 플래그 off 비트 동일
# ═════════════════════════════════════════════════════════════════════════════


class TestFlagOffBitIdentical:
    def test_jennifer_sev2_is_collection_failed_page(self):
        # off: 폴스타 리포지토리가 미등록 db_id(jennifer)에 unavailable → step 5 보수 PAGE(현행).
        ev = _apm_event("JVM_HEAP_MEM_HIGH", severity=2, kind="metric")
        d = _decide(ev, _unavailable())
        assert d.tier == TIER_PAGE and d.stage == STAGE_COLLECTION_FAILED
        assert d.reason == "신호 수집 실패 — 보수적 PAGE"
        assert "apm_policy" not in d.evidence

    def test_fingerprint_uses_raw_alarm_name(self):
        ev = _apm_event("ERROR_OUTOFMEMORY")
        d = _decide(ev, _unavailable())
        assert d.fingerprint == compute_fingerprint(ev)
        assert compute_fingerprint(ev) != compute_fingerprint(ev, alarm_key="OUTOFMEMORY")

    def test_compute_fingerprint_default_unchanged(self):
        # alarm_key 미지정 = 현행 산식(SHA-1 of db_id·server·alarm_name·resource)
        import hashlib

        ev = _polestar_event()
        raw = "\x1f".join([ev.db_id, ev.server_name, ev.alarm_name, ev.resource_name])
        assert compute_fingerprint(ev) == hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def test_worker_does_not_build_provider_when_off(self):
        w, _ = _worker(apm_on=False)
        assert w._build_apm_noise_ctx() is None

    async def test_worker_state_has_no_apm_policy_when_off(self):
        w, g = _worker(apm_on=False)
        await _feed(w, _record("ERROR_OUTOFMEMORY", alarm_id="a1"))
        assert g.states[0]["apm_policy"] is None
        # 회복 `OUTOFMEMORY`는 원문 지문이라 `ERROR_OUTOFMEMORY` 발생과 짝이 맞지 않는다(현행).
        await _feed(w, _record("OUTOFMEMORY", severity=0, alarm_id="a2"))
        assert g.states[1]["self_heal"] is False


# ═════════════════════════════════════════════════════════════════════════════
# B. 공급자
# ═════════════════════════════════════════════════════════════════════════════


class TestProvider:
    def test_contract_keys_and_source(self):
        ctx = _provider().fetch(_apm_event("ERROR_OUTOFMEMORY"))
        assert set(ctx) == set(_NOISE_CTX_KEYS) | {"source"}
        assert ctx["source"] == "apm_policy"
        assert ctx["maintenance"] is None and ctx["parent_avail_status"] is None
        assert ctx["cascaded"] is None and ctx["change_nearby"] is None

    def test_critical_type_is_high_and_notify(self):
        ctx = _provider().fetch(_apm_event("ERROR_OUTOFMEMORY"))
        assert ctx["importance_id"] == "높음" and ctx["noti_policy"] == "notify"

    def test_warning_type_is_normal_without_notify(self):
        ctx = _provider().fetch(_apm_event("HTTP_IO_EXCEPTION"))
        assert ctx["importance_id"] == "보통" and ctx["noti_policy"] is None

    def test_unknown_type_defaults_to_normal(self):
        p = _provider()
        ev = _apm_event("ERROR_SOMETHING_NEW")
        assert p.match(ev).row_key == "default"
        ctx = p.fetch(ev)
        assert ctx["importance_id"] == "보통" and ctx["noti_policy"] is None

    def test_normalized_type_prefers_gateway_field(self):
        ev = _apm_event("ERROR_JVM_HEAP_MEM_HIGH", norm="JVM_HEAP_MEM_HIGH")
        assert event_type_norm(ev) == "JVM_HEAP_MEM_HIGH"
        # 구버전 게이트웨이(필드 없음) — alarm_name을 같은 규칙으로 정규화
        assert event_type_norm(_apm_event("warning_jvm_heap_mem_high")) == "JVM_HEAP_MEM_HIGH"
        assert normalize_event_type("ERROR_ERROR_X") == "ERROR_X"  # 접두는 1회만

    def test_instance_override(self, tmp_path: Path):
        path = _write_policy(tmp_path, instances="  was-order-01_8080: 높음\n")
        p = ApmNoiseContext.load(path)
        ctx = p.fetch(_apm_event("HTTP_IO_EXCEPTION", instance="was-order-01_8080"))
        assert ctx["importance_id"] == "높음"
        assert p.fetch(_apm_event("HTTP_IO_EXCEPTION"))["importance_id"] == "보통"

    @pytest.mark.parametrize(
        "body",
        [
            None,
            "types: [broken",
            "types:\n  default: {notify: page, importance: 최고}\n",
            "types:\n  X: {notify: page}\n",
        ],
    )
    def test_broken_policy_is_unavailable_with_warning(self, tmp_path: Path, caplog, body):
        path = tmp_path / "apm_noise_policy.yaml"
        if body is not None:
            path.write_text(body, encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="noise_gate.infrastructure.apm_noise_context"):
            p = ApmNoiseContext.load(path)
        assert not p.available and p.error
        assert any("적재 실패" in r.getMessage() for r in caplog.records)
        ctx = p.fetch(_apm_event("ERROR_OUTOFMEMORY"))
        assert ctx == _unavailable()
        d = _decide(_apm_event("ERROR_OUTOFMEMORY"), ctx)
        assert d.tier == TIER_PAGE and d.stage == STAGE_COLLECTION_FAILED

    def test_suppress_row_is_treated_as_dashboard(self, tmp_path: Path, caplog):
        path = _write_policy(
            tmp_path, extra_types="  NOISY_TYPE: {notify: suppress, importance: 낮음}\n"
        )
        with caplog.at_level(logging.WARNING, logger="noise_gate.infrastructure.apm_noise_context"):
            p = ApmNoiseContext.load(path)
        assert p.available
        assert any("suppress" in r.getMessage() for r in caplog.records)
        m = p.match(_apm_event("NOISY_TYPE"))
        assert m.row is not None and m.row.notify == "dashboard"
        ctx = p.fetch(_apm_event("NOISY_TYPE"))
        assert ctx["noti_policy"] is None
        # 낮음 sev2 → 매트릭스 DASHBOARD — SUPPRESS로 가지 않는다
        assert _decide(_apm_event("NOISY_TYPE"), ctx).tier == TIER_DASHBOARD

    def test_shipped_policy_has_no_suppress_and_covers_gateway_types(self):
        p = _provider()
        types = p._policy.types
        assert "default" in types
        assert all(row.notify != "suppress" for row in types.values())
        expected = {
            "SERVICE_QUEUING",
            "PLC_REJECTED",
            "HIGH_RATE_REJECT",
            "JDBC_CONNECTION_FAIL",
            "DB_CONNECTION_FAIL",
            "DB_CONN_UNCLOSED",
            "MAYBE_GC_TIME_DELAY",
            "OUTOFMEMORY",
            "JVM_HEAP_MEM_HIGH",
            "HTTP_IO_EXCEPTION",
            "HIGH_RATE_FAIL",
        }
        assert expected <= set(types)
        assert DEFAULT_POLICY_PATH.name == "apm_noise_policy.yaml"


def _write_policy(tmp_path: Path, *, instances: str = "", extra_types: str = "") -> Path:
    text = (
        "version: 1\n"
        "types:\n"
        "  HTTP_IO_EXCEPTION: {notify: dashboard, importance: 보통}\n"
        "  default: {notify: dashboard, importance: 보통}\n"
        + extra_types
        + "instances:\n"
        + (instances or "  {}\n")
    )
    if not instances:
        text = text.replace("instances:\n  {}\n", "instances: {}\n")
    path = tmp_path / "apm_noise_policy.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ═════════════════════════════════════════════════════════════════════════════
# C. 정책
# ═════════════════════════════════════════════════════════════════════════════


def _persist(count: int, met: bool) -> dict:
    return {"count": count, "min_count": 2, "window_seconds": 300, "met": met}


class TestPolicyTiers:
    def test_critical_type_sev2_is_page(self):
        p = _provider()
        ev = _apm_event("ERROR_OUTOFMEMORY", severity=2)
        d = _decide(ev, p.fetch(ev), apm_policy=_apm_policy(ev, p))
        assert d.tier == TIER_PAGE and d.stage == STAGE_MATRIX
        assert d.signals["importance"] == "높음"

    def test_critical_type_sev1_is_promoted_to_page(self):
        # 매트릭스 sev1×높음 = TICKET → 통보 정책(page) 승격 → PAGE
        p = _provider()
        ev = _apm_event("ERROR_DB_CONNECTION_FAIL", severity=1)
        d = _decide(ev, p.fetch(ev), apm_policy=_apm_policy(ev, p))
        assert d.evidence["base_tier"] == TIER_TICKET and d.tier == TIER_PAGE

    def test_warning_type_sev2_follows_matrix(self):
        p = _provider()
        ev = _apm_event("HTTP_IO_EXCEPTION", severity=2)
        d = _decide(ev, p.fetch(ev), apm_policy=_apm_policy(ev, p))
        assert d.tier == TIER_TICKET and d.evidence["promote"] == [] and d.evidence["demote"] == []

    def test_unknown_type_is_normal(self):
        p = _provider()
        ev = _apm_event("SOMETHING_NEW", severity=2)
        d = _decide(ev, p.fetch(ev), apm_policy=_apm_policy(ev, p))
        assert d.signals["importance"] == "보통" and d.tier == TIER_TICKET

    def test_unknown_label_in_apm_ctx_is_normal(self):
        ctx = {k: None for k in _NOISE_CTX_KEYS} | {"importance_id": "최고", "source": "apm_policy"}
        d = _decide(_apm_event("X"), ctx)
        assert d.signals["importance"] == "보통"

    def test_instance_override_high_pages_warning_type(self, tmp_path: Path):
        p = ApmNoiseContext.load(_write_policy(tmp_path, instances="  was-app-01_8080: 높음\n"))
        ev = _apm_event("HTTP_IO_EXCEPTION", severity=2)
        assert _decide(ev, p.fetch(ev), apm_policy=_apm_policy(ev, p)).tier == TIER_PAGE

    def test_fingerprint_and_evidence_use_normalized_type(self):
        p = _provider()
        ev = _apm_event("ERROR_OUTOFMEMORY", severity=2)
        pol = _apm_policy(ev, p)
        d = _decide(ev, p.fetch(ev), apm_policy=pol)
        assert d.fingerprint == compute_fingerprint(ev, alarm_key="OUTOFMEMORY")
        assert d.evidence["apm_policy"] == {
            "event_type_norm": "OUTOFMEMORY",
            "policy_row": "OUTOFMEMORY",
            "persistence": None,
        }


class TestPersistencePolicy:
    def _metric(self, severity: int = 2) -> tuple[AlarmEvent, dict, ApmNoiseContext]:
        p = _provider()
        ev = _apm_event("JVM_HEAP_MEM_HIGH", severity=severity, kind="metric")
        return ev, p.fetch(ev), p

    def test_unmet_demotes_one_step(self):
        ev, ctx, p = self._metric()
        d = _decide(ev, ctx, apm_policy=_apm_policy(ev, p, _persist(1, False)))
        assert d.evidence["base_tier"] == TIER_TICKET and d.tier == TIER_DASHBOARD
        assert d.evidence["demote"] == ["지속 조건 미달(제니퍼 지표형)"]
        assert d.evidence["apm_policy"]["persistence"]["count"] == 1

    def test_met_keeps_matrix(self):
        ev, ctx, p = self._metric()
        d = _decide(ev, ctx, apm_policy=_apm_policy(ev, p, _persist(2, True)))
        assert d.tier == TIER_TICKET and d.evidence["demote"] == []

    def test_floor_is_dashboard(self, tmp_path: Path):
        # 중요도 낮음 → 매트릭스 DASHBOARD. 지속 조건 미달만으로는 SUPPRESS로 내려가지 않는다.
        p = ApmNoiseContext.load(_write_policy(tmp_path, instances="  was-app-01_8080: 낮음\n"))
        ev = _apm_event("JVM_HEAP_MEM_HIGH", severity=2, kind="metric")
        d = _decide(ev, p.fetch(ev), apm_policy=_apm_policy(ev, p, _persist(1, False)))
        assert d.evidence["base_tier"] == TIER_DASHBOARD and d.tier == TIER_DASHBOARD

    def test_other_demote_reason_keeps_suppress_floor(self, tmp_path: Path):
        # 다른 강등 사유(is_routine)가 함께면 현행 하한(SUPPRESS) 그대로
        # — 지속 조건이 완화하지 않는다.
        p = ApmNoiseContext.load(_write_policy(tmp_path, instances="  was-app-01_8080: 낮음\n"))
        ev = _apm_event("JVM_HEAP_MEM_HIGH", severity=2, kind="metric")
        analysis = SimpleNamespace(is_routine=True, pattern_type="", ai_message_severity=None)
        d = _decide(
            ev, p.fetch(ev), apm_policy=_apm_policy(ev, p, _persist(1, False)), analysis=analysis
        )
        assert d.tier == TIER_SUPPRESS

    def test_promotion_wins_over_unmet(self):
        # 승격 우선 규칙 그대로 — 비일상 패턴(is_routine=False) 승격이면 지속 조건 강등은 무시.
        ev, ctx, p = self._metric()
        analysis = SimpleNamespace(is_routine=False, pattern_type="", ai_message_severity=None)
        d = _decide(ev, ctx, apm_policy=_apm_policy(ev, p, _persist(1, False)), analysis=analysis)
        assert d.tier == TIER_PAGE

    def test_severity3_unchanged(self):
        ev, ctx, p = self._metric(severity=3)
        d = _decide(ev, ctx, apm_policy=_apm_policy(ev, p, _persist(1, False)))
        assert d.tier == TIER_PAGE and d.stage == STAGE_SEVERITY3

    def test_severity1_not_demoted(self):
        ev, ctx, p = self._metric(severity=1)
        d = _decide(ev, ctx, apm_policy=_apm_policy(ev, p, _persist(1, False)))
        assert d.evidence["demote"] == []

    def test_polestar_without_apm_policy_unchanged(self):
        ev = _polestar_event()
        ctx = {k: None for k in _NOISE_CTX_KEYS} | {"importance_id": "3", "source": "polestar_db"}
        a = decide_notification(ev, None, None, ctx, _gate_cfg(importance_value_map={"3": "높음"}))
        b = decide_notification(
            ev, None, None, ctx, _gate_cfg(importance_value_map={"3": "높음"}), apm_policy=None
        )
        assert (a.tier, a.reason, a.priority, a.fingerprint, a.evidence) == (
            b.tier,
            b.reason,
            b.priority,
            b.fingerprint,
            b.evidence,
        )
        assert a.tier == TIER_PAGE and "apm_policy" not in a.evidence


# ═════════════════════════════════════════════════════════════════════════════
# D. enricher 분기
# ═════════════════════════════════════════════════════════════════════════════


class _SpyNoiseRepo:
    def __init__(self):
        self.calls: list[str] = []

    def is_db_registered(self, db_id):
        return db_id != "jennifer"

    async def fetch(self, event, **kw):
        self.calls.append(event.db_id)
        if event.db_id == "jennifer":
            return _unavailable()
        return {k: None for k in _NOISE_CTX_KEYS} | {"importance_id": "3", "source": "polestar_db"}


def _enricher_config(repo, apm_ctx=None) -> dict:
    alarm = SimpleNamespace(
        history_enabled=False, enrich_timeout_seconds=5, process_enrich_enabled=False
    )
    gate = SimpleNamespace(
        enable_noise_gate=True,
        dependency_suppression=False,
        multi_hop_cascade_enabled=False,
        message_enrichment_enabled=False,
        dynamic_baseline_enabled=False,
        noise_context_cache_ttl_seconds=0,
    )
    configurable = {
        "app_config": SimpleNamespace(alarm=alarm, noise_gate=gate),
        "noise_repo": repo,
        "history_repo": None,
        "process_client": None,
        "history_redis": None,
    }
    if apm_ctx is not None:
        configurable["apm_noise_ctx"] = apm_ctx
    return {"configurable": configurable}


class TestEnricherBranch:
    async def test_off_jennifer_goes_to_polestar_repo_unavailable(self):
        repo = _SpyNoiseRepo()
        out = await alarm_context_enricher_node(
            {"alarm_event": _apm_event()}, _enricher_config(repo)
        )
        assert out["noise_context"]["source"] == "unavailable" and repo.calls == ["jennifer"]

    async def test_on_jennifer_uses_apm_provider(self):
        repo = _SpyNoiseRepo()
        out = await alarm_context_enricher_node(
            {"alarm_event": _apm_event()}, _enricher_config(repo, _provider())
        )
        assert out["noise_context"]["source"] == "apm_policy" and repo.calls == []
        assert set(out) == {"history_stats", "process_snapshot", "noise_context"}

    async def test_on_polestar_unchanged(self):
        repo = _SpyNoiseRepo()
        off = await alarm_context_enricher_node(
            {"alarm_event": _polestar_event()}, _enricher_config(repo)
        )
        on = await alarm_context_enricher_node(
            {"alarm_event": _polestar_event()}, _enricher_config(repo, _provider())
        )
        assert off == on and on["noise_context"]["source"] == "polestar_db"
        assert repo.calls == ["polestar_cm_gp", "polestar_cm_gp"]

    async def test_apm_provider_alone_does_not_gate_polestar(self):
        """noise_repo 없이 APM 공급자만 있으면 폴스타 알람은 게이트 off 그대로(2키)."""
        out = await alarm_context_enricher_node(
            {"alarm_event": _polestar_event()}, _enricher_config(None, _provider())
        )
        assert set(out) == {"history_stats", "process_snapshot"}
        jen = await alarm_context_enricher_node(
            {"alarm_event": _apm_event()}, _enricher_config(None, _provider())
        )
        assert jen["noise_context"]["source"] == "apm_policy"


# ═════════════════════════════════════════════════════════════════════════════
# E. 워커
# ═════════════════════════════════════════════════════════════════════════════


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


def _worker(*, apm_on: bool, policy_path: Path | None = None) -> tuple[AlarmWorker, _FakeGraph]:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            repeat_interval_seconds=14400,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
            apm_noise_policy_enabled=apm_on,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    w = AlarmWorker(cfg)
    g = _FakeGraph()
    w._graph = g
    if apm_on:
        w._apm_noise_ctx = (
            ApmNoiseContext.load(policy_path) if policy_path else w._build_apm_noise_ctx()
        )
    return w, g


async def _feed(w: AlarmWorker, record: dict) -> None:
    fields = {b"data": json.dumps(record, ensure_ascii=False).encode("utf-8")}
    await w._process(_WorkerRedis(), "alarm:raw", "g", b"1-1", fields, {})


class TestWorker:
    async def test_recovery_pairs_with_prefixed_occurrence(self):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("ERROR_OUTOFMEMORY", norm="OUTOFMEMORY", alarm_id="a1"))
        await _feed(w, _record("OUTOFMEMORY", severity=0, norm="OUTOFMEMORY", alarm_id="a2"))
        assert [s["self_heal"] for s in g.states] == [False, True]
        assert g.states[0]["apm_policy"]["event_type_norm"] == "OUTOFMEMORY"
        assert g.states[0]["apm_policy"]["policy_row"] == "OUTOFMEMORY"

    async def test_recovery_pairs_without_gateway_norm_field(self):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("ERROR_OUTOFMEMORY", alarm_id="a1"))
        await _feed(w, _record("OUTOFMEMORY", severity=0, alarm_id="a2"))
        assert g.states[1]["self_heal"] is True

    async def test_error_kind_has_no_persistence(self):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="error", alarm_id="a1"))
        assert g.states[0]["apm_policy"]["persistence"] is None

    async def test_persistence_unmet_then_met_reevaluates_once(self):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="a1"))
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="a2"))
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="a3"))
        # 1건째: 미달(그래프로 간다 · 붙잡지 않음) / 2건째: 충족 → dedup 1회 건너뛰고 재판정 /
        # 3건째: 현행 지문 dedup(재발 억제)
        assert len(g.states) == 2
        p1, p2 = (s["apm_policy"]["persistence"] for s in g.states)
        assert p1["met"] is False and p1["count"] == 1 and "reevaluated" not in p1
        assert p2["met"] is True and p2["count"] == 2 and p2["reevaluated"] is True
        assert w._apm_persist_pending == {}

    async def test_off_second_metric_event_is_deduped(self):
        w, g = _worker(apm_on=False)
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="a1"))
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="a2"))
        assert len(g.states) == 1

    def test_counter_window_and_sweep(self):
        w, _ = _worker(apm_on=True)
        p = w._apm_noise_ctx
        ev = _apm_event("JVM_HEAP_MEM_HIGH", kind="metric")
        m = p.match(ev)
        fp = compute_fingerprint(ev, alarm_key=m.event_type_norm)
        s1 = w._apm_policy_signal(ev, fp, m, 1000.0)
        s2 = w._apm_policy_signal(ev, fp, m, 1400.0)  # 창(300초) 밖 — 다시 1건
        assert s1["persistence"]["count"] == 1 and s2["persistence"]["count"] == 1
        assert fp in w._apm_persist and fp in w._apm_persist_pending
        # 다른 지문 이벤트가 창 밖 시각에 오면 만료 키가 정리된다
        other = _apm_event("MAYBE_GC_TIME_DELAY", kind="metric")
        om = p.match(other)
        w._apm_policy_signal(
            other, compute_fingerprint(other, alarm_key=om.event_type_norm), om, 1800.0
        )
        assert fp not in w._apm_persist
        # 보류 지문은 repeat_interval 밖에서 정리된다
        w._apm_policy_signal(other, "fp-x", om, 1400.0 + 14400 + 1)
        assert fp not in w._apm_persist_pending

    def test_counter_cap(self, monkeypatch):
        monkeypatch.setattr(worker_mod, "_APM_PERSIST_MAX_KEYS", 3)
        w, _ = _worker(apm_on=True)
        p = w._apm_noise_ctx
        ev = _apm_event("JVM_HEAP_MEM_HIGH", kind="metric")
        m = p.match(ev)
        for i in range(5):
            w._apm_policy_signal(ev, f"fp-{i}", m, 1000.0 + i)
        assert set(w._apm_persist) == {"fp-2", "fp-3", "fp-4"}
        assert set(w._apm_persist_pending) == {"fp-2", "fp-3", "fp-4"}

    def test_policy_error_is_recorded(self, tmp_path: Path):
        w, _ = _worker(apm_on=True, policy_path=tmp_path / "missing.yaml")
        ev = _apm_event("JVM_HEAP_MEM_HIGH", kind="metric")
        m = w._apm_noise_ctx.match(ev)
        sig = w._apm_policy_signal(ev, "fp", m, 1000.0)
        assert sig["policy_row"] == "" and sig["persistence"] is None and sig["policy_error"]

    async def test_polestar_event_has_no_apm_policy(self):
        w, g = _worker(apm_on=True)
        rec = _record("CPU 사용률 임계 초과", alarm_id="p1")
        rec.update(dbId="polestar_cm_gp", source="", resourceType="server.Server")
        rec.pop("apm")
        await _feed(w, rec)
        assert g.states[0]["apm_policy"] is None


class _Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


def _polestar_record(alarm_id: str, severity: int = 2) -> dict:
    rec = _record("CPU 사용률 임계 초과", severity=severity, alarm_id=alarm_id)
    rec.update(dbId="polestar_cm_gp", source="", resourceType="server.Server")
    rec.pop("apm")
    return rec


class TestWorkerDedupInteraction:
    """정책 경로의 지문 dedup 해제는 재판정 1회 · 심각도 상승뿐이다(교정 라운드)."""

    async def test_unmet_dropped_by_dedup_does_not_set_pending(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="a"))  # 미달
        clock.t += 60
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="b"))  # 재판정
        clock.t += 600
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="x"))  # 미달·중복
        assert w._apm_persist_pending == {}
        clock.t += 60
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="y"))  # 충족·중복
        assert len(g.states) == 2
        assert g.states[1]["apm_policy"]["persistence"]["reevaluated"] is True

    async def test_sev3_after_demoted_sev2_passes_dedup_once(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("WARNING_JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="w"))
        clock.t += 30
        await _feed(w, _record("ERROR_JVM_HEAP_MEM_HIGH", severity=3, kind="metric", alarm_id="e1"))
        clock.t += 30
        await _feed(w, _record("ERROR_JVM_HEAP_MEM_HIGH", severity=3, kind="metric", alarm_id="e2"))
        clock.t += 30
        await _feed(w, _record("WARNING_JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="w2"))
        # sev3 1건만 dedup을 건너뛴다 — 같은 심각도 반복·하위 심각도는 현행 dedup
        assert [s["alarm_event"].severity for s in g.states] == [2, 3]
        assert g.states[1]["apm_policy"]["escalated_from"] == 2
        assert "escalated_from" not in g.states[0]["apm_policy"]

    async def test_sev3_escalation_off_and_polestar_unchanged(self, clock):
        off, g_off = _worker(apm_on=False)
        await _feed(off, _record("WARNING_OUTOFMEMORY", alarm_id="w"))
        await _feed(off, _record("WARNING_OUTOFMEMORY", severity=3, alarm_id="e"))
        assert len(g_off.states) == 1 and off._apm_sent_severity == {}
        on, g_on = _worker(apm_on=True)
        await _feed(on, _polestar_record("p1"))
        await _feed(on, _polestar_record("p2", severity=3))
        assert len(g_on.states) == 1 and on._apm_sent_severity == {}

    async def test_clear_resets_counter_and_pending(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="a"))  # 미달
        assert w._apm_persist_pending and w._apm_persist
        clock.t += 30
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", severity=0, kind="metric", alarm_id="c"))
        assert w._apm_persist_pending == {} and w._apm_persist == {}
        clock.t += 30
        await _feed(w, _record("JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="b"))  # 재발
        # 해소 뒤 재발은 1건째 미달로 다시 세고, 현행 dedup(4h)이 버린다 — 「충족」 우회 없음
        assert [s["alarm_event"].severity for s in g.states] == [2, 0]
        assert w._apm_persist_pending == {}


class _SpyStore:
    def __init__(self):
        self.kwargs: list[dict] = []

    def record(self, decision, **kw):
        self.kwargs.append(kw)


class TestGateWiring:
    async def test_gate_passes_apm_policy_and_records_evidence(self):
        from noise_gate.application.nodes.notification_gate import notification_gate_node

        p = _provider()
        ev = _apm_event("JVM_HEAP_MEM_HIGH", severity=2, kind="metric")
        store = _SpyStore()
        state = {
            "alarm_event": ev,
            "analysis_result": SimpleNamespace(
                error=None, is_routine=None, pattern_type="", ai_message_severity=None
            ),
            "noise_context": p.fetch(ev),
            "apm_policy": _apm_policy(ev, p, _persist(1, False)),
        }
        gate = SimpleNamespace(
            enable_noise_gate=True,
            suppress_max_severity=2,
            importance_value_map={},
            resolved_to_dashboard=False,
        )
        out = await notification_gate_node(
            state,
            {
                "configurable": {
                    "app_config": SimpleNamespace(noise_gate=gate),
                    "decision_store": store,
                }
            },
        )
        d = out["notification_decision"]
        assert d.tier == TIER_DASHBOARD
        assert d.fingerprint == compute_fingerprint(ev, alarm_key="JVM_HEAP_MEM_HIGH")
        assert store.kwargs[0]["stage_evidence"]["apm_policy"]["persistence"]["met"] is False
