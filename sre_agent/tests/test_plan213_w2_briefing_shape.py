"""plans/213 W2(D-366 ②) 짝 테스트 — 플레이북 브리핑의 심층 섹션 상태 키 경로·값 집합.

본체 `src/api/routes/alarm.py`의 `_DEEP_DIVE_KEY = "deep_dive"`·`_DEEP_DIVE_STATUS_KEY = "status"`·
`_DEEP_DIVE_RETRYABLE = {"failed", "timeout", "skipped"}`과 **같은 리터럴**이다(양방향 import 0 ·
D-139 — 본체는 저장된 JSON만 읽는다). 이 테스트가 깨지면 본체 상수와 본체 테스트
`tests/test_plan213_w2_join_exception.py`의 픽스처를 함께 고친다.

실 LLM 0 — 가짜 HolmesGPT(`FakeDeep`)·가짜 폴스타 호출기만 쓴다.
"""

from __future__ import annotations

import json

from sre_agent.application.investigation_dispatcher import DEEP_DIVE_LABEL
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeDeep, alarm_of, build, make_settings, run_job
from test_plan188_w4_markup import SCREEN_RAW

# 본체 상수와 같은 리터럴(주석으로만 연결 — import 금지).
DEEP_DIVE_KEY = "deep_dive"
DEEP_DIVE_STATUS_KEY = "status"
RETRYABLE = frozenset({"failed", "timeout", "skipped"})


def _wire(job) -> dict:  # noqa: ANN001
    """소비자(본체)가 받는 모양 — `summary()`를 JSON 왕복한 dict(MCP 응답 본문)."""
    return json.loads(json.dumps(job.summary(), ensure_ascii=False, default=str))


def _run(settings, deep=None):  # noqa: ANN001, ANN202
    return run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep),
                   alarm_of("latency_sql"))


def _deep(wire: dict) -> dict:
    briefing = wire["briefing"]
    assert isinstance(briefing, dict)  # 문자열 JSON이 아니라 dict로 실린다
    return briefing[DEEP_DIVE_KEY]


def test_failed_deep_dive_path_and_value():
    settings = make_settings(sre_deep_dive_mode="parallel")
    wire = _wire(_run(settings, FakeDeep(settings, error=RuntimeError("holmes 400"))))
    assert wire["status"] == "done"  # 조사 상태는 done(dispatcher 플레이북 종결)
    dd = _deep(wire)
    assert dd[DEEP_DIVE_STATUS_KEY] == "failed" and dd[DEEP_DIVE_STATUS_KEY] in RETRYABLE
    assert set(dd) == {"status", "mode", "reason", "label"}
    assert dd["label"] == DEEP_DIVE_LABEL


def test_skipped_deep_dive_markup_and_mode_off():
    settings = make_settings(sre_deep_dive_mode="parallel")
    dd = _deep(_wire(_run(settings, FakeDeep(settings, answer=SCREEN_RAW))))
    assert dd[DEEP_DIVE_STATUS_KEY] == "skipped"
    off = _deep(_wire(_run(make_settings())))
    assert off[DEEP_DIVE_STATUS_KEY] == "skipped"


def test_done_and_incomplete_are_status_done():
    """`incomplete`는 상태가 아니라 별도 불리언이다(본체는 합류 — 사용자 결정 2026-11-04)."""
    settings = make_settings(sre_deep_dive_mode="parallel")
    dd = _deep(_wire(_run(settings, FakeDeep(settings))))
    assert dd[DEEP_DIVE_STATUS_KEY] == "done" and dd["incomplete"] is False
    assert dd[DEEP_DIVE_STATUS_KEY] not in RETRYABLE


def test_timeout_status_literal_passes_through_section():
    """심층 대기 타임아웃은 `_deep_section`에 상태 "timeout"으로 들어가 그대로 실린다."""
    settings = make_settings(sre_deep_dive_mode="parallel")
    d = build(settings, FakeCaller(_load("latency_sql")))
    out = d._deep_section("parallel", "timeout", "심층 조사 타임아웃 — 전체 예산", None,
                          verdict=None, gate_tier=None)  # type: ignore[arg-type]
    assert out == {"status": "timeout", "mode": "parallel", "reason": "심층 조사 타임아웃 — 전체 예산",
                   "label": DEEP_DIVE_LABEL}
    assert out[DEEP_DIVE_STATUS_KEY] in RETRYABLE
