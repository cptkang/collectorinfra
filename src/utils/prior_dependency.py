"""복합 질의 순차 의존 계약 — 선행 결과 게이트 · 사후 대조 · 경과 노트 (D-203 · plans/88).

`input_from`으로 선행 조회 결과에 의존하는 후속 task가 **선행이 비었을 때** 스코프 없이
전체 서버를 조회하는 침묵 오류(plans/88 §3 R-1)를 막는다. 판정은 전부 결정적(LLM 0회)이며
1단(deepagents 도구 경로)·2단(agent_orchestrator 계획 경로)이 **같은 함수**를 호출한다.

## 왜 `utils`인가
소비자가 orchestration 두 곳이지만, 스코프 컬럼 판정(`is_server_identity_col`)과 값 수집
규칙(`collect_prior_identity_values`)이 이미 `utils.query_gen_common`에 있어 같은 계층에 두면
역방향 의존이 생기지 않는다. `src.config`는 import하지 않는다 — 플래그·상한은 호출부가 넘긴다
(`prior_targets.py`와 같은 자세).

## 사유 코드
- `prior_failed`      선행 task가 error
- `prior_empty`       선행 행 0건
- `prior_no_identity` 행은 있으나 서버 식별 컬럼 없음(대상 확정 불가)
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.utils.query_gen_common import (
    _MAX_PRIOR_SCOPE_VALUES,
    _PRIOR_HOSTNAME_HINTS,
    collect_prior_identity_values,
    is_server_identity_col,
)

REASON_PRIOR_FAILED = "prior_failed"
REASON_PRIOR_EMPTY = "prior_empty"
REASON_PRIOR_NO_IDENTITY = "prior_no_identity"

# 노트 종류 — 응답 경과 블록·API 노출에 그대로 실린다(plans/88 §4.10).
NOTE_GATE = "gate"              # 선행 결과 게이트로 후속 미실행
NOTE_TRACE = "trace"            # 정상 주입 경과(선별 대수·컬럼)
NOTE_TRUNCATION = "truncation"  # 스코프 상한 절단
NOTE_POSTCHECK = "postcheck"    # 사후 대조(스코프 밖 제거·미조회 서버)
NOTE_SUFFICIENCY = "sufficiency"  # 78 W5 충족도 미달(병기)
NOTE_SCOPE_DB = "scope_db"      # DB별 스코프 분할로 미조회한 DB
NOTE_DECOMPOSE = "decompose"    # 분해 단계 경과(재분해·미적용·DAG 보정·폴백 사유)

# 순차 표지(plans/88 §4.2-b) — **판정에만** 쓴다. 코드가 자연어를 쪼개지 않는다. 좁게 못 박아
# 정상 단일 질의("김포 서버를 찾아줘")의 오탐은 재분해 1회 비용에 그치고 실행 경로는 바뀌지 않는다.
SEQUENTIAL_MARKERS: tuple[str, ...] = (
    "찾아 ", "찾아서", "찾은 ", "찾고 ", "조회한 후", "조회한 뒤", "조회해서", "조회하고 ",
    "그 서버", "해당 서버", "그 장비", "해당 장비", "그 중", "그중", "선별된", "앞서 조회",
)


def has_sequential_marker(query: str) -> bool:
    """질의에 '앞 결과가 뒤 조회의 대상'임을 시사하는 표지가 있는가 (결정적)."""
    text = (query or "").strip()
    if not text:
        return False
    padded = text + " "
    return any(m in padded for m in SEQUENTIAL_MARKERS)

# 경과 블록에 예시로 싣는 식별 값 상한 — 100개를 본문에 나열하지 않는다.
_NOTE_SAMPLE_VALUES = 10


class DependencyVerdict(BaseModel):
    """`input_from` 선행 결과 판정 (plans/88 §4.1). LLM 0회."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    reason: Optional[str] = None
    detail: str = ""
    source_task_ids: list[str] = Field(default_factory=list)
    scope_col: str = ""
    scope_values: list[str] = Field(default_factory=list)  # 상한 적용 후 값(대조 정본)
    scope_size: int = 0
    truncated: bool = False
    truncated_count: int = 0


