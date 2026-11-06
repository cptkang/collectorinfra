"""plans/215 독립 검증 — 플래그 off 화면 비트 동일(수용 13 「P1 메뉴 숨김」) (verifier 작성).

포털 매뉴얼 메뉴의 「업무 서비스 정의」(`bizServicesLink`)는 기본 숨김 + 기능 조회로만 보인다.
관리자 대시보드의 같은 진입 링크(`bizServicesEntryLink`)도 기본 숨김이고 스크립트가 보이게
해야 한다 — `NOISE_BIZ_SERVICE_ENABLED` off 기동에서 404로 가는 버튼이 나타나면 안 된다
(W5 교정에서 고쳐 xfail 마크를 지웠다).
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "static"


def _anchor(html: str, anchor_id: str) -> str:
    m = re.search(rf'<a\b[^>]*\bid="{anchor_id}"[^>]*>', html)
    assert m is not None, f"{anchor_id} 링크가 없다"
    return m.group(0)


def test_portal_menu_link_hidden_by_default() -> None:
    """기준선 — 포털 메뉴 링크는 기본 숨김이다(app.js가 기능 조회로만 보인다)."""
    tag = _anchor((STATIC / "index.html").read_text(encoding="utf-8"), "bizServicesLink")
    assert "display: none" in tag


def test_admin_dashboard_link_hidden_when_flag_off() -> None:
    html = (STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    tag = _anchor(html, "bizServicesEntryLink")
    hidden_by_default = "display: none" in tag or "display:none" in tag or " hidden" in tag
    gated = any(
        "bizServicesEntryLink" in p.read_text(encoding="utf-8")
        for p in (STATIC / "js").rglob("*.js")
    )
    assert hidden_by_default and gated
