"""토폴로지 의존성 그래프 로더 (Plan 60 E4 · D-080 — 읽기전용 인프라).

`cmm_resource`의 가용성 의존 컬럼으로 정적 엣지를 로드해 domain `DependencyGraph`를
구성하고(장기 캐시), 조상 리소스의 동적 비정상 상태(AVAIL_STATUS≠0)를 매 이벤트 신선
조회한다. 정적/동적을 분리한다(§6.2):

    - **정적 엣지(장기 캐시)**: `SELECT ID, NAME, AVAIL_DEPEND_RESOURCE_ID,
      AVAIL_DEPEND_RESOURCE_ID_2 FROM polestar.cmm_resource WHERE DTIME IS NULL
      AND AVAIL_DEPEND_RESOURCE_ID IS NOT NULL`(엣지 보유 행만) → db_id별
      topology_cache_ttl_seconds 캐시 → DependencyGraph.
    - **동적 상태(매 이벤트)**: 조상 ID 집합을 `WHERE ID IN (...) AND DTIME IS NULL`로
      AVAIL_STATUS 신선 조회(행 수=홉 상한으로 유계) → 비정상 집합(AVAIL_STATUS≠0) 반환.

안전 제약:
    - 읽기 전용 SELECT 단일문만. DML/DDL 금지. 외부 입력 ID는 `_sql_literal`로 이스케이프.
    - PostgreSQL 방언(`polestar.` 스키마 한정)은 기존 polestar_noise_context 패턴 재사용.
      b0(DB2)/비PostgreSQL·조회 실패 → None(호출부 1홉 폴백·비억제, 보수적).
    - DBHub(MCP) 실행 경로는 polestar_noise_context.fetch가 쓰는 `client.execute_sql`을
      그대로 사용한다(호출부가 열어 준 client 재사용 — 별도 커넥션 열지 않음).
    - 폐쇄망 로컬.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from src.alarm.domain.topology import DependencyGraph

# 스키마 한정·리터럴 이스케이프·행 조회는 노이즈 컨텍스트와 단일 출처 공유(동일 infra 계층).
from src.alarm.infrastructure.polestar_noise_context import (
    _row_value,
    _sql_literal,
    _table,
)

logger = logging.getLogger(__name__)


def build_edges_sql(db_id: str) -> str:
    """정적 의존 엣지 로드 SQL을 조립한다(엣지 보유 행만 — 전량 스캔 아님).

    NAME은 name_of 역조회용, `AVAIL_DEPEND_RESOURCE_ID(_2)`는 부모(가용성 의존) ID다.
    읽기 전용 SELECT 단일문. RESOURCE_CONF_ID JOIN 미포함(D-022).
    """
    t_resource = _table(db_id, "cmm_resource")
    return (
        "SELECT\n"
        "    ID,\n"
        "    NAME,\n"
        "    AVAIL_DEPEND_RESOURCE_ID,\n"
        "    AVAIL_DEPEND_RESOURCE_ID_2\n"
        f"FROM {t_resource}\n"
        "WHERE DTIME IS NULL\n"
        "  AND AVAIL_DEPEND_RESOURCE_ID IS NOT NULL"
    )


def build_ancestor_status_sql(db_id: str, ancestor_ids: list[str]) -> str:
    """조상 리소스들의 동적 가용 상태(AVAIL_STATUS)와 NAME을 신선 조회하는 SQL을 조립한다.

    행 수는 조상 수(=홉 상한으로 유계)라 IN 절이 유계다. 각 ID는 `_sql_literal`로
    이스케이프한다. ancestor_ids가 비면 빈 문자열을 반환한다(호출부가 조회 스킵).
    NAME은 root(부모 없는 최상위 조상)의 이름 역조회용 — 엣지 로더는 엣지 보유 행만
    담으므로 부모 없는 root의 NAME이 누락될 수 있어, 이 IN 조회로 신선 보강한다.
    읽기 전용 SELECT 단일문. RESOURCE_CONF_ID JOIN 미포함(D-022).
    """
    if not ancestor_ids:
        return ""
    t_resource = _table(db_id, "cmm_resource")
    in_list = ", ".join(_sql_literal(str(i)) for i in ancestor_ids)
    return (
        "SELECT\n"
        "    ID,\n"
        "    NAME,\n"
        "    AVAIL_STATUS\n"
        f"FROM {t_resource}\n"
        f"WHERE ID IN ({in_list})\n"
        "  AND DTIME IS NULL"
    )


class TopologyLoader:
    """정적 엣지 그래프 로더 + 동적 비정상 상태 조회기(db_id별 캐시 보유).

    repo가 인스턴스 하나를 보유·재사용하므로 캐시가 이벤트 간 유지된다(단일 워커).
    """

    def __init__(self) -> None:
        # db_id → (만료 epoch, DependencyGraph). 정적 엣지만 캐시(동적 상태는 매번 조회).
        self._cache: dict[str, tuple[float, DependencyGraph]] = {}

    async def load_graph(
        self,
        client,  # noqa: ANN001 — DBClient (execute_sql)
        db_id: str,
        *,
        cache_ttl_seconds: int,
        max_hops: int,
        now: Optional[float] = None,
    ) -> Optional[DependencyGraph]:
        """정적 의존 그래프를 로드한다(캐시 히트 시 재조회 없음). 실패 시 None(1홉 폴백).

        Args:
            client: 호출부가 열어 준 DBClient(execute_sql). 별도 커넥션을 열지 않는다.
            db_id: 대상 폴스타 DB.
            cache_ttl_seconds: 정적 그래프 캐시 TTL(변경 드묾 — 장기).
            max_hops: 구성될 DependencyGraph의 기본 홉 상한.
            now: 캐시 판정 기준 시각(테스트 주입용, 기본 time.time()).
        """
        clock = time.time() if now is None else now
        cached = self._cache.get(db_id)
        if cached is not None and clock < cached[0]:
            return cached[1]
        try:
            result = await client.execute_sql(build_edges_sql(db_id))
            rows = list(getattr(result, "rows", []) or [])
        except Exception as exc:  # noqa: BLE001 — 조회 실패 → None(1홉 폴백)
            logger.warning("토폴로지 엣지 로드 실패 — 1홉 폴백: db_id=%s: %s", db_id, exc)
            return None

        edges: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        for row in rows:
            rid = _row_value(row, "id")
            if rid is None or rid == "":
                continue
            rid = str(rid)
            name = _row_value(row, "name")
            if name is not None:
                names[rid] = str(name)
            parents: list[str] = []
            for col in ("avail_depend_resource_id", "avail_depend_resource_id_2"):
                pid = _row_value(row, col)
                if pid is not None and pid != "":
                    parents.append(str(pid))
            if parents:
                edges.setdefault(rid, []).extend(parents)

        graph = DependencyGraph(edges, names, max_hops=max_hops)
        self._cache[db_id] = (clock + max(cache_ttl_seconds, 0), graph)
        return graph

    async def fetch_ancestor_state(
        self,
        client,  # noqa: ANN001 — DBClient (execute_sql)
        db_id: str,
        ancestor_ids: list[str],
    ) -> Optional[tuple[set[str], dict[str, str]]]:
        """조상 ID들의 (비정상 집합 AVAIL_STATUS≠0, id→NAME 맵)을 신선 조회한다.

        실패 시 None(1홉 폴백). ancestor_ids가 비면 (빈 집합, 빈 맵)(연쇄 조상 없음 —
        조회 불필요). 파싱 불가 행은 건너뛴다. NAME 맵은 root 이름 역조회 보강용
        (엣지 로더가 담지 못한 부모 없는 root의 NAME을 여기서 확보).
        """
        if not ancestor_ids:
            return set(), {}
        try:
            result = await client.execute_sql(
                build_ancestor_status_sql(db_id, ancestor_ids)
            )
            rows = list(getattr(result, "rows", []) or [])
        except Exception as exc:  # noqa: BLE001 — 조회 실패 → None(1홉 폴백)
            logger.warning(
                "토폴로지 비정상 상태 조회 실패 — 1홉 폴백: db_id=%s: %s", db_id, exc
            )
            return None

        abnormal: set[str] = set()
        names: dict[str, str] = {}
        for row in rows:
            rid = _row_value(row, "id")
            if rid is None or rid == "":
                continue
            rid = str(rid)
            name = _row_value(row, "name")
            if name is not None:
                names[rid] = str(name)
            status = _row_value(row, "avail_status")
            if status is None or status == "":
                continue
            try:
                if int(status) != 0:
                    abnormal.add(rid)
            except (TypeError, ValueError):
                continue
        return abnormal, names
