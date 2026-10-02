"""운영자 침묵(Silence) 규칙 — 매칭 순수 함수 (Plan 54 모듈 4).

운영자가 "이 패턴의 알람을 언제까지 조용히 시켜라"고 건 규칙을 정의하고, 알람 이벤트가 그
규칙에 걸리는지 결정적으로 판정한다. 이 모듈은 **판정만** 하고 저장·조회는 infrastructure가 맡는다.

설계 원칙:
    - **억제 ≠ 삭제**: 침묵으로 걸러진 알람도 감사에 기록된다(판정 결과가 SUPPRESS일 뿐).
    - **심각도 상한**: 규칙은 자기 `max_severity` 이하의 알람만 침묵시킨다. 게이트의
      심각도3 단락이 침묵 단계보다 앞이므로 최고 심각도는 어떤 규칙으로도 조용해지지 않는다.
    - **전체 침묵 금지**: 매처 전 필드가 비어 있으면 매칭하지 않는다 — 실수 한 번으로 모든
      알람이 사라지는 경로를 만들지 않는다.
    - **글롭 1종만**: 정규식을 받지 않는다(오작성으로 의도보다 넓게 걸리는 사고 방지).

표준 라이브러리(fnmatch/datetime/dataclasses)만 사용한다. event는 덕 타이핑으로 소비한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Optional

# 매처를 구성하는 필드 — 순서가 곧 화면 표기 순서다.
MATCHER_FIELDS: tuple[str, ...] = ("db_id", "server_name", "alarm_name", "resource_name")


@dataclass(frozen=True)
class SilenceRule:
    """운영자가 건 침묵 규칙 1건.

    매처 필드는 대소문자를 구분하는 글롭(`WEB-*`)이며, **빈 문자열은 "무조건 일치"** 다.
    전 매처 필드가 빈 규칙은 전체 침묵이므로 매칭 단계에서 거부된다(`matches` 참조).
    """

    id: str
    db_id: str
    server_name: str
    alarm_name: str
    resource_name: str
    max_severity: int
    reason: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """JSON 직렬화용 dict로 바꾼다(시각은 ISO 8601)."""
        return {
            "id": self.id,
            "db_id": self.db_id,
            "server_name": self.server_name,
            "alarm_name": self.alarm_name,
            "resource_name": self.resource_name,
            "max_severity": self.max_severity,
            "reason": self.reason,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }

    @property
    def matcher_summary(self) -> str:
        """화면·감사 로그용 매처 요약(`server_name=WEB-* · sev<=2`)."""
        parts = [
            f"{field}={getattr(self, field)}"
            for field in MATCHER_FIELDS
            if getattr(self, field)
        ]
        parts.append(f"sev<={self.max_severity}")
        return " · ".join(parts)


def is_blank_matcher(rule: SilenceRule) -> bool:
    """매처 전 필드가 비어 있는지(=전체 침묵인지) 판정한다."""
    return not any(str(getattr(rule, field, "") or "").strip() for field in MATCHER_FIELDS)


def is_active(rule: SilenceRule, now: datetime) -> bool:
    """규칙이 지금 유효한지 판정한다(해제되지 않았고 만료 전).

    Args:
        rule: 침묵 규칙.
        now: 기준 시각(tz-aware 권장 — naive면 UTC로 간주).

    Returns:
        유효하면 True.
    """
    moment = _as_utc(now)
    if rule.revoked_at is not None and _as_utc(rule.revoked_at) <= moment:
        return False
    return _as_utc(rule.expires_at) > moment


def matches(rule: SilenceRule, event, *, effective_severity: int) -> bool:
    """이벤트가 규칙의 매처·심각도 상한에 걸리는지 판정한다.

    매처 필드가 빈 문자열이면 그 축은 조건이 없는 것으로 본다. 전 필드가 비면 전체 침묵이므로
    **무조건 False**다(안전 가드 — 저장소에 억지로 넣어도 여기서 막힌다).

    Args:
        rule: 침묵 규칙.
        event: 알람 이벤트(db_id/server_name/hostname/alarm_name/resource_name 속성).
        effective_severity: 실효 심각도(AI 보강 반영 후 값).

    Returns:
        걸리면 True.
    """
    if is_blank_matcher(rule):
        return False
    if effective_severity > rule.max_severity:
        return False

    server = str(getattr(event, "server_name", "") or "") or str(
        getattr(event, "hostname", "") or ""
    )
    subject = {
        "db_id": str(getattr(event, "db_id", "") or ""),
        "server_name": server,
        "alarm_name": str(getattr(event, "alarm_name", "") or ""),
        "resource_name": str(getattr(event, "resource_name", "") or ""),
    }
    for field in MATCHER_FIELDS:
        pattern = str(getattr(rule, field, "") or "").strip()
        if not pattern:
            continue
        if not fnmatchcase(subject[field], pattern):
            return False
    return True


def match_rules(
    rules, event, *, effective_severity: int, now: datetime
) -> Optional[SilenceRule]:
    """활성 규칙 중 이벤트에 처음 걸리는 것을 돌려준다(없으면 None).

    여러 규칙이 걸리면 **먼저 등록된 것**이 이긴다 — 감사에서 "어느 규칙이 이 알람을 조용히
    시켰나"가 흔들리지 않게 순서를 고정한다.

    Args:
        rules: SilenceRule 목록(등록 순).
        event: 알람 이벤트.
        effective_severity: 실효 심각도.
        now: 기준 시각.

    Returns:
        매칭된 규칙 또는 None.
    """
    for rule in rules or []:
        if not is_active(rule, now):
            continue
        if matches(rule, event, effective_severity=effective_severity):
            return rule
    return None


def _as_utc(value: datetime) -> datetime:
    """naive datetime을 UTC로 간주해 tz-aware로 맞춘다(비교 시 예외 방지)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
