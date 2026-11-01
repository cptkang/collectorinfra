"""plans/201 W5 §7.1 · §7.2 · §7.3 — 공용 렌더(briefing-view.js) 조치 지원 실행 검증.

- §7.1 질의 묶음(`requeryGroups`): 입력은 **실제 `build_requery` 출력**(호스트형·WAS형 플레이북)과 서버
  메타(`duty_action_assist_meta`)다 — 개수·집합 불변 · 안정 · 담당 미지정이면 null(종전 순서).
- §7.2 점검 순서(`fillChecklist`): 실제 `renderBody` 마크업을 가짜 DOM에 올린 뒤 「권고」의 사람 확인 줄 바로
  아래에 접힌 채 붙는지 · 문안이 없으면 아무것도 붙지 않는지.
- §7.3 인계 요약(`alarmHandoffText`): 사용자 뷰는 **본체 마스킹 경로**(`investigation_view` +
  `_investigation_mask_fn`)를 통과한 값만 · 운영자 원문 뷰는 근거 문구를 싣지 않는다.

node는 stdin으로 실행한다(`node -e` 금지).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.api.duty_area_support import duty_action_assist_meta
from src.domain.investigation_requery import build_requery
from tests.test_plan195_w1_investigation_requery import _host_playbook
from tests.test_plan201_w5_card_flow import _FAKE_DOM

_ROOT = Path(__file__).resolve().parents[1]
_BV_JS = _ROOT / "src" / "static" / "js" / "briefing-view.js"
_FIX = _ROOT / "tests" / "fixtures" / "plan199"
MASK = "***MASKED***"


def _js(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _node(body: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    script = "\n".join([
        _FAKE_DOM,
        "global.window = global; global.document = fakeDoc;",
        f"var BV = require({_js(str(_BV_JS))});",
        "var out = null;",
        body,
        "process.stdout.write(JSON.stringify(out));",
    ])
    done = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60,
                          check=False, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _meta(areas: list[str] | None) -> dict[str, Any]:
    """app.js `loadDutyActionAssist`가 넘기는 모양 — 서버 메타 + 사용자 담당(`me.duty_areas`)."""
    m = duty_action_assist_meta(areas)
    return {"requery_axes": m["requery_axes"], "checklists": m["checklists"], "mine": areas or []}


def _view(name: str) -> dict[str, Any]:
    return copy.deepcopy(json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))["view"])


# ── §7.1 질의 묶음 ───────────────────────────────────────────────────────────────


def _host_items() -> list[dict[str, Any]]:
    """실 출력(현 카탈로그는 제니퍼 축뿐 — 실측 고정)에 폴스타 축 항목을 섞은 입력.

    `REQUERY_CATALOG`에는 아직 폴스타 축 템플릿이 없어(`Axis` 타입에는 있음) 실 출력만으로는 묶음이 1개다.
    두 축 묶음 규칙을 보려고 실 항목 2개의 `axis`만 `polestar`로 바꾼다(키 모양은 실 출력 그대로).
    """
    items = build_requery(_host_playbook(), mask_marker=MASK)
    assert {it["axis"] for it in items} == {"apm"}                  # 실측: 카탈로그는 제니퍼 축뿐
    mixed = copy.deepcopy(items)
    for i in (1, 3):
        mixed[i]["axis"] = "polestar"
        mixed[i]["polestar_source"] = "polestar"
    return mixed


def test_requery_groups_real_output_single_area_same_order() -> None:
    items = build_requery(_host_playbook(), mask_marker=MASK)
    out = _node(f"var I = {_js(items)}; out = BV.requeryGroups(I, {_js(_meta(['db']))});")
    assert [g["area"] for g in out] == ["was_mw"] and out[0]["items"] == items
    assert out[0]["mine"] is False


@pytest.mark.parametrize("areas,first", [
    (["was_mw"], "was_mw"), (["server_os"], "server_os"), (["db"], "was_mw"),   # 담당 없음 = 처음 나온 순
    (["db", "was_mw"], "was_mw"),
])
def test_requery_groups_keep_count_set_and_stable(areas: list[str], first: str) -> None:
    items = _host_items()
    out = _node(f"var I = {_js(items)}; out = BV.requeryGroups(I, {_js(_meta(areas))});")
    flat = [it for g in out for it in g["items"]]
    assert len(flat) == len(items)
    assert sorted(_js(x) for x in flat) == sorted(_js(x) for x in items)   # 집합 불변(새 항목 0)
    assert out[0]["area"] == first
    for g in out:                                                       # 묶음 안은 원래 순서
        idx = [items.index(it) for it in g["items"]]
        assert idx == sorted(idx)
    labels = {g["area"]: g["label"] for g in out}
    assert labels == {"was_mw": "WAS/미들웨어", "server_os": "서버/OS"}
    assert [g["mine"] for g in out] == [g["area"] in areas for g in out]


def test_requery_groups_null_without_assignment_or_meta() -> None:
    items = _host_items()
    out = _node(f"""
