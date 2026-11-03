"""plans/210 W9 §6.3 축 B 독립 검증 — 서버명 직행 보기의 파드 노드 주소 부착 경계.

구현자 테스트(`test_plan210_w9_host_node.py`)의 모의 게이트웨이를 그대로 쓴다. 실 게이트웨이·
실 LLM 0.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from tests.test_orchestration import test_plan210_w8_node_address as w8
from tests.test_orchestration import test_plan210_w9_host_node as w9

TOOL = w9.TOOL
HEALTH = w9.HEALTH
RUNTIME = w9.RUNTIME


async def test_two_views_same_host_share_one_node_call(monkeypatch):
    """같은 서버를 두 보기(응답시간·런타임)가 부르면 (소스, 도메인) 노드 주소 도구 1회 · 줄 1개."""
    gw = w8._install(monkeypatch, w9._Gateway())
    res = await w8._run(views=[HEALTH, RUNTIME], hosts=("app01",), query="app01 응답시간과 힙")
    [args] = gw.named(TOOL)
    assert args["domain_ids"] == [1000] and args["instance_ids"] == [1]
    assert w8._lines(res) == [w9._line("order-api-1", "10.136.1.7")]


async def test_pod_and_vm_hosts_in_two_domains(monkeypatch):
    """파드 서버 + VM 서버(도메인 다름) — 도구는 (소스, 도메인)마다 1회(2회) · 줄은 파드만."""
    gw = w8._install(monkeypatch, w9._Gateway())
    res = await w8._run(views=[HEALTH], hosts=("app01", "web01"), query="app01 web01 응답시간")
    assert sorted(a["domain_ids"][0] for a in gw.named(TOOL)) == [1000, 2000]
    lines = w8._lines(res)
    assert lines == [w9._line("order-api-1", "10.136.1.7")]
    assert not any("vm-was01" in x for x in lines)


async def test_batch_with_one_unresolved_host_attaches_only_resolved(monkeypatch):
    """배치 대상 중 하나가 정합 실패 — 실패 항목은 표지를 보지 않고 나머지만 부착."""
    gw = w8._install(monkeypatch, w9._Gateway())
    res = await w8._run(views=[HEALTH], hosts=("app01", "nohost01"),
                        query="app01 nohost01 응답시간")
    [health] = gw.named("apm_app_health")
    assert len(health.get("targets") or []) == 2  # 배치 1호출
    [args] = gw.named(TOOL)
    assert args["instance_ids"] == [1]
    assert w9._line("order-api-1", "10.136.1.7") in w8._lines(res)


class _EmptyRefs(w9._Gateway):
    """표지는 있으나 `instance_refs`가 빈 봉투(정합 0건이지만 오류가 아닌 경우)."""

    def _data(self, name: str, args: dict) -> dict:
        env = super()._data(name, args)
        if "instance_resolution" in env:
            env["instance_resolution"]["instance_refs"] = []
        return env


async def test_marker_without_refs_makes_no_call(monkeypatch):
    gw = w8._install(monkeypatch, _EmptyRefs())
    res = await w8._run(**w9._OFF["host_pod"])
    assert gw.named(TOOL) == [] and w8._lines(res) == []


class _NodeToolRaises(w9._Gateway):
    """노드 주소 도구 호출이 예외(연결 끊김 등) — 원문에 URL·토큰 모양 문자열."""

    async def call_tool(self, name: str, arguments: dict):
        if name == TOOL:
            self.calls.append((name, dict(arguments)))
            raise RuntimeError("connect http://gw.internal:9000/sse token=SECRET-TOKEN-1")
        return await super().call_tool(name, arguments)


async def test_node_tool_exception_is_one_public_line_without_raw_text(monkeypatch):
    """노드 주소 도구 예외 — 다른 보기 답은 그대로 · 실패 줄 1개 · 원문(URL·토큰) 미노출."""
    gw = w8._install(monkeypatch, _NodeToolRaises())
    res = await w8._run(**w9._OFF["host_pod"])
    assert gw.named("apm_app_health")
    lines = w8._lines(res)
    node = [x for x in lines if "노드 주소" in x]
    assert len(node) == 1 and "노드 주소 조회 실패" in node[0]
    dumped = w8._dump(w8._answer(res))
    assert "SECRET-TOKEN-1" not in dumped and "gw.internal" not in dumped


JOB_ID = "a" * 32


class _AcceptedBatch(w9._Gateway):
    """배치(`targets`) 데이터 호출이 작업으로 승격(접수)되고 조회 마감까지 끝나지 않는다."""

    async def call_tool(self, name: str, arguments: dict):
        if name == "apm_app_health" and arguments.get("targets"):
            self.calls.append((name, dict(arguments)))
            env = {"rows": [], "row_count": 0, "tool": name,
                   "job": {"job_id": JOB_ID, "state": "running"}}
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))],
                                   isError=False)
        return await super().call_tool(name, arguments)


async def test_accepted_batch_makes_no_node_call(monkeypatch):
    """작업 승격 배치(W8 `accepted` 경로) — 대상별 봉투가 없어 노드 주소 도구 0 · 예외 없음 ·
    접수 고지는 그대로(`_unbatched` 뒤로 옮긴 부착 단계가 접수 배치를 깨지 않는다)."""
    from src.orchestration import apm_jobs

    async def _never(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(apm_jobs, "poll_job", _never)
    gw = w8._install(monkeypatch, _AcceptedBatch())
    res = await w8._run(**w9._OFF["host_batch"])
    assert gw.named(TOOL) == []
    assert not any("노드 주소" in x for x in w8._lines(res))
    assert JOB_ID in json.dumps(res, default=str)  # 접수 작업이 답에 남는다
