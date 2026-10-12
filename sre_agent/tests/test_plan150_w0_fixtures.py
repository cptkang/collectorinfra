"""plans/150 W0 — sre_agent 측 픽스처 모양 고정(브리핑 6요소 목업).

`tests/fixtures/plan150/briefing_mock.json`은 크로스소스 사건(폴스타 CPU PAGE → 270초 뒤
같은 호스트 제니퍼 SERVICE_QUEUING)의 브리핑 목업이다. W5(`cross_source` 블록)·W6(웹 표시)이
이 모양에서 출발한다. noise_gate 쪽 사본(`noise_gate/testdata/plan150/briefing_mock.json`)과
내용이 같지만 경계(D-118) 때문에 서로 읽지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from sre_agent.application.briefing_builder import BRIEFING_ELEMENTS, HUMAN_GATED_NOTE

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plan150"

# 본체 `noise_gate/domain/investigation_briefing.BRIEFING_CONTRACT_KEYS`와 같은 리터럴
# (양방향 import 0).
CONTRACT_KEYS = {
    "severity", "summary", "timeline", "bottleneck", "cause", "root_cause_hypotheses",
    "recommendation", "limitations", "citations_verified", "hypotheses",
}


def _mock() -> dict:
    return json.loads((FIXTURES / "briefing_mock.json").read_text(encoding="utf-8"))


def test_briefing_mock_has_contract_keys():
    assert set(_mock()) == CONTRACT_KEYS


def test_briefing_mock_six_elements_non_empty():
    mock = _mock()
    assert set(BRIEFING_ELEMENTS) <= set(mock)
    assert all(mock[key] for key in BRIEFING_ELEMENTS)
    assert mock["recommendation"]["note"] == HUMAN_GATED_NOTE
    assert mock["severity"]["gate_tier"] == "page"
