"""라우터 intent/DB 2단 분리 (WU-D2 / `plans/79` 트랙 B · SPEC-router-two-stage).

## 이 파일이 지키는 것

트랙 B는 **보류 해제 착수**다(D-170 후속은 이월 유지였다). 구조가 서더라도 아래 넷은
여전히 미검증이며(SPEC 「미검증으로 남는 것」), 그래서 **플래그 off가 기본**이다:

    M-1 이득/손해 자체 — 근거가 모델 크기 종속(1.5B −33.6 / 9B +11.2)
    M-2 컨텍스트 대역폭 손실 — 완화책의 효과 미측정
    M-3 조기 차단 임계 — 자기보고 값에 교정 기반 없음
    M-4 비용 — 호출 1회 → 2회

따라서 이 테스트가 단언하는 것은 **"정확해졌다"가 아니라 "구조가 규율을 지킨다"** 이다:
플래그 off 비트동일 · 멀티 DB 불변식 보존 · D-004 준수 · 조기 차단 기본 off.

실 LLM은 부르지 않는다(D-127) — mock 전용.
"""

from __future__ import annotations

import importlib
import string

import pytest

from src.config import load_config
from src.prompts.semantic_router import (
    INTENTS_WITHOUT_DATABASES,
    SEMANTIC_ROUTER_STAGE1_INTENT_TEMPLATE,
    SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE,
    STAGE2_INTENT_SECTIONS,
)
from src.routing.domain_config import DB_DOMAINS
from src.routing.schemas import DatabaseSelection, IntentDecision

# 패키지 `__init__`가 동명 함수를 re-export해 모듈을 가린다 — importlib만 모듈을 준다.
router = importlib.import_module("src.routing.semantic_router")


def _domains():
    return list(DB_DOMAINS.values()) if isinstance(DB_DOMAINS, dict) else list(DB_DOMAINS)


class _ScriptedLLM:
    """호출 순서대로 정해진 응답을 돌려주는 LLM 대역. **호출 횟수를 센다.**"""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls: list[str] = []

    async def ainvoke(self, messages):
        self.calls.append(messages[0].content)
        if not self._responses:
            raise AssertionError("대역에 준비된 응답보다 많이 호출됐다")
        return type("R", (), {"content": self._responses.pop(0)})()


@pytest.fixture
def two_stage_on(monkeypatch):
    monkeypatch.setenv("ROUTER_TWO_STAGE_ENABLED", "true")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


DB_JSON = """{"databases": [
  {"db_id": "%s", "relevance_score": 0.9, "reason": "r", "sub_query_context": "서버 목록 조회"}
]}"""


async def _classify(llm, query="서버 목록 보여줘", **kw):
    return await router._llm_classify(llm, query, _domains(), **kw)


# ──────────────────────────────────────────────
# 플래그 off — 회귀 0 (T7)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flag_off_uses_single_call_path():
    """★ 기본(off)이면 **단일 호출 경로**가 그대로 실행된다 — 호출 1회."""
    load_config.cache_clear()
    llm = _ScriptedLLM('{"intent": "data_query", ' + DB_JSON[1:])
    result = await _classify(llm)
    assert len(llm.calls) == 1
    assert result["intent"] == "data_query"
    assert "two_stage" not in result


@pytest.mark.asyncio
async def test_flag_off_prompt_is_the_single_template():
    """off 경로가 받는 시스템 프롬프트가 **단일 템플릿 렌더**와 동일하다(비트동일)."""
    load_config.cache_clear()
    llm = _ScriptedLLM('{"intent": "data_query", "databases": []}')
    await _classify(llm)
    expected = router._build_router_prompt(_domains(), db_descriptions=None)
    assert llm.calls[0] == expected


def test_flag_default_is_off():
    """`plans/80` §5.4-③ — 신규 플래그의 기본값은 현행 동작이다."""
    load_config.cache_clear()
    cfg = load_config().router
    assert cfg.two_stage_enabled is False
    assert cfg.early_stop_enabled is False
    assert cfg.min_confidence is None
    assert cfg.confidence_source == "self_report"


