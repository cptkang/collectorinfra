"""오케스트레이션 사다리 확정 판정과 기동 로그 (D-161 / plans/70 P0-1 · D-225 기준 개정).

## 왜 필요한가

실행 경로 4종은 **대등하게 병존하지 않는다** — 빌드 타임에 한 단만 확정되는 사다리다.
그런데 그 구조가 코드·설정·문서 어디에도 명시되지 않아, `plans/70` v1이 `graph.py`의
`if/elif` 형태만 보고 "4경로 병존"으로 오독해 운영 경로를 붕괴시킬 폐기를 권고했다.

**기준 경로(기준 운영 단)는 2단 `intent_orchestration`이다**(D-251 ①, 2026-09-23 — D-225 ①의
3단 기준을 전면 개정). 3단 `semantic_router`는 2단 대비 성능을 재는 **비교 arm**이고, 1단
`deep_agent`는 폐기 대상이 아닌 **부가 경로(opt-in)** 다(D-225 ② 유지). 4단 `legacy`는 기준이 아닌
단이다. 종전(D-161)에는 1단을 정본으로, D-225에서는 3단을 기준으로 보았다.

이 모듈은 **기동 시 확정된 단과 그 사유를 로그 1줄로 판독 가능**하게 만든다.

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

    DEEP_AGENT = "deep_agent"                    # 1단(부가 경로 opt-in) — deepagents 패키지
    INTENT_ORCHESTRATION = "intent_orchestration"  # 2단(기준 경로 · D-251) — 의도 분해(트랙 A)
    SEMANTIC_ROUTER = "semantic_router"          # 3단(비교 arm) — 시멘틱 라우팅
    LEGACY = "legacy"                            # 4단 — field_mapper → schema_analyzer 직행

    @property
    def is_canonical(self) -> bool:
        """기준 경로 단인지 (D-251 ① — 2단 `intent_orchestration` · D-225 ①의 3단을 개정)."""
        return self is LadderTier.INTENT_ORCHESTRATION

    @property
    def is_optin(self) -> bool:
        """부가 경로(opt-in) 단인지 (D-225 ② — 1단 `deep_agent`). 강등이 아니다."""
        return self is LadderTier.DEEP_AGENT


#: 확정 사유. 어느 단으로 왜 확정됐는지, opt-in이 성립하지 않았으면 **왜**인지를 구분한다 —
#: 사유 없는 확정은 진단이 안 된다. 로그 필드명(`degraded_reason`)과 어휘 5종은 판독 도구·러너
#: 정규식 호환을 위해 D-251(기준 단 2단 전환) 뒤에도 그대로 둔다.
#: - none: 3단(비교 arm — 2단 플래그 명시 off) 확정, 또는 부가 경로(1단) opt-in 확정
#: - orchestrator_unavailable: 1단 플래그는 on인데 오케스트레이터(vLLM/Gemini/mlx) 미가용
#: - package_missing: 백엔드는 1단을 골랐으나 deepagents 조립 실패(폐쇄망 wheel 미반입 등)
#: - intent_flag_on: 1단 플래그 off · 2단 플래그 on(미입력 포함)으로 **기준 단(2단)** 확정(D-251)
#: - semantic_routing_off: 1단 플래그 off · 2·3단 플래그도 off라 4단 확정
#: 종전 `flag_off`(1단 플래그 off)는 D-225로 폐기했다 — 1단 off가 기준 상태가 됐으므로
#: 그 자체로는 사유가 아니고, 실제로 어느 비기준 단으로 갔는지를 위 두 어휘가 대신 말한다.
_REASON_NONE = "none"
_REASON_ORCHESTRATOR_UNAVAILABLE = "orchestrator_unavailable"
_REASON_PACKAGE_MISSING = "package_missing"
_REASON_INTENT_FLAG_ON = "intent_flag_on"
_REASON_SEMANTIC_ROUTING_OFF = "semantic_routing_off"

#: 1단 opt-in(플래그 on)이 성립하지 않은 사유 — 의도하지 않은 경로로 확정됐다는 뜻이다.
#: 시나리오 러너의 `scripts/scenario/server.py` `UNINTENDED_DEGRADATION`과 같은 집합이어야 한다.
OPTIN_FAILURE_REASONS = frozenset({_REASON_ORCHESTRATOR_UNAVAILABLE, _REASON_PACKAGE_MISSING})


def resolve_ladder_tier(
    config: Any,
    *,
    backend: str,
    buildable: bool,
) -> tuple[LadderTier, str]:
    """확정된 단과 그 사유를 판정한다.

    분기 순서는 `build_graph()`의 노드 등록 순서와 일치해야 한다 —
    여기서만 순서가 달라지면 로그가 실제 경로와 어긋난다.

    Args:
        config: 앱 설정
        backend: `select_orchestration_backend()` 결과("deep_agent" | "semantic_router")
        buildable: `_deep_agent_buildable()` 결과

    Returns:
        (확정 단, 사유) — 사유 어휘는 모듈 상단 `_REASON_*` 주석 참조
    """
    if backend == "deep_agent" and buildable:
        return LadderTier.DEEP_AGENT, _REASON_NONE

    # 1단 opt-in이 켜졌는데 성립하지 않았으면 그 실패가 사유다 — 어느 하위 단으로 갔든
    # 운영자가 고른 경로가 아니므로 opt-in 실패를 먼저 말한다.
    optin_failure: str | None = None
    if getattr(config, "enable_deepagents_package", False):
        optin_failure = (
            _REASON_ORCHESTRATOR_UNAVAILABLE if backend != "deep_agent"
            else _REASON_PACKAGE_MISSING
        )

    if getattr(config, "enable_intent_orchestration", False):
        return LadderTier.INTENT_ORCHESTRATION, optin_failure or _REASON_INTENT_FLAG_ON
    if getattr(config, "enable_semantic_routing", False):
        return LadderTier.SEMANTIC_ROUTER, optin_failure or _REASON_NONE
    return LadderTier.LEGACY, optin_failure or _REASON_SEMANTIC_ROUTING_OFF


def resolve_flag_origin(flag_value: bool | None) -> str:
    """플래그 값이 명시 설정인지 암묵 활성인지 판정한다.

    `enable_semantic_routing`은 tri-state다 — `None`이면 "멀티 DB 등록 여부"로 자동
    결정된다. 운영 경로가 DB 상태에 종속되므로 그 사실이 로그에 드러나야 한다.

    `enable_intent_orchestration`도 tri-state지만 `None`은 DB 등록과 무관하게 **항상 on**이다
    (D-251 ① — 종전 D-225 ④는 항상 off) — 이 함수가 돌려주는 `auto_multidb`에 해당하지
    않는다. 기동 로그의 실제
    `resolved_by`(`auto_multidb` / `code_default` / `explicit_env`)는 두 플래그를 함께 보는
    `src/config.py` `model_post_init`의 `_orchestration_resolved_by`가 정한다.

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

    첫 줄(INFO) 형식은 바꾸지 않는다 — `scripts/scenario/server.py`가 정규식으로 읽는다.
    추가 줄은 **최대 1줄**이다(빌드 시 1회 호출이므로 스팸이 되지 않는다):
      - 1단 opt-in 실패(`OPTIN_FAILURE_REASONS`) → WARNING
      - 1단 opt-in 확정 → INFO (부가 경로 선택은 강등이 아니다 — D-225 ②)
      - 3단·4단 확정 → WARNING (기준 경로가 아니다 — D-251. 3단은 비교 arm)
      - 2단(기준) 확정 → 추가 줄 없음
    """
    logger.info(
        "오케스트레이션 사다리 확정: tier=%s degraded_reason=%s resolved_by=%s",
        tier.value, reason, flag_origin,
    )
    if reason in OPTIN_FAILURE_REASONS:
        logger.warning(
            "부가 경로(deep_agent) opt-in이 성립하지 않아 %s 단으로 확정됐습니다 (사유: %s). "
            "ENABLE_DEEPAGENTS_PACKAGE=true인데 오케스트레이터 가용성 또는 deepagents 조립이 "
            "실패했습니다 — docs/21_orchestration_ladder.md §4",
            tier.value, reason,
        )
    elif tier.is_optin:
        logger.info(
            "부가 경로(deep_agent) opt-in으로 확정됐습니다 — "
            "기준 경로는 intent_orchestration(2단)이며 "
            "1단은 ENABLE_DEEPAGENTS_PACKAGE=true일 때만 선택됩니다(D-225 ② · D-251) — "
            "docs/21_orchestration_ladder.md",
        )
    elif not tier.is_canonical:
        logger.warning(
            "기준 경로(intent_orchestration)가 아닌 %s 단으로 확정됐습니다 (사유: %s). "
            "3단은 2단 대비 비교 arm입니다(D-251) — 의도한 구성인지 확인하세요 "
            "— docs/21_orchestration_ladder.md §4",
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
