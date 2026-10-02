"""존 역질문 조기 판정 (plans/119 Q-1 · D-267 ② · D-143 후속2 개정).

**등가성이 착수 조건이다.** "위치·서버 식별·승계·핀·스코프 신호 전무 + 활성 후보 DB 전부 존 그룹
소속"이면 DB 분류 LLM이 무엇을 내든 후단 게이트(`_zone_clarification_or_none_task`)가 같은 역질문
페이로드로 끝나야 한다. 그래야 분류 **전에** 같은 판정을 해도 결과가 같다.

- `TestEquivalence`는 분류 LLM의 원시 출력(정상 부분집합·환각 db_id·저점수·깨진 JSON·예외)을
  바꿔 가며 **조기 판정을 끈 상태**(후단 게이트만)로 파이프라인을 돌려 페이로드가 전부 같음을
  보인다.
- `TestEarlyDecision`은 조기 판정이 켜진 실제 경로가 분류 LLM을 0회 부르고 같은 페이로드를
  내는지 본다.
- `TestBoundary`는 동등하지 않은 경우(존 없는 DB 활성 · 위치 힌트 · 비대화 채널)에 종전대로
  분류 뒤 판정하는지 본다.

전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

import importlib
import itertools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

import src.orchestration.subagents as sub

# `src.routing` 패키지가 같은 이름의 함수를 재노출해 `import … as`가 함수를 가리킨다 —
# 모듈을 직접 얻는다.
sr = importlib.import_module("src.routing.semantic_router")

_ZONED = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_POLESTAR = {*_ZONED, "polestar"}
_TASK = {"task_id": "t1", "agent": "data_query", "sub_query": "OS 종류 확인"}


def _cfg(active=None, *, exclusive=True, ownership=False):
    active = list(_ZONED if active is None else active)
    return SimpleNamespace(
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: list(active), zone_group_exclusive=exclusive,
        ),
        get_polestar_db_ids=lambda: set(_POLESTAR),
        polestar_rest=SimpleNamespace(realtime_usage_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=ownership),
    )


def _isolated(**over):
    base = {
        "zone_clarification_allowed": True,
        "is_composite": False,
        "conversation_context": None,
        "original_user_query": "OS 종류, OS 버전 및 OS패치버전을 확인하시오",
        "user_query": "OS 종류 확인",
        "parsed_requirements": {
            "original_query": "OS 종류 확인",
            "query_targets": ["os_type", "os_version"],
            "filter_conditions": [],
        },
        "selected_db_ids": None,
        "zone_selection_db_ids": None,
        "realtime_usage_intent": False,
        "allowed_db_ids": None,
        "user_role": None,
    }
    base.update(over)
    return base


def _llm_outputs() -> list[object]:
    """분류 LLM의 원시 출력 — 정상·환각·저점수·깨짐·예외. 예외 인스턴스는 호출 시 발생시킨다."""
    subsets = [
        list(c) for n in range(1, len(_ZONED) + 1) for c in itertools.combinations(_ZONED, n)
    ]
    outs: list[object] = [
        json.dumps({"intent": "data_query", "databases": [
            {"db_id": d, "relevance_score": 0.9, "sub_query_context": "OS"} for d in s
        ]}) for s in subsets
    ]
    outs += [
        # 활성 밖·미등록 db_id(환각) — 탈락 후 첫 활성 DB 폴백
        json.dumps({"databases": [{"db_id": "itam", "relevance_score": 0.95}]}),
        json.dumps({"databases": [{"db_id": "bogus_db", "relevance_score": 0.99}]}),
        # 환각 + 정상 혼합
        json.dumps({"databases": [
            {"db_id": "bogus_db", "relevance_score": 0.99},
            {"db_id": "polestar_cm_yd", "relevance_score": 0.7},
        ]}),
        # 임계 미만 · 점수 형식 오류 · 빈 목록 · databases 키 없음
        json.dumps({"databases": [{"db_id": "polestar_b0", "relevance_score": 0.01}]}),
        json.dumps({"databases": [{"db_id": "polestar_b0", "relevance_score": "높음"}]}),
        json.dumps({"databases": []}),
        json.dumps({"intent": "general_inference"}),
        # JSON 아님 · 예외
        "죄송합니다. 어떤 DB인지 모르겠습니다.",
        RuntimeError("LLM 백엔드 오류"),
    ]
    return outs


@pytest.fixture
def classify_env(monkeypatch):
    """실제 `classify_dbs` → 실제 `_llm_classify`(검증·폴백 포함)를 가짜 LLM으로 돌린다.

    `.env` 영향을 끊는다 — 2단 분리 분류·구조화 출력·소유 플래그는 off로 고정하고 DB 설명 캐시는
    실패시킨다(`classify_dbs`가 삼키는 경로). 뒤 단계(인가 이후)는 여기서 호출되지 않아야 한다.
    """
    real = sr.load_config()
    pinned_cfg = real.model_copy(update={
        "router": real.router.model_copy(update={
            "two_stage_enabled": False, "capability_ownership_enabled": False,
        }),
        "structured_output_backend": "none",
        "structured_output_max_retries": 1,
    })
    monkeypatch.setattr(sr, "load_config", lambda: pinned_cfg)

    def _no_cache(*_a, **_k):
        raise RuntimeError("테스트 — DB 설명 캐시 없음")

    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", _no_cache)
    calls: list[int] = []
    orig = sub.classify_dbs

    async def _spy(*a, **k):
        calls.append(1)
        return await orig(*a, **k)

    monkeypatch.setattr(sub, "classify_dbs", _spy)
    # 역질문이 안 나면 파이프라인이 실행 단계로 간다 — 그 경계를 표지로 잡는다.
    monkeypatch.setattr(sub, "_run_single_db_pipeline", AsyncMock(
        return_value={"error_message": None, "query_results": []}))
    monkeypatch.setattr(sub, "multi_db_executor", AsyncMock(return_value={}))
    monkeypatch.setattr(sub, "result_merger", AsyncMock(return_value={}))
    monkeypatch.setattr(sub, "result_organizer", AsyncMock(
        return_value={"organized_data": {"rows": [], "summary": ""}}))
    return calls


def _llm(output: object) -> MagicMock:
    llm = MagicMock()
    if isinstance(output, BaseException):
        llm.ainvoke = AsyncMock(side_effect=output)
    else:
        llm.ainvoke = AsyncMock(return_value=AIMessage(content=str(output)))
    return llm


async def _run(output, cfg, *, task=None, isolated=None):
    llm = _llm(output)
    out = await sub.run_data_query_pipeline(
        dict(task or _TASK), isolated if isolated is not None else _isolated(),
        llm=llm, app_config=cfg,
    )
    return out, llm


def _disable_early(monkeypatch):
    """조기 판정을 끈다 — 분류 → 핀 → 승계 → 소유 제한 → 후단 게이트(종전 경로)만 남는다."""
    monkeypatch.setattr(
        sub, "_zone_clarification_before_classify", lambda *a, **k: None, raising=False,
    )


class TestEquivalence:
    """분류 결과와 무관하게 후단 게이트가 같은 페이로드로 끝난다(조기 판정 off)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exclusive", [True, False])
    @pytest.mark.parametrize("ownership", [False, True])
    async def test_any_classification_yields_same_payload(
        self, monkeypatch, classify_env, exclusive, ownership,
    ):
        _disable_early(monkeypatch)
        cfg = _cfg(exclusive=exclusive, ownership=ownership)
        # 소유 on이면 폴스타(다중 존 시스템) 답변 영역을 단 task — 분류 뒤 소유 제한 단계를 탄다.
        task = {**_TASK, "capability": "server_spec"} if ownership else _TASK
        results = []
        for output in _llm_outputs():
            out, llm = await _run(output, cfg, task=task)
            assert llm.ainvoke.await_count == 1, output  # 분류 LLM이 실제로 불렸다(후단 경로)
            results.append(out)
        assert len(classify_env) == len(results)
        reference = results[0]
        assert reference.get("zone_clarification", {}).get("kind") == "zone_select"
        for output, out in zip(_llm_outputs(), results):
            assert out == reference, output

    @pytest.mark.asyncio
    async def test_composite_and_alarm_tasks_also_equivalent(self, monkeypatch, classify_env):
        """복합 계획·알람 task(G-3 확대분)도 같다 — 게이트 입력이 분류와 무관하기 때문이다."""
        _disable_early(monkeypatch)
        cfg = _cfg()
        alarm = {"task_id": "t2", "agent": "alarm_query", "sub_query": "알람 조회"}
        for task, iso in ((_TASK, _isolated(is_composite=True)), (alarm, _isolated())):
            outs = [(await _run(o, cfg, task=task, isolated=iso))[0] for o in _llm_outputs()]
            assert outs[0].get("zone_clarification")
            assert all(o == outs[0] for o in outs)


