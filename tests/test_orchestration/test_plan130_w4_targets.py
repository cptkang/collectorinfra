"""plans/130 W4 — 본체 `apm_query` 대상 텍스트 해석(M-1·M-2·M-4·M-5·M-6).

고정하는 계약:
  1. 분해 `targets`([{text, kind}])는 활성 렌더에만 있고(G-6 ①) 형태만 정제된다 — 종류는 LLM,
     해석은 코드(D-004).
  2. 해석: `instance`·`auto` → 게이트웨이 `query` · `business`(또는 `auto` 0건) → 게이트웨이
     `business` ∥ 업무명 간선 E6 → E1r(hostname → 인스턴스). 합집합 + 근거(G-3 ①) · 상한 없음
     (D-296 ④) · 해석 인스턴스마다 `instance_name`+`source_ids`(+`instance_id` 받는 도구만)로
     부른다.
  3. 텍스트가 있었는데 0건이면 첫 홉 없음 · 보기 호출 0 · 「찾지 못함」 + 후보 ≤3(자동 채택 없음 ·
     D-290 ⑥). 텍스트가 없으면 종전과 같다.
  4. E6은 활성 폴스타 DB ∩ 사용자 DB 권한만(D-232 · 관리자 전체) — 실패해도 게이트웨이 근거로
     계속하고 고지한다.
  5. `TargetRef`는 APM 인스턴스만으로도 유효하고(직렬화 불변) 서버 소비처는 그런 대상을 건너뛴다.
  6. 본체가 보내는 인자는 게이트웨이 실코드 입력 스키마와 맞는다(jsonschema · 별도 프로세스).
게이트웨이는 모의 MCP 세션, E2·E6은 모의 링크다(실 LLM·실 제니퍼·실 DB 0).
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.orchestration.conditional_agents import sanitize_task_views
from src.orchestration.entity_link import AMBIGUOUS, LINKED, UNLINKED, LinkEntry
from src.orchestration.investigation_audit import _apm_query_fields
from src.orchestration.replanner import _assign_ids
from src.orchestration.schemas import DecomposedPlan, views_plan_model
from src.prompts import intent_planner as prompts
from src.utils.prior_dependency import NOTE_BRIDGE
from src.utils.prior_targets import REASON_APM_ONLY, TargetRef, resolve_targets
from tests.test_orchestration import apm_batch_mock

# 패키지 `__init__`이 같은 이름의 함수를 다시 내보내 `from … import 모듈`이 함수를 가리킨다
ip = importlib.import_module("src.orchestration.intent_planner")

ROOT = Path(__file__).resolve().parents[2]
GW_ROOT = ROOT / "apm_gateway"
NOW = datetime(2026, 10, 6, 10, 0, 0)
APM = aq._short_label(aq.APM_SYSTEM)
OWNER = aq._short_label("polestar")


def _cfg(*, active: bool = True, max_targets: int = 10,
         dbs: tuple[str, ...] = ("polestar_cm_gp",)) -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None,
                        source_endpoints={"apm": "http://127.0.0.1:9096/sse"} if active else {},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=max_targets, fanout_concurrency=2,
                                  audit_enabled=False, task_frame_enabled=False,
                                  plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: list(dbs)),
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    """게이트웨이 정상 봉투(`ApmTools.ok`) 모양."""
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-06T10:00:00+09:00",
            "source_kind": "apm_api", "source": "apm", "tool": tool, "limits": [], **extra}


def _inst(iid: int, name: str, host: str | None = None, *, sid: str = "default",
          did: int = 10, **extra: Any) -> dict:
    """인스턴스 행(게이트웨이 `_instance_row` 모양의 필요한 칸)."""
    row = {"source_id": sid, "instance_id": iid, "instance_name": name, "domain_id": did,
           "domain_name": f"D{did}", "match_confidence": "high" if host else None}
    if host is not None:
        row["hostname"] = host
    row.update(extra)
    return row


class _Gateway:
    """모의 게이트웨이 — `apm_instance_map`은 인자 종류(query·business·hostname·목록)로 답한다."""

    def __init__(self, *, search: dict | None = None, business: dict | None = None,
                 hosts: dict | None = None, listing: list | None = None,
                 fixed: dict | None = None) -> None:
        self.search = search or {}
        self.business = business or {}
        self.hosts = hosts or {}
        self.listing = listing or []
        #: (검색 인자, 값) → 고정 봉투(오류·작업 핸들)
        self.fixed = fixed or {}
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]

    def searched(self, key: str) -> list[Any]:
        return [a[key] for a in self.named("apm_instance_map") if key in a]

    def listings(self) -> list[dict]:
        return [a for a in self.named("apm_instance_map")
                if not any(k in a for k in ("query", "business", "hostname"))]

    def _reply(self, name: str, args: dict) -> dict:
        for key in ("query", "business"):
            if key in args and (key, args[key]) in self.fixed:
                return self.fixed[(key, args[key])]
        if name != "apm_instance_map":
            return _env(name, [{"target": args.get("instance_name") or args.get("hostname"),
                                "tps": 1.0}])
        if "query" in args or "business" in args:
            table = self.search if "query" in args else self.business
            rows, suggestions = table.get(args.get("query") or args.get("business"), ([], []))
            return _env(name, rows, **({} if rows else {"suggestions": suggestions}))
        if "hostname" in args:
            rows = self.hosts.get(args["hostname"])
            if rows is None:
                return {"error": "instance_unresolved", "reason": "그 서버의 인스턴스가 없다"}
            return _env(name, rows)
        return _env(name, self.listing)

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        # plans/134 M-5 — 다건 대상은 `targets` 배치 1호출(계약 A-2 봉투로 흉내)
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._reply(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


class _Edge:
    """모의 업무명 간선 E6 — 호출 기록(검색어 · 권한 DB)과 hit 표."""

    def __init__(self, hits: dict | Exception | None = None) -> None:
        self.hits = hits if hits is not None else {}
        self.seen: list[dict] = []

    async def __call__(self, terms, *, app_config, authorized_db_ids):
        self.seen.append({"terms": list(terms), "authorized_db_ids": authorized_db_ids})
        if isinstance(self.hits, Exception):
            raise self.hits
        found = {t: list(self.hits.get(t, [])) for t in terms}
        entries = [LinkEntry(t, "business_name", "E6", LINKED if found[t] else UNLINKED,
                             grade="many" if found[t] else "none") for t in terms]
        steps = [{"edge": "E6", "from": "business_name", "to": "hostname", "owner": "polestar",
                  "keys": len(terms), "db_ids": list(authorized_db_ids or [])}]
        return found, entries, steps


@pytest.fixture
def gateway(monkeypatch):
    def install(edge: _Edge | None = None, **tables: Any) -> tuple[_Gateway, _Edge]:
        gw = _Gateway(**tables)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        link = edge or _Edge()
        monkeypatch.setattr(aq, "link_business_names", link)
        return gw, link

    return install


def _isolated(filters: list | None = None, **extra: Any) -> dict:
    return {"parsed_requirements": {"filter_conditions": filters or [], "time_range": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice", **extra}


async def _run(views: list[str], targets: Any = None, *, isolated: dict | None = None,
               cfg: SimpleNamespace | None = None, view_args: dict | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views}
    if targets is not None:
        task["targets"] = targets
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, isolated or _isolated(), llm=None,
                                  app_config=cfg or _cfg(), now=NOW)


def _kinds(res: dict, kind: str) -> list[str]:
    return [d["text"] for d in res.get("disclosures") or [] if d["kind"] == kind]


# ── 1. 인스턴스 이름 → 검색 → 인스턴스별 보기 호출 ─────────────────────────────

async def test_instance_target_calls_the_view_per_resolved_instance(gateway) -> None:
    gw, edge = gateway(search={"abc": ([_inst(1, "abc-was01", "h1"),
                                        _inst(2, "abc-was02", did=20)], [])})
    res = await _run(["apm.app_health"], [{"text": "abc", "kind": "instance"}])

    assert not res.get("error"), res.get("error")
    assert gw.searched("query") == ["abc"] and gw.searched("business") == []
    assert gw.listings() == [], "대상 텍스트가 있으면 첫 홉(목록)을 부르지 않는다"
    assert edge.seen == [], "인스턴스 이름이 맞으면 업무명 근거를 찾지 않는다"
    # plans/134 M-5 — 해석 인스턴스 2개 = `targets` 배치 1호출(항목 = 이름 + 소스 + id)
    assert len(gw.named("apm_app_health")) == 1
    calls = apm_batch_mock.expanded(gw.calls, "apm_app_health")
    assert [(c["instance_name"], c["source_ids"], c["instance_id"]) for c in calls] == [
        ("abc-was01", ["default"], 1), ("abc-was02", ["default"], 2)]
    assert all("hostname" not in c for c in calls), "인스턴스로 부른다(hostname 없이)"
    assert [r["target"] for r in res["query_results"]] == ["abc-was01", "abc-was02"]
    assert _kinds(res, NOTE_BRIDGE) == [f"'abc' → {APM} 인스턴스 2개(근거: {APM} 인스턴스 이름 2)"]
    meta = res["apm_query"]
    assert meta["targets"]["texts"][0]["instances"] == 2
    first = meta["targets"]["instances"][0]
    assert first == {"server_name": None, "hostname": "h1", "ip": None, "db_id": None,
                     "apm_domain_id": "10", "apm_instance_id": "1",
                     "apm_instance_name": "abc-was01", "apm_source_id": "default"}
    assert TargetRef(**meta["targets"]["instances"][1]).has_host_identifier is False
    assert "해석 인스턴스 2개" in res["organized_data"]["summary"]
    prov = meta["provenance"][0]
    assert prov["instance"]["instance_name"] == "abc-was01"
    assert prov["args"] == {"instance_name": "abc-was01", "instance_id": 1,
                            "source_ids": ["default"]}
    commands = _apm_query_fields(res)["commands"]
    assert "instance_name=abc-was01" in commands[0], commands


# ── 2. 업무명 — 게이트웨이 ∪ 간선(E6 → E1r) · 중복 제거 · 근거 고지 ────────────────

async def test_business_union_dedupes_and_discloses_evidence(gateway) -> None:
    edge = _Edge({"결제": [
        {"hostname": "h3", "server_name": "결제서버3", "db_id": "polestar_cm_gp", "field": "name"},
        {"hostname": "h1", "server_name": "공용", "db_id": "polestar_cm_gp",
         "field": "description"},
        {"hostname": "h9", "server_name": "결제배치", "db_id": "polestar_cm_gp", "field": "name"},
    ]})
    gw, _ = gateway(edge, business={"결제": ([
        _inst(1, "pay-was01", "h1", match_kind="domain", match_kinds=["domain", "business"]),
        _inst(2, "pay-was02", "h2", match_kind="instance_text", match_kinds=["instance_text"]),
    ], [])}, hosts={"h3": [_inst(3, "pay-was03")], "h1": [_inst(1, "pay-was01")]})
    res = await _run(["apm.app_health"], [{"text": "결제", "kind": "business"}])

    assert gw.searched("query") == [], "업무명 종류는 이름 검색을 건너뛴다"
    assert gw.searched("business") == ["결제"]
    e1r = gw.named("apm_instance_map")
    assert sorted(a["hostname"] for a in e1r if "hostname" in a) == ["h1", "h3", "h9"]
    assert all("query" not in a and "business" not in a for a in e1r if "hostname" in a)
    calls = apm_batch_mock.expanded(gw.calls, "apm_app_health")  # plans/134 M-5 — 배치 항목
    assert sorted(c["instance_id"] for c in calls) == [1, 2, 3], "같은 인스턴스는 한 번만 부른다"
    bridge = _kinds(res, NOTE_BRIDGE)
    assert len(bridge) == 1 and bridge[0].startswith(f"'결제' → {APM} 인스턴스 3개(근거: ")
    for part in (f"{APM} 도메인 이름 1", f"{APM} 업무 정의 1", f"{APM} 인스턴스 이름·설명 1",
                 f"{OWNER} 등록명 1", f"{OWNER} 비고 1"):
        assert part in bridge[0], (part, bridge[0])
    assert _kinds(res, disc.APM_PARTIAL_SOURCES) == [], "인스턴스 없는 서버(h9)는 실패가 아니다"
    meta = res["apm_query"]
    assert any(e["facet"] == "business_name" and e["edge"] == "E6" for e in meta["link_ledger"])
    assert any(s.get("edge") == "E6" for s in meta["inserted_steps"])
    evidence = meta["targets"]["texts"][0]["evidence"]
    assert evidence[f"{OWNER} 비고"] == 1 and evidence[f"{APM} 도메인 이름"] == 1


# ── 3. auto — 이름 먼저, 0건이면 업무명 ─────────────────────────────────────────

async def test_auto_falls_back_to_business_only_when_name_misses(gateway) -> None:
    gw, edge = gateway(
        search={"img": ([], [{"instance_name": "img1"}]), "abc": ([_inst(1, "abc-was01")], [])},
        business={"img": ([_inst(5, "img-was", "h5", match_kind="business",
                                 match_kinds=["business"], business_names=["이미지"])], [])})
    res = await _run(["apm.app_health"], [{"text": "img", "kind": "auto"}])
    order = [("query" if "query" in a else "business") for a in gw.named("apm_instance_map")]
    assert order == ["query", "business"], order
    assert edge.seen and edge.seen[0]["terms"] == ["img"], "업무명 단계는 간선도 함께 본다"
    assert [c["instance_name"] for c in gw.named("apm_app_health")] == ["img-was"]
    assert _kinds(res, disc.APM_UNRESOLVED_CONDITION) == []

    gw, edge = gateway(search={"abc": ([_inst(1, "abc-was01")], [])})
    await _run(["apm.app_health"], [{"text": "abc", "kind": "auto"}])
    assert gw.searched("business") == [] and edge.seen == [], "이름이 맞으면 업무명으로 가지 않는다"


# ── 4. 전부 0건 — 보기 호출 0 · 첫 홉 없음 · 후보 ≤3 ────────────────────────────

async def test_all_unresolved_makes_no_view_call_and_offers_candidates(gateway) -> None:
    suggestions = [{"instance_name": "abc-was01", "source_id": "default", "domain_id": 10},
                   {"instance_name": "abc-was01", "source_id": "s2", "domain_id": 10},
                   {"instance_name": "abd"}, {"instance_name": "abe"}, {"instance_name": "abf"}]
    gw, _ = gateway(search={"abx": ([], suggestions)},
                    listing=[_inst(9, "was09", "was09")])
    res = await _run(["apm.app_health"], [{"text": "abx", "kind": "instance"}])

    assert gw.named("apm_app_health") == [] and gw.listings() == []
    assert res["degraded_reason"] == "apm_target_unresolved"
    assert res["source_status"][0]["status"] == "not_queried"
    text = res["final_response"]
    assert f"'abx'에 해당하는 {APM} 인스턴스를 찾지 못해 조회하지 않았습니다" in text
    assert "다른 인스턴스로 대신 조회하지 않았습니다" in text
    assert "비슷한 이름: abc-was01 · abd · abe — 자동으로 고르지 않았습니다." in text
    assert "abf" not in text, "후보는 3개까지"
    assert _kinds(res, disc.APM_UNRESOLVED_CONDITION) == [text]


async def test_failed_search_says_not_checked_rather_than_not_found(gateway) -> None:
    gw, _ = gateway(fixed={("query", "abc"): {"error": "source_unavailable",
                                                "reason": "제니퍼 응답 없음"}})
    res = await _run(["apm.app_health"], [{"text": "abc", "kind": "instance"}])
    assert gw.named("apm_app_health") == []
    assert "확인하지 못해 조회하지 않았습니다(검색 실패)" in res["final_response"]
    assert any("검색 실패" in t for t in _kinds(res, disc.APM_PARTIAL_SOURCES))


@pytest.mark.parametrize("poll", ["raises", "completes"])
async def test_search_promoted_to_a_job_is_awaited_or_fails_closed(gateway, monkeypatch,
                                                                    poll) -> None:
    job = {"job_id": "a" * 32, "state": "running"}
    gw, _ = gateway(fixed={("query", "abc"): _env("apm_instance_map", [], job=job)})

    async def poll_job(session, job_id, owner, *, until, interval=None):
        if poll == "raises":
            raise RuntimeError("poll broke")
        return {"job": {**job, "state": "completed"}, "rows": [_inst(1, "abc-was01", "h1")]}

    monkeypatch.setattr(aq.jobs, "poll_job", poll_job)
    res = await _run(["apm.app_health"], [{"text": "abc", "kind": "instance"}])
    calls = gw.named("apm_app_health")
    if poll == "raises":
        assert calls == [], "작업 핸들뿐인 검색을 0건으로 읽어 다른 대상을 부르지 않는다"
        assert "확인하지 못해 조회하지 않았습니다(검색 실패)" in res["final_response"]
        assert any("RuntimeError" in t for t in _kinds(res, disc.APM_PARTIAL_SOURCES))
    else:
        assert [c["instance_name"] for c in calls] == ["abc-was01"]


# ── 5. 부분 해석 ────────────────────────────────────────────────────────────

async def test_partial_resolution_queries_only_what_resolved(gateway) -> None:
    gw, _ = gateway(search={"abc": ([_inst(1, "abc-was01", "h1")], [])})
    res = await _run(["apm.app_health"], [{"text": "abc", "kind": "instance"},
                                          {"text": "zzz", "kind": "instance"}])
    assert [c["instance_name"] for c in gw.named("apm_app_health")] == ["abc-was01"]
    assert _kinds(res, NOTE_BRIDGE) and len(_kinds(res, disc.APM_UNRESOLVED_CONDITION)) == 1
    assert "'zzz'" in _kinds(res, disc.APM_UNRESOLVED_CONDITION)[0]
    assert res["source_status"][0]["status"] == "partial"
    assert {"view": None, "hostname": None, "target": "zzz",
            "reason": "대상 'zzz' 해석 0건"} in res["apm_query"]["failures"]


# ── 6. 텍스트 없음 = 종전 그대로 ───────────────────────────────────────────────

async def test_without_target_text_the_first_hop_is_unchanged(gateway) -> None:
    listing = [_inst(1, "was01_a", "was01"), _inst(2, "was02_a", "was02")]
    gw, edge = gateway(listing=listing)
    base = await _run(["apm.app_health"])
    # plans/134 M-5 — 첫 홉 대상 2대 = `targets` 배치 1호출
    assert [n for n, _ in gw.calls] == ["apm_instance_map", "apm_app_health"]
    assert gw.listings() == [gw.calls[0][1]], "대상이 없으면 첫 홉 목록 그대로"
    assert [c["hostname"] for c in apm_batch_mock.expanded(gw.calls, "apm_app_health")] == [
        "was01", "was02"]
    assert edge.seen == [] and "targets" not in base["apm_query"]
    assert "해석 인스턴스" not in base["organized_data"]["summary"]

    gateway(listing=listing)
    empty = await _run(["apm.app_health"], [])
    assert json.dumps(empty, sort_keys=True, default=str) == json.dumps(
        base, sort_keys=True, default=str), "빈 targets 는 없는 것과 같다"


async def test_targets_on_a_view_without_target_are_ignored(gateway) -> None:
    gw, _ = gateway()
    base = await _run(["apm.metrics"])
    gw2, _ = gateway()
    with_text = await _run(["apm.metrics"], [{"text": "abc", "kind": "instance"}])
    assert gw2.searched("query") == [], "대상으로 좁히지 않는 보기는 해석하지 않는다"
    assert gw.calls == gw2.calls
    assert json.dumps(with_text, sort_keys=True, default=str) == json.dumps(
        base, sort_keys=True, default=str)


async def test_linked_server_name_stays_on_the_hostname_path(gateway, monkeypatch) -> None:
    async def linked(refs, *, consumer, app_config):
        hosts = ["h7" for r in refs if r.server_name]
        entries = [LinkEntry(str(r.server_name), "server_name", "E2", LINKED, grade="one",
                             value="h7") for r in refs if r.server_name]
        return hosts, entries, []

    monkeypatch.setattr(aq, "link_hostnames", linked)
    gw, _ = gateway()
    await _run(["apm.app_health"], isolated=_isolated(
        [{"field": "server_name", "op": "=", "value": "결제서버"}]))
    assert gw.searched("query") == [] and gw.listings() == []
    assert [c["hostname"] for c in gw.named("apm_app_health")] == ["h7"]


async def test_ambiguous_server_name_is_resolved_not_sent_to_the_first_hop(gateway,
                                                                          monkeypatch) -> None:
    """E2가 잇지 않은(`ambiguous` — hostname 여럿) 서버명도 대상 텍스트다(첫 홉 금지)."""
    async def ambiguous(refs, *, consumer, app_config):
        return [], [LinkEntry(str(r.server_name), "server_name", "E2", AMBIGUOUS, grade="many",
                              reason="hostname 2건 — 자동 결합하지 않는다")
                    for r in refs if r.server_name], []

    monkeypatch.setattr(aq, "link_hostnames", ambiguous)
    gw, _ = gateway()
    res = await _run(["apm.app_health"], isolated=_isolated(
        [{"field": "server_name", "op": "=", "value": "결제서버"}]))
    assert gw.searched("query") == ["결제서버"] and gw.listings() == []
    assert gw.named("apm_app_health") == []
    assert any("찾지 못" in t for t in _kinds(res, "apm_unresolved_condition"))


# ── 7. 인스턴스 목록 보기 + 대상 텍스트 = 해석 행이 답 ──────────────────────────

async def test_instances_view_answers_with_the_resolved_rows(gateway) -> None:
    gw, _ = gateway(search={"abc": ([_inst(1, "abc-was01", "h1"),
                                     _inst(2, "abc-was02", "h2")], [])})
    res = await _run(["apm.instances"], [{"text": "abc", "kind": "instance"}],
                     view_args={"apm.instances": {"domain_id": 10}})
    assert gw.calls == [("apm_instance_map", {"query": "abc", "thread_id": "th-1",
                                              "owner": "user:alice", "domain_id": 10,
                                              "wait_seconds": gw.calls[0][1]["wait_seconds"]})]
    rows = res["query_results"]
    assert [r["instance_name"] for r in rows] == ["abc-was01", "abc-was02"]
    assert all(r["target_text"] == "abc" and r["match_evidence"] == f"{APM} 인스턴스 이름"
               for r in rows)
    assert res["apm_query"]["provenance"][0]["args"] == {"targets": ["abc"]}
    assert "targets=" in _apm_query_fields(res)["commands"][0]


# ── 8. 상한 없음 ────────────────────────────────────────────────────────────

async def test_every_resolved_instance_is_queried_beyond_max_targets(gateway) -> None:
    many = [_inst(i, f"abc-was{i:02d}") for i in range(1, 13)]
    gw, _ = gateway(search={"abc": (many, [])})
    res = await _run(["apm.app_health"], [{"text": "abc", "kind": "instance"}],
                     cfg=_cfg(max_targets=10))
    # plans/134 M-5 — 12개(> max_targets 10) 전부가 `targets` 배치 1호출
    assert len(gw.named("apm_app_health")) == 1
    assert len(apm_batch_mock.expanded(gw.calls, "apm_app_health")) == 12, (
        "해석 인스턴스는 상한 없이 모두 부른다")
    assert len(res["query_results"]) == 12
    assert f"{APM} 인스턴스 12개" in _kinds(res, NOTE_BRIDGE)[0]


# ── 9. E6 DB 권한 · 실패 ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("role", "allowed", "expected"), [
    ("user", ["polestar_cm_yd"], ["polestar_cm_yd"]),
    ("user", [], []),
    ("user", None, ["polestar_cm_gp", "polestar_cm_yd"]),
    ("admin", [], ["polestar_cm_gp", "polestar_cm_yd"]),
])
async def test_edge_db_scope_follows_user_db_permission(gateway, role, allowed,
                                                        expected) -> None:
    _, edge = gateway()
    await _run(["apm.app_health"], [{"text": "결제", "kind": "business"}],
               isolated=_isolated(user_role=role, allowed_db_ids=allowed),
               cfg=_cfg(dbs=("polestar_cm_gp", "polestar_cm_yd")))
    assert edge.seen[0]["authorized_db_ids"] == expected


async def test_edge_failure_proceeds_with_gateway_and_discloses(gateway) -> None:
    gw, _ = gateway(_Edge(RuntimeError("db down")),
                    business={"결제": ([_inst(1, "pay-was01", "h1", match_kind="domain",
                                             match_kinds=["domain"])], [])})
    res = await _run(["apm.app_health"], [{"text": "결제", "kind": "business"}])
    assert [c["instance_name"] for c in gw.named("apm_app_health")] == ["pay-was01"]
    assert _kinds(res, disc.APM_PARTIAL_SOURCES) == [
        "'결제' 대상 해석: 업무명 → 서버 조회 실패(RuntimeError)"]
    assert _kinds(res, NOTE_BRIDGE) == [f"'결제' → {APM} 인스턴스 1개(근거: {APM} 도메인 이름 1)"]


async def test_gateway_business_failure_still_uses_the_edge(gateway) -> None:
    edge = _Edge({"결제": [{"hostname": "h3", "server_name": "결제서버", "db_id": "polestar_cm_gp",
                            "field": "name"}]})
    gw, _ = gateway(edge, hosts={"h3": [_inst(3, "pay-was03")]},
                    fixed={("business", "결제"): {"error": "source_unavailable",
                                                   "reason": "응답 없음"}})
    res = await _run(["apm.app_health"], [{"text": "결제", "kind": "business"}])
    assert [c["instance_name"] for c in gw.named("apm_app_health")] == ["pay-was03"]
    assert any(t.startswith(f"'결제' 대상 해석: {APM} 업무명 검색 실패")
               for t in _kinds(res, disc.APM_PARTIAL_SOURCES))


# ── 10. 정제 · 분해 슬롯 · 렌더 ─────────────────────────────────────────────

def test_sanitize_targets_keeps_shape_only() -> None:
    raw = [1, "abc", {"text": "  a  ", "kind": "INSTANCE"}, {"text": "A"}, {"text": ""},
           {"text": "x" * (aq.TARGET_TEXT_MAX + 1)}, {"text": "b", "kind": "weird"},
           {"text": "c", "kind": "business"}, {"kind": "auto"}, {"text": 3}]
    clean = aq.sanitize_targets(raw)
    # 길이 초과는 버리지 않고 줄여 남긴다 — 처리기가 「대상 있음」으로 세고 해석 없이 고지(V130-7)
    assert clean == [{"text": "a", "kind": "instance"},
                     {"text": "x" * aq.TARGET_TEXT_MAX + "…", "kind": "auto"},
                     {"text": "b", "kind": "auto"}, {"text": "c", "kind": "business"}]
    assert aq.sanitize_targets(clean) == clean, "다시 걸러도 같다"
    assert aq.sanitize_targets(None) == [] and aq.sanitize_targets("abc") == []


def test_task_sanitizer_keeps_targets_only_on_apm_tasks() -> None:
    plan = {"tasks": [
        {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"],
         "targets": [{"text": "abc", "kind": "instance"}, "junk"]},
        {"task_id": "t2", "agent": "data_query", "targets": [{"text": "abc"}]},
        {"task_id": "t3", "agent": "apm_query", "views": ["apm.app_health"], "targets": []},
    ]}
    sanitize_task_views(plan, _cfg())
    t1, t2, t3 = plan["tasks"]
    assert t1["targets"] == [{"text": "abc", "kind": "instance"}]
    assert "targets" not in t2 and "targets" not in t3


class _JsonLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


async def test_json_decompose_carries_targets_only_when_active() -> None:
    payload = {"tasks": [{"task_id": "t1", "agent": "apm_query", "sub_query": "abc 응답시간",
                          "views": ["apm.app_health"],
                          "targets": [{"text": "abc", "kind": "instance"}]}]}
    active = await ip._llm_decompose(_JsonLLM(payload), "q", _cfg(active=True))
    assert active["tasks"][0]["targets"] == [{"text": "abc", "kind": "instance"}]
    inactive = await ip._llm_decompose(_JsonLLM(payload), "q", _cfg(active=False))
    assert all("targets" not in t for t in inactive["tasks"])


def test_structured_plan_accepts_loose_targets_only_with_view_args() -> None:
    model = views_plan_model(DecomposedPlan, ("apm_query",), True)
    plan = model.model_validate({"tasks": [{
        "task_id": "t1", "agent": "apm_query", "sub_query": "x", "views": ["apm.app_health"],
        "targets": [{"text": "abc", "kind": "instance"}, "junk", {"kind": "auto"}]}]})
    assert plan.model_dump()["tasks"][0]["targets"][0] == {"text": "abc", "kind": "instance"}
    plain = views_plan_model(DecomposedPlan, ("apm_query",), False)
    task_schema = next(iter(plain.model_json_schema()["$defs"].values()))
    assert "targets" not in task_schema["properties"], "보기 조건 없는 모델은 스키마 불변"


def test_planner_prompt_has_targets_only_when_active() -> None:
    inactive = ip._planner_system_prompt(_cfg(active=False))
    active = ip._planner_system_prompt(_cfg(active=True))
    assert "targets" not in inactive, "비활성 렌더는 바이트 불변(대상 텍스트 슬롯 없음)"
    assert "targets" in prompts.APM_SKELETON_TAIL_WITH_KEYS
    assert "targets" not in prompts.APM_SKELETON_TAIL
    assert active.count('"targets": []') == 1, "골격 꼬리에 한 번"
    assert "`instance`" in active and "`business`" in active and "`auto`" in active
    assert "hostname·IP는 `targets`에 넣지 마세요" in active


# ── 11. TargetRef 계약(M-5) ──────────────────────────────────────────────────

def test_target_ref_apm_only_is_valid_and_serialization_is_unchanged() -> None:
    plain = TargetRef(server_name="svr-1", hostname="h1")
    assert plain.model_dump() == {"server_name": "svr-1", "hostname": "h1", "ip": None,
                                  "db_id": None}, "종전 4필드 직렬화 그대로"
    apm_only = TargetRef(apm_instance_name="abc-was01", apm_source_id="default")
    assert apm_only.has_host_identifier is False and plain.has_host_identifier is True
    assert apm_only.model_dump() == {"server_name": None, "hostname": None, "ip": None,
                                     "db_id": None, "apm_instance_name": "abc-was01",
                                     "apm_source_id": "default"}
    assert TargetRef(**apm_only.model_dump()) == apm_only
    with pytest.raises(ValueError, match="apm_instance_name"):
        TargetRef(db_id="polestar_cm_gp")


def test_server_consumers_skip_apm_only_targets() -> None:
    apm_only = {"apm_instance_name": "abc-was01", "apm_source_id": "default"}
    res = resolve_targets(prior_targets=[apm_only, {"hostname": "h1"}], db_id=None)
    assert [t.hostname for t in res.targets] == ["h1"]
    assert any(d.get("reason") == REASON_APM_ONLY for d in res.dropped)
    # APM 인스턴스만 있으면 선행 결과가 없는 것과 같다 — 다음 출처(이번 턴 식별자)로 간다
    fallback = resolve_targets(prior_targets=[TargetRef(**apm_only)], filter_conditions=[
        {"field": "hostname", "op": "=", "value": "h2"}], db_id=None)
    assert [t.hostname for t in fallback.targets] == ["h2"]


# ── 12. 게이트웨이 실계약(입력 스키마 대조) ─────────────────────────────────────

_DUMP_SCHEMA = """
import asyncio, json, sys, tempfile
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.config import load_config
from apm_gateway.interface.server import create_server

