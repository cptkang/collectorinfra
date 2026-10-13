"""P층(축소) — 빈 결과 판별 · 허용값 탐침 (plans/152 §3.3 · W3).

이 모듈은 네트워크 코드를 갖지 않는다. 게이트웨이 호출은 주입받은 `call_tool(name, args) -> dict`
(MCP 봉투 — W2 제공)로만 하고, 매 호출 전에 `budget_ok()`(U층과 같은 예산)를 묻는다. 결과는
`probe.jsonl` 행 dict 목록이다 — 디스크 쓰기·치환은 호출 측(위생 관문)이 한다.

① 빈 결과 판별(`empty_result_probes`): U층에서 오류 없이 0행을 받은 호출을 **같은 인자**로 직접 다시
   부른다. 처리기 provenance 인자에는 기간 키가 빠져 있으므로(`apm_query` 3520행대) 봉투 `window`의
   끝·길이를 `reference_time`·`lookback_minutes`로 되살려 붙인다. 과거 고정 창(끝이 송신 시각보다
   `FIXED_WINDOW_MIN_LAG_MIN`분 넘게 앞)일 때만 부른다 — 상대 창(끝 = 조회 시각)은 다시 부르면 다른
   구간이라 대조가 성립하지 않는다. 직접 호출도 0행이면 `no_data`(데이터 없음 — 판정이
   `empty_result`를 붙이지 않는다), 행이 있으면 `rows_present`(처리기가 받은 결과를 잘못 읽음)다.
   무거운 등급 시나리오(`heavy`)의 0행 호출은 다시 부르지 않는다(`skipped` · `heavy` — 부하 한도).
② 허용값 탐침(`allowed_value_probes` · 134 W10): `interval_minute`·`sort_by_metrics`·`max_row`
   후보값을 1회씩 부르고 수락·거부(게이트웨이 오류 코드·사유)를 남긴다. 반복 측정은 없다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from .record import TurnRecord
from .scenarios import RegistryInfo

CallTool = Callable[[str, dict[str, Any]], dict[str, Any]]
BudgetOk = Callable[[], bool]

KST = timezone(timedelta(hours=9), "KST")
#: 과거 고정 창 판정 — 끝이 턴 송신 시각보다 이만큼(분) 넘게 앞서야 한다(상대 창은 끝 ≈ 조회 시각).
FIXED_WINDOW_MIN_LAG_MIN = 5
#: 오류 사유 문자열 상한(자) — 게이트웨이 사유를 그대로 싣되 길이만 자른다(치환은 위생 관문).
REASON_MAX = 200

#: 허용값 탐침 — (탐침 이름, 레지스트리 보기, 그 보기 조건 이름, 후보값). 도구·고정 인자·도구 인자
#: 이름은 레지스트리(`views[].tool`·`fixed_args`·`args[].tool_arg`)에서 읽는다(D-053 — 사본 금지).
#: 후보: `interval_minute` 게이트웨이 기본 5 주변 · `sort_by_metrics` 스펙 기본 `calls`와 카탈로그
#: 정렬 이름(`spec/CAPABILITY-MAP-134.md` 표 B-2) · `max_row`(조건 `n`) 기본 10 과 구 사본 상한
#: 1000.
ALLOWED_VALUE_PROBES: tuple[tuple[str, str, str, tuple[Any, ...]], ...] = (
    ("interval_minute", "apm.runtime", "interval_minute", (1, 5, 10, 30, 60)),
    ("sort_by_metrics", "apm.app_stats", "sort_by",
     ("calls", "count", "averageResponseTime", "maxResponseTime", "errCount", "failures",
      "responseTime")),
    ("max_row", "apm.app_stats", "n", (1, 10, 100, 1000)),
)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt


def _rows(envelope: Mapping[str, Any]) -> int:
    count = envelope.get("row_count")
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    rows = envelope.get("rows")
    return len(rows) if isinstance(rows, list) else 0


def _reason(envelope: Mapping[str, Any]) -> str:
    for key in ("reason", "message", "detail"):
        value = envelope.get(key)
        if isinstance(value, str) and value:
            return value[:REASON_MAX]
    return ""


def _invoke(call_tool: CallTool, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """호출 1회 → {status, rows, error, reason}. 호출 예외는 삼키고 유형만 남긴다(독립 수집)."""
    try:
        envelope = call_tool(tool, args)
    except Exception as e:  # noqa: BLE001 — 탐침 하나의 실패가 나머지를 막지 않는다
        return {"status": "error", "rows": None, "error": type(e).__name__,
                "reason": str(e)[:REASON_MAX]}
    if not isinstance(envelope, Mapping):
        return {"status": "error", "rows": None, "error": "bad_envelope", "reason": ""}
    error = envelope.get("error")
    if error:
        return {"status": "error", "rows": None, "error": str(error),
                "reason": _reason(envelope)}
    return {"status": "ok", "rows": _rows(envelope), "error": None, "reason": ""}


def fixed_window_args(window: Any, sent_at: Any) -> tuple[dict[str, Any] | None, str]:
    """봉투 창 → 재호출 기간 인자(과거 고정 창일 때만) · 아니면 (None, 건너뛴 사유)."""
    if not isinstance(window, Mapping):
        return None, "no_window"
    if {"current", "baseline"} <= set(window):
        return None, "compare_window"
    start, end = _parse_dt(window.get("start")), _parse_dt(window.get("end"))
    anchor = _parse_dt(sent_at)
    if start is None or end is None:
        return None, "no_window"
    if anchor is None:
        return None, "no_sent_at"
    if (anchor - end).total_seconds() / 60 <= FIXED_WINDOW_MIN_LAG_MIN:
        return None, "relative_window"
    minutes = window.get("minutes")
    if not isinstance(minutes, int) or minutes <= 0:
        minutes = max(1, round((end - start).total_seconds() / 60))
    return {"reference_time": end.isoformat(timespec="seconds"), "lookback_minutes": minutes}, ""


def empty_result_probes(records: Sequence[Mapping[str, Any]], call_tool: CallTool, *,
                        budget_ok: BudgetOk = lambda: True) -> list[dict[str, Any]]:
    """U층에서 0행을 받은 호출을 같은 인자로 다시 불러 「데이터 없음」을 확인한다.

    대상: 건너뛰지 않은 턴의 호출 중 오류 없이 `rows == 0`인 것. 행마다 `kind="empty_check"` ·
    `scenario_id` · `turn` · `call_index`(그 턴 `observed.calls` 순번 — 판정이 이것으로 짝짓는다) ·
    `tool` · `args`(재호출 인자 — 호출 `hostname`이 있으면 함께 싣는다) ·
    `status`(`no_data`·`rows_present`·`error`·`skipped`) · `rows` · `reason`을 낸다.
    `budget_ok()`가 거짓이면(예산 소진·가드 비정상 — 드라이버는 `guard.healthy`) 남은 호출은
    `skipped`(`budget`)로 적고 부르지 않는다. 무거운 등급 레코드는 `skipped`(`heavy`).
    """
    out: list[dict[str, Any]] = []
    for record in records:
        if (record.get("verdict") or {}).get("skipped"):
            continue
        calls = (record.get("observed") or {}).get("calls") or []
        for index, call in enumerate(calls):
            if not isinstance(call, Mapping) or call.get("error") or call.get("rows") != 0:
                continue
            base = {"kind": "empty_check", "scenario_id": record.get("scenario_id"),
                    "turn": record.get("turn"), "call_index": index,
                    "tool": call.get("tool")}
            if record.get("heavy"):
                out.append({**base, "status": "skipped", "rows": None, "reason": "heavy"})
                continue
            period, why = fixed_window_args(call.get("window"), record.get("sent_at"))
            if period is None:
                out.append({**base, "status": "skipped", "rows": None, "reason": why})
                continue
            args = {**dict(call.get("args") or {}), **period}
            if call.get("hostname") and not args.get("hostname"):
                # 대상은 provenance `hostname`에만 있다(처리기가 감사 인자와 따로 싣는다)
                args["hostname"] = call["hostname"]
            if not budget_ok():
                out.append({**base, "args": args, "status": "skipped", "rows": None,
                            "reason": "budget"})
                continue
            res = _invoke(call_tool, str(call.get("tool")), args)
            status = res["status"]
            if status == "ok":
                status = "no_data" if res["rows"] == 0 else "rows_present"
            out.append({**base, "args": args, "status": status, "rows": res["rows"],
                        "error": res["error"], "reason": res["reason"]})
    return out


def allowed_value_probes(call_tool: CallTool, registry: RegistryInfo, target: Mapping[str, Any],
                         *, budget_ok: BudgetOk = lambda: True,
                         probes: Sequence[tuple[str, str, str, Sequence[Any]]] = (
                             ALLOWED_VALUE_PROBES),
                         ) -> list[dict[str, Any]]:
    """허용값 후보를 1회씩 부른다(134 W10).

    Args:
        call_tool: 게이트웨이 도구 호출(W2)
        registry: 레지스트리 apm 보기(도구·고정 인자·도구 인자 이름의 정본)
        target: 대상 인자(예 `{"instance_name": <내부망 키트가 채운 인스턴스>}`) — 그대로 싣는다
        budget_ok: 매 호출 전 예산 확인(U층과 같은 예산)
        probes: (탐침 이름, 보기, 조건 이름, 후보값) — 기본 `ALLOWED_VALUE_PROBES`

    Returns:
        `kind="allowed_value"` 행 — `param` · `tool` · `arg` · `value` · `status`(`accepted`·
        `rejected`·`skipped`) · `rows` · `error` · `reason`. 예산이 떨어지면 나머지는 `skipped`.
    """
    out: list[dict[str, Any]] = []
    for param, view_id, arg_name, values in probes:
        view = registry.views.get(view_id)
        spec = next((a for a in getattr(view, "args", ()) if a.name == arg_name), None)
        if view is None or spec is None:
            out.append({"kind": "allowed_value", "param": param, "view": view_id,
                        "status": "skipped", "reason": "레지스트리에 보기·조건 없음"})
            continue
        tool_arg = spec.tool_arg or spec.name
        for value in values:
            base = {"kind": "allowed_value", "param": param, "view": view_id, "tool": view.tool,
                    "arg": tool_arg, "value": value}
            if not budget_ok():
                out.append({**base, "status": "skipped", "reason": "budget"})
                continue
            args = {**dict(view.fixed_args), **dict(target), tool_arg: value}
            res = _invoke(call_tool, view.tool, args)
            out.append({**base, "status": "accepted" if res["status"] == "ok" else "rejected",
                        "rows": res["rows"], "error": res["error"], "reason": res["reason"]})
    return out


def attach(records: Sequence[TurnRecord], rows: Sequence[Mapping[str, Any]]) -> None:
    """빈 결과 판별 행을 (시나리오 id, 턴)으로 레코드 `probe`에 붙인다(제자리 · 판정 재실행 전)."""
    by_turn: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("kind") == "empty_check":
            by_turn.setdefault((row.get("scenario_id"), row.get("turn")), []).append(dict(row))
    for record in records:
        found = by_turn.get((record.get("scenario_id"), record.get("turn")))
        if found:
            record["probe"] = [*(record.get("probe") or []), *found]


__all__ = [
    "ALLOWED_VALUE_PROBES",
    "FIXED_WINDOW_MIN_LAG_MIN",
    "BudgetOk",
    "CallTool",
    "allowed_value_probes",
    "attach",
    "empty_result_probes",
    "fixed_window_args",
]
