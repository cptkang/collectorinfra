"""라우터 프롬프트 구조 불변식 테스트 (Plan 79 트랙 A · SPEC-semantic-router-instruction).

실 LLM 없이 검증한다(폐쇄망 CI 전제 · D-127). 프롬프트는 문자열이므로 구조를 직접 단언한다.

검증 대상(SPEC §8 성공 기준):
    S1 저신뢰 제외 지시가 없다 — 제외 판단은 코드 몫(A-1)
    S2 신뢰도 3대역(0.8+ / 0.5~0.8 / 0.3~0.5)에 각각 예시가 있다(A-2)
    S3 클래스별 예시 편중이 상한(30%) 이내다(A-4)
    S4 「출력 형식」 클래스 정의 집합 = 예시 등장 클래스 집합(A-3)
    S5 fault_diagnosis 절이 「intent 판단 우선순위」보다 앞에 있다(A-5)
    S6 멀티 DB 예시가 보존된다 — Plan 79 §1.1 불변식

배경(Plan 79 §3.5 감사): 종전 프롬프트는 규칙 5로 모델에게 0.3 미만을 스스로 버리라고 지시해
`MIN_RELEVANCE_SCORE` 코드 게이트가 설계상 no-op이었고, 예시가 0.8~1.0 대역에만 몰려 있었다.
"""

from __future__ import annotations

import json
import re

import pytest

from src.routing.domain_config import DB_DOMAINS
from src.routing.semantic_router import _build_router_prompt

# 클래스별 예시 편중 상한 — 5클래스이므로 균등 시 20%, 여유 10%p (SPEC Q2 확정).
MAX_CLASS_EXAMPLE_SHARE = 0.30

# 규칙 4가 정의한 신뢰도 대역. 각 대역에 최소 1개 예시가 있어야 모델이 그 대역을 산출한다.
_BANDS = (
    ("확실(0.8~1.0)", 0.8, 1.0),
    ("가능(0.5~0.8)", 0.5, 0.8),
    ("약함(0.3~0.5)", 0.3, 0.5),
)


def _prompt(*, fault_diagnosis_enabled: bool = True) -> str:
    """완성된 라우터 프롬프트를 얻는다(이스케이프가 풀린 실제 전달 문자열)."""
    return _build_router_prompt(
        DB_DOMAINS, fault_diagnosis_enabled=fault_diagnosis_enabled
    )


def _example_objects(prompt: str) -> list[dict]:
    """프롬프트의 few-shot JSON 예시를 파싱한다.

    ```json 코드펜스 블록과 인라인 JSON({"intent": ...})을 모두 수집한다.
    파싱 실패한 블록은 조용히 건너뛰지 않고 테스트에서 드러나도록 수집 결과로 판단한다.
    """
    objs: list[dict] = []
    for block in re.findall(r"```json\s*(.*?)```", prompt, re.DOTALL):
        try:
            objs.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    # 코드펜스 없이 한 줄로 쓰인 예시(fault_diagnosis 절 등)
    for line in re.findall(r'^출력:\s*(\{.*\})\s*$', prompt, re.MULTILINE):
        try:
            objs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return objs


def _scores(prompt: str) -> list[float]:
    """예시에 등장하는 relevance_score 값 전체."""
    return [float(v) for v in re.findall(r'"relevance_score":\s*([0-9.]+)', prompt)]


def _intents(prompt: str) -> list[str]:
    """예시에 등장하는 intent 값 전체."""
    return re.findall(r'"intent":\s*"([a-z_]+)"', prompt)


def _defined_classes(prompt: str) -> set[str]:
    """「출력 형식」 절에서 `- <class>: 설명` 형태로 정의된 클래스 집합."""
    return set(re.findall(r"^- ([a-z_]+):\s", prompt, re.MULTILINE))


