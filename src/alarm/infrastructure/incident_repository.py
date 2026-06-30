"""PostgreSQL 기반 incident 라이프사이클 저장소 구현 (Plan 52 §9.1 · D-049).

`alarm_incidents` 테이블에 incident open/ack/resolved 상태 전이를 영속하고,
시간창 집계(MTTA/MTTR/사건전환율)를 SQL로 산출한다.
`audit_repository.PostgresAuditRepository` 구조(풀 acquire + graceful)를 미러한다.

쓰기 단일화(D-049): 워커가 PG 풀을 신설하지 않고, incident 이벤트를 Redis로 발행하면
API 프로세스의 단일 라이터(subscriber)가 이 저장소로 영속한다.
모든 메서드는 graceful — 예외를 삼키고 안전값을 반환하여 파이프라인·서버를 막지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import asyncpg

from src.alarm.domain.incident_store import IncidentStore

logger = logging.getLogger(__name__)


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """asyncpg Record를 incident 딕셔너리로 변환한다(시각은 ISO8601 문자열)."""

    def _iso(value) -> Any:  # noqa: ANN001
        return value.isoformat() if isinstance(value, datetime) else None

    return {
        "id": row["id"],
        "fingerprint": row["fingerprint"],
        "alarm_id": row["alarm_id"],
        "db_id": row["db_id"],
        "server_name": row["server_name"],
        "severity": row["severity"],
        "priority": row["priority"],
        "tier": row["tier"],
        "status": row["status"],
        "created_at": _iso(row["created_at"]),
        "acked_at": _iso(row["acked_at"]),
        "acked_by": row["acked_by"],
        "resolved_at": _iso(row["resolved_at"]),
        "resolution": row["resolution"],
    }


def _empty_metrics() -> dict[str, Any]:
    """None-안전한 빈 지표 dict(전 키 포함)를 반환한다."""
    return {
        "mtta_seconds": None,
        "incident_mttr_seconds": None,
        "incident_conversion_rate": None,
        "incident_count": 0,
        "open_count": 0,
    }


class PostgresIncidentStore(IncidentStore):
    """PostgreSQL 기반 incident 라이프사이클 저장소."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_open(
        self,
        *,
        fingerprint: str,
        alarm_id: str,
        db_id: str,
        server_name: str,
        severity: int,
        priority: str,
        tier: str,
        created_at: datetime,
    ) -> int:
        """open incident를 INSERT하고 생성된 id를 반환한다(실패 시 0)."""
        try:
            async with self._pool.acquire() as conn:
                incident_id = await conn.fetchval(
                    """
                    INSERT INTO alarm_incidents
                        (fingerprint, alarm_id, db_id, server_name,
                         severity, priority, tier, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'open', $8)
                    RETURNING id
                    """,
                    fingerprint,
                    alarm_id,
                    db_id,
                    server_name,
                    severity,
                    priority,
                    tier,
                    created_at,
                )
            return int(incident_id) if incident_id is not None else 0
        except Exception as e:  # noqa: BLE001 — 영속 실패가 발송을 막지 않는다
            logger.error("incident open 생성 실패: %s", e)
            return 0

    async def resolve_by_fingerprint(
        self,
        *,
        fingerprint: str,
        resolved_at: datetime,
        resolution: str,
    ) -> int:
        """열린 incident 중 가장 최근 1건을 resolved로 UPDATE한다(영향 행 수 반환).

        경합(같은 fingerprint 복수 open) 시 `ORDER BY created_at DESC LIMIT 1`로
        가장 최근 1건만 해소한다(D-049 기본 정책). 매칭 open이 없으면 0.
        """
        try:
            async with self._pool.acquire() as conn:
                target_id = await conn.fetchval(
                    """
                    SELECT id FROM alarm_incidents
                    WHERE fingerprint = $1 AND status = 'open'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    fingerprint,
                )
                if target_id is None:
                    return 0
                result = await conn.execute(
                    """
                    UPDATE alarm_incidents
                    SET status = 'resolved', resolved_at = $2, resolution = $3
                    WHERE id = $1 AND status = 'open'
                    """,
                    target_id,
                    resolved_at,
                    resolution,
                )
            # asyncpg execute returns "UPDATE N"
            return int(result.split()[-1]) if result else 0
        except Exception as e:  # noqa: BLE001
            logger.error("incident resolved 갱신 실패: %s", e)
            return 0

    async def ack(
        self,
        *,
        incident_id: int,
        acked_at: datetime,
        acked_by: str,
    ) -> bool:
        """open incident를 ack로 전이한다(이미 ack/resolved면 영향 0 → False)."""
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE alarm_incidents
                    SET status = 'ack', acked_at = $2, acked_by = $3
                    WHERE id = $1 AND status = 'open'
                    """,
                    incident_id,
                    acked_at,
                    acked_by,
                )
            affected = int(result.split()[-1]) if result else 0
            return affected > 0
        except Exception as e:  # noqa: BLE001
            logger.error("incident ack 실패: %s", e)
            return False

    async def list_open(self, *, limit: int = 100) -> list[dict]:
        """열린 incident를 최신순으로 조회한다(실패 시 빈 리스트)."""
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, fingerprint, alarm_id, db_id, server_name,
                           severity, priority, tier, status,
                           created_at, acked_at, acked_by, resolved_at, resolution
                    FROM alarm_incidents
                    WHERE status = 'open'
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            return [_row_to_dict(row) for row in rows]
        except Exception as e:  # noqa: BLE001
            logger.error("열린 incident 조회 실패: %s", e)
            return []

    async def metrics(self, *, window_seconds: int, page_count: int) -> dict:
        """시간창 내 MTTA/MTTR/사건전환율/열린 건수를 집계한다(None-안전).

        - mtta_seconds = AVG(acked_at - created_at) (ack된 건만)
        - incident_mttr_seconds = AVG(resolved_at - created_at) (해소된 건만)
        - incident_count = 창 내 created_at 기준 incident 건수
        - incident_conversion_rate = incident_count / page_count (0분모면 None)
        - open_count = 현재 열린 incident 수(창 무관)
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        AVG(EXTRACT(EPOCH FROM (acked_at - created_at)))
                            FILTER (WHERE acked_at IS NOT NULL)    AS mtta_seconds,
                        AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)))
                            FILTER (WHERE resolved_at IS NOT NULL) AS mttr_seconds,
                        COUNT(*)                                   AS incident_count
                    FROM alarm_incidents
                    WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                    """,
                    str(int(window_seconds)),
                )
                open_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM alarm_incidents WHERE status = 'open'"
                )

            mtta = row["mtta_seconds"] if row else None
            mttr = row["mttr_seconds"] if row else None
            incident_count = (row["incident_count"] if row else 0) or 0
            conversion = (
                (incident_count / page_count) if page_count else None
            )
            return {
                "mtta_seconds": round(float(mtta), 2) if mtta is not None else None,
                "incident_mttr_seconds": (
                    round(float(mttr), 2) if mttr is not None else None
                ),
                "incident_conversion_rate": (
                    round(float(conversion), 4) if conversion is not None else None
                ),
                "incident_count": int(incident_count),
                "open_count": int(open_count or 0),
            }
        except Exception as e:  # noqa: BLE001
            logger.error("incident 지표 집계 실패: %s", e)
            return _empty_metrics()
