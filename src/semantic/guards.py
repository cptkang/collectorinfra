"""교정 가드·게이트 발동 계측 (Plan 67 R4).

``src/nodes/semantic_compiler.py``에서 분리했다(Plan 69 P5-1) — 상태·설정·LLM에 결합하지
않는 조각이라 nodes 밖에 두어 ``src.tools``가 nodes를 거치지 않고 참조하게 한다(순환 해소).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 이름 → 누적 발동 횟수. LLM 비결정 교정 가드와 표면어 폴백이 실제로 얼마나 발동하는지
#: 계측해 stepwise ON/OFF 발동률을 비교하는 재료다(계획서 §3.2-R4). **계측만** 하고 가드는
#: 삭제하지 않는다 — 발동 0이 실증된 것부터 단계 축소한다.
_GUARD_COUNTERS: dict[str, int] = {}

GUARD_PHYSICALCORE_DROP = "normalize.physicalcore_drop"       # 동시 선택 시 PHYSICALCORE 제거
GUARD_PHYSICALCORE_SWAP = "normalize.physicalcore_swap"       # 단독 선택 시 LOGICALCORE 치환
GUARD_CAPACITY_INJECT = "normalize.capacity_inject"           # 명시 용량 차원 누락 보정
GUARD_TIME_FILTER_PROMOTE = "normalize.time_filter_promote"   # 기간 표현 필터 → time_range 승격
GUARD_TIME_RANGE_OVERRIDE = "normalize.time_range_override"   # LLM 기간값을 결정적 해석으로 교정
GUARD_BREAKDOWN_PROMOTE = "normalize.breakdown_promote"       # 월별 표면어 → time_breakdown 승격
GUARD_MONTHLY_GATE = "gate.monthly_breakdown_fallback"        # 월별 폴백 게이트(승격 불가 시)
GUARD_RANKING_SURFACE = "ranking.surface_fallback"            # 표면어 기반 정렬 결정(IR 부재 폴백)
GUARD_SCOPE_FILTER_STRIP = "scope.identity_filter_strip"      # 선행 스코프 우선 — SMQ 식별 필터 제거
GUARD_SCOPE_GLOBAL_DROP = "scope.global_aggregate_drop"       # 선행 스코프 우선 — 전역 집계 해제
GUARD_RESOURCE_TYPE_FILTER_IGNORED = "compile.resource_type_filter_ignored"
GUARD_IR_ORDER_BY = "ir.order_by"                             # IR 정렬 사용(표면어 미의존)
GUARD_IR_LIMIT = "ir.limit"                                   # IR 상한 사용
GUARD_IR_TIME_RANGE = "ir.time_range"                         # IR 기간 사용(호출부 미지정 시)
GUARD_HYPERNYM_EXPAND = "taxonomy.hypernym_expand"            # 상위어 단독 질의 → 하위 전부 제시


def note_guard(name: str, detail: str = "") -> None:
    """교정 가드·폴백 게이트의 발동을 계측한다(R4 — 발동률 비교 재료).

    Args:
        name: 가드 식별자(``GUARD_*`` 상수)
        detail: 로그에 남길 부가 사유(선택)
    """
    _GUARD_COUNTERS[name] = _GUARD_COUNTERS.get(name, 0) + 1
    logger.info(
        "[가드계측] %s 발동(누적 %d)%s",
        name, _GUARD_COUNTERS[name], f" — {detail}" if detail else "",
    )


def guard_counters() -> dict[str, int]:
    """가드 발동 누적 카운터의 사본을 반환한다."""
    return dict(_GUARD_COUNTERS)


def reset_guard_counters() -> None:
    """가드 발동 카운터를 초기화한다(계측 구간 분리·테스트용)."""
    _GUARD_COUNTERS.clear()


def _guard_delta(before: dict[str, int]) -> dict[str, int]:
    """스냅샷 이후 발동한 가드만 골라 {이름: 증분}으로 만든다(질의 단위 귀속)."""
    return {
        name: count - before.get(name, 0)
        for name, count in _GUARD_COUNTERS.items()
        if count - before.get(name, 0) > 0
    }
