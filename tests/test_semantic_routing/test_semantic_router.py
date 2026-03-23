"""semantic_router 노드 테스트 (v2 - LLM 전용).

키워드 기반 분류가 제거되었으므로, LLM 전용 라우팅과
사용자 직접 DB 지정, 동적 프롬프트 생성 등을 테스트한다.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import AppConfig, MultiDBConfig
from src.routing.domain_config import DB_DOMAINS, DBDomainConfig
from src.routing.semantic_router import (
    MIN_RELEVANCE_SCORE,
    _build_router_prompt,
    _llm_classify,
    semantic_router,
)
from src.utils.json_extract import extract_json_from_response
from src.state import create_initial_state


def _make_config(**overrides) -> AppConfig:
    """테스트용 AppConfig를 생성한다."""
    # 기본적으로 4개 DB를 활성화. overrides로 특정 DB만 활성화 가능.
    default_ids = ["polestar", "cloud_portal", "itsm", "itam"]
    if overrides:
        active_ids = [k for k, v in overrides.items() if v]
    else:
        active_ids = default_ids
    multi_db = MultiDBConfig(
        active_db_ids_csv=",".join(active_ids),
    )
    return AppConfig(
        multi_db=multi_db,
        enable_semantic_routing=True,
    )


def _make_llm_response(databases: list[dict]) -> MagicMock:
    """LLM 응답 Mock을 생성한다."""
    response = MagicMock()
    response.content = json.dumps({"databases": databases}, ensure_ascii=False)
    return response


class TestKeywordFunctionsRemoved:
    """키워드 기반 함수가 완전히 제거되었는지 검증한다."""

    def test_no_keyword_match_function(self):
        """_keyword_match 함수가 존재하지 않는다."""
        import src.routing.semantic_router as module
        assert not hasattr(module, "_keyword_match")

    def test_no_needs_llm_fallback_function(self):
        """_needs_llm_fallback 함수가 존재하지 않는다."""
        import src.routing.semantic_router as module
        assert not hasattr(module, "_needs_llm_fallback")

    def test_no_keyword_confidence_threshold(self):
        """KEYWORD_CONFIDENCE_THRESHOLD 상수가 존재하지 않는다."""
        import src.routing.semantic_router as module
        assert not hasattr(module, "KEYWORD_CONFIDENCE_THRESHOLD")


class TestDomainConfigNoKeywords:
    """DBDomainConfig에서 keywords 필드가 제거되었는지 검증한다."""

    def test_no_keywords_field(self):
        """DBDomainConfig에 keywords 필드가 없다."""
        assert not hasattr(DB_DOMAINS[0], "keywords")

    def test_has_aliases_field(self):
        """DBDomainConfig에 aliases 필드가 있다."""
        for domain in DB_DOMAINS:
            assert hasattr(domain, "aliases")
            assert isinstance(domain.aliases, list)

    def test_each_domain_has_aliases(self):
        """각 도메인에 별칭이 정의되어 있다."""
        for domain in DB_DOMAINS:
            assert len(domain.aliases) > 0, f"{domain.db_id}에 aliases가 없습니다."


class TestBuildRouterPrompt:
    """_build_router_prompt 함수 테스트."""

    def test_includes_all_active_domains(self):
        """모든 활성 도메인이 프롬프트에 포함된다."""
        prompt = _build_router_prompt(DB_DOMAINS)
        for domain in DB_DOMAINS:
            assert domain.display_name in prompt
            assert domain.db_id in prompt
            assert domain.description in prompt

    def test_includes_aliases(self):
        """별칭 정보가 프롬프트에 포함된다."""
        prompt = _build_router_prompt(DB_DOMAINS)
        polestar = next(d for d in DB_DOMAINS if d.db_id == "polestar")
        for alias in polestar.aliases:
            assert alias in prompt

    def test_subset_domains(self):
        """일부 도메인만 전달하면 DB 목록 영역에 해당 도메인만 포함된다."""
        subset = [d for d in DB_DOMAINS if d.db_id in ("polestar", "itsm")]
        prompt = _build_router_prompt(subset)
        # DB 목록 영역에서 확인 (## 사용 가능한 데이터베이스 ~ ## 사용자 직접 DB 지정 규칙 사이)
        db_list_section = prompt.split("## 사용 가능한 데이터베이스")[1].split("## 사용자 직접 DB 지정")[0]
        assert "Polestar DB" in db_list_section
        assert "ITSM DB" in db_list_section
        assert "Cloud Portal DB" not in db_list_section
        assert "ITAM DB" not in db_list_section


class TestExtractJsonFromResponse:
    """extract_json_from_response 함수 테스트."""

    def test_json_code_block(self):
        """```json ... ``` 블록에서 JSON을 추출한다."""
        content = '```json\n{"databases": [{"db_id": "polestar"}]}\n```'
        result = extract_json_from_response(content)
        assert result is not None
        assert result["databases"][0]["db_id"] == "polestar"

    def test_plain_json(self):
        """순수 JSON 문자열을 파싱한다."""
        content = '{"databases": [{"db_id": "itsm"}]}'
        result = extract_json_from_response(content)
        assert result is not None
        assert result["databases"][0]["db_id"] == "itsm"

    def test_invalid_json(self):
        """잘못된 JSON은 None을 반환한다."""
        result = extract_json_from_response("이것은 JSON이 아닙니다")
        assert result is None


class TestLLMClassify:
    """_llm_classify 함수 테스트."""

    @pytest.mark.asyncio
    async def test_single_db_classification(self):
        """단일 DB 분류 결과를 반환한다."""
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "polestar",
                "relevance_score": 0.9,
                "reason": "서버 CPU 사용률 조회",
                "sub_query_context": "CPU 사용률 80% 이상 서버 목록",
                "user_specified": False,
            }
        ])

        results = await _llm_classify(llm, "CPU 사용률이 80% 이상인 서버", DB_DOMAINS)
        assert len(results) == 1
        assert results[0]["db_id"] == "polestar"
        assert results[0]["relevance_score"] == 0.9
        assert results[0]["user_specified"] is False

    @pytest.mark.asyncio
    async def test_multi_db_classification(self):
        """멀티 DB 분류 결과를 반환한다."""
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "polestar",
                "relevance_score": 0.9,
                "reason": "서버 사양 조회",
                "sub_query_context": "서버 CPU, Memory 사양 조회",
                "user_specified": False,
            },
            {
                "db_id": "cloud_portal",
                "relevance_score": 0.8,
                "reason": "VM 정보 조회",
                "sub_query_context": "해당 서버의 VM 정보 조회",
                "user_specified": False,
            },
        ])

        results = await _llm_classify(
            llm,
            "서버 사양과 해당 서버의 VM 정보를 보여줘",
            DB_DOMAINS,
        )
        assert len(results) == 2
        db_ids = [r["db_id"] for r in results]
        assert "polestar" in db_ids
        assert "cloud_portal" in db_ids

    @pytest.mark.asyncio
    async def test_user_specified_db(self):
        """사용자 직접 DB 지정 결과를 반환한다."""
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "polestar",
                "relevance_score": 1.0,
                "reason": "사용자가 polestar DB를 직접 지정",
                "sub_query_context": "서버 목록 조회",
                "user_specified": True,
            }
        ])

        results = await _llm_classify(llm, "polestar에서 서버 목록 조회해줘", DB_DOMAINS)
        assert len(results) == 1
        assert results[0]["db_id"] == "polestar"
        assert results[0]["user_specified"] is True
        assert results[0]["relevance_score"] == 1.0

    @pytest.mark.asyncio
    async def test_filters_invalid_db_ids(self):
        """유효하지 않은 DB ID는 필터링된다."""
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "nonexistent_db",
                "relevance_score": 0.9,
                "reason": "테스트",
                "sub_query_context": "테스트",
                "user_specified": False,
            },
            {
                "db_id": "polestar",
                "relevance_score": 0.8,
                "reason": "서버 조회",
                "sub_query_context": "서버 목록",
                "user_specified": False,
            },
        ])

        results = await _llm_classify(llm, "테스트 질의", DB_DOMAINS)
        assert len(results) == 1
        assert results[0]["db_id"] == "polestar"

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """LLM이 빈 응답을 반환하면 빈 리스트를 반환한다."""
        llm = AsyncMock()
        response = MagicMock()
        response.content = "잘 모르겠습니다"
        llm.ainvoke.return_value = response

        results = await _llm_classify(llm, "날씨 알려줘", DB_DOMAINS)
        assert len(results) == 0


class TestSemanticRouter:
    """semantic_router 노드 함수 통합 테스트."""

    @pytest.mark.asyncio
    async def test_legacy_mode_no_active_dbs(self):
        """활성 DB가 없으면 레거시 모드로 동작한다."""
        config = AppConfig(multi_db=MultiDBConfig())
        llm = AsyncMock()
        state = create_initial_state(user_query="테스트 질의")

        result = await semantic_router(state, llm=llm, app_config=config)

        assert result["active_db_id"] == "default"
        assert result["is_multi_db"] is False
        assert result["user_specified_db"] is None
        assert len(result["target_databases"]) == 1

    @pytest.mark.asyncio
    async def test_single_db_routing(self):
        """단일 DB 라우팅 결과를 반환한다."""
        config = _make_config()
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "polestar",
                "relevance_score": 0.9,
                "reason": "서버 CPU 조회",
                "sub_query_context": "CPU 사용률 조회",
                "user_specified": False,
            }
        ])
        state = create_initial_state(user_query="CPU 사용률 현황")

        result = await semantic_router(state, llm=llm, app_config=config)

        assert result["active_db_id"] == "polestar"
        assert result["is_multi_db"] is False
        assert result["user_specified_db"] is None
        assert len(result["target_databases"]) == 1

    @pytest.mark.asyncio
    async def test_multi_db_routing(self):
        """멀티 DB 라우팅 결과를 반환한다."""
        config = _make_config()
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "polestar",
                "relevance_score": 0.9,
                "reason": "서버 사양",
                "sub_query_context": "서버 CPU 사양",
                "user_specified": False,
            },
            {
                "db_id": "cloud_portal",
                "relevance_score": 0.8,
                "reason": "VM 정보",
                "sub_query_context": "VM 목록",
                "user_specified": False,
            },
        ])
        state = create_initial_state(user_query="서버 사양과 VM 정보")

        result = await semantic_router(state, llm=llm, app_config=config)

        assert result["is_multi_db"] is True
        assert len(result["target_databases"]) == 2

    @pytest.mark.asyncio
    async def test_user_specified_db_in_result(self):
        """사용자 직접 DB 지정이 결과에 반영된다."""
        config = _make_config()
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "itsm",
                "relevance_score": 1.0,
                "reason": "사용자 지정",
                "sub_query_context": "장애 건수 조회",
                "user_specified": True,
            }
        ])
        state = create_initial_state(user_query="ITSM에서 장애 건수 알려줘")

        result = await semantic_router(state, llm=llm, app_config=config)

        assert result["user_specified_db"] == "itsm"
        assert result["target_databases"][0]["user_specified"] is True

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        """LLM 호출 실패 시 첫 번째 활성 DB로 폴백한다."""
        config = _make_config()
        llm = AsyncMock()
        llm.ainvoke.side_effect = Exception("LLM 호출 실패")
        state = create_initial_state(user_query="테스트 질의")

        result = await semantic_router(state, llm=llm, app_config=config)

        assert result["active_db_id"] == "polestar"
        assert result["is_multi_db"] is False
        assert len(result["target_databases"]) == 1

    @pytest.mark.asyncio
    async def test_low_score_filtered(self):
        """최소 관련도 이하의 결과는 필터링된다."""
        config = _make_config()
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "polestar",
                "relevance_score": 0.1,
                "reason": "약한 관련",
                "sub_query_context": "테스트",
                "user_specified": False,
            }
        ])
        state = create_initial_state(user_query="날씨 알려줘")

        result = await semantic_router(state, llm=llm, app_config=config)

        # 관련도 0.1은 MIN_RELEVANCE_SCORE(0.3) 미만이므로 필터링되고
        # 기본 DB가 사용된다
        assert len(result["target_databases"]) == 1
        assert result["target_databases"][0]["relevance_score"] == 0.5

    @pytest.mark.asyncio
    async def test_results_sorted_by_relevance(self):
        """결과가 관련도 점수 내림차순으로 정렬된다."""
        config = _make_config()
        llm = AsyncMock()
        llm.ainvoke.return_value = _make_llm_response([
            {
                "db_id": "cloud_portal",
                "relevance_score": 0.6,
                "reason": "VM",
                "sub_query_context": "VM 조회",
                "user_specified": False,
            },
            {
                "db_id": "polestar",
                "relevance_score": 0.9,
                "reason": "서버",
                "sub_query_context": "서버 조회",
                "user_specified": False,
            },
        ])
        state = create_initial_state(user_query="서버와 VM 정보")

        result = await semantic_router(state, llm=llm, app_config=config)

        assert result["target_databases"][0]["db_id"] == "polestar"
        assert result["target_databases"][1]["db_id"] == "cloud_portal"
