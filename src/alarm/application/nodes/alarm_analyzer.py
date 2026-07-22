"""알람 LLM 분석 노드.

Redis Stream에서 소비된 AlarmEvent를 LLM으로 분석하여
AlarmAnalysisResult를 생성한다.

LLM 응답 형식 (JSON):
    {
        "severity_label": "심각" | "경고" | "주의" | "해소",
        "summary": "...",
        "probable_cause": "...",
        "recommended_action": "...",
        "pattern_type": "첫 발생" | "주기적" | "급증" | "산발적",
        "is_routine": true | false,
        "pattern_analysis": "...",
        "ai_message_severity": 1 | 2 | 3 | null,   # E3: 메시지형 알람 상향 등급(상향 전용)
        "ai_severity_reason": "..."                # E3: 상향 근거
    }

패턴 필드(pattern_type/is_routine/pattern_analysis)는 `parsed.get()` 기본값으로
처리한다 — LLM이 누락 응답해도 기존 분석 결과 생성에 실패하지 않는다 (Plan 47 §5.6).

이력 통계(history_section)와 영향 프로세스(process_section, Plan 47-1)는 state에 있으면
프롬프트에 주입하고, 없으면 빈 문자열로 주입한다 (graceful degradation).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from src.alarm.domain.alarm import (
    AlarmAnalysisResult,
    AlarmEvent,
    AlarmHistoryStats,
    ProcessSnapshot,
)
from src.alarm.domain.severity_signatures import scan_signature_severity
from src.alarm.prompts.alarm_analyzer import (
    ALARM_ANALYZER_SYSTEM_PROMPT,
    ALARM_ANALYZER_USER_TEMPLATE,
)
from src.llm import create_llm

logger = logging.getLogger(__name__)

_SEVERITY_LABELS = {1: "주의", 2: "경고", 3: "심각"}

# E3: 임계형(메트릭) 자원 토큰 — resource_type에 포함되면 메시지형 알람에서 제외한다.
_METRIC_RESOURCE_TOKENS = ("cpu", "memory", "disk", "filesystem", "network")


def is_message_alarm(event: AlarmEvent) -> bool:
    """메시지형 알람(condition_log 보유) 여부를 보수적으로 판정한다 (Plan 52 E3).

    LogMonitor/보안/앱 로그처럼 conditionLog에 실제 로그 메시지가 담긴 알람에만
    LLM 메시지 심각도 자문을 적용한다. CPU/메모리 등 수치 임계형(condition_log가 측정값)
    자원은 제외한다 — resource_type에 메트릭 토큰이 포함되면 False.
    """
    if not (event.condition_log and event.condition_log.strip()):
        return False
    resource_type = (event.resource_type or "").lower()
    if any(token in resource_type for token in _METRIC_RESOURCE_TOKENS):
        return False
    return True


def _coerce_severity_int(value: Any) -> Optional[int]:
    """LLM이 반환한 ai_message_severity 후보를 안전하게 1~3 정수로 변환한다.

    문자열 숫자("3")·None·범위 밖·bool 등 비정상 입력은 None으로 처리한다(환각 방어).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 3 else None
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            v = int(s)
            return v if 1 <= v <= 3 else None
    return None


def _extract_json(text: str) -> dict:
    """LLM 응답 텍스트에서 JSON 객체를 추출한다.

    마크다운 코드 블록(```json ... ```)으로 감싸진 경우와
    일반 텍스트에 JSON이 포함된 경우를 모두 처리한다.
    """
    # 1) 마크다운 코드 블록 안의 JSON 추출
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        return json.loads(code_block.group(1))

    # 2) 직접 파싱 (순수 JSON 응답)
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 3) 텍스트에서 첫 번째 { ... } 블록 추출
    brace_match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))

    raise ValueError(f"LLM 응답에서 JSON을 찾을 수 없습니다: {text[:200]!r}")


