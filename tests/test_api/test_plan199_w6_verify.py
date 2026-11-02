"""plans/199 W6 독립 검증(verifier) — break-glass 관제 서랍의 「한눈에 보기」 카드.

구현자 테스트(`test_plan199_w6_breakglass.py`)가 다루지 않은 곳만 본다.

    1. 인가 — 만료 토큰(운영자·사용자) · ``alg=none`` · 비활성 관리자 역할 사용자 ·
       운영자 시크릿 + 사용자 type 의 role 클레임 위조. 거부 응답에 필드가 실리지 않는다.
    2. 실 설정 모양 — ``NoiseGateConfig`` 실물(``_env_file=None`` · 검증 필드 명시)로
       헬스 값이 따라간다 · 헬스 응답 모델을 쓰는 경로는 운영자 경로 1개뿐 · 같은 값의
       다른 출구(기능 조회)는 사용자 인가(W1)라 W6이 새 사용자 출구를 만들지 않았다.
    3. noise.js — 토큰 선택 블록을 실제로 돌려 ``IS_BREAK_GLASS`` 판정(운영자 페이지에
       사용자 토큰만 있으면 거짓 · 포털 탭 거짓) · 호출 순서(대입은 await 뒤라 mount
       동기 본문의 ``var summaryViewOn = false``보다 늦다 — 최악 순서로 불러도 참) ·
       break-glass 재조회 경로(사용자 API 401 → 운영자 결정 블록 폴백)도 원문 뷰.
    4. 공용 렌더 — 픽스처 13종 전부에서 break-glass 서랍에 포털 진입·분석 버튼 0 ·
       전달문 포털 주소는 ``<pre>`` 글자로만(href 없음) · 꺼짐(summaryView 거짓) 렌더 =
       summaryView 옵션 자체가 없는 렌더(W1 이전)와 바이트 동일.

망·LLM 호출 0. 설정은 대역 또는 ``_env_file=None`` 실물이라 ``.env``를 읽지 않는다.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import noise_dashboard as noise_routes
from src.api.routes.noise_dashboard import NoiseHealthResponse
from src.config.alarm import NoiseGateConfig
from src.domain.user import User, UserRole, UserStatus
from tests.test_api.test_noise_dashboard_api import _js_function_body, _make_config
from tests.test_plan199_w1_summary import _js, _node, _view

_ROOT = Path(__file__).resolve().parents[2]
_NOISE_JS = _ROOT / "src" / "static" / "js" / "noise.js"
_FIX = _ROOT / "tests" / "fixtures" / "plan199"

AUTH_SECRET = "auth-secret-199w6v-0123456789abcdef0"
ADMIN_SECRET = "admin-secret-199w6v-0123456789abcdef"
HEALTH = "/api/v1/admin/noise/health"
FIELD = "investigation_summary_view_enabled"
_PAST = timedelta(minutes=-5)

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


class _Users:
    """`user_repo` 대역 — 역할·상태는 저장소가 정한다(토큰 클레임이 아니라)."""

    async def get_by_user_id(self, user_id: str) -> User:
        admin = user_id.startswith("u-admin")
        off = user_id.endswith("-off")
        return User(
            user_id=user_id,
            username=user_id,
            hashed_password="x",
            role=UserRole.ADMIN if admin else UserRole.USER,
            status=UserStatus.INACTIVE if off else UserStatus.ACTIVE,
        )


def _token(
    payload: dict[str, Any], secret: str, *, exp_delta: timedelta = timedelta(hours=1)
) -> str:
    exp = datetime.now(UTC) + exp_delta
    return jwt.encode({**payload, "exp": exp}, secret, algorithm="HS256")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _break_glass() -> str:
    return _token({"sub": "ops", "type": "admin"}, ADMIN_SECRET)


def _app(config: Any) -> TestClient:
    """실제 인가 의존성(오버라이드 없음)으로 관제 라우터를 올린다."""
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.state.config = config
    app.state.user_repo = _Users()
    return TestClient(app)


def _client(tmp_path: Path) -> TestClient:
    config = _make_config(tmp_path)
    config.auth.jwt_secret = AUTH_SECRET
    config.admin = SimpleNamespace(jwt_secret=ADMIN_SECRET)
    return _app(config)


def _alg_none(payload: dict[str, Any]) -> str:
    """서명 없는(alg=none) JWT — 직접 조립한다."""

    def b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    exp = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    return b64({"alg": "none", "typ": "JWT"}) + "." + b64({**payload, "exp": exp}) + "."


# ─── 1. 인가 — 구현자 표 밖의 거부 경우 ─────────────────────────────────────


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(
            _token({"sub": "ops", "type": "admin"}, ADMIN_SECRET, exp_delta=_PAST),
            id="break-glass-expired",
        ),
        pytest.param(
            _token({"sub": "u-admin", "type": "user", "role": "admin"}, AUTH_SECRET,
                   exp_delta=_PAST),
            id="admin-role-user-expired",
        ),
        pytest.param(_alg_none({"sub": "ops", "type": "admin"}), id="alg-none-admin"),
        pytest.param(_alg_none({"sub": "u-admin", "type": "user"}), id="alg-none-user"),
        # 운영자 시크릿 + type=user + role 클레임 admin — 운영자 경로는 type 불일치,
        # 사용자 경로는 시크릿 불일치로 둘 다 떨어진다
        pytest.param(
            _token({"sub": "u-admin", "type": "user", "role": "admin"}, ADMIN_SECRET),
            id="admin-secret-user-type-role-claim",
        ),
        # 사용자 시크릿 + type=admin + role 클레임 admin — 위와 반대 방향
        pytest.param(
            _token({"sub": "u-admin", "type": "admin", "role": "admin"}, AUTH_SECRET),
            id="user-secret-admin-type-role-claim",
        ),
        # 비활성 관리자 역할 사용자 — 저장소 상태가 막는다
        pytest.param(
            _token({"sub": "u-admin-off", "type": "user"}, AUTH_SECRET),
            id="inactive-admin-role-user",
        ),
    ],
)
def test_health_rejects_expired_unsigned_and_inactive(tmp_path: Path, token: str) -> None:
    res = _client(tmp_path).get(HEALTH, headers=_bearer(token))
    assert res.status_code == 401, res.text
    assert FIELD not in res.text


# ─── 2. 실 설정 모양 · 경로 ───────────────────────────────────────────────


def test_noise_gate_config_default_is_on_without_env() -> None:
    """코드 기본값 on(D-352 부기 2) — 클래스 필드 기본값으로 본다(.env 미독)."""
    assert NoiseGateConfig.model_fields[FIELD].default is True


@pytest.mark.parametrize("flag", [True, False])
def test_health_follows_real_noise_gate_config(tmp_path: Path, flag: bool) -> None:
    """SimpleNamespace 대역이 아니라 `NoiseGateConfig` 실물.

    검증 필드를 명시해 `.env` 누수를 막는다.
    """
    ng = NoiseGateConfig(
        _env_file=None,
        investigation_summary_view_enabled=flag,
        enable_noise_gate=True,
        decision_store_path=str(tmp_path / "decisions.jsonl"),
        decision_store_enabled=True,
    )
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=ng,
    )
    res = _app(config).get(HEALTH, headers=_bearer(_break_glass()))
    assert res.status_code == 200, res.text
    assert res.json()[FIELD] is flag


def test_health_response_model_served_only_on_operator_path() -> None:
    """헬스 응답 모델을 쓰는 경로는 `/admin/noise/health` 1개.

    사용자 경로로도 걸리는 읽기 라우터(read_router)에 헬스가 없다.
    """
    served = [
        r.path for r in noise_routes.router.routes
        if getattr(r, "response_model", None) is NoiseHealthResponse
    ]
    assert served == ["/admin/noise/health"]
    shared = [getattr(r, "path", "") for r in noise_routes.read_router.routes]
    assert not any("health" in p for p in shared), shared
    props = NoiseHealthResponse.model_json_schema()["properties"]
    assert props[FIELD]["type"] == "boolean"


def test_flag_exits_are_health_and_user_capabilities_only() -> None:
    """같은 값의 출구는 운영자 헬스와 기능 조회(사용자 인가 · W1부터) 둘뿐."""
    api = _ROOT / "src" / "api"
    hits = sorted(
        str(p.relative_to(_ROOT)) for p in api.rglob("*.py")
        if FIELD in p.read_text(encoding="utf-8")
    )
    assert hits == [
        "src/api/routes/alarm.py",
        "src/api/routes/alarm_schemas.py",
        "src/api/routes/noise_dashboard.py",
    ]
    src = (api / "routes" / "noise_dashboard.py").read_text(encoding="utf-8")
    body = src[src.index("async def noise_health("):src.index("# ─── 결정 조회")]
    assert "Depends(require_admin_user)" in body
    # 응답 모델 필드 1 + 헬스 핸들러(키워드 인자 · getattr 이름) 2
    assert src.count(FIELD) == 3
    assert body.count(FIELD) == 2


# ─── 3. noise.js — 판정 · 순서 · 재조회 ────────────────────────────────────


def _token_pick_block(src: str) -> str:
    start = src.index("    var IS_ADMIN = (opts.mode")
    end = src.index("    var API = IS_ADMIN ?")
    return src[start:end]


def _break_glass_expr(src: str) -> str:
    m = re.search(r"^\s*var IS_BREAK_GLASS = (.+?);", src, re.M)
    assert m, "IS_BREAK_GLASS 선언"
    return m.group(1)


@_needs_node
@pytest.mark.parametrize(
    ("opts", "store", "key", "bg"),
    [
        ({"mode": "admin"}, {"admin_token": "a"}, "admin_token", True),
        ({"mode": "admin"}, {"admin_token": "a", "user_token": "u"}, "admin_token", True),
        # 관리자 역할 사용자가 운영자 관제를 연 경우 — 헬스 값으로 덮지 않는다
        ({"mode": "admin"}, {"user_token": "u"}, "user_token", False),
        ({"mode": "user"}, {"user_token": "u", "admin_token": "a"}, "user_token", False),
        ({"mode": "user"}, {"admin_token": "a"}, "admin_token", True),
        # 포털 탭(mount 인자 token) — 늘 사용자 토큰 세션
        ({"mode": "admin", "token": "t"}, {"admin_token": "a"}, "user_token", False),
    ],
)
def test_break_glass_flag_from_real_token_selection(
    opts: dict[str, Any], store: dict[str, str], key: str, bg: bool
) -> None:
    src = _NOISE_JS.read_text(encoding="utf-8")
    out = _node(f"""
