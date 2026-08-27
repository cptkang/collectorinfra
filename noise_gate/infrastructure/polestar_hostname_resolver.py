"""서버명(cmm_resource.name) → 호스트명(cmm_resource.hostname) 해소 (D-046).

폴스타 실시간 프로세스 API(`polestar_process_api.py`)의 조회 키는 **hostname**이지만,
사용자는 보통 서버명(cmm_resource.name)으로 질의한다. 공동존 폴스타(gp/yd)에서는
name ≠ hostname 이므로(Known Mistakes 2026-06-10) 서버명을 그대로 hostname으로 보내면
프로세스 API가 0건을 반환하고, 결국 환각(리소스명이 '프로세스'인 행을 DB에서 조회)으로
이어진다. 본 모듈은 프로세스 조회 전에 입력 값을 정규 hostname으로 해소한다.

조회 경로:
    기존 DBHub(MCP) 경로(`DBRegistry.get_client(db_id)` → `execute_sql`)로 고정 SELECT
    단일문(LLM 미사용, 읽기 전용)을 실행한다 — `polestar_history.py`와 동일 패턴.

매칭 규칙 (Plan 47 §5.3 / Known Mistakes 2026-06-10 정합):
    - 입력 값을 `cmm_resource.name`(서버명/장비명) 또는 `cmm_resource.hostname`(OS 호스트명)
      양쪽과 비교하여 일치하는 `server.Server` 행의 hostname을 반환한다.
    - name 일치를 우선한다(서버명 질의가 일반적). name 미일치 시 hostname 일치 행 사용.
    - 입력이 이미 hostname이면 그대로(idempotent) 같은 값을 반환한다.
    - 미등록 db_id / 조회 실패 / 0건 / 빈 hostname → None (호출부에서 원시 값 폴백).

계층: infrastructure (`polestar_history.py`와 동일 — DBHub 읽기 전용 SELECT 재사용).
    D-022: RESOURCE_CONF_ID JOIN 미사용. `is_lob` 조건 미사용(2026-06-10 정합).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.routing.domain_config import get_domain_by_id

logger = logging.getLogger(__name__)

# 폴스타 테이블은 'polestar' 스키마에 존재한다(polestar_history.py와 동일 전제).
# 인스턴스별 스키마가 다르면 db_id별로 분기한다(미등록 db_id는 기본값 사용).
_DEFAULT_SCHEMA = "polestar"
_SCHEMA_BY_DB_ID: dict[str, str] = {}


def _sql_literal(value: str) -> str:
    """문자열을 안전한 SQL 리터럴로 변환한다 (작은따옴표 이스케이프).

    DBHub execute_sql이 파라미터 바인딩을 지원하지 않으므로, 외부 입력 문자열은
    반드시 이 함수를 거쳐 보간한다 (polestar_history._sql_literal과 동일 취지).
    """
    cleaned = value.replace("\x00", "").replace("'", "''")
    return f"'{cleaned}'"


def _table(db_id: str, name: str, db_engine: str = "postgresql") -> str:
    """db_id/엔진에 맞는 테이블 참조를 반환한다.

    스키마는 domain_config의 `db_schema`(D-057)를 단일 출처로 사용한다. 설정돼 있으면
    `schema.table`로 한정하고, 비어 있으면 무스키마(연결 CURRENT SCHEMA)로 참조한다.
    DB2(b0)는 db_schema 미설정 시 무스키마로 두되, 실 스키마가 확인되면 domain_config에
    등록하는 즉시 본 경로와 LLM 생성 경로가 동시에 스키마 한정된다.
    (하위호환: domain_config에 값이 없고 PostgreSQL이면 기존 `polestar.` 기본값 유지.)
    """
    domain = get_domain_by_id(db_id)
    schema = (getattr(domain, "db_schema", "") or "") if domain else ""
    if schema:
        return f"{schema}.{name}"
    if db_engine == "db2":
        return name
    schema = _SCHEMA_BY_DB_ID.get(db_id, _DEFAULT_SCHEMA)
    return f"{schema}.{name}"


def build_hostname_sql(db_id: str, value: str, db_engine: str = "postgresql") -> str:
    """서버명/호스트명 → 정규 hostname 해소용 고정 SELECT를 조립한다 (읽기 전용 단일문).

    - server.Server 행만 대상(DTIME IS NULL — 삭제 리소스 제외).
    - name 또는 hostname 일치, name 일치를 우선(ORDER BY).
    - RESOURCE_CONF_ID JOIN 미포함(D-022). is_lob 조건 미사용(2026-06-10).
    - 엔진 인지(D-051): DB2(b0)는 `LIMIT` 미지원 → `FETCH FIRST 1 ROWS ONLY`,
      무스키마 테이블 사용. PostgreSQL(gp/yd)은 `LIMIT 1` + `polestar.` 스키마.
    """
    lit = _sql_literal(value)
    t_resource = _table(db_id, "cmm_resource", db_engine)
    row_limit = "FETCH FIRST 1 ROWS ONLY" if db_engine == "db2" else "LIMIT 1"
    return (
        "SELECT r.hostname AS hostname, r.name AS name\n"
        f"FROM {t_resource} r\n"
        "WHERE r.resource_type = 'server.Server'\n"
        "  AND r.dtime IS NULL\n"
        f"  AND (r.name = {lit} OR r.hostname = {lit})\n"
        f"ORDER BY CASE WHEN r.name = {lit} THEN 0 ELSE 1 END\n"
        f"{row_limit}"
    )


def build_server_identity_sql(db_id: str, hostname: str, db_engine: str = "postgresql") -> str:
    """hostname → 등록 서버명·IP 역조회 고정 SELECT를 조립한다 (D-179 · 읽기 전용 단일문).

    - server.Server 행만 대상(DTIME IS NULL — 삭제 리소스 제외), hostname **완전 일치**.
    - 최대 2행을 읽어 동일 hostname 중복(모호) 여부를 판별한다 — 모호하면 호출부가 승격을 생략.
    - OS는 `core_config_prop`(EAV) `OSType`·`OSVerson`(원본 철자)을 **스칼라 서브쿼리(MAX)** 로 얹는다 — LEFT JOIN과 달리
      속성 행이 여러 개여도 서버 행이 증식되지 않아 모호 판별(2행)이 오염되지 않는다. 조인 키는
      `r.resource_conf_id = cc.configuration_id` — D-022 재검토(2026-07-30)로 확정된 현행 정본
      방식(프롬프트 규칙 9·docs/10 검증 쿼리·D-076 시맨틱 모델과 동일).
    - 엔진 인지(D-051): DB2는 `FETCH FIRST 2 ROWS ONLY`·무스키마, PostgreSQL은 `LIMIT 2`.
    """
    lit = _sql_literal(hostname)
    t_resource = _table(db_id, "cmm_resource", db_engine)
    row_limit = "FETCH FIRST 2 ROWS ONLY" if db_engine == "db2" else "LIMIT 2"
    t_prop = _table(db_id, "core_config_prop", db_engine)
    return (
        "SELECT r.name AS name, r.hostname AS hostname, r.ipaddress AS ipaddress,\n"
        f"       (SELECT MAX(cc.stringvalue_short) FROM {t_prop} cc\n"
        "         WHERE cc.configuration_id = r.resource_conf_id AND cc.name = 'OSType') AS ostype,\n"
        f"       (SELECT MAX(cv.stringvalue_short) FROM {t_prop} cv\n"
        "         WHERE cv.configuration_id = r.resource_conf_id AND cv.name = 'OSVerson') AS osversion\n"
        f"FROM {t_resource} r\n"
        "WHERE r.resource_type = 'server.Server'\n"
        "  AND r.dtime IS NULL\n"
        f"  AND r.hostname = {lit}\n"
        f"{row_limit}"
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


class PolestarHostnameResolver:
    """폴스타 DB에서 서버명/호스트명 입력을 정규 hostname으로 해소한다 (고정 SQL)."""

    def __init__(self, registry) -> None:  # noqa: ANN001 — DBRegistry
        """리졸버를 초기화한다.

        Args:
            registry: DBRegistry (get_client/is_registered 사용)
        """
        self._registry = registry

    async def resolve(self, db_id: str, value: str) -> Optional[str]:
        """입력 값(서버명 또는 호스트명)을 정규 hostname으로 해소한다.

        Args:
            db_id: 조회 대상 폴스타 인스턴스 식별자
            value: 사용자 입력 식별자(서버명/장비명 또는 호스트명)

        Returns:
            일치하는 server.Server 행의 hostname. 미등록/실패/0건/빈 값이면 None.
        """
        if not value or not db_id:
            return None
        if not self._registry.is_registered(db_id):
            logger.debug("hostname 해소 건너뜀 — 미등록 db_id: %s", db_id)
            return None

        # 대상 DB 엔진에 맞는 방언으로 SQL을 생성한다(DB2 b0는 LIMIT 미지원 → FETCH FIRST).
        domain = get_domain_by_id(db_id)
        db_engine = domain.db_engine if domain else "postgresql"
        sql = build_hostname_sql(db_id, value, db_engine)
        try:
            async with self._registry.get_client(db_id) as client:
                result = await client.execute_sql(sql)
        except Exception as exc:
            # 방언/스키마 불일치(예: b0의 CURRENT SCHEMA 미스매치)를 테스트 중 식별할 수 있도록
            # 실패한 SQL을 함께 남긴다(원시 값 폴백 동작은 유지 — 회귀 없음).
            logger.warning(
                "hostname 해소 조회 실패 — 원시 값 폴백: db_id=%s engine=%s value=%s err=%s sql=%s",
                db_id, db_engine, value, exc, sql,
            )
            return None

        for row in result.rows:
            if not isinstance(row, dict):
                continue
            hostname = _row_value(row, "hostname")
            if hostname and str(hostname).strip():
                resolved = str(hostname).strip()
                logger.info(
                    "hostname 해소: db_id=%s 입력='%s' → hostname='%s'",
                    db_id, value, resolved,
                )
                return resolved
        logger.info("hostname 해소 결과 없음(원시 값 사용): db_id=%s value='%s'", db_id, value)
        return None

    async def lookup_identity(self, db_id: str, hostname: str) -> Optional[dict[str, Any]]:
        """hostname → {name, hostname, ip_address, os_type, os_version, ambiguous} 역조회 (D-179).

        미등록 db_id / 조회 실패 / 0건이면 None. 같은 hostname의 server.Server 행이 2건 이상이면
        첫 행 값과 함께 ambiguous=True를 돌려 호출부가 승격을 생략하게 한다.
        """
        if not hostname or not db_id:
            return None
        if not self._registry.is_registered(db_id):
            logger.debug("서버 식별 역조회 건너뜀 — 미등록 db_id: %s", db_id)
            return None
        domain = get_domain_by_id(db_id)
        db_engine = domain.db_engine if domain else "postgresql"
        sql = build_server_identity_sql(db_id, hostname, db_engine)
        try:
            async with self._registry.get_client(db_id) as client:
                result = await client.execute_sql(sql)
        except Exception as exc:
            logger.warning(
                "서버 식별 역조회 실패 — 이벤트 값 유지: db_id=%s engine=%s hostname=%s err=%s sql=%s",
                db_id, db_engine, hostname, exc, sql,
            )
            return None
        rows = [r for r in result.rows if isinstance(r, dict)]
        if not rows:
            logger.info("서버 식별 역조회 0건(이벤트 값 사용): db_id=%s hostname='%s'", db_id, hostname)
            return None
        first = rows[0]
        found = {
            "name": str(_row_value(first, "name") or "").strip(),
            "hostname": str(_row_value(first, "hostname") or "").strip(),
            "ip_address": str(_row_value(first, "ipaddress") or "").strip(),
            "os_type": str(_row_value(first, "ostype") or "").strip(),
            "os_version": str(_row_value(first, "osversion") or "").strip(),
            "ambiguous": len(rows) > 1,
        }
        logger.info(
            "서버 식별 역조회: db_id=%s hostname='%s' → name='%s' ip='%s' os='%s' ver='%s' ambiguous=%s",
            db_id, hostname, found["name"], found["ip_address"], found["os_type"], found["os_version"],
            found["ambiguous"],
        )
        return found
