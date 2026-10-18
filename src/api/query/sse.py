"""SSE 직렬화·그래프 이벤트 스트림·heartbeat·오류/진행 이벤트.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from fastapi.responses import StreamingResponse

from src.api.error_detail import current_request_id
from src.api.sse_events import SSE_ERROR, SSE_HEARTBEAT, SSE_PROGRESS
from src.api.stream_failure import StreamTrace
from src.utils.json_extract import coerce_content_text
from src.utils.user_error import user_error_text

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


async def _next_event_or_timeout(event_iter: Any, timeout: float) -> tuple[object, bool]:
    """SSE 이벤트 fetch를 대기하되, 타임아웃 **검출**과 내부 **취소 완료**를 분리한다(D-198).

    종전의 ``asyncio.wait_for``는 타임아웃 발화 후 내부 태스크의 취소가 **완료될
    때까지** 기다린다 — LLM 호출이 취소에 반응하지 않으면(FabriX 미귀환,
    2026-09-07 task_dump 실측: event_generator가 wait_for에 8분+ 고정) TimeoutError가
    영영 raise되지 않아 가드 자체가 무력화된다. ``asyncio.wait``는 타임아웃 시
    태스크를 건드리지 않고 반환하므로, 취소는 걸어두되 완료는 기다리지 않는다.
    잔여(유령) 태스크의 수명은 FabriX 클라이언트 총상한(D-198 F1)이 보장한다.

    Returns:
        (event, False) 정상 수신 / (None, True) 타임아웃.

    Raises:
        StopAsyncIteration: 이터레이터 소진 (호출부 break 용 — 종전과 동일).
    """
    fetch = asyncio.ensure_future(event_iter.__anext__())
    done, _pending = await asyncio.wait({fetch}, timeout=timeout)
    if not done:
        fetch.cancel()
        logger.warning(
            "SSE 이벤트 대기 %.0fs 초과 — 내부 실행에 취소만 걸고 응답을 종료한다 "
            "(취소 완료 대기 없음, 잔여 태스크는 FabriX 총상한에서 정리 — D-198)",
            timeout,
        )
        return None, True
    return fetch.result(), False


def _sse_event(data: dict[str, Any]) -> str:
    """SSE 이벤트 문자열을 생성한다."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# 처리 현황 표시 대상 노드(SSE node_start/node_complete 화이트리스트 · D-039).
# D-204(plans/89 T1): 사다리 1단 `deep_agent`(부가 경로 · D-225 ②)와 옵트인 노드가 빠져 있어
# 그 경로에서 field_mapper 이후 최종 토큰까지 이벤트가 0건이던 것을 정정한다.
_STREAM_KNOWN_NODES: frozenset[str] = frozenset({
    "context_resolver", "input_parser",
    "semantic_router", "schema_analyzer",
    "field_mapper",
    "query_generator", "query_validator",
    "approval_gate", "query_executor",
    "result_organizer", "output_generator",
    "multi_db_executor", "result_merger",
    "synonym_registrar", "general_inference", "error_response",
    # Plan 48/49: 다중 의도 오케스트레이션 노드 (처리 현황 표시)
    "intent_planner", "agent_orchestrator",
    "replanner", "result_aggregator",
    # plans/89 · D-204: 사다리 1단(부가 경로) + 옵트인 노드
    "deep_agent", "fault_diagnosis", "cache_management",
    # plans/103 P2 · plans/111 C-4·C-5: 3단 계획 루프(`TIER3_PLAN_LOOP_ENABLED`)
    "plan", "normalize", "dispatch", "task_run", "join", "replan", "finalize",
    # plans/163 D5: 그래프가 등록하는데 빠져 있던 3·4단 옵트인 노드 — 식별자 소재 프로브
    # (`cross_system_probe_enabled`) · 순차 2-pass(`composite.sequential_fallback_tiers_enabled`)
    "entity_locator", "sequential_runner",
})


def _is_subgraph_event(event: Any) -> bool:
    """서브그래프 **안**에서 난 이벤트인가 (plans/103 K-3).

    ``astream_events(v2)``의 ``parent_ids``는 루트 실행 0개 · 루트 직속 노드 1개이고, 노드 안에서
    도는 그래프(3단 `task_run` 서브그래프 · 1단 deep_agent 내부 그래프)의 이벤트는 2개 이상이다
    (2026-09-22 langgraph 1.2.11 토이 그래프 실측). 종료 판정은 루트 직속까지만 본다 — 서브그래프
    노드가 ``final_response``를 내도 스트림이 부분 답으로 닫히지 않게 한다. 키가 없으면(테스트 대역)
    루트로 본다(종전 동작).
    """
    return len(event.get("parent_ids") or ()) > 1


_PRODUCER_CANCEL_GRACE_SEC = 1.0   # 생산자 취소 완료를 기다리는 상한(D-198 F2 정합)


