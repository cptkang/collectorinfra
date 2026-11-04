"""plans/208 A1b — 본체 실시간 허브의 사용자 경로 마스킹 공유(R4 · §8.5 수용 기준 2 · D-359).

- 같은 이벤트의 사용자 가림은 엔트리 단위로 1번 — 사용자 구독자 3명·상세 조회가 결과를 나눠 쓴다
- 출력 동일: 교정 전 함수(아래 `_legacy_user_events` — 교정 전 `user_events` 원문)로 만든
  SSE 프레임·`activity_payload`와 바이트 동일(사용자 구독자 여럿 · 상세 조회 · 운영자 · 요약 JSONL로
  버퍼를 갈아 끼운 뒤)
- 큰 묶음(첫 연결 300건)은 이벤트 루프 밖(스레드)에서 1번만 가린다
- 캐시 상한(버퍼 이벤트 수 · 마스크 함수 수) · 엔트리 sweep 때 함께 사라진다
- 라우트 마스크 함수는 마스크 꼴이 같으면 같은 객체(사용자·권한 무관)

조사 서비스·실 LLM 0 — 허브 버퍼를 직접 채운다.
"""

from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import Callable, Iterable, Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from src.api import investigation_live_hub as hub_mod
from src.api.investigation_live_hub import InvestigationLiveHub, LiveEntry, StreamContext
from src.api.routes.alarm import _investigation_mask_fn

INV = "inv-a1b"
SECRET = "sk-abcdefghijklmnopqrstuvwx"  # DataMasker 값 패턴(API 키)
_MASK = _investigation_mask_fn(SimpleNamespace(security=SimpleNamespace(mask_pattern="***")))


def _legacy_user_events(
    events: Iterable[Mapping[str, Any]], mask_fn: Callable[[str], str] | None
) -> list[dict[str, Any]]:
    """교정 전 `user_events` 원문(기준값) — 이벤트마다 그 자리에서 가린다."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") in hub_mod.ADMIN_ONLY_KINDS or (ev.get("engine"), ev.get("kind")) in (
            hub_mod.ADMIN_ONLY_ENGINE_KINDS
        ):
            continue
        item: dict[str, Any] = {}
        for key in hub_mod.USER_EVENT_KEYS:
            if key not in ev:
                continue
            value = ev[key]
            item[key] = (
                value if key in hub_mod._UNMASKED_EVENT_KEYS
                else hub_mod._mask_value(value, mask_fn)
            )
        out.append(item)
    return out


def _event(seq: int, tag: str = "") -> dict[str, Any]:
    """비밀값이 섞인 여러 줄 발췌 · 관리자 전용(플레이북 메모) · 중첩 값을 고루 담은 이벤트."""
    base: dict[str, Any] = {
        "seq": seq, "at": f"2026-11-02T10:00:{seq % 60:02d}", "step": f"s{seq % 7}",
        "status": "ok", "elapsed_ms": 100 + seq,
    }
    if seq % 5 == 0:
        engine = "playbook" if seq % 10 == 0 else "holmes"
        return {**base, "engine": engine, "kind": "ai_note", "note": f"메모{tag} {SECRET}\n둘째 줄"}
    return {
        **base,
        "engine": "holmes",
        "kind": "tool_result",
        "tool": "polestar_query",
        "label": f"조회{tag} #{seq}",
        "rows": seq % 50,
        "purpose": f"token={SECRET}",
        "excerpt": f"host-{seq} Bearer {SECRET}\n{SECRET}\npassword=hunter2{tag}",
        "finding": [f"근거 {SECRET}", {"phrase": f"api_key={SECRET}"}],
        "secret_extra": "버려야 하는 키",
    }


def _res(seqs: Iterable[int], tag: str = "") -> dict[str, Any]:
    return {
        "status": "running",
        "phase": "collecting",
        "elapsed_s": 3,
        "activity": {"events": [_event(s, tag) for s in seqs]},
    }


class _ArchiveStore:
    """요약 JSONL 대역 — 같은 seq에 다른 내용(발췌 없음)을 돌려준다."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def get_summary(self, investigation_id: str) -> dict[str, Any] | None:  # noqa: ARG002
        return {"events": self.events, "truncated": True}

    def record_summary(self, **_: Any) -> bool:
        return True


def _hub(activity_store: Any = None) -> InvestigationLiveHub:
    ng = SimpleNamespace(investigation_live_poll_seconds=100.0)
    return InvestigationLiveHub(
        ng, store_factory=lambda: None, activity_store=activity_store, poll_seconds=100.0
    )


def _ctx(operator: bool, mask_fn: Callable[[str], str] | None = _MASK) -> StreamContext:
    return StreamContext(
        operator=operator,
        mask_fn=None if operator else mask_fn,
        in_scope=lambda rec: True,  # noqa: ARG005
        watch={INV: {"investigation_id": INV}},
        cursors={INV: -1},
    )


