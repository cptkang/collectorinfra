"""변경/구성 이벤트 상관 (Plan 60 E5 · D-081 초안 — 결정적 순수함수).

알람 발생 시점 **직전 창**의 변경 이벤트를 타임라인에 오버레이하고, 알람 영향범위
(리소스 ID)와 매칭해 원인 후보(`ChangeCandidate`)를 산출한다. "장애의 최대 원인은
변경"이라는 관측(Davis/Watchdog faulty-deployment detection)에 근거해, 변경 근접
알람을 **억제가 아니라 승격**(원인성 판단·PAGE 근거 보강)하기 위한 신호를 만든다(§7.2).

정적/동적 분리(topology.py와 동일 원리):
    - 이 모듈은 상태를 보관하지 않는 **순수함수**다. 변경 이벤트(`changes`)와 알람
      시각창(`incident_window`)·영향 리소스(`affected_resource_ids`)를 *인자로* 받는다.
    - 변경 이벤트의 실제 DB 조회(읽기전용)는 인프라 계층 `change_feed.py`가 담당한다.

이 모듈은 domain 계층에 위치하므로 **표준 라이브러리만** 의존한다(infra·config import 금지).
변경 이벤트(`changes`)는 **덕 타이핑**으로 소비한다 — `resource_id`/`change_type`/
`description`/`event_time` 속성을 가진 객체(예: `change_feed.ChangeEvent`)면 되고, 이
모듈은 그 타입을 import하지 않는다(notification_policy가 event를 덕 타이핑하는 패턴과 동일).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ChangeCandidate:
    """알람과 상관된 변경 이벤트 후보(감사·원인성 표기용 불변 스냅샷).

    Attributes:
        resource_id: 변경된 리소스 ID(문자열 정규화).
        change_type: 변경 유형(폴스타 lifecycle_type — 예: 배포/구성변경/재기동).
        description: 변경 설명.
        event_time: 변경 발생 시각(epoch bigint — 단위는 폴스타 timestamp 관례, change_feed 주석 참조).
        proximity_seconds: 알람 발생 시점과의 근접도(= incident_time − event_time, 작을수록 근접).
            음수면 변경이 알람 이후에 발생한 것으로 해석하지 않는다(오버레이가 창으로 배제).
    """

    resource_id: str
    change_type: str
    description: str
    event_time: int
    proximity_seconds: int


def _coerce_int(value) -> Optional[int]:  # noqa: ANN001
    """event_time류 값을 int로 강제한다(변환 불가·None → None, 해당 변경은 오버레이에서 제외)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def overlay_changes(
    incident_window: tuple[int, int],
    changes: Iterable,
    *,
    affected_resource_ids: Optional[set[str]] = None,
) -> list[ChangeCandidate]:
    """알람 직전 창의 변경을 타임라인 오버레이 + 영향범위 매칭해 후보를 산출한다(순수·결정적).

    Args:
        incident_window: `(start_epoch, end_epoch)` — end_epoch는 알람 발생 시각(포함),
            start_epoch은 창 시작(포함). `start <= event_time <= end`인 변경만 후보다
            (알람 **직전** 창의 변경만 — 알람 이후 변경은 원인 후보에서 배제).
        changes: 변경 이벤트 반복자(덕 타이핑 — resource_id/change_type/description/
            event_time 속성 보유). event_time 파싱 불가 항목은 조용히 건너뛴다.
        affected_resource_ids: 알람의 영향 리소스 ID 집합. **비어있지 않으면** 그 집합에
            속한 변경만 후보로 남긴다(영향범위 매칭). None/빈 집합이면 리소스 필터 없이
            창 내 전 변경을 오버레이한다(리소스 미해소 시 graceful — 시간창만 적용).

    Returns:
        `list[ChangeCandidate]` — event_time 내림차순(최신 변경 우선), 동시각은
        (resource_id, change_type) 오름차순으로 결정적 정렬. 매칭 없으면 빈 리스트.
    """
    start_epoch, end_epoch = int(incident_window[0]), int(incident_window[1])
    scope = {str(r) for r in affected_resource_ids} if affected_resource_ids else None

    candidates: list[ChangeCandidate] = []
    for change in changes or ():
        event_time = _coerce_int(getattr(change, "event_time", None))
        if event_time is None:
            continue
        # 타임라인 오버레이: 알람 직전 창(start~end, 포함) 안의 변경만.
        if event_time < start_epoch or event_time > end_epoch:
            continue
        resource_id = str(getattr(change, "resource_id", "") or "")
        # 영향범위 매칭: scope가 지정됐으면 그 리소스의 변경만.
        if scope is not None and resource_id not in scope:
            continue
        candidates.append(
            ChangeCandidate(
                resource_id=resource_id,
                change_type=str(getattr(change, "change_type", "") or ""),
                description=str(getattr(change, "description", "") or ""),
                event_time=event_time,
                proximity_seconds=end_epoch - event_time,
            )
        )

    # 결정적 정렬: event_time 내림차순(최신 변경 우선), 동시각은 resource_id·change_type 오름차순.
    candidates.sort(key=lambda c: (-c.event_time, c.resource_id, c.change_type))
    return candidates
