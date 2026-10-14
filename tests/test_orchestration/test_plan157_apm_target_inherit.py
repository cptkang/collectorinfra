"""plans/157 W3 · 결함 ② — 대상을 생략한 APM 후속 턴이 직전 대상을 잇는다(130 M-5 대상 칸).

고정하는 계약(G-3 (a) 결정적 AND · G-4 (a) 스레드 키 `apm_source_scope.last_targets`):
  1. 기록 — 대상별 보기(`required_input`)로 조회한 해석 인스턴스만 `[{text, kind, source_id}]`로
     메타 `source_selection`에 싣고 집계기가 스레드 키로 옮긴다. 목록·설정 보기 턴 · 대상 텍스트
     없는 턴은 싣지 않는다(키 없음 — 종전 스코프 바이트 불변 · C-2).
  2. 읽기 — ① 직전 턴이 APM(`previous_sources`) + 기록 있음 ② 이번 턴 대상 없음 ③ 보기 전부 대상별
     ④ 소스가 승계(사다리 단 3)일 때만 기록을 분해 `targets`처럼 실어 종전 해석 경로를 탄다 —
     의무 고지 1줄(`apm_untargeted_scope`) · 첫 홉(전 인스턴스·부하 순위) 없음(C-1).
  3. 가드 — 새 대상(C-3) · 새 소스 단어(C-4) · 칩 해제(C-5) · 목록/설정 보기 · 직전 턴 비APM ·
     복합 계획이면 승계 없음 = 기록 유무와 무관하게 결과 동일(G-6 (a) 비트 동일). 승계 값을 내지
     않은 APM 턴(되묻기 등)은 집계기가 기록만 뺀다. 응답 칸에는 기록을 싣지 않는다.
  4. 종단 — 실제 `build_graph()` · 2단 `intent_orchestration` · 가짜 LLM · 같은 thread_id 2턴.
실 LLM·실 제니퍼·실 DB 0.
"""

from __future__ import annotations

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
from src.orchestration.result_aggregator import _apply_apm_source_state
from src.routing import apm_source_select as sel
from src.state import create_followup_input
from tests.test_orchestration import apm_batch_mock
from tests.test_orchestration.test_plan134_w34_verify_real import _NoEdge
from tests.test_plan122_t_verify import build_env  # noqa: F401 — 픽스처

pytestmark = pytest.mark.apm_source_ladder

NOW = datetime(2026, 10, 14, 10, 0, 0)
INHERIT_HEAD = "직전 대상 "
#: 인스턴스 이름 검색표 — 검색어 → (소스, 인스턴스 id, 인스턴스 이름)
SEARCH = {
    "abc": [("cm_prd", 1, "abc-was01")],
    "abc-was01": [("cm_prd", 1, "abc-was01")],
    "xyz": [("cm_prd", 7, "xyz-was07")],
    "bnk": [("b0", 3, "bnk-was03")],
}


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-14T10:00:00+09:00",
            "source_kind": "apm_api", "source": "apm", "tool": tool, "limits": [], **extra}


