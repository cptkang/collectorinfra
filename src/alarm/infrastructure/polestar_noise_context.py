"""폴스타 DB 노이즈 컨텍스트 조회 리포지토리 (Plan 52, Phase E1).

알람 발송 판단(notification gate)에 필요한 **억제 신호**를 폴스타 DB에서 **고정 SQL**
(LLM 미사용, 읽기 전용 SELECT 단일문)로 수집한다. 기존 DBHub(MCP) 경로
(`DBRegistry.get_client(db_id)` → `execute_sql`)를 재사용한다(Plan 47 history repo 패턴 계승).

Phase E1 수집 범위 (Plan 52 §10):
    1. **자산 중요도** — `cmm_resource.IMPORTANCE_ID` (원값 그대로, 매핑은 정책 계층 책임)
    2. **유지보수 상태** — `cmm_resource.IS_MAINTENANCE` (불리언)
    3. **폴스타 알림정책** — `cmm_alarm_def_noti` 통보대상 존재 여부 (보수적 판정)

수집 제외 (E2 범위, Plan 52 §3.6 — 본 파일에서 절대 수집 금지):
    - 의존성/부모 상태(`AVAIL_DEPEND_RESOURCE_ID` + 부모 `AVAIL_STATUS`). 반환 dict의
      `parent_avail_status`는 항상 None(자리예약).

서버 매칭 규칙 (Plan 47 §5.3, Known Mistakes 2026-06-10 계승):
    - 알람 이벤트의 `server_name`(=${platformName})은 폴스타 등록 서버명으로, server.Server
      행의 `CMM_RESOURCE.NAME`과 매칭한다.
    - 공동존 폴스타(polestar_cm_gp/yd)는 r.name(=SVR.NAME)으로 매칭한다 — hostname 매칭 금지.
    - 중요도/유지보수는 **서버 본체(server.Server) 행**의 값이므로, 알람 하위 자원에서
      COALESCE로 거슬러 올라갈 필요 없이 server.Server 행을 NAME으로 직접 식별한다.

안전 제약:
    - 읽기 전용 SELECT 단일문만 생성한다. DML/DDL 절대 금지.
    - 외부 입력(alarm_name/server_name)은 반드시 `_sql_literal`로 이스케이프한다
      (DBHub execute_sql은 파라미터 바인딩 미지원).
    - D-022: RESOURCE_CONF_ID JOIN 절대 금지. 고정 SQL에 불확실 조건(is_lob 등) 추가 금지.
    - graceful degradation: 미등록 db_id / 조회 실패 / 빈 결과에 예외를 전파하지 않는다 —
      `source="unavailable"` 또는 부분 None을 반환한다.
    - 캐시/타임아웃은 구현하지 않는다(enricher 책임) — 순수 fetch만 수행한다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.alarm.domain.alarm import AlarmEvent

logger = logging.getLogger(__name__)

# 서버 매칭 식 — {server_name}에 안전 처리된 SQL 리터럴이 보간된다.
# SVR은 server.Server 행(= cmm_resource) 별칭이므로 SVR.NAME = 폴스타 등록 서버명이다.
_DEFAULT_SERVER_MATCH = "SVR.NAME = {server_name}"

# db_id별 서버 매칭 식 분기 (미등록 db_id는 기본식 사용).
# 공동존 폴스타(gp/yd)는 r.name(=SVR.NAME) 매칭을 명시 고정한다 — hostname 매칭 금지.
_SERVER_MATCH_BY_DB_ID: dict[str, str] = {
    "polestar_cm_gp": _DEFAULT_SERVER_MATCH,
    "polestar_cm_yd": _DEFAULT_SERVER_MATCH,
}

# 폴스타 테이블은 'polestar' 스키마에 존재한다 — search_path에 포함되지 않으므로
# 반드시 스키마로 한정해야 한다 (미한정 시 public에서 relation does not exist).
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
    등 외부 입력 문자열은 반드시 이 함수를 거쳐 보간한다 (Plan 47 §5.3 계승).
    """
    cleaned = value.replace("\x00", "").replace("'", "''")
    return f"'{cleaned}'"


