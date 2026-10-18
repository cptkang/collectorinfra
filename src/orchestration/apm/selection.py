"""보기 선택 재시도 조각과 소스 선택 사다리 되묻기.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.orchestration.apm.common import APM_QUERY_AGENT, FLAT_VIEW_ARGS_KEY, GENERAL_AREAS
from src.routing import apm_source_select as source_select
from src.routing.registry import SourceSpec, ViewSpec

logger = logging.getLogger("src.orchestration.apm_query")


# ── 보기 선택 재시도 (plans/134 M-8 · SPEC §6.4) ───────────────────────────────

@dataclass
class _Selection:
    """이번 task 가 조회할 보기 · 조건 · 선택 경과(메타) · 되묻기 문구.

    `unresolved`는 조회하지 않을 때(덮은 영역 없음), `uncovered`는 덮은 보기는 조회하고 못 덮은
    영역만 되물을 때의 문구다.
    """

    views: list[str]
    view_args: dict[str, Any]
    info: dict[str, Any]
    unresolved: str = ""
    uncovered: str = ""


_SELECTION_SYSTEM = """너는 WAS·미들웨어(APM) 조회 보기 선택기다. 아래 「보기 표」에서만 고른다.

- 사용자가 요청한 정보(영역): <areas>
- 그 정보를 답하는 보기 id 를 `views`에 넣는다. 표에 없는 id 는 쓰지 않는다.
- 맞는 보기가 없으면 `views`를 비운다 — 다른 정보를 보여 주는 보기(응답시간·목록 등)로 대신하지 않는다.
- 보기에 「조건(view_args)」이 있으면 사용자가 말한 조건만 `view_args`에 넣는다(보기 id → 조건 이름 → 값).
- 보기 표 「예」의 값은 형식을 보여 주는 예일 뿐이다 — 질문(사용자 원문 포함)에 없는 값은 `view_args`에 넣지 않는다.
- 기간·시간(「최근 3시간」·「오늘」)은 `view_args`가 아니다 — 조건 이름은 표의 「조건(view_args)」에 있는 것만 쓴다.
- 앞 결과의 몇 번째 행(「두 번째 트랜잭션」)을 가리키면 그 보기의 `ref`에 번호(1부터)를 넣는다. 번호를 말하지 않았으면 넣지 않는다.
- JSON 객체 하나만 출력한다: {"views": ["<보기 id>"], "view_args": {"<보기 id>": {"<조건 이름>": 값}}}

## 보기 표
<rows>
"""  # noqa: E501


def _uncovered(views: list[str], areas: list[str], by_id: dict[str, ViewSpec]) -> list[str]:
    """고른 보기들이 덮지 못하는 영역 — 보기가 없으면 일반 현황 밖 영역(SPEC §6.4 ①)."""
    if not views:
        return [a for a in areas if a not in GENERAL_AREAS]
    covered = {by_id[v].capability for v in views if v in by_id}
    return [a for a in areas if a not in covered]


def _selected_args(views: list[str], planned: dict[str, Any], retried: dict[str, Any],
                   planned_views: list[str]) -> dict[str, Any]:
    """조회할 보기의 조건 묶음 — 분해가 고른 보기는 분해 조건만, 재시도가 더한 보기는 재시도 조건.

    재시도 조건이 분해가 고른 보기의 조건을 덮지 않는다(V-2 — 재시도가 보기 표 예문 값으로 분해가
    비운 필수 조건을 채워 되묻기를 건너뛰었다). 보기 id 없는 평면 묶음(`FLAT_VIEW_ARGS_KEY`)도
    처리기까지 옮긴다(W1 검증 L-1) — 재시도 것은 분해가 고른 보기가 없을 때만 쓴다.
    """
    keep = {*views, FLAT_VIEW_ARGS_KEY}
    by_plan = set(planned_views)
    fresh = {k: v for k, v in retried.items() if k in views and k not in by_plan}
    if FLAT_VIEW_ARGS_KEY in retried and not by_plan & set(views):
        fresh[FLAT_VIEW_ARGS_KEY] = retried[FLAT_VIEW_ARGS_KEY]
    return {**{k: v for k, v in planned.items() if k in keep}, **fresh}


# ── 소스 선택 사다리 (plans/147 · D-322) ───────────────────────────────────────

#: 소스 되묻기 페이로드 — 처리기 결과 키 = 상태·응답 키(집계기가 상태로 옮긴다).
SOURCE_CLARIFICATION_KEY = "apm_source_clarification"
#: 결과 행의 소스 표시 칸(레지스트리 라벨 · 모르는 id는 원문 — 행의 `source_id` 원값은 그대로).
SOURCE_LABEL_KEY = "source_label"


def _source_clarification(decision: source_select.SourceDecision, specs: tuple[SourceSpec, ...],
                          isolated: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """소스 되묻기 페이로드 — 선택지(키 = 소스 id · 「전체」 = `ALL_SOURCES`) · 복수 선택 가능."""
    by_id = {spec.id: spec for spec in specs}
    return {
        "question": decision.question,
        "options": [{"key": i, "label": source_select.source_label(i, specs),
                     "zone": by_id[i].zone if i in by_id else ""} for i in decision.choices],
        "allow_all": source_select.ALL_SOURCES in decision.choices,
        "multi": True,
        # 버튼 답은 같은 원 질문을 선택과 함께 다시 보낸다(글 답 턴에도 원 질문이 남는다)
        "original_query": str(isolated.get("original_user_query") or task.get("sub_query") or ""),
    }


async def _audit_source_ask(isolated: dict[str, Any], option_count: int) -> None:
    """소스 되묻기 발동을 감사에 남긴다(되묻기율 — 기존 어휘 `clarification_issued`)."""
    from src.security.audit_logger import log_clarification

    try:
        await log_clarification(kind="apm_source", axis="apm_source", option_count=option_count,
                                user_id=isolated.get("user_id"),
                                thread_id=isolated.get("thread_id"))
    except Exception as e:  # noqa: BLE001 — 감사 실패는 경고로 남기고 진행한다
        logger.warning("%s 소스 되묻기 감사 기록 실패: %s", APM_QUERY_AGENT, e)
