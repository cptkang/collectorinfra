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

가용성 사전 판정 (Plan 81 / D-175):
    같은 `cmm_resource` 행에 `avail_status`·`is_maintenance`가 있으므로 **해소 SELECT에
    두 컬럼을 더해** 판정 근거를 함께 가져온다 — 프로세스 조회 경로의 **DB 왕복이 늘지 않는다**
    (`resolve_with_status`). 다대상 경로는 `lookup_many`가 IN 절 **1쿼리**로 처리한다.
    판정 자체는 `src.domain.host_availability`(순수)가 하고 여기서는 조회만 한다.

계층: infrastructure (`polestar_history.py`와 동일 — DBHub 읽기 전용 SELECT 재사용).
    D-022: RESOURCE_CONF_ID JOIN 미사용. `is_lob` 조건 미사용(2026-06-10 정합).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.domain.host_availability import HostAvailability, judge_availability
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
        # 가용성 판정 컬럼 2개를 함께 가져온다(Plan 81) — 별도 쿼리를 두면 왕복이 배가된다.
        "SELECT r.hostname AS hostname, r.name AS name,\n"
        "       r.avail_status AS avail_status, r.is_maintenance AS is_maintenance\n"
        f"FROM {t_resource} r\n"
        "WHERE r.resource_type = 'server.Server'\n"
        "  AND r.dtime IS NULL\n"
        f"  AND (r.name = {lit} OR r.hostname = {lit})\n"
        f"ORDER BY CASE WHEN r.name = {lit} THEN 0 ELSE 1 END\n"
        f"{row_limit}"
    )


