"""사용자 화면에 낼 작업 실패 사유 판정 — 예외 원문은 가리고 고정 사유는 살린다(plans/163 S3+).

작업 실패 사유(`error`·`error_message`·`db_errors`)에는 코드가 보간 없이 만든 고정 사유
(「모든 DB 쿼리가 실패했습니다.」)와 예외 원문(DSN·SQL·경로·외부 응답이 섞인다 — CWE-209)이 함께
흐른다. 판별은 결정적이다(허용 목록):

- 고정 사유(`_FIXED_REASONS`)는 그대로 보인다.
- 고정 머리말 뒤에 SQL·예외가 보간되는 사유(`_PREFIX_REASONS` — 「SQL 실행 에러: {e}」)는 머리말만
  남긴다.
- 그 밖은 보일 수 없다 — 호출부가 고정 문구(`GENERIC_TASK_ERROR`)로 바꾸고 원문은 서버 로그에 둔다.

멀티 DB의 DB별 사유(`db_errors` — 존 커버리지 각주·작업 카드·전 DB 실패 요약)는 `db_error_text`가
따로 판별한다: 「DB '…' 실행 에러: {예외}」의 예외 보간부만 떼고, 검증 사유·비-SQL 산출 발췌
(「| LLM 산출 발췌: '…'」 — D-153 후속2 폐쇄망 자가 진단)·조회 마감 문구·고정 문구는 남긴다.

처리기가 사용자 문구로 지은 결과는 그대로 보인다(`user_error_text`의 `result` 인자):
- 거부·실패 결과(`degraded_reason` — 제니퍼·호스트 조사 `_refusal`·문서·포탈 도움말 처리기가
  `error`·`degraded_reason`으로 구조화한 사용자 문구 · 소스 되묻기 질문 포함)
- 선행 0건으로 실행하지 않은 결과(`skipped` — `prior_dependency.skip_result`의 결정적 안내)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import structlog

#: 보일 수 없는 사유 대신 쓰는 고정 문구.
GENERIC_TASK_ERROR = "작업 처리 중 오류가 발생했습니다"

#: 코드가 보간 없이 만드는 고정 사유 — 그대로 보인다.
_FIXED_REASONS: frozenset[str] = frozenset({
    "모든 DB 쿼리가 실패했습니다.",  # multi_db_executor — 전 DB 실패
    "data_insufficient",  # result_organizer — 데이터 부족 판정 표지
    "쿼리 타임아웃 초과. 쿼리를 최적화해주세요.",  # query_executor
    "subagent가 예상치 못한 값을 반환했습니다.",  # agent_orchestrator._normalize
    "결과 생성 실패",  # result_aggregator — 출력 생성 예외
})

#: 「머리말: 보간」 꼴 사유의 고정 머리말 — 뒤(SQL·검증 사유·예외)를 버리고 머리말만 보인다.
_PREFIX_REASONS: tuple[str, ...] = (
    "SQL 검증 실패",  # query_validator — 뒤에 검증 사유(생성 SQL 조각 포함)
    "SQL 실행 에러",  # query_executor — 뒤에 DB 예외
    "DB 연결 에러",  # query_executor — 뒤에 연결 예외
    "DB 스키마 조회 실패",  # schema_analyzer — 뒤에 예외
)

#: 처리기 거부·실패 결과의 구조화 사유 키(`apm_query`·`host_inspect`·`doc_query`·`portal_help`).
_DEGRADED_KEY = "degraded_reason"
#: 선행 0건 미실행 결과 표지(`prior_dependency.skip_result`).
_SKIPPED_KEY = "skipped"


def safe_error_text(raw: Any) -> str | None:
    """사유 중 화면에 보일 수 있는 부분 — 없으면 None.

    Args:
        raw: 작업 실패 사유(문자열이 아니어도 된다)

    Returns:
        고정 사유면 그대로, 고정 머리말 사유면 머리말만, 그 밖(예외 원문 등)은 None.
    """
    summary = _DB_SUMMARY_RE.match(str(raw or ""))
    if summary:
        return _db_summary_text(summary)
    text = " ".join(str(raw or "").split())
    if not text:
        return None
    if text in _FIXED_REASONS:
        return text
    for head in _PREFIX_REASONS:
        if text == head or text.startswith(f"{head}:"):
            return head
    return None


def user_error_text(raw: Any, *, result: Mapping[str, Any] | None = None) -> str:
    """화면에 보일 실패 사유 — 보일 수 없으면 고정 문구(`GENERIC_TASK_ERROR`).

    Args:
        raw: 작업 실패 사유
        result: 그 사유를 낸 작업 결과(있으면 처리기가 지은 사용자 문구인지 본다)
    """
    if raw and isinstance(result, Mapping) and (
        result.get(_DEGRADED_KEY) or result.get(_SKIPPED_KEY)
    ):
        return str(raw)
    return safe_error_text(raw) or GENERIC_TASK_ERROR


#: 검증 사유 중 예외 원문이 뒤따르는 머리말(`sql_validation` — 「SQL 파싱 에러: {e}」).
_VALIDATION_PREFIX = "SQL 파싱 에러"
#: 검증 사유에 붙는 LLM 백엔드 응답 원문 발췌의 구분자(`sql_validation.detect_llm_backend_error`).
_BACKEND_EXCERPT_SEP = " | 응답 원문:"


def validation_reason_text(reason: Any) -> str:
    """화면에 보일 SQL 검증 사유 — 예외 원문·백엔드 응답 원문을 뗀다.

    검증 사유는 대부분 코드가 지은 문구라 그대로 보이고, 파서 예외(「SQL 파싱 에러: {e}」)는
    머리말만, 백엔드 오류 응답 판정 사유는 응답 원문 발췌 앞까지만 보인다.

    Args:
        reason: `validation_result.reason`
    """
    text = str(reason or "")
    if text.startswith(_VALIDATION_PREFIX):
        return _VALIDATION_PREFIX
    return text.split(_BACKEND_EXCERPT_SEP, 1)[0]


#: 비-SQL 산출 발췌 구분자(`multi_db_executor` · D-153 후속2) —
#: 「SQL 검증 실패: … | LLM 산출 발췌: '…'」.
_EXCERPT_SEP = " | LLM 산출 발췌: "
#: 검증 실패 DB 사유의 머리말(`multi_db_executor` — 뒤는 검증 사유).
_DB_VALIDATION_HEAD = "SQL 검증 실패: "
#: 조회 마감 사용자 문구의 시작(`query_validator.deadline_stop_message`) — 사유 이음 분할용.
_DEADLINE_HEAD = "답변할 시간을 남기려고"
#: 조회 마감 사용자 문구 전체 꼴(`deadline_stop_message` — 단계 3종 · 사유 2종 ·
#: 꼬리는 고정 사유·머리말).
#: 머리만 맞는 문자열의 뒤 원문이 따라 나오지 않게 전체를 맞춘다.
_DEADLINE_RE = re.compile(
    r"답변할 시간을 남기려고 (?:스키마 분석|SQL 생성|SQL 재생성)을\(를\) 하지 않았습니다"
    r"\((?:조회 마감 경과|조회 마감까지 \d+초 < 직전 SQL 생성 \d+초)\)\."
    r"(?: 마지막 실패 사유: (?P<reason>.+))?"
)
#: 테이블 선별 0개 안내의 시작(`table_selection.SELECTION_NONE_MESSAGE` — 일치는 테스트가 지킨다).
_SELECTION_NONE_HEAD = "질문에 맞는 조회 대상 테이블을 찾지 못했습니다"
#: 테이블 선별 0개 안내 전체 꼴(`table_selection.selection_none_guidance` —
#: 업무 영역 예시는 괄호 없이).
_SELECTION_NONE_RE = re.compile(
    rf"{_SELECTION_NONE_HEAD} — 무엇을 찾는지 알려 주세요(?:\(예: [^()]*\))?"
)
#: 코드가 지은 DB별 고정 사유(`multi_db_executor` — 미등록 DB · 테이블 없음).
_DB_FIXED_RE = re.compile(
    r"DB '[^']*'(?:이\(가\) 레지스트리에 등록되지 않았습니다|에서 테이블을 찾을 수 없습니다)\."
)
#: 실행 오류 DB 사유(`multi_db_executor` — 「DB '{id}' 실행 에러: {예외}[ | 재생성 경위]」).
_DB_EXEC_RE = re.compile(r"(DB '[^']*' 실행 에러)(.*)")
#: 실행 오류 뒤에 붙는 재생성 경위 표지 — (표지, 뒤를 검증 사유로 보이는가). 그 밖 꼬리는 예외다.
_DB_EXEC_TAILS: tuple[tuple[str, bool], ...] = (
    (" | 재생성 1회 시도했으나 검증 실패", True),
    (" | 재생성 1회 후 재실패", False),  # 뒤는 재실행 예외
)
#: 여러 작업의 같은 DB 사유를 잇는 구분(`result_aggregator._merged_zone_state`) —
#: 사유 시작 앞에서만 끊는다.
_DB_JOIN_RE = re.compile(
    rf" / (?=DB '|{re.escape(_DB_VALIDATION_HEAD)}|{_DEADLINE_HEAD}|{_SELECTION_NONE_HEAD})"
)
#: 멀티 DB 실패 요약(`result_merger._build_error_summary` — 머리 + 「[표시명] 사유」 줄).
_DB_SUMMARY_RE = re.compile(
    r"(모든 DB 쿼리가 실패했습니다"
    r"|일부 DB 쿼리가 실패했습니다 \(성공: \d+개, 실패: \d+개\)):\n(.*)",
    re.DOTALL,
)
_DB_SUMMARY_LINE_RE = re.compile(r"\n(?=\[[^\]\n]*\] )")
_DB_SUMMARY_NAME_RE = re.compile(r"(\[[^\]\n]*\]) (.*)", re.DOTALL)


def _is_deadline_message(text: str) -> bool:
    """코드가 지은 조회 마감 문구 그대로인가 — 꼬리(마지막 실패 사유)도 보일 수 있는 값이다."""
    matched = _DEADLINE_RE.fullmatch(text)
    if not matched:
        return False
    reason = matched.group("reason")
    return reason is None or safe_error_text(reason) == reason


def _db_error_part(text: str) -> str:
    """DB별 사유 한 건의 판별(`db_error_text`의 단위)."""
    if text.startswith(_DB_VALIDATION_HEAD):
        reason, sep, excerpt = text[len(_DB_VALIDATION_HEAD):].partition(_EXCERPT_SEP)
        return f"{_DB_VALIDATION_HEAD}{validation_reason_text(reason)}{sep}{excerpt}"
    if (
        _is_deadline_message(text)
        or _SELECTION_NONE_RE.fullmatch(text)
        or _DB_FIXED_RE.fullmatch(text)
    ):
        return text
    exec_error = _DB_EXEC_RE.fullmatch(text)
    if exec_error:
        head, rest = exec_error.groups()
        deadline_at = rest.find(f" | {_DEADLINE_HEAD}")
        if deadline_at >= 0:
            deadline = rest[deadline_at + len(" | "):]
            return f"{head} | {deadline}" if _is_deadline_message(deadline) else head
        for marker, keeps_reason in _DB_EXEC_TAILS:
            at = rest.find(marker)
            if at >= 0:
                if not keeps_reason:
                    return f"{head}{marker}"
                reason = rest[at + len(marker):].removeprefix(": ")
                return f"{head}{marker}: {validation_reason_text(reason)}"
        return head
    return safe_error_text(text) or GENERIC_TASK_ERROR


def db_error_text(raw: Any) -> str:
    """화면에 보일 DB별 실패 사유 — 예외 보간부만 떼고 코드가 지은 문구·산출 발췌는 남긴다.

    - 「DB '{id}' 실행 에러: {예외}」 → 「DB '{id}' 실행 에러」(재생성 경위 표지·조회 마감 문구는
      유지, 재실행 예외는 뗀다)
    - 「SQL 검증 실패: {검증 사유}[ | LLM 산출 발췌: '…']」 → 검증 사유는 `validation_reason_text`,
      발췌는 그대로(D-153 후속2 — PII 스크럽된 150자)
    - 조회 마감 문구 · 테이블 선별 0개 안내 · 미등록 DB · 테이블 없음 → 그대로
    - 그 밖 → `safe_error_text`, 그래도 없으면 `GENERIC_TASK_ERROR`

    여러 작업의 같은 DB 사유가 「 / 」로 이어져 있으면 건마다 판별한다.

    Args:
        raw: `db_errors`의 값 한 개
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return GENERIC_TASK_ERROR
    return " / ".join(_db_error_part(part) for part in _DB_JOIN_RE.split(text))


def _db_summary_text(match: re.Match[str]) -> str:
    """멀티 DB 실패 요약 — 머리는 그대로, 「[표시명] 사유」 줄마다 `db_error_text`."""
    head, body = match.groups()
    lines: list[str] = []
    for line in _DB_SUMMARY_LINE_RE.split(body):
        named = _DB_SUMMARY_NAME_RE.fullmatch(line)
        lines.append(
            f"{named.group(1)} {db_error_text(named.group(2))}" if named else db_error_text(line)
        )
    return f"{head}:\n" + "\n".join(lines)


def request_id() -> str:
    """오류 문구·로그에 붙일 요청 번호 — 감사 미들웨어가 structlog 컨텍스트에 묶은 값(없으면 "-").

    `src.api` 밖(orchestration·nodes)에서도 같은 값을 읽도록 utils에 둔다.
    """
    rid = structlog.contextvars.get_contextvars().get("request_id")
    return str(rid) if rid else "-"
