"""SQL 생성 루프 — 산문 조기 종결 · 재생성 시간 게이트 · 진입 전 검사 · 종결 표지 (plans/119).

- **N-5**(114 T-2): 2단 단일 DB 루프(`subagents._run_single_db_pipeline`)와 멀티 DB 재생성
  루프(`multi_db_executor._generate_validated_sql`)가 그래프 경로와 **같은 상수**
  (`NON_SQL_RETRY_BUDGET`)로 산문(비-SQL) 응답을 끝낸다. 산문을 계속 내는 모의 생성기로
  생성 호출 수를 센다 — 단일 4회 → 2회, 멀티 3회 → 2회. 일반 검증 실패는 종전 예산
  (단일 4회 · 멀티 3회) 그대로다.
- **T-3**: 재생성 직전 `조회 마감까지 남은 시간 < 방금 잰 직전 생성 소요`면 재생성하지 않는다(검증
  실패·실행 오류 모두 · 두 루프 대칭). 시계는 모듈의 `_monotonic`을 바꿔 끼워 결정적으로 돌린다.
- **T-1ⓑ**: 스키마 분석·SQL 생성 **진입 전** 조회 마감이 지났으면 시작하지 않고 사유를 남긴다.
- **종결 표지**: 유효 SQL 없이 끝난 실패 task 결과에 `regen_stop`(`{"reason", "detail"}`)을 싣고,
  성공 task에는 싣지 않는다(재계획기 Q-3 계약).
- 마감(`request_deadline`)이 없으면(CLI·테스트) 시간 게이트는 발동하지 않는다(종전 동작).

전부 mock — LLM·DB·네트워크 미사용.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.state import create_initial_state

# 패키지 `__init__`이 같은 이름의 노드 함수를 재노출해 `import … as`가 함수를 가리킬 수
# 있다 — 모듈을 직접 얻는다.
graph = importlib.import_module("src.graph")
mdb = importlib.import_module("src.nodes.multi_db_executor")
qv = importlib.import_module("src.nodes.query_validator")
sub = importlib.import_module("src.orchestration.subagents")

PROSE = (
    "요청하신 '프로세스 이력'은 제공된 스키마에 없습니다. "
    "어떤 서버의 어떤 지표가 필요하신가요?"
)
VALID_SQL = "SELECT hostname FROM inventory"
TABLELESS_SQL = "SELECT 1"  # SQL이지만 검증에 걸린다 — 일반 재시도 경로(plans/114 P-6)
RESERVE = 15.0


# ──────────────────────────────────────────────
# 공통 대역
# ──────────────────────────────────────────────

def _cfg(max_retry: int = 3, reserve: float = RESERVE):
    """검증 대상 필드만 둔 설정 대역(.env 누수 차단)."""
    return SimpleNamespace(
        query=SimpleNamespace(max_retry_count=max_retry, default_limit=1000),
        get_polestar_db_ids=lambda: set(),
        server=SimpleNamespace(answer_reserve_sec=reserve),
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: ["polestar_b0"], zone_group_exclusive=True,
        ),
        polestar_rest=SimpleNamespace(realtime_usage_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        text2sql=SimpleNamespace(multi_full_validation=False, alarm_deterministic=False),
    )


class _Clock:
    """모의 단조 시계 — 생성 1회마다 `gen_cost`초가 흐른다."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


def _deadline(clock: _Clock, retrieval_left: float) -> float:
    """조회 마감까지 `retrieval_left`초가 남도록 처리 마감(request_deadline)을 만든다."""
    return clock.t + RESERVE + retrieval_left


