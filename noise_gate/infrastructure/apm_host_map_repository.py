"""APM·SMS 연계 매핑 PG 정본 저장소 (plans/210 §3.3 · §3.5 · D-362).

`apm_host_map`(매핑) · `apm_host_map_scan_run`(스캔 실행 기록) · 스냅샷 4표와 「현재 스냅샷」 포인터
(`apm_host_map_snapshot_current`)를 asyncpg 풀(auth 풀)로 읽고 쓴다. DDL은
`ddl/apm_host_map.sql`(기동 시 `src/api/server.py` `_ensure_apm_host_map_tables`).

- 기록자는 API 프로세스뿐이다(D-049) — 워커는 Redis Stream `apmhostmap:req`로 넘긴다.
- 자동 반영은 도메인 쓰기 규칙(`plan_auto_write`)을 **행 잠금(FOR UPDATE) 트랜잭션 안에서** 적용한다
  — 관리자 행(manual·verified)·disabled를 자동이 덮지 않는다. 행별 개별 트랜잭션이다.
- 관리 동작(추가·수정·확인·확인 취소·삭제)은 `version` 낙관적 동시성
  — 불일치는 `ApmHostMapVersionError`.
- SQL 값은 전부 파라미터 바인딩이다. 식별자(스냅샷 표·칼럼)는 이 모듈의 고정 목록에서만 고른다.
- 관리 동작의 DB 오류는 삼키지 않고 올린다(호출부가 응답에 사유를 싣는다).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from noise_gate.domain.apm_host_map import (
    AUTO_INSTANCE_IDLE_DAYS,
    AUTO_UNSEEN_DAYS,
    BASIS_MANUAL,
    ORIGIN_AUTO,
    ORIGIN_MANUAL,
    SCAN_ABORTED,
    SCAN_RUN_RETENTION_DAYS,
    SCAN_RUNNING,
    SCAN_STATUSES,
    SCOPE_INSTANCE,
    STATE_ACTIVE,
    STATE_DISABLED,
    STATES,
    WRITE_INSERT,
    WRITE_SAME,
    WRITE_SKIP,
    WRITE_SUGGEST,
    WRITE_UPDATE,
    ApmHostMapExistsError,
    ApmHostMapRepository,
    ApmHostMapVersionError,
    AutoProposal,
    MapWrite,
    mark_targets_gone,
    next_retry_at,
    normalize_targets,
    plan_auto_write,
    valid_key,
)

logger = logging.getLogger(__name__)

#: 스냅샷 표 → 적재 칼럼(고정 목록 — 식별자는 여기서만 고른다 · ITAM은 PII 칼럼 없음).
SNAPSHOT_COLUMNS: dict[str, tuple[str, ...]] = {
    "apm_inventory": (
        "source_id", "domain_id", "domain_name", "domain_description", "instance_id",
        "instance_name", "ip", "host_name", "workload_key", "host_address", "host_address_status",
    ),
    "sms_inventory": ("db_id", "resource_id", "server_name", "hostname", "ip", "nic_ips"),
    "itam_snapshot_app": ("app_code", "app_name"),
    "itam_snapshot_server": ("app_code", "app_name", "hostname", "ips", "active", "clue_text"),
}
#: 검색어(ILIKE) 대상 — 문자열 칼럼만.
_SNAPSHOT_TEXT_COLUMNS: dict[str, tuple[str, ...]] = {
    "apm_inventory": (
        "domain_id", "domain_name", "domain_description", "instance_name", "ip", "host_name",
        "host_address",
    ),
    "sms_inventory": ("db_id", "server_name", "hostname", "ip"),
    "itam_snapshot_app": ("app_code", "app_name"),
    "itam_snapshot_server": ("app_code", "app_name", "hostname", "clue_text"),
}

#: 목록 페이지 상한.
_MAX_LIST_LIMIT = 500
#: 자동 반영 행의 작성자 표기.
_AUTO_BY = "auto"
#: 같은 키 신규 삽입이 경쟁으로 비었을 때 다시 시도하는 횟수.
_INSERT_ATTEMPTS = 2

#: 「검토 필요」 조건 — ambiguous·failed · 변경됨 · scan_suggests · target_gone 대상.
_REVIEW_SQL = (
    "(state IN ('ambiguous', 'failed') OR evidence ? 'changed' OR evidence ? 'scan_suggests'"
    " OR targets @> '[{\"target_gone\": true}]'::jsonb)"
)


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
    if "targets" in out:
        targets = _json_load(out["targets"], [])
        out["targets"] = targets if isinstance(targets, list) else []
    for key in ("evidence", "scanners", "link_summary"):
        if key in out:
            value = _json_load(out[key], {})
            out[key] = value if isinstance(value, dict) else {}
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


def _snapshot_table(table: str) -> tuple[str, ...]:
    columns = SNAPSHOT_COLUMNS.get(table)
    if columns is None:
        raise ValueError(f"알 수 없는 스냅샷 표: {table!r}")
    return columns


class PostgresApmHostMapRepository(ApmHostMapRepository):
    """PostgreSQL 연계 매핑 저장소."""

    def __init__(self, pool: Any) -> None:  # asyncpg.Pool (덕 타이핑)
        """asyncpg 풀(auth 풀)을 받는다."""
        self._pool = pool

    # ── 매핑 조회 ───────────────────────────────────────────────────────
    async def list_maps(
        self,
        *,
        source_id: str | None = None,
        state: str | None = None,
        origin: str | None = None,
        basis: str | None = None,
        query: str | None = None,
        review_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """목록(최근 갱신 먼저)과 전체 건수."""
        where: list[str] = []
        args: list[Any] = []
        for column, value in (
            ("source_id", source_id), ("state", state), ("origin", origin), ("basis", basis),
        ):
            if value:
                args.append(value)
                where.append(f"{column} = ${len(args)}")
        if query and query.strip():
            args.append(_like(query.strip()))
            n = len(args)
            where.append(
                f"(domain_name ILIKE ${n} OR instance_example ILIKE ${n} OR match_key ILIKE ${n}"
                f" OR domain_id ILIKE ${n} OR targets::text ILIKE ${n})"
            )
        if review_only:
            where.append(_REVIEW_SQL)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        page = max(1, min(int(limit), _MAX_LIST_LIMIT))
        start = max(0, int(offset))
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(f"SELECT COUNT(*) FROM apm_host_map {clause}", *args)
            rows = await conn.fetch(
                f"SELECT * FROM apm_host_map {clause} ORDER BY updated_at DESC, id DESC"
                f" LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
                *args, page, start,
            )
        return [_row(r) for r in rows], int(total or 0)

    async def get(self, map_id: int) -> dict[str, Any] | None:
        """단건(없으면 None)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM apm_host_map WHERE id = $1", int(map_id))
        return _row(row) if row is not None else None

    async def get_by_key(
        self, source_id: str, domain_id: str, scope: str, match_key: str
    ) -> dict[str, Any] | None:
        """키로 단건(없으면 None)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM apm_host_map WHERE source_id = $1 AND domain_id = $2"
                " AND scope = $3 AND match_key = $4",
                source_id, domain_id, scope, match_key,
            )
        return _row(row) if row is not None else None

    async def all_rows(self) -> list[dict[str, Any]]:
        """전체 행(사본 재구성용)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM apm_host_map ORDER BY id")
        return [_row(r) for r in rows]

    # ── 관리 동작 ───────────────────────────────────────────────────────
    async def create_manual(
        self,
        *,
        source_id: str,
        domain_id: str,
        scope: str,
        match_key: str,
        targets: Sequence[Mapping[str, Any]],
        state: str = STATE_ACTIVE,
        domain_name: str = "",
        instance_example: str = "",
        note: str = "",
        by: str = "",
    ) -> dict[str, Any]:
        """수동 추가(origin manual · basis manual)."""
        if not valid_key(source_id, domain_id, scope, match_key):
            raise ValueError("매핑 키로 쓸 수 없는 source_id·domain_id·scope·match_key")
        if state not in (STATE_ACTIVE, STATE_DISABLED):
            raise ValueError(f"수동 매핑 state는 active·disabled만: {state!r}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO apm_host_map
                    (source_id, domain_id, scope, match_key, domain_name, instance_example,
                     origin, basis, state, verified, targets, evidence, note,
                     created_by, updated_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE, $10::jsonb, '{}'::jsonb,
                        $11, $12, $12)
                ON CONFLICT (source_id, domain_id, scope, match_key) DO NOTHING
                RETURNING *
                """,
                source_id, domain_id, scope, match_key, domain_name, instance_example,
                ORIGIN_MANUAL, BASIS_MANUAL, state, _dumps(normalize_targets(list(targets))),
                note, by,
            )
            if row is None:
                existing = await conn.fetchval(
                    "SELECT id FROM apm_host_map WHERE source_id = $1 AND domain_id = $2"
                    " AND scope = $3 AND match_key = $4",
                    source_id, domain_id, scope, match_key,
                )
                raise ApmHostMapExistsError(int(existing or 0))
        return _row(row)

    async def _version_miss(self, conn: Any, map_id: int) -> None:
        """조건부 쓰기가 0행 — 행이 있으면 version 불일치로 올린다(없으면 그대로 반환)."""
        current = await conn.fetchval("SELECT version FROM apm_host_map WHERE id = $1", map_id)
        if current is not None:
            raise ApmHostMapVersionError(map_id, int(current))

    async def update(
        self,
        map_id: int,
        *,
        version: int,
        targets: Sequence[Mapping[str, Any]] | None = None,
        state: str | None = None,
        note: str | None = None,
        by: str = "",
    ) -> dict[str, Any] | None:
        """관리자 수정.

        origin manual · 대상을 바꾸면 basis manual · 「변경됨」·scan_suggests 해소.
        """
        if state is not None and state not in (STATE_ACTIVE, STATE_DISABLED):
            raise ValueError(f"관리자 수정 state는 active·disabled만: {state!r}")
        targets_json = _dumps(normalize_targets(list(targets))) if targets is not None else None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE apm_host_map SET
                    targets = COALESCE($3::jsonb, targets),
                    basis = CASE WHEN $3::jsonb IS NULL THEN basis ELSE $7 END,
                    state = COALESCE($4, state),
                    note = COALESCE($5, note),
                    origin = $8,
                    evidence = evidence - 'changed' - 'scan_suggests',
                    next_retry_at = NULL,
                    updated_by = $6, updated_at = NOW(), version = version + 1
                WHERE id = $1 AND version = $2
                RETURNING *
                """,
                int(map_id), int(version), targets_json, state, note, by,
                BASIS_MANUAL, ORIGIN_MANUAL,
            )
            if row is None:
                await self._version_miss(conn, int(map_id))
                return None
        return _row(row)

    async def verify(self, map_id: int, *, version: int, by: str = "") -> dict[str, Any] | None:
        """자동 결과 확인(verified=true · 「변경됨」 해소)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE apm_host_map SET
                    verified = TRUE, evidence = evidence - 'changed',
                    updated_by = $3, updated_at = NOW(), version = version + 1
                WHERE id = $1 AND version = $2
                RETURNING *
                """,
                int(map_id), int(version), by,
            )
            if row is None:
                await self._version_miss(conn, int(map_id))
                return None
        return _row(row)

    async def unverify(
        self, map_id: int, *, version: int, by: str = ""
    ) -> dict[str, Any] | None:
        """확인 취소(verified=false만 — 대상·상태·evidence는 그대로)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE apm_host_map SET
                    verified = FALSE,
                    updated_by = $3, updated_at = NOW(), version = version + 1
                WHERE id = $1 AND version = $2
                RETURNING *
                """,
                int(map_id), int(version), by,
            )
            if row is None:
                await self._version_miss(conn, int(map_id))
                return None
        return _row(row)

    async def delete(self, map_id: int, *, version: int) -> dict[str, Any] | None:
        """삭제 → 지운 행(없으면 None)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "DELETE FROM apm_host_map WHERE id = $1 AND version = $2 RETURNING *",
                int(map_id), int(version),
            )
            if row is None:
                await self._version_miss(conn, int(map_id))
                return None
        return _row(row)

    # ── 자동 반영 ───────────────────────────────────────────────────────
    async def apply_auto(
        self,
        source_id: str,
        proposal: AutoProposal,
        *,
        now: datetime | None = None,
        retry_hours: int = 24,
    ) -> tuple[str, dict[str, Any] | None]:
        """자동 제안 1건 → `(action, 행)` — 행 잠금 트랜잭션 안에서 쓰기 규칙을 판정한다."""
        if not valid_key(source_id, proposal.domain_id, proposal.scope, proposal.match_key):
            raise ValueError("매핑 키로 쓸 수 없는 자동 제안")
        if proposal.state not in STATES or proposal.state == STATE_DISABLED:
            raise ValueError(f"자동 제안 state 오류: {proposal.state!r}")
        at = _now(now)
        key = (source_id, proposal.domain_id, proposal.scope, proposal.match_key)
        for _attempt in range(_INSERT_ATTEMPTS):
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    current = await conn.fetchrow(
                        "SELECT * FROM apm_host_map WHERE source_id = $1 AND domain_id = $2"
                        " AND scope = $3 AND match_key = $4 FOR UPDATE",
                        *key,
                    )
                    existing = _row(current) if current is not None else None
                    write = plan_auto_write(existing, proposal, now_iso=at.isoformat())
                    retry_at = next_retry_at(write.state, at, retry_hours)
                    if existing is None:
                        row = await conn.fetchrow(
                            """
                            INSERT INTO apm_host_map
                                (source_id, domain_id, scope, match_key, domain_name,
                                 instance_example, origin, basis, state, verified, targets,
                                 evidence, created_by, updated_by, created_at, updated_at,
                                 last_seen_at, next_retry_at)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE, $10::jsonb,
                                    $11::jsonb, $12, $12, $13, $13, $13, $14)
                            ON CONFLICT (source_id, domain_id, scope, match_key) DO NOTHING
                            RETURNING *
                            """,
                            *key, proposal.domain_name, proposal.instance_example, ORIGIN_AUTO,
                            write.basis, write.state, _dumps(list(write.targets)),
                            _dumps(write.evidence), _AUTO_BY, at, retry_at,
                        )
                        if row is None:
                            continue  # 같은 키 동시 삽입 — 다시 읽어 규칙을 적용한다
                        return WRITE_INSERT, _row(row)
                    row = await self._apply_existing(conn, existing, write, at, retry_at,
                                                     proposal)
                    return write.action, (_row(row) if row is not None else existing)
        raise RuntimeError("apm_host_map 자동 반영 — 같은 키 동시 삽입 경쟁이 반복됨")

    async def _apply_existing(
        self,
        conn: Any,
        existing: Mapping[str, Any],
        write: MapWrite,
        at: datetime,
        retry_at: datetime | None,
        proposal: AutoProposal,
    ) -> Any:
        """기존 행에 쓰기 판정을 반영한다(update·suggest·same·skip) → 갱신 행."""
        map_id = int(existing["id"])
        action = write.action
        if action == WRITE_UPDATE:
            return await conn.fetchrow(
                """
                UPDATE apm_host_map SET
                    basis = $2, state = $3, targets = $4::jsonb, evidence = $5::jsonb,
                    domain_name = COALESCE(NULLIF($6, ''), domain_name),
                    instance_example = COALESCE(NULLIF($7, ''), instance_example),
                    updated_by = $8, updated_at = $9, last_seen_at = $9, next_retry_at = $10,
                    version = version + 1
                WHERE id = $1
                RETURNING *
                """,
                map_id, write.basis, write.state, _dumps(list(write.targets)),
                _dumps(write.evidence), proposal.domain_name, proposal.instance_example,
                _AUTO_BY, at, retry_at,
            )
        if action == WRITE_SUGGEST:
            return await conn.fetchrow(
                "UPDATE apm_host_map SET evidence = $2::jsonb, last_seen_at = $3,"
                " version = version + 1 WHERE id = $1 RETURNING *",
                map_id, _dumps(write.evidence), at,
            )
        if action == WRITE_SAME:
            return await conn.fetchrow(
                "UPDATE apm_host_map SET updated_at = $2, last_seen_at = $2, next_retry_at = $3"
                " WHERE id = $1 RETURNING *",
                map_id, at, retry_at,
            )
        if action == WRITE_SKIP:
            return await conn.fetchrow(
                "UPDATE apm_host_map SET last_seen_at = $2 WHERE id = $1 RETURNING *",
                map_id, at,
            )
        raise ValueError(f"알 수 없는 쓰기 판정: {action!r}")

    async def apply_auto_many(
        self,
        source_id: str,
        proposals: Sequence[AutoProposal],
        *,
        now: datetime | None = None,
        retry_hours: int = 24,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """여러 건 — 행별 개별 트랜잭션 · 실패 행은 `("error", None)`(사유는 로그)."""
        out: list[tuple[str, dict[str, Any] | None]] = []
        for proposal in proposals:
            try:
                out.append(await self.apply_auto(
                    source_id, proposal, now=now, retry_hours=retry_hours,
                ))
            except Exception:  # noqa: BLE001 — 한 행 실패가 나머지를 막지 않는다
                logger.warning(
                    "apm_host_map 자동 반영 실패: source=%s domain=%s scope=%s key=%s",
                    source_id, proposal.domain_id, proposal.scope, proposal.match_key,
                    exc_info=True,
                )
                out.append(("error", None))
        return out

    async def touch(self, map_ids: Sequence[int], *, at: datetime | None = None) -> int:
        """`last_used_at` 묶음 갱신 → 갱신 행 수."""
        ids = sorted({int(i) for i in map_ids if int(i) > 0})
        if not ids:
            return 0
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE apm_host_map SET last_used_at = $2 WHERE id = ANY($1::bigint[])",
                ids, _now(at),
            )
        return _count(status)

    async def set_targets_gone(
        self, present: Collection[tuple[str, str]], scanned_db_ids: Collection[str]
    ) -> int:
        """스캔 결과로 전 행의 `target_gone`을 다시 매긴다 → 바뀐 행 수(행별 갱신).

        읽은 `version`이 그대로일 때만 쓴다(낙관적 잠금) — 그 사이 관리자 수정·자동 반영이 바꾼
        행은 덮지 않고 건너뛴다(건수 로그 · 다음 스캔이 다시 매긴다).
        """
        if not scanned_db_ids:
            return 0
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, version, targets FROM apm_host_map")
            changed = 0
            raced = 0
            for r in rows:
                new = mark_targets_gone(
                    _json_load(r["targets"], []), present, scanned_db_ids,
                )
                if new is None:
                    continue
                status = await conn.execute(
                    "UPDATE apm_host_map SET targets = $2::jsonb, version = version + 1"
                    " WHERE id = $1 AND version = $3",
                    int(r["id"]), _dumps(new), int(r["version"]),
                )
                if _count(status):
                    changed += 1
                else:
                    raced += 1
        if raced:
            logger.info("apm_host_map target_gone — 읽은 뒤 바뀐 행 건너뜀: %d건", raced)
        return changed

    async def cleanup(self, *, now: datetime | None = None) -> dict[str, int]:
        """정리 — 자동·미확인 행만(관리자 행·disabled 유지) · 실행 기록 90일."""
        at = _now(now)
        async with self._pool.acquire() as conn:
            idle = await conn.execute(
                """
                DELETE FROM apm_host_map
                WHERE origin = $1 AND verified = FALSE AND state <> $2 AND scope = $3
                  AND GREATEST(COALESCE(last_used_at, created_at),
                               COALESCE(last_seen_at, created_at)) < $4
                """,
                ORIGIN_AUTO, STATE_DISABLED, SCOPE_INSTANCE,
                at - timedelta(days=AUTO_INSTANCE_IDLE_DAYS),
            )
            unseen = await conn.execute(
                """
                DELETE FROM apm_host_map
                WHERE origin = $1 AND verified = FALSE AND state <> $2
                  AND COALESCE(last_seen_at, created_at) < $3
                """,
                ORIGIN_AUTO, STATE_DISABLED, at - timedelta(days=AUTO_UNSEEN_DAYS),
            )
            runs = await conn.execute(
                "DELETE FROM apm_host_map_scan_run WHERE started_at < $1",
                at - timedelta(days=SCAN_RUN_RETENTION_DAYS),
            )
        return {
            "instance_idle": _count(idle),
            "unseen": _count(unseen),
            "scan_runs": _count(runs),
        }

    # ── 스캔 실행 기록 ─────────────────────────────────────────────────
    async def start_scan_run(
        self, *, requested_by: str, run_id: str | None = None
    ) -> dict[str, Any]:
        """스캔 실행 기록 생성(status running)."""
        rid = run_id or uuid.uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO apm_host_map_scan_run (run_id, requested_by, status)"
                " VALUES ($1, $2, $3) RETURNING *",
                rid, requested_by, SCAN_RUNNING,
            )
        return _row(row)

    async def update_scan_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        scanners: Mapping[str, Any] | None = None,
        link_summary: Mapping[str, Any] | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        """실행 기록 갱신(준 칸만)."""
        if status is not None and status not in SCAN_STATUSES:
            raise ValueError(f"알 수 없는 스캔 상태: {status!r}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE apm_host_map_scan_run SET
                    status = COALESCE($2, status),
                    scanners = COALESCE($3::jsonb, scanners),
                    link_summary = COALESCE($4::jsonb, link_summary),
                    finished_at = CASE WHEN $5 THEN NOW() ELSE finished_at END
                WHERE run_id = $1
                RETURNING *
                """,
                run_id, status,
                _dumps(dict(scanners)) if scanners is not None else None,
                _dumps(dict(link_summary)) if link_summary is not None else None,
                bool(finished),
            )
        return _row(row) if row is not None else None

    async def get_scan_run(self, run_id: str) -> dict[str, Any] | None:
        """실행 기록 단건."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM apm_host_map_scan_run WHERE run_id = $1", run_id,
            )
        return _row(row) if row is not None else None

    async def list_scan_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """실행 기록 목록(최근 먼저)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM apm_host_map_scan_run ORDER BY started_at DESC LIMIT $1",
                max(1, min(int(limit), _MAX_LIST_LIMIT)),
            )
        return [_row(r) for r in rows]

    async def abort_running_scan_runs(self) -> int:
        """기동 시 남은 `running` 기록 → `aborted`."""
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE apm_host_map_scan_run SET status = $1, finished_at = NOW()"
                " WHERE status = $2",
                SCAN_ABORTED, SCAN_RUNNING,
            )
        return _count(status)

    # ── 스냅샷 ─────────────────────────────────────────────────────────
    async def load_snapshot(
        self, table: str, run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """스냅샷 행 적재(한 트랜잭션 · 같은 run 재적재는 덮는다) → 적재 행 수."""
        columns = _snapshot_table(table)
        names = ", ".join(("run_id", *columns))
        marks = ", ".join(f"${i}" for i in range(1, len(columns) + 2))
        records = [(run_id, *(r.get(c) for c in columns)) for r in rows]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"DELETE FROM {table} WHERE run_id = $1", run_id)
                if records:
                    await conn.executemany(
                        f"INSERT INTO {table} ({names}) VALUES ({marks})", records,
                    )
        return len(records)

    async def swap_snapshot(self, table: str, run_id: str) -> None:
        """「현재 스냅샷」 포인터 교체 + 이전 run 행 삭제(한 트랜잭션)."""
        _snapshot_table(table)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO apm_host_map_snapshot_current (snapshot, run_id, swapped_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (snapshot) DO UPDATE
                        SET run_id = EXCLUDED.run_id, swapped_at = EXCLUDED.swapped_at
                    """,
                    table, run_id,
                )
                await conn.execute(f"DELETE FROM {table} WHERE run_id <> $1", run_id)

    async def current_snapshot_run(self, table: str) -> str | None:
        """현재 스냅샷 run_id(없으면 None)."""
        _snapshot_table(table)
        async with self._pool.acquire() as conn:
            run_id = await conn.fetchval(
                "SELECT run_id FROM apm_host_map_snapshot_current WHERE snapshot = $1", table,
            )
        return str(run_id) if run_id is not None else None

    async def snapshot_rows(
        self,
        table: str,
        *,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """현재 스냅샷 행(검색어 · 페이지 선택) — 현재 포인터가 없으면 빈 목록."""
        columns = _snapshot_table(table)
        run_id = await self.current_snapshot_run(table)
        if run_id is None:
            return []
        args: list[Any] = [run_id]
        sql = f"SELECT {', '.join(('id', 'run_id', *columns))} FROM {table} WHERE run_id = $1"
        if query and query.strip():
            args.append(_like(query.strip()))
            text_cols = _SNAPSHOT_TEXT_COLUMNS[table]
            sql += " AND (" + " OR ".join(f"{c} ILIKE $2" for c in text_cols) + ")"
        sql += " ORDER BY id"
        if limit is not None:
            args.append(max(1, int(limit)))
            sql += f" LIMIT ${len(args)}"
        if offset:
            args.append(max(0, int(offset)))
            sql += f" OFFSET ${len(args)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row(r) for r in rows]


__all__ = [
    "SNAPSHOT_COLUMNS",
    "PostgresApmHostMapRepository",
]
