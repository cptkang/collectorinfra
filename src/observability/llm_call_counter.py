"""요청(프롬프트) 1건당 LLM 호출 수 계측기 — 차단 없는 순수 관측 (권고 ①⑥).

LangChain 콜백 핸들러로 모델 생성자 `callbacks=[get_handler()]`에 부착하면 그
인스턴스의 모든 invoke/ainvoke/stream에서 `on_chat_model_start`(방어적으로
`on_llm_start`도)가 발화하여 contextvars 기반 요청별 카운터를 증가시킨다.

사용 계약:
- 요청 진입점에서 ``start_request(request_id)`` → 처리 종료 시 ``finish_request()``.
- ``start_request`` 없이 이벤트가 발생하면(테스트/배치 등) no-op — 예외를 내지 않는다.
- 임계치(env ``LLM_CALL_WARN_THRESHOLD``, 기본 30)를 **넘는 순간 1회만** warning.
  차단·예외 발생은 절대 하지 않는다(동작 불변 — 관측 전용).

구현 노트:
- ``start_request``가 ContextVar에 dict 버킷을 심고, 핸들러는 그 dict를 **제자리
  변경**만 한다(ContextVar.set 재호출 없음). async 태스크/스레드로 컨텍스트가
  복사되어도 같은 dict 객체를 공유하므로 카운트가 유실되지 않는다.
- sync 핸들러(BaseCallbackHandler)이지만 async 실행도 지원한다: langchain-core의
  async 콜백 매니저는 coroutine이 아닌 핸들러를 ``run_inline=True``이면 이벤트
  루프에서 직접, 아니면 ``copy_context().run``으로 실행하므로 어느 쪽이든
  contextvars가 보존된다.
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

_DEFAULT_WARN_THRESHOLD = 30

# 요청 스코프 계측 버킷 (start_request가 dict를 심는다 — 모듈 밖에서 직접 접근 금지).
_request_ctx: ContextVar[Optional[dict]] = ContextVar(
    "llm_call_counter_ctx", default=None
)


def _warn_threshold() -> int:
    """env LLM_CALL_WARN_THRESHOLD를 안전 파싱한다 (실패 시 기본 30)."""
    raw = os.environ.get("LLM_CALL_WARN_THRESHOLD")
    if raw is None:
        return _DEFAULT_WARN_THRESHOLD
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_WARN_THRESHOLD


def start_request(request_id: str) -> None:
    """요청 스코프 계측 버킷을 초기화한다 (요청 진입점에서 1회 호출)."""
    _request_ctx.set(
        {
            "request_id": request_id,
            "llm_calls": 0,
            "by_model": {},
            "threshold": _warn_threshold(),
            "threshold_warned": False,
            "task_warned": False,
        }
    )


def finish_request() -> dict:
    """요청 계측을 종료하고 요약을 반환한다 + [LLM계측] info 로그 1줄.

    Returns:
        ``{"request_id", "llm_calls", "by_model": {model_name: count}}``
        (start_request 없이 호출되면 request_id=None, 카운트 0)
    """
    ctx = _request_ctx.get()
    if ctx is None:
        summary: dict = {"request_id": None, "llm_calls": 0, "by_model": {}}
    else:
        summary = {
            "request_id": ctx.get("request_id"),
            "llm_calls": ctx.get("llm_calls", 0),
            "by_model": dict(ctx.get("by_model") or {}),
        }
        _request_ctx.set(None)
    logger.info(
        "[LLM계측] request_id=%s llm_calls=%d by_model=%s",
        summary["request_id"],
        summary["llm_calls"],
        summary["by_model"],
    )
    return summary


def record_tool_use(tool_name: str) -> None:
    """오케스트레이터가 사용한 도구명을 기록한다 (관측 전용).

    deepagents 내장 ``task`` 도구(서브에이전트 위임)는 중첩 에이전트 루프로 LLM
    호출이 배가되므로, 요청당 첫 감지 시 1회만 warning을 남긴다.
    """
    ctx = _request_ctx.get()
    if ctx is None:
        return
    if tool_name == "task" and not ctx.get("task_warned"):
        ctx["task_warned"] = True
        logger.warning(
            "[LLM계측] task 내장 서브에이전트 첫 호출 감지 — 중첩 에이전트 비용 발생"
        )


def _model_name(serialized: Any, metadata: Any) -> str:
    """콜백 인자에서 모델명을 최대한 추출한다 (실패 시 'unknown')."""
    if isinstance(metadata, dict) and metadata.get("ls_model_name"):
        return str(metadata["ls_model_name"])
    if isinstance(serialized, dict):
        kwargs = serialized.get("kwargs")
        if isinstance(kwargs, dict):
            for key in ("model", "model_name", "chat_model", "asset_id"):
                if kwargs.get(key):
                    return str(kwargs[key])
        if serialized.get("name"):
            return str(serialized["name"])
    return "unknown"


def _count_call(serialized: Any, metadata: Any) -> None:
    """LLM 호출 1건을 계측한다 — 어떤 경우에도 예외를 전파하지 않는다."""
    try:
        ctx = _request_ctx.get()
        if ctx is None:
            return  # start_request 없는 경로(테스트/배치) — no-op
        ctx["llm_calls"] = ctx.get("llm_calls", 0) + 1
        model = _model_name(serialized, metadata)
        by_model = ctx.setdefault("by_model", {})
        by_model[model] = by_model.get(model, 0) + 1
        if not ctx.get("threshold_warned") and ctx["llm_calls"] > ctx.get(
            "threshold", _DEFAULT_WARN_THRESHOLD
        ):
            ctx["threshold_warned"] = True
            logger.warning(
                "[LLM계측] 요청당 LLM 호출 %d회 — 임계치 %d 초과 (request_id=%s)",
                ctx["llm_calls"],
                ctx.get("threshold", _DEFAULT_WARN_THRESHOLD),
                ctx.get("request_id"),
            )
    except Exception:  # noqa: BLE001 — 계측 실패가 요청 경로를 깨서는 안 된다
        logger.debug("[LLM계측] 카운터 갱신 실패(무시)", exc_info=True)


class LLMCallCounterHandler(BaseCallbackHandler):
    """모델 생성자 callbacks에 넣는 전역 계측 핸들러 (sync/async 겸용, 관측 전용)."""

    run_inline = True  # async 콜백 매니저가 이벤트 루프에서 직접 호출 → contextvars 보존
    raise_error = False

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        _count_call(serialized, kwargs.get("metadata"))

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        # 방어: 채팅 모델이 아닌 (완성형 LLM) 경로. 한 run은 on_chat_model_start와
        # on_llm_start 중 하나만 발화하므로 이중 계측되지 않는다.
        _count_call(serialized, kwargs.get("metadata"))


_handler: Optional[LLMCallCounterHandler] = None


def get_handler() -> BaseCallbackHandler:
    """모델 생성 시 ``callbacks=[...]``에 넣을 싱글턴 핸들러를 반환한다."""
    global _handler
    if _handler is None:
        _handler = LLMCallCounterHandler()
    return _handler
