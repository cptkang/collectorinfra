"""plans/134 W3·W4 본체 — M-5 복수 보기·대상 · 보기 6종 · 소스 선택 · 결정적 줄(계약 B-1 ~ B-3).

고정하는 계약(가짜 LLM 0 · 모의 게이트웨이 세션 — 실 제니퍼·실 게이트웨이 0):
  1. 보기 수 절단 없음(종전 `MAX_VIEWS = 3` 폐지) · APM 대상 수 상한 없음(종전 `max_targets`
     절단 폐지).
  2. 한 보기의 대상 2개 이상 = `targets` 배치 1호출 → `batch[i]`·`target_index`로 대상별 가상 호출을
     복원(집계·판정·출처·실패가 대상별) · 대상 1개 = 종전 호출 모양 그대로 · 배치 부분/전부 실패 ·
     배치 접수(작업 승격) = 작업 1건 접수 답(예상·진행).
  3. 첫 홉 = 절단 없이 전 인스턴스(호스트 없는 인스턴스도 대상 · 범위 고지 — W6 ④부터는 부하 순위
     상위 N · 순위를 못 받으면 목록 앞 N · `test_plan134_w6_body.py`) · `target: none`·
     `named`·지표 목록 보기는 첫 홉 없음 · 명시 대상 미해결은 첫 홉·다른 조회로 대신하지 않는다.
  4. 이름 보기(서비스·업무) — 이름 목록 1호출 · 없으면 전체 · 전부 미해결 = 「찾지 못함」.
  5. 전 대상 순위 `provisional` → 「잠정 순위」 줄 · 전 대상 이벤트 실패 도메인 ≠ 0건.
  6. 분해 `sources` — 검증 · 모르는 id 고지 · 전부 무효면 되묻기(게이트웨이를 열지 않는다) · 운반.
  7. 분해 프롬프트 — 활성 렌더만 바뀐다(비활성 sha256 앞 16자 `06da76f1a17e4090` · 8,032자 불변).
"""

from __future__ import annotations

import hashlib
import importlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.orchestration.conditional_agents import sanitize_task_views
from src.orchestration.investigation_audit import _apm_query_fields
from src.orchestration.replanner import _assign_ids
from src.orchestration.schemas import DecomposedPlan, views_plan_model
from src.prompts import intent_planner as pip
from src.routing.registry import SourceSpec, _parse_views, get_registry
from src.utils.prior_dependency import NOTE_TRACE
from tests.test_orchestration import apm_batch_mock

ip = importlib.import_module("src.orchestration.intent_planner")
ra = importlib.import_module("src.orchestration.result_aggregator")

NOW = datetime(2026, 10, 6, 10, 0, 0)
JOB = "b" * 32
#: 게이트웨이 `RANKING_METRICS`(계약 A-6 — 실시간 인스턴스 수치 칸 중립 이름 30개)
RANKING_METRICS = (
    "response_time_avg_ms", "tps", "active_services", "bad_response_active_services",
    "reject_rate", "concurrent_users", "arrival_rate", "heap_used_mb", "heap_committed_mb",
    "non_heap_used_mb", "gc_time_usage_pct", "process_cpu_pct", "process_memory_mb",
    "thread_current", "db_pool_active", "db_pool_idle_avg", "db_pool_configured_avg",
    "visit_day", "visit_hour", "hit_day", "hit_hour", "active_range_count_0",
    "active_range_count_1", "active_range_count_2", "active_range_count_3", "collection_count",
    "file_count", "socket_count", "thread_daemon", "thread_started",
)
#: 게이트웨이 `PERIOD_RANKING_METRICS` 중 기간 전용 이름(plans/134 W6 A-4)
PERIOD_ONLY_METRICS = ("calls", "failures", "failure_rate", "max_response_time_ms")
W34_VIEWS = ("apm.service", "apm.service_trend", "apm.ranking", "apm.fleet_events",
             "apm.business", "apm.business_trend")


def _cfg(*, active: bool = True) -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None,
                        source_endpoints={"apm": "http://127.0.0.1:9096/sse"} if active else {},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-06T10:00:00+09:00",
            "source_kind": "apm_api", "source": "apm", "tool": tool, "limits": [], **extra}