class TestLowConfidenceExpressible:
    """S1 — 저신뢰가 표현될 수 있어야 한다(A-1)."""

    def test_no_self_exclusion_instruction(self):
        """모델에게 저신뢰 항목을 버리라고 지시하지 않는다.

        종전 규칙 5("0.3 미만의 관련도를 가진 DB는 포함하지 마세요")가 있으면 저신뢰 후보가
        출력되지 않아 코드 게이트(MIN_RELEVANCE_SCORE)가 no-op이 된다(Plan 79 §3.5①).
        """
        prompt = _prompt()
        forbidden = [
            "미만의 관련도를 가진 DB는 포함하지 마세요",
            "미만인 DB는 제외",
            "미만은 포함하지",
        ]
        hits = [f for f in forbidden if f in prompt]
        assert not hits, (
            f"저신뢰 자기 제외 지시가 남아 있다: {hits}. "
            "제외 판단은 코드(MIN_RELEVANCE_SCORE)가 하고 모델은 값만 부여해야 한다."
        )

    def test_system_owns_exclusion(self):
        """제외 판단의 주체가 시스템임을 프롬프트가 명시한다."""
        prompt = _prompt()
        assert "제외 판단은 시스템" in prompt or "시스템이 제외" in prompt, (
            "제외 주체가 명시되지 않았다 — 모델이 임의로 후보를 버릴 수 있다."
        )


class TestConfidenceBandCoverage:
    """S2 — 규칙 4의 세 대역에 각각 예시가 있어야 한다(A-2)."""

    def test_all_bands_have_example(self):
        scores = _scores(_prompt())
        assert scores, "예시에 relevance_score가 하나도 없다"
        missing = []
        for name, lo, hi in _BANDS:
            in_band = [s for s in scores if lo <= s < hi or (hi == 1.0 and s == 1.0)]
            if not in_band:
                missing.append(name)
        assert not missing, (
            f"예시가 없는 신뢰도 대역: {missing}. 관측된 값: {sorted(set(scores))}. "
            "모델은 지시문보다 예시를 따르므로 대역별 예시가 없으면 그 값을 산출하지 않는다."
        )


class TestClassBalance:
    """S3 — 클래스별 예시 편중 상한(A-4)."""

    def test_no_class_dominates(self):
        intents = _intents(_prompt())
        assert intents, "예시에 intent가 하나도 없다"
        counts: dict[str, int] = {}
        for it in intents:
            counts[it] = counts.get(it, 0) + 1
        total = len(intents)
        worst, worst_n = max(counts.items(), key=lambda kv: kv[1])
        share = worst_n / total
        assert share <= MAX_CLASS_EXAMPLE_SHARE, (
            f"'{worst}' 예시가 {worst_n}/{total} = {share:.0%}로 상한"
            f"({MAX_CLASS_EXAMPLE_SHARE:.0%})을 넘는다. 분포: {counts}"
        )


class TestClassDefinitionConsistency:
    """S4 — 정의된 클래스 집합 = 예시 등장 클래스 집합(A-3)."""

    def test_defined_matches_exemplified(self):
        prompt = _prompt()
        defined = _defined_classes(prompt)
        used = set(_intents(prompt))
        assert used, "예시 intent가 없다"
        undefined = used - defined
        unexemplified = defined - used
        assert not undefined, (
            f"예시에는 있으나 「출력 형식」에 정의되지 않은 클래스: {sorted(undefined)}"
        )
        assert not unexemplified, (
            f"정의됐으나 예시가 없는 클래스: {sorted(unexemplified)}"
        )


class TestSectionOrdering:
    """S5 — 선언과 배치의 일치(A-5)."""

    def test_fault_diagnosis_precedes_priority_section(self):
        """'최우선 검토'로 선언된 절이 우선순위 절보다 앞에 있어야 한다.

        긴 프롬프트에서 위치는 주의 배분에 영향을 준다(Plan 79 §3.5⑤).
        """
        prompt = _prompt(fault_diagnosis_enabled=True)
        i_fault = prompt.find("## 장애 진단 의도")
        i_priority = prompt.find("## intent 판단 우선순위")
        assert i_fault != -1, "fault_diagnosis 절이 프롬프트에 없다"
        assert i_priority != -1, "intent 판단 우선순위 절이 프롬프트에 없다"
        assert i_fault < i_priority, (
            f"'최우선 검토'로 선언된 fault_diagnosis 절이 우선순위 절보다 뒤에 있다 "
            f"(fault={i_fault}, priority={i_priority})."
        )