def _sent() -> dict[str, dict[str, int]]:
    return {"state": {}, "partial": {}, "error": {}}


async def _scenario(legacy: bool, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """사용자 구독자 3 · 운영자 1 · 상세 조회(사용자·운영자)를 단계별로 돌린 출력 전부."""
    if legacy:
        monkeypatch.setattr(
            LiveEntry, "user_view",
            lambda self, events, mask_fn: _legacy_user_events(events, mask_fn),  # noqa: ARG005
        )

        async def _noop(self: LiveEntry, since: int, mask_fn: Any) -> None:  # noqa: ARG001
            return None

        monkeypatch.setattr(LiveEntry, "prepare_user_events", _noop)
    archive = [
        {"seq": s, "engine": "holmes", "kind": "tool_result", "label": f"요약 {SECRET} #{s}"}
        for s in range(331, 361)  # 지금 버퍼·캐시에 든 seq와 겹친다
    ]
    hub = _hub(_ArchiveStore(archive))
    entry = hub._ensure_entry({"investigation_id": INV, "status": "running"})
    entry.polled_at = 0.0
    ctxs = [_ctx(False), _ctx(False), _ctx(False), _ctx(True)]
    sents = [_sent() for _ in ctxs]
    out: list[Any] = []

    async def _round() -> None:
        for ctx, sent in zip(ctxs, sents, strict=True):
            if not ctx.operator:
                await entry.prepare_user_events(ctx.cursors.get(INV, -1), ctx.mask_fn)
            out.append(hub_mod._watch_frames(hub, ctx, INV, sent))
        for since in (-1, 120, 330):
            for operator in (False, True):
                payload = hub.activity_payload(
                    entry, since, operator=operator, mask_fn=None if operator else _MASK
                )
                out.append(hub_mod.sse("payload", payload))

    hub._absorb(entry, _res(range(1, 301)))  # 진행 중 조사에 처음 붙음(300건)
    await _round()
    hub._absorb(entry, _res(range(301, 311), tag="+"))  # 폴링 1회 증분(넘쳐 앞 10건이 밀림)
    await _round()
    hub._absorb(entry, _res(range(311, 361), tag="++"))  # 큰 증분(스레드 경로)
    await _round()
    ctxs.append(_ctx(False))  # 늦게 붙은 사용자 구독자(커서 -1)
    sents.append(_sent())
    await _round()
    hub._finish_from_archive(entry)  # 버퍼를 요약 JSONL로 갈아 끼움(같은 seq · 다른 내용)
    ctxs.append(_ctx(False))
    sents.append(_sent())
    await _round()
    return out


async def test_frames_and_payload_byte_identical_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    new = await _scenario(False, monkeypatch)
    with monkeypatch.context() as m:
        old = await _scenario(True, m)
    assert new == old
    joined = "".join(f for item in new for f in (item[0] if isinstance(item, tuple) else [item]))
    assert SECRET in joined  # 운영자 경로는 원문 그대로
    user_frames = [
        f for item in new if isinstance(item, tuple) for f in item[0]
        if '"note": "메모' in f or "Bearer" in f
    ]
    assert user_frames  # 사용자 프레임이 실제로 비교됐다


async def test_user_subscribers_share_one_masking(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"mask": 0, "pairs": 0, "threads": 0}

    def counting(text: str) -> str:
        calls["mask"] += 1
        return _MASK(text)

    real_pairs = hub_mod._user_pairs
    real_to_thread = asyncio.to_thread

    def pairs(events: list[dict[str, Any]], mask_fn: Any) -> Any:
        calls["pairs"] += 1
        return real_pairs(events, mask_fn)

    async def to_thread(fn: Any, *args: Any) -> Any:
        calls["threads"] += 1
        return await real_to_thread(fn, *args)

    monkeypatch.setattr(hub_mod, "_user_pairs", pairs)
    monkeypatch.setattr(hub_mod.asyncio, "to_thread", to_thread)
    hub = _hub()
    entry = hub._ensure_entry({"investigation_id": INV, "status": "running"})
    entry.polled_at = 0.0
    hub._absorb(entry, _res(range(1, 301)))
    expected = _legacy_user_events(entry.events, _MASK)
    one_pass = calls["mask"]
    _legacy_user_events(entry.events, counting)
    one_pass = calls["mask"] - one_pass
    calls["mask"] = 0

    ctxs = [_ctx(False, counting) for _ in range(3)]
    # 같은 깨움에 걸린 구독자 3명이 동시에 준비해도 스레드 가림은 1번
    await asyncio.gather(*(
        entry.prepare_user_events(ctx.cursors[INV], ctx.mask_fn) for ctx in ctxs
    ))
    assert calls["threads"] == 1 and calls["pairs"] == 1
    for ctx in ctxs:
        hub_mod._watch_frames(hub, ctx, INV, _sent())
    view = hub.activity_payload(entry, -1, operator=False, mask_fn=counting)
    assert view["activity"]["events"] == expected
    assert calls["mask"] == one_pass  # 구독자 3 + 상세 조회 = 가림 1번 분량

    # 작은 증분은 스레드를 쓰지 않고 읽을 때 1번 가린다
    hub._absorb(entry, _res(range(301, 306)))
    await entry.prepare_user_events(300, counting)
    assert calls["threads"] == 1
    before = calls["mask"]
    for ctx in ctxs:
        hub_mod._watch_frames(hub, ctx, INV, _sent())
    increment = calls["mask"] - before
    assert 0 < increment < one_pass / 10


def test_cache_bounded_by_buffer_and_mask_functions() -> None:
    hub = _hub()
    entry = hub._ensure_entry({"investigation_id": INV, "status": "running"})
    for start in range(1, 1001, 50):
        hub._absorb(entry, _res(range(start, start + 50)))
        entry.user_view(entry.events, _MASK)
        cache = entry.user_cache[_MASK]
        assert len(cache) <= hub_mod.ACTIVITY_MAX_EVENTS
        live = {id(ev) for ev in entry.events}
        assert all(id(ev) in live for ev, _ in cache.values())
    assert len(entry.events) == hub_mod.ACTIVITY_MAX_EVENTS

    fns = [lambda t, n=n: f"{n}:{t}" for n in range(hub_mod.USER_MASK_CACHE_MAX_FNS + 3)]
    for fn in fns:
        assert entry.user_view(entry.events[-3:], fn) == _legacy_user_events(
            entry.events[-3:], fn
        )
    assert len(entry.user_cache) == hub_mod.USER_MASK_CACHE_MAX_FNS
    assert fns[-1] in entry.user_cache and _MASK not in entry.user_cache


def test_cache_swept_with_entry() -> None:
    now = [0.0]
    hub = InvestigationLiveHub(
        SimpleNamespace(investigation_live_poll_seconds=5.0),
        store_factory=lambda: None,
        activity_store=None,
        buffer_ttl_seconds=600.0,
        clock=lambda: now[0],
    )
    entry = hub._ensure_entry({"investigation_id": INV, "status": "running"})
    hub._absorb(entry, _res(range(1, 301)))
    entry.user_view(entry.events, _MASK)
    assert len(entry.user_cache[_MASK]) == 300
    ref = weakref.ref(entry)
    del entry
    now[0] = 601.0
    hub.sweep()
    gc.collect()
    assert hub.entry(INV) is None
    assert ref() is None  # 엔트리와 함께 가림 결과도 사라졌다


def test_route_mask_fn_shared_per_mask_pattern() -> None:
    def cfg(mask: str) -> Any:
        return SimpleNamespace(security=SimpleNamespace(mask_pattern=mask))

    assert _investigation_mask_fn(cfg("***")) is _investigation_mask_fn(cfg("***"))
    other = _investigation_mask_fn(cfg("[가림]"))
    assert other is not _MASK
    assert other(SECRET) == "[가림]" and _MASK(SECRET) == "***"


async def test_stream_events_frames_identical_and_masked_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 SSE 제너레이터 — 사용자 3 · 운영자 1 구독자가 받은 프레임이 교정 전과 같다."""

    async def run(legacy: bool) -> list[list[str]]:
        hub = _hub()
        entry = hub._ensure_entry({"investigation_id": INV, "status": "running"})
        entry.polled_at = hub.clock()  # 폴러가 곧바로 다시 묻지 않게(간격 100초)
        hub._absorb(entry, _res(range(1, 301)))
        if legacy:
            monkeypatch.setattr(
                LiveEntry, "user_view",
                lambda self, events, mask_fn: _legacy_user_events(events, mask_fn),  # noqa: ARG005
            )
        stop = asyncio.Event()

        async def consume(ctx: StreamContext) -> list[str]:
            frames: list[str] = []

            async def disconnected() -> bool:
                return stop.is_set()

            stream = hub_mod.stream_events(hub, ctx, disconnected)
            try:
                async for frame in stream:
                    frames.append(frame)
                    if len(frames) == 3:  # retry · summary · activity
                        break
            finally:
                await stream.aclose()
            return frames

        try:
            return await asyncio.gather(*(
                consume(_ctx(operator)) for operator in (False, False, False, True)
            ))
        finally:
            stop.set()
            await hub.aclose()
            monkeypatch.undo()

    new = await run(False)
    old = await run(True)
    assert new == old
    assert all(frames[2].startswith("id: ") and "event: activity" in frames[2] for frames in new)
    assert SECRET not in "".join(new[0]) and SECRET in "".join(new[3])
