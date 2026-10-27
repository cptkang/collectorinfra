"""plans/187 W20c — 「조사 흐름」 화면 · 사용자 경로 가림 키 · 종결 요약 보관(§5.6.4 · G-47~G-50).

두 갈래로 고정한다:
    1. 서버 — 허브 사용자 경로 키(`USER_EVENT_KEYS`·`_CLIP_KEYS`) ·
       종결 요약 키(`SUMMARY_EVENT_KEYS`) · 새 키(`finding`·`because`·`phrase`)와
       AI 메모(`ai_note` · G-48)가 사용자 경로 2차 가림을 탄다 ·
       partial_briefing 섹션의 `finding`·`because`·`opinion.text`도 가려진다.
    2. 화면 — `diagnosis-view.js`를 node로 실제 실행한다(W15 하네스 재사용):
       단계 카드 · 건너뜀 카드 · 「지금까지」 줄 · 해석 pending/ok/none · 「종합과 다름」 칩 ·
       HolmesGPT 회차 카드 · 전환 탭을 오가도 EventSource 1개 · 실행 기록 탭 DOM 종전 그대로 ·
       매뉴얼 예시 픽스처의 카드 순서(수용 21).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noise_gate.infrastructure.investigation_activity_store import (
    EXCLUDED_KINDS,
    SUMMARY_EVENT_KEYS,
    summarize_events,
)
from noise_gate.infrastructure.investigation_store import playbook_user_view
from src.api import investigation_live_hub as hub_mod
from src.api.routes.alarm import _investigation_mask_fn
from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_ROOT = Path(__file__).resolve().parents[2]
_MANUAL_FIX = _ROOT / "scripts" / "manual" / "fixtures" / "alarms" / "briefing.json"

MASK = "***MASKED***"
_mask = _investigation_mask_fn(SimpleNamespace(security=SimpleNamespace(mask_pattern=MASK)))

# 새 키 표본 — 비밀값 · 명령행 · SQL 리터럴(사용자 경로 2차 가림 대상)
_PW = "hunter2"
_CARD = "4111111111111111"
_JWT = "eyJhbGciOi.eyJzdWIi.sig"


# ─── 1. 서버 — 키 목록 ─────────────────────────────────────────────────────


def test_user_event_and_clip_keys_include_flow_sentences() -> None:
    assert hub_mod.USER_EVENT_KEYS == (
        "seq",
        "at",
        "engine",
        "kind",
        "step",
        "tool",
        "label",
        "status",
        "elapsed_ms",
        "rows",
        "excerpt",
        "purpose",
        "note",
        "finding",
        "because",
        "phrase",
    )
    assert hub_mod._CLIP_KEYS == (
        "label",
        "excerpt",
        "note",
        "purpose",
        "finding",
        "because",
        "phrase",
    )
    # 새 문자열 키는 마스킹 대상(가림 제외 목록에 없다) · AI 메모도 사용자에게 간다(G-48)
    assert not {"finding", "because", "phrase", "note"} & hub_mod._UNMASKED_EVENT_KEYS
    assert hub_mod.ADMIN_ONLY_KINDS == frozenset()
    assert hub_mod.ADMIN_ONLY_ENGINE_KINDS == frozenset({("playbook", "ai_note")})


def test_user_path_drops_playbook_draft_note_keeps_holmes_note() -> None:
    """G-48 범위 = HolmesGPT `ai_note`만 — 플레이북 소견 초안(가드 전)은 사용자 경로에서 뺀다."""
    events = [
        {"seq": 1, "engine": "holmes", "kind": "ai_note", "note": f"Bearer {_JWT} 의심"},
        {"seq": 2, "engine": "playbook", "kind": "ai_note", "step": "S4", "note": "가드 전 초안"},
        {"seq": 3, "engine": "playbook", "kind": "step_end", "step": "S4", "finding": "최대 96%"},
    ]
    user = hub_mod.user_events(events, _mask)
    assert [e["seq"] for e in user] == [1, 3]
    assert _JWT not in user[0]["note"] and "의심" in user[0]["note"]
    assert "가드 전 초안" not in json.dumps(user, ensure_ascii=False)


def test_summary_keys_keep_flow_sentences_and_exclude_ai_note() -> None:
    assert SUMMARY_EVENT_KEYS == (
        "seq",
        "at",
        "engine",
        "kind",
        "step",
        "tool",
        "label",
        "status",
        "elapsed_ms",
        "rows",
        "purpose",
        "finding",
        "because",
        "phrase",
    )
    assert EXCLUDED_KINDS == frozenset({"ai_note"})
    out = summarize_events(
        [
            {
                "seq": 1,
                "kind": "step_start",
                "engine": "playbook",
                "step": "S4",
                "label": "S4 추이",
                "because": "왜",
                "excerpt": "발췌",
            },
            {
                "seq": 2,
                "kind": "tool_end",
                "engine": "playbook",
                "step": "S4",
                "tool": "t",
                "label": "t",
                "phrase": "폴스타 · 추이",
                "excerpt": "발췌",
                "status": "ok",
            },
            {
                "seq": 3,
                "kind": "step_end",
                "engine": "playbook",
                "step": "S4",
                "finding": "최대 96%",
            },
            {"seq": 4, "kind": "ai_note", "engine": "holmes", "note": "AI 메모"},
            {
                "seq": 5,
                "kind": "step_skip",
                "engine": "playbook",
                "step": "S1",
                "label": "S1 골든",
                "status": "skipped",
                "because": "정상이라 생략",
            },
        ]
    )
    assert [e["seq"] for e in out] == [1, 2, 3, 5]
    assert out[0]["because"] == "왜" and out[1]["phrase"] == "폴스타 · 추이"
    assert out[2]["finding"] == "최대 96%"
    assert out[3]["kind"] == "step_skip" and out[3]["because"] == "정상이라 생략"
    assert all("excerpt" not in e and "note" not in e for e in out)


# ─── 1. 서버 — 사용자 경로 2차 가림 ─────────────────────────────────────────


def _secret_flow_events() -> list[dict[str, Any]]:
    return [
        {
            "seq": 1,
            "at": 1.0,
            "engine": "playbook",
            "kind": "step_start",
            "step": "S6",
            "label": "S6 프로세스 CPU 상위",
            "because": f"토큰 Bearer {_JWT} 확인",
        },
        {
            "seq": 2,
            "at": 2.0,
            "engine": "playbook",
            "kind": "tool_end",
            "step": "S6",
            "tool": "polestar_top_processes",
            "label": "polestar_top_processes",
            "phrase": f"java -jar app.jar --password={_PW} -Xmx2g",
            "status": "ok",
        },
        {
            "seq": 3,
            "at": 3.0,
            "engine": "playbook",
            "kind": "step_end",
            "step": "S3b",
            "finding": f"SELECT * FROM PAY WHERE CARD_NO = '{_CARD}'",
            "status": "ok",
        },
        {
            "seq": 4,
            "at": 4.0,
            "engine": "holmes",
            "kind": "ai_note",
            "label": "HolmesGPT 1회차 메모",
            "note": f"DB 접속 postgresql://u:{_PW}@db:5432/x 지연 의심",
        },
    ]


def test_user_path_masks_new_keys_and_ai_note() -> None:
    out = hub_mod.user_events(_secret_flow_events(), _mask)
    raw = json.dumps(out, ensure_ascii=False)
    assert _PW not in raw and _CARD not in raw and _JWT not in raw
    assert [e["seq"] for e in out] == [1, 2, 3, 4]
    # 가림 표지는 규칙마다 다르다(`mask_secrets` = *** · 값 패턴 = 설정 표지) — 앞뒤 문장은 남는다
    assert out[0]["because"].startswith("토큰 Bearer ***") and out[0]["because"].endswith(" 확인")
    assert out[1]["phrase"].startswith("java -jar app.jar --password=***")
    assert out[2]["finding"].startswith("SELECT * FROM PAY WHERE CARD_NO = '***")
    # G-48 — 사용자에게도 AI 메모가 가린 뒤 보인다
    assert out[3]["kind"] == "ai_note" and "***" in out[3]["note"] and "지연 의심" in out[3]["note"]


def test_new_keys_are_clipped_like_label() -> None:
    long = "가" * (hub_mod.TEXT_MAX_CHARS + 10)
    ev = hub_mod._clip_event({"seq": 1, "finding": long, "because": long, "phrase": long})
    for key in ("finding", "because", "phrase"):
        assert len(ev[key]) == hub_mod.TEXT_MAX_CHARS + 1 and ev[key].endswith("…")


def test_partial_sections_flow_fields_masked_on_user_path() -> None:
    partial = {
        "partial": True,
        "playbook": {
            "id": "host.cpu",
            "sections": [
                {
                    "step": "S3b",
                    "action": "apm.active_sql",
                    "status": "ok",
                    "finding": f"CARD_NO = '{_CARD}'",
                    "opinion": {
                        "status": "ok",
                        "text": f"--password={_PW} 노출",
                        "basis": "이 단계까지의 근거",
                    },
                },
                {
                    "step": "S1",
                    "action": "apm.golden_signals",
                    "status": "skipped",
                    "because": f"Bearer {_JWT} 정상이라 생략",
                },
                {
                    "step": "S4",
                    "action": "host.usage_trend",
                    "status": "ok",
                    "opinion": {"status": "none", "reason": "가드 탈락"},
                },
            ],
        },
    }
    view = playbook_user_view(partial, _mask)
    assert view is not None
    raw = json.dumps(view, ensure_ascii=False)
    assert _PW not in raw and _CARD not in raw and _JWT not in raw
    secs = view["playbook"]["sections"]
    assert secs[0]["opinion"]["status"] == "ok" and secs[2]["opinion"]["status"] == "none"
    assert secs[1]["status"] == "skipped" and "정상이라 생략" in secs[1]["because"]


# ─── 2. 화면 ──────────────────────────────────────────────────────────────

_RUN_EVENTS = [
    {
        "seq": 1,
        "at": 1792395100,
        "engine": "playbook",
        "kind": "step_start",
        "step": "S4",
        "label": "S4 호스트 자원 시간대별 추이",
        "because": "알람 「CPU 사용률 95%」 — 평소보다 얼마나 높은지부터 본다",
    },
    {
        "seq": 2,
        "at": 1792395101,
        "engine": "playbook",
        "kind": "tool_start",
        "step": "S4",
        "tool": "polestar_metric_trend",
        "label": "polestar_metric_trend {host}",
        "phrase": "폴스타 · SAE0011 CPU 사용률 시간대별",
    },
    {
        "seq": 3,
        "at": 1792395102,
        "engine": "playbook",
        "kind": "tool_end",
        "step": "S4",
        "tool": "polestar_metric_trend",
        "label": "polestar_metric_trend {host}",
        "phrase": "폴스타 · SAE0011 CPU 사용률 시간대별",
        "status": "ok",
        "elapsed_ms": 900,
        "rows": 12,
    },
    {
        "seq": 4,
        "at": 1792395103,
        "engine": "playbook",
        "kind": "step_end",
        "step": "S4",
        "label": "S4 호스트 자원 시간대별 추이",
        "status": "ok",
        "elapsed_ms": 1200,
        "finding": "CPU 포화 — 최대 96% · 전일의 2.2배",
    },
    {
        "seq": 5,
        "at": 1792395104,
        "engine": "playbook",
        "kind": "step_start",
        "step": "S6",
        "label": "S6 프로세스 CPU 상위",
        "because": "CPU 포화 → 어느 프로세스가 쓰는지 확인",
    },
    {
        "seq": 6,
        "at": 1792395105,
        "engine": "playbook",
        "kind": "step_end",
        "step": "S6",
        "label": "S6 프로세스 CPU 상위",
        "status": "ok",
        "elapsed_ms": 800,
        "finding": "1위 java(WAS A1) 78% · 2위 sshd 3%",
    },
    {
        "seq": 7,
        "at": 1792395106,
        "engine": "playbook",
        "kind": "step_skip",
        "step": "S1",
        "label": "S1 WAS 골든 시그널",
        "status": "skipped",
        "because": "호스트의 WAS 인스턴스 영향 결과가 정상이라 생략",
    },
    {
        "seq": 8,
        "at": 1792395107,
        "engine": "playbook",
        "kind": "step_start",
        "step": "S3a",
        "label": "S3a 액티브 서비스",
        "because": "A1이 CPU 대부분 → 지금 무엇을 처리 중인지",
    },
    {
        "seq": 9,
        "at": 1792395108,
        "engine": "playbook",
        "kind": "tool_start",
        "step": "S3a",
        "tool": "apm_active_services",
        "label": "apm_active_services {A1}",
    },
]

_RUN_PARTIAL = {
    "partial": True,
    "playbook": {
        "id": "host.cpu",
        "version": 2,
        "sections": [
            {
                "step": "S4",
                "action": "host.usage_trend",
                "title": "호스트 자원 시간대별 추이",
                "status": "ok",
                "verdict": "degraded",
                "verdict_source": "playbook_rule",
                "badges": [],
                "table": {"columns": ["metric", "max"], "rows": [["cpu_pct", 96.0]]},
                "finding": "CPU 포화 — 최대 96% · 전일의 2.2배",
                "opinion": {
                    "status": "ok",
                    "text": "전일 대비 급상승이며 포화가 지속된다",
                    "basis": "이 단계까지의 근거",
                },
            },
            {
                "step": "S6",
                "action": "host.process_top",
                "title": "프로세스 CPU 상위",
                "status": "ok",
                "badges": ["현재값 · 알람 후 25분"],
                "table": {"columns": ["process", "cpu_pct"], "rows": [["java", 78.0]]},
                "finding": "1위 java(WAS A1) 78% · 2위 sshd 3%",
                "opinion": {"status": "pending"},
            },
            {
                "step": "S1",
                "action": "apm.golden_signals",
                "title": "WAS 골든 시그널",
                "status": "skipped",
                "because": "호스트의 WAS 인스턴스 영향 결과가 정상이라 생략",
            },
        ],
    },
}


def _flow_script(
    events: list[dict], partial: dict | None, *, operator: bool = False, extra: str = ""
) -> str:
    """조사 1건을 열고 activity(·partial) 프레임을 받은 뒤 조사 흐름 화면을 읽는다."""
    partial_js = (
        f'liveES()[0].emit("partial", {{ investigation_id: "inv-f", partial_briefing: '
        f"{json.dumps(partial, ensure_ascii=False)} }});"
        if partial is not None
        else ""
    )
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-f"] = {{ status: 200, body: running("inv-f") }};
var v = make({str(operator).lower()});
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-f");
await flush();
runTimers(1000);
liveES()[0].onopen();
liveES()[0].emit("activity", {{ investigation_id: "inv-f", status: "running", next_seq: 99,
    events: {json.dumps(events, ensure_ascii=False)} }});
{partial_js}
var tab = v.ctl.tab("inv-f");
function cards() {{ return findAll(tab.dom.flowBody, "dv-card").map(function (c) {{
    return {{ text: c.textContent, cls: c.className, step: c.getAttribute("data-step"),
        title: texts(c, "dv-card-title")[0], why: texts(c, "dv-why"), q: texts(c, "dv-q-text"),
        finding: texts(c, "dv-finding"), op: texts(c, "dv-opinion"), live: texts(c, "dv-live"),
        verdict: texts(c, "dv-verdict"), conflict: texts(c, "dv-conflict"),
        caution: texts(c, "dv-caution"), judge: texts(c, "dv-ai-badge") }}; }}); }}
{extra}
"""


