"""plans/150 W5 — 이름 칸 0행 재생성 힌트의 결정적 핵심어 후보 (F6 ② · D-297 부기).

내부망 5회차 118은 「핵심어를 뽑아라」 힌트만 받고 값 문구 전체(「통합인증 업무 스토리지」)를
LIKE에 넣었다. 그래서 등호·`IN` 리터럴에서 일반어와 공백 경계 조사를 뺀 가장 긴 낱말 하나를
힌트에 싣는다. 후보가 없으면 종전 문구와 비트 동일하다. LLM·DB는 쓰지 않는다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

import src.nodes.name_match_retry as nmr
import src.orchestration.subagents as sub
from src.nodes.name_match_retry import (
    GENERIC_NAME_WORDS,
    build_name_match_hint,
    find_name_literals,
    loggable_reason,
    name_keyword,
    name_match_columns,
    name_match_retry_hint,
)

DB = "itam"

DESCRIPTIONS = {
    "tcdmsif52.업무명": "업무 이름 자유 기재. 등호·IN이 아니라 LIKE 부분 일치로 건다.",
    "tcdmsif52.호스트명": "서버 원장 서버호스트명과 잇는 키(정의 관계).",
}
COLS = name_match_columns(DESCRIPTIONS)

#: 149 W3 종전 문구 원문 — 후보가 없을 때 이 문자열과 비트 동일해야 한다.
LEGACY_HINT = (
    "이전 SQL은 0행이었습니다. 이름 칸 `업무명`에 등호(=)·IN으로 값을 그대로 걸었습니다 — "
    "이 칸은 컬럼 설명이 부분 일치로 표시한 이름 칸입니다. "
    "이 칸 조건만 질문에서 고유한 핵심어를 뽑아 LIKE '%핵심어%' 부분 일치로 다시 쓰고, "
    "다른 조건·조인·선택 칼럼은 그대로 두세요. "
    "컬럼 설명이 이 칸의 사용 범위를 제한하면(예: 특정 질문에만 쓴다) 그 설명을 따르세요."
)


def _sql(where: str) -> str:
    return f"SELECT a.`호스트명`\nFROM `tcdmsif52` AS a\nWHERE {where}\nLIMIT 10000;"


SQL_118 = _sql("a.`업무명` = '통합인증 업무 스토리지'")
SQL_GENERIC = _sql("a.`업무명` = '업무 스토리지'")
SQL_IN = _sql("a.`업무명` IN ('인증 업무', '통합인증의 시스템')")


@pytest.fixture
def knowledge_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """임시 설명 정본 루트 — `itam`만 정본 파일이 있다(폴스타 계열은 없다)."""
    d = tmp_path / DB
    d.mkdir()
    (d / "column_descriptions.yaml").write_text(
        yaml.safe_dump(
            {"version": 1, "origin": "claude_code", "descriptions": DESCRIPTIONS},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(nmr, "KNOWLEDGE_ROOT", tmp_path)
    return tmp_path


def _state(sql: str, *, db_id: str = DB) -> dict[str, Any]:
    return {
        "query_results": [],
        "generated_sql": sql,
        "active_db_id": db_id,
        "is_multi_db": False,
        "retry_count": 0,
        "query_attempts": [{"sql": sql, "success": True, "row_count": 0}],
    }


# ──────────────────────────────────────────────
# 리터럴 추출 · 핵심어 선택(순수 함수)
# ──────────────────────────────────────────────

class TestLiterals:
    def test_equality_literal(self):
        assert find_name_literals(SQL_118, COLS) == ["통합인증 업무 스토리지"]

    def test_in_literals_in_order(self):
        assert find_name_literals(SQL_IN, COLS) == ["인증 업무", "통합인증의 시스템"]

    def test_escaped_quote_unescaped(self):
        assert find_name_literals(_sql("a.`업무명` = 'A''B 업무'"), COLS) == ["A'B 업무"]

    def test_non_name_column_and_like_ignored(self):
        sql = _sql("a.`호스트명` = 'h1' AND a.`업무명` LIKE '%통합인증%'")
        assert find_name_literals(sql, COLS) == []

    def test_case_branch_ignored(self):
        sql = ("SELECT CASE WHEN a.`업무명` = '통합인증' THEN 1 END AS f\n"
               "FROM `tcdmsif52` a WHERE a.`호스트명` LIKE '%h%'")
        assert find_name_literals(sql, COLS) == []

    def test_no_columns(self):
        assert find_name_literals(SQL_118, {}) == []


class TestKeyword:
    def test_generic_words_constant(self):
        assert GENERIC_NAME_WORDS == {"서비스", "업무", "시스템", "스토리지", "서버"}

    def test_118_shape(self):
        assert name_keyword(["통합인증 업무 스토리지"]) == "통합인증"

    def test_generic_only_none(self):
        assert name_keyword(["업무 스토리지"]) is None
        assert name_keyword(["서비스 시스템 서버"]) is None
        assert name_keyword([]) is None

    def test_trailing_particle_stripped(self):
        assert name_keyword(["통합인증의 서버"]) == "통합인증"
        assert name_keyword(["업무의 스토리지"]) is None, "조사를 뗀 일반어도 뺀다"

    def test_short_remainder_keeps_particle(self):
        assert name_keyword(["메가"]) == "메가", "떼고 남은 길이가 2자 미만이면 그대로"
        assert name_keyword(["콜에 서버"]) == "콜에"

    @pytest.mark.parametrize(
        "word", ["게이트웨이", "카카오페이", "메트로", "그린뱅크와", "리눅스도"],
    )
    def test_ambiguous_one_char_particles_kept(self, word):
        """이·가·도·로·와·과는 이름 끝 음절로 흔해 떼지 않는다(검증 150 Low 13)."""
        assert name_keyword([word]) == word

    @pytest.mark.parametrize("word,expected", [
        ("통합인증의", "통합인증"), ("통합인증에", "통합인증"), ("통합인증은", "통합인증"),
        ("통합인증을", "통합인증"), ("통합인증에서", "통합인증"), ("통합인증으로", "통합인증"),
    ])
    def test_kept_particles_stripped(self, word, expected):
        assert name_keyword([word]) == expected

    def test_wildcards_stripped(self):
        """등호에 `'%값%'`을 넣은 리터럴 — 와일드카드를 걷어낸다(검증 150 Low 12)."""
        sql = _sql("a.`업무명` = '%그린뱅크%'")
        assert name_keyword(find_name_literals(sql, COLS)) == "그린뱅크"
        assert name_keyword(["%통합인증_ 업무%"]) == "통합인증"
        assert name_keyword(["AP_GW"]) == "AP_GW", "가운데 밑줄은 그대로"
        assert name_keyword(["% 업무 _"]) is None

    def test_longest_across_literals_first_wins_tie(self):
        assert name_keyword(["인증 업무", "통합인증의 시스템"]) == "통합인증"
        assert name_keyword(["가나다 라마바"]) == "가나다"


# ──────────────────────────────────────────────
# 힌트 문구
# ──────────────────────────────────────────────

class TestHint:
    def test_without_keyword_is_legacy(self):
        assert build_name_match_hint(["`업무명`"]) == LEGACY_HINT
        assert build_name_match_hint(["`업무명`"], None) == LEGACY_HINT

    def test_118_hint_carries_keyword(self, knowledge_root):
        hint = name_match_retry_hint(_state(SQL_118), max_retry=3)
        assert hint is not None and hint.startswith(LEGACY_HINT)
        assert "「통합인증」" in hint and "LIKE '%통합인증%'" in hint
        assert "통합인증 업무 스토리지" not in hint

    def test_generic_only_bit_identical(self, knowledge_root):
        assert name_match_retry_hint(_state(SQL_GENERIC), max_retry=3) == LEGACY_HINT

    def test_in_literal_hint(self, knowledge_root):
        hint = name_match_retry_hint(_state(SQL_IN), max_retry=3)
        assert hint is not None and "LIKE '%통합인증%'" in hint

    def test_polestar_unchanged(self, knowledge_root):
        sql = ("SELECT r.hostname FROM polestar.cmm_resource r "
               "WHERE r.hostname = 'web-01 서버' LIMIT 100")
        assert name_match_retry_hint(_state(sql, db_id="polestar"), max_retry=3) is None
        assert find_name_literals(sql, {}) == []

    def test_log_has_column_only(self, knowledge_root, caplog):
        with caplog.at_level(logging.DEBUG, logger=nmr.__name__):
            assert name_match_retry_hint(_state(SQL_118), max_retry=3) is not None
            st = _state(SQL_118)
            st["retry_count"] = 3
            assert name_match_retry_hint(st, max_retry=3) is None
        text = caplog.text
        assert "`업무명`" in text
        assert "통합인증" not in text


# ──────────────────────────────────────────────
# 로그 — 이름 칸 힌트는 칸 이름만 (검증 150 Low 14)
# ──────────────────────────────────────────────

class TestLoggableReason:
    def test_hint_reduced_to_columns(self):
        hint = build_name_match_hint(["`업무명`", "a.`용도내용`"], "통합인증")
        assert loggable_reason(hint) == "이름 칸 부분 일치 힌트(칸=`업무명`·a.`용도내용`)"
        assert loggable_reason(LEGACY_HINT) == "이름 칸 부분 일치 힌트(칸=`업무명`)"

    @pytest.mark.parametrize("other", [None, "", "SQL 실행 오류: 칸 없음", 3])
    def test_other_reasons_unchanged(self, other):
        assert loggable_reason(other) is other

    async def test_tier2_rollback_log_has_no_literal(self, monkeypatch, knowledge_root, caplog):
        """2단 루프 — 힌트를 실은 뒤 재생성 전에 마감으로 멈춰도 되돌림 로그에 후보가 없다."""
        sqls = iter([SQL_118, "SELECT 1"])
        calls = {"n": 0}

        def _has_time(*_a, **_k):
            calls["n"] += 1
            return calls["n"] < 4  # 스키마 전 · 첫 생성 전 · 힌트 판정 통과 → 재생성 전 마감

        async def _schema(state, llm=None, app_config=None):
            return {"schema_info": {"tables": {}}}

        async def _gen(state, llm=None, app_config=None):
            return {"generated_sql": next(sqls), "error_message": None}

        async def _validate(state, app_config=None):
            return {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None},
                    "error_message": None}

        async def _exec(state, app_config=None):
            sql = state["generated_sql"]
            return {"query_results": [], "error_message": None,
                    "query_attempts": [{"sql": sql, "success": True, "row_count": 0}]}

        monkeypatch.setattr(sub, "has_time_for", _has_time)
        monkeypatch.setattr(sub, "schema_analyzer", _schema)
        monkeypatch.setattr(sub, "query_generator", _gen)
        monkeypatch.setattr(sub, "query_validator", _validate)
        monkeypatch.setattr(sub, "query_executor", _exec)
        cfg = SimpleNamespace(
            query=SimpleNamespace(max_retry_count=3, default_limit=1000),
            server=SimpleNamespace(answer_reserve_sec=0.0),
        )
        st = {
            "user_query": "통합인증 업무 스토리지", "retry_count": 0, "error_message": None,
            "query_results": [], "query_attempts": [], "generated_sql": "",
            "active_db_id": DB, "is_multi_db": False, "request_deadline": None,
        }
        with caplog.at_level(logging.INFO):
            out = await sub._run_single_db_pipeline(st, MagicMock(), cfg)
        assert out["generated_sql"] == SQL_118 and out["query_results"] == []
        rollback = [r.getMessage() for r in caplog.records if "되돌림" in r.getMessage()]
        assert len(rollback) == 1
        assert "칸=`업무명`" in rollback[0]
        assert "통합인증" not in caplog.text