class ScopeConformance(BaseModel):
    """후속 결과의 스코프 대조 (plans/88 §4.3)."""

    model_config = ConfigDict(extra="forbid")

    checked: bool
    result_col: str = ""
    outside: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


def _result_rows(res: Any) -> list[dict]:
    """task 결과에서 행 목록을 꺼낸다 — `subagents._prior_result_rows`와 같은 3-shape 규약.

    utils는 orchestration을 import할 수 없어 여기서 재구현한다(계층 방향).
    """
    if not isinstance(res, dict):
        return []
    rows = res.get("rows")
    if rows is None:
        rows = res.get("query_results")
    if rows is None:
        organized = res.get("organized_data") or {}
        rows = organized.get("rows", []) if isinstance(organized, dict) else []
    return [r for r in (rows or []) if isinstance(r, dict)]


def assess_prior_dependency(
    task: dict, prior: dict | None, *, max_values: int = _MAX_PRIOR_SCOPE_VALUES,
) -> Optional[DependencyVerdict]:
    """`input_from` 선행 결과를 결정적으로 판정한다.

    Args:
        task: 현재 TaskSpec(dict). `input_from`이 비면 판정 대상이 아니다(None).
        prior: 완료된 선행 결과 {task_id: 결과 dict}
        max_values: 스코프 값 상한(현행 `_MAX_PRIOR_SCOPE_VALUES`=100). 초과분은 절단으로 보고한다.

    Returns:
        판정, 또는 `input_from`이 없으면 None
    """
    input_from = [str(t) for t in (task.get("input_from") or []) if t]
    if not input_from:
        return None

    prior = prior or {}
    failed: list[str] = []
    rows: list[dict] = []
    for tid in input_from:
        res = prior.get(tid)
        # 선행 결과가 없거나(미실행) error면 실패 — 부분 스코프로 조용히 좁히지 않는다(SPEC 가정 3).
        if not isinstance(res, dict) or res.get("error"):
            failed.append(tid)
            continue
        rows.extend(_result_rows(res))

    ids_text = ", ".join(input_from)
    if failed:
        first = prior.get(failed[0]) if isinstance(prior.get(failed[0]), dict) else {}
        err = str((first or {}).get("error") or "결과 없음")[:200]
        return DependencyVerdict(
            ok=False, reason=REASON_PRIOR_FAILED, source_task_ids=input_from,
            detail=f"선행 작업({', '.join(failed)})이 실패해 이 단계를 실행하지 않았습니다: {err}",
        )
    if not rows:
        return DependencyVerdict(
            ok=False, reason=REASON_PRIOR_EMPTY, source_task_ids=input_from,
            detail=f"선행 작업({ids_text})의 결과가 0건이라 이 단계를 실행하지 않았습니다.",
        )

    col, all_values = collect_prior_identity_values({"_": rows}, limit=None)
    if not col or not all_values:
        return DependencyVerdict(
            ok=False, reason=REASON_PRIOR_NO_IDENTITY, source_task_ids=input_from,
            detail=(
                f"선행 작업({ids_text}) 결과에 서버 식별 컬럼이 없어 대상을 확정할 수 없습니다 "
                f"(행 {len(rows)}건)."
            ),
        )

    kept = all_values[:max_values]
    truncated_count = max(0, len(all_values) - len(kept))
    detail = f"선행 작업({ids_text}) 결과 {len(all_values)}대({col})로 대상을 한정했습니다."
    if truncated_count:
        detail += f" 상한 {max_values}대 초과분 {truncated_count}대는 제외됐습니다."
    return DependencyVerdict(
        ok=True, source_task_ids=input_from, scope_col=col, scope_values=kept,
        scope_size=len(kept), truncated=truncated_count > 0, truncated_count=truncated_count,
        detail=detail,
    )


