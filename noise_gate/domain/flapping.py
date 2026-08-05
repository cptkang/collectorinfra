"""플래핑 탐지 (Plan 52 Phase E2 — Nagios 알고리즘 순수함수).

알람이 짧은 시간에 발생↔해소를 반복하는 '플래핑(flapping)' 상태를 Nagios의
가중 %-state-change + 히스테리시스 방식으로 판정한다(§3.7, 출처 §15 — Nagios
flapping, 기본 임계 low 5.0 / high 20.0).

설계 원칙:
    - 순수함수(부작용·전역상태·I/O 없음)·결정적. 동일 입력 → 동일 출력.
    - 최신 전이일수록 큰 가중(1.0→1.5 선형 보간)을 부여해 최근 불안정성을 강조.
    - 부동소수 오차를 줄이기 위해 가중합을 산출한 뒤 마지막에 비율을 계산한다.

이 모듈은 domain 계층에 위치하므로 표준 라이브러리만 의존한다(src 내 다른 모듈 import 금지).
"""

from __future__ import annotations

# Nagios 기본 플래핑 임계(샘플 nagios.cfg) — §8.5 / §15
DEFAULT_FLAP_HIGH = 20.0
DEFAULT_FLAP_LOW = 5.0

# 최근 상태 시퀀스 최대 길이(Nagios: 21개 결과 → 최대 20개 전이)
MAX_STATES = 21


def flap_percent(states: list[bool]) -> float:
    """최근 상태 시퀀스(오래된→최신 순)로 가중 %-state-change를 산출한다.

    Args:
        states: 각 체크의 firing 여부 시퀀스(True=발생, False=해소). 오래된→최신 순.
            최대 21개만 사용하며, 초과 시 뒤(최신)에서 21개로 잘라 방어적으로 처리한다.

    Returns:
        가중 %-state-change(0.0~100.0). 상태가 2개 미만이거나 전이가 없으면 0.0.

    알고리즘(§3.7):
        - 연속한 두 상태가 다르면(전이) 1, 같으면 0. N개 상태 → 최대 N-1개 전이(≤20).
        - 각 전이에 최신 가중치를 부여: 가장 오래된 전이=1.0 → 가장 최신 전이=1.5(선형 보간).
          전이가 k개면 i번째(0=가장 오래된)의 가중치 = 1.0 + 0.5·(i/(k-1))(k>1), k==1이면 1.5.
        - %-state-change = (Σ weightᵢ·changedᵢ) / (Σ weightᵢ) × 100.
    """
    # 방어적 절단: 최신 21개만 사용(호출부가 관리하나 안전망)
    window = states[-MAX_STATES:]

    transition_count = len(window) - 1
    if transition_count < 1:
        # 상태가 2개 미만 → 전이 없음
        return 0.0

    weighted_changed = 0.0
    weight_total = 0.0
    for i in range(transition_count):
        if transition_count == 1:
            weight = 1.5
        else:
            weight = 1.0 + 0.5 * (i / (transition_count - 1))
        changed = 1.0 if window[i] != window[i + 1] else 0.0
        weighted_changed += weight * changed
        weight_total += weight

    if weight_total == 0.0:
        return 0.0
    return weighted_changed / weight_total * 100.0


def update_flap_state(
    prev_flapping: bool,
    percent: float,
    high: float = DEFAULT_FLAP_HIGH,
    low: float = DEFAULT_FLAP_LOW,
) -> bool:
    """히스테리시스로 플래핑 상태를 갱신한다 (Nagios).

    Args:
        prev_flapping: 직전 플래핑 상태.
        percent: `flap_percent`로 산출한 가중 %-state-change.
        high: 플래핑 시작 임계(기본 20.0). percent >= high면 시작.
        low: 플래핑 종료 임계(기본 5.0). percent <= low면 종료.

    Returns:
        갱신된 플래핑 상태.

    히스테리시스(§3.7):
        - 비플래핑(prev=False)에서 percent >= high → True(시작).
        - 플래핑(prev=True)에서 percent <= low → False(종료).
        - 그 사이(low < percent < high)는 prev 유지.
    """
    if not prev_flapping:
        if percent >= high:
            return True
        return False
    # prev_flapping is True
    if percent <= low:
        return False
    return True
