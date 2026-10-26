"""plans/194 W5 독립 검증 — 새 마크업의 속성 이스케이프 · 시간당 잠금 시각(U2).

W15 가짜 DOM 하네스(`_node`)로 실제 briefing-view.js·diagnosis-view.js를 돌린다
(브라우저·서버 기동 없음).
"""

from __future__ import annotations

from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_HOSTILE = '"><img src=x onerror=alert(1)>'


def test_start_msg_escapes_id_and_label_attributes() -> None:
    out = _node(
        f"out = BV.startMsgHtml({_HOSTILE!r}, {{ id: {_HOSTILE!r}, label: {_HOSTILE!r} }}, true);"
    )
    assert "<img" not in out and 'onerror=alert(1)>' not in out.replace("&gt;", "")
    assert out.count("&quot;&gt;&lt;img") == 3


def test_hourly_retry_time_and_reason_text_escape_free() -> None:
    # 429 detail(code·reason·retry_after_seconds) → 다시 가능한 시각(로컬 시각) · 사유 앞 구절만
    out = _node(
        """
var now = new Date(2026, 9, 26, 10, 0, 30).getTime();
var r = BV.startOutcome({ ok: false, status: 429, body: { detail: { code: "hourly_limit",
    reason: "시간당 사용자 요청 조사 상한(3건)에 도달했습니다 — 잠시 후 다시 요청해 주세요.",
    retry_after_seconds: 3570 } } }, now, { failed: "실패 — {reason}", rejected: "거부 — {reason}",
    joined: "합류" });
out = { kind: r.kind, text: r.text, lockMs: r.retryAt - now };
"""
    )
    assert out == {
        "kind": "retry_later",
        "text": (
            "시간당 사용자 요청 조사 상한(3건)에 도달했습니다"
            " — 11:00 이후 다시 요청할 수 있습니다."
        ),
        "lockMs": 3570000,
    }


def test_retry_after_missing_falls_back_to_failed_with_reason() -> None:
    # retry_after_seconds가 없는 429(구서버)는 침묵하지 않고 사유를 싣는다
    out = _node(
        """
var r = BV.startOutcome({ ok: false, status: 429, body: { detail: { code: "hourly_limit",
    reason: "상한 도달" } } }, 0, { failed: "실패 — {reason}", rejected: "", joined: "" });
out = r;
"""
    )
    assert out == {"kind": "failed", "text": "실패 — 상한 도달"}


def test_investigation_state_maps_rejected_and_terminal() -> None:
    out = _node(
        """
out = {
  old: DV.investigationState({ alarm_id: "a" }) === undefined,
  none: DV.investigationState({ investigation: null }).kind,
  queued: DV.investigationState({ investigation: { investigation_id: "i", status: "submitted",
      queue_position: 3 } }).text,
  doneQ: DV.investigationState({ investigation: { investigation_id: "i", status: "done",
      queue_position: 3 } }).kind,
  failed: DV.investigationState({ investigation: { investigation_id: "i", status: "timeout" } }),
};
"""
    )
    assert out["old"] is True and out["none"] == "none"
    assert out["queued"] == "대기 중 · 순번 3" and out["doneQ"] == "done"
    assert out["failed"]["startable"] is True and out["failed"]["kind"] == "fail"