def build_resource_signal_sql(db_id: str, server_name: str) -> str:
    """서버 본체(server.Server)의 중요도/유지보수 신호 조회 SQL을 조립한다.

    읽기 전용 SELECT 단일문. server.Server 행을 NAME(서버명)으로 직접 식별한다
    (알람 하위 자원에서 시작하지 않으므로 PLATFORM_RESOURCE_ID COALESCE 불필요).
    - DTIME IS NULL — 삭제된 리소스 제외
    - RESOURCE_CONF_ID JOIN 미포함 (D-022)
    - LIMIT 1 — 단일 서버행만 필요
    """
    server_match = _SERVER_MATCH_BY_DB_ID.get(db_id, _DEFAULT_SERVER_MATCH).format(
        server_name=_sql_literal(server_name),
    )
    t_resource = _table(db_id, "cmm_resource")
    return (
        "SELECT\n"
        "    SVR.IMPORTANCE_ID AS importance_id,\n"
        "    SVR.IS_MAINTENANCE AS maintenance\n"
        f"FROM {t_resource} SVR\n"
        "WHERE SVR.DTIME IS NULL\n"
        "  AND SVR.RESOURCE_TYPE = 'server.Server'\n"
        f"  AND {server_match}\n"
        "LIMIT 1"
    )


