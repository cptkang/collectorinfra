"""plans/213 W4 — 교정 2건 (D-366 부기 1).

① 심층 섹션 `retryable` 표지: 다시 돌리면 결과가 달라질 수 있는 생략(시간 예산 부족 · 형식 불일치)만
   True, 모드 off·트리거 미충족·툴셋 연결 실패·정상 완료는 키 없음.
② Harmony 재시도 직전 취소 확인: 조사 루프 스레드의 취소 신호(D-347)가 서 있으면 재시도하지 않는다.

실 LLM 0 — 가짜 caller·가짜 심층·가짜 `completion`.
"""

from __future__ import annotations

import inspect
import logging
import threading
from typing import Any

import pytest
from holmes.core.llm import DefaultLLM
from sre_agent.application import investigation_dispatcher as disp
from sre_agent.infrastructure import harmony_retry_guard as hr
from sre_agent.infrastructure.harmony_retry_guard import (
    harmony_cancel_scope,
    install_harmony_retry_guard,
)
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeDeep, alarm_of, build, make_settings, run_job
from test_plan188_w4_markup import SCREEN_RAW
from test_plan213_w3_harmony_retry import MSGS, TOOLS, _Base, _Clock, _harmony_400, _resp, _use

# ── ① retryable 표지 ─────────────────────────────────────────────────────


def _deep_of(job: Any) -> dict:
    return job.briefing["deep_dive"]


def test_malformed_skip_is_retryable():
    settings = make_settings(sre_deep_dive_mode="parallel")
    job = run_job(build(settings, FakeCaller(_load("latency_sql")),
                        deep=FakeDeep(settings, answer=SCREEN_RAW)),
                  alarm_of("latency_sql"))
    dd = _deep_of(job)
    assert dd["status"] == "skipped" and dd["retryable"] is True


def test_time_budget_skip_is_retryable(monkeypatch):
    monkeypatch.setattr(disp, "DEEP_MIN_REMAINING_S", 10**9)  # 남은 시간이 늘 하한 미만
    settings = make_settings(sre_deep_dive_mode="conditional")
    deep = FakeDeep(settings)
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep),
                  alarm_of("latency_sql"))  # 1순위 판단 보류 → 트리거 충족
    dd = _deep_of(job)
    assert deep.jobs == []
    assert dd["status"] == "skipped" and "시간 예산" in dd["reason"]
    assert dd["retryable"] is True


def test_not_triggered_skip_has_no_retryable():
    settings = make_settings(sre_deep_dive_mode="conditional")
    job = run_job(build(settings, FakeCaller(_load("latency_external")),
                        deep=FakeDeep(settings)),
                  alarm_of("latency_external"))
    dd = _deep_of(job)
    assert dd["status"] == "skipped" and "트리거 미충족" in dd["reason"]
    assert "retryable" not in dd


def test_mode_off_skip_has_no_retryable():
    job = run_job(build(make_settings(), FakeCaller(_load("latency_sql"))),
                  alarm_of("latency_sql"))
    dd = _deep_of(job)
    assert dd["status"] == "skipped" and "retryable" not in dd


def test_tools_unavailable_skip_has_no_retryable():
    settings = make_settings(sre_deep_dive_mode="parallel")

    class Unavailable(FakeDeep):
        def __call__(self, job):  # noqa: ANN001, ANN204
            r = super().__call__(job)
            r.tools_unavailable = "조사 불가 — 관측 도구 연결 실패"
            return r

    job = run_job(build(settings, FakeCaller(_load("latency_sql")),
                        deep=Unavailable(settings, answer=SCREEN_RAW)),
                  alarm_of("latency_sql"))
    dd = _deep_of(job)
    assert dd["status"] == "skipped" and "retryable" not in dd


def test_done_has_no_retryable():
    settings = make_settings(sre_deep_dive_mode="parallel")
    job = run_job(build(settings, FakeCaller(_load("latency_sql")),
                        deep=FakeDeep(settings, answer="심층 서술 ← apm_resource_pool")),
                  alarm_of("latency_sql"))
    dd = _deep_of(job)
    assert dd["status"] == "done" and "retryable" not in dd


# ── ② 재시도 직전 취소 확인 ──────────────────────────────────────────────


@pytest.fixture
def clean_completion():
    saved = DefaultLLM.__dict__["completion"]
    DefaultLLM.completion = inspect.unwrap(saved)  # type: ignore[method-assign]
    yield DefaultLLM
    DefaultLLM.completion = saved  # type: ignore[method-assign]


def test_cancel_set_skips_retry(clean_completion, caplog):
    base = _Base([_harmony_400(), _resp()])
    _use(clean_completion, base)
    assert install_harmony_retry_guard(1, 45.0, clock=_Clock(0.0, 1.0)) is True
    cancel = threading.Event()
    cancel.set()
    with caplog.at_level(logging.INFO, logger=hr.__name__), harmony_cancel_scope(cancel):
        with pytest.raises(Exception, match="expecting start token"):
            clean_completion.completion(None, MSGS, TOOLS, "auto")
    assert len(base.calls) == 1
    assert any("조사 취소 신호" in r.getMessage() for r in caplog.records)


def test_cancel_not_set_still_retries(clean_completion):
    ok = _resp()
    base = _Base([_harmony_400(), ok])
    _use(clean_completion, base)
    install_harmony_retry_guard(1, 45.0, clock=_Clock(0.0, 1.0))
    with harmony_cancel_scope(threading.Event()):
        assert clean_completion.completion(None, MSGS, TOOLS, "auto") is ok
    assert len(base.calls) == 2


def test_cancel_scope_is_thread_local_and_restored():
    outer, inner = threading.Event(), threading.Event()
    inner.set()
    seen: list[bool] = []
    with harmony_cancel_scope(outer):
        with harmony_cancel_scope(inner):
            assert hr._cancelled() is True
            t = threading.Thread(target=lambda: seen.append(hr._cancelled()))
            t.start()
            t.join()
        assert hr._cancelled() is False  # 바깥 신호(안 섬)로 되돌림
    assert hr._cancelled() is False
    assert seen == [False]  # 다른 스레드에는 걸리지 않는다


def test_none_scope_is_noop():
    with harmony_cancel_scope(None):
        assert hr._cancelled() is False


def test_stream_drain_run_binds_cancel_scope(monkeypatch):
    """`_StreamDrain.run`이 자기 취소 신호를 범위로 건 채 스트림을 돈다."""
    from sre_agent import diagnosis

    cancel = threading.Event()
    cancel.set()
    seen: list[bool] = []

    def fake_stream(self, messages):  # noqa: ANN001, ANN202
        seen.append(hr._cancelled())
        return "result"

    monkeypatch.setattr(diagnosis._StreamDrain, "_run_stream", fake_stream)
    drain = diagnosis._StreamDrain.__new__(diagnosis._StreamDrain)
    drain._cancel = cancel
    assert drain.run([]) == "result"
    assert seen == [True] and hr._cancelled() is False
