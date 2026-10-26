"""plans/192 W3 (Q-2 B) — 진행 중 목록에 본인이 접수한 `kind=manual` 조사 포함 · 빈 목록 안내 문구.

- 목록 범위 표(`/alarm/investigations/active` · SSE `summary` 둘 다): 관리자 = 전부 · 본인 manual =
  보임 · 타인 manual·자동 조사 = 안 보임 · 인가는 `_assert_investigation_access` 재사용
  (manual 플래그 off면 존 검사 — 타 존이면 빠지고 같은 존이면 보인다)
- 사용자 항목: `owner_sub` 없음 · 값 패턴 마스킹
- 빈 목록 문구: 「이벤트 찾아 조사」 칸이 보일 때·숨을 때 두 갈래(node 실행)

라이브 플래그 off → 404는 `test_plan187_w14_live_api.test_flag_off_routes_404_before_auth`가 맡는다.
조사 서비스는 가짜 클라이언트 · 실 LLM·외부 호출 0.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from tests.test_api.test_plan187_w14_live_api import (
    ADMIN,
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
from tests.test_api.test_plan187_w14_live_hub import SECRET, FakeJob, seed
from tests.test_api.test_ui_plan187_w15_diagnosis import _node


def _manual(tmp_path, inv: str, owner: str, db_id: str = GONGJON_DB, **kw: Any) -> None:  # noqa: ANN001
    """`request_manual_investigation`이 남기는 꼴의 레코드를 심는다."""
    alarm_id = f"al-{inv}"
    seed(tmp_path, inv, kind="manual", owner_sub=owner, origin="manual",
         alarm_ids=[alarm_id], alarm_db_ids={alarm_id: db_id}, parent_alarm_id=alarm_id, **kw)


async def _visible(app, sre, invs: list[str]) -> tuple[set[str], set[str]]:  # noqa: ANN001
    """(`/active` id 집합, SSE summary id 합집합)."""
    for inv in invs:
        sre.jobs[inv] = FakeJob()
    active = await _get(app, "/alarm/investigations/active")
    assert active.status_code == 200, active.text
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    summaries = _of(frames, "summary")
    assert summaries
    return (
        {i["investigation_id"] for i in active.json()["items"]},
        {i["investigation_id"] for s in summaries for i in s["data"]["items"]},
    )


# ── 목록 범위 표 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("who", "owner", "db_id", "manual_on", "expected"),
    [
        ("admin", "u9", BANKJON_DB, True, True),     # 관리자 = 전부(남의 manual 포함)
        ("u1", "u1", GONGJON_DB, True, True),        # 본인 manual — 보임
        ("u1", "u1", BANKJON_DB, True, True),        # 본인 manual · 타 존 — 소유자 열람(플래그 on)
        ("u1", "u9", GONGJON_DB, True, False),       # 타인 manual — 같은 존이라도 안 보임
        ("u1", "u1", BANKJON_DB, False, False),      # 플래그 off · 타 존 — 인가 실패
        ("u1", "u1", GONGJON_DB, False, True),       # 플래그 off · 같은 존 — 존 검사로 통과
    ],
    ids=["admin-all", "own", "own-other-zone", "foreign", "flag-off-other-zone",
         "flag-off-own-zone"],
)
async def test_manual_scope_table(  # noqa: PLR0913
    tmp_path, fake_sre, who, owner, db_id, manual_on, expected,  # noqa: ANN001, F811
) -> None:
    _manual(tmp_path, "man-x", owner, db_id)
    config = _config(tmp_path)
    config.noise_gate.investigation_manual_enabled = manual_on
    app = _app(config, ADMIN if who == "admin" else U1)
    active, summary = await _visible(app, fake_sre, ["man-x"])
    want = {"man-x"} if expected else set()
    assert active == want
    assert summary == want


async def test_auto_and_foreign_stay_out_for_user(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    """자동 조사·남의 manual은 빠지고 본인 manual만 남는다(관리자는 셋 다)."""
    seed(tmp_path, "auto-1", alarm_ids=["a1"], alarm_db_ids={"a1": GONGJON_DB})
    _manual(tmp_path, "man-u1", "u1")
    _manual(tmp_path, "man-u9", "u9")
    invs = ["auto-1", "man-u1", "man-u9"]
    user_active, user_summary = await _visible(_app(_config(tmp_path), U1), fake_sre, invs)
    assert user_active == user_summary == {"man-u1"}
    admin_active, admin_summary = await _visible(_app(_config(tmp_path), ADMIN), fake_sre, invs)
    assert admin_active == admin_summary == set(invs)


async def test_user_manual_item_masked_without_owner(tmp_path, fake_sre) -> None:  # noqa: ANN001, F811
    _manual(tmp_path, "man-u1", "u1", subject={"hostname": "wasdemo01", "note": SECRET})
    fake_sre.jobs["man-u1"] = FakeJob()
    app = _app(_config(tmp_path), U1)
    items = (await _get(app, "/alarm/investigations/active")).json()["items"]
    assert [i["investigation_id"] for i in items] == ["man-u1"]
    frames = await asyncio.wait_for(_stream(app), timeout=10)
    items += [i for s in _of(frames, "summary") for i in s["data"]["items"]]
    for item in items:
        assert "owner_sub" not in item
        assert SECRET not in json.dumps(item, ensure_ascii=False)


# ── 빈 목록 안내 문구(node 실행) ─────────────────────────────────────────────

_FULL = (
    "진행 중인 조사만 보입니다 — 관리자는 전체, 일반 사용자는 직접 요청한 조사만. "
    "끝난 조사는 이벤트 알람 탭의 지난 알람 검색에서 엽니다. "
    "다시 조사하려면 위 「이벤트 찾아 조사」를 씁니다."
)
_NO_SEARCH = (
    "진행 중인 조사만 보입니다 — 관리자는 전체, 일반 사용자는 직접 요청한 조사만. "
    "끝난 조사는 이벤트 알람 탭의 지난 알람 검색에서 엽니다."
)


def _empty_note(event_search: bool, start: bool) -> dict[str, Any]:
    start_opt = "startInvestigation: function () { return Promise.resolve({}); }," if start else ""
    return _node(
        f"""
routes[API + "/active"] = {{ status: 200,
    body: {{ items: [], event_search: {str(event_search).lower()} }} }};
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }}, onActivate: function () {{}},
    analyzeFor: function () {{ return null; }}, {start_opt}
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }} }});
await ctl.probe();
var panel = findAll(rootEl, "dv-panel--list")[0];
var note = panel.children.filter(function (c) {{ return c.className === "dv-note"; }})[0];
out = {{ note: note.textContent, hidden: findAll(rootEl, "dv-search")[0].hidden }};
"""
    )


@pytest.mark.parametrize(
    ("event_search", "start", "text", "hidden"),
    [
        (True, True, _FULL, False),
        (False, True, _NO_SEARCH, True),
        (True, False, _NO_SEARCH, True),  # 접수 함수 미주입 — 칸이 숨으니 구절도 뺀다
    ],
    ids=["search-shown", "event-search-off", "no-starter"],
)
def test_empty_list_note_follows_search_box(
    event_search: bool, start: bool, text: str, hidden: bool
) -> None:
    assert _empty_note(event_search, start) == {"note": text, "hidden": hidden}
