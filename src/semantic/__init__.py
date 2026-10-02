"""시맨틱 IR·커버리지 판정 계층 (Plan 69 P5-1).

``src/nodes/semantic_compiler.py``에서 **상태·설정·LLM에 결합하지 않는 조각**(IR 모델·커버리지
판정·카탈로그 렌더·가드 계측·taxonomy 확장)을 옮겨 온 패키지다. 옮긴 이유는 순환 해소다 —
``src.tools.binding``/``src.tools.catalog``가 이 조각들을 쓰려고 ``src.nodes.semantic_compiler``를
임포트해 nodes→tools→nodes 모듈 순환이 있었다(계층 위반은 아니지만 순환).

구성:
    - ``ir``            : SMQ 중간표현(gold_smq 계약)과 IR 상수
    - ``coverage``      : 커버리지 판정 + dimension 인덱스·IR 정렬 해소
    - ``catalog_render``: 시맨틱 모델 → 프롬프트 카탈로그 텍스트
    - ``guards``        : 교정 가드·게이트 발동 계측(R4)
    - ``taxonomy``      : 상위어 단독 질의의 모호성 확장(N4/D-133)

모델 로드·컴파일(SQL 조립)·정규화·NL 진입점은 여전히 ``src.nodes.semantic_compiler``에 있고,
그 모듈이 여기 심볼을 재노출하므로 기존 임포트 경로는 무수정으로 동작한다.

계층: application — ``src.nodes``·``src.tools``와 동일(``scripts/arch_check.py`` 참조).
"""

from src.semantic.catalog_render import render_catalog
from src.semantic.coverage import check_coverage
from src.semantic.guards import (
    GUARD_BREAKDOWN_PROMOTE,
    GUARD_CAPACITY_INJECT,
    GUARD_HYPERNYM_EXPAND,
    GUARD_IR_LIMIT,
    GUARD_IR_ORDER_BY,
    GUARD_IR_TIME_RANGE,
    GUARD_MONTHLY_GATE,
    GUARD_PHYSICALCORE_DROP,
    GUARD_PHYSICALCORE_SWAP,
    GUARD_RANKING_SURFACE,
    GUARD_RESOURCE_TYPE_FILTER_IGNORED,
    GUARD_SCOPE_FILTER_STRIP,
    GUARD_SCOPE_GLOBAL_DROP,
    GUARD_TIME_FILTER_PROMOTE,
    GUARD_TIME_RANGE_OVERRIDE,
    guard_counters,
    note_guard,
    reset_guard_counters,
)
from src.semantic.ir import (
    CoverageResult,
    SMQ,
    SMQFilter,
    SMQMeasure,
    SMQOrderBy,
)

__all__ = [
    # IR 모델
    "SMQ",
    "SMQFilter",
    "SMQMeasure",
    "SMQOrderBy",
    "CoverageResult",
    # 커버리지 판정
    "check_coverage",
    # 카탈로그 렌더
    "render_catalog",
    # 가드 계측
    "note_guard",
    "guard_counters",
    "reset_guard_counters",
    "GUARD_PHYSICALCORE_DROP",
    "GUARD_PHYSICALCORE_SWAP",
    "GUARD_CAPACITY_INJECT",
    "GUARD_TIME_FILTER_PROMOTE",
    "GUARD_TIME_RANGE_OVERRIDE",
    "GUARD_BREAKDOWN_PROMOTE",
    "GUARD_MONTHLY_GATE",
    "GUARD_RANKING_SURFACE",
    "GUARD_SCOPE_FILTER_STRIP",
    "GUARD_SCOPE_GLOBAL_DROP",
    "GUARD_RESOURCE_TYPE_FILTER_IGNORED",
    "GUARD_IR_ORDER_BY",
    "GUARD_IR_LIMIT",
    "GUARD_IR_TIME_RANGE",
    "GUARD_HYPERNYM_EXPAND",
]
