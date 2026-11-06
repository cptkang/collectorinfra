"""업무 서비스 PG 정본 저장소 (plans/215 §3.5 · §3.6 · §4.5 · D-368).

`biz_service`(서비스) · `biz_service_member`(구성원) · `biz_service_candidate`(후보) ·
`biz_service_run`(계산 실행 기록) · `biz_service_attr`(보강 속성 · run별) ·
`biz_service_state_log`(상태 전이 기록)을 asyncpg 풀(auth 풀)로 읽고 쓴다. DDL은
`ddl/biz_service.sql`(기동 시 `src/api/server.py` `_ensure_biz_service_tables`).

- 기록자는 API 프로세스뿐이다(D-049). 행은 dict로 주고받는다. (W3) 계산 결과의 적용
  `apply_ops`만 도메인 적용 연산 목록(`ApplyOps`)을 받아 한 트랜잭션으로 실행한다.
- 서비스 수정·구성원 동작은 서비스 `version` 낙관적 잠금 — 불일치는 `BizServiceVersionConflict`
  (라우트가 409). 구성원 동작은 서비스 `version`을 올린다(가져오기 미리보기 뒤 변경 감지).
- 구성원은 UNIQUE(kind, member_key) — SMS 서버 1대는 서비스 1개. 중복 추가는
  `BizServiceMemberExistsError`. 옮기기는 두 서비스를 id 순으로 잠그는 한 트랜잭션이다.
- 일괄 동작은 한 번에 `MAX_BULK`(200)건까지 — 넘으면 ValueError.
- (W4) 병합·분할·member 후보 수락은 각각 한 트랜잭션이다(옮긴 구성원은 `origin=manual`).
- (W5) P1 조회 보조(조건 검색 · 구성원 묶음 통계 · 구성원 키 소속 · 담당 부점)와 일괄 지정
  (`assign_members` — 추가·옮기기를 한 트랜잭션).
- (W5b) CSV 가져오기 — 키·id로 서비스 칸 읽기(`services_brief`) · 적용(`import_members` — 새 서비스
  생성 · 관계 서비스 version 확인 · 구성원 추가·옮기기를 한 트랜잭션).
- SQL 값은 전부 파라미터 바인딩이다. 식별자(갱신 칼럼·표시 칼럼)는 이 모듈의 고정 목록에서만 고른다.
- 관리 동작의 DB 오류는 삼키지 않고 올린다(호출부가 응답에 사유를 싣는다).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from noise_gate.domain.biz_service_apply import (
    OP_DELETE,
    OP_INSERT,
    OP_MOVE,
    OP_PURGE,
    OP_RESTORE,
    OP_SKIP,
    OP_SUGGEST,
    OP_TARGET_GONE,
    OP_UPDATE,
    ApplyOps,
    MemberOp,
    ServiceOp,
)
from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER, KINDS, parse_member_key
from noise_gate.domain.biz_service_model import CAND_MERGE, Candidate

logger = logging.getLogger(__name__)

ORIGIN_AUTO = "auto"
ORIGIN_MANUAL = "manual"
SERVICE_STATES = ("active", "ambiguous", "disabled")
#: 관리자가 지정할 수 있는 서비스 상태(ambiguous는 계산만 낸다).
ADMIN_SERVICE_STATES = ("active", "disabled")
MEMBER_ACTIVE = "active"
MEMBER_EXCLUDED = "excluded"
MEMBER_TARGET_GONE = "target_gone"
MEMBER_STATES = (MEMBER_ACTIVE, MEMBER_EXCLUDED, MEMBER_TARGET_GONE)
CONFIDENCE_CONFIRMED = "confirmed"
CONFIDENCE_INFERRED = "inferred"
LAYERS = ("web", "was", "db", "batch", "etc")
BASIS_MANUAL = "manual"
CANDIDATE_KINDS = ("member", "merge", "apm_unlinked")
CANDIDATE_STATUSES = ("open", "accepted", "rejected")
RUN_MODES = ("preview", "apply")
RUN_RUNNING = "running"
RUN_ABORTED = "aborted"
RUN_FINISHED_STATUSES = ("done", "failed", RUN_ABORTED)

#: 목록 페이지 상한.
MAX_LIST_LIMIT = 100
#: 일괄 동작 상한(구성원·후보).
MAX_BULK = 200
#: (W5b) CSV 가져오기 적용 상한(구성원 · 파일 데이터 행 상한과 같다).
IMPORT_MAX = 5000
#: (W5) P1 목록 계산이 한 번에 읽는 서비스 상한(존 범위·정렬은 호출부가 이 안에서 한다).
SEARCH_CAP = 5000
#: 실행 기록·상태 전이 보존(일) · target_gone 자동 구성원 보존(일) · 보강 속성 보존 run 수.
RUN_RETENTION_DAYS = 90
STATE_LOG_RETENTION_DAYS = 90
TARGET_GONE_RETENTION_DAYS = 180
ATTR_KEEP_RUNS = 3
#: 서비스 표시 텍스트 상한(DDL과 같다).
SERVICE_KEY_MAX = 128
DISPLAY_NAME_MAX = 255
DESCRIPTION_MAX = 500
OWNER_ORG_MAX = 60
#: 후보 정규 키 상한(DDL `candidate_key` VARCHAR(512)).
CANDIDATE_KEY_MAX = 512

#: 관리자 수정이 바꿀 수 있는 서비스 칼럼(식별자는 여기서만 고른다).
_SERVICE_UPDATABLE = (
    "display_name", "tier", "description", "owner_org", "note", "state", "monitored", "verified",
)
#: 구성원 표시 칼럼(추가 시 받는다 · 식별자는 여기서만 고른다).
MEMBER_DISPLAY_COLUMNS = (
    "db_id", "resource_id", "server_name", "hostname", "ip", "zone",
    "source_id", "domain_id", "domain_name", "instance_name", "prefix_code",
)
#: 보강 속성 적재 칼럼(run_id 제외 · 담당자 개인 칸 없음).
ATTR_COLUMNS = (
    "db_id", "resource_id", "tree_group_id", "tree_group_name", "tree_code", "registered_at",
    "depends", "contract_no", "contract_name", "maint_contract_no", "owner_branch",
)
_JSON_DICT_COLUMNS = ("evidence", "summary", "reason")
_JSON_LIST_COLUMNS = ("signals", "depends")


class BizServiceVersionConflict(Exception):  # noqa: N818 — 위임 계약 이름
    """서비스 `version` 불일치(낙관적 잠금 — 라우트가 409)."""

    def __init__(self, service_id: int, current_version: int) -> None:
        """대상 서비스 id와 현재 version을 싣는다."""
        super().__init__(f"biz_service {service_id} version 불일치(현재 {current_version})")
        self.service_id = service_id
        self.current_version = current_version


class BizServiceExistsError(Exception):
    """같은 `service_key`가 이미 있다."""

    def __init__(self, existing_id: int) -> None:
        """이미 있는 서비스 id를 싣는다."""
        super().__init__(f"biz_service 키 중복(기존 id {existing_id})")
        self.existing_id = existing_id


class BizServiceMemberExistsError(Exception):
    """같은 (kind, member_key) 구성원이 이미 있다 — SMS 서버는 서비스 1개에만 속한다."""

    def __init__(self, member_id: int, service_id: int) -> None:
        """이미 있는 구성원 id와 그 소속 서비스 id를 싣는다."""
        super().__init__(f"biz_service_member 중복(구성원 {member_id} · 서비스 {service_id})")
        self.member_id = member_id
        self.service_id = service_id


class BizServiceCandidateNotOpen(Exception):  # noqa: N818 — 다른 예외 이름과 같은 꼴
    """(W4) 후보가 열려 있지 않다(이미 수락·거절됨) — 라우트가 409."""

    def __init__(self, candidate_id: int, status: str) -> None:
        """대상 후보 id와 현재 상태를 싣는다."""
        super().__init__(f"biz_service_candidate {candidate_id} 상태 {status}")
        self.candidate_id = candidate_id
        self.status = status


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _json_load(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _row(row: Any) -> dict[str, Any]:
    """asyncpg Record → dict(JSONB는 파싱 · 시각은 ISO8601 문자열)."""
    out = {k: _iso(v) for k, v in dict(row).items()}
    for key in _JSON_DICT_COLUMNS:
        if key in out:
            value = _json_load(out[key], {})
            out[key] = value if isinstance(value, dict) else {}
    for key in _JSON_LIST_COLUMNS:
        if key in out:
            value = _json_load(out[key], [])
            out[key] = value if isinstance(value, list) else []
    return out


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _count(status: Any) -> int:
    """`UPDATE n`·`DELETE n` 상태 문자열 → n."""
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _ids(values: Collection[int]) -> list[int]:
    """일괄 대상 id 정리(중복 제거·순서 보존) — 비었거나 `MAX_BULK`를 넘으면 ValueError."""
    out = list(dict.fromkeys(int(v) for v in values))
    if not out:
        raise ValueError("일괄 대상이 비었다")
    if len(out) > MAX_BULK:
        raise ValueError(f"일괄 동작은 한 번에 {MAX_BULK}건까지")
    return out


def _page(limit: int, cap: int = MAX_LIST_LIMIT) -> int:
    return max(1, min(int(limit), cap))


def _check_text(name: str, value: Any, limit: int, *, required: bool = False) -> None:
    if value is None:
        if required:
            raise ValueError(f"{name}은(는) 비울 수 없다")
        return
    if not isinstance(value, str):
        raise ValueError(f"{name}은(는) 문자열이어야 한다")
    if required and not value.strip():
        raise ValueError(f"{name}은(는) 비울 수 없다")
    if len(value) > limit:
        raise ValueError(f"{name}은(는) {limit}자 이하")


def _check_tier(tier: Any) -> None:
    if tier is None:
        return
    if isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= 3:
        raise ValueError("tier는 1~3 또는 미지정(None)")


def candidate_key(kind: str, service_key: str, target_kind: str, target_key: str) -> str:
    """후보 정규 키(결정적) — `{kind}|{service_key}|{target_kind}|{target_key}`.

    거절 후보를 기억하는 UNIQUE 칼럼 값이다. 칸 상한(512자)을 넘으면 같은 문자열의 sha256으로
    줄인다(`{kind}|sha256:{hex}`).
    """
    raw = "|".join((kind, service_key, target_kind, target_key))
    if len(raw) <= CANDIDATE_KEY_MAX:
        return raw
    return f"{kind}|sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _member_display(op: MemberOp) -> tuple[list[str], list[Any]]:
    """자동 구성원 표시 칼럼(고정 목록만) — SMS는 정규 키에서 db_id·resource_id를 채운다."""
    shown = {k: v for k, v in dict(op.display).items() if k in MEMBER_DISPLAY_COLUMNS}
    if op.kind == KIND_SMS_SERVER:
        parts = parse_member_key(op.kind, op.member_key)
        if parts is not None:
            shown["db_id"], shown["resource_id"] = parts[0], parts[1]
    if op.prefix_code:
        shown["prefix_code"] = op.prefix_code
    columns = [c for c in MEMBER_DISPLAY_COLUMNS if c in shown]
    return columns, [None if shown[c] is None else str(shown[c]) for c in columns]


def _service_where(
    *,
    zone: str | None,
    tier: int | None,
    state: str | None,
    monitored: bool | None,
    query: str | None,
    member_zones: Collection[str] | None = None,
) -> tuple[str, list[Any]]:
    """서비스 목록 조건(`s` 별칭) → `(WHERE 절, 바인드 값)` — 값은 전부 바인드한다.

    `member_zones`(W5 · 존 범위 사용자)를 주면 검색어의 구성원 서버명·hostname 일치를 그 존
    구성원으로만 한정한다(존 밖 호스트 이름으로 소속 서비스를 알아내는 경로 차단 · `zone` 칸이 빈
    구성원은 맞추지 않는다). None이면 종전과 같다.
    """
    where: list[str] = []
    args: list[Any] = []
    if state:
        args.append(state)
        where.append(f"s.state = ${len(args)}")
    if tier is not None:
        args.append(int(tier))
        where.append(f"s.tier = ${len(args)}")
    if monitored is not None:
        args.append(bool(monitored))
        where.append(f"s.monitored = ${len(args)}")
    if zone:
        args.append(zone)
        where.append(
            "EXISTS (SELECT 1 FROM biz_service_member z WHERE z.service_id = s.id"
            f" AND z.kind = 'sms_server' AND z.state = 'active' AND z.zone = ${len(args)})"
        )
    if query and query.strip():
        args.append(_like(query.strip()))
        n = len(args)
        zone_cond = ""
        if member_zones is not None:
            args.append(sorted({str(z) for z in member_zones if z}))
            zone_cond = f" AND q.zone = ANY(${len(args)}::text[])"
        where.append(
            f"(s.display_name ILIKE ${n} OR s.service_key ILIKE ${n}"
            " OR EXISTS (SELECT 1 FROM biz_service_member q WHERE q.service_id = s.id"
            f"{zone_cond} AND (q.server_name ILIKE ${n} OR q.hostname ILIKE ${n})))"
        )
    return (f"WHERE {' AND '.join(where)}" if where else ""), args


def _attr_value(column: str, value: Any) -> Any:
    if column == "depends":
        return _dumps(list(value or []))
    if column == "registered_at":
        if isinstance(value, str):
            return datetime.fromisoformat(value) if value else None
        return value
    return None if value is None else str(value)


class PostgresBizServiceRepository:
    """PostgreSQL 업무 서비스 저장소."""

    def __init__(self, pool: Any) -> None:  # asyncpg.Pool (덕 타이핑)
        """asyncpg 풀(auth 풀)을 받는다."""
        self._pool = pool

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """연결 1개를 잡고 트랜잭션을 연다 — 블록 안의 연결로 여러 쓰기를 원자적으로 묶는다.

        예외가 나면 롤백된다. (W3) 적용 연산 실행 메서드가 이 위에서 돈다.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    # ── 서비스 조회 ─────────────────────────────────────────────────────
    async def list_services(
        self,
        *,
        zone: str | None = None,
        tier: int | None = None,
        state: str | None = None,
        monitored: bool | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """목록(tier 오름차순 · 미지정 뒤 · 표시명 순)과 전체 건수.

        존은 그 존의 활성 SMS 구성원이 있는 서비스 · 검색어는 표시명·서비스 키·구성원 서버명·
        hostname(LIKE 이스케이프 · 바인드). 행마다 활성 구성원 수 `sms_count`·`apm_count`를 단다.
        """
        clause, args = _service_where(
            zone=zone, tier=tier, state=state, monitored=monitored, query=query
        )
        page = _page(limit)
        start = max(0, int(offset))
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(f"SELECT COUNT(*) FROM biz_service s {clause}", *args)
            rows = await conn.fetch(
                "SELECT s.*,"
                " (SELECT COUNT(*) FROM biz_service_member m WHERE m.service_id = s.id"
                "  AND m.kind = 'sms_server' AND m.state = 'active') AS sms_count,"
                " (SELECT COUNT(*) FROM biz_service_member m WHERE m.service_id = s.id"
                "  AND m.kind <> 'sms_server' AND m.state = 'active') AS apm_count"
                f" FROM biz_service s {clause}"
                " ORDER BY s.tier ASC NULLS LAST, s.display_name ASC, s.id ASC"
                f" LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
                *args, page, start,
            )
        return [_row(r) for r in rows], int(total or 0)

    async def get_service(self, service_id: int) -> dict[str, Any] | None:
        """단건 + 구성원 전체(`members` · 종류·id 순 · 없으면 None)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM biz_service WHERE id = $1", int(service_id))
            if row is None:
                return None
            members = await conn.fetch(
                "SELECT * FROM biz_service_member WHERE service_id = $1 ORDER BY kind, id",
                int(service_id),
            )
        out = _row(row)
        out["members"] = [_row(m) for m in members]
        return out

    # ── (W5) P1 조회 · 편집 보조 ────────────────────────────────────────
    async def search_services(
        self,
        *,
        tier: int | None = None,
        state: str | None = None,
        monitored: bool | None = None,
        query: str | None = None,
        member_zones: Collection[str] | None = None,
        cap: int = SEARCH_CAP,
    ) -> list[dict[str, Any]]:
        """조건에 맞는 서비스 전체(구성원 없이 · 목록 순서 · 상한 `cap`) — P1 존 범위·정렬용.

        존 범위·커버리지 필터와 정렬은 구성원 통계가 있어야 하므로 호출부가 이 결과 위에서 한다.
        `member_zones`는 요청자 존 범위다 — 검색어의 구성원 이름 일치를 그 존 구성원으로 한정한다
        (None = 전 존).
        """
        clause, args = _service_where(
            zone=None, tier=tier, state=state, monitored=monitored, query=query,
            member_zones=member_zones,
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT s.* FROM biz_service s {clause}"
                " ORDER BY s.tier ASC NULLS LAST, s.display_name ASC, s.id ASC"
                f" LIMIT ${len(args) + 1}",
                *args, max(1, int(cap)),
            )
        return [_row(r) for r in rows]

    async def member_stats(self) -> list[dict[str, Any]]:
        """활성 구성원 묶음 통계 — `{service_id, kind, zone, db_id, source_id, confidence, n}` 행.

        존 칩·추정 비율·존 범위(보이는 구성원 유무) 계산 재료다(이름은 싣지 않는다).
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT service_id, kind, zone, db_id, source_id, confidence, COUNT(*) AS n"
                " FROM biz_service_member WHERE state = 'active'"
                " GROUP BY service_id, kind, zone, db_id, source_id, confidence"
            )
        return [_row(r) for r in rows]

    async def member_owners(
        self, kind: str, member_keys: Collection[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """구성원 키 → 소속 `{member_id, service_id, service_name, state}`(전 상태).

        `member_keys`를 안 주면 그 종류 전체(미소속 서버 계산용)다.
        """
        if kind not in KINDS:
            raise ValueError(f"구성원 종류 오류: {kind!r}")
        sql = (
            "SELECT m.member_key, m.id, m.service_id, m.state, s.display_name"
            " FROM biz_service_member m JOIN biz_service s ON s.id = m.service_id"
            " WHERE m.kind = $1"
        )
        args: list[Any] = [kind]
        if member_keys is not None:
            keys = sorted({str(k) for k in member_keys if k})
            if not keys:
                return {}
            args.append(keys)
            sql += " AND m.member_key = ANY($2::text[])"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return {
            str(r["member_key"]): {"member_id": int(r["id"]), "service_id": int(r["service_id"]),
                                   "service_name": r["display_name"], "state": r["state"]}
            for r in rows
        }

    async def assign_members(
        self,
        service_id: int,
        items: Sequence[tuple[str, str, Mapping[str, Any]]],
        *,
        version: int | None = None,
        by: str = "",
    ) -> list[dict[str, Any]]:
        """(W5) 일괄 지정 — 구성원 `(kind, member_key, 표시 칸)` ≤ `MAX_BULK`건을 이 서비스로
        (한 트랜잭션).

        없으면 추가(`origin=manual`·`basis=manual`·`confirmed`·`active`), 다른 서비스에 있으면
        옮기고(`evidence.moved_from`), 이미 이 서비스면 수동 확정·활성으로 둔다. 관계된 서비스
        version을 id 순으로 올린다(대상 서비스는 `version` 확인). → 행마다
        `{member, outcome, moved_from}`
        (`outcome` = added·moved·kept).

        Raises:
            ValueError: 비었거나 상한 초과 · 종류·키 규칙 밖 · 표시 칼럼 밖
            LookupError: 서비스 없음
            BizServiceVersionConflict: 대상 서비스 `version` 불일치
        """
        rows = list(items)
        if not rows:
            raise ValueError("일괄 대상이 비었다")
        if len(rows) > MAX_BULK:
            raise ValueError(f"일괄 동작은 한 번에 {MAX_BULK}건까지")
        for kind, key, display in rows:
            if kind not in KINDS or parse_member_key(kind, key) is None:
                raise ValueError("구성원 종류·키 규칙 밖")
            if set(display) - set(MEMBER_DISPLAY_COLUMNS):
                raise ValueError("표시 칼럼 밖")
        sid = int(service_id)
        out: list[dict[str, Any]] = []
        async with self.transaction() as conn:
            existing: dict[tuple[str, str], dict[str, Any]] = {}
            for kind, key, _display in rows:
                rec = await conn.fetchrow(
                    "SELECT id, service_id FROM biz_service_member"
                    " WHERE kind = $1 AND member_key = $2 FOR UPDATE",
                    kind, key,
                )
                if rec is not None:
                    existing[(kind, key)] = dict(rec)
            others = {int(e["service_id"]) for e in existing.values()} - {sid}
            for s in sorted({sid} | others):
                await self._bump_service(conn, s, version if s == sid else None, by)
            for kind, key, display in rows:
                hit = existing.get((kind, key))
                if hit is None:
                    columns = [c for c in MEMBER_DISPLAY_COLUMNS if c in display]
                    values = [None if display[c] is None else str(display[c]) for c in columns]
                    names = ["service_id", "kind", "member_key", "origin", "basis",
                             "confidence", "state", "created_by", "updated_by", *columns]
                    marks = ", ".join(f"${i}" for i in range(1, len(names) + 1))
                    row = await conn.fetchrow(
                        f"INSERT INTO biz_service_member ({', '.join(names)})"
                        f" VALUES ({marks}) RETURNING *",
                        sid, kind, key, ORIGIN_MANUAL, BASIS_MANUAL, CONFIDENCE_CONFIRMED,
                        MEMBER_ACTIVE, by, by, *values,
                    )
                    existing[(kind, key)] = {"id": row["id"], "service_id": sid}
                    out.append({"member": _row(row), "outcome": "added", "moved_from": None})
                    continue
                old = int(hit["service_id"])
                args: list[Any] = [int(hit["id"]), sid, ORIGIN_MANUAL, CONFIDENCE_CONFIRMED, by]
                moved = ""
                if old != sid:
                    args.append(old)
                    moved = ", evidence = evidence || jsonb_build_object('moved_from', $6::bigint)"
                row = await conn.fetchrow(
                    "UPDATE biz_service_member SET service_id = $2, origin = $3,"
                    f" confidence = $4, state = 'active', gone_at = NULL{moved},"
                    " updated_by = $5, updated_at = NOW() WHERE id = $1 RETURNING *",
                    *args,
                )
                hit["service_id"] = sid
                out.append({"member": _row(row), "outcome": "moved" if old != sid else "kept",
                            "moved_from": old if old != sid else None})
        return out

    async def services_brief(
        self, *, keys: Collection[str] = (), ids: Collection[int] = ()
    ) -> list[dict[str, Any]]:
        """(W5b) 서비스 키(정확히 일치) 또는 id로 찾은 서비스 칸 — 구성원 없이(가져오기 판정용)."""
        key_list = sorted({str(k) for k in keys if k})
        id_list = sorted({int(i) for i in ids})
        if not key_list and not id_list:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, service_key, display_name, tier, owner_org, state, version"
                " FROM biz_service"
                " WHERE service_key = ANY($1::text[]) OR id = ANY($2::bigint[]) ORDER BY id",
                key_list, id_list,
            )
        return [_row(r) for r in rows]

    async def import_members(
        self,
        *,
        creates: Sequence[Mapping[str, Any]],
        versions: Mapping[int, int],
        items: Sequence[Mapping[str, Any]],
        by: str = "",
    ) -> dict[str, Any]:
        """(W5b) CSV 가져오기 적용 — 새 서비스 생성 · 관계 서비스 version 확인 · 구성원
        추가·옮기기를 **한 트랜잭션**으로.

        - `creates`: `{service_key, display_name, tier, owner_org}`(origin manual · active).
        - `versions`: 기존 서비스 id → 미리보기 때 version(대상 + 옮겨 나가는 쪽 · id 순으로
          잠근다).
        - `items`: `{line, service_key, service_id(새 서비스면 None), kind, member_key, display,
          layer, owner_id(미리보기 때 소속 — 없으면 None)}` ≤ `IMPORT_MAX`건. 소속이 미리보기와
          다르면 그 서비스의 version 불일치로 올린다. 추가·옮기기 모두 `origin=manual` ·
          `confirmed` · `active` · 계층을 주면 `layer_origin=manual`.

        → `{created[서비스 행], members[{line, service_key, member, outcome, moved_from}]}`
        (`outcome` = added·moved·kept).

        Raises:
            ValueError: 상한 초과 · 종류·키 규칙 밖 · 표시 칼럼 밖 · 계층 값 · 서비스 칸 규칙 밖
            LookupError: 기존 서비스 없음
            BizServiceVersionConflict: 관계 서비스 version 불일치 · 소속이 미리보기와 다르다
            BizServiceExistsError: 새 서비스 키가 그 사이 생겼다
        """
        rows = list(items)
        if len(rows) > IMPORT_MAX:
            raise ValueError(f"가져오기는 한 번에 {IMPORT_MAX}건까지")
        for it in rows:
            if it["kind"] not in KINDS or parse_member_key(it["kind"], it["member_key"]) is None:
                raise ValueError("구성원 종류·키 규칙 밖")
            if set(it.get("display") or {}) - set(MEMBER_DISPLAY_COLUMNS):
                raise ValueError("표시 칼럼 밖")
            if it.get("layer") is not None and it["layer"] not in LAYERS:
                raise ValueError(f"layer 오류: {it['layer']!r}")
        for c in creates:
            _check_text("service_key", str(c.get("service_key") or "").strip(), SERVICE_KEY_MAX,
                        required=True)
            _check_text("display_name", c.get("display_name"), DISPLAY_NAME_MAX, required=True)
            _check_text("owner_org", c.get("owner_org"), OWNER_ORG_MAX)
            _check_tier(c.get("tier"))
        created: list[dict[str, Any]] = []
        out: list[dict[str, Any]] = []
        async with self.transaction() as conn:
            new_ids: dict[str, int] = {}
            for c in creates:
                key = str(c["service_key"]).strip()
                row = await conn.fetchrow(
                    """
                    INSERT INTO biz_service
                        (service_key, display_name, tier, owner_org, origin, state, verified,
                         monitored, evidence, created_by, updated_by)
                    VALUES ($1, $2, $3, $4, $5, 'active', FALSE, FALSE, '{}'::jsonb, $6, $6)
                    ON CONFLICT (service_key) DO NOTHING
                    RETURNING *
                    """,
                    key, str(c["display_name"]).strip(), c.get("tier"), c.get("owner_org"),
                    ORIGIN_MANUAL, by,
                )
                if row is None:
                    existing = await conn.fetchval(
                        "SELECT id FROM biz_service WHERE service_key = $1", key
                    )
                    raise BizServiceExistsError(int(existing or 0))
                created.append(_row(row))
                new_ids[key] = int(row["id"])
            for sid in sorted(versions):
                await self._bump_service(conn, int(sid), int(versions[sid]), by)
            current: dict[tuple[str, str], dict[str, Any]] = {}
            wanted: dict[str, set[str]] = defaultdict(set)
            for it in rows:
                wanted[it["kind"]].add(it["member_key"])
            for kind, keys in sorted(wanted.items()):
                recs = await conn.fetch(
                    "SELECT id, service_id, member_key FROM biz_service_member"
                    " WHERE kind = $1 AND member_key = ANY($2::text[]) FOR UPDATE",
                    kind, sorted(keys),
                )
                for rec in recs:
                    current[(kind, str(rec["member_key"]))] = dict(rec)
            for it in rows:
                out.append(await self._import_member(conn, it, current, new_ids, by))
        return {"created": created, "members": out}

    async def _import_member(
        self,
        conn: Any,
        it: Mapping[str, Any],
        current: dict[tuple[str, str], dict[str, Any]],
        new_ids: Mapping[str, int],
        by: str,
    ) -> dict[str, Any]:
        """가져오기 구성원 1건(트랜잭션 안) — 소속 확인 → 추가 · 옮기기 · 유지."""
        kind, key = str(it["kind"]), str(it["member_key"])
        sid = int(it["service_id"]) if it.get("service_id") is not None else new_ids[
            str(it["service_key"]).strip()]
        layer = it.get("layer")
        hit = current.get((kind, key))
        now_owner = int(hit["service_id"]) if hit is not None else None
        expected = int(it["owner_id"]) if it.get("owner_id") is not None else None
        if now_owner != expected:
            changed = now_owner if now_owner is not None else int(expected or 0)
            ver = await conn.fetchval("SELECT version FROM biz_service WHERE id = $1", changed)
            raise BizServiceVersionConflict(changed, int(ver or 0))
        base = {"line": it.get("line"), "service_key": it.get("service_key")}
        if hit is None:
            display = dict(it.get("display") or {})
            columns = [c for c in MEMBER_DISPLAY_COLUMNS if c in display]
            values = [None if display[c] is None else str(display[c]) for c in columns]
            names = ["service_id", "kind", "member_key", "origin", "basis", "confidence",
                     "state", "layer", "layer_origin", "created_by", "updated_by", *columns]
            marks = ", ".join(f"${i}" for i in range(1, len(names) + 1))
            row = await conn.fetchrow(
                f"INSERT INTO biz_service_member ({', '.join(names)})"
                f" VALUES ({marks}) RETURNING *",
                sid, kind, key, ORIGIN_MANUAL, BASIS_MANUAL, CONFIDENCE_CONFIRMED, MEMBER_ACTIVE,
                layer, ORIGIN_MANUAL if layer is not None else None, by, by, *values,
            )
            current[(kind, key)] = {"id": row["id"], "service_id": sid, "member_key": key}
            return {**base, "member": _row(row), "outcome": "added", "moved_from": None}
        args: list[Any] = [int(hit["id"]), sid, ORIGIN_MANUAL, CONFIDENCE_CONFIRMED, by]
        extra = ""
        if layer is not None:
            args.extend([layer, ORIGIN_MANUAL])
            extra += f", layer = ${len(args) - 1}, layer_origin = ${len(args)}"
        moved = now_owner != sid
        if moved:
            args.append(now_owner)
            extra += (", evidence = evidence || jsonb_build_object('moved_from',"
                      f" ${len(args)}::bigint)")
        row = await conn.fetchrow(
            "UPDATE biz_service_member SET service_id = $2, origin = $3, confidence = $4,"
            f" state = 'active', gone_at = NULL{extra}, updated_by = $5, updated_at = NOW()"
            " WHERE id = $1 RETURNING *",
            *args,
        )
        hit["service_id"] = sid
        return {**base, "member": _row(row), "outcome": "moved" if moved else "kept",
                "moved_from": now_owner if moved else None}

    async def owner_branches(
        self, refs: Collection[tuple[str, str]]
    ) -> dict[tuple[str, str], str]:
        """(W5) 최근 보강 속성 run의 담당 부점(값이 있는 서버만) — `(db_id, resource_id)` → 값."""
        pairs = sorted({(str(d), str(r)) for d, r in refs if d and r})
        if not pairs:
            return {}
        async with self._pool.acquire() as conn:
            rid = await conn.fetchval(
                "SELECT run_id FROM biz_service_attr GROUP BY run_id"
                " ORDER BY MAX(id) DESC LIMIT 1"
            )
            if rid is None:
                return {}
            rows = await conn.fetch(
                "SELECT db_id, resource_id, owner_branch FROM biz_service_attr"
                " WHERE run_id = $1 AND db_id = ANY($2::text[]) AND resource_id = ANY($3::text[])"
                " AND owner_branch IS NOT NULL AND owner_branch <> ''",
                rid, [p[0] for p in pairs], [p[1] for p in pairs],
            )
        wanted = set(pairs)
        return {(str(r["db_id"]), str(r["resource_id"])): str(r["owner_branch"]) for r in rows
                if (str(r["db_id"]), str(r["resource_id"])) in wanted}

    async def active_services_with_members(self) -> list[dict[str, Any]]:
        """활성(state=active) 서비스 전체 + 각자의 활성 구성원(`members`) — 사본 재구성용."""
        async with self._pool.acquire() as conn:
            services = await conn.fetch(
                "SELECT * FROM biz_service WHERE state = 'active' ORDER BY id"
            )
            members = await conn.fetch(
                "SELECT m.* FROM biz_service_member m JOIN biz_service s ON s.id = m.service_id"
                " WHERE s.state = 'active' AND m.state = 'active'"
                " ORDER BY m.service_id, m.kind, m.id"
            )
        by_service: dict[int, list[dict[str, Any]]] = {}
        for m in members:
            rec = _row(m)
            by_service.setdefault(int(rec["service_id"]), []).append(rec)
        out: list[dict[str, Any]] = []
        for s in services:
            rec = _row(s)
            rec["members"] = by_service.get(int(rec["id"]), [])
            out.append(rec)
        return out

    # ── 서비스 관리 ─────────────────────────────────────────────────────
    async def create_service(
        self,
        *,
        service_key: str,
        display_name: str,
        tier: int | None = None,
        description: str | None = None,
        owner_org: str | None = None,
        note: str | None = None,
        monitored: bool = False,
        by: str = "",
    ) -> dict[str, Any]:
        """수동 생성(origin manual · state active).

        Raises:
            ValueError: 키·표시명 비었음 · 길이 초과 · tier 범위 밖
            BizServiceExistsError: 같은 `service_key`가 있다
        """
        key = str(service_key or "").strip()
        _check_text("service_key", key, SERVICE_KEY_MAX, required=True)
        _check_text("display_name", display_name, DISPLAY_NAME_MAX, required=True)
        _check_text("description", description, DESCRIPTION_MAX)
        _check_text("owner_org", owner_org, OWNER_ORG_MAX)
        _check_tier(tier)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO biz_service
                    (service_key, display_name, tier, description, owner_org, origin, state,
                     verified, monitored, evidence, note, created_by, updated_by)
                VALUES ($1, $2, $3, $4, $5, $6, 'active', FALSE, $7, '{}'::jsonb, $8, $9, $9)
                ON CONFLICT (service_key) DO NOTHING
                RETURNING *
                """,
                key, display_name.strip(), tier, description, owner_org, ORIGIN_MANUAL,
                bool(monitored), note, by,
            )
            if row is None:
                existing = await conn.fetchval(
                    "SELECT id FROM biz_service WHERE service_key = $1", key
                )
                raise BizServiceExistsError(int(existing or 0))
        return _row(row)

    async def _version_miss(self, conn: Any, service_id: int) -> None:
        """조건부 쓰기가 0행 — 서비스가 있으면 version 불일치로 올린다(없으면 그대로 반환)."""
        current = await conn.fetchval(
            "SELECT version FROM biz_service WHERE id = $1", int(service_id)
        )
        if current is not None:
            raise BizServiceVersionConflict(int(service_id), int(current))

    async def update_service(
        self,
        service_id: int,
        *,
        version: int,
        changes: Mapping[str, Any],
        by: str = "",
    ) -> dict[str, Any] | None:
        """관리자 수정 — `changes`의 칼럼만(고정 목록 · None은 NULL로 비운다) · version 1 증가.

        Returns:
            갱신된 행(서비스가 없으면 None).

        Raises:
            ValueError: 수정할 수 없는 칼럼 · 값 검증 실패 · 빈 변경
            BizServiceVersionConflict: version 불일치
        """
        unknown = set(changes) - set(_SERVICE_UPDATABLE)
        if unknown:
            raise ValueError(f"수정할 수 없는 칼럼: {sorted(unknown)}")
        if not changes:
            raise ValueError("변경이 비었다")
        if "display_name" in changes:
            _check_text("display_name", changes["display_name"], DISPLAY_NAME_MAX, required=True)
        if "description" in changes:
            _check_text("description", changes["description"], DESCRIPTION_MAX)
        if "owner_org" in changes:
            _check_text("owner_org", changes["owner_org"], OWNER_ORG_MAX)
        if "tier" in changes:
            _check_tier(changes["tier"])
        if "state" in changes and changes["state"] not in ADMIN_SERVICE_STATES:
            raise ValueError(f"관리자 수정 state는 active·disabled만: {changes['state']!r}")
        for flag in ("monitored", "verified"):
            if flag in changes and not isinstance(changes[flag], bool):
                raise ValueError(f"{flag}는 참·거짓이어야 한다")
        args: list[Any] = [int(service_id), int(version), by]
        sets: list[str] = []
        for column in _SERVICE_UPDATABLE:
            if column in changes:
                args.append(changes[column])
                sets.append(f"{column} = ${len(args)}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE biz_service SET {', '.join(sets)},"
                " updated_by = $3, updated_at = NOW(), version = version + 1"
                " WHERE id = $1 AND version = $2 RETURNING *",
                *args,
            )
            if row is None:
                await self._version_miss(conn, int(service_id))
                return None
        return _row(row)

    async def _bump_service(
        self, conn: Any, service_id: int, version: int | None, by: str
    ) -> int:
        """구성원 동작의 서비스 version 증가(행 잠금) → 새 version.

        Raises:
            LookupError: 서비스 없음
            BizServiceVersionConflict: `version`이 주어졌고 다르다
        """
        current = await conn.fetchval(
            "SELECT version FROM biz_service WHERE id = $1 FOR UPDATE", int(service_id)
        )
        if current is None:
            raise LookupError(f"biz_service {service_id} 없음")
        if version is not None and int(current) != int(version):
            raise BizServiceVersionConflict(int(service_id), int(current))
        await conn.execute(
            "UPDATE biz_service SET version = version + 1, updated_by = $2, updated_at = NOW()"
            " WHERE id = $1",
            int(service_id), by,
        )
        return int(current) + 1

    # ── 구성원 관리 ─────────────────────────────────────────────────────
    async def add_member(
        self,
        service_id: int,
        *,
        kind: str,
        member_key: str,
        display: Mapping[str, Any] | None = None,
        basis: str = BASIS_MANUAL,
        confidence: str = CONFIDENCE_CONFIRMED,
        layer: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        version: int | None = None,
        by: str = "",
    ) -> dict[str, Any]:
        """수동 구성원 추가(origin manual · state active) — 서비스 version 증가와 한 트랜잭션.

        `layer`를 주면 `layer_origin=manual`. `display`는 `MEMBER_DISPLAY_COLUMNS`만 받는다.

        Raises:
            ValueError: 종류·키 규칙 밖 · 표시 칼럼 밖 · 계층·확신도 값 오류
            LookupError: 서비스 없음
            BizServiceVersionConflict: `version` 불일치
            BizServiceMemberExistsError: 같은 (kind, member_key)가 이미 있다(SMS 1서버 1서비스)
        """
        if kind not in KINDS or parse_member_key(kind, member_key) is None:
            raise ValueError("구성원 종류·키 규칙 밖")
        if confidence not in (CONFIDENCE_CONFIRMED, CONFIDENCE_INFERRED):
            raise ValueError(f"confidence 오류: {confidence!r}")
        if layer is not None and layer not in LAYERS:
            raise ValueError(f"layer 오류: {layer!r}")
        shown = dict(display or {})
        unknown = set(shown) - set(MEMBER_DISPLAY_COLUMNS)
        if unknown:
            raise ValueError(f"표시 칼럼 밖: {sorted(unknown)}")
        columns = [c for c in MEMBER_DISPLAY_COLUMNS if c in shown]
        values = [None if shown[c] is None else str(shown[c]) for c in columns]
        base = [
            "service_id", "kind", "member_key", "origin", "basis", "confidence", "state",
            "layer", "layer_origin", "evidence", "created_by", "updated_by",
        ]
        args: list[Any] = [
            int(service_id), kind, member_key, ORIGIN_MANUAL, basis, confidence, MEMBER_ACTIVE,
            layer, ORIGIN_MANUAL if layer is not None else None, _dumps(dict(evidence or {})),
            by, by, *values,
        ]
        names = [*base, *columns]
        marks = [f"${i}" for i in range(1, len(names) + 1)]
        marks[base.index("evidence")] += "::jsonb"
        async with self.transaction() as conn:
            await self._bump_service(conn, int(service_id), version, by)
            row = await conn.fetchrow(
                f"INSERT INTO biz_service_member ({', '.join(names)})"
                f" VALUES ({', '.join(marks)})"
                " ON CONFLICT (kind, member_key) DO NOTHING RETURNING *",
                *args,
            )
            if row is None:
                existing = await conn.fetchrow(
                    "SELECT id, service_id FROM biz_service_member"
                    " WHERE kind = $1 AND member_key = $2",
                    kind, member_key,
                )
                rec = dict(existing) if existing is not None else {}
                raise BizServiceMemberExistsError(
                    int(rec.get("id") or 0), int(rec.get("service_id") or 0)
                )
        return _row(row)

    async def set_members_state(
        self,
        service_id: int,
        member_ids: Collection[int],
        *,
        state: str,
        version: int | None = None,
        by: str = "",
    ) -> int:
        """구성원 제외(`excluded`)·제외 취소(`active`) 일괄 → 바뀐 행 수(그 서비스 구성원만)."""
        if state not in (MEMBER_ACTIVE, MEMBER_EXCLUDED):
            raise ValueError(f"관리자 구성원 state는 active·excluded만: {state!r}")
        ids = _ids(member_ids)
        async with self.transaction() as conn:
            await self._bump_service(conn, int(service_id), version, by)
            status = await conn.execute(
                "UPDATE biz_service_member SET state = $3, gone_at = NULL,"
                " updated_by = $4, updated_at = NOW()"
                " WHERE service_id = $1 AND id = ANY($2::bigint[]) AND state <> $3",
                int(service_id), ids, state, by,
            )
        return _count(status)

    async def set_members_confirmed(
        self,
        service_id: int,
        member_ids: Collection[int],
        *,
        confirmed: bool,
        version: int | None = None,
        by: str = "",
    ) -> int:
        """구성원 확인(`confirmed`)·확인 취소(`inferred`) 일괄 → 바뀐 행 수.

        (W4) 관리자 판단이므로 `origin=manual`로 바꾼다 — 재계산이 확신도를 되돌리지 않는다.
        """
        ids = _ids(member_ids)
        confidence = CONFIDENCE_CONFIRMED if confirmed else CONFIDENCE_INFERRED
        async with self.transaction() as conn:
            await self._bump_service(conn, int(service_id), version, by)
            status = await conn.execute(
                "UPDATE biz_service_member SET confidence = $3, origin = $5,"
                " updated_by = $4, updated_at = NOW()"
                " WHERE service_id = $1 AND id = ANY($2::bigint[])"
                " AND (confidence <> $3 OR origin <> $5)",
                int(service_id), ids, confidence, by, ORIGIN_MANUAL,
            )
        return _count(status)

    async def set_members_layer(
        self,
        service_id: int,
        member_ids: Collection[int],
        *,
        layer: str | None,
        version: int | None = None,
        by: str = "",
    ) -> int:
        """계층 일괄 지정(`layer_origin=manual` — 재계산이 덮지 않는다 · None은 미지정) → 행 수."""
        if layer is not None and layer not in LAYERS:
            raise ValueError(f"layer 오류: {layer!r}")
        ids = _ids(member_ids)
        async with self.transaction() as conn:
            await self._bump_service(conn, int(service_id), version, by)
            status = await conn.execute(
                "UPDATE biz_service_member SET layer = $3, layer_origin = $4,"
                " updated_by = $5, updated_at = NOW()"
                " WHERE service_id = $1 AND id = ANY($2::bigint[])",
                int(service_id), ids, layer, ORIGIN_MANUAL, by,
            )
        return _count(status)

    async def move_members(
        self,
        member_ids: Collection[int],
        *,
        from_service_id: int,
        to_service_id: int,
        from_version: int | None = None,
        to_version: int | None = None,
        by: str = "",
    ) -> int:
        """구성원 옮기기(한 트랜잭션 · 두 서비스를 id 순으로 잠근다) → 옮긴 행 수.

        옮긴 구성원은 `origin=manual`(자동이 되돌리지 않는다) · `evidence.moved_from`에 원 서비스
        id. 원 서비스에 속한 구성원만 옮긴다. 두 서비스 version이 모두 오른다.

        Raises:
            ValueError: 같은 서비스로 옮기기 · 일괄 상한
            LookupError: 서비스 없음
            BizServiceVersionConflict: 주어진 version 불일치
        """
        if int(from_service_id) == int(to_service_id):
            raise ValueError("같은 서비스로는 옮길 수 없다")
        ids = _ids(member_ids)
        versions = {int(from_service_id): from_version, int(to_service_id): to_version}
        async with self.transaction() as conn:
            for sid in sorted(versions):
                await self._bump_service(conn, sid, versions[sid], by)
            status = await conn.execute(
                "UPDATE biz_service_member SET service_id = $2, origin = $4,"
                " evidence = evidence || jsonb_build_object('moved_from', $3::bigint),"
                " updated_by = $5, updated_at = NOW()"
                " WHERE id = ANY($1::bigint[]) AND service_id = $3",
                ids, int(to_service_id), int(from_service_id), ORIGIN_MANUAL, by,
            )
        return _count(status)

    # ── 후보 ───────────────────────────────────────────────────────────
    async def list_candidates(
        self,
        *,
        status: str | None = "open",
        kind: str | None = None,
        service_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """후보 목록(최근 갱신 먼저 · 페이지 ≤ `MAX_BULK`)과 전체 건수."""
        where: list[str] = []
        args: list[Any] = []
        for column, value in (("status", status), ("kind", kind)):
            if value:
                args.append(value)
                where.append(f"{column} = ${len(args)}")
        if service_id is not None:
            args.append(int(service_id))
            where.append(f"(service_id = ${len(args)} OR other_service_id = ${len(args)})")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        page = _page(limit, MAX_BULK)
        start = max(0, int(offset))
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM biz_service_candidate {clause}", *args
            )
            rows = await conn.fetch(
                f"SELECT * FROM biz_service_candidate {clause} ORDER BY updated_at DESC, id DESC"
                f" LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
                *args, page, start,
            )
        return [_row(r) for r in rows], int(total or 0)

    async def set_candidates_status(
        self, candidate_ids: Collection[int], *, status: str, by: str = ""
    ) -> list[dict[str, Any]]:
        """후보 상태 일괄 변경(≤ `MAX_BULK`) → 바뀐 행(행마다 감사 1줄용 · 이미 그 상태면 제외).

        거절(`rejected`)은 행이 남아 `candidate_key` UNIQUE로 다시 내지 않는다.
        """
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"후보 status 오류: {status!r}")
        ids = _ids(candidate_ids)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "UPDATE biz_service_candidate SET status = $2, decided_by = $3,"
                " decided_at = CASE WHEN $2 = 'open' THEN NULL ELSE NOW() END,"
                " updated_at = NOW()"
                " WHERE id = ANY($1::bigint[]) AND status <> $2 RETURNING *",
                ids, status, by,
            )
        return [_row(r) for r in rows]

    async def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        """(W4) 후보 단건(없으면 None)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM biz_service_candidate WHERE id = $1", int(candidate_id)
            )
        return _row(row) if row is not None else None

    async def insert_member_candidates(
        self,
        service_id: int,
        service_key: str,
        items: Sequence[Mapping[str, Any]],
        *,
        run_id: str | None = None,
    ) -> dict[str, int]:
        """(W4) 관리자 동작(APM 대조)이 내는 member 후보 삽입 → `{inserted, existing}`.

        `items`는 `member_kind`·`member_key`·`signals`(목록)·`display`(표시 칸 dict)를 읽는다.
        후보 키는 계산과 같은 `candidate_key(member, service_key, kind, key)`라
        같은 대상(거절 포함)이 이미 있으면 그대로 둔다(다시 내지 않는다). 구성원은 바꾸지 않는다.
        """
        if len(items) > MAX_BULK:
            raise ValueError(f"후보는 한 번에 {MAX_BULK}건까지")
        inserted = existing = 0
        async with self.transaction() as conn:
            for item in items:
                kind, key = str(item.get("member_kind") or ""), str(item.get("member_key") or "")
                if kind not in KINDS or parse_member_key(kind, key) is None:
                    raise ValueError("후보 구성원 종류·키 규칙 밖")
                display = {k: v for k, v in dict(item.get("display") or {}).items()
                           if k in MEMBER_DISPLAY_COLUMNS}
                evidence = {"service_key": service_key, "target_kind": kind, "target_key": key,
                            "alt_count": 1, "new_service_name": "", "display": display}
                row_id = await conn.fetchval(
                    """
                    INSERT INTO biz_service_candidate
                        (kind, candidate_key, service_id, member_kind, member_key, signals,
                         evidence, status, run_id)
                    VALUES ('member', $1, $2, $3, $4, $5::jsonb, $6::jsonb, 'open', $7)
                    ON CONFLICT (candidate_key) DO NOTHING
                    RETURNING id
                    """,
                    candidate_key("member", service_key, kind, key), int(service_id), kind, key,
                    _dumps(list(item.get("signals") or [])), _dumps(evidence), run_id,
                )
                if row_id is None:
                    existing += 1
                else:
                    inserted += 1
        return {"inserted": inserted, "existing": existing}

    async def _close_candidate(self, conn: Any, candidate_id: int, by: str) -> dict[str, Any]:
        """열린 후보를 accepted로 닫는다(같은 트랜잭션) — 열려 있지 않으면 예외(롤백)."""
        row = await conn.fetchrow(
            "UPDATE biz_service_candidate SET status = 'accepted', decided_by = $2,"
            " decided_at = NOW(), updated_at = NOW()"
            " WHERE id = $1 AND status = 'open' RETURNING *",
            int(candidate_id), by,
        )
        if row is None:
            status = await conn.fetchval(
                "SELECT status FROM biz_service_candidate WHERE id = $1", int(candidate_id)
            )
            if status is None:
                raise LookupError(f"biz_service_candidate {candidate_id} 없음")
            raise BizServiceCandidateNotOpen(int(candidate_id), str(status))
        return _row(row)

    async def accept_member_candidate(
        self,
        candidate_id: int,
        *,
        display: Mapping[str, Any] | None = None,
        by: str = "",
    ) -> dict[str, Any]:
        """(W4) member 후보 수락 — 한 트랜잭션 → `{member, moved_from, service_version, candidate}`.

        후보 서비스에 구성원을 넣는다(`origin=manual`·`basis=manual`·`confirmed`·`active` ·
        `evidence.aux`에 후보 근거 `signals`와 `candidate_id`를 보존 — 재계산이 바꾸지 않는다). 같은
        (kind, member_key)가 다른 서비스에 있으면 옮기고(`moved_from`) 두 서비스 version을 id 순으로
        올린다. 후보는 accepted.

        Raises:
            LookupError: 후보·서비스 없음
            BizServiceCandidateNotOpen: 이미 수락·거절됨
            ValueError: member 후보가 아님 · 후보 서비스 미정(아직 없는 서비스) · 키 규칙 밖
        """
        shown = {k: v for k, v in dict(display or {}).items() if k in MEMBER_DISPLAY_COLUMNS}
        async with self.transaction() as conn:
            cand = await conn.fetchrow(
                "SELECT * FROM biz_service_candidate WHERE id = $1 FOR UPDATE", int(candidate_id)
            )
            if cand is None:
                raise LookupError(f"biz_service_candidate {candidate_id} 없음")
            rec = _row(cand)
            if rec.get("status") != "open":
                raise BizServiceCandidateNotOpen(int(candidate_id), str(rec.get("status")))
            if rec.get("kind") != "member":
                raise ValueError("member 후보만 구성원으로 수락한다")
            if rec.get("service_id") is None:
                raise ValueError("후보 서비스가 아직 없다 — 서비스를 먼저 만든다")
            sid = int(rec["service_id"])
            kind, key = str(rec.get("member_kind") or ""), str(rec.get("member_key") or "")
            if kind not in KINDS or parse_member_key(kind, key) is None:
                raise ValueError("후보 구성원 종류·키 규칙 밖")
            aux = _dumps({"aux": list(rec.get("signals") or []), "candidate_id": int(candidate_id)})
            existing = await conn.fetchrow(
                "SELECT id, service_id FROM biz_service_member"
                " WHERE kind = $1 AND member_key = $2 FOR UPDATE",
                kind, key,
            )
            old = int(dict(existing)["service_id"]) if existing is not None else None
            versions: dict[int, int] = {}
            for s in sorted({sid} | ({old} if old is not None else set())):
                versions[s] = await self._bump_service(conn, s, None, by)
            if existing is not None:
                moved = ("|| jsonb_build_object('moved_from', $7::bigint)"
                         if old != sid else "")
                args: list[Any] = [int(dict(existing)["id"]), sid, ORIGIN_MANUAL, BASIS_MANUAL,
                                   CONFIDENCE_CONFIRMED, aux]
                if old != sid:
                    args.append(old)
                row = await conn.fetchrow(
                    "UPDATE biz_service_member SET service_id = $2, origin = $3, basis = $4,"
                    " confidence = $5, state = 'active', gone_at = NULL,"
                    f" evidence = evidence || $6::jsonb {moved},"
                    f" updated_by = ${len(args) + 1}, updated_at = NOW()"
                    " WHERE id = $1 RETURNING *",
                    *args, by,
                )
            else:
                columns = [c for c in MEMBER_DISPLAY_COLUMNS if c in shown]
                values = [None if shown[c] is None else str(shown[c]) for c in columns]
                base = ["service_id", "kind", "member_key", "origin", "basis", "confidence",
                        "state", "evidence", "created_by", "updated_by"]
                names = [*base, *columns]
                marks = [f"${i}" for i in range(1, len(names) + 1)]
                marks[base.index("evidence")] += "::jsonb"
                row = await conn.fetchrow(
                    f"INSERT INTO biz_service_member ({', '.join(names)})"
                    f" VALUES ({', '.join(marks)}) RETURNING *",
                    sid, kind, key, ORIGIN_MANUAL, BASIS_MANUAL, CONFIDENCE_CONFIRMED,
                    MEMBER_ACTIVE, aux, by, by, *values,
                )
            closed = await self._close_candidate(conn, int(candidate_id), by)
        return {"member": _row(row), "moved_from": old if old != sid else None,
                "service_version": versions[sid], "candidate": closed}

    # ── (W4) 병합 · 분할 ───────────────────────────────────────────────
    async def merge_services(
        self,
        from_service_id: int,
        to_service_id: int,
        *,
        from_version: int | None = None,
        to_version: int | None = None,
        by: str = "",
        candidate_id: int | None = None,
    ) -> dict[str, Any]:
        """병합 A → B — 한 트랜잭션 → `{moved, from_version, to_version, candidate}`.

        A의 구성원 전부(전 상태)를 B로 옮기고(`origin=manual` · `evidence.moved_from`·`merged_from`)
        A는 `disabled` + `evidence.merged_into`(B id)·`merged_by`·`merged_at`, B는
        `evidence.merged_from`(목록)에 A id를 더한다. `candidate_id`를 주면 그 merge 후보를 같은
        트랜잭션에서 accepted로 닫는다.

        Raises:
            ValueError: 같은 서비스 · A나 B가 이미 비활성
            LookupError: 서비스·후보 없음
            BizServiceVersionConflict: 주어진 version 불일치
            BizServiceCandidateNotOpen: 후보가 이미 닫혔다
        """
        src, dst = int(from_service_id), int(to_service_id)
        if src == dst:
            raise ValueError("같은 서비스로는 병합할 수 없다")
        versions = {src: from_version, dst: to_version}
        async with self.transaction() as conn:
            new_versions = {sid: await self._bump_service(conn, sid, versions[sid], by)
                            for sid in sorted(versions)}
            states = {int(dict(r)["id"]): str(dict(r)["state"]) for r in await conn.fetch(
                "SELECT id, state FROM biz_service WHERE id = ANY($1::bigint[])", [src, dst]
            )}
            if "disabled" in (states.get(src), states.get(dst)):
                raise ValueError("비활성 서비스는 병합할 수 없다")
            status = await conn.execute(
                "UPDATE biz_service_member SET service_id = $2, origin = $3,"
                " evidence = evidence || jsonb_build_object('moved_from', $1::bigint,"
                " 'merged_from', $1::bigint), updated_by = $4, updated_at = NOW()"
                " WHERE service_id = $1",
                src, dst, ORIGIN_MANUAL, by,
            )
            await conn.execute(
                "UPDATE biz_service SET state = 'disabled',"
                " evidence = evidence || jsonb_build_object('merged_into', $2::bigint,"
                " 'merged_by', $3::text, 'merged_at', to_jsonb(NOW()))"
                " WHERE id = $1",
                src, dst, by,
            )
            await conn.execute(
                "UPDATE biz_service SET evidence = jsonb_set(evidence, '{merged_from}',"
                " COALESCE(evidence->'merged_from', '[]'::jsonb) || to_jsonb($2::bigint))"
                " WHERE id = $1",
                dst, src,
            )
            closed = (await self._close_candidate(conn, int(candidate_id), by)
                      if candidate_id is not None else None)
        return {"moved": _count(status), "from_version": new_versions[src],
                "to_version": new_versions[dst], "candidate": closed}

    async def split_service(
        self,
        service_id: int,
        member_ids: Collection[int],
        *,
        version: int | None = None,
        service_key: str,
        display_name: str,
        tier: int | None = None,
        description: str | None = None,
        owner_org: str | None = None,
        by: str = "",
    ) -> dict[str, Any]:
        """분할 — 선택 구성원을 새 수동 서비스로 옮긴다(한 트랜잭션).

        → `{service, moved, from_version}`.

        새 서비스는 `origin=manual`·`active`·`evidence.split_from`(원 서비스 id) · 옮긴 구성원은
        `origin=manual`·`evidence.moved_from`·`split_from`. 원 서비스에 속한 구성원만 옮긴다.

        Raises:
            ValueError: 값 검증 실패 · 일괄 상한 · 옮길 구성원 0(롤백)
            LookupError: 원 서비스 없음
            BizServiceVersionConflict: version 불일치
            BizServiceExistsError: 같은 `service_key`가 있다(롤백)
        """
        key = str(service_key or "").strip()
        _check_text("service_key", key, SERVICE_KEY_MAX, required=True)
        _check_text("display_name", display_name, DISPLAY_NAME_MAX, required=True)
        _check_text("description", description, DESCRIPTION_MAX)
        _check_text("owner_org", owner_org, OWNER_ORG_MAX)
        _check_tier(tier)
        ids = _ids(member_ids)
        src = int(service_id)
        async with self.transaction() as conn:
            from_version = await self._bump_service(conn, src, version, by)
            row = await conn.fetchrow(
                """
                INSERT INTO biz_service
                    (service_key, display_name, tier, description, owner_org, origin, state,
                     verified, monitored, evidence, created_by, updated_by)
                VALUES ($1, $2, $3, $4, $5, $6, 'active', FALSE, FALSE,
                        jsonb_build_object('split_from', $7::bigint), $8, $8)
                ON CONFLICT (service_key) DO NOTHING
                RETURNING *
                """,
                key, display_name.strip(), tier, description, owner_org, ORIGIN_MANUAL, src, by,
            )
            if row is None:
                existing = await conn.fetchval(
                    "SELECT id FROM biz_service WHERE service_key = $1", key
                )
                raise BizServiceExistsError(int(existing or 0))
            new_id = int(dict(row)["id"])
            status = await conn.execute(
                "UPDATE biz_service_member SET service_id = $2, origin = $4,"
                " evidence = evidence || jsonb_build_object('moved_from', $3::bigint,"
                " 'split_from', $3::bigint), updated_by = $5, updated_at = NOW()"
                " WHERE id = ANY($1::bigint[]) AND service_id = $3",
                ids, new_id, src, ORIGIN_MANUAL, by,
            )
            moved = _count(status)
            if not moved:
                raise ValueError("옮길 구성원이 없다(원 서비스 구성원만 고른다)")
        return {"service": _row(row), "moved": moved, "from_version": from_version}

    # ── (W3) 계산 적용 ─────────────────────────────────────────────────
    async def all_services_with_members(self) -> list[dict[str, Any]]:
        """서비스 전체(전 상태) + 각자의 구성원 전체(`members` · 전 상태) — 계산 입력(저장 상태)."""
        async with self._pool.acquire() as conn:
            services = await conn.fetch("SELECT * FROM biz_service ORDER BY id")
            members = await conn.fetch(
                "SELECT * FROM biz_service_member ORDER BY service_id, kind, id"
            )
        by_service: dict[int, list[dict[str, Any]]] = {}
        for m in members:
            rec = _row(m)
            by_service.setdefault(int(rec["service_id"]), []).append(rec)
        out: list[dict[str, Any]] = []
        for s in services:
            rec = _row(s)
            rec["members"] = by_service.get(int(rec["id"]), [])
            out.append(rec)
        return out

    async def all_candidates(self) -> list[dict[str, Any]]:
        """후보 전체(전 상태 · id 순) — 거절·기존 후보를 다시 내지 않는 판정 재료."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM biz_service_candidate ORDER BY id")
        return [_row(r) for r in rows]

    async def apply_ops(self, ops: ApplyOps, *, run_id: str, by: str = "") -> dict[str, int]:
        """적용 연산 목록(`plan_apply` 결과)을 **한 트랜잭션**으로 실행 → 연산별 건수.

        서비스 → 구성원 → 후보 순이다. 자동 쓰기는 WHERE 조건으로 한 번 더 막는다 — 관리자 결정
        (manual·verified·disabled 서비스 · `origin=manual`·`excluded` 구성원)은
        미리보기와 적용 사이에 바뀌었어도 덮지 않고 `skipped_*`로 센다. 구성원이 바뀐
        서비스는 `version`이 1 오른다(가져오기 미리보기 뒤 변경 감지). 신규 서비스 키가
        그 사이 생겼으면 `BizServiceExistsError`(롤백).
        """
        counts: Counter[str] = Counter()
        touched: set[int] = set()
        async with self.transaction() as conn:
            ids = {str(r["service_key"]): int(r["id"])
                   for r in await conn.fetch("SELECT id, service_key FROM biz_service")}
            for op in ops.services:
                sid, changed = await self._apply_service(conn, op, run_id=run_id, by=by,
                                                         counts=counts)
                if sid is not None:
                    ids[op.service_key] = sid
                    if changed and op.action in (OP_INSERT, OP_UPDATE):
                        touched.add(sid)
            for mop in ops.members:
                touched |= await self._apply_member(conn, mop, ids, by=by, counts=counts)
            for cop in ops.candidates:
                await self._apply_candidate(conn, cop.candidate, ids, run_id=run_id,
                                            counts=counts)
            if touched:
                await conn.execute(
                    "UPDATE biz_service SET version = version + 1, updated_by = $2,"
                    " updated_at = NOW() WHERE id = ANY($1::bigint[])",
                    sorted(touched), by,
                )
        return dict(sorted(counts.items()))

    async def _apply_service(
        self, conn: Any, op: ServiceOp, *, run_id: str, by: str, counts: Counter[str]
    ) -> tuple[int | None, bool]:
        """서비스 연산 1건 → (그 서비스 id, 행이 바뀌었나) — 신규 키 충돌은 예외."""
        if op.action == OP_INSERT:
            new_id = await conn.fetchval(
                """
                INSERT INTO biz_service
                    (service_key, display_name, origin, state, verified, monitored, evidence,
                     created_by, updated_by, last_run_id)
                VALUES ($1, $2, $3, $4, FALSE, FALSE, $5::jsonb, $6, $6, $7)
                ON CONFLICT (service_key) DO NOTHING
                RETURNING id
                """,
                op.service_key, op.display_name, ORIGIN_AUTO, op.state,
                _dumps(dict(op.evidence)), by, run_id,
            )
            if new_id is None:
                existing = await conn.fetchval(
                    "SELECT id FROM biz_service WHERE service_key = $1", op.service_key
                )
                raise BizServiceExistsError(int(existing or 0))
            counts["service_insert"] += 1
            return int(new_id), True
        if op.service_id is None or op.action == OP_SKIP:
            return op.service_id, False
        sid = int(op.service_id)
        if op.action == OP_UPDATE:
            status = await conn.execute(
                "UPDATE biz_service SET display_name = $2, state = $3, evidence = $4::jsonb,"
                " last_run_id = $5, updated_by = $6, updated_at = NOW()"
                " WHERE id = $1 AND origin = 'auto' AND NOT verified AND state <> 'disabled'",
                sid, op.display_name, op.state, _dumps(dict(op.evidence)), run_id, by,
            )
        elif op.action == OP_SUGGEST:
            status = await conn.execute(
                "UPDATE biz_service SET evidence = evidence || jsonb_build_object("
                "'scan_suggests', $2::jsonb), last_run_id = $3 "
                "WHERE id = $1 AND state <> 'disabled'",
                sid, _dumps(dict(op.evidence).get("scan_suggests") or {}), run_id,
            )
        else:  # same — 결과 동일(updated_at만)
            status = await conn.execute(
                "UPDATE biz_service SET last_run_id = $2, updated_at = NOW() WHERE id = $1",
                sid, run_id,
            )
        changed = bool(_count(status))
        counts[f"service_{op.action}" if changed else f"skipped_service_{op.action}"] += 1
        return sid, changed

    async def _apply_member(
        self, conn: Any, op: MemberOp, ids: Mapping[str, int], *, by: str, counts: Counter[str]
    ) -> set[int]:
        """구성원 연산 1건 → 바뀐 서비스 id 집합(조건 불일치·대상 없음이면 빈 집합)."""
        sid = op.service_id if op.service_id is not None else ids.get(op.service_key)
        if sid is None:
            counts[f"skipped_member_{op.action}"] += 1
            return set()
        sid = int(sid)
        if op.action == OP_INSERT:
            columns, values = _member_display(op)
            base = [
                "service_id", "kind", "member_key", "origin", "basis", "confidence", "state",
                "apm_map_id", "layer", "layer_origin", "evidence", "created_by", "updated_by",
            ]
            args: list[Any] = [
                sid, op.kind, op.member_key, ORIGIN_AUTO, op.basis, op.confidence, MEMBER_ACTIVE,
                op.apm_map_id, op.layer, op.layer_origin, _dumps(dict(op.evidence)), by, by,
                *values,
            ]
            names = [*base, *columns]
            marks = [f"${i}" for i in range(1, len(names) + 1)]
            marks[base.index("evidence")] += "::jsonb"
            row_id = await conn.fetchval(
                f"INSERT INTO biz_service_member ({', '.join(names)})"
                f" VALUES ({', '.join(marks)})"
                " ON CONFLICT (kind, member_key) DO NOTHING RETURNING id",
                *args,
            )
            changed = row_id is not None
        elif op.action in (OP_UPDATE, OP_MOVE):
            columns, values = _member_display(op)
            args = [op.kind, op.member_key, sid, op.basis, op.confidence, op.apm_map_id,
                    op.layer, op.layer_origin, _dumps(dict(op.evidence)), by, *values]
            sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(columns, start=11))
            where = " WHERE kind = $1 AND member_key = $2 AND origin = 'auto'" \
                    " AND state <> 'excluded'"
            if op.action == OP_MOVE:
                args.append(int(op.from_service_id or 0))
                where += f" AND service_id = ${len(args)}"
            else:
                where += " AND service_id = $3"
            status = await conn.execute(
                "UPDATE biz_service_member SET service_id = $3, basis = $4, confidence = $5,"
                " state = 'active', gone_at = NULL, apm_map_id = $6, layer = $7,"
                " layer_origin = $8, evidence = $9::jsonb, updated_by = $10, updated_at = NOW()"
                f"{', ' + sets if sets else ''}{where}",
                *args,
            )
            changed = bool(_count(status))
            if changed and op.action == OP_MOVE and op.from_service_id is not None:
                return {sid, int(op.from_service_id)}
        elif op.action in (OP_TARGET_GONE, OP_RESTORE):
            gone = op.action == OP_TARGET_GONE
            status = await conn.execute(
                "UPDATE biz_service_member SET state = $4,"
                " gone_at = CASE WHEN $4 = 'target_gone' THEN NOW() ELSE NULL END,"
                " updated_by = $5, updated_at = NOW()"
                " WHERE kind = $1 AND member_key = $2 AND service_id = $3 AND state = $6",
                op.kind, op.member_key, sid, MEMBER_TARGET_GONE if gone else MEMBER_ACTIVE, by,
                MEMBER_ACTIVE if gone else MEMBER_TARGET_GONE,
            )
            changed = bool(_count(status))
        elif op.action in (OP_DELETE, OP_PURGE):
            extra = " AND state = 'target_gone'" if op.action == OP_PURGE else \
                " AND state <> 'excluded'"
            status = await conn.execute(
                "DELETE FROM biz_service_member WHERE kind = $1 AND member_key = $2"
                f" AND service_id = $3 AND origin = 'auto'{extra}",
                op.kind, op.member_key, sid,
            )
            changed = bool(_count(status))
        else:
            raise ValueError(f"구성원 연산 오류: {op.action!r}")
        counts[f"member_{op.action}" if changed else f"skipped_member_{op.action}"] += 1
        return {sid} if changed else set()

    async def _apply_candidate(
        self, conn: Any, cand: Candidate, ids: Mapping[str, int], *, run_id: str,
        counts: Counter[str],
    ) -> None:
        """후보 1건 삽입 — 같은 `candidate_key`(거절 포함)가 있으면 그대로 둔다."""
        is_merge = cand.kind == CAND_MERGE
        signals = [
            {"basis": s.basis, "bundle_key": s.bundle_key, "anchors": s.anchors,
             "purity": s.purity}
            for s in cand.signals
        ]
        evidence = {
            "service_key": cand.service_key, "target_kind": cand.target_kind,
            "target_key": cand.target_key, "alt_count": cand.alt_count,
            "new_service_name": cand.new_service_name,
        }
        row_id = await conn.fetchval(
            """
            INSERT INTO biz_service_candidate
                (kind, candidate_key, service_id, other_service_id, member_kind, member_key,
                 signals, evidence, status, run_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, 'open', $9)
            ON CONFLICT (candidate_key) DO NOTHING
            RETURNING id
            """,
            cand.kind,
            candidate_key(cand.kind, cand.service_key, cand.target_kind, cand.target_key),
            ids.get(cand.service_key), ids.get(cand.target_key) if is_merge else None,
            None if is_merge else cand.target_kind, None if is_merge else cand.target_key,
            _dumps(signals), _dumps(evidence), run_id,
        )
        counts["candidate_insert" if row_id is not None else "skipped_candidate_insert"] += 1

    # ── 실행 기록 ───────────────────────────────────────────────────────
    async def start_run(
        self,
        *,
        mode: str,
        requested_by: str = "",
        scan_run_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """실행 기록 시작(status running) → 행."""
        if mode not in RUN_MODES:
            raise ValueError(f"실행 mode 오류: {mode!r}")
        rid = run_id or uuid.uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO biz_service_run (run_id, requested_by, scan_run_id, mode, status)"
                " VALUES ($1, $2, $3, $4, $5) RETURNING *",
                rid, requested_by, scan_run_id, mode, RUN_RUNNING,
            )
        return _row(row)

    async def finish_run(
        self, run_id: str, *, status: str, summary: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """실행 기록 완료(done·failed·aborted · finished_at NOW) → 행(없으면 None)."""
        if status not in RUN_FINISHED_STATUSES:
            raise ValueError(f"완료 status 오류: {status!r}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE biz_service_run SET status = $2, summary = $3::jsonb, finished_at = NOW()"
                " WHERE run_id = $1 RETURNING *",
                run_id, status, _dumps(dict(summary or {})),
            )
        return _row(row) if row is not None else None

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """실행 기록 단건(없으면 None)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM biz_service_run WHERE run_id = $1", run_id)
        return _row(row) if row is not None else None

    async def list_runs(
        self, *, mode: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """실행 기록 최근 순(summary 포함 — 추이용 · 페이지 ≤ `MAX_LIST_LIMIT`)."""
        page = _page(limit)
        async with self._pool.acquire() as conn:
            if mode:
                rows = await conn.fetch(
                    "SELECT * FROM biz_service_run WHERE mode = $1"
                    " ORDER BY started_at DESC LIMIT $2",
                    mode, page,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM biz_service_run ORDER BY started_at DESC LIMIT $1", page
                )
        return [_row(r) for r in rows]

    async def abort_running_runs(self) -> int:
        """기동 시 남은 running 실행 기록을 aborted로 닫는다 → 건수."""
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE biz_service_run SET status = $1, finished_at = NOW() WHERE status = $2",
                RUN_ABORTED, RUN_RUNNING,
            )
        return _count(status)

    # ── 보강 속성(run별) ────────────────────────────────────────────────
    async def save_attrs(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """run 1개의 보강 속성 적재(같은 run은 교체) · 최근 run `ATTR_KEEP_RUNS`개분만 남긴다.

        행은 `ATTR_COLUMNS` 키만 읽는다(`db_id`·`resource_id` 필수 · 없는 행은 건너뛴다).
        """
        records: list[tuple[Any, ...]] = []
        for raw in rows:
            if not raw.get("db_id") or not raw.get("resource_id"):
                continue
            records.append(
                (run_id, *(_attr_value(c, raw.get(c)) for c in ATTR_COLUMNS))
            )
        marks = ", ".join(
            f"${i + 2}::jsonb" if c == "depends" else f"${i + 2}"
            for i, c in enumerate(ATTR_COLUMNS)
        )
        async with self.transaction() as conn:
            await conn.execute("DELETE FROM biz_service_attr WHERE run_id = $1", run_id)
            if records:
                await conn.executemany(
                    f"INSERT INTO biz_service_attr (run_id, {', '.join(ATTR_COLUMNS)})"
                    f" VALUES ($1, {marks})"
                    " ON CONFLICT (run_id, db_id, resource_id) DO NOTHING",
                    records,
                )
            await conn.execute(
                "DELETE FROM biz_service_attr WHERE run_id NOT IN ("
                " SELECT run_id FROM biz_service_attr GROUP BY run_id"
                " ORDER BY MAX(id) DESC LIMIT $1)",
                ATTR_KEEP_RUNS,
            )
        return len(records)

    async def get_attrs(
        self, run_id: str | None = None
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """보강 속성 읽기 → `(run_id, 행)` — run_id를 안 주면 가장 최근 run(없으면 `(None, [])`)."""
        async with self._pool.acquire() as conn:
            rid = run_id
            if rid is None:
                rid = await conn.fetchval(
                    "SELECT run_id FROM biz_service_attr GROUP BY run_id"
                    " ORDER BY MAX(id) DESC LIMIT 1"
                )
                if rid is None:
                    return None, []
            rows = await conn.fetch(
                "SELECT * FROM biz_service_attr WHERE run_id = $1 ORDER BY db_id, resource_id",
                rid,
            )
        return str(rid), [_row(r) for r in rows]

    # ── 상태 전이 기록 ──────────────────────────────────────────────────
    async def add_state_log(
        self,
        service_id: int,
        *,
        from_state: str | None,
        to_state: str,
        reason: Mapping[str, Any] | None = None,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        """서비스 상태 전이 1건 기록 → 행."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO biz_service_state_log (service_id, from_state, to_state, reason, at)"
                " VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING *",
                int(service_id), from_state, to_state, _dumps(dict(reason or {})), _now(at),
            )
        return _row(row)

    async def list_state_log(
        self, service_id: int, *, since: datetime | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """서비스 상태 전이 최근 순(`since` 이후만 · 페이지 ≤ `MAX_LIST_LIMIT`)."""
        page = _page(limit)
        async with self._pool.acquire() as conn:
            if since is not None:
                rows = await conn.fetch(
                    "SELECT * FROM biz_service_state_log WHERE service_id = $1 AND at >= $2"
                    " ORDER BY at DESC, id DESC LIMIT $3",
                    int(service_id), since, page,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM biz_service_state_log WHERE service_id = $1"
                    " ORDER BY at DESC, id DESC LIMIT $2",
                    int(service_id), page,
                )
        return [_row(r) for r in rows]

    # ── 정리 ───────────────────────────────────────────────────────────
    async def cleanup(self, *, now: datetime | None = None) -> dict[str, int]:
        """보존 기한 정리 — 실행 기록 90일 · 상태 전이 90일 · target_gone 자동 구성원 180일.

        수동 구성원(origin manual)은 target_gone이어도 지우지 않는다.
        """
        at = _now(now)
        async with self._pool.acquire() as conn:
            runs = await conn.execute(
                "DELETE FROM biz_service_run WHERE started_at < $1 AND status <> $2",
                at - timedelta(days=RUN_RETENTION_DAYS), RUN_RUNNING,
            )
            logs = await conn.execute(
                "DELETE FROM biz_service_state_log WHERE at < $1",
                at - timedelta(days=STATE_LOG_RETENTION_DAYS),
            )
            gone = await conn.execute(
                "DELETE FROM biz_service_member WHERE state = $1 AND origin = $2"
                " AND gone_at < $3",
                MEMBER_TARGET_GONE, ORIGIN_AUTO, at - timedelta(days=TARGET_GONE_RETENTION_DAYS),
            )
        out = {"runs": _count(runs), "state_logs": _count(logs), "gone_members": _count(gone)}
        if any(out.values()):
            logger.info("biz_service 보존 정리: %s", out)
        return out


__all__ = [
    "ADMIN_SERVICE_STATES",
    "ATTR_COLUMNS",
    "ATTR_KEEP_RUNS",
    "CANDIDATE_KINDS",
    "CANDIDATE_STATUSES",
    "LAYERS",
    "MAX_BULK",
    "MAX_LIST_LIMIT",
    "MEMBER_DISPLAY_COLUMNS",
    "MEMBER_STATES",
    "RUN_MODES",
    "SEARCH_CAP",
    "SERVICE_STATES",
    "BizServiceCandidateNotOpen",
    "BizServiceExistsError",
    "BizServiceMemberExistsError",
    "BizServiceVersionConflict",
    "PostgresBizServiceRepository",
    "candidate_key",
]
