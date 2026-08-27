"""폴스타 심각도 라벨 정규화 + 워커 dead-letter + 워커/API 경로 대칭 (D-175).

배경(2026-08-25 폐쇄망 실측): 폴스타 템플릿 `${severity}`가 설계 전제(정수 0~3)와 달리
한글 라벨(`해제`/`주의`/`경고`/`심각`)로 렌더링되어 Redis `alarm:raw`에 적재됐고, 워커의
`int(payload["severity"])`가 ValueError → `except: 로그 / finally: ACK`로 전량 폐기되어
UI·통보 어느 쪽에도 도달하지 못했다.

검증 항목:
    A. domain `parse_severity` / `coerce_severity` — 정수·정수 문자열·한글/영문 라벨 수용, 미지값 거부
    B. 워커 `_process` — 한글 라벨이 그래프까지 도달, 미지값은 보수적 폴백 + WARNING(폐기 아님),
       파싱 실패는 dead-letter 적재 후 ACK(무한 재처리 방지), dead-letter off/실패 시 graceful
    C. API `_build_alarm_event_from_payload` — 워커와 동일 정규화(경로 대칭), 현행 계약 유지
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.domain.severity import (
    CONSERVATIVE_SEVERITY,
    SeverityParseError,
    coerce_severity,
    parse_severity,
)
from noise_gate.infrastructure.redis_queue import dead_letter_message
from src.api.routes.alarm import _build_alarm_event_from_payload

# ─────────────────────────────────────────────────────────────
# Part A: domain 정규화
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 폴스타 실측 한글 라벨(해제=0 — 코드 관례 '해소'와 동일 취급)
        ("해제", 0), ("해소", 0), ("주의", 1), ("경고", 2), ("심각", 3),
        # 공백·대소문자 무시, 폴스타 조건식 어휘
        (" 주의 ", 1), ("CLEAR", 0), ("Attention", 1), ("TROUBLE", 3), ("warning", 2),
        # 정수·정수 문자열·정수형 실수
        (0, 0), (3, 3), ("2", 2), (" 3 ", 3), (2.0, 2), ("1.0", 1),
        # redis-cli가 이스케이프해 보여준 UTF-8 바이트(\xed\x95\xb4\xec\xa0\x9c = 해제)
        (b"\xed\x95\xb4\xec\xa0\x9c", 0),
    ],
)
def test_parse_accepts(raw, expected):
    assert parse_severity(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, True, False, 4, -1, "", "   ", "알수없음", "2.5", "high", [2], {"v": 2}],
)
def test_parse_rejects_unknown(raw):
    with pytest.raises(SeverityParseError):
        parse_severity(raw)


def test_parse_error_is_value_error_subclass():
    # 기존 `int()` 호출부의 `except ValueError` 절과 호환되어야 한다.
    assert issubclass(SeverityParseError, ValueError)


def test_coerce_success_has_no_reason():
    assert coerce_severity("경고") == (2, None)


def test_coerce_fallback_returns_reason():
    value, reason = coerce_severity("알수없음")
    assert value == CONSERVATIVE_SEVERITY
    assert reason is not None and "알수없음" in reason


def test_coerce_custom_fallback():
    assert coerce_severity(None, fallback=0) == (0, coerce_severity(None)[1])


# ─────────────────────────────────────────────────────────────
# Part B: 워커 _process — fake redis/graph 구동
# ─────────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self) -> None:
        self.acks: list[tuple] = []
        self.xadds: list[tuple[str, dict, dict]] = []

    async def xack(self, *args, **kwargs):
        self.acks.append(args)
        return 1

    async def xadd(self, key, fields, **kwargs):
        self.xadds.append((key, fields, kwargs))
        return b"1-0"


class _FakeGraph:
    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


def _worker(*, min_severity: int = 1) -> tuple[AlarmWorker, _FakeGraph]:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            repeat_interval_seconds=14400,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
        ),
        alarm=SimpleNamespace(
            min_severity=min_severity,
            dedup_ttl_seconds=300,
            dead_letter_stream_key="alarm:dead",
            dead_letter_maxlen=50,
        ),
    )
    worker = AlarmWorker(cfg)
    graph = _FakeGraph()
    worker._graph = graph
    return worker, graph


def _fields(**payload) -> dict:
    return {b"data": json.dumps(payload, ensure_ascii=False).encode("utf-8")}


async def _run(worker: AlarmWorker, fields: dict, r: _FakeRedis | None = None) -> _FakeRedis:
    r = r or _FakeRedis()
    await worker._process(r, "alarm:raw", "alarm-workers", b"1-1", fields, {})
    return r


@pytest.mark.parametrize("label,expected", [("주의", 1), ("경고", 2), ("심각", 3)])
async def test_worker_korean_label_reaches_graph(label, expected):
    worker, graph = _worker()
    await _run(worker, _fields(alarmId="A-1", severity=label, alarmTime="20260825120000"))
    assert len(graph.states) == 1
    event = graph.states[0]["alarm_event"]
    assert event.severity == expected
    assert event.is_clear is False


async def test_worker_haeje_label_is_clear():
    worker, graph = _worker()
    await _run(worker, _fields(alarmId="A-2", severity="해제"))
    event = graph.states[0]["alarm_event"]
    assert event.severity == 0
    assert event.is_clear is True


async def test_worker_unknown_label_conservative_fallback_with_warning(caplog):
    worker, graph = _worker()
    with caplog.at_level(logging.WARNING, logger="noise_gate.application.alarm_worker"):
        r = await _run(worker, _fields(alarmId="A-3", severity="알수없음"))
    # 폐기 대신 보수적 폴백으로 그래프 도달
    assert graph.states[0]["alarm_event"].severity == CONSERVATIVE_SEVERITY
    # 침묵 폴백 금지 — 사유·원값을 WARNING으로 가시화
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("보수적 폴백" in m and "알수없음" in m for m in messages)
    assert r.xadds == []  # 처리 성공이므로 dead-letter 아님
    assert len(r.acks) == 1


async def test_worker_min_severity_drop_still_applies_to_labels():
    # 라벨 정규화 후에도 기존 min_severity 드롭(1 <= sev < min)은 그대로 동작한다.
    worker, graph = _worker(min_severity=2)
    await _run(worker, _fields(alarmId="A-4", severity="주의"))
    assert graph.states == []


async def test_worker_parse_failure_goes_to_dead_letter_then_ack():
    worker, graph = _worker()
    r = _FakeRedis()
    await worker._process(r, "alarm:raw", "alarm-workers", b"9-9", {b"data": b"{not json"}, {})
    assert graph.states == []
    assert len(r.xadds) == 1
    key, fields, kwargs = r.xadds[0]
    assert key == "alarm:dead"
    assert fields["source_stream"] == "alarm:raw"
    assert fields["source_msg_id"] == "9-9"
    assert fields["data"] == "{not json"
    assert "JSONDecodeError" in fields["error"]
    assert kwargs["maxlen"] == 50 and kwargs["approximate"] is True
    assert len(r.acks) == 1  # 여전히 ACK — 같은 실패의 무한 재처리 방지


async def test_worker_missing_alarm_id_goes_to_dead_letter():
    worker, graph = _worker()
    r = await _run(worker, _fields(severity="경고"))
    assert graph.states == []
    assert len(r.xadds) == 1
    assert "KeyError" in r.xadds[0][1]["error"]


async def test_worker_dead_letter_defaults_without_alarm_config():
    # 상시 동작: alarm 설정이 없어도 기본 키(alarm:dead)·상한(1000)으로 적재한다(플래그 없음).
    worker = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(enable_noise_gate=False)))
    worker._graph = _FakeGraph()
    r = _FakeRedis()
    await worker._process(r, "alarm:raw", "g", b"1-1", {b"data": b"{bad"}, {})
    assert r.xadds[0][0] == "alarm:dead" and r.xadds[0][2]["maxlen"] == 1000
    assert len(r.acks) == 1


async def test_worker_dead_letter_failure_does_not_block_ack():
    class _Boom(_FakeRedis):
        async def xadd(self, *args, **kwargs):
            raise RuntimeError("redis down")

    worker, _ = _worker()
    r = _Boom()
    await worker._process(r, "alarm:raw", "g", b"1-1", {b"data": b"{bad"}, {})
    assert len(r.acks) == 1


async def test_dead_letter_message_decodes_bytes_and_records_reason():
    r = _FakeRedis()
    raw = '{"severity":"해제"}'.encode("utf-8")
    await dead_letter_message(
        r, "alarm:dead", "alarm:raw", b"5-5", {b"data": raw}, ValueError("x")
    )
    fields = r.xadds[0][1]
    assert fields["data"] == '{"severity":"해제"}'
    assert fields["source_msg_id"] == "5-5"
    assert fields["error"] == "ValueError: x"
    assert fields["failed_at"]


# ─────────────────────────────────────────────────────────────
# Part C: API 경로 파서 — 워커와 대칭
# ─────────────────────────────────────────────────────────────


def test_route_parser_accepts_korean_labels_non_tolerant():
    assert _build_alarm_event_from_payload({"alarmId": "R-1", "severity": "주의"}).severity == 1
    event = _build_alarm_event_from_payload({"alarmId": "R-2", "severity": "해제"})
    assert event.severity == 0 and event.is_clear is True


def test_route_parser_missing_severity_is_zero_non_tolerant():
    # 현행 계약 유지: 누락 → 0
    assert _build_alarm_event_from_payload({"alarmId": "R-3"}).severity == 0


def test_route_parser_unknown_raises_value_error_non_tolerant():
    # 현행 계약 유지: 비정수(미지값) → ValueError 전파
    with pytest.raises(ValueError):
        _build_alarm_event_from_payload({"alarmId": "R-4", "severity": "???"})


def test_route_parser_tolerant_fallback():
    tolerant = lambda p: _build_alarm_event_from_payload(p, format_tolerant=True)  # noqa: E731
    assert tolerant({"alarmId": "R-5", "severity": "???"}).severity == 2
    assert tolerant({"alarmId": "R-6"}).severity == 2
    assert tolerant({"alarmId": "R-7", "severity": "심각"}).severity == 3


def test_worker_and_route_agree_on_labels():
    # 경로 대칭: 같은 원문 → 같은 정수(단일 출처 domain/severity)
    for label in ("해제", "주의", "경고", "심각", "2"):
        route = _build_alarm_event_from_payload({"alarmId": "S", "severity": label}).severity
        assert parse_severity(label) == route
