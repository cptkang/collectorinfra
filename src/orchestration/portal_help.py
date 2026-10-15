"""포탈 기능 도움말 조건부 처리기 `portal_help` — plans/142 §5 W3 · D-315.

`src/doc_qa/portal_help.py`(W2 — 매뉴얼 색인 검색·답변 서비스·되돌림 판정)를 1·2단이 부르게 하는
**배선**이다. 서비스는 바꾸지 않는다.

- **활성**(`portal_active`): `PORTAL_HELP_ENABLED`(기본 on — G-7 (b) 예외 · config 주석) ∧ 매뉴얼
  색인 성립. 색인이 없거나 손상이면 켜도 비활성이고 기동 로그 1줄로 사유를 남긴다(침묵 금지). 비활성
  배포는 처리기도 분해 프롬프트 줄도 1단 도구도 없다 — 바이트 불변.
- **데이터 소스가 아니다**: 레지스트리 항목(`solutions[portal]` · `data_source: false`)은 명시 명령
  별칭의 정본일 뿐이다. 소스 권한(`allowed_sources`)으로 막지 않는다 — 매뉴얼은 무인증 공개(D-252 ①)
  이고, 역할 필터(일반 사용자 = 사용자 매뉴얼 · G-2)는 답변 서비스가 한다.
- **진입 넷**: 분해 LLM 처리기 목록(계층 B) · 명시 명령 단락 ③.9(`parse_portal_command`) ·
  `general_inference` 되돌림(G-5 (b) — 분해 출구 `coerce_portal_redirect` · 1·2단 수렴 handler
  `redirect_general_inference`) · 1단 도구(G-8 (b) — `deepagents_tools.build_tools`).
- **폴백 없음**: 실패를 일반 LLM 답(`general_inference`)으로 넘기지 않는다 — 서비스가 사유 문구와
  관련 매뉴얼 절 링크를 들고 돌아온다.
- **간선 없음**: 매뉴얼 답은 다른 task 의 입력이 되지 않는다(`key_facets_out=()`).

계층: orchestration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig
from src.doc_qa.authz import _is_admin
from src.doc_qa.portal_help import (
    STATUS_ERROR,
    PortalRedirectMatch,
    answer_portal_help,
    help_index_status,
    portal_redirect_match,
    strip_screen_terms,
)
from src.llm import create_llm
from src.orchestration.doc_command import parse_designated_command
from src.orchestration.subagents import SubAgentSpec
from src.routing.registry import get_registry
from src.utils.help_question import (
    compact_text,
    has_data_request_cue,
    is_method_or_concept_question,
)
from src.utils.task_result_keys import DEGRADED_KEY
from src.utils.usage_query import is_usage_query

logger = logging.getLogger(__name__)

#: 처리기 이름(분해 어휘 — 활성일 때만 렌더) · 레지스트리 안내 항목 코드(`solutions[portal]`).
PORTAL_HELP_AGENT = "portal_help"
PORTAL_SYSTEM = "portal"
#: 1단 도구 이름(동사+목적어 — `deepagents_tools._TOOL_NAMES` 규칙).
PORTAL_TOOL_NAME = "answer_portal_help"
#: 계획 경로 표지(`intent_planner` 계획 요약·로그) — 명시 명령 ③.9 · 분해 출구 되돌림(G-5).
PORTAL_COMMAND_PATH = "portal_command"
PORTAL_REDIRECT_PATH = "portal_redirect"
#: 결과 메타 키(실패 사유 키 `DEGRADED_KEY`는 `src.utils.task_result_keys` 정본).
META_KEY = "portal_help"
#: 되돌림 대상 담당(G-5 (b) — W0 실측: 문항 1은 분해가 `cache_management`로 보냈다).
REDIRECTABLE_AGENTS = frozenset({"general_inference", "cache_management"})
#: 명시 명령 동사 어간(③.9 — 설명·검색). 「조회」는 넣지 않는다 — 데이터 조회 명령과 겹친다.
_COMMAND_VERBS = ("검색", "찾아", "찾기", "알려", "설명")
#: 설명 동사 — 문서를 지칭하지 않는 별칭(「AIOps 포탈」)도 이 동사면 명령으로 본다(V-1).
_EXPLAIN_VERB = "설명"
#: 별칭이 매뉴얼·도움말(문서)을 지칭하는지 가르는 낱말(V-1 — 별칭 하드코딩 없이 결정적으로 가른다).
_DOC_ALIAS_WORDS = ("매뉴얼", "도움말")
#: 비활성 명시 명령 안내(LLM 0 — 사용자가 쓴 지목 표현만 싣는다 · D-264).
_INACTIVE_NOTICE = (
    "요청하신 「{hint}」 도움말은 지금 쓸 수 없어 답하지 않았습니다. "
    "사용자 매뉴얼(/manual/user)에서 직접 보실 수 있습니다."
)


# ── 활성 ──────────────────────────────────────────────────────────────────────

def portal_enabled(app_config: Any) -> bool:
    """설정 스위치(`PORTAL_HELP_ENABLED`).

    `is True`로 판정한다 — MagicMock 설정(테스트)의 속성이 참으로 평가돼 켜진 것처럼 보이지 않게.
    """
    return getattr(getattr(app_config, "portal_help", None), "enabled", False) is True


def portal_active(app_config: Any) -> bool:
    """처리기가 활성인가(스위치 ∧ 색인 성립).

    색인 적재는 프로세스 1회 캐시다(값이 흔들리지 않는다).
    """
    return portal_enabled(app_config) and help_index_status()[0]


def log_portal_help_startup(app_config: Any) -> None:
    """기동 시 확정값 1줄(그래프 빌드 끝 — 사다리 로그와 나란히). 비활성이면 사유를 남긴다."""
    if not portal_enabled(app_config):
        logger.info("포탈 도움말(plans/142) 비활성 — PORTAL_HELP_ENABLED=false")
        return
    ok, reason = help_index_status()
    if ok:
        logger.info("포탈 도움말(plans/142) 활성 — 매뉴얼 색인 %s", reason)
    else:
        logger.warning("포탈 도움말(plans/142) 비활성 — 매뉴얼 색인을 쓸 수 없음: %s", reason)


def render_agent_line() -> str:
    """분해 프롬프트의 담당 목록 한 줄(활성일 때만 삽입)."""
    return f"- **{PORTAL_HELP_AGENT}**: {PORTAL_HELP_SPEC.purpose}"


# ── 처리기 ────────────────────────────────────────────────────────────────────

def _answer_llm(app_config: Any, *, fallback: Any) -> Any:
    """매뉴얼 근거 서술 LLM — answer 프로파일(D-194 · `doc_query._answer_llm`과 같은 방식).

    만들지 못하면 주입 LLM으로 서술하고 사유를 남긴다.
    """
    try:
        return create_llm(app_config, purpose="answer")
    except Exception as e:  # noqa: BLE001 — 서술 LLM 생성 실패가 매뉴얼 답을 막지 않는다(사유 로그)
        logger.warning("%s answer 프로파일 LLM 생성 실패 — 주입 LLM으로 서술: %s: %s",
                       PORTAL_HELP_AGENT, type(e).__name__, e)
        return fallback


async def run_portal_help(
    task: dict[str, Any],
    isolated: dict[str, Any],
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict[str, Any]:
    """매뉴얼 근거로 포탈 기능 질문에 답한다(handler 규약 · LLM 은 서비스의 서술 1회뿐).

    소스 권한(`allowed_sources`)은 보지 않는다 — 매뉴얼은 데이터 소스가 아니다(모듈 독스트링).

    Returns:
        텍스트 결과 `{final_response, portal_help, source_status}` — 서비스가 `error`로 끝나면
        `error`·`degraded_reason`을 더한다(사유 문구·관련 절 링크는 서비스가 만든다).
    """
    query = str(task.get("sub_query") or isolated.get("user_query") or "")
    # 관리자 판정 키(`authz._is_admin` — role·type·roles)로 격리 입력의 신원을 옮긴다
    user = {"role": isolated.get("user_role"), "sub": isolated.get("user_id")}
    result = await answer_portal_help(query, user=user,
                                      llm=_answer_llm(app_config, fallback=llm))
    logger.info("%s status=%s 근거=%s", PORTAL_HELP_AGENT, result.status, result.section_ids)
    meta = {
        "status": result.status,
        "sections": list(result.section_ids),
        "reason": result.reason,
        "llm_calls": result.diagnostics.get("llm_calls", 0),
    }
    out: dict[str, Any] = {
        "final_response": result.answer,
        META_KEY: meta,
        "source_status": [{
            "system": PORTAL_SYSTEM,
            "label": get_registry().system_label(PORTAL_SYSTEM),
            "status": result.status,
            "rows": len(result.section_ids),
            "reason": result.reason,
        }],
    }
    if result.status == STATUS_ERROR:
        out["error"] = result.reason or result.answer
        out[DEGRADED_KEY] = f"portal_help_{result.status}"
    return out


PORTAL_HELP_SPEC = SubAgentSpec(
    PORTAL_HELP_AGENT,
    "AIOps 포탈(이 시스템)의 기능·화면 용어·사용 방법 질문에 사용자·관리자 매뉴얼 근거로 답한다"
    "(데이터 조회가 아님)",
    run_portal_help,
    purpose=(
        "이 포탈(AIOps)의 **기능·화면 용어·사용 방법** 질문 — 사용자·관리자 매뉴얼 근거로 답한다"
        "(데이터 조회가 아님)"
    ),
    backend="none",  # 프로세스 안 색인(`src/static/manual/help_index.json`)
    output_type="text",
    key_facets_out=(),  # 간선 없음 — 다른 task 의 입력이 되지 않는다
    prerequisites=("portal_help_index",),
)


def active_portal_subagents(app_config: Any) -> dict[str, SubAgentSpec]:
    """활성이면 `{portal_help}` · 아니면 빈 dict."""
    return {PORTAL_HELP_AGENT: PORTAL_HELP_SPEC} if portal_active(app_config) else {}


# ── 명시 명령 ③.9 ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PortalCommand:
    """포탈 도움말 명령 판정 결과(판정되면 `intent_planner`가 계획을 만든다)."""

    #: 사용자가 쓴 지목 표현(안내 문구용 — D-264)
    hint: str
    #: 처리기 활성 여부(비활성이면 조회하지 않고 안내만)
    active: bool
    #: 매뉴얼 검색 질의(지목 구간·명령 꼬리 제거)
    query: str


def portal_aliases() -> tuple[str, ...]:
    """포탈 별칭(정본 레지스트리 `solutions[portal].aliases` — G-4)."""
    for spec in get_registry().help_systems():
        if spec.code == PORTAL_SYSTEM:
            return tuple(a for a in spec.aliases if a)
    return ()


def parse_portal_command(user_query: str, app_config: Any) -> PortalCommand | None:
    """원문이 포탈 도움말 명령이면 판정 결과, 아니면 None(종전 분해로 간다).

    판정은 문서 검색 명령(D-307)과 같은 공용 함수다(등록 별칭 + 지목 조사 + 동사 — 설명·검색·
    찾아·찾기·알려). 다음이면 판정하지 않는다(보류 — 종전 분해로 간다):

    - 매뉴얼·도움말을 지칭하지 않는 별칭(「AIOps 포탈」)으로 지목했는데 남은 질의가 사용법·개념
      의문이 아니고 동사도 「설명」이 아니다(V-1 — 「AIOps 포탈에서 활성 알람을 알려줘」는 데이터
      조회다).
    - 남은 질의에 데이터 요청 표지(건수·목록·상위·오늘 …)가 있다. 따옴표 구간·색인 용어(화면
      용어)는 빼고 본다(V-3 — 「'사용자 목록'을 설명해줘」는 포탈 질문이다).
    """
    command = parse_designated_command(user_query, portal_aliases(), verbs=_COMMAND_VERBS,
                                       label="포탈 도움말")
    if command is None:
        return None
    if (not any(w in compact_text(command.hint) for w in _DOC_ALIAS_WORDS)
            and not is_method_or_concept_question(command.query)
            and _EXPLAIN_VERB not in compact_text(user_query)):
        logger.info("포탈 도움말 명령 보류 — 문서 지칭 없는 별칭 · 사용법 의문 아님(종전 분해): %r",
                    command.query)
        return None
    if has_data_request_cue(strip_screen_terms(command.query)):
        logger.info("포탈 도움말 명령 보류 — 데이터 요청 표지(종전 분해): %r", command.query)
        return None
    return PortalCommand(hint=command.hint, active=portal_active(app_config), query=command.query)


def inactive_notice(hint: str) -> str:
    """비활성 명시 명령 안내(결정적 · LLM 0)."""
    return _INACTIVE_NOTICE.format(hint=hint)


# ── 되돌림 (G-5 (b) — D-004 부기 변경 · 사용자 승인 2026-10-13) ────────────────

def portal_redirect(
    query: str, app_config: Any, *, user_role: str | None = None,
) -> PortalRedirectMatch | None:
    """`query`를 매뉴얼로 되돌릴지 — 활성 ∧ 사용법 단락(③.8) 질의 아님 ∧ 서비스 판정 성립.

    매칭은 **호출자 역할**로 한다(V-2 — 관리자 = 두 권 · 그 밖 = 사용자 매뉴얼만). 역할을 모르면
    (None) 사용자 매뉴얼만 본다(fail-closed · 좁은 쪽) — 일반 사용자의 일반 IT 질문이 관리자 매뉴얼
    용어에 끌려 「관리자 기능」 안내로 끝나지 않게. 관리자 판정은 `authz._is_admin`과 같다.
    """
    if not portal_active(app_config) or not query or is_usage_query(query):
        return None
    return portal_redirect_match(query, is_admin=_is_admin({"role": user_role}))


def coerce_portal_redirect(
    tasks: list[dict[str, Any]], user_query: str, app_config: Any,
    *, user_role: str | None = None,
) -> PortalRedirectMatch | None:
    """분해 출구 결정적 교정(in-place · 2단) — 성립하면 근거, 아니면 None(계획 불변).

    **단일 task** 계획이고 담당이 `general_inference`·`cache_management`이며 고정 안내
    (`direct_response`)가 아니고 원문이 되돌림 판정을 통과할 때만 그 task를
    `portal_help`(질의 = 원문)로 바꾼다. 다중 task 계획·조회 담당 task 는 건드리지 않는다.
    `user_role`은 상태의 역할(없으면 사용자 매뉴얼만 — `portal_redirect`).
    """
    items = [t for t in tasks if isinstance(t, dict)]
    if len(items) != 1:
        return None
    task = items[0]
    if task.get("agent") not in REDIRECTABLE_AGENTS or task.get("direct_response"):
        return None
    match = portal_redirect(user_query, app_config, user_role=user_role)
    if match is None:
        return None
    logger.info("intent_planner: 포탈 도움말 되돌림(G-5) %s → %s 용어=%r 절=%s:%s 점수=%.3f",
                task.get("agent"), PORTAL_HELP_AGENT, match.term, match.manual, match.section_id,
                match.score)
    task["agent"] = PORTAL_HELP_AGENT
    task["sub_query"] = user_query
    task.pop("views", None)
    return match


def coerce_portal_data_request(
    tasks: list[dict[str, Any]], user_query: str,
) -> list[dict[str, Any]]:
    """분해 출구 결정적 교정(in-place · 2단) — 데이터 요청을 `portal_help`로 보낸 task 를 되돌린다.

    분해 LLM 이 낸 `portal_help` task 에 데이터 요청 표지(건수·목록·상위·오늘 …)가 있으면 그 task 를
    `data_query`로 바꾼다. 따옴표 구간·색인 용어(화면 용어)는 빼고 본다(V-3 — ③.9와 같은 함수).
    단일 task 계획은 sub_query 또는 원문으로, 다중 task 계획은 그 task 의
    sub_query 로만 판정한다(혼합 질의의 원문 표지가 포탈 질문 task 까지 뒤집지 않게).
    알람·프로세스 교정(`_coerce_alarm_intent` 등)보다 **앞**에서 불러 알람 신호가 있으면 그
    교정이 `alarm_query`로 마저 바꾸게 한다
    (W5 실측 — 「오늘 수신 알람 몇 건이야」를 분해가 `portal_help`로 보냈다). 비활성 배포는
    분해 어휘에 `portal_help`가 없어 no-op 이다. 명시 명령 ③.9 는 분해를 거치지 않아 대상 밖이다.
    """
    single = len([t for t in tasks if isinstance(t, dict)]) == 1
    for task in tasks:
        if not isinstance(task, dict) or task.get("agent") != PORTAL_HELP_AGENT:
            continue
        sub_query = str(task.get("sub_query") or "")
        if not (has_data_request_cue(strip_screen_terms(sub_query))
                or (single and has_data_request_cue(strip_screen_terms(user_query)))):
            continue
        task["agent"] = "data_query"
        logger.info("intent_planner: 포탈 도움말 데이터 요청 교정 — %s→data_query (sub_query=%r)",
                    PORTAL_HELP_AGENT, sub_query)
    return tasks


async def redirect_general_inference(
    task: dict[str, Any],
    isolated: dict[str, Any],
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict[str, Any] | None:
    """1·2단 수렴점(`run_general_inference`)의 되돌림 — 성립하면 매뉴얼 답, 아니면 None.

    고정 안내 task(`direct_response`)는 호출부가 먼저 거른다. 1단 `general_answer` 도구도 이
    handler 를 탄다(G-8 (b)와 함께 1·2단 대칭 — D-038). 원문(`original_user_query`)이 사용법
    단락(③.8) 질의면 되돌리지 않는다(`general_inference`와 같은 기준 — V-6). 매칭은 격리 입력의
    `user_role`로 한다(V-2 — `run_portal_help`의 신원과 같은 키).
    """
    if is_usage_query(str(isolated.get("original_user_query") or "")):
        return None
    query = str(task.get("sub_query") or isolated.get("user_query") or "")
    match = portal_redirect(query, app_config, user_role=isolated.get("user_role"))
    if match is None:
        return None
    logger.info("general_inference: 포탈 도움말 되돌림(G-5) 용어=%r 절=%s:%s 점수=%.3f",
                match.term, match.manual, match.section_id, match.score)
    return await run_portal_help({**task, "agent": PORTAL_HELP_AGENT, "sub_query": query},
                                 isolated, llm=llm, app_config=app_config)


__all__ = [
    "META_KEY",
    "PORTAL_COMMAND_PATH",
    "PORTAL_HELP_AGENT",
    "PORTAL_HELP_SPEC",
    "PORTAL_REDIRECT_PATH",
    "PORTAL_SYSTEM",
    "PORTAL_TOOL_NAME",
    "PortalCommand",
    "active_portal_subagents",
    "coerce_portal_data_request",
    "coerce_portal_redirect",
    "inactive_notice",
    "log_portal_help_startup",
    "parse_portal_command",
    "portal_active",
    "portal_aliases",
    "portal_enabled",
    "portal_redirect",
    "redirect_general_inference",
    "render_agent_line",
    "run_portal_help",
]
