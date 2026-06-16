"""폴스타 DB 알람 이력 조회 리포지토리 (Plan 47).

동일 (서버, 알람명)의 과거 알람을 폴스타 DB에서 **고정 SQL**(LLM 미사용, 읽기 전용
SELECT 단일문)로 조회한다. 기존 DBHub(MCP) 경로(`DBRegistry.get_client(db_id)` →
`execute_sql`)를 재사용한다.

서버 매칭 규칙 (Plan 47 §5.3, Known Mistakes 2026-06-10):
    - 알람 이벤트의 `server_name`(=${platformName})은 폴스타에 등록된 서버명으로,
      `CMM_RESOURCE.NAME`(server.Server 행)과 매칭한다.
    - 공동존 폴스타(polestar_cm_gp/yd)는 장비 식별에 r.name(=SVR.NAME)을 사용한다 —
      hostname 매칭 금지 (serverName과 hostname이 완전히 다른 사례 실측 확인됨.
      hostname으로 매칭하면 이력 0건 → 전부 "첫 발생" 오판).
    - 알람은 서버 하위 자원(server.Cpus 등)에도 발생하므로 Template C-6 패턴
      (PLATFORM_RESOURCE_ID COALESCE JOIN)으로 server.Server 행을 식별해 매칭한다.
    - D-022: RESOURCE_CONF_ID JOIN 절대 금지 — 본 SQL은 CORE_CONFIG_PROP 미사용.

db_id별 서버 매칭 식은 `_SERVER_MATCH_BY_DB_ID`로 분기 가능하게 분리해 둔다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from src.alarm.domain.alarm import AlarmEvent, AlarmHistoryEntry

logger = logging.getLogger(__name__)

_TIME_FMT = "%Y-%m-%d %H:%M:%S"

# 서버 매칭 식 — {server_name}에 안전 처리된 SQL 리터럴이 보간된다.
# SVR은 Template C-6 패턴으로 식별된 server.Server 행(= cmm_resource r) 별칭이므로
# SVR.NAME = 폴스타 등록 장비명(r.name)이다.
_DEFAULT_SERVER_MATCH = "SVR.NAME = {server_name}"

# db_id별 서버 매칭 식 분기 (미등록 db_id는 기본식 사용).
# 공동존 폴스타(gp/yd)는 r.name(=SVR.NAME) 매칭을 명시적으로 고정한다 —
# 프로필 query_guide의 "[filter_conditions 필드명 재매핑]" 규칙과 동일 취지.
_SERVER_MATCH_BY_DB_ID: dict[str, str] = {
    "polestar_cm_gp": _DEFAULT_SERVER_MATCH,
    "polestar_cm_yd": _DEFAULT_SERVER_MATCH,
}

# 폴스타 테이블은 'polestar' 스키마에 존재한다 — search_path에 포함되지 않으므로
# 반드시 스키마로 한정해야 한다 (query_generator 메트릭 템플릿 polestar.cmm_resource 와 동일).
# 인스턴스별 스키마가 다르면 여기서 db_id별로 분기한다 (미등록 db_id는 기본값 사용).
_DEFAULT_SCHEMA = "polestar"
_SCHEMA_BY_DB_ID: dict[str, str] = {}


def _table(db_id: str, name: str) -> str:
    """db_id에 해당하는 스키마로 한정된 테이블 참조를 반환한다."""
    schema = _SCHEMA_BY_DB_ID.get(db_id, _DEFAULT_SCHEMA)
    return f"{schema}.{name}"


def _sql_literal(value: str) -> str:
    """문자열을 안전한 SQL 리터럴로 변환한다 (작은따옴표 이스케이프).

    DBHub execute_sql이 파라미터 바인딩을 지원하지 않으므로, 폴스타 알람 정의명·서버명
    등 외부 입력 문자열은 반드시 이 함수를 거쳐 보간한다 (Plan 47 §5.3).
    """
    cleaned = value.replace("\x00", "").replace("'", "''")
    return f"'{cleaned}'"


def build_history_sql(
    db_id: str,
    alarm_name: str,
    server_name: str,
    lookback_start: datetime,
    max_rows: int,
) -> str:
    """이력 조회 고정 SQL을 조립한다 (읽기 전용 SELECT 단일문).

    Template C-2/C-6 패턴 준수:
    - CMM_ALARM_ACTIVE JOIN 없음 (이력 조회)
    - ALARMSEVERITY IN (0,1,2,3) — 0=해소 포함 (D-030)
    - CR.DTIME IS NULL — 삭제된 리소스 제외
    - 서버 매칭: PLATFORM_RESOURCE_ID COALESCE JOIN으로 server.Server(SVR) 식별
    - RESOURCE_CONF_ID JOIN 미포함 (D-022)
    """
    server_match = _SERVER_MATCH_BY_DB_ID.get(db_id, _DEFAULT_SERVER_MATCH).format(
        server_name=_sql_literal(server_name),
    )
    t_alarm = _table(db_id, "cmm_alarm")
    t_alarm_def = _table(db_id, "cmm_alarm_def")
    t_resource = _table(db_id, "cmm_resource")
    return (
        "SELECT\n"
        "    CA.ID AS alarm_id,\n"
        "    CA.CTIME AS alarm_time,\n"
        "    CA.ALARMSEVERITY AS severity,\n"
        "    CA.CURRENTALARMSTATUS AS alarm_status,\n"
        "    CR.NAME AS resource_name\n"
        f"FROM {t_alarm} CA\n"
        f"JOIN {t_alarm_def} D ON CA.DEFINITION_ID = D.ID\n"
        f"JOIN {t_resource} CR ON CA.RESOURCE_ID = CR.ID\n"
        f"JOIN {t_resource} SVR ON SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)\n"
        "                     AND SVR.RESOURCE_TYPE = 'server.Server'\n"
        "                     AND SVR.DTIME IS NULL\n"
        "WHERE CR.DTIME IS NULL\n"
        "  AND CA.ALARMSEVERITY IN (0, 1, 2, 3)\n"
        f"  AND D.NAME = {_sql_literal(alarm_name)}\n"
        f"  AND {server_match}\n"
        f"  AND CA.CTIME >= TO_TIMESTAMP('{lookback_start.strftime(_TIME_FMT)}', "
        "'YYYY-MM-DD HH24:MI:SS')\n"
        "ORDER BY CA.CTIME DESC\n"
        f"LIMIT {int(max_rows)}"
    )


def _row_value(row: dict[str, Any], key: str) -> Any:
    """행에서 컬럼 값을 대소문자 무시하고 조회한다 (DB 드라이버별 키 케이스 상이)."""
    if key in row:
        return row[key]
    upper = key.upper()
    if upper in row:
        return row[upper]
    for k, v in row.items():
        if k.lower() == key:
            return v
    return None


def _parse_alarm_time(value: Any) -> Optional[datetime]:
    """CTIME 값을 datetime으로 변환한다 (datetime / ISO 문자열 / 공백 구분 문자열 허용)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip().replace(" ", "T", 1) if " " in value.strip() else value.strip()
        # 마이크로초·타임존 표기까지 fromisoformat이 처리
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
        try:
            return datetime.strptime(value.strip(), _TIME_FMT)
        except ValueError:
            return None
    return None


