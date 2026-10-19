"""외부 소스 어댑터 슬롯 — ITSM · DPM (plans/187 §6 · D-341 ⑤).

플레이북 스텝(S7 변경의 ITSM 축 · S8 DPM)이 외부 소스를 부르는 계약이다. 실 연동은 제품·API·
자격증명 확인 뒤 별 계획(G-6)이고, 지금은 스텁(`NotConnectedAdapter`)만 둔다 — 스텁은 늘
`skipped: not_connected`를 내서 브리핑에 「미연동」으로 보이게 한다(침묵 생략 금지).

**순수 도메인 모듈**(stdlib만) — 어댑터 구현이 IO를 하더라도 이 모듈은 계약·스텁·레지스트리만 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: 어댑터 이름.
ITSM = "itsm"
DPM = "dpm"

#: 미연동 사유(스텝 결과 `reason`) — `playbook_steps.NOT_CONNECTED`와 같은 값.
NOT_CONNECTED = "not_connected"


@runtime_checkable
class SourceAdapter(Protocol):
    """외부 소스 어댑터 계약(§6).

    name:      어댑터 이름(`itsm`·`dpm` …).
    available: 연동돼 호출할 수 있으면 참.
    fetch:     스텝 ID · 범위(호스트·인스턴스 등) · 창(사건창·기준 시각)을 받아 `StepResult` 호환
               dict(`status`·`reason`·`data`·`missing` …)를 돌려준다.
    """

    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def fetch(
        self, step_id: str, scope: Mapping[str, Any], window: Mapping[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class NotConnectedAdapter:
    """미연동 스텁 — 늘 `available() is False`, `fetch`는 `skipped: not_connected`."""

    name: str

    def available(self) -> bool:
        """스텁은 연동되지 않았다."""
        return False

    def fetch(
        self, step_id: str, scope: Mapping[str, Any], window: Mapping[str, Any]
    ) -> dict[str, Any]:
        """`skipped: not_connected` 결과(입력은 쓰지 않는다)."""
        return not_connected_result(self.name)


def not_connected_result(name: str) -> dict[str, Any]:
    """미연동 어댑터의 `StepResult` 호환 dict."""
    return {
        "status": "skipped",
        "reason": NOT_CONNECTED,
        "data": {"adapter": name, "connected": False},
    }


@dataclass(frozen=True)
class AdapterRegistry:
    """이름 → 어댑터. 등록되지 않은 이름은 미연동 스텁을 돌려준다(기본 = 전부 스텁)."""

    adapters: Mapping[str, SourceAdapter] = field(default_factory=dict)

    def get(self, name: str) -> SourceAdapter:
        """이름의 어댑터(없으면 `NotConnectedAdapter(name)`)."""
        return self.adapters.get(name) or NotConnectedAdapter(name)


#: 기본 레지스트리 — ITSM·DPM 모두 미연동 스텁.
DEFAULT_REGISTRY = AdapterRegistry({ITSM: NotConnectedAdapter(ITSM), DPM: NotConnectedAdapter(DPM)})


def fetch_or_not_connected(
    adapter: SourceAdapter,
    step_id: str,
    scope: Mapping[str, Any],
    window: Mapping[str, Any],
) -> dict[str, Any]:
    """연동된 어댑터면 `fetch` 결과, 아니면 `skipped: not_connected`.

    연동 어댑터의 예외는 삼키지 않고 `missing` + 사유로 바꾼다(독립 신호 · 침묵 금지).
    """
    if not adapter.available():
        return not_connected_result(adapter.name)
    try:
        return dict(adapter.fetch(step_id, scope, window))
    except Exception as exc:  # noqa: BLE001 — 외부 소스 1개 실패가 스텝 전체를 깨지 않게
        return {
            "status": "missing",
            "reason": f"{adapter.name}: {type(exc).__name__}: {exc}",
            "data": {"adapter": adapter.name, "connected": True},
        }


__all__ = [
    "DEFAULT_REGISTRY",
    "DPM",
    "ITSM",
    "NOT_CONNECTED",
    "AdapterRegistry",
    "NotConnectedAdapter",
    "SourceAdapter",
    "fetch_or_not_connected",
    "not_connected_result",
]
