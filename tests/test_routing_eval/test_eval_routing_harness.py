"""라우팅 평가 하네스 검출력 검증 (Plan 80 WU-05 선행).

**왜 하네스를 테스트하나.** S-1은 회귀 게이트다. 게이트가 **거짓 통과**를 내면 승인·과금을
쓰고도 아무것도 보장하지 못한다. 실제로 초안 하네스는 멀티 DB 축소만 보고 종료 코드를 정해
**의도 오분류와 LLM 전면 실패를 exit 0으로 통과**시켰다(2026-08-27 목업 결함 주입으로 발견).

실 LLM 호출 0건 — `KBGenAIChat`의 **HTTP 경계만** 갈아끼우므로 클라이언트 로직
(페이로드 조립·status 규약·`remove_llm_junk`·PII 훅)은 그대로 실행된다. D-127 무관.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from tests.mocks import fabrix_kbgenai_mock as fx

_REPO = Path(__file__).resolve().parents[2]


def _harness():
    spec = importlib.util.spec_from_file_location(
        "eval_routing", _REPO / "scripts" / "eval_routing.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _harness()


def _run(fault: str) -> dict:
    items = H.load_gold()
    with fx.mock_kbgenai(fault=fault):
        results = asyncio.run(H.run(items, llm=fx.make_llm()))
    return H.summarize(results)


class TestGoldsetIntegrity:
    def test_goldset_is_valid(self):
        """골든셋이 실제 도메인·intent와 정합한다(실행 전에 잡는다)."""
        assert H.validate_gold(H.load_gold()) == []

    def test_goldset_has_multi_db_cases(self):
        """멀티 DB 케이스가 없으면 불변식 ⑩을 감시할 수 없다."""
        items = H.load_gold()
        multi = [i for i in items if i.get("critical") == "multi_db"]
        assert len(multi) >= 3, f"멀티 DB 감시 케이스가 부족하다: {len(multi)}건"
        for it in multi:
            assert it["expect"]["min_databases"] >= 2


class TestCleanBaseline:
    def test_clean_mock_passes_everything(self):
        """정상 응답에 오탐을 내면 게이트를 신뢰할 수 없다."""
        s = _run(fx.FAULT_NONE)
        assert s["passed"] == s["total"], f"정상인데 실패했다: {s}"
        assert s["multi_db_preserved"] == s["multi_db_cases"]
        assert H._verdict(s) == 0

    def test_clean_mock_observes_low_confidence_band(self):
        """S-2 — A-1 이후 저신뢰 대역이 분포에 나타나야 관측이 성립한다."""
        s = _run(fx.FAULT_NONE)
        assert s["score_count"] > 0
        assert any("0.3~0.5" in k for k in s["score_distribution"]), (
            f"저신뢰 대역이 분포에 없다 — A-1 효과를 관측할 수 없다: {s['score_distribution']}"
        )


class TestRegressionDetection:
    """★ 이 클래스가 게이트의 존재 이유다 — 각 결함을 **반드시** 잡아야 한다."""

    @pytest.mark.parametrize(
        "fault",
        [
            fx.FAULT_COLLAPSE_MULTI,
            fx.FAULT_BAD_INTENT,
            fx.FAULT_BAD_SCORE,
            fx.FAULT_MALFORMED,
            fx.FAULT_ERROR_STATUS,
        ],
    )
    def test_every_injected_fault_is_detected(self, fault):
        s = _run(fault)
        assert H._verdict(s) == 1, (
            f"결함 {fault!r}이 게이트를 통과했다 — 거짓 통과다. summary={s}"
        )

    def test_multi_collapse_is_detected_even_when_tolerant(self):
        """멀티 DB 축소는 --tolerate와 무관하게 항상 회귀다."""
        s = _run(fx.FAULT_COLLAPSE_MULTI)
        assert H._verdict(s, tolerate=999) == 1

    def test_llm_errors_are_detected_even_when_tolerant(self):
        """호출 실패는 측정 자체가 무효다 — 관용 대상이 아니다."""
        s = _run(fx.FAULT_ERROR_STATUS)
        assert H._verdict(s, tolerate=999) == 1


class TestGuardsWorkThroughRealClientPath:
    """E-1·E-2가 **실제 KBGenAIChat 경로**에서 동작하는지(대역 LLM이 아니다)."""

    def test_e1_demotes_unknown_intent_end_to_end(self):
        """오타 intent가 data_query로 강등된다 — 하류 분기 낙하가 막힌다."""
        s = _run(fx.FAULT_BAD_INTENT)
        # 전 케이스가 'prosess_query'로 오염됐으나 E-1이 data_query로 강등하므로,
        # data_query를 기대하는 케이스만 일치한다. 오염이 그대로 흘렀다면 0이었을 것이다.
        assert s["intent_match"] > 0, "강등이 동작하지 않아 오염 intent가 그대로 흘렀다"
        assert s["intent_match"] < s["total"], "오염을 전혀 반영하지 못했다"

    def test_e2_isolates_bad_score_without_discarding_all(self):
        """★ 한 항목의 형식 오류가 분류 전체를 죽이지 않는다(F2 해소 실증)."""
        s = _run(fx.FAULT_BAD_SCORE)
        assert s["dropped_total"] > 0, "형식 오류 항목이 탈락 처리되지 않았다"
        assert s["intent_match"] == s["total"], (
            "점수 형식 오류가 intent 판정까지 오염시켰다 — 격리 실패"
        )
        assert s["multi_db_preserved"] > 0, (
            "형식 오류 하나로 멀티 DB가 전멸했다 — 종전 '분류 전체 폐기' 동작이 남아 있다"
        )