def test_running_flow_cards_sofar_and_opinions() -> None:
    out = _node(
        _flow_script(
            _RUN_EVENTS,
            _RUN_PARTIAL,
            extra="""
var table = findAll(tab.dom.flowBody, "dv-card-table-wrap")[0];
var hiddenBefore = table.hidden;
findAll(tab.dom.flowBody, "dv-flow-toggle")[0].click();
out = { cards: cards(), sofar: tab.dom.sofarText.textContent, before: hiddenBefore,
        after: table.hidden, mode: tab.mode, flowHidden: tab.dom.flowBox.hidden,
        gridHidden: tab.dom.grid.hidden };
""",
        )
    )
    cards = out["cards"]
    assert [c["step"] for c in cards] == ["S4", "S6", "S1", "S3a"]
    s4, s6, s1, s3a = cards
    # 처음 열면 「조사 흐름」 · 실행 기록(dv-grid)은 숨김
    assert out["mode"] == "flow" and out["flowHidden"] is False and out["gridHidden"] is True
    # 단계 카드 = 번호 · 제목 · 상태 · 판정 · 왜 · 조회(phrase) · 결과(finding) · 해석
    assert (
        s4["title"] == "호스트 자원 시간대별 추이" and "①" in s4["text"] and "✓ 1.2s" in s4["text"]
    )
    assert s4["verdict"] == ["이상"]
    assert s4["why"] == ["알람 「CPU 사용률 95%」 — 평소보다 얼마나 높은지부터 본다"]
    assert s4["q"] == ["폴스타 · SAE0011 CPU 사용률 시간대별"]  # 시작·끝 짝은 한 줄
    assert s4["finding"] == ["CPU 포화 — 최대 96% · 전일의 2.2배"]
    assert s4["op"] == ["AI · 이 단계까지의 근거전일 대비 급상승이며 포화가 지속된다"]
    assert "원인 확정이 아닌 단계 해석입니다." in s4["caution"][0]
    assert "최종 판단은 종합을 따르세요" in s4["caution"][0]
    assert out["before"] is True and out["after"] is False  # 「표 보기 ▸」 카드 안 펼침
    # 해석 작성 중(C8) · 현재값 배지(C4 — 알람 후 25분 > 10분)
    assert s6["op"] == ["AI 해석 작성 중 ⟳"] and "AI 응답 시간만큼" in s6["caution"][0]
    assert s6["live"][0].startswith("현재값 · 조사 시각 ")
    assert "알람 시점과 다를 수 있음" in s6["caution"][0]
    # 건너뜀 카드(이벤트 + 섹션 — 한 장)
    assert "dv-card--skip" in s1["cls"]
    assert "건너뜀 WAS 골든 시그널 — 호스트의 WAS 인스턴스 영향 결과가 정상이라 생략" in s1["text"]
    # 진행 중 단계 — 사전 밖 도구는 현행 한 줄(label)
    assert "⟳ 진행 중" in s3a["text"] and s3a["q"] == ["apm_active_services {A1}"]
    # 지금까지 — 결과 고리 + 확인 중
    assert out["sofar"] == "CPU 포화 → 1위 java(WAS A1) 78% → 액티브 서비스 확인 중 ⟳"


