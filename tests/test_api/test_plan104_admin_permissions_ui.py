"""plans/104 C-4(화면 몫) — 관리자 「사용자 관리」 탭의 DB 접근 권한 편집 UI 정적·경계 점검.

배경: `AUTH_DEFAULT_ALLOWED_DB_IDS`를 실제로 적용하면 신규 가입자는 설정값(빈 값이면 조회 불가)을
받는데, 관리자 화면에 권한을 **부여**하는 자리가 없어 회수만 가능했다. 화면만 추가하고
저장은 기존 `PUT /api/v1/admin/users/{user_id}/permissions`를 쓴다.

브라우저 도구가 없으므로 JS는 정적으로 점검하고, 서빙·인증 경계만 TestClient로 확인한다.
- 사용자 표에 「조회 가능 DB」 열 머리말이 있고, 관리자 역할 예외가 문구로 적혀 있다.
- admin.js가 권한 셀 렌더·편집 진입·저장(PUT …/permissions)을 갖는다.
- 새 코드 블록에 HTML 주입 싱크가 0건이다(서버 값은 textContent로만 그린다).
- 비로그인 401 · 비관리자 403(기존 RBAC 규약 그대로).
"""

from __future__ import annotations

import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.config import AdminConfig, AppConfig, AuthConfig

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"
_ADMIN_JS = _STATIC / "js" / "admin.js"
_DASHBOARD_HTML = _STATIC / "admin" / "dashboard.html"

AUTH_SECRET = "plan104-perm-ui-auth-secret-0123456789"
ADMIN_SECRET = "plan104-perm-ui-admin-secret-0123456789"
PERM_PATH = "/api/v1/admin/users/u1/permissions"


@pytest.fixture(scope="module")
def client() -> TestClient:
    config = AppConfig(
        _env_file=None,
        auth=AuthConfig(_env_file=None, enabled=True, jwt_secret=AUTH_SECRET),
        admin=AdminConfig(_env_file=None, jwt_secret=ADMIN_SECRET),
    )
    return TestClient(create_app(config))  # lifespan 미기동 → user_repo 없음


def _user_token(role: str) -> str:
    return jwt.encode(
        {
            "sub": "u1",
            "name": "U",
            "role": role,
            "type": "user",
            "exp": int(time.time()) + 3600,
        },
        AUTH_SECRET,
        algorithm="HS256",
    )


def _perm_js_block() -> str:
    """admin.js에서 이번에 추가한 DB 권한 블록만 잘라낸다."""
    text = _ADMIN_JS.read_text(encoding="utf-8")
    start = text.index("// --- 사용자 DB 접근 권한 (plans/104 C-4) ---")
    end = text.index("function escapeHtml(", start)
    return text[start:end]


# ─── 표 머리말·안내 문구 ─────────────────────────────────────────────────────


def test_users_table_has_allowed_db_column_header() -> None:
    html = _DASHBOARD_HTML.read_text(encoding="utf-8")
    users_table = html[html.index('id="usersTable"'):html.index('id="usersBody"')]
    assert "<th>알림그룹</th>" in users_table
    assert "조회 가능 DB" in users_table
    # 열 순서: 알림그룹 → 조회 가능 DB → 마지막 로그인 (렌더 쪽 insertBefore와 일치)
    assert users_table.index("알림그룹") < users_table.index("조회 가능 DB")
    assert users_table.index("조회 가능 DB") < users_table.index("마지막 로그인")


def test_admin_role_exception_is_stated_on_screen() -> None:
    """관리자 역할은 권한 목록과 무관하게 전체 조회가 허용된다 — 서버 동작과 같은 문구."""
    html = _DASHBOARD_HTML.read_text(encoding="utf-8")
    users_tab = html[html.index('id="tab-users"'):html.index('id="usersBody"')]
    assert "전체 DB 조회가 허용" in users_tab
    assert "관리자가 직접 지정" in users_tab


def test_dashboard_serves_users_tab(client: TestClient) -> None:
    response = client.get("/admin")
    assert response.status_code == 200
    assert "조회 가능 DB" in response.text


# ─── admin.js 배선 ──────────────────────────────────────────────────────────


