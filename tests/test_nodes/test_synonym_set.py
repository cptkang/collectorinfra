"""앵커 없는 동의어 집합 등록 테스트 (D-142).

집합 원소 중 **실제 스키마에 존재하는 것**을 앵커로 채택한다.
후보가 0개(미존재)·2개 이상(모호)이면 되묻고 **아무것도 등록하지 않는다** —
임의 선택은 LLM 비결정성을 코드로 옮기는 것에 불과하고, 오등록은 조용히
검색 품질을 갉아먹으며 자기강화된다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.nodes.cache_management import _handle_add_synonym_set, _infer_anchor


def _cache_mgr(
    *,
    schema_columns: list[str] | None = None,
    global_keys: list[str] | None = None,
    eav_names: list[str] | None = None,
):
    """앵커 후보 소스 3개를 흉내내는 캐시 매니저 대역."""
    mgr = MagicMock()
    mgr.redis_available = True
    mgr.get_schema = AsyncMock(return_value={
        "tables": {
            "server": {"columns": [{"name": c} for c in (schema_columns or [])]}
        }
    })
    mgr.get_global_synonyms = AsyncMock(
        return_value={k: [] for k in (global_keys or [])}
    )
    mgr._redis_cache = MagicMock()
    mgr._redis_cache.load_eav_name_synonyms = AsyncMock(
        return_value={k: [] for k in (eav_names or [])}
    )
    mgr.add_global_synonym = AsyncMock(return_value=True)
    mgr.add_synonyms = AsyncMock(return_value=True)
    return mgr


def _config(db_ids: list[str] | None = None):
    cfg = MagicMock()
    cfg.multi_db.get_active_db_ids.return_value = db_ids if db_ids is not None else ["polestar_b0"]
    return cfg


class TestAnchorInference:
    async def test_schema_column_becomes_anchor(self):
        mgr = _cache_mgr(schema_columns=["cpu", "hostname"])

        anchor, candidates, _ = await _infer_anchor(mgr, _config(), ["vcore", "cpu", "core"])

        assert anchor == "cpu"
        assert candidates == ["cpu"]

    async def test_global_synonym_key_becomes_anchor(self):
        mgr = _cache_mgr(schema_columns=[], global_keys=["cpu"])

        anchor, _, _ = await _infer_anchor(mgr, _config(), ["vcore", "cpu", "core"])

        assert anchor == "cpu"

    async def test_eav_name_becomes_anchor(self):
        mgr = _cache_mgr(schema_columns=[], global_keys=[], eav_names=["cpu"])

        anchor, _, _ = await _infer_anchor(mgr, _config(), ["vcore", "cpu", "core"])

        assert anchor == "cpu"

    async def test_no_candidate_returns_none(self):
        mgr = _cache_mgr(schema_columns=["hostname"])

        anchor, candidates, reason = await _infer_anchor(mgr, _config(), ["aaa", "bbb"])

        assert anchor is None and candidates == []
        assert reason == "not_found"

    async def test_ambiguous_candidates_return_none(self):
        """후보가 둘이면 임의로 고르지 않는다."""
        mgr = _cache_mgr(schema_columns=["cpu", "core"])

        anchor, candidates, reason = await _infer_anchor(mgr, _config(), ["vcore", "cpu", "core"])

        assert anchor is None
        assert set(candidates) == {"cpu", "core"}
        assert reason == "ambiguous"

    async def test_case_insensitive_match(self):
        """대소문자가 달라도 매칭되며, 앵커는 **스키마 표기**로 반환된다.

        종전에는 사용자 입력 표기(`cpu`)를 돌려줘 사전 키가 스키마(`CPU`)와 갈렸다.
        같은 컬럼에 대소문자만 다른 키가 둘 생기므로 스키마 표기로 통일한다.
        """
        mgr = _cache_mgr(schema_columns=["CPU"])

        anchor, _, _ = await _infer_anchor(mgr, _config(), ["vcore", "cpu"])

        assert anchor == "CPU"

    async def test_schema_lookup_failure_is_tolerated(self):
        """한 소스가 실패해도 나머지로 판정한다 (개별 try/except)."""
        mgr = _cache_mgr(global_keys=["cpu"])
        mgr.get_schema = AsyncMock(side_effect=RuntimeError("redis down"))

        anchor, _, _ = await _infer_anchor(mgr, _config(), ["vcore", "cpu"])

        assert anchor == "cpu"


class TestRegistration:
    async def test_registers_remaining_words_under_anchor(self):
        mgr = _cache_mgr(schema_columns=["cpu"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["vcore", "cpu", "core"])

        mgr.add_global_synonym.assert_awaited_once()
        anchor_arg, words_arg = mgr.add_global_synonym.await_args.args[:2]
        assert anchor_arg == "cpu"
        assert set(words_arg) == {"vcore", "core"}
        assert "cpu" in msg and "vcore" in msg and "core" in msg

    async def test_not_found_asks_back_without_registering(self):
        mgr = _cache_mgr(schema_columns=["hostname"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["aaa", "bbb"])

        mgr.add_global_synonym.assert_not_awaited()
        mgr.add_synonyms.assert_not_awaited()
        assert "찾지 못했" in msg or "어느" in msg

    async def test_ambiguous_asks_back_without_registering(self):
        mgr = _cache_mgr(schema_columns=["cpu", "core"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["vcore", "cpu", "core"])

        mgr.add_global_synonym.assert_not_awaited()
        assert "cpu" in msg and "core" in msg  # 후보를 알려준다

    async def test_redis_unavailable_reports_reason(self):
        mgr = _cache_mgr(schema_columns=["cpu"])
        mgr.redis_available = False

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["vcore", "cpu"])

        mgr.add_global_synonym.assert_not_awaited()
        assert "Redis" in msg

    async def test_syncs_to_db_that_has_the_column(self):
        mgr = _cache_mgr(schema_columns=["cpu"])

        await _handle_add_synonym_set(mgr, _config(["polestar_b0"]), None, ["vcore", "cpu"])

        mgr.add_synonyms.assert_awaited()
        db_id, col_key = mgr.add_synonyms.await_args.args[:2]
        assert db_id == "polestar_b0"
        assert col_key == "server.cpu"

    async def test_response_states_what_was_registered(self):
        """등록 내역을 명시한다 — 침묵 등록 금지."""
        mgr = _cache_mgr(schema_columns=["cpu"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["vcore", "cpu", "core"])

        assert "cpu" in msg
        assert "vcore" in msg and "core" in msg


class TestValidationGuards:
    @pytest.mark.parametrize("words", [None, [], ["cpu"]])
    async def test_too_few_words_rejected(self, words):
        mgr = _cache_mgr(schema_columns=["cpu"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, words)

        mgr.add_global_synonym.assert_not_awaited()
        assert "2개" in msg or "부족" in msg

    async def test_too_many_words_rejected(self):
        mgr = _cache_mgr(schema_columns=["cpu"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["cpu"] + [f"w{i}" for i in range(25)])

        mgr.add_global_synonym.assert_not_awaited()
        assert "20개" in msg or "많" in msg

    async def test_invalid_characters_rejected(self):
        mgr = _cache_mgr(schema_columns=["cpu"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["cpu", "drop;table"])

        mgr.add_global_synonym.assert_not_awaited()
        assert "형식" in msg or "허용" in msg

    async def test_duplicates_collapsed_before_count_check(self):
        mgr = _cache_mgr(schema_columns=["cpu"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["cpu", "cpu", "CPU"])

        mgr.add_global_synonym.assert_not_awaited()
        assert "2개" in msg or "부족" in msg


class TestConflictDisclosure:
    async def test_existing_other_anchor_is_disclosed(self):
        """같은 단어가 다른 앵커에 이미 있으면 침묵 병합하지 않는다."""
        mgr = _cache_mgr(schema_columns=["cpu"], global_keys=["memory"])
        mgr.get_global_synonyms = AsyncMock(return_value={"memory": ["vcore"]})

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["vcore", "cpu"])

        assert "memory" in msg and "vcore" in msg
        assert "이미" in msg or "충돌" in msg


class TestAnchorCasing:
    """앵커는 **스키마의 실제 표기**로 등록되어야 한다.

    Prove-It: 사용자가 `CPU`로 입력하면 사전 키가 `CPU`로 들어가는데 실제 컬럼은 `cpu`이고
    DB 동기화는 `server.cpu`로 간다(2026-08-19 실측). 표기가 갈리면 같은 컬럼에 대해
    대소문자만 다른 사전 키가 둘 생기고, 소비 측(`field_mapper`)이 어느 쪽을 집을지가
    입력 표기에 좌우된다.
    """

    async def test_anchor_uses_schema_casing_not_user_input(self):
        mgr = _cache_mgr(schema_columns=["cpu"])

        await _handle_add_synonym_set(mgr, _config(), None, ["vcore", "CPU"])

        anchor_arg = mgr.add_global_synonym.await_args.args[0]
        assert anchor_arg == "cpu", (
            f"사전 키가 '{anchor_arg}' — 스키마 표기 'cpu'와 달라 중복 키가 생긴다"
        )

    async def test_global_key_and_db_sync_key_agree(self):
        """글로벌 사전 키와 DB 동기화 컬럼명이 같은 표기여야 한다."""
        mgr = _cache_mgr(schema_columns=["cpu"])

        await _handle_add_synonym_set(mgr, _config(), None, ["vcore", "CPU"])

        anchor = mgr.add_global_synonym.await_args.args[0]
        col_key = mgr.add_synonyms.await_args.args[1]

        assert col_key.split(".")[-1] == anchor

    async def test_rest_excludes_anchor_case_insensitively(self):
        """앵커와 대소문자만 다른 입력은 유사어로 재등록되지 않는다."""
        mgr = _cache_mgr(schema_columns=["cpu"])

        await _handle_add_synonym_set(mgr, _config(), None, ["CPU", "vcore"])

        words = mgr.add_global_synonym.await_args.args[1]
        assert [w.lower() for w in words] == ["vcore"]

    async def test_anchor_from_global_dict_uses_stored_casing(self):
        """전역 사전에서 앵커를 찾은 경우도 저장된 표기를 따른다."""
        mgr = _cache_mgr(schema_columns=[], global_keys=["Hostname"])

        await _handle_add_synonym_set(mgr, _config(), None, ["서버명", "hostname"])

        assert mgr.add_global_synonym.await_args.args[0] == "Hostname"


class TestLLMFallbackPath:
    """선파서가 매칭하지 못한 문장은 LLM 파싱 결과로 이 핸들러에 도달한다."""

    async def test_llm_supplied_words_go_through_same_guards(self):
        """LLM이 준 단어도 동일한 결정적 검증을 통과해야 한다."""
        mgr = _cache_mgr(schema_columns=["cpu"])

        msg = await _handle_add_synonym_set(mgr, _config(), None, ["cpu", "drop;table"])

        mgr.add_global_synonym.assert_not_awaited()
        assert "형식" in msg or "허용" in msg
