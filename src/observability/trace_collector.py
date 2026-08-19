"""요청 스코프 단계 트레이스 수집기 (D-141).

노드별 실행 기록을 요청 단위 링버퍼에 누적한다. **파일은 쓰지 않는다** — 덤프는
`trace_writer`가 실패로 판정된 요청에 대해서만 수행한다. 정상 경로의 디스크 비용은 0이다.

버퍼는 두 방향으로 bound된다:
- 요청당 단계 수(`max_steps`) — 재시도 루프가 길어져도 메모리가 선형 증가하지 않는다
- 동시 요청 키 수(`_MAX_ACTIVE_REQUESTS`) — `end_request` 누락 시에도 dict가 무한히 자라지
  않는다(값 bound만으론 키 누수를 못 막는다는 교훈을 반영)
"""

from __future__ import annotations

import inspect
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from src.observability.levels import TraceLevel

logger = logging.getLogger(__name__)

#: 동시에 추적할 요청 수 상한. 초과 시 가장 오래 시작된 요청 버퍼부터 밀어낸다.
#: 요청 하나가 수백 KB를 넘지 않으므로 64개면 수십 MB 안에서 bound된다.
_MAX_ACTIVE_REQUESTS = 64

#: 요청당 기본 단계 상한 (설정 `OBS_TRACE_MAX_STEPS`가 주어지면 그 값을 쓴다).
_DEFAULT_MAX_STEPS = 200


@dataclass
class TraceStep:
    """단일 노드 실행 기록.

    Raises:
        ValueError: ERROR·WARN인데 구조화 `reason`이 없을 때. 사후 집계가 메시지 문구에
            의존하지 않도록 기록 시점에 강제한다.
    """

    step: int
    node: str
    level: TraceLevel
    event: str
    elapsed_ms: float
    reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.level.requires_reason and not self.reason:
            raise ValueError(
                f"{self.level.value} 단계에는 구조화 reason이 필요합니다 "
                f"(node={self.node}, event={self.event})"
            )


@dataclass
class _RequestBuffer:
    """한 요청의 수집 상태."""

    meta: dict[str, Any]
    steps: deque[TraceStep]
    started_at: float
    counter: int = 0
    #: 실패 판정에 필요한 신호만 축약해 보관한다. 노드가 볼 때마다 갱신되므로
    #: 진입점은 최종 state를 몰라도 `flush_if_failed(request_id)`만 부르면 된다.
    observed: dict[str, Any] = field(default_factory=dict)


#: request_id -> 버퍼. 삽입 순서를 유지해 오래된 것부터 밀어낸다.
_buffers: "OrderedDict[str, _RequestBuffer]" = OrderedDict()


def reset_all() -> None:
    """모든 수집 상태를 비운다 (테스트 전용)."""
    _buffers.clear()


def active_request_count() -> int:
    """추적 중인 요청 수를 반환한다."""
    return len(_buffers)


def start_request(
    request_id: str,
    *,
    thread_id: str | None = None,
    user_query: str = "",
    max_steps: int = _DEFAULT_MAX_STEPS,
) -> None:
    """요청 추적을 시작한다.

    Args:
        request_id: 요청 추적 ID (AuditMiddleware가 생성)
        thread_id: 멀티턴 세션 식별자
        user_query: 자연어 질의 (요약 헤더용)
        max_steps: 링버퍼 단계 상한
    """
    if not request_id:
        return
    try:
        _buffers[request_id] = _RequestBuffer(
            meta={
                "request_id": request_id,
                "thread_id": thread_id,
                "user_query": user_query,
            },
            steps=deque(maxlen=max(1, max_steps)),
            started_at=time.perf_counter(),
        )
        _buffers.move_to_end(request_id)
        while len(_buffers) > _MAX_ACTIVE_REQUESTS:
            evicted, _ = _buffers.popitem(last=False)
            logger.debug("트레이스 버퍼 축출(동시 요청 상한 초과): %s", evicted)
    except Exception as e:  # pragma: no cover - 방어
        logger.debug("트레이스 시작 실패(무시): %s", e)


