"""LLM 응답에서 SQL 추출 — 단일/멀티 경로 공용 단일 출처(D-066 · D-153).

plans/166 N2(D-337)로 `src/utils/query_gen_common.py`에서 옮겼다(동작 불변 Move-only).
옮긴 이름은 `query_gen_common`이 모두 재노출한다.
"""

from __future__ import annotations

import re
from typing import Any

from src.utils.json_extract import coerce_content_text


def extract_sql_from_response(content: str | list[Any]) -> str:
    """LLM 응답에서 SQL 쿼리를 추출한다.

    단일 DB 경로(query_generator)와 멀티 DB 경로(multi_db_executor)가 동일 추출
    규칙을 쓰도록 단일 출처로 공유한다(D-066). 추출 엔진은 강화판
    `extract_sql_from_llm_response`(펜스 태그 변형·세미콜론 생략·WITH 지원, D-153)에
    위임하고, 여기서는 실 모델의 콘텐츠 블록 리스트 정규화만 얹는다(json_extract와 대칭).

    Args:
        content: LLM 응답 텍스트(콘텐츠 블록 리스트 허용)

    Returns:
        추출된 SQL 문자열
    """
    return extract_sql_from_llm_response(coerce_content_text(content))


# ── LLM 응답 SQL 추출 (D-153 — 단일/멀티 경로 2벌 중복 해소, D-067 취지) ─────────
# 폐쇄망 실측(2026-08-04): 추출 실패 시 응답 전문(산문)이 SQL로 흘러 간이 검증
# "SELECT 문이 아닙니다"로 떨어졌고, 멀티 경로에서는 동일 스키마 SQL 캐시(D-066 후속6)
# 구조상 첫 번째 DB(공동존 gp)만 간헐 누락됐다. LLM 출력 형식(펜스 태그 대소문자·
# 언어 태그 변형·세미콜론 생략)의 비결정성은 프롬프트 강제가 아니라 결정적 후처리로
# 흡수한다(Known Mistakes 원칙).
_SQL_FENCE_RE = re.compile(
    r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n?(.*?)```", re.DOTALL
)
# 세미콜론 종결 SQL(서두 산문 허용). WITH는 영어 산문("with the ...")과의 오탐을 막기 위해
# CTE 형태(WITH <이름> AS ()만 인정한다.
_SQL_SEMI_RE = re.compile(
    r'((?:SELECT\s+|WITH\s+[\w"]+\s+AS\s*\().*?;)', re.IGNORECASE | re.DOTALL
)
# 세미콜론 생략 SQL — SELECT/WITH부터 말미까지(닫는 펜스 잔재는 호출부에서 제거).
_SQL_TAIL_RE = re.compile(
    r'((?:SELECT\s+|WITH\s+[\w"]+\s+AS\s*\().*)', re.IGNORECASE | re.DOTALL
)
_SQL_COMMENT_RE = re.compile(r"(?:--[^\n]*\n?|/\*.*?\*/)", re.DOTALL)


def looks_like_readonly_sql(text: str) -> bool:
    """주석 제거 후 SELECT/WITH로 시작하는 읽기 전용 SQL 형태인지 판정한다."""
    cleaned = _SQL_COMMENT_RE.sub("", text or "").strip()
    return cleaned.upper().startswith(("SELECT", "WITH"))


def extract_sql_from_llm_response(content: str) -> str:
    """LLM 응답에서 SQL을 추출한다 (query_generator·multi_db_executor 공용 단일 출처).

    흡수하는 실패 형태(종전 구현은 전부 응답 전문 폴백 → 검증 실패):
    ① 펜스 언어 태그 변형: ```SQL / ```postgresql / 무태그 — 태그 불문 매칭 후
       블록 내용이 SQL 형태(SELECT/WITH 시작)인지로 판정.
    ② 세미콜론 생략: SELECT/WITH부터 말미까지 추출(닫는 펜스 잔재 제거).
    ③ 서두 산문 + 펜스 없는 SQL(세미콜론 유무 불문).

    말미 산문이 딸려 들어가는 과추출은 후단 검증(_find_bare_hangul_tokens·실행 에러
    재시도)이 잡는다 — 종전의 "확정 추출 실패"보다 항상 좁거나 같은 실패면이다.

    Args:
        content: LLM 응답 텍스트

    Returns:
        추출된 SQL 문자열 (추출 불가 시 전문 반환 — 기존 폴백 시맨틱 유지)
    """
    text = content or ""

    # 1) 코드 펜스(언어 태그·대소문자 불문) 중 SQL 형태인 첫 블록
    for m in _SQL_FENCE_RE.finditer(text):
        block = m.group(1).strip()
        if block and looks_like_readonly_sql(block):
            return block

    # 2) 세미콜론 종결 SQL (서두 산문 허용)
    m = _SQL_SEMI_RE.search(text)  # type: ignore[assignment]
    if m:
        return m.group(1).strip()

    # 3) 세미콜론 생략 SQL — SELECT/WITH부터 말미까지, 닫는 펜스·백틱 잔재 제거
    m = _SQL_TAIL_RE.search(text)
    if m:
        tail = m.group(1).split("```", 1)[0].strip().strip("`").strip()
        if tail:
            return tail

    # 4) 최후 수단: 전문 반환 (후단 검증이 거른다 — 기존 동작 유지)
    return text.strip()
