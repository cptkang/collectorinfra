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

**표본 앞 턴(plans/150 W1 · F1 · G-1 (a)).** 앞 턴 결과가 엔티티 상한(`ENTITY_SAMPLE_ROWS`)을
넘으면 다음 턴이 보존하는 엔티티는 앞쪽 일부(표본)다. 지시어 후속 턴이 그 표본을 대상으로 삼으면
앞 결과 일부만 조회하는 침묵 부분 답이 된다(5회차 ITAM-106 t2 — 85대 중 IP 10개 `IN`).
1. 턴 끝 마감이 상한을 넘는 행으로 끝난 조회 턴의 원 질의·실행 SQL·행 수를 남긴다
   (`SAMPLED_TURN_KEY` — 바로 다음 턴만 본다).
2. 다음 턴 `context_resolver`가 지시어 명사구 + 표본(엔티티가 앞 결과 전체를 담지 않음)을 확인하면
   승계 맥락(`SAMPLED_ANTECEDENT_KEY`)을 싣는다 — 계획 맥락은 표본 값을 「직전 대상」으로 싣지 않고
   앞 질의 조건 승계를 지시한다.
3. 실행 뒤 표본 값을 `IN`으로 걸었으면 「앞 결과 N건 중 일부만 조회」, 앞 턴 조건을 하나도 잇지
   않았으면 「전체 대상일 수 있다」를 응답 **맨 앞**에 싣는다. 행은 숨기지 않는다(146 기각 설계).
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

#: 턴 끝 마감이 상한을 넘는 행으로 끝난 조회 턴을 맥락에 남기는 키(plans/150 W1) — 다음 턴
#: `context_resolver`만 읽는다(옮기지 않는다).
SAMPLED_TURN_KEY = "sampled_turn"
#: 이번 턴 맥락의 승계 키 — 지시어가 표본만 보존된 앞 턴 조회를 가리킨다
#: (`{"query", "sqls", "row_count", "sample_values"}`).
SAMPLED_ANTECEDENT_KEY = "sampled_antecedent"
#: 직전 턴 식별 엔티티를 보존하는 행수 상한 — 앞 턴 결과가 이보다 많으면 엔티티는 표본이다
#: (`context_resolver._MAX_ENTITY_ROWS`의 정본 · 2026-06-11 상한 원칙).
ENTITY_SAMPLE_ROWS = 20

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
#: SQL 칸 참조 — `별칭.칸`·`` `칸` ``·`` 별칭.`칸` ``·맨 이름(한글 포함).
_COLUMN_REF = (
    r'(?:[A-Za-z_가-힣][\w가-힣]*\.|"[^"]+"\.)?'
    r'(?:`[^`]+`|"[^"]+"|[A-Za-z_가-힣][\w가-힣]*)'
)
#: 칸 이름이 붙은 문자열 `IN` 목록(`칸 [NOT] IN ('…', …)`) — 표본 `IN` 판정용(plans/150 W1 교정).
_IN_WITH_COLUMN = re.compile(
    rf"({_COLUMN_REF})\s+(NOT\s+)?IN\s*\(((?:\s*'(?:[^']|'')*'\s*,?)+)\)", re.IGNORECASE
)
#: `WHERE`·`HAVING` 절 본문 — 다음 절(GROUP/ORDER BY·LIMIT·FETCH·UNION)이나 끝까지.
_FILTER_CLAUSE = re.compile(
    r"\b(?:WHERE|HAVING)\b(.*?)(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bFETCH\b"
    r"|\bUNION\b|$)",
    re.IGNORECASE | re.DOTALL,
)
#: 비교 술어의 왼쪽 칸 — 오른쪽이 다른 칸(`a.x = b.y` · 조인 모양)이면 조건으로 보지 않는다.
_COMPARED_COLUMN = re.compile(
    rf"({_COLUMN_REF})\s*(?:<=|>=|<>|!=|=|<|>|\bNOT\s+LIKE\b|\bLIKE\b|\bBETWEEN\b"
    rf"|\bNOT\s+IN\b|\bIN\b|\bIS\b)\s*(`|(?:[A-Za-z_가-힣][\w가-힣]*|\"[^\"]+\")\.[`A-Za-z_가-힣\"])?",
    re.IGNORECASE,
)
#: 판정 전용 표본 식별 칸 힌트 — 엔티티 힌트(`context_resolver._IDENTITY_KEY_HINTS`)에 한글 식별
#: 칸을 더한다(교정 M2 · `서버호스트명`). `previous_entities`·단수 지시어 승계에는 쓰지 않는다.
_SAMPLE_KEY_HINTS = (
    "hostname", "host_name", "name", "server_name", "ip", "id", "호스트", "서버명", "장비명",
)


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


