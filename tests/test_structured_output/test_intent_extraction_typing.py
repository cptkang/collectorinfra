"""의도 추출 3표면 구조화 출력 적용 (Plan 79 E-3a·b·c · SPEC-intent-extraction-typing).

전부 대역 LLM으로 검증한다 — 실 LLM 0건(D-127 무관).

    S1 백엔드 off에서 기존 경로와 결과 동일
    S2 TaskSpec.agent 허용값 == SUBAGENT_REGISTRY 키 (사본 금지)
    S3 RouterDecision.intent 허용값 == allowed_intents() (플래그 양쪽)
    S4 잘못된 agent가 되먹임 재질의로 교정된다
    S5 _llm_decompose 소진 시 degraded 사유가 반환에 실린다 (F4)
    S6 소진 시에도 기존 단일 data_query 폴백 유지 (가용성)
    S7 input_parser 두 함수가 같은 스키마 — synonym_registration 키가 양쪽 존재
    S8 라우터 구조화 경로가 멀티 DB 2건 이상 보존 (§1.1 불변식)
"""

from __future__ import annotations

import importlib

import pytest
from langchain_core.messages import AIMessage

from src.nodes.schemas import ParsedRequirements
from src.orchestration.schemas import DecomposedPlan, TaskSpec, allowed_agents
from src.orchestration.subagents import SUBAGENT_REGISTRY
from src.prompts.semantic_router import allowed_intents
from src.routing.schemas import RouterDecision

sr = importlib.import_module("src.routing.semantic_router")
ip = importlib.import_module("src.orchestration.intent_planner")


class _FakeLLM:
    def __init__(self, *contents):
        self._c = list(contents)
        self.calls = 0

    async def ainvoke(self, messages, **_kw):
        i = min(self.calls, len(self._c) - 1)
        self.calls += 1
        return AIMessage(content=self._c[i])


def _md(body: str) -> str:
    return f"```json\n{body}\n```"


class _Cfg:
    def __init__(self, backend="instructor", retries=1):
        self.structured_output_backend = backend
        self.structured_output_max_retries = retries


# ─────────────────── S2·S3 — 열거형 단일 출처 ───────────────────

class TestEnumSingleSource:
    def test_agent_enum_derives_from_registry(self):
        """S2 — 사본을 두면 레지스트리에 agent가 늘 때 조용히 어긋난다."""
        assert allowed_agents() == frozenset(SUBAGENT_REGISTRY.keys())

    def test_fault_diagnosis_is_not_a_subagent(self):
        """fault_diagnosis는 그래프 노드이지 서브에이전트가 아니다 — 혼동 방지 고정."""
        assert "fault_diagnosis" not in allowed_agents()

    def test_router_intent_enum_derives_from_prompt_source(self):
        """S3 — 라우터 intent 정본은 WU-02가 만든 allowed_intents()다."""
        for flag in (False, True):
            expected = allowed_intents(fault_diagnosis_enabled=flag)
            for name in expected:
                assert RouterDecision.validate_intent_against(
                    name, fault_diagnosis_enabled=flag
                )
            assert not RouterDecision.validate_intent_against(
                "prosess_query", fault_diagnosis_enabled=flag
            )

    def test_bad_agent_is_rejected_by_contract(self):
        """오타 agent는 계약에서 걸린다(종전에는 dict라 런타임까지 살았다)."""
        with pytest.raises(Exception):
            TaskSpec(task_id="t1", agent="prosess_query", sub_query="x")


# ─────────────── S1·S4·S5·S6 — DAG 분해(E-3a) ───────────────