class _Gateway:
    """모의 게이트웨이 — 도구별 응답(함수 가능) · `emulate`면 `targets` 배치를 계약 A-2로 흉내."""

    def __init__(self, replies: dict, emulate: bool) -> None:
        self.replies = replies
        self.emulate = emulate
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    def _single(self, name: str, args: dict) -> dict:
        reply = self.replies.get(name)
        if callable(reply):
            reply = reply(args)
        return reply if reply is not None else _env(name, [{"tool_row": name}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if self.emulate:
            reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        else:
            reply = self._single(name, arguments)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def store(monkeypatch) -> ApmJobStore:
    ledger = ApmJobStore(None)
    monkeypatch.setattr(store_mod, "_STORE", ledger)
    return ledger


@pytest.fixture
def gateway(monkeypatch, store):
    def install(replies: dict | None = None, *, emulate: bool = True) -> _Gateway:
        gw = _Gateway(replies or {}, emulate)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _isolated(*hosts: str, limit: int | None = None, time_range: dict | None = None,
              **extra: Any) -> dict:
    parsed: dict[str, Any] = {
        "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
        "time_range": time_range}
    if limit is not None:
        parsed["limit"] = limit
    return {"parsed_requirements": parsed, "conversation_context": {}, "thread_id": "th-1",
            "user_id": "alice", **extra}


async def _run(views: list[str], isolated: dict | None = None, **task: Any) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": views, **task},
                                  isolated or _isolated(), llm=None, app_config=_cfg(), now=NOW)


def _kinds(res: dict, kind: str) -> list[str]:
    return [d["text"] for d in res.get("disclosures") or [] if d["kind"] == kind]


#: 부하 순위 실패 봉투(plans/134 W6 ④ — 첫 홉이 목록 폴백으로 가는 사례)
_NO_RANK = {"error": "api_error", "reason": "순위 실패", "tool": "apm_fleet"}


def _inst(iid: int, name: str | None, host: str | None, *, sid: str | None = "default",
          did: int = 10, confidence: str | None = None) -> dict:
    return {"source_id": sid, "instance_id": iid, "instance_name": name, "domain_id": did,
            "hostname": host, "match_confidence": confidence or ("high" if host else None)}


# ── 1. 보기 수 · 대상 수 상한 없음 ──────────────────────────────────────────────

def test_view_count_is_not_cut_and_the_constant_is_gone() -> None:
    views = ["apm.app_health", "apm.runtime", "apm.pool", "apm.active", "apm.slow_tx",
             "apm.events"]
    assert aq.sanitize_views([*views, "bogus", "apm.pool"]) == views, "중복 제거·닫힌 어휘만"
    assert not hasattr(aq, "MAX_VIEWS"), "보기 수 상한(종전 3)은 없다"


@pytest.mark.asyncio
async def test_four_or_more_views_in_one_task_are_all_queried(gateway) -> None:
    gw = gateway()
    views = ["apm.app_health", "apm.runtime", "apm.pool", "apm.active", "apm.slow_tx"]
    res = await _run(views, _isolated("web01"))
    assert res["apm_query"]["views"] == views
    assert [n for n, _ in gw.calls] == ["apm_app_health", "apm_runtime_health",
                                        "apm_resource_pool", "apm_active_services",
                                        "apm_slow_transactions"], "보기마다 1호출(절단 없음)"
    assert {p["view"] for p in res["apm_query"]["provenance"]} == set(views)


@pytest.mark.asyncio
async def test_plan125_a6_2_twelve_hostnames_one_batch_per_view(gateway) -> None:
    """plans/125 A-6 ② 증거 — hostname 12개(> 종전 상한 10)가 보기마다 `targets` 배치 1호출."""
    hosts = [f"web{i:02d}" for i in range(1, 13)]
    gw = gateway({"apm_app_health": lambda a: _env("apm_app_health", [{"tps": 1.0}],
                                                   summary={"calls": 10}),
                  "apm_events": lambda a: _env("apm_events", [{"event": "x"}])})
    res = await _run(["apm.app_health", "apm.events"], _isolated(*hosts))
    for tool in ("apm_app_health", "apm_events"):
        (call,) = gw.named(tool)
        assert call["targets"] == [{"hostname": h} for h in hosts], tool
        assert "hostname" not in call
    meta = res["apm_query"]
    assert meta["hostnames"] == hosts
    assert [r["hostname"] for r in res["query_results"]] == hosts * 2, "행은 대상별로 복원"
    assert all("target_index" not in r for r in res["query_results"])
    assert len([a for a in meta["aggregates"] if a["view"] == "apm.app_health"]) == 12
    assert meta["batches"] == [
        {"view": "apm.app_health", "tool": "apm_app_health", "targets": 12, "failed": 0},
        {"view": "apm.events", "tool": "apm_events", "targets": 12, "failed": 0}]
    assert res["source_status"][0]["status"] == "ok"
    assert len(_apm_query_fields(res)["commands"]) == 24, "감사 명령은 대상별(종전 모양)"


@pytest.mark.asyncio
async def test_one_target_keeps_the_former_call_shape(gateway) -> None:
    gw = gateway({"apm_instance_map": _env("apm_instance_map", [
        {"source_id": "default", "instance_id": 1, "instance_name": "abc-was01",
         "domain_id": 10, "hostname": None, "match_confidence": None}])})
    res = await _run(["apm.app_health"], _isolated("web01"))
    assert gw.calls == [("apm_app_health", {"hostname": "web01", "thread_id": "th-1",
                                            "owner": "user:alice", "wait_seconds": 8.0})]
    assert "batches" not in res["apm_query"], "배치가 없으면 메타 모양이 종전과 같다"
    gw2 = gateway({"apm_instance_map": _env("apm_instance_map", [
        {"source_id": "default", "instance_id": 1, "instance_name": "abc-was01",
         "domain_id": 10, "hostname": None, "match_confidence": None}])})
    await _run(["apm.app_health"], targets=[{"text": "abc", "kind": "instance"}])
    assert gw2.named("apm_app_health") == [{
        "thread_id": "th-1", "owner": "user:alice", "instance_name": "abc-was01",
        "source_ids": ["default"], "instance_id": 1, "wait_seconds": 8.0}], "해석 인스턴스 1개"


# ── 2. 배치 봉투 복원 · 부분 실패 · 전부 실패 · 접수 ──────────────────────────────

def _batch_reply(args: dict) -> dict:
    """계약 A-2 봉투(손으로 쓴 모양) — web02만 정합 실패."""
    assert args["targets"] == [{"hostname": "web01"}, {"hostname": "web02"}, {"hostname": "web03"}]
    resolution = {"matched": True, "confidence": "high", "reason": "hostName", "instances": [11]}
    return {
        "tool": "apm_app_health", "queried_at": "2026-10-06T10:00:00+09:00",
        "source": "apm", "source_kind": "apm_api", "row_count": 2, "partial": True,
        "limits": ["[한계] 대상 1/3 조회 실패: web02(instance_unresolved)"],
        "rows": [{"instance_id": 11, "tps": 1.0, "target_index": 0},
                 {"instance_id": 33, "tps": 3.0, "target_index": 2}],
        "batch": [
            {"index": 0, "target": {"hostname": "web01"}, "status": "ok", "row_count": 1,
             "summary": {"calls": 10, "errors": 1, "error_rate": 0.1},
             "instance_resolution": resolution,
             "was_signals": [{"kind": "was_heap_pressure", "level": "WARNING",
                              "category": "medium", "source_id": "default", "instance_id": 11,
                              "label": "힙 메모리 압박", "evidence": "heap 92%"}]},
            {"index": 1, "target": {"hostname": "web02"}, "status": "error",
             "error": "instance_unresolved", "reason": "hostName 불일치", "row_count": 0},
            {"index": 2, "target": {"hostname": "web03"}, "status": "ok", "row_count": 1,
             "summary": {"calls": 30}, "instance_resolution": {**resolution, "instances": [33]}},
        ],
    }


@pytest.mark.asyncio
async def test_batch_partial_failure_is_restored_per_target(gateway) -> None:
    gateway({"apm_app_health": _batch_reply}, emulate=False)
    res = await _run(["apm.app_health"], _isolated("web01", "web02", "web03"))
    meta = res["apm_query"]
    assert [r["hostname"] for r in res["query_results"]] == ["web01", "web03"]
    assert all("target_index" not in r for r in res["query_results"])
    assert meta["failures"] == [{"view": "apm.app_health", "hostname": "web02",
                                 "reason": "instance_unresolved: hostName 불일치"}]
    assert res["source_status"][0]["status"] == "partial"
    assert [p["hostname"] for p in meta["provenance"]] == ["web01", "web03"]
    assert [p["confidence"] for p in meta["provenance"]] == ["high", "high"]
    assert [(a["hostname"], a["summary"]) for a in meta["aggregates"]] == [
        ("web01", {"calls": 10, "errors": 1, "error_rate": 0.1}), ("web03", {"calls": 30})]
    assert meta["was_signals"][0]["hostname"] == "web01", "판정도 대상별"
    assert meta["batches"] == [{"view": "apm.app_health", "tool": "apm_app_health",
                                "targets": 3, "failed": 1}]
    ledger = {e["key"]: e["status"] for e in meta["link_ledger"] if e["facet"] == "apm_instance"}
    assert ledger == {"web01": "linked", "web02": "unlinked", "web03": "linked"}
    assert "조회하지 못한 대상 1건: web02(instance_unresolved" in res["organized_data"]["summary"]
    assert not any("대상 1/3" in x for x in meta["limits"]), "배치 실패 줄은 대상별 실패와 겹친다"
    assert "web01: 구간 호출 10건 · 오류 1건 · 오류율 10.0%" in "\n".join(res["answer_lines"])
    fields = _apm_query_fields(res)
    assert fields["outcome"] == "partial" and fields["commands"] == [
        "apm_app_health(hostname=web01)", "apm_app_health(hostname=web03)"]


@pytest.mark.asyncio
async def test_batch_total_failure_reports_each_target(gateway) -> None:
    def reply(args: dict) -> dict:
        return {"error": "instance_unresolved", "tool": "apm_app_health",
                "reason": "대상 2개 모두 실패 — web01 · web02",
                "batch": [{"index": i, "target": t, "status": "error",
                           "error": "instance_unresolved", "reason": f"{t['hostname']} 불일치",
                           "row_count": 0} for i, t in enumerate(args["targets"])]}

    gateway({"apm_app_health": reply}, emulate=False)
    res = await _run(["apm.app_health"], _isolated("web01", "web02"))
    assert res["degraded_reason"] == "apm_calls_failed"
    assert [(f["hostname"], f["reason"]) for f in res["apm_query"]["failures"]] == [
        ("web01", "instance_unresolved: web01 불일치"),
        ("web02", "instance_unresolved: web02 불일치")]
    assert "web01 불일치" in res["final_response"] and "web02 불일치" in res["final_response"]


@pytest.mark.asyncio
async def test_batch_without_per_target_results_is_a_failure_not_zero_rows(gateway) -> None:
    """계약과 다른 봉투(`batch` 없음 — 예 `targets`를 모르는 게이트웨이)를 행 0건으로 읽지 않는다.

    대상별 실패(`batch_missing`)로 끝낸다.
    """
    gateway({"apm_app_health": _env("apm_app_health", [{"tps": 9.0}])}, emulate=False)
    res = await _run(["apm.app_health"], _isolated("web01", "web02"))
    assert res["degraded_reason"] == "apm_calls_failed"
    assert all("batch_missing" in f["reason"] for f in res["apm_query"]["failures"])


@pytest.mark.asyncio
async def test_promoted_batch_is_one_accepted_job_with_estimate_and_progress(
        gateway, monkeypatch) -> None:
    handle = {"job_id": JOB, "state": "running",
              "progress": {"done": 12, "total": 120, "unit": "api_calls", "label": "API 호출"},
              "estimate": {"api_calls": 120, "seconds": 300}}
    gw = gateway({"apm_app_health": _env("apm_app_health", [], job=handle)}, emulate=False)

    async def poll_job(session, job_id, owner, *, until, interval=None):
        return {"job": handle}

    monkeypatch.setattr(aq.jobs, "poll_job", poll_job)
    hosts = [f"web{i:02d}" for i in range(1, 13)]
    res = await _run(["apm.app_health"], _isolated(*hosts))
    assert len(gw.named("apm_app_health")) == 1
    assert res["source_status"][0]["status"] == "accepted"
    assert res["accepted_jobs"] == [JOB]
    (line,) = _kinds(res, disc.APM_JOB_ACCEPTED)
    assert "대상 12개" in line and "예상 약 5분(API 호출 120회) · 진행 12/120 API 호출" in line
    assert "작업으로 접수했습니다 — 아직 조회 결과가 아닙니다" in res["final_response"]
    assert res["apm_query"]["batches"] == [{"view": "apm.app_health", "tool": "apm_app_health",
                                            "targets": 12, "job_id": JOB, "accepted": True}]


@pytest.mark.asyncio
async def test_batch_job_finished_in_time_is_restored_from_the_result_meta(
        gateway, monkeypatch) -> None:
    running = {"job_id": JOB, "state": "running"}
    gateway({"apm_app_health": _env("apm_app_health", [], job=running)}, emulate=False)
    done = _batch_reply({"targets": [{"hostname": h} for h in ("web01", "web02", "web03")]})
    status = {"job": {**running, "state": "partial"}, "rows": done.pop("rows"),
              "total_row_count": 2, "result_meta": done}

    async def poll_job(session, job_id, owner, *, until, interval=None):
        return status

    monkeypatch.setattr(aq.jobs, "poll_job", poll_job)
    res = await _run(["apm.app_health"], _isolated("web01", "web02", "web03"))
    assert [r["hostname"] for r in res["query_results"]] == ["web01", "web03"]
    assert [f["hostname"] for f in res["apm_query"]["failures"]] == ["web02"]
    assert [j["job_id"] for j in res["apm_query"]["jobs"]] == [JOB], "작업은 배치 1건"
    assert "accepted_jobs" not in res


# ── 3. 첫 홉 · 대상 없는 보기 · 명시 대상 미해결 ────────────────────────────────

@pytest.mark.asyncio
async def test_first_hop_takes_every_instance_including_hostless_ones(gateway) -> None:
    inventory = [_inst(1, "a1", "h1"), _inst(2, "a2", "h1"), _inst(3, "b3", None, did=20),
                 {**_inst(4, "c4", "h4"), "match_confidence": None},  # 호스트 정합 안 됨
                 _inst(5, None, None, sid=None)]
    # plans/134 W6 ④ — 부하 순위를 받지 못하면 목록(종전 첫 홉)의 앞 N개(기본 20 — 여기선 전부)
    gw = gateway({"apm_instance_map": _env("apm_instance_map", inventory), "apm_fleet": _NO_RANK})
    res = await _run(["apm.app_health", "apm.events"])
    (health,) = gw.named("apm_app_health")
    assert health["targets"] == [
        {"hostname": "h1"},
        {"instance_name": "b3", "source_id": "default", "instance_id": 3},
        {"instance_name": "c4", "source_id": "default", "instance_id": 4}]
    (events,) = gw.named("apm_events")
    assert events["targets"] == [{"hostname": "h1"},
                                 {"instance_name": "b3", "source_id": "default"},
                                 {"instance_name": "c4", "source_id": "default"}], \
        "인스턴스 id를 받지 않는 도구는 이름 + 소스"
    step = res["apm_query"]["inserted_steps"][0]
    assert {k: step[k] for k in ("hosts", "instances", "hostless", "unaddressed")} == {
        "hosts": 1, "instances": 5, "hostless": 2, "unaddressed": 1}
    (notice,) = _kinds(res, NOTE_TRACE)
    assert notice == ("대상 서버 미지정 — 전체 인스턴스 5개(호스트 1대) 조회 — 부하 순위를 받지"
                      " 못해(api_error: 순위 실패) 목록으로 골랐습니다 · 이름·소스가 없어"
                      " 부를 수 없는 인스턴스 1개는 빠졌습니다")


@pytest.mark.asyncio
async def test_targetless_views_never_insert_a_first_hop(gateway) -> None:
    gw = gateway({"apm_fleet": lambda a: _env("apm_fleet", [])})
    await _run(["apm.ranking", "apm.fleet_events", "apm.service", "apm.business", "apm.metrics"])
    assert gw.named("apm_instance_map") == [], "전 대상·이름·지표 목록 보기는 첫 홉 없음"
    assert [n for n, _ in gw.calls] == ["apm_fleet", "apm_fleet", "apm_service_status",
                                        "apm_business", "apm_metrics"]
    assert all("hostname" not in a and "targets" not in a for _, a in gw.calls)


@pytest.mark.asyncio
async def test_unresolved_explicit_hostname_is_not_replaced_by_a_first_hop(gateway) -> None:
    gw = gateway({"apm_app_health": {"error": "instance_unresolved", "reason": "불일치",
                                     "tool": "apm_app_health"}})
    res = await _run(["apm.app_health"], _isolated("web99"))
    assert gw.named("apm_instance_map") == [], "명시 hostname 실패를 첫 홉으로 대신하지 않는다"
    assert res["degraded_reason"] == "apm_calls_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["instance", "business"])
async def test_unresolved_name_or_business_text_makes_no_substitute_call(gateway, monkeypatch,
                                                                        kind) -> None:
    async def no_edge(terms, *, app_config, authorized_db_ids):
        return {t: [] for t in terms}, [], []

    monkeypatch.setattr(aq, "link_business_names", no_edge)
    gw = gateway({"apm_instance_map": lambda a: _env("apm_instance_map", [])})
    res = await _run(["apm.app_health"], targets=[{"text": "zzz", "kind": kind}])
    assert gw.named("apm_app_health") == []
    assert all(any(k in a for k in ("query", "business")) for a in gw.named("apm_instance_map")), \
        "해석 검색만 있고 목록 첫 홉은 없다"
    assert res["degraded_reason"] == "apm_target_unresolved"


# ── 4. 이름 보기(서비스·업무) ───────────────────────────────────────────────────

_SERVICE_ROW = {"source_id": "default", "domain_id": 1000, "domain_name": "주문",
                "tps": 12.0, "service_text": "주문", "match_tier": "exact"}


@pytest.mark.asyncio
async def test_named_view_sends_the_name_list_once_and_skips_instance_kind(gateway) -> None:
    gw = gateway({"apm_service_status": _env("apm_service_status", [_SERVICE_ROW])})
    res = await _run(["apm.service"], targets=[{"text": "주문", "kind": "auto"},
                                              {"text": "결제", "kind": "business"},
                                              {"text": "abc-was", "kind": "instance"}])
    assert gw.calls == [("apm_service_status", {"thread_id": "th-1", "owner": "user:alice",
                                                "service": ["주문", "결제"],
                                                "wait_seconds": 8.0})], "이름 해석은 게이트웨이"
    assert res["source_status"][0]["status"] == "ok"
    assert res["apm_query"]["provenance"][0]["args"] == {"service": ["주문", "결제"]}
    assert "service=" in _apm_query_fields(res)["commands"][0]


@pytest.mark.asyncio
async def test_named_view_without_names_is_one_call_for_everything(gateway) -> None:
    gw = gateway({"apm_business": _env("apm_business", [{"business_name": "결제"}])})
    await _run(["apm.business"], view_args={"apm.business": {"mode": "list"}})
    (args,) = gw.named("apm_business")
    assert args["mode"] == "list" and "business" not in args
    gw2 = gateway({"apm_metrics": _env("apm_metrics", [])})
    await _run(["apm.business_trend"], targets=[{"text": "결제", "kind": "business"}],
               isolated=_isolated(time_range={"start": "2026-10-06 07:00",
                                              "end": "2026-10-06 10:00"}))
    (trend,) = gw2.named("apm_metrics")
    assert (trend["mode"], trend["scope"], trend["business"], trend["lookback_minutes"]) == (
        "series", "business", ["결제"], 180)


def _not_found(args: dict) -> dict:
    texts = [f"서비스 '{s}'에 해당하는 제니퍼 도메인을 찾지 못했습니다" for s in args["service"]]
    return _env("apm_service_status", [],
                disclosures=[{"kind": "apm_unresolved_condition", "text": t} for t in texts],
                suggestions=[{"domain_name": "주문관리", "source_id": "default"}, "주문배치"])


@pytest.mark.asyncio
async def test_named_view_with_every_name_unresolved_answers_not_found(gateway) -> None:
    gw = gateway({"apm_service_status": _not_found})
    res = await _run(["apm.service"], targets=[{"text": "주문", "kind": "auto"}])
    assert len(gw.calls) == 1, "다른 조회로 대신하지 않는다"
    assert res["degraded_reason"] == "apm_target_unresolved"
    assert res["source_status"][0]["status"] == "not_queried"
    text = res["final_response"]
    assert "서비스 '주문'에 해당하는 제니퍼 도메인을 찾지 못했습니다" in text, "게이트웨이 고지"
    assert "다른 서비스로 대신 조회하지 않았습니다" in text
    assert "비슷한 이름: 주문관리 · 주문배치 — 자동으로 고르지 않았습니다." in text
    assert {"view": "apm.service", "hostname": None, "target": "주문",
            "reason": "대상 '주문' 해석 0건"} in res["apm_query"]["failures"]


@pytest.mark.asyncio
async def test_unresolved_named_view_beside_a_queried_view_is_partial(gateway) -> None:
    gateway({"apm_service_status": _not_found,
             "apm_app_health": _env("apm_app_health", [{"tps": 1.0}])})
    res = await _run(["apm.service", "apm.app_health"], _isolated("web01"),
                     targets=[{"text": "주문", "kind": "auto"}])
    assert [r["hostname"] for r in res["query_results"]] == ["web01"]
    assert res["source_status"][0]["status"] == "partial"
    assert any("찾지 못했습니다" in t for t in _kinds(res, disc.APM_UNRESOLVED_CONDITION))


@pytest.mark.asyncio
async def test_unresolved_service_text_answers_without_any_substitute(gateway) -> None:
    """서비스 이름 미해결 — 서비스 추세 보기도 전 서비스로 넓히지 않는다(이름 목록 그대로 보냄)."""
    gw = gateway({"apm_metrics": lambda a: _env("apm_metrics", [], disclosures=[
        {"kind": "apm_unresolved_condition", "text": "서비스 'x'를 찾지 못했습니다"}])})
    res = await _run(["apm.service_trend"], targets=[{"text": "x", "kind": "auto"}])
    (args,) = gw.named("apm_metrics")
    assert args["service"] == ["x"] and (args["mode"], args["scope"]) == ("series", "domain")
    assert res["degraded_reason"] == "apm_target_unresolved"


# ── 5. 전 대상 순위 · 이벤트 결정적 줄 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_provisional_ranking_line_goes_into_the_deterministic_block(gateway) -> None:
    rows = [{"rank": i, "instance_name": f"w{i}", "metric": "response_time_avg_ms",
             "value": 900.0 - i} for i in range(1, 6)]
    gw = gateway({"apm_fleet": _env(
        "apm_fleet", rows, partial=True, provisional=True,
        limits=["[한계] 잠정 순위 — 조회 실패 도메인 2곳(default/1, default/2)은 순위에 없습니다"],
        summary={"metric": "response_time_avg_ms", "order": "desc", "domains_total": 5,
                 "domains_ok": 3, "domains_failed": 2, "instances_total": 120,
                 "instances_ranked": 118, "instances_unranked": 2})})
    res = await _run(["apm.ranking"], _isolated(limit=5))
    (args,) = gw.named("apm_fleet")
    assert {k: args[k] for k in ("mode", "metric", "order", "n")} == {
        "mode": "ranking", "metric": "response_time_avg_ms", "order": "desc", "n": 5}
    lines = res["answer_lines"]
    assert any("response_time_avg_ms 내림차순 순위 — 전체 인스턴스 120개 중 상위 5" in x
               and "값 없는 인스턴스 2개" in x for x in lines), lines
    assert any(x.endswith("잠정 순위 — 조회 실패 도메인 2곳 제외") for x in lines), lines
    assert _kinds(res, disc.APM_PARTIAL_SOURCES), "잠정 순위는 부분 결과 의무 고지"
    assert res["source_status"][0]["status"] == "partial"
    block = ra._with_answer_lines("본문", lines)
    assert "**판정·집계**" in block and "잠정 순위 — 조회 실패 도메인 2곳 제외" in block


@pytest.mark.asyncio
async def test_fleet_events_failed_domains_are_not_zero_events(gateway) -> None:
    gw = gateway({"apm_fleet": _env(
        "apm_fleet", [], partial=True,
        limits=["[한계] 이벤트 조회 실패 도메인 1곳(default/3)"],
        summary={"domains_total": 4, "domains_failed": 1},
        coverage={"domains_total": 4, "from_buffer": 2, "from_api": 1, "mixed": 0,
                  "failed": 1})})
    res = await _run(["apm.fleet_events"], _isolated(
        time_range={"start": "2026-10-06", "end": "2026-10-06"}),
        view_args={"apm.fleet_events": {"level": "fatal", "level_mode": "exact", "full": True}})
    (args,) = gw.named("apm_fleet")
    assert (args["mode"], args["level"], args["level_mode"], args["full"]) == (
        "events", "fatal", "exact", True)
    assert args["lookback_minutes"] == 600
    (line,) = res["answer_lines"]
    assert line.endswith("이벤트 0건 · 도메인 4곳(버퍼 2 · API 1 · 실패 1) — 실패 도메인은 0건이"
                         " 아니라 확인하지 못함"), line
    assert res["source_status"][0]["status"] == "partial", "확인 못 한 도메인 ≠ 0건(empty)"


# ── 6. 소스 선택 ─────────────────────────────────────────────────────────────

def test_sanitize_sources_is_shape_only() -> None:
    assert aq.sanitize_sources([" BANK ", "bank", None, 7, ""]) == ["bank", "7"]
    assert aq.sanitize_sources("common") == ["common"] and aq.sanitize_sources(None) == []


@pytest.mark.asyncio
async def test_sources_scope_every_gateway_call(gateway) -> None:
    gw = gateway({"apm_instance_map": _env("apm_instance_map", [_inst(1, "a1", "h1"),
                                                                 _inst(2, "a2", "h2")])})
    await _run(["apm.app_health"], sources=["common"])
    (ranking,) = gw.named("apm_fleet")
    assert ranking["source_ids"] == ["common"], "첫 홉(부하 순위 · W6 ④)도 고른 소스 안에서"
    (first_hop,) = gw.named("apm_instance_map")
    assert first_hop["source_ids"] == ["common"], "순위 폴백 목록도 고른 소스 안에서"
    (batch,) = gw.named("apm_app_health")
    assert batch["source_ids"] == ["common"] and len(batch["targets"]) == 2
    gw2 = gateway({"apm_instance_map": _env("apm_instance_map", [_inst(1, "abc", None)])})
    await _run(["apm.app_health"], targets=[{"text": "abc", "kind": "instance"}],
               sources=["bank", "legacy"])
    (search,) = gw2.named("apm_instance_map")
    assert search["source_ids"] == ["bank", "legacy"], "해석 검색도 고른 소스 안에서"
    gw3 = gateway()
    await _run(["apm.ranking"], sources=["bank"])
    assert gw3.named("apm_fleet")[0]["source_ids"] == ["bank"]


@pytest.mark.asyncio
async def test_unknown_source_is_dropped_with_notice(gateway) -> None:
    gw = gateway()
    res = await _run(["apm.app_health"], _isolated("web01"), sources=["bank", "zzz"])
    assert gw.named("apm_app_health")[0]["source_ids"] == ["bank"]
    (notice,) = _kinds(res, disc.APM_UNRESOLVED_CONDITION)
    assert "zzz" in notice and "은행존 제니퍼만 조회했습니다" in notice
    assert res["apm_query"]["source_selection"] == {"given": ["bank", "zzz"], "used": ["bank"]}


@pytest.mark.asyncio
async def test_every_source_invalid_asks_back_without_opening_the_gateway(gateway) -> None:
    gw = gateway()
    res = await _run(["apm.app_health"], _isolated("web01"), sources=["zzz", "yyy"])
    assert gw.opened == 0 and gw.calls == [], "전 소스로 넓혀 조회하지 않는다"
    assert res["degraded_reason"] == "apm_unresolved_condition"
    assert res["source_status"][0]["status"] == "not_queried"
    text = res["final_response"]
    assert "zzz, yyy" in text
    assert "「은행존 제니퍼」 · 「공동존 제니퍼」 · 「레거시 제니퍼」" in text


def test_sources_ride_the_targets_transport_and_replanning() -> None:
    plan = {"tasks": [
        {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"],
         "sources": [" Bank ", "bank"]},
        {"task_id": "t2", "agent": "data_query", "sub_query": "q", "sources": ["bank"]}]}
    sanitize_task_views(plan, _cfg())
    apm, data = plan["tasks"]
    assert apm["sources"] == ["bank"] and "sources" not in data
    (replanned,) = _assign_ids([{"agent": "apm_query", "sub_query": "q",
                                 "sources": ["COMMON"]}], existing=[])
    assert replanned["sources"] == ["common"]
    model = views_plan_model(DecomposedPlan, ("apm_query",), True)
    task = next(iter(model.model_json_schema()["$defs"].values()))
    assert "sources" in task["properties"]
    plain = views_plan_model(DecomposedPlan, ("apm_query",), False)
    assert "sources" not in next(iter(plain.model_json_schema()["$defs"].values()))["properties"]


class _JsonLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


@pytest.mark.asyncio
async def test_json_decomposition_keeps_sources_only_for_apm_tasks() -> None:
    payload = {"tasks": [
        {"task_id": "t1", "agent": "apm_query", "sub_query": "은행존 WAS", "views": [],
         "sources": ["bank"], "areas": [], "requested_source": ""}]}
    out = await ip._llm_decompose(_JsonLLM(payload), "q", _cfg(active=True))
    assert out["tasks"][0]["sources"] == ["bank"]


# ── 7. 레지스트리 보기 6종 ─────────────────────────────────────────────────────

def test_w34_views_are_registry_data() -> None:
    views = {v.id: v for v in aq.apm_views()}
    assert [v.id for v in aq.apm_views()][22:28] == list(W34_VIEWS)
    table = {vid: (views[vid].tool, views[vid].fixed_args, views[vid].window, views[vid].target,
                   views[vid].target_arg, views[vid].capability) for vid in W34_VIEWS}
    assert table == {
        "apm.service": ("apm_service_status", {}, "current", "named", "service",
                        "was_performance"),
        "apm.service_trend": ("apm_metrics", {"mode": "series", "scope": "domain"}, "range",
                              "named", "service", "was_performance"),
        "apm.ranking": ("apm_fleet", {"mode": "ranking"}, "range", "none", "",
                        "was_performance"),
        "apm.fleet_events": ("apm_fleet", {"mode": "events"}, "range", "none", "", "apm_event"),
        "apm.business": ("apm_business", {}, "current", "named", "business", "was_performance"),
        "apm.business_trend": ("apm_metrics", {"mode": "series", "scope": "business"}, "range",
                               "named", "business", "was_performance"),
    }
    args = {vid: {a.name: a for a in views[vid].args} for vid in W34_VIEWS}
    metric = args["apm.ranking"]["metric"]
    # plans/134 W6 A-4 — 기간 전용 지표 4개가 뒤에 붙는다(게이트웨이 `ALL_RANKING_METRICS`)
    assert metric.type == "enum" and metric.choices == (*RANKING_METRICS, *PERIOD_ONLY_METRICS)
    assert metric.default == "response_time_avg_ms"
    assert args["apm.ranking"]["order"].choices == ("desc", "asc")
    assert args["apm.ranking"]["order"].default == "desc"
    assert {"n", "full"} <= set(args["apm.ranking"])
    assert args["apm.ranking"]["service"].type == "text"
    assert [a for a in args["apm.fleet_events"]] == ["level", "level_mode", "error_type", "n",
                                                     "full", "service"]
    assert args["apm.fleet_events"]["error_type"].type == "token"
    assert (args["apm.service_trend"]["metrics"].catalog,
            args["apm.business_trend"]["metrics"].catalog) == ("domain", "business")
    assert args["apm.business"]["mode"].choices == ("current", "list")
    assert args["apm.business"]["mode"].default == "current"
    apm_areas = set(next(s for s in get_registry().solutions() if s.code == "apm").capabilities)
    assert {views[v].capability for v in W34_VIEWS} <= apm_areas, "새 영역 없음(영역 카탈로그 불변)"


@pytest.mark.parametrize("raw,needle", [
    ({"id": "x", "target": "named"}, "target_arg"),
    ({"id": "x", "target_arg": "service"}, "target_arg"),
    ({"id": "x", "target": "named", "target_arg": "host"}, "target_arg"),
])
def test_named_target_requires_a_known_target_arg(raw, needle) -> None:
    with pytest.raises(ValueError, match=needle):
        _parse_views([raw])


def test_view_rows_describe_named_targets() -> None:
    rows = aq.render_view_rows()
    assert "`apm.service`: 서비스(APM 도메인) 현재 상태" in rows
    assert "서비스 이름을 말하면(targets) 그 서비스 · 없으면 전체" in rows
    assert "업무 이름을 말하면(targets) 그 업무 · 없으면 전체" in rows
    assert "`metric` = response_time_avg_ms|tps|" in rows


# ── 8. 분해 프롬프트 — 활성 렌더만 바뀐다 ──────────────────────────────────────

def test_inactive_decomposition_prompt_bytes_are_unchanged() -> None:
    prompt = ip._planner_system_prompt(_cfg(active=False))
    assert (hashlib.sha256(prompt.encode()).hexdigest()[:16], len(prompt)) == (
        "06da76f1a17e4090", 8032)
    for needle in ("sources", "apm.ranking", "필요한 보기 id", "apm_query"):
        assert needle not in prompt, needle


def test_active_prompt_asks_for_every_needed_view_and_renders_the_source_slot() -> None:
    prompt = ip._planner_system_prompt(_cfg(active=True))
    assert "필요한 보기 id를 **모두** 넣으세요" in prompt and "**1~2개**" not in prompt.split(
        "## 사내 문서")[0]
    assert "`apm.ranking`" in prompt and "`apm.fleet_events`" in prompt
    assert "`apm.service`" in prompt and "`apm.business_trend`" in prompt
    assert "`sources`에 소스 id를 넣습니다: `bank`(은행존 제니퍼) · `common`(공동존 제니퍼) ·" \
           " `legacy`(레거시 제니퍼)" in prompt
    assert prompt.count(pip.APM_SKELETON_TAIL_WITH_SOURCES) == 1
    assert prompt.count('"sources": []') == 1 and prompt.count(pip.APM_SOURCES_RULE) == 1
    assert pip.APM_SOURCES_SLOT not in prompt


def test_single_source_registry_renders_no_source_slot(monkeypatch) -> None:
    fake = SimpleNamespace(sources_of=lambda system: (SourceSpec("bank", "은행존 제니퍼"),))
    monkeypatch.setattr(aq, "get_registry", lambda: fake)
    assert aq.render_source_line() == ""
    monkeypatch.undo()
    base = ip._render_areas_prompt(ip.INTENT_PLANNER_SYSTEM_TEMPLATE,
                                   ip._area_rows(frozenset({"apm"})))
    rendered = pip.render_intent_planner_apm_template(base, "- **apm_query**: x", "rows", "")
    assert rendered.count(pip.APM_SKELETON_TAIL_WITH_KEYS) == 1
    assert '"sources"' not in rendered and pip.APM_SOURCES_RULE not in rendered
    assert pip.APM_SOURCES_SLOT not in rendered


# ── 9. 검증·리뷰 교정(body-fix-34) ───────────────────────────────────────────────

def _spooled(tool: str, rows: list[dict], inline: int, *, chunk_rows: int = 3) -> dict:
    """결과 파일로 간 봉투(게이트웨이 `jobs._finalize` 모양) — 앞 `inline`행 + 청크 목록 + 작업."""
    chunks = [{"index": i, "rows": len(rows[i * chunk_rows:(i + 1) * chunk_rows])}
              for i in range((len(rows) + chunk_rows - 1) // chunk_rows)]
    return _env(tool, rows[:inline], total_row_count=len(rows),
                artifact={"job_id": JOB, "total_rows": len(rows), "chunks": chunks,
                          "chunk_rows": chunk_rows},
                job={"job_id": JOB, "state": "completed"})


def _reader(rows: list[dict], *, chunk_rows: int = 3, fail_at: int | None = None):
    def read(args: dict) -> dict:
        assert args["job_id"] == JOB and args["owner"] == "user:alice"
        index = args["chunk"]
        if index == fail_at:
            return {"error": "api_error", "reason": "청크 읽기 실패", "tool": "apm_job_read"}
        return _env("apm_job_read", rows[index * chunk_rows:(index + 1) * chunk_rows])

    return read


@pytest.mark.asyncio
async def test_b2_first_hop_reads_the_whole_result_file(gateway) -> None:
    """V34-2 — 첫 홉 목록이 결과 파일로 가면 청크를 끝까지 읽어 전부 대상으로 쓴다."""
    inventory = [_inst(i, f"w{i}", f"h{i}") for i in range(1, 9)]
    gw = gateway({"apm_instance_map": _spooled("apm_instance_map", inventory, 2),
                  "apm_job_read": _reader(inventory), "apm_fleet": _NO_RANK})
    res = await _run(["apm.app_health"])
    assert [a["chunk"] for a in gw.named("apm_job_read")] == [0, 1, 2]
    (batch,) = gw.named("apm_app_health")
    assert [t["hostname"] for t in batch["targets"]] == [f"h{i}" for i in range(1, 9)]
    (notice,) = _kinds(res, NOTE_TRACE)
    assert notice == ("대상 서버 미지정 — 전체 인스턴스 8개(호스트 8대) 조회 — 부하 순위를 받지"
                      " 못해(api_error: 순위 실패) 목록으로 골랐습니다")
    assert res["source_status"][0]["status"] == "ok"


@pytest.mark.asyncio
async def test_b2_unreadable_result_file_is_disclosed_not_called_whole(gateway) -> None:
    inventory = [_inst(i, f"w{i}", f"h{i}") for i in range(1, 9)]
    gw = gateway({"apm_instance_map": _spooled("apm_instance_map", inventory, 2),
                  "apm_job_read": _reader(inventory, fail_at=1), "apm_fleet": _NO_RANK})
    res = await _run(["apm.app_health"])
    (batch,) = gw.named("apm_app_health")
    assert len(batch["targets"]) == 3, "읽은 청크(0)의 3대 — 미리보기 2대보다 많은 쪽"
    assert _kinds(res, NOTE_TRACE) == [], "「전체」 범위 고지를 내지 않는다"
    (partial,) = [t for t in _kinds(res, disc.APM_PARTIAL_SOURCES) if "대상 서버 미지정" in t]
    assert partial.startswith("대상 서버 미지정 — 인스턴스 8개 중 3개(호스트 3대)만 조회")
    assert "청크 2/3 읽기 실패" in partial
    assert res["source_status"][0]["status"] == "partial"


@pytest.mark.asyncio
async def test_b7_no_scope_notice_for_zero_instances(gateway) -> None:
    gateway({"apm_instance_map": _env("apm_instance_map", []), "apm_fleet": _env("apm_fleet", [])})
    res = await _run(["apm.app_health"])
    assert _kinds(res, NOTE_TRACE) == [] and res["degraded_reason"] == "apm_not_queried"


@pytest.mark.asyncio
async def test_b3_batch_rows_only_in_the_result_file_are_marked(gateway) -> None:
    """R34-1 — 배치 결과가 결과 파일로 가 미리보기에 행이 없는 대상은 표지가 붙고 0건이 아니다."""
    def reply(args: dict) -> dict:
        return {"tool": "apm_slow_transactions", "queried_at": "t", "source": "apm",
                "source_kind": "apm_api", "row_count": 2, "total_row_count": 6,
                "rows": [{"txid": "a", "target_index": 0}, {"txid": "b", "target_index": 0}],
                "artifact": {"job_id": JOB, "total_rows": 6, "chunks": [{"index": 0}]},
                "job": {"job_id": JOB, "state": "completed"},
                "batch": [{"index": i, "target": t, "status": "ok", "row_count": 3}
                          for i, t in enumerate(args["targets"])]}

    gateway({"apm_slow_transactions": reply}, emulate=False)
    res = await _run(["apm.slow_tx"], _isolated("web01", "web02"))
    prov = {p["hostname"]: p for p in res["apm_query"]["provenance"]}
    assert (prov["web01"]["rows"], prov["web01"]["rows_in_file"]) == (3, 1)
    assert (prov["web02"]["rows"], prov["web02"]["rows_in_file"]) == (3, 3)
    (notice,) = _kinds(res, disc.APM_FULL_RESULT_FILE)
    assert "앞 2행" in notice and "전체 6행" in notice
    assert notice.endswith("일부 대상의 행은 결과 파일에만 있습니다(2개 대상).")
    assert res["source_status"][0]["status"] == "ok"


@pytest.mark.asyncio
async def test_b3_rows_all_in_the_file_are_not_counted_empty(gateway) -> None:
    def reply(args: dict) -> dict:
        return {"tool": "apm_slow_transactions", "rows": [], "row_count": 0,
                "total_row_count": 4, "artifact": {"job_id": JOB, "chunks": [{"index": 0}]},
                "job": {"job_id": JOB, "state": "completed"},
                "batch": [{"index": i, "target": t, "status": "ok", "row_count": 2}
                          for i, t in enumerate(args["targets"])]}

    gateway({"apm_slow_transactions": reply}, emulate=False)
    res = await _run(["apm.slow_tx"], _isolated("web01", "web02"))
    assert res["source_status"][0]["status"] == "ok", "결과 파일에 행이 있다 — empty가 아니다"


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["apm.ranking", "apm.fleet_events"])
async def test_b4_service_texts_narrow_the_fleet_view(gateway, view) -> None:
    """V34-4 — 대상 텍스트(인스턴스 이름 제외)는 `service` 목록으로(조건 `service`와 합침)."""
    gw = gateway({"apm_fleet": _env("apm_fleet", [{"rank": 1}])})
    await _run([view], targets=[{"text": "주문", "kind": "auto"},
                                {"text": "결제", "kind": "business"}],
               view_args={view: {"service": "주문"}})
    (args,) = gw.named("apm_fleet")
    assert args["service"] == ["주문", "결제"], "중복 제거 · 조건 값이 앞"
    gw2 = gateway({"apm_fleet": _env("apm_fleet", [{"rank": 1}])})
    await _run([view], view_args={view: {"service": "주문"}})
    assert gw2.named("apm_fleet")[0]["service"] == "주문", "조건만이면 종전 모양(문자열)"


@pytest.mark.asyncio
async def test_b4_unresolved_fleet_service_is_not_found(gateway) -> None:
    gw = gateway({"apm_fleet": _env("apm_fleet", [], disclosures=[
        {"kind": "apm_unresolved_condition", "text": "서비스 '주문'을 찾지 못했습니다"}])})
    res = await _run(["apm.ranking"], targets=[{"text": "주문", "kind": "auto"}])
    assert len(gw.calls) == 1 and res["degraded_reason"] == "apm_target_unresolved"
    assert "다른 서비스로 대신 조회하지 않았습니다" in res["final_response"]


@pytest.mark.asyncio
async def test_b4_spoken_servers_beside_a_fleet_view_are_disclosed(gateway) -> None:
    gw = gateway({"apm_fleet": _env("apm_fleet", [{"rank": 1}])})
    res = await _run(["apm.ranking"], _isolated("web01", "web02"),
                     targets=[{"text": "abc-was", "kind": "instance"}])
    (args,) = gw.named("apm_fleet")
    assert "hostname" not in args and "service" not in args, "전체 범위로 조회"
    (notice,) = _kinds(res, disc.APM_UNRESOLVED_CONDITION)
    assert "말한 서버(web01, web02, abc-was)로는 좁히지 않았습니다" in notice
    assert disc.KIND_TABLE[disc.APM_UNRESOLVED_CONDITION].mandatory
    gw2 = gateway()
    res2 = await _run(["apm.ranking"], _isolated("web01", is_composite=True))
    assert _kinds(res2, disc.APM_UNRESOLVED_CONDITION) == [], \
        "복합 계획의 파서 식별자는 다른 task 몫일 수 있다"
    assert gw2.named("apm_fleet")


@pytest.mark.asyncio
async def test_b5_fleet_event_line_shows_total_and_shown(gateway) -> None:
    gateway({"apm_fleet": _env("apm_fleet", [{"e": 1}, {"e": 2}],
                               summary={"domains_total": 2, "events_total": 9},
                               coverage={"domains_total": 2, "from_buffer": 1, "from_api": 1,
                                         "mixed": 0, "failed": 0})})
    res = await _run(["apm.fleet_events"], view_args={"apm.fleet_events": {"n": 2}})
    (line,) = res["answer_lines"]
    assert line.endswith("이벤트 9건(표시 2건) · 도메인 2곳(버퍼 1 · API 1 · 실패 0)"), line


@pytest.mark.asyncio
async def test_b6_sources_without_a_source_table_are_disclosed(gateway, monkeypatch) -> None:
    fake = SimpleNamespace(sources_of=lambda system: ())
    monkeypatch.setattr(aq, "get_registry", lambda: SimpleNamespace(
        **{k: getattr(get_registry(), k) for k in ("system_label", "views_of",
                                                     "capability_specs", "capability_owners")},
        sources_of=fake.sources_of))
    gw = gateway()
    res = await _run(["apm.app_health"], _isolated("web01"), sources=["bank"])
    assert "source_ids" not in gw.named("apm_app_health")[0]
    assert any("소스를 가려 조회하지 않아 지목한 소스(bank)는 쓰지 않았습니다" in t
               for t in _kinds(res, NOTE_TRACE))
