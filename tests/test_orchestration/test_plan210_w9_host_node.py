"""plans/210 W9 §6.3 축 B — 서버명 직행 보기의 파드 노드 주소 부착 (D-300 ⑧).

- 「app01 응답시간」처럼 서버명으로 바로 부르는 호출(`_host_calls` — `c.instance` 없음)의 봉투가
  게이트웨이 표지 `instance_resolution.node_address: true`를 달았으면 `instance_refs`를 W8 C 해석
  행과 합쳐 (소스, 도메인)마다 `apm_node_addresses` 1회 · `pod: true`만 답 줄 · VM 0.
- 배치(`targets`) 호출은 항목 봉투(복원된 대상별 가상 호출)의 표지를 본다.
- 표지가 없으면(게이트웨이 플래그 off) 시도 0 — 답은 W9 변경 전 코드로 같은 모의 입력을 돌려 뜬
  스냅샷(`tests/snapshots/plan210_w9/before_answers.json`)과 바이트 동일 · 게이트웨이 호출 목록
  동일.
- 단어 매칭 0: 원문에 「파드」「노드」가 있어도 표지가 없으면 부착 0.

실 게이트웨이·실 LLM 0 — 모의 게이트웨이 세션(실 `SourceMcpSession` 래퍼를 지난다).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.orchestration.apm import node_address as na
from tests.test_orchestration import apm_batch_mock
from tests.test_orchestration import test_plan210_w8_node_address as w8

TOOL = w8.TOOL
LABEL = w8.LABEL
HEALTH = "apm.app_health"
RUNTIME = "apm.runtime"
BUDGET_REASON = "호출 시간 예산 초과로 조회하지 못함(JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS)"

#: 모의 인벤토리 — (소스, 도메인, id, 이름, hostname, 파드 여부, 노드 주소)
_INVENTORY = [
    ("b0", 1000, 1, "order-api-1", "app01", True, "10.136.1.7"),
    ("b0", 1000, 2, "order-api-2", "app02", True, "10.136.1.8"),
    ("b0", 1000, 3, "pay-api-1", "", True, "10.136.1.9"),
    ("b0", 2000, 7, "vm-was01", "web01", False, "10.20.0.7"),
]


class _Gateway(w8._Gateway):
    """W8 모의 게이트웨이 + 서버명 데이터 호출의 정합 봉투(`instance_resolution`).

    - 데이터 도구(`hostname=…`): 그 서버의 인스턴스 행 + `instance_resolution`(`instance_refs` —
      이름 정합이라 `hostname` 포함) · 플래그 on이면 `node_address: true` 가산 · 서버에 인스턴스가
      없으면 `instance_unresolved`.
    - 데이터 도구(`instance_id=…`): 그 인스턴스 행 + id 정합 `instance_resolution`(표지 규칙 같음).
    - `apm_node_addresses`: 이 파일 인벤토리로 거른 행 · `node_status`가 `budget_exceeded`면 파드
      도메인은 `pod: true`(종류를 안다) · 사유는 게이트웨이 예산 사유.
    """

    def _resolution(self, items: list[tuple], by_name: bool) -> dict:
        res: dict[str, Any] = {
            "matched": bool(items), "confidence": "high", "reason": "",
            "instances": [i[2] for i in items],
            "instance_refs": [{"source_id": i[0], "domain_id": i[1], "instance_id": i[2],
                               **({"hostname": i[4]} if by_name else {})} for i in items]}
        if self.flag:
            res["node_address"] = True
        return res

    def _data(self, name: str, args: dict) -> dict:
        if args.get("hostname"):
            items = [i for i in _INVENTORY if i[4] == args["hostname"]]
            if not items:
                return {"error": "instance_unresolved", "reason": "그 서버의 인스턴스가 없다"}
            by_name = True
        else:
            items = [i for i in _INVENTORY if i[2] == args.get("instance_id")]
            by_name = False
        rows = [{"source_id": i[0], "domain_id": i[1], "instance_id": i[2],
                 "instance_name": i[3], "hostname": i[4], "tps": 1.0} for i in items]
        return w8._env(name, rows, instance_resolution=self._resolution(items, by_name))

    def _node_reply(self, args: dict) -> dict:
        sources = args.get("source_ids")
        domains = args.get("domain_ids")
        ids = args.get("instance_ids")
        status = self.node_status
        rows = []
        for sid, did, iid, name, _host, pod, node in _INVENTORY:
            if sources and sid not in sources or domains and did not in domains:
                continue
            if ids is not None and iid not in ids:
                continue
            ok = status == "ok"
            rows.append({"source_id": sid, "domain_id": did, "instance_id": iid,
                         "instance_name": name, "ip": f"10.139.0.{iid}",
                         "host_address": node if ok else "",
                         "pod": pod if ok or status == "budget_exceeded" else None,
                         "status": status})
        reason = {"ok": "", "budget_exceeded": BUDGET_REASON}.get(
            status, "웹 로그인 실패 — 웹 계정·비밀번호 확인")
        src = {"source_id": "b0", "status": status, "reason": reason,
               "domains": len({r["domain_id"] for r in rows}), "failed_domains": [],
               "instances": len(rows), "instance_status": {status: len(rows)}}
        extra: dict[str, Any] = {"node_address_sources": [src]}
        if status != "ok":
            extra.update(partial=True,
                         limits=[f"[한계] APM 소스 b0 노드 주소 {status} — {reason}"])
        return w8._env(TOOL, rows, **extra)

    def _single(self, name: str, args: dict) -> dict:
        if name == TOOL:
            return self._node_reply(args)
        if name == "apm_instance_map":
            if "query" in args:
                rows = [self._inst_row(i) for i in _INVENTORY if i[3].startswith(args["query"])]
                return w8._env(name, rows, **({} if rows else {
                    "suggestions": [{"instance_name": "order-api-1"}]}))
            if "hostname" in args:
                items = [i for i in _INVENTORY if i[4] == args["hostname"]]
                if not items:
                    return {"error": "instance_unresolved", "reason": "그 서버의 인스턴스가 없다"}
                return w8._env(name, [self._inst_row(i) for i in items],
                               instance_resolution=self._resolution(items, True))
            # 무인자 목록(첫 홉 `full`) — 서버가 있는 인스턴스만 hostname을 싣는다
            return w8._env(name, [self._inst_row(i) for i in _INVENTORY])
        return self._data(name, args)

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if name == TOOL and not self.registered:
            return SimpleNamespace(content=[SimpleNamespace(text=f"Unknown tool: {name}")],
                                   isError=True)
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


PAY = [{"text": "pay-api", "kind": "instance"}]

#: 플래그 off 스냅샷 시나리오(W9 변경 전 코드로 캡처 — 같은 바이트여야 한다)
_OFF: dict[str, dict[str, Any]] = {
    "host_pod": dict(views=[HEALTH], hosts=("app01",), query="app01 응답시간"),
    "host_batch": dict(views=[HEALTH], hosts=("app01", "app02"), query="app01 app02 응답시간"),
    "host_mixed": dict(views=[HEALTH, RUNTIME], hosts=("app01",), targets=PAY,
                       query="app01 과 pay-api 응답시간과 힙"),
    "host_vm": dict(views=[HEALTH], hosts=("web01",), query="web01 응답시간"),
    "host_words": dict(views=[HEALTH], hosts=("app01",),
                       query="app01 파드가 도는 노드 주소와 응답시간"),
}

SNAPSHOT = Path(__file__).parents[1] / "snapshots" / "plan210_w9" / "before_answers.json"


async def capture(monkeypatch) -> dict[str, Any]:
    """스냅샷 캡처 — 시나리오마다 (답, 게이트웨이 작업 호출 목록)."""
    out: dict[str, Any] = {}
    for name, kw in sorted(_OFF.items()):
        gw = w8._install(monkeypatch, _Gateway(flag=False))
        res = await w8._run(**kw)
        out[name] = {"answer": json.loads(w8._dump(w8._answer(res))),
                     "calls": [[n, a] for n, a in gw.calls]}
    return out


_BEFORE = json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _work(gw: _Gateway) -> list[str]:
    return [n for n, _ in gw.calls if not n.startswith("apm_job_")]


def _line(name: str, node: str) -> str:
    return f"{LABEL} · {name}: 노드 주소 {node}(상태 ok)"


# ── ① 서버명 직행 보기에 노드 주소 부착 ─────────────────────────────────────────────


async def test_host_pod_health_view_gets_node_line(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway())
    res = await w8._run(**_OFF["host_pod"])
    assert _work(gw) == ["apm_app_health", TOOL]  # 추가 호출 = 노드 주소 도구 1회
    [args] = gw.named(TOOL)
    assert args["source_ids"] == ["b0"] and args["domain_ids"] == [1000]
    assert args["instance_ids"] == [1]
    assert w8._lines(res) == [_line("order-api-1", "10.136.1.7")]


async def test_host_vm_gets_no_node_line(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway())
    res = await w8._run(**_OFF["host_vm"])
    assert len(gw.named(TOOL)) == 1  # 파드 여부는 게이트웨이 `pod`만 안다 — VM은 줄 0
    assert w8._lines(res) == []


async def test_host_batch_targets_get_node_lines(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway())
    res = await w8._run(**_OFF["host_batch"])
    [health] = gw.named("apm_app_health")
    assert [t.get("hostname") for t in health["targets"]] == ["app01", "app02"]  # 배치 1호출
    [args] = gw.named(TOOL)
    assert args["domain_ids"] == [1000] and args["instance_ids"] == [1, 2]
    assert w8._lines(res) == [_line("order-api-1", "10.136.1.7"),
                              _line("order-api-2", "10.136.1.8")]


async def test_host_and_instance_target_same_domain_share_one_call(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway())
    res = await w8._run(**_OFF["host_mixed"])
    [args] = gw.named(TOOL)  # 두 보기 · 서버명 + 인스턴스 이름 대상 → (b0, 1000) 1회
    assert args["domain_ids"] == [1000] and sorted(args["instance_ids"]) == [1, 3]
    assert w8._lines(res) == [_line("order-api-1", "10.136.1.7"),
                              _line("pay-api-1", "10.136.1.9")]


async def test_unresolved_host_gets_no_attach(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway())
    res = await w8._run(views=[HEALTH], hosts=("nohost01",), query="nohost01 응답시간")
    assert gw.named(TOOL) == [] and w8._lines(res) == []  # 오류 호출은 표지를 보지 않는다


async def test_first_hop_hosts_are_not_attached(monkeypatch):
    """대상 없는 질의 — 첫 홉이 고른 서버는 사용자가 말한 서버가 아니다(호출 수 불변)."""
    gw = w8._install(monkeypatch, _Gateway())
    res = await w8._run(views=[HEALTH], query="응답시간 전부", view_args={HEALTH: {"full": True}})
    assert gw.named("apm_app_health")  # 첫 홉 서버로 대상 보기를 불렀다
    assert gw.named(TOOL) == [] and w8._lines(res) == []


async def test_instance_list_view_with_host_is_not_attached(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway())
    await w8._run(views=["apm.instances"], hosts=("app01",), query="app01 인스턴스 목록")
    assert gw.named(TOOL) == []  # 목록 행이 이미 `host_address` 칸을 싣는다


async def test_unknown_tool_with_host_marker_is_one_disabled_line(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway(registered=False))
    res = await w8._run(**_OFF["host_mixed"])
    assert len(gw.named(TOOL)) == 1
    lines = w8._lines(res)
    assert len(lines) == 1 and na.NODE_ADDRESS_DISABLED in lines[0]


async def test_node_view_with_host_does_not_double_attach(monkeypatch):
    gw = w8._install(monkeypatch, _Gateway())
    await w8._run(views=[w8.VIEW, HEALTH], hosts=("app01",), query="app01 노드 주소와 응답시간")
    assert len(gw.named(TOOL)) == 1  # 노드 주소 보기가 답한다(W8 규칙)


# ── ② budget_exceeded 행 — 사유 줄(침묵 없음) ─────────────────────────────────────────


async def test_budget_exceeded_row_has_reason_line(monkeypatch):
    w8._install(monkeypatch, _Gateway(node_status="budget_exceeded"))
    res = await w8._run(**_OFF["host_pod"])
    assert w8._lines(res) == [
        f"{LABEL} · order-api-1: 노드 주소 확인 못 함 — budget_exceeded({BUDGET_REASON})"]


def test_budget_exceeded_lines_in_every_rule():
    src = [{"source_id": "b0", "status": "budget_exceeded", "reason": BUDGET_REASON,
            "instances": 2, "instance_status": {"budget_exceeded": 2}}]
    pod_row = {"source_id": "b0", "instance_name": "p1", "pod": True,
               "status": "budget_exceeded", "host_address": ""}
    vm_kind_row = {"source_id": "b0", "instance_name": "x", "pod": None,
                   "status": "budget_exceeded", "host_address": ""}
    env = {"rows": [pod_row, vm_kind_row], "node_address_sources": src}
    # 파드 부착: 파드는 행 줄 · 종류를 모르면 소스 사유 줄
    assert na.pod_node_lines("S", [(None, env)]) == [
        f"S · p1: 노드 주소 확인 못 함 — budget_exceeded({BUDGET_REASON})",
        f"S: 소스 b0 노드 주소 budget_exceeded — {BUDGET_REASON}"]
    # 노드 주소 보기 — 인스턴스 대상(행마다) · 대상 없음(상태별 수 + 소스 사유)
    rows_agg = {"scope": "S", "node_address_sources": src, na.NODE_ROWS_KEY: [pod_row]}
    assert na.node_address_lines(rows_agg) == [
        f"S · p1: 노드 주소 확인 못 함 — budget_exceeded({BUDGET_REASON})"]
    assert na.node_address_lines({"scope": "S", "node_address_sources": src}) == [
        "S: 노드 주소 확인 0/2개(확인 못 함 — budget_exceeded 2개)",
        f"S: 소스 b0 노드 주소 budget_exceeded — {BUDGET_REASON}"]


# ── ③ 플래그 off 표지 → 변경 전과 답·호출 바이트 동일 · 단어 매칭 0 ──────────────────


@pytest.mark.parametrize("name", sorted(_OFF))
async def test_flag_off_answer_and_calls_identical(monkeypatch, name):
    gw = w8._install(monkeypatch, _Gateway(flag=False))
    res = await w8._run(**_OFF[name])
    assert w8._dump(w8._answer(res)) == w8._dump(_BEFORE[name]["answer"])
    assert json.dumps([[n, a] for n, a in gw.calls], sort_keys=True) == json.dumps(
        _BEFORE[name]["calls"], sort_keys=True)  # 추가 호출 0
    assert gw.named(TOOL) == [] and w8._lines(res) == []


def test_marked_refs_needs_true_marker():
    refs = [{"source_id": "b0", "domain_id": 1, "instance_id": 5, "hostname": "h"}]
    assert na.marked_refs({"instance_resolution": {"node_address": True,
                                                   "instance_refs": refs}}) == [
        {"source_id": "b0", "domain_id": 1, "instance_id": 5}]
    assert na.marked_refs({"instance_resolution": {"instance_refs": refs}}) == []
    assert na.marked_refs({"instance_resolution": {"node_address": "true",
                                                   "instance_refs": refs}}) == []
    assert na.marked_refs(None) == [] and na.marked_refs({}) == []
