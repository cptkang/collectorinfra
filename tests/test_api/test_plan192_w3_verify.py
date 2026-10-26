"""plans/192 W3 독립 검증 — 목록 범위의 인가 경계와 watch·조회 인가의 판정 일치.

- `owner_sub` 없는 manual 레코드 · `sub` 없는 사용자 → 목록 0건
- 알람 수신 권한(존) 없는 사용자: 플래그 on이면 소유자 열람으로 보이고, off면 빠진다
- 목록에 보이는 manual은 watch(`denied` 아님)·조회(`GET /alarm/investigations/{id}` 200)도 열린다
  — docs/18 2026-10-19 「요청 경로마다 열람 인가를 따로 씀」 회귀 가드(같은 판정 사용 확인)
- 합류 사용자(남이 접수한 manual에 joined)는 목록 밖 — 현 설계(owner_sub=접수자) 고정
- 라이브 플래그 off → manual 레코드가 있어도 `/active` 404(인증보다 먼저)
- 프런트: `/active` 실패면 `listFailed`가 범위 안내보다 우선 · 「이벤트 찾아 조사」 칸은 안내 문구
  **위**에 그려진다(문구 「아래」와 어긋남 — 보고 대상)

조사 서비스는 가짜 클라이언트 · 실 LLM·외부 호출 0.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.test_api.test_plan187_w14_live_api import (
    BANKJON_DB,
    GONGJON_DB,
    U1,
    _app,
    _close_hubs,  # noqa: F401 — autouse 픽스처 재사용
    _config,
    _get,
    _of,
    _short_stream,  # noqa: F401 — autouse 픽스처 재사용
    _stream,
    fake_sre,  # noqa: F401 — 픽스처 재사용
)
from tests.test_api.test_plan187_w14_live_hub import FakeJob, seed
from tests.test_api.test_ui_plan187_w15_diagnosis import _node

NO_ZONE_USER = {"sub": "u1", "role": "user"}  # 알람 수신 존 없음
NO_SUB_USER = {"role": "user", "alarm_zones": U1["alarm_zones"]}


def _manual(tmp_path, inv: str, owner: str | None, db_id: str = GONGJON_DB) -> None:  # noqa: ANN001
    alarm_id = f"al-{inv}"
    seed(tmp_path, inv, kind="manual", owner_sub=owner, origin="manual",
         alarm_ids=[alarm_id], alarm_db_ids={alarm_id: db_id}, parent_alarm_id=alarm_id)


async def _listed(app, sre, invs: list[str]) -> set[str]:  # noqa: ANN001
    for inv in invs:
        sre.jobs[inv] = FakeJob()
    resp = await _get(app, "/alarm/investigations/active")
    assert resp.status_code == 200, resp.text
    active = {i["investigation_id"] for i in resp.json()["items"]}
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    summary = {i["investigation_id"] for s in _of(frames, "summary") for i in s["data"]["items"]}
    assert active == summary  # 두 경로 같은 범위(_live_scope 공용)
    return active


async def test_manual_without_owner_sub_never_listed(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    _manual(tmp_path, "man-none", None)
    assert await _listed(_app(_config(tmp_path), U1), fake_sre, ["man-none"]) == set()


async def test_user_without_sub_sees_nothing(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    _manual(tmp_path, "man-none", None)
    _manual(tmp_path, "man-u1", "u1")
    listed = await _listed(_app(_config(tmp_path), NO_SUB_USER), fake_sre, ["man-none", "man-u1"])
    assert listed == set()


@pytest.mark.parametrize(("manual_on", "expected"), [(True, {"man-u1"}), (False, set())])
async def test_no_alarm_zone_user_follows_owner_view(
    tmp_path, fake_sre, manual_on: bool, expected: set[str],  # noqa: ANN001, F811
) -> None:
    """존 없는 사용자 — on: 소유자 열람(존 검사 생략)으로 보임 · off: 수신 권한 403으로 빠짐."""
    _manual(tmp_path, "man-u1", "u1")
    config = _config(tmp_path)
    config.noise_gate.investigation_manual_enabled = manual_on
    assert await _listed(_app(config, NO_ZONE_USER), fake_sre, ["man-u1"]) == expected


_CASES = [
    # (id, owner, db_id) — 본인 같은 존 · 본인 타 존 · 남 같은 존 · 남 타 존
    ("man-own-gp", "u1", GONGJON_DB),
    ("man-own-b0", "u1", BANKJON_DB),
    ("man-u9-gp", "u9", GONGJON_DB),
    ("man-u9-b0", "u9", BANKJON_DB),
]


@pytest.mark.parametrize("manual_on", [True, False])
@pytest.mark.parametrize("user", [U1, NO_ZONE_USER], ids=["gongjon-user", "no-zone-user"])
async def test_listed_implies_watch_and_get_allowed(
    tmp_path, fake_sre, manual_on: bool, user: dict[str, Any],  # noqa: ANN001, F811
) -> None:
    """목록에 보이는 것은 watch·조회도 열린다 — 세 경로 같은 인가 함수."""
    for inv, owner, db_id in _CASES:
        _manual(tmp_path, inv, owner, db_id)
    config = _config(tmp_path)
    config.noise_gate.investigation_manual_enabled = manual_on
    app = _app(config, user)
    invs = [c[0] for c in _CASES]
    listed = await _listed(app, fake_sre, invs)
    frames = await asyncio.wait_for(
        _stream(app, watch=",".join(f"{i}:0" for i in invs)), timeout=10
    )
    denied = {f["data"]["investigation_id"] for f in _of(frames, "denied")}
    got = {inv: (await _get(app, f"/alarm/investigations/{inv}")).status_code for inv in invs}
    for inv, owner, _db in _CASES:
        allowed = inv not in denied
        assert allowed == (got[inv] == 200), (inv, denied, got)  # watch ≡ 조회
        assert (inv in listed) == (allowed and owner == "u1"), (inv, listed, denied)
    assert not ({"man-u9-gp", "man-u9-b0"} & listed)  # 남의 manual은 어떤 경우도 목록 밖


async def test_joined_user_does_not_see_others_manual(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    """합류(joined) 사용자 — owner_sub가 접수자라 합류자 목록에는 없다(현 설계 고정 · 보고 대상)."""
    _manual(tmp_path, "man-u9", "u9")
    joiner = {"sub": "u2", "role": "user", "alarm_zones": U1["alarm_zones"]}
    app = _app(_config(tmp_path), joiner)
    assert await _listed(app, fake_sre, ["man-u9"]) == set()
    # 탭(watch)으로는 존 검사로 열린다 — 목록 밖이지만 열람은 가능
    frames = await asyncio.wait_for(_stream(app, watch="man-u9:0"), timeout=10)
    assert not _of(frames, "denied")


async def test_live_flag_off_404_with_manual_record(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    _manual(tmp_path, "man-u1", "u1")
    app = _app(_config(tmp_path, live=False))  # 인증 의존성 미주입 — 404가 인증보다 먼저
    assert (await _get(app, "/alarm/investigations/active")).status_code == 404
    assert (await _get(app, "/alarm/investigations/stream")).status_code == 404


# ── 프런트 ───────────────────────────────────────────────────────────────


def _render(active_route: str) -> dict[str, Any]:
    return _node(
        f"""
routes[API + "/active"] = {active_route};
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }}, onActivate: function () {{}},
    analyzeFor: function () {{ return null; }},
    startInvestigation: function () {{ return Promise.resolve({{}}); }},
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }} }});
await ctl.probe();
var panel = findAll(rootEl, "dv-panel--list")[0];
var names = panel.children.map(function (c) {{ return c.className; }});
var note = panel.children.filter(function (c) {{ return c.className === "dv-note"; }})[0];
out = {{ note: note.textContent, order: names }};
"""
    )


def test_list_failed_takes_precedence_over_scope_note() -> None:
    out = _render('{ status: 500, body: { detail: "boom" } }')
    assert out["note"] == "진행 중 목록을 불러오지 못했습니다 — 사유: boom"


def test_search_box_is_rendered_above_note() -> None:
    """「이벤트 찾아 조사」 칸이 안내 문구보다 앞(위)에 온다 — 문구의 「아래」와 방향이 반대다."""
    out = _render("{ status: 200, body: { items: [], event_search: true } }")
    order = out["order"]
    assert order.index("dv-search") < order.index("dv-note")
    assert "위 「이벤트 찾아 조사」" in out["note"]  # 칸이 문구 위에 있다(교정 반영)
