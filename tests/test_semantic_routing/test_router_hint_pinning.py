"""3단 라우터 — 이번 턴 원문 위치 힌트의 결정적 DB 고정 (plans/113 F-1 · F-3).

결함(2026-09-22 로컬 MLX 실측 · plans/113 §1.2 #7): "공동존의 vm 중 …"에서 입력 파서는
`target_db_hints=['공동존']`을 결정적으로 보강하지만, 3단 `semantic_router`는 그 값을 읽지 않고
LLM 분류만 썼다. LLM은 "공동존" 별칭이 김포·여의도 **양쪽**에 있어도 한 곳만 골라 단일 DB
경로(김포만)로 갔다. 1·2단은 `_apply_turn_hint_pinning`으로 이미 고정한다 — 3단만 빠진 비대칭.

수정: 관련도 필터·정렬 뒤, 소유 검증·존 역질문 게이트 앞에서 같은 해소 함수
(`resolve_priority_db_ids`)로 고정한다. 존 그룹이 없는 DB는 분류 결과를 보존한다(1·2단의
전량 탈락을 복제하지 않는다). LLM·Redis·DB 0 — 분류는 대역으로 바꾼다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.input_parser import _ensure_location_hints
from src.routing.semantic_router import semantic_router
from src.state import create_initial_state

_B0, _GP, _YD, _ITAM = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam"
_ZONED = [_B0, _GP, _YD]
_QUERY = "공동존의 vm 중 최대 cpu와 최대 memory 서버 top 10을 정리해줘"


def _row(db_id: str, score: float = 0.95, ctx: str = "VM 중 최대 CPU·메모리 서버 상위 10") -> dict:
    return {
        "db_id": db_id,
        "relevance_score": score,
        "sub_query_context": ctx,
        "user_specified": False,
        "reason": "LLM 분류",
    }


def _state(query: str, hints: list[str] | None = None, **extra) -> dict:
    """입력 파서 결정적 보강(`_ensure_location_hints`)을 거친 parsed_requirements를 싣는다."""
    state = create_initial_state(query)
    parsed = {"original_query": query, "query_targets": ["cpu", "memory"],
              "target_db_hints": list(hints or [])}
    state["parsed_requirements"] = _ensure_location_hints(parsed, query)
    state.update(extra)
    return state


def _config(active: list[str], *, plan_loop: bool = False, exclusive: bool = True):
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = active
    config.multi_db.zone_group_exclusive = exclusive
    config.enable_semantic_routing = True
    config.tier3_plan_loop_enabled = plan_loop
    return config


async def _route(state: dict, classify, active: list[str] | None = None, **cfg) -> dict:
    with patch("src.routing.semantic_router._llm_classify", classify), \
            patch("src.routing.semantic_router._ownership_enabled", return_value=False):
        return await semantic_router(
            state, llm=AsyncMock(), app_config=_config(active or _ZONED, **cfg)
        )


def _ids(out: dict) -> list[str]:
    return [t["db_id"] for t in out["target_databases"]]


class TestGongdongjonFanout:
    """"공동존" → 김포·여의도 둘 다 — LLM이 어느 한쪽만 골라도."""

    @pytest.mark.asyncio
    async def test_llm_picks_gp_only_but_both_are_targets(self):
        out = await _route(_state(_QUERY), AsyncMock(return_value=[_row(_GP)]))

        assert _ids(out) == [_GP, _YD]
        assert out["is_multi_db"] is True
        assert out["db_scope_source"] == "hint"
        assert out["active_db_id"] == _GP
        # 분류 항목은 표지까지 그대로 재사용(1·2단과 같다), 채운 항목만 직접 지정 표지
        assert [t["user_specified"] for t in out["target_databases"]] == [False, True]
        # 집합 단위 지정 — 존 선택 고정과 같은 규칙(여럿이면 대표 없음)
        assert out["user_specified_db"] is None

    @pytest.mark.asyncio
    async def test_llm_picks_yd_only_order_follows_registry(self):
        out = await _route(_state(_QUERY), AsyncMock(return_value=[_row(_YD)]))
        assert _ids(out) == [_GP, _YD]
        assert out["is_multi_db"] is True

    @pytest.mark.asyncio
    async def test_classified_entry_is_reused_and_missing_one_is_synthesized(self):
        """분류 항목(정제 질의)은 재사용하고, 빠진 DB는 위치어를 걷어낸 질의로 채운다."""
        out = await _route(_state(_QUERY), AsyncMock(return_value=[_row(_GP, ctx="정제 질의")]))
        gp, yd = out["target_databases"]
        assert gp["sub_query_context"] == "정제 질의"
        assert "공동존" not in yd["sub_query_context"]
        assert "vm" in yd["sub_query_context"]
        assert yd["relevance_score"] == 1.0

    @pytest.mark.asyncio
    async def test_compound_gimpo_hint_narrows_to_gp(self):
        """파서가 "공동존 김포"를 한 힌트로 뽑으면(규칙 10 예시) 김포만 — 좁히는 표현은 좁힌다."""
        state = _state("공동존 김포 vm 중 최대 cpu 서버 top 10", hints=["공동존 김포"])
        assert state["parsed_requirements"]["target_db_hints"] == ["공동존 김포"]
        out = await _route(state, AsyncMock(return_value=[_row(_GP), _row(_YD, 0.8)]))

        assert _ids(out) == [_GP]
        assert out["is_multi_db"] is False
        assert out["db_scope_source"] == "hint"
        assert out["user_specified_db"] == _GP

    @pytest.mark.asyncio
    async def test_bank_and_gongdongjon_open_selects_all_three(self):
        """ZONE_GROUP_EXCLUSIVE=false(개방) — "은행존과 공동존" → b0·gp·yd."""
        state = _state("은행존과 공동존 서버 cpu 사용률 top 10")
        out = await _route(state, AsyncMock(return_value=[_row(_GP)]), exclusive=False)
        assert _ids(out) == [_B0, _GP, _YD]

    @pytest.mark.asyncio
    async def test_exclusive_does_not_pin_across_zone_groups(self):
        """상호배타에서 두 존 그룹에 걸친 해소(제품명 단독 "폴스타")는 고정하지 않는다 — 종전 분류.

        원문에 두 존 그룹이 함께 있으면 라우트 pre-gate가 먼저 역질문으로 끝낸다(아래 클래스).
        여기 오는 것은 제품명·부분 별칭 힌트뿐이라, 결정적으로 두 그룹에 펼치면
        D-143 후속3 위반이다.
        """
        rows = [_row(_B0)]
        out = await _route(
            _state("폴스타 서버 cpu top 10", hints=["폴스타"]), AsyncMock(return_value=rows)
        )
        assert out["target_databases"] == rows
        assert out["db_scope_source"] == "classified"

    @pytest.mark.asyncio
    async def test_open_product_hint_fans_out_like_tier12(self):
        """개방(false)이면 1·2단과 같다 — "폴스타" 단독은 폴스타 DB 전부(D-065 해소 규칙)."""
        out = await _route(
            _state("폴스타 서버 cpu top 10", hints=["폴스타"]),
            AsyncMock(return_value=[_row(_B0)]), exclusive=False,
        )
        assert _ids(out) == [_B0, _GP, _YD]


class TestUnchangedWithoutResolvableHint:
    """힌트 없음·해소 0건이면 종전과 동작·반환이 같다."""

    @pytest.mark.asyncio
    async def test_no_hint_keeps_classification(self):
        rows = [_row(_GP), _row(_YD, 0.6)]
        out = await _route(_state("vm 중 최대 cpu 서버 top 10"), AsyncMock(return_value=rows))

        assert out["target_databases"] == rows
        assert out["db_scope_source"] == "classified"
        assert out["user_specified_db"] is None

    @pytest.mark.asyncio
    async def test_no_hint_return_is_bit_identical(self):
        rows = [_row(_GP)]
        out = await _route(_state("vm 중 최대 cpu 서버 top 10"), AsyncMock(return_value=rows))
        assert out == {
            "target_databases": rows,
            "is_multi_db": False,
            "active_db_id": _GP,
            "user_specified_db": None,
            "routing_intent": "data_query",
            "db_scope_source": "classified",
            "current_node": "semantic_router",
        }

    @pytest.mark.asyncio
    async def test_unresolvable_hint_keeps_classification(self):
        rows = [_row(_GP)]
        out = await _route(
            _state("판교 서버 cpu", hints=["알 수 없는 위치"]), AsyncMock(return_value=rows)
        )
        assert out["target_databases"] == rows
        assert out["db_scope_source"] == "classified"


class TestZonelessTargetsArePreserved:
    """존 그룹이 없는 DB는 존 힌트로 좁혀지지 않는다(1·2단 전량 탈락을 복제하지 않는다)."""

    @pytest.mark.asyncio
    async def test_itam_classification_survives_zone_hint(self):
        itam = _row(_ITAM, 0.7, ctx="유지보수 계약 만료일")
        out = await _route(
            _state("공동존 서버의 유지보수 계약 만료일"),
            AsyncMock(return_value=[_row(_GP), itam]),
            active=[*_ZONED, _ITAM],
        )
        assert _ids(out) == [_GP, _YD, _ITAM]
        assert out["target_databases"][-1] == itam  # 분류 항목 그대로(고정 표시 없음)

    @pytest.mark.asyncio
    async def test_zoneless_only_hint_adds_without_dropping_zoned(self):
        """힌트가 존 없는 DB만 지목하면 존 그룹 대상은 좁히지 않는다(힌트가 존을 말하지 않았다)."""
        out = await _route(
            _state("ITAM 계약 만료 서버의 CPU 사용률", hints=["ITAM"]),
            AsyncMock(return_value=[_row(_GP)]),
            active=[*_ZONED, _ITAM],
        )
        assert _ids(out) == [_ITAM, _GP]
        assert out["target_databases"][0]["user_specified"] is True


class TestPinnedTargetsStillPassDownstreamGates:
    """고정은 대상 집합만 정한다 — 소유 교정은 그대로 받고, 존 역질문은 끈다(1·2단과 같다)."""

    @pytest.mark.asyncio
    async def test_reused_entry_is_not_marked_user_specified_for_ownership(self):
        """분류 항목에 직접 지정 표지를 새로 달면 소유 교정(plans/102 X-7)이 건너뛴다."""
        seen: list[list[dict]] = []

        def _spy(targets, *, active_db_ids):
            seen.append([dict(t) for t in targets])
            return targets, []

        with patch("src.routing.semantic_router._llm_classify",
                   AsyncMock(return_value=[_row(_GP)])), \
                patch("src.routing.semantic_router._ownership_enabled", return_value=True), \
                patch("src.routing.semantic_router.enforce_target_ownership", _spy), \
                patch("src.routing.semantic_router._ownership_state_fields", return_value={}):
            out = await semantic_router(
                _state(_QUERY), llm=AsyncMock(), app_config=_config(_ZONED)
            )
        assert [t["db_id"] for t in seen[0]] == [_GP, _YD]
        assert seen[0][0]["user_specified"] is False  # 분류 항목 — 교정 대상으로 남는다
        assert out["db_scope_source"] == "hint"

    @pytest.mark.asyncio
    async def test_zone_clarification_gate_is_off_when_pinned(self):
        """위치 표면어가 원문에 없는 힌트(파서 LLM 추출)로 고정돼도 존 역질문은 뜨지 않는다."""
        state = _state("운영 서버 cpu 사용률 top 10", hints=["공동존 김포 폴스타"],
                       zone_clarification_allowed=True)
        with patch("src.routing.semantic_router._zone_clarification_or_none_router") as gate:
            out = await _route(state, AsyncMock(return_value=[_row(_B0)]))
        gate.assert_not_called()
        assert _ids(out) == [_GP]
        assert out["routing_intent"] == "data_query"


class TestEarlyReturnPathsUnaffected:
    """조기 반환 경로(UI 존 선택 · 양식 매핑)는 힌트 고정을 거치지 않는다."""

    @pytest.mark.asyncio
    async def test_selected_db_ids_win_over_hint(self):
        classify = AsyncMock()
        out = await _route(_state(_QUERY, selected_db_ids=[_GP]), classify)
        classify.assert_not_awaited()
        assert _ids(out) == [_GP]
        assert out["db_scope_source"] == "selected"

    @pytest.mark.asyncio
    async def test_mapped_db_ids_win_over_hint(self):
        classify = AsyncMock()
        out = await _route(_state(_QUERY, mapped_db_ids=[_YD]), classify)
        classify.assert_not_awaited()
        assert _ids(out) == [_YD]
        assert out["db_scope_source"] == "planned"


class TestFallbackIsPinned:
    """분류 실패 폴백(첫 활성 DB)도 힌트가 있으면 고정 집합으로 바뀐다."""

    @pytest.mark.asyncio
    async def test_llm_error_fallback_replaced_by_hint(self):
        out = await _route(_state(_QUERY), AsyncMock(side_effect=RuntimeError("down")))
        assert _ids(out) == [_GP, _YD]

    @pytest.mark.asyncio
    async def test_empty_classification_replaced_by_hint(self):
        out = await _route(_state(_QUERY), AsyncMock(return_value=[]))
        assert _ids(out) == [_GP, _YD]


class TestTurnHintPinningParity:
    """`tests/test_orchestration/test_turn_hint_pinning.py`(1·2단) 케이스의 3단판."""

    @pytest.mark.asyncio
    async def test_bankzone_hint_overrides_gp_classification(self):
        out = await _route(
            _state("지난 하루동안 은행존에서 발생한 모든 알람", hints=["은행존"]),
            AsyncMock(return_value=[_row(_GP)]),
        )
        assert _ids(out) == [_B0]

    @pytest.mark.asyncio
    async def test_reuses_classify_target_when_db_matches(self):
        out = await _route(
            _state("은행존 알람", hints=["은행존"]),
            AsyncMock(return_value=[_row(_B0, ctx="지난 하루 알람 조회")]),
        )
        assert _ids(out) == [_B0]
        assert out["target_databases"][0]["sub_query_context"] == "지난 하루 알람 조회"

    @pytest.mark.asyncio
    async def test_cross_zone_hints_supplement_missing_db(self):
        """두 존 그룹 지목은 개방(ZONE_GROUP_EXCLUSIVE=false)에서만 라우터까지 온다
        (배타면 pre-gate)."""
        out = await _route(
            _state("은행존과 공동존 김포 알람", hints=["은행존", "공동존 김포 폴스타"]),
            AsyncMock(return_value=[_row(_GP)]), exclusive=False,
        )
        assert _ids(out) == [_B0, _GP]
        b0 = out["target_databases"][0]
        assert "은행존" not in b0["sub_query_context"]
        assert "김포" not in b0["sub_query_context"]


class TestZoneGroupExclusivePreGateComesFirst:
    """`ZONE_GROUP_EXCLUSIVE=true`에서 은행존+공동존 혼합 텍스트는 라우트 pre-gate가 먼저 막는다.

    라우터 고정은 그래프 안이라 pre-gate(`src/api/routes/query.py`)가 역질문으로 끝낸 턴에는
    도달하지 않는다 — 개방(false)일 때만 라우터가 세 DB를 고정한다(위 클래스).
    """

    def test_pre_gate_fires_on_mixed_zone_text_when_exclusive(self):
        from src.api.routes.query import _zone_group_exclusive_or_none

        config = _config(_ZONED)
        config.multi_db.zone_group_exclusive = True
        assert _zone_group_exclusive_or_none("은행존과 공동존 서버 cpu top 10", None, config)

    def test_pre_gate_is_silent_when_open(self):
        from src.api.routes.query import _zone_group_exclusive_or_none

        config = _config(_ZONED)
        config.multi_db.zone_group_exclusive = False
        query = "은행존과 공동존 서버 cpu top 10"
        assert _zone_group_exclusive_or_none(query, None, config) is None


class TestTier3PlanLoopInheritsPinnedTargets:
    """F-3 — 3단 계획 루프의 데이터 task는 라우터 대상을 이어 받는다(별도 수정 없이 자동 해소)."""

    @pytest.mark.asyncio
    async def test_composite_tasks_receive_both_dbs(self):
        from src.orchestration.tier3_plan import route_task_entry, task_payload

        state = _state(_QUERY)
        classify = AsyncMock(return_value={
            "intent": "data_query", "databases": [_row(_GP)], "needs_plan": True,
        })
        out = await _route(state, classify, plan_loop=True)
        assert out["needs_plan"] is True
        state.update(out)

        tasks = [
            {"task_id": "t1", "agent": "data_query", "sub_query": "공동존 VM 최대 CPU 상위 10"},
            {"task_id": "t2", "agent": "data_query", "sub_query": "공동존 VM 최대 메모리 상위 10"},
        ]
        for task in tasks:
            payload = task_payload(task, state, {}, total=len(tasks))
            assert [t["db_id"] for t in payload["target_databases"]] == [_GP, _YD]
            assert payload["is_multi_db"] is True
            assert payload["db_scope_source"] == "hint"
            assert route_task_entry(payload) == "multi_db_executor"


class TestTier3PlanLoopTaskScope:
    """F-2의 3단 계획 루프판 — 복합 task는 라우터 고정 집합 안에서 자기 위치 범위로 좁힌다."""

    @pytest.mark.asyncio
    async def test_location_split_tasks_narrow_within_router_pin(self):
        from src.orchestration.tier3_plan import task_payload

        query = "김포 서버 CPU top 10과 여의도 서버 메모리 top 10"
        state = _state(query)
        out = await _route(state, AsyncMock(return_value=[_row(_GP)]))
        assert [t["db_id"] for t in out["target_databases"]] == [_GP, _YD]
        state.update(out)
        state["is_composite"] = True
        gp = task_payload({"task_id": "t1", "agent": "data_query",
                           "sub_query": "김포 서버 CPU top 10"}, state, {}, total=2)
        yd = task_payload({"task_id": "t2", "agent": "data_query",
                           "sub_query": "여의도 서버 메모리 top 10"}, state, {}, total=2)
        assert [t["db_id"] for t in gp["target_databases"]] == [_GP]
        assert gp["is_multi_db"] is False
        assert [t["db_id"] for t in yd["target_databases"]] == [_YD]

    @pytest.mark.asyncio
    async def test_classified_router_targets_are_not_narrowed(self):
        """라우터가 힌트로 고정하지 않은 턴(분류 결과)은 task 범위로 건드리지 않는다."""
        from src.orchestration.tier3_plan import task_payload

        rows = [_row(_GP), _row(_YD, 0.8)]
        state = _state("vm 중 cpu top 10과 메모리 top 10")
        state.update(await _route(state, AsyncMock(return_value=rows)))
        state["is_composite"] = True
        payload = task_payload({"task_id": "t1", "agent": "data_query",
                                "sub_query": "김포 cpu top 10"}, state, {}, total=2)
        assert [t["db_id"] for t in payload["target_databases"]] == [_GP, _YD]
