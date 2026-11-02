"""plans/201 W1 독립 검증(verifier) — 실제 앱 조립(`create_app`) + 실 JWT로 계약을 확인한다.

implementer 테스트(`test_plan201_w1_duty_areas.py`)는 SimpleNamespace 앱·직접 호출 위주라,
여기서는 다음을 실제 앱 경로로 다시 고정한다.

  A. 관리자 현황 인가 — 비로그인 401 · 일반 사용자 403 · 토큰 type/시크릿 교차 거부 ·
     토큰 role 클레임이 아니라 DB 실시간 role로 판정 · break-glass 운영자 허용.
  B. 플래그 off 비트 동일 — `/auth/me`·`/admin/users`·`PUT /admin/users/{id}` 응답 키 집합 ·
     요청 duty_areas 무시·미저장 · 라우트 404.
  C. 422 경로 — 비문자열·None 원소·문자열 단일값·어휘 밖 · 가입 422는 생성 전.
  D. 권한 비참조 — `duty_area`를 참조하는 모듈이 허용 목록 밖에 없다(인가 모듈 전수).
  E. XSS — 담당 업무 프런트 코드가 서버 값을 HTML 문자열로 넣지 않는다.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api import duty_area_support
from src.domain.duty_areas import parse_vocabulary
from src.domain.user import User, UserRole, UserStatus
from src.security.audit_service import AuditService

_ROOT = Path(__file__).resolve().parents[1]
_USER_SECRET = "u" * 40
_ADMIN_SECRET = "a" * 40

_VOCAB = parse_vocabulary({
    "areas": [
        {"code": "alpha", "label": "알파", "resource_types": ["kind.One"]},
        {"code": "beta", "label": "베타", "resource_types": ["kind.Two"]},
    ],
})

# 기준 SHA(a289818f)의 UserInfoResponse 필드 순서 — off 응답은 이것과 같아야 한다
_BASE_USER_KEYS = ["user_id", "username", "role", "department", "allowed_db_ids",
                   "alarm_zones", "is_protected", "status", "last_login_at"]


@pytest.fixture(autouse=True)
def _vocab(monkeypatch):
    monkeypatch.setattr(duty_area_support, "load_duty_area_vocabulary", lambda: _VOCAB)


class _CaptureRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, event: dict) -> None:
        self.events.append(event)


def _user(user_id: str = "u1", role: UserRole = UserRole.USER, **kw) -> User:
    return User(user_id=user_id, username=user_id, hashed_password="x", role=role,
                status=UserStatus.ACTIVE, **kw)


def _token(sub: str, *, secret: str = _USER_SECRET, typ: str = "user", **claims) -> str:
    payload = {"sub": sub, "type": typ, "exp": datetime.now(UTC) + timedelta(hours=1), **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


def _hdr(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def _app(tmp_path: Path, *, enabled: bool, users: dict[str, User] | None = None,
         auth_enabled: bool = True):
    from src.api.server import create_app
    from src.config import AdminConfig, AppConfig, AuthConfig, DutyAreaConfig, NoiseGateConfig

    store = tmp_path / "decisions.jsonl"
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    store.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in [
            {"ts": recent, "resource_type": "kind.One"},
            {"ts": recent, "resource_type": "<img src=x onerror=alert(1)>"},
        ]) + "\n",
        encoding="utf-8",
    )
    cfg = AppConfig(
        auth=AuthConfig(enabled=auth_enabled, jwt_secret=_USER_SECRET),
        admin=AdminConfig(jwt_secret=_ADMIN_SECRET),
        duty_area=DutyAreaConfig(enabled=enabled),
        noise_gate=NoiseGateConfig(decision_store_path=str(store), decision_store_enabled=True),
    )
    app = create_app(cfg)
    users = dict(users or {})
    repo = SimpleNamespace(
        users=users,
        # 실 저장소(Postgres)처럼 조회마다 새 객체 — 422 전 메모리 변경이 저장소에 새지 않게
        get_by_user_id=AsyncMock(side_effect=lambda uid: copy.deepcopy(users.get(uid))),
        list_all=AsyncMock(side_effect=lambda: list(users.values())),
        exists=AsyncMock(side_effect=lambda uid: uid in users),
        create=AsyncMock(side_effect=lambda u: users.__setitem__(u.user_id, u)),
        update=AsyncMock(side_effect=lambda u: users.__setitem__(u.user_id, u)),
        count_active_admins=AsyncMock(return_value=5),
    )
    app.state.user_repo = repo
    captured = _CaptureRepo()
    audit_cfg = SimpleNamespace(jsonl_enabled=False, db_enabled=True,
                                alert_on_failed_login=5, alert_on_large_result=5000)
    app.state.audit_service = AuditService(audit_cfg, captured)
    return app, repo, captured


# ─── A. 관리자 현황 인가 ─────────────────────────────────────────────────────

_STATUS = "/api/v1/admin/duty-areas/status"


def test_status_authz_matrix(tmp_path) -> None:
    users = {"plain": _user("plain"), "boss": _user("boss", role=UserRole.ADMIN)}
    app, _, _ = _app(tmp_path, enabled=True, users=users)
    c = TestClient(app)
    assert c.get(_STATUS).status_code == 401                                   # 비로그인
    assert c.get(_STATUS, headers={"Authorization": "x"}).status_code == 401   # 형식 불량
    assert c.get(_STATUS, headers=_hdr(_token("plain"))).status_code == 403    # 일반 사용자
    # 토큰 role 클레임을 admin으로 위조해도 DB 실시간 role(user)로 판정 → 403
    assert c.get(_STATUS, headers=_hdr(_token("plain", role="admin"))).status_code == 403
    # 사용자 시크릿으로 서명한 type=admin 토큰 → break-glass 불가 + 사용자 토큰도 아님 → 401
    assert c.get(_STATUS, headers=_hdr(_token("plain", typ="admin"))).status_code == 401
    # 운영자 시크릿으로 서명한 type=user 토큰 → 둘 다 실패 → 401
    assert c.get(_STATUS, headers=_hdr(
        _token("boss", secret=_ADMIN_SECRET, typ="user"))).status_code == 401
    # type 클레임 없음 → 401
    no_type = jwt.encode({"sub": "boss", "exp": datetime.now(UTC) + timedelta(hours=1)},
                         _USER_SECRET, algorithm="HS256")
    assert c.get(_STATUS, headers=_hdr(no_type)).status_code == 401
    # 정상 관리자(DB role admin) · break-glass 운영자 토큰 → 200
    assert c.get(_STATUS, headers=_hdr(_token("boss"))).status_code == 200
    assert c.get(_STATUS, headers=_hdr(
        _token("ops", secret=_ADMIN_SECRET, typ="admin"))).status_code == 200


def test_status_body_carries_raw_resource_type_for_text_rendering(tmp_path) -> None:
    """결정 기록 유래 resource_type은 원문 그대로 JSON에 실린다 — 프런트가 textContent로 그려야 한다(E)."""
    app, _, _ = _app(tmp_path, enabled=True, users={"boss": _user("boss", role=UserRole.ADMIN)})
    body = TestClient(app).get(_STATUS, headers=_hdr(_token("boss"))).json()
    (sec,) = body["sections"]
    assert [t["resource_type"] for t in sec["top"]] == ["<img src=x onerror=alert(1)>"]
    assert body["badge_count"] == 1
    assert body["assignment"]["total_users"] == 1


def test_public_options_exposes_code_label_only(tmp_path) -> None:
    """무인증 공개 메타는 code·label뿐 — resource_types 등 내부 매핑은 노출하지 않는다."""
    app, _, _ = _app(tmp_path, enabled=True)
    resp = TestClient(app).get("/api/v1/auth/duty-areas")
    assert resp.status_code == 200
    for opt in resp.json()["options"]:
        assert set(opt) == {"code", "label"}


# ─── B. 플래그 off 비트 동일 ─────────────────────────────────────────────────


def test_off_routes_absent_and_response_keys_unchanged(tmp_path) -> None:
    boss = _user("boss", role=UserRole.ADMIN, duty_areas=["alpha"])
    plain = _user("plain", duty_areas=["beta"])
    app, repo, captured = _app(tmp_path, enabled=False, users={"boss": boss, "plain": plain})
    c = TestClient(app)
    adm = _hdr(_token("boss"))
    assert c.get(_STATUS, headers=adm).status_code == 404
    assert c.get("/api/v1/auth/duty-areas").status_code == 404

    me = c.get("/api/v1/auth/me", headers=_hdr(_token("plain"))).json()
    assert list(me) == _BASE_USER_KEYS

    listed = c.get("/api/v1/admin/users", headers=adm).json()
    assert all("duty_areas" not in u and "duty_area_options" not in u for u in listed)

    resp = c.put("/api/v1/admin/users/plain", headers=adm,
                 json={"duty_areas": ["alpha"], "department": "ops"})
    assert resp.status_code == 200
    assert list(resp.json()) == _BASE_USER_KEYS
    assert repo.users["plain"].duty_areas == ["beta"]             # 무시 · 미저장
    # off에서도 어휘 밖 값은 검증하지 않는다(무시) — 422 아님
    assert c.put("/api/v1/admin/users/plain", headers=adm,
                 json={"duty_areas": ["nope"]}).status_code == 200
    changes = [e["detail"]["extra"]["changes"] for e in captured.events
               if (e.get("detail") or {}).get("extra", {}).get("action") == "user_update"]
    assert changes and all("duty_areas" not in ch for ch in changes)


def test_on_response_keys_added_only(tmp_path) -> None:
    plain = _user("plain", duty_areas=["beta", "retired"])
    app, _, _ = _app(tmp_path, enabled=True, users={"plain": plain})
    me = TestClient(app).get("/api/v1/auth/me", headers=_hdr(_token("plain"))).json()
    assert list(me) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options"]
    assert me["duty_areas"] == ["beta"]                           # 폐지 코드 무시


def test_flag_resolved_once_at_startup(tmp_path) -> None:
    """설정 리로드가 app.state.config를 바꿔도 기동 시 해석한 값은 그대로다."""
    from src.config import AppConfig, DutyAreaConfig

    app, _, _ = _app(tmp_path, enabled=False)
    app.state.config = AppConfig(duty_area=DutyAreaConfig(enabled=True))
    assert app.state.duty_area_enabled is False
    assert TestClient(app).get("/api/v1/auth/duty-areas").status_code == 404


# ─── C. 422 경로 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    [1], [None], "alpha", {"a": 1}, ["alpha", "zzz"], ["ALPHA"],
])
def test_admin_put_rejects_bad_payload_without_saving(tmp_path, payload) -> None:
    boss = _user("boss", role=UserRole.ADMIN)
    plain = _user("plain", duty_areas=["beta"])
    app, repo, _ = _app(tmp_path, enabled=True, users={"boss": boss, "plain": plain})
    resp = TestClient(app).put("/api/v1/admin/users/plain", headers=_hdr(_token("boss")),
                               json={"duty_areas": payload, "department": "changed"})
    assert resp.status_code == 422
    repo.update.assert_not_called()
    assert repo.users["plain"].department is None and repo.users["plain"].duty_areas == ["beta"]


def test_admin_put_empty_clears_and_whitespace_dup_normalized(tmp_path) -> None:
    boss = _user("boss", role=UserRole.ADMIN)
    plain = _user("plain", duty_areas=["beta"])
    app, repo, captured = _app(tmp_path, enabled=True, users={"boss": boss, "plain": plain})
    c = TestClient(app)
    r = c.put("/api/v1/admin/users/plain", headers=_hdr(_token("boss")),
              json={"duty_areas": [" beta ", "alpha", "beta"]})
    assert r.status_code == 200 and r.json()["duty_areas"] == ["alpha", "beta"]
    r = c.put("/api/v1/admin/users/plain", headers=_hdr(_token("boss")), json={"duty_areas": []})
    assert r.status_code == 200 and r.json()["duty_areas"] is None
    assert repo.users["plain"].duty_areas is None
    updates = [e["detail"]["extra"] for e in captured.events
               if (e.get("detail") or {}).get("extra", {}).get("action") == "user_update"]
    assert updates[0]["target_user_id"] == "plain"
    assert updates[0]["changes"]["duty_areas"] == {"before": ["beta"], "after": ["alpha", "beta"]}
    assert updates[1]["changes"]["duty_areas"] == {"before": ["alpha", "beta"], "after": None}
    assert all(e["user_id"] == "boss" for e in captured.events
               if (e.get("detail") or {}).get("extra", {}).get("action") == "user_update")


@pytest.mark.parametrize("payload", [[1], "alpha", ["zzz"]])
def test_register_422_before_any_creation(tmp_path, payload) -> None:
    app, repo, captured = _app(tmp_path, enabled=True, auth_enabled=True)
    resp = TestClient(app).post("/api/v1/auth/register", json={
        "user_id": "newbie", "username": "새", "password": "password123", "duty_areas": payload,
    })
    assert resp.status_code == 422
    repo.create.assert_not_called()
    assert "newbie" not in repo.users
    assert not [e for e in captured.events if e.get("event_type") == "register"
                or (e.get("detail") or {}).get("event") == "register"]


def test_register_off_ignores_and_audit_detail_unchanged(tmp_path) -> None:
    app, repo, captured = _app(tmp_path, enabled=False, auth_enabled=True)
    resp = TestClient(app).post("/api/v1/auth/register", json={
        "user_id": "newbie", "username": "새", "password": "password123", "duty_areas": ["zzz"],
    })
    assert resp.status_code in (200, 201)
    assert "duty_areas" not in resp.json()
    assert repo.users["newbie"].duty_areas is None
    extras = [e["detail"].get("extra") for e in captured.events if isinstance(e.get("detail"), dict)]
    reg = [x for x in extras if isinstance(x, dict) and "username" in x and "department" in x]
    assert reg and all("duty_areas" not in x for x in reg)


def test_register_unbounded_unknown_list_echo(tmp_path) -> None:
    """[관찰] 무인증 가입 경로가 어휘 밖 값 목록 길이를 제한하지 않고 422 detail에 전부 되돌린다."""
    app, repo, _ = _app(tmp_path, enabled=True, auth_enabled=True)
    junk = [f"x{i}" for i in range(5000)]
    resp = TestClient(app).post("/api/v1/auth/register", json={
        "user_id": "newbie", "username": "새", "password": "password123", "duty_areas": junk,
    })
    assert resp.status_code == 422
    repo.create.assert_not_called()
    # 교정 후: 목록 길이 상한(32)으로 거부하고 detail은 입력을 되돌려 싣지 않는다
    assert len(str(resp.json()["detail"])) < 200


# ─── D. 권한 비참조 — 참조 모듈 허용 목록 ────────────────────────────────────

_ALLOWED_REFERRERS = {
    "src/api/duty_area_support.py",
    "src/api/routes/admin.py",
    "src/api/routes/duty_areas.py",
    "src/api/routes/user_auth.py",
    "src/api/schemas.py",
    "src/api/server.py",
    "src/api/settings_catalog.py",
    "src/config/services.py",
    "src/config/__init__.py",
    "src/domain/duty_areas.py",
    "src/domain/user.py",
    "src/infrastructure/duty_area_vocabulary.py",
    "src/infrastructure/user_repository.py",
    # (plans/201 W3) 알람 화면 담당 강조 — 통보 노드가 `cfg.duty_area.alarm_focus_effective`로 SSE 키를
    # 실을지만 정한다(인가 판정 아님 · 존·티어 가시성 `event_visible_to`는 이 값을 읽지 않는다).
    "noise_gate/application/nodes/alarm_notifier.py",
    # (plans/201 W6) 첫 화면 24시간 요약 집계 — 담당 어휘로 **보이는** 알람 중 겹침만 센다(인가 판정 아님 ·
    # 존·SUPPRESS 공개는 `event_visible_to`가 하고 그 함수는 이 값을 읽지 않는다).
    "src/api/duty_daily_summary.py",
}


def test_duty_area_referenced_only_by_allowlisted_modules() -> None:
    """인가 계열(db_authz·db_scope·host_authz·doc_qa/authz·query/gates·apm targets 등) 전수 —
    허용 목록 밖 모듈이 duty_area를 읽으면 실패한다(새 소비처는 의도 확인 후 여기에 추가)."""
    found = set()
    for pkg in ("src", "noise_gate"):
        for p in (_ROOT / pkg).rglob("*.py"):
            rel = p.relative_to(_ROOT).as_posix()
            if "/tests/" in rel:
                continue
            if "duty_area" in p.read_text(encoding="utf-8", errors="ignore"):
                found.add(rel)
    assert found <= _ALLOWED_REFERRERS, sorted(found - _ALLOWED_REFERRERS)


def test_auth_dict_and_token_claims_exclude_duty() -> None:
    u = _user(duty_areas=["alpha"])
    assert "duty_areas" not in u.to_auth_dict()
    src = (_ROOT / "src/api/routes/user_auth.py").read_text(encoding="utf-8")
    # 토큰 발급 payload에 duty 키를 싣지 않는다
    for m in re.finditer(r"jwt\.encode\((.*?)\)", src, re.S):
        assert "duty" not in m.group(1)


# ─── E. XSS — 프런트 담당 업무 코드 ─────────────────────────────────────────


def _admin_duty_block() -> str:
    text = (_ROOT / "src/static/js/admin.js").read_text(encoding="utf-8")
    start = text.index("var dutyState = null")
    end = text.index("// --- 사용자 DB 접근 권한", start)
    return text[start:end]


def test_admin_duty_block_never_builds_html_strings() -> None:
    block = _admin_duty_block()
    assert "insertAdjacentHTML" not in block and "outerHTML" not in block
    for m in re.finditer(r"\.innerHTML\s*=\s*([^;]+);", block):
        assert m.group(1).strip() in ('""', "''"), m.group(0)
    # 결정 기록 유래 resource_type은 textContent로만
    assert re.search(r"body\.textContent\s*=.*\n.*t\.resource_type", block)


def test_register_and_app_duty_render_via_text_nodes() -> None:
    reg = (_ROOT / "src/static/register.html").read_text(encoding="utf-8")
    seg = reg[reg.index('fetch("/api/v1/auth/duty-areas")'):]
    seg = seg[:seg.index("form.addEventListener")]
    assert "innerHTML" not in seg and "createTextNode(opt.label)" in seg
    app_js = (_ROOT / "src/static/js/app.js").read_text(encoding="utf-8")
    seg = app_js[app_js.index("function showDutyAreaNotice"):]
    seg = seg[:seg.index("function loadDutyAreaNotice")]
    assert "innerHTML" not in seg


def test_on_admin_list_carries_duty_through_response_model(tmp_path) -> None:
    """response_model 재검증(FastAPI)을 거쳐도 on이면 duty_areas 키가 남는다(직접 호출이 아닌 실 경로)."""
    boss = _user("boss", role=UserRole.ADMIN)
    plain = _user("plain", duty_areas=["beta"])
    app, _, _ = _app(tmp_path, enabled=True, users={"boss": boss, "plain": plain})
    listed = TestClient(app).get("/api/v1/admin/users", headers=_hdr(_token("boss"))).json()
    by_id = {u["user_id"]: u for u in listed}
    assert by_id["plain"]["duty_areas"] == ["beta"] and by_id["boss"]["duty_areas"] is None
    assert all("duty_area_options" not in u for u in listed)