var opts = {_js(opts)};
var store = {_js(store)};
var localStorage = {{ getItem: function (k) {{ return store[k] || null; }} }};
var window = {{ location: {{ href: "" }} }};
function pick() {{
{_token_pick_block(src)}
    var IS_BREAK_GLASS = {_break_glass_expr(src)};
    return {{ key: tokenKey, bg: IS_BREAK_GLASS }};
}}
out = pick();
""")
    assert out == {"key": key, "bg": bg}


def test_summary_view_init_runs_before_first_health_call() -> None:
    """mount 동기 본문 순서 — 초기화 2개가 첫 `loadHealth()`보다 앞이다.

    loadHealth 안의 대입은 `await getJSON` 뒤라 초기화가 헬스 값을 덮지 않는다.
    """
    src = _NOISE_JS.read_text(encoding="utf-8")
    mount = _js_function_body(src, "mount")
    i_bg = mount.index("var IS_BREAK_GLASS = ")
    i_init = mount.index("var summaryViewOn = false;")
    i_first = mount.index("loadHealth().catch(function (err)")
    assert i_bg < i_init < i_first
    body = _js_function_body(src, "loadHealth")
    assert body.index("await getJSON(") < body.index("summaryViewOn =")


_HEALTH_STUBS = r"""
function stub() { return { style: {}, className: "", innerHTML: "", textContent: "",
    title: "", appendChild: function (c) { return c; } }; }
