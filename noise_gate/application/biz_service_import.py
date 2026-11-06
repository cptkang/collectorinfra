"""업무 서비스 CSV 가져오기 — 파싱 · 행별 판정(미리보기) · 적용 (plans/215 §5.1 ⑤ · §6 · §9 · §14 ·
Q26 · D-368).

관리 라우트(`src/api/routes/admin_biz_service.py` `POST /import/preview`·`/import/apply`)가
부른다(API 프로세스 — D-049 PG 기록자). 인가·감사·사본 재구성은 라우트 몫이고, 여기는
파일 → 판정 → 적용 계획의 판단만 둔다.

- **파일**: UTF-8(BOM 허용) · ≤1MB · 데이터 행 ≤5,000 · 머리 행은 내보내기와 같은 템플릿
  (`biz_service_view.CSV_HEADER`) · 칸 길이 상한. 내보내기가 수식 주입 방지로 붙인 `'`는 떼고 읽는다
  (내보낸 파일을 그대로 다시 가져올 수 있게).
- **판정**(행마다 1개): 210 현재 스냅샷(`sms_inventory` — 한 번에 읽고 메모리에서 대조 · 서버 이름은
  SQL에 넣지 않는다)에서 같은 `db_id` 안의 서버명·hostname 정규형(`canonical_host_key`) 일치를 센다.
  1대 = 「추가」(다른 서비스 소속이면 「옮김」 · 이미 이 서비스면 「이미 소속」) · 0대 = 「스냅샷에
  없음」 · 2대 이상 = 「고르지 않음」(D-224 ④ — 고르지 않는다) · 레지스트리에 없는 `db_id` =
  「등록되지 않은 DB」 · 같은 서버가 파일 안에서 두 서비스에 = 해당 행 모두 「파일 안 충돌」
  (적용 안 함) · 담당 부서가 PII 패턴 = 「개인정보 의심」(값은 싣지 않는다).
- **서비스**: `service_key`로 기존 서비스를 찾는다. 없으면 새 서비스를 만든다(origin manual — 적용할
  행이나 서버 칸이 빈 「서비스만」 행이 있을 때만 · 표시명이 없으면 그 서비스 행 전부 거부). 기존
  서비스의 표시명·등급·담당 부서는 바꾸지 않는다(구성원만 — 차이는 `attr_note`로 알린다).
- **적용**: 미리보기 계획을 한 트랜잭션으로(`origin=manual` · `confidence=confirmed` · 계층을 주면
  `layer_origin=manual`). 미리보기 뒤 스냅샷 run이 바뀌면 409 `snapshot_changed`, 관계 서비스
  `version`이 바뀌면 저장소가 `BizServiceVersionConflict`(409)를 올린다.
- 미리보기 계획은 API 프로세스 메모리에 둔다(TTL · 개수 상한 · 만든 관리자만 적용 — W3 미리보기의
  `preview_not_found`와 같은 오류 코드).

계층: application. SQL·관측 DB 조회·LLM 0.
"""

from __future__ import annotations

import csv
import io
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.domain.host_names import canonical_host_key

from noise_gate.application.apm_host_map_scan import TABLE_SMS
from noise_gate.application.biz_service import registry_zone
from noise_gate.application.biz_service_admin import BizServiceAdminError
from noise_gate.application.biz_service_view import CSV_HEADER
from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER, sms_member_key
from noise_gate.domain.biz_service_model import text

#: 파일 크기(바이트) · 데이터 행 상한(§5.1 ⑤).
MAX_BYTES = 1_048_576
MAX_ROWS = 5_000
#: 미리보기 계획 보관(초) · 개수 상한(넘으면 오래된 것부터 버린다).
PREVIEW_TTL_SECONDS = 900
MAX_PREVIEWS = 8
#: 칸 길이 상한(DDL과 같다).
FIELD_LIMITS = {
    "service_key": 128, "display_name": 255, "tier": 1, "owner_org": 60, "db_id": 64,
    "server_name": 255, "hostname": 255, "layer": 16,
}
LAYERS = ("web", "was", "db", "batch", "etc")
#: 내보내기 `csv_cell`이 앞에 `'`를 붙이는 시작 문자(역변환용).
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

