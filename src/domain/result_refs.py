"""앞 결과 행 참조(plans/134 M-6) — 「앞 결과 N번째」가 가리킬 수 있는 행을 표시 순서대로 추린다.

**무엇인가.** 게이트웨이 행에는 다음 조회의 입력이 되는 참조 칸이 있다 — `profile_ref`
(트랜잭션 프로파일 · 느린 트랜잭션·WAS 이벤트·오류 기록 행) · `active_ref`(실행 중 요청 상세 ·
실행 중 서비스 행) · `guid`(연계 거래 추적 · 트랜잭션 행). 칸 이름은 게이트웨이 도구 계약
이름이다(벤더 중립).

**번호.** 후보의 번호(1부터)는 그 종류의 칸이 **있는** 행 사이의 표시 순서다. 값이 비어 있는 행(예
txid 없는 거래의 `profile_ref: null`)도 자리를 지켜 번호가 화면 표와 어긋나지 않는다 — 값 없음은
`value: None`으로 남기고 고른 쪽(처리기)이 「그 행에는 참조가 없다」로 되묻는다. 계획 LLM이 낸 순번
(`ref`)을 코드가 이 목록으로 검증한다 — 한국어 순번·지시어를 정규식으로 읽지 않는다(132 계약).

**소비처.** `context_resolver`(직전 턴 결과 → `conversation_context.previous_result_refs`)와
`apm_query`(같은 계획의 선행 task 행)가 같은 함수로 추린다 — 두 출처의 번호 규칙이 같다.

**표 경계.** 복합 턴의 `query_results`는 task별 표를 이어 붙인 목록이다. 집계기가 남긴 표 경계
(`RESULT_TABLES_KEY` — 표마다 `{"label", "rows"}`)가 있으면 표마다 따로 추리고 후보에 표 번호
(`table`)를 단다 — 번호는 사용자에게 보인 표 안의 순서다(plans/134 R-1). 행에는 칸을 더하지 않는다
(화면 표·CSV 칸 불변).

계층: domain — 순수 · I/O·LLM·전역 상태 0.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

#: 행 참조 칸(게이트웨이 도구 계약 이름) — 이 칸이 있는 행만 그 종류의 참조 후보다.
REF_KINDS: tuple[str, ...] = ("profile_ref", "active_ref", "guid")
#: 복합 턴 결과의 표 경계 — 집계기가 `conversation_context`에 남기는 키(표가 둘 이상일 때만).
RESULT_TABLES_KEY = "result_tables"
#: 후보 목록 옆에 싣는 표 이름 목록의 키(`previous_result_refs` 안 · 후보의 `table`이 가리킨다).
TABLE_LABELS_KEY = "tables"
#: 후보에 함께 남기는 칸 — 라벨 · 대상 서버(게이트웨이 정합 재검사 입력) · 기준 시각.
_KEEP_FIELDS: tuple[str, ...] = (
    "hostname", "source_id", "domain_id", "instance_id", "instance_name", "application", "txid",
    "start_time_ms", "end_time_ms", "time_ms", "response_time_ms",
)
#: 라벨에 싣는 문자열 칸 길이(자) — 되묻기 문구용(원값은 후보에 그대로 남는다).
_LABEL_TEXT_MAX = 60


def usable_ref(kind: str, value: Any) -> bool:
    """참조 값이 다음 조회에 쓸 수 있는 모양인가(참조 dict는 `txid` 필수 · GUID는 빈 값 아님)."""
    if kind == "guid":
        return isinstance(value, str) and bool(value.strip())
    return isinstance(value, Mapping) and value.get("txid") not in (None, "")


def extract_result_refs(rows: Any) -> dict[str, list[dict[str, Any]]]:
    """결과 행 → 참조 종류별 후보 목록(표시 순서 · 빈 종류는 싣지 않는다).

    Args:
        rows: 결과 행 목록(화면 표와 같은 순서)

    Returns:
        `{kind: [{"value": 참조 값 또는 None, <_KEEP_FIELDS>…}, …]}` — 참조 칸이 있는 행이
        없으면 빈 dict
    """
    out: dict[str, list[dict[str, Any]]] = {kind: [] for kind in REF_KINDS}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        kinds = [kind for kind in REF_KINDS if kind in row]
        if not kinds:
            continue
        base = {key: row[key] for key in _KEEP_FIELDS if row.get(key) not in (None, "")}
        for kind in kinds:
            value = row[kind]
            if usable_ref(kind, value):
                value = dict(value) if isinstance(value, Mapping) else str(value).strip()
            else:
                value = None
            out[kind].append({**base, "value": value})
    return {kind: entries for kind, entries in out.items() if entries}


def extract_table_refs(rows: Any, tables: Any) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """표 경계를 따라 표마다 후보를 추린다 — (종류별 후보 · 표 이름 목록).

    `tables`가 표 둘 이상이고 행 수의 합이 `rows` 길이와 같을 때만 나눈다. 그 밖이면(경계 없음 ·
    어긋남) 한 표로 보고 `extract_result_refs`와 같고 표 이름은 빈 목록이다.
    """
    valid = (isinstance(rows, list) and isinstance(tables, list) and len(tables) >= 2
             and all(isinstance(t, Mapping) and isinstance(t.get("rows"), int)
                     and not isinstance(t.get("rows"), bool) and t["rows"] >= 0 for t in tables))
    sizes: list[int] = [int(t["rows"]) for t in tables] if valid else []
    if not valid or sum(sizes) != len(rows):
        return extract_result_refs(rows), []
    out: dict[str, list[dict[str, Any]]] = {}
    start = 0
    for index, size in enumerate(sizes):
        for kind, entries in extract_result_refs(rows[start:start + size]).items():
            out.setdefault(kind, []).extend({**entry, "table": index} for entry in entries)
        start += size
    return out, [str(t.get("label") or "") for t in tables]


def ref_time_ms(entry: Mapping[str, Any]) -> int | None:
    """후보의 기준 시각(epoch ms) — 시작 → 끝 → 참조 시각 → 행 시각 순."""
    value = entry.get("value")
    nested = value.get("time_ms") if isinstance(value, Mapping) else None
    for raw in (entry.get("start_time_ms"), entry.get("end_time_ms"), nested,
                entry.get("time_ms")):
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
    return None


def ref_label(entry: Mapping[str, Any]) -> str:
    """되묻기·경과 문구에 쓰는 후보 한 줄(서버 · 서비스 · 시각 · 응답시간 · txid — 있는 것만)."""
    parts: list[str] = []
    host = entry.get("hostname") or entry.get("instance_name")
    if host:
        parts.append(str(host))
    app = str(entry.get("application") or "")
    if app:
        parts.append(app if len(app) <= _LABEL_TEXT_MAX else app[:_LABEL_TEXT_MAX] + "…")
    when = ref_time_ms(entry)
    if when is not None:
        parts.append(datetime.fromtimestamp(when / 1000).strftime("%m-%d %H:%M:%S"))
    elapsed = entry.get("response_time_ms")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
        parts.append(f"{elapsed:,.0f}ms")
    if entry.get("txid") not in (None, ""):
        parts.append(f"txid {entry['txid']}")
    return " · ".join(parts) or "(설명 칸 없음)"


__all__ = ["REF_KINDS", "RESULT_TABLES_KEY", "TABLE_LABELS_KEY", "extract_result_refs",
           "extract_table_refs", "ref_label", "ref_time_ms", "usable_ref"]
