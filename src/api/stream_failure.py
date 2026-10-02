"""스트림 실패 경위 — error 이벤트에 "어디서·어떻게" 끊겼는지를 싣는다 (D-242).

운영 실측(2026-09-21): 은행존 질의가 60.6초에 "처리 시간이 초과되었습니다" 한 줄로만 끝났다.
실제로는 SQL 검증 실패 → 재생성 도중 상한에 닿은 것이었는데, 화면에는 어느 단계에서 왜
멈췄는지가 없었다. SSE 라우트가 이미 받는 진행 신호(노드 시작·종료, 도구·단계·작업 progress)를
관측해 단계 목록·진행 중이던 단계·마지막 실패 사유를 모으고, 실패 시 error 이벤트에 붙인다.

판정·재시도는 하지 않는다 — 경위를 보고 다시 할지는 사용자가 정한다.
error 이벤트의 ``message``는 바꾸지 않는다(하네스가 문구로 실패 유형을 분류한다).

plans/119 T-5·T-0(D-267 ⑦)에서 같은 관측 위에 두 가지를 더 얹는다 — 스트림 상한의 세 시계
(`StreamCaps`)와 단계 경계 타임라인(`StreamTimeline`). 둘 다 라우트가 이미 받는 이벤트만 본다.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from src.domain.partial_result import extract_partial_answer

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


# ── 스트림 상한 세 시계 (plans/119 T-5 · D-267 ⑦ G-7) ──────────────────────

#: 상한 종류 — 타임라인 `timeout_kind` 값과 같다(하네스 계약).
CUT_PROCESSING = "processing"   # 첫 답변 전 처리 상한
CUT_IDLE = "idle"               # 첫 답변 뒤 토큰 간 idle 상한
CUT_HARD_CAP = "hard_cap"       # 무한 대기 방지 전체 상한(처리 상한 + 전달 연장)


def _sec(value: float) -> str:
    """사용자 문구용 초 표기 — 정수면 소수점을 떼고 쓴다."""
    return f"{value:g}"


class StreamCaps:
    """스트림 응답의 세 시계 — 처리 상한 · 토큰 간 idle · 전체 상한 (plans/119 T-5 · D-267 ⑦).

    - **첫 답변 전**: 요청 시작부터 `limit_sec` 을 넘으면 `processing` 이다. 운영 설정
      `API_QUERY_TIMEOUT`·`API_FILE_QUERY_TIMEOUT` 이 이 값이다(G-7 — 종전 "요청 전체 상한").
    - **첫 답변 뒤**: 마지막 답변 토큰 이후 `idle_sec` 동안 토큰이 없으면 `idle` 이다. heartbeat·
      진행 이벤트는 이 시계를 되돌리지 않는다 — 답변 토큰만 되돌린다.
    - **무한 대기 방지**: 요청 시작부터 `limit_sec + grace_sec` 을 넘으면 `hard_cap` 이다
      (D-242 CU-11 유지). 첫 답변 전에는 처리 상한이 먼저 걸리므로 첫 답변 뒤에만 의미가 있다.

    값이 0 이하인 시계는 끈다(종전 `_exceeded_total_timeout` 의 "0 이하 = 상한 없음"과 같은 뜻).
    시계는 `time.monotonic()` 이라 벽시계 조정에 흔들리지 않는다.
    """

    def __init__(
        self,
        *,
        limit_sec: float,
        idle_sec: float,
        grace_sec: float,
        start: float | None = None,
    ) -> None:
        self.limit_sec = float(limit_sec or 0)
        self.idle_sec = max(0.0, float(idle_sec or 0))
        self.grace_sec = max(0.0, float(grace_sec or 0))
        self.start = time.monotonic() if start is None else float(start)
        self.first_answer_at: float | None = None
        self.last_answer_at: float | None = None

    @property
    def answered(self) -> bool:
        """첫 답변이 나갔는가."""
        return self.first_answer_at is not None

    @property
    def hard_cap_sec(self) -> float | None:
        """무한 대기 방지 전체 상한(초) — 처리 상한이 꺼져 있으면 None."""
        return self.limit_sec + self.grace_sec if self.limit_sec > 0 else None

    def mark_answer(self, now: float | None = None) -> None:
        """답변 토큰을 보냈다 — 첫 답변 시각을 잡고 idle 시계를 되돌린다."""
        at = time.monotonic() if now is None else now
        if self.first_answer_at is None:
            self.first_answer_at = at
        self.last_answer_at = at

    def _deadlines(self) -> list[tuple[float, str]]:
        if self.first_answer_at is None:
            if self.limit_sec > 0:
                return [(self.start + self.limit_sec, CUT_PROCESSING)]
            return []
        out: list[tuple[float, str]] = []
        if self.idle_sec > 0 and self.last_answer_at is not None:
            out.append((self.last_answer_at + self.idle_sec, CUT_IDLE))
        if self.limit_sec > 0:
            out.append((self.start + self.limit_sec + self.grace_sec, CUT_HARD_CAP))
        return out

    def next_check_at(self) -> float | None:
        """다음 판정 시각(monotonic) — 이벤트가 없어도 이 시각에 깨어나 판정해야 한다."""
        deadlines = self._deadlines()
        return min(at for at, _ in deadlines) if deadlines else None

    def exceeded(self, now: float | None = None) -> str | None:
        """넘긴 상한 종류 — 여럿이면 먼저 닿은 것. 넘기지 않았으면 None."""
        at = time.monotonic() if now is None else now
        hit = [(deadline, kind) for deadline, kind in self._deadlines() if at >= deadline]
        return min(hit)[1] if hit else None


def delivery_cut_notice(kind: str, *, idle_sec: float, limit_sec: float, grace_sec: float) -> str:
    """첫 답변 뒤에 끊은 사유 한 줄 — 받은 답 아래에 붙는다(침묵적 절단 금지)."""
    if kind == CUT_IDLE:
        return (
            f"답변 생성이 {_sec(idle_sec)}초 동안 멈춰 여기서 끊었습니다"
            f"(첫 답변 뒤 토큰 간 상한 {_sec(idle_sec)}초). 위 내용은 끊기기 전까지 받은 답입니다."
        )
    return (
        f"답변 전달이 전체 상한 {_sec(limit_sec + grace_sec)}초(처리 상한 {_sec(limit_sec)}초 + "
        f"전달 연장 {_sec(grace_sec)}초)를 넘어 여기서 끊었습니다. "
        "위 내용은 끊기기 전까지 받은 답입니다."
    )


# ── 단계 타임라인 (plans/119 T-0) ─────────────────────────────────────────

#: 상한 의미 — 이 값 이후 run 의 타임아웃은 "첫 답변까지"다(D-267 주의 ③ · 비교 키).
CAP_SEMANTIC = "first_answer"

#: 타임라인 경계 키 — **하네스가 이 이름으로 수집한다**(계약). 모든 값은 요청 시작 기준 ms.
TIMELINE_KEYS: tuple[str, ...] = (
    "parse_end_ms", "plan_end_ms", "first_rows_ms", "answer_start_ms", "first_answer_ms",
)

_PARSE_NODES = frozenset({"input_parser"})
#: 계획 끝 — 2단 `intent_planner` · 3단 `semantic_router`. 1단·4단에는 이 경계가 없다.
_PLAN_NODES = frozenset({"intent_planner", "semantic_router"})
#: 답변 단계 시작 — 2단 `result_aggregator` · 3단 계획 루프 `finalize`(같은 함수) · 3단·4단
#: `output_generator` · 일반 응답 `general_inference`(노드 전체가 답변 생성이다).
_ANSWER_NODES = frozenset({
    "result_aggregator", "output_generator", "finalize", "general_inference",
})
#: 1단 `deep_agent` 는 최종 합성을 노드 안에서 함수로 부른다 — 그 시작 단계 이벤트로 잡는다.
_ANSWER_STEPS = frozenset({"agent.aggregate"})
#: 행을 싣고 끝날 수 있는 노드 — 출력의 `query_results`·`organized_data.rows`·`task_results[*]`.
_ROW_NODES = frozenset({
    "query_executor", "multi_db_executor", "result_merger", "result_organizer",
    "agent_orchestrator", "task_run", "join", "sequential_runner", "deep_agent",
})


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


class StreamTimeline:
    """한 스트림 요청의 단계 경계 시각 (plans/119 T-0 — 제품 동작 불변).

    경계마다 **처음 도달한 시각**만 남긴다. 루트 직속 노드만 보는 경계(파싱·계획·답변 시작)는
    서브그래프 안의 같은 이름 노드에 끌려가지 않게 `root` 로 거른다(`_is_subgraph_event`). 첫 행은
    서브그래프 안에서 확보돼도 행을 손에 넣은 시각이므로 거르지 않는다.
    """

    def __init__(self, *, limit_sec: float) -> None:
        self.limit_sec = float(limit_sec or 0)
        self._marks: dict[str, float | None] = dict.fromkeys(TIMELINE_KEYS)

    def mark(self, key: str, at_ms: float) -> None:
        if self._marks.get(key) is None:
            self._marks[key] = round(float(at_ms), 1)

    def get(self, key: str) -> float | None:
        return self._marks.get(key)

    @property
    def answer_started(self) -> bool:
        return self._marks["answer_start_ms"] is not None

    def observe(self, event: Mapping[str, Any], at_ms: float, *, root: bool) -> None:
        """그래프 이벤트 한 건에서 경계를 읽는다."""
        kind = event.get("event")
        name = event.get("name") or ""
        raw = event.get("data")
        data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        if kind == "on_chain_end":
            if root and name in _PARSE_NODES:
                self.mark("parse_end_ms", at_ms)
            elif root and name in _PLAN_NODES:
                self.mark("plan_end_ms", at_ms)
            if (
                name in _ROW_NODES
                and self._marks["first_rows_ms"] is None
                and extract_partial_answer(data.get("output")) is not None
            ):
                self.mark("first_rows_ms", at_ms)
        elif kind == "on_chain_start":
            if root and name in _ANSWER_NODES:
                self.mark("answer_start_ms", at_ms)
        elif kind == "on_custom_event":
            phase = data.get("phase") or "start"
            if name in _ANSWER_STEPS and phase == "start":
                self.mark("answer_start_ms", at_ms)
            elif name == "task" and phase == "end" and _positive(data.get("row_count")):
                self.mark("first_rows_ms", at_ms)
            elif name == "group" and phase == "end":
                group = data.get("group")
                if isinstance(group, Mapping) and _positive(group.get("row_count")):
                    self.mark("first_rows_ms", at_ms)

    def stage(self) -> str:
        """가장 늦게 도달한 경계로 본 현재 단계 — 상한이 걸렸을 때의 사망 단계다."""
        marks = self._marks
        if marks["first_answer_ms"] is not None:
            return "delivery"
        if marks["answer_start_ms"] is not None:
            return "answer"
        if marks["plan_end_ms"] is not None or marks["first_rows_ms"] is not None:
            return "retrieval"
        if marks["parse_end_ms"] is not None:
            return "plan"
        return "parse"

    def payload(self, *, end_ms: float, timeout_kind: str | None = None) -> dict[str, Any]:
        """`done`·`error` SSE 페이로드와 로그 한 줄에 싣는 타임라인(계약 키 고정)."""
        return {
            "cap_semantic": CAP_SEMANTIC,
            "limit_sec": self.limit_sec,
            **self._marks,
            "end_ms": round(float(end_ms), 1),
            "timeout_stage": self.stage() if timeout_kind else None,
            "timeout_kind": timeout_kind,
        }


class StreamWatch:
    """스트림 요청 한 건의 상한(`StreamCaps`)·타임라인(`StreamTimeline`)·보낸 답변 본문.

    **첫 답변**은 답변 단계(`answer_start_ms` 이후)에 보낸 첫 `token` 이다(코드 렌더 표
    prefix 포함).
    답변 단계 밖에서 새는 토큰 — 2단 `agent_orchestrator` 안의 `general_inference` 하위 작업이
    `USER_RESPONSE_TAG` 로 스트리밍하는 경우 — 은 화면에는 그대로 나가지만 첫 답변으로 세지 않는다.
    세면 뒤이은 조회 동안 토큰 간 idle 상한이 걸려 정상 턴이 끊긴다. 세지 않았을 때의 최악은
    종전 동작(처리 상한이 스트리밍 중에도 걸림)이다. 그래프 종료 출력을 한 번에 보내는 경우는
    그 자체가 최종 답이라 단계와 무관하게 센다(`final=True`).
    """

    def __init__(
        self,
        *,
        limit_sec: float,
        idle_sec: float,
        grace_sec: float,
        start_time: float | None = None,
    ) -> None:
        self.start_time = time.time() if start_time is None else float(start_time)
        self.caps = StreamCaps(limit_sec=limit_sec, idle_sec=idle_sec, grace_sec=grace_sec)
        self.timeline = StreamTimeline(limit_sec=limit_sec)
        self.prefix_sent = False
        self._answer: list[str] = []

    def elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000

    def observe(self, event: Any, *, root: bool) -> None:
        """그래프 이벤트(astream_events v2 dict) 한 건 — 라우트 루프의 페이로드를 그대로 받는다."""
        self.timeline.observe(event, self.elapsed_ms(), root=root)

    def answer_sent(self, text: str, *, prefix: bool = False, final: bool = False) -> None:
        """답변 `token` 한 건을 보냈다."""
        self._answer.append(text)
        if prefix:
            self.prefix_sent = True
        if final or self.timeline.answer_started:
            self.caps.mark_answer()
            self.timeline.mark("first_answer_ms", self.elapsed_ms())

    @property
    def answer_text(self) -> str:
        """지금까지 보낸 답변 본문(화면 누적과 같다)."""
        return "".join(self._answer)

    def cut(self) -> str | None:
        return self.caps.exceeded()

    def next_check_at(self) -> float | None:
        return self.caps.next_check_at()

    def timeline_payload(
        self, *, timeout_kind: str | None = None, done: bool = False
    ) -> dict[str, Any]:
        """타임라인 확정. `done`(정상 완료)이면 첫 답변이 아직 없을 때 지금을 첫 답변으로 본다 —
        답변 단계로 식별되지 않는 노드(일부 옵트인 노드)가 최종 답을 낸 경우다."""
        end_ms = self.elapsed_ms()
        if done:
            self.timeline.mark("first_answer_ms", end_ms)
        return self.timeline.payload(end_ms=end_ms, timeout_kind=timeout_kind)