class TestEarlyDecision:
    """조기 판정 on(실제 경로) — 분류 LLM 0회 · 페이로드는 후단 경로와 같다."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exclusive", [True, False])
    async def test_classifier_not_called_and_payload_identical(
        self, monkeypatch, classify_env, exclusive,
    ):
        cfg = _cfg(exclusive=exclusive)
        early, llm = await _run(RuntimeError("분류 LLM이 불리면 안 된다"), cfg)
        assert llm.ainvoke.await_count == 0
        assert classify_env == []
        assert early["zone_clarification"]["kind"] == "zone_select"
        # 기준: 조기 판정을 끈 후단 경로
        _disable_early(monkeypatch)
        late, _ = await _run(json.dumps({"databases": [
            {"db_id": "polestar_cm_gp", "relevance_score": 0.9}]}), cfg)
        assert early == late
        assert "target_db_ids" not in early  # DB 승격 오염 차단(체크포인트 위생) 유지

    @pytest.mark.asyncio
    async def test_early_payload_matches_gate_function(self, classify_env):
        """페이로드는 후단 게이트와 **같은 함수**가 만든다(사본 금지) — 필드 단위로 확인."""
        cfg = _cfg()
        out, _ = await _run(RuntimeError("x"), cfg)
        direct = sub._zone_clarification_or_none_task(
            _TASK, _isolated(), [{"db_id": d} for d in _ZONED],
            db_pinned=False, db_succeeded=False, app_config=cfg,
        )
        assert out["zone_clarification"] == direct
        assert out["final_response"] == direct["question"]
        assert out["source"] == []


class TestBoundary:
    """동등하지 않은 경우는 종전대로 분류 뒤 판정한다."""

    @pytest.mark.asyncio
    async def test_zoneless_active_db_keeps_post_gate(self, classify_env):
        """존 없는 DB가 활성이면 분류가 그 DB를 고를 수 있다 → 결과가 분류에 달렸다(W-10 경계)."""
        cfg = _cfg(active=[*_ZONED, "itam"])
        zoned, llm1 = await _run(json.dumps({"databases": [
            {"db_id": "polestar_b0", "relevance_score": 0.9}]}), cfg)
        zoneless, llm2 = await _run(json.dumps({"databases": [
            {"db_id": "itam", "relevance_score": 0.9}]}), cfg)
        assert llm1.ainvoke.await_count == 1 and llm2.ainvoke.await_count == 1
        assert zoned.get("zone_clarification")        # 분류가 존 DB → 역질문
        assert "zone_clarification" not in zoneless    # 분류가 존 없는 DB → 조회 진행
        assert len(classify_env) == 2

    @pytest.mark.asyncio
    async def test_polestar_without_zone_group_keeps_post_gate(self, classify_env):
        """폴스타 계열이어도 존 그룹 미소속(로컬 샌드박스형)이면 조기 판정하지 않는다."""
        cfg = _cfg(active=[*_ZONED, "polestar"])
        await _run(json.dumps({"databases": [
            {"db_id": "polestar_b0", "relevance_score": 0.9}]}), cfg)
        assert len(classify_env) == 1

    @pytest.mark.asyncio
    async def test_location_hint_keeps_post_gate(self, classify_env):
        """위치 힌트가 있으면 핀 판정이 결정한다 — 조기 판정 대상이 아니다(분류 뒤 종전 경로)."""
        iso = _isolated()
        iso["parsed_requirements"] = {**iso["parsed_requirements"], "target_db_hints": ["김포"]}
        out, llm = await _run(json.dumps({"databases": [
            {"db_id": "polestar_b0", "relevance_score": 0.9}]}), _cfg(), isolated=iso)
        assert llm.ainvoke.await_count == 1
        assert "zone_clarification" not in out  # 힌트 고정 → 역질문 없음(종전)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("over", [
        {"zone_clarification_allowed": False},
        {"conversation_context": {"previous_db_ids": ["polestar_cm_gp"]}},
        {"original_user_query": "공동존 김포 서버 OS 확인"},
    ])
    async def test_no_early_question_when_signal_present(self, classify_env, over):
        """신호가 있으면 조기 판정은 역질문하지 않고 분류로 넘어간다(후단도 비발동 — 종전)."""
        out, llm = await _run(json.dumps({"databases": [
            {"db_id": "polestar_b0", "relevance_score": 0.9}]}), _cfg(), isolated=_isolated(**over))
        assert llm.ainvoke.await_count == 1
        assert "zone_clarification" not in out

    @pytest.mark.asyncio
    async def test_fixed_targets_skip_both_gates(self, classify_env):
        """계획이 DB를 고정했거나(`db_ids`) 존 선택 재개 턴이면 분류도 역질문도 없다(종전)."""
        out, llm = await _run("x", _cfg(), task={**_TASK, "db_ids": ["polestar_b0"]})
        assert llm.ainvoke.await_count == 0 and "zone_clarification" not in out
        out, llm = await _run("x", _cfg(), isolated=_isolated(selected_db_ids=["polestar_cm_gp"]))
        assert llm.ainvoke.await_count == 0 and "zone_clarification" not in out
        assert classify_env == []
