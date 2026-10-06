"""패싯 변환 단계 · 연결 장부 (plans/125 E-3 · E-4 · §4.5) — LLM 0.

비SQL 처리기(예 `apm_query`)가 요구하는 대상 패싯(hostname)을 선행 결과가 주지 못할 때, 레지스트리
간선 표에서 경로를 찾아 **간선 소유자의 결정적 수단**으로 변환한다. 지금 실행 수단이 있는 간선은
E2(폴스타 등록명 → OS hostname · 어댑터 고정 조회 — D-089)와 E6(업무명 → hostname · 같은 어댑터
고정 조회 · plans/130 M-3)이다. 표 밖 경로·수단 없는 간선은 조회하지
않고 장부에 사유를 남긴다(침묵 금지).

**연결 장부**: 대상 키마다 `linked`(등급) · `unlinked`(0건) · `ambiguous`(다건 — 자동 결합 금지) ·
`not_queried`(사유)를 적는다(121 §4.6 ③ 체크리스트의 브리지 커버리지와 같은 구조).

계층: orchestration.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from src.db import get_db_client
from src.db_adapters import get_adapter
from src.routing import db_authz
from src.routing.domain_config import get_domain_by_id
from src.routing.entity_edges import cross_system_only, facet_path
from src.routing.registry import get_registry
from src.utils.prior_targets import TargetRef

logger = logging.getLogger(__name__)

LINKED = "linked"
UNLINKED = "unlinked"
AMBIGUOUS = "ambiguous"
NOT_QUERIED = "not_queried"


@dataclass
class LinkEntry:
    """연결 장부 1행 — 대상 키 하나의 한 간선 결과."""

    key: str
    facet: str
    edge: str | None
    status: str
    grade: str | None = None
    value: str | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


def _active_system_dbs(system: str, app_config: Any) -> list[str]:
    getter = getattr(getattr(app_config, "multi_db", None), "get_active_db_ids", None)
    active = list(getter()) if callable(getter) else []
    return [d for d in get_registry().system_db_ids(system) if d in active]


async def _lookup_hostnames(
    names: list[str], db_ids: list[str], app_config: Any,
) -> tuple[dict[str, set[str]], list[str]]:
    """간선 E2 실행 — {이름(소문자): hostname 집합}, 실패 사유 목록."""
    found: dict[str, set[str]] = {n.lower(): set() for n in names}
    errors: list[str] = []
    getter = getattr(app_config, "get_polestar_db_ids", None)
    polestar_ids: set[str] | None = (set(getter()) if callable(getter) else set()) or None
    for db_id in db_ids:
        adapter = get_adapter(db_id, polestar_ids)
        build = getattr(adapter, "hostname_lookup_sql", None) if adapter else None
        if not callable(build):
            errors.append(f"{db_id}: hostname 변환 수단(어댑터)이 없다")
            continue
        domain = get_domain_by_id(db_id)
        sql = build(names, db_engine=domain.db_engine if domain else None,
                    db_schema=(domain.db_schema if domain else "") or None)
        try:
            async with get_db_client(app_config, db_id=db_id) as client:
                result = await client.execute_sql(sql)
        except Exception as e:  # noqa: BLE001 — 변환 실패가 조회 전체를 막지 않는다(사유로 남김)
            logger.warning("간선 E2 조회 실패(%s): %s", db_id, type(e).__name__)
            errors.append(f"{db_id}: 조회 실패({type(e).__name__})")
            continue
        for row in result.rows or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("server_name") or "").strip().lower()
            host = str(row.get("hostname") or "").strip()
            if name in found and host:
                found[name].add(host)
    return found, errors


async def link_hostnames(
    targets: list[TargetRef], *, consumer: str, app_config: Any,
) -> tuple[list[str], list[LinkEntry], list[dict[str, Any]]]:
    """대상 → hostname 목록 · 연결 장부 · 삽입한 변환 단계.

    hostname 이 있는 대상은 그대로 쓰고(장부 `given`), 없는 대상은 간선 표 경로로 변환한다 — 교차
    시스템 간선에서만(소비 시스템이 소유한 간선은 삽입하지 않는다).
    """
    hostnames: list[str] = []
    ledger: list[LinkEntry] = []
    steps: list[dict[str, Any]] = []
    pending: list[TargetRef] = []
    for target in targets:
        if target.hostname:
            if target.hostname not in hostnames:
                hostnames.append(target.hostname)
            ledger.append(LinkEntry(target.hostname, "hostname", None, LINKED, grade="given"))
        else:
            pending.append(target)
    if not pending:
        return hostnames, ledger, steps

    by_name = [t for t in pending if t.server_name]
    for target in pending:
        if not target.server_name:
            key = target.ip or "?"
            ledger.append(LinkEntry(key, "ip", None, NOT_QUERIED,
                                    reason="ip → hostname 간선이 표에 없다"))
    if not by_name:
        return hostnames, ledger, steps

    path = facet_path({"server_name"}, "hostname")
    if not path or not cross_system_only(path, consumer) or len(path) != 1 or path[0].id != "E2":
        for target in by_name:
            ledger.append(LinkEntry(str(target.server_name), "server_name", None, NOT_QUERIED,
                                    reason="허용된 교차 시스템 간선 경로가 없다"))
        return hostnames, ledger, steps

    edge = path[0]
    db_ids = list(dict.fromkeys(t.db_id for t in by_name if t.db_id)) or _active_system_dbs(
        edge.owner, app_config)
    names = list(dict.fromkeys(str(t.server_name) for t in by_name))
    step: dict[str, Any] = {"edge": edge.id, "from": edge.from_facet, "to": edge.to_facet,
                            "owner": edge.owner, "keys": len(names), "db_ids": db_ids}
    if not db_ids:
        step["error"] = f"{edge.owner} 활성 DB 가 없다"
        steps.append(step)
        for name in names:
            ledger.append(LinkEntry(name, "server_name", edge.id, NOT_QUERIED,
                                    reason=step["error"]))
        return hostnames, ledger, steps

    found, errors = await _lookup_hostnames(names, db_ids, app_config)
    if errors:
        step["errors"] = errors
    for name in names:
        hosts = sorted(found.get(name.lower()) or ())
        if len(hosts) == 1:
            ledger.append(LinkEntry(name, "server_name", edge.id, LINKED, grade="one",
                                    value=hosts[0]))
            if hosts[0] not in hostnames:
                hostnames.append(hosts[0])
        elif hosts:
            ledger.append(LinkEntry(name, "server_name", edge.id, AMBIGUOUS, grade="many",
                                    reason=f"hostname {len(hosts)}건 — 자동 결합하지 않는다"))
        elif errors and len(errors) == len(db_ids):
            ledger.append(LinkEntry(name, "server_name", edge.id, NOT_QUERIED,
                                    reason="; ".join(errors)[:160]))
        else:
            ledger.append(LinkEntry(name, "server_name", edge.id, UNLINKED, grade="none",
                                    reason="등록 서버에서 찾지 못했다"))
    step["linked"] = sum(1 for e in ledger if e.edge == edge.id and e.status == LINKED)
    steps.append(step)
    return hostnames, ledger, steps


#: 업무명 조회 행 상한 — 간선 E6 소유자 어댑터의 조회 상한과 같은 값(시스템 기본 LIMIT · 테스트가
#: 두 값의 일치를 고정한다). 결과가 이 수에 닿으면 「잘림」으로 본다.
_BUSINESS_ROW_CAP = 1000
#: 업무명 검색어 최소 길이 — 어댑터가 1자 검색어를 버리므로 장부에 「조회 안 함」 사유를 남긴다.
_BUSINESS_TERM_MIN_LEN = 2


def _norm_row(row: dict[str, Any]) -> dict[str, str]:
    """결과 행 키를 소문자로 정규화한다(DB2 는 대문자 칼럼 키를 줄 수 있다)."""
    return {str(k).lower(): str(v or "").strip() for k, v in row.items()}


async def _lookup_business(
    terms: list[str], db_ids: list[str], app_config: Any,
) -> tuple[list[tuple[str, dict[str, str]]], list[str], bool]:
    """간선 E6 실행 — (db_id, 정규화 행) 목록 · 실패 사유 목록 · 상한 도달 여부."""
    rows: list[tuple[str, dict[str, str]]] = []
    errors: list[str] = []
    truncated = False
    getter = getattr(app_config, "get_polestar_db_ids", None)
    polestar_ids: set[str] | None = (set(getter()) if callable(getter) else set()) or None
    for db_id in db_ids:
        adapter = get_adapter(db_id, polestar_ids)
        build = getattr(adapter, "business_lookup_sql", None) if adapter else None
        if not callable(build):
            errors.append(f"{db_id}: 업무명 조회 수단(어댑터)이 없다")
            continue
        domain = get_domain_by_id(db_id)
        sql = build(terms, db_engine=domain.db_engine if domain else None,
                    db_schema=(domain.db_schema if domain else "") or None)
        try:
            async with get_db_client(app_config, db_id=db_id) as client:
                result = await client.execute_sql(sql)
        except Exception as e:  # noqa: BLE001 — 한 DB 실패가 다른 DB 조회를 막지 않는다(사유로 남김)
            logger.warning("간선 E6 조회 실패(%s): %s", db_id, type(e).__name__)
            errors.append(f"{db_id}: 조회 실패({type(e).__name__})")
            continue
        got = [_norm_row(r) for r in (result.rows or []) if isinstance(r, dict)]
        if getattr(result, "truncated", False) is True or len(got) >= _BUSINESS_ROW_CAP:
            truncated = True
        rows.extend((db_id, r) for r in got)
    return rows, errors, truncated


async def link_business_names(
    terms: list[str], *, app_config: Any, authorized_db_ids: list[str] | None,
) -> tuple[dict[str, list[dict[str, Any]]], list[LinkEntry], list[dict[str, Any]]]:
    """업무명 → 서버(hostname) 목록 · 연결 장부 · 변환 단계(plans/130 M-3 간선 E6 · LLM 0).

    간선 소유자의 **활성 DB ∩ 사용자 DB 권한**만 조회한다(D-232). `authorized_db_ids` 규약은
    `src.routing.db_authz` 와 같다 — `None`=전체 허용 · `[]`=없음 · 목록=교집합(관리자 예외는
    호출부가 역할을 반영해 넘긴다). 업무 하나에 서버 여러 대는 정상이라 다건도 `linked`(many)다.
    검색어별 판정은 등록명 → 비고 순 대소문자 무시 부분 일치이며, hostname 이 빈 행은 결과에서
    빼고 개수만 사유에 남긴다. DB 별 실패는 사유로 남기고 계속한다.

    Returns:
        `({검색어: [{hostname, server_name, db_id, field}]}, 장부, 단계)` — field 는 "name"(등록명)
        또는 "description"(비고). 같은 db_id+hostname 은 한 번만 담는다.
    """
    keys = list(dict.fromkeys(str(t).strip() for t in terms if str(t).strip()))
    found: dict[str, list[dict[str, Any]]] = {k: [] for k in keys}
    ledger: list[LinkEntry] = []
    steps: list[dict[str, Any]] = []
    queryable = [k for k in keys if len(k) >= _BUSINESS_TERM_MIN_LEN]
    for key in keys:
        if key not in queryable:
            ledger.append(LinkEntry(key, "business_name", None, NOT_QUERIED,
                                    reason=f"검색어가 {_BUSINESS_TERM_MIN_LEN}자 미만이다"))
    if not queryable:
        return found, ledger, steps

    path = facet_path({"business_name"}, "hostname")
    if not path or len(path) != 1 or path[0].id != "E6":
        for key in queryable:
            ledger.append(LinkEntry(key, "business_name", None, NOT_QUERIED,
                                    reason="업무명 → hostname 간선(E6)이 표에 없다"))
        return found, ledger, steps

    edge = path[0]
    active = _active_system_dbs(edge.owner, app_config)
    db_ids = db_authz.authorized_db_ids(active, authorized_db_ids)
    step: dict[str, Any] = {"edge": edge.id, "from": edge.from_facet, "to": edge.to_facet,
                            "owner": edge.owner, "keys": len(queryable), "db_ids": db_ids}
    if not db_ids:
        step["error"] = (f"{edge.owner} 활성 DB 가 없다" if not active
                         else f"권한 안의 활성 {edge.owner} DB 가 없다")
        steps.append(step)
        for key in queryable:
            ledger.append(LinkEntry(key, "business_name", edge.id, NOT_QUERIED,
                                    reason=step["error"]))
        return found, ledger, steps

    rows, errors, truncated = await _lookup_business(queryable, db_ids, app_config)
    if errors:
        step["errors"] = errors
    if truncated:
        step["truncated"] = True
    for key in queryable:
        needle = key.lower()
        seen: set[tuple[str, str]] = set()
        blank = 0
        for db_id, row in rows:
            name, desc = row.get("server_name", ""), row.get("description", "")
            field = ("name" if needle in name.lower()
                     else "description" if needle in desc.lower() else None)
            if field is None:
                continue
            host = row.get("hostname", "")
            if not host:
                blank += 1
                continue
            if (db_id, host) in seen:
                continue
            seen.add((db_id, host))
            found[key].append({"hostname": host, "server_name": name, "db_id": db_id,
                               "field": field})
        notes = [n for n in (
            f"hostname 빈 행 {blank}건 제외" if blank else "",
            f"조회 상한 {_BUSINESS_ROW_CAP}행에 닿아 일부만 확인" if truncated else "",
            "일부 DB 조회 실패" if errors else "",
        ) if n]
        hits = found[key]
        if hits:
            hosts = list(dict.fromkeys(h["hostname"] for h in hits))
            ledger.append(LinkEntry(key, "business_name", edge.id, LINKED,
                                    grade="one" if len(hits) == 1 else "many",
                                    value=", ".join(hosts)[:160], reason=" · ".join(notes)))
        elif errors and len(errors) == len(db_ids):
            ledger.append(LinkEntry(key, "business_name", edge.id, NOT_QUERIED,
                                    reason="; ".join(errors)[:160]))
        else:
            ledger.append(LinkEntry(key, "business_name", edge.id, UNLINKED, grade="none",
                                    reason=" · ".join(["등록 서버의 등록명·비고에서 찾지 못했다",
                                                       *notes])))
    step["linked"] = sum(1 for e in ledger if e.edge == edge.id and e.status == LINKED)
    steps.append(step)
    return found, ledger, steps


def ledger_summary(ledger: list[LinkEntry]) -> dict[str, dict[str, int]]:
    """패싯별 상태 건수(개수만 — 계획 요약·반출용)."""
    out: dict[str, dict[str, int]] = {}
    for entry in ledger:
        bucket = out.setdefault(entry.facet, {})
        label = entry.status if entry.status != LINKED else f"{LINKED}:{entry.grade or '-'}"
        bucket[label] = bucket.get(label, 0) + 1
    return out


def ledger_line(ledger: list[LinkEntry]) -> str:
    """응답에 붙일 연결 장부 한 줄 — 변환·정합 결과가 있을 때만(모두 직접 지정이면 빈 문자열)."""
    parts: list[str] = []
    names = [e for e in ledger if e.facet == "server_name"]
    if names:
        linked = sum(1 for e in names if e.status == LINKED)
        extra = [f"{label} {n}" for label, n in (
            ("미연결", sum(1 for e in names if e.status == UNLINKED)),
            ("모호", sum(1 for e in names if e.status == AMBIGUOUS)),
            ("조회 안 함", sum(1 for e in names if e.status == NOT_QUERIED)),
        ) if n]
        parts.append(f"호스트 연결(서버명 → hostname) {linked}/{len(names)}"
                     + (" · " + " · ".join(extra) if extra else ""))
    business = [e for e in ledger if e.facet == "business_name"]
    if business:
        linked = sum(1 for e in business if e.status == LINKED)
        extra = [f"{label} {n}" for label, n in (
            ("미연결", sum(1 for e in business if e.status == UNLINKED)),
            ("조회 안 함", sum(1 for e in business if e.status == NOT_QUERIED)),
        ) if n]
        parts.append(f"업무명 연결(업무명 → hostname) {linked}/{len(business)}"
                     + (" · " + " · ".join(extra) if extra else ""))
    apm = [e for e in ledger if e.facet == "apm_instance"]
    if apm:
        grades = {g: sum(1 for e in apm if e.status == LINKED and e.grade == g)
                  for g in ("high", "medium")}
        linked = sum(grades.values())
        graded = " · ".join(f"{g} {n}" for g, n in grades.items() if n)
        unlinked = sum(1 for e in apm if e.status != LINKED)
        parts.append(f"WAS 연결 {linked}/{len(apm)}" + (f"({graded})" if graded else "")
                     + (f" · 미연결 {unlinked}" if unlinked else ""))
    return " · ".join(parts)


__all__ = [
    "AMBIGUOUS", "LINKED", "NOT_QUERIED", "UNLINKED", "LinkEntry", "ledger_line",
    "ledger_summary", "link_business_names", "link_hostnames",
]
