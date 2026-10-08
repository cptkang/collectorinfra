"""plans/144 Wave A — ① 지문 dedup 심각도 상승 우회(Q-4) ② 제니퍼 상관 토큰 확장(§4.4).

  A. `_is_duplicate_fingerprint` — 플래그 on: 상승만 통과(창당 최대 2회 1→2→3) · 같거나 낮은
     심각도는 중복 · 구 레코드(심각도 키 없음) 우회 없음 · sev3 TTL 분기 유지 ·
     반환 메타 모양 불변 / 플래그 off·경량 설정: 종전과 비트 동일(레코드에 키 없음)
  B. 워커 종단(폴스타 경로) — 같은 지문 sev2 → sev3 게이트 도달 · 그 뒤 반복은 dedup
  C. 정책 경로 전용 장치(`escalated_from`)와 함께 켜져도 이중 우회·로그 중복 없음
  D. `correlation_extra` — was kind·domain_id 값을 접두 붙은 한 토큰으로
  E. `_detect_correlated_storm` — 정책 경로 제니퍼만 토큰 확장(매칭 강화) · 폴스타·off 비트 동일

apm_gateway는 import하지 않는다(D-139) — 게이트웨이 `alarm:raw` 레코드는 아래에서 합성한다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.correlation import signature_tokens
from noise_gate.infrastructure.apm_noise_context import ApmNoiseContext, correlation_extra

REF = datetime(2026, 10, 7, 10, 0, 0)
_META_KEYS = {"first_seen", "last_notified", "last_seen", "count"}


# ─── 합성 픽스처 ──────────────────────────────────────────────────────────────


def _apm_record(
    alarm_name: str = "JDBC_CONNECTION_FAIL",
    *,
    severity: int = 2,
    instance: str = "was-app-01_8080",
    alarm_id: str = "jennifer:1",
    kinds: tuple[str, ...] = ("was_db_pool_exhaustion",),
    domain_id: object = 1000,
    kind: str = "error",
) -> dict:
    """게이트웨이 `alarm:raw` 레코드(SPEC-apm-gateway §5 · events.build_alarm_payload 모양)."""
    signals = [
        {
            "kind": k,
            "level": "CRITICAL",
            "category": "strong",
            "instance_id": 1001,
            "source_tool": "event_poller",
            "source_id": "default",
        }
        for k in kinds
    ]
    return {
        "dbId": "jennifer",
        "source": "jennifer",
        "serverName": instance.split("_")[0],
        "hostname": instance.split("_")[0],
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
        "apm": {
            "source": "jennifer",
            "source_id": "default",
            "domain_id": domain_id,
            "instance_id": 1001,
            "instance_name": instance,
            "event_type": alarm_name,
            "event_kind": kind,
            "level": "warning",
            "was_signals": signals,
        },
    }


def _event(rec: dict) -> AlarmEvent:
    return AlarmEvent(
        db_id=rec["dbId"],
        server_name=rec["serverName"],
        hostname=rec["hostname"],
        ip_address=rec["ipAddress"],
        resource_ancestry=rec["resourceAncestry"],
        alarm_id=rec["alarmId"],
        severity=rec["severity"],
        alarm_status=rec["alarmStatus"],
        resource_type=rec["resourceType"],
        resource_name=rec["resourceName"],
        alarm_name=rec["alarmName"],
        alarm_time=REF,
        conditions=rec["conditions"],
        condition_log=rec["conditionLog"],
        is_clear=(rec["severity"] == 0),
        raw_payload=rec,
    )


def _polestar_record(alarm_id: str, severity: int = 2, server: str = "srv-1") -> dict:
    rec = _apm_record("CPU 사용률 임계 초과", severity=severity, alarm_id=alarm_id)
    rec.update(
        dbId="polestar_cm_gp",
        source="",
        resourceType="server.Server",
        serverName=server,
        hostname=server,
        resourceName="r1",
    )
    rec.pop("apm")
    return rec


def _dedup_worker(**ng) -> AlarmWorker:
    base = dict(repeat_interval_seconds=100, sev3_repeat_interval_seconds=100)
    base.update(ng)
    return AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(**base)))


# ═════════════════════════════════════════════════════════════════════════════
# A. _is_duplicate_fingerprint
# ═════════════════════════════════════════════════════════════════════════════


class TestDedupSeverityRise:
    def test_rise_passes_then_same_or_lower_is_dup(self):
        w = _dedup_worker(dedup_severity_rise_bypass=True)
        assert w._is_duplicate_fingerprint("fp", 1000.0, 2)[0] is False
        assert w._is_duplicate_fingerprint("fp", 1010.0, 3)[0] is False  # 상승 → 비중복
        assert w._gate_dedup["fp"]["notified_severity"] == 3
        assert w._gate_dedup["fp"]["last_notified"] == 1010.0  # 비중복 경로 그대로(리셋)
        assert w._is_duplicate_fingerprint("fp", 1020.0, 3)[0] is True  # 같은 심각도 반복
        assert w._is_duplicate_fingerprint("fp", 1030.0, 2)[0] is True  # 하위 심각도
        assert w._gate_dedup["fp"]["notified_severity"] == 3  # 중복 판정은 기록을 바꾸지 않는다

    def test_at_most_two_extra_passes_per_window(self):
        w = _dedup_worker(dedup_severity_rise_bypass=True)
        seq = [(1, False), (2, False), (3, False), (3, True), (2, True), (1, True)]
        for i, (sev, dup) in enumerate(seq):
            assert w._is_duplicate_fingerprint("fp", 1000.0 + i, sev)[0] is dup, (i, sev)

    def test_flag_off_drops_rise_and_keeps_record_shape(self):
        w = _dedup_worker(dedup_severity_rise_bypass=False)
        assert w._is_duplicate_fingerprint("fp", 1000.0, 2)[0] is False
        assert w._is_duplicate_fingerprint("fp", 1010.0, 3)[0] is True
        assert set(w._gate_dedup["fp"]) == _META_KEYS

    def test_lightweight_config_without_flag_is_off(self):
        w = _dedup_worker()  # 경량 설정 — 필드 자체가 없다
        assert w._is_duplicate_fingerprint("fp", 1000.0, 2)[0] is False
        assert w._is_duplicate_fingerprint("fp", 1010.0, 3)[0] is True
        assert set(w._gate_dedup["fp"]) == _META_KEYS

    def test_old_record_without_severity_key_is_not_bypassed(self):
        w = _dedup_worker(dedup_severity_rise_bypass=True)
        w._gate_dedup["fp"] = {
            "first_seen": 1000.0,
            "last_notified": 1000.0,
            "last_seen": 1000.0,
            "count": 1,
        }
        assert w._is_duplicate_fingerprint("fp", 1010.0, 3)[0] is True

    def test_sev3_ttl_branch_kept(self):
        w = _dedup_worker(
            dedup_severity_rise_bypass=True,
            repeat_interval_seconds=14400,
            sev3_repeat_interval_seconds=60,
        )
        assert w._is_duplicate_fingerprint("fp", 1000.0, 2)[0] is False
        assert w._is_duplicate_fingerprint("fp", 1030.0, 3)[0] is False  # 상승 우회
        assert w._is_duplicate_fingerprint("fp", 1089.0, 3)[0] is True  # sev3 TTL(60) 안
        assert w._is_duplicate_fingerprint("fp", 1091.0, 3)[0] is False  # sev3 TTL 만료
        assert w._is_duplicate_fingerprint("fp", 1100.0, 2)[0] is True  # sev2 TTL(4h) 안

    def test_returned_meta_has_no_severity_key(self):
        w = _dedup_worker(dedup_severity_rise_bypass=True)
        w._is_duplicate_fingerprint("fp", 1000.0, 2)
        is_dup, meta = w._is_duplicate_fingerprint("fp", 1001.0, 2)
        assert is_dup is True and set(meta) == _META_KEYS and meta["count"] == 2
        is_dup, prev = w._is_duplicate_fingerprint("fp", 1002.0, 3)  # 상승 → 직전 창 메타
        assert is_dup is False and set(prev) == _META_KEYS and prev["count"] == 2

    def test_bypass_logs_one_info_line(self, caplog):
        w = _dedup_worker(dedup_severity_rise_bypass=True)
        w._is_duplicate_fingerprint("fp", 1000.0, 2)
        with caplog.at_level(logging.INFO, logger=worker_mod.logger.name):
            w._is_duplicate_fingerprint("fp", 1010.0, 3, alarm_id="A-9")
            w._is_duplicate_fingerprint("fp", 1020.0, 3, alarm_id="A-10")
        lines = [r.getMessage() for r in caplog.records if "dedup 우회" in r.getMessage()]
        assert lines == ["심각도 상승 — 지문 dedup 우회: fingerprint=fp 2→3 alarm_id=A-9"]

    def test_apm_dedup_preview_matches_bypass(self):
        on = _dedup_worker(dedup_severity_rise_bypass=True)
        on._is_duplicate_fingerprint("fp", 1000.0, 1)
        assert on._apm_dedup_active("fp", 1010.0) is False  # sev2 > 1 → 통과할 것
        on._is_duplicate_fingerprint("fp", 1020.0, 2)
        assert on._apm_dedup_active("fp", 1030.0) is True
        off = _dedup_worker(dedup_severity_rise_bypass=False)
        off._is_duplicate_fingerprint("fp", 1000.0, 1)
        assert off._apm_dedup_active("fp", 1010.0) is True


# ═════════════════════════════════════════════════════════════════════════════
# B·C. 워커 종단
# ═════════════════════════════════════════════════════════════════════════════


class _Redis:
    async def xack(self, *a, **k):
        return 1

    async def xadd(self, *a, **k):
        return b"1-0"

    async def get(self, key):
        return None

    async def set(self, *a, **k):
        return True


class _Graph:
    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


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


def _gate_worker(*, bypass: bool, apm_on: bool = False) -> tuple[AlarmWorker, _Graph]:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            repeat_interval_seconds=14400,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
            apm_noise_policy_enabled=apm_on,
            dedup_severity_rise_bypass=bypass,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    w = AlarmWorker(cfg)
    g = _Graph()
    w._graph = g
    w._apm_noise_ctx = w._build_apm_noise_ctx()
    return w, g


async def _feed(w: AlarmWorker, rec: dict, n: int) -> None:
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


class TestWorkerSeverityRise:
    async def test_polestar_sev3_after_sev2_reaches_gate(self, clock):
        w, g = _gate_worker(bypass=True)
        await _feed(w, _polestar_record("p1", 2), 1)
        clock.t += 60
        await _feed(w, _polestar_record("p2", 3), 2)  # 상승 → 게이트
        clock.t += 60
        await _feed(w, _polestar_record("p3", 3), 3)  # 같은 심각도 반복 → 중복
        clock.t += 60
        await _feed(w, _polestar_record("p4", 2), 4)  # 하위 → 중복
        assert [s["alarm_event"].severity for s in g.states] == [2, 3]

    async def test_flag_off_polestar_sev3_is_dropped(self, clock):
        w, g = _gate_worker(bypass=False)
        await _feed(w, _polestar_record("p1", 2), 1)
        clock.t += 60
        await _feed(w, _polestar_record("p2", 3), 2)
        assert [s["alarm_event"].severity for s in g.states] == [2]

    async def test_policy_and_global_bypass_do_not_double(self, clock, caplog):
        w, g = _gate_worker(bypass=True, apm_on=True)
        with caplog.at_level(logging.INFO, logger=worker_mod.logger.name):
            await _feed(w, _apm_record("WARNING_JVM_HEAP_MEM_HIGH", kind="metric", alarm_id="w"), 1)
            clock.t += 30
            await _feed(
                w,
                _apm_record("ERROR_JVM_HEAP_MEM_HIGH", severity=3, kind="metric", alarm_id="e1"),
                2,
            )
            clock.t += 30
            await _feed(
                w,
                _apm_record("ERROR_JVM_HEAP_MEM_HIGH", severity=3, kind="metric", alarm_id="e2"),
                3,
            )
        assert [s["alarm_event"].severity for s in g.states] == [2, 3]
        assert g.states[1]["apm_policy"]["escalated_from"] == 2
        msgs = [r.getMessage() for r in caplog.records]
        # 정책 경로가 먼저 dedup 기록을 지우므로 전역 우회 로그는 나지 않는다(1건만)
        assert sum("심각도 상승" in m for m in msgs) == 1
        assert not any("지문 dedup 우회" in m for m in msgs)


# ═════════════════════════════════════════════════════════════════════════════
# D. correlation_extra
# ═════════════════════════════════════════════════════════════════════════════


class TestCorrelationExtra:
    def test_kinds_and_domain_as_prefixed_atomic_tokens(self):
        ev = _event(_apm_record(kinds=("was_gc_stall", "was_db_pool_exhaustion", "was_gc_stall")))
        assert correlation_extra(ev) == (
            "apmkindwasdbpoolexhaustion apmkindwasgcstall apmdomain1000"
        )
        toks = signature_tokens("", "", "", extra=correlation_extra(ev))
        assert toks == frozenset(
            {"apmkindwasdbpoolexhaustion", "apmkindwasgcstall", "apmdomain1000"}
        )

    def test_no_signals_gives_domain_only(self):
        assert correlation_extra(_event(_apm_record(kinds=()))) == "apmdomain1000"

    def test_string_domain_and_bad_entries(self):
        rec = _apm_record(kinds=(), domain_id="7001")
        rec["apm"]["was_signals"] = ["x", {"level": "CRITICAL"}, {"kind": ""}, {"kind": "WAS_Heap"}]
        assert correlation_extra(_event(rec)) == "apmkindwasheap apmdomain7001"

    @pytest.mark.parametrize("domain", [None, "", True])
    def test_missing_domain_is_skipped(self, domain):
        assert correlation_extra(_event(_apm_record(kinds=(), domain_id=domain))) == ""

    def test_no_apm_block(self):
        assert correlation_extra(_event(_polestar_record("p1"))) == ""


# ═════════════════════════════════════════════════════════════════════════════
# E. _detect_correlated_storm 토큰
# ═════════════════════════════════════════════════════════════════════════════


def _storm_worker(*, apm_on: bool, sim: float = 0.7) -> AlarmWorker:
    w = AlarmWorker(
        SimpleNamespace(
            noise_gate=SimpleNamespace(
                correlation_window_seconds=120,
                correlation_sim_threshold=sim,
                correlation_min_cluster_size=2,
                correlation_buffer_max=100,
            )
        )
    )
    w._apm_noise_ctx = ApmNoiseContext.load() if apm_on else None
    return w


def _tokens(w: AlarmWorker, ev: AlarmEvent) -> frozenset[str]:
    w._correlation_clusters.clear()
    w._detect_correlated_storm(ev, 1000.0)
    return w._correlation_clusters[ev.db_id][0].tokens


class TestStormTokens:
    def test_policy_path_adds_kind_and_domain_tokens(self):
        ev = _event(_apm_record("JDBC_CONNECTION_FAIL"))
        toks = _tokens(_storm_worker(apm_on=True), ev)
        base = signature_tokens(ev.alarm_name, ev.resource_type, "")
        assert toks == base | {"apmkindwasdbpoolexhaustion", "apmdomain1000"}

    def test_same_domain_same_kind_correlates_only_with_policy(self):
        a = _event(_apm_record("JDBC_CONNECTION_FAIL", instance="was-a_8080", alarm_id="j:a"))
        b = _event(_apm_record("DB_CONNECTION_FAIL", instance="was-b_8080", alarm_id="j:b"))
        assert a.db_id == b.db_id  # 스코프는 그대로 db_id(B-6)
        on = _storm_worker(apm_on=True)
        assert on._detect_correlated_storm(a, 1000.0) == (False, None)
        correlated, meta = on._detect_correlated_storm(b, 1001.0)
        assert correlated is True and meta["similarity"] == 0.75
        off = _storm_worker(apm_on=False)  # 토큰 확장 없음 → 4/6 < 0.7
        off._detect_correlated_storm(a, 1000.0)
        assert off._detect_correlated_storm(b, 1001.0) == (False, None)

    def test_other_domain_does_not_share_domain_token(self):
        w = _storm_worker(apm_on=True)
        a = _tokens(w, _event(_apm_record(domain_id=1000)))
        b = _tokens(w, _event(_apm_record(domain_id=2000)))
        assert "apmdomain1000" in a and "apmdomain2000" in b and "apmdomain1000" not in b

    def test_polestar_and_flag_off_tokens_unchanged(self):
        pol = _event(_polestar_record("p1"))
        assert _tokens(_storm_worker(apm_on=True), pol) == _tokens(_storm_worker(apm_on=False), pol)
        apm = _event(_apm_record())
        assert _tokens(_storm_worker(apm_on=False), apm) == signature_tokens(
            apm.alarm_name, apm.resource_type, ""
        )