async def _graph_event_stream(
    graph: Any,
    input_state: dict[str, Any],
    thread_config: dict[str, Any],
    *,
    idle_timeout: float | None,
    heartbeat_interval: float,
    wake_at: Callable[[], float | None] | None = None,
) -> AsyncGenerator[tuple[str, object], None]:
    """astream_events를 생산자 태스크로 돌리고 (kind, payload)를 낸다 (plans/89 §3.2-④ · D-204).

    kind: ``"event"``(LangGraph 이벤트) · ``"heartbeat"``(``{"idle_ms"}``) · ``"tick"``(상한 판정
    시각 — 페이로드 없음) · ``"timeout"``.

    - 무이벤트 연속 시간이 ``idle_timeout``을 넘으면 ``timeout``을 내고 끝낸다 — D-066 후속의
      "이벤트 fetch당 타임아웃"(무한 hang 방지 안전 상한)이다. None·0 이하면 없음. 라우트는 전체
      상한(처리 상한 + 전달 연장)을 넘긴다 — 상한 판정 자체는 라우트가 한다(plans/119 T-5).
    - ``wake_at``은 다음 상한 판정 시각(``time.monotonic()``)을 돌려준다. 그 시각까지
      이벤트가 없으면 ``tick``을 내서 라우트가 판정하게 한다 — heartbeat가 꺼져 있어도(0)
      판정이 이벤트 도착에 묶이지 않는다(종전에는 heartbeat off면 이벤트가 올 때만 전체
      상한을 쟀다).
    - ``heartbeat_interval``(초) 동안 이벤트가 없으면 ``heartbeat``를 낸다. 0 이하면 하트비트 없음.
      heartbeat는 상한 시계를 되돌리지 않는다.
    - **``wait_for(__anext__)``를 재호출하지 않는다** — 취소된 ``__anext__``는 비동기 제너레이터를
      깨뜨린다. 대신 큐를 기다린다(``Queue.get`` 취소는 안전).
    - 생산자 예외는 소비자에게 재전달한다(기존 ``except (AttributeError, …)`` 폴백 경로 유지).
    - 소비자가 어떤 경로로 끝나든 ``finally``에서 생산자를 취소한다(클라이언트 단절 포함).
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def _produce() -> None:
        try:
            async for ev in graph.astream_events(input_state, thread_config, version="v2"):
                await queue.put(("event", ev))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — 소비자 측 폴백 판정에 그대로 넘긴다
            await queue.put(("error", exc))
        finally:
            await queue.put(("done", None))

    producer = asyncio.create_task(_produce())
    idle_cap = idle_timeout if idle_timeout and idle_timeout > 0 else None
    beat = heartbeat_interval if heartbeat_interval and heartbeat_interval > 0 else None
    last_activity = time.monotonic()
    last_signal = last_activity   # 마지막 이벤트 또는 heartbeat
    try:
        while True:
            marks: list[float] = []
            if idle_cap is not None:
                marks.append(last_activity + idle_cap)
            if beat is not None:
                marks.append(last_signal + beat)
            check_at = wake_at() if wake_at is not None else None
            if check_at is not None:
                marks.append(check_at)
            wait = max(0.0, min(marks) - time.monotonic()) if marks else None
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=wait)
            except asyncio.TimeoutError:  # noqa: UP041 — 이동 전 원문 유지(plans/179 W2)
                now = time.monotonic()
                idle = now - last_activity
                if idle_cap is not None and idle >= idle_cap:
                    yield ("timeout", None)
                    return
                if beat is not None and now - last_signal >= beat:
                    last_signal = now
                    yield ("heartbeat", {"idle_ms": idle * 1000})
                else:
                    yield ("tick", None)
                continue
            if kind == "done":
                return
            if kind == "error":
                raise payload
            last_activity = last_signal = time.monotonic()
            yield ("event", payload)
    finally:
        if not producer.done():
            # D-198 F2(검출·취소 분리): 취소는 걸되 완료를 무한정 기다리지 않는다 — 취소에
            # 반응하지 않는 LLM 호출(FabriX 미귀환 실측)이 있으면 종전 wait_for처럼 여기서
            # 갇힌다. 유한 대기 뒤 남는 유령 태스크의 수명은 FabriX 총상한(F1)이 보장한다.
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait({producer}, timeout=_PRODUCER_CANCEL_GRACE_SEC)


def _heartbeat_sse_payload(start_time: float, hb: dict[str, Any]) -> dict[str, Any]:
    """heartbeat SSE 페이로드 — 경과와 마지막 활동 시점(ms)."""
    elapsed_ms = (time.time() - start_time) * 1000
    idle_ms = float(hb.get("idle_ms", 0.0))
    return {
        "type": SSE_HEARTBEAT,
        "elapsed_ms": elapsed_ms,
        "last_activity_ms": max(0.0, elapsed_ms - idle_ms),
    }


def _stream_error_payload(
    message: str,
    trace: StreamTrace,
    *,
    code: str,
    start_time: float,
    limit_sec: float | None,
    timeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """SSE ``error`` 이벤트 — 문구(``message``)는 그대로 두고 실패 경위를 덧붙인다 (D-242).

    경위(어느 단계에서·얼마나 걸려·앞서 무슨 실패가 있었는지)는 화면이 그대로 보여 주고,
    다시 시도할지는 사용자가 정한다. 문구를 바꾸지 않는 이유: 하네스가 문구로 실패 유형을 가른다.
    ``timeline``은 단계 경계 시각이다(plans/119 T-0 — 있을 때만 싣는다).
    """
    payload = {
        "type": SSE_ERROR,
        "message": message,
        **trace.failure_fields(
            code=code, elapsed_ms=(time.time() - start_time) * 1000, limit_sec=limit_sec
        ),
    }
    if timeline is not None:
        payload["timeline"] = timeline
    return payload


def _progress_sse_payload(
    event: dict[str, Any], current_node: str | None, start_time: float
) -> dict[str, Any] | None:
    """도구·커스텀 이벤트를 SSE ``progress``로 변환한다 (plans/89 §3.1 · D-204).

    - ``on_tool_start``/``on_tool_end`` → ``kind:"tool"``(``name``=도구명, 시작 시 ``sub_query``를 label로)
    - ``on_custom_event`` name ``"task"`` → ``kind:"task"`` + ``task`` 페이로드(plans/88 verdict 필드명 동일)
    - ``on_custom_event`` name ``"group"`` → ``kind:"group"`` + ``group`` 페이로드 — 존 그룹 시작·완료와
      peer 그룹의 **마스킹된** 행 미리보기(plans/82 v7 R-2 · D-249 — D-204 "라벨만" 원칙의 명시 예외)
    - 그 외 ``on_custom_event`` → ``kind:"step"``
    라벨은 클라이언트가 붙인다 — 서버는 원시 이름만 낸다.
    """  # noqa: E501 — 이동 전부터 있던 긴 줄(plans/179 W2)
    kind = event.get("event", "")
    name = event.get("name", "") or ""
    if kind not in ("on_tool_start", "on_tool_end", "on_custom_event"):
        return None
    base = {
        "type": SSE_PROGRESS,
        "name": name,
        "node": current_node or "",
        "timestamp_ms": (time.time() - start_time) * 1000,
    }
    if kind in ("on_tool_start", "on_tool_end"):
        base["kind"] = "tool"
        base["phase"] = "start" if kind == "on_tool_start" else "end"
        if kind == "on_tool_start":
            inp = (event.get("data") or {}).get("input")
            if isinstance(inp, dict) and isinstance(inp.get("sub_query"), str):
                base["label"] = inp["sub_query"][:200]
        return base
    data = event.get("data")
    if not isinstance(data, dict):
        data = {}
    base["phase"] = data.get("phase") or "start"
    if name == "task":
        base["kind"] = "task"
        base["task"] = data
    elif name == "group":
        base["kind"] = "group"
        base["group"] = data.get("group") if isinstance(data.get("group"), dict) else {}
    else:
        base["kind"] = "step"
        if data.get("label"):
            base["label"] = str(data["label"])[:200]
        if data.get("detail"):  # 단계 실패 사유(D-242) — 있을 때만 싣는다
            # 예외 원문(「SQL 실행 에러: {e}」 등)은 SSE 본문에 싣지 않는다 — 고정 사유만 보이고
            # 나머지는 고정 문구로 바꾼다(plans/163 S3+). 원문은 여기서 로그로 남긴다.
            shown = user_error_text(data["detail"])
            if shown != data["detail"]:
                logger.warning(
                    "진행 단계 실패 사유 (요청 %s · %s): %r",
                    current_request_id(), name, data["detail"],
                )
            base["detail"] = shown[:300]
    return base


def _token_text(chunk: object) -> str:
    """스트리밍 청크의 content를 텍스트로 정규화한다.

    Gemini 3.x thinking 계열은 content를 블록 리스트로 반환하는데, 이를 그대로
    SSE에 실으면 프론트가 배열을 문자열에 더해(`accumulatedText += event.content`)
    `[object Object]`가 되어 마크다운 표가 렌더되지 않는다.
    """
    return coerce_content_text(getattr(chunk, "content", ""))


def _sse_response(
    gen: AsyncIterator[str], *, accel_buffering: bool = True
) -> StreamingResponse:
    """SSE 스트림 응답 — 네 스트림 진입의 헤더 단일 출처(plans/168 A1).

    `accel_buffering=False`는 텍스트 스트림 선택 거부 응답의 현행 헤더(`X-Accel-Buffering`
    없음)를 보존하기 위한 것이다 — 헤더 차이는 바이트 불변 대상이다.
    """
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    if accel_buffering:
        headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(gen, media_type="text/event-stream", headers=headers)


async def _single_done_stream(payload: dict[str, Any]) -> AsyncGenerator[str, None]:
    """done 이벤트 1건만 내는 SSE 스트림 — 네 스트림 진입의 조기 반환 공유(plans/168 A3)."""
    yield _sse_event(payload)