var document = { getElementById: function () { return stub(); },
    createTextNode: function (t) { return t; } };
function el() { return stub(); }
function fmtInt(v) { return String(v); }
function fmtAge(v) { return String(v); }
function fmtTime(v) { return String(v); }
function metaAlertText(a) { return String(a.type); }
var API = "/api/v1/admin/noise";
var HEALTH = { healthy: true, alerts: [], suppress_ratio: 0, suppress_ratio_threshold: 0.9,
    total: 0, last_event_ts: null, last_event_age_seconds: null, window_seconds: 3600,
    gate_enabled: true, investigation_summary_view_enabled: true };
async function getJSON() { return HEALTH; }
"""


def test_load_health_called_before_var_init_still_wins() -> None:
    """최악 순서(초기화 전에 호출)로도 참 — 대입이 await 뒤다."""
    body = _js_function_body(_NOISE_JS.read_text(encoding="utf-8"), "loadHealth")
    out = _node(_HEALTH_STUBS + f"""
function sim() {{
    var p = loadHealth();
    var IS_BREAK_GLASS = true;
    var summaryViewOn = false;
    async {body}
    return p.then(function () {{ return summaryViewOn; }});
}}
out = await sim();
""")
    assert out is True


def test_break_glass_reload_falls_back_to_operator_block_keeping_raw_view() -> None:
    """재조회 — 사용자 API(401) 실패 뒤 운영자 결정 블록으로 대신한다.

    그 블록도 원문 뷰(`isRawView`)라 카드의 「원문 포함」 판정이 재조회 뒤에도 같다.
    """
    src = _NOISE_JS.read_text(encoding="utf-8")
    view = _view("confirmed_cpu_ke")
    decision = _js_function_body(src, "decisionInvestigation")
    reload_ = _js_function_body(src, "reloadInvestigation")
    out = _node(f"""
