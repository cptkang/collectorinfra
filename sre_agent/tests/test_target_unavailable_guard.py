"""대상 가용성 가드 — 죽은 호스트에 조사 예산을 쓰지 않는다 (Plan 81 · `docs/25` L-5).

## 왜 sre_agent에도 필요한가

본체 게이트는 **본체를 경유하는 조사만** 막는다. 알람 자동 조사 등 다른 진입점은 그대로
들어온다 — L-4와 같은 구조다(`docs/25`).

## 왜 에러가 아니라 낭비인가

원격 배치(`remote_vm_profile`)는 bash를 확장하지 않는다. 전원이 꺼진 호스트에 대해 MCP
도구들은 **에러가 아니라 빈 결과**를 돌려주므로, ReAct 루프는 "도구를 더 불러보자"로 반응해
전체 타임아웃(기본 300s)까지 돌 수 있다(조사 1건 실측 161s).

## fail-open인 이유

이 가드는 보안 통제가 아니다. 호출자가 `target_state`를 싣지 않으면 **통과**시킨다 —
정보가 없을 때 막으면 정상 조사를 잃는다(인가 가드의 fail-closed와 정반대).
"""

from __future__ import annotations

import time

import pytest

from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import (
    GUARD_TARGET_UNAVAILABLE,
    InvestigationDispatcher,
    _target_state,
)
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.settings import AgentSettings


def make_settings(**over) -> AgentSettings:
    base = dict(
        _env_file=None, model="test/model", gemini_api_key="k",
        investigation_max_concurrent=4, investigation_dedup_ttl_seconds=None,
        investigation_hourly_budget=None,
    )
    base.update(over)
    return AgentSettings(**base)


_DOWN = {"state": "unavailable", "reason": "avail_status_down",
         "as_of": "2026-08-28 09:00:00", "evidence": {"avail_status": 1}}
_UP = {"state": "available", "reason": "ok", "as_of": "2026-08-28 09:00:00"}
_UNKNOWN = {"state": "unknown", "reason": "lookup_failed", "as_of": None}


def alarm_job(iid="i1", *, target_state=None) -> InvestigationJob:
    """알람 트리거 경로 — `payload.meta.target_state`."""
    now = time.time()
    return InvestigationJob(
        investigation_id=iid, kind="alarm", status="running",
        created_at=now, updated_at=now, fingerprint=iid,
        payload={
            "event": {"dbId": "polestar_gimpo", "hostname": "web-01", "severity": 2},
            "decision": {"tier": "PAGE"},
            "meta": {"target_state": target_state},
        },
    )


def diagnosis_job(iid="i2", *, target_state=None) -> InvestigationJob:
    """pull 진단 경로 — `payload.target_state`(형태가 다르다)."""
    now = time.time()
    return InvestigationJob(
        investigation_id=iid, kind="diagnosis", status="running",
        created_at=now, updated_at=now, fingerprint=None,
        payload={"server_name": None, "hostname": "web-01",
                 "db_id": "polestar_gimpo", "target_state": target_state},
        question="원인 분석",
    )


_calls: list[str] = []


def _ok_diagnose(job):
    _calls.append(job.investigation_id)
    return DiagnosisResult(
        answer="원인 ← journalctl",
        tool_outputs=[ToolCallRecord("t", "d", "success", "clean")],
    )


def _dispatcher(**settings_over) -> InvestigationDispatcher:
    return InvestigationDispatcher(
        make_settings(**settings_over),
        diagnose_fn=_ok_diagnose,
        briefing_fn=build_briefing,
        timeout_seconds=10.0,
    )


@pytest.fixture(autouse=True)
def _reset():
    _calls.clear()
    yield
    _calls.clear()


# ──────────────────────────────────────────────
# 추출 — 두 진입점 대칭 (한쪽만 보면 그 경로에서 무력화된다)
# ──────────────────────────────────────────────

def test_알람_페이로드에서_판정을_읽는다():
    assert _target_state(alarm_job(target_state=_DOWN)) == _DOWN


def test_진단_페이로드에서_판정을_읽는다():
    assert _target_state(diagnosis_job(target_state=_DOWN)) == _DOWN


