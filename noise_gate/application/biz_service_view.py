"""업무 서비스 정의(P1) 조회 조립 — 존 범위 · 목록 행 · 상세 · CSV 내보내기 · 연계 그래프 ·
스냅샷 검색 표시 · 미소속 서버 · 변경 이력 (plans/215 §3.4 · §3.7 · §5.1 · §5.3 · D-368).

P1 조회 라우트(`src/api/routes/biz_service.py`)와 관리 라우트의 편집 보조(스냅샷 검색·미소속 서버·
변경 이력)가 부른다. 저장소·210 스냅샷·감사 기록 행(dict)을 받아 화면 모양 dict를 만드는 판단만
여기 둔다(D-139 — 본체 라우트는 인가·입력·마스킹만). SQL·관측 시스템 호출·LLM 0.

- 존 범위(`scope`): None = 전 존. 존 밖 구성원은 이름 없이 건수만 — 보이는 구성원이 하나도 없는
  서비스는 목록에서 빼고 `hidden_service_count`로만 센다(업무 현황 보기와 같은 규칙).
- 연계 지표(APM 커버리지·열린 후보·충돌·끊김)는 마지막 적용 실행의 §3.4 요약에서 읽는다
  (없으면 None).
- CSV 칸이 수식 시작 문자(`=`·`+`·`-`·`@`·탭·CR)로 시작하면 앞에 `'`를 붙인다(§9 · 수용 22).

계층: application.
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any

from noise_gate.application.biz_investigation_context import member_zone
from noise_gate.application.biz_monitor_alarms import zone_visible
from noise_gate.application.biz_service import sms_servers
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, match_key_for
from noise_gate.domain.biz_service_keys import (
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_instance_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_model import HostPrefixConfig, text
from noise_gate.domain.biz_service_signals import extract_prefix_code

#: CSV 형식 — 가져오기 템플릿과 같다(§5.1 ⑤).
CSV_HEADER = (
    "service_key", "display_name", "tier", "owner_org", "db_id", "server_name", "hostname",
    "layer",
)
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"
#: 목록 상태 필터 — 확정(확인됨) · 추정(미확인 활성) · 검토 필요 · 비활성.
STATUS_FILTERS = ("confirmed", "inferred", "review", "disabled")
SORT_KEYS = ("tier", "name", "sms", "apm", "coverage", "inferred", "updated")
#: 미소속 서버 응답 상한(서버 행 수).
UNASSIGNED_LIMIT = 500
#: 변경 이력 최근 건수.
HISTORY_LIMIT = 50
_LINE_CONFIRMED, _LINE_INFERRED, _LINE_BROKEN = "confirmed", "inferred", "broken"


# ── 존 범위 ─────────────────────────────────────────────────────────────
def member_visible(scope: Collection[str] | None, member: Mapping[str, Any]) -> bool:
    """이 구성원을 이름까지 보여도 되는가(존 범위)."""
    return zone_visible(scope, member_zone(member))


# ── 연계 지표(마지막 적용 실행) ─────────────────────────────────────────
def analysis_services(run: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """적용 실행 요약 → 서비스 키별 §3.4 지표(없으면 빈 dict)."""
    summary = run.get("summary") if isinstance(run, Mapping) else None
    services = summary.get("services") if isinstance(summary, Mapping) else None
    if not isinstance(services, Mapping):
        return {}
    return {str(k): v for k, v in services.items() if isinstance(v, Mapping)}


def _coverage(metrics: Mapping[str, Any] | None) -> float | None:
    if not metrics:
        return None
    total = int(metrics.get("sms_servers") or 0)
    if total <= 0:
        return None
    return round(int(metrics.get("apm_linked_servers") or 0) / total, 4)


def review_reasons(service: Mapping[str, Any], metrics: Mapping[str, Any] | None) -> list[str]:
    """「검토 필요」 사유 — 모호 상태 · 충돌 · 열린 후보 · 끊김(지표가 있을 때만)."""
    out: list[str] = []
    if service.get("state") == "ambiguous":
        out.append("ambiguous")
    for key in ("conflicts", "open_candidates", "target_gone"):
        if metrics and int(metrics.get(key) or 0) > 0:
            out.append(key)
    return out


# ── 목록 ────────────────────────────────────────────────────────────────
def _stat_index(
    stats: Iterable[Mapping[str, Any]], scope: Collection[str] | None
) -> dict[int, dict[str, Any]]:
    """구성원 묶음 통계 → 서비스별 `{sms_by_zone, sms, apm, hidden, inferred, total}`."""
    out: dict[int, dict[str, Any]] = {}
    for row in stats:
        sid = int(row.get("service_id") or 0)
        n = int(row.get("n") or 0)
        cur = out.setdefault(sid, {"sms_by_zone": Counter(), "sms": 0, "apm": 0, "hidden": 0,
                                   "inferred": 0, "total": 0})
        cur["total"] += n
        if text(row.get("confidence")) == "inferred":
            cur["inferred"] += n
        if not member_visible(scope, row):
            cur["hidden"] += n
            continue
        if text(row.get("kind")) == KIND_SMS_SERVER:
            cur["sms"] += n
            cur["sms_by_zone"][member_zone(row) or ""] += n
        else:
            cur["apm"] += n
    return out


def list_rows(
    services: Sequence[Mapping[str, Any]],
    stats: Iterable[Mapping[str, Any]],
    *,
    scope: Collection[str] | None,
    metrics: Mapping[str, Mapping[str, Any]],
    zone: str | None = None,
    status: str | None = None,
    coverage_below: int | None = None,
    sort: str = "tier",
    descending: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """목록 행(존 범위·필터·정렬 적용 · 페이지 전) → `(행, 존 범위로 숨긴 서비스 수)`."""
    index = _stat_index(stats, scope)
    rows: list[dict[str, Any]] = []
    hidden = 0
    for svc in services:
        sid = int(svc.get("id") or 0)
        st = index.get(sid) or {"sms_by_zone": Counter(), "sms": 0, "apm": 0, "hidden": 0,
                                "inferred": 0, "total": 0}
        if scope is not None and st["sms"] + st["apm"] == 0:
            hidden += 1
            continue
        m = metrics.get(text(svc.get("service_key")))
        reasons = review_reasons(svc, m)
        row = {k: svc.get(k) for k in (
            "id", "service_key", "display_name", "tier", "owner_org", "origin", "state",
            "verified", "monitored", "version", "updated_at", "updated_by",
        )}
        row.update(
            sms_count=st["sms"], apm_count=st["apm"], hidden_member_count=st["hidden"],
            sms_by_zone=dict(sorted(st["sms_by_zone"].items())),
            inferred_ratio=round(st["inferred"] / st["total"], 4) if st["total"] else None,
            apm_coverage=_coverage(m), review_needed=bool(reasons), review_reasons=reasons,
        )
        if _keep(row, zone=zone, status=status, coverage_below=coverage_below):
            rows.append(row)
    rows.sort(key=_sort_key(sort), reverse=descending)
    return rows, hidden


def _keep(row: Mapping[str, Any], *, zone: str | None, status: str | None,
          coverage_below: int | None) -> bool:
    if zone and not row["sms_by_zone"].get(zone):
        return False
    if status == "confirmed" and not (row["verified"] and row["state"] != "disabled"):
        return False
    if status == "inferred" and (row["verified"] or row["state"] != "active"):
        return False
    if status == "review" and not row["review_needed"]:
        return False
    if status == "disabled" and row["state"] != "disabled":
        return False
    if coverage_below is not None:
        cov = row["apm_coverage"]
        if cov is None or cov * 100 >= coverage_below:
            return False
    return True


def _sort_key(sort: str) -> Callable[[Mapping[str, Any]], tuple[Any, ...]]:
    def _num(value: Any, missing: float) -> float:
        return missing if value is None else float(value)

    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        name = (text(row.get("display_name")).lower(), int(row.get("id") or 0))
        if sort == "name":
            return name
        if sort == "sms":
            return (row["sms_count"], *name)
        if sort == "apm":
            return (row["apm_count"], *name)
        if sort == "coverage":
            return (_num(row["apm_coverage"], 2.0), *name)
        if sort == "inferred":
            return (_num(row["inferred_ratio"], -1.0), *name)
        if sort == "updated":
            return (text(row.get("updated_at")), *name)
        return (_num(row.get("tier"), 9.0), *name)

    return key


# ── 상세 ────────────────────────────────────────────────────────────────
def scoped_detail(
    service: Mapping[str, Any], scope: Collection[str] | None, *, operator: bool
) -> dict[str, Any] | None:
    """상세 — 존 밖 구성원은 종류별 건수만 · 보이는 구성원이 없으면 None(404 — 존재도 숨긴다).

    운영자(관리자)가 아니면 근거 원문(`evidence`)·관리 메모(`note`)를 싣지 않는다.
    """
    members = [dict(m) for m in service.get("members") or []]
    shown = [m for m in members if member_visible(scope, m)]
    if scope is not None and not shown:
        return None
    hidden = Counter(text(m.get("kind")) for m in members if not member_visible(scope, m))
    head = {k: v for k, v in service.items() if k != "members"}
    if not operator:
        head.pop("evidence", None)
        head.pop("note", None)
        for m in shown:
            m.pop("evidence", None)
    return {"service": head, "members": shown, "hidden_member_count": sum(hidden.values()),
            "hidden_by_kind": dict(sorted(hidden.items()))}


def link_graph(
    members: Sequence[Mapping[str, Any]],
    maps: Iterable[Mapping[str, Any]] | None,
    scope: Collection[str] | None,
    zone_of: Callable[[str], str],
) -> dict[str, Any]:
    """연계 그래프(§3.4) — 서비스 → APM 도메인 → 인스턴스 → SMS 서버 트리.

    선 구분: `broken`(끊김 — 구성원·대상 사라짐 · 매핑 없음/비활성) · `inferred`(추정 — 구성원 추정
    또는 매핑 미확인) · `confirmed`(확정). 존 밖 서버는 이름 없이 `hidden_server_count`로만 센다.
    `maps`가 None이면 210 저장소가 없다는 뜻이다(`available=false`).
    """
    if maps is None:
        return {"available": False,
                "reason": "APM·SMS 연계(사전 스캔)가 꺼져 있어 연계 선을 그릴 수 없습니다.",
                "domains": [], "unlinked_servers": [], "hidden_server_count": 0}
    by_id: dict[int, Mapping[str, Any]] = {}
    for r in maps:
        try:
            by_id[int(r.get("id") or 0)] = r
        except (TypeError, ValueError):
            continue
    live = [m for m in members if m.get("state") != "excluded"]
    sms_keys = {text(m.get("member_key")) for m in live if m.get("kind") == KIND_SMS_SERVER}
    domains: dict[tuple[str, str], dict[str, Any]] = {}

    def _domain(m: Mapping[str, Any]) -> dict[str, Any]:
        k = (text(m.get("source_id")), text(m.get("domain_id")))
        return domains.setdefault(k, {"source_id": k[0], "domain_id": k[1],
                                      "domain_name": text(m.get("domain_name")) or None,
                                      "in_service": False, "instances": []})

    linked: set[str] = set()
    hidden = 0
    for m in live:
        kind = m.get("kind")
        if kind == KIND_APM_DOMAIN:
            _domain(m)["in_service"] = True
            continue
        if kind != KIND_APM_INSTANCE or not member_visible(scope, m):
            continue
        dom = _domain(m)
        if not dom["domain_name"] and text(m.get("domain_name")):
            dom["domain_name"] = text(m.get("domain_name"))
        map_id = m.get("apm_map_id")
        row = by_id.get(int(map_id)) if isinstance(map_id, int) else None
        base = _LINE_INFERRED if m.get("confidence") == "inferred" else _LINE_CONFIRMED
        if row is not None and row.get("verified") is not True:
            base = _LINE_INFERRED
        member_gone = m.get("state") == "target_gone"
        servers: list[dict[str, Any]] = []
        targets = row.get("targets") if row is not None else None
        for t in targets if isinstance(targets, list) else []:
            if not isinstance(t, Mapping) or text(t.get("kind") or "sms") != "sms":
                continue
            db_id = text(t.get("db_id"))
            if not zone_visible(scope, zone_of(db_id)):
                hidden += 1
                continue
            key = sms_member_key(db_id, text(t.get("resource_id")))
            gone = member_gone or bool(t.get("target_gone"))
            if key and key in sms_keys:
                linked.add(key)
            servers.append({"db_id": db_id, "resource_id": text(t.get("resource_id")) or None,
                            "server_name": text(t.get("server_name")) or None,
                            "in_service": bool(key and key in sms_keys),
                            "line": _LINE_BROKEN if gone else base})
        if row is None or text(row.get("state")) not in ("", "active"):
            line = _LINE_BROKEN
        else:
            line = _LINE_BROKEN if member_gone else base
        dom["instances"].append({
            "member_id": m.get("id"), "instance_name": text(m.get("instance_name")) or None,
            "member_key": m.get("member_key"), "confidence": m.get("confidence"),
            "state": m.get("state"), "map_id": map_id, "line": line, "servers": servers,
        })
    unlinked = [
        {"member_id": m.get("id"), "db_id": m.get("db_id"), "server_name": m.get("server_name")}
        for m in live
        if m.get("kind") == KIND_SMS_SERVER and member_visible(scope, m)
        and text(m.get("member_key")) not in linked
    ]
    ordered = sorted(domains.values(), key=lambda d: (d["source_id"], d["domain_id"]))
    return {"available": True, "reason": None, "domains": ordered,
            "unlinked_servers": unlinked, "hidden_server_count": hidden}


# ── CSV 내보내기 ────────────────────────────────────────────────────────
def csv_cell(value: Any) -> str:
    """CSV 칸 1개 — 수식 시작 문자로 시작하면 앞에 `'`(수식 주입 방지)."""
    out = "" if value is None else str(value)
    if out.startswith(_FORMULA_LEADS):
        return "'" + out
    return out


def export_csv(
    services: Iterable[Mapping[str, Any]],
    scope: Collection[str] | None,
    *,
    mask: Callable[[str], str] | None = None,
) -> str:
    """서비스(+구성원) → CSV 텍스트(UTF-8 BOM · 가져오기 템플릿 형식 · 존 범위 같음).

    보이는 활성 SMS 구성원 1대가 1행이다. 보이는 SMS 구성원이 없는 서비스(APM만)는 서버 칸이 빈
    1행이다. 보이는 구성원이 없는 서비스는 내보내지 않는다(목록과 같은 규칙).
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for svc in services:
        members = [m for m in svc.get("members") or [] if m.get("state") == "active"]
        shown = [m for m in members if member_visible(scope, m)]
        if scope is not None and not shown:
            continue
        owner = text(svc.get("owner_org"))
        if owner and mask is not None:
            owner = mask(owner)
        head = [svc.get("service_key"), svc.get("display_name"), svc.get("tier"), owner]
        servers = [m for m in shown if m.get("kind") == KIND_SMS_SERVER]
        if not servers:
            writer.writerow([csv_cell(v) for v in (*head, "", "", "", "")])
            continue
        for m in servers:
            writer.writerow([csv_cell(v) for v in (
                *head, m.get("db_id"), m.get("server_name"), m.get("hostname"), m.get("layer"),
            )])
    return BOM + buf.getvalue()