def build_noti_policy_sql(db_id: str, alarm_name: str) -> str:
    """폴스타 알림정책(통보대상) 존재 여부 조회 SQL을 조립한다.

    읽기 전용 SELECT 단일문. 알람 정의명(D.NAME)으로 정의를 식별하고, 해당 정의에
    매핑된 통보정의(cmm_alarm_def_noti) 행 수를 센다.

    조인 규칙(query_generator 템플릿 실측 — src/prompts/query_generator.py:413):
        DN.DEFINITION_ID = D.MASTERDEFINITION_ID
    (cmm_alarm 이력 조인의 CA.DEFINITION_ID = D.ID 와 키가 다름에 주의)

    보수적 판정: 통보정의 행이 존재하면 "통보대상이 지정됨"으로 본다. 행이 없을 때
    "비통보로 설정됨"이라 단정하지 않는다(부재는 기본/상속 통보일 수 있고, 명시적
    suppress를 가리키는 컬럼 의미가 불확실하므로). → 행 부재는 None으로 처리한다.
    - RESOURCE_CONF_ID JOIN 미포함 (D-022)
    """
    t_alarm_def = _table(db_id, "cmm_alarm_def")
    t_noti = _table(db_id, "cmm_alarm_def_noti")
    return (
        "SELECT COUNT(*) AS noti_count\n"
        f"FROM {t_alarm_def} D\n"
        f"JOIN {t_noti} DN ON DN.DEFINITION_ID = D.MASTERDEFINITION_ID\n"
        f"WHERE D.NAME = {_sql_literal(alarm_name)}"
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


def _to_bool(value: Any) -> Optional[bool]:
    """IS_MAINTENANCE 값을 bool로 변환한다 (1/true→True, 0/false→False, 미상→None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "t", "y", "yes"}:
            return True
        if text in {"0", "false", "f", "n", "no"}:
            return False
    return None


def _parse_importance(rows: list[dict[str, Any]]) -> Optional[Any]:
    """중요도 신호를 파싱한다. **원값을 그대로 보존**한다 (낮음/높음 매핑은 정책 계층 책임).

    행이 없거나 값이 비어 있으면 None을 반환한다. 예외는 잡아 None으로 강등한다.
    """
    try:
        if not rows:
            return None
        value = _row_value(rows[0], "importance_id")
        if value is None or value == "":
            return None
        return value
    except Exception:  # noqa: BLE001 — graceful degradation: 어떤 파싱 오류도 None으로
        logger.debug("중요도 파싱 실패 — None 처리", exc_info=True)
        return None


def _parse_maintenance(rows: list[dict[str, Any]]) -> Optional[bool]:
    """유지보수 상태를 bool로 파싱한다. 행 없음·미상은 None. 예외는 None으로 강등."""
    try:
        if not rows:
            return None
        return _to_bool(_row_value(rows[0], "maintenance"))
    except Exception:  # noqa: BLE001
        logger.debug("유지보수 상태 파싱 실패 — None 처리", exc_info=True)
        return None


def _parse_noti_policy(rows: list[dict[str, Any]]) -> Optional[str]:
    """폴스타 알림정책을 보수적으로 판정한다.

    통보정의(cmm_alarm_def_noti) 행이 1건 이상 존재하면 "notify",
    그렇지 않으면 None을 반환한다. **"suppress"는 반환하지 않는다** — 명시적 비통보를
    가리키는 컬럼 의미가 불확실하므로 보수적으로 단정하지 않는다(Plan 52 §6.2/§6.3).
    """
    try:
        if not rows:
            return None
        count = _row_value(rows[0], "noti_count")
        if count is None:
            return None
        return "notify" if int(count) > 0 else None
    except (TypeError, ValueError):
        logger.debug("알림정책 카운트 파싱 실패 — None 처리", exc_info=True)
        return None


def _unavailable() -> dict[str, Any]:
    """신호 수집 불가 시 반환하는 동결 dict (모든 신호 None, source='unavailable')."""
    return {
        "importance_id": None,
        "maintenance": None,
        "noti_policy": None,
        "parent_avail_status": None,  # E2 자리예약 — 항상 None
        "source": "unavailable",
    }


class PolestarNoiseContextRepository:
    """폴스타 DB에서 알람 노이즈 컨텍스트(중요도/유지보수/알림정책)를 고정 SQL로 조회한다."""

    def __init__(self, registry, alarm_cfg) -> None:  # noqa: ANN001
        """리포지토리를 초기화한다.

        Args:
            registry: DBRegistry (get_client/is_registered 사용)
            alarm_cfg: AlarmConfig (인터페이스 일관성용 — 본 Phase E1 SQL은 미사용)
        """
        self._registry = registry
        self._alarm_cfg = alarm_cfg

    def is_db_registered(self, db_id: str) -> bool:
        """event.db_id가 DB 레지스트리에 등록되어 있는지 확인한다."""
        return bool(self._registry.is_registered(db_id))

    async def fetch(self, event: AlarmEvent) -> dict[str, Any]:
        """노이즈 컨텍스트를 수집하여 동결 dict로 반환한다 (Plan 52 §8.2 계약).

        반환 dict (동결):
            {"importance_id": int|str|None,   # 원값 그대로
             "maintenance": bool|None,
             "noti_policy": "notify"|None,     # 보수적 — "suppress" 미반환
             "parent_avail_status": None,      # E2 자리예약 — 항상 None
             "source": "polestar_db"|"unavailable"}

        graceful degradation:
            - 미등록 db_id → 즉시 source="unavailable" (DB 미접근).
            - 조회/연결 예외 → 잡아서 source="unavailable" (예외 전파 금지).
            - 빈 결과/파싱 실패 → 해당 신호만 None, source="polestar_db" (부분 반환).
        """
        if not self.is_db_registered(event.db_id):
            logger.warning(
                "노이즈 컨텍스트 조회 건너뜀 — 미등록 db_id: %s (alarm_id=%s)",
                event.db_id,
                event.alarm_id,
            )
            return _unavailable()

        resource_sql = build_resource_signal_sql(event.db_id, event.server_name)
        noti_sql = build_noti_policy_sql(event.db_id, event.alarm_name)
        try:
            async with self._registry.get_client(event.db_id) as client:
                resource_result = await client.execute_sql(resource_sql)
                noti_result = await client.execute_sql(noti_sql)
        except Exception as exc:  # noqa: BLE001 — graceful: 연결/조회 실패는 unavailable
            logger.warning(
                "노이즈 컨텍스트 조회 실패 — db_id=%s alarm_id=%s: %s",
                event.db_id,
                event.alarm_id,
                exc,
            )
            return _unavailable()

        resource_rows = list(getattr(resource_result, "rows", []) or [])
        noti_rows = list(getattr(noti_result, "rows", []) or [])
        context = {
            "importance_id": _parse_importance(resource_rows),
            "maintenance": _parse_maintenance(resource_rows),
            "noti_policy": _parse_noti_policy(noti_rows),
            "parent_avail_status": None,  # E2 자리예약 — 본 파일에서 수집 금지
            "source": "polestar_db",
        }
        logger.debug(
            "노이즈 컨텍스트 조회 완료: db_id=%s server=%s alarm=%s ctx=%r",
            event.db_id,
            event.server_name,
            event.alarm_name,
            context,
        )
        return context
