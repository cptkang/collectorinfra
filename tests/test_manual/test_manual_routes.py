"""매뉴얼 진입 — 라우트와 머리글 링크 (plans/116 §4.8 · V-1·V-2).

역할별 노출의 실제 렌더는 캡처 단계(``captures.yaml`` 의 ``assert_visible``·``assert_hidden``)가
브라우저로 확인한다. 여기서는 서버 없이 확인되는 계약만 고정한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app

STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.parametrize("manual,title", [("user", "사용자 매뉴얼"), ("admin", "관리자 매뉴얼")])
def test_manual_route_serves_its_own_html(client: TestClient, manual: str, title: str) -> None:
    """V-1 — 두 권이 각자의 URL 에서 열린다(G-2: 다른 HTML 화면처럼 서버 인증 없이)."""
    r = client.get(f"/manual/{manual}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert f"<title>{title}" in r.text
    assert f'data-manual="{manual}"' in r.text


def test_header_links_open_in_new_tab() -> None:
    """V-2 — 메인 머리글 매뉴얼 링크 둘은 새 탭(D-248)이고 관리자 매뉴얼은 기본 숨김이다."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for ident, href in (("userManualLink", "/manual/user"), ("adminManualLink", "/manual/admin")):
        tag = re.search(rf'<a[^>]*id="{ident}"[^>]*>', html)
        assert tag, ident
        assert f'href="{href}"' in tag.group(0)
        assert 'target="_blank"' in tag.group(0) and 'rel="noopener"' in tag.group(0)
    admin_tag = re.search(r'<a[^>]*id="adminManualLink"[^>]*>', html).group(0)
    assert "display: none" in admin_tag


def test_admin_manual_link_shares_admin_entry_branch() -> None:
    """V-2 — 관리자 매뉴얼 링크는 Admin 링크와 **같은 분기**에서만 드러난다.

    판정 로직을 복제하지 않는다(§4.8)."""
    js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    branch = js[js.index('userInfo.role === "admin" || !data.auth_enabled') :]
    branch = branch[: branch.index("}")]
    assert 'adminLink.style.display = "inline-flex"' in branch
    assert "adminManualLink.style.display" in branch
    assert js.count("adminManualLink.style.display") == 1, (
        "관리자 매뉴얼 링크를 다른 곳에서도 드러낸다"
    )