def _patch_single_nodes(monkeypatch, gen_outputs, *, clock=None, gen_cost=0.0,
                        exec_errors=None):
    """단일 DB 파이프라인의 노드를 대역으로 바꾼다 — 검증은 **실제 노드**(산문 판정 포함)."""
    calls = {"schema": 0, "gen": 0, "exec": 0, "organize": 0}
    outs = list(gen_outputs)
    errs = list(exec_errors or [])

    async def _schema(state, llm=None, app_config=None):
        calls["schema"] += 1
        return {"schema_info": {"tables": {}}}

    async def _gen(state, llm=None, app_config=None):
        # 실제 query_generator 규약: 직전 실패(error_message)가 있으면 retry_count +1,
        # 사유는 비운다.
        calls["gen"] += 1
        if clock is not None:
            clock.t += gen_cost
        sql = outs[min(calls["gen"], len(outs)) - 1]
        rc = state.get("retry_count", 0) + (1 if state.get("error_message") else 0)
        return {"generated_sql": sql, "retry_count": rc, "error_message": None}

    async def _exec(state, app_config=None):
        calls["exec"] += 1
        err = errs.pop(0) if errs else None
        if err:
            return {"error_message": err, "query_results": []}
        return {"error_message": None, "query_results": [{"hostname": "h1"}]}

    async def _organize(state, llm=None, app_config=None):
        calls["organize"] += 1
        rows = state.get("query_results") or []
        return {"organized_data": {"rows": rows, "summary": ""}, "error_message": None}

    monkeypatch.setattr(sub, "schema_analyzer", _schema)
    monkeypatch.setattr(sub, "query_generator", _gen)
    monkeypatch.setattr(sub, "query_executor", _exec)
    monkeypatch.setattr(sub, "result_organizer", _organize)
    if clock is not None:
        monkeypatch.setattr(sub, "_monotonic", clock)
    return calls


def _single_isolated(**over):
    """계획이 DB를 고정한 단일 task의 격리 입력(분류·존 게이트를 건너뛴다)."""
    base = {
        "user_query": "프로세스 이력 보여줘",
        "original_user_query": "프로세스 이력 보여줘",
        "parsed_requirements": {"original_query": "프로세스 이력 보여줘", "query_targets": ["x"]},
        "conversation_context": None,
        "allowed_db_ids": None,
        "user_role": None,
        "selected_db_ids": None,
        "zone_selection_db_ids": None,
        "realtime_usage_intent": False,
        "zone_clarification_allowed": False,
        "retry_count": 0,
        "error_message": None,
        "validation_result": {"passed": False, "reason": "", "auto_fixed_sql": None},
        "organized_data": {"rows": [], "summary": ""},
        "query_results": [],
        "generated_sql": "",
        "request_deadline": None,
    }
    base.update(over)
    return base


_TASK = {"task_id": "t1", "agent": "data_query", "sub_query": "프로세스 이력",
         "db_ids": ["polestar_b0"]}


async def _run_task(isolated=None, cfg=None):
    return await sub.run_data_query_pipeline(
        dict(_TASK), isolated if isolated is not None else _single_isolated(),
        llm=MagicMock(), app_config=cfg or _cfg(),
    )


# ──────────────────────────────────────────────
# 단일 출처
# ──────────────────────────────────────────────

class TestSingleSource:
    def test_budget_constant_is_one_object(self):
        """사본 금지 — 그래프·2단·멀티가 같은 객체를 쓴다(종전 이름 `graph.…` 유지)."""
        assert qv.NON_SQL_RETRY_BUDGET == 1
        assert graph.NON_SQL_RETRY_BUDGET is qv.NON_SQL_RETRY_BUDGET
        assert mdb.NON_SQL_RETRY_BUDGET is qv.NON_SQL_RETRY_BUDGET
        assert callable(graph._non_sql_exhausted)

    def test_non_sql_judgment_shared_by_both_validators(self):
        """단일(검증 코어 문구)·멀티(간이 검증 문구) 두 사유를 같은 함수가 산문으로 판정한다."""
        assert qv.is_non_sql_prose(PROSE, ["SELECT 문만 허용됩니다. 감지된 타입: UNKNOWN"])
        assert qv.is_non_sql_prose(PROSE, ["SELECT 문이 아닙니다."])
        assert not qv.is_non_sql_prose(TABLELESS_SQL, ["테이블을 하나도 조회하지 않는 상수 SELECT"])

    def test_pii_block_text_is_not_prose(self):
        """PII 차단 안내문은 원인이 달라 산문 예산 대상이 아니다(D-153 후속2 — 종전 경로)."""
        blocked = "The request was blocked by the filter."
        assert not qv.is_non_sql_prose(blocked, ["SELECT 문만 허용됩니다. 감지된 타입: UNKNOWN"])


