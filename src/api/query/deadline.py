"""요청 마감·처리 상한·스트림 감시·타임라인.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Mapping
from contextvars import Token
from typing import Any

from src.api.stream_failure import StreamWatch
from src.config import AppConfig
from src.utils.deadline import bind_request_deadline, unbind_request_deadline

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


def _request_deadline(limit_sec: float) -> float | None:
    """이 요청의 마감 시각(`time.monotonic()` 기준 초) — 그래프 입력 상태에 싣는다(plans/118 P-1).

    재계획기가 남은 시간이 직전 한 바퀴보다 짧으면 후속을 붙이지 않는다. **요청 스코프 값이라
    매 턴 라우트가 명시 초기화한다**(체크포인터는 델타만 병합한다 — CLAUDE.md Known Mistakes).
    상한이 0 이하(상한 없음)면 None 이다 — 재계획기는 종전 동작이다.
    """
    return time.monotonic() + float(limit_sec) if limit_sec and limit_sec > 0 else None


def _processing_limit(config: AppConfig, *, file_turn: bool) -> float:
    """처리 상한(초) — 요청 후 **첫 답변(표 또는 첫 토큰)까지** (plans/119 T-5 · D-267 ⑦ G-7).

    네 진입점이 이 함수 하나로 정한다(D-066). 파일 턴 — 양식 업로드와 폼필 답변 턴(FIX-18: 양식
    재채움 전체 파이프라인이라 파일 런과 부하가 같다) — 은 `API_FILE_QUERY_TIMEOUT`, 나머지는
    `API_QUERY_TIMEOUT`. 비스트림 경로는 첫 답변 개념이 없어 이 값이 곧 전체 상한이다(종전과
    같다). 스트림 경로는 첫 답변 뒤 토큰 간 idle·전달 연장 상한으로 넘어간다(`StreamCaps`).
    """
    return config.server.file_query_timeout if file_turn else config.server.query_timeout


def _sec_or(value: Any, default: float) -> float:
    """초 단위 설정값 — 숫자가 아니면 기본값(테스트 대역·구 설정 호환).

    필드명은 호출부가 `getattr(config.server, "필드", 기본)` 처럼 **상수로** 읽는다 — 이름을
    변수로 넘기면 벤치 소비처 색인(`scripts/bench/axes.py`)이 동적 접근으로 보고 판정을 보류한다.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bind_deadline(state: Mapping[str, Any], config: Any, *, stream: bool) -> Token[Any]:
    """그래프 실행 구간에 요청 마감을 묶는다 (plans/119 T-1 · D-267 ⑥).

    LLM 클라이언트는 그래프 상태를 받지 않으므로 같은 마감(`request_deadline`)과 서술 예약
    (`API_ANSWER_RESERVE_SEC`)을 ContextVar로 묶는다(`src/utils/deadline.py`). asyncio 태스크는
    **생성 시** 컨텍스트를 복사하므로 그래프 실행(비스트림 `wait_for` · 스트림 생산자 태스크)을
    시작하기 **전에** 불러야 노드·서브에이전트 태스크까지 보인다. 네 진입점이 공유한다(D-066).
    """
    return bind_request_deadline(
        state.get("request_deadline"),
        _sec_or(config.server.answer_reserve_sec, 0),
        # 전달 연장은 스트림만 — 최종 응답 스트림의 호출 상한이 처리 마감 + 이 값까지다(G-7).
        # 비스트림은 첫 답변 개념이 없어 처리 상한이 곧 전체 상한이다.
        _sec_or(config.server.stream_delivery_grace_sec, 0) if stream else 0.0,
    )


def _unbind_deadline(token: Token[Any]) -> None:
    """`_bind_deadline` 해제. 스트림 제너레이터가 다른 컨텍스트에서 닫히면(비정상 종료 뒤 GC
    정리) reset 이 거부된다 — 그 컨텍스트는 요청과 함께 사라지므로 무시한다."""
    with contextlib.suppress(ValueError):
        unbind_request_deadline(token)


def _stream_watch(config: Any, limit_sec: float, start_time: float) -> StreamWatch:
    """스트림 요청 한 건의 상한·타임라인 — 두 스트림 라우트가 같은 조립을 쓴다(D-066)."""
    return StreamWatch(
        limit_sec=limit_sec,
        idle_sec=_sec_or(getattr(config.server, "stream_idle_timeout_sec", 30), 30),
        grace_sec=_sec_or(getattr(config.server, "stream_delivery_grace_sec", 60), 60),
        start_time=start_time,
    )


def _answer_prefix_text(event: Any) -> str:
    """`answer_prefix` custom event 의 본문(코드 렌더 표 · plans/119 N-1). 없으면 빈 문자열."""
    data = event.get("data")
    text = data.get("text") if isinstance(data, dict) else None
    return text if isinstance(text, str) else ""


def _finish_timeline(
    watch: StreamWatch, query_id: str, *, timeout_kind: str | None = None, done: bool = False
) -> dict[str, Any]:
    """단계 타임라인을 확정하고 서버 로그 한 줄로 남긴다 (plans/119 T-0 — 하네스 수집 대상)."""
    timeline = watch.timeline_payload(timeout_kind=timeout_kind, done=done)
    logger.info("[timeline] query_id=%s %s", query_id, json.dumps(timeline, ensure_ascii=False))
    return timeline
