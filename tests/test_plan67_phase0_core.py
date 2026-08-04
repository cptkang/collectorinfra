"""Plan 67 Phase 0-3 코어 결함 수정 검증.

대상: ① try_semantic_compile 죽은 코드 삭제 / ④ AgentState.prior_rows 선언 /
⑤ get_schema_or_fetch 반환 타입힌트 / ⑥ redis invalidate의 value index 키 /
⑩ JSON·SQL 추출 정규식 공용화(동작 불변).
"""

from __future__ import annotations

import json
import typing
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────
# ① try_semantic_compile 삭제
# ──────────────────────────────────────────────

class TestDeadSemanticCompileHelperRemoved:
    """정의 시점에 이미 NameError(server_scope 미정의)였던 죽은 헬퍼의 부활을 막는다."""

    def test_symbol_absent(self):
        import src.nodes.semantic_compiler as sc

        assert not hasattr(sc, "try_semantic_compile")

    def test_no_undefined_server_scope_reference(self):
        """모듈 내 server_scope 참조는 모두 파라미터가 있는 함수 안에만 남아야 한다."""
        import ast
        import inspect

        import src.nodes.semantic_compiler as sc

        tree = ast.parse(inspect.getsource(sc))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            uses = any(
                isinstance(n, ast.Name) and n.id == "server_scope"
                for n in ast.walk(fn)
            )
            if not uses:
                continue
            args = fn.args
            names = {a.arg for a in args.args + args.kwonlyargs + args.posonlyargs}
            assert "server_scope" in names, f"{fn.name}이 정의되지 않은 server_scope를 참조"


# ──────────────────────────────────────────────
# ④ AgentState.prior_rows 선언
# ──────────────────────────────────────────────

class TestPriorRowsDeclared:
    """생산자(subagents)·소비자(query_generator/multi_db_executor)가 쓰는 키의 State 선언."""

    def test_declared_in_agent_state(self):
        from src.state import AgentState

        assert "prior_rows" in AgentState.__annotations__

    def test_annotation_shape(self):
        from src.state import AgentState

        ann = AgentState.__annotations__["prior_rows"]
        text = getattr(ann, "__forward_arg__", str(ann))
        assert "dict[str, list[dict]]" in text

    def test_producer_key_matches_declaration(self):
        """subagents가 주입하는 키가 State 선언과 일치해야 소비처가 읽을 수 있다."""
        from src.orchestration.subagents import _make_isolated_input
        from src.state import AgentState

        task = {"task_id": "t2", "objective": "후속", "input_from": ["t1"]}
        state = {"user_query": "원본 질의"}
        prior = {"t1": {"query_results": [{"hostname": "srv-01"}]}}
        isolated = _make_isolated_input(task, state, prior)

        assert "prior_rows" in isolated
        assert "prior_rows" in AgentState.__annotations__


# ──────────────────────────────────────────────
# ⑤ get_schema_or_fetch 반환 타입힌트
# ──────────────────────────────────────────────

class TestGetSchemaOrFetchReturnHint:
    """타입힌트(4-튜플)와 실제 반환(5-튜플) 불일치 회귀 방지."""

    def test_hint_is_five_tuple_with_cache_source(self):
        from src.schema_cache.cache_manager import SchemaCacheManager

        hints = typing.get_type_hints(SchemaCacheManager.get_schema_or_fetch)
        ret = hints["return"]
        args = typing.get_args(ret)
        assert typing.get_origin(ret) is tuple
        assert len(args) == 5
        assert args[1] is bool          # cache_hit
        assert args[2] is str           # cache_source

    @pytest.mark.asyncio
    async def test_runtime_arity_matches_hint(self):
        """메모리 캐시 히트 경로의 실제 반환 원소 수가 타입힌트와 같다."""
        from src.schema_cache.cache_manager import SchemaCacheManager

        config = MagicMock()
        config.schema_cache.backend = "file"
        config.schema_cache.cache_dir = "/tmp/test_cache"
        config.schema_cache.enabled = True
        mgr = SchemaCacheManager(config)
        mgr._memory_cache.set({"tables": {}}, "db1")
        mgr.get_descriptions = AsyncMock(return_value={})
        mgr.load_synonyms_with_global_fallback = AsyncMock(return_value={})

        result = await mgr.get_schema_or_fetch(MagicMock(), "db1")

        hints = typing.get_type_hints(SchemaCacheManager.get_schema_or_fetch)
        assert len(result) == len(typing.get_args(hints["return"])) == 5
        assert result[2] == "메모리"


