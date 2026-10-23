"""plans/187 v2.0 독립 검증(W14) — W13 실물 응답 교차 · 사용자 경로 누출 · 인증 · 허브 수명.

W13 교차: sre_agent venv 테스트(`sre_agent/tests/test_plan187_v2_verify_activity.py`)가
`PLAN187_V2_CONTRACT_DUMP`로 남긴 실물 `JobStore.get(id, activity_since)` 응답을 읽어 가짜
클라이언트로 재생한다. 파일이 없으면 그 테스트만 건너뛴다(두 venv라 파일로 넘긴다).
실 LLM·외부 호출 0.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.api import investigation_live_hub as hub_mod
from src.api.investigation_live_hub import InvestigationLiveHub, StreamContext, stream_events
from src.routing.zones import ZONE_GONGJON
from tests.test_api.test_plan187_w14_live_api import (
    ADMIN,
    GONGJON_DB,
    U1,
    _app,
    _close_hubs,  # noqa: F401 — autouse 픽스처 재사용
    _config,
    _get,
    _of,
    _short_stream,  # noqa: F401 — autouse 픽스처 재사용
    _stream,
)
from tests.test_api.test_plan187_w14_live_hub import (
    FakeJob,
    FakeSre,
    activity_of,
    ev,
    make_hub,
    seed,
    store_of,
    wait_until,
)

DUMP = Path(
    os.environ.get(
        "PLAN187_V2_CONTRACT_DUMP",
        "/private/tmp/claude-501/-Users-cptkang-AIOps-collectorinfra/"
        "88309dfc-8255-46e2-a89d-2315bf052bc2/scratchpad/contract_dump.json",
    )
)
RAW_SECRET = "Sup3rS3cret"
VISA = "4111111111111111"  # D-325 ③ 값 패턴(줄 단위) — 사용자 경로에서만 가려져야 한다


@pytest.fixture()
def fake_sre(monkeypatch: pytest.MonkeyPatch) -> FakeSre:
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod

    fake = FakeSre()
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", fake.build)
    return fake


# ── 의심 3: W13 실물 응답 재생 ─────────────────────────────────────────────────


class _Replay:
    """실물 응답 페이지를 `activity_since`로 잘라 돌려주는 가짜 연결."""

    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.stage = "queued"
        self.calls: list[int | None] = []

    def build(self, gate_cfg: Any) -> _Replay:  # noqa: ARG002
        return self

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def poll(self, investigation_id: str, activity_since: int | None = None) -> dict:
        self.calls.append(activity_since)
        page = json.loads(json.dumps(self.pages[{
            "queued": "queued_2", "running": "running_0", "done": "done_0"}[self.stage]]))
        if activity_since is None or activity_since < 0:
            for k in ("activity", "phase", "queue_position", "elapsed_s"):
                page.pop(k, None)
            return page
        page["activity"]["events"] = [
            e for e in page["activity"]["events"] if e["seq"] > activity_since
        ]
        return page


def _load_dump() -> dict[str, Any]:
    if not DUMP.exists():
        pytest.skip("W13 계약 덤프 없음 — sre_agent 검증 테스트를 먼저 돌린다")
    return json.loads(DUMP.read_text(encoding="utf-8"))


@pytest.mark.parametrize("who", ["user", "admin"])
async def test_w13_real_contract_flows_through_hub(tmp_path, monkeypatch, who):  # noqa: ANN001
    pages = _load_dump()
    replay = _Replay(pages)
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod

    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", replay.build)
    monkeypatch.setattr(hub_mod, "STREAM_MAX_SECONDS", 0.5)
    seed(tmp_path, "inv-real", kind="diagnosis", owner_sub="u1", origin="chat")
    app = _app(_config(tmp_path), U1 if who == "user" else ADMIN)

    # ① 대기 — phase·대기 순번
    f1 = await asyncio.wait_for(_stream(app, "inv-real:-1"), timeout=10)
    act1 = _of(f1, "activity")
    assert act1 and act1[-1]["data"]["phase"] == "queued"
    assert act1[-1]["data"]["queue_position"] == 1 and act1[-1]["data"]["status"] == "running"
    summ = _of(f1, "summary")[-1]["data"]["items"]
    assert summ and summ[0]["phase"] == "queued" and summ[0]["queue_position"] == 1
    # ② 진행 — 같은 허브에 커서로 이어 붙는다(Last-Event-ID)
    replay.stage = "running"
    await asyncio.sleep(0.1)
    f2 = await asyncio.wait_for(
        _stream(app, "inv-real:-1", headers={"Last-Event-ID": act1[-1]["id"]}), timeout=10)
    # ③ 종결
    replay.stage = "done"
    await asyncio.sleep(0.1)
    last_id = (_of(f2, "activity") or act1)[-1]["id"]
    f3 = await asyncio.wait_for(
        _stream(app, "inv-real:-1", headers={"Last-Event-ID": last_id}), timeout=10)

    frames = f1 + f2 + f3
    seqs = [e["seq"] for f in _of(frames, "activity") for e in f["data"]["events"]]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)), seqs  # 겹침 0
    done = _of(f3, "done")
    assert len(done) == 1 and done[0]["data"]["status"] == "done"
    assert done[0]["data"]["next_seq"] == pages["done_0"]["activity"]["next_seq"]
    kinds = [e["kind"] for f in _of(frames, "activity") for e in f["data"]["events"]]
    raw = json.dumps(frames, ensure_ascii=False)
    assert RAW_SECRET not in raw  # sre_agent 가림이 그대로 흐른다
    if who == "user":
        # 사용자(plans/187 W20 · G-48): ai_note도 받되 값 패턴(VISA)은 줄 단위 2차 마스킹
        assert "ai_note" in kinds
        assert VISA not in raw
        assert {1, 2, 3, 4, 5} <= set(seqs)  # 누락 없음
    else:
        assert "ai_note" in kinds and VISA in raw
        assert set(range(1, pages["done_0"]["activity"]["next_seq"] + 1)) <= set(seqs) | {1}
    # 끝: 조회 경로(activity_since)도 같은 규칙
    resp = await _get(app, "/alarm/investigations/inv-real", params={"activity_since": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["activity"]["source"] in ("live", "archive")
    assert "ai_note" in resp.text  # G-48 — 사용자·관리자 모두
    assert (VISA in resp.text) is (who == "admin")


# ── 의심 4: 사용자 경로 누출 ────────────────────────────────────────────────────


async def test_user_active_and_summary_never_carry_owner_or_foreign(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "chat-u1", kind="diagnosis", owner_sub="u1", origin="chat",
         alarm_ids=["a9"], alarm_db_ids={"a9": GONGJON_DB})
    seed(tmp_path, "chat-u2", kind="diagnosis", owner_sub="u2", origin="chat")
    seed(tmp_path, "auto-z", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    for inv in ("chat-u1", "chat-u2", "auto-z"):
        fake_sre.jobs[inv] = FakeJob(events=[ev(1), ev(2, kind="ai_note", note="메모")])
    app = _app(_config(tmp_path), U1)
    active = (await _get(app, "/alarm/investigations/active")).json()
    assert [i["investigation_id"] for i in active["items"]] == ["chat-u1"]
    for item in active["items"]:
        assert "owner_sub" not in item and "alarm_ids" not in item
    frames = await asyncio.wait_for(_stream(app, "chat-u1,auto-z"), timeout=10)
    for s in _of(frames, "summary"):
        assert {i["investigation_id"] for i in s["data"]["items"]} <= {"chat-u1"}
        assert all("owner_sub" not in i for i in s["data"]["items"])
    raw = json.dumps(frames, ensure_ascii=False)
    assert "chat-u2" not in raw


async def test_denied_reason_vs_single_get_for_zoneless_user(tmp_path, fake_sre):  # noqa: ANN001
    """존 없는 사용자: 단건 GET은 있음·없음 모두 403이고, 스트림 denied도 한 사유(`unavailable`)라
    존재 여부를 알려 주지 않는다(검증 Low 1 교정 — W14 교정 2)."""
    seed(tmp_path, "auto-x", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    nozone = {"sub": "u7", "role": "user", "alarm_zones": []}
    app = _app(_config(tmp_path), nozone)
    g1 = await _get(app, "/alarm/investigations/auto-x")
    g2 = await _get(app, "/alarm/investigations/nope-x")
    assert g1.status_code == g2.status_code == 403
    frames = await asyncio.wait_for(_stream(app, "auto-x,nope-x"), timeout=10)
    denied = {f["data"]["investigation_id"]: f["data"]["reason"] for f in _of(frames, "denied")}
    assert denied == {"auto-x": "unavailable", "nope-x": "unavailable"}


async def test_zone_user_single_get_already_distinguishes(tmp_path, fake_sre):  # noqa: ANN001
    """존 사용자: 단건 GET도 없음=200(status none) · 남의 존=403 — 종전부터 구분된다(비교 기준)."""
    seed(tmp_path, "bank-x", alarm_ids=["b1"], alarm_db_ids={"b1": "polestar_b0"})
    app = _app(_config(tmp_path), U1)
    assert (await _get(app, "/alarm/investigations/bank-x")).status_code == 403
    missing = await _get(app, "/alarm/investigations/nope-y")
    assert missing.status_code == 200 and missing.json() == {"status": "none"}


# ── 의심 5: 개발 모드 익명 ──────────────────────────────────────────────────────


async def test_auth_disabled_anonymous_is_operator_scope(tmp_path, fake_sre):  # noqa: ANN001
    """인증 꺼짐(개발 모드) — 익명이 관리자 범위·ai_note를 받는다(종전 `_is_alarm_operator`와 같은
    전제 · 운영 배포에서 인증을 끄면 전부 노출됨을 고정)."""
    seed(tmp_path, "chat-u2", kind="diagnosis", owner_sub="u2", origin="chat")
    fake_sre.jobs["chat-u2"] = FakeJob(events=[ev(1, kind="ai_note", note="메모")])
    config = _config(tmp_path)
    config.auth.enabled = False
    app = _app(config)  # 인증 대역 없음
    active = await _get(app, "/alarm/investigations/active")
    assert active.status_code == 200
    item = active.json()["items"][0]
    assert item["owner_sub"] == "u2"


# ── 의심 7: 허브 수명 ───────────────────────────────────────────────────────────


async def test_stream_release_on_disconnect_and_on_cancel(tmp_path, fake_sre):  # noqa: ANN001
    rec = seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    fake_sre.jobs["inv-a"] = FakeJob(events=[ev(1)])
    hub = make_hub(tmp_path, poll_seconds=0.05)
    try:
        flag = {"gone": False}

        async def disc() -> bool:
            return flag["gone"]

        def ctx() -> StreamContext:
            return StreamContext(operator=True, mask_fn=None, in_scope=lambda r: True,
                                 watch={"inv-a": rec}, cursors={"inv-a": -1})

        # (a) is_disconnected 경로
        gen = stream_events(hub, ctx(), disc)
        got = [await gen.__anext__() for _ in range(2)]
        assert got
        await wait_until(lambda: (hub.entry("inv-a") or object()) and
                         getattr(hub.entry("inv-a"), "subscribers", 0) == 1)
        flag["gone"] = True
        with pytest.raises(StopAsyncIteration):
            while True:
                await asyncio.wait_for(gen.__anext__(), timeout=2)
        assert hub.entry("inv-a").subscribers == 0
        # (b) 서버가 제너레이터를 닫음(취소) 경로
        flag["gone"] = False
        gen2 = stream_events(hub, ctx(), disc)
        await gen2.__anext__()
        await gen2.__anext__()
        await wait_until(lambda: hub.entry("inv-a").subscribers == 1)
        await gen2.aclose()
        assert hub.entry("inv-a").subscribers == 0
    finally:
        await hub.aclose()


async def test_poller_survives_malformed_response(tmp_path, monkeypatch):  # noqa: ANN001
    """예상 밖 응답 모양(activity 문자열 · events 쓰레기)도 그 조사만 실패/무시 · 폴러 생존."""
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod

    calls: list[int | None] = []

    class _Bad:
        def build(self, _cfg: Any) -> _Bad:
            return self

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def poll(self, inv: str, activity_since: int | None = None) -> dict:
            calls.append(activity_since)
            if len(calls) == 1:
                return {"status": "running", "activity": "garbage", "phase": ["x"]}
            if len(calls) == 2:
                return {"status": "running",
                        "activity": {"events": [None, {"seq": "1"}, {"seq": True}, ev(1)]}}
            return {"status": "running", "activity": {"events": [ev(1), ev(2)]}}

    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", _Bad().build)
    rec = seed(tmp_path, "inv-m", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    hub = make_hub(tmp_path, poll_seconds=0.02)
    try:
        hub.set_interest("t", {"inv-m": rec})
        await wait_until(lambda: len(calls) >= 3)
        entry = hub.entry("inv-m")
        assert [e["seq"] for e in entry.events] == [1, 2]
        assert entry.running
    finally:
        hub.release("t")
        await hub.aclose()


async def test_terminal_tail_misses_late_finalize(tmp_path, fake_sre):  # noqa: ANN001
    """종결을 본 뒤 추가 1회 읽기 이후에 붙은 이벤트(sre_agent는 status=done 대입 뒤에 finalize를
    덧붙인다)는 허브가 다시 묻지 않아 버퍼·보관 요약에서 빠진다 — 현 동작 고정(보고 지적)."""
    rec = seed(tmp_path, "inv-t", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    job = FakeJob(events=[ev(1)], status="done")
    fake_sre.jobs["inv-t"] = job
    hub = make_hub(tmp_path, poll_seconds=0.02)
    try:
        hub.set_interest("t", {"inv-t": rec})
        await wait_until(lambda: hub.entry("inv-t").final)
        job.events.append(ev(2, kind="finalize", engine="system"))  # 늦게 붙은 finalize
        await asyncio.sleep(0.1)
        assert [e["seq"] for e in hub.entry("inv-t").events] == [1]
        summary = activity_of(tmp_path).get_summary("inv-t")
        assert summary is not None and [e["seq"] for e in summary["events"]] == [1]
    finally:
        hub.release("t")
        await hub.aclose()


def test_store_helpers_importable() -> None:
    # 보조 import 사용 확인(린트용) — store_of/InvestigationLiveHub 시그니처 회귀 감시
    assert callable(store_of) and InvestigationLiveHub.__init__ is not None
    assert ZONE_GONGJON


# ── 의심 8: 화면 ↔ sre_agent phase 어휘 ─────────────────────────────────────────


def test_frontend_phase_labels_cover_sre_agent_phases() -> None:
    import re

    root = Path(__file__).resolve().parents[2]
    domain = (root / "sre_agent/sre_agent/domain/activity_log.py").read_text(encoding="utf-8")
    m = re.search(r"PHASES: tuple\[str, \.\.\.\] = \(([^)]*)\)", domain)
    assert m
    phases = set(re.findall(r'"([a-z_]+)"', m.group(1)))
    js = (root / "src/static/js/diagnosis-view.js").read_text(encoding="utf-8")
    lm = re.search(r"var PHASE_LABELS = \{([^}]*)\}", js)
    assert lm
    labels = set(re.findall(r"([a-z_]+)\s*:", lm.group(1)))
    assert phases <= labels, sorted(phases - labels)
