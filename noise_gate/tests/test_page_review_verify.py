"""plans/162 W1 독립 검증 — PAGE 필요성 재판정(page_review · D-330).

구현자 테스트(`test_page_review.py`)와 겹치지 않게 다음을 본다.

    A. 실시간 경로(워커 → 실제 게이트 노드 → 판정 기록 → observe)와 기동 복원(같은 기록 → restore)이
       같은 레지스트리 상태가 되는가 — shadow·enforce 양쪽.
    B. 해소 이벤트가 실제 게이트 노드를 거쳐 레지스트리를 닫는가 · R1 재개 시 resolved_ts 처리.
    C. 그래프 뒤 observe 실패가 ACK를 막지 않고 dead-letter로 가지 않는가.
    D. 앱 영향 승격(게이트 노드) 뒤에 재판정이 돌아 보존 ③이 걸리는가.
    E. 시각 단위 — 오프셋이 다른 ISO ts도 같은 epoch로 복원되는가.
    F. 제니퍼 정책 경로 지문 — 워커 지문(alarm_key)과 decision.fingerprint 일치.
    G. shadow 근거가 관제 집계(aggregate·funnel)의 티어 수를 바꾸지 않는가.
    H. 설정 7종 기본값(.env 누수 차단) · R6/CHRONIC 부재 · .env.example 인라인 주석 없음.
    I. 도메인 순수성(stdlib + noise_gate.domain만).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
import time as _real_time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from src.config import NoiseGateConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.notification_policy import (
    STAGE_MATRIX,
    STAGE_PAGE_REVIEW,
    TIER_PAGE,
    TIER_TICKET,
    compute_fingerprint,
    decide_notification,
)
from noise_gate.domain.page_review import (
    EVIDENCE_KEY,
    KEEP_APP_IMPACT,
    RULE_FANOUT,
    RULE_OPEN_REPEAT,
    RULE_SAME_HOST,
    PageRegistry,
    _record_epoch,
    fanout_key_of,
    host_key_of,
    review_page,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.test_notification_policy import IMP_MAP, make_ctx, make_event
from noise_gate.tests.test_plan144_cross_source import _Clock, _polestar_rec

ROOT = Path(__file__).resolve().parents[2]
DB = "polestar_cm_gp"


# ── 공용 ──────────────────────────────────────────────────────────────────


def _review_ns(mode: str) -> dict:
    # 검증 대상 필드를 전부 명시한다(.env 누수 차단).
    return dict(
        page_review_mode=mode,
        page_review_host_window_minutes=30,
        page_review_fanout_window_minutes=10,
        page_review_fanout_page_max=1,
        page_review_reminder_hours=24,
        page_review_reopen_quiet_minutes=30,
        page_review_always_page_globs=[],
    )


def _cfg(mode: str, **gate_extra) -> SimpleNamespace:
    """워커·게이트 노드가 함께 쓰는 경량 설정."""
    return SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            repeat_interval_seconds=60,
            dedup_severity_rise_bypass=False,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
            importance_value_map=IMP_MAP,
            resolved_to_dashboard=False,
            **_review_ns(mode),
            **gate_extra,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )


class _ClockStore(DecisionStore):
    """판정 기록 ts를 워커 시계로 찍는다(실 운용에서는 ts ≈ 워커 now + 분석 지연)."""

    def __init__(self, path: str, clock: _Clock) -> None:
        super().__init__(path)
        self._clock = clock

    def record(self, decision, **kw):  # noqa: ANN001, ANN003, ANN201
        kw.setdefault("ts", datetime.fromtimestamp(self._clock.t, tz=UTC))
        return super().record(decision, **kw)


class _GateGraph:
    """분석기만 건너뛰고 **실제 게이트 노드**를 부르는 가짜 그래프(기록까지 실경로)."""

    def __init__(self, cfg, store: DecisionStore, importance: str = "HIGH") -> None:  # noqa: ANN001
        self.cfg = cfg
        self.store = store
        self.importance = importance
        self.states: list[dict] = []
        self.decisions: list = []

    async def ainvoke(self, state, config=None):  # noqa: ANN001, ANN201
        self.states.append(state)
        st = {
            **state,
            "analysis_result": SimpleNamespace(error=None),
            "noise_context": make_ctx(importance_id=self.importance),
        }
        out = await notification_gate_node(
            st, {"configurable": {"app_config": self.cfg, "decision_store": self.store}}
        )
        self.decisions.append(out.get("notification_decision"))
        return {**st, **out}


class _CountingRedis:
    def __init__(self) -> None:
        self.acks = 0
        self.xadds: list = []

    async def xack(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        self.acks += 1
        return 1

    async def xadd(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        self.xadds.append(a)
        return b"1-0"

    async def get(self, key):  # noqa: ANN001, ANN201
        return None

    async def set(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return True


async def _feed(w: AlarmWorker, rec: dict, n: int, redis=None) -> None:  # noqa: ANN001
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(redis or _CountingRedis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    # 판정 기록 조회(recent_decisions)는 실 시각 기준 24h 창이라 시계를 실 시각 2시간 전에서
    # 시작한다. ISO ts는 마이크로초까지라 정수 초로 맞춰 float 왕복 오차를 없앤다.
    c = _Clock(float(int(_real_time.time()) - 2 * 3600))
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


def _registry_state(reg: PageRegistry) -> dict:
    """비교용 레지스트리 상태 — 동시발생 키는 대표 항목만(비대표는 근거 수치 전용)."""
    pages = {fp: dataclasses.asdict(e) for fp, e in reg._pages.items()}
    by_host = {k: sorted(v) for k, v in reg._by_host.items() if v}
    fanout = {}
    for key, hosts in reg._fanout.items():
        reps = {h: dict(m) for h, m in hosts.items() if m.get("representative")}
        if reps:
            fanout[key] = reps
    return {"pages": pages, "by_host": by_host, "fanout": fanout}


# 실시간 시퀀스: (경과 초, 알람 id, 호스트, 알람명, 심각도)
_SEQUENCE = [
    (0, "A-1", "WEB01.corp", "CPU 사용률", 2),      # 새 사건 첫 PAGE (대표)
    (120, "A-2", "WEB01.corp", "CPU 사용률", 2),    # R1 — 열린 사건 반복
    (180, "A-3", "web01", "메모리 사용률", 2),      # R2 — 같은 호스트(정규화 키) 다른 사건
    (240, "A-4", "web02", "CPU 사용률", 2),         # R3 — 동시 발생 대표 외
    (300, "A-5", "WEB01.corp", "CPU 사용률", 0),    # 해소 → 닫힘
    (420, "A-6", "WEB01.corp", "CPU 사용률", 2),    # R1 — 해소 뒤 유예 안 재발(재개)
    (480, "A-7", "web03", "디스크 사용률", 2),      # 새 사건
    (600, "A-8", "web03", "디스크 사용률", 0),      # 해소
    (3000, "A-9", "web04", "CPU 사용률", 3),        # 창 밖 — 새 사건(sev3)
    (3100, "A-10", "web01", "디스크 사용률", 2),    # web01 호스트 창(30분) 밖 — 새 사건
]


async def _run_live(clock: _Clock, tmp_path: Path, mode: str):  # noqa: ANN202
    cfg = _cfg(mode)
    store = _ClockStore(str(tmp_path / f"d-{mode}.jsonl"), clock)
    w = AlarmWorker(cfg)
    w._decision_store = store
    w._page_registry = w._build_page_registry()
    w._graph = g = _GateGraph(cfg, store)
    t0 = clock.t
    for n, (dt, aid, host, name, sev) in enumerate(_SEQUENCE, start=1):
        clock.t = t0 + dt
        await _feed(w, _polestar_rec(aid, severity=sev, host=host, name=name), n)
    return cfg, store, w, g


# ═════════════════════════════════════════════════════════════════════════════
# A. 실시간 ↔ 복원 동등성
# ═════════════════════════════════════════════════════════════════════════════


class TestLiveVsRestore:
    @pytest.mark.parametrize("mode", ["shadow", "enforce"])
    async def test_restore_reproduces_live_registry(self, clock, tmp_path, mode):
        cfg, store, live, g = await _run_live(clock, tmp_path, mode)
        now = clock.t

        # 실시간 경로의 판정이 의도한 규칙을 밟았는지 먼저 고정한다(시퀀스가 공허하지 않게).
        rules = [
            (d.evidence or {}).get(EVIDENCE_KEY, {}).get("rule") if d is not None else None
            for d in g.decisions
        ]
        assert rules[1] == RULE_OPEN_REPEAT
        assert rules[2] == RULE_SAME_HOST
        assert rules[3] == RULE_FANOUT
        assert rules[5] == RULE_OPEN_REPEAT
        tiers = [d.tier for d in g.decisions]
        if mode == "enforce":
            assert [tiers[i] for i in (1, 2, 3, 5)] == [TIER_TICKET] * 4
            assert all(g.decisions[i].stage == STAGE_PAGE_REVIEW for i in (1, 2, 3, 5))
        else:
            assert [tiers[i] for i in (1, 2, 3, 5)] == [TIER_PAGE] * 4
            assert all(g.decisions[i].stage != STAGE_PAGE_REVIEW for i in (1, 2, 3, 5))

        restored = AlarmWorker(cfg)
        restored._decision_store = store
        restored._page_registry = restored._build_page_registry()
        restored._restore_page_registry(now)

        live._page_registry._sweep(now)  # 비교 시점 정리를 맞춘다
        assert _registry_state(restored._page_registry) == _registry_state(live._page_registry)

    @pytest.mark.parametrize("mode", ["shadow", "enforce"])
    async def test_restored_signals_match_live_for_every_key(self, clock, tmp_path, mode):
        cfg, store, live, _ = await _run_live(clock, tmp_path, mode)
        now = clock.t + 30
        reg2 = PageRegistry(
            host_window_minutes=30, fanout_window_minutes=10, reminder_hours=24,
            reopen_quiet_minutes=30,
        )
        reg2.restore(store.recent_decisions(PageRegistry.restore_window_seconds(24)), now - 30)
        for _, aid, host, name, _sev in _SEQUENCE:
            ev = make_event(server_name=host, hostname=host, alarm_name=name,
                            resource_name="cpu0", db_id=DB)
            fp = compute_fingerprint(ev)
            hk, fk = host_key_of(DB, host), fanout_key_of(DB, name)
            a = live._page_registry.signals_for(fp, hk, fk, now)
            b = reg2.signals_for(fp, hk, fk, now)
            # 비대표 동시발생 항목은 근거 수치(fanout_hosts)용이라 대표만 비교한다
            for s in (a, b):
                s["fanout_members"] = sorted(
                    (m["host"], m["alarm_id"]) for m in s["fanout_members"] if m["representative"]
                )
            assert a == b, aid


# ═════════════════════════════════════════════════════════════════════════════
# B. 해소 → 닫힘 · R1 재개
# ═════════════════════════════════════════════════════════════════════════════


class TestResolutionThroughGate:
    async def test_clear_event_reaches_gate_and_closes_then_r1_reopens(self, clock, tmp_path):
        cfg = _cfg("enforce")
        store = _ClockStore(str(tmp_path / "d.jsonl"), clock)
        w = AlarmWorker(cfg)
        w._decision_store = store
        w._page_registry = reg = w._build_page_registry()
        w._graph = g = _GateGraph(cfg, store)
        t0 = clock.t
        await _feed(w, _polestar_rec("P-1", severity=2, host="h1"), 1)
        fp = g.decisions[0].fingerprint
        hk = host_key_of(DB, "h1")
        assert reg._pages[fp].resolved_ts is None and fp in reg._by_host[hk]

        clock.t = t0 + 100
        await _feed(w, _polestar_rec("C-1", severity=0, host="h1"), 2)
        assert g.decisions[1] is not None  # 해소도 게이트까지 온다
        assert "page_review_signals" not in g.states[1]
        assert reg._pages[fp].resolved_ts == t0 + 100
        assert hk not in reg._by_host  # 닫힌 사건은 R2 대상이 아니다

        # 유예(30분) 안 재발 → R1 강등 + 사건 재개(resolved_ts 해제 · 호스트 연결 복구)
        clock.t = t0 + 400
        await _feed(w, _polestar_rec("P-2", severity=2, host="h1"), 3)
        d = g.decisions[2]
        assert d.tier == TIER_TICKET and d.evidence[EVIDENCE_KEY]["rule"] == RULE_OPEN_REPEAT
        assert reg._pages[fp].resolved_ts is None
        assert fp in reg._by_host[hk]
        assert reg._pages[fp].last_page_ts == t0  # 강등은 마지막 대표 PAGE 시각을 바꾸지 않는다
        assert reg._pages[fp].repeat_count == 1

        # 다시 해소 → 다시 닫힘
        clock.t = t0 + 500
        await _feed(w, _polestar_rec("C-2", severity=0, host="h1"), 4)
        assert reg._pages[fp].resolved_ts == t0 + 500

        # 유예 밖 재발 → 새 사건 첫 PAGE
        clock.t = t0 + 500 + 31 * 60
        await _feed(w, _polestar_rec("P-3", severity=2, host="h1"), 5)
        assert g.decisions[4].tier == TIER_PAGE
        assert g.decisions[4].evidence[EVIDENCE_KEY]["rule"] is None
        assert reg._pages[fp].first_page_ts == clock.t


# ═════════════════════════════════════════════════════════════════════════════
# C. observe 실패 — ACK·dead-letter
# ═════════════════════════════════════════════════════════════════════════════


class TestObserveFailureIsolated:
    async def test_observe_exception_does_not_block_ack(self, clock, tmp_path, monkeypatch):
        cfg = _cfg("enforce")
        store = _ClockStore(str(tmp_path / "d.jsonl"), clock)
        w = AlarmWorker(cfg)
        w._decision_store = store
        w._page_registry = w._build_page_registry()
        w._graph = g = _GateGraph(cfg, store)

        def _boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("레지스트리 고장")

        monkeypatch.setattr(w._page_registry, "observe", _boom)
        redis = _CountingRedis()
        await _feed(w, _polestar_rec("P-1", severity=2), 1, redis)
        assert g.decisions[0].tier == TIER_PAGE
        assert redis.acks == 1
        assert redis.xadds == []  # dead-letter 미적재
        assert len(store.path.read_text(encoding="utf-8").splitlines()) == 1


# ═════════════════════════════════════════════════════════════════════════════
# D. 앱 영향 승격 뒤 재판정 — 보존 ③
# ═════════════════════════════════════════════════════════════════════════════


class TestAppImpactOrdering:
    async def test_review_runs_after_app_impact_promotion_and_keeps(self, tmp_path):
        cfg = _cfg("enforce", app_impact_enabled=True, apm_healthy_demotion_shadow=False)
        event = make_event(severity=2)  # sev2 × 보통 = TICKET → 앱 영향 승격으로 PAGE
        now = 1_791_330_000.0
        reg = PageRegistry()
        hk = host_key_of(event.db_id, event.server_name or event.hostname)
        fk = fanout_key_of(event.db_id, event.alarm_name)
        first = decide_notification(
            event, None, None, make_ctx(importance_id="HIGH"),
            SimpleNamespace(suppress_max_severity=2, importance_value_map=IMP_MAP,
                            resolved_to_dashboard=False),
        )
        reg.observe(first, fingerprint=first.fingerprint, host_key=hk, fanout_key=fk,
                    alarm_id="P-0", now=now - 600)
        signals = dict(reg.signals_for(first.fingerprint, hk, fk, now))
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        state = {
            "alarm_event": event, "history_stats": None,
            "analysis_result": SimpleNamespace(error=None), "error": None,
            "noise_context": make_ctx(importance_id="MID"),
            "episode_app_impact": {
                "app_impact": {"fatal_events": 2, "event_types": ["ERROR_X"],
                               "was_signals": [], "source": "episode"},
                "window_events": 2,
            },
            "page_review_signals": signals,
        }
        out = await notification_gate_node(
            state, {"configurable": {"app_config": cfg, "decision_store": store}}
        )
        d = out["notification_decision"]
        assert d.tier == TIER_PAGE and d.stage == STAGE_MATRIX
        block = d.evidence[EVIDENCE_KEY]
        assert block["rule"] == RULE_OPEN_REPEAT and block["keep"] == KEEP_APP_IMPACT
        assert block["would_demote"] is False


# ═════════════════════════════════════════════════════════════════════════════
# E. 시각 단위
# ═════════════════════════════════════════════════════════════════════════════


class TestTimeUnits:
    def test_record_epoch_is_offset_independent(self):
        at = 1_791_330_000.0
        utc = datetime.fromtimestamp(at, tz=UTC).isoformat()
        kst = datetime.fromtimestamp(at, tz=timezone(timedelta(hours=9))).isoformat()
        assert _record_epoch(utc) == _record_epoch(kst) == at

    def test_restore_from_kst_records_equals_utc_records(self):
        at = 1_791_330_000.0
        base = {"tier": "page", "stage": "matrix", "fingerprint": "fp-a",
                "signals": {"severity": 2, "effective_severity": 2},
                "db_id": DB, "server_name": "h1", "alarm_name": "N", "alarm_id": "P-1"}
        kst = timezone(timedelta(hours=9))
        r1, r2 = PageRegistry(), PageRegistry()
        r1.restore([{**base, "ts": datetime.fromtimestamp(at, tz=UTC).isoformat()}], at + 60)
        r2.restore([{**base, "ts": datetime.fromtimestamp(at, tz=kst).isoformat()}], at + 60)
        assert r1._pages["fp-a"].first_page_ts == r2._pages["fp-a"].first_page_ts == at

    def test_signals_and_registry_use_epoch_seconds(self):
        reg = PageRegistry()
        d = decide_notification(
            make_event(severity=2), None, None, make_ctx(importance_id="HIGH"),
            SimpleNamespace(suppress_max_severity=2, importance_value_map=IMP_MAP,
                            resolved_to_dashboard=False),
        )
        hk, fk = host_key_of(DB, "h1"), fanout_key_of(DB, "N")
        reg.observe(d, fingerprint=d.fingerprint, host_key=hk, fanout_key=fk,
                    alarm_id="P", now=1000.0)
        sig = reg.signals_for(d.fingerprint, hk, fk, 1000.0 + 23 * 3600)
        assert isinstance(sig["now"], float)
        assert isinstance(sig["fingerprint_page"]["last_page_ts"], float)
        # 23시간 < 리마인더 24시간 → R1 강등(시 단위 오해 시 리마인더로 빠진다)
        out = review_page(d, sig, SimpleNamespace(**_review_ns("enforce")))
        assert out.tier == TIER_TICKET


# ═════════════════════════════════════════════════════════════════════════════
# F. 제니퍼 정책 경로 지문 일치
# ═════════════════════════════════════════════════════════════════════════════


class TestApmFingerprint:
    @pytest.mark.parametrize("norm", ["SERVICE_QUEUING", ""])
    def test_worker_fingerprint_matches_decision_fingerprint(self, norm):
        ev = make_event(severity=2, alarm_name="ERROR_SERVICE_QUEUING")
        worker_fp = compute_fingerprint(ev, alarm_key=norm or None)  # alarm_worker 지문 규칙
        d = decide_notification(
            ev, None, None, make_ctx(importance_id="HIGH"),
            SimpleNamespace(suppress_max_severity=2, importance_value_map=IMP_MAP,
                            resolved_to_dashboard=False),
            apm_policy={"event_type_norm": norm, "policy_row": None, "persistence": None},
        )
        assert d.fingerprint == worker_fp


# ═════════════════════════════════════════════════════════════════════════════
# G. 관제 집계 불변(shadow)
# ═════════════════════════════════════════════════════════════════════════════


class TestShadowDoesNotChangeAggregates:
    async def test_aggregate_and_funnel_equal_off_vs_shadow(self, clock, tmp_path):
        t0 = clock.t
        _, shadow_store, _, _ = await _run_live(clock, tmp_path, "shadow")
        clock.t = t0
        _, off_store, _, _ = await _run_live(clock, tmp_path, "off")
        sh, off = shadow_store.aggregate(), off_store.aggregate()
        for key in ("total", "by_tier", "page_count", "ticket_count"):
            assert sh[key] == off[key], key
        f_sh, f_off = shadow_store.funnel(), off_store.funnel()
        assert f_sh["tiers"] == f_off["tiers"]
        assert [s["terminated"] for s in f_sh["stages"]] == [
            s["terminated"] for s in f_off["stages"]
        ]
        # off 기록에는 재판정 블록 자체가 없다(비트 동일)
        for line in off_store.path.read_text(encoding="utf-8").splitlines():
            assert EVIDENCE_KEY not in (json.loads(line).get("stage_evidence") or {})


# ═════════════════════════════════════════════════════════════════════════════
# H. 설정
# ═════════════════════════════════════════════════════════════════════════════

_KEYS = {
    "page_review_mode": "off",
    "page_review_host_window_minutes": 30,
    "page_review_fanout_window_minutes": 10,
    "page_review_fanout_page_max": 1,
    "page_review_reminder_hours": 24,
    "page_review_reopen_quiet_minutes": 30,
    "page_review_always_page_csv": "",
    "page_review_idle_close_minutes": 0,
}


class TestConfig:
    def test_seven_fields_defaults_without_env(self, monkeypatch):
        for key in _KEYS:
            monkeypatch.delenv(f"NOISE_{key.upper()}", raising=False)
        cfg = NoiseGateConfig(_env_file=None)
        for key, default in _KEYS.items():
            assert getattr(cfg, key) == default, key
        assert cfg.page_review_always_page_globs == []
        fields = {f for f in NoiseGateConfig.model_fields if f.startswith("page_review_")}
        assert fields == set(_KEYS)
        assert not any("chronic" in f for f in NoiseGateConfig.model_fields)

    def test_env_example_has_seven_keys_no_inline_comment_no_chronic(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "PAGE_REVIEW_CHRONIC" not in text
        lines = [ln for ln in text.splitlines() if "NOISE_PAGE_REVIEW_" in ln and "=" in ln]
        keys = {ln.lstrip("# ").split("=", 1)[0] for ln in lines}
        assert keys == {f"NOISE_{k.upper()}" for k in _KEYS}
        for ln in lines:
            value = ln.lstrip("# ").split("=", 1)[1]
            assert "#" not in value, ln

    def test_registry_absent_when_gate_off_even_if_mode_on(self):
        cfg = _cfg("enforce")
        cfg.noise_gate.enable_noise_gate = False
        assert AlarmWorker(cfg)._build_page_registry() is None


# ═════════════════════════════════════════════════════════════════════════════
# I. 도메인 순수성
# ═════════════════════════════════════════════════════════════════════════════


class TestDomainPurity:
    def test_imports_are_stdlib_or_domain_only(self):
        src = (ROOT / "noise_gate/domain/page_review.py").read_text(encoding="utf-8")
        mods: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        for mod in mods:
            top = mod.split(".")[0]
            assert top == "__future__" or top in sys.stdlib_module_names or mod.startswith(
                "noise_gate.domain."
            ), mod