def test_legacy_events_without_new_keys_still_draw_cards() -> None:
    legacy = [
        {
            "seq": 1,
            "at": 1.0,
            "engine": "playbook",
            "kind": "step_start",
            "step": "S2",
            "label": "S2 WAS 골든 시그널",
            "purpose": "응답·TPS·에러율 비교",
        },
        {
            "seq": 2,
            "at": 2.0,
            "engine": "playbook",
            "kind": "tool_end",
            "step": "S2",
            "tool": "apm_app_health",
            "label": "apm_app_health {instance: was-01}",
            "status": "ok",
            "rows": 4,
        },
        {
            "seq": 3,
            "at": 3.0,
            "engine": "playbook",
            "kind": "step_end",
            "step": "S2",
            "label": "S2 WAS 골든 시그널",
            "status": "ok",
            "elapsed_ms": 1290,
        },
    ]
    out = _node(
        _flow_script(
            legacy, None, extra="out = { cards: cards(), sofar: tab.dom.sofarText.textContent };"
        )
    )
    assert len(out["cards"]) == 1
    card = out["cards"][0]
    assert card["title"] == "WAS 골든 시그널"
    assert card["why"] == ["응답·TPS·에러율 비교"]  # because 없으면 목적
    assert card["q"] == ["apm_app_health {instance: was-01}"] and "4행" in card["text"]
    assert card["finding"] == [] and card["op"] == []
    assert out["sofar"] == "아직 이상으로 본 단계가 없습니다."


