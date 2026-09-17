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

# 상관 ≠ 인과 — 결정적 상관에서 도출한 가설의 신뢰도 한계(plans/50 §6.3).
CORRELATION_NOT_CAUSATION_NOTE = "상관 ≠ 인과 — 가설 신뢰도는 선행성·지속성(결정적 상관)에서 도출한 것이며 인과 확정이 아님"
_KIND_LABEL = {"metric": "메트릭", "alarm": "알람", "change": "변경"}


def _fmt_offset(minutes: object) -> str:
    try:
        m = int(minutes)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "T?"
    return f"T{'-' if m < 0 else '+'}{abs(m)}m"


def correlation_timeline(correlation: dict) -> list[str]:
    """§9.1 상대시각 타임라인 — `T-15m 메트릭 디스크 IO 급등 …`(수치는 주입값만)."""
    lines: list[str] = []
    for t in correlation.get("timeline") or []:
        if not isinstance(t, dict):
            continue
        kind = _KIND_LABEL.get(str(t.get("kind")), str(t.get("kind")))
        lines.append(f"{_fmt_offset(t.get('t_offset_min'))} {kind} {t.get('detail')}")
    return lines


def root_cause_hypotheses(correlation: dict | None, llm_cause: str, citations_verified: bool) -> list[dict]:
    """§7.2 복수 가설(rank·confidence·evidence) — 결정적 상관에서 먼저, LLM 인용 원인은 마지막.

    ① 선행 신호(첫 알람보다 앞선 onset) 지표: sustained=high · spike=medium
    ② 그 외 이상 지표: 알람 선행이면 medium, 아니면 low
    ③ LLM 인용 원인: 인용 검증됐으면 medium, 아니면 low(가설 강등)

    상관이 없으면 빈 목록 — 렌더러가 빈 값을 생략하므로 사용자 출력은 종전과 같다(기본 off 비트 동일).
    """
    out: list[dict] = []
    if not correlation:
        return out
    if correlation:
        findings = correlation.get("metric_findings") or {}
        timeline = correlation_timeline(correlation)
        leading = correlation.get("leading_signal")
        first_alarm = (correlation.get("alarm_summary") or {}).get("first_offset_min")
        # plans/91 1-2(C′-2): 변경이 첫 알람보다 앞서면 "변경 직후" 가설을 rank 1에 — confidence 상한 medium(상관≠인과).
        cf = correlation.get("change_finding") or {}
        if isinstance(cf, dict) and cf.get("before_first_alarm") and isinstance(cf.get("last_change_offset_min"), int) \
                and isinstance(first_alarm, int):
            gap = first_alarm - int(cf["last_change_offset_min"])
            descs = [str(d) for d in (cf.get("descriptions") or [])]
            desc = descs[0] if descs else "변경 이벤트"
            out.append({
                "rank": 1, "cause": f"변경 직후 — {desc} 이후 {gap}분 뒤 첫 알람", "confidence": "medium",
                "evidence": [ln for ln in timeline if " 변경 " in ln] or [f"변경 {_fmt_offset(cf['last_change_offset_min'])}"],
                "reasoning": f"lookback 내 변경 {cf.get('count')}건 · 최근 변경 {_fmt_offset(cf['last_change_offset_min'])} → 첫 알람 "
                             f"{_fmt_offset(first_alarm)}. " + CORRELATION_NOT_CAUSATION_NOTE,
            })
        ordered = sorted(
            ((f.get("onset_offset_min"), name, f) for name, f in findings.items()
             if isinstance(f, dict) and f.get("is_anomalous")),
            key=lambda x: (x[0] if x[0] is not None else 0, x[1]),
        )
        for onset, name, f in ordered:
            lag = f.get("lead_lag_min")
            leads = isinstance(lag, int) and lag < 0
            if name == leading and leads:
                confidence = "high" if f.get("kind") == "sustained" else "medium"
                cause = f"{name} {f.get('kind')} 이상이 첫 알람에 {abs(lag)}분 선행"
            else:
                confidence = "medium" if leads else "low"
                lag_s = f"첫 알람 대비 {lag:+d}분" if isinstance(lag, int) else "알람 대비 시차 미산출"
                cause = f"{name} {f.get('kind')} 이상 ({lag_s})"
            evidence = [ln for ln in timeline if ln.split(" ", 2)[-1].startswith(name)]
            if first_alarm is not None:
                evidence += [ln for ln in timeline if " 알람 " in ln][:1]
            out.append({
                "rank": len(out) + 1, "cause": cause, "confidence": confidence,
                "evidence": evidence or [f"{name} onset {_fmt_offset(onset)}"],
                "reasoning": f"{name} onset {_fmt_offset(onset)} · peak {f.get('peak_value')} · z={f.get('z_score')}. "
                             + CORRELATION_NOT_CAUSATION_NOTE,
            })
    if llm_cause and llm_cause.strip():
        out.append({
            "rank": len(out) + 1, "cause": llm_cause,
            "confidence": "medium" if citations_verified else "low",
            "evidence": [llm_cause] if citations_verified else [],
            "reasoning": "조사 서술의 인용 원인" + ("" if citations_verified else " — 도구 출력 인용 결여로 가설 강등"),
        })
    return out


