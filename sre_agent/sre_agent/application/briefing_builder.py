"""글래스박스 브리핑 결정적 조립 (Plan 02 §7).

HolmesGPT의 서술(`answer`)과 severity_judge 판정(`ImportanceVerdict`)을 **6요소 스키마**로
결정적으로 조립한다. LLM 서술을 그대로 신뢰하지 않고:

- **인용 검증**: 도구 출력 인용이 결여된 단정은 "가설"로 강등 표기한다(§7).
- **한계 서술 강제**: 단면 데이터·미수집 신호·증거 불충분을 반드시 명시한다.
- **조치는 권고만**: 실행 경로 없음(D-011). 권고 문자열에 human-gated 안내를 강제한다.

계층: application. 순수 조립 로직으로 domain(severity_signatures)만 참조하고 stdlib만 쓴다
(도구 원시 출력 타입 등 application 세부는 dispatcher가 평문으로 넘긴다 — 결합 최소화).
"""

from __future__ import annotations

from sre_agent.domain.severity_signatures import ImportanceVerdict

# 브리핑 6요소(§7). 중요도([중요도])는 severity_judge 판정에서 오므로 별도 헤더로 조립하고,
# 아래 6요소가 briefing_builder의 결정적 조립 대상이다.
BRIEFING_ELEMENTS: tuple[str, ...] = (
    "summary",         # 요약
    "timeline",        # 타임라인
    "bottleneck",      # 병목
    "cause",           # 원인
    "recommendation",  # 권고
    "limitations",     # 한계
)

# 인용으로 인정하는 마커. 도구명 언급 또는 아래 마커가 있으면 근거 인용으로 본다.
CITATION_MARKERS: tuple[str, ...] = ("←", "<-", "출처", "인용", "근거", "[원문")

# 가설 강등 표기 접두어.
HYPOTHESIS_PREFIX = "[가설] "

# 조치 권고 human-gated 안내(항상 병기 — 실행 경로 부재를 서술로 고정, D-011).
HUMAN_GATED_NOTE = "※ 실행은 운영자 승인 후 수동 — 시스템은 제안만(자동 실행 경로 없음)"


def _is_cited(line: str, tool_names: list[str]) -> bool:
    """한 라인이 도구 출력 인용을 포함하는지 판정한다(도구명 언급 또는 인용 마커)."""
    if any(marker in line for marker in CITATION_MARKERS):
        return True
    return any(name and name in line for name in tool_names)


def _claim_lines(answer: str) -> list[str]:
    """서술을 단정(claim) 라인으로 분해한다(빈 줄 제외)."""
    return [ln.strip() for ln in answer.splitlines() if ln.strip()]


def build_briefing(
    *,
    answer: str,
    verdict: ImportanceVerdict,
    tool_names: list[str] | None = None,
    gate_tier: str | None = None,
    remediation: list[str] | None = None,
    limitations: list[str] | None = None,
) -> dict:
    """6요소 브리핑 dict를 결정적으로 조립한다.

    인용 검증: 근거 인용이 없는 단정은 가설로 강등하고 `hypotheses`에 모은다. 도구 출력이
    전무하거나(tool_names 없음) 인용된 단정이 하나도 없으면 요약/원인을 가설로 표기한다.
    """
    tool_names = tool_names or []
    claims = _claim_lines(answer)
    cited = [c for c in claims if _is_cited(c, tool_names)]
    uncited = [c for c in claims if not _is_cited(c, tool_names)]

    # 도구 출력이 하나라도 있고, 인용된 단정이 존재해야 "검증됨"으로 본다.
    citations_verified = bool(tool_names) and bool(cited)

    # 요약: 첫 단정. 검증 불가(인용 결여)면 가설 강등 표기.
    head = claims[0] if claims else "조사 서술 없음"
    summary = head if _is_cited(head, tool_names) and tool_names else HYPOTHESIS_PREFIX + head

    # 원인: 인용된 단정이 있으면 그중 마지막, 없으면 서술 말미를 가설로.
    if cited:
        cause = cited[-1]
    elif claims:
        cause = HYPOTHESIS_PREFIX + claims[-1]
    else:
        cause = HYPOTHESIS_PREFIX + "원인 미확정"

    # 타임라인: 인용/시간 패턴을 가진 라인만 추린다(없으면 안내).
    timeline = [c for c in claims if _is_cited(c, tool_names)] or ["타임라인 근거 없음(도구 출력 인용 결여)"]

    # 병목: 매칭된 시그니처 라벨에서 도출(없으면 미확정).
    if verdict.signals:
        bottleneck = "; ".join(s.label for s in verdict.signals)
    else:
        bottleneck = "미확정(시그니처 매칭 없음)"

    # 권고: 제공된 조치 후보 + human-gated 안내 강제(실행 경로 없음).
    rec_items = list(remediation or [])
    if not rec_items:
        rec_items = ["조치 권고 없음(remediation_recommender는 W-C 소관)"]
    recommendation = {"items": rec_items, "note": HUMAN_GATED_NOTE}

    # 한계: 제공된 한계 + 자동 부가(단면 데이터·증거 불충분·미검증 인용).
    limits = list(limitations or [])
    limits.append("프로세스·자원 스냅샷은 조사 시점 단면일 수 있음")
    if verdict.evidence_insufficient:
        limits.append("증거 불충분 — 원격 배치에서 로그·대체 카운터 무매칭(상향 보류)")
    if not citations_verified:
        limits.append("도구 출력 인용이 결여돼 서술을 가설로 강등함(글래스박스 검증 불가)")

    # 중요도 헤더: severity_judge 판정 + 게이트 근거.
    severity = {
        "level": verdict.level,
        "confidence": verdict.confidence,
        "escalate": verdict.escalate,
        "gate_tier": gate_tier,
        "signals": [s.name for s in verdict.signals],
        "evidence_insufficient": verdict.evidence_insufficient,
    }

    return {
        "severity": severity,
        "summary": summary,
        "timeline": timeline,
        "bottleneck": bottleneck,
        "cause": cause,
        "recommendation": recommendation,
        "limitations": limits,
        "citations_verified": citations_verified,
        "hypotheses": uncited,
    }


def stub_briefing(message: str) -> dict:
    """조사 미실행(스텁) 시의 브리핑 표기 — 6요소 조립을 생략하고 사유만 노출한다."""
    return {"stub": True, "message": message, "elements": None}


__all__ = [
    "BRIEFING_ELEMENTS",
    "CITATION_MARKERS",
    "HYPOTHESIS_PREFIX",
    "HUMAN_GATED_NOTE",
    "build_briefing",
    "stub_briefing",
]