# ──────────────────────────────────────────────
# ⑥ invalidate()의 column_value_index 삭제
# ──────────────────────────────────────────────

class TestInvalidateDeletesValueIndex:
    """캐시 무효화 후에도 값 인덱스가 남아 낡은 값이 SQL 생성에 주입되던 누락 수정."""

    @pytest.fixture
    def connected_cache(self):
        from src.schema_cache.redis_cache import RedisSchemaCache

        config = MagicMock()
        config.host, config.port, config.db = "localhost", 6379, 0
        config.password, config.ssl, config.socket_timeout = "", False, 5
        cache = RedisSchemaCache(config)
        cache._redis = AsyncMock()
        cache._connected = True
        return cache

    @pytest.mark.asyncio
    async def test_value_index_key_deleted(self, connected_cache):
        assert await connected_cache.invalidate("test_db") is True

        deleted = set(connected_cache._redis.delete.await_args.args)
        assert connected_cache._key("test_db", "column_value_index") in deleted

    @pytest.mark.asyncio
    async def test_existing_keys_still_deleted(self, connected_cache):
        """기존 삭제 대상(meta/tables/…)은 그대로 유지된다."""
        await connected_cache.invalidate("test_db")

        deleted = set(connected_cache._redis.delete.await_args.args)
        for suffix in (
            "meta", "tables", "relationships", "descriptions",
            "synonyms", "fingerprint_checked_at", "structure_meta",
        ):
            assert connected_cache._key("test_db", suffix) in deleted


# ──────────────────────────────────────────────
# ⑩ JSON 추출 공용화
# ──────────────────────────────────────────────

class TestJsonExtractVariants:
    """공용 모듈에 추가한 배열판·코드펜스 변형."""

    def test_array_from_code_block(self):
        from src.utils.json_extract import extract_json_array_from_response

        content = '설명\n```json\n[{"column": "hostname"}]\n```\n끝'
        assert extract_json_array_from_response(content) == [{"column": "hostname"}]

    def test_array_bare(self):
        from src.utils.json_extract import extract_json_array_from_response

        assert extract_json_array_from_response('앞 [1, 2, 3] 뒤') == [1, 2, 3]

    def test_array_whole_content(self):
        from src.utils.json_extract import extract_json_array_from_response

        assert extract_json_array_from_response('[]') == []

    def test_array_failure_returns_none(self):
        from src.utils.json_extract import extract_json_array_from_response

        assert extract_json_array_from_response("배열 없음") is None

    def test_strip_code_fence_dict_and_array(self):
        from src.utils.json_extract import strip_code_fence

        assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert strip_code_fence('```\n[1, 2]\n```') == "[1, 2]"

    def test_strip_code_fence_passthrough(self):
        from src.utils.json_extract import strip_code_fence

        assert strip_code_fence('  {"a": 1}  ') == '{"a": 1}'


