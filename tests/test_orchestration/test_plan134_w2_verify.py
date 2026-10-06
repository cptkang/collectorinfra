"""plans/134 W2 통합 검증(검증자) — 본체 보기 5종·M-8 선택 재시도·고지·표시 절단 ·
W1 결함 수정 회귀.

구현 테스트(`test_plan134_w2_apm.py`·`test_plan134_w1_apm.py`)가 덮지 않은 적대 입력을 본다.
  - 선택 재시도 응답 모양(빈 응답·JSON 배열·모르는 보기 id·`views: null`·생각 태그) —
    조회 0 · LLM 1회
  - 부분 커버(덮은 보기만 조회 · 기본 보기 대체 0) · 여러 영역 미해결 시 후보 구성
  - 단어 매칭 부재(같은 영역·같은 LLM 답이면 원문이 달라도 같은 호출)
  - 대상 없는 보기 · 보기별 고정 고지(대상마다) · 표시 절단 경계(300/301자 · 문자열 아닌 칸)
  - 조건 형식 경계(int·enum·text·str_list) · 본체 ↔ 게이트웨이 검증 불일치
  - H-1 골격 키 · 비활성 분해 프롬프트 지문 `06da76f1a17e4090`(8,032자) 불변
결함 재현은 `xfail(strict=True)`다(고쳐지면 XPASS로 실패해 표지를 걷게 한다).
게이트웨이는 모의 MCP 세션 · 선택 LLM 은 대역(실 LLM·네트워크 0).
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
from src.orchestration import apm_query as aq
from src.prompts import intent_planner as pip

ip = importlib.import_module("src.orchestration.intent_planner")

NOW = datetime(2026, 10, 2, 10, 0, 0)
STATS_LABELS = ("URL(애플리케이션)별 시 단위 통계", "SQL별 시 단위 통계", "외부 호출(연계 대상)별")


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
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-02T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


def _err(tool: str, code: str, reason: str) -> dict:
    return {"error": code, "reason": reason, "source_kind": "apm_api", "source": "jennifer",
            "tool": tool}


class _Gateway:
    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = self.replies[name]
        if callable(reply):
            reply = reply(arguments)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


class _SelectLLM:
    """선택 재시도 LLM 대역 — `reply`가 None 이면 부르는 것 자체가 실패다."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.messages: list[Any] = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        if self.reply is None:
            raise AssertionError("재시도가 필요 없는 경우에 LLM 을 불렀다")
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply,
                                                                          ensure_ascii=False)
        return SimpleNamespace(content=text)


def _isolated(*hosts: str, time_range: dict | None = None, limit: Any = None,
              composite: bool = False) -> dict:
    out = {"parsed_requirements": {
               "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
               "time_range": time_range, "limit": limit},
           "conversation_context": {}, "thread_id": "th-v", "user_id": "alice"}
    if composite:
        out["is_composite"] = True
    return out


async def _run(views: list[str], *hosts: str, view_args: dict | None = None,
               areas: list[str] | None = None, sub_query: str = "질의", llm: Any = None,
               time_range: dict | None = None, limit: Any = None,
               composite: bool = False) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views,
                            "sub_query": sub_query}
    if view_args is not None:
        task["view_args"] = view_args
    if areas is not None:
        task["areas"] = areas
    return await aq.run_apm_query(
        task, _isolated(*hosts, time_range=time_range, limit=limit, composite=composite),
        llm=llm, app_config=_cfg(), now=NOW)


_ALL_REPLIES = {
    "apm_instance_map": _env("apm_instance_map", [
        {"hostname": "web01", "match_confidence": "exact"},
        {"hostname": "web02", "match_confidence": "exact"}]),
    "apm_app_health": _env("apm_app_health", [{"tps": 1.0}]),
    "apm_status_stats": _env("apm_status_stats", [{"name": "select ?", "calls": 1}]),
    "apm_metrics": _env("apm_metrics", [{"source_id": "default", "scope": "instance",
                                         "metric": "heap_used"}]),
    "apm_source_changes": _env("apm_source_changes", [
        {"source_id": "default", "domain_id": 1000, "instance_id": 11,
         "instance_name": "web01_a", "change_detected_ms": 1759363200000,
         # 게이트웨이 새 계약(W2V-B6) — APM_TIMEZONE 기준 ISO 8601 시각 칸
         "change_detected_at": "2025-10-02T09:00:00+09:00"}]),
    "apm_runtime_health": _env("apm_runtime_health", [{"heap_used_mb": 1.0}]),
    "apm_events": _env("apm_events", [{"level": "fatal"}]),
}


