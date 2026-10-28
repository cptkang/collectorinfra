"""글래스박스 브리핑 결정적 조립 (Plan 02 §7).

HolmesGPT의 서술(`answer`)과 severity_judge 판정(`ImportanceVerdict`)을 **6요소 스키마**로
결정적으로 조립한다. LLM 서술을 그대로 신뢰하지 않고:

- **인용 검증**: 도구 출력 인용이 결여된 단정은 "가설"로 강등 표기한다(§7).
- **한계 서술 강제**: 단면 데이터·미수집 신호·증거 불충분을 반드시 명시한다.
- **조치는 권고만**: 실행 경로 없음(D-011). 권고 문자열에 human-gated 안내를 강제한다.

계층: application. 순수 조립 로직으로 domain(severity_signatures · cross_source_episode)만
참조하고 stdlib만 쓴다
(도구 원시 출력 타입 등 application 세부는 dispatcher가 평문으로 넘긴다 — 결합 최소화).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple

from sre_agent.domain.cross_source_episode import (
    APM_TOOLS_NOT_CONNECTED_LIMIT,
    KIND_APM_MEMBER,
    SOURCE_APM,
    SOURCE_INFRA,
    STRONG_ONLY_NOTE,
    EpisodeContext,
    EpisodeLink,
    episode_timeline,
)
from sre_agent.domain.engine_agreement import DIFFERENT_LAYER, LAYER_UNKNOWN, playbook_layer
from sre_agent.domain.evidence_ledger import EvidenceLedger, check_claim, ledger_summary
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

# 증거 소스 라벨(plans/87 §5.4(d) · J3) — `source_labels=True`(apm_guidance_enabled)일 때만 타임라인 앞에 붙인다.
APM_SOURCE_LABEL = "애플리케이션(APM)"
INFRA_SOURCE_LABEL = "인프라"
_APM_TOOL_PREFIX = "apm_"

# 정체 가드로 미결 판정된 조사의 요약 접두어(plans/87 P15 · J3).
UNRESOLVED_PREFIX = "[미결] "

# 관측 도구 연결 실패로 LLM 조사를 생략한 브리핑의 원인 고정 문구(plans/188 C2 · Q1 A안).
SKIPPED_CAUSE = "판단 불가(조사 생략)"

# 값 대조 인용 검증(plans/198 I-4) — 원장에 없는 값 한계 문구와 실을 값 개수 상한.
UNMATCHED_VALUES_LIMIT = "원장에 없는 값: {values}"
UNMATCHED_VALUES_MAX = 10
# `citation_check.unmatched`에 싣는 값 개수 상한(브리핑 저장 상한 32KB 대비).
CITATION_CHECK_UNMATCHED_MAX = 20

# 상관 ≠ 인과 — 결정적 상관에서 도출한 가설의 신뢰도 한계(plans/50 §6.3).
CORRELATION_NOT_CAUSATION_NOTE = "상관 ≠ 인과 — 가설 신뢰도는 선행성·지속성(결정적 상관)에서 도출한 것이며 인과 확정이 아님"
_KIND_LABEL = {"metric": "메트릭", "alarm": "알람", "change": "변경", KIND_APM_MEMBER: "이벤트"}

# 크로스소스 규칙 가설(plans/153 §4.6)의 confidence — 규칙 결합은 측정이 아니라
# 사건 추적기의 판정이므로 기존 상관 가설의 비측정 상한(변경 직후 가설 · plans/91 1-2)과
# 같은 medium에서 멈춘다(R-3).
CROSS_SOURCE_HYPOTHESIS_CONFIDENCE = "medium"


def _fmt_offset(minutes: object) -> str:
    try:
        m = int(minutes)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "T?"
    return f"T{'-' if m < 0 else '+'}{abs(m)}m"


def _timeline_pairs(correlation: dict) -> list[tuple[str, str]]:
    """`(kind, 줄)` 쌍 — 소스 라벨을 kind별로 붙이기 위해 kind를 함께 돌려준다."""
    pairs: list[tuple[str, str]] = []
    for t in correlation.get("timeline") or []:
        if not isinstance(t, dict):
            continue
        kind = str(t.get("kind"))
        offset, label = _fmt_offset(t.get("t_offset_min")), _KIND_LABEL.get(kind, kind)
        pairs.append((kind, f"{offset} {label} {t.get('detail')}"))
    return pairs


def correlation_timeline(correlation: dict) -> list[str]:
    """§9.1 상대시각 타임라인 — `T-15m 메트릭 디스크 IO 급등 …`(수치는 주입값만)."""
    return [line for _, line in _timeline_pairs(correlation)]


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


def neighbor_keys(correlation: dict | None) -> dict[str, Any]:
    """상관 결과의 `neighbors`(plans/198 I-7)를 브리핑 가산 키로 — `neighbors`(이웃·도구 호출 수 ·
    상한·노트)와 `external_cause_candidates`(선행 순). 키가 없으면(플래그 off) 빈 dict(비트 동일).

    가설 순위(`root_cause_hypotheses`)는 바꾸지 않는다(D-341 ④ · D-197 가산만).
    """
    block = (correlation or {}).get("neighbors")
    if not isinstance(block, dict):
        return {}
    return {
        "neighbors": {k: v for k, v in block.items() if k != "external_cause_candidates"},
        "external_cause_candidates": list(block.get("external_cause_candidates") or []),
    }


def _llm_hypothesis(rank: int, llm_cause: str, citations_verified: bool) -> dict:
    return {
        "rank": rank, "cause": llm_cause,
        "confidence": "medium" if citations_verified else "low",
        "evidence": [llm_cause] if citations_verified else [],
        "reasoning": "조사 서술의 인용 원인"
        + ("" if citations_verified else " — 도구 출력 인용 결여로 가설 강등"),
    }


def _lag_phrase(lag: float | None) -> str:
    if lag is None:
        return "시차 미상으로 결합"
    return f"{int(lag)}초 선행" if lag >= 0 else f"{int(-lag)}초 후행(규칙 창 허용)"


_SUPPORT_TEXT = {True: "양쪽 소스 도구 인용 있음", False: "한쪽 소스 도구 인용만 있음",
                 None: "도구 인용 없음"}


def cross_source_hypotheses(
    ctx: EpisodeContext,
    timeline: list[str],
    supported_by_tools: bool | None,
    cited_lines: list[str],
) -> list[dict]:
    """plans/153 §4.6 — 성립 링크마다 규칙 기반 가설 1개(결정적 · confidence 상한 · 상관≠인과).

    문안: "인프라 {kind} 알람이 {lag}초 선행 → 애플리케이션 {was_kind} (규칙 {rule_id})".
    LLM 인용이 양쪽 소스를 모두 뒷받침하면(`supported_by_tools`) 근거에 그 인용 줄을 함께
    싣는다 — 반박 서술도 지우지 않는다(둘 다 보임).
    """
    out: list[dict] = []
    for lk in ctx.valid_links:
        cause_m, effect_m = ctx.member(lk.cause_alarm_id), ctx.member(lk.effect_alarm_id)
        cause_label = ctx.link_label(lk.cause_alarm_id)
        effect_label = ctx.link_label(lk.effect_alarm_id)
        kind = (cause_m.trait if cause_m else "") or cause_label
        was = (effect_m.trait if effect_m else "") or effect_label
        ends = (f"] {cause_label}", f"] {effect_label}")
        evidence = [ln for ln in timeline if "[사건 · " in ln and any(e in ln for e in ends)]
        if supported_by_tools:
            evidence += cited_lines
        lag = _lag_phrase(lk.lag_seconds)
        out.append({
            "rank": 0,
            "cause": f"인프라 {kind} 알람이 {lag} → 애플리케이션 {was} (규칙 {lk.rule_id})",
            "confidence": CROSS_SOURCE_HYPOTHESIS_CONFIDENCE,
            "evidence": evidence or [f"{cause_label} → {effect_label} (규칙 {lk.rule_id})"],
            "reasoning": f"사건 {ctx.episode_id or '?'}의 규칙 {lk.rule_id} 결합"
                         "(같은 호스트 키 strong 매칭 · 창 안) · "
                         f"{_SUPPORT_TEXT[supported_by_tools]}. " + CORRELATION_NOT_CAUSATION_NOTE,
        })
    return out


def _supported_by_tools(cited_lines: list[str], tool_names: list[str]) -> bool | None:
    """§4.7 — 인용 줄의 소스 라벨(`evidence_source_label`) 판정.

    양쪽이면 True · 한쪽이면 False · 인용 없음 None.
    """
    if not cited_lines:
        return None
    labels = {evidence_source_label(c, tool_names) for c in cited_lines}
    return APM_SOURCE_LABEL in labels and INFRA_SOURCE_LABEL in labels


def _cross_source_block(
    ctx: EpisodeContext,
    links: list[EpisodeLink],
    supported: bool | None,
    tool_names: list[str],
    apm_connected: bool,
) -> dict:
    """§4.7 브리핑 선택 키 `cross_source`(6요소는 그대로)."""
    return {
        "episode_id": ctx.episode_id,
        "host_key": ctx.host_key,
        "sources": {SOURCE_INFRA: len(ctx.infra_members), SOURCE_APM: len(ctx.apm_members)},
        "apm_tools_connected": apm_connected,
        "apm_tools_called": sum(1 for n in tool_names if n.startswith(_APM_TOOL_PREFIX)),
        "links": [
            {"rule_id": lk.rule_id, "cause": ctx.link_label(lk.cause_alarm_id),
             "effect": ctx.link_label(lk.effect_alarm_id), "lag_seconds": lk.lag_seconds,
             "supported_by_tools": supported}
            for lk in links
        ],
    }


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


def evidence_source_label(line: str, tool_names: list[str]) -> str:
    """증거 한 줄의 소스 라벨 — 실제 호출된 `apm_*` 도구를 인용하면 애플리케이션(APM), 그 밖은 인프라."""
    if any(name.startswith(_APM_TOOL_PREFIX) and name in line for name in tool_names):
        return APM_SOURCE_LABEL
    return INFRA_SOURCE_LABEL


class _CitationState(NamedTuple):
    """서술 인용 판정 결과(렌더 전 근거 상태) — `build_briefing`의 판정 단계 산출."""

    claims: list[str]
    cited: list[str]
    uncited: list[str]
    verified: bool  # 도구 출력이 하나라도 있고 인용된 단정이 존재해야 "검증됨"
    check: dict[str, Any] | None = None  # 값 대조(plans/198 I-4) 결과 — 원장이 있을 때만


def _judge_values(answer: str, tool_names: list[str], ledger: EvidenceLedger) -> _CitationState:
    """값 대조 인용 판정(plans/198 I-4) — 마커가 아니라 원장 대조로 인용을 가른다.

    단정마다 호스트·수치·시각 토큰을 원장과 맞댄다. 원장에 없는 값이 하나라도 있으면 비인용(가설
    강등), 값이 있고 모두 맞으면 인용이다. 값이 없는 단정은 종전 마커 판정을 따른다.
    `verified`는 도구 출력이 있고 값이 모두 맞은 단정이 하나 이상일 때만 참이다(마커는 세지 않는다).
    도구 출력이 0건이면 종전처럼 아무것도 인용이 아니다.
    """
    claims = _claim_lines(answer)
    cited: list[str] = []
    uncited: list[str] = []
    unmatched: list[str] = []
    checked = demoted = 0
    value_verified = False
    for c in claims:
        chk = check_claim(c, ledger)
        checked += chk.checked
        unmatched.extend(chk.unmatched)
        if chk.unmatched:
            ok = False
            demoted += 1
        elif chk.checked:
            ok = bool(tool_names)
            value_verified = value_verified or ok
        else:
            ok = _is_cited(c, tool_names)
        (cited if ok else uncited).append(c)
    check = {
        "checked": checked,
        "unmatched": list(dict.fromkeys(unmatched)),
        "demoted": demoted,
        "ledger_size": len(ledger.rows),
    }
    return _CitationState(claims, cited, uncited, bool(tool_names) and value_verified, check)


def _unmatched_limitation(unmatched: list[str]) -> str:
    """「원장에 없는 값: …」 한계 1줄(상한 `UNMATCHED_VALUES_MAX` · 넘으면 「외 N개」)."""
    shown = ", ".join(unmatched[:UNMATCHED_VALUES_MAX])
    rest = len(unmatched) - UNMATCHED_VALUES_MAX
    return UNMATCHED_VALUES_LIMIT.format(values=shown + (f" 외 {rest}개" if rest > 0 else ""))


def _judge_citations(answer: str, tool_names: list[str]) -> _CitationState:
    """서술을 단정으로 쪼개 인용/비인용으로 가르고 검증 여부를 판정한다."""
    claims = _claim_lines(answer)
    cited = [c for c in claims if _is_cited(c, tool_names)]
    uncited = [c for c in claims if not _is_cited(c, tool_names)]
    return _CitationState(claims, cited, uncited, bool(tool_names) and bool(cited))


def _render_summary(
    claims: list[str], tool_names: list[str], unresolved: bool, cited: list[str] | None = None
) -> str:
    """요약: 첫 단정. 검증 불가(인용 결여)면 가설 강등 표기, 미결이면 `[미결] ` 접두.

    `cited`(값 대조 판정 결과)를 주면 첫 단정이 그 안에 있을 때만 인용으로 본다.
    """
    head = claims[0] if claims else "조사 서술 없음"
    if cited is not None:
        head_cited = bool(claims) and head in cited
    else:
        head_cited = _is_cited(head, tool_names) and bool(tool_names)
    summary = head if head_cited else HYPOTHESIS_PREFIX + head
    if unresolved:
        summary = UNRESOLVED_PREFIX + summary
    return summary


def _render_llm_cause(claims: list[str], cited: list[str]) -> str:
    """원인: 인용된 단정이 있으면 그중 마지막, 없으면 서술 말미를 가설로."""
    if cited:
        return cited[-1]
    if claims:
        return HYPOTHESIS_PREFIX + claims[-1]
    return HYPOTHESIS_PREFIX + "원인 미확정"


def _corr_pairs(
    correlation: dict[str, Any] | None, cross_source: EpisodeContext | None
) -> list[tuple[str, str]]:
    """상관 타임라인 `(kind, 줄)` — 상관이 없고 사건이 있으면 사건 멤버를 편입한다."""
    corr_pairs = _timeline_pairs(correlation) if correlation else []
    if cross_source is not None and not correlation:
        corr_pairs = _timeline_pairs({"timeline": [
            {"t_offset_min": t.t_offset_min, "kind": t.kind, "detail": t.detail}
            for t in episode_timeline(cross_source)
        ]})
    return corr_pairs


def _render_timeline(
    corr_pairs: list[tuple[str, str]],
    cited: list[str],
    tool_names: list[str],
    correlation: dict[str, Any] | None,
    source_labels: bool,
) -> tuple[list[str], list[str]]:
    """`(상관 줄, 타임라인)` — 상관 타임라인(상대시각 · 주입값) 먼저, 인용 라인은 근거로 뒤에.

    둘 다 없으면 안내 문구 1줄.
    """
    corr_lines = [ln for _, ln in corr_pairs]
    cited_lines = list(cited)
    if source_labels:
        corr_lines = [
            f"[{APM_SOURCE_LABEL if kind == KIND_APM_MEMBER else INFRA_SOURCE_LABEL}] {ln}"
            for kind, ln in corr_pairs
        ]
        cited_lines = [f"[{evidence_source_label(c, tool_names)}] {c}" for c in cited_lines]
    if correlation or corr_lines:
        timeline = corr_lines + cited_lines
        timeline = timeline or ["타임라인 근거 없음(상관 타임라인·도구 출력 인용 모두 결여)"]
    else:
        timeline = cited_lines or ["타임라인 근거 없음(도구 출력 인용 결여)"]
    return corr_lines, timeline


def _merge_rule_hypotheses(
    hypotheses_ranked: list[dict[str, Any]],
    cross_source: EpisodeContext,
    corr_lines: list[str],
    supported: bool | None,
    cited: list[str],
    llm_cause: str,
    citations_verified: bool,
) -> list[dict[str, Any]]:
    """규칙 가설은 결정적 상관 가설 뒤 · LLM 인용 원인 앞(LLM 항목은 항상 마지막) — rank 재부여."""
    corr_hyps = hypotheses_ranked[:-1] if hypotheses_ranked else []
    rules = cross_source_hypotheses(cross_source, corr_lines, supported, cited)
    merged = corr_hyps + rules + [_llm_hypothesis(0, llm_cause, citations_verified)]
    return [{**h, "rank": i} for i, h in enumerate(merged, start=1)]


def _render_bottleneck(verdict: ImportanceVerdict) -> str:
    """병목: 매칭된 시그니처 라벨에서 도출(없으면 미확정)."""
    if verdict.signals:
        return "; ".join(s.label for s in verdict.signals)
    return "미확정(시그니처 매칭 없음)"


def _render_recommendation(remediation: list[str] | None) -> dict[str, Any]:
    """권고: 제공된 조치 후보 + human-gated 안내 강제(실행 경로 없음)."""
    rec_items = list(remediation or [])
    if not rec_items:
        rec_items = ["조치 권고 없음(remediation_recommender off 또는 근거 시그니처 무매칭)"]
    return {"items": rec_items, "note": HUMAN_GATED_NOTE}


def _render_limitations(
    limitations: list[str] | None,
    verdict: ImportanceVerdict,
    citations_verified: bool,
    correlation: dict[str, Any] | None,
    cross_source: EpisodeContext | None,
    apm_tools_connected: bool,
    skipped: bool = False,
) -> list[str]:
    """한계: 제공된 한계 + 자동 부가(단면 데이터·증거 불충분·미검증 인용·상관·크로스소스).

    `skipped`(plans/188 A안 — LLM 조사 생략)면 조사 서술 전제의 세 줄(단면 데이터·증거 불충분·
    인용 결여 강등)을 싣지 않는다. 상관·크로스소스 노트는 그대로다.
    """
    limits = list(limitations or [])
    if not skipped:
        limits.append("프로세스·자원 스냅샷은 조사 시점 단면일 수 있음")
        if verdict.evidence_insufficient:
            limits.append("증거 불충분 — 원격 배치에서 로그·대체 카운터 무매칭(상향 보류)")
        if not citations_verified:
            limits.append("도구 출력 인용이 결여돼 서술을 가설로 강등함(글래스박스 검증 불가)")
    if correlation:
        limits.extend(str(n) for n in (correlation.get("notes") or []))
        limits.append(CORRELATION_NOT_CAUSATION_NOTE)
    if cross_source is not None:
        if not apm_tools_connected:
            limits.append(APM_TOOLS_NOT_CONNECTED_LIMIT)
        limits.append(STRONG_ONLY_NOTE)
        limits.extend(cross_source.excluded_link_notes())
        limits.extend(cross_source.notes)
        if cross_source.valid_links and CORRELATION_NOT_CAUSATION_NOTE not in limits:
            limits.append(CORRELATION_NOT_CAUSATION_NOTE)
    return limits


def _render_severity(verdict: ImportanceVerdict, gate_tier: str | None) -> dict[str, Any]:
    """중요도 헤더: severity_judge 판정 + 게이트 근거."""
    return {
        "level": verdict.level,
        "confidence": verdict.confidence,
        "escalate": verdict.escalate,
        "gate_tier": gate_tier,
        "signals": [s.name for s in verdict.signals],
        "evidence_insufficient": verdict.evidence_insufficient,
    }


def build_briefing(
    *,
    answer: str,
    verdict: ImportanceVerdict,
    tool_names: list[str] | None = None,
    gate_tier: str | None = None,
    remediation: list[str] | None = None,
    limitations: list[str] | None = None,
    correlation: dict | None = None,
    source_labels: bool = False,
    unresolved: bool = False,
    cross_source: EpisodeContext | None = None,
    apm_tools_connected: bool = False,
    investigation_skipped: bool = False,
    skipped_cause: str = SKIPPED_CAUSE,
    evidence_ledger: EvidenceLedger | None = None,
    ledger_masker: Callable[[str], str] | None = None,
) -> dict:
    """6요소 브리핑 dict를 결정적으로 조립한다.

    인용 검증: 근거 인용이 없는 단정은 가설로 강등하고 `hypotheses`에 모은다. 도구 출력이
    전무하거나(tool_names 없음) 인용된 단정이 하나도 없으면 요약/원인을 가설로 표기한다.

    `correlation`(plans/50 G4 · `CorrelationResult.to_dict()`)이 있으면 **그 수치가 정본**이다 —
    타임라인은 상대시각 항목(§9.1)으로, 원인은 rank·confidence 가설(§7.2)로 조립하고 `notes`를
    한계에 싣는다. 없으면 `root_cause_hypotheses`만 빈 목록이고 나머지는 종전과 같다.

    `source_labels`(plans/87 J3 — apm_guidance_enabled): 타임라인 증거 앞에 `[애플리케이션(APM)]`
    (호출된 apm_* 인용) · `[인프라]`(그 밖 · 상관 타임라인) 라벨을 붙인다. `unresolved`(정체 가드):
    요약 앞에 `[미결] `을 붙인다(사유는 호출자가 `limitations`에 싣는다).
    둘 다 기본 False — 종전과 같다.

    `cross_source`(plans/153 §4.6·§4.7 — 두 소스 멤버가 있는 사건만 dispatcher가 넘긴다):
    상관 타임라인이 없으면 사건 멤버를 타임라인에 편입하고(있으면 사전수집이 이미 편입),
    성립 링크마다 규칙 가설을 LLM 원인 앞에 넣는다(`cause`는 바꾸지 않는다 — 규칙은 가설).
    한계에 strong 매칭 고지·절단·제외 링크와 APM 도구 미연결(`apm_tools_connected=False`)을
    강제하고 선택 키 `cross_source`를 더한다. None이면 종전과 같다.

    `investigation_skipped`(plans/188 C2 · Q1 A안 — 관측 도구 연결 실패로 LLM 조사 생략): 서술을
    단정·가설로 다루지 않는다. 요약은 `[미결] ` + 서술 첫 줄(가설 표기 없음), 원인은
    `SKIPPED_CAUSE`, `hypotheses`는 빈 목록, 원인 가설은 결정적 상관·규칙 가설만 남긴다.
    생략 사유는 호출자가 `limitations`에 싣는다. 기본 False — 종전과 같다.
    `skipped_cause`(plans/188 C7): 생략 브리핑의 원인 문구 — 조사 LLM 답변 형식 불일치 종결은
    dispatcher가 고정 요약을 `answer`로, 전용 원인 문구를 여기로 넘긴다(원문은 넘기지 않는다).

    `evidence_ledger`(plans/198 I-4 — `citation_value_check_enabled`): 인용을 마커가 아니라 원장 값
    대조로 판정한다(`_judge_values`). 원장에 없는 값이 있는 단정은 가설로 강등하고 한계에
    「원장에 없는 값: …」을 싣는다. 가설 순위는 바꾸지 않는다. 가산 키 `citation_check`
    (`checked`·`unmatched`·`demoted`·`ledger_size`)와 `evidence_ledger`(`ledger_summary` —
    `ev_id`·도구·인자 요약 — 인자는 `ledger_masker`로 가린다)를 더한다. None이면 종전과 같다.
    조사 생략 브리핑에는 쓰지 않는다.
    """
    if investigation_skipped:
        return _skipped_briefing(answer, verdict, gate_tier, remediation, limitations,
                                 correlation, source_labels, cross_source, apm_tools_connected,
                                 skipped_cause)
    tool_names = tool_names or []
    if evidence_ledger is None:
        st = _judge_citations(answer, tool_names)
        summary = _render_summary(st.claims, tool_names, unresolved)
    else:
        st = _judge_values(answer, tool_names, evidence_ledger)
        summary = _render_summary(st.claims, tool_names, unresolved, st.cited)
    llm_cause = _render_llm_cause(st.claims, st.cited)
    corr_lines, timeline = _render_timeline(
        _corr_pairs(correlation, cross_source), st.cited, tool_names, correlation, source_labels
    )

    # 원인 가설(§7.2): 결정적 상관에서 먼저, LLM 인용 원인은 마지막.
    # rank 1이 `cause` 문자열이 된다.
    hypotheses_ranked = root_cause_hypotheses(correlation, llm_cause, st.verified)
    cause = hypotheses_ranked[0]["cause"] if correlation and hypotheses_ranked else llm_cause
    supported = _supported_by_tools(st.cited, tool_names) if cross_source is not None else None
    if cross_source is not None and cross_source.valid_links:
        # `cause`는 그대로 둔다.
        hypotheses_ranked = _merge_rule_hypotheses(
            hypotheses_ranked, cross_source, corr_lines, supported, st.cited, llm_cause, st.verified
        )

    limits = _render_limitations(
        limitations, verdict, st.verified, correlation, cross_source, apm_tools_connected
    )
    if st.check is not None and st.check["unmatched"]:
        limits.append(_unmatched_limitation(st.check["unmatched"]))
    briefing = {
        "severity": _render_severity(verdict, gate_tier),
        "summary": summary,
        "timeline": timeline,
        "bottleneck": _render_bottleneck(verdict),
        "cause": cause,
        "root_cause_hypotheses": hypotheses_ranked,
        "recommendation": _render_recommendation(remediation),
        "limitations": limits,
        "citations_verified": st.verified,
        "hypotheses": st.uncited,
    }
    if cross_source is not None:
        briefing["cross_source"] = _cross_source_block(
            cross_source, list(cross_source.valid_links), supported, tool_names, apm_tools_connected
        )
    if evidence_ledger is not None and st.check is not None:
        briefing["citation_check"] = {
            **st.check, "unmatched": st.check["unmatched"][:CITATION_CHECK_UNMATCHED_MAX]
        }
        briefing["evidence_ledger"] = ledger_summary(
            evidence_ledger, masker=ledger_masker or (lambda text: text)
        )
    briefing.update(neighbor_keys(correlation))
    return briefing


# ── 플레이북 모드 브리핑(plans/187 W4 · §5.0.1 5번 · §5.5) ────────────────────────────────
#
# `SRE_PLAYBOOK_ENABLED` on이고 플레이북이 돈 잡만 쓴다. 6요소는 **플레이북 종합**
# (결정적 가설 순위 · 대응 · S5 타임라인 · 지배 계층 · 결손)으로 채우고, HolmesGPT 결과는
# 선택 키 `deep_dive`에만 붙는다(가설 순위를 바꾸지 않는다 — D-035). off 경로는
# `build_briefing`을 그대로 쓴다.

#: 타임라인 스텝 액션(S5 연관 알람) — 상관 결과가 없을 때 이 섹션 표를 쓴다.
_TIMELINE_ACTION = "xsource.related_alarms"
_SYNTHESIZE_ACTION = "synthesize"
SNAPSHOT_LIMIT = "프로세스·자원 스냅샷은 조사 시점 단면일 수 있음"
REALTIME_BADGE_PREFIX = "현재값"
DEEP_DIVE_SKIPPED_LIMIT = "심층 조사(HolmesGPT) 생략 — {reason}"
ENGINE_DISAGREEMENT_LIMIT = (
    "엔진 간 불일치 — {reason} (대략적 대조 · 판정·가설 순위는 플레이북 기준)"
)


def _pb_summary(playbook: dict, hyps: list[dict], verdict: ImportanceVerdict) -> str:
    judged = f"판정 {verdict.level}(신뢰도 {verdict.confidence})"
    head = f"[플레이북 {playbook.get('id') or '미상'}] "
    if not hyps:
        return head + f"1순위 가설 없음(판독 근거 부족) · {judged}"
    top = hyps[0]
    return head + f"1순위 가설: {top.get('title')}({top.get('status_label')}) · {judged}"


def _pb_cause(hyps: list[dict]) -> str:
    if not hyps:
        return "원인 가설 없음 — 판독 근거 부족(결손은 한계 참조)"
    lines = []
    for h in hyps:
        steps = "·".join(str(s) for s in h.get("evidence_steps") or [])
        basis = f" (근거 {steps})" if steps else ""
        lines.append(f"{h.get('rank')}위 {h.get('title')} — {h.get('status_label')}{basis}")
    return "\n".join(lines)


def _pb_hypotheses(hyps: list[dict]) -> list[dict]:
    """`root_cause_hypotheses` 계약 키(rank·cause·confidence·evidence·reasoning)로 옮긴다.

    `confidence` 자리에는 상태 라벨(지지·판단 보류·반박)을 싣는다 — 플레이북 가설은 신뢰도가 아니라
    규칙 충족 상태로 순위를 매긴다.
    """
    return [
        {
            "rank": h.get("rank"),
            "cause": h.get("title"),
            "confidence": h.get("status_label"),
            "status": h.get("status"),
            "evidence": [str(s) for s in h.get("evidence_steps") or []],
            "reasoning": h.get("rule_id"),
        }
        for h in hyps
    ]


def _pb_timeline(sections: list[dict], correlation: dict | None) -> list[str]:
    """타임라인 = S5(연관 알람)로 다시 계산한 상관 타임라인, 없으면 S5 표 행."""
    lines = correlation_timeline(correlation) if correlation else []
    if lines:
        return lines
    s5 = next((s for s in sections if s.get("action") == _TIMELINE_ACTION), None)
    if s5 is None:
        return []
    table = s5.get("table") or {}
    rows = [" · ".join(str(c) for c in row) for row in table.get("rows") or []]
    if rows:
        return [f"[{s5.get('step')}] {r}" for r in rows]
    return [f"[{s5.get('step')}] 연관 알람 없음({s5.get('status')}" + (
        f" — {s5['reason']})" if s5.get("reason") else ")"
    )]


def _pb_bottleneck(readings: dict, verdict: ImportanceVerdict) -> str:
    layer, basis = playbook_layer(readings)
    if layer != LAYER_UNKNOWN:
        return f"지배 계층 {layer} (판독 {', '.join(basis)})"
    if verdict.signals:
        return "; ".join(s.label for s in verdict.signals)
    return "미확정(판독 근거·시그니처 매칭 없음)"


def _pb_limitations(
    playbook: dict,
    limitations: list[str] | None,
    verdict: ImportanceVerdict,
    correlation: dict | None,
    deep_dive: dict | None,
    engine_agreement: dict | None,
    cross_source: EpisodeContext | None,
    apm_tools_connected: bool,
) -> list[str]:
    """한계 — 결손·생략 스텝 · 실시간 단면 · n/a 판독 · 플레이북 주석 · 심층 생략 · 엔진 불일치."""
    limits = list(limitations or [])
    for s in playbook.get("sections") or []:
        if s.get("action") == _SYNTHESIZE_ACTION:
            continue
        status = s.get("status")
        if status in ("missing", "skipped"):
            word = "수집 결손" if status == "missing" else "생략"
            reason = f" — {s['reason']}" if s.get("reason") else ""
            limits.append(f"{s.get('step')} {s.get('title')}: {word}{reason}")
        realtime = [b for b in s.get("badges") or [] if str(b).startswith(REALTIME_BADGE_PREFIX)]
        if realtime:
            limits.append(f"{s.get('step')} {s.get('title')}: {realtime[0]} — 사건 시점 값이 아님")
    na = [k for k, v in (playbook.get("readings") or {}).items() if v == "n/a"]
    if na:
        limits.append("판독 불가(n/a — 근거 부족): " + ", ".join(na))
    limits.extend(str(n) for n in playbook.get("notes") or [])
    limits.append(SNAPSHOT_LIMIT)
    if verdict.evidence_insufficient:
        limits.append("증거 불충분 — 원격 배치에서 로그·대체 카운터 무매칭(상향 보류)")
    if correlation:
        limits.extend(str(n) for n in (correlation.get("notes") or []))
        limits.append(CORRELATION_NOT_CAUSATION_NOTE)
    if deep_dive and deep_dive.get("status") == "skipped":
        limits.append(DEEP_DIVE_SKIPPED_LIMIT.format(reason=deep_dive.get("reason") or "사유 미상"))
    if engine_agreement and engine_agreement.get("result") == DIFFERENT_LAYER:
        limits.append(ENGINE_DISAGREEMENT_LIMIT.format(reason=engine_agreement.get("reason")))
    if cross_source is not None:
        if not apm_tools_connected:
            limits.append(APM_TOOLS_NOT_CONNECTED_LIMIT)
        limits.append(STRONG_ONLY_NOTE)
        limits.extend(cross_source.excluded_link_notes())
        limits.extend(cross_source.notes)
    return list(dict.fromkeys(limits))


def build_playbook_briefing(
    *,
    playbook: dict,
    verdict: ImportanceVerdict,
    gate_tier: str | None = None,
    remediation: list[str] | None = None,
    limitations: list[str] | None = None,
    correlation: dict | None = None,
    deep_dive: dict | None = None,
    engine_agreement: dict | None = None,
    cross_source: EpisodeContext | None = None,
    apm_tools_connected: bool = False,
    tool_names: list[str] | None = None,
) -> dict:
    """플레이북 종합으로 6요소 브리핑을 조립한다(결정적 — LLM 서술은 `playbook` 안에만).

    playbook: dispatcher가 만든 `playbook` 블록(`PlaybookRun.to_briefing_dict()` + 평탄 `readings`).
    6요소: 요약 = 1순위 가설 + 판정 · 원인 = 상태 라벨이 붙은 가설 순위 · 권고 = 플레이북 대응 제안
    (알려진 오류 우회 > 런북 완화 > 응답 힌트 · W8) + 시그니처 조치 후보 · 타임라인 = S5 ·
    병목 = 지배 계층 · 한계 = 결손·실시간 단면·n/a 등.
    선택 키: `playbook` · `deep_dive`(있을 때) · `engine_agreement`(있을 때) · `cross_source`(사건 ·
    `tool_names` = 두 엔진이 부른 도구 — APM 호출 수 집계).
    결론이 다르면(`different_layer`) 두 결론을 그대로 두고 한계에 「엔진 간 불일치」를 싣는다.
    """
    synthesis = playbook.get("synthesis") or {}
    hyps = list(synthesis.get("hypotheses") or [])
    sections = list(playbook.get("sections") or [])
    # 알려진 오류 우회(W8)는 remediation 형식 한 줄(`line` — 위험도·신뢰도·근거·검증·롤백)로 싣는다.
    items = [
        str(a.get("line") or a.get("text"))
        for a in synthesis.get("actions") or []
        if a.get("text")
    ]
    items += [r for r in remediation or [] if r not in items]
    if not items:
        items = ["조치 권고 없음(플레이북 대응 제안·근거 시그니처 없음)"]
    briefing: dict[str, Any] = {
        "severity": _render_severity(verdict, gate_tier),
        "summary": _pb_summary(playbook, hyps, verdict),
        "timeline": _pb_timeline(sections, correlation),
        "bottleneck": _pb_bottleneck(playbook.get("readings") or {}, verdict),
        "cause": _pb_cause(hyps),
        "root_cause_hypotheses": _pb_hypotheses(hyps),
        "recommendation": {"items": items, "note": HUMAN_GATED_NOTE},
        "limitations": _pb_limitations(
            playbook, limitations, verdict, correlation, deep_dive, engine_agreement,
            cross_source, apm_tools_connected,
        ),
        "citations_verified": True,
        "hypotheses": [],
        "playbook": playbook,
    }
    if deep_dive is not None:
        briefing["deep_dive"] = deep_dive
    if engine_agreement is not None:
        briefing["engine_agreement"] = engine_agreement
    if cross_source is not None:
        briefing["cross_source"] = _cross_source_block(
            cross_source, list(cross_source.valid_links), None, list(tool_names or []),
            apm_tools_connected,
        )
    return briefing


def _skipped_briefing(
    answer: str,
    verdict: ImportanceVerdict,
    gate_tier: str | None,
    remediation: list[str] | None,
    limitations: list[str] | None,
    correlation: dict[str, Any] | None,
    source_labels: bool,
    cross_source: EpisodeContext | None,
    apm_tools_connected: bool,
    skipped_cause: str = SKIPPED_CAUSE,
) -> dict[str, Any]:
    """LLM 조사를 생략한 브리핑(plans/188 A안) — 서술은 요약에만, 오류 원문은 호출자 한계에만."""
    claims = _claim_lines(answer)
    corr_lines, timeline = _render_timeline(
        _corr_pairs(correlation, cross_source), [], [], correlation, source_labels
    )
    hypotheses_ranked = root_cause_hypotheses(correlation, "", False)
    if cross_source is not None and cross_source.valid_links:
        rules = cross_source_hypotheses(cross_source, corr_lines, None, [])
        hypotheses_ranked = [{**h, "rank": i}
                             for i, h in enumerate(hypotheses_ranked + rules, start=1)]
    briefing = {
        "severity": _render_severity(verdict, gate_tier),
        "summary": UNRESOLVED_PREFIX + (claims[0] if claims else "조사 생략"),
        "timeline": timeline,
        "bottleneck": _render_bottleneck(verdict),
        "cause": skipped_cause,
        "root_cause_hypotheses": hypotheses_ranked,
        "recommendation": _render_recommendation(remediation),
        "limitations": _render_limitations(
            limitations, verdict, False, correlation, cross_source, apm_tools_connected,
            skipped=True,
        ),
        "citations_verified": False,
        "hypotheses": [],
    }
    if cross_source is not None:
        briefing["cross_source"] = _cross_source_block(
            cross_source, list(cross_source.valid_links), None, [], apm_tools_connected
        )
    briefing.update(neighbor_keys(correlation))
    return briefing


#: 부분 브리핑 사유 어휘(plans/198 I-10) → 사람이 읽는 한 마디.
PARTIAL_REASONS: dict[str, str] = {
    "incomplete": "step 상한 도달",
    "stalled": "조사 정체로 중단",
    "timeout": "전체 시간 상한 초과",
}
PARTIAL_CAUSE = HYPOTHESIS_PREFIX + "원인 미확정(조사 미완주 — 결론 서술 없음)"
PARTIAL_LIMIT = (
    "조사 미완주({why}) — LLM 결론 서술 없이 회수한 도구 결과 {n}건만 정리함"
    "(근거 목록은 결론이 아님)"
)
PARTIAL_NO_EVIDENCE = "회수한 도구 결과 없음"


def build_partial_briefing(
    *,
    reason: str,
    verdict: ImportanceVerdict,
    ledger: EvidenceLedger,
    gate_tier: str | None = None,
    remediation: list[str] | None = None,
    limitations: list[str] | None = None,
    correlation: dict | None = None,
    steps: int | None = None,
    ledger_masker: Callable[[str], str] | None = None,
    cross_source: EpisodeContext | None = None,
    apm_tools_connected: bool = False,
    source_labels: bool = False,
) -> dict:
    """미완주·정체·타임아웃 조사의 결정적 부분 브리핑(plans/198 I-10 · 154 H-5).

    LLM 서술을 쓰지 않는다 — 회수한 도구 결과(증거 원장 행)를 근거 목록으로 타임라인에 싣고
    (원시 출력 없이 `ev_id`·도구·인자 요약), 원인은 결정적 상관 가설이 있으면 그것, 없으면
    「원인 미확정」이다.
    요약은 `[미결] `로 시작한다. 6요소 키에 가산 키 `partial`(`reason`·`evidence_rows`·`steps`)과
    `evidence_ledger`(`ledger_summary`)를 더한다. `reason`은 `PARTIAL_REASONS`의 키다.

    `cross_source`·`apm_tools_connected`·`source_labels`는 `build_briefing`과 같은 뜻이다(재검토
    Minor7 — 부분 브리핑에서도 사건 멤버 타임라인·소스 라벨·크로스소스 한계·`cross_source` 키를
    싣는다). 인용 서술이 없으므로 규칙 가설의 도구 뒷받침은 「도구 인용 없음」이다.
    기본값이면 종전과 같다.
    """
    why = PARTIAL_REASONS.get(reason, reason)
    rows = ledger_summary(ledger, masker=ledger_masker or (lambda text: text))
    n = len(ledger.rows)
    evidence_lines = [f"[{r['ev_id']}] {r['tool']} {r['args']}".rstrip() for r in rows]
    corr_lines = correlation_timeline(correlation) if correlation else []
    tool_names = [r.tool for r in ledger.rows]
    if cross_source is not None or source_labels:
        corr_lines, _ = _render_timeline(
            _corr_pairs(correlation, cross_source), [], tool_names, correlation, source_labels
        )
        if source_labels:
            evidence_lines = [
                f"[{evidence_source_label(ln, tool_names)}] {ln}" for ln in evidence_lines
            ]
    timeline = corr_lines + evidence_lines or [f"타임라인 근거 없음({PARTIAL_NO_EVIDENCE})"]
    hypotheses_ranked = root_cause_hypotheses(correlation, PARTIAL_CAUSE, False)
    cause = hypotheses_ranked[0]["cause"] if correlation and hypotheses_ranked else PARTIAL_CAUSE
    if cross_source is not None and cross_source.valid_links:
        hypotheses_ranked = _merge_rule_hypotheses(
            hypotheses_ranked, cross_source, corr_lines, None, [], PARTIAL_CAUSE, False
        )
    limits = _render_limitations(
        [*(limitations or []), PARTIAL_LIMIT.format(why=why, n=n)],
        verdict, False, correlation, cross_source, apm_tools_connected, skipped=True,
    )
    extra: dict[str, Any] = {}
    if cross_source is not None:
        extra["cross_source"] = _cross_source_block(
            cross_source, list(cross_source.valid_links), None, tool_names, apm_tools_connected
        )
    return {
        "severity": _render_severity(verdict, gate_tier),
        "summary": f"{UNRESOLVED_PREFIX}조사 미완주({why}) — 회수한 도구 결과 {n}건으로 부분 정리",
        "timeline": timeline,
        "bottleneck": _render_bottleneck(verdict),
        "cause": cause,
        "root_cause_hypotheses": hypotheses_ranked,
        "recommendation": _render_recommendation(remediation),
        "limitations": limits,
        "citations_verified": False,
        "hypotheses": [],
        "partial": {"reason": reason, "evidence_rows": n, "steps": steps},
        "evidence_ledger": rows,
        **extra,
        **neighbor_keys(correlation),
    }


def stub_briefing(message: str) -> dict:
    """조사 미실행(스텁) 시의 브리핑 표기 — 6요소 조립을 생략하고 사유만 노출한다."""
    return {"stub": True, "message": message, "elements": None}


__all__ = [
    "BRIEFING_ELEMENTS",
    "CORRELATION_NOT_CAUSATION_NOTE",
    "correlation_timeline",
    "root_cause_hypotheses",
    "neighbor_keys",
    "cross_source_hypotheses",
    "CROSS_SOURCE_HYPOTHESIS_CONFIDENCE",
    "CITATION_MARKERS",
    "UNMATCHED_VALUES_LIMIT",
    "HYPOTHESIS_PREFIX",
    "HUMAN_GATED_NOTE",
    "APM_SOURCE_LABEL",
    "INFRA_SOURCE_LABEL",
    "UNRESOLVED_PREFIX",
    "evidence_source_label",
    "build_briefing",
    "build_playbook_briefing",
    "build_partial_briefing",
    "PARTIAL_REASONS",
    "stub_briefing",
]
