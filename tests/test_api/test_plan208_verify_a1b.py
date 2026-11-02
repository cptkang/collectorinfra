"""plans/208 A1b 독립 검증(verifier) — 사용자 경로 가림 공유 캐시의 보안 경계.

- 스레드 가림이 도는 사이 버퍼가 바뀌어도(폴링 증분 · 요약 JSONL 교체) 사용자 출력에 원문 0 ·
  교체된 내용이 반영된다(옛 가림 결과 재사용 0)
- 운영자 조회가 먼저 돌아도 사용자 캐시에 원문·`None` 키가 생기지 않는다
- 라우트 마스크 함수는 마스크 꼴 외 설정(인증·사용자)과 무관하게 같은 객체 · 같은 결과

실 조사 서비스·LLM 0.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.api import investigation_live_hub as hub_mod
from src.api.routes.alarm import _investigation_mask_fn
from tests.test_api.test_plan208_a1b_mask_share import (
    _MASK,
    INV,
    SECRET,
    _ArchiveStore,
    _ctx,
    _hub,
    _legacy_user_events,
    _res,
    _sent,
)


def _frames_text(item: Any) -> str:
    frames = item[0] if isinstance(item, tuple) else [item]
    return "".join(frames)


async def test_buffer_swapped_during_thread_masking_never_leaks_or_goes_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = [
        {"seq": s, "engine": "holmes", "kind": "tool_result", "label": f"교체 {SECRET} #{s}"}
        for s in range(1, 301)  # 지금 가리는 중인 seq와 같다
    ]
    hub = _hub(_ArchiveStore(archive))
    entry = hub._ensure_entry({"investigation_id": INV, "status": "running"})
    entry.polled_at = 0.0
    hub._absorb(entry, _res(range(1, 301)))
    real_to_thread = asyncio.to_thread

    async def racing_to_thread(fn: Any, *args: Any) -> Any:
        result = await real_to_thread(fn, *args)
        # 스레드가 옛 이벤트를 가리는 사이 버퍼를 갈아 끼운다(요약 JSONL)
        hub._finish_from_archive(entry)
        return result

    monkeypatch.setattr(hub_mod.asyncio, "to_thread", racing_to_thread)
    user = _ctx(False)
    await entry.prepare_user_events(-1, user.mask_fn)
    frames = hub_mod._watch_frames(hub, user, INV, _sent())
    payload = hub.activity_payload(entry, -1, operator=False, mask_fn=_MASK)

    assert SECRET not in _frames_text(frames)
    assert SECRET not in json.dumps(payload, ensure_ascii=False)
    assert payload["activity"]["events"] == _legacy_user_events(entry.events, _MASK)
    assert any("교체" in str(e.get("label")) for e in payload["activity"]["events"])


async def test_operator_first_never_seeds_user_cache_with_raw() -> None:
    hub = _hub()
    entry = hub._ensure_entry({"investigation_id": INV, "status": "running"})
    entry.polled_at = 0.0
    hub._absorb(entry, _res(range(1, 301)))
    op = _ctx(True)
    op_frames = hub_mod._watch_frames(hub, op, INV, _sent())
    op_payload = hub.activity_payload(entry, -1, operator=True, mask_fn=None)
    assert SECRET in _frames_text(op_frames)  # 운영자는 원문
    assert SECRET in json.dumps(op_payload, ensure_ascii=False)
    assert entry.user_cache == {}  # 운영자 경로는 캐시를 건드리지 않는다

    user = _ctx(False)
    await entry.prepare_user_events(-1, user.mask_fn)
    user_text = _frames_text(hub_mod._watch_frames(hub, user, INV, _sent()))
    user_payload = hub.activity_payload(entry, -1, operator=False, mask_fn=_MASK)
    assert SECRET not in user_text
    assert SECRET not in json.dumps(user_payload, ensure_ascii=False)
    assert None not in entry.user_cache
    cached = json.dumps([item for _, item in entry.user_cache[_MASK].values()],
                        ensure_ascii=False)
    assert SECRET not in cached
    # 운영자 재조회는 여전히 원문(사용자 캐시가 운영자 경로로 새지 않는다)
    assert SECRET in json.dumps(
        hub.activity_payload(entry, -1, operator=True, mask_fn=None), ensure_ascii=False
    )


def test_mask_fn_identity_independent_of_non_mask_config() -> None:
    def cfg(**auth: Any) -> Any:
        return SimpleNamespace(security=SimpleNamespace(mask_pattern="***"),
                               auth=SimpleNamespace(**auth))

    a = _investigation_mask_fn(cfg(enabled=True, user="u1", roles=["user"]))
    b = _investigation_mask_fn(cfg(enabled=False, user="admin", roles=["admin"]))
    assert a is b is _MASK
    text = f"host-1 Bearer {SECRET}\n{SECRET}\npassword=hunter2"
    assert a(text) == b(text) and SECRET not in a(text)
