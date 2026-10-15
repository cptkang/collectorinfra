"""plans/162 F4 — 해소 이벤트가 오지 않는 출처의 「무소식 닫힘」(D-330 R1 해소 대용 규칙).

    A. 도메인 `PageRegistry` — idle 0 비트 동일 · 무소식 초과 뒤 재발은 새 사건 ·
       idle 안 재발은 R1 · touch(지문 중복 재발)가 닫힘을 미룸 · 비-silent 무관 ·
       R2 host_pages 제외 · sweep 정리
    B. 복원 — 레코드 술어 · recurrence 레코드 touch · idle 0이면 종전과 같음
    C. 워커 배선 — 설정 전달 · `_observe_page` silent 전달 · dedup touch · 복원 술어
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.server_identity import is_apm_db_id, is_apm_source
from noise_gate.domain.notification_policy import (
    STAGE_PAGE_REVIEW,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_TICKET,
)
from noise_gate.domain.page_review import (
    RULE_OPEN_REPEAT,
    RULE_SAME_HOST,
    PageRegistry,
    fanout_key_of,
    host_key_of,
    review_page,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.test_notification_policy import make_event
from noise_gate.tests.test_page_review import (
    DB,
    T0,
    _block,
    _cfg,
    _page,
    _ReviewGraph,
    _worker_cfg,
)
from noise_gate.tests.test_plan144_cross_source import (
    _Clock,
    _jennifer_rec,
    _polestar_rec,
    _Redis,
)

IDLE_MIN = 60
IDLE_S = IDLE_MIN * 60


def _keys(host: str = "web01", name: str = "CPU 사용률") -> tuple:
    return host_key_of(DB, host), fanout_key_of(DB, name)


def _step(reg: PageRegistry, cfg, decision, *, now: float, silent: bool,
          host: str = "web01", name: str = "CPU 사용률", alarm_id: str = ""):
    """신호 → 재판정 → 관측(silent 전달)을 워커·게이트 순서대로 한 번 돈다."""
    hk, fk = _keys(host, name)
    signals = reg.signals_for(decision.fingerprint, hk, fk, now)
    out = review_page(decision, signals, cfg)
    reg.observe(out, fingerprint=decision.fingerprint, host_key=hk, fanout_key=fk,
                alarm_id=alarm_id or f"A-{int(now)}", now=now, silent_source=silent)
    return out


def _summary(d) -> tuple:
    block = (d.evidence or {}).get("page_review") or {}
    return (d.tier, d.stage, d.reason, block.get("rule"), block.get("keep"),
            block.get("repeat_count"))


# 같은 지문 반복 · 무소식 긴 간격 · 같은 호스트 다른 지문 · 해소를 섞은 시퀀스
_SEQUENCE: list[tuple[float, str, str, int]] = [
    (0, "fp-a", "web01", 2),
    (300, "fp-a", "web01", 2),
    (600, "fp-b", "web01", 2),
    (5 * 3600, "fp-a", "web01", 2),
    (5 * 3600 + 60, "fp-b", "web01", 3),
    (9 * 3600, "fp-c", "web02", 2),
    (30 * 3600, "fp-a", "web01", 2),
]


def _run_sequence(reg: PageRegistry, *, silent: bool) -> list[tuple]:
    cfg = _cfg("enforce")
    out = []
    for dt, fp, host, eff in _SEQUENCE:
        d = _step(reg, cfg, _page(fp=fp, eff=eff), now=T0 + dt, silent=silent, host=host,
                  alarm_id=f"{fp}-{int(dt)}")
        out.append(_summary(d))
    return out


# ═════════════════════════════════════════════════════════════════════════════
# A. 도메인
# ═════════════════════════════════════════════════════════════════════════════


class TestBitIdentical:
    def test_idle_zero_is_same_as_before_even_for_silent(self):
        base = _run_sequence(PageRegistry(), silent=False)
        assert _run_sequence(PageRegistry(idle_close_minutes=0), silent=True) == base
        # 음수는 0 취급(끔)
        assert _run_sequence(PageRegistry(idle_close_minutes=-5), silent=True) == base

    def test_idle_on_but_non_silent_is_same_as_before(self):
        base = _run_sequence(PageRegistry(), silent=False)
        assert _run_sequence(PageRegistry(idle_close_minutes=IDLE_MIN), silent=False) == base

    def test_idle_on_silent_differs_only_after_idle_gap(self):
        base = _run_sequence(PageRegistry(), silent=False)
        got = _run_sequence(PageRegistry(idle_close_minutes=IDLE_MIN), silent=True)
        assert got[:3] == base[:3]
        # 5시간 무소식 뒤 fp-a 재발 — 종전은 R1 강등, 무소식 닫힘이면 새 사건 PAGE
        assert base[3][0] == TIER_TICKET and base[3][3] == RULE_OPEN_REPEAT
        assert got[3][0] == TIER_PAGE and got[3][3] is None

    def test_restore_idle_zero_ignores_recurrence(self):
        recs = [
            _rec("fp-a", at=T0 - 7200, db_id="jennifer_cm_prd"),
            {"type": "recurrence", "fingerprint": "fp-a", "count": 1,
             "ts": _iso(T0 - 60), "alarm_id": "R-1"},
        ]
        reg = PageRegistry()
        assert reg.restore(recs, T0, silent_source=lambda r: True) == 1
        assert reg.signals_for("fp-a", *_keys("h1", "N"), T0)["fingerprint_page"] is not None


class TestIdleClose:
    def _reg(self) -> PageRegistry:
        return PageRegistry(idle_close_minutes=IDLE_MIN)

    def test_silent_reoccurrence_after_idle_is_new_incident(self):
        reg, cfg = self._reg(), _cfg("enforce")
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        later = T0 + IDLE_S + 1
        sig = reg.signals_for("fp-a", *_keys(), later)
        assert sig["fingerprint_page"] is None
        d = _step(reg, cfg, _page(fp="fp-a"), now=later, silent=True, alarm_id="P-2")
        assert d.tier == TIER_PAGE and _block(d)["rule"] is None
        # 새 사건 — 최초 PAGE id·시각이 새 알람으로 바뀌고 반복 수 0
        fp_page = reg.signals_for("fp-a", *_keys(), later + 1)["fingerprint_page"]
        assert fp_page["alarm_id"] == "P-2" and fp_page["first_page_ts"] == later
        assert fp_page["repeat_count"] == 0

    def test_silent_reoccurrence_within_idle_is_r1(self):
        reg, cfg = self._reg(), _cfg("enforce")
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        d = _step(reg, cfg, _page(fp="fp-a"), now=T0 + IDLE_S, silent=True)
        assert d.tier == TIER_TICKET and d.stage == STAGE_PAGE_REVIEW
        assert _block(d)["rule"] == RULE_OPEN_REPEAT

    def test_every_observe_tier_extends_last_seen(self):
        """강등·그 밖 티어 관측도 마지막 관측 시각을 올린다 — 반복이 이어지면 닫히지 않는다."""
        reg, cfg = self._reg(), _cfg("enforce")
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        d = _step(reg, cfg, _page(fp="fp-a"), now=T0 + IDLE_S - 10, silent=True)
        assert d.tier == TIER_TICKET  # 강등 관측
        hk, fk = _keys()
        reg.observe(_page(fp="fp-a", tier=TIER_DASHBOARD), fingerprint="fp-a", host_key=hk,
                    fanout_key=fk, alarm_id="D-1", now=T0 + 2 * IDLE_S - 20, silent_source=True)
        d = _step(reg, cfg, _page(fp="fp-a"), now=T0 + 3 * IDLE_S - 30, silent=True)
        assert d.tier == TIER_TICKET and _block(d)["rule"] == RULE_OPEN_REPEAT

    def test_touch_defers_close(self):
        reg, cfg = self._reg(), _cfg("enforce")
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        reg.touch("fp-a", T0 + IDLE_S - 1)
        d = _step(reg, cfg, _page(fp="fp-a"), now=T0 + IDLE_S + 60, silent=True)
        assert _block(d)["rule"] == RULE_OPEN_REPEAT

    def test_touch_missing_or_closed_is_noop(self):
        reg, cfg = self._reg(), _cfg("enforce")
        reg.touch("fp-none", T0)
        assert len(reg) == 0
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        reg.touch("fp-a", T0 + IDLE_S + 1)  # 이미 닫힘 — 되살리지 않는다
        assert reg.signals_for("fp-a", *_keys(), T0 + IDLE_S + 2)["fingerprint_page"] is None

    def test_non_silent_ignores_idle(self):
        reg, cfg = self._reg(), _cfg("enforce")
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=False, alarm_id="P-1")
        d = _step(reg, cfg, _page(fp="fp-a"), now=T0 + 5 * IDLE_S, silent=False)
        assert _block(d)["rule"] == RULE_OPEN_REPEAT

    def test_host_pages_exclude_idle_closed(self):
        reg = PageRegistry(idle_close_minutes=IDLE_MIN, host_window_minutes=600)
        cfg = _cfg("enforce", page_review_host_window_minutes=600)
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        # 호스트 창 안이지만 무소식 닫힘 — R2 재료에서 빠진다
        sig = reg.signals_for("fp-b", *_keys(), T0 + IDLE_S + 1)
        assert sig["host_pages"] == []
        d = _step(reg, cfg, _page(fp="fp-b"), now=T0 + IDLE_S + 1, silent=True)
        assert d.tier == TIER_PAGE and _block(d)["rule"] is None
        # idle 안이면 R2
        reg2 = PageRegistry(idle_close_minutes=IDLE_MIN, host_window_minutes=600)
        _step(reg2, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        d2 = _step(reg2, cfg, _page(fp="fp-b"), now=T0 + IDLE_S - 1, silent=True)
        assert _block(d2)["rule"] == RULE_SAME_HOST

    def test_sweep_drops_idle_closed(self):
        reg, cfg = self._reg(), _cfg("enforce")
        _step(reg, cfg, _page(fp="fp-a"), now=T0, silent=True, alarm_id="P-1")
        _step(reg, cfg, _page(fp="fp-b"), now=T0, silent=False, alarm_id="P-2", host="web02",
              name="메모리")
        reg._sweep(T0 + IDLE_S + 1)
        assert len(reg) == 1 and "fp-b" in reg._pages


# ═════════════════════════════════════════════════════════════════════════════
# B. 복원
# ═════════════════════════════════════════════════════════════════════════════


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, tz=UTC).isoformat()


def _rec(fp: str, *, at: float, db_id: str, alarm_id: str = "P-1") -> dict:
    return {"ts": _iso(at), "tier": "page", "stage": "matrix", "fingerprint": fp,
            "signals": {"severity": 2, "effective_severity": 2}, "db_id": db_id,
            "server_name": "h1", "alarm_name": "N", "alarm_id": alarm_id}


class TestRestore:
    def test_predicate_marks_silent_and_sweep_closes(self):
        reg = PageRegistry(idle_close_minutes=IDLE_MIN)
        recs = [_rec("fp-j", at=T0 - 2 * IDLE_S, db_id="jennifer_cm_prd"),
                _rec("fp-p", at=T0 - 2 * IDLE_S, db_id=DB, alarm_id="P-2")]
        applied = reg.restore(recs, T0, silent_source=lambda r: is_apm_db_id(r["db_id"]))
        assert applied == 2
        assert set(reg._pages) == {"fp-p"}  # 제니퍼 지문은 무소식 닫힘으로 정리

    def test_recurrence_record_touches(self):
        reg = PageRegistry(idle_close_minutes=IDLE_MIN)
        recs = [
            _rec("fp-j", at=T0 - 2 * IDLE_S, db_id="jennifer"),
            # 무소식 시간 안마다 지문 중복 재발 — 닫힘이 이어서 미뤄진다
            {"type": "recurrence", "fingerprint": "fp-j", "count": 3,
             "ts": _iso(T0 - 2 * IDLE_S + 3000), "alarm_id": "R-1"},
            {"type": "recurrence", "fingerprint": "fp-j", "count": 4,
             "ts": _iso(T0 - 2 * IDLE_S + 6000), "alarm_id": "R-1"},
            {"type": "resolution", "fingerprint": "fp-j", "ts": _iso(T0 - 300)},  # 그대로 무시
            {"type": "recurrence", "fingerprint": "fp-x", "count": 1,
             "ts": _iso(T0 - 300), "alarm_id": "R-2"},  # 항목 없음 — 무시
        ]
        applied = reg.restore(recs, T0, silent_source=lambda r: is_apm_db_id(r.get("db_id", "")))
        assert applied == 4
        fp_page = reg.signals_for("fp-j", *_keys("h1", "N"), T0)["fingerprint_page"]
        assert fp_page is not None and fp_page["alarm_id"] == "P-1"

    def test_restore_matches_live(self):
        """복원 결과가 같은 기록을 실시간으로 관측한 결과와 같다(무소식 닫힘 포함)."""
        cfg = _cfg("enforce")
        live = PageRegistry(idle_close_minutes=IDLE_MIN)
        hk, fk = host_key_of("jennifer", "h1"), fanout_key_of("jennifer", "N")
        recs = []
        for i, dt in enumerate((-5 * IDLE_S, -5 * IDLE_S + 600, -2 * IDLE_S)):
            now = T0 + dt
            d = review_page(_page(fp="fp-j"), live.signals_for("fp-j", hk, fk, now), cfg)
            live.observe(d, fingerprint="fp-j", host_key=hk, fanout_key=fk,
                         alarm_id=f"P-{i}", now=now, silent_source=True)
            recs.append({"ts": _iso(now), "tier": d.tier, "stage": d.stage,
                         "fingerprint": "fp-j", "signals": d.signals, "db_id": "jennifer",
                         "server_name": "h1", "alarm_name": "N", "alarm_id": f"P-{i}",
                         "stage_evidence": d.evidence})
        restored = PageRegistry(idle_close_minutes=IDLE_MIN)
        restored.restore(recs, T0, silent_source=lambda r: is_apm_db_id(r["db_id"]))
        assert restored.signals_for("fp-j", hk, fk, T0) == live.signals_for("fp-j", hk, fk, T0)
        # 세 번째(-2h)는 무소식 닫힘 뒤라 새 사건이다
        assert live.signals_for("fp-j", hk, fk, T0)["fingerprint_page"] is None


# ═════════════════════════════════════════════════════════════════════════════
# C. 워커 배선
# ═════════════════════════════════════════════════════════════════════════════


def _idle_worker_cfg(idle: int = IDLE_MIN) -> SimpleNamespace:
    cfg = _worker_cfg("enforce")
    cfg.noise_gate.page_review_idle_close_minutes = idle
    return cfg


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


async def _feed(w: AlarmWorker, rec: dict, n: int) -> None:
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


class TestWorker:
    def test_build_passes_idle_setting(self):
        reg = AlarmWorker(_idle_worker_cfg(45))._build_page_registry()
        assert reg is not None and reg._idle_close_s == 45 * 60
        # 설정 필드가 없으면 0(끔)
        assert AlarmWorker(_worker_cfg("enforce"))._build_page_registry()._idle_close_s == 0

    def test_observe_page_passes_silent_source(self):
        w = AlarmWorker(_idle_worker_cfg())
        w._page_registry = w._build_page_registry()
        for db_id, fp in (("jennifer_cm_prd", "fp-j"), (DB, "fp-p")):
            ev = make_event(db_id=db_id, alarm_id=f"A-{fp}")
            w._observe_page(ev, fp, {"notification_decision": _page(fp=fp)}, T0)
        assert w._page_registry._pages["fp-j"].silent is True
        assert w._page_registry._pages["fp-p"].silent is False
        assert is_apm_source(make_event(db_id="jennifer")) is True

    async def test_dedup_recurrence_touches_registry(self, clock):
        cfg = _idle_worker_cfg()
        w = AlarmWorker(cfg)
        w._graph = g = _ReviewGraph(cfg)
        w._page_registry = reg = w._build_page_registry()
        t0 = clock.t
        await _feed(w, _jennifer_rec("J-1"), 1)
        assert g.states[0]["alarm_event"].alarm_id == "J-1"
        fp = next(iter(reg._pages))
        assert reg._pages[fp].silent is True and reg._pages[fp].last_seen_ts == t0
        clock.t = t0 + 30  # 지문 dedup 간격(60초) 안 — 그래프에 안 가고 touch만
        await _feed(w, _jennifer_rec("J-2"), 2)
        assert len(g.states) == 1
        assert reg._pages[fp].last_seen_ts == t0 + 30
        # touch 덕에 첫 관측 기준으로는 닫혔을 시각에도 열린 사건이다
        clock.t = t0 + IDLE_S + 20
        await _feed(w, _jennifer_rec("J-3"), 3)
        assert g.states[-1]["page_review_signals"]["fingerprint_page"]["alarm_id"] == "J-1"

    async def test_jennifer_after_idle_is_new_incident(self, clock):
        cfg = _idle_worker_cfg()
        w = AlarmWorker(cfg)
        w._graph = g = _ReviewGraph(cfg)
        w._page_registry = w._build_page_registry()
        await _feed(w, _jennifer_rec("J-1"), 1)
        clock.t += IDLE_S + 1
        await _feed(w, _jennifer_rec("J-2"), 2)
        assert g.states[-1]["page_review_signals"]["fingerprint_page"] is None
        # 폴스타는 해소 이벤트가 오므로 무소식 닫힘 대상이 아니다
        await _feed(w, _polestar_rec("P-1"), 3)
        clock.t += IDLE_S + 1
        await _feed(w, _polestar_rec("P-2"), 4)
        assert g.states[-1]["page_review_signals"]["fingerprint_page"]["alarm_id"] == "P-1"

    def test_dedup_without_registry_is_untouched(self):
        w = AlarmWorker(_worker_cfg("off"))
        assert w._build_page_registry() is None

    def test_restore_uses_db_id_predicate(self, tmp_path):
        import time as _time

        now = _time.time()
        w = AlarmWorker(_idle_worker_cfg())
        w._page_registry = w._build_page_registry()
        w._decision_store = store = DecisionStore(str(tmp_path / "d.jsonl"))
        for fp, db_id in (("fp-j", "jennifer_cm_prd"), ("fp-p", DB)):
            store.record(_page(fp=fp), alarm_id=f"A-{fp}", alarm_name="N", server_name="h1",
                         ts=datetime.fromtimestamp(now - 2 * IDLE_S, tz=UTC), db_id=db_id)
        w._restore_page_registry(now)
        assert set(w._page_registry._pages) == {"fp-p"}
        # 끔이면 둘 다 복원(종전과 같음)
        w2 = AlarmWorker(_idle_worker_cfg(0))
        w2._page_registry = w2._build_page_registry()
        w2._decision_store = store
        w2._restore_page_registry(now)
        assert set(w2._page_registry._pages) == {"fp-j", "fp-p"}


class TestStoreRecurrenceRestore:
    def _store(self, tmp_path, now: float) -> DecisionStore:
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        store.record(_page(fp="fp-j"), alarm_id="J-1", alarm_name="N", server_name="h1",
                     ts=datetime.fromtimestamp(now - 2 * IDLE_S, tz=UTC), db_id="jennifer")
        # 무소식 시간 안마다 지문 중복 재발 — 실제 record_recurrence 경로(ts는 UTC ISO)
        for i, dt in enumerate((-2 * IDLE_S + 3000, -2 * IDLE_S + 6000)):
            store.record_recurrence(fingerprint="fp-j", count=i + 1, alarm_id=f"J-R{i}",
                                    ts=datetime.fromtimestamp(now + dt, tz=UTC))
        store.record_resolution(fingerprint="fp-x", duration_seconds=1.0)  # 계속 제외
        return store

    def test_default_call_is_unchanged(self, tmp_path):
        import time as _time

        now = _time.time()
        store = self._store(tmp_path, now)
        window = PageRegistry.restore_window_seconds(24)
        records = store.recent_decisions(window)
        assert [r["alarm_id"] for r in records] == ["J-1"]
        assert store.recent_decisions(window, include_recurrence=False) == records
        both = store.recent_decisions(window, include_recurrence=True)
        assert [r.get("type") for r in both] == [None, "recurrence", "recurrence"]

    def test_recurrence_window_uses_record_ts(self, tmp_path):
        import time as _time

        now = _time.time()
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        store.record_recurrence(fingerprint="fp-j", count=1, alarm_id="old",
                                ts=datetime.fromtimestamp(now - 3 * 86400, tz=UTC))
        store.record_recurrence(fingerprint="fp-j", count=2, alarm_id="new",
                                ts=datetime.fromtimestamp(now - 60, tz=UTC))
        got = store.recent_decisions(86400, include_recurrence=True)
        assert [r["alarm_id"] for r in got] == ["new"]

    def test_worker_restore_applies_recurrence_touch(self, tmp_path):
        import time as _time

        now = _time.time()
        store = self._store(tmp_path, now)
        w = AlarmWorker(_idle_worker_cfg())
        w._page_registry = w._build_page_registry()
        w._decision_store = store
        w._restore_page_registry(now)
        # 재발 touch 덕에 마지막 관측이 now-1200 — 무소식 시간(60분) 안이라 열린 사건
        assert set(w._page_registry._pages) == {"fp-j"}
        entry = w._page_registry._pages["fp-j"]
        assert entry.silent is True
        assert entry.last_seen_ts == pytest.approx(now - 2 * IDLE_S + 6000, abs=1e-3)

    def test_worker_restore_idle_off_reads_without_recurrence(self):
        calls: list[dict] = []

        class _Reader:
            enabled = True

            def recent_decisions(self, window_seconds, **kw):
                calls.append(kw)
                return []

        for idle, expected in ((0, {}), (IDLE_MIN, {"include_recurrence": True})):
            w = AlarmWorker(_idle_worker_cfg(idle))
            w._page_registry = w._build_page_registry()
            w._decision_store = _Reader()
            w._restore_page_registry(T0)
            assert calls[-1] == expected


class TestCapRecency:
    def _fill(self, reg: PageRegistry) -> None:
        cfg = _cfg("enforce")
        # 서로 다른 호스트·알람명 — R2·R3 없이 각자 대표 PAGE
        _step(reg, cfg, _page(fp="fp-old"), now=T0, silent=True, host="h1", name="N1")
        _step(reg, cfg, _page(fp="fp-mid"), now=T0 + 10, silent=True, host="h2", name="N2")
        reg.touch("fp-old", T0 + 20)  # 가장 먼저 PAGE됐지만 가장 최근에 봤다
        _step(reg, cfg, _page(fp="fp-new"), now=T0 + 30, silent=True, host="h3", name="N3")

    def test_idle_on_evicts_by_last_seen(self):
        reg = PageRegistry(idle_close_minutes=IDLE_MIN, max_keys=2)
        self._fill(reg)
        assert set(reg._pages) == {"fp-old", "fp-new"}

    def test_idle_off_keeps_last_page_order(self):
        reg = PageRegistry(max_keys=2)
        self._fill(reg)
        assert set(reg._pages) == {"fp-mid", "fp-new"}
