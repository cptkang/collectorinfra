"""plans/210 W8 §6.2 독립 검증 — 구현자 테스트가 비운 통로·경계를 고정한다.

- 직전 대상 승계 통로 종단(플래그 on: 승계 인스턴스만 · 플래그 off: 승계 고지까지 지워 무기록 턴과
  답 동일) — 구현자 보고 「승계 종단 테스트 없음」 보충.
- 플래그 off 표지에서 대상 텍스트가 무엇이든(일부 0건 섞임 포함) 답은 대상 없는 W2 답과 같다 —
  W2는 대상을 받지 않으므로 변경 전 답 = 대상 없는 질의의 답이다(스냅샷 `a_text`와 대조).
- q4 실측 shape: 분해가 대상을 task `targets`가 아니라 `view_args[보기]["targets"]`에 넣었을 때.
- 노드 주소 보기 + 다른 대상 보기(플래그 off) — 노드 주소 보기는 W2 1회 그대로.
- 파드 부착 도구 호출 오류 원문이 답에 그대로 나가지 않는다(`_public_call_error` 경유).

실 게이트웨이·실 LLM 0 — W8 구현자 테스트의 모의 게이트웨이를 그대로 쓴다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from tests.test_orchestration import test_plan210_w8_node_address as w8

VIEW, TOOL, ORDER = w8.VIEW, w8.TOOL, w8.ORDER

LAST_ORDER1 = [{"text": "order-api-1", "kind": "instance", "source_id": "b0"}]
SCOPE_REC = {"ids": ["b0"], "basis": "named", "last_target_sources": ["b0"],
             "last_targets": LAST_ORDER1}
SCOPE_NONE = {"ids": ["b0"], "basis": "named", "last_target_sources": ["b0"]}


def _iso(query: str, scope: dict) -> dict:
    return {"parsed_requirements": {"filter_conditions": [], "time_range": None, "limit": None},
            "conversation_context": {"previous_sources": ["apm"]}, "thread_id": "th-1",
            "user_id": "alice", "original_user_query": query, "user_query": query,
            "apm_source_scope": scope}


async def _run_iso(views: list[str], iso: dict, **task_extra: Any) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query",
                            "sub_query": iso["user_query"], "input_from": [], "views": views,
                            **task_extra}
    return await aq.run_apm_query(task, iso, llm=None, app_config=w8._cfg(), now=w8.NOW)


def _texts(res: dict) -> list[str]:
    return [d["text"] for d in res.get("disclosures") or []]


# ── 직전 대상 승계 통로 (실 소스 사다리) ─────────────────────────────────────────────


@pytest.mark.apm_source_ladder
async def test_inherited_target_flag_on_queries_only_that_instance(monkeypatch):
    gw = w8._install(monkeypatch, w8._Gateway())
    res = await _run_iso([VIEW], _iso("노드 주소도", SCOPE_REC))
    assert not res.get("error"), res.get("error")
    assert [a.get("query") for a in gw.named("apm_instance_map")] == ["order-api-1"]
    [args] = gw.named(TOOL)
    assert args["instance_ids"] == [1] and args["domain_ids"] == [1000]
    assert args["source_ids"] == ["b0"]
    lines = list(res.get("answer_lines") or [])
    assert any("order-api-1" in x and "10.136.1.7" in x for x in lines)
    assert not any("order-api-2" in x for x in lines)
    assert any(t.startswith("직전 대상 ") for t in _texts(res)), "승계 고지 1줄"


@pytest.mark.apm_source_ladder
async def test_inherited_target_flag_off_matches_no_record_turn(monkeypatch):
    """플래그 off 표지 — 승계 고지·해석 고지를 지우고 W2 현행 1회(무기록 턴과 답 동일)."""
    w8._install(monkeypatch, w8._Gateway(flag=False))
    base = await _run_iso([VIEW], _iso("노드 주소도", SCOPE_NONE))
    gw = w8._install(monkeypatch, w8._Gateway(flag=False))
    res = await _run_iso([VIEW], _iso("노드 주소도", SCOPE_REC))
    assert w8._dump(w8._answer(res)) == w8._dump(w8._answer(base))
    [args] = gw.named(TOOL)
    assert "instance_ids" not in args


# ── 플래그 off · 대상 텍스트 변형 ────────────────────────────────────────────────────


async def test_flag_off_partly_unresolved_targets_answer_same_as_before(monkeypatch):
    """해석 1건 + 0건 1건이 섞여도(플래그 off) 답은 변경 전(W2는 대상을 받지 않는다)과 같다."""
    w8._install(monkeypatch, w8._Gateway(flag=False))
    res = await w8._run([VIEW], [*ORDER, {"text": "zzz-api", "kind": "instance"}])
    assert w8._dump(w8._answer(res)) == w8._dump(w8._BEFORE["a_text"]["answer"])


async def test_flag_off_all_unresolved_target_deviates_from_before(monkeypatch):
    """해석 0건이면 표지를 몰라 「찾지 못함」이다(독스트링 명시 편차) — 변경 전은 비활성 사유.

    수용 기준 ③ 「플래그 off 비트 동일」의 예외 — 현행 동작을 고정해 둔다(보고 Minor)."""
    gw = w8._install(monkeypatch, w8._Gateway(flag=False))
    res = await w8._run([VIEW], [{"text": "zzz-api", "kind": "instance"}])
    assert gw.named(TOOL) == []
    assert w8._dump(w8._answer(res)) != w8._dump(w8._BEFORE["a_text"]["answer"])
    assert "찾지 못" in json.dumps(w8._answer(res), ensure_ascii=False)


async def test_node_view_with_other_target_view_flag_off_keeps_w2_call(monkeypatch):
    gw = w8._install(monkeypatch, w8._Gateway(flag=False))
    await w8._run([VIEW, "apm.app_health"], ORDER, query="order-api 노드 주소와 응답시간")
    [args] = gw.named(TOOL)
    assert "instance_ids" not in args  # W2 현행 1회(대상으로 좁히지 않음)
    assert len(gw.named("apm_app_health")) + len(
        [a for n, a in gw.calls if n == "apm_batch"]) >= 1


# ── q4 실측 shape: view_args 안의 targets ────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    [{"text": "주문", "kind": "business"}],
    ["주문"],
    "주문",
])
async def test_q4_targets_in_view_args_is_lifted_to_task_targets(monkeypatch, value):
    """분해가 대상을 `view_args[apm.node_address].targets`에 넣어도 — 대상으로 해석해 그
    인스턴스만 조회한다(W8 교정 q4 · 모양 정규화) · 「버린 조건」 고지 없음."""
    gw = w8._install(monkeypatch, w8._Gateway())
    res = await w8._run([VIEW], None, query="주문 업무 WAS 파드들의 노드(호스트) IP 목록",
                        view_args={VIEW: {"targets": value}})
    [args] = gw.named(TOOL)
    assert args["instance_ids"] == [1, 2] and args["domain_ids"] == [1000]
    unresolved = [d["text"] for d in res.get("disclosures") or []
                  if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert unresolved == [], res.get("disclosures")
    assert any("order-api-1" in x for x in res.get("answer_lines") or [])


async def test_q4_unresolved_lifted_target_is_not_found_without_call(monkeypatch):
    gw = w8._install(monkeypatch, w8._Gateway())
    res = await w8._run([VIEW], None, query="결제 업무 노드 주소",
                        view_args={VIEW: {"targets": "결제"}})
    assert gw.named(TOOL) == []  # 해석 0건 — 전 도메인으로 넓히지 않는다(D-290 ⑥)
    assert "찾지 못" in json.dumps(w8._answer(res), ensure_ascii=False)


# ── 파드 부착 오류 원문 비노출 ──────────────────────────────────────────────────────


class _ErrGateway(w8._Gateway):
    async def call_tool(self, name: str, arguments: dict):
        if name == TOOL:
            self.calls.append((name, dict(arguments)))
            return SimpleNamespace(content=[SimpleNamespace(
                text="Traceback: secret=JSESSIONID=abc123 password=hunter2")], isError=True)
        return await super().call_tool(name, arguments)


async def test_pod_attach_error_text_is_not_leaked(monkeypatch):
    w8._install(monkeypatch, _ErrGateway())
    res = await w8._run(["apm.app_health"], ORDER, query="order-api 응답시간")
    text = json.dumps(w8._answer(res), ensure_ascii=False)
    assert "hunter2" not in text and "JSESSIONID" not in text
    lines = list(res.get("answer_lines") or [])
    assert len(lines) == 1 and "노드 주소 조회 실패" in lines[0]
    # 본 보기 결과는 그대로(부착 실패가 답 전체를 막지 않는다)
    assert res.get("query_results")


async def test_node_view_targeted_error_text_is_not_leaked(monkeypatch):
    w8._install(monkeypatch, _ErrGateway())
    res = await w8._run([VIEW], ORDER)
    text = json.dumps(w8._answer(res), ensure_ascii=False)
    assert "hunter2" not in text and "JSESSIONID" not in text


# ── 혼합 보기 + 직전 대상 승계 (게이트웨이 플래그 off) ─────────────────────────────


@pytest.mark.apm_source_ladder
async def test_mixed_views_inheritance_flag_off_changes_answer(monkeypatch):
    """[노드 주소 + 응답시간] 후속 턴 — W8은 노드 주소 보기를 대상별 보기로 세어 승계가 새로
    성립한다.

    변경 전(노드 주소 보기는 대상별 보기 아님)에는 이 조합이 승계하지 않았다 = 기록 없는 턴과
    같은 답.
    지금은 게이트웨이 플래그 off(표지 없음)여도 응답시간 보기가 직전 대상으로 좁혀진다 — 수용 기준 ③
    「플래그 off 비트 동일」 범위 밖의 편차를 현행 동작으로 고정한다(검증 보고 참조)."""
    w8._install(monkeypatch, w8._Gateway(flag=False))
    base = await _run_iso([VIEW, "apm.app_health"], _iso("노드 주소랑 응답시간도", SCOPE_NONE))
    gw = w8._install(monkeypatch, w8._Gateway(flag=False))
    res = await _run_iso([VIEW, "apm.app_health"], _iso("노드 주소랑 응답시간도", SCOPE_REC))
    assert w8._dump(w8._answer(res)) != w8._dump(w8._answer(base))
    assert [a.get("query") for a in gw.named("apm_instance_map")] == ["order-api-1"]
    assert any(t.startswith("직전 대상 ") for t in _texts(res))


# ── 여러 (소스, 도메인) · 대상 통로 혼합 ─────────────────────────────────────────────


async def test_targets_across_two_domains_call_once_per_domain(monkeypatch):
    gw = w8._install(monkeypatch, w8._Gateway())
    res = await w8._run([VIEW], [*ORDER, {"text": "vm-was", "kind": "instance"}],
                        query="order-api와 vm-was 노드 주소")
    calls = sorted((a["domain_ids"], a["instance_ids"]) for a in gw.named(TOOL))
    assert calls == [([1000], [1, 2]), ([2000], [7])]
    lines = list(res.get("answer_lines") or [])
    assert sum(("order-api-" in x) or ("vm-was01" in x) for x in lines) == 3
    assert not any("pay-api-1" in x for x in lines)


async def test_text_and_hostname_channels_merge_without_duplicate(monkeypatch):
    """분해 `targets`(이름)와 파서 hostname이 같은 인스턴스를 가리키면 한 번만 싣는다."""
    gw = w8._install(monkeypatch, w8._Gateway())
    res = await w8._run([VIEW], [{"text": "vm-was", "kind": "instance"}], hosts=("web01",),
                        query="web01 vm-was 노드 주소")
    [args] = gw.named(TOOL)
    assert args["instance_ids"] == [7]
    assert sum("vm-was01" in x for x in res.get("answer_lines") or []) == 1
