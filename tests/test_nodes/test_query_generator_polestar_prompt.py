"""Plan 34: Polestar 도메인별 쿼리 생성 시스템 프롬프트 검증 테스트.

_build_system_prompt()의 설정 기반 Polestar 전용 프롬프트 선택 로직을 검증한다.
LLM 호출 없이 프롬프트 문자열 생성만 테스트한다.
"""

import pytest

from src.nodes.query_generator import _build_system_prompt
from src.prompts.query_generator import POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE


# ---------------------------------------------------------------------------
# 공통 헬퍼: 최소 schema_info
# ---------------------------------------------------------------------------

def _minimal_schema_info() -> dict:
    """테스트용 최소 스키마 정보를 반환한다."""
    return {"tables": {}}


# ---------------------------------------------------------------------------
# 테스트 1~4: 설정 기반 프롬프트 선택 로직
# ---------------------------------------------------------------------------

class TestPolestarPromptSelection:
    """_build_system_prompt()의 Polestar 전용 프롬프트 선택 로직을 검증한다."""

    def test_polestar_db_id_matches_active_db_id(self):
        """polestar_db_id="polestar" + active_db_id="polestar" 이면
        Polestar 전용 프롬프트가 사용되어야 한다."""
        result = _build_system_prompt(
            schema_info=_minimal_schema_info(),
            default_limit=1000,
            active_db_id="polestar",
            polestar_db_id="polestar",
        )
        # Polestar 전용 프롬프트의 핵심 키워드 확인
        assert "POLESTAR 인프라 모니터링 DB" in result
        # 범용 프롬프트의 키워드는 포함되지 않아야 함
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" not in result

    def test_polestar_db_id_does_not_match_active_db_id(self):
        """polestar_db_id="polestar" + active_db_id="cloud_portal" 이면
        범용 프롬프트가 사용되어야 한다."""
        result = _build_system_prompt(
            schema_info=_minimal_schema_info(),
            default_limit=1000,
            active_db_id="cloud_portal",
            polestar_db_id="polestar",
        )
        # 범용 프롬프트의 키워드 확인
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" in result
        # Polestar 전용 키워드는 포함되지 않아야 함
        assert "POLESTAR 인프라 모니터링 DB" not in result

    def test_polestar_db_id_empty_uses_generic(self):
        """polestar_db_id="" (미설정) + active_db_id="polestar" 이면
        범용 프롬프트가 사용되어야 한다 (전용 프롬프트 비활성화)."""
        result = _build_system_prompt(
            schema_info=_minimal_schema_info(),
            default_limit=1000,
            active_db_id="polestar",
            polestar_db_id=None,  # .env에서 POLESTAR_DB_ID="" -> app_config.polestar_db_id or None -> None
        )
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" in result
        assert "POLESTAR 인프라 모니터링 DB" not in result

    def test_polestar_db_id_renamed_matches(self):
        """polestar_db_id="polestar_prod" + active_db_id="polestar_prod" 이면
        Polestar 전용 프롬프트가 사용되어야 한다 (DB명 변경 대응)."""
        result = _build_system_prompt(
            schema_info=_minimal_schema_info(),
            default_limit=1000,
            active_db_id="polestar_prod",
            polestar_db_id="polestar_prod",
        )
        assert "POLESTAR 인프라 모니터링 DB" in result
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" not in result


# ---------------------------------------------------------------------------
# 테스트 5: Polestar 프롬프트 상수에 핵심 규칙 키워드 포함 여부
# ---------------------------------------------------------------------------

class TestPolestarPromptContent:
    """POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE 상수의 핵심 규칙 키워드를 검증한다."""

    def test_contains_hallucination_prohibition(self):
        """Hallucination 금지 지시가 포함되어야 한다."""
        assert "Hallucination" in POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

    def test_contains_join_relation(self):
        """hostname 기반 값 조인 규칙이 포함되어야 한다."""
        assert (
            "R.HOSTNAME = P_HOST.STRINGVALUE_SHORT"
            in POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
        )

    def test_contains_is_lob_handling(self):
        """IS_LOB 분기 규칙이 포함되어야 한다."""
        assert "IS_LOB" in POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

    def test_contains_eav_pivot_pattern(self):
        """MAX(CASE WHEN 피벗 패턴이 포함되어야 한다."""
        assert "MAX(CASE WHEN" in POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

    def test_contains_format_variables(self):
        """범용 프롬프트와 동일한 포맷 변수가 존재해야 한다
        (기존 _build_system_prompt() 호환)."""
        for var in ("{schema}", "{structure_guide}", "{default_limit}", "{db_engine_hint}"):
            assert var in POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE, (
                f"Polestar 프롬프트에 포맷 변수 {var}가 누락됨"
            )


# ---------------------------------------------------------------------------
# 테스트 보조: 프롬프트 포맷팅이 에러 없이 완료되는지
# ---------------------------------------------------------------------------

class TestPolestarPromptFormatting:
    """Polestar 프롬프트 선택 후 .format() 호출이 정상 동작하는지 검증한다."""

    def test_format_with_schema_and_limit(self):
        """schema_info에 테이블이 있어도 Polestar 프롬프트가 정상 포맷된다."""
        schema_info = {
            "tables": {
                "polestar.cmm_resource": {
                    "columns": [
                        {"name": "id", "type": "BIGINT", "primary_key": True},
                        {"name": "hostname", "type": "VARCHAR(255)"},
                    ],
                },
            },
        }
        result = _build_system_prompt(
            schema_info=schema_info,
            default_limit=500,
            active_db_id="polestar",
            polestar_db_id="polestar",
            active_db_engine="db2",
        )
        # 포맷 변수가 치환되었는지 확인
        assert "LIMIT 500" in result or "500" in result
        assert "DB2" in result
        assert "cmm_resource" in result

    def test_format_with_structure_guide(self):
        """_structure_meta가 있을 때 structure_guide가 프롬프트에 삽입된다."""
        schema_info = {
            "tables": {},
            "_structure_meta": {
                "query_guide": "EAV 구조 가이드 텍스트",
                "patterns": [{"type": "eav", "entity_table": "cmm_resource", "config_table": "core_config_prop", "attribute_column": "NAME"}],
            },
        }
        result = _build_system_prompt(
            schema_info=schema_info,
            default_limit=1000,
            active_db_id="polestar",
            polestar_db_id="polestar",
        )
        assert "EAV 구조 가이드 텍스트" in result

    def test_polestar_prompt_none_active_db_id(self):
        """active_db_id=None 이면 범용 프롬프트가 사용된다."""
        result = _build_system_prompt(
            schema_info=_minimal_schema_info(),
            default_limit=1000,
            active_db_id=None,
            polestar_db_id="polestar",
        )
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" in result
        assert "POLESTAR 인프라 모니터링 DB" not in result

    def test_both_none_uses_generic(self):
        """active_db_id=None, polestar_db_id=None 이면 범용 프롬프트가 사용된다."""
        result = _build_system_prompt(
            schema_info=_minimal_schema_info(),
            default_limit=1000,
            active_db_id=None,
            polestar_db_id=None,
        )
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" in result
