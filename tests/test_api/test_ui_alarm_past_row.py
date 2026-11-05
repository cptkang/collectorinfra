"""지난 알람 검색 행 — 4티어 배지 · 알람 출처 · 한 줄 시각 (U-65).

실물 `renderPastAlarmRow`를 가짜 DOM 위에서 node로 돌려 행의 칸 순서·클래스·문구를 본다.
"""

from __future__ import annotations

import re

from tests.test_api.test_ui_plan192_w1_entry import APP_JS, app_run

_STYLE = APP_JS.parents[1] / "css" / "style.css"

_BODY = r"""
var TZ_SAFE = new Date(2026, 10, 4, 9, 5, 7).toISOString();
function cells(item) {
    var li = renderPastAlarmRow(item);
    var row = li.children[0];
    return row.children.map(function (c) {
        return { cls: c.className, text: c.textContent, title: c.title || "" };
    });
}
out = {
    full: cells({ ts: TZ_SAFE, tier: "ticket", solution_label: "폴스타(인프라 모니터링)",
        location_label: "공동존 김포", db_id: "polestar_cm_gp", server_name: "srv-01",
        alarm_name: "CPU 사용률", stage_label: "우선순위 매트릭스" }),
    unresolved: cells({ ts: TZ_SAFE, tier: "page", solution_label: "", location_label: "",
        db_id: "polestar_x", server_name: "", alarm_name: "" }),
    unknownTier: cells({ ts: "", tier: "weird" }),
};
"""


def _run() -> dict:
    return app_run(("renderPastAlarmRow",), _BODY)


def test_row_columns_order_and_values() -> None:
    row = _run()["full"]
    assert [c["cls"].split()[0] for c in row] == [
        "alarm-past-time",
        "alarm-past-tier",
        "alarm-past-source",
        "alarm-past-server",
        "alarm-past-name",
        "alarm-past-stage",
    ]
    time, tier, source, server, name, stage = row
    # 한 줄 시각(MM-DD HH:mm:ss) — 전체 일시는 title
    assert time["text"] == "11-04 09:05:07"
    assert "2026" in time["title"]
    # 티어 배지 — 노이즈 관제와 같은 이름·색 클래스
    assert tier["cls"] == "alarm-past-tier t-ticket" and tier["text"] == "TICKET"
    # 출처 — 솔루션 짧은 이름 · 존 위치, title은 전체 라벨
    assert source["text"] == "폴스타 · 공동존 김포"
    assert source["title"] == "알람 출처: 폴스타(인프라 모니터링) · 공동존 김포"
    assert (server["text"], name["text"], stage["text"]) == ("srv-01", "CPU 사용률", "우선순위 매트릭스")


def test_unresolved_origin_falls_back_to_db_id() -> None:
    _, tier, source, server, name, stage = _run()["unresolved"]
    assert tier["cls"] == "alarm-past-tier t-page" and tier["text"] == "PAGE"
    assert source["text"] == "polestar_x"
    assert (server["text"], name["text"], stage["text"]) == ("-", "-", "")


def test_unknown_tier_has_no_color_class() -> None:
    time, tier, source, *_ = _run()["unknownTier"]
    assert tier["cls"] == "alarm-past-tier" and tier["text"] == "WEIRD"
    assert time["text"] == "" and source["text"] == "-"


def test_tier_colors_and_single_line_time_in_css() -> None:
    css = _STYLE.read_text(encoding="utf-8")
    for tier, var in (("page", "page"), ("ticket", "ticket"), ("dashboard", "dash"),
                      ("suppress", "suppress")):
        assert re.search(rf"\.alarm-past-tier\.t-{tier} \{{ background: var\(--tier-{var}\); \}}", css)
    time_rule = re.search(r"\.alarm-past-time, \.alarm-past-stage \{([^}]*)\}", css)
    assert time_rule and "white-space: nowrap" in time_rule.group(1)
