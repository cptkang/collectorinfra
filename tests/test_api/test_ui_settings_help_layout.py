"""설정 도움말 패널 — 레이아웃 계약 회귀 (D-191).

패널을 열면 설정 목록이 좁아진다. 좁아진 폭에서 행이 뭉개지는 회귀는 **CSS 한 줄**로
되돌아오고, 브라우저 e2e가 이 환경에서 돌지 않아 눈으로도 잡히지 않는다. 그래서
"뭉개지지 않는다"를 성립시키는 조건만 정적으로 고정한다.

최초 구현이 실제로 깨졌던 원인이 그대로 이 파일의 테스트가 됐다:
- `.container`가 920px여서 3열 행(위젯 200 + 상태 160)과 패널(최소 320)을 빼면
  키·설명 열에 70px도 남지 않았다 → 운영자 화면 폭을 넓힌다.
- `.card { overflow: hidden }`이 sticky를 무력화해 패널이 스크롤을 따라오지 못했다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


@pytest.fixture(scope="module")
def dashboard_html() -> str:
    return (_STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css() -> str:
    return (_STATIC / "css" / "style.css").read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """셀렉터의 선언 블록을 돌려준다(없으면 실패)."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"규칙이 없다: {selector}"
    return match.group(1)


# ─── 폭 예산: 패널을 나란히 놓을 수 있어야 한다 ───


def test_admin_dashboard_opts_into_wide_container(dashboard_html: str, style_css: str):
    """운영자 대시보드는 넓은 폭을 쓴다 — 920px로는 패널을 나란히 둘 수 없다."""
    assert 'class="admin-dashboard"' in dashboard_html, "body에 admin-dashboard 클래스가 없다"
    wide = _rule(style_css, ".admin-dashboard .container")
    match = re.search(r"max-width:\s*(\d+)px", wide)
    assert match, "admin container에 max-width가 없다"
    assert int(match.group(1)) >= 1400, "패널 400 + 3열 행을 담으려면 1400px 이상이어야 한다"


def test_chat_container_width_unchanged(style_css: str):
    """사용자 화면 폭은 건드리지 않는다 — 넓힌 것은 운영자 화면뿐이다."""
    base = re.search(r"^\.container \{[^}]*max-width:\s*(\d+)px", style_css, re.M)
    assert base and base.group(1) == "920"


def test_label_column_survives_open_panel(style_css: str):
    """패널이 열리면 고정 열(위젯·상태)부터 줄여 키·설명 폭을 지킨다.

    상태 열이 160px 고정으로 남아 있으면 좁은 폭에서 라벨이 먼저 뭉개진다.
    """
    row = _rule(style_css, ".settings-layout.help-open .setting-row")
    columns = re.search(r"grid-template-columns:\s*([^;]+);", row)
    assert columns, "열 정의가 없다"
    value = columns.group(1)
    assert "auto" in value, "상태 열은 내용 폭(auto)이어야 남는 폭이 라벨로 간다"
    assert "160px" not in value, "160px 고정 상태 열이 남아 있다"


# ─── sticky: 패널이 스크롤을 따라온다 ───


def test_settings_card_does_not_clip_sticky_panel(style_css: str):
    """`.card { overflow: hidden }`은 sticky를 죽인다 — 설정 카드에서만 푼다."""
    assert "overflow: hidden" in _rule(style_css, ".card"), "전제(카드 클리핑)가 바뀌었다"
    assert "overflow: visible" in _rule(style_css, "#tab-settings .card")


def test_sticky_panel_clears_fixed_header(style_css: str):
    """헤더가 sticky(64px)라 패널은 그 아래에 붙어야 가려지지 않는다."""
    panel = _rule(style_css, ".settings-help")
    assert "position: sticky" in panel
    assert "--header-h" in panel, "헤더 높이를 반영하지 않으면 패널 머리가 가려진다"


# ─── 강등 사다리: 좁아질수록 단계적으로 물러난다 ───


def test_narrow_layout_degrades_in_order(style_css: str):
    """1360 → 1100 → 900 순서로 강등한다.

    같은 특이성이라 **나중 규칙이 이긴다** — 순서가 뒤집히면 좁은 화면에서
    3열이 되살아나 뭉개진다.
    """
    positions = [
        style_css.index("@media (max-width: 1360px)"),
        style_css.rindex("@media (max-width: 1100px)"),
        style_css.rindex("@media (max-width: 900px)"),
    ]
    assert positions == sorted(positions), "미디어쿼리 순서가 뒤집혔다"


def test_panel_moves_below_list_when_too_narrow(style_css: str):
    """나란히 두기 무리한 폭에서는 패널을 목록 아래로 내린다."""
    block = style_css[style_css.rindex("@media (max-width: 1100px)"):]
    block = block[: block.index("\n}\n\n")]
    assert "position: static" in block, "좁은 화면에서 sticky를 풀지 않으면 패널이 겹친다"
    assert "grid-template-columns: minmax(0, 1fr);" in block


def test_row_stacks_before_panel_drops(style_css: str):
    """패널이 아래로 가기 전 단계에서 먼저 행을 세로로 쌓는다."""
    block = style_css[style_css.index("@media (max-width: 1360px)"):]
    block = block[: block.index("\n}\n\n")]
    assert ".settings-layout.help-open .setting-row" in block
    assert "grid-template-columns: minmax(0, 1fr);" in block


# ─── 캐시 ───


def test_stylesheet_cache_busted(dashboard_html: str):
    """CSS를 고쳤으면 캐시 버전을 올려야 반영된다(D-187)."""
    match = re.search(r"style\.css\?v=(\d+)", dashboard_html)
    assert match and int(match.group(1)) >= 13
