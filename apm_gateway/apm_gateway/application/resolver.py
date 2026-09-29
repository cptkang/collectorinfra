"""인스턴스 ↔ hostname 정합 (plans/87 §5.3 [v3.2] · R-1 · SPEC-apm-gateway §3).

순서: ⓪ 정합 파일 `overrides`(수동 · high) → ① APM `Instance.hostName` 직접 대조(대소문자·FQDN
정규화 · high) → ③ 인스턴스명 규칙(`exact` high · `prefix`·`regex` medium). ② 폴스타 `was_object`
브릿지는 운영 실재(U-10)가 확인되지 않아 두지 않았다 — 규칙 목록에 넣으면 경고 후 건너뛴다.

인벤토리(도메인 → 인스턴스)는 TTL 캐시한다(기본 600초). 일부 도메인만 실패하면 부분 결과와 사유를
함께 싣고, 전부 실패하거나 도메인이 0건이면 `source_unavailable`이다 — 빈 결과를 "정상 · 0건"으로
단정하지 않는다(§0.10 #18).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from apm_gateway.adapters.jennifer.api import JenniferApi
from apm_gateway.domain.errors import INSTANCE_UNRESOLVED, SOURCE_UNAVAILABLE, ApmError

logger = logging.getLogger(__name__)

MAX_INSTANCES_PER_HOST = 5
HIGH = "high"
MEDIUM = "medium"
NONE = "none"


@dataclass
class Inventory:
    domains: list[dict[str, Any]] = field(default_factory=list)
    instances: list[dict[str, Any]] = field(default_factory=list)
    unavailable: dict[int, str] = field(default_factory=dict)  # domain_id → 사유
    fetched_at: float = 0.0


@dataclass
class Resolution:
    hostname: str
    instances: list[dict[str, Any]]
    confidence: str
    reason: str
    limits: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": bool(self.instances),
            "confidence": self.confidence,
            "reason": self.reason,
            "instances": [i["instance_id"] for i in self.instances],
        }

    @property
    def domain_ids(self) -> set[int]:
        return {i["domain_id"] for i in self.instances if i.get("domain_id") is not None}


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
    """정합기. 한 프로세스에서 공유하고 도구·폴러가 함께 쓴다."""

    def __init__(
        self,
        api: JenniferApi,
        instance_map: dict[str, Any],
        *,
        domain_filter: tuple[int, ...] = (),
        cache_seconds: int = 600,
    ) -> None:
        self._api = api
        self._overrides = list(instance_map.get("overrides") or [])
        self._rules = []
        for rule in instance_map.get("match_rules") or []:
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
        self._inventory: Inventory | None = None

    @property
    def override_count(self) -> int:
        return len(self._overrides)

    async def inventory(self, *, refresh: bool = False) -> Inventory:
        now = time.monotonic()
        inv = self._inventory
        if inv is not None and not refresh and now - inv.fetched_at < self._cache_seconds:
            return inv
        domains = await self._api.domains()
        if self._domain_filter:
            domains = [d for d in domains if d["domain_id"] in self._domain_filter]
        inv = Inventory(domains=domains, fetched_at=now)
        for d in domains:
            try:
                inv.instances.extend(await self._api.instances(d["domain_id"], d["domain_name"]))
            except ApmError as e:
                inv.unavailable[d["domain_id"]] = f"{e.code}: {e.reason}"
        self._inventory = inv
        return inv

    def unavailable_note(self, inv: Inventory) -> list[str]:
        if not inv.unavailable:
            return []
        ids = ", ".join(str(k) for k in sorted(inv.unavailable))
        return [f"[한계] APM 도메인 {ids} 조회 불가 — 그 도메인의 인스턴스는 정합에서 빠졌다"]

    def ensure_available(self, inv: Inventory) -> None:
        if not inv.domains:
            raise ApmError(
                SOURCE_UNAVAILABLE,
                "APM 도메인 0건 — 에이전트 미접속·라이선스·도메인 필터를 확인"
                "(빈 결과를 정상으로 보지 않는다)",
            )
        if not inv.instances and inv.unavailable:
            detail = "; ".join(f"{k}={v}" for k, v in sorted(inv.unavailable.items()))
            raise ApmError(SOURCE_UNAVAILABLE, f"모든 APM 도메인 조회 불가: {detail}")

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

    async def resolve(self, hostname: str, instance_id: int | None = None) -> Resolution:
        """hostname → 인스턴스 목록(최대 5). 실패는
        `ApmError`(instance_unresolved·source_unavailable)."""
        inv = await self.inventory()
        self.ensure_available(inv)
        limits = self.unavailable_note(inv)
        matched: list[dict[str, Any]] = []
        confidence, reason = NONE, "no_match"
        found = self._match_override(hostname, inv.instances)
        if found:
            matched, confidence, reason = found, HIGH, "override"
        else:
            for rule in self._rules:
                found = self._match_rule(rule, hostname, inv.instances)
                if found:
                    matched = found
                    confidence = HIGH if rule["kind"] in ("host_name", "exact") else MEDIUM
                    reason = rule["kind"]
                    break
        if not matched:
            raise ApmError(
                INSTANCE_UNRESOLVED,
                f"hostname {hostname!r}에 대응하는 APM 인스턴스 없음"
                f"(인스턴스 {len(inv.instances)}건 대조"
                + (" · 일부 도메인 조회 불가" if inv.unavailable else "")
                + ") — 정합 파일 overrides로 매핑할 수 있다",
            )
        if instance_id is not None:
            matched = [i for i in matched if i["instance_id"] == instance_id]
            if not matched:
                raise ApmError(
                    INSTANCE_UNRESOLVED,
                    f"instance_id {instance_id}는 hostname {hostname!r} 정합 결과에 없음",
                )
        if len(matched) > MAX_INSTANCES_PER_HOST:
            limits.append(
                f"[한계] 인스턴스 {len(matched)}개 중 {MAX_INSTANCES_PER_HOST}개만 조회(상한)"
                " — instance_id로 좁힐 수 있다"
            )
            matched = matched[:MAX_INSTANCES_PER_HOST]
        if confidence != HIGH:
            limits.append(f"[한계] 정합 신뢰도 {confidence}({reason}) — 인스턴스명 규칙 기반 대응")
        return Resolution(hostname, matched, confidence, reason, limits)

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
