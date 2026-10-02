"""TICKET 일배치 요약 발송·큐 정리 테스트 (결함 ⑬ · D-048.9 「일배치 요약」의 소비 측).

재현: 큐(`ticket_queue.py`)는 적재만 하고 읽어 보내는 호출부가 없어 요약이 한 번도 나가지 않고
큐 파일이 계속 커졌다. 아래를 고정한다.

  1. 쌓인 TICKET을 요약 1통으로 보내고, 보낸 항목은 큐에서 지운다(다시 보내지 않는다).
  2. 보내는 도중 새로 쌓인 항목은 남는다(다음 요약 대상).
  3. 발송 실패·worKB 미설정(D-243)은 항목을 남기고 로그로 드러낸다 — 예외는 올라가지 않는다.
  4. 보존 한도(`trim`)는 오래된 줄부터 지우고 경고를 남긴다. 0이면 정리하지 않는다.
  5. 주기 루프는 기동 시 정리 → 지정 시각마다 (켜져 있으면) 발송 → 정리 순서로 돈다.
  6. 워커 `run()`이 큐가 있을 때만 루프를 띄우고, 종료 시 취소한다(호출부 배선 — D-083 교훈).

외부 발송은 전부 대역이다(네트워크 호출 0건).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

import noise_gate.application.alarm_worker as worker_mod
import noise_gate.orchestration.ticket_summary as summary_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_notifier import alarm_notifier_node
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import TIER_TICKET, NotificationDecision
from noise_gate.infrastructure.ticket_queue import TicketBatchQueue
from noise_gate.orchestration.ticket_summary import (
    build_ticket_summary,
    run_ticket_summary_loop,
    seconds_until_hour,
    send_ticket_summary_once,
)


def _decision(fingerprint: str = "fp-1") -> NotificationDecision:
    return NotificationDecision(
        tier=TIER_TICKET, reason="매트릭스: 경고 × 보통", priority=222,
        signals={"severity": 2}, fingerprint=fingerprint,
    )


def _workb(base_url: str = "http://workb.test") -> SimpleNamespace:
    return SimpleNamespace(
        base_url=base_url, bearer_token="t", system_div="S", send_id="0001",
        user_ids_csv="1001,1002", alias="[인프라알람]", timeout_seconds=1,
    )


class _Sent:
    """`_send_workb_summary` 대역 — 보낸 (제목, 본문)을 모은다."""

    def __init__(self, side_effect=None) -> None:  # noqa: ANN001
        self.calls: list[tuple[str, str]] = []
        self.side_effect = side_effect

    async def __call__(self, workb_cfg, title: str, body: str) -> None:  # noqa: ANN001
        self.calls.append((title, body))
        if self.side_effect is not None:
            self.side_effect()


def _fill(queue: TicketBatchQueue) -> None:
    queue.enqueue(_decision("fp-a"), alarm_id="A-1", alarm_name="CPU High", server_name="srv-1")
    queue.enqueue(_decision("fp-a"), alarm_id="A-2", alarm_name="CPU High", server_name="srv-1")
    queue.enqueue(_decision("fp-b"), alarm_id="A-3", alarm_name="Disk Full", server_name="srv-2")


# ── 1·2. 발송 후 제거 · 도중 적재분 보존 ─────────────────────────────────


async def test_pending_tickets_are_sent_once_and_removed(tmp_path, monkeypatch):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    _fill(queue)
    sent = _Sent()
    monkeypatch.setattr(summary_mod, "_send_workb_summary", sent)

    assert await send_ticket_summary_once(queue, _workb()) is True
    assert len(sent.calls) == 1
    title, body = sent.calls[0]
    assert "3건" in title
    assert "CPU High" in body and "srv-1" in body and "2건" in body
    assert "Disk Full" in body and "srv-2" in body
    assert queue.read_pending() == []  # 보낸 항목은 큐에서 지워진다

    # 두 번째 호출은 보낼 것이 없다 — 같은 항목을 다시 보내지 않는다.
    assert await send_ticket_summary_once(queue, _workb()) is False
    assert len(sent.calls) == 1


async def test_items_enqueued_during_send_are_kept(tmp_path, monkeypatch):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    _fill(queue)
    sent = _Sent(side_effect=lambda: queue.enqueue(
        _decision("fp-c"), alarm_id="A-9", alarm_name="Mem High", server_name="srv-3",
    ))
    monkeypatch.setattr(summary_mod, "_send_workb_summary", sent)

    assert await send_ticket_summary_once(queue, _workb()) is True
    remaining = queue.read_pending()
    assert [r["alarm_id"] for r in remaining] == ["A-9"]


# ── 3. 실패는 항목을 남기고 드러낸다 ─────────────────────────────────────


async def test_send_failure_keeps_items_and_logs(tmp_path, monkeypatch, caplog):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    _fill(queue)

    def boom() -> None:
        raise RuntimeError("worKB 502")

    monkeypatch.setattr(summary_mod, "_send_workb_summary", _Sent(side_effect=boom))
    with caplog.at_level(logging.WARNING, logger=summary_mod.__name__):
        assert await send_ticket_summary_once(queue, _workb()) is False

    assert len(queue.read_pending()) == 3  # 다음 주기에 다시 보낸다
    failures = [r for r in caplog.records if "TICKET 요약 발송 실패" in r.getMessage()]
    assert failures and failures[0].exc_info is not None  # 삼키지 않고 원인을 남긴다


async def test_workb_unconfigured_skips_with_one_line_warning(tmp_path, caplog):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    _fill(queue)
    with caplog.at_level(logging.WARNING, logger=summary_mod.__name__):
        assert await send_ticket_summary_once(queue, _workb(base_url="")) is False

    assert len(queue.read_pending()) == 3
    skips = [r for r in caplog.records if "worKB 미설정" in r.getMessage()]
    assert len(skips) == 1 and skips[0].exc_info is None  # D-243: traceback 없는 한 줄


async def test_workb_payload_goes_to_default_recipients(monkeypatch):
    """전송 규약은 기존 worKB 쪽지와 같고, 수신자는 기본 수신자다(대역 전송 — 네트워크 0)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        summary_mod.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    await summary_mod._send_workb_summary(_workb(), "제목", "본문")

    assert seen["url"] == "http://workb.test/api/sendWorkbMsg"
    assert seen["auth"] == "Bearer t"
    assert seen["json"] == {
        "systemDiv": "S", "msgTitle": "제목", "msgBody": "본문",
        "sendId": "0001", "userIds": "1001,1002", "alias": "[인프라알람]",
    }