def _render_history_section(
    stats: AlarmHistoryStats,
    event: AlarmEvent,
    alarm_cfg,  # noqa: ANN001 — AlarmConfig
) -> str:
    """AlarmHistoryStats를 LLM 프롬프트용 텍스트로 렌더링한다 (Plan 47 §5.5)."""
    lines = [
        f"[알람 이력 통계 — 최근 {alarm_cfg.history_lookback_days}일, "
        "동일 서버·동일 알람 (폴스타 DB 조회)]"
    ]
    if stats.truncated:
        lines.append(
            f"(이력 일부만 반영 — 최근 {alarm_cfg.history_max_rows:,}건 한정)"
        )
    lines.append(
        f"- 발생 횟수: {stats.total_count}건 "
        f"(24시간: {stats.count_24h}건 / 7일: {stats.count_7d}건 / 30일: {stats.count_30d}건)"
    )
    lines.append(f"- 동일 자원({event.resource_name}): {stats.same_resource_count}건")

    if stats.first_seen is not None and stats.last_seen is not None:
        hours_ago = (event.alarm_time - stats.last_seen).total_seconds() / 3600.0
        lines.append(
            f"- 최초/직전 발생: {stats.first_seen:%Y-%m-%d %H:%M} / "
            f"{stats.last_seen:%Y-%m-%d %H:%M} ({hours_ago:.0f}시간 전)"
        )
    else:
        lines.append("- 발생 이력 없음 (조회 기간 내 첫 발생)")

    if stats.hour_histogram:
        dist = ", ".join(
            f"{hour:02d}시 {count}건"
            for hour, count in sorted(
                stats.hour_histogram.items(), key=lambda kv: (-kv[1], kv[0])
            )
        )
        lines.append(f"- 시간대 분포(최근 30일): {dist}")

    if stats.median_interval_minutes is not None:
        interval = f"- 발생 간격: 중앙값 {stats.median_interval_minutes / 60.0:.1f}시간"
        if stats.interval_cv is not None:
            interval += f", 변동계수 {stats.interval_cv:.2f}"
        if stats.period_label:
            interval += f" (간격 일정 — {stats.period_label})"
        lines.append(interval)

    pre = stats.pre_classification
    if stats.period_label:
        pre += f" ({stats.period_label})"
    lines.append(f"- 사전 분류: {pre}")
    lines.append(f"- 이번 발생 시각: {event.alarm_time:%Y-%m-%d %H:%M}  ← 시간대 비교용")
    return "\n".join(lines)


def _render_process_section(snapshot: ProcessSnapshot) -> str:
    """ProcessSnapshot을 LLM 프롬프트용 텍스트로 렌더링한다 (Plan 47-1 §5.5).

    수치·선별·마스킹은 이미 결정적으로 완료된 상태이며, LLM은 상위 프로세스를
    원인/권고에 인용만 한다 (새로 계산 금지). args는 마스킹된 값만 노출된다.
    """
    metric_label = "메모리" if snapshot.alarm_kind == "memory" else "CPU"
    captured = (
        f"{snapshot.captured_at:%Y-%m-%d %H:%M:%S} 기준, "
        if snapshot.captured_at is not None
        else ""
    )
    lines = [
        f"[영향 프로세스 — {metric_label} 상위 "
        f"({captured}전체 {snapshot.total_count}개)]"
    ]
    if not snapshot.top:
        lines.append("- 조회된 프로세스 없음")
        return "\n".join(lines)
    for i, p in enumerate(snapshot.top, start=1):
        args = p.args if p.args else "-"
        lines.append(
            f"{i}. {p.name:<10} pid {p.pid} user {p.user}  "
            f"메모리 {p.pmem:.1f}% · CPU {p.p100cpu:.1f}%  args: {args}"
        )
    return "\n".join(lines)