ST_ADD = "add"
ST_MOVE = "move"
ST_KEEP = "keep"
ST_SERVICE_ONLY = "service_only"
ST_NOT_FOUND = "not_found"
ST_AMBIGUOUS = "ambiguous"
ST_UNKNOWN_DB = "unknown_db"
ST_FILE_CONFLICT = "file_conflict"
ST_DUPLICATE = "duplicate"
ST_PII = "pii_rejected"
ST_INVALID = "invalid"
#: 적용하는 판정(구성원 쓰기).
APPLIED_STATUSES = (ST_ADD, ST_MOVE, ST_KEEP)
STATUS_LABELS = {
    ST_ADD: "추가", ST_MOVE: "옮김", ST_KEEP: "이미 소속", ST_SERVICE_ONLY: "서비스만",
    ST_NOT_FOUND: "스냅샷에 없음", ST_AMBIGUOUS: "고르지 않음", ST_UNKNOWN_DB: "등록되지 않은 DB",
    ST_FILE_CONFLICT: "파일 안 충돌", ST_DUPLICATE: "중복 행", ST_PII: "개인정보 의심",
    ST_INVALID: "입력 오류",
}
_MATCHED = "matched"


def _err(status: int, code: str, message: str, **detail: Any) -> BizServiceAdminError:
    return BizServiceAdminError(code, message, status=status, **detail)


# ── 파싱 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ImportRow:
    """CSV 데이터 행 1개 — `line`은 파일 줄 번호(머리 행 = 1) · `error`는 행 단위 형식 오류."""

    line: int
    values: dict[str, str]
    error: str | None = None


def _cell(value: str) -> str:
    """칸 다듬기 — 앞뒤 공백 제거 + 내보내기 수식 방지 `'` 떼기."""
    out = value.strip()
    if out.startswith("'") and out[1:].startswith(_FORMULA_LEADS):
        return out[1:]
    return out


def too_large_error() -> BizServiceAdminError:
    """413 `import_too_large` — 파일 판정과 수신 중 차단(라우트)이 같은 사유를 쓴다."""
    return _err(413, "import_too_large", f"파일은 {MAX_BYTES:,}바이트(1MB)까지입니다.",
                max_bytes=MAX_BYTES)


def parse_csv(data: bytes) -> list[ImportRow]:
    """CSV 바이트 → 데이터 행(빈 줄 건너뜀).

    Raises:
        BizServiceAdminError: 413 `import_too_large` · 400 `import_bad_encoding` ·
            `import_bad_csv` · `import_empty` · `import_bad_header` · `import_too_many_rows`
    """
    if len(data) > MAX_BYTES:
        raise too_large_error()
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _err(400, "import_bad_encoding",
                   "UTF-8 CSV만 받습니다(BOM 허용) — 엑셀은 「CSV UTF-8」로 저장하세요.") from None
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    rows: list[ImportRow] = []
    try:
        header = next(reader, None)
        if header is None:
            raise _err(400, "import_empty", "빈 파일입니다.")
        if [h.strip().lower() for h in header] != list(CSV_HEADER):
            raise _err(400, "import_bad_header",
                       "머리 행이 템플릿과 다릅니다 — " + ",".join(CSV_HEADER),
                       expected=list(CSV_HEADER))
        for raw in reader:
            if not any(c.strip() for c in raw):
                continue
            if len(rows) >= MAX_ROWS:
                raise _err(400, "import_too_many_rows", f"데이터 행은 {MAX_ROWS:,}개까지입니다.",
                           max_rows=MAX_ROWS)
            rows.append(_row(reader.line_num, raw))
    except csv.Error:
        raise _err(400, "import_bad_csv",
                   "CSV 형식을 읽지 못했습니다(따옴표·줄바꿈 확인).") from None
    if not rows:
        raise _err(400, "import_empty", "데이터 행이 없습니다.")
    return rows


def _row(line: int, raw: Sequence[str]) -> ImportRow:
    if len(raw) != len(CSV_HEADER):
        return ImportRow(line, {}, f"칸 수가 {len(raw)}개입니다({len(CSV_HEADER)}개여야 합니다).")
    values = {k: _cell(v) for k, v in zip(CSV_HEADER, raw, strict=True)}
    long = [k for k, v in values.items() if len(v) > FIELD_LIMITS[k]]
    if long:
        return ImportRow(line, values, "칸 길이 초과: " + ", ".join(long))
    return ImportRow(line, values)