def test_holmes_rounds_show_ai_judgment_to_users_and_split_engines() -> None:
    events = _RUN_EVENTS[:4] + [
        {
            "seq": 20,
            "at": 1.0,
            "engine": "holmes",
            "kind": "ai_note",
            "label": "HolmesGPT 1회차 메모",
            "note": "CPU 추이를 먼저 확인하겠다",
        },
        {
            "seq": 21,
            "at": 2.0,
            "engine": "holmes",
            "kind": "tool_start",
            "tool": "polestar_metric_trend",
            "label": "polestar_metric_trend",
        },
        {
            "seq": 22,
            "at": 3.0,
            "engine": "holmes",
            "kind": "tool_end",
            "tool": "polestar_metric_trend",
            "label": "CPU 추이",
            "status": "success",
            "rows": 3,
            "excerpt": "cpu=96",
        },
        {
            "seq": 23,
            "at": 4.0,
            "engine": "holmes",
            "kind": "ai_note",
            "label": "HolmesGPT 2회차 메모",
            "note": "WAS 부하로 보인다",
        },
        # 플레이북 소견 초안 메모는 카드에 싣지 않는다(해석은 섹션에서 읽는다)
        {
            "seq": 24,
            "at": 5.0,
            "engine": "playbook",
            "kind": "ai_note",
            "step": "S4",
            "label": "S4 소견 초안",
            "note": "가드 전 초안",
        },
    ]
    out = _node(
        _flow_script(
            events,
            None,
            extra="""
out = { cards: cards(), groups: texts(tab.dom.flowBody, "dv-flow-engine"),
        log: findAll(tab.dom.grid, "dv-row--ai").length };
""",
        )
    )
    holmes = [c for c in out["cards"] if "dv-card--holmes" in c["cls"]]
    assert out["groups"] == ["플레이북", "HolmesGPT"]  # parallel — 엔진별 줄
    assert [c["title"] for c in holmes] == ["HolmesGPT 1회차", "HolmesGPT 2회차"]
    assert holmes[0]["judge"] == ["AI 판단 — 검증 전"] and holmes[0]["q"] == ["CPU 추이"]
    assert "CPU 추이를 먼저 확인하겠다" in holmes[0]["op"][0] and "3행" in holmes[0]["text"]
    caution = holmes[0]["caution"][0]
    assert "자동 검사를 거치지 않은 AI 중간 판단입니다." in caution
    assert "원인 확정이 아닌 단계 해석입니다." in caution and "사용자 화면에도" in caution
    assert holmes[1]["q"] == [] and "이 회차에는 조회가 없습니다." in holmes[1]["text"]
    assert "가드 전 초안" not in json.dumps(out["cards"], ensure_ascii=False)
    # 실행 기록 탭은 종전 그대로 — 사용자 화면 AI 메모 행 0
    assert out["log"] == 0


