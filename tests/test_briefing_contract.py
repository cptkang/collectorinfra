"""조사 브리핑 키 계약 정합 (plans/50 G6 · SPEC-briefing-contract · D-194).

생산자(`sre_agent` briefing_builder.build_briefing)가 내는 키가 정본이고, 두 소비자
(챗 `_briefing_to_text` · WorkB `_investigation_briefing_html`)는 같은 domain 렌더러를 쓴다.

`sre_agent`는 루트 venv에서 import되지 않는다(별도 venv · 양방향 import 0 — D-118). 그래서
생산자 산출 형태를 아래 `PRODUCER_BRIEFING` 리터럴로 고정하고, **같은 키 집합**을
`sre_agent/tests/test_briefing_builder.py::test_output_keys_match_consumer_contract`가
생산자 쪽에서 단언한다(대칭 계약 테스트 — 한쪽이 키를 늘리면 양쪽이 함께 실패한다).
"""

from __future__ import annotations

import html as html_mod
import re

from noise_gate.application.nodes.alarm_notifier import _investigation_briefing_html
from noise_gate.domain.investigation_briefing import (
    BRIEFING_CONTRACT_KEYS,
    render_briefing_lines,
)
from src.nodes.fault_diagnosis import _briefing_to_text, _extract_diagnosis_text

# build_briefing()이 실제로 내는 형태(2026-09-02 실측 재현 · probe).
PRODUCER_BRIEFING = {
    "severity": {
        "level": "심각", "confidence": "high", "escalate": True,
        "gate_tier": "PAGE", "signals": ["oom_kill"], "evidence_insufficient": False,
    },
    "summary": "web-01 메모리 고갈 ← metric_trend",
    "timeline": ["14:03 Mem 78%→95% ← metric_trend", "14:06 OOM ← 근거 dmesg"],
    "bottleneck": "메모리 포화",
    "cause": "14:06 OOM ← 근거 dmesg",
    "root_cause_hypotheses": [
        {"rank": 1, "cause": "disk_io sustained 이상이 첫 알람에 5분 선행", "confidence": "high",
         "evidence": ["T-15m 메트릭 disk_io sustained 시작", "T-10m 알람 [severity 3] CPU"],
         "reasoning": "상관 ≠ 인과"},
    ],
    "recommendation": {"items": ["힙 상향 재기동"], "note": "※ 실행은 운영자 승인 후 수동"},
    "limitations": ["프로세스·자원 스냅샷은 조사 시점 단면일 수 있음"],
    "citations_verified": True,
    "hypotheses": ["트래픽 증가가 원인으로 보임"],
}

# sre_agent/tests/test_briefing_builder.py 와 **같은 리터럴**이어야 한다.
CONTRACT_KEYS = frozenset({
    "severity", "summary", "timeline", "bottleneck", "cause", "root_cause_hypotheses",
    "recommendation", "limitations", "citations_verified", "hypotheses",
})

STUB = {"stub": True, "message": "조사 미실행 — LLM 키 부재(스텁)", "elements": None}


def _pull() -> str:
    return _briefing_to_text(dict(PRODUCER_BRIEFING))


def _push() -> str:
    return _investigation_briefing_html(dict(PRODUCER_BRIEFING))


def _push_text() -> str:
    return html_mod.unescape(_push().replace("<br>", "\n"))


def _pull_labels(text: str) -> list[str]:
    return re.findall(r"^\[([^\]]+)\]", text, re.M)


def _push_labels(h: str) -> list[str]:
    return re.findall(r"<b>([^<:]+):</b>", h)


# ── 계약 ─────────────────────────────────────────────────────────


def test_contract_keys_covered():
    """렌더러가 인지하는 키 == 생산자 산출 키(대칭 리터럴)."""
    assert BRIEFING_CONTRACT_KEYS == CONTRACT_KEYS
    assert set(PRODUCER_BRIEFING) == CONTRACT_KEYS