def build_host_status_sql(
    db_id: str, values: list[str], db_engine: str = "postgresql"
) -> str:
    """여러 서버명/호스트명의 가용성 근거를 **한 번에** 조회하는 SELECT를 조립한다 (Plan 81).

    다대상 fan-out에서 대상마다 쿼리를 날리면 왕복이 N배가 된다 — 그 비용이 이 기능의
    도입 근거(불필요한 호출 제거)와 정면으로 충돌한다. IN 절 1쿼리로 처리한다.

    `LIMIT`을 붙이지 않는다 — 대상 수만큼만 매칭되고 실행 클라이언트 `max_rows`가 안전망이다
    (`src/nodes/realtime_usage.py:_server_list_sql` 선례).

    Args:
        db_id: 조회 대상 폴스타 인스턴스
        values: 서버명 또는 호스트명 목록(빈 값은 호출부에서 제거)
        db_engine: 대상 DB 엔진("db2"면 무스키마 참조)

    Returns:
        읽기 전용 단일 SELECT 문자열
    """
    t_resource = _table(db_id, "cmm_resource", db_engine)
    literals = ", ".join(_sql_literal(v) for v in values)
    return (
        "SELECT r.hostname AS hostname, r.name AS name,\n"
        "       r.avail_status AS avail_status, r.is_maintenance AS is_maintenance\n"
        f"FROM {t_resource} r\n"
        "WHERE r.resource_type = 'server.Server'\n"
        "  AND r.dtime IS NULL\n"
        f"  AND (r.name IN ({literals}) OR r.hostname IN ({literals}))"
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


def _now_text() -> str:
    """판정 근거를 **언제 조회했는지**를 남긴다.

    `cmm_resource.mtime`을 쓰지 않는 이유: mtime은 리소스 수정 시각이라 "가용성을 언제
    확인했는지"를 말하지 못한다. 사용자에게 보이는 "확인 시각"은 조회 시각이 정확하다.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class HostLookup:
    """hostname 해소 + 가용성 판정을 함께 담은 조회 결과 (Plan 81).

    Attributes:
        hostname: 해소된 정규 hostname. None이면 호출부가 원시 입력 값을 그대로 쓴다(D-046)
        server_name: 매칭된 `cmm_resource.name`(관찰성 — 서버명≠호스트명 병기용)
        availability: 가용성 판정. 조회 실패·미등록도 판정 객체로 표현된다(침묵 금지)
    """

    hostname: Optional[str]
    server_name: Optional[str]
    availability: HostAvailability


def build_resolver(app_config):  # noqa: ANN001 — AppConfig
    """앱 설정으로 리졸버를 만든다 (DBRegistry는 지연 import — 순환 회피).

    Args:
        app_config: 앱 설정

    Returns:
        `PolestarHostnameResolver`
    """
    from src.routing.db_registry import DBRegistry

    return PolestarHostnameResolver(DBRegistry(app_config))


async def lookup_host(app_config, db_id: str, value: str) -> "HostLookup":  # noqa: ANN001
    """대상 1건의 hostname·가용성을 조회한다 — **세 진입 경로 공용** (Plan 81).

    `process_query`(orchestration) · `fault_diagnosis`(application) ·
    `investigation_trigger`(application)가 **같은 함수**를 쓴다. 각자 구현하면 fail-open
    규약이 한쪽에만 들어간다(D-171 G5 선례).

    어떤 실패도 예외를 올리지 않는다 — 판정 불가(`lookup_failed`)로 돌려 조회를 막지 않는다.

    Args:
        app_config: 앱 설정
        db_id: 대상 폴스타 인스턴스
        value: 서버명 또는 호스트명

    Returns:
        `HostLookup`
    """
    try:
        return await build_resolver(app_config).resolve_with_status(db_id, value)
    except Exception as exc:  # noqa: BLE001 — 판정 실패가 조회·조사를 막지 않는다
        logger.warning(
            "가용성 조회 중 예외 — 판정 없이 진행: db_id=%s value=%s err=%s",
            db_id, value, exc,
        )
        return HostLookup(None, None, judge_availability(lookup_failed=True))


async def lookup_hosts(
    app_config, db_id: str, values: list[str]  # noqa: ANN001
) -> dict[str, "HostLookup"]:
    """대상 N건의 hostname·가용성을 **1쿼리**로 조회한다 (Plan 81 fan-out 공용)."""
    try:
        return await build_resolver(app_config).lookup_many(db_id, values)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "가용성 배치 조회 중 예외 — 판정 없이 진행: db_id=%s n=%d err=%s",
            db_id, len(values or []), exc,
        )
        return {
            v: HostLookup(None, None, judge_availability(lookup_failed=True))
            for v in (values or [])
        }


def _server_name_of(row: dict[str, Any]) -> Optional[str]:
    """행에서 `cmm_resource.name`(서버명)을 꺼낸다(없으면 None)."""
    name = _row_value(row, "name")
    return str(name).strip() if name and str(name).strip() else None


def _judge_row(row: dict[str, Any], as_of: str) -> HostAvailability:
    """조회 행의 판정 컬럼으로 가용성을 판정한다(값 해석은 domain 소관)."""
    return judge_availability(
        found=True,
        avail_status=_row_value(row, "avail_status"),
        is_maintenance=_row_value(row, "is_maintenance"),
        as_of=as_of,
    )


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

        Plan 81 이후 본체는 `resolve_with_status`이고 이 메서드는 **종전 반환형을 유지하는
        얇은 래퍼**다(사본 금지 — 두 경로가 같은 조회를 쓴다). 동작·로그 문구는 불변이다.

        Args:
            db_id: 조회 대상 폴스타 인스턴스 식별자
            value: 사용자 입력 식별자(서버명/장비명 또는 호스트명)

        Returns:
            일치하는 server.Server 행의 hostname. 미등록/실패/0건/빈 값이면 None.
        """
        return (await self.resolve_with_status(db_id, value)).hostname

    async def resolve_with_status(self, db_id: str, value: str) -> HostLookup:
        """hostname 해소와 **가용성 판정을 한 번의 조회로** 수행한다 (Plan 81).

        판정 컬럼이 해소 SELECT에 함께 실리므로 **DB 왕복이 늘지 않는다.**
        판정 자체는 `judge_availability`(domain·순수)가 한다 — 여기서는 조회만 한다.

        Args:
            db_id: 조회 대상 폴스타 인스턴스 식별자
            value: 사용자 입력 식별자(서버명/장비명 또는 호스트명)

        Returns:
            `HostLookup`. 조회 실패·미등록 db_id는 판정 불가(`lookup_failed`)로 표현되며,
            hostname은 None이라 호출부가 원시 값으로 폴백한다(D-046 규약 유지).
        """
        as_of = _now_text()
        if not value or not db_id:
            return HostLookup(None, None, judge_availability(lookup_failed=True, as_of=as_of))
        if not self._registry.is_registered(db_id):
            logger.debug("hostname 해소 건너뜀 — 미등록 db_id: %s", db_id)
            return HostLookup(None, None, judge_availability(lookup_failed=True, as_of=as_of))

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
            return HostLookup(None, None, judge_availability(lookup_failed=True, as_of=as_of))

        first_row: Optional[dict[str, Any]] = None
        for row in result.rows:
            if not isinstance(row, dict):
                continue
            if first_row is None:
                first_row = row
            hostname = _row_value(row, "hostname")
            if hostname and str(hostname).strip():
                resolved = str(hostname).strip()
                logger.info(
                    "hostname 해소: db_id=%s 입력='%s' → hostname='%s'",
                    db_id, value, resolved,
                )
                return HostLookup(resolved, _server_name_of(row), _judge_row(row, as_of))
        logger.info("hostname 해소 결과 없음(원시 값 사용): db_id=%s value='%s'", db_id, value)
        if first_row is not None:
            # 행은 있으나 hostname이 비었다 — 대상은 등록돼 있으므로 가용성은 판정할 수 있다.
            return HostLookup(None, _server_name_of(first_row), _judge_row(first_row, as_of))
        return HostLookup(
            None, None, judge_availability(found=False, as_of=as_of)
        )

    async def lookup_many(self, db_id: str, values: list[str]) -> dict[str, HostLookup]:
        """여러 대상의 hostname·가용성을 **1쿼리**로 조회한다 (Plan 81 fan-out 경로).

        단일 경로와 **같은 매칭 규칙**을 쓴다(name 우선 → hostname). 대상마다 쿼리를 날리면
        왕복이 N배가 되므로 IN 절 하나로 처리한다.

        Args:
            db_id: 조회 대상 폴스타 인스턴스
            values: 서버명/호스트명 목록

        Returns:
            `{입력 값: HostLookup}`. 조회 실패 시 전 항목이 `lookup_failed` 판정으로 채워진다
            (일부만 비우면 호출부가 "판정 없음"과 "판정 불가"를 구분하지 못한다).
        """
        as_of = _now_text()
        targets = [v for v in dict.fromkeys(values or []) if v and str(v).strip()]
        if not targets or not db_id or not self._registry.is_registered(db_id):
            return {
                v: HostLookup(None, None, judge_availability(lookup_failed=True, as_of=as_of))
                for v in targets
            }

        domain = get_domain_by_id(db_id)
        db_engine = domain.db_engine if domain else "postgresql"
        sql = build_host_status_sql(db_id, targets, db_engine)
        try:
            async with self._registry.get_client(db_id) as client:
                result = await client.execute_sql(sql)
        except Exception as exc:
            logger.warning(
                "가용성 배치 조회 실패 — 판정 없이 진행: db_id=%s engine=%s n=%d err=%s sql=%s",
                db_id, db_engine, len(targets), exc, sql,
            )
            return {
                v: HostLookup(None, None, judge_availability(lookup_failed=True, as_of=as_of))
                for v in targets
            }

        by_name: dict[str, dict[str, Any]] = {}
        by_hostname: dict[str, dict[str, Any]] = {}
        for row in result.rows:
            if not isinstance(row, dict):
                continue
            name = _row_value(row, "name")
            hostname = _row_value(row, "hostname")
            if name and str(name).strip():
                by_name.setdefault(str(name).strip(), row)
            if hostname and str(hostname).strip():
                by_hostname.setdefault(str(hostname).strip(), row)

        out: dict[str, HostLookup] = {}
        for value in targets:
            row = by_name.get(value) or by_hostname.get(value)
            if row is None:
                out[value] = HostLookup(
                    None, None, judge_availability(found=False, as_of=as_of)
                )
                continue
            hostname = _row_value(row, "hostname")
            resolved = str(hostname).strip() if hostname and str(hostname).strip() else None
            out[value] = HostLookup(resolved, _server_name_of(row), _judge_row(row, as_of))
        logger.info(
            "가용성 배치 조회: db_id=%s 대상=%d 매칭=%d",
            db_id, len(targets), sum(1 for lk in out.values() if lk.server_name),
        )
        return out