def _done_script(final_briefing: dict, events: list[dict]) -> str:
    final = {
        "status": "done",
        "investigation_id": "inv-f",
        "kind": "alarm",
        "briefing_lines": [],
        "briefing": final_briefing,
        "activity": {"next_seq": 99, "truncated": False, "source": "archive", "events": events},
    }
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-f"] = {{ status: 200, body: {json.dumps(final, ensure_ascii=False)} }};
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-f");
await flush();
runTimers(1000);
var tab = v.ctl.tab("inv-f");
function cards() {{ return findAll(tab.dom.flowBody, "dv-card").map(function (c) {{
    return {{ text: c.textContent, cls: c.className, step: c.getAttribute("data-step"),
        title: texts(c, "dv-card-title")[0], why: texts(c, "dv-why"), q: texts(c, "dv-q-text"),
        finding: texts(c, "dv-finding"), op: texts(c, "dv-opinion"), live: texts(c, "dv-live"),
        conflict: texts(c, "dv-conflict"), caution: texts(c, "dv-caution") }}; }}); }}
out = {{ done: tab.done, cards: cards(), sofar: tab.dom.sofarText.textContent,
        final: tab.dom.finalBody.innerHTML.indexOf("bv--playbook") >= 0 }};
"""


def _manual_example() -> tuple[dict, list[dict]]:
    data = json.loads(_MANUAL_FIX.read_text(encoding="utf-8"))
    rec = [
        r
        for r in data["investigations"]
        if r["investigation_id"] == "inv-manual-flow" and r["status"] == "done"
    ][0]
    act = [a for a in data["activity"] if a["investigation_id"] == "inv-manual-flow"][0]
    events = [
        {k: v for k, v in e.items() if k != "ago_min"} | {"at": 1792395000 + e["seq"]}
        for e in act["events"]
    ]
    return rec["briefing"], events


def test_manual_example_card_order_and_done_synthesis() -> None:
    """수용 21 — 사용자 예시 사슬이 CPU 추이 → 프로세스 상위 → WAS 프로세스 CPU → 실행 중 요청 →
    실행 중 SQL 순으로 나오고, 카드마다 왜·조회·결과가 있다.
    종결 보관 요약으로 다시 열어도 그려진다."""
    briefing, events = _manual_example()
    out = _node(_done_script(briefing, events))
    assert out["done"] is True and out["final"] is True
    cards = out["cards"]
    titles = [c["title"] for c in cards]
    chain = [
        "호스트 자원 시간대별 추이",
        "프로세스 CPU 상위",
        "JVM 런타임 · DB 커넥션 풀",
        "액티브 서비스",
        "실행 중 SQL",
    ]
    idx = [titles.index(t) for t in chain]
    assert idx == sorted(idx), titles
    for c in cards:
        if "dv-card--skip" in c["cls"]:
            continue
        assert c["why"] and c["q"] and c["finding"], c
    skips = [c for c in cards if "dv-card--skip" in c["cls"]]
    assert len(skips) == 1 and "정상이라 생략" in skips[0]["text"]
    by = {c["step"]: c for c in cards}
    assert by["S4"]["op"][0].startswith("AI · 이 단계까지의 근거")  # 종결 브리핑 문자열 소견(ok)
    assert by["S2"]["op"] == ["AI 해석 없음 — 표에 없는 수치를 써서 가드가 버렸습니다"]  # none(C5)
    assert "AI 해석이 없습니다" in by["S2"]["caution"][0]
    assert by["S3b"]["live"][0].startswith("현재값 · 조사 시각 ")
    assert all(not c["conflict"] for c in cards)  # 1위 지지 + 근거 단계 이상 = 같은 방향
    # 종결 — 「지금까지」는 종합(가설 1위 + 서술 첫 문장)
    assert out["sofar"] == (
        "종합 — WAS 요청 처리 부하(CPU 주인 = WAS)(지지) · "
        "CPU 포화는 WAS 인스턴스 A1의 주문 요청 처리에서 비롯된 것으로 보인다."
    )


def test_conflict_chip_when_step_polarity_differs_from_top_hypothesis() -> None:
    briefing, events = _manual_example()
    pb = briefing["playbook"]
    # S0r(정상 판정)에 해석을 붙이고 1위 가설(지지)의 근거로 넣으면 극성 반대 → 칩
    for sec in pb["sections"]:
        if sec["step"] == "S0r":
            sec["opinion"] = "WAS 인스턴스 응답은 정상이다"
    pb["synthesis"]["hypotheses"][0]["evidence_steps"].append("S0r")
    out = _node(_done_script(briefing, events))
    by = {c["step"]: c for c in out["cards"]}
    assert by["S0r"]["conflict"] == ["종합과 다름 — 종합 기준"]
    assert "종합을 기준으로 보세요" in by["S0r"]["caution"][0]
    assert by["S4"]["conflict"] == []


def test_conflict_rule_pure() -> None:
    out = _node("""
