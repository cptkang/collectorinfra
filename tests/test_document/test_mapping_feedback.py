"""매핑 피드백 (MD diff + Redis 반영) 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.document.field_mapper import analyze_md_diff, apply_mapping_feedback_to_redis


def _make_cache_manager(redis_available: bool = True) -> MagicMock:
    """테스트용 cache_manager mock을 생성한다."""
    cm = MagicMock()
    cm.redis_available = redis_available
    cm.add_synonyms = AsyncMock(return_value=True)
    cm.remove_synonyms = AsyncMock(return_value=True)
    cm.add_global_synonym = AsyncMock(return_value=True)

    redis_cache = MagicMock()
    redis_cache.load_eav_name_synonyms = AsyncMock(return_value={})
    redis_cache.save_eav_name_synonyms = AsyncMock(return_value=None)
    cm._redis_cache = redis_cache

    return cm


class TestAnalyzeMdDiff:
    """analyze_md_diff 함수 테스트."""

    def test_analyze_md_diff_no_changes(self) -> None:
        """동일한 매핑이면 added/modified/deleted 모두 빈 리스트이다."""
        original = [
            {"field": "서버명", "column": "CMM_RESOURCE.HOSTNAME", "db_id": "polestar"},
            {"field": "IP주소", "column": "CMM_RESOURCE.IP_ADDRESS", "db_id": "polestar"},
        ]
        modified = [
            {"field": "서버명", "column": "CMM_RESOURCE.HOSTNAME", "db_id": "polestar"},
            {"field": "IP주소", "column": "CMM_RESOURCE.IP_ADDRESS", "db_id": "polestar"},
        ]

        result = analyze_md_diff(original, modified)

        assert result["added"] == []
        assert result["modified"] == []
        assert result["deleted"] == []
        assert result["unchanged"] == 2
        assert result["summary"] == "변경사항 없음"

    def test_analyze_md_diff_modified(self) -> None:
        """column이 변경된 경우 modified에 포함된다."""
        original = [
            {"field": "서버명", "column": "CMM_RESOURCE.HOSTNAME", "db_id": "polestar"},
        ]
        modified = [
            {"field": "서버명", "column": "CMM_RESOURCE.HOST_NAME", "db_id": "polestar"},
        ]

        result = analyze_md_diff(original, modified)

        assert len(result["modified"]) == 1
        mod = result["modified"][0]
        assert mod["field"] == "서버명"
        assert mod["old_column"] == "CMM_RESOURCE.HOSTNAME"
        assert mod["new_column"] == "CMM_RESOURCE.HOST_NAME"
        assert result["added"] == []
        assert result["deleted"] == []

    def test_analyze_md_diff_deleted(self) -> None:
        """원본에 있던 매핑이 수정본에서 None이면 deleted에 포함된다."""
        original = [
            {"field": "서버명", "column": "CMM_RESOURCE.HOSTNAME", "db_id": "polestar"},
        ]
        modified = [
            {"field": "서버명", "column": None, "db_id": None},
        ]

        result = analyze_md_diff(original, modified)

        assert len(result["deleted"]) == 1
        deleted = result["deleted"][0]
        assert deleted["field"] == "서버명"
        assert deleted["old_column"] == "CMM_RESOURCE.HOSTNAME"
        assert deleted["old_db_id"] == "polestar"
        assert result["added"] == []
        assert result["modified"] == []

    def test_analyze_md_diff_added(self) -> None:
        """원본에서 None이던 필드가 수정본에서 값이 있으면 added에 포함된다."""
        original = [
            {"field": "비고", "column": None, "db_id": None},
        ]
        modified = [
            {"field": "비고", "column": "CMM_RESOURCE.REMARK", "db_id": "polestar"},
        ]

        result = analyze_md_diff(original, modified)

        assert len(result["added"]) == 1
        added = result["added"][0]
        assert added["field"] == "비고"
        assert added["column"] == "CMM_RESOURCE.REMARK"
        assert added["db_id"] == "polestar"
        assert result["modified"] == []
        assert result["deleted"] == []

    def test_analyze_md_diff_row_removed(self) -> None:
        """수정본에서 행 자체가 삭제되면 deleted에 포함된다."""
        original = [
            {"field": "서버명", "column": "CMM_RESOURCE.HOSTNAME", "db_id": "polestar"},
            {"field": "IP주소", "column": "CMM_RESOURCE.IP_ADDRESS", "db_id": "polestar"},
        ]
        # 수정본에서 IP주소 행이 삭제됨
        modified = [
            {"field": "서버명", "column": "CMM_RESOURCE.HOSTNAME", "db_id": "polestar"},
        ]

        result = analyze_md_diff(original, modified)

        assert result["unchanged"] == 1
        assert len(result["deleted"]) == 1
        deleted = result["deleted"][0]
        assert deleted["field"] == "IP주소"
        assert deleted["old_column"] == "CMM_RESOURCE.IP_ADDRESS"
        assert deleted["old_db_id"] == "polestar"


class TestApplyMappingFeedbackToRedis:
    """apply_mapping_feedback_to_redis 함수 테스트."""

    @pytest.mark.asyncio
    async def test_apply_mapping_feedback_added(self) -> None:
        """added 항목이 cache_manager.add_synonyms로 등록된다."""
        cm = _make_cache_manager()
        diff_result = {
            "added": [
                {"field": "비고", "column": "CMM_RESOURCE.REMARK", "db_id": "polestar"},
            ],
            "modified": [],
            "deleted": [],
        }

        result = await apply_mapping_feedback_to_redis(cm, diff_result)

        assert result["registered"] == 1
        assert result["errors"] == []
        cm.add_synonyms.assert_called_once_with(
            "polestar", "CMM_RESOURCE.REMARK", ["비고"], source="user_corrected"
        )

    @pytest.mark.asyncio
    async def test_apply_mapping_feedback_modified(self) -> None:
        """modified 항목이 remove + add로 처리된다."""
        cm = _make_cache_manager()
        diff_result = {
            "added": [],
            "modified": [
                {
                    "field": "서버명",
                    "old_column": "CMM_RESOURCE.HOSTNAME",
                    "new_column": "CMM_RESOURCE.HOST_NAME",
                    "old_db_id": "polestar",
                    "new_db_id": "polestar",
                },
            ],
            "deleted": [],
        }

        result = await apply_mapping_feedback_to_redis(cm, diff_result)

        assert result["modified"] == 1
        assert result["errors"] == []
        # 기존 매핑 제거
        cm.remove_synonyms.assert_called_once_with(
            "polestar", "CMM_RESOURCE.HOSTNAME", ["서버명"]
        )
        # 새 매핑 등록
        cm.add_synonyms.assert_called_once_with(
            "polestar", "CMM_RESOURCE.HOST_NAME", ["서버명"], source="user_corrected"
        )

    @pytest.mark.asyncio
    async def test_apply_mapping_feedback_deleted(self) -> None:
        """deleted 항목이 remove로 처리된다."""
        cm = _make_cache_manager()
        diff_result = {
            "added": [],
            "modified": [],
            "deleted": [
                {
                    "field": "서버명",
                    "old_column": "CMM_RESOURCE.HOSTNAME",
                    "old_db_id": "polestar",
                },
            ],
        }

        result = await apply_mapping_feedback_to_redis(cm, diff_result)

        assert result["deleted"] == 1
        assert result["errors"] == []
        cm.remove_synonyms.assert_called_once_with(
            "polestar", "CMM_RESOURCE.HOSTNAME", ["서버명"]
        )
