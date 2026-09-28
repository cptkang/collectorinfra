"""강제 보충 범위 — 수동 프로필 테이블만 보충하고 유사어 테이블은 허용만 한다 (plans/114 P-4①).

단일 경로 `schema_analyzer` 의 Step 2 는 `allowed_tables` 에 있으면서 LLM 이 고르지 않은
테이블을 **강제로** relevant 에 넣는다. 그런데 그 `allowed_tables` 에는 이번 질의와 매칭된
유사어 테이블도 합쳐져 있어(D-051), LLM 이 무관하다고 판단한 테이블까지 끌려 들어왔다 —
폐쇄망 run 실측에서 보충 557회 중 **61회가 화이트리스트 밖 테이블**이었고(`plans/114` §2.8-②),
그 테이블의 라이브 샘플 수집이 턴 예산을 태웠다(§2.8-⑤ · 8초 타임아웃 × n).

멀티 경로 게이트(`multi_db_executor._scope_multi_schema`)는 **필터만** 하고 보충하지 않는다.
단일 경로도 같아야 한다(D-159 대칭).

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.schema_analyzer import schema_analyzer

#: 스키마에 있는 테이블 3종 — 프로필 화이트리스트 1개 · 유사어 매칭 1개 · 무관 1개.
_TABLES = ("polestar.cmm_resource", "polestar.sms_send_file_info", "polestar.rep_document")


def _schema_dict() -> dict:
    return {
        "tables": {name: {"columns": [{"name": "id", "type": "integer"}]} for name in _TABLES},
        "relationships": [],
    }


def _cache_mgr(synonyms: dict) -> AsyncMock:
    mgr = AsyncMock()
    mgr.get_schema_or_fetch.return_value = (_schema_dict(), True, "메모리", {}, {})
    mgr.get_synonyms.return_value = synonyms
    return mgr


async def _run(sample_state, mock_config, *, llm_picks: str, synonyms: dict) -> list[str]:
    sample_state["active_db_id"] = "polestar_test"
    sample_state["parsed_requirements"] = {
        "query_targets": ["서버", "용량"],
        "original_query": "서버 용량 목록",
    }
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=llm_picks)

    @asynccontextmanager
    async def _ctx(client):
        yield client

    with patch("src.nodes.schema_analyzer.get_db_client", return_value=_ctx(AsyncMock())), \
         patch("src.nodes.schema_analyzer.get_cache_manager", return_value=_cache_mgr(synonyms)), \
         patch("src.nodes.schema_analyzer._load_manual_profile",
               return_value={"source": "manual", "allowed_tables": ["cmm_resource"]}):
        result = await schema_analyzer(sample_state, llm=llm, app_config=mock_config)
    return list(result.get("relevant_tables") or [])


@pytest.mark.asyncio
async def test_유사어_매칭_테이블은_LLM이_고르지_않으면_보충되지_않는다(
    sample_state, mock_config
) -> None:
    """`'용량'` 이 질의에 있어 허용 집합에는 들지만, 보충 대상은 아니다(§2.8-② 재현)."""
    relevant = await _run(
        sample_state, mock_config,
        llm_picks="polestar.cmm_resource",
        synonyms={"sms_send_file_info.file_size": ["용량"]},
    )

    assert "polestar.cmm_resource" in relevant, "프로필 화이트리스트는 종전대로 보충된다"
    assert "polestar.sms_send_file_info" not in relevant


@pytest.mark.asyncio
async def test_LLM이_고른_유사어_테이블은_그대로_남는다(sample_state, mock_config) -> None:
    """보충을 좁히는 것이지 **필터를 좁히는 것이 아니다** — 허용은 종전 그대로다(D-051)."""
    relevant = await _run(
        sample_state, mock_config,
        llm_picks="polestar.cmm_resource, polestar.sms_send_file_info",
        synonyms={"sms_send_file_info.file_size": ["용량"]},
    )

    assert "polestar.sms_send_file_info" in relevant


@pytest.mark.asyncio
async def test_프로필_테이블은_LLM이_빠뜨려도_보충된다(sample_state, mock_config) -> None:
    """LLM 환각으로 화이트리스트가 누락되는 것을 막는 원래 목적은 유지된다."""
    relevant = await _run(
        sample_state, mock_config,
        llm_picks="polestar.rep_document",
        synonyms={},
    )

    assert "polestar.cmm_resource" in relevant


@pytest.mark.asyncio
async def test_허용_밖_테이블은_종전대로_걸러진다(sample_state, mock_config) -> None:
    relevant = await _run(
        sample_state, mock_config,
        llm_picks="polestar.rep_document",
        synonyms={},
    )

    assert "polestar.rep_document" not in relevant