var top = function (status) { return { rank: 1, status: status, evidence_steps: ["S1"] }; };
var sec = function (verdict, step) { return { step: step || "S1", verdict: verdict }; };
out = [
    DV.conflictsWithSynthesis(sec("normal"), top("supported")),
    DV.conflictsWithSynthesis(sec("degraded"), top("refuted")),
    DV.conflictsWithSynthesis(sec("degraded"), top("supported")),
    DV.conflictsWithSynthesis(sec("normal"), top("inconclusive")),
    DV.conflictsWithSynthesis(sec(undefined), top("supported")),
    DV.conflictsWithSynthesis(sec("normal", "S9"), top("supported")),
    DV.opinionOf({ opinion: { status: "none", reason: "시간 초과" } }),
    DV.opinionOf({ opinion: "", opinion_dropped_reason: "극성 반대" }),
    DV.opinionOf({ opinion: "" }),
];
""")
    assert out[:6] == [True, True, False, False, False, False]
    assert out[6] == {"status": "none", "reason": "시간 초과"}
    assert out[7] == {"status": "none", "reason": "극성 반대"}
    assert out[8] is None


def test_flow_order_sofar_filter_and_skip_reason_pure() -> None:
    """교정(Major 1·2 · Minor 8) — 섹션 순서 · 「지금까지」 고리 규칙 · 건너뜀 원문 사유 대체."""
    out = _node("""
