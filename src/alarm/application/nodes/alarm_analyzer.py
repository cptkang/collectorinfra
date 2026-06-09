"""알람 LLM 분석 노드.

Redis Stream에서 소비된 AlarmEvent를 LLM으로 분석하여
AlarmAnalysisResult를 생성한다.

LLM 응답 형식 (JSON):
    {
        "severity_label": "심각" | "경고" | "주의" | "해소",
        "summary": "...",
        "probable_cause": "...",
        "recommended_action": "..."
    }
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.alarm.domain.alarm import AlarmAnalysisResult
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


async def alarm_analyzer_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """알람 이벤트를 LLM으로 분석하여 AlarmAnalysisResult를 반환한다.

    Args:
        state: LangGraph 상태 딕셔너리 (alarm_event 필드 필수)
        config: LangGraph configurable 설정 (app_config 필드 필수)

    Returns:
        analysis_result 또는 error 업데이트
    """
    event = state["alarm_event"]
    cfg = config["configurable"]["app_config"]
    llm = create_llm(cfg)

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
    )
    try:
        response = await llm.ainvoke([
            {"role": "system", "content": ALARM_ANALYZER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        parsed = _extract_json(response.content)
        result = AlarmAnalysisResult(
            alarm_event=event,
            severity_label=parsed["severity_label"],
            summary=parsed["summary"],
            probable_cause=parsed["probable_cause"],
            recommended_action=parsed["recommended_action"],
            notification_channels=cfg.alarm.get_notification_channels(),
        )
        logger.info(
            "알람 LLM 분석 완료: alarm_id=%s severity_label=%s",
            event.alarm_id,
            result.severity_label,
        )
        return {"analysis_result": result}
    except Exception as e:
        logger.exception("알람 LLM 분석 실패: alarm_id=%s", event.alarm_id)
        return {"error": str(e)}
