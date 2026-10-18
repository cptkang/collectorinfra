"""존 역질문 후단 게이트의 공통 꼬리 (plans/169 O6 · 종전 2단·3단 복제본).

2단(`orchestration.db_targeting._zone_clarification_or_none_task`)과
3단(`routing.semantic_router._zone_clarification_or_none_router`)이 각자의 앞단 비발동
신호(채널·승계·위치/DB 신호)를 거른 뒤 공통으로 밟는 판정 사슬만 모은다.

서버 식별 필터 → 조회 대상 파싱 → 대상 전부 폴스타 → 권한 거른 존 → 역질문 페이로드.
앞단 비발동 신호는 단마다 다르며 여기로 옮기지 않는다(plans/166 §7 U-1 ① — 통일은 행동 변경).
발동 로그도 호출부에 남긴다(로거 이름 불변).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.config import AppConfig
from src.routing.db_authz import authorized_db_ids
from src.utils.query_gen_common import (
    ZONE_CLARIFY_OPTIONS,
    build_zone_clarification,
    has_host_identifier_filter,
)


def zone_clarification_for_targets(
    parsed: dict[str, Any],
    targets: list[dict[str, Any]],
    query_text: str,
    *,
    allowed_db_ids: Sequence[str] | None,
    user_role: str | None,
    app_config: AppConfig,
) -> dict[str, Any] | None:
    """앞단 비발동 신호를 통과한 질의에 존 선택 역질문 페이로드를 만든다(결정적 · LLM 0).

    Args:
        parsed: 파싱 요구사항(`parsed_requirements` — 없으면 빈 dict)
        targets: 분류·핀·승계(2단) 또는 관련도 필터·정렬(3단)이 끝난 대상 DB 목록
        query_text: 역질문에 실을 원문 질의(프론트가 selected_db_ids와 함께 재전송)
        allowed_db_ids: 사용자 허용 DB 목록(D-232 — `None`이면 전체 허용)
        user_role: 사용자 역할
        app_config: 앱 설정

    Returns:
        발동 시 clarification 페이로드, 비발동이면 None
    """
    # 서버명 지목 질의는 존이 결과에 영향 없음(§4.2 ⓐ) — hostname으로 어차피 특정됨
    if has_host_identifier_filter(parsed):
        return None
    # 조회 대상 필드가 파싱되지 않은 질의(잡담성 오분류 등)는 비발동 — 과잉 역질문 방지
    if not parsed.get("query_targets"):
        return None
    # 대상이 전부 폴스타 존일 때만(비폴스타 대상은 존 선택이 무의미)
    polestar_ids = app_config.get_polestar_db_ids() or set()
    target_ids = [t.get("db_id") for t in targets if t.get("db_id")]
    if not target_ids or not all(d in polestar_ids for d in target_ids):
        return None
    # 선택지는 사용자 조회 권한(D-232)으로 거른다 — 라우트 사전 게이트
    # (`_authorized_zone_clarification`)와 같은 규칙(plans/116 §10.3). 빈 목록을 넘기면
    # build_zone_clarification이 "제한 없음"으로 읽으므로 권한 내 존이 없으면 묻지 않는다
    # (뒤이은 인가 필터가 사유를 알린다).
    zone_ids = authorized_db_ids(
        app_config.multi_db.get_active_db_ids() or [o["db_id"] for o in ZONE_CLARIFY_OPTIONS],
        allowed_db_ids, user_role,
    )
    if not zone_ids:
        return None
    return build_zone_clarification(
        zone_ids, query_text,
        # 존 그룹 상호배타(D-143 후속3) — 라우트 pre-gate와 동일 UI 규칙
        group_exclusive=bool(
            app_config.multi_db.zone_group_exclusive
        ),
    )
