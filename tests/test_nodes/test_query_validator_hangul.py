"""query_validator 한글 토큰 잔존 가드 테스트 (D-087).

폐쇄망 실측(2026-07-20, EX 평가 yd-006 2회 재현): LLM이 생성 SQL의 구조 영역에
자연어 조각("해당", "현재" — 실행마다 토큰 가변)을 남겨 DB 구문 오류로 실행 실패.
프롬프트로는 재발을 못 막는 비결정 오류라, validator가 결정적으로 검출해 재생성
루프를 유도한다. 따옴표 안 한글(별칭 "CPU 평균", 리터럴 '서울')은 정당하므로 통과.
"""

import pytest

from src.config import AppConfig
from src.nodes.query_validator import _find_bare_hangul_tokens, query_validator


@pytest.fixture
def schema_info() -> dict:
    return {
        "tables": {
            "polestar.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False,
                     "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "name", "type": "varchar(255)", "nullable": True,
                     "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "hostname", "type": "varchar(255)", "nullable": True,
                     "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "resource_type", "type": "varchar(100)", "nullable": False,
                     "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "dtime", "type": "timestamp", "nullable": True,
                     "primary_key": False, "foreign_key": False, "references": None},
                ],
            },
        },
    }


def _state(sql: str, schema_info: dict) -> dict:
    return {
        "generated_sql": sql,
        "schema_info": schema_info,
        "user_query": "테스트 질의",
    }


class TestFindBareHangulTokens:
    def test_detects_bare_demonstrative(self):
        # 실측 사례: WHERE 절에 지시어 잔존
        sql = "SELECT r.name FROM polestar.cmm_resource r WHERE r.name = 해당 서버"
        tokens = _find_bare_hangul_tokens(sql)
        assert "해당" in tokens and "서버" in tokens

    def test_detects_bare_token_variant(self):
        # 실측 2회차: 토큰이 "현재"로 가변
        sql = "SELECT * FROM polestar.cmm_resource WHERE 현재 dtime IS NULL"
        assert "현재" in _find_bare_hangul_tokens(sql)

    def test_quoted_alias_and_literal_are_allowed(self):
        # 폼필 별칭("CPU 평균")·값 리터럴('서울')·'' 이스케이프는 정당
        sql = (
            "SELECT AVG(s.avg_val) AS \"CPU 평균\" FROM polestar.cmm_resource r "
            "WHERE r.name = '서울''센터' AND r.dtime IS NULL"
        )
        assert _find_bare_hangul_tokens(sql) == []

    def test_comments_are_stripped(self):
        sql = (
            "SELECT id FROM polestar.cmm_resource -- 서버 목록 조회\n"
            "/* 비정상 필터 */ WHERE dtime IS NULL"
        )
        assert _find_bare_hangul_tokens(sql) == []


class TestValidatorHangulGuard:
    @pytest.mark.asyncio
    async def test_bare_hangul_fails_validation(self, schema_info):
        sql = "SELECT r.name FROM polestar.cmm_resource r WHERE r.hostname = 해당"
        result = await query_validator(_state(sql, schema_info), app_config=AppConfig())
        assert result["validation_result"]["passed"] is False
        assert "한글" in result["validation_result"]["reason"]

    @pytest.mark.asyncio
    async def test_quoted_hangul_passes(self, schema_info):
        sql = (
            "SELECT r.name AS \"서버 이름\" FROM polestar.cmm_resource r "
            "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL LIMIT 10"
        )
        result = await query_validator(_state(sql, schema_info), app_config=AppConfig())
        assert result["validation_result"]["passed"] is True
