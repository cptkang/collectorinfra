"""호스트 가용성 사전 판정 (Plan 81 · D-175).

## 무엇을 푸는가

전원이 꺼졌거나 가용성이 비정상인 서버에 실시간 프로세스·OS 조사를 요청하면, 지금은
사용자가 **원인을 알 수 없는 두 응답** 중 하나를 받는다(`plans/81` §1.1 실측):

    ① 프로세스 API 미응답 → "잠시 후 다시 시도해 주세요"  ← 재시도해도 같다(오안내)
    ② API 200 + 빈 목록   → "현재 실행 중 프로세스 0건"    ← 정상 결과로 서술된다(거짓 단정)

여기서는 **조회·조사 진입 전에** 폴스타 자원 정보(`cmm_resource`)로 대상 가용성을 결정적으로
판정한다. 판정은 순수 함수다 — LLM을 쓰지 않는다(D-035).

## 왜 domain 계층인가 (D-171 선례)

소비자 넷 중 둘이 `application`이다(`src/nodes/fault_diagnosis.py` ·
`noise_gate/application/nodes/investigation_trigger.py`). `orchestration`에 두면 그쪽이
import할 수 없어 대칭 배선 자체가 불가능하다. `domain`은 허용 의존이 `set()`이라 어디서도 쓸 수 있다.

## fail-open — 인가(host_authz)와 정반대다

`host_authz`는 미상 값을 전부 차단한다(fail-closed). **여기는 반대다.**
이 판정은 보안 통제가 아니라 *"성공할 수 없는 호출을 생략하는"* 낭비 방지 게이트이므로,
근거가 약할 때 차단하면 **정상 조회를 잃는다.** 따라서:

    - 차단하는 것은 `avail_status == 1`(DOWN) **하나뿐**이다.
    - 값이 2(알 수 없음)·미상 코드·조회 실패·미등록이면 `unknown`으로 두고 **진행시킨다**.

## 단정하지 않는 것

`avail_status`는 **Power off와 에이전트 통신 이슈를 구분하지 못한다**(`src/nodes/realtime_usage.py:73-74`
2026-07-24 실측). 그러므로 사용자 문구는 "전원이 꺼져 있습니다"라고 **단정하지 않는다** —
말할 수 있는 것은 "가용성이 비정상(중지/통신이상)이며 확인 시각은 …"까지다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── 판정 상태 ────────────────────────────────────────────────
STATE_AVAILABLE = "available"
STATE_UNAVAILABLE = "unavailable"
STATE_MAINTENANCE = "maintenance"
STATE_UNKNOWN = "unknown"

# ── 사유 코드 — 감사·응답에 그대로 싣는다(침묵 폴백 금지) ────
REASON_OK = "ok"
REASON_DOWN = "avail_status_down"
REASON_MAINTENANCE = "maintenance"
REASON_STATUS_UNKNOWN = "avail_status_unknown"      # 폴스타가 '알 수 없음'(2)으로 등록
REASON_STATUS_UNRECOGNIZED = "avail_status_unrecognized"  # 규약에 없는 코드
REASON_NOT_REGISTERED = "not_registered"            # cmm_resource에 대상 행 없음
REASON_LOOKUP_FAILED = "lookup_failed"              # 조회 자체 실패(DB 미연결 등)

#: 폴스타 `cmm_resource.avail_status` 값 규약 (`src/prompts/output_generator.py:30`).
AVAIL_UP = 0
AVAIL_DOWN = 1
AVAIL_UNKNOWN = 2


@dataclass(frozen=True)
class HostAvailability:
    """대상 호스트 가용성 판정 결과.

    `state`만으로 동작을 가르지 않는다 — `reason`·`evidence`는 사용자 문구와 감사 양쪽이
    소비하며, 근거 없는 판정을 만들지 않기 위한 계약이다(D-035).

    Attributes:
        state: available | unavailable | maintenance | unknown
        reason: 기계 판독 사유 코드(위 REASON_* 상수)
        evidence: 판정 근거 원값(`avail_status`·`is_maintenance` 등)
        as_of: 근거 데이터를 조회한 시각(문자열). 호출부가 채운다 — `cmm_resource.mtime`이
            아니다. mtime은 리소스 수정 시각이라 "가용성을 언제 확인했는지"를 말하지 못한다
    """

    state: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    as_of: Optional[str] = None

    @property
    def blocks_collection(self) -> bool:
        """수집·조사를 생략해야 하는 판정인지.

        **`unavailable`만 True다.** `unknown`을 막으면 판정 근거가 약할 때 정상 조회가
        차단된다 — 이 게이트의 유일한 회귀 위험이 그것이다(모듈 docstring "fail-open").
        """
        return self.state == STATE_UNAVAILABLE

    @property
    def is_notable(self) -> bool:
        """사용자에게 알릴 만한 판정인지(정상이면 문구를 붙이지 않는다)."""
        return self.state != STATE_AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        """상태 저장·감사용 직렬화 형태(LangGraph 체크포인터가 나를 수 있어야 한다)."""
        return {
            "state": self.state,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "as_of": self.as_of,
        }


def _coerce_int(value: Any) -> Optional[int]:
    """DB 드라이버 편차(문자열·Decimal·bool)를 흡수해 정수로 만든다(불가하면 None)."""
    if value is None or isinstance(value, bool):
        return None if value is None else int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def judge_availability(
    *,
    found: bool = True,
    avail_status: Any = None,
    is_maintenance: Any = None,
    as_of: Optional[str] = None,
    lookup_failed: bool = False,
) -> HostAvailability:
    """폴스타 자원 정보로 대상 호스트의 가용성을 결정적으로 판정한다.

    Args:
        found: `cmm_resource`에서 대상 행을 찾았는지
        avail_status: `cmm_resource.avail_status` 원값(0=정상 · 1=비정상/DOWN · 2=알 수 없음)
        is_maintenance: `cmm_resource.is_maintenance` 원값(0/1)
        as_of: 근거 데이터를 조회한 시각 문자열(호출부가 채운다)
        lookup_failed: 조회 자체가 실패했는지(DB 미연결·예외). True면 판정하지 않는다

    Returns:
        `HostAvailability`. **차단(`blocks_collection`)은 `avail_status == 1`일 때뿐**이다.
    """
    evidence: dict[str, Any] = {
        "avail_status": avail_status,
        "is_maintenance": is_maintenance,
    }

    if lookup_failed:
        # 조회 실패는 "가용하지 않다"가 아니다 — 종전 경로로 진행시킨다(fail-open).
        return HostAvailability(STATE_UNKNOWN, REASON_LOOKUP_FAILED, evidence, as_of)
    if not found:
        return HostAvailability(STATE_UNKNOWN, REASON_NOT_REGISTERED, evidence, as_of)

    status = _coerce_int(avail_status)
    maintenance = bool(_coerce_int(is_maintenance))
    evidence["maintenance"] = maintenance

    if status is None:
        return HostAvailability(STATE_UNKNOWN, REASON_STATUS_UNKNOWN, evidence, as_of)
    if status == AVAIL_DOWN:
        # 점검 중이면서 DOWN일 수 있다 — 차단 판정은 같고 문구만 점검 사실을 덧붙인다.
        return HostAvailability(STATE_UNAVAILABLE, REASON_DOWN, evidence, as_of)
    if status == AVAIL_UP:
        if maintenance:
            return HostAvailability(STATE_MAINTENANCE, REASON_MAINTENANCE, evidence, as_of)
        return HostAvailability(STATE_AVAILABLE, REASON_OK, evidence, as_of)
    if status == AVAIL_UNKNOWN:
        return HostAvailability(STATE_UNKNOWN, REASON_STATUS_UNKNOWN, evidence, as_of)
    # 규약에 없는 코드 — 막지 않는다. 막으면 인스턴스마다 다른 코드 하나가 조회를 통째로 끊는다.
    return HostAvailability(STATE_UNKNOWN, REASON_STATUS_UNRECOGNIZED, evidence, as_of)


def describe(availability: HostAvailability, server_label: str) -> str:
    """판정을 사용자 문구로 만든다(정상이면 빈 문자열).

    문구 규약: **"전원이 꺼져 있습니다"라고 단정하지 않는다.** `avail_status`는 Power off와
    에이전트 통신 이슈를 구분하지 못한다(모듈 docstring). 또한 `unavailable`에는
    "잠시 후 다시 시도" 류의 재시도 유도를 **넣지 않는다** — 재시도해도 결과가 같다.

    Args:
        availability: 판정 결과
        server_label: 사용자에게 보일 서버 표기(서버명 또는 `서버명(호스트명 …)`)

    Returns:
        사용자 문구. `available`이면 빈 문자열
    """
    at = f" (확인 시각 {availability.as_of})" if availability.as_of else ""
    maintenance_note = (
        " 해당 서버는 점검(maintenance) 상태로도 등록돼 있습니다."
        if availability.evidence.get("maintenance")
        else ""
    )

    if availability.state == STATE_UNAVAILABLE:
        return (
            f"'{server_label}' 서버는 폴스타 가용성이 비정상(중지/통신이상) 상태입니다{at}. "
            "서버가 내려가 있거나 모니터링 에이전트와 통신이 끊긴 상태로, "
            f"실시간 조회가 불가능합니다.{maintenance_note}"
        )
    if availability.state == STATE_MAINTENANCE:
        return (
            f"'{server_label}' 서버는 점검(maintenance) 상태로 등록돼 있습니다{at}. "
            "조회 결과가 평소와 다를 수 있습니다."
        )
    if availability.reason == REASON_NOT_REGISTERED:
        return (
            f"'{server_label}' 서버를 폴스타 자원 목록에서 찾지 못했습니다 — "
            "서버명을 확인해 주세요."
        )
    if availability.reason == REASON_STATUS_UNKNOWN:
        return (
            f"'{server_label}' 서버의 가용성이 '알 수 없음'으로 등록돼 있습니다{at} — "
            "조회 결과가 비어 있을 수 있습니다."
        )
    if availability.reason == REASON_STATUS_UNRECOGNIZED:
        raw = availability.evidence.get("avail_status")
        return (
            f"'{server_label}' 서버의 가용성 코드({raw})가 알려진 값이 아닙니다 — "
            "조회는 진행하되 결과 해석에 주의가 필요합니다."
        )
    return ""


__all__ = [
    "HostAvailability",
    "judge_availability",
    "describe",
    "STATE_AVAILABLE",
    "STATE_UNAVAILABLE",
    "STATE_MAINTENANCE",
    "STATE_UNKNOWN",
    "REASON_OK",
    "REASON_DOWN",
    "REASON_MAINTENANCE",
    "REASON_STATUS_UNKNOWN",
    "REASON_STATUS_UNRECOGNIZED",
    "REASON_NOT_REGISTERED",
    "REASON_LOOKUP_FAILED",
]
