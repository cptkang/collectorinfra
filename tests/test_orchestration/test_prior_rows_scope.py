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
    cfg.text2sql.generic_llm_mapping = False  # P3(D-090) 기본 OFF — 범용 기간 힌트 미주입
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
    async def test_prior_rows_passed_to_semantic_compile_as_scope(
        self, sample_state, monkeypatch
    ):
        """선행 결과 한정을 트랙 C에 server_scope로 결정적 전달한다 (D-099).

        과거에는 SMQ가 스코프를 표현하지 못해 트랙 C를 우회했으나(D-086), 조립기가 HAVING으로
        스코프를 강제하게 되어(D-099) 이 형태도 결정적 조립 대상이다. 프롬프트 지시에만
        의존하면 LLM이 WHERE 배치·모순 alias 변종을 만들어 침묵 0건/오답이 반복된다
        (D-096·D-098 실측).
        """
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

        compile_mock.assert_called_once()
        scope = compile_mock.call_args.kwargs["server_scope"]
        assert scope == ("name", ["SV-WEB-001", "SV-BATCH-009"])


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


class TestInjectionBlockGeneralization:
    """P1(D-088): 공용 주입 블록에 특정 DB 스키마 리터럴이 없고, 폴스타 통계 블록은
    폴스타 DB에만 주입되는지 검증(프로필 부재 DB 오지시 주입 차단, L1/L2 일반화)."""

    def test_prior_rows_block_has_no_schema_literal(self):
        """build_prior_rows_block에는 특정 DB 테이블 리터럴(cmm_/server.)이 없어야 한다."""
        block = build_prior_rows_block(PRIOR_ROWS)
        assert "cmm_" not in block
        assert "server." not in block
        # 일반 환각 금지 원칙은 유지
        assert "환각 금지" in block

    @pytest.mark.asyncio
    async def test_stat_block_injected_for_polestar(self, sample_state):
        """폴스타 DB + 기간 표현이면 통계 테이블 강제 블록이 주입된다(동작 보존)."""
        sample_state["user_query"] = "지난달 CPU 사용률이 높은 서버 목록"
        sample_state["active_db_id"] = "polestar"
        cfg = _mock_config()
        cfg.get_polestar_db_ids.return_value = {"polestar"}
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )

        await query_generator(sample_state, llm=mock_llm, app_config=cfg)

        human_content = mock_llm.ainvoke.call_args[0][0][-1].content
        assert "기간 조건" in human_content

    @pytest.mark.asyncio
    async def test_stat_block_not_injected_for_non_polestar(self, sample_state):
        """프로필 부재(비폴스타) DB는 기간 표현이 있어도 폴스타 통계 블록 미주입 —
        cmm_metric_stat_m 오지시가 타 DB 프롬프트에 새지 않는다(L2)."""
        sample_state["user_query"] = "지난달 CPU 사용률이 높은 서버 목록"
        sample_state["active_db_id"] = "generic_mon"
        cfg = _mock_config()
        cfg.get_polestar_db_ids.return_value = {"polestar"}
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="```sql\nSELECT hostname FROM servers LIMIT 10;\n```"
        )

        await query_generator(sample_state, llm=mock_llm, app_config=cfg)

        human_content = mock_llm.ainvoke.call_args[0][0][-1].content
        assert "기간 조건" not in human_content
        assert "cmm_metric_stat_m" not in human_content


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
