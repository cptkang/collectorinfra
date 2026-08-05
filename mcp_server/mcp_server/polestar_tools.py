"""폴스타 조사용 고수준 MCP 도구 (Plan 66 Wave 2-A · sre-agent/04 §4.2).

HolmesGPT 등 소비자가 방언·스키마·조인 규칙을 모른 채 **값 인자만**으로 폴스타
관측 데이터를 읽도록 하는 고정 SQL/REST 도구 8종을 등록한다. 소비자는 SQL 텍스트를
넘기지 않는다 — 서버가 고정 SQL을 조립하고 방언을 분기한다(§5).

공통 반환 계약(§4.2):
    - 정상: JSON 문자열 ``{"rows": [...], "row_count": N, "queried_at": ISO,
      "source_kind": ...}`` (인용 의무 지원).
    - 오류: ``{"error": ...}`` (예외를 전파하지 않음 — RemoteMCPTool이 ERROR로 변환).
    - 각 고정 SQL에 **LIMIT/FETCH FIRST를 직접 명시**한다(max_rows 사후 슬라이스는 DB
      전량 fetch 함정이 있어 SQL 자체 제한이 1차 방어다).

안전 계약(§6):
    - MCP는 파라미터 바인딩이 없다 — 값 인자는 서버가 ``_sql_literal``(널바이트 제거 +
      작은따옴표 이중화)로 보간한다.
    - ``process_snapshot``의 프로세스 args/커맨드라인은 서버가 마스킹한다(우회 불가).
    - 읽기 전용(D-003): 고수준 도구는 SELECT/GET만. DML/DDL을 절대 생성하지 않는다.

방언 규칙(Known Mistakes · D-016/D-022/D-028):
    - 스키마 한정: PostgreSQL=소문자 ``polestar.`` / DB2=대문자 ``POLESTAR.``.
    - 행 제한: PostgreSQL=``LIMIT n`` / DB2=``FETCH FIRST n ROWS ONLY``.
    - RESOURCE_CONF_ID JOIN 금지 — hostname 브릿지 조인만(D-022). cmm_vendor/cmm_os/
      cmm_os_param lookup 미참조(D-028).
    - 본 도구들은 집계를 쓰지 않으므로 ``CAST(... AS DECIMAL)``/``::numeric``가 불필요하다
      (집계 전 캐스트 규칙은 집계 도입 시 적용).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import Context, FastMCP

from mcp_server.config import AppServerConfig
from mcp_server.db import DBPoolManager

logger = logging.getLogger(__name__)

# 스키마 한정자 (Known Mistakes 방언 규칙 — DB2는 대문자 POLESTAR).
_PG_SCHEMA = "polestar"
_DB2_SCHEMA = "POLESTAR"

# metric kind → (resource_type, definition_name) 서버 상수
# (noise_gate/domain/anomaly.py·src/db_adapters/polestar/prompts.py 실측 정합).
_METRIC_KIND_MAP: dict[str, tuple[str, str]] = {
    "cpu": ("server.Cpus", "Utilization"),
    "memory": ("server.Memory", "Utilization"),
    "filesystem": ("server.FileSystems", "Utilization"),
    "disk_io": ("server.Disks", "MaxIORate"),
}
# granularity → 통계 테이블
_METRIC_TABLE: dict[str, str] = {
    "h": "cmm_metric_stat_h",
    "d": "cmm_metric_stat_d",
    "m": "cmm_metric_stat_m",
}

_PROCESS_PATH = "/rest/server/process/listByhostname"

# 프로세스 args 마스킹 패턴 (noise_gate/domain/process_rank.py 이식 — 값만 *** 치환).
_SENSITIVE_KEY = (
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|credential)"
)
_SENSITIVE_RE = re.compile(rf"({_SENSITIVE_KEY})(\s*[=:]\s*|\s+)(\S+)", re.IGNORECASE)
# scheme://user:<password>@host 형태의 비밀번호만 치환한다.
_CONN_STRING_RE = re.compile(r"(\b[a-zA-Z][\w+.-]*://[^\s:/@]+:)([^\s@]+)(@)")
_MASK = "***"


# =====================================================================
# 이스케이프 · 방언 유틸
# =====================================================================


def _sql_literal(value: str) -> str:
    """문자열을 안전한 SQL 리터럴로 변환한다(널바이트 제거 + 작은따옴표 이중화).

    MCP는 파라미터 바인딩이 없으므로 모든 외부 입력 문자열은 이 함수를 거쳐 보간한다
    (§6-2 이스케이프 계약 — 단위 테스트로 고정).
    """
    cleaned = str(value).replace("\x00", "").replace("'", "''")
    return f"'{cleaned}'"


def _id_literal(value: Any) -> str:
    """식별자(alarm_id 등)를 안전하게 보간한다.

    순수 숫자면 바인딩 없이도 인젝션이 불가능하므로 정수 리터럴로, 그 외에는
    ``_sql_literal``로 이스케이프한다(엔진 무관 — DB2의 문자열↔숫자 캐스트 회피).
    """
    s = str(value).strip()
    if s.isdigit():
        return s
    return _sql_literal(s)


def _q(is_db2: bool, table: str) -> str:
    """엔진별 스키마로 한정된 테이블 참조를 반환한다."""
    schema = _DB2_SCHEMA if is_db2 else _PG_SCHEMA
    return f"{schema}.{table}"


def _row_limit(is_db2: bool, n: int) -> str:
    """엔진별 행 제한 절을 반환한다(DB2는 LIMIT 미지원 → FETCH FIRST)."""
    n = int(n)
    return f"FETCH FIRST {n} ROWS ONLY" if is_db2 else f"LIMIT {n}"


def _time_floor(is_db2: bool, hours: int) -> str:
    """엔진별 '지금으로부터 hours시간 전' 시각 표현을 반환한다."""
    h = int(hours)
    return f"CURRENT TIMESTAMP - {h} HOURS" if is_db2 else f"NOW() - INTERVAL '{h} hours'"


def _now_iso() -> str:
    """queried_at용 UTC ISO 타임스탬프를 반환한다."""
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: int, lo: int, hi: int) -> int:
    """value를 [lo, hi] 범위로 제한한다."""
    return max(lo, min(int(value), hi))


# =====================================================================
# 프로세스 마스킹 · 랭킹 (순수 함수 — 단위 테스트 대상)
# =====================================================================


def _mask_process_args(args: str, max_len: int = 120) -> str:
    """프로세스 실행 인자에서 민감정보를 마스킹하고 길이를 제한한다.

    마스킹 대상(대소문자 무시, 키는 보존하고 값만 *** 치환):
        - password|passwd|pwd|secret|token|api_key|access_key|credential 의 값
        - DB 접속 문자열(scheme://user:<password>@host)의 비밀번호 부분
    """
    if not args:
        return ""
    masked = _CONN_STRING_RE.sub(rf"\1{_MASK}\3", args)
    masked = _SENSITIVE_RE.sub(rf"\1\2{_MASK}", masked)
    if len(masked) > max_len:
        masked = masked[:max_len].rstrip() + "…"
    return masked


def _to_float(value: Any) -> float:
    """값을 float로 변환한다(실패 시 0.0)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sort_key(item: dict[str, Any], sort: str) -> float:
    """정렬 지표를 반환한다(mem=pmem, cpu=p100cpu→pcpu 폴백)."""
    if sort == "mem":
        return _to_float(item.get("pmem"))
    if item.get("p100cpu") is not None:
        return _to_float(item.get("p100cpu"))
    return _to_float(item.get("pcpu"))


def _rank_processes(
    raw_list: list[dict[str, Any]], sort: str, top_n: int
) -> tuple[list[dict[str, Any]], int]:
    """원시 프로세스 목록을 지표 내림차순 상위 N으로 선별하고 args를 마스킹한다.

    Returns:
        (마스킹된 상위 N 프로세스 목록, 전체 프로세스 건수)
    """
    items = [it for it in (raw_list or []) if isinstance(it, dict)]
    total = len(items)
    ranked = sorted(items, key=lambda it: _sort_key(it, sort), reverse=True)
    top: list[dict[str, Any]] = []
    for it in ranked[: max(0, int(top_n))]:
        top.append(
            {
                "name": str(it.get("name", "") or ""),
                "pid": it.get("pid"),
                "ppid": it.get("ppid"),
                "user": str(it.get("user", "") or ""),
                "p100cpu": it.get("p100cpu"),
                "pcpu": it.get("pcpu"),
                "pmem": it.get("pmem"),
                "rss": it.get("rss"),
                "args": _mask_process_args(str(it.get("args", "") or "")),
            }
        )
    return top, total


# =====================================================================
# 고정 SQL 빌더 (순수 함수 — 단위 테스트 대상, 방언 분기 서버 내부)
# =====================================================================


def build_alarm_history_sql(
    is_db2: bool,
    server_name: str,
    alarm_name: str,
    hours: int,
    exclude_alarm_id: Optional[str],
    limit: int,
) -> str:
    """동일 (서버, 알람명) 과거 알람 이력 조회 SQL을 조립한다(읽기 전용 SELECT 단일문).

    cmm_alarm ⋈ cmm_alarm_def ⋈ cmm_resource. PLATFORM_RESOURCE_ID COALESCE JOIN으로
    server.Server 행(SVR)을 식별해 SVR.NAME으로 매칭한다(hostname 매칭 금지 — 공동존
    폴스타는 name≠hostname). ALARMSEVERITY IN (0,1,2,3)로 해소(0) 포함(D-030).
    RESOURCE_CONF_ID JOIN 미포함(D-022).
    """
    t_alarm = _q(is_db2, "cmm_alarm")
    t_def = _q(is_db2, "cmm_alarm_def")
    t_res = _q(is_db2, "cmm_resource")
    exclude = ""
    if exclude_alarm_id is not None and str(exclude_alarm_id).strip():
        exclude = f"  AND CA.ID <> {_id_literal(exclude_alarm_id)}\n"
    return (
        "SELECT\n"
        "    CA.ID AS alarm_id,\n"
        "    CA.CTIME AS alarm_time,\n"
        "    CA.ALARMSEVERITY AS severity,\n"
        "    CA.CURRENTALARMSTATUS AS alarm_status,\n"
        "    CR.NAME AS resource_name\n"
        f"FROM {t_alarm} CA\n"
        f"JOIN {t_def} D ON CA.DEFINITION_ID = D.ID\n"
        f"JOIN {t_res} CR ON CA.RESOURCE_ID = CR.ID\n"
        f"JOIN {t_res} SVR ON SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)\n"
        "                     AND SVR.RESOURCE_TYPE = 'server.Server'\n"
        "                     AND SVR.DTIME IS NULL\n"
        "WHERE CR.DTIME IS NULL\n"
        "  AND CA.ALARMSEVERITY IN (0, 1, 2, 3)\n"
        f"  AND D.NAME = {_sql_literal(alarm_name)}\n"
        f"  AND SVR.NAME = {_sql_literal(server_name)}\n"
        f"  AND CA.CTIME >= {_time_floor(is_db2, hours)}\n"
        f"{exclude}"
        "ORDER BY CA.CTIME DESC\n"
        f"{_row_limit(is_db2, limit)}"
    )


def build_metric_trend_sql(
    is_db2: bool,
    server_name: str,
    kind: str,
    granularity: str,
    periods: int,
) -> str:
    """서버 사용률 시계열(avg/min/max) 조회 SQL을 조립한다(읽기 전용 SELECT 단일문).

    server.Server(svr, NAME 매칭) ─ platform_resource_id ─ 자식 리소스 ─ 통계 테이블 조인.
    kind→(resource_type, definition_name)·granularity→테이블은 서버 상수다.
    최신 periods개를 stat_date DESC로 가져온다. 집계 없음 → CAST 불요.

    Raises:
        ValueError: 지원하지 않는 kind 또는 granularity.
    """
    if kind not in _METRIC_KIND_MAP:
        raise ValueError(
            f"지원하지 않는 kind: {kind} (허용: {sorted(_METRIC_KIND_MAP)})"
        )
    if granularity not in _METRIC_TABLE:
        raise ValueError(
            f"지원하지 않는 granularity: {granularity} (허용: {sorted(_METRIC_TABLE)})"
        )
    resource_type, definition_name = _METRIC_KIND_MAP[kind]
    t_res = _q(is_db2, "cmm_resource")
    t_metric = _q(is_db2, _METRIC_TABLE[granularity])
    return (
        "SELECT\n"
        "    s.stat_date AS stat_date,\n"
        "    s.avg_val AS avg_val,\n"
        "    s.min_val AS min_val,\n"
        "    s.max_val AS max_val\n"
        f"FROM {t_res} svr\n"
        f"JOIN {t_res} child ON child.platform_resource_id = svr.id\n"
        f"JOIN {t_metric} s ON s.resource_id = child.id\n"
        "WHERE svr.resource_type = 'server.Server'\n"
        f"  AND svr.name = {_sql_literal(server_name)}\n"
        "  AND svr.dtime IS NULL\n"
        f"  AND child.resource_type = {_sql_literal(resource_type)}\n"
        "  AND child.dtime IS NULL\n"
        f"  AND s.definition_name = {_sql_literal(definition_name)}\n"
        "ORDER BY s.stat_date DESC\n"
        f"{_row_limit(is_db2, periods)}"
    )


def build_resource_status_sql(is_db2: bool, server_name: str, limit: int) -> str:
    """서버와 서브리소스별 가용 상태·중요도·유지보수 조회 SQL을 조립한다.

    server.Server(SVR, NAME 매칭)와 그 하위 리소스(PLATFORM_RESOURCE_ID COALESCE) 각각의
    AVAIL_STATUS/IMPORTANCE_ID/IS_MAINTENANCE를 반환한다. RESOURCE_CONF_ID JOIN 미포함(D-022).
    """
    t_res = _q(is_db2, "cmm_resource")
    return (
        "SELECT\n"
        "    CR.ID AS resource_id,\n"
        "    CR.NAME AS resource_name,\n"
        "    CR.RESOURCE_TYPE AS resource_type,\n"
        "    CR.AVAIL_STATUS AS avail_status,\n"
        "    CR.IMPORTANCE_ID AS importance_id,\n"
        "    CR.IS_MAINTENANCE AS is_maintenance\n"
        f"FROM {t_res} CR\n"
        f"JOIN {t_res} SVR ON SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)\n"
        "                     AND SVR.RESOURCE_TYPE = 'server.Server'\n"
        "                     AND SVR.DTIME IS NULL\n"
        "WHERE CR.DTIME IS NULL\n"
        f"  AND SVR.NAME = {_sql_literal(server_name)}\n"
        "ORDER BY CR.RESOURCE_TYPE\n"
        f"{_row_limit(is_db2, limit)}"
    )


def _topology_pg_sql(name_lit: str, max_hops: int, limit: int) -> str:
    """PostgreSQL 다홉 토폴로지 SQL(WITH RECURSIVE — 조상 up + 자손 down)."""
    schema = _PG_SCHEMA
    hops = int(max_hops)
    return (
        "WITH RECURSIVE up (id, name, avail_status, p1, p2, hop) AS (\n"
        "    SELECT ID, NAME, AVAIL_STATUS, AVAIL_DEPEND_RESOURCE_ID, "
        "AVAIL_DEPEND_RESOURCE_ID_2, 0\n"
        f"    FROM {schema}.cmm_resource\n"
        "    WHERE RESOURCE_TYPE = 'server.Server' AND DTIME IS NULL "
        f"AND NAME = {name_lit}\n"
        "  UNION ALL\n"
        "    SELECT r.ID, r.NAME, r.AVAIL_STATUS, r.AVAIL_DEPEND_RESOURCE_ID, "
        "r.AVAIL_DEPEND_RESOURCE_ID_2, up.hop + 1\n"
        f"    FROM {schema}.cmm_resource r\n"
        "    JOIN up ON (r.ID = up.p1 OR r.ID = up.p2)\n"
        f"    WHERE r.DTIME IS NULL AND up.hop < {hops}\n"
        "),\n"
        "down (id, name, avail_status, hop) AS (\n"
        "    SELECT ID, NAME, AVAIL_STATUS, 0\n"
        f"    FROM {schema}.cmm_resource\n"
        "    WHERE RESOURCE_TYPE = 'server.Server' AND DTIME IS NULL "
        f"AND NAME = {name_lit}\n"
        "  UNION ALL\n"
        "    SELECT c.ID, c.NAME, c.AVAIL_STATUS, down.hop + 1\n"
        f"    FROM {schema}.cmm_resource c\n"
        "    JOIN down ON (c.AVAIL_DEPEND_RESOURCE_ID = down.id "
        "OR c.AVAIL_DEPEND_RESOURCE_ID_2 = down.id)\n"
        f"    WHERE c.DTIME IS NULL AND down.hop < {hops}\n"
        ")\n"
        "SELECT id AS resource_id, name AS resource_name, avail_status, hop, "
        "'ancestor' AS direction FROM up WHERE hop > 0\n"
        "UNION ALL\n"
        "SELECT id AS resource_id, name AS resource_name, avail_status, hop, "
        "'descendant' AS direction FROM down WHERE hop > 0\n"
        "ORDER BY direction, hop\n"
        f"LIMIT {int(limit)}"
    )


def _topology_db2_sql(name_lit: str, limit: int) -> str:
    """DB2 1홉 폴백 토폴로지 SQL(재귀 CTE 미사용 — §5 축소 결과)."""
    schema = _DB2_SCHEMA
    return (
        "SELECT r.ID AS resource_id, r.NAME AS resource_name, "
        "r.AVAIL_STATUS AS avail_status, 1 AS hop, 'ancestor' AS direction\n"
        f"FROM {schema}.cmm_resource r\n"
        f"JOIN {schema}.cmm_resource a "
        "ON (r.ID = a.AVAIL_DEPEND_RESOURCE_ID OR r.ID = a.AVAIL_DEPEND_RESOURCE_ID_2)\n"
        "WHERE a.RESOURCE_TYPE = 'server.Server' AND a.DTIME IS NULL "
        f"AND a.NAME = {name_lit} AND r.DTIME IS NULL\n"
        "UNION ALL\n"
        "SELECT c.ID, c.NAME, c.AVAIL_STATUS, 1, 'descendant'\n"
        f"FROM {schema}.cmm_resource c\n"
        f"JOIN {schema}.cmm_resource a2 "
        "ON (c.AVAIL_DEPEND_RESOURCE_ID = a2.ID OR c.AVAIL_DEPEND_RESOURCE_ID_2 = a2.ID)\n"
        "WHERE a2.RESOURCE_TYPE = 'server.Server' AND a2.DTIME IS NULL "
        f"AND a2.NAME = {name_lit} AND c.DTIME IS NULL\n"
        f"FETCH FIRST {int(limit)} ROWS ONLY"
    )


def build_topology_sql(
    is_db2: bool, server_name: str, max_hops: int, limit: int
) -> str:
    """가용성 의존 조상/자손 토폴로지 + 상태 조회 SQL을 조립한다.

    PostgreSQL은 WITH RECURSIVE로 max_hops까지, DB2(b0)는 1홉 폴백(§5). AVAIL_DEPEND_
    RESOURCE_ID(_2)로 부모/자식을 잇는다. RESOURCE_CONF_ID JOIN 미포함(D-022).
    """
    name_lit = _sql_literal(server_name)
    if is_db2:
        return _topology_db2_sql(name_lit, limit)
    return _topology_pg_sql(name_lit, max_hops, limit)


def build_condition_log_sql(is_db2: bool, alarm_id: Any) -> str:
    """알람의 조건 로그(CONDITIONLOGTEXT — 실제 울린 값) 조회 SQL을 조립한다."""
    t_alarm = _q(is_db2, "cmm_alarm")
    return (
        "SELECT\n"
        "    CA.ID AS alarm_id,\n"
        "    CA.CONDITIONLOGTEXT AS condition_log,\n"
        "    CA.CTIME AS alarm_time,\n"
        "    CA.ALARMSEVERITY AS severity,\n"
        "    CA.CURRENTALARMSTATUS AS alarm_status\n"
        f"FROM {t_alarm} CA\n"
        f"WHERE CA.ID = {_id_literal(alarm_id)}\n"
        f"{_row_limit(is_db2, 1)}"
    )


def build_os_config_sql(is_db2: bool, hostname: str, limit: int) -> str:
    """OS 구성 EAV를 hostname 브릿지로 조회하는 SQL을 조립한다(D-022 준수).

    core_config_prop을 ``NAME='Hostname'`` 속성(p_host)으로 앵커하고 동일
    configuration_id의 모든 속성(p)을 name/value 행으로 반환한다. 소비자가 OSType/커널/
    sysctl/벤더를 그 행에서 인용한다. RESOURCE_CONF_ID JOIN·cmm_vendor/cmm_os 미참조
    (D-022/D-028 — 속성명 하드코딩으로 인한 환각 회피).
    """
    t_prop = _q(is_db2, "core_config_prop")
    return (
        "SELECT\n"
        "    p_host.stringvalue_short AS hostname,\n"
        "    p.name AS prop_name,\n"
        "    p.stringvalue_short AS prop_value\n"
        f"FROM {t_prop} p_host\n"
        f"JOIN {t_prop} p ON p.configuration_id = p_host.configuration_id\n"
        f"WHERE p_host.name = {_sql_literal('Hostname')}\n"
        f"  AND p_host.stringvalue_short = {_sql_literal(hostname)}\n"
        "ORDER BY p.name\n"
        f"{_row_limit(is_db2, limit)}"
    )


def build_change_history_sql(server_name: str, cutoff_epoch: int, limit: int) -> str:
    """변경 이력 조회 SQL을 조립한다(PostgreSQL 전용 — gp/yd).

    cmm_resource_lifecycle_history를 서버 리소스(PLATFORM_RESOURCE_ID COALESCE)로 스코프
    하고 event_time >= cutoff_epoch(초 epoch)로 창을 유계한다. DB2(b0)는 호출부에서 빈
    결과+사유로 강등한다(§5 — 침묵 강등 금지).
    """
    schema = _PG_SCHEMA
    return (
        "SELECT\n"
        "    h.id AS id,\n"
        "    h.resource_id AS resource_id,\n"
        "    h.resource_type AS resource_type,\n"
        "    h.lifecycle_type AS lifecycle_type,\n"
        "    h.description AS description,\n"
        "    h.event_time AS event_time\n"
        f"FROM {schema}.cmm_resource_lifecycle_history h\n"
        f"JOIN {schema}.cmm_resource r ON r.id = h.resource_id\n"
        f"JOIN {schema}.cmm_resource svr ON svr.id = COALESCE(r.platform_resource_id, r.id)\n"
        "                     AND svr.resource_type = 'server.Server'\n"
        "                     AND svr.dtime IS NULL\n"
        f"WHERE svr.name = {_sql_literal(server_name)}\n"
        f"  AND h.event_time >= {int(cutoff_epoch)}\n"
        "ORDER BY h.event_time DESC\n"
        f"LIMIT {int(limit)}"
    )


# =====================================================================
# 반환 헬퍼
# =====================================================================


def _ok(rows: list[dict[str, Any]], source: str, engine: str, **extra: Any) -> str:
    """정상 반환 JSON 문자열을 만든다({rows, row_count, queried_at, source_kind, ...})."""
    payload: dict[str, Any] = {
        "rows": rows,
        "row_count": len(rows),
        "queried_at": _now_iso(),
        "source_kind": "polestar_db",
        "source": source,
        "engine": engine,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    """오류 반환 JSON 문자열을 만든다({error})."""
    return json.dumps({"error": message}, ensure_ascii=False)


# =====================================================================
# 컨텍스트 접근 헬퍼
# =====================================================================


def _pool(ctx: Context) -> DBPoolManager:
    """컨텍스트에서 DBPoolManager를 가져온다."""
    return ctx.request_context.lifespan_context["pool_manager"]


def _config(ctx: Context) -> AppServerConfig:
    """컨텍스트에서 서버 설정을 가져온다."""
    return ctx.request_context.lifespan_context["config"]


def _resolve_engine(pool: DBPoolManager, source: str) -> tuple[bool, str]:
    """소스의 (is_db2, source_type)을 반환한다. 미등록 소스는 ValueError."""
    if not pool.is_source_active(source):
        raise ValueError(
            f"알 수 없는 소스: {source} (활성: {pool.get_active_sources()})"
        )
    source_type = pool.get_source_type(source)
    return source_type == "db2", source_type


# =====================================================================
# 도구 등록
# =====================================================================


def register_polestar_tools(mcp: FastMCP) -> None:
    """폴스타 조사용 고수준 도구 8종을 MCP 서버에 등록한다."""

    @mcp.tool()
    async def polestar_alarm_history(
        source: str,
        server_name: str,
        alarm_name: str,
        hours: int = 168,
        exclude_alarm_id: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """동일 (서버, 알람명)의 과거 알람 이력을 조회한다.

        Args:
            source: 데이터소스(db_id).
            server_name: 폴스타 등록 서버명(cmm_resource.name).
            alarm_name: 알람 정의명(cmm_alarm_def.name).
            hours: 조회 창(시간). 기본 168(7일).
            exclude_alarm_id: 제외할 알람 ID(현재 이벤트 자기 제외용).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows, row_count, queried_at, source_kind} 또는 {error}.
        """
        pool = _pool(ctx)
        try:
            is_db2, engine = _resolve_engine(pool, source)
        except ValueError as e:
            return _err(str(e))
        limit = pool.get_source_config(source).max_rows
        sql = build_alarm_history_sql(
            is_db2, server_name, alarm_name, hours, exclude_alarm_id, limit
        )
        try:
            rows = await pool.execute(source, sql)
            return _ok(rows, source, engine)
        except Exception as e:
            logger.warning("polestar_alarm_history 실패 (%s): %s", source, e)
            return _err(str(e))

    @mcp.tool()
    async def polestar_metric_trend(
        source: str,
        server_name: str,
        kind: str,
        granularity: str = "h",
        periods: int = 24,
        ctx: Context | None = None,
    ) -> str:
        """서버의 사용률 시계열(최신 periods개)을 조회한다.

        Args:
            source: 데이터소스(db_id).
            server_name: 폴스타 등록 서버명.
            kind: cpu | memory | filesystem | disk_io.
            granularity: h(시간) | d(일) | m(월). 기본 h.
            periods: 반환 기간 수(최신 우선). 기본 24.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows, row_count, queried_at, source_kind} 또는 {error}.
        """
        pool = _pool(ctx)
        try:
            is_db2, engine = _resolve_engine(pool, source)
        except ValueError as e:
            return _err(str(e))
        max_rows = pool.get_source_config(source).max_rows
        capped = _clamp(periods, 1, max_rows)
        try:
            sql = build_metric_trend_sql(is_db2, server_name, kind, granularity, capped)
        except ValueError as e:
            return _err(str(e))
        try:
            rows = await pool.execute(source, sql)
            return _ok(rows, source, engine, kind=kind, granularity=granularity)
        except Exception as e:
            logger.warning("polestar_metric_trend 실패 (%s): %s", source, e)
            return _err(str(e))

    @mcp.tool()
    async def polestar_resource_status(
        source: str,
        server_name: str,
        ctx: Context | None = None,
    ) -> str:
        """서버와 서브리소스의 가용 상태·중요도·유지보수 여부를 조회한다.

        Args:
            source: 데이터소스(db_id).
            server_name: 폴스타 등록 서버명.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows, row_count, queried_at, source_kind} 또는 {error}.
        """
        pool = _pool(ctx)
        try:
            is_db2, engine = _resolve_engine(pool, source)
        except ValueError as e:
            return _err(str(e))
        limit = pool.get_source_config(source).max_rows
        sql = build_resource_status_sql(is_db2, server_name, limit)
        try:
            rows = await pool.execute(source, sql)
            return _ok(rows, source, engine)
        except Exception as e:
            logger.warning("polestar_resource_status 실패 (%s): %s", source, e)
            return _err(str(e))

    @mcp.tool()
    async def polestar_topology(
        source: str,
        server_name: str,
        max_hops: int = 3,
        ctx: Context | None = None,
    ) -> str:
        """서버의 가용성 의존 조상/자손 토폴로지와 상태를 조회한다.

        Args:
            source: 데이터소스(db_id).
            server_name: 폴스타 등록 서버명.
            max_hops: 조상/자손 탐색 최대 홉(PostgreSQL). 기본 3.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows, row_count, queried_at, source_kind} 또는 {error}.
            DB2(b0)는 1홉 폴백이며 note로 표기한다.
        """
        pool = _pool(ctx)
        try:
            is_db2, engine = _resolve_engine(pool, source)
        except ValueError as e:
            return _err(str(e))
        limit = pool.get_source_config(source).max_rows
        hops = _clamp(max_hops, 1, 10)
        sql = build_topology_sql(is_db2, server_name, hops, limit)
        extra: dict[str, Any] = {}
        if is_db2:
            extra["note"] = "DB2(b0)는 1홉 폴백 — 다홉 토폴로지는 PostgreSQL만 지원"
        try:
            rows = await pool.execute(source, sql)
            return _ok(rows, source, engine, **extra)
        except Exception as e:
            logger.warning("polestar_topology 실패 (%s): %s", source, e)
            return _err(str(e))

    @mcp.tool()
    async def polestar_process_snapshot(
        hostname: str,
        top_n: int = 10,
        sort: str = "cpu",
        ctx: Context | None = None,
    ) -> str:
        """폴스타 실시간 프로세스 API로 상위 자원 점유 프로세스를 조회한다(실시간 단면).

        args/커맨드라인의 비밀번호·키·토큰은 서버가 마스킹한 값만 반환한다(§6-3).

        Args:
            hostname: OS 호스트명(cmm_resource.hostname).
            top_n: 상위 프로세스 수. 기본 10.
            sort: cpu(p100cpu→pcpu) | mem(pmem). 기본 cpu.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows, row_count, captured_at, queried_at, source_kind} 또는 {error}.
        """
        if sort not in ("cpu", "mem"):
            return _err(f"지원하지 않는 sort: {sort} (허용: cpu, mem)")
        if not hostname or not hostname.strip():
            return _err("hostname이 비어 있음")

        base_url = _config(ctx).server.process_api_base_url
        if not base_url:
            return _err(
                "PROCESS_API_BASE_URL 미설정 — 프로세스 스냅샷 조회 불가"
            )

        url = f"{base_url.rstrip('/')}{_PROCESS_PATH}?hostname={quote(hostname, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return _err(
                    f"프로세스 API 비200 응답: status={resp.status_code}"
                )
            payload = resp.json()
        except Exception as e:
            logger.warning("polestar_process_snapshot 실패 (%s): %s", hostname, e)
            return _err(str(e))

        captured_at = payload.get("date") if isinstance(payload, dict) else None
        data = payload.get("data") if isinstance(payload, dict) else None
        proc_list = data.get("list") if isinstance(data, dict) else None
        top, total = _rank_processes(proc_list or [], sort, top_n)
        return json.dumps(
            {
                "rows": top,
                "row_count": len(top),
                "total_processes": total,
                "captured_at": captured_at,
                "queried_at": _now_iso(),
                "source_kind": "polestar_process_realtime",
                "note": "실시간 단면 — 조회 시점의 프로세스 상태(과거 이력 아님)",
            },
            ensure_ascii=False,
            default=str,
        )

    @mcp.tool()
    async def polestar_os_config(
        source: str,
        hostname: str,
        ctx: Context | None = None,
    ) -> str:
        """호스트의 OS 구성(EAV: OSType/커널/sysctl/벤더 등)을 hostname 브릿지로 조회한다.

        Args:
            source: 데이터소스(db_id).
            hostname: OS 호스트명(core_config_prop의 Hostname 속성값).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows(prop_name/prop_value), row_count, queried_at, source_kind}
            또는 {error}.
        """
        pool = _pool(ctx)
        try:
            is_db2, engine = _resolve_engine(pool, source)
        except ValueError as e:
            return _err(str(e))
        limit = pool.get_source_config(source).max_rows
        sql = build_os_config_sql(is_db2, hostname, limit)
        try:
            rows = await pool.execute(source, sql)
            return _ok(rows, source, engine)
        except Exception as e:
            logger.warning("polestar_os_config 실패 (%s): %s", source, e)
            return _err(str(e))

    @mcp.tool()
    async def polestar_change_history(
        source: str,
        server_name: str,
        hours: int = 24,
        ctx: Context | None = None,
    ) -> str:
        """서버 리소스의 최근 변경 이력을 조회한다(PostgreSQL 전용 — gp/yd).

        Args:
            source: 데이터소스(db_id).
            server_name: 폴스타 등록 서버명.
            hours: 조회 창(시간). 기본 24.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows, row_count, queried_at, source_kind} 또는 {error}.
            DB2(b0)는 빈 결과 + 사유(note)를 반환한다(침묵 강등 금지).
        """
        pool = _pool(ctx)
        try:
            is_db2, engine = _resolve_engine(pool, source)
        except ValueError as e:
            return _err(str(e))
        if is_db2:
            return _ok(
                [], source, engine,
                unsupported=True,
                note="변경 이력은 PostgreSQL(gp/yd)만 지원 — DB2(b0)는 미지원",
            )
        limit = pool.get_source_config(source).max_rows
        cutoff_epoch = int(time.time()) - _clamp(hours, 1, 24 * 365) * 3600
        sql = build_change_history_sql(server_name, cutoff_epoch, limit)
        try:
            rows = await pool.execute(source, sql)
            return _ok(rows, source, engine)
        except Exception as e:
            logger.warning("polestar_change_history 실패 (%s): %s", source, e)
            return _err(str(e))

    @mcp.tool()
    async def polestar_condition_log(
        source: str,
        alarm_id: str,
        ctx: Context | None = None,
    ) -> str:
        """알람의 조건 로그(CONDITIONLOGTEXT — 실제 울린 값)를 조회한다.

        Args:
            source: 데이터소스(db_id).
            alarm_id: 알람 ID(cmm_alarm.id).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {rows, row_count, queried_at, source_kind} 또는 {error}.
        """
        pool = _pool(ctx)
        try:
            is_db2, engine = _resolve_engine(pool, source)
        except ValueError as e:
            return _err(str(e))
        sql = build_condition_log_sql(is_db2, alarm_id)
        try:
            rows = await pool.execute(source, sql)
            return _ok(rows, source, engine)
        except Exception as e:
            logger.warning("polestar_condition_log 실패 (%s): %s", source, e)
            return _err(str(e))
