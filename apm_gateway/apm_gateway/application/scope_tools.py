"""서비스(도메인)·업무 조회 — `apm_service_status`·`apm_business` · 도메인·업무 지표 시계열
(plans/134 W3 N-9 · W4 N-12 · SPEC-apm-question-coverage §3 · D-290 ④⑥ · D-296 ④ · D-287).

- 서비스 = APM 도메인. 서비스 이름은 캐시된 인벤토리의 도메인 이름에서, 업무 이름은 업무
  정의(`ApmTools._business_defs_of` 캐시)의 업무 이름에서 plans/130 단계 검색(`search_names` —
  exact → normalized → prefix → contains · 가장 앞 단계만)으로 찾는다. 못 찾은 이름은 사용자용
  고지(`_unresolved`)와 유사 이름 후보(`suggestions` — 이름마다 ≤3 · 자동 채택 안 함)를 남긴다.
- **명시 대상 미해결을 다른 대상으로 대신하지 않는다**(D-290 ⑥) — 이름·도메인 ID를 줬는데
  하나도 못 찾으면 데이터 API를 부르지 않고 행 0을 돌려준다(전체로 넓히지 않는다). 한 이름에
  여럿이 맞으면 전부 조회하고 맞은 이름 목록을 `[한계]`에 싣는다(되묻지 않는다 · G-13).
- 대상이 없으면 고른 소스의 전 도메인 — 서비스 현재값은 소스당 1호출, 업무·시계열은 도메인마다.
  자체 상한 없음(D-296 ④) — 큰 결과는 작업·결과 파일이 맡는다.
- 식별은 (`source_id`, `domain_id`[, `business_id`]) — 소스마다 id를 따로 매겨 겹칠 수 있다(D-287).
- 단위 조회 실패는 `[한계]` + `partial`, 전부 실패는 오류(0건으로 세지 않는다).
- 시계열의 지표 검증은 인스턴스 시계열과 같은 코드(`ApmTools._metric_plan`)다. 지표를 비우면 군별
  기본 지표(응답시간·호출 수·오류 수 — 카탈로그에 있는 것)로 조회하고 `[한계]`에 적는다. 모르는
  지표는 빼고(일부) 또는 기본 지표로(전부) 조회하고 후보(≤3)를 미해결 고지에 싣는다 — 선택 조건
  하나가 보기 전체를 실패시키지 않는다(plans/134 V34-1 · W2 런타임 추세와 같은 계열).
- 벤더 경로·필드명은 어댑터에만 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apm_gateway.adapters.jennifer.api import JenniferApi
from apm_gateway.application.masking import mask_text, mask_url
from apm_gateway.application.sources import (
    STATUS_NO_MATCH,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    JenniferSource,
)
from apm_gateway.application.tools import (
    SERIES_DEFAULT_MINUTES,
    SERIES_INTERVAL_NOTE,
    VISIT_HIT_NOTE,
    ApmTools,
    _domain_order,
    _fresh,
    _Limits,
    _nonneg_int,
    normalize_name,
    search_names,
    search_query,
    suggest_names,
)
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import CONTRACT_VIOLATION, INVALID_ARGUMENT, ApmError

SERVICE_TOOL = "apm_service_status"
BUSINESS_TOOL = "apm_business"
METRICS_TOOL = "apm_metrics"
BUSINESS_MODES = ("current", "list")
# 서비스 행의 현재값 칸(중립 이름 — 어댑터 `RealtimeDomainData` 18필드 중 식별 둘 밖)
SERVICE_FIELDS = (
    "tps",
    "response_time_avg_ms",
    "active_services",
    "active_users",
    "concurrent_users",
    "reject_rate",
    "visit_day",
    "visit_hour",
    "hit_day",
    "hit_hour",
    "active_range_count_0",
    "active_range_count_1",
    "active_range_count_2",
    "active_range_count_3",
    "data_server_ip",
    "data_server_port",
)
# 업무 현재값 칸(어댑터 `RealtimeBusinessData` 11필드 중 식별 셋 밖)
BUSINESS_FIELDS = (
    "tps",
    "response_time_avg_ms",
    "active_services",
    "concurrent_users",
    "active_range_count_0",
    "active_range_count_1",
    "active_range_count_2",
    "active_range_count_3",
)
# 업무 목록 행의 전체 파일 전용 칸(내부 OID — COV-BUSINESS)
BUSINESS_FILE_ONLY = ("business_oid",)


def name_list(value: Any, name: str) -> list[str] | None:
    """이름 인자(문자열 하나 또는 문자열 목록) → 검색어 목록 — 항목마다 `search_query` 형식 검사 ·
    대소문자 무시 중복 제거 · 순서 유지. None이면 None, 빈 목록·형식 밖은 `invalid_argument`."""
    if value is None:
        return None
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        raise ApmError(INVALID_ARGUMENT, f"{name}는 문자열 또는 비지 않은 문자열 목록이어야 한다")
    out: list[str] = []
    for item in items:
        text = search_query(item, name)
        if all(text.casefold() != seen.casefold() for seen in out):
            out.append(text)
    return out


def scope_target(
    service: Any = None,
    business: Any = None,
    domain_id: Any = None,
    business_id: Any = None,
) -> str:
    """감사 대상 문자열(`service:a,b business:x domain:1000` · 없으면 `*`) — 마스킹은 감사 쪽이
    한다."""
    parts = []
    for label, value in (("service", service), ("business", business)):
        if value:
            names = [value] if isinstance(value, str) else value
            text = ",".join(map(str, names)) if isinstance(names, list) else str(names)
            parts.append(f"{label}:{text}")
    if domain_id is not None:
        parts.append(f"domain:{domain_id}")
    if business_id is not None:
        parts.append(f"business_id:{business_id}")
    return " ".join(parts) or "*"


@dataclass
class ServiceUnit:
    """조회 단위 (소스, 도메인) — 서비스 이름으로 찾았으면 그 이름(원문)·검색 단계."""

    source: JenniferSource
    domain_id: int
    domain_name: str
    text: str = ""
    tier: str = ""


@dataclass
class ServiceScope:
    """서비스(도메인) 범위(`ScopeTools.service_scope`) — 조회 단위·소스 상태·`[한계]`·미해결 이름.

    `narrowed`면 `service`·`domain_id`로 좁혔다 — 그런데 `units`가 비면 `blocked`(데이터 API를
    부르지 않는다). 아니면 `units`는 고른 소스의 전 도메인이다."""

    units: list[ServiceUnit]
    sources: list[JenniferSource]
    statuses: list[dict[str, Any]]
    limits: _Limits
    narrowed: bool
    missing: list[str] = field(default_factory=list)  # 못 찾은 이름(마스킹본)
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.narrowed and not self.units


@dataclass
class BusinessUnit:
    """조회 단위 (소스, 도메인, 업무) — 업무 이름으로 찾았으면 그 이름(원문)·검색 단계."""

    unit: ServiceUnit
    business_id: int
    business_name: str
    definition: dict[str, Any] | None
    text: str = ""
    tier: str = ""


class ScopeTools:
    """서비스·업무 도구 코어 — 코어(`ApmTools`)의 봉투·소스 묶음·업무 정의 캐시·지표 검증을 쓴다
    (상태 없음 · 호출마다 만들어도 된다)."""

    def __init__(self, core: ApmTools) -> None:
        self.core = core
        self._filters = {s.source_id: frozenset(s.domain_ids) for s in core.cfg.sources}

    # ── 범위 ──────────────────────────────────────────────

    def _at(self, source_id: str, domain_id: int | None = None) -> str:
        where = self.core.sources.where(source_id, domain_id)
        return f"({where})" if where else ""

    def _in_scope(self, source_id: str, domain_id: int) -> bool:
        """소스 설정의 도메인 필터(`domain_ids`) 안인가 — 필터가 없으면 전부."""
        allowed = self._filters.get(source_id)
        return not allowed or domain_id in allowed

    def _domain_label(self, unit: ServiceUnit) -> str:
        return f"{unit.domain_name}{self._at(unit.source.source_id, unit.domain_id)}"

    async def service_scope(
        self, service: Any, domain_id: Any, source_ids: list[str] | None
    ) -> ServiceScope:
        """서비스(도메인) 범위 — `service`(이름 하나 또는 목록)·`domain_id`가 있으면 그것으로 좁힌
        (소스, 도메인)(둘 다 주면 AND), 없으면 고른 소스의 전 도메인. 이름은 캐시된 인벤토리의
        도메인 이름에서 찾는다(새 HTTP 없음 — 인벤토리 적재 제외)."""
        names = name_list(service, "service")
        domain = None if domain_id is None else _nonneg_int(domain_id, "domain_id")
        core = self.core
        usable, statuses, notes = await core.sources.available(core.sources.select(source_ids))
        limits = _Limits(notes)
        if any(row["status"] != STATUS_OK for row in statuses):
            limits.partial = True  # 고른 소스 중 빠진 소스가 있다(사유는 notes에)
        scope = ServiceScope(
            [],
            [src for src, _ in usable],
            statuses,
            limits,
            names is not None or domain is not None,
        )
        domains = [
            (rank, ServiceUnit(src, d["domain_id"], str(d.get("domain_name") or "")))
            for rank, (src, inv) in enumerate(usable)
            for d in inv.domains
            if isinstance(d.get("domain_id"), int) and (domain is None or d["domain_id"] == domain)
        ]
        if domain is not None and not domains:
            known = sorted({d["domain_id"] for _, inv in usable for d in inv.domains})
            listed = ", ".join(map(str, known)) or "없음"
            limits.append(
                f"[한계] 도메인 {domain}은 조회한 APM 소스의 도메인 목록에 없다({listed})"
            )
            limits.unresolve(
                f"도메인 ID {domain}는 제니퍼 도메인 목록에 없습니다(있는 도메인: {listed})"
            )
            return scope
        if names is None:
            scope.units = [u for _, u in domains]
            return scope
        chosen: dict[tuple[str, int], ServiceUnit] = {}
        for text in names:
            tier, hits, _ = search_names(text, ((h, h[1].domain_name, "") for h in domains))
            shown = mask_text(text, limit=None)
            if tier is None:
                scope.missing.append(shown)
                limits.unresolve(f"서비스 '{shown}'에 해당하는 제니퍼 도메인을 찾지 못했습니다")
                scope.suggestions += [
                    {"query": shown, **s}
                    for s in suggest_names(
                        normalize_name(text),
                        (
                            (
                                (rank, _domain_order(u.domain_id)),
                                u.domain_name,
                                {
                                    "domain_name": u.domain_name,
                                    "source_id": u.source.source_id,
                                    "domain_id": u.domain_id,
                                },
                            )
                            for rank, u in domains
                        ),
                    )
                ]
                continue
            ordered = [
                u
                for _, u in sorted(
                    hits, key=lambda h: (h[0], _domain_order(h[1].domain_id), h[1].domain_name)
                )
            ]
            if len(ordered) > 1:
                limits.append(
                    f"[한계] 서비스 '{shown}' — 이름이 맞은 도메인 {len(ordered)}곳을 모두"
                    f" 조회했다({tier} 단계): " + ", ".join(map(self._domain_label, ordered))
                )
            for u in ordered:
                chosen.setdefault(
                    (u.source.source_id, u.domain_id),
                    ServiceUnit(u.source, u.domain_id, u.domain_name, text, tier),
                )
        scope.units = list(chosen.values())
        hit_sources = {u.source.source_id for u in scope.units}
        for row in statuses:
            if row["status"] == STATUS_OK and row["source_id"] not in hit_sources:
                row["status"] = STATUS_NO_MATCH
        return scope

    def envelope(
        self, tool: str, rows: list[dict[str, Any]], scope: ServiceScope, /, **extra: Any
    ) -> dict[str, Any]:
        """서비스 범위 봉투(이 모듈 · 전 대상 조회 공용) — 못 찾은 이름이 있으면 `suggestions`를
        싣는다(`extra`의 `scope`는 시계열 군 이름이라 범위 인자는 위치 전용이다)."""
        if scope.missing:
            extra["suggestions"] = scope.suggestions
        return self.core.ok(
            tool,
            rows,
            limits=scope.limits,
            sources=scope.statuses,
            partial=scope.limits.partial,
            **extra,
        )

    @staticmethod
    def _mark_failed_sources(
        scope: ServiceScope, failed: dict[str, ApmError], answered: set[str]
    ) -> None:
        """한 호출도 답하지 않은 소스는 봉투 `sources[]`에서 unavailable(일부 실패는 `[한계]`)."""
        for row in scope.statuses:
            sid = row["source_id"]
            if sid in failed and sid not in answered:
                e = failed[sid]
                row["status"] = STATUS_UNAVAILABLE
                row["reason"] = f"{e.code}: {mask_text(e.reason, limit=240)}"

    # ── apm_service_status ──────────────────────────────────

    async def apm_service_status(
        self,
        service: str | list[str] | None = None,
        domain_id: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """서비스(APM 도메인) 현재값 — (소스, 도메인)당 1행. 대상이 없으면 소스당 1호출로 전
        도메인, 좁혔으면 도메인마다 1호출."""
        tool = SERVICE_TOOL
        core = self.core
        scope = await self.service_scope(service, domain_id, source_ids)
        limits = scope.limits
        if scope.blocked:
            return self.envelope(tool, [], scope)
        calls: list[tuple[JenniferSource, int | None]] = (
            [(u.source, u.domain_id) for u in scope.units]
            if scope.narrowed
            else [(src, None) for src in scope.sources]
        )
        expect_calls(len(calls))
        got: dict[tuple[str, int], dict[str, Any]] = {}
        failures: list[tuple[str, ApmError]] = []
        failed: dict[str, ApmError] = {}
        answered: set[str] = set()
        for src, did in calls:
            sid = src.source_id
            try:
                records = await src.api.realtime_domains(did)
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failures.append((core.sources.where(sid, did), e))
                failed.setdefault(sid, e)
                limits.fail(
                    f"[한계] 실시간 도메인 조회 실패{self._at(sid, did)}: {e.code}"
                    + core._reason_tail(e)
                )
                continue
            answered.add(sid)
            for rec in records:
                rid = rec.get("domain_id")
                if rid is None or (did is not None and rid != did) or not self._in_scope(sid, rid):
                    continue
                got.setdefault((sid, rid), rec)
        if failures and len(failures) == len(calls):
            raise core._all_failed(failures)
        self._mark_failed_sources(scope, failed, answered)
        rows: list[dict[str, Any]] = []
        missing: list[str] = []
        for u in scope.units:
            key = (u.source.source_id, u.domain_id)
            if key not in got:
                if u.source.source_id in answered:
                    missing.append(self._domain_label(u))
                continue
            rows.append(self._service_row(*key, u.domain_name, got.pop(key), u))
        for (sid, did), extra in got.items():  # 인벤토리에 아직 없는 도메인(전체 조회)
            rows.append(self._service_row(sid, did, "", extra, None))
        if missing:
            limits.append(f"[한계] 실시간 데이터 없는 도메인: {', '.join(missing)}")
        if rows:
            limits.append(VISIT_HIT_NOTE)
        return self.envelope(tool, rows, scope)

    @staticmethod
    def _service_row(
        source_id: str,
        domain_id: int,
        domain_name: str,
        rec: dict[str, Any],
        unit: ServiceUnit | None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "source_id": source_id,
            "domain_id": domain_id,
            "domain_name": rec.get("domain_name") or domain_name,
        }
        for name in SERVICE_FIELDS:
            row[name] = rec.get(name)
        if unit is not None and unit.text:
            row["service_text"] = mask_text(unit.text, limit=None)
            row["match_tier"] = unit.tier
        return row

    # ── 업무 해석 ──────────────────────────────────────────

    async def _business_defs(
        self, scope: ServiceScope
    ) -> list[tuple[ServiceUnit, list[dict[str, Any]]]]:
        """범위의 도메인마다 업무 정의(`_business_defs_of` TTL 캐시 — 캐시 밖만 호출 신고). 읽은
        도메인만 돌려준다 · 일부 실패는 `[한계]`(부분) · 전부 실패는 오류."""
        core = self.core
        now = core.clock()
        expect_calls(
            sum(
                1
                for u in scope.units
                if not _fresh(core._business_defs.get((u.source.source_id, u.domain_id)), now)
            )
        )
        out: list[tuple[ServiceUnit, list[dict[str, Any]]]] = []
        failures: list[tuple[str, ApmError]] = []
        for u in scope.units:
            defs = await core._business_defs_of(u.source, u.domain_id, now, scope.limits, failures)
            if defs is not None:
                out.append((u, defs))
        if scope.units and len(failures) == len(scope.units):
            raise core._all_failed(failures)
        return out

    async def _resolve_businesses(
        self, scope: ServiceScope, names: list[str] | None
    ) -> list[BusinessUnit]:
        """업무 대상 — `names`가 없으면 범위의 전 업무, 있으면 업무 이름 단계 검색으로 찾은 업무
        (이름마다 가장 앞 단계 · 못 찾은 이름은 고지·후보). 업무 정의를 읽지 못한 도메인이 있으면
        못 찾은 이름의 고지에 그 사실을 붙인다."""
        defs = await self._business_defs(scope)
        candidates = [(u, b) for u, bs in defs for b in bs if isinstance(b.get("business_id"), int)]
        if names is None:
            return [BusinessUnit(u, b["business_id"], b["business_name"], b) for u, b in candidates]
        unread = len(scope.units) - len(defs)
        limits = scope.limits
        chosen: dict[tuple[str, int, int], BusinessUnit] = {}
        for text in names:
            tier, hits, _ = search_names(
                text, (((i, u, b), b["business_name"], "") for i, (u, b) in enumerate(candidates))
            )
            shown = mask_text(text, limit=None)
            if tier is None:
                scope.missing.append(shown)
                limits.unresolve(
                    f"업무 '{shown}'에 해당하는 제니퍼 업무를 찾지 못했습니다"
                    + (
                        f"(업무 목록을 읽지 못한 도메인 {unread}곳은 확인하지 못했습니다)"
                        if unread
                        else ""
                    )
                )
                scope.suggestions += [
                    {"query": shown, **s}
                    for s in suggest_names(
                        normalize_name(text),
                        (
                            (
                                (i,),
                                b["business_name"],
                                {
                                    "business_name": b["business_name"],
                                    "business_id": b["business_id"],
                                    "source_id": u.source.source_id,
                                    "domain_id": u.domain_id,
                                },
                            )
                            for i, (u, b) in enumerate(candidates)
                        ),
                    )
                ]
                continue
            if len(hits) > 1:
                limits.append(
                    f"[한계] 업무 '{shown}' — 이름이 맞은 업무 {len(hits)}건을 모두 조회했다"
                    f"({tier} 단계): "
                    + ", ".join(
                        f"{b['business_name']}"
                        f"({self.core.sources.where(u.source.source_id, u.domain_id)}"
                        f" · 업무 {b['business_id']})"
                        for _, u, b in hits
                    )
                )
            for _, u, b in hits:
                chosen.setdefault(
                    (u.source.source_id, u.domain_id, b["business_id"]),
                    BusinessUnit(u, b["business_id"], b["business_name"], b, text, tier),
                )
        return list(chosen.values())

    # ── apm_business ────────────────────────────────────────

    async def apm_business(
        self,
        mode: str = "current",
        business: str | list[str] | None = None,
        service: str | list[str] | None = None,
        domain_id: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """업무 — `list`: 업무 정의(도메인마다 업무 목록) · `current`: 업무 현재값(이름이 있으면 그
        업무마다 1호출, 없으면 도메인마다 전 업무 1호출). `service`·`domain_id`로 도메인을
        좁힌다."""
        tool = BUSINESS_TOOL
        mode_name = str(mode or "current").strip().lower()
        if mode_name not in BUSINESS_MODES:
            raise ApmError(
                INVALID_ARGUMENT, f"mode는 {'·'.join(BUSINESS_MODES)} 중 하나여야 한다: {mode!r}"
            )
        names = name_list(business, "business")
        scope = await self.service_scope(service, domain_id, source_ids)
        if scope.blocked:
            return self.envelope(tool, [], scope, mode=mode_name)
        if mode_name == "list":
            targets = await self._resolve_businesses(scope, names)
            rows = [self._definition_row(t) for t in targets]
            return self.envelope(tool, rows, scope, mode=mode_name, file_only=BUSINESS_FILE_ONLY)
        if names is None:
            rows = await self._current_all(scope)
        else:
            targets = await self._resolve_businesses(scope, names)
            rows = await self._current_named(scope, targets) if targets else []
        return self.envelope(tool, rows, scope, mode=mode_name)

    @staticmethod
    def _definition_row(t: BusinessUnit) -> dict[str, Any]:
        b = t.definition or {}
        row: dict[str, Any] = {
            "source_id": t.unit.source.source_id,
            "domain_id": t.unit.domain_id,
            "domain_name": t.unit.domain_name,
            "business_id": t.business_id,
            "business_name": t.business_name,
            "business_description": mask_text(b.get("business_description", ""), limit=None),
            "bad_response_time_ms": b.get("bad_response_time_ms"),
            "business_index": b.get("business_index", ""),
            "business_oid": b.get("business_oid"),
            "rules": [mask_url(r) for r in b.get("rules") or []],
        }
        if t.text:
            row["business_text"] = mask_text(t.text, limit=None)
            row["match_tier"] = t.tier
        return row

    @staticmethod
    def _business_row(
        unit: ServiceUnit, rec: dict[str, Any], target: BusinessUnit | None = None
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "source_id": unit.source.source_id,
            "domain_id": unit.domain_id,
            "domain_name": unit.domain_name,
            "business_id": target.business_id if target else rec.get("business_id"),
            "business_name": rec.get("business_name") or (target.business_name if target else ""),
        }
        for name in BUSINESS_FIELDS:
            row[name] = rec.get(name)
        if target is not None and target.text:
            row["business_text"] = mask_text(target.text, limit=None)
            row["match_tier"] = target.tier
        return row

    async def _current_all(self, scope: ServiceScope) -> list[dict[str, Any]]:
        """도메인마다 전 업무 현재값 1호출."""
        core = self.core
        expect_calls(len(scope.units))
        rows: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        failed: dict[str, ApmError] = {}
        answered: set[str] = set()
        for u in scope.units:
            sid = u.source.source_id
            try:
                records = await u.source.api.realtime_business(u.domain_id)
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failures.append((core.sources.where(sid, u.domain_id), e))
                failed.setdefault(sid, e)
                scope.limits.fail(
                    f"[한계] 실시간 업무 조회 실패{self._at(sid, u.domain_id)}: {e.code}"
                    + core._reason_tail(e)
                )
                continue
            answered.add(sid)
            rows += [self._business_row(u, rec) for rec in records]
        if failures and len(failures) == len(scope.units):
            raise core._all_failed(failures)
        self._mark_failed_sources(scope, failed, answered)
        return rows

    async def _current_named(
        self, scope: ServiceScope, targets: list[BusinessUnit]
    ) -> list[dict[str, Any]]:
        """찾은 업무마다 현재값 1호출 — 응답에서 그 업무 행만 남긴다."""
        core = self.core
        expect_calls(len(targets))
        rows: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        failed: dict[str, ApmError] = {}
        answered: set[str] = set()
        missing: list[str] = []
        for t in targets:
            u, sid = t.unit, t.unit.source.source_id
            # 도메인이 있으니 위치 문구는 비지 않는다(소스가 하나면 「도메인 N」)
            label = f"{core.sources.where(sid, u.domain_id)} · 업무 {t.business_id}"
            try:
                records = await u.source.api.realtime_business(u.domain_id, t.business_id)
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failures.append((label, e))
                failed.setdefault(sid, e)
                scope.limits.fail(
                    f"[한계] 실시간 업무 조회 실패({label}): {e.code}" + core._reason_tail(e)
                )
                continue
            answered.add(sid)
            rec = next((r for r in records if r.get("business_id") == t.business_id), None)
            if rec is None:
                missing.append(f"{t.business_name}({label})")
                continue
            rows.append(self._business_row(u, rec, t))
        if failures and len(failures) == len(targets):
            raise core._all_failed(failures)
        self._mark_failed_sources(scope, failed, answered)
        if missing:
            scope.limits.append(f"[한계] 실시간 데이터 없는 업무: {', '.join(missing)}")
        return rows

    # ── 도메인·업무 시계열(apm_metrics series) ──────────────────

    async def metric_series(
        self,
        scope_name: str,
        requested: list[str],
        interval: int,
        reference_time: str | None,
        lookback_minutes: int | None,
        source_ids: list[str] | None,
        *,
        service: str | list[str] | None = None,
        business: str | list[str] | None = None,
        business_id: int | None = None,
        domain_id: int | None = None,
    ) -> dict[str, Any]:
        """도메인(`scope_name` domain)·업무(business) 지표 시계열 — 대상 × 지표마다 1호출. 대상이
        없으면 고른 범위의 전 도메인·전 업무. 지표는 카탈로그의 그 군으로 검증한다(인스턴스
        시계열과 같은 규칙 — 모르는 지표는 후보 ≤3 · 카탈로그 실패는 검증 없이 조회 + `[한계]`)."""
        core = self.core
        if scope_name == "domain" and (business is not None or business_id is not None):
            raise ApmError(
                INVALID_ARGUMENT, "business·business_id는 scope business 시계열에서만 쓴다"
            )
        names = name_list(business, "business")
        bid = None if business_id is None else _nonneg_int(business_id, "business_id")
        if bid is not None and names is not None:
            raise ApmError(INVALID_ARGUMENT, "business와 business_id는 함께 줄 수 없다")
        if bid is not None and domain_id is None:
            raise ApmError(
                INVALID_ARGUMENT,
                "business_id에는 domain_id가 필요하다(업무 id는 도메인마다 매긴다)",
            )
        window = core.window(
            reference_time, lookback_minutes, default_minutes=SERIES_DEFAULT_MINUTES
        )
        assert window is not None
        scope = await self.service_scope(service, domain_id, source_ids)
        limits = scope.limits
        limits.append(SERIES_INTERVAL_NOTE)
        extra: dict[str, Any] = {
            "window": window,
            "mode": "series",
            "scope": scope_name,
            "interval_minute": interval,
        }
        if scope.blocked:
            return self.envelope(METRICS_TOOL, [], scope, **extra)
        if scope_name == "domain":
            targets: list[tuple[ServiceUnit, BusinessUnit | None]] = [
                (u, None) for u in scope.units
            ]
        else:
            found = (
                await self._business_by_id(scope, bid)
                if bid is not None
                else await self._resolve_businesses(scope, names)
            )
            targets = [(t.unit, t) for t in found]
            if not targets:
                return self.envelope(METRICS_TOOL, [], scope, **extra)
        plan = await self._series_plan(
            sorted({u.source.source_id for u, _ in targets}), requested, scope_name, limits
        )
        expect_calls(sum(len(plan.get(u.source.source_id, [])) for u, _ in targets))
        rows: list[dict[str, Any]] = []
        empty: list[str] = []
        failures: list[tuple[str, ApmError]] = []
        attempted = 0
        for u, t in targets:
            sid = u.source.source_id
            where = core.sources.where(sid, u.domain_id)  # 도메인이 있어 비지 않는다
            label = f"{where} · 업무 {t.business_id}" if t else where
            base: dict[str, Any] = {
                "source_id": sid,
                "domain_id": u.domain_id,
                "domain_name": u.domain_name,
            }
            if t is not None:
                base["business_id"] = t.business_id
                base["business_name"] = t.business_name
            for metric_id, _label in plan.get(sid, []):
                attempted += 1
                try:
                    if t is None:
                        points = await u.source.api.domain_metric_series(
                            u.domain_id, metric_id, interval, window.start_ms, window.end_ms
                        )
                    else:
                        points = await u.source.api.business_metric_series(
                            u.domain_id,
                            t.business_id,
                            metric_id,
                            interval,
                            window.start_ms,
                            window.end_ms,
                        )
                except ApmError as e:
                    if e.code == CONTRACT_VIOLATION:
                        raise
                    failures.append((f"{label} 지표 {metric_id}", e))
                    limits.fail(
                        f"[한계] 지표 {metric_id} 조회 실패({label}): {e.code}"
                        + core._reason_tail(e)
                    )
                    continue
                if not points:
                    empty.append(f"{label}:{metric_id}")
                rows += [
                    {**base, "metric": metric_id, "time_ms": p["time_ms"], "value": p["value"]}
                    for p in points
                ]
        if attempted and len(failures) == attempted:
            raise core._all_failed(failures)
        if empty:
            what = "도메인" if scope_name == "domain" else "업무"
            limits.append(f"[한계] 데이터 점이 없는 ({what}:지표): {', '.join(empty)}")
        return self.envelope(METRICS_TOOL, rows, scope, **extra)

    async def _series_plan(
        self, source_ids: list[str], requested: list[str], scope_name: str, limits: _Limits
    ) -> dict[str, list[tuple[str, str]]]:
        """도메인·업무 시계열 지표 → 소스별 `[(식별자, 표시 이름)]`.

        - 지정한 지표는 인스턴스 시계열과 같은 검증(`_metric_plan`). 일부만 모르면 빼고 조회 +
          미해결 고지(후보 ≤3) + `partial`(요청한 지표가 빠진 결과).
        - 비우면 군별 기본 지표(카탈로그에 있는 것)로 조회하고 `[한계]`에 적는다.
        - 전부 모르면 기본 지표로 조회하고 미해결 고지(요청 지표 · 쓴 기본 지표 · 후보) + `partial`.
        """
        core = self.core
        candidates: dict[str, list[str]] = {}
        if requested:
            plan = await core._metric_plan(
                source_ids,
                requested,
                scope_name,
                limits,
                all_unknown_raises=False,
                candidates=candidates,
            )
            if plan is not None:
                if candidates:  # 일부를 빼고 조회했다
                    limits.partial = True
                return plan
        plan, used = await self._default_series_plan(source_ids, scope_name, limits)
        shown = ", ".join(used) or "없음"
        if not requested:
            limits.append(f"[한계] 지표 미지정 — 기본 지표({shown})로 조회")
        else:
            hints = "; ".join(
                f"{label}(후보: {', '.join(close) if close else '없음'})"
                for label, close in candidates.items()
            )
            limits.unresolve(
                f"요청한 지표 {', '.join(candidates)}는 목록에 없어 기본 지표({shown})로"
                f" 조회했습니다 · 후보 {hints}"
            )
            limits.partial = True
        if not used:
            limits.fail(
                f"[한계] {scope_name} 기본 지표가 지표 카탈로그에 없어 조회하지 못했다 —"
                " apm_metrics(mode catalog)로 지표를 고른다"
            )
        return plan

    async def _default_series_plan(
        self, source_ids: list[str], scope_name: str, limits: _Limits
    ) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
        """군별 기본 지표 중 카탈로그(그 군)에 있는 것 → (소스별 계획, 쓴 표시 이름 — 중립 이름이
        있으면 중립 이름). 카탈로그를 읽지 못했거나 그 군이 모양 위반인 소스는 검증 없이 기본 지표
        전부(`[한계]` · partial)."""
        core = self.core
        defaults = [
            (mid, JenniferApi.neutral_metric(mid) or mid)
            for mid in JenniferApi.default_series_metrics(scope_name)
        ]
        plan: dict[str, list[tuple[str, str]]] = {}
        for sid in source_ids:
            try:
                entry = await core._catalog(sid)
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.fail(
                    f"[한계] 지표 카탈로그 조회 실패{self._at(sid)}: {e.code} — 기본 지표를"
                    " 검증하지 못하고 그대로 조회했다"
                )
                plan[sid] = list(defaults)
                continue
            if entry.change_note:
                limits.append(entry.change_note)
            if scope_name in entry.invalid:
                limits.fail(
                    f"[한계] 지표 카탈로그 {scope_name} 군 모양 위반{self._at(sid)} — 기본 지표를"
                    " 검증하지 못하고 그대로 조회했다"
                )
                plan[sid] = list(defaults)
                continue
            names = set(entry.scopes.get(scope_name, []))
            plan[sid] = [(mid, label) for mid, label in defaults if mid in names]
        used = [label for mid, label in defaults if any((mid, label) in p for p in plan.values())]
        return plan, used

    async def _business_by_id(self, scope: ServiceScope, business_id: int) -> list[BusinessUnit]:
        """업무 id 직접 지정 — 범위의 도메인마다 업무 정의(캐시)로 이름을 채운다. 정의에 없는 id는
        그 도메인에서 대상이 아니다(고지 · 다른 업무로 대신하지 않는다). 정의를 읽지 못한 도메인은
        이름 없이 조회한다(그 사실은 `[한계]`)."""
        core = self.core
        now = core.clock()
        expect_calls(
            sum(
                1
                for u in scope.units
                if not _fresh(core._business_defs.get((u.source.source_id, u.domain_id)), now)
            )
        )
        out: list[BusinessUnit] = []
        for u in scope.units:
            defs = await core._business_defs_of(u.source, u.domain_id, now, scope.limits)
            if defs is None:
                out.append(BusinessUnit(u, business_id, "", None))
                continue
            b = next((x for x in defs if x.get("business_id") == business_id), None)
            if b is None:
                scope.limits.unresolve(
                    f"업무 ID {business_id}는 {self._domain_label(u)} 업무 목록에 없습니다"
                )
                continue
            out.append(BusinessUnit(u, business_id, b["business_name"], b))
        return out
