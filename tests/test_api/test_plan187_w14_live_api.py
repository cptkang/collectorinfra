"""plans/187 W14 — 조사 실시간 SSE·목록·조회 경로 (§5.6.3 ⓒⓔ · G-36·G-38·G-41·G-42 · D-325 ③).

- 플래그 off: stream·active 404(인증보다 먼저) · 조회 경로 `activity_since` 무시(응답 비트 동일)
- 목록 범위: 사용자 = 자기 채팅 조사만(남의 채팅·자동·알람에서 연 조사 0건) · 관리자 = 전부
- watch: 존 밖·없는 id → `denied`(사용자 `unavailable` · 운영자 구분)
  · watch 밖 조사의 `activity` 0건
- 사용자 경로: 한 줄·발췌 D-325 ③ 마스킹 · `ai_note` 0건 / 관리자 경로: `ai_note` 있음
- 재연결: `watch` 커서·`Last-Event-ID`로 누락·중복 0
- 종결 뒤(잡 TTL 경과) `activity_since` 조회 → 요약 JSONL
- 인증: 토큰 없음·종류 다른 토큰·만료 401 · 운영자 토큰은 헤더로만(쿼리 401) · 사용자 토큰 type 검증

스트림은 최대 수명을 짧게 줄여 스스로 끝나게 하고, 읽기 전체를 `asyncio.wait_for`로 묶는다
(무한 대기 가드).
조사 서비스는 가짜 클라이언트 · 실 LLM·외부 호출 0.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI

from src.api import investigation_live_hub as hub_mod
from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api.test_plan153_w2_investigation_api import _config as _w2_config
from tests.test_api.test_plan153_w2_investigation_api import _PollClient
from tests.test_api.test_plan187_w14_live_hub import (
    SECRET,
    FakeJob,
    FakeSre,
    ev,
    seed,
)

GONGJON_DB = "polestar_cm_gp"
BANKJON_DB = "polestar_b0"
ADMIN = {"sub": "admin1", "role": "admin"}
U1 = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
USER_SECRET = "user-secret-for-tests-0123456789abcdef"
ADMIN_SECRET = "admin-secret-for-tests-0123456789abcdef"


@pytest.fixture()
def fake_sre(monkeypatch: pytest.MonkeyPatch) -> FakeSre:
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod

    fake = FakeSre()
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", fake.build)
    return fake


@pytest.fixture(autouse=True)
def _short_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    # 스트림이 스스로 끝나게 한다(ASGI 전송은 응답 완료까지 모은다).
    monkeypatch.setattr(hub_mod, "STREAM_MAX_SECONDS", 0.6)


def _config(tmp_path, *, live: bool = True):  # noqa: ANN001, ANN202
    config = _w2_config(tmp_path)
    ng = config.noise_gate
    ng.investigation_live_view_enabled = live
    ng.investigation_live_poll_seconds = 0.05
    ng.sre_chat_investigation_enabled = True
    ng.investigation_manual_enabled = True
    ng.investigation_additional_enabled = False
    config.auth.jwt_secret = USER_SECRET
    config.admin = SimpleNamespace(jwt_secret=ADMIN_SECRET)
    config.host_authz = SimpleNamespace(mode="admin_only")
    return config


_APPS: list[FastAPI] = []


@pytest.fixture(autouse=True)
async def _close_hubs():
    """테스트가 만든 허브의 폴러를 정리한다(이벤트 루프 종료 전에)."""
    yield
    while _APPS:
        hub = getattr(_APPS.pop().state, "investigation_live_hub", None)
        if hub is not None:
            await hub.aclose()


def _app(config, user: dict | None = None) -> FastAPI:  # noqa: ANN001
    app = FastAPI()
    _APPS.append(app)
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    if user is not None:
        app.dependency_overrides[alarm_routes.require_live_user] = lambda: user
        app.dependency_overrides[require_user] = lambda: user
    return app


async def _get(
    app: FastAPI, path: str, *, cookies: dict[str, str] | None = None, **kw: Any
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t", cookies=cookies
    ) as client:
        return await asyncio.wait_for(client.get(f"/api/v1{path}", **kw), timeout=10)


def _frames(text: str) -> list[dict[str, Any]]:
    """SSE 본문 → [{event, id, data}]."""
    out: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        frame: dict[str, Any] = {}
        for line in block.split("\n"):
            if line.startswith("event: "):
                frame["event"] = line[7:]
            elif line.startswith("id: "):
                frame["id"] = line[4:]
            elif line.startswith("data: "):
                frame["data"] = json.loads(line[6:])
        if "event" in frame:
            out.append(frame)
    return out


def _of(frames: list[dict], event: str) -> list[dict]:
    return [f for f in frames if f["event"] == event]


async def _stream(app: FastAPI, watch: str | None = None, **kw: Any) -> list[dict[str, Any]]:
    params = {"watch": watch} if watch is not None else {}
    resp = await _get(app, "/alarm/investigations/stream", params=params, **kw)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return _frames(resp.text)


def _seed_scope(tmp_path) -> None:  # noqa: ANN001
    seed(tmp_path, "chat-u1", kind="manual", owner_sub="u1", origin="chat",
         subject={"hostname": "wasdemo01", "note": SECRET})
    seed(tmp_path, "chat-u9", kind="diagnosis", owner_sub="u9", origin="chat")
    seed(tmp_path, "auto-1", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    seed(tmp_path, "man-u1", kind="manual", owner_sub="u1", origin="manual",
         alarm_ids=["a2"], alarm_db_ids={"a2": GONGJON_DB}, parent_alarm_id="a2")


# ── 플래그 off ───────────────────────────────────────────────────────────────


async def test_flag_off_routes_404_before_auth(tmp_path):  # noqa: ANN001
    app = _app(_config(tmp_path, live=False))  # 인증 대역 없음 — 404가 먼저
    assert (await _get(app, "/alarm/investigations/stream")).status_code == 404
    assert (await _get(app, "/alarm/investigations/active")).status_code == 404


async def test_flag_off_activity_since_ignored_bit_identical(tmp_path, monkeypatch):  # noqa: ANN001
    from noise_gate.infrastructure import sre_agent_client as sre_client_mod

    seed(tmp_path, "inv-1", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    # 종전 대역(poll 인자 1개) — 커서를 실으면 TypeError로 응답이 달라진다.
    fake = _PollClient({"status": "running", "progress": {"done": 1, "total": 3}})
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda ng: fake)
    from src.api import investigation_poll

    investigation_poll.POLL_AT.clear()
    investigation_poll.POLL_LOCKS.clear()
    investigation_poll.POLL_LIVE.clear()
    app = _app(_config(tmp_path, live=False), ADMIN)
    await _get(app, "/alarm/investigations/inv-1")  # 첫 조회 = poll 1회
    with_cursor = await _get(app, "/alarm/investigations/inv-1", params={"activity_since": 0})
    plain = await _get(app, "/alarm/investigations/inv-1")  # 같은 간격 안 — 같은 조건의 대조군
    by_alarm = await _get(app, "/alarm/investigations/by-alarm/a1", params={"activity_since": 0})
    assert plain.status_code == with_cursor.status_code == by_alarm.status_code == 200
    assert plain.content == with_cursor.content
    assert "activity" not in with_cursor.json() and "activity" not in by_alarm.json()
    assert fake.poll_calls == ["inv-1"]  # 간격 안 재요청은 종전 지연 poll 규칙 그대로
    assert getattr(app.state, "investigation_live_hub", None) is None
    investigation_poll.POLL_AT.clear()
    investigation_poll.POLL_LOCKS.clear()
    investigation_poll.POLL_LIVE.clear()


# ── 목록 범위 ────────────────────────────────────────────────────────────────


async def test_user_summary_only_own_chat(tmp_path, fake_sre):  # noqa: ANN001
    _seed_scope(tmp_path)
    for inv in ("chat-u1", "chat-u9", "auto-1", "man-u1"):
        fake_sre.jobs[inv] = FakeJob()
    app = _app(_config(tmp_path), U1)
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    summaries = _of(frames, "summary")
    assert summaries
    ids = {item["investigation_id"] for s in summaries for item in s["data"]["items"]}
    assert ids == {"chat-u1"}
    item = summaries[-1]["data"]["items"][0]
    assert SECRET not in json.dumps(item) and "owner_sub" not in item
    active = await _get(app, "/alarm/investigations/active")
    assert active.status_code == 200
    assert [i["investigation_id"] for i in active.json()["items"]] == ["chat-u1"]
    # 사용자 스트림은 남의 조사를 폴링하지 않는다(폴러는 관심 조사만).
    assert not fake_sre.calls_for("chat-u9") and not fake_sre.calls_for("auto-1")


async def test_admin_summary_all(tmp_path, fake_sre):  # noqa: ANN001
    _seed_scope(tmp_path)
    for inv in ("chat-u1", "chat-u9", "auto-1", "man-u1"):
        fake_sre.jobs[inv] = FakeJob(elapsed_s=3)
    app = _app(_config(tmp_path), ADMIN)
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    last = _of(frames, "summary")[-1]["data"]["items"]
    assert {i["investigation_id"] for i in last} == {"chat-u1", "chat-u9", "auto-1", "man-u1"}
    by_id = {i["investigation_id"]: i for i in last}
    assert by_id["chat-u1"]["owner_sub"] == "u1" and SECRET in json.dumps(by_id["chat-u1"])
    assert by_id["auto-1"]["phase"] == "collecting" and by_id["auto-1"]["elapsed_s"] == 3
    active = await _get(app, "/alarm/investigations/active")
    assert {i["investigation_id"] for i in active.json()["items"]} == {
        "chat-u1", "chat-u9", "auto-1", "man-u1",
    }


# ── watch 권한 · 범위 ────────────────────────────────────────────────────────


async def test_watch_out_of_zone_and_missing_denied(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "bank-1", alarm_ids=["b1"], alarm_db_ids={"b1": BANKJON_DB})
    seed(tmp_path, "gong-1", alarm_ids=["g1"], alarm_db_ids={"g1": GONGJON_DB})
    fake_sre.jobs["gong-1"] = FakeJob(events=[ev(1)])
    fake_sre.jobs["bank-1"] = FakeJob(events=[ev(1)])
    app = _app(_config(tmp_path), U1)
    frames = await asyncio.wait_for(
        _stream(app, "bank-1:-1,nope:0,gong-1,bad%0Aid"), timeout=10
    )
    denied = {f["data"]["investigation_id"]: f["data"]["reason"] for f in _of(frames, "denied")}
    # 사용자 경로: 없음·권한 없음을 한 사유로 합친다(남의 id 존재 비누출 · 검증 Low 1)
    assert denied["bank-1"] == "unavailable" and denied["nope"] == "unavailable"
    assert "invalid" in denied.values()
    acts = _of(frames, "activity")
    assert acts and {f["data"]["investigation_id"] for f in acts} == {"gong-1"}
    assert not fake_sre.calls_for("bank-1")  # 거부된 조사는 폴링하지 않는다
    # 알람에서 연 조사는 목록 밖이지만 watch로는 열린다(존 검사 통과).
    assert all(not s["data"]["items"] for s in _of(frames, "summary"))


async def test_unwatched_investigation_gets_no_activity(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    seed(tmp_path, "inv-b", alarm_ids=["a2"], alarm_db_ids={"a2": GONGJON_DB})
    fake_sre.jobs["inv-a"] = FakeJob(events=[ev(1), ev(2)])
    fake_sre.jobs["inv-b"] = FakeJob(events=[ev(1), ev(2)])
    app = _app(_config(tmp_path), ADMIN)
    frames = await asyncio.wait_for(_stream(app, "inv-a:-1"), timeout=10)
    acts = _of(frames, "activity")
    assert acts and {f["data"]["investigation_id"] for f in acts} == {"inv-a"}
    assert {i["investigation_id"] for i in _of(frames, "summary")[-1]["data"]["items"]} == {
        "inv-a", "inv-b",
    }


# ── 마스킹 · AI 메모 ─────────────────────────────────────────────────────────


def _secret_events() -> list[dict]:
    return [
        ev(1, label=SECRET, excerpt=f"행 1\n{SECRET}"),
        ev(2, kind="ai_note", engine="holmes", label="AI 메모", note="커넥션 대기 의심"),
        ev(3, kind="tool_start", label="다음 도구"),
    ]


async def test_user_path_masked_without_ai_note(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "chat-u1", kind="diagnosis", owner_sub="u1", origin="chat")
    fake_sre.jobs["chat-u1"] = FakeJob(events=_secret_events())
    app = _app(_config(tmp_path), U1)
    frames = await asyncio.wait_for(_stream(app, "chat-u1"), timeout=10)
    events = [e for f in _of(frames, "activity") for e in f["data"]["events"]]
    assert [e["seq"] for e in events] == [1, 3]
    assert events[0]["label"] == "***" and events[0]["excerpt"] == "행 1\n***"
    raw = json.dumps(frames, ensure_ascii=False)
    assert SECRET not in raw and "ai_note" not in raw and "커넥션 대기 의심" not in raw
    # 조회 경로 activity_since도 같은 규칙.
    resp = await _get(app, "/alarm/investigations/chat-u1", params={"activity_since": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert [e["seq"] for e in body["activity"]["events"]] == [1, 3]
    assert SECRET not in resp.text and "ai_note" not in resp.text
    assert body["phase"] == "collecting"


async def test_admin_path_has_ai_note(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "chat-u1", kind="diagnosis", owner_sub="u1", origin="chat")
    fake_sre.jobs["chat-u1"] = FakeJob(events=_secret_events())
    app = _app(_config(tmp_path), ADMIN)
    frames = await asyncio.wait_for(_stream(app, "chat-u1"), timeout=10)
    events = [e for f in _of(frames, "activity") for e in f["data"]["events"]]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["kind"] == "ai_note" and events[1]["note"] == "커넥션 대기 의심"
    assert events[0]["label"] == SECRET  # 관리자는 sre_agent 가림 뒤 원문


# ── 재연결 커서 ──────────────────────────────────────────────────────────────


async def test_reconnect_cursor_no_gap_no_duplicate(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    job = FakeJob(events=[ev(1), ev(2)])
    fake_sre.jobs["inv-a"] = job
    app = _app(_config(tmp_path), ADMIN)
    first = await asyncio.wait_for(_stream(app, "inv-a:-1"), timeout=10)
    acts = _of(first, "activity")
    seen = [e["seq"] for f in acts for e in f["data"]["events"]]
    assert seen == [1, 2]
    last_id = acts[-1]["id"]
    assert last_id == "inv-a:2"
    job.events.extend([ev(3), ev(4)])
    # EventSource 자동 재연결 — 원래 URL(옛 커서) + Last-Event-ID.
    second = await asyncio.wait_for(
        _stream(app, "inv-a:-1", headers={"Last-Event-ID": last_id}), timeout=10
    )
    more = [e["seq"] for f in _of(second, "activity") for e in f["data"]["events"]]
    assert more == [3, 4]
    # 탭 열고 닫기로 watch를 바꿔 다시 연결 — 커서가 있어 겹치지 않는다.
    third = await asyncio.wait_for(_stream(app, "inv-a:4"), timeout=10)
    assert [e["seq"] for f in _of(third, "activity") for e in f["data"]["events"]] == []


async def test_done_event_and_partial(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    job = FakeJob(events=[ev(1)], status="done",
                  partial={"playbook": {"id": "was.latency", "sections": []}})
    fake_sre.jobs["inv-a"] = job
    app = _app(_config(tmp_path), ADMIN)
    frames = await asyncio.wait_for(_stream(app, "inv-a"), timeout=10)
    done = _of(frames, "done")
    assert len(done) == 1 and done[0]["data"]["status"] == "done"
    assert done[0]["id"] == "inv-a:1"
    assert all(not s["data"]["items"] for s in _of(frames, "summary")[-1:])


async def test_poll_failure_event_only_for_that_investigation(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    seed(tmp_path, "inv-b", alarm_ids=["a2"], alarm_db_ids={"a2": GONGJON_DB})
    fake_sre.fail.add("inv-a")
    fake_sre.jobs["inv-b"] = FakeJob(events=[ev(1)])
    app = _app(_config(tmp_path), ADMIN)
    frames = await asyncio.wait_for(_stream(app, "inv-a,inv-b"), timeout=10)
    errors = _of(frames, "poll_error")
    assert errors and {f["data"]["investigation_id"] for f in errors} == {"inv-a"}
    assert errors[0]["data"]["reason"].startswith(hub_mod.POLL_ERROR_PREFIX)
    by_id = {i["investigation_id"]: i for i in _of(frames, "summary")[-1]["data"]["items"]}
    assert by_id["inv-a"]["poll_error"] and by_id["inv-b"]["poll_error"] is None


# ── 종결 뒤 요약 재조회 ──────────────────────────────────────────────────────


async def test_after_job_ttl_activity_since_reads_archive(tmp_path, fake_sre):  # noqa: ANN001
    seed(tmp_path, "inv-a", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    job = FakeJob(events=[ev(1, excerpt=SECRET), ev(2, kind="ai_note", note="메모")])
    fake_sre.jobs["inv-a"] = job
    config = _config(tmp_path)
    app = _app(config, ADMIN)
    first = await _get(app, "/alarm/investigations/inv-a", params={"activity_since": 0})
    assert [e["seq"] for e in first.json()["activity"]["events"]] == [1, 2]
    job.status = "done"
    hub = app.state.investigation_live_hub
    entry = hub.entry("inv-a")
    loop = asyncio.get_running_loop()
    end = loop.time() + 5
    while not entry.final:
        assert loop.time() < end
        await asyncio.sleep(0.02)
    await hub.aclose()
    # 재기동 + 잡 TTL 경과.
    del fake_sre.jobs["inv-a"]
    app2 = _app(config, ADMIN)
    resp = await _get(app2, "/alarm/investigations/inv-a", params={"activity_since": 0})
    body = resp.json()
    assert body["status"] == "done"
    assert body["activity"]["source"] == "archive"
    assert [e["seq"] for e in body["activity"]["events"]] == [1]  # AI 메모 미보관
    assert SECRET not in resp.text


# ── 인증 ─────────────────────────────────────────────────────────────────────


class _UserRepo:
    async def get_by_user_id(self, sub: str) -> Any:
        if sub != "u1":
            return None
        return SimpleNamespace(is_active=True, to_auth_dict=lambda: dict(U1))


def _token(secret: str, **claims: Any) -> str:
    payload = {"exp": int(time.time()) + 600, **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("none", 401),
        ("user_ok", 200),
        ("user_wrong_type", 401),
        ("admin_type_in_cookie", 401),
        ("admin_in_query", 401),
        ("admin_in_header", 200),
        ("expired", 401),
    ],
    ids=["no-token", "user", "user-type-admin", "admin-cookie", "admin-query", "admin-header",
         "expired"],
)
async def test_live_auth_token_type_and_role(tmp_path, kind, expected):  # noqa: ANN001
    _seed_scope(tmp_path)
    app = _app(_config(tmp_path))
    app.state.user_repo = _UserRepo()
    kw: dict[str, Any] = {}
    if kind == "user_ok":
        kw["cookies"] = {"user_token": _token(USER_SECRET, sub="u1", type="user")}
    elif kind == "user_wrong_type":
        kw["cookies"] = {"user_token": _token(USER_SECRET, sub="u1", type="admin")}
    elif kind == "admin_type_in_cookie":
        kw["cookies"] = {"user_token": _token(ADMIN_SECRET, sub="ops", type="admin")}
    elif kind == "admin_in_query":
        kw["params"] = {"token": _token(ADMIN_SECRET, sub="ops", type="admin")}
    elif kind == "admin_in_header":
        kw["headers"] = {"Authorization": "Bearer " + _token(ADMIN_SECRET, sub="ops", type="admin")}
    elif kind == "expired":
        kw["cookies"] = {"user_token": jwt.encode(
            {"sub": "u1", "type": "user", "exp": int(time.time()) - 10}, USER_SECRET,
            algorithm="HS256",
        )}
    resp = await _get(app, "/alarm/investigations/active", **kw)
    assert resp.status_code == expected, resp.text
    if kind == "user_ok":
        assert [i["investigation_id"] for i in resp.json()["items"]] == ["chat-u1"]
    if kind == "admin_in_header":  # break-glass 운영자 = 관리자 범위(전부)
        assert len(resp.json()["items"]) == 4
    stream = await _get(app, "/alarm/investigations/stream", **kw)
    assert stream.status_code == expected


async def test_bankjon_user_sees_no_gongjon_summary(tmp_path, fake_sre):  # noqa: ANN001
    # 존 사용자라도 남의 자동 조사는 목록에 없다(존과 무관 — 목록은 채팅 조사만).
    _seed_scope(tmp_path)
    user = {"sub": "u5", "role": "user", "alarm_zones": [ZONE_BANKJON, ZONE_GONGJON]}
    app = _app(_config(tmp_path), user)
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    assert all(not s["data"]["items"] for s in _of(frames, "summary"))


async def test_operator_denied_keeps_not_found(tmp_path, fake_sre):  # noqa: ANN001
    """운영자 경로는 사유를 그대로 구분한다(없음=not_found · 형식 오류=invalid)."""
    app = _app(_config(tmp_path), ADMIN)
    frames = await asyncio.wait_for(_stream(app, "nope,bad%0Aid"), timeout=10)
    denied = {f["data"]["investigation_id"]: f["data"]["reason"] for f in _of(frames, "denied")}
    assert denied["nope"] == "not_found"
    assert "invalid" in denied.values() and "unavailable" not in denied.values()