var ev = function (seq, step, kind, extra) {
    var e = { seq: seq, at: 1792390000 + seq, engine: "playbook", kind: kind,
              step: step, label: step };
    Object.keys(extra || {}).forEach(function (k) { e[k] = extra[k]; });
    return e;
};
var t = function (n) {
    var rows = [];
    for (var i = 0; i < n; i++) rows.push([i]);
    return { columns: ["v"], rows: rows };
};
var events = [
    ev(1, "S6", "step_start"), ev(2, "S4", "step_start"),
    ev(3, "S6", "step_end", { status: "ok", finding: "1위 java 78%" }),
    ev(4, "S4", "step_end", { status: "ok", finding: "CPU 이상 96%" }),
    ev(5, "S2", "step_end", { status: "ok", finding: "WAS CPU 정상" }),
    ev(6, "S5", "step_end", { status: "ok", finding: "판정 없음 · 결과 0건" }),
    ev(7, "S8", "step_start"),
];
var sections = [
    { step: "S4", status: "ok", verdict: "degraded", table: t(1) },
    { step: "S6", status: "ok", table: t(2) },
    { step: "S1", status: "skipped", reason: "when_false — S0r.status == 'ok'" },
    { step: "S9", status: "skipped", reason: "budget — 남은 3s" },
    { step: "S2", status: "ok", verdict: "normal", table: t(2) },
    { step: "S5", status: "ok", table: t(0) },
];
var m = DV.flowModel(events, sections, { done: false });
out = { order: m.cards.map(function (c) { return c.step; }), sofar: m.sofar,
        skip: m.cards.filter(function (c) { return c.skipped; })
            .map(function (c) { return c.because; }) };
