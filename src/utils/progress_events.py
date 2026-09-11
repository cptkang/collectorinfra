"""노드·핸들러 내부 마일스톤 이벤트 — 계층 무관 공통 발행 (plans/89 §3.2-③ · T4 · D-204).

긴 무이벤트 구간(라이브 샘플 수집·서브에이전트 파이프라인 단계·deep_agent 재개/합성) 앞뒤에서
LangChain custom event를 낸다. 바깥 그래프의 `astream_events(v2)`가 `on_custom_event`로 올리고
SSE 라우트가 `progress{kind:"step"}`으로 변환한다(`src/api/routes/query.py::_progress_sse_payload`).

`utils` 계층에 두는 이유: `src/nodes`(application)와 `src/orchestration`이 **같은 함수**를 써야
하는데 nodes → orchestration 참조는 계층 위반이다(`scripts/arch_check.py`).

- 판정·상태 변경 없음. 부모 run이 없는 컨텍스트(단위 테스트·CLI)에서는 `RuntimeError`가 나므로
  삼키고 debug 로그만 남긴다 — 진행 표시 실패는 질의를 죽이지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.callbacks.manager import adispatch_custom_event

logger = logging.getLogger(__name__)


async def dispatch_progress_event(name: str, data: dict) -> None:
    """custom event를 낸다. 부모 run이 없으면 조용히 건너뛴다."""
    try:
        await adispatch_custom_event(name, data)
    except RuntimeError as e:  # 부모 run 없음(그래프 밖 호출) — 진행 표시만 생략
        logger.debug("progress event 생략(%s): %s", name, e)
    except Exception as e:  # noqa: BLE001 — 표시 실패가 질의를 죽이면 안 된다
        logger.warning("progress event 발행 실패(%s): %s", name, e)


async def emit_step(name: str, phase: str = "start", *, label: Optional[str] = None) -> None:
    """노드 내부 마일스톤(예: `schema.sample`·`pipeline.generate`·`agent.resume`)을 낸다."""
    data: dict[str, Any] = {"phase": phase}
    if label:
        data["label"] = label
    await dispatch_progress_event(name, data)
