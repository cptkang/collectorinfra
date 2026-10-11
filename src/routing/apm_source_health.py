"""제니퍼 소스 정합 점검 — 레지스트리 소스 표 ↔ 게이트웨이 `gateway_health`(plans/147 W4 · D-322 ⑥).

본체 기동 시 1회(그리고 관리 API의 재점검) 레지스트리 `solutions[apm].sources[]`와 게이트웨이가
실제로 설정한 소스를 대조한다.

- 레지스트리∖게이트웨이(**미연결**) → 사용 가능 집합에서 뺀다(되묻기 선택지·화면 축에 뜨지 않는다).
- 게이트웨이∖레지스트리(존·단어 미상) → 선택 불가로 표시만 한다(사용 가능 집합은 레지스트리
  안에서만).
- 설정은 됐으나 도달 실패 → **빼지 않는다**(조회 시 종전 실패 고지) — 보고서·로그에 강조만.
- 점검 실패(게이트웨이 다운·타임아웃·계약 밖 응답) → `available=None`(레지스트리 전 소스 사용 가능 —
  선택지를 비우지 않는다).

보고서에는 소스 id·라벨·존·단어 수·상태만 싣는다 — URL·토큰은 싣지 않는다(입력에도 없다).

계층: infrastructure(routing). 세션은 `src.clients.source_mcp_client`로 연다 — 게이트웨이 패키지를
import 하지 않는다(D-274 ③).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from src.clients.source_mcp_client import SessionFactory, SourceMcpError, open_source_session
from src.routing.registry import SourceSpec

logger = logging.getLogger(__name__)

#: 기동 점검 상한(초) — 연결·호출 전체. 넘으면 점검 실패(= 전 소스 사용 가능)로 두고 기동을
#: 계속한다.
CHECK_TIMEOUT_SECONDS = 5.0
#: 게이트웨이 헬스 도구 이름(MCP 계약).
HEALTH_TOOL = "gateway_health"
_LABEL = "APM 게이트웨이"

#: 소스별 상태 어휘.
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"            # 도달은 되나 게이트웨이가 degraded(도메인 0건 등)
STATUS_UNREACHABLE = "unreachable"      # 설정은 됐으나 제니퍼 도달 실패 — 선택지에서 빼지 않는다
STATUS_REGISTRY_ONLY = "registry_only"  # 미연결 — 선택지에서 뺀다
STATUS_GATEWAY_ONLY = "gateway_only"    # 존·단어 미상 — 선택 불가
STATUS_UNKNOWN = "unknown"              # 점검 실패 — 판정 보류(사용 가능으로 둔다)


@dataclass(frozen=True)
class SourceHealth:
    """소스 1건의 정합 결과."""

    id: str
    label: str
    zone: str
    term_count: int
    in_registry: bool
    gateway_configured: bool | None  # None = 점검 실패로 모름
    reachable: bool | None
    domain_count: int | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "zone": self.zone,
            "term_count": self.term_count, "in_registry": self.in_registry,
            "gateway_configured": self.gateway_configured, "reachable": self.reachable,
            "domain_count": self.domain_count, "status": self.status,
        }


@dataclass(frozen=True)
class SourceHealthReport:
    """정합 점검 보고서.

    Attributes:
        sources: 레지스트리 선언 순서 → 게이트웨이에만 있는 소스(게이트웨이 순서)
        registry_only · gateway_only · unreachable: 불일치 id 목록
        available: 사용 가능 소스 id(레지스트리 ∩ 게이트웨이 설정) — None = 점검 실패(전부
            사용 가능)
        check_ok: 게이트웨이 응답을 해석했는가
        error: 점검 실패 사유(짧은 문구 — URL·토큰 없음)
        checked_at: 점검 시각(ISO · 순수 판정은 빈 값)
        registry_ids: 레지스트리 소스 id(선언 순서 — 목록 표시 순서)
    """

    sources: tuple[SourceHealth, ...]
    registry_only: tuple[str, ...]
    gateway_only: tuple[str, ...]
    unreachable: tuple[str, ...]
    available: frozenset[str] | None
    check_ok: bool
    error: str | None = None
    checked_at: str = ""
    registry_ids: tuple[str, ...] = ()

    @property
    def consistent(self) -> bool:
        """점검이 성공했고 불일치·도달 실패가 0건인가."""
        return self.check_ok and not (self.registry_only or self.gateway_only or self.unreachable)

    def to_dict(self) -> dict[str, Any]:
        """관리 API 응답 본문(사용 가능 집합은 레지스트리 선언 순서)."""
        available = (None if self.available is None
                     else [sid for sid in self.registry_ids if sid in self.available])
        return {
            "check_ok": self.check_ok, "error": self.error, "checked_at": self.checked_at or None,
            "consistent": self.consistent, "available": available,
            "registry_only": list(self.registry_only), "gateway_only": list(self.gateway_only),
            "unreachable": list(self.unreachable),
            "sources": [s.to_dict() for s in self.sources],
        }

    def log_line(self) -> str:
        """기동 로그 1줄 — 소스 id와 불일치 요약만(비밀 값 0)."""
        if not self.check_ok:
            return (f"제니퍼 소스 정합 점검 실패({self.error}) — 레지스트리 전 소스를 사용 가능으로"
                    f" 둔다(레지스트리 {list(self.registry_ids)})")
        available = [sid for sid in self.registry_ids if self.available and sid in self.available]
        parts = [f"사용 가능 {available}"]
        if self.registry_only:
            parts.append(f"미연결(레지스트리만 · 선택지 제외) {list(self.registry_only)}")
        if self.gateway_only:
            parts.append(f"게이트웨이만(존·단어 미상 · 선택 불가) {list(self.gateway_only)}")
        if self.unreachable:
            parts.append(f"도달 실패 {list(self.unreachable)}")
        return "제니퍼 소스 정합 점검: " + " · ".join(parts)


def _gateway_rows(
    envelope: Mapping[str, Any] | None,
) -> tuple[list[Mapping[str, Any]] | None, str | None]:
    """봉투 → (소스 행, 실패 사유). 계약 밖 응답은 점검 실패로 읽는다."""
    if envelope is None:
        return None, "응답 없음"
    if not isinstance(envelope, Mapping):
        return None, "응답 형식 오류"
    if envelope.get("error"):
        return None, f"게이트웨이 오류 {str(envelope.get('error'))[:60]}"
    rows = envelope.get("rows")
    if not isinstance(rows, list):
        return None, "응답에 rows 없음"
    good = [r for r in rows if isinstance(r, Mapping) and isinstance(r.get("source_id"), str)]
    return good, None


def reconcile(
    registry_sources: Sequence[SourceSpec],
    health_envelope: Mapping[str, Any] | None,
    *,
    error: str | None = None,
) -> SourceHealthReport:
    """레지스트리 소스 표와 `gateway_health` 봉투를 대조한다(순수 함수).

    Args:
        registry_sources: `get_registry().sources_of("apm")`(선언 순서)
        health_envelope: `gateway_health` 반환 봉투 — None이면 점검 실패
        error: 호출 측이 이미 아는 실패 사유(연결 실패·타임아웃) — 주면 봉투를 보지 않는다

    Returns:
        정합 보고서. 점검 실패면 `available=None`·소스 상태 `unknown`.
    """
    order = tuple(s.id for s in registry_sources)
    rows, reason = (None, error) if error else _gateway_rows(health_envelope)
    if rows is None:
        unknown = tuple(
            SourceHealth(id=s.id, label=s.label, zone=s.zone, term_count=len(s.terms),
                         in_registry=True, gateway_configured=None, reachable=None,
                         domain_count=None, status=STATUS_UNKNOWN)
            for s in registry_sources
        )
        return SourceHealthReport(sources=unknown, registry_only=(), gateway_only=(),
                                  unreachable=(), available=None, check_ok=False,
                                  error=reason or "점검 실패", registry_ids=order)

    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        by_id.setdefault(row["source_id"], row)
    configured = {sid for sid, row in by_id.items() if bool(row.get("jennifer_configured", True))}

    results: list[SourceHealth] = []
    registry_only: list[str] = []
    unreachable: list[str] = []
    for spec in registry_sources:
        found = by_id.get(spec.id)
        if found is None or spec.id not in configured:
            registry_only.append(spec.id)
            results.append(SourceHealth(
                id=spec.id, label=spec.label, zone=spec.zone, term_count=len(spec.terms),
                in_registry=True, gateway_configured=False, reachable=None,
                domain_count=None, status=STATUS_REGISTRY_ONLY))
            continue
        reachable = bool(found.get("jennifer_reachable"))
        if not reachable:
            unreachable.append(spec.id)
            status = STATUS_UNREACHABLE
        else:
            status = STATUS_OK if found.get("status") == STATUS_OK else STATUS_DEGRADED
        results.append(SourceHealth(
            id=spec.id, label=spec.label, zone=spec.zone, term_count=len(spec.terms),
            in_registry=True, gateway_configured=True, reachable=reachable,
            domain_count=_int_or_none(found.get("domain_count")), status=status))

    known = set(order)
    gateway_only = [sid for sid in by_id if sid not in known]
    for sid in gateway_only:
        row = by_id[sid]
        results.append(SourceHealth(
            id=sid, label="", zone="", term_count=0, in_registry=False,
            gateway_configured=sid in configured, reachable=bool(row.get("jennifer_reachable")),
            domain_count=_int_or_none(row.get("domain_count")), status=STATUS_GATEWAY_ONLY))

    available = frozenset(sid for sid in order if sid in configured)
    return SourceHealthReport(
        sources=tuple(results), registry_only=tuple(registry_only),
        gateway_only=tuple(gateway_only), unreachable=tuple(unreachable),
        available=available, check_ok=True, error=None, registry_ids=order)


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def check_sources(
    url: str,
    token: str | None,
    registry_sources: Sequence[SourceSpec],
    *,
    timeout: float | None = None,
    session_factory: SessionFactory | None = None,
) -> SourceHealthReport:
    """게이트웨이 `gateway_health`를 1회 불러 정합 보고서를 만든다 — 예외를 올리지 않는다.

    연결·호출 전체를 `timeout`(None = `CHECK_TIMEOUT_SECONDS`)으로 묶는다. 실패 사유 문구에는
    URL·토큰을 싣지 않는다.
    """
    timeout = CHECK_TIMEOUT_SECONDS if timeout is None else timeout
    envelope: dict[str, Any] | None = None
    error: str | None = None
    try:
        envelope = await asyncio.wait_for(
            _call_health(url, token, timeout=timeout, session_factory=session_factory),
            timeout=timeout,
        )
    except TimeoutError:
        error = f"시간 초과({timeout:g}초)"
    except SourceMcpError as e:
        error = str(e)[:200]
    except Exception as e:  # noqa: BLE001 — 점검 실패는 기동을 막지 않는다(사유만 남긴다)
        error = f"점검 오류: {type(e).__name__}"
    report = reconcile(registry_sources, envelope, error=error)
    return replace(report, checked_at=datetime.now().astimezone().isoformat(timespec="seconds"))


async def _call_health(
    url: str, token: str | None, *, timeout: float, session_factory: SessionFactory | None
) -> dict[str, Any]:
    async with open_source_session(url, token, call_timeout=timeout, label=_LABEL,
                                   session_factory=session_factory) as session:
        return await session.call_tool(HEALTH_TOOL, {})


__all__ = [
    "CHECK_TIMEOUT_SECONDS",
    "SourceHealth",
    "SourceHealthReport",
    "check_sources",
    "reconcile",
]
