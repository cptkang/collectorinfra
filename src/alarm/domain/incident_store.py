"""Incident 라이프사이클 저장소 포트 (Plan 52 §9.1 · D-049).

알람 incident(firing→ack→resolved) 상태 전이와 시간창 집계를 위한 추상 인터페이스를
domain 계층에 정의한다. Clean Architecture: 포트는 domain, PostgreSQL 구현체는
infrastructure(`incident_repository.py`)에 배치한다.

domain은 최내곽 계층이므로 asyncpg/redis 등 인프라 의존을 import하지 않는다
(타입 힌트는 표준 라이브러리 타입만 사용). 실제 PG 의존은 infra 구현에만 둔다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class IncidentStore(ABC):
    """incident 라이프사이클 저장소 인터페이스.

    DB 엔진에 독립적인 포트. open(생성)→ack(확인)→resolved(해소) 상태 전이와
    열린 incident 조회·시간창 집계(MTTA/MTTR/사건전환율) 메서드를 정의한다.
    모든 구현은 graceful해야 한다(예외를 삼키고 안전값 반환 — 발송·서버 무차단).
    """

    @abstractmethod
    async def create_open(
        self,
        *,
        fingerprint: str,
        alarm_id: str,
        alarm_name: str = "",
        db_id: str,
        server_name: str,
        severity: int,
        priority: str,
        tier: str,
        created_at: datetime,
    ) -> int:
        """open 상태의 incident를 생성하고 incident id를 반환한다.

        Args:
            fingerprint: 재발생 상관용 안정 식별자(compute_fingerprint 결과)
            alarm_id: 폴스타 알람 ID
            alarm_name: 폴스타 알람 이름(admin 목록 "알람명" 표시용)
            db_id: 폴스타 인스턴스 식별자
            server_name: 대상 서버명
            severity: 알람 심각도(0~3)
            priority: 산출 우선순위(문자열로 저장)
            tier: 발송 티어(page 등)
            created_at: firing(PAGE) 시각

        Returns:
            생성된 incident id. 실패 시 0(graceful).
        """
        ...

    @abstractmethod
    async def resolve_by_fingerprint(
        self,
        *,
        fingerprint: str,
        resolved_at: datetime,
        resolution: str,
    ) -> int:
        """fingerprint로 열린 incident 중 가장 최근 1건을 resolved로 갱신한다.

        Args:
            fingerprint: 해소 대상 incident의 fingerprint
            resolved_at: 해소 시각
            resolution: 해소 사유(self_heal | clear | manual)

        Returns:
            영향 행 수(1=해소 완료, 0=매칭 open 없음). 실패 시 0(graceful).
        """
        ...

    @abstractmethod
    async def ack(
        self,
        *,
        incident_id: int,
        acked_at: datetime,
        acked_by: str,
    ) -> bool:
        """open 상태인 incident를 ack로 전이한다(식별키=incident id).

        이미 ack/resolved인 incident는 영향 0 → False.

        Args:
            incident_id: 확인 대상 incident id
            acked_at: 확인 시각
            acked_by: 확인 주체(인증 사용자 id 또는 operator)

        Returns:
            True=ack 완료, False=대상 없음/이미 처리됨. 실패 시 False(graceful).
        """
        ...

    @abstractmethod
    async def list_open(self, *, limit: int = 100) -> list[dict]:
        """열린(open) incident 목록을 최신순으로 반환한다.

        Args:
            limit: 최대 조회 건수

        Returns:
            incident dict 목록(시각은 ISO8601 문자열). 실패 시 빈 리스트(graceful).
        """
        ...

    @abstractmethod
    async def metrics(self, *, window_seconds: int, page_count: int) -> dict:
        """시간창 내 MTTA/MTTR/사건전환율/열린 건수를 집계한다.

        Args:
            window_seconds: 집계 창(초) — created_at 기준
            page_count: 사건전환율 분모(decision_store 집계의 page_count)

        Returns:
            dict — mtta_seconds, incident_mttr_seconds, incident_conversion_rate,
            incident_count, open_count. 데이터 없음/0분모는 None-안전.
            실패 시 전부 None-안전한 빈 dict(graceful).
        """
        ...