async def test_empty_queue_sends_nothing(tmp_path, monkeypatch):
    sent = _Sent()
    monkeypatch.setattr(summary_mod, "_send_workb_summary", sent)
    queue = TicketBatchQueue(str(tmp_path / "missing.jsonl"))
    assert await send_ticket_summary_once(queue, _workb()) is False
    assert sent.calls == []  # 빈 요약은 보내지 않는다


# ── 요약 본문 ─────────────────────────────────────────────────────────────


def test_summary_groups_escapes_and_falls_back_to_fingerprint():
    now = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
    records = [
        {"ts": now.isoformat(), "fingerprint": "fp-x",
         "alarm_name": "<b>X</b>", "server_name": "s"},
        {"ts": (now + timedelta(hours=1)).isoformat(), "fingerprint": "abcdef0123456789"},
    ]
    title, body = build_ticket_summary(records)
    assert "2건" in title
    assert "&lt;b&gt;X&lt;/b&gt;" in body and "<b>X</b>" not in body  # 외부 문자열은 escape
    assert "abcdef012345" in body  # 이름 없는 옛 항목은 지문 앞자리로 표시


def test_summary_caps_listed_groups():
    records = [
        {"ts": "2026-09-27T00:00:00+00:00", "fingerprint": f"fp-{i:02d}", "alarm_name": f"A{i:02d}"}
        for i in range(summary_mod._SUMMARY_TOP_N + 5)
    ]
    _, body = build_ticket_summary(records)
    assert f"A{summary_mod._SUMMARY_TOP_N - 1:02d}" in body
    assert f"A{summary_mod._SUMMARY_TOP_N:02d}" not in body
    assert "외 5종 5건" in body