def _render_feedback_section(examples: list[dict]) -> str:
    """운영자 피드백 few-shot 예시를 LLM 프롬프트용 텍스트로 렌더링한다 (Plan 52 E4).

    examples가 빈 리스트면 빈 문자열을 반환한다(섹션 미주입 — 프롬프트 규칙상
    [운영자 피드백] 섹션이 없으면 LLM은 llm_actionability=null 을 출력한다).
    각 예시는 `- [유효|노이즈] alarm_name (resource_name, pattern): note` 형식으로 렌더한다.
    """
    if not examples:
        return ""
    label_ko = {"valid": "유효", "noise": "노이즈"}
    lines = ["[운영자 피드백 — 유사 과거 알람에 대한 운영자 라벨]"]
    for ex in examples:
        label = label_ko.get(ex.get("label", ""), str(ex.get("label", "")))
        alarm_name = str(ex.get("alarm_name", ""))
        meta = ", ".join(
            p for p in (str(ex.get("resource_name", "")), str(ex.get("pattern", ""))) if p
        )
        note = str(ex.get("note", ""))
        line = f"- [{label}] {alarm_name}"
        if meta:
            line += f" ({meta})"
        if note:
            line += f": {note}"
        lines.append(line)
    return "\n".join(lines)


async def alarm_analyzer_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """알람 이벤트를 LLM으로 분석하여 AlarmAnalysisResult를 반환한다.

    Args:
        state: LangGraph 상태 딕셔너리 (alarm_event 필드 필수,
            history_stats 필드 선택 — Plan 47 이력 통계)
        config: LangGraph configurable 설정 (app_config 필드 필수)

    Returns:
        analysis_result 또는 error 업데이트
    """
    event = state["alarm_event"]
    cfg = config["configurable"]["app_config"]
    llm = create_llm(cfg)

    # Plan 47: 이력 통계가 있으면 프롬프트에 주입, 없으면 빈 문자열
    stats: Optional[AlarmHistoryStats] = state.get("history_stats")
    history_section = ""
    if stats is not None:
        history_section = "\n" + _render_history_section(stats, event, cfg.alarm)

    # Plan 47-1: 영향 프로세스 스냅샷이 있으면 프롬프트에 주입, 없으면 빈 문자열
    snapshot: Optional[ProcessSnapshot] = state.get("process_snapshot")
    process_section = ""
    if snapshot is not None:
        process_section = "\n" + _render_process_section(snapshot)

    # Plan 52 E4: LLM 액션가능성 few-shot — 게이트 활성(enable_llm_actionability) + feedback_store
    # 주입 시에만 유사 과거 피드백을 조회해 프롬프트에 주입한다. 추가 LLM 호출은 없으며(단일
    # 응답 재파싱), off/미주입/실패면 빈 문자열 → 프롬프트 규칙상 LLM이 null을 출력한다(회귀 0).
    feedback_section = ""
    gate = getattr(cfg, "noise_gate", None)
    if gate is not None and getattr(gate, "enable_llm_actionability", False):
        fb_store = config["configurable"].get("feedback_store")
        if fb_store is not None:
            try:
                pattern = stats.pre_classification if stats is not None else ""
                examples = fb_store.find_similar(
                    alarm_name=event.alarm_name,
                    resource_name=event.resource_name,
                    pattern=pattern or "",
                    limit=getattr(gate, "actionability_fewshot_count", 3),
                )
                rendered = _render_feedback_section(examples)
                if rendered:
                    feedback_section = "\n" + rendered
            except Exception:  # noqa: BLE001 — few-shot 없이 진행(graceful)
                feedback_section = ""

    severity_label = _SEVERITY_LABELS.get(event.severity, "해소" if event.is_clear else "알 수 없음")
    user_msg = ALARM_ANALYZER_USER_TEMPLATE.format(
        db_id=event.db_id,
        server_name=event.server_name,
        hostname=event.hostname,
        ip_address=event.ip_address,
        resource_ancestry=event.resource_ancestry,
        resource_type=event.resource_type,
        resource_name=event.resource_name,
        alarm_name=event.alarm_name,
        alarm_id=event.alarm_id,
        severity=event.severity,
        severity_label=severity_label,
        alarm_status=event.alarm_status,
        alarm_time=event.alarm_time.strftime("%Y-%m-%d %H:%M:%S"),
        conditions=event.conditions,
        condition_log=event.condition_log,
        history_section=history_section,
        process_section=process_section,
        feedback_section=feedback_section,
    )
    try:
        response = await llm.ainvoke([
            {"role": "system", "content": ALARM_ANALYZER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        parsed = _extract_json(response.content)
        is_routine = parsed.get("is_routine")
        result = AlarmAnalysisResult(
            alarm_event=event,
            severity_label=parsed["severity_label"],
            summary=parsed["summary"],
            probable_cause=parsed["probable_cause"],
            recommended_action=parsed["recommended_action"],
            notification_channels=cfg.alarm.get_notification_channels(),
            # Plan 47 패턴 필드 — LLM 누락 시 기본값 (분석 실패로 처리하지 않음)
            pattern_type=str(parsed.get("pattern_type") or ""),
            is_routine=is_routine if isinstance(is_routine, bool) else None,
            pattern_analysis=str(parsed.get("pattern_analysis") or ""),
        )

        # Plan 52 E3: AI 메시지 심각도 보강(상향 전용) — 추가 LLM 호출 없이 기존 응답 재파싱.
        # 결정적 시그니처 스캔(항상)과 LLM 메시지 해석(메시지형 알람만)을 결합하여, 후보가
        # 원 severity보다 클 때만(상향) 채운다. 정책 계층이 max()로 결합하여 하향은 불가하다.
        ai_sev: Optional[int] = None
        ai_reason = ""
        if cfg.noise_gate.enable_ai_severity_boost:
            sig = scan_signature_severity(event.condition_log or "")
            llm_sev = (
                _coerce_severity_int(parsed.get("ai_message_severity"))
                if is_message_alarm(event)
                else None
            )
            cands = [
                s for s in ((sig[0] if sig else None), llm_sev) if isinstance(s, int)
            ]
            cand = max(cands) if cands else None
            if cand is not None and cand > event.severity:  # 상향일 때만
                ai_sev = cand
                ai_reason = (sig[1] if sig else "") or "LLM 메시지 해석"
        result.ai_message_severity = ai_sev
        result.ai_severity_reason = ai_reason

        # Plan 60 E3: 동적 baseline 이상탐지 상향 후처리(결정적·LLM 무관, §5.2 확정 설계).
        # enricher가 산출한 anomaly_severity(baseline z-score 상향 후보)를 **상향 전용** 가드로
        # ai_message_severity에 병합한다. dynamic_baseline_enabled AND enable_ai_severity_boost
        # (AND 조건) + 후보>event.severity + 후보>기존 ai일 때만 반영 → agentic_enricher·기존
        # sig/llm 보강과 공존해도 셋 다 "후보>기존" 상향 전용이라 결과는 max와 동일(게이트 무변경).
        anomaly_sev = state.get("anomaly_severity")
        if (
            anomaly_sev is not None
            and getattr(cfg.noise_gate, "dynamic_baseline_enabled", False)
            and cfg.noise_gate.enable_ai_severity_boost
            and anomaly_sev > event.severity
            and anomaly_sev > (result.ai_message_severity or 0)
        ):
            result.ai_message_severity = anomaly_sev
            result.ai_severity_reason = "동적 baseline 이상탐지 (z-score 상향)"

        # Plan 52 E4: LLM 액션가능성 재파싱(추가 호출 없이 기존 응답 재파싱) — 게이트 활성 시에만.
        # enable off면 필드가 기본 None/""로 남아 정책 계층(step 9)이 무시한다(이중 안전·회귀 0).
        if gate is not None and getattr(gate, "enable_llm_actionability", False):
            act = parsed.get("llm_actionability")
            result.llm_actionability = act if act in ("actionable", "noise") else None
            result.actionability_reason = str(parsed.get("actionability_reason") or "")

        logger.info(
            "알람 LLM 분석 완료: alarm_id=%s severity_label=%s pattern_type=%s",
            event.alarm_id,
            result.severity_label,
            result.pattern_type or "-",
        )
        return {"analysis_result": result}
    except Exception as e:
        logger.exception("알람 LLM 분석 실패: alarm_id=%s", event.alarm_id)
        return {"error": str(e)}
