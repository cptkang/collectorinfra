"""벤치 서버 진입점 — 측정 수신기를 설치하고 하네스와 같은 방식으로 앱을 띄운다.

plans/135 §3.6 · W3.

`scripts/scenario/_serve.py`와 같다(리로드 없음 · 단일 프로세스). 다른 점은 앱을 띄우기 전에
`src.observability.run_capture`에 수신기를 꽂는 것 하나다. 설치는 **이 진입점에서만** 한다(D-301 ⑥).

수신기 규칙(§3.6): 받은 상태 객체를 보관하지 않는다. 그 자리에서 `thread_id`·테이블 이름·컬럼
**이름**· 컬럼별 의미 보유 여부·표본 행 유무(불린)·구조 정보 유무만 복사해 한 줄 쓰고 놓는다. **값과
사용자 필드(`user_id`·`user_department`)는 읽지 않는다.**

수신 파일 경로는 벤치 부모 프로세스가 환경변수 `ITAM_BENCH_CAPTURE_PATH`로 넘긴다(세션 임시 디렉터리
· 실행 종료 시 부모가 지운다). 없으면 설치하지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CAPTURE_ENV = "ITAM_BENCH_CAPTURE_PATH"
CAPTURE_KIND = "task_pipeline_state"


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


def schema_context_record(kind: str, state: Any) -> dict[str, Any] | None:
    """수신한 task 상태 → 스키마 맥락 레코드. 대상 종류가 아니면 None."""
    if kind != CAPTURE_KIND or not isinstance(state, Mapping):
        return None
    thread_id = state.get("thread_id")
    dbs: dict[str, Any] = {}
    db_schemas = state.get("db_schemas")
    if state.get("is_multi_db") and isinstance(db_schemas, Mapping):
        for db_id, schema in db_schemas.items():
            dbs[str(db_id)] = _schema_shape(schema, None)
    else:
        dbs[str(state.get("active_db_id") or "")] = _schema_shape(
            state.get("schema_info"), state.get("column_descriptions")
        )
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
