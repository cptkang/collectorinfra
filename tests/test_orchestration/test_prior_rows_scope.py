"""선행 task 결과(prior_rows) 서버 스코프 강제 검증 (D-086).

배경(2026-07-18 실측): "현재 활성 상태인 심각 알람이 있는 서버들의 최근 1개월
CPU 사용률" 질의가 alarm_query(t1)→data_query(t2)로 분해됐으나, t2가 t1의 서버
목록을 전달받지 못해(prior_rows 생성만 되고 소비처 전무 — 죽은 배선) 알람 조건을
재표현하다 resource_type='alarm.Alarm' 환각으로 0건 → CPU 사용률 미조회.

검증 범위:
- build_prior_rows_block 헬퍼 (식별 컬럼 우선순위·이스케이프·빈 입력)
- 단일 DB 경로(query_generator) 프롬프트 주입 + 결정적 컴파일 우회
- 멀티 DB 경로(_generate_sql) 대칭 주입 (D-066)
- _coerce_alarm_intent가 데이터 의존(input_from) task를 뒤집지 않는지
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.nodes.multi_db_executor import _generate_sql
from src.nodes.query_generator import query_generator
from src.orchestration.intent_planner import _coerce_alarm_intent
from src.utils.query_gen_common import build_prior_rows_block

PRIOR_ROWS = {"t1": [{"server_name": "SV-WEB-001"}, {"server_name": "SV-BATCH-009"}]}


class TestBuildPriorRowsBlock:
    """build_prior_rows_block 헬퍼 단위 검증."""

    def test_empty_inputs_return_empty(self):
        assert build_prior_rows_block(None) == ""
        assert build_prior_rows_block({}) == ""
        assert build_prior_rows_block({"t1": []}) == ""

    def test_name_column_renders_in_clause(self):
        block = build_prior_rows_block(PRIOR_ROWS)
        assert "선행 작업 결과 서버 스코프" in block
        assert "name IN ('SV-WEB-001', 'SV-BATCH-009')" in block
        # 알람 조건 재표현 금지(환각 차단) 지시 포함
        assert "환각 금지" in block
        assert "alarm" in block.lower() or "알람" in block

    def test_hostname_takes_priority_over_name(self):
        """hostname류 컬럼이 있으면 name류보다 우선한다 (폴스타 name≠hostname, D-061)."""
        block = build_prior_rows_block(
            {"t1": [{"hostname": "svweb001", "server_name": "SV-WEB-001"}]}
        )
        assert "hostname IN ('svweb001')" in block
        assert "SV-WEB-001" not in block

    def test_single_quote_escaped(self):
        block = build_prior_rows_block({"t1": [{"name": "O'Brien"}]})
        assert "'O''Brien'" in block

    def test_rows_without_identity_columns_return_empty(self):
        assert build_prior_rows_block({"t1": [{"cpu_avg": 72.1}]}) == ""

    def test_duplicate_values_deduped(self):
        block = build_prior_rows_block(
            {"t1": [{"name": "A"}], "t2": [{"name": "A"}, {"name": "B"}]}
        )
        assert block.count("'A'") == 2  # 목록 표시 1회 + IN 절 1회
        assert "name IN ('A', 'B')" in block


def _mock_config() -> MagicMock:
    """plain LLM 경로로 진입하는 최소 설정 mock을 만든다."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.text2sql.semantic_compose = False
    cfg.text2sql.multi_candidate = False
    cfg.synonym.value_retrieval = False
    cfg.get_polestar_db_ids.return_value = None
    return cfg


