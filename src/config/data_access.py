"""데이터 접근 설정 — DBHub(MCP)·질의 실행·폴스타 REST·멀티 DB·Redis·스키마 캐시.

plans/166 W2 I4 — 옛 `src/config.py`에서 클래스를 그대로 옮겼다. 공개 경로는 `src.config`
그대로다(패키지 `__init__`이 이 모듈의 클래스를 재노출한다 · D-337 ①).
"""

# 옮긴 주석·문자열의 긴 줄은 이동 전부터 있던 것이다 — 바이트 그대로 두려고 파일 단위로 끈다.
# ruff: noqa: E501

from __future__ import annotations

import json
import logging

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I4)
logger = logging.getLogger("src.config")


class DBHubConfig(BaseSettings):
    """MCP 서버 접속 설정.

    DB 연결 정보는 포함하지 않는다 (MCP 서버 VM이 관리).
    클라이언트는 서버 URL만 보유한다.
    """

    server_url: str = "http://localhost:9099/sse"   # MCP 서버 SSE 엔드포인트
    source_name: str = ""                              # 기본 쿼리 대상 소스 (DBHUB_SOURCE_NAME으로 설정)
    mcp_call_timeout: int = 60                       # MCP 호출 전체 대기시간 (초)
    bearer_token: str = ""                           # 전송 인증 Bearer 토큰 (DBHUB_BEARER_TOKEN, 빈 값이면 무헤더 — 서버 무인증 전제)

    # ── 두 번째 이후 MCP 엔드포인트(plans/125 A-2 · G-8 (a) · D-274 ⑦) ──────────────────
    # 비SQL 관측 시스템(레지스트리 `solutions[]` 중 DB 항목이 없는 것 — 예 `apm`)의 게이트웨이.
    # **시스템 코드 → URL**(JSON · 예 {"apm": "http://127.0.0.1:9096/sse"}). 코드가 없으면 그
    # 시스템은 **비활성**이다 — 처리기 미등록·분해 프롬프트 렌더 0(신규 `enable_*` 없음 · D-162 ·
    # D-251 ⑥).
    source_endpoints: dict[str, str] = Field(
        default_factory=dict, validation_alias=AliasChoices("MCP_SOURCE_ENDPOINTS"),
    )
    # 시스템 코드 → 정적 Bearer 토큰(JSON 문자열 · 시크릿 — 웹UI 편집 차단·repr 마스킹).
    # 비면 무헤더.
    # DBHubConfig 는 `.env` 만 읽는다(DBHUB_BEARER_TOKEN 과 같은 자리).
    source_tokens: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("MCP_SOURCE_TOKENS"),
    )
    # 관측 소스 도구 호출 1회 상한(초) — 세션은 처리기 호출 동안 재사용한다(fan-out 지연).
    source_call_timeout: float = Field(
        default=10.0, validation_alias=AliasChoices("MCP_SOURCE_CALL_TIMEOUT"),
    )

    model_config = {
        "env_prefix": "DBHUB_", "env_file": ".env", "extra": "ignore", "populate_by_name": True,
    }

    def active_source_codes(self) -> tuple[str, ...]:
        """엔드포인트가 설정된(= 활성) 비SQL 시스템 코드 — 설정 선언 순서."""
        return tuple(code for code, url in self.source_endpoints.items() if str(url or "").strip())

    def source_endpoint(self, code: str) -> tuple[str, str | None] | None:
        """(URL, 토큰) — 설정이 없으면 None(= 그 시스템 비활성).

        토큰 JSON 이 깨졌으면 무헤더로 두고 경고를 남긴다(토큰 값은 로그에 싣지 않는다).
        """
        url = str(self.source_endpoints.get(code) or "").strip()
        if not url:
            return None
        raw = self.source_tokens.get_secret_value().strip()
        tokens: dict[str, object] = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("MCP_SOURCE_TOKENS 가 JSON 이 아니다 — 무헤더로 연결한다")
                parsed = {}
            tokens = parsed if isinstance(parsed, dict) else {}
        token = str(tokens.get(code) or "").strip() or None
        return url, token


