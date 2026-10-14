"""plans/157 W3 독립 검증 — 대상 생략 후속 승계의 종단 음성 사례(verifier).

구현 측 테스트(`test_plan157_apm_target_inherit.py`)는 C-2~C-5를 처리기 단위(손으로 만든
스레드 키)로 고정하고 종단은 C-1 2턴만 본다.
여기서는 실제 `build_graph()` · 2단 · 같은 thread_id 3턴으로
체크포인터 델타 병합(`apm_source_scope` 통째 교체 · 미기록 턴의 잔존)을 확인한다.

- C-2 종단: A 대상 → 목록 턴 → 대상 생략 = 승계 없음(목록 턴이 스코프를 갈아 끼워 기록이 사라진다)
- 설정 보기 턴 사이 · 새 대상 턴 사이(C-3 연쇄) · 새 소스 단어 턴 사이(C-4 연쇄) · 칩 해제(C-5)
- 연쇄 승계(A → 생략 → 생략)는 계속 A
- 소스 되묻기 턴(스코프 미기록) 사이: 직전 턴은 대상별 조회가 아니었으니 두 턴 전 대상을 잇지
  않는다(지적 사항 — 교정 라운드에서 고침 · 집계기가 `last_targets`만 뺀다)
- 복합 턴은 승계하지 않는다(지적 사항 — 교정 라운드에서 고침)
실 LLM·실 제니퍼·실 DB 0.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.state import create_followup_input
from tests.test_orchestration import apm_batch_mock
from tests.test_orchestration.test_plan134_w34_verify_real import _NoEdge
from tests.test_orchestration.test_plan157_apm_target_inherit import (
    LAST_ABC,
    SCOPE_ABC,
    _by_id,
    _decision,
    _Gateway,
    _isolated,
)
from tests.test_plan122_t_verify import build_env  # noqa: F401 — 픽스처

pytestmark = pytest.mark.apm_source_ladder

INHERIT = "직전 대상 "

Q_A = "김포 abc 최근 1시간 이벤트"
Q_SLOW = "느린 거래도 10분치"
PLANS: dict[str, dict[str, Any]] = {
    Q_A: {"views": ["apm.events"], "targets": [{"text": "abc", "kind": "instance"}]},
    Q_SLOW: {"views": ["apm.slow_tx"]},
    "응답시간도": {"views": ["apm.app_health"]},
    "제니퍼 인스턴스 리스트": {"views": ["apm.instances"]},
    "환경 설정 보여줘": {"views": ["apm.environment"]},
    "xyz 응답시간": {"views": ["apm.app_health"], "targets": [{"text": "xyz", "kind": "instance"}]},
    "은행존 제니퍼 느린 거래": {"views": ["apm.slow_tx"]},
    "은행존이랑 김포 제니퍼 응답시간": {"views": ["apm.app_health"]},
}


async def _env(build_env, monkeypatch) -> tuple[Any, _Gateway]:  # noqa: F811
    import src.graph as graph_module

    ip = importlib.import_module("src.orchestration.intent_planner")
    real_build = graph_module.build_graph
    seen: dict[str, Any] = {}

    async def _planner(state: Any, **kw: Any) -> dict:
        q = state["user_query"]
        result = {"task_plan": [{
            "task_id": "t1", "agent": "apm_query", "sub_query": q, "depends_on": [],
            "input_from": [], "order": 1, "status": "pending", **PLANS[q],
        }], "is_composite": False, "current_node": "intent_planner"}
        ip._apply_source_selection(result, state, seen["cfg"])
        return result

    def _build(cfg: Any, **kw: Any) -> Any:
        monkeypatch.setattr(graph_module, "intent_planner", _planner)
        cfg.dbhub.source_endpoints = {"apm": "http://127.0.0.1:9/sse"}
        seen["cfg"] = cfg
        return real_build(cfg, **kw)

    monkeypatch.setattr(graph_module, "build_graph", _build)
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    env, _ = build_env("t2", ["polestar"])
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    return env, gw


def _inherited(out: dict) -> bool:
    return INHERIT in (out.get("final_response") or "")


def _last(out: dict) -> Any:
    return (out.get("apm_source_scope") or {}).get("last_targets")


async def _first_turn(env: Any, gw: _Gateway, thread: str) -> None:
    first = await env.run(Q_A, thread)
    assert first.get("apm_source_clarification") is None
    assert _last(first) == LAST_ABC
    gw.calls.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("middle", ["제니퍼 인스턴스 리스트", "환경 설정 보여줘"])
async def test_e2e_list_or_settings_turn_between_blocks_inherit(build_env, monkeypatch,  # noqa: F811
                                                                middle) -> None:
    """C-2 종단: A → 목록/설정 턴 → 대상 생략 = 승계 없음.

    목록 턴이 스코프를 통째로 갈아 끼워 기록을 지운다."""
    env, gw = await _env(build_env, monkeypatch)
    thread = f"v157-mid-{len(middle)}"
    await _first_turn(env, gw, thread)

    mid = await env.run(middle, thread, followup=True)
    assert mid.get("apm_source_clarification") is None
    assert mid["apm_source_scope"]["ids"] == ["cm_prd"], "소스는 승계"
    assert _last(mid) is None, "목록·설정 턴은 기록을 남기지 않고 옛 기록도 지운다"
    assert not _inherited(mid)
    gw.calls.clear()

    third = await env.run(Q_SLOW, thread, followup=True)
    assert gw.searched() == [], "직전 턴이 대상별 조회가 아니면 이름 검색(승계) 없음"
    calls = apm_batch_mock.expanded(gw.calls, "apm_slow_transactions")
    assert "abc-was01" not in {c.get("instance_name") for c in calls}, "두 턴 전 대상 조회 없음"
    # (목록 턴 행의 hostname이 `previous_entities`로 이어지는 것은 종전 경로 — 이 계획 밖)
    assert not _inherited(third)


@pytest.mark.asyncio
async def test_e2e_chain_inherits_same_target(build_env, monkeypatch) -> None:  # noqa: F811
    """A → 생략 → 생략: 승계 턴이 재료를 다시 남겨 같은 대상을 계속 잇는다(대상 확장 없음)."""
    env, gw = await _env(build_env, monkeypatch)
    thread = "v157-chain"
    await _first_turn(env, gw, thread)
    for q in (Q_SLOW, "응답시간도"):
        out = await env.run(q, thread, followup=True)
        assert gw.searched() == ["abc-was01"], q
        assert "apm_fleet" not in gw.tools(), q
        assert _inherited(out), q
        assert _last(out) == LAST_ABC, q
        gw.calls.clear()


@pytest.mark.asyncio
async def test_e2e_new_target_then_ellipsis_inherits_new(build_env, monkeypatch) -> None:  # noqa: F811
    """C-3 연쇄: A → 「xyz 응답시간」 → 생략 = xyz를 잇는다(A 아님)."""
    from tests.test_orchestration import test_plan157_apm_target_inherit as impl

    monkeypatch.setitem(impl.SEARCH, "xyz-was07", [("cm_prd", 7, "xyz-was07")])
    env, gw = await _env(build_env, monkeypatch)
    thread = "v157-c3"
    await _first_turn(env, gw, thread)
    mid = await env.run("xyz 응답시간", thread, followup=True)
    assert gw.searched() == ["xyz"] and not _inherited(mid)
    assert _last(mid) == [{"text": "xyz-was07", "kind": "instance", "source_id": "cm_prd"}]
    gw.calls.clear()
    third = await env.run(Q_SLOW, thread, followup=True)
    assert gw.searched() == ["xyz-was07"]
    calls = apm_batch_mock.expanded(gw.calls, "apm_slow_transactions")
    assert [c["instance_name"] for c in calls] == ["xyz-was07"]
    assert _inherited(third)


@pytest.mark.asyncio
async def test_e2e_new_source_word_then_ellipsis_no_inherit(build_env, monkeypatch) -> None:  # noqa: F811
    """C-4 연쇄: A(김포) → 「은행존 제니퍼 느린 거래」(새 소스 · 대상 없음) → 생략 = 승계 없음."""
    env, gw = await _env(build_env, monkeypatch)
    thread = "v157-c4"
    await _first_turn(env, gw, thread)
    mid = await env.run("은행존 제니퍼 느린 거래", thread, followup=True)
    assert gw.searched() == [] and not _inherited(mid)
    assert mid["apm_source_scope"]["ids"] == ["b0"]
    assert _last(mid) is None
    gw.calls.clear()
    third = await env.run("응답시간도", thread, followup=True)
    assert gw.searched() == []
    assert all(a.get("source_ids") == ["b0"] for _, a in gw.calls)
    assert not _inherited(third)


@pytest.mark.asyncio
async def test_e2e_chip_reset_then_ellipsis_no_inherit(build_env, monkeypatch) -> None:  # noqa: F811
    """C-5 종단: A → 칩 × 와 함께 생략 후속 = 기록이 키째 비어 승계 없음(소스 되묻기로 간다)."""
    env, gw = await _env(build_env, monkeypatch)
    thread = "v157-c5"
    await _first_turn(env, gw, thread)
    reset = create_followup_input(Q_SLOW, reset_apm_source_scope=True)
    out = await env.run(Q_SLOW, thread, followup=True,
                        extra={"apm_source_scope": reset["apm_source_scope"]})
    assert gw.searched() == []
    assert not _inherited(out)
    assert out.get("apm_source_clarification") is not None, "승계 소스가 없어 되묻는다(단 4)"


@pytest.mark.asyncio
async def test_e2e_source_ask_turn_between_blocks_inherit(build_env, monkeypatch) -> None:  # noqa: F811
    """A → 소스 되묻기 턴(두 소스 지목 · 조회 0) → 생략 = 승계 없음이어야 한다."""
    env, gw = await _env(build_env, monkeypatch)
    thread = "v157-ask"
    await _first_turn(env, gw, thread)
    mid = await env.run("은행존이랑 김포 제니퍼 응답시간", thread, followup=True)
    assert mid.get("apm_source_clarification") is not None, "전제: 소스 되묻기 턴"
    gw.calls.clear()
    third = await env.run(Q_SLOW, thread, followup=True)
    assert gw.searched() == [], "직전 턴은 대상별 조회가 아니었다 — 두 턴 전 대상 승계 금지"
    assert not _inherited(third)


def test_composite_turn_does_not_inherit() -> None:
    iso = {**_isolated("xyz 응답시간이랑 느린 거래", scope=SCOPE_ABC), "is_composite": True}
    assert aq.inherited_targets({}, iso, ["apm.slow_tx"], _by_id(), _decision()) == []
