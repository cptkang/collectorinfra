"""조사 서비스 거부 사유 코드 → 사용자 문구 (plans/194 W1 · D-347).

「이 알람 분석」·추가 조사 접수가 조사 서비스(sre_agent)의 `status:"rejected"`를 받으면 응답의
`reason`(코드)과 함께 `reason_text`(한국어 한 문장)를 싣는다. 채팅 장애 진단·조사 접수
(`src/nodes/fault_diagnosis.py`)도 같은 문구로 거부 사유를 보인다. 코드는 조사 서비스의 동기 가드·
대기열 가드가 내는 값이며, 표에 없는 코드는 코드를 괄호에 담은 일반 문구로 돌려준다(침묵 금지).
조사 서비스는 거부마다 `verdict`도 싣는데, 가용성 거부만 확인 시각·판정 같은 사실을 담고 나머지는
「조사 거부 — <코드>」(코드 원문)라서 표시 문구는 `reject_display_text`로 고른다.

소비자가 interface(api 라우트)와 application(nodes) 양쪽이라 stdlib 전용 domain에 둔다.
"""

from __future__ import annotations

#: 대상 가용성 거부 코드(Plan 81 G-2 — sre_agent `GUARD_TARGET_UNAVAILABLE`과 같은 값). 이 거부의
#: verdict만 사실(확인 시각·판정)을 담으므로 표 문구보다 verdict를 먼저 보인다.
TARGET_UNAVAILABLE_REASON = "target_unavailable"

#: 거부 사유 코드 → 사용자 문구. 코드 값은 sre_agent 조사 디스패처의 가드 반환값과 같다.
REJECT_REASON_TEXTS: dict[str, str] = {
    "host_investigation_in_flight": (
        "같은 서버를 조사 중입니다 — 진행 중인 조사가 끝난 뒤 다시 요청하세요."
    ),
    "hourly_budget_exceeded": "조사 서비스의 시간당 조사 상한에 도달했습니다.",
    "dedup_ttl_active": "같은 알람을 최근에 조사했습니다 — 잠시 뒤 다시 요청하세요.",
    "target_unavailable": "조사 대상 서버가 가용하지 않아 조사하지 않았습니다.",
    "queue_full": "조사 대기열이 가득 찼습니다(동시 조사 상한) — 잠시 뒤 다시 요청하세요.",
    "service_overloaded": (
        "조사 서비스가 바쁩니다(끝나지 않은 조사 정리 중) — 잠시 뒤 다시 요청하세요."
    ),
}


def reject_reason_text(code: str | None) -> str:
    """거부 사유 코드를 사용자 문구 한 문장으로 바꾼다(모르는 코드는 코드를 괄호에 담는다)."""
    key = str(code or "").strip()
    text = REJECT_REASON_TEXTS.get(key)
    if text is not None:
        return text
    return f"조사 서비스가 요청을 거절했습니다({key or '사유 미상'})."


def reject_display_text(code: str | None, verdict: str | None) -> str:
    """거부 응답의 사용자 표시 문구 한 문장을 고른다(plans/194 W1 · D-347).

    가용성 거부이고 verdict가 있으면 verdict(사실 브리핑), 코드가 표에 있으면 표 문구, 그 밖에는
    verdict, verdict도 없으면 `reject_reason_text`의 기본 문구(코드를 괄호에 담는다).
    """
    key = str(code or "").strip()
    said = str(verdict or "").strip()
    if key == TARGET_UNAVAILABLE_REASON and said:
        return said
    text = REJECT_REASON_TEXTS.get(key)
    if text is not None:
        return text
    return said or reject_reason_text(key)