# ── 4. 보존 한도 ─────────────────────────────────────────────────────────


def test_trim_keeps_newest_lines_and_warns(tmp_path, caplog):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    for i in range(30):
        queue.enqueue(_decision(f"fp-{i}"), alarm_id=f"A-{i}")

    with caplog.at_level(logging.WARNING):
        assert queue.trim(10) == 20
    assert [r["alarm_id"] for r in queue.read_pending()] == [f"A-{i}" for i in range(20, 30)]
    assert any("보존 한도" in r.getMessage() for r in caplog.records)

    assert queue.trim(0) == 0  # 0 = 정리하지 않음(종전 동작)
    assert queue.trim(10) == 0  # 한도 이하면 그대로
    assert len(queue.read_pending()) == 10


async def test_notifier_records_labels_in_queue(tmp_path):
    """요약이 사람이 읽는 이름을 쓰려면 적재 시 알람명·서버명이 실려야 한다(지문은 해시)."""
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    event = AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=2,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="r1",
        alarm_name="CPU High", alarm_time=datetime(2026, 9, 27, 9, 0, 0),
        conditions=">90%", condition_log="cpu 95%",
    )
    result = AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=["workb"],
    )
    state = {"analysis_result": result, "notification_decision": _decision()}
    config = {"configurable": {
        "app_config": SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace()),
        "ticket_queue": queue,
    }}
    await alarm_notifier_node(state, config)

    text = (tmp_path / "ticket.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(ln) for ln in text.splitlines()]
    assert rows[0]["alarm_name"] == "CPU High"
    assert rows[0]["server_name"] == "srv-1"


# ── 5. 주기 루프 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "hour", "expected"),
    [
        (datetime(2026, 9, 27, 8, 30), 9, 1800.0),
        (datetime(2026, 9, 27, 9, 0), 9, 86400.0),       # 정각이면 다음 날
        (datetime(2026, 9, 27, 10, 0), 9, 23 * 3600.0),
    ],
)
def test_seconds_until_hour(now, hour, expected):
    assert seconds_until_hour(hour, now) == expected


def test_out_of_range_hour_falls_back_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=summary_mod.__name__):
        assert summary_mod._summary_hour(SimpleNamespace(ticket_batch_summary_hour=24)) == 9
    assert any("0~23" in r.getMessage() for r in caplog.records)  # 조용히 바꾸지 않는다


def test_loop_reads_real_config_fields_with_safe_defaults():
    """루프는 getattr로 읽는다 — 필드명 오타는 조용히 '꺼짐'이 되므로 실재·기본값을 고정한다."""
    from src.config import NoiseGateConfig

    fields = NoiseGateConfig.model_fields
    assert fields["ticket_batch_summary_enabled"].default is False  # 밖으로 나가는 발송은 기본 off
    assert fields["ticket_batch_summary_hour"].default == 9
    assert fields["ticket_batch_queue_max_lines"].default == 20000


def _gate(**overrides) -> SimpleNamespace:  # noqa: ANN003
    base = dict(
        ticket_batch_summary_enabled=True,
        ticket_batch_summary_hour=9,
        ticket_batch_queue_max_lines=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _one_cycle_sleep(delays: list[float]):  # noqa: ANN202
    """첫 대기는 통과시키고 둘째 대기에서 취소해 루프를 한 바퀴만 돌린다."""

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) > 1:
            raise asyncio.CancelledError

    return fake_sleep


