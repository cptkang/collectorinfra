"""plans/210 W8 §6.2 — 채팅 노드 주소 결정성 (A 인스턴스 대상 · C 파드 대상 보기 부착 · D-300 ⑧).

- A: `apm.node_address`가 대상(분해 `targets` · 파서 hostname · 직전 대상 승계)을 받아 해석
  인스턴스의 (소스, 도메인)마다 `apm_node_addresses(instance_ids=…)` 1회 · 답 줄은 그 인스턴스들만.
  대상 0건이면 「찾지 못함」+ 후보 · 노드 주소 도구 호출 0(D-290 ⑥).
- C: 해석 인스턴스별로 부르는 보기(응답시간·런타임)에서 해석 행에 `host_address` 칸(게이트웨이
  플래그 on 표지)이 있으면 처리기 1회 안에서 (소스, 도메인)마다 노드 주소 도구 1회 · `pod: true`만
  답 줄 · VM(`pod: false`) 부착 0.
- 플래그 off 표지(해석 행에 `host_address` 칸 없음)면 A·C 답이 변경 전과 바이트 동일 — 기대값은
  W8 변경 전 코드로 같은 모의 입력을 돌려 뜬 값이다(`_BEFORE_*`).
- 단어 매칭 0: 원문에 「파드」「노드」가 있어도 표지가 없으면 부착 0.

실 게이트웨이·실 LLM 0 — 모의 게이트웨이 세션(실 `SourceMcpSession` 래퍼를 지난다).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.orchestration.apm import node_address as na
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 11, 3, 10, 0, 0)
VIEW = "apm.node_address"
TOOL = "apm_node_addresses"
LABEL = "WAS 인스턴스가 도는 노드(호스트) 주소 — K8s 파드의 노드 IP"

#: 모의 인벤토리 — (소스, 도메인, id, 이름, hostname, 파드 여부, 노드 주소)
_INVENTORY = [
    ("b0", 1000, 1, "order-api-1", "", True, "10.136.1.7"),
    ("b0", 1000, 2, "order-api-2", "", True, "10.136.1.8"),
    ("b0", 1000, 3, "pay-api-1", "", True, "10.136.1.9"),
    ("b0", 2000, 7, "vm-was01", "web01", False, "10.20.0.7"),
]


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-11-03T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


class _Gateway:
    """모의 게이트웨이.

    - `flag`: 게이트웨이 `JENNIFER_NODE_ADDRESS_ENABLED` — on이면 `apm_instance_map` 행에
      `host_address` 칸이 있고 노드 주소 도구가 등록된다(`registered`로 따로 끌 수 있다).
    - `apm_instance_map(query=…)`: 이름 앞부분 일치 · 0건이면 `suggestions` ·
      `(business=…)`: 업무명 「주문」만 order-api-* 인스턴스.
      `(hostname=…)`: 그 서버의 인스턴스 · 없으면 `instance_unresolved`.
    - `apm_node_addresses`: 인자(source_ids·domain_ids·instance_ids)로 거른 행 + 소스 집계.
    """

    def __init__(self, *, flag: bool = True, registered: bool | None = None,
                 node_status: str = "ok", host_limits: list[str] | None = None) -> None:
        self.flag = flag
        self.host_limits = host_limits or []
        self.registered = flag if registered is None else registered
        self.node_status = node_status
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]

    def _inst_row(self, item: tuple) -> dict:
        sid, did, iid, name, host, _pod, node = item
        row = {"source_id": sid, "instance_id": iid, "instance_name": name, "domain_id": did,
               "domain_name": f"D{did}", "hostname": host, "match_confidence": "high"}
        if self.flag:
            row["host_address"] = node
        return row

    def _node_reply(self, args: dict) -> dict:
        sources = args.get("source_ids")
        domains = args.get("domain_ids")
        ids = args.get("instance_ids")
        rows = []
        for sid, did, iid, name, _host, pod, node in _INVENTORY:
            if sources and sid not in sources or domains and did not in domains:
                continue
            if ids is not None and iid not in ids:
                continue
            ok = self.node_status == "ok"
            rows.append({"source_id": sid, "domain_id": did, "instance_id": iid,
                         "instance_name": name, "ip": f"10.139.0.{iid}",
                         "host_address": node if ok else "",
                         "pod": pod if ok else None, "status": self.node_status})
        reason = "" if self.node_status == "ok" else "웹 로그인 실패 — 웹 계정·비밀번호 확인"
        src = {"source_id": "b0", "status": self.node_status, "reason": reason,
               "domains": len({r["domain_id"] for r in rows}), "failed_domains": [],
               "instances": len(rows), "instance_status": {self.node_status: len(rows)}}
        extra: dict[str, Any] = {"node_address_sources": [src]}
        if self.node_status != "ok":
            extra.update(partial=True,
                         limits=[f"[한계] APM 소스 b0 노드 주소 {self.node_status} — {reason}"])
        return _env(TOOL, rows, **extra)

    def _single(self, name: str, args: dict) -> dict:
        if name == "apm_instance_map":
            if "query" in args:
                rows = [self._inst_row(i) for i in _INVENTORY if i[3].startswith(args["query"])]
                return _env(name, rows, **({} if rows else {
                    "suggestions": [{"instance_name": "order-api-1"}]}))
            if "business" in args:  # 업무명 「주문」 = order-api-* (모의 업무 정의)
                rows = [self._inst_row(i) for i in _INVENTORY
                        if args["business"] == "주문" and i[3].startswith("order-api")]
                return _env(name, rows)
            if "hostname" in args:
                rows = [self._inst_row(i) for i in _INVENTORY if i[4] == args["hostname"]]
                if not rows:
                    return {"error": "instance_unresolved", "reason": "그 서버의 인스턴스가 없다"}
                return _env(name, rows, limits=self.host_limits)
            return _env(name, [])
        if name == TOOL:
            return self._node_reply(args)
        return _env(name, [{"source_id": "b0", "instance_id": args.get("instance_id"),
                            "instance_name": args.get("instance_name"),
                            "hostname": args.get("hostname"), "tps": 1.0}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if name == TOOL and not self.registered:
            return SimpleNamespace(content=[SimpleNamespace(text=f"Unknown tool: {name}")],
                                   isError=True)
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


async def _no_edge(terms, *, app_config, authorized_db_ids):
    return {t: [] for t in terms}, [], []


def _install(monkeypatch, gw: _Gateway) -> _Gateway:
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    monkeypatch.setattr(aq, "link_business_names", _no_edge)
    return gw


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens='{"apm": "gw"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


def _isolated(query: str, hosts: tuple[str, ...] = ()) -> dict:
    filters = [{"field": "hostname", "op": "=", "value": h} for h in hosts]
    return {"parsed_requirements": {"filter_conditions": filters, "time_range": None,
                                    "limit": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice",
            "original_user_query": query, "user_query": query}


async def _run(views: list[str], targets: list[dict] | None = None, *,
               query: str = "order-api 파드가 도는 노드 주소", hosts: tuple[str, ...] = (),
               view_args: dict | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "sub_query": query,
                            "input_from": [], "views": views}
    if targets is not None:
        task["targets"] = targets
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, _isolated(query, hosts), llm=None, app_config=_cfg(),
                                  now=NOW)


def _answer(res: dict) -> dict:
    """사용자에게 보이는 답 — 메타(감사)를 뺀 처리기 결과 전부."""
    return {k: v for k, v in res.items() if k != aq.META_KEY}


def _work(gw: _Gateway) -> list[str]:
    return [n for n, _ in gw.calls if not n.startswith("apm_job_")]


ORDER = [{"text": "order-api", "kind": "instance"}]


def _lines(res: dict) -> list[str]:
    return list(res.get("answer_lines") or [])


# ── ① A: 인스턴스 대상 → 그 인스턴스만 ──────────────────────────────────────────────


async def test_instance_target_lines_only_those_instances(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run([VIEW], ORDER)
    assert _work(gw) == ["apm_instance_map", TOOL]
    [args] = gw.named(TOOL)
    assert args["source_ids"] == ["b0"] and args["domain_ids"] == [1000]
    assert args["instance_ids"] == [1, 2]
    lines = _lines(res)
    assert any("order-api-1" in x and "10.136.1.7" in x for x in lines)
    assert any("order-api-2" in x and "10.136.1.8" in x for x in lines)
    assert not any("pay-api-1" in x or "vm-was01" in x for x in lines)


async def test_instance_target_not_found_has_candidates_and_no_node_call(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run([VIEW], [{"text": "zzz-api", "kind": "instance"}])
    assert gw.named(TOOL) == []  # D-290 ⑥ — 대체 조회 0
    text = json.dumps(_answer(res), ensure_ascii=False)
    assert "찾지 못" in text and "order-api-1" in text  # 후보 노출


async def test_hostname_target_resolves_instances_via_instance_map(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run([VIEW], hosts=("web01",), query="web01 노드 주소")
    assert {"hostname": "web01"}.items() <= gw.named("apm_instance_map")[0].items()
    [args] = gw.named(TOOL)
    assert args["domain_ids"] == [2000] and args["instance_ids"] == [7]
    assert any("vm-was01" in x and "10.20.0.7" in x for x in _lines(res))


async def test_unknown_hostname_is_not_found_without_node_call(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run([VIEW], hosts=("nohost01",), query="nohost01 노드 주소")
    assert gw.named(TOOL) == []
    assert "찾지 못" in json.dumps(_answer(res), ensure_ascii=False)


async def test_domain_id_and_targets_are_and(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    await _run([VIEW], ORDER, view_args={VIEW: {"domain_id": 1000}})
    [args] = gw.named(TOOL)
    assert args["domain_ids"] == [1000] and args["instance_ids"] == [1, 2]
    gw2 = _install(monkeypatch, _Gateway())
    res = await _run([VIEW], ORDER, view_args={VIEW: {"domain_id": 2000}})
    assert gw2.named(TOOL) == []  # 대상이 그 도메인에 없으면 호출 0 · 침묵 아님
    assert "2000" in json.dumps(_answer(res), ensure_ascii=False)


async def test_no_target_keeps_w2_single_call(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    await _run([VIEW], query="노드 주소 보여 줘")
    assert _work(gw) == [TOOL]
    [args] = gw.named(TOOL)
    assert "instance_ids" not in args


async def test_non_ok_status_carries_reason_line(monkeypatch):
    _install(monkeypatch, _Gateway(node_status="web_login_failed"))
    res = await _run([VIEW], ORDER)
    lines = _lines(res)
    assert any("order-api-1" in x and "web_login_failed" in x and "웹 로그인 실패" in x
               for x in lines)


# ── ② C: 파드 대상 보기에 노드 주소 부착 ─────────────────────────────────────────────


async def test_pod_health_view_gets_node_lines(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run(["apm.app_health"], ORDER, query="order-api 응답시간")
    [args] = gw.named(TOOL)
    assert args["instance_ids"] == [1, 2] and args["domain_ids"] == [1000]
    assert _lines(res) == [
        f"{LABEL} · order-api-1: 노드 주소 10.136.1.7(상태 ok)",
        f"{LABEL} · order-api-2: 노드 주소 10.136.1.8(상태 ok)",
    ]


async def test_two_views_same_domain_call_node_tool_once(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run(["apm.app_health", "apm.runtime"], ORDER, query="order-api 응답시간과 힙")
    assert len(gw.named(TOOL)) == 1
    assert len(_lines(res)) == 2


async def test_vm_instance_gets_no_node_line(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run(["apm.app_health"], [{"text": "vm-was", "kind": "instance"}],
                     query="vm-was 응답시간")
    assert len(gw.named(TOOL)) == 1
    assert _lines(res) == []


async def test_node_view_with_health_view_does_not_double_attach(monkeypatch):
    gw = _install(monkeypatch, _Gateway())
    res = await _run([VIEW, "apm.app_health"], ORDER, query="order-api 노드 주소와 응답시간")
    assert len(gw.named(TOOL)) == 1  # 노드 주소 보기가 살아 있으면 C 부착 없음
    assert sum("order-api-1" in x for x in _lines(res)) == 1


# ── ③ 플래그 off 표지 → 변경 전과 답 바이트 동일 ─────────────────────────────────────

_BEFORE = json.loads((__import__("pathlib").Path(__file__).parents[1] / "snapshots" / "plan210_w8"
                      / "before_answers.json").read_text(encoding="utf-8"))

_OFF = {
    "a_text": dict(views=[VIEW], targets=ORDER),
    "a_host": dict(views=[VIEW], hosts=("web01",)),
    "a_text_domain": dict(views=[VIEW], targets=ORDER, view_args={VIEW: {"domain_id": 1000}}),
    "c_health": dict(views=["apm.app_health"], targets=ORDER),
    "c_two": dict(views=["apm.app_health", "apm.runtime"], targets=ORDER,
                  query="order-api 파드 노드 응답시간과 힙"),
    "c_vm": dict(views=["apm.app_health"], targets=[{"text": "vm-was", "kind": "instance"}]),
}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


@pytest.mark.parametrize("name", sorted(_OFF))
async def test_flag_off_answer_bytes_identical(monkeypatch, name):
    gw = _install(monkeypatch, _Gateway(flag=False))
    res = await _run(**_OFF[name])
    assert _dump(_answer(res)) == _dump(_BEFORE[name]["answer"])
    if name.startswith("c_"):
        assert gw.named(TOOL) == []  # 표지 없음 → 시도 0


async def test_unknown_tool_with_marker_is_one_disabled_line(monkeypatch):
    gw = _install(monkeypatch, _Gateway(flag=True, registered=False))
    res = await _run(["apm.app_health", "apm.runtime"], ORDER, query="order-api 응답시간과 힙")
    assert len(gw.named(TOOL)) == 1
    lines = _lines(res)
    assert len(lines) == 1 and "node_address_disabled" in lines[0]


# ── ④ 단어 매칭 0 ────────────────────────────────────────────────────────────────


async def test_pod_node_words_without_marker_attach_nothing(monkeypatch):
    gw = _install(monkeypatch, _Gateway(flag=False))
    res = await _run(["apm.app_health"], ORDER, query="order-api 파드가 도는 노드 주소와 응답시간")
    assert gw.named(TOOL) == [] and _lines(res) == []


# ── 단위: 줄 상한 · 부착 규칙 · 대상 채널 ───────────────────────────────────────────


def test_pod_node_lines_cap_and_rules():
    rows = [{"source_id": "b0", "instance_name": f"p{i}", "pod": True, "status": "ok",
             "host_address": f"10.0.0.{i}"} for i in range(na.LINE_MAX + 2)]
    rows += [{"source_id": "b0", "instance_name": "vm", "pod": False, "status": "ok",
              "host_address": "10.9.9.9"},
             {"source_id": "c1", "instance_name": "x", "pod": None, "status": "web_timeout",
              "host_address": ""}]
    env = {"rows": rows, "node_address_sources": [{"source_id": "c1", "reason": "시간 초과"}]}
    lines = na.pod_node_lines("S", [(None, env), ("node_address_disabled: 끔", None),
                                    ("node_address_disabled: 끔", None)])
    assert lines[:na.LINE_MAX] == [f"S · p{i}: 노드 주소 10.0.0.{i}(상태 ok)"
                                   for i in range(na.LINE_MAX)]
    assert lines[na.LINE_MAX] == "S: 외 2개(전체는 결과 표)"
    assert "S: 소스 c1 노드 주소 web_timeout — 시간 초과" in lines
    assert lines.count("S: node_address_disabled: 끔") == 1
    assert not any("vm" in x for x in lines)


def test_marked_needs_every_row_with_host_address():
    assert na.marked([{"host_address": ""}, {"host_address": "1.1.1.1"}])
    assert not na.marked([{"host_address": "x"}, {"instance_id": 1}])
    assert not na.marked([])


def test_target_groups_and_args_per_source_domain():
    rows = [{"source_id": "b0", "domain_id": 1, "instance_id": 5},
            {"source_id": "b0", "domain_id": 1, "instance_id": 6},
            {"source_id": "c1", "domain_id": 2, "instance_id": 9}]
    groups = na.target_groups(rows)
    assert [(s, d, list(ids)) for s, d, ids in groups] == [("b0", 1, [5, 6]), ("c1", 2, [9])]
    args = na.targeted_args({}, "b0", 1, [5, 6])
    assert args["source_ids"] == ["b0"] and args["domain_ids"] == [1]
    assert args[na.INSTANCE_IDS_ARG] == [5, 6]


def test_node_view_uses_targets_per_target():
    from src.orchestration.apm import targets as tg

    node = SimpleNamespace(id=VIEW, target="none", required_input=False, first_hop=False)
    other = SimpleNamespace(id="apm.metric_list", target="none", required_input=False,
                            first_hop=False)
    assert tg._uses_target(node, None) and not tg._uses_target(other, None)
    assert tg._per_target_views([VIEW], {VIEW: node})
    assert not tg._per_target_views([VIEW, "apm.metric_list"],
                                    {VIEW: node, "apm.metric_list": other})


def test_line_cap_counts_rows_beyond_inline_limit():
    """「외 n개」는 인라인 행이 아니라 게이트웨이가 센 전체 수로 센다(W8 교정 Minor 4)."""
    rows = [{"source_id": "b0", "instance_name": f"p{i}", "pod": True, "status": "ok",
             "host_address": f"10.0.0.{i}"} for i in range(3)]
    env = {"rows": rows, "total_row_count": 600, "node_address_sources": []}
    assert na.row_total(env) == 600
    assert na.row_total({"rows": rows, "node_address_sources": [{"instances": 4},
                                                                {"instances": 5}]}) == 9
    assert na.row_total({"rows": rows}) == 3
    lines = na.pod_node_lines("S", [(None, env)])
    assert lines[-1] == "S: 외 597개(전체는 결과 표)" and len(lines) == 4
    agg = {"scope": "S", "node_address_sources": [{"source_id": "b0", "status": "ok"}],
           na.NODE_ROWS_KEY: rows, na.NODE_TOTAL_KEY: 8}
    assert na.node_address_lines(agg)[-1] == "S: 외 5개(전체는 결과 표)"


async def test_hostname_resolution_limits_reach_meta(monkeypatch):
    """서버 정합 봉투의 `[한계]`가 `meta.limits`에 오른다(W8 교정 Minor 6)."""
    limit = "[한계] 노드 주소: 도메인 51개 중 50개만 새로 조회했다"
    _install(monkeypatch, _Gateway(host_limits=[limit]))
    res = await _run([VIEW], hosts=("web01",), query="web01 노드 주소")
    assert limit in res[aq.META_KEY]["limits"]


def test_lift_node_targets_shapes():
    """노드 주소 보기 조건의 `targets` → task `targets`(모양만 · 다른 보기·빈 값은 그대로)."""
    from src.orchestration.apm.targets import lift_node_targets

    assert lift_node_targets([VIEW], {VIEW: {"targets": "주문", "domain_id": 1}}, None) == (
        [{"text": "주문", "kind": "auto"}], {VIEW: {"domain_id": 1}})
    assert lift_node_targets([VIEW], {VIEW: {"targets": [{"text": "a", "kind": "business"}]}},
                             [{"text": "b", "kind": "instance"}]) == (
        [{"text": "b", "kind": "instance"}, {"text": "a", "kind": "business"}], {})
    untouched = {"apm.app_health": {"targets": "주문"}}
    assert lift_node_targets(["apm.app_health"], untouched, None) == (None, untouched)
    empty = {VIEW: {"targets": [1, ""]}}
    assert lift_node_targets([VIEW], empty, None) == (None, empty)  # 종전 「버린 조건」 고지
