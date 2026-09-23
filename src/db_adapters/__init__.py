"""DB 어댑터 레지스트리 — DB 특화 로직 격리 계층 (Plan 63 P2, D-089).

공용 코어는 어댑터의 존재를 모른다. 어댑터가 임포트 시점에 레지스트리에 등록하고,
코어는 `get_adapter(db_id, polestar_db_ids)`로 담당 어댑터를 조회해 훅을 호출한다.

**죽은 레지스트리 방지**(D-086 계열): 이 패키지를 임포트하면 하단 부트스트랩이 폴스타
어댑터를 등록한다. 소비처(query_generator 등)가 `from src.db_adapters import get_adapter`
하면 자동 등록된다. 등록·조회 배선은 `tests/test_db_adapters.py`로 고정한다.
"""

from __future__ import annotations

import logging
from typing import Any

from src.db_adapters.base import DBAdapter

logger = logging.getLogger(__name__)

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


def log_adapter_ownership_startup(config: Any) -> dict[str, list[str]]:
    """어댑터 제품군 DB가 활성인데 담당 ID 설정이 비어 있으면 기동 WARNING 1줄을 남긴다.

    담당 ID(`<어댑터명>_DB_IDS`, 예: POLESTAR_DB_IDS)가 비면 그 DB는 어댑터 검증기·결정적
    조립·전용 프롬프트 없이 공통 경로로 돈다 — 설정 누락이 로그에 흔적 없이 품질만 떨어뜨렸다
    (plans/116 §10.3 사례 녹화). 판정은 db_registry 제품군(솔루션 코드 → 없으면 family)이
    등록 어댑터 이름과 같은지로 한다. 동작은 바꾸지 않는다(기록만).

    Returns:
        {어댑터명: [담당 설정에 빠진 활성 db_id]} — 경고 대상이 없으면 빈 dict
    """
    from src.routing.registry import get_registry

    registry = get_registry()
    owned = set(config.get_polestar_db_ids() or set())
    unowned: dict[str, list[str]] = {}
    for db_id in config.multi_db.get_active_db_ids():
        entry = registry.get(db_id)
        if entry is None or db_id in owned:
            continue
        family = registry.system_of(db_id) or entry.family
        for adapter in _REGISTRY:
            if adapter.name == family:
                unowned.setdefault(adapter.name, []).append(db_id)
    for name, db_ids in unowned.items():
        logger.warning(
            "어댑터 담당 설정 누락: 활성 DB %s는 '%s' 제품군인데 %s_DB_IDS에 없어 전용 검증·"
            "결정적 조립·전용 프롬프트 없이 공통 경로로 조회됩니다(.env에 %s_DB_IDS 지정 필요)",
            db_ids, name, name.upper(), name.upper(),
        )
    return unowned


# 부트스트랩 — 임포트 시 폴스타 어댑터 등록. register/get_adapter 정의 뒤에 두어
# 순환 임포트를 피한다(polestar.__init__이 register를 참조).
from src.db_adapters import polestar as _polestar_bootstrap  # noqa: E402,F401
