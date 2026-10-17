"""라우트 진입 원문 판정 — 턴 고지·비조회 입력 섀도·원문 시드.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
from typing import Any

from src.pipeline.turn.zone import _zone_display_label

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


def _turn_disclosures(
    query: str, selected_db_ids: list[str] | None
) -> list[Any] | None:
    """원문만으로 정해지는 턴 단위 고지(plans/123 W-4 · S-1) — 없으면 None.

    - W-4: 선택 존으로 조회하는 턴에 원문이 등록되지 않은 존을 지목했으면 바꿔 조회한 사실.
      역질문 턴에는 붙지 않는다(이 값은 파이프라인을 탄 턴에서만 렌더된다).
    - S-1: 사용자 값의 단위가 지표에 비해 이상하면(「메모리 64MB 이상」) 알린다. 조회는 요청
      그대로 한다(D-264 ⑤ — 사용자 값은 고치지 않는다).
    렌더는 집계기(1·2단 — 턴당 한 번)와 3단 `output_generator`가 한다(W-9).
    """
    from src.domain import disclosure as disc
    from src.domain.input_guard import render_unit_suspect, unit_suspects
    from src.routing.db_scope import find_unregistered_zone_terms

    items: list[Any] = []  # `disclosure.Disclosure` 목록
    if selected_db_ids:
        unknown = find_unregistered_zone_terms(query)
        if unknown:
            named = ", ".join(f"'{t}'" for t in unknown)
            labels = ", ".join(_zone_display_label(d) for d in selected_db_ids)
            items.append(disc.make(
                disc.UNREGISTERED_ZONE,
                f"{named}은(는) 등록된 존이 아니어서 선택하신 {labels}(으)로 조회했습니다.",
            ))
    for suspect in unit_suspects(query):
        items.append(disc.make(disc.UNIT_SUSPECT, render_unit_suspect(suspect)))
    return items or None


def _shadow_input_guard(query: str, thread_id: str | None) -> None:
    """비조회 입력 판정 섀도(plans/123 S-2 · 123·G-8 (c)) — **응답은 바꾸지 않고 로그만** 남긴다.

    붙여넣은 SQL · 쓰기 요청 · 지시 무시 · 계정 요구 · 빈 입력을 좁은 결정적 규칙으로 판정한다.
    판정 위치는 존 앞단 게이트보다 앞(라우트 진입)이다 — on 전환 때 단락할 자리와 같다. 대조군
    과잉 판정 0을 run으로 확인한 뒤(run R5′) 안내 단락으로 켠다. 판정 실패는 질의를 막지 않는다.
    """
    try:
        from src.domain.input_guard import classify_non_query_input

        verdict = classify_non_query_input(query)
    except Exception as e:  # noqa: BLE001 — 섀도 판정은 질의 경로를 막지 않는다
        logger.warning("S-2 섀도 판정 실패(plans/123): %s", e)
        return
    if verdict is not None:
        logger.info(
            "S-2 섀도(plans/123): 비조회 입력 kind=%s matched=%r thread=%s — 응답 불변",
            verdict.kind, verdict.matched, thread_id,
        )


def _raw_query_seed(query: str, config: Any) -> str | None:
    """라우트 진입 원문을 상태에 실을지 정한다(plans/107 P-2 · G-7).

    ``INTENT_FRAME_ENABLED``일 때만 원문을 돌려준다 — 꺼져 있으면 None이라 상태·체크포인트에
    원문·표시문이 실리지 않는다(현행과 비트 동일). ``user_query``는 종전대로 존 표기 치환본이다.
    """
    ifc = getattr(config, "intent_frame", None)
    return query if ifc is not None and ifc.enabled is True else None