class TestSingleDbPathInjection:
    """단일 DB 경로(query_generator)가 prior_rows 블록을 프롬프트에 주입하는지."""

    @pytest.mark.asyncio
    async def test_prior_rows_injected_into_user_prompt(self, sample_state):
        sample_state["prior_rows"] = PRIOR_ROWS
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )

        await query_generator(sample_state, llm=mock_llm, app_config=_mock_config())

        human_content = mock_llm.ainvoke.call_args[0][0][-1].content
        assert "선행 작업 결과 서버 스코프" in human_content
        assert "name IN ('SV-WEB-001', 'SV-BATCH-009')" in human_content

    @pytest.mark.asyncio
    async def test_no_prior_rows_no_block(self, sample_state):
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )

        await query_generator(sample_state, llm=mock_llm, app_config=_mock_config())

        human_content = mock_llm.ainvoke.call_args[0][0][-1].content
        assert "선행 작업 결과 서버 스코프" not in human_content

    @pytest.mark.asyncio
    async def test_prior_rows_bypasses_semantic_compile(self, sample_state, monkeypatch):
        """SMQ는 선행 결과 한정을 표현할 수 없으므로 트랙 C를 우회해야 한다."""
        sample_state["prior_rows"] = PRIOR_ROWS
        cfg = _mock_config()
        cfg.text2sql.semantic_compose = True

        # src.nodes.__init__이 동명 함수(query_generator)로 모듈 속성을 가리므로
        # sys.modules에서 모듈 객체를 직접 가져와 패치한다.
        import sys

        qg_module = sys.modules["src.nodes.query_generator"]
        compile_mock = AsyncMock(return_value=(None, None, None))
        monkeypatch.setattr(qg_module, "compile_from_nl", compile_mock)
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )

        await query_generator(sample_state, llm=mock_llm, app_config=cfg)

        compile_mock.assert_not_called()


class _FakeLLM:
    """messages를 캡처하고 더미 SQL을 반환하는 LLM 스텁."""

    def __init__(self):
        self.captured = None

    async def ainvoke(self, messages):
        self.captured = messages
        return SimpleNamespace(content="SELECT 1 FROM cmm_metric_stat_m LIMIT 100;")


class TestMultiDbPathInjection:
    """멀티 DB 경로(_generate_sql)가 동일 블록을 주입하는지 (D-066 대칭)."""

    async def test_prior_block_injected(self):
        llm = _FakeLLM()
        schema_info = {
            "tables": {"cmm_metric_stat_m": {"columns": [{"name": "avg_val", "type": "numeric"}]}},
            "_structure_meta": {},
        }
        await _generate_sql(
            llm, {}, schema_info, "선행 결과 서버들의 CPU 사용률", 1000,
            db_engine="postgresql", db_id="polestar_cm_gp",
            prior_block=build_prior_rows_block(PRIOR_ROWS),
        )
        human_msg = llm.captured[-1].content
        assert "선행 작업 결과 서버 스코프" in human_msg
        assert "name IN ('SV-WEB-001', 'SV-BATCH-009')" in human_msg

    async def test_no_prior_block_no_injection(self):
        llm = _FakeLLM()
        schema_info = {
            "tables": {"cmm_resource": {"columns": [{"name": "id", "type": "int"}]}},
            "_structure_meta": {},
        }
        await _generate_sql(
            llm, {}, schema_info, "서버 조회", 1000,
            db_engine="postgresql", db_id="polestar_cm_gp",
        )
        assert "선행 작업 결과 서버 스코프" not in llm.captured[-1].content


class TestCoerceAlarmIntentDependencyGuard:
    """데이터 의존(input_from) task는 알람 어휘가 남아도 alarm_query로 뒤집지 않는다."""

    def test_dependent_task_not_coerced(self):
        tasks = [{
            "task_id": "t2",
            "agent": "data_query",
            "sub_query": "심각 알람이 있는 서버들의 최근 1개월 CPU 사용률 조회",
            "depends_on": ["t1"],
            "input_from": ["t1"],
        }]
        assert _coerce_alarm_intent(tasks)[0]["agent"] == "data_query"

    def test_independent_alarm_task_still_coerced(self):
        """기존 교정 동작(D-076 후속3)은 유지된다 — 독립 task는 여전히 교정."""
        tasks = [{
            "task_id": "t1",
            "agent": "data_query",
            "sub_query": "최근 발생한 알람 목록 조회",
            "depends_on": [],
            "input_from": [],
        }]
        assert _coerce_alarm_intent(tasks)[0]["agent"] == "alarm_query"
