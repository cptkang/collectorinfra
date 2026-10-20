"""잡 워치독 임계 — JobStore 워치독과 dispatcher 착수 전 판정이 함께 쓰는 단일 기준.

**순수 도메인 모듈**(stdlib만). JobStore(`_sweep`)는 `updated_at`이 이 임계를 넘긴 active 잡을
`failed(stuck_watchdog)`로 확정하고, dispatcher는 동시 상한 슬롯을 얻은 직후 같은 임계로 이미 확정됐거나
넘긴 잡을 조사하지 않는다(받을 쪽이 없는 조사로 슬롯을 쓰지 않게). 두 곳의 식이 갈라지면 워치독이 끝낸
잡을 dispatcher가 다시 돌리는 틈이 생기므로 한 곳에 둔다(application 간 직접 의존 회피).
"""

from __future__ import annotations

#: 워치독 임계 하한(초) — 짧은 타임아웃 설정에서도 정상 조사를 오판하지 않게.
STUCK_AFTER_FLOOR_SECONDS = 600.0

#: 워치독 확정 사유 — noise_gate·화면이 이 어휘로 읽는다(D-123 상태 어휘 불변 · 사유만).
STUCK_WATCHDOG_REASON = "stuck_watchdog"


def stuck_after_seconds(timeout_seconds: float) -> float:
    """active 잡을 낑김으로 볼 임계 — 조사 전체 타임아웃의 2배(하한 600초)."""
    return max(STUCK_AFTER_FLOOR_SECONDS, 2.0 * float(timeout_seconds))


__all__ = ["STUCK_AFTER_FLOOR_SECONDS", "STUCK_WATCHDOG_REASON", "stuck_after_seconds"]