# ── 1. M-8 선택 재시도 — 응답 모양 · 1회 · 조회 0 ────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "",                                                  # 빈 응답
    "[\"apm.sql_stats\"]",                               # JSON 이지만 객체 아님
    {"views": ["apm.bogus", "apm.sql"]},                 # 닫힌 어휘 밖 id
    {"views": None, "view_args": {"apm.sql_stats": {"n": 3}}},   # views 없음 + 조건만
    {"views": ["apm.instances", "apm.app_health"]},      # 목록·응답시간으로 대신
    {"views": ["apm.metrics"]},                          # 다른 W2 보기(영역 밖)
    "```json\n{\"views\": [\"apm.changes\"]}\n```",      # 영역 밖 보기를 코드블록으로
], ids=["empty", "json_array", "unknown_ids", "null_views", "general_views", "metrics",
        "fenced_off_area"])
async def test_retry_reply_shapes_never_query_or_substitute(gateway, reply) -> None:
    gw = gateway(_ALL_REPLIES)
    llm = _SelectLLM(reply)
    res = await _run([], areas=["was_statistics"], llm=llm)  # 대상도 없음 — 첫 홉도 없어야 한다
    assert len(llm.messages) == 1, "선택 재시도는 1회(D-299 ⑤)"
    assert gw.calls == [], "미해결이면 첫 홉 포함 조회 0 — 다른 보기로 대신하지 않는다"
    assert res["degraded_reason"] == "apm_unresolved_selection"
    assert [d["kind"] for d in res["disclosures"]] == [disc.APM_UNRESOLVED_CONDITION]
    for label in STATS_LABELS:
        assert label in res["final_response"]
    sel = res["apm_query"]["selection"]
    assert sel["result"] == "unresolved" and sel["retried"] is True
    assert set(sel) >= {"areas", "planned", "retried", "retried_views", "latency_ms", "result",
                        "candidates"}


