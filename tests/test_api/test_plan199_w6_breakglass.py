"""plans/199 W6 — break-glass 관제 서랍의 「한눈에 보기」 카드(D-352 · 사용자 결정 2026-10-30).

break-glass 운영자 토큰은 기능 조회(`/alarm/capabilities` — 사용자 토큰 인가)를 못 부르므로,
운영자 관제가 이미 부르는 `GET /admin/noise/health` 응답에 설정 플래그 1개
(`investigation_summary_view_enabled`)를 가산하고 `noise.js`가 break-glass 세션에서만 그 값을 쓴다.

    1. 백엔드 — 실제 인가 의존성(`require_admin_user`)으로 올린 헬스 라우트가
       필드를 config 값으로 싣는다.
    2. 보안 — 사용자 토큰(role≠admin)·토큰 없음·서명 위조·`type` 불일치는 거부,
       사용자 경로는 미등록, 응답 키는 종전 + 이 필드 1개(다른 설정값 유출 없음).
    3. JS — `loadHealth` 판정식을 뽑아 node로 돌린다(break-glass만 헬스 값을 쓰고
       사용자 세션은 덮지 않음).
    4. 공용 렌더 — 운영자 결정 추적의 `investigation` 블록(원 `briefing`)을
       break-glass 서랍 옵션으로 그리면 「원문 포함」이 붙고 포털 진입 버튼은 없다
       (D-341 부기 18 Q-3 A 유지).

망·LLM 호출 0. 설정은 SimpleNamespace로 필요한 필드만 둔다 — `.env`를 읽지 않는다.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api.routes import noise_dashboard as noise_routes
from src.api.routes.noise_dashboard import NoiseHealthResponse
from src.domain.user import User, UserRole
from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _js_function_body,
    _make_config,
    _seed_decisions,
)
from tests.test_plan199_w1_summary import _js, _node, _view

_ROOT = Path(__file__).resolve().parents[2]
_NOISE_JS = _ROOT / "src" / "static" / "js" / "noise.js"

AUTH_SECRET = "auth-secret-199w6-0123456789abcdef01"
ADMIN_SECRET = "admin-secret-199w6-0123456789abcdef0"
HEALTH = "/api/v1/admin/noise/health"
FIELD = "investigation_summary_view_enabled"
#: W6 이전 헬스 응답 키 — 이 필드 1개만 더한다
_BASE_HEALTH_KEYS = {
    "healthy", "alerts", "suppress_ratio", "suppress_ratio_threshold", "total",
    "last_event_ts", "last_event_age_seconds", "window_seconds", "gate_enabled",
}

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


class _Users:
    """`user_repo` 대역 — `u-admin`만 DB role admin(토큰 클레임이 아니라 저장소가 역할을 정한다)."""

    async def get_by_user_id(self, user_id: str) -> User:
        role = UserRole.ADMIN if user_id == "u-admin" else UserRole.USER
        return User(user_id=user_id, username=user_id, hashed_password="x", role=role)


def _token(payload: dict[str, Any], secret: str) -> str:
    payload = {**payload, "exp": datetime.now(UTC) + timedelta(hours=1)}
    return jwt.encode(payload, secret, algorithm="HS256")


def _break_glass() -> str:
    return _token({"sub": "ops", "type": "admin"}, ADMIN_SECRET)


def _user_token(sub: str = "u1", *, role: str = "user", secret: str = AUTH_SECRET,
                type_: str = "user") -> str:
    return _token({"sub": sub, "name": sub, "role": role, "type": type_}, secret)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(tmp_path: Path, **ng_over: Any) -> TestClient:
    """실제 인가 의존성(오버라이드 없음)으로 관제 라우터를 올린다."""
    config = _make_config(tmp_path)
    config.auth.jwt_secret = AUTH_SECRET
    config.admin = SimpleNamespace(jwt_secret=ADMIN_SECRET)
    for key, value in ng_over.items():
        setattr(config.noise_gate, key, value)
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.state.config = config
    app.state.user_repo = _Users()
    return TestClient(app)


# ─── 1. 백엔드 — 헬스 응답의 설정 플래그 ─────────────────────────────────────


def test_health_field_defaults_true_for_break_glass(tmp_path: Path) -> None:
    """설정 대역에 키가 없으면(구 설정) 코드 기본값과 같은 True.

    NoiseGateConfig 기본 on(D-352 부기 2)과 같다.
    """
    res = _client(tmp_path).get(HEALTH, headers=_bearer(_break_glass()))
    assert res.status_code == 200, res.text
    assert res.json()[FIELD] is True


@pytest.mark.parametrize("flag", [True, False])
def test_health_field_follows_config(tmp_path: Path, flag: bool) -> None:
    client = _client(tmp_path, investigation_summary_view_enabled=flag)
    body = client.get(HEALTH, headers=_bearer(_break_glass())).json()
    assert body[FIELD] is flag


def test_admin_role_user_token_sees_same_field(tmp_path: Path) -> None:
    """DB role admin 사용자 토큰도 종전대로 헬스를 읽는다.

    필드 값은 같다 — 화면(사용자 토큰 세션)은 이 값을 쓰지 않는다.
    """
    client = _client(tmp_path, investigation_summary_view_enabled=False)
    res = client.get(HEALTH, headers=_bearer(_user_token("u-admin")))
    assert res.status_code == 200, res.text
    assert res.json()[FIELD] is False


# ─── 2. 보안 — 인가 · 경로 · 응답 유출 ─────────────────────────────────────


def test_plain_user_token_is_403(tmp_path: Path) -> None:
    res = _client(tmp_path).get(HEALTH, headers=_bearer(_user_token("u1")))
    assert res.status_code == 403
    assert FIELD not in res.text


def test_role_claim_in_token_does_not_grant_admin(tmp_path: Path) -> None:
    """토큰의 role 클레임이 admin이어도 저장소 역할이 user면 403(역할은 DB 실시간 값 · D-069)."""
    res = _client(tmp_path).get(HEALTH, headers=_bearer(_user_token("u1", role="admin")))
    assert res.status_code == 403


def test_missing_token_is_401(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get(HEALTH).status_code == 401
    assert client.get(HEALTH, headers={"Authorization": "Basic abc"}).status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(_user_token("u-admin", secret="forged-secret-0123456789abcdef0123"),
                     id="user-forged-signature"),
        pytest.param(_token({"sub": "ops", "type": "admin"}, "forged-secret-0123456789abcdef0123"),
                     id="admin-forged-signature"),
        pytest.param(_token({"sub": "ops", "type": "user"}, ADMIN_SECRET),
                     id="admin-secret-type-user"),
        pytest.param(_user_token("u-admin", type_="admin"), id="user-secret-type-admin"),
        pytest.param(_token({"type": "admin"}, ADMIN_SECRET), id="admin-no-sub"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_forged_or_mistyped_tokens_are_rejected(tmp_path: Path, token: str) -> None:
    res = _client(tmp_path).get(HEALTH, headers=_bearer(token))
    assert res.status_code == 401, res.text
    assert FIELD not in res.text


@pytest.mark.parametrize("token", [_break_glass(), _user_token("u1"), _user_token("u-admin")])
def test_user_path_health_is_not_registered(tmp_path: Path, token: str) -> None:
    """사용자 경로 `/noise/*`에는 헬스가 없다 — 이 필드도 사용자 경로로 새지 않는다(D-245)."""
    res = _client(tmp_path).get("/api/v1/noise/health", headers=_bearer(token))
    assert res.status_code in (404, 405)


def test_health_keys_are_base_plus_one_field(tmp_path: Path) -> None:
    """응답 키 = 종전 키 + 이 필드 1개 — 다른 설정값(경로·토큰·URL 등)을 싣지 않는다."""
    expected = _BASE_HEALTH_KEYS | {FIELD}
    assert set(NoiseHealthResponse.model_fields) == expected
    client = _client(tmp_path, investigation_service_token="svc-secret-token",
                     investigation_service_url="http://127.0.0.1:9/sse")
    res = client.get(HEALTH, headers=_bearer(_break_glass()))
    assert set(res.json()) == expected
    for leaked in ("svc-secret-token", "127.0.0.1:9", "decisions.jsonl", ADMIN_SECRET):
        assert leaked not in res.text


def test_health_route_keeps_admin_dependency() -> None:
    """핸들러 단위 `require_admin_user`가 그대로 걸려 있다(인가 의존성 교체·제거 없음)."""
    from src.api.dependencies import require_admin_user

    routes = [
        r for r in noise_routes.router.routes if getattr(r, "path", "") == "/admin/noise/health"
    ]
    assert len(routes) == 1
    deps = [d.call for d in routes[0].dependant.dependencies]
    assert require_admin_user in deps


# ─── 3. noise.js — break-glass만 헬스 값을 쓴다 ─────────────────────────────


_HEALTH_HARNESS = r"""
function stub() {
    return { style: {}, className: "", innerHTML: "", textContent: "", title: "",
        appendChild: function (c) { return c; } };
}
var document = { getElementById: function () { return stub(); },
    createTextNode: function (t) { return { t: t }; } };
