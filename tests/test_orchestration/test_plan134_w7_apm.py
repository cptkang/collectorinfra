"""plans/134 W7 본체 — 설정·관리·민감 조회 보기 7종(N-15·N-16)과 선택 대상 · 필수 조건.

고정하는 계약(팀 리드 계약 §3.3 도구 인자 · §4.1):
  1. 보기: `apm.event_rules`·`apm.process`·`apm.jennifer_server` = `apm_config`(kind는 고정
     인자 또는 `view_args.kind` — 기본값) · `apm.loaded_classes` =
     `apm_config(kind=loaded_classes)`(hostname 필수 — 종전 필수 대상 규칙) · `apm.environment` =
     `apm_environment` · `apm.users` = `apm_users`(대상 없음) · `apm.active_detail` =
     `apm_active_detail`(앞 결과 `active_ref`). 답변 영역 `apm_management`(`active_only`) ·
     실행 중 요청 상세는 기존 `was_activity` 재사용.
  2. **선택 대상**(`target: optional`): 이번 턴 대상·선행 task 결과가 있으면 대상별 · 없으면
     hostname 없이 1회 · 인스턴스 목록 첫 홉 삽입 없음 · 직전 턴 대상은 지시어(「그 서버」)일
     때만 · 소스 범위 kind(색상 경계·데이터 서버·RDB Export)는 대상과 무관하게 1회
     (`targeted_choices`).
  3. **필수 조건** `process_id`: 없거나 무효면 그 보기는 조회하지 않고 되묻는다(의무 고지) — 같은
     task의 다른 보기는 조회한다. 선택 조건 무효는 종전대로 버리고 고지 후 조회.
  4. 기존 12개 보기의 대상 규칙은 그대로다(명시 보기는 직전 대상을 쓴다 · 필수 대상 없음 =
     첫 홉).
게이트웨이는 모의 MCP 세션이다(실 게이트웨이 · 실 LLM 0).
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.domain.result_refs import extract_result_refs
from src.orchestration import apm_query as aq
from src.routing.registry import get_registry, parse_registry
from tests.test_orchestration import apm_batch_mock

ip = importlib.import_module("src.orchestration.intent_planner")

NOW = datetime(2026, 10, 2, 10, 0, 0)
W7_VIEWS = ("apm.event_rules", "apm.process", "apm.jennifer_server", "apm.loaded_classes",
            "apm.environment", "apm.users", "apm.active_detail")


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
        # plans/134 M-5 — 다건 대상은 `targets` 배치 1호출(계약 A-2 봉투로 흉내)
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self.replies[name])
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


_REPLIES = {
    "apm_config": _env("apm_config", [{"source_id": "default", "rule_type": "error"}]),
    "apm_environment": _env("apm_environment", [{"scope": "JAVA", "name": "java.vendor",
                                                 "value": "x"}]),
    "apm_users": _env("apm_users", [{"origin": "auth", "id": "a***"}]),
    "apm_active_detail": _env("apm_active_detail", [{"guid": "g", "sql": "select ?"}]),
    "apm_instance_map": _env("apm_instance_map", [
        {"hostname": "web01", "match_confidence": "high"}]),
    "apm_app_health": _env("apm_app_health", [{"tps": 1}]),
}


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict | None = None) -> _Gateway:
        gw = _Gateway(replies or _REPLIES)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _isolated(*hosts: str, previous: tuple[str, ...] = (), query: str = "질의",
              ctx: dict | None = None, **extra: Any) -> dict:
    context = {"previous_entities": [{"field": "hostname", "value": h} for h in previous],
               **(ctx or {})}
    out = {"parsed_requirements": {
               "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
               "time_range": None, "limit": None},
           "conversation_context": context, "thread_id": "th-1", "user_id": "alice",
           "original_user_query": query}
    out.update(extra)
    return out


async def _run(views: list[str], isolated: dict, view_args: dict | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views,
                            "sub_query": "질의"}
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, isolated, llm=None, app_config=_cfg(), now=NOW)


# ── 1. 레지스트리 ─────────────────────────────────────────────────────────────

def test_w7_views_follow_the_contract_rows() -> None:
    views = {v.id: v for v in aq.apm_views()}
    expect = {
        "apm.event_rules": ("apm_config", "optional", "", {}, "apm_management"),
        "apm.process": ("apm_config", "optional", "", {"kind": "process_instance"},
                        "apm_management"),
        "apm.jennifer_server": ("apm_config", "optional", "", {}, "apm_management"),
        "apm.loaded_classes": ("apm_config", "", "hostname", {"kind": "loaded_classes"},
                               "apm_management"),
        "apm.environment": ("apm_environment", "optional", "", {}, "apm_management"),
        "apm.users": ("apm_users", "none", "", {}, "apm_management"),
        "apm.active_detail": ("apm_active_detail", "reference", "", {}, "was_activity"),
    }
    for vid, (tool, target, required, fixed, area) in expect.items():
        view = views[vid]
        assert (view.tool, view.target, view.required_input, view.fixed_args, view.capability,
                view.window) == (tool, target, required, fixed, area, "none"), vid
        assert view.examples, vid
    args = {vid: [(a.name, a.type) for a in views[vid].args] for vid in W7_VIEWS}
    assert args["apm.event_rules"] == [("kind", "enum"), ("rule_type", "enum"),
                                       ("target", "enum"), ("error_type", "token")]
    assert args["apm.process"] == [("process_id", "int")]
    assert args["apm.environment"] == [("scope", "enum"), ("key", "text")]
    assert args["apm.users"] == [("user_id", "account")]
    assert args["apm.active_detail"] == [("ref", "int")]
    (pid,) = views["apm.process"].args
    assert pid.required and pid.min == 1
    kind = views["apm.event_rules"].args[0]
    assert (kind.default, kind.targeted_choices) == ("event_rules", ("event_rules",))
    server = views["apm.jennifer_server"].args[0]
    assert (server.choices, server.default, server.targeted_choices) == (
        ("data_server", "db_path", "rdb_export"), "data_server", ("db_path",))
    assert views["apm.active_detail"].reference == "active_ref"
    reg = get_registry()
    assert reg.capability_owners("apm_management") == ("apm",)


def test_registry_rejects_inconsistent_target_declarations() -> None:
    def parse(view: dict) -> None:
        parse_registry({"solutions": [{"code": "x", "backend": "mcp", "family": "x",
                                       "capabilities": ["b"], "views": [view]}]})

    for bad in (
        {"id": "x.v", "target": "sometimes"},
        {"id": "x.v", "target": "optional", "required_input": "hostname"},
        {"id": "x.v", "target": "reference"},
        {"id": "x.v", "target": "reference", "reference": "row"},
        {"id": "x.v", "reference": "guid"},
        {"id": "x.v", "args": [{"name": "k", "type": "enum", "choices": ["a"], "default": "b"}]},
        {"id": "x.v", "args": [{"name": "k", "type": "enum", "choices": ["a"],
                                "targeted_choices": ["b"]}]},
    ):
        with pytest.raises(Exception, match=r"x\.v"):
            parse(bad)
    parse({"id": "x.v", "target": "optional"})  # 형식이 맞으면 통과


def test_management_area_renders_only_when_active() -> None:
    active = ip._planner_system_prompt(_cfg(active=True))
    inactive = ip._planner_system_prompt(_cfg(active=False))
    for token in ("| apm_management |", "`apm.environment`", "`process_id` = 정수 1 이상(필수)",
                  "서버를 말하면 그 서버 · 없으면 전체"):
        assert token in active, token
        assert token not in inactive, token


# ── 2. 선택 대상 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_optional_target_without_target_calls_once_without_hostname(gateway) -> None:
    gw = gateway()
    res = await _run(["apm.environment"], _isolated(), {"apm.environment": {"scope": "java"}})
    (args,) = gw.named("apm_environment")
    assert "hostname" not in args and args["scope"] == "JAVA"
    assert gw.named("apm_instance_map") == [], "첫 홉(인스턴스 앞부분) 삽입 없음"
    assert res["apm_query"]["hostnames"] == []


@pytest.mark.asyncio
async def test_optional_target_fans_out_over_this_turns_hosts(gateway) -> None:
    gw = gateway()
    await _run(["apm.environment"], _isolated("web01", "web02"))
    # plans/134 M-5 — 대상 2대 = `targets` 배치 1호출
    assert len(gw.named("apm_environment")) == 1
    assert [a["hostname"] for a in apm_batch_mock.expanded(gw.calls, "apm_environment")] == [
        "web01", "web02"]


@pytest.mark.asyncio
async def test_optional_target_uses_previous_turn_only_when_pointed_at(gateway) -> None:
    gw = gateway()
    await _run(["apm.environment"], _isolated(previous=("web09",), query="JVM 옵션 보여줘"))
    assert "hostname" not in gw.named("apm_environment")[0], "직전 대상으로 조용히 좁히지 않는다"
    gw2 = gateway()
    await _run(["apm.environment"], _isolated(previous=("web09",), query="그 서버 JVM 옵션"))
    assert gw2.named("apm_environment")[0]["hostname"] == "web09", "지시어면 직전 대상"


@pytest.mark.asyncio
async def test_existing_required_views_keep_their_target_rules(gateway) -> None:
    gw = gateway()
    await _run(["apm.app_health"], _isolated(previous=("web09",), query="응답시간"))
    assert gw.named("apm_app_health")[0]["hostname"] == "web09", "종전 명시 보기 규칙 그대로"
    gw2 = gateway()
    await _run(["apm.loaded_classes"], _isolated(), {"apm.loaded_classes": {"search": "Order"}})
    assert gw2.named("apm_instance_map"), "hostname 필수 보기는 종전 첫 홉 규칙"
    (args,) = gw2.named("apm_config")
    assert (args["kind"], args["hostname"], args["search"]) == ("loaded_classes", "web01", "Order")


@pytest.mark.asyncio
async def test_kind_default_and_source_scoped_kinds(gateway) -> None:
    gw = gateway()
    await _run(["apm.event_rules"], _isolated("web01", "web02"),
               {"apm.event_rules": {"rule_type": "metric", "target": "instance",
                                    "error_type": "OutOfMemory"}})
    assert len(gw.named("apm_config")) == 1, "대상 2대 = `targets` 배치 1호출(plans/134 M-5)"
    calls = apm_batch_mock.expanded(gw.calls, "apm_config")
    assert [(a["kind"], a["hostname"]) for a in calls] == [
        ("event_rules", "web01"), ("event_rules", "web02")], \
        "미지정 kind = event_rules · 도메인 범위"
    assert (calls[0]["rule_type"], calls[0]["target"], calls[0]["error_type"]) == (
        "metric", "instance", "OUTOFMEMORY"), "오류 유형은 게이트웨이 경로 변수 형식(대문자)으로"
    rules = {v.id: v for v in aq.apm_views()}["apm.event_rules"]
    for bad in ("OUT OF MEMORY", "out-of-memory", "가나", "X" * 65):
        assert aq.validate_view_args(rules, {"error_type": bad})[0] == {}, bad
    gw2 = gateway()
    await _run(["apm.event_rules"], _isolated("web01", "web02"),
               {"apm.event_rules": {"kind": "color_boundary"}})
    (args,) = gw2.named("apm_config")
    assert args["kind"] == "color_boundary" and "hostname" not in args, "소스 범위 — 1회"
    gw3 = gateway()
    await _run(["apm.jennifer_server"], _isolated("web01"))
    (args,) = gw3.named("apm_config")
    assert args["kind"] == "data_server" and "hostname" not in args
    gw4 = gateway()
    await _run(["apm.jennifer_server"], _isolated("web01"),
               {"apm.jennifer_server": {"kind": "db_path"}})
    assert gw4.named("apm_config")[0]["hostname"] == "web01", "DB 경로만 도메인 범위"


# ── 3. 필수 조건 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("view_args", [None, {"apm.process": {"process_id": "abc"}},
                                       {"apm.process": {"process_id": 0}}],
                         ids=["missing", "not_int", "below_min"])
async def test_process_without_valid_pid_asks_back(gateway, view_args) -> None:
    gw = gateway()
    res = await _run(["apm.process"], _isolated("web01"), view_args)
    assert gw.calls == []
    assert res["degraded_reason"] == "apm_unresolved_condition"
    text = res["final_response"]
    assert "필요한 조건(프로세스 ID(PID))" in text and "다시 물어 주세요" in text
    assert "「web01 PID 12345는 어느 WAS 인스턴스야?」" in text
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_UNRESOLVED_CONDITION and item["text"] == text
    assert res["apm_query"]["unresolved"][0]["queried"] is False


@pytest.mark.asyncio
async def test_valid_pid_reaches_the_tool_and_other_views_still_run(gateway) -> None:
    gw = gateway()
    await _run(["apm.process"], _isolated("web01"), {"apm.process": {"process_id": "12345"}})
    (args,) = gw.named("apm_config")
    assert (args["kind"], args["process_id"], args["hostname"]) == (
        "process_instance", 12345, "web01")
    gw2 = gateway()
    res = await _run(["apm.process", "apm.environment"], _isolated("web01"))
    assert [n for n, _ in gw2.calls] == ["apm_environment"], "막힌 보기만 빼고 조회"
    kinds = [d["kind"] for d in res["disclosures"]]
    assert disc.APM_UNRESOLVED_CONDITION in kinds and res["query_results"]


# ── 4. 사용자 계정 · 실행 중 요청 상세 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_users_view_is_target_free_and_takes_account_ids(gateway) -> None:
    gw = gateway()
    await _run(["apm.users"], _isolated("web01"), {"apm.users": {"user_id": "john.doe@corp"}})
    (args,) = gw.named("apm_users")
    assert args["user_id"] == "john.doe@corp" and "hostname" not in args
    gw2 = gateway()
    res = await _run(["apm.users"], _isolated(), {"apm.users": {"user_id": "john doe"}})
    assert "user_id" not in gw2.named("apm_users")[0], "형식 밖 계정 ID는 버리고 고지 후 조회"
    assert disc.APM_UNRESOLVED_CONDITION in [d["kind"] for d in res["disclosures"]]
    assert aq.validate_view_args(
        {v.id: v for v in aq.apm_views()}["apm.users"], {"user_id": "a..b"})[0] == {}


@pytest.mark.asyncio
async def test_active_detail_passes_the_active_ref_as_is(gateway) -> None:
    gw = gateway()
    rows = [{"source_id": "s1", "txid": "-77", "hostname": "web01", "start_time_ms": 5,
             "active_ref": {"source_id": "s1", "domain_id": 1000, "txid": "-77",
                            "session_id": 31, "thread_hash": 8}},
            {"source_id": "s1", "txid": "-78", "hostname": "web01", "active_ref": None}]
    ctx = {"turn_count": 2, "previous_result_refs": {"turn": 1, **extract_result_refs(rows)}}
    await _run(["apm.active_detail"], _isolated(ctx=ctx), {"apm.active_detail": {"ref": 1}})
    (args,) = gw.named("apm_active_detail")
    for key, value in {"source_id": "s1", "domain_id": 1000, "txid": "-77", "session_id": 31,
                       "thread_hash": 8, "hostname": "web01"}.items():
        assert args[key] == value, key
    gw2 = gateway()
    res = await _run(["apm.active_detail"], _isolated(ctx=ctx), {"apm.active_detail": {"ref": 2}})
    assert gw2.calls == [] and "실행 중 요청 참조가 없어" in res["final_response"]