""")
    assert out["order"] == ["S4", "S6", "S1", "S9", "S2", "S5", "S8"]
    assert out["sofar"] == "CPU 이상 96% → 1위 java 78% → S8 확인 중 ⟳"
    assert out["skip"] == ["조건에 맞지 않아 생략", "시간 예산 소진으로 생략"]


def test_mode_switch_keeps_one_stream_and_log_dom_unchanged() -> None:
    out = _node(
        _flow_script(
            _RUN_EVENTS,
            _RUN_PARTIAL,
            extra="""
var made = esList.length;
function shape(n) { return { c: n.className, k: n.children.map(shape) }; }
var btns = findAll(tab.dom.panel, "dv-mode");
btns[1].click();
var inLog = { grid: tab.dom.grid.hidden, flow: tab.dom.flowBox.hidden, sel: btns[1].className };
var rowsBefore = texts(tab.dom.grid, "dv-row");
btns[0].click();
btns[1].click();
btns[0].click();
runTimers(1000);
out = { labels: btns.map(function (b) { return b.textContent; }), made: made, after: esList.length,
        live: liveES().length, inLog: inLog,
        back: { grid: tab.dom.grid.hidden, flow: tab.dom.flowBox.hidden },
        rowsSame: JSON.stringify(rowsBefore) === JSON.stringify(texts(tab.dom.grid, "dv-row")),
        grid: shape(tab.dom.grid).k.map(function (x) { return x.c; }),
        log: shape(tab.dom.grid.children[0]).k.map(function (x) { return x.c; }),
        bar: shape(tab.dom.grid.children[0].children[0]).k.map(function (x) { return x.c; }),
        part: shape(tab.dom.grid.children[1]).k.map(function (x) { return x.c; }) };
""",
        )
    )
    assert out["labels"] == ["조사 흐름", "실행 기록"]
    assert out["made"] == out["after"] and out["live"] == 1  # 전환은 스트림을 다시 열지 않는다
    assert out["inLog"] == {"grid": False, "flow": True, "sel": "dv-mode active"}
    assert out["back"] == {"grid": True, "flow": False}
    assert out["rowsSame"] is True
    # 실행 기록 탭 = 종전 dv-grid 골격 그대로(좌 실행 기록 · 우 중간 결과)
    assert out["grid"] == ["dv-log", "dv-partial"]
    assert out["log"] == ["dv-log-bar", "dv-note", "dv-rows"]
    assert out["bar"] == ["dv-label", "dv-filters", "dv-check"]
    assert out["part"] == ["dv-label", "dv-partial-body"]
