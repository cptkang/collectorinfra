"""plans/187 W14 — 본체 실시간 허브(`InvestigationLiveHub`) 단위 검증 (§5.6.3 ⓒ · G-37·G-39).

- 구독자 3명이 같은 조사를 봐도 sre_agent poll은 간격당 1회(폴러 1개)
- 구독 0이 이어지면 폴러 정지 · 버퍼 TTL sweep
- 폴러 실패는 그 조사에만 「갱신 실패 — 사유」 · 다른 조사는 정상 · 실패한 조사도 계속 묻는다
- 커서 단조 · sre_agent가 겹쳐 보내도 버퍼 중복 0 · 구버전(키 없음) 응답도 깨지지 않는다
- 종결: 마지막 1회 추가 읽기 → 저장소 종결 행 · 요약 JSONL 1행(발췌·AI 메모 제외)
- 재기동(새 허브) 후 잡 TTL 경과(not_found) → 요약 JSONL로 실행 기록 재조회
- `SreAgentClient.poll`: 커서 미지정이면 종전 인자 그대로

조사 서비스는 띄우지 않는다 — 가짜 클라이언트(잡 상태를 테스트가 바꾼다)를 주입한다. 실 LLM 0.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from noise_gate.infrastructure import sre_agent_client as sre_client_mod
from noise_gate.infrastructure.investigation_activity_store import (
    InvestigationActivityStore,
)
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api import investigation_live_hub as hub_mod
from src.api.investigation_live_hub import InvestigationLiveHub

SECRET = "sk-abcdefghijklmnopqrstuvwx"  # DataMasker 값 패턴(API 키)


@dataclass
class FakeJob:
    """sre_agent 잡 대역 — 테스트가 상태·이벤트를 바꾼다."""

    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    phase: str | None = "collecting"
    elapsed_s: int = 0
    honor_cursor: bool = True
    legacy: bool = False  # 구버전 sre_agent — activity 등 키 없음
    partial: dict[str, Any] | None = None


class FakeSre:
    """`build_sre_agent_client` 대역 — poll 호출을 기록하고 잡 상태로 응답한다."""

    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.calls: list[tuple[str, int | None]] = []
        self.fail: set[str] = set()

    def build(self, gate_cfg: Any) -> _FakeConn:  # noqa: ARG002
        return _FakeConn(self)

    def calls_for(self, investigation_id: str) -> list[int | None]:
        return [since for inv, since in self.calls if inv == investigation_id]


class _FakeConn:
    def __init__(self, fake: FakeSre) -> None:
        self.fake = fake

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def poll(self, investigation_id: str, activity_since: int | None = None) -> dict:
        self.fake.calls.append((investigation_id, activity_since))
        if investigation_id in self.fake.fail:
            raise RuntimeError("boom")
        job = self.fake.jobs.get(investigation_id)
        if job is None:
            return {"status": "not_found"}
        res: dict[str, Any] = {"status": job.status}
        if job.status == "done":
            res["briefing"] = {"summary": "원인 확인", "limitations": []}
        if job.partial is not None:
            res["partial_briefing"] = job.partial
        if activity_since is None or activity_since < 0 or job.legacy:
            return res
        since = activity_since if job.honor_cursor else -1
        events = [dict(e) for e in job.events if e["seq"] > since]
        res["activity"] = {
            "next_seq": job.events[-1]["seq"] if job.events else 0,
            "truncated": False,
            "events": events,
        }
        res["phase"] = job.phase
        res["queue_position"] = None
        res["elapsed_s"] = job.elapsed_s
        return res


def ev(seq: int, kind: str = "tool_end", **extra: Any) -> dict[str, Any]:
    """이벤트 1건(계약 모양)."""
    base = {
        "seq": seq, "at": f"2026-10-19T00:00:{seq:02d}", "engine": "playbook", "kind": kind,
        "step": "S1", "tool": "apm_app_health", "label": f"이벤트 {seq}", "status": "ok",
        "elapsed_ms": 10 * seq, "rows": seq,
    }
    base.update(extra)
    return base


@pytest.fixture()
def fake_sre(monkeypatch: pytest.MonkeyPatch) -> FakeSre:
    fake = FakeSre()
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", fake.build)
    return fake


class _Ng:
    """noise_gate 설정 대역(허브가 읽는 키만)."""

    investigation_mcp_call_timeout_seconds = 2.0
    investigation_live_poll_seconds = 0.05
    investigation_service_url = "http://127.0.0.1:9/sse"


def store_of(tmp_path) -> InvestigationStore:  # noqa: ANN001
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


def activity_of(tmp_path) -> InvestigationActivityStore:  # noqa: ANN001
    return InvestigationActivityStore(tmp_path / "investigation_activity.jsonl")


def seed(tmp_path, inv: str, *, status: str = "submitted", **links: Any) -> dict:  # noqa: ANN001
    store = store_of(tmp_path)
    store.record(
        investigation_id=inv, alarm_ids=links.pop("alarm_ids", []), kind=links.pop("kind", "alarm"),
        status=status, source="test", **links,
    )
    rec = store.get_by_investigation(inv)
    assert rec is not None
    return rec


def make_hub(tmp_path, **kw: Any) -> InvestigationLiveHub:  # noqa: ANN001
    return InvestigationLiveHub(
        _Ng(),
        store_factory=lambda: store_of(tmp_path),
        activity_store=activity_of(tmp_path),
        **kw,
    )


async def wait_until(pred, timeout: float = 3.0) -> None:  # noqa: ANN001
    """조건이 참이 될 때까지 기다린다(무한 대기 가드)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("시간 안에 조건이 성립하지 않음")
        await asyncio.sleep(0.01)


