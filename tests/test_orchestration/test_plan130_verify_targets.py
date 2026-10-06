"""plans/130 독립 검증(verify-130 · 2026-10-06) — 본체 대상 해석의 결함 재현과 경계.

1. **결함 재현**(W4 교정 뒤 통과 — `xfail(strict=True)` 표지는 걷었다):
   - V130-1(Major) 대상 텍스트가 있는 턴에 직전 턴 서버(`previous_entities`)로 대신·함께 조회한다.
   - V130-2(Minor) 같은 소스의 두 도메인에 같은 이름·같은 id 인스턴스가 있으면 같은 인자로 두 번
     부른다(게이트웨이 데이터 도구는 `domain_id`를 받지 않는다).
   - V130-3(Minor) 업무명 간선(E6)이 행 상한에 닿아도 사용자 고지·요약에 나오지 않는다.
   - V130-4(Minor · plans/125 B-7 잔여) 재계획 task가 `targets`를 잃는다.
   - V130-6(Minor) 파서 서버명 대상(E2 미연결)은 `max_targets`(기본 10)에서 잘리고 고지가 없다.
   - V130-7(Minor) 200자를 넘는 대상 텍스트는 조용히 버려져 첫 홉(임의 인스턴스)으로 간다.
2. **경계**: 관측 소스 권한(`allowed_sources`)이 APM을 막으면 해석 호출(게이트웨이·E6)도 0이다 ·
   `TargetRef` 완화(APM 인스턴스만)가 서버 소비처(폴스타 SQL 경로)로 새지 않는다.

게이트웨이는 모의 MCP 세션, E6은 모의 링크다(실 LLM·실 제니퍼·실 DB 0).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.orchestration.entity_link import LINKED, UNLINKED, LinkEntry
from src.orchestration.process_query import _targets_from_prior_rows
from src.orchestration.replanner import _assign_ids
from src.utils.prior_targets import REASON_APM_ONLY, resolve_targets
from tests.test_orchestration.test_plan130_w4_targets import (
    _cfg,
    _Edge,
    _Gateway,
    _inst,
    _isolated,
    _kinds,
    _run,
)


@pytest.fixture
def gateway(monkeypatch):
    def install(edge: _Edge | None = None, **tables: Any) -> tuple[_Gateway, _Edge]:
        gw = _Gateway(**tables)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        link = edge or _Edge()
        monkeypatch.setattr(aq, "link_business_names", link)
        return gw, link

    return install


def _previous_turn(query: str, host: str = "web01") -> dict:
    """2턴째 — 직전 턴이 `host`를 조회했고 이번 턴은 지시어 없이 다른 대상을 말한다."""
    return _isolated(conversation_context={
        "previous_entities": [{"field": "hostname", "value": host}], "turn_count": 2},
        original_user_query=query, user_query=query)


def _view_calls(gw: _Gateway) -> list[tuple[str, dict]]:
    return [(n, a) for n, a in gw.calls if n != "apm_instance_map"]


# ── V130-1 (Major) 직전 턴 서버로 대신·함께 조회 ───────────────────────────────

async def test_unresolved_text_without_previous_turn_makes_no_view_call(gateway) -> None:
    """대조군 — 직전 턴 대상이 없으면 A-1대로 검색만 하고 보기 호출 0 · 「찾지 못함」."""
    gw, _ = gateway(search={})
    res = await _run(["apm.app_health"], [{"text": "abc-was", "kind": "instance"}])
    assert _view_calls(gw) == []
    assert res["apm_query"]["source_status"]["status"] == "not_queried"
    assert any("찾지 못" in t for t in _kinds(res, disc.APM_UNRESOLVED_CONDITION))


async def test_unresolved_text_does_not_query_the_previous_turn_host(gateway) -> None:
    gw, _ = gateway(search={}, hosts={"web01": [_inst(9, "old-was", "web01")]})
    res = await _run(["apm.app_health"], [{"text": "abc-was", "kind": "instance"}],
                     isolated=_previous_turn("abc-was 응답시간 알려줘"))
    assert gw.searched("query") == ["abc-was"]
    assert _view_calls(gw) == [], _view_calls(gw)
    assert res["apm_query"]["hostnames"] == []


async def test_resolved_text_does_not_widen_to_the_previous_turn_host(gateway) -> None:
    gw, _ = gateway(search={"abc-was01": ([_inst(1, "abc-was01", "h1")], [])},
                    hosts={"web01": [_inst(9, "old-was", "web01")]})
    await _run(["apm.app_health"], [{"text": "abc-was01", "kind": "instance"}],
               isolated=_previous_turn("abc-was01 응답시간 알려줘"))
    calls = _view_calls(gw)
    assert [a.get("instance_name") for _, a in calls] == ["abc-was01"], calls
    assert all(a.get("hostname") != "web01" for _, a in calls), calls


# ── V130-2 (Minor) 같은 이름·같은 id · 두 도메인 → 같은 인자 두 번 ─────────────────

async def test_same_name_and_id_in_two_domains_is_called_once(gateway) -> None:
    gw, _ = gateway(search={"pay-was": ([_inst(5, "pay-was", "h1", did=10),
                                         _inst(5, "pay-was", "h2", did=20)], [])})
    await _run(["apm.app_health"], [{"text": "pay-was", "kind": "instance"}])
    calls = gw.named("apm_app_health")
    keys = [(a["instance_name"], a["instance_id"], tuple(a["source_ids"])) for a in calls]
    assert len(keys) == len(set(keys)), keys


# ── V130-3 (Minor) E6 행 상한 도달이 사용자에게 보이지 않는다 ──────────────────────

class _TruncatedEdge(_Edge):
    """행 상한에 닿은 E6 — 장부 사유·단계 `truncated`만 상한을 말한다(실 링크 모양)."""

    async def __call__(self, terms, *, app_config, authorized_db_ids):
        self.seen.append({"terms": list(terms), "authorized_db_ids": authorized_db_ids})
        found = {t: [{"hostname": "h1", "server_name": "결제서버", "db_id": "polestar_cm_gp",
                      "field": "name"}] for t in terms}
        entries = [LinkEntry(t, "business_name", "E6", LINKED, grade="one", value="h1",
                             reason="조회 상한 1000행에 닿아 일부만 확인") for t in terms]
        steps = [{"edge": "E6", "from": "business_name", "to": "hostname", "owner": "polestar",
                  "keys": len(terms), "db_ids": ["polestar_cm_gp"], "truncated": True}]
        return found, entries, steps


async def test_edge_row_cap_reaches_the_user(gateway) -> None:
    gw, _ = gateway(_TruncatedEdge(), hosts={"h1": [_inst(1, "pay-was", None)]})
    res = await _run(["apm.app_health"], [{"text": "결제", "kind": "business"}])
    assert gw.named("apm_app_health"), "전제: E6 → E1r로 인스턴스가 풀렸다"
    texts = [d["text"] for d in res.get("disclosures") or []]
    texts.append(str((res.get("organized_data") or {}).get("summary") or ""))
    assert any("상한" in t or "일부만" in t for t in texts), texts


# ── V130-4 (Minor · B-7 잔여) 재계획 task의 targets ─────────────────────────────

def test_replanned_apm_task_keeps_targets() -> None:
    targets = [{"text": "abc-was01", "kind": "instance"}]
    (task,) = _assign_ids([{"agent": "apm_query", "sub_query": "abc-was01 응답시간",
                            "views": ["apm.app_health"], "targets": targets}],
                          existing=[{"task_id": "t1", "order": 1}])
    assert task.get("targets") == targets, task


# ── V130-6 (Minor) 파서 서버명 대상의 max_targets 절단 ─────────────────────────

async def test_every_unlinked_server_name_is_searched_beyond_max_targets(gateway,
                                                                         monkeypatch) -> None:
    async def unlinked(refs, *, consumer, app_config):
        return [], [LinkEntry(str(r.server_name), "server_name", "E2", UNLINKED, grade="none")
                    for r in refs if r.server_name], []

    monkeypatch.setattr(aq, "link_hostnames", unlinked)
    names = [f"was{i:02d}" for i in range(1, 13)]
    gw, _ = gateway(search={n: ([_inst(i, n)], []) for i, n in enumerate(names, 1)})
    await _run(["apm.app_health"], isolated=_isolated(
        [{"field": "server_name", "op": "=", "value": n} for n in names]),
        cfg=_cfg(max_targets=10))
    assert gw.searched("query") == names


# ── V130-7 (Minor) 길이 초과 대상 텍스트 → 첫 홉 ───────────────────────────────

async def test_over_length_target_text_does_not_fall_back_to_the_first_hop(gateway) -> None:
    gw, _ = gateway(listing=[_inst(1, "first-was", "h1")],
                    hosts={"h1": [_inst(1, "first-was", "h1")]})
    await _run(["apm.app_health"], [{"text": "x" * 201, "kind": "instance"}])
    assert gw.listings() == [] and _view_calls(gw) == [], gw.calls


# ── 경계: 관측 소스 권한 ────────────────────────────────────────────────────────

async def test_source_permission_denial_makes_no_resolution_call(gateway, monkeypatch) -> None:
    """APM 소스 권한이 없으면 대상 텍스트 해석(게이트웨이 검색·업무명 간선)도 하지 않는다."""
    gw, edge = gateway(search={"abc": ([_inst(1, "abc-was01", "h1")], [])})
    opened: list[str] = []
    factory = gw.factory()

    def counting(url, headers):
        opened.append(url)
        return factory(url, headers)

    monkeypatch.setattr(aq, "_SESSION_FACTORY", counting)
    res = await _run(["apm.app_health"], [{"text": "결제", "kind": "auto"}],
                     isolated=_isolated(user_role="user", allowed_sources=["polestar"],
                                        allowed_db_ids=["polestar_cm_gp"]))
    assert opened == [] and gw.calls == [] and edge.seen == []
    assert res.get("routing_intent") == "access_denied", res
    assert "제니퍼" not in str(res.get("final_response") or "")


async def test_admin_role_ignores_source_list(gateway) -> None:
    gw, _ = gateway(search={"abc": ([_inst(1, "abc-was01", "h1")], [])})
    await _run(["apm.app_health"], [{"text": "abc", "kind": "instance"}],
               isolated=_isolated(user_role="admin", allowed_sources=["polestar"]))
    assert [a["instance_name"] for a in gw.named("apm_app_health")] == ["abc-was01"]


# ── 경계: TargetRef 완화가 폴스타 SQL 경로로 새지 않는다 ─────────────────────────

def test_apm_only_targets_never_reach_server_consumers() -> None:
    """APM 인스턴스만 지목한 대상은 서버 소비처 해소 결과에 없다(사유 `apm_only_target`)."""
    res = resolve_targets(filter_conditions=[], db_id="polestar_cm_gp", max_targets=10,
                          prior_targets=[{"apm_instance_name": "abc-was02",
                                          "apm_instance_id": "1002", "apm_source_id": "default"},
                                         {"hostname": "h1", "apm_instance_name": "abc-was01"}])
    assert [(t.hostname, t.server_name) for t in res.targets] == [("h1", None)]
    assert [d["reason"] for d in res.dropped] == [REASON_APM_ONLY]


def test_apm_answer_rows_yield_only_hostname_targets_downstream() -> None:
    """해석 인스턴스 답 행(hostname 빈 행 포함)을 선행 결과로 받은 후속 task는 hostname만 대상이다 —
    인스턴스 이름이 서버 이름(`server_name`)으로 바뀌어 폴스타 SQL에 들어가지 않는다."""
    rows = {"t1": [
        {"instance_name": "abc-was02", "hostname": "", "source_id": "default", "domain_id": 2000,
         "instance_id": 1002, "target_text": "abc", "match_evidence": "제니퍼 인스턴스 이름"},
        {"instance_name": "abc-was01", "hostname": "was-host01", "source_id": "default",
         "domain_id": 1000, "instance_id": 1001},
    ]}
    targets = _targets_from_prior_rows(rows, db_id=None, max_targets=10)
    assert targets == [{"server_name": None, "hostname": "was-host01", "ip": None, "db_id": None}]
