"""제니퍼 소스 묶음 — 소스별 클라이언트·정합기·부분 실패 (plans/87 J8 §0.13 M-1·M-5·M-6 ·
D-287 ①②⑦ · SPEC-apm-gateway §2.1·§3).

게이트웨이 1개가 소스(뷰 서버) N개를 묶는다. 소스마다 토큰·속도 상한·응답 상한·인벤토리가 따로다.
소비자는 소스 목록을 몰라도 되고, 필요하면 `source_ids`로 좁힌다.

- 선택: `source_ids`가 비면 전 소스(선언 순서) · 모르는 id는 `invalid_argument`(설정된 id 목록
  동반).
- 인벤토리 갱신은 소스 간 병렬이다. 한 소스가 실패하면 그 소스만 빠지고 `[한계]`와 봉투
  `sources[]`로 드러낸다. 고른 소스가 **전부** 실패하거나 도메인 0건이면 오류다(빈 결과를 정상으로
  보지 않는다) — 원인 코드가 모두 같으면 그 코드(소스 1개면 v4와 같은 코드·사유), 섞이면
  `source_unavailable`.
- 한 hostname이 여러 소스에서 정합되면 모두 싣는다(행마다 `source_id` · 인스턴스 수 상한 없음 —
  plans/134 W1 N-4 · D-296 ④에서 종전 「호스트당 5」를 걷었다).
- 일부 소스·도메인을 쓸 수 없으면 봉투 `partial: true`의 근거가 된다(`partial_of` · plans/134 W0-B).
- 소스 클라이언트는 메모리 임계를 넘는 응답을 스풀 `tmp/`에 받고, 호출 대기열을 우선순위·에이징으로
  판다(plans/134 W0-B N-14·N-18).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from apm_gateway.adapters.jennifer.api import JenniferApi
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.application.resolver import (
    HIGH,
    MEDIUM,
    InstanceResolver,
    Inventory,
    Resolution,
)
from apm_gateway.application.spool import TMP_DIR
from apm_gateway.config import GatewayConfig
from apm_gateway.domain.errors import (
    INSTANCE_UNRESOLVED,
    INVALID_ARGUMENT,
    NOT_CONFIGURED,
    SOURCE_UNAVAILABLE,
    ApmError,
)

# 봉투 `sources[].status` 어휘(SPEC-apm-gateway §3.1).
STATUS_OK = "ok"
STATUS_NO_MATCH = "no_match"
STATUS_EMPTY = "empty"
STATUS_UNAVAILABLE = "unavailable"


@dataclass
class JenniferSource:
    source_id: str
    api: JenniferApi
    resolver: InstanceResolver


def _status(inv: Inventory) -> str:
    return STATUS_EMPTY if not inv.error_code and not inv.domains else STATUS_UNAVAILABLE


def partial_of(
    usable: list[tuple[JenniferSource, Inventory]], statuses: list[dict[str, Any]]
) -> bool:
    """고른 소스 중 하나라도 빠졌거나(조회 불가·도메인 0건) 쓸 수 있는 소스에 조회 불가 도메인이
    있으면 True — 결과가 전체가 아니다."""
    return any(row["status"] in (STATUS_EMPTY, STATUS_UNAVAILABLE) for row in statuses) or any(
        inv.unavailable for _, inv in usable
    )


class SourceSet:
    """설정된 소스 전부. 한 프로세스에서 공유하고 도구·폴러가 함께 쓴다."""

    def __init__(self, sources: list[JenniferSource]) -> None:
        self._sources = list(sources)
        self._by_id = {s.source_id: s for s in self._sources}

    def __iter__(self) -> Iterator[JenniferSource]:
        return iter(self._sources)

    def __len__(self) -> int:
        return len(self._sources)

    @property
    def ids(self) -> list[str]:
        return [s.source_id for s in self._sources]

    @property
    def configured(self) -> bool:
        return bool(self._sources)

    def get(self, source_id: str) -> JenniferSource:
        return self._by_id[source_id]

    def calls(self) -> dict[str, int]:
        """소스별 누적 HTTP 호출 수(감사 — 도구 1회 전후 차이로 호출한 소스를 적는다)."""
        return {s.source_id: s.api.calls_total for s in self._sources}

    @property
    def calls_total(self) -> int:
        return sum(self.calls().values())

    def where(self, source_id: str, domain_id: int | None = None) -> str:
        """`[한계]` 문구의 위치. 소스가 하나면 v4 문구 그대로(도메인만)."""
        parts = [] if len(self._sources) == 1 else [f"소스 {source_id}"]
        if domain_id is not None:
            parts.append(f"도메인 {domain_id}")
        return " · ".join(parts)

    def require_configured(self) -> None:
        if not self._sources:
            raise ApmError(
                NOT_CONFIGURED,
                "APM API URL 미설정 — 게이트웨이 .env의 JENNIFER_API_URL 또는"
                " JENNIFER_SOURCES를 설정",
            )

    def select(self, source_ids: list[str] | None) -> list[JenniferSource]:
        """`source_ids`로 좁힌다(비면 전 소스 · 순서는 설정 선언 순서)."""
        self.require_configured()
        if not source_ids:
            return list(self._sources)
        wanted = {str(x).strip() for x in source_ids}
        unknown = sorted(wanted - set(self._by_id))
        if unknown:
            raise ApmError(
                INVALID_ARGUMENT, f"모르는 source_ids {unknown} — 설정된 소스: {self.ids}"
            )
        return [s for s in self._sources if s.source_id in wanted]

    async def available(
        self, selected: list[JenniferSource]
    ) -> tuple[list[tuple[JenniferSource, Inventory]], list[dict[str, Any]], list[str]]:
        """고른 소스의 인벤토리를 병렬로 받는다 → (쓸 수 있는 소스, `sources[]`, `[한계]`)."""
        inventories = await asyncio.gather(*(s.resolver.inventory() for s in selected))
        usable: list[tuple[JenniferSource, Inventory]] = []
        statuses: list[dict[str, Any]] = []
        limits: list[str] = []
        failures: list[tuple[str, str, str]] = []
        for src, inv in zip(selected, inventories, strict=True):
            problem = inv.problem()
            if problem is None:
                usable.append((src, inv))
                statuses.append({"source_id": src.source_id, "status": STATUS_OK, "reason": ""})
                tag = "" if len(self._sources) == 1 else f"소스 {src.source_id} "
                if inv.unavailable:
                    ids = ", ".join(str(k) for k in sorted(inv.unavailable))
                    limits.append(
                        f"[한계] APM {tag}도메인 {ids} 조회 불가"
                        " — 그 도메인의 인스턴스는 정합에서 빠졌다"
                    )
                limits += src.resolver.staleness_note(inv, tag)
                continue
            code, reason = problem
            failures.append((src.source_id, code, reason))
            statuses.append(
                {"source_id": src.source_id, "status": _status(inv), "reason": f"{code}: {reason}"}
            )
        if not usable:
            raise _all_failed(failures)
        for sid, code, _ in failures:
            limits.append(f"[한계] APM 소스 {sid} 조회 불가({code}) — 그 소스의 결과는 빠졌다")
        return usable, statuses, limits

    async def resolve(
        self,
        hostname: str,
        instance_id: int | None = None,
        source_ids: list[str] | None = None,
    ) -> Resolution:
        """hostname → 인스턴스 목록(정합된 전부 · 소스 공유). 실패는
        `ApmError`(instance_unresolved·source_unavailable·invalid_argument)."""
        usable, statuses, limits = await self.available(self.select(source_ids))
        matched: list[dict[str, Any]] = []
        matches: dict[str, tuple[str, str]] = {}
        compared = 0
        for src, inv in usable:
            compared += len(inv.instances)
            found, confidence, reason = src.resolver.match(hostname, inv.instances)
            if found:
                matched += found
                matches[src.source_id] = (confidence, reason)
        if not matched:
            partial = any(inv.unavailable for _, inv in usable)
            failed = len(usable) < len(statuses)
            raise ApmError(
                INSTANCE_UNRESOLVED,
                f"hostname {hostname!r}에 대응하는 APM 인스턴스 없음"
                f"(인스턴스 {compared}건 대조"
                + (" · 일부 도메인 조회 불가" if partial else "")
                + (" · 일부 소스 조회 불가" if failed else "")
                + ") — 정합 파일 overrides로 매핑할 수 있다",
            )
        if instance_id is not None:
            matched = [i for i in matched if i["instance_id"] == instance_id]
            if not matched:
                raise ApmError(
                    INSTANCE_UNRESOLVED,
                    f"instance_id {instance_id}는 hostname {hostname!r} 정합 결과에 없음",
                )
        kept = {i["source_id"] for i in matched}
        matches = {sid: m for sid, m in matches.items() if sid in kept}
        confidence = HIGH if all(c == HIGH for c, _ in matches.values()) else MEDIUM
        reason = "+".join(dict.fromkeys(r for _, r in matches.values()))
        if confidence != HIGH:
            limits.append(f"[한계] 정합 신뢰도 {confidence}({reason}) — 인스턴스명 규칙 기반 대응")
        partial = partial_of(usable, statuses)
        for row in statuses:
            if row["status"] == STATUS_OK and row["source_id"] not in kept:
                row["status"] = STATUS_NO_MATCH
        return Resolution(
            hostname, matched, confidence, reason, limits, statuses, matches, partial=partial
        )


def _all_failed(failures: list[tuple[str, str, str]]) -> ApmError:
    if len(failures) == 1:
        _, code, reason = failures[0]
        return ApmError(code, reason)
    codes = {code for _, code, _ in failures}
    detail = "; ".join(f"{sid}: {code}: {reason}" for sid, code, reason in failures)
    code = codes.pop() if len(codes) == 1 else SOURCE_UNAVAILABLE
    return ApmError(code, f"모든 APM 소스 조회 불가 — {detail}")


def build_source_set(
    cfg: GatewayConfig,
    *,
    transport: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> SourceSet:
    """설정의 소스마다 클라이언트·조회 함수·정합기를 만든다(토큰은 그 소스 클라이언트에만)."""
    sources = []
    for api_cfg in cfg.sources:
        client = JenniferClient(
            api_cfg,
            transport=transport,
            spool_dir=cfg.jobs.spool_dir / TMP_DIR,
            aging_seconds=cfg.jobs.priority_aging_seconds,
        )
        api = JenniferApi(client)
        resolver = InstanceResolver(
            api,
            cfg.policies.instance_map,
            source_id=api_cfg.source_id,
            domain_filter=api_cfg.domain_ids,
            cache_seconds=cfg.runtime.instance_cache_seconds,
            clock=clock,
        )
        sources.append(JenniferSource(api_cfg.source_id, api, resolver))
    return SourceSet(sources)
