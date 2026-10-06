"""plans/122 T-4·T-5 — LLM SQL 생성 경로의 시간 해석 배선(단일·멀티 대칭) · 기간 블록.

고정하는 계약:
  1. 기간 블록(`time_period.build_period_block`)의 리터럴은 `stat_bounds`·`alarm_ts_bounds`와 같다 ·
     모든 변형에 「일반 규칙보다 우선」·「재계산 금지」가 있고 「_h/_d로 대체하지 마세요」는 없다 ·
     기간 미지정은 조건부 문구 · 「현재」+기간 미지정은 블록 없음 · 「~한 적이 있는」은 전 보관 기간
  2. 같은 state `time_resolution`을 단일 경로(`query_generator`)와 멀티 경로(`multi_db_executor`
     — state → `_prepare_multi_run` → `_generate_validated_sql`)에 넣으면 LLM 사용자 프롬프트의
     기간 블록이 바이트 동일하다(D-066)
  3. 해석이 있으면 「파싱된 요구사항」 JSON에 원시 `time_range`·`time_expr`가 없다(§10.2 ⑦)
  4. 플래그 off(`time_resolution` 없음)면 종전 블록·종전 JSON 그대로다
  5. 결정적 경로(시맨틱 컴파일·폼필 피벗·월 시리즈·알람 조립·검증기 훅·급증 비교)에 해석이
     실제로 넘어가고(인자 캡처), 폼필·알람은 최종 SQL에 그 리터럴 경계가 남는다
  6. (교정) 멀티 간이 검증에서도 폴스타 DB면 시간 조건 대조가 돌고 재생성 사유가 되며 재시도
     예산(총 3회) 안에서 끝난다 · full 모드는 어댑터 훅 1회만(중복 없음)
  7. (교정) 「현재·지금」+기간 미지정은 폼필 피벗·월 시리즈에 해석을 넘기지 않는다(`metric_period`)
  8. (교정) 단계적 도출 도구 컨텍스트(`ToolContext.time_resolution`)에 state 값이 실린다

기준 시각 2026-09-29(화) 10:00 KST. 가짜 LLM·목만 쓴다(실 LLM·DB 0 — D-127).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from src.db_adapters.polestar import assembler as polestar_assembler
from src.db_adapters.polestar.time_period import (
    PERIOD_BLOCK_HEADER,
    SPAN_MAX_CHARS,
    alarm_ts_bounds,
    build_period_block,
    sql_applies_period,
    stat_bounds,
)
from src.db_adapters.time_hint import (
    build_generic_time_hint,
    metric_period,
    stat_month_compat,
    strip_raw_time_keys,
)
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST
from src.nodes import multi_db_executor as mdb
from src.nodes.query_generator import _GenContext, _try_spike, query_generator
from src.utils.query_gen_common import build_stat_month_block, resolve_stat_month_range

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
_DB_ID = "polestar_cm_gp"

#: 대칭 테스트 질의(지시서 9종).
QUERIES = (
    "최근 30일 CPU 사용률",
    "어제 CPU 사용률",
    "지난달 CPU 사용률",
    "이번 달 CPU 사용률",
    "최근 3시간 CPU 사용률",
    "CPU 90% 넘은 적이 있는 서버",
    "CPU 상위 10대",
    "현재 CPU 사용률",
    "어제 알람 목록",
)

_SCHEMA_INFO: dict[str, Any] = {
    "tables": {
        "polestar.cmm_resource": {
            "columns": [
                {"name": "id", "type": "bigint", "primary_key": True, "nullable": False},
                {"name": "name", "type": "varchar(255)", "nullable": True},
                {"name": "hostname", "type": "varchar(255)", "nullable": True},
                {"name": "resource_type", "type": "varchar(255)", "nullable": True},
                {"name": "dtime", "type": "timestamp", "nullable": True},
            ],
        },
        "polestar.cmm_metric_stat_d": {
            "columns": [
                {"name": "resource_id", "type": "bigint", "nullable": False},
                {"name": "avg_val", "type": "numeric", "nullable": True},
                {"name": "stat_date", "type": "varchar(10)", "nullable": True},
            ],
        },
    },
}

_LLM_SQL = "SELECT r.name FROM polestar.cmm_resource r WHERE r.dtime IS NULL LIMIT 10"


def _qt(text: str, now: datetime = NOW) -> QueryTime:
    return resolve_query_time(text, now)


def _parsed(text: str) -> dict[str, Any]:
    """input_parser 산출물 모양 — 원시 기간 키(time_range·time_expr)를 일부러 싣는다."""
    return {
        "original_query": text,
        "query_targets": ["서버", "CPU"],
        "filter_conditions": [],
        "time_range": {"start": "2026-08-30", "end": "2026-09-28"},
        "time_expr": {"relation": "last", "n": 30, "unit": "day", "span": "최근 30일"},
        "output_format": "text",
        "aggregation": None,
        "limit": None,
    }


class _CapturingLLM:
    """모든 호출의 메시지를 기록하고 고정 SQL을 돌려주는 LLM 대역(실 호출 없음)."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        self.calls.append(list(messages))
        return SimpleNamespace(content=f"```sql\n{_LLM_SQL}\n```")

    @property
    def first_human(self) -> str:
        assert self.calls, "LLM이 불리지 않았다"
        human = [m for m in self.calls[0] if isinstance(m, HumanMessage)]
        assert len(human) == 1
        return str(human[0].content)