# ── 폴러 1개 · 간격당 1회 ────────────────────────────────────────────────────


async def test_three_subscribers_share_one_poller(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    fake_sre.jobs["inv-a"] = FakeJob(events=[ev(1)])
    hub = make_hub(tmp_path)
    tokens = [object(), object(), object()]
    try:
        for token in tokens:
            hub.set_interest(token, {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None and entry.subscribers == 3
        await asyncio.sleep(0.5)
        polls = len(fake_sre.calls_for("inv-a"))
        # 0.5초 / 0.05초 간격 ≈ 10회 — 구독자 수(3)를 곱한 횟수가 아니다.
        assert 3 <= polls <= 13
        assert entry.running
    finally:
        await hub.aclose()


async def test_zero_subscribers_stops_poller_and_sweeps(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    fake_sre.jobs["inv-a"] = FakeJob()
    hub = make_hub(tmp_path, idle_stop_seconds=0.1, buffer_ttl_seconds=0.2)
    token = object()
    try:
        hub.set_interest(token, {"inv-a": rec})
        await wait_until(lambda: len(fake_sre.calls_for("inv-a")) >= 1)
        hub.release(token)
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: not entry.running)
        stopped_at = len(fake_sre.calls_for("inv-a"))
        await asyncio.sleep(0.2)
        assert len(fake_sre.calls_for("inv-a")) == stopped_at  # 멈춘 뒤 poll 0
        hub.sweep()
        assert hub.entry("inv-a") is None  # 버퍼 TTL 경과 → sweep
    finally:
        await hub.aclose()


# ── 실패 격리 ────────────────────────────────────────────────────────────────


async def test_poll_failure_marks_only_that_investigation(tmp_path, fake_sre):  # noqa: ANN001
    rec_a = seed(tmp_path, "inv-a")
    rec_b = seed(tmp_path, "inv-b")
    fake_sre.jobs["inv-b"] = FakeJob(events=[ev(1)])
    fake_sre.fail.add("inv-a")
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec_a, "inv-b": rec_b})
        await wait_until(lambda: len(fake_sre.calls_for("inv-a")) >= 3)
        a, b = hub.entry("inv-a"), hub.entry("inv-b")
        assert a is not None and b is not None
        assert a.poll_error and a.poll_error.startswith(hub_mod.POLL_ERROR_PREFIX)
        assert "RuntimeError" in a.poll_error
        assert a.running  # 침묵 정지 금지 — 다음 주기에 다시 묻는다
        assert b.poll_error is None and [e["seq"] for e in b.events] == [1]
        fake_sre.fail.clear()
        fake_sre.jobs["inv-a"] = FakeJob()
        await wait_until(lambda: a.poll_error is None)
    finally:
        await hub.aclose()


# ── 커서 · 중복 · 구버전 ─────────────────────────────────────────────────────


async def test_cursor_monotonic_and_no_duplicates(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    job = FakeJob(events=[ev(1), ev(2)], honor_cursor=False)  # 서비스가 겹쳐 보내도
    fake_sre.jobs["inv-a"] = job
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.cursor == 2)
        job.events.append(ev(3))
        await wait_until(lambda: entry.cursor == 3)
        await asyncio.sleep(0.15)
        assert [e["seq"] for e in entry.events] == [1, 2, 3]
        sinces = [s for s in fake_sre.calls_for("inv-a")]
        assert sinces[0] == 0  # 재기동·첫 구독은 커서 0부터
        assert sinces == sorted(sinces)  # 단조
        assert entry.source == "live"
    finally:
        await hub.aclose()


async def test_legacy_service_without_activity_keys(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    fake_sre.jobs["inv-a"] = FakeJob(legacy=True)
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.polled_at is not None)
        payload = hub.activity_payload(entry, -1, operator=True, mask_fn=None)
        assert payload["activity"] == {
            "next_seq": 0, "truncated": False, "source": "none", "events": [],
        }
        assert payload["phase"] is None and entry.poll_error is None
    finally:
        await hub.aclose()


async def test_buffer_cap_sets_truncated(tmp_path, fake_sre, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(hub_mod, "ACTIVITY_MAX_EVENTS", 3)
    rec = seed(tmp_path, "inv-a")
    fake_sre.jobs["inv-a"] = FakeJob(events=[ev(i) for i in range(1, 6)])
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.cursor == 5)
        assert [e["seq"] for e in entry.events] == [3, 4, 5] and entry.truncated
    finally:
        await hub.aclose()


# ── 종결 · 보관 · 재기동 ─────────────────────────────────────────────────────


async def test_terminal_reads_once_more_persists_and_archives(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    job = FakeJob(events=[ev(1, excerpt=SECRET), ev(2, kind="ai_note", note="커넥션 대기 의심")])
    fake_sre.jobs["inv-a"] = job
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.cursor == 2)
        job.status = "done"
        job.events.append(ev(3, kind="finalize"))
        await wait_until(lambda: entry.final)
        assert [e["seq"] for e in entry.events] == [1, 2, 3]
        assert not entry.running
        # 종결 응답 뒤 1회 더 읽었다(마지막 두 호출의 커서가 같은 종결 주기).
        assert len(fake_sre.calls_for("inv-a")) >= 2
        stored = store_of(tmp_path).get_by_investigation("inv-a")
        assert stored is not None and stored["status"] == "done"
        assert stored["source"] == "api_poll" and entry.status == "done"
        lines = (tmp_path / "investigation_activity.jsonl").read_text("utf-8").splitlines()
        assert len(lines) == 1
        archived = json.loads(lines[0])
        assert archived["investigation_id"] == "inv-a" and archived["status"] == "done"
        assert [e["seq"] for e in archived["events"]] == [1, 3]  # AI 메모 제외
        assert SECRET not in lines[0] and "excerpt" not in lines[0] and "note" not in archived
        polls = len(fake_sre.calls_for("inv-a"))
        await asyncio.sleep(0.15)
        assert len(fake_sre.calls_for("inv-a")) == polls  # 종결 뒤 poll 0
    finally:
        await hub.aclose()


async def test_restart_after_job_ttl_reads_archive(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    job = FakeJob(events=[ev(1, excerpt=SECRET)], status="running")
    fake_sre.jobs["inv-a"] = job
    hub = make_hub(tmp_path)
    try:
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        job.status = "done"
        await wait_until(lambda: entry.final)
    finally:
        await hub.aclose()
    # 재기동 — 허브 비어 있음 · 잡은 TTL로 사라짐.
    del fake_sre.jobs["inv-a"]
    done_rec = store_of(tmp_path).get_by_investigation("inv-a")
    assert done_rec is not None and done_rec["status"] == "done"
    hub2 = make_hub(tmp_path)
    try:
        entry2 = await hub2.snapshot(done_rec)
        assert entry2.final and entry2.source == "archive"
        payload = hub2.activity_payload(entry2, -1, operator=True, mask_fn=None)
        assert [e["seq"] for e in payload["activity"]["events"]] == [1]
        assert "excerpt" not in payload["activity"]["events"][0]
        # 같은 허브에서 다시 물어도 sre_agent를 또 부르지 않는다(버퍼 재사용).
        calls = len(fake_sre.calls)
        await hub2.snapshot(done_rec)
        assert len(fake_sre.calls) == calls
    finally:
        await hub2.aclose()


async def test_unwatched_terminal_archived_once(tmp_path, fake_sre):  # noqa: ANN001
    """아무도 안 볼 때 끝난 조사 — TTL 안에 처음 읽으면 요약을 남기고 다시 읽어도 1행(Medium 3)."""
    done_rec = seed(tmp_path, "inv-a", status="done")
    fake_sre.jobs["inv-a"] = FakeJob(
        status="done", events=[ev(1, excerpt=SECRET), ev(2, kind="ai_note", note="메모")]
    )
    archive = tmp_path / "investigation_activity.jsonl"
    hub = make_hub(tmp_path)
    try:
        entry = await hub.snapshot(done_rec)
        assert entry.final and entry.source == "live"
        lines = archive.read_text("utf-8").splitlines()
        assert len(lines) == 1
        assert [e["seq"] for e in json.loads(lines[0])["events"]] == [1]  # AI 메모 제외
        assert SECRET not in lines[0]
    finally:
        await hub.aclose()
    # 재기동한 허브가 잡 TTL 안에 다시 읽어도 이미 보관한 조사는 건너뛴다.
    hub2 = make_hub(tmp_path)
    try:
        entry2 = await hub2.snapshot(done_rec)
        assert entry2.final and entry2.source == "live"
        assert len(archive.read_text("utf-8").splitlines()) == 1
    finally:
        await hub2.aclose()


async def test_snapshot_sweeps_expired_buffers(tmp_path, fake_sre):  # noqa: ANN001
    """`activity_since` 조회만 반복돼도 TTL 지난 종결 버퍼가 지워진다(검증 Low 5)."""
    now = [1000.0]
    hub = make_hub(tmp_path, clock=lambda: now[0], buffer_ttl_seconds=10)
    old = seed(tmp_path, "inv-old", status="done")
    fake_sre.jobs["inv-old"] = FakeJob(status="done", events=[ev(1)])
    other = seed(tmp_path, "inv-b", status="done")
    fake_sre.jobs["inv-b"] = FakeJob(status="done", events=[ev(1)])
    try:
        await hub.snapshot(old)
        assert hub.entry("inv-old") is not None
        now[0] += 11
        await hub.snapshot(other)
        assert hub.entry("inv-old") is None and hub.entry("inv-b") is not None
    finally:
        await hub.aclose()


async def test_restart_resumes_active_from_store_cursor_zero(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "inv-a")
    seed(tmp_path, "inv-done", status="done")
    fake_sre.jobs["inv-a"] = FakeJob(events=[ev(1), ev(2)])
    hub = make_hub(tmp_path)
    try:
        active = hub.active_records(fresh=True)
        assert [r["investigation_id"] for r in active] == ["inv-a"]
        hub.set_interest(object(), {r["investigation_id"]: r for r in active})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.cursor == 2)
        assert fake_sre.calls_for("inv-a")[0] == 0
    finally:
        await hub.aclose()


# ── 사용자 경로 이벤트 ───────────────────────────────────────────────────────


def test_user_events_keep_ai_note_masked():
    """(plans/187 W20 · G-48) HolmesGPT AI 메모는 사용자에게 간다 — `note`는 가린 뒤(G-38 개정).
    플레이북 소견 초안(engine=playbook `ai_note` · 가드 전)은 사용자 경로에서 뺀다."""
    events = [
        ev(1, excerpt=f"x\n{SECRET}", label=SECRET),
        ev(2, kind="ai_note", engine="holmes", note=f"원인 추정\n{SECRET}"),
        ev(3, note="메모 키"),
        ev(4, kind="ai_note", note="가드 전 초안"),
    ]
    out = hub_mod.user_events(events, lambda s: "***" if s == SECRET else s)
    assert [e["seq"] for e in out] == [1, 2, 3]
    assert out[0]["excerpt"] == "x\n***" and out[0]["label"] == "***"
    assert out[1]["kind"] == "ai_note" and out[1]["note"] == "원인 추정\n***"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a:3,b", {"a": 3, "b": -1}),
        ("a:3,a:5", {"a": 5}),
        ("a:,b:x", {"a": -1, "b": -1}),
        ("", {}),
        (":4", {}),
    ],
    ids=["seq-and-bare", "max-wins", "bad-seq", "empty", "no-id"],
)
def test_parse_watch(raw, expected):  # noqa: ANN001
    assert hub_mod.parse_watch(raw) == expected