class PolestarAlarmHistoryRepository:
    """폴스타 DB에서 동일 (서버, 알람명) 이력을 고정 SQL로 조회한다."""

    def __init__(self, registry, alarm_cfg) -> None:  # noqa: ANN001
        """리포지토리를 초기화한다.

        Args:
            registry: DBRegistry (get_client/is_registered 사용)
            alarm_cfg: AlarmConfig (history_lookback_days/history_max_rows 사용)
        """
        self._registry = registry
        self._alarm_cfg = alarm_cfg

    def is_db_registered(self, db_id: str) -> bool:
        """event.db_id가 DB 레지스트리에 등록되어 있는지 확인한다."""
        return bool(self._registry.is_registered(db_id))

    async def fetch(self, event: AlarmEvent) -> list[AlarmHistoryEntry]:
        """DBRegistry.get_client(event.db_id)로 SELECT 1회 실행 후 변환한다.

        - event.db_id가 레지스트리 미등록이면 빈 리스트 반환 (warning 로그).
          ※ 호출부(enricher)는 미등록 여부를 먼저 확인하여 history_stats=None으로
          처리해야 한다 — 빈 리스트로 통계를 계산하면 "첫 발생" 오판이 발생한다.
        - 행 수 상한: LIMIT {history_max_rows}
        - lookback_start = event.alarm_time - history_lookback_days (처리 시점 now 기준 아님)
        """
        if not self.is_db_registered(event.db_id):
            logger.warning(
                "알람 이력 조회 건너뜀 — 미등록 db_id: %s (alarm_id=%s)",
                event.db_id,
                event.alarm_id,
            )
            return []

        lookback_start = event.alarm_time - timedelta(
            days=self._alarm_cfg.history_lookback_days
        )
        sql = build_history_sql(
            db_id=event.db_id,
            alarm_name=event.alarm_name,
            server_name=event.server_name,
            lookback_start=lookback_start,
            max_rows=self._alarm_cfg.history_max_rows,
        )
        async with self._registry.get_client(event.db_id) as client:
            result = await client.execute_sql(sql)

        entries: list[AlarmHistoryEntry] = []
        for row in result.rows:
            alarm_time = _parse_alarm_time(_row_value(row, "alarm_time"))
            if alarm_time is None:
                logger.debug("알람 이력 행 시각 파싱 실패 — 건너뜀: %r", row)
                continue
            try:
                severity = int(_row_value(row, "severity"))
            except (TypeError, ValueError):
                logger.debug("알람 이력 행 심각도 파싱 실패 — 건너뜀: %r", row)
                continue
            entries.append(
                AlarmHistoryEntry(
                    alarm_id=str(_row_value(row, "alarm_id")),
                    severity=severity,
                    alarm_status=str(_row_value(row, "alarm_status") or ""),
                    resource_name=str(_row_value(row, "resource_name") or ""),
                    alarm_time=alarm_time,
                )
            )
        logger.debug(
            "알람 이력 조회 완료: db_id=%s server=%s alarm=%s rows=%d",
            event.db_id,
            event.server_name,
            event.alarm_name,
            len(entries),
        )
        return entries