class TestDecomposeStructured:
    @pytest.mark.asyncio
    async def test_backend_off_uses_legacy_path(self):
        """S1 — off면 기존 정규식 파싱 결과와 같다."""
        payload = _md('{"tasks":[{"task_id":"t1","agent":"data_query",'
                      '"sub_query":"서버 조회","depends_on":[],"input_from":[],"order":1}]}')
        out = await ip._llm_decompose(_FakeLLM(payload), "서버 조회", _Cfg(backend="none"))
        assert [t["agent"] for t in out["tasks"]] == ["data_query"]
        assert "degraded" not in out

    @pytest.mark.asyncio
    async def test_bad_agent_is_repaired_by_reask(self):
        """S4 ★ — 오타 agent가 되먹임 재질의로 교정된다."""
        bad = _md('{"tasks":[{"task_id":"t1","agent":"prosess_query","sub_query":"프로세스","order":1}]}')
        good = _md('{"tasks":[{"task_id":"t1","agent":"process_query","sub_query":"프로세스",'
                   '"depends_on":[],"input_from":[],"order":1}]}')
        llm = _FakeLLM(bad, good)
        out = await ip._llm_decompose(llm, "프로세스", _Cfg())
        assert [t["agent"] for t in out["tasks"]] == ["process_query"], (
            "되먹임 재질의로 교정되지 않았다"
        )
        assert llm.calls == 2, f"재질의가 일어나지 않았다({llm.calls}회)"
        assert out["tasks"][0]["status"] == "pending", "status 부여가 빠졌다"

    @pytest.mark.asyncio
    async def test_exhaustion_reports_reason_and_keeps_fallback(self):
        """S5·S6 ★ — 침묵 폴백 금지(F4). 사유를 싣되 가용성은 유지한다."""
        bad = _md('{"tasks":[{"task_id":"t1","agent":"prosess_query","sub_query":"x","order":1}]}')
        out = await ip._llm_decompose(_FakeLLM(bad, bad, bad), "질의", _Cfg(retries=1))

        assert out.get("degraded"), "강등 사유가 반환에 실리지 않았다 — 침묵 폴백이다"
        d = out["degraded"][0]
        assert d["stage"].endswith("_llm_decompose")
        assert d["attempts"] >= 2
        assert "prosess_query" in d["detail"], "무엇이 틀렸는지 사유에 없다"

        assert [t["agent"] for t in out["tasks"]] == ["data_query"], (
            "사유를 남기느라 가용성을 잃었다 — 폴백은 유지되어야 한다"
        )


# ─────────────── S8 — 라우터(E-3b) 멀티 DB 보존 ───────────────

class TestRouterStructured:
    @pytest.mark.asyncio
    async def test_multi_db_survives_structured_path(self, monkeypatch):
        """S8 ★ — 구조화 경로가 멀티 DB 선택을 축소하면 §1.1 불변식 위배다."""
        monkeypatch.setattr(sr, "_structured_backend", lambda: "instructor")
        monkeypatch.setattr(sr, "_structured_max_retries", lambda: 1)
        doms = sr.DB_DOMAINS[:2]
        payload = _md(
            '{"intent":"data_query","databases":['
            f'{{"db_id":"{doms[0].db_id}","relevance_score":0.9,"sub_query_context":"A"}},'
            f'{{"db_id":"{doms[1].db_id}","relevance_score":0.7,"sub_query_context":"B"}}]}}'
        )
        out = await sr._llm_classify(_FakeLLM(payload), "복합 질의", doms)
        ids = [d["db_id"] for d in out["databases"]]
        assert len(ids) == 2, f"멀티 DB가 축소됐다: {ids}"
        ctxs = [d["sub_query_context"] for d in out["databases"]]
        assert len(set(ctxs)) == 2, f"sub_query_context 분리가 사라졌다: {ctxs}"


# ─────────────── S7 — 요구사항 추출(E-3c) 대칭 ───────────────

class TestRequirementsSymmetry:
    def test_shared_schema_has_synonym_registration(self):
        """S7 — 공유 스키마가 비대칭을 구조적으로 없앤다."""
        assert "synonym_registration" in ParsedRequirements.model_fields

    def test_schema_defaults_are_complete(self):
        """빈 입력에서도 모든 키가 존재한다(setdefault 사후 보정 의존 제거)."""
        keys = set(ParsedRequirements().model_dump().keys())
        expected = {
            "query_targets", "output_format", "filter_conditions", "time_range",
            "aggregation", "limit", "field_mapping_hints", "target_db_hints",
            "synonym_registration",
        }
        assert expected <= keys, f"누락: {expected - keys}"

    @pytest.mark.asyncio
    async def test_both_parsers_emit_synonym_registration(self):
        """S7 ★ — 두 경로 모두 키를 갖는다(종전 CSV 경로에만 없었다)."""
        from src.nodes.input_parser import (
            _parse_natural_language,
            _parse_natural_language_with_csv,
        )
        payload = _md('{"query_targets":["서버"],"output_format":"text"}')

        a = await _parse_natural_language(_FakeLLM(payload), "서버 조회")
        b = await _parse_natural_language_with_csv(
            _FakeLLM(payload), "서버 조회", "col1,col2\n1,2", sheet_name="시트1"
        )
        assert "synonym_registration" in a
        assert "synonym_registration" in b, (
            "CSV 경로에 synonym_registration 키가 없다 — 비대칭이 남아 있다."
        )


class TestDecomposedPlanContract:
    def test_empty_plan_is_valid_but_signals(self):
        """빈 tasks는 계약상 유효하나 호출부가 폴백해야 한다."""
        assert DecomposedPlan().tasks == []
