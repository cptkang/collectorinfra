"""불변식 (plans/123 V-4) — 모든 턴에 계산하는 결정적 「그럴듯한 오답」 신호.

**결정적이다. LLM 을 쓰지 않는다.** 응답 본문 · 실행 SQL(감사 로그 항목) · DB별 행 수 · 응답 고지
(`disclosures`) · 질의 원문 · 존 선택만 본다 — 결과 행(H-1)은 선언한 턴에서만 수집되므로 쓰지 않는다
(V-4 ②).

**트리아지 칸 → 활성 시점 편입.** 위반은 전부 행 칸 `invariant_violations` 에 싣고(트리아지),
군 헤더 `invariants:` 가 `active_from` 을 적은 불변식만 판정(`func_verdict`)에 넣는다. 소유 제품
수정이 랜딩되기 전에 켜면 남의 결함으로 불합격을 만들어 합격률을 오염시킨다(122 §1.1). 활성은
**응답 고지를 수집한 run**(`Observation.disclosures is not None` — W-8 이후 러너 · run R5~)에서만
성립한다 — 과거 run 재판정(J-4)에서는 선언이 활성이어도 트리아지로 남는다(그 run 의 제품에는 소유
수정이 없다).

**보류.** 모의 실행 · `kind: probe` · 환경 불일치 보류 턴(D-216 ③ — 러너가 `expect` 키만 걸러
불변식이 직접 거른다) · 역질문으로 끝난 턴 · 오류 턴은 계산하지 않는다. 응답 본문이 잘린 재판정 행은
계산하지 않고, 활성 불변식이 있으면 `invariant` 출처로 보류한다.

| 불변식 | 위반 | 활성(소유) |
|---|---|---|
| `limit_disclosed` | 실행 SQL 행 수 ≥ 그 SQL 의 LIMIT(질의 지정 건수 제외) + 상한 고지 없음 | W-1 |
| `zone_coverage_named` | 「전체」 질의 + 선택한 존 ⊊ 제시된 존 + 범위 고지 없음 | W-2 · W-5 |
| `empty_template_misuse` | 빈 결과 템플릿 + 조건이 식별자·기간·충돌 | S-4 |
| `overgeneralization` | 「모든·유일·전수」 단정 + 행 수 > 20(미리보기) | 121·TP-4.4 ② |
| `demonstrative_bulk` | 첫 요청의 지시어(「그 장비」) + 행 > 1 | G-6 · 106·H1 · S-7(b) |
| `upload_misattributed` | 업로드 없는 턴에 「업로드하신」 | 295feee |
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import yaml

from .assertions import (
    ENV_MISMATCH_NOTE_PREFIX,
    Observation,
    _contains_any,
    _ended_in_question,
    _Holds,
    _norm_text,
    disclosure_kinds,
    sql_body,
    undisclosed_text,
)
from .catalog import DB_REGISTRY_PATH, Group, Scenario, Turn

#: 결과 미리보기 행 수 — 요약 LLM 이 보는 표본(plans/123 §2.2 SW13 · 119·N-1).
PREVIEW_ROWS = 20

#: 상한 고지로 읽는 문구(kind 가 없는 과거 run · 문구로만 고지한 경로). 제품 W-1 문구 `상한(LIMIT
#: n)에 도달해 이후 행이 절단되었을 수 있습니다`·3단 CU-8 과 같은 뜻.
_LIMIT_NOTE_MARKERS = ("상한", "절단", "잘렸", "일부만")
#: 범위 고지로 읽는 문구 — 제품 W-2 `…만 조회했습니다. …은(는) 조회하지 않았습니다` · W-5 `전체가
#: 아니라`.
_SCOPE_NOTE_MARKERS = ("조회하지 않았", "전체가 아니", "만 조회")
#: 업로드 오귀속 문구 — 업로드가 없는 턴의 「업로드하신 양식」(run 20260923-103638 R1-03·R4-07C ·
#: D-264 ④).
_UPLOAD_MARKERS = ("업로드하신",)

#: 「전체」 범위어 — 제품 `has_all_scope_keyword` 와 같은 뜻의 하네스 사본(판정기는 제품 규칙을 빌려
#: 쓰지 않는다).
_ALL_SCOPE_RE = re.compile(r"전체|모든|전부|모두|전수")
#: 질의가 지정한 건수(명시 건수 · 상위 N · TOP N · N대 · N위). 「N건 이상·이하·초과·미만·넘…」은
#: 조건이지 건수 지정이 아니다(123 W-0 — 제품 `explicit_row_count` 와 독립으로 둔다: 제품 규칙이
#: 틀려도 여기서 잡힌다).
_COUNT_RE = re.compile(
    r"(\d[\d,]*)\s*(?:건|개|대|위|행|명)(?!\s*(?:이상|이하|초과|미만|넘|보다|까지|부터|이내|정도))"
)
_TOP_RE = re.compile(r"(?i)(?:상위|하위|top)\s*(\d[\d,]*)")
#: SQL 의 행 상한 — PostgreSQL `LIMIT n` · DB2 `FETCH FIRST n ROWS`. 바깥 절이 뒤에 오므로 마지막
#: 것을 쓴다.
_SQL_LIMIT_RE = re.compile(r"(?i)\blimit\s+(\d+)\b|\bfetch\s+first\s+(\d+)\s+rows?\b")
#: 식별자 조건 — 호스트·장비명(영문으로 시작 · `-`/`_` 로 이은 토막 · 숫자 포함) · IPv4.
_IDENTIFIER_RE = re.compile(r"(?<![\w-])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+(?![\w-])")
_IPV4_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
#: 기간 조건 — 연·월 · 상대 기간어.
_PERIOD_RE = re.compile(r"\d{2,4}\s*년|\d{1,2}\s*월|지난|이번\s*(?:주|달)|어제|오늘|작년|올해|분기")
#: 수치 경계 — 하한(넘·초과·이상)과 상한(미만·이하·아래). 하한 ≥ 상한이면 조건 충돌이다(R3-07).
_BOUND_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(넘|초과|이상|미만|이하|아래)")
#: 지시어 — 선행 대상이 있어야 풀리는 말.
_DEMONSTRATIVE_RE = re.compile(
    r"(?<![가-힣])(?:그|이|저|해당)\s*(?:장비|서버|호스트|머신)|(?<![가-힣])그거(?![가-힣])"
)


def effective_query(scenario: Scenario, turn_index: int) -> str:
    """이 턴까지 보낸 마지막 질의 원문.

    구조화 답변만 보낸 턴(존 선택)은 앞 턴의 원문을 다시 보낸다.
    """
    queries = [str(t.send.get("query") or "").strip() for t in scenario.turns[:turn_index]]
    return next((q for q in reversed(queries) if q), "")


def _first_request(scenario: Scenario, turn_index: int) -> bool:
    """이 턴까지 사용자가 한 가지 요청만 했다(존 선택 답변은 같은 요청의 연속이다)."""
    queries = {str(t.send.get("query") or "").strip() for t in scenario.turns[:turn_index]}
    return len(queries - {""}) <= 1


@lru_cache(maxsize=1)
def registry_zone_db_ids() -> tuple[str, ...]:
    """레지스트리에서 존(zone)이 배정된 DB.

    존 선택지를 기록하지 않은 행(과거 run)의 제시 존 대용이다.
    """
    try:
        raw = yaml.safe_load(DB_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    return tuple(
        str(entry["db_id"]) for entry in raw.get("databases") or []
        if isinstance(entry, dict) and entry.get("db_id") and entry.get("zone")
    )


def requested_counts(query: str) -> set[int]:
    """질의가 지정한 건수들 — 이 값과 같은 상한은 절단이 아니다(W-1 ②)."""
    found: set[int] = set()
    for regex in (_COUNT_RE, _TOP_RE):
        for match in regex.finditer(query or ""):
            digits = match.group(1).replace(",", "")
            if digits.isdigit():
                found.add(int(digits))
    return found


def sql_row_limit(sql: str) -> int | None:
    """SQL 의 행 상한(주석 제외 · 마지막 LIMIT/FETCH FIRST). 없으면 None."""
    matches = list(_SQL_LIMIT_RE.finditer(sql_body(sql)))
    if not matches:
        return None
    value = matches[-1].group(1) or matches[-1].group(2)
    return int(value) if value else None


def _sql_row_pairs(obs: Observation) -> list[tuple[str, int]]:
    """(실행 SQL, 그 SQL 의 행 수) — 감사 로그 항목(성공분). 항목이 없으면 done 의 SQL·행 수 1건."""
    pairs = [
        (str(entry.get("sql")), int(entry["row_count"]))
        for entry in obs.sql_entries
        if isinstance(entry, dict) and entry.get("sql") and entry.get("success") is not False
        and isinstance(entry.get("row_count"), int) and not isinstance(entry.get("row_count"), bool)
    ]
    if not pairs and obs.executed_sql and isinstance(obs.row_count, int):
        pairs = [(obs.executed_sql, obs.row_count)]
    return pairs


def _row_total(obs: Observation) -> int:
    return max(int(obs.row_count or 0), sum(int(c or 0) for c in obs.row_counts_by_db.values()))


# --- 불변식별 판정 — 위반이면 상세 dict, 아니면 None -----------------------------------------

def _limit_disclosed(scenario: Scenario, turn_index: int, query: str, obs: Observation,
                     mode: str) -> dict[str, Any] | None:
    requested = requested_counts(query)
    reached = []
    for sql, rows in _sql_row_pairs(obs):
        limit = sql_row_limit(sql)
        if limit and rows >= limit and limit not in requested:
            reached.append({"limit": limit, "rows": rows})
    if not reached:
        return None
    if "row_limit_reached" in disclosure_kinds(obs) or _contains_any(
        _norm_text(obs.response), _LIMIT_NOTE_MARKERS
    ):
        return None
    return {"reached": reached[:3]}


def _zone_coverage_named(scenario: Scenario, turn_index: int, query: str, obs: Observation,
                         mode: str) -> dict[str, Any] | None:
    if not _ALL_SCOPE_RE.search(query):
        return None
    selection = obs.zone_selection if isinstance(obs.zone_selection, dict) else {}
    selected = [str(d) for d in selection.get("selected") or [] if d]
    offered = [str(d) for d in selection.get("offered") or [] if d] or list(registry_zone_db_ids())
    if not selected or not set(selected) < set(offered):
        return None
    if {"scope_narrowed", "scope_partial"} & set(disclosure_kinds(obs)) or _contains_any(
        _norm_text(obs.response), _SCOPE_NOTE_MARKERS
    ):
        return None
    return {"selected": selected, "offered": offered}


def _conflicting_bounds(query: str) -> bool:
    lower = [float(n) for n, op in _BOUND_RE.findall(query) if op in ("넘", "초과", "이상")]
    upper = [float(n) for n, op in _BOUND_RE.findall(query) if op in ("미만", "이하", "아래")]
    return bool(lower and upper and max(lower) >= min(upper))


def _empty_template_misuse(scenario: Scenario, turn_index: int, query: str, obs: Observation,
                           mode: str) -> dict[str, Any] | None:
    if mode != "empty_template":
        return None
    conditions = []
    identifiers = [m.group(0) for m in _IDENTIFIER_RE.finditer(query)
                   if any(ch.isdigit() for ch in m.group(0))]
    if identifiers or _IPV4_RE.search(query):
        conditions.append("identifier")
    if _PERIOD_RE.search(query):
        conditions.append("period")
    if _conflicting_bounds(query):
        conditions.append("conflict")
    return {"conditions": conditions} if conditions else None


def _overgeneralization(scenario: Scenario, turn_index: int, query: str, obs: Observation,
                        mode: str) -> dict[str, Any] | None:
    rows = _row_total(obs)
    if rows <= PREVIEW_ROWS:
        return None
    marker = re.search(r"모든|유일|전수", undisclosed_text(obs))
    return {"marker": marker.group(0), "rows": rows} if marker else None


def _demonstrative_bulk(scenario: Scenario, turn_index: int, query: str, obs: Observation,
                        mode: str) -> dict[str, Any] | None:
    if not _first_request(scenario, turn_index) or not _DEMONSTRATIVE_RE.search(query):
        return None
    rows = _row_total(obs)
    return {"rows": rows} if rows > 1 else None


def _upload_misattributed(scenario: Scenario, turn_index: int, query: str, obs: Observation,
                          mode: str) -> dict[str, Any] | None:
    if scenario.upload or scenario.upload_generate or scenario.endpoint in ("file", "file_stream"):
        return None
    marker = _contains_any(obs.response, _UPLOAD_MARKERS)
    return {"marker": marker} if marker else None


_Check = Callable[[Scenario, int, str, Observation, str], dict[str, Any] | None]
#: 불변식 이름(`catalog.INVARIANTS`) → 판정 함수. 이름 집합은 테스트가 카탈로그 어휘와 대조한다.
CHECKS: dict[str, _Check] = {
    "limit_disclosed": _limit_disclosed,
    "zone_coverage_named": _zone_coverage_named,
    "empty_template_misuse": _empty_template_misuse,
    "overgeneralization": _overgeneralization,
    "demonstrative_bulk": _demonstrative_bulk,
    "upload_misattributed": _upload_misattributed,
}


def active_invariants(group: Group, obs: Observation) -> list[str]:
    """이 턴에서 판정에 들어가는 불변식.

    군 헤더가 `active_from` 을 적었고 run 이 응답 고지를 수집했을 때만이다.
    """
    if obs.disclosures is None:
        return []
    return [name for name, since in group.invariants.items() if since]


def evaluate_invariants(
    scenario: Scenario, turn_index: int, turn: Turn, obs: Observation, group: Group,
    *, mode: str, mock: bool, manual: _Holds,
) -> list[dict[str, Any]]:
    """이 턴의 불변식 위반 `{name, active, detail}` 목록(트리아지 칸).

    계산하지 않는 턴은 빈 목록이다.
    """
    if (mock or scenario.kind == "probe" or _ended_in_question(obs)
            or obs.status == "error" or obs.http_status >= 400
            or ENV_MISMATCH_NOTE_PREFIX in str(turn.expect.get("manual_review") or "")):
        return []
    active = set(active_invariants(group, obs))
    if obs.response_truncated:
        if active:
            manual.add(f"불변식 {sorted(active)} 를 판정하지 못했다 - 응답 본문이 원시 로그 상한"
                       "(4,000자)에서 잘렸다", "invariant")
        return []
    query = effective_query(scenario, turn_index)
    violations = []
    for name, check in CHECKS.items():
        detail = check(scenario, turn_index, query, obs, mode)
        if detail is not None:
            violations.append({"name": name, "active": name in active, "detail": detail})
    return violations
