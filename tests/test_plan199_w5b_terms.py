"""plans/199 W5b — 용어 정리(표시 문구만 · §3.9 · D-351).

`BriefingView`(node 실행)의 단계 표 열 머리·셀 값 문구와 단계 스트립 머리를 검사한다.
기능 조회 플래그(`summaryView`)가 켜졌을 때만 바뀌고, 꺼지면 종전 원문 그대로다
(바이트 동일은 `tests/test_plan199_w1_summary.py`가 기준 SHA 렌더와 대조한다).
문구 표가 생산 코드(sre_agent 단계 표·지표 이름)와 어긋나지 않는지도 소스를 읽어 대조한다.
장애 진단 「조사 흐름」 카드 표(`diagnosis-view.js`)도 같은 표를 쓴다(W15 하네스).
"""

from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path
from typing import Any

from tests.test_api.test_ui_plan187_w20_flow import _RUN_EVENTS, _RUN_PARTIAL
from tests.test_api.test_ui_plan199_w5_diagnosis import _run, _running_route
from tests.test_plan199_w1_summary import _js, _node, _view

_ROOT = Path(__file__).resolve().parents[1]
_FIX = _ROOT / "tests" / "fixtures" / "plan199"
_SRE = _ROOT / "sre_agent" / "sre_agent"
_STEPS = _SRE / "application" / "playbook_steps.py"
_PREFETCH = _SRE / "application" / "evidence_prefetch.py"
_CORRELATION = _SRE / "domain" / "correlation.py"

#: W0 실측 — 종전 COLUMN_LABELS에 없던 단계 표 열(§1.8)
_W0_COLUMNS = (
    "host",
    "baseline_p95_ms",
    "error_rate",
    "verdict_source",
    "prev_day",
    "prev_week",
    "change_pct_prev_day",
    "change_pct_prev_week",
    "rss",
    "args",
    "elapsed_ms",
    "running_mode",
    "running_text",
    "sql",
    "error_type",
    "count",
    "status",
    "alarm_time",
    "severity",
    "alarm_name",
)
_OLD_STRIP = "단계 판정 — 공통(엔진 선택과 무관)"
_NEW_STRIP = "단계별 판정"


def _render(view: dict[str, Any], on: bool) -> str:
    extra = {"mode": "detail", "summaryView": on}
    return _node(f"out = BV.renderBody({_js(view)}, {_js(extra)});")


def _ths(html: str) -> list[str]:
    return re.findall(r"<th>([^<]*)</th>", html)


def _tds(html: str) -> list[str]:
    return re.findall(r"<td>([^<]*)</td>", html)


def _step_tables() -> list[dict[str, Any]]:
    """W0 픽스처의 단계 표 전부(종합 S9 표 제외 — 화면은 단계 섹션에서 뺀다)."""
    out: list[dict[str, Any]] = []
    for p in sorted(_FIX.glob("*.json")):
        snap = json.loads(p.read_text(encoding="utf-8"))
        for key in ("view", "user_view", "poll_view"):
            v = snap.get(key) or {}
            b = v.get("briefing") or v.get("partial_briefing")
            pb = (b or {}).get("playbook") or {}
            for sec in pb.get("sections") or []:
                t = sec.get("table")
                if sec.get("action") != "synthesize" and isinstance(t, dict) and t.get("columns"):
                    out.append(t)
    return out


def _column_values(col: str) -> set[str]:
    vals: set[str] = set()
    for t in _step_tables():
        cols = [str(c) for c in t["columns"]]
        if col not in cols:
            continue
        i = cols.index(col)
        for row in t.get("rows") or []:
            v = row.get(col) if isinstance(row, dict) else row[i]
            if isinstance(v, str):
                vals.add(v)
    return vals


def _terms() -> dict[str, Any]:
    return _node(
        "out = { text: BV.TERM_TEXT, cols: BV.TERM_COLUMNS, metrics: BV.METRIC_LABELS,"
        " kinds: BV.KIND_LABELS, badges: BV.PB_TEXT.sourceBadges };"
    )


# ─── 1. 켜짐 — 열 머리 · 셀 값 · 스트립 머리 ──────────────────────────────