def test_admin_js_is_served(client: TestClient) -> None:
    response = client.get("/static/js/admin.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "조회 가능 DB" in response.text or "renderPermCell" in response.text


def test_perm_cell_is_rendered_into_users_row() -> None:
    text = _ADMIN_JS.read_text(encoding="utf-8")
    assert 'var permTd = document.createElement("td");' in text
    assert "renderPermCell(permTd, u.user_id, u.allowed_db_ids, u.role === \"admin\");" in text
    assert 'tr.insertBefore(permTd, tr.querySelector(".last-login-cell"));' in text


def test_perm_labels_cover_null_empty_and_list() -> None:
    block = _perm_js_block()
    assert 'if (allowed === null || allowed === undefined) return "전체";' in block
    assert 'if (allowed.length === 0) return "없음(관리자 지정 필요)";' in block
    assert "return allowed.join(\", \");" in block


def test_editor_offers_active_dbs_and_allow_all() -> None:
    block = _perm_js_block()
    assert "function openPermEditor(" in block
    assert 'chk.className = "perm-db-chk";' in block          # 활성 DB 체크박스
    assert 'allChk.className = "perm-all-chk";' in block      # 「전체 허용(null)」 선택지
    assert '"전체 허용"' in block
    assert '"활성 DB 없음"' in block                            # 후보가 비면 전체 허용만 가능
    assert "chk.disabled = allChk.checked;" in block


def test_active_db_candidates_come_from_health_db_status_map() -> None:
    """후보 목록은 화면이 이미 받는 /api/v1/health 응답에서 온다(권한 전용 API 없음)."""
    text = _ADMIN_JS.read_text(encoding="utf-8")
    assert "activeDbIds = dbIds.slice();" in text
    health_calls = text.count('fetch("/api/v1/health")')
    assert health_calls == 1, health_calls
    assert "/api/v1/admin/db-permissions" not in text


def test_save_calls_permissions_endpoint_and_reports_result() -> None:
    block = _perm_js_block()
    assert '"/api/v1/admin/users/" + encodeURIComponent(uid) + "/permissions"' in block
    assert "{allowed_db_ids: allowed}" in block
    assert "allChk.checked ? null : selected" in block        # 전체 허용이면 null 저장
    assert "showSuccess(" in block
    assert "showError(errorMessage(data, \"DB 권한 저장에 실패했습니다.\"));" in block


def test_protected_account_can_still_edit_db_permissions() -> None:
    """D-083 보호는 역할·상태·삭제만 — 권한 편집 버튼에는 disabled를 걸지 않는다."""
    block = _perm_js_block()
    assert "protAttr" not in block
    assert "disabled" not in block.replace("chk.disabled = allChk.checked;", "")


# ─── XSS: 새 코드에 HTML 주입 싱크 0건 ───────────────────────────────────────


@pytest.mark.parametrize(
    "sink",
    ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"],
)
def test_permissions_block_has_no_html_injection_sinks(sink: str) -> None:
    assert sink not in _perm_js_block()


def test_permissions_block_uses_text_nodes_for_server_values() -> None:
    block = _perm_js_block()
    assert "textContent" in block
    assert "createTextNode(dbId)" in block


# ─── 인증 경계 (기존 RBAC 규약) ──────────────────────────────────────────────


def test_permissions_endpoint_requires_login(client: TestClient) -> None:
    assert client.put(PERM_PATH, json={"allowed_db_ids": ["polestar"]}).status_code == 401


def test_permissions_endpoint_rejects_non_admin(client: TestClient) -> None:
    response = client.put(
        PERM_PATH,
        json={"allowed_db_ids": ["polestar"]},
        headers={"Authorization": "Bearer " + _user_token("user")},
    )
    assert response.status_code == 403


def test_permissions_endpoint_passes_admin_gate(client: TestClient) -> None:
    """관리자 토큰은 RBAC를 통과한다(저장소 미기동이라 503 — 403/401이 아님)."""
    response = client.put(
        PERM_PATH,
        json={"allowed_db_ids": ["polestar"]},
        headers={"Authorization": "Bearer " + _user_token("admin")},
    )
    assert response.status_code == 503
