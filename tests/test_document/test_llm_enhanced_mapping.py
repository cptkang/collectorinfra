"""LLM 통합 추론 매핑 및 Redis 등록 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.document.field_mapper import (
    MappingResult,
    _register_llm_mappings_to_redis,
    perform_3step_mapping,
)


def _make_cache_manager(redis_available: bool = True) -> MagicMock:
    """테스트용 cache_manager mock을 생성한다."""
    cm = MagicMock()
    cm.redis_available = redis_available
    cm.add_synonyms = AsyncMock(return_value=True)
    cm.remove_synonyms = AsyncMock(return_value=True)

    redis_cache = MagicMock()
    redis_cache.load_eav_name_synonyms = AsyncMock(return_value={})
    redis_cache.save_eav_name_synonyms = AsyncMock(return_value=None)
    redis_cache.add_global_synonym = AsyncMock(return_value=True)
    cm._redis_cache = redis_cache

    return cm


class TestRegisterLlmMappingsToRedis:
    """_register_llm_mappings_to_redis 함수 테스트."""

    @pytest.mark.asyncio
    async def test_register_llm_mappings_to_redis_normal(self) -> None:
        """일반 매핑이 redis_cache.add_global_synonym()으로 글로벌에 등록된다."""
        cm = _make_cache_manager()
        details = [
            {
                "field": "서버명",
                "db_id": "polestar",
                "column": "CMM_RESOURCE.HOSTNAME",
                "matched_synonym": "hostname",
                "confidence": "high",
                "reason": "호스트명",
            },
            {
                "field": "IP주소",
                "db_id": "polestar",
                "column": "CMM_RESOURCE.IP_ADDRESS",
                "matched_synonym": "ip",
                "confidence": "medium",
                "reason": "IP 주소",
            },
        ]

        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms=None)

        # Plan 37: 비-EAV 매핑은 redis_cache.add_global_synonym()으로 글로벌에 등록
        assert cm._redis_cache.add_global_synonym.call_count == 2
        cm._redis_cache.add_global_synonym.assert_any_call(
            "HOSTNAME", ["서버명"]
        )
        cm._redis_cache.add_global_synonym.assert_any_call(
            "IP_ADDRESS", ["IP주소"]
        )

    @pytest.mark.asyncio
    async def test_register_llm_mappings_to_redis_eav(self) -> None:
        """EAV 매핑이 eav_name_synonyms에 등록된다."""
        cm = _make_cache_manager()
        details = [
            {
                "field": "담당자명",
                "db_id": "polestar",
                "column": "EAV:관리담당자",
                "matched_synonym": "담당자",
                "confidence": "high",
                "reason": "EAV 속성 매핑",
            }
        ]

        await _register_llm_mappings_to_redis(cm, details, eav_name_synonyms={})

        # EAV 매핑이므로 add_synonyms는 호출되지 않음
        cm.add_synonyms.assert_not_called()
        # redis_cache에 EAV synonyms가 저장됨
        cm._redis_cache.load_eav_name_synonyms.assert_called_once()
        cm._redis_cache.save_eav_name_synonyms.assert_called_once()
        # 저장된 내용 확인
        saved_eav = cm._redis_cache.save_eav_name_synonyms.call_args[0][0]
        assert "관리담당자" in saved_eav
        assert "담당자명" in saved_eav["관리담당자"]
        # Plan 37: EAV도 global에 등록됨
        cm._redis_cache.add_global_synonym.assert_called_once_with(
            "관리담당자", ["담당자명"]
        )

    @pytest.mark.asyncio
    async def test_register_llm_mappings_to_redis_no_cache(self) -> None:
        """cache_manager=None일 때 에러 없이 정상 반환된다."""
        details = [
            {
                "field": "서버명",
                "db_id": "polestar",
                "column": "CMM_RESOURCE.HOSTNAME",
                "confidence": "high",
                "reason": "test",
            }
        ]

        # None이어도 에러 없이 실행된다
        result = await _register_llm_mappings_to_redis(None, details, eav_name_synonyms=None)
        assert result is None  # -> None 반환


class TestPerform3stepMapping:
    """perform_3step_mapping 함수 테스트."""

    @pytest.mark.asyncio
    async def test_perform_3step_mapping_returns_tuple(self) -> None:
        """perform_3step_mapping()이 (MappingResult, list[dict]) tuple을 반환한다."""
        llm = AsyncMock()
        # LLM 응답을 JSON 형식으로 설정
        llm_response_content = json.dumps({
            "mappings": [
                {
                    "field": "비고",
                    "db_id": "polestar",
                    "column": "CMM_RESOURCE.REMARK",
                    "confidence": "medium",
                    "reason": "비고란에 해당",
                    "matched_synonym": "remark",
                }
            ]
        })
        llm.ainvoke = AsyncMock(
            return_value=MagicMock(content=llm_response_content)
        )

        field_names = ["서버명", "비고"]
        # 서버명은 synonym으로 매핑되도록 설정
        all_db_synonyms = {
            "polestar": {
                "CMM_RESOURCE.HOSTNAME": ["서버명", "호스트명"],
            }
        }
        all_db_descriptions = {
            "polestar": {
                "CMM_RESOURCE.HOSTNAME": "서버 호스트명",
                "CMM_RESOURCE.REMARK": "비고",
            }
        }

        result_tuple = await perform_3step_mapping(
            llm=llm,
            field_names=field_names,
            field_mapping_hints=[],
            all_db_synonyms=all_db_synonyms,
            all_db_descriptions=all_db_descriptions,
            priority_db_ids=["polestar"],
        )

        # tuple 반환 확인
        assert isinstance(result_tuple, tuple)
        assert len(result_tuple) == 2

        mapping_result, llm_details = result_tuple
        assert isinstance(mapping_result, MappingResult)
        assert isinstance(llm_details, list)

        # 서버명은 synonym으로 매핑됨
        assert mapping_result.column_mapping.get("서버명") == "CMM_RESOURCE.HOSTNAME"
        assert mapping_result.mapping_sources.get("서버명") == "synonym"

    @pytest.mark.asyncio
    async def test_perform_3step_mapping_with_cache_manager(self) -> None:
        """cache_manager 전달 시 _register_llm_mappings_to_redis가 호출된다."""
        llm = AsyncMock()

        cm = _make_cache_manager()

        field_names = ["비고"]
        all_db_synonyms = {"polestar": {}}
        all_db_descriptions = {
            "polestar": {
                "CMM_RESOURCE.REMARK": "비고란",
            }
        }

        fake_llm_details = [
            {
                "field": "비고",
                "db_id": "polestar",
                "column": "CMM_RESOURCE.REMARK",
                "confidence": "high",
                "reason": "비고 필드",
                "matched_synonym": "remark",
            }
        ]

        # _apply_llm_mapping_with_synonyms를 패치하여 LLM 추론 결과를 반환하도록 한다.
        # _register_llm_mappings_to_redis를 패치하여 호출 여부를 확인한다.
        with (
            patch(
                "src.document.field_mapper._apply_llm_mapping_with_synonyms",
                new_callable=AsyncMock,
                return_value=fake_llm_details,
            ),
            patch(
                "src.document.field_mapper._register_llm_mappings_to_redis",
                new_callable=AsyncMock,
            ) as mock_register,
        ):
            await perform_3step_mapping(
                llm=llm,
                field_names=field_names,
                field_mapping_hints=[],
                all_db_synonyms=all_db_synonyms,
                all_db_descriptions=all_db_descriptions,
                priority_db_ids=["polestar"],
                cache_manager=cm,
            )

            # LLM 추론이 발생했으므로 _register_llm_mappings_to_redis가 호출됨
            mock_register.assert_called_once()
            # 첫 번째 인자가 cache_manager인지 확인
            call_args = mock_register.call_args
            assert call_args[0][0] is cm
