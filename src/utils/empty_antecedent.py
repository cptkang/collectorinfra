"""앞 턴 0행 지시어 승계 (plans/146 W3 · F6 · G-3 (a)).

**무엇인가.** 앞 턴 조회가 0행이었는데 이번 턴이 지시어(「그 서버들 …」)로 앞 턴 대상을 가리키면,
가리키는 대상은 **빈 집합**이다. 결과 행에서 뽑는 엔티티(`previous_entities`)가 비어 지시어가
무력화되면 다음 턴이 조건 없이 전체를 조회한다(내부망 3회차 ITAM-106 t2 — 3,748행 침묵 오답).

**규칙.**
1. 턴 끝 마감(2단 `result_aggregator` · 3단/4단 `output_generator`)이 SQL을 실행해 0행으로 끝난
   턴의 원 질의·실행 SQL을 맥락에 남긴다(`ZERO_ROW_TURN_KEY` — 바로 다음 턴만 본다).
2. 다음 턴 `context_resolver`가 이번 턴 지시어 + 앞 턴 0행 + 앞 턴이 식별자를 지목하지 않음을
   확인하면 승계 맥락(`EMPTY_ANTECEDENT_KEY`)을 싣는다 — 계획·SQL 생성이 앞 턴 조건을 잇는다.
3. 이번 턴 결과가 다시 0행이면 「앞 질문에서 찾은 서버가 없다」를 결정적으로 붙인다. 행이 나왔는데
   실행 SQL에 앞 턴 조건 값이 하나도 없으면(조건 없이 넓힘) 행은 그대로 보이되 응답 **맨 앞**에
   「아래 결과는 전체 대상일 수 있다」 안내를 싣는다 — LLM이 조건을 승계했는지에 정합성을 맡기지
   않는다(D-004). 판정은 근사라(오탐 가능) 행을 숨기지 않는다(교정 1).
4. 존 역질문 턴은 조회가 아니다 — 승계 중이면 기록을 그대로 넘겨 존 답변 턴이 잇게 한다.

앞 턴이 행을 돌려줬거나 이번 턴에 지시어가 없으면 아무것도 하지 않는다(종전 동작 그대로).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.utils.query_gen_common import DEMONSTRATIVE_NOUNS, DEMONSTRATIVE_PREFIXES

#: 턴 끝 마감이 0행 조회 턴을 맥락에 남기는 키 — 다음 턴 `context_resolver`만 읽는다(옮기지 않는다).
ZERO_ROW_TURN_KEY = "zero_row_turn"
#: 이번 턴 맥락의 승계 키 — 지시어가 앞 턴 0행 조회를 가리킨다(`{"query", "sqls"}`).
EMPTY_ANTECEDENT_KEY = "empty_antecedent"

#: 맥락에 남기는 SQL 수 상한(멀티 DB · 재조회) — 프롬프트 토큰 절약.
_MAX_SQLS = 3
#: 안내 문구에 인용하는 앞 질문 길이 상한.
_QUOTE_MAX = 60

#: SQL 문자열 상수('…' — '' 이스케이프 포함).
_SQL_LITERAL = re.compile(r"'((?:[^']|'')*)'")
_QUOTED = r"'((?:[^']|'')*)'"
#: 앞 턴 조건 값 — LIKE 술어(이름·용도 검색 · `UPPER('…')` 감싸기 허용).
_LIKE_VALUE = re.compile(
    rf"\bI?LIKE\s*(?:(?:UPPER|LOWER)\s*\(\s*)?{_QUOTED}", re.IGNORECASE
)
#: 앞 턴 조건 값 — LIKE 오른쪽이 이어 붙인 식(`CONCAT('%', '값', '%')` · `'%' || '값' || '%'`).
_LIT = r"'(?:[^']|'')*'"
_LIKE_EXPR = re.compile(
    rf"\bI?LIKE\s*(CONCAT\s*\([^)]*\)|{_LIT}(?:\s*\|\|\s*{_LIT})+)", re.IGNORECASE
)
#: 앞 턴 조건 값 — 같음 비교 술어(`= '…'`·`<> '…'`).
_EQ_VALUE = re.compile(rf"(?:<>|!=|(?<![<>!])=)\s*{_QUOTED}")
#: 앞 턴 조건 값 — `IN ('…', '…')` 목록.
_IN_LIST = re.compile(r"\bIN\s*\(((?:\s*'(?:[^']|'')*'\s*,?)+)\)", re.IGNORECASE)
#: 조건 값으로 보지 않는 모양 — 날짜·시각·숫자 문자열과 날짜 형식 문자열(`%Y-%m`·`YYYY-MM-DD`).
_DATE_OR_FORMAT = re.compile(r"^(?:[\d\s\-/:.T]|%[A-Za-z]|YYYY|YY|MM|DD|HH24|HH|MI|SS)+$")
#: 지시어 명사구 — 접두 앞은 단어 경계(앞 글자가 한글·영숫자가 아님 · 「로그서버」 배제).
_DEMONSTRATIVE_PHRASE = re.compile(
    r"(?<![가-힣A-Za-z0-9])(?:"
    + "|".join(map(re.escape, DEMONSTRATIVE_PREFIXES))
    + r")(?: )?(?:"
    + "|".join(map(re.escape, DEMONSTRATIVE_NOUNS))
    + ")"
)
#: 지시어 명사구 **앞**에 오면 전역 질의로 보는 말(「전체 서버 중 …」).
#: 명사구 뒤의 「모두」는 수량 부사다.
_GLOBAL_BEFORE = ("전체", "모든")
#: 조건 값 비교에서 걷어 내는 문자 — LIKE 와일드카드·공백.
_LITERAL_NOISE = re.compile(r"[%_\s]")


def zero_row_turn(query: str, sqls: list[str]) -> dict[str, Any] | None:
    """0행으로 끝난 조회 턴의 기록 — 실행 SQL이 없으면 None."""
    kept = [str(s) for s in sqls if s][:_MAX_SQLS]
    if not kept:
        return None
    return {"query": str(query or ""), "sqls": kept}


def resolve_empty_antecedent(
    query: str, prior_ctx: Mapping[str, Any] | None, fresh_entities: list[Any],
) -> dict[str, Any] | None:
    """이번 턴 지시어가 앞 턴 0행 조회를 가리키면 그 기록, 아니면 None.

    앞 턴이 식별자를 지목해 엔티티가 남았으면(「server01 CPU」 0행 → 「그 서버 메모리」) 대상은 그
    식별자다 — 빈 집합으로 보지 않는다.
    """
    if fresh_entities or not _has_demonstrative_phrase(query or ""):
        return None
    record = (prior_ctx or {}).get(ZERO_ROW_TURN_KEY)
    if not isinstance(record, Mapping) or not record.get("sqls"):
        return None
    return {"query": str(record.get("query") or ""), "sqls": list(record["sqls"])}


def _has_demonstrative_phrase(text: str) -> bool:
    """지시어 명사구(「그 서버들」·「해당 장비」)가 있는가.

    공용 `refers_to_demonstrative_server`와 같은 접두·명사 인접 판정이되 두 가지가 다르다.
    - 「전체/모든」은 명사구 **앞**에 올 때만 배제한다 — 「그 서버들의 지원 종료일을 모두 알려줘」도
      앞 턴 대상을 가리킨다(교정 1 M-4).
    - 접두 앞은 단어 경계여야 한다 — 「로그서버」의 「그서버」를 지시어로 보지 않는다(교정 2 M-R1).
    """
    match = _DEMONSTRATIVE_PHRASE.search(text)
    if match is None:
        return False
    return not any(g in text[: match.start()] for g in _GLOBAL_BEFORE)


def carried_zero_row_turn(
    antecedent: Mapping[str, Any] | None, query: str, sqls: list[str],
) -> dict[str, Any] | None:
    """이번 턴이 0행으로 보일 때 다음 턴에 남길 기록 — 승계 중이면 앞 턴 기록을 그대로 잇는다."""
    if antecedent:
        return {
            "query": str(antecedent.get("query") or ""),
            "sqls": list(antecedent.get("sqls") or []),
        }
    return zero_row_turn(query, sqls)


def carried_zone_turn_context(ctx: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """존 역질문 턴의 맥락 — 승계 중이면 기록을 넘겨 존 답변 턴이 잇게 한다(교정 1 M-2).

    승계 중이 아니면 None.
    """
    record = carried_zero_row_turn((ctx or {}).get(EMPTY_ANTECEDENT_KEY), "", [])
    if not ctx or not record:
        return None
    return {**ctx, ZERO_ROW_TURN_KEY: record}


def _normalized(raw: str) -> str:
    """비교용 값 — '' 이스케이프 복원 · 와일드카드·공백 제거 · 소문자."""
    return _LITERAL_NOISE.sub("", raw.replace("''", "'")).lower()


def _literal_values(sql: str) -> set[str]:
    """SQL 문자열 상수 전부의 비교용 값(2자 이상) — 이번 턴 쪽(조건을 어떤 형태로 이었든 인정)."""
    return {v for v in map(_normalized, _SQL_LITERAL.findall(sql or "")) if len(v) >= 2}


def _prior_condition_values(sql: str) -> set[str]:
    """앞 턴 SQL의 조건 값 — 비교 술어(`LIKE`·`=`·`IN`)의 문자열 값만(교정 1 H-2·M-1).

    날짜·숫자·형식 문자열은 조건 값으로 보지 않는다(이번 턴이 함수·숫자로 이어도 판정이 틀리지
    않게). LIKE 값(이름·용도 검색)이 있으면 그것만 본다 — 상태값·속성명 같은 부수 같음 조건이
    겹친다고 이은 것으로 보지 않는다.
    """
    text = sql or ""
    like_raws = [
        *_LIKE_VALUE.findall(text),
        # CONCAT·`||` 식 안의 리터럴(교정 2 R-L2) — 와일드카드만인 것은 아래에서 걸러진다
        *(v for expr in _LIKE_EXPR.findall(text) for v in _SQL_LITERAL.findall(expr)),
    ]
    like = [v for v in like_raws if _normalized(v) and not _DATE_OR_FORMAT.match(v)]
    raws = like or [
        *_EQ_VALUE.findall(text),
        *(v for group in _IN_LIST.findall(text) for v in _SQL_LITERAL.findall(group)),
    ]
    return {
        v for v in (_normalized(r) for r in raws if not _DATE_OR_FORMAT.match(r))
        if len(v) >= 2
    }


def widened_beyond_antecedent(antecedent: Mapping[str, Any], sqls: list[str]) -> bool:
    """이번 턴 실행 SQL이 앞 턴 조건 값을 하나도 잇지 않았는가(조건 없이 넓힘).

    앞 턴 조건 값이 없으면 판정하지 않는다(False — 종전 동작). 값은 한쪽이 다른 쪽을 포함하면 이은
    것으로 본다(「통합인증 서비스」 → 「통합인증」 재표현 허용).
    """
    prior = set().union(*(_prior_condition_values(s) for s in antecedent.get("sqls") or []))
    if not prior:
        return False
    current = set().union(*(_literal_values(s) for s in sqls))
    return not any(p in c or c in p for p in prior for c in current)


def antecedent_query_quote(antecedent: Mapping[str, Any]) -> str:
    """앞 질문 인용 — 안내 문구·계획 맥락 공통 길이 상한(60자)."""
    query = str(antecedent.get("query") or "").strip()
    return query[:_QUOTE_MAX] + "…" if len(query) > _QUOTE_MAX else query


def empty_antecedent_note(antecedent: Mapping[str, Any], *, widened: bool) -> str:
    """사용자 안내 — 앞 질문에서 찾은 서버가 없다(다시 0행 · 조건 없이 넓힘).

    넓힘 안내는 응답 맨 앞에, 다시 0행 안내는 본문 뒤에 붙인다.
    """
    query = antecedent_query_quote(antecedent)
    head = f"[안내] 앞 질문(「{query}」)에서 찾은 서버가 없습니다" if query else (
        "[안내] 앞 질문에서 찾은 서버가 없습니다"
    )
    if widened:
        return (
            f"{head} — 이번 조회는 앞 질문의 조건을 잇지 않아, "
            "아래 결과는 앞에서 찾은 서버가 아니라 전체 대상일 수 있습니다. "
            "앞 조건으로 보려면 대상을 직접 적어 다시 질문해 주세요."
        )
    return f"{head} — 그 조건을 이어 다시 조회했지만 해당하는 서버가 없습니다."


__all__ = [
    "EMPTY_ANTECEDENT_KEY",
    "ZERO_ROW_TURN_KEY",
    "antecedent_query_quote",
    "carried_zero_row_turn",
    "carried_zone_turn_context",
    "empty_antecedent_note",
    "resolve_empty_antecedent",
    "widened_beyond_antecedent",
    "zero_row_turn",
]
