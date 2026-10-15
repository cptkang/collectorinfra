"""plans/162 외부망 잔여 3건 독립 검증 — F4 무소식 닫힘 · replay `--page-review-mode` · W0-T.

실 LLM·Redis·외부 API 0. 구현 쪽 테스트(`test_page_review_idle_close.py` ·
`test_replay_noise_page_review.py` · `test_page_audit_w0t.py`)가 덮지 않는 경계만 본다.

    A. 도메인 — shadow·enforce 레지스트리 상태 동일 · 닫힌 항목 뒤 새 사건의 최초 id · touch 포함
       복원 = 실시간 · 음수 idle = 끔
    B. 설정 — `NOISE_PAGE_REVIEW_IDLE_CLOSE_MINUTES` 읽기 · 기본 0
    C. 워커 — 지문 dedup TTL(기본 4h) 안의 진짜 재발은 무소식 닫힘이어도 그래프에 가지 않는다
       (특성 고정 — 설정 도움말 예시와 어긋남 · 검증 보고 Medium) · 복원 reader 실패는 빈 상태
    D. replay — 재판정은 도메인 `review_page`·`PageRegistry`를 부른다(재구현 없음)
    E. W0-T — 새 집계 키 열거 · 간격 분포 경계 · idle 0·음수 시나리오 없음 · T1 고정 어휘 문제
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from src.config import NoiseGateConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.server_identity import is_apm_db_id
from noise_gate.domain.page_review import (
    PageRegistry,
    fanout_key_of,
    host_key_of,
    review_page,
)
from noise_gate.scripts import page_audit as pa
from noise_gate.scripts import replay_noise as rn
from noise_gate.scripts.page_audit_sanitize import OTHER
from noise_gate.tests.test_page_review import T0, _cfg, _page, _ReviewGraph, _worker_cfg
from noise_gate.tests.test_plan144_cross_source import _Clock, _jennifer_rec, _Redis

IDLE_MIN = 60
IDLE_S = IDLE_MIN * 60
DB = "jennifer"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _entries(reg: PageRegistry) -> dict[str, dict[str, Any]]:
    return {fp: dataclasses.asdict(e) for fp, e in reg._pages.items()}


def _live(reg: PageRegistry, cfg, steps, *, recs: list[dict] | None = None) -> list:
    """(dt, kind, fp, host) — kind page=신호→재판정→관측 · touch=지문 중복 재발."""
    out = []
    for dt, kind, fp, host in steps:
        now = T0 + dt
        hk, fk = host_key_of(DB, host), fanout_key_of(DB, "N")
        if kind == "touch":
            reg.touch(fp, now)
            if recs is not None:
                recs.append({"type": "recurrence", "fingerprint": fp, "count": 2,
                             "alarm_id": f"R-{int(dt)}", "ts": _iso(now)})
            continue
        d = review_page(_page(fp=fp), reg.signals_for(fp, hk, fk, now), cfg)
        reg.observe(d, fingerprint=fp, host_key=hk, fanout_key=fk, alarm_id=f"{fp}-{int(dt)}",
                    now=now, silent_source=True)
        out.append((d.tier, d.stage, ((d.evidence or {}).get("page_review") or {}).get("rule"),
                    ((d.evidence or {}).get("page_review") or {}).get("would_demote")))
        if recs is not None:
            recs.append({"ts": _iso(now), "tier": d.tier, "stage": d.stage, "fingerprint": fp,
                         "signals": d.signals, "db_id": DB, "server_name": host,
                         "alarm_name": "N", "alarm_id": f"{fp}-{int(dt)}",
                         "stage_evidence": d.evidence})
    return out


_STEPS = [
    (0, "page", "fp-a", "h1"),
    (600, "page", "fp-a", "h1"),            # R1
    (900, "page", "fp-b", "h1"),            # R2(같은 호스트)
    (1500, "touch", "fp-a", "h1"),          # 지문 중복 재발 — fp-a 닫힘을 미룬다
    (1500 + IDLE_S - 10, "page", "fp-a", "h1"),   # 아직 열림 — R1
    (1500 + 3 * IDLE_S, "touch", "fp-a", "h1"),   # 닫힌 뒤 touch — 되살리지 않는다
    (1500 + 3 * IDLE_S + 60, "page", "fp-a", "h1"),  # 새 사건
    (1500 + 3 * IDLE_S + 120, "page", "fp-a", "h1"),  # 새 사건의 R1
]


# ═════════════════════════════════════════════════════════════════════════════
# A. 도메인
# ═════════════════════════════════════════════════════════════════════════════


def test_shadow_and_enforce_leave_same_registry_state_with_idle_close() -> None:
    """shadow도 「했을 결과」로 갱신한다 — 무소식 닫힘이 켜져도 두 모드의
    레지스트리·했을 강등이 같다."""
    shadow, enforce = PageRegistry(idle_close_minutes=IDLE_MIN), PageRegistry(
        idle_close_minutes=IDLE_MIN)
    s_out = _live(shadow, _cfg("shadow"), _STEPS)
    e_out = _live(enforce, _cfg("enforce"), _STEPS)
    assert [(r, w) for _, _, r, w in s_out] == [(r, w) for _, _, r, w in e_out]
    assert _entries(shadow) == _entries(enforce)
    assert [r for _, _, r, _ in e_out] == [None, "R1", "R2", "R1", None, "R1"]


def test_closed_entry_is_replaced_by_fresh_incident() -> None:
    """닫힌 뒤 다음 PAGE는 새 사건 — 최초 id·최초 시각·반복 수가 새로 잡히고
    touch로 되살지 않는다."""
    reg = PageRegistry(idle_close_minutes=IDLE_MIN)
    _live(reg, _cfg("enforce"), _STEPS[:6])
    old = reg._pages["fp-a"]
    assert old.alarm_id == "fp-a-0" and old.repeat_count == 3  # R1 2 + R2(fp-b) 1
    # 닫힌 뒤 touch는 마지막 관측 시각을 올리지 않는다
    assert old.last_seen_ts == T0 + 1500 + IDLE_S - 10
    _live(reg, _cfg("enforce"), _STEPS[6:7])
    new = reg._pages["fp-a"]
    t_new = T0 + 1500 + 3 * IDLE_S + 60
    assert (new.alarm_id, new.first_page_ts, new.repeat_count, new.silent) == (
        f"fp-a-{1500 + 3 * IDLE_S + 60}", t_new, 0, True)


def test_restore_with_recurrence_touches_matches_live() -> None:
    """판정·재발 레코드 복원 = 같은 기록의 실시간 관측(touch·닫힘·새 사건 포함)."""
    live = PageRegistry(idle_close_minutes=IDLE_MIN)
    recs: list[dict] = []
    _live(live, _cfg("enforce"), _STEPS, recs=recs)
    now = T0 + 1500 + 3 * IDLE_S + 600
    restored = PageRegistry(idle_close_minutes=IDLE_MIN)
    applied = restored.restore(recs, now, silent_source=lambda r: is_apm_db_id(r["db_id"]))
    assert applied == len(recs)
    for fp in ("fp-a", "fp-b"):
        hk, fk = host_key_of(DB, "h1"), fanout_key_of(DB, "N")
        assert restored.signals_for(fp, hk, fk, now) == live.signals_for(fp, hk, fk, now)
    assert _entries(restored) == _entries(live)


def test_negative_idle_is_off() -> None:
    """음수는 0(끔) — silent 항목도 종전 R1."""
    neg, off = PageRegistry(idle_close_minutes=-30), PageRegistry(idle_close_minutes=0)
    assert _live(neg, _cfg("enforce"), _STEPS) == _live(off, _cfg("enforce"), _STEPS)


# ═════════════════════════════════════════════════════════════════════════════
# B. 설정
# ═════════════════════════════════════════════════════════════════════════════


def test_config_reads_idle_close_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOISE_PAGE_REVIEW_IDLE_CLOSE_MINUTES", raising=False)
    assert NoiseGateConfig(_env_file=None).page_review_idle_close_minutes == 0
    monkeypatch.setenv("NOISE_PAGE_REVIEW_IDLE_CLOSE_MINUTES", "45")
    assert NoiseGateConfig(_env_file=None).page_review_idle_close_minutes == 45


# ═════════════════════════════════════════════════════════════════════════════
# C. 워커
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


async def _feed(w: AlarmWorker, rec: dict, n: int) -> None:
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


async def test_dedup_ttl_still_hides_real_recurrence_after_idle_close(clock: _Clock) -> None:
    """특성 고정 — 지문 dedup TTL(기본 4h · 마지막 통보 기준 고정창) 안의 재발은 무소식 닫힘이어도
    그래프에 가지 않는다. 그래서 닫힌 사건의 다음 PAGE는 TTL이 끝난 뒤의 재통보다.

    설정 도움말 예시(값 60 · 10:00 PAGE · 10:20 멈춤 · 11:30 재발 → PAGE)는 TTL < 무소식 시간일
    때만 맞다(검증 보고 Medium).
    """
    cfg = _worker_cfg("enforce")
    cfg.noise_gate.repeat_interval_seconds = 14400
    cfg.noise_gate.sev3_repeat_interval_seconds = 14400
    cfg.noise_gate.page_review_idle_close_minutes = IDLE_MIN
    w = AlarmWorker(cfg)
    w._graph = g = _ReviewGraph(cfg)
    w._page_registry = reg = w._build_page_registry()
    t0 = clock.t
    await _feed(w, _jennifer_rec("J-1"), 1)          # 10:00 PAGE
    clock.t = t0 + 20 * 60
    await _feed(w, _jennifer_rec("J-2"), 2)          # 10:20 반복 — dedup · touch
    clock.t = t0 + 90 * 60
    await _feed(w, _jennifer_rec("J-3"), 3)          # 11:30 진짜 재발 — dedup에 버려진다
    assert len(g.states) == 1
    fp = next(iter(reg._pages))
    hk = host_key_of("jennifer_cm_prd", "xsapp01")
    fk = fanout_key_of("jennifer_cm_prd", "SERVICE_QUEUING")
    assert reg.signals_for(fp, hk, fk, clock.t)["fingerprint_page"] is None  # 닫혔지만 통보 없음
    clock.t = t0 + 14400 + 1
    await _feed(w, _jennifer_rec("J-4"), 4)          # TTL 뒤 재통보 — 새 사건
    assert len(g.states) == 2
    assert g.states[-1]["page_review_signals"]["fingerprint_page"] is None


def test_restore_reader_without_recurrence_kwarg_starts_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """idle>0인데 reader가 `include_recurrence`를 모르면 빈 레지스트리 + 경고 1줄(침묵 아님)."""

    class _OldReader:
        enabled = True

        def recent_decisions(self, window_seconds):  # noqa: ANN001
            return [{"ts": _iso(T0), "tier": "page", "fingerprint": "fp", "db_id": DB}]

    cfg = _worker_cfg("enforce")
    cfg.noise_gate.page_review_idle_close_minutes = IDLE_MIN
    w = AlarmWorker(cfg)
    w._page_registry = w._build_page_registry()
    w._decision_store = _OldReader()
    with caplog.at_level(logging.WARNING):
        w._restore_page_registry(T0 + 10)
    assert w._page_registry is not None and len(w._page_registry) == 0
    assert any("복원 실패" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# D. replay
# ═════════════════════════════════════════════════════════════════════════════


def _rec(sec: int, db_id: str = "polestar_cm_gp", server: str = "fake-r1") -> dict:
    base = datetime(2026, 10, 15, 1, 0, 0, tzinfo=UTC)
    return {
        "ts": (base + timedelta(seconds=sec)).isoformat(), "alarm_id": f"R-{sec}",
        "tier": "page", "reason": "x", "priority": 1,
        "signals": {"severity": 3, "effective_severity": 3, "importance": "보통"},
        "stage": "severity3", "alarm_name": "FAKE_ALARM", "server_name": server,
        "db_id": db_id, "resource_name": "res-r1",
    }


def test_replay_calls_domain_review_and_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """§10.7 — replay 재판정은 도메인 함수 호출이다(행 수만큼 review_page · 레지스트리 1개)."""
    calls = {"review": 0, "registry": 0}
    real_review, real_registry = rn.review_page, rn.PageRegistry

    def _review(*a: Any, **k: Any):
        calls["review"] += 1
        return real_review(*a, **k)

    def _registry(*a: Any, **k: Any):
        calls["registry"] += 1
        return real_registry(*a, **k)

    monkeypatch.setattr(rn, "review_page", _review)
    monkeypatch.setattr(rn, "PageRegistry", _registry)
    lines = [_rec(0), _rec(60), _rec(120, server="fake-r2")]
    rows, _ = rn.replay(lines, page_review_mode="shadow")
    assert calls == {"review": 3, "registry": 1}
    assert [(r["page_review"] or {}).get("rule") for r in rows] == [None, "R1", "R3"]
    rows_off, _ = rn.replay(lines)
    assert calls == {"review": 3, "registry": 1}  # off는 부르지 않는다
    assert all("page_review" not in r for r in rows_off)


# ═════════════════════════════════════════════════════════════════════════════
# E. W0-T
# ═════════════════════════════════════════════════════════════════════════════

_W0T = Path(__file__).resolve().parents[1] / "testdata" / "page_audit" / "w0t"


def _audit(tmp_path: Path, recs: list[dict[str, Any]]) -> tuple[dict, list[dict], str]:
    from noise_gate.tests import test_page_audit_w0t as w0t

    result = w0t._run(tmp_path, stream=None, decisions=w0t._write(tmp_path, recs))
    assert result.exit_code == 0, result.leak
    return w0t._summary(result), w0t._rows(result), w0t._blob(result)


def test_new_aggregate_keys_are_enumerated_for_unregistered_db(tmp_path: Path) -> None:
    """S6·S9 출처별 키는 열거(폴스타·제니퍼·`<other>`)만 — 미등록 db_id 원문이 키로 새지 않는다."""
    from noise_gate.tests import test_page_audit_w0t as w0t

    recs = [w0t._dec(0, "fake-w0t-u1", "x", db="fakeunregdb77"),
            w0t._dec(60, "fake-w0t-u2", "y", db="jennifer_fakeunsrc9"),
            w0t._dec(120, "fake-w0t-u3", "z")]
    s, _, blob = _audit(tmp_path, recs)
    allowed = {pa.SOURCE_POLESTAR, pa.SOURCE_JENNIFER, OTHER}
    assert set(s["S6"]["by_source"]) <= allowed
    assert set(s["S6"]["clear_events_by_source_long"]) <= allowed
    assert set(s["S6"]["sources_without_clear_events"]) <= allowed
    assert set(s["S9"]["by_source"]) <= allowed
    assert "fakeunregdb77" not in blob and "fakeunsrc9" not in blob


def test_jennifer_intervals_skip_polestar_and_pre_window_recurrences() -> None:
    start = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)

    def _d(idx: int, minutes: int, fp: str, *, sev: int = 2) -> pa.Decision:
        return pa.Decision(idx, start + timedelta(minutes=minutes), {
            "fingerprint": fp, "tier": "ticket", "signals": {"severity": sev}})

    decisions = [_d(0, 0, "fj"), _d(1, 30, "fj"), _d(2, 10, "fp"), _d(3, 40, "fp"),
                 _d(4, 50, "fj", sev=0)]  # 해소는 간격에서 뺀다
    jennifer = {0: True, 1: True, 2: False, 3: False, 4: True}
    recurrences = [(start + timedelta(minutes=10), {"fingerprint": "fj"}),
                   (start - timedelta(minutes=10), {"fingerprint": "fj"}),  # 창 앞
                   (start + timedelta(minutes=20), {"fingerprint": "fp"})]  # 폴스타
    out = pa.jennifer_event_intervals(decisions, recurrences, jennifer=jennifer, start=start)
    assert (out["fingerprints"], out["events"], out["recurrences_included"], out["gaps"]) == (
        1, 3, 1, 2)
    assert out["max_s"] == 1200 and out["histogram"]["15~30분"] == 1


def test_run_scenarios_ignores_zero_and_negative_idle() -> None:
    out = pa.run_scenarios([], {}, idle_close_minutes=[0, -5, 30, 30])
    assert set(out) == {*pa.SCENARIOS, "R1~R3@idle=30m", "APM_SEV3_POLICY+R1~R3@idle=30m"}


def test_t1_event_type_kept_when_only_vocabulary_word_overlaps(tmp_path: Path) -> None:
    from noise_gate.tests import test_page_audit_w0t as w0t

    pol = w0t._dec(0, "fake-w0t-p1", "메모리 사용률")
    pol["resource_name"] = "Memory"
    apm = w0t._dec(60, "fake-w0t-j1", "OUT_OF_MEMORY", db="jennifer",
                   evidence={"apm_policy": {"event_type_norm": "OUT_OF_MEMORY"}})
    _, rows, _ = _audit(tmp_path, [pol, apm])
    got = next(r for r in rows if r["db_id"] == "jennifer")["evidence"]["apm_policy"]
    assert got["event_type_norm"] == "OUT_OF_MEMORY"


def test_snapshot_policy_table_is_count_only(tmp_path: Path) -> None:
    """정책 스냅샷 `apm_policy_table`은 적재 여부·키 수만(키 목록 원문 없음)."""
    from noise_gate.tests import test_page_audit_w0t as w0t

    result = w0t._run(tmp_path, stream=None, decisions=w0t._write(tmp_path, [
        w0t._dec(0, "fake-w0t-s1", "x")]))
    snap = w0t._snapshot(result)
    assert set(snap["apm_policy_table"]) == {"loaded", "type_keys"}
    assert isinstance(snap["apm_policy_table"]["type_keys"], int)
    assert _W0T.exists()
