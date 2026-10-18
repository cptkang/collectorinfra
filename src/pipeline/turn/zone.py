"""존 역질문 판정·자리표시자 치환·범위 축소/재확장.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
from typing import Any

from src.routing.db_authz import authorized_db_ids
from src.utils.query_gen_common import (
    ZONE_CLARIFY_OPTIONS,
    ZONE_GROUP_EXCLUSIVE_QUESTION,
    build_zone_clarification,
    has_mixed_zone_group_terms,
    mixed_zone_groups,
    rewrite_zone_mentions_for_selection,
)

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


# ── 존 모호성 역질문 (Plan 75 §4 — clarification 배선) ─────────────────────────
# 결정적 게이트(D-035): LLM(clarification_needed) 방출에 의존하지 않는다.
# 발동 = (존 단위 대량 조회 의도 — is_full_scan_query, LIMIT 상향 게이트와 동일 판정 공유)
# AND (존 식별 불가), 또는 "ㅇㅇ존" 리터럴(버튼 프리필 무수정 전송 — 항상 모호).
# 후속 턴은 직전 존 승계가 우선이므로 비발동(§4.2), 단 "ㅇㅇ존" 리터럴은 턴과 무관하게
# 발동. selected_db_ids가 이미 오면 비발동(재개 턴).
_ZONE_PLACEHOLDER = "ㅇㅇ존"
# 선택지 입도는 DB 라우팅 입도와 일치(§4.4 확정 — 체크박스 3개 단독, 단축 버튼 없음).
# canonical은 utils.query_gen_common.ZONE_CLARIFY_OPTIONS(D-153 — 후단 게이트와 공유,
# 계층 규칙상 utils로 이동). 여기서는 기존 이름으로 alias만 유지.
_ZONE_OPTIONS: tuple[dict[str, Any], ...] = ZONE_CLARIFY_OPTIONS


_ZONE_LABEL_BY_ID: dict[str, str] = {o["db_id"]: o["label"] for o in _ZONE_OPTIONS}


def _zone_display_label(db_id: str) -> str:
    """존 선택지 라벨 → 레지스트리 표시명 → db_id 순으로 사용자 표기를 고른다."""
    if db_id in _ZONE_LABEL_BY_ID:
        return _ZONE_LABEL_BY_ID[db_id]
    from src.routing.domain_config import get_domain_by_id

    domain = get_domain_by_id(db_id)
    return domain.display_name if domain else db_id


def _substitute_zone_placeholder(query: str, selected_db_ids: list[str] | None) -> str:
    """존 선택 재개 턴에서 존 표기를 선택 존 라벨로 치환한다.

    결정적 문자열 치환(LLM 재해석 아님 — 라우팅은 selected_db_ids가 이미 고정).
    ① 'ㅇㅇ존' 플레이스홀더 치환: 미치환 시 sub_query·처리 현황·응답 서술에 'ㅇㅇ존'이
       그대로 남는다(2026-07-24 폐쇄망 실측: 데이터는 정상인데 화면 표기가 전부 'ㅇㅇ존').
    ② 혼합 존 열거 재작성(D-154): 상호배타 재선택 후 원문("은행존 및 공동존 여의도…")이
       그대로 흐르면 처리 현황에 미선택 존이 남고, 미선택 존 위치어가 SQL WHERE로
       누출된다(2026-08-05 실측). 미선택 그룹 표면어를 포함한 열거만 선택 라벨로 치환.
    """
    if not selected_db_ids:
        return query
    if _ZONE_PLACEHOLDER in (query or ""):
        labels = [_ZONE_LABEL_BY_ID.get(d, d) for d in selected_db_ids]
        return query.replace(_ZONE_PLACEHOLDER, ", ".join(labels))
    return _replace_unregistered_zones(
        rewrite_zone_mentions_for_selection(query, selected_db_ids), selected_db_ids
    )


def _replace_unregistered_zones(query: str, selected_db_ids: list[str]) -> str:
    """③ 등록되지 않은 존 표기를 선택 존 라벨로 치환한다(plans/123 W-4).

    존 선택 답변 턴(미등록 존 역질문 → 사용자가 존을 골라 재전송)에서 원문의 「판교존」이 그대로
    흐르면 sub_query·SQL 주석·응답 서술에 없는 존 이름이 남고(run `20260923-103638` R3-03 —
    「전체 1690건의 판교존 서버」), 생성기가 그 표기를 조건으로 오인한다. 라우팅은 이미
    `selected_db_ids`가 정했으므로 텍스트만 바꾼다. 바꾼 사실은 `_turn_disclosures`가 알린다.
    """
    from src.routing.db_scope import find_unregistered_zone_terms

    unknown = find_unregistered_zone_terms(query)
    if not unknown:
        return query
    labels = ", ".join(_zone_display_label(d) for d in selected_db_ids)
    for token in unknown:
        query = query.replace(token, labels)
    return query


def _scope_narrowed_or_none(
    body: Any, config: Any, current_user: dict[str, Any] | None
) -> dict[str, Any] | None:
    """이번 턴이 범위를 **좁혔는지** 판정하고 기록을 만든다 (D-176 후속4 · §5.3 불변식 6).

    범위 축소는 정보 손실이 복구되지 않는 절단이라, 무엇을 보지 않았는지가 응답과 감사
    로그 양쪽에 남아야 한다. 전부 선택했으면 절단이 아니므로 None.
    """
    if not body.selected_db_ids:
        return None
    from src.domain.scope_select import narrowed_record
    from src.routing.execution_groups import partition_execution_groups

    allowed = (current_user or {}).get("allowed_db_ids")
    active = config.multi_db.get_active_db_ids() or []
    # 권한 밖 존은 미조회 목록에도 싣지 않는다(D-264 ② · plans/123 W-2 ④ — 대상이 활성 ∩ 허용)
    targets = [d for d in active if allowed is None or d in set(allowed)]
    record = narrowed_record(
        partition_execution_groups(targets),
        body.selected_db_ids,
        # 그룹 안 일부 선택도 좁힌 것이다 — 미조회 DB를 존 이름으로 적는다(plans/123 W-2 ③)
        db_labels={d: _zone_display_label(d) for d in targets},
    )
    if record:
        record["all_db_ids"] = targets
    return record


def build_scope_reexpand(
    record: dict[str, Any] | None, original_query: str
) -> dict[str, Any] | None:
    """미조회 범위를 되돌릴 **사후 패널**을 만든다(폼필 역질문 패널과 동형).

    이것이 없으면 §5.3 불변식 6의 절반(기록)만 지켜진다 — 사용자가 "아, 은행존도 봐야겠다"
    고 생각했을 때 원문을 다시 타이핑해야 한다면 좁히기의 비용이 이득을 넘는다.
    """
    if not record or not record.get("skipped_db_ids"):
        return None
    return {
        "kind": "scope_reexpand",
        "question": (
            f"{', '.join(record.get('skipped') or [])}은(는) 조회하지 않았습니다. "
            "전체 범위로 다시 조회할까요?"
        ),
        "options": [{
            "key": "__all__",
            "label": "전체 범위로 다시 조회",
            "db_ids": list(record.get("all_db_ids") or []),
            "default": True,
        }],
        "original_query": original_query or "",
        "multi": False,
    }


def _authorized_zone_clarification(
    config: Any, current_user: dict[str, Any] | None, query: str, **kwargs: Any
) -> dict[str, Any] | None:
    """존 선택 역질문 — 선택지를 사용자 조회 권한(D-232)으로 거른다 (plans/116 §10.3).

    종전에는 활성 DB 전체로 선택지를 만들어, 샌드박스 권한만 있는 사용자에게도 은행존·공동존을
    물었다(고르면 요청 경계 인가가 「권한 없음」으로 막는 막다른 길). 선택지 수 규칙은 종전과
    같다 — 권한 내 존이 0이면 묻지 않고(`build_zone_clarification`의 "존 전부 비활성" 폴백과 같은
    처리 · 인가 0이면 파이프라인이 D-232 ③ 사유를 알린다), 1이상이면 묻는다.

    Args:
        config: AppConfig
        current_user: 요청 사용자(`allowed_db_ids`·`role`). None이면 거르지 않는다(종전 동작)
        query: 원문 질의
        **kwargs: `build_zone_clarification` 인자(question·has_file·group_exclusive)
    """
    active = config.multi_db.get_active_db_ids() or [o["db_id"] for o in ZONE_CLARIFY_OPTIONS]
    user = current_user or {}
    ids = authorized_db_ids(active, user.get("allowed_db_ids"), user.get("role"))
    if not ids:
        # 빈 목록을 넘기면 build_zone_clarification은 "제한 없음"으로 읽어 전 존을 낸다
        logger.info("존 역질문 생략 — 조회 권한 내 DB 0개(D-232)")
        return None
    return build_zone_clarification(ids, query, **kwargs)


def _zone_group_exclusive_or_none(
    query: str, selected_db_ids: list[str] | None, config: Any, *, has_file: bool = False,
    current_user: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """존 그룹 상호배타 위반이면 안내 문구를 붙인 존 선택 clarification을 반환한다.

    (D-143 후속3) 은행존(b0)과 공동존(gp/yd)은 동시 조회 불가 — 담당 조직 분리(존 조합
    실수요 없음, 사용자 확정 2026-08-05) + b0+gp 조합의 FabriX PII 필터 차단(미종결) 회피.
    ①혼합 selected_db_ids(프론트 우회·API 직접 호출 포함 — UI 게이트 ≠ 검증)와
    ②혼합 텍스트 지정("은행존과 공동존 김포…")을 결정적으로 감지해, 에러 대신
    기존 존 선택 UI로 재선택을 요청한다(막다른 에러·침묵 강등 금지). 턴 유형 무관 발동.
    """
    if not config.multi_db.zone_group_exclusive:
        return None
    if selected_db_ids:
        if not mixed_zone_groups(selected_db_ids):
            return None  # 단일 그룹 선택 — 정상 재개
    elif not has_mixed_zone_group_terms(query or ""):
        return None
    return _authorized_zone_clarification(
        config,
        current_user,
        query or "",
        question=ZONE_GROUP_EXCLUSIVE_QUESTION,
        has_file=has_file,
        group_exclusive=True,
    )


def _unregistered_zone_clarification_or_none(
    query: str, config: Any, *, has_file: bool = False, current_user: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """원문이 **등록되지 않은 존**을 지목하면 존 선택 역질문을 돌려준다 (plans/108 CU-B2 · G-3).

    run `20260918-182507` R3-03(3회 전건 동일): "판교존 서버 목록"에 대해 생성 SQL이
    `-- 판교존(지역 힌트는 스키마에 없으므로 무시)` 주석을 달고 전 서버 1,690건을 반환했다.
    사용자가 지목한 스코프를 **조용히 버린** 것이라 「침묵적 폴백 금지」 위반이다.

    탐지는 `find_unregistered_zone_terms`가 좁게 판정한다(레지스트리 어휘 화이트리스트 +
    `…존` 일반 낱말 접미 배제). 오탐의 대가는 존 선택창 재표시라 막다른 에러가 아니지만,
    정상 질의를 가로채지 않는 것이 우선이라 미탐 쪽으로 기운 규칙이다.

    Args:
        query: 사용자 원문 질의
        config: AppConfig (활성 DB·존 그룹 배타 설정)
        has_file: 파일(폼필) 경로 여부 — 프론트가 보관 파일과 함께 재전송한다
        current_user: 요청 사용자 — 선택지를 조회 권한으로 거른다(plans/116 §10.3)

    Returns:
        clarification 페이로드 dict. 미등록 존이 없으면 None.
    """
    from src.routing.db_scope import find_unregistered_zone_terms

    unknown = find_unregistered_zone_terms(query)
    if not unknown:
        return None
    named = ", ".join(f"'{t}'" for t in unknown)
    logger.info("미등록 존 지목 감지(plans/108 CU-B2): %s — 존 선택 역질문 발행", named)
    exclusive = config.multi_db.zone_group_exclusive
    return _authorized_zone_clarification(
        config,
        current_user,
        query or "",
        question=(
            f"{named}은(는) 등록되지 않은 존입니다. 조회할 수 있는 존은 아래 목록뿐입니다. "
            + (
                "(은행존과 공동존은 동시 선택 불가 — 공동존은 김포/여의도 복수 선택 가능)"
                if exclusive
                else "(복수 선택 가능 — 전체 조회는 모두 선택)"
            )
        ),
        has_file=has_file,
        group_exclusive=exclusive,
    )