# ── 도달 ─────────────────────────────────────────────────────────


def test_limitations_reach_user():
    for out in (_pull(), _push_text()):
        assert "한계" in out
        assert "조사 시점 단면일 수 있음" in out


def test_hypotheses_reach_user():
    for out in (_pull(), _push_text()):
        assert "가설" in out
        assert "트래픽 증가가 원인으로 보임" in out


def test_severity_reaches_user():
    for out in (_pull(), _push_text()):
        assert "중요도" in out
        assert "심각" in out and "PAGE" in out and "oom_kill" in out


def test_summary_is_first_content_after_severity():
    labels = _pull_labels(_pull())
    assert labels[:2] == ["중요도", "요약"]


# ── repr · 영문 키 · 메타 ──────────────────────────────────────────


def test_no_repr_leak():
    for out in (_pull(), _push()):
        assert "{'" not in out and "['" not in out and "&#x27;" not in out


def test_metadata_and_english_keys_not_exposed():
    for out in (_pull(), _push_text()):
        assert "citations_verified" not in out
        assert "summary" not in out and "limitations" not in out


def test_recommendation_dict_renders_items_and_note():
    text = _pull()
    assert "[권고] 힙 상향 재기동" in text
    assert "※ 실행은 운영자 승인 후 수동" in text


# ── 재발 방지 규칙 ────────────────────────────────────────────────


def test_unknown_key_not_dropped():
    """순서 목록에 없는 키는 버리지 않고 말미에 원 키명으로 렌더한다(list·dict 포함)."""
    b = {"cause": "c", "further_investigation": ["h1 (0.8)"], "extra_scalar": 3}
    labels = [lab for lab, _ in render_briefing_lines(b)]
    assert labels == ["원인", "extra_scalar", "further_investigation"]
    assert "h1 (0.8)" in _briefing_to_text(b)
    assert "h1 (0.8)" in _investigation_briefing_html(b)


def test_empty_values_omitted():
    b = {"summary": "", "timeline": [], "recommendation": {}, "hypotheses": None, "cause": "c"}
    assert render_briefing_lines(b) == [("원인", "c")]


def test_stub_briefing_unchanged():
    """스텁 경로는 현행과 비트 동일(회귀 0)."""
    assert _briefing_to_text(dict(STUB)) == "조사 미실행 — LLM 키 부재(스텁)"
    assert _investigation_briefing_html(dict(STUB)) == (
        "<br><br><b>조사 브리핑 (자동 조사)</b><br>조사 미실행 — LLM 키 부재(스텁)"
    )


def test_push_pull_symmetry():
    """같은 briefing → 두 경로의 라벨 집합·순서가 같다."""
    assert _pull_labels(_pull()) == _push_labels(_push())
    assert _pull_labels(_pull()) == [
        "중요도", "요약", "타임라인", "병목", "원인", "원인 가설", "권고", "한계", "가설",
    ]


def test_hypotheses_render_as_ranked_lines_without_repr():
    """§7.2 가설(list[dict])은 `1) (high) 원인 — 근거: …`로 펴진다."""
    for out in (_pull(), _push_text()):
        assert "1) (high) disk_io sustained 이상이 첫 알람에 5분 선행 — 근거: T-15m 메트릭 disk_io sustained 시작; T-10m 알람 [severity 3] CPU" in out
    assert "{" not in _pull().split("[원인 가설]")[1].split("[권고]")[0]


def test_push_escapes_html_in_values():
    h = _investigation_briefing_html({"cause": "<b>x</b> & y"})
    assert "<b>x</b>" not in h and "&lt;b&gt;x&lt;/b&gt; &amp; y" in h


def test_append_extras_uses_contract_key():
    """자연어 답변에 덧붙는 한계도 정본 키(`limitations`)로 읽는다."""
    out = _extract_diagnosis_text({"answer": "원인입니다.", "briefing": {"limitations": ["샘플 1회"]}})
    assert "[한계] 샘플 1회" in out
