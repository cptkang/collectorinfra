"""시멘틱 라우팅 패키지.

사용자의 자연어 질의를 분석하여 적절한 DB를 자동 선택하는
시멘틱 라우팅 기능을 제공한다.
"""

from src.routing.db_registry import DBRegistry
from src.routing.domain_config import DB_DOMAINS, DBDomainConfig
from src.routing.semantic_router import semantic_router

__all__ = [
    "DBRegistry",
    "DB_DOMAINS",
    "DBDomainConfig",
    "semantic_router",
]
