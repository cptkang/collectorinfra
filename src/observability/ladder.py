"""오케스트레이션 사다리 확정 판정과 기동 로그 (D-161 / plans/70 P0-1).

## 왜 필요한가

실행 경로 4종은 **대등하게 병존하지 않는다** — 1 정본 + 3 폴백의 강등 사다리다.
그런데 그 구조가 코드·설정·문서 어디에도 명시되지 않아, `plans/70` v1이 `graph.py`의
`if/elif` 형태만 보고 "4경로 병존"으로 오독해 정본을 붕괴시킬 폐기를 권고했다.

이 모듈은 **기동 시 확정된 단과 강등 사유를 로그 1줄로 판독 가능**하게 만든다.

## 왜 요청별 카운터가 아닌가

확정은 `build_graph()` 안에서 1회 일어나고, `and not use_deep_agent` 조건이 하위 단의
노드 **등록 자체를 막는다**(빌드 타임 배타). 요청 시점에는 이미 단일 경로만 존재하므로
요청별 계측은 같은 값을 반복 기록할 뿐이다.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class LadderTier(str, Enum):
    """사다리 단. 값이 곧 로그 표기다."""

    DEEP_AGENT = "deep_agent"                    # 1단(정본) — deepagents 패키지
    INTENT_ORCHESTRATION = "intent_orchestration"  # 2단 — 의도 분해(트랙 A)
    SEMANTIC_ROUTER = "semantic_router"          # 3단 — 시멘틱 라우팅
    LEGACY = "legacy"                            # 4단 — field_mapper → schema_analyzer 직행

    @property
    def is_canonical(self) -> bool:
        """정본 단인지."""
        return self is LadderTier.DEEP_AGENT


#: 강등 사유. 정본이 아닐 때 **왜**인지를 구분한다 — 사유 없는 강등은 진단이 안 된다.
#: - none: 정본 확정
#: - flag_off: `enable_deepagents_package`가 off (운영 선택)
#: - orchestrator_unavailable: 플래그는 on인데 오케스트레이터(vLLM/Gemini) 미가용
#: - package_missing: 백엔드는 골랐으나 deepagents 조립 실패(폐쇄망 wheel 미반입 등)
_REASON_NONE = "none"
_REASON_FLAG_OFF = "flag_off"
_REASON_ORCHESTRATOR_UNAVAILABLE = "orchestrator_unavailable"
_REASON_PACKAGE_MISSING = "package_missing"


def resolve_ladder_tier(
    config: Any,
    *,
    backend: str,
    buildable: bool,
) -> tuple[LadderTier, str]:
    """확정된 단과 강등 사유를 판정한다.

    분기 순서는 `build_graph()`의 노드 등록 순서와 일치해야 한다 —
    여기서만 순서가 달라지면 로그가 실제 경로와 어긋난다.

    Args:
        config: 앱 설정
        backend: `select_orchestration_backend()` 결과("deep_agent" | "semantic_router")
        buildable: `_deep_agent_buildable()` 결과

    Returns:
        (확정 단, 강등 사유)
    """
    if backend == "deep_agent" and buildable:
        return LadderTier.DEEP_AGENT, _REASON_NONE

    # 정본이 아닌 이유를 구분한다.
    if not getattr(config, "enable_deepagents_package", False):
        reason = _REASON_FLAG_OFF
    elif backend != "deep_agent":
        reason = _REASON_ORCHESTRATOR_UNAVAILABLE
    else:
        reason = _REASON_PACKAGE_MISSING

    if getattr(config, "enable_intent_orchestration", False):
        return LadderTier.INTENT_ORCHESTRATION, reason
    if getattr(config, "enable_semantic_routing", False):
        return LadderTier.SEMANTIC_ROUTER, reason
    return LadderTier.LEGACY, reason


def resolve_flag_origin(flag_value: bool | None) -> str:
    """플래그 값이 명시 설정인지 암묵 활성인지 판정한다.

    `enable_semantic_routing`·`enable_intent_orchestration`은 tri-state다 —
    `None`이면 "멀티 DB 등록 여부"로 자동 결정된다. 운영 경로가 DB 상태에 종속되므로
    그 사실이 로그에 드러나야 한다.

    Note:
        `model_post_init`이 `None`을 bool로 덮어쓰므로, 호출부는 **덮어쓰기 전 원본**을
        넘겨야 의미가 있다. 그렇지 못하면 항상 `explicit_env`가 나온다.
    """
    return "auto_multidb" if flag_value is None else "explicit_env"


def log_ladder_resolution(
    tier: LadderTier,
    reason: str,
    *,
    flag_origin: str = "explicit_env",
) -> None:
    """확정 단을 기동 로그로 남긴다.

    정본이 아니면 경고를 **1회** 추가한다. 빌드 시 1회 호출이므로 스팸이 되지 않는다.
    """
    logger.info(
        "오케스트레이션 사다리 확정: tier=%s degraded_reason=%s resolved_by=%s",
        tier.value, reason, flag_origin,
    )
    if not tier.is_canonical:
        logger.warning(
            "정본 경로(deep_agent)가 아닌 %s 단으로 확정됐습니다 (사유: %s). "
            "의도한 구성인지 확인하세요 — docs/21_orchestration_ladder.md",
            tier.value, reason,
        )


#: 마지막으로 확정된 단. 확정은 빌드 시 1회뿐이라 카운터가 아니라 단일 스냅샷이다.
#: 실패 트레이스가 이 값을 읽어 "어느 파이프라인에서 난 실패인가"를 함께 남긴다 —
#: 단이 달라지면 노드 구성 자체가 달라지므로, 이 값이 없으면 node_path를 해석할 기준이 없다.
_resolution: dict[str, str] | None = None


def record_ladder_resolution(
    tier: LadderTier,
    reason: str,
    *,
    flag_origin: str = "explicit_env",
) -> None:
    """확정 결과를 프로세스에 보존하고 기동 로그를 남긴다.

    보존과 로그를 한 진입점에 묶는다 — 나뉘면 한쪽만 호출돼 둘이 어긋난다.
    재빌드 시에는 덮어쓴다(누적 아님). 최신 확정만이 유효한 사실이다.
    """
    global _resolution
    _resolution = {
        "tier": tier.value,
        "degraded_reason": reason,
        "resolved_by": flag_origin,
    }
    log_ladder_resolution(tier, reason, flag_origin=flag_origin)


def current_ladder() -> dict[str, str] | None:
    """확정된 단을 조회한다. 아직 빌드 전이면 None.

    호출부가 반환값을 변형해도 내부 상태가 오염되지 않도록 사본을 준다.
    """
    return dict(_resolution) if _resolution is not None else None


def reset_ladder() -> None:
    """확정 상태를 지운다 (테스트 격리용)."""
    global _resolution
    _resolution = None