def _is_cited(line: str, tool_names: list[str]) -> bool:
    """한 라인이 도구 출력 인용을 포함하는지 판정한다(도구명 언급 또는 인용 마커).

    도구 출력이 0건이면 인용할 대상이 없으므로 마커가 있어도 인용이 아니다 — `DiagnosisAgent`의 미완주
    안내("수집된 근거가 불충분…")가 마커 `근거`에 걸려 원인·타임라인에 근거처럼 실리던 결함(2026-09-17
    MLX e2e 실측). `build_briefing` docstring의 "도구 출력 전무 → 가설" 계약을 모든 소비 지점에 같게 적용한다.
    """
    if not tool_names:
        return False
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
    correlation: dict | None = None,
) -> dict:
    """6요소 브리핑 dict를 결정적으로 조립한다.

    인용 검증: 근거 인용이 없는 단정은 가설로 강등하고 `hypotheses`에 모은다. 도구 출력이
    전무하거나(tool_names 없음) 인용된 단정이 하나도 없으면 요약/원인을 가설로 표기한다.

    `correlation`(plans/50 G4 · `CorrelationResult.to_dict()`)이 있으면 **그 수치가 정본**이다 —
    타임라인은 상대시각 항목(§9.1)으로, 원인은 rank·confidence 가설(§7.2)로 조립하고 `notes`를
    한계에 싣는다. 없으면 `root_cause_hypotheses`만 빈 목록이고 나머지는 종전과 같다.
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

    # 타임라인: 상관 타임라인(상대시각 · 주입값) 먼저, 인용 라인은 근거로 뒤에(없으면 안내).
    cited_lines = [c for c in claims if _is_cited(c, tool_names)]
    if correlation:
        timeline = correlation_timeline(correlation) + cited_lines
        timeline = timeline or ["타임라인 근거 없음(상관 타임라인·도구 출력 인용 모두 결여)"]
    else:
        timeline = cited_lines or ["타임라인 근거 없음(도구 출력 인용 결여)"]

    # 원인 가설(§7.2): 결정적 상관에서 먼저, LLM 인용 원인은 마지막. rank 1이 `cause` 문자열이 된다.
    hypotheses_ranked = root_cause_hypotheses(correlation, cause, citations_verified)
    if correlation and hypotheses_ranked:
        cause = hypotheses_ranked[0]["cause"]

    # 병목: 매칭된 시그니처 라벨에서 도출(없으면 미확정).
    if verdict.signals:
        bottleneck = "; ".join(s.label for s in verdict.signals)
    else:
        bottleneck = "미확정(시그니처 매칭 없음)"

    # 권고: 제공된 조치 후보 + human-gated 안내 강제(실행 경로 없음).
    rec_items = list(remediation or [])
    if not rec_items:
        rec_items = ["조치 권고 없음(remediation_recommender off 또는 근거 시그니처 무매칭)"]
    recommendation = {"items": rec_items, "note": HUMAN_GATED_NOTE}

    # 한계: 제공된 한계 + 자동 부가(단면 데이터·증거 불충분·미검증 인용).
    limits = list(limitations or [])
    limits.append("프로세스·자원 스냅샷은 조사 시점 단면일 수 있음")
    if verdict.evidence_insufficient:
        limits.append("증거 불충분 — 원격 배치에서 로그·대체 카운터 무매칭(상향 보류)")
    if not citations_verified:
        limits.append("도구 출력 인용이 결여돼 서술을 가설로 강등함(글래스박스 검증 불가)")
    if correlation:
        limits.extend(str(n) for n in (correlation.get("notes") or []))
        limits.append(CORRELATION_NOT_CAUSATION_NOTE)

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
        "root_cause_hypotheses": hypotheses_ranked,
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
    "CORRELATION_NOT_CAUSATION_NOTE",
    "correlation_timeline",
    "root_cause_hypotheses",
    "CITATION_MARKERS",
    "HYPOTHESIS_PREFIX",
    "HUMAN_GATED_NOTE",
    "build_briefing",
    "stub_briefing",
]
