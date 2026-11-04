"""plans/210 W9 독립 검증 — 예산 취소 안전성(실 `WebSession` + 지연 MockTransport) · 잠금 대기 ·
호출자 취소 · 걸린 뷰서버 반복 · 설정 값 검증 · 로그 비밀 유출 (§6.3).

실제 제니퍼·외부 네트워크·실 LLM 없음 — 웹은 `test_plan202_web_session.FakeWeb`(실측 규칙 가짜)를
비동기 지연 처리기로 감싼다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.jennifer.web_session import LIST_PATH, WebSession
from apm_gateway.application.node_address import (
    STATUS_BUDGET_EXCEEDED,
    STATUS_OK,
    NodeAddressService,
    lookup_fields,
)
from conftest import NOW_S
from test_plan202_web_session import (
    COOKIE_ID,
    COOKIE_PW,
    PASSWORD,
    POD_SID,
    USER,
    VM_SID,
    WEB,
    FakeWeb,
)

from apm_gateway.config import load_config

SLACK = 0.5
LOGIN_POST = ("POST", "/login/page")


class SlowWeb:
    """`FakeWeb`을 비동기 지연으로 감싼다 — (메서드, 경로) → 지연(초)."""

    def __init__(self, delays: dict[tuple[str, str], float] | None = None) -> None:
        self.fake = FakeWeb()
        self.delays = dict(delays or {})
        self.started: list[tuple[str, str]] = []
        self.inflight = 0
        self.max_inflight = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.started.append(key)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(self.delays.get(key, 0.0))
            return self.fake(request)
        finally:
            self.inflight -= 1


def _session(web: SlowWeb, source_id: str = "b0") -> WebSession:
    return WebSession(
        source_id=source_id,
        url=WEB,
        user=USER,
        password=PASSWORD,
        transport=httpx.MockTransport(web),
    )


def _service(sessions: dict[str, Any], budget: float = 0.3) -> NodeAddressService:
    return NodeAddressService(sessions, call_budget_seconds=budget, clock=lambda: NOW_S)


def _logins(web: SlowWeb) -> int:
    return web.fake.calls.count(LOGIN_POST)


async def _timed(coro: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    out = await coro
    return out, time.perf_counter() - started


async def _drain(service: NodeAddressService, limit: float = 3.0) -> None:
    """예산 뒤 마저 도는 웹 조회가 끝날 때까지 기다린다."""
    deadline = time.perf_counter() + limit
    while service._background:
        assert time.perf_counter() < deadline, "뒤에서 도는 조회가 끝나지 않았다"
        await asyncio.sleep(0.01)


# ── 1. 취소 안전성 — 실 WebSession ────────────────────────────────────────────────


async def test_cancel_mid_list_stream_releases_lock_and_reuses_login():
    """목록 응답 대기 중 예산 종료 → 호출자에게는 예산에 돌려주고 진행 중 요청은 뒤에서 끝난다
    (W9 교정 2) · 실패 기록 0 · 끝나면 잠금 해제·캐시 · 다음 호출은 웹 0회(로그인 소스당 1회)."""
    web = SlowWeb({("GET", LIST_PATH): 0.6})
    session = _session(web)
    service = _service({"b0": session}, budget=0.3)
    (snap, missed), took = await _timed(
        service.domain_snapshots([("b0", POD_SID)], budget_seconds=0.3)
    )
    assert took <= 0.3 + SLACK
    assert missed == {("b0", POD_SID)}
    assert snap[("b0", POD_SID)].status == STATUS_BUDGET_EXCEEDED
    assert service._background  # 진행 중 요청은 취소하지 않았다
    await _drain(service)
    assert not session._lock.locked() and not service._state("b0").lock.locked()
    assert service._failures == {} and service._state("b0").backoff_until == 0.0
    assert web.inflight == 0  # 요청 처리기도 끝났다(스트림 남김 없음)
    again = await service.domain_snapshots([("b0", POD_SID)], budget_seconds=0.3)
    assert again[0][("b0", POD_SID)].status == STATUS_OK
    assert web.started.count(("GET", LIST_PATH)) == 1  # 뒤에서 끝난 결과가 캐시 hit
    assert _logins(web) == 1
    await service.aclose()


async def test_budget_mid_login_finishes_in_background_single_post():
    """로그인 POST 응답 대기 중 예산 종료 → 로그인·목록은 뒤에서 끝까지(W9 교정 2) · 차단기·실패
    기록 0 · 다음 호출은 캐시 hit — 로그인 POST 1회뿐(중간 취소 뒤 재로그인 없음)."""
    web = SlowWeb({LOGIN_POST: 0.5})
    session = _session(web)
    service = _service({"b0": session}, budget=0.2)
    _, missed = await service.domain_snapshots([("b0", POD_SID)], budget_seconds=0.2)
    assert missed == {("b0", POD_SID)} and session._logged_in is False
    await _drain(service)
    assert session._logged_in is True and not session._lock.locked()
    assert service._state("b0").breaker.failures == 0 and service._failures == {}
    out, _ = await service.domain_snapshots([("b0", POD_SID)], budget_seconds=1.0)
    assert out[("b0", POD_SID)].status == STATUS_OK
    assert web.started.count(LOGIN_POST) == 1
    assert web.started.count(("GET", LIST_PATH)) == 1
    await service.aclose()


async def test_source_serial_and_cross_source_parallel_with_real_sessions():
    """실 세션 2소스 · 도메인 2개씩 · 목록 0.15초 — 소스 안 동시 1 · 소스 간 병렬(합계 < 직렬)."""
    webs = {s: SlowWeb({("GET", LIST_PATH): 0.15}) for s in ("b0", "b1")}
    service = _service({s: _session(w, s) for s, w in webs.items()}, budget=5.0)
    pairs = [(s, d) for s in webs for d in (POD_SID, VM_SID)]
    (out, missed), took = await _timed(service.domain_snapshots(pairs, budget_seconds=5.0))
    assert missed == set() and {x.status for x in out.values()} == {STATUS_OK}
    assert all(w.max_inflight == 1 for w in webs.values())
    assert took < 4 * 0.15  # 직렬 4회보다 짧다 — 소스 간 병렬
    assert all(_logins(w) == 1 for w in webs.values())
    await service.aclose()


async def test_caller_cancel_leaves_no_running_task_and_keeps_legacy_record():
    """호출자 취소(예산 전) — 소스 작업이 남지 않는다 · 웹 무응답 기록(소스 백오프)은 종전
    그대로."""
    web = SlowWeb({("GET", LIST_PATH): 5.0})
    service = _service({"b0": _session(web)}, budget=10.0)
    before = set(asyncio.all_tasks())
    outer = asyncio.create_task(service.domain_snapshots([("b0", POD_SID)], budget_seconds=10.0))
    await asyncio.sleep(0.1)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    for _ in range(5):
        await asyncio.sleep(0.01)
    leftover = [t for t in asyncio.all_tasks() - before if t is not asyncio.current_task()]
    assert leftover == []
    assert service._state("b0").backoff_until > 0  # 종전 취소 기록
    await service.aclose()


# ── 2. 예산이 전체를 묶는가 ─────────────────────────────────────────────────────────


async def test_budget_bounds_wait_on_source_lock_held_by_alarm_path():
    """알람 경로 조회가 소스 잠금을 쥐고 있어도 대상 지정 일괄 조회는 예산 안에 돌아온다 · 알람
    경로 조회는 예산 취소의 영향을 받지 않는다(제 상한으로 끝난다)."""
    web = SlowWeb({("GET", LIST_PATH): 1.0})
    service = _service({"b0": _session(web)}, budget=0.2)
    alarm = asyncio.create_task(lookup_fields(service, "b0", POD_SID, 1001, timeout_seconds=3.0))
    await asyncio.sleep(0.05)
    assert service._state("b0").lock.locked()
    (out, missed), took = await _timed(
        service.domain_snapshots([("b0", VM_SID)], budget_seconds=0.2)
    )
    assert took <= 0.2 + SLACK and missed == {("b0", VM_SID)}
    assert out[("b0", VM_SID)].status == STATUS_BUDGET_EXCEEDED
    fields = await alarm
    assert fields["host_address_status"] == STATUS_OK
    await service.aclose()


# ── 2b. 걸린 뷰서버 — 뒤에서 끝난 전송 시간 초과가 소스 백오프를 건다(W9 교정 2) ──────────


class _HungWeb(SlowWeb):
    """목록 요청이 지연 뒤 전송 시간 초과로 끝난다(MockTransport는 httpx 타임아웃을 적용하지
    않으므로 `ReadTimeout`을 직접 올려 웹 요청 상한 도달을 대신한다)."""

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        response = await super().__call__(request)
        if request.url.path == LIST_PATH:
            raise httpx.ReadTimeout("hung", request=request)
        return response


async def test_hung_web_first_call_returns_at_budget_then_source_backoff_holds_network():
    """웹 무응답 — 첫 대상 지정 호출은 예산에 돌려주고, 뒤에서 끝난 요청의 전송 시간 초과가
    종전대로 소스 백오프(60초)를 건다 → 다음 호출들은 네트워크 0 · `error`(W9 이전과 같은 보호)."""
    web = _HungWeb({("GET", LIST_PATH): 0.5})
    service = _service({"b0": _session(web)}, budget=0.2)
    (_, missed), took = await _timed(
        service.domain_snapshots([("b0", POD_SID)], budget_seconds=0.2)
    )
    assert missed == {("b0", POD_SID)} and took <= 0.2 + SLACK
    await _drain(service)
    assert service._state("b0").backoff_until > 0
    for _ in range(2):
        (out, missed), t = await _timed(
            service.domain_snapshots([("b0", POD_SID)], budget_seconds=0.2)
        )
        assert missed == set() and out[("b0", POD_SID)].status == "error"
        assert t < 0.1
    assert web.started.count(("GET", LIST_PATH)) == 1  # 걸린 서버를 다시 치지 않는다
    await service.aclose()


# ── 6. 설정 값 검증 · 로그 ───────────────────────────────────────────────────────


def _cfg(value: str):
    return load_config(
        {"JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS": value}, dotenv=None
    ).node_address.call_budget_seconds


def test_budget_config_default_and_decimal():
    assert load_config({}, dotenv=None).node_address.call_budget_seconds == 5.0
    assert _cfg("2.5") == 2.5
    assert _cfg("") == 5.0


@pytest.mark.parametrize("value", ["0", "-1", "-0.5"])
def test_budget_config_rejects_non_positive(value):
    with pytest.raises(ValueError, match="JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS"):
        _cfg(value)


def test_budget_config_non_numeric_error_names_key():
    """비숫자 — 오류에 키 이름을 싣는다(`_positive_int`와 같은 형식)."""
    with pytest.raises(ValueError, match="JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS"):
        _cfg("abc")


@pytest.mark.parametrize("value", ["nan", "inf"])
def test_budget_config_rejects_nan_inf(value):
    with pytest.raises(ValueError):
        _cfg(value)


async def test_budget_cancel_logs_no_secret(caplog):
    web = SlowWeb({("GET", LIST_PATH): 5.0})
    service = _service({"b0": _session(web)}, budget=0.2)
    with caplog.at_level(logging.DEBUG, logger="apm_gateway"):
        await service.domain_snapshots([("b0", POD_SID), ("b0", VM_SID)], budget_seconds=0.2)
    text = caplog.text
    assert "예산" in text
    for secret in (PASSWORD, COOKIE_ID, COOKIE_PW, "S1", WEB):
        assert secret not in text, secret
    await service.aclose()