def sampled_turn(query: str, sqls: list[str], row_count: int) -> dict[str, Any] | None:
    """엔티티 상한을 넘는 행으로 끝난 조회 턴의 기록 — 상한 이하이거나 실행 SQL이 없으면 None."""
    kept = [str(s) for s in sqls if s][:_MAX_SQLS]
    if row_count <= ENTITY_SAMPLE_ROWS or not kept:
        return None
    return {"query": str(query or ""), "sqls": kept, "row_count": int(row_count)}


def resolve_sampled_antecedent(
    query: str,
    prior_ctx: Mapping[str, Any] | None,
    fresh_entities: list[Any],
    *,
    entities_complete: bool,
    sample_rows: list[Any] | None = None,
) -> dict[str, Any] | None:
    """이번 턴 지시어가 표본만 보존된 앞 턴 조회를 가리키면 그 기록(+표본 값), 아니면 None.

    엔티티가 앞 결과 전체를 담으면(앞 턴이 서버 식별 필터로 좁혔다 — `previous_entities_complete`)
    대상은 그 엔티티다 — 표본으로 보지 않는다. 표본 값(`sample_values`)·칸(`sample_columns`)은 실행
    뒤 「일부만 조회」 판정에만 쓴다 — 엔티티 값에 앞 결과 앞쪽 행(`sample_rows`)의 식별 칸 값(한글
    식별 칸 포함)을 더한다(교정 M2).
    """
    if entities_complete or not _has_demonstrative_phrase(query or ""):
        return None
    record = (prior_ctx or {}).get(SAMPLED_TURN_KEY)
    if not isinstance(record, Mapping) or not record.get("sqls"):
        return None
    values, columns = _sample_identity_values(sample_rows or [])
    for e in fresh_entities:
        if not isinstance(e, Mapping) or e.get("value") in (None, ""):
            continue
        if str(e["value"]) not in values:
            values.append(str(e["value"]))
        if e.get("field") and str(e["field"]) not in columns:
            columns.append(str(e["field"]))
    return {
        "query": str(record.get("query") or ""),
        "sqls": list(record["sqls"]),
        "row_count": int(record.get("row_count") or 0),
        "sample_values": values,
        "sample_columns": columns,
    }