class QueryConfig(BaseSettings):
    """클라이언트 측 쿼리 정책.

    DB 레벨 제한(query_timeout, max_rows)은 MCP 서버에서 관리한다.
    클라이언트는 재시도 횟수와 SQL 생성 기본 LIMIT만 관리한다.
    """

    max_retry_count: int = 3   # SQL 재생성 재시도 예산 (검증·실행·충분성 회귀 공용, D-099)
    default_limit: int = 1000  # SQL 생성 시 기본 LIMIT

    # 데이터 충분성 검사 임계값 (0.0 ~ 1.0)
    sufficiency_required_threshold: float = 0.7   # hint/synonym 매핑
    sufficiency_optional_threshold: float = 0.5   # llm_inferred 매핑

    # 모호한 사용자 의사 표현(유사어 등록·SQL 실행 승인)을 LLM으로 분류(Plan 67 R3-(ii), 기본 OFF).
    # OFF에서도 결정적 판정은 그대로 동작하고, 확정 불가 입력은 재질의(등록)·거부(승인 fail-closed,
    # D-130)로 처리된다 — ON은 그 확정 불가분만 LLM 1콜로 회복한다.
    intent_llm_assist: bool = False

    # 폼필 확인 이력(D-151 Phase 3) TTL — sliding(적용 시 연장). 0이면 기능 OFF.
    # 짧은 기본값이 안전측: 만료 비용(패널 재답변 1회) ≪ 부패 지속 비용(감사자료 오기재).
    form_memory_ttl_days: int = 7

    # 시간 표현 정규화(plans/122 트랙 T · D-309) — input_parser가 요청마다 한 번 기간을 해석해
    # state `time_resolution`에 싣고(규칙 1순위 + LLM `time_expr` 슬롯 폴백 · 기준 시각 주입),
    # 기간을 쓰는 모든 경로가 그 반개구간·입도를 리터럴로 쓴다. 해석 불가면 되묻는다.
    # **기본 ON — 「신규 플래그 기본 off」(plans/80 §5.4-③ · D-162)의 명시 예외**: 사용자 결정
    # (2026-10-06 · D-309) *"구현하고 기본 on으로 둡니다. 날짜 해석이 바로 효력을 내고 134 W6도 바로
    # 동작합니다."* — 섀도 단계(D-275 ⑬)를 건너뛴다. off면 `time_resolution`을 싣지 않고
    # input_parser 프롬프트도 종전 바이트 그대로라 모든 소비처가 종전 경로
    # (`resolve_stat_month_range`)로 돈다. 폐쇄망 run R2는 변경 전 커밋으로 고정해 전후를
    # 비교한다(D-309).
    time_resolution_enabled: bool = True

    model_config = {"env_prefix": "QUERY_", "env_file": ".env", "extra": "ignore"}


class PolestarRestConfig(BaseSettings):
    """폴스타 REST measurement API 설정 (Plan 71 / Plan 75 §1 — 실시간 사용률 데이터 평면).

    전 기능 기본 OFF — realtime_usage_enabled=false면 기존 SQL 경로 바이트 무변경(회귀 0).
    프로세스 API(AlarmConfig.process_api_base_urls_csv, Plan 47-1)와 base가 같은 폴스타
    REST지만 소비처 회귀 방지를 위해 설정은 분리 유지(통합 rename은 Plan 75 §1.3-5 후속).
    내부망 http·비인증·읽기 전용 GET(D-003) — Plan 47-1과 동일 규약.
    """

    realtime_usage_enabled: bool = False
    # db_id=base_url CSV (프로세스 API와 동일 형식). 은행존(b0)은 포트 명시 필수
    # (Plan 75 §1.3-5 실측: 10.37.16.51:9010 — gp/yd와 달리 기본 포트가 아님).
    base_urls_csv: str = (
        "polestar_b0=http://10.37.16.51:9010,"
        "polestar_cm_gp=http://polestar.kbonecloud.com,"
        "polestar_cm_yd=http://yd-polestar.kbonecloud.com"
    )
    # gp 200대 실측 2.46s(Plan 75 §1.3-4) — 프로세스 API 3s 재사용 금지, 여유 있게 별도 설정.
    measurement_timeout_seconds: int = 10
    measurement_chunk_size: int = 200   # 실측 확정(URL 길이·응답 크기 안전 범위)
    stale_after_minutes: int = 15       # time(수집 시각)이 이보다 오래되면 "수집 지연" 표기

    model_config = {"env_prefix": "POLESTAR_REST_", "env_file": ".env", "extra": "ignore"}

    def get_base_url(self, db_id: str) -> str | None:
        """db_id에 매핑된 measurement base_url을 반환한다 (없으면 None).

        매핑 형식: "db_id1=http://host1,db_id2=http://host2:port" (CSV, '=' 구분).
        AlarmConfig.get_process_api_base_url와 동일 규칙 — 잘못된 항목(= 미포함)은 무시.
        """
        for pair in self.base_urls_csv.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            key, _, url = pair.partition("=")
            if key.strip() == db_id and url.strip():
                return url.strip().rstrip("/")
        return None


