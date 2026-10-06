"""측정 연결점 — 프로세스 전역 수신 함수 하나 (plans/135 §3.6 · D-301 ⑥).

조회 파이프라인이 task 결과를 접는 자리(`src/orchestration/subagents.py` `_pack_pipeline_result`)가
`emit()`을 부른다. **수신 함수가 없으면 즉시 반환한다** — 기본 동작은 비트 동일하다.

설치는 측정 전용 서버 진입점에서만 한다(벤치 서버 `scripts/itam_bench/_serve.py`). 운영 기동과
시나리오 하네스 기동에는 설치 코드가 없다.

계약:
- 수신 함수의 예외는 삼키고 debug 로그만 남긴다 — 측정이 제품 흐름을 깨지 않는다.
- 수신 함수는 받은 객체를 **보관하지 않는다**. 필요한 이름·불린만 그 자리에서 복사하고 놓는다
  (값·사용자 필드를 읽지 않는 것도 수신 측 책임이다).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: 수신 함수 — (종류, 페이로드). 반환값은 쓰지 않는다.
Sink = Callable[[str, Any], None]

_sink: Sink | None = None


def install(sink: Sink) -> None:
    """수신 함수를 꽂는다(프로세스 전역 · 하나만). 다시 부르면 바꾼다."""
    global _sink
    _sink = sink


def uninstall() -> None:
    global _sink
    _sink = None


def installed() -> bool:
    return _sink is not None


def emit(kind: str, payload: Any) -> None:
    """수신 함수가 있으면 넘긴다. 없으면 아무것도 하지 않는다."""
    sink = _sink
    if sink is None:
        return
    try:
        sink(kind, payload)
    except Exception:  # noqa: BLE001 — 측정 실패가 조회 흐름을 바꾸면 안 된다
        logger.debug("측정 수신 함수 실패(무시) kind=%s", kind, exc_info=True)