def _sample_identity_values(rows: list[Any]) -> tuple[list[str], list[str]]:
    """앞 결과 앞쪽 행(`ENTITY_SAMPLE_ROWS`)의 식별 칸 값·칸 이름 — 표본 판정 전용(교정 M2)."""
    limited = [r for r in rows[:ENTITY_SAMPLE_ROWS] if isinstance(r, Mapping)]
    if not limited:
        return [], []
    columns = [
        str(c) for c in limited[0] if any(h in str(c).lower() for h in _SAMPLE_KEY_HINTS)
    ]
    values: list[str] = []
    for row in limited:
        for col in columns:
            value = row.get(col)
            if value not in (None, "") and str(value) not in values:
                values.append(str(value))
    return values, columns


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

    표본 앞 턴 승계(plans/150 W1 · 교정 L9)도 같은 자리에서 기록(`SAMPLED_TURN_KEY`)을 넘긴다.
    승계 중이 아니면 None.
    """
    if not ctx:
        return None
    extra: dict[str, Any] = {}
    record = carried_zero_row_turn(ctx.get(EMPTY_ANTECEDENT_KEY), "", [])
    if record:
        extra[ZERO_ROW_TURN_KEY] = record
    sampled = ctx.get(SAMPLED_ANTECEDENT_KEY)
    if isinstance(sampled, Mapping) and sampled.get("sqls"):
        extra[SAMPLED_TURN_KEY] = {
            "query": str(sampled.get("query") or ""),
            "sqls": list(sampled["sqls"]),
            "row_count": int(sampled.get("row_count") or 0),
        }
    return {**ctx, **extra} if extra else None


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


def _column_key(ref: str) -> str:
    """칸 참조의 비교용 이름 — 별칭·따옴표를 떼고 소문자."""
    return ref.rsplit(".", 1)[-1].strip('`"').lower()


def _compared_columns(sql: str) -> set[str]:
    """`WHERE`·`HAVING` 절에서 값과 비교되는 칸 이름(조인 모양 `a.x = b.y` 제외)."""
    return {
        _column_key(m.group(1))
        for clause in _FILTER_CLAUSE.findall(sql or "")
        for m in _COMPARED_COLUMN.finditer(clause)
        if not m.group(2)
    }


def _carries_prior(antecedent: Mapping[str, Any], sql: str) -> bool | None:
    """한 SQL이 앞 턴 조건을 잇는가 — 판정할 지문이 없으면 None.

    앞 턴 문자열 조건 값이 있으면 그 값으로(146 W3 규칙), 없으면 앞 턴 비교 술어의 칸 이름으로
    판정한다(교정 M3 — 날짜 함수뿐인 앞 조건 · ITAM-212 1턴 모양).
    """
    prior_sqls = antecedent.get("sqls") or []
    prior = set().union(*(_prior_condition_values(s) for s in prior_sqls))
    if prior:
        current = _literal_values(sql)
        return any(p in c or c in p for p in prior for c in current)
    prior_cols = set().union(*(_compared_columns(s) for s in prior_sqls))
    if not prior_cols:
        return None
    return bool(prior_cols & _compared_columns(sql))


def widened_beyond_antecedent(
    antecedent: Mapping[str, Any], sqls: list[str], *, by_columns: bool = False,
) -> bool:
    """이번 턴 실행 SQL이 앞 턴 조건 값을 하나도 잇지 않았는가(조건 없이 넓힘).

    앞 턴 조건 값이 없으면 판정하지 않는다(False — 종전 동작). 값은 한쪽이 다른 쪽을 포함하면 이은
    것으로 본다(「통합인증 서비스」 → 「통합인증」 재표현 허용). `by_columns`(표본 승계 · 교정 M3)면
    문자열 조건 값이 없을 때 앞 턴 비교 술어의 칸 이름으로 판정한다 — 이번 SQL에 그 칸의 비교가
    하나도 없으면 넓힘이다. 146 W3 0행 경로는 종전대로 쓴다(기본 False).
    """
    prior = set().union(*(_prior_condition_values(s) for s in antecedent.get("sqls") or []))
    if not prior:
        if not by_columns:
            return False
        verdicts = [_carries_prior(antecedent, s) for s in sqls]
        # None = 앞 턴 비교 칸도 없다(판정하지 않음 — 모든 SQL에서 같다)
        if not verdicts or None in verdicts:
            return False
        return not any(verdicts)
    current = set().union(*(_literal_values(s) for s in sqls))
    return not any(p in c or c in p for p in prior for c in current)


def _has_sample_in(antecedent: Mapping[str, Any], sql: str) -> bool:
    """한 SQL이 표본 값만으로 된 `칸 IN (…)` 목록을 갖는가.

    - 목록이 표본 값과 겹치고 표본 밖 값이 없어야 한다 — 표본 밖 값이 섞인 목록(선행 결과 전체로 건
      스코프)은 표본이 아니다.
    - `NOT IN`은 표본을 빼는 조건이라 보지 않는다.
    - 값이 모두 숫자면(코드·ID) 칸 이름이 표본 칸과 같을 때만 본다(교정 L10 — 다른 칸의 코드
      `IN ('1', '2')`와 숫자 ID 표본이 우연히 겹친다).
    """
    sample = {_normalized(str(v)) for v in antecedent.get("sample_values") or []} - {""}
    if not sample:
        return False
    columns = {_column_key(str(c)) for c in antecedent.get("sample_columns") or []}
    for col, negated, group in _IN_WITH_COLUMN.findall(sql or ""):
        if negated:
            continue
        listed = {_normalized(v) for v in _SQL_LITERAL.findall(group)} - {""}
        if not (listed & sample and listed <= sample):
            continue
        if all(v.isdigit() for v in listed) and _column_key(col) not in columns:
            continue
        return True
    return False


def scoped_to_sample(antecedent: Mapping[str, Any], sqls: list[str]) -> bool:
    """이번 턴 실행 SQL이 앞 턴 표본 값을 `IN` 목록으로 걸었는가(앞 결과 일부만 조회).

    표본 `IN` 목록(`_has_sample_in`)이 있으면 표본으로 좁힌 것으로 본다 — 표본 밖 행은 애초에
    조회되지 않는다(같은 SQL에서 앞 턴 조건을 함께 이었어도 일부다). 다만 같은 턴의 **앞선** SQL이
    앞 턴 조건을 다시 걸어 조회했으면(표본 `IN` 없이) 그 뒤 `IN`은 그 결과에서 좁힌 정상 승계다
    (교정 M1 — 「그 서버들 중 AIX」 재조회 1대가 우연히 표본 안).
    """
    for k, sql in enumerate(sqls):
        if not _has_sample_in(antecedent, sql):
            continue
        if any(
            _carries_prior(antecedent, s) and not _has_sample_in(antecedent, s)
            for s in sqls[:k]
        ):
            continue
        return True
    return False


def sampled_antecedent_note(antecedent: Mapping[str, Any], *, partial: bool) -> str:
    """사용자 안내 — 표본 앞 턴 승계(plans/150 W1). 응답 맨 앞에 붙인다.

    `partial`이면 표본 값만 걸어 앞 결과 일부만 조회한 경우, 아니면 앞 턴 조건을 잇지 않아 전체로
    넓힌 경우(146 W3 「넓힘」과 같은 부류)다.
    """
    query = antecedent_query_quote(antecedent)
    count = int(antecedent.get("row_count") or 0)
    where = f"앞 질문(「{query}」)" if query else "앞 질문"
    if partial:
        return (
            f"[안내] 이번 조회는 {where}의 결과 {count}건 중 일부 서버만 대상으로 했습니다 — "
            "아래 결과는 앞 결과 전체가 아닙니다. 전체를 보려면 앞 질문의 조건을 함께 적어 "
            "다시 질문해 주세요."
        )
    return (
        f"[안내] 이번 조회는 {where}의 조건을 잇지 않아, 아래 결과는 앞에서 찾은 서버 {count}건이 "
        "아니라 전체 대상일 수 있습니다. 앞 조건으로 보려면 대상을 직접 적어 다시 질문해 주세요."
    )


def sampled_antecedent_outcome(
    antecedent: Mapping[str, Any] | None, sqls: list[str], rows: int,
) -> str | None:
    """표본 앞 턴 승계 턴의 실행 뒤 안내 — 붙일 문구 또는 None(plans/150 W1 · 2단/3·4단 공통).

    표본 `IN`은 행 수와 무관하게 알린다(0행이어도 일부만 본 결과다). 넓힘은 행이 있을 때만 알린다.
    """
    if not antecedent:
        return None
    if scoped_to_sample(antecedent, sqls):
        return sampled_antecedent_note(antecedent, partial=True)
    if rows > 0 and widened_beyond_antecedent(antecedent, sqls, by_columns=True):
        return sampled_antecedent_note(antecedent, partial=False)
    return None


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
    "ENTITY_SAMPLE_ROWS",
    "SAMPLED_ANTECEDENT_KEY",
    "SAMPLED_TURN_KEY",
    "ZERO_ROW_TURN_KEY",
    "antecedent_query_quote",
    "carried_zero_row_turn",
    "carried_zone_turn_context",
    "empty_antecedent_note",
    "resolve_empty_antecedent",
    "resolve_sampled_antecedent",
    "sampled_antecedent_note",
    "sampled_antecedent_outcome",
    "sampled_turn",
    "scoped_to_sample",
    "widened_beyond_antecedent",
    "zero_row_turn",
]