class TestMultiDbInvariant:
    """S6 — 멀티 DB 선택 보존(Plan 79 §1.1 불변식 · 사용자 확정)."""

    def test_multi_db_example_preserved(self):
        objs = _example_objects(_prompt())
        assert objs, "few-shot 예시를 하나도 파싱하지 못했다"
        multi = [o for o in objs if len(o.get("databases") or []) >= 2]
        assert multi, (
            "databases 배열 길이가 2 이상인 예시가 없다 — 멀티 DB 선택 능력이 "
            "예시에서 사라지면 모델이 단일 선택으로 수렴한다(Plan 79 §1.1 불변식)."
        )

    def test_sub_query_context_separated_in_multi_example(self):
        """멀티 DB 예시에서 DB별 sub_query_context가 분리돼 있어야 한다."""
        objs = _example_objects(_prompt())
        multi = [o for o in objs if len(o.get("databases") or []) >= 2]
        if not multi:
            pytest.skip("멀티 DB 예시 없음 — test_multi_db_example_preserved가 먼저 잡는다")
        for obj in multi:
            contexts = [d.get("sub_query_context") for d in obj["databases"]]
            assert all(contexts), f"sub_query_context가 비어 있는 DB 항목: {obj}"
            assert len(set(contexts)) == len(contexts), (
                f"멀티 DB 예시의 sub_query_context가 중복된다(분리 실패): {contexts}"
            )


class TestNoRegressionWhenFaultDiagnosisOff:
    """옵트인 off 경로 보호 — 기존 계약(Plan 64 CW-B) 유지."""

    def test_off_prompt_excludes_fault_section(self):
        off = _prompt(fault_diagnosis_enabled=False)
        assert "## 장애 진단 의도" not in off, (
            "fault_diagnosis_enabled=False인데 절이 포함됐다 — 옵트인 계약 위반."
        )

    def test_off_prompt_never_mentions_fault_diagnosis(self):
        """off일 때 `fault_diagnosis`가 **어디에도** 등장하지 않아야 한다.

        절(section)뿐 아니라 「출력 형식」의 클래스 정의 목록도 조건부여야 한다.
        정의만 남아도 LLM이 그 클래스를 알게 되어 산출할 수 있는데, off면 그래프에
        해당 노드가 없다(Plan 64 CW-B 옵트인 계약).
        """
        off = _prompt(fault_diagnosis_enabled=False)
        assert "fault_diagnosis" not in off, (
            "fault_diagnosis_enabled=False인데 프롬프트에 클래스명이 남아 있다 — "
            "LLM이 산출 가능해져 옵트인 계약이 깨진다."
        )

    def test_on_prompt_defines_fault_diagnosis_class(self):
        """on일 때는 클래스 정의가 있어야 한다(S4 정합)."""
        on = _prompt(fault_diagnosis_enabled=True)
        assert "- fault_diagnosis:" in on, "on인데 「출력 형식」에 클래스 정의가 없다"

    def test_off_prompt_has_no_dangling_placeholder(self):
        """플레이스홀더 방식으로 바꿔도 off일 때 빈 자리가 남지 않아야 한다."""
        off = _prompt(fault_diagnosis_enabled=False)
        assert "{fault_diagnosis_section}" not in off, "미치환 플레이스홀더가 남았다"
        assert "\n\n\n" not in off, (
            "off 경로에 빈 줄이 3개 이상 연속한다 — 섹션 제거 자리가 정리되지 않았다."
        )
