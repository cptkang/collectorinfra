"""선택 인가 판정 — DB·소스·APM 소스 선택 인가 · 사용자 신원 갱신 · APM 소스 글 답 해석.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.routing.db_authz import filter_selected_db_ids

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


def _with_current_identity(
    delta: dict[str, Any], current_user: dict[str, Any]
) -> dict[str, Any]:
    """후속·승인 턴 델타에 **이번 요청 토큰의 인가 정보**를 다시 싣는다(plans/104 C-2).

    체크포인터는 델타만 병합하므로, 재주입이 없으면 스레드 첫 턴의 `user_role`·
    `allowed_db_ids`가 그대로 승계된다 — 관리자에서 강등된 사용자가 기존 스레드에서는
    계속 관리자 전용 동작(채팅 캐시 생성·무효화 — plans/104 S2)을 할 수 있었다.
    권한은 매 요청 DB에서 최신값을 읽으므로(D-069 `require_admin_user`·`require_user`)
    그 값을 턴마다 덮어쓰면 강등·권한 변경이 즉시 반영된다.
    """
    delta["user_role"] = current_user.get("role")
    delta["allowed_db_ids"] = current_user.get("allowed_db_ids")
    delta["allowed_sources"] = current_user.get("allowed_sources")  # plans/125 A-7
    return delta


def _parse_selected_db_ids_form(raw: str | None) -> list[str] | None:
    """multipart Form의 selected_db_ids(CSV)를 목록으로 변환한다."""
    if not raw:
        return None
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    return ids or None


def apply_selection_authorization(
    selected_db_ids: list[str] | None, current_user: dict[str, Any]
) -> tuple[list[str] | None, bool]:
    """요청이 지정한 DB 선택에 인가를 적용한다(plans/104 C-4 · D-232 — 요청 경계 단일 지점).

    선택 값은 **외부 입력**이라 라우터 반환값 필터로는 막히지 않는다. 그대로 상태에 실리면
    존 선택 재개 턴의 task 고정이 그 DB로 조회를 확정한다(순차 러너는 3단 기본 경로에서도
    도달한다). 네 진입(텍스트·스트림·파일 2종)이 모두 이 함수를 지난다.

    Returns:
        `(상태에 실을 선택, 전부 비인가 여부)` — 전부 비인가면 호출부가 사유를 응답하고 멈춘다.
    """
    authorized, dropped = filter_selected_db_ids(
        selected_db_ids, current_user.get("allowed_db_ids"), current_user.get("role")
    )
    if dropped and not authorized:
        logger.info("DB 선택 전량 비인가 — 조회하지 않고 사유 반환: %s", dropped)
        return None, True
    return authorized, False


def apply_source_selection_authorization(
    selected_sources: list[str] | None, current_user: dict[str, Any]
) -> list[str] | None:
    """소스 선택 칩 답변(plans/132 N-10)에 인가를 적용한다 — 외부 입력이라 요청 경계에서 거른다.

    레지스트리에 없는 코드는 버린다. 비DB 소스는 관측 소스 인가(`allowed_sources` · D-264 ②)를
    지나야 한다. DB 소스는 함께 오는 `selected_db_ids`가 DB 인가
    (`apply_selection_authorization`)를 지난다. 전부 걸러지면 None(선택 없음 — 종전 턴 규칙)이다.
    """
    if not selected_sources:
        return None
    from src.routing.db_authz import is_source_allowed
    from src.routing.registry import get_registry

    reg = get_registry()
    known = {spec.code for spec in reg.solutions()} | {reg.system_of(d) or d for d in reg.db_ids()}
    kept = [
        s for s in dict.fromkeys(str(x) for x in selected_sources if x)
        if s in known and (
            not reg.is_non_db_system(s)
            or is_source_allowed(s, current_user.get("allowed_sources"), current_user.get("role"))
        )
    ]
    if len(kept) != len(selected_sources):
        logger.info("소스 선택 인가·검증으로 제외: %s → %s", selected_sources, kept)
    return kept or None


def apply_apm_source_selection_authorization(
    selected: list[str] | None, current_user: dict[str, Any]
) -> list[str] | None:
    """제니퍼 소스 선택(plans/147 · D-322)을 요청 경계에서 거른다 — 외부 입력이다.

    레지스트리 소스(`solutions[apm].sources`) ∩ 사용 가능 소스(기동 정합 점검 · 없으면 전부)만
    남기고
    「전체」(`ALL_SOURCES`)는 허용한다. 소스 단위 권한은 없다 — 관측 소스 `apm` 권한이 없으면 전부
    버린다(D-287 ⑤ · 처리기도 실행 경계에서 거부한다). 전부 걸러지면 None(선택 없음 — 사다리가
    다음 단으로 정한다).
    """
    if not selected:
        return None
    from src.routing.apm_source_select import ALL_SOURCES, get_available_apm_sources
    from src.routing.db_authz import is_source_allowed
    from src.routing.registry import get_registry

    if not is_source_allowed("apm", current_user.get("allowed_sources"), current_user.get("role")):
        logger.info("제니퍼 소스 선택 제외(관측 소스 권한 없음): %d건 %s", len(selected),
                    _apm_ids_preview(selected))
        return None
    available = get_available_apm_sources()
    known = {
        spec.id for spec in get_registry().sources_of("apm")
        if available is None or spec.id in available
    }
    # 상한(심층 방어) — 넘는 개수·긴 항목은 거부(422)하지 않고 버린다(선택 칸 관례: 거르기)
    given = list(dict.fromkeys(
        s for s in (str(x) for x in selected[:_APM_IDS_MAX] if x) if len(s) <= _APM_ID_MAX_LEN))
    kept = [s for s in given if s == ALL_SOURCES or s in known]
    if len(kept) != len(selected):
        logger.info("제니퍼 소스 선택 검증으로 제외: %d건 %s → %s", len(selected),
                    _apm_ids_preview(selected), kept)
    return kept or None


#: 제니퍼 소스 선택 상한 — 개수 · 항목 길이(레지스트리 소스 id는 짧은 슬러그다)
_APM_IDS_MAX = 32
_APM_ID_MAX_LEN = 64


def _apm_ids_preview(ids: list[Any], head: int = 3, width: int = 20) -> list[str]:
    """로그용 — 외부 입력 목록의 앞 몇 개만, 각 항목은 잘라서."""
    return [str(x)[:width] for x in ids[:head]]


#: 소스 되묻기 글 답에 붙어도 되는 꼬리(plans/147 — 조사·맺음말·문장부호만 · 좁은 목록).
#: 이 밖의 말이 섞이면 새 질문이다.
_APM_ANSWER_TAIL = re.compile(
    r"(?:조회해\s*주세요|조회해\s*줘|조회해요|조회|해\s*주세요|해\s*줘|해요|부탁해요|부탁합니다"
    r"|부탁해|주세요|제니퍼|으로요|으로|로요|로|이요|만요|만|요|은|는|이|가|을|를|도|과|와|랑"
    r"|[\s.,!?~·/])*"
)
#: 글 답 길이 상한 — 소스 단어 몇 개 + 꼬리를 넘는 문장은 새 질문으로 본다.
_APM_ANSWER_MAX = 40


def _apm_source_text_answer(
    query: str, pending: Any, current_user: dict[str, Any]
) -> tuple[list[str] | None, str | None] | None:
    """직전 턴 소스 되묻기에 글로 답했으면 (선택 id, 다시 물을 답 문구), 아니면 None(plans/147).

    조건: 체크포인트에 되묻기 대기(`apm_source_pending`)가 있고, 이번 턴 원문이 소스 단어·「전체」와
    좁은 꼬리(`_APM_ANSWER_TAIL`)뿐이다(결정적 · 존 역질문 Q-2 「같은 원 질의 + 선택뿐」과 같은 결).
    - 「전체」 단독 → `[ALL_SOURCES]` — 소스 단어가 함께 있으면(「공동존 전체」) 「전체」는
      「그 소스 전부」로 읽고 무시한다(전 소스로 넓히지 않는다 · D-290 ⑥)
    - 걸린 소스 1개 → `[그 id]`
    - 2개 이상(「은행존」) → `(None, 원문)` — 사다리가 이 답 문구만 단어로 보고 다시 되묻는다.
    관측 소스 `apm` 권한이 없으면 None(새 질문 — 처리기가 거부한다). 미연결 여부는 사다리가
    판정한다.
    """
    from src.routing.apm_source_select import ALL_LABEL, ALL_SOURCES, match_source_terms
    from src.routing.db_authz import is_source_allowed
    from src.routing.registry import get_registry
    from src.utils.query_gen_common import term_spans

    if not isinstance(pending, dict) or not pending.get("query"):
        return None
    text = (query or "").strip()
    if not text or len(text) > _APM_ANSWER_MAX:
        return None
    if not is_source_allowed("apm", current_user.get("allowed_sources"), current_user.get("role")):
        return None
    specs = get_registry().sources_of("apm")
    spans = sorted(
        span for term in {*(t for spec in specs for t in spec.terms), ALL_LABEL}
        for span in term_spans(term, text)
    )
    if not spans:
        return None
    pieces, cursor = [], 0
    for start, end in spans:
        if start > cursor:
            pieces.append(text[cursor:start])
        cursor = max(cursor, end)
    pieces.append(text[cursor:])
    if not all(_APM_ANSWER_TAIL.fullmatch(p) for p in pieces):
        return None
    matched = match_source_terms(text, specs).source_ids
    if not matched:
        return ([ALL_SOURCES], None) if term_spans(ALL_LABEL, text) else None
    if len(matched) == 1:
        return [matched[0]], None
    return None, text