# ──────────────────────────────────────────────
# 2단 경로 — 호출 구조 (T5)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_stage_makes_two_calls(two_stage_on):
    """★ 켜면 **1단(intent) → 2단(DB)** 두 번 호출한다."""
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.9}", DB_JSON % dom)
    result = await _classify(llm)
    assert len(llm.calls) == 2
    assert result["two_stage"] is True
    assert result["stage2_called"] is True
    assert result["intent"] == "data_query"
    assert [d["db_id"] for d in result["databases"]] == [dom]


@pytest.mark.asyncio
async def test_stage1_prompt_does_not_ask_for_databases(two_stage_on):
    """1단계는 DB를 고르지 않는다 — 상충하는 지시를 주면 분류가 떨어진다(B-0 ①)."""
    llm = _ScriptedLLM("general_inference\n{\"confidence\": 0.8}")
    await _classify(llm)
    stage1 = llm.calls[0]
    assert "데이터베이스를 고르지 않습니다" in stage1
    assert "## 사용 가능한 데이터베이스" not in stage1


@pytest.mark.asyncio
async def test_stage2_prompt_states_intent_is_already_fixed(two_stage_on):
    """2단계는 의도를 **다시 판단하지 않는다**(B-1-1)."""
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("alarm_query\n{\"confidence\": 0.9}", DB_JSON % dom)
    await _classify(llm)
    stage2 = llm.calls[1]
    assert "이미 확정" in stage2
    assert "alarm_query" in stage2


@pytest.mark.asyncio
async def test_stage2_carries_only_the_relevant_intent_section(two_stage_on):
    """★ B-2 완화 — 확정된 intent의 절만 넘긴다.

    전량을 넘기면 프롬프트 축소 이득(B-0 ③)이 사라지고, 아무것도 안 넘기면 대역폭 손실이
    커진다. **효과는 미측정**이다(SPEC M-2).
    """
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("alarm_query\n{\"confidence\": 0.9}", DB_JSON % dom)
    await _classify(llm)
    stage2 = llm.calls[1]
    assert "## 알람 조회 판단" in stage2          # 해당 intent 절은 있고
    assert "## 캐시 관리 의도 분류" not in stage2   # 다른 intent 절은 없다


@pytest.mark.asyncio
async def test_prompt_split_actually_shrinks_each_call(two_stage_on):
    """B-0 ③ — 각 단계 프롬프트가 단일 프롬프트보다 짧다(주의 분산 완화의 전제)."""
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.9}", DB_JSON % dom)
    await _classify(llm)
    single = len(router._build_router_prompt(_domains(), db_descriptions=None))
    assert len(llm.calls[0]) < single
    assert len(llm.calls[1]) < single


# ──────────────────────────────────────────────
# 2단계 생략 (Q3) — 의도 종류로 판정
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("intent", sorted(INTENTS_WITHOUT_DATABASES))
async def test_intents_without_databases_skip_stage2(two_stage_on, intent):
    """★ DB를 고를 대상이 없는 의도는 **2단계를 부르지 않는다**(호출 1회).

    조기 차단과 달리 신뢰도가 아니라 **의도 종류**로 판정하므로 근거가 확실하다.
    """
    llm = _ScriptedLLM(f"{intent}\n{{\"confidence\": 0.9}}")
    result = await _classify(llm)
    assert len(llm.calls) == 1
    assert result["stage2_called"] is False
    assert result["stage2_skipped_reason"] == "intent_without_databases"
    assert result["databases"] == []


# ──────────────────────────────────────────────
# 신뢰도 2축 · 조기 차단 (T6 · B-1-5 · B-2-1)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_and_db_confidence_are_separate_axes(two_stage_on):
    """★ B-1-5 — intent 신뢰도와 DB 관련도가 **각각** 산출된다.

    현행 단일 호출은 `relevance_score` 하나가 두 판단을 덮는다(트랙 C-1 ③).
    """
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.42}", DB_JSON % dom)
    result = await _classify(llm)
    assert result["intent_confidence"] == pytest.approx(0.42)      # intent 축
    assert result["databases"][0]["relevance_score"] == 0.9         # DB 축
    assert result["confidence_source"] == "self_report"


