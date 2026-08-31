"""호스트 조사 경로 — `mcp_server` 고수준 도구 배선 (Plan 78 W3-1·W3-2·W3-3 · WU-18).

**중간 비용대의 공백을 메운다.** 지금까지 "서버 상태를 본다"는 요구가 갈 곳은 둘뿐이었다 —
`data_query`(DB SQL)이거나 `fault_diagnosis`(`sre_agent` 위임 · 비쌈). 그 사이의
**OS 구성·자원 현황·메트릭 추세 단건 조회**가 통째로 비어 있었다(78 §2.2 G1).

이 모듈이 `DBHubClient.inspect_host`(WU-13)의 **프로덕션 호출부**다. 그전까지 호출부가
`tests/`에만 있어, W3-1 수용 기준의 *"본체에서 호출되고 소비된다"* 가 e2e로 충족되지 않았다
(`docs/18_known_mistakes.md` 2026-08-27).

설계는 `SPEC-host-inspect-routing.md`. 요지 셋:

1. **도구는 하나만 는다**(W3-4) — 프로파일 4종을 subagent 1개가 `profile` 인자로 흡수한다.
2. **게이트는 handler에**(W3-3 · P14) — 도구 목록은 **항상 고정**(빼면 KV 캐시가 무효화된다).
   비활성이면 handler가 **구조화 거부**를 돌려주고, 모델은 그 사유를 보고 대체 경로를 고른다.
3. **판정은 결정적**(D-035) — 프로파일도 대상도 코드가 정한다. LLM은 값을 만들지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig
from src.db import get_db_client
from src.utils.prior_targets import TargetRef

logger = logging.getLogger(__name__)

#: SUBAGENT_REGISTRY 키 — `_TOOL_NAMES` 동사+목적어 관례(W3-4).
HOST_INSPECT_AGENT = "host_inspect"

#: 거부·실패 사유 키. 침묵 폴백 금지 — 호출자가 이 키를 보고 다음 행동을 고른다.
DEGRADED_KEY = "degraded_reason"

# ── 프로파일 판정 (W3-2) ────────────────────────────────────────────────
#
# **의도적으로 좁다.** `data_query`는 본체의 주력 경로라 여기서 욕심을 내면 정상 조회를
# 잠식하는데, WU-06(분포 실측)이 G-BILL로 막혀 있어 **정확도를 측정할 수단이 없다**
# (SPEC §0.1). 측정 없이 넓히지 않는다 — 넓히는 것은 WU-06 이후의 판단이다.
# Known Mistakes: "금지·교정 규칙은 범위를 좁게 못 박는다."
#
# 선언 순서 = 충돌 시 우선순위. `processes`는 여기 없다 — 실시간 프로세스 조회는
# `process_query`가 이미 1급 경로다(D-041 · D-046/047 결정적 교정).
_PROFILE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("os_config", ("os 정보", "os정보", "운영체제", "커널", "os 버전", "os 구성", "os구성")),
    ("resource_status", ("자원 현황", "자원현황", "리소스 현황", "리소스현황")),
    ("metric_trend", ("메트릭 추세", "지표 추세", "사용률 추세")),
)

#: `inspect_host`가 프로파일별로 요구하는 식별자 (D-046 — 폴스타는 server_name ≠ hostname).
_PROFILE_IDENTIFIER: dict[str, str] = {
    "os_config": "hostname",
    "resource_status": "server_name",
    "metric_trend": "server_name",
}


def detect_profile(sub_query: str) -> Optional[str]:
    """질의 문자열에서 조사 프로파일을 **결정적으로** 판정한다 (W3-2).

    Args:
        sub_query: task의 sub_query (또는 격리 입력의 user_query)

    Returns:
        `os_config` | `resource_status` | `metric_trend`, 해당 없으면 None.
        None은 "이 경로가 아니다"라는 뜻이지 실패가 아니다.
    """
    text = (sub_query or "").lower()
    if not text:
        return None
    for profile, keywords in _PROFILE_KEYWORDS:
        if any(k in text for k in keywords):
            return profile
    return None


def has_target_signal(state: dict, app_config: AppConfig) -> bool:
    """이번 턴에 **조사 대상 호스트 신호가 있는지** 판정한다 (W3-2 발화 조건 ③).

    `intent_planner`가 경로를 교정하기 전에 부른다. **이 함수가 `src.utils.prior_targets`
    접촉을 이 모듈 안에 가둔다** — `intent_planner`는 W1(대상 해소)의 소유 경계 밖이고,
    그 경계는 테스트로 고정돼 있다(`test_r13_boundary_untouched` · 80 §6 소유권 계약).

    planner 시점에는 선행 task 결과가 아직 없으므로 `prior_targets`는 보지 않는다 —
    **이번 턴 지목(`filter_conditions`)과 승계 엔티티(`previous_entities`)만** 본다.

    Args:
        state: 현재 상태
        app_config: 앱 설정(상한)

    Returns:
        대상이 하나라도 해소되면 True.
    """
    from src.utils.prior_targets import resolve_targets

    parsed = state.get("parsed_requirements") or {}
    ctx = state.get("conversation_context") or {}
    resolution = resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        previous_entities=ctx.get("previous_entities"),
        max_targets=app_config.composite.max_targets,
    )
    return bool(resolution.targets)


def _identifier_for(profile: str, target: TargetRef) -> tuple[Optional[str], Optional[str]]:
    """프로파일이 요구하는 식별자를 대상에서 뽑는다.

    한 필드로 뭉개면 절반이 0건이 된다(TargetRef 주석 · D-046). 요구 필드가 비어 있으면
    **다른 필드로 대체하지 않는다** — 폴스타에서 server_name과 hostname은 다른 값이므로
    대체는 엉뚱한 호스트를 조사하는 길이다.

    Returns:
        `(hostname, server_name)` — 요구하지 않는 쪽은 None.
    """
    if _PROFILE_IDENTIFIER[profile] == "hostname":
        return (target.hostname or None), None
    return None, (target.server_name or None)


async def run_host_inspect(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict[str, Any]:
    """`mcp_server` 고수준 도구로 호스트를 단건 조사한다 (W3-1 호출부 · W3-3 게이트).

    SUBAGENT_REGISTRY handler 규약(task, isolated, *, llm, app_config)을 따른다.
    `llm`은 쓰지 않는다 — 프로파일·대상·인자가 전부 결정적이다(D-035).

    **게이트는 여기 하나뿐이다**(W3-3 · P14): 도구 목록에서 빼는 방식으로 라우팅하지 않는다 —
    도구 정의는 컨텍스트 접두부라 목록이 흔들리면 이후 전 턴의 KV 캐시가 무효화된다.
    목록은 고정하고 **가용성만** 여기서 제어하며, 거부는 **조용히 하지 않고** 사유를 구조화해
    돌려준다 — 모델이 대체 경로를 고를 수 있어야 한다(W3-4).

    Args:
        task: 현재 TaskSpec
        isolated: 격리 입력(parsed_requirements·conversation_context·prior_* 포함)
        llm: 미사용 (시그니처 호환)
        app_config: 앱 설정

    Returns:
        성공 시 `inspect_host`의 서버 반환 계약을 **변형 없이** 실은 dict
        (`{rows, row_count, queried_at, source_kind, source, engine}` · D-122),
        실패·거부는 `{error, degraded_reason, ...}`.
    """
    # ── 게이트 (W3-3 · fail-closed) ─────────────────────────────────
    if not app_config.composite.investigation_enabled:
        logger.info("host_inspect 거부: composite.investigation_enabled=False")
        return {
            "error": "호스트 조사 경로가 비활성입니다(COMPOSITE_INVESTIGATION_ENABLED).",
            DEGRADED_KEY: "composite_investigation_disabled",
            "organized_data": "",
        }

    sub_query = task.get("sub_query") or isolated.get("user_query") or ""
    profile = detect_profile(sub_query)
    if not profile:
        logger.info("host_inspect 거부: 프로파일 미판정 sub_query=%r", sub_query[:120])
        return {
            "error": "조사 프로파일을 판정하지 못했습니다(OS 구성·자원 현황·메트릭 추세 중 하나여야 합니다).",
            DEGRADED_KEY: "profile_undetected",
            "organized_data": "",
        }

    # ── 대상 해소 — 공통 모듈 경유 (W1-4 · G5) ──────────────────────
    # 사본을 만들지 않는다(D-053): 세 진입 경로가 쓰는 그 함수를 그대로 쓴다.
    from src.orchestration.process_query import (  # 지연 임포트 — 순환 방지
        _resolve_db_id,
        resolve_investigation_targets,
    )

    db_id = _resolve_db_id(task, isolated, sub_query, app_config)
    resolution = resolve_investigation_targets(isolated, db_id=db_id)
    if not resolution.targets:
        logger.info("host_inspect 0건: 대상 미식별 db_id=%s source=%s", db_id, resolution.source)
        return {
            "error": "조사 대상 서버를 식별하지 못했습니다. 서버명을 지정해 주세요.",
            DEGRADED_KEY: "target_unresolved",
            "organized_data": "",
        }

    # **단건 조회 경로다**(78 W3-2 경로표 "단건 조회"). N개 대상은 W2 fan-out 소관이므로
    # 여기서 조용히 첫 건만 쓰지 않고 **절단 사실을 결과에 싣는다**(침묵 절단 금지).
    target = resolution.targets[0]
    truncated = len(resolution.targets) - 1

    hostname, server_name = _identifier_for(profile, target)
    if not (hostname or server_name):
        need = _PROFILE_IDENTIFIER[profile]
        logger.info("host_inspect 0건: %s 프로파일이 요구하는 %s 부재", profile, need)
        return {
            "error": f"{profile} 조사에 필요한 {need}을(를) 대상에서 찾지 못했습니다.",
            DEGRADED_KEY: "identifier_missing",
            "organized_data": "",
        }

    logger.info(
        "host_inspect 진입: profile=%s db_id=%s hostname=%s server_name=%s targets=%d",
        profile, db_id, hostname, server_name, len(resolution.targets),
    )

    async with get_db_client(app_config, db_id=db_id) as client:
        result = await client.inspect_host(
            profile=profile,
            hostname=hostname,
            server_name=server_name,
        )

    # 반환 계약은 **서버가 정본**이다(D-122) — 본체는 변형하지 않고 그대로 싣는다.
    if isinstance(result, dict) and result.get("error"):
        return {**result, DEGRADED_KEY: "inspect_failed", "organized_data": ""}

    payload: dict[str, Any] = {**result, "profile": profile, "target": target.model_dump()}
    if truncated > 0:
        payload["truncated_targets"] = truncated
        payload["truncation_note"] = (
            f"대상 {len(resolution.targets)}건 중 1건만 조사했습니다"
            f"(단건 조회 경로 — 나머지 {truncated}건은 조사하지 않았습니다)."
        )
    return payload
