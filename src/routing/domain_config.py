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
    # 테이블 참조 시 붙일 스키마명(D-057). 빈 문자열이면 무스키마(연결 CURRENT SCHEMA)로 참조한다.
    # LLM SQL 생성 경로(multi_db_executor)와 hostname 해소(polestar_hostname_resolver)가
    # 이 값을 단일 출처로 사용하여 `schema.table`을 결정적으로 한정한다.
    # DB2(b0)는 연결 계정 CURRENT SCHEMA(예: SDQ000)가 테이블 소유 스키마와 다를 수 있으므로,
    # 실 스키마를 SYSCAT.TABLES로 확인 후 여기에 명시해야 SQL0204N(-204)을 방지할 수 있다.
    db_schema: str = ""


DB_DOMAINS: list[DBDomainConfig] = [
    DBDomainConfig(
        db_id="polestar_b0",
        display_name="은행 레거시 및 K리전(은행존) Polestar",
        description=(
            "은행 레거시 및 K리전(은행존) 서버 물리 사양, 사용량 및 모니터링 데이터. "
            "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
            "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
            "서버 정보(hostname, IP, gateway), "
            "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
            "알람 심각도(0=해소/1=주의/2=경고/3=심각), 알람 발생 시각(CTIME), "
            "알람 담당자(ACKUSERNAME), 알람 대상 장비(CMM_RESOURCE)"
        ),
        aliases=["polestar_b0", "은행 폴스타", "레거시 폴스타"],
        env_connection_key="POLESTAR_B0_CONNECTION",
        env_type_key="POLESTAR_B0_TYPE",
        db_engine="db2",
        # D-057(2026-07-02 실측): 운영 b0 DB2에서 cmm_resource 소유 스키마 = POLESTAR
        #   (SYSCAT.TABLES 조회 결과 TABSCHEMA='POLESTAR'). 연결 계정 CURRENT SCHEMA는 SDQ000이라
        #   무스키마 참조 시 SDQ000.CMM_RESOURCE로 해소되어 SQL0204N(-204) 발생 → 명시 스키마 한정 필수.
        #   DB2는 미인용 식별자를 대문자로 저장하므로 스키마명도 대문자 POLESTAR로 지정한다.
        db_schema="POLESTAR",
    ),
    DBDomainConfig(
        db_id="polestar_cm_gp",
        display_name="K리전(공동존) 김포(운영/DR) Polestar",
        description=(
            "K리전(공동존) 김포 운영 및 DR 서버 물리 사양, 사용량 및 모니터링 데이터. "
            "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
            "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
            "서버 정보(hostname, IP, gateway), "
            "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
            "알람 심각도(0=해소/1=주의/2=경고/3=심각), 알람 발생 시각(CTIME), "
            "알람 담당자(ACKUSERNAME), 알람 상태(CURRENTALARMSTATUS), "
            "알람 대상 장비(CMM_RESOURCE), 알람 유형(CMM_ALARM_DEF)"
        ),
        aliases=["polestar_cm_gp", "공동존", "공동존 폴스타", "공동존 김포 폴스타", "공동존 운영 폴스타", "공동존 DR 폴스타", "김포", "김포 폴스타"],
        env_connection_key="POLESTAR_CM_GP_CONNECTION",
        env_type_key="POLESTAR_CM_GP_TYPE",
        db_engine="postgresql",
        db_schema="polestar",
    ),
    DBDomainConfig(
        db_id="polestar_cm_yd",
        display_name="K리전(공동존) 여의도(개발/스테이징) Polestar",
        description=(
            "K리전(공동존) 여의도 개발 및 스테이징 서버 물리 사양, 사용량 및 모니터링 데이터. "
            "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
            "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
            "서버 정보(hostname, IP, gateway), "
            "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
            "알람 심각도(0=해소/1=주의/2=경고/3=심각), 알람 발생 시각(CTIME), "
            "알람 담당자(ACKUSERNAME), 알람 상태(CURRENTALARMSTATUS), "
            "알람 대상 장비(CMM_RESOURCE), 알람 유형(CMM_ALARM_DEF)"
        ),
        aliases=["polestar_cm_yd", "공동존", "공동존 폴스타", "공동존 여의도 폴스타", "공동존 개발 폴스타", "공동존 스테이징 폴스타", "여의도", "여의도 폴스타"],
        env_connection_key="POLESTAR_CM_YD_CONNECTION",
        env_type_key="POLESTAR_CM_YD_TYPE",
        db_engine="postgresql",
        db_schema="polestar",
    ),
    # 로컬 개발 샌드박스(도커 polestar_pg, testdata/pg/init 픽스처). 운영 폴스타(gp/yd/b0)와
    # 동일 스키마. 트랙 C(D-076) 로컬 검증을 위해 재등재(2026-07-14) — db_engine/db_schema
    # 결정적 주입(D-066 후속6)과 시맨틱 모델 로드(config/semantic_models/polestar.yaml)에 필요.
    # 운영 .env는 ACTIVE_DB_IDS에 polestar를 넣지 않으므로 라우팅에 영향 없음(활성 DB만 순회).
    DBDomainConfig(
        db_id="polestar",
        display_name="로컬 개발 샌드박스 Polestar",
        description=(
            "로컬 개발/테스트용 Polestar 샌드박스 (도커 testdata 픽스처). "
            "운영 폴스타와 동일 스키마 — 서버 물리 사양, 사용량 및 모니터링 데이터. "
            "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
            "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
            "서버 정보(hostname, IP, gateway), "
            "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
            "알람 심각도(0=해소/1=주의/2=경고/3=심각)"
        ),
        aliases=["polestar", "로컬 폴스타", "테스트 폴스타"],
        env_connection_key="POLESTAR_CONNECTION",
        env_type_key="POLESTAR_TYPE",
        db_engine="postgresql",
        db_schema="polestar",
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
