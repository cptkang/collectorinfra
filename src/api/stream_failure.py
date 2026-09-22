"""스트림 실패 경위 — error 이벤트에 "어디서·어떻게" 끊겼는지를 싣는다 (D-242).

운영 실측(2026-09-21): 은행존 질의가 60.6초에 "처리 시간이 초과되었습니다" 한 줄로만 끝났다.
실제로는 SQL 검증 실패 → 재생성 도중 상한에 닿은 것이었는데, 화면에는 어느 단계에서 왜
멈췄는지가 없었다. SSE 라우트가 이미 받는 진행 신호(노드 시작·종료, 도구·단계·작업 progress)를
관측해 단계 목록·진행 중이던 단계·마지막 실패 사유를 모으고, 실패 시 error 이벤트에 붙인다.

판정·재시도는 하지 않는다 — 경위를 보고 다시 할지는 사용자가 정한다.
error 이벤트의 ``message``는 바꾸지 않는다(하네스가 문구로 실패 유형을 분류한다).
"""

from __future__ import annotations

from typing import Any

#: 페이로드에 싣는 단계 수 상한 — 넘치면 앞쪽(오래된 것)을 버리고 버린 수를 알린다.
_MAX_STEPS = 30
#: 실패 사유 한 건의 글자 수 상한.
_MAX_TEXT = 300


def _clip(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


class StreamTrace:
    """한 스트림 요청의 진행 경위. 요청마다 새로 만든다(공유 금지)."""

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []
        self._dropped = 0
        self._last_error: str | None = None

    # ── 관측 ──────────────────────────────────────────────

    def start(self, kind: str, name: str, at_ms: float, label: str | None = None) -> None:
        self._steps.append({
            "kind": kind, "name": name, "label": _clip(label),
            "status": "running", "start_ms": at_ms, "end_ms": None, "detail": None,
        })
        if len(self._steps) > _MAX_STEPS:
            self._steps.pop(0)
            self._dropped += 1

    def end(
        self, kind: str, name: str, at_ms: float, *,
        error: Any = None, status: str = "done", note: Any = None,
    ) -> None:
        entry = next(
            (s for s in reversed(self._steps)
             if s["kind"] == kind and s["name"] == name and s["status"] == "running"),
            None,
        )
        if entry is None:
            # 시작 신호 없이 끝만 온 경우 — 재시도 루프에서 같은 노드가 다시 끝날 때(시작 이벤트는
            # 노드당 1회만 낸다) · 상한으로 앞 단계가 잘린 경우
            self.start(kind, name, at_ms)
            entry = self._steps[-1]
        entry["end_ms"] = at_ms
        detail = _clip(error)
        if detail:
            entry["status"] = "failed"
            entry["detail"] = detail
            self._last_error = detail
        else:
            entry["status"] = status
            entry["detail"] = _clip(note)

    def observe_progress(self, payload: dict) -> None:
        """라우트가 만든 SSE ``progress`` 페이로드(도구·단계·작업)를 관측한다."""
        kind = payload.get("kind")
        phase = payload.get("phase")
        at = float(payload.get("timestamp_ms") or 0.0)
        if kind == "task":
            task = payload.get("task") or {}
            name = str(task.get("task_id") or task.get("order") or "task")
            if phase == "start":
                self.start("task", name, at, task.get("sub_query") or task.get("agent"))
            elif task.get("status") == "skipped":
                self.end("task", name, at, status="skipped", note=task.get("reason"))
            else:
                failed = task.get("status") == "failed"
                self.end("task", name, at, error=(task.get("error") or "실패") if failed else None)
        elif kind == "group":  # 존 그룹 순차 실행(plans/82 v7 R-2 · D-249)
            group = payload.get("group") or {}
            name = str(group.get("group_key") or "group")
            if phase == "start":
                self.start("group", name, at, group.get("label"))
            else:
                failed = bool(group.get("error_dbs")) and not group.get("row_count")
                self.end("group", name, at, error="조회 실패" if failed else None)
        elif kind in ("tool", "step"):
            name = str(payload.get("name") or "")
            if phase == "start":
                self.start(kind, name, at, payload.get("label"))
            else:
                self.end(kind, name, at, error=payload.get("detail"))

    def node_started(self, node: str, at_ms: float) -> None:
        self.start("node", node, at_ms)

    def node_ended(self, node: str, output: Any, at_ms: float) -> None:
        err = output.get("error_message") if isinstance(output, dict) else None
        self.end("node", node, at_ms, error=err)

    # ── 산출 ──────────────────────────────────────────────

    def failure_fields(self, *, code: str, elapsed_ms: float, limit_sec: float | None) -> dict:
        """error 이벤트에 덧붙일 경위 필드. ``message``·``type``은 호출자가 채운다."""
        steps = []
        for s in self._steps:
            end = s["end_ms"] if s["end_ms"] is not None else elapsed_ms
            steps.append({
                "kind": s["kind"], "name": s["name"], "label": s["label"],
                "status": s["status"], "ms": round(max(0.0, end - s["start_ms"]), 1),
                "detail": s["detail"],
            })
        # 진행 중이던 가장 안쪽 단계 = 가장 최근에 시작해 아직 끝나지 않은 것
        stage = next((st for st in reversed(steps) if st["status"] == "running"), None)
        return {
            "code": code,
            "elapsed_ms": round(elapsed_ms, 1),
            "limit_sec": limit_sec,
            "stage": stage,
            "last_error": self._last_error,
            "steps": steps,
            "steps_dropped": self._dropped,
        }