# ──────────────────────────────────────────────
# N-5 — 2단 단일 DB 루프
# ──────────────────────────────────────────────

class TestSingleNonSqlBudget:
    @pytest.mark.asyncio
    async def test_prose_chain_stops_after_two_generations(self, monkeypatch):
        """산문을 계속 내면 생성 2회로 끝난다(종전 4회 — 일반 예산 3까지 돌았다)."""
        calls = _patch_single_nodes(monkeypatch, [PROSE])
        out = await sub._run_single_db_pipeline(_single_isolated(), MagicMock(), _cfg())
        assert calls["gen"] == 2 and calls["exec"] == 0
        assert out["regen_stop"]["reason"] == qv.REGEN_STOP_NON_SQL
        assert out["regen_stop"]["detail"].startswith("SELECT 문만 허용됩니다")
        assert out["error_message"]

    @pytest.mark.asyncio
    async def test_prose_then_sql_recovers(self, monkeypatch):
        """retry=0 산문은 한 번 더 생성한다 — 실측 회복의 7/10이 여기서 난다."""
        calls = _patch_single_nodes(monkeypatch, [PROSE, VALID_SQL])
        out = await sub._run_single_db_pipeline(_single_isolated(), MagicMock(), _cfg())
        assert calls["gen"] == 2 and calls["exec"] == 1
        assert "regen_stop" not in out and not out.get("error_message")

    @pytest.mark.asyncio
    async def test_ordinary_failure_keeps_full_budget(self, monkeypatch):
        """산문이 아닌 검증 실패는 종전 예산(3) 그대로 — 생성 4회."""
        calls = _patch_single_nodes(monkeypatch, [TABLELESS_SQL])
        out = await sub._run_single_db_pipeline(_single_isolated(), MagicMock(), _cfg())
        assert calls["gen"] == 4
        assert out["regen_stop"]["reason"] == qv.REGEN_STOP_VALIDATION_BUDGET
        assert "상수 SELECT" in out["regen_stop"]["detail"]

    @pytest.mark.asyncio
    async def test_task_result_carries_graph_form_prose(self, monkeypatch):
        """2단 task 결과 본문 = 그래프 `error_response`의 산문 응답(같은 함수 · 경로 대칭).

        결과 정리를 건너뛰고 `organized_data=None` + `final_response`로 싣는다 — 집계기
        `_finalize_task`가 텍스트 계열로 그대로 쓴다(0행 "데이터가 없습니다"로 덮이지 않는다).
        """
        calls = _patch_single_nodes(monkeypatch, [PROSE])
        out = await _run_task()
        assert calls["organize"] == 0
        assert out["organized_data"] is None
        assert out["error"] and out["regen_stop"]["reason"] == "non_sql"
        # 그래프 경로 응답과 바이트 동일(구조 노트가 없는 상태)
        gstate = create_initial_state(user_query="프로세스 이력 보여줘")
        gstate["validation_result"] = {"passed": False, "reason": "x", "non_sql": True}
        gstate["retry_count"] = 1
        gstate["generated_sql"] = PROSE
        assert out["final_response"] == graph._error_response_node(gstate)["final_response"]
        assert PROSE in out["final_response"]

    @pytest.mark.asyncio
    async def test_aggregator_surfaces_prose_not_empty_notice(self, monkeypatch):
        """집계기 최종화까지 — 산문이 응답에 실리고 0건 안내로 바뀌지 않는다."""
        from src.orchestration.result_aggregator import _finalize_task

        _patch_single_nodes(monkeypatch, [PROSE])
        res = await _run_task()
        f = await _finalize_task(
            {"task_id": "t1", "agent": "data_query", "order": 0, "sub_query": "프로세스 이력"},
            res, {"user_query": "프로세스 이력 보여줘"}, MagicMock(), MagicMock(),
        )
        assert PROSE in f["text"] and "데이터가 없습니다" not in f["text"]
        assert f["error"]

    @pytest.mark.asyncio
    async def test_success_task_has_no_regen_stop(self, monkeypatch):
        _patch_single_nodes(monkeypatch, [VALID_SQL])
        out = await _run_task()
        assert "regen_stop" not in out and "error" not in out
        assert out["organized_data"]["rows"] == [{"hostname": "h1"}]

    @pytest.mark.asyncio
    async def test_validation_budget_keeps_organized_path(self, monkeypatch):
        """검증 소진은 종전 응답 경로(결과 정리) 그대로 — 표지만 더한다."""
        calls = _patch_single_nodes(monkeypatch, [TABLELESS_SQL])
        out = await _run_task()
        assert calls["organize"] == 1 and out["organized_data"] is not None
        assert out["regen_stop"]["reason"] == "validation_budget"

    @pytest.mark.asyncio
    async def test_exec_budget_exhaustion_has_no_regen_stop(self, monkeypatch):
        """실행 오류로 예산을 다 쓴 것은 검증 루프 종결이 아니다 — 표지 없음(계약 범위 밖)."""
        _patch_single_nodes(monkeypatch, [VALID_SQL], exec_errors=["syntax error"] * 5)
        out = await _run_task()
        assert out["error"] and "regen_stop" not in out


