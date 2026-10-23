"""plans/187 W24 — 조사 대화 머리의 「발생 소스」·「조사 항목」.

`diagnosis-view.js`의 순수 함수 `conversationHeadLines`를 node로 실제 실행한다(W15 하네스).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_APP_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "app.js"


def _lines(view: dict) -> list[dict]:
    return _node(
        f"out = DV.conversationHeadLines({json.dumps(view, ensure_ascii=False)});"
    )


def _by_head(lines: list[dict]) -> dict[str, dict]:
    return {line["head"]: line for line in lines}


def test_source_line_follows_alarm_and_decision() -> None:
    lines = _lines({
        "kind": "episode", "alarm_ids": ["a1", "a2"],
        "alarm": {
            "alarm_id": "a1", "alarm_name": "CPU 사용률", "server_name": "svr-was-01",
            "reason": "매트릭스 → page", "db_id": "polestar_cm_gp", "source": "공동존 김포 폴스타",
            "alarm_count": 2,
            "related": [{"alarm_id": "a2", "alarm_name": "WAS 응답 지연",
                         "server_name": "svr-was-01", "source": "공동존 운영 제니퍼"}],
        },
    })
    heads = [line["head"] for line in lines]
    assert heads[:4] == ["조사", "알람", "판정 사유", "발생 소스"]
    assert lines[3]["text"] == "공동존 김포 폴스타 (polestar_cm_gp)"
    related = _by_head(lines)["함께 조사하는 알람"]["text"]
    assert related == "WAS 응답 지연 — svr-was-01 · 공동존 운영 제니퍼"


def test_source_line_without_label_shows_db_id_once() -> None:
    lines = _lines({"alarm": {"alarm_id": "a1", "db_id": "jennifer"}})
    assert _by_head(lines)["발생 소스"]["text"] == "jennifer"


def test_steps_from_final_briefing_with_status() -> None:
    lines = _lines({
        "kind": "alarm", "alarm_ids": ["a1"], "status": "done",
        "briefing": {"playbook": {"id": "host.cpu", "version": 4, "sections": [
            {"step": "S4", "title": "호스트 자원 시간대별 추이", "status": "ok"},
            {"step": "S1", "title": "WAS 골든 시그널", "status": "skipped"},
            {"step": "S3b", "status": "error"},
        ]}},
        "progress": {"done": 3, "total": 3, "current_step": None},
    })
    steps = _by_head(lines)["조사 항목"]
    assert steps["items"] == [
        "① 호스트 자원 시간대별 추이 — 완료", "② WAS 골든 시그널 — 생략", "③ S3b — 실패",
    ]
    assert [line["head"] for line in lines][-2:] == ["플레이북", "조사 항목"]


def test_steps_from_partial_briefing_add_progress() -> None:
    lines = _lines({
        "kind": "alarm", "alarm_ids": ["a1"], "status": "running",
        "partial_briefing": {"partial": True, "playbook": {
            "id": "host.cpu", "version": 4,
            "sections": [{"step": "S4", "title": "호스트 자원 시간대별 추이", "status": "ok"}],
        }},
        "progress": {"done": 1, "total": 9, "current_step": "S6"},
    })
    by = _by_head(lines)
    assert by["플레이북"]["text"].startswith("host.cpu v4")
    assert by["조사 항목"]["items"] == [
        "① 호스트 자원 시간대별 추이 — 완료", "진행 중 S6 · 1/9단계 끝남",
    ]


def test_no_steps_line_without_playbook() -> None:
    lines = _lines({"kind": "alarm", "alarm_ids": ["a1"], "status": "running"})
    assert "조사 항목" not in _by_head(lines)


def test_pin_renders_step_items_as_list_by_text_content() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    body = src[src.index("function renderPinTarget"):src.index("// ─── plans/187 FE-2 공용 호출")]
    assert 'createElement("ol")' in body and "li.textContent = item" in body
    assert "innerHTML" not in body