class _Gateway:
    """모의 게이트웨이 — 인스턴스 검색(`query`)은 `SEARCH` 표 · 목록·순위·데이터 도구는 고른
    소스마다 인스턴스 행 1개(전 인스턴스 = `fleet-*`)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]

    def searched(self) -> list[str]:
        return [a["query"] for a in self.named("apm_instance_map") if "query" in a]

    def tools(self) -> list[str]:
        return [n for n, _ in self.calls]

    def _single(self, name: str, args: dict) -> dict:
        picked = args.get("source_ids") or ["b0", "cm_prd", "cm_stg"]
        if name == "apm_instance_map" and "query" in args:
            rows = [{"source_id": s, "instance_id": i, "instance_name": n, "domain_id": 10,
                     "hostname": f"h-{n}"} for s, i, n in SEARCH.get(args["query"], [])
                    if s in picked]
            return _env(name, rows)
        if args.get("instance_name"):
            rows = [{"source_id": args["source_ids"][0], "instance_id": args.get("instance_id"),
                     "instance_name": args["instance_name"], "tps": 1.0}]
        else:
            rows = [{"source_id": s, "instance_id": 90 + k, "instance_name": f"fleet-{s}",
                     "hostname": f"fleet-{s}", "domain_id": 10, "tps": 2.0}
                    for k, s in enumerate(picked)]
        return _env(name, rows, sources=[{"source_id": s, "status": "ok", "reason": ""}
                                         for s in picked])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        env = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())

    def install() -> _Gateway:
        gw = _Gateway()
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _isolated(query: str, *, scope: dict | None = None, prev: tuple[str, ...] = ("apm",),
              filters: list | None = None) -> dict:
    return {"parsed_requirements": {"filter_conditions": filters or [], "time_range": None},
            "conversation_context": {"previous_sources": list(prev)} if prev else {},
            "thread_id": "th-157", "user_id": "alice", "original_user_query": query,
            **({"apm_source_scope": scope} if scope is not None else {})}


async def _run(query: str, views: list[str], *, targets: Any = None, isolated: dict | None = None,
               view_args: dict | None = None, sources: list[str] | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "sub_query": query,
                            "views": views}
    if targets is not None:
        task["targets"] = targets
    if sources is not None:  # 분해 LLM `sources`(실 shape — 앞 턴 맥락의 소스를 다시 낸다)
        task["sources"] = sources
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, isolated or _isolated(query), llm=None,
                                  app_config=_cfg(), now=NOW)


def _notices(res: dict, kind: str) -> list[str]:
    return [d["text"] for d in res.get("disclosures") or [] if d["kind"] == kind]


def _inherit_notices(res: dict) -> list[str]:
    return [t for t in _notices(res, disc.APM_UNTARGETED_SCOPE) if t.startswith(INHERIT_HEAD)]


LAST_ABC = [{"text": "abc-was01", "kind": "instance", "source_id": "cm_prd"}]
SCOPE_ABC = {"ids": ["cm_prd"], "basis": "named", "last_target_sources": ["cm_prd"],
             "last_targets": LAST_ABC}
SCOPE_NONE = {"ids": ["cm_prd"], "basis": "named", "last_target_sources": ["cm_prd"]}


def _normalized(res: dict) -> dict:
    """결과 비교용 — JSON 왕복으로 정규화한다(튜플·목록 차이 없이 값만 비교)."""
    text = json.dumps(res, ensure_ascii=False, sort_keys=True, default=str)
    return json.loads(text)


# ── 1. 판정 함수(G-3 AND) ───────────────────────────────────────────────────────

def _decision(**kw: Any) -> sel.SourceDecision:
    base: dict[str, Any] = {"action": "query", "source_ids": ("cm_prd",), "basis": "inherited"}
    base.update(kw)
    return sel.SourceDecision(**base)


def _by_id() -> dict[str, aq.ViewSpec]:
    return {v.id: v for v in aq.apm_views()}


def test_inherited_targets_all_conditions_true() -> None:
    iso = _isolated("느린 거래도 10분치", scope=SCOPE_ABC)
    got = aq.inherited_targets({}, iso, ["apm.slow_tx"], _by_id(), _decision())
    assert got == [{"text": "abc-was01", "kind": "instance"}]


@pytest.mark.parametrize("case", [
    "no_record", "prev_not_apm", "task_targets", "host_filter", "list_view", "settings_view",
    "mixed_views", "source_named", "source_selected", "not_query", "other_source", "composite",
    "llm_other_source", "llm_outside_scope",
])
def test_inherited_targets_each_condition_off_is_empty(case: str) -> None:
    task: dict[str, Any] = {}
    views = ["apm.slow_tx"]
    decision = _decision()
    iso = _isolated("느린 거래도 10분치", scope=SCOPE_ABC)
    if case == "no_record":
        iso = _isolated("느린 거래도 10분치", scope=SCOPE_NONE)
    elif case == "prev_not_apm":
        iso = _isolated("느린 거래도 10분치", scope=SCOPE_ABC, prev=("polestar",))
    elif case == "task_targets":
        task = {"targets": [{"text": "xyz", "kind": "instance"}]}
    elif case == "host_filter":
        iso = _isolated("web01 느린 거래", scope=SCOPE_ABC,
                        filters=[{"field": "hostname", "op": "=", "value": "web01"}])
    elif case == "list_view":
        views = ["apm.instances"]
    elif case == "settings_view":
        views = ["apm.environment"]
    elif case == "mixed_views":
        views = ["apm.slow_tx", "apm.ranking"]
    elif case == "source_named":  # 소스 단어(근거 있음) — 같은 소스여도 보수적으로 잇지 않는다
        decision = _decision(basis="named", evidence=("김포",))
    elif case == "llm_other_source":  # 단어 없이 분해 LLM이 다른 소스를 냈다
        decision = _decision(basis="named", source_ids=("b0",))
    elif case == "llm_outside_scope":  # 승계 값 밖 소스가 섞였다
        decision = _decision(basis="named", source_ids=("cm_prd", "b0"))
    elif case == "source_selected":
        decision = _decision(basis="selected")
    elif case == "not_query":
        decision = _decision(action="ask")
    elif case == "other_source":
        decision = _decision(source_ids=("b0",))
    elif case == "composite":
        iso = {**iso, "is_composite": True}
    assert aq.inherited_targets(task, iso, views, _by_id(), decision) == []


def test_inherited_targets_llm_same_source_counts_as_unchanged() -> None:
    iso = _isolated("느린 거래도 10분치", scope=SCOPE_ABC)
    got = aq.inherited_targets({}, iso, ["apm.slow_tx"], _by_id(), _decision(basis="named"))
    assert got == [{"text": "abc-was01", "kind": "instance"}]
    star = _isolated("느린 거래도 10분치", scope={**SCOPE_ABC, "ids": ["*"]})
    assert aq.inherited_targets({}, star, ["apm.slow_tx"], _by_id(), _decision(basis="named"))


def test_per_target_views_reuses_view_meta() -> None:
    by_id = _by_id()
    per_target = {v for v in by_id if aq._per_target_views([v], by_id)}
    # 대상 필수 보기만 — 목록(첫 홉)·지표 목록·설정·관리(optional)·이름·참조·전 대상 보기는 아니다
    assert {"apm.app_health", "apm.slow_tx", "apm.events"} <= per_target
    assert not per_target & {"apm.instances", "apm.metrics", "apm.environment", "apm.event_rules",
                             "apm.jennifer_server", "apm.process", "apm.service", "apm.trace",
                             "apm.ranking", "apm.users", "apm.fleet_events"}
    assert aq._per_target_views([], by_id) is False


# ── 2. 기록(처리기 메타 → 집계기 스레드 키) ───────────────────────────────────────

async def test_targeted_turn_records_last_targets(gateway) -> None:
    gw = gateway()
    q = "김포 abc 최근 1시간 이벤트"
    res = await _run(q, ["apm.events"], targets=[{"text": "abc", "kind": "instance"}])
    assert not res.get("error"), res.get("error")
    picked = res["apm_query"]["source_selection"]
    assert picked["scope"] == {"ids": ["cm_prd"], "basis": "named"}
    assert picked[aq.LAST_TARGETS_KEY] == LAST_ABC
    assert gw.searched() == ["abc"]
    assert not _inherit_notices(res)


@pytest.mark.parametrize("views,targets", [
    (["apm.instances"], [{"text": "abc", "kind": "instance"}]),  # 목록 보기(대상 텍스트 있어도)
    (["apm.instances"], None),  # 「제니퍼 인스턴스 리스트」
    (["apm.environment"], None),  # 설정 보기
    (["apm.app_health"], None),  # 대상별 보기지만 대상 텍스트 없음(첫 홉)
])
async def test_list_settings_untargeted_turns_record_nothing(gateway, views, targets) -> None:
    gateway()
    res = await _run("김포 제니퍼 조회", views, targets=targets)
    assert not res.get("error"), res.get("error")
    assert aq.LAST_TARGETS_KEY not in res["apm_query"]["source_selection"]


def _agg_state(meta: dict) -> dict:
    return {"task_plan": [{"task_id": "t1", "agent": "apm_query", "order": 1}],
            "task_results": {"t1": {aq.META_KEY: meta}}}


def test_aggregator_lifts_last_targets_only_when_present() -> None:
    sel_meta = {"scope": {"ids": ["cm_prd"], "basis": "named"}, "last_target_sources": ["cm_prd"]}
    out = _apply_apm_source_state({}, _agg_state({"source_selection": {
        **sel_meta, aq.LAST_TARGETS_KEY: LAST_ABC}}))
    assert out["apm_source_scope"] == SCOPE_ABC
    out = _apply_apm_source_state({}, _agg_state({"source_selection": sel_meta}))
    assert out["apm_source_scope"] == SCOPE_NONE, "기록이 없으면 키를 싣지 않는다(종전 모양)"


@pytest.mark.parametrize("res", [
    {aq.SOURCE_CLARIFICATION_KEY: {"options": [], "original_query": "q"},
     aq.META_KEY: {"source_selection": {"action": "ask", "scope": None}}},  # 소스 되묻기
    {"error": "x", aq.META_KEY: {"views": []}},  # 보기 미선택·엔드포인트 없음
    {"error": "denied"},  # 인가 거부
    None,  # 계획에는 있으나 결과 없음
])
def test_aggregator_strips_record_when_apm_turn_wrote_no_scope(res) -> None:
    """APM task가 있었는데 승계 값을 내지 않은 턴 — 앞 스코프에서 `last_targets`만 뺀다(C-2)."""
    state = {"task_plan": [{"task_id": "t1", "agent": "apm_query", "order": 1}],
             "task_results": {"t1": res} if res is not None else {},
             "apm_source_scope": SCOPE_ABC}
    out = _apply_apm_source_state({}, state)
    assert out["apm_source_scope"] == SCOPE_NONE


def test_aggregator_keeps_bytes_when_nothing_to_strip() -> None:
    # 기록 없는 앞 스코프 · APM task 없는 턴은 쓰지 않는다(종전 바이트 불변)
    ask_free = {"task_plan": [{"task_id": "t1", "agent": "apm_query", "order": 1}],
                "task_results": {"t1": {"error": "x"}}, "apm_source_scope": SCOPE_NONE}
    assert _apply_apm_source_state({}, ask_free) == {}
    polestar = {"task_plan": [{"task_id": "t1", "agent": "data_query", "order": 1}],
                "task_results": {"t1": {"query_results": []}}, "apm_source_scope": SCOPE_ABC}
    assert _apply_apm_source_state({}, polestar) == {}


def test_response_fields_drop_internal_last_targets() -> None:
    """응답·SSE done에는 내부 승계 재료(인스턴스 이름)를 싣지 않는다 — 칩 칸은 그대로."""
    from src.api.routes.query import _apm_source_fields

    assert _apm_source_fields({"apm_source_scope": SCOPE_ABC}) == {"apm_source_scope": SCOPE_NONE}
    assert _apm_source_fields({"apm_source_scope": SCOPE_NONE}) == {"apm_source_scope": SCOPE_NONE}
    assert _apm_source_fields({}) == {}


# ── 3. 읽기 — C-1 · 가드 C-2~C-5 ────────────────────────────────────────────────

async def test_c1_ellipsis_followup_inherits_previous_instance(gateway) -> None:
    gw = gateway()
    q = "느린 거래도 10분치"
    res = await _run(q, ["apm.slow_tx"], isolated=_isolated(q, scope=SCOPE_ABC))
    assert not res.get("error"), res.get("error")
    assert gw.searched() == ["abc-was01"], "직전 대상을 종전 해석 경로(인스턴스 이름 검색)로 푼다"
    assert "apm_fleet" not in gw.tools(), "부하 순위 첫 홉(대상 미지정) 없음"
    listing = [a for a in gw.named("apm_instance_map") if "query" not in a]
    assert not listing, "전 인스턴스 목록 없음"
    calls = apm_batch_mock.expanded(gw.calls, "apm_slow_transactions")
    assert [(c["instance_name"], c["source_ids"]) for c in calls] == [("abc-was01", ["cm_prd"])]
    assert all(a.get("source_ids") == ["cm_prd"] for _, a in gw.calls), "승계 소스 안에서만"
    assert _inherit_notices(res) == [
        "직전 대상 1건(abc-was01)을 이어서 조회했습니다 — 다른 대상은 이름을 말해 주세요."]
    # 승계 턴도 대상별 조회라 다음 후속의 재료를 다시 남긴다(연쇄)
    assert res["apm_query"]["source_selection"][aq.LAST_TARGETS_KEY] == LAST_ABC


async def test_c1_llm_restates_same_source_still_inherits(gateway) -> None:
    """실 shape(E1 `APM-SRC-inherit#1`) — 분해가 소스 단어 없이 앞 턴 소스 `sources: [cm_prd]`를
    다시 낸다(사다리 단 2 · 근거 단어 0). 같은 소스 재언급은 소스 변경이 아니다."""
    gw = gateway()
    q = "느린 거래도 10분치"
    res = await _run(q, ["apm.slow_tx"], isolated=_isolated(q, scope=SCOPE_ABC),
                     sources=["cm_prd"])
    assert res["apm_query"]["source_selection"]["basis"] == "named"
    assert res["apm_query"]["source_selection"]["evidence"] == []
    assert gw.searched() == ["abc-was01"] and "apm_fleet" not in gw.tools()
    calls = apm_batch_mock.expanded(gw.calls, "apm_slow_transactions")
    assert [(c["instance_name"], c["source_ids"]) for c in calls] == [("abc-was01", ["cm_prd"])]
    assert len(_inherit_notices(res)) == 1


@pytest.mark.parametrize("q,sources,sent", [
    ("느린 거래도 10분치", ["b0"], ["b0"]),  # 분해가 다른 소스를 냈다(단어 없음)
    ("은행존 느린 거래도 10분치", ["cm_prd"], ["b0"]),  # 다른 소스 단어(단어가 LLM을 이긴다)
    ("김포 느린 거래도 10분치", None, ["cm_prd"]),  # 같은 소스라도 사용자가 단어로 말함(보수)
])
async def test_source_named_differently_or_spoken_does_not_inherit(gateway, q, sources,
                                                                   sent) -> None:
    gw = gateway()
    res = await _run(q, ["apm.slow_tx"], isolated=_isolated(q, scope=SCOPE_ABC), sources=sources)
    assert {tuple(a.get("source_ids") or ()) for _, a in gw.calls} == {tuple(sent)}
    assert gw.searched() == [] and "apm_fleet" in gw.tools()
    assert not _inherit_notices(res)


async def test_c2_after_list_turn_no_record_means_untargeted(gateway) -> None:
    gw = gateway()
    q = "응답시간"
    res = await _run(q, ["apm.app_health"], isolated=_isolated(q, scope=SCOPE_NONE))
    assert gw.searched() == [], "목록 턴은 기록을 남기지 않아 승계할 것이 없다"
    assert "apm_fleet" in gw.tools(), "종전대로 대상 미지정 첫 홉"
    assert not _inherit_notices(res)


async def test_c3_new_target_overrides_record(gateway) -> None:
    gw = gateway()
    q = "xyz 응답시간"
    res = await _run(q, ["apm.app_health"], targets=[{"text": "xyz", "kind": "instance"}],
                     isolated=_isolated(q, scope=SCOPE_ABC))
    assert gw.searched() == ["xyz"]
    calls = apm_batch_mock.expanded(gw.calls, "apm_app_health")
    assert [c["instance_name"] for c in calls] == ["xyz-was07"]
    assert not _inherit_notices(res)
    assert res["apm_query"]["source_selection"][aq.LAST_TARGETS_KEY] == [
        {"text": "xyz-was07", "kind": "instance", "source_id": "cm_prd"}], "새 대상으로 덮어쓴다"


async def test_c4_new_source_word_does_not_inherit(gateway) -> None:
    gw = gateway()
    q = "은행존 제니퍼 느린 거래"
    res = await _run(q, ["apm.slow_tx"], isolated=_isolated(q, scope=SCOPE_ABC))
    assert gw.searched() == []
    assert all(a.get("source_ids") == ["b0"] for _, a in gw.calls), gw.calls
    assert "apm_fleet" in gw.tools()
    assert not _inherit_notices(res)


async def test_c5_reset_clears_record_and_no_inherit(gateway) -> None:
    # 칩 × — 입력 델타가 스레드 키를 키째 비운다(`last_targets` 포함)
    delta = create_followup_input("느린 거래도 10분치", reset_apm_source_scope=True)
    assert "apm_source_scope" in delta and delta["apm_source_scope"] is None
    gw = gateway()
    q = "김포 느린 거래도 10분치"  # 해제 뒤 같은 후속(승계 값 없음 — 소스는 단어로)
    res = await _run(q, ["apm.slow_tx"], isolated=_isolated(q, scope=None))
    assert gw.searched() == [] and "apm_fleet" in gw.tools()
    assert not _inherit_notices(res)


async def test_previous_turn_not_apm_does_not_inherit(gateway) -> None:
    gw = gateway()
    q = "느린 거래도 10분치"
    res = await _run(q, ["apm.slow_tx"],
                     isolated=_isolated(q, scope=SCOPE_ABC, prev=("polestar",)))
    assert gw.searched() == [] and "apm_fleet" in gw.tools()
    assert not _inherit_notices(res)


@pytest.mark.parametrize("q,views,prev", [
    ("제니퍼 인스턴스 리스트", ["apm.instances"], ("apm",)),  # 목록 보기
    ("환경 설정 보여줘", ["apm.environment"], ("apm",)),  # 설정·관리 보기
    ("이벤트 룰 보여줘", ["apm.event_rules"], ("apm",)),
    ("느린 거래도 10분치", ["apm.slow_tx"], ("polestar",)),  # 직전 턴 비APM
    ("느린 거래도 10분치", ["apm.slow_tx", "apm.ranking"], ("apm",)),  # 보기 섞임
])
async def test_record_is_ignored_bit_identical(gateway, q, views, prev) -> None:
    """승계 조건 밖이면 기록이 있어도 없을 때와 결과·호출이 같다(G-6 (a))."""
    gw = gateway()
    with_record = await _run(q, views, isolated=_isolated(q, scope=SCOPE_ABC, prev=prev))
    calls_with = list(gw.calls)
    gw = gateway()
    without = await _run(q, views, isolated=_isolated(q, scope=SCOPE_NONE, prev=prev))
    assert calls_with == gw.calls
    assert _normalized(with_record) == _normalized(without)


# ── 4. 종단 — 실제 그래프 · 2단 · 같은 thread_id 2턴 ─────────────────────────────

@pytest.mark.asyncio
async def test_e2e_two_turns_same_thread_inherits_target(build_env, monkeypatch) -> None:  # noqa: F811
    import importlib

    import src.graph as graph_module

    ip = importlib.import_module("src.orchestration.intent_planner")
    real_build = graph_module.build_graph
    # 분해 실 shape(E1 trace) — 후속 턴도 앞 턴 맥락의 소스를 `sources`로 다시 낸다
    plans = {
        "김포 abc 최근 1시간 이벤트": {"views": ["apm.events"], "sources": ["cm_prd"],
                                "targets": [{"text": "abc", "kind": "instance"}]},
        "느린 거래도 10분치": {"views": ["apm.slow_tx"], "sources": ["cm_prd"], "targets": []},
    }
    seen_cfg: dict[str, Any] = {}

    async def _planner(state: Any, **kw: Any) -> dict:
        q = state["user_query"]
        result = {"task_plan": [{
            "task_id": "t1", "agent": "apm_query", "sub_query": q, "depends_on": [],
            "input_from": [], "order": 1, "status": "pending", **plans[q],
        }], "is_composite": False, "current_node": "intent_planner"}
        # 계획 출구의 실제 소스 기록(`turn_sources` → 다음 턴 `previous_sources`)
        ip._apply_source_selection(result, state, seen_cfg["cfg"])
        return result

    def _build(cfg: Any, **kw: Any) -> Any:
        monkeypatch.setattr(graph_module, "intent_planner", _planner)
        cfg.dbhub.source_endpoints = {"apm": "http://127.0.0.1:9/sse"}
        seen_cfg["cfg"] = cfg
        return real_build(cfg, **kw)

    monkeypatch.setattr(graph_module, "build_graph", _build)
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    env, _ = build_env("t2", ["polestar"])
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    thread = "m157-inherit"

    first = await env.run("김포 abc 최근 1시간 이벤트", thread)
    assert first.get("clarification") is None and first.get("apm_source_clarification") is None
    assert gw.searched() == ["abc"]
    assert first["apm_source_scope"]["last_targets"] == LAST_ABC
    assert first["turn_sources"] == ["apm"]

    gw.calls.clear()
    second = await env.run("느린 거래도 10분치", thread, followup=True)
    assert second.get("apm_source_clarification") is None, "소스는 승계(되묻기 없음)"
    assert gw.searched() == ["abc-was01"]
    assert "apm_fleet" not in gw.tools()
    calls = apm_batch_mock.expanded(gw.calls, "apm_slow_transactions")
    assert [(c["instance_name"], c["source_ids"]) for c in calls] == [("abc-was01", ["cm_prd"])]
    assert calls[0].get("lookback_minutes") == 10, calls[0]
    final = second.get("final_response") or ""
    assert "직전 대상 1건(abc-was01)을 이어서 조회했습니다" in final, final
    assert env.executed == [], "폴스타 SQL 0"
