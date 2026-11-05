"""D-351 — 결정 이력 「솔루션」·「존」 칸과 솔루션 필터(API · 화면 정적 배선).

- `GET /decisions` 행에 `solution`·`solution_label`·`location_label`이 실리고 `solution`으로 거른다.
  `facets.solutions`·`solution_labels`가 선택지를 만든다. db_id 없는 판단은 `unclassified`(미분류).
- `GET /decisions/{alarm_id}`(결정 추적 서랍)도 같은 출처 키를 싣는다.
- 두 화면(사용자·운영자)의 결정 이력 표가 8칸이고 「전 솔루션」 선택 상자가 있다.
- (부기 1) `zone`으로 거르고 `facets.zones`·`zone_labels`가 「전 존」 선택지를 만든다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_api.test_noise_dashboard_api import (
    MODES,
    _decision_record,
    _make_config,
    _mode_client,
    _seed_decisions,
)

STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


def _records() -> list[dict]:
    now = datetime.now(UTC)

    def at(minutes: int) -> str:
        return (now - timedelta(minutes=60 - minutes)).isoformat()

    return [
        _decision_record(alarm_id="gp1", tier="page", ts=at(1), db_id="polestar_cm_gp"),
        _decision_record(alarm_id="b01", tier="ticket", ts=at(2), db_id="polestar_b0"),
        _decision_record(alarm_id="jp1", tier="suppress", ts=at(3), db_id="jennifer_cm_prd"),
        _decision_record(alarm_id="old", tier="suppress", ts=at(4)),
    ]


@pytest.mark.parametrize("mode", MODES)
def test_rows_carry_origin_and_options(tmp_path, mode):
    _seed_decisions(tmp_path, _records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions").json()
    rows = {i["alarm_id"]: i for i in body["items"]}
    assert (rows["gp1"]["solution_label"], rows["gp1"]["location_label"]) == ("폴스타", "공동존 김포")
    assert (rows["b01"]["solution"], rows["b01"]["location_label"]) == ("polestar", "은행존")
    assert (rows["jp1"]["solution"], rows["jp1"]["location_label"]) == ("apm", "공동존 운영")
    assert (rows["old"]["solution"], rows["old"]["solution_label"]) == ("unclassified", "미분류")
    assert body["facets"]["solutions"] == {"polestar": 2, "apm": 1, "unclassified": 1}
    assert body["solution_labels"] == {
        "polestar": "폴스타", "apm": "제니퍼", "unclassified": "미분류",
    }


@pytest.mark.parametrize("mode", MODES)
def test_solution_filter(tmp_path, mode):
    _seed_decisions(tmp_path, _records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions?solution=polestar").json()
    assert sorted(i["alarm_id"] for i in body["items"]) == ["b01", "gp1"]
    assert body["total"] == 2 == sum(body["facets"]["tiers"].values())
    # 선택지는 솔루션 필터와 무관하게 유지된다
    assert body["facets"]["solutions"] == {"polestar": 2, "apm": 1, "unclassified": 1}
    old = client.get(f"{base}/decisions?solution=unclassified").json()
    assert [i["alarm_id"] for i in old["items"]] == ["old"]
    assert client.get(f"{base}/decisions?solution=").json()["total"] == 4  # 빈 값 = 필터 없음


@pytest.mark.parametrize("mode", MODES)
def test_trace_carries_origin(tmp_path, mode):
    _seed_decisions(tmp_path, _records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    decision = client.get(f"{base}/decisions/jp1").json()["decision"]
    assert (decision["solution_label"], decision["location_label"]) == ("제니퍼", "공동존 운영")


@pytest.mark.parametrize("page", ["noise.html", "admin/noise.html"])
def test_decision_table_has_origin_columns_and_solution_filter(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert '<select id="decSolution"' in html and "전 솔루션" in html
    assert (
        "<th>시각</th><th>알람</th><th>서버</th><th>솔루션</th><th>존</th>"
        "<th>티어</th><th>결정 단계</th><th>사유</th>"
    ) in html


def test_noise_js_wires_solution_filter_and_zone_labels():
    js = (STATIC / "js" / "noise.js").read_text(encoding="utf-8")
    assert '"solution=" + encodeURIComponent(document.getElementById("decSolution").value)' in js
    assert 'fillOriginFilter("decSolution", facets.solutions, data.solution_labels' in js
    assert "cell.colSpan = 8;" in js
    # 서랍·세부 리스트의 존 칸은 같은 표기(zoneText)를 쓴다
    assert 'render: function (it) { return zoneText(it); }' in js
    assert 'addFactRow(drawerFactsBody, "솔루션", record.solution_label);' in js


# ── D-351 부기 1 — 존 필터 ────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
def test_zone_facets_and_labels(tmp_path, mode):
    _seed_decisions(tmp_path, _records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions").json()
    assert body["facets"]["zones"] == {"gongjon": 2, "bankjon": 1, "unclassified": 1}
    assert body["zone_labels"] == {
        "gongjon": "공동존", "bankjon": "은행존", "unclassified": "미분류",
    }


@pytest.mark.parametrize("mode", MODES)
def test_zone_filter(tmp_path, mode):
    _seed_decisions(tmp_path, _records())
    client, base = _mode_client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions?zone=gongjon").json()
    assert sorted(i["alarm_id"] for i in body["items"]) == ["gp1", "jp1"]
    assert body["total"] == 2 == sum(body["facets"]["tiers"].values())
    # 존 선택지는 존 필터와 무관하게 유지되고, 솔루션 선택지는 존 필터를 반영한다
    assert body["facets"]["zones"] == {"gongjon": 2, "bankjon": 1, "unclassified": 1}
    assert body["facets"]["solutions"] == {"polestar": 1, "apm": 1}
    both = client.get(f"{base}/decisions?zone=gongjon&solution=polestar").json()
    assert [i["alarm_id"] for i in both["items"]] == ["gp1"]
    assert both["facets"]["zones"] == {"gongjon": 1, "bankjon": 1}
    old = client.get(f"{base}/decisions?zone=unclassified").json()
    assert [i["alarm_id"] for i in old["items"]] == ["old"]
    assert client.get(f"{base}/decisions?zone=nope").json()["total"] == 0
    assert client.get(f"{base}/decisions?zone=").json()["total"] == 4  # 빈 값 = 필터 없음


@pytest.mark.parametrize("page", ["noise.html", "admin/noise.html"])
def test_decision_table_has_zone_filter(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert '<select id="decZone"' in html and "전 존" in html


def test_noise_js_wires_zone_filter():
    js = (STATIC / "js" / "noise.js").read_text(encoding="utf-8")
    assert '"zone=" + encodeURIComponent(document.getElementById("decZone").value)' in js
    assert 'fillOriginFilter("decZone", facets.zones, data.zone_labels' in js