# ── 스냅샷 검색 표시(관리자) ────────────────────────────────────────────
def _owner(owners: Mapping[str, Mapping[str, Any]], key: str | None) -> dict[str, Any] | None:
    hit = owners.get(key) if key else None
    return dict(hit) if hit else None


def sms_search_keys(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """스냅샷 SMS 행 → 구성원 키 목록(만들 수 없는 행은 뺀다)."""
    return [k for r in rows
            if (k := sms_member_key(text(r.get("db_id")), text(r.get("resource_id"))))]


def sms_search_rows(
    rows: Iterable[Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    zone_of: Callable[[str], str],
) -> list[dict[str, Any]]:
    """스냅샷 SMS 검색 결과 행 — 표시 칸 + 구성원 키 + 현재 소속(없으면 None)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        db_id, rid = text(r.get("db_id")), text(r.get("resource_id"))
        key = sms_member_key(db_id, rid)
        out.append({"db_id": db_id, "resource_id": rid,
                    "server_name": text(r.get("server_name")) or None,
                    "hostname": text(r.get("hostname")) or None,
                    "ip": text(r.get("ip")) or None, "zone": zone_of(db_id) or None,
                    "member_key": key, "owner": _owner(owners, key)})
    return out


def apm_instance_key(row: Mapping[str, Any]) -> str | None:
    """스냅샷 APM 행 → 인스턴스(instance scope) 구성원 키(만들 수 없으면 None)."""
    return apm_instance_member_key(
        text(row.get("source_id")), text(row.get("domain_id")), SCOPE_INSTANCE,
        match_key_for(SCOPE_INSTANCE, text(row.get("instance_name"))),
    )


def apm_search_rows(
    rows: Iterable[Mapping[str, Any]], owners: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """스냅샷 APM 검색 결과 행 — 표시 칸 + 인스턴스 구성원 키 + 현재 소속."""
    out: list[dict[str, Any]] = []
    for r in rows:
        key = apm_instance_key(r)
        out.append({c: text(r.get(c)) or None for c in (
            "source_id", "domain_id", "domain_name", "instance_name", "ip", "host_name",
        )} | {"member_key": key, "owner": _owner(owners, key)})
    return out


# ── 미소속 서버(관리자 · 검토함) ────────────────────────────────────────
def unassigned_groups(
    sms_rows: Iterable[Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    zone_of: Callable[[str], str],
    prefix: HostPrefixConfig | None,
    *,
    limit: int = UNASSIGNED_LIMIT,
) -> dict[str, Any]:
    """스냅샷에 있는데 어느 서비스의 활성 구성원도 아닌 서버 → 존·접두 코드 묶음
    (서버 `limit`대까지).

    제외(excluded)·끊김 구성원으로만 남은 서버도 미소속으로 보이고 그 서비스를
    `excluded_from`에 적는다.
    접두 코드는 S6 설정이 켜졌을 때만 뽑는다(꺼지면 전부 「코드 없음」 묶음).
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    total = 0
    for server in sms_servers(sms_rows, zone_of):
        key = sms_member_key(server.db_id, server.resource_id)
        hit = owners.get(key) if key else None
        if hit and hit.get("state") == "active":
            continue
        total += 1
        if total > limit:
            continue
        code = ""
        if prefix is not None and prefix.enabled:
            code, _ = extract_prefix_code(server, prefix)
        groups[(server.zone, code)].append({
            "db_id": server.db_id, "resource_id": server.resource_id,
            "server_name": server.server_name or None, "hostname": server.hostname or None,
            "ip": server.ip or None, "member_key": key,
            "excluded_from": dict(hit) if hit else None,
        })
    ordered = [
        {"zone": z or None, "prefix_code": c or None, "count": len(v), "servers": v}
        for (z, c), v in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return {"groups": ordered, "total": total, "truncated": total > limit, "limit": limit}


# ── 변경 이력(관리자) ───────────────────────────────────────────────────
def _view_ids(view: Any) -> set[int]:
    """감사 before/after 안의 서비스 id — 서비스 모양(`service_key` 있음)의 `id`와 서비스 칸들."""
    if not isinstance(view, Mapping):
        return set()
    out: set[int] = set()
    keys = ["service_id", "other_service_id", "from_service_id", "to_service_id"]
    if "service_key" in view:
        keys.append("id")
    for k in keys:
        v = view.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out.add(v)
    return out


def history_rows(
    logs: Iterable[Mapping[str, Any]], service_id: int, *, limit: int = HISTORY_LIMIT
) -> list[dict[str, Any]]:
    """감사 기록(최근 순) → 이 서비스의 업무 서비스 관리 동작 `limit`건(before/after 그대로)."""
    out: list[dict[str, Any]] = []
    for log in logs:
        detail = log.get("detail")
        if not isinstance(detail, Mapping):
            continue
        nested = detail.get("extra")
        extra: Mapping[str, Any] = nested if isinstance(nested, Mapping) else detail
        action = text(extra.get("action"))
        if not action.startswith("biz_service"):
            continue
        ids = _view_ids(extra.get("before")) | _view_ids(extra.get("after")) | _view_ids(extra)
        into = extra.get("into_service_id")
        if isinstance(into, int):
            ids.add(into)
        if int(service_id) not in ids:
            continue
        out.append({"at": log.get("created_at"), "user_id": log.get("user_id"),
                    "action": action, "outcome": extra.get("outcome"),
                    "reason": extra.get("reason"), "batch_id": extra.get("batch_id"),
                    "before": extra.get("before"), "after": extra.get("after")})
        if len(out) >= limit:
            break
    return out


__all__ = [
    "BOM",
    "CSV_HEADER",
    "HISTORY_LIMIT",
    "SORT_KEYS",
    "STATUS_FILTERS",
    "UNASSIGNED_LIMIT",
    "analysis_services",
    "apm_instance_key",
    "apm_search_rows",
    "csv_cell",
    "export_csv",
    "history_rows",
    "link_graph",
    "list_rows",
    "member_visible",
    "review_reasons",
    "scoped_detail",
    "sms_search_keys",
    "sms_search_rows",
    "unassigned_groups",
]