async def main():
    spool = tempfile.mkdtemp(prefix="gw-schema-")
    cfg = load_config({"JENNIFER_API_URL": "http://127.0.0.1:1", "JENNIFER_API_TOKEN": "x",
                       "APM_SPOOL_DIR": spool})
    tools = ApmTools(build_source_set(cfg), cfg)
    jobs = JobManager.from_config(cfg, envelope=tools.ok, error_envelope=tools.err)
    mcp = create_server(tools, jobs=jobs, port=0)
    listed = await mcp.list_tools()
    sys.stdout.write(json.dumps({t.name: t.inputSchema for t in listed}, ensure_ascii=False))
    await jobs.aclose()

asyncio.run(main())
"""


@pytest.fixture(scope="module")
def gw_schema() -> dict[str, dict]:
    """게이트웨이 실코드의 MCP 입력 스키마(별도 프로세스 · 게이트웨이 cwd — import 경계 유지)."""
    pytest.importorskip("mcp")
    env = {**os.environ, "PYTHONPATH": "."}
    proc = subprocess.run([sys.executable, "-c", _DUMP_SCHEMA], cwd=GW_ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _check(schema: dict, sent: dict) -> None:
    from jsonschema import Draft202012Validator

    unknown = set(sent) - set(schema["properties"])
    assert not unknown, f"게이트웨이가 모르는 인자(FastMCP는 조용히 버린다): {unknown}"
    assert set(schema.get("required") or []) <= set(sent), (schema.get("required"), sent)
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(sent)]
    assert not errors, errors


async def test_target_calls_match_the_gateway_schema(gw_schema, gateway) -> None:
    edge = _Edge({"결제": [{"hostname": "h3", "server_name": "결제서버", "db_id": "polestar_cm_gp",
                            "field": "name"}]})
    gw, _ = gateway(edge, search={"abc": ([_inst(1, "abc-was01", "h1")], [])},
                    business={"결제": ([_inst(2, "pay-was02", "h2", match_kind="domain",
                                             match_kinds=["domain"])], [])},
                    hosts={"h3": [_inst(3, "pay-was03")]})
    await _run(["apm.app_health", "apm.events", "apm.loaded_classes"],
               [{"text": "abc", "kind": "instance"}, {"text": "결제", "kind": "business"}])
    await _run(["apm.instances", "apm.environment"], [{"text": "abc", "kind": "auto"}],
               view_args={"apm.instances": {"domain_id": 10}})
    sent = {(name, tuple(sorted(a))) for name, a in gw.calls}
    assert {"query", "business", "hostname"} <= {
        k for n, a in gw.calls if n == "apm_instance_map" for k in a}
    assert {n for n, _ in sent} >= {"apm_instance_map", "apm_app_health", "apm_events",
                                    "apm_config", "apm_environment"}
    for name, args in gw.calls:
        _check(gw_schema[name], args)


def test_instance_id_tools_match_the_gateway_schema(gw_schema) -> None:
    view_tools = {v.tool for v in aq.apm_views()}
    with_id = {t for t in view_tools if "instance_id" in gw_schema[t]["properties"]}
    assert aq.INSTANCE_ID_TOOLS == with_id, "인스턴스 id 를 받는 도구 표가 게이트웨이와 어긋났다"
    target_tools = {v.tool for v in aq.apm_views() if v.required_input or v.target == "optional"}
    for tool in sorted(target_tools):
        props = gw_schema[tool]["properties"]
        assert {"instance_name", "source_ids"} <= set(props), (tool, sorted(props))
    search = gw_schema["apm_instance_map"]["properties"]
    assert {"query", "business", "hostname", "domain_id"} <= set(search)


# ── 12. W4 교정(verify-130 · V130-1·3·4·6·7) ──────────────────────────────────

def _second_turn(query: str) -> dict:
    """2턴째 — 직전 턴이 web01을 조회했다."""
    return _isolated(conversation_context={
        "previous_entities": [{"field": "hostname", "value": "web01"}], "turn_count": 2},
        original_user_query=query, user_query=query)


async def test_demonstrative_keeps_the_previous_turn_host_beside_target_text(gateway) -> None:
    """대상 텍스트가 있어도 지시어(「그 서버」)로 가리키면 직전 턴 서버를 잇는다(V130-1 대조군)."""
    gw, _ = gateway(search={"abc-was01": ([_inst(1, "abc-was01", "h1")], [])})
    res = await _run(["apm.app_health"], [{"text": "abc-was01", "kind": "instance"}],
                     isolated=_second_turn("그 서버랑 abc-was01 응답시간 알려줘"))
    calls = sorted((c.get("hostname") or "", c.get("instance_name") or "")
                   for c in apm_batch_mock.expanded(gw.calls, "apm_app_health"))
    assert calls == [("", "abc-was01"), ("web01", "")]
    assert len(gw.named("apm_app_health")) == 1, "서버 + 인스턴스 = 한 보기 배치 1호출(M-5)"
    assert res["apm_query"]["hostnames"] == ["web01"]


async def test_over_length_target_survives_decomposition_and_is_disclosed(gateway) -> None:
    """분해 정제가 길이 초과 텍스트를 버리지 않아 처리기가 첫 홉을 막고 고지한다(V130-7)."""
    plan = {"tasks": [{"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"],
                       "targets": [{"text": "가" * 500, "kind": "instance"}]}]}
    sanitize_task_views(plan, _cfg())
    (task,) = plan["tasks"]
    assert len(task["targets"][0]["text"]) == aq.TARGET_TEXT_MAX + 1
    gw, edge = gateway(listing=[_inst(1, "first-was", "h1")])
    res = await aq.run_apm_query(task, _isolated(), llm=None, app_config=_cfg(), now=NOW)
    assert gw.calls == [] and edge.seen == [], "해석 호출도 첫 홉도 없다"
    assert res["degraded_reason"] == "apm_target_unresolved"
    (notice,) = _kinds(res, disc.APM_UNRESOLVED_CONDITION)
    assert notice.startswith(f"대상 이름이 너무 길어({aq.TARGET_TEXT_MAX}자 초과)")
    assert len(notice) < 150, "원문 전체를 싣지 않는다"


def test_replanned_targets_are_sanitized_and_kept_only_on_apm_tasks() -> None:
    apm, data = _assign_ids([
        {"agent": "apm_query", "sub_query": "q",
         "targets": [{"text": " abc ", "kind": "INSTANCE"}, "junk"]},
        {"agent": "data_query", "sub_query": "q", "targets": [{"text": "abc"}]},
    ], existing=[])
    assert apm["targets"] == [{"text": "abc", "kind": "instance"}]
    assert "targets" not in data


async def test_server_names_have_no_legacy_cap(gateway, monkeypatch) -> None:
    """파서 서버명은 상한 없이 E2로 간다(plans/134 M-5 · D-296 ④ — 종전 `max_targets` 절단 폐지).

    V130-6(상한 밖 이름이 조용히 빠짐)은 상한 자체가 없어져 생기지 않는다 — 이은 이름은 모두
    hostname 대상이고 한 보기의 `targets` 배치 1호출이다.
    """
    linked_names: list[str] = []

    async def linked(refs, *, consumer, app_config):
        names = [str(r.server_name) for r in refs if r.server_name]
        linked_names.extend(names)
        return [f"h-{n}" for n in names], [
            LinkEntry(n, "server_name", "E2", LINKED, grade="one", value=f"h-{n}")
            for n in names], []

    monkeypatch.setattr(aq, "link_hostnames", linked)
    names = [f"srv{i:02d}" for i in range(1, 5)]
    gw, _ = gateway(search={"srv04": ([_inst(4, "srv04-was")], [])})
    res = await _run(["apm.app_health"], isolated=_isolated(
        [{"field": "server_name", "op": "=", "value": n} for n in names]),
        cfg=_cfg(max_targets=2))
    assert linked_names == names, "상한(2)으로 자르지 않는다"
    assert gw.searched("query") == [], "모두 E2로 이어 대상 텍스트가 없다"
    called = sorted(c.get("hostname") or c.get("instance_name")
                    for c in apm_batch_mock.expanded(gw.calls, "apm_app_health"))
    assert called == ["h-srv01", "h-srv02", "h-srv03", "h-srv04"]
    assert len(gw.named("apm_app_health")) == 1
    assert res["apm_query"]["hostnames"] == [f"h-{n}" for n in names]


async def test_edge_row_cap_is_a_partial_notice_and_in_the_summary(gateway) -> None:
    class Capped(_Edge):
        async def __call__(self, terms, *, app_config, authorized_db_ids):
            found, entries, steps = await super().__call__(
                terms, app_config=app_config, authorized_db_ids=authorized_db_ids)
            return found, entries, [{**s, "truncated": True} for s in steps]

    edge = Capped({"결제": [{"hostname": "h3", "server_name": "결제서버",
                             "db_id": "polestar_cm_gp", "field": "name"}]})
    gw, _ = gateway(edge, hosts={"h3": [_inst(3, "pay-was03")]})
    res = await _run(["apm.app_health"], [{"text": "결제", "kind": "business"}])
    assert [c["instance_name"] for c in gw.named("apm_app_health")] == ["pay-was03"]
    assert _kinds(res, disc.APM_PARTIAL_SOURCES) == [
        f"'결제' 대상 해석: {OWNER} 등록명·비고 조회 상한에 닿아 일부만 확인 — 빠진 서버가 있을 수"
        " 있습니다"]
    assert "E6 변환이 조회 상한에 닿아 일부만 확인했습니다." in res["organized_data"]["summary"]
