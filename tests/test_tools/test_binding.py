"""LangChain 도구 바인딩 검증 (Plan 67 Phase S1 §4.2).

LLM 호출 없이, 주입 의존성에 따라 어떤 도구가 노출되고 무엇을 반환하는지만 확인한다.
"""

from __future__ import annotations

import json

from src.tools.binding import ToolContext, build_query_tools


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


class TestToolExposure:
    def test_context_without_dependencies_exposes_only_pure_tools(self):
        """주입이 없으면 의존성 없는 해석·분류 도구만 노출된다(헛호출 방지)."""
        names = {t.name for t in build_query_tools(ToolContext())}
        assert names == {"resolve_time_range", "resolve_limit", "classify_metric_field"}

    def test_semantic_model_enables_catalog_tools(self, semantic_model):
        names = {t.name for t in build_query_tools(ToolContext(semantic_model=semantic_model))}
        assert {"search_catalog", "check_smq_coverage"} <= names

    def test_synonyms_enable_lookup(self, synonyms):
        names = {t.name for t in build_query_tools(ToolContext(synonyms=synonyms))}
        assert "lookup_synonym" in names

    def test_value_index_enables_value_search(self):
        names = {t.name for t in build_query_tools(ToolContext(value_index={"k": ["v"]}))}
        assert "search_value_index" in names

    def test_schema_info_enables_validation(self, schema_info):
        names = {t.name for t in build_query_tools(ToolContext(schema_info=schema_info))}
        assert "validate_sql_draft" in names

    def test_db_client_enables_probe_tools(self):
        class Client:
            async def get_table_schema(self, table_name): ...
            async def get_sample_data(self, table_name, limit=5): ...

        names = {t.name for t in build_query_tools(ToolContext(db_client=Client()))}
        assert {"get_table_schema", "get_sample_data"} <= names

    def test_all_tools_have_korean_description(self, semantic_model, synonyms, schema_info):
        tools = build_query_tools(
            ToolContext(semantic_model=semantic_model, synonyms=synonyms,
                        schema_info=schema_info, value_index={"k": ["v"]})
        )
        for t in tools:
            assert t.description and t.description.strip()


class TestToolInvocation:
    def test_search_catalog_returns_json_hits(self, semantic_model):
        tools = build_query_tools(ToolContext(semantic_model=semantic_model))
        payload = json.loads(_tool(tools, "search_catalog").invoke({"term": "서버명"}))
        assert payload[0]["name"] == "hostname"

    def test_search_catalog_empty_term_renders_full_catalog(self, semantic_model):
        tools = build_query_tools(ToolContext(semantic_model=semantic_model))
        text = _tool(tools, "search_catalog").invoke({"term": ""})
        assert "hostname" in text and "Utilization" in text

    def test_resolve_limit_uses_context_query_when_arg_empty(self):
        tools = build_query_tools(ToolContext(user_query="상위 5대", default_limit=1000))
        assert json.loads(_tool(tools, "resolve_limit").invoke({"query": ""})) == {"limit": 5}

    def test_validate_sql_draft_reports_errors(self, schema_info):
        tools = build_query_tools(ToolContext(schema_info=schema_info))
        payload = json.loads(_tool(tools, "validate_sql_draft").invoke({"sql": "DROP TABLE host;"}))
        assert payload["valid"] is False

    async def test_probe_tool_error_is_surfaced(self):
        """조회 실패는 삼키지 않고 사유를 그대로 노출한다(침묵 폴백 금지)."""
        class FailingClient:
            async def get_table_schema(self, table_name):
                raise RuntimeError("연결 끊김")

            async def get_sample_data(self, table_name, limit=5):
                raise RuntimeError("연결 끊김")

        tools = build_query_tools(ToolContext(db_client=FailingClient()))
        payload = json.loads(await _tool(tools, "get_table_schema").ainvoke({"table": "host"}))
        assert "연결 끊김" in payload["error"]
