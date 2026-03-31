"""DB 도메인 정의 모듈.

각 DB의 식별자, 표시명, 도메인 설명, 별칭을 정의한다.
시멘틱 라우터가 이 정의를 기반으로 LLM에 DB 도메인 정보를 제공한다.

v2 변경: keywords 필드를 제거하고, aliases 필드로 교체.
라우팅은 LLM 전용으로 수행되며, aliases는 사용자 직접 DB 지정 감지에 사용된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


DB_DOMAINS: list[DBDomainConfig] = [
    DBDomainConfig(
        db_id="polestar",
        display_name="Polestar DB",
        description=(
            "서버 물리 사양 및 사용량 데이터. "
            "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
            "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
            "서버 정보(hostname, IP, gateway), "
            "프로세스 정보(서버에서 동작 중인 프로세스 종류)"
        ),
        aliases=["polestar", "폴스타", "Polestar", "Polestar DB"],
        env_connection_key="POLESTAR_DB_CONNECTION",
        env_type_key="POLESTAR_DB_TYPE",
        db_engine="db2",
    ),
    DBDomainConfig(
        db_id="cloud_portal",
        display_name="Cloud Portal DB",
        description=(
            "가상화 인프라 데이터. "
            "VM(가상머신) 정보, 데이터 스토어 정보, "
            "전체 VM 대수, "
            "영역별 VM 대수(김포, 여의도, DMZ, 내부망 등)"
        ),
        aliases=[
            "cloud_portal", "클라우드 포탈", "클라우드포탈",
            "Cloud Portal", "Cloud Portal DB",
        ],
        env_connection_key="CLOUD_PORTAL_DB_CONNECTION",
        env_type_key="CLOUD_PORTAL_DB_TYPE",
    ),
    DBDomainConfig(
        db_id="itsm",
        display_name="ITSM DB",
        description=(
            "IT 서비스 관리 데이터. "
            "서비스 요청, 인시던트, 변경 관리, 문제 관리, SLA 등"
        ),
        aliases=["itsm", "ITSM", "ITSM DB"],
        env_connection_key="ITSM_DB_CONNECTION",
        env_type_key="ITSM_DB_TYPE",
    ),
    DBDomainConfig(
        db_id="itam",
        display_name="ITAM DB",
        description=(
            "IT 자산 관리 데이터. "
            "IT 자산 목록, 자산 라이프사이클, 계약 정보, "
            "소프트웨어 라이선스, 하드웨어 자산 등"
        ),
        aliases=["itam", "ITAM", "ITAM DB", "자산관리", "자산관리 DB"],
        env_connection_key="ITAM_DB_CONNECTION",
        env_type_key="ITAM_DB_TYPE",
    ),
]


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
