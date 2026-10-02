"""테이블 선택 LLM 생략 — 질의 신호가 프로필 선언 집합 안일 때 (plans/119 Q-5 · D-267 ④).

조사(run `20260923-103638` 서버 로그 · 단일 경로 `schema_analyzer` 354회):
- 비알람 호출 321회 전부에서 최종 집합 ⊆ 프로필 `allowed_tables` ∪ 질의 유사어 테이블(위반 0).
- 그중 297회(전체의 83.9%)는 최종 집합 == `allowed_tables` — 유사어 신호가 선언 집합 밖의
  실재 테이블을 하나도 가리키지 않았다. 이 경우 2-2 필터·보충의 결과는 LLM 출력과 무관하다
  (보충 = 프로필 테이블만 · plans/114 P-4①, 필터 = 선언 ∪ 신호 밖 제거).
- 알람 의도 33회는 LLM이 `alarm_allowed_tables`(12개)를 4개로 좁힌다 — LLM 의존이라 대상 밖.

여기서 고정하는 것:
- 플래그 off(기본)는 종전 순서·호출 그대로다(LLM 1회 → 유사어 조회 1회).
- on + 신호가 선언 집합 안이면 LLM을 부르지 않고, 최종 집합은 off 경로가 **어떤 LLM 출력**에서도
  내는 집합과 같다(동등성).
- on + 신호가 선언 밖 · 알람 의도 · 프로필 부재 · 선언 테이블 스키마 부재 · 유사어 조회 실패면
  종전대로 LLM을 부르고 결과도 off와 같다.

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import AppConfig, Text2SQLConfig
from src.nodes.schema_analyzer import _table_select_skip_enabled, schema_analyzer

#: 스키마 — 선언 집합 3개(엔티티·EAV 설정·지표) + 선언 밖 2개(유사어 대상 · 무관).
_DECLARED = ("app.entity", "app.entity_prop", "app.metric_h")
_TABLES = _DECLARED + ("app.audit_log", "app.misc")

_PROFILE = {
    "source": "manual",
    "allowed_tables": ["entity", "entity_prop", "metric_h"],
    "patterns": [{"type": "eav", "entity_table": "entity", "config_table": "entity_prop"}],
}

#: 질의 `"서버 상태 목록"`의 유사어 신호 — 선언 안(`entity`)만 / 선언 밖(`audit_log`) 포함.
_SYN_INSIDE = {"entity.status": ["상태"]}
_SYN_OUTSIDE = {"entity.status": ["상태"], "audit_log.host": ["서버"]}


def _schema_dict() -> dict:
    return {
        "tables": {name: {"columns": [{"name": "id", "type": "integer"}]} for name in _TABLES},
        "relationships": [],
    }


def _config(*, skip: bool) -> AppConfig:
    cfg = AppConfig(checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    cfg.text2sql = Text2SQLConfig(schema_table_select_skip_enabled=skip)
    return cfg


async def _run(
    sample_state: dict,
    *,
    skip: bool,
    llm_picks: str,
    synonyms: dict | Exception,
    profile: dict | None = _PROFILE,
    routing_intent: str | None = None,
) -> tuple[list[str], AsyncMock, AsyncMock, list[str]]:
    """schema_analyzer를 목 스키마·목 LLM으로 돌린다. (relevant, llm, cache_mgr, 호출 순서)."""
    state = dict(sample_state)
    state["active_db_id"] = "app_db"
    state["routing_intent"] = routing_intent
    state["parsed_requirements"] = {"query_targets": ["서버"], "original_query": "서버 상태 목록"}

    order: list[str] = []
    llm = AsyncMock()

    async def _llm_invoke(*_a, **_k):
        order.append("llm")
        return MagicMock(content=llm_picks)

    llm.ainvoke.side_effect = _llm_invoke

    mgr = AsyncMock()
    mgr.get_schema_or_fetch.return_value = (_schema_dict(), True, "메모리", {}, {})

    async def _get_synonyms(_db_id):
        order.append("synonyms")
        if isinstance(synonyms, Exception):
            raise synonyms
        return synonyms

    mgr.get_synonyms.side_effect = _get_synonyms

    @asynccontextmanager
    async def _ctx(client):
        yield client

    with patch("src.nodes.schema_analyzer.get_db_client", return_value=_ctx(AsyncMock())), \
         patch("src.nodes.schema_analyzer.get_cache_manager", return_value=mgr), \
         patch("src.nodes.schema_analyzer._load_manual_profile", return_value=profile):
        result = await schema_analyzer(state, llm=llm, app_config=_config(skip=skip))
    return list(result.get("relevant_tables") or []), llm, mgr, order


# ── 플래그 판정 ──


def test_flag_defaults_off_and_env_name_is_fixed(monkeypatch) -> None:
    """플래그 기본값은 off이고 환경변수명이 고정된다."""
    monkeypatch.delenv("TEXT2SQL_SCHEMA_TABLE_SELECT_SKIP_ENABLED", raising=False)
    assert Text2SQLConfig(_env_file=None).schema_table_select_skip_enabled is False
    monkeypatch.setenv("TEXT2SQL_SCHEMA_TABLE_SELECT_SKIP_ENABLED", "true")
    assert Text2SQLConfig(_env_file=None).schema_table_select_skip_enabled is True


def test_mock_config_does_not_turn_flag_on() -> None:
    """목 설정은 플래그를 켜지 않는다."""
    assert _table_select_skip_enabled(MagicMock()) is False
    assert _table_select_skip_enabled(_config(skip=False)) is False
    assert _table_select_skip_enabled(_config(skip=True)) is True


# ── off = 종전 동작 ──


@pytest.mark.asyncio
async def test_off_keeps_legacy_order_llm_then_synonyms(sample_state) -> None:
    """off는 LLM 먼저 · 유사어 조회 한 번 — 종전 순서다."""
    relevant, llm, mgr, order = await _run(
        sample_state, skip=False, llm_picks="app.entity", synonyms=_SYN_INSIDE,
    )

    assert order == ["llm", "synonyms"]
    llm.ainvoke.assert_awaited_once()
    assert set(relevant) == set(_DECLARED)


# ── on + 신호가 선언 안 = LLM 생략 · 동등성 ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm_picks",
    [
        "app.entity",                          # 엔티티만(EAV 동반 보충 경로)
        "app.metric_h",                        # 선언 일부만
        "app.misc, app.audit_log",             # 선언 밖만(필터 경로)
        ", ".join(_TABLES),                    # 전부
        "모르겠습니다",                         # 무효 응답(전체 폴백 경로)
    ],
)
async def test_on_signal_inside_skips_llm_with_same_set_as_off(
    sample_state, llm_picks: str,
) -> None:
    off_relevant, _, _, _ = await _run(
        sample_state, skip=False, llm_picks=llm_picks, synonyms=_SYN_INSIDE,
    )
    on_relevant, llm, mgr, order = await _run(
        sample_state, skip=True, llm_picks=llm_picks, synonyms=_SYN_INSIDE,
    )

    llm.ainvoke.assert_not_awaited()
    assert order == ["synonyms"], "유사어 신호는 한 번만 조회해 2-2에서 재사용한다"
    assert set(on_relevant) == set(off_relevant) == set(_DECLARED)
    assert on_relevant == list(_DECLARED), "선언 순서 — 요청마다 같은 프롬프트 접두"


@pytest.mark.asyncio
async def test_on_skip_is_logged(sample_state, caplog) -> None:
    """on 생략은 로그로 남는다."""
    with caplog.at_level(logging.INFO, logger="src.nodes.schema_analyzer"):
        await _run(sample_state, skip=True, llm_picks="app.entity", synonyms=_SYN_INSIDE)

    assert any("테이블 선택 LLM 생략(plans/119 Q-5)" in r.getMessage() for r in caplog.records)


# ── on 이지만 종전대로 LLM을 부르는 경우 ──


@pytest.mark.asyncio
@pytest.mark.parametrize("llm_picks", ["app.entity", "app.entity, app.audit_log"])
async def test_on_signal_outside_keeps_llm(sample_state, llm_picks: str) -> None:
    """on이어도 신호가 선언 밖이면 종전대로 LLM이 정한다."""
    off_relevant, _, _, _ = await _run(
        sample_state, skip=False, llm_picks=llm_picks, synonyms=_SYN_OUTSIDE,
    )
    on_relevant, llm, _, order = await _run(
        sample_state, skip=True, llm_picks=llm_picks, synonyms=_SYN_OUTSIDE,
    )

    llm.ainvoke.assert_awaited_once()
    assert order == ["synonyms", "llm"], "신호는 LLM 전에 한 번만 조회한다"
    assert on_relevant == off_relevant, "선언 밖 신호 — LLM 경로 결과를 그대로 쓴다"


@pytest.mark.asyncio
async def test_on_alarm_intent_is_out_of_scope(sample_state) -> None:
    """알람 의도는 대상 밖이다 — LLM이 알람 허용 테이블을 좁힌다."""
    alarm_picks = "app.entity, app.audit_log"
    off_relevant, _, _, _ = await _run(
        sample_state, skip=False, llm_picks=alarm_picks, synonyms=_SYN_INSIDE,
        routing_intent="alarm_query",
    )
    on_relevant, llm, _, order = await _run(
        sample_state, skip=True, llm_picks=alarm_picks, synonyms=_SYN_INSIDE,
        routing_intent="alarm_query",
    )

    llm.ainvoke.assert_awaited_once()
    assert order == ["llm"]
    assert on_relevant == off_relevant


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        None,                                                  # 프로필 부재
        {"source": "manual"},                                  # allowed_tables 미선언
        {"source": "manual", "allowed_tables": ["nowhere"]},   # 선언 테이블이 스키마에 없음
    ],
)
async def test_on_without_usable_declaration_calls_llm(sample_state, profile) -> None:
    """선언이 없거나 스키마에 없으면 LLM을 부른다."""
    off_relevant, _, _, _ = await _run(
        sample_state, skip=False, llm_picks="app.entity", synonyms=_SYN_INSIDE, profile=profile,
    )
    on_relevant, llm, _, _ = await _run(
        sample_state, skip=True, llm_picks="app.entity", synonyms=_SYN_INSIDE, profile=profile,
    )

    llm.ainvoke.assert_awaited_once()
    assert on_relevant == off_relevant


@pytest.mark.asyncio
async def test_on_synonym_lookup_failure_calls_llm(sample_state) -> None:
    """유사어 조회가 실패하면 LLM을 부른다."""
    off_relevant, _, _, _ = await _run(
        sample_state, skip=False, llm_picks="app.entity", synonyms=RuntimeError("redis down"),
    )
    on_relevant, llm, _, order = await _run(
        sample_state, skip=True, llm_picks="app.entity", synonyms=RuntimeError("redis down"),
    )

    llm.ainvoke.assert_awaited_once()
    assert order == ["synonyms", "llm", "synonyms"], "판정 실패 뒤 2-2는 종전대로 다시 조회한다"
    assert on_relevant == off_relevant
