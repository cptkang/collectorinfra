"""plans/104 A-7 — 관리자 「DB 구조」 탭 화면 배선·정적 점검.

브라우저 도구가 없으므로 TestClient로 HTML·정적 자산 서빙과 인증 경계를 확인하고,
JS는 정적으로 점검한다.
- `/admin` HTML에 새 탭·탭 콘텐츠·스크립트·설정 저장 경고 배너가 있다.
- 새 스크립트가 200으로 서빙된다.
- 로그인 안 한 첫 방문자: HTML은 JS 게이트(토큰 없으면 `/login?next=/admin`) ·
  새 API는 401(서비스 생성 전 차단).
- XSS 방지: 새 스크립트에 HTML 문자열 주입 API가 없다 · 설정 경고 배너도 textContent로만 그린다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.routes import db_structure
from src.api.server import create_app
from src.config import AdminConfig, AppConfig, AuthConfig

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"
_DB_JS = _STATIC / "js" / "admin-db-structure.js"
_ADMIN_JS = _STATIC / "js" / "admin.js"


@pytest.fixture(scope="module")
def client() -> TestClient:
    config = AppConfig(
        _env_file=None,
        auth=AuthConfig(
            _env_file=None, enabled=True, jwt_secret="plan104-ui-auth-secret-0123456789"
        ),
        admin=AdminConfig(_env_file=None, jwt_secret="plan104-ui-admin-secret-0123456789"),
    )
    return TestClient(create_app(config))  # lifespan 미기동


def test_admin_page_has_db_structure_tab_and_script(client: TestClient) -> None:
    response = client.get("/admin")
    assert response.status_code == 200
    html = response.text
    assert 'data-tab="dbstructure"' in html
    assert 'id="tab-dbstructure"' in html
    assert 'id="readinessBanner"' in html
    admin_js = html.index('src="/static/js/admin.js')
    db_js = html.index('src="/static/js/admin-db-structure.js')
    assert admin_js < db_js  # 탭 스크립트는 admin.js가 노출한 헬퍼를 쓴다


def test_db_structure_script_is_served(client: TestClient) -> None:
    response = client.get("/static/js/admin-db-structure.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "window.AdminApi" in response.text


def test_unauthenticated_first_visit_api_is_401_before_service_is_built(
    client: TestClient, monkeypatch
) -> None:
    """토큰 없는 요청은 서비스·잡 러너를 만들기 전에 401로 끊긴다(override 없이 실제 의존성)."""

    def must_not_build(config):  # noqa: ANN001
        raise AssertionError("인증 전에 서비스를 만들면 안 된다")

    monkeypatch.setattr(db_structure, "build_structure_service", must_not_build)
    monkeypatch.setattr(db_structure, "build_registration_service", must_not_build)
    for method, path in [
        ("get", "/api/v1/admin/db-structure/sources"),
        ("post", "/api/v1/admin/db-structure/itam/check"),
        ("get", "/api/v1/admin/db-structure/itam/registration"),
    ]:
        assert getattr(client, method)(path).status_code == 401


def test_admin_js_gate_runs_before_helpers_are_exposed() -> None:
    """토큰이 없으면 로그인으로 보내고 return — 그 뒤에 있는 AdminApi 노출에 도달하지 않는다."""
    text = _ADMIN_JS.read_text(encoding="utf-8")
    gate = text.index('window.location.href = "/login?next=/admin";')
    exposure = text.index("window.AdminApi = {")
    assert gate < exposure
    db_text = _DB_JS.read_text(encoding="utf-8")
    assert re.search(r"if \(!api \|\| !tabButton \|\| !tabContent\) return;", db_text)


@pytest.mark.parametrize(
    "sink",
    ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"],
)
def test_db_structure_script_has_no_html_injection_sinks(sink: str) -> None:
    assert sink not in _DB_JS.read_text(encoding="utf-8")


def test_readiness_banner_renders_with_text_nodes_only() -> None:
    text = _ADMIN_JS.read_text(encoding="utf-8")
    start = text.index("function showReadinessWarnings(data)")
    end = text.index("// --- 설정 리로드", start)
    body = text[start:end]
    assert "innerHTML" not in body
    assert "textContent" in body


def test_script_calls_only_declared_routes() -> None:
    """스크립트가 부르는 경로 접미사가 라우터에 선언돼 있다(오타·계약 이탈 방지)."""
    script = _DB_JS.read_text(encoding="utf-8")
    declared = {route.path for route in db_structure.router.routes}
    expected_suffixes = {
        '"/sources"': "/admin/db-structure/sources",
        '"/jobs/"': "/admin/db-structure/jobs/{job_id}",
        '"/registration"': "/admin/db-structure/{source}/registration",
        '"/check"': "/admin/db-structure/{source}/check",
        '"/analyze"': "/admin/db-structure/{source}/analyze",
        '"/legacy/draft"': "/admin/db-structure/{source}/legacy/draft",
        '"/register"': "/admin/db-structure/{source}/register",
        '"/approve"': "/admin/db-structure/{source}/drafts/{draft_id}/approve",
        '"/reject"': "/admin/db-structure/{source}/drafts/{draft_id}/reject",
        '"/rollback"': "/admin/db-structure/{source}/versions/{ver}/rollback",
        '"/diff-report?draft_id="': "/admin/db-structure/{source}/diff-report",
        '"/apply"': "/admin/db-structure/{source}/description-drafts/{draft_id}/apply",
        '"/discard"': "/admin/db-structure/{source}/description-drafts/{draft_id}/discard",
        '"/db-description"': "/admin/db-structure/{source}/db-description",
        '"/config-snippets"': "/admin/db-structure/{source}/config-snippets",
    }
    for literal, route_path in expected_suffixes.items():
        assert literal in script, literal
        assert route_path in declared, route_path
