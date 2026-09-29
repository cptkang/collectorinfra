"""게이트웨이 오류 어휘 (SPEC-apm-gateway §3.2) — 도구 반환 `{"error": code}`의 정본.

제니퍼는 도메인 미접속과 필수 파라미터 누락을 모두 HTTP 500으로 돌려준다(plans/87 §0.10 #11). 그래서
코드는 HTTP 상태가 아니라 **본문**으로 가른 결과다(분류는 어댑터 몫).
"""

from __future__ import annotations

NOT_CONFIGURED = "not_configured"
INVALID_ARGUMENT = "invalid_argument"
INSTANCE_UNRESOLVED = "instance_unresolved"
PROFILE_REF_MISMATCH = "profile_ref_mismatch"
SOURCE_UNAVAILABLE = "source_unavailable"
CONTRACT_VIOLATION = "contract_violation"
QUOTA_EXCEEDED = "apm_quota_exceeded"
API_ERROR = "apm_api_error"
RATE_LIMITED = "rate_limited"

ERROR_CODES: frozenset[str] = frozenset(
    {
        NOT_CONFIGURED,
        INVALID_ARGUMENT,
        INSTANCE_UNRESOLVED,
        PROFILE_REF_MISMATCH,
        SOURCE_UNAVAILABLE,
        CONTRACT_VIOLATION,
        QUOTA_EXCEEDED,
        API_ERROR,
        RATE_LIMITED,
    }
)


class ApmError(Exception):
    """코드가 붙은 게이트웨이 오류. 도구 계층이 `{"error": code, "reason": ...}`로 바꾼다."""

    def __init__(self, code: str, reason: str, *, status: int | None = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"미지 오류 코드: {code}")
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.status = status
