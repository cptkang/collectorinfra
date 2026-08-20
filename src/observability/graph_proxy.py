"""LangGraph `StateGraph` 얇은 프록시 — 노드 트레이싱을 일괄 적용한다 (D-141).

## 왜 프록시인가

`build_graph`의 `add_node` 호출은 20여 곳이고 상당수가 플래그 조건부다
(`use_deep_agent`·`enable_semantic_routing`·`fault_diagnosis_enabled`). 호출부마다
데코레이터를 붙이면 한 곳만 빠뜨려도 그 경로만 관측이 비는 **비대칭**이 생기고,
새 노드가 추가될 때 또 빠진다.

`StateGraph(...)`를 한 번 감싸면 `add_node`만 가로채고 나머지는 원본에 위임하므로
조건부·신규 노드가 자동으로 편입되고, 노드 파일은 한 줄도 고치지 않는다.

위임이 어긋나면 그래프 전체가 불능이 되므로 계약은 테스트로 고정한다
(`tests/test_observability/test_graph_proxy.py`).
"""

from __future__ import annotations

import logging
from typing import Any

from src.observability.trace_collector import traced

logger = logging.getLogger(__name__)


class TracedGraph:
    """`add_node`만 가로채고 나머지는 원본 `StateGraph`에 위임하는 프록시.

    Attributes:
        raw: 감싼 원본 `StateGraph`. 위임되지 않는 특수 동작이 필요할 때 직접 쓴다.
    """

    def __init__(self, graph: Any, *, enabled: bool = True) -> None:
        """프록시를 만든다.

        Args:
            graph: 원본 `StateGraph` 인스턴스
            enabled: False면 원본 함수를 그대로 등록한다(비트동일 — OBS_TRACE_ENABLED off)
        """
        # `__getattr__`이 이 두 속성을 위임으로 넘기지 않도록 `__dict__`에 직접 넣는다.
        object.__setattr__(self, "raw", graph)
        object.__setattr__(self, "_enabled", enabled)

    def add_node(self, name: Any, action: Any = None, **kwargs: Any) -> Any:
        """노드를 등록하되 트레이싱 래퍼를 씌운다.

        LangGraph는 `add_node(name, action)`과 `add_node(action)`(함수명이 노드명)
        두 형태를 모두 받는다. 후자에서도 이름을 잃지 않도록 분기한다.
        """
        if action is None:
            action, name = name, getattr(name, "__name__", "unnamed")

        if not self._enabled:
            return self.raw.add_node(name, action, **kwargs)

        try:
            wrapped = traced(action, name=str(name))
        except Exception as e:
            # 래핑 실패가 그래프 빌드를 막으면 안 된다 — 관측을 포기하고 원본을 등록한다.
            logger.warning("노드 트레이싱 래핑 실패(%s), 원본 등록: %s", name, e)
            wrapped = action

        return self.raw.add_node(name, wrapped, **kwargs)

    def __getattr__(self, item: str) -> Any:
        """가로채지 않는 모든 속성·메서드를 원본에 위임한다.

        `add_edge`·`add_conditional_edges`·`set_entry_point`·`compile` 등이 여기로 간다.
        """
        return getattr(object.__getattribute__(self, "raw"), item)

    def __setattr__(self, key: str, value: Any) -> None:
        """속성 설정도 원본에 위임한다 (프록시가 상태를 따로 갖지 않게)."""
        setattr(object.__getattribute__(self, "raw"), key, value)
