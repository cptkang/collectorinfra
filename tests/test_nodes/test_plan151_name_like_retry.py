"""plans/151 W4 — 이름 칸 LIKE 0행 재생성 (F4 · G-4 (a) · D-297 부기).

6회차 101은 이름 칸 두 곳에 질문 문구 전체(일반어 「서비스」 포함)를 `LIKE`로 걸어 0행이었고,
149 W3·150 W5 판정은 등호·`IN`만 보아 재생성하지 않았다. 이제 이름 칸 LIKE의 문구(양끝
와일드카드를 뗀 것)가 핵심어 후보와 다르면 같은 힌트로 1회 다시 만든다. 이미 핵심어만 건 LIKE
(5회차 118 재생성 꼴)는 발동하지 않는다. SQL은 값 없는 재구성(테이블·컬럼 이름만)이고, LLM·DB는
쓰지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

import src.nodes.name_match_retry as nmr
import src.orchestration.subagents as sub
from src.nodes.name_match_retry import (
    NAME_MATCH_CHECKED_KEY,
    build_name_match_hint,
    find_name_equalities,
    find_name_likes,
    loggable_reason,
    name_match_columns,
    name_match_retry_hint,
)
from tests.test_nodes.test_plan149_name_column_retry import _patch_tier2, _sub_cfg, _tier2_input

DB = "itam"

DESCRIPTIONS = {
    "tcdmsif72.그룹경로내용": "서비스 그룹 경로 자유 기재 — 핵심어로 부분 일치 조회한다.",
    "tcdmsif72.용도내용": "서버 용도 자유 기재 — 핵심어로 부분 일치 조회한다.",
    "tcdmsif72.서버호스트명": "서버 호스트 이름(정의 관계 키).",
}
COLS = name_match_columns(DESCRIPTIONS)


def _sql(where: str) -> str:
    return f"SELECT s.`서버호스트명`\nFROM `tcdmsif72` AS s\nWHERE {where}\nLIMIT 10000;"


#: 101 꼴 — 이름 칸 두 곳에 일반어가 섞인 문구 전체.
SQL_101 = _sql("s.`그룹경로내용` LIKE '%통합인증 서비스%' OR s.`용도내용` LIKE '%통합인증 서비스%'")
#: 118 재생성 꼴 — 핵심어만 건 LIKE.
SQL_KEYWORD = _sql("s.`그룹경로내용` LIKE '%통합인증%' OR s.`용도내용` LIKE '%통합인증%'")


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


def _attempt(sql: str, rows: int) -> dict[str, Any]:
    return {"sql": sql, "success": True, "error": None, "row_count": rows,
            "execution_time_ms": 1.0}


def _state(sql: str, *, db_id: str = DB, rows: int = 0) -> dict:
    return {
        "query_results": [{"h": i} for i in range(rows)],
        "generated_sql": sql,
        "active_db_id": db_id,
        "is_multi_db": False,
        "retry_count": 0,
        "query_attempts": [_attempt(sql, rows)],
    }


# ──────────────────────────────────────────────
# SQL 판정(순수 함수)
# ──────────────────────────────────────────────

class TestFindNameLikes:
    def test_101_generic_word_mixed(self):
        assert find_name_likes(SQL_101, COLS) == [
            ("`그룹경로내용`", "%통합인증 서비스%"), ("`용도내용`", "%통합인증 서비스%"),
        ]

    def test_keyword_only_like_not_a_hit(self):
        assert find_name_likes(SQL_KEYWORD, COLS) == []
        assert find_name_likes(_sql("s.`용도내용` LIKE '% 통합인증_'"), COLS) == []

    def test_generic_only_has_no_keyword(self):
        assert find_name_likes(_sql("s.`용도내용` LIKE '%업무 서비스%'"), COLS) == []

    def test_particle_counts_as_mixed(self):
        assert find_name_likes(_sql("s.`용도내용` LIKE '%통합인증의%'"), COLS) == [
            ("`용도내용`", "%통합인증의%"),
        ]

    def test_non_name_column(self):
        assert find_name_likes(_sql("s.`서버호스트명` LIKE '%통합인증 서비스%'"), COLS) == []

    @pytest.mark.parametrize("where,expected", [
        ("s.용도내용 ilike '%통합인증 서비스%'", ["용도내용"]),
        ("용도내용 like '%통합인증 서비스%'", ["용도내용"]),
        ("`tcdmsif72`.\"용도내용\" LIKE '%통합인증 서비스%'", ["\"용도내용\""]),
        ("s.`용도내용` NOT LIKE '%통합인증 서비스%'", []),
        ("s.`용도내용` LIKE ''", []),
        ("s.`비고` = '용도내용 LIKE ''%통합인증 서비스%'''", []),
    ])
    def test_boundaries(self, where, expected):
        assert [c for c, _ in find_name_likes(_sql(where), COLS)] == expected

    def test_alias_of_other_table(self):
        sql = ("SELECT 1 FROM `tcdmsif72` s JOIN `tcdmsif52` b ON b.`호스트명` = s.`서버호스트명` "
               "WHERE b.`용도내용` LIKE '%통합인증 서비스%'")
        assert find_name_likes(sql, COLS) == []

    def test_case_when_and_comment_excluded(self):
        sql = ("SELECT CASE WHEN s.`용도내용` LIKE '%통합인증 서비스%' THEN 1 END "
               "FROM `tcdmsif72` s -- s.`그룹경로내용` LIKE '%통합인증 서비스%'\n"
               "WHERE 1 = 1")
        assert find_name_likes(sql, COLS) == []

    def test_equalities_unchanged_by_like(self):
        assert find_name_equalities(SQL_101, COLS) == []


# ──────────────────────────────────────────────
# 발동 판정 · 힌트
# ──────────────────────────────────────────────

class TestRetryHint:
    def test_101_fires_with_keyword_hint(self, knowledge_root):
        hint = name_match_retry_hint(_state(SQL_101), max_retry=3)
        assert hint == build_name_match_hint(
            ["`그룹경로내용`", "`용도내용`"], "통합인증", like_only=True,
        )
        assert hint.startswith(
            "이전 SQL은 0행이었습니다. 이름 칸 `그룹경로내용`·`용도내용`에 LIKE로 "
            "값 문구를 그대로 걸었습니다 — 이 칸은 컬럼 설명이 부분 일치로 표시한 이름 칸입니다. "
        )
        assert "등호(=)·IN" not in hint
        assert "LIKE '%통합인증%'" in hint

    def test_keyword_only_like_no_retry(self, knowledge_root):
        assert name_match_retry_hint(_state(SQL_KEYWORD), max_retry=3) is None

    def test_rows_present_no_retry(self, knowledge_root):
        assert name_match_retry_hint(_state(SQL_101, rows=3), max_retry=3) is None

    def test_non_name_column_no_retry(self, knowledge_root):
        sql = _sql("s.`서버호스트명` LIKE '%통합인증 서비스%'")
        assert name_match_retry_hint(_state(sql), max_retry=3) is None

    def test_polestar_without_knowledge_unchanged(self, knowledge_root):
        sql = ("SELECT r.hostname FROM polestar.cmm_resource r "
               "WHERE r.description LIKE '%통합인증 서비스%' LIMIT 100")
        assert name_match_retry_hint(_state(sql, db_id="polestar"), max_retry=3) is None
        assert find_name_likes(sql, {}) == []

    def test_mixed_equality_and_like(self, knowledge_root):
        sql = _sql("s.`그룹경로내용` = '인증' AND s.`용도내용` LIKE '%통합인증 서비스%'")
        hint = name_match_retry_hint(_state(sql), max_retry=3)
        assert hint == build_name_match_hint(["`그룹경로내용`", "`용도내용`"], "통합인증")
        assert hint.startswith(
            "이전 SQL은 0행이었습니다. 이름 칸 `그룹경로내용`·`용도내용`에 등호(=)·IN으로 "
            "값을 그대로 걸었습니다 — "
        ), "등호·IN 칸이 하나라도 있으면 종전 문구"

    def test_like_only_differs_from_legacy_only_in_first_sentence(self):
        legacy = build_name_match_hint(["`용도내용`"], "통합인증")
        like = build_name_match_hint(["`용도내용`"], "통합인증", like_only=True)
        assert like.replace("에 LIKE로 값 문구를 그대로", "에 등호(=)·IN으로 값을 그대로") == legacy

    @pytest.mark.parametrize("like_only", [False, True])
    def test_loggable_reason_keeps_columns_only(self, like_only):
        hint = build_name_match_hint(["`그룹경로내용`", "`용도내용`"], "통합인증",
                                     like_only=like_only)
        assert loggable_reason(hint) == "이름 칸 부분 일치 힌트(칸=`그룹경로내용`·`용도내용`)"


# ──────────────────────────────────────────────
# 2단 task 루프 — 1회 상한(재생성 LIKE가 핵심어면 멈춘다)
# ──────────────────────────────────────────────

class TestTier2Loop:
    async def test_101_regenerates_once(self, monkeypatch, knowledge_root):
        seen = _patch_tier2(monkeypatch, [SQL_101, SQL_KEYWORD, SQL_101], {SQL_KEYWORD: 5})
        out = await sub._run_single_db_pipeline(_tier2_input(), MagicMock(), _sub_cfg())
        assert len(seen) == 2 and "LIKE '%통합인증%'" in str(seen[1])
        assert out["generated_sql"] == SQL_KEYWORD and len(out["query_results"]) == 5
        assert out[NAME_MATCH_CHECKED_KEY] is True

    async def test_regen_zero_rows_does_not_loop(self, monkeypatch, knowledge_root):
        seen = _patch_tier2(monkeypatch, [SQL_101, SQL_101, SQL_101], {})
        out = await sub._run_single_db_pipeline(_tier2_input(), MagicMock(), _sub_cfg())
        assert len(seen) == 2
        assert out["query_results"] == []

    async def test_keyword_only_like_no_regen(self, monkeypatch, knowledge_root):
        seen = _patch_tier2(monkeypatch, [SQL_KEYWORD, SQL_101], {})
        out = await sub._run_single_db_pipeline(_tier2_input(), MagicMock(), _sub_cfg())
        assert len(seen) == 1 and out["generated_sql"] == SQL_KEYWORD
