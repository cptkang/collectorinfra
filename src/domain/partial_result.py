"""시간 상한에 걸린 턴의 **부분 결과** 응답 (plans/114 P-2 · 게이트 G-E).

## 왜 필요한가

조회는 끝났는데 **서술(LLM)에서 상한을 넘겨 죽는 턴**이 있다. 그러면 종전에는 오류만 나가고
이미 읽어 온 행은 버려졌다 — 직전 폐쇄망 run에서 55턴이 그랬고, 그중 41턴은 답변 앞부분
(p50 588자)을 이미 스트리밍한 뒤였다(`plans/114` §2.3). 사용자는 데이터를 보다가 오류를 받는다.

서술만 못 만들었을 뿐 **행은 손에 있다.** 이 모듈은 그 행을 LLM 없이 표로 렌더해
답을 내보낼 수 있게 한다. 여기서는 상태에서 행을 건지는 규칙과 표 렌더만 정하고,
어느 출구에서 쓸지는 `src/api/routes/query.py` 가 정한다.

## 사다리 단 대칭

행이 실리는 자리가 단마다 다르다 — 3단·단일 경로는 `query_results`/`organized_data`,
2단은 `task_results[*]` 다. 한쪽만 보면 그 단에서만 부분 결과가 나가는 비대칭이 된다
(CLAUDE.md 「단일/멀티 경로 대칭」).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

#: 표에 싣는 최대 행 수. 나머지는 CSV 내려받기로 간다 — 서술을 못 만든 턴에서
#: 표까지 길면 읽히지 않고, 행 전부는 어차피 내려받기 경로가 있다.
MAX_TABLE_ROWS = 20

#: 부분 결과 턴의 상태값. **`completed` 와 구별한다** — 기록기(`_turn_status`)와 측정
#: 하네스가 "서술이 없는 답"임을 알아야 한다. 하네스는 이 값을 `timeout` 과 같은 제거
#: 사유로 본다(`plans/114` P-2 확정 · D-241 사유 어휘는 늘리지 않는다).
PARTIAL_STATUS = "partial"


@dataclass(frozen=True)
class PartialAnswer:
    """상태에서 건진 부분 결과 한 벌."""

    rows: list[dict[str, Any]]
    #: 어디서 건졌는지 — `query_results` · `organized_data` · `task`
    source: str
    #: `source == "task"` 일 때 그 작업 id
    task_id: Optional[str] = None
    #: 행을 낸 하위 작업 수(2단). 1보다 크면 **마지막 작업의 결과만** 표로 나간다.
    tasks_with_rows: int = 0


def _row_list(value: Any) -> list[dict[str, Any]]:
    """dict 행만 추린다. 행이 아닌 값이 섞여 있어도 표가 깨지지 않게 한다."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, dict)]


def _rows_of(container: Mapping[str, Any]) -> list[dict[str, Any]]:
    """한 컨테이너(상태 또는 task 결과)에서 행을 건진다. 원시 행이 먼저다."""
    rows = _row_list(container.get("query_results"))
    if rows:
        return rows
    organized = container.get("organized_data")
    if isinstance(organized, Mapping):
        return _row_list(organized.get("rows"))
    return []


def extract_partial_answer(state: Optional[Mapping[str, Any]]) -> Optional[PartialAnswer]:
    """체크포인트 상태에서 부분 결과를 건진다. 행이 없으면 None.

    None 이면 **종전 오류 그대로** 나가야 한다 — 빈 표를 답이라고 내보내지 않는다.
    """
    if not isinstance(state, Mapping):
        return None

    rows = _row_list(state.get("query_results"))
    if rows:
        return PartialAnswer(rows=rows, source="query_results")

    organized = state.get("organized_data")
    if isinstance(organized, Mapping):
        rows = _row_list(organized.get("rows"))
        if rows:
            return PartialAnswer(rows=rows, source="organized_data")

    # 2단 — 하위 작업별 결과. 여러 작업이 행을 냈으면 **마지막 작업**을 쓴다.
    # 작업마다 컬럼이 다르므로 이어 붙이면 표가 아니라 잡동사니가 된다. 대신 몇 건 중
    # 어느 것인지를 렌더가 말한다.
    tasks = state.get("task_results")
    if isinstance(tasks, Mapping):
        picked: Optional[tuple[str, list[dict[str, Any]]]] = None
        count = 0
        for task_id, result in tasks.items():
            if not isinstance(result, Mapping):
                continue
            rows = _rows_of(result)
            if rows:
                count += 1
                picked = (str(task_id), rows)
        if picked is not None:
            return PartialAnswer(
                rows=picked[1], source="task", task_id=picked[0], tasks_with_rows=count
            )
    return None


def _columns(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """행들의 키 합집합(등장 순서 유지) — CSV 내려받기와 같은 규칙이다."""
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            name = str(key)
            if name not in seen:
                seen.add(name)
                columns.append(name)
    return columns


def _cell(value: Any) -> str:
    """셀 한 칸. `|` 는 표를 깨뜨리므로 탈출하고, 없는 값은 빈 칸이다."""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_partial_text(
    answer: PartialAnswer,
    *,
    limit_sec: Optional[float] = None,
    max_rows: int = MAX_TABLE_ROWS,
) -> str:
    """부분 결과를 사용자 응답 본문으로 렌더한다 (LLM 0).

    첫 줄은 **왜 표만 나가는지**다 — 사유 없이 형식만 바뀌면 사용자는 답이 잘렸는지
    원래 그런지 알 수 없다(CLAUDE.md 「침묵적 폴백 금지」).
    """
    limit = f"(설정 상한 {int(limit_sec)}초)" if limit_sec and limit_sec > 0 else ""
    lines = [
        f"서술 생성이 시간 상한{limit}을 넘어 표로 대신합니다. "
        "조회는 끝났고 아래가 그 결과입니다.",
    ]
    if answer.source == "task" and answer.tasks_with_rows > 1:
        lines.append(
            f"\n하위 작업 {answer.tasks_with_rows}건이 결과를 냈고, 그중 마지막 작업"
            f"(`{answer.task_id}`)의 결과입니다."
        )

    shown = answer.rows[:max_rows] if max_rows > 0 else answer.rows
    columns = _columns(shown)
    if columns:
        lines.append("")
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("|" + "|".join(["---"] * len(columns)) + "|")
        for row in shown:
            lines.append("| " + " | ".join(_cell(row.get(c)) for c in columns) + " |")

    total = len(answer.rows)
    if total > len(shown):
        lines.append(
            f"\n전체 {total:,}건 중 상위 {len(shown):,}건입니다. "
            "나머지는 CSV 내려받기로 확인하세요."
        )
    else:
        lines.append(f"\n총 {total:,}건입니다.")
    return "\n".join(lines)