# ── 판정 ────────────────────────────────────────────────────────────────
class _Store(Protocol):
    async def member_owners(self, kind: str, member_keys: Collection[str] | None = None
                            ) -> dict[str, dict[str, Any]]: ...

    async def services_brief(self, *, keys: Collection[str] = (),
                             ids: Collection[int] = ()) -> list[dict[str, Any]]: ...

    async def import_members(self, *, creates: Sequence[Mapping[str, Any]],
                             versions: Mapping[int, int], items: Sequence[Mapping[str, Any]],
                             by: str = "") -> dict[str, Any]: ...


class _Snapshot(Protocol):
    async def current_snapshot_run(self, table: str) -> str | None: ...

    async def snapshot_rows(self, table: str) -> list[dict[str, Any]]: ...


@dataclass
class ImportPlan:
    """미리보기 결과 — 화면 응답(`view`)과 적용 입력(생성 · version 기준 · 구성원)."""

    snapshot_run_id: str
    view: dict[str, Any]
    creates: list[dict[str, Any]] = field(default_factory=list)
    versions: dict[int, int] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)


def _host_index(snapshot: Sequence[Mapping[str, Any]]
                ) -> dict[tuple[str, str], dict[str, Mapping[str, Any]]]:
    """(db_id, 정규형) → resource_id → 스냅샷 행(서버명·hostname 둘 다 키)."""
    out: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for r in snapshot:
        db_id, rid = text(r.get("db_id")), text(r.get("resource_id"))
        if not db_id or not rid:
            continue
        for value in (r.get("server_name"), r.get("hostname")):
            key = canonical_host_key(text(value))
            if key:
                out[(db_id, key)][rid] = r
    return out


def _base(row: ImportRow) -> dict[str, Any]:
    v = row.values
    return {"line": row.line, "service_key": v.get("service_key", ""),
            "display_name": v.get("display_name", ""), "db_id": v.get("db_id", ""),
            "server_name": v.get("server_name", ""), "hostname": v.get("hostname", ""),
            "layer": (v.get("layer") or "").lower() or None, "status": None, "reason": None,
            "match_count": 0, "match": None, "member_key": None, "owner": None}


def _set(rec: dict[str, Any], status: str, reason: str | None = None) -> dict[str, Any]:
    rec["status"] = status
    rec["reason"] = reason
    return rec


def _tier(raw: str) -> int | None:
    if not raw:
        return None
    if raw in ("1", "2", "3"):
        return int(raw)
    raise ValueError(raw)