class MultiDBConfig(BaseSettings):
    """멀티 DB 라우팅 설정.

    연결 문자열은 MCP 서버 VM이 관리한다.
    클라이언트는 활성 DB 목록만 관리하여 시멘틱 라우팅에 사용한다.
    활성 DB 목록은 MCP 서버의 list_sources 도구로 동적 조회하거나,
    환경변수 ACTIVE_DB_IDS로 명시적으로 설정할 수 있다.
    """

    # 활성 DB ID 목록 (쉼표 구분, 환경변수로 설정)
    # 예: ACTIVE_DB_IDS=polestar,cloud_portal,itsm,itam
    active_db_ids_csv: str = Field(
        default="",
        validation_alias=AliasChoices("ACTIVE_DB_IDS", "MULTI_DB_ACTIVE_DB_IDS_CSV"),
    )

    # 존 그룹 상호배타(D-143 후속3): 은행존(b0)과 공동존(gp/yd)의 동시 조회 차단.
    # **기본값 False = 동시 선택 개방**(D-206 · 2026-09-23 사용자 지시로 코드 기본값 전환).
    # 도입 근거 ①"담당 조직 분리로 존 조합 실수요 없음"(2026-08-05 확정)은 2026-09-09
    # 사용자 지시로 뒤집혔고, ②b0+gp 조합의 FabriX PII 차단(D-155 미종결)은 실행기가 존
    # 그룹별로 `_prepare_multi_run`을 새로 불러 한 run에 재료를 섞지 않는 것으로 회피한다.
    # 종전 기본값 True는 운영 `.env` 한 줄(ZONE_GROUP_EXCLUSIVE=false)로만 열려 있었는데,
    # `.env`는 git 미추적이라 그 줄이 없는 배포·클론이 확정과 반대로 동작했다.
    # `plans/80` §5.4-③(신규 플래그 기본 off = 비트 동일)의 명시적 예외 — 기존 플래그의
    # 기본 동작 전환이다. True로 되돌리면 D-143 후속3 동작(그룹 간 라디오)이 전부 복원된다.
    zone_group_exclusive: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ZONE_GROUP_EXCLUSIVE", "MULTI_DB_ZONE_GROUP_EXCLUSIVE"
        ),
    )

    model_config = {
        "env_prefix": "MULTI_DB_",
        "env_file": ".env",
        "extra": "ignore",
        "populate_by_name": True,
    }

    def get_active_db_ids(self) -> list[str]:
        """활성 DB 식별자 목록을 반환한다.

        Returns:
            활성 DB 식별자 목록
        """
        if not self.active_db_ids_csv:
            return []
        return [
            db_id.strip()
            for db_id in self.active_db_ids_csv.split(",")
            if db_id.strip()
        ]


class RedisConfig(BaseSettings):
    """Redis 관련 설정."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    ssl: bool = False
    socket_timeout: int = 5

    model_config = {"env_prefix": "REDIS_", "env_file": [".env", ".encenv"], "extra": "ignore"}


class SchemaCacheConfig(BaseSettings):
    """스키마 캐시 관련 설정."""

    cache_dir: str = ".cache/schema"
    enabled: bool = True
    backend: str = "redis"  # "redis" | "file"
    fingerprint_ttl_seconds: int = 1800  # fingerprint 검증 주기 (기본 30분)
    # ── plans/104 관리자 「DB 구조」 기능의 상한 — 플래그가 아니다(질의 경로 동작 불변) ──
    # 관리자 잡이 컬럼 설명·유사어 초안을 만들 때 동시에 띄우는 LLM 호출 수 상한(테이블당 1회 호출)
    admin_llm_concurrency: int = 2
    # 구조 분석을 FK 연결 묶음 단위로 나눌 때 한 번의 LLM 분석에 넣는 테이블 수 상한
    structure_group_max_tables: int = 40

    model_config = {"env_prefix": "SCHEMA_CACHE_", "env_file": ".env", "extra": "ignore"}
