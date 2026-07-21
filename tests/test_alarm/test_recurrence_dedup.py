"""Plan 60 E1 — 재발생 dedup 강화(count/last_seen 집계 관측성) 단위·통합 테스트.

검증 범위(§3·§11 E1 행):
    - `_is_duplicate_fingerprint`의 count/first_seen 집계 + 억제 판정 비트 동일.
    - TTL 비교가 **last_notified 기준**(고정창) — 지속 재발 알람이 TTL 경과 후 재통보되는지
      (last_seen 기준이면 슬라이딩 창으로 변질되어 영원히 재통보 안 되는 회귀 시나리오).
    - 재통보 시 직전 창 재발 메타(prev, count>1)를 반환.
    - `record_recurrence` 레코드가 `aggregate()` by_tier/total에서 제외(resolution 전례 동일).
    - 대표 알람(재통보 시) recurrence 노출: decision_store.record() 최상위 recurrence 필드 +
      build_workb_body 재발생 이력 1줄.
    - recurrence_audit_every_n 샘플링.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.application.nodes.alarm_notifier import build_workb_body
from src.alarm.domain.alarm import AlarmAnalysisResult, AlarmEvent
from src.alarm.domain.notification_policy import (
    NotificationDecision,
    TIER_PAGE,
    TIER_SUPPRESS,
)
from src.alarm.infrastructure.decision_store import DecisionStore
from tests.test_alarm_pattern import REF, make_event


def _worker(repeat: int = 100, sev3: int = 100, every_n: int = 1) -> AlarmWorker:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            repeat_interval_seconds=repeat,
            sev3_repeat_interval_seconds=sev3,
            recurrence_audit_every_n=every_n,
        )
    )
    return AlarmWorker(cfg)


class _CaptureStore:
    """decision_store 대역 — record_recurrence 호출 인자만 캡처."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_recurrence(self, *, fingerprint, count, first_seen_ts, alarm_id=""):
        self.calls.append(
            {
                "fingerprint": fingerprint,
                "count": count,
                "first_seen_ts": first_seen_ts,
                "alarm_id": alarm_id,
            }
        )


# ─── _is_duplicate_fingerprint 집계 + 판정 비트동일 ───────────────────────────

class TestAggregation:
    def test_count_and_first_seen_aggregate(self):
        w = _worker(repeat=100)
        fp = "fp-agg"
        is_dup, meta = w._is_duplicate_fingerprint(fp, now=1000.0, severity=2)
        assert is_dup is False and meta is None            # 최초
        is_dup, meta = w._is_duplicate_fingerprint(fp, now=1010.0, severity=2)
        assert is_dup is True                              # 억제
        assert meta["count"] == 2
        assert meta["first_seen"] == 1000.0                # 최초 발생 시각 보존
        assert meta["last_seen"] == 1010.0                 # 최근 목격 갱신
        assert meta["last_notified"] == 1000.0             # 통보 시각 고정(갱신 안 함)
        is_dup, meta = w._is_duplicate_fingerprint(fp, now=1020.0, severity=2)
        assert is_dup is True
        assert meta["count"] == 3
        assert meta["last_notified"] == 1000.0             # 여전히 고정창

    def test_suppression_verdict_bit_identical(self):
        # 최초 통보 후 TTL 이내 재발은 억제, TTL 경과 후 재통보 — 현행과 동일.
        w = _worker(repeat=100)
        fp = "fp-verdict"
        assert w._is_duplicate_fingerprint(fp, now=1000.0, severity=2)[0] is False
        assert w._is_duplicate_fingerprint(fp, now=1099.0, severity=2)[0] is True
        assert w._is_duplicate_fingerprint(fp, now=1100.0, severity=2)[0] is False


# ─── TTL 비교 기준 = last_notified(고정창, 슬라이딩 변질 방지) ─────────────────