def test_on_column_headers_use_term_labels() -> None:
    terms = _terms()
    ths = _ths(_render(_view("confirmed_same_layer"), True))
    for raw in (
        "host",
        "baseline_p95_ms",
        "error_rate",
        "verdict_source",
        "prev_day",
        "prev_week",
        "change_pct_prev_day",
        "change_pct_prev_week",
        "rss",
        "args",
    ):
        assert raw not in ths, raw
        assert terms["cols"][raw] in ths, raw
    assert "전일 대비(%)" in ths and "기준선 p95(ms)" in ths


def test_on_cells_use_metric_kind_and_source_labels() -> None:
    terms = _terms()
    tds = _tds(_render(_view("confirmed_same_layer"), True))
    metrics = ("p95_ms", "heap_used_mb", "process_cpu_pct", "db_pool_usage_ratio")
    for raw in metrics:
        assert raw not in tds and terms["metrics"][raw] in tds, raw
    for raw in ("cpu", "memory", "filesystem", "apm_event"):
        assert raw not in tds and terms["kinds"][raw] in tds, raw
    assert "sustained" not in tds and "지속" in tds
    assert "playbook_rule" not in tds and terms["badges"]["playbook_rule"] in tds


def test_on_unknown_columns_and_values_stay_raw() -> None:
    view = _view("confirmed_same_layer")
    secs = view["briefing"]["playbook"]["sections"]
    secs.insert(
        0,
        {
            "step": "S0z",
            "action": "x.y",
            "title": "임의",
            "status": "ok",
            "table": {
                "columns": ["instance", "metric", "kind", "zz_col"],
                "rows": [["A1", "zz_metric", "zz_kind", "v<1>"]],
            },
        },
    )
    html = _render(view, True)
    assert "zz_col" in _ths(html)
    tds = _tds(html)
    assert "zz_metric" in tds and "zz_kind" in tds and "v&lt;1&gt;" in tds


def test_on_strip_head_and_engine_layers() -> None:
    view = _view("confirmed_same_layer")
    on = _render(view, True)
    assert _NEW_STRIP in on and _OLD_STRIP not in on
    labels = _node("out = BV.LAYER_LABELS;")
    ea = view["briefing"]["engine_agreement"]
    assert "계층 " + labels[ea["playbook"]["layer"]] in on
    assert "계층 " + ", ".join(labels[x] for x in ea["holmesgpt"]["layers"]) in on


def test_head_verdict_level_is_importance_when_on() -> None:
    """`verdict_level`은 중요도(해소·주의·경고·심각)다 — §3.9 「신뢰도」 전제 대신 팀 리드 B안.

    켜짐이면 브리핑 줄 라벨(noise_gate `investigation_briefing`)과 같은 「중요도」,
    꺼짐이면 종전 「평가」.
    플레이북 머리(`renderHead`)·비플레이북 머리(`renderBody`) 양쪽이 같다.
    """
    for name in ("confirmed_same_layer", "non_playbook"):
        view = _view(name)
        assert view["verdict_level"] == "경고", name
        on, off = _render(view, True), _render(view, False)
        assert '<span class="bv-meta">중요도 경고</span>' in on and "평가 경고" not in on, name
        assert '<span class="bv-meta">평가 경고</span>' in off and "중요도 경고" not in off, name


# ─── 2. 꺼짐 — 종전 원문 그대로 ──────────────────────────────────────────


def test_off_keeps_old_strip_and_raw_names() -> None:
    view = _view("confirmed_same_layer")
    off = _render(view, False)
    assert off.count(_OLD_STRIP) == 1 and _NEW_STRIP not in off
    ths, tds = _ths(off), _tds(off)
    assert {"host", "prev_day", "change_pct_prev_day", "rss"} <= set(ths)
    assert {"p95_ms", "heap_used_mb", "cpu", "sustained", "playbook_rule"} <= set(tds)
    ea = view["briefing"]["engine_agreement"]
    assert "계층 " + ea["playbook"]["layer"] + "<" in off


# ─── 3. 문구 표 ↔ 생산 코드 ──────────────────────────────────────────────


def test_every_fixture_step_column_has_label() -> None:
    cols = sorted({str(c) for t in _step_tables() for c in t["columns"]})
    labels = _node(f"out = {_js(cols)}.map(function (c) {{ return BV.termColumn(c); }});")
    # txid는 종전 표가 원문 그대로 둔 열(식별자) — 이번 범위 밖
    assert [c for c, lb in zip(cols, labels, strict=True) if lb == c] == ["txid"]