function el() { return stub(); }
function fmtInt(v) { return String(v); }
function fmtAge(v) { return String(v); }
function fmtTime(v) { return String(v); }
function metaAlertText(a) { return String(a.type); }
var API = "/api/v1/admin/noise";
"""

_HEALTH_BASE = {
    "healthy": True, "alerts": [], "suppress_ratio": 0.1, "suppress_ratio_threshold": 0.9,
    "total": 3, "last_event_ts": None, "last_event_age_seconds": None, "window_seconds": 3600,
    "gate_enabled": True,
}


def _run_load_health(*, break_glass: bool, initial: bool, health: Any) -> dict[str, Any]:
    """`loadHealth`를 뽑아 가짜 DOM·getJSON으로 돌린다.

    health가 "throw"면 조회 실패, None이면 403(getJSON이 null을 돌려준다).
    """
    body = _js_function_body(_NOISE_JS.read_text(encoding="utf-8"), "loadHealth")
    script = _HEALTH_HARNESS + f"""
var IS_BREAK_GLASS = {json.dumps(break_glass)};
var summaryViewOn = {json.dumps(initial)};
var health = {json.dumps(health)};
var calls = [];
async function getJSON(path) {{
    calls.push(path);
    if (health === "throw") throw new Error("요청 실패 (500)");
    return health;
}}
async {body}
loadHealth().then(function () {{ return "ok"; }},
    function () {{ return "rejected"; }}).then(function (r) {{
    console.log(JSON.stringify({{ on: summaryViewOn, result: r, calls: calls }}));
}});
"""
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@_needs_node
@pytest.mark.parametrize(
    ("break_glass", "initial", "health", "expected"),
    [
        (True, False, {**_HEALTH_BASE, FIELD: True}, True),
        (True, True, {**_HEALTH_BASE, FIELD: False}, False),
        (True, False, _HEALTH_BASE, False),                    # 키 없음(구 서버) → 종전 화면
        (True, False, {**_HEALTH_BASE, FIELD: "true"}, False),  # 불리언 참만
        (True, False, "throw", False),                         # 첫 조회 실패 → 거짓 유지
        (True, False, None, False),                            # 403 → 거짓 유지
        # 사용자 토큰 세션(관리자 역할 포함) — 기능 조회 값(initial)을 헬스 값으로 덮지 않는다
        (False, False, {**_HEALTH_BASE, FIELD: True}, False),
        (False, True, {**_HEALTH_BASE, FIELD: False}, True),
        (False, True, _HEALTH_BASE, True),
    ],
)
def test_load_health_sets_summary_view_only_for_break_glass(
    break_glass: bool, initial: bool, health: Any, expected: bool
) -> None:
    out = _run_load_health(break_glass=break_glass, initial=initial, health=health)
    assert out["on"] is expected
    assert out["calls"] == ["/api/v1/admin/noise/health"]
    assert out["result"] == ("rejected" if health == "throw" else "ok")


def test_summary_view_assignments_are_caps_and_break_glass_health_only() -> None:
    """`summaryViewOn` 대입은 3곳뿐이다.

    선언 · 기능 조회(비 break-glass 분기) · 헬스(break-glass 가드).
    """
    src = _NOISE_JS.read_text(encoding="utf-8")
    sites = re.findall(r"^.*\bsummaryViewOn\s*=(?!=).*$", src, re.M)
    assert [s.strip() for s in sites] == [
        "if (IS_BREAK_GLASS) summaryViewOn = data.investigation_summary_view_enabled === true;",
        "var summaryViewOn = false;",
        "summaryViewOn = caps.investigation_summary_view_enabled === true;",
    ]
    assert "if (IS_BREAK_GLASS) summaryViewOn" in _js_function_body(src, "loadHealth")
    # 기능 조회는 break-glass면 부르지 않는다(종전 · 401 로그아웃 회피)
    caps = re.search(
        r"var manualOnlyMode = \(IS_BREAK_GLASS \|\| !window\.BriefingView\)\s*\n"
        r"\s*\? Promise\.resolve\(false\)\s*\n"
        r"\s*: alarmCall\(\"/api/v1/alarm/capabilities\"\)",
        src,
    )
    assert caps, "기능 조회 분기"
    # 헬스는 운영자 페이지(IS_ADMIN)에서만 부른다 — 사용자 페이지는 헬스 경로가 없다
    assert re.search(r"if \(IS_ADMIN\) \{\n\s*loadHealth\(\)\.catch\(", src), "첫 로드"
    assert "if (IS_ADMIN) loadHealth().catch(function () {});" in src, "60초 주기"
    assert src.count("loadHealth().") == 2


# ─── 4. 공용 렌더 — break-glass 서랍 옵션으로 운영자 블록을 그린다 ─────────────


_BOX = r"""
var viewed = [];
function box() {
    return { innerHTML: "", isConnected: true, addEventListener: function () {},
        contains: function () { return true; }, querySelectorAll: function () { return []; },
        querySelector: function () { return null; } };
}
"""

#: 포털 진입·조사 시작 계열 — break-glass 서랍에는 없다(D-341 부기 18 Q-3 A)
_PORTAL_MARKS = (
    "장애 진단에서 보기", "조사 대화로 열기", "이 알람 분석", "조사 시작", "cause-live-open",
    'data-bv-action="open-investigation"', 'data-bv-action="open-conversation"',
    'data-bv-action="analyze"',
)


def _operator_block(tmp_path: Path, briefing: dict[str, Any]) -> dict[str, Any]:
    """결정 1건 + 종결 조사 1건을 심고 운영자 결정 추적의 조사 블록을 읽는다.

    break-glass 토큰으로 읽는다 — 블록은 서버가 정한 운영자 뷰(원 `briefing`)다.
    """
    _seed_decisions(tmp_path, [_decision_record(alarm_id="a1", db_id="polestar_cm_gp")])
    InvestigationStore(str(tmp_path / "inv.jsonl")).record(
        investigation_id="inv-w6", alarm_ids=["a1"], status="done", source="inline",
        verdict={"level": "high"}, briefing=briefing,
    )
    client = _client(
        tmp_path,
        investigation_store_path=str(tmp_path / "inv.jsonl"),
        investigation_store_max_lines=2000,
        investigation_trigger_enabled=False,
        investigation_manual_enabled=False,
    )
    res = client.get("/api/v1/admin/noise/decisions/a1", headers=_bearer(_break_glass()))
    assert res.status_code == 200, res.text
    block = res.json()["decision"]["investigation"]
    assert isinstance(block.get("briefing"), dict) and "summary" in block["briefing"]  # 원 브리핑
    return block


def _render_break_glass(block: dict[str, Any], summary: bool) -> dict[str, Any]:
    """noise.js renderInvestigation의 break-glass 옵션으로 attach 한다.

    상세형 · analyze 없음 · 진입 콜백 없음.
    """
    return _node(_BOX + f"""
