"""plans/215 W5 — 「업무 서비스 정의」(P1) 정적 자산(HTML · JS · CSS) · 진입 링크.

브라우저 없이 소스를 읽어 화면 규칙을 겨눈다(망·LLM 호출 0):
    1. 금지 — HTML 문자열 주입 · 브라우저 확인 대화상자 · 새 탭(P1 페이지 안) · 「정상」 문구 ·
       하드코딩 색.
    2. 배선 — JS가 찾는 id가 HTML에 모두 있다 · 새 API 경로를 부른다 · 충돌·빈 상태·스냅샷
       변경 문구.
    3. 진입 — 포털 매뉴얼 메뉴 링크(기능 조회 `biz_service_enabled`일 때만) · 관리자 대시보드 링크.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body

_STATIC = Path(__file__).resolve().parents[1] / "src" / "static"
_HTML = (_STATIC / "biz-services.html").read_text(encoding="utf-8")
_JS = (_STATIC / "js" / "biz-services.js").read_text(encoding="utf-8")
_CSS = (_STATIC / "css" / "biz-services.css").read_text(encoding="utf-8")


@pytest.mark.parametrize("src", [_HTML, _JS], ids=["html", "js"])
def test_no_html_injection_confirm_or_new_tab(src: str) -> None:
    assert "innerHTML" not in src and "outerHTML" not in src and "insertAdjacentHTML" not in src
    assert "confirm(" not in src and "alert(" not in src
    assert "_blank" not in src and "window.open" not in src
    assert "정상" not in src


def test_css_uses_theme_tokens_only() -> None:
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", _CSS)
    assert not re.search(r"\brgba?\(", _CSS)
    assert "var(--" in _CSS


def test_js_ids_exist_in_html() -> None:
    ids = set(re.findall(r'\bel\("([A-Za-z]+)"\)', _JS))
    assert len(ids) > 40
    missing = sorted(i for i in ids if f'id="{i}"' not in _HTML)
    assert missing == []
    assert '<script src="/static/js/biz-services.js' in _HTML
    assert '/static/css/biz-services.css' in _HTML


def test_admin_controls_hidden_until_role_known() -> None:
    for marker in ('data-section="review" data-admin hidden',
                   'data-section="compute" data-admin hidden',
                   'data-section="analysis" data-admin hidden', 'id="bsNew" data-admin hidden'):
        assert marker in _HTML
    assert 'role === "admin"' in _JS and "data.is_admin === false" in _JS


def test_js_calls_w5_endpoints_and_preview_before_writes() -> None:
    for path in ('"/api/v1/biz-services"', '"/api/v1/admin/biz-services"', '"/export.csv"',
                 '"/inventory/"', '"/unassigned"', '"/bulk-assign"', '"/members/layer"',
                 '"/history"', '"/candidates/bulk"', '"/merge"', '"/split"', '"/members/move"',
                 '"/gateway-business-map"', '"/apm-compare"', '"/preview"', '"/apply"'):
        assert path in _JS, path
    # 옮기기·계층·분할·병합·일괄 지정·후보 일괄·적용은 미리보기 패널을 거친다
    assert _JS.count("previewPanel(") >= 9
    for fn in ("moveForm", "layerForm", "splitForm", "previewAssign", "previewCandidates",
               "runApply"):
        assert "previewPanel(" in _js_function_body(_JS, fn), fn
    merge = _js_function_body(_JS, "renderManage")
    assert merge.index("previewPanel(") < merge.index('"/merge"')


def test_wording_conflict_empty_snapshot_changed() -> None:
    assert "다른 사람이 먼저 고쳤습니다 · 다시 불러오기" in _JS
    assert 'detail.code === "version_conflict"' in _JS
    assert "자동 계산 또는 가져오기로 서비스를 만드세요" in _JS
    assert "스냅샷이 바뀌었습니다" in _JS and 'err.code === "snapshot_changed"' in _JS
    assert "scan_disabled" in _JS


def test_url_state_and_login_redirect() -> None:
    sync = _js_function_body(_JS, "syncUrl")
    assert "p.service" in sync and "p.tab" in sync and "history.replaceState" in sync
    assert '"/login?next="' in _JS and 'localStorage.getItem("user_token")' in _JS


def test_csv_download_uses_fetch_blob_not_navigation() -> None:
    body = _js_function_body(_JS, "exportCsv")
    assert "resp.blob()" in body and "a.download" in body and "Authorization" not in body


def test_owner_branch_only_when_present() -> None:
    body = _js_function_body(_JS, "renderMembers")
    assert "if (m.owner_branch)" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_js_syntax_with_node() -> None:
    res = subprocess.run(["node", "--check", str(_STATIC / "js" / "biz-services.js")],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr


# ── 진입 링크 ──────────────────────────────────────────────────────────
def test_portal_menu_link_gated_by_capability() -> None:
    index = (_STATIC / "index.html").read_text(encoding="utf-8")
    line = next(x for x in index.splitlines() if 'id="bizServicesLink"' in x)
    assert 'href="/biz-services"' in line and 'target="_blank"' in line
    assert 'rel="noopener"' in line and "display: none" in line
    app = (_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    body = _js_function_body(app, "applyBizServicesLink")
    assert "biz_service_enabled === true" in body
    assert "applyBizServicesLink();" in app


def test_admin_dashboard_link() -> None:
    dash = (_STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    line = next(x for x in dash.splitlines() if 'id="bizServicesEntryLink"' in x)
    assert 'href="/biz-services"' in line and 'rel="noopener"' in line
    assert "display: none" in line  # 플래그 off 기동에서 404 버튼이 나타나지 않는다
    admin = (_STATIC / "js" / "admin.js").read_text(encoding="utf-8")
    gate = admin[admin.index('getElementById("bizServicesEntryLink")'):]
    assert 'fetch("/biz-services"' in gate[:400] and "res.ok" in gate[:400]
    assert '<script src="/static/js/admin.js?v=19">' in dash


# ── (W5b) CSV 가져오기 ─────────────────────────────────────────────────
def test_import_section_admin_only_preview_panel_before_apply() -> None:
    boot = _js_function_body(_JS, "boot")
    assert "if (st.isAdmin) buildImportSection();" in boot
    assert boot.index("buildImportSection()") < boot.index("wire();")
    assert 'imp.sec.hidden = name !== "import"' in _js_function_body(_JS, "showSection")
    preview = _js_function_body(_JS, "runImportPreview")
    assert '"/import/preview"' in preview and "new FormData()" in preview
    apply = _js_function_body(_JS, "previewImportApply")
    assert apply.index("previewPanel(") < apply.index('"/import/apply"')
    assert 'err.code === "snapshot_changed"' in apply and 'err.code === "version_conflict"' in apply
    # 파일 본문은 JSON으로 감싸지 않는다(Content-Type은 브라우저가 multipart 경계와 함께 정한다)
    req = _js_function_body(_JS, "request")
    assert "body instanceof FormData" in req and "!isForm" in req


def test_import_rows_rendered_as_text_with_status_badges() -> None:
    table = _js_function_body(_JS, "importRowTable")
    assert "badge(r.label || r.status" in table and "IMPORT_ROWS_SHOWN" in table
    for status in ("add", "move", "keep", "not_found", "ambiguous", "unknown_db",
                   "file_conflict"):
        assert f"{status}:" in _JS, status
