"""결과 병합 노드.

멀티 DB 실행 결과를 통합하여 result_organizer가
처리할 수 있는 형태로 변환한다.
DB별 에러가 있으면 부분 에러 정보도 포함한다.
DB별 결과 요약 정보를 생성하여 result_organizer에 전달한다.

순위 질의(top N · 최대/최소 N)는 DB별 결과를 이어 붙이기만 하면 "김포 10행 + 여의도 10행"이 되고
전체 기준 순위가 아니다(D-202 잔여 ① · plans/113 S-1). 각 DB가 **실행한 SQL**의 최외곽
`ORDER BY` 첫 키·방향과 행 상한(`LIMIT n` / `FETCH FIRST n ROWS ONLY`)을 결정적으로 읽어, 모든
DB가 같은 기준이면 전 DB 결과를 같은 키로 다시 정렬한 뒤 N행으로 자른다(LLM 판단 없음 —
D-035·D-068). 전역 상위 N ⊆ ∪(DB별 상위 N)이므로 DB별 SQL에 행 상한이 있으면 누락이 없다.

- `query_results`(원본 병합 — CSV 다운로드·선행 결과 전달의 원천)는 **바꾸지 않는다**(G-4:
  CSV는 DB별 전체 행 유지). 재정렬 결과는 `merged_ranking`으로 따로 싣고, `result_organizer`가
  응답용 행(`organized_data.rows`)으로 쓴다.
- 적용 조건이 깨지면(기준 불일치·판독 실패·UNION 등 한 SQL이 여러 목록을 내는 형태) 종전
  이어 붙이기를 유지하고 사유를 로그와 `merged_ranking.reason`으로 남긴다(침묵 강등 금지).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlparse
from sqlparse import sql as sql_ast
from sqlparse import tokens as sql_tokens

from src.config import AppConfig, load_config
from src.routing.domain_config import get_domain_by_id
from src.routing.registry import get_registry
from src.state import AgentState
from src.utils.query_gen_common import surface_query_for_judgment

logger = logging.getLogger(__name__)


async def result_merger(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """멀티 DB 결과를 통합한다.

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드:
        - query_results: 병합된 결과 행
        - db_result_summary: DB별 요약
        - merged_ranking: 순위 질의 전역 재정렬 결과(순위 형태가 아니면 None — 항상 싣는다)
        - merged_aggregates: 집계 질의 DB별·전체 값(집계 형태가 아니면 None — 항상 싣는다)
        - error_message: 부분 에러 메시지 (모든 DB 실패 시)
        - current_node: "result_merger"
    """
    if app_config is None:
        app_config = load_config()

    db_results = state.get("db_results", {})
    db_errors = state.get("db_errors", {})

    # 결과 병합 (이미 multi_db_executor에서 query_results로 병합됨)
    merged_results = state.get("query_results", [])

    # DB별 결과 요약 정보 생성
    db_result_summary: dict[str, dict] = {}
    for db_id, rows in db_results.items():
        domain = get_domain_by_id(db_id)
        db_result_summary[db_id] = {
            "display_name": domain.display_name if domain else db_id,
            "row_count": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
        }

    # 에러 요약 생성
    error_summary = _build_error_summary(db_results, db_errors)

    # 결과 통계 로그
    total_rows = len(merged_results)
    logger.info(
        "결과 병합 완료: %d개 DB에서 총 %d건, 에러 %d개",
        len(db_results),
        total_rows,
        len(db_errors),
    )

    # 집계 종합(S-3)이 먼저다 — DB마다 1행인 집계 결과는 순위 목록이 아니므로 재정렬하지 않는다.
    aggregates = plan_aggregate_synthesis(state, merged_results)
    ranking = (
        None if aggregates and aggregates.get("applied")
        else plan_global_ranking(state, merged_results, app_config)
    )

    return {
        "query_results": merged_results,
        # DB별 요약 — 종전에는 생성 즉시 버려졌다(반환 dict 미포함). 그룹별 섹션·처리
        # 현황이 이 값을 쓰므로 승격한다(D-176 · plans/82 §3.3).
        "db_result_summary": db_result_summary,
        # 순위 질의 전역 재정렬(plans/113 S-1) — 매 실행 덮어쓴다(직전 병합분이 남지 않게).
        "merged_ranking": ranking,
        # 집계 질의 종합(plans/113 S-3) — 매 실행 덮어쓴다.
        "merged_aggregates": aggregates,
        "error_message": error_summary if not db_results else None,
        "current_node": "result_merger",
    }


def _build_error_summary(
    db_results: dict[str, list],
    db_errors: dict[str, str],
) -> str | None:
    """에러 요약 메시지를 생성한다.

    Args:
        db_results: DB별 쿼리 결과
        db_errors: DB별 에러 메시지

    Returns:
        에러 요약 문자열 또는 None (에러 없음)
    """
    if not db_errors:
        return None

    error_parts = []
    for db_id, error_msg in db_errors.items():
        domain = get_domain_by_id(db_id)
        display_name = domain.display_name if domain else db_id
        error_parts.append(f"[{display_name}] {error_msg}")

    if not db_results:
        # 모든 DB 실패
        return "모든 DB 쿼리가 실패했습니다:\n" + "\n".join(error_parts)
    else:
        # 부분 실패 - 성공한 결과는 있으므로 경고로 처리
        return (
            "일부 DB 쿼리가 실패했습니다 "
            f"(성공: {len(db_results)}개, 실패: {len(db_errors)}개):\n"
            + "\n".join(error_parts)
        )


# ──────────────────────────────────────────────
# 순위 질의 전역 재정렬 (plans/113 S-1 · D-202 잔여 ①)
# ──────────────────────────────────────────────

#: 재정렬 미적용 사유 — 응답 존별 건수 줄에 그대로 실린다(사용자 문구).
REASON_KEY_MISMATCH = "DB별 정렬 기준이 서로 달라"
REASON_SQL_UNREADABLE = "실행 SQL의 정렬 기준을 판독하지 못해"
REASON_SET_OPERATION = "한 SQL이 여러 목록을 합친 형태(UNION 등)라"
REASON_OFFSET = "페이지 조회(OFFSET)라"
REASON_KEY_NOT_IN_RESULT = "정렬 키가 결과 칼럼에 없어"
REASON_KEY_NOT_ORDERABLE = "정렬 키 값이 수치·날짜가 아니어서"
REASON_NULL_KEYS = "정렬 키가 빈 값(NULL)인 행이 DB별 상위 N을 차지했을 수 있어"

#: DB·존마다 따로 순위를 원하는 표현 — 전역 한 표로 자르면 요청한 존별 목록이 사라진다
#: ("김포·여의도 각각 상위 5" · "존별 상위 10" · "5개씩"). 표면어 결정적 판정(D-202 조립기
#: 가드와 같은 방식 · 라우팅 의도 분류가 아니다 — D-004). 오탐(전역 순위 요청을 존별로 오인)의
#: 대가는 종전 이어 붙이기라 하방 안전하다 — 가드는 넓게 둔다.
_PER_SOURCE_RE = re.compile(
    r"각각|각\s*(?:존|db|센터|지역)|(?:존|db|센터|지역|위치)\s*(?:별|마다)"
    r"|\d+\s*(?:개|건|대|곳|행)?\s*씩",
    re.IGNORECASE,
)
#: NULL 기본 위치가 오름차순 앞인 엔진(MariaDB·MySQL은 NULL을 가장 작은 값으로 본다).
_NULLS_SMALLEST_ENGINES = frozenset({"mariadb", "mysql"})

_SET_OPERATIONS = frozenset({"UNION", "UNION ALL", "INTERSECT", "INTERSECT ALL",
                             "EXCEPT", "EXCEPT ALL", "MINUS"})
_ROW_LIMIT_KEYWORDS = frozenset({"LIMIT", "OFFSET", "FETCH"})
_DB2_ISOLATION = frozenset({"UR", "CS", "RS", "RR"})
_SOURCE_KEY = "_source_db"
#: 식별자로 받는 토큰 종류 — 이름 · 따옴표 식별자 · 비예약 키워드
#: (`host` 같은 낱말을 sqlparse가 키워드로 본다).
_NAME_KINDS = (sql_tokens.Name, sql_tokens.Literal.String.Symbol, sql_tokens.Keyword)
_ISO_TEMPORAL_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


@dataclass(frozen=True)
class RankSpec:
    """실행 SQL에서 읽은 순위 기준.

    Attributes:
        key: 정렬 키의 결과 칼럼명(따옴표 제거 원형) 또는 위치 번호 `#n`
        descending: 내림차순 여부(ASC·미표기는 오름차순)
        limit: 행 상한 N
        nulls: 명시된 NULL 위치(`"FIRST"`·`"LAST"`) — 미표기면 None(엔진 기본값)
    """

    key: str
    descending: bool
    limit: int
    nulls: str | None = None


def _norm(name: object) -> str:
    """결과 칼럼명 정규형 — `multi_db_executor._merge_results`와 같은 규칙(소문자·공백/밑줄 제거).

    DB2는 결과 칼럼의 라틴 문자를 소문자로 반환하므로(gp "IP주소" vs b0 "ip주소") 대조는
    정규형으로 한다.
    """
    return str(name).lower().replace(" ", "").replace("_", "")


def _flatten_top_level(group: Any) -> list[Any]:
    """최외곽 토큰을 평탄화한다 — 괄호(서브쿼리·CTE 본문·함수 인자)는 한 덩어리로 둔다.

    괄호 안의 `ORDER BY`·`LIMIT`는 최외곽 절이 아니므로 대조 대상이 아니다. 공백·주석은 뺀다.
    """
    out: list[Any] = []
    for tok in group.tokens:
        if isinstance(tok, sql_ast.Parenthesis):
            out.append(tok)
        elif tok.is_group:
            out.extend(_flatten_top_level(tok))
        elif not tok.is_whitespace and tok.ttype not in sql_tokens.Comment:
            out.append(tok)
    return out


def _keyword(tok: Any) -> str | None:
    """키워드 정규형 — 여러 낱말 키워드(`ORDER BY`·`UNION ALL`)의 줄바꿈·이중 공백을 한 칸으로."""
    return " ".join(str(tok.normalized).split()) if tok.ttype in sql_tokens.Keyword else None


def _is_comma(tok: Any) -> bool:
    return tok.ttype in sql_tokens.Punctuation and tok.value == ","


def _integer(tok: Any) -> int | None:
    if tok.ttype in sql_tokens.Number.Integer:
        try:
            return int(tok.value)
        except ValueError:
            return None
    return None


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"`":
        return text[1:-1]
    return text


def _compact(toks: list[Any]) -> str:
    """토큰 열을 공백 없이 이은 대조용 문자열(대소문자 무시 비교에 쓴다)."""
    return "".join(t.value for t in toks).upper()


def _split_top_level(toks: list[Any]) -> list[list[Any]]:
    parts: list[list[Any]] = [[]]
    for tok in toks:
        if _is_comma(tok):
            parts.append([])
        else:
            parts[-1].append(tok)
    return parts


def _select_items(toks: list[Any], order_idx: int) -> list[tuple[str, str | None]]:
    """최외곽 SELECT 목록을 (식 대조 문자열, 출력 이름)으로 읽는다. 출력 이름을 모르면 None."""
    selects = [i for i, t in enumerate(toks[:order_idx]) if _keyword(t) == "SELECT"]
    if not selects:
        return []
    start = selects[-1] + 1
    end = next(
        (i for i in range(start, order_idx) if _keyword(toks[i]) == "FROM"), order_idx
    )
    items: list[tuple[str, str | None]] = []
    for part in _split_top_level(toks[start:end]):
        while part and _keyword(part[0]) in ("DISTINCT", "ALL"):
            part = part[1:]
        if not part:
            continue
        as_idx = next((i for i, t in enumerate(part) if _keyword(t) == "AS"), None)
        if as_idx is not None and as_idx + 1 < len(part):
            items.append((_compact(part[:as_idx]), _unquote(part[as_idx + 1].value)))
            continue
        name = _identifier_name(part)
        items.append((_compact(part), name))
    return items


def _identifier_name(toks: list[Any]) -> str | None:
    """(한정) 식별자 하나면 마지막 부분 이름, 아니면 None — `t.col` → `col`."""
    if not toks:
        return None
    for i, tok in enumerate(toks):
        expect_name = i % 2 == 0
        if expect_name:
            if not any(tok.ttype in kind for kind in _NAME_KINDS):
                return None
        elif not (tok.ttype in sql_tokens.Punctuation and tok.value == "."):
            return None
    if len(toks) % 2 == 0:
        return None
    return _unquote(toks[-1].value)


def parse_rank_spec(sql: str) -> tuple[RankSpec | None, str | None]:
    """실행 SQL의 최외곽 `ORDER BY` 첫 키·방향과 행 상한을 결정적으로 읽는다.

    Returns:
        - `(spec, None)`: 순위 형태(최외곽 ORDER BY + 행 상한)이고 판독했다
        - `(None, None)`: 순위 형태가 아니다(최외곽 ORDER BY 또는 행 상한 없음) — 재정렬 대상 아님
        - `(None, 사유)`: 순위 형태이나 판독 불가·적용 제외(UNION 등 · OFFSET)
    """
    try:
        statements = [s for s in sqlparse.parse(sql or "") if str(s).strip().strip(";").strip()]
    except Exception:  # noqa: BLE001 — 판독 실패는 사유로 돌려준다
        return None, REASON_SQL_UNREADABLE
    if len(statements) != 1:
        return None, REASON_SQL_UNREADABLE if statements else None
    toks = _flatten_top_level(statements[0])
    order_positions = [i for i, t in enumerate(toks) if _keyword(t) == "ORDER BY"]
    if not order_positions:
        return None, None
    order_idx = order_positions[-1]
    key_start = order_idx + 1

    key_end = next(
        (i for i in range(key_start, len(toks)) if _keyword(toks[i]) in _ROW_LIMIT_KEYWORDS),
        len(toks),
    )
    limit, offset, readable = _read_row_limit(toks[key_end:])
    if not readable:
        return None, REASON_SQL_UNREADABLE
    if limit is None:
        return None, None  # 정렬만 한 목록 — 순위(상위 N)가 아니다
    if any(_keyword(t) in _SET_OPERATIONS for t in toks):
        return None, REASON_SET_OPERATION  # G-3: 한 SQL이 여러 순위 목록을 내는 형태는 제외
    if offset:
        return None, REASON_OFFSET

    first_key = _split_top_level(toks[key_start:key_end])[0]
    order_toks = [t for t in first_key if t.ttype in sql_tokens.Keyword.Order]
    expr = [t for t in first_key if t.ttype not in sql_tokens.Keyword.Order]
    if not expr:
        return None, REASON_SQL_UNREADABLE
    order_text = " ".join(" ".join(str(t.normalized).upper().split()) for t in order_toks)
    descending = "DESC" in order_text
    nulls = (
        "FIRST" if "NULLS FIRST" in order_text
        else "LAST" if "NULLS LAST" in order_text
        else None
    )

    def _spec(key: str) -> RankSpec:
        return RankSpec(key=key, descending=descending, limit=limit, nulls=nulls)

    position = _integer(expr[0]) if len(expr) == 1 else None
    if position is not None:
        return _spec(f"#{position}"), None
    expr_text = _compact(expr)
    items = _select_items(toks, order_idx)
    for item_expr, item_name in items:
        if item_expr == expr_text and item_name:
            return _spec(item_name), None
    name = _identifier_name(expr)
    if name is None:
        return None, REASON_KEY_NOT_IN_RESULT  # 별칭 없는 식으로 정렬 — 결과 칼럼으로 못 잇는다
    if len(expr) > 1 and any(
        item_name and _norm(item_name) == _norm(name) and item_expr != expr_text
        for item_expr, item_name in items
    ):
        # `ORDER BY s.value`인데 결과의 `value`는 다른 식의 별칭(`s.avg AS value`) — 한정 이름은
        # 입력 칼럼을 가리키므로 결과 칼럼으로 이으면 다른 값으로 정렬하게 된다.
        return None, REASON_KEY_NOT_IN_RESULT
    return _spec(name), None


def _read_row_limit(tail: list[Any]) -> tuple[int | None, int, bool]:
    """ORDER BY 뒤 행 상한 절을 읽는다 → (limit, offset, 판독 성공).

    `LIMIT n [OFFSET m]` · `LIMIT m, n` · `[OFFSET m ROWS] FETCH FIRST|NEXT n ROW|ROWS ONLY`.
    """
    limit: int | None = None
    offset = 0
    i = 0
    while i < len(tail):
        tok = tail[i]
        kw = _keyword(tok)
        if tok.ttype in sql_tokens.Punctuation and tok.value == ";":
            i += 1
        elif kw == "LIMIT":
            n = _integer(tail[i + 1]) if i + 1 < len(tail) else None
            if n is None:
                return None, 0, False
            if i + 3 < len(tail) and _is_comma(tail[i + 2]):  # MariaDB `LIMIT m, n`
                m = _integer(tail[i + 3])
                if m is None:
                    return None, 0, False
                offset, limit = n, m
                i += 4
            else:
                limit = n
                i += 2
        elif kw == "OFFSET":
            m = _integer(tail[i + 1]) if i + 1 < len(tail) else None
            if m is None:
                return None, 0, False
            offset = m
            i += 2
            if i < len(tail) and _keyword(tail[i]) in ("ROW", "ROWS"):
                i += 1
        elif kw == "FETCH":
            words = tail[i + 1:i + 5]
            if (len(words) == 4 and _keyword(words[0]) in ("FIRST", "NEXT")
                    and _integer(words[1]) is not None
                    and _keyword(words[2]) in ("ROW", "ROWS") and _keyword(words[3]) == "ONLY"):
                limit = _integer(words[1])
                i += 5
            else:
                return None, 0, False
        elif kw == "WITH" and i + 1 < len(tail) and tail[i + 1].value.upper() in _DB2_ISOLATION:
            i += 2  # DB2 격리 수준 절(`WITH UR` 등) — 행 상한과 무관
        elif (kw == "FOR" and i + 2 < len(tail)
              and tail[i + 1].value.upper() in ("READ", "FETCH")
              and _keyword(tail[i + 2]) == "ONLY"):
            i += 3  # `FOR READ ONLY` / `FOR FETCH ONLY`
        else:
            return None, 0, False
    return limit, offset, True


def _orderable_values(rows: list[dict[str, Any]], column: str) -> dict[int, Any] | None:
    """정렬 키 값을 비교 가능한 값으로 바꾼다 — 행 위치 → 값(NULL은 None).

    수치(int·float·Decimal — bool 제외)와 날짜(date·datetime·ISO 8601 문자열)만 받는다.
    일반 문자열은 DB 정렬 규칙(collation)과 파이썬 순서가 달라 전역 순위를 보장할 수 없다.
    한 칼럼에 두 종류가 섞이면 None.
    """
    values: dict[int, Any] = {}
    kinds: set[str] = set()
    for idx, row in enumerate(rows):
        raw = row.get(column)
        if raw is None:
            values[idx] = None
            continue
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float, Decimal)):
            values[idx] = raw
            kinds.add("number")
        elif isinstance(raw, (date, datetime)):
            values[idx] = raw
            kinds.add("temporal")
        elif isinstance(raw, str) and _ISO_TEMPORAL_RE.match(raw.strip()):
            try:
                values[idx] = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            kinds.add("temporal")
        else:
            return None
    return values if len(kinds) <= 1 else None


def _resolve_column(rows: list[dict[str, Any]], key: str) -> str | None:
    """RankSpec 키를 병합 행의 실제 칼럼명으로 잇는다(정규형 대조 · 위치 번호는 첫 행 순서)."""
    if not rows:
        return None
    columns = [c for c in rows[0].keys() if c != _SOURCE_KEY]
    if key.startswith("#"):
        try:
            pos = int(key[1:])
        except ValueError:
            return None
        return columns[pos - 1] if 1 <= pos <= len(columns) else None
    target = _norm(key)
    for row in rows:
        for col in row.keys():
            if col != _SOURCE_KEY and _norm(col) == target:
                return col
    return None


def _skip(reason: str, detail: str) -> dict[str, Any]:
    logger.warning(
        "결과 병합: 순위 질의 전역 재정렬 미적용 — %s DB별 결과를 이어 붙인다(%s)", reason, detail
    )
    return {"applied": False, "reason": reason}


def plan_global_ranking(
    state: AgentState, merged_rows: list[dict[str, Any]], app_config: AppConfig | None = None,
) -> dict[str, Any] | None:
    """순위 질의면 전 DB 결과를 전역 재정렬·재LIMIT한다 (plans/113 S-1).

    Args:
        state: 이번 실행 상태(`db_results`·`db_executed_sqls`·`template_structure`)
        merged_rows: `_merge_results` 병합 행(`_source_db` 태그 포함)
        app_config: 앱 설정(기본 행 상한 — 안전 상한은 순위 요청이 아니다)

    Returns:
        - None: 순위 형태가 아님(행 있는 DB 2곳 미만 · 양식 턴 · ORDER BY+행 상한 없음 ·
          기본 안전 상한)
        - `{"applied": True, key, descending, limit, rows, source_row_count, db_ids}`
        - `{"applied": False, "reason": 사유}`: 순위 형태이나 적용 조건 불성립
          (종전 이어 붙이기 유지)
    """
    # 양식 턴(폼필)은 행 전체가 산출물이다 — 순위로 자르지 않는다.
    if state.get("template_structure"):
        return None
    db_results = state.get("db_results") or {}
    with_rows = [d for d, rows in db_results.items() if rows]
    if len(with_rows) < 2 or not merged_rows:
        return None
    if _PER_SOURCE_RE.search(surface_query_for_judgment(state)):
        logger.info(
            "결과 병합: DB·존별 순위 요청 표현 — 전역 재정렬하지 않고 DB별 결과를 이어 붙인다"
        )
        return None

    cap = getattr(getattr(app_config, "query", None), "default_limit", None)
    cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else None
    sqls = state.get("db_executed_sqls") or {}

    specs: dict[str, RankSpec] = {}
    reasons: dict[str, str] = {}
    missing = [d for d in with_rows if not sqls.get(d)]
    for db_id in with_rows:
        if db_id in missing:
            continue
        spec, reason = parse_rank_spec(sqls[db_id])
        if spec is not None and cap is not None and spec.limit >= cap:
            spec = None  # 기본(안전) 상한 — 사용자가 요청한 상위 N이 아니다
        if spec is not None:
            specs[db_id] = spec
        elif reason:
            reasons[db_id] = reason
    if not specs and not reasons:
        return None  # 어느 DB도 순위 형태가 아니다 — 종전 이어 붙이기(노트 없음)

    if missing:
        # 순위 형태인 DB가 있는데 다른 DB의 실행 SQL을 모른다 — 기준 대조 불가
        return _skip(REASON_SQL_UNREADABLE, f"실행 SQL 미기록 DB={missing}")
    if reasons:
        first = next(iter(reasons.values()))
        return _skip(first, f"DB별 사유={reasons}")
    if len(specs) != len(with_rows):
        return _skip(REASON_KEY_MISMATCH, f"순위 형태 DB={sorted(specs)} · 행 있는 DB={with_rows}")
    distinct = {(_norm(s.key) if not s.key.startswith("#") else s.key, s.descending, s.limit)
                for s in specs.values()}
    if len(distinct) != 1:
        return _skip(REASON_KEY_MISMATCH, f"DB별 기준={ {d: s for d, s in specs.items()} }")

    spec = next(iter(specs.values()))
    column = _resolve_column(merged_rows, spec.key)
    if column is None:
        return _skip(REASON_KEY_NOT_IN_RESULT, f"key={spec.key}")
    values = _orderable_values(merged_rows, column)
    if values is None:
        return _skip(REASON_KEY_NOT_ORDERABLE, f"key={column}")
    truncated_by_nulls = [
        db_id for db_id in with_rows
        if _nulls_lead(specs[db_id], db_id)
        and len(db_results[db_id]) >= specs[db_id].limit
        and any(values[i] is None for i, r in enumerate(merged_rows) if r.get(_SOURCE_KEY) == db_id)
    ]
    if truncated_by_nulls:
        # 그 DB는 NULL을 앞에 둔 채 N행에서 잘렸다 — 값 있는 상위 행이 병합에 오지 않았을 수 있어
        # "전체 기준 상위 N"을 보장할 수 없다.
        return _skip(REASON_NULL_KEYS, f"key={column} · DB={truncated_by_nulls}")

    ranked = _rank(merged_rows, values, descending=spec.descending)
    if ranked is None:
        return _skip(REASON_KEY_NOT_ORDERABLE, f"key={column} (비교 불가 값 혼재)")
    top = ranked[: spec.limit]
    logger.info(
        "결과 병합: 순위 질의 전역 재정렬 적용 key=%s desc=%s limit=%d — %d개 DB %d건 → %d건",
        column, spec.descending, spec.limit, len(with_rows), len(merged_rows), len(top),
    )
    return {
        "applied": True,
        "key": column,
        "descending": spec.descending,
        "limit": spec.limit,
        "rows": top,
        "source_row_count": len(merged_rows),
        "db_ids": with_rows,
    }


def _nulls_lead(spec: RankSpec, db_id: str) -> bool:
    """이 DB의 정렬에서 NULL이 앞(상위 N 쪽)에 오는지 — 명시 `NULLS FIRST/LAST`가 우선이다.

    기본값: PostgreSQL·DB2는 NULL을 가장 큰 값으로 봐 내림차순 앞, MariaDB·MySQL은 가장 작은
    값으로 봐 오름차순 앞이다.
    """
    if spec.nulls is not None:
        return spec.nulls == "FIRST"
    domain = get_domain_by_id(db_id)
    engine = str(getattr(domain, "db_engine", "") or "").lower()
    if engine in _NULLS_SMALLEST_ENGINES:
        return not spec.descending
    return spec.descending


def _rank(
    rows: list[dict[str, Any]], values: dict[int, Any], *, descending: bool,
) -> list[dict[str, Any]] | None:
    """NULL은 뒤로, 동률은 레지스트리 DB 선언 순(같은 DB 안은 원래 순서)으로 정렬한다."""
    declared = {db_id: i for i, db_id in enumerate(get_registry().db_ids())}
    tie_order = sorted(
        range(len(rows)),
        key=lambda i: (declared.get(str(rows[i].get(_SOURCE_KEY)), len(declared)), i),
    )
    present = [i for i in tie_order if values[i] is not None]
    missing = [i for i in tie_order if values[i] is None]
    try:
        # reverse=True도 안정 정렬이다 — 같은 값의 원래(동률) 순서가 유지된다.
        present.sort(key=lambda i: values[i], reverse=descending)
    except TypeError:
        return None
    return [rows[i] for i in present + missing]


# ──────────────────────────────────────────────
# 집계 질의 종합 (plans/113 S-3 · G-5 (가))
# ──────────────────────────────────────────────

#: 집계 종합 미적용 사유 — 응답 존별 줄에 그대로 실린다(사용자 문구).
REASON_AGG_UNREADABLE = "실행 SQL의 집계 항목을 판독하지 못해"
REASON_AGG_MISMATCH = "DB별 집계 항목이 서로 달라"

_AGG_FUNCS = frozenset({"COUNT", "SUM", "MAX", "MIN", "AVG"})
#: 전체 값을 코드가 계산하는 집계(건수·합계는 더하고, 최대·최소는 그중 최대·최소).
#: 평균·중복 제거 건수·그 밖의 식은 DB별로만 싣는다 — 평균은 행 수 가중 정보가 없어 재집계할 수
#: 없고(I-3), 중복 제거 건수는 같은 대상이 두 DB에 있으면 더한 값이 틀린다.
_TOTAL_RULES = {"COUNT": "sum", "SUM": "sum", "MAX": "max", "MIN": "min"}


def parse_aggregate_items(sql: str) -> tuple[list[str] | None, str | None]:
    """실행 SQL 최외곽 SELECT 목록의 항목별 집계 종류를 결정적으로 읽는다(LLM 0).

    Returns:
        - `(종류 목록, None)`: 집계 스칼라 질의 — 항목 순서대로 `COUNT`·`SUM`·`MAX`·`MIN`·`AVG`·
          `COUNT_DISTINCT`·`OTHER`(집계를 감싼 식·상수 등)
        - `(None, None)`: 집계 스칼라 질의가 아니다(최외곽 GROUP BY · 집계 항목 0개)
        - `(None, 사유)`: 판독 불가(문장 수 · UNION 등)
    """
    try:
        statements = [s for s in sqlparse.parse(sql or "") if str(s).strip().strip(";").strip()]
    except Exception:  # noqa: BLE001 — 판독 실패는 사유로 돌려준다
        return None, REASON_AGG_UNREADABLE
    if len(statements) != 1:
        return None, REASON_AGG_UNREADABLE if statements else None
    toks = _flatten_top_level(statements[0])
    if any(_keyword(t) in _SET_OPERATIONS for t in toks):
        return None, REASON_AGG_UNREADABLE
    if any(_keyword(t) == "GROUP BY" for t in toks):
        return None, None  # 그룹별 집계 — DB 간 그룹 대조는 이 범위 밖
    selects = [i for i, t in enumerate(toks) if _keyword(t) == "SELECT"]
    if not selects:
        return None, REASON_AGG_UNREADABLE
    start = selects[-1] + 1
    end = next((i for i in range(start, len(toks)) if _keyword(toks[i]) == "FROM"), len(toks))
    kinds: list[str] = []
    for part in _split_top_level(toks[start:end]):
        as_idx = next((i for i, t in enumerate(part) if _keyword(t) == "AS"), None)
        expr = part[:as_idx] if as_idx is not None else part
        kinds.append(_aggregate_kind(expr))
    if not kinds or all(k == "OTHER" for k in kinds):
        return None, None
    return kinds, None


def _aggregate_kind(expr: list[Any]) -> str:
    """항목 식 전체가 집계 함수 호출 하나(`COUNT(*)`·`MAX(x)`)면 그 종류, 아니면 OTHER."""
    if len(expr) != 2 or not isinstance(expr[1], sql_ast.Parenthesis):
        return "OTHER"
    func = str(expr[0].value).upper()
    if func not in _AGG_FUNCS:
        return "OTHER"
    inner = [t for t in _flatten_top_level(expr[1]) if t.value not in ("(", ")")]
    if func == "COUNT" and inner and _keyword(inner[0]) == "DISTINCT":
        return "COUNT_DISTINCT"
    return func


def _number(value: Any) -> Any:
    """집계 값 — 수치(int·float·Decimal, bool 제외)면 그대로, NULL이면 None, 그 밖은 판정 불가."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("bool")
    if isinstance(value, Decimal):
        return float(value)  # float와 섞여도 더할 수 있게(Decimal + float는 TypeError)
    if isinstance(value, (int, float)):
        return value
    raise TypeError(type(value).__name__)