def test_w0_columns_are_labelled_and_produced_by_steps() -> None:
    terms = _terms()
    src = _STEPS.read_text(encoding="utf-8")
    assert set(_W0_COLUMNS) == set(terms["cols"])
    missing = [c for c in _W0_COLUMNS if f'"{c}"' not in src]
    # 기준선 창 이름(`playbook.py` Literal)이 열 이름이 되고
    # 변화율 열은 그 이름에 접두를 붙인다(S1 표)
    assert set(missing) == {"prev_day", "prev_week", "change_pct_prev_day", "change_pct_prev_week"}
    assert 'f"change_pct_{n}"' in src
    playbook_src = (_SRE / "domain" / "playbook.py").read_text(encoding="utf-8")
    assert 'Literal["prev_day", "prev_week"]' in playbook_src


def test_metric_and_kind_labels_match_producers() -> None:
    terms = _terms()
    # 「지표」 열 값 전수(W0 픽스처 = 생산 코드 산출)와 표가 같다
    assert _column_values("metric") == set(terms["metrics"])
    # S4 지표 종류 = evidence_prefetch.METRIC_KINDS · S5 타임라인 종류 = correlation._KIND_ORDER
    s4 = {
        str(e.elts[0].value)
        for node in ast.walk(ast.parse(_PREFETCH.read_text(encoding="utf-8")))
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "METRIC_KINDS"
        and isinstance(node.value, ast.Tuple)
        for e in node.value.elts
        if isinstance(e, ast.Tuple) and isinstance(e.elts[0], ast.Constant)
    }
    tl = {
        str(k.value)
        for node in ast.walk(ast.parse(_CORRELATION.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_KIND_ORDER"
        and isinstance(node.value, ast.Dict)
        for k in node.value.keys
        if isinstance(k, ast.Constant)
    }
    assert s4 == {"cpu", "memory", "filesystem", "disk_io"} and tl
    assert set(terms["kinds"]) == s4 | tl
    assert _column_values("kind") <= set(terms["kinds"])


def test_source_badges_cover_step_verdict_sources() -> None:
    terms = _terms()
    consts = {
        node.targets[0].id: str(node.value.value)
        for node in ast.parse(_STEPS.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.startswith("VERDICT_SOURCE_")
        and isinstance(node.value, ast.Constant)
    }
    assert set(consts.values()) == set(terms["badges"])
    assert _column_values("verdict_source") <= set(terms["badges"])


# ─── 4. 장애 진단 「조사 흐름」 카드 표 ────────────────────────────────────


def _flow_table(on: bool) -> dict[str, list[str]]:
    part = copy.deepcopy(_RUN_PARTIAL)
    part["playbook"]["sections"][0]["table"] = {
        "columns": ["metric", "prev_day", "kind", "zz_col"],
        "rows": [["p95_ms", 800.0, "cpu", "zz_metric"], ["zz_metric", 1, "zz_kind", None]],
    }
    ev = json.dumps(_RUN_EVENTS, ensure_ascii=False)
    pj = json.dumps(part, ensure_ascii=False)
    return _run(
        _running_route()
        + f"""
flag = {str(on).lower()};
var v = makeOn();
var tab = await openRun(v, {{ source: "live", events: {ev} }}, {pj});
function tags(n, tag) {{
    var o = [];
    (function walk(x) {{
        if (x.tagName === tag) o.push(x.textContent);
        x.children.forEach(walk);
    }})(n);
    return o;
}}
var t = findAll(tab.dom.flowBody, "dv-card-table")[0];
out = {{ th: tags(t, "th"), td: tags(t, "td") }};
"""
    )


def test_diagnosis_flow_table_terms_on() -> None:
    terms = _terms()
    out = _flow_table(True)
    assert out["th"] == ["지표", terms["cols"]["prev_day"], "종류", "zz_col"]
    assert out["td"] == [
        terms["metrics"]["p95_ms"],
        "800",
        terms["kinds"]["cpu"],
        "zz_metric",
        "zz_metric",
        "1",
        "zz_kind",
        "—",
    ]


def test_diagnosis_flow_table_off_is_raw() -> None:
    out = _flow_table(False)
    assert out["th"] == ["metric", "prev_day", "kind", "zz_col"]
    assert out["td"] == ["p95_ms", "800", "cpu", "zz_metric", "zz_metric", "1", "zz_kind", "—"]