def _cfg(**text2sql: Any) -> MagicMock:
    """검증 대상 플래그만 명시한 설정 대역(.env 누수 차단 — MagicMock 속성은 기본 truthy)."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.get_polestar_db_ids.return_value = {_DB_ID}
    cfg.multi_db.get_active_db_ids.return_value = [_DB_ID]
    cfg.db_connection_string = ""
    cfg.synonym.value_retrieval = False
    cfg.synonym.fuzzy_match = False
    cfg.synonym.match_confidence_min = 0.85
    cfg.cross_system_key_bridge_enabled = False
    flags = {
        "semantic_compose": False,
        "multi_candidate": False,
        "complexity_gate": False,
        "generic_llm_mapping": False,
        "query_history_fewshot": False,
        "stepwise_derivation": False,
        "hypernym_ambiguity": False,
        "path_parity": False,
        "alarm_deterministic": False,
        "spike_condition_enabled": False,
        "multi_full_validation": False,
        "prompt_token_budget": 0,
    }
    flags.update(text2sql)
    for key, value in flags.items():
        setattr(cfg.text2sql, key, value)
    return cfg


def _state(text: str, *, time_resolution: Any = "auto", **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "user_query": text,
        "schema_info": _SCHEMA_INFO,
        "parsed_requirements": _parsed(text),
        "active_db_id": _DB_ID,
        "active_db_engine": "postgresql",
        "retry_count": 0,
        "time_resolution": _qt(text).to_state() if time_resolution == "auto" else time_resolution,
    }
    state.update(overrides)
    return state


def _blocks(prompt: str) -> list[str]:
    """프롬프트에서 시스템 기간 블록(머리 `## 기간 조건 (시스템…)`)만 뽑는다(빈 줄로 구획)."""
    return [p for p in prompt.split("\n\n") if p.startswith("## 기간 조건 (시스템")]


def _requirements_json(prompt: str) -> dict[str, Any]:
    head = "## 파싱된 요구사항\n```json\n"
    start = prompt.index(head) + len(head)
    return json.loads(prompt[start:prompt.index("\n```", start)])


async def _single_prompt(state: dict[str, Any], cfg: MagicMock | None = None) -> str:
    llm = _CapturingLLM()
    await query_generator(state, llm=llm, app_config=cfg or _cfg())  # type: ignore[arg-type]
    return llm.first_human


async def _multi_prompt(state: dict[str, Any], cfg: MagicMock | None = None) -> str:
    """멀티 경로 — state → `_prepare_multi_run`(시간 해석 운반) → `_generate_validated_sql`."""
    llm = _CapturingLLM()
    run = await mdb._prepare_multi_run(state, llm, cfg or _cfg())  # type: ignore[arg-type]
    sql, error = await mdb._generate_validated_sql(
        run, MagicMock(), _SCHEMA_INFO, state["user_query"], {},
        db_engine="postgresql", db_id=_DB_ID,
    )
    assert error is None and sql
    return llm.first_human


@pytest.fixture(autouse=True)
def _pin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """환경 의존 입력을 고정한다 — 스키마 접두사·캐시 재료·폴스타 지식 렌더 플래그."""
    from src.db_adapters.polestar import prompts as polestar_prompts

    monkeypatch.setattr(polestar_prompts, "_rendered_cache", {})
    monkeypatch.setattr(polestar_prompts, "knowledge_render_enabled", lambda: False)
    monkeypatch.setattr("src.routing.db_schema.get_schema_prefix", lambda db_id: "polestar.")

    class _EmptyCache:
        redis_available = False

        async def get_descriptions(self, db_id: str) -> dict[str, str]:
            return {}

        async def get_synonyms(self, db_id: str) -> dict[str, list[str]]:
            return {}

    monkeypatch.setattr(
        "src.schema_cache.cache_manager.get_cache_manager", lambda cfg=None: _EmptyCache()
    )


# ──────────────────────────────────────────────
# 1. 기간 블록 — 리터럴은 time_period 투영과 같다
# ──────────────────────────────────────────────

_PRIORITY = "'하드코딩 날짜 금지·CURRENT_DATE 동적 계산' 일반 규칙보다 **우선**"
_NO_RECALC = "CURRENT_DATE·CURRENT DATE·NOW()·CURRENT_TIMESTAMP·INTERVAL로 기간을 다시 계산하지"


@pytest.mark.parametrize("text", QUERIES)
def test_period_block_literals_match_time_period(text: str) -> None:
    qt = _qt(text)
    block = build_period_block(qt)
    assert qt.metric is not None and qt.event is not None
    if qt.present and qt.metric.source == "default":
        assert block == ""  # 「현재」+기간 미지정 — 종전 프로필 규칙·실시간 경로(D-291)
        return
    assert block.startswith(PERIOD_BLOCK_HEADER)
    assert _PRIORITY in block and _NO_RECALC in block
    assert "대체하지 마세요" not in block  # 입도는 해석기가 정했다(§10.2 ③)
    sb = stat_bounds(qt.metric)
    if sb is not None:
        assert f"`{sb.table}`" in block and f"`{sb.where()}`" in block
    else:
        assert "전 보관 기간" in block and "`cmm_metric_stat_m`" in block
    ab = alarm_ts_bounds(qt.event)
    if ab is not None:
        start, end = ab
        assert f"a.ctime >= TIMESTAMP '{start}' AND a.ctime < TIMESTAMP '{end}'" in block
    else:
        assert "기간 조건을 넣지 마세요(최신순" in block


def test_default_period_block_is_conditional() -> None:
    block = build_period_block(_qt("CPU 상위 10대"))
    assert "질의에 기간이 없습니다" in block and "지난달 2026-08-01 ~ 2026-08-31" in block
    assert "성능 통계를 조인할 때만 지난달로 한정" in block
    assert "`s.stat_date >= '202608' AND s.stat_date < '202609'`" in block
    assert "통계·알람이 필요 없는 질의(구성·목록 등)에는 기간 조건을 넣지 마세요" in block
    assert "a.ctime" not in block  # 알람은 기간 없음 = 조건 없음(D-291)


def test_this_month_block_keeps_daily_aggregation_guidance() -> None:
    block = build_period_block(_qt("이번 달 CPU 사용률"))
    assert "`s.stat_date >= '20260901' AND s.stat_date < '20260929'`" in block
    assert "AVG(s.avg_val)" in block and "MAX(s.max_val)" in block
    # 알람(사건)은 기준 시각까지(D-291)
    assert "TIMESTAMP '2026-09-29 10:00:00'" in block


def test_month_block_allows_equality_and_between() -> None:
    assert "`s.stat_date = '202608'`" in build_period_block(_qt("지난달 CPU"))
    between = "`s.stat_date BETWEEN '202606' AND '202608'`"
    assert between in build_period_block(_qt("지난 3개월 CPU"))


def test_empty_range_block_says_zero_rows() -> None:
    block = build_period_block(_qt("이번 달 CPU 사용률", datetime(2026, 10, 1, 9, tzinfo=KST)))
    assert "0행이 정답" in block
    assert "`s.stat_date >= '20261001' AND s.stat_date < '20261001'`" in block


def test_block_empty_without_resolution() -> None:
    assert build_period_block(None) == ""
    clarify = QueryTime(NOW, None, None, clarify="invalid_date")
    assert build_period_block(clarify) == ""


def test_generic_hint_is_db_agnostic() -> None:
    hint = build_generic_time_hint(_qt("최근 30일 CPU").metric)
    assert "['2026-08-30 00:00:00', '2026-08-30 00:00:00')" not in hint
    assert "['2026-08-30 00:00:00', '2026-09-29 00:00:00')" in hint
    assert "실제 존재하는" in hint and "다시 계산하지 마세요" in hint
    assert "cmm_" not in hint and "stat_date" not in hint
    # 기본값·기간 조건 없음은 무선언 DB에 강제하지 않는다
    assert build_generic_time_hint(_qt("CPU 상위 10대").metric) == ""
    assert build_generic_time_hint(_qt("CPU 90% 넘은 적이 있는 서버").metric) == ""
    assert build_generic_time_hint(None) == ""


def test_helpers_keep_flag_off_identity() -> None:
    parsed = _parsed("q")
    assert strip_raw_time_keys(parsed, None) is parsed  # off — 같은 객체(덤프 바이트 불변)
    stripped = strip_raw_time_keys(parsed, _qt("q"))
    assert "time_range" not in stripped and "time_expr" not in stripped
    assert stripped["query_targets"] == parsed["query_targets"]
    assert stat_month_compat(None) is None
    assert stat_month_compat(_qt("지난 3개월 CPU")) == ("202606", "202608")
    assert stat_month_compat(_qt("최근 30일 CPU")) is None      # 일 입도 — 월 경계 아님
    assert stat_month_compat(_qt("CPU 상위 10대")) == ("202608", "202608")  # 기본값
    assert stat_month_compat(_qt("현재 CPU")) is None           # 「현재」는 기본값 강제 안 함


# ──────────────────────────────────────────────
# 2·3. 단일·멀티 대칭 — 같은 해석 → 같은 블록 · 원시 기간 키 제거
# ──────────────────────────────────────────────


@pytest.mark.parametrize("text", QUERIES)
async def test_single_and_multi_prompts_carry_identical_period_block(text: str) -> None:
    qt = _qt(text)
    expected = build_period_block(qt)
    single = await _single_prompt(_state(text))
    multi = await _multi_prompt(_state(text))

    assert _blocks(single) == _blocks(multi) == ([expected] if expected else [])
    for prompt in (single, multi):
        req = _requirements_json(prompt)
        assert "time_range" not in req and "time_expr" not in req
        assert req["original_query"] == text
        # 원시 ISO 기간이 다른 자리로 새지 않는다(블록의 리터럴만 남는다)
        assert '"2026-08-30"' not in prompt


@pytest.mark.parametrize("text", QUERIES)
def test_period_block_values_are_applied_by_projection(text: str) -> None:
    """블록 리터럴로 만든 SQL은 `sql_applies_period`(고지·검증 판정)가 참으로 본다.

    생산자(블록)와 판정자(고지·검증)가 같은 투영을 쓴다는 확인이다.
    """
    qt = _qt(text)
    assert qt.metric is not None
    sb = stat_bounds(qt.metric)
    if sb is None or (qt.present and qt.metric.source == "default"):
        return
    sql = f"SELECT 1 FROM polestar.{sb.table} s WHERE {sb.where()}"
    assert sql_applies_period(sql, qt.metric)


# ──────────────────────────────────────────────
# 4. 플래그 off — 종전 블록·종전 JSON
# ──────────────────────────────────────────────


@pytest.mark.parametrize("text", ("지난달 CPU 사용률", "지난 3개월 CPU 사용률", "CPU 상위 10대"))
async def test_flag_off_keeps_legacy_block_and_json(text: str) -> None:
    # 종전 해석 = 표면어 → LLM time_range 2단 폴백(R3-(i)) — 픽스처의 time_range도 함께 탄다
    legacy = build_stat_month_block(
        resolve_stat_month_range(text, parsed_time_range=_parsed(text)["time_range"])
    )
    single = await _single_prompt(_state(text, time_resolution=None))
    multi = await _multi_prompt(_state(text, time_resolution=None))
    for prompt in (single, multi):
        assert _blocks(prompt) == ([legacy] if legacy else [])
        req = _requirements_json(prompt)
        assert req["time_range"] == _parsed(text)["time_range"]
        assert "time_expr" in req


async def test_malformed_resolution_falls_back_to_legacy_path() -> None:
    text = "지난달 CPU 사용률"
    single = await _single_prompt(_state(text, time_resolution={"version": 999}))
    assert _blocks(single) == [build_stat_month_block(resolve_stat_month_range(text))]
    assert "time_range" in _requirements_json(single)


async def test_generic_db_gets_generic_hint_on_both_paths() -> None:
    """무선언 DB(GENERIC_LLM_MAPPING 옵트인) — 두 경로 모두 범용 힌트(폴스타 리터럴 없음)."""
    text = "최근 30일 CPU 사용률"
    cfg = _cfg(generic_llm_mapping=True)
    cfg.get_polestar_db_ids.return_value = set()
    hint = build_generic_time_hint(_qt(text).metric)
    single = await _single_prompt(_state(text), cfg)
    multi = await _multi_prompt(_state(text), cfg)
    for prompt in (single, multi):
        assert hint in prompt and _blocks(prompt) == []


# ──────────────────────────────────────────────
# 5. 결정적 경로 배선 — 인자 캡처 + 최종 SQL 리터럴
# ──────────────────────────────────────────────


async def test_single_semantic_compile_receives_query_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    qg_module = sys.modules["src.nodes.query_generator"]
    captured: list[dict[str, Any]] = []

    async def _fake(llm: Any, user_query: str, db_id: str, **kwargs: Any) -> tuple[Any, ...]:
        captured.append(kwargs)
        return None, None, None

    monkeypatch.setattr(qg_module, "compile_from_nl", _fake)
    text = "지난 3개월 CPU 사용률"
    await _single_prompt(_state(text), _cfg(semantic_compose=True))
    (kwargs,) = captured
    assert kwargs["query_time"] == _qt(text)
    assert kwargs["stat_month"] == ("202606", "202608")


async def test_multi_semantic_compile_receives_query_time(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def _fake(llm: Any, user_query: str, db_id: str, **kwargs: Any) -> tuple[Any, ...]:
        captured.append(kwargs)
        return None, None, None

    monkeypatch.setattr(mdb, "compile_from_nl", _fake)
    text = "최근 30일 CPU 사용률"
    await _multi_prompt(_state(text), _cfg(semantic_compose=True))
    assert captured and captured[0]["query_time"] == _qt(text)
    assert captured[0]["stat_month"] is None  # 일 입도 — 호환 값 없음(해석은 query_time으로)


_FORM_META: dict[str, Any] = {
    "_structure_meta": {
        "patterns": [{
            "type": "eav",
            "entity_table": "cmm_resource",
            "config_table": "core_config_prop",
            "attribute_column": "name",
            "value_column": "stringvalue_short",
            "direct_join": {
                "entity_column": "resource_conf_id", "config_column": "configuration_id",
            },
            "known_attributes": [
                {"name": "Model", "description": "모델명 [resource_type: server.Server]"},
            ],
        }],
    },
    "tables": {"polestar.cmm_resource": {"columns": [
        {"name": "hostname", "type": "varchar(255)"},
        {"name": "dtime", "type": "timestamp"},
    ]}},
}
_FORM_MAPPING = {"호스트명": "cmm_resource.hostname", "CPU 평균 사용률": None}
_FORM_TEMPLATE = {"sheets": [{"title_text": "서버 현황", "name": "Sheet1"}]}


@pytest.mark.parametrize(
    "text", ("최근 30일 CPU 사용률 양식 채워줘", "지난달 CPU 사용률 양식 채워줘"),
)
async def test_single_form_fill_sql_applies_resolution(text: str) -> None:
    from src.nodes.query_generator import _try_build_form_fill_pivot_sql

    qt = _qt(text)
    state = _state(text, column_mapping=dict(_FORM_MAPPING), schema_info=_FORM_META,
                   template_structure=_FORM_TEMPLATE)
    result = _try_build_form_fill_pivot_sql(
        state, 1000, text, adapter_db_ids={_DB_ID}, query_time=qt,
    )
    assert result and result["sql"]
    assert qt.metric is not None and sql_applies_period(result["sql"], qt.metric)


async def test_form_fill_and_month_series_receive_metric_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """단일(결정적 조립·LLM 폴백 프롬프트)·멀티 폼필 — 월 시리즈·피벗에 같은 metric 해석."""
    import sys

    qg_module = sys.modules["src.nodes.query_generator"]
    text = "최근 30일 CPU 사용률 양식 채워줘"
    qt = _qt(text)
    seen: list[tuple[str, Any]] = []

    def _recog(*args: Any, **kwargs: Any) -> None:
        seen.append(("month_series", kwargs.get("period")))
        return None

    def _pivot(*args: Any, **kwargs: Any) -> str:
        seen.append(("pivot", kwargs.get("period")))
        seen.append(("stat_month", kwargs.get("stat_month")))
        return _LLM_SQL

    for module in (qg_module, mdb):
        monkeypatch.setattr(module, "recognize_month_series", _recog)
        monkeypatch.setattr(module, "build_form_fill_pivot_sql", _pivot)

    form = {"column_mapping": dict(_FORM_MAPPING), "schema_info": _FORM_META,
            "template_structure": _FORM_TEMPLATE}
    # 단일: 결정적 조립(첫 턴)
    await query_generator(_state(text, **form), llm=_CapturingLLM(), app_config=_cfg())  # type: ignore[arg-type]
    # 단일: 재시도 턴 — 결정적 조립을 건너뛰고 LLM 폴백 프롬프트의 월 시리즈 블록 경로
    await query_generator(
        _state(text, error_message="x", generated_sql="SELECT 1", **form),
        llm=_CapturingLLM(), app_config=_cfg(),  # type: ignore[arg-type]
    )
    # 멀티: 양식 매핑 섹션 → 폼필 피벗
    run = await mdb._prepare_multi_run(_state(text, **form), _CapturingLLM(), _cfg())  # type: ignore[arg-type]
    await mdb._generate_validated_sql(
        run, MagicMock(), _FORM_META, text, dict(_FORM_MAPPING),
        db_engine="postgresql", db_id=_DB_ID,
    )
    periods = [v for k, v in seen if k in ("month_series", "pivot")]
    assert [k for k, _ in seen if k != "stat_month"].count("month_series") == 3
    assert [k for k, _ in seen].count("pivot") == 2
    assert periods and all(p == qt.metric for p in periods)
    assert all(v is None for k, v in seen if k == "stat_month")  # 일 입도 — 호환 값 없음


@pytest.mark.parametrize("text", ("어제 알람 목록", "이번 달 알람 목록", "최근 발생 순 알람 100건"))
async def test_alarm_assembly_uses_event_on_both_paths(text: str) -> None:
    qt = _qt(text)
    assert qt.event is not None
    cfg = _cfg(alarm_deterministic=True)
    state = _state(text, routing_intent="alarm_query")

    llm = _CapturingLLM()
    single = await query_generator(state, llm=llm, app_config=cfg)  # type: ignore[arg-type]
    assert not llm.calls  # 결정적 조립 — LLM 미호출
    run = await mdb._prepare_multi_run(state, _CapturingLLM(), cfg)  # type: ignore[arg-type]
    multi_sql = mdb._deterministic_alarm_sql_or_none(run, db_engine="postgresql", db_id=_DB_ID)

    assert multi_sql is not None and single["generated_sql"]
    bounds = alarm_ts_bounds(qt.event)
    for sql in (single["generated_sql"], multi_sql):
        if bounds is None:  # 기간 미지정 = 조건 없음(D-291)
            assert "a.ctime >=" not in sql and "a.ctime <" not in sql
        else:
            assert sql_applies_period(sql, qt.event)


async def test_alarm_assembly_receives_event_period(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def _fake(*args: Any, **kwargs: Any) -> None:
        seen.append(kwargs.get("period"))
        return None

    monkeypatch.setattr(polestar_assembler, "try_deterministic_alarm_sql", _fake)
    text = "이번 달 알람 목록"
    cfg = _cfg(alarm_deterministic=True)
    state = _state(text, routing_intent="alarm_query")
    await query_generator(state, llm=_CapturingLLM(), app_config=cfg)  # type: ignore[arg-type]
    run = await mdb._prepare_multi_run(state, _CapturingLLM(), cfg)  # type: ignore[arg-type]
    mdb._deterministic_alarm_sql_or_none(run, db_engine="postgresql", db_id=_DB_ID)
    assert seen == [_qt(text).event, _qt(text).event]


async def test_multi_run_carries_resolution_to_generate_and_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gen_kwargs: list[dict[str, Any]] = []
    val_kwargs: list[dict[str, Any]] = []

    async def _gen(*args: Any, **kwargs: Any) -> str:
        gen_kwargs.append(kwargs)
        return _LLM_SQL

    def _val(*args: Any, **kwargs: Any) -> tuple[None, None]:
        val_kwargs.append(kwargs)
        return None, None

    monkeypatch.setattr(mdb, "_generate_sql", _gen)
    monkeypatch.setattr(mdb, "_validate_sql", _val)
    text = "어제 CPU 사용률"
    qt = _qt(text)
    run = await mdb._prepare_multi_run(_state(text), _CapturingLLM(), _cfg())  # type: ignore[arg-type]
    assert run.query_time == qt
    await mdb._generate_validated_sql(
        run, MagicMock(), _SCHEMA_INFO, text, {}, db_engine="postgresql", db_id=_DB_ID,
    )
    assert gen_kwargs[0]["query_time"] == qt
    assert val_kwargs[0]["time_resolution"] == qt.to_state()

    # 플래그 off — 해석 없음
    gen_kwargs.clear()
    val_kwargs.clear()
    run_off = await mdb._prepare_multi_run(
        _state(text, time_resolution=None), _CapturingLLM(), _cfg(),  # type: ignore[arg-type]
    )
    assert run_off.query_time is None
    await mdb._generate_validated_sql(
        run_off, MagicMock(), _SCHEMA_INFO, text, {}, db_engine="postgresql", db_id=_DB_ID,
    )
    assert gen_kwargs[0]["query_time"] is None and val_kwargs[0]["time_resolution"] is None


def test_multi_full_validation_passes_resolution_to_adapter_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    class _Adapter:
        def validator_checks(self, user_query: Any = None, **kwargs: Any) -> list[Any]:
            seen.append({"user_query": user_query, **kwargs})
            return []

    monkeypatch.setattr("src.db_adapters.get_adapter", lambda db_id, ids=None: _Adapter())
    qt = _qt("어제 CPU 사용률")
    mdb._validate_sql(
        _LLM_SQL, _SCHEMA_INFO, db_id=_DB_ID, db_engine="postgresql", user_query="q",
        app_config=_cfg(multi_full_validation=True), time_resolution=qt.to_state(),
    )
    assert seen == [{"user_query": "q", "time_resolution": qt.to_state()}]


def test_spike_comparison_uses_resolution_anchor() -> None:
    """기간 대비(급증)의 기준일 = 해석 기준 시각(오늘이 아니다 — 단일 기준 시각)."""
    from src.config import AppConfig, QueryConfig, Text2SQLConfig

    config = AppConfig()
    config.query = QueryConfig()
    config.text2sql = Text2SQLConfig(spike_condition_enabled=True)
    text = "지난달 대비 파일시스템 사용률이 갑자기 80% 이상으로 상승한 서버 목록"
    qt = _qt(text)
    ctx = _GenContext(
        llm=MagicMock(), app_config=config, user_query=text, retry_count=0, is_retry=False,
        limit_value=100, stat_month=stat_month_compat(qt), stat_block_db=True,
        conversation_context=None, prior_scope=None, adapter_db_ids={"polestar"},
        query_time=qt,
    )
    state = {"active_db_id": "polestar", "active_db_engine": "postgresql", "user_query": text,
             "parsed_requirements": {"query_targets": ["서버"]}, "template_structure": None,
             "column_mapping": None}
    result = _try_spike(state, ctx)
    assert result and result.get("sql")
    # 기준 2026-09-29 → 비교 202607 대비 202608 (오늘 기준이면 202608 대비 202609)
    assert "'202608'" in result["sql"] and "'202607'" in result["sql"]
    assert "'202609'" not in result["sql"]


# ──────────────────────────────────────────────
# 6. (교정 1) 멀티 간이 검증의 시간 조건 대조 — 단일 경로와 대칭
# ──────────────────────────────────────────────

_GOOD_30D = (
    "SELECT r.name, AVG(s.avg_val) AS cpu FROM polestar.cmm_resource r "
    "JOIN polestar.cmm_metric_stat_d s ON s.resource_id = r.id WHERE r.dtime IS NULL "
    "AND s.stat_date >= '20260830' AND s.stat_date < '20260929' GROUP BY r.name LIMIT 10"
)
_BAD_30D = (
    "SELECT r.name, AVG(s.avg_val) AS cpu FROM polestar.cmm_resource r "
    "JOIN polestar.cmm_metric_stat_m s ON s.resource_id = r.id WHERE r.dtime IS NULL "
    "AND s.stat_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM') "
    "GROUP BY r.name LIMIT 10"
)
_STAT_SCHEMA: dict[str, Any] = {
    "tables": {
        name: {"columns": [{"name": c, "type": "varchar(32)"} for c in cols]}
        for name, cols in {
            "polestar.cmm_resource": ("id", "name", "dtime"),
            "polestar.cmm_metric_stat_d": ("resource_id", "avg_val", "stat_date"),
            "polestar.cmm_metric_stat_m": ("resource_id", "avg_val", "stat_date"),
        }.items()
    },
}


class _ScriptedLLM(_CapturingLLM):
    """정해 둔 SQL을 차례로 돌려주는 LLM 대역(마지막 값을 반복)."""

    def __init__(self, *sqls: str) -> None:
        super().__init__()
        self._sqls = list(sqls)

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        self.calls.append(list(messages))
        sql = self._sqls[min(len(self.calls), len(self._sqls)) - 1]
        return SimpleNamespace(content=f"```sql\n{sql}\n```")


async def _multi_validated(llm: _CapturingLLM, text: str, cfg: MagicMock | None = None,
                           **state: Any) -> tuple[Any, str, str | None]:
    run = await mdb._prepare_multi_run(  # type: ignore[arg-type]
        _state(text, schema_info=_STAT_SCHEMA, **state), llm, cfg or _cfg(),
    )
    sql, error = await mdb._generate_validated_sql(
        run, MagicMock(), _STAT_SCHEMA, text, {}, db_engine="postgresql", db_id=_DB_ID,
    )
    return run, sql, error


async def test_multi_simple_mode_regenerates_on_time_condition_error() -> None:
    llm = _ScriptedLLM(_BAD_30D, _GOOD_30D)
    _run, sql, error = await _multi_validated(llm, "최근 30일 CPU 사용률")
    assert error is None and sql == _GOOD_30D
    assert len(llm.calls) == 2
    retry_prompt = str([m for m in llm.calls[1] if isinstance(m, HumanMessage)][0].content)
    assert "## 이전 에러" in retry_prompt and "cmm_metric_stat_d" in retry_prompt


async def test_multi_simple_mode_time_error_stops_within_retry_budget() -> None:
    llm = _ScriptedLLM(_BAD_30D)
    run, _sql, error = await _multi_validated(llm, "최근 30일 CPU 사용률")
    assert len(llm.calls) == 3  # 1회 + 재생성 2회(단일 경로 재시도 3회와 대칭)
    assert error and "s.stat_date >= '20260830' AND s.stat_date < '20260929'" in error
    assert run.regen_stops[_DB_ID]["reason"] == mdb.REGEN_STOP_VALIDATION_BUDGET


async def test_multi_simple_mode_skips_time_check_without_resolution_or_default() -> None:
    # 플래그 off — 종전 간이 검증 그대로(시간 대조 없음)
    llm = _ScriptedLLM(_BAD_30D)
    _run, sql, error = await _multi_validated(llm, "최근 30일 CPU 사용률", time_resolution=None)
    assert error is None and sql == _BAD_30D and len(llm.calls) == 1
    # 기간 미지정 기본값 — 대조는 경고 로그만(반려 없음)
    llm = _ScriptedLLM(_BAD_30D)
    _run, sql, error = await _multi_validated(llm, "CPU 상위 10대")
    assert error is None and len(llm.calls) == 1


def test_multi_simple_time_check_only_on_polestar_db(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    real = mdb.check_time_conditions

    def _spy(sql: str, qt: QueryTime) -> list[str]:
        seen.append(sql)
        return real(sql, qt)

    monkeypatch.setattr(mdb, "check_time_conditions", _spy)
    tr = _qt("최근 30일 CPU 사용률").to_state()
    error, _fixed = mdb._validate_sql(
        _BAD_30D, _STAT_SCHEMA, db_id=_DB_ID, db_engine="postgresql", user_query="q",
        app_config=_cfg(), time_resolution=tr,
    )
    assert error and "cmm_metric_stat_d" in error and seen == [_BAD_30D]
    other = _cfg()
    other.get_polestar_db_ids.return_value = set()  # 폴스타 게이트 밖(무선언·생성 어댑터 DB)
    seen.clear()
    error, _fixed = mdb._validate_sql(
        _BAD_30D, _STAT_SCHEMA, db_id="itam", db_engine="postgresql", user_query="q",
        app_config=other, time_resolution=tr,
    )
    assert error is None and seen == []


def test_multi_full_mode_runs_time_check_once_via_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """full 모드 — 어댑터 훅이 같은 검사를 등록하므로 간이 경로 대조는 돌지 않는다(중복 없음)."""
    from src.db_adapters.polestar import adapter as polestar_adapter

    calls: list[str] = []
    real = polestar_adapter.check_time_conditions

    def _adapter_spy(sql: str, qt: QueryTime) -> list[str]:
        calls.append("adapter")
        return real(sql, qt)

    def _simple_spy(sql: str, qt: QueryTime) -> list[str]:
        calls.append("simple")
        return []

    monkeypatch.setattr(polestar_adapter, "check_time_conditions", _adapter_spy)
    monkeypatch.setattr(mdb, "check_time_conditions", _simple_spy)
    error, _fixed = mdb._validate_sql(
        _BAD_30D, _STAT_SCHEMA, db_id=_DB_ID, db_engine="postgresql", user_query="q",
        app_config=_cfg(multi_full_validation=True),
        time_resolution=_qt("최근 30일 CPU 사용률").to_state(),
    )
    assert calls == ["adapter"]
    assert error and "cmm_metric_stat_d" in error


# ──────────────────────────────────────────────
# 7. (교정 2) 「현재·지금」+기간 미지정 — 결정적 경로에 기본값을 넘기지 않는다
# ──────────────────────────────────────────────


def test_metric_period_follows_compiler_rule() -> None:
    assert metric_period(None) is None
    assert metric_period(_qt("현재 CPU 사용률")) is None          # 기본값 강제 안 함(D-291)
    assert metric_period(_qt("CPU 상위 10대")) == _qt("CPU 상위 10대").metric  # 기본값
    assert metric_period(_qt("최근 30일 CPU")) == _qt("최근 30일 CPU").metric  # 명시
    assert metric_period(QueryTime(NOW, None, None, clarify="invalid_date")) is None


async def test_present_query_form_fill_passes_no_default_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    qg_module = sys.modules["src.nodes.query_generator"]
    text = "현재 CPU 사용률 양식 채워줘"
    seen: list[tuple[str, Any]] = []

    def _recog(*args: Any, **kwargs: Any) -> None:
        seen.append(("month_series", kwargs.get("period")))
        return None

    def _pivot(*args: Any, **kwargs: Any) -> str:
        seen.append(("pivot", kwargs.get("period")))
        seen.append(("stat_month", kwargs.get("stat_month")))
        return _LLM_SQL

    for module in (qg_module, mdb):
        monkeypatch.setattr(module, "recognize_month_series", _recog)
        monkeypatch.setattr(module, "build_form_fill_pivot_sql", _pivot)
    form = {"column_mapping": dict(_FORM_MAPPING), "schema_info": _FORM_META,
            "template_structure": _FORM_TEMPLATE}
    await query_generator(_state(text, **form), llm=_CapturingLLM(), app_config=_cfg())  # type: ignore[arg-type]
    await query_generator(
        _state(text, error_message="x", generated_sql="SELECT 1", **form),
        llm=_CapturingLLM(), app_config=_cfg(),  # type: ignore[arg-type]
    )
    run = await mdb._prepare_multi_run(_state(text, **form), _CapturingLLM(), _cfg())  # type: ignore[arg-type]
    await mdb._generate_validated_sql(
        run, MagicMock(), _FORM_META, text, dict(_FORM_MAPPING),
        db_engine="postgresql", db_id=_DB_ID,
    )
    assert [k for k, _ in seen].count("month_series") == 3
    assert [k for k, _ in seen].count("pivot") == 2
    assert all(v is None for _k, v in seen)


async def test_present_query_form_fill_sql_has_no_default_month() -> None:
    from src.nodes.query_generator import _try_build_form_fill_pivot_sql

    text = "현재 CPU 사용률 양식 채워줘"
    qt = _qt(text)
    assert qt.present and qt.metric is not None and qt.metric.source == "default"
    state = _state(text, column_mapping=dict(_FORM_MAPPING), schema_info=_FORM_META,
                   template_structure=_FORM_TEMPLATE)
    result = _try_build_form_fill_pivot_sql(
        state, 1000, text, adapter_db_ids={_DB_ID}, query_time=qt,
    )
    assert result and result["sql"]
    assert not sql_applies_period(result["sql"], qt.metric)  # 지난달(202608) 강제 없음
    assert "'202608'" not in result["sql"]


# ──────────────────────────────────────────────
# 8. (교정 3) 단계적 도출 도구 컨텍스트 — time_resolution 운반
# ──────────────────────────────────────────────


def test_single_stepwise_deps_carry_time_resolution() -> None:
    from src.nodes.column_deriver import build_tool_context
    from src.nodes.query_generator import _build_stepwise_deps

    state = _state("최근 30일 CPU 사용률")
    deps = _build_stepwise_deps(state, _cfg(stepwise_derivation=True), 100)  # type: ignore[arg-type]
    assert deps is not None and deps.time_resolution == state["time_resolution"]
    ctx = build_tool_context(_DB_ID, {}, state["user_query"], deps)
    assert ctx.time_resolution == state["time_resolution"]
    off = _build_stepwise_deps(  # type: ignore[arg-type]
        _state("q", time_resolution=None), _cfg(stepwise_derivation=True), 100,
    )
    assert off is not None and off.time_resolution is None
    assert build_tool_context(_DB_ID, {}, "q", off).time_resolution is None


async def test_multi_stepwise_deps_carry_time_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []

    async def _fake(llm: Any, user_query: str, db_id: str, **kwargs: Any) -> tuple[Any, ...]:
        captured.append(kwargs.get("stepwise_deps"))
        return None, None, None

    monkeypatch.setattr(mdb, "compile_from_nl", _fake)
    text = "어제 CPU 사용률"
    await _multi_prompt(_state(text), _cfg(semantic_compose=True, stepwise_derivation=True))
    (deps,) = captured
    assert deps is not None and deps.time_resolution == _qt(text).to_state()


# ──────────────────────────────────────────────
# 9. (2차 교정) 판정 단일 출처 · span 정규화
# ──────────────────────────────────────────────


@pytest.mark.parametrize("text", QUERIES)
def test_metric_period_delegates_to_domain_rule(text: str) -> None:
    qt = _qt(text)
    assert metric_period(qt) is qt.metric_for_sql
    sb = qt.metric_for_sql
    assert stat_month_compat(qt) == (sb.month_range() if sb is not None else None)
    # 기간 블록도 같은 판정 — 적용할 해석이 없으면 블록이 없다
    assert (build_period_block(qt) == "") == (sb is None)


def test_period_block_normalizes_and_caps_span() -> None:
    qt = _qt("최근 30일 CPU 사용률")
    assert qt.metric is not None
    messy = replace(qt, metric=replace(qt.metric, span="최근   30일\n  동안"))
    assert "질의 표현 「최근 30일 동안」" in build_period_block(messy)
    long_span = "가" * (SPAN_MAX_CHARS + 10)
    capped = build_period_block(replace(qt, metric=replace(qt.metric, span=long_span)))
    expected = "가" * (SPAN_MAX_CHARS - 1) + "…"
    assert f"「{expected}」" in capped and len(expected) == SPAN_MAX_CHARS == 40
    blank = build_period_block(replace(qt, metric=replace(qt.metric, span="  \n ")))
    assert "질의 표현" not in blank and blank.startswith(PERIOD_BLOCK_HEADER)