class TestContentBlockListCoercion:
    """실 모델(Gemini 3.x thinking 계열)의 콘텐츠 블록 리스트 content 정규화.

    2026-08-04 E1 라이브 실측: content가 list로 오면 정규식 파서가
    TypeError("expected string or bytes-like object, got 'list'")로 죽었다.
    """

    _BLOCKS = [
        {"type": "thinking", "thinking": "무시할 사고 블록"},
        {"type": "text", "text": '```json\n{"query_type": "조회"}\n```'},
    ]

    def test_json_from_block_list(self):
        from src.utils.json_extract import extract_json_from_response

        assert extract_json_from_response(self._BLOCKS) == {"query_type": "조회"}

    def test_json_array_from_block_list(self):
        from src.utils.json_extract import extract_json_array_from_response

        blocks = [{"type": "text", "text": "[1, 2]"}]
        assert extract_json_array_from_response(blocks) == [1, 2]

    def test_strip_code_fence_from_block_list(self):
        from src.utils.json_extract import strip_code_fence

        assert strip_code_fence(self._BLOCKS) == '{"query_type": "조회"}'

    def test_sql_from_block_list(self):
        from src.utils.query_gen_common import extract_sql_from_response

        blocks = [
            {"type": "thinking", "thinking": "사고"},
            {"type": "text", "text": "```sql\nSELECT 1\n```"},
        ]
        assert extract_sql_from_response(blocks) == "SELECT 1"

    def test_str_items_and_str_passthrough(self):
        from src.utils.json_extract import coerce_content_text

        assert coerce_content_text(['{"a"', ": 1}"]) == '{"a": 1}'
        assert coerce_content_text('{"a": 1}') == '{"a": 1}'
        assert coerce_content_text(None) == ""


class TestCallSitesUseSharedExtractor:
    """인라인 복제 정규식이 재유입되지 않도록 소스를 검사한다."""

    _SITES = (
        "src/nodes/cache_management.py",
        "src/nodes/semantic_compiler.py",
        "src/nodes/candidate_selector.py",
        "src/nodes/schema_analyzer.py",
        "src/schema_cache/cache_manager.py",
        "src/alarm/application/nodes/alarm_analyzer.py",
    )
    _DUP_PATTERNS = (
        r'```(?:json)?\s*(\{.*?\})\s*```',
        r'```(?:json)?\s*(\[.*?\])\s*```',
        r'\{.*\}',
        r'\[.*\]',
    )

    @pytest.mark.parametrize("rel_path", _SITES)
    def test_no_inline_json_regex(self, rel_path):
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        for pattern in self._DUP_PATTERNS:
            assert f're.search(r"{pattern}"' not in source, (
                f"{rel_path}에 인라인 JSON 추출 정규식 재유입: {pattern}"
            )

    @pytest.mark.parametrize("rel_path", _SITES)
    def test_imports_shared_helper(self, rel_path):
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert "from src.utils.json_extract import" in source


