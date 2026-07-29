"""폴백 few-shot 동적 선택 테스트 (Plan 67 트랙 N / N2, D-133).

검증 축:
    1. 플래그 OFF — 프롬프트 **바이트 동일** + 이력 검색 호출 0건(Redis 접근 없음).
    2. 플래그 ON·적중 — 고정 few-shot(프로필 예시) 대신 이력 예시가 주입되고 채택 계측.
    3. 플래그 ON·무적중/실패 — 고정 few-shot으로 조용히 복귀하되 사유는 로그.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config import Text2SQLConfig
from src.nodes.query_generator import (
    _build_system_prompt,
    _format_structure_guide,
    _select_query_history_examples,
)
from src.schema_cache import query_history as qh

_PROFILE_QUESTION = "호스트명, OS종류, 벤더를 조회해줘"
_HISTORY_QUESTION = "전체 서버의 호스트명을 조회해줘"
_HISTORY_SQL = "SELECT hostname FROM t WHERE flag IS NULL"

_STRUCTURE_META = {
    "query_guide": "테스트 쿼리 가이드",
    "patterns": [],
    "query_examples": [
        {
            "question": _PROFILE_QUESTION,
            "sql": "SELECT hostname, os_type FROM t",
            "explanation": "프로필 고정 예시",
        }
    ],
}

_SCHEMA_INFO = {
    "tables": {
        "t": {
            "columns": [
                {"name": "hostname", "type": "varchar"},
                {"name": "os_type", "type": "varchar"},
            ]
        }
    },
    "relationships": [],
    "_structure_meta": _STRUCTURE_META,
}


def _config(**overrides) -> SimpleNamespace:
    """text2sql 플래그만 명시한 설정 대역(.env 누수 차단)."""
    defaults = {
        "query_history_fewshot": False,
        "query_history_top_k": 2,
        "query_history_min_score": 0.35,
    }
    return SimpleNamespace(text2sql=Text2SQLConfig(**{**defaults, **overrides}))


@pytest.fixture(autouse=True)
def _clean_metrics():
    qh.reset_search_metrics()
    yield
    qh.reset_search_metrics()


# ──────────────────────────────────────────────
# 1. 플래그 OFF — 회귀 0
# ──────────────────────────────────────────────

def test_prompt_is_byte_identical_without_history():
    """이력 예시가 없으면 시스템 프롬프트가 기존과 바이트 동일하다."""
    baseline = _build_system_prompt(_SCHEMA_INFO, 100)
    explicit_none = _build_system_prompt(
        _SCHEMA_INFO, 100, query_history_examples=None,
    )
    assert explicit_none == baseline
    assert _PROFILE_QUESTION in baseline


def test_structure_guide_is_byte_identical_without_history():
    """구조 가이드도 이력 미주입 시 기존 경로와 바이트 동일하다."""
    baseline = _format_structure_guide(_STRUCTURE_META)
    assert _format_structure_guide(
        _STRUCTURE_META, query_history_examples=None,
    ) == baseline
    assert _format_structure_guide(
        _STRUCTURE_META, query_history_examples=[],
    ) == baseline


async def test_flag_off_skips_search_entirely(monkeypatch):
    """플래그 OFF면 검색 함수를 아예 호출하지 않는다(Redis 접근 0)."""
    async def _fail(*args, **kwargs):
        raise AssertionError("플래그 OFF인데 이력 검색이 호출됐다")

    monkeypatch.setattr(qh, "search_query_history", _fail)
    state = {"active_db_id": "db1"}
    assert await _select_query_history_examples(state, "서버 목록", _config()) is None
    assert qh.search_metrics_snapshot()["searches"] == 0


# ──────────────────────────────────────────────
# 2. 플래그 ON — 동적 주입
# ──────────────────────────────────────────────

async def test_flag_on_returns_history_examples(monkeypatch):
    """적중 시 few-shot 예시(question/sql)를 돌려주고 채택을 계측한다."""
    async def _hit(db_id, user_query, *, top_k, min_score, store=None):
        assert (db_id, top_k, min_score) == ("db1", 2, 0.35)
        return [{"query": _HISTORY_QUESTION, "sql": _HISTORY_SQL, "score": 0.9}]

    monkeypatch.setattr(qh, "search_query_history", _hit)
    examples = await _select_query_history_examples(
        {"active_db_id": "db1"}, "서버 호스트명 조회",
        _config(query_history_fewshot=True),
    )
    assert examples == [{"question": _HISTORY_QUESTION, "sql": _HISTORY_SQL}]
    assert qh.search_metrics_snapshot()["adoptions"] == 1


def test_history_examples_replace_fixed_fewshot():
    """이력 예시가 주어지면 프로필 고정 예시 대신 그것이 프롬프트에 실린다."""
    prompt = _build_system_prompt(
        _SCHEMA_INFO, 100,
        query_history_examples=[
            {"question": _HISTORY_QUESTION, "sql": _HISTORY_SQL}
        ],
    )
    assert _HISTORY_QUESTION in prompt
    assert _PROFILE_QUESTION not in prompt
    # 블록 포맷은 기존 헬퍼를 그대로 통과 — 머리말·코드펜스 유지
    assert "## 쿼리 예시 (반드시 이 패턴을 따르세요)" in prompt
    assert f"```sql\n{_HISTORY_SQL}\n```" in prompt


# ──────────────────────────────────────────────
# 3. 무적중·실패 강등
# ──────────────────────────────────────────────

async def test_no_hit_keeps_fixed_fewshot(monkeypatch):
    """임계 미달(무적중)이면 None → 고정 few-shot 유지."""
    async def _miss(*args, **kwargs):
        return []

    monkeypatch.setattr(qh, "search_query_history", _miss)
    assert await _select_query_history_examples(
        {"active_db_id": "db1"}, "무관한 질의",
        _config(query_history_fewshot=True),
    ) is None


async def test_search_failure_degrades_with_log(monkeypatch, caplog):
    """검색 실패는 SQL 생성을 막지 않고, 사유를 로그로 남긴다(침묵 금지)."""
    async def _boom(*args, **kwargs):
        raise ConnectionError("Redis down")

    monkeypatch.setattr(qh, "search_query_history", _boom)
    with caplog.at_level("WARNING"):
        result = await _select_query_history_examples(
            {"active_db_id": "db1"}, "서버 목록",
            _config(query_history_fewshot=True),
        )
    assert result is None
    assert "질의 이력 검색 실패" in caplog.text


async def test_missing_db_id_skips_search(monkeypatch):
    """활성 DB가 없으면 검색하지 않는다."""
    async def _fail(*args, **kwargs):
        raise AssertionError("db_id가 없는데 검색이 호출됐다")

    monkeypatch.setattr(qh, "search_query_history", _fail)
    assert await _select_query_history_examples(
        {"active_db_id": ""}, "서버 목록", _config(query_history_fewshot=True),
    ) is None