def verdict_note(verdict: DependencyVerdict, task_id: str) -> dict:
    """판정을 경과 노트 1건으로 만든다(체크포인터 직렬화 대상 — dict)."""
    kind = NOTE_TRACE if verdict.ok else NOTE_GATE
    note = {
        "kind": kind,
        "task_id": task_id,
        "reason": verdict.reason or "ok",
        "detail": verdict.detail,
        "source_task_ids": list(verdict.source_task_ids),
    }
    if verdict.ok:
        note["scope_col"] = verdict.scope_col
        note["scope_size"] = verdict.scope_size
        note["sample"] = list(verdict.scope_values[:_NOTE_SAMPLE_VALUES])
        if verdict.truncated:
            note["truncated_count"] = verdict.truncated_count
    return note


def skip_result(verdict: DependencyVerdict, *, guidance: str = "") -> dict:
    """미실행 task의 결과 dict — `error` 키가 있어야 기존 집계기가 부분 실패로 서술한다."""
    text = verdict.detail + (f" {guidance}" if guidance else "")
    return {"error": text, "skipped": True, "skip_reason": verdict.reason or ""}


# ──────────────────────────────────────────────
# 사후 대조 (plans/88 §4.3)
# ──────────────────────────────────────────────

def _is_hostname_col(col: str) -> bool:
    return any(h in str(col).lower() for h in _PRIOR_HOSTNAME_HINTS)


def _pick_result_col(rows: list[dict], scope_col: str) -> str:
    """스코프 컬럼과 **같은 종류**(hostname류/name류)의 결과 식별 컬럼을 고른다(D-061 혼합 금지)."""
    if not rows:
        return ""
    want_host = _is_hostname_col(scope_col)
    for col in rows[0].keys():
        if not is_server_identity_col(col):
            continue
        if _is_hostname_col(col) == want_host:
            return str(col)
    return ""


def _norm(v: Any) -> str:
    return str(v).strip().casefold()