@pytest.mark.asyncio
async def test_early_stop_is_off_by_default(two_stage_on):
    """★ 조기 차단은 **기본 off** — 자기보고 확신도에 교정 기반이 없다(SPEC M-3).

    저신뢰(0.05)여도 켜지 않았으면 2단계로 간다.
    """
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.05}", DB_JSON % dom)
    result = await _classify(llm)
    assert len(llm.calls) == 2
    assert result["stage2_called"] is True


@pytest.mark.asyncio
async def test_early_stop_needs_an_explicit_threshold(two_stage_on, monkeypatch):
    """임계 미설정이면 켜도 차단하지 않는다 — 기본 숫자를 두면 근거 없는 값이 판단에 관여한다."""
    monkeypatch.setenv("ROUTER_EARLY_STOP_ENABLED", "true")
    load_config.cache_clear()
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.05}", DB_JSON % dom)
    await _classify(llm)
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_early_stop_skips_stage2_when_configured(two_stage_on, monkeypatch):
    """★ B-2-1 — 저신뢰면 **2단계 호출 없이** 중단한다(호출 0회).

    저신뢰 질의는 어차피 결과를 쓸 수 없으므로 2단계가 낭비다. 단일 호출은 다 생성한 뒤에 버린다.
    """
    monkeypatch.setenv("ROUTER_EARLY_STOP_ENABLED", "true")
    monkeypatch.setenv("ROUTER_MIN_CONFIDENCE", "0.5")
    load_config.cache_clear()
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.05}")
    result = await _classify(llm)
    assert len(llm.calls) == 1
    assert result["stage2_called"] is False
    assert result["stage2_skipped_reason"] == "low_intent_confidence"


@pytest.mark.asyncio
async def test_logprob_source_reports_instead_of_silently_degrading(two_stage_on, monkeypatch, caplog):
    """★ 침묵 강등 금지 — 현행 평면은 logprobs 원천 불가다(FabriX KBGenAI).

    설정을 잘못 두면 사유를 남기고 신뢰도를 **미산출**로 둔다. 자기보고로 조용히 갈아타면
    "logprob을 쓰고 있다"는 오해가 생긴다.
    """
    monkeypatch.setenv("ROUTER_CONFIDENCE_SOURCE", "logprob")
    load_config.cache_clear()
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.9}", DB_JSON % dom)
    with caplog.at_level("WARNING", logger="src.routing.semantic_router"):
        result = await _classify(llm)
    assert result["intent_confidence"] is None
    assert "logprobs" in " ".join(r.getMessage() for r in caplog.records)


