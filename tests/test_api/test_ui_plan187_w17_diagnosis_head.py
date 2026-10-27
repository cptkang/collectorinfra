"""plans/187 W17 — 장애 진단 하위 탭 머리 「어떤 알람을 · 왜」와 실행 기록 목적 줄.

W15 가짜 DOM·EventSource 하네스(`_node`)로 실제 렌더를 돈다. 서버 텍스트는 textContent로만 들어간다.
"""

from __future__ import annotations

import json

from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_ALARM = {
    "alarm_id": "a1", "alarm_name": "CPU 사용률 임계 초과", "server_name": "WEB-01",
    "resource_name": "cpu", "tier": "page", "severity": 3,
}
_EVENTS = [
    {"seq": 1, "engine": "playbook", "kind": "started", "label": "플레이북 cpu_high 선택",
     "purpose": "알람의 자원 유형이 이 플레이북 조건과 일치"},
    {"seq": 2, "engine": "playbook", "kind": "step_start", "step": "S2", "label": "S2 사용량 추이",
     "purpose": "최근 6시간 CPU 추이가 급증인지 본다"},
    {"seq": 3, "engine": "playbook", "kind": "tool_start", "step": "S2",
     "tool": "polestar_metric_trend", "label": "polestar_metric_trend {WEB-01}"},
]


def _script(body: dict) -> str:
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-1"] = {{ status: 200, body: {json.dumps(body, ensure_ascii=False)} }};
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-1");
await flush();
runTimers(1000);
var es = liveES()[0];
es.onopen();
es.emit("activity", {{ investigation_id: "inv-1", status: "running", next_seq: 3,
    events: {json.dumps(_EVENTS, ensure_ascii=False)} }});
var tab = v.ctl.tab("inv-1");
var tools = findAll(tab.dom.panel, "dv-text").filter(function (t) {{
    return t.textContent.indexOf("polestar_metric_trend") >= 0; }});
out = {{ title: tab.dom.title.textContent, target: tab.dom.target.textContent,
    hidden: tab.dom.target.hidden, purposes: texts(tab.dom.panel, "dv-purpose"),
    toolTitle: tools.length ? tools[0].title : null,
    html: htmlWrites.indexOf(tab.dom.target) >= 0 }};
"""


def _running(**extra: object) -> dict:
    return {"status": "running", "investigation_id": "inv-1", "kind": "alarm",
            "activity": {"next_seq": 0, "truncated": False, "events": []},
            "phase": "collecting", **extra}


def test_alarm_head_shows_alarm_and_why() -> None:
    out = _node(_script(_running(alarm_ids=["a1"], alarm=_ALARM)))
    assert out["title"] == "WEB-01 · CPU 사용률 임계 초과"
    assert "[위험] CPU 사용률 임계 초과 — WEB-01 · cpu" in out["target"]
    assert "판정 즉시 통보" in out["target"]
    assert "플레이북 cpu_high 선택 — 알람의 자원 유형이 이 플레이북 조건과 일치" in out["target"]
    assert out["hidden"] is False and out["html"] is False
    assert "목적: 최근 6시간 CPU 추이가 급증인지 본다" in out["purposes"]
    # 도구 행은 목적을 되풀이하지 않고 마우스 올림 설명으로 단다
    assert out["toolTitle"] == "목적: 최근 6시간 CPU 추이가 급증인지 본다"


def test_alarm_without_decision_says_unknown() -> None:
    out = _node(_script(_running(alarm_ids=["a1"])))
    assert "알람 정보를 찾지 못했습니다" in out["target"]


def test_chat_subject_shows_target_and_route() -> None:
    body = _running(kind="diagnosis", origin="chat", subject={
        "hostname": "wasdemo01", "route_label": "증상 질의", "route_reason": "「느려요」 증상어",
    })
    out = _node(_script(body))
    assert "wasdemo01" in out["target"]
    assert "증상 질의 — 「느려요」 증상어" in out["target"]
    assert "알람 정보를 찾지 못했습니다" not in out["target"]
