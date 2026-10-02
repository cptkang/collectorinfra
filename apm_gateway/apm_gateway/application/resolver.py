"""인스턴스 ↔ hostname 정합 (plans/87 §5.3 [v3.2] · R-1 · SPEC-apm-gateway §3).

순서: ⓪ 정합 파일 `overrides`(수동 · high) → ① APM `Instance.hostName` 직접 대조(대소문자·FQDN
정규화 · high) → ③ 인스턴스명 규칙(`exact` high · `prefix`·`regex` medium). ② 폴스타 `was_object`
브릿지는 운영 실재(U-10)가 확인되지 않아 두지 않았다 — 규칙 목록에 넣으면 경고 후 건너뛴다.

정합기는 **제니퍼 소스 하나**를 맡는다(plans/87 J8 · D-287 — 소스 묶음은 `application.sources`).
정합 파일의 `overrides[].source_id`가 있으면 그 소스에만 쓰고, `per_source.<id>.match_rules`가
있으면 그 소스는 전역 `match_rules` 대신 그것을 쓴다(M-8). 인스턴스 레코드에는 `source_id`를 붙인다.

인벤토리(도메인 → 인스턴스)는 TTL 캐시한다(기본 600초). 도메인 목록 조회 실패는 예외를 올리지 않고
인벤토리에 사유로 담는다(한 소스 장애가 다른 소스 조회를 막지 않게 — S-3). 실패·도메인 0건·전
도메인 조회 불가 인벤토리는 짧게(30초) 캐시한다 — 라이선스 적용·에이전트 접속 뒤 최대 10분 동안
"도메인 0건"을 돌려주지 않게 한다(F-3). 빈 결과를 "정상 · 0건"으로 단정하지 않는다(§0.10 #18).

적재는 도메인마다 순차 호출이라 도메인이 수백 개면 수십 초~수 분이 걸린다(운영 실측 2026-09-30 —
도메인 약 350개 · 호출 상한 5회/초 → 적재 1회 ≈ 70초, 겹친 적재 2회 ≈ 140~160초). 그래서
- **적재는 한 번에 하나**다 — 진행 중이면 새 요청은 그 적재를 함께 기다린다(중복 적재가 호출 상한을
  나눠 쓰며 서로를 두 배로 늦췄다).
- 기다리던 요청이 취소돼도 적재는 끝까지 간다 — 결과가 캐시에 남아야 다음 요청이 빈 캐시를 만나지 않는다.
- **정상 명단이 만료되면 기존 명단으로 바로 답하고** 뒤에서 한 번만 갱신한다. 갱신 결과를
  쓸 수 없으면(실패·도메인 0건) 기존 명단을 두고 `REFRESH_RETRY_SECONDS` 동안 다시 시도하지
  않는다. 기준 시각이 지난 명단으로 답할 때는 `staleness_note`가 그 사실을 한계로 싣는다.
  실패·빈 인벤토리는 쓸 명단이 없으므로 30초가 지나면 요청이 적재를 기다린다(F-3).
- 게이트웨이 기동 직후 `warm_up`으로 소스마다 미리 적재한다(`__main__`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apm_gateway.adapters.jennifer.api import JenniferApi
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError
from apm_gateway.domain.sources import DEFAULT_SOURCE_ID

logger = logging.getLogger(__name__)

MAX_INSTANCES_PER_HOST = 5
# 백그라운드 갱신이 실패한 뒤 다시 시도하기까지(초) — 제니퍼가 멈춘 동안 요청마다 적재를 다시 걸지 않는다
REFRESH_RETRY_SECONDS = 60
HIGH = "high"
MEDIUM = "medium"
NONE = "none"
# 실패·빈 인벤토리 재조회 간격(F-3) — `gateway_health` 캐시와 같은 값.
SHORT_CACHE_SECONDS = 30.0
EMPTY_REASON = (
    "APM 도메인 0건 — 에이전트 미접속·라이선스·도메인 필터를 확인(빈 결과를 정상으로 보지 않는다)"
)


@dataclass
class Inventory:
    domains: list[dict[str, Any]] = field(default_factory=list)
    instances: list[dict[str, Any]] = field(default_factory=list)
    unavailable: dict[int, str] = field(default_factory=dict)  # domain_id → 사유
    fetched_at: float = 0.0
    error_code: str = ""  # 도메인 목록 조회 실패(소스 단위)
    error: str = ""

    def problem(self) -> tuple[str, str] | None:
        """이 소스를 쓸 수 없으면 (오류 코드, 사유). 쓸 수 있으면 None."""
        if self.error_code:
            return self.error_code, self.error
        if not self.domains:
            return SOURCE_UNAVAILABLE, EMPTY_REASON
        if not self.instances and self.unavailable:
            detail = "; ".join(f"{k}={v}" for k, v in sorted(self.unavailable.items()))
            return SOURCE_UNAVAILABLE, f"모든 APM 도메인 조회 불가: {detail}"
        return None


@dataclass
class Resolution:
    hostname: str
    instances: list[dict[str, Any]]
    confidence: str
    reason: str
    limits: list[str] = field(default_factory=list)
    # 소스별 결과 `[{source_id, status, reason}]`(봉투 `sources`) · 정합된 소스 → (신뢰도, 근거)
    sources: list[dict[str, Any]] = field(default_factory=list)
    matches: dict[str, tuple[str, str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": bool(self.instances),
            "confidence": self.confidence,
            "reason": self.reason,
            "instances": [i["instance_id"] for i in self.instances],
            "instance_refs": [
                {
                    "source_id": i["source_id"],
                    "domain_id": i["domain_id"],
                    "instance_id": i["instance_id"],
                }
                for i in self.instances
            ],
        }

    @property
    def source_domains(self) -> set[tuple[str, int]]:
        return {
            (i["source_id"], i["domain_id"])
            for i in self.instances
            if i.get("domain_id") is not None
        }

    def match_of(self, inst: dict[str, Any]) -> tuple[str, str]:
        """인스턴스가 정합된 (신뢰도, 근거) — 소스마다 규칙이 달라 행마다 다를 수 있다."""
        return self.matches.get(inst["source_id"], (self.confidence, self.reason))


def normalize_host(value: str) -> str:
    return str(value or "").strip().rstrip(".").lower()


def short_host(value: str) -> str:
    return normalize_host(value).split(".", 1)[0]


def _host_equal(a: str, b: str) -> bool:
    na, nb = normalize_host(a), normalize_host(b)
    if not na or not nb:
        return False
    return na == nb or short_host(na) == short_host(nb)


class InstanceResolver:
    """소스 하나의 정합기. 한 프로세스에서 공유하고 도구·폴러가 함께 쓴다."""

    def __init__(
        self,
        api: JenniferApi,
        instance_map: dict[str, Any],
        *,
        source_id: str = DEFAULT_SOURCE_ID,
        domain_filter: tuple[int, ...] = (),
        cache_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self.source_id = source_id
        self._overrides = [
            ov
            for ov in instance_map.get("overrides") or []
            if not ov.get("source_id") or str(ov["source_id"]) == source_id
        ]
        per_source = (instance_map.get("per_source") or {}).get(source_id) or {}
        rules = per_source.get("match_rules")
        if rules is None:
            rules = instance_map.get("match_rules")
        self._rules = []
        for rule in rules or []:
            kind = str(rule.get("kind") or "")
            if kind == "polestar_was_object":
                logger.warning("정합 규칙 polestar_was_object는 미구현(U-10 미확인) — 건너뜀")
                continue
            if kind not in ("host_name", "exact", "prefix", "regex"):
                logger.warning("미지 정합 규칙 무시: %s", kind)
                continue
            if kind == "regex":
                rule = {**rule, "_rx": re.compile(str(rule.get("pattern") or ""))}
            self._rules.append(rule)
        self._domain_filter = tuple(domain_filter)
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._inventory: Inventory | None = None
        self._loading: asyncio.Task[Inventory] | None = None
        self._retry_after = 0.0

    @property
    def override_count(self) -> int:
        return len(self._overrides)

    def _ttl(self, inv: Inventory) -> float:
        return SHORT_CACHE_SECONDS if inv.problem() else float(self._cache_seconds)

    async def inventory(self, *, refresh: bool = False) -> Inventory:
        """소스 인벤토리 — 정상 명단은 즉시(만료면 뒤에서 갱신), 명단이 없거나 실패·빈 인벤토리가
        30초를 넘었으면 진행 중 적재를 함께 기다린다. 도메인 목록 조회 실패는 예외가 아니라
        `error_code`·`error`로 담는다."""
        inv = self._inventory
        if inv is not None and not refresh:
            if self._clock() - inv.fetched_at < self._ttl(inv):
                return inv
            if inv.problem() is None:
                self._refresh_in_background()
                return inv
        return await asyncio.shield(self._load_once())

    async def warm_up(self) -> None:
        """기동 직후 선적재 — 실패해도 기동은 막지 않는다(첫 요청 때 다시 적재한다)."""
        try:
            problem = (await self.inventory()).problem()
        except Exception as e:  # noqa: BLE001 — 선적재 실패는 로그로만 알린다
            problem = ("error", str(e))
        if problem is not None:
            logger.warning(
                "인스턴스 목록 선적재 실패(소스 %s) — 첫 요청 때 다시 적재한다: %s: %s",
                self.source_id, *problem,
            )

    def _load_once(self) -> asyncio.Task[Inventory]:
        if self._loading is None or self._loading.done():
            self._loading = asyncio.create_task(self._load())
        return self._loading

    def _refresh_in_background(self) -> None:
        if self._loading is not None and not self._loading.done():
            return
        if self._clock() < self._retry_after:
            return
        self._load_once().add_done_callback(self._after_background_refresh)

    def _after_background_refresh(self, task: asyncio.Task[Inventory]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._retry_after = self._clock() + REFRESH_RETRY_SECONDS
            logger.warning(
                "인스턴스 목록 갱신 실패 — 기존 명단을 유지하고 %d초 뒤 다시 시도한다: %s",
                REFRESH_RETRY_SECONDS, error,
            )

    async def _load(self) -> Inventory:
        started = self._clock()
        try:
            domains = await self._api.domains()
        except ApmError as e:
            return self._store(Inventory(fetched_at=started, error_code=e.code, error=e.reason))
        if self._domain_filter:
            domains = [d for d in domains if d["domain_id"] in self._domain_filter]
        inv = Inventory(domains=domains, fetched_at=started)
        for d in domains:
            try:
                found = await self._api.instances(d["domain_id"], d["domain_name"])
            except ApmError as e:
                inv.unavailable[d["domain_id"]] = f"{e.code}: {e.reason}"
                continue
            inv.instances.extend({**inst, "source_id": self.source_id} for inst in found)
        logger.info(
            "인스턴스 목록 적재: 도메인 %d · 인스턴스 %d · 조회 불가 도메인 %d · %.1f초 · 소스 %s",
            len(inv.domains), len(inv.instances), len(inv.unavailable), self._clock() - started,
            self.source_id,
        )
        return self._store(inv)

    def _store(self, inv: Inventory) -> Inventory:
        """새 인벤토리를 캐시한다 — 갱신 결과를 쓸 수 없으면(실패·도메인 0건) 기존 정상 명단을
        유지한다."""
        prev = self._inventory
        problem = inv.problem()
        if problem is not None and prev is not None and prev.problem() is None:
            self._retry_after = self._clock() + REFRESH_RETRY_SECONDS
            logger.warning(
                "인스턴스 목록 갱신 실패(소스 %s) — 기존 명단을 유지하고 %d초 뒤 다시 시도한다:"
                " %s: %s",
                self.source_id, REFRESH_RETRY_SECONDS, *problem,
            )
            return prev
        self._inventory = inv
        return inv

    def staleness_note(self, inv: Inventory, tag: str = "") -> list[str]:
        """기준 시각이 지난 명단으로 답할 때의 한계(`tag` — 소스가 여럿이면 「소스 <id> 」)."""
        age = self._clock() - inv.fetched_at
        if age < self._cache_seconds:
            return []
        return [
            f"[한계] {tag}인스턴스 목록이 {int(age // 60)}분 전 기준이다"
            " — 갱신 중이거나 갱신에 실패했다"
        ]

    def _match_override(
        self, hostname: str, instances: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        out = []
        for ov in self._overrides:
            if not _host_equal(str(ov.get("hostname") or ""), hostname):
                continue
            for inst in instances:
                if ov.get("instance_name") and inst["instance_name"] != ov["instance_name"]:
                    continue
                if ov.get("instance_id") is not None and inst["instance_id"] != int(
                    ov["instance_id"]
                ):
                    continue
                if ov.get("domain_id") is not None and inst["domain_id"] != int(ov["domain_id"]):
                    continue
                out.append({**inst, "port": ov.get("port"), "kind": ov.get("kind")})
        return out

    def _match_rule(
        self, rule: dict[str, Any], hostname: str, instances: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        kind = rule["kind"]
        host = normalize_host(hostname)
        short = short_host(hostname)
        if kind == "host_name":
            return [i for i in instances if _host_equal(i["host_name"], hostname)]
        if kind == "exact":
            return [i for i in instances if i["instance_name"].strip().lower() in (host, short)]
        if kind == "prefix":
            return [
                i
                for i in instances
                if any(
                    i["instance_name"].lower().startswith(h + sep)
                    for h in (host, short)
                    for sep in ("_", "-")
                )
            ]
        rx: re.Pattern[str] = rule["_rx"]
        out = []
        for inst in instances:
            m = rx.match(inst["instance_name"])
            if m and "hostname" in m.groupdict() and _host_equal(m.group("hostname"), hostname):
                out.append(inst)
        return out

    def match(
        self, hostname: str, instances: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str, str]:
        """hostname → (이 소스 인스턴스 목록, 신뢰도, 근거). 없으면 ([], none, no_match)."""
        found = self._match_override(hostname, instances)
        if found:
            return found, HIGH, "override"
        for rule in self._rules:
            found = self._match_rule(rule, hostname, instances)
            if found:
                confidence = HIGH if rule["kind"] in ("host_name", "exact") else MEDIUM
                return found, confidence, rule["kind"]
        return [], NONE, "no_match"

    def reverse(
        self,
        inv: Inventory,
        domain_id: int | None,
        instance_id: int | None,
        instance_name: str = "",
    ) -> tuple[str, str, str, dict[str, Any] | None]:
        """이벤트의 인스턴스 → (hostname, confidence, reason, instance). 미해소면 hostname 빈 값."""
        inst = next(
            (
                i
                for i in inv.instances
                if i["instance_id"] == instance_id and i["domain_id"] == domain_id
            ),
            None,
        )
        name = inst["instance_name"] if inst else str(instance_name or "")
        for ov in self._overrides:
            if ov.get("instance_name") and ov["instance_name"] != name:
                continue
            if ov.get("instance_id") is not None and int(ov["instance_id"]) != instance_id:
                continue
            if ov.get("domain_id") is not None and int(ov["domain_id"]) != domain_id:
                continue
            if not (ov.get("instance_name") or ov.get("instance_id") is not None):
                continue
            return str(ov.get("hostname") or ""), HIGH, "override", inst
        if inst and inst["host_name"].strip():
            return inst["host_name"].strip().rstrip("."), HIGH, "host_name", inst
        for rule in self._rules:
            if rule["kind"] != "regex":
                continue
            m = rule["_rx"].match(name)
            if m and m.groupdict().get("hostname"):
                return m.group("hostname"), MEDIUM, "regex", inst
        return "", NONE, "unresolved", inst