def test_confidence_source_has_exactly_one_swap_point():
    """★ 트랙 C 재개 비용 — 소스 **분기**가 한 함수에만 있어야 한다.

    vLLM 전환 시 `logprob`을 실제로 읽도록 바꿀 자리가 흩어져 있으면 한쪽만 고쳐진다.
    "logprob"이라는 값으로 분기하는 함수가 `_intent_confidence` 하나임을 고정한다.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/routing/semantic_router.py").read_text())
    branching = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(c, ast.Constant) and c.value == "logprob"
            for c in ast.walk(n)
        )
    }
    assert branching == {"_intent_confidence"}, (
        f"신뢰도 소스 분기가 여러 곳이다: {branching} — 트랙 C 재개 시 한쪽만 고쳐진다"
    )


# ──────────────────────────────────────────────
# 불변식 · D-004 (T7 · 79 §1.1 · B-0-1)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_db_selection_is_preserved(two_stage_on):
    """★ 79 §1.1 불변식 — 2단 분리가 **멀티 DB 선택을 축소하지 않는다**.

    단일 선택으로 줄이면 회귀다(트랙 D를 배제한 바로 그 이유).
    """
    doms = _domains()
    if len(doms) < 2:
        pytest.skip("멀티 DB 검증에는 도메인이 2개 이상 필요하다")
    a, b = doms[0].db_id, doms[1].db_id
    payload = (
        '{"databases": ['
        f'{{"db_id": "{a}", "relevance_score": 0.9, "sub_query_context": "서버 사양 조회"}},'
        f'{{"db_id": "{b}", "relevance_score": 0.7, "sub_query_context": "VM 정보 조회"}}'
        "]}"
    )
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.9}", payload)
    result = await _classify(llm)
    assert {d["db_id"] for d in result["databases"]} == {a, b}


@pytest.mark.asyncio
async def test_sub_query_context_separation_is_preserved(two_stage_on):
    """★ 79 §1.1 — DB별 `sub_query_context` 분리가 보존된다(D-004 정합)."""
    doms = _domains()
    if len(doms) < 2:
        pytest.skip("멀티 DB 검증에는 도메인이 2개 이상 필요하다")
    a, b = doms[0].db_id, doms[1].db_id
    payload = (
        '{"databases": ['
        f'{{"db_id": "{a}", "relevance_score": 0.9, "sub_query_context": "서버 사양 조회"}},'
        f'{{"db_id": "{b}", "relevance_score": 0.7, "sub_query_context": "VM 정보 조회"}}'
        "]}"
    )
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.9}", payload)
    result = await _classify(llm)
    contexts = {d["db_id"]: d["sub_query_context"] for d in result["databases"]}
    assert contexts[a] != contexts[b]
    assert contexts[a] == "서버 사양 조회"


@pytest.mark.asyncio
async def test_user_specified_db_is_independent_of_intent(two_stage_on):
    """★ 수용 기준 3 — 사용자 DB 직접 지정이 **intent와 무관하게** 반영된다.

    2단계 프롬프트가 그 규칙을 intent와 독립적으로 보유해야 성립한다.
    """
    dom = _domains()[0].db_id
    for intent in ("data_query", "alarm_query"):
        llm = _ScriptedLLM(
            f"{intent}\n{{\"confidence\": 0.9}}",
            '{"databases": [{"db_id": "%s", "relevance_score": 1.0, "user_specified": true}]}' % dom,
        )
        result = await _classify(llm)
        assert "## 사용자 직접 DB 지정 규칙" in llm.calls[1]
        assert result["databases"][0]["user_specified"] is True


def test_stage2_is_llm_not_deterministic_rules():
    """★ D-004 — 2단계도 **LLM**이 판단한다.

    위치 힌트(`_LOCATION_DB_HINTS`)로 DB를 고르면 `db_registry.yaml:22-25`가 금지한
    "키워드 기반 사전 분류" 재도입이 된다. 힌트는 폴백·보강뿐이다.
    """
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(router._llm_classify_two_stage))
    tree = ast.parse(src)
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    # 2단계는 LLM 호출 또는 구조화 호출을 반드시 거친다.
    assert "try_structured_call" in called
    # 위치 힌트로 DB를 직접 고르는 결정적 경로가 없다.
    assert "_LOCATION_DB_HINTS" not in src
    assert "location_db_hints" not in src


def test_both_paths_share_the_entry_validator():
    """★ E-2 대칭 — 단일·2단 경로가 **같은 검증 함수**를 쓴다.

    갈라 두면 항목 단위 격리가 한쪽에만 적용되고, 그게 이 저장소의 반복 실수다.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/routing/semantic_router.py").read_text())
    users = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "_validate_db_entries" in ast.dump(n)
    ]
    assert "_llm_classify" in users
    assert "_llm_classify_two_stage" in users


@pytest.mark.asyncio
async def test_invalid_entry_is_isolated_in_two_stage_path(two_stage_on):
    """한 항목의 형식 오류가 2단 경로에서도 분류 전체를 폐기하지 않는다(E-2)."""
    dom = _domains()[0].db_id
    payload = (
        '{"databases": ['
        '{"db_id": "%s", "relevance_score": "높음"},'
        '{"db_id": "%s", "relevance_score": 0.8}'
        "]}" % (dom, dom)
    )
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.9}", payload)
    result = await _classify(llm)
    assert len(result["databases"]) == 1
    assert result["dropped"][0]["reason"] == "invalid_relevance_score"


