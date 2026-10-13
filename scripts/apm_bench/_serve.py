"""APM 벤치 서버 진입점 — 관측 수신기를 꽂고 하네스처럼 앱을 띄운다 (plans/152 §3.6 · W2).

`scripts/itam_bench/_serve.py`와 같은 구조다(리로드 없음 · 단일 프로세스). 앱을 띄우기 전에
`src.observability.run_capture`에 수신기를 꽂는 것 하나만 다르다. 설치는 **이 진입점에서만** 한다
(D-301 ⑥ — 본체는 미설치면 `emit`이 즉시 반환 · 비트 동일).

관측 지점 3곳(본체 emit):
  ① `investigation_result` — `investigation_audit.audited_investigation` 의 `finally`
     (backend, task, isolated, result) · **backend 가 APM(`apm_gateway`)일 때만** 옮긴다
  ② `apm_plan_raw` — `intent_planner._sanitize_task_views` 정제 직전 분해 원출력(thread_id 없음 ·
     드라이버가 캡처 오프셋으로 턴에 귀속)
  ③ `apm_view_reselect` — `apm_query._retry_selection` 보기 재선택 LLM 원출력

수신기 규칙(§3.6): 받은 객체를 보관하지 않는다. 그 자리에서 **이름·불린·수·리스트·인자 dict**만
JSON 으로 복사해 한 줄 쓰고 놓는다. 결과 행(`query_results`)·`source_status` 행은 읽지 않는다 —
메타 칸(`apm_query` 메타의 선택·대상·provenance 요약)만 옮긴다. provenance 인자·대상 이름에는
운영 이름이 들어갈 수 있으므로 수신 파일은 부모가 0700 세션 임시 디렉터리에 두고 끝나면 지운다
(디스크에 남기는 산출물은 W4 위생을 거친다).

수신 파일 경로는 벤치 부모 프로세스가 환경변수 `APM_BENCH_CAPTURE_PATH`로 넘긴다. 없으면 설치하지
않는다.
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

CAPTURE_ENV = "APM_BENCH_CAPTURE_PATH"
KIND_RESULT = "investigation_result"
KIND_PLAN_RAW = "apm_plan_raw"
KIND_RESELECT = "apm_view_reselect"
#: 조사 감사 backend 값(`investigation_audit.BACKEND_APM`) — 이것만 옮긴다.
APM_BACKEND = "apm_gateway"
APM_AGENT = "apm_query"
#: 처리기 결과의 메타 키(`apm_query.META_KEY`) · 소스 되묻기 키(`SOURCE_CLARIFICATION_KEY`).
META_KEY = "apm_query"
SOURCE_CLARIFICATION_KEY = "apm_source_clarification"
#: task 에서 옮기는 칸(분해 계약 — `record.PlanTask`).
TASK_KEYS = ("task_id", "agent", "views", "view_args", "targets", "sources", "areas")
#: 처리기 메타 선택 정보(`_select_views` info)에서 옮기는 칸.
SELECTION_KEYS = ("areas", "planned", "retried", "result", "latency_ms", "retried_views",
                  "candidates", "uncovered", "agreed", "error")
#: provenance 1건에서 옮기는 칸(`reference` = 앞 결과 행 참조 경과 — 종류·출처·번호).
PROVENANCE_KEYS = ("view", "tool", "hostname", "instance", "reference", "queried_at", "window",
                   "rows", "confidence", "args")
RESPONSE_HEAD = 2000


def _json(value: Any) -> Any:
    """JSON 직렬화 가능한 사본(깊은 복사 · 원본 참조를 끊는다)."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return None


def _pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    return {k: _json(source[k]) for k in keys if k in source}


def _task(task: Any) -> dict[str, Any]:
    return _pick(task, TASK_KEYS)


