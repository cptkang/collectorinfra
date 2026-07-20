"""DB 어댑터 레지스트리 — DB 특화 로직 격리 계층 (Plan 63 P2, D-089).

공용 코어는 어댑터의 존재를 모른다. 어댑터가 임포트 시점에 레지스트리에 등록하고,
코어는 `get_adapter(db_id, polestar_db_ids)`로 담당 어댑터를 조회해 훅을 호출한다.

**죽은 레지스트리 방지**(D-086 계열): 이 패키지를 임포트하면 하단 부트스트랩이 폴스타
어댑터를 등록한다. 소비처(query_generator 등)가 `from src.db_adapters import get_adapter`
하면 자동 등록된다. 등록·조회 배선은 `tests/test_db_adapters.py`로 고정한다.
"""

from __future__ import annotations

from src.db_adapters.base import DBAdapter

_REGISTRY: list[DBAdapter] = []


def register(adapter: DBAdapter) -> None:
    """어댑터를 레지스트리에 등록한다(중복 등록 무시)."""
    if all(a.name != adapter.name for a in _REGISTRY):
        _REGISTRY.append(adapter)


def get_adapter(
    db_id: str | None, polestar_db_ids: set[str] | None = None
) -> DBAdapter | None:
    """db_id를 담당하는 어댑터를 반환한다(없으면 None → 공통 경로)."""
    for adapter in _REGISTRY:
        if adapter.owns(db_id, polestar_db_ids):
            return adapter
    return None


def registered_adapters() -> list[DBAdapter]:
    """등록된 어댑터 목록(배선 테스트용)."""
    return list(_REGISTRY)


# 부트스트랩 — 임포트 시 폴스타 어댑터 등록. register/get_adapter 정의 뒤에 두어
# 순환 임포트를 피한다(polestar.__init__이 register를 참조).
from src.db_adapters import polestar as _polestar_bootstrap  # noqa: E402,F401