def match_rows(
    rows: Sequence[ImportRow],
    snapshot: Sequence[Mapping[str, Any]],
    *,
    known_db_ids: Collection[str],
    pii_rules: Callable[[str], list[str]],
    zone_of: Callable[[str], str] = registry_zone,
) -> list[dict[str, Any]]:
    """1단계 — 형식·PII·DB·스냅샷 일치·파일 안 충돌 판정(소속은 아직 모른다 → `matched`)."""
    index = _host_index(snapshot)
    known = set(known_db_ids)
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = _base(row)
        rec["_tier"] = None
        rec["_owner_org"] = row.values.get("owner_org") or None
        out.append(rec)
        if row.error:
            _set(rec, ST_INVALID, row.error)
            continue
        if not rec["service_key"]:
            _set(rec, ST_INVALID, "service_key가 비었습니다.")
            continue
        try:
            rec["_tier"] = _tier(row.values.get("tier", ""))
        except ValueError:
            _set(rec, ST_INVALID, "tier는 1·2·3 또는 빈 값입니다.")
            continue
        if rec["layer"] is not None and rec["layer"] not in LAYERS:
            _set(rec, ST_INVALID, "layer는 web·was·db·batch·etc 또는 빈 값입니다.")
            continue
        rules = pii_rules(rec["_owner_org"] or "")
        if rules:
            rec["_owner_org"] = None
            _set(rec, ST_PII, "담당 부서에 개인정보로 보이는 값(" + ", ".join(rules) + ")")
            continue
        if not rec["server_name"] and not rec["hostname"]:
            _set(rec, ST_SERVICE_ONLY)
            continue
        if not rec["db_id"]:
            _set(rec, ST_INVALID, "db_id가 비었습니다.")
            continue
        if rec["db_id"] not in known:
            _set(rec, ST_UNKNOWN_DB, "DB 레지스트리에 없는 db_id입니다.")
            continue
        keys = {canonical_host_key(rec["server_name"]), canonical_host_key(rec["hostname"])} - {""}
        if not keys:
            _set(rec, ST_INVALID, "서버명·hostname이 호스트 이름 형식이 아닙니다.")
            continue
        hits: dict[str, Mapping[str, Any]] = {}
        for key in keys:
            hits.update(index.get((rec["db_id"], key), {}))
        rec["match_count"] = len(hits)
        if not hits:
            _set(rec, ST_NOT_FOUND, "현재 스냅샷에 일치하는 서버가 없습니다.")
            continue
        if len(hits) > 1:
            _set(rec, ST_AMBIGUOUS, f"일치 서버 {len(hits)}대 — 고르지 않습니다.")
            continue
        rid, snap = next(iter(hits.items()))
        rec["member_key"] = sms_member_key(rec["db_id"], rid)
        rec["match"] = {"resource_id": rid, "server_name": text(snap.get("server_name")) or None,
                        "hostname": text(snap.get("hostname")) or None,
                        "ip": text(snap.get("ip")) or None}
        rec["_display"] = {"db_id": rec["db_id"], "resource_id": rid,
                           **{c: rec["match"][c] for c in ("server_name", "hostname", "ip")},
                           "zone": zone_of(rec["db_id"]) or None}
        _set(rec, _MATCHED)
    _mark_file_conflicts(out)
    return out


def _mark_file_conflicts(recs: Sequence[dict[str, Any]]) -> None:
    """같은 서버가 두 서비스 → 그 행 모두 「파일 안 충돌」 · 같은 서비스 반복 → 뒤 행 「중복 행」.
    """
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in recs:
        if rec["status"] == _MATCHED:
            by_key[rec["member_key"]].append(rec)
    for group in by_key.values():
        services = {r["service_key"] for r in group}
        if len(services) > 1:
            lines = ", ".join(str(r["line"]) for r in group)
            for r in group:
                _set(r, ST_FILE_CONFLICT,
                     f"같은 서버가 파일 안에서 두 서비스 이상에 있습니다(줄 {lines}).")
            continue
        for r in group[1:]:
            _set(r, ST_DUPLICATE, f"줄 {group[0]['line']}과 같은 서버입니다.")


def _first(recs: Sequence[Mapping[str, Any]], key: str) -> Any:
    return next((r[key] for r in recs if r.get(key) not in (None, "")), None)


