"""TicketBatchQueue 일배치 요약 큐 단위 테스트 (Plan 52 §7 · Phase E3).

enqueue→JSONL 1줄·read_pending 복원·enabled=False no-op·graceful(잘못된 경로)·
TICKET 외 티어 무시·summarize 집계를 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.alarm.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from src.alarm.infrastructure.ticket_queue import TicketBatchQueue


def make_decision(tier: str = TIER_TICKET, *, fingerprint: str = "fp-1", **kwargs) -> NotificationDecision:
    base = dict(
        tier=tier,
        reason="TICKET 테스트",
        priority=210,
        signals={"severity": 2, "importance": "보통"},
        fingerprint=fingerprint,
    )
    base.update(kwargs)
    return NotificationDecision(**base)


def _read_lines(path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestEnqueue:
    def test_append_single_jsonl_line(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        queue.enqueue(make_decision(), alarm_id="A-1")

        rows = _read_lines(qpath)
        assert len(rows) == 1
        rec = rows[0]
        assert rec["alarm_id"] == "A-1"
        assert rec["fingerprint"] == "fp-1"
        assert rec["reason"] == "TICKET 테스트"
        assert rec["priority"] == 210
        assert rec["signals"]["severity"] == 2
        assert datetime.fromisoformat(rec["ts"])  # ISO8601 파싱 가능

    def test_read_pending_restores_records(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        queue.enqueue(make_decision(fingerprint="fp-a"), alarm_id="A-1")
        queue.enqueue(make_decision(fingerprint="fp-b"), alarm_id="A-2")

        pending = queue.read_pending()
        assert len(pending) == 2
        assert [r["alarm_id"] for r in pending] == ["A-1", "A-2"]
        assert {r["fingerprint"] for r in pending} == {"fp-a", "fp-b"}

    def test_creates_parent_directories(self, tmp_path):
        qpath = tmp_path / "deep" / "nested" / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        queue.enqueue(make_decision())
        assert qpath.exists()

    def test_custom_ts_recorded(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        ts = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone.utc)
        queue.enqueue(make_decision(), ts=ts)
        assert _read_lines(qpath)[0]["ts"] == ts.isoformat()


class TestOnlyTicketTier:
    def test_non_ticket_tiers_ignored(self, tmp_path):
        # TICKET 외 티어(PAGE/DASHBOARD/SUPPRESS)는 일배치 큐 대상이 아니다
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        queue.enqueue(make_decision(tier=TIER_PAGE))
        queue.enqueue(make_decision(tier=TIER_DASHBOARD))
        queue.enqueue(make_decision(tier=TIER_SUPPRESS))
        # 어떤 TICKET도 적재되지 않았으므로 파일이 생성되지 않는다
        assert not qpath.exists()
        assert queue.read_pending() == []

    def test_only_ticket_persisted_in_mixed_stream(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        queue.enqueue(make_decision(tier=TIER_PAGE))
        queue.enqueue(make_decision(tier=TIER_TICKET, fingerprint="fp-t"))
        queue.enqueue(make_decision(tier=TIER_SUPPRESS))

        rows = _read_lines(qpath)
        assert len(rows) == 1
        assert rows[0]["fingerprint"] == "fp-t"


class TestEnabledFlag:
    def test_disabled_is_noop(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath), enabled=False)
        queue.enqueue(make_decision())
        assert not qpath.exists()
        assert queue.read_pending() == []
        assert queue.summarize() == {"total": 0, "by_fingerprint": {}}


class TestGracefulFailure:
    def test_enqueue_failure_does_not_raise(self, tmp_path):
        # 경로가 디렉토리면 파일 open 실패 → 예외 전파 없이 graceful(발송 차단 금지)
        bad_path = tmp_path / "imadir"
        bad_path.mkdir()
        queue = TicketBatchQueue(str(bad_path))
        queue.enqueue(make_decision())  # 예외가 나지 않아야 한다

    def test_read_pending_missing_file_empty(self, tmp_path):
        queue = TicketBatchQueue(str(tmp_path / "missing.jsonl"))
        assert queue.read_pending() == []


class TestSummarize:
    def test_counts_by_fingerprint(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        queue.enqueue(make_decision(fingerprint="fp-a"))
        queue.enqueue(make_decision(fingerprint="fp-a"))
        queue.enqueue(make_decision(fingerprint="fp-b"))

        summary = queue.summarize()
        assert summary["total"] == 3
        assert summary["by_fingerprint"]["fp-a"] == 2
        assert summary["by_fingerprint"]["fp-b"] == 1

    def test_window_filters_old(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        now = datetime.now(timezone.utc)
        queue.enqueue(make_decision(fingerprint="old"), ts=now - timedelta(hours=2))
        queue.enqueue(make_decision(fingerprint="new"), ts=now)

        summary = queue.summarize(window_seconds=3600)  # 최근 1시간
        assert summary["total"] == 1
        assert summary["by_fingerprint"] == {"new": 1}

    def test_corrupted_line_skipped(self, tmp_path):
        qpath = tmp_path / "ticket.jsonl"
        queue = TicketBatchQueue(str(qpath))
        queue.enqueue(make_decision(fingerprint="fp-ok"))
        with qpath.open("a", encoding="utf-8") as fh:
            fh.write("not-json\n")

        assert queue.summarize()["total"] == 1
