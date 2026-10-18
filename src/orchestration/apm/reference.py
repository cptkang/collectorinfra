"""앞 결과 행 참조 조각(참조 문구 · 후보 번호 · 추적 창).

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain.result_refs import ref_label, ref_time_ms
from src.orchestration.apm.common import MAX_SELECTION_CANDIDATES
from src.orchestration.apm.window import WindowPlan

# ── 앞 결과 행 참조 (plans/134 M-6 · 계약 §4.2) ──────────────────────────────────

#: 참조 종류별 문구 — (무엇이 없는가 · 세는 이름 · 행에 없는 칸 · 먼저 할 일)
_REF_TEXT: dict[str, tuple[str, str, str, str]] = {
    "profile_ref": ("프로파일을 볼 트랜잭션이", "트랜잭션", "프로파일 참조",
                    "느린 트랜잭션·WAS 이벤트(오류 기록) 목록을 먼저 조회한 뒤"
                    " 몇 번째인지 함께 물어 주세요"),
    "active_ref": ("상세를 볼 실행 중 요청이", "실행 중 요청", "실행 중 요청 참조",
                   "실행 중 서비스 목록을 먼저 조회한 뒤 몇 번째인지 함께 물어 주세요"),
    "guid": ("GUID가 있는 거래가", "거래", "GUID",
             "GUID 값을 직접 적어 주시거나 트랜잭션 목록을 먼저 조회한 뒤"
             " 몇 번째인지 함께 물어 주세요"),
}
#: GUID 추적의 행 시각 기준 창 키(게이트웨이 `apm_transaction_trace` 인자 — 계약 §2.3).
_AROUND_KEY = "around_ms"


#: 표 이름(되묻기 문구)에 싣는 task 질의 길이(자) — 보기 라벨이 없을 때만 쓴다.
_TABLE_LABEL_MAX = 40


@dataclass
class _RefPick:
    """참조 보기 1개의 선택 결과 — 도구 인자 · 행 hostname · 경과 문구 · 되묻기 문구."""

    args: dict[str, Any] = field(default_factory=dict)
    hostname: str | None = None
    note: str = ""
    unresolved: str = ""
    reference: dict[str, Any] = field(default_factory=dict)


def _numbered(candidates: list[dict[str, Any]]) -> str:
    """쓸 수 있는 후보 ≤`MAX_SELECTION_CANDIDATES`개 — 「1번 「…」 · 2번 「…」 외 N건」.

    번호는 표시 순서다(값 없는 행도 자리를 지킨다).
    """
    usable = [(i, e) for i, e in enumerate(candidates, 1) if e.get("value") is not None]
    shown = " · ".join(f"{i}번 「{ref_label(e)}」" for i, e in usable[:MAX_SELECTION_CANDIDATES])
    more = (f" 외 {len(usable) - MAX_SELECTION_CANDIDATES}건"
            if len(usable) > MAX_SELECTION_CANDIDATES else "")
    return shown + more


def _choose_text(candidates: list[dict[str, Any]]) -> str:
    """되묻기 꼬리 — 쓸 수 있는 후보 ≤`MAX_SELECTION_CANDIDATES`개(번호 = 표시 순서)."""
    shown = _numbered(candidates)
    return f" 몇 번째인지 함께 다시 물어 주세요: {shown}" if shown else ""


def _tables_text(candidates: list[dict[str, Any]], names: list[str]) -> str:
    """표가 여럿일 때의 되묻기 꼬리 — 표(이름)마다 표 안 번호로 후보 ≤3개(plans/134 R-1)."""
    parts: list[str] = []
    for table in dict.fromkeys(e.get("table") for e in candidates):
        name = names[table] if isinstance(table, int) and table < len(names) else ""
        shown = _numbered([e for e in candidates if e.get("table") == table])
        parts.append(f"「{name or f'표 {len(parts) + 1}'}」 {shown or '(고를 행 없음)'}")
    return " / ".join(parts)


def _trace_window(plan: WindowPlan, entry: dict[str, Any] | None) -> dict[str, Any]:
    """GUID 추적 창 — 사용자 기간 > 참조 행 시각(±5분 · 게이트웨이 기본) > 게이트웨이 기본 60분."""
    if plan.mode == "window":
        return dict(plan.args)
    when = ref_time_ms(entry) if entry else None
    return {_AROUND_KEY: when} if when is not None else {}