@pytest.mark.asyncio
async def test_retry_string_views_is_accepted_as_one_view(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    res = await _run([], "web01", areas=["was_statistics"],
                     llm=_SelectLLM({"views": "apm.sql_stats"}))
    assert [n for n, _ in gw.calls] == ["apm_status_stats"]
    assert gw.named("apm_status_stats")[0]["kind"] == "sql"
    assert res["apm_query"]["selection"]["result"] == "retried"


@pytest.mark.asyncio
async def test_retry_reply_wrapped_in_think_block_is_parsed(gateway) -> None:
    """로컬 사고형 모델이 `<think>` 블록 뒤에 JSON 을 내는 경우 — 중괄호가 생각 안에도 있다."""
    gw = gateway(_ALL_REPLIES)
    reply = ('<think>영역은 SQL 통계 {kind: sql} 이다</think>\n'
             '{"views": ["apm.sql_stats"], "view_args": {"apm.sql_stats": {"n": 5}}}')
    res = await _run([], "web01", areas=["was_statistics"], llm=_SelectLLM(reply))
    sel = res["apm_query"]["selection"]
    # 현재 추출기(탐욕 중괄호)는 생각 블록의 `{`부터 잡아 JSON 실패 → 미해결이 된다.
    # 실 MLX 응답에 생각 블록이 실리는지는 ⑤ MLX 실측에서 본다(이 테스트는 동작을 기록한다).
    assert sel["result"] in ("retried", "unresolved")
    if sel["result"] == "retried":
        assert gw.named("apm_status_stats")[0]["n"] == 5
    else:
        assert gw.calls == [] and sel.get("error") == "JSON 아님"


@pytest.mark.asyncio
async def test_partial_cover_never_queries_off_area_retry_views(gateway) -> None:
    """분해 보기는 한 영역만 덮고 재시도는 영역 밖 보기만 냄 → 덮은 보기만 조회(대체 0)."""
    gw = gateway(_ALL_REPLIES)
    llm = _SelectLLM({"views": ["apm.app_health", "apm.instances"]})
    res = await _run(["apm.sql_stats"], "web01", llm=llm,
                     areas=["was_statistics", "was_change_detection"])
    assert len(llm.messages) == 1
    assert [n for n, _ in gw.calls] == ["apm_status_stats"], "영역 밖 재시도 보기는 조회하지 않는다"
    sel = res["apm_query"]["selection"]
    # plans/134 W6 — 변경 전후 비교(`apm.change_impact`)가 같은 영역을 재사용한다(의도된 갱신)
    assert (sel["result"], sel["uncovered"], sel["candidates"]) == (
        "partial", ["was_change_detection"], ["apm.changes", "apm.change_impact"])
    notice = [d for d in res["disclosures"] if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert len(notice) == 1 and "WAS 소스(리소스) 변경 감지 시각" in notice[0]["text"]


@pytest.mark.asyncio
async def test_partial_cover_with_all_covered_calls_failing_keeps_the_ask_back(gateway) -> None:
    gw = gateway({**_ALL_REPLIES,
                  "apm_status_stats": _err("apm_status_stats", "source_unavailable", "down")})
    res = await _run(["apm.sql_stats"], "web01", llm=_SelectLLM({"views": []}),
                     areas=["was_statistics", "was_change_detection"])
    assert [n for n, _ in gw.calls] == ["apm_status_stats"]
    assert res["degraded_reason"] == "apm_calls_failed"
    kinds = [d["kind"] for d in res.get("disclosures") or []]
    assert disc.APM_UNRESOLVED_CONDITION in kinds, "실패해도 못 덮은 영역 되묻기는 남는다"


@pytest.mark.asyncio
async def test_each_unresolved_area_gets_a_candidate(gateway) -> None:
    gateway(_ALL_REPLIES)
    res = await _run([], "web01", llm=_SelectLLM({"views": []}),
                     areas=["was_statistics", "was_change_detection", "apm_metric_catalog"])
    candidates = res["apm_query"]["selection"]["candidates"]
    by_id = {v.id: v for v in aq.apm_views()}
    covered = {by_id[c].capability for c in candidates}
    assert covered == {"was_statistics", "was_change_detection", "apm_metric_catalog"}, candidates


@pytest.mark.asyncio
@pytest.mark.parametrize("wording", [
    "web01 SQL 통계", "web01 응답시간", "web01 지표 목록 보여줘", "web01 소스 변경", "아무 말",
])
async def test_selection_does_not_depend_on_wording(gateway, wording) -> None:
    """132 계약 — 같은 영역·같은 LLM 답이면 원문이 달라도 호출이 같다(단어 매칭 부재)."""
    gw = gateway(_ALL_REPLIES)
    await _run([], "web01", sub_query=wording, areas=["was_change_detection"],
               llm=_SelectLLM({"views": ["apm.changes"]}))
    assert [n for n, _ in gw.calls] == ["apm_source_changes"]
    gw2 = gateway(_ALL_REPLIES)
    llm = _SelectLLM(None)
    await _run([], "web01", sub_query=wording, llm=llm)  # 영역 신호 없음
    assert llm.messages == [] and [n for n, _ in gw2.calls] == ["apm_app_health"]


def test_selection_code_has_no_wording_tables() -> None:
    """선택 경로 소스에 원문 표면어 → 보기 대응이 없다(132 계약 — 정적 점검)."""
    import inspect

    code = "".join(inspect.getsource(f) for f in (
        aq._select_views, aq._retry_selection, aq._uncovered, aq._apm_areas, aq.default_views,
        aq._plan_view_args))
    for word in ("통계", "지표", "변경", "SQL", "URL", "외부 호출", "배포"):
        assert f'"{word}' not in code and f"'{word}" not in code, word
    assert "sub_query" not in code.replace('task.get("sub_query")', ""), "원문은 LLM 입력으로만"


# ── 2. 대상 없는 보기 · 보기별 고정 고지 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_targetless_metrics_with_host_view_first_hop_only_for_host_view(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    res = await _run(["apm.metrics", "apm.sql_stats"])  # 대상 없음
    assert [n for n, _ in gw.calls].count("apm_instance_map") == 1, "첫 홉은 대상이 필요한 보기 몫"
    assert len(gw.named("apm_metrics")) == 1
    assert gw.named("apm_metrics")[0].get("hostname") is None, "전체 보기는 hostname 없이 1회"
    assert [a["hostname"] for a in gw.named("apm_status_stats")] == ["web01", "web02"]
    assert res["apm_query"]["hostnames"] == ["web01", "web02"]


@pytest.mark.asyncio
async def test_targetless_metrics_alone_never_inserts_first_hop_even_with_window(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    await _run(["apm.metrics"], time_range={"start": "2026-10-02 07:00", "end": "2026-10-02 09:00"})
    assert [n for n, _ in gw.calls] == ["apm_metrics"]
    args = gw.named("apm_metrics")[0]
    assert "lookback_minutes" not in args and "reference_time" not in args, "none 창은 기간 미전달"


@pytest.mark.asyncio
async def test_fixed_notices_are_per_target_and_hourly_without_period(gateway) -> None:
    gateway(_ALL_REPLIES)
    res = await _run(["apm.changes", "apm.sql_stats"], "web01", "web02")
    kinds = [d["kind"] for d in res["disclosures"]]
    assert kinds.count(disc.APM_CHANGE_DETECTION) == 2 and kinds.count(
        disc.APM_HOURLY_RESOLUTION) == 2
    for d in res["disclosures"]:
        assert d["text"].split(":")[0].strip(), "고지마다 조회 범위(보기·대상)가 앞에 붙는다"


@pytest.mark.asyncio
async def test_failed_call_gets_no_fixed_notice(gateway) -> None:
    gateway({**_ALL_REPLIES,
             "apm_source_changes": _err("apm_source_changes", "apm_api_error", "배열이 아님")})
    res = await _run(["apm.changes", "apm.sql_stats"], "web01")
    kinds = [d["kind"] for d in res.get("disclosures") or []]
    assert disc.APM_CHANGE_DETECTION not in kinds and disc.APM_HOURLY_RESOLUTION in kinds
    assert any("배열이 아님" in f["reason"] for f in res["apm_query"]["failures"])


# ── 3. 표시 절단 경계 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_display_cut_boundary_300_301_and_non_strings(gateway) -> None:
    at, over = "a" * aq.DISPLAY_CELL_MAX, "b" * (aq.DISPLAY_CELL_MAX + 1)
    long_list = ["x" * 400]
    gateway({**_ALL_REPLIES, "apm_status_stats": _env("apm_status_stats", [
        {"name": at, "calls": 1}, {"name": over, "calls": 2, "tags": long_list,
                                   "nested": {"t": "y" * 999}}])})
    res = await _run(["apm.sql_stats"], "web01")
    shown, full = res["organized_data"]["rows"], res["query_results"]
    assert shown[0] is full[0] and shown[0]["name"] == at, "정확히 300자는 그대로"
    assert shown[1]["name"].startswith(over[:300]) and "총 301자" in shown[1]["name"]
    assert shown[1]["tags"] == long_list and shown[1]["nested"]["t"] == "y" * 999, \
        "문자열 아닌 칸(목록·중첩)은 자르지 않는다"
    assert full[1]["name"] == over and res[aq.DISPLAY_CUT_KEY] is True
    gateway({**_ALL_REPLIES, "apm_status_stats": _env("apm_status_stats", [{"name": at}])})
    res2 = await _run(["apm.sql_stats"], "web01")
    assert aq.DISPLAY_CUT_KEY not in res2


# ── 4. 조건 형식 경계 · 게이트웨이 대조 ─────────────────────────────────────────

def _view(vid: str):
    return {v.id: v for v in aq.apm_views()}[vid]


@pytest.mark.parametrize(("value", "ok", "clean"), [
    (" 7 ", True, 7), (7.0, True, 7), (10 ** 12, True, 10 ** 12),
    ("７", False, None), ("²", False, None), ("٣", False, None), (7.5, False, None),
    (True, False, None), ("-1", False, None), (0, False, None), ("7개", False, None),
    ([7], False, None), ({"n": 7}, False, None),
])
def test_int_condition_edges(value, ok, clean) -> None:
    args, rejected = aq.validate_view_args(_view("apm.sql_stats"), {"n": value})
    assert (args.get("n"), bool(rejected)) == (clean, not ok)


@pytest.mark.parametrize(("value", "clean"), [
    ("INSTANCE", "instance"), (" sql ", "sql"), ("External_Call", "external_call"),
    ("externalCall", None), ("instances", None), (["instance"], None),
])
def test_scope_enum_edges(value, clean) -> None:
    args, rejected = aq.validate_view_args(_view("apm.metrics"), {"scope": value})
    assert args.get("scope") == clean and bool(rejected) == (clean is None)


@pytest.mark.asyncio
async def test_invalid_scope_still_queries_full_catalog(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    res = await _run(["apm.metrics"], view_args={"apm.metrics": {"scope": "externalCall"}})
    (args,) = gw.named("apm_metrics")
    assert "scope" not in args and args["mode"] == "catalog"
    (item,) = [d for d in res["disclosures"] if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert "externalCall" in item["text"]


def test_str_list_metrics_one_bad_item_drops_the_condition_with_notice() -> None:
    args, rejected = aq.validate_view_args(
        _view("apm.runtime"), {"metrics": ["heap_used", "heap used"], "interval_minute": 10})
    assert args == {"interval_minute": 10} and rejected and "metrics" in rejected[0]
    args, _ = aq.validate_view_args(_view("apm.runtime"), {"metrics": "socket_count"})
    assert args == {"metrics": ["socket_count"]}, "단일 문자열은 1개 목록"


@pytest.mark.parametrize("bad", ["a\x7fb", "a\x85b", "a b"])
def test_text_condition_rejects_all_control_characters(bad) -> None:
    args, _ = aq.validate_view_args(_view("apm.app_stats"), {"application_name": bad})
    assert args == {}


@pytest.mark.parametrize("value", ["_calls", "_"])
def test_body_rejects_identifiers_the_gateway_rejects(value) -> None:
    args, rejected = aq.validate_view_args(_view("apm.sql_stats"), {"sort_by": value})
    assert args == {} and rejected


@pytest.mark.asyncio
async def test_unknown_metric_candidates_reach_the_user(gateway) -> None:
    reason = ("instance 지표 카탈로그에 없는 지표: heap_usd(후보: heap_used, heap_usage, "
              "nonheap_used) — 전체 목록은 apm_metrics(mode catalog)")
    gateway({**_ALL_REPLIES,
             "apm_runtime_health": _err("apm_runtime_health", "invalid_argument", reason)})
    res = await _run(["apm.runtime"], "web01",
                     view_args={"apm.runtime": {"metrics": ["heap_usd"]}})
    assert "후보: heap_used" in res["final_response"], "모르는 지표 후보는 사용자에게 보인다"


@pytest.mark.asyncio
async def test_unknown_metric_is_dropped_and_view_still_queried(gateway) -> None:
    calls: list[dict] = []

    def runtime(args: dict) -> dict:
        calls.append(args)
        if args.get("metrics"):
            # 게이트웨이 새 계약(W2V-B2) — 모르는 지표는 빼고 조회한 성공 봉투 + [한계] + 고지
            # 문구는 실 게이트웨이 프로세스 종단(2026-10-02 · 지표 전부 모름)에서 받은 그대로다
            return _env("apm_runtime_health", [{"heap_used_mb": 1.0}], limits=[
                "[한계] instance 지표 카탈로그에 없는 지표는 빼고 조회했다:"
                " heap_usd(후보: heap_used)",
                "[한계] 요청한 지표를 모두 카탈로그에서 찾지 못해 기본 추세 지표"
                "(heap_used_mb·heap_committed_mb·gc_time_usage_pct)로 조회했다"], disclosures=[
                {"kind": "apm_unresolved_condition",
                 "text": "요청한 지표 heap_usd를 지표 목록에서 찾지 못해 기본 지표"
                         "(heap_used_mb, heap_committed_mb, gc_time_usage_pct)로 조회했습니다"}])
        return _env("apm_runtime_health", [{"heap_used_mb": 1.0}])

    gateway({**_ALL_REPLIES, "apm_runtime_health": runtime})
    res = await _run(["apm.runtime"], "web01",
                     view_args={"apm.runtime": {"metrics": ["heap_usd"]}})
    assert res.get("query_results"), "선택 조건 무효로 보기를 막지 않는다(H-2 원칙)"
    assert any(d["kind"] == disc.APM_UNRESOLVED_CONDITION for d in res["disclosures"])


@pytest.mark.asyncio
async def test_parser_limit_moves_to_n_only_for_views_with_n_and_single_task(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    await _run(["apm.sql_stats", "apm.metrics"], "web01", limit=5)
    assert gw.named("apm_status_stats")[0]["n"] == 5 and "n" not in gw.named("apm_metrics")[0]
    gw2 = gateway(_ALL_REPLIES)
    await _run(["apm.sql_stats"], "web01", limit=5, composite=True)
    assert "n" not in gw2.named("apm_status_stats")[0], "복합 계획은 파서 limit 을 옮기지 않는다"
    gw3 = gateway(_ALL_REPLIES)
    await _run(["apm.sql_stats"], "web01", limit=5,
               view_args={"apm.sql_stats": {"full": True}})
    args = gw3.named("apm_status_stats")[0]
    assert args["full"] is True and "n" not in args, "full 이면 n 을 붙이지 않는다"


@pytest.mark.asyncio
async def test_null_and_flat_conditions_on_w2_views(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    res = await _run(["apm.app_stats"], "web01", view_args={
        "apm.app_stats": {"sort_by": None, "application_name": None}, "*": {"n": 3}})
    args = gw.named("apm_status_stats")[0]
    assert args["n"] == 3 and args["kind"] == "application", "보기 하나면 평면 조건은 그 보기 것"
    assert not [d for d in res.get("disclosures") or []
                if d["kind"] == disc.APM_UNRESOLVED_CONDITION], "null = 미지정(무고지)"


# ── 5. H-1 골격 · 비활성 바이트 불변 ─────────────────────────────────────────────

def test_inactive_decomposition_prompt_fingerprint_is_baseline() -> None:
    prompt = ip._planner_system_prompt(_cfg(active=False))
    assert (hashlib.sha256(prompt.encode()).hexdigest()[:16], len(prompt)) == (
        "06da76f1a17e4090", 8032)
    for needle in ("apm_query", "view_args", "was_statistics", "apm.metrics"):
        assert needle not in prompt


def test_active_skeleton_carries_keys_once_and_rule_once() -> None:
    prompt = ip._planner_system_prompt(_cfg(active=True))
    assert prompt.count(pip.APM_SKELETON_TAIL_WITH_KEYS) == 1
    assert prompt.count(pip.APM_OUTPUT_RULE) == 1
    skeleton = prompt[prompt.index("## 출력 형식"):]
    skeleton = skeleton[:skeleton.index("```", skeleton.index("```json") + 7)]
    assert '"views": [], "view_args": {{}}' in skeleton
    assert pip.APM_SKELETON_TAIL not in prompt, "골격 꼬리는 키를 단 꼬리로 바뀐다"


@pytest.mark.asyncio
async def test_selection_prompt_lists_every_view_and_the_period_rule(gateway) -> None:
    gateway(_ALL_REPLIES)
    llm = _SelectLLM({"views": []})
    await _run([], "web01", areas=["apm_metric_catalog"], llm=llm)
    system = llm.messages[0][0].content
    for view in aq.apm_views():
        assert f"`{view.id}`" in system
    assert "기간·시간" in system and "view_args`가 아니다" in system
    assert "APM 지표 목록" in system, "요청 영역 라벨"


# ── 6. 2단 최종 답 — 비의무 task 고지(시 단위 · 변경 감지 · 가림)가 본문에 실리는가 ──────────

async def _two_tier(gateway_replies: dict, plan: list[dict], gateway) -> dict:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.config import AppConfig, LLMConfig
    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.replanner import replanner
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import create_initial_state

    gateway(gateway_replies)
    config = AppConfig(_env_file=None, llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
                       dbhub=DBHubConfig(_env_file=None, server_url="http://localhost:9099/sse",
                                         source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
                       checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    state = create_initial_state(user_query="web01 질의", thread_id="th-v", user_id="alice",
                                 user_role="user", allowed_sources=None)
    state.update({"parsed_requirements": {"filter_conditions": [
        {"field": "hostname", "op": "=", "value": "web01"}], "original_query": "q"},
        "task_plan": plan, "task_results": {}, "replan_count": 0})
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"] * 4),
                                          app_config=config))
    nf = json.dumps({"needs_followup": False, "reason": "충분", "new_tasks": []})
    state.update(await replanner(state, llm=FakeListChatModel(responses=[nf] * 3),
                                 app_config=config))
    return await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."] * 8),
                                   app_config=config, synthesize=True, composite_answer="steps")


def _apm_task(tid: str, views: list[str]) -> dict:
    return {"task_id": tid, "agent": "apm_query", "sub_query": "web01 조회", "order": 1,
            "depends_on": [], "input_from": [], "status": "pending", "views": views}


@pytest.mark.asyncio
async def test_two_tier_structured_disclosures_carry_w2_notices(gateway) -> None:
    out = await _two_tier({**_ALL_REPLIES, "apm_source_changes": _env("apm_source_changes", [
        {"instance_id": 11, "change_detected_ms": 1759363200000}], disclosures=[
        {"kind": "apm_masked_fields", "text": "개인정보·자격증명 보호를 위해 값을 가린 칸:"
                                              " name"}])},
        [_apm_task("t1", ["apm.changes"])], gateway)
    kinds = {d["kind"] for d in out.get("disclosures") or []}
    assert {disc.APM_CHANGE_DETECTION, disc.APM_MASKED_FIELDS} <= kinds, "구조 필드에는 실린다"


@pytest.mark.asyncio
async def test_two_tier_answer_body_shows_change_detection_notice(gateway) -> None:
    out = await _two_tier(_ALL_REPLIES, [_apm_task("t1", ["apm.changes"])], gateway)
    assert "배포 시각으로 확정할 수 없습니다" in (out.get("final_response") or "")


@pytest.mark.asyncio
async def test_change_rows_show_a_readable_time(gateway) -> None:
    import re

    gateway(_ALL_REPLIES)
    res = await _run(["apm.changes"], "web01")
    row = res["organized_data"]["rows"][0]
    assert any(isinstance(v, str) and re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", v)
               for v in row.values()), row


@pytest.mark.asyncio
async def test_rejected_sort_by_does_not_sink_the_stats_view(gateway) -> None:
    def stats(args: dict) -> dict:
        if args.get("sort_by"):
            # 게이트웨이 새 계약(W2V-B2) — 원천이 정렬 기준을 거부하면 그 조건 없이 다시 받아
            # 로컬 정렬한 성공 봉투 + [한계] + 고지
            # 문구는 실 게이트웨이 프로세스 종단(2026-10-02 · response_time → responseTime 대응)
            return _env("apm_status_stats", [{"name": "select ?", "calls": 1}], limits=[
                "[한계] 원천이 정렬 기준 response_time를 받지 않아 전체를 받아 정렬했다(그 조건·행"
                " 수 없이 다시 받음 — 행 수는 서버 기본값 · 미공개 W10 · 원천 사유 도메인 1000:"
                " Unknown sort_by_metrics: response_time)"], disclosures=[
                {"kind": "apm_unresolved_condition",
                 "text": "원천이 정렬 기준 response_time을(를) 받지 않아 그 조건 없이 받아"
                         " 직접 정렬했습니다"}])
        return _env("apm_status_stats", [{"name": "select ?", "calls": 1}])

    gateway({**_ALL_REPLIES, "apm_status_stats": stats})
    res = await _run(["apm.sql_stats"], "web01",
                     view_args={"apm.sql_stats": {"sort_by": "response_time"}})
    assert res.get("query_results"), "정렬 기준만 빼고라도 답한다"
    assert any(d["kind"] == disc.APM_UNRESOLVED_CONDITION for d in res["disclosures"])


@pytest.mark.asyncio
async def test_agreeing_planner_and_retry_view_survives_a_wrong_area_label(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    args = {"apm.runtime": {"metrics": ["socket_count"], "interval_minute": 10}}
    res = await _run(["apm.runtime"], "web01", areas=["apm_metric_catalog"], view_args=args,
                     llm=_SelectLLM({"views": ["apm.runtime"], "view_args": args}))
    sel = res["apm_query"]["selection"]
    assert gw.named("apm_runtime_health") or "apm.runtime" in (sel.get("candidates") or []), sel