class TestFixedWindowNotSliding:
    def test_persistent_recurrence_renotifies_after_ttl(self):
        """지속 재발 알람이 TTL 경과 후 실제로 재통보되는지.

        매 스텝(50s 간격) 억제(count 증가·last_seen 갱신)되어도, TTL(100s) 비교는
        고정된 last_notified(=1000) 기준이므로 t=1100에서 창이 만료되어 재통보된다.
        만약 TTL 비교를 last_seen(매번 갱신) 기준으로 하면 슬라이딩 창이 되어 이 알람은
        영원히 재통보되지 않는다(회귀). 그 회귀를 이 테스트가 고정한다.
        """
        w = _worker(repeat=100)
        fp = "fp-persist"
        assert w._is_duplicate_fingerprint(fp, now=1000.0, severity=2)[0] is False  # 통보
        assert w._is_duplicate_fingerprint(fp, now=1050.0, severity=2)[0] is True   # 억제(last_seen=1050)
        assert w._is_duplicate_fingerprint(fp, now=1090.0, severity=2)[0] is True   # 억제(last_seen=1090)
        # t=1100: now-last_notified(1000)=100 >= TTL(100) → 재통보(고정창 만료).
        # last_seen 기준이면 now-last_seen(1090)=10 < 100 → 여전히 억제(회귀).
        is_dup, prev = w._is_duplicate_fingerprint(fp, now=1100.0, severity=2)
        assert is_dup is False

    def test_renotify_returns_prev_window_meta(self):
        # 재통보(비중복) 시 직전 창의 count(>1)를 prev로 반환 — 대표 알람 표기용.
        w = _worker(repeat=100)
        fp = "fp-prev"
        w._is_duplicate_fingerprint(fp, now=1000.0, severity=2)   # 통보
        w._is_duplicate_fingerprint(fp, now=1030.0, severity=2)   # 억제 count=2
        w._is_duplicate_fingerprint(fp, now=1060.0, severity=2)   # 억제 count=3
        is_dup, prev = w._is_duplicate_fingerprint(fp, now=1200.0, severity=2)
        assert is_dup is False
        assert prev is not None
        assert prev["count"] == 3
        assert prev["first_seen"] == 1000.0
        # 리셋 후 새 창은 count=1
        is_dup, prev2 = w._is_duplicate_fingerprint(fp, now=1250.0, severity=2)
        assert is_dup is True and prev2["count"] == 2

    def test_first_notify_no_prev(self):
        # 직전 창이 없거나 count==1(억제 이력 없음)이면 prev는 None.
        w = _worker(repeat=100)
        fp = "fp-noprev"
        assert w._is_duplicate_fingerprint(fp, now=1000.0, severity=2) == (False, None)
        # TTL 경과 후 재통보이나 직전 창 count==1 → prev None
        is_dup, prev = w._is_duplicate_fingerprint(fp, now=1200.0, severity=2)
        assert is_dup is False and prev is None

    def test_expired_sweep_uses_last_seen(self):
        # 만료 sweep은 last_seen 기준 — 다른 핑거프린트가 last_seen+TTL 경과 시 제거.
        w = _worker(repeat=100)
        w._is_duplicate_fingerprint("fp-old", now=1000.0, severity=2)   # last_seen=1000
        # 다른 fp의 신규(비중복) 처리 시 sweep 발생 — now=1200이면 fp-old(1000) 만료
        w._is_duplicate_fingerprint("fp-new", now=1200.0, severity=2)
        assert "fp-old" not in w._gate_dedup
        assert "fp-new" in w._gate_dedup


# ─── record_recurrence 감사 + aggregate 제외 ──────────────────────────────────