var I = {_js(items)};
BV.setActionAssist(null);
out = [BV.requeryGroups(I, {_js(_meta(None))}), BV.requeryGroups(I, {_js(_meta([]))}), BV.requeryGroups(I)];
""")
    assert out == [None, None, None]


def test_requery_groups_unmapped_axis_goes_last_as_other() -> None:
    items = build_requery(_host_playbook(), mask_marker=MASK)
    odd = [{**items[0], "axis": "kafka"}, *items[1:]]                  # 표에 없는 축
    out = _node(f"var I = {_js(odd)}; out = BV.requeryGroups(I, {_js(_meta(['db']))});")
    assert out[-1]["area"] == "" and out[-1]["label"] == "그 밖" and out[-1]["items"] == [odd[0]]
    assert sum(len(g["items"]) for g in out) == len(odd)


def test_requery_groups_was_only_playbook_single_group() -> None:
    items = build_requery(_view("episode_scope")["briefing"]["playbook"], mask_marker=MASK)
    out = _node(f"var I = {_js(items)}; out = BV.requeryGroups(I, {_js(_meta(['server_os']))});")
    assert [g["area"] for g in out] == ["was_mw"] and out[0]["items"] == items


# ── §7.2 점검 순서 ───────────────────────────────────────────────────────────────


def _fill(view: dict[str, Any], areas: list[str] | None, *, opts: str = "{}",
          render: str = '{ mode: "detail" }') -> Any:
    return _node(f"""