def record_step(request_id: str, step: TraceStep) -> None:
    """단계 기록을 누적한다.

    시작되지 않은 요청(`start_request` 미호출)의 기록은 버린다 — 임의의 키로 dict가
    자라는 것을 막는다.

    Note:
        수집 실패는 메인 로직에 영향을 주면 안 되므로 예외를 삼키고 debug만 남긴다.
    """
    try:
        buf = _buffers.get(request_id)
        if buf is None:
            return
        buf.steps.append(step)
    except Exception as e:  # pragma: no cover - 방어
        logger.debug("트레이스 기록 실패(무시): %s", e)


def next_step_number(request_id: str) -> int:
    """다음 단계 번호를 발급한다 (밀려난 뒤에도 단조 증가)."""
    buf = _buffers.get(request_id)
    if buf is None:
        return 0
    buf.counter += 1
    return buf.counter


#: 실패 판정이 참조하는 키. 이 목록 밖의 state는 담지 않는다(메모리·민감정보 최소화).
_SIGNAL_KEYS = (
    "error_message", "current_node", "routing_intent",
    "retry_count", "file_type", "output_file",
)


def _shrink_signals(source: Mapping[str, Any]) -> dict[str, Any]:
    """판정에 필요한 신호만 뽑아 축약한다.

    `query_results`·`output_file`은 통째로 담으면 버퍼가 MB 단위로 부푼다. 판정은
    "비었는가"만 보므로 크기 정보만 남는 대역으로 바꾼다.
    """
    out: dict[str, Any] = {}
    for k in _SIGNAL_KEYS:
        if k in source:
            v = source[k]
            # 파일 바이너리는 존재 여부만 필요하다.
            out[k] = bool(v) if k == "output_file" else v

    if "query_results" in source:
        rows = source["query_results"]
        out["query_results"] = [] if not isinstance(rows, list) or not rows else [None]

    if "smq_derivation" in source:
        has_unresolved = False
        entries = source["smq_derivation"]
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, Mapping) and isinstance(e.get("unresolved"), list) and e["unresolved"]:
                    has_unresolved = True
                    break
        out["smq_derivation"] = [{"unresolved": [None]}] if has_unresolved else []

    return out


def observe_state(request_id: str, source: Any) -> None:
    """노드가 본 state(또는 반환 델타)에서 실패 신호를 갱신한다.

    LangGraph 노드는 진입 시 **누적 state 전체**를 받고 델타를 반환하므로, 둘을 합쳐
    관찰하면 최종 상태에 준하는 신호를 얻는다. 덕분에 진입점(HTTP 라우트·CLI)은
    최종 state를 넘기지 않아도 된다.
    """
    try:
        if not isinstance(source, Mapping):
            return
        buf = _buffers.get(request_id)
        if buf is None:
            return
        buf.observed.update(_shrink_signals(source))
    except Exception as e:  # pragma: no cover - 방어
        logger.debug("트레이스 신호 관찰 실패(무시): %s", e)


def observed_state(request_id: str) -> dict[str, Any]:
    """관찰된 실패 신호를 반환한다."""
    buf = _buffers.get(request_id)
    return dict(buf.observed) if buf else {}


def steps_for(request_id: str) -> list[TraceStep]:
    """요청의 누적 단계를 순서대로 반환한다."""
    buf = _buffers.get(request_id)
    return list(buf.steps) if buf else []


def meta_for(request_id: str) -> dict[str, Any] | None:
    """요청 메타(thread_id·user_query 등)를 반환한다."""
    buf = _buffers.get(request_id)
    return dict(buf.meta) if buf else None


def elapsed_ms_for(request_id: str) -> float:
    """요청 시작 이후 경과 시간(ms)."""
    buf = _buffers.get(request_id)
    return (time.perf_counter() - buf.started_at) * 1000 if buf else 0.0