async def test_loop_trims_on_start_then_sends_at_hour(tmp_path, monkeypatch):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    for i in range(3):
        queue.enqueue(_decision(f"fp-{i}"), alarm_id=f"A-{i}", alarm_name=f"N{i}")
    sent = _Sent()
    monkeypatch.setattr(summary_mod, "_send_workb_summary", sent)
    delays: list[float] = []
    config = SimpleNamespace(noise_gate=_gate(), workb=_workb())

    await run_ticket_summary_loop(
        queue, config,
        now=lambda: datetime(2026, 9, 27, 8, 0), sleep=_one_cycle_sleep(delays),
    )

    assert delays[0] == 3600.0  # 08:00 → 09:00
    assert len(sent.calls) == 1
    # 기동 시 정리(3→2줄)가 먼저라 가장 오래된 A-0은 요약에 없다.
    assert "N0" not in sent.calls[0][1] and "N2" in sent.calls[0][1]
    assert queue.read_pending() == []


async def test_loop_disabled_only_trims(tmp_path, monkeypatch):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    for i in range(5):
        queue.enqueue(_decision(f"fp-{i}"), alarm_id=f"A-{i}")
    sent = _Sent()
    monkeypatch.setattr(summary_mod, "_send_workb_summary", sent)
    config = SimpleNamespace(noise_gate=_gate(ticket_batch_summary_enabled=False), workb=_workb())

    await run_ticket_summary_loop(
        queue, config, now=lambda: datetime(2026, 9, 27, 8, 0), sleep=_one_cycle_sleep([]),
    )

    assert sent.calls == []  # 발송은 기본 꺼짐 — 밖으로 나가는 것이 없다
    assert len(queue.read_pending()) == 2  # 정리는 발송과 무관하게 동작한다


# ── 6. 워커 배선 ─────────────────────────────────────────────────────────


class _FakeRedis:
    async def aclose(self) -> None:
        return None


def _patched_worker(monkeypatch, queue):  # noqa: ANN001, ANN202
    config = SimpleNamespace(
        alarm=SimpleNamespace(
            enabled=True, redis_stream_key="alarm:raw",
            redis_consumer_group="g", min_severity=1,
        ),
        redis=SimpleNamespace(host="127.0.0.1", port=6379, password="", db=0),
        noise_gate=_gate(),
        workb=_workb(),
    )
    worker = AlarmWorker(config)
    for name in [n for n in dir(AlarmWorker) if n.startswith("_build_")]:
        monkeypatch.setattr(worker, name, lambda: None)
    monkeypatch.setattr(worker, "_build_ticket_queue", lambda: queue)

    async def _noop(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    async def _stop(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_mod.aioredis, "from_url", lambda *a, **k: _FakeRedis())
    monkeypatch.setattr(worker_mod, "ensure_consumer_group", _noop)
    monkeypatch.setattr(worker_mod, "build_alarm_graph", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "read_messages", _stop)
    return worker, config


async def test_worker_run_starts_and_cancels_summary_loop(tmp_path, monkeypatch):
    queue = TicketBatchQueue(str(tmp_path / "ticket.jsonl"))
    worker, config = _patched_worker(monkeypatch, queue)
    calls: list[tuple] = []

    def fake_loop(q, cfg):  # noqa: ANN001, ANN202
        calls.append((q, cfg))
        return asyncio.sleep(3600)

    monkeypatch.setattr(worker_mod, "run_ticket_summary_loop", fake_loop)
    await worker.run()
    await asyncio.sleep(0)

    assert calls == [(queue, config)]
    assert worker._ticket_summary_task is not None
    assert worker._ticket_summary_task.cancelled()  # 워커 종료 시 루프도 멈춘다


async def test_worker_run_without_queue_starts_no_loop(tmp_path, monkeypatch):
    worker, _ = _patched_worker(monkeypatch, None)
    calls: list[tuple] = []
    monkeypatch.setattr(
        worker_mod, "run_ticket_summary_loop",
        lambda q, cfg: calls.append((q, cfg)) or asyncio.sleep(0),
    )
    await worker.run()
    assert calls == []  # 게이트·큐가 꺼져 있으면 종전과 같다
    assert worker._ticket_summary_task is None
