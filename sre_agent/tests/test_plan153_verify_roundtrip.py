"""plans/153 검증(verifier) — noise_gate 실 출력 페이로드를 sre_agent 수신 측이 해석하는 왕복.

입력은 `tests/fixtures/plan153/noise_gate_episode_payloads.json` — noise_gate 실 워커가
`cross_source_incident.jsonl`(폴스타 CPU PAGE → 270초 뒤 같은 호스트 제니퍼 큐잉)에서 만든 제출
페이로드 3건(폴스타 단독 · 제니퍼 단독 · 사건 종합)이다. 생산자 쪽 고정은
`noise_gate/tests/test_plan153_verify_contract.py`가 같은 파일로 한다(import 0 · D-118).

`occurred_at`은 epoch 초(생산 머신 로컬 시각 규약)라 시간대 중립으로 비교한다.

`TestCompositeInvestigationDefects` 2건은 검증에서 찾은 결함을 「기대 동작」으로 적은 것이다 —
W5 교정(가드 키 kind 축 · 종합 조사 기준시각 = 멤버 최댓값, 둘 다 플래그 on에서만)으로 고쳐져
xfail 표지를 걷었다.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from sre_agent.application import evidence_prefetch
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob, JobStore
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.domain import cross_source_episode as cse
from sre_agent.domain.correlation import offset_minutes
from sre_agent.domain.incident_scope import alarm_time_to_iso
from sre_agent.domain.severity_signatures import ImportanceVerdict
from sre_agent.settings import AgentSettings

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "plan153" / "noise_gate_episode_payloads.json"
)
P = json.loads(FIXTURE.read_text(encoding="utf-8"))
CPU_ID = "P150-CPU-1"
J_ID = "jennifer:p150q0000000001"


def _ref(payload: dict) -> str | None:
    return alarm_time_to_iso(payload["event"]["alarmTime"])


def _settings(**kw) -> AgentSettings:
    base = {
        "_env_file": None, "model": "test/model", "api_key": None, "gemini_api_key": "k",
        "service_bearer_token": None, "investigation_max_concurrent": 4,
        "investigation_dedup_ttl_seconds": None, "investigation_hourly_budget": None,
    }
    return AgentSettings(**{**base, **kw})


def _job(iid: str, payload: dict) -> InvestigationJob:
    now = time.time()
    job = InvestigationJob(
        investigation_id=iid, kind="alarm", status="running", created_at=now, updated_at=now,
        fingerprint=payload["decision"]["fingerprint"], payload=payload,
    )
    job.reference_time = _ref(payload)
    job.lookback_minutes = 60
    return job


class TestRoundTripParse:
    def test_composite_parses_as_mixed_episode(self):
        comp = P["episode_composite"]
        ctx = cse.active_episode(comp, True, _ref(comp))
        assert ctx is not None and ctx.is_mixed
        assert ctx.investigation_kind == "episode"
        assert ctx.episode_id == comp["meta"]["episode_id"]
        assert ctx.host_key == "xsapp01" and ctx.zone == "gongjon"
        # 최근 순(제니퍼 → 폴스타) 그대로 · 절단 노트 없음
        assert [m.alarm_id for m in ctx.members] == [J_ID, CPU_ID]
        assert ctx.notes == ()
        # 링크 끝점·규칙·lag가 맞물린다(effect_alarm_id 해석)
        (lk,) = ctx.valid_links
        assert (lk.cause_alarm_id, lk.effect_alarm_id) == (CPU_ID, J_ID)
        assert ctx.link_label(lk.cause_alarm_id) == "CPU 사용률"
        assert ctx.link_label(lk.effect_alarm_id) == "SERVICE_QUEUING"
        assert lk.lag_seconds == 270.0
        assert [r.id for r in ctx.rules] == [lk.rule_id]
        assert ctx.infra_db_ids == ("polestar_cm_gp",)
        # 멤버 trait — 인프라 kind · 애플리케이션 was_kind(송신 값 그대로)
        assert ctx.member(CPU_ID).trait == "cpu"
        assert ctx.member(J_ID).trait == "was_service_queuing"

    def test_occurred_at_epoch_round_trips_timezone_neutral(self):
        comp = P["episode_composite"]
        raw = {m["alarm_id"]: m["occurred_at"] for m in comp["meta"]["episode"]["members"]}
        ctx = cse.episode_from_payload(comp)
        for m in ctx.members:
            assert m.occurred_at == datetime.fromtimestamp(raw[m.alarm_id]).isoformat()
        gap = datetime.fromisoformat(ctx.member(J_ID).occurred_at) - datetime.fromisoformat(
            ctx.member(CPU_ID).occurred_at
        )
        assert gap.total_seconds() == 270.0

    def test_single_source_snapshot_is_not_cross_source(self):
        cpu = P["polestar_cpu_single"]
        assert cse.episode_from_payload(cpu) is not None
        assert cse.active_episode(cpu, True, _ref(cpu)) is None

    def test_apm_individual_prefetch_source_from_infra_db_ids(self):
        apm = P["jennifer_queuing_individual"]
        job = _job("i-apm", apm)
        on = evidence_prefetch.scope_from_job(job, cross_source=True)
        assert on is not None and on.source == "polestar_cm_gp" and not on.skip_infra
        assert {k for k, _ in evidence_prefetch.build_calls(on)} >= {"polestar_incident_alarms"}
        # R-4 — 플래그 off면 제니퍼 dbId를 폴스타 도구에 넘기지 않고 축을 생략한다
        off = evidence_prefetch.scope_from_job(job, cross_source=False)
        assert off is not None and off.skip_infra and evidence_prefetch.build_calls(off) == []

    def test_composite_briefing_block_from_real_snapshot(self):
        comp = P["episode_composite"]
        ctx = cse.active_episode(comp, True, _ref(comp))
        b = build_briefing(
            answer="CPU 포화 ← polestar_metric_trend\n큐잉 증가 ← apm_events",
            verdict=ImportanceVerdict(level="경고", confidence="none", escalate=False),
            tool_names=["polestar_metric_trend", "apm_events"],
            cross_source=ctx, apm_tools_connected=True,
        )
        cs = b["cross_source"]
        assert cs["sources"] == {"polestar": 1, "jennifer": 1}
        assert cs["links"] == [{
            "rule_id": "host_resource_to_was_latency", "cause": "CPU 사용률",
            "effect": "SERVICE_QUEUING", "lag_seconds": 270.0, "supported_by_tools": True,
        }]

    def test_dispatcher_audit_keys_from_real_payload(self):
        disp = InvestigationDispatcher(
            _settings(cross_source_investigation_enabled=True), diagnose_fn=None,
            briefing_fn=build_briefing, timeout_seconds=5.0,
        )
        audit = disp._episode_audit(_job("i-comp", P["episode_composite"]))
        assert audit == {
            "episode_id": P["episode_composite"]["meta"]["episode_id"],
            "kind": "episode", "cross_source_links": 1,
        }
        off = InvestigationDispatcher(
            _settings(), diagnose_fn=None, briefing_fn=build_briefing, timeout_seconds=5.0
        )
        assert off._episode_audit(_job("i-comp", P["episode_composite"])) == {}


class TestCompositeInvestigationDefects:
    # 검증 결함 — 종합 조사는 대표(원인) 알람 event를 실어 L-4 호스트 키(polestar_cm_gp, xsapp01)가
    # 원인 알람 단독 조사와 같았다. 단독 조사가 돌고 있으면 host_investigation_in_flight로 거부되고,
    # 사건당 1회(성패 무관)라 재시도도 없다. 교정은 플래그 on에서만 키에 kind 축을 붙인다.
    def test_composite_not_rejected_while_cause_investigation_runs(self):
        started, release = threading.Event(), threading.Event()

        def _slow(job):
            started.set()
            release.wait(5)
            return DiagnosisResult(
                answer="원인 ← polestar_metric_trend",
                tool_outputs=[ToolCallRecord("polestar_metric_trend", "d", "success", "ok")],
            )

        disp = InvestigationDispatcher(
            _settings(cross_source_investigation_enabled=True),
            diagnose_fn=_slow, briefing_fn=build_briefing, timeout_seconds=10.0,
        )
        try:
            cause = _job("i-cpu", P["polestar_cpu_single"])
            disp(cause)
            assert started.wait(5)
            comp = _job("i-comp", P["episode_composite"])
            disp(comp)
            assert comp.reason != "host_investigation_in_flight"
        finally:
            release.set()

    # 검증 결함 — 종합 조사 기준시각 = 대표(원인) 알람 alarmTime이라 조사 구간(기준시각 이전
    # lookback분)이 증상(제니퍼) 멤버 시각을 담지 못했다(T+4m). 교정은 플래그 on에서 잡 기준시각을
    # 멤버 최댓값으로 잡는다 — 그래서 alarmTime이 아니라 제출로 정해진 잡 기준시각을 본다.
    def test_composite_window_covers_all_members(self, tmp_path):
        comp = P["episode_composite"]
        store = JobStore(
            _settings(cross_source_investigation_enabled=True),
            executor=lambda job: None, audit_path=tmp_path / "audit.jsonl",
        )
        ref = store.get(store.submit(comp)["investigation_id"])["reference_time"]
        ctx = cse.episode_from_payload(comp, ref)
        assert all(offset_minutes(ref, m.occurred_at) <= 0 for m in ctx.members)