var V = {_js(view)};
var c = fakeDoc.createElement("div");
c.innerHTML = BV.renderBody(V, {render});
var before = c.all().length;
var box = BV.fillChecklist(c, V, {_js(_meta(areas))}, {opts});
var gated = c.querySelector(".bv-gated");
out = {{
    added: c.all().length - before,
    box: box ? {{
        tag: box.tagName, open: !!box.open, cls: box.className,
        summary: box.querySelector("summary").textContent,
        steps: box.querySelectorAll("li").map(function (li) {{ return li.textContent; }}),
        areas: box.querySelectorAll(".bv-checklist-area").map(function (d) {{ return d.textContent; }}),
        gated: box.querySelector(".bv-gated").textContent,
        afterGated: gated && gated !== box.querySelector(".bv-gated") ? gated.nextSibling === box : null,
        afterSynthesisGated: (function () {{
            var g = c.querySelector(".bv-pb-synthesis .bv-gated");
            return g ? g.nextSibling === box : null;
        }})(),
        inSummaryCard: !!box.closest(".bv-sum-row"),
        parentCls: box.parentNode.className,
        summaryCardGated: c.querySelectorAll(".bv-sum-row .bv-gated").length,
    }} : null,
}};
""")


def test_checklist_after_recommendation_collapsed_user_areas_only() -> None:
    view = _view("confirmed_same_layer")
    assert view["briefing"]["playbook"]["id"] == "was.latency"
    meta = duty_action_assist_meta(["db", "was_mw"])["checklists"]["was.latency"]
    out = _fill(view, ["db", "was_mw"])
    box = out["box"]
    assert box["tag"] == "DETAILS" and box["open"] is False and "bv-checklist" in box["cls"]
    assert box["afterGated"] is True                               # 「권고」 사람 확인 줄 바로 아래
    assert box["summary"] == "점검 순서 — " + " · ".join(r["area_label"] for r in meta)
    assert box["steps"] == [s for r in meta for s in r["steps"]]
    assert box["areas"] == [r["area_label"] for r in meta]
    assert box["gated"] == "사람이 확인하는 순서입니다 — 시스템이 실행하지 않습니다."
    assert "server_os" not in {r["area"] for r in meta}


def test_checklist_anchor_is_recommendation_not_summary_card() -> None:
    """「권고」 기준 — 상세는 플레이북 종합 권고 조치 줄 바로 아래 · 「한눈에 보기」 할 일 행 안에는 끼우지 않는다."""
    view = _view("confirmed_same_layer")
    detail = _fill(view, ["db"], render='{ mode: "detail", summaryView: true }')["box"]
    assert detail["afterSynthesisGated"] is True and detail["inSummaryCard"] is False
    summary = _fill(view, ["db"], render='{ mode: "summary", summaryView: true }')["box"]
    assert summary["inSummaryCard"] is False
    if summary["afterSynthesisGated"] is None:                       # 요약형에 종합 권고가 없으면 브리핑 끝
        assert summary["parentCls"].split()[0] == "bv"
    else:
        assert summary["afterSynthesisGated"] is True
    plain = _fill(view, ["db"], render='{ mode: "summary" }')["box"]
    assert plain["inSummaryCard"] is False and plain["summaryCardGated"] == 0


def test_checklist_unassigned_common_only() -> None:
    view = _view("confirmed_same_layer")
    common = duty_action_assist_meta(None)["checklists"]["was.latency"]
    assert [r["area"] for r in common] == ["common"]
    out = _fill(view, None)
    assert out["box"]["areas"] == []                               # 한 벌이면 영역 머리 없음
    assert out["box"]["steps"] == common[0]["steps"]
    assert out["box"]["summary"] == "점검 순서 — 공통"


def test_checklist_absent_renders_nothing() -> None:
    assert _fill(_view("non_playbook"), ["db"]) == {"added": 0, "box": None}
    view = _view("confirmed_same_layer")
    view["briefing"]["playbook"]["id"] = "host.network"            # 문안 없는 플레이북
    assert _fill(view, ["db"]) == {"added": 0, "box": None}


def test_checklist_open_state_passed_in() -> None:
    out = _fill(_view("confirmed_same_layer"), ["db"], opts="{ open: true }")
    assert out["box"]["open"] is True


def test_render_body_unchanged_by_assist_meta() -> None:
    """메타를 넣어도 HTML 문자열 렌더(renderBody)는 그대로 — 점검 순서는 decorate(DOM 단계)에서만 붙는다."""
    views = [_view(n) for n in ("confirmed_same_layer", "different_layer", "non_playbook", "episode_scope")]
    out = _node(f"""
