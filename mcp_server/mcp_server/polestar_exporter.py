"""폴스타 → OpenMetrics 브리지 (plans/92 트랙 B-2 · O5 · §4.5 [v3]).

``GET /metrics``(``custom_route``)로 폴스타 서버 정보·사용률·활성 알람을 노출해 Prometheus가
스크레이프하게 한다. 이 모듈에는 **SQL과 패밀리 정의만** 둔다 — 직렬화·Accept 협상·캐시·절단
gauge·종료 배선은 벤더 중립 ``om_exposition``이 맡는다(plans/87 J7 ``/metrics/apm``과 공유).
폴스타 리터럴이 있으므로 overfit 스캔에서 제외한다(``polestar_tools.py``와 대칭).

라벨 규약:
    - ``nodename`` = 폴스타 **server_name**(``cmm_resource.name`` · D-119 ③). OS ``hostname``
      칼럼과 다르다(공동존은 name ≠ hostname — D-046). node_exporter 쪽은
      ``static_configs.labels.nodename``을 이 값에 맞추면 한 TSDB에서 조인된다.
    - 서버별 패밀리에는 모두 ``db_id``를 둔다 — 존 간 server_name이 겹쳐도 시리즈가 중복되지 않는다
      (중복 시리즈는 Prometheus가 스크레이프 전체를 거부한다).
    - ``ipaddress``·``os``는 info 패밀리에만 싣는다(plans/92 §7.3).

착수 조건 5건(§4.5 [v3]) 반영:
    1. DB 접근 — ``custom_route`` 핸들러는 세션 lifespan의 풀에 닿지 않는다(P-5). 브리지 **전용 지연
       풀**을 첫 스크레이프 때 만든다: ``bridge_sources`` 중 활성 소스만, 소스당
       ``pool_min_size=1``·``pool_max_size=1``(``DBPoolManager`` 재사용 — DB2는 풀 없이 요청별
       동기 연결을 스레드에서 연다). 앱 종료 시 ``close_all``. 기존 도구 lifespan은 건드리지 않는다.
    2. 단위 — ``polestar_metric_utilization_percent``의 kind는 cpu·memory·filesystem 3종뿐이다.
       ``disk_io``(``MaxIORate``)는 퍼센트가 아니고 단위 미실측이라 1차에서 제외한다.
    3. 신선도 — 샘플에 명시 타임스탬프를 달지 않는다. 대신 ``…_stat_timestamp_seconds``로 그 값이
       몇 시 통계인지 낸다. 캐시 TTL = ``bridge_cache_seconds``(기본 300).
    4. 일괄 SQL — 소스별로 서버 정보 1쿼리 · "서버×kind 최신 1행" 1쿼리(``ROW_NUMBER``) · 활성 알람
       집계 1쿼리. ``stat_date`` 하한은 파이썬이 계산해 리터럴로 넣는다(스캔 축소).
    5. 행 상한 — 각 쿼리 행 제한은 소스 ``max_rows``다. 받은 행 수가 상한에 닿으면 자르지 않고
       ``polestar_bridge_truncated{db_id}`` 1로 알린다.

소스 조회 실패는 조용히 빼지 않는다 — 그 소스의 시리즈를 내지 않고 ``polestar_bridge_source_up`` 0 +
WARNING. 미등록·비활성·비폴스타 엔진 소스도 같다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from prometheus_client.core import Metric

from mcp_server import sql_log
from mcp_server.config import AppServerConfig, SourceConfig
from mcp_server.db import DBPoolManager
from mcp_server.om_exposition import (
    ExpositionCache,
    flag_gauge,
    gauge_family,
    info_family,
    make_exposition_endpoint,
    register_shutdown,
)
from mcp_server.polestar_tools import (
    _METRIC_KIND_MAP,
    _METRIC_TABLE,
    _STAT_DATE_FMT,
    _q,
    _row_limit,
    _sql_literal,
)

logger = logging.getLogger(__name__)

# =====================================================================
# 노출 어휘 — O6 백필 스크립트가 이 모듈에서 임포트해 재사용한다
# =====================================================================

#: 노출 경로(``/metrics/apm``은 plans/87 J7 몫).
ROUTE_PATH = "/metrics"

#: info 패밀리 이름(샘플 이름은 ``polestar_server_info``).
METRIC_SERVER_INFO = "polestar_server"
METRIC_UTILIZATION = "polestar_metric_utilization_percent"
METRIC_STAT_TIMESTAMP = "polestar_metric_stat_timestamp_seconds"
METRIC_ALARM_ACTIVE = "polestar_alarm_active"
METRIC_BRIDGE_TRUNCATED = "polestar_bridge_truncated"
METRIC_BRIDGE_SOURCE_UP = "polestar_bridge_source_up"

LABEL_NODENAME = "nodename"
LABEL_DB_ID = "db_id"
LABEL_ZONE = "zone"
LABEL_IPADDRESS = "ipaddress"
LABEL_OS = "os"
LABEL_KIND = "kind"
LABEL_SEVERITY = "severity"

SERVER_INFO_LABELS: tuple[str, ...] = (
    LABEL_NODENAME, LABEL_DB_ID, LABEL_ZONE, LABEL_IPADDRESS, LABEL_OS,
)
UTILIZATION_LABELS: tuple[str, ...] = (LABEL_NODENAME, LABEL_DB_ID, LABEL_KIND)
ALARM_ACTIVE_LABELS: tuple[str, ...] = (LABEL_NODENAME, LABEL_DB_ID, LABEL_SEVERITY)

#: 노출하는 사용률 kind — 퍼센트 단위만(§4.5 [v3] 2). ``disk_io``(MaxIORate)는 제외.
EXPORTED_KINDS: tuple[str, ...] = ("cpu", "memory", "filesystem")
#: 원천 통계 해상도 — 시간 통계(``cmm_metric_stat_h``).
STAT_GRANULARITY = "h"
#: 최신 1행 탐색 범위(시간). 이보다 오래된 통계만 있는 서버는 사용률 시리즈를 내지 않는다
#: (서버 info는 그대로 나간다). 풀스캔을 피하려는 하한이다.
STAT_LOOKBACK_HOURS = 24

#: 브리지가 조회하는 엔진(폴스타 = PostgreSQL · DB2).
_SUPPORTED_ENGINES: frozenset[str] = frozenset({"postgresql", "db2"})

#: (resource_type, definition_name) → kind — 노출 kind만.
_KIND_BY_SOURCE: dict[tuple[str, str], str] = {
    _METRIC_KIND_MAP[kind]: kind for kind in EXPORTED_KINDS
}


# =====================================================================
# 시각 규약 — stat_date(varchar 버킷)
# =====================================================================


def stat_date_to_epoch(stat_date: Any) -> float:
    """시간 통계 버킷(``YYYYMMDDHH``)을 **버킷 시작** epoch 초로 바꾼다.

    ``stat_date``는 시간대 표기가 없는 DB 벽시계 값이다(``polestar_tools``의 ``_STAT_DATE_FMT``·
    ``_wall_clock`` 규약 — 서버는 DB 시간대를 모른다). 그래서 **mcp_server 호스트의 로컬 시각**으로
    해석한다. 이는 ``change_history_window_epochs``가 naive 기준시각을 서버 로컬로 해석하는 것과
    같은 좌표계이고, 조회 하한(``stat_date_lower_bound``)도 같은 로컬 시계로 계산한다.
    전제: mcp_server 호스트와 폴스타 DB의 시간대가 같다(△ 운영 실측 필요).

    ``strptime``만으로는 부족하다 — ``%d``·``%H``가 한 자리도 받아 ``20260922``(일 버킷)를
    ``2026-09-02 02시``로 읽는다. 그래서 숫자 10자리를 먼저 강제한다.

    Raises:
        ValueError: 형식이 ``YYYYMMDDHH``(숫자 10자리)가 아닌 경우.
    """
    text = "" if stat_date is None else str(stat_date).strip()
    if len(text) != 10 or not text.isdigit():
        raise ValueError(f"stat_date 형식이 YYYYMMDDHH가 아님: {stat_date!r}")
    return datetime.strptime(text, _STAT_DATE_FMT[STAT_GRANULARITY]).timestamp()


def stat_date_lower_bound(now: datetime, hours: int = STAT_LOOKBACK_HOURS) -> str:
    """``now − hours``를 ``stat_date`` 비교용 문자열(``YYYYMMDDHH``)로 만든다(리터럴 보간용)."""
    return (now - timedelta(hours=int(hours))).strftime(_STAT_DATE_FMT[STAT_GRANULARITY])


# =====================================================================
# 일괄 SQL 빌더 (순수 함수 — 읽기 전용 SELECT 단일문 · 방언 분기)
# =====================================================================


def build_server_info_sql(is_db2: bool, limit: int) -> str:
    """존(소스)의 전 서버 info 행을 조회하는 SQL — 서버당 1행(``GROUP BY svr.name``).

    OS 종류는 EAV ``OSType``을 **hostname 브릿지**로 읽는다(``build_os_config_sql``과 같은 앵커 —
    RESOURCE_CONF_ID 조인 금지 D-022 · cmm_os lookup 금지 D-028). 한 server_name에 행이 여럿이어도
    ``MAX``로 1행이 되므로 info 시리즈가 중복되지 않는다(문자열 ``MAX`` — 숫자 캐스트 불요).
    """
    t_res = _q(is_db2, "cmm_resource")
    t_prop = _q(is_db2, "core_config_prop")
    return (
        "SELECT\n"
        "    svr.name AS server_name,\n"
        "    MAX(svr.ipaddress) AS ipaddress,\n"
        "    MAX(p_os.stringvalue_short) AS os\n"
        f"FROM {t_res} svr\n"
        f"LEFT JOIN {t_prop} p_host ON p_host.name = {_sql_literal('Hostname')}\n"
        "                          AND p_host.stringvalue_short = svr.hostname\n"
        f"LEFT JOIN {t_prop} p_os ON p_os.configuration_id = p_host.configuration_id\n"
        f"                        AND p_os.name = {_sql_literal('OSType')}\n"
        "WHERE svr.resource_type = 'server.Server'\n"
        "  AND svr.dtime IS NULL\n"
        "GROUP BY svr.name\n"
        "ORDER BY svr.name\n"
        f"{_row_limit(is_db2, limit)}"
    )


def build_latest_utilization_sql(is_db2: bool, stat_date_from: str, limit: int) -> str:
    """존(소스)의 "서버×kind별 최신 1행" 사용률을 한 쿼리로 조회하는 SQL.

    조인 골격은 ``build_metric_trend_sql``과 같다(server.Server ─ platform_resource_id ─ 자식 ─
    통계). 서버 1대용 빌더와 달리 전 서버를 ``ROW_NUMBER() OVER (PARTITION BY server_name, kind
    ORDER BY stat_date DESC)``로 한 번에 자른다 — PostgreSQL·DB2 공통 구문.
    ``(server_name, kind)``당 1행이라 시리즈가 중복되지 않는다.
    값은 ``avg_val``(시간 평균 · 숫자 칼럼 — 집계 없음, 캐스트 불요).

    Args:
        is_db2: DB2 방언 여부.
        stat_date_from: ``stat_date`` 하한(``YYYYMMDDHH``) — 리터럴로 보간한다.
        limit: 행 제한(소스 ``max_rows``).
    """
    t_res = _q(is_db2, "cmm_resource")
    t_metric = _q(is_db2, _METRIC_TABLE[STAT_GRANULARITY])
    kind_pairs = "\n        OR ".join(
        f"(child.resource_type = {_sql_literal(resource_type)} "
        f"AND s.definition_name = {_sql_literal(definition_name)})"
        for resource_type, definition_name in (_METRIC_KIND_MAP[k] for k in EXPORTED_KINDS)
    )
    return (
        "SELECT\n"
        "    latest.server_name AS server_name,\n"
        "    latest.resource_type AS resource_type,\n"
        "    latest.definition_name AS definition_name,\n"
        "    latest.stat_date AS stat_date,\n"
        "    latest.avg_val AS avg_val\n"
        "FROM (\n"
        "    SELECT\n"
        "        svr.name AS server_name,\n"
        "        child.resource_type AS resource_type,\n"
        "        s.definition_name AS definition_name,\n"
        "        s.stat_date AS stat_date,\n"
        "        s.avg_val AS avg_val,\n"
        "        ROW_NUMBER() OVER (\n"
        "            PARTITION BY svr.name, child.resource_type, s.definition_name\n"
        "            ORDER BY s.stat_date DESC, child.id\n"
        "        ) AS rn\n"
        f"    FROM {t_res} svr\n"
        f"    JOIN {t_res} child ON child.platform_resource_id = svr.id\n"
        f"    JOIN {t_metric} s ON s.resource_id = child.id\n"
        "    WHERE svr.resource_type = 'server.Server'\n"
        "      AND svr.dtime IS NULL\n"
        "      AND child.dtime IS NULL\n"
        f"      AND ({kind_pairs})\n"
        f"      AND s.stat_date >= {_sql_literal(stat_date_from)}\n"
        ") latest\n"
        "WHERE latest.rn = 1\n"
        "ORDER BY latest.server_name, latest.resource_type\n"
        f"{_row_limit(is_db2, limit)}"
    )


def build_active_alarm_count_sql(is_db2: bool, limit: int) -> str:
    """존(소스)의 서버·심각도별 활성 알람 수를 집계하는 SQL.

    활성 = ``cmm_alarm_active`` 행 존재(``currentalarmstatus``는 ACK 칼럼이라 판정에 쓰지 않는다).
    서버 식별은 ``COALESCE(platform_resource_id, id)`` 승격 조인이다(``build_alarm_history_sql``과
    같은 골격 · RESOURCE_CONF_ID 미조인). ``nodename``이 server_name이므로 server.Server로 승격되지
    않는 알람은 이 패밀리에 들어가지 않는다. ``COUNT(*)`` — 숫자 캐스트 불요.
    """
    t_active = _q(is_db2, "cmm_alarm_active")
    t_res = _q(is_db2, "cmm_resource")
    return (
        "SELECT\n"
        "    svr.name AS server_name,\n"
        "    a.alarmseverity AS severity,\n"
        "    COUNT(*) AS alarm_count\n"
        f"FROM {t_active} a\n"
        f"JOIN {t_res} cr ON a.resource_id = cr.id\n"
        f"JOIN {t_res} svr ON svr.id = COALESCE(cr.platform_resource_id, cr.id)\n"
        "                 AND svr.resource_type = 'server.Server'\n"
        "                 AND svr.dtime IS NULL\n"
        "WHERE cr.dtime IS NULL\n"
        "GROUP BY svr.name, a.alarmseverity\n"
        "ORDER BY svr.name, a.alarmseverity\n"
        f"{_row_limit(is_db2, limit)}"
    )


# =====================================================================
# 소스 해석 · 스냅샷 → 패밀리 (순수 함수)
# =====================================================================


@dataclass(frozen=True)
class BridgeSource:
    """브리지 소스 1건. ``source``가 None이면 조회할 수 없는 소스다(``source_up`` 0)."""

    db_id: str
    zone: str
    source: SourceConfig | None


@dataclass
class SourceSnapshot:
    """소스 1건의 수집 결과. ``up``이 거짓이면 행 목록은 비어 있고 시리즈를 내지 않는다."""

    db_id: str
    zone: str
    up: bool
    truncated: bool = False
    servers: list[dict[str, Any]] = field(default_factory=list)
    utilization: list[dict[str, Any]] = field(default_factory=list)
    alarms: list[dict[str, Any]] = field(default_factory=list)


def resolve_bridge_sources(config: AppServerConfig) -> list[BridgeSource]:
    """``[[openmetrics.bridge_sources]]``를 활성 ``[[sources]]``와 대조한다(설정 순서 유지).

    - 미등록·비활성(연결 문자열 없음) 이름 → ``source=None`` + WARNING(``source_up`` 0으로 노출).
    - 폴스타 엔진(PostgreSQL·DB2)이 아닌 소스 → 같다.
    - 같은 이름이 두 번 오면 뒤의 것을 버린다 + WARNING(중복 ``db_id`` 시리즈는 스크레이프
      거부 원인).
    """
    active = {s.name: s for s in config.sources if s.connection}
    resolved: list[BridgeSource] = []
    seen: set[str] = set()
    for entry in config.openmetrics.bridge_sources:
        if entry.name in seen:
            logger.warning("폴스타 브리지 소스 중복 — 뒤 항목 무시: %s", entry.name)
            continue
        seen.add(entry.name)
        src = active.get(entry.name)
        if src is None:
            logger.warning(
                "폴스타 브리지 소스 미등록·비활성 — source_up 0으로 노출: %s (활성: %s)",
                entry.name, sorted(active),
            )
        elif src.type not in _SUPPORTED_ENGINES:
            logger.warning(
                "폴스타 브리지 소스 엔진 미지원(%s) — source_up 0으로 노출: %s",
                src.type, entry.name,
            )
            src = None
        resolved.append(BridgeSource(db_id=entry.name, zone=entry.zone, source=src))
    return resolved


def _text(value: Any) -> str:
    """라벨 값 문자열화(NULL → 빈 문자열)."""
    return "" if value is None else str(value)


def build_families(snapshots: Sequence[SourceSnapshot]) -> list[Metric]:
    """소스 스냅샷 목록을 노출 패밀리 6종으로 바꾼다(순서 고정 · 샘플 타임스탬프 없음).

    사용률 행 중 ``stat_date`` 형식이 깨졌거나 값이 NULL인 행은 신선도를 보장할 수 없어 내지
    않는다 — 소스별 건수를 WARNING으로 남긴다.
    """
    info_rows: list[list[str]] = []
    util_samples: list[tuple[list[str], float]] = []
    stat_ts_samples: list[tuple[list[str], float]] = []
    alarm_samples: list[tuple[list[str], float]] = []
    truncated: dict[str, bool] = {}
    source_up: dict[str, bool] = {}

    for snap in snapshots:
        truncated[snap.db_id] = snap.truncated
        source_up[snap.db_id] = snap.up
        if not snap.up:
            continue
        for row in snap.servers:
            info_rows.append([
                _text(row.get("server_name")), snap.db_id, snap.zone,
                _text(row.get("ipaddress")), _text(row.get("os")),
            ])
        skipped = 0
        for row in snap.utilization:
            kind = _KIND_BY_SOURCE.get(
                (_text(row.get("resource_type")), _text(row.get("definition_name")))
            )
            value = row.get("avg_val")
            try:
                if kind is None or value is None:
                    raise ValueError("kind 또는 값 없음")
                stat_epoch = stat_date_to_epoch(row.get("stat_date"))
                numeric = float(value)
            except (TypeError, ValueError):
                skipped += 1
                continue
            labels = [_text(row.get("server_name")), snap.db_id, kind]
            util_samples.append((labels, numeric))
            stat_ts_samples.append((labels, stat_epoch))
        if skipped:
            logger.warning(
                "폴스타 브리지 사용률 행 %d건 제외 (%s) — stat_date 형식·값·kind 불일치",
                skipped, snap.db_id,
            )
        for row in snap.alarms:
            alarm_samples.append((
                [_text(row.get("server_name")), snap.db_id, _text(row.get("severity"))],
                float(row.get("alarm_count") or 0),
            ))

    return [
        info_family(
            METRIC_SERVER_INFO,
            "폴스타 등록 서버 정보 — nodename은 폴스타 server_name(OS hostname 아님)",
            SERVER_INFO_LABELS,
            info_rows,
        ),
        gauge_family(
            METRIC_UTILIZATION,
            "폴스타 시간 통계 최신 버킷의 평균 사용률(%) — kind: cpu·memory·filesystem",
            UTILIZATION_LABELS,
            util_samples,
            unit="percent",
        ),
        gauge_family(
            METRIC_STAT_TIMESTAMP,
            "사용률 값이 속한 시간 통계 버킷의 시작 시각(epoch 초) — 신선도 판단용",
            UTILIZATION_LABELS,
            stat_ts_samples,
            unit="seconds",
        ),
        gauge_family(
            METRIC_ALARM_ACTIVE,
            "폴스타 활성 알람 수(서버·심각도별)",
            ALARM_ACTIVE_LABELS,
            alarm_samples,
        ),
        flag_gauge(
            METRIC_BRIDGE_TRUNCATED,
            "소스 쿼리 결과가 행 상한(max_rows)에 닿음 — 1이면 일부 시리즈가 빠졌을 수 있다",
            LABEL_DB_ID,
            truncated,
        ),
        flag_gauge(
            METRIC_BRIDGE_SOURCE_UP,
            "소스 조회 성공 여부 — 0이면 그 소스의 시리즈를 내지 않았다",
            LABEL_DB_ID,
            source_up,
        ),
    ]


# =====================================================================
# 브리지 — 전용 지연 풀 + 수집 + 캐시
# =====================================================================

PoolFactory = Callable[[list[SourceConfig]], DBPoolManager]


class PolestarBridge:
    """폴스타 브리지 본체 — 전용 지연 풀·소스별 일괄 조회·응답 캐시.

    Args:
        config: 서버 설정(``openmetrics.bridge_sources``·``bridge_cache_seconds``·``sources``).
        pool_factory: 풀 생성 함수(테스트 주입용). None이면 ``DBPoolManager``.
        clock: 캐시 TTL용 단조 시계.
        now: ``stat_date`` 하한 계산용 벽시계(로컬 naive).
    """

    def __init__(
        self,
        config: AppServerConfig,
        *,
        pool_factory: PoolFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.sources = resolve_bridge_sources(config)
        self._pool_factory = pool_factory
        self._now = now
        self._pool: DBPoolManager | None = None
        self.cache = ExpositionCache(
            self.collect, config.openmetrics.bridge_cache_seconds, clock
        )

    async def _ensure_pool(self) -> DBPoolManager | None:
        """첫 호출 때 브리지 전용 풀을 만든다(조회 가능한 소스가 없으면 만들지 않는다).

        소스당 ``pool_min_size=1``·``pool_max_size=1``로 복제한 설정을 쓴다. 실행 SQL은 도구 경로와
        같이 ``logs/sql/``에 남도록 SQL 파일 로거가 꺼져 있으면 초기화한다(D-140).
        호출은 ``ExpositionCache``의 single-flight 안에서만 일어나므로 풀은 1회만 생성된다.
        """
        if self._pool is not None:
            return self._pool
        queryable = [bs.source for bs in self.sources if bs.source is not None]
        if not queryable:
            return None
        if not sql_log.is_enabled():
            sql_log.init()
        factory = self._pool_factory or DBPoolManager
        pool = factory([replace(s, pool_min_size=1, pool_max_size=1) for s in queryable])
        await pool.initialize()
        self._pool = pool
        logger.info(
            "폴스타 브리지 전용 풀 생성: %s (소스당 풀 1-1)", [s.name for s in queryable]
        )
        return pool

    async def _snapshot(
        self, pool: DBPoolManager | None, bs: BridgeSource, stat_date_from: str
    ) -> SourceSnapshot:
        """소스 1건을 3쿼리로 조회한다. 하나라도 실패하면 그 소스는 ``up=False``(WARNING)."""
        if bs.source is None or pool is None:
            return SourceSnapshot(db_id=bs.db_id, zone=bs.zone, up=False)
        is_db2 = bs.source.type == "db2"
        limit = bs.source.max_rows
        try:
            servers = await pool.execute(bs.db_id, build_server_info_sql(is_db2, limit))
            utilization = await pool.execute(
                bs.db_id, build_latest_utilization_sql(is_db2, stat_date_from, limit)
            )
            alarms = await pool.execute(bs.db_id, build_active_alarm_count_sql(is_db2, limit))
        except Exception as e:
            logger.warning("폴스타 브리지 소스 조회 실패 — source_up 0 (%s): %s", bs.db_id, e)
            return SourceSnapshot(db_id=bs.db_id, zone=bs.zone, up=False)
        truncated = any(len(rows) >= limit for rows in (servers, utilization, alarms))
        if truncated:
            logger.warning(
                "폴스타 브리지 행 상한 도달(max_rows=%d) — truncated 1 (%s): "
                "info=%d util=%d alarm=%d",
                limit, bs.db_id, len(servers), len(utilization), len(alarms),
            )
        return SourceSnapshot(
            db_id=bs.db_id, zone=bs.zone, up=True, truncated=truncated,
            servers=servers, utilization=utilization, alarms=alarms,
        )

    async def collect(self) -> list[Metric]:
        """전 소스를 병렬 조회해 패밀리 목록을 만든다(``ExpositionCache``의 수집 함수)."""
        pool = await self._ensure_pool()
        stat_date_from = stat_date_lower_bound(self._now())
        snapshots = await asyncio.gather(
            *(self._snapshot(pool, bs, stat_date_from) for bs in self.sources)
        )
        return build_families(snapshots)

    async def aclose(self) -> None:
        """전용 풀을 닫는다(앱 종료 시 — 만들지 않았으면 아무것도 하지 않는다)."""
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close_all()
            logger.info("폴스타 브리지 전용 풀 종료")


def register_polestar_exporter(
    mcp: FastMCP,
    config: AppServerConfig,
    *,
    pool_factory: PoolFactory | None = None,
) -> PolestarBridge:
    """``GET /metrics`` 폴스타 브리지 라우트를 등록하고 종료 정리를 건다.

    라우트는 ``build_asgi_app``의 Bearer 미들웨어 안쪽에 붙으므로 ``MCP_BEARER_TOKEN``이 그대로
    적용된다. 전용 풀 정리는 ``om_exposition.install_shutdown_hooks``가 앱 lifespan 종료에 건다.

    Args:
        mcp: FastMCP 인스턴스.
        config: 서버 설정.
        pool_factory: 풀 생성 함수(테스트 주입용).

    Returns:
        등록된 ``PolestarBridge``.
    """
    bridge = PolestarBridge(config, pool_factory=pool_factory)
    mcp.custom_route(ROUTE_PATH, methods=["GET"])(make_exposition_endpoint(bridge.cache))
    register_shutdown(mcp, bridge.aclose)
    logger.info(
        "폴스타 → OpenMetrics 브리지 등록: GET %s (소스 %s · 캐시 %ds)",
        ROUTE_PATH,
        [f"{bs.db_id}{'' if bs.source else '(조회 불가)'}" for bs in bridge.sources],
        config.openmetrics.bridge_cache_seconds,
    )
    return bridge
