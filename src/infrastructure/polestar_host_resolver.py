"""폴스타 hostname 해석기 (Plan 48 §5.2).

프로세스 API는 **hostname**으로만 조회되지만 사용자는 보통 **서버명/장비명**
(=`cmm_resource.name`)으로 질의한다. 서버명과 hostname은 완전히 다른 문자열이라
서버명을 그대로 hostname으로 넣으면 0건이 난다 (Plan 47-1 §9·D-036).

핵심 원칙 (Plan 48 §3.4):
    - 서버명/hostname을 문자열 패턴으로 구분하려 시도하지 않는다 (오판 발생).
    - `cmm_resource`에 `name`·`hostname` 컬럼이 **모두** 존재하므로, 두 컬럼을
      동시 매칭(`WHERE name = <id> OR hostname = <id>`)하여 입력이 어느 쪽이든
      정규 hostname으로 해석한다. 정확한 hostname 매칭을 서버명 매칭보다 우선한다.

제약:
    - **읽기 전용**: 단일 SELECT 단일문. INSERT/UPDATE/DDL 생성 금지.
    - **DBHub는 파라미터 바인딩 미지원** → identifier는 `_sql_literal()`로 이스케이프
      (작은따옴표 `''` + `\x00` 제거)하여 인젝션을 차단한다. f-string 직접 보간 금지
      (polestar_history.py `_sql_literal` 패턴 그대로 재사용).
    - **스키마 한정 필수**: `cmm_resource`는 `polestar` 스키마에 있으므로
      `polestar.cmm_resource`로 한정한다.
    - **db_engine별 LIMIT**: db2 = `FETCH FIRST 2 ROWS ONLY`, postgresql = `LIMIT 2`.
      모호성(서로 다른 hostname 2건↑) 감지를 위해 2건까지 조회한다.
    - 조회 자체는 try/except로 graceful 처리한다 (예외를 호출부로 던지지 않는다).

계층: infrastructure. application(노드)이 본 모듈을 주입받아 사용한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.routing.domain_config import get_domain_by_id

logger = logging.getLogger(__name__)

# 폴스타 테이블은 'polestar' 스키마에 존재 — search_path 미포함이라 스키마 한정 필수
# (polestar_history.py / query_generator 메트릭 템플릿과 동일).
_SCHEMA = "polestar"
_TABLE = f"{_SCHEMA}.cmm_resource"


def _sql_literal(value: str) -> str:
    """문자열을 안전한 SQL 리터럴로 변환한다 (작은따옴표 이스케이프).

    DBHub execute_sql이 파라미터 바인딩을 지원하지 않으므로, 외부 입력 문자열은
    반드시 이 함수를 거쳐 보간한다 (polestar_history.py `_sql_literal`과 동일 패턴).
    """
    cleaned = value.replace("\x00", "").replace("'", "''")
    return f"'{cleaned}'"


def _row_value(row: dict[str, Any], key: str) -> Any:
    """행에서 컬럼 값을 대소문자 무시하고 조회한다 (DB 드라이버별 키 케이스 상이).

    polestar_history.py `_row_value`와 동일 패턴.
    """
    if key in row:
        return row[key]
    upper = key.upper()
    if upper in row:
        return row[upper]
    for k, v in row.items():
        if k.lower() == key.lower():
            return v
    return None


def _limit_clause(db_engine: str, n: int) -> str:
    """db_engine에 맞는 상위 N 제한 절을 반환한다.

    - db2: ``FETCH FIRST {n} ROWS ONLY``
    - 그 외(postgresql 등): ``LIMIT {n}``
    """
    if (db_engine or "").lower() == "db2":
        return f"FETCH FIRST {int(n)} ROWS ONLY"
    return f"LIMIT {int(n)}"


def build_resolve_sql(identifier: str, db_engine: str) -> str:
    """name/hostname 동시 매칭 read-only SELECT를 조립한다 (Plan 48 §3.4).

    - identifier는 `_sql_literal`로 이스케이프하여 인젝션을 차단한다.
    - 정확한 hostname 매칭을 서버명 매칭보다 우선(`ORDER BY CASE`).
    - 모호성 감지를 위해 db_engine별 LIMIT로 2건까지 조회한다.
    """
    lit = _sql_literal(identifier)
    limit = _limit_clause(db_engine, 2)
    return (
        "SELECT hostname\n"
        f"FROM {_TABLE}\n"
        f"WHERE name = {lit} OR hostname = {lit}\n"
        f"ORDER BY CASE WHEN hostname = {lit} THEN 0 ELSE 1 END\n"
        f"{limit}"
    )


@dataclass
class HostResolution:
    """hostname 해석 결과 (Plan 48 §5.2).

    - hostname is not None  → 단일 확정 (그 값으로 API 호출)
    - candidates 2건 이상     → 모호 (HITL 되물음/후보 안내, 임의 선택 금지)
    - 둘 다 비어있음          → 미해석 (호출부에서 §3.4 폴백 — 직접 hostname/타 db 재해석)
    """

    hostname: Optional[str] = None
    candidates: list[str] = field(default_factory=list)


class PolestarHostResolver:
    """서버명/hostname(token) → cmm_resource 정규 hostname 해석 (읽기 전용 조회).

    DB 접근은 `get_db_client` 동등 팩토리(async context manager 반환)와 app_config를
    주입받아 수행한다. 메인 그래프 노드와 동일한 DB 접근 경로를 재사용한다
    (`async with db_client_factory(app_config, db_id=...) as client:` → `execute_sql`).
    """

    def __init__(self, db_client_factory, app_config) -> None:  # noqa: ANN001
        """리졸버를 초기화한다.

        Args:
            db_client_factory: `get_db_client`(또는 동등) — (app_config, db_id) 인자로
                async context manager를 반환하여 `execute_sql(sql)`을 제공한다.
            app_config: 애플리케이션 설정 (db_client_factory에 그대로 전달).
        """
        self._db_client_factory = db_client_factory
        self._app_config = app_config

    async def resolve(self, db_id: str, identifier: str) -> HostResolution:
        """cmm_resource에서 name OR hostname == identifier 인 행의 hostname을 해석한다.

        Args:
            db_id: 활성 폴스타 db 식별자.
            identifier: 서버명 또는 hostname 원문 (외부 입력 — 이스케이프 대상).

        Returns:
            - 단일 hostname → HostResolution(hostname=...)
            - 서로 다른 hostname 2건 이상(모호) → HostResolution(candidates=[...])
              (중복 hostname은 1건으로 취급)
            - 0건 / 비폴스타 db(cmm_resource 부재) / 예외 → 빈 HostResolution
              (호출부에서 §3.4 폴백)
        """
        if not identifier or not db_id:
            return HostResolution()

        domain = get_domain_by_id(db_id)
        if domain is None:
            # 비폴스타/미등록 db — cmm_resource 조회 불가
            logger.debug("hostname 해석 건너뜀 — 미등록 db_id: %s", db_id)
            return HostResolution()

        sql = build_resolve_sql(identifier, domain.db_engine)

        try:
            async with self._db_client_factory(
                self._app_config, db_id=db_id
            ) as client:
                result = await client.execute_sql(sql)
            rows = result.rows
        except Exception as exc:
            # cmm_resource 부재(비폴스타 스키마)·DB 장애 등 — graceful (폴백 대상)
            logger.warning(
                "hostname 해석 조회 실패 — 폴백 진행: db_id=%s identifier=%r err=%s",
                db_id,
                identifier,
                exc,
            )
            return HostResolution()

        # 중복 hostname은 1건으로 취급 (입력 순서 보존)
        hostnames: list[str] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            value = _row_value(row, "hostname")
            if value is None:
                continue
            host = str(value).strip()
            if host and host not in hostnames:
                hostnames.append(host)

        if not hostnames:
            return HostResolution()
        if len(hostnames) == 1:
            return HostResolution(hostname=hostnames[0])
        return HostResolution(candidates=hostnames)