var VS = {_js(views)};
var a = VS.map(function (v) {{ return BV.renderBody(v, {{ mode: "detail" }}); }});
BV.setActionAssist({_js(_meta(["db"]))});
var b = VS.map(function (v) {{ return BV.renderBody(v, {{ mode: "detail" }}); }});
out = a.map(function (x, i) {{ return x === b[i]; }});
""")
    assert out == [True, True, True, True]


# ── §7.3 인계 요약 ───────────────────────────────────────────────────────────────

_ALARM = {"server": "서버 A", "alarm_name": "응답 지연", "resource": "inst1", "severity": "Critical",
          "time": "2026-10-26 19:10:00", "type": "apm.Instance"}

_DUTY_VIEW = {
    "areas": [
        {"code": "was_mw", "label": "WAS/미들웨어", "status": "suspect", "status_label": "의심 · 확인 필요",
         "signals": ["풀 고갈 password=abc123 (S3 ← apm_slow_transactions)"], "steps": ["S3", "S2"],
         "reason": "획득·실행 구분 정보 없음"},
        {"code": "db", "label": "DB", "status": "unverified", "status_label": "확인 안 됨",
         "signals": [], "steps": ["S3"]},
    ],
}


def _views() -> tuple[dict[str, Any], dict[str, Any]]:
    """본체 경로로 만든 (사용자 뷰, 운영자 뷰) — 같은 저장 레코드."""
    from noise_gate.infrastructure.investigation_store import investigation_view
    from src.api.routes.alarm import _investigation_mask_fn
    from src.config import AppConfig

    raw = _view("confirmed_same_layer")
    briefing = copy.deepcopy(raw["briefing"])
    briefing["duty_view"] = copy.deepcopy(_DUTY_VIEW)
    rec = {"status": raw["status"], "investigation_id": raw["investigation_id"], "kind": "alarm",
           "briefing_lines": raw.get("briefing_lines") or [], "briefing": briefing, "ts": raw.get("updated_at")}
    user = investigation_view(rec, mask_fn=_investigation_mask_fn(AppConfig()))
    op = investigation_view(rec, operator_view=True)
    return user, op


def _handoff(view: Any, link: str = "http://portal/?investigation=x") -> str:
    return _node(f"out = BV.alarmHandoffText({_js(_ALARM)}, {_js(view)}, {{ link: {_js(link)} }});")


def test_handoff_user_view_masked_values() -> None:
    user, _ = _views()
    text = _handoff(user)
    lines = text.split("\n")
    assert lines[0] == "[알람 인계] 서버 A · 응답 지연(inst1) · Critical"
    assert lines[1] == "시각: 2026-10-26 19:10:00" and lines[2] == "유형: apm.Instance"
    assert lines[3].startswith("영역별 점검: WAS/미들웨어 의심 · 확인 필요 — 풀 고갈")
    assert "(획득·실행 구분 정보 없음)" in lines[3] and "DB 확인 안 됨" in lines[3]
    assert "abc123" not in text                                    # 본체 마스킹 경로를 거친 값
    assert lines[4] == "근거 단계: S3 · S2"
    assert lines[5].startswith("조사: " + user["investigation_id"] + " · ")
    assert "http://portal/?investigation=x" in lines[5]
    assert lines[-1] == "※ 사람이 확인·조치합니다 — 시스템이 실행하지 않습니다."
    for bad in ("SELECT", "owner_sub", "subject"):
        assert bad not in text


def test_handoff_operator_raw_view_omits_free_text() -> None:
    _, op = _views()
    text = _handoff(op)
    assert "abc123" not in text and "풀 고갈" not in text and "획득·실행" not in text
    assert "영역별 점검: WAS/미들웨어 의심 · 확인 필요 / DB 확인 안 됨" in text
    assert "근거 단계: S3 · S2" in text


def test_handoff_without_view_and_without_duty() -> None:
    no_view = _handoff(None)
    assert no_view.split("\n")[3].startswith("조사: 조사 결과를 펼쳐 보지 않음")
    assert not any(ln.startswith("영역별 점검:") for ln in no_view.split("\n"))
    plain = _view("non_playbook")
    text = _handoff(plain)
    assert "영역별 점검: 영역별 점검 결과 없음" in text
    bad_id = {**plain, "investigation_id": "../etc"}
    assert "조사:" not in _handoff(bad_id)


def test_handoff_folds_newlines_and_clips_long_values() -> None:
    alarm = {**_ALARM, "server": "a\nb", "type": "x" * 300}
    text = _node(f"out = BV.alarmHandoffText({_js(alarm)}, null, {{}});")
    assert text.split("\n")[0].startswith("[알람 인계] a b · ")
    t = [ln for ln in text.split("\n") if ln.startswith("유형: ")][0]
    assert len(t) == len("유형: ") + 120 and t.endswith("…")


# ── 정적 계약 ────────────────────────────────────────────────────────────────


def _bv_block() -> str:
    src = _BV_JS.read_text(encoding="utf-8")
    i = src.index("// ─── (plans/201 W5 · D-354) 조치 지원")
    return src[i:src.index("    function rqNode(doc, tag, cls, text) {", i)]


def test_assist_block_dom_only_no_vocab_copy() -> None:
    block = _bv_block()
    for bad in ("innerHTML", "insertAdjacentHTML", "outerHTML", "fetch(", "localStorage"):
        assert bad not in block, bad
    for literal in ('"was_mw"', '"server_os"', '"db"', "host.cpu", "was.latency"):
        assert literal not in block, literal


def test_requery_box_falls_back_to_original_list() -> None:
    src = _BV_JS.read_text(encoding="utf-8")
    assert "var groups = assistMeta ? requeryGroups(items) : null;" in src
    assert ("            } else {\n                box.appendChild(requeryList(doc, items, { stage: opts.requery.stage, "
            "busy: opts.requery.busy, showStep: true }));\n            }") in src
    assert "if (assistMeta) fillChecklist(container, view, null, {" in src
