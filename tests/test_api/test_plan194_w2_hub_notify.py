"""plans/194 W2 — 허브는 바뀐 것이 있을 때만 스트림을 깨운다(`hub.version`).

- 무변화 poll(경과 `elapsed_s`만 바뀜 포함)은 `version`을 올리지 않는다
- 새 이벤트 · 단계 변경 · 부분 브리핑 · 오류 설정/해제 · 종결은 올린다(같은 오류 반복은 1회)
- 하트비트 대신: 깨우지 않아도 스트림은 `wait_changed` 시한마다 다시 읽어 새 경과를 보낸다

가짜 sre_agent는 plans/187 W14 테스트의 것을 쓴다. 실 LLM 0.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from noise_gate.infrastructure import sre_agent_client as sre_client_mod
from src.api.investigation_live_hub import StreamContext, stream_events
from tests.test_api.test_plan187_w14_live_hub import (
    FakeJob,
    FakeSre,
    ev,
    make_hub,
    seed,
    wait_until,
)


@pytest.fixture()
def fake_sre(monkeypatch: pytest.MonkeyPatch) -> FakeSre:
    fake = FakeSre()
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", fake.build)
    return fake


async def _settle(hub, fake_sre, inv: str, extra_polls: int) -> None:  # noqa: ANN001
    """첫 poll 뒤 `extra_polls`회 더 묻기를 기다린다."""
    start = len(fake_sre.calls_for(inv))
    await wait_until(lambda: len(fake_sre.calls_for(inv)) >= start + extra_polls)


async def test_unchanged_polls_do_not_wake(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    job = FakeJob(events=[ev(1)])
    fake_sre.jobs["inv-a"] = job
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.cursor == 1)
        await _settle(hub, fake_sre, "inv-a", 1)
        version = hub.version
        for _ in range(4):  # 경과만 흐르는 poll
            job.elapsed_s += 5
            await _settle(hub, fake_sre, "inv-a", 1)
        assert hub.version == version
        assert entry.state.get("elapsed_s") == job.elapsed_s  # 값은 반영돼 있다
    finally:
        await hub.aclose()


async def test_changes_wake(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    job = FakeJob(events=[ev(1)])
    fake_sre.jobs["inv-a"] = job
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.cursor == 1)
        await _settle(hub, fake_sre, "inv-a", 1)

        v = hub.version
        job.events.append(ev(2))  # 새 이벤트
        await wait_until(lambda: hub.version > v)
        assert entry.cursor == 2

        v = hub.version
        job.phase = "analyzing"  # 단계 변경
        await wait_until(lambda: hub.version > v)

        v = hub.version
        job.partial = {"playbook": {"id": "pb"}}  # 부분 브리핑
        await wait_until(lambda: hub.version > v)

        v = hub.version
        fake_sre.fail.add("inv-a")  # 오류 설정 — 같은 사유가 되풀이돼도 1회
        await wait_until(lambda: entry.poll_error is not None)
        await _settle(hub, fake_sre, "inv-a", 3)
        assert hub.version == v + 1

        v = hub.version
        fake_sre.fail.clear()  # 오류 해제
        await wait_until(lambda: entry.poll_error is None)
        await wait_until(lambda: hub.version > v)

        v = hub.version
        job.status = "done"  # 종결
        await wait_until(lambda: entry.final)
        assert hub.version > v
    finally:
        await hub.aclose()


async def test_stream_refreshes_elapsed_without_wake(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    job = FakeJob(events=[ev(1)], elapsed_s=1)
    fake_sre.jobs["inv-a"] = job
    hub = make_hub(tmp_path)
    ctx = StreamContext(
        operator=True, mask_fn=None, in_scope=lambda r: True,
        watch={"inv-a": rec}, cursors={"inv-a": -1},
    )

    async def connected() -> bool:
        return False

    gen = stream_events(hub, ctx, connected)
    try:
        frames: list[str] = []

        async def read_until(pred) -> None:  # noqa: ANN001
            while True:
                frame = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
                frames.append(frame)
                if pred(frame):
                    return

        def activity_elapsed(frame: str) -> int | None:
            if not frame.startswith("id:") or "event: activity" not in frame:
                return None
            data = json.loads(frame.split("data: ", 1)[1])
            return data.get("elapsed_s")

        await read_until(lambda f: activity_elapsed(f) == 1)
        entry = hub.entry("inv-a")
        assert entry is not None
        await _settle(hub, fake_sre, "inv-a", 1)
        version = hub.version
        job.elapsed_s = 42
        await read_until(lambda f: activity_elapsed(f) == 42)
        assert hub.version == version  # 깨우지 않았는데도 새 경과가 나갔다
    finally:
        await gen.aclose()
        await hub.aclose()