def finalize(
    recs: list[dict[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    services: Mapping[str, Mapping[str, Any]],
    owner_services: Mapping[int, Mapping[str, Any]],
    snapshot_run_id: str,
) -> ImportPlan:
    """2단계 — 소속(추가·옮김·이미 소속) · 새 서비스 · version 기준 · 화면 요약."""
    by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in recs:
        if rec["service_key"] and rec["status"] not in (ST_INVALID, ST_PII):
            by_service[rec["service_key"]].append(rec)
    plan = ImportPlan(snapshot_run_id, {})
    summaries: list[dict[str, Any]] = []
    for key, group in by_service.items():
        existing = services.get(key)
        if existing is None and not _first(group, "display_name"):
            for r in group:
                _set(r, ST_INVALID, "새 서비스인데 표시명(display_name)이 없습니다.")
            continue
        target = int(existing["id"]) if existing is not None else None
        for r in group:
            if r["status"] != _MATCHED:
                continue
            owner = owners.get(r["member_key"])
            if owner is None:
                _set(r, ST_ADD)
                continue
            oid = int(owner["service_id"])
            r["owner"] = {"service_id": oid, "service_name": owner.get("service_name"),
                          "state": owner.get("state")}
            if oid == target:
                _set(r, ST_KEEP, None if owner.get("state") == "active" else "비활성 구성원 → 활성")
            else:
                _set(r, ST_MOVE, f"「{owner.get('service_name') or oid}」에서 옮깁니다.")
        applied = [r for r in group if r["status"] in APPLIED_STATUSES]
        wanted = applied or any(r["status"] == ST_SERVICE_ONLY for r in group)
        summary = _service_summary(key, group, existing, applied)
        summaries.append(summary)
        if existing is None and wanted:
            plan.creates.append({"service_key": key, "display_name": summary["display_name"],
                                 "tier": _first(group, "_tier"),
                                 "owner_org": _first(group, "_owner_org")})
        elif target is not None and existing is not None and applied:
            plan.versions[target] = int(existing.get("version") or 0)
        for r in applied:
            from_id: int | None = r["owner"]["service_id"] if r["owner"] else None
            if from_id is not None and from_id != target:
                plan.versions[from_id] = int(
                    (owner_services.get(from_id) or {}).get("version") or 0)
            plan.items.append({"line": r["line"], "service_key": key, "service_id": target,
                               "kind": KIND_SMS_SERVER, "member_key": r["member_key"],
                               "display": r["_display"], "layer": r["layer"],
                               "owner_id": from_id})
    plan.view = _view(recs, summaries, plan)
    return plan


def _service_summary(key: str, group: Sequence[Mapping[str, Any]],
                     existing: Mapping[str, Any] | None,
                     applied: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(r["status"] for r in applied)
    if existing is not None:
        name = existing.get("display_name")
        differs = any(
            (r["display_name"] and r["display_name"] != existing.get("display_name"))
            or (r["_tier"] is not None and r["_tier"] != existing.get("tier"))
            or (r["_owner_org"] and r["_owner_org"] != existing.get("owner_org"))
            for r in group)
        note = "기존 서비스 — 표시명·등급·담당 부서는 바꾸지 않습니다." if differs else None
    else:
        name = _first(group, "display_name")
        distinct = {(r["display_name"], r["_tier"], r["_owner_org"]) for r in group
                    if r["display_name"] or r["_tier"] is not None or r["_owner_org"]}
        note = "행마다 서비스 속성이 달라 처음 값을 씁니다." if len(distinct) > 1 else None
    return {"service_key": key, "service_id": existing.get("id") if existing else None,
            "display_name": name, "is_new": existing is None,
            "state": existing.get("state") if existing else None,
            "add": counts[ST_ADD], "move_in": counts[ST_MOVE], "keep": counts[ST_KEEP],
            "attr_note": note}


def _view(recs: Sequence[Mapping[str, Any]], summaries: list[dict[str, Any]],
          plan: ImportPlan) -> dict[str, Any]:
    moves: Counter[tuple[int, str, str]] = Counter()
    for r in recs:
        if r["status"] == ST_MOVE and r["owner"]:
            moves[(r["owner"]["service_id"], str(r["owner"]["service_name"] or ""),
                   r["service_key"])] += 1
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} | {
        "label": STATUS_LABELS.get(r["status"], r["status"])} for r in recs]
    return {
        "snapshot_run_id": plan.snapshot_run_id, "rows": rows,
        "counts": {s: sum(1 for r in recs if r["status"] == s) for s in STATUS_LABELS},
        "services": summaries,
        "moves": [{"from_service_id": f, "from_service_name": n, "to_service_key": t, "count": c}
                  for (f, n, t), c in sorted(moves.items())],
        "creates": len(plan.creates), "applicable": len(plan.items),
        "labels": dict(STATUS_LABELS),
    }


async def build_preview(
    rows: Sequence[ImportRow],
    *,
    repo: _Store,
    maps: _Snapshot,
    known_db_ids: Collection[str],
    pii_rules: Callable[[str], list[str]],
    zone_of: Callable[[str], str] = registry_zone,
) -> ImportPlan:
    """파싱된 행 → 미리보기 계획(쓰기 0 — 스냅샷 1회 · 소속 1회 · 서비스 1회 읽기).

    Raises:
        BizServiceAdminError: 409 `snapshot_missing`(210 현재 스냅샷 없음)
    """
    run_id = await maps.current_snapshot_run(TABLE_SMS)
    if run_id is None:
        raise _err(409, "snapshot_missing",
                   "사전 스캔 스냅샷이 없습니다 — 「APM·SMS 연계」 탭에서 스캔을 먼저 실행하세요.")
    snapshot = await maps.snapshot_rows(TABLE_SMS)
    recs = match_rows(rows, snapshot, known_db_ids=known_db_ids, pii_rules=pii_rules,
                      zone_of=zone_of)
    keys = sorted({r["member_key"] for r in recs if r["status"] == _MATCHED})
    owners = await repo.member_owners(KIND_SMS_SERVER, keys) if keys else {}
    service_keys = sorted({r["service_key"] for r in recs if r["service_key"]})
    owner_ids = sorted({int(o["service_id"]) for o in owners.values()})
    brief = await repo.services_brief(keys=service_keys, ids=owner_ids) if (
        service_keys or owner_ids) else []
    by_key = {str(s["service_key"]): s for s in brief}
    by_id = {int(s["id"]): s for s in brief}
    return finalize(recs, owners, by_key, by_id, run_id)


async def apply_import(plan: ImportPlan, *, repo: _Store, maps: _Snapshot, by: str
                       ) -> dict[str, Any]:
    """미리보기 계획 적용(한 트랜잭션 — 저장소).

    Raises:
        BizServiceAdminError: 409 `snapshot_changed` · 400 `import_nothing`
        BizServiceVersionConflict · BizServiceExistsError: 저장소(관계 서비스가 바뀌었다)
    """
    current = await maps.current_snapshot_run(TABLE_SMS)
    if current != plan.snapshot_run_id:
        raise _err(409, "snapshot_changed",
                   "미리보기 뒤 스냅샷이 바뀌었습니다 — 다시 미리보기 하세요.",
                   changed=["snapshots"])
    if not plan.items and not plan.creates:
        raise _err(400, "import_nothing", "적용할 행이 없습니다.")
    return await repo.import_members(creates=plan.creates, versions=plan.versions,
                                     items=plan.items, by=by)


# ── 미리보기 보관 ───────────────────────────────────────────────────────
class ImportPreviewStore:
    """미리보기 계획 메모리 보관 — TTL · 개수 상한 · 만든 관리자만 꺼낸다."""

    def __init__(self, *, ttl_seconds: int = PREVIEW_TTL_SECONDS, max_entries: int = MAX_PREVIEWS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """보관 시간·개수·시계(테스트 주입)를 받는다."""
        self.ttl_seconds = int(ttl_seconds)
        self._max = max(1, int(max_entries))
        self._clock = clock
        self._items: dict[str, tuple[float, str, ImportPlan]] = {}

    def _sweep(self) -> None:
        now = self._clock()
        for pid in [p for p, (at, _, _) in self._items.items() if now - at > self.ttl_seconds]:
            del self._items[pid]

    def put(self, by: str, plan: ImportPlan) -> str:
        """계획 보관 → `preview_id`."""
        self._sweep()
        while len(self._items) >= self._max:
            del self._items[next(iter(self._items))]
        pid = uuid.uuid4().hex
        self._items[pid] = (self._clock(), by, plan)
        return pid

    def get(self, preview_id: str, by: str) -> ImportPlan:
        """계획 꺼내기(지우지 않는다).

        Raises:
            BizServiceAdminError: 404 `preview_not_found`(없음 · 만료 · 다른 관리자)
        """
        self._sweep()
        hit = self._items.get(preview_id)
        if hit is None or hit[1] != by:
            raise _err(404, "preview_not_found",
                       "미리보기가 없거나 만료됐습니다 — 파일을 다시 미리보기 하세요.",
                       preview_id=preview_id)
        return hit[2]

    def drop(self, preview_id: str) -> None:
        """적용 뒤 지운다(같은 미리보기를 두 번 적용하지 않는다)."""
        self._items.pop(preview_id, None)


__all__ = [
    "APPLIED_STATUSES",
    "FIELD_LIMITS",
    "MAX_BYTES",
    "MAX_ROWS",
    "PREVIEW_TTL_SECONDS",
    "STATUS_LABELS",
    "ImportPlan",
    "ImportPreviewStore",
    "ImportRow",
    "apply_import",
    "build_preview",
    "finalize",
    "match_rows",
    "parse_csv",
    "too_large_error",
]