# ──────────────────────────────────────────────
# T-3 · T-1ⓑ — 단일 DB 루프
# ──────────────────────────────────────────────

class TestSingleDeadline:
    @pytest.mark.asyncio
    async def test_regeneration_skipped_when_last_generation_does_not_fit(self, monkeypatch):
        """남은 조회 시간 10초 < 방금 잰 생성 20초 → 재생성하지 않는다(생성 1회)."""
        clock = _Clock()
        calls = _patch_single_nodes(monkeypatch, [TABLELESS_SQL], clock=clock, gen_cost=20.0)
        iso = _single_isolated(request_deadline=_deadline(clock, 30.0))
        out = await sub._run_single_db_pipeline(iso, MagicMock(), _cfg())
        assert calls["gen"] == 1
        stop = out["regen_stop"]
        assert stop["reason"] == qv.REGEN_STOP_DEADLINE
        assert "상수 SELECT" in stop["detail"]  # 마지막 검증 사유
        assert "10초 < 직전 SQL 생성 20초" in out["regen_stop_response"]
        assert "마지막 실패 사유" in out["regen_stop_response"]

    @pytest.mark.asyncio
    async def test_deadline_after_prose_keeps_the_prose(self, monkeypatch):
        """첫 산출이 산문이고 시간이 없으면 산문(되물음)을 버리지 않고 시간 사유와 함께 싣는다."""
        clock = _Clock()
        calls = _patch_single_nodes(monkeypatch, [PROSE], clock=clock, gen_cost=20.0)
        iso = _single_isolated(request_deadline=_deadline(clock, 30.0))
        out = await sub._run_single_db_pipeline(iso, MagicMock(), _cfg())
        assert calls["gen"] == 1
        assert out["regen_stop"]["reason"] == "deadline"
        assert out["regen_stop"]["detail"].startswith("SQL 검증 실패: SELECT 문만 허용됩니다")
        response = out["regen_stop_response"]
        assert response.startswith(qv.non_sql_prose_response(PROSE))
        assert "답변할 시간을 남기려고" in response and "마지막 실패 사유" not in response

    @pytest.mark.asyncio
    async def test_regeneration_allowed_when_time_fits(self, monkeypatch):
        """남은 시간이 직전 소요 이상이면 종전대로 재생성한다(예산 3 소진까지 4회)."""
        clock = _Clock()
        calls = _patch_single_nodes(monkeypatch, [TABLELESS_SQL], clock=clock, gen_cost=1.0)
        iso = _single_isolated(request_deadline=_deadline(clock, 100.0))
        out = await sub._run_single_db_pipeline(iso, MagicMock(), _cfg())
        assert calls["gen"] == 4 and out["regen_stop"]["reason"] == "validation_budget"

    @pytest.mark.asyncio
    async def test_exec_error_regeneration_uses_same_gate(self, monkeypatch):
        """실행 오류 재생성도 같은 게이트 — 사유는 실행 오류다."""
        clock = _Clock()
        calls = _patch_single_nodes(
            monkeypatch, [VALID_SQL], clock=clock, gen_cost=20.0, exec_errors=["syntax error"],
        )
        iso = _single_isolated(request_deadline=_deadline(clock, 30.0))
        out = await sub._run_single_db_pipeline(iso, MagicMock(), _cfg())
        assert calls["gen"] == 1 and calls["exec"] == 1
        assert out["regen_stop"] == {"reason": "deadline", "detail": "syntax error"}

    @pytest.mark.asyncio
    async def test_schema_analysis_not_started_after_deadline(self, monkeypatch):
        """T-1ⓑ: 조회 마감이 이미 지났으면 스키마 분석도 생성도 시작하지 않는다(사유 남김)."""
        clock = _Clock()
        calls = _patch_single_nodes(monkeypatch, [VALID_SQL], clock=clock)
        iso = _single_isolated(request_deadline=_deadline(clock, -1.0))
        out = await sub._run_single_db_pipeline(iso, MagicMock(), _cfg())
        assert calls == {"schema": 0, "gen": 0, "exec": 0, "organize": 0}
        assert out["regen_stop"]["reason"] == "deadline"
        assert "스키마 분석" in out["error_message"] and "조회 마감 경과" in out["error_message"]

    @pytest.mark.asyncio
    async def test_first_generation_not_started_when_schema_used_up_time(self, monkeypatch):
        """T-1ⓑ: 스키마 분석이 조회 마감을 넘기면 첫 생성을 시작하지 않는다."""
        clock = _Clock()
        calls = _patch_single_nodes(monkeypatch, [VALID_SQL], clock=clock)

        async def _slow_schema(state, llm=None, app_config=None):
            calls["schema"] += 1
            clock.t += 50.0
            return {"schema_info": {"tables": {}}}

        monkeypatch.setattr(sub, "schema_analyzer", _slow_schema)
        iso = _single_isolated(request_deadline=_deadline(clock, 30.0))
        out = await sub._run_single_db_pipeline(iso, MagicMock(), _cfg())
        assert calls["schema"] == 1 and calls["gen"] == 0
        assert "SQL 생성" in out["regen_stop"]["detail"]

    @pytest.mark.asyncio
    async def test_deadline_stop_surfaces_in_task_result(self, monkeypatch):
        """task 결과 본문에 사유 문구가 실린다(침묵 금지) — 결과 정리는 건너뛴다."""
        clock = _Clock()
        calls = _patch_single_nodes(monkeypatch, [TABLELESS_SQL], clock=clock, gen_cost=20.0)
        out = await _run_task(isolated=_single_isolated(request_deadline=_deadline(clock, 30.0)))
        assert calls["organize"] == 0 and out["organized_data"] is None
        assert "답변할 시간을 남기려고" in out["final_response"]
        assert out["regen_stop"]["reason"] == "deadline"

    @pytest.mark.asyncio
    async def test_no_deadline_means_legacy_behaviour(self, monkeypatch):
        """마감이 없으면(CLI·테스트) 시계가 아무리 흘러도 게이트는 발동하지 않는다."""
        clock = _Clock()
        calls = _patch_single_nodes(monkeypatch, [TABLELESS_SQL], clock=clock, gen_cost=10_000.0)
        out = await sub._run_single_db_pipeline(_single_isolated(), MagicMock(), _cfg())
        assert calls["gen"] == 4 and out["regen_stop"]["reason"] == "validation_budget"

    def test_isolated_input_carries_request_deadline(self):
        """격리 입력이 요청 마감을 운반한다 — 없으면 게이트 입력이 비어 종전 동작이 된다."""
        state = {"user_query": "q", "request_deadline": 1234.5, "parsed_requirements": {}}
        iso = sub._make_isolated_input({"task_id": "t1", "agent": "data_query"}, state, {})
        assert iso["request_deadline"] == 1234.5


