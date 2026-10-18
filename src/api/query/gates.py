"""사전 게이트 판정 — 역질문·범위 선택·저장 값 삭제와 그 감사.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
from typing import Any

from src.api.schemas import QueryRequest
from src.pipeline.turn.zone import (
    _ZONE_PLACEHOLDER,
    _authorized_zone_clarification,
    _unregistered_zone_clarification_or_none,
    _zone_group_exclusive_or_none,
)
from src.utils.query_gen_common import is_full_scan_query

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


async def _audit_clarification(
    clarification: dict[str, Any], current_user: dict[str, Any] | None, thread_id: str | None
) -> None:
    """파이프라인 전 역질문의 발동을 감사에 남긴다 — 발동률 관측 (plans/82 v7 R-6 · D-249).

    범위 선택은 시간 임계 없이 묻기로 했고(U11) 그 대가인 습관화를 **발동률로** 통제한다.
    분모는 같은 감사 파일의 `user_request`다. 4개 진입점이 모두 부른다(경로 대칭).
    기록 실패는 질의를 막지 않는다.
    """
    from src.security.audit_logger import log_clarification

    try:
        await log_clarification(
            kind=str(clarification.get("kind") or ""),
            axis=clarification.get("axis"),
            option_count=len(clarification.get("options") or []),
            user_id=(current_user or {}).get("sub"),
            thread_id=thread_id,
        )
    except Exception as e:  # noqa: BLE001 — 감사 실패는 경고로 남기고 진행한다
        logger.warning("역질문 발동 감사 기록 실패: %s", e)


async def _audit_scope_narrowed(
    input_state: dict[str, Any], current_user: dict[str, Any] | None, thread_id: str | None
) -> None:
    """범위를 좁힌 턴을 감사에 남긴다 — §5.3 불변식 6의 감사 쪽 (plans/82 v7 R-6 · D-249).

    응답 쪽(미조회 범위 문구)은 `output_generator`가 붙인다. 좁히지 않은 턴은 기록하지 않는다.
    """
    record = input_state.get("scope_narrowed")
    if not record:
        return
    from src.security.audit_logger import log_scope_narrowed

    try:
        await log_scope_narrowed(
            selected=list(record.get("selected") or []),
            skipped=list(record.get("skipped") or []),
            skipped_db_ids=list(record.get("skipped_db_ids") or []),
            user_id=(current_user or {}).get("sub"),
            thread_id=thread_id,
        )
    except Exception as e:  # noqa: BLE001 — 감사 실패는 경고로 남기고 진행한다
        logger.warning("범위 축소 감사 기록 실패: %s", e)


def _zone_gate_deferred_to_tasks(config: Any, gate: str) -> bool:
    """2단 다중 시스템 배포면 라우트 존 게이트를 task 단위 게이트에 미룬다(plans/132 v1.5 후속 ①).

    소스 지목이 없는 「…서버들 담당 부서」 같은 질문은 라우트 시점에 어느 시스템인지 모른다 —
    여기서 폴스타 존을 물으면 ITAM 질문에 오답 되물음이 된다. 2단(`intent_orchestration`)은
    분류 뒤 task 게이트(`_zone_clarification_or_none_task`)가 대상이 전부 폴스타 존일 때만
    묻는다. 활성 시스템이 하나뿐이거나 2단이 아니면(확정 전 포함) 종전대로 라우트가 판정한다.

    활성 시스템 = 활성 DB의 소유 시스템(`registry.systems_of` — 분류 경로 `_multiple_systems_active`
    와 같은 재료 · 선언 없는·미등록 DB 제외) ∪ **서버 엔터티를 가진** 활성 비DB 시스템(엔터티 간선
    `owner`로 등장하는 시스템 — 간선이 없는 문서 검색은 서버 질문의 대상이 아니라 세지 않는다).
    """
    from src.observability.ladder import current_ladder

    if (current_ladder() or {}).get("tier") != "intent_orchestration":
        return False
    from src.orchestration.conditional_agents import active_conditional_systems
    from src.routing.registry import get_registry

    registry = get_registry()
    owners = {e.owner for e in registry.entity_edges()}
    systems = set(registry.systems_of(config.multi_db.get_active_db_ids() or []))
    systems |= {s for s in active_conditional_systems(config) if s in owners}
    if len(systems) < 2:
        return False
    logger.info(
        "라우트 %s 생략(plans/132 v1.5): 2단 · 활성 시스템 %s — task 단위 존 게이트에 위임",
        gate, sorted(systems),
    )
    return True


def _scope_select_or_none(
    body: Any,
    checkpoint_state: dict[str, Any] | None,
    config: Any,
    current_user: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """범위 사전 선택 역질문 (D-176 후속4 · plans/82 §5.3~§5.5).

    **모호성 해소(`_zone_clarification_or_none`) 다음 순위**로만 호출된다 — 두 질문이 한 턴에
    겹치면 사용자를 두 번 붙잡고, 성격이 다른 질문(진행 불가 vs 성능 최적화)을 같은 것으로
    학습시킨다.

    발동 형태를 **전량 조회 질의로 좁힌다**: 비용은 존당 SQL(~50ms)이 아니라 **스키마
    분석(존당 ≤20s) + LLM 생성**에 있고(§5.1 실측), 그 비용이 실제로 배가되는 형태가
    전량 조회다. 서버 식별자 하나를 찾는 질의는 탐색(D-176 후속3)이 ~150ms에 끝내므로
    여기서 사용자를 붙잡는 것은 **순손실**이다(§5.2).

    Args:
        body: 요청 본문
        checkpoint_state: 체크포인트(후속 턴이면 not None)
        config: 앱 설정
        current_user: 인증 주체(인가 존 필터)

    Returns:
        clarification 페이로드 또는 None.
    """
    from src.domain.scope_select import scope_question_or_none
    from src.routing.execution_groups import partition_execution_groups

    if body.selected_db_ids:
        return None  # 재개 턴
    query = body.query or ""
    # 후속 턴은 직전 존 승계가 우선이다(§4.2 비발동 — zone_select와 같은 규칙).
    # 스코프 해제 턴(D-205)은 zone_select와 같이 첫 턴 규칙으로 본다.
    if checkpoint_state is not None and not getattr(body, "reset_db_scope", False):
        return None
    if not is_full_scan_query(query):
        return None
    # 위치어가 있으면 D-065가 결정적으로 좁힌다 — 이미 정해진 것을 되묻지 않는다.
    from src.utils.query_gen_common import LOCATION_HINT_TERMS, term_in_text
    if any(term_in_text(t, query) for t in LOCATION_HINT_TERMS):
        return None
    # 존 없는 소스만 지목한 질의는 존 게이트와 같이 묻지 않는다(plans/132 N-10 — 대칭).
    from src.routing.db_scope import names_only_zoneless_sources
    if names_only_zoneless_sources(query):
        return None
    # 2단 다중 시스템이면 존 게이트와 같이 묻지 않는다(plans/132 v1.5 — 대칭).
    if _zone_gate_deferred_to_tasks(config, "범위 사전 선택"):
        return None

    allowed = (current_user or {}).get("allowed_db_ids")
    active = config.multi_db.get_active_db_ids() or []
    targets = [d for d in active if allowed is None or d in set(allowed)]
    groups = partition_execution_groups(targets)

    # 예상 시간 문구의 재료(§5.5 S-C · plans/82 v7 R-7) — 그룹 유형별 실측 분포를 싣는다.
    # 표본이 가장 적은 그룹이 기준이다: 한 그룹이라도 표본 미달이면 합계 추정이 근거를 잃어
    # 문구를 내지 않는다(scope_question_or_none이 samples < 20이면 생략).
    from src.observability.group_metrics import group_stats

    stats = [group_stats(g) for g in groups]
    groups = [
        {**g, "p50_ms": s["p50_ms"], "p90_ms": s["p90_ms"]} for g, s in zip(groups, stats)
    ]
    return scope_question_or_none(
        groups=groups,
        ctx={
            "zone_clarification_allowed": True,  # 텍스트 라우트만 이 함수를 부른다
            "original_query": query,
        },
        enabled=config.composite.scope_select_enabled,
        samples=min((s["sample_size"] for s in stats), default=0),
    )

async def _form_memory_delete_or_none(
    body: Any, checkpoint_state: dict[str, Any] | None, config: Any
) -> dict[str, Any] | None:
    """저장 값 패널의 삭제 버튼 요청(form_memory_delete)을 파이프라인 없이 결정적으로 처리한다(D-187).

    - 파이프라인·LLM 미경유(FIX-23의 "삭제했다" 환각 경로 원천 차단).
    - signature는 이 세션이 마지막으로 조회·채운 양식(`last_form_signature`)과 일치해야
      수행한다 — 클라이언트가 임의 시그니처를 보내 다른 양식의 이력을 지우는 것을 막는다.
    - 삭제 후 남은 항목으로 패널을 다시 만들어 돌려준다(진부화 방지). 0건이면 패널 없음.

    Returns:
        {"response": str, "form_memory_panel": dict | None} 또는 None(요청 아님).
    """  # noqa: E501 — 이동 전부터 있던 긴 줄(plans/179 W2)
    req = getattr(body, "form_memory_delete", None)
    if not isinstance(req, dict):
        return None
    from src.orchestration.intent_planner import build_form_memory_panel
    from src.schema_cache.form_memory import (
        delete_form_memory_entries,
        load_form_memory_answers,
    )

    signature = str(req.get("signature") or "")
    session_sig = (checkpoint_state or {}).get("last_form_signature")
    if not signature or signature != session_sig:
        logger.warning(
            "form_memory_delete 거부(D-187): 요청 signature=%r ≠ 세션 signature=%r",
            signature[:16], (session_sig or "")[:16],
        )
        return {
            "response": (
                "삭제 요청의 양식이 이 세션에서 조회한 양식과 일치하지 않아 처리하지 않았습니다. "
                "양식을 다시 첨부하고 '?'로 저장 값을 조회한 뒤 삭제해 주세요."
            ),
            "form_memory_panel": None,
        }
    fields = None if req.get("all") else [str(f) for f in (req.get("fields") or []) if f]
    if fields is not None and not fields:
        return {"response": "삭제할 항목을 선택해 주세요.", "form_memory_panel": None}
    removed, display = await delete_form_memory_entries(
        None, config, fields, signature=signature,
    )
    _sig, remaining, meta = await load_form_memory_answers(
        None, config, touch=False, signature=signature,
    )
    panel = build_form_memory_panel(signature, remaining, meta)
    name = display or "이 양식"
    if removed == 0:
        text = f"'{name}'에서 삭제할 항목이 없었습니다(이미 삭제됐거나 만료됨)."
    elif fields is None:
        text = f"'{name}'의 기억 {removed}건을 모두 삭제했습니다."
    else:
        text = (
            f"'{name}'의 기억 {removed}건을 삭제했습니다: "
            + ", ".join(f.replace("|", " > ") for f in fields)
        )
    if panel:
        text += f"\n\n남은 항목 {len(panel['entries'])}건은 아래 패널에서 계속 관리할 수 있습니다."
    else:
        text += "\n\n남은 저장 값이 없습니다."
    logger.info("form_memory_delete 처리(D-187): removed=%d, fields=%s", removed, fields)
    return {"response": text, "form_memory_panel": panel}


def _file_zone_clarification_or_none(
    query: str,
    selected_db_ids: list[str] | None,
    config: Any,
    current_user: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """파일(폼필) 경로 존 역질문 (Plan 75 §4 확장, 2026-07-24 실측 요구).

    폼필은 본질적으로 존 단위 대량 조회이므로, 텍스트 경로와 달리 "모든/전체" 표면어
    조건 없이 **위치어 미해소면 발동**한다(미발동 시 임의 존(b0 등)으로 오라우팅되는
    실측 사례). has_file=True로 표기해 프론트가 보관한 파일과 함께 재전송하게 한다.
    """
    q = query or ""
    # FIX-20(라이브 실측 2026-08-03): 확인 이력 조회·삭제는 DB 조회가 없어 존 선택이
    # 불필요 — 존 역질문이 가로채면 ③.45(결정적 단락)에 도달하지 못한다.
    from src.orchestration.intent_planner import is_form_memory_command  # type: ignore
    if is_form_memory_command(q):
        return None
    # 존 그룹 상호배타(D-143 후속3) — 텍스트 경로와 대칭(혼합 선택·혼합 텍스트)
    exclusive = _zone_group_exclusive_or_none(
        q, selected_db_ids, config, has_file=True, current_user=current_user
    )
    if exclusive:
        return exclusive
    if selected_db_ids:
        return None  # 선택 재개 턴
    # 미등록 존 지목(plans/108 CU-B2 · G-3) — 텍스트 경로와 대칭. 종전에도 위치어 미해소라
    # 역질문은 떴지만 "그 존이 없다"는 사실은 전달되지 않았다.
    unregistered = _unregistered_zone_clarification_or_none(
        q, config, has_file=True, current_user=current_user
    )
    if unregistered:
        return unregistered
    if _ZONE_PLACEHOLDER not in q:
        from src.utils.query_gen_common import LOCATION_HINT_TERMS, term_in_text
        if any(term_in_text(t, q) for t in LOCATION_HINT_TERMS):
            return None  # 위치어 해소 — D-065 결정적 보강이 처리
    return _authorized_zone_clarification(
        config,
        current_user,
        q,
        question=(
            "양식을 채울 대상 존이 지정되지 않았습니다. 아래에서 대상 존을 선택해 주세요. "
            + (
                "(은행존과 공동존은 동시 선택 불가 — 공동존은 김포/여의도 복수 선택 가능)"
                if config.multi_db.zone_group_exclusive
                else "(복수 선택 가능 — 전체는 모두 선택. 함께 선택하면 은행존을 먼저 조회한 뒤 공동존을 조회합니다)"  # noqa: E501
            )
        ),
        has_file=True,
        group_exclusive=config.multi_db.zone_group_exclusive,
    )


def _zone_clarification_or_none(
    body: QueryRequest,
    checkpoint_state: dict[str, Any] | None,
    config: Any,
    current_user: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """존 선택 역질문이 필요하면 clarification 컨텍스트를, 아니면 None을 반환한다."""
    # 존 그룹 상호배타(D-143 후속3) — 혼합 선택·혼합 텍스트는 턴 유형 무관 최우선 발동
    exclusive = _zone_group_exclusive_or_none(
        body.query or "", body.selected_db_ids, config, current_user=current_user
    )
    if exclusive:
        return exclusive
    if body.selected_db_ids:
        return None  # 선택 재개 턴 — 게이트 통과
    query = body.query or ""
    # 미등록 존 지목(plans/108 CU-B2 · G-3)은 아래 좁히기 조건("모든/전체"·"서버"·위치어 해소)
    # 보다 **앞선다** — "판교존 서버 목록"은 대량 조회 표현이 아니라 종전 규칙에 걸리지 않았고,
    # 그 결과 지목이 조용히 버려졌다. 지목이 틀렸다는 사실 자체가 되물을 이유다.
    unregistered = _unregistered_zone_clarification_or_none(
        query, config, current_user=current_user
    )
    if unregistered:
        return unregistered
    placeholder = _ZONE_PLACEHOLDER in query
    if not placeholder:
        # 후속 턴: previous_entities/DB 승계 우선(§4.2 비발동). 단 스코프 칩 "해제"(reset_db_scope,
        # D-205)는 승계를 끊는 턴이므로 첫 턴 규칙으로 되돌린다 — 해제했는데 조용히 직전 존으로
        # 가면 해제가 거짓말이 된다. 자연 소진(스코프 없는 후속 턴)은 현행 유지(SPEC Open Q1).
        if checkpoint_state is not None and not getattr(body, "reset_db_scope", False):
            return None
        if not is_full_scan_query(query) or "서버" not in query:
            return None  # 존 단위 대량 조회 의도 아님 — 과잉 역질문 방지
        # 위치 표면어가 하나라도 해소되면 비발동 (D-065 결정적 보강이 처리)
        from src.utils.query_gen_common import LOCATION_HINT_TERMS, term_in_text
        if any(term_in_text(t, query) for t in LOCATION_HINT_TERMS):
            return None
        # 존 없는 소스만 이름으로 지목했으면 비발동(plans/132 N-10) — 그 소스에는 고를 존이 없다.
        from src.routing.db_scope import names_only_zoneless_sources
        if names_only_zoneless_sources(query):
            return None
        # 2단 다중 시스템이면 분류 뒤 task 게이트가 판정한다(plans/132 v1.5 후속 ①).
        if _zone_gate_deferred_to_tasks(config, "존 게이트"):
            return None
    # 페이로드 조립은 공용 헬퍼로(D-143 후속3 — 상호배타 시 안내 문구·그룹 렌더 일원화)
    return _authorized_zone_clarification(
        config,
        current_user,
        query,
        group_exclusive=config.multi_db.zone_group_exclusive,
    )
