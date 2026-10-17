"""plans/186 Wave 1-A — 멀티 DB 실행기 행동 변경 2건(M7 · J7).

M7: 멀티 SQL 루프의 재생성 중단 detail(`regen_stops[db].detail`)을 단일 경로
    `error_message`와 같은 「SQL 검증 실패: {원문}」 꼴로 남긴다. 고지(`_failure_disclosure`)는
    `safe_error_text`로 머리말만 싣는데, 머리말 없는 원문은 통째로 버려져 「(마지막 사유: …)」가
    빠졌다. 원문은 화면에 싣지 않고(163 S3+), kind 판정은 그대로다.
J7: per-DB 스키마에 사용률 통계 테이블이 없어 스키마 필터에서 빠진 통계 칼럼 매핑 사용률 필드를
    폼필 피벗이 필드명으로 회수한다 — 단일 경로(185 W1 뒤)와 같은 회수 대상. 정규·EAV 매핑과
    비사용률 필드는 종전처럼 버린다.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain import disclosure as disc
from src.utils.user_error import safe_error_text, safe_stop_detail_text, with_validation_head
from tests.test_nodes.test_form_month_series import _EAV_META
from tests.test_nodes.test_plan166_w0_form_fill_pivot_inputs import (  # noqa: F401 — 픽스처
    _TABLES,
    _run_multi,
    _run_single,
    recorder,
)
from tests.test_nodes.test_plan185_single_metric_recovery import _CPU, _MEM, _STAT_TABLES, _case
from tests.test_orchestration.test_plan119_sql_loop import (
    PROSE,
    TABLELESS_SQL,
    VALID_SQL,
    _cfg,
    _Clock,
    _deadline,
    _patch_multi_gen,
    _patch_single_nodes,
    _real_run,
    _single_isolated,
)

# 패키지 `__init__`이 같은 이름의 노드 함수를 재노출한다 — 서브모듈로 받는다
mdb = importlib.import_module("src.nodes.multi_db_executor")
agg_mod = importlib.import_module("src.orchestration.result_aggregator")
sub = importlib.import_module("src.orchestration.subagents")
rp = importlib.import_module("src.orchestration.replanner")
# 테스트 클래스를 이름으로 들이면 이 모듈에서도 수집된다 — 모듈로 받아 헬퍼만 쓴다
_l119 = importlib.import_module("tests.test_orchestration.test_plan119_sql_loop")

_RAW_PIECE = "테이블을 하나도"  # TABLELESS_SQL의 검증 사유 원문 조각
_ALL_FAILED = "모든 DB 쿼리가 실패했습니다."
_TASK = {"task_id": "t1", "sub_query": "서버 목록"}


async def _gen_validated(run: Any) -> tuple[str, str | None]:
    return await mdb._generate_validated_sql(
        run, AsyncMock(), {"tables": {}}, "ctx", {}, db_engine="postgresql", db_id="gp",
    )


def _disclosure(stop: dict[str, str]) -> Any:
    return agg_mod._failure_disclosure(_TASK, {"error": _ALL_FAILED, "regen_stop": stop})


# ── M7 ──────────────────────────────────────────────────────────────────────


class TestValidationStopDetail:
    def test_raw_reason_gets_head(self):
        assert with_validation_head("x 없음") == "SQL 검증 실패: x 없음"

    def test_head_is_not_doubled(self):
        assert with_validation_head("SQL 검증 실패: x") == "SQL 검증 실패: x"
        assert with_validation_head("SQL 검증 실패") == "SQL 검증 실패"


class TestMultiRegenStopDisclosure:
    @pytest.mark.asyncio
    async def test_budget_stop_discloses_head_only(self, monkeypatch):
        """검증 예산 소진 — 고지에 「(마지막 사유: SQL 검증 실패)」만, 원문 조각은 없다."""
        _patch_multi_gen(monkeypatch, [TABLELESS_SQL])
        run = _real_run({"user_query": "q"})
        await _gen_validated(run)
        stop = run.regen_stops["gp"]
        assert stop["reason"] == disc.FAIL_VALIDATION_BUDGET
        assert stop["detail"].startswith(f"SQL 검증 실패: {_RAW_PIECE}")
        d = _disclosure(stop)
        assert d["kind"] == disc.FAIL_VALIDATION_BUDGET
        assert "(마지막 사유: SQL 검증 실패)" in d["text"]
        assert _RAW_PIECE not in d["text"]

    @pytest.mark.asyncio
    async def test_deadline_stop_discloses_head_only(self, monkeypatch):
        """시간 게이트 차단 — detail은 사용자 문구가 아니라 머리말 붙은 검증 사유다."""
        clock = _Clock()
        _patch_multi_gen(monkeypatch, [TABLELESS_SQL], clock=clock, gen_cost=20.0)
        run = _real_run({"user_query": "q", "request_deadline": _deadline(clock, 30.0)})
        await _gen_validated(run)
        stop = run.regen_stops["gp"]
        assert stop["reason"] == disc.FAIL_DEADLINE
        assert stop["detail"].startswith(f"SQL 검증 실패: {_RAW_PIECE}")
        d = _disclosure(stop)
        assert d["kind"] == disc.FAIL_DEADLINE
        assert "(마지막 사유: SQL 검증 실패)" in d["text"]
        assert _RAW_PIECE not in d["text"]

    @pytest.mark.asyncio
    async def test_already_headed_reason_not_doubled(self, monkeypatch):
        _patch_multi_gen(monkeypatch, [TABLELESS_SQL])
        monkeypatch.setattr(mdb, "_validate_sql", lambda *a, **k: ("SQL 검증 실패: y 없음", None))
        run = _real_run({"user_query": "q"})
        await _gen_validated(run)
        assert run.regen_stops["gp"]["detail"] == "SQL 검증 실패: y 없음"

    @pytest.mark.asyncio
    async def test_sql_blocked_kind_unchanged(self, monkeypatch):
        """읽기 전용 가드 차단 사유는 머리말 뒤에도 남아 kind가 `sql_blocked` 그대로다."""
        _patch_multi_gen(monkeypatch, ["DELETE FROM t"])
        monkeypatch.setattr(
            mdb, "_validate_sql",
            lambda *a, **k: ("금지된 키워드가 포함되어 있습니다: DELETE", None),
        )
        run = _real_run({"user_query": "q"})
        await _gen_validated(run)
        assert _disclosure(run.regen_stops["gp"])["kind"] == disc.SQL_BLOCKED

    @pytest.mark.asyncio
    async def test_token_limit_kind_unchanged(self, monkeypatch):
        """토큰 한도 중단(`backend_limit`)은 regen kind 밖이라 종전처럼 `query_failed`다."""
        _patch_multi_gen(monkeypatch, [TABLELESS_SQL])
        monkeypatch.setattr(
            mdb, "_validate_sql",
            lambda *a, **k: (f"{mdb._TOKEN_LIMIT_ERROR_PREFIX}: 12000 > 8000", None),
        )
        run = _real_run({"user_query": "q"})
        await _gen_validated(run)
        stop = run.regen_stops["gp"]
        assert stop["reason"] == "backend_limit"
        assert stop["detail"].startswith("SQL 검증 실패: ")
        assert _disclosure(stop)["kind"] == disc.QUERY_FAILED


# ── J7 ──────────────────────────────────────────────────────────────────────


def _no_stat_case() -> dict[str, Any]:
    """185 케이스(통계 칼럼 매핑 사용률 + 미매핑 사용률 + 비사용률 통계 매핑)를 통계 테이블 없는
    스키마로 — 정규 매핑 하나는 스키마에 없는 테이블을 가리킨다."""
    case = _case("postgresql", "")
    case["mapping"] = {**case["mapping"], "부서": "cmm_dept.name"}
    case["schema_info"] = {"tables": _TABLES, **_EAV_META}
    return case


class TestSchemaDroppedMetricRecovery:
    def test_split_returns_schema_dropped_metric_fields(self):
        case = _no_stat_case()
        mapping = {f: c for f, c in case["mapping"].items() if c is not None}
        regular, _eav, metric, dropped = mdb._split_mapping_for_prompt(
            mapping, case["schema_info"],
        )
        # 판정은 성능지표 묶음 기준 — 비사용률 「비고」도 여기 들고 회수 술어가 거른다
        assert dropped == [_CPU, "비고"]
        assert metric == []
        assert "부서" not in {f for f, _ in regular}

    def test_split_with_stat_tables_has_no_dropped(self):
        case = _case("postgresql", "")
        mapping = {f: c for f, c in case["mapping"].items() if c is not None}
        _r, _e, metric, dropped = mdb._split_mapping_for_prompt(mapping, case["schema_info"])
        assert dropped == []
        assert [f for f, _ in metric] == [_CPU, "비고"]

    @pytest.mark.asyncio
    async def test_multi_recovers_schema_dropped_metric_field(self, recorder):  # noqa: F811
        """통계 테이블 없는 스키마에서도 통계 칼럼 매핑 사용률 필드를 회수 — 단일과 같은 대상."""
        case = _no_stat_case()
        multi = await _run_multi(case, recorder)
        fields = multi["inputs"]["metric_fields"]
        assert _CPU in fields and _MEM in fields
        assert "비고" not in fields
        assert f'AS "{_CPU}"' in multi["sql"]
        # 정규 매핑이 스키마 밖 테이블을 가리키면 종전처럼 버린다
        assert "cmm_dept" not in multi["sql"]
        single = await _run_single(_case("postgresql", ""), recorder)
        assert set(fields) == set(single["inputs"]["metric_fields"])

    @pytest.mark.asyncio
    async def test_stat_tables_present_result_unchanged(self, recorder):  # noqa: F811
        """통계 테이블이 있으면 회수 후보가 그대로 — 없는 스키마와 같은 SQL을 낸다."""
        with_stat = await _run_multi(_case("postgresql", ""), recorder)
        case = _case("postgresql", "")
        case["schema_info"] = {"tables": _TABLES, **_EAV_META}
        without_stat = await _run_multi(case, recorder)
        assert with_stat["inputs"]["metric_fields"] == [_MEM, _CPU]
        assert without_stat["inputs"]["metric_fields"] == with_stat["inputs"]["metric_fields"]
        assert without_stat["sql"] == with_stat["sql"]


def test_stat_tables_fixture_has_stat_table():
    """전제 확인 — 185 픽스처의 통계 스키마에는 통계 테이블이 있고 `_TABLES`에는 없다."""
    assert any(t.startswith("cmm_metric_stat") for t in _STAT_TABLES)
    assert not any(t.startswith("cmm_metric_stat") for t in _TABLES)


# ── M7 교정 — 2단 집계 detail(「db_id: 사유」 · 「 / 」 이음) ─────────────────────────


_RAW_SQL = "SELECT secret_col FROM t"
_JOINED = f"polestar: SQL 검증 실패: {_RAW_SQL} 없음 / itam: SQL 검증 실패: 테이블 x 없음"


class TestSafeStopDetailText:
    def test_joined_validation_head_once(self):
        assert safe_stop_detail_text(_JOINED) == "SQL 검증 실패"

    def test_distinct_heads_in_first_seen_order(self):
        got = safe_stop_detail_text("gp: SQL 검증 실패: a / yd: SQL 실행 에러: boom")
        assert got == "SQL 검증 실패 · SQL 실행 에러"

    def test_unheaded_pieces_are_dropped(self):
        assert safe_stop_detail_text("gp: 테이블 x 없음 / yd: boom at 0x1") is None
        assert safe_stop_detail_text("gp: 테이블 x 없음 / yd: SQL 검증 실패: y") == "SQL 검증 실패"

    @pytest.mark.parametrize("raw", [
        None, "", "SQL 검증 실패: x", "SQL 실행 에러: e", "모든 DB 쿼리가 실패했습니다.",
        "테이블 x 없음", "결과 생성 실패", "SQL 검증 실패",
        "모든 DB 쿼리가 실패했습니다:\n[김포] SQL 검증 실패: x",
    ])
    def test_single_piece_matches_safe_error_text(self, raw):
        assert safe_stop_detail_text(raw) == safe_error_text(raw)


def _multi_task_stop() -> dict[str, str]:
    """두 DB 모두 검증 예산 소진 — 실 집계 함수(`_task_regen_stop`)로 접는다."""
    detail = {
        "polestar": f"SQL 검증 실패: {_RAW_SQL} 없음",
        "itam": "SQL 검증 실패: 테이블 x 없음",
    }
    stop = sub._task_regen_stop({
        "regen_stops": {d: {"reason": "validation_budget", "detail": v} for d, v in detail.items()},
        "db_errors": dict(detail),
    })
    assert stop is not None and stop["detail"].startswith("polestar: SQL 검증 실패")
    return stop


class TestJoinedDetailConsumers:
    def test_failure_disclosure_shows_head_once(self):
        d = _disclosure(_multi_task_stop())
        assert d["kind"] == disc.FAIL_VALIDATION_BUDGET
        assert "(마지막 사유: SQL 검증 실패)" in d["text"]
        assert d["text"].count("SQL 검증 실패") == 1
        assert _RAW_SQL not in d["text"] and "테이블 x" not in d["text"]

    def test_failure_disclosure_omits_reason_without_head(self):
        stop = {"reason": "validation_budget", "detail": "gp: 테이블 x 없음 / yd: boom"}
        assert "마지막 사유" not in _disclosure(stop)["text"]

    def test_replan_notice_shows_head_once(self):
        prior = {"task_id": "t1", "sub_query": "서버 목록"}
        notice = rp._regen_stop_notice([(prior, _multi_task_stop())])
        assert "(마지막 사유: SQL 검증 실패)" in notice
        assert notice.count("SQL 검증 실패") == 1
        assert _RAW_SQL not in notice

    def test_replan_notice_omits_reason_without_head(self):
        prior = {"task_id": "t1", "sub_query": "서버 목록"}
        stop = {"reason": "validation_budget", "detail": "gp: 테이블 x 없음"}
        assert "마지막 사유" not in rp._regen_stop_notice([(prior, stop)])


class TestSinglePathDetailHead:
    @pytest.mark.asyncio
    async def test_validation_budget_detail_headed(self, monkeypatch):
        _patch_single_nodes(monkeypatch, [TABLELESS_SQL])
        out = await sub._run_single_db_pipeline(_single_isolated(), MagicMock(), _cfg())
        assert out["regen_stop"]["reason"] == disc.FAIL_VALIDATION_BUDGET
        assert out["regen_stop"]["detail"].startswith("SQL 검증 실패: ")
        assert not out["regen_stop"]["detail"].startswith("SQL 검증 실패: SQL 검증 실패")
        # 실패 사유(error_message)는 검증 노드가 지은 그대로다
        assert out["error_message"].startswith("SQL 검증 실패: ")

    @pytest.mark.asyncio
    async def test_non_sql_detail_headed(self, monkeypatch):
        _patch_single_nodes(monkeypatch, [PROSE])
        out = await sub._run_single_db_pipeline(_single_isolated(), MagicMock(), _cfg())
        assert out["regen_stop"]["reason"] == disc.FAIL_NON_SQL
        assert out["regen_stop"]["detail"].startswith("SQL 검증 실패: ")


class TestMultiExecErrorDeadlineDetail:
    @pytest.mark.asyncio
    async def test_exec_error_detail_has_exec_head(self, monkeypatch):
        """실행 오류 뒤 시간 게이트 차단 — detail은 「SQL 실행 에러: …」, 고지는 머리말만."""
        clock = _Clock()
        _l119.TestMultiDeadline._patch_target(monkeypatch)
        _patch_multi_gen(monkeypatch, [VALID_SQL], clock=clock, gen_cost=20.0)
        client = MagicMock()
        client.execute_sql = AsyncMock(side_effect=Exception('ERROR: syntax error at "x"'))
        monkeypatch.setattr(mdb, "_record_failure", AsyncMock())
        run = _real_run({"user_query": "q", "request_deadline": _deadline(clock, 30.0)},
                        client=client)
        await mdb._run_single_target({"db_id": "gp"}, run)
        stop = run.regen_stops["gp"]
        assert stop["reason"] == disc.FAIL_DEADLINE
        assert stop["detail"].startswith("SQL 실행 에러: ")
        d = _disclosure(stop)
        assert "(마지막 사유: SQL 실행 에러)" in d["text"]
        assert "syntax error" not in d["text"]


# ── J4 보완 — classify_dbs가 넘긴 설정으로 소유 판정 ─────────────────────────────


@pytest.mark.asyncio
async def test_classify_dbs_passes_app_config(monkeypatch):
    seen: dict[str, Any] = {}

    async def _fake(llm, query, domains, **kwargs):
        seen.update(kwargs)
        return {"intent": "data_query", "databases": []}

    monkeypatch.setattr(sub, "_llm_classify", _fake)
    cfg = MagicMock()
    cfg.multi_db.get_active_db_ids.return_value = ["polestar"]
    await sub.classify_dbs(MagicMock(), "서버 목록", cfg)
    assert seen["app_config"] is cfg
