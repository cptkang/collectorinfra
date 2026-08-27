"""L-4 동일 호스트 중복 동시 조사 금지 (Plan 78 W2-6 · `docs/25` L-4).

## fingerprint dedup과 무엇이 다른가

    fingerprint dedup TTL : **같은 알람**의 재조사를 TTL로 억제한다
    L-4 in-flight 가드    : **서로 다른 알람이라도 같은 호스트**의 **동시** 조사를 막는다

부하는 곱해진다 — 이미 포화된 대상에 조사를 겹쳐 걸면 **조사가 장애를 악화시킨다.**
dedup만으로는 이 경로가 열려 있었다(2026-08-27 실측).

## 왜 직렬화가 아니라 거부인가

조사는 분 단위로 길다(실측 161s). submit을 붙들면 MCP 동기 타임아웃(60s)을 넘긴다.
거부하고 사유를 남기면 호출자가 진행 중인 조사의 브리핑을 받아 쓸 수 있다.
(본체 `process_query`는 API 호출이 짧아 락으로 직렬화한다 — 같은 요구, 다른 수단.)
"""

from __future__ import annotations

import threading
import time

import pytest

from sre_agent.application.investigation_dispatcher import (
    InvestigationDispatcher,
    _host_key,
)
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.application.briefing_builder import build_briefing
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


def alarm_job(iid: str, *, host="web-01", db_id="polestar_gimpo", fp=None) -> InvestigationJob:
    """알람 트리거 경로의 payload 형태 — `event.dbId` / `event.hostname`."""
    now = time.time()
    return InvestigationJob(
        investigation_id=iid, kind="alarm", status="running",
        created_at=now, updated_at=now, fingerprint=fp or iid,
        payload={"event": {"dbId": db_id, "hostname": host, "severity": 2},
                 "decision": {"tier": "PAGE"}},
    )


def diagnosis_job(iid: str, *, host="web-01", db_id="polestar_gimpo") -> InvestigationJob:
    """pull 진단 경로의 payload 형태 — `db_id` / `hostname`(형태가 다르다)."""
    now = time.time()
    return InvestigationJob(
        investigation_id=iid, kind="diagnosis", status="running",
        created_at=now, updated_at=now, fingerprint=None,
        payload={"server_name": None, "hostname": host, "db_id": db_id},
        question="원인 분석",
    )


def _ok_diagnose(job):
    return DiagnosisResult(
        answer="원인 ← journalctl",
        tool_outputs=[ToolCallRecord("t", "d", "success", "clean")],
    )


def _dispatcher(diagnose_fn=_ok_diagnose, **settings_over) -> InvestigationDispatcher:
    return InvestigationDispatcher(
        make_settings(**settings_over),
        diagnose_fn=diagnose_fn,
        briefing_fn=build_briefing,
        timeout_seconds=10.0,
    )


# ──────────────────────────────────────────────
# 키 추출 — 두 진입점 대칭
# ──────────────────────────────────────────────

def test_key_from_alarm_payload():
    assert _host_key(alarm_job("i1")) == ("polestar_gimpo", "web-01")


def test_key_from_diagnosis_payload():
    """★ payload 형태가 다르다 — 한쪽만 보면 그 경로에서 가드가 통째로 무력화된다."""
    assert _host_key(diagnosis_job("i2")) == ("polestar_gimpo", "web-01")


def test_both_entry_points_produce_the_same_key():
    """★ 대칭 — 같은 호스트면 어느 경로로 와도 같은 키여야 서로를 막는다(G5와 같은 원칙)."""
    assert _host_key(alarm_job("i1")) == _host_key(diagnosis_job("i2"))


def test_server_name_is_used_when_hostname_missing():
    """`hostname`이 없으면 `serverName`으로 대체한다(잔여 한계는 docstring에 명시)."""
    now = time.time()
    job = InvestigationJob(
        investigation_id="i", kind="alarm", status="running",
        created_at=now, updated_at=now, fingerprint="f",
        payload={"event": {"dbId": "db", "serverName": "웹서버01"}},
    )
    assert _host_key(job) == ("db", "웹서버01")


@pytest.mark.parametrize("payload", [None, {}, {"event": {}}, {"event": {"dbId": "db"}},
                                     {"hostname": "   "}])
def test_unidentifiable_target_is_not_guarded(payload):
    """대상을 식별할 수 없으면 부하 귀속이 불가하다 — 가드 대상이 아니다(막지 않는다)."""
    now = time.time()
    job = InvestigationJob(
        investigation_id="i", kind="alarm", status="running",
        created_at=now, updated_at=now, fingerprint="f", payload=payload,
    )
    assert _host_key(job) is None


# ──────────────────────────────────────────────
# 동시 조사 차단
# ──────────────────────────────────────────────

def test_same_host_concurrent_investigation_is_rejected():
    """★ L-4 핵심 — 같은 호스트를 조사 중이면 두 번째는 거부된다."""
    started = threading.Event()
    release = threading.Event()

    def _slow(job):
        started.set()
        release.wait(5)
        return _ok_diagnose(job)

    disp = _dispatcher(_slow)
    j1, j2 = alarm_job("i1"), alarm_job("i2")   # fingerprint가 다르다 = dedup으로는 안 막힌다
    disp(j1)
    assert started.wait(5)
    disp(j2)
    assert j2.status == "rejected"
    assert j2.reason == "host_investigation_in_flight"
    release.set()
    disp.wait_workers(5)
    assert j1.status == "done"