var calls = [];
var API = "/api/v1/admin/noise";
var block = {_js(view)};
function authHeaders() {{ return {{ Authorization: "Bearer admin" }}; }}
async function fetch(path) {{ calls.push(["fetch", path]); return {{ ok: false, status: 401 }}; }}
async function getJSON(path) {{
    calls.push(["getJSON", path]);
    return {{ decision: {{ investigation: block }} }};
}}
async {decision}
async {reload_}
var got = await reloadInvestigation(block, "a1");
out = {{ calls: calls, raw: BV.isRawView(got),
    same: JSON.stringify(got) === JSON.stringify(block) }};
""")
    assert out["raw"] is True and out["same"] is True
    assert out["calls"] == [
        ["fetch", "/api/v1/alarm/investigations/" + view["investigation_id"]],
        ["getJSON", "/api/v1/admin/noise/decisions/a1"],
    ]


# ─── 4. 공용 렌더 — 픽스처 전 종류 ─────────────────────────────────────────


_PORTAL_MARKS = (
    "장애 진단에서 보기", "조사 대화로 열기", "이 알람 분석", "조사 시작", "cause-live-open",
    'data-bv-action="open-investigation"', 'data-bv-action="open-conversation"',
    'data-bv-action="open-previous"', 'data-bv-action="analyze"',
)
_FIXTURES = sorted(p.stem for p in _FIX.glob("*.json"))
_ORIGIN = "http://ops.example:8000"


def _bg_render(view: dict[str, Any], summary: bool | None) -> str:
    """noise.js renderInvestigation의 break-glass 옵션으로 attach 한다.

    상세형 · analyze 없음 · 진입 콜백 없음. summary가 None이면 summaryView 키 자체를 뺀다.
    """
    sv = "" if summary is None else (
        f"summaryView: function () {{ return {json.dumps(summary)}; }},"
    )
    return _node(f"""
globalThis.location = {{ origin: {json.dumps(_ORIGIN)} }};
function box() {{ return {{ innerHTML: "", isConnected: true,
    addEventListener: function () {{}}, contains: function () {{ return true; }},
    querySelectorAll: function () {{ return []; }},
    querySelector: function () {{ return null; }} }}; }}
var v = {_js(view)};
var c = box();
var p = BV.attach(c, {{ initial: v, load: function () {{ return Promise.resolve(v); }},
    render: {{ mode: "detail" }}, analyze: null, onView: function () {{}}, {sv} }});
await p.open(); await tick(); p.close();
out = c.innerHTML;
""")


@pytest.mark.parametrize("name", _FIXTURES)
def test_break_glass_drawer_no_portal_entry_and_link_is_text_only(name: str) -> None:
    html = _bg_render(_view(name), True)
    for mark in _PORTAL_MARKS:
        assert mark not in html, (name, mark)
    # 전달문 포털 주소는 미리보기 <pre> 글자로만 — 클릭되는 링크가 아니다(D-341 부기 18 Q-3 A)
    assert not re.search(r'href="[^"]*\?investigation=', html), name
    assert 'data-bv-summary="1"' in html, name
    assert "원문 포함" in html and 'data-bv-action="handoff-copy"' in html, name
    pre = re.search(r'<pre class="bv-pre bv-handoff-text">(.*?)</pre>', html, re.S)
    assert pre, name
    assert _ORIGIN + "/?investigation=" in pre.group(1), name


@pytest.mark.parametrize("name", _FIXTURES)
def test_break_glass_drawer_flag_off_is_byte_identical_to_pre_w1(name: str) -> None:
    """꺼짐(헬스 값 거짓 · 첫 응답 전) = summaryView 옵션 없는 렌더와 바이트 동일.

    D-352 · D-162 — W6 이전 break-glass 서랍(summaryViewOn 늘 거짓)과도 같다.
    """
    view = _view(name)
    off = _bg_render(view, False)
    absent = _bg_render(view, None)
    assert off == absent, name
    assert 'data-bv-summary="1"' not in off and "원문 포함" not in off
