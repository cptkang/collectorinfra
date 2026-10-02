"""변화·급증 어휘 선언 로더 (Plan 82 Wave 8·9 · D-176 후속1·후속2).

**무엇을 하나.** *"갑자기"·"급증"* 같은 변화 어휘와 급증 판정 기본 임계를 선언 파일에서 읽고,
질의가 급증을 요구하는지·임계를 명시했는지를 **결정적으로** 판정한다. 판정만 하며 SQL도
프롬프트도 만들지 않는다 — 조립은 `src/db_adapters/polestar/spike_sql.py` 소관이다.

**왜 결정적인가.** LLM 분류에 정합성을 의존하지 않는다(D-035 · Known Mistakes). 급증이
반영됐는지 여부는 사용자에게 *"0건의 의미"* 를 바꾸는 정보라(§6.5), 같은 질의에 같은 판정이
나와야 하고 미판정이 *"모델이 못 맞혔다"* 가 아니라 *"어휘에 없다"* 로 귀결되어야 한다.

**규칙은 코드가 아니라 선언 파일에 있다** — `config/change_terms.yaml`
(하네스 문서 표 29 G · `src/domain/middleware.py` 선례). 어휘 추가에 코드 변경이 필요하면
그것은 설계 실패다.

계층: domain (`scripts/arch_check.py` `src.domain` 매핑) — 순수 · 내부 모듈 의존 0.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "change_terms.yaml"


class ChangeTerms(BaseModel):
    """선언 파일 전체. 코드가 아니라 데이터다."""

    spike_terms: list[str] = Field(default_factory=list)
    default_delta_pp: float = 20.0
    default_baseline: str = "month"
    explicit_delta_patterns: list[str] = Field(default_factory=list)
    week_terms: list[str] = Field(default_factory=list)
    absolute_threshold_patterns: list[str] = Field(default_factory=list)
    filesystem_terms: list[str] = Field(default_factory=list)
    other_metric_terms: list[str] = Field(default_factory=list)


class SpikeRequest(BaseModel):
    """질의가 요구한 급증 판정 조건.

    `delta_source`는 응답 표기용이다 — 기본값을 썼다는 사실과 그 값을 사용자에게
    **반드시 노출**한다(§6.12 ② · 시스템이 고른 임계를 모른 채 결과를 믿게 하지 않는다).
    """

    delta_pp: float
    delta_source: str  # "explicit" | "default"
    matched_term: str = ""


@lru_cache(maxsize=1)
def _raw_config() -> dict[str, Any]:
    """선언 파일 원문. **파일이 없으면 빈 규칙**이다 — 예외가 아니다.

    이 모듈의 소비처는 전부 플래그 off가 기본이라, 파일 부재가 파이프라인을 죽이면
    안 된다. 어휘가 없으면 급증 판정이 발동하지 않을 뿐이다(현행 동작 = 회귀 0).
    """
    try:
        return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


@lru_cache(maxsize=1)
def load_change_terms() -> ChangeTerms:
    """선언 파일에서 변화 어휘·기본 임계를 읽는다(누락 키는 모델 기본값)."""
    return ChangeTerms(**_raw_config())


def matched_spike_term(text: str | None, terms: Optional[ChangeTerms] = None) -> str:
    """원문에서 처음 매칭된 급증 어휘를 반환한다(없으면 빈 문자열).

    Args:
        text: 사용자 원문 질의
        terms: 선언 규칙. 미지정 시 선언 파일에서 읽는다(테스트·확장용 주입점).
    """
    rules = terms or load_change_terms()
    haystack = (text or "").lower()
    for term in rules.spike_terms:
        if term.lower() in haystack:
            return term
    return ""


def matched_week_term(text: str | None, terms: Optional[ChangeTerms] = None) -> str:
    """원문에서 처음 매칭된 **주 단위** 비교 표면어를 반환한다(없으면 빈 문자열).

    주 단위는 일별 통계 보존기간이 확인되지 않아 열려 있지 않다(§6.12 ③). 식별은
    지원하기 위해서가 아니라 **차단 사유를 붙이기 위해서**다 — 약속하고 조용히
    누락시키는 것이 최악이다.
    """
    rules = terms or load_change_terms()
    haystack = (text or "").lower()
    for term in rules.week_terms:
        if term.lower() in haystack:
            return term
    return ""


def resolve_spike_request(
    user_query: str | None, terms: Optional[ChangeTerms] = None
) -> Optional[SpikeRequest]:
    """질의의 급증 요구를 해석한다(급증 어휘가 없으면 None).

    임계는 **명시 수치 우선**이다 — *"30%p 이상 상승"* 이 있으면 그 값을,
    없으면 선언 파일 기본값(`default_delta_pp`)을 쓰고 그 사실을 `delta_source`로 남긴다.

    ⚠ *"80% 이상으로 상승"* 의 80은 **도달 수준**(절대 임계)이지 차분이 아니다 —
    선언 파일의 부정 전방탐색이 이 형태를 배제한다(요구 4 원문이 정확히 이 형태다).
    """
    rules = terms or load_change_terms()
    term = matched_spike_term(user_query, rules)
    if not term:
        return None

    text = user_query or ""
    for pattern in rules.explicit_delta_patterns:
        m = re.search(pattern, text)
        if m and m.groups():
            try:
                return SpikeRequest(
                    delta_pp=float(m.group(1)), delta_source="explicit", matched_term=term
                )
            except ValueError:  # 캡처가 수치가 아니면 다음 패턴으로 — 선언 파일 오타 내성
                continue

    return SpikeRequest(
        delta_pp=rules.default_delta_pp, delta_source="default", matched_term=term
    )


def _first_listed(text: str | None, terms: list[str]) -> str:
    haystack = (text or "").lower()
    for term in terms:
        if term.lower() in haystack:
            return term
    return ""


def matched_filesystem_term(
    text: str | None, terms: Optional[ChangeTerms] = None
) -> str:
    """급증 결정적 조립의 **적용 범위** 판정 — 파일시스템 축인지(SPEC Ask first)."""
    return _first_listed(text, (terms or load_change_terms()).filesystem_terms)


def matched_other_metric_terms(
    text: str | None, terms: Optional[ChangeTerms] = None
) -> list[str]:
    """질의에 함께 있는 다른 지표 축. 있으면 *"파일시스템만 판정했다"* 를 응답에 표기한다.

    조용히 축소하면 사용자는 CPU 조건도 반영된 줄 안다 — 0건의 의미가 달라진다.
    """
    rules = terms or load_change_terms()
    haystack = (text or "").lower()
    return [t for t in rules.other_metric_terms if t.lower() in haystack]


def resolve_absolute_threshold(
    user_query: str | None, terms: Optional[ChangeTerms] = None
) -> Optional[float]:
    """질의의 **절대 임계**(도달 수준 %)를 뽑는다(없으면 None).

    차분(%p)과 다른 축이다 — 급증은 둘을 병행해야 저사용 파일시스템이 상위를 점령하지
    않는다(§6.10 ①). *"25% 이상 올라간"* 처럼 상승 동사가 뒤따르면 그것은 차분이므로
    절대 임계로 읽지 않는다(선언 파일의 부정 전방탐색).
    """
    rules = terms or load_change_terms()
    text = user_query or ""
    for pattern in rules.absolute_threshold_patterns:
        m = re.search(pattern, text)
        if m and m.groups():
            try:
                return float(m.group(1))
            except ValueError:  # 선언 파일 오타 내성
                continue
    return None
