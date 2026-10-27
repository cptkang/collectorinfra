"""plans/187 W20 독립 검증(verifier) — sre_agent가 실제로 낸 조사 흐름 출력 → 본체 화면 종단.

입력은 `sre_agent/tests/test_plan187_w20_verify.py`가 디스패처 종단 실행(host.cpu v2 · 기록 on ·
가짜 LLM)으로 떨어뜨린 JSON 픽스처다(파일 계약만 — 양방향 import 0). 이 테스트는 그 이벤트·부분
브리핑을 본체 사용자 경로(허브 `user_events` · `playbook_user_view`)와 종결 요약 보관
(`summarize_events`)에 통과시킨 뒤 `diagnosis-view.js`의 `flowModel`(node 실행)에 넣는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from noise_gate.infrastructure.investigation_activity_store import summarize_events
from noise_gate.infrastructure.investigation_store import playbook_user_view
from src.api import investigation_live_hub as hub_mod
from src.api.routes.alarm import _investigation_mask_fn
from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_ROOT = Path(__file__).resolve().parents[2]
_FIX = (
    _ROOT / "sre_agent" / "tests" / "fixtures" / "plan187" / "snapshots" / "w20_flow_hostcpu.json"
)
_mask = _investigation_mask_fn(SimpleNamespace(security=SimpleNamespace(mask_pattern="***")))

#: 수용 21 — 카드 순서(부분 수열)와 그 단계 id
CHAIN = ["S4", "S6", "S2", "S3a", "S3b"]


@pytest.fixture(scope="module")
def fx() -> dict:
    if not _FIX.exists():
        pytest.skip("sre_agent 픽스처 없음 — sre_agent/tests/test_plan187_w20_verify.py 먼저")
    return json.loads(_FIX.read_text(encoding="utf-8"))


def _model(events: list, sections: list, opts: dict) -> dict:
    return _node(
        f"out = DV.flowModel({json.dumps(events, ensure_ascii=False)}, "
        f"{json.dumps(sections, ensure_ascii=False)}, {json.dumps(opts, ensure_ascii=False)});"
    )


def _cards(model: dict) -> dict[str, dict]:
    return {c["step"]: c for c in model["cards"]}


def _assert_chain(model: dict) -> None:
    order = [c["step"] for c in model["cards"]]
    assert [s for s in order if s in CHAIN] == CHAIN, order
    cards = _cards(model)
    for sid in CHAIN:
        c = cards[sid]
        assert c["because"], (sid, "왜")
        assert c["queries"] and all(q["phrase"] for q in c["queries"]), (sid, "조회")
        assert c["finding"], (sid, "결과")


def test_running_user_path_cards_follow_chain(fx: dict) -> None:
    """진행 중(사용자 경로 두 겹 가림 후) — 카드 순서 · 왜·조회·결과 · 건너뜀 문장 · 해석 상태."""
    events = hub_mod.user_events(fx["events"], _mask)
    partial = playbook_user_view(fx["partial"], _mask)
    assert partial is not None
    model = _model(events, partial["playbook"]["sections"], {"done": False})
    _assert_chain(model)
    s1 = _cards(model)["S1"]
    assert s1["skipped"] and s1["because"] and "==" not in s1["because"]
    assert "when_false" not in s1["because"]
    # 소견 대상 단계는 해석 상태가 있다(pending·ok·none 중 하나)
    assert all(_cards(model)[s]["opinion"] for s in ("S4", "S6", "S2", "S3a", "S3b"))
    # 플레이북 소견 초안(가드 전 ai_note)은 사용자 경로에 없다
    assert not [e for e in events if e["engine"] == "playbook" and e["kind"] == "ai_note"]


def test_sofar_line_links_the_chain_while_running(fx: dict) -> None:
    """「지금까지」 줄이 사슬(CPU 추이 → 프로세스 → WAS CPU → 실행 중 요청 → SQL)을 잇는가."""
    model = _model(fx["events"], fx["partial"]["playbook"]["sections"], {"done": False})
    sofar = model["sofar"]
    cards = _cards(model)
    # 최대 4고리 — 실행 중 요청·실행 중 SQL 고리는 있어야 한다.
    # 고리 규칙(이상 판정 + 판정 없는 1행 이상 단계)상 정상 판정인 S2(이 픽스처
    # verdict normal)와 0행 단계(S5·S7 「판정 없음 · 결과 0건」)는 빠진다.
    for sid in ("S3a", "S3b"):
        head = cards[sid]["finding"].split(" · ")[0].split(" — ")[0]
        assert head in sofar, (sid, sofar)
    assert "판정 없음" not in sofar, sofar


def test_archived_summary_redraws_cards_after_ttl(fx: dict) -> None:
    """잡 TTL 뒤 — 보관 요약 이벤트 + 종결 브리핑 섹션(finding 없음)만으로 카드가 다시 그려진다."""
    archived = summarize_events(fx["events"])
    assert all("excerpt" not in e for e in archived)
    sections = fx["final_playbook"]["sections"]
    assert all("finding" not in s for s in sections)
    synth = fx["final_playbook"]["synthesis"]
    model = _model(archived, sections, {"done": True, "synthesis": synth})
    _assert_chain(model)
    s1 = _cards(model)["S1"]
    assert s1["skipped"] and "when_false" not in s1["because"] and "==" not in s1["because"]


def test_final_skipped_without_events_falls_back_to_raw_condition(fx: dict) -> None:
    """이벤트가 잘린(행 상한 300 · 구버전) 종결 화면 — 건너뜀 카드 사유가 조건식 원문이 된다."""
    events = [e for e in fx["events"] if e.get("kind") != "step_skip"]
    model = _model(events, fx["final_playbook"]["sections"], {"done": True})
    because = _cards(model)["S1"]["because"]
    assert "when_false" not in because, because


def test_cards_follow_playbook_order_even_if_parallel_steps_start_out_of_order(fx: dict) -> None:
    """같은 단계(stage)의 병렬 스텝 — S6이 S4보다 먼저 시작해도 카드는 플레이북 순서여야 한다."""
    events = [dict(e) for e in fx["events"]]
    s4 = [e for e in events if e.get("step") == "S4"]
    s6 = [e for e in events if e.get("step") == "S6"]
    seqs = sorted(e["seq"] for e in s4 + s6)
    # S6 이벤트를 앞 seq로, S4를 뒤 seq로 재배치(병렬 실행에서 가능한 순서)
    for e, seq in zip(s6 + s4, seqs):
        e["seq"] = seq
    model = _model(events, fx["partial"]["playbook"]["sections"], {"done": False})
    order = [c["step"] for c in model["cards"]]
    assert order.index("S4") < order.index("S6"), order


def test_rendered_card_strings_are_text_nodes_only(fx: dict) -> None:
    """XSS — 새 문자열(finding·because·phrase·opinion·note)의 마크업이 요소가 되지 않는다."""
    payload = "<img src=x onerror=alert(1)>"
    events = [dict(e) for e in fx["events"]]
    for e in events:
        for key in ("finding", "because", "phrase"):
            if key in e:
                e[key] = e[key] + payload
    events.append({"seq": 9999, "at": 1792399999, "engine": "holmes", "kind": "ai_note",
                   "label": "판단", "note": payload})
    partial = json.loads(json.dumps(fx["partial"]))
    for s in partial["playbook"]["sections"]:
        if isinstance(s.get("opinion"), dict):
            s["opinion"] = {"status": "ok", "text": payload, "basis": "이 단계까지의 근거"}
    from tests.test_api.test_ui_plan187_w20_flow import _flow_script

    out = _node(
        _flow_script(events, partial, extra="out = { imgs: tab.dom.flowBody.querySelectorAll ? "
                     "tab.dom.flowBody.querySelectorAll('img').length : -1, "
                     "text: tab.dom.flowBody.textContent };")
    )
    assert out["imgs"] in (0, -1)
    assert payload in out["text"]