# ──────────────────────────────────────────────
# N-5 · T-3 · T-1ⓑ — 멀티 DB 루프
# ──────────────────────────────────────────────

def _real_run(state: dict, cfg=None, *, client=None):
    registry = MagicMock()
    registry.is_registered.return_value = True
    if client is not None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        registry.get_client.return_value = ctx
    return mdb._MultiRun(
        state=state, llm=MagicMock(), app_config=cfg or _cfg(), registry=registry,
        parsed_requirements={}, effective_limit=100, unmapped_fields=[], prior_block=None,
        prior_scope=None, value_index=None, db_results={}, db_schemas={}, db_errors={},
        all_attempts=[], mc_candidates=[], mc_derivations=[], sql_by_schema={},
        validation_failed={}, form_context="", form_intent=False, mapping_sources={},
        form_fill_answers=None, form_fill_out={},
    )


def _patch_multi_gen(monkeypatch, outputs, *, clock=None, gen_cost=0.0):
    calls: list[str | None] = []

    async def _gen(llm, parsed, schema_info, sub_context, limit, **kwargs):
        calls.append(kwargs.get("error_context"))
        if clock is not None:
            clock.t += gen_cost
        return outputs[min(len(calls), len(outputs)) - 1]

    monkeypatch.setattr(mdb, "_generate_sql", _gen)
    if clock is not None:
        monkeypatch.setattr(mdb, "_monotonic", clock)
    return calls