def test_different_hosts_run_concurrently():
    """다른 호스트는 막지 않는다 — L-4는 **같은 호스트**의 부하 중첩만 막는다."""
    disp = _dispatcher()
    j1, j2 = alarm_job("i1", host="web-01"), alarm_job("i2", host="web-02")
    disp(j1)
    disp(j2)
    disp.wait_workers(5)
    assert j1.status == "done" and j2.status == "done"


def test_dedup_alone_would_not_have_blocked_it():
    """★ 이 가드가 왜 별도인가 — **fingerprint가 다르면 dedup은 통과**시킨다.

    서로 다른 알람(CPU·메모리)이 같은 호스트를 가리키는 것은 흔하고, 그때 부하가 곱해진다.
    """
    started, release = threading.Event(), threading.Event()

    def _slow(job):
        started.set()
        release.wait(5)
        return _ok_diagnose(job)

    disp = _dispatcher(_slow, investigation_dedup_ttl_seconds=3600)
    j1 = alarm_job("i1", fp="cpu-alarm")
    j2 = alarm_job("i2", fp="mem-alarm")      # 지문이 다르므로 dedup은 통과
    disp(j1)
    assert started.wait(5)
    disp(j2)
    assert j2.reason == "host_investigation_in_flight"   # dedup이 아니라 L-4가 막았다
    release.set()
    disp.wait_workers(5)


def test_cross_entry_point_blocking():
    """★ 알람 조사 중이면 pull 진단도 같은 호스트를 못 잡는다(경로가 달라도 부하는 같다)."""
    started, release = threading.Event(), threading.Event()

    def _slow(job):
        started.set()
        release.wait(5)
        return _ok_diagnose(job)

    disp = _dispatcher(_slow)
    disp(alarm_job("i1"))
    assert started.wait(5)
    j2 = diagnosis_job("i2")
    disp(j2)
    assert j2.reason == "host_investigation_in_flight"
    release.set()
    disp.wait_workers(5)


# ──────────────────────────────────────────────
# 해제 — 누락하면 그 호스트가 영구히 조사 불가가 된다
# ──────────────────────────────────────────────

def test_key_is_released_after_completion():
    """★ 조사가 끝나면 같은 호스트를 다시 조사할 수 있다."""
    disp = _dispatcher()
    disp(alarm_job("i1"))
    disp.wait_workers(5)
    j2 = alarm_job("i2")
    disp(j2)
    disp.wait_workers(5)
    assert j2.status == "done"


def test_key_is_released_after_worker_failure():
    """워커가 터져도 키가 남지 않는다 — 남으면 그 호스트가 **영구히 조사 불가**가 된다."""
    def _boom(job):
        raise RuntimeError("조사 중 예외")

    disp = _dispatcher(_boom)
    j1 = alarm_job("i1")
    disp(j1)
    disp.wait_workers(5)
    assert j1.status == "failed"
    j2 = alarm_job("i2")
    disp(j2)
    disp.wait_workers(5)
    assert j2.status != "rejected"


def test_key_is_released_on_stub_path():
    """★ 스텁 경로는 워커가 돌지 않는다 — 여기서 풀지 않으면 키가 영구히 남는다."""
    disp = InvestigationDispatcher(
        make_settings(gemini_api_key=None),   # 키 부재 → 스텁 확정
        diagnose_fn=_ok_diagnose,
        briefing_fn=build_briefing,
    )
    j1 = alarm_job("i1")
    disp(j1)
    assert j1.status == "stub"
    j2 = alarm_job("i2")
    disp(j2)
    assert j2.status == "stub"                # 두 번째도 스텁 — 거부가 아니다


def test_release_only_frees_its_own_key():
    """★ 방어적 축출 뒤 다른 조사가 같은 키를 잡았을 수 있다 — **남의 가드를 풀지 않는다**."""
    disp = _dispatcher()
    key = ("polestar_gimpo", "web-01")
    disp._inflight_hosts[key] = ("남의-조사", 0.0)
    disp._release_host(alarm_job("내-조사"))
    assert disp._inflight_hosts[key][0] == "남의-조사"


def test_stale_key_is_swept():
    """워커 유실로 남은 키를 방어적으로 축출한다 — 없으면 그 호스트가 영구히 조사 불가다."""
    now = [0.0]
    disp = InvestigationDispatcher(
        make_settings(), diagnose_fn=_ok_diagnose, briefing_fn=build_briefing,
        timeout_seconds=10.0, clock=lambda: now[0],
    )
    disp._inflight_hosts[("polestar_gimpo", "web-01")] = ("유실된-조사", 0.0)
    now[0] = 10.0 * 2 + 1                      # timeout*2 초과
    j = alarm_job("i2")
    disp(j)
    disp.wait_workers(5)
    assert j.status == "done"
