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
        "pattern_analysis": "..."
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
from src.alarm.prompts.alarm_analyzer import (
    ALARM_ANALYZER_SYSTEM_PROMPT,
    ALARM_ANALYZER_USER_TEMPLATE,
)
from src.llm import create_llm

logger = logging.getLogger(__name__)

_SEVERITY_LABELS = {1: "주의", 2: "경고", 3: "심각"}


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
