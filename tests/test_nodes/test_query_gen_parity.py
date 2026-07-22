"""단일/멀티 DB SQL 생성 경로 동등화 회귀 테스트 (D-066).

버그(2026-07-09): 은행존(단일 DB)은 query_generator 경로라 few-shot 예시·전체조회 LIMIT
상향이 적용돼 정상 동작했으나, 공동존(gp+yd, 멀티 DB)은 multi_db_executor 경로라 예시
미주입·LIMIT 1000 고정으로 조인 환각·데이터 절단이 발생.

수정: 공용 헬퍼(query_gen_common)로 예시 주입·LIMIT 상향을 두 경로가 공유.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.nodes.multi_db_executor import _analyze_schema, _generate_sql
from src.utils.query_gen_common import (
    build_query_examples_block,
    resolve_query_limit,
)


class TestResolveQueryLimit:
    """'전체/모든/모두' 조회 시 LIMIT 상향 규칙."""

    @pytest.mark.parametrize(
        "query",
        ["공동존의 전체 서버들에 대하여", "모든 서버 조회", "서버 모두 보여줘"],
    )
    def test_all_query_raises_limit(self, query):
        assert resolve_query_limit(query, 1000) == 100_000

    @pytest.mark.parametrize("query", ["CPU 높은 서버 10개", "", None])
    def test_normal_query_keeps_default(self, query):
        assert resolve_query_limit(query, 1000) == 1000

    @pytest.mark.parametrize("query,want", [
        ("경고 이상 알람 이력을 최근 발생 순으로 100건 조회해줘", 100),
        ("알람 50건만 보여줘", 50),
        ("장애가 많은 상위 10개 서버를 알려줘", 10),
        ("전체 서버 중 200건", 200),          # 명시 건수가 '전체' 상향보다 우선
    ])
    def test_explicit_count_overrides(self, query, want):
        """명시 건수("N건"/"상위 N")는 결정적으로 LIMIT에 반영한다(2026-07-21 yd-006 실측).

        단독 "N개"는 "개월"·"코어 4개인 서버" 등 수량 한정과 혼동되므로 인정하지 않는다
        (위 test_normal_query_keeps_default의 "서버 10개" 케이스가 그 경계를 고정).
        """
        assert resolve_query_limit(query, 1000) == want

    def test_month_expression_not_mistaken_for_count(self):
        assert resolve_query_limit("지난 3개월간 통계를 조회해줘", 1000) == 1000


class TestBuildQueryExamplesBlock:
    """프로필 query_examples → few-shot 블록."""

    def test_empty_when_no_examples(self):
        assert build_query_examples_block(None) == ""
        assert build_query_examples_block({}) == ""
        assert build_query_examples_block({"query_examples": []}) == ""

    def test_includes_question_sql_explanation(self):
        meta = {
            "query_examples": [
                {
                    "question": "서버들의 CPU/메모리 사용률",
                    "sql": "SELECT avg_val FROM cmm_metric_stat_m",
                    "explanation": "월별 피벗 패턴",
                }
            ]
        }
        block = build_query_examples_block(meta)
        assert "서버들의 CPU/메모리 사용률" in block
        assert "SELECT avg_val FROM cmm_metric_stat_m" in block
        assert "월별 피벗 패턴" in block
        assert "반드시 이 패턴을 따르세요" in block


class _FakeLLM:
    """messages를 캡처하고 더미 SQL을 반환하는 LLM 스텁."""

    def __init__(self):
        self.captured = None

    async def ainvoke(self, messages):
        self.captured = messages
        return SimpleNamespace(content="SELECT 1 FROM cmm_metric_stat_m LIMIT 100;")


class TestMultiDbExecutorInjectsExamples:
    """멀티 DB 경로(_generate_sql)가 프로필 예시를 시스템 프롬프트에 주입하는지(RC1)."""

    async def test_examples_injected_into_system_prompt(self):
        llm = _FakeLLM()
        schema_info = {
            "tables": {
                "cmm_metric_stat_m": {"columns": [{"name": "avg_val", "type": "numeric"}]}
            },
            "_structure_meta": {
                "query_guide": "가이드 텍스트",
                "query_examples": [
                    {
                        "question": "서버들의 CPU/메모리 사용률",
                        "sql": "SELECT ... FROM polestar.cmm_metric_stat_m s",
                        "explanation": "3중 조인 피벗",
                    }
                ],
            },
        }
        await _generate_sql(
            llm,
            {},
            schema_info,
            "공동존 전체 서버 사용률",
            100_000,
            db_engine="postgresql",
            db_id="polestar_cm_gp",
        )
        system_prompt = llm.captured[0].content
        assert "서버들의 CPU/메모리 사용률" in system_prompt
        assert "SELECT ... FROM polestar.cmm_metric_stat_m s" in system_prompt
        assert "3중 조인 피벗" in system_prompt

    async def test_unmapped_fields_aliased_by_korean_header(self):
        """미매핑 필드(사용률 등)를 한글 헤더명 그대로 alias하라는 블록이 프롬프트에 포함(폼필 채우기)."""
        llm = _FakeLLM()
        schema_info = {
            "tables": {"cmm_metric_stat_m": {"columns": [{"name": "avg_val", "type": "numeric"}]}},
            "_structure_meta": {"query_guide": "가이드", "query_examples": []},
        }
        await _generate_sql(
            llm, {}, schema_info, "공동존 전체 서버 사용률", 100_000,
            db_engine="postgresql", db_id="polestar_cm_gp",
            unmapped_fields=["CPU 평균", "메모리 최고"],
        )
        human_msg = llm.captured[-1].content
        assert "자동 매핑 실패 필드" in human_msg
        assert '"CPU 평균"' in human_msg
        assert '"메모리 최고"' in human_msg
        assert "alias" in human_msg  # 한글 alias 지시

    async def test_regular_field_aliased_by_korean_header(self):
        """멀티 DB에서 매핑된 식별 필드도 한글 헤더명으로 alias하라 지시(gp/yd 간 alias 일관성)."""
        llm = _FakeLLM()
        schema_info = {
            "tables": {"cmm_resource": {"columns": [{"name": "name", "type": "text"}]}},
            "_structure_meta": {},
        }
        await _generate_sql(
            llm, {}, schema_info, "공동존 서버", 1000,
            column_mapping={"서버 이름": "cmm_resource.name"},
            db_engine="postgresql", db_id="polestar_cm_gp",
        )
        msg = llm.captured[-1].content
        # 양식 필드명을 alias로 쓰라는 지시 + 임의 영문 alias 금지 문구
        assert '"서버 이름"' in msg
        assert "양식 필드명" in msg
        assert "영문 alias" in msg

    async def test_no_examples_no_crash(self):
        """query_examples 없으면 예시 블록 없이 정상 진행."""
        llm = _FakeLLM()
        schema_info = {
            "tables": {"cmm_resource": {"columns": [{"name": "id", "type": "int"}]}},
            "_structure_meta": {"query_guide": "가이드"},
        }
        sql = await _generate_sql(
            llm, {}, schema_info, "서버 조회", 1000,
            db_engine="postgresql", db_id="polestar_cm_gp",
        )
        assert sql  # 정상 반환
        assert "쿼리 예시" not in llm.captured[0].content


class TestMetricMappingDeferredToExamples:
    """field_mapper가 지어낸 metric-stat 컬럼을 강제 SELECT에서 제외하고 예시로 넘기는지(RC2)."""

    async def test_fake_metric_column_not_forced(self):
        llm = _FakeLLM()
        schema_info = {
            "tables": {
                "cmm_resource": {"columns": [{"name": "name", "type": "text"}]},
                "cmm_metric_stat_h": {"columns": [{"name": "avg_val", "type": "numeric"}]},
            },
            "_structure_meta": {"query_guide": "가이드", "query_examples": []},
        }
        column_mapping = {
            "서버 이름": "cmm_resource.name",
            "CPU 평균": "cmm_metric_stat_h.cpu_avg_val",  # field_mapper 환각 컬럼
            "메모리 평균": "cmm_metric_stat_h.mem_avg_val",
        }
        await _generate_sql(
            llm, {}, schema_info, "공동존 전체 서버 월간 사용률", 100_000,
            column_mapping=column_mapping,
            db_engine="postgresql", db_id="polestar_cm_gp",
        )
        human_msg = llm.captured[-1].content
        # 지어낸 metric 컬럼은 "반드시 SELECT에 포함" 강제 블록에 없어야 함
        assert "cmm_metric_stat_h.cpu_avg_val" not in human_msg
        assert "cmm_metric_stat_h.mem_avg_val" not in human_msg
        # 대신 성능 지표 안내(예시 피벗 패턴)로 넘어가야 함
        assert "성능 지표 필드" in human_msg
        assert "cmm_metric_stat_m" in human_msg  # 월별 강제 안내
        # 단순 컬럼(서버 이름→name)은 여전히 강제 포함
        assert "cmm_resource.name" in human_msg


class TestMetricUsageFieldSkip:
    """사용률 지표 필드는 매핑을 스킵해 LLM hang 트리거를 제거하고 예시에 위임하는지(RC2/hang 수정)."""

    @pytest.mark.parametrize(
        "field,expected",
        [
            ("CPU 평균", True), ("CPU 최고", True), ("메모리 평균", True),
            ("메모리 최고", True), ("메모리 사용률", True), ("디스크 사용률", True),
            ("메모리 용량", False), ("CPU 코어 수", False), ("서버 이름", False),
            ("호스트네임", False), ("IP주소", False), ("OS 종류", False),
        ],
    )
    def test_is_metric_usage_field(self, field, expected):
        from src.document.field_mapper import _is_metric_usage_field

        assert _is_metric_usage_field(field) is expected

    async def test_metric_only_fields_skip_llm_on_pivot_schema(self):
        """cmm_metric_stat 피벗 스키마 + 사용률 필드만 → LLM 호출 없이 미매핑(예시 위임)."""
        from unittest.mock import AsyncMock

        from src.document.field_mapper import perform_3step_mapping

        llm = AsyncMock()
        result, _ = await perform_3step_mapping(
            llm=llm,
            field_names=["CPU 평균", "메모리 최고"],
            field_mapping_hints=[],
            all_db_synonyms={},
            # 피벗 스키마 신호: cmm_metric_stat 컬럼 존재
            all_db_descriptions={
                "polestar_cm_gp": {"cmm_metric_stat_m.avg_val": "기간 평균값"}
            },
            priority_db_ids=["polestar_cm_gp"],
            eav_name_synonyms={},
            active_db_ids=["polestar_cm_gp"],
            global_synonyms={},
        )
        # hang을 유발하던 step 2.8/3 LLM 호출이 발생하지 않아야 함
        assert not llm.ainvoke.called
        assert result.column_mapping == {"CPU 평균": None, "메모리 최고": None}

    async def test_metric_field_mapped_on_direct_column_schema(self):
        """직접 컬럼 스키마(cmm_metric_stat 없음)에서는 사용률 필드 스킵 안 함 → 정상 매핑 시도."""
        from unittest.mock import AsyncMock, MagicMock
        import json as _json

        from src.document.field_mapper import perform_3step_mapping

        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(
            content=_json.dumps({
                "CPU 사용률": {"db_id": "metrics_db", "column": "cpu_metrics.usage_pct", "confidence": "high"}
            })
        )
        result, _ = await perform_3step_mapping(
            llm=llm,
            field_names=["CPU 사용률"],
            field_mapping_hints=[],
            all_db_synonyms={},
            all_db_descriptions={"metrics_db": {"cpu_metrics.usage_pct": "CPU 사용률 (%)"}},
            priority_db_ids=[],
            eav_name_synonyms={},
            active_db_ids=["metrics_db"],
            global_synonyms={},
        )
        # 피벗 스키마가 아니므로 스킵하지 않고 LLM으로 매핑되어야 함
        assert llm.ainvoke.called
        assert result.column_mapping["CPU 사용률"] == "cpu_metrics.usage_pct"


class _FakeCacheMgr:
    """get_schema_or_fetch가 _structure_meta 없는 bare 스키마만 반환하는 스텁."""

    async def get_schema_or_fetch(self, client, db_id):
        bare = {"tables": {"cmm_resource": {"columns": [{"name": "id", "type": "int"}]}}}
        return bare, True, "메모리", {}, {}

    async def get_structure_meta(self, db_id):
        return None


class _FakeClient:
    async def get_sample_data(self, table_name, limit=3):
        return []


class TestAnalyzeSchemaAttachesStructureMeta:
    """멀티 DB _analyze_schema가 _structure_meta(query_guide/examples)를 부착하는지 (핵심 근본원인)."""

    async def test_structure_meta_attached_from_manual_profile(self, monkeypatch):
        """get_schema_or_fetch가 _structure_meta를 안 붙여도, 수동 프로필에서 로드해 부착한다."""
        monkeypatch.setattr(
            "src.schema_cache.cache_manager.get_cache_manager",
            lambda cfg: _FakeCacheMgr(),
        )
        schema = await _analyze_schema(
            _FakeClient(), {}, db_id="polestar_cm_gp", app_config=MagicMock()
        )
        meta = schema.get("_structure_meta")
        assert meta is not None, "_structure_meta가 부착되지 않음(근본원인 회귀)"
        examples = meta.get("query_examples") or []
        assert examples, "프로필 query_examples가 비어있음"
        # 사용률(CPU/메모리) 예시가 실제로 포함됐는지 — 폼필 metric 조인의 정답 패턴
        questions = " ".join(ex.get("question", "") for ex in examples)
        assert "사용률" in questions
        assert meta.get("query_guide")  # 조인 가이드도 함께 로드


# ── C: 경로 패리티 가드 (D-067) ───────────────────────────────────────────────
# 단일 DB(query_generator)와 멀티 DB(multi_db_executor)가 예시·LIMIT 로직을 각자
# 복붙으로 갈라놓으면 D-066 같은 비대칭이 재발한다. 두 경로가 같은 헬퍼를 참조하고,
# 각자의 프롬프트 빌더가 예시를 실제로 주입하는지 못 박는다.


class TestCrossPathParity:
    """두 SQL 생성 경로가 예시/LIMIT 재료를 동일하게 쓰는지 검증."""

    def test_both_paths_reference_same_helpers(self):
        """query_generator·multi_db_executor가 동일한 공유 헬퍼 객체를 참조(복붙 분기 차단)."""
        import importlib

        # 패키지 __init__ 재노출이 submodule 이름을 가릴 수 있어 importlib로 실제 모듈 획득.
        qg = importlib.import_module("src.nodes.query_generator")
        mdb = importlib.import_module("src.nodes.multi_db_executor")
        assert qg.build_query_examples_block is mdb.build_query_examples_block
        assert qg.resolve_query_limit is mdb.resolve_query_limit

    def test_query_generator_guide_injects_examples(self):
        """단일 DB 경로(_format_structure_guide)도 예시를 가이드에 주입하는지."""
        import importlib

        qg = importlib.import_module("src.nodes.query_generator")
        meta = {
            "query_guide": "가이드",
            "query_examples": [
                {
                    "question": "서버들의 CPU/메모리 사용률",
                    "sql": "SELECT ... FROM cmm_metric_stat_m s",
                    "explanation": "월별 피벗",
                }
            ],
        }
        guide = qg._format_structure_guide(meta)
        assert "서버들의 CPU/메모리 사용률" in guide
        assert "cmm_metric_stat_m" in guide

    def test_all_query_limit_symmetric(self):
        """'전체' 조회 LIMIT 상향이 두 경로 공통 함수로 동일 적용."""
        assert resolve_query_limit("전체 서버", 1000) == 100_000
        assert resolve_query_limit("서버 10개", 1000) == 1000

    async def test_both_paths_emit_unmapped_field_alias_instruction(self):
        """미매핑 필드를 한글 헤더로 alias하라는 지시가 두 경로 모두에 있어야 함(폼필 채우기 비대칭 방지)."""
        import importlib

        qg = importlib.import_module("src.nodes.query_generator")
        # 단일 DB 경로 (_build_user_prompt)
        single = qg._build_user_prompt(
            parsed_requirements={},
            template_structure=None,
            error_message=None,
            previous_sql=None,
            column_mapping={"CPU 평균": None},
            schema_info={},
        )
        assert "자동 매핑 실패 필드" in single
        assert '"CPU 평균"' in single

        # 멀티 DB 경로 (_generate_sql)
        llm = _FakeLLM()
        await _generate_sql(
            llm, {}, {"tables": {}, "_structure_meta": {}}, "q", 1000,
            db_engine="postgresql", db_id="polestar_cm_gp",
            unmapped_fields=["CPU 평균"],
        )
        multi = llm.captured[-1].content
        assert "자동 매핑 실패 필드" in multi
        assert '"CPU 평균"' in multi

    def test_decimal_cast_example_engine_aware(self):
        """엔진별 소수 캐스트 예시 — PostgreSQL은 ::numeric, DB2는 CAST DECIMAL."""
        from src.db_adapters.polestar.assembler import decimal_cast_example

        assert "::numeric" in decimal_cast_example("postgresql")
        assert "DECIMAL" in decimal_cast_example("db2")
        assert "::numeric" not in decimal_cast_example("db2")  # DB2 문법 오류 방지