def test_last_event_id_only_advances_watched():
    cursors = {"a": 1, "b": 7}
    hub_mod.merge_last_event_id(cursors, "a:4,b:2,c:9")
    assert cursors == {"a": 4, "b": 7}


# ── 목록 엔진 칸(W14 교정) ───────────────────────────────────────────────────


async def test_list_item_engines_from_buffer_events(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a")
    events = [ev(1), ev(2, engine="holmes"), ev(3, engine="other"), ev(4)]
    fake_sre.jobs["inv-a"] = FakeJob(events=events)
    hub = make_hub(tmp_path)
    try:
        assert hub.summary_item(rec, operator=True, mask_fn=None)["engines"] == []
        hub.set_interest(object(), {"inv-a": rec})
        entry = hub.entry("inv-a")
        assert entry is not None
        await wait_until(lambda: entry.cursor == 4)
        items = hub.list_items([rec], operator=False, mask_fn=None)
        assert items[0]["engines"] == ["holmes", "playbook"]  # 정렬 집합 · 그 밖의 엔진 제외
    finally:
        await hub.aclose()


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        ({"done": 1, "total": 3, "engine": "playbook"}, ["playbook"]),
        ({"engines": ["playbook", "holmes", "x"]}, ["holmes", "playbook"]),
        ({"done": 1, "total": 3}, []),
        (None, []),
    ],
    ids=["progress-engine", "progress-engines", "progress-no-engine", "no-progress"],
)
def test_engines_fallback_to_progress(progress, expected):  # noqa: ANN001
    entry = hub_mod.LiveEntry(investigation_id="inv-a", rec={})
    if progress is not None:
        entry.live["progress"] = progress
    assert hub_mod._engines_of(entry) == expected


# ── 클라이언트 인자 ──────────────────────────────────────────────────────────


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, dict(arguments)))
        return '{"status": "running"}'


async def test_client_poll_arguments_unchanged_without_cursor():
    client = sre_client_mod.SreAgentClient("http://127.0.0.1:9/sse")
    session = _Session()
    client._mcp_session = session
    client._connected = True
    await client.poll("inv-a")
    await client.poll("inv-a", activity_since=4)
    assert session.calls == [
        ("sre_get_investigation", {"investigation_id": "inv-a"}),
        ("sre_get_investigation", {"investigation_id": "inv-a", "activity_since": 4}),
    ]
