"""DB 도메인 정의 모듈.

각 DB의 식별자, 표시명, 도메인 설명, 별칭을 정의한다.
시멘틱 라우터가 이 정의를 기반으로 LLM에 DB 도메인 정보를 제공한다.

v2 변경: keywords 필드를 제거하고, aliases 필드로 교체.
라우팅은 LLM 전용으로 수행되며, aliases는 사용자 직접 DB 지정 감지에 사용된다.

v3 변경(Plan 67 R2): 도메인 정의를 이 파일에 직접 쓰지 않고 **레지스트리 정본**
(`config/db_registry.yaml`)에서 파생한다. 신규 DB 편입 시 이 파일은 수정 대상이
아니며 레지스트리만 수정한다(수정 지점 9곳+ → 레지스트리 + `.env` 2곳).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.routing.registry import DBRegistry, get_registry


@dataclass(frozen=True)
class DBDomainConfig:
    """단일 DB 도메인 설정.

    aliases: 사용자가 프롬프트에서 직접 DB를 지정할 때 인식할 이름 목록.
             LLM 프롬프트에 별칭 정보로 제공된다.
    """

    db_id: str
    display_name: str
    description: str
    aliases: list[str] = field(default_factory=list)
    env_connection_key: str = ""
    env_type_key: str = ""
    db_engine: str = "postgresql"  # "postgresql", "mysql", "db2", etc.
    # 테이블 참조 시 붙일 스키마명(D-057). 빈 문자열이면 무스키마(연결 CURRENT SCHEMA)로 참조한다.
    # LLM SQL 생성 경로(multi_db_executor)와 hostname 해소(polestar_hostname_resolver)가
    # 이 값을 단일 출처로 사용하여 `schema.table`을 결정적으로 한정한다.
    # DB2(b0)는 연결 계정 CURRENT SCHEMA(예: SDQ000)가 테이블 소유 스키마와 다를 수 있으므로,
    # 실 스키마를 SYSCAT.TABLES로 확인 후 레지스트리에 명시해야 SQL0204N(-204)을 방지할 수 있다.
    db_schema: str = ""


def build_domains(registry: DBRegistry | None = None) -> list[DBDomainConfig]:
    """레지스트리 정본에서 DB 도메인 목록을 생성한다.

    Args:
        registry: 레지스트리(미지정 시 정본 `config/db_registry.yaml`)

    Returns:
        선언 순서를 유지한 DBDomainConfig 목록
    """
    reg = registry or get_registry()
    return [
        DBDomainConfig(
            db_id=entry.db_id,
            display_name=entry.display_name,
            description=entry.description,
            aliases=list(entry.aliases),
            env_connection_key=entry.env_connection_key,
            env_type_key=entry.env_type_key,
            db_engine=entry.engine,
            db_schema=entry.db_schema,
        )
        for entry in reg.databases
    ]


DB_DOMAINS: list[DBDomainConfig] = build_domains()


def get_domain_by_id(db_id: str) -> DBDomainConfig | None:
    """DB 식별자로 도메인 설정을 조회한다.

    Args:
        db_id: DB 식별자

    Returns:
        해당 DBDomainConfig 또는 None
    """
    for domain in DB_DOMAINS:
        if domain.db_id == db_id:
            return domain
    return None


def get_all_db_ids() -> list[str]:
    """등록된 모든 DB 식별자를 반환한다.

    Returns:
        DB 식별자 목록
    """
    return [d.db_id for d in DB_DOMAINS]