async def _gen_validated(run, db_id="gp"):
    return await mdb._generate_validated_sql(
        run, AsyncMock(), {"tables": {}}, "ctx", {}, db_engine="postgresql", db_id=db_id,
    )


class TestMultiNonSqlBudget:
    @pytest.mark.asyncio
    async def test_prose_chain_stops_after_two_generations(self, monkeypatch):
        """간이 검증("SELECT 문이 아닙니다") 산문 — 3회 → 2회."""
        calls = _patch_multi_gen(monkeypatch, [PROSE])
        run = _real_run({"user_query": "q"})
        _sql, err = await _gen_validated(run)
        assert len(calls) == 2 and err
        assert run.regen_stops["gp"]["reason"] == qv.REGEN_STOP_NON_SQL

    @pytest.mark.asyncio
    async def test_full_validation_wording_is_also_prose(self, monkeypatch):
        """전체 검증 문구("SELECT 문만 허용됩니다 …")도 같은 판정 — 사유가 `; `로 이어진 형태."""
        calls = _patch_multi_gen(monkeypatch, [PROSE])
        monkeypatch.setattr(mdb, "_validate_sql", lambda *a, **k: (
            "SELECT 문만 허용됩니다. 감지된 타입: UNKNOWN; SQL 구조에 자연어(한글) 토큰", None,
        ))
        run = _real_run({"user_query": "q"})
        await _gen_validated(run)
        assert len(calls) == 2 and run.regen_stops["gp"]["reason"] == "non_sql"

    @pytest.mark.asyncio
    async def test_ordinary_failure_keeps_three_attempts(self, monkeypatch):
        calls = _patch_multi_gen(monkeypatch, [TABLELESS_SQL])
        run = _real_run({"user_query": "q"})
        await _gen_validated(run)
        assert len(calls) == 3
        assert run.regen_stops["gp"]["reason"] == qv.REGEN_STOP_VALIDATION_BUDGET

    @pytest.mark.asyncio
    async def test_prose_then_sql_recovers_without_stop(self, monkeypatch):
        calls = _patch_multi_gen(monkeypatch, [PROSE, VALID_SQL])
        run = _real_run({"user_query": "q"})
        sql, err = await _gen_validated(run)
        assert len(calls) == 2 and err is None and sql.startswith(VALID_SQL)
        assert run.regen_stops == {}


