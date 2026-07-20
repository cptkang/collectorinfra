"""DB 어댑터 인터페이스 — DB 특화 로직 격리 계층 (Plan 63 P2, D-089).

공용 코어(DB-agnostic)는 어댑터의 존재를 모르고, 어댑터가 레지스트리 훅에 등록한다.
훅은 **실제로 배선하는 것만** 정의한다 — 두 번째 어댑터가 생기기 전까지 추상화를 확장하지
않는다(Simplicity First, Plan 63 §9 "어댑터 과설계 금지"). 훅은 이동 트랙이 진행되며 추가된다.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class DBAdapter(Protocol):
    """DB 특화 로직을 제공하는 어댑터 계약.

    담당 DB 판정은 런타임 설정(`polestar_db_ids` 등)에 따르므로 정적 `db_ids`가 아니라
    `owns()` 메서드로 위임한다(POLESTAR_DB_IDS가 .env 런타임 값이기 때문).
    """

    name: str

    def owns(self, db_id: str | None, polestar_db_ids: set[str] | None = None) -> bool:
        """이 어댑터가 해당 db_id를 담당하는지."""
        ...

    def system_template(self, routing_intent: str | None) -> str | None:
        """의도별 전용 시스템 프롬프트 템플릿(공통을 쓰면 None)."""
        ...

    def validator_checks(self) -> list[Callable[[str], list[str]]]:
        """SQL 문자열을 받아 오류 메시지 목록을 반환하는 전용 검증 함수들(없으면 [])."""
        ...

    # 참고: 알람 코어 테이블/schema_table_policy 훅은 P3-4에서 프로필 `alarm_core_tables`
    # 선언과 함께 추가한다(schema_analyzer 알람 분기가 intent_hint 프롬프트와 얽혀 있어
    # 프로필 선언으로 다루는 편이 정합적 — dead hook 방지, Plan 63 P3-4).
