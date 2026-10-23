"""plans/187 W23 — 조사 대화 카드 머리 「무엇을 어떤 방식으로 조사하는가」.

`diagnosis-view.js`의 순수 함수 `conversationHeadLines`를 node로 실제 실행하고(W15 하네스),
`app.js` 고정 카드 배선(첫 조회 `with_alarm` · 재조회마다 다시 그림 · textContent)을 고정한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_api.test_ui_plan187_w15_diagnosis import _js_function_body, _node

_APP_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "app.js"


def _lines(view: dict) -> list[list[str]]:
    out = _node(
        f"out = DV.conversationHeadLines({json.dumps(view, ensure_ascii=False)})"
        ".map(function (l) {{ return [l.head, l.text]; }});"
    )
    return [list(x) for x in out]


def test_episode_shows_alarm_decision_related_and_count() -> None:
    lines = _lines({
        "kind": "episode", "alarm_ids": ["a1", "a2", "a3"],
        "alarm": {
            "alarm_id": "a1", "alarm_name": "CPU 사용률", "server_name": "svr-was-01",
            "severity": 2, "tier": "page", "stage_label": "우선순위 매트릭스",
            "reason": "심각도2×중요도높음 → page", "alarm_count": 3,
            "related": [
                {"alarm_id": "a2", "alarm_name": "WAS 응답 지연", "server_name": "svr-was-01"},
            ],
        },
    })
    heads = [h for h, _ in lines]
    assert heads == ["조사", "알람", "판정 사유", "함께 조사하는 알람"]
    assert lines[0][1] == "사건 종합 조사 — 같은 사건 알람 3건을 함께 조사"
    assert "CPU 사용률" in lines[1][1] and "같은 사건 알람 3건 중 첫 알람" in lines[1][1]
    assert lines[2][1] == "우선순위 매트릭스 — 심각도2×중요도높음 → page"
    # 상한으로 1건만 실렸으면 남은 수를 「외 N건」으로
    assert lines[3][1] == "WAS 응답 지연 — svr-was-01 외 1건"


def test_chat_diagnosis_shows_subject_and_playbook() -> None:
    lines = _lines({
        "kind": "diagnosis", "origin": "chat",
        "subject": {"server_name": "svr-was-01", "route_label": "플레이북 host.cpu",
                    "route_reason": "규칙"},
        "briefing": {"playbook": {
            "id": "host.cpu", "version": 2, "matched_by": "match:resource_type+alarm_kind",
            "scope": {"host": "svr-was-01"},
        }},
    })
    assert lines == [
        ["조사", "채팅 증상 조사"],
        ["대상", "svr-was-01"],
        ["조사 이유", "플레이북 host.cpu — 규칙"],
        ["플레이북", "host.cpu v2 — 알람의 자원 유형·알람 종류가 이 플레이북 조건과 일치"
                    " · 범위 svr-was-01"],
    ]


def test_alarm_without_decision_says_unknown_and_followup_focus() -> None:
    lines = _lines({"kind": "alarm", "alarm_ids": ["zz"]})
    assert lines[0] == ["조사", "알람 자동 조사"]
    assert lines[1][0] == "알람" and "찾지 못했습니다" in lines[1][1]
    follow = _lines({"kind": "diagnosis", "parent_investigation_id": "inv-1",
                     "focus": "원인 가설 재확인"})
    assert follow[0] == ["조사", "추가 조사 — 초점 원인 가설 재확인"]
    manual = _lines({"kind": "manual", "parent_alarm_id": "a1"})
    assert manual[0] == ["조사", "다시 분석(사용자 요청)"]
    # 조사 응답에는 alarm_ids가 없다 — 사건 알람 수는 알람 요약의 alarm_count로
    ep = _lines({"kind": "episode", "alarm": {"alarm_name": "x", "alarm_count": 2}})
    assert ep[0] == ["조사", "사건 종합 조사 — 같은 사건 알람 2건을 함께 조사"]


def test_pin_wiring_first_load_with_alarm_and_rerender() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    pin = _js_function_body(src, "renderInvestigationPin")
    assert '"?with_alarm=true"' in pin and "targetShown ? path" in pin
    assert "renderPinTarget(targetBox, Object.assign({}, view, { alarm: pinAlarm }))" in pin
    head = _js_function_body(src, "renderPinTarget")
    assert "conversationHeadLines(view)" in head and "innerHTML" not in head
    assert "textContent = line.text" in head
