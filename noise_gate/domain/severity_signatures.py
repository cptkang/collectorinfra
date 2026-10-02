"""OS 장애 시그니처 → 심각도 스캐너 (Plan 52 Phase E3 · domain 순수함수).

Plan 51 부록 A.1 「OS 장애 시그니처 치트시트」(verbatim 로그 패턴)를 결정적으로 스캔하여,
로그 메시지가 드러내는 장애의 상향 심각도(escalate-only)를 산출한다.

설계 원칙:
    - 상향 전용: 여기서 산출한 값은 폴스타 구조화 severity를 **올리는 데만** 쓰인다(R-10).
      이 모듈은 방향을 강제하지 않고 매칭 심각도만 반환하며, max() 결합은 호출측이 수행한다.
    - 결정적·환각 없음: 사전 정의된 verbatim 핵심 토큰의 부분일치(대소문자 무시)만 인정한다.
      LLM 호출이 없으며, benign/일상 로그는 None을 반환한다.
    - 여러 시그니처가 동시에 매칭되면 **최고 심각도**를 반환한다(보수적·재현율 우선).

이 모듈은 domain 계층에 위치하므로 표준 라이브러리만 의존한다(src 내 다른 모듈 import 금지).
"""

from __future__ import annotations

# 각 규칙: (상향심각도, 시그니처 라벨, 매칭 토큰들)
# 토큰은 비교 전에 소문자화된 텍스트와 대조하므로 모두 소문자로 보관한다.
# 핵심 토큰은 Plan 51 §A.1 치트시트 원문에서 발췌한 verbatim 부분 문자열이다.
_SIGNATURE_RULES: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    # ── → 3 (심각): 커널/메모리/파일시스템·블록·프로세스 치명 장애 ──
    (3, "OOM 강제종료", ("out of memory", "oom-kill", "invoked oom-killer")),
    (3, "소프트 락업", ("soft lockup",)),
    (3, "Hung task", ("hung_task", "blocked for more than")),
    (3, "RCU stall", ("rcu_sched detected stalls", "rcu stall")),
    (3, "커널 패닉", ("kernel panic",)),
    (
        3,
        "FS/블록 오류",
        (
            "ext4-fs error",
            "blk_update_request: i/o error",
            "buffer i/o error",
            "remounting filesystem read-only",
            "corruption detected",
            "shutting down filesystem",
        ),
    ),
    (3, "Segfault", ("segfault at",)),
    (3, "GP fault", ("general protection",)),
    # ── → 2 (경고): 네트워크·자원 고갈·서비스 플래핑·인증 브루트포스 ──
    (2, "conntrack 고갈", ("nf_conntrack: table full",)),
    (2, "SYN 플러드", ("possible syn flooding",)),
    (2, "포트 고갈", ("cannot assign requested address",)),
    (2, "MTU 블랙홀", ("message too long, mtu=",)),
    (2, "FD 고갈", ("too many open files",)),
    (
        2,
        "스레드/PID 고갈",
        ("fork: retry: resource temporarily unavailable", "pthread_create"),
    ),
    (2, "systemd 플래핑", ("start request repeated too quickly", "start-limit-hit")),
    (2, "인증 브루트포스", ("failed password for", "authentication failure")),
    (2, "NFS Stale", ("stale file handle",)),
)


def scan_signature_severity(text: str) -> tuple[int, str] | None:
    """로그 텍스트를 스캔하여 (상향심각도, 시그니처 라벨) 또는 None을 반환한다.

    대소문자를 무시하고 사전 정의 핵심 토큰의 부분일치를 검사한다. 여러 규칙이 매칭되면
    **최고 심각도**의 규칙을 반환하며(동일 심각도 내에서는 정의 순서가 앞선 규칙),
    어떤 시그니처도 매칭되지 않으면(benign/일상 로그) None을 반환한다.

    Args:
        text: 조사할 로그 메시지(예: AlarmEvent.condition_log). 빈 값이면 None.

    Returns:
        매칭된 (심각도, 라벨) 튜플, 또는 매칭 없음 시 None.
    """
    if not text:
        return None
    haystack = text.lower()
    best: tuple[int, str] | None = None
    for severity, label, tokens in _SIGNATURE_RULES:
        if any(token in haystack for token in tokens):
            if best is None or severity > best[0]:
                best = (severity, label)
    return best