# ──────────────────────────────────────────────
# 1단계 파싱 · 계약 (T3)
# ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,label,conf",
    [
        ("data_query\n{\"confidence\": 0.9}", "data_query", 0.9),
        ("alarm_query", "alarm_query", None),
        ("`data_query`\n{\"confidence\": 0.5}", "data_query", 0.5),
        ('"alarm_query"', "alarm_query", None),
        ("data_query\n설명이 섞임", "data_query", None),
        ("", "", None),
    ],
)
def test_stage1_parsing_is_forgiving_about_the_second_line(raw, label, conf):
    """첫 줄만 보면 되므로 **JSON 파싱 실패가 라벨을 버리지 않는다**(E-2와 같은 원칙)."""
    got_label, got_conf = router._parse_stage1(raw)
    assert got_label == label
    assert got_conf == conf


def test_intent_decision_validates_against_the_canonical_set():
    """intent 허용값 정본은 `allowed_intents()`다 — 사본을 만들지 않는다(D-053)."""
    assert IntentDecision.validate_intent_against("data_query", fault_diagnosis_enabled=False)
    assert not IntentDecision.validate_intent_against("없는의도", fault_diagnosis_enabled=False)
    assert not IntentDecision.validate_intent_against(
        "fault_diagnosis", fault_diagnosis_enabled=False
    )


def test_database_selection_has_no_intent_field():
    """2단계 계약에 `intent`가 없다 — 의도는 이미 확정됐다(B-1-1)."""
    assert "intent" not in DatabaseSelection.model_fields
    assert "databases" in DatabaseSelection.model_fields


@pytest.mark.asyncio
async def test_unknown_intent_is_coerced_not_trusted(two_stage_on):
    """★ E-1 — 1단계가 환각 라벨을 내도 허용 집합 밖이면 강등된다.

    2단 분리라고 해서 E-1을 건너뛰지 않는다.
    """
    dom = _domains()[0].db_id
    llm = _ScriptedLLM("데이터조회\n{\"confidence\": 0.9}", DB_JSON % dom)
    result = await _classify(llm)
    assert result["intent"] == "data_query"


# ──────────────────────────────────────────────
# 프롬프트 조립 (T2 · D-053)
# ──────────────────────────────────────────────

def test_stage_templates_reuse_shared_sections_no_copies():
    """★ D-053 — 두 단계 템플릿이 **같은 절 상수**를 재사용한다(텍스트 사본 0)."""
    from src.prompts import semantic_router as sr

    assert sr._S_ALARM in SEMANTIC_ROUTER_STAGE1_INTENT_TEMPLATE
    assert sr._S_ALARM in sr.SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE
    assert sr._S_MULTI_DB in SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE
    assert sr._S_MULTI_DB in sr.SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE


def test_stage_templates_declare_only_the_keys_they_need():
    """포맷 키 누락은 런타임 `KeyError`다 — 계약을 테스트로 고정한다."""
    def keys(t):
        return {f for _, f, _, _ in string.Formatter().parse(t) if f}

    assert keys(SEMANTIC_ROUTER_STAGE1_INTENT_TEMPLATE) == {
        "fault_diagnosis_class_line", "fault_diagnosis_section", "location_db_examples",
    }
    assert keys(SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE) == {
        "confirmed_intent", "db_list", "intent_section", "location_vocab",
    }


def test_stage2_sections_cover_every_base_intent():
    """2단계 근거 절 표가 기본 intent를 전부 덮는다 — 빠지면 그 의도만 대역폭이 더 줄어든다."""
    from src.prompts.semantic_router import SEMANTIC_ROUTER_BASE_INTENTS

    assert set(STAGE2_INTENT_SECTIONS) == set(SEMANTIC_ROUTER_BASE_INTENTS)


@pytest.mark.asyncio
async def test_optin_class_does_not_leak_into_stage1_when_off(two_stage_on):
    """★ 계약 C-A — 옵트인 클래스가 off 상태 1단계 프롬프트에 새지 않는다."""
    llm = _ScriptedLLM("data_query\n{\"confidence\": 0.9}", '{"databases": []}')
    await _classify(llm, fault_diagnosis_enabled=False)
    assert "fault_diagnosis" not in llm.calls[0]