def _meta(meta: Any) -> dict[str, Any] | None:
    """처리기 메타 → 선택·대상·호출 요약(결과 행 없음)."""
    if not isinstance(meta, Mapping):
        return None
    targets = meta.get("targets") if isinstance(meta.get("targets"), Mapping) else {}
    texts = [
        {
            "text": str(t.get("text") or ""),
            "kind": t.get("kind"),
            "searched": _json(list(t.get("searched") or [])),
            "instances": t.get("instances") if isinstance(t.get("instances"), int) else 0,
            "suggestions": [str(s) for s in t.get("suggestions") or []],
            "failures": _json(list(t.get("failures") or [])),
        }
        for t in targets.get("texts") or [] if isinstance(t, Mapping)
    ]
    instances = [
        _pick(i, ("apm_instance_name", "apm_instance_id", "apm_domain_id", "apm_source_id",
                  "hostname"))
        for i in targets.get("instances") or [] if isinstance(i, Mapping)
    ]
    return {
        "selection": _pick(meta.get("selection"), SELECTION_KEYS),
        "views": _json(list(meta.get("views") or [])),
        "targets": {"texts": texts, "instances": instances},
        "hostnames": [str(h) for h in meta.get("hostnames") or []],
        "inserted_steps": [
            _pick(s, ("view", "tool", "mode", "error", "rank_error"))
            for s in meta.get("inserted_steps") or [] if isinstance(s, Mapping)
        ],
        "provenance": [
            _pick(p, PROVENANCE_KEYS) for p in meta.get("provenance") or []
            if isinstance(p, Mapping)
        ],
        "failures": [
            _pick(f, ("view", "hostname", "reason"))
            for f in meta.get("failures") or [] if isinstance(f, Mapping)
        ],
        "source_selection": _json(meta.get("source_selection")),
        "limits": _json(meta.get("limits")),
    }


def capture_record(kind: str, payload: Any) -> dict[str, Any] | None:
    """emit 1건 → 수신 레코드(옮길 것이 없으면 None). 원본 객체를 참조로 남기지 않는다."""
    if kind == KIND_PLAN_RAW:
        if not isinstance(payload, Mapping):
            return None
        tasks = [_task(t) for t in payload.get("tasks") or [] if isinstance(t, Mapping)]
        return {"kind": kind, "thread_id": None, "tasks": tasks}
    if kind == KIND_RESELECT:
        if not isinstance(payload, Mapping):
            return None
        parsed = payload.get("parsed")
        thread_id = payload.get("thread_id")
        return {
            "kind": kind,
            "thread_id": thread_id if isinstance(thread_id, str) else None,
            "areas": _json(list(payload.get("areas") or [])),
            "parsed_is_dict": isinstance(parsed, Mapping),
            "parsed_views": _json(parsed.get("views")) if isinstance(parsed, Mapping) else None,
            "parsed_view_args": (_json(parsed.get("view_args"))
                                 if isinstance(parsed, Mapping) else None),
        }
    if kind == KIND_RESULT:
        if not isinstance(payload, tuple) or len(payload) != 4:
            return None
        backend, task, isolated, result = payload
        if backend != APM_BACKEND:
            return None
        thread_id = isolated.get("thread_id") if isinstance(isolated, Mapping) else None
        res = result if isinstance(result, Mapping) else {}
        final = res.get("final_response")
        return {
            "kind": kind,
            "thread_id": thread_id if isinstance(thread_id, str) else None,
            "task": _task(task),
            "has_result": isinstance(result, Mapping),
            "meta": _meta(res.get(META_KEY)),
            "answer_lines": [str(x) for x in res.get("answer_lines") or []],
            "disclosures": [
                _pick(d, ("kind", "text", "source"))
                for d in res.get("disclosures") or [] if isinstance(d, Mapping)
            ],
            "degraded_reason": _json(res.get("degraded_reason")),
            "error": _json(res.get("error")),
            "final_response": str(final)[:RESPONSE_HEAD] if isinstance(final, str) else None,
            "source_clarification": _json(res.get(SOURCE_CLARIFICATION_KEY)),
        }
    return None


class FileSink:
    """수신 레코드를 JSONL 한 줄씩 덧붙인다(스레드 안전). 받은 객체는 보관하지 않는다."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, kind: str, payload: Any) -> None:
        record = capture_record(kind, payload)
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


class CaptureTail:
    """수신 파일을 크기 오프셋부터 읽는다(턴 귀속 — mark → collect)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def mark(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def collect(self, since: int) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, "rb") as handle:
            handle.seek(since)
            data = handle.read()
        out: list[dict[str, Any]] = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                out.append(record)
        return out


def main() -> None:
    install_from_env()
    from scripts.scenario._serve import main as serve

    serve()


if __name__ == "__main__":
    main()