var v = {_js(block)};
var c = box();
var p = BV.attach(c, {{ initial: v, load: function () {{ return Promise.resolve(v); }},
    render: {{ mode: "detail" }}, analyze: null,
    onView: function (view) {{ viewed.push(view && view.investigation_id); }},
    summaryView: function () {{ return {json.dumps(summary)}; }} }});
await p.open(); await tick(); p.close();
out = {{ html: c.innerHTML, raw: BV.isRawView(v), viewed: viewed }};
""")


def test_break_glass_drawer_card_has_raw_marker_and_no_portal_buttons(tmp_path: Path) -> None:
    briefing = copy.deepcopy(_view("confirmed_cpu_ke")["briefing"])
    out = _render_break_glass(_operator_block(tmp_path, briefing), summary=True)
    html = out["html"]
    assert out["raw"] is True and out["viewed"]
    assert html.count('data-bv-summary="1"') == 1           # 「한눈에 보기」 카드
    assert 'data-bv-action="handoff-copy"' in html          # 전달문 버튼(서랍 안 동작)
    assert "원문 포함" in html and "bv-handoff-raw" in html  # 운영자 뷰 표지
    for mark in _PORTAL_MARKS:
        assert mark not in html, mark


def test_break_glass_drawer_flag_off_has_no_card(tmp_path: Path) -> None:
    briefing = copy.deepcopy(_view("confirmed_cpu_ke")["briefing"])
    out = _render_break_glass(_operator_block(tmp_path, briefing), summary=False)
    html = out["html"]
    assert html and 'data-bv-summary="1"' not in html
    assert "원문 포함" not in html and "handoff-copy" not in html
    for mark in _PORTAL_MARKS:
        assert mark not in html, mark


def test_break_glass_drawer_wiring_adds_no_portal_entry() -> None:
    """서랍 쪽 배선 — 조사 블록 렌더는 진입 콜백을 넘기지 않는다.

    머리 진입 버튼·분석 옵션은 break-glass에서 빠진다.
    """
    src = _NOISE_JS.read_text(encoding="utf-8")
    render = _js_function_body(src, "renderInvestigation")
    assert "onOpenInvestigation" not in render and "onOpenConversation" not in render
    assert "render: IS_BREAK_GLASS ? { mode: \"detail\" } : { mode: \"summary\" }," in render
    assert "summaryView: summaryViewNow," in render
    assert "IS_BREAK_GLASS || !id" in _js_function_body(src, "portalEntryButton")
    assert "if (IS_BREAK_GLASS) return null;" in _js_function_body(src, "analyzeOptions")
    assert "if (IS_BREAK_GLASS) return;" in _js_function_body(src, "renderAnalyzeOnly")