def assess_scope_conformance(verdict: DependencyVerdict, rows: list[dict]) -> ScopeConformance:
    """후속 결과 행을 선행 스코프와 대조한다.

    - outside: 스코프에 없는 식별 값(제거 대상)
    - missing: 스코프에 있으나 결과에 없는 값(표기 대상). 스코프가 절단됐으면 계산하지 않는다.
    같은 종류의 식별 컬럼이 없으면 대조하지 않는다(`checked=False`) — 오제거보다 미제거가 낫다.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    col = _pick_result_col(rows, verdict.scope_col) if verdict.ok else ""
    if not col:
        return ScopeConformance(checked=False)
    scope = {_norm(v) for v in verdict.scope_values}
    seen: set[str] = set()
    outside: list[str] = []
    for r in rows:
        val = r.get(col)
        if val is None or str(val).strip() == "":
            continue
        key = _norm(val)
        seen.add(key)
        if key not in scope and str(val).strip() not in outside:
            outside.append(str(val).strip())
    missing = [] if verdict.truncated else [v for v in verdict.scope_values if _norm(v) not in seen]
    return ScopeConformance(checked=True, result_col=col, outside=outside, missing=missing)


def filter_outside_rows(rows: list[dict], result_col: str, outside: list[str]) -> list[dict]:
    """outside 식별 값을 가진 행을 제거한다(입력은 변경하지 않는다)."""
    if not outside or not result_col:
        return list(rows or [])
    bad = {_norm(v) for v in outside}
    return [r for r in (rows or []) if not (isinstance(r, dict) and _norm(r.get(result_col, "")) in bad)]


def conformance_note(conf: ScopeConformance, task_id: str) -> Optional[dict]:
    """대조 결과를 노트로 만든다. 대조 안 함·이상 없음이면 None."""
    if not conf.checked or (not conf.outside and not conf.missing):
        return None
    parts: list[str] = []
    if conf.outside:
        parts.append(
            f"선행 스코프 밖 서버 {len(conf.outside)}대의 행을 제외했습니다"
            f"({', '.join(conf.outside[:_NOTE_SAMPLE_VALUES])})"
        )
    if conf.missing:
        parts.append(
            f"선별 서버 중 {len(conf.missing)}대는 결과가 없습니다"
            f"({', '.join(conf.missing[:_NOTE_SAMPLE_VALUES])})"
        )
    return {
        "kind": NOTE_POSTCHECK, "task_id": task_id, "reason": "scope_mismatch",
        "detail": ". ".join(parts) + ".", "outside": list(conf.outside), "missing": list(conf.missing),
    }


# 사후 대조를 적용하는 agent — SQL 스코프(prior_rows) 소비자만. process_query·fault_diagnosis는
# 78 W5 충족도 검증이 같은 역할을 하므로 이중 처리하지 않는다.
POSTCHECK_AGENTS: tuple[str, ...] = ("data_query", "alarm_query")


def extract_result_rows(res: Any) -> list[dict]:
    """task 결과의 행 목록(3-shape 규약) — 호출부 공용."""
    return _result_rows(res)


def apply_scope_postcheck(verdict: Optional[DependencyVerdict], result: Any, task_id: str) -> Any:
    """후속 결과에 사후 대조를 적용한다 (plans/88 §4.3) — 1단·2단 공통.

    outside 행을 `query_results`·`rows`·`organized_data.rows`에서 제거하고, 대조 노트를
    `result["dependency_notes"]`에 싣는다(집계기가 task_results에서 모은다). 판정이 없거나
    실패 결과·대조 불가면 입력을 그대로 돌려준다.
    """
    if verdict is None or not verdict.ok or not isinstance(result, dict) or result.get("error"):
        return result
    conf = assess_scope_conformance(verdict, extract_result_rows(result))
    note = conformance_note(conf, task_id)
    if note is None:
        return result
    out = dict(result)
    if conf.outside:
        for key in ("query_results", "rows"):
            if isinstance(out.get(key), list):
                out[key] = filter_outside_rows(out[key], conf.result_col, conf.outside)
        organized = out.get("organized_data")
        if isinstance(organized, dict) and isinstance(organized.get("rows"), list):
            out["organized_data"] = {
                **organized,
                "rows": filter_outside_rows(organized["rows"], conf.result_col, conf.outside),
            }
    out["dependency_notes"] = list(out.get("dependency_notes") or []) + [note]
    return out


def scope_db_note(db_id: str, *, label: Optional[str] = None) -> dict:
    """DB별 스코프 분할로 미조회한 DB의 노트 (plans/88 §4.9)."""
    shown = label or db_id
    return {
        "kind": NOTE_SCOPE_DB, "task_id": None, "reason": "no_selected_servers", "db_id": db_id,
        "detail": f"{shown}: 선행 결과에 이 DB의 서버가 없어 조회하지 않았습니다.",
    }


# ──────────────────────────────────────────────
# 경과 블록 렌더 (plans/88 §4.4) — LLM 0회
# ──────────────────────────────────────────────

_BLOCK_TITLE = "## 순차 처리 경과"


def render_dependency_notes(notes: list[dict] | None) -> str:
    """노트 목록을 응답 말미 블록으로 렌더한다. 노트가 없으면 빈 문자열."""
    items = [n for n in (notes or []) if isinstance(n, dict) and n.get("detail")]
    if not items:
        return ""
    lines = [_BLOCK_TITLE]
    for n in items:
        tid = n.get("task_id")
        prefix = f"[{tid}] " if tid else ""
        lines.append(f"- {prefix}{n['detail']}")
    return "\n".join(lines)
