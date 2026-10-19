"""plans/187 W5a — workb 후속 메시지 요약+링크 (§5.6 ④ · D-137 · D-341 ⑦).

- 종결 브리핑에 `playbook`이 있으면 본문 = 식별 머리 + 「종합 3줄(1순위 가설+상태 · 대응 1순위 ·
  엔진 일치 칩) + 조사 대화 링크 `<base>/?investigation=<id>`」 — 6요소 블록은 싣지 않는다.
- 줄마다 길이 상한(`PLAYBOOK_SUMMARY_LINE_MAX_CHARS`).
- 링크 base가 없으면 링크 대신 「웹에서 확인」 문구.
- `playbook`이 없으면 본문·발송 호출이 종전과 같다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from noise_gate.application.nodes import alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_notifier import (
    PLAYBOOK_SUMMARY_LINE_MAX_CHARS,
    PLAYBOOK_WEB_FALLBACK,
    _investigation_briefing_html,
    build_followup_body,
    playbook_summary_lines,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent

BASE = "https://aiops.example.internal"


def _result() -> AlarmAnalysisResult:
    ev = AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=3,
        alarm_status="NOT_ACK", resource_type="was.Instance", resource_name="r1",
        alarm_name="WAS 응답 지연", alarm_time=datetime(2026, 10, 19, 9, 0, 0),
        conditions=">3000ms", condition_log="p95 3500ms",
    )
    return AlarmAnalysisResult(
        alarm_event=ev, severity_label="심각", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=["workb"],
    )


def _pb_briefing(
    *, title: str = "DB 커넥션 획득 지연 → 풀 고갈", action: str = "풀 점유 트랜잭션 확인",
    agreement: bool = True,
) -> dict:
    briefing = {
        "severity": {"level": "high"},
        "summary": "[플레이북 was.latency] 1순위 가설: …",
        "cause": "1위 … — 지지",
        "playbook": {
            "id": "was.latency",
            "synthesis": {
                "hypotheses": [
                    {"rank": 1, "title": title, "status": "supported", "status_label": "지지"},
                    {"rank": 2, "title": "외부 호출 지연", "status": "refuted",
                     "status_label": "반박"},
                ],
                "actions": [{"order": 1, "source": "runbook", "text": action},
                            {"order": 2, "source": "response_hint", "text": "업무팀 통보"}],
                "narrative": "LLM 서술 — 후속 본문에 싣지 않는다",
            },
        },
    }
    if agreement:
        briefing["engine_agreement"] = {"result": "same_layer", "label": "같은 계층"}
    return briefing


def test_summary_three_lines_and_link():
    body = build_followup_body(_result(), _pb_briefing(), investigation_id="inv-9",
                               web_base_url=BASE + "/")
    assert "1순위 가설: DB 커넥션 획득 지연 → 풀 고갈 [지지]" in body.replace("&gt;", ">")
    assert "대응(제안) 1순위: 풀 점유 트랜잭션 확인" in body
    assert "HolmesGPT: 같은 계층" in body
    assert f'<a href="{BASE}/?investigation=inv-9">' in body
    # 6요소 블록·LLM 서술·2순위 이하는 싣지 않는다.
    assert "조사 브리핑 (자동 조사)" not in body and "LLM 서술" not in body
    assert "외부 호출 지연" not in body and "업무팀 통보" not in body
    # 식별 머리는 종전과 같다.
    assert "<b>알람 ID:</b> A-1" in body


def test_lines_without_agreement_and_hypotheses():
    briefing = _pb_briefing(agreement=False)
    briefing["playbook"]["synthesis"] = {"hypotheses": [], "actions": []}
    assert playbook_summary_lines(briefing) == ["1순위 가설 없음 — 판독 근거 부족"]
    assert playbook_summary_lines(_pb_briefing(agreement=False)) == [
        "1순위 가설: DB 커넥션 획득 지연 → 풀 고갈 [지지]",
        "대응(제안) 1순위: 풀 점유 트랜잭션 확인",
    ]


def test_line_length_cap():
    long_title = "가" * 1000
    lines = playbook_summary_lines(_pb_briefing(title=long_title, action="나" * 1000))
    assert len(lines) == 3
    assert all(len(line) <= PLAYBOOK_SUMMARY_LINE_MAX_CHARS for line in lines)
    assert lines[0].endswith("…") and lines[1].endswith("…")
    body = build_followup_body(_result(), _pb_briefing(title=long_title, action="나" * 1000),
                               investigation_id="inv-9", web_base_url=BASE)
    assert "가" * PLAYBOOK_SUMMARY_LINE_MAX_CHARS not in body
    assert len(body) < 1200


def test_non_http_base_url_uses_fallback_and_warns_once(caplog):
    """(교정 L4) http(s)가 아닌 base는 링크로 만들지 않는다 — 안내 문구 + warning 1회."""
    import logging

    notifier_mod._WARNED_WEB_BASES.clear()
    bad = "javascript:alert(1)//"
    with caplog.at_level(logging.WARNING, logger=notifier_mod.__name__):
        for _ in range(2):
            body = build_followup_body(_result(), _pb_briefing(), investigation_id="inv-9",
                                       web_base_url=bad)
            assert "<a " not in body and "javascript:" not in body
            assert PLAYBOOK_WEB_FALLBACK in body
    warned = [r for r in caplog.records if "http(s)가 아니라" in r.getMessage()]
    assert len(warned) == 1
    notifier_mod._WARNED_WEB_BASES.clear()
    http_body = build_followup_body(_result(), _pb_briefing(), investigation_id="inv-9",
                                    web_base_url="HTTP://aiops.example.internal")
    assert '<a href="HTTP://aiops.example.internal/?investigation=inv-9">' in http_body


def test_no_base_url_uses_web_fallback_text():
    body = build_followup_body(_result(), _pb_briefing(), investigation_id="inv-9", web_base_url="")
    assert "<a " not in body and "?investigation=" not in body
    assert PLAYBOOK_WEB_FALLBACK in body


def test_link_escapes_and_quotes_id():
    body = build_followup_body(_result(), _pb_briefing(), investigation_id='x"&<y',
                               web_base_url=BASE)
    assert "?investigation=x%22%26%3Cy" in body and 'x"&<y' not in body


def test_text_is_html_escaped():
    body = build_followup_body(_result(), _pb_briefing(title="<script>x</script>"),
                               investigation_id="i", web_base_url=BASE)
    assert "<script>" not in body and "&lt;script&gt;" in body


def test_without_playbook_body_is_unchanged():
    briefing = {"cause": "OOM", "evidence": "dmesg"}
    expected = (
        "<b>자동 조사 결과 (후속)</b><br>"
        "<b>알람명:</b> WAS 응답 지연<br>"
        "<b>서버:</b> srv-1 (h1)<br>"
        "<b>알람 ID:</b> A-1"
        + _investigation_briefing_html(briefing)
    )
    assert build_followup_body(_result(), briefing) == expected
    # 링크 인자가 와도 플레이북이 없으면 쓰지 않는다.
    assert build_followup_body(_result(), briefing, investigation_id="i",
                               web_base_url=BASE) == expected


# ── 발송 경로 배선 ───────────────────────────────────────────────────────────
def _gate(**over) -> SimpleNamespace:
    base = dict(investigation_followup_timeout_seconds=5.0, fault_escalation_enabled=False)
    base.update(over)
    return SimpleNamespace(**base)


async def _deliver(monkeypatch, briefing: dict, gate: SimpleNamespace) -> list[tuple]:
    calls: list[tuple] = []

    async def fake_poll(inv_id, gate_cfg):  # noqa: ANN001
        return briefing, "done", None, None

    async def fake_send(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append((args, kwargs))

    monkeypatch.setattr(notifier_mod, "_poll_until_terminal", fake_poll)
    monkeypatch.setattr(notifier_mod, "_send_workb_followup", fake_send)
    pending = {"investigation_id": "inv-7", "alarm_id": "A-1", "fingerprint": "fp"}
    await notifier_mod._deliver_investigation_followup(
        pending, _result(), SimpleNamespace(workb=object()), gate, None,
    )
    return calls


async def test_deliver_passes_link_kwargs_for_playbook(monkeypatch):
    calls = await _deliver(monkeypatch, _pb_briefing(), _gate(investigation_web_base_url=BASE))
    assert len(calls) == 1
    assert calls[0][1] == {"investigation_id": "inv-7", "web_base_url": BASE}


async def test_deliver_without_base_setting_passes_empty_base(monkeypatch):
    calls = await _deliver(monkeypatch, _pb_briefing(), _gate())
    assert calls[0][1] == {"investigation_id": "inv-7", "web_base_url": ""}


async def test_deliver_without_playbook_call_is_unchanged(monkeypatch):
    calls = await _deliver(monkeypatch, {"cause": "OOM"},
                           _gate(investigation_web_base_url=BASE))
    assert len(calls) == 1 and calls[0][1] == {}
    assert len(calls[0][0]) == 4  # (workb_cfg, result, briefing, escalation) — 종전 호출 그대로


async def test_send_workb_followup_puts_summary_in_msg_body(monkeypatch):
    posted: list[dict] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc) -> None:  # noqa: ANN002
            return None

        async def post(self, url, json=None, headers=None):  # noqa: ANN001, A002
            posted.append(json)
            return _Resp()

    monkeypatch.setattr(notifier_mod.httpx, "AsyncClient", _Client)
    workb = SimpleNamespace(
        base_url="http://workb", system_div="S", send_id="u", alias="[a]", bearer_token="t",
        timeout_seconds=1, get_user_ids=lambda sev: "u1",
    )
    await notifier_mod._send_workb_followup(
        workb, _result(), _pb_briefing(), None, investigation_id="inv-7", web_base_url=BASE,
    )
    body = posted[0]["msgBody"]
    assert f"{BASE}/?investigation=inv-7" in body and "HolmesGPT: 같은 계층" in body
    assert posted[0]["msgTitle"] == "[조사 결과] srv-1 (h1)"