class TestCallSiteBehaviourUnchanged:
    """교체 후에도 각 사이트의 성공·실패 계약이 유지되는지 확인한다."""

    @pytest.mark.asyncio
    async def test_cache_intent_parses_and_falls_back(self):
        from src.nodes.cache_management import _parse_cache_intent

        llm = MagicMock()
        llm.ainvoke = AsyncMock(
            return_value=MagicMock(content='```json\n{"action": "refresh", "db_id": "db1"}\n```')
        )
        assert await _parse_cache_intent(llm, "캐시 갱신") == {
            "action": "refresh", "db_id": "db1",
        }

        llm.ainvoke = AsyncMock(return_value=MagicMock(content="JSON 없음"))
        assert await _parse_cache_intent(llm, "질의") == {
            "action": "status", "db_id": None,
        }

    @pytest.mark.asyncio
    async def test_candidate_selector_choice(self):
        from src.nodes.candidate_selector import _llm_pairwise_choice

        entries = [{"index": 0, "sql": "SELECT 1"}, {"index": 1, "sql": "SELECT 2"}]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"choice": 1}'))
        assert await _llm_pairwise_choice(
            llm, "질의", entries, is_kbgenai=False
        ) == 1

        llm.ainvoke = AsyncMock(return_value=MagicMock(content="판단 불가"))
        assert await _llm_pairwise_choice(
            llm, "질의", entries, is_kbgenai=False
        ) is None

        # 유효 index 밖 선택은 폐기(기존 계약)
        llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"choice": 9}'))
        assert await _llm_pairwise_choice(
            llm, "질의", entries, is_kbgenai=False
        ) is None

    def test_smq_parse_variants(self):
        from src.nodes.semantic_compiler import parse_smq_response

        assert parse_smq_response('{"pattern": "A", "dimensions": ["OSType"]}').pattern == "A"
        fenced = '```json\n{"pattern": "C", "entities": ["CMM_ALARM"]}\n```'
        assert parse_smq_response(fenced).pattern == "C"
        assert parse_smq_response("JSON 없음") is None

    def test_schema_analyzer_parse_llm_json(self):
        from src.nodes.schema_analyzer import _parse_llm_json

        assert _parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert _parse_llm_json('```\n["SELECT 1"]\n```') == ["SELECT 1"]
        assert _parse_llm_json('{"a": 1}') == {"a": 1}
        with pytest.raises(ValueError):
            _parse_llm_json("not json at all")

    def test_alarm_extract_json(self):
        from src.alarm.application.nodes.alarm_analyzer import _extract_json

        assert _extract_json('{"severity_label": "경고"}') == {"severity_label": "경고"}
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert _extract_json('설명\n{"a": 1}\n부연') == {"a": 1}
        with pytest.raises(ValueError):
            _extract_json("JSON이 없는 응답")

    @pytest.mark.asyncio
    async def test_cache_manager_global_synonyms(self):
        from src.schema_cache.cache_manager import SchemaCacheManager

        config = MagicMock()
        config.schema_cache.backend = "file"
        config.schema_cache.cache_dir = "/tmp/test_cache"
        mgr = SchemaCacheManager(config)

        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content='```json\n{"words": ["서버명"], "description": "호스트명"}\n```'
        ))
        result = await mgr.generate_global_synonyms("hostname", llm)
        assert result["words"] == ["서버명"]
        assert result["description"] == "호스트명"

        # 파싱 실패는 기존대로 seed 폴백(예외 전파 없음)
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="JSON 없음"))
        fallback = await mgr.generate_global_synonyms("hostname", llm)
        assert fallback == {"words": [], "description": ""}

    @pytest.mark.asyncio
    async def test_cache_manager_similar_columns_array(self):
        from src.schema_cache.cache_manager import SchemaCacheManager

        config = MagicMock()
        config.schema_cache.backend = "file"
        config.schema_cache.cache_dir = "/tmp/test_cache"
        mgr = SchemaCacheManager(config)
        mgr.get_global_synonyms_full = AsyncMock(return_value={
            "hostname": {"words": ["서버명"], "description": "호스트명"},
        })

        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content='```json\n[{"column": "hostname"}]\n```'
        ))
        results = await mgr.find_similar_global_columns("server_name", llm)
        assert results == [
            {"column": "hostname", "words": ["서버명"], "description": "호스트명"},
        ]

        # 배열이 아니면 기존대로 빈 목록
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="목록 없음"))
        assert await mgr.find_similar_global_columns("server_name", llm) == []


class TestSqlExtractorSingleSource:
    """SQL 코드블록 추출 2벌 통합 — 단일/멀티 DB 경로 대칭(D-066)."""

    def test_both_paths_share_one_function(self):
        import importlib

        from src.utils.query_gen_common import extract_sql_from_response

        single = importlib.import_module("src.nodes.query_generator")
        multi = importlib.import_module("src.nodes.multi_db_executor")
        assert single.extract_sql_from_response is extract_sql_from_response
        assert multi.extract_sql_from_response is extract_sql_from_response

    @pytest.mark.parametrize("content,expected_fragment", [
        ("```sql\nSELECT * FROM servers LIMIT 10;\n```", "LIMIT 10"),
        ("```\nSELECT hostname FROM servers LIMIT 5;\n```", "hostname"),
        ("설명: SELECT id FROM servers LIMIT 10;", "SELECT id"),
    ])
    def test_extraction_rules(self, content, expected_fragment):
        from src.utils.query_gen_common import extract_sql_from_response

        assert expected_fragment in extract_sql_from_response(content)

    def test_fallback_returns_full_content(self):
        from src.utils.query_gen_common import extract_sql_from_response

        assert extract_sql_from_response("SQL을 만들 수 없습니다") == "SQL을 만들 수 없습니다"

    def test_no_duplicate_definition_in_nodes(self):
        """노드 모듈에 사본 정의가 다시 생기지 않도록 소스를 검사한다."""
        for rel_path in ("src/nodes/query_generator.py", "src/nodes/multi_db_executor.py"):
            source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
            assert "def _extract_sql" not in source
            assert 're.search(r"```sql' not in source