@pytest.mark.parametrize("payload", [None, {}, {"meta": {}}, {"meta": {"target_state": None}},
                                     {"target_state": {}}, {"target_state": "down"}])
def test_판정이_없으면_None이다(payload):
    now = time.time()
    job = InvestigationJob(
        investigation_id="i", kind="alarm", status="running",
        created_at=now, updated_at=now, fingerprint="f", payload=payload,
    )
    assert _target_state(job) is None


# ──────────────────────────────────────────────
# 가드 동작
# ──────────────────────────────────────────────

def test_비정상_대상은_조사하지_않는다():
    job = alarm_job(target_state=_DOWN)
    _dispatcher()(job)
    assert job.status == "rejected"
    assert job.reason == GUARD_TARGET_UNAVAILABLE
    assert _calls == [], "조사 함수가 호출되면 예산을 이미 쓴 것이다"


def test_진단_경로도_같이_막힌다():
    job = diagnosis_job(target_state=_DOWN)
    _dispatcher()(job)
    assert job.reason == GUARD_TARGET_UNAVAILABLE


def test_거부_문구에_사실이_담긴다():
    """G-2 확정 — 거부하되 왜 조사하지 않았는지가 호출자에게 전달돼야 한다."""
    job = alarm_job(target_state=_DOWN)
    _dispatcher()(job)
    assert "가용하지 않습니다" in job.verdict
    assert "2026-08-28 09:00:00" in job.verdict
    assert "avail_status_down" in job.verdict
    assert job.briefing["message"] == job.verdict


def test_정상_대상은_그대로_조사한다():
    job = alarm_job(target_state=_UP)
    _dispatcher()(job)
    assert job.reason != GUARD_TARGET_UNAVAILABLE


def test_판정_불가는_막지_않는다():
    """fail-open — 정보가 없을 때 막으면 정상 조사를 잃는다."""
    job = alarm_job(target_state=_UNKNOWN)
    _dispatcher()(job)
    assert job.reason != GUARD_TARGET_UNAVAILABLE


def test_필드가_없으면_종전대로_통과한다():
    """구버전 호출자 호환 — 계약 확장이 기존 경로를 깨지 않는다."""
    job = alarm_job(target_state=None)
    _dispatcher()(job)
    assert job.reason != GUARD_TARGET_UNAVAILABLE


# ──────────────────────────────────────────────
# 다른 가드와의 상호작용
# ──────────────────────────────────────────────

def test_거부된_조사는_inflight_슬롯을_잡지_않는다():
    """가용성 거부가 in-flight 키를 잡으면 그 호스트가 조사 불가로 굳는다."""
    d = _dispatcher()
    d(alarm_job("i1", target_state=_DOWN))
    assert d._inflight_hosts == {}


def test_거부된_조사는_시간당_예산을_쓰지_않는다():
    d = _dispatcher(investigation_hourly_budget=1)
    d(alarm_job("i1", target_state=_DOWN))
    # 예산을 썼다면 다음 정상 조사가 예산 초과로 막힌다
    job2 = alarm_job("i2", target_state=_UP)
    d(job2)
    assert job2.reason != "hourly_budget_exceeded"


def test_거부된_조사는_dedup_슬롯을_쓰지_않는다():
    d = _dispatcher(investigation_dedup_ttl_seconds=300)
    d(alarm_job("i1", target_state=_DOWN))
    # 같은 지문이 회복 후 다시 오면 조사돼야 한다
    job2 = InvestigationJob(
        investigation_id="i2", kind="alarm", status="running",
        created_at=time.time(), updated_at=time.time(), fingerprint="i1",
        payload={"event": {"dbId": "polestar_gimpo", "hostname": "web-01", "severity": 2},
                 "meta": {"target_state": _UP}},
    )
    d(job2)
    assert job2.reason != "dedup_ttl_active"


def test_다른_거부_사유의_문구는_종전_그대로다():
    """회귀 0 — 가용성 외 사유는 문구를 바꾸지 않는다."""
    d = _dispatcher(investigation_hourly_budget=0)
    job = alarm_job("i1", target_state=_UP)
    d(job)
    assert job.verdict == "조사 거부 — hourly_budget_exceeded"
