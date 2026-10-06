"""벤치 서버 진입점 — 측정 수신기를 설치하고 하네스와 같은 방식으로 앱을 띄운다.

plans/135 §3.6 · W3.

`scripts/scenario/_serve.py`와 같다(리로드 없음 · 단일 프로세스). 다른 점은 앱을 띄우기 전에
`src.observability.run_capture`에 수신기를 꽂는 것 하나다. 설치는 **이 진입점에서만** 한다(D-301 ⑥).

수신기 규칙(§3.6): 받은 상태 객체를 보관하지 않는다. 그 자리에서 `thread_id`·테이블 이름·컬럼
**이름**· 컬럼별 의미 보유 여부·표본 행 유무(불린)·구조 정보 유무만 복사해 한 줄 쓰고 놓는다. **값과
사용자 필드(`user_id`·`user_department`)는 읽지 않는다.**

plans/139 W6-d: DB 별로 프롬프트 크기·선별 결과도 옮긴다 — **숫자와 짧은 열거만**(추정 토큰 수 ·
예산 단계 · 백엔드가 보고한 토큰 수 · 선별 출처 · 선별 수 · 재생성 종결 사유). 선별된 테이블 이름
목록·사유 문구는 옮기지 않는다. 상태에 없으면 null 이다.

수신 파일 경로는 벤치 부모 프로세스가 환경변수 `ITAM_BENCH_CAPTURE_PATH`로 넘긴다(세션 임시 디렉터리
· 실행 종료 시 부모가 지운다). 없으면 설치하지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CAPTURE_ENV = "ITAM_BENCH_CAPTURE_PATH"
CAPTURE_KIND = "task_pipeline_state"

#: 상태 계약(plans/139 W2 `prompt_budget.stage` · W4 `table_selection.source`)의 열거값.
#: 그 밖의 값은 null 로 옮긴다.
BUDGET_STAGES: frozenset[str] = frozenset({"within", "materials", "samples", "exceeded"})
SELECTION_SOURCES: frozenset[str] = frozenset({"llm", "lexical", "none"})
#: 재생성 종결 사유(`regen_stop.reason`)는 코드 열거(`backend_limit` 등)만 옮긴다.
#: 문구 모양이면 null 이다.
_REASON_CODE = re.compile(r"^[a-z][a-z_]{0,39}$")


def _schema_shape(schema: Any, descriptions: Any) -> dict[str, Any]:
    """스키마 상태 → 이름·불린만. `descriptions`가 None 이면 의미 보유를 모른다(멀티 DB 경로)."""
    described: set[str] | None = None
    if isinstance(descriptions, Mapping):
        described = {str(key).casefold() for key in descriptions}
    tables: dict[str, Any] = {}
    raw_tables = schema.get("tables") if isinstance(schema, Mapping) else None
    for name, table in (raw_tables or {}).items():
        if not isinstance(table, Mapping):
            continue
        columns = [
            str(col.get("name"))
            for col in table.get("columns") or []
            if isinstance(col, Mapping) and col.get("name")
        ]
        bare = str(name).rsplit(".", 1)[-1]
        with_meaning = (
            None
            if described is None
            else [
                col
                for col in columns
                if f"{bare}.{col}".casefold() in described
                or f"{name}.{col}".casefold() in described
            ]
        )
        tables[str(name)] = {
            "columns": columns,
            "with_meaning": with_meaning,
            "sample_rows": bool(table.get("sample_data")),
        }
    structure = bool(schema.get("_structure_meta")) if isinstance(schema, Mapping) else False
    return {"tables": tables, "structure_meta": structure}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _member(value: Any, allowed: Collection[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _prompt_shape(state: Mapping[str, Any], db_id: str, *, single: bool) -> dict[str, Any]:
    """DB 하나의 프롬프트 크기·선별 결과 → 숫자·짧은 열거만(plans/139 W6-d).

    예산(`prompt_budget`)·백엔드 보고(`validation_result.backend_error`)·종결 사유(`regen_stop`)는
    단일 경로 상태의 몫이다. 멀티 DB 상태는 DB 별 종결 사유(`regen_stops`)와 선별만 읽는다.
    선별(`table_selection`)은 두 경로 모두 DB id 키다.
    """
    budget = _mapping(state.get("prompt_budget")) if single else {}
    backend = (
        _mapping(_mapping(state.get("validation_result")).get("backend_error")) if single else {}
    )
    selection = _mapping(_mapping(state.get("table_selection")).get(db_id))
    selected = selection.get("selected")
    stop = (
        _mapping(state.get("regen_stop"))
        if single
        else _mapping(_mapping(state.get("regen_stops")).get(db_id))
    )
    reason = stop.get("reason")
    return {
        "prompt_tokens_est": _count(budget.get("estimated_tokens")),
        "budget_stage": _member(budget.get("stage"), BUDGET_STAGES),
        "backend_reported_tokens": _count(backend.get("given")),
        "selection_source": _member(selection.get("source"), SELECTION_SOURCES),
        "selected_count": len(selected) if isinstance(selected, (list, tuple)) else None,
        "stop_reason": reason if isinstance(reason, str) and _REASON_CODE.match(reason) else None,
    }


def schema_context_record(kind: str, state: Any) -> dict[str, Any] | None:
    """수신한 task 상태 → 스키마 맥락 레코드. 대상 종류가 아니면 None."""
    if kind != CAPTURE_KIND or not isinstance(state, Mapping):
        return None
    thread_id = state.get("thread_id")
    dbs: dict[str, Any] = {}
    db_schemas = state.get("db_schemas")
    if state.get("is_multi_db") and isinstance(db_schemas, Mapping):
        # 선별·종결만 남은 DB(스키마 없이 끝난 DB)도 칸을 둔다
        db_ids = dict.fromkeys(
            [
                *db_schemas,
                *_mapping(state.get("table_selection")),
                *_mapping(state.get("regen_stops")),
            ]
        )
        for db_id in db_ids:
            dbs[str(db_id)] = {
                **_schema_shape(db_schemas.get(db_id), None),
                **_prompt_shape(state, str(db_id), single=False),
            }
    else:
        active = str(state.get("active_db_id") or "")
        dbs[active] = {
            **_schema_shape(state.get("schema_info"), state.get("column_descriptions")),
            **_prompt_shape(state, active, single=True),
        }
    return {
        "kind": kind,
        "thread_id": thread_id if isinstance(thread_id, str) else None,
        "dbs": dbs,
    }


class FileSink:
    """수신 레코드를 JSONL 한 줄씩 덧붙인다(스레드 안전). 상태 객체는 보관하지 않는다."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, kind: str, payload: Any) -> None:
        record = schema_context_record(kind, payload)
        if record is None:
            return
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, open(self._path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")


def install_from_env() -> bool:
    """환경변수가 있으면 수신기를 설치한다. 설치했으면 True."""
    path = os.environ.get(CAPTURE_ENV)
    if not path:
        return False
    from src.observability import run_capture

    run_capture.install(FileSink(Path(path)))
    return True


def main() -> None:
    install_from_env()
    from scripts.scenario._serve import main as serve

    serve()


if __name__ == "__main__":
    main()