class TestMultiDeadline:
    @pytest.mark.asyncio
    async def test_regeneration_skipped_when_last_generation_does_not_fit(self, monkeypatch):
        clock = _Clock()
        calls = _patch_multi_gen(monkeypatch, [TABLELESS_SQL], clock=clock, gen_cost=20.0)
        run = _real_run({"user_query": "q", "request_deadline": _deadline(clock, 30.0)})
        _sql, err = await _gen_validated(run)
        assert len(calls) == 1
        assert run.regen_stops["gp"]["reason"] == qv.REGEN_STOP_DEADLINE
        # detail은 마지막 검증 사유 그대로(사용자 문구는 반환 오류 쪽)
        assert run.regen_stops["gp"]["detail"].startswith("테이블을 하나도 조회하지 않는")
        assert "10초 < 직전 SQL 생성 20초" in err and "마지막 실패 사유" in err

    @pytest.mark.asyncio
    async def test_no_deadline_keeps_legacy_attempts(self, monkeypatch):
        clock = _Clock()
        calls = _patch_multi_gen(monkeypatch, [TABLELESS_SQL], clock=clock, gen_cost=10_000.0)
        run = _real_run({"user_query": "q"})
        await _gen_validated(run)
        assert len(calls) == 3

    @staticmethod
    def _patch_target(monkeypatch, *, analyze=None):
        monkeypatch.setattr(mdb, "_analyze_schema", analyze or AsyncMock(
            return_value={"tables": {"t": {}}, "_structure_meta": {"k": 1}}))
        monkeypatch.setattr(mdb, "get_domain_by_id", MagicMock(
            return_value=MagicMock(db_engine="postgresql", db_schema="s")))
        monkeypatch.setattr(mdb, "log_query_execution", AsyncMock())

    @pytest.mark.asyncio
    async def test_schema_analysis_not_started_after_deadline(self, monkeypatch):
        """T-1ⓑ: 조회 마감 경과 — 클라이언트도 열지 않고 사유를 DB 오류로 남긴다."""
        clock = _Clock()
        monkeypatch.setattr(mdb, "_monotonic", clock)
        analyze = AsyncMock()
        self._patch_target(monkeypatch, analyze=analyze)
        run = _real_run({"user_query": "q", "request_deadline": _deadline(clock, -1.0)})
        await mdb._run_single_target({"db_id": "gp"}, run)
        analyze.assert_not_called()
        run.registry.get_client.assert_not_called()
        assert "조회 마감 경과" in run.db_errors["gp"]
        assert run.regen_stops["gp"]["reason"] == "deadline"

    @pytest.mark.asyncio
    async def test_first_generation_not_started_when_schema_used_up_time(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(mdb, "_monotonic", clock)

        async def _slow_analyze(*a, **k):
            clock.t += 50.0
            return {"tables": {"t": {}}, "_structure_meta": {"k": 1}}

        self._patch_target(monkeypatch, analyze=_slow_analyze)
        gen = AsyncMock()
        monkeypatch.setattr(mdb, "_generate_sql", gen)
        run = _real_run({"user_query": "q", "request_deadline": _deadline(clock, 30.0)},
                        client=MagicMock())
        await mdb._run_single_target({"db_id": "gp"}, run)
        gen.assert_not_called()
        assert "SQL 생성" in run.db_errors["gp"]
        assert run.regen_stops["gp"]["reason"] == "deadline"

    @pytest.mark.asyncio
    async def test_exec_error_regeneration_uses_same_gate(self, monkeypatch):
        """실행 오류 재생성(D-176)도 같은 게이트 — 원 실행 오류를 감사·사유에 남긴다."""
        clock = _Clock()
        self._patch_target(monkeypatch)
        calls = _patch_multi_gen(monkeypatch, [VALID_SQL], clock=clock, gen_cost=20.0)
        client = MagicMock()
        client.execute_sql = AsyncMock(side_effect=Exception('ERROR: syntax error at or near "x"'))
        record_failure = AsyncMock()
        monkeypatch.setattr(mdb, "_record_failure", record_failure)
        run = _real_run({"user_query": "q", "request_deadline": _deadline(clock, 30.0)},
                        client=client)
        await mdb._run_single_target({"db_id": "gp"}, run)
        assert len(calls) == 1 and client.execute_sql.await_count == 1
        record_failure.assert_awaited_once()
        assert run.regen_stops["gp"]["reason"] == "deadline"
        assert "syntax error" in run.regen_stops["gp"]["detail"]
        assert "재생성" in run.db_errors["gp"]


class TestMultiTaskFolding:
    """멀티 DB 결과를 2단 task 결과로 접을 때의 `regen_stop` 계약."""

    @pytest.mark.asyncio
    async def test_executor_reports_stops_and_task_carries_regen_stop(self, monkeypatch):
        """두 DB 모두 산문 → 실행기는 `regen_stops`, task 결과는 `regen_stop`(non_sql)."""
        TestMultiDeadline._patch_target(monkeypatch)
        calls = _patch_multi_gen(monkeypatch, [PROSE])
        state = {"user_query": "q", "target_databases": [{"db_id": "gp"}, {"db_id": "yd"}]}
        run = _real_run(state, client=MagicMock())
        monkeypatch.setattr(mdb, "_prepare_multi_run", AsyncMock(return_value=run))
        out = await mdb.multi_db_executor(state, llm=MagicMock(), app_config=_cfg())
        assert len(calls) == 4  # DB당 2회(종전 3회)
        assert set(out["regen_stops"]) == {"gp", "yd"}
        assert out["error_message"] == "모든 DB 쿼리가 실패했습니다."
        packed = sub._pack_pipeline_result(
            {**state, **out}, state["target_databases"], out["error_message"],
            ownership_notes=[], db_origin="planned", db_succeeded=False, db_pinned=False,
        )
        assert packed["regen_stop"]["reason"] == "non_sql"
        assert "gp:" in packed["regen_stop"]["detail"] and "yd:" in packed["regen_stop"]["detail"]

    def test_mixed_failure_without_loop_stop_carries_no_stop(self):
        """SQL 루프 밖 실패(연결 오류 등)가 섞이면 재위임 무익을 단정할 수 없다 — 싣지 않는다."""
        s = {
            "db_errors": {"gp": "SQL 검증 실패", "yd": "DB 'yd' 실행 에러: connection refused"},
            "regen_stops": {"gp": {"reason": "non_sql", "detail": "x"}},
        }
        assert sub._task_regen_stop(s) is None

    def test_reason_merge_prefers_deadline(self):
        s = {
            "db_errors": {"gp": "a", "yd": "b"},
            "regen_stops": {"gp": {"reason": "non_sql", "detail": "a"},
                            "yd": {"reason": "deadline", "detail": "b"}},
        }
        assert sub._task_regen_stop(s)["reason"] == "deadline"
        s["regen_stops"]["yd"]["reason"] = "validation_budget"
        assert sub._task_regen_stop(s)["reason"] == "validation_budget"

    def test_success_task_has_no_regen_stop_even_with_partial_stops(self):
        """일부 DB만 멈추고 task가 성공이면 키를 두지 않는다."""
        packed = sub._pack_pipeline_result(
            {"db_errors": {"gp": "x"}, "regen_stops": {"gp": {"reason": "non_sql", "detail": "x"}},
             "organized_data": {"rows": [{"a": 1}]}},
            [{"db_id": "gp"}, {"db_id": "yd"}], None,
            ownership_notes=[], db_origin="planned", db_succeeded=False, db_pinned=False,
        )
        assert "regen_stop" not in packed and "error" not in packed

    @pytest.mark.asyncio
    async def test_executor_shape_unchanged_without_stops(self, monkeypatch):
        """멈춘 DB가 없으면 `regen_stops` 키를 싣지 않는다(반환 shape 현행 유지)."""
        TestMultiDeadline._patch_target(monkeypatch)
        _patch_multi_gen(monkeypatch, [VALID_SQL])
        client = MagicMock()
        client.execute_sql = AsyncMock(return_value=SimpleNamespace(rows=[{"h": 1}], row_count=1))
        state = {"user_query": "q", "target_databases": [{"db_id": "gp"}]}
        run = _real_run(state, client=client)
        monkeypatch.setattr(mdb, "_prepare_multi_run", AsyncMock(return_value=run))
        out = await mdb.multi_db_executor(state, llm=MagicMock(), app_config=_cfg())
        assert "regen_stops" not in out and out["error_message"] is None