class TestRecordRecurrenceAudit:
    def test_recurrence_excluded_from_aggregate(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        store.record(
            NotificationDecision(
                tier=TIER_PAGE, reason="r", priority=300, signals={}, fingerprint="fp"
            )
        )
        store.record_recurrence(fingerprint="fp", count=3, first_seen_ts=1000.0)
        store.record(
            NotificationDecision(
                tier=TIER_SUPPRESS, reason="r", priority=0, signals={}, fingerprint="fp"
            )
        )
        agg = store.aggregate()
        assert agg["total"] == 2                    # 결정 2건만(recurrence 제외)
        assert agg["page_count"] == 1
        assert agg["suppress_count"] == 1
        assert "recurrence" not in agg["by_tier"]

    def test_record_recurrence_noop_when_disabled(self, tmp_path):
        path = tmp_path / "d.jsonl"
        store = DecisionStore(str(path), enabled=False)
        store.record_recurrence(fingerprint="fp", count=2, first_seen_ts=1.0)
        assert not path.exists()

    def test_record_recurrence_fields(self, tmp_path):
        path = tmp_path / "d.jsonl"
        store = DecisionStore(str(path))
        store.record_recurrence(
            fingerprint="fp1", count=5, first_seen_ts=1000.0, alarm_id="A-1"
        )
        rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        assert rows[0]["type"] == "recurrence"
        assert rows[0]["count"] == 5
        assert rows[0]["first_seen_ts"] == 1000.0
        assert rows[0]["alarm_id"] == "A-1"

    def test_worker_record_recurrence_sampling(self):
        # recurrence_audit_every_n=2 → count가 짝수일 때만 적재.
        w = _worker(every_n=2)
        w._decision_store = _CaptureStore()
        ev = make_event(alarm_id="A-1")
        w._record_recurrence("fp", ev, {"count": 3, "first_seen": 1.0})  # 3%2 != 0 → skip
        w._record_recurrence("fp", ev, {"count": 4, "first_seen": 1.0})  # 4%2 == 0 → 적재
        assert len(w._decision_store.calls) == 1
        assert w._decision_store.calls[0]["count"] == 4

    def test_worker_record_recurrence_every_time(self):
        w = _worker(every_n=1)
        w._decision_store = _CaptureStore()
        ev = make_event(alarm_id="A-2")
        w._record_recurrence("fp", ev, {"count": 2, "first_seen": 1.0})
        w._record_recurrence("fp", ev, {"count": 3, "first_seen": 1.0})
        assert len(w._decision_store.calls) == 2

    def test_worker_record_recurrence_noop_without_store(self):
        # decision_store 미주입/메타 없음 → 예외 없이 no-op.
        w = _worker()
        assert w._decision_store is None
        ev = make_event()
        w._record_recurrence("fp", ev, {"count": 2, "first_seen": 1.0})  # store None
        w._decision_store = _CaptureStore()
        w._record_recurrence("fp", ev, None)                              # meta None
        assert w._decision_store.calls == []


# ─── 대표 알람 노출: decision_store.record 최상위 필드 + workb 본문 ──────────────

def _result(**kwargs) -> AlarmAnalysisResult:
    base = dict(
        alarm_event=make_event(),
        severity_label="경고",
        summary="요약",
        probable_cause="원인",
        recommended_action="조치",
        notification_channels=["workb"],
    )
    base.update(kwargs)
    return AlarmAnalysisResult(**base)


class TestRepresentativeExposure:
    def test_record_recurrence_top_level_field(self, tmp_path):
        path = tmp_path / "d.jsonl"
        store = DecisionStore(str(path))
        meta = {"count": 4, "first_seen": 1000.0, "last_seen": 1300.0, "last_notified": 1000.0}
        store.record(
            NotificationDecision(
                tier=TIER_PAGE, reason="r", priority=300,
                signals={"severity": 2}, fingerprint="fp",
            ),
            alarm_id="A-1",
            recurrence=meta,
        )
        rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        assert rows[0]["recurrence"] == meta          # 최상위 필드
        assert "recurrence" not in rows[0]["signals"]  # signals 스키마 미훼손

    def test_record_none_recurrence_omits_key(self, tmp_path):
        # recurrence=None(기본)이면 키 자체를 넣지 않음(현행 스냅샷 훼손 방지).
        path = tmp_path / "d.jsonl"
        store = DecisionStore(str(path))
        store.record(
            NotificationDecision(
                tier=TIER_PAGE, reason="r", priority=300, signals={}, fingerprint="fp"
            )
        )
        rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        assert "recurrence" not in rows[0]

    def test_workb_body_shows_recurrence_line(self):
        meta = {"count": 3, "first_seen": 1000.0}
        body = build_workb_body(_result(), None, recurrence=meta, repeat_interval_seconds=14400)
        assert "재발생 이력" in body
        assert "직전 4h 3회 재발 후 재통보" in body

    def test_workb_body_no_line_when_count_one(self):
        # count<=1(억제 이력 없음)이면 표기 생략.
        body = build_workb_body(_result(), None, recurrence={"count": 1}, repeat_interval_seconds=14400)
        assert "재발생 이력" not in body

    def test_workb_body_no_line_when_none(self):
        body = build_workb_body(_result(), None, recurrence=None)
        assert "재발생 이력" not in body