def plan_aggregate_synthesis(
    state: AgentState, merged_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """집계 질의면 DB별 값과 전체 값을 코드가 계산한다 (plans/113 S-3).

    적용: 행 있는 DB가 2곳 이상 · DB마다 정확히 1행 · 각 DB 실행 SQL이 최외곽 GROUP BY 없는 집계
    질의 · 항목 수·집계 종류·칼럼(정규형)이 전 DB에서 같다. 전체 값은 건수·합계(더함)·최대·최소만
    계산하고, 평균·중복 제거 건수·그 밖의 식은 DB별 값만 싣는다(전체 평균 생성 금지 — 행 수 가중
    정보 없음 · G-5 (가)). 원본 병합(`query_results`)은 바꾸지 않는다(G-4).

    Returns:
        - None: 집계 종합 대상 아님(양식 턴 · 행 있는 DB 2곳 미만 · DB당 1행 아님 · 집계 형태 아님)
        - `{"applied": True, "columns": [{column, kind, per_db: {db_id: 값}, total}], "db_ids"}`
          (`total`은 전체 값을 만들지 않는 종류면 None)
        - `{"applied": False, "reason": 사유}`
    """
    if state.get("template_structure"):
        return None
    db_results = state.get("db_results") or {}
    with_rows = [d for d, rows in db_results.items() if rows]
    if len(with_rows) < 2 or not merged_rows:
        return None
    if any(len(db_results[d]) != 1 for d in with_rows):
        return None
    sqls = state.get("db_executed_sqls") or {}

    kinds_by_db: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    for db_id in with_rows:
        sql = sqls.get(db_id)
        if not sql:
            reasons[db_id] = REASON_AGG_UNREADABLE
            continue
        kinds, reason = parse_aggregate_items(sql)
        if kinds is not None:
            kinds_by_db[db_id] = kinds
        elif reason:
            reasons[db_id] = reason
    if not kinds_by_db:
        return None  # 어느 DB도 집계 질의가 아니다 — 종전 그대로(노트 없음)
    if reasons:
        return _skip_agg(next(iter(reasons.values())), f"DB별 사유={reasons}")
    if len(kinds_by_db) != len(with_rows):
        return _skip_agg(
            REASON_AGG_MISMATCH, f"집계 DB={sorted(kinds_by_db)} · 행 있는 DB={with_rows}"
        )

    first = with_rows[0]
    kinds = kinds_by_db[first]
    columns_by_db = {d: [c for c in db_results[d][0].keys() if c != _SOURCE_KEY] for d in with_rows}
    shapes = {
        (tuple(kinds_by_db[d]), tuple(_norm(c) for c in columns_by_db[d])) for d in with_rows
    }
    if len(shapes) != 1 or len(kinds) != len(columns_by_db[first]):
        return _skip_agg(REASON_AGG_MISMATCH, f"DB별 형태={shapes}")

    columns: list[dict[str, Any]] = []
    for pos, kind in enumerate(kinds):
        per_db: dict[str, Any] = {}
        try:
            for d in with_rows:
                per_db[d] = _number(db_results[d][0][columns_by_db[d][pos]])
        except TypeError:
            continue  # 수치가 아닌 항목(식·문자열)은 종합하지 않는다
        rule = _TOTAL_RULES.get(kind)
        present = [v for v in per_db.values() if v is not None]
        total: Any = None
        if rule == "sum" and present:
            total = sum(present)
        elif rule == "max" and present:
            total = max(present)
        elif rule == "min" and present:
            total = min(present)
        columns.append({
            "column": columns_by_db[first][pos], "kind": kind, "per_db": per_db, "total": total,
        })
    if not columns:
        return None
    logger.info(
        "결과 병합: 집계 질의 DB별·전체 값 계산 — %d개 DB · 항목 %s",
        len(with_rows), [(c["column"], c["kind"]) for c in columns],
    )
    return {"applied": True, "columns": columns, "db_ids": with_rows,
            "source_row_count": len(merged_rows)}


def _skip_agg(reason: str, detail: str) -> dict[str, Any]:
    logger.warning("결과 병합: 집계 종합 미적용 — %s DB별 값만 싣는다(%s)", reason, detail)
    return {"applied": False, "reason": reason}
