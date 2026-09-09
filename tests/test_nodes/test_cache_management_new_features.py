"""cache_management 노드 신규 3가지 기능 테스트.

기능 1: update-description action, list-synonyms에 description 표시
기능 2: generate-global-synonyms action (LLM 생성)
기능 3: Smart Synonym Reuse (유사 필드 자동 탐색 및 재활용)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.cache_management import (
    _handle_generate_global_synonyms,
    _handle_list_synonyms,
    _handle_reuse_synonym,
    _handle_update_description,
)


@pytest.fixture
def app_config():
    """테스트용 AppConfig."""
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ["polestar", "cloud_portal"]
    return config


@pytest.fixture
def cache_mgr():
    """Mock SchemaCacheManager."""
    mgr = AsyncMock()
    mgr.backend = "redis"
    mgr.ensure_redis_connected = AsyncMock(return_value=True)
    return mgr


@pytest.fixture
def mock_llm():
    """Mock LLM."""
    llm = AsyncMock()
    return llm


# === 기능 1: update-description ===


class TestHandleUpdateDescription:
    """update-description 핸들러 테스트."""

    @pytest.mark.asyncio
    async def test_update_description_success(self, cache_mgr):
        """description 업데이트 성공."""
        cache_mgr.get_global_description = AsyncMock(return_value="이전 설명")
        cache_mgr.update_global_description = AsyncMock(return_value=True)

        result = await _handle_update_description(
            cache_mgr, "hostname", "서버의 호스트명 (FQDN)"
        )

        assert "업데이트했습니다" in result
        assert "이전 설명" in result
        assert "서버의 호스트명 (FQDN)" in result
        cache_mgr.update_global_description.assert_called_once_with(
            "hostname", "서버의 호스트명 (FQDN)"
        )

    @pytest.mark.asyncio
    async def test_update_description_no_previous(self, cache_mgr):
        """이전 description 없을 때."""
        cache_mgr.get_global_description = AsyncMock(return_value=None)
        cache_mgr.update_global_description = AsyncMock(return_value=True)

        result = await _handle_update_description(
            cache_mgr, "hostname", "새 설명"
        )

        assert "업데이트했습니다" in result
        assert "이전" not in result  # 이전 설명 라인 없음

    @pytest.mark.asyncio
    async def test_update_description_no_column(self, cache_mgr):
        """컬럼 미지정 시 에러."""
        result = await _handle_update_description(cache_mgr, None, "설명")
        assert "지정해야" in result

    @pytest.mark.asyncio
    async def test_update_description_no_text(self, cache_mgr):
        """설명 텍스트 미지정 시 에러."""
        result = await _handle_update_description(
            cache_mgr, "hostname", None
        )
        assert "입력해야" in result

    @pytest.mark.asyncio
    async def test_update_description_failure(self, cache_mgr):
        """업데이트 실패 시 에러 메시지."""
        cache_mgr.get_global_description = AsyncMock(return_value=None)
        cache_mgr.update_global_description = AsyncMock(return_value=False)

        result = await _handle_update_description(
            cache_mgr, "hostname", "설명"
        )

        assert "실패" in result

    @pytest.mark.asyncio
    async def test_update_description_strips_table_prefix(self, cache_mgr):
        """table.column 형식에서 bare name 추출."""
        cache_mgr.get_global_description = AsyncMock(return_value=None)
        cache_mgr.update_global_description = AsyncMock(return_value=True)

        await _handle_update_description(
            cache_mgr, "servers.hostname", "설명"
        )

        # bare name "hostname"으로 호출
        cache_mgr.update_global_description.assert_called_once_with(
            "hostname", "설명"
        )


# === 기능 1: list-synonyms에 description 표시 ===


class TestListSynonymsWithDescription:
    """list-synonyms에서 description 표시 테스트."""

    @pytest.mark.asyncio
    async def test_list_column_shows_description(self, cache_mgr, app_config):
        """특정 컬럼 조회 시 description 표시."""
        cache_mgr.get_global_synonyms_full = AsyncMock(return_value={
            "hostname": {
                "words": ["서버명", "호스트명"],
                "description": "서버의 호스트명",
            }
        })
        cache_mgr.get_synonyms = AsyncMock(return_value={})

        result = await _handle_list_synonyms(
            cache_mgr, app_config, None, "hostname"
        )

        assert "[설명] 서버의 호스트명" in result
        assert "[글로벌 유사 단어]" in result
        assert "서버명" in result

    @pytest.mark.asyncio
    async def test_list_global_shows_descriptions(self, cache_mgr, app_config):
        """전체 글로벌 조회 시 description 표시."""
        cache_mgr.get_global_synonyms_full = AsyncMock(return_value={
            "hostname": {
                "words": ["서버명"],
                "description": "서버의 호스트명",
            },
            "ip_address": {
                "words": ["IP"],
                "description": "IP 주소",
            },
        })

        result = await _handle_list_synonyms(
            cache_mgr, app_config, None, None
        )

        assert "글로벌 유사 단어 사전" in result
        assert "서버의 호스트명" in result
        assert "IP 주소" in result

    @pytest.mark.asyncio
    async def test_list_global_empty(self, cache_mgr, app_config):
        """글로벌 사전 비어있을 때."""
        cache_mgr.get_global_synonyms_full = AsyncMock(return_value={})

        result = await _handle_list_synonyms(
            cache_mgr, app_config, None, None
        )

        assert "비어 있습니다" in result


# === 기능 2: generate-global-synonyms ===


class TestHandleGenerateGlobalSynonyms:
    """generate-global-synonyms 핸들러 테스트."""

    @pytest.mark.asyncio
    async def test_generate_existing_column(self, cache_mgr, mock_llm):
        """이미 글로벌 사전에 있는 컬럼 -> 바로 생성."""
        cache_mgr.get_global_synonyms = AsyncMock(
            return_value={"hostname": ["서버명"]}
        )
        cache_mgr.generate_global_synonyms = AsyncMock(return_value={
            "words": ["서버명", "호스트명", "서버 이름"],
            "description": "서버의 호스트명",
        })
        cache_mgr.find_similar_global_columns = AsyncMock(return_value=[])

        result = await _handle_generate_global_synonyms(
            cache_mgr, mock_llm, "hostname", None, None
        )

        assert "생성했습니다" in result
        assert "서버의 호스트명" in result
        cache_mgr.generate_global_synonyms.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_with_seed_words(self, cache_mgr, mock_llm):
        """seed_words 포함 생성."""
        cache_mgr.get_global_synonyms = AsyncMock(
            return_value={"hostname": ["서버명"]}
        )
        cache_mgr.generate_global_synonyms = AsyncMock(return_value={
            "words": ["서버명", "호스트", "서버 이름"],
            "description": "서버 호스트명",
        })

        result = await _handle_generate_global_synonyms(
            cache_mgr, mock_llm, "hostname", ["서버명", "호스트"], None
        )

        assert "생성했습니다" in result
        cache_mgr.generate_global_synonyms.assert_called_once_with(
            "hostname", mock_llm, seed_words=["서버명", "호스트"]
        )

    @pytest.mark.asyncio
    async def test_generate_no_column(self, cache_mgr, mock_llm):
        """컬럼 미지정 시 에러."""
        result = await _handle_generate_global_synonyms(
            cache_mgr, mock_llm, None, None, None
        )
        assert "지정해야" in result

    @pytest.mark.asyncio
    async def test_generate_new_column_with_similar_found(self, cache_mgr, mock_llm):
        """새 컬럼 + 유사 필드 발견 -> 재활용 제안."""
        cache_mgr.get_global_synonyms = AsyncMock(return_value={})
        cache_mgr.find_similar_global_columns = AsyncMock(return_value=[
            {
                "column": "hostname",
                "words": ["서버명", "호스트명"],
                "description": "서버의 호스트명",
            }
        ])

        result = await _handle_generate_global_synonyms(
            cache_mgr, mock_llm, "server_name", None, None
        )

        # dict 반환 (pending_synonym_reuse 포함)
        assert isinstance(result, dict)
        assert "pending_synonym_reuse" in result
        assert result["pending_synonym_reuse"]["target_column"] == "server_name"
        assert "재활용" in result["response_text"]
        assert "hostname" in result["response_text"]

    @pytest.mark.asyncio
    async def test_generate_new_column_no_similar(self, cache_mgr, mock_llm):
        """새 컬럼 + 유사 필드 없음 -> 바로 생성."""
        cache_mgr.get_global_synonyms = AsyncMock(return_value={})
        cache_mgr.find_similar_global_columns = AsyncMock(return_value=[])
        cache_mgr.generate_global_synonyms = AsyncMock(return_value={
            "words": ["서버명", "서버 이름"],
            "description": "서버의 이름",
        })

        result = await _handle_generate_global_synonyms(
            cache_mgr, mock_llm, "server_name", None, None
        )

        assert isinstance(result, str)
        assert "생성했습니다" in result


# === 기능 3: Smart Synonym Reuse ===


class TestHandleReuseSynonym:
    """reuse-synonym 핸들러 테스트."""

    @pytest.mark.asyncio
    async def test_reuse_copy(self, cache_mgr, mock_llm):
        """재활용 (copy) 모드."""
        pending = {
            "target_column": "server_name",
            "suggestions": [
                {
                    "column": "hostname",
                    "words": ["서버명", "호스트명"],
                    "description": "서버의 호스트명",
                }
            ],
        }
        cache_mgr.reuse_synonyms = AsyncMock(return_value={
            "words": ["서버명", "호스트명"],
            "description": "서버의 호스트명",
        })

        result = await _handle_reuse_synonym(
            cache_mgr, mock_llm, pending, "reuse"
        )

        assert "재활용했습니다" in result
        assert "서버의 호스트명" in result
        cache_mgr.reuse_synonyms.assert_called_once_with(
            "hostname", "server_name", mode="copy"
        )

    @pytest.mark.asyncio
    async def test_reuse_new(self, cache_mgr, mock_llm):
        """새로 생성 모드."""
        pending = {
            "target_column": "server_name",
            "suggestions": [
                {"column": "hostname", "words": ["서버명"], "description": ""}
            ],
        }
        cache_mgr.generate_global_synonyms = AsyncMock(return_value={
            "words": ["서버이름", "서버명"],
            "description": "서버의 이름",
        })

        result = await _handle_reuse_synonym(
            cache_mgr, mock_llm, pending, "new"
        )

        assert "새로 생성했습니다" in result
        cache_mgr.generate_global_synonyms.assert_called_once_with(
            "server_name", mock_llm
        )

    @pytest.mark.asyncio
    async def test_reuse_merge(self, cache_mgr, mock_llm):
        """병합 모드."""
        pending = {
            "target_column": "server_name",
            "suggestions": [
                {
                    "column": "hostname",
                    "words": ["서버명"],
                    "description": "서버의 호스트명",
                }
            ],
        }
        cache_mgr.reuse_synonyms = AsyncMock(return_value={
            "words": ["서버명", "서버이름", "호스트"],
            "description": "서버의 이름",
        })

        result = await _handle_reuse_synonym(
            cache_mgr, mock_llm, pending, "merge"
        )

        assert "병합했습니다" in result
        cache_mgr.reuse_synonyms.assert_called_once_with(
            "hostname", "server_name", mode="merge", llm=mock_llm
        )

    @pytest.mark.asyncio
    async def test_reuse_no_suggestions(self, cache_mgr, mock_llm):
        """suggestion 없을 때."""
        pending = {
            "target_column": "server_name",
            "suggestions": [],
        }

        result = await _handle_reuse_synonym(
            cache_mgr, mock_llm, pending, "reuse"
        )

        assert "없습니다" in result


# === 결정적 db_id 검증·제품군 전개 (2026-09-01 A-05 실측 재발 방지) ===


class TestResolveCacheDbTarget:
    """`_resolve_cache_db_target` — LLM 추출 db_id의 활성 목록 검증·프리픽스 전개."""

    def _cfg(self, active):
        config = MagicMock()
        config.multi_db.get_active_db_ids.return_value = active
        return config

    def test_active_db_id_passes_through(self):
        from src.nodes.cache_management import _resolve_cache_db_target

        db_id, override, note = _resolve_cache_db_target(
            "polestar_cm_gp", self._cfg(["polestar_cm_gp", "polestar_cm_yd"])
        )
        assert (db_id, override, note) == ("polestar_cm_gp", None, None)

    def test_family_surface_word_expands_to_active(self):
        """A-05 형태: 'polestar'(제품군 표면어)가 비활성 샌드박스로 가지 않고 활성 3종 전개."""
        from src.nodes.cache_management import _resolve_cache_db_target

        active = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
        db_id, override, note = _resolve_cache_db_target("polestar", self._cfg(active))
        assert db_id is None
        assert override == active
        assert note and "제품군" in note

    def test_sandbox_active_locally_unchanged(self):
        """로컬 개발(ACTIVE=polestar)에서는 'polestar'가 활성이므로 전개하지 않는다."""
        from src.nodes.cache_management import _resolve_cache_db_target

        db_id, override, note = _resolve_cache_db_target(
            "polestar", self._cfg(["polestar"])
        )
        assert (db_id, override, note) == ("polestar", None, None)

    def test_unknown_db_id_aborts_with_guidance(self):
        from src.nodes.cache_management import _resolve_cache_db_target

        db_id, override, note = _resolve_cache_db_target(
            "cloud_portal", self._cfg(["polestar_cm_gp"])
        )
        assert db_id is None
        assert override is None
        assert note and "활성 DB" in note

    def test_none_db_id_unchanged(self):
        from src.nodes.cache_management import _resolve_cache_db_target

        assert _resolve_cache_db_target(None, self._cfg(["polestar_cm_gp"])) == (
            None, None, None,
        )


class TestGenerateSkipsDb2:
    """DB2 존 캐시 갱신 명시 안내 (2026-09-01 V2-1 실측 — 침묵 error 방지)."""

    @pytest.mark.asyncio
    async def test_db2_zone_skipped_with_notice(self, app_config, cache_mgr):
        from src.nodes.cache_management import _handle_generate

        db2_cfg = MagicMock()
        db2_cfg.db_engine = "db2"
        with patch(
            "src.routing.domain_config.get_domain_by_id", return_value=db2_cfg
        ):
            result = await _handle_generate(cache_mgr, app_config, "polestar_b0")

        assert "캐시 갱신 미지원" in result
        assert "polestar_b0" in result
        cache_mgr.refresh_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_pg_zone_still_refreshes(self, app_config, cache_mgr):
        from src.nodes.cache_management import _handle_generate

        pg_cfg = MagicMock()
        pg_cfg.db_engine = "postgresql"
        refresh_result = MagicMock()
        refresh_result.status = "unchanged"
        refresh_result.table_count = 384
        refresh_result.fingerprint = "f" * 32
        cache_mgr.refresh_cache = AsyncMock(return_value=refresh_result)
        with patch(
            "src.routing.domain_config.get_domain_by_id", return_value=pg_cfg
        ), patch("src.db.get_db_client") as gc:
            gc.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            gc.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _handle_generate(cache_mgr, app_config, "polestar_cm_gp")

        assert "unchanged" in result
        cache_mgr.refresh_cache.assert_called_once()