def end_request(request_id: str) -> None:
    """요청 추적을 종료하고 버퍼를 해제한다."""
    _buffers.pop(request_id, None)


def _summarize_delta(result: Any) -> dict[str, Any]:
    """노드 반환 델타를 요약한다 (원본을 통째로 담지 않는다).

    값 전체를 담으면 버퍼가 수 MB로 부풀고 민감 데이터가 섞인다. 키 목록과
    행 수 같은 크기 정보만 남긴다.
    """
    if not isinstance(result, Mapping):
        return {}
    summary: dict[str, Any] = {"keys": sorted(str(k) for k in result.keys())[:40]}
    rows = result.get("query_results")
    if isinstance(rows, list):
        summary["row_count"] = len(rows)
    if result.get("error_message"):
        summary["has_error"] = True
    return summary


def traced(
    fn: Callable[..., Any],
    *,
    name: str,
) -> Callable[[Any], Awaitable[Any]]:
    """노드 함수를 감싸 진입·이탈·예외를 자동 기록한다.

    `graph.py`의 `add_node` 등록 지점에서 일괄 적용하므로, 노드 파일은 수정하지 않는다.
    조건부로 등록되는 노드도 자동으로 편입된다.

    Args:
        fn: 원본 노드 함수 (동기·비동기 모두 지원)
        name: 그래프에 등록된 노드 이름

    Returns:
        같은 시그니처의 async 래퍼. 원본 반환값·예외를 그대로 전달한다.
    """

    async def _wrapper(state: Any, *args: Any, **kwargs: Any) -> Any:
        request_id = ""
        try:
            if isinstance(state, Mapping):
                request_id = state.get("request_id") or ""
        except Exception:  # pragma: no cover - 방어
            request_id = ""

        # 추적 대상이 아니면 계측 없이 그대로 실행한다(오버헤드 0).
        if not request_id or request_id not in _buffers:
            result = fn(state, *args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        observe_state(request_id, state)
        _safe_record(
            request_id,
            node=name,
            level=TraceLevel.INFO,
            event="node.enter",
            elapsed_ms=0.0,
        )
        started = time.perf_counter()
        try:
            result = fn(state, *args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:
            observe_state(request_id, {"error_message": str(e)[:500]})
            _safe_record(
                request_id,
                node=name,
                level=TraceLevel.ERROR,
                event="node.exception",
                elapsed_ms=(time.perf_counter() - started) * 1000,
                reason=type(e).__name__,
                payload={"error": str(e)[:500]},
            )
            raise

        observe_state(request_id, result)
        _safe_record(
            request_id,
            node=name,
            level=TraceLevel.INFO,
            event="node.exit",
            elapsed_ms=(time.perf_counter() - started) * 1000,
            payload=_summarize_delta(result),
        )
        return result

    _wrapper.__name__ = f"traced_{name}"
    # 원본을 표준 속성으로 노출한다. 노드는 대개 `functools.partial`로 등록되고,
    # 배선 검증은 컴파일된 그래프에서 그 partial의 keywords를 들여다본다
    # (`tests/test_orchestration/test_deep_agent_wiring.py`). 래핑이 그 인트로스펙션을
    # 가로막으면 관측 계층 추가만으로 **배선 검사가 조용히 무력화**된다 —
    # `inspect.unwrap()`이 원본까지 따라갈 수 있게 한다.
    _wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
    return _wrapper


def _safe_record(
    request_id: str,
    *,
    node: str,
    level: TraceLevel,
    event: str,
    elapsed_ms: float,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """단계 생성·기록을 예외 없이 수행한다."""
    try:
        record_step(
            request_id,
            TraceStep(
                step=next_step_number(request_id),
                node=node,
                level=level,
                event=event,
                elapsed_ms=elapsed_ms,
                reason=reason,
                payload=payload or {},
            ),
        )
    except Exception as e:  # pragma: no cover - 방어
        logger.debug("트레이스 단계 생성 실패(무시): %s", e)
