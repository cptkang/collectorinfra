"""LLM 응답 SQL 추출 공용 구현 테스트 (D-153).

폐쇄망 실측(2026-08-04): 종전 추출(소문자 ```sql 전용·세미콜론 필수)이 LLM 출력 형식
변형에서 전부 빗나가면 응답 전문(산문)이 SQL로 흘러 "SELECT 문이 아닙니다" 간이 검증
실패 → 멀티 경로 첫 DB(공동존 gp)만 간헐 누락됐다. 실패 형태 3종의 결정적 흡수와
단일/멀티 경로 위임(2벌 중복 해소, D-067 취지)을 고정한다.
"""

from __future__ import annotations

from src.utils.query_gen_common import extract_sql_from_llm_response


class TestFenceVariants:
    """펜스 언어 태그·대소문자 변형 — 종전 구현의 1차 실패 형태."""

    def test_lowercase_sql_fence(self):
        content = "```sql\nSELECT * FROM servers LIMIT 10;\n```"
        assert extract_sql_from_llm_response(content) == "SELECT * FROM servers LIMIT 10;"

    def test_uppercase_sql_fence(self):
        """```SQL(대문자) — 종전엔 미매칭 → 전문 폴백 → 검증 실패."""
        content = "```SQL\nSELECT hostname FROM servers\n```"
        assert extract_sql_from_llm_response(content) == "SELECT hostname FROM servers"

    def test_language_tag_fence(self):
        """```postgresql 언어 태그 — 종전엔 미매칭."""
        content = "```postgresql\nSELECT os_type FROM cmm_resource WHERE dtime IS NULL\n```"
        assert extract_sql_from_llm_response(content).startswith("SELECT os_type")

    def test_untagged_fence_with_select(self):
        content = "```\nSELECT hostname FROM servers LIMIT 5;\n```"
        assert "hostname" in extract_sql_from_llm_response(content)

    def test_untagged_fence_with_cte(self):
        content = "```\nWITH t AS (SELECT 1 AS a) SELECT a FROM t\n```"
        assert extract_sql_from_llm_response(content).startswith("WITH t AS")

    def test_non_sql_fence_skipped_sql_fence_used(self):
        """SQL이 아닌 코드 블록(설명 등)은 건너뛰고 SQL 형태 블록을 채택한다."""
        content = "```\n주의: 아래 쿼리 참고\n```\n```sql\nSELECT 1 FROM t;\n```"
        assert extract_sql_from_llm_response(content) == "SELECT 1 FROM t;"

    def test_fence_with_leading_comment(self):
        """블록이 SQL 주석으로 시작해도 SQL 형태로 인정한다."""
        content = "```sql\n-- 서버 OS 조회\nSELECT os FROM servers;\n```"
        assert extract_sql_from_llm_response(content).endswith("SELECT os FROM servers;")


class TestSemicolonFree:
    """세미콜론 생략 — 종전 구현의 2차 실패 형태(펜스 없음 + ; 없음 → 전문 폴백)."""

    def test_prose_then_sql_without_semicolon(self):
        content = "다음 쿼리로 조회합니다.\nSELECT os_type, os_version FROM cmm_resource WHERE dtime IS NULL"
        result = extract_sql_from_llm_response(content)
        assert result.startswith("SELECT os_type")
        assert "다음 쿼리" not in result

    def test_prose_then_cte_without_semicolon(self):
        content = "쿼리:\nWITH r AS (SELECT id FROM t) SELECT * FROM r"
        assert extract_sql_from_llm_response(content).startswith("WITH r AS")

    def test_trailing_fence_residue_stripped(self):
        """말미 닫는 펜스 잔재는 제거한다."""
        content = "SELECT a FROM t\n```"
        assert extract_sql_from_llm_response(content) == "SELECT a FROM t"

    def test_english_with_prose_not_treated_as_cte(self):
        """영어 산문의 'with the ...'는 CTE 형태가 아니므로 SQL로 오인하지 않는다."""
        content = "I will proceed with the analysis of your data."
        assert extract_sql_from_llm_response(content) == content


class TestExistingSemantics:
    """종전 동작 보존 — 세미콜론 종결 인라인 추출·전문 폴백."""

    def test_prose_then_sql_with_semicolon(self):
        content = "Here is the query: SELECT id FROM servers LIMIT 10;"
        assert extract_sql_from_llm_response(content) == "SELECT id FROM servers LIMIT 10;"

    def test_fallback_returns_full_content(self):
        content = "I cannot generate SQL"
        assert extract_sql_from_llm_response(content) == "I cannot generate SQL"

    def test_empty_and_none_safe(self):
        assert extract_sql_from_llm_response("") == ""
        assert extract_sql_from_llm_response(None) == ""


class TestPathDelegation:
    """단일/멀티 경로가 공용 구현에 위임하는지 고정 (D-067 2벌 중복 재발 방지).

    병합 후 두 경로 모두 utils의 `extract_sql_from_response`(콘텐츠 블록 정규화 +
    강화 추출 엔진 위임)를 임포트해 쓴다 — 동일 객체인지까지 고정한다.
    """

    def test_multi_path_delegates(self):
        from src.nodes.multi_db_executor import extract_sql_from_response as multi_fn
        from src.utils.query_gen_common import extract_sql_from_response as shared_fn

        assert multi_fn is shared_fn
        content = "```SQL\nSELECT 1 FROM t\n```"
        assert multi_fn(content) == "SELECT 1 FROM t"

    def test_single_path_delegates(self):
        from src.nodes.query_generator import extract_sql_from_response as single_fn
        from src.utils.query_gen_common import extract_sql_from_response as shared_fn

        assert single_fn is shared_fn
        content = "```SQL\nSELECT 1 FROM t\n```"
        assert single_fn(content) == "SELECT 1 FROM t"
