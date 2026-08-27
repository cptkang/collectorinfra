"""호스트 조사 인가 정책 (Plan 78 W3-5 · G 계층 · **P11 안전 필수**).

## 왜 별도 인가인가

**조회 권한 ≠ 조사 권한이다.** `allowed_db_ids`(DB 단위)만으로 실호스트 조사를 허용하면,
"DB를 읽을 수 있는 사람 = 서버에 명령을 보낼 수 있는 사람"이 된다. ETCLOVG G 계층이
지목하는 갭 ①이 이것이며, 78 §4.4에서 **최우선**으로 분류됐다.

## 왜 domain 계층인가 (SPEC-composite-orchestration C-4)

판정은 **순수**하다 — `(mode, principal) → 허용/거부 + 사유`. I/O가 없다.
`src/orchestration/`에 두면 소비자인 `src/nodes/`(application)·`noise_gate/application/`이
import할 수 없다(계층 규칙). `domain`은 허용 의존이 `set()`이라 어디서도 쓸 수 있다.

## fail-closed

**미설정·미상 값은 전부 차단**한다. 인가에서 fail-open은 통제가 없는 것과 같다 —
설정 오타 하나로 전 호스트가 열린다. 78 §6.2가 `HOST_AUTHZ_MODE`에 그것을 못 박았다.

## 이벤트 경로의 주체

알람 자동 조사(CW-A)에는 사용자가 없다. `system` 주체를 명시 도입하고 `admin_only`에서
허용한다 — 막으면 CW-A가 무력화되기 때문이다. **다만 감사 필수**다(허용 경로는 전부
`log_investigation`의 `authz` 슬롯에 남는다). 이 조합이 옳은지는 사용자 확인 사항이다
(SPEC Q4). 막아야 한다면 mode 값 추가로 해소된다 — 정책이 여기 한 곳에 모여 있으므로.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: 인가 모드. 미상 값은 차단된다(fail-closed) — 여기 없는 문자열은 전부 거부다.
MODE_ADMIN_ONLY = "admin_only"

#: 주체 역할. `system`은 이벤트 경로(알람 자동 조사)의 주체다.
ROLE_ADMIN = "admin"
ROLE_SYSTEM = "system"

#: 거부 사유 코드 — 감사·응답에 그대로 싣는다(침묵 폴백 금지).
DENY_UNKNOWN_MODE = "unknown_authz_mode"
DENY_NO_PRINCIPAL = "no_principal"
DENY_ROLE_NOT_ALLOWED = "role_not_allowed"
DENY_DB_NOT_ALLOWED = "db_not_allowed"

#: 모드별 허용 역할.
_MODE_ALLOWED_ROLES: dict[str, frozenset[str]] = {
    MODE_ADMIN_ONLY: frozenset({ROLE_ADMIN, ROLE_SYSTEM}),
}


@dataclass(frozen=True)
class Principal:
    """조사를 요청한 주체.

    Attributes:
        role: 역할 문자열. **미상·None은 거부**된다(fail-closed)
        user_id: 사용자 식별자(감사용)
        allowed_db_ids: 조회 인가된 DB 목록. None이면 제한 없음(기존 규약)
        entry_point: "chat" | "event" — G5 대칭 확인의 재료
    """

    role: Optional[str] = None
    user_id: Optional[str] = None
    allowed_db_ids: Optional[list[str]] = None
    entry_point: str = "chat"

    @classmethod
    def system(cls, *, reason: str = "alarm_auto_investigation") -> "Principal":
        """이벤트 경로(알람 자동 조사)의 시스템 주체."""
        return cls(role=ROLE_SYSTEM, user_id=f"system:{reason}", entry_point="event")


@dataclass(frozen=True)
class AuthzDecision:
    """인가 판정 결과. **감사 레코드의 `authz` 슬롯에 그대로 실린다**(78 W6-5).

    문서의 지적: *"관측성 추적은 신원과 권한 상태가 같은 세밀도로 포착될 때만 거버넌스
    증거가 된다."*
    """

    allowed: bool
    mode: str
    principal: Optional[str]
    reason: str = ""
    target: Optional[str] = None

    def as_audit(self) -> dict:
        """감사 레코드에 실을 dict."""
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "principal": self.principal,
            "reason": self.reason,
            "target": self.target,
        }


def authorize_host_investigation(
    *,
    mode: Optional[str],
    principal: Optional[Principal],
    hostname: Optional[str] = None,
    db_id: Optional[str] = None,
) -> AuthzDecision:
    """호스트 조사 인가를 판정한다 (Plan 78 W3-5).

    **판정은 위임·호출 직전(실행 경계)에서 호출해야 한다** — planner·LLM 경로에서 막으면
    우회가 생긴다(UI 게이트 ≠ 인가).

    **채팅·이벤트 두 진입점이 이 함수 하나를 호출한다**(G5) — 한쪽만 적용되는 비대칭 금지.

    Args:
        mode: `HOST_AUTHZ_MODE` 값. **미상·None은 차단**(fail-closed)
        principal: 요청 주체. None이면 차단
        hostname: 조사 대상(감사·사유용)
        db_id: 대상 DB. 주체의 `allowed_db_ids`와 대조한다

    Returns:
        `AuthzDecision` — 거부는 반드시 **사유**를 갖는다
    """
    mode_key = (mode or "").strip().lower()
    allowed_roles = _MODE_ALLOWED_ROLES.get(mode_key)
    if allowed_roles is None:
        # 미설정·오타·모르는 값 → 전부 차단. 인가에서 fail-open은 통제가 없는 것과 같다.
        return AuthzDecision(
            allowed=False, mode=mode_key or "(미설정)",
            principal=getattr(principal, "user_id", None),
            reason=DENY_UNKNOWN_MODE, target=hostname,
        )

    if principal is None or not (principal.role or "").strip():
        return AuthzDecision(
            allowed=False, mode=mode_key, principal=None,
            reason=DENY_NO_PRINCIPAL, target=hostname,
        )

    role = principal.role.strip().lower()
    if role not in allowed_roles:
        return AuthzDecision(
            allowed=False, mode=mode_key, principal=principal.user_id,
            reason=DENY_ROLE_NOT_ALLOWED, target=hostname,
        )

    # 조사 권한이 있어도 **조회 인가 밖의 DB**는 열지 않는다(둘은 곱해진다, 대체하지 않는다).
    if db_id and principal.allowed_db_ids is not None and db_id not in principal.allowed_db_ids:
        return AuthzDecision(
            allowed=False, mode=mode_key, principal=principal.user_id,
            reason=DENY_DB_NOT_ALLOWED, target=hostname,
        )

    return AuthzDecision(
        allowed=True, mode=mode_key, principal=principal.user_id, target=hostname
    )
